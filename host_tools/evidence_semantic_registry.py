#!/usr/bin/env python3
"""Offline semantic registry for raw final-evidence artifacts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dual_state_evidence_contract import DUAL_STATE_RAW_ARTIFACTS

from evidence_semantic_common import (
    EvidenceSemanticError,
    ValidationContext,
    _regular_bytes,
)
from evidence_semantic_profiles import (
    _validate_agent_suite,
    _validate_dual,
    _validate_file_resource,
    _validate_fs_allocator,
    _validate_fs_enospc,
    _validate_metadata,
    _validate_observe,
    _validate_physical_resource,
    _validate_proc,
    _validate_syscall,
    _validate_thread_resource,
    _validate_virtio,
    _validate_workflow,
)


@dataclass(frozen=True)
class RawArtifactRule:
    name: str
    artifacts: tuple[str, ...]
    validator: Callable[[ValidationContext], None]


RAW_ARTIFACT_REGISTRY = (
    RawArtifactRule(
        "agent-suite",
        ("agent-suite-timings.log", "agent-suite-guest.log"),
        _validate_agent_suite,
    ),
    RawArtifactRule(
        "dual-platforms",
        (
            "dual-plain-qemu.log", "dual-agentos-qemu.log", "dual-stage-timings.csv",
            "dual-state-compare.json",
            "host-platform-alignment.json", *DUAL_STATE_RAW_ARTIFACTS,
            "dual-targeted-agentbench-guest.log", "dual-measured-experiments.json",
            "dual-file-query-benchmark.csv",
        ),
        _validate_dual,
    ),
    RawArtifactRule("proc-reap", ("proc-reap.log",), _validate_proc),
    RawArtifactRule("syscall-fairness", ("syscall-fairness.log",), _validate_syscall),
    RawArtifactRule("file-resource", ("file-resource.log",), _validate_file_resource),
    RawArtifactRule("thread-resource", ("thread-resource.log",), _validate_thread_resource),
    RawArtifactRule("physical-resource", ("physical-resource.log",), _validate_physical_resource),
    RawArtifactRule("metadata-recovery", ("metadata-recovery.log",), _validate_metadata),
    RawArtifactRule(
        "observe-recovery",
        ("observe-recovery.log", "observe-recovery-before-reap.img"),
        _validate_observe,
    ),
    RawArtifactRule("virtio-disk", ("virtio-disk.log",), _validate_virtio),
    RawArtifactRule(
        "workflow-teardown-race", ("workflow-teardown-race.log",), _validate_workflow
    ),
    RawArtifactRule("fs-enospc", ("fs-enospc.log",), _validate_fs_enospc),
    RawArtifactRule(
        "fs-allocator-fault",
        ("fs-allocator-fault.log", "fs-allocator-evidence.tar"),
        _validate_fs_allocator,
    ),
)


def _validation_context(raw_dir: Path, repo_root: Path) -> ValidationContext:
    raw_dir = Path(raw_dir)
    repo_root = Path(repo_root)
    if raw_dir.is_symlink() or not raw_dir.is_dir():
        raise EvidenceSemanticError(f"raw artifact directory is missing or unsafe: {raw_dir}")
    if repo_root.is_symlink() or not repo_root.is_dir():
        raise EvidenceSemanticError(f"repository root is missing or unsafe: {repo_root}")
    return ValidationContext(raw_dir=raw_dir.resolve(), repo_root=repo_root.resolve())


def _registry_index() -> tuple[dict[str, RawArtifactRule], set[str]]:
    selectors: dict[str, RawArtifactRule] = {}
    artifact_names: set[str] = set()
    for rule in RAW_ARTIFACT_REGISTRY:
        if rule.name in selectors or not rule.artifacts or not callable(rule.validator):
            raise EvidenceSemanticError(f"semantic registry rule is invalid: {rule.name}")
        duplicates = artifact_names.intersection(rule.artifacts)
        if duplicates:
            raise EvidenceSemanticError(
                f"semantic registry assigns artifacts more than once: {sorted(duplicates)}"
            )
        artifact_names.update(rule.artifacts)
        selectors[rule.name] = rule
        for artifact in rule.artifacts:
            if artifact in selectors:
                raise EvidenceSemanticError(f"semantic registry selector is ambiguous: {artifact}")
            selectors[artifact] = rule
    return selectors, artifact_names


def _run_rules(context: ValidationContext, rules: tuple[RawArtifactRule, ...]) -> None:
    required = {artifact for rule in rules for artifact in rule.artifacts}
    context.allowed_files.update(required)
    for name in sorted(required):
        _regular_bytes(context.raw_dir / name, f"raw artifact {name}")
    for rule in rules:
        try:
            rule.validator(context)
        except EvidenceSemanticError as error:
            raise EvidenceSemanticError(f"{rule.name}: {error}") from error
        except Exception as error:
            raise EvidenceSemanticError(f"{rule.name}: unexpected validator failure: {error}") from error


def _require_exact_inventory(context: ValidationContext) -> None:
    actual: set[str] = set()
    for path in context.raw_dir.iterdir():
        if path.is_symlink() or not path.is_file():
            raise EvidenceSemanticError(f"raw artifact entry is unsafe: {path.name}")
        actual.add(path.name)
    if actual != context.allowed_files:
        raise EvidenceSemanticError(
            "raw artifact inventory differs from the semantic registry: "
            f"missing={sorted(context.allowed_files - actual)} "
            f"extra={sorted(actual - context.allowed_files)}"
        )


def validate_selected_artifacts(
    names: tuple[str, ...] | list[str] | set[str], raw_dir: Path, repo_root: Path,
    require_exact_inventory: bool = False,
) -> None:
    """Validate selected registry rules, allowing unrelated files in ``raw_dir``."""
    if isinstance(names, (str, bytes)) or not names:
        raise EvidenceSemanticError("semantic artifact selection is empty or invalid")
    requested = tuple(names)
    if any(not isinstance(name, str) for name in requested):
        raise EvidenceSemanticError("semantic artifact selection is empty or invalid")
    selectors, _ = _registry_index()
    unknown = sorted(name for name in requested if name not in selectors)
    if unknown:
        raise EvidenceSemanticError(f"unknown semantic artifact selection: {unknown}")
    selected = {selectors[name] for name in requested}
    rules = tuple(rule for rule in RAW_ARTIFACT_REGISTRY if rule in selected)
    context = _validation_context(raw_dir, repo_root)
    _run_rules(context, rules)
    if require_exact_inventory:
        _require_exact_inventory(context)


def validate_artifact(
    name: str, raw_dir: Path, repo_root: Path, require_exact_inventory: bool = False
) -> None:
    """Validate the registry rule owning one artifact or rule name."""
    validate_selected_artifacts(
        (name,), raw_dir, repo_root, require_exact_inventory=require_exact_inventory
    )


def validate_raw_artifacts(
    raw_dir: Path, repo_root: Path, require_exact_inventory: bool = True
) -> None:
    """Validate every registered artifact, optionally enforcing exact inventory."""
    context = _validation_context(raw_dir, repo_root)
    _, artifact_names = _registry_index()
    _run_rules(context, RAW_ARTIFACT_REGISTRY)
    if not require_exact_inventory:
        return
    _require_exact_inventory(context)


__all__ = [
    "EvidenceSemanticError",
    "RAW_ARTIFACT_REGISTRY",
    "RawArtifactRule",
    "validate_artifact",
    "validate_raw_artifacts",
    "validate_selected_artifacts",
]
