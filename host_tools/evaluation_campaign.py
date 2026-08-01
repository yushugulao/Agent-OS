#!/usr/bin/env python3
"""Create and verify provenance for independent AgentOS evaluation boots.

This module deliberately does not calculate benchmark results.  Its only job is
to bind the collection campaign to a clean commit, the executable environment,
the exact runner command, and immutable raw logs.  Statistical interpretation
belongs to ``evaluation_contract.py``.
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
            _entry_sys.base_prefix,
            _entry_sys.base_exec_prefix,
            _entry_sys.prefix,
            _entry_sys.exec_prefix,
        )
        if value
    }
    _entry_sys.path[:] = [
        value
        for value in _entry_sys.path
        if value
        and any(
            (normalized := value.replace("\\", "/").rstrip("/").casefold())
            == prefix
            or normalized.startswith(f"{prefix}/")
            for prefix in prefixes
        )
    ]


_isolate_direct_entry_imports()

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Iterable

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
    from .evidence_delivery_contract import (
        DeliveryContractError,
        controlled_git_environment,
        tracked_worktree_identity,
    )
except ImportError:
    from evidence_delivery_contract import (
        DeliveryContractError,
        controlled_git_environment,
        tracked_worktree_identity,
    )

try:
    from .evidence_toolchain_attestation import (
        EVALUATION_ARTIFACT_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_ROOTS,
        EVALUATION_CACHE_OUTPUT_ROOTS,
        ToolAttestationError,
        purge_evaluation_generated_outputs,
        verify_evaluation_source_tree,
    )
except ImportError:
    from evidence_toolchain_attestation import (
        EVALUATION_ARTIFACT_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_ROOTS,
        EVALUATION_CACHE_OUTPUT_ROOTS,
        ToolAttestationError,
        purge_evaluation_generated_outputs,
        verify_evaluation_source_tree,
    )

try:
    from .safe_host_paths import (
        absolute_lexical_path,
        atomic_write_bytes,
        ensure_safe_directory,
        path_is_link,
        read_regular_file,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
    )
except ImportError:
    from safe_host_paths import (
        absolute_lexical_path,
        atomic_write_bytes,
        ensure_safe_directory,
        path_is_link,
        read_regular_file,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
    )

try:
    from .agenteval_measurement_source_contract import (
        FORMAL_BOOT_COUNT,
        STOP_RULE as MEASUREMENT_STOP_RULE,
        build_measurement_source_receipt,
        validate_measurement_source_receipt_shape,
        verify_measurement_source_files,
    )
except ImportError:
    from agenteval_measurement_source_contract import (
        FORMAL_BOOT_COUNT,
        STOP_RULE as MEASUREMENT_STOP_RULE,
        build_measurement_source_receipt,
        validate_measurement_source_receipt_shape,
        verify_measurement_source_files,
    )


KIND = "agentos-evaluation-campaign"
SCENARIO_KIND = "agentos-evaluation-scenario-campaign"
SCHEMA_VERSION = 6
SCENARIO_SCHEMA_VERSION = 5
FORMAL_MICRO_TIMEOUT_SECONDS = 900
FORMAL_INNER_PAIR_COUNT = 7
PREFLIGHT_RECEIPT_KIND = "agentos-evaluation-preflight-receipt"
PREFLIGHT_RECEIPT_SCHEMA_VERSION = 1
PREFLIGHT_RECEIPT_MAX_BYTES = 4096
MINIMUM_BOOTS = FORMAL_BOOT_COUNT
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BOOT_ID_RE = re.compile(r"^boot-[0-9]{2,4}$")
SAMPLE_IDENTIFIER_PATTERN = r"[a-z][a-z0-9_-]{0,63}"
SAMPLE_RE = re.compile(
    rf"^agenteval_ucore: sample schema=2 "
    rf"experiment={SAMPLE_IDENTIFIER_PATTERN} load=[0-9]+ "
    rf"pair=([1-7]) variant={SAMPLE_IDENTIFIER_PATTERN} "
    rf"order=(AB|BA) .* status=measured$"
)
CHALLENGE_RE = re.compile(r"^[0-9a-f]{16}$")
SCENARIO_CHALLENGE_RE = re.compile(r"^ch-[0-9]{12}$")
CAMPAIGN_LOCK_TOKEN_ENV = "AGENTOS_EVALUATION_CAMPAIGN_TOKEN"
CAMPAIGN_LOCK_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
SCENARIO_TARGET_COUNT = 2
SCENARIO_COORDINATION_ALLOWANCE_SECONDS = 60
SCENARIO_CLEAN_HOME = "/tmp"
SCENARIO_CLEAN_PATH = (
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)
SCENARIO_CLEAN_ENVIRONMENT_KEYS = (
    "CC",
    "HOME",
    "HOSTCC",
    "HOST_CC",
    "LANG",
    "LC_ALL",
    "MAKE_TOOL",
    "PATH",
    "QEMU",
    "SHELL",
    "SYSTEMDRIVE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TOOLPREFIX",
    "TZ",
)


class CampaignError(ValueError):
    """Raised when campaign provenance is incomplete or inconsistent."""


class CampaignBusy(CampaignError):
    """Raised when another formal evaluation owns the campaign lock."""


class ScenarioBusy(CampaignError):
    """Raised when another collector owns the scenario manifest lock."""


def _derive_micro_challenge(
    source_commit: str, boot_number: int, occupied: set[str] | None = None
) -> str:
    """Derive one parity-balanced challenge from the public source identity."""

    if COMMIT_RE.fullmatch(source_commit) is None or boot_number < 1:
        raise CampaignError("micro challenge identity is invalid")
    occupied = occupied or set()
    counter = 0
    while True:
        material = (
            f"agentos-evaluation-micro-challenge-v1\0{source_commit}\0"
            f"{boot_number}\0{counter}"
        ).encode("ascii")
        numeric = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
        numeric = (numeric & ~1) | (boot_number & 1)
        if numeric == 0:
            numeric = 2
        challenge = f"{numeric:016x}"
        if challenge not in occupied:
            return challenge
        counter += 1


def _derive_scenario_challenge(
    source_commit: str, boot_number: int, occupied: set[str] | None = None
) -> str:
    """Derive one canonical scenario challenge from the public source identity."""

    if COMMIT_RE.fullmatch(source_commit) is None or boot_number < 1:
        raise CampaignError("scenario challenge identity is invalid")
    occupied = occupied or set()
    counter = 0
    while True:
        material = (
            f"agentos-evaluation-scenario-challenge-v1\0{source_commit}\0"
            f"{boot_number}\0{counter}"
        ).encode("ascii")
        numeric = (
            int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
            % (10**12 - 1)
        ) + 1
        challenge = f"ch-{numeric:012d}"
        if challenge not in occupied:
            return challenge
        counter += 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _command_output(error: subprocess.SubprocessError) -> str:
    output = getattr(error, "stdout", "") or getattr(error, "output", "") or ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    return str(output).replace("\x00", "").strip()[-4000:]


def _run(
    argv: list[str],
    cwd: Path,
    *,
    timeout_seconds: int = 30,
    environment: dict[str, str] | None = None,
) -> str:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        raise CampaignError(
            f"command timed out after {timeout_seconds}s: {argv!r}; "
            f"output={_command_output(error)!r}"
        ) from error
    except subprocess.CalledProcessError as error:
        raise CampaignError(
            f"command failed with exit {error.returncode}: {argv!r}; "
            f"output={_command_output(error)!r}"
        ) from error
    except OSError as error:
        raise CampaignError(f"command could not start: {argv!r}: {error}") from error
    return result.stdout.replace("\x00", "")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_file(path: Path, label: str, *, nonempty: bool = True) -> None:
    try:
        require_regular_file(path, nonempty=nonempty)
    except (OSError, ValueError) as error:
        raise CampaignError(f"{label} is not a safe regular file: {path}") from error


def _require_safe_directory(path: Path, label: str) -> Path:
    try:
        return require_safe_directory(path)
    except (OSError, ValueError) as error:
        raise CampaignError(f"{label} is not a safe directory: {path}") from error


def _ensure_safe_directory(path: Path, label: str) -> Path:
    try:
        return ensure_safe_directory(path)
    except (OSError, ValueError) as error:
        raise CampaignError(f"{label} is not a safe directory: {path}") from error


def _reject_link_components(path: Path, label: str) -> Path:
    try:
        return reject_link_components(path)
    except (OSError, ValueError) as error:
        raise CampaignError(f"{label} is link-backed: {path}") from error


def _resolved_safe_directory(path: Path, label: str) -> Path:
    return _require_safe_directory(absolute_lexical_path(path), label).resolve(strict=True)


def _resolved_safe_file(path: Path, label: str, *, nonempty: bool = True) -> Path:
    _require_regular_file(absolute_lexical_path(path), label, nonempty=nonempty)
    return absolute_lexical_path(path).resolve(strict=True)


def _safe_output_path(path: Path, label: str) -> Path:
    absolute = absolute_lexical_path(path)
    _reject_link_components(absolute, label)
    _ensure_safe_directory(absolute.parent, f"{label} parent")
    if path_is_link(absolute):
        raise CampaignError(f"{label} is link-backed: {absolute}")
    return absolute


def _resolved_link_free_path(path: Path, label: str) -> Path:
    return _reject_link_components(
        absolute_lexical_path(path), label
    ).resolve(strict=False)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    try:
        atomic_write_bytes(
            path,
            (
                json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)
                + "\n"
            ).encode("utf-8"),
        )
    except (OSError, ValueError) as error:
        raise CampaignError(f"manifest output is unsafe: {path}") from error


def _atomic_copy(source: Path, destination: Path, label: str) -> None:
    _require_regular_file(source, label)
    parent = _ensure_safe_directory(destination.parent, f"{label} archive parent")
    if path_is_link(destination):
        raise CampaignError(f"{label} archive path must not be link-backed: {destination}")
    temporary = destination.with_name(f".{destination.name}.tmp.{os.getpid()}")
    try:
        shutil.copyfile(source, temporary)
        _reject_link_components(parent, f"{label} archive parent")
        if path_is_link(destination):
            raise CampaignError(f"{label} archive path became link-backed: {destination}")
        os.replace(temporary, destination)
    except OSError as error:
        raise CampaignError(f"failed to archive {label}: {error}") from error
    finally:
        if temporary.exists():
            temporary.unlink()


def _campaign_lock_path(repo: Path) -> Path:
    """Use one lock name per Git repository, distinct from the per-boot lock."""

    from plain_ucore_action_runner import _run_lock_path

    return _run_lock_path(_require_safe_directory(repo, "repository")).with_name(
        ".agentos-evaluation-campaign.lock"
    )


def _campaign_lease_path(repo: Path) -> Path:
    return _campaign_lock_path(repo).with_name(
        ".agentos-evaluation-campaign.lease"
    )


def _scenario_coordination_lock_path(repo: Path) -> Path:
    """Keep manifest coordination distinct from destructive target locks."""

    from plain_ucore_action_runner import _run_lock_path

    return _run_lock_path(_require_safe_directory(repo, "repository")).with_name(
        ".agentos-evaluation-scenario.lock"
    )


@contextmanager
def exclusive_evaluation_campaign_lock(repo: Path):
    """Fail closed when a complete formal campaign is already in progress."""

    from plain_ucore_action_runner import _try_lock_file, _unlock_file

    lock_path = _campaign_lock_path(repo)
    _ensure_safe_directory(lock_path.parent, "campaign lock parent")
    if path_is_link(lock_path):
        raise CampaignError(f"campaign lock is link-backed: {lock_path}")
    with lock_path.open("a+b") as handle:
        try:
            _try_lock_file(handle)
        except OSError as error:
            raise CampaignBusy(
                f"another formal evaluation campaign is running: {repo.resolve()}"
            ) from error
        try:
            yield
        finally:
            _unlock_file(handle)


@contextmanager
def exclusive_scenario_coordination_lock(repo: Path):
    """Serialize scenario state without holding a lock needed by its child."""

    from plain_ucore_action_runner import _try_lock_file, _unlock_file

    lock_path = _scenario_coordination_lock_path(repo)
    _ensure_safe_directory(lock_path.parent, "scenario lock parent")
    if path_is_link(lock_path):
        raise CampaignError(f"scenario lock is link-backed: {lock_path}")
    with lock_path.open("a+b") as handle:
        try:
            _try_lock_file(handle)
        except OSError as error:
            raise ScenarioBusy(
                f"another scenario collector is running: {repo.resolve()}"
            ) from error
        try:
            yield
        finally:
            _unlock_file(handle)


def execute_under_campaign_lock(*, repo: Path, command: list[str]) -> int:
    """Execute the complete collection script while holding the named lock."""

    if not command or command[0] == "--":
        command = command[1:]
    if not command:
        raise CampaignError("campaign lock command must not be empty")
    with exclusive_evaluation_campaign_lock(repo):
        token = secrets.token_hex(32)
        lease_path = _campaign_lease_path(repo)
        _reject_link_components(lease_path.parent, "campaign lease parent")
        if lease_path.exists() or path_is_link(lease_path):
            raise CampaignError(
                f"stale or unsafe campaign lease must be removed: {lease_path}"
            )
        try:
            descriptor = os.open(
                lease_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except OSError as error:
            raise CampaignError(f"campaign lease cannot be created: {error}") from error
        try:
            with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as handle:
                handle.write(token + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            environment = os.environ.copy()
            environment[CAMPAIGN_LOCK_TOKEN_ENV] = token
            try:
                result = subprocess.run(
                    command,
                    cwd=repo.resolve(),
                    check=False,
                    env=environment,
                )
            except OSError as error:
                raise CampaignError(
                    f"campaign command could not start: {error}"
                ) from error
        finally:
            try:
                lease_path.unlink()
            except FileNotFoundError:
                pass
    return int(result.returncode)


def verify_campaign_lock_lease(*, repo: Path, token: str) -> None:
    """Prove that this child was launched by the process holding the lock."""

    if CAMPAIGN_LOCK_TOKEN_RE.fullmatch(token) is None:
        raise CampaignError("campaign lock token is missing or malformed")
    lease_path = _campaign_lease_path(repo)
    _require_regular_file(lease_path, "campaign lock lease")
    try:
        supplied = lease_path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as error:
        raise CampaignError(f"campaign lock lease is unreadable: {error}") from error
    if supplied != token + "\n":
        raise CampaignError("campaign lock lease token differs")
    try:
        with exclusive_evaluation_campaign_lock(repo):
            pass
    except CampaignBusy:
        pass
    else:
        raise CampaignError("campaign lock lease exists without a held lock")
    _require_regular_file(lease_path, "campaign lock lease after verification")
    try:
        final_value = lease_path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as error:
        raise CampaignError(f"campaign lock lease cannot be rechecked: {error}") from error
    if final_value != token + "\n":
        raise CampaignError("campaign lock lease changed during verification")


def _strict_json(path: Path) -> dict[str, Any]:
    _require_regular_file(path, "campaign manifest")

    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CampaignError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise CampaignError(f"non-finite JSON number: {value}")

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CampaignError(f"invalid campaign JSON: {error}") from error
    if not isinstance(value, dict):
        raise CampaignError("campaign manifest must be a JSON object")
    return value


def _expect_keys(value: dict[str, Any], keys: Iterable[str], label: str) -> None:
    expected = set(keys)
    observed = set(value)
    if observed != expected:
        raise CampaignError(
            f"{label} keys differ: missing={sorted(expected - observed)} "
            f"extra={sorted(observed - expected)}"
        )


def _repo_relative(repo: Path, path: Path) -> str:
    repository = _require_safe_directory(repo, "repository")
    candidate = _reject_link_components(path, "repository path")
    try:
        relative = candidate.resolve(strict=False).relative_to(
            repository.resolve(strict=True)
        )
    except ValueError as error:
        raise CampaignError(f"path escapes repository: {path}") from error
    if not relative.parts:
        raise CampaignError("path must not be the repository root")
    return relative.as_posix()


def _is_portable_absolute_path(value: object) -> bool:
    """Recognize canonical POSIX or Windows absolute paths on any Host OS."""

    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        return False
    if value.startswith("/"):
        if "\\" in value:
            return False
        parts = value.split("/")[1:]
        return bool(parts) and all(part not in {"", ".", ".."} for part in parts)
    if re.match(r"^[A-Za-z]:[\\/]", value):
        separator = value[2]
        other = "/" if separator == "\\" else "\\"
        if other in value[3:]:
            return False
        parts = value[3:].split(separator)
        return bool(parts) and all(part not in {"", ".", ".."} for part in parts)
    if value.startswith("\\\\"):
        if "/" in value:
            return False
        parts = value[2:].split("\\")
        return len(parts) >= 3 and all(part not in {"", ".", ".."} for part in parts)
    return False


def _portable_name(value: str) -> str:
    pure = PurePosixPath(value) if value.startswith("/") else PureWindowsPath(value)
    return pure.name


def _absolute_toolprefix(compiler_path: str) -> tuple[str, str]:
    if compiler_path.endswith("gcc.exe"):
        return compiler_path[:-7], ".exe"
    if compiler_path.endswith("gcc"):
        return compiler_path[:-3], ""
    raise CampaignError("compiler path cannot derive an absolute TOOLPREFIX")


def _artifact_root(value: object) -> PurePosixPath:
    """Parse the repository-relative run root carried by a campaign manifest."""
    if not isinstance(value, str):
        raise CampaignError("artifact root must be a canonical relative path")
    root = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value) is not None
        or root.is_absolute()
        or not root.parts
        or any(part in {"", ".", ".."} for part in root.parts)
    ):
        raise CampaignError("artifact root must be a canonical relative path")
    return root


def _require_manifest_artifact_root(
    repo: Path,
    manifest_path: Path,
    artifact_root: object,
    *,
    scenario: bool = False,
) -> None:
    actual_parent = PurePosixPath(_repo_relative(repo, manifest_path)).parent
    expected_parent = _artifact_root(artifact_root)
    if scenario:
        expected_parent /= "scenario"
    if actual_parent != expected_parent:
        raise CampaignError("manifest parent differs from its bound artifact root")


def repository_identity(repo: Path) -> tuple[str, bool]:
    repo = _require_safe_directory(repo, "repository")
    git_name = shutil.which("git")
    if git_name is None:
        raise CampaignError("Git executable is unavailable")
    git = _resolved_safe_file(Path(git_name), "Git executable")
    environment = controlled_git_environment()
    root_text = _run(
        [str(git), "-c", "core.fsmonitor=false", "-c",
         "core.untrackedCache=false", "rev-parse", "--show-toplevel"],
        repo,
        environment=environment,
    ).strip()
    observed = _require_safe_directory(Path(root_text), "Git repository root")
    try:
        same_repository = os.path.samefile(observed, repo)
    except OSError:
        same_repository = False
    if not same_repository:
        raise CampaignError(f"repository root differs from --repo: {root_text}")
    commit = _run(
        [str(git), "-c", "core.fsmonitor=false", "-c",
         "core.untrackedCache=false", "rev-parse", "HEAD"],
        repo, environment=environment
    ).strip()
    if not COMMIT_RE.fullmatch(commit):
        raise CampaignError(f"invalid Git commit identity: {commit!r}")
    try:
        tracked_clean, _tracked_digest = tracked_worktree_identity(
            str(git), repo
        )
    except DeliveryContractError as error:
        raise CampaignError(f"tracked source identity is unsafe: {error}") from error
    status = _run(
        [str(git), "-c", "core.fsmonitor=false", "-c",
         "core.untrackedCache=false", "status", "--porcelain=v1",
         "--untracked-files=all"],
        repo,
        environment=environment,
    )
    return commit, status == "" and tracked_clean


def _require_repository_identity(repo: Path, expected_commit: str, stage: str) -> None:
    commit, clean = repository_identity(repo)
    if commit != expected_commit or not clean:
        raise CampaignError(
            f"source identity changed {stage}: expected clean {expected_commit}, "
            f"observed commit={commit} clean={clean}"
        )


def _evaluation_output_roots(artifact_root: object) -> tuple[str, ...]:
    root = _artifact_root(artifact_root)
    return (
        *EVALUATION_BUILD_OUTPUT_ROOTS,
        *EVALUATION_CACHE_OUTPUT_ROOTS,
        root.as_posix(),
    )


def _evaluation_output_files(artifact_root: object) -> tuple[str, ...]:
    _artifact_root(artifact_root)
    return (*EVALUATION_BUILD_OUTPUT_FILES, *EVALUATION_ARTIFACT_OUTPUT_FILES)


def _evaluation_source_gate(
    repo: Path, expected_commit: str, artifact_root: object, stage: str
) -> None:
    git_name = shutil.which("git")
    if git_name is None:
        raise CampaignError("Git executable is unavailable")
    git = _resolved_safe_file(Path(git_name), "Git executable")
    try:
        verify_evaluation_source_tree(
            git,
            repo,
            repo,
            expected_commit,
            controlled_git_environment(),
            allowed_output_roots=_evaluation_output_roots(artifact_root),
            allowed_output_files=_evaluation_output_files(artifact_root),
            stage=stage,
        )
    except (OSError, ToolAttestationError) as error:
        raise CampaignError(f"evaluation source gate failed {stage}: {error}") from error


def _prepare_evaluation_build_tree(
    repo: Path, expected_commit: str, artifact_root: object, stage: str
) -> None:
    _evaluation_source_gate(repo, expected_commit, artifact_root, f"before {stage} purge")
    git_name = shutil.which("git")
    if git_name is None:
        raise CampaignError("Git executable is unavailable")
    git = _resolved_safe_file(Path(git_name), "Git executable")
    purge_roots = (*EVALUATION_BUILD_OUTPUT_ROOTS, *EVALUATION_CACHE_OUTPUT_ROOTS)
    try:
        purge_evaluation_generated_outputs(
            git,
            repo,
            repo,
            expected_commit,
            controlled_git_environment(),
            output_roots=purge_roots,
            output_files=EVALUATION_BUILD_OUTPUT_FILES,
        )
    except (OSError, ToolAttestationError) as error:
        raise CampaignError(f"evaluation build purge failed {stage}: {error}") from error
    _evaluation_source_gate(repo, expected_commit, artifact_root, f"after {stage} purge")


def _build_measurement_receipt(repo: Path, commit: str) -> dict[str, Any]:
    try:
        return build_measurement_source_receipt(repo, source_commit=commit)
    except (OSError, UnicodeError, ValueError) as error:
        raise CampaignError(f"measurement timing source contract failed: {error}") from error


def _require_measurement_receipt(
    repo: Path, commit: str, receipt: object, stage: str
) -> None:
    try:
        verify_measurement_source_files(
            receipt, repo, expected_commit=commit
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise CampaignError(
            f"measurement timing sources changed {stage}: {error}"
        ) from error


class _SourceIntegrityMonitor:
    """Check source identity at the two timing-external boot boundaries."""

    def __init__(
        self, repo: Path, expected_commit: str, measurement_receipt: object,
        artifact_root: object,
    ) -> None:
        self.repo = repo
        self.expected_commit = expected_commit
        self.measurement_receipt = measurement_receipt
        self.artifact_root = artifact_root
    def __enter__(self) -> "_SourceIntegrityMonitor":
        _require_repository_identity(self.repo, self.expected_commit, "before boot")
        _evaluation_source_gate(
            self.repo, self.expected_commit, self.artifact_root, "before boot"
        )
        _require_measurement_receipt(
            self.repo, self.expected_commit, self.measurement_receipt,
            "before boot",
        )
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        _require_repository_identity(self.repo, self.expected_commit, "after boot")
        _evaluation_source_gate(
            self.repo, self.expected_commit, self.artifact_root, "after boot"
        )
        _require_measurement_receipt(
            self.repo, self.expected_commit, self.measurement_receipt,
            "after boot",
        )


def _trusted_process_path(tools: Iterable[dict[str, str]]) -> str:
    directories: list[str] = []
    flavor: str | None = None
    for tool in tools:
        path = tool["path"]
        current_flavor = "posix" if path.startswith("/") else "windows"
        if flavor is None:
            flavor = current_flavor
        elif flavor != current_flavor:
            raise CampaignError("execution tools mix POSIX and Windows path styles")
        pure = PurePosixPath(path) if flavor == "posix" else PureWindowsPath(path)
        directory = str(pure.parent)
        if directory not in directories:
            directories.append(directory)
    return (":" if flavor == "posix" else ";").join(directories)


def _verify_bound_host_cc_resolution(
    host_cc: dict[str, str], process_path: str
) -> None:
    """Require an unqualified ``cc`` lookup to reach the attested compiler."""

    if host_cc.get("argv0") != "cc":
        raise CampaignError("Host C compiler identity must use the canonical cc name")
    separator = ":" if process_path.startswith("/") else ";"
    invocation: Path | None = None
    for directory in process_path.split(separator):
        for executable in ("cc", "cc.exe"):
            candidate = Path(directory) / executable
            if candidate.exists():
                invocation = candidate
                break
        if invocation is not None:
            break
    if invocation is None:
        raise CampaignError("bound Host C compiler is unavailable through PATH")
    try:
        observed = invocation.resolve(strict=True)
        expected = Path(host_cc["path"]).resolve(strict=True)
        same = os.path.samefile(observed, expected)
    except (KeyError, OSError, RuntimeError) as error:
        raise CampaignError("bound Host C compiler identity cannot be resolved") from error
    if not same:
        raise CampaignError(
            "unqualified cc differs from the attested Host C compiler"
        )


def _micro_boot_environment(
    environment: dict[str, dict[str, str]],
    platform: dict[str, Any],
    challenge: str,
    guest_log: str,
    timeout_seconds: int = FORMAL_MICRO_TIMEOUT_SECONDS,
) -> dict[str, str]:
    compiler_path = environment["compiler"]["path"]
    absolute_prefix, _ = _absolute_toolprefix(compiler_path)
    posix_temporary, native_temporary = _platform_temporary_identity(platform)
    system_drive = _platform_system_drive(platform)
    result = {
        "AGENT_EVAL_CHALLENGE_HEX": challenge,
        "AGENT_TEST_CASE": "agenteval_ucore",
        "AGENT_TEST_GUEST_LOG_FILE": guest_log,
        "CASE_TIMEOUT": f"{timeout_seconds}s",
        "CC": environment["host_cc"]["path"],
        "CHAPTER": "agent_eval",
        "HOSTCC": environment["host_cc"]["path"],
        "HOST_CC": environment["host_cc"]["path"],
        "LANG": "C",
        "LC_ALL": "C",
        "MAKE_TOOL": environment["make"]["path"],
        "PATH": _trusted_process_path(environment.values()),
        "PYTHONHASHSEED": "0",
        "PYTHON_BIN": environment["python"]["path"],
        "QEMU": environment["qemu"]["path"],
        "TEMP": native_temporary,
        "TMP": native_temporary,
        "TMPDIR": posix_temporary,
        "TOOLPREFIX": absolute_prefix,
    }
    if system_drive is not None:
        result["SYSTEMDRIVE"] = system_drive
    return result


def _verify_platform_execution_binding(
    repo: Path, platform_proof: dict[str, Any]
) -> None:
    """Revalidate the public platform proof in the current execution domain."""

    if platform_proof["repository"]["execution_path"] != str(repo.resolve()):
        raise CampaignError("platform repository differs before boot")
    try:
        from evaluation_platform import (
            PlatformPreflightError,
            _verify_bound_msys2_preflight,
            _verify_hardware_identity,
        )
    except ImportError:  # pragma: no cover
        from .evaluation_platform import (  # type: ignore[no-redef]
            PlatformPreflightError,
            _verify_bound_msys2_preflight,
            _verify_hardware_identity,
        )
    if platform_proof["domain"] == "native-msys2":
        try:
            _verify_bound_msys2_preflight(platform_proof, repo=repo)
        except PlatformPreflightError as error:
            raise CampaignError(f"MSYS2 platform binding changed before boot: {error}") from error
    else:
        try:
            _verify_hardware_identity(platform_proof.get("hardware"))
        except PlatformPreflightError as error:
            raise CampaignError(
                f"platform hardware binding changed before boot: {error}"
            ) from error
        for label, tool in platform_proof["tools"].items():
            executable = Path(tool["path"])
            _require_regular_file(executable, f"platform executable {label}")
            if _sha256(executable) != tool["sha256"]:
                raise CampaignError(f"platform executable changed before boot: {label}")


def _verify_native_execution_binding(
    repo: Path, campaign: dict[str, Any], boot: dict[str, Any]
) -> None:
    _verify_platform_execution_binding(repo, campaign["platform"])
    for label, tool in campaign["environment"].items():
        executable = Path(tool["path"])
        if not executable.is_absolute():
            raise CampaignError(f"environment executable is not absolute: {label}")
        _require_regular_file(executable, f"environment executable {label}")
        if _sha256(executable) != tool["sha256"]:
            raise CampaignError(f"environment executable changed before boot: {label}")
    if boot["command_argv"][0] != campaign["environment"]["bash"]["path"]:
        raise CampaignError("micro command does not use the preflighted bash executable")
    expected_script = str(
        _resolved_link_free_path(
            repo / "scripts" / "run-agent-tests.sh", "micro runner script"
        )
    )
    if boot["command_argv"][1] != expected_script:
        raise CampaignError("micro runner script differs from the committed source")
    _verify_bound_host_cc_resolution(
        campaign["environment"]["host_cc"],
        boot["command_environment"]["PATH"],
    )


def probe_executable(name: str, version_args: list[str], repo: Path) -> dict[str, str]:
    path = shutil.which(name)
    if path is None:
        raise CampaignError(f"required executable is unavailable: {name}")
    resolved = _resolved_safe_file(Path(path), f"executable {name}")
    output = _run([str(resolved), *version_args], repo)
    first_line = next((line.strip() for line in output.splitlines() if line.strip()), "")
    if not first_line:
        raise CampaignError(f"version output is empty: {name}")
    return {
        "argv0": name,
        "path": str(resolved),
        "sha256": _sha256(resolved),
        "version": first_line,
    }


def require_formal_execution_domain(
    repo: Path,
    *,
    toolprefix: str,
    qemu: str,
    python_bin: str,
    shell_bin: str,
) -> dict[str, Any]:
    """Reject native Windows and incomplete POSIX domains before planning."""

    try:
        from evaluation_platform import (
            PlatformPreflightError,
            probe_native_collection_domain,
        )
    except ImportError:  # pragma: no cover - package import is used by callers.
        from .evaluation_platform import (  # type: ignore[no-redef]
            PlatformPreflightError,
            probe_native_collection_domain,
        )
    try:
        preflight = probe_native_collection_domain(
            repo=repo,
            toolprefix=toolprefix,
            qemu=qemu,
            python_bin=python_bin,
            shell_bin=shell_bin,
        )
    except PlatformPreflightError as error:
        raise CampaignError(f"formal execution-domain preflight failed: {error}") from error
    marker = os.environ.get("AGENTOS_EVALUATION_EXECUTION_DOMAIN", "")
    execution_domain = str(preflight.get("domain", ""))
    if marker.startswith("windows-wsl:"):
        distribution = marker.removeprefix("windows-wsl:")
        try:
            kernel_release = Path("/proc/sys/kernel/osrelease").read_text(
                encoding="ascii", errors="strict"
            )
        except (OSError, UnicodeError) as error:
            raise CampaignError("WSL execution-domain kernel identity is unavailable") from error
        if (
            re.fullmatch(r"[A-Za-z0-9._-]{1,64}", distribution) is None
            or os.environ.get("EVALUATION_WSL_DISTRO") != distribution
            or "microsoft" not in kernel_release.casefold()
        ):
            raise CampaignError("WSL execution-domain marker is not kernel-bound")
        execution_domain = marker
    if preflight.get("domain") not in {"native-linux", "native-msys2"}:
        raise CampaignError("formal execution-domain identity is invalid")
    proof = dict(preflight)
    proof["entry_domain"] = execution_domain
    return proof


def create_campaign(
    *,
    repo: Path,
    output: Path,
    run_id: str,
    requested_boots: int,
    toolprefix: str,
    qemu: str,
    python_bin: str,
    shell_bin: str,
    timeout_seconds: int = FORMAL_MICRO_TIMEOUT_SECONDS,
    require_clean: bool = True,
) -> dict[str, Any]:
    repo = _resolved_safe_directory(repo, "repository")
    output = _safe_output_path(output, "campaign manifest")
    if not RUN_ID_RE.fullmatch(run_id):
        raise CampaignError(f"invalid run id: {run_id!r}")
    if type(requested_boots) is not int or requested_boots != FORMAL_BOOT_COUNT:
        raise CampaignError(
            f"formal evaluation requires the fixed {FORMAL_BOOT_COUNT}-boot stopping rule"
        )
    if (
        type(timeout_seconds) is not int
        or timeout_seconds != FORMAL_MICRO_TIMEOUT_SECONDS
    ):
        raise CampaignError(
            "formal evaluation requires the fixed micro boot timeout"
        )
    if output.exists():
        raise CampaignError(f"campaign manifest already exists: {output}")
    platform_proof = require_formal_execution_domain(
        repo,
        toolprefix=toolprefix,
        qemu=qemu,
        python_bin=python_bin,
        shell_bin=shell_bin,
    )
    manifest_rel = _repo_relative(repo, output)
    run_dir_rel = str(Path(manifest_rel).parent.as_posix())
    commit, clean = repository_identity(repo)
    if require_clean and not clean:
        raise CampaignError("formal evaluation requires a clean committed worktree")
    _evaluation_source_gate(repo, commit, run_dir_rel, "before campaign creation")
    measurement_receipt = _build_measurement_receipt(repo, commit)
    suite_path = repo / "ci" / "evaluation-suite.json"
    _require_regular_file(suite_path, "evaluation suite")
    expected_samples_per_boot = _expected_samples_per_boot(suite_path)

    compiler = f"{toolprefix}gcc"
    environment = {
        "bash": probe_executable(shell_bin, ["--version"], repo),
        "compiler": probe_executable(compiler, ["--version"], repo),
        "git": probe_executable("git", ["--version"], repo),
        "host_cc": dict(platform_proof["tools"]["host_cc"]),
        "linker": probe_executable(f"{toolprefix}ld", ["--version"], repo),
        "make": probe_executable("make", ["--version"], repo),
        "objcopy": probe_executable(f"{toolprefix}objcopy", ["--version"], repo),
        "objdump": probe_executable(f"{toolprefix}objdump", ["--version"], repo),
        "python": probe_executable(python_bin, ["--version"], repo),
        "qemu": probe_executable(qemu, ["--version"], repo),
    }
    _verify_bound_host_cc_resolution(
        environment["host_cc"], _trusted_process_path(environment.values())
    )

    boots: list[dict[str, Any]] = []
    challenges: set[str] = set()
    for number in range(1, requested_boots + 1):
        boot_id = f"boot-{number:02d}"
        challenge = _derive_micro_challenge(commit, number, challenges)
        challenges.add(challenge)
        raw_dir = f"{run_dir_rel}/raw/{boot_id}"
        guest_log = f"{raw_dir}/guest.log"
        runner_log = f"{raw_dir}/runner.log"
        command = [
            environment["bash"]["path"],
            str(
                _resolved_link_free_path(
                    repo / "scripts" / "run-agent-tests.sh", "micro runner script"
                )
            ),
            f"AGENT_EVAL_CHALLENGE_HEX={challenge}",
            f"AGENT_TEST_GUEST_LOG_FILE={guest_log}",
        ]
        boots.append(
            {
                "boot_id": boot_id,
                "challenge": challenge,
                "command_argv": command,
                "command_environment": _micro_boot_environment(
                    environment,
                    platform_proof,
                    challenge,
                    guest_log,
                    timeout_seconds,
                ),
                "exit_code": None,
                "finished_at_utc": None,
                "guest_log": guest_log,
                "guest_log_sha256": None,
                "image_final_path": f"{raw_dir}/fs-copy.img",
                "image_final_sha256": None,
                "image_input_path": f"{raw_dir}/fs.img",
                "image_input_sha256": None,
                "kernel_path": f"{raw_dir}/kernel",
                "kernel_sha256": None,
                "observed_sample_orders": [],
                "runner_log": runner_log,
                "runner_log_sha256": None,
                "sample_count": None,
                "status": "planned",
            }
        )

    campaign: dict[str, Any] = {
        "boots": boots,
        "environment": environment,
        "kind": KIND,
        "measurement_source_receipt": measurement_receipt,
        "platform": platform_proof,
        "phase": "collecting",
        "protocol": {
            "fresh_filesystem_per_boot": True,
            "independent_unit": "fresh-qemu-boot",
            "expected_samples_per_boot": expected_samples_per_boot,
            "minimum_boots": MINIMUM_BOOTS,
            "micro_timeout_seconds": timeout_seconds,
            "requested_boots": requested_boots,
            "sample_order_policy": "guest-paired-alternating-ab-ba",
            "suite_path": "ci/evaluation-suite.json",
            "suite_sha256": _sha256(suite_path),
            "target": "agentos-same-kernel-ablation",
        },
        "run": {
            "artifact_root": run_dir_rel,
            "clean_worktree": clean,
            "commit": commit,
            "completed_at_utc": None,
            "execution_domain": platform_proof["entry_domain"],
            "id": run_id,
            "started_at_utc": _utc_now(),
        },
        "schema_version": SCHEMA_VERSION,
    }
    validate_campaign(campaign)
    _atomic_json(output, campaign)
    return campaign


def _expected_samples_per_boot(suite_path: Path) -> int:
    try:
        from evaluation_contract import EvaluationError, load_suite
    except ImportError:  # pragma: no cover
        from .evaluation_contract import EvaluationError, load_suite

    try:
        suite = load_suite(suite_path)
    except (EvaluationError, OSError, ValueError) as error:
        raise CampaignError(f"evaluation suite sample plan is invalid: {error}") from error
    inner_pairs = suite["pairing"]["minimum_inner_pairs"]
    schedule = suite["execution_schedule"]
    if inner_pairs != FORMAL_INNER_PAIR_COUNT:
        raise CampaignError("evaluation suite inner-pair count is invalid")
    count = len(schedule) * inner_pairs * 2
    if type(count) is not int or count <= 0 or count > 100000:
        raise CampaignError("evaluation suite sample count is invalid")
    return count


def _read_sample_orders(
    path: Path, expected_challenge: str, expected_count: int
) -> tuple[int, list[str]]:
    count = 0
    orders: set[str] = set()
    challenges: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if line.startswith("agenteval_ucore: challenge="):
            challenges.append(line.removeprefix("agenteval_ucore: challenge="))
            continue
        if not line.startswith("agenteval_ucore: sample "):
            continue
        match = SAMPLE_RE.fullmatch(line)
        if match is None:
            raise CampaignError(f"malformed evaluation sample marker: {line}")
        pair = int(match.group(1))
        order = match.group(2)
        expected_order = (
            "AB" if (pair & 1) == (int(expected_challenge, 16) & 1) else "BA"
        )
        if order != expected_order:
            raise CampaignError("evaluation sample order differs from challenge parity")
        count += 1
        orders.add(order)
    if type(expected_count) is not int or expected_count <= 0:
        raise CampaignError("evaluation sample count contract is invalid")
    if count != expected_count:
        raise CampaignError(
            f"evaluation boot must contain {expected_count} samples, observed {count}"
        )
    if orders != {"AB", "BA"}:
        raise CampaignError(f"evaluation boot is not order-balanced: {sorted(orders)}")
    if challenges != [expected_challenge]:
        raise CampaignError(
            "guest challenge marker differs from the precommitted boot challenge"
        )
    return count, sorted(orders)


def _record_boot_result(
    *,
    repo: Path,
    manifest_path: Path,
    boot_id: str,
    exit_code: int,
    guest_log: Path,
    runner_log: Path,
    kernel: Path,
    input_image: Path,
    final_image: Path,
) -> dict[str, Any]:
    repo = _resolved_safe_directory(repo, "repository")
    manifest_path = _resolved_safe_file(manifest_path, "campaign manifest")
    campaign = _strict_json(manifest_path)
    validate_campaign(campaign)
    _require_manifest_artifact_root(repo, manifest_path, campaign["run"]["artifact_root"])
    if campaign["phase"] != "collecting":
        raise CampaignError("cannot record a boot after campaign collection is sealed")
    if not BOOT_ID_RE.fullmatch(boot_id):
        raise CampaignError(f"invalid boot id: {boot_id!r}")
    matching = [boot for boot in campaign["boots"] if boot["boot_id"] == boot_id]
    if len(matching) != 1:
        raise CampaignError(f"boot is not uniquely planned: {boot_id}")
    boot = matching[0]
    if boot["status"] != "planned":
        raise CampaignError(f"boot was already recorded: {boot_id}")
    _require_repository_identity(repo, campaign["run"]["commit"], "before archival")
    _evaluation_source_gate(
        repo,
        campaign["run"]["commit"],
        campaign["run"]["artifact_root"],
        "before archival",
    )

    guest_log = _resolved_safe_file(guest_log, "guest log", nonempty=False)
    runner_log = _resolved_safe_file(runner_log, "runner log")
    expected_guest = _resolved_safe_file(
        repo / boot["guest_log"], "planned guest log", nonempty=False
    )
    expected_runner = _resolved_safe_file(repo / boot["runner_log"], "planned runner log")
    if guest_log != expected_guest or runner_log != expected_runner:
        raise CampaignError("recorded log paths differ from the planned command")
    if (
        _repo_relative(repo, kernel) != "build/kernel"
        or _repo_relative(repo, input_image) != "nfs/fs.img"
        or _repo_relative(repo, final_image) != "nfs/fs-copy.img"
    ):
        raise CampaignError("kernel or runtime image does not use the canonical source")
    _require_regular_file(runner_log, "runner log")
    boot["exit_code"] = exit_code
    boot["finished_at_utc"] = _utc_now()
    boot["runner_log_sha256"] = _sha256(runner_log)

    if exit_code == 0:
        _require_regular_file(guest_log, "guest log")
        archived_kernel = repo / boot["kernel_path"]
        archived_input = repo / boot["image_input_path"]
        archived_final = repo / boot["image_final_path"]
        _atomic_copy(kernel, archived_kernel, "kernel image")
        _atomic_copy(input_image, archived_input, "input filesystem image")
        _atomic_copy(final_image, archived_final, "final filesystem image")
        sample_count, orders = _read_sample_orders(
            guest_log,
            boot["challenge"],
            campaign["protocol"]["expected_samples_per_boot"],
        )
        boot["guest_log_sha256"] = _sha256(guest_log)
        boot["kernel_sha256"] = _sha256(archived_kernel)
        boot["image_input_sha256"] = _sha256(archived_input)
        boot["image_final_sha256"] = _sha256(archived_final)
        boot["observed_sample_orders"] = orders
        boot["sample_count"] = sample_count
        _require_repository_identity(
            repo, campaign["run"]["commit"], "after artifact archival"
        )
        _evaluation_source_gate(
            repo,
            campaign["run"]["commit"],
            campaign["run"]["artifact_root"],
            "after artifact archival",
        )
        boot["status"] = "passed"
    else:
        if guest_log.exists():
            _require_regular_file(guest_log, "failed guest log", nonempty=False)
            boot["guest_log_sha256"] = _sha256(guest_log)
        boot["status"] = "failed"

    validate_campaign(campaign)
    _atomic_json(manifest_path, campaign)
    return campaign


def _micro_process_group_options() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _terminate_micro_process(proc: subprocess.Popen[str]) -> None:
    from plain_ucore_action_runner import terminate_process

    terminate_process(proc)


def scenario_pair_deadline_contract(runner_timeout_seconds: int) -> dict[str, Any]:
    """Derive the hard paired-scenario deadline from the target phase contract."""

    from plain_ucore_action_runner import seeded_ucore_deadline_contract

    if (
        type(runner_timeout_seconds) is not int
        or not 60 <= runner_timeout_seconds <= 3600
    ):
        raise CampaignError(
            "scenario runner timeout must be between 60 and 3600 seconds"
        )
    try:
        target = seeded_ucore_deadline_contract(runner_timeout_seconds)
    except ValueError as error:
        raise CampaignError(f"invalid scenario runner timeout: {error}") from error
    target_deadline = target.get("server_deadline_seconds")
    if type(target_deadline) is not int or target_deadline <= 0:
        raise CampaignError("seeded target deadline contract is invalid")
    pair_deadline = (
        SCENARIO_TARGET_COUNT * target_deadline
        + SCENARIO_COORDINATION_ALLOWANCE_SECONDS
    )
    return {
        "contract": "paired-scenario-deadline-v1",
        "runner_timeout_seconds": runner_timeout_seconds,
        "target_count": SCENARIO_TARGET_COUNT,
        "target_deadline": target,
        "coordination_allowance_seconds": SCENARIO_COORDINATION_ALLOWANCE_SECONDS,
        "pair_deadline_seconds": pair_deadline,
    }


def _run_micro_process(
    *,
    command: list[str],
    environment: dict[str, str],
    repo: Path,
    runner_log: Path,
    timeout_seconds: int,
    maximum_timeout_seconds: int = 3600,
    deadline_label: str = "micro boot",
) -> int:
    if (
        type(timeout_seconds) is not int
        or type(maximum_timeout_seconds) is not int
        or not 60 <= timeout_seconds <= maximum_timeout_seconds
    ):
        raise CampaignError(
            f"{deadline_label} timeout must be between 60 and "
            f"{maximum_timeout_seconds} seconds"
        )

    proc = subprocess.Popen(
        command,
        cwd=repo,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_micro_process_group_options(),
    )
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        timed_out = True
        partial = _output_text(error.stdout or error.output)
        _terminate_micro_process(proc)
        try:
            remainder, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired as cleanup_error:
            remainder = _output_text(cleanup_error.stdout or cleanup_error.output)
            if proc.stdout is not None:
                proc.stdout.close()
        # CPython normally returns the complete buffered stream on the second
        # communicate(); test doubles and alternative runtimes may return only
        # the tail, so retain the timeout snapshot when it is longer.
        output = _output_text(remainder)
        if partial and output and not output.startswith(partial):
            output = partial + output
        elif len(partial) > len(output):
            output = partial
    output = _output_text(output).replace("\x00", "")
    if timed_out:
        output += (
            f"\n[evaluation] {deadline_label} exceeded "
            f"{timeout_seconds}s total deadline\n"
        )
    runner_log.write_text(output, encoding="utf-8", newline="\n")
    if output:
        sys.stdout.write(output)
        sys.stdout.flush()
    return 124 if timed_out else int(proc.returncode)


def execute_and_record_boot(
    *,
    repo: Path,
    manifest_path: Path,
    boot_id: str,
    timeout_seconds: int,
) -> int:
    """Run one planned boot and archive its artifacts under the repository lock."""

    from plain_ucore_action_runner import exclusive_repo_run_lock

    repo = _resolved_safe_directory(repo, "repository")
    manifest_path = _resolved_safe_file(manifest_path, "campaign manifest")
    with exclusive_repo_run_lock(repo):
        # This load is intentionally inside the lock: a contender may have
        # completed or failed this same manifest while we were acquiring it.
        campaign = _strict_json(manifest_path)
        validate_campaign(campaign)
        if (
            type(timeout_seconds) is not int
            or timeout_seconds != campaign["protocol"]["micro_timeout_seconds"]
        ):
            raise CampaignError("micro boot timeout differs from the sealed campaign")
        _require_manifest_artifact_root(
            repo, manifest_path, campaign["run"]["artifact_root"]
        )
        check_preflight_receipt(
            manifest_path, manifest_path.parent / "preflight.log"
        )
        if campaign["phase"] != "collecting":
            raise CampaignError("cannot run a boot after campaign collection is sealed")
        matching = [boot for boot in campaign["boots"] if boot["boot_id"] == boot_id]
        if len(matching) != 1 or matching[0]["status"] != "planned":
            raise CampaignError(f"boot is not uniquely planned and pending: {boot_id}")
        boot = matching[0]
        _prepare_evaluation_build_tree(
            repo,
            campaign["run"]["commit"],
            campaign["run"]["artifact_root"],
            f"micro {boot_id}",
        )
        guest_log = repo / boot["guest_log"]
        runner_log = repo / boot["runner_log"]
        runner_log = _safe_output_path(runner_log, "planned runner log")
        guest_log = _safe_output_path(guest_log, "planned guest log")
        try:
            atomic_write_bytes(guest_log, b"")
        except (OSError, ValueError) as error:
            raise CampaignError("planned guest log is unsafe") from error
        try:
            with _SourceIntegrityMonitor(
                repo, campaign["run"]["commit"],
                campaign["measurement_source_receipt"],
                campaign["run"]["artifact_root"],
            ):
                _verify_native_execution_binding(repo, campaign, boot)
                exit_code = _run_micro_process(
                    command=boot["command_argv"],
                    environment=boot["command_environment"],
                    repo=repo,
                    runner_log=runner_log,
                    timeout_seconds=timeout_seconds,
                )
        except OSError as error:
            exit_code = 127
            message = f"evaluation runner launch failed: {error}\n"
            runner_log.write_text(message, encoding="utf-8", newline="\n")
            print(message, end="", file=sys.stderr)
        _verify_native_execution_binding(repo, campaign, boot)
        _record_boot_result(
            repo=repo,
            manifest_path=manifest_path,
            boot_id=boot_id,
            exit_code=exit_code,
            guest_log=guest_log,
            runner_log=runner_log,
            kernel=repo / "build/kernel",
            input_image=repo / "nfs/fs.img",
            final_image=repo / "nfs/fs-copy.img",
        )
    return exit_code


def seal_campaign(manifest_path: Path) -> dict[str, Any]:
    campaign = _strict_json(manifest_path)
    validate_campaign(campaign)
    if campaign["phase"] != "collecting":
        raise CampaignError("campaign is already sealed")
    statuses = [boot["status"] for boot in campaign["boots"]]
    if any(status != "passed" for status in statuses):
        raise CampaignError(f"cannot seal incomplete campaign: {statuses}")
    campaign["phase"] = "collected"
    campaign["run"]["completed_at_utc"] = _utc_now()
    validate_campaign(campaign)
    _atomic_json(manifest_path, campaign)
    return campaign


def export_run_plan(manifest_path: Path, output: Path) -> dict[str, Any]:
    """Export the intentionally small input accepted by evaluation_contract.py."""
    campaign = _strict_json(manifest_path)
    validate_campaign(campaign)
    if campaign["phase"] != "collected":
        raise CampaignError("run plan can only be exported from a collected campaign")
    environment_bytes = json.dumps(
        campaign["environment"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    environment_sha256 = hashlib.sha256(environment_bytes).hexdigest()
    logs = []
    for boot in campaign["boots"]:
        parts = PurePosixPath(boot["guest_log"]).parts
        if (
            len(parts) < 3
            or parts[-3:] != ("raw", boot["boot_id"], "guest.log")
        ):
            raise CampaignError("guest log path is not canonical within the raw root")
        relative = f"{boot['boot_id']}/guest.log"
        command_bytes = json.dumps(
            boot["command_argv"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        logs.append(
            {
                "boot_id": boot["boot_id"],
                "challenge": boot["challenge"],
                "command_argv": boot["command_argv"],
                "command_sha256": hashlib.sha256(command_bytes).hexdigest(),
                "commit": campaign["run"]["commit"],
                "detail": None,
                "image_final_sha256": boot["image_final_sha256"],
                "image_input_sha256": boot["image_input_sha256"],
                "kernel_sha256": boot["kernel_sha256"],
                "path": relative,
                "runner_log_sha256": boot["runner_log_sha256"],
                "sha256": boot["guest_log_sha256"],
                "status": "supported",
            }
        )
    plan = {
        "campaign_sha256": _sha256(manifest_path),
        "environment_sha256": environment_sha256,
        "kind": "agentos-evaluation-run-plan",
        "logs": logs,
        "measurement_source_receipt": json.loads(json.dumps(
            campaign["measurement_source_receipt"]
        )),
        "run_id": campaign["run"]["id"],
        "schema_version": 2,
        "stop_rule": MEASUREMENT_STOP_RULE,
        "suite_sha256": campaign["protocol"]["suite_sha256"],
    }
    if output.exists():
        existing = _strict_json(output)
        if existing != plan:
            raise CampaignError("existing run plan differs from collected campaign")
        return plan
    _atomic_json(output, plan)
    return plan


def _validate_platform_proof(proof: object, execution_domain: str) -> None:
    try:
        from evaluation_platform import (
            SCHEMA_VERSION as PLATFORM_SCHEMA_VERSION,
            KIND as PLATFORM_KIND,
            MSYS_TOOL_LABELS,
            TOOL_LABELS,
            PlatformPreflightError,
            _validate_hardware_identity,
        )
    except ImportError:  # pragma: no cover
        from .evaluation_platform import (  # type: ignore[no-redef]
            SCHEMA_VERSION as PLATFORM_SCHEMA_VERSION,
            KIND as PLATFORM_KIND,
            MSYS_TOOL_LABELS,
            TOOL_LABELS,
            PlatformPreflightError,
            _validate_hardware_identity,
        )
    if not isinstance(proof, dict):
        raise CampaignError("platform proof must be an object")
    common = {
        "distribution", "domain", "entry_domain", "hardware", "kind", "launcher",
        "repository", "schema_version", "status", "toolprefix", "tools",
    }
    domain = proof.get("domain")
    expected = common | (
        {
            "runtime", "temporary_directory", "uname",
            "windows_system_drive", "windows_temporary_directory",
        }
        if domain == "native-msys2"
        else set()
    )
    if set(proof) != expected:
        raise CampaignError("platform proof keys differ from its execution domain")
    if (
        proof.get("schema_version") != PLATFORM_SCHEMA_VERSION
        or proof.get("kind") != PLATFORM_KIND
        or proof.get("status") != "ready"
        or domain not in {"native-linux", "native-msys2"}
        or proof.get("entry_domain") != execution_domain
        or not isinstance(proof.get("toolprefix"), str)
        or not proof["toolprefix"]
    ):
        raise CampaignError("platform proof header is invalid")
    try:
        hardware = _validate_hardware_identity(proof.get("hardware"))
    except PlatformPreflightError as error:
        raise CampaignError(f"platform hardware proof is invalid: {error}") from error
    if hardware != proof.get("hardware"):
        raise CampaignError("platform hardware proof is not canonical")
    repository = proof.get("repository")
    if not isinstance(repository, dict):
        raise CampaignError("platform repository proof is invalid")
    _expect_keys(repository, ["execution_path", "host_path"], "platform repository")
    if not all(
        isinstance(repository[key], str)
        and _is_portable_absolute_path(repository[key])
        for key in repository
    ):
        raise CampaignError("platform repository path is invalid")
    expected_labels = set(MSYS_TOOL_LABELS if domain == "native-msys2" else TOOL_LABELS)
    tools = proof.get("tools")
    if not isinstance(tools, dict) or set(tools) != expected_labels:
        raise CampaignError("platform tool proof is incomplete")
    for label, tool in tools.items():
        if not isinstance(tool, dict):
            raise CampaignError(f"platform tool proof is invalid: {label}")
        _expect_keys(tool, ["argv0", "path", "sha256", "version"], f"platform tool {label}")
        if (
            not all(isinstance(tool[key], str) and tool[key] for key in tool)
            or not _is_portable_absolute_path(tool["path"])
            or re.fullmatch(r"[0-9a-f]{64}", tool["sha256"]) is None
        ):
            raise CampaignError(f"platform tool identity is invalid: {label}")
    if tools["host_cc"]["argv0"] != "cc":
        raise CampaignError("platform Host C compiler is not the canonical cc tool")
    if proof.get("launcher") != tools["bash"]:
        raise CampaignError("platform launcher is not the bound Bash")
    if domain == "native-msys2":
        runtime = proof.get("runtime")
        if not isinstance(runtime, dict):
            raise CampaignError("MSYS2 runtime proof is unavailable")
        _expect_keys(runtime, ["path", "sha256", "version"], "MSYS2 runtime")
        if (
            not _is_portable_absolute_path(runtime.get("path"))
            or re.fullmatch(r"[0-9a-f]{64}", str(runtime.get("sha256", ""))) is None
            or not isinstance(runtime.get("version"), str)
            or not runtime["version"]
            or not _is_portable_absolute_path(proof.get("temporary_directory"))
            or not isinstance(proof.get("windows_temporary_directory"), str)
            or not PureWindowsPath(proof["windows_temporary_directory"]).is_absolute()
            or re.fullmatch(r"[A-Z]:", str(proof.get("windows_system_drive", "")))
            is None
        ):
            raise CampaignError("MSYS2 runtime or temporary namespace proof is invalid")
        uname = proof.get("uname")
        expected_uname = {
            "command", "machine", "release", "system", "version",
            "windows_version",
        }
        if (
            not isinstance(uname, dict)
            or set(uname) != expected_uname
            or any(not isinstance(value, str) or not value for value in uname.values())
            or re.fullmatch(r"MSYS_NT-[0-9]+\.[0-9]+-[0-9]+", uname["system"])
            is None
        ):
            raise CampaignError("MSYS2 uname proof is invalid")


def validate_campaign(campaign: dict[str, Any]) -> None:
    _expect_keys(
        campaign,
        [
            "boots", "environment", "kind", "measurement_source_receipt",
            "phase", "platform", "protocol", "run", "schema_version",
        ],
        "campaign",
    )
    if (
        type(campaign["schema_version"]) is not int
        or campaign["schema_version"] != SCHEMA_VERSION
        or campaign["kind"] != KIND
    ):
        raise CampaignError("unsupported campaign schema")
    if campaign["phase"] not in {"collecting", "collected"}:
        raise CampaignError("invalid campaign phase")

    run = campaign["run"]
    if not isinstance(run, dict):
        raise CampaignError("run must be an object")
    _expect_keys(
        run,
        [
            "artifact_root", "clean_worktree", "commit", "completed_at_utc", "id",
            "execution_domain", "started_at_utc",
        ],
        "run",
    )
    if not RUN_ID_RE.fullmatch(run["id"]) or not COMMIT_RE.fullmatch(run["commit"]):
        raise CampaignError("invalid run identity")
    if not isinstance(run["execution_domain"], str) or (
        run["execution_domain"] not in {"native-linux", "native-msys2"}
        and re.fullmatch(r"windows-wsl:[A-Za-z0-9._-]{1,64}", run["execution_domain"])
        is None
    ):
        raise CampaignError("invalid formal execution-domain identity")
    artifact_root = _artifact_root(run["artifact_root"])
    if artifact_root.name != run["id"]:
        raise CampaignError("artifact root is not bound to the run identity")
    if run["clean_worktree"] is not True:
        raise CampaignError("campaign is not bound to a clean worktree")
    if not isinstance(run["started_at_utc"], str):
        raise CampaignError("run start time is missing")
    if campaign["phase"] == "collected" and not isinstance(
        run["completed_at_utc"], str
    ):
        raise CampaignError("collected campaign lacks completion time")
    if campaign["phase"] == "collecting" and run["completed_at_utc"] is not None:
        raise CampaignError("collecting campaign already has a completion time")
    try:
        validate_measurement_source_receipt_shape(
            campaign["measurement_source_receipt"],
            expected_commit=run["commit"],
        )
    except ValueError as error:
        raise CampaignError(f"invalid measurement source receipt: {error}") from error
    platform = campaign["platform"]
    _validate_platform_proof(platform, run["execution_domain"])

    protocol = campaign["protocol"]
    if not isinstance(protocol, dict):
        raise CampaignError("protocol must be an object")
    _expect_keys(
        protocol,
        [
            "fresh_filesystem_per_boot",
            "independent_unit",
            "expected_samples_per_boot",
            "minimum_boots",
            "micro_timeout_seconds",
            "requested_boots",
            "sample_order_policy",
            "suite_path",
            "suite_sha256",
            "target",
        ],
        "protocol",
    )
    requested = protocol["requested_boots"]
    if (
        type(requested) is not int
        or requested != FORMAL_BOOT_COUNT
        or type(protocol["expected_samples_per_boot"]) is not int
        or protocol["expected_samples_per_boot"] <= 0
        or protocol["expected_samples_per_boot"] > 100000
        or protocol["minimum_boots"] != MINIMUM_BOOTS
        or type(protocol["micro_timeout_seconds"]) is not int
        or protocol["micro_timeout_seconds"] != FORMAL_MICRO_TIMEOUT_SECONDS
        or protocol["fresh_filesystem_per_boot"] is not True
        or protocol["independent_unit"] != "fresh-qemu-boot"
        or protocol["sample_order_policy"] != "guest-paired-alternating-ab-ba"
        or protocol["suite_path"] != "ci/evaluation-suite.json"
        or not isinstance(protocol["suite_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", protocol["suite_sha256"])
        or protocol["target"] != "agentos-same-kernel-ablation"
    ):
        raise CampaignError("invalid campaign protocol")

    environment = campaign["environment"]
    if not isinstance(environment, dict):
        raise CampaignError("environment must be an object")
    _expect_keys(
        environment,
        [
            "bash", "compiler", "git", "host_cc", "linker", "make", "objcopy",
            "objdump", "python", "qemu",
        ],
        "environment",
    )
    for label, tool in environment.items():
        if not isinstance(tool, dict):
            raise CampaignError(f"environment.{label} must be an object")
        _expect_keys(tool, ["argv0", "path", "sha256", "version"], f"environment.{label}")
        if not all(isinstance(tool[key], str) and tool[key] for key in tool):
            raise CampaignError(f"environment.{label} is incomplete")
        if not _is_portable_absolute_path(tool["path"]):
            raise CampaignError(f"environment.{label} path is not absolute")
        if not re.fullmatch(r"[0-9a-f]{64}", tool["sha256"]):
            raise CampaignError(f"environment.{label} executable hash is invalid")
    compiler_path = environment["compiler"]["path"]
    absolute_prefix, executable_suffix = _absolute_toolprefix(compiler_path)
    if (
        environment["host_cc"].get("argv0") != "cc"
        or environment["host_cc"] != campaign["platform"]["tools"]["host_cc"]
    ):
        raise CampaignError("Host C compiler is not bound to the platform proof")
    for label, suffix in (("linker", "ld"), ("objcopy", "objcopy"), ("objdump", "objdump")):
        if environment[label]["path"] != absolute_prefix + suffix + executable_suffix:
            raise CampaignError(f"environment.{label} does not share TOOLPREFIX")

    boots = campaign["boots"]
    if not isinstance(boots, list) or len(boots) != requested:
        raise CampaignError("planned boot count differs from protocol")
    seen_ids: set[str] = set()
    seen_logs: set[str] = set()
    boot_keys = [
        "boot_id",
        "challenge",
        "command_argv",
        "command_environment",
        "exit_code",
        "finished_at_utc",
        "guest_log",
        "guest_log_sha256",
        "image_final_path",
        "image_final_sha256",
        "image_input_path",
        "image_input_sha256",
        "kernel_path",
        "kernel_sha256",
        "observed_sample_orders",
        "runner_log",
        "runner_log_sha256",
        "sample_count",
        "status",
    ]
    seen_challenges: set[str] = set()
    for index, boot in enumerate(boots, start=1):
        if not isinstance(boot, dict):
            raise CampaignError("boot entry must be an object")
        _expect_keys(boot, boot_keys, f"boot[{index}]")
        expected_id = f"boot-{index:02d}"
        if boot["boot_id"] != expected_id or boot["boot_id"] in seen_ids:
            raise CampaignError("boot identities are not canonical and unique")
        seen_ids.add(boot["boot_id"])
        if (
            not isinstance(boot["challenge"], str)
            or not CHALLENGE_RE.fullmatch(boot["challenge"])
            or boot["challenge"] == "0" * 16
            or boot["challenge"] in seen_challenges
            or (int(boot["challenge"], 16) & 1) != (index & 1)
        ):
            raise CampaignError(
                "boot challenges must be unique, nonzero, and parity-balanced"
            )
        seen_challenges.add(boot["challenge"])
        if boot["guest_log"] in seen_logs or boot["runner_log"] in seen_logs:
            raise CampaignError("each boot must use independent log paths")
        seen_logs.update((boot["guest_log"], boot["runner_log"]))
        raw_dir = (artifact_root / "raw" / expected_id).as_posix()
        if (
            boot["guest_log"] != f"{raw_dir}/guest.log"
            or boot["runner_log"] != f"{raw_dir}/runner.log"
            or boot["kernel_path"] != f"{raw_dir}/kernel"
            or boot["image_input_path"] != f"{raw_dir}/fs.img"
            or boot["image_final_path"] != f"{raw_dir}/fs-copy.img"
        ):
            raise CampaignError("boot artifact paths are not canonical")
        if not isinstance(boot["command_argv"], list) or not all(
            isinstance(item, str) and item for item in boot["command_argv"]
        ):
            raise CampaignError("boot command is invalid")
        compiler_argv0 = environment["compiler"]["argv0"]
        if not compiler_argv0.endswith("gcc"):
            raise CampaignError("compiler identity cannot derive TOOLPREFIX")
        if (
            len(boot["command_argv"]) != 4
            or boot["command_argv"][0] != environment["bash"]["path"]
            or not _is_portable_absolute_path(boot["command_argv"][1])
            or _portable_name(boot["command_argv"][1]) != "run-agent-tests.sh"
            or boot["command_argv"][2]
            != f"AGENT_EVAL_CHALLENGE_HEX={boot['challenge']}"
            or boot["command_argv"][3]
            != f"AGENT_TEST_GUEST_LOG_FILE={boot['guest_log']}"
        ):
            raise CampaignError("boot command lacks evaluation provenance binding")
        expected_environment = _micro_boot_environment(
            environment,
            platform,
            boot["challenge"],
            boot["guest_log"],
            protocol["micro_timeout_seconds"],
        )
        if boot["command_environment"] != expected_environment:
            raise CampaignError("boot process environment differs from its preflight")
        status = boot["status"]
        if status not in {"planned", "passed", "failed"}:
            raise CampaignError("invalid boot status")
        if status == "planned":
            if any(
                boot[key] is not None
                for key in [
                    "exit_code",
                    "finished_at_utc",
                    "guest_log_sha256",
                    "image_final_sha256",
                    "image_input_sha256",
                    "kernel_sha256",
                    "runner_log_sha256",
                    "sample_count",
                ]
            ) or boot["observed_sample_orders"] != []:
                raise CampaignError("planned boot contains observed evidence")
        elif status == "passed":
            if (
                boot["exit_code"] != 0
                or boot["sample_count"] != protocol["expected_samples_per_boot"]
                or boot["observed_sample_orders"] != ["AB", "BA"]
            ):
                raise CampaignError("passed boot lacks the complete sample contract")
            for key in [
                "guest_log_sha256",
                "image_final_sha256",
                "image_input_sha256",
                "kernel_sha256",
                "runner_log_sha256",
            ]:
                if not isinstance(boot[key], str) or not re.fullmatch(
                    r"[0-9a-f]{64}", boot[key]
                ):
                    raise CampaignError(f"passed boot lacks {key}")
        elif type(boot["exit_code"]) is not int or boot["exit_code"] == 0:
            raise CampaignError("failed boot must retain a nonzero exit code")
    if campaign["phase"] == "collected" and any(
        boot["status"] != "passed" for boot in boots
    ):
        raise CampaignError("collected campaign contains an unsuccessful boot")


def check_campaign(repo: Path, manifest_path: Path, *, require_collected: bool) -> dict[str, Any]:
    repo = _resolved_safe_directory(repo, "repository")
    manifest_path = _resolved_safe_file(manifest_path, "campaign manifest")
    campaign = _strict_json(manifest_path)
    validate_campaign(campaign)
    _require_manifest_artifact_root(repo, manifest_path, campaign["run"]["artifact_root"])
    if require_collected and campaign["phase"] != "collected":
        raise CampaignError("campaign has not completed collection")
    commit, clean = repository_identity(repo)
    if commit != campaign["run"]["commit"] or not clean:
        raise CampaignError("current source tree differs from campaign identity")
    _evaluation_source_gate(
        repo,
        campaign["run"]["commit"],
        campaign["run"]["artifact_root"],
        "during campaign check",
    )
    _require_measurement_receipt(
        repo, campaign["run"]["commit"],
        campaign["measurement_source_receipt"], "during campaign check",
    )
    suite_path = repo / campaign["protocol"]["suite_path"]
    _require_regular_file(suite_path, "evaluation suite")
    if _sha256(suite_path) != campaign["protocol"]["suite_sha256"]:
        raise CampaignError("evaluation suite changed after campaign collection")
    if (
        _expected_samples_per_boot(suite_path)
        != campaign["protocol"]["expected_samples_per_boot"]
    ):
        raise CampaignError("evaluation sample count differs from the suite")
    for label, tool in campaign["environment"].items():
        executable = Path(tool["path"])
        _require_regular_file(executable, f"environment executable {label}")
        if _sha256(executable) != tool["sha256"]:
            raise CampaignError(f"environment executable changed: {label}")
    for boot in campaign["boots"]:
        for path_key, hash_key in [
            ("runner_log", "runner_log_sha256"),
            ("guest_log", "guest_log_sha256"),
            ("kernel_path", "kernel_sha256"),
            ("image_input_path", "image_input_sha256"),
            ("image_final_path", "image_final_sha256"),
        ]:
            expected = boot[hash_key]
            if expected is None:
                continue
            path = repo / boot[path_key]
            _require_regular_file(
                path,
                path_key,
                nonempty=path_key in {"runner_log", "kernel_path", "image_input_path", "image_final_path"},
            )
            if _sha256(path) != expected:
                raise CampaignError(f"raw evidence changed after collection: {path}")
    return campaign


def get_boot_field(manifest_path: Path, boot_id: str, field: str) -> str:
    campaign = _strict_json(manifest_path)
    validate_campaign(campaign)
    matching = [boot for boot in campaign["boots"] if boot["boot_id"] == boot_id]
    if len(matching) != 1:
        raise CampaignError(f"boot is not uniquely planned: {boot_id}")
    if field not in {
        "challenge", "guest_log", "runner_log", "kernel_path",
        "image_input_path", "image_final_path",
    }:
        raise CampaignError(f"boot field is not exportable: {field}")
    return matching[0][field]


def _environment_sha256(campaign: dict[str, Any]) -> str:
    encoded = json.dumps(
        campaign["environment"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _preflight_binding(value: dict[str, Any]) -> dict[str, Any]:
    kind = value.get("kind")
    if kind == KIND:
        validate_campaign(value)
        run_keys = (
            "artifact_root",
            "clean_worktree",
            "commit",
            "execution_domain",
            "id",
            "started_at_utc",
        )
        boot_keys = (
            "boot_id",
            "challenge",
            "command_argv",
            "command_environment",
            "guest_log",
            "image_final_path",
            "image_input_path",
            "kernel_path",
            "runner_log",
        )
        return {
            "boots": [
                {key: boot[key] for key in boot_keys} for boot in value["boots"]
            ],
            "environment": value["environment"],
            "kind": kind,
            "measurement_source_receipt": value["measurement_source_receipt"],
            "platform": value["platform"],
            "protocol": value["protocol"],
            "run": {key: value["run"][key] for key in run_keys},
            "schema_version": value["schema_version"],
        }
    if kind == SCENARIO_KIND:
        validate_scenario_campaign(value)
        boot_keys = (
            "boot_id",
            "challenge",
            "command_argv",
            "command_environment",
            "host_summary",
            "runner_log",
            "target_order",
            "work_dir",
        )
        return {
            "boots": [
                {key: boot[key] for key in boot_keys} for boot in value["boots"]
            ],
            "kind": kind,
            "measurement_source_receipt": value["measurement_source_receipt"],
            "platform": value["platform"],
            "protocol": value["protocol"],
            "report_path": value["report"]["path"],
            "run": value["run"],
            "schema_version": value["schema_version"],
        }
    raise CampaignError("preflight receipt campaign kind is unsupported")


def build_preflight_receipt(value: dict[str, Any]) -> dict[str, Any]:
    binding = _preflight_binding(value)
    return {
        "binding_sha256": _canonical_sha256(binding),
        "campaign_kind": value["kind"],
        "campaign_schema_version": value["schema_version"],
        "kind": PREFLIGHT_RECEIPT_KIND,
        "run_id": value["run"]["id"],
        "schema_version": PREFLIGHT_RECEIPT_SCHEMA_VERSION,
        "source_commit": value["run"]["commit"],
        "status": "passed",
    }


def format_preflight_receipt(value: dict[str, Any]) -> str:
    return json.dumps(
        build_preflight_receipt(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ) + "\n"


def _write_ascii_stdout(value: str) -> None:
    try:
        encoded = value.encode("ascii")
        output = sys.stdout.buffer
    except (UnicodeError, AttributeError) as error:
        raise CampaignError("canonical stdout is unavailable") from error
    output.write(encoded)
    output.flush()


def check_preflight_receipt(manifest_path: Path, receipt_path: Path) -> None:
    campaign = _strict_json(manifest_path)
    _require_regular_file(receipt_path, "campaign preflight receipt")
    try:
        observed = read_regular_file(
            receipt_path, maximum_bytes=PREFLIGHT_RECEIPT_MAX_BYTES
        )
    except (OSError, ValueError) as error:
        raise CampaignError(
            f"campaign preflight receipt is unreadable: {error}"
        ) from error
    expected = format_preflight_receipt(campaign).encode("ascii")
    if observed != expected:
        raise CampaignError("campaign preflight receipt differs from its manifest")


def _scenario_clean_environment(
    tools: dict[str, dict[str, str]],
    *,
    posix_temporary: str = "/tmp",
    native_temporary: str | None = None,
    system_drive: str = "/",
) -> dict[str, str]:
    compiler_prefix, _ = _absolute_toolprefix(tools["compiler"]["path"])
    host_cc_path = tools["host_cc"]["path"]
    if (
        tools["host_cc"].get("argv0") != "cc"
        or not host_cc_path.startswith("/")
        or str(PurePosixPath(host_cc_path).parent)
        not in SCENARIO_CLEAN_PATH.split(":")
    ):
        raise CampaignError(
            "scenario Host C compiler is unavailable through the clean PATH"
        )
    native_msys = (
        os.environ.get("AGENTOS_EVALUATION_EXECUTION_DOMAIN") == "native-msys2"
    )
    locale = "C.UTF-8" if native_msys else "C"
    native_temporary = native_temporary or posix_temporary
    if not posix_temporary.startswith("/") or not (
        native_temporary.startswith("/")
        or PureWindowsPath(native_temporary).is_absolute()
    ):
        raise CampaignError("scenario temporary namespace is invalid")
    if system_drive != "/" and re.fullmatch(r"[A-Z]:", system_drive) is None:
        raise CampaignError("scenario Windows system drive identity is invalid")
    environment = {
        "CC": host_cc_path,
        "HOME": SCENARIO_CLEAN_HOME,
        "HOSTCC": host_cc_path,
        "HOST_CC": host_cc_path,
        "LANG": locale,
        "LC_ALL": locale,
        "MAKE_TOOL": tools["make"]["path"],
        "PATH": SCENARIO_CLEAN_PATH,
        "QEMU": tools["qemu"]["path"],
        "SHELL": tools["bash"]["path"],
        "SYSTEMDRIVE": system_drive,
        "TEMP": native_temporary,
        "TMP": native_temporary,
        "TMPDIR": posix_temporary,
        "TOOLPREFIX": compiler_prefix,
        "TZ": "UTC",
    }
    if tuple(environment) != SCENARIO_CLEAN_ENVIRONMENT_KEYS:
        raise CampaignError("scenario clean environment order changed")
    return environment


def _platform_temporary_identity(platform: dict[str, Any]) -> tuple[str, str]:
    if platform.get("domain") != "native-msys2":
        return "/tmp", "/tmp"
    posix_temporary = platform.get("temporary_directory")
    native_temporary = platform.get("windows_temporary_directory")
    if (
        not isinstance(posix_temporary, str)
        or not posix_temporary.startswith("/")
        or not isinstance(native_temporary, str)
        or not PureWindowsPath(native_temporary).is_absolute()
    ):
        raise CampaignError("MSYS2 scenario temporary identity is unavailable")
    return posix_temporary, native_temporary


def _platform_system_drive(platform: dict[str, Any]) -> str | None:
    if platform.get("domain") != "native-msys2":
        return None
    system_drive = platform.get("windows_system_drive")
    if not isinstance(system_drive, str) or re.fullmatch(r"[A-Z]:", system_drive) is None:
        raise CampaignError("MSYS2 Windows system drive identity is unavailable")
    return system_drive


def _probe_scenario_environment(
    repo: Path, *, wsl_distro: str, toolprefix: str, qemu: str,
    posix_temporary: str = "/tmp", native_temporary: str | None = None,
    system_drive: str = "/",
) -> dict[str, Any]:
    if os.name == "nt":
        launcher = probe_executable("wsl.exe", ["--version"], repo)
        domain_prefix = [launcher["path"], "-d", wsl_distro, "--"]
        domain = "wsl-clean-shell"
        bootstrap_path = SCENARIO_CLEAN_PATH
        bootstrap_env = "/usr/bin/env"
        bootstrap_bash = "/bin/bash"
    else:
        launcher = None
        domain_prefix = []
        if os.environ.get("AGENTOS_EVALUATION_EXECUTION_DOMAIN") == "native-msys2":
            domain = "native-msys2-clean-shell"
            bootstrap_path = os.environ.get("PATH", "")
            bootstrap_env = shutil.which("env") or ""
            bootstrap_bash = os.environ.get("BASH_BIN", "")
            if (
                not bootstrap_path
                or not bootstrap_env.startswith("/")
                or not bootstrap_bash.startswith("/")
            ):
                raise CampaignError("native-msys2 scenario bootstrap is incomplete")
        else:
            domain = "native-clean-shell"
            bootstrap_path = SCENARIO_CLEAN_PATH
            bootstrap_env = "/usr/bin/env"
            bootstrap_bash = "/bin/bash"

    bootstrap_locale = "C.UTF-8" if domain == "native-msys2-clean-shell" else "C"
    bootstrap_environment = [
        f"HOME={SCENARIO_CLEAN_HOME}",
        f"PATH={bootstrap_path}",
        f"LANG={bootstrap_locale}",
        f"LC_ALL={bootstrap_locale}",
        "TZ=UTC",
    ]
    if domain == "native-msys2-clean-shell":
        if re.fullmatch(r"[A-Z]:", system_drive) is None:
            raise CampaignError("native-msys2 scenario probe lacks SYSTEMDRIVE")
        bootstrap_environment.append(f"SYSTEMDRIVE={system_drive}")
    elif system_drive != "/":
        raise CampaignError("POSIX scenario probe received a Windows system drive")
    bootstrap_prefix = [
        *domain_prefix,
        bootstrap_env,
        "-i",
        *bootstrap_environment,
        bootstrap_bash,
        "--noprofile",
        "--norc",
        "-c",
    ]

    tools: dict[str, dict[str, str]] = {}
    for label, argv0 in (
        ("bash", "bash"),
        ("compiler", f"{toolprefix}gcc"),
        ("env", "env"),
        ("host_cc", "cc"),
        ("linker", f"{toolprefix}ld"),
        ("make", "make"),
        ("objcopy", f"{toolprefix}objcopy"),
        ("objdump", f"{toolprefix}objdump"),
        ("qemu", qemu),
        ("timeout", "timeout"),
    ):
        quoted = shlex.quote(argv0)
        script = (
            "set -eu; "
            f"tool={quoted}; "
            'path=$(command -v -- "$tool"); '
            'path=$(readlink -f -- "$path"); '
            'sha=$(sha256sum -- "$path"); sha=${sha%% *}; '
            'version=$("$path" --version 2>&1 | sed -n "1p"); '
            'test -n "$path"; test -n "$version"; '
            'printf "__AGENTEVAL_PATH__%s\\n" "$path"; '
            'printf "__AGENTEVAL_SHA256__%s\\n" "$sha"; '
            'printf "__AGENTEVAL_VERSION__%s\\n" "$version"'
        )
        output = _run([*bootstrap_prefix, script], repo)
        values: dict[str, str] = {}
        for line in output.splitlines():
            for key, prefix in (
                ("path", "__AGENTEVAL_PATH__"),
                ("sha256", "__AGENTEVAL_SHA256__"),
                ("version", "__AGENTEVAL_VERSION__"),
            ):
                if line.startswith(prefix):
                    if key in values:
                        raise CampaignError(f"duplicate scenario {label} {key}")
                    values[key] = line.removeprefix(prefix)
        if set(values) != {"path", "sha256", "version"}:
            raise CampaignError(f"scenario execution-domain probe is incomplete: {label}")
        if not values["path"].startswith("/") or not re.fullmatch(
            r"[0-9a-f]{64}", values["sha256"]
        ) or not values["version"]:
            raise CampaignError(f"scenario execution-domain probe is invalid: {label}")
        tools[label] = {"argv0": argv0, **values}
    if launcher is None:
        launcher = dict(tools["env"])
        launcher_argv = [tools["env"]["path"], "-i"]
    else:
        launcher_argv = [
            launcher["path"],
            "-d",
            wsl_distro,
            "--",
            tools["env"]["path"],
            "-i",
        ]
    return {
        "clean_environment": _scenario_clean_environment(
            tools,
            posix_temporary=posix_temporary,
            native_temporary=native_temporary,
            system_drive=system_drive,
        ),
        "domain": domain,
        "launcher": launcher,
        "launcher_argv": launcher_argv,
        "tools": tools,
    }


def _scenario_boot_environment(
    micro_environment: dict[str, dict[str, str]],
    execution_environment: dict[str, Any],
) -> dict[str, str]:
    tools = execution_environment["tools"]
    clean = execution_environment["clean_environment"]
    host_locale = "C.UTF-8" if execution_environment["domain"] == "native-msys2-clean-shell" else "C"
    return {
        "AGENTOS_WSL_BASH": tools["bash"]["path"],
        "AGENTOS_WSL_ENV": tools["env"]["path"],
        "AGENTOS_WSL_HOME": clean["HOME"],
        "AGENTOS_WSL_LANG": clean["LANG"],
        "AGENTOS_WSL_LAUNCHER": execution_environment["launcher"]["path"],
        "AGENTOS_WSL_LC_ALL": clean["LC_ALL"],
        "AGENTOS_WSL_MAKE": tools["make"]["path"],
        "AGENTOS_WSL_PATH": clean["PATH"],
        "AGENTOS_WSL_TMPDIR": clean["TMPDIR"],
        "AGENTOS_WSL_TIMEOUT": tools["timeout"]["path"],
        "AGENTOS_WSL_TZ": clean["TZ"],
        "CC": clean["CC"],
        "HOSTCC": clean["HOSTCC"],
        "HOST_CC": clean["HOST_CC"],
        "LANG": host_locale,
        "LC_ALL": host_locale,
        "PATH": _trusted_process_path(
            [
                micro_environment["python"],
                micro_environment["git"],
                execution_environment["launcher"],
            ]
        ),
        "PYTHONHASHSEED": "0",
        "PYTHON_BIN": micro_environment["python"]["path"],
        "QEMU": clean["QEMU"],
        "SYSTEMDRIVE": clean["SYSTEMDRIVE"],
        "TEMP": clean["TEMP"],
        "TMP": clean["TMP"],
        "TMPDIR": clean["TMPDIR"],
        "TOOLPREFIX": clean["TOOLPREFIX"],
        "TZ": "UTC",
        "AGENTOS_WINDOWS_SYSTEM_DRIVE": clean["SYSTEMDRIVE"],
    }


def create_scenario_campaign(
    *,
    repo: Path,
    micro_manifest: Path,
    output: Path,
    requested_boots: int,
    timeout_seconds: int,
    wsl_distro: str,
) -> dict[str, Any]:
    if type(requested_boots) is not int or requested_boots != FORMAL_BOOT_COUNT:
        raise CampaignError(
            f"scenario evaluation requires the fixed {FORMAL_BOOT_COUNT}-boot stopping rule"
        )
    repo = _resolved_safe_directory(repo, "repository")
    micro_manifest = _resolved_safe_file(micro_manifest, "micro campaign manifest")
    micro = _strict_json(micro_manifest)
    validate_campaign(micro)
    _require_manifest_artifact_root(
        repo, micro_manifest, micro["run"]["artifact_root"]
    )
    if micro["phase"] not in {"collecting", "collected"}:
        raise CampaignError("scenario plan requires a valid micro campaign")
    commit, clean = repository_identity(repo)
    if not clean or commit != micro["run"]["commit"]:
        raise CampaignError("scenario collection requires the same clean source commit")
    _evaluation_source_gate(
        repo,
        commit,
        micro["run"]["artifact_root"],
        "before scenario campaign creation",
    )
    _require_measurement_receipt(
        repo, commit, micro["measurement_source_receipt"],
        "before scenario campaign creation",
    )
    scenario_pair_deadline_contract(timeout_seconds)
    if not isinstance(wsl_distro, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", wsl_distro):
        raise CampaignError("invalid WSL distribution name")
    output = _safe_output_path(output, "scenario campaign manifest")
    if output.exists():
        raise CampaignError(f"scenario campaign already exists: {output}")
    output_rel = _repo_relative(repo, output)
    run_dir_rel = PurePosixPath(output_rel).parent.as_posix()
    artifact_root = _artifact_root(micro["run"]["artifact_root"])
    if PurePosixPath(run_dir_rel) != artifact_root / "scenario":
        raise CampaignError(
            "scenario manifest parent differs from the micro artifact root"
        )
    python_bin = micro["environment"]["python"]["path"]
    compiler = micro["environment"]["compiler"]["argv0"]
    requested_toolprefix = compiler[:-3]
    collector_path = repo / "host_tools" / "evaluation_scenario.py"
    driver_path = repo / "host_tools" / "check_seeded_action_state.py"
    _require_regular_file(collector_path, "scenario collector")
    _require_regular_file(driver_path, "scenario input driver")
    posix_temporary, native_temporary = _platform_temporary_identity(
        micro["platform"]
    )
    system_drive = _platform_system_drive(micro["platform"]) or "/"
    execution_environment = _probe_scenario_environment(
        repo,
        wsl_distro=wsl_distro,
        toolprefix=requested_toolprefix,
        qemu=micro["environment"]["qemu"]["argv0"],
        posix_temporary=posix_temporary,
        native_temporary=native_temporary,
        system_drive=system_drive,
    )
    toolprefix, _ = _absolute_toolprefix(
        execution_environment["tools"]["compiler"]["path"]
    )
    challenges: set[str] = set()
    boots = []
    for number in range(1, requested_boots + 1):
        boot_id = f"boot-{number:02d}"
        challenge = _derive_scenario_challenge(
            micro["run"]["commit"], number, challenges
        )
        challenges.add(challenge)
        target_order = "plain-agentos" if number % 2 else "agentos-plain"
        work_dir = f"{run_dir_rel}/raw/{boot_id}"
        host_summary = f"{work_dir}/host-summary.json"
        runner_log = f"{work_dir}/runner.log"
        command = [
            micro["environment"]["python"]["path"],
            str(_resolved_safe_file(driver_path, "scenario input driver")),
            "--work-dir",
            work_dir,
            "--timeout",
            str(timeout_seconds),
            "--wsl-distro",
            wsl_distro,
            "--target-order",
            target_order,
            "--challenge",
            challenge,
            "--json-out",
            host_summary,
        ]
        boots.append(
            {
                "boot_id": boot_id,
                "challenge": challenge,
                "command_argv": command,
                "command_environment": _scenario_boot_environment(
                    micro["environment"], execution_environment
                ),
                "exit_code": None,
                "finished_at_utc": None,
                "host_summary": host_summary,
                "host_summary_sha256": None,
                "runner_log": runner_log,
                "runner_log_sha256": None,
                "status": "planned",
                "target_order": target_order,
                "work_dir": work_dir,
            }
        )
    value = {
        "boots": boots,
        "kind": SCENARIO_KIND,
        "measurement_source_receipt": json.loads(json.dumps(
            micro["measurement_source_receipt"]
        )),
        "phase": "collecting",
        "platform": json.loads(json.dumps(micro["platform"])),
        "protocol": {
            "collector_path": "host_tools/evaluation_scenario.py",
            "collector_sha256": _sha256(collector_path),
            "execution_environment": execution_environment,
            "input_driver_path": "host_tools/check_seeded_action_state.py",
            "input_driver_sha256": _sha256(driver_path),
            "git_bin": micro["environment"]["git"]["path"],
            "git_sha256": micro["environment"]["git"]["sha256"],
            "minimum_boots": MINIMUM_BOOTS,
            "python_bin": python_bin,
            "python_sha256": micro["environment"]["python"]["sha256"],
            "requested_boots": requested_boots,
            "timeout_seconds": timeout_seconds,
            "toolprefix": toolprefix,
            "wsl_distro": wsl_distro,
        },
        "report": {
            "path": f"{run_dir_rel}/report.json",
            "sha256": None,
            "status": "planned",
        },
        "run": {
            "artifact_root": artifact_root.as_posix(),
            "commit": micro["run"]["commit"],
            "environment_sha256": _environment_sha256(micro),
            "id": micro["run"]["id"],
            "platform_sha256": _canonical_sha256(micro["platform"]),
            "scenario_environment_sha256": _canonical_sha256(execution_environment),
        },
        "schema_version": SCENARIO_SCHEMA_VERSION,
    }
    validate_scenario_campaign(value)
    _atomic_json(output, value)
    return value


def validate_scenario_campaign(value: dict[str, Any]) -> None:
    _expect_keys(
        value,
        [
            "boots", "kind", "measurement_source_receipt", "phase", "platform",
            "protocol", "report", "run", "schema_version",
        ],
        "scenario campaign",
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCENARIO_SCHEMA_VERSION
        or value["kind"] != SCENARIO_KIND
    ):
        raise CampaignError("unsupported scenario campaign schema")
    if value["phase"] not in {"collecting", "collected"}:
        raise CampaignError("invalid scenario campaign phase")
    run = value["run"]
    _expect_keys(
        run,
        [
            "artifact_root", "commit", "environment_sha256", "id",
            "platform_sha256", "scenario_environment_sha256",
        ],
        "scenario run",
    )
    if not COMMIT_RE.fullmatch(run["commit"]) or not RUN_ID_RE.fullmatch(run["id"]):
        raise CampaignError("invalid scenario run identity")
    artifact_root = _artifact_root(run["artifact_root"])
    if artifact_root.name != run["id"]:
        raise CampaignError("scenario artifact root is not bound to the run identity")
    if any(
        not isinstance(run[key], str)
        or re.fullmatch(r"[0-9a-f]{64}", run[key]) is None
        for key in (
            "environment_sha256", "platform_sha256",
            "scenario_environment_sha256",
        )
    ):
        raise CampaignError("invalid scenario environment identity")
    try:
        validate_measurement_source_receipt_shape(
            value["measurement_source_receipt"],
            expected_commit=run["commit"],
        )
    except ValueError as error:
        raise CampaignError(
            f"invalid scenario measurement source receipt: {error}"
        ) from error
    platform = value["platform"]
    if not isinstance(platform, dict):
        raise CampaignError("scenario platform proof must be an object")
    _validate_platform_proof(platform, str(platform.get("entry_domain", "")))
    if run["platform_sha256"] != _canonical_sha256(platform):
        raise CampaignError("scenario platform proof differs from its hash")
    protocol = value["protocol"]
    _expect_keys(
        protocol,
        [
            "collector_path", "collector_sha256", "execution_environment", "git_bin", "git_sha256", "input_driver_path",
            "input_driver_sha256", "minimum_boots", "python_bin", "python_sha256",
            "requested_boots", "timeout_seconds", "toolprefix", "wsl_distro",
        ],
        "scenario protocol",
    )
    requested = protocol["requested_boots"]
    if (
        type(requested) is not int
        or requested != FORMAL_BOOT_COUNT
        or protocol["minimum_boots"] != MINIMUM_BOOTS
    ):
        raise CampaignError("invalid scenario boot protocol")
    scenario_pair_deadline_contract(protocol["timeout_seconds"])
    if (
        protocol["collector_path"] != "host_tools/evaluation_scenario.py"
        or protocol["input_driver_path"] != "host_tools/check_seeded_action_state.py"
        or any(
            not isinstance(protocol[key], str)
            or not re.fullmatch(r"[0-9a-f]{64}", protocol[key])
            for key in ("collector_sha256", "input_driver_sha256")
        )
        or not isinstance(protocol["python_bin"], str)
        or not _is_portable_absolute_path(protocol["python_bin"])
        or not re.fullmatch(r"[0-9a-f]{64}", protocol["python_sha256"])
        or not _is_portable_absolute_path(protocol["git_bin"])
        or not re.fullmatch(r"[0-9a-f]{64}", protocol["git_sha256"])
        or not isinstance(protocol["toolprefix"], str)
    ):
        raise CampaignError("invalid scenario input contract")
    execution_environment = protocol["execution_environment"]
    _expect_keys(
        execution_environment,
        ["clean_environment", "domain", "launcher", "launcher_argv", "tools"],
        "scenario execution environment",
    )
    for container, labels in (
        (execution_environment["launcher"], None),
        (
            execution_environment["tools"],
            {
                "bash", "compiler", "env", "host_cc", "linker", "make",
                "objcopy", "objdump", "qemu", "timeout",
            },
        ),
    ):
        if not isinstance(container, dict) or (labels is not None and set(container) != labels):
            raise CampaignError("invalid scenario execution-domain tools")
    _expect_keys(
        execution_environment["launcher"],
        ["argv0", "path", "sha256", "version"],
        "scenario launcher",
    )
    for label, tool in {
        "launcher": execution_environment["launcher"],
        **execution_environment["tools"],
    }.items():
        _expect_keys(tool, ["argv0", "path", "sha256", "version"], f"scenario tool {label}")
        if (
            not all(isinstance(tool[key], str) and tool[key] for key in tool)
            or not re.fullmatch(r"[0-9a-f]{64}", tool["sha256"])
            or not _is_portable_absolute_path(tool["path"])
        ):
            raise CampaignError(f"invalid scenario tool identity: {label}")
    tools = execution_environment["tools"]
    clean_environment = execution_environment["clean_environment"]
    if not isinstance(clean_environment, dict):
        raise CampaignError("scenario clean environment must be an object")
    _expect_keys(
        clean_environment,
        SCENARIO_CLEAN_ENVIRONMENT_KEYS,
        "scenario clean environment",
    )
    if any(
        not isinstance(value, str) or "\x00" in value or "\n" in value
        for value in clean_environment.values()
    ):
        raise CampaignError("scenario clean environment contains an invalid value")
    posix_temporary, native_temporary = _platform_temporary_identity(platform)
    system_drive = _platform_system_drive(platform) or "/"
    if clean_environment != _scenario_clean_environment(
        tools,
        posix_temporary=posix_temporary,
        native_temporary=native_temporary,
        system_drive=system_drive,
    ):
        raise CampaignError("scenario clean environment differs from bound tools")
    domain = execution_environment["domain"]
    launcher = execution_environment["launcher"]
    if domain == "wsl-clean-shell":
        expected_launcher = [
            launcher["path"],
            "-d",
            protocol["wsl_distro"],
            "--",
            tools["env"]["path"],
            "-i",
        ]
        launcher_valid = launcher["argv0"] == "wsl.exe"
    elif domain in {"native-clean-shell", "native-msys2-clean-shell"}:
        expected_launcher = [tools["env"]["path"], "-i"]
        launcher_valid = launcher == tools["env"]
    else:
        expected_launcher = []
        launcher_valid = False
    if (
        not launcher_valid
        or execution_environment["launcher_argv"] != expected_launcher
        or any(argument in {"-l", "-lc", "--login"} for argument in expected_launcher)
    ):
        raise CampaignError("invalid scenario execution-domain launcher")
    if (
        tools["bash"]["argv0"] != "bash"
        or tools["env"]["argv0"] != "env"
        or tools["host_cc"]["argv0"] != "cc"
        or tools["make"]["argv0"] != "make"
        or tools["compiler"]["path"] != f"{protocol['toolprefix']}gcc"
        or tools["linker"]["path"] != f"{protocol['toolprefix']}ld"
        or tools["objcopy"]["path"] != f"{protocol['toolprefix']}objcopy"
        or tools["objdump"]["path"] != f"{protocol['toolprefix']}objdump"
        or tools["timeout"]["argv0"] != "timeout"
        or run["scenario_environment_sha256"] != _canonical_sha256(execution_environment)
    ):
        raise CampaignError("scenario execution environment is not bound to the protocol")
    if not isinstance(value["boots"], list) or len(value["boots"]) != requested:
        raise CampaignError("scenario boot count differs from protocol")
    seen_challenges: set[str] = set()
    expected_keys = [
        "boot_id", "challenge", "command_argv", "command_environment", "exit_code", "finished_at_utc",
        "host_summary", "host_summary_sha256", "runner_log", "runner_log_sha256",
        "status", "target_order", "work_dir",
    ]
    for number, boot in enumerate(value["boots"], 1):
        _expect_keys(boot, expected_keys, f"scenario boot[{number}]")
        expected_id = f"boot-{number:02d}"
        expected_order = "plain-agentos" if number % 2 else "agentos-plain"
        if boot["boot_id"] != expected_id or boot["target_order"] != expected_order:
            raise CampaignError("scenario boot order is not prebalanced")
        challenge = boot["challenge"]
        if not isinstance(challenge, str) or not SCENARIO_CHALLENGE_RE.fullmatch(challenge) or challenge in seen_challenges:
            raise CampaignError("scenario challenges must be unique canonical values")
        seen_challenges.add(challenge)
        scenario_root = artifact_root / "scenario"
        expected_work_dir = (scenario_root / "raw" / expected_id).as_posix()
        if boot["work_dir"] != expected_work_dir:
            raise CampaignError("scenario work directory is not canonical")
        if boot["runner_log"] != f"{boot['work_dir']}/runner.log" or boot["host_summary"] != f"{boot['work_dir']}/host-summary.json":
            raise CampaignError("scenario evidence paths are not canonical")
        command = boot["command_argv"]
        expected_command = [
            protocol["python_bin"],
            str(Path(protocol["python_bin"]).parent / "__driver_placeholder__"),
            "--work-dir", boot["work_dir"],
            "--timeout", str(protocol["timeout_seconds"]),
            "--wsl-distro", protocol["wsl_distro"],
            "--target-order", expected_order,
            "--challenge", challenge,
            "--json-out", boot["host_summary"],
        ]
        if (
            len(command) != len(expected_command)
            or command[0] != protocol["python_bin"]
            or not _is_portable_absolute_path(command[1])
            or _portable_name(command[1]) != PurePosixPath(protocol["input_driver_path"]).name
            or command[2:] != expected_command[2:]
        ):
            raise CampaignError("scenario command lacks a provenance binding")
        expected_environment = _scenario_boot_environment(
            {
                "python": {
                    "path": protocol["python_bin"],
                    "sha256": protocol["python_sha256"],
                },
                "git": {
                    "path": protocol["git_bin"],
                    "sha256": protocol["git_sha256"],
                },
            },
            execution_environment,
        )
        if boot["command_environment"] != expected_environment:
            raise CampaignError("scenario process environment differs from preflight")
        if boot["status"] not in {"planned", "passed", "failed"}:
            raise CampaignError("invalid scenario boot status")
        if boot["status"] == "planned":
            if any(boot[key] is not None for key in ("exit_code", "finished_at_utc", "host_summary_sha256", "runner_log_sha256")):
                raise CampaignError("planned scenario boot contains observations")
        elif boot["status"] == "passed":
            if boot["exit_code"] != 0:
                raise CampaignError("passed scenario boot has nonzero exit")
            for key in ("host_summary_sha256", "runner_log_sha256"):
                if not isinstance(boot[key], str) or not re.fullmatch(r"[0-9a-f]{64}", boot[key]):
                    raise CampaignError(f"passed scenario boot lacks {key}")
        elif type(boot["exit_code"]) is not int or boot["exit_code"] == 0:
            raise CampaignError("failed scenario boot lacks a nonzero exit")
    report = value["report"]
    _expect_keys(report, ["path", "sha256", "status"], "scenario report")
    expected_report = (artifact_root / "scenario" / "report.json").as_posix()
    if report["path"] != expected_report:
        raise CampaignError("scenario report path is not canonical")
    if report["status"] not in {"planned", "recorded"}:
        raise CampaignError("invalid scenario report status")
    if report["status"] == "recorded" and (
        not isinstance(report["sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", report["sha256"])
    ):
        raise CampaignError("recorded scenario report lacks a hash")
    if value["phase"] == "collected" and (
        report["status"] != "recorded" or any(boot["status"] != "passed" for boot in value["boots"])
    ):
        raise CampaignError("collected scenario campaign is incomplete")


def get_scenario_boot_field(path: Path, boot_id: str, field: str) -> str:
    value = _strict_json(path)
    validate_scenario_campaign(value)
    if field not in {"challenge", "target_order", "work_dir", "runner_log", "host_summary"}:
        raise CampaignError("scenario field is not exportable")
    matching = [item for item in value["boots"] if item["boot_id"] == boot_id]
    if len(matching) != 1:
        raise CampaignError("scenario boot is not uniquely planned")
    return matching[0][field]


def get_scenario_metadata(path: Path, field: str) -> str:
    value = _strict_json(path)
    validate_scenario_campaign(value)
    if field == "boots":
        return str(value["protocol"]["requested_boots"])
    if field == "run_id":
        return value["run"]["id"]
    if field == "commit":
        return value["run"]["commit"]
    raise CampaignError("scenario metadata field is not exportable")


def _record_scenario_boot_result(*, repo: Path, manifest_path: Path, boot_id: str, exit_code: int, runner_log: Path, host_summary: Path) -> None:
    repo = _resolved_safe_directory(repo, "repository")
    manifest_path = _resolved_safe_file(manifest_path, "scenario campaign manifest")
    value = _strict_json(manifest_path)
    validate_scenario_campaign(value)
    _require_manifest_artifact_root(
        repo, manifest_path, value["run"]["artifact_root"], scenario=True
    )
    if value["phase"] != "collecting":
        raise CampaignError("scenario collection is already sealed")
    matching = [item for item in value["boots"] if item["boot_id"] == boot_id]
    if len(matching) != 1 or matching[0]["status"] != "planned":
        raise CampaignError("scenario boot is not pending")
    boot = matching[0]
    _require_repository_identity(repo, value["run"]["commit"], "before scenario archival")
    _evaluation_source_gate(
        repo,
        value["run"]["commit"],
        value["run"]["artifact_root"],
        "before scenario archival",
    )
    if _repo_relative(repo, runner_log) != boot["runner_log"] or _repo_relative(repo, host_summary) != boot["host_summary"]:
        raise CampaignError("scenario evidence paths differ from the plan")
    _require_regular_file(runner_log, "scenario runner log")
    boot["exit_code"] = exit_code
    boot["finished_at_utc"] = _utc_now()
    boot["runner_log_sha256"] = _sha256(runner_log)
    if exit_code == 0:
        _require_regular_file(host_summary, "scenario host summary")
        summary = _strict_json(host_summary)
        if summary.get("status") != "ready" or summary.get("challenge") != boot["challenge"] or summary.get("target_order") != boot["target_order"]:
            raise CampaignError("scenario host summary differs from the planned boot")
        for target in ("plain", "agentos"):
            if not isinstance(summary.get(target), dict) or summary[target].get("status") != "ready":
                raise CampaignError(f"scenario target did not complete: {target}")
        boot["host_summary_sha256"] = _sha256(host_summary)
        _require_repository_identity(
            repo, value["run"]["commit"], "after scenario archival"
        )
        _evaluation_source_gate(
            repo,
            value["run"]["commit"],
            value["run"]["artifact_root"],
            "after scenario archival",
        )
        boot["status"] = "passed"
    else:
        if host_summary.exists():
            _require_regular_file(host_summary, "failed scenario host summary", nonempty=False)
            boot["host_summary_sha256"] = _sha256(host_summary)
        boot["status"] = "failed"
    validate_scenario_campaign(value)
    _atomic_json(manifest_path, value)


def _verify_scenario_execution_binding(
    repo: Path, value: dict[str, Any], boot: dict[str, Any]
) -> None:
    _verify_platform_execution_binding(repo, value["platform"])
    protocol = value["protocol"]
    python_path = Path(protocol["python_bin"])
    _require_regular_file(python_path, "scenario Python executable")
    if _sha256(python_path) != protocol["python_sha256"]:
        raise CampaignError("scenario Python executable changed before boot")
    git_path = Path(protocol["git_bin"])
    _require_regular_file(git_path, "scenario Git executable")
    if _sha256(git_path) != protocol["git_sha256"]:
        raise CampaignError("scenario Git executable changed before boot")
    expected_driver = str(
        _resolved_safe_file(
            repo / protocol["input_driver_path"], "scenario input driver"
        )
    )
    if boot["command_argv"][:2] != [str(python_path), expected_driver]:
        raise CampaignError("scenario command does not use preflighted absolute inputs")
    posix_temporary, native_temporary = _platform_temporary_identity(
        value["platform"]
    )
    system_drive = _platform_system_drive(value["platform"]) or "/"
    observed = _probe_scenario_environment(
        repo,
        wsl_distro=protocol["wsl_distro"],
        toolprefix=protocol["execution_environment"]["tools"]["compiler"]["argv0"][:-3],
        qemu=protocol["execution_environment"]["tools"]["qemu"]["argv0"],
        posix_temporary=posix_temporary,
        native_temporary=native_temporary,
        system_drive=system_drive,
    )
    if observed != protocol["execution_environment"]:
        raise CampaignError("scenario execution environment changed before boot")


def execute_and_record_scenario_boot(
    *, repo: Path, manifest_path: Path, boot_id: str
) -> int:
    """Execute one scenario boot from its precommitted absolute command."""

    repo = _resolved_safe_directory(repo, "repository")
    manifest_path = _resolved_safe_file(manifest_path, "scenario campaign manifest")
    with exclusive_scenario_coordination_lock(repo):
        value = _strict_json(manifest_path)
        validate_scenario_campaign(value)
        _require_manifest_artifact_root(
            repo, manifest_path, value["run"]["artifact_root"], scenario=True
        )
        check_preflight_receipt(
            manifest_path,
            manifest_path.parent.parent / "scenario-preflight.log",
        )
        if value["phase"] != "collecting":
            raise CampaignError("scenario collection is already sealed")
        matching = [item for item in value["boots"] if item["boot_id"] == boot_id]
        if len(matching) != 1 or matching[0]["status"] != "planned":
            raise CampaignError("scenario boot is not uniquely planned and pending")
        boot = matching[0]
        _prepare_evaluation_build_tree(
            repo,
            value["run"]["commit"],
            value["run"]["artifact_root"],
            f"scenario {boot_id}",
        )
        runner_log = repo / boot["runner_log"]
        host_summary = repo / boot["host_summary"]
        runner_log = _safe_output_path(runner_log, "scenario runner log")
        host_summary = _safe_output_path(host_summary, "scenario Host summary")
        deadline = scenario_pair_deadline_contract(
            value["protocol"]["timeout_seconds"]
        )
        timeout_seconds = deadline["pair_deadline_seconds"]
        maximum_timeout_seconds = scenario_pair_deadline_contract(3600)[
            "pair_deadline_seconds"
        ]
        with _SourceIntegrityMonitor(
            repo, value["run"]["commit"],
            value["measurement_source_receipt"],
            value["run"]["artifact_root"],
        ):
            _verify_scenario_execution_binding(repo, value, boot)
            try:
                exit_code = _run_micro_process(
                    command=boot["command_argv"],
                    environment=boot["command_environment"],
                    repo=repo,
                    runner_log=runner_log,
                    timeout_seconds=timeout_seconds,
                    maximum_timeout_seconds=maximum_timeout_seconds,
                    deadline_label="scenario pair",
                )
            except OSError as error:
                exit_code = 127
                runner_log.write_text(
                    f"scenario runner launch failed: {error}\n",
                    encoding="utf-8",
                    newline="\n",
                )
        _verify_scenario_execution_binding(repo, value, boot)
        _record_scenario_boot_result(
            repo=repo,
            manifest_path=manifest_path,
            boot_id=boot_id,
            exit_code=exit_code,
            runner_log=runner_log,
            host_summary=host_summary,
        )
    return exit_code


def record_scenario_report(*, repo: Path, manifest_path: Path, report_path: Path) -> None:
    repo = _resolved_safe_directory(repo, "repository")
    manifest_path = _resolved_safe_file(manifest_path, "scenario campaign manifest")
    report_path = _resolved_safe_file(report_path, "scenario report")
    value = _strict_json(manifest_path)
    validate_scenario_campaign(value)
    _require_manifest_artifact_root(
        repo, manifest_path, value["run"]["artifact_root"], scenario=True
    )
    if any(boot["status"] != "passed" for boot in value["boots"]):
        raise CampaignError("scenario report cannot precede successful boots")
    expected_path = value["report"]["path"]
    if _repo_relative(repo, report_path) != expected_path:
        raise CampaignError("scenario report path differs from the plan")
    report = _strict_json(report_path)
    if report.get("status") not in {"supported", "regressed", "inconclusive"} or report.get("source_commit") != value["run"]["commit"] or report.get("run_id") != value["run"]["id"]:
        raise CampaignError("scenario collector did not produce a bound report")
    summary = report.get("summary")
    if not isinstance(summary, dict) or summary.get("independent_boots") != len(value["boots"]) or summary.get("unique_challenges") != len(value["boots"]) or summary.get("target_order_balanced") is not True:
        raise CampaignError("scenario report does not cover the planned balanced boots")
    value["report"]["sha256"] = _sha256(report_path)
    value["report"]["status"] = "recorded"
    validate_scenario_campaign(value)
    _atomic_json(manifest_path, value)


def seal_scenario_campaign(path: Path) -> None:
    value = _strict_json(path)
    validate_scenario_campaign(value)
    if any(boot["status"] != "passed" for boot in value["boots"]) or value["report"]["status"] != "recorded":
        raise CampaignError("scenario campaign is incomplete")
    value["phase"] = "collected"
    validate_scenario_campaign(value)
    _atomic_json(path, value)


def check_scenario_campaign(
    repo: Path, path: Path, micro_manifest: Path | None = None
) -> dict[str, Any]:
    repo = _resolved_safe_directory(repo, "repository")
    path = _resolved_safe_file(path, "scenario campaign manifest")
    value = _strict_json(path)
    validate_scenario_campaign(value)
    _require_manifest_artifact_root(
        repo, path, value["run"]["artifact_root"], scenario=True
    )
    if value["phase"] != "collected":
        raise CampaignError("scenario campaign is not collected")
    if micro_manifest is not None:
        micro = _strict_json(micro_manifest)
        validate_campaign(micro)
        if (
            micro["phase"] != "collected"
            or value["run"]["commit"] != micro["run"]["commit"]
            or value["run"]["id"] != micro["run"]["id"]
            or value["run"]["artifact_root"] != micro["run"]["artifact_root"]
            or value["run"]["environment_sha256"] != _environment_sha256(micro)
            or value["platform"] != micro["platform"]
            or value["run"]["platform_sha256"]
            != _canonical_sha256(micro["platform"])
            or value["measurement_source_receipt"]
            != micro["measurement_source_receipt"]
        ):
            raise CampaignError("scenario campaign differs from the micro campaign")
    commit, clean = repository_identity(repo)
    if commit != value["run"]["commit"] or not clean:
        raise CampaignError("source tree differs from the scenario campaign")
    _require_measurement_receipt(
        repo, commit, value["measurement_source_receipt"],
        "during scenario campaign check",
    )
    _verify_platform_execution_binding(repo, value["platform"])
    for path_key, hash_key in (
        ("collector_path", "collector_sha256"),
        ("input_driver_path", "input_driver_sha256"),
    ):
        source = repo / value["protocol"][path_key]
        _require_regular_file(source, f"scenario {path_key}")
        if _sha256(source) != value["protocol"][hash_key]:
            raise CampaignError(f"scenario input contract changed: {source}")
    posix_temporary, native_temporary = _platform_temporary_identity(
        value["platform"]
    )
    system_drive = _platform_system_drive(value["platform"]) or "/"
    observed_environment = _probe_scenario_environment(
        repo,
        wsl_distro=value["protocol"]["wsl_distro"],
        toolprefix=value["protocol"]["execution_environment"]["tools"]["compiler"]["argv0"][:-3],
        qemu=value["protocol"]["execution_environment"]["tools"]["qemu"]["argv0"],
        posix_temporary=posix_temporary,
        native_temporary=native_temporary,
        system_drive=system_drive,
    )
    if observed_environment != value["protocol"]["execution_environment"]:
        raise CampaignError("scenario execution environment changed after planning")
    python_path = Path(value["protocol"]["python_bin"])
    _require_regular_file(python_path, "scenario Python executable")
    if _sha256(python_path) != value["protocol"]["python_sha256"]:
        raise CampaignError("scenario Python executable changed after planning")
    git_path = Path(value["protocol"]["git_bin"])
    _require_regular_file(git_path, "scenario Git executable")
    if _sha256(git_path) != value["protocol"]["git_sha256"]:
        raise CampaignError("scenario Git executable changed after planning")
    for boot in value["boots"]:
        for path_key, hash_key in (("runner_log", "runner_log_sha256"), ("host_summary", "host_summary_sha256")):
            evidence = repo / boot[path_key]
            _require_regular_file(evidence, f"scenario {path_key}")
            if _sha256(evidence) != boot[hash_key]:
                raise CampaignError(f"scenario evidence changed: {evidence}")
    report = repo / value["report"]["path"]
    _require_regular_file(report, "scenario report")
    if _sha256(report) != value["report"]["sha256"]:
        raise CampaignError("scenario report changed after collection")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--repo", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--boots", type=int, required=True)
    create.add_argument("--toolprefix", required=True)
    create.add_argument("--qemu", required=True)
    create.add_argument("--python-bin", required=True)
    create.add_argument("--shell-bin", default="bash")
    create.add_argument("--timeout", type=int, required=True)

    run_boot = subparsers.add_parser("run-boot")
    run_boot.add_argument("--repo", type=Path, required=True)
    run_boot.add_argument("--manifest", type=Path, required=True)
    run_boot.add_argument("--boot-id", required=True)
    run_boot.add_argument("--timeout", type=int, required=True)

    locked = subparsers.add_parser("with-campaign-lock")
    locked.add_argument("--repo", type=Path, required=True)
    locked.add_argument("command_argv", nargs=argparse.REMAINDER)

    verify_lock = subparsers.add_parser("verify-campaign-lock")
    verify_lock.add_argument("--repo", type=Path, required=True)
    verify_lock.add_argument("--token", required=True)

    seal = subparsers.add_parser("seal")
    seal.add_argument("--manifest", type=Path, required=True)

    export = subparsers.add_parser("export-plan")
    export.add_argument("--manifest", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)

    check = subparsers.add_parser("check")
    check.add_argument("--repo", type=Path, required=True)
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--require-collected", action="store_true")

    preflight_check = subparsers.add_parser("check-preflight")
    preflight_check.add_argument("--manifest", type=Path, required=True)
    preflight_check.add_argument("--receipt", type=Path, required=True)

    boot_field = subparsers.add_parser("get-boot-field")
    boot_field.add_argument("--manifest", type=Path, required=True)
    boot_field.add_argument("--boot-id", required=True)
    boot_field.add_argument(
        "--field",
        choices=(
            "challenge", "guest_log", "runner_log", "kernel_path",
            "image_input_path", "image_final_path",
        ),
        required=True,
    )

    scenario_create = subparsers.add_parser("create-scenario")
    scenario_create.add_argument("--repo", type=Path, required=True)
    scenario_create.add_argument("--micro-manifest", type=Path, required=True)
    scenario_create.add_argument("--output", type=Path, required=True)
    scenario_create.add_argument("--boots", type=int, required=True)
    scenario_create.add_argument("--timeout", type=int, required=True)
    scenario_create.add_argument("--wsl-distro", required=True)

    scenario_field = subparsers.add_parser("get-scenario-field")
    scenario_field.add_argument("--manifest", type=Path, required=True)
    scenario_field.add_argument("--boot-id", required=True)
    scenario_field.add_argument(
        "--field",
        choices=("challenge", "target_order", "work_dir", "runner_log", "host_summary"),
        required=True,
    )

    scenario_metadata = subparsers.add_parser("get-scenario-metadata")
    scenario_metadata.add_argument("--manifest", type=Path, required=True)
    scenario_metadata.add_argument(
        "--field", choices=("boots", "run_id", "commit"), required=True
    )

    scenario_run = subparsers.add_parser("run-scenario-boot")
    scenario_run.add_argument("--repo", type=Path, required=True)
    scenario_run.add_argument("--manifest", type=Path, required=True)
    scenario_run.add_argument("--boot-id", required=True)

    scenario_report = subparsers.add_parser("record-scenario-report")
    scenario_report.add_argument("--repo", type=Path, required=True)
    scenario_report.add_argument("--manifest", type=Path, required=True)
    scenario_report.add_argument("--report", type=Path, required=True)

    scenario_seal = subparsers.add_parser("seal-scenario")
    scenario_seal.add_argument("--manifest", type=Path, required=True)

    scenario_check = subparsers.add_parser("check-scenario")
    scenario_check.add_argument("--repo", type=Path, required=True)
    scenario_check.add_argument("--manifest", type=Path, required=True)
    scenario_check.add_argument("--micro-manifest", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            campaign = create_campaign(
                repo=args.repo,
                output=args.output,
                run_id=args.run_id,
                requested_boots=args.boots,
                toolprefix=args.toolprefix,
                qemu=args.qemu,
                python_bin=args.python_bin,
                shell_bin=args.shell_bin,
                timeout_seconds=args.timeout,
            )
            _write_ascii_stdout(format_preflight_receipt(campaign))
        elif args.command == "run-boot":
            return execute_and_record_boot(
                repo=args.repo,
                manifest_path=args.manifest,
                boot_id=args.boot_id,
                timeout_seconds=args.timeout,
            )
        elif args.command == "with-campaign-lock":
            return execute_under_campaign_lock(
                repo=args.repo, command=args.command_argv
            )
        elif args.command == "verify-campaign-lock":
            verify_campaign_lock_lease(repo=args.repo, token=args.token)
        elif args.command == "seal":
            seal_campaign(args.manifest)
        elif args.command == "export-plan":
            export_run_plan(args.manifest, args.output)
        elif args.command == "check":
            check_campaign(
                args.repo, args.manifest, require_collected=args.require_collected
            )
        elif args.command == "check-preflight":
            check_preflight_receipt(args.manifest, args.receipt)
        elif args.command == "get-boot-field":
            print(get_boot_field(args.manifest, args.boot_id, args.field))
        elif args.command == "create-scenario":
            campaign = create_scenario_campaign(
                repo=args.repo,
                micro_manifest=args.micro_manifest,
                output=args.output,
                requested_boots=args.boots,
                timeout_seconds=args.timeout,
                wsl_distro=args.wsl_distro,
            )
            _write_ascii_stdout(format_preflight_receipt(campaign))
        elif args.command == "get-scenario-field":
            print(get_scenario_boot_field(args.manifest, args.boot_id, args.field))
        elif args.command == "get-scenario-metadata":
            print(get_scenario_metadata(args.manifest, args.field))
        elif args.command == "run-scenario-boot":
            return execute_and_record_scenario_boot(
                repo=args.repo,
                manifest_path=args.manifest,
                boot_id=args.boot_id,
            )
        elif args.command == "record-scenario-report":
            record_scenario_report(
                repo=args.repo,
                manifest_path=args.manifest,
                report_path=args.report,
            )
        elif args.command == "seal-scenario":
            seal_scenario_campaign(args.manifest)
        elif args.command == "check-scenario":
            check_scenario_campaign(
                args.repo, args.manifest, micro_manifest=args.micro_manifest
            )
        else:  # pragma: no cover - argparse enforces the command set.
            raise CampaignError(f"unsupported command: {args.command}")
    except CampaignError as error:
        print(f"evaluation campaign error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
