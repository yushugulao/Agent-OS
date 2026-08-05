#!/usr/bin/env python3
"""Run QEMU resource regressions in isolated, bounded parallel lanes."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


DEFAULT_JOBS = 4
MAX_JOBS = 8
LANE_BUILD_JOBS = 2
MAX_BUILD_JOBS = 24


@dataclass(frozen=True)
class RegressionCase:
    label: str
    runner: str
    weight: int
    shell_flags: tuple[str, ...] = ()
    agent_case: str | None = None


RESOURCE_CASES = (
    RegressionCase("proc-reap", "scripts/run-proc-reap-tests.sh", 8),
    RegressionCase("syscall-fairness", "scripts/run-syscall-fairness-tests.sh", 5),
    RegressionCase("file-resource", "scripts/run-file-resource-tests.sh", 7),
    RegressionCase("thread-resource", "scripts/run-thread-resource-tests.sh", 5),
    RegressionCase("physical-resource", "scripts/run-physical-resource-tests.sh", 6),
    RegressionCase("metadata-recovery", "scripts/run-metadata-recovery-tests.sh", 45),
    RegressionCase("observe-recovery", "scripts/run-observe-recovery-tests.sh", 14),
    RegressionCase("virtio-disk", "scripts/run-virtio-disk-tests.sh", 7),
    RegressionCase(
        "workflow-teardown-race", "scripts/run-workflow-teardown-race-tests.sh", 10
    ),
    RegressionCase("fs-enospc", "scripts/run-fs-enospc-tests.sh", 20),
    RegressionCase("fs-epoch", "scripts/run-fs-epoch-tests.sh", 24),
    RegressionCase(
        "fs-allocator-fault",
        "scripts/run-fs-allocator-fault-tests.sh",
        30,
        ("--noprofile", "--norc", "-p"),
    ),
)

AGENT_CASE_NAMES = (
    "agentfinal_ucore",
    "agentfs_ucore",
    "agentscan_ucore",
    "agentloop_ucore",
    "agentsched_ucore",
    "agentconflict_ucore",
    "agentllm_ucore",
    "agentbench_ucore",
    "ch8_cow_ucore",
    "labbench_ucore",
    "labdemo_ucore",
    "agentsecurity_ucore",
    "agenttoolabi_ucore",
    "agentscope_ucore",
    "agenttrust_ucore",
    "agentvfs_ucore",
    "iobudget_ucore",
    "usersafety_ucore",
    "blocking_semantics_ucore",
)
AGENT_CASES = tuple(
    RegressionCase(
        name,
        "scripts/run-agent-tests.sh",
        12 if name in {"agentfinal_ucore", "agentfs_ucore", "labdemo_ucore"} else 8,
        agent_case=name,
    )
    for name in AGENT_CASE_NAMES
)

# Preserve the original import surface used by the runner's contract tests.
CASES = RESOURCE_CASES
CASE_BY_LABEL = {case.label: case for case in CASES}

SANITIZED_KEYS = {
    "MAKEFLAGS",
    "MFLAGS",
    "MAKEOVERRIDES",
    "GNUMAKEFLAGS",
    "AGENTOS_BUILD_JOBS",
    "AGENTOS_TEST_JOBS",
    "AGENT_TEST_CASE",
    "AGENT_TEST_TIMING_FILE",
    "AGENT_TEST_GUEST_LOG_FILE",
    "AGENT_TEST_DURATION_PROFILE",
    "AGENT_TEST_CALIBRATE",
    "REQUIRE_FULL_SUITE",
    "FINAL_EVIDENCE_STAGE",
    "EVIDENCE_INCOMING_DIR",
    "EVIDENCE_WORK_DIR",
    "EVIDENCE_STEPS_FILE",
    "EVIDENCE_GUEST_LOG_FILE",
    "FS_ALLOCATOR_ARTIFACT_DIR",
    "FS_ALLOCATOR_EVIDENCE_ARCHIVE",
    "FS_EPOCH_ARTIFACT_DIR",
    "FSEPOCH_QEMU_JOBS",
    "INFLIGHT_DELAY_CANDIDATES",
    "INFLIGHT_MAX_ATTEMPTS",
    "OBSERVE_RECOVERY_SNAPSHOT_FILE",
    "OBSERVE_RECOVERY_ERASED_SNAPSHOT_FILE",
    "BASH_ENV",
    "ENV",
    "CDPATH",
    "SHELLOPTS",
    "BASHOPTS",
}
GIT_ENV_DROP = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_ASKPASS",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_WORK_TREE",
}


class RegressionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceIdentity:
    commit: str
    tree: str
    base_commit: str = ""
    dirty: bool = False
    manifest_sha256: str = ""
    manifest_files: int = 0


@dataclass(frozen=True)
class CaseResult:
    case: RegressionCase
    lane: int
    status: int
    started_ns: int
    ended_ns: int
    stdout_file: Path
    guest_file: Path
    combined_file: Path
    detail: str = ""


def git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in GIT_ENV_DROP:
        environment.pop(name, None)
    for name in tuple(environment):
        if name.startswith("GIT_CONFIG_KEY_") or name.startswith(
            "GIT_CONFIG_VALUE_"
        ):
            environment.pop(name, None)
    return environment


def git(
    root: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
    input_data: bytes | None = None,
) -> bytes:
    if environment is None:
        environment = git_environment()
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        env=environment,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RegressionError(
            f"git {' '.join(arguments)} failed with {result.returncode}: {detail}"
        )
    return result.stdout


def source_identity(root: Path) -> SourceIdentity:
    status_before = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status_before:
        raise RegressionError(
            "parallel QEMU lanes require a clean committed source tree"
        )
    commit = git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    tree = git(root, "rev-parse", "--verify", "HEAD^{tree}").decode("ascii").strip()
    manifest_sha256, manifest_files = source_manifest(root)
    status_after = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    final_manifest, final_files = source_manifest(root)
    if (
        status_after
        or final_manifest != manifest_sha256
        or final_files != manifest_files
    ):
        raise RegressionError("source changed while manifest was captured")
    return SourceIdentity(
        commit, tree, commit, False, manifest_sha256, manifest_files
    )


def source_paths(root: Path) -> tuple[str, ...]:
    payload = git(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    )
    names = tuple(
        sorted(
            entry.decode("utf-8", errors="surrogateescape")
            for entry in payload.split(b"\0")
            if entry
        )
    )
    for name in names:
        basename = Path(name).name.lower()
        if basename == "tokens.txt" or basename.endswith("_api.txt"):
            raise RegressionError(f"secret-like source path is not allowed: {name}")
    return names


def source_manifest(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    names = source_paths(root)
    for name in names:
        path = root / name
        try:
            status = path.lstat()
            if path.is_symlink():
                payload = os.readlink(path).encode(
                    "utf-8", errors="surrogateescape"
                )
                kind = b"link"
            elif path.is_file():
                payload = path.read_bytes()
                kind = b"file"
            else:
                raise RegressionError(f"unsupported source path type: {name}")
        except FileNotFoundError as error:
            raise RegressionError(
                f"source changed while manifest was captured: {name}"
            ) from error
        encoded_name = name.encode("utf-8", errors="surrogateescape")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(kind)
        digest.update((status.st_mode & 0o777).to_bytes(4, "big"))
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest(), len(names)


def snapshot_source(root: Path, evidence_mode: bool) -> SourceIdentity:
    try:
        return source_identity(root)
    except RegressionError:
        if evidence_mode:
            raise

    base_commit = git(root, "rev-parse", "--verify", "HEAD^{commit}").decode(
        "ascii"
    ).strip()
    head_tree = git(root, "rev-parse", "--verify", "HEAD^{tree}").decode(
        "ascii"
    ).strip()
    descriptor, index_name = tempfile.mkstemp(prefix="agentos-source-index-")
    os.close(descriptor)
    index_path = Path(index_name)
    index_path.unlink()
    environment = git_environment()
    environment["GIT_INDEX_FILE"] = str(index_path)
    try:
        stable_tree = ""
        stable_manifest = ""
        stable_files = 0
        for _ in range(3):
            before_manifest, before_files = source_manifest(root)
            git(root, "read-tree", base_commit, environment=environment)
            git(root, "add", "-A", "--", ".", environment=environment)
            first = git(root, "write-tree", environment=environment).decode(
                "ascii"
            ).strip()
            git(root, "add", "-A", "--", ".", environment=environment)
            second = git(root, "write-tree", environment=environment).decode(
                "ascii"
            ).strip()
            after_manifest, after_files = source_manifest(root)
            if (
                first == second
                and before_manifest == after_manifest
                and before_files == after_files
            ):
                stable_tree = first
                stable_manifest = after_manifest
                stable_files = after_files
                break
        if not stable_tree:
            raise RegressionError("working tree changed while source was captured")
        if stable_tree == head_tree:
            return SourceIdentity(
                base_commit,
                head_tree,
                base_commit,
                False,
                stable_manifest,
                stable_files,
            )
        commit_environment = environment.copy()
        commit_environment.update(
            {
                "GIT_AUTHOR_NAME": "AgentOS verifier",
                "GIT_AUTHOR_EMAIL": "agentos@example.invalid",
                "GIT_COMMITTER_NAME": "AgentOS verifier",
                "GIT_COMMITTER_EMAIL": "agentos@example.invalid",
            }
        )
        commit = git(
            root,
            "commit-tree",
            stable_tree,
            "-p",
            base_commit,
            environment=commit_environment,
            input_data=b"AgentOS isolated verification snapshot\n",
        ).decode("ascii").strip()
        return SourceIdentity(
            commit,
            stable_tree,
            base_commit,
            True,
            stable_manifest,
            stable_files,
        )
    finally:
        index_path.unlink(missing_ok=True)
        Path(str(index_path) + ".lock").unlink(missing_ok=True)


def write_source_archive(root: Path, output: Path, source: SourceIdentity) -> Path:
    archive = output / "source.tar"
    temporary = output / ".source.tar.partial"
    temporary.write_bytes(
        git(root, "archive", "--format=tar", source.commit)
    )
    temporary.replace(archive)
    return archive


def write_replay_manifest(
    root: Path,
    output: Path,
    source: SourceIdentity,
    suite: str,
    jobs: int,
    build_jobs: int,
    bash: str,
    failed_cases: list[str],
) -> Path:
    archive_name = write_source_archive(root, output, source).name
    runner_argv = [
        sys.executable,
        "scripts/run-parallel-qemu-regressions.py",
        "--root",
        ".",
        "--output-dir",
        "replay-output",
        "--suite",
        suite,
        "--jobs",
        str(jobs),
        "--build-jobs",
        str(build_jobs),
        "--bash",
        bash,
    ]
    for label in failed_cases:
        runner_argv.extend(("--case", label))
    payload = {
        "schema": 1,
        "source": {
            "commit": source.commit,
            "tree": source.tree,
            "base_commit": source.base_commit or source.commit,
            "dirty_snapshot": source.dirty,
            "manifest_sha256": source.manifest_sha256,
            "manifest_files": source.manifest_files,
            "archive": archive_name,
        },
        "failed_cases": failed_cases,
        "restore_argvs": [
            ["mkdir", "replay-source"],
            ["tar", "-xf", archive_name, "-C", "replay-source"],
            ["git", "-C", "replay-source", "init"],
            ["git", "-C", "replay-source", "add", "-A"],
            [
                "git",
                "-C",
                "replay-source",
                "-c",
                "user.name=AgentOS replay",
                "-c",
                "user.email=agentos@example.invalid",
                "commit",
                "-m",
                "verification replay source",
            ],
        ],
        "runner_cwd": "replay-source",
        "runner_argv": runner_argv,
    }
    temporary = output / ".replay.json.partial"
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="ascii"
    )
    manifest = output / "replay.json"
    temporary.replace(manifest)
    return manifest


def materialize_lane(root: Path, lane: Path, source: SourceIdentity) -> None:
    git(root, "worktree", "add", "--quiet", "--detach", str(lane), source.commit)


def remove_lane(root: Path, lane: Path) -> None:
    git(root, "worktree", "remove", "--force", str(lane))


def assign_lanes(
    cases: tuple[RegressionCase, ...], jobs: int
) -> tuple[tuple[RegressionCase, ...], ...]:
    lane_count = min(jobs, len(cases))
    lanes: list[list[RegressionCase]] = [[] for _ in range(lane_count)]
    loads = [0] * lane_count
    order = {case.label: index for index, case in enumerate(cases)}
    for case in sorted(cases, key=lambda item: (-item.weight, order[item.label])):
        lane = min(range(lane_count), key=lambda index: (loads[index], index))
        lanes[lane].append(case)
        loads[lane] += case.weight
    for lane in lanes:
        lane.sort(key=lambda item: order[item.label])
    return tuple(tuple(lane) for lane in lanes)


def child_environment(
    case: RegressionCase,
    output: Path,
    temporary_root: Path,
    build_jobs: int = LANE_BUILD_JOBS,
) -> dict[str, str]:
    environment = os.environ.copy()
    for key in SANITIZED_KEYS:
        environment.pop(key, None)
    for key in tuple(environment):
        if key.startswith("BASH_FUNC_"):
            environment.pop(key, None)
    environment.update(
        {
            "TOOLPREFIX": os.environ.get("TOOLPREFIX", "riscv64-linux-gnu-"),
            "QEMU": os.environ.get("QEMU", "qemu-system-riscv64"),
            "PYTHON_BIN": os.environ.get("PYTHON_BIN", "python3"),
            "CASE_TIMEOUT": os.environ.get("CASE_TIMEOUT", "240s"),
            "IDLE_NOTICE_SECONDS": os.environ.get("IDLE_NOTICE_SECONDS", "20s"),
            "MARKER_GRACE_SECONDS": os.environ.get(
                "MECHANISM_MARKER_GRACE_SECONDS", "5s"
            ),
            "EVIDENCE_GUEST_LOG_FILE": str(output / f"{case.label}.guest"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(temporary_root),
            "TEMP": str(temporary_root),
            "TMP": str(temporary_root),
            "AGENTOS_BUILD_JOBS": str(build_jobs),
            "AGENTOS_TEST_JOBS": "1",
        }
    )
    if case.label == "workflow-teardown-race":
        environment["WORKFLOW_TEARDOWN_STABILITY_RUNS"] = "3"
    elif case.label == "observe-recovery":
        environment["OBSERVE_RECOVERY_SNAPSHOT_FILE"] = str(
            output / "observe-recovery-before-reap.img"
        )
    elif case.label == "fs-allocator-fault":
        environment["FS_ALLOCATOR_ARTIFACT_DIR"] = str(
            output / "fs-allocator-evidence"
        )
        environment["FS_ALLOCATOR_EVIDENCE_ARCHIVE"] = str(
            output / "fs-allocator-evidence.tar"
        )
    elif case.label == "fs-epoch":
        environment["FSEPOCH_QEMU_JOBS"] = "1"
    if case.agent_case is not None:
        environment.update(
            {
                "AGENT_TEST_CASE": case.agent_case,
                "AGENT_TEST_TIMING_FILE": str(
                    output / f"{case.label}.timing"
                ),
                "AGENT_TEST_GUEST_LOG_FILE": str(
                    output / f"{case.label}.guest"
                ),
                "AGENT_TEST_DURATION_PROFILE": "none",
                "AGENT_TEST_CALIBRATE": "0",
                "REQUIRE_FULL_SUITE": "0",
            }
        )
    return environment


def expected_artifacts(case: RegressionCase, output: Path) -> tuple[Path, ...]:
    artifacts = [output / f"{case.label}.guest"]
    if case.agent_case is not None:
        artifacts.append(output / f"{case.label}.timing")
    if case.label == "observe-recovery":
        artifacts.append(output / "observe-recovery-before-reap.img")
    elif case.label == "fs-allocator-fault":
        artifacts.append(output / "fs-allocator-evidence.tar")
    return tuple(artifacts)


def run_case(
    lane_number: int,
    lane: Path,
    case: RegressionCase,
    output_root: Path,
    bash: str,
    build_jobs: int = LANE_BUILD_JOBS,
) -> CaseResult:
    output = output_root / case.label
    output.mkdir(exist_ok=True)
    temporary_root = output / "tmp"
    temporary_root.mkdir()
    stdout_file = output / f"{case.label}.stdout"
    guest_file = output / f"{case.label}.guest"
    combined_file = output / f"{case.label}.combined"
    guest_file.write_bytes(b"")
    started_ns = time.time_ns()
    status, detail = 70, ""
    try:
        runner = lane / case.runner
        if not runner.is_file():
            raise RegressionError(f"runner is missing from lane: {case.runner}")
        with stdout_file.open("wb") as stream:
            result = subprocess.run(
                [bash, *case.shell_flags, case.runner],
                cwd=lane,
                env=child_environment(case, output, temporary_root, build_jobs),
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
            )
        status = result.returncode
        missing = [
            path.name
            for path in expected_artifacts(case, output)
            if not path.is_file() or path.stat().st_size == 0
        ]
        if missing:
            detail = "missing or empty artifacts: " + ", ".join(missing)
            status = status or 65
    except (OSError, RegressionError) as error:
        detail = str(error)
        stdout_file.write_text(detail + "\n", encoding="utf-8")
        status = 70
    ended_ns = time.time_ns()
    if status == 0:
        try:
            shutil.rmtree(temporary_root)
        except OSError as error:
            detail = f"temporary directory cleanup failed: {error}"
            status = 70
    stdout = stdout_file.read_bytes() if stdout_file.exists() else b""
    guest = guest_file.read_bytes() if guest_file.exists() else b""
    with combined_file.open("wb") as combined:
        combined.write(f"===== runner-stdout:{case.label} =====\n".encode("ascii"))
        combined.write(stdout)
        if stdout and not stdout.endswith(b"\n"):
            combined.write(b"\n")
        combined.write(f"\n===== runner-guest-logs:{case.label} =====\n".encode("ascii"))
        combined.write(guest)
        if detail:
            combined.write(f"\n===== coordinator-error =====\n{detail}\n".encode())
    return CaseResult(
        case,
        lane_number,
        status,
        started_ns,
        ended_ns,
        stdout_file,
        guest_file,
        combined_file,
        detail,
    )


def run_lane(
    lane_number: int,
    lane: Path,
    cases: tuple[RegressionCase, ...],
    output: Path,
    bash: str,
    build_jobs: int = LANE_BUILD_JOBS,
) -> list[CaseResult]:
    results = []
    for case in cases:
        try:
            result = run_case(
                lane_number, lane, case, output, bash, build_jobs
            )
        except Exception as error:
            detail = f"internal runner failure: {type(error).__name__}: {error}"
            result = internal_failure_result(
                output, case, lane_number, detail
            )
        results.append(result)
    return results


def internal_failure_result(
    output: Path, case: RegressionCase, lane_number: int, detail: str
) -> CaseResult:
    case_output = output / case.label
    case_output.mkdir(exist_ok=True)
    stdout_file = case_output / f"{case.label}.stdout"
    guest_file = case_output / f"{case.label}.guest"
    combined_file = case_output / f"{case.label}.combined"
    stdout_file.write_text(detail + "\n", encoding="utf-8")
    guest_file.touch()
    combined_file.write_text(detail + "\n", encoding="utf-8")
    now = time.time_ns()
    return CaseResult(
        case,
        lane_number,
        70,
        now,
        now,
        stdout_file,
        guest_file,
        combined_file,
        detail,
    )


def published_artifacts(result: CaseResult, output: Path) -> tuple[tuple[Path, str], ...]:
    artifacts: list[tuple[Path, str]] = [
        (result.combined_file, f"{result.case.label}.log")
    ]
    case_output = output / result.case.label
    if result.case.label == "observe-recovery":
        artifacts.append(
            (case_output / "observe-recovery-before-reap.img", "observe-recovery-before-reap.img")
        )
    elif result.case.label == "fs-allocator-fault":
        artifacts.append(
            (case_output / "fs-allocator-evidence.tar", "fs-allocator-evidence.tar")
        )
    return tuple(artifacts)


def write_reports(
    output: Path,
    source: SourceIdentity,
    jobs: int,
    lanes: tuple[tuple[RegressionCase, ...], ...],
    results: dict[str, CaseResult],
    cleanup_errors: list[str],
    cases: tuple[RegressionCase, ...] = CASES,
    suite: str = "resource",
    lane_build_jobs: int = LANE_BUILD_JOBS,
    replay_manifest: str = "",
) -> None:
    ordered = [results[case.label] for case in cases if case.label in results]
    with (output / "combined.log").open("wb") as combined:
        for result in ordered:
            combined.write(result.combined_file.read_bytes())
            combined.write(b"\n")
    with (output / "steps.tsv").open("w", encoding="ascii") as steps:
        for result in ordered:
            names = [name for _, name in published_artifacts(result, output)]
            steps.write(
                f"{result.case.label}\t{result.started_ns / 1e9:.9f}"
                f"\t{result.ended_ns / 1e9:.9f}"
                + "".join(f"\t{name}" for name in names)
                + "\n"
            )
    with (output / "artifacts.tsv").open("w", encoding="ascii") as artifacts:
        for result in ordered:
            for source_path, name in published_artifacts(result, output):
                artifacts.write(
                    f"{name}\t{source_path.relative_to(output).as_posix()}\n"
                )
    if suite == "agent":
        with (output / "agent-suite-guest.log").open("wb") as guest_stream:
            for result in ordered:
                payload = result.guest_file.read_bytes()
                guest_stream.write(payload)
                if payload and not payload.endswith(b"\n"):
                    guest_stream.write(b"\n")
        with (output / "agent-suite-timings.log").open(
            "wb"
        ) as timing_stream:
            for result in ordered:
                timing = output / result.case.label / f"{result.case.label}.timing"
                if timing.is_file():
                    payload = timing.read_bytes()
                    timing_stream.write(payload)
                    if payload and not payload.endswith(b"\n"):
                        timing_stream.write(b"\n")
    payload = {
        "schema": 1,
        "suite": suite,
        "source": {
            "commit": source.commit,
            "tree": source.tree,
            "base_commit": source.base_commit or source.commit,
            "dirty_snapshot": source.dirty,
            "manifest_sha256": source.manifest_sha256,
            "manifest_files": source.manifest_files,
        },
        "requested_jobs": jobs,
        "effective_jobs": len(lanes),
        "lane_build_jobs": lane_build_jobs,
        "case_order": [result.case.label for result in ordered],
        "lanes": [[case.label for case in lane] for lane in lanes],
        "results": [
            {
                "case": result.case.label,
                "lane": result.lane,
                "status": result.status,
                "elapsed_ms": (result.ended_ns - result.started_ns) // 1_000_000,
                "detail": result.detail,
                "artifacts": [
                    {
                        "name": name,
                        "source": path.relative_to(output).as_posix(),
                    }
                    for path, name in published_artifacts(result, output)
                ],
            }
            for result in ordered
        ],
        "cleanup_errors": cleanup_errors,
        "replay_manifest": replay_manifest,
    }
    temporary = output / ".run-summary.json.partial"
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="ascii"
    )
    temporary.replace(output / "run-summary.json")


def select_cases(
    names: list[str] | None,
    available: tuple[RegressionCase, ...] = CASES,
) -> tuple[RegressionCase, ...]:
    if not names:
        return available
    if len(set(names)) != len(names):
        raise RegressionError("duplicate --case value")
    selected = set(names)
    known = {case.label for case in available}
    unknown = sorted(selected - known)
    if unknown:
        raise RegressionError("unknown case: " + ", ".join(unknown))
    return tuple(case for case in available if case.label in selected)


def jobs_value(value: str) -> int:
    try:
        jobs = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("jobs must be an integer") from error
    if not 1 <= jobs <= MAX_JOBS:
        raise argparse.ArgumentTypeError(f"jobs must be between 1 and {MAX_JOBS}")
    return jobs


def build_jobs_value(value: str) -> int:
    try:
        jobs = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("build jobs must be an integer") from error
    if not 1 <= jobs <= MAX_BUILD_JOBS:
        raise argparse.ArgumentTypeError(
            f"build jobs must be between 1 and {MAX_BUILD_JOBS}"
        )
    return jobs


def build_jobs_per_lane(total_jobs: int, lane_count: int) -> int:
    if lane_count < 1:
        raise RegressionError("at least one execution lane is required")
    return max(1, total_jobs // lane_count)


def execution_jobs(requested: int, evidence_mode: bool) -> int:
    return 1 if evidence_mode else requested


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument(
        "--jobs",
        type=jobs_value,
        default=os.environ.get("AGENTOS_QEMU_JOBS", str(DEFAULT_JOBS)),
    )
    parser.add_argument(
        "--build-jobs",
        type=build_jobs_value,
        default=os.environ.get(
            "AGENTOS_BUILD_JOBS", str(DEFAULT_JOBS * LANE_BUILD_JOBS)
        ),
    )
    parser.add_argument("--bash", default=os.environ.get("BASH_BIN", "bash"))
    parser.add_argument("--keep-worktrees", action="store_true")
    parser.add_argument("--suite", choices=("resource", "agent"), default="resource")
    parser.add_argument("--case", action="append")
    parser.add_argument("--list-cases", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    available = AGENT_CASES if args.suite == "agent" else RESOURCE_CASES
    if args.list_cases:
        print(*(case.label for case in available), sep="\n")
        return 0
    if args.output_dir is None:
        print("parallel-qemu: --output-dir is required", file=sys.stderr)
        return 2
    try:
        cases = select_cases(args.case, available)
        root = Path(
            git(args.root.resolve(), "rev-parse", "--show-toplevel")
            .decode("utf-8", errors="surrogateescape")
            .strip()
        ).resolve()
        output = args.output_dir.resolve()
        if output.exists():
            raise RegressionError(f"output directory already exists: {output}")
        evidence_stage_value = os.environ.get("FINAL_EVIDENCE_STAGE")
        evidence_mode = bool(evidence_stage_value)
        if evidence_mode:
            evidence_stage = Path(evidence_stage_value).resolve()
            if not output.is_relative_to(evidence_stage):
                raise RegressionError(
                    "evidence output must remain inside FINAL_EVIDENCE_STAGE"
                )
            if args.work_root is not None:
                raise RegressionError("evidence mode does not permit --work-root")
        work_parent = args.work_root.resolve() if args.work_root else root / "build"
        work_parent.mkdir(parents=True, exist_ok=True)
        source = snapshot_source(root, evidence_mode)
        run_root = Path(tempfile.mkdtemp(prefix="agentos-qemu-lanes-", dir=work_parent))
        output.mkdir(parents=True)
        execution_lane_limit = min(
            execution_jobs(args.jobs, evidence_mode), args.build_jobs
        )
        lanes = assign_lanes(cases, execution_lane_limit)
        lane_build_jobs = build_jobs_per_lane(args.build_jobs, len(lanes))
        lane_paths = tuple(
            run_root / f"lane-{index + 1:02d}" / "source"
            for index in range(len(lanes))
        )
        created: list[Path] = []
        cleanup_errors: list[str] = []
        results: dict[str, CaseResult] = {}
        try:
            for lane in lane_paths:
                materialize_lane(root, lane, source)
                created.append(lane)
            print(
                f"[parallel-qemu] cases={len(cases)} jobs={len(lanes)} "
                f"build_jobs_per_lane={lane_build_jobs} "
                f"source={source.commit[:12]} dirty={int(source.dirty)} "
                f"evidence={int(evidence_mode)}",
                flush=True,
            )
            for number, lane_cases in enumerate(lanes, 1):
                print(
                    f"[parallel-qemu] lane={number} "
                    + ",".join(case.label for case in lane_cases),
                    flush=True,
                )
            with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
                futures = {
                    pool.submit(
                        run_lane,
                        index + 1,
                        lane_paths[index],
                        lane_cases,
                        output,
                        args.bash,
                        lane_build_jobs,
                    ): index
                    for index, lane_cases in enumerate(lanes)
                }
                for future in as_completed(futures):
                    try:
                        lane_results = future.result()
                    except Exception as error:
                        lane_number = futures[future] + 1
                        detail = (
                            f"lane {lane_number} failed internally: "
                            f"{type(error).__name__}: {error}"
                        )
                        lane_results = [
                            internal_failure_result(
                                output, case, lane_number, detail
                            )
                            for case in lanes[lane_number - 1]
                            if case.label not in results
                        ]
                    for result in lane_results:
                        results[result.case.label] = result
        finally:
            if not args.keep_worktrees:
                for lane in reversed(created):
                    try:
                        remove_lane(root, lane)
                    except RegressionError as error:
                        cleanup_errors.append(str(error))
                try:
                    git(root, "worktree", "prune")
                except RegressionError as error:
                    cleanup_errors.append(str(error))
                if not cleanup_errors:
                    shutil.rmtree(run_root)

        for lane_number, lane_cases in enumerate(lanes, 1):
            for case in lane_cases:
                if case.label not in results:
                    results[case.label] = internal_failure_result(
                        output,
                        case,
                        lane_number,
                        "coordinator did not receive a case result",
                    )
        failed_cases = [
            case.label for case in cases if results[case.label].status != 0
        ]
        replay_manifest = ""
        if failed_cases or cleanup_errors:
            try:
                replay_manifest = write_replay_manifest(
                    root,
                    output,
                    source,
                    args.suite,
                    args.jobs,
                    args.build_jobs,
                    args.bash,
                    failed_cases,
                ).name
            except (OSError, RegressionError) as error:
                cleanup_errors.append(f"could not retain replay source: {error}")
        write_reports(
            output,
            source,
            args.jobs,
            lanes,
            results,
            cleanup_errors,
            cases,
            args.suite,
            lane_build_jobs,
            replay_manifest,
        )
        failed = 0
        for case in cases:
            result = results[case.label]
            elapsed_ms = (result.ended_ns - result.started_ns) // 1_000_000
            state = "ok" if result.status == 0 else "failed"
            print(
                f"[parallel-qemu] {case.label} status={state} "
                f"exit={result.status} lane={result.lane} elapsed_ms={elapsed_ms}",
                flush=True,
            )
            payload = result.combined_file.read_bytes()
            sys.stdout.buffer.write(payload)
            if payload and not payload.endswith(b"\n"):
                sys.stdout.buffer.write(b"\n")
            sys.stdout.buffer.flush()
            failed += result.status != 0
        if cleanup_errors:
            for error in cleanup_errors:
                print(f"parallel-qemu: cleanup failed: {error}", file=sys.stderr)
            return 2
        print(
            f"[parallel-qemu] total={len(cases)} failed={failed} jobs={len(lanes)}",
            flush=True,
        )
        return 1 if failed else 0
    except (OSError, RegressionError) as error:
        print(f"parallel-qemu: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
