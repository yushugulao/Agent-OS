#!/usr/bin/env python3
"""Bind selected worktree sources to immutable blobs from one Git commit."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

try:
    from .evidence_delivery_contract import (
        DeliveryContractError,
        MAX_COMMITTED_FILE_BYTES,
        MAX_COMMITTED_FILES,
        MAX_COMMITTED_TOTAL_BYTES,
        SAFE_GIT_CONFIG_ARGUMENTS,
        controlled_git_environment,
    )
    from .safe_host_paths import (
        absolute_lexical_path,
        atomic_write_bytes,
        read_regular_file,
        require_regular_file,
        require_safe_directory,
    )
except ImportError:
    from evidence_delivery_contract import (
        DeliveryContractError,
        MAX_COMMITTED_FILE_BYTES,
        MAX_COMMITTED_FILES,
        MAX_COMMITTED_TOTAL_BYTES,
        SAFE_GIT_CONFIG_ARGUMENTS,
        controlled_git_environment,
    )
    from safe_host_paths import (
        absolute_lexical_path,
        atomic_write_bytes,
        read_regular_file,
        require_regular_file,
        require_safe_directory,
    )


OBJECT_ID = re.compile(rb"[0-9a-f]{40}|[0-9a-f]{64}")


def _git_output(git: Path, repo: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            [str(git), *SAFE_GIT_CONFIG_ARGUMENTS, *arguments],
            cwd=repo,
            env=controlled_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise DeliveryContractError("Git source sampling could not run") from error
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise DeliveryContractError(
            f"Git source sampling failed: {detail or result.returncode}"
        )
    return result.stdout


def _decode_path(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise DeliveryContractError("source sample path is not UTF-8") from error
    path = Path(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DeliveryContractError(f"source sample path is unsafe: {value!r}")
    return value


def _blob_oid(raw: bytes, oid_length: int) -> str:
    framed = f"blob {len(raw)}\0".encode("ascii") + raw
    if oid_length == 40:
        return hashlib.sha1(framed).hexdigest()
    if oid_length == 64:
        return hashlib.sha256(framed).hexdigest()
    raise DeliveryContractError("Git repository uses an unsupported object format")


def committed_source_path_sample(
    git: str,
    repo: Path,
    commit: str,
    source_paths: tuple[str, ...],
    *,
    snapshot_root: Path | None = None,
) -> tuple[tuple[str, int, str, str], ...]:
    """Sample sources against exact commit blobs and optionally materialize them."""

    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is None:
        raise DeliveryContractError("source sample commit is invalid")
    if any(not isinstance(path, str) for path in source_paths):
        raise DeliveryContractError("source sample path inventory is invalid")
    paths = tuple(_decode_path(path.encode("utf-8")) for path in source_paths)
    if not paths or len(paths) > MAX_COMMITTED_FILES or len(paths) != len(set(paths)):
        raise DeliveryContractError("source sample path inventory is invalid")
    try:
        repo = require_safe_directory(absolute_lexical_path(repo)).resolve(strict=True)
        resolved_git = shutil.which(git)
        if resolved_git is None:
            candidate = Path(git)
            resolved_git = str(candidate if candidate.is_absolute() else repo / candidate)
        git_path = require_regular_file(Path(resolved_git)).resolve(strict=True)
    except (OSError, ValueError) as error:
        raise DeliveryContractError("source sample repository or Git is unsafe") from error
    try:
        snapshot = (
            require_safe_directory(absolute_lexical_path(snapshot_root)).resolve(strict=True)
            if snapshot_root is not None else None
        )
    except (OSError, ValueError) as error:
        raise DeliveryContractError("source snapshot root is unsafe") from error
    try:
        resolved = _git_output(
            git_path, repo, "rev-parse", "--verify", f"{commit}^{{commit}}"
        ).decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise DeliveryContractError("source sample commit output is invalid") from error
    if resolved != commit:
        raise DeliveryContractError("source sample commit does not resolve exactly")

    pathspecs = tuple(f":(literal){path}" for path in paths)
    flags_raw = _git_output(
        git_path, repo, "ls-files", "-v", "-z", "--", *pathspecs
    )
    indexed: set[str] = set()
    for record in flags_raw.split(b"\0"):
        if not record:
            continue
        if len(record) < 3 or record[1:2] != b" ":
            raise DeliveryContractError("source sample index flags are malformed")
        path = _decode_path(record[2:])
        if path in indexed:
            raise DeliveryContractError("source sample index contains a duplicate path")
        if record[:1] != b"H":
            raise DeliveryContractError(
                f"Git index has a hidden or nonstandard tracked flag: {path}"
            )
        indexed.add(path)
    if indexed != set(paths):
        raise DeliveryContractError("source sample paths are not fully tracked")

    total_bytes = 0
    samples: list[tuple[str, int, str, str]] = []
    for path, pathspec in zip(paths, pathspecs, strict=True):
        listing = _git_output(
            git_path, repo, "ls-tree", "-z", commit, "--", pathspec
        )
        records = [record for record in listing.split(b"\0") if record]
        if len(records) != 1:
            raise DeliveryContractError(f"committed source path is ambiguous: {path}")
        header, separator, raw_path = records[0].partition(b"\t")
        fields = header.split(b" ")
        if not separator or len(fields) != 3:
            raise DeliveryContractError(f"committed source entry is malformed: {path}")
        mode, kind, raw_oid = fields
        if (
            _decode_path(raw_path) != path
            or mode not in {b"100644", b"100755"}
            or kind != b"blob"
            or OBJECT_ID.fullmatch(raw_oid) is None
        ):
            raise DeliveryContractError(f"committed source entry is unsafe: {path}")
        oid = raw_oid.decode("ascii")
        size_text = _git_output(git_path, repo, "cat-file", "-s", oid).strip()
        if not size_text.isdigit():
            raise DeliveryContractError(f"committed source size is invalid: {path}")
        size = int(size_text)
        total_bytes += size
        if (
            size <= 0
            or size > MAX_COMMITTED_FILE_BYTES
            or total_bytes > MAX_COMMITTED_TOTAL_BYTES
        ):
            raise DeliveryContractError(f"committed source exceeds its byte budget: {path}")
        committed = _git_output(git_path, repo, "cat-file", "blob", oid)
        if len(committed) != size:
            raise DeliveryContractError(f"committed source size differs: {path}")
        try:
            worktree = read_regular_file(
                repo / Path(path), nonempty=True,
                maximum_bytes=MAX_COMMITTED_FILE_BYTES,
            )
        except (OSError, ValueError) as error:
            raise DeliveryContractError(
                f"worktree source cannot be sampled safely: {path}"
            ) from error
        if worktree != committed or _blob_oid(worktree, len(oid)) != oid:
            raise DeliveryContractError(
                f"worktree source differs from commit blob: {path}"
            )
        if snapshot is not None:
            try:
                atomic_write_bytes(snapshot / Path(path), committed, replace=False)
            except (OSError, ValueError) as error:
                raise DeliveryContractError(
                    f"committed source snapshot cannot be materialized: {path}"
                ) from error
        samples.append((path, size, hashlib.sha256(committed).hexdigest(), oid))
    return tuple(samples)
