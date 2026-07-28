#!/usr/bin/env python3
"""Verify the source-C to evidence-E Git delivery boundary."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from strict_json import read_strict_json


POLICY_VERSION = 1
INDEX_PATH = "evidence/releases/INDEX.md"
BINDING_KIND = "git-containing-head"
BINDING_OID = "SELF"
SAFE_RELEASE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class DeliveryContractError(RuntimeError):
    pass


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
        candidate = Path(value).absolute()
        if not candidate.is_file():
            raise DeliveryContractError(f"git executable is invalid: {candidate}")
        return str(candidate)
    found = shutil.which(value)
    if found is None:
        raise DeliveryContractError(f"git executable not found: {value}")
    return found


def _run(
    git: str, repo: Path, *args: str, check: bool = True, text: bool = False
) -> subprocess.CompletedProcess[Any]:
    result = subprocess.run(
        [git, *args], cwd=repo, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, text=text,
    )
    if check and result.returncode:
        stdout = result.stdout if text else result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr if text else result.stderr.decode("utf-8", errors="replace")
        detail = (stderr or stdout).strip()
        raise DeliveryContractError(f"git {' '.join(args)} failed: {detail}")
    return result


def _text(git: str, repo: Path, *args: str) -> str:
    return _run(git, repo, *args, text=True).stdout.strip()


def _bytes(git: str, repo: Path, *args: str) -> bytes:
    return _run(git, repo, *args).stdout


def _discover_repo(git: str, bundle: Path) -> Path | None:
    result = _run(git, bundle, "rev-parse", "--show-toplevel", check=False, text=True)
    if result.returncode:
        return None
    return Path(result.stdout.strip()).resolve()


def _require_no_symlink_path(repo: Path, target: Path) -> None:
    try:
        relative = target.relative_to(repo)
    except ValueError as error:
        raise DeliveryContractError("evidence bundle is outside the repository") from error
    current = repo
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise DeliveryContractError(f"evidence delivery path is a symlink: {current}")


def _decode_path(value: bytes) -> str:
    try:
        path = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DeliveryContractError("Git delivery path is not UTF-8") from error
    if not path or any(ord(character) < 0x20 for character in path):
        raise DeliveryContractError("Git delivery path contains control characters")
    return path


def _diff_name_status(git: str, repo: Path, source: str, evidence: str) -> list[tuple[str, tuple[str, ...]]]:
    raw = _bytes(
        git, repo, "diff", "--name-status", "-z", "--find-renames",
        "--find-copies", source, evidence, "--",
    )
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


def _tree_entries(git: str, repo: Path, commit: str, prefix: str) -> dict[str, tuple[str, str, str]]:
    raw = _bytes(git, repo, "ls-tree", "-r", "-z", commit, "--", prefix)
    entries: dict[str, tuple[str, str, str]] = {}
    for row in raw.split(b"\0"):
        if not row:
            continue
        try:
            metadata, raw_path = row.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ")
        except (ValueError, UnicodeDecodeError) as error:
            raise DeliveryContractError("Git tree inventory is invalid") from error
        path = _decode_path(raw_path)
        if path in entries:
            raise DeliveryContractError("Git tree inventory repeats a path")
        entries[path] = (mode, kind, oid)
    return entries


def _blob(git: str, repo: Path, commit: str, path: str) -> bytes:
    return _bytes(git, repo, "show", f"{commit}:{path}")


def _worktree_files(bundle: Path) -> set[str]:
    files: set[str] = set()
    for path in bundle.rglob("*"):
        if path.is_symlink():
            raise DeliveryContractError(
                f"evidence delivery contains a symlink: {path.relative_to(bundle)}"
            )
        if path.is_file():
            relative = path.relative_to(bundle).as_posix()
            if any(ord(character) < 0x20 for character in relative):
                raise DeliveryContractError("evidence filename contains control characters")
            files.add(relative)
    if not files:
        raise DeliveryContractError("evidence release contains no files")
    return files


def prepare_index_update(
    repo: Path, output: Path, source_commit: str, release: dict[str, str],
    git: str | os.PathLike[str] = "git",
) -> tuple[Path, bytes] | None:
    """Return an exact append-only index update for in-repository collection."""
    repo = repo.resolve()
    output = output.absolute()
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
    index_path = repo / INDEX_PATH
    if index_path.is_symlink() or not index_path.is_file():
        raise DeliveryContractError("evidence release index is missing or unsafe")
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
    update = prepare_index_update(repo, output, source_commit, release, git=git)
    temporary: Path | None = None
    if output.exists():
        raise DeliveryContractError(f"evidence output already exists: {output}")
    try:
        if update is not None:
            index_path, index_bytes = update
            temporary = index_path.with_name(f".{index_path.name}.partial.{os.getpid()}")
            if temporary.exists():
                raise DeliveryContractError(f"stale evidence index transaction: {temporary}")
            with temporary.open("xb") as handle:
                handle.write(index_bytes)
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(stage, output)
        if update is not None:
            try:
                os.replace(temporary, update[0])
            except BaseException:
                os.replace(output, stage)
                raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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
    """Resolve symbolic SELF to E and prove that C..E is evidence-only."""
    validate_manifest_binding(source_commit, evidence_commit, release)
    executable = _git_path(git)
    original_bundle = bundle.absolute()
    if original_bundle.is_symlink() or not original_bundle.is_dir():
        raise DeliveryContractError("evidence bundle path is missing or unsafe")
    bundle = original_bundle.resolve()
    if repo_root is None:
        repo = _discover_repo(executable, bundle)
        if repo is None:
            if require_committed:
                raise DeliveryContractError("evidence bundle is not in a Git repository")
            return {
                "status": "detached-uncommitted",
                "source_commit": source_commit,
                "evidence_commit": None,
                "policy_version": POLICY_VERSION,
            }
    else:
        repo = repo_root.resolve()
        discovered = _discover_repo(executable, repo)
        if discovered is None or discovered != repo:
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
    source = _text(executable, repo, "rev-parse", "--verify", f"{source_commit}^{{commit}}")
    if source != source_commit:
        raise DeliveryContractError("manifest source commit does not exist exactly")
    evidence = _text(executable, repo, "rev-parse", "--verify", "HEAD^{commit}")
    parents = _text(executable, repo, "rev-list", "--parents", "-n", "1", evidence).split()
    if parents != [evidence, source_commit]:
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
    for path, (mode, kind, _oid) in tree.items():
        if mode != "100644" or kind != "blob":
            raise DeliveryContractError(f"evidence tree entry is not a regular data file: {path}")
    actual_files = _worktree_files(bundle)
    tree_files = {path[len(prefix):] for path in tree}
    if actual_files != tree_files:
        raise DeliveryContractError("committed evidence inventory differs from worktree bundle")
    for path, (_mode, _kind, oid) in tree.items():
        worktree_oid = _text(
            executable, repo, "hash-object", "--no-filters", "--", path
        )
        if worktree_oid != oid:
            raise DeliveryContractError(f"committed evidence bytes differ from worktree: {path}")

    expected_changes = {
        ("A", (path,)) for path in tree
    } | {("M", (INDEX_PATH,))}
    actual_changes = set(_diff_name_status(executable, repo, source_commit, evidence))
    if actual_changes != expected_changes:
        raise DeliveryContractError(
            "C..E changes paths or statuses outside the evidence delivery allowlist"
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


def verify_manifest_delivery(
    bundle: Path, repo_root: Path, git: str | os.PathLike[str] = "git"
) -> dict[str, object]:
    try:
        manifest = read_strict_json(bundle / "manifest.json")
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise DeliveryContractError(f"bundle manifest is invalid: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("commit"), str):
        raise DeliveryContractError("bundle manifest is invalid")
    delivery = validate_delivery_field(manifest.get("delivery"), manifest["commit"])
    return verify_committed_delivery(
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
            args.bundle.resolve(), args.repo_root.resolve(), args.git
        )
    except DeliveryContractError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
