#!/usr/bin/env python3
"""Build and verify a compact, commit-bound AgentOS acceptance evidence bundle."""
from __future__ import annotations
import sys as _entry_sys
if __name__ == "__main__" and (not _entry_sys.flags.isolated or not _entry_sys.flags.no_site):
    print("capture-final-evidence: formal entry requires python -I -S scripts/trusted-python-entry.py scripts/capture-final-evidence.py", file=_entry_sys.stderr)
    raise SystemExit(2)
def _isolate_direct_entry_imports() -> None:
    if __name__ != "__main__":
        return
    prefixes = {
        value.replace("\\", "/").rstrip("/").casefold()
        for value in (_entry_sys.base_prefix, _entry_sys.base_exec_prefix, _entry_sys.prefix, _entry_sys.exec_prefix)
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
import argparse, csv, hashlib, json
import math, os, platform, re, shutil, signal, subprocess, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(Path(tempfile.gettempdir()) / f"agentos-pycache-{os.urandom(16).hex()}")
HOST_TOOLS = Path(__file__).resolve().parents[1] / "host_tools"
if "host_tools" not in sys.modules:
    import types as _entry_types
    _entry_package = _entry_types.ModuleType("host_tools")
    _entry_package.__path__ = [str(HOST_TOOLS)]
    _entry_package.__package__ = "host_tools"
    sys.modules["host_tools"] = _entry_package
if str(HOST_TOOLS) not in sys.path:
    sys.path.append(str(HOST_TOOLS))
from host_tools.measured_experiments import (  # noqa: E402
    MeasurementError,
    capture_bundle_artifacts,
    verify_bundle_artifacts,
    verify_measurement_artifact_set,
)
from host_tools.evidence_delivery_contract import (  # noqa: E402
    DeliveryContractError, make_manifest_binding, publish_bundle_and_index,
    validate_delivery_field,
)
from host_tools.evidence_semantic_registry import validate_raw_artifacts  # noqa: E402
from host_tools.dual_state_evidence_contract import DUAL_STATE_RAW_ARTIFACTS  # noqa: E402
from host_tools.strict_json import read_strict_json, strict_json_loads  # noqa: E402
from host_tools.safe_host_paths import (  # noqa: E402
    require_regular_file, require_safe_directory, walk_regular_files_no_links,
)
from host_tools.duration_profile_attestation import (  # noqa: E402
    DurationAttestationError, build_duration_attestation,
    validate_attested_duration_policy, validate_duration_execution_binding)
from host_tools.full_verification_metrics import (  # noqa: E402
    FullVerificationEvidenceError as EvidenceError,
    REQUIRED_EVIDENCE_METRICS, load_budget_config, measurement_values_match,
    parse_measurements,
    write_agent_csv, write_chart, write_metrics_csv,
)
from host_tools.evidence_toolchain_attestation import (  # noqa: E402
    EVALUATION_ARTIFACT_OUTPUT_FILES, EVALUATION_BUILD_OUTPUT_FILES,
    EVALUATION_BUILD_OUTPUT_ROOTS, EVALUATION_CACHE_OUTPUT_ROOTS,
    FormalPythonRuntimeError, ToolAttestationError,
    capture_formal_temporary_binding, capture_version,
    controlled_environment, decode_external_output,
    create_isolated_detached_worktree, create_formal_python_runtime,
    formal_execution_overrides, purge_evaluation_generated_outputs,
    require_clean_head, require_nested_tool_resolution,
    resolve_bash_executable, resolve_executable,
    verify_evaluation_source_tree, validate_formal_evidence_binding,
    verify_tool_attestations, verify_tracked_worktree_bytes,
)
SCHEMA_VERSION = 8
FULL_VERIFY_PROFILE_VERSION = 6
FULL_VERIFY_TIMEOUT_SECONDS = 5 * 60 * 60
SUMMARY_NAME = "verification-summary.json"
SUCCESS_MARKER = "[full-verify] all checks passed"
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SUMMARY_FIELDS = {"schema_version", "full_verify_profile_version", "kind", "status", "commit", "completed_at_utc", "settings", "steps", "artifacts", "step_contract_sha256"}
MANIFEST_FIELDS = {"schema_version", "status", "commit", "collected_at_utc", "authenticity", "delivery", "command", "verification_summary", "raw_artifacts", "environment", "configuration", "metrics", "duration_attestation"}
STEP_CONTRACT = (
    ("target-structure", (), ()),
    ("kernel-budgets", (), ()),
    ("host-platform-alignment", (), ()),
    ("agent-suite", ("agent-suite-timings.log", "agent-suite-guest.log"), ()),
    ("dual-platforms", ("dual-plain-qemu.log", "dual-agentos-qemu.log",
                         "dual-stage-timings.csv", "dual-state-compare.json", "host-platform-alignment.json", *DUAL_STATE_RAW_ARTIFACTS,
                         "dual-targeted-agentbench-guest.log", "dual-measured-experiments.json", "dual-file-query-benchmark.csv"), ()),
    ("proc-reap", ("proc-reap.log",), ()),
    ("syscall-fairness", ("syscall-fairness.log",), ()),
    ("file-resource", ("file-resource.log",), ()),
    ("thread-resource", ("thread-resource.log",), ()),
    ("physical-resource", ("physical-resource.log",), ()),
    ("metadata-recovery", ("metadata-recovery.log",), ()),
    ("observe-recovery", ("observe-recovery.log", "observe-recovery-before-reap.img"), ()),
    ("virtio-disk", ("virtio-disk.log",), ()),
    ("workflow-teardown-race", ("workflow-teardown-race.log",), ()),
    ("fs-enospc", ("fs-enospc.log",), ()),
    ("fs-allocator-fault", ("fs-allocator-fault.log", "fs-allocator-evidence.tar"), ()),
)
REQUIRED_RAW_FILES = {
    "dual-plain-qemu.log", "dual-agentos-qemu.log", "dual-stage-timings.csv",
    "dual-state-compare.json", "host-platform-alignment.json",
    *DUAL_STATE_RAW_ARTIFACTS, "dual-targeted-agentbench-guest.log",
    "dual-measured-experiments.json", "dual-file-query-benchmark.csv",
    "agent-suite-timings.log", "agent-suite-guest.log",
    "proc-reap.log", "syscall-fairness.log", "file-resource.log", "thread-resource.log",
    "physical-resource.log", "metadata-recovery.log", "observe-recovery.log", "observe-recovery-before-reap.img",
    "virtio-disk.log",
    "workflow-teardown-race.log", "fs-enospc.log",
    "fs-allocator-fault.log", "fs-allocator-evidence.tar",
}
CORE_FILES = {
    "manifest.json", SUMMARY_NAME, "checksums.sha256", "configuration/kernel-budgets.json",
    "environment/environment.json", "logs/full-verify.log", "metrics/measurements.csv",
    "metrics/agent-case-timings.csv", "metrics/commands.csv", "charts/budget-usage.svg",
    "metrics/file-query-benchmark.csv", "metrics/file-query-benchmark.json",
} | {f"logs/raw/{name}" for name in REQUIRED_RAW_FILES}
TOOL_LABELS = {"git", "compiler", "qemu", "python", "make", "bash", "host_cc"}
CSV_SCHEMAS = {
    "measurements_csv": ["metric", "actual", "baseline", "limit", "unit", "usage_ratio",
                         "source_line", "source_log", "source_log_sha256"],
    "agent_timings_csv": ["sequence", "case", "seconds", "source_line", "source_log",
                          "source_log_sha256"],
    "command_csv": ["command", "argv_json", "returncode", "elapsed_seconds", "timeout_seconds", "source_log", "source_log_sha256"],
}
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
def step_contract_sha256(steps: list[dict[str, object]]) -> str:
    payload = json.dumps(steps, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
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
def verify_capture_source_tree(
    git: Path,
    repository: Path,
    worktree: Path,
    commit: str,
    environment: dict[str, str],
    stage: str,
    *,
    source_checkout: bool,
    source_output_roots: tuple[str, ...] = (),
) -> None:
    roots = [*EVALUATION_BUILD_OUTPUT_ROOTS, *EVALUATION_CACHE_OUTPUT_ROOTS]
    if source_checkout:
        roots.extend(source_output_roots)
    try:
        verify_evaluation_source_tree(
            git,
            repository,
            worktree,
            commit,
            environment,
            allowed_output_roots=roots,
            allowed_output_files=(
                *EVALUATION_BUILD_OUTPUT_FILES,
                *EVALUATION_ARTIFACT_OUTPUT_FILES,
            ),
            stage=stage,
        )
    except (OSError, ToolAttestationError) as error:
        raise EvidenceError(f"capture source gate failed {stage}: {error}") from error
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
    for step, (name, fixed, patterns) in zip(steps, STEP_CONTRACT):
        artifacts = step.get("artifacts")
        duration = step.get("duration_seconds")
        started, ended = step.get("started_epoch"), step.get("ended_epoch")
        if (not isinstance(artifacts, list) or not all(isinstance(item, str) for item in artifacts)
                or not isinstance(duration, (int, float)) or isinstance(duration, bool)
                or not isinstance(started, (int, float)) or isinstance(started, bool)
                or not isinstance(ended, (int, float)) or isinstance(ended, bool)
                or not all(math.isfinite(float(item)) for item in (duration, started, ended))
                or float(started) <= 0
                or float(duration) < 0 or float(ended) < float(started)
                or abs(float(duration) - (float(ended) - float(started))) > 1e-6):
            raise EvidenceError(f"verification summary step timing is invalid: {name}")
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
    if not valid_utc_timestamp(value.get("completed_at_utc")):
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
    if value.get("step_contract_sha256") != step_contract_sha256(steps):
        raise EvidenceError("verification summary step contract hash differs")
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
        "step_contract_sha256": step_contract_sha256(steps),
    }
    validate_summary(summary)
    atomic_json(incoming / SUMMARY_NAME, summary)
    print(f"[evidence] wrote {SUMMARY_NAME}")
    return 0
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
def validate_dual_measurement_inventory(directory: Path, commit: str) -> None:
    try:
        verify_measurement_artifact_set(
            directory / "dual-measured-experiments.json", directory / "dual-file-query-benchmark.csv",
            directory, commit,
            "dual-targeted-agentbench-guest.log",
        )
    except MeasurementError as error:
        raise EvidenceError(f"dual measurement evidence is invalid: {error}") from error
def validate_fs_allocator_archive(path: Path) -> None:
    result = subprocess.run([sys.executable, "-I", "-S", str(Path(__file__).with_name("trusted-python-entry.py")),
                             "scripts/fs-allocator-evidence.py", "verify-archive", "--archive", str(path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        detail = (decode_external_output(result.stderr).strip()
                  or decode_external_output(result.stdout).strip()
                  or f"exit {result.returncode}")
        raise EvidenceError(f"filesystem allocator evidence is invalid: {detail}")
def replay_raw_contract(
    raw_dir: Path, summary_path: Path, expected_commit: str
) -> dict[str, object]:
    """Replay every semantic contract from this script's immutable source tree."""
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise EvidenceError("raw replay expected commit is invalid")
    try:
        summary = validate_summary(read_strict_json(summary_path))
    except (OSError, UnicodeDecodeError, ValueError, KeyError) as error:
        raise EvidenceError(f"raw replay summary is invalid: {error}") from error
    if summary.get("commit") != expected_commit:
        raise EvidenceError("raw replay summary commit differs")
    try:
        raw_dir = require_safe_directory(raw_dir)
        summary_path = require_regular_file(
            summary_path, nonempty=True, maximum_bytes=16 << 20
        )
        raw_files = walk_regular_files_no_links(
            raw_dir,
            max_files=512,
            max_directories=1,
            max_total_bytes=2 << 30,
            max_depth=1,
        )
    except (OSError, ValueError) as error:
        raise EvidenceError(f"raw replay input tree is unsafe: {error}") from error
    actual = {path.relative_to(raw_dir).as_posix() for path in raw_files}
    expected = {str(record["name"]) for record in summary["artifacts"]}
    if summary_path.parent == raw_dir:
        if summary_path.name != SUMMARY_NAME:
            raise EvidenceError("raw replay colocated summary name is invalid")
        expected.add(SUMMARY_NAME)
    if actual != expected:
        raise EvidenceError(
            "raw replay inventory differs from its summary: "
            f"missing={sorted(expected - actual)} extra={sorted(actual - expected)}"
        )
    for record in summary["artifacts"]:
        path = raw_dir / str(record["name"])
        try:
            path = require_regular_file(path, nonempty=True, maximum_bytes=1 << 30)
        except (OSError, ValueError) as error:
            raise EvidenceError(
                f"raw replay artifact is unsafe: {record['name']}"
            ) from error
        if (
            path.stat().st_size != record["bytes"]
            or sha256_file(path) != record["sha256"]
        ):
            raise EvidenceError(
                f"raw replay artifact differs from summary: {record['name']}"
            )
    validate_dual_measurement_inventory(raw_dir, expected_commit)
    validate_raw_artifacts(
        raw_dir,
        Path(__file__).resolve().parents[1],
        summary_path.parent != raw_dir,
    )
    validate_fs_allocator_archive(raw_dir / "fs-allocator-evidence.tar")
    return summary
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
def validate_authenticity(manifest: dict[str, object], commit: str) -> None:
    authenticity = manifest.get("authenticity")
    if not isinstance(authenticity, dict) or set(authenticity) != {"local", "remote_ci"}:
        raise EvidenceError("manifest authenticity boundary is invalid")
    local = authenticity.get("local")
    if local != {"kind": "committed-git-head", "commit": commit,
                  "execution": "clean-detached-worktree"}:
        raise EvidenceError("manifest local authenticity boundary is invalid")
    if authenticity.get("remote_ci") != {"status": "not-attached"}:
        raise EvidenceError("manifest remote CI boundary must remain not-attached")
def verify_bundle(root: Path, contract_root: Path) -> dict[str, object]:
    root = root.resolve()
    contract_root = require_safe_directory(contract_root).resolve(strict=True)
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
        manifest = read_strict_json(root / "manifest.json")
        summary = validate_summary(read_strict_json(root / SUMMARY_NAME))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise EvidenceError(f"bundle JSON is invalid: {error}") from error
    if (not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS
            or manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("status") != "ready"):
        raise EvidenceError("manifest is not ready")
    if manifest.get("commit") != summary["commit"]:
        raise EvidenceError("manifest and summary commits differ")
    git = resolve_executable("git")
    identity_root = Path(tempfile.mkdtemp(prefix="agentos-verify-source-"))
    try:
        source_commit = require_clean_head(
            git, contract_root, controlled_environment(identity_root, [git.parent])
        )
    except ToolAttestationError as error: raise EvidenceError(f"contract root is not authenticated: {error}") from error
    finally:
        shutil.rmtree(identity_root, ignore_errors=True)
    if source_commit != manifest["commit"]:
        raise EvidenceError("contract root HEAD differs from evidence commit")
    validate_delivery_field(manifest.get("delivery"), manifest["commit"])
    validate_authenticity(manifest, str(summary["commit"]))
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
    validate_dual_measurement_inventory(root / "logs/raw", str(summary["commit"]))
    validate_raw_artifacts(root / "logs/raw", contract_root)
    validate_fs_allocator_archive(root / "logs/raw" / "fs-allocator-evidence.tar")
    command = manifest.get("command")
    if (not isinstance(command, dict)
            or set(command) != {"argv", "environment", "returncode", "elapsed_seconds", "timeout_seconds", "timed_out", "log", "log_sha256"}
            or not isinstance(command.get("argv"), list) or not command["argv"] or any(not isinstance(item, str) or not item for item in command["argv"])
            or not isinstance(command.get("environment"), dict) or not command["environment"] or any(not isinstance(key, str) or not key or not isinstance(item, str) for key, item in command["environment"].items())
            or isinstance(command.get("returncode"), bool) or command.get("returncode") != 0
            or isinstance(command.get("elapsed_seconds"), bool)
            or not isinstance(command.get("elapsed_seconds"), (int, float)) or not math.isfinite(command["elapsed_seconds"]) or command["elapsed_seconds"] < 0
            or command.get("timed_out") is not False
            or isinstance(command.get("timeout_seconds"), bool) or not isinstance(command.get("timeout_seconds"), (int, float))
            or not 0 < command["timeout_seconds"] <= 86400
            or command.get("log") != "logs/full-verify.log"
            or command.get("log_sha256") != sha256_file(root / "logs/full-verify.log")):
        raise EvidenceError("full-verify command did not succeed")
    full_log = root / "logs/full-verify.log"
    full_log_lines = full_log.read_text(encoding="utf-8", errors="replace").splitlines()
    if full_log_lines.count(SUCCESS_MARKER) != 1: raise EvidenceError("full-verify completion marker is missing or duplicated")
    metric_paths = {"measurements_csv": "metrics/measurements.csv",
                    "agent_timings_csv": "metrics/agent-case-timings.csv", "command_csv": "metrics/commands.csv"}
    csv_rows = {key: read_exact_csv(root / path, CSV_SCHEMAS[key]) for key, path in metric_paths.items()}
    measurement_rows = csv_rows["measurements_csv"]
    metric_names = [row.get("metric") for row in measurement_rows]
    if len(metric_names) != len(REQUIRED_EVIDENCE_METRICS) or set(metric_names) != REQUIRED_EVIDENCE_METRICS: raise EvidenceError("critical metric inventory differs")
    environment_path = require_file_ref(root, manifest.get("environment"), "environment/environment.json")
    configuration_path = require_file_ref(root, manifest.get("configuration"), "configuration/kernel-budgets.json")
    try:
        environment_record = read_strict_json(environment_path)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise EvidenceError("environment inventory is invalid") from error
    try:
        tool_by_label = validate_formal_evidence_binding(
            environment_record, command["environment"], command["environment"].get("PYTHON_BIN"),
            environment_record.get("python_path_resolution") if isinstance(environment_record, dict) else None,
            contract_root, TOOL_LABELS)
    except (OSError, FormalPythonRuntimeError) as error:
        raise EvidenceError(f"formal execution environment is invalid: {error}") from error
    try:
        duration_profile = validate_attested_duration_policy(
            manifest["duration_attestation"], contract_root=contract_root,
            environment=command["environment"], log_lines=full_log_lines,
            execution_tools=tool_by_label, configuration_path=configuration_path)
    except (DurationAttestationError, OSError, ValueError) as error:
        raise EvidenceError(f"formal duration policy is invalid: {error}") from error
    tool_records = environment_record["tools"]
    if (not (compiler_path := str(tool_by_label["compiler"].get("path", ""))).endswith("gcc") or command["argv"] != [tool_by_label["make"].get("path"), "full-verify", f"TOOLPREFIX={compiler_path[:-3]}"]): raise EvidenceError("full-verify command argv is not canonical")
    version_logs = set()
    for record in tool_records:
        label, relative = record["label"], record.get("log")
        expected_log = f"environment/versions/{label}.txt"
        version_path = root / safe_relative(str(relative))
        if relative != expected_log or not version_path.is_file():
            raise EvidenceError(f"environment version record is invalid: {label}")
        version_lines = decode_external_output(version_path.read_bytes()).splitlines()
        if (record.get("log_sha256") != sha256_file(version_path)
                or not SHA256.fullmatch(str(record.get("executable_sha256", "")))
                or not version_lines
                or record.get("first_line") != version_lines[0]):
            raise EvidenceError(f"environment version record is invalid: {label}")
        version_logs.add(relative)
    actual_version_logs = {path.relative_to(root).as_posix() for path in
                           (root / "environment/versions").rglob("*") if path.is_file()}
    if actual_version_logs != version_logs:
        raise EvidenceError("environment version file inventory differs")
    allowed_files = ((CORE_FILES - {"checksums.sha256"}) | version_logs
                     | {record["path"] for record in raw_records})
    if actual != allowed_files:
        raise EvidenceError("bundle contains unreferenced files")
    metrics_ref = manifest.get("metrics")
    if not isinstance(metrics_ref, dict):
        raise EvidenceError("manifest metrics references are invalid")
    for key, path in metric_paths.items():
        require_file_ref(root, metrics_ref.get(key), path)
    try:
        verify_bundle_artifacts(
            root,
            metrics_ref,
            str(manifest["commit"]),
            summary_by_name.get("agent-suite-guest.log"), command["argv"],
        )
    except MeasurementError as error:
        raise EvidenceError(f"file-query benchmark provenance is invalid: {error}") from error
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
        command_argv = strict_json_loads(command_row["argv_json"])
        row_elapsed, row_timeout = float(command_row["elapsed_seconds"]), float(command_row["timeout_seconds"])
    except (json.JSONDecodeError, ValueError) as error:
        raise EvidenceError("command CSV argv is invalid") from error
    if (command_row["command"] != "make full-verify" or command_argv != command.get("argv")
            or command_row["returncode"] != str(command["returncode"])
            or not math.isfinite(row_elapsed) or not math.isfinite(row_timeout)
            or row_timeout != command["timeout_seconds"]
            or abs(row_elapsed - float(command["elapsed_seconds"])) > 1e-9):
        raise EvidenceError("command CSV differs from manifest command")
    config = load_budget_config(configuration_path)
    replayed, replayed_cases = parse_measurements(
        root / "logs/full-verify.log",
        root / "logs/raw/agent-suite-timings.log",
        config,
        duration_profile=duration_profile,
    )
    expected_rows = {row["metric"]: row for row in replayed}
    for row in measurement_rows:
        expected_row = expected_rows[row["metric"]]
        if (not measurement_values_match(row, expected_row)
                or row["unit"] != expected_row["unit"]
                or row["source_line"] != str(expected_row["source_line"])):
            raise EvidenceError("measurements CSV differs from raw evidence")
    agent_row = next(row for row in measurement_rows
                     if row["metric"] == "agent_suite_total_seconds")
    agent_total = metrics_ref.get("agent_total_seconds")
    expected_agent_total = expected_rows["agent_suite_total_seconds"]
    if not isinstance(agent_total, dict) or agent_total != expected_agent_total:
        raise EvidenceError("manifest Agent total differs from metrics CSV")
    agent_rows = csv_rows["agent_timings_csv"]
    case_fields = ("sequence", "case", "seconds", "source_line")
    if len(agent_rows) != len(replayed_cases) or any(
            tuple(row[key] for key in case_fields) != tuple(str(expected[key]) for key in case_fields)
            for row, expected in zip(agent_rows, replayed_cases)):
        raise EvidenceError("Agent timing CSV differs from raw evidence")
    full_hash = command["log_sha256"]
    chart = (chart_bytes := chart_path.read_bytes()).decode("utf-8")
    expected_metadata = (f"source_measurements_sha256={measurement_hash} full_verify_sha256={full_hash} "
                         f"agent_timing_sha256={timing_hash}")
    if re.findall(r"<metadata>([^<>]*)</metadata>", chart) != [expected_metadata]:
        raise EvidenceError("chart provenance hashes are incomplete")
    with tempfile.TemporaryDirectory(prefix="agentos-chart-replay-") as temporary:
        replayed_chart = Path(temporary) / "budget-usage.svg"
        write_chart(replayed_chart, replayed, measurement_hash, full_hash, str(timing_hash))
        if chart_bytes != replayed_chart.read_bytes():
            raise EvidenceError("chart differs from replayed metrics")
    return {"schema_version": SCHEMA_VERSION, "status": "ready", "commit": manifest["commit"],
            "remote_ci": manifest["authenticity"]["remote_ci"]["status"],
            "files_verified": len(expected)}
def collect(args: argparse.Namespace) -> int:
    if not math.isfinite(args.command_timeout) or not 0 < args.command_timeout <= 86400:
        raise EvidenceError("full-verify command timeout is invalid")
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
        "bash": resolve_bash_executable(args.bash, git),
        "host_cc": resolve_executable(args.host_cc),
    }
    environment_root = Path(tempfile.mkdtemp(prefix="agentos-evidence-env-"))
    tool_directories = [
        tools[label].parent
        for label in ("make", "git", "bash", "python", "host_cc", "compiler", "qemu")
    ]
    base_environment = controlled_environment(environment_root, tool_directories)
    require_nested_tool_resolution(tools, base_environment)
    try:
        commit = require_clean_head(git, repo, base_environment)
        verify_capture_source_tree(
            git,
            repo,
            repo,
            commit,
            base_environment,
            "before detached checkout",
            source_checkout=True,
            source_output_roots=(
                PurePosixPath(*output.relative_to(repo).parts).as_posix(),
            ) if output.is_relative_to(repo) else (),
        )
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
    checkout_root: Path | None = None
    failed = output.with_name(output.name + ".failed")
    try:
        versions = [
            capture_version(label, path, stage, base_environment)
            for label, path in tools.items()
        ]
        checkout_root = Path(tempfile.mkdtemp(prefix="agentos-evidence-checkout-"))
        if failed.exists():
            raise EvidenceError(f"failed evidence path already exists: {failed}")
        _isolated, worktree = create_isolated_detached_worktree(
            git, repo, commit, checkout_root, base_environment
        )
        verify_capture_source_tree(
            git,
            _isolated,
            worktree,
            commit,
            base_environment,
            "before full-verify purge",
            source_checkout=False,
        )
        try:
            purge_evaluation_generated_outputs(
                git,
                _isolated,
                worktree,
                commit,
                base_environment,
                output_roots=(
                    *EVALUATION_BUILD_OUTPUT_ROOTS,
                    *EVALUATION_CACHE_OUTPUT_ROOTS,
                ),
                output_files=EVALUATION_BUILD_OUTPUT_FILES,
            )
        except (OSError, ToolAttestationError) as error:
            raise EvidenceError(f"capture build purge failed: {error}") from error
        verify_capture_source_tree(
            git,
            _isolated,
            worktree,
            commit,
            base_environment,
            "after full-verify purge",
            source_checkout=False,
        )
        duration_attestation = build_duration_attestation(
            contract_root=worktree, profile=args.agent_test_duration_profile,
            toolprefix=args.toolprefix, qemu=args.qemu, python_bin=args.python,
            host_cc=args.host_cc, shell_bin=args.bash)
        validate_duration_execution_binding(
            duration_attestation, {record["label"]: record for record in versions})
        python_runtime = create_formal_python_runtime(
            root=environment_root, real_python=tools["python"], shell=tools["bash"],
            git=git, repository=_isolated, worktree=worktree, commit=commit,
            environment=base_environment)
        evidence_stage = stage / "runtime" / "evidence-stage"
        (evidence_stage / "incoming").mkdir(parents=True)
        full_log = stage / "logs" / "full-verify.log"
        command = [str(tools["make"]), "full-verify", f"TOOLPREFIX={effective_prefix}"]
        selected_env = formal_execution_overrides(
            evidence_stage, tools, python_runtime.executable,
            args.case_timeout, args.idle_notice,
            args.agent_test_duration_profile)
        environment = controlled_environment(environment_root, [python_runtime.directory, *tool_directories], selected_env)
        temporary_directory_binding = capture_formal_temporary_binding(environment)
        python_path_resolution = python_runtime.path_resolution(environment)
        verify_tool_attestations(tools, versions, stage, base_environment, "before execution")
        python_runtime.verify("before execution")
        returncode, elapsed, timed_out = run_logged(
            command, worktree, environment, full_log, args.command_timeout
        )
        verify_tool_attestations(tools, versions, stage, base_environment, "during execution")
        python_runtime.verify("after execution")
        if capture_formal_temporary_binding(environment) != temporary_directory_binding:
            raise EvidenceError(
                "formal temporary directory identity changed during execution"
            )
        verify_capture_source_tree(
            git,
            _isolated,
            worktree,
            commit,
            base_environment,
            "after full-verify execution",
            source_checkout=False,
        )
        if returncode != 0:
            raise EvidenceError(f"make full-verify failed with rc={returncode}")
        summary_source = evidence_stage / "incoming" / SUMMARY_NAME
        try:
            summary = validate_summary(read_strict_json(summary_source))
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise EvidenceError(f"full-verify did not publish a valid summary: {error}") from error
        if summary["commit"] != commit:
            raise EvidenceError("full-verify summary is not bound to detached HEAD")
        validate_dual_measurement_inventory(evidence_stage / "incoming", commit)
        validate_raw_artifacts(evidence_stage / "incoming", worktree, False)
        validate_fs_allocator_archive(evidence_stage / "incoming" / "fs-allocator-evidence.tar")
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
        try:
            benchmark_refs = capture_bundle_artifacts(
                stage, raw_records, command, commit
            )
        except MeasurementError as error:
            raise EvidenceError(f"measured file-query benchmark is missing: {error}") from error
        metrics, agent_cases = parse_measurements(
            full_log,
            stage / timing["path"],
            config,
            duration_profile=args.agent_test_duration_profile,
        )
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
        environment_record = {
            "captured_at_utc": utc_now(), "platform": platform.platform(),
            "machine": platform.machine(), "python_runtime": sys.version,
            "python_launch": python_runtime.record,
            "python_path_resolution": python_path_resolution,
            "execution_environment": environment,
            "temporary_directory_binding": temporary_directory_binding,
            "tools": versions,
        }
        environment_path = stage / "environment" / "environment.json"
        write_json(environment_path, environment_record)
        command_record = {
            "argv": command, "environment": environment, "returncode": returncode,
            "elapsed_seconds": round(elapsed, 9), "timeout_seconds": args.command_timeout,
            "timed_out": timed_out,
            "log": full_relative, "log_sha256": full_hash,
        }
        with (stage / "metrics" / "commands.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "command", "argv_json", "returncode", "elapsed_seconds", "timeout_seconds",
                "source_log", "source_log_sha256",
            ])
            writer.writeheader()
            writer.writerow({"command": "make full-verify", "argv_json": json.dumps(command),
                             "returncode": returncode, "elapsed_seconds": round(elapsed, 9),
                             "timeout_seconds": args.command_timeout,
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
            "delivery": make_manifest_binding(commit, output.name),
            "duration_attestation": duration_attestation,
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
                        **benchmark_refs,
                        "chart": {"path": "charts/budget-usage.svg", "sha256": sha256_file(chart_path)},
                        "chart_sha256": sha256_file(chart_path),
                        "source_measurements_sha256": measurements_hash,
                        "agent_total_seconds": agent_total},
        }
        write_json(stage / "manifest.json", manifest)
        shutil.rmtree(stage / "runtime")
        write_checksums(stage)
        verify_bundle(stage, worktree)
        verify_tool_attestations(
            tools, versions, stage, base_environment, "before publication"
        )
        python_runtime.verify("before publication")
        publish_bundle_and_index(
            repo, stage, output, commit, manifest["delivery"]["release"], git=git,
        )
        print(f"[evidence] bundle captured: {output}")
        return 0
    except BaseException as error:
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
        if checkout_root is not None and checkout_root.exists():
            shutil.rmtree(checkout_root, ignore_errors=True)
        release_output_lock(output_lock)
        shutil.rmtree(environment_root, ignore_errors=True)
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
    collect_parser.add_argument(
        "--agent-test-duration-profile",
        choices=("local-e3", "none"),
        required=True,
    )
    collect_parser.add_argument("--case-timeout", default="300s")
    collect_parser.add_argument("--idle-notice", default="20")
    collect_parser.add_argument("--command-timeout", type=float, default=FULL_VERIFY_TIMEOUT_SECONDS)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("bundle")
    verify_parser.add_argument("--contract-root", required=True)
    raw_parser = commands.add_parser("replay-raw")
    raw_parser.add_argument("--raw-dir", type=Path, required=True)
    raw_parser.add_argument("--summary", type=Path, required=True)
    raw_parser.add_argument("--expected-commit", required=True)
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
        if args.operation == "replay-raw":
            replay_raw_contract(args.raw_dir, args.summary, args.expected_commit)
            print("[evidence] raw semantic replay passed")
            return 0
        print(json.dumps(verify_bundle(Path(args.bundle), Path(args.contract_root)), indent=2, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        print("[evidence] interrupted", file=sys.stderr)
        return 130
    except (EvidenceError, DeliveryContractError, OSError, ValueError, KeyError) as error:
        print(f"[evidence] failed: {error}", file=sys.stderr)
        return 1
if __name__ == "__main__":
    raise SystemExit(main())
