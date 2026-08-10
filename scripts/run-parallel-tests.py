#!/usr/bin/env python3
"""并发运行独立 Python 契约测试，并按顺序输出结果。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


MAX_JOBS = 24
MAX_TIMEOUT_SECONDS = 3600
CHILD_ENV_DROP = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "FS_ALLOCATOR_ARTIFACT_DIR",
        "GNUMAKEFLAGS",
        "AGENTOS_BUILD_JOBS",
        "AGENTOS_OUTER_JOBS",
        "AGENTOS_PARALLEL_DEPTH",
        "AGENTOS_QEMU_JOBS",
        "AGENTOS_TEST_JOBS",
        "MAKEFILES",
        "MAKEFLAGS",
        "MAKEOVERRIDES",
        "MFLAGS",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONPATH",
        "PYTHONSTARTUP",
    }
)


@dataclass(frozen=True)
class TestResult:
    returncode: int
    elapsed_ms: int
    detail: str = ""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", required=True, type=int)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--timeout",
        type=int,
        default=os.environ.get("AGENTOS_HOST_TEST_TIMEOUT", "600"),
    )
    parser.add_argument("--failure-root", type=Path)
    parser.add_argument("--no-keep-failures", action="store_true")
    parser.add_argument(
        "--exclusive-test",
        "--exclusive",
        action="append",
        default=[],
        help="test path that must not overlap any other test",
    )
    parser.add_argument("tests", nargs="+")
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.jobs > MAX_JOBS:
        parser.error(f"--jobs must be between 1 and {MAX_JOBS}")
    if args.timeout < 1 or args.timeout > MAX_TIMEOUT_SECONDS:
        parser.error(
            f"--timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds"
        )
    return args


def validate_tests(root: Path, tests: list[str]) -> list[Path]:
    resolved: list[Path] = []
    seen: set[Path] = set()
    for name in tests:
        candidate = (root / name).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"test escapes repository: {name}") from exc
        if candidate in seen:
            raise ValueError(f"duplicate test: {name}")
        if not candidate.is_file():
            raise ValueError(f"test is not a file: {name}")
        seen.add(candidate)
        resolved.append(candidate)
    return resolved


def validate_exclusive_tests(
    root: Path, tests: list[Path], names: list[str]
) -> frozenset[Path]:
    resolved: set[Path] = set()
    inventory = set(tests)
    for name in names:
        candidate = (root / name).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"exclusive test escapes repository: {name}") from exc
        if candidate in resolved:
            raise ValueError(f"duplicate exclusive test: {name}")
        if candidate not in inventory:
            raise ValueError(f"exclusive test is not in the test inventory: {name}")
        resolved.add(candidate)
    return frozenset(resolved)


def execution_batches(
    tests: list[Path], exclusive: frozenset[Path]
) -> tuple[tuple[int, ...], ...]:
    """让独占测试按清单顺序成为共享批次之间的屏障。"""
    batches: list[tuple[int, ...]] = []
    shared: list[int] = []
    for index, test in enumerate(tests):
        if test in exclusive:
            if shared:
                batches.append(tuple(shared))
                shared = []
            batches.append((index,))
        else:
            shared.append(index)
    if shared:
        batches.append(tuple(shared))
    return tuple(batches)


def environment_job_value(
    name: str, minimum: int = 1, maximum: int | None = None
) -> int | None:
    value = os.environ.get(name)
    if value is None:
        return None
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < minimum:
        qualifier = "positive " if minimum == 1 else "non-negative "
        raise ValueError(f"{name} must be a {qualifier}integer")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{name} must not exceed {maximum}")
    return parsed


def terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
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
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if process.poll() is None:
        process.kill()
    process.wait()


def run_one(
    root: Path,
    python: str,
    test: Path,
    log: Path,
    temporary: Path,
    timeout: int,
    parallel_jobs: int = 1,
    parent_outer_jobs: int = 1,
    parent_depth: int = 0,
    build_budget: int | None = None,
) -> TestResult:
    env = os.environ.copy()
    for name in CHILD_ENV_DROP:
        env.pop(name, None)
    for name in tuple(env):
        if name.startswith("BASH_FUNC_"):
            env.pop(name, None)
    env.update(
        {
            "AGENTOS_OUTER_JOBS": str(parent_outer_jobs * parallel_jobs),
            "AGENTOS_PARALLEL_DEPTH": str(parent_depth + 1),
            "AGENTOS_QEMU_JOBS": "1",
            "AGENTOS_TEST_JOBS": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "TMPDIR": str(temporary),
        }
    )
    if build_budget is not None:
        env["AGENTOS_BUILD_JOBS"] = str(max(1, build_budget // parallel_jobs))
    if os.name == "nt":
        env.update({"TEMP": str(temporary), "TMP": str(temporary)})
    started = time.monotonic_ns()
    detail = ""
    with log.open("wb") as stream:
        try:
            process = subprocess.Popen(
                [python, str(test)],
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
            )
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                detail = f"timeout after {timeout}s"
                stream.write(f"parallel-tests: {detail}\n".encode("ascii"))
                stream.flush()
                terminate_process_tree(process)
                returncode = 124
        except OSError as exc:
            message = str(exc).encode("ascii", errors="backslashreplace").decode(
                "ascii"
            )
            detail = f"cannot start Python: {message}"
            stream.write(f"parallel-tests: {detail}\n".encode("ascii"))
            returncode = 126
    elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
    return TestResult(returncode, elapsed_ms, detail)


def retain_failures(
    root: Path,
    args: argparse.Namespace,
    tests: list[Path],
    results: list[TestResult],
    logs: list[Path],
    effective_jobs: int,
    exclusive: frozenset[Path],
) -> Path:
    failure_root = args.failure_root
    if failure_root is None:
        failure_root = root / "build" / "host-test-failures"
    failure_root = failure_root.resolve()
    run_dir = failure_root / f"{time.time_ns()}-{os.getpid()}"
    run_dir.mkdir(parents=True)
    entries = []
    failed_tests = []
    for index, (test, result, log) in enumerate(zip(tests, results, logs)):
        name = f"{index:03d}-{test.name}.log"
        shutil.copyfile(log, run_dir / name)
        relative = test.relative_to(root).as_posix()
        entries.append(
            {
                "test": relative,
                "returncode": result.returncode,
                "elapsed_ms": result.elapsed_ms,
                "detail": result.detail,
                "log": name,
            }
        )
        if result.returncode != 0:
            failed_tests.append(relative)
    replay = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--jobs",
        str(min(args.jobs, len(failed_tests))),
        "--python",
        args.python,
        "--timeout",
        str(args.timeout),
    ]
    failed_set = set(failed_tests)
    for test in tests:
        relative = test.relative_to(root).as_posix()
        if relative in failed_set and test in exclusive:
            replay.extend(("--exclusive-test", relative))
    replay.extend(failed_tests)
    manifest = {
        "schema": 1,
        "cwd": str(root),
        "requested_jobs": args.jobs,
        "effective_jobs": effective_jobs,
        "exclusive_tests": [
            test.relative_to(root).as_posix()
            for test in tests
            if test in exclusive
        ],
        "timeout_seconds": args.timeout,
        "tests": entries,
        "replay_argv": replay,
    }
    temporary = run_dir / ".replay.json.partial"
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(run_dir / "replay.json")
    return run_dir


def main() -> int:
    args = parse_args()
    root = Path.cwd().resolve()
    try:
        tests = validate_tests(root, args.tests)
        exclusive = validate_exclusive_tests(
            root, tests, args.exclusive_test
        )
        parent_outer_jobs = environment_job_value("AGENTOS_OUTER_JOBS") or 1
        parent_depth = environment_job_value(
            "AGENTOS_PARALLEL_DEPTH", minimum=0
        ) or 0
        build_budget = environment_job_value(
            "AGENTOS_BUILD_JOBS", maximum=MAX_JOBS
        )
        test_budget = environment_job_value(
            "AGENTOS_TEST_JOBS", maximum=MAX_JOBS
        )
    except ValueError as exc:
        print(f"parallel-tests: {exc}", file=sys.stderr)
        return 2

    job_limit = min(args.jobs, len(tests))
    if build_budget is not None:
        job_limit = min(job_limit, build_budget)
    # 子测试继承 AGENTOS_TEST_JOBS=1。测试再启动 runner 时必须遵守该预算，
    # 不得递归开启满宽度进程池。
    if test_budget is not None:
        job_limit = min(job_limit, test_budget)
    batches = execution_batches(tests, exclusive)
    effective_jobs = max(min(job_limit, len(batch)) for batch in batches)
    print(
        f"[parallel-tests] start total={len(tests)} jobs={effective_jobs} "
        f"exclusive={len(exclusive)}",
        flush=True,
    )
    results: list[TestResult | None] = [None] * len(tests)
    with tempfile.TemporaryDirectory(prefix="agentos-host-tests-") as temp_name:
        temp = Path(temp_name)
        logs = [temp / f"{index:03d}.log" for index in range(len(tests))]
        finished = 0
        for batch in batches:
            batch_jobs = min(effective_jobs, len(batch))
            with ThreadPoolExecutor(max_workers=batch_jobs) as pool:
                futures = {}
                for index in batch:
                    test = tests[index]
                    temporary = temp / f"{index:03d}.tmp"
                    temporary.mkdir()
                    future = pool.submit(
                        run_one,
                        root,
                        args.python,
                        test,
                        logs[index],
                        temporary,
                        args.timeout,
                        batch_jobs,
                        parent_outer_jobs,
                        parent_depth,
                        build_budget,
                    )
                    futures[future] = index
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        results[index] = future.result()
                    except Exception as exc:
                        detail = f"runner failure: {type(exc).__name__}: {exc}"
                        logs[index].write_text(
                            f"parallel-tests: {detail}\n", encoding="utf-8"
                        )
                        results[index] = TestResult(125, 0, detail)
                    completed = results[index]
                    assert completed is not None
                    finished += 1
                    print(
                        f"[parallel-tests] progress={finished}/{len(tests)} "
                        f"test={tests[index].relative_to(root).as_posix()} "
                        f"result={'ok' if completed.returncode == 0 else 'failed'} "
                        f"elapsed_ms={completed.elapsed_ms}",
                        flush=True,
                    )

        failed = 0
        completed_results: list[TestResult] = []
        for index, test in enumerate(tests):
            result = results[index]
            if result is None:
                result = TestResult(125, 0, "parallel test result missing")
                logs[index].write_text(
                    "parallel-tests: parallel test result missing\n", encoding="ascii"
                )
            completed_results.append(result)
            status, elapsed_ms = result.returncode, result.elapsed_ms
            label = test.relative_to(root).as_posix()
            print(
                f"[parallel-tests] {label} status="
                f"{'ok' if status == 0 else 'failed'} returncode={status} "
                f"elapsed_ms={elapsed_ms}",
                flush=True,
            )
            payload = logs[index].read_bytes()
            if payload:
                sys.stdout.buffer.write(payload)
                sys.stdout.buffer.flush()
                if not payload.endswith(b"\n"):
                    print()
            if status != 0:
                failed += 1
        if failed and not args.no_keep_failures:
            try:
                retained = retain_failures(
                    root,
                    args,
                    tests,
                    completed_results,
                    logs,
                    effective_jobs,
                    exclusive,
                )
                print(f"[parallel-tests] replay={retained / 'replay.json'}")
            except OSError as exc:
                message = str(exc).encode(
                    "ascii", errors="backslashreplace"
                ).decode("ascii")
                print(f"[parallel-tests] replay_error={message}")
        print(
            f"[parallel-tests] total={len(tests)} failed={failed} jobs="
            f"{effective_jobs}"
        )
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
