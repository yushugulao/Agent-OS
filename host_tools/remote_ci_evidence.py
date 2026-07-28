#!/usr/bin/env python3
"""Create fail-closed attestations for the repository's GitLab jobs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from strict_json import strict_json_loads


ATTESTATION_FORMAT = "agentos-remote-ci-job-attestation-v1"
ATTESTATION_NAME = "remote-ci-attestation.json"
ATTESTATION_ARCHIVE_PATH = f"ci-artifacts/{ATTESTATION_NAME}"
MARKER_PREFIX = "AGENTOS_REMOTE_CI_ATTESTATION_V1"
SEMANTIC_REGISTRY = "agentos-remote-ci-semantics"
SEMANTIC_VERSION = 1
SOURCE_CONTRACT_FILES = (
    ".gitlab-ci.yml",
    "ci/agent-metadata-disk-format.json",
    "ci/agent-observe-disk-format.json",
    "ci/kernel-budgets.json",
    "host_tools/agent_metadata_disk_format.py",
    "host_tools/agent_observe_disk_acceptance.py",
    "host_tools/agent_observe_disk_contract.py",
    "host_tools/agent_observe_disk_evidence.py",
    "host_tools/evidence_semantic_common.py",
    "host_tools/evidence_semantic_metadata.py",
    "host_tools/evidence_semantic_profiles.py",
    "host_tools/evidence_semantic_registry.py",
    "host_tools/gitlab_ci_contract.py",
    "host_tools/plain_ucore_fs_extract.py",
    "host_tools/remote_ci_archive.py",
    "host_tools/remote_ci_bundle.py",
    "host_tools/remote_ci_evidence.py",
    "host_tools/remote_ci_job_semantics.py",
    "host_tools/research_state_manifest.py",
    "scripts/fs-allocator-evidence.py",
    "scripts/fs-allocator-image.py",
    "scripts/validate-kernel-test-log.py",
    "scripts/validate-metadata-crash-log.py",
    "scripts/validate-metadata-reprobe-log.py",
    "scripts/validate-virtio-disk-log.py",
)
SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_JOB_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
SAFE_PROJECT_RE = re.compile(
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\Z"
)
MAX_ATTESTATION_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 1024 * 1024 * 1024


class RemoteCIEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class JobProfile:
    runner_class: str
    runner_tag: str


JOB_PROFILES: dict[str, JobProfile] = {
    "kernel-budgets": JobProfile("host", "agentos-host-calibrated"),
    "reader-e2e": JobProfile("qemu", "agentos-qemu-calibrated"),
    "agent-regression": JobProfile("qemu", "agentos-qemu-calibrated"),
    "kernel-mechanism-regression": JobProfile("qemu", "agentos-qemu-calibrated"),
    "physical-resource-regression": JobProfile("qemu", "agentos-qemu-calibrated"),
    "metadata-recovery-regression": JobProfile("qemu", "agentos-qemu-calibrated"),
    "observe-recovery-regression": JobProfile("qemu", "agentos-qemu-calibrated"),
    "virtio-disk-regression": JobProfile("qemu", "agentos-qemu-calibrated"),
    "fs-allocator-fault-regression": JobProfile("qemu", "agentos-qemu-calibrated"),
}


@dataclass(frozen=True)
class CIIdentity:
    project_id: int
    project_path: str
    pipeline_id: int
    pipeline_source: str
    job_id: int
    job_name: str
    commit: str
    ref: str
    runner_id: int
    runner_tags: tuple[str, ...]


@dataclass(frozen=True)
class FileIdentity:
    path: str
    size: int
    sha256: str

    def as_json(self) -> dict[str, object]:
        return {"path": self.path, "bytes": self.size, "sha256": self.sha256}


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise RemoteCIEvidenceError(f"{label} must be a positive integer")
    try:
        parsed = int(str(value), 10)
    except (TypeError, ValueError) as error:
        raise RemoteCIEvidenceError(f"{label} must be a positive integer") from error
    if parsed <= 0 or str(parsed) != str(value):
        raise RemoteCIEvidenceError(f"{label} must be a canonical positive integer")
    return parsed


def _exact_keys(value: object, expected: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise RemoteCIEvidenceError(f"{label} has an invalid field set")
    return value


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_relative(value: str, label: str = "artifact path") -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or "\\" in value
        or "\0" in value
        or ":" in value
        or value.startswith("/")
        or any(ord(character) < 32 for character in value)
    ):
        raise RemoteCIEvidenceError(f"{label} is unsafe")
    pure = PurePosixPath(value)
    if any(part in {"", ".", ".."} or len(part) > 128 for part in pure.parts):
        raise RemoteCIEvidenceError(f"{label} is unsafe")
    if pure.as_posix() != value:
        raise RemoteCIEvidenceError(f"{label} is not canonical")
    return value


def _read_regular(path: Path, label: str, maximum: int) -> bytes:
    try:
        lexical = path.lstat()
    except OSError as error:
        raise RemoteCIEvidenceError(f"{label} is unavailable: {error}") from error
    if stat.S_ISLNK(lexical.st_mode) or not stat.S_ISREG(lexical.st_mode):
        raise RemoteCIEvidenceError(f"{label} is not a regular non-symlink file")
    if lexical.st_size < 0 or lexical.st_size > maximum:
        raise RemoteCIEvidenceError(f"{label} exceeds its size budget")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RemoteCIEvidenceError(f"{label} could not be opened safely: {error}") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != lexical.st_dev
            or opened.st_ino != lexical.st_ino
            or opened.st_size != lexical.st_size
        ):
            raise RemoteCIEvidenceError(f"{label} changed while it was opened")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise RemoteCIEvidenceError(f"{label} was truncated while being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RemoteCIEvidenceError(f"{label} grew while it was read")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        final = path.lstat()
    except OSError as error:
        raise RemoteCIEvidenceError(f"{label} disappeared while it was read") from error
    before_identity = (
        lexical.st_dev,
        lexical.st_ino,
        lexical.st_size,
        lexical.st_mtime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    final_identity = (
        final.st_dev,
        final.st_ino,
        final.st_size,
        final.st_mtime_ns,
    )
    if before_identity != after_identity or after_identity != final_identity:
        raise RemoteCIEvidenceError(f"{label} changed while it was read")
    return b"".join(chunks)


def _identity(path: Path, relative: str, maximum: int = MAX_ARCHIVE_MEMBER_BYTES) -> FileIdentity:
    try:
        lexical = path.lstat()
    except OSError as error:
        raise RemoteCIEvidenceError(f"{relative} is unavailable: {error}") from error
    if (
        stat.S_ISLNK(lexical.st_mode)
        or not stat.S_ISREG(lexical.st_mode)
        or lexical.st_size < 0
        or lexical.st_size > maximum
    ):
        raise RemoteCIEvidenceError(f"{relative} has an unsafe file type or size")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RemoteCIEvidenceError(f"{relative} could not be opened safely") from error
    digest = hashlib.sha256()
    size = 0
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (lexical.st_dev, lexical.st_ino, lexical.st_size, lexical.st_mtime_ns)
        ):
            raise RemoteCIEvidenceError(f"{relative} changed while it was opened")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum or size > opened.st_size:
                raise RemoteCIEvidenceError(f"{relative} grew while it was hashed")
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final = path.lstat()
    fingerprints = (
        (lexical.st_dev, lexical.st_ino, lexical.st_size, lexical.st_mtime_ns),
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
        (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns),
    )
    if size != lexical.st_size or len(set(fingerprints)) != 1:
        raise RemoteCIEvidenceError(f"{relative} changed while it was hashed")
    return FileIdentity(relative, size, digest.hexdigest())


def _materialize_regular(source: Path, destination: Path, label: str, maximum: int) -> None:
    record = _identity(source, label, maximum)
    if destination.exists() or destination.is_symlink() or destination.parent.is_symlink():
        raise RemoteCIEvidenceError(f"semantic destination is unsafe: {destination.name}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    input_descriptor = os.open(source, source_flags)
    try:
        output = os.open(destination, flags, 0o600)
    except BaseException:
        os.close(input_descriptor)
        raise
    digest = hashlib.sha256()
    size = 0
    try:
        try:
            while True:
                chunk = os.read(input_descriptor, 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    raise RemoteCIEvidenceError(
                        f"{label} exceeds its materialization budget"
                    )
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(output, view)
                    if written <= 0:
                        raise RemoteCIEvidenceError(f"{label} could not be materialized")
                    view = view[written:]
        finally:
            try:
                os.close(input_descriptor)
            finally:
                os.close(output)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    if (size, digest.hexdigest()) != (record.size, record.sha256):
        destination.unlink(missing_ok=True)
        raise RemoteCIEvidenceError(f"{label} changed while it was materialized")


def _snapshot_tree(root: Path) -> tuple[FileIdentity, ...]:
    try:
        lexical = root.lstat()
    except OSError as error:
        raise RemoteCIEvidenceError(f"artifact root is unavailable: {error}") from error
    if stat.S_ISLNK(lexical.st_mode) or not stat.S_ISDIR(lexical.st_mode):
        raise RemoteCIEvidenceError("artifact root must be a real directory")
    records: list[FileIdentity] = []
    for directory, directories, files in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for name in sorted(directories):
            child = directory_path / name
            mode = child.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise RemoteCIEvidenceError("artifact tree contains a symlink or special directory")
        directories.sort()
        for name in sorted(files):
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            _safe_relative(relative)
            if relative == ATTESTATION_NAME:
                continue
            records.append(_identity(path, relative))
    if not records:
        raise RemoteCIEvidenceError("artifact root contains no evidence files")
    return tuple(sorted(records, key=lambda record: record.path))


def _source_contract(repo_root: Path) -> tuple[FileIdentity, ...]:
    return tuple(_identity(repo_root / relative, relative, MAX_TEXT_BYTES) for relative in SOURCE_CONTRACT_FILES)


def build_attestation(
    identity: CIIdentity,
    artifact_root: Path,
    repo_root: Path,
) -> tuple[dict[str, object], tuple[FileIdentity, ...], tuple[FileIdentity, ...]]:
    if identity.job_name not in JOB_PROFILES:
        raise RemoteCIEvidenceError("CI job is not part of the required evidence set")
    profile = JOB_PROFILES[identity.job_name]
    if profile.runner_tag not in identity.runner_tags:
        raise RemoteCIEvidenceError("CI runner lacks the required calibrated tag")
    before = _snapshot_tree(artifact_root)
    by_path = {record.path: record for record in before}

    def read(relative: str, maximum: int) -> bytes:
        if relative not in by_path:
            raise RemoteCIEvidenceError(f"artifact is not in the captured inventory: {relative}")
        data = _read_regular(artifact_root / relative, relative, maximum)
        record = by_path[relative]
        if len(data) != record.size or _hash_bytes(data) != record.sha256:
            raise RemoteCIEvidenceError(f"artifact changed during semantic validation: {relative}")
        return data

    def materialize(relative: str, destination: Path, maximum: int) -> None:
        if relative not in by_path:
            raise RemoteCIEvidenceError(f"artifact is not in the captured inventory: {relative}")
        _materialize_regular(artifact_root / relative, destination, relative, maximum)

    from remote_ci_job_semantics import (  # Imported lazily to keep the attester core acyclic.
        RemoteCIJobSemanticError,
        validate_remote_job_semantics,
    )
    try:
        validate_remote_job_semantics(
            identity.job_name, set(by_path), read, materialize, repo_root
        )
    except RemoteCIJobSemanticError as error:
        raise RemoteCIEvidenceError(str(error)) from error
    sources = _source_contract(repo_root)
    value: dict[str, object] = {
        "schema_version": 1,
        "kind": ATTESTATION_FORMAT,
        "identity": {
            "project_id": identity.project_id,
            "project_path": identity.project_path,
            "pipeline_id": identity.pipeline_id,
            "pipeline_source": identity.pipeline_source,
            "job_id": identity.job_id,
            "job_name": identity.job_name,
            "commit": identity.commit,
            "ref": identity.ref,
        },
        "runner": {
            "id": identity.runner_id,
            "class": profile.runner_class,
            "required_tag": profile.runner_tag,
            "tags": list(identity.runner_tags),
        },
        "source_contract": {
            "files": [record.as_json() for record in sources],
        },
        "artifact_contract": {
            "root": "ci-artifacts",
            "profile": identity.job_name,
        },
        "artifacts": [record.as_json() for record in before],
        "semantic": {
            "registry": SEMANTIC_REGISTRY,
            "version": SEMANTIC_VERSION,
            "status": "passed",
        },
    }
    return value, before, sources


def marker_for(attestation: dict[str, object]) -> str:
    identity = attestation["identity"]
    if not isinstance(identity, dict):
        raise RemoteCIEvidenceError("attestation identity is invalid")
    digest = _hash_bytes(canonical_json(attestation))
    return (
        f"{MARKER_PREFIX} job={identity['job_name']} commit={identity['commit']} "
        f"sha256={digest}"
    )


DANGEROUS_ENV = {
    "BASH_ENV",
    "ENV",
    "PYTHONPATH",
    "PYTHONHOME",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "CDPATH",
    "MAKEFLAGS",
    "MFLAGS",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
}


def _ci_identity(job: str, environment: Mapping[str, str]) -> CIIdentity:
    if not SAFE_JOB_RE.fullmatch(job) or job not in JOB_PROFILES:
        raise RemoteCIEvidenceError("--job is not a required CI job")
    dangerous = [
        key
        for key, value in environment.items()
        if value and (key in DANGEROUS_ENV or key.startswith("GIT_CONFIG_"))
    ]
    if dangerous:
        raise RemoteCIEvidenceError(f"dangerous CI environment is active: {sorted(dangerous)}")
    if environment.get("CI_JOB_NAME") != job:
        raise RemoteCIEvidenceError("CI_JOB_NAME differs from --job")
    commit = environment.get("CI_COMMIT_SHA", "")
    project_path = environment.get("CI_PROJECT_PATH", "")
    ref = environment.get("CI_COMMIT_REF_NAME", "")
    pipeline_source = environment.get("CI_PIPELINE_SOURCE", "")
    if not SHA_RE.fullmatch(commit):
        raise RemoteCIEvidenceError("CI_COMMIT_SHA is invalid")
    if not SAFE_PROJECT_RE.fullmatch(project_path):
        raise RemoteCIEvidenceError("CI_PROJECT_PATH is invalid")
    if not ref or len(ref) > 255 or any(ord(character) < 32 for character in ref):
        raise RemoteCIEvidenceError("CI_COMMIT_REF_NAME is invalid")
    if not re.fullmatch(r"[a-z_]{2,32}", pipeline_source):
        raise RemoteCIEvidenceError("CI_PIPELINE_SOURCE is invalid")
    raw_tags = environment.get("CI_RUNNER_TAGS", "")
    try:
        tags = strict_json_loads(raw_tags)
    except (UnicodeDecodeError, ValueError) as error:
        raise RemoteCIEvidenceError("CI_RUNNER_TAGS must be a strict JSON array") from error
    if (
        not isinstance(tags, list)
        or not tags
        or len(tags) != len(set(tags))
        or any(not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", tag) for tag in tags)
    ):
        raise RemoteCIEvidenceError("CI_RUNNER_TAGS is invalid")
    return CIIdentity(
        project_id=_positive_int(environment.get("CI_PROJECT_ID"), "CI_PROJECT_ID"),
        project_path=project_path,
        pipeline_id=_positive_int(environment.get("CI_PIPELINE_ID"), "CI_PIPELINE_ID"),
        pipeline_source=pipeline_source,
        job_id=_positive_int(environment.get("CI_JOB_ID"), "CI_JOB_ID"),
        job_name=job,
        commit=commit,
        ref=ref,
        runner_id=_positive_int(environment.get("CI_RUNNER_ID"), "CI_RUNNER_ID"),
        runner_tags=tuple(sorted(tags)),
    )


def _run_git(repo_root: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RemoteCIEvidenceError("git is unavailable in the CI image")
    try:
        result = subprocess.run(
            [str(Path(executable).resolve()), *arguments],
            cwd=repo_root,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RemoteCIEvidenceError("git identity check could not run") from error
    if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
        raise RemoteCIEvidenceError("git identity check failed")
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise RemoteCIEvidenceError("git identity output is not UTF-8") from error


def _verify_ci_checkout(identity: CIIdentity, repo_root: Path) -> None:
    top = Path(_run_git(repo_root, "rev-parse", "--show-toplevel")).resolve()
    if top != repo_root or Path.cwd().resolve() != repo_root:
        raise RemoteCIEvidenceError("attester must run at the CI checkout root")
    if _run_git(repo_root, "rev-parse", "HEAD") != identity.commit:
        raise RemoteCIEvidenceError("CI commit differs from checked-out HEAD")
    if _run_git(repo_root, "status", "--porcelain=v1", "--untracked-files=no"):
        raise RemoteCIEvidenceError("tracked CI checkout changed before attestation")
    for relative in SOURCE_CONTRACT_FILES:
        if _run_git(repo_root, "ls-files", "--error-unmatch", "--", relative) != relative:
            raise RemoteCIEvidenceError(f"source contract file is not tracked: {relative}")


def attest_ci_job(job: str, artifact_root_argument: str) -> Path:
    if artifact_root_argument != "ci-artifacts":
        raise RemoteCIEvidenceError("CI artifact root must be the canonical ci-artifacts path")
    identity = _ci_identity(job, os.environ)
    project_text = os.environ.get("CI_PROJECT_DIR", "")
    if not project_text:
        raise RemoteCIEvidenceError("CI_PROJECT_DIR is missing")
    repo_root = Path(project_text).resolve()
    artifact_root = (repo_root / artifact_root_argument).resolve()
    try:
        artifact_root.relative_to(repo_root)
    except ValueError as error:
        raise RemoteCIEvidenceError("artifact root escapes CI_PROJECT_DIR") from error
    output = artifact_root / ATTESTATION_NAME
    if output.exists() or output.is_symlink():
        raise RemoteCIEvidenceError("remote CI attestation already exists")
    _verify_ci_checkout(identity, repo_root)
    attestation, before, sources = build_attestation(identity, artifact_root, repo_root)
    payload = canonical_json(attestation)
    if len(payload) > MAX_ATTESTATION_BYTES:
        raise RemoteCIEvidenceError("remote CI attestation exceeds its size budget")
    temporary = artifact_root / f".{ATTESTATION_NAME}.partial-{os.getpid()}"
    if temporary.exists() or temporary.is_symlink():
        raise RemoteCIEvidenceError("remote CI attestation staging path already exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    after = _snapshot_tree(artifact_root)
    final_sources = _source_contract(repo_root)
    if after != before or final_sources != sources:
        output.unlink(missing_ok=True)
        raise RemoteCIEvidenceError("CI inputs changed while the attestation was published")
    print(marker_for(attestation), flush=True)
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    attest = commands.add_parser("attest")
    attest.add_argument("--job", required=True)
    attest.add_argument("--artifact-root", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        attest_ci_job(args.job, args.artifact_root)
    except (RemoteCIEvidenceError, OSError) as error:
        print(f"remote_ci_evidence: invalid: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
