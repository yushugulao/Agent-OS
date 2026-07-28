#!/usr/bin/env python3
"""Build real remote-CI proof ZIPs for no-QEMU collector tests."""
from __future__ import annotations

import copy
import hashlib
import io
import tempfile
import zipfile
from pathlib import Path

from remote_ci_evidence import CIIdentity, build_attestation, canonical_json, marker_for


JOB_SPECS = (
    ("kernel-budgets", 801, 901, "agentos-host-calibrated"),
    ("reader-e2e", 802, 902, "agentos-qemu-calibrated"),
    ("agent-regression", 803, 902, "agentos-qemu-calibrated"),
    ("kernel-mechanism-regression", 804, 902, "agentos-qemu-calibrated"),
    ("physical-resource-regression", 805, 902, "agentos-qemu-calibrated"),
    ("metadata-recovery-regression", 806, 902, "agentos-qemu-calibrated"),
    ("observe-recovery-regression", 807, 902, "agentos-qemu-calibrated"),
    ("virtio-disk-regression", 808, 902, "agentos-qemu-calibrated"),
    ("fs-allocator-fault-regression", 809, 902, "agentos-qemu-calibrated"),
)

JOB_LABELS = {
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


def _reader_files(raw: Path) -> dict[str, bytes]:
    result = {
        "reader-e2e.log": (raw / "reader-e2e.log").read_bytes(),
        "reader-e2e-raw/reader-e2e-log-manifest.json": (
            raw / "reader-e2e-log-manifest.json"
        ).read_bytes(),
    }
    prefix = "reader-e2e-"
    for path in sorted(raw.glob("reader-e2e-run-*-ucore-*")):
        remainder = path.name.removeprefix(prefix)
        run, separator, name = remainder.rpartition("-ucore-")
        if not separator:
            raise AssertionError(f"invalid Reader fixture artifact: {path.name}")
        result[f"reader-e2e-raw/{run}/ucore-{name}"] = path.read_bytes()
    return result


def _mechanism_files(raw: Path, label: str) -> dict[str, bytes]:
    combined = (raw / f"{label}.log").read_bytes()
    header = f"===== runner-stdout:{label} =====\n".encode("ascii")
    separator = f"\n===== runner-guest-logs:{label} =====\n".encode("ascii")
    if not combined.startswith(header) or combined.count(separator) != 1:
        raise AssertionError(f"invalid combined fixture artifact: {label}")
    stdout, guest = combined[len(header) :].split(separator, 1)
    return {
        f"{label}-job.log": stdout,
        f"{label}-guest.log": guest,
        f"{label}-combined.log": combined,
    }


def _job_files(name: str, bundle: Path) -> dict[str, bytes]:
    raw = bundle / "logs" / "raw"
    if name == "kernel-budgets":
        return {
            "kernel-budgets.log": (
                b"[dual-target-check] docs: wording scan passed\n"
                b"[kernel-budget] kernel checks passed\n"
                b"[kernel-budget] agent-modules checks passed\n"
            )
        }
    if name == "reader-e2e":
        return _reader_files(raw)
    if name == "agent-regression":
        return {
            "agent-suite-timings.log": (raw / "agent-suite-timings.log").read_bytes(),
            "agent-suite-guest.log": (raw / "agent-suite-guest.log").read_bytes(),
            "agent-regression-job.log": b"[agent-tests] all Agent-OS uCore checks passed\n",
        }
    result: dict[str, bytes] = {}
    for label in JOB_LABELS[name]:
        result.update(_mechanism_files(raw, label))
    if name == "observe-recovery-regression":
        result["observe-recovery-before-reap.img"] = (
            raw / "observe-recovery-before-reap.img"
        ).read_bytes()
    elif name == "fs-allocator-fault-regression":
        result["fs-allocator-evidence.tar"] = (
            raw / "fs-allocator-evidence.tar"
        ).read_bytes()
    return result


def _forge_physical(files: dict[str, bytes]) -> dict[str, bytes]:
    stdout = b"[physical-resource] all checks passed\n"
    guest = (
        b"===== guest:physical-resource =====\n"
        b"physicalresource_ucore: parent passed\n"
        b"===== end-guest:physical-resource =====\n"
    )
    combined = (
        b"===== runner-stdout:physical-resource =====\n"
        + stdout
        + b"\n===== runner-guest-logs:physical-resource =====\n"
        + guest
    )
    forged = dict(files)
    forged.update(
        {
            "physical-resource-job.log": stdout,
            "physical-resource-guest.log": guest,
            "physical-resource-combined.log": combined,
        }
    )
    return forged


def _archive(files: dict[str, bytes], attestation: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(files.items()):
            archive.writestr(f"ci-artifacts/{name}", payload)
        archive.writestr("ci-artifacts/remote-ci-attestation.json", attestation)
    return output.getvalue()


def build_remote_job_payloads(
    repo_root: Path,
    bundle: Path,
    commit: str,
    forge_semantic_job: str | None = None,
) -> dict[int, tuple[bytes, bytes]]:
    """Return job-id keyed trace and ZIP payloads bound to ``commit``."""
    repo_root, bundle = Path(repo_root).resolve(), Path(bundle).resolve()
    result: dict[int, tuple[bytes, bytes]] = {}
    for name, job_id, runner_id, runner_tag in JOB_SPECS:
        files = _job_files(name, bundle)
        with tempfile.TemporaryDirectory(prefix=f"remote-ci-fixture-{name}-") as temporary:
            artifact_root = Path(temporary)
            for relative, payload in files.items():
                path = artifact_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            identity = CIIdentity(
                project_id=39809,
                project_path="contest/agentos",
                pipeline_id=701,
                pipeline_source="push",
                job_id=job_id,
                job_name=name,
                commit=commit,
                ref="main",
                runner_id=runner_id,
                runner_tags=(runner_tag,),
            )
            attestation = build_attestation(identity, artifact_root, repo_root)[0]
        if name == forge_semantic_job:
            files = _forge_physical(files)
            attestation = copy.deepcopy(attestation)
            attestation["artifacts"] = [
                {
                    "path": relative,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                for relative, payload in sorted(files.items())
            ]
        raw_attestation = canonical_json(attestation)
        trace = (marker_for(attestation) + "\n").encode("ascii")
        result[job_id] = (trace, _archive(files, raw_attestation))
    return result


__all__ = ["JOB_SPECS", "build_remote_job_payloads"]
