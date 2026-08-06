#!/usr/bin/env python3
"""Profile validators for offline final-evidence artifacts."""
from __future__ import annotations

import csv
import math
import re
from pathlib import Path

from evidence_semantic_common import (
    SAFE_TAG,
    CombinedLog,
    EvidenceSemanticError,
    ValidationContext,
    _call,
    _expect_tags,
    _json,
    _load_module,
    _nonnegative_int,
    _parse_combined,
    _parse_guest_stream,
    _positive_int,
    _regular_bytes,
    _reject_tokens,
    _require_line,
    _require_regex,
    _text,
)
from evidence_semantic_metadata import validate_metadata as _validate_metadata
from evidence_semantic_dual import (
    validate_complete_dual_state, validate_dual_alignment, validate_program_ledgers,
)
from dual_state_evidence_contract import (
    DualStateContractError,
    validate_dual_state,
)
from reference_catalog_contract import expected_reference_identities


def _validate_agent_suite(ctx: ValidationContext) -> None:
    config = _json(ctx.repo_root / "ci/kernel-budgets.json", "kernel budget configuration")
    expected_cases = (
        config.get("agent_test_suite", {}).get("expected_cases")
        if isinstance(config, dict) and isinstance(config.get("agent_test_suite"), dict)
        else None
    )
    if (
        not isinstance(expected_cases, list)
        or not expected_cases
        or not all(isinstance(item, str) and SAFE_TAG.fullmatch(item) for item in expected_cases)
        or len(expected_cases) != len(set(expected_cases))
    ):
        raise EvidenceSemanticError("Agent case configuration is invalid")
    guests = _parse_guest_stream(ctx.raw_dir / "agent-suite-guest.log", "Agent suite Guest log")
    expected_tags = (
        "agent-mechanism:context-sync-atomicity",
        *(f"agent-case:{case}" for case in expected_cases),
    )
    if tuple(guests) != expected_tags:
        raise EvidenceSemanticError("Agent suite Guest tag inventory or order differs")
    kernel = _load_module(ctx, "scripts/validate-kernel-test-log.py")
    mechanism = guests["agent-mechanism:context-sync-atomicity"]
    _call(
        kernel, "validate_agent_case", "Agent context-sync Guest log",
        mechanism, "agentfinal_ucore", True,
    )
    for case in expected_cases:
        text = guests[f"agent-case:{case}"]
        _call(kernel, "validate_agent_case", f"Agent case {case}", text, case)
    timing_text = _text(ctx.raw_dir / "agent-suite-timings.log", "Agent suite timings")
    rows: list[tuple[str, float]] = []
    for line_number, line in enumerate(timing_text.splitlines(), 1):
        match = re.fullmatch(r"([A-Za-z0-9_]+)[ \t]+([0-9]+\.[0-9]{9})", line)
        if match is None:
            raise EvidenceSemanticError(f"Agent timing row {line_number} is invalid")
        seconds = float(match.group(2))
        if not math.isfinite(seconds) or seconds <= 0:
            raise EvidenceSemanticError(f"Agent timing row {line_number} is invalid")
        rows.append((match.group(1), seconds))
    if (
        [name for name, _ in rows] != expected_cases
        or len(rows) != len({name for name, _ in rows})
    ):
        raise EvidenceSemanticError("Agent timing case inventory differs from configuration")


DUAL_STAGES = (
    "structure-check", "seeded-dual-run", "qemu-log-marker-check",
    "state-extract-copy", "host-alignment", "state-compare",
    "measured-file-query",
)


def _validate_dual_stage_csv(path: Path) -> None:
    try:
        with path.open(encoding="utf-8", errors="strict", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise EvidenceSemanticError(f"dual stage timings are invalid: {error}") from error
    fields = ["stage", "start_epoch", "end_epoch", "duration_seconds", "status"]
    if reader.fieldnames != fields or len(rows) != len(DUAL_STAGES):
        raise EvidenceSemanticError("dual stage timing schema or row count differs")
    previous_end = 0
    for row, stage in zip(rows, DUAL_STAGES):
        if set(row) != set(fields) or row.get("stage") != stage or row.get("status") != "ready":
            raise EvidenceSemanticError(f"dual stage timing contract differs: {stage}")
        try:
            started = int(str(row["start_epoch"]), 10)
            ended = int(str(row["end_epoch"]), 10)
            duration = int(str(row["duration_seconds"]), 10)
        except (TypeError, ValueError) as error:
            raise EvidenceSemanticError(f"dual stage timing is not integral: {stage}") from error
        if started <= 0 or started < previous_end or ended < started or duration != ended - started:
            raise EvidenceSemanticError(f"dual stage timing is inconsistent: {stage}")
        previous_end = ended


def _validate_dual_state(
    path: Path,
    plain_programs: int | None = None,
    agentos_programs: int | None = None,
) -> dict[str, object]:
    value = _json(path, "dual state comparison")
    references = {
        target: expected_reference_identities(target) for target in ("plain", "agentos")
    }
    try:
        return validate_dual_state(value, references, plain_programs, agentos_programs)
    except DualStateContractError as error:
        raise EvidenceSemanticError(str(error)) from error


def _validate_dual(ctx: ValidationContext) -> None:
    backend = _load_module(ctx, "host_tools/backend_evidence_contract.py")
    plain_path = ctx.raw_dir / "dual-plain-qemu.log"
    agentos_path = ctx.raw_dir / "dual-agentos-qemu.log"
    plain_backend = _call(
        backend, "parse_log", "plain dual Guest log", "plain", plain_path
    )
    agentos_backend = _call(
        backend, "parse_log", "AgentOS dual Guest log", "agentos", agentos_path
    )
    if (
        not isinstance(plain_backend, dict)
        or plain_backend.get("cases") != 7
        or not isinstance(agentos_backend, dict)
        or agentos_backend.get("cases") != 8
    ):
        raise EvidenceSemanticError("dual backend case inventory differs")
    plain = _text(plain_path, "plain dual Guest log")
    agentos = _text(agentos_path, "AgentOS dual Guest log")
    _require_line(plain, "rp_orch: passed", "plain dual Guest log")
    _require_line(agentos, "rp_agentos_orch: passed", "AgentOS dual Guest log")
    _require_line(
        agentos,
        "rp_agentos_orch: kernel_agent=1 workflow=rp_orch status=ready",
        "AgentOS dual Guest log",
    )
    plain_inventory = _require_regex(
        plain,
        re.compile(
            r"rp_orch: evidence_role=demo_reference evidence_generation=runtime "
            r"observation_source=guest_runtime "
            r"program_source=rp_orch_timing program_source_bytes=([1-9][0-9]*) "
            r"program_source_hash=([1-9][0-9]*) program_names_digest=([1-9][0-9]*) "
            r"programs_observed=([1-9][0-9]*) status=reference_observed"
        ),
        "plain dual program inventory",
    )
    agent_inventory = _require_regex(
        agentos,
        re.compile(
            r"rp_orch: evidence_role=runtime_verified evidence_generation=runtime "
            r"program_source=rp_orch_timing program_source_bytes=([1-9][0-9]*) "
            r"program_source_hash=([1-9][0-9]*) program_names_digest=([1-9][0-9]*) "
            r"programs_observed=([1-9][0-9]*) status=verified"
        ),
        "AgentOS dual program inventory",
    )
    receipt_keys = (
        "program_source_bytes", "program_source_hash", "program_names_digest",
        "programs_observed",
    )
    plain_receipt = dict(zip(receipt_keys, map(int, plain_inventory.groups())))
    agent_receipt = dict(zip(receipt_keys, map(int, agent_inventory.groups())))
    plain_count = plain_receipt["programs_observed"]
    agent_count = agent_receipt["programs_observed"]
    if plain_count != agent_count:
        raise EvidenceSemanticError("dual program inventories use different program counts")
    for text, label in ((plain, "plain dual Guest log"), (agentos, "AgentOS dual Guest log")):
        _require_line(
            text, f"rp_orch: programs_ok={plain_count} programs_total={plain_count}", label
        )
        _reject_tokens(
            text,
            ("child_failed", "IllegalInstruction", "unknown syscall", "rp_orch: failed", "status=failed"),
            label,
        )
    _require_line(
        plain,
        "rp_compare_plain: evidence_role=demo_reference catalog_generation=demo_expected status=reference_ready",
        "plain dual Guest log",
    )
    compared = _require_regex(
        agentos,
        re.compile(
            r"rp_compare_plain: evidence_generation=runtime "
            r"runtime_assertions_executed=([1-9][0-9]*) "
            r"runtime_assertions_passed=([1-9][0-9]*) status=verified"
        ),
        "AgentOS dual comparison",
    )
    if compared.group(1) != compared.group(2):
        raise EvidenceSemanticError("AgentOS dual comparison did not pass every assertion")
    _validate_dual_stage_csv(ctx.raw_dir / "dual-stage-timings.csv")
    state = _validate_dual_state(
        ctx.raw_dir / "dual-state-compare.json", plain_count, agent_count
    )
    inventories = validate_complete_dual_state(ctx, state)
    validate_program_ledgers(ctx, state, plain_receipt, agent_receipt)
    validate_dual_alignment(ctx, state, plain_count, agent_count, inventories)
    targeted = _parse_guest_stream(
        ctx.raw_dir / "dual-targeted-agentbench-guest.log",
        "dual targeted Agent benchmark Guest log",
    )
    _expect_tags(targeted, {"agent-case:agentbench_ucore"}, "dual targeted benchmark")
    _require_line(
        targeted["agent-case:agentbench_ucore"],
        "agentbench_ucore: parent passed",
        "dual targeted benchmark",
    )
    measured = _load_module(ctx, "host_tools/measured_experiments.py")
    manifest_path = ctx.raw_dir / "dual-measured-experiments.json"
    manifest = _call(measured, "verify_manifest", "dual measured experiments", manifest_path, ctx.raw_dir)
    if not isinstance(manifest, dict) or manifest.get("source", {}).get("path") != "dual-targeted-agentbench-guest.log":
        raise EvidenceSemanticError("dual measurement source is not the targeted Guest log")
    _call(
        measured,
        "verify_measurement_artifact_set",
        "dual measured experiment set",
        manifest_path,
        ctx.raw_dir / "dual-file-query-benchmark.csv",
        ctx.raw_dir,
        manifest.get("commit"),
        "dual-targeted-agentbench-guest.log",
    )


def _combined_rule(
    ctx: ValidationContext, filename: str, runner_label: str, final_marker: str
) -> CombinedLog:
    result = _parse_combined(ctx.raw_dir / filename, filename, runner_label)
    _require_line(result.stdout, final_marker, f"{filename} runner output")
    return result


def _validate_proc(ctx: ValidationContext) -> None:
    result = _combined_rule(ctx, "proc-reap.log", "proc-reap", "[proc-reap] both targets passed")
    expected = {
        "proc-reap:agent": "procreap_ucore: parent passed",
        "proc-reap:agent-adversarial": "procreap_agent_ucore: parent passed",
        "proc-reap:baseline": "procreap_ucore: parent passed",
    }
    _expect_tags(result.guests, set(expected), "proc-reap.log")
    kernel = _load_module(ctx, "scripts/validate-kernel-test-log.py")
    for tag in expected:
        _call(kernel, "validate_proc_reap", tag, result.guests[tag])


def _validate_syscall(ctx: ValidationContext) -> None:
    result = _combined_rule(
        ctx, "syscall-fairness.log", "syscall-fairness", "[syscall-fairness] both targets passed"
    )
    expected = {"syscall-fairness:agent", "syscall-fairness:baseline"}
    _expect_tags(result.guests, expected, "syscall-fairness.log")
    kernel = _load_module(ctx, "scripts/validate-kernel-test-log.py")
    for tag in sorted(expected):
        _call(kernel, "validate_syscall", tag, result.guests[tag])


def _validate_file_resource(ctx: ValidationContext) -> None:
    result = _combined_rule(
        ctx, "file-resource.log", "file-resource", "[file-resource] both targets passed"
    )
    expected = {"file-resource:agent", "file-resource:baseline"}
    _expect_tags(result.guests, expected, "file-resource.log")
    kernel = _load_module(ctx, "scripts/validate-kernel-test-log.py")
    for tag in sorted(expected):
        _call(kernel, "validate_file", tag, result.guests[tag])


def _validate_thread_resource(ctx: ValidationContext) -> None:
    result = _combined_rule(
        ctx, "thread-resource.log", "thread-resource", "[thread-resource] all checks passed"
    )
    _expect_tags(result.guests, {"thread-resource"}, "thread-resource.log")
    kernel = _load_module(ctx, "scripts/validate-kernel-test-log.py")
    _call(kernel, "validate_thread", "thread-resource", result.guests["thread-resource"])


def _validate_physical_resource(ctx: ValidationContext) -> None:
    result = _combined_rule(
        ctx, "physical-resource.log", "physical-resource", "[physical-resource] all checks passed"
    )
    _expect_tags(result.guests, {"physical-resource"}, "physical-resource.log")
    kernel = _load_module(ctx, "scripts/validate-kernel-test-log.py")
    _call(
        kernel, "validate_physical_resource", "physical-resource", result.guests["physical-resource"]
    )


LEASE_MARKER = re.compile(
    r"agentobsreboot_ucore: (?P<tag>lease_cut_alloc|lease_cut_successor) "
    r"audit=(?P<audit>[0-9]+) span=(?P<span>[0-9]+) "
    r"event=(?P<event>[0-9]+) control=(?P<control>[0-9]+) "
    r"agent=(?P<agent>[0-9]+) lifecycle_slot=(?P<slot>[0-9]+) "
    r"lifecycle_generation=(?P<generation>[0-9]+)"
)


def _validate_observe(ctx: ValidationContext) -> None:
    final = "[observe-recovery] power-cut lease and three-boot durable evidence lifecycle passed"
    result = _combined_rule(ctx, "observe-recovery.log", "observe-recovery", final)
    expected = {f"observe-recovery-boot{index}" for index in (1, 2, 3)} | {
        "observe-recovery-boot0-cut"
    }
    _expect_tags(result.guests, expected, "observe-recovery.log")
    before_reap = ctx.raw_dir / "observe-recovery-before-reap.img"
    _regular_bytes(before_reap, "observation pre-reap disk image")
    disk_evidence = _load_module(ctx, "host_tools/agent_observe_disk_evidence.py")
    disk_result = _call(
        disk_evidence,
        "verify_observation_image",
        "observation durable disk image",
        before_reap,
        result.guests["observe-recovery-boot1"],
        ctx.repo_root / "ci" / "agent-metadata-disk-format.json",
        ctx.repo_root / "ci" / "agent-observe-disk-format.json",
    )
    acceptance = _load_module(ctx, "host_tools/agent_observe_disk_acceptance.py")
    layout = _call(
        disk_evidence, "load_observation_contract", "observation disk contract",
        ctx.repo_root / "ci" / "agent-observe-disk-format.json",
    )
    disk_result = _call(
        acceptance, "validate_observation_acceptance",
        "observation v8 retention acceptance", disk_result, layout,
    )
    if not isinstance(disk_result, dict) or disk_result.get("status") != "verified":
        raise EvidenceSemanticError("observation durable disk result is invalid")
    cut = _require_regex(result.guests["observe-recovery-boot0-cut"], LEASE_MARKER, "observe cut lease")
    successor = _require_regex(result.guests["observe-recovery-boot1"], LEASE_MARKER, "observe successor lease")
    if cut.group("tag") != "lease_cut_alloc" or successor.group("tag") != "lease_cut_successor":
        raise EvidenceSemanticError("observation lease marker roles differ")
    for name in ("audit", "span", "event", "control", "agent"):
        if int(successor.group(name)) <= int(cut.group(name)):
            raise EvidenceSemanticError(f"observation durable lease reused {name}")
    if successor.group("slot") != cut.group("slot") or int(successor.group("generation")) <= int(cut.group("generation")):
        raise EvidenceSemanticError("observation lifecycle lease did not advance safely")
    required = {
        "observe-recovery-boot0-cut": (
            "agentobsreboot_ucore: receipt_permission_not_agent=1",
        ),
        "observe-recovery-boot1": (
            "agentobsreboot_ucore: boot1_checkpoint_ready=1",
            "agentobsreboot_ucore: receipt_pending_not_evidence=1 receipt_durable_exact=1 receipt_fake_stale=1 receipt_window_not_evidence=1",
        ),
        "observe-recovery-boot2": (
            "agentobsreboot_ucore: boot2_reap_replicated=1",
            "agentobsreboot_ucore: receipt_teardown_stale=1",
            "agentobsreboot_ucore: receipt_permission_recovery_denied=1",
            "agentobsreboot_ucore: receipt_recovery_exact=1 receipt_v1_compatible=1 bank_generation_bound=1",
        ),
        "observe-recovery-boot3": (
            "agentobsreboot_ucore: boot3_erased=1 generation_isolated=1 stable_identity=1",
            "agentobsreboot_ucore: timeline_wait_epoch_recheck=1 injection=2 retries=1 bounded_timeout=1",
            "agentobsreboot_ucore: timeline_wait_threads=1 filters=2 deadlines=2 targeted=1 timeout=1 cleanup=1",
            "agentobsreboot_ucore: parent passed",
        ),
    }
    for tag, markers in required.items():
        for marker in markers:
            _require_line(result.guests[tag], marker, tag)


def _validate_virtio(ctx: ValidationContext) -> None:
    result = _combined_rule(
        ctx, "virtio-disk.log", "virtio-disk", "[virtio-disk] fault matrix passed"
    )
    _expect_tags(result.guests, {"virtio-disk:fault-matrix"}, "virtio-disk.log")
    validator = _load_module(ctx, "scripts/validate-virtio-disk-log.py")
    _call(
        validator,
        "validate_text",
        "VirtIO fault-matrix Guest log",
        result.guests["virtio-disk:fault-matrix"],
    )


def _validate_workflow(ctx: ValidationContext) -> None:
    result = _combined_rule(
        ctx,
        "workflow-teardown-race.log",
        "workflow-teardown-race",
        "[workflow-teardown] 3 stable runs passed",
    )
    expected = {f"workflow-teardown:{index}" for index in range(1, 4)}
    _expect_tags(result.guests, expected, "workflow-teardown-race.log")
    kernel = _load_module(ctx, "scripts/validate-kernel-test-log.py")
    for tag in sorted(expected):
        _call(kernel, "validate_workflow_teardown", tag, result.guests[tag], 14, 64)


FS_ENOSPC_CASES = {
    "fs-enospc:agent": ("generic", "fsenospc_ucore: parent passed"),
    "fs-enospc:baseline": ("generic", "fsenospc_ucore: parent passed"),
    "fs-enospc:quota-domain": ("domain", "fsquota_ucore: parent passed"),
    "fs-enospc:quota-reserve": ("reserve", "fsquota_ucore: parent passed"),
    "fs-enospc:principal-agent-orphan": ("orphan-crash", "fspquota_ucore: crash_orphan_ready=1"),
    "fs-enospc:principal-agent-seed": (
        "persistent-seed", "fspquota_ucore: durable_fixture=1 blocks=18 inodes=8 owner_exited=1"
    ),
    "fs-enospc:principal-agent-verify": ("persistent-verify", "fspquota_ucore: parent passed"),
    "fs-enospc:principal-baseline-orphan": ("orphan-crash", "fspquota_ucore: crash_orphan_ready=1"),
    "fs-enospc:principal-baseline-seed": (
        "persistent-seed", "fspquota_ucore: durable_fixture=1 blocks=18 inodes=8 owner_exited=1"
    ),
    "fs-enospc:principal-baseline-verify": ("persistent-verify", "fspquota_ucore: parent passed"),
}


def _validate_fs_enospc(ctx: ValidationContext) -> None:
    result = _combined_rule(
        ctx,
        "fs-enospc.log",
        "fs-enospc",
        "[fs-enospc] generic, persistent principal, and Agent quota cases passed",
    )
    _expect_tags(result.guests, set(FS_ENOSPC_CASES), "fs-enospc.log")
    kernel = _load_module(ctx, "scripts/validate-kernel-test-log.py")
    for tag, (profile, marker) in FS_ENOSPC_CASES.items():
        text = result.guests[tag]
        _require_line(text, marker, tag)
        _call(kernel, "validate_fs", tag, text, profile, marker)


def _validate_fs_allocator(ctx: ValidationContext) -> None:
    final = "[fs-allocator-fault] dynamic matrix, negative mutant, and raw evidence passed"
    _combined_rule(ctx, "fs-allocator-fault.log", "fs-allocator-fault", final)
    archive = ctx.raw_dir / "fs-allocator-evidence.tar"
    _regular_bytes(archive, "filesystem allocator evidence archive")
    verifier = _load_module(ctx, "scripts/fs-allocator-evidence.py")
    _call(
        verifier,
        "verify_archive",
        "filesystem allocator evidence archive",
        archive,
    )
