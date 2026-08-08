#!/usr/bin/env python3
"""AgentOS 评测源回执的闭合集合策略清单。"""
from __future__ import annotations

if __package__:
    from .functional_acceptance_compile_contract import COMPILE_DEPENDENCY_PATHS
    from .scenario_timing_source_contract import SOURCE_PATHS
else:
    from functional_acceptance_compile_contract import COMPILE_DEPENDENCY_PATHS
    from scenario_timing_source_contract import SOURCE_PATHS


SOURCE_RELATIVE = "user/src/agenteval_ucore.c"
EVALUATION_SUITE_SOURCE_PATH = "ci/evaluation-suite.json"
POLICY_INVENTORY_SCHEMA = "agentos-evaluation-policy-inventory-v4"

# 这里必须使用允许列表，而非递归源码树快照。每个条目都会参与正式测量的
# 选择、执行、解释、渲染或打包，因此必须纳入签名回执。
CONTROL_PLANE_POLICY = (
    ("suite", EVALUATION_SUITE_SOURCE_PATH),
    ("trusted-python-entry", "scripts/trusted-python-entry.py"),
    ("trusted-python-child", "scripts/trusted-python-child.py"),
    ("evaluation-source-gate", "host_tools/evaluation_source_gate.py"),
    ("formal-python-runtime", "host_tools/formal_python_runtime.py"),
    ("formal-temporary-binding", "host_tools/formal_temp_binding.py"),
    ("campaign", "host_tools/evaluation_campaign.py"),
    ("contract", "host_tools/evaluation_contract.py"),
    ("scenario", "host_tools/evaluation_scenario.py"),
    ("compatibility-producer", "host_tools/compatibility_overhead.py"),
    ("compatibility-contract", "host_tools/compatibility_overhead_contract.py"),
    ("compatibility-guest-source", "evaluation_guest/compatbench.c"),
    ("bundle", "host_tools/evaluation_bundle.py"),
    ("contest-demo", "host_tools/contest_demo.py"),
    ("committed-source-identity", "host_tools/committed_source_identity.py"),
    ("full-verification-stage", "host_tools/full_verification_payload.py"),
    ("full-verification-metrics", "host_tools/full_verification_metrics.py"),
    ("full-verification-metrics", "host_tools/full_verification_metrics_render.py"),
    ("full-verification-collector", "scripts/capture-final-evidence.py"),
    ("full-verification-runner", "scripts/run-full-verification.sh"),
    ("dual-platform-runner", "scripts/run-dual-platforms.sh"),
    ("measurement-set-publisher", "host_tools/extract_measured_experiments.py"),
    ("tool-attestation", "host_tools/evidence_toolchain_attestation.py"),
    ("semantic-replay", "host_tools/agent_metadata_disk_format.py"),
    ("semantic-replay", "host_tools/agent_metadata_journal.py"),
    ("semantic-replay", "host_tools/agent_observe_disk_acceptance.py"),
    ("semantic-replay", "host_tools/agent_observe_disk_contract.py"),
    ("semantic-replay", "host_tools/agent_observe_disk_evidence.py"),
    ("semantic-replay", "host_tools/backend_evidence_contract.py"),
    ("semantic-replay", "host_tools/check_host_platform_alignment.py"),
    ("semantic-replay", "host_tools/check_host_test_alignment.py"),
    ("semantic-replay", "host_tools/compare_dual_platform_state.py"),
    ("semantic-replay", "host_tools/dual_state_archive.py"),
    ("semantic-replay", "host_tools/dual_state_evidence_contract.py"),
    ("semantic-replay", "host_tools/evidence_semantic_common.py"),
    ("semantic-replay", "host_tools/evidence_semantic_dual.py"),
    ("semantic-replay", "host_tools/evidence_semantic_metadata.py"),
    ("semantic-replay", "host_tools/evidence_semantic_profiles.py"),
    ("semantic-replay", "host_tools/evidence_semantic_registry.py"),
    ("semantic-replay", "host_tools/measured_experiments.py"),
    ("semantic-replay", "host_tools/reference_catalog_contract.py"),
    ("semantic-replay", "scripts/fs-allocator-evidence.py"),
    ("semantic-replay", "scripts/fs-allocator-image.py"),
    ("semantic-replay", "scripts/validate-kernel-test-log.py"),
    ("semantic-replay", "scripts/validate-metadata-crash-log.py"),
    ("semantic-replay", "scripts/validate-metadata-reprobe-log.py"),
    ("semantic-replay", "scripts/validate-virtio-disk-log.py"),
    ("semantic-replay-data", "ci/agent-metadata-disk-format.json"),
    ("semantic-replay-data", "ci/agent-observe-disk-format.json"),
    ("semantic-replay-data", "user/src/agentbench_ucore.c"),
    ("renderer", "host_tools/render_evaluation_dashboard.py"),
    ("renderer-asset", "host_tools/assets/evaluation-dashboard.css"),
    ("renderer-asset", "host_tools/assets/evaluation-dashboard.js"),
    ("platform", "host_tools/evaluation_platform.py"),
    ("platform", "host_tools/duration_profile_attestation.py"),
    ("kernel-build", "host_tools/evaluation_kernel_build.py"),
    ("kernel-cost", "host_tools/evaluation_kernel_cost.py"),
    ("resource-job-budget", "host_tools/resource_job_budget.py"),
    ("resource-job-policy", "scripts/resource-jobs.py"),
    ("kernel-budget-policy", "ci/kernel-budgets.json"),
    ("kernel-budget-policy", "scripts/agent_test_calibration.py"),
    ("kernel-budget-checker", "scripts/check-kernel-budgets.py"),
    ("kernel-budget-probe", "scripts/probes/struct-proc-size.c"),
    ("user-stack-checker", "scripts/check-user-stack-usage.py"),
    ("user-stack-contract", "user_stack_policy.h"),
    ("source-contract", "host_tools/agenteval_measurement_source_contract.py"),
    ("source-contract", "host_tools/agenteval_measurement_source_policy.py"),
    ("source-contract", "host_tools/agenteval_measurement_source_receipt.py"),
    ("source-contract", "host_tools/agenteval_measurement_source_validator.py"),
    ("source-contract", "host_tools/benchmark_source_contract.py"),
    ("source-contract", "host_tools/functional_acceptance_compile_contract.py"),
    ("source-contract", "host_tools/functional_acceptance_source_contract.py"),
    ("source-contract", "host_tools/scenario_timing_source_contract.py"),
    ("delivery-contract", "host_tools/evidence_delivery_contract.py"),
    ("delivery-contract", "host_tools/git_history_contract.py"),
    ("path-contract", "host_tools/safe_host_paths.py"),
    ("json-contract", "host_tools/strict_json.py"),
    ("plain-runner", "host_tools/plain_ucore_action_runner.py"),
    ("plain-fs-extractor", "host_tools/plain_ucore_fs_extract.py"),
    ("state-manifest-resolver", "host_tools/research_state_manifest.py"),
    ("state-manifest", "ci/research-state-manifest.json"),
    ("seed-checker", "host_tools/check_seeded_action_state.py"),
    ("seed-oracle", "evaluation_guest/fixtures/task6-count-corpus.csv"),
    ("micro-runner", "scripts/run-agent-tests.sh"),
    ("micro-parallel-qemu-runner", "scripts/run-parallel-qemu-regressions.py"),
    ("micro-evidence-wiring", "scripts/evidence-wiring.sh"),
    ("micro-qemu-runner", "scripts/agent_test_runner.py"),
    ("micro-guest-failure-classifier", "scripts/guest_failure_classifier.py"),
    ("micro-preflight", "scripts/test-sync-owner-wiring.py"),
    ("micro-preflight", "scripts/test-wait-atomic-wiring.py"),
    ("micro-preflight", "scripts/check-wait-queue-contract.py"),
    ("run-script", "scripts/run-evaluation-suite.sh"),
    ("package-script", "scripts/package-evaluation-evidence.sh"),
    ("agentos-build-map", "Makefile"),
    ("agentos-init-selector", "scripts/initproc.py"),
    ("agentos-image-build-map", "nfs/Makefile"),
    ("agentos-user-build-map", "user/Makefile"),
    ("baseline-build-map", "baseline_ucore/Makefile"),
    ("baseline-init-selector", "baseline_ucore/scripts/initproc.py"),
    ("baseline-image-build-map", "baseline_ucore/nfs/Makefile"),
    ("baseline-user-build-map", "baseline_ucore/user/Makefile"),
    ("agentos-state-helper", "user/include/research_platform_state.h"),
    ("agentos-program-manifest", "user/include/rp_program_manifest.h"),
    ("baseline-state-helper", "baseline_ucore/user/include/research_platform_state.h"),
    ("baseline-program-manifest", "baseline_ucore/user/include/rp_program_manifest.h"),
)

# 可移植双状态重放不得查询未绑定的检出内容来发现 Guest 生产者。
SEMANTIC_REPLAY_COMMON_SOURCES = (
    "rp_agent_collab.c",
    "rp_analysisres.c",
    "rp_analyst.c",
    "rp_artifact_ops.c",
    "rp_auditor.c",
    "rp_backend.c",
    "rp_calculation.c",
    "rp_campaign.c",
    "rp_catalog.c",
    "rp_coherenceplane.c",
    "rp_compare_plain.c",
    "rp_complete.c",
    "rp_consistency.c",
    "rp_controlplane.c",
    "rp_data_pipeline.c",
    "rp_decsupport.c",
    "rp_delta.c",
    "rp_dossier.c",
    "rp_evidence.c",
    "rp_execobs.c",
    "rp_expsched.c",
    "rp_governance.c",
    "rp_integrityplane.c",
    "rp_invoke.c",
    "rp_lab.c",
    "rp_lineage.c",
    "rp_llm_bridge.c",
    "rp_llm_relay.c",
    "rp_mature.c",
    "rp_metrics.c",
    "rp_modelreg.c",
    "rp_notebook_export.c",
    "rp_object_query.c",
    "rp_object_store.c",
    "rp_opsboard.c",
    "rp_orch.c",
    "rp_package.c",
    "rp_plain.c",
    "rp_planner.c",
    "rp_portability.c",
    "rp_privacy.c",
    "rp_projectrel.c",
    "rp_prov_query.c",
    "rp_prov_view.c",
    "rp_publication.c",
    "rp_query.c",
    "rp_realtask.c",
    "rp_reldossier.c",
    "rp_release.c",
    "rp_repair.c",
    "rp_retriever.c",
    "rp_revdash.c",
    "rp_reviewboard.c",
    "rp_reviewer.c",
    "rp_runbooks.c",
    "rp_runconf.c",
    "rp_seed_orch.c",
    "rp_service_surface.c",
    "rp_site_export.c",
    "rp_startup_doctor.c",
    "rp_state_catalog.c",
    "rp_stdesign.c",
    "rp_studyproto.c",
    "rp_sysreview.c",
    "rp_test_suite.c",
    "rp_traincomp.c",
    "rp_ui_export.c",
    "rp_usable.c",
    "rp_usableproject.c",
    "rp_web_export.c",
    "rp_workbench.c",
    "rp_workflow_runner.c",
    "rp_writer.c",
)

SEMANTIC_REPLAY_SOURCE_POLICY = tuple(
    ("semantic-replay-guest-source", f"{prefix}/{name}")
    for prefix in ("baseline_ucore/user/src", "user/src")
    for name in SEMANTIC_REPLAY_COMMON_SOURCES
    if f"{prefix}/{name}" not in {
        "baseline_ucore/user/src/rp_seed_orch.c",
        "user/src/rp_orch.c",
    }
)

GUEST_POLICY_ROLES = (
    "micro-guest",
    "baseline-orchestrator",
    "agentos-seeded-orchestrator",
    "agentos-orchestrator",
    "agentos-resource-probe",
    "agentos-resource-contract",
    "agentos-exec-role-policy",
    "kernel-lifecycle-identity-abi",
    "kernel-lifecycle-identity-observer",
    "kernel-performance-abi",
    "kernel-resource-abi",
    "kernel-resource-observer",
    "kernel-performance-counter-producer",
    "kernel-performance-counter-interface",
    "kernel-agent-identity-authority",
    "kernel-exec-bootstrap-policy",
    "kernel-physical-io-counter-producer",
    "kernel-virtio-counter-producer",
    "kernel-fs-epoch-counter-producer",
    "kernel-cow-counter-producer",
    "kernel-exec-cache-counter-producer",
    "kernel-resource-controller-implementation",
    "kernel-resource-controller-interface",
    "kernel-syscall-dispatch",
    "kernel-syscall-id",
    "agentos-riscv-syscall-id-template",
    "agentos-syscall-id",
    "baseline-clock-helper",
    "agentos-clock-helper",
    "agentos-showcase-retry-contract",
    "agentos-showcase-performance-pair",
)


def _policy_entries() -> tuple[tuple[str, str], ...]:
    guest_paths = (SOURCE_RELATIVE, *SOURCE_PATHS)
    if len(guest_paths) != len(GUEST_POLICY_ROLES):
        raise ValueError("measurement Guest policy roles differ from source paths")
    base_entries = (
        tuple(zip(GUEST_POLICY_ROLES, guest_paths))
        + CONTROL_PLANE_POLICY
        + SEMANTIC_REPLAY_SOURCE_POLICY
    )
    base_paths = {path for _role, path in base_entries}
    entries = base_entries + tuple(
        ("functional-compile-dependency", path)
        for path in COMPILE_DEPENDENCY_PATHS
        if path not in base_paths
    )
    paths = tuple(path for _role, path in entries)
    if len(paths) != len(set(paths)):
        raise ValueError("evaluation policy inventory repeats a source path")
    return entries


def measurement_source_policy_inventory() -> dict[str, object]:
    return {
        "schema": POLICY_INVENTORY_SCHEMA,
        "entries": [
            {"role": role, "path": path}
            for role, path in _policy_entries()
        ],
    }


def _receipt_source_paths() -> tuple[str, ...]:
    return tuple(path for _role, path in _policy_entries())
