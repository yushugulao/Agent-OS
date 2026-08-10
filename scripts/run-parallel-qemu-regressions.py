#!/usr/bin/env python3
"""Run the real QEMU regression cases in bounded, isolated lanes."""

from __future__ import annotations

import argparse
import ctypes
from concurrent.futures import ThreadPoolExecutor, as_completed
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time


HOST_TOOLS = Path(__file__).resolve().parents[1] / "host_tools"
sys.path.insert(0, str(HOST_TOOLS))
from plain_ucore_action_runner import RepoRunBusy, exclusive_repo_run_lock


MAX_JOBS = 8
LANE_BUILD_JOBS = 2
MAX_BUILD_JOBS = 24
DEFAULT_CASE_TIMEOUT_SECONDS = 3600
MAX_CASE_TIMEOUT_SECONDS = 14400


def adaptive_jobs(kind: str) -> int:
    resource_jobs = runpy.run_path(
        str(Path(__file__).with_name("resource-jobs.py"))
    )
    return resource_jobs["choose_jobs"](
        kind, memory=resource_jobs["available_memory"]()
    )


def environment_or_adaptive_jobs(name: str, kind: str) -> str:
    value = os.environ.get(name)
    return value if value is not None else str(adaptive_jobs(kind))


def outer_make_job_limit(environment: dict[str, str] | None = None) -> int | None:
    values = environment if environment is not None else os.environ
    limits: list[int] = []
    for variable in ("MAKEFLAGS", "MFLAGS"):
        try:
            tokens = shlex.split(values.get(variable, ""))
        except ValueError as error:
            raise RegressionError(f"{variable} is malformed") from error
        index = 0
        while index < len(tokens):
            token = tokens[index]
            value = ""
            if token.startswith("-j") and token != "-j":
                value = token[2:]
            elif token.startswith("--jobs="):
                value = token.split("=", 1)[1]
            elif token in {"-j", "--jobs"} and index + 1 < len(tokens):
                candidate = tokens[index + 1]
                if candidate.isdecimal():
                    value = candidate
                    index += 1
            if value:
                if not value.isdecimal() or int(value, 10) < 1:
                    raise RegressionError(f"{variable} has an invalid job limit")
                limits.append(int(value, 10))
            index += 1
    return min(limits) if limits else None


def bounded_build_jobs(
    requested: int, environment: dict[str, str] | None = None
) -> tuple[int, int | None]:
    limit = outer_make_job_limit(environment)
    available = max(1, limit - 1) if limit is not None else requested
    return min(requested, available), limit


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
    "agentcontract_ucore",
    "agent_eevdf_ucore",
    "agenttask_ucore",
    "ch8_cow_ucore",
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

# Kept as the small import surface used by local runner tests.
CASES = RESOURCE_CASES
CASE_BY_LABEL = {case.label: case for case in CASES}

SANITIZED_KEYS = {
    "MAKEFLAGS",
    "MFLAGS",
    "MAKEOVERRIDES",
    "GNUMAKEFLAGS",
    "AGENTOS_BUILD_JOBS",
    "AGENTOS_OUTER_JOBS",
    "AGENTOS_PARALLEL_DEPTH",
    "AGENTOS_QEMU_JOBS",
    "AGENTOS_TEST_JOBS",
    "AGENT_TEST_CASE",
    "AGENT_TEST_TIMING_FILE",
    "REQUIRE_FULL_SUITE",
    "FS_ALLOCATOR_ARTIFACT_DIR",
    "FS_EPOCH_ARTIFACT_DIR",
    "FSEPOCH_QEMU_JOBS",
    "INFLIGHT_DELAY_CANDIDATES",
    "INFLIGHT_MAX_ATTEMPTS",
    "WORKFLOW_TEARDOWN_RUNS",
    "WORKFLOW_TEARDOWN_STABILITY_RUNS",
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
class CaseResult:
    case: RegressionCase
    lane: int
    status: int
    started_ns: int
    ended_ns: int
    log_file: Path
    timing_file: Path | None
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


def git(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        env=git_environment(),
        stdin=subprocess.DEVNULL,
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


def repository_root(path: Path) -> Path:
    return Path(
        git(path, "rev-parse", "--show-toplevel")
        .decode("utf-8", errors="surrogateescape")
        .strip()
    ).resolve()


def workspace_paths(root: Path) -> tuple[str, ...]:
    """Return the tracked and ordinary untracked files in the current workspace."""
    payload = git(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    )
    paths = tuple(
        sorted(
            entry.decode("utf-8", errors="surrogateescape")
            for entry in payload.split(b"\0")
            if entry
        )
    )
    for relative in paths:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise RegressionError(f"unsafe workspace path returned by git: {relative}")
    return paths


def materialize_lane(
    root: Path, lane: Path, paths: tuple[str, ...] | None = None
) -> None:
    """Copy the live workspace into a lane, including uncommitted product fixes."""
    if lane.exists():
        raise RegressionError(f"lane already exists: {lane}")
    lane.mkdir(parents=True)
    try:
        for relative in paths if paths is not None else workspace_paths(root):
            source = root / relative
            if not source.exists() and not source.is_symlink():
                # A deleted tracked file is intentionally absent from the snapshot.
                continue
            destination = lane / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                os.symlink(
                    os.readlink(source),
                    destination,
                    target_is_directory=source.is_dir(),
                )
            elif source.is_file():
                shutil.copy2(source, destination)
            elif source.is_dir():
                shutil.copytree(source, destination, symlinks=True)
            else:
                raise RegressionError(f"unsupported workspace path: {relative}")
    except (OSError, RegressionError):
        shutil.rmtree(lane, ignore_errors=True)
        raise


def remove_lane(root: Path, lane: Path) -> None:
    del root
    shutil.rmtree(lane)


def assign_lanes(
    cases: tuple[RegressionCase, ...], jobs: int
) -> tuple[tuple[RegressionCase, ...], ...]:
    lane_count = min(jobs, len(cases))
    if lane_count < 1:
        raise RegressionError("at least one case and one lane are required")
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
    case_output: Path,
    temporary_root: Path,
    build_jobs: int = LANE_BUILD_JOBS,
    outer_jobs: int = 1,
    parallel_depth: int = 1,
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
                (
                    "MARKER_GRACE_SECONDS"
                    if case.agent_case is not None
                    else "MECHANISM_MARKER_GRACE_SECONDS"
                ),
                "2s" if case.agent_case is not None else "5s",
            ),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(temporary_root),
            "TEMP": str(temporary_root),
            "TMP": str(temporary_root),
            "AGENTOS_BUILD_JOBS": str(build_jobs),
            "AGENTOS_OUTER_JOBS": str(outer_jobs),
            "AGENTOS_PARALLEL_DEPTH": str(parallel_depth),
            "AGENTOS_QEMU_JOBS": "1",
            "AGENTOS_TEST_JOBS": "1",
        }
    )
    if case.label == "workflow-teardown-race":
        environment["WORKFLOW_TEARDOWN_RUNS"] = "1"
    elif case.label == "fs-epoch":
        environment["FSEPOCH_QEMU_JOBS"] = "1"
    if case.agent_case is not None:
        environment.update(
            {
                "AGENT_TEST_CASE": case.agent_case,
                "AGENT_TEST_TIMING_FILE": str(
                    case_output / f"{case.label}.timing"
                ),
                "REQUIRE_FULL_SUITE": "0",
            }
        )
    return environment


def windows_job_launcher(command: list[str]) -> int:
    if os.name != "nt" or not command or sys.stdin.buffer.read(1) != b"1":
        return 125
    return subprocess.call(command, stdin=subprocess.DEVNULL)


def run_case(
    lane_number: int,
    lane: Path,
    case: RegressionCase,
    output_root: Path,
    bash: str,
    build_jobs: int = LANE_BUILD_JOBS,
    timeout: int = DEFAULT_CASE_TIMEOUT_SECONDS,
    outer_jobs: int = 1,
    parallel_depth: int = 1,
) -> CaseResult:
    case_output = output_root / case.label
    case_output.mkdir(parents=True, exist_ok=True)
    temporary_root = case_output / "tmp"
    temporary_root.mkdir()
    log_file = case_output / f"{case.label}.log"
    timing_file = (
        case_output / f"{case.label}.timing"
        if case.agent_case is not None
        else None
    )
    started_ns = time.time_ns()
    status, detail = 70, ""
    try:
        runner = lane / case.runner
        if not runner.is_file():
            raise RegressionError(f"runner is missing from lane: {case.runner}")
        command = [bash, *case.shell_flags, case.runner]
        process_stdin = subprocess.DEVNULL
        if os.name == "nt":
            command = [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(Path(__file__).resolve()),
                "--windows-job-launch",
                *command,
            ]
            process_stdin = subprocess.PIPE
        with log_file.open("wb") as stream:
            stream.write(
                (f"[parallel-qemu] lane={lane_number} case={case.label}\n").encode(
                    "ascii"
                )
            )
            stream.flush()
            process = subprocess.Popen(
                command,
                cwd=lane,
                env=child_environment(
                    case,
                    case_output,
                    temporary_root,
                    build_jobs,
                    outer_jobs,
                    parallel_depth,
                ),
                stdin=process_stdin,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
            )
            windows_job = windows_kill_job(process)
            if os.name == "nt":
                if windows_job is None or process.stdin is None:
                    process.kill()
                    process.wait()
                    raise RegressionError("could not contain the Windows runner process")
                process.stdin.write(b"1")
                process.stdin.close()
                process.stdin = None
            try:
                status = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                detail = f"timeout after {timeout}s"
                stream.write(f"parallel-qemu: {detail}\n".encode("ascii"))
                stream.flush()
                terminate_process_tree(process, windows_job)
                windows_job = None
                status = 124
            finally:
                close_windows_kill_job(windows_job)
        if status == 0 and timing_file is not None:
            if not timing_file.is_file() or timing_file.stat().st_size == 0:
                detail = "agent case did not write timing output"
                status = 65
    except (OSError, RegressionError) as error:
        detail = str(error)
        status = 70
    finally:
        try:
            shutil.rmtree(temporary_root)
        except OSError as error:
            cleanup_detail = f"temporary directory cleanup failed: {error}"
            detail = f"{detail}; {cleanup_detail}" if detail else cleanup_detail
            status = 70
    ended_ns = time.time_ns()
    if detail:
        with log_file.open("ab") as stream:
            stream.write(f"parallel-qemu: {detail}\n".encode("utf-8"))
    return CaseResult(
        case,
        lane_number,
        status,
        started_ns,
        ended_ns,
        log_file,
        timing_file,
        detail,
    )


def run_lane(
    lane_number: int,
    lane: Path,
    cases: tuple[RegressionCase, ...],
    output: Path,
    bash: str,
    build_jobs: int = LANE_BUILD_JOBS,
    timeout: int = DEFAULT_CASE_TIMEOUT_SECONDS,
    outer_jobs: int = 1,
    parallel_depth: int = 1,
) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in cases:
        try:
            result = run_case(
                lane_number,
                lane,
                case,
                output,
                bash,
                build_jobs,
                timeout,
                outer_jobs,
                parallel_depth,
            )
        except Exception as error:
            detail = f"internal runner failure: {type(error).__name__}: {error}"
            result = internal_failure_result(output, case, lane_number, detail)
        results.append(result)
    return results


def internal_failure_result(
    output: Path, case: RegressionCase, lane_number: int, detail: str
) -> CaseResult:
    case_output = output / case.label
    case_output.mkdir(parents=True, exist_ok=True)
    log_file = case_output / f"{case.label}.log"
    log_file.write_text(detail + "\n", encoding="utf-8")
    now = time.time_ns()
    return CaseResult(case, lane_number, 70, now, now, log_file, None, detail)


def write_summary(
    output: Path,
    requested_jobs: int,
    requested_build_jobs: int,
    effective_build_jobs: int,
    outer_limit: int | None,
    lanes: tuple[tuple[RegressionCase, ...], ...],
    lane_build_jobs: tuple[int, ...],
    results: dict[str, CaseResult],
    cleanup_errors: list[str],
    cases: tuple[RegressionCase, ...],
    suite: str,
    timeout: int,
) -> None:
    ordered = [results[case.label] for case in cases]
    payload = {
        "schema": 1,
        "suite": suite,
        "requested_jobs": requested_jobs,
        "effective_jobs": len(lanes),
        "requested_build_jobs": requested_build_jobs,
        "effective_build_jobs": effective_build_jobs,
        "outer_make_job_limit": outer_limit,
        "lane_build_job_slots": list(lane_build_jobs),
        "case_timeout_seconds": timeout,
        "case_order": [case.label for case in cases],
        "lanes": [[case.label for case in lane] for lane in lanes],
        "results": [
            {
                "case": result.case.label,
                "lane": result.lane,
                "status": result.status,
                "elapsed_ms": (result.ended_ns - result.started_ns) // 1_000_000,
                "detail": result.detail,
                "log": result.log_file.relative_to(output).as_posix(),
                "timing": (
                    result.timing_file.relative_to(output).as_posix()
                    if result.timing_file is not None
                    and result.timing_file.is_file()
                    else ""
                ),
            }
            for result in ordered
        ],
        "cleanup_errors": cleanup_errors,
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


def allocate_build_jobs(total_jobs: int, lane_count: int) -> tuple[int, ...]:
    if lane_count < 1:
        raise RegressionError("at least one execution lane is required")
    if total_jobs < lane_count:
        raise RegressionError("build job budget is smaller than execution lanes")
    base, remainder = divmod(total_jobs, lane_count)
    return tuple(base + int(index < remainder) for index in range(lane_count))


def timeout_value(value: str) -> int:
    try:
        timeout = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be an integer") from error
    if not 1 <= timeout <= MAX_CASE_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"timeout must be between 1 and {MAX_CASE_TIMEOUT_SECONDS} seconds"
        )
    return timeout


def environment_job_value(name: str, minimum: int = 1) -> int | None:
    value = os.environ.get(name)
    if value is None:
        return None
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise RegressionError(f"{name} must be an integer") from error
    if parsed < minimum:
        qualifier = "positive " if minimum == 1 else "non-negative "
        raise RegressionError(f"{name} must be a {qualifier}integer")
    return parsed


def windows_kill_job(process: subprocess.Popen[bytes]) -> int | None:
    if os.name != "nt":
        return None

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_longlong),
            ("job_time", ctypes.c_longlong),
            ("flags", wintypes.DWORD),
            ("minimum_working_set", ctypes.c_size_t),
            ("maximum_working_set", ctypes.c_size_t),
            ("active_processes", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("read_operations", ctypes.c_ulonglong),
            ("write_operations", ctypes.c_ulonglong),
            ("other_operations", ctypes.c_ulonglong),
            ("read_bytes", ctypes.c_ulonglong),
            ("write_bytes", ctypes.c_ulonglong),
            ("other_bytes", ctypes.c_ulonglong),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits),
            ("io", IoCounters),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t),
            ("peak_job_memory", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = ExtendedLimits()
    limits.basic.flags = 0x00002000
    configured = kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
    )
    assigned = configured and kernel32.AssignProcessToJobObject(
        job, wintypes.HANDLE(process._handle)
    )
    if not assigned:
        kernel32.CloseHandle(job)
        return None
    return int(job)


def close_windows_kill_job(job: int | None) -> None:
    if job is not None:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
            wintypes.HANDLE(job)
        )


def terminate_process_tree(
    process: subprocess.Popen[bytes], windows_job: int | None = None
) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        if windows_job is not None:
            close_windows_kill_job(windows_job)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        else:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if process.poll() is None:
        process.kill()
    process.wait()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument(
        "--jobs",
        type=jobs_value,
        default=environment_or_adaptive_jobs("AGENTOS_QEMU_JOBS", "qemu"),
    )
    parser.add_argument(
        "--build-jobs",
        type=build_jobs_value,
        default=environment_or_adaptive_jobs("AGENTOS_BUILD_JOBS", "build"),
    )
    parser.add_argument(
        "--timeout",
        type=timeout_value,
        default=os.environ.get(
            "AGENTOS_QEMU_CASE_TIMEOUT", str(DEFAULT_CASE_TIMEOUT_SECONDS)
        ),
        help="whole shell-case timeout in seconds",
    )
    parser.add_argument("--bash", default=os.environ.get("BASH_BIN", "bash"))
    parser.add_argument(
        "--keep-worktrees",
        action="store_true",
        help="keep the temporary lane copies for debugging",
    )
    parser.add_argument("--suite", choices=("resource", "agent"), default="resource")
    parser.add_argument("--case", action="append")
    parser.add_argument("--list-cases", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_lock = None
    available = AGENT_CASES if args.suite == "agent" else RESOURCE_CASES
    if args.list_cases:
        print(*(case.label for case in available), sep="\n")
        return 0
    if args.output_dir is None:
        print("parallel-qemu: --output-dir is required", file=sys.stderr)
        return 2
    try:
        cases = select_cases(args.case, available)
        root = repository_root(args.root.resolve())
        output = args.output_dir.resolve()
        if output.exists():
            raise RegressionError(f"output directory already exists: {output}")

        candidate_lock = exclusive_repo_run_lock(root)
        try:
            candidate_lock.__enter__()
        except (OSError, RepoRunBusy, ValueError) as error:
            raise RegressionError(f"parallel QEMU repository is busy: {error}") from error
        run_lock = candidate_lock

        source_paths = workspace_paths(root)
        work_parent = args.work_root.resolve() if args.work_root else root / "build"
        work_parent.mkdir(parents=True, exist_ok=True)
        run_root = Path(tempfile.mkdtemp(prefix="agentos-qemu-lanes-", dir=work_parent))
        output.mkdir(parents=True)
        effective_build_jobs, parent_make_limit = bounded_build_jobs(args.build_jobs)
        lane_limit = min(args.jobs, effective_build_jobs)
        lanes = assign_lanes(cases, lane_limit)
        lane_build_jobs = allocate_build_jobs(effective_build_jobs, len(lanes))
        parent_outer_jobs = environment_job_value("AGENTOS_OUTER_JOBS") or 1
        parent_depth = environment_job_value(
            "AGENTOS_PARALLEL_DEPTH", minimum=0
        ) or 0
        lane_paths = tuple(
            run_root / f"lane-{index + 1:02d}" / "source"
            for index in range(len(lanes))
        )
        created: list[Path] = []
        cleanup_errors: list[str] = []
        results: dict[str, CaseResult] = {}
        try:
            for lane in lane_paths:
                materialize_lane(root, lane, source_paths)
                created.append(lane)
            print(
                f"[parallel-qemu] cases={len(cases)} jobs={len(lanes)} "
                "build_jobs_by_lane="
                f"{','.join(str(value) for value in lane_build_jobs)} "
                "source=current-worktree",
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
                        lane_build_jobs[index],
                        args.timeout,
                        parent_outer_jobs * len(lanes),
                        parent_depth + 1,
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
                            internal_failure_result(output, case, lane_number, detail)
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
                    except OSError as error:
                        cleanup_errors.append(str(error))
                if not cleanup_errors:
                    shutil.rmtree(run_root)
            else:
                print(f"[parallel-qemu] kept lanes at {run_root}", flush=True)

        for lane_number, lane_cases in enumerate(lanes, 1):
            for case in lane_cases:
                if case.label not in results:
                    results[case.label] = internal_failure_result(
                        output,
                        case,
                        lane_number,
                        "coordinator did not receive a case result",
                    )
        write_summary(
            output,
            args.jobs,
            args.build_jobs,
            effective_build_jobs,
            parent_make_limit,
            lanes,
            lane_build_jobs,
            results,
            cleanup_errors,
            cases,
            args.suite,
            args.timeout,
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
            payload = result.log_file.read_bytes()
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
    finally:
        if run_lock is not None:
            run_lock.__exit__(None, None, None)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--windows-job-launch":
        raise SystemExit(windows_job_launcher(sys.argv[2:]))
    raise SystemExit(main())
