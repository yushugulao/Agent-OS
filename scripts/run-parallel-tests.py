#!/usr/bin/env python3
"""Run independent Python contract tests concurrently with ordered output."""

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
        "EVIDENCE_GUEST_LOG_FILE",
        "EVIDENCE_INCOMING_DIR",
        "EVIDENCE_STEPS_FILE",
        "EVIDENCE_WORK_DIR",
        "FINAL_EVIDENCE_STAGE",
        "FS_ALLOCATOR_ARTIFACT_DIR",
        "FS_ALLOCATOR_EVIDENCE_ARCHIVE",
        "GNUMAKEFLAGS",
        "MAKEFILES",
        "MAKEFLAGS",
        "MAKEOVERRIDES",
        "MFLAGS",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "OBSERVE_RECOVERY_ERASED_SNAPSHOT_FILE",
        "OBSERVE_RECOVERY_SNAPSHOT_FILE",
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
) -> TestResult:
    env = os.environ.copy()
    for name in CHILD_ENV_DROP:
        env.pop(name, None)
    for name in tuple(env):
        if name.startswith("BASH_FUNC_"):
            env.pop(name, None)
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "TMPDIR": str(temporary),
        }
    )
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


def source_receipt(root: Path) -> dict[str, object]:
    receipt: dict[str, object] = {}
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{tree}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=True,
            ).stdout
        )
        receipt.update({"commit": commit, "tree": tree, "dirty": dirty})
    except (OSError, subprocess.CalledProcessError):
        receipt["git"] = "unavailable"
    return receipt


def retain_failures(
    root: Path,
    args: argparse.Namespace,
    tests: list[Path],
    results: list[TestResult],
    logs: list[Path],
) -> Path:
    stage = os.environ.get("EVIDENCE_WORK_DIR") or os.environ.get(
        "FINAL_EVIDENCE_STAGE"
    )
    failure_root = args.failure_root
    if failure_root is None:
        failure_root = (
            Path(stage) / "host-test-failures"
            if stage
            else root / "build" / "host-test-failures"
        )
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
        *failed_tests,
    ]
    manifest = {
        "schema": 1,
        "source": source_receipt(root),
        "cwd": str(root),
        "requested_jobs": args.jobs,
        "effective_jobs": 1
        if os.environ.get("FINAL_EVIDENCE_STAGE")
        else min(args.jobs, len(tests)),
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
    except ValueError as exc:
        print(f"parallel-tests: {exc}", file=sys.stderr)
        return 2

    evidence_mode = bool(os.environ.get("FINAL_EVIDENCE_STAGE"))
    effective_jobs = min(1 if evidence_mode else args.jobs, len(tests))
    print(
        f"[parallel-tests] start total={len(tests)} jobs={effective_jobs}",
        flush=True,
    )
    results: list[TestResult | None] = [None] * len(tests)
    with tempfile.TemporaryDirectory(prefix="agentos-host-tests-") as temp_name:
        temp = Path(temp_name)
        logs = [temp / f"{index:03d}.log" for index in range(len(tests))]
        with ThreadPoolExecutor(max_workers=effective_jobs) as pool:
            futures = {}
            for index, test in enumerate(tests):
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
                )
                futures[future] = index
            finished = 0
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
                    root, args, tests, completed_results, logs
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
