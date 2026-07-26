#!/usr/bin/env python3
"""Build and verify a compact, commit-bound AgentOS acceptance evidence bundle."""
from __future__ import annotations
import argparse
import csv
import hashlib
import html
import json
import math
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from datetime import datetime, timezone
from pathlib import Path
SCHEMA_VERSION = 2
FULL_VERIFY_PROFILE_VERSION = 1
REMOTE_CI_SCHEMA_VERSION = 1
REMOTE_CI_JOBS = (("kernel-budgets", "host"), ("reader-e2e", "qemu"),
                  ("agent-regression", "qemu"), ("kernel-mechanism-regression", "qemu"))
REMOTE_CI_TAGS = {"host": "agentos-host-calibrated", "qemu": "agentos-qemu-calibrated"}
REMOTE_RESPONSE_LIMIT = 256 * 1024 * 1024
SUMMARY_NAME = "verification-summary.json"
SUCCESS_MARKER = "[full-verify] all checks passed"
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SUMMARY_FIELDS = {"schema_version", "full_verify_profile_version", "kind", "status", "commit",
                  "completed_at_utc", "settings", "steps", "artifacts",
                  "orchestration_source_sha256"}
MANIFEST_FIELDS = {"schema_version", "status", "commit", "collected_at_utc", "authenticity",
                   "command", "verification_summary", "raw_artifacts", "environment",
                   "configuration", "metrics"}
READER_RUN_ARTIFACTS = (
    re.compile(r"^reader-e2e-run-[a-z0-9._-]+-ucore-build\.log$"),
    re.compile(r"^reader-e2e-run-[a-z0-9._-]+-ucore-run\.log$"),
    re.compile(r"^reader-e2e-run-[a-z0-9._-]+-ucore-run-summary\.json$"),
)
READER_LOG_FILENAMES = ("ucore-build.log", "ucore-run.log", "ucore-run-summary.json")
STEP_CONTRACT = (
    ("target-structure", (), ()),
    ("kernel-budgets", (), ()),
    ("reader-e2e", ("reader-e2e.log", "reader-e2e-log-manifest.json"), READER_RUN_ARTIFACTS),
    ("host-platform-alignment", (), ()),
    ("dual-platforms", ("dual-plain-qemu.log", "dual-agentos-qemu.log",
                        "dual-stage-timings.csv", "dual-state-compare.json",
                        "dual-reader-compare.json"), ()),
    ("agent-suite", ("agent-suite-timings.log", "agent-suite-guest.log"), ()),
    ("proc-reap", ("proc-reap.log",), ()),
    ("syscall-fairness", ("syscall-fairness.log",), ()),
    ("file-resource", ("file-resource.log",), ()),
    ("thread-resource", ("thread-resource.log",), ()),
    ("workflow-teardown-race", ("workflow-teardown-race.log",), ()),
    ("fs-enospc", ("fs-enospc.log",), ()),
)
BUDGET_LINE = re.compile(
    r"^\[kernel-budget\] (?P<name>.+?): actual=(?P<actual>[0-9]+(?:\.[0-9]+)?)"
    r"(?P<unit> lines| bytes| seconds) baseline=(?P<baseline>[0-9]+(?:\.[0-9]+)?)"
    r"(?P=unit) limit=(?P<limit>[0-9]+(?:\.[0-9]+)?)(?P=unit)$"
)
STACK_LINE = re.compile(r"^kernel stack budget: .*required=(?P<required>[0-9]+) limit=(?P<limit>[0-9]+)$")
BOOT_STACK_LINE = re.compile(r"^boot stack budget: .*required=(?P<required>[0-9]+) limit=(?P<limit>[0-9]+)$")
AGENT_TIMING_LINE = re.compile(r"^(?P<case>[A-Za-z0-9_]+)[ \t]+(?P<seconds>[0-9]+\.[0-9]{9})$")
METRIC_NAMES = {
    "stripped kernel ELF": "stripped_kernel_elf_bytes",
    "raw kernel image": "raw_kernel_image_bytes",
    "kernel runtime text": "kernel_runtime_text_bytes",
    "kernel runtime data": "kernel_runtime_data_bytes",
    "kernel runtime bss": "kernel_runtime_bss_bytes",
    "kernel runtime total": "kernel_runtime_total_bytes",
    "struct proc": "struct_proc_bytes",
}
REQUIRED_KERNEL_METRICS = {
    "kernel_source_lines",
    *METRIC_NAMES.values(),
}
REQUIRED_EVIDENCE_METRICS = REQUIRED_KERNEL_METRICS | {
    "kernel_stack_required_bytes", "boot_stack_required_bytes", "agent_suite_total_seconds",
}
CONTROLLED_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
REQUIRED_RAW_FILES = {
    "reader-e2e.log", "reader-e2e-log-manifest.json", "dual-plain-qemu.log",
    "dual-agentos-qemu.log", "dual-stage-timings.csv", "dual-state-compare.json",
    "dual-reader-compare.json", "agent-suite-timings.log", "agent-suite-guest.log",
    "proc-reap.log", "syscall-fairness.log", "file-resource.log", "thread-resource.log",
    "workflow-teardown-race.log", "fs-enospc.log",
}
CORE_FILES = {
    "manifest.json", SUMMARY_NAME, "checksums.sha256", "configuration/kernel-budgets.json",
    "environment/environment.json", "logs/full-verify.log", "metrics/measurements.csv",
    "metrics/agent-case-timings.csv", "metrics/commands.csv", "charts/budget-usage.svg",
} | {f"logs/raw/{name}" for name in REQUIRED_RAW_FILES}
TOOL_LABELS = {"git", "compiler", "qemu", "python", "make", "bash", "host_cc"}
CSV_SCHEMAS = {
    "measurements_csv": ["metric", "actual", "baseline", "limit", "unit", "usage_ratio",
                         "source_line", "source_log", "source_log_sha256"],
    "agent_timings_csv": ["sequence", "case", "seconds", "source_line", "source_log",
                          "source_log_sha256"],
    "command_csv": ["command", "argv_json", "returncode", "elapsed_seconds", "source_log",
                    "source_log_sha256"],
}
class EvidenceError(RuntimeError):
    pass
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
def valid_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.partial.{os.getpid()}")
    if path.exists() or temporary.exists():
        raise EvidenceError(f"refusing to overwrite {path}")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
def resolve_executable(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or candidate.parent != Path("."):
        result = candidate.absolute()
    else:
        found = shutil.which(value)
        if found is None:
            raise EvidenceError(f"required executable not found: {value}")
        result = Path(found).absolute()
    if not result.is_file():
        raise EvidenceError(f"required executable is not a file: {result}")
    return result
def controlled_environment(root: Path, tool_directories: list[Path], extra: dict[str, str] | None = None) -> dict[str, str]:
    home, temporary = root / "home", root / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    temporary.mkdir(parents=True, exist_ok=True)
    system_paths = CONTROLLED_PATH.split(":") if os.name == "posix" else [os.defpath]
    search_paths = list(dict.fromkeys(str(path) for path in tool_directories)) + system_paths
    environment = {
        "PATH": os.pathsep.join(search_paths), "HOME": str(home), "TMPDIR": str(temporary),
        "LC_ALL": "C", "LANG": "C", "TZ": "UTC", "PYTHONNOUSERSITE": "1",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"):
            if name in os.environ:
                environment[name] = os.environ[name]
    if extra:
        environment.update(extra)
    return environment
def git_output(git: Path, repo: Path, environment: dict[str, str], *args: str) -> str:
    result = subprocess.run(
        [str(git), *args], cwd=repo, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False, env=environment,
    )
    if result.returncode:
        raise EvidenceError(f"git {' '.join(args)} failed: {result.stdout.strip()}")
    return result.stdout.strip()
def require_clean_head(git: Path, repo: Path, environment: dict[str, str]) -> str:
    if git_output(git, repo, environment, "status", "--porcelain", "--untracked-files=all"):
        raise EvidenceError("repository worktree is dirty")
    commit = git_output(git, repo, environment, "rev-parse", "--verify", "HEAD^{commit}")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise EvidenceError("HEAD is not a full commit")
    return commit
def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        pgid = process.pid
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait()
            return
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        return
    if process.poll() is None:
        process.kill()
        process.wait()
def run_logged(argv: list[str], cwd: Path, env: dict[str, str], log: Path, timeout: float) -> tuple[int, float, bool]:
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timed_out = False
    with log.open("wb") as output:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=(os.name == "posix"))
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_process_group(process)
            returncode = 124
        except BaseException:
            terminate_process_group(process)
            raise
    return returncode, time.monotonic() - started, timed_out
def parse_steps(path: Path) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise EvidenceError(f"cannot read full-verify steps: {error}") from error
    steps: list[dict[str, object]] = []
    names: set[str] = set()
    artifacts: set[str] = set()
    for line_number, line in enumerate(lines, 1):
        fields = line.split("\t")
        if len(fields) < 3 or not SAFE_NAME.fullmatch(fields[0]):
            raise EvidenceError(f"invalid step row {line_number}")
        name, artifact_names = fields[0], fields[3:]
        try:
            started, ended = float(fields[1]), float(fields[2])
        except ValueError as error:
            raise EvidenceError(f"invalid step timing at row {line_number}") from error
        if (name in names or not math.isfinite(started) or not math.isfinite(ended)
                or started <= 0 or ended < started):
            raise EvidenceError(f"invalid or duplicate step {name}")
        if any(not SAFE_NAME.fullmatch(item) for item in artifact_names):
            raise EvidenceError(f"invalid artifact name in step {name}")
        if artifacts.intersection(artifact_names) or len(artifact_names) != len(set(artifact_names)):
            raise EvidenceError(f"artifact is assigned more than once in step {name}")
        names.add(name)
        artifacts.update(artifact_names)
        steps.append({"name": name, "started_epoch": started, "ended_epoch": ended,
                      "duration_seconds": round(ended - started, 9), "artifacts": artifact_names})
    if not steps:
        raise EvidenceError("full-verify recorded no steps")
    return steps

def validate_step_contract(steps: object) -> list[dict[str, object]]:
    if not isinstance(steps, list):
        raise EvidenceError("verification summary steps are invalid")
    expected_names = [record[0] for record in STEP_CONTRACT]
    if [step.get("name") if isinstance(step, dict) else None for step in steps] != expected_names:
        raise EvidenceError("verification summary step order differs from the full-verify profile")
    previous_end = 0.0
    for step, (name, fixed, patterns) in zip(steps, STEP_CONTRACT):
        artifacts = step.get("artifacts")
        duration = step.get("duration_seconds")
        started, ended = step.get("started_epoch"), step.get("ended_epoch")
        if (not isinstance(artifacts, list) or not all(isinstance(item, str) for item in artifacts)
                or not isinstance(duration, (int, float)) or isinstance(duration, bool)
                or not isinstance(started, (int, float)) or isinstance(started, bool)
                or not isinstance(ended, (int, float)) or isinstance(ended, bool)
                or not all(math.isfinite(float(item)) for item in (duration, started, ended))
                or float(started) <= 0 or float(started) < previous_end
                or float(duration) < 0 or float(ended) < float(started)
                or abs(float(duration) - (float(ended) - float(started))) > 1e-6):
            raise EvidenceError(f"verification summary step timing is invalid: {name}")
        previous_end = float(ended)
        remaining = list(artifacts)
        if any(item not in remaining for item in fixed):
            raise EvidenceError(f"verification summary artifact contract differs: {name}")
        for item in fixed:
            remaining.remove(item)
        counts = [0] * len(patterns)
        for artifact in remaining:
            matches = [index for index, pattern in enumerate(patterns) if pattern.fullmatch(artifact)]
            if len(matches) != 1:
                raise EvidenceError(f"verification summary artifact contract differs: {name}")
            counts[matches[0]] += 1
        if any(count < 1 for count in counts) or (not patterns and remaining):
            raise EvidenceError(f"verification summary artifact contract differs: {name}")
    return steps

def validate_settings(settings: object) -> dict[str, object]:
    expected = {"agent_marker_grace_seconds", "mechanism_marker_grace_seconds",
                "workflow_stability_runs"}
    if not isinstance(settings, dict) or set(settings) != expected:
        raise EvidenceError("verification summary settings contract is invalid")
    runs = settings["workflow_stability_runs"]
    if (settings["agent_marker_grace_seconds"] != "2s"
            or settings["mechanism_marker_grace_seconds"] != "5s"
            or not isinstance(runs, int) or isinstance(runs, bool) or runs < 3):
        raise EvidenceError("verification summary settings contract is invalid")
    return settings

def validate_reader_inventory(directory: Path, steps: list[dict[str, object]]) -> None:
    reader_step = next(step for step in steps if step["name"] == "reader-e2e")
    try:
        manifest = json.loads((directory / "reader-e2e-log-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"Reader E2E artifact manifest is invalid: {error}") from error
    runs = manifest.get("runs") if isinstance(manifest, dict) else None
    if (set(manifest) != {"schema_version", "required_files", "runs"}
            or manifest.get("schema_version") != 1
            or manifest.get("required_files") != list(READER_LOG_FILENAMES)
            or not isinstance(runs, list) or not runs):
        raise EvidenceError("Reader E2E artifact manifest contract is invalid")
    expected = {"reader-e2e.log", "reader-e2e-log-manifest.json"}
    seen_runs: set[str] = set()
    for record in runs:
        run = record.get("run") if isinstance(record, dict) else None
        if (not isinstance(record, dict) or set(record) != {"run", "files", "missing"}
                or not isinstance(run, str) or run in seen_runs
                or not SAFE_NAME.fullmatch(run) or not run.startswith("run-")
                or record.get("files") != list(READER_LOG_FILENAMES) or record.get("missing") != []):
            raise EvidenceError("Reader E2E artifact manifest run is invalid")
        seen_runs.add(run)
        expected.update(f"reader-e2e-{run}-{name}" for name in READER_LOG_FILENAMES)
    if set(reader_step["artifacts"]) != expected:
        raise EvidenceError("Reader E2E manifest and artifact inventory differ")

def validate_summary(value: object) -> dict[str, object]:
    if (not isinstance(value, dict) or set(value) != SUMMARY_FIELDS
            or value.get("schema_version") != SCHEMA_VERSION):
        raise EvidenceError("verification summary schema is invalid")
    if value.get("full_verify_profile_version") != FULL_VERIFY_PROFILE_VERSION:
        raise EvidenceError("verification summary full-verify profile is invalid")
    if value.get("kind") != "agentos-full-verification" or value.get("status") != "passed":
        raise EvidenceError("verification summary did not pass")
    if not re.fullmatch(r"[0-9a-f]{40}", str(value.get("commit", ""))):
        raise EvidenceError("verification summary commit is invalid")
    if (not SHA256.fullmatch(str(value.get("orchestration_source_sha256", "")))
            or not valid_utc_timestamp(value.get("completed_at_utc"))):
        raise EvidenceError("verification summary provenance is invalid")
    steps, artifacts = value.get("steps"), value.get("artifacts")
    if not isinstance(steps, list) or not steps or not isinstance(artifacts, list) or not artifacts:
        raise EvidenceError("verification summary has no steps or artifacts")
    artifact_names: set[str] = set()
    for record in artifacts:
        if (not isinstance(record, dict) or set(record) != {"name", "bytes", "sha256"}
                or not SAFE_NAME.fullmatch(str(record.get("name", "")))):
            raise EvidenceError("verification summary artifact is invalid")
        name = str(record["name"])
        if name in artifact_names or not SHA256.fullmatch(str(record.get("sha256", ""))):
            raise EvidenceError(f"duplicate or unhashed summary artifact: {name}")
        if not isinstance(record.get("bytes"), int) or int(record["bytes"]) <= 0:
            raise EvidenceError(f"empty summary artifact: {name}")
        artifact_names.add(name)
    step_names: set[str] = set()
    assigned: list[str] = []
    for step in steps:
        if (not isinstance(step, dict)
                or set(step) != {"name", "started_epoch", "ended_epoch", "duration_seconds", "artifacts"}
                or not SAFE_NAME.fullmatch(str(step.get("name", "")))):
            raise EvidenceError("verification summary step is invalid")
        if step["name"] in step_names or not isinstance(step.get("artifacts"), list):
            raise EvidenceError("verification summary has duplicate steps")
        if not all(isinstance(item, str) for item in step["artifacts"]):
            raise EvidenceError("verification summary step artifacts are invalid")
        step_names.add(str(step["name"]))
        assigned.extend(step["artifacts"])
    if len(assigned) != len(set(assigned)) or set(assigned) != artifact_names:
        raise EvidenceError("summary steps and artifact inventory differ")
    validate_step_contract(steps)
    validate_settings(value.get("settings"))
    return value
def write_summary(args: argparse.Namespace) -> int:
    stage = Path(args.stage).resolve()
    incoming = stage / "incoming"
    steps_path = Path(args.steps).resolve()
    if not incoming.is_dir() or incoming.is_symlink() or not steps_path.is_file():
        raise EvidenceError("summary input stage is invalid")
    steps = parse_steps(steps_path)
    names = [name for step in steps for name in step["artifacts"]]
    actual = []
    for path in sorted(incoming.iterdir(), key=lambda item: item.name):
        if path.name == SUMMARY_NAME:
            raise EvidenceError("verification summary already exists")
        if path.name.startswith(".") or path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise EvidenceError(f"invalid incoming artifact: {path.name}")
        actual.append(path.name)
    if sorted(names) != actual:
        raise EvidenceError("recorded steps do not match actual incoming artifacts")
    artifacts = [
        {"name": name, "bytes": (incoming / name).stat().st_size,
         "sha256": sha256_file(incoming / name)}
        for name in actual
    ]
    summary = {
        "schema_version": SCHEMA_VERSION,
        "full_verify_profile_version": FULL_VERIFY_PROFILE_VERSION,
        "kind": "agentos-full-verification",
        "status": "passed",
        "commit": args.commit,
        "completed_at_utc": utc_now(),
        "settings": {
            "agent_marker_grace_seconds": args.agent_grace,
            "mechanism_marker_grace_seconds": args.mechanism_grace,
            "workflow_stability_runs": args.workflow_runs,
        },
        "steps": steps,
        "artifacts": artifacts,
        "orchestration_source_sha256": sha256_file(steps_path),
    }
    validate_summary(summary)
    validate_reader_inventory(incoming, steps)
    atomic_json(incoming / SUMMARY_NAME, summary)
    print(f"[evidence] wrote {SUMMARY_NAME}")
    return 0
def metric_key(name: str, unit: str) -> str | None:
    if name.startswith("kernel source (") and unit == "lines":
        return "kernel_source_lines"
    return METRIC_NAMES.get(name)
def load_budget_config(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"invalid kernel budget configuration: {error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("agent_test_suite"), dict):
        raise EvidenceError("kernel budget configuration lacks Agent suite")
    return value
def parse_measurements(full_log: Path, timing_log: Path, config: dict[str, object]
                       ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    text = full_log.read_text(encoding="utf-8", errors="replace")
    metrics: dict[str, dict[str, object]] = {}
    stack_match = boot_match = None
    stack_line = boot_line = 0
    for line_number, line in enumerate(text.splitlines(), 1):
        match = BUDGET_LINE.fullmatch(line.strip())
        if match:
            unit = match.group("unit").strip()
            key = metric_key(match.group("name"), unit)
            if key:
                if key in metrics:
                    raise EvidenceError(f"duplicate kernel metric: {key}")
                metrics[key] = {
                    "metric": key, "actual": float(match.group("actual")),
                    "baseline": float(match.group("baseline")),
                    "limit": float(match.group("limit")), "unit": unit,
                    "source_line": line_number,
                }
        new_stack = STACK_LINE.fullmatch(line.strip())
        new_boot = BOOT_STACK_LINE.fullmatch(line.strip())
        if (new_stack and stack_match) or (new_boot and boot_match):
            raise EvidenceError("duplicate stack budget metric")
        stack_match, boot_match = new_stack or stack_match, new_boot or boot_match
        stack_line = line_number if new_stack else stack_line
        boot_line = line_number if new_boot else boot_line
    missing = sorted(REQUIRED_KERNEL_METRICS - set(metrics))
    if missing or stack_match is None or boot_match is None:
        raise EvidenceError(f"full-verify log lacks required metrics: {missing}")
    kernel_stack = config.get("kernel_stack")
    if not isinstance(kernel_stack, dict):
        raise EvidenceError("kernel stack budget configuration is invalid")
    stack_capacity = int(stack_match.group("limit"))
    boot_capacity = int(boot_match.group("limit"))
    if (stack_capacity != int(kernel_stack["stack_size_bytes"])
            or boot_capacity != int(kernel_stack["boot_stack_size_bytes"])):
        raise EvidenceError("stack capacity log and configuration differ")
    metrics["kernel_stack_required_bytes"] = {
        "metric": "kernel_stack_required_bytes",
        "actual": float(stack_match.group("required")),
        "baseline": float(kernel_stack["baseline_required_bytes"]),
        "limit": float(kernel_stack["max_required_bytes"]),
        "unit": "bytes", "source_line": stack_line,
    }
    metrics["boot_stack_required_bytes"] = {
        "metric": "boot_stack_required_bytes",
        "actual": float(boot_match.group("required")),
        "baseline": float(kernel_stack["baseline_boot_required_bytes"]),
        "limit": float(kernel_stack["max_boot_required_bytes"]),
        "unit": "bytes", "source_line": boot_line,
    }
    cases: list[dict[str, object]] = []
    for line_number, line in enumerate(timing_log.read_text(encoding="utf-8").splitlines(), 1):
        match = AGENT_TIMING_LINE.fullmatch(line.strip())
        if match is None or float(match.group("seconds")) <= 0:
            raise EvidenceError(f"invalid Agent timing row {line_number}")
        cases.append({"sequence": len(cases) + 1, "case": match.group("case"),
                      "seconds": match.group("seconds"), "source_line": line_number})
    if not cases:
        raise EvidenceError("Agent timing log is empty")
    suite = config["agent_test_suite"]
    expected_cases = suite.get("expected_cases")
    actual_cases = [row["case"] for row in cases]
    if (not isinstance(expected_cases, list) or not expected_cases
            or actual_cases != expected_cases or len(actual_cases) != len(set(actual_cases))):
        raise EvidenceError("Agent timing cases do not match the configured suite")
    total = sum(float(row["seconds"]) for row in cases)
    metrics["agent_suite_total_seconds"] = {
        "metric": "agent_suite_total_seconds", "actual": total,
        "baseline": float(suite["baseline_seconds"]),
        "limit": float(suite["max_seconds"]), "unit": "seconds", "source_line": 0,
    }
    rows = list(metrics.values())
    for row in rows:
        limit = float(row["limit"])
        if limit <= 0 or float(row["actual"]) > limit:
            raise EvidenceError(f"metric exceeds limit: {row['metric']}")
        row["usage_ratio"] = float(row["actual"]) / limit
    return rows, cases
def write_metrics_csv(path: Path, rows: list[dict[str, object]], full_source: str,
                      full_hash: str, timing_source: str, timing_hash: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["metric", "actual", "baseline", "limit", "unit", "usage_ratio",
              "source_line", "source_log", "source_log_sha256"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            agent_total = row["metric"] == "agent_suite_total_seconds"
            writer.writerow({**row,
                             "source_log": timing_source if agent_total else full_source,
                             "source_log_sha256": timing_hash if agent_total else full_hash})
def write_agent_csv(path: Path, rows: list[dict[str, object]], source: str,
                    source_hash: str) -> None:
    fields = ["sequence", "case", "seconds", "source_line", "source_log", "source_log_sha256"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "source_log": source, "source_log_sha256": source_hash})
def write_chart(path: Path, rows: list[dict[str, object]], measurements_hash: str,
                full_hash: str, timing_hash: str) -> None:
    width, row_height, top = 980, 34, 54
    height = top + row_height * len(rows) + 24
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="20" y="28" font-family="sans-serif" font-size="18" fill="#111">'
        'AgentOS acceptance budgets</text>',
        f'<metadata>source_measurements_sha256={measurements_hash} full_verify_sha256={full_hash} '
        f'agent_timing_sha256={timing_hash}</metadata>',
    ]
    for index, row in enumerate(rows):
        y = top + index * row_height
        usage = float(row["usage_ratio"])
        baseline = float(row["baseline"]) / float(row["limit"])
        bar_width = min(430, 430 * usage)
        baseline_x = 330 + min(430, 430 * baseline)
        label = html.escape(str(row["metric"]))
        detail = html.escape(f"actual={row['actual']:.9g} baseline={row['baseline']:.9g} "
                             f"limit={row['limit']:.9g} usage={usage:.2%}")
        parts.extend([
            f'<text x="20" y="{y + 16}" font-family="monospace" font-size="12">{label}</text>',
            f'<rect x="330" y="{y + 3}" width="430" height="15" fill="#e5e7eb"/>',
            f'<rect x="330" y="{y + 3}" width="{bar_width:.2f}" height="15" fill="#167d68"/>',
            f'<line x1="{baseline_x:.2f}" y1="{y}" x2="{baseline_x:.2f}" y2="{y + 21}" '
            'stroke="#b45309" stroke-width="2"/>',
            f'<text x="775" y="{y + 16}" font-family="sans-serif" font-size="11">{detail}</text>',
        ])
    parts.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
def capture_version(label: str, executable: Path, directory: Path,
                    environment: dict[str, str]) -> dict[str, object]:
    result = subprocess.run(
        [str(executable), "--version"], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False, env=environment,
    )
    if result.returncode or not result.stdout.strip():
        raise EvidenceError(f"cannot capture {label} version")
    relative = f"environment/versions/{label}.txt"
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.stdout, encoding="utf-8")
    return {"label": label, "path": str(executable), "executable_sha256": sha256_file(executable),
            "first_line": result.stdout.splitlines()[0], "log": relative,
            "log_sha256": sha256_file(path)}
def write_checksums(root: Path) -> None:
    checksum = root / "checksums.sha256"
    files = sorted(path for path in root.rglob("*") if path.is_file() and path != checksum)
    checksum.write_text("".join(
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}\n" for path in files), encoding="ascii")
def safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise EvidenceError(f"unsafe bundle path: {value}")
    return path
def require_file_ref(root: Path, value: object, expected_path: str) -> Path:
    if not isinstance(value, dict) or value.get("path") != expected_path:
        raise EvidenceError(f"manifest file reference is invalid: {expected_path}")
    path = root / safe_relative(expected_path)
    if not path.is_file() or value.get("sha256") != sha256_file(path):
        raise EvidenceError(f"manifest file hash differs: {expected_path}")
    return path
def read_exact_csv(path: Path, fields: list[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if reader.fieldnames != fields or not rows or any(any(row.get(field) in (None, "") for field in fields)
                                                     for row in rows):
        raise EvidenceError(f"CSV schema or data is invalid: {path.name}")
    return rows
def acquire_output_lock(output: Path):
    if os.name != "posix":
        raise EvidenceError("ready evidence collection requires POSIX file locking")
    import fcntl
    lock_dir = Path(tempfile.gettempdir()) / "agentos-final-evidence-locks"
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    lock_path = lock_dir / hashlib.sha256(str(output).encode("utf-8")).hexdigest()
    handle = lock_path.open("a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        handle.close()
        raise EvidenceError(f"evidence collection already running for output: {output}") from error
    return handle
def release_output_lock(handle) -> None:
    if handle is None:
        return
    import fcntl
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()

def positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0

def valid_gitlab_base_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse.urlsplit(value)
    loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    return (parsed.scheme == "https" or (parsed.scheme == "http" and loopback)) and bool(parsed.netloc) \
        and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment and not parsed.username

def validate_remote_file(root: Path, record: object, expected_path: str) -> Path:
    path = require_file_ref(root, record, expected_path)
    if (not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}
            or not positive_int(record.get("bytes")) or path.stat().st_size != record["bytes"]):
        raise EvidenceError(f"remote CI artifact differs: {expected_path}")
    return path

def validate_remote_ci_provenance(value: object, commit: str, root: Path) -> set[str]:
    fields = {"schema_version", "kind", "capture", "project", "pipeline", "api_records", "jobs"}
    if (not isinstance(value, dict) or set(value) != fields
            or value.get("schema_version") != REMOTE_CI_SCHEMA_VERSION
            or value.get("kind") != "agentos-gitlab-api-provenance"):
        raise EvidenceError("remote CI provenance schema is invalid")
    capture, project, pipeline = value.get("capture"), value.get("project"), value.get("pipeline")
    if (not isinstance(capture, dict)
            or set(capture) != {"method", "api_base_url", "fetched_at_utc"}
            or capture.get("method") != "gitlab-api-live-fetch"
            or not valid_gitlab_base_url(capture.get("api_base_url"))
            or not valid_utc_timestamp(capture.get("fetched_at_utc"))):
        raise EvidenceError("remote CI capture provenance is invalid")
    if (not isinstance(project, dict) or set(project) != {"id", "path_with_namespace", "web_url"}
            or not positive_int(project.get("id")) or not project.get("path_with_namespace")
            or not str(project.get("web_url", "")).startswith("https://")):
        raise EvidenceError("remote CI project provenance is invalid")
    if (not isinstance(pipeline, dict)
            or set(pipeline) != {"id", "sha", "ref", "source", "status", "web_url"}
            or not positive_int(pipeline.get("id")) or pipeline.get("sha") != commit
            or pipeline.get("ref") != "main" or pipeline.get("source") != "push"
            or pipeline.get("status") != "success"
            or not str(pipeline.get("web_url", "")).startswith("https://")):
        raise EvidenceError("remote CI pipeline provenance is invalid")
    api_records = value.get("api_records")
    expected_api = {"project": "remote-ci/api/project.json",
                    "pipeline": "remote-ci/api/pipeline.json",
                    "pipeline-jobs": "remote-ci/api/pipeline-jobs.json"}
    expected_api.update({f"job-{name}": f"remote-ci/api/job-{name}.json"
                         for name, _ in REMOTE_CI_JOBS})
    if not isinstance(api_records, list) or len(api_records) != len(expected_api):
        raise EvidenceError("remote CI API record inventory is invalid")
    api_paths: dict[str, Path] = {}
    for record in api_records:
        name = record.get("name") if isinstance(record, dict) else None
        if (not isinstance(record, dict) or set(record) != {"name", "path", "bytes", "sha256"}
                or name not in expected_api or name in api_paths):
            raise EvidenceError("remote CI API record inventory is invalid")
        api_paths[name] = validate_remote_file(root, {key: record[key] for key in ("path", "bytes", "sha256")},
                                               expected_api[name])
    jobs = value.get("jobs")
    if (not isinstance(jobs, list) or [job.get("name") if isinstance(job, dict) else None for job in jobs]
            != [name for name, _ in REMOTE_CI_JOBS]):
        raise EvidenceError("remote CI job provenance is incomplete")
    job_ids: set[int] = set()
    remote_paths = {"remote-ci/provenance.json", *expected_api.values()}
    for job, (name, runner_class) in zip(jobs, REMOTE_CI_JOBS):
        job_fields = {"name", "job_id", "runner_id", "runner_class", "runner_tag", "status",
                      "trace", "artifact"}
        if (not isinstance(job, dict) or set(job) != job_fields or job.get("name") != name
                or not positive_int(job.get("job_id")) or job["job_id"] in job_ids
                or not positive_int(job.get("runner_id")) or job.get("runner_class") != runner_class
                or job.get("runner_tag") != REMOTE_CI_TAGS[runner_class]
                or job.get("status") != "success"):
            raise EvidenceError("remote CI job provenance is invalid")
        job_ids.add(job["job_id"])
        for kind, suffix in (("trace", "trace.log"), ("artifact", "artifacts.zip")):
            relative = f"remote-ci/jobs/{name}.{suffix}"
            validate_remote_file(root, job[kind], relative)
            remote_paths.add(relative)
    try:
        raw_project = json.loads(api_paths["project"].read_text(encoding="utf-8"))
        raw_pipeline = json.loads(api_paths["pipeline"].read_text(encoding="utf-8"))
        raw_jobs = json.loads(api_paths["pipeline-jobs"].read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceError("remote CI API JSON is invalid") from error
    if (not isinstance(raw_project, dict) or any(raw_project.get(key) != project[key]
            for key in ("id", "path_with_namespace", "web_url"))
            or not isinstance(raw_pipeline, dict) or any(raw_pipeline.get(key) != pipeline[key]
            for key in ("id", "sha", "ref", "source", "status", "web_url"))
            or not isinstance(raw_jobs, list)):
        raise EvidenceError("remote CI API records differ from provenance")
    listed = {item.get("name"): item for item in raw_jobs if isinstance(item, dict)}
    for job in jobs:
        try:
            raw_job = json.loads(api_paths[f"job-{job['name']}"] .read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EvidenceError("remote CI job API JSON is invalid") from error
        listed_job = listed.get(job["name"])
        runner = raw_job.get("runner") if isinstance(raw_job, dict) else None
        raw_pipeline_ref = raw_job.get("pipeline") if isinstance(raw_job, dict) else None
        if (not isinstance(listed_job, dict) or listed_job.get("id") != job["job_id"]
                or listed_job.get("status") != "success" or not isinstance(raw_job, dict)
                or raw_job.get("id") != job["job_id"] or raw_job.get("name") != job["name"]
                or raw_job.get("status") != "success" or not isinstance(runner, dict)
                or runner.get("id") != job["runner_id"] or job["runner_tag"] not in raw_job.get("tag_list", [])
                or not isinstance(raw_pipeline_ref, dict)
                or raw_pipeline_ref.get("id") != pipeline["id"]
                or raw_pipeline_ref.get("sha") != commit):
            raise EvidenceError("remote CI job API record differs from provenance")
    return remote_paths

def validate_authenticity(root: Path, manifest: dict[str, object], commit: str) -> set[str]:
    authenticity = manifest.get("authenticity")
    if not isinstance(authenticity, dict) or set(authenticity) != {"local", "remote_ci"}:
        raise EvidenceError("manifest authenticity boundary is invalid")
    local = authenticity.get("local")
    if local != {"kind": "committed-git-head", "commit": commit,
                  "execution": "clean-detached-worktree"}:
        raise EvidenceError("manifest local authenticity boundary is invalid")
    remote = authenticity.get("remote_ci")
    if remote == {"status": "not-attached"}:
        return set()
    fields = {"status", "pipeline_sha", "pipeline_ref", "pipeline_source",
              "source_local_checksums_sha256", "provenance"}
    if (not isinstance(remote, dict) or set(remote) != fields
            or remote.get("status") != "provenance-attached"
            or remote.get("pipeline_sha") != commit
            or remote.get("pipeline_ref") != "main" or remote.get("pipeline_source") != "push"
            or not SHA256.fullmatch(str(remote.get("source_local_checksums_sha256", "")))):
        raise EvidenceError("manifest remote CI binding is invalid")
    proof_path = require_file_ref(root, remote.get("provenance"), "remote-ci/provenance.json")
    try:
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise EvidenceError("remote CI provenance JSON is invalid") from error
    return validate_remote_ci_provenance(proof, commit, root)

def verify_bundle(root: Path) -> dict[str, object]:
    root = root.resolve()
    checksum = root / "checksums.sha256"
    if not checksum.is_file() or checksum.is_symlink():
        raise EvidenceError("bundle has no checksum inventory")
    expected: dict[str, str] = {}
    for line_number, line in enumerate(checksum.read_text(encoding="ascii").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None or match.group(2) in expected:
            raise EvidenceError(f"invalid checksum row {line_number}")
        safe_relative(match.group(2))
        expected[match.group(2)] = match.group(1)
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink() or ".partial." in path.name:
            raise EvidenceError(f"unsafe bundle entry: {path.relative_to(root)}")
        if path.is_file() and path != checksum:
            actual.add(path.relative_to(root).as_posix())
    if actual != set(expected):
        raise EvidenceError("checksum inventory and bundle files differ")
    if not CORE_FILES.issubset(actual | {"checksums.sha256"}):
        raise EvidenceError("bundle is missing fixed core files")
    for relative, digest in expected.items():
        if sha256_file(root / safe_relative(relative)) != digest:
            raise EvidenceError(f"checksum mismatch: {relative}")
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        summary = validate_summary(
            json.loads((root / SUMMARY_NAME).read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"bundle JSON is invalid: {error}") from error
    if (not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS
            or manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("status") != "ready"):
        raise EvidenceError("manifest is not ready")
    if manifest.get("commit") != summary["commit"]:
        raise EvidenceError("manifest and summary commits differ")
    remote_files = validate_authenticity(root, manifest, str(summary["commit"]))
    summary_ref = manifest.get("verification_summary")
    if not isinstance(summary_ref, dict) or summary_ref.get("path") != SUMMARY_NAME:
        raise EvidenceError("manifest summary reference is invalid")
    if summary_ref.get("sha256") != sha256_file(root / SUMMARY_NAME):
        raise EvidenceError("manifest summary hash differs")
    raw_records = manifest.get("raw_artifacts")
    if not isinstance(raw_records, list) or len(raw_records) != len(summary["artifacts"]):
        raise EvidenceError("manifest raw artifact list is invalid")
    summary_by_name = {item["name"]: item for item in summary["artifacts"]}
    raw_names = [item.get("name") for item in raw_records if isinstance(item, dict)]
    if len(raw_names) != len(set(raw_names)) or set(raw_names) != set(summary_by_name):
        raise EvidenceError("manifest and summary raw name sets differ")
    for record in raw_records:
        if not isinstance(record, dict) or record.get("name") not in summary_by_name:
            raise EvidenceError("manifest raw artifact is unknown")
        source = root / safe_relative(str(record.get("path", "")))
        summary_record = summary_by_name[record["name"]]
        if (record.get("path") != f"logs/raw/{record['name']}"
                or not source.is_file() or source.stat().st_size != summary_record["bytes"]
                or sha256_file(source) != summary_record["sha256"]
                or record.get("sha256") != summary_record["sha256"]
                or record.get("bytes") != summary_record["bytes"]):
            raise EvidenceError(f"raw artifact differs: {record.get('name')}")
    validate_reader_inventory(root / "logs/raw", summary["steps"])
    command = manifest.get("command")
    if (not isinstance(command, dict) or command.get("returncode") != 0
            or command.get("timed_out") is not False
            or command.get("log") != "logs/full-verify.log"
            or command.get("log_sha256") != sha256_file(root / "logs/full-verify.log")):
        raise EvidenceError("full-verify command did not succeed")
    metric_paths = {"measurements_csv": "metrics/measurements.csv",
                    "agent_timings_csv": "metrics/agent-case-timings.csv", "command_csv": "metrics/commands.csv"}
    csv_rows = {key: read_exact_csv(root / path, CSV_SCHEMAS[key]) for key, path in metric_paths.items()}
    measurement_rows = csv_rows["measurements_csv"]
    metric_names = [row.get("metric") for row in measurement_rows]
    if any(metric_names.count(name) != 1 for name in REQUIRED_EVIDENCE_METRICS):
        raise EvidenceError("critical metrics are missing or duplicated")
    environment_path = require_file_ref(root, manifest.get("environment"),
                                        "environment/environment.json")
    configuration_path = require_file_ref(root, manifest.get("configuration"),
                                          "configuration/kernel-budgets.json")
    try:
        environment_record = json.loads(environment_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise EvidenceError("environment inventory is invalid") from error
    tool_records = environment_record.get("tools") if isinstance(environment_record, dict) else None
    if not isinstance(tool_records, list) or any(not isinstance(item, dict) for item in tool_records):
        raise EvidenceError("environment tool inventory is invalid")
    labels = [item.get("label") for item in tool_records]
    if len(labels) != len(set(labels)) or set(labels) != TOOL_LABELS:
        raise EvidenceError("environment tool labels are missing or duplicated")
    version_logs = set()
    for record in tool_records:
        label, relative = record["label"], record.get("log")
        expected_log = f"environment/versions/{label}.txt"
        version_path = root / safe_relative(str(relative))
        if (relative != expected_log or not version_path.is_file()
                or record.get("log_sha256") != sha256_file(version_path)
                or not SHA256.fullmatch(str(record.get("executable_sha256", "")))
                or not version_path.read_text(encoding="utf-8").splitlines()
                or record.get("first_line") != version_path.read_text(encoding="utf-8").splitlines()[0]):
            raise EvidenceError(f"environment version record is invalid: {label}")
        version_logs.add(relative)
    actual_version_logs = {path.relative_to(root).as_posix() for path in
                           (root / "environment/versions").rglob("*") if path.is_file()}
    if actual_version_logs != version_logs:
        raise EvidenceError("environment version file inventory differs")
    allowed_files = ((CORE_FILES - {"checksums.sha256"}) | version_logs
                     | {record["path"] for record in raw_records} | remote_files)
    if actual != allowed_files:
        raise EvidenceError("bundle contains unreferenced files")
    metrics_ref = manifest.get("metrics")
    if not isinstance(metrics_ref, dict):
        raise EvidenceError("manifest metrics references are invalid")
    for key, path in metric_paths.items():
        require_file_ref(root, metrics_ref.get(key), path)
    chart_path = require_file_ref(root, metrics_ref.get("chart"), "charts/budget-usage.svg")
    measurement_hash = metrics_ref["measurements_csv"]["sha256"]
    if (metrics_ref.get("chart_sha256") != sha256_file(chart_path)
            or metrics_ref.get("source_measurements_sha256") != measurement_hash):
        raise EvidenceError("chart manifest provenance differs")
    timing_hash = summary_by_name.get("agent-suite-timings.log", {}).get("sha256")
    if not timing_hash:
        raise EvidenceError("summary lacks Agent timing provenance")
    for key, rows in csv_rows.items():
        for row_number, row in enumerate(rows, 2):
            timing_row = key == "agent_timings_csv" or (key == "measurements_csv"
                                                         and row.get("metric") == "agent_suite_total_seconds")
            expected_source = ("logs/raw/agent-suite-timings.log", timing_hash) if timing_row else \
                ("logs/full-verify.log", command["log_sha256"])
            if (row["source_log"], row["source_log_sha256"]) != expected_source:
                raise EvidenceError(f"CSV source mismatch: {metric_paths[key]}:{row_number}")
    command_rows = csv_rows["command_csv"]
    if len(command_rows) != 1:
        raise EvidenceError("command CSV must contain exactly one command")
    command_row = command_rows[0]
    try:
        command_argv = json.loads(command_row["argv_json"])
    except json.JSONDecodeError as error:
        raise EvidenceError("command CSV argv is invalid") from error
    if (command_row["command"] != "make full-verify" or command_argv != command.get("argv")
            or command_row["returncode"] != str(command["returncode"])
            or abs(float(command_row["elapsed_seconds"]) - float(command["elapsed_seconds"])) > 1e-9):
        raise EvidenceError("command CSV differs from manifest command")
    for row in measurement_rows:
        try:
            actual_value, limit = float(row["actual"]), float(row["limit"])
            usage = float(row["usage_ratio"])
        except (KeyError, ValueError) as error:
            raise EvidenceError("measurement values are invalid") from error
        if limit <= 0 or abs(usage - actual_value / limit) > 1e-9:
            raise EvidenceError("measurement usage is inconsistent")
    agent_row = next(row for row in measurement_rows
                     if row["metric"] == "agent_suite_total_seconds")
    agent_total = metrics_ref.get("agent_total_seconds")
    if not isinstance(agent_total, dict) or agent_total.get("actual") != float(agent_row["actual"]):
        raise EvidenceError("manifest Agent total differs from metrics CSV")
    config = load_budget_config(configuration_path)
    agent_rows = csv_rows["agent_timings_csv"]
    actual_cases = [row["case"] for row in agent_rows]
    if (actual_cases != config["agent_test_suite"].get("expected_cases")
            or len(actual_cases) != len(set(actual_cases))
            or [row["sequence"] for row in agent_rows] != [str(index) for index in range(1, len(agent_rows) + 1)]
            or abs(sum(float(row["seconds"]) for row in agent_rows) - float(agent_row["actual"])) > 1e-9):
        raise EvidenceError("Agent timing CSV differs from configured suite")
    full_hash = command["log_sha256"]
    chart = chart_path.read_text(encoding="utf-8")
    expected_metadata = (f"source_measurements_sha256={measurement_hash} full_verify_sha256={full_hash} "
                         f"agent_timing_sha256={timing_hash}")
    if re.findall(r"<metadata>([^<>]*)</metadata>", chart) != [expected_metadata]:
        raise EvidenceError("chart provenance hashes are incomplete")
    return {"schema_version": SCHEMA_VERSION, "status": "ready", "commit": manifest["commit"],
            "remote_ci": manifest["authenticity"]["remote_ci"]["status"],
            "files_verified": len(expected)}
def collect(args: argparse.Namespace) -> int:
    repo, output = Path(args.repo_root).resolve(), Path(args.output).resolve()
    if not (repo / ".git").exists() or output.exists():
        raise EvidenceError("repository or output path is invalid")
    git = resolve_executable(args.git)
    compiler_command = args.toolprefix + "gcc"
    compiler = resolve_executable(compiler_command)
    compiler_path = str(compiler)
    if not compiler_path.endswith("gcc"):
        raise EvidenceError("TOOLPREFIX compiler must end in gcc")
    effective_prefix = compiler_path[:-3]
    tools = {
        "git": git,
        "compiler": compiler,
        "qemu": resolve_executable(args.qemu),
        "python": resolve_executable(args.python),
        "make": resolve_executable(args.make),
        "bash": resolve_executable(args.bash),
        "host_cc": resolve_executable(args.host_cc),
    }
    environment_root = Path(tempfile.mkdtemp(prefix="agentos-evidence-env-"))
    tool_directories = [path.parent for path in tools.values()]
    base_environment = controlled_environment(environment_root, tool_directories)
    try:
        commit = require_clean_head(git, repo, base_environment)
    except BaseException:
        shutil.rmtree(environment_root, ignore_errors=True)
        raise
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        output_lock = acquire_output_lock(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(environment_root, ignore_errors=True)
        raise
    worktree: Path | None = None
    failed = output.with_name(output.name + ".failed")
    worktree_registered = False
    try:
        worktree = Path(tempfile.mkdtemp(prefix="agentos-evidence-worktree-"))
        if failed.exists():
            raise EvidenceError(f"failed evidence path already exists: {failed}")
        worktree.rmdir()
        git_output(git, repo, base_environment, "worktree", "add", "--detach",
                   str(worktree), commit)
        worktree_registered = True
        detached = subprocess.run(
            [str(git), "symbolic-ref", "-q", "HEAD"], cwd=worktree,
            env=base_environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        if (detached.returncode != 1
                or git_output(git, worktree, base_environment, "rev-parse", "HEAD") != commit
                or git_output(git, worktree, base_environment, "status", "--porcelain",
                              "--untracked-files=all")):
            raise EvidenceError("execution worktree is not a clean detached commit")
        evidence_stage = stage / "runtime" / "evidence-stage"
        (evidence_stage / "incoming").mkdir(parents=True)
        full_log = stage / "logs" / "full-verify.log"
        command = [str(tools["make"]), "full-verify", f"TOOLPREFIX={effective_prefix}"]
        selected_env = {
            "FINAL_EVIDENCE_STAGE": str(evidence_stage),
            "QEMU": str(tools["qemu"]),
            "PYTHON_BIN": str(tools["python"]),
            "CASE_TIMEOUT": args.case_timeout,
            "IDLE_NOTICE_SECONDS": args.idle_notice,
            "MARKER_GRACE_SECONDS": "2s",
            "MECHANISM_MARKER_GRACE_SECONDS": "5s",
        }
        environment = controlled_environment(environment_root, tool_directories,
                                             selected_env)
        returncode, elapsed, timed_out = run_logged(
            command, worktree, environment, full_log, args.command_timeout
        )
        if returncode != 0:
            raise EvidenceError(f"make full-verify failed with rc={returncode}")
        summary_source = evidence_stage / "incoming" / SUMMARY_NAME
        try:
            summary = validate_summary(json.loads(summary_source.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            raise EvidenceError(f"full-verify did not publish a valid summary: {error}") from error
        if summary["commit"] != commit:
            raise EvidenceError("full-verify summary is not bound to detached HEAD")
        validate_reader_inventory(evidence_stage / "incoming", summary["steps"])
        if full_log.read_text(encoding="utf-8", errors="replace").splitlines().count(SUCCESS_MARKER) != 1:
            raise EvidenceError("full-verify completion marker is missing or duplicated")
        shutil.copyfile(summary_source, stage / SUMMARY_NAME)
        raw_dir = stage / "logs" / "raw"
        raw_dir.mkdir(parents=True)
        raw_records = []
        for record in summary["artifacts"]:
            source = evidence_stage / "incoming" / record["name"]
            destination = raw_dir / record["name"]
            if (not source.is_file() or source.is_symlink()
                    or source.stat().st_size != record["bytes"]
                    or sha256_file(source) != record["sha256"]):
                raise EvidenceError(f"summary artifact differs: {record['name']}")
            shutil.copyfile(source, destination)
            raw_records.append({"name": record["name"],
                                "path": destination.relative_to(stage).as_posix(),
                                "bytes": record["bytes"], "sha256": record["sha256"]})
        config_source = worktree / "ci" / "kernel-budgets.json"
        config = load_budget_config(config_source)
        config_path = stage / "configuration" / "kernel-budgets.json"
        config_path.parent.mkdir(parents=True)
        shutil.copyfile(config_source, config_path)
        timing = next((item for item in raw_records if item["name"] == "agent-suite-timings.log"), None)
        if timing is None:
            raise EvidenceError("summary lacks Agent timing artifact")
        metrics, agent_cases = parse_measurements(full_log, stage / timing["path"], config)
        full_relative = "logs/full-verify.log"
        full_hash = sha256_file(full_log)
        timing_hash = timing["sha256"]
        measurements_path = stage / "metrics" / "measurements.csv"
        write_metrics_csv(measurements_path, metrics, full_relative, full_hash,
                          str(timing["path"]), str(timing_hash))
        write_agent_csv(stage / "metrics" / "agent-case-timings.csv", agent_cases,
                        str(timing["path"]), str(timing_hash))
        measurements_hash = sha256_file(measurements_path)
        chart_path = stage / "charts" / "budget-usage.svg"
        write_chart(chart_path, metrics, measurements_hash, full_hash, str(timing_hash))
        versions = [capture_version(label, path, stage, base_environment)
                    for label, path in tools.items()]
        environment_record = {
            "captured_at_utc": utc_now(), "platform": platform.platform(),
            "machine": platform.machine(), "python_runtime": sys.version,
            "tools": versions,
        }
        environment_path = stage / "environment" / "environment.json"
        write_json(environment_path, environment_record)
        command_record = {
            "argv": command, "environment": environment, "returncode": returncode,
            "elapsed_seconds": round(elapsed, 9), "timed_out": timed_out,
            "log": full_relative, "log_sha256": full_hash,
        }
        with (stage / "metrics" / "commands.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "command", "argv_json", "returncode", "elapsed_seconds", "source_log",
                "source_log_sha256",
            ])
            writer.writeheader()
            writer.writerow({"command": "make full-verify", "argv_json": json.dumps(command),
                             "returncode": returncode, "elapsed_seconds": round(elapsed, 9),
                             "source_log": full_relative, "source_log_sha256": full_hash})
        agent_total = next(row for row in metrics if row["metric"] == "agent_suite_total_seconds")
        manifest = {
            "schema_version": SCHEMA_VERSION, "status": "ready", "commit": commit,
            "collected_at_utc": utc_now(),
            "authenticity": {
                "local": {"kind": "committed-git-head", "commit": commit,
                          "execution": "clean-detached-worktree"},
                "remote_ci": {"status": "not-attached"},
            },
            "command": command_record,
            "verification_summary": {"path": SUMMARY_NAME,
                                     "sha256": sha256_file(stage / SUMMARY_NAME)},
            "raw_artifacts": raw_records,
            "environment": {"path": "environment/environment.json", "sha256": sha256_file(environment_path)},
            "configuration": {"path": "configuration/kernel-budgets.json",
                              "sha256": sha256_file(config_path)},
            "metrics": {"measurements_csv": {"path": "metrics/measurements.csv", "sha256": measurements_hash},
                        "agent_timings_csv": {"path": "metrics/agent-case-timings.csv", "sha256": sha256_file(stage / "metrics/agent-case-timings.csv")},
                        "command_csv": {"path": "metrics/commands.csv", "sha256": sha256_file(stage / "metrics/commands.csv")},
                        "chart": {"path": "charts/budget-usage.svg", "sha256": sha256_file(chart_path)},
                        "chart_sha256": sha256_file(chart_path),
                        "source_measurements_sha256": measurements_hash,
                        "agent_total_seconds": agent_total},
        }
        write_json(stage / "manifest.json", manifest)
        shutil.rmtree(stage / "runtime")
        git_output(git, repo, base_environment, "worktree", "remove", "--force",
                   str(worktree))
        worktree_registered = False
        write_checksums(stage)
        verify_bundle(stage)
        if output.exists():
            raise EvidenceError(f"output appeared while collection was running: {output}")
        os.replace(stage, output)
        print(f"[evidence] bundle captured: {output}")
        return 0
    except BaseException as error:
        if worktree_registered:
            subprocess.run([str(git), "worktree", "remove", "--force", str(worktree)],
                           cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=base_environment)
        if stage.exists():
            write_json(stage / "failure.json", {"schema_version": SCHEMA_VERSION,
                       "status": "failed", "failed_at_utc": utc_now(), "error": str(error)})
            if not failed.exists():
                os.replace(stage, failed)
            else:
                shutil.rmtree(stage, ignore_errors=True)
        if isinstance(error, EvidenceError):
            raise
        if not isinstance(error, Exception):
            raise
        raise EvidenceError(f"unexpected collection failure: {error}") from error
    finally:
        if worktree is not None and worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)
        release_output_lock(output_lock)
        shutil.rmtree(environment_root, ignore_errors=True)

class GitLabRedirectHandler(urlrequest.HTTPRedirectHandler):
    def __init__(self, allow_cross_origin: bool):
        super().__init__()
        self.allow_cross_origin = allow_cross_origin

    @staticmethod
    def origin(url: str) -> tuple[str, str | None, int | None]:
        parsed = urlparse.urlsplit(url)
        port = parsed.port or ({"http": 80, "https": 443}.get(parsed.scheme))
        return parsed.scheme, parsed.hostname, port

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None or self.origin(req.full_url) == self.origin(newurl):
            return redirected
        old_host, new_target = urlparse.urlsplit(req.full_url).hostname, urlparse.urlsplit(newurl)
        loopback = old_host in {"127.0.0.1", "::1", "localhost"} \
            and new_target.hostname in {"127.0.0.1", "::1", "localhost"}
        if not self.allow_cross_origin or (new_target.scheme != "https" and not loopback):
            raise urlerror.HTTPError(req.full_url, code, "cross-origin redirect rejected", headers, fp)
        for name, _ in list(redirected.header_items()):
            if name.lower() in {"private-token", "authorization"}:
                redirected.remove_header(name)
        return redirected

def gitlab_fetch(base_url: str, endpoint: str, token: str, timeout: float,
                 allow_cross_origin: bool = False) -> tuple[bytes, object]:
    request = urlrequest.Request(f"{base_url.rstrip('/')}/api/v4/{endpoint}",
                                 headers={"PRIVATE-TOKEN": token,
                                          "User-Agent": "agentos-evidence-collector/2"})
    try:
        opener = urlrequest.build_opener(GitLabRedirectHandler(allow_cross_origin))
        with opener.open(request, timeout=timeout) as response:
            data = response.read(REMOTE_RESPONSE_LIMIT + 1)
            headers = response.headers
    except (urlerror.HTTPError, urlerror.URLError, TimeoutError) as error:
        raise EvidenceError(f"GitLab API request failed: {endpoint}") from error
    if not data or len(data) > REMOTE_RESPONSE_LIMIT:
        raise EvidenceError(f"GitLab API response is empty or too large: {endpoint}")
    return data, headers

def decode_gitlab_json(data: bytes, label: str) -> object:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceError(f"GitLab API JSON is invalid: {label}") from error

def bind_remote_ci(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle).resolve()
    output = Path(args.output).resolve()
    token_input = Path(args.token_file)
    token_path = token_input.resolve()
    if (not bundle.is_dir() or output.exists() or output == bundle
            or token_input.is_symlink() or not token_path.is_file()
            or not positive_int(args.project_id) or not positive_int(args.pipeline_id)
            or not math.isfinite(args.api_timeout) or args.api_timeout <= 0 or args.api_timeout > 300
            or not valid_gitlab_base_url(args.gitlab_url)):
        raise EvidenceError("remote CI binding input or output is invalid")
    try:
        output.relative_to(bundle)
    except ValueError:
        pass
    else:
        raise EvidenceError("remote CI output cannot be inside the source bundle")
    local_result = verify_bundle(bundle)
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as error:
        raise EvidenceError("GitLab token file is invalid") from error
    if not 8 <= len(token) <= 512 or any(character.isspace() for character in token):
        raise EvidenceError("GitLab token file is invalid")
    source_manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if source_manifest["authenticity"]["remote_ci"] != {"status": "not-attached"}:
        raise EvidenceError("source bundle already has remote CI provenance")
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = None
    stage = None
    try:
        lock = acquire_output_lock(output)
        stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
        shutil.copytree(bundle, stage, dirs_exist_ok=True)
        (stage / "checksums.sha256").unlink()
        api_dir = stage / "remote-ci" / "api"
        jobs_dir = stage / "remote-ci" / "jobs"
        api_dir.mkdir(parents=True)
        jobs_dir.mkdir()
        api_records: list[dict[str, object]] = []
        api_base = args.gitlab_url.rstrip("/")
        prefix = f"projects/{args.project_id}"
        def fetch_json(name: str, endpoint: str) -> object:
            data, headers = gitlab_fetch(api_base, endpoint, token, args.api_timeout)
            relative = f"remote-ci/api/{name}.json"
            path = stage / relative
            path.write_bytes(data)
            api_records.append({"name": name, "path": relative, "bytes": len(data),
                                "sha256": sha256_file(path)})
            return decode_gitlab_json(data, name), headers
        project_pair = fetch_json("project", prefix)
        project = project_pair[0]
        pipeline_pair = fetch_json("pipeline", f"{prefix}/pipelines/{args.pipeline_id}")
        pipeline = pipeline_pair[0]
        jobs_pair = fetch_json("pipeline-jobs",
                               f"{prefix}/pipelines/{args.pipeline_id}/jobs?per_page=100")
        listed_jobs, jobs_headers = jobs_pair
        commit = str(local_result["commit"])
        if (not isinstance(project, dict) or project.get("id") != args.project_id
                or not project.get("path_with_namespace")
                or not str(project.get("web_url", "")).startswith("https://")):
            raise EvidenceError("GitLab project response differs from requested project")
        if (not isinstance(pipeline, dict) or pipeline.get("id") != args.pipeline_id
                or pipeline.get("sha") != commit or pipeline.get("ref") != "main"
                or pipeline.get("source") != "push" or pipeline.get("status") != "success"
                or not str(pipeline.get("web_url", "")).startswith("https://")):
            raise EvidenceError("GitLab pipeline did not pass the final main push")
        if not isinstance(listed_jobs, list) or str(jobs_headers.get("X-Next-Page", "")):
            raise EvidenceError("GitLab pipeline job inventory is invalid or incomplete")
        job_records: list[dict[str, object]] = []
        for name, runner_class in REMOTE_CI_JOBS:
            candidates = [job for job in listed_jobs
                          if isinstance(job, dict) and job.get("name") == name]
            if len(candidates) != 1 or not positive_int(candidates[0].get("id")):
                raise EvidenceError(f"GitLab pipeline lacks unique required job: {name}")
            job_id = candidates[0]["id"]
            detail_pair = fetch_json(f"job-{name}", f"{prefix}/jobs/{job_id}")
            detail = detail_pair[0]
            runner = detail.get("runner") if isinstance(detail, dict) else None
            job_pipeline = detail.get("pipeline") if isinstance(detail, dict) else None
            required_tag = REMOTE_CI_TAGS[runner_class]
            if (not isinstance(detail, dict) or detail.get("id") != job_id
                    or detail.get("name") != name or detail.get("status") != "success"
                    or not isinstance(runner, dict) or not positive_int(runner.get("id"))
                    or required_tag not in detail.get("tag_list", [])
                    or not isinstance(job_pipeline, dict) or job_pipeline.get("id") != args.pipeline_id
                    or job_pipeline.get("sha") != commit):
                raise EvidenceError(f"GitLab required job provenance is invalid: {name}")
            references: dict[str, dict[str, object]] = {}
            for kind, endpoint, suffix in (
                ("trace", f"{prefix}/jobs/{job_id}/trace", "trace.log"),
                ("artifact", f"{prefix}/jobs/{job_id}/artifacts", "artifacts.zip"),
            ):
                data, _ = gitlab_fetch(api_base, endpoint, token, args.api_timeout,
                                       allow_cross_origin=True)
                relative = f"remote-ci/jobs/{name}.{suffix}"
                path = stage / relative
                path.write_bytes(data)
                references[kind] = {"path": relative, "bytes": len(data),
                                     "sha256": sha256_file(path)}
            job_records.append({"name": name, "job_id": job_id, "runner_id": runner["id"],
                                "runner_class": runner_class, "runner_tag": required_tag,
                                "status": "success", **references})
        proof = {
            "schema_version": REMOTE_CI_SCHEMA_VERSION,
            "kind": "agentos-gitlab-api-provenance",
            "capture": {"method": "gitlab-api-live-fetch", "api_base_url": api_base,
                        "fetched_at_utc": utc_now()},
            "project": {key: project[key] for key in ("id", "path_with_namespace", "web_url")},
            "pipeline": {key: pipeline[key]
                         for key in ("id", "sha", "ref", "source", "status", "web_url")},
            "api_records": api_records,
            "jobs": job_records,
        }
        remote_path = stage / "remote-ci" / "provenance.json"
        write_json(remote_path, proof)
        validate_remote_ci_provenance(proof, commit, stage)
        source_manifest["authenticity"]["remote_ci"] = {
            "status": "provenance-attached", "pipeline_sha": commit,
            "pipeline_ref": "main", "pipeline_source": "push",
            "source_local_checksums_sha256": sha256_file(bundle / "checksums.sha256"),
            "provenance": {"path": "remote-ci/provenance.json",
                           "sha256": sha256_file(remote_path)},
        }
        write_json(stage / "manifest.json", source_manifest)
        write_checksums(stage)
        verify_bundle(stage)
        if output.exists():
            raise EvidenceError(f"output appeared while remote CI binding was running: {output}")
        os.replace(stage, output)
        print(f"[evidence] live GitLab CI provenance attached: {output}")
        return 0
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        release_output_lock(lock)

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--repo-root", default=".")
    collect_parser.add_argument("--output", required=True)
    collect_parser.add_argument("--git", default="git")
    collect_parser.add_argument("--toolprefix", default="riscv64-linux-gnu-")
    collect_parser.add_argument("--qemu", default="qemu-system-riscv64")
    collect_parser.add_argument("--python", default=sys.executable)
    collect_parser.add_argument("--make", default="make")
    collect_parser.add_argument("--bash", default="bash")
    collect_parser.add_argument("--host-cc", default="cc")
    collect_parser.add_argument("--case-timeout", default="300s")
    collect_parser.add_argument("--idle-notice", default="20")
    collect_parser.add_argument("--command-timeout", type=float, default=3600.0)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("bundle")
    remote_parser = commands.add_parser("bind-remote-ci")
    remote_parser.add_argument("--bundle", required=True)
    remote_parser.add_argument("--output", required=True)
    remote_parser.add_argument("--gitlab-url", required=True)
    remote_parser.add_argument("--project-id", required=True, type=int)
    remote_parser.add_argument("--pipeline-id", required=True, type=int)
    remote_parser.add_argument("--token-file", required=True)
    remote_parser.add_argument("--api-timeout", type=float, default=60.0)
    summary_parser = commands.add_parser("write-summary")
    summary_parser.add_argument("--stage", required=True)
    summary_parser.add_argument("--steps", required=True)
    summary_parser.add_argument("--commit", required=True)
    summary_parser.add_argument("--agent-grace", default="2s")
    summary_parser.add_argument("--mechanism-grace", default="5s")
    summary_parser.add_argument("--workflow-runs", type=int, default=3)
    return parser.parse_args(argv)
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.operation == "collect":
            return collect(args)
        if args.operation == "write-summary":
            return write_summary(args)
        if args.operation == "bind-remote-ci":
            return bind_remote_ci(args)
        print(json.dumps(verify_bundle(Path(args.bundle)), indent=2, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        print("[evidence] interrupted", file=sys.stderr)
        return 130
    except (EvidenceError, OSError, ValueError, KeyError) as error:
        print(f"[evidence] failed: {error}", file=sys.stderr)
        return 1
if __name__ == "__main__":
    raise SystemExit(main())
