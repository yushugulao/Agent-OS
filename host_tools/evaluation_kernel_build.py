#!/usr/bin/env python3
"""Build both kernel-cost targets from one clean, committed source snapshot.

This is the trusted producer for the sidecars consumed by
``evaluation_kernel_cost.py``.  It deliberately owns the build commands and
the process environment instead of accepting either from a caller-supplied
manifest.
"""

from __future__ import annotations

import sys as _entry_sys


def _isolate_direct_entry_imports() -> None:
    """Use only interpreter-owned paths for top-level import resolution."""

    if __name__ != "__main__":
        return
    prefixes = {
        value.replace("\\", "/").rstrip("/").casefold()
        for value in (
            _entry_sys.base_prefix, _entry_sys.base_exec_prefix,
            _entry_sys.prefix, _entry_sys.exec_prefix,
        )
        if value
    }
    _entry_sys.path[:] = [
        value for value in _entry_sys.path
        if value and any(
            (normalized := value.replace("\\", "/").rstrip("/").casefold())
            == prefix or normalized.startswith(f"{prefix}/")
            for prefix in prefixes
        )
    ]


_isolate_direct_entry_imports()

import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(
        Path(tempfile.gettempdir()) / f"agentos-pycache-{os.urandom(16).hex()}"
    )
    if not __package__:
        import types as _entry_types

        _entry_package = _entry_types.ModuleType("host_tools")
        _entry_package.__path__ = [str(Path(__file__).resolve().parent)]
        _entry_package.__package__ = "host_tools"
        sys.modules["host_tools"] = _entry_package
        __package__ = "host_tools"
        sys.path.append(_entry_package.__path__[0])

try:
    from . import evaluation_kernel_cost as cost
    from .evidence_delivery_contract import (
        DeliveryContractError,
        controlled_git_environment,
        tracked_worktree_identity,
    )
    from .evidence_toolchain_attestation import (
        EVALUATION_ARTIFACT_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_ROOTS,
        EVALUATION_CACHE_OUTPUT_ROOTS,
        ToolAttestationError,
        purge_evaluation_generated_outputs,
        verify_evaluation_source_tree,
    )
    from .safe_host_paths import (
        absolute_lexical_path,
        ensure_safe_directory,
        path_is_link,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
    )
except ImportError:
    import evaluation_kernel_cost as cost
    from evidence_delivery_contract import (
        DeliveryContractError,
        controlled_git_environment,
        tracked_worktree_identity,
    )
    from evidence_toolchain_attestation import (
        EVALUATION_ARTIFACT_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_ROOTS,
        EVALUATION_CACHE_OUTPUT_ROOTS,
        ToolAttestationError,
        purge_evaluation_generated_outputs,
        verify_evaluation_source_tree,
    )
    from safe_host_paths import (
        absolute_lexical_path,
        ensure_safe_directory,
        path_is_link,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
    )


BUILDER_VERSION = cost.TRUSTED_BUILD_SCHEMA_VERSION
BUILD_CONFIG_KIND = cost.TRUSTED_BUILD_CONFIG_KIND
BUILD_LOG_KIND = cost.TRUSTED_BUILD_LOG_KIND
COMMAND_TIMEOUT_SECONDS = cost.TRUSTED_BUILD_TIMEOUT_SECONDS
MAX_COMMAND_OUTPUT_BYTES = cost.TRUSTED_BUILD_MAX_OUTPUT_BYTES
EXPECTED_TARGETS = {
    "baseline": {
        "path": "baseline_ucore/build/kernel",
        "cwd": "baseline_ucore",
        "clean_target": "clean",
        "build_target": "build/kernel",
    },
    "treatment": {
        "path": "build/kernel",
        "cwd": ".",
        "clean_target": "clean",
        "build_target": "build/kernel",
    },
}


class KernelBuildError(ValueError):
    """Raised when a build cannot produce trustworthy evidence."""


@dataclass(frozen=True)
class CommandExecution:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    duration_ms: int
    error: str | None = None


@dataclass(frozen=True)
class ToolIdentity:
    name: str
    invocation_path: Path
    path: Path
    recorded_path: str
    resolved_path: str
    sha256: str


CommandRunner = Callable[
    [Sequence[str], Path, Mapping[str, str], int, int], CommandExecution
]


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _bytes_sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lexical_absolute(path: Path) -> Path:
    return absolute_lexical_path(path)


def _inside(root: Path, candidate: Path, label: str) -> tuple[Path, str]:
    lexical = _lexical_absolute(candidate if candidate.is_absolute() else root / candidate)
    try:
        reject_link_components(lexical)
    except (OSError, ValueError) as error:
        raise KernelBuildError(f"{label} is link-backed: {candidate}") from error
    try:
        absolute = lexical.resolve(strict=False)
        relative_path = absolute.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise KernelBuildError(f"{label} escapes the repository: {candidate}") from error
    if not relative_path.parts:
        raise KernelBuildError(f"{label} cannot be the repository root")
    relative = PurePosixPath(*relative_path.parts).as_posix()
    if any(part in {"", ".", ".."} for part in PurePosixPath(relative).parts):
        raise KernelBuildError(f"{label} is not a canonical relative path")
    return absolute, relative


def _regular_file(path: Path, label: str) -> None:
    try:
        require_regular_file(path)
    except (OSError, ValueError) as error:
        raise KernelBuildError(
            f"{label} is not a regular non-link file: {path}"
        ) from error


def _safe_directory(path: Path, label: str) -> Path:
    try:
        return require_safe_directory(path)
    except (OSError, ValueError) as error:
        raise KernelBuildError(f"{label} is not a safe directory: {path}") from error


def _ensure_directory(path: Path, label: str) -> Path:
    try:
        return ensure_safe_directory(path)
    except (OSError, ValueError) as error:
        raise KernelBuildError(f"{label} is not a safe directory: {path}") from error


def _recorded_absolute(path: Path) -> str:
    resolved = path.resolve()
    return resolved.as_posix() if os.name == "nt" else str(resolved)


def _reject_external_link_components(path: Path, label: str) -> None:
    try:
        reject_link_components(path)
    except (OSError, ValueError) as error:
        raise KernelBuildError(f"{label} is link-backed: {path}") from error


def _resolve_toolchain(toolprefix: str) -> tuple[str, list[ToolIdentity]]:
    if not toolprefix or any(character in toolprefix for character in "\x00\r\n"):
        raise KernelBuildError("toolprefix must be bounded single-line text")
    raw_prefix = Path(toolprefix)
    if not raw_prefix.is_absolute():
        raise KernelBuildError("toolprefix must be absolute")
    absolute_prefix = _lexical_absolute(raw_prefix)
    prefix = absolute_prefix.as_posix() if os.name == "nt" else str(absolute_prefix)
    identities: list[ToolIdentity] = []
    for name in cost.TRUSTED_BUILD_TOOL_NAMES:
        base = Path(str(absolute_prefix) + name)
        candidates = [base]
        if os.name == "nt":
            candidates.append(Path(str(base) + ".exe"))
        existing = [candidate for candidate in candidates if candidate.exists()]
        if len(existing) != 1:
            raise KernelBuildError(
                f"toolprefix must resolve exactly one {name} executable"
            )
        candidate = existing[0]
        _reject_external_link_components(candidate.parent, f"toolchain {name} parent")
        _regular_file(candidate, f"toolchain {name} invocation")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise KernelBuildError(f"toolchain {name} cannot be resolved") from error
        _regular_file(resolved, f"resolved toolchain {name}")
        identities.append(
            ToolIdentity(
                name,
                candidate,
                resolved,
                candidate.absolute().as_posix() if os.name == "nt" else str(candidate.absolute()),
                _recorded_absolute(resolved),
                _file_sha(resolved),
            )
        )
    return prefix, identities


def _require_tool_hashes(
    make_tool: Path,
    make_sha256: str,
    toolchain: Sequence[ToolIdentity],
) -> None:
    _regular_file(make_tool, "make tool")
    if _file_sha(make_tool) != make_sha256:
        raise KernelBuildError("make tool changed during the kernel build")
    for item in toolchain:
        _regular_file(item.invocation_path, f"toolchain {item.name} invocation")
        try:
            current_target = item.invocation_path.resolve(strict=True)
        except OSError as error:
            raise KernelBuildError(
                f"toolchain {item.name} invocation cannot be resolved"
            ) from error
        if current_target != item.path:
            raise KernelBuildError(
                f"toolchain {item.name} link target changed during the kernel build"
            )
        _regular_file(item.path, f"toolchain {item.name}")
        if _file_sha(item.path) != item.sha256:
            raise KernelBuildError(
                f"toolchain {item.name} changed during the kernel build"
            )


def _git(root: Path, arguments: Sequence[str], maximum: int = 1024 * 1024) -> bytes:
    environment = os.environ.copy()
    environment.update({"LANG": "C", "LC_ALL": "C"})
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c",
             "core.untrackedCache=false", "-C", str(root), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise KernelBuildError(f"cannot inspect repository: {error}") from error
    if (
        result.returncode != 0
        or len(result.stdout) > maximum
        or len(result.stderr) > maximum
    ):
        detail = result.stderr[:512].decode("utf-8", errors="replace").strip()
        raise KernelBuildError(f"git inspection failed: {detail or result.returncode}")
    return result.stdout


def _repository_state(root: Path) -> tuple[str, str, bool]:
    try:
        commit = _git(root, ["rev-parse", "--verify", "HEAD^{commit}"]).decode(
            "ascii", errors="strict"
        ).strip()
        status = _git(
            root, ["status", "--porcelain=v1", "--untracked-files=all"]
        ).decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise KernelBuildError("repository state is not canonical text") from error
    if cost.COMMIT_RE.fullmatch(commit) is None:
        raise KernelBuildError("repository HEAD is not a canonical commit")
    try:
        tracked_clean, _tracked_digest = tracked_worktree_identity("git", root)
    except DeliveryContractError as error:
        raise KernelBuildError(
            f"tracked source identity is unsafe: {error}"
        ) from error
    return commit, status, tracked_clean


def _require_same_clean_head(root: Path, expected_commit: str | None = None) -> str:
    commit, status, tracked_clean = _repository_state(root)
    if status or not tracked_clean:
        raise KernelBuildError("repository must be clean, including untracked files")
    if expected_commit is not None and commit != expected_commit:
        raise KernelBuildError("repository HEAD changed during the kernel build")
    return commit


def _source_gate(
    root: Path, commit: str, evidence_root_relative: str, stage: str
) -> None:
    git_name = shutil.which("git")
    if git_name is None:
        raise KernelBuildError("Git executable is unavailable")
    git = Path(git_name)
    _regular_file(git, "Git executable")
    git = git.resolve(strict=True)
    roots = (
        *EVALUATION_BUILD_OUTPUT_ROOTS,
        *EVALUATION_CACHE_OUTPUT_ROOTS,
        evidence_root_relative,
    )
    try:
        verify_evaluation_source_tree(
            git,
            root,
            root,
            commit,
            controlled_git_environment(),
            allowed_output_roots=roots,
            allowed_output_files=(
                *EVALUATION_BUILD_OUTPUT_FILES,
                *EVALUATION_ARTIFACT_OUTPUT_FILES,
            ),
            stage=stage,
        )
    except (OSError, ToolAttestationError) as error:
        raise KernelBuildError(f"kernel build source gate failed {stage}: {error}") from error


def _purge_build_outputs(root: Path, commit: str) -> None:
    git_name = shutil.which("git")
    if git_name is None:
        raise KernelBuildError("Git executable is unavailable")
    git = Path(git_name)
    _regular_file(git, "Git executable")
    git = git.resolve(strict=True)
    try:
        purge_evaluation_generated_outputs(
            git,
            root,
            root,
            commit,
            controlled_git_environment(),
            output_roots=(
                *EVALUATION_BUILD_OUTPUT_ROOTS,
                *EVALUATION_CACHE_OUTPUT_ROOTS,
            ),
            output_files=EVALUATION_BUILD_OUTPUT_FILES,
        )
    except (OSError, ToolAttestationError) as error:
        raise KernelBuildError(f"kernel build output purge failed: {error}") from error


def _require_tracked(root: Path, relative: str, label: str) -> None:
    output = _git(root, ["ls-files", "--error-unmatch", "--", relative], 4096)
    try:
        observed = output.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as error:
        raise KernelBuildError(f"{label} tracked path is not UTF-8") from error
    if observed != [relative]:
        raise KernelBuildError(f"{label} must be exactly one committed file")


def _require_ignored(root: Path, relative: str) -> None:
    environment = os.environ.copy()
    environment.update({"LANG": "C", "LC_ALL": "C"})
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", "--", relative],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise KernelBuildError(f"cannot verify output ignore policy: {error}") from error
    if result.returncode != 0:
        raise KernelBuildError(
            "output directory must be ignored so evidence cannot dirty the bound HEAD"
        )


class _RepositoryLock(AbstractContextManager["_RepositoryLock"]):
    def __init__(self, root: Path) -> None:
        raw = _git(root, ["rev-parse", "--git-path", "agentos-kernel-build.lock"], 4096)
        try:
            value = raw.decode("utf-8", errors="strict").strip()
        except UnicodeError as error:
            raise KernelBuildError("git lock path is not UTF-8") from error
        candidate = Path(value)
        self.path = _lexical_absolute(candidate if candidate.is_absolute() else root / candidate)
        _reject_external_link_components(self.path, "kernel build lock")
        self.handle: Any = None

    def __enter__(self) -> "_RepositoryLock":
        _ensure_directory(self.path.parent, "kernel build lock parent")
        if path_is_link(self.path):
            raise KernelBuildError("kernel build lock is link-backed")
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        if self.handle.read(1) == b"":
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as error:
            self.handle.close()
            self.handle = None
            raise KernelBuildError("another trusted kernel build is active") from error
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        import signal

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()


def _run_command(
    argv: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    maximum_output: int,
) -> CommandExecution:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="agentos-kernel-build-command-") as temporary:
        stdout_path = Path(temporary) / "stdout"
        stderr_path = Path(temporary) / "stderr"
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                creationflags = 0
                popen_options: dict[str, Any] = {}
                if os.name == "nt":
                    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                else:
                    popen_options["start_new_session"] = True
                process = subprocess.Popen(
                    list(argv),
                    cwd=cwd,
                    env=dict(environment),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=creationflags,
                    **popen_options,
                )
                deadline = started + timeout_seconds
                error: str | None = None
                while process.poll() is None:
                    if (
                        stdout_path.stat().st_size > maximum_output
                        or stderr_path.stat().st_size > maximum_output
                    ):
                        error = "output_limit_exceeded"
                        _terminate(process)
                        break
                    if time.monotonic() >= deadline:
                        error = "timeout"
                        _terminate(process)
                        break
                    time.sleep(0.01)
                returncode = process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired) as execution_error:
            return CommandExecution(
                None,
                b"",
                str(execution_error).encode("utf-8")[:maximum_output],
                int((time.monotonic() - started) * 1000),
                "execution_error",
            )
        stdout_raw = stdout_path.read_bytes()[: maximum_output + 1]
        stderr_raw = stderr_path.read_bytes()[: maximum_output + 1]
        if len(stdout_raw) > maximum_output or len(stderr_raw) > maximum_output:
            error = "output_limit_exceeded"
        return CommandExecution(
            returncode,
            stdout_raw[:maximum_output],
            stderr_raw[:maximum_output],
            int((time.monotonic() - started) * 1000),
            error,
        )


def _fixed_environment(commit_epoch: str, toolprefix: str) -> dict[str, str]:
    # MSYS/Cygwin tools need a UTF-8 C locale to preserve non-ASCII Win32
    # paths when a compiler subprogram is normalized through cygpath.
    build_locale = "C.UTF-8" if sys.platform in {"cygwin", "msys"} else "C"
    environment: dict[str, str] = {
        "LANG": build_locale,
        "LC_ALL": build_locale,
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": commit_epoch,
        "TOOLPREFIX": toolprefix,
        "TZ": "UTC",
    }
    # Only process-discovery and OS bootstrap variables are inherited.  Build
    # flags and tool overrides must come from the committed Makefiles.
    inherited = ("PATH", "SystemRoot", "COMSPEC", "PATHEXT", "HOME", "TMP", "TEMP")
    for name in inherited:
        if name in os.environ:
            environment[name] = os.environ[name]
    return dict(sorted(environment.items()))


def _command_record(
    sequence: int,
    target_id: str,
    phase: str,
    cwd_relative: str,
    argv: Sequence[str],
    execution: CommandExecution,
    environment_sha256: str,
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "target_id": target_id,
        "phase": phase,
        "cwd": cwd_relative,
        "argv": list(argv),
        "environment_sha256": environment_sha256,
        "returncode": execution.returncode,
        "duration_ms": execution.duration_ms,
        "error": execution.error,
        "stdout_base64": base64.b64encode(execution.stdout).decode("ascii"),
        "stdout_sha256": _bytes_sha(execution.stdout),
        "stderr_base64": base64.b64encode(execution.stderr).decode("ascii"),
        "stderr_sha256": _bytes_sha(execution.stderr),
    }


def _first_line(raw: bytes, label: str) -> str:
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as error:
        raise KernelBuildError(f"{label} output is not UTF-8") from error
    line = next((item.strip() for item in lines if item.strip()), "")
    if not line or len(line) > 512 or any(ord(character) < 32 for character in line):
        raise KernelBuildError(f"{label} version is not bounded printable text")
    return line


def _write(path: Path, raw: bytes) -> None:
    _ensure_directory(path.parent, "build evidence parent")
    if path_is_link(path):
        raise KernelBuildError(f"build evidence output is link-backed: {path}")
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _receipt(relative: str, raw: bytes) -> dict[str, str]:
    return {"path": relative, "sha256": _bytes_sha(raw)}


def build_evidence(
    *,
    config_path: Path,
    repository_root: Path,
    make_tool: Path,
    toolprefix: str,
    run_id: str,
    output_dir: Path,
    evidence_root: Path | None = None,
    runner: CommandRunner = _run_command,
) -> dict[str, Any]:
    """Run the fixed dual build and atomically publish collector sidecars."""

    if cost.ID_RE.fullmatch(run_id) is None:
        raise KernelBuildError("run id is not a canonical identifier")
    root = _safe_directory(
        _lexical_absolute(repository_root), "repository root"
    ).resolve(strict=True)
    config_absolute, config_relative = _inside(root, config_path, "kernel cost config")
    if config_relative != "ci/evaluation-kernel-cost.json":
        raise KernelBuildError("kernel cost config must use the fixed committed path")
    output_absolute, output_relative = _inside(root, output_dir, "output directory")
    portable_root = root if evidence_root is None else _safe_directory(
        _lexical_absolute(evidence_root), "evidence root"
    ).resolve(strict=True)
    try:
        portable_root.relative_to(root)
        portable_output_relative = output_absolute.relative_to(portable_root)
    except ValueError as error:
        raise KernelBuildError("output directory must be inside the evidence root") from error
    portable_output = PurePosixPath(*portable_output_relative.parts).as_posix()
    if not portable_output or any(
        part in {"", ".", ".."} for part in PurePosixPath(portable_output).parts
    ):
        raise KernelBuildError("output directory is not canonical within the evidence root")
    if output_absolute.exists():
        raise KernelBuildError("output directory already exists")
    if portable_root == root:
        gate_root_path = output_absolute.relative_to(root)
    else:
        gate_root_path = portable_root.relative_to(root)
    evidence_gate_root = PurePosixPath(*gate_root_path.parts).as_posix()
    if not evidence_gate_root or evidence_gate_root == ".":
        raise KernelBuildError("kernel build evidence root must be below the repository")
    _reject_external_link_components(output_absolute.parent, "output directory parent")
    _regular_file(config_absolute, "kernel cost config")
    config, config_raw = cost.load_config(config_absolute)
    _require_ignored(root, output_relative)

    tool_lexical = _lexical_absolute(make_tool)
    _regular_file(tool_lexical, "make tool")
    tool = tool_lexical.resolve()
    _regular_file(tool, "resolved make tool")
    make_sha256 = _file_sha(tool)
    recorded_toolprefix, toolchain_identities = _resolve_toolchain(toolprefix)

    by_role = {target["role"]: target for target in config["targets"]}
    for role, expected in EXPECTED_TARGETS.items():
        if by_role[role]["required_relative_path"] != expected["path"]:
            raise KernelBuildError(f"{role} target path is not the fixed trusted path")
    _require_tracked(root, config_relative, "kernel cost config")
    for relative in (
        "Makefile",
        "baseline_ucore/Makefile",
        "ci/kernel-budgets.json",
        "scripts/check-kernel-budgets.py",
        "scripts/probes/struct-proc-size.c",
        "scripts/check-user-stack-usage.py",
        "user_stack_policy.h",
        "user/Makefile",
    ):
        path, canonical = _inside(root, Path(relative), f"{relative} build input")
        _regular_file(path, f"{relative} build input")
        _require_tracked(root, canonical, f"{relative} build input")

    with _RepositoryLock(root):
        commit = _require_same_clean_head(root)
        _source_gate(root, commit, evidence_gate_root, "before trusted purge")
        _purge_build_outputs(root, commit)
        _source_gate(root, commit, evidence_gate_root, "after trusted purge")
        commit_epoch = _git(root, ["show", "-s", "--format=%ct", commit], 4096).decode(
            "ascii", errors="strict"
        ).strip()
        if not commit_epoch.isdigit():
            raise KernelBuildError("commit timestamp is invalid")
        _require_tool_hashes(tool, make_sha256, toolchain_identities)
        environment = _fixed_environment(commit_epoch, recorded_toolprefix)
        environment_sha = _bytes_sha(cost._canonical_json(environment))
        commands: list[dict[str, Any]] = []

        make_version_argv = [str(tool), "--version"]
        version_execution = runner(
            make_version_argv,
            root,
            environment,
            COMMAND_TIMEOUT_SECONDS,
            MAX_COMMAND_OUTPUT_BYTES,
        )
        commands.append(
            _command_record(
                0, "builder", "make_version", ".", make_version_argv,
                version_execution, environment_sha
            )
        )
        if version_execution.error is not None or version_execution.returncode != 0:
            raise KernelBuildError("make --version did not complete successfully")
        make_version = _first_line(version_execution.stdout, "make")
        _require_tool_hashes(tool, make_sha256, toolchain_identities)
        _require_same_clean_head(root, commit)

        toolchain_records: list[dict[str, Any]] = []
        sequence = 1
        for identity in toolchain_identities:
            version_argv = [identity.recorded_path, "--version"]
            execution = runner(
                version_argv,
                root,
                environment,
                COMMAND_TIMEOUT_SECONDS,
                MAX_COMMAND_OUTPUT_BYTES,
            )
            commands.append(
                _command_record(
                    sequence,
                    "toolchain",
                    f"{identity.name}_version",
                    ".",
                    version_argv,
                    execution,
                    environment_sha,
                )
            )
            sequence += 1
            if execution.error is not None or execution.returncode != 0:
                raise KernelBuildError(
                    f"toolchain {identity.name} --version did not complete successfully"
                )
            toolchain_records.append(
                {
                    "name": identity.name,
                    "path": identity.recorded_path,
                    "resolved_path": identity.resolved_path,
                    "sha256": identity.sha256,
                    "version_argv": version_argv,
                    "version": _first_line(execution.stdout, identity.name),
                }
            )
            _require_tool_hashes(tool, make_sha256, toolchain_identities)
            _require_same_clean_head(root, commit)
        toolchain_sha = _bytes_sha(
            cost._canonical_json(
                {"prefix": recorded_toolprefix, "tools": toolchain_records}
            )
        )

        target_receipts: list[dict[str, Any]] = []
        for configured_target in config["targets"]:
            role = configured_target["role"]
            specification = EXPECTED_TARGETS[role]
            target_id = configured_target["id"]
            cwd_relative = specification["cwd"]
            cwd = root if cwd_relative == "." else root / cwd_relative
            target_path, target_relative = _inside(
                root, Path(specification["path"]), f"{target_id} kernel"
            )
            for phase, make_target in (
                ("clean", specification["clean_target"]),
                ("build", specification["build_target"]),
            ):
                argv = [str(tool)]
                if cwd_relative != ".":
                    argv.extend(["-C", cwd_relative])
                    command_cwd = root
                else:
                    command_cwd = cwd
                argv.append(f"TOOLPREFIX={recorded_toolprefix}")
                argv.append(make_target)
                execution = runner(
                    argv,
                    command_cwd,
                    environment,
                    COMMAND_TIMEOUT_SECONDS,
                    MAX_COMMAND_OUTPUT_BYTES,
                )
                commands.append(
                    _command_record(
                        sequence,
                        target_id,
                        phase,
                        ".",
                        argv,
                        execution,
                        environment_sha,
                    )
                )
                sequence += 1
                if execution.error is not None or execution.returncode != 0:
                    raise KernelBuildError(
                        f"{target_id} {phase} failed with return code "
                        f"{execution.returncode!r} ({execution.error or 'no execution error'})"
                    )
                _require_tool_hashes(tool, make_sha256, toolchain_identities)
                _require_same_clean_head(root, commit)
                _source_gate(
                    root,
                    commit,
                    evidence_gate_root,
                    f"after {target_id} {phase}",
                )
                if phase == "clean" and target_path.exists():
                    raise KernelBuildError(f"{target_id} clean left a stale kernel artifact")
            _regular_file(target_path, f"{target_id} kernel")
            cost.parse_elf_identity(target_path)
            target_receipts.append(
                {
                    "id": target_id,
                    "path": target_relative,
                    "sha256": _file_sha(target_path),
                    "command_argv": commands[-1]["argv"],
                }
            )

        treatment_id = by_role["treatment"]["id"]
        guardrail_commands = [
            {
                "id": "struct_proc_bytes",
                "target_id": treatment_id,
                "phase": "kernel_budget",
                "argv": [
                    str(tool),
                    f"TOOLPREFIX={recorded_toolprefix}",
                    "kernel-budget-check",
                ],
            },
            {
                "id": "user_stack_call_path_bytes",
                "target_id": treatment_id,
                "phase": "user_stack",
                "argv": [
                    str(tool),
                    f"TOOLPREFIX={recorded_toolprefix}",
                    "user-stack-check",
                ],
            },
        ]
        for guardrail in guardrail_commands:
            execution = runner(
                guardrail["argv"],
                root,
                environment,
                COMMAND_TIMEOUT_SECONDS,
                MAX_COMMAND_OUTPUT_BYTES,
            )
            commands.append(
                _command_record(
                    sequence,
                    guardrail["target_id"],
                    guardrail["phase"],
                    ".",
                    guardrail["argv"],
                    execution,
                    environment_sha,
                )
            )
            sequence += 1
            if execution.error is not None or execution.returncode != 0:
                raise KernelBuildError(
                    f"{guardrail['id']} check failed with return code "
                    f"{execution.returncode!r} "
                    f"({execution.error or 'no execution error'})"
                )
            _require_tool_hashes(tool, make_sha256, toolchain_identities)
            _require_same_clean_head(root, commit)
            _source_gate(
                root,
                commit,
                evidence_gate_root,
                f"after {guardrail['id']}",
            )

        _require_same_clean_head(root, commit)
        for target in target_receipts:
            final_path, _ = _inside(
                root, Path(*PurePosixPath(target["path"]).parts),
                f"final {target['id']} kernel",
            )
            _regular_file(final_path, f"final {target['id']} kernel")
            cost.parse_elf_identity(final_path)
            if _file_sha(final_path) != target["sha256"]:
                raise KernelBuildError(
                    f"{target['id']} kernel changed after its measured build"
                )

        git_version = _git(root, ["--version"], 4096).decode(
            "utf-8", errors="strict"
        ).strip()
        facts = {
            "build_environment_sha256": environment_sha,
            "builder": f"evaluation_kernel_build.py/{BUILDER_VERSION}",
            "git": git_version,
            "make": make_version,
            "make_path": str(tool),
            "make_sha256": make_sha256,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "source_date_epoch": commit_epoch,
            "toolchain_identity_sha256": toolchain_sha,
            "toolchain_prefix": recorded_toolprefix,
        }
        environment_manifest = {
            "schema_version": 1,
            "kind": cost.ENVIRONMENT_KIND,
            "run_id": run_id,
            "source_commit": commit,
            "environment_id": f"kernel-build-{environment_sha[:20]}",
            "facts": [
                {"name": name, "value": value} for name, value in sorted(facts.items())
            ],
        }
        environment_raw = _canonical_json(environment_manifest)
        build_config = {
            "schema_version": BUILDER_VERSION,
            "kind": BUILD_CONFIG_KIND,
            "run_id": run_id,
            "source_commit": commit,
            "kernel_cost_config": {
                "path": config_relative,
                "sha256": _file_sha(config_absolute),
            },
            "make_tool": {
                "path": str(tool),
                "sha256": make_sha256,
                "version_argv": make_version_argv,
                "version": make_version,
            },
            "toolchain": {
                "prefix": recorded_toolprefix,
                "identity_sha256": toolchain_sha,
                "tools": toolchain_records,
            },
            "command_policy": {
                "timeout_seconds": COMMAND_TIMEOUT_SECONDS,
                "max_output_bytes": MAX_COMMAND_OUTPUT_BYTES,
            },
            "environment": environment,
            "environment_sha256": environment_sha,
            "targets": [
                {
                    "id": target["id"],
                    "role": target["role"],
                    "path": target["required_relative_path"],
                    "clean_argv": (
                        [
                            str(tool), "-C", "baseline_ucore",
                            f"TOOLPREFIX={recorded_toolprefix}", "clean",
                        ]
                        if target["role"] == "baseline"
                        else [str(tool), f"TOOLPREFIX={recorded_toolprefix}", "clean"]
                    ),
                    "build_argv": (
                        [
                            str(tool), "-C", "baseline_ucore",
                            f"TOOLPREFIX={recorded_toolprefix}", "build/kernel",
                        ]
                        if target["role"] == "baseline"
                        else [
                            str(tool), f"TOOLPREFIX={recorded_toolprefix}",
                            "build/kernel",
                        ]
                    ),
                }
                for target in config["targets"]
            ],
            "guardrail_commands": guardrail_commands,
        }
        build_config_raw = _canonical_json(build_config)
        build_log = {
            "schema_version": BUILDER_VERSION,
            "kind": BUILD_LOG_KIND,
            "run_id": run_id,
            "source_commit": commit,
            "environment_sha256": environment_sha,
            "toolchain_sha256": toolchain_sha,
            "commands": commands,
        }
        build_log_raw = _canonical_json(build_log)

        environment_relative = f"{portable_output}/environment.json"
        build_config_relative = f"{portable_output}/kernel-build-config.json"
        build_log_relative = f"{portable_output}/raw/kernel-build.log"
        manifest = {
            "schema_version": 1,
            "kind": cost.BUILD_KIND,
            "run_id": run_id,
            "source_commit": commit,
            "environment_sha256": _bytes_sha(environment_raw),
            "toolchain_sha256": toolchain_sha,
            "build_config": _receipt(build_config_relative, build_config_raw),
            "build_log": _receipt(build_log_relative, build_log_raw),
            "targets": target_receipts,
        }
        manifest_raw = _canonical_json(manifest)

        # Validate the same public schema used by portable verifiers before
        # publishing any sidecar. This prevents producer/consumer drift.
        cost.validate_trusted_build_config(
            build_config, config, cost._bytes_sha(config_raw)
        )
        cost.validate_trusted_build_log(build_log, build_config, manifest)
        cost.validate_trusted_build_environment(environment_manifest, build_config)

        _ensure_directory(output_absolute.parent, "output directory parent")
        stage = Path(
            tempfile.mkdtemp(prefix=f".{output_absolute.name}.tmp-", dir=output_absolute.parent)
        )
        try:
            _write(stage / "environment.json", environment_raw)
            _write(stage / "kernel-build-config.json", build_config_raw)
            _write(stage / "raw" / "kernel-build.log", build_log_raw)
            _write(stage / "kernel-build.json", manifest_raw)
            if output_absolute.exists():
                raise KernelBuildError("output directory appeared during publication")
            os.replace(stage, output_absolute)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise

        _source_gate(root, commit, evidence_gate_root, "after evidence publication")

    return {
        "source_commit": commit,
        "output_dir": str(output_absolute),
        "environment_manifest": environment_relative,
        "build_manifest": f"{portable_output}/kernel-build.json",
        "toolchain_sha256": toolchain_sha,
        "targets": target_receipts,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build trusted, same-commit kernel cost evidence."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--config", required=True, type=Path)
    build.add_argument("--repository-root", required=True, type=Path)
    build.add_argument("--make-tool", required=True, type=Path)
    build.add_argument(
        "--toolprefix",
        required=True,
        help="absolute RISC-V prefix, for example /usr/bin/riscv64-linux-gnu-",
    )
    build.add_argument("--run-id", required=True)
    build.add_argument("--output-dir", required=True, type=Path)
    build.add_argument("--evidence-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_evidence(
            config_path=args.config,
            repository_root=args.repository_root,
            make_tool=args.make_tool,
            toolprefix=args.toolprefix,
            run_id=args.run_id,
            output_dir=args.output_dir,
            evidence_root=args.evidence_root,
        )
    except (KernelBuildError, cost.KernelCostError, OSError, UnicodeError) as error:
        print(f"trusted kernel build error: {error}", file=sys.stderr)
        return 2
    print(
        f"trusted kernel build: {result['source_commit']} -> {result['output_dir']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
