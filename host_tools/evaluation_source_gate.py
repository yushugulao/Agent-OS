#!/usr/bin/env python3
"""Closed source-tree inventory and generated-output policy for formal runs."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

try:
    from .safe_host_paths import (
        DEFAULT_MAX_WALK_BYTES,
        DEFAULT_MAX_WALK_DIRECTORIES,
        DEFAULT_MAX_WALK_FILES,
        path_is_link,
        reject_link_components,
        require_safe_directory,
        walk_directory_tree_no_links,
    )
except ImportError:
    from safe_host_paths import (
        DEFAULT_MAX_WALK_BYTES,
        DEFAULT_MAX_WALK_DIRECTORIES,
        DEFAULT_MAX_WALK_FILES,
        path_is_link,
        reject_link_components,
        require_safe_directory,
        walk_directory_tree_no_links,
    )


class ToolAttestationError(ValueError):
    """Raised when source closure or a bound tool cannot be trusted."""


EVALUATION_BUILD_OUTPUT_ROOTS = (
    "asm", "build", "target", "user/asm", "user/build", "user/target",
    "baseline_ucore/asm", "baseline_ucore/build", "baseline_ucore/target",
    "baseline_ucore/user/asm", "baseline_ucore/user/build",
    "baseline_ucore/user/target",
)
EVALUATION_CACHE_OUTPUT_ROOTS = (
    "__pycache__", "host_tools/__pycache__", "scripts/__pycache__",
)
EVALUATION_ARTIFACT_OUTPUT_ROOTS = (
    "results/evaluation", "evidence/releases",
)
EVALUATION_ARTIFACT_OUTPUT_FILES = (
    "results/evaluation/last-attempt.txt",
    "results/evaluation/latest-run.txt",
)
EVALUATION_BUILD_OUTPUT_FILES = (
    "os/initproc.S", "nfs/fs", "nfs/fs.exe", "nfs/fs.img", "nfs/fs-copy.img",
    "baseline_ucore/os/initproc.S", "baseline_ucore/nfs/fs",
    "baseline_ucore/nfs/fs.exe", "baseline_ucore/nfs/fs.img",
    "baseline_ucore/nfs/fs-copy.img",
)

# Local repository configuration is untrusted input.  In particular,
# core.fsmonitor can execute a hook while a supposedly read-only inventory is
# being assembled.  Keep these overrides on every Git invocation made by the
# source gate rather than relying on the caller's environment.
SAFE_GIT_CONFIG_ARGUMENTS = (
    "-c", "core.fsmonitor=false",
    "-c", "core.untrackedCache=false",
)


@dataclass(frozen=True)
class SourceTreeReceipt:
    commit: str
    tracked_files: int
    generated_paths: tuple[str, ...]
    executable_mode_verified: bool


def _run_git(
    git: Path,
    directory: Path,
    environment: dict[str, str],
    *arguments: str,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(git), *SAFE_GIT_CONFIG_ARGUMENTS, *arguments],
        cwd=directory, env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )


def _git_blob_hash(path: Path, algorithm: str) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ToolAttestationError(f"tracked path is not a regular file: {path}")
    digest = hashlib.new(algorithm)
    digest.update(f"blob {info.st_size}\0".encode("ascii"))
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        opened = os.fstat(handle.fileno())
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_size != info.st_size
        or opened.st_mtime_ns != info.st_mtime_ns
        or (info.st_ino and opened.st_ino != info.st_ino)
    ):
        raise ToolAttestationError(f"tracked path changed while hashing: {path}")
    return digest.hexdigest()


def verify_tracked_worktree_bytes(
    git: Path,
    repository: Path,
    worktree: Path,
    commit: str,
    environment: dict[str, str],
    *,
    verify_executable_mode: bool | None = None,
) -> int:
    """Compare regular checkout files and executable modes with a commit."""

    if verify_executable_mode is None:
        verify_executable_mode = _executable_mode_is_reliable(
            git, worktree, environment
        )
    listing = _run_git(git, repository, environment, "ls-tree", "-rz", "--full-tree", commit)
    if listing.returncode:
        raise ToolAttestationError("cannot enumerate committed source blobs")
    object_format = _run_git(git, repository, environment, "rev-parse", "--show-object-format")
    algorithm = object_format.stdout.strip().decode("ascii", "strict")
    if object_format.returncode or algorithm not in {"sha1", "sha256"}:
        raise ToolAttestationError("repository object format is unsupported")
    count = 0
    for entry in listing.stdout.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_name = entry.split(b"\t", 1)
            mode, kind, expected = metadata.split(b" ", 2)
        except ValueError as error:
            raise ToolAttestationError("committed source inventory is malformed") from error
        components = raw_name.split(b"/")
        if (
            kind != b"blob"
            or mode not in {b"100644", b"100755"}
            or any(part in {b"", b".", b".."} or part.lower() == b".git" for part in components)
        ):
            raise ToolAttestationError("committed source contains a link or other unsafe entry")
        path = worktree.joinpath(*(os.fsdecode(part) for part in components))
        try:
            actual_mode = path.lstat().st_mode
        except OSError as error:
            raise ToolAttestationError(
                f"tracked worktree path is unavailable: {os.fsdecode(raw_name)}"
            ) from error
        if not stat.S_ISREG(actual_mode):
            raise ToolAttestationError(
                f"tracked worktree type differs from HEAD: {os.fsdecode(raw_name)}"
            )
        if verify_executable_mode:
            expected_x = mode == b"100755"
            actual_x = bool(actual_mode & stat.S_IXUSR)
            if expected_x != actual_x:
                raise ToolAttestationError(
                    f"tracked worktree executable mode differs from HEAD: {os.fsdecode(raw_name)}"
                )
        if _git_blob_hash(path, algorithm).encode("ascii") != expected:
            raise ToolAttestationError(
                f"tracked worktree bytes differ from HEAD blob: {os.fsdecode(raw_name)}"
            )
        count += 1
    if count == 0:
        raise ToolAttestationError("committed source inventory is empty")
    return count


def require_clean_head(
    git: Path, repository: Path, environment: dict[str, str]
) -> str:
    """Return HEAD only after the complete checkout matches that commit."""

    head = _run_git(git, repository, environment, "rev-parse", "--verify", "HEAD^{commit}")
    raw_commit = head.stdout.strip()
    if head.returncode or len(raw_commit) != 40 or any(c not in b"0123456789abcdef" for c in raw_commit):
        raise ToolAttestationError("HEAD is not a full commit")
    commit = raw_commit.decode("ascii")
    staged = _run_git(git, repository, environment, "diff-index", "--cached", "--quiet", commit, "--")
    untracked = _run_git(git, repository, environment, "ls-files", "--others", "--exclude-standard", "-z")
    if staged.returncode or untracked.returncode or untracked.stdout:
        raise ToolAttestationError("repository worktree is dirty")
    verify_tracked_worktree_bytes(git, repository, repository, commit, environment)
    return commit


def _executable_mode_is_reliable(
    git: Path, worktree: Path, environment: dict[str, str]
) -> bool:
    result = _run_git(git, worktree, environment, "config", "--bool", "core.filemode")
    value = result.stdout.rstrip(b"\r\n")
    if result.returncode or value not in {b"true", b"false"}:
        raise ToolAttestationError("cannot determine Git executable-mode fidelity")
    return value == b"true" and os.name == "posix" and sys.platform != "cygwin"


def _canonical_relative_path(value: str, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ToolAttestationError(f"{label} is not a canonical relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} or part.casefold() == ".git" for part in path.parts)
    ):
        raise ToolAttestationError(f"{label} is not a canonical relative path")
    return path


def _under(path: PurePosixPath, roots: Iterable[PurePosixPath]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _folded_under(path: PurePosixPath, root: PurePosixPath) -> bool:
    path_parts = tuple(part.casefold() for part in path.parts)
    root_parts = tuple(part.casefold() for part in root.parts)
    return path_parts[:len(root_parts)] == root_parts


def _canonical_output_policy(
    roots: Iterable[str], files: Iterable[str]
) -> tuple[tuple[PurePosixPath, ...], tuple[PurePosixPath, ...]]:
    root_paths = tuple(_canonical_relative_path(value, "generated output root") for value in roots)
    file_paths = tuple(_canonical_relative_path(value, "generated output file") for value in files)
    folded = [path.as_posix().casefold() for path in (*root_paths, *file_paths)]
    if len(folded) != len(set(folded)):
        raise ToolAttestationError("generated output policy contains duplicate paths")
    for file_path in file_paths:
        if _under(file_path, root_paths):
            raise ToolAttestationError("generated output file is already covered by an output root")
    generated_namespaces = tuple(
        PurePosixPath(value)
        for value in (
            *EVALUATION_BUILD_OUTPUT_ROOTS,
            *EVALUATION_CACHE_OUTPUT_ROOTS,
        )
    )
    artifact_namespaces = tuple(
        PurePosixPath(value) for value in EVALUATION_ARTIFACT_OUTPUT_ROOTS
    )
    fixed_files = {
        PurePosixPath(value)
        for value in (*EVALUATION_BUILD_OUTPUT_FILES, *EVALUATION_ARTIFACT_OUTPUT_FILES)
    }
    for root in root_paths:
        if _under(root, generated_namespaces):
            continue
        if any(namespace in root.parents for namespace in artifact_namespaces):
            continue
        if root in artifact_namespaces:
            raise ToolAttestationError(
                f"generated artifact root must be an exact namespace descendant: "
                f"{root.as_posix()}"
            )
        else:
            raise ToolAttestationError(
                f"generated output root is outside canonical namespaces: {root.as_posix()}"
            )
    for file_path in file_paths:
        if file_path not in fixed_files:
            raise ToolAttestationError(
                f"generated output file is outside canonical namespaces: {file_path.as_posix()}"
            )
    return root_paths, file_paths


def _decode_git_names(payload: bytes, label: str) -> tuple[PurePosixPath, ...]:
    names: list[PurePosixPath] = []
    folded: set[str] = set()
    for raw in payload.split(b"\0"):
        if not raw:
            continue
        path = _canonical_relative_path(os.fsdecode(raw), label)
        key = path.as_posix().casefold()
        if key in folded:
            raise ToolAttestationError(f"{label} contains a path collision")
        folded.add(key)
        names.append(path)
    return tuple(names)


def _tracked_path_inventory(
    git: Path, repository: Path, commit: str, environment: dict[str, str]
) -> tuple[PurePosixPath, ...]:
    result = _run_git(git, repository, environment, "ls-tree", "-rz", "--name-only", commit)
    if result.returncode:
        raise ToolAttestationError("cannot enumerate committed source paths")
    paths = _decode_git_names(result.stdout, "committed source inventory")
    if not paths:
        raise ToolAttestationError("committed source inventory is empty")
    return paths


def _verify_tracked_parent_directories(
    worktree: Path, tracked_paths: Iterable[PurePosixPath]
) -> None:
    parents: set[PurePosixPath] = set()
    for relative in tracked_paths:
        parents.update(parent for parent in relative.parents if parent.parts)
    for relative in sorted(parents, key=lambda item: (len(item.parts), item.as_posix())):
        directory = worktree.joinpath(*relative.parts)
        try:
            info = directory.lstat()
        except OSError as error:
            raise ToolAttestationError(
                f"tracked source parent is unavailable: {relative.as_posix()}"
            ) from error
        if path_is_link(directory, info.st_mode, file_info=info) or not stat.S_ISDIR(info.st_mode):
            raise ToolAttestationError(
                f"tracked source parent is link-backed: {relative.as_posix()}"
            )


def _filesystem_worktree_paths(
    repository: Path,
    worktree: Path,
    *,
    generated_roots: Iterable[PurePosixPath] = (),
    generated_files: Iterable[PurePosixPath] = (),
) -> tuple[tuple[PurePosixPath, bool], ...]:
    """Inventory every non-administrative path without consulting Git ignores."""

    generated_roots = tuple(generated_roots)
    generated_files = frozenset(generated_files)
    paths: list[tuple[PurePosixPath, bool]] = []
    seen: dict[str, PurePosixPath] = {}
    counters = {
        "source": [0, 0, 0],
        "generated": [0, 0, 0],
    }

    def add(path: Path, is_directory: bool) -> None:
        relative = _canonical_relative_path(
            path.relative_to(worktree).as_posix(), "filesystem worktree inventory"
        )
        folded = relative.as_posix().casefold()
        if folded in seen:
            raise ToolAttestationError("filesystem worktree inventory contains a path collision")
        seen[folded] = relative
        paths.append((relative, is_directory))
        namespace = (
            "generated"
            if _under(relative, generated_roots) or relative in generated_files
            else "source"
        )
        file_count, directory_count, total_bytes = counters[namespace]
        if is_directory:
            directory_count += 1
        else:
            file_count += 1
            total_bytes += path.lstat().st_size
        counters[namespace] = [file_count, directory_count, total_bytes]
        if (
            file_count > DEFAULT_MAX_WALK_FILES
            or directory_count > DEFAULT_MAX_WALK_DIRECTORIES
            or total_bytes > DEFAULT_MAX_WALK_BYTES
        ):
            raise ToolAttestationError(
                f"filesystem worktree {namespace} inventory exceeds its budget"
            )

    try:
        with os.scandir(worktree) as iterator:
            entries = sorted(iterator, key=lambda item: item.name)
        admin = [entry for entry in entries if entry.name.casefold() == ".git"]
        if len(admin) != 1:
            raise ToolAttestationError("worktree Git administration entry is ambiguous")
        admin_info = admin[0].stat(follow_symlinks=False)
        admin_path = worktree / admin[0].name
        if admin[0].name != ".git" or path_is_link(
            admin_path, admin_info.st_mode, file_info=admin_info
        ):
            raise ToolAttestationError("worktree Git administration entry is unsafe")
        if stat.S_ISREG(admin_info.st_mode):
            if admin_info.st_size > 4096:
                raise ToolAttestationError("worktree Git administration file is oversized")
            raw = admin_path.read_bytes()
            if not raw.startswith(b"gitdir: ") or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
                raise ToolAttestationError("worktree Git administration file is malformed")
            try:
                target_name = os.fsdecode(raw[8:-1])
                target_path = PurePosixPath(target_name)
                if (
                    "\\" in target_name
                    or any(part in {"", ".", ".."} for part in target_path.parts)
                    or not (
                        target_path.is_absolute()
                        or ((os.name == "nt" or sys.platform == "cygwin")
                            and len(target_name) >= 3
                            and target_name[0].isalpha() and target_name[1:3] == ":/")
                    )
                ):
                    raise ValueError("noncanonical gitdir")
                root = reject_link_components(repository / ".git" / "worktrees").resolve(
                    strict=True
                )
                candidates = []
                with os.scandir(root) as iterator:
                    for entry in iterator:
                        candidate = root / entry.name
                        info = entry.stat(follow_symlinks=False)
                        if path_is_link(candidate, info.st_mode, file_info=info):
                            raise ValueError("link-backed worktree administration")
                        if stat.S_ISDIR(info.st_mode) and os.path.samefile(target_name, candidate):
                            candidates.append(candidate)
                if len(candidates) != 1:
                    raise ValueError("gitdir does not identify one registered worktree")
                target = candidates[0]
                backpointer = target / "gitdir"
                back_raw = backpointer.read_bytes()
                if (
                    back_raw.count(b"\n") != 1 or not back_raw.endswith(b"\n")
                    or not os.path.samefile(os.fsdecode(back_raw[:-1]), admin_path)
                ):
                    raise ValueError("registered worktree backpointer differs")
            except (OSError, ValueError, UnicodeError) as error:
                raise ToolAttestationError(
                    "worktree Git administration file escapes its repository"
                ) from error
            if not stat.S_ISDIR(target.lstat().st_mode):
                raise ToolAttestationError("worktree Git administration target is unsafe")
        elif not stat.S_ISDIR(admin_info.st_mode):
            raise ToolAttestationError("worktree Git administration entry is unsafe")
        for entry in entries:
            if entry is admin[0]:
                continue
            path = worktree / entry.name
            info = entry.stat(follow_symlinks=False)
            if path_is_link(path, info.st_mode, file_info=info):
                raise ToolAttestationError("filesystem worktree contains a link")
            if stat.S_ISREG(info.st_mode):
                add(path, False)
            elif stat.S_ISDIR(info.st_mode):
                directories, files = walk_directory_tree_no_links(path)
                for directory in directories:
                    add(directory, True)
                for regular in files:
                    add(regular, False)
            else:
                raise ToolAttestationError("filesystem worktree contains a special file")
    except ToolAttestationError:
        raise
    except (OSError, ValueError) as error:
        raise ToolAttestationError("cannot safely inventory filesystem worktree") from error
    return tuple(sorted(paths, key=lambda item: item[0].as_posix()))


def verify_evaluation_source_tree(
    git: Path,
    repository: Path,
    worktree: Path,
    commit: str,
    environment: dict[str, str],
    *,
    allowed_output_roots: Iterable[str] = (),
    allowed_output_files: Iterable[str] = (),
    stage: str,
) -> SourceTreeReceipt:
    """Fail closed on tracked, ordinary-untracked and ignored inputs."""

    if not isinstance(stage, str) or not stage.strip():
        raise ToolAttestationError("source-tree verification stage is empty")
    try:
        repository = require_safe_directory(repository).resolve(strict=True)
        worktree = require_safe_directory(worktree).resolve(strict=True)
    except (OSError, ValueError) as error:
        raise ToolAttestationError(f"evaluation source tree is unsafe {stage}") from error
    roots, files = _canonical_output_policy(allowed_output_roots, allowed_output_files)
    prefix = _run_git(git, worktree, environment, "rev-parse", "--show-prefix")
    inside = _run_git(git, worktree, environment, "rev-parse", "--is-inside-work-tree")
    if (
        prefix.returncode
        or prefix.stdout.rstrip(b"\r\n")
        or inside.returncode
        or inside.stdout.rstrip(b"\r\n") != b"true"
    ):
        raise ToolAttestationError(
            f"Git registered worktree differs from evaluation worktree {stage}"
        )
    tracked_paths = _tracked_path_inventory(git, repository, commit, environment)
    _verify_tracked_parent_directories(worktree, tracked_paths)
    executable_mode_verified = _executable_mode_is_reliable(git, worktree, environment)
    tracked_count = verify_tracked_worktree_bytes(
        git,
        repository,
        worktree,
        commit,
        environment,
        verify_executable_mode=executable_mode_verified,
    )
    tracked_files = {path.as_posix().casefold(): path for path in tracked_paths}
    tracked_directories = {
        parent.as_posix().casefold(): parent
        for path in tracked_paths for parent in path.parents if parent.parts
    }
    policy_parents = {
        parent.as_posix().casefold()
        for path in (*roots, *files) for parent in path.parents if parent.parts
    }
    generated: list[str] = []
    unauthorized: list[str] = []
    for relative, is_directory in _filesystem_worktree_paths(
        repository,
        worktree,
        generated_roots=roots,
        generated_files=files,
    ):
        value = relative.as_posix()
        folded = value.casefold()
        tracked = tracked_directories if is_directory else tracked_files
        if folded in tracked:
            if tracked[folded] != relative:
                raise ToolAttestationError(
                    f"filesystem path casing differs from committed source {stage}: {value}"
                )
            continue
        if is_directory and folded in policy_parents:
            continue
        if not _under(relative, roots) and relative not in files:
            unauthorized.append(value)
            continue
        path = worktree.joinpath(*relative.parts)
        try:
            reject_link_components(path)
            info = path.lstat()
        except (OSError, ValueError) as error:
            raise ToolAttestationError(f"generated output path is unsafe {stage}: {value}") from error
        if path_is_link(path, info.st_mode, file_info=info) or (
            relative in files and not stat.S_ISREG(info.st_mode)
        ) or (relative not in files and not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
            raise ToolAttestationError(
                f"generated output path has an unsafe type {stage}: {value}"
            )
        generated.append(value)
    if unauthorized:
        shown = ", ".join(unauthorized[:8])
        suffix = "" if len(unauthorized) <= 8 else f" (+{len(unauthorized) - 8} more)"
        raise ToolAttestationError(
            f"nontracked source input is not an authorized generated output {stage}: {shown}{suffix}"
        )
    for relative in roots:
        path = worktree.joinpath(*relative.parts)
        if path.exists() or path_is_link(path):
            try:
                reject_link_components(path)
            except (OSError, ValueError) as error:
                raise ToolAttestationError(
                    f"generated output root is link-backed {stage}: {relative.as_posix()}"
                ) from error
    return SourceTreeReceipt(
        commit, tracked_count, tuple(generated), executable_mode_verified
    )


def purge_evaluation_generated_outputs(
    git: Path,
    repository: Path,
    worktree: Path,
    commit: str,
    environment: dict[str, str],
    *,
    output_roots: Iterable[str],
    output_files: Iterable[str],
) -> None:
    """Remove fixed untracked outputs without parsing a poisoned Makefile."""

    try:
        repository = require_safe_directory(repository).resolve(strict=True)
        worktree = require_safe_directory(worktree).resolve(strict=True)
    except (OSError, ValueError) as error:
        raise ToolAttestationError("evaluation purge root is unsafe") from error
    roots, files = _canonical_output_policy(output_roots, output_files)
    tracked = set(_tracked_path_inventory(git, repository, commit, environment))
    for root in roots:
        if any(_folded_under(path, root) for path in tracked):
            raise ToolAttestationError(f"refusing to purge a tracked output root: {root.as_posix()}")
    for relative in files:
        if any(path.as_posix().casefold() == relative.as_posix().casefold() for path in tracked):
            raise ToolAttestationError(f"refusing to purge a tracked output file: {relative.as_posix()}")
    for relative in sorted(roots, key=lambda item: len(item.parts), reverse=True):
        path = worktree.joinpath(*relative.parts)
        if not path.exists() and not path_is_link(path):
            continue
        try:
            reject_link_components(path)
            info = path.lstat()
            if path_is_link(path, info.st_mode, file_info=info) or not stat.S_ISDIR(info.st_mode):
                raise ToolAttestationError(
                    f"generated output root is not a safe directory: {relative.as_posix()}"
                )
            walk_directory_tree_no_links(path)
            shutil.rmtree(path)
        except ToolAttestationError:
            raise
        except (OSError, ValueError) as error:
            raise ToolAttestationError(
                f"cannot safely purge generated output root: {relative.as_posix()}"
            ) from error
    for relative in files:
        path = worktree.joinpath(*relative.parts)
        if not path.exists() and not path_is_link(path):
            continue
        try:
            reject_link_components(path)
            info = path.lstat()
            if path_is_link(path, info.st_mode, file_info=info) or not stat.S_ISREG(info.st_mode):
                raise ToolAttestationError(
                    f"generated output file is not regular: {relative.as_posix()}"
                )
            path.unlink()
        except ToolAttestationError:
            raise
        except (OSError, ValueError) as error:
            raise ToolAttestationError(
                f"cannot safely purge generated output file: {relative.as_posix()}"
            ) from error
