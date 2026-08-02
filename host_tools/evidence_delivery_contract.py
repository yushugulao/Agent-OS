#!/usr/bin/env python3
"""Verify the source-C to evidence-E Git delivery boundary."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path, PureWindowsPath
from typing import Any

from strict_json import read_strict_json

try:
    from . import git_history_contract as _git_history
except ImportError:
    import git_history_contract as _git_history

try:
    from .safe_host_paths import (
        absolute_lexical_path,
        ensure_safe_directory,
        path_is_link,
        read_regular_file,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
        walk_regular_files_no_links,
    )
except ImportError:
    from safe_host_paths import (
        absolute_lexical_path,
        ensure_safe_directory,
        path_is_link,
        read_regular_file,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
        walk_regular_files_no_links,
    )


POLICY_VERSION = 1
INDEX_PATH = "evidence/releases/INDEX.md"
BINDING_KIND = "git-containing-head"
BINDING_OID = "SELF"
SAFE_RELEASE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
MAX_COMMITTED_FILES = 1000
MAX_COMMITTED_FILE_BYTES = 64 << 20
MAX_COMMITTED_TOTAL_BYTES = 256 << 20
MANIFEST_SOURCE_FIELDS = {
    ("agentos-evaluation-evidence-bundle", 5): "source_commit",
    (None, 6): "commit",
    (None, 7): "commit",
}
FULL_EVIDENCE_FIELDS = {
    "authenticity",
    "collected_at_utc",
    "command",
    "commit",
    "configuration",
    "delivery",
    "environment",
    "metrics",
    "raw_artifacts",
    "schema_version",
    "status",
    "verification_summary",
}
DOCUMENTATION_DESCENDANT_FILES = {"README.md", "evidence/README.md"}
DOCUMENTATION_DESCENDANT_PREFIX = "docs/"
GIT_SYSTEM_ENVIRONMENT_KEYS = (
    "COMSPEC",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
)
GIT_FIXED_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "LANG": "C",
    "LC_ALL": "C",
}
SAFE_GIT_CONFIG_ARGUMENTS = ("-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false")


class DeliveryContractError(RuntimeError):
    pass


def _safe_directory(path: Path, label: str) -> Path:
    try:
        return require_safe_directory(path)
    except (OSError, ValueError) as error:
        raise DeliveryContractError(f"{label} is missing or link-backed: {path}") from error


def _safe_regular_file(path: Path, label: str) -> Path:
    try:
        return require_regular_file(path)
    except (OSError, ValueError) as error:
        raise DeliveryContractError(f"{label} is missing or link-backed: {path}") from error


def _reject_link_chain(path: Path, label: str) -> Path:
    try:
        return reject_link_components(path)
    except (OSError, ValueError) as error:
        raise DeliveryContractError(f"{label} is link-backed: {path}") from error


def _ensure_directory(path: Path, label: str) -> Path:
    try:
        return ensure_safe_directory(path)
    except (OSError, ValueError) as error:
        raise DeliveryContractError(f"{label} is link-backed: {path}") from error


def _new_file_path(path: Path, label: str) -> Path:
    absolute = absolute_lexical_path(path)
    _ensure_directory(absolute.parent, f"{label} parent")
    try:
        absolute.lstat()
    except FileNotFoundError:
        return absolute
    raise DeliveryContractError(f"{label} already exists or is unsafe: {absolute}")


def release_path(name: str) -> str:
    if not isinstance(name, str) or SAFE_RELEASE.fullmatch(name) is None:
        raise DeliveryContractError("evidence release name is invalid")
    return f"evidence/releases/{name}"


def make_manifest_binding(source_commit: str, release_name: str) -> dict[str, object]:
    if FULL_COMMIT.fullmatch(source_commit) is None:
        raise DeliveryContractError("source commit is invalid")
    path = release_path(release_name)
    return {
        "source_commit": source_commit,
        "evidence_commit": {
            "binding": BINDING_KIND,
            "oid": BINDING_OID,
            "source_parent": source_commit,
            "policy_version": POLICY_VERSION,
            "index_path": INDEX_PATH,
        },
        "release": {"name": release_name, "path": path},
    }


def validate_manifest_binding(
    source_commit: object, evidence_commit: object, release: object
) -> tuple[str, dict[str, object], dict[str, str]]:
    if not isinstance(source_commit, str) or FULL_COMMIT.fullmatch(source_commit) is None:
        raise DeliveryContractError("manifest source commit is invalid")
    if not isinstance(release, dict) or set(release) != {"name", "path"}:
        raise DeliveryContractError("manifest release binding is invalid")
    name = release.get("name")
    if not isinstance(name, str) or release != {"name": name, "path": release_path(name)}:
        raise DeliveryContractError("manifest release binding is invalid")
    expected = make_manifest_binding(source_commit, name)["evidence_commit"]
    if evidence_commit != expected:
        raise DeliveryContractError("manifest evidence commit binding is invalid")
    return source_commit, dict(evidence_commit), {"name": name, "path": release["path"]}


def validate_delivery_field(value: object, expected_source: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "source_commit", "evidence_commit", "release"
    }:
        raise DeliveryContractError("manifest delivery binding is invalid")
    source, evidence, release = validate_manifest_binding(
        value.get("source_commit"), value.get("evidence_commit"), value.get("release")
    )
    if source != expected_source:
        raise DeliveryContractError("manifest delivery source differs from verified commit")
    return {"source_commit": source, "evidence_commit": evidence, "release": release}


def index_record(release: dict[str, str], source_commit: str) -> bytes:
    value = (
        f"- {release['path']}/: source_commit={source_commit}; "
        f"evidence_commit={BINDING_KIND}\n"
    )
    return value.encode("ascii")


def _git_path(git: str | os.PathLike[str]) -> str:
    value = os.fspath(git)
    if Path(value).is_absolute() or Path(value).parent != Path("."):
        candidate = _safe_regular_file(
            absolute_lexical_path(Path(value)), "git executable"
        )
        return str(candidate)
    found = shutil.which(value)
    if found is None:
        raise DeliveryContractError(f"git executable not found: {value}")
    return str(_safe_regular_file(Path(found), "git executable"))


def controlled_git_environment() -> dict[str, str]:
    """Return the minimal process environment trusted by Git verification."""

    environment = {
        name: value
        for name in GIT_SYSTEM_ENVIRONMENT_KEYS
        if (value := os.environ.get(name))
    }
    environment.update(GIT_FIXED_ENVIRONMENT)
    return environment


def _decode_git_path(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise DeliveryContractError("Git source path is not UTF-8") from error
    candidate = Path(value)
    if (
        not value
        or "\\" in value
        or candidate.is_absolute()
        or candidate.as_posix() != value
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise DeliveryContractError(f"Git source path is unsafe: {value!r}")
    return value


def _git_blob_oid(raw: bytes, oid_length: int) -> str:
    framed = f"blob {len(raw)}\0".encode("ascii") + raw
    if oid_length == 40:
        return hashlib.sha1(framed).hexdigest()
    if oid_length == 64:
        return hashlib.sha256(framed).hexdigest()
    raise DeliveryContractError("Git repository uses an unsupported object format")


def tracked_worktree_identity(
    git: str, repo: Path
) -> tuple[bool, str]:
    """Compare real tracked bytes with HEAD without trusting index stat hints.

    Git's normal status and diff commands intentionally honor ``assume-unchanged``
    and ``skip-worktree``.  Formal source binding must not: both flags are
    rejected, and every HEAD blob is hashed from the link-free worktree path.
    """

    flags_raw = _bytes(git, repo, "ls-files", "-v", "-z", "--")
    flagged_paths: set[str] = set()
    for record in flags_raw.split(b"\0"):
        if not record:
            continue
        if len(record) < 3 or record[1:2] != b" ":
            raise DeliveryContractError("Git index flag inventory is malformed")
        path = _decode_git_path(record[2:])
        if record[:1] != b"H":
            raise DeliveryContractError(
                f"Git index has a hidden or nonstandard tracked flag: {path}"
            )
        if path in flagged_paths:
            raise DeliveryContractError("Git index contains a duplicate tracked path")
        flagged_paths.add(path)

    tree_raw = _bytes(git, repo, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    digest = hashlib.sha256()
    tree_paths: set[str] = set()
    matches_head = True
    for record in tree_raw.split(b"\0"):
        if not record:
            continue
        header, separator, raw_path = record.partition(b"\t")
        fields = header.split(b" ")
        if not separator or len(fields) != 3:
            raise DeliveryContractError("Git HEAD tree inventory is malformed")
        raw_mode, object_type, raw_oid = fields
        if (
            object_type != b"blob"
            or raw_mode not in {b"100644", b"100755", b"120000"}
            or re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", raw_oid) is None
        ):
            raise DeliveryContractError(
                "Git HEAD contains an unsupported tracked object"
            )
        path = _decode_git_path(raw_path)
        if path in tree_paths:
            raise DeliveryContractError("Git HEAD tree contains a duplicate path")
        tree_paths.add(path)
        worktree_path = repo / Path(path)
        raw: bytes | None
        try:
            reject_link_components(worktree_path.parent)
            info = worktree_path.lstat()
        except FileNotFoundError:
            raw = None
        except (OSError, ValueError) as error:
            raise DeliveryContractError(
                f"tracked source parent is unsafe: {path}"
            ) from error
        else:
            if raw_mode == b"120000":
                if not stat.S_ISLNK(info.st_mode):
                    raw = None
                else:
                    try:
                        raw = os.fsencode(os.readlink(worktree_path))
                    except OSError as error:
                        raise DeliveryContractError(
                            f"tracked source link cannot be read: {path}"
                        ) from error
            else:
                if path_is_link(worktree_path, info.st_mode, file_info=info):
                    raise DeliveryContractError(
                        f"tracked source file is link-backed: {path}"
                    )
                if not stat.S_ISREG(info.st_mode):
                    raw = None
                else:
                    try:
                        raw = read_regular_file(worktree_path)
                    except (OSError, ValueError) as error:
                        raise DeliveryContractError(
                            f"tracked source file cannot be read safely: {path}"
                        ) from error

        path_bytes = path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(raw_mode)
        digest.update(raw_oid)
        if raw is None:
            digest.update(b"missing\0")
            matches_head = False
            continue
        actual_sha256 = hashlib.sha256(raw).digest()
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(actual_sha256)
        if _git_blob_oid(raw, len(raw_oid)) != raw_oid.decode("ascii"):
            matches_head = False

    if flagged_paths != tree_paths:
        matches_head = False
    return matches_head, digest.hexdigest()


def _run(
    git: str, repo: Path, *args: str, check: bool = True, text: bool = False,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[Any]:
    result = subprocess.run(
        [git, *SAFE_GIT_CONFIG_ARGUMENTS, *args], cwd=repo, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, text=text, input=input_bytes,
        env=controlled_git_environment(),
    )
    if check and result.returncode:
        stdout = result.stdout if text else result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr if text else result.stderr.decode("utf-8", errors="replace")
        detail = (stderr or stdout).strip()
        raise DeliveryContractError(f"git {' '.join(args)} failed: {detail}")
    return result


def _text(git: str, repo: Path, *args: str) -> str:
    return _run(git, repo, *args, text=True).stdout.strip()


def _bytes(git: str, repo: Path, *args: str,
           input_bytes: bytes | None = None) -> bytes:
    return _run(git, repo, *args, input_bytes=input_bytes).stdout

def _raw_commit_ancestry(
    git: str,
    repo: Path,
    head: str,
    *,
    budget: _git_history.HistoryBudget | None = None,
) -> dict[str, list[str]]:
    try:
        return _git_history.raw_commit_ancestry(
            git,
            repo,
            head,
            controlled_git_environment(),
            budget=budget,
        )
    except _git_history.GitHistoryError as error:
        raise DeliveryContractError(str(error)) from error


def _discover_repo(git: str, bundle: Path) -> Path | None:
    result = _run(git, bundle, "rev-parse", "--show-toplevel", check=False, text=True)
    if result.returncode:
        return None
    reported = result.stdout.strip()
    if not reported or any(character in reported for character in "\0\r\n"):
        raise DeliveryContractError("Git reported an invalid repository path")
    candidate = Path(reported)
    if not candidate.is_absolute() and PureWindowsPath(reported).is_absolute():
        cygpath = shutil.which("cygpath")
        if cygpath is None and Path("/usr/bin/cygpath").is_file():
            # A deliberately sparse MSYS2 environment can omit /usr/bin from
            # PATH even though Python and Git still execute in that namespace.
            cygpath = "/usr/bin/cygpath"
        if cygpath is None:
            raise DeliveryContractError(
                "Git reported a Windows repository path outside a convertible domain"
            )
        try:
            converted = subprocess.run(
                [cygpath, "-a", "-u", reported],
                cwd=bundle,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise DeliveryContractError(
                "Git repository path conversion failed"
            ) from error
        lines = converted.stdout.splitlines()
        if converted.returncode != 0 or len(lines) != 1 or not lines[0]:
            raise DeliveryContractError(
                "Git repository path conversion failed"
            )
        candidate = Path(lines[0])
    elif not candidate.is_absolute():
        candidate = bundle / candidate
    discovered = _safe_directory(
        absolute_lexical_path(candidate), "discovered repository"
    )
    return discovered.resolve(strict=True)


def _require_no_symlink_path(repo: Path, target: Path) -> None:
    lexical_repo = _safe_directory(
        absolute_lexical_path(repo), "evidence repository"
    )
    lexical_target = _reject_link_chain(
        absolute_lexical_path(target), "evidence delivery path"
    )
    repo = lexical_repo.resolve(strict=True)
    target = lexical_target.resolve(strict=False)
    try:
        relative = target.relative_to(repo)
    except ValueError as error:
        raise DeliveryContractError("evidence bundle is outside the repository") from error


def _decode_path(value: bytes) -> str:
    try:
        path = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DeliveryContractError("Git delivery path is not UTF-8") from error
    if not path or any(ord(character) < 0x20 for character in path):
        raise DeliveryContractError("Git delivery path contains control characters")
    return path


def _parse_name_status(raw: bytes) -> list[tuple[str, tuple[str, ...]]]:
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    records: list[tuple[str, tuple[str, ...]]] = []
    index = 0
    while index < len(fields):
        try:
            status = fields[index].decode("ascii")
        except UnicodeDecodeError as error:
            raise DeliveryContractError("Git diff status is invalid") from error
        index += 1
        path_count = 2 if status.startswith(("R", "C")) else 1
        if index + path_count > len(fields):
            raise DeliveryContractError("Git diff name-status output is truncated")
        paths = tuple(_decode_path(item) for item in fields[index:index + path_count])
        index += path_count
        records.append((status, paths))
    return records


def _diff_name_status(git: str, repo: Path, source: str, evidence: str) -> list[tuple[str, tuple[str, ...]]]:
    raw = _bytes(
        git, repo, "diff", "--name-status", "-z", "--find-renames",
        "--find-copies", source, evidence, "--",
    )
    return _parse_name_status(raw)


def _tree_entries(
    git: str, repo: Path, commit: str, prefix: str
) -> dict[str, tuple[str, str, str, int | None]]:
    raw = _bytes(git, repo, "ls-tree", "-r", "-l", "-z", commit, "--", prefix)
    entries: dict[str, tuple[str, str, str, int | None]] = {}
    for row in raw.split(b"\0"):
        if not row:
            continue
        try:
            metadata, raw_path = row.split(b"\t", 1)
            mode, kind, oid, raw_size = metadata.decode("ascii").split()
            size = int(raw_size) if kind == "blob" else None
        except (ValueError, UnicodeDecodeError) as error:
            raise DeliveryContractError("Git tree inventory is invalid") from error
        if size is not None and size < 0:
            raise DeliveryContractError("Git tree inventory has an invalid blob size")
        path = _decode_path(raw_path)
        if path in entries:
            raise DeliveryContractError("Git tree inventory repeats a path")
        entries[path] = (mode, kind, oid, size)
    return entries


def _verify_committed_tree_limits(
    tree: dict[str, tuple[str, str, str, int | None]],
) -> None:
    """Enforce delivery limits using only sizes recorded in the Git tree."""
    if len(tree) > MAX_COMMITTED_FILES:
        raise DeliveryContractError("committed evidence contains too many tracked files")
    total = 0
    for path, (mode, kind, _oid, size) in tree.items():
        if mode != "100644" or kind != "blob" or size is None:
            raise DeliveryContractError(
                f"evidence tree entry is not a regular data file: {path}"
            )
        if size > MAX_COMMITTED_FILE_BYTES:
            raise DeliveryContractError(
                f"committed evidence file exceeds size limit: {path}"
            )
        total += size
        if total > MAX_COMMITTED_TOTAL_BYTES:
            raise DeliveryContractError("committed evidence exceeds total size limit")


def _blob(git: str, repo: Path, commit: str, path: str) -> bytes:
    return _bytes(git, repo, "show", f"{commit}:{path}")


def _worktree_files(bundle: Path) -> set[str]:
    bundle = _safe_directory(
        absolute_lexical_path(bundle), "evidence release"
    )
    try:
        inventory = walk_regular_files_no_links(
            bundle,
            max_files=MAX_COMMITTED_FILES,
            max_directories=MAX_COMMITTED_FILES + 1,
            max_total_bytes=MAX_COMMITTED_TOTAL_BYTES,
        )
    except (OSError, ValueError) as error:
        raise DeliveryContractError(
            "evidence delivery tree is unsafe or exceeds its resource limits"
        ) from error
    files: set[str] = set()
    for path in inventory:
        relative = path.relative_to(bundle).as_posix()
        if any(ord(character) < 0x20 for character in relative):
            raise DeliveryContractError("evidence filename contains control characters")
        if path.stat().st_size > MAX_COMMITTED_FILE_BYTES:
            raise DeliveryContractError(
                f"evidence delivery file exceeds size limit: {relative}"
            )
        files.add(relative)
    if not files:
        raise DeliveryContractError("evidence release contains no files")
    return files


def prepare_index_update(
    repo: Path, output: Path, source_commit: str, release: dict[str, str],
    git: str | os.PathLike[str] = "git",
) -> tuple[Path, bytes] | None:
    """Return an exact append-only index update for in-repository collection."""
    repo = _safe_directory(
        absolute_lexical_path(repo), "evidence repository"
    ).resolve(strict=True)
    # Canonicalize both sides before the containment check.  On Windows a
    # temporary path may use an 8.3 component while resolve() expands the
    # repository path, and lexical ``..`` components have the same issue on
    # every platform.  The release directory does not exist yet, so keep the
    # non-strict resolution semantics.
    lexical_output = _reject_link_chain(
        absolute_lexical_path(output), "evidence output"
    )
    output = lexical_output.resolve(strict=False)
    try:
        relative = output.relative_to(repo).as_posix()
    except ValueError:
        return None
    if relative != release["path"]:
        raise DeliveryContractError(
            "in-repository evidence output must be evidence/releases/<bundle>"
        )
    executable = _git_path(git)
    resolved = _text(executable, repo, "rev-parse", "--verify", f"{source_commit}^{{commit}}")
    if resolved != source_commit:
        raise DeliveryContractError("source commit does not exist exactly")
    head = _text(executable, repo, "rev-parse", "--verify", "HEAD^{commit}")
    if head != source_commit:
        raise DeliveryContractError(
            "evidence must be published while source C is the current HEAD"
        )
    index_path = repo / INDEX_PATH
    index_path = _safe_regular_file(index_path, "evidence release index")
    committed = _blob(executable, repo, source_commit, INDEX_PATH)
    current = index_path.read_bytes()
    if current != committed or not current.endswith(b"\n"):
        raise DeliveryContractError("evidence release index differs from source commit")
    record = index_record(release, source_commit)
    if record in current.splitlines(keepends=True):
        raise DeliveryContractError("evidence release is already indexed")
    return index_path, current + record


def publish_bundle_and_index(
    repo: Path, stage: Path, output: Path, source_commit: str,
    release: dict[str, str], git: str | os.PathLike[str] = "git",
) -> None:
    """Publish a ready bundle and its append-only index update as one transaction."""
    stage = _safe_directory(absolute_lexical_path(stage), "staged evidence bundle")
    output = absolute_lexical_path(output)
    _reject_link_chain(output, "evidence output")
    _ensure_directory(output.parent, "evidence output parent")
    update = prepare_index_update(repo, output, source_commit, release, git=git)
    temporary: Path | None = None
    if output.exists() or path_is_link(output):
        raise DeliveryContractError(f"evidence output already exists: {output}")
    try:
        if update is not None:
            index_path, index_bytes = update
            temporary = index_path.with_name(f".{index_path.name}.partial.{os.getpid()}")
            temporary = _new_file_path(temporary, "evidence index transaction")
            with temporary.open("xb") as handle:
                handle.write(index_bytes)
                handle.flush()
                os.fsync(handle.fileno())
        _ensure_directory(output.parent, "evidence output parent")
        if output.exists() or path_is_link(output):
            raise DeliveryContractError(f"evidence output already exists: {output}")
        os.replace(stage, output)
        if update is not None:
            try:
                _safe_regular_file(temporary, "evidence index transaction")
                _safe_regular_file(update[0], "evidence release index")
                os.replace(temporary, update[0])
            except BaseException:
                _safe_directory(output, "published evidence bundle")
                _reject_link_chain(stage, "evidence rollback target")
                os.replace(output, stage)
                raise
    finally:
        if temporary is not None:
            try:
                _safe_regular_file(temporary, "evidence index transaction").unlink()
            except DeliveryContractError:
                pass


def _resolve_delivery_repo(
    bundle: Path,
    release: dict[str, str],
    executable: str,
    repo_root: Path | None,
    require_committed: bool,
) -> tuple[Path, Path] | None:
    original_bundle = _safe_directory(
        absolute_lexical_path(bundle), "evidence bundle path"
    )
    bundle = original_bundle.resolve(strict=True)
    if repo_root is None:
        repo = _discover_repo(executable, bundle)
        if repo is None:
            if require_committed:
                raise DeliveryContractError("evidence bundle is not in a Git repository")
            return None
    else:
        repo = _safe_directory(
            absolute_lexical_path(repo_root), "repository root"
        ).resolve(strict=True)
        discovered = _discover_repo(executable, repo)
        try:
            same_repository = (
                discovered is not None and os.path.samefile(discovered, repo)
            )
        except OSError:
            same_repository = False
        if not same_repository:
            raise DeliveryContractError("repository root is invalid")
    _require_no_symlink_path(repo, bundle)
    try:
        relative = bundle.relative_to(repo).as_posix()
    except ValueError as error:
        raise DeliveryContractError("evidence bundle is outside the repository") from error
    if relative != release["path"]:
        raise DeliveryContractError("bundle path differs from manifest release binding")
    dirty = _text(executable, repo, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise DeliveryContractError("repository worktree is dirty during evidence verification")
    tracked_clean, _tracked_digest = tracked_worktree_identity(executable, repo)
    if not tracked_clean:
        raise DeliveryContractError(
            "repository tracked bytes differ from HEAD during evidence verification"
        )
    return repo, bundle


def _verify_delivery_commit(
    bundle: Path,
    repo: Path,
    executable: str,
    source_commit: str,
    release: dict[str, str],
    evidence: str,
    *,
    raw_ancestry: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    source = _text(executable, repo, "rev-parse", "--verify", f"{source_commit}^{{commit}}")
    if source != source_commit:
        raise DeliveryContractError("manifest source commit does not exist exactly")
    resolved_evidence = _text(
        executable, repo, "rev-parse", "--verify", f"{evidence}^{{commit}}"
    )
    if resolved_evidence != evidence:
        raise DeliveryContractError("evidence commit does not exist exactly")
    ancestry = raw_ancestry or _raw_commit_ancestry(executable, repo, evidence)
    parents = ancestry.get(evidence)
    if parents != [source_commit]:
        raise DeliveryContractError("evidence commit must have source C as its sole parent")

    source_release = _tree_entries(executable, repo, source_commit, release["path"])
    if source_release:
        raise DeliveryContractError("release path already existed in source commit")
    tree = _tree_entries(executable, repo, evidence, release["path"])
    if not tree:
        raise DeliveryContractError("evidence commit does not contain the release")
    prefix = release["path"] + "/"
    if any(not path.startswith(prefix) for path in tree):
        raise DeliveryContractError("evidence tree escaped its release directory")
    _verify_committed_tree_limits(tree)
    actual_files = _worktree_files(bundle)
    tree_files = {path[len(prefix):] for path in tree}
    if actual_files != tree_files:
        raise DeliveryContractError("committed evidence inventory differs from worktree bundle")
    ordered_paths = sorted(tree)
    path_request = b"".join(path.encode("utf-8") + b"\n" for path in ordered_paths)
    oid_rows = _bytes(executable, repo, "hash-object", "--no-filters",
                      "--stdin-paths", input_bytes=path_request).splitlines()
    if len(oid_rows) != len(ordered_paths):
        raise DeliveryContractError("worktree blob identity inventory is invalid")
    for path, worktree_oid in zip(ordered_paths, oid_rows):
        if re.fullmatch(rb"[0-9a-f]{40}", worktree_oid) is None:
            raise DeliveryContractError("worktree blob identity inventory is invalid")
        if worktree_oid.decode("ascii") != tree[path][2]:
            raise DeliveryContractError(f"committed evidence bytes differ from worktree: {path}")
    expected_changes = {
        ("A", (path,)) for path in tree
    } | {("M", (INDEX_PATH,))}
    actual_changes = set(_diff_name_status(executable, repo, source_commit, evidence))
    if actual_changes != expected_changes:
        raise DeliveryContractError(
            "C..E changes paths or statuses outside the evidence delivery allowlist"
        )
    source_index_tree = _tree_entries(executable, repo, source_commit, INDEX_PATH)
    evidence_index_tree = _tree_entries(executable, repo, evidence, INDEX_PATH)
    for label, index_tree in (
        ("source", source_index_tree),
        ("evidence", evidence_index_tree),
    ):
        if set(index_tree) != {INDEX_PATH}:
            raise DeliveryContractError(f"{label} evidence index tree is invalid")
        mode, kind, _oid, size = index_tree[INDEX_PATH]
        if mode != "100644" or kind != "blob" or size is None:
            raise DeliveryContractError(
                f"{label} evidence index must be a 100644 regular blob"
            )
    source_index = _blob(executable, repo, source_commit, INDEX_PATH)
    evidence_index = _blob(executable, repo, evidence, INDEX_PATH)
    if not source_index.endswith(b"\n"):
        raise DeliveryContractError("source evidence index is not newline terminated")
    if evidence_index != source_index + index_record(release, source_commit):
        raise DeliveryContractError("evidence index update is not the exact append-only record")
    return {
        "status": "committed",
        "source_commit": source_commit,
        "evidence_commit": evidence,
        "release_path": release["path"],
        "index_path": INDEX_PATH,
        "files_verified": len(tree),
        "policy_version": POLICY_VERSION,
    }


def verify_committed_delivery(
    bundle: Path,
    source_commit: str,
    evidence_commit: dict[str, object],
    release: dict[str, str],
    *,
    git: str | os.PathLike[str] = "git",
    repo_root: Path | None = None,
    require_committed: bool = False,
) -> dict[str, object]:
    """Resolve symbolic SELF to current HEAD E and prove C..E is evidence-only."""
    validate_manifest_binding(source_commit, evidence_commit, release)
    executable = _git_path(git)
    resolved = _resolve_delivery_repo(
        bundle, release, executable, repo_root, require_committed
    )
    if resolved is None:
        return {
            "status": "detached-uncommitted",
            "source_commit": source_commit,
            "evidence_commit": None,
            "policy_version": POLICY_VERSION,
        }
    repo, bundle = resolved
    evidence = _text(executable, repo, "rev-parse", "--verify", "HEAD^{commit}")
    return _verify_delivery_commit(
        bundle, repo, executable, source_commit, release, evidence
    )


def _documentation_descendant_path(path: str) -> bool:
    return (
        path in DOCUMENTATION_DESCENDANT_FILES
        or path.startswith(DOCUMENTATION_DESCENDANT_PREFIX)
    )


def _verify_documentation_descendants(
    executable: str,
    repo: Path,
    evidence: str,
    head: str,
    *,
    head_ancestry: dict[str, list[str]] | None = None,
    budget: _git_history.HistoryBudget | None = None,
) -> None:
    """Require every commit after E to be documentation-only, not just net-clean."""

    history_budget = budget or _git_history.HistoryBudget()
    head_graph = (
        head_ancestry
        if head_ancestry is not None
        else _raw_commit_ancestry(executable, repo, head, budget=history_budget)
    )
    if evidence not in head_graph:
        raise DeliveryContractError("evidence commit is not an ancestor of current HEAD")
    try:
        changed = _git_history.documentation_changed_paths(
            executable,
            repo,
            evidence,
            head_graph,
            controlled_git_environment(),
            history_budget,
        )
    except _git_history.GitHistoryError as error:
        raise DeliveryContractError(str(error)) from error
    forbidden = sorted(path for path in changed if not _documentation_descendant_path(path))
    if forbidden:
        raise DeliveryContractError(
            f"E..HEAD must be documentation-only; forbidden paths: {forbidden}"
        )


def verify_historical_committed_delivery(
    bundle: Path,
    source_commit: str,
    evidence_commit: dict[str, object],
    release: dict[str, str],
    *,
    git: str | os.PathLike[str] = "git",
    repo_root: Path | None = None,
    require_committed: bool = False,
) -> dict[str, object]:
    """Prove the unique immutable introduction E from any clean descendant HEAD."""
    validate_manifest_binding(source_commit, evidence_commit, release)
    executable = _git_path(git)
    resolved = _resolve_delivery_repo(
        bundle, release, executable, repo_root, require_committed
    )
    if resolved is None:
        return {
            "status": "detached-uncommitted",
            "source_commit": source_commit,
            "evidence_commit": None,
            "policy_version": POLICY_VERSION,
        }
    repo, bundle = resolved
    head = _text(executable, repo, "rev-parse", "--verify", "HEAD^{commit}")
    history_budget = _git_history.HistoryBudget()
    head_ancestry = _raw_commit_ancestry(executable, repo, head, budget=history_budget)
    direct_children = sorted(commit for commit, parents in head_ancestry.items()
                             if parents == [source_commit])
    try:
        candidates = sorted(
            _git_history.commits_containing_path(
                executable, repo, direct_children, release["path"],
                controlled_git_environment(), history_budget))
    except _git_history.GitHistoryError as error:
        raise DeliveryContractError(str(error)) from error
    if len(candidates) != 1:
        raise DeliveryContractError(
            "evidence release must have one immutable introducing commit in HEAD history"
        )
    evidence = candidates[0]
    _verify_documentation_descendants(
        executable, repo, evidence, head, head_ancestry=head_ancestry,
        budget=history_budget,
    )
    result = _verify_delivery_commit(
        bundle,
        repo,
        executable,
        source_commit,
        release,
        evidence,
        raw_ancestry=head_ancestry,
    )
    evidence_index = _blob(executable, repo, evidence, INDEX_PATH)
    head_index = _blob(executable, repo, head, INDEX_PATH)
    if head_index != evidence_index:
        raise DeliveryContractError(
            "current evidence index differs from the introducing commit"
        )
    result["status"] = "committed-history"
    result["containing_head"] = head
    return result


def verify_manifest_delivery(
    bundle: Path, repo_root: Path, git: str | os.PathLike[str] = "git"
) -> dict[str, object]:
    try:
        manifest_path = _safe_regular_file(
            absolute_lexical_path(bundle) / "manifest.json", "bundle manifest"
        )
        manifest = read_strict_json(manifest_path)
    except (OSError, UnicodeDecodeError, ValueError, DeliveryContractError) as error:
        raise DeliveryContractError(f"bundle manifest is invalid: {error}") from error
    if not isinstance(manifest, dict):
        raise DeliveryContractError("bundle manifest is invalid")
    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int:
        raise DeliveryContractError(
            "bundle manifest kind/schema is unsupported by verify-committed"
        )
    identity = (manifest.get("kind"), schema_version)
    source_field = MANIFEST_SOURCE_FIELDS.get(identity)
    if source_field is None:
        raise DeliveryContractError(
            "bundle manifest kind/schema is unsupported by verify-committed"
        )
    if identity in {(None, 6), (None, 7)} and (
        set(manifest) != FULL_EVIDENCE_FIELDS or manifest.get("status") != "ready"
    ):
        raise DeliveryContractError(f"full-evidence schema v{schema_version} manifest is not ready or differs")
    source_commit = manifest.get(source_field)
    if not isinstance(source_commit, str):
        raise DeliveryContractError("bundle manifest source commit is invalid")
    delivery = validate_delivery_field(manifest.get("delivery"), source_commit)
    return verify_historical_committed_delivery(
        bundle, delivery["source_commit"], delivery["evidence_commit"],
        delivery["release"], git=git, repo_root=repo_root, require_committed=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verify-committed", choices=("verify-committed",))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--git", default="git")
    args = parser.parse_args()
    try:
        result = verify_manifest_delivery(
            args.bundle, args.repo_root, args.git
        )
    except DeliveryContractError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
