#!/usr/bin/env python3
"""Facade for formal source closure and executable identity attestation."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from .safe_host_paths import path_is_link
except ImportError:
    from safe_host_paths import path_is_link

try:
    from .formal_temp_binding import (
        capture_formal_temporary_binding,
        cygwin_native_directory,
    )
except ImportError:
    from formal_temp_binding import (
        capture_formal_temporary_binding,
        cygwin_native_directory,
    )

try:
    from .formal_python_runtime import (
        FORMAL_CYGWIN_LOCALE,
        FORMAL_ENVIRONMENT_FIXED,
        FORMAL_EXECUTION_OVERRIDE_KEYS,
        POSIX_SYSTEM_PATHS,
        FormalPythonRuntimeError,
        controlled_search_path,
        create_formal_python_runtime,
        formal_execution_overrides,
        validate_formal_evidence_binding,
        validate_formal_python_runtime_record,
        validate_formal_python_tool_binding,
    )
except ImportError:
    from formal_python_runtime import (
        FORMAL_CYGWIN_LOCALE,
        FORMAL_ENVIRONMENT_FIXED,
        FORMAL_EXECUTION_OVERRIDE_KEYS,
        POSIX_SYSTEM_PATHS,
        FormalPythonRuntimeError,
        controlled_search_path,
        create_formal_python_runtime,
        formal_execution_overrides,
        validate_formal_evidence_binding,
        validate_formal_python_runtime_record,
        validate_formal_python_tool_binding,
    )

try:
    from .evaluation_source_gate import (
        EVALUATION_ARTIFACT_OUTPUT_FILES,
        EVALUATION_ARTIFACT_OUTPUT_ROOTS,
        EVALUATION_BUILD_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_ROOTS,
        EVALUATION_CACHE_OUTPUT_ROOTS,
        SAFE_GIT_CONFIG_ARGUMENTS,
        SourceTreeReceipt,
        ToolAttestationError,
        purge_evaluation_generated_outputs,
        require_clean_head,
        verify_evaluation_source_tree,
        verify_tracked_worktree_bytes,
    )
except ImportError:
    from evaluation_source_gate import (
        EVALUATION_ARTIFACT_OUTPUT_FILES,
        EVALUATION_ARTIFACT_OUTPUT_ROOTS,
        EVALUATION_BUILD_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_ROOTS,
        EVALUATION_CACHE_OUTPUT_ROOTS,
        SAFE_GIT_CONFIG_ARGUMENTS,
        SourceTreeReceipt,
        ToolAttestationError,
        purge_evaluation_generated_outputs,
        require_clean_head,
        verify_evaluation_source_tree,
        verify_tracked_worktree_bytes,
    )


CONTROLLED_PATH = ":".join(POSIX_SYSTEM_PATHS)


def decode_external_output(payload: bytes | None) -> str:
    """Decode human-facing tool output without depending on the host locale."""

    return (payload or b"").decode("utf-8", errors="replace")


def _run_git(
    git: Path,
    directory: Path,
    environment: dict[str, str],
    *arguments: str,
    input_data: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(git), *SAFE_GIT_CONFIG_ARGUMENTS, *arguments],
        cwd=directory, env=environment, input=input_data,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )


def _git_isolation_arguments(root: Path) -> list[str]:
    hooks, template = root / "empty-hooks", root / "empty-template"
    attributes = root / "empty-attributes"
    hooks.mkdir(parents=True, exist_ok=True)
    template.mkdir(parents=True, exist_ok=True)
    attributes.touch(exist_ok=True)
    return [
        "-c", f"core.hooksPath={hooks}",
        "-c", f"core.attributesFile={attributes}",
        "-c", "core.autocrlf=false",
        "-c", "core.safecrlf=false",
        "-c", "core.fsmonitor=false",
        "-c", "core.untrackedCache=false",
        "-c", "core.sparseCheckout=false",
        "-c", f"init.templateDir={template}",
    ]


def create_isolated_detached_worktree(
    git: Path,
    source: Path,
    commit: str,
    root: Path,
    environment: dict[str, str],
) -> tuple[Path, Path]:
    """Checkout ``commit`` without inheriting repository filters or hooks."""

    isolated, worktree = root / "repository", root / "worktree"
    options = _git_isolation_arguments(root)
    clone = _run_git(
        git, root, environment, *options, "clone", "--local", "--no-hardlinks",
        "--no-checkout", "--no-tags", "--no-recurse-submodules", "--",
        str(source), str(isolated),
    )
    if clone.returncode:
        raise ToolAttestationError("cannot create isolated evidence repository")
    local_config = _run_git(
        git, isolated, environment, "config", "--local", "--name-only", "--list"
    )
    names = local_config.stdout.decode("utf-8", "replace").lower().splitlines()
    if local_config.returncode or any(
        name.startswith("filter.") or name in {"core.hookspath", "core.attributesfile"}
        for name in names
    ):
        raise ToolAttestationError("isolated evidence repository inherited unsafe config")
    add = _run_git(
        git, isolated, environment, *options, "worktree", "add", "--detach",
        "--no-checkout", str(worktree), commit,
    )
    if add.returncode:
        raise ToolAttestationError("cannot register isolated evidence worktree")
    reset = _run_git(git, worktree, environment, *options, "reset", "--hard", commit)
    if reset.returncode:
        raise ToolAttestationError("cannot materialize isolated evidence worktree")
    detached = _run_git(git, worktree, environment, "symbolic-ref", "-q", "HEAD")
    head = _run_git(git, worktree, environment, "rev-parse", "HEAD")
    status = _run_git(
        git, worktree, environment, *options, "status", "--porcelain",
        "--untracked-files=all",
    )
    if (
        detached.returncode != 1
        or head.returncode
        or head.stdout.strip().decode("ascii", "replace") != commit
        or status.returncode
        or status.stdout
    ):
        raise ToolAttestationError("execution worktree is not a clean detached commit")
    verify_tracked_worktree_bytes(git, isolated, worktree, commit, environment)
    return isolated, worktree


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_executable(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or candidate.parent != Path("."):
        result = candidate.resolve(strict=True)
    else:
        found = shutil.which(value)
        if found is None:
            raise ToolAttestationError(f"required executable not found: {value}")
        result = Path(found).resolve(strict=True)
    if not result.is_file() or result.is_symlink():
        raise ToolAttestationError(f"required executable is not a file: {result}")
    return result


def resolve_bash_executable(value: str, git: Path | None = None) -> Path:
    """Resolve a real Bash, preferring the shell beside this POSIX runtime."""

    requested = Path(value)
    if requested.is_absolute() or requested.parent != Path(".") or value != "bash":
        candidates = [requested]
    else:
        executable = Path(sys.executable).resolve(strict=True)
        names = ("bash", "bash.exe")
        candidates = [executable.with_name(name) for name in names]
        if git is not None:
            roots = (git.parent, git.parent.parent / "bin", git.parent.parent / "usr" / "bin")
            candidates.extend(root / name for root in roots for name in names)
        if found := shutil.which(value):
            candidates.append(Path(found))
    failures: list[str] = []
    seen: set[Path] = set()
    probe_environment = {"PATH": os.defpath, "LC_ALL": "C", "LANG": "C"}
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR", "COMSPEC"):
            if name in os.environ:
                probe_environment[name] = os.environ[name]
    for candidate in candidates:
        try:
            resolved = resolve_executable(str(candidate))
        except (OSError, ToolAttestationError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            probe = subprocess.run(
                [str(resolved), "--noprofile", "--norc", "-c", "printf '%s\\n' \"${BASH_VERSION-}\""],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=False, timeout=10,
                env=probe_environment,
            )
        except (OSError, subprocess.SubprocessError) as error:
            failures.append(f"{resolved}: {error}")
            continue
        version = decode_external_output(probe.stdout).strip()
        if probe.returncode == 0 and re.fullmatch(
            r"[0-9]+\.[0-9]+(?:\.[0-9]+)?[^\r\n]*", version
        ):
            return resolved
        failures.append(f"{resolved}: rc={probe.returncode}: {version}")
    detail = "; ".join(failures) or "no executable candidate"
    raise ToolAttestationError(f"required GNU Bash not found: {detail}")


def controlled_environment(
    root: Path,
    tool_directories: list[Path],
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    if extra is not None and (
        set(extra) != FORMAL_EXECUTION_OVERRIDE_KEYS
        or any(not isinstance(value, str) or not value for value in extra.values())
    ):
        raise ToolAttestationError("formal execution override schema differs")
    home, temporary = root / "home", root / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    temporary.mkdir(parents=True, exist_ok=True)
    system_paths = list(POSIX_SYSTEM_PATHS) if os.name == "posix" else [os.defpath]
    search_path = controlled_search_path(tool_directories, os.pathsep, system_paths)
    system_drive = os.environ.get("SYSTEMDRIVE", "") if sys.platform == "cygwin" else "/"
    if re.fullmatch(r"[A-Z]:", system_drive) is None and system_drive != "/":
        raise ToolAttestationError("host system drive identity is not canonical")
    native_temporary = str(temporary)
    if sys.platform == "cygwin":
        native_temporary = cygwin_native_directory(temporary)
    environment = {
        "PATH": search_path,
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "TEMP": native_temporary,
        "TMP": native_temporary,
        "SYSTEMDRIVE": system_drive,
    }
    if os.name == "posix":
        environment.update(FORMAL_ENVIRONMENT_FIXED)
    else:
        environment.update({
            **FORMAL_ENVIRONMENT_FIXED,
            "GIT_CONFIG_GLOBAL": os.devnull,
        })
    if sys.platform == "cygwin":
        environment.update({
            "LANG": FORMAL_CYGWIN_LOCALE,
            "LC_ALL": FORMAL_CYGWIN_LOCALE,
        })
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"):
            if name in os.environ:
                environment[name] = os.environ[name]
    if extra is not None:
        environment.update(extra)
    return environment


def require_nested_tool_resolution(
    tools: dict[str, Path], environment: dict[str, str]
) -> None:
    """Bind legacy bare tool calls to the executable records in the manifest."""

    for label, command in (("git", "git"), ("make", "make"), ("bash", "bash")):
        found = shutil.which(command, path=environment.get("PATH", ""))
        try:
            resolved = Path(found).resolve(strict=True) if found else None
        except OSError as error:
            raise ToolAttestationError(f"nested {command} executable is unavailable") from error
        if resolved != tools[label]:
            raise ToolAttestationError(
                f"nested {command} does not resolve to the attested {label} tool"
            )


def capture_version(
    label: str,
    executable: Path,
    directory: Path,
    environment: dict[str, str],
) -> dict[str, object]:
    before = _sha256_file(executable)
    result = subprocess.run(
        [str(executable), "--version"], stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False, env=environment,
    )
    raw_output = result.stdout or b""
    output = decode_external_output(raw_output)
    if result.returncode or not output.strip():
        raise ToolAttestationError(f"cannot capture {label} version")
    if _sha256_file(executable) != before:
        raise ToolAttestationError(f"{label} executable changed during pre-run attestation")
    relative = f"environment/versions/{label}.txt"
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw_output)
    return {
        "label": label, "path": str(executable), "executable_sha256": before,
        "first_line": output.splitlines()[0], "log": relative,
        "log_sha256": _sha256_file(path),
    }


def verify_tool_attestations(
    tools: dict[str, Path],
    records: list[dict[str, object]],
    directory: Path,
    environment: dict[str, str],
    stage: str,
) -> None:
    by_label = {record.get("label"): record for record in records}
    if set(by_label) != set(tools) or len(by_label) != len(records):
        raise ToolAttestationError("tool attestation inventory is invalid")
    for label, executable in tools.items():
        record = by_label[label]
        before = _sha256_file(executable)
        result = subprocess.run(
            [str(executable), "--version"], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False, env=environment,
        )
        raw_output = result.stdout or b""
        output = decode_external_output(raw_output)
        after = _sha256_file(executable)
        version_path = directory / str(record["log"])
        if (
            result.returncode != 0 or not output.strip() or before != after
            or before != record.get("executable_sha256")
            or str(executable) != record.get("path")
            or output.splitlines()[0] != record.get("first_line")
            or version_path.read_bytes() != raw_output
            or _sha256_file(version_path) != record.get("log_sha256")
        ):
            raise ToolAttestationError(f"{label} tool identity changed {stage}")
