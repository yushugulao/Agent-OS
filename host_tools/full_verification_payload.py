#!/usr/bin/env python3
"""Collect and replay a stage-only, commit-bound ``make full-verify`` payload."""

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
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

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

from evidence_toolchain_attestation import (
    EVALUATION_ARTIFACT_OUTPUT_FILES,
    EVALUATION_BUILD_OUTPUT_FILES,
    EVALUATION_BUILD_OUTPUT_ROOTS,
    EVALUATION_CACHE_OUTPUT_ROOTS,
    ToolAttestationError,
    capture_version,
    capture_formal_temporary_binding,
    controlled_environment,
    create_isolated_detached_worktree,
    decode_external_output,
    purge_evaluation_generated_outputs,
    require_nested_tool_resolution,
    resolve_bash_executable,
    resolve_executable,
    verify_evaluation_source_tree,
    verify_tool_attestations,
)
from safe_host_paths import (
    absolute_lexical_path,
    path_is_link,
    reject_link_components,
    require_regular_file,
    require_safe_directory,
    walk_regular_files_no_links,
)
from strict_json import read_strict_json
from formal_python_runtime import (
    FormalPythonRuntimeError,
    create_formal_python_runtime,
    formal_execution_overrides,
    validate_duration_profile_policy_marker,
    validate_formal_evidence_binding,
)
from duration_profile_attestation import (
    DurationAttestationError, build_duration_attestation,
    duration_attestation_sha256, validate_duration_attestation,
    validate_duration_execution_binding,
)


KIND = "agentos-full-verification-stage"
SCHEMA_VERSION = 3
RECEIPT_NAME = "receipt.json"
SUMMARY_NAME = "verification-summary.json"
CHECKSUM_NAME = "checksums.sha256"
LOG_NAME = "logs/full-verify.log"
RAW_ROOT = "logs/raw"
ENVIRONMENT_NAME = "environment/environment.json"
SUCCESS_MARKER = "[full-verify] all checks passed"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_FILES = 512
MAX_FILE_BYTES = 1 << 30
MAX_TOTAL_BYTES = 2 << 30
MAX_EXECUTION_LOG_BYTES = 64 << 20
MAX_EXECUTION_OUTPUT_BYTES = 4 << 30
TOOL_LABELS = {"git", "compiler", "qemu", "python", "make", "bash", "host_cc"}
RECEIPT_FIELDS = {
    "schema_version",
    "kind",
    "source_commit",
    "created_at_utc",
    "execution",
    "verification_summary",
    "raw_artifacts",
    "environment",
    "duration_attestation",
}


class FullVerificationError(ValueError):
    """Raised when full-verification evidence is absent, incomplete, or forged."""


_COLLECTOR: ModuleType | None = None


def _source_gate(
    git: Path,
    repository: Path,
    worktree: Path,
    commit: str,
    environment: dict[str, str],
    stage: str,
    *,
    output_roots: tuple[str, ...] = (),
) -> None:
    try:
        verify_evaluation_source_tree(
            git,
            repository,
            worktree,
            commit,
            environment,
            allowed_output_roots=(
                *EVALUATION_BUILD_OUTPUT_ROOTS,
                *EVALUATION_CACHE_OUTPUT_ROOTS,
                *output_roots,
            ),
            allowed_output_files=(
                *EVALUATION_BUILD_OUTPUT_FILES,
                *EVALUATION_ARTIFACT_OUTPUT_FILES,
            ),
            stage=stage,
        )
    except (OSError, ToolAttestationError) as error:
        raise FullVerificationError(
            f"full-verification source gate failed {stage}: {error}"
        ) from error


def _source_checkout_output_roots(repo: Path, output: Path) -> tuple[str, ...]:
    try:
        run_root = output.parent.relative_to(repo)
    except ValueError:
        return ()
    return (PurePosixPath(*run_root.parts).as_posix(),)


def _load_collector(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise FullVerificationError("full-verify evidence contract is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collector() -> ModuleType:
    """Load the canonical full-verify summary contract without duplicating it."""

    global _COLLECTOR
    if _COLLECTOR is not None:
        return _COLLECTOR
    path = Path(__file__).resolve().parents[1] / "scripts" / "capture-final-evidence.py"
    module = _load_collector(path, "agentos_capture_final_evidence")
    _COLLECTOR = module
    return module


def _contract_root(path: Path) -> Path:
    lexical = absolute_lexical_path(path)
    try:
        reject_link_components(lexical)
        root = require_safe_directory(lexical).resolve(strict=True)
        require_regular_file(root / "scripts" / "capture-final-evidence.py")
        require_regular_file(root / "scripts" / "trusted-python-entry.py")
    except (OSError, ValueError) as error:
        raise FullVerificationError(
            "full-verification semantic contract snapshot is missing or unsafe"
        ) from error
    return root


def _replay_semantics(
    raw_root: Path,
    summary_path: Path,
    commit: str,
    contract_root: Path,
) -> None:
    """Run the complete raw verifier from the authenticated source-C snapshot."""

    root = _contract_root(contract_root)
    launcher = root / "scripts" / "trusted-python-entry.py"
    environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in ("SYSTEMROOT", "WINDIR"):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    command = [
        sys.executable,
        "-I",
        "-S",
        str(launcher),
        "scripts/capture-final-evidence.py",
        "replay-raw",
        "--raw-dir",
        str(raw_root),
        "--summary",
        str(summary_path),
        "--expected-commit",
        commit,
    ]
    try:
        with tempfile.TemporaryDirectory(
            prefix="agentos-full-verify-replay-"
        ) as temporary:
            inherited_temporary = getattr(sys, "_agentos_temporary_root", None)
            if inherited_temporary is None:
                replay_temporary = Path(temporary).resolve(strict=True)
            else:
                try:
                    replay_temporary = require_safe_directory(
                        absolute_lexical_path(Path(inherited_temporary))
                    ).resolve(strict=True)
                except (OSError, ValueError) as error:
                    raise FullVerificationError(
                        "trusted semantic-replay temporary root is unsafe"
                    ) from error
                if str(replay_temporary) != inherited_temporary:
                    raise FullVerificationError(
                        "trusted semantic-replay temporary root is noncanonical"
                    )
            environment["TMPDIR"] = str(replay_temporary)
            replay_log = Path(temporary) / "semantic-replay.log"
            returncode, _elapsed, timed_out = _run_bounded(
                command,
                root,
                environment,
                replay_log,
                300,
                (),
                log_limit=8 << 20,
            )
            detail = replay_log.read_bytes()[-8192:].decode(
                "utf-8", errors="replace"
            ).strip()
    except FullVerificationError as error:
        raise FullVerificationError(
            f"full-verification semantic replay could not run safely: {error}"
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise FullVerificationError(
            f"full-verification semantic replay could not run: {error}"
        ) from error
    if timed_out:
        raise FullVerificationError("full-verification semantic replay timed out")
    if returncode != 0:
        raise FullVerificationError(
            "full-verification semantic replay failed"
            + (f": {detail}" if detail else "")
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_tree_bytes(roots: tuple[Path, ...], limit: int) -> int:
    total = 0
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_symlink():
                raise FullVerificationError(
                    f"full-verification execution created a link-backed output: {path}"
                )
            if path.is_file():
                total += path.stat().st_size
                if total > limit:
                    return total
    return total


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


def _process_group_alive(pid: int) -> bool:
    if os.name != "posix":
        return False
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _run_bounded(
    command: list[str],
    cwd: Path,
    environment: dict[str, str],
    log_path: Path,
    timeout: float,
    output_roots: tuple[Path, ...],
    *,
    log_limit: int = MAX_EXECUTION_LOG_BYTES,
    output_limit: int = MAX_EXECUTION_OUTPUT_BYTES,
) -> tuple[int, float, bool]:
    """Run one isolated session with online disk budgets and no surviving children."""

    if os.name != "posix":
        raise FullVerificationError(
            "full-verification bounded execution requires POSIX process groups"
        )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    reason: str | None = None
    with log_path.open("xb") as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                elapsed = time.monotonic() - started
                log.flush()
                if elapsed > timeout:
                    reason = "timed out"
                    break
                if log_path.stat().st_size > log_limit:
                    reason = "exceeded the online log budget"
                    break
                if _bounded_tree_bytes(output_roots, output_limit) > output_limit:
                    reason = "exceeded the online output budget"
                    break
                time.sleep(0.2)
            if reason is not None:
                _terminate_process_group(process)
            returncode = process.wait()
            elapsed = time.monotonic() - started
            log.flush()
            if reason is None and log_path.stat().st_size > log_limit:
                reason = "exceeded the online log budget"
            if (
                reason is None
                and _bounded_tree_bytes(output_roots, output_limit) > output_limit
            ):
                reason = "exceeded the online output budget"
            if reason is None and _process_group_alive(process.pid):
                _terminate_process_group(process)
                reason = "left a live descendant in its execution session"
            if reason is not None and reason != "timed out":
                raise FullVerificationError(f"full-verification command {reason}")
            return returncode, elapsed, reason == "timed out"
        finally:
            if process.poll() is None or _process_group_alive(process.pid):
                _terminate_process_group(process)


def _safe_relative(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise FullVerificationError(f"{label} path is invalid")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise FullVerificationError(f"{label} path is unsafe: {value!r}")
    return path


def _regular_file(root: Path, relative: object, label: str) -> Path:
    safe = _safe_relative(relative, label)
    try:
        path = require_regular_file(root.joinpath(*safe.parts))
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise FullVerificationError(f"{label} is missing or link-backed") from error
    if path.stat().st_size > MAX_FILE_BYTES:
        raise FullVerificationError(f"{label} exceeds its byte budget")
    return path


def _file_record(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _validate_file_record(
    root: Path, value: object, expected_path: str, label: str, *, positive: bool = True
) -> Path:
    if not isinstance(value, dict) or set(value) != {"path", "bytes", "sha256"}:
        raise FullVerificationError(f"{label} receipt is invalid")
    path = _regular_file(root, value.get("path"), label)
    size = value.get("bytes")
    if (
        value.get("path") != expected_path
        or type(size) is not int
        or size < (1 if positive else 0)
        or path.stat().st_size != size
        or not isinstance(value.get("sha256"), str)
        or SHA256_RE.fullmatch(str(value.get("sha256"))) is None
        or _sha256(path) != value["sha256"]
    ):
        raise FullVerificationError(f"{label} receipt differs from its bytes")
    return path


def _inventory(root: Path) -> list[Path]:
    try:
        files = walk_regular_files_no_links(
            root,
            max_files=MAX_FILES,
            max_total_bytes=MAX_TOTAL_BYTES,
            max_depth=12,
        )
    except (OSError, ValueError) as error:
        raise FullVerificationError(f"full-verification payload is unsafe: {error}") from error
    if len(files) > MAX_FILES:
        raise FullVerificationError("full-verification payload has too many files")
    total = sum(path.stat().st_size for path in files)
    if total > MAX_TOTAL_BYTES:
        raise FullVerificationError("full-verification payload exceeds its byte budget")
    return files


def _tree_sha256(records: list[dict[str, object]]) -> str:
    canonical = json.dumps(
        records, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_checksums(root: Path) -> None:
    checksum = root / CHECKSUM_NAME
    files = sorted(
        (path for path in _inventory(root) if path != checksum),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    checksum.write_text(
        "".join(
            f"{_sha256(path)}  {path.relative_to(root).as_posix()}\n" for path in files
        ),
        encoding="ascii",
        newline="\n",
    )


def _verify_checksum_inventory(root: Path) -> list[dict[str, object]]:
    checksum = _regular_file(root, CHECKSUM_NAME, "checksum inventory")
    expected: dict[str, str] = {}
    observed: list[str] = []
    lines = checksum.read_text(encoding="ascii").splitlines()
    for line_number, line in enumerate(lines, 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise FullVerificationError(f"invalid checksum row {line_number}")
        relative = _safe_relative(match.group(2), "checksum")
        canonical = relative.as_posix()
        if canonical == CHECKSUM_NAME or canonical in expected:
            raise FullVerificationError(f"duplicate checksum row {line_number}")
        expected[canonical] = match.group(1)
        observed.append(canonical)
    if observed != sorted(observed):
        raise FullVerificationError("full-verification checksum order is not canonical")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in _inventory(root)
        if path != checksum
    }
    if set(expected) != actual_paths:
        raise FullVerificationError("full-verification checksum inventory differs")
    records: list[dict[str, object]] = []
    for relative in sorted(expected):
        path = _regular_file(root, relative, "checksummed payload")
        digest = _sha256(path)
        if digest != expected[relative]:
            raise FullVerificationError(f"full-verification checksum differs: {relative}")
        records.append(
            {"path": relative, "bytes": path.stat().st_size, "sha256": digest}
        )
    return records


def _validate_tools(
    root: Path,
    environment_path: Path,
    execution: dict[str, Any],
    contract_root: Path,
) -> tuple[str, dict[str, dict[str, object]]]:
    try:
        environment = read_strict_json(environment_path)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise FullVerificationError("full-verification environment is invalid") from error
    try:
        tool_by_label = validate_formal_evidence_binding(
            environment, environment.get("execution_environment") if isinstance(environment, dict) else None,
            execution.get("python_bin"), execution.get("python_path_resolution"),
            contract_root, TOOL_LABELS,
        )
    except (OSError, FormalPythonRuntimeError) as error:
        raise FullVerificationError(
            f"full-verification execution environment is invalid: {error}"
        ) from error
    tools = environment["tools"]
    version_paths: set[str] = set()
    for label, record in tool_by_label.items():
        expected = f"environment/versions/{label}.txt"
        path = _validate_file_record(
            root,
            {
                "path": record.get("log"),
                "bytes": _regular_file(root, expected, "tool version log").stat().st_size,
                "sha256": record.get("log_sha256"),
            },
            expected,
            "tool version log",
        )
        lines = decode_external_output(path.read_bytes()).splitlines()
        if (
            set(record) != {
                "label",
                "path",
                "executable_sha256",
                "log",
                "log_sha256",
                "first_line",
            }
            or record.get("label") != label
            or not isinstance(record.get("path"), str)
            or not record["path"]
            or SHA256_RE.fullmatch(str(record.get("executable_sha256", ""))) is None
            or not lines
            or record.get("first_line") != lines[0]
        ):
            raise FullVerificationError(f"full-verification tool record is invalid: {label}")
        version_paths.add(expected)
    actual_versions = {
        path.relative_to(root).as_posix()
        for path in _inventory(root)
        if path.relative_to(root).as_posix().startswith("environment/versions/")
    }
    if actual_versions != version_paths:
        raise FullVerificationError("full-verification version-log inventory differs")
    argv = execution.get("argv")
    compiler = str(tool_by_label["compiler"].get("path", ""))
    if (
        not isinstance(argv, list)
        or len(argv) != 3
        or argv[0] != tool_by_label["make"].get("path")
        or argv[1] != "full-verify"
        or not compiler.endswith("gcc")
        or argv[2] != f"TOOLPREFIX={compiler[:-3]}"
    ):
        raise FullVerificationError("full-verification command argv is not canonical")
    return (
        str(environment["execution_environment"]["AGENT_TEST_DURATION_PROFILE"]),
        tool_by_label,
    )


def verify_payload(
    root: Path, *, expected_commit: str | None = None, contract_root: Path
) -> tuple[dict[str, object], set[str]]:
    """Replay a stage payload and return its outer-bundle binding receipt."""

    lexical = absolute_lexical_path(root)
    try:
        reject_link_components(lexical)
        root = require_safe_directory(lexical).resolve(strict=True)
    except (OSError, ValueError) as error:
        raise FullVerificationError("full-verification payload is missing or link-backed") from error
    records = _verify_checksum_inventory(root)
    try:
        receipt = read_strict_json(_regular_file(root, RECEIPT_NAME, "receipt"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise FullVerificationError("full-verification receipt is invalid") from error
    if (
        not isinstance(receipt, dict)
        or set(receipt) != RECEIPT_FIELDS
        or receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("kind") != KIND
    ):
        raise FullVerificationError("full-verification receipt schema differs")
    commit = receipt.get("source_commit")
    if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None:
        raise FullVerificationError("full-verification source commit is invalid")
    if expected_commit is not None and commit != expected_commit:
        raise FullVerificationError("full-verification commit differs from evaluation campaign")
    created = receipt.get("created_at_utc")
    if (
        not isinstance(created, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created) is None
    ):
        raise FullVerificationError("full-verification receipt timestamp is invalid")

    summary_path = _validate_file_record(
        root, receipt.get("verification_summary"), SUMMARY_NAME, "verification summary"
    )
    try:
        summary = read_strict_json(summary_path)
    except (OSError, UnicodeDecodeError, ValueError, KeyError) as error:
        raise FullVerificationError(f"full-verification summary is invalid: {error}") from error
    if not isinstance(summary, dict) or summary.get("commit") != commit:
        raise FullVerificationError("full-verification summary commit differs from its receipt")

    execution = receipt.get("execution")
    if not isinstance(execution, dict) or set(execution) != {
        "isolation",
        "argv",
        "returncode",
        "elapsed_seconds",
        "timeout_seconds",
        "timed_out",
        "python_bin",
        "python_path_resolution",
        "log",
    }:
        raise FullVerificationError("full-verification execution receipt is invalid")
    elapsed, timeout = execution.get("elapsed_seconds"), execution.get("timeout_seconds")
    if (
        execution.get("isolation") != "clean-detached-worktree"
        or execution.get("returncode") != 0
        or execution.get("timed_out") is not False
        or isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(float(elapsed))
        or float(elapsed) < 0
        or isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or not 0 < float(timeout) <= 86400
    ):
        raise FullVerificationError("full-verification execution did not succeed")
    environment_record = read_strict_json(root / ENVIRONMENT_NAME)
    launch = environment_record["python_launch"]
    if (
        execution.get("python_bin") != launch["shim"]["path"]
        or execution.get("python_path_resolution")
        != dict(zip(("python", "python3"), launch["shim"]["aliases"], strict=True))
    ):
        raise FullVerificationError("full-verification Python execution binding differs")
    log_path = _validate_file_record(root, execution.get("log"), LOG_NAME, "full-verify log")
    log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if log_lines.count(SUCCESS_MARKER) != 1:
        raise FullVerificationError(
            "full-verification completion marker is missing or duplicated"
        )

    raw = receipt.get("raw_artifacts")
    summary_artifacts = summary.get("artifacts")
    if (
        not isinstance(raw, list)
        or not isinstance(summary_artifacts, list)
        or len(raw) != len(summary_artifacts)
    ):
        raise FullVerificationError("full-verification raw inventory is incomplete")
    by_name = {item.get("name"): item for item in summary_artifacts if isinstance(item, dict)}
    names: set[str] = set()
    for record in raw:
        if not isinstance(record, dict) or set(record) != {"name", "path", "bytes", "sha256"}:
            raise FullVerificationError("full-verification raw receipt is invalid")
        name = record.get("name")
        expected = by_name.get(name)
        if (
            not isinstance(name, str)
            or not name
            or name in names
            or expected is None
            or record.get("bytes") != expected.get("bytes")
            or record.get("sha256") != expected.get("sha256")
        ):
            raise FullVerificationError("full-verification raw receipt differs from summary")
        _validate_file_record(
            root,
            {key: record[key] for key in ("path", "bytes", "sha256")},
            f"{RAW_ROOT}/{name}",
            f"raw artifact {name}",
        )
        names.add(name)
    if names != set(by_name):
        raise FullVerificationError("full-verification raw inventory differs from summary")

    environment_path = _validate_file_record(
        root, receipt.get("environment"), ENVIRONMENT_NAME, "environment"
    )
    duration_profile, execution_tools = _validate_tools(
        root, environment_path, execution, contract_root
    )
    try:
        attested_profile = validate_duration_attestation(
            receipt["duration_attestation"], contract_root=contract_root
        )
    except (DurationAttestationError, OSError, ValueError) as error:
        raise FullVerificationError(
            f"full-verification duration attestation is invalid: {error}"
        ) from error
    if attested_profile != duration_profile:
        raise FullVerificationError(
            "full-verification duration attestation differs from execution"
        )
    try:
        validate_duration_execution_binding(
            receipt["duration_attestation"], execution_tools
        )
    except DurationAttestationError as error:
        raise FullVerificationError(
            f"full-verification duration execution binding is invalid: {error}"
        ) from error
    try:
        validate_duration_profile_policy_marker(
            {"AGENT_TEST_DURATION_PROFILE": duration_profile}, log_lines
        )
    except FormalPythonRuntimeError as error:
        raise FullVerificationError(str(error)) from error
    required = {
        RECEIPT_NAME,
        SUMMARY_NAME,
        CHECKSUM_NAME,
        LOG_NAME,
        ENVIRONMENT_NAME,
        *(f"{RAW_ROOT}/{name}" for name in names),
        *(f"environment/versions/{label}.txt" for label in TOOL_LABELS),
    }
    actual = {path.relative_to(root).as_posix() for path in _inventory(root)}
    if actual != required:
        raise FullVerificationError("full-verification payload inventory differs")
    _replay_semantics(root / RAW_ROOT, summary_path, commit, contract_root)

    all_records = sorted(
        [_file_record(root, path) for path in _inventory(root)],
        key=lambda item: str(item["path"]),
    )
    binding = {
        "status": "verified",
        "source_commit": commit,
        "agent_test_duration_profile": duration_profile,
        "duration_attestation_sha256": duration_attestation_sha256(
            receipt["duration_attestation"]
        ),
        "duration_platform_identity_sha256": receipt["duration_attestation"].get(
            "platform_identity_sha256"
        ),
        "payload_root": "full-verification",
        "receipt_sha256": _sha256(root / RECEIPT_NAME),
        "summary_sha256": _sha256(root / SUMMARY_NAME),
        "checksums_sha256": _sha256(root / CHECKSUM_NAME),
        "file_count": len(all_records),
        "total_bytes": sum(int(item["bytes"]) for item in all_records),
        "tree_sha256": _tree_sha256(all_records),
    }
    return binding, actual


def _copy_regular(source: Path, destination: Path) -> None:
    try:
        source = require_regular_file(source)
    except (OSError, ValueError) as error:
        raise FullVerificationError(f"full-verification input is unsafe: {source}") from error
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or path_is_link(destination):
        raise FullVerificationError(f"refusing to replace staged evidence: {destination}")
    shutil.copyfile(source, destination)


def _seal_payload(
    stage: Path,
    incoming: Path,
    full_log: Path,
    *,
    commit: str,
    command: list[str],
    returncode: int,
    elapsed: float,
    timeout: float,
    timed_out: bool,
    tools: list[dict[str, object]],
    python_launch: dict[str, object],
    python_path_resolution: dict[str, str],
    execution_environment: dict[str, str],
    temporary_directory_binding: dict[str, object],
    duration_attestation: dict[str, object],
    source_tree: Path,
    collector: ModuleType,
) -> None:
    if returncode != 0 or timed_out:
        raise FullVerificationError(f"make full-verify failed with rc={returncode}")
    summary_source = incoming / SUMMARY_NAME
    try:
        summary = collector.validate_summary(read_strict_json(summary_source))
    except (collector.EvidenceError, OSError, UnicodeDecodeError, ValueError, KeyError) as error:
        raise FullVerificationError(f"full-verify did not publish a valid summary: {error}") from error
    if summary.get("commit") != commit:
        raise FullVerificationError("full-verify summary is not bound to detached HEAD")
    _replay_semantics(incoming, summary_source, commit, source_tree)
    if full_log.read_text(encoding="utf-8", errors="replace").splitlines().count(
        SUCCESS_MARKER
    ) != 1:
        raise FullVerificationError(
            "full-verification completion marker is missing or duplicated"
        )

    _copy_regular(summary_source, stage / SUMMARY_NAME)
    _copy_regular(full_log, stage / LOG_NAME)
    raw_records: list[dict[str, object]] = []
    for summary_record in summary["artifacts"]:
        name = str(summary_record["name"])
        source = incoming / name
        if (
            not source.is_file()
            or source.is_symlink()
            or source.stat().st_size != summary_record["bytes"]
            or _sha256(source) != summary_record["sha256"]
        ):
            raise FullVerificationError(f"full-verify artifact differs: {name}")
        destination = stage / RAW_ROOT / name
        _copy_regular(source, destination)
        raw_records.append(
            {
                "name": name,
                "path": f"{RAW_ROOT}/{name}",
                "bytes": summary_record["bytes"],
                "sha256": summary_record["sha256"],
            }
        )
    environment_path = stage / ENVIRONMENT_NAME
    _write_json(
        environment_path,
        {
            "captured_at_utc": collector.utc_now(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python_runtime": sys.version,
            "python_launch": python_launch,
            "python_path_resolution": python_path_resolution,
            "execution_environment": execution_environment,
            "temporary_directory_binding": temporary_directory_binding,
            "tools": tools,
        },
    )
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "source_commit": commit,
        "created_at_utc": collector.utc_now(),
        "execution": {
            "isolation": "clean-detached-worktree",
            "argv": command,
            "returncode": returncode,
            "elapsed_seconds": round(elapsed, 9),
            "timeout_seconds": timeout,
            "timed_out": timed_out,
            "python_bin": python_launch["shim"]["path"],
            "python_path_resolution": python_path_resolution,
            "log": _file_record(stage, stage / LOG_NAME),
        },
        "verification_summary": _file_record(stage, stage / SUMMARY_NAME),
        "raw_artifacts": raw_records,
        "environment": _file_record(stage, environment_path),
        "duration_attestation": duration_attestation,
    }
    _write_json(stage / RECEIPT_NAME, receipt)


def collect(args: argparse.Namespace) -> int:
    """Execute full-verify once in detached C and atomically seal its raw evidence."""

    timeout = float(args.command_timeout)
    if not math.isfinite(timeout) or not 0 < timeout <= 86400:
        raise FullVerificationError("full-verify command timeout is invalid")
    repo = absolute_lexical_path(Path(args.repo_root))
    output = absolute_lexical_path(Path(args.output))
    try:
        repo = require_safe_directory(repo).resolve(strict=True)
        reject_link_components(output)
        require_safe_directory(output.parent)
    except (OSError, ValueError) as error:
        raise FullVerificationError("repository or output path is unsafe") from error
    failed_output = output.with_name(output.name + ".failed")
    if (
        output.exists()
        or path_is_link(output)
        or failed_output.exists()
        or path_is_link(failed_output)
    ):
        raise FullVerificationError("full-verification output already exists")
    git = resolve_executable(args.git)
    compiler = resolve_executable(args.toolprefix + "gcc")
    if not str(compiler).endswith("gcc"):
        raise FullVerificationError("TOOLPREFIX compiler must end in gcc")
    tools = {
        "git": git,
        "compiler": compiler,
        "qemu": resolve_executable(args.qemu),
        "python": resolve_executable(args.python),
        "make": resolve_executable(args.make),
        "bash": resolve_bash_executable(args.bash, git),
        "host_cc": resolve_executable(args.host_cc),
    }
    environment_root = Path(tempfile.mkdtemp(prefix="agentos-full-verify-env-"))
    checkout_root = Path(tempfile.mkdtemp(prefix="agentos-full-verify-checkout-"))
    stage: Path | None = None
    output_lock = None
    collector = _collector()
    try:
        tool_dirs = [tools[label].parent for label in (
            "make", "git", "bash", "python", "host_cc", "compiler", "qemu"
        )]
        base_env = controlled_environment(environment_root, tool_dirs)
        require_nested_tool_resolution(tools, base_env)
        commit = collector.require_clean_head(git, repo, base_env)
        if args.expected_commit is not None and commit != args.expected_commit:
            raise FullVerificationError(
                "full-verification HEAD differs from the evaluation campaign"
            )
        _source_gate(
            git,
            repo,
            repo,
            commit,
            base_env,
            "before detached checkout",
            output_roots=_source_checkout_output_roots(repo, output),
        )
        try:
            output_lock = collector.acquire_output_lock(output)
        except collector.EvidenceError as error:
            raise FullVerificationError(str(error)) from error
        stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
        versions = [capture_version(label, path, stage, base_env) for label, path in tools.items()]
        _isolated, worktree = create_isolated_detached_worktree(
            git, repo, commit, checkout_root, base_env
        )
        detached_collector = _load_collector(
            worktree / "scripts" / "capture-final-evidence.py",
            f"agentos_capture_final_evidence_{commit}",
        )
        _source_gate(
            git,
            _isolated,
            worktree,
            commit,
            base_env,
            "before trusted purge",
        )
        try:
            purge_evaluation_generated_outputs(
                git,
                _isolated,
                worktree,
                commit,
                base_env,
                output_roots=(
                    *EVALUATION_BUILD_OUTPUT_ROOTS,
                    *EVALUATION_CACHE_OUTPUT_ROOTS,
                ),
                output_files=EVALUATION_BUILD_OUTPUT_FILES,
            )
        except (OSError, ToolAttestationError) as error:
            raise FullVerificationError(
                f"full-verification build purge failed: {error}"
            ) from error
        _source_gate(
            git,
            _isolated,
            worktree,
            commit,
            base_env,
            "after trusted purge",
        )
        duration_attestation = build_duration_attestation(
            contract_root=worktree,
            profile=args.agent_test_duration_profile,
            toolprefix=args.toolprefix,
            qemu=args.qemu,
            python_bin=args.python,
            host_cc=args.host_cc,
            shell_bin=args.bash,
        )
        validate_duration_execution_binding(
            duration_attestation,
            {record["label"]: record for record in versions},
        )
        python_runtime = create_formal_python_runtime(
            root=environment_root,
            real_python=tools["python"],
            shell=tools["bash"],
            git=git,
            repository=_isolated,
            worktree=worktree,
            commit=commit,
            environment=base_env,
        )
        evidence_stage = stage / "runtime" / "evidence-stage"
        (evidence_stage / "incoming").mkdir(parents=True)
        full_log = stage / "runtime" / "full-verify.log"
        command = [str(tools["make"]), "full-verify", f"TOOLPREFIX={str(compiler)[:-3]}"]
        selected = formal_execution_overrides(
            evidence_stage, tools, python_runtime.executable,
            args.case_timeout, args.idle_notice, args.agent_test_duration_profile,
        )
        execution_env = controlled_environment(
            environment_root, [python_runtime.directory, *tool_dirs], selected
        )
        temporary_directory_binding = capture_formal_temporary_binding(execution_env)
        python_path_resolution = python_runtime.path_resolution(execution_env)
        verify_tool_attestations(tools, versions, stage, base_env, "before execution")
        python_runtime.verify("before execution")
        monitored_roots = tuple(
            worktree / relative
            for relative in (
                *EVALUATION_BUILD_OUTPUT_ROOTS,
                *EVALUATION_CACHE_OUTPUT_ROOTS,
            )
        ) + (evidence_stage,)
        returncode, elapsed, timed_out = _run_bounded(
            command,
            worktree,
            execution_env,
            full_log,
            timeout,
            monitored_roots,
        )
        verify_tool_attestations(tools, versions, stage, base_env, "during execution")
        python_runtime.verify("after execution")
        if capture_formal_temporary_binding(execution_env) != temporary_directory_binding:
            raise FullVerificationError(
                "formal temporary directory identity changed during execution"
            )
        _source_gate(
            git,
            _isolated,
            worktree,
            commit,
            base_env,
            "after full-verify execution",
        )
        _seal_payload(
            stage,
            evidence_stage / "incoming",
            full_log,
            commit=commit,
            command=command,
            returncode=returncode,
            elapsed=elapsed,
            timeout=timeout,
            timed_out=timed_out,
            tools=versions,
            python_launch=python_runtime.record,
            python_path_resolution=python_path_resolution,
            execution_environment=execution_env,
            temporary_directory_binding=temporary_directory_binding,
            duration_attestation=duration_attestation,
            source_tree=worktree,
            collector=detached_collector,
        )
        shutil.rmtree(stage / "runtime")
        _write_checksums(stage)
        verify_payload(stage, expected_commit=commit, contract_root=worktree)
        verify_tool_attestations(tools, versions, stage, base_env, "before publication")
        python_runtime.verify("before publication")
        if output.exists() or path_is_link(output):
            raise FullVerificationError("full-verification output appeared during collection")
        os.replace(stage, output)
        print(f"[full-verification] stage payload: {output}")
        return 0
    except FullVerificationError:
        failed = output.with_name(output.name + ".failed")
        if stage is not None and stage.exists() and not failed.exists():
            os.replace(stage, failed)
        raise
    except (ToolAttestationError, OSError, subprocess.SubprocessError) as error:
        failed = output.with_name(output.name + ".failed")
        if stage is not None and stage.exists() and not failed.exists():
            os.replace(stage, failed)
        raise FullVerificationError(f"full-verification collection failed: {error}") from error
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        collector.release_output_lock(output_lock)
        shutil.rmtree(checkout_root, ignore_errors=True)
        shutil.rmtree(environment_root, ignore_errors=True)


def _add_collect_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-commit")
    parser.add_argument("--git", default="git")
    parser.add_argument("--toolprefix", default="riscv64-linux-gnu-")
    parser.add_argument("--qemu", default="qemu-system-riscv64")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--make", default="make")
    parser.add_argument("--bash", default="bash")
    parser.add_argument("--host-cc", default="cc")
    parser.add_argument(
        "--agent-test-duration-profile",
        choices=("local-e3", "none"),
        required=True,
    )
    parser.add_argument("--case-timeout", default="300s")
    parser.add_argument("--idle-notice", default="20")
    parser.add_argument("--command-timeout", type=float, default=5 * 60 * 60)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    _add_collect_arguments(commands.add_parser("collect"))
    verify = commands.add_parser("verify")
    verify.add_argument("--payload", required=True)
    verify.add_argument("--expected-commit")
    verify.add_argument("--contract-root", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "collect":
            return collect(args)
        binding, _paths = verify_payload(
            Path(args.payload),
            expected_commit=args.expected_commit,
            contract_root=Path(args.contract_root),
        )
        print(json.dumps(binding, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (FullVerificationError, ToolAttestationError, OSError, ValueError) as error:
        print(f"[full-verification] failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
