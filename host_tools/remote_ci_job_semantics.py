#!/usr/bin/env python3
"""Project remote GitLab artifacts into the shared evidence semantic registry."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Callable

from evidence_semantic_registry import EvidenceSemanticError, validate_selected_artifacts


MAX_TEXT_BYTES = 128 * 1024 * 1024
MAX_BINARY_BYTES = 1024 * 1024 * 1024
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
READER_RUN = re.compile(
    r"reader-e2e-raw/(run-[A-Za-z0-9._-]{1,96})/"
    r"(ucore-build\.log|ucore-run\.log|ucore-run-summary\.json)\Z"
)


class RemoteCIJobSemanticError(ValueError):
    pass


ArtifactReader = Callable[[str, int], bytes]
ArtifactMaterializer = Callable[[str, Path, int], None]


JOB_RULES: dict[str, tuple[str, ...]] = {
    "reader-e2e": ("reader-e2e",),
    "agent-regression": ("agent-suite",),
    "kernel-mechanism-regression": (
        "proc-reap",
        "syscall-fairness",
        "file-resource",
        "thread-resource",
        "workflow-teardown-race",
        "fs-enospc",
    ),
    "physical-resource-regression": ("physical-resource",),
    "metadata-recovery-regression": ("metadata-recovery",),
    "observe-recovery-regression": ("observe-recovery",),
    "virtio-disk-regression": ("virtio-disk",),
    "fs-allocator-fault-regression": ("fs-allocator-fault",),
}


MECHANISM_LABELS: dict[str, tuple[str, ...]] = {
    job: rules
    for job, rules in JOB_RULES.items()
    if job not in {"reader-e2e", "agent-regression"}
}


def _lines(raw: bytes, label: str) -> list[str]:
    if not raw or len(raw) > MAX_TEXT_BYTES or b"\0" in raw:
        raise RemoteCIJobSemanticError(f"{label} is empty or not bounded text")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RemoteCIJobSemanticError(f"{label} is not UTF-8") from error
    return [ANSI.sub("", line).rstrip("\r") for line in text.splitlines()]


def _require_once(raw: bytes, marker: str, label: str) -> None:
    if _lines(raw, label).count(marker) != 1:
        raise RemoteCIJobSemanticError(f"{label} lacks one exact marker {marker!r}")


def _require_once_matching(raw: bytes, pattern: re.Pattern[str], label: str) -> None:
    matches = [line for line in _lines(raw, label) if pattern.fullmatch(line)]
    if len(matches) != 1:
        raise RemoteCIJobSemanticError(
            f"{label} lacks one sanitizer-backed marker {pattern.pattern!r}"
        )


def _stage(
    materialize: ArtifactMaterializer,
    source: str,
    destination_root: Path,
    destination_name: str,
    maximum: int = MAX_TEXT_BYTES,
) -> None:
    destination = destination_root / destination_name
    if destination.exists() or destination.is_symlink():
        raise RemoteCIJobSemanticError(f"duplicate semantic projection: {destination_name}")
    materialize(source, destination, maximum)


def _validate_kernel(paths: set[str], read: ArtifactReader) -> None:
    name = "kernel-budgets.log"
    if paths != {name}:
        raise RemoteCIJobSemanticError("kernel budget artifact inventory differs")
    raw = read(name, MAX_TEXT_BYTES)
    for marker in (
        "[dual-target-check] docs: wording scan passed",
        "[kernel-budget] kernel checks passed",
        "[kernel-budget] agent-modules checks passed",
    ):
        _require_once(raw, marker, name)
    for pattern in (
        re.compile(
            r"\[printf-format\] host probes and [1-9][0-9]* mutations passed; "
            r"audited=[1-9][0-9]*; mode=ASan/UBSan"
        ),
        re.compile(
            r"\[rp-evidence-field\] streaming and malformed-input probes passed; "
            r"mode=ASan/UBSan"
        ),
        re.compile(
            r"\[rp-state-append\] canonical boundary probes passed; "
            r"mode=ASan/UBSan"
        ),
    ):
        _require_once_matching(raw, pattern, name)


def _stage_reader(
    paths: set[str], materialize: ArtifactMaterializer, destination: Path
) -> None:
    required = {
        "reader-e2e.log": "reader-e2e.log",
        "reader-e2e-raw/reader-e2e-log-manifest.json": "reader-e2e-log-manifest.json",
    }
    for source, target in required.items():
        if source not in paths:
            raise RemoteCIJobSemanticError(f"Reader artifact is missing: {source}")
        _stage(materialize, source, destination, target)
    run_count = 0
    projected = set(required)
    for source in sorted(paths):
        match = READER_RUN.fullmatch(source)
        if match is None:
            continue
        run_count += 1
        projected.add(source)
        _stage(
            materialize,
            source,
            destination,
            f"reader-e2e-{match.group(1)}-{match.group(2)}",
        )
    if run_count == 0:
        raise RemoteCIJobSemanticError("Reader artifact contains no persistent run logs")
    if paths != projected:
        raise RemoteCIJobSemanticError("Reader artifact inventory contains unknown paths")


def _stage_agent(
    paths: set[str],
    read: ArtifactReader,
    materialize: ArtifactMaterializer,
    destination: Path,
) -> None:
    required = {
        "agent-suite-timings.log",
        "agent-suite-guest.log",
        "agent-regression-job.log",
    }
    if paths != required:
        raise RemoteCIJobSemanticError("Agent suite artifact inventory differs")
    _require_once(
        read("agent-regression-job.log", MAX_TEXT_BYTES),
        "[agent-tests] all Agent-OS uCore checks passed",
        "agent-regression-job.log",
    )
    _require_once(
        read("agent-regression-job.log", MAX_TEXT_BYTES),
        "[agent-tests] duration-profile profile=none gate=skipped "
        "reason=different-runner",
        "agent-regression-job.log",
    )
    for name in ("agent-suite-timings.log", "agent-suite-guest.log"):
        _stage(materialize, name, destination, name)


def _stage_mechanisms(
    job: str,
    paths: set[str],
    read: ArtifactReader,
    materialize: ArtifactMaterializer,
    destination: Path,
) -> None:
    expected: set[str] = set()
    for label in MECHANISM_LABELS[job]:
        job_name = f"{label}-job.log"
        guest_name = f"{label}-guest.log"
        combined_name = f"{label}-combined.log"
        expected.update((job_name, guest_name, combined_name))
        if not {job_name, guest_name, combined_name}.issubset(paths):
            raise RemoteCIJobSemanticError(f"mechanism evidence is incomplete: {label}")
        stdout = read(job_name, MAX_TEXT_BYTES)
        guest = read(guest_name, MAX_TEXT_BYTES)
        combined = read(combined_name, MAX_TEXT_BYTES)
        combined_expected = (
            f"===== runner-stdout:{label} =====\n".encode("ascii")
            + stdout
            + f"\n===== runner-guest-logs:{label} =====\n".encode("ascii")
            + guest
        )
        if not stdout or not guest or combined != combined_expected:
            raise RemoteCIJobSemanticError(f"mechanism combined log is not reproducible: {label}")
        _stage(materialize, combined_name, destination, f"{label}.log")
    if job == "observe-recovery-regression":
        image = "observe-recovery-before-reap.img"
        expected.add(image)
        if image not in paths:
            raise RemoteCIJobSemanticError("observation pre-reap image is missing")
        _stage(materialize, image, destination, image, MAX_BINARY_BYTES)
    elif job == "fs-allocator-fault-regression":
        archive = "fs-allocator-evidence.tar"
        expected.add(archive)
        if archive not in paths:
            raise RemoteCIJobSemanticError("filesystem allocator archive is missing")
        _stage(materialize, archive, destination, archive, MAX_BINARY_BYTES)
    if paths != expected:
        raise RemoteCIJobSemanticError("mechanism artifact inventory contains unknown paths")


def validate_remote_job_semantics(
    job: str,
    paths: set[str],
    read: ArtifactReader,
    materialize: ArtifactMaterializer,
    repo_root: Path,
) -> None:
    """Validate one remote job through the same registry as final evidence."""
    if job == "kernel-budgets":
        _validate_kernel(paths, read)
        return
    rules = JOB_RULES.get(job)
    if rules is None:
        raise RemoteCIJobSemanticError(f"remote job has no semantic projection: {job}")
    with tempfile.TemporaryDirectory(prefix=f"agentos-remote-semantic-{job}-") as temporary:
        destination = Path(temporary)
        if job == "reader-e2e":
            _stage_reader(paths, materialize, destination)
        elif job == "agent-regression":
            _stage_agent(paths, read, materialize, destination)
        else:
            _stage_mechanisms(job, paths, read, materialize, destination)
        try:
            validate_selected_artifacts(
                rules, destination, repo_root, require_exact_inventory=True
            )
        except EvidenceSemanticError as error:
            raise RemoteCIJobSemanticError(
                f"shared evidence semantic registry rejected {job}: {error}"
            ) from error


__all__ = ["RemoteCIJobSemanticError", "validate_remote_job_semantics"]
