#!/usr/bin/env python3
"""Safely verify downloaded GitLab traces and artifact ZIP archives."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import sys
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

from remote_ci_evidence import (
    ATTESTATION_ARCHIVE_PATH,
    ATTESTATION_FORMAT,
    JOB_PROFILES,
    MARKER_PREFIX,
    MAX_ARCHIVE_MEMBER_BYTES,
    MAX_ATTESTATION_BYTES,
    SEMANTIC_REGISTRY,
    SEMANTIC_VERSION,
    SHA256_RE,
    SHA_RE,
    SAFE_PROJECT_RE,
    FileIdentity,
    RemoteCIEvidenceError,
    _exact_keys,
    _hash_bytes,
    _read_regular,
    _run_git,
    _safe_relative,
    _source_contract,
    canonical_json,
)
from strict_json import strict_json_loads


TRACE_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z[ ]+"
)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MARKER_RE = re.compile(
    rf"{MARKER_PREFIX} job=([a-z0-9][a-z0-9-]{{0,63}}) "
    r"commit=([0-9a-f]{40}(?:[0-9a-f]{24})?) sha256=([0-9a-f]{64})\Z"
)
MAX_TRACE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 4096
MAX_ARCHIVE_EXPANDED_BYTES = 1280 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200.0
COMPRESSION_RATIO_FLOOR = 1024 * 1024


@dataclass(frozen=True)
class RemoteJobExpectation:
    project_id: int
    project_path: str
    pipeline_id: int
    pipeline_source: str
    job_id: int
    job_name: str
    commit: str
    ref: str
    runner_id: int
    runner_tag: str


def _normalise_trace_line(raw: str) -> str:
    line = ANSI_ESCAPE_RE.sub("", raw).rstrip("\r")
    return TRACE_TIMESTAMP_RE.sub("", line, count=1)


def parse_trace_marker(trace: bytes) -> tuple[str, str, str]:
    if not trace or len(trace) > MAX_TRACE_BYTES or b"\0" in trace:
        raise RemoteCIEvidenceError("GitLab trace is empty or exceeds its size budget")
    try:
        lines = [_normalise_trace_line(line) for line in trace.decode("utf-8").splitlines()]
    except UnicodeDecodeError as error:
        raise RemoteCIEvidenceError("GitLab trace is not UTF-8") from error
    matches = [MARKER_RE.fullmatch(line) for line in lines]
    values = [match.groups() for match in matches if match is not None]
    if len(values) != 1:
        raise RemoteCIEvidenceError("GitLab trace lacks one unique complete attestation marker")
    return values[0]


@contextmanager
def _open_zip(source: Path | bytes) -> Iterator[zipfile.ZipFile]:
    if isinstance(source, Path):
        raw = _read_regular(source, "GitLab artifact ZIP", MAX_ARCHIVE_BYTES)
        if not raw:
            raise RemoteCIEvidenceError("GitLab artifact ZIP is empty")
        data_source: io.BytesIO = io.BytesIO(raw)
    else:
        if not source or len(source) > MAX_ARCHIVE_BYTES:
            raise RemoteCIEvidenceError("GitLab artifact ZIP is empty or too large")
        data_source = io.BytesIO(source)
    try:
        with zipfile.ZipFile(data_source, "r") as archive:
            yield archive
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError) as error:
        raise RemoteCIEvidenceError("GitLab artifact is not a valid ZIP archive") from error


def _zip_index(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if not infos or len(infos) > MAX_ARCHIVE_ENTRIES:
        raise RemoteCIEvidenceError("GitLab artifact ZIP has an invalid entry count")
    result: dict[str, zipfile.ZipInfo] = {}
    seen: set[str] = set()
    file_paths: set[str] = set()
    expanded = 0
    for info in infos:
        name = info.filename
        directory = name.endswith("/")
        canonical = name[:-1] if directory else name
        _safe_relative(canonical, "ZIP member path")
        if canonical != "ci-artifacts" and not canonical.startswith("ci-artifacts/"):
            raise RemoteCIEvidenceError("GitLab artifact ZIP contains a path outside ci-artifacts")
        if canonical in seen:
            raise RemoteCIEvidenceError("GitLab artifact ZIP contains a duplicate path")
        seen.add(canonical)
        if info.flag_bits & 0x1:
            raise RemoteCIEvidenceError("GitLab artifact ZIP contains an encrypted member")
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        parts = PurePosixPath(canonical).parts
        if any("/".join(parts[:index]) in file_paths for index in range(1, len(parts))):
            raise RemoteCIEvidenceError("GitLab artifact ZIP nests content below a file")
        if directory:
            if file_type not in {0, stat.S_IFDIR}:
                raise RemoteCIEvidenceError("GitLab artifact ZIP directory has a special type")
            continue
        if canonical == "ci-artifacts":
            raise RemoteCIEvidenceError("GitLab artifact ZIP replaces its root with a file")
        if any(path.startswith(canonical + "/") for path in seen - {canonical}):
            raise RemoteCIEvidenceError("GitLab artifact ZIP replaces a directory with a file")
        if file_type not in {0, stat.S_IFREG}:
            raise RemoteCIEvidenceError("GitLab artifact ZIP contains a symlink or special file")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise RemoteCIEvidenceError("GitLab artifact ZIP uses unsupported compression")
        if info.file_size < 0 or info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise RemoteCIEvidenceError("GitLab artifact ZIP member exceeds its size budget")
        if (
            info.file_size >= COMPRESSION_RATIO_FLOOR
            and info.file_size / max(1, info.compress_size) > MAX_COMPRESSION_RATIO
        ):
            raise RemoteCIEvidenceError("GitLab artifact ZIP member exceeds its compression budget")
        expanded += info.file_size
        if expanded > MAX_ARCHIVE_EXPANDED_BYTES:
            raise RemoteCIEvidenceError("GitLab artifact ZIP exceeds its expanded size budget")
        result[canonical] = info
        file_paths.add(canonical)
    return result


def _zip_read(archive: zipfile.ZipFile, info: zipfile.ZipInfo, maximum: int) -> bytes:
    if info.file_size > maximum:
        raise RemoteCIEvidenceError(f"ZIP member exceeds semantic read budget: {info.filename}")
    try:
        with archive.open(info, "r") as handle:
            data = handle.read(maximum + 1)
            if len(data) > maximum or len(data) != info.file_size or handle.read(1):
                raise RemoteCIEvidenceError(f"ZIP member changed size while read: {info.filename}")
            return data
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError) as error:
        raise RemoteCIEvidenceError(f"ZIP member failed integrity validation: {info.filename}") from error


def _zip_identity(archive: zipfile.ZipFile, info: zipfile.ZipInfo, relative: str) -> FileIdentity:
    digest = hashlib.sha256()
    size = 0
    try:
        with archive.open(info, "r") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > info.file_size:
                    raise RemoteCIEvidenceError(f"ZIP member grew while read: {info.filename}")
                digest.update(chunk)
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError) as error:
        raise RemoteCIEvidenceError(f"ZIP member failed integrity validation: {info.filename}") from error
    if size != info.file_size:
        raise RemoteCIEvidenceError(f"ZIP member was truncated while read: {info.filename}")
    return FileIdentity(relative, size, digest.hexdigest())


def _zip_materialize(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
    maximum: int,
) -> None:
    if info.file_size > maximum or destination.exists() or destination.is_symlink():
        raise RemoteCIEvidenceError(f"ZIP semantic destination is invalid: {destination.name}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    output = os.open(destination, flags, 0o600)
    size = 0
    try:
        try:
            with archive.open(info, "r") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > maximum or size > info.file_size:
                        raise RemoteCIEvidenceError(
                            f"ZIP member grew while materialized: {info.filename}"
                        )
                    view = memoryview(chunk)
                    while view:
                        written = os.write(output, view)
                        if written <= 0:
                            raise RemoteCIEvidenceError(
                                "ZIP semantic artifact could not be written"
                            )
                        view = view[written:]
        finally:
            os.close(output)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    if size != info.file_size:
        destination.unlink(missing_ok=True)
        raise RemoteCIEvidenceError(f"ZIP member was truncated: {info.filename}")


def _parse_identity_records(value: object, label: str) -> tuple[FileIdentity, ...]:
    if not isinstance(value, list) or not value:
        raise RemoteCIEvidenceError(f"{label} must be a non-empty array")
    result: list[FileIdentity] = []
    previous = ""
    for raw in value:
        raw = _exact_keys(raw, {"path", "bytes", "sha256"}, f"{label} entry")
        path = _safe_relative(raw["path"], f"{label} path")
        size, digest = raw["bytes"], raw["sha256"]
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or size > MAX_ARCHIVE_MEMBER_BYTES
            or not isinstance(digest, str)
            or not SHA256_RE.fullmatch(digest)
            or path <= previous
        ):
            raise RemoteCIEvidenceError(f"{label} entry is invalid or unsorted")
        result.append(FileIdentity(path, size, digest))
        previous = path
    return tuple(result)


def validate_attestation(
    value: object, expectation: RemoteJobExpectation, repo_root: Path
) -> tuple[FileIdentity, ...]:
    value = _exact_keys(
        value,
        {"schema_version", "kind", "identity", "runner", "source_contract",
         "artifact_contract", "artifacts", "semantic"},
        "remote CI attestation",
    )
    if value["schema_version"] != 1 or value["kind"] != ATTESTATION_FORMAT:
        raise RemoteCIEvidenceError("remote CI attestation format is unsupported")
    identity = _exact_keys(
        value["identity"],
        {"project_id", "project_path", "pipeline_id", "pipeline_source", "job_id",
         "job_name", "commit", "ref"},
        "remote CI identity",
    )
    expected_identity = {
        "project_id": expectation.project_id,
        "project_path": expectation.project_path,
        "pipeline_id": expectation.pipeline_id,
        "pipeline_source": expectation.pipeline_source,
        "job_id": expectation.job_id,
        "job_name": expectation.job_name,
        "commit": expectation.commit,
        "ref": expectation.ref,
    }
    if identity != expected_identity:
        raise RemoteCIEvidenceError("remote CI attestation differs from GitLab API identity")
    profile = JOB_PROFILES.get(expectation.job_name)
    if profile is None or expectation.runner_tag != profile.runner_tag:
        raise RemoteCIEvidenceError("remote CI expected runner policy is invalid")
    runner = _exact_keys(value["runner"], {"id", "class", "required_tag", "tags"}, "runner")
    tags = runner["tags"]
    if (
        runner["id"] != expectation.runner_id
        or runner["class"] != profile.runner_class
        or runner["required_tag"] != expectation.runner_tag
        or not isinstance(tags, list)
        or any(not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", tag)
               for tag in tags)
        or tags != sorted(tags)
        or len(tags) != len(set(tags))
        or expectation.runner_tag not in tags
    ):
        raise RemoteCIEvidenceError("remote CI runner attestation is invalid")
    artifact_contract = _exact_keys(value["artifact_contract"], {"root", "profile"}, "artifacts")
    if artifact_contract != {"root": "ci-artifacts", "profile": expectation.job_name}:
        raise RemoteCIEvidenceError("remote CI artifact contract is invalid")
    semantic = _exact_keys(value["semantic"], {"registry", "version", "status"}, "semantic")
    if semantic != {"registry": SEMANTIC_REGISTRY, "version": SEMANTIC_VERSION, "status": "passed"}:
        raise RemoteCIEvidenceError("remote CI semantic attestation is invalid")
    source_contract = _exact_keys(value["source_contract"], {"files"}, "source contract")
    if _parse_identity_records(source_contract["files"], "source contract") != _source_contract(repo_root):
        raise RemoteCIEvidenceError("remote CI source contract differs from verifier source")
    return _parse_identity_records(value["artifacts"], "artifact inventory")


def _validate_expectation(expectation: RemoteJobExpectation) -> None:
    if (
        not isinstance(expectation.job_name, str)
        or expectation.job_name not in JOB_PROFILES
        or not isinstance(expectation.commit, str)
        or not SHA_RE.fullmatch(expectation.commit)
        or not isinstance(expectation.project_path, str)
        or not SAFE_PROJECT_RE.fullmatch(expectation.project_path)
        or not isinstance(expectation.ref, str)
        or not expectation.ref
        or len(expectation.ref) > 255
        or any(ord(character) < 32 for character in expectation.ref)
        or not isinstance(expectation.pipeline_source, str)
        or not re.fullmatch(r"[a-z_]{2,32}", expectation.pipeline_source)
        or expectation.runner_tag != JOB_PROFILES[expectation.job_name].runner_tag
    ):
        raise RemoteCIEvidenceError("remote CI verification expectation is invalid")
    for name in ("project_id", "pipeline_id", "job_id", "runner_id"):
        value = getattr(expectation, name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RemoteCIEvidenceError("remote CI verification expectation has an invalid ID")


def verify_downloaded_job_evidence(
    trace_source: Path | bytes,
    archive_source: Path | bytes,
    expectation: RemoteJobExpectation,
    repo_root: Path,
) -> dict[str, object]:
    _validate_expectation(expectation)
    trace = _read_regular(trace_source, "GitLab trace", MAX_TRACE_BYTES) \
        if isinstance(trace_source, Path) else trace_source
    marker_job, marker_commit, marker_digest = parse_trace_marker(trace)
    if marker_job != expectation.job_name or marker_commit != expectation.commit:
        raise RemoteCIEvidenceError("GitLab trace marker differs from the API job identity")
    repo_input = Path(repo_root)
    if repo_input.is_symlink() or not repo_input.is_dir():
        raise RemoteCIEvidenceError("remote CI verifier repository root is unsafe")
    repo_root = repo_input.resolve()
    if (
        Path(_run_git(repo_root, "rev-parse", "--show-toplevel")).resolve() != repo_root
        or _run_git(repo_root, "rev-parse", "HEAD") != expectation.commit
    ):
        raise RemoteCIEvidenceError(
            "remote CI evidence commit differs from the verifier checkout"
        )
    with _open_zip(archive_source) as archive:
        index = _zip_index(archive)
        attestation_info = index.get(ATTESTATION_ARCHIVE_PATH)
        if attestation_info is None:
            raise RemoteCIEvidenceError("GitLab artifact ZIP lacks the job attestation")
        raw_attestation = _zip_read(archive, attestation_info, MAX_ATTESTATION_BYTES)
        if _hash_bytes(raw_attestation) != marker_digest:
            raise RemoteCIEvidenceError("GitLab trace marker does not bind the artifact attestation")
        try:
            attestation = strict_json_loads(raw_attestation)
        except (UnicodeDecodeError, ValueError) as error:
            raise RemoteCIEvidenceError("remote CI attestation is not strict JSON") from error
        if canonical_json(attestation) != raw_attestation:
            raise RemoteCIEvidenceError("remote CI attestation JSON is not canonical")
        expected_records = validate_attestation(attestation, expectation, repo_root)
        artifact_infos = {
            path.removeprefix("ci-artifacts/"): info
            for path, info in index.items()
            if path != ATTESTATION_ARCHIVE_PATH
        }
        if set(artifact_infos) != {record.path for record in expected_records}:
            raise RemoteCIEvidenceError("GitLab artifact ZIP inventory differs from its attestation")
        actual_records = tuple(
            _zip_identity(archive, artifact_infos[record.path], record.path)
            for record in expected_records
        )
        if actual_records != expected_records:
            raise RemoteCIEvidenceError("GitLab artifact content differs from its attestation")

        def read(relative: str, maximum: int) -> bytes:
            info = artifact_infos.get(relative)
            if info is None:
                raise RemoteCIEvidenceError(f"attested artifact is missing: {relative}")
            return _zip_read(archive, info, maximum)

        def materialize(relative: str, destination: Path, maximum: int) -> None:
            info = artifact_infos.get(relative)
            if info is None:
                raise RemoteCIEvidenceError(f"attested artifact is missing: {relative}")
            _zip_materialize(archive, info, destination, maximum)

        from remote_ci_job_semantics import RemoteCIJobSemanticError, validate_remote_job_semantics
        try:
            validate_remote_job_semantics(
                expectation.job_name, set(artifact_infos), read, materialize, repo_root
            )
        except RemoteCIJobSemanticError as error:
            raise RemoteCIEvidenceError(str(error)) from error
    return {
        "status": "execution-attested",
        "job": expectation.job_name,
        "commit": expectation.commit,
        "job_id": expectation.job_id,
        "attestation_sha256": marker_digest,
        "artifact_count": len(expected_records),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--project-path", required=True)
    parser.add_argument("--pipeline-id", type=int, required=True)
    parser.add_argument("--pipeline-source", required=True)
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--runner-id", type=int, required=True)
    parser.add_argument("--runner-tag", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    expectation = RemoteJobExpectation(
        args.project_id, args.project_path, args.pipeline_id, args.pipeline_source,
        args.job_id, args.job, args.commit, args.ref, args.runner_id, args.runner_tag,
    )
    try:
        result = verify_downloaded_job_evidence(
            args.trace, args.archive, expectation, args.repo_root
        )
    except (RemoteCIEvidenceError, OSError) as error:
        print(f"remote_ci_archive: invalid: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RemoteJobExpectation",
    "parse_trace_marker",
    "validate_attestation",
    "verify_downloaded_job_evidence",
]
