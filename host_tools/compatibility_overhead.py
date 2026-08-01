#!/usr/bin/env python3
"""Run a symmetric, standalone compatibility-overhead campaign in QEMU."""

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
import gzip
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path, PurePosixPath, PureWindowsPath
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

try:
    from .compatibility_overhead_contract import (
        BUILD_STAMP_SCHEMA,
        EVIDENCE_TIER,
        FORMAL_BOOT_COUNT,
        FORMAL_CONTEXT_SCHEMA,
        LIMITATIONS,
        SCHEMA,
        TARGETS,
        CompatibilityContractError,
        canonical_json_bytes,
        create_plan,
        parse_guest_log,
        sha256_file,
        source_receipt,
        summarize_boots,
        validate_campaign,
        validate_plan,
    )
    from .evidence_delivery_contract import controlled_git_environment
    from .evidence_toolchain_attestation import (
        EVALUATION_ARTIFACT_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_ROOTS,
        EVALUATION_CACHE_OUTPUT_ROOTS,
        ToolAttestationError,
        purge_evaluation_generated_outputs,
        verify_evaluation_source_tree,
    )
    from .evaluation_campaign import check_campaign
    from .plain_ucore_action_runner import (
        RepoRunBusy,
        capture_source_identity,
        exclusive_repo_run_lock,
        make_var_arg,
        run_command,
        run_observed_command,
        shell_quote,
        verify_source_identity,
    )
    from .plain_ucore_fs_extract import T_FILE, read_file, read_inode, read_superblock, root_entries
    from .safe_host_paths import (
        absolute_lexical_path,
        atomic_write_bytes,
        create_private_directory,
        path_is_link,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
    )
except ImportError:  # Direct execution from host_tools/.
    from compatibility_overhead_contract import (
        BUILD_STAMP_SCHEMA,
        EVIDENCE_TIER,
        FORMAL_BOOT_COUNT,
        FORMAL_CONTEXT_SCHEMA,
        LIMITATIONS,
        SCHEMA,
        TARGETS,
        CompatibilityContractError,
        canonical_json_bytes,
        create_plan,
        parse_guest_log,
        sha256_file,
        source_receipt,
        summarize_boots,
        validate_campaign,
        validate_plan,
    )
    from evidence_delivery_contract import controlled_git_environment
    from evidence_toolchain_attestation import (
        EVALUATION_ARTIFACT_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_FILES,
        EVALUATION_BUILD_OUTPUT_ROOTS,
        EVALUATION_CACHE_OUTPUT_ROOTS,
        ToolAttestationError,
        purge_evaluation_generated_outputs,
        verify_evaluation_source_tree,
    )
    from evaluation_campaign import check_campaign
    from plain_ucore_action_runner import (
        RepoRunBusy,
        capture_source_identity,
        exclusive_repo_run_lock,
        make_var_arg,
        run_command,
        run_observed_command,
        shell_quote,
        verify_source_identity,
    )
    from plain_ucore_fs_extract import T_FILE, read_file, read_inode, read_superblock, root_entries
    from safe_host_paths import (
        absolute_lexical_path,
        atomic_write_bytes,
        create_private_directory,
        path_is_link,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
    )


DEFAULT_TIMEOUT_SECONDS = 600
PASS_MARKER = "compatbench: passed"
CHALLENGE_RE = re.compile(r"[0-9a-f]{16}\Z")
CLEANUP_RE = re.compile(
    r"\[runner\] wsl cleanup verified=([01]) initial=([0-9]+) "
    r"remaining=([0-9]+)(?: error=(.*))?\Z"
)
MAX_RUNTIME_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_RUNTIME_ARCHIVE_BYTES = 64 * 1024 * 1024
SOURCE_SNAPSHOT_FILES = (
    "evaluation_guest/compatbench.c",
    "Makefile",
    "user/Makefile",
    "baseline_ucore/Makefile",
    "baseline_ucore/user/Makefile",
)
FORMAL_SHELL_ENVIRONMENT_KEYS = (
    "HOME",
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


class CompatibilityRunError(RuntimeError):
    """Raised when a build, boot, or evidence check fails closed."""


def _source_gate(
    repo: Path, source_commit: str, artifact_root: str, stage: str
) -> None:
    git_name = shutil.which("git")
    if git_name is None:
        raise CompatibilityRunError("Git executable is unavailable")
    git = require_regular_file(Path(git_name), nonempty=True).resolve(strict=True)
    try:
        verify_evaluation_source_tree(
            git,
            repo,
            repo,
            source_commit,
            controlled_git_environment(),
            allowed_output_roots=(
                *EVALUATION_BUILD_OUTPUT_ROOTS,
                *EVALUATION_CACHE_OUTPUT_ROOTS,
                artifact_root,
            ),
            allowed_output_files=(
                *EVALUATION_BUILD_OUTPUT_FILES,
                *EVALUATION_ARTIFACT_OUTPUT_FILES,
            ),
            stage=stage,
        )
    except (OSError, ToolAttestationError) as error:
        raise CompatibilityRunError(
            f"compatibility source gate failed {stage}: {error}"
        ) from error


def _purge_build_outputs(repo: Path, source_commit: str) -> None:
    git_name = shutil.which("git")
    if git_name is None:
        raise CompatibilityRunError("Git executable is unavailable")
    git = require_regular_file(Path(git_name), nonempty=True).resolve(strict=True)
    try:
        purge_evaluation_generated_outputs(
            git,
            repo,
            repo,
            source_commit,
            controlled_git_environment(),
            output_roots=(
                *EVALUATION_BUILD_OUTPUT_ROOTS,
                *EVALUATION_CACHE_OUTPUT_ROOTS,
            ),
            output_files=EVALUATION_BUILD_OUTPUT_FILES,
        )
    except (OSError, ToolAttestationError) as error:
        raise CompatibilityRunError(
            f"compatibility build purge failed: {error}"
        ) from error


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
        + "\n"
    ).encode("ascii")


def _write_json(path: Path, value: object) -> None:
    atomic_write_bytes(path, _json_bytes(value))


def _clean_source_identity(
    repo: Path, *, expected_git: Path | None = None
) -> dict[str, object]:
    identity = capture_source_identity(repo)
    if identity.get("source_tree_clean") is not True:
        raise CompatibilityRunError("tracked source is dirty")
    git_name = shutil.which("git")
    if git_name is None:
        raise CompatibilityRunError("Git executable is unavailable")
    git = require_regular_file(Path(git_name), nonempty=True).resolve(strict=True)
    if expected_git is not None:
        expected_git = require_regular_file(expected_git, nonempty=True).resolve(
            strict=True
        )
        try:
            same_git = os.path.samefile(git, expected_git)
        except OSError:
            same_git = False
        if not same_git:
            raise CompatibilityRunError(
                "clean-source probe does not use the formal platform Git"
            )
    result = subprocess.run(
        [str(git), "-c", "core.fsmonitor=false", "-c",
         "core.untrackedCache=false", "-C", str(repo), "status",
         "--porcelain=v1", "--untracked-files=all"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=10,
        check=False,
        env=controlled_git_environment(),
    )
    if result.returncode != 0:
        raise CompatibilityRunError(f"Git source status failed: {result.stderr.strip()}")
    if result.stdout:
        raise CompatibilityRunError("source has untracked or modified files")
    return identity


def _formal_context(
    repo: Path, micro_manifest: Path
) -> tuple[dict[str, object], dict[str, Any]]:
    if micro_manifest.name != "campaign.json":
        raise CompatibilityRunError("formal micro manifest must be named campaign.json")
    campaign = check_campaign(repo, micro_manifest, require_collected=True)
    run = campaign.get("run")
    platform = campaign.get("platform")
    environment = campaign.get("environment")
    if (
        not isinstance(run, dict)
        or run.get("clean_worktree") is not True
        or not isinstance(platform, dict)
        or not isinstance(environment, dict)
        or len(campaign.get("boots", [])) != FORMAL_BOOT_COUNT
    ):
        raise CompatibilityRunError("formal micro campaign context is incomplete")
    source_commit = str(run.get("commit", ""))
    context: dict[str, object] = {
        "schema": FORMAL_CONTEXT_SCHEMA,
        "micro_campaign_path": "campaign.json",
        "micro_campaign_sha256": sha256_file(micro_manifest),
        "micro_run_id": run.get("id"),
        "source_commit": source_commit,
        "clean_worktree": True,
        "phase": campaign.get("phase"),
        "formal_boot_count": FORMAL_BOOT_COUNT,
        "platform_sha256": hashlib.sha256(canonical_json_bytes(platform)).hexdigest(),
        "environment_sha256": hashlib.sha256(
            canonical_json_bytes(environment)
        ).hexdigest(),
        "tool_identities_sha256": hashlib.sha256(
            canonical_json_bytes(platform.get("tools"))
        ).hexdigest(),
        "shell_environment_sha256": hashlib.sha256(
            canonical_json_bytes(_formal_shell_environment(campaign))
        ).hexdigest(),
        "execution_domain": run.get("execution_domain"),
    }
    return context, campaign


def _verify_formal_tools(campaign: dict[str, Any]) -> None:
    platform = campaign.get("platform")
    tools = platform.get("tools") if isinstance(platform, dict) else None
    if not isinstance(tools, dict) or not tools:
        raise CompatibilityRunError("formal platform tool inventory is unavailable")
    for label, identity in tools.items():
        if not isinstance(identity, dict):
            raise CompatibilityRunError(f"formal platform tool is malformed: {label}")
        executable = require_regular_file(
            Path(str(identity.get("path", ""))), nonempty=True
        ).resolve(strict=True)
        if sha256_file(executable) != identity.get("sha256"):
            raise CompatibilityRunError(f"formal platform tool changed: {label}")


def _formal_shell_environment(campaign: dict[str, Any]) -> dict[str, str]:
    """Derive the exact env -i payload from the attested micro platform."""

    platform = campaign.get("platform")
    run = campaign.get("run")
    if not isinstance(platform, dict) or not isinstance(run, dict):
        raise CompatibilityRunError("formal campaign platform is unavailable")
    domain = platform.get("domain")
    if domain not in ("native-linux", "native-msys2"):
        raise CompatibilityRunError("formal compatibility domain is unsupported")
    entry_domain = platform.get("entry_domain")
    if run.get("execution_domain") != entry_domain:
        raise CompatibilityRunError("formal compatibility execution domain differs")
    if domain == "native-msys2":
        if entry_domain != "native-msys2":
            raise CompatibilityRunError("formal MSYS2 entry domain is invalid")
    elif entry_domain != "native-linux" and (
        not isinstance(entry_domain, str)
        or re.fullmatch(r"windows-wsl:[A-Za-z0-9._-]{1,64}", entry_domain) is None
    ):
        raise CompatibilityRunError("formal Linux entry domain is invalid")

    tools = platform.get("tools")
    required = ("bash", "env", "make", "python", "qemu", "timeout")
    if not isinstance(tools, dict) or any(label not in tools for label in required):
        raise CompatibilityRunError("formal platform lacks a required compatibility tool")
    tool_paths: dict[str, str] = {}
    for label in sorted(tools):
        identity = tools[label]
        path = identity.get("path") if isinstance(identity, dict) else None
        if not isinstance(path, str) or not PurePosixPath(path).is_absolute():
            raise CompatibilityRunError(
                f"formal compatibility tool path is invalid: {label}"
            )
        tool_paths[label] = path

    temporary_value = platform.get("temporary_directory")
    toolprefix = platform.get("toolprefix")
    if (
        not isinstance(toolprefix, str)
        or not PurePosixPath(toolprefix).is_absolute()
    ):
        raise CompatibilityRunError("formal compatibility path binding is invalid")
    if domain == "native-msys2":
        temporary = temporary_value
        system_drive = platform.get("windows_system_drive")
        native_temporary = platform.get("windows_temporary_directory")
        if (
            not isinstance(temporary, str)
            or not PurePosixPath(temporary).is_absolute()
            or not isinstance(system_drive, str)
            or re.fullmatch(r"[A-Z]:", system_drive) is None
            or not isinstance(native_temporary, str)
            or not PureWindowsPath(native_temporary).is_absolute()
        ):
            raise CompatibilityRunError(
                "formal MSYS2 system drive or temporary directory is invalid"
            )
    else:
        if (
            temporary_value is not None
            or platform.get("windows_system_drive") is not None
            or platform.get("windows_temporary_directory") is not None
        ):
            raise CompatibilityRunError(
                "formal Linux platform contains a Host path binding"
            )
        temporary = "/tmp"
        system_drive = "/"
        native_temporary = temporary

    search_path = ":".join(
        dict.fromkeys(str(PurePosixPath(path).parent) for path in tool_paths.values())
    )
    environment = {
        "HOME": temporary,
        "LANG": "C",
        "LC_ALL": "C",
        "MAKE_TOOL": tool_paths["make"],
        "PATH": search_path,
        "QEMU": tool_paths["qemu"],
        "SHELL": tool_paths["bash"],
        "SYSTEMDRIVE": system_drive,
        "TEMP": native_temporary,
        "TMP": native_temporary,
        "TMPDIR": temporary,
        "TOOLPREFIX": toolprefix,
        "TZ": "UTC",
    }
    if tuple(environment) != FORMAL_SHELL_ENVIRONMENT_KEYS:
        raise CompatibilityRunError("formal compatibility environment order changed")
    if any(
        not value or any(character in value for character in "\x00\r\n")
        for value in environment.values()
    ):
        raise CompatibilityRunError("formal compatibility environment is invalid")
    if any("%systemdrive%" in value.casefold() for value in environment.values()):
        raise CompatibilityRunError(
            "formal compatibility environment contains an unresolved system drive"
        )
    return environment


def _formal_shell_command(
    command_text: str, campaign: dict[str, Any], timeout_seconds: int
) -> list[str]:
    platform = campaign["platform"]
    tools = platform["tools"]
    environment = _formal_shell_environment(campaign)
    inner_timeout = max(1, timeout_seconds - 10)
    return [
        str(tools["env"]["path"]),
        "-i",
        *(f"{name}={environment[name]}" for name in FORMAL_SHELL_ENVIRONMENT_KEYS),
        str(tools["timeout"]["path"]),
        "--signal=TERM",
        "--kill-after=5s",
        f"{inner_timeout}s",
        str(tools["bash"]["path"]),
        "--noprofile",
        "--norc",
        "-c",
        command_text,
    ]


def _artifact(path: Path, repo: Path) -> dict[str, object]:
    path = require_regular_file(path, nonempty=True).resolve(strict=True)
    try:
        label = path.relative_to(repo).as_posix()
    except ValueError:
        label = path.name
    return {
        "path": label,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _copy_artifact(source: Path, destination: Path) -> dict[str, object]:
    source = require_regular_file(source, nonempty=True)
    if destination.exists() or path_is_link(destination):
        raise CompatibilityRunError(f"artifact archive destination is occupied: {destination}")
    reject_link_components(destination.parent)
    temporary = destination.with_name(f".{destination.name}.tmp.{os.getpid()}")
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {
        "path": destination.name,
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
    }


def _archive_source_snapshot(
    repo: Path, work_dir: Path, expected: dict[str, object]
) -> None:
    snapshot = create_private_directory(work_dir / "source-snapshot")
    for relative in ("evaluation_guest", "user", "baseline_ucore"):
        create_private_directory(snapshot / relative)
    create_private_directory(snapshot / "baseline_ucore/user")
    for relative in SOURCE_SNAPSHOT_FILES:
        _copy_artifact(repo / relative, snapshot / relative)
    if source_receipt(snapshot) != expected:
        raise CompatibilityRunError(
            "materialized compatibility source snapshot differs from its receipt"
        )


def _gzip_artifact(source: Path, destination: Path) -> dict[str, object]:
    source = require_regular_file(source, nonempty=True)
    if destination.exists() or path_is_link(destination):
        raise CompatibilityRunError(f"artifact archive destination is occupied: {destination}")
    reject_link_components(destination.parent)
    temporary = destination.with_name(f".{destination.name}.tmp.{os.getpid()}")
    try:
        with source.open("rb") as source_handle, temporary.open("wb") as raw_output:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw_output, compresslevel=6, mtime=0
            ) as compressed:
                shutil.copyfileobj(source_handle, compressed, length=1024 * 1024)
            raw_output.flush()
            os.fsync(raw_output.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {
        "path": destination.name,
        "encoding": "gzip-mtime0",
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
        "uncompressed_bytes": source.stat().st_size,
        "uncompressed_sha256": sha256_file(source),
    }


def _target_root(repo: Path, target: str) -> Path:
    if target == "plain":
        return require_safe_directory(repo / "baseline_ucore").resolve(strict=True)
    if target == "agentos":
        return repo
    raise CompatibilityRunError(f"unknown target: {target}")


def _make_arguments(challenge: str, campaign: dict[str, Any]) -> str:
    if CHALLENGE_RE.fullmatch(challenge) is None or int(challenge, 16) == 0:
        raise CompatibilityRunError("challenge is malformed")
    platform = campaign["platform"]
    tools = platform["tools"]
    return " ".join(
        (
            make_var_arg("TOOLPREFIX", str(platform["toolprefix"])),
            make_var_arg("PYTHON_BIN", str(tools["python"]["path"])),
            make_var_arg("QEMU", str(tools["qemu"]["path"])),
            make_var_arg("CHAPTER", "compat_eval"),
            make_var_arg("COMPAT_BENCH_CHALLENGE_HEX", challenge),
        )
    )


def _build_stamp(
    repo: Path,
    target_root: Path,
    target: str,
    challenge: str,
    source_identity: dict[str, object],
    source: dict[str, object],
    target_dir: Path,
    build_log: Path,
    build_command: str,
) -> dict[str, object]:
    paths = {
        "compatbench_binary": target_root / "user/target/bin/compatbench",
        "compatbench_elf": target_root / "user/target/elf/compatbench",
        "filesystem_image": target_root / "nfs/fs-copy.img",
        "kernel": target_root / "build/kernel",
    }
    artifacts = {name: _artifact(path, repo) for name, path in paths.items()}
    binary_payload = require_regular_file(
        paths["compatbench_binary"],
        nonempty=True,
        maximum_bytes=MAX_RUNTIME_ARTIFACT_BYTES,
    ).read_bytes()
    image_payload = require_regular_file(
        paths["filesystem_image"],
        nonempty=True,
        maximum_bytes=MAX_RUNTIME_ARTIFACT_BYTES,
    ).read_bytes()
    if extract_compatbench_from_image(image_payload) != binary_payload:
        raise CompatibilityRunError("QEMU filesystem does not contain the built compatbench binary")
    kernel_payload = require_regular_file(
        paths["kernel"],
        nonempty=True,
        maximum_bytes=MAX_RUNTIME_ARTIFACT_BYTES,
    ).read_bytes()
    if b"compatbench\0" not in kernel_payload:
        raise CompatibilityRunError("built kernel does not bind INIT_PROC=compatbench")
    archived_binary = _copy_artifact(paths["compatbench_binary"], target_dir / "compatbench.bin")
    archived_elf = _copy_artifact(paths["compatbench_elf"], target_dir / "compatbench.elf")
    archived_kernel = _gzip_artifact(paths["kernel"], target_dir / "kernel.gz")
    archived_image = _gzip_artifact(paths["filesystem_image"], target_dir / "fs-input.img.gz")
    if archived_binary["sha256"] != artifacts["compatbench_binary"]["sha256"]:
        raise CompatibilityRunError("archived compatibility binary changed during capture")
    if archived_elf["sha256"] != artifacts["compatbench_elf"]["sha256"]:
        raise CompatibilityRunError("archived compatibility ELF changed during capture")
    if archived_kernel["uncompressed_sha256"] != artifacts["kernel"]["sha256"]:
        raise CompatibilityRunError("archived kernel changed during capture")
    if archived_image["uncompressed_sha256"] != artifacts["filesystem_image"]["sha256"]:
        raise CompatibilityRunError("archived filesystem image changed during capture")
    artifacts["compatbench_binary"]["archive"] = archived_binary
    artifacts["compatbench_elf"]["archive"] = archived_elf
    artifacts["kernel"]["archive"] = archived_kernel
    artifacts["filesystem_image"]["archive"] = archived_image
    return {
        "schema": BUILD_STAMP_SCHEMA,
        "target": target,
        "challenge": challenge,
        "source_commit": source_identity["source_commit"],
        "source_tracked_sha256": source_identity["source_tracked_sha256"],
        "canonical_source_sha256": source["canonical_sha256"],
        "chapter": "compat_eval",
        "init_proc": "compatbench",
        "build_command": build_command,
        "build_log": build_log.name,
        "build_log_sha256": sha256_file(build_log),
        "artifacts": artifacts,
    }


def _runtime_artifact_hashes(target_root: Path) -> dict[str, str]:
    return {
        "compatbench_binary": sha256_file(
            require_regular_file(target_root / "user/target/bin/compatbench", nonempty=True)
        ),
        "compatbench_elf": sha256_file(
            require_regular_file(target_root / "user/target/elf/compatbench", nonempty=True)
        ),
        "filesystem_image": sha256_file(
            require_regular_file(target_root / "nfs/fs-copy.img", nonempty=True)
        ),
        "kernel": sha256_file(
            require_regular_file(target_root / "build/kernel", nonempty=True)
        ),
    }


def run_target(
    repo: Path,
    target: str,
    challenge: str,
    target_dir: Path,
    timeout_seconds: int,
    source_identity: dict[str, object],
    source: dict[str, object],
    formal_campaign: dict[str, Any],
) -> dict[str, object]:
    target_root = _target_root(repo, target)
    artifact_root = str(formal_campaign["run"]["artifact_root"])
    source_commit = str(source_identity["source_commit"])
    _source_gate(repo, source_commit, artifact_root, f"before {target} purge")
    _purge_build_outputs(repo, source_commit)
    _source_gate(repo, source_commit, artifact_root, f"after {target} purge")
    build_log = target_dir / "build.log"
    guest_log = target_dir / "guest.log"
    make = shell_quote(str(formal_campaign["platform"]["tools"]["make"]["path"]))
    arguments = _make_arguments(challenge, formal_campaign)
    root_bash = str(target_root)
    build_command = (
        f"cd {shell_quote(root_bash)} && "
        f"{make} clean && "
        f"{make} nfs/fs-copy.img {arguments} && "
        f"{make} build {arguments} LOG=warn INIT_PROC=compatbench"
    )
    phase_timeout = timeout_seconds + 30
    _verify_formal_tools(formal_campaign)
    build_code = run_command(
        _formal_shell_command(build_command, formal_campaign, phase_timeout),
        build_log,
        phase_timeout,
    )
    if build_code != 0:
        raise CompatibilityRunError(f"{target} build failed with exit {build_code}")
    verify_source_identity(repo, source_identity)
    _source_gate(repo, source_commit, artifact_root, f"after {target} build")
    stamp = _build_stamp(
        repo,
        target_root,
        target,
        challenge,
        source_identity,
        source,
        target_dir,
        build_log,
        build_command,
    )
    _write_json(target_dir / "build-stamp.json", stamp)

    pre_run_hashes = _runtime_artifact_hashes(target_root)
    expected_hashes = {
        name: str(artifact["sha256"])
        for name, artifact in stamp["artifacts"].items()
    }
    if pre_run_hashes != expected_hashes:
        raise CompatibilityRunError("runtime artifacts changed before QEMU launch")

    run_command_text = (
        f"cd {shell_quote(root_bash)} && "
        f"{make} run-prebuilt {arguments} LOG=warn INIT_PROC=compatbench"
    )
    observed = run_observed_command(
        _formal_shell_command(run_command_text, formal_campaign, phase_timeout),
        guest_log,
        phase_timeout,
        pass_marker=PASS_MARKER,
        idle_notice_seconds=20,
        marker_grace_seconds=1,
    )
    runner_signals = [int(item) for item in observed["runner_signals"]]
    raw_returncode = observed["raw_returncode"]
    if (
        observed["runner_terminated"] is False
        and raw_returncode == 0
        and not runner_signals
    ):
        termination_mode = "natural_exit"
    elif (
        observed["runner_terminated"] is True
        and runner_signals == [int(signal.SIGTERM)]
    ):
        termination_mode = "observer_sigterm"
    else:
        termination_mode = None
    if (
        int(observed["returncode"]) != 0
        or observed["marker_seen"] is not True
        or observed["failure_seen"] is True
        or observed["timed_out"] is True
        or termination_mode is None
        or observed["host_process_quiesced"] is not True
        or observed["output_eof"] is not True
        or observed["output_error"]
        or observed["wsl_cleanup_verified"] is not True
        or observed["wsl_cleanup_initial_survivors"] != 0
        or observed["wsl_cleanup_remaining_survivors"] != 0
    ):
        raise CompatibilityRunError(
            f"{target} Guest run failed: {observed.get('failure_reason') or observed.get('returncode')}"
        )
    guest_text = require_regular_file(guest_log, nonempty=True).read_text(
        encoding="utf-8", errors="replace"
    )
    guest = parse_guest_log(guest_text, challenge)
    post_run_hashes = _runtime_artifact_hashes(target_root)
    for immutable in ("compatbench_binary", "compatbench_elf", "kernel"):
        if post_run_hashes[immutable] != pre_run_hashes[immutable]:
            raise CompatibilityRunError(f"{immutable} changed while QEMU was running")
    verify_source_identity(repo, source_identity)
    _source_gate(repo, source_commit, artifact_root, f"after {target} boot")
    _verify_formal_tools(formal_campaign)
    return {
        "status": "ready",
        "target": target,
        "challenge": challenge,
        "fresh_boot": True,
        "build_log": build_log.name,
        "build_log_sha256": sha256_file(build_log),
        "guest_log": guest_log.name,
        "guest_log_sha256": sha256_file(guest_log),
        "build_stamp_path": "build-stamp.json",
        "build_stamp": stamp,
        "runtime_artifact_attestation": {
            "launch_contract": "make-run-prebuilt-fixed-kernel-and-fs-paths",
            "pre_run_sha256": pre_run_hashes,
            "post_run_sha256": post_run_hashes,
            "immutable_runtime_artifacts_unchanged": True,
            "filesystem_expected_mutable": True,
        },
        "guest": guest,
        "observer": {
            "marker_seen": observed["marker_seen"],
            "failure_seen": observed["failure_seen"],
            "timed_out": observed["timed_out"],
            "returncode": observed["returncode"],
            "runner_terminated": observed["runner_terminated"],
            "termination_mode": termination_mode,
            "runner_signals": runner_signals,
            "raw_returncode": raw_returncode,
            "elapsed_seconds": observed["elapsed_seconds"],
            "host_process_quiesced": observed["host_process_quiesced"],
            "output_eof": observed["output_eof"],
            "output_error": observed["output_error"],
            "wsl_cleanup_applicable": observed["wsl_cleanup_applicable"],
            "wsl_cleanup_verified": observed["wsl_cleanup_verified"],
            "wsl_cleanup_initial_survivors": observed[
                "wsl_cleanup_initial_survivors"
            ],
            "wsl_cleanup_remaining_survivors": observed[
                "wsl_cleanup_remaining_survivors"
            ],
            "wsl_cleanup_error": observed["wsl_cleanup_error"],
        },
    }


def run_campaign(
    repo: Path,
    work_dir: Path,
    *,
    micro_manifest: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    repo = require_safe_directory(absolute_lexical_path(repo)).resolve(strict=True)
    if type(timeout_seconds) is not int or timeout_seconds < 30 or timeout_seconds > 3600:
        raise CompatibilityRunError("timeout must be an integer between 30 and 3600 seconds")
    work_dir = create_private_directory(absolute_lexical_path(work_dir))
    micro_manifest = require_regular_file(
        absolute_lexical_path(micro_manifest), nonempty=True
    ).resolve(strict=True)
    try:
        with exclusive_repo_run_lock(repo):
            formal_context, formal_campaign = _formal_context(repo, micro_manifest)
            _verify_formal_tools(formal_campaign)
            source_identity = _clean_source_identity(
                repo,
                expected_git=Path(
                    str(formal_campaign["platform"]["tools"]["git"]["path"])
                ),
            )
            artifact_root = str(formal_campaign["run"]["artifact_root"])
            source_commit = str(source_identity["source_commit"])
            _source_gate(
                repo, source_commit, artifact_root, "before compatibility collection"
            )
            _purge_build_outputs(repo, source_commit)
            _source_gate(
                repo, source_commit, artifact_root, "after compatibility initial purge"
            )
            source = source_receipt(repo)
            _archive_source_snapshot(repo, work_dir, source)
            if source_identity["source_commit"] != formal_context["source_commit"]:
                raise CompatibilityRunError(
                    "clean source identity differs from the formal micro campaign"
                )
            plan = create_plan(str(source_identity["source_commit"]))
            validate_plan(plan)
            _write_json(work_dir / "plan.json", plan)
            _write_json(work_dir / "source-receipt.json", source)

            boot_results: list[dict[str, object]] = []
            for planned in plan["boots"]:
                boot_dir = create_private_directory(work_dir / str(planned["boot_id"]))
                order = str(planned["target_order"]).split("-")
                if tuple(order) not in (("plain", "agentos"), ("agentos", "plain")):
                    raise CompatibilityRunError("planned target order is invalid")
                targets: dict[str, object] = {}
                for target in order:
                    target_dir = create_private_directory(boot_dir / target)
                    targets[target] = run_target(
                        repo,
                        target,
                        str(planned["challenge"]),
                        target_dir,
                        timeout_seconds,
                        source_identity,
                        source,
                        formal_campaign,
                    )
                boot_results.append(
                    {
                        "boot_id": planned["boot_id"],
                        "challenge": planned["challenge"],
                        "target_order": planned["target_order"],
                        "targets": {target: targets[target] for target in TARGETS},
                    }
                )
                _write_json(boot_dir / "boot-summary.json", boot_results[-1])

            verify_source_identity(repo, source_identity)
            _source_gate(
                repo, source_commit, artifact_root, "after compatibility collection"
            )
            final_context, final_campaign = _formal_context(repo, micro_manifest)
            if final_context != formal_context or final_campaign != formal_campaign:
                raise CompatibilityRunError(
                    "formal micro campaign or platform identity changed during collection"
                )
            result: dict[str, object] = {
                "schema": SCHEMA,
                "status": "ready",
                "evidence_tier": EVIDENCE_TIER,
                "formal_bundle_eligible": True,
                "formal_context": formal_context,
                "source_identity": source_identity,
                "source": source,
                "plan": plan,
                "boots": boot_results,
                "summary": summarize_boots(boot_results),
                "limitations": list(LIMITATIONS),
            }
            validate_campaign(result)
            summary_path = work_dir / "compatibility-overhead.json"
            _write_json(summary_path, result)
            verify_campaign_artifacts(summary_path, micro_manifest=micro_manifest)
            return result
    except Exception as error:
        ready_path = work_dir / "compatibility-overhead.json"
        try:
            ready_path.unlink()
        except FileNotFoundError:
            pass
        failure = {
            "schema": SCHEMA,
            "status": "failed",
            "error_type": type(error).__name__,
            "error": str(error),
        }
        try:
            _write_json(work_dir / "failure.json", failure)
        except Exception:
            pass
        raise


def _load_json(path: Path) -> Any:
    path = require_regular_file(path, nonempty=True, maximum_bytes=32 * 1024 * 1024)
    return json.loads(path.read_text(encoding="utf-8"))


def _require_exact_directory(path: Path, expected: set[str]) -> Path:
    path = require_safe_directory(path)
    observed = {entry.name for entry in path.iterdir()}
    if observed != expected:
        raise CompatibilityRunError(
            f"artifact directory inventory differs at {path}: "
            f"missing={sorted(expected - observed)} extra={sorted(observed - expected)}"
        )
    return path


def _verify_source_snapshot(root: Path, expected: object) -> None:
    snapshot = _require_exact_directory(
        root / "source-snapshot",
        {"Makefile", "baseline_ucore", "evaluation_guest", "user"},
    )
    _require_exact_directory(snapshot / "evaluation_guest", {"compatbench.c"})
    _require_exact_directory(snapshot / "user", {"Makefile"})
    baseline = _require_exact_directory(
        snapshot / "baseline_ucore", {"Makefile", "user"}
    )
    _require_exact_directory(baseline / "user", {"Makefile"})
    try:
        observed = source_receipt(snapshot)
    except (CompatibilityContractError, OSError, UnicodeError, ValueError) as error:
        raise CompatibilityRunError(
            f"compatibility source snapshot cannot be replayed: {error}"
        ) from error
    if observed != expected:
        raise CompatibilityRunError(
            "compatibility source snapshot differs from its sealed receipt"
        )


def _verify_gzip_archive(
    path: Path, archive: object, artifact: dict[str, object]
) -> bytes:
    if not isinstance(archive, dict) or archive.get("encoding") != "gzip-mtime0":
        raise CompatibilityRunError("compressed runtime archive contract is invalid")
    path = require_regular_file(
        path, nonempty=True, maximum_bytes=MAX_RUNTIME_ARCHIVE_BYTES
    )
    if archive.get("bytes") != path.stat().st_size or archive.get("sha256") != sha256_file(path):
        raise CompatibilityRunError("compressed runtime archive hash differs")
    header = path.read_bytes()[:10]
    if len(header) != 10 or header[:3] != b"\x1f\x8b\x08" or header[4:8] != b"\0\0\0\0":
        raise CompatibilityRunError("compressed runtime archive is not deterministic gzip")
    digest = hashlib.sha256()
    size = 0
    payload = bytearray()
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        with path.open("rb") as handle:
            pending = b""
            while True:
                if not pending:
                    pending = handle.read(64 * 1024)
                    if not pending:
                        break
                output = decompressor.decompress(
                    pending, MAX_RUNTIME_ARTIFACT_BYTES - size + 1
                )
                size += len(output)
                digest.update(output)
                payload.extend(output)
                if size > MAX_RUNTIME_ARTIFACT_BYTES:
                    raise CompatibilityRunError("compressed runtime archive expands past its limit")
                if decompressor.eof:
                    if decompressor.unused_data or handle.read(1):
                        raise CompatibilityRunError(
                            "compressed runtime archive has trailing data or members"
                        )
                    pending = b""
                    break
                pending = decompressor.unconsumed_tail
            if not decompressor.eof:
                raise CompatibilityRunError("compressed runtime archive is truncated")
    except (OSError, EOFError, zlib.error) as error:
        raise CompatibilityRunError("compressed runtime archive cannot be replayed") from error
    if (
        archive.get("uncompressed_bytes") != size
        or archive.get("uncompressed_sha256") != digest.hexdigest()
        or artifact.get("bytes") != size
        or artifact.get("sha256") != digest.hexdigest()
    ):
        raise CompatibilityRunError("compressed runtime archive payload differs")
    return bytes(payload)


def extract_compatbench_from_image(image: bytes) -> bytes:
    try:
        superblock = read_superblock(image)
        matches = [inum for inum, name in root_entries(image, superblock) if name == "compatbench"]
        if len(matches) != 1:
            raise CompatibilityRunError("filesystem image lacks one exact compatbench entry")
        inode = read_inode(image, superblock, matches[0])
        if inode.type != T_FILE or inode.size <= 0 or inode.size > MAX_RUNTIME_ARTIFACT_BYTES:
            raise CompatibilityRunError("filesystem compatbench inode is invalid")
        data = read_file(image, inode)
        if len(data) != inode.size:
            raise CompatibilityRunError("filesystem compatbench payload is truncated")
        return data
    except CompatibilityRunError:
        raise
    except (IndexError, UnicodeError, ValueError) as error:
        raise CompatibilityRunError("filesystem input image cannot be parsed") from error


def _verify_formal_campaign_binding(
    value: dict[str, object], micro_manifest: Path
) -> None:
    context = value.get("formal_context")
    if not isinstance(context, dict):
        raise CompatibilityRunError("formal compatibility context is missing")
    micro_manifest = require_regular_file(
        absolute_lexical_path(micro_manifest), nonempty=True, maximum_bytes=32 * 1024 * 1024
    )
    if micro_manifest.name != context.get("micro_campaign_path"):
        raise CompatibilityRunError("formal micro campaign path binding differs")
    if sha256_file(micro_manifest) != context.get("micro_campaign_sha256"):
        raise CompatibilityRunError("formal micro campaign hash differs")
    campaign = _load_json(micro_manifest)
    run = campaign.get("run") if isinstance(campaign, dict) else None
    platform = campaign.get("platform") if isinstance(campaign, dict) else None
    environment = campaign.get("environment") if isinstance(campaign, dict) else None
    boots = campaign.get("boots") if isinstance(campaign, dict) else None
    if (
        not isinstance(run, dict)
        or not isinstance(platform, dict)
        or not isinstance(environment, dict)
        or not isinstance(boots, list)
        or len(boots) != FORMAL_BOOT_COUNT
        or campaign.get("phase") != "collected"
        or run.get("clean_worktree") is not True
        or run.get("id") != context.get("micro_run_id")
        or run.get("commit") != context.get("source_commit")
        or run.get("execution_domain") != context.get("execution_domain")
        or hashlib.sha256(canonical_json_bytes(platform)).hexdigest()
        != context.get("platform_sha256")
        or hashlib.sha256(canonical_json_bytes(environment)).hexdigest()
        != context.get("environment_sha256")
        or hashlib.sha256(canonical_json_bytes(platform.get("tools"))).hexdigest()
        != context.get("tool_identities_sha256")
        or hashlib.sha256(
            canonical_json_bytes(_formal_shell_environment(campaign))
        ).hexdigest()
        != context.get("shell_environment_sha256")
    ):
        raise CompatibilityRunError("formal micro campaign context differs")


def _verify_cleanup_receipt(guest_text: str, observer: object) -> None:
    if not isinstance(observer, dict):
        raise CompatibilityRunError("Guest observer receipt is unavailable")
    matches = [
        match
        for line in guest_text.splitlines()
        if (match := CLEANUP_RE.fullmatch(line.rstrip("\r"))) is not None
    ]
    applicable = observer.get("wsl_cleanup_applicable") is True
    if not applicable:
        if matches:
            raise CompatibilityRunError("unexpected WSL cleanup record in Guest log")
        return
    if len(matches) != 1:
        raise CompatibilityRunError("Guest log must contain one WSL cleanup receipt")
    match = matches[0]
    expected_error = str(observer.get("wsl_cleanup_error") or "")
    if (
        match.group(1) != ("1" if observer.get("wsl_cleanup_verified") is True else "0")
        or int(match.group(2)) != observer.get("wsl_cleanup_initial_survivors")
        or int(match.group(3)) != observer.get("wsl_cleanup_remaining_survivors")
        or (match.group(4) or "") != expected_error
    ):
        raise CompatibilityRunError("WSL cleanup receipt differs from the observer")


def verify_campaign_artifacts(
    summary_path: Path, *, micro_manifest: Path
) -> dict[str, object]:
    """Replay the campaign exclusively from its sealed raw artifact tree."""

    summary_path = require_regular_file(
        absolute_lexical_path(summary_path), nonempty=True, maximum_bytes=32 * 1024 * 1024
    )
    root = require_safe_directory(summary_path.parent)
    value = _load_json(summary_path)
    validate_campaign(value)
    _verify_formal_campaign_binding(value, micro_manifest)
    plan = value["plan"]
    boot_names = {str(boot["boot_id"]) for boot in plan["boots"]}
    _require_exact_directory(
        root,
        {
            summary_path.name,
            "plan.json",
            "source-receipt.json",
            "source-snapshot",
            *boot_names,
        },
    )
    if _load_json(root / "plan.json") != plan:
        raise CompatibilityRunError("materialized plan differs from campaign summary")
    if _load_json(root / "source-receipt.json") != value["source"]:
        raise CompatibilityRunError("materialized source receipt differs from campaign summary")
    _verify_source_snapshot(root, value["source"])

    for boot in value["boots"]:
        boot_dir = _require_exact_directory(
            root / str(boot["boot_id"]),
            {"boot-summary.json", "plain", "agentos"},
        )
        if _load_json(boot_dir / "boot-summary.json") != boot:
            raise CompatibilityRunError("materialized boot summary differs from campaign summary")
        for target in TARGETS:
            result = boot["targets"][target]
            target_dir = _require_exact_directory(
                boot_dir / target,
                {
                    "build.log",
                    "build-stamp.json",
                    "compatbench.bin",
                    "compatbench.elf",
                    "fs-input.img.gz",
                    "guest.log",
                    "kernel.gz",
                },
            )
            if result.get("build_log") != "build.log" or result.get("guest_log") != "guest.log":
                raise CompatibilityRunError("target raw log path contract drifted")
            build_log = require_regular_file(
                target_dir / "build.log", nonempty=True, maximum_bytes=32 * 1024 * 1024
            )
            guest_log = require_regular_file(
                target_dir / "guest.log", nonempty=True, maximum_bytes=2 * 1024 * 1024
            )
            if sha256_file(build_log) != result.get("build_log_sha256"):
                raise CompatibilityRunError("raw build log hash differs")
            if sha256_file(guest_log) != result.get("guest_log_sha256"):
                raise CompatibilityRunError("raw Guest log hash differs")
            reparsed = parse_guest_log(
                guest_log.read_text(encoding="utf-8", errors="replace"),
                str(boot["challenge"]),
            )
            if reparsed != result.get("guest"):
                raise CompatibilityRunError("parsed Guest receipt differs from raw log")
            _verify_cleanup_receipt(
                guest_log.read_text(encoding="utf-8", errors="replace"),
                result.get("observer"),
            )
            if result.get("build_stamp_path") != "build-stamp.json":
                raise CompatibilityRunError("build stamp path contract drifted")
            stamp = _load_json(target_dir / "build-stamp.json")
            if stamp != result.get("build_stamp"):
                raise CompatibilityRunError("materialized build stamp differs from campaign summary")
            if stamp.get("build_log_sha256") != result.get("build_log_sha256"):
                raise CompatibilityRunError("build stamp does not bind the raw build log")
            for artifact_name, archive_name in (
                ("compatbench_binary", "compatbench.bin"),
                ("compatbench_elf", "compatbench.elf"),
            ):
                artifact = stamp["artifacts"][artifact_name]
                archive = artifact.get("archive")
                archived = require_regular_file(target_dir / archive_name, nonempty=True)
                if (
                    not isinstance(archive, dict)
                    or archive.get("path") != archive_name
                    or archive.get("bytes") != archived.stat().st_size
                    or archive.get("sha256") != sha256_file(archived)
                    or archive.get("sha256") != artifact.get("sha256")
                ):
                    raise CompatibilityRunError(f"archived {artifact_name} differs from its build stamp")
            kernel_artifact = stamp["artifacts"]["kernel"]
            kernel_archive = kernel_artifact.get("archive")
            if not isinstance(kernel_archive, dict) or kernel_archive.get("path") != "kernel.gz":
                raise CompatibilityRunError("archived kernel path differs")
            kernel_payload = _verify_gzip_archive(
                target_dir / "kernel.gz", kernel_archive, kernel_artifact
            )
            if b"compatbench\0" not in kernel_payload:
                raise CompatibilityRunError("archived kernel does not bind INIT_PROC=compatbench")
            image_artifact = stamp["artifacts"]["filesystem_image"]
            image_archive = image_artifact.get("archive")
            if not isinstance(image_archive, dict) or image_archive.get("path") != "fs-input.img.gz":
                raise CompatibilityRunError("archived filesystem image path differs")
            image_payload = _verify_gzip_archive(
                target_dir / "fs-input.img.gz", image_archive, image_artifact
            )
            installed_binary = extract_compatbench_from_image(image_payload)
            archived_binary = require_regular_file(
                target_dir / "compatbench.bin",
                nonempty=True,
                maximum_bytes=MAX_RUNTIME_ARTIFACT_BYTES,
            ).read_bytes()
            if installed_binary != archived_binary:
                raise CompatibilityRunError(
                    "filesystem input compatbench differs from the archived canonical binary"
                )
            runtime = result.get("runtime_artifact_attestation")
            if not isinstance(runtime, dict):
                raise CompatibilityRunError("runtime artifact attestation is missing")
            pre = runtime.get("pre_run_sha256")
            post = runtime.get("post_run_sha256")
            expected_pre = {
                name: artifact["sha256"]
                for name, artifact in stamp["artifacts"].items()
            }
            if (
                pre != expected_pre
                or not isinstance(post, dict)
                or set(post) != set(expected_pre)
                or runtime.get("immutable_runtime_artifacts_unchanged") is not True
                or runtime.get("filesystem_expected_mutable") is not True
            ):
                raise CompatibilityRunError("runtime artifact attestation is invalid")
            for immutable in ("compatbench_binary", "compatbench_elf", "kernel"):
                if post.get(immutable) != pre.get(immutable):
                    raise CompatibilityRunError("immutable runtime artifact changed in QEMU")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect formal baseline/AgentOS compatibility-tax evidence."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--repo", type=Path, default=Path("."))
    run_parser.add_argument("--work-dir", type=Path, required=True)
    run_parser.add_argument("--micro-manifest", type=Path, required=True)
    run_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("summary", type=Path)
    verify_parser.add_argument("--micro-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            result = run_campaign(
                args.repo,
                args.work_dir,
                micro_manifest=args.micro_manifest,
                timeout_seconds=args.timeout,
            )
            print(
                f"compatibility_overhead: boots={len(result['boots'])} "
                "scope=traditional-ucore-only status=ready"
            )
        else:
            verify_campaign_artifacts(
                args.summary, micro_manifest=args.micro_manifest
            )
            print("compatibility_overhead: contract=valid status=ready")
    except (
        CompatibilityContractError,
        CompatibilityRunError,
        RepoRunBusy,
        OSError,
        ValueError,
    ) as error:
        print(f"compatibility_overhead: status=failed error={error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
