#!/usr/bin/env python3
"""Regression tests for the stage-only full-verification evidence contract."""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

import full_verification_payload as payload
import formal_python_runtime as python_runtime


COMMIT = "1" * 40
BACKING_PYTHON = getattr(sys, "_agentos_backing_executable", sys.executable)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": digest(path),
    }


def make_payload(root: Path, *, commit: str = COMMIT) -> None:
    collector = payload._collector()
    root.mkdir()
    raw = root / payload.RAW_ROOT
    raw.mkdir(parents=True)
    dynamic = {
        "reader-e2e": [
            "reader-e2e-run-fixture-ucore-build.log",
            "reader-e2e-run-fixture-ucore-run.log",
            "reader-e2e-run-fixture-ucore-run-summary.json",
        ]
    }
    steps: list[dict[str, object]] = []
    artifacts: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, (name, fixed, _patterns) in enumerate(collector.STEP_CONTRACT, 1):
        names = [*fixed, *dynamic.get(name, [])]
        for artifact in names:
            if artifact in seen:
                continue
            seen.add(artifact)
            path = raw / artifact
            path.write_bytes(f"fixture {artifact}\n".encode("utf-8"))
            artifacts.append(
                {"name": artifact, "bytes": path.stat().st_size, "sha256": digest(path)}
            )
        steps.append(
            {
                "name": name,
                "started_epoch": float(index),
                "ended_epoch": float(index) + 0.5,
                "duration_seconds": 0.5,
                "artifacts": names,
            }
        )
    summary = {
        "schema_version": collector.SCHEMA_VERSION,
        "full_verify_profile_version": collector.FULL_VERIFY_PROFILE_VERSION,
        "kind": "agentos-full-verification",
        "status": "passed",
        "commit": commit,
        "completed_at_utc": "2026-07-31T00:00:00Z",
        "settings": {
            "agent_marker_grace_seconds": "2s",
            "mechanism_marker_grace_seconds": "5s",
            "workflow_stability_runs": 3,
        },
        "steps": steps,
        "artifacts": artifacts,
        "step_contract_sha256": collector.step_contract_sha256(steps),
    }
    write_json(root / payload.SUMMARY_NAME, summary)
    log = root / payload.LOG_NAME
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"full verification fixture\n{payload.SUCCESS_MARKER}\n", encoding="utf-8")

    tools: list[dict[str, object]] = []
    tool_paths = {
        "git": "/fixture/bin/git",
        "compiler": "/fixture/bin/riscv64-linux-gnu-gcc",
        "qemu": "/fixture/bin/qemu-system-riscv64",
        "python": "/fixture/bin/python3",
        "make": "/fixture/bin/make",
        "bash": "/fixture/bin/bash",
        "host_cc": "/fixture/bin/cc",
    }
    for label in sorted(payload.TOOL_LABELS):
        version = root / f"environment/versions/{label}.txt"
        version.parent.mkdir(parents=True, exist_ok=True)
        version.write_text(f"{label} fixture 1\n", encoding="utf-8")
        tools.append(
            {
                "label": label,
                "path": tool_paths[label],
                "executable_sha256": hashlib.sha256(label.encode("ascii")).hexdigest(),
                "log": version.relative_to(root).as_posix(),
                "log_sha256": digest(version),
                "first_line": f"{label} fixture 1",
            }
        )
    tool_by_label = {str(item["label"]): item for item in tools}
    repository = Path(__file__).resolve().parents[1]
    runtime_root = "/fixture/runtime"
    repository_path = "/fixture/repository"
    shim_path = f"{runtime_root}/python-runtime/python"
    dispatcher_path = f"{runtime_root}/python-runtime/trusted-python-child.py"
    aliases = [shim_path, f"{runtime_root}/python-runtime/python3"]
    dispatcher_hash = digest(repository / python_runtime.DISPATCHER_PATH)
    shim_hash = hashlib.sha256(
        python_runtime._shim_bytes(
            PurePosixPath(tool_paths["bash"]), PurePosixPath(tool_paths["python"]),
            PurePosixPath(dispatcher_path), PurePosixPath(shim_path),
            PurePosixPath(repository_path),
        )
    ).hexdigest()
    flags = {
        "isolated": 1, "no_site": 1, "safe_path": 1,
        "ignore_environment": 1, "no_user_site": 1, "dont_write_bytecode": 1,
    }
    python_launch = {
        "schema_version": 1,
        "backing_python": {
            "path": tool_paths["python"],
            "sha256": tool_by_label["python"]["executable_sha256"],
            "probe": {
                "implementation": "CPython", "version": [3, 10, 0],
                "cache_tag": "cpython-310", "abi_flags": "",
                "executable": tool_paths["python"],
                "base_executable": tool_paths["python"],
                "path": ["/fixture/stdlib"], **flags,
            },
        },
        "shell": {
            "path": tool_paths["bash"],
            "sha256": tool_by_label["bash"]["executable_sha256"],
        },
        "dispatcher": {
            "source_path": python_runtime.DISPATCHER_PATH,
            "runtime_path": dispatcher_path,
            "repository_path": repository_path,
            "sha256": dispatcher_hash,
        },
        "shim": {
            "path": shim_path, "aliases": aliases, "sha256": shim_hash,
            "exec_argv_prefix": [
                tool_paths["python"], "-I", "-S", "-B", "-u", dispatcher_path,
            ],
        },
        "probe": {
            "executable": shim_path, "base_executable": shim_path,
            "path": ["/fixture/stdlib", repository_path], **flags,
        },
    }
    python_path_resolution = {"python": aliases[0], "python3": aliases[1]}
    execution_environment = {
        **python_runtime.FORMAL_ENVIRONMENT_FIXED,
        "PATH": ":".join((
            f"{runtime_root}/python-runtime", "/fixture/bin",
            *python_runtime.POSIX_SYSTEM_PATHS,
        )),
        "HOME": f"{runtime_root}/home", "TMPDIR": f"{runtime_root}/tmp",
        "TEMP": f"{runtime_root}/tmp", "TMP": f"{runtime_root}/tmp",
        "FINAL_EVIDENCE_STAGE": "/fixture/stage", "QEMU": tool_paths["qemu"],
        "PYTHON_BIN": shim_path, "CASE_TIMEOUT": "300s",
        "IDLE_NOTICE_SECONDS": "20", "MARKER_GRACE_SECONDS": "2s",
        "MECHANISM_MARKER_GRACE_SECONDS": "5s",
        "HOST_CC": tool_paths["host_cc"], "HOSTCC": tool_paths["host_cc"],
        "CC": tool_paths["host_cc"], "SYSTEMDRIVE": "/",
    }
    temporary_directory_binding = {
        "schema_version": 1,
        "kind": "formal-temporary-directory-binding",
        "execution_platform": "posix",
        "conversion_api": "identity",
        "posix_path": f"{runtime_root}/tmp",
        "native_path": f"{runtime_root}/tmp",
        "roundtrip_path": f"{runtime_root}/tmp",
        "identities": {
            name: {"device": 1, "inode": 1}
            for name in ("posix", "native", "roundtrip")
        },
        "checks": ["posix-native-samefile", "posix-roundtrip-samefile"],
    }
    environment = root / payload.ENVIRONMENT_NAME
    write_json(
        environment,
        {
            "captured_at_utc": "2026-07-31T00:00:00Z",
            "platform": "fixture",
            "machine": "fixture",
            "python_runtime": "fixture",
            "python_launch": python_launch,
            "python_path_resolution": python_path_resolution,
            "execution_environment": execution_environment,
            "temporary_directory_binding": temporary_directory_binding,
            "tools": tools,
        },
    )
    raw_records = [
        {
            "name": item["name"],
            "path": f"{payload.RAW_ROOT}/{item['name']}",
            "bytes": item["bytes"],
            "sha256": item["sha256"],
        }
        for item in artifacts
    ]
    receipt = {
        "schema_version": payload.SCHEMA_VERSION,
        "kind": payload.KIND,
        "source_commit": commit,
        "created_at_utc": "2026-07-31T00:00:00Z",
        "execution": {
            "isolation": "clean-detached-worktree",
            "argv": [
                tool_paths["make"],
                "full-verify",
                "TOOLPREFIX=/fixture/bin/riscv64-linux-gnu-",
            ],
            "returncode": 0,
            "elapsed_seconds": 17.0,
            "timeout_seconds": 18000.0,
            "timed_out": False,
            "python_bin": shim_path,
            "python_path_resolution": python_path_resolution,
            "log": record(root, log),
        },
        "verification_summary": record(root, root / payload.SUMMARY_NAME),
        "raw_artifacts": raw_records,
        "environment": record(root, environment),
    }
    write_json(root / payload.RECEIPT_NAME, receipt)
    payload._write_checksums(root)


def expect_rejected(action, message: str) -> None:
    try:
        action()
    except payload.FullVerificationError as error:
        assert message in str(error), error
    else:
        raise AssertionError(f"accepted invalid full-verification payload: {message}")


def rewrite_receipt(root: Path, mutate) -> None:
    path = root / payload.RECEIPT_NAME
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    write_json(path, value)
    payload._write_checksums(root)


def rewrite_environment_and_receipt(
    root: Path, mutate_environment, mutate_receipt=lambda _receipt: None
) -> None:
    environment_path = root / payload.ENVIRONMENT_NAME
    value = json.loads(environment_path.read_text(encoding="utf-8"))
    mutate_environment(value)
    write_json(environment_path, value)
    receipt_path = root / payload.RECEIPT_NAME
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["environment"] = record(root, environment_path)
    mutate_receipt(receipt)
    write_json(receipt_path, receipt)
    payload._write_checksums(root)


def rewrite_environment(root: Path, mutate) -> None:
    rewrite_environment_and_receipt(root, mutate)


def refresh_shim_hash(environment: dict[str, object]) -> None:
    launch = environment["python_launch"]
    shim = launch["shim"]
    shim["sha256"] = hashlib.sha256(
        python_runtime._shim_bytes(
            PurePosixPath(launch["shell"]["path"]),
            PurePosixPath(launch["backing_python"]["path"]),
            PurePosixPath(launch["dispatcher"]["runtime_path"]),
            PurePosixPath(shim["path"]),
            PurePosixPath(launch["dispatcher"]["repository_path"]),
        )
    ).hexdigest()


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    test_temporary_root = repository / "results"
    test_temporary_root.mkdir(exist_ok=True)
    runner = (repository / "scripts/run-evaluation-suite.sh").read_text(
        encoding="utf-8"
    )
    makefile = (repository / "Makefile").read_text(encoding="utf-8")
    full_mode = runner.split("full-verify)\n", 1)[1].split("\n\t;;", 1)[0]
    package_mode = runner.split("\npackage)\n", 1)[1].split("\n\t;;", 1)[0]
    assert '"${FULL_VERIFICATION_TOOL}" collect' in full_mode
    assert '--expected-commit "${commit}"' in full_mode
    assert "FULL_VERIFICATION_TOOL" not in package_mode
    assert "evaluation-full-verify:" in makefile
    assert "host_tools/evaluation_platform.py formal-exec" in makefile
    assert "--script-relative scripts/run-evaluation-suite.sh --mode $(1)" in makefile

    replay_environment: dict[str, str] = {}
    bounded_runner = payload._run_bounded

    def record_replay_environment(
        _command, _cwd, environment, log_path, *_args, **_kwargs
    ):
        replay_environment.update(environment)
        Path(log_path).write_text("fixture replay failure\n", encoding="ascii")
        return 1, 0.0, False

    payload._run_bounded = record_replay_environment
    try:
        expect_rejected(
            lambda: payload._replay_semantics(
                repository,
                repository / "README.md",
                COMMIT,
                repository,
            ),
            "semantic replay failed",
        )
    finally:
        payload._run_bounded = bounded_runner
    assert Path(replay_environment["TMPDIR"]).is_absolute()
    inherited_temporary = getattr(sys, "_agentos_temporary_root", None)
    if inherited_temporary is not None:
        assert replay_environment["TMPDIR"] == inherited_temporary

    make_tool = shutil.which("make")
    if make_tool is not None and os.name == "posix":
        # A dry run proves the canonical target enters the Python preflight
        # before any repository Bash script.
        with tempfile.TemporaryDirectory(dir=test_temporary_root) as make_temporary:
            fake_bin = Path(make_temporary)
            fake_bash = fake_bin / "bash"
            fake_bash.write_text(
                """#!/bin/sh
printf 'argv=%s\\n' "$*"
printf 'TOOLPREFIX=%s\\n' "$TOOLPREFIX"
printf 'QEMU=%s\\n' "$QEMU"
printf 'PYTHON_BIN=%s\\n' "$PYTHON_BIN"
printf 'HOST_CC=%s\\n' "$HOST_CC"
""",
                encoding="ascii",
            )
            fake_bash.chmod(0o755)
            make_environment = dict(os.environ)
            make_environment["PATH"] = str(fake_bin) + os.pathsep + os.environ["PATH"]
            dry_run = subprocess.run(
                [
                    make_tool,
                    "-n",
                    "evaluation-full-verify",
                    "TOOLPREFIX=bound-riscv64-unknown-elf-",
                    "QEMU=bound-qemu-system-riscv64",
                    "PYTHON_BIN=bound-python3",
                    "HOST_CC=bound-cc",
                ],
                cwd=repository,
                env=make_environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        assert dry_run.returncode == 0, dry_run.stdout + dry_run.stderr
        assert "host_tools/evaluation_platform.py formal-exec" in dry_run.stdout
        assert "--mode full-verify" in dry_run.stdout
        for value in (
            "bound-riscv64-unknown-elf-",
            "bound-qemu-system-riscv64",
            "bound-python3",
        ):
            assert value in dry_run.stdout

    if os.name == "posix":
        with tempfile.TemporaryDirectory(dir=test_temporary_root) as package_temporary:
            package_root = Path(package_temporary)
            run_dir = package_root / "development-package"
            run_dir.mkdir()
            for name in (
                "campaign.json",
                "run-plan.json",
                "measurement-source-receipt.json",
                "summary.json",
                "metrics.jsonl",
            ):
                (run_dir / name).write_text("{}\n", encoding="ascii")
            fake_python = package_root / "fake-python"
            sentinel = package_root / "full-verify-ran"
            calls = package_root / "calls.log"
            fake_python.write_text(
                """#!/usr/bin/bash
set -eu
printf '%s\\n' "$*" >>"${FAKE_CALLS}"
while [[ ${1:-} == -I || ${1:-} == -S ]]; do
  shift
done
if [[ ${1:-} == -c ]]; then
  [[ ${2:-} == *os.name* ]] && printf 'posix\\n' || printf 'linux\\n'
  exit 0
fi
if [[ ${1:-} == *trusted-python-entry.py ]]; then
  shift
fi
case ${1:-} in
  *full_verification_payload.py)
    : >"${FAKE_SENTINEL}"
    exit 97
    ;;
  *evaluation_campaign.py)
    if [[ ${2:-} == export-plan ]]; then
      while (($#)); do
        if [[ $1 == --output ]]; then cp "${FAKE_RUN_PLAN}" "$2"; exit 0; fi
        shift
      done
    fi
    ;;
  *render_evaluation_dashboard.py)
    mkdir -p "$3"
    : >"$3/index.html"
    ;;
  *evaluation_bundle.py)
    while (($#)); do
      if [[ $1 == --output ]]; then mkdir -p "$2"; exit 0; fi
      shift
    done
    ;;
esac
exit 0
""",
                encoding="ascii",
            )
            fake_python.chmod(0o755)
            package_output = package_root / "bundle"
            environment = dict(os.environ)
            environment.update(
                {
                    "PATH": "/usr/bin:" + environment.get("PATH", ""),
                    "PYTHON_BIN": str(fake_python),
                    "EVALUATION_RUN_DIR": str(run_dir),
                    "EVALUATION_BUNDLE_DIR": str(package_output),
                    "FAKE_CALLS": str(calls),
                    "FAKE_SENTINEL": str(sentinel),
                    "FAKE_RUN_PLAN": str(run_dir / "run-plan.json"),
                }
            )
            packaged = subprocess.run(
                ["/usr/bin/bash", "scripts/run-evaluation-suite.sh", "package"],
                cwd=repository,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            assert packaged.returncode == 0, packaged.stdout + packaged.stderr
            assert package_output.is_dir()
            assert not sentinel.exists()
            assert "full_verification_payload.py" not in calls.read_text(
                encoding="utf-8"
            )

        with tempfile.TemporaryDirectory(dir=test_temporary_root) as bounded_temporary:
            bounded_root = Path(bounded_temporary)
            expect_rejected(
                lambda: payload._run_bounded(
                    [sys.executable, "-c", "print('x' * 4096)"],
                    bounded_root,
                    dict(os.environ),
                    bounded_root / "large.log",
                    10,
                    (bounded_root / "outputs",),
                    log_limit=1024,
                    output_limit=4096,
                ),
                "online log budget",
            )
            expect_rejected(
                lambda: payload._run_bounded(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import subprocess,sys; "
                            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])"
                        ),
                    ],
                    bounded_root,
                    dict(os.environ),
                    bounded_root / "descendant.log",
                    10,
                    (bounded_root / "outputs",),
                    log_limit=4096,
                    output_limit=4096,
                ),
                "live descendant",
            )

    with tempfile.TemporaryDirectory(dir=test_temporary_root) as temporary:
        base = Path(temporary)
        valid = base / "valid"
        make_payload(valid)
        expect_rejected(
            lambda: payload.verify_payload(
                valid, expected_commit=COMMIT, contract_root=repository
            ),
            "semantic replay",
        )

        semantic_replay = payload._replay_semantics
        payload._replay_semantics = lambda *_args: None
        binding, paths = payload.verify_payload(
            valid, expected_commit=COMMIT, contract_root=repository
        )
        assert binding["status"] == "verified"
        assert binding["source_commit"] == COMMIT
        assert binding["file_count"] == len(paths)
        assert payload.CHECKSUM_NAME in paths

        legacy_schema = base / "legacy-schema"
        shutil.copytree(valid, legacy_schema)
        rewrite_receipt(
            legacy_schema, lambda value: value.update(schema_version=1)
        )
        expect_rejected(
            lambda: payload.verify_payload(legacy_schema, contract_root=repository),
            "receipt schema differs",
        )

        cygwin_valid = base / "cygwin-valid"
        shutil.copytree(valid, cygwin_valid)

        def use_cygwin_temporary_binding(environment):
            execution = environment["execution_environment"]
            execution.update(
                TEMP="R:/fixture/runtime/tmp",
                TMP="R:/fixture/runtime/tmp",
                SYSTEMDRIVE="C:",
            )
            binding_record = environment["temporary_directory_binding"]
            binding_record.update(
                execution_platform="cygwin",
                conversion_api="msys-2.0.dll:cygwin_conv_path",
                native_path="R:/fixture/runtime/tmp",
            )

        rewrite_environment_and_receipt(
            cygwin_valid, use_cygwin_temporary_binding
        )
        payload.verify_payload(cygwin_valid, contract_root=repository)

        platform_downgrade = base / "platform-downgrade"
        shutil.copytree(cygwin_valid, platform_downgrade)

        def downgrade_temporary_platform(environment):
            execution = environment["execution_environment"]
            execution.update(
                TEMP=execution["TMPDIR"], TMP=execution["TMPDIR"], SYSTEMDRIVE="/"
            )

        rewrite_environment_and_receipt(
            platform_downgrade, downgrade_temporary_platform
        )
        expect_rejected(
            lambda: payload.verify_payload(
                platform_downgrade, contract_root=repository
            ),
            "temporary directory binding differs",
        )

        missing = base / "missing-raw"
        shutil.copytree(valid, missing)
        (missing / payload.RAW_ROOT / "proc-reap.log").unlink()
        expect_rejected(
            lambda: payload.verify_payload(missing, contract_root=repository),
            "checksum inventory",
        )

        changed = base / "changed-raw"
        shutil.copytree(valid, changed)
        target = changed / payload.RAW_ROOT / "proc-reap.log"
        target.write_bytes(b"changed raw bytes\n")
        payload._write_checksums(changed)
        expect_rejected(
            lambda: payload.verify_payload(changed, contract_root=repository),
            "differs from its bytes",
        )

        wrong_commit = base / "wrong-commit"
        shutil.copytree(valid, wrong_commit)
        rewrite_receipt(wrong_commit, lambda value: value.update(source_commit="2" * 40))
        expect_rejected(
            lambda: payload.verify_payload(
                wrong_commit, expected_commit=COMMIT, contract_root=repository
            ),
            "differs from evaluation campaign",
        )

        failed = base / "failed-command"
        shutil.copytree(valid, failed)
        rewrite_receipt(
            failed, lambda value: value["execution"].update(returncode=1)
        )
        expect_rejected(
            lambda: payload.verify_payload(failed, contract_root=repository),
            "did not succeed",
        )

        no_marker = base / "no-marker"
        shutil.copytree(valid, no_marker)
        log = no_marker / payload.LOG_NAME
        log.write_text("full verification stopped\n", encoding="utf-8")
        rewrite_receipt(
            no_marker,
            lambda value: value["execution"].update(log=record(no_marker, log)),
        )
        expect_rejected(
            lambda: payload.verify_payload(no_marker, contract_root=repository),
            "completion marker",
        )

        missing_step = base / "missing-step"
        shutil.copytree(valid, missing_step)
        summary_path = missing_step / payload.SUMMARY_NAME
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["steps"].pop()
        summary["step_contract_sha256"] = payload._collector().step_contract_sha256(
            summary["steps"]
        )
        write_json(summary_path, summary)
        rewrite_receipt(
            missing_step,
            lambda value: value.update(
                verification_summary=record(missing_step, summary_path)
            ),
        )
        payload._replay_semantics = semantic_replay
        expect_rejected(
            lambda: payload.verify_payload(missing_step, contract_root=repository),
            "semantic replay",
        )
        payload._replay_semantics = lambda *_args: None

        receipt_only = base / "receipt-only"
        receipt_only.mkdir()
        shutil.copyfile(valid / payload.RECEIPT_NAME, receipt_only / payload.RECEIPT_NAME)
        payload._write_checksums(receipt_only)
        expect_rejected(
            lambda: payload.verify_payload(receipt_only, contract_root=repository),
            "verification summary",
        )

        # These cases rewrite the embedded record, its receipt and the outer
        # checksum inventory.  Portable verification must still reject an
        # impossible or cross-record-inconsistent execution topology.
        self_consistent_cases = base / "binding-tamper-cases"
        self_consistent_cases.mkdir()

        def binding_case(name, mutate_environment, expected, mutate_receipt=lambda _r: None):
            case = self_consistent_cases / name
            shutil.copytree(valid, case)
            rewrite_environment_and_receipt(
                case, mutate_environment, mutate_receipt
            )
            expect_rejected(
                lambda: payload.verify_payload(case, contract_root=repository), expected
            )

        def move_dispatcher(environment):
            launch = environment["python_launch"]
            replacement = "/fixture/runtime/python-runtime/alternate-child.py"
            launch["dispatcher"]["runtime_path"] = replacement
            launch["shim"]["exec_argv_prefix"][-1] = replacement
            refresh_shim_hash(environment)

        binding_case("dispatcher", move_dispatcher, "shim binding is invalid")

        def move_backing(environment):
            launch = environment["python_launch"]
            replacement = "/fixture/bin/python-forged"
            launch["backing_python"]["path"] = replacement
            launch["backing_python"]["probe"]["executable"] = replacement
            launch["backing_python"]["probe"]["base_executable"] = replacement
            launch["shim"]["exec_argv_prefix"][0] = replacement
            refresh_shim_hash(environment)

        binding_case("backing", move_backing, "tool binding differs")

        def move_shell(environment):
            environment["python_launch"]["shell"]["path"] = "/fixture/bin/bash-forged"
            refresh_shim_hash(environment)

        binding_case("shell", move_shell, "tool binding differs")

        def change_base_executable(environment):
            launch = environment["python_launch"]
            launch["probe"]["base_executable"] = launch["shim"]["aliases"][1]

        binding_case(
            "base-executable", change_base_executable, "runtime probe record is invalid"
        )

        def move_shim(environment):
            launch = environment["python_launch"]
            replacement = "/fixture/runtime/python-runtime/python-renamed"
            aliases = [replacement, "/fixture/runtime/python-runtime/python3"]
            launch["shim"]["path"] = replacement
            launch["shim"]["aliases"] = aliases
            launch["probe"]["executable"] = replacement
            launch["probe"]["base_executable"] = replacement
            environment["python_path_resolution"] = dict(
                zip(("python", "python3"), aliases, strict=True)
            )
            environment["execution_environment"]["PYTHON_BIN"] = replacement
            refresh_shim_hash(environment)

        def move_shim_receipt(receipt):
            launch = json.loads(
                (self_consistent_cases / "shim" / payload.ENVIRONMENT_NAME).read_text(
                    encoding="utf-8"
                )
            )["python_launch"]
            receipt["execution"]["python_bin"] = launch["shim"]["path"]
            receipt["execution"]["python_path_resolution"] = dict(
                zip(("python", "python3"), launch["shim"]["aliases"], strict=True)
            )

        binding_case("shim", move_shim, "shim binding is invalid", move_shim_receipt)

        def move_runtime_directory(environment):
            launch = environment["python_launch"]
            directory = "/fixture/runtime/formal-python"
            shim = f"{directory}/python"
            aliases = [shim, f"{directory}/python3"]
            dispatcher = f"{directory}/trusted-python-child.py"
            launch["shim"].update(path=shim, aliases=aliases)
            launch["dispatcher"]["runtime_path"] = dispatcher
            launch["shim"]["exec_argv_prefix"][-1] = dispatcher
            launch["probe"].update(executable=shim, base_executable=shim)
            environment["python_path_resolution"] = dict(
                zip(("python", "python3"), aliases, strict=True)
            )
            execution_environment = environment["execution_environment"]
            execution_environment["PATH"] = execution_environment["PATH"].replace(
                "/fixture/runtime/python-runtime", directory, 1
            )
            execution_environment["PYTHON_BIN"] = shim
            refresh_shim_hash(environment)

        def move_runtime_receipt(receipt):
            launch = json.loads(
                (self_consistent_cases / "runtime-directory" / payload.ENVIRONMENT_NAME).read_text(
                    encoding="utf-8"
                )
            )["python_launch"]
            receipt["execution"]["python_bin"] = launch["shim"]["path"]
            receipt["execution"]["python_path_resolution"] = dict(
                zip(("python", "python3"), launch["shim"]["aliases"], strict=True)
            )

        binding_case(
            "runtime-directory", move_runtime_directory,
            "shim binding is invalid", move_runtime_receipt,
        )

        binding_case(
            "path",
            lambda environment: environment["execution_environment"].update(
                PATH=environment["execution_environment"]["PATH"] + ":/hostile"
            ),
            "environment binding differs",
        )
        binding_case(
            "native-temporary-redirect",
            lambda environment: environment["execution_environment"].update(
                TEMP="Z:/hostile", TMP="Z:/hostile", SYSTEMDRIVE="C:"
            ),
            "temporary directory binding differs",
        )

        def forge_temporary_receipt(environment):
            execution = environment["execution_environment"]
            execution.update(TEMP="Z:/hostile", TMP="Z:/hostile", SYSTEMDRIVE="C:")
            temporary = environment["temporary_directory_binding"]
            temporary.update(
                execution_platform="cygwin",
                conversion_api="msys-2.0.dll:cygwin_conv_path",
                native_path="Z:/hostile",
                roundtrip_path="/totally/different",
                identities={
                    name: {"device": 999, "inode": 999}
                    for name in ("posix", "native", "roundtrip")
                },
            )

        binding_case(
            "forged-temporary-receipt",
            forge_temporary_receipt,
            "temporary directory binding differs",
        )
        binding_case(
            "dangerous-environment",
            lambda environment: environment["execution_environment"].update(
                BASH_ENV="/tmp/poison"
            ),
            "environment schema differs",
        )
        binding_case(
            "path-resolution-mismatch",
            lambda environment: environment["python_path_resolution"].update(
                python=environment["python_launch"]["shim"]["aliases"][1]
            ),
            "environment record schema differs",
        )
        duplicate_path = python_runtime.controlled_search_path(
            [PurePosixPath("/usr/bin")], ":", python_runtime.POSIX_SYSTEM_PATHS
        )
        assert duplicate_path.split(":").count("/usr/bin") == 2
        payload._replay_semantics = semantic_replay

        if os.name == "posix":
            from evidence_toolchain_attestation import (
                resolve_bash_executable,
                resolve_executable,
            )
            from test_capture_final_evidence import init_fixture_repo

            integration_parent = Path(__file__).resolve().parents[1] / "results"
            integration_parent.mkdir(exist_ok=True)
            integration_root = Path(
                tempfile.mkdtemp(prefix="full-verification-test-", dir=integration_parent)
            )

            def cleanup_integration_root() -> None:
                if integration_root.exists():
                    shutil.rmtree(integration_root)

            atexit.register(cleanup_integration_root)
            repo = integration_root / "collector-repo"
            repo.mkdir()
            tools = init_fixture_repo(repo)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                check=True,
            ).stdout.strip()
            output = integration_root / "collected-stage"
            command = [
                sys.executable,
                str(Path(payload.__file__).resolve()),
                "collect",
                "--repo-root",
                str(repo),
                "--output",
                str(output),
                "--expected-commit",
                commit,
                "--toolprefix",
                str(tools["compiler_prefix"]),
                "--qemu",
                str(tools["qemu"]),
                "--make",
                str(tools["make"]),
                "--host-cc",
                str(tools["host_cc"]),
                "--python",
                BACKING_PYTHON,
                "--bash",
                str(resolve_bash_executable("bash", resolve_executable("git"))),
                "--command-timeout",
                "30",
            ]
            exclude = repo / ".git" / "info" / "exclude"
            exclude.write_text("os/hidden.c\nuser/src/hidden.c\n", encoding="ascii")
            hidden_sources = (repo / "os" / "hidden.c", repo / "user" / "src" / "hidden.c")
            for hidden in hidden_sources:
                hidden.parent.mkdir(parents=True, exist_ok=True)
                hidden.write_text("int hidden;\n", encoding="ascii")
            hidden_status = subprocess.run(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                cwd=repo,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                check=True,
            ).stdout
            assert hidden_status == ""
            rejected_output = integration_root / "hidden-source-stage"
            rejected_command = list(command)
            rejected_command[rejected_command.index(str(output))] = str(rejected_output)
            rejected = subprocess.run(
                rejected_command,
                cwd=repo,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            assert rejected.returncode != 0
            assert "source gate failed" in rejected.stderr
            assert not rejected_output.exists()
            for hidden in hidden_sources:
                hidden.unlink()
            exclude.write_text("", encoding="ascii")
            collected = subprocess.run(
                command,
                cwd=repo,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            failed_log = output.with_name(output.name + ".failed") / "runtime/full-verify.log"
            detail = (
                failed_log.read_text(encoding="utf-8", errors="replace")
                if failed_log.is_file()
                else ""
            )
            assert collected.returncode == 0, collected.stdout + collected.stderr + detail
            binding, _paths = payload.verify_payload(
                output, expected_commit=commit, contract_root=repo
            )
            assert binding["status"] == "verified"

            # Exercise the exact source allowlist and copier used by formal
            # bundles.  The first verification above proves the fixture raw
            # evidence; this one proves that a portable source snapshot is a
            # complete closure for the authenticated launcher and every nested
            # semantic verifier, without falling back to the live checkout.
            import evaluation_bundle
            from agenteval_measurement_source_receipt import (
                build_measurement_source_receipt,
                verify_measurement_source_receipt,
            )

            source_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                text=True,
                encoding="utf-8",
                errors="strict",
                stdout=subprocess.PIPE,
                check=True,
            ).stdout.strip()
            live_source_receipt = build_measurement_source_receipt(
                repository, source_commit=source_commit
            )
            policy_source = integration_root / "policy-source-fixture"
            for source_record in live_source_receipt["sources"]:
                relative = Path(source_record["path"])
                destination = policy_source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repository / relative, destination)
            # The detached collector fixture deliberately uses a compact
            # allocator verifier bound to its synthetic archive.  Keep that
            # one source in the policy-shaped fixture so the integration test
            # exercises closure and launch isolation rather than duplicating
            # the allocator subsystem's own semantic test corpus.
            shutil.copyfile(
                repo / "scripts/fs-allocator-evidence.py",
                policy_source / "scripts/fs-allocator-evidence.py",
            )
            source_receipt = build_measurement_source_receipt(
                policy_source, source_commit=source_commit
            )
            snapshot_stage = integration_root / "policy-snapshot-stage"
            snapshot_stage.mkdir()
            evaluation_bundle._copy_measurement_source_snapshot(
                policy_source, snapshot_stage, source_receipt
            )
            snapshot_root = (
                snapshot_stage / evaluation_bundle.MEASUREMENT_SOURCE_SNAPSHOT_ROOT
            )
            verify_measurement_source_receipt(
                source_receipt, snapshot_root, expected_commit=source_commit
            )
            snapshot_binding, _snapshot_paths = payload.verify_payload(
                output, expected_commit=commit, contract_root=snapshot_root
            )
            assert snapshot_binding == binding

            forged = integration_root / "self-consistent-forgery"
            shutil.copytree(output, forged)
            forged_raw = forged / payload.RAW_ROOT / "proc-reap.log"
            forged_raw.write_text("not a QEMU log\n", encoding="utf-8")
            summary_path = forged / payload.SUMMARY_NAME
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary_record = next(
                item for item in summary["artifacts"] if item["name"] == "proc-reap.log"
            )
            summary_record.update(
                bytes=forged_raw.stat().st_size, sha256=digest(forged_raw)
            )
            write_json(summary_path, summary)
            receipt_path = forged / payload.RECEIPT_NAME
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["verification_summary"] = record(forged, summary_path)
            raw_record = next(
                item
                for item in receipt["raw_artifacts"]
                if item["name"] == "proc-reap.log"
            )
            raw_record.update(bytes=forged_raw.stat().st_size, sha256=digest(forged_raw))
            write_json(receipt_path, receipt)
            payload._write_checksums(forged)
            expect_rejected(
                lambda: payload.verify_payload(
                    forged, expected_commit=commit, contract_root=repo
                ),
                "semantic replay failed",
            )
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                check=True,
            ).stdout
            assert status == ""
            cleanup_integration_root()
            atexit.unregister(cleanup_integration_root)

    print("test_full_verification_payload: passed")


if __name__ == "__main__":
    main()
