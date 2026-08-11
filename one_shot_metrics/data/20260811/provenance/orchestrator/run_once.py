#!/usr/bin/env python3
"""Run the frozen advanced-figure measurement campaign exactly once.

This entry point is deliberately not wired into Make or CI.  It publishes a
campaign directory only after collection and post-processing have stopped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


CI_VARIABLES = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "CIRCLECI",
    "JENKINS_URL",
    "TEAMCITY_VERSION",
    "TF_BUILD",
)
SAFE_ENV_KEYS = (
    "LANG",
    "LC_ALL",
    "TZ",
    "WSL_DISTRO_NAME",
    "WSL_INTEROP",
    "PYTHONHASHSEED",
    "AGENTOS_BUILD_JOBS",
)
CANONICAL_GUESTS = (
    "agenteval_ucore",
    "agenttask_ucore",
    "agent_eevdf_ucore",
)


class CampaignError(RuntimeError):
    pass


class CommandFailure(CampaignError):
    def __init__(self, label: str, returncode: int, log_path: Path):
        super().__init__(
            f"command {label!r} failed with exit code {returncode}; "
            f"see {log_path}"
        )
        self.label = label
        self.returncode = returncode
        self.log_path = log_path


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def discover_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / ".git").exists()
            and (candidate / "Makefile").is_file()
            and (candidate / "user" / "Makefile").is_file()
            and (candidate / "scripts" / "agent_test_runner.py").is_file()
        ):
            return candidate.resolve()
    raise CampaignError("could not discover the AgentOS repository root")


def load_plan(script_dir: Path) -> dict[str, Any]:
    path = script_dir / "plan.json"
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CampaignError(f"cannot read {path}: {error}") from error
    try:
        assert plan["schema_version"] == 1
        assert plan["collections"]["contest"]["boots"] == 16
        assert plan["collections"]["agenteval"]["hit_counts"] == [1, 2, 4, 8]
        assert plan["collections"]["agenteval"]["boots_per_hit_count"] == 1
        assert plan["collections"]["task"]["boots"] == 4
        assert plan["collections"]["task"]["rounds_per_path_per_boot"] == 8
        assert plan["collections"]["eevdf"]["boots"] == 6
    except (AssertionError, KeyError, TypeError) as error:
        raise CampaignError("plan.json does not describe the frozen campaign") from error
    challenges = plan["collections"]["agenteval"]["challenge_by_hit_count"]
    if set(challenges) != {"1", "2", "4", "8"}:
        raise CampaignError("plan.json has an invalid agenteval challenge grid")
    values = list(challenges.values())
    if len(set(values)) != 4 or any(
        re.fullmatch(r"[0-9a-f]{16}", value) is None or value == "0" * 16
        for value in values
    ):
        raise CampaignError("agenteval challenges must be distinct nonzero hex64 values")
    return plan


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise CampaignError(f"refusing to replace stale atomic-write file: {temporary}")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write(path, payload)


def fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def command_output(command: Sequence[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise CampaignError(f"preflight command failed: {' '.join(command)}: {error}") from error
    return result.stdout.strip()


def resolve_executable(value: str, label: str) -> str:
    resolved = shutil.which(value)
    if resolved is None:
        raise CampaignError(f"required {label} executable was not found: {value}")
    return resolved


def resolve_toolprefix(requested: str | None) -> str:
    if requested:
        return requested
    for prefix in ("riscv64-unknown-elf-", "riscv64-linux-gnu-"):
        if shutil.which(prefix + "gcc") is not None:
            return prefix
    raise CampaignError(
        "no RISC-V compiler found; set TOOLPREFIX or pass --toolprefix"
    )


def is_windows_interop_executable(path: str) -> bool:
    return os.name == "posix" and path.lower().endswith(".exe")


def windows_path(path: Path, repo: Path) -> str:
    resolve_executable("wslpath", "WSL path converter")
    converted = command_output(("wslpath", "-w", str(path)), repo)
    if not converted:
        raise CampaignError(f"wslpath returned an empty path for {path}")
    return converted


def interpreter_path(path: Path, interpreter: str, repo: Path) -> str:
    if is_windows_interop_executable(interpreter):
        return windows_path(path, repo)
    return str(path)


def git_metadata(repo: Path, campaign_dir: Path) -> dict[str, Any]:
    commit = command_output(("git", "rev-parse", "HEAD"), repo)
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise CampaignError("git returned an invalid HEAD commit")
    branch = command_output(("git", "rev-parse", "--abbrev-ref", "HEAD"), repo)
    excluded = relative_posix(campaign_dir, repo)
    production_status = command_output(
        (
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            ".",
            f":(exclude){excluded}",
            f":(exclude){excluded}/**",
        ),
        repo,
    )
    if production_status:
        preview = "\n".join(production_status.splitlines()[:20])
        raise CampaignError(
            "production tree is not clean outside one_shot_metrics; "
            f"refusing one-shot collection:\n{preview}"
        )
    campaign_status = command_output(
        (
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            excluded,
        ),
        repo,
    )
    return {
        "source_commit": commit,
        "branch": branch,
        "source_tree_scope_clean": True,
        "clean_scope_exclusions": [f"{excluded}/**"],
        "excluded_scope_status": campaign_status.splitlines(),
    }


def environment_metadata(
    repo: Path,
    *,
    make_tool: str,
    qemu: str,
    bash: str,
    toolprefix: str,
    plot_python: str,
) -> dict[str, Any]:
    compiler = resolve_executable(toolprefix + "gcc", "RISC-V compiler")
    tools = {
        "python": command_output((sys.executable, "--version"), repo),
        "git": command_output(("git", "--version"), repo),
        "make": command_output((make_tool, "--version"), repo).splitlines()[0],
        "qemu": command_output((qemu, "--version"), repo).splitlines()[0],
        "compiler": command_output((compiler, "--version"), repo).splitlines()[0],
        "bash": command_output((bash, "--version"), repo).splitlines()[0],
        "plot_python": command_output((plot_python, "--version"), repo),
    }
    try:
        uname = command_output(("uname", "-a"), repo)
    except CampaignError:
        uname = platform.platform()
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "uname": uname,
        "tools": tools,
        "selected_environment": {
            key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ
        },
    }


class CommandRecorder:
    def __init__(
        self,
        repo: Path,
        staging: Path,
        scratch: Path,
        extra_redactions: Sequence[tuple[str, str]] = (),
    ):
        self.repo = repo
        self.staging = staging
        self.scratch = scratch
        self.records: list[dict[str, Any]] = []
        self.extra_redactions = tuple(extra_redactions)
        self.logs_dir = staging / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _scrub(self, value: str) -> str:
        replacements = (
            (str(self.scratch), "<scratch>"),
            (str(self.staging), "<campaign>"),
            (str(self.repo), "<repo>"),
            *self.extra_redactions,
        )
        result = value
        for original, replacement in replacements:
            result = result.replace(original, replacement)
        return result

    def run(
        self,
        label: str,
        command: Sequence[str],
        *,
        env_updates: Mapping[str, str] | None = None,
    ) -> None:
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", label) is None:
            raise CampaignError(f"unsafe command label: {label!r}")
        log_path = self.logs_dir / f"{label}.txt"
        if log_path.exists():
            raise CampaignError(f"duplicate command log: {log_path}")
        environment = os.environ.copy()
        environment.pop("BASH_ENV", None)
        environment.pop("ENV", None)
        if env_updates:
            environment.update(env_updates)
        record: dict[str, Any] = {
            "label": label,
            "argv": [self._scrub(str(item)) for item in command],
            "log": relative_posix(log_path, self.staging),
            "started_at_utc": utc_now(),
        }
        started = time.monotonic()
        print(f"[one-shot] {label}", flush=True)
        try:
            with log_path.open("xb") as log:
                result = subprocess.run(
                    list(command),
                    cwd=self.repo,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            returncode = result.returncode
        except OSError as error:
            with log_path.open("ab") as log:
                log.write(f"orchestrator error: {error}\n".encode("utf-8"))
            returncode = 127
        record.update(
            {
                "finished_at_utc": utc_now(),
                "duration_seconds": round(time.monotonic() - started, 6),
                "returncode": returncode,
            }
        )
        self.records.append(record)
        if returncode != 0:
            raise CommandFailure(label, returncode, log_path)


def validate_output_path(repo: Path, plan: Mapping[str, Any]) -> Path:
    relative = Path(str(plan["canonical_output"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise CampaignError("canonical output must be a contained relative path")
    if any(part.lower() == "results" for part in relative.parts):
        raise CampaignError("one-shot output must not be placed below results/")
    output = (repo / relative).absolute()
    try:
        output.resolve().relative_to(repo.resolve())
    except ValueError as error:
        raise CampaignError("canonical output escapes the repository") from error
    return output


def reject_symlink_chain(repo: Path, destination: Path) -> None:
    current = repo
    for part in destination.relative_to(repo).parts:
        current = current / part
        if current.is_symlink():
            raise CampaignError(f"output path traverses a symlink: {current}")


def copy_provenance_scripts(script_dir: Path, staging: Path) -> Path:
    destination = staging / "provenance" / "orchestrator"
    destination.mkdir(parents=True, exist_ok=False)
    for name in (
        "plan.json",
        "run_once.py",
        "prepare_guests.py",
        "extract.py",
        "validate.py",
        "plot.py",
    ):
        source = script_dir / name
        if not source.is_file():
            raise CampaignError(f"required campaign component is missing: {source}")
        shutil.copy2(source, destination / name)
    return destination


def prepare_guests(
    recorder: CommandRecorder,
    script_dir: Path,
    repo: Path,
    scratch: Path,
    staging: Path,
    hit_counts: Iterable[int],
) -> dict[int, Path]:
    prepared: dict[int, Path] = {}
    for hit_count in hit_counts:
        app_dir = scratch / "prepared-guests" / f"hit-{hit_count:02d}"
        app_dir.mkdir(parents=True, exist_ok=False)
        recorder.run(
            f"prepare-hit-{hit_count:02d}",
            (
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(script_dir / "prepare_guests.py"),
                str(app_dir),
                "--hit-count",
                str(hit_count),
                "--repo-root",
                str(repo),
            ),
        )
        expected = [app_dir / f"{guest}.c" for guest in CANONICAL_GUESTS]
        expected.append(app_dir / "guest-manifest.json")
        missing = [path.name for path in expected if not path.is_file()]
        if missing:
            raise CampaignError(
                f"prepare_guests.py omitted files for hit={hit_count}: {missing}"
            )
        snapshot = staging / "provenance" / "prepared-guests" / f"hit-{hit_count:02d}"
        shutil.copytree(app_dir, snapshot)
        prepared[hit_count] = app_dir
    return prepared


def make_arguments(
    *,
    make_tool: str,
    target: str,
    app_dir: Path,
    guest: str,
    toolprefix: str,
    challenge: str,
) -> list[str]:
    return [
        make_tool,
        "--no-print-directory",
        "-rR",
        "-f",
        "Makefile",
        target,
        f"app_dir={app_dir}",
        f"CH_TESTS={guest}",
        f"INIT_PROC={guest}",
        "CHAPTER=agent",
        "LOG=error",
        f"TOOLPREFIX={toolprefix}",
        f"PYTHON_BIN={sys.executable}",
        f"AGENT_EVAL_CHALLENGE_HEX={challenge}",
    ]


def build_guest_image(
    recorder: CommandRecorder,
    repo: Path,
    scratch: Path,
    *,
    label: str,
    guest: str,
    app_dir: Path,
    make_tool: str,
    toolprefix: str,
    challenge: str,
) -> dict[str, Any]:
    recorder.run(
        f"clean-{label}-user",
        (
            make_tool,
            "--no-print-directory",
            "-rR",
            "-C",
            "user",
            "-f",
            "Makefile",
            "clean",
        ),
    )
    recorder.run(
        f"build-{label}-image",
        make_arguments(
            make_tool=make_tool,
            target="nfs/fs-copy.img",
            app_dir=app_dir,
            guest=guest,
            toolprefix=toolprefix,
            challenge=challenge,
        ),
    )
    recorder.run(
        f"build-{label}-kernel",
        make_arguments(
            make_tool=make_tool,
            target="build",
            app_dir=app_dir,
            guest=guest,
            toolprefix=toolprefix,
            challenge=challenge,
        ),
    )
    image_source = repo / "nfs" / "fs-copy.img"
    kernel_source = repo / "build" / "kernel"
    binary_source = repo / "user" / "target" / "bin" / guest
    if (
        not image_source.is_file()
        or not kernel_source.is_file()
        or not binary_source.is_file()
    ):
        raise CampaignError(f"build did not produce an image and kernel for {guest}")
    base_dir = scratch / "built-images" / label
    base_dir.mkdir(parents=True, exist_ok=False)
    image = base_dir / "fs.img"
    kernel = base_dir / "kernel"
    shutil.copy2(image_source, image)
    shutil.copy2(kernel_source, kernel)
    return {
        "guest": guest,
        "label": label,
        "challenge_hex": challenge,
        "image": image,
        "kernel": kernel,
        "image_sha256": sha256_file(image),
        "kernel_sha256": sha256_file(kernel),
        "guest_binary_sha256": sha256_file(binary_source),
        "image_size": image.stat().st_size,
        "kernel_size": kernel.stat().st_size,
        "guest_binary_size": binary_source.stat().st_size,
    }


def run_boot(
    recorder: CommandRecorder,
    repo: Path,
    scratch: Path,
    *,
    qemu: str,
    build: Mapping[str, Any],
    boot_label: str,
    serial_path: Path,
    case_timeout: str,
) -> None:
    boot_dir = scratch / "boot-images"
    boot_dir.mkdir(parents=True, exist_ok=True)
    boot_image = boot_dir / f"{boot_label}.img"
    if boot_image.exists():
        raise CampaignError(f"duplicate boot image: {boot_image}")
    shutil.copy2(Path(build["image"]), boot_image)
    guest = str(build["guest"])
    recorder.run(
        f"run-{boot_label}",
        (
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(repo / "scripts" / "agent_test_runner.py"),
            "--init-proc",
            guest,
            "--marker",
            f"{guest}: parent passed",
            "--marker-mode",
            "exact-line",
            "--log-file",
            str(serial_path),
            "--case-timeout",
            case_timeout,
            "--idle-notice-seconds",
            "15",
            "--marker-grace-seconds",
            "2s",
            "--kernel",
            str(build["kernel"]),
            "--image",
            str(boot_image),
            "--qemu",
            qemu,
        ),
    )
    if not serial_path.is_file() or serial_path.stat().st_size == 0:
        raise CampaignError(f"runner did not preserve serial output: {serial_path}")


def inventory(staging: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(staging.rglob("*")):
        if not path.is_file() or path.name in {"manifest.json", "COMPLETED"}:
            continue
        if path.suffix.lower() in {".log", ".out", ".pyc"} or "__pycache__" in path.parts:
            raise CampaignError(
                f"campaign contains a repository-ignored artifact: {path}"
            )
        rows.append(
            {
                "path": relative_posix(path, staging),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return rows


def normalize_contest_logs(staging: Path) -> list[Path]:
    contest_dir = staging / "raw" / "contest"
    normalized = sorted(contest_dir.glob("sample-*-qemu.serial.txt"))
    for source in sorted(contest_dir.glob("sample-*-qemu.log")):
        destination = source.with_name(
            source.name.removesuffix(".log") + ".serial.txt"
        )
        os.replace(source, destination)
        normalized.append(destination)
    return sorted(normalized)


def promote(staging: Path, output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise CampaignError(f"output appeared during the campaign: {output}")
    os.rename(staging, output)
    fsync_directory(output.parent)


def dry_run(plan: Mapping[str, Any], repo: Path, args: argparse.Namespace) -> None:
    canonical = validate_output_path(repo, plan)
    print(f"campaign: {plan['campaign_id']}")
    print(f"canonical output: {canonical}")
    print("writes: none (--dry-run)")
    print("1. reject CI and changes outside one_shot_metrics")
    print("2. scripts/run-contest-demo.sh with CONTEST_DEMO_SAMPLES=16")
    for hit in plan["collections"]["agenteval"]["hit_counts"]:
        challenge = plan["collections"]["agenteval"]["challenge_by_hit_count"][str(hit)]
        print(
            "3. prepare/build/run agenteval_ucore "
            f"hit_count={hit} challenge={challenge} boots=1"
        )
    print("4. build agenttask_ucore once; run 4 fresh boots from one pristine image")
    print("5. build agent_eevdf_ucore once; run 6 fresh boots from one pristine image")
    print("6. extract.py, validate.py, then plot.py (unless --skip-plots)")
    print("7. atomically publish manifest.json and COMPLETED, then rename campaign")
    if args.skip_plots:
        print("note: --skip-plots suppresses COMPLETED and publishes status=plots_skipped")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--plan-only", action="store_true", help="print plan.json and exit")
    modes.add_argument("--dry-run", action="store_true", help="describe execution without writes")
    parser.add_argument(
        "--acknowledge-one-shot",
        action="store_true",
        help="required to perform the irreversible canonical campaign",
    )
    parser.add_argument(
        "--skip-plots",
        action="store_true",
        help="collect and validate data but do not mark the campaign complete",
    )
    parser.add_argument("--qemu", default=os.environ.get("QEMU", "qemu-system-riscv64"))
    parser.add_argument("--make-tool", default=os.environ.get("MAKE_TOOL", "make"))
    parser.add_argument("--bash", default=os.environ.get("BASH_BIN", "bash"))
    parser.add_argument("--toolprefix", default=os.environ.get("TOOLPREFIX"))
    parser.add_argument(
        "--plot-python",
        default=os.environ.get("PLOT_PYTHON", sys.executable),
        help="Python interpreter used only for plot.py (may be Windows python.exe under WSL)",
    )
    parser.add_argument(
        "--internal-dev-override",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def run_campaign(
    args: argparse.Namespace,
    plan: dict[str, Any],
    repo: Path,
    script_dir: Path,
) -> int:
    if not args.acknowledge_one_shot:
        raise CampaignError("pass --acknowledge-one-shot to run the campaign")
    if os.name != "posix":
        raise CampaignError(
            "the campaign must run under POSIX Python; from Windows invoke it through WSL"
        )
    active_ci = [key for key in CI_VARIABLES if os.environ.get(key)]
    if active_ci:
        raise CampaignError(f"one-shot campaign refuses CI environment: {active_ci}")

    canonical = validate_output_path(repo, plan)
    reject_symlink_chain(repo, canonical.parent)
    completion_name = str(plan["completion_marker"])
    if not args.internal_dev_override and (
        canonical.exists()
        or canonical.is_symlink()
        or (canonical / completion_name).exists()
    ):
        raise CampaignError(f"canonical one-shot output already exists: {canonical}")

    git = git_metadata(repo, script_dir)
    make_tool = resolve_executable(args.make_tool, "make")
    qemu = resolve_executable(args.qemu, "QEMU")
    bash = resolve_executable(args.bash, "bash")
    plot_python = resolve_executable(args.plot_python, "plot Python")
    resolve_executable("git", "git")
    toolprefix = resolve_toolprefix(args.toolprefix)
    environment = environment_metadata(
        repo,
        make_tool=make_tool,
        qemu=qemu,
        bash=bash,
        toolprefix=toolprefix,
        plot_python=plot_python,
    )

    started_at = utc_now()
    compact_time = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{plan['campaign_id']}-{compact_time}-{git['source_commit'][:12]}"
    output = canonical
    if args.internal_dev_override:
        output = canonical.parent / f".dev-{compact_time}-{os.getpid()}"
        if output.exists() or output.is_symlink():
            raise CampaignError(f"internal development output already exists: {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink_chain(repo, output.parent)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.partial-", dir=output.parent)
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "agentos-advanced-figure-one-shot-campaign",
        "campaign_id": plan["campaign_id"],
        "run_id": run_id,
        "status": "running",
        "canonical": not args.internal_dev_override,
        "started_at_utc": started_at,
        "repository": git,
        "environment": environment,
        "plan_sha256": sha256_file(script_dir / "plan.json"),
        "commands": [],
        "builds": [],
    }

    published = False
    plot_failure: str | None = None
    try:
        for directory in (
            staging / "raw" / "contest",
            staging / "raw" / "serial",
            staging / "tables",
            staging / "figures",
        ):
            directory.mkdir(parents=True, exist_ok=False)
        frozen_scripts = copy_provenance_scripts(script_dir, staging)
        manifest["plan_sha256"] = sha256_file(frozen_scripts / "plan.json")

        with tempfile.TemporaryDirectory(prefix="agentos-one-shot-") as scratch_text:
            scratch = Path(scratch_text).resolve()
            extra_redactions: list[tuple[str, str]] = [
                (plot_python, "<plot-python>"),
            ]
            if is_windows_interop_executable(plot_python):
                extra_redactions.extend(
                    (
                        (windows_path(repo, repo), "<repo>"),
                        (windows_path(staging, repo), "<campaign>"),
                        (windows_path(scratch, repo), "<scratch>"),
                    )
                )
            recorder = CommandRecorder(
                repo,
                staging,
                scratch,
                extra_redactions=extra_redactions,
            )
            manifest["commands"] = recorder.records

            contest = plan["collections"]["contest"]
            recorder.run(
                "contest-demo-16",
                (bash, str(repo / contest["runner"])),
                env_updates={
                    "CONTEST_DEMO_OUTPUT": str(staging / "raw" / "contest"),
                    "CONTEST_DEMO_SAMPLES": str(contest["boots"]),
                    "CONTEST_DEMO_CASE_TIMEOUT": str(contest["case_timeout"]),
                    "TOOLPREFIX": toolprefix,
                    "QEMU": qemu,
                    "PYTHON_BIN": sys.executable,
                    "MAKE_TOOL": make_tool,
                },
            )
            contest_csv = staging / "raw" / "contest" / "measurements.csv"
            if not contest_csv.is_file():
                raise CampaignError("contest-demo did not produce measurements.csv")
            contest_logs = normalize_contest_logs(staging)
            if len(contest_logs) != int(contest["boots"]):
                raise CampaignError(
                    "contest-demo did not preserve the expected serial-log count"
                )

            eval_plan = plan["collections"]["agenteval"]
            prepared = prepare_guests(
                recorder,
                frozen_scripts,
                repo,
                scratch,
                staging,
                eval_plan["hit_counts"],
            )
            builds: list[dict[str, Any]] = []
            for hit_count in eval_plan["hit_counts"]:
                challenge = eval_plan["challenge_by_hit_count"][str(hit_count)]
                build = build_guest_image(
                    recorder,
                    repo,
                    scratch,
                    label=f"agenteval-hit-{hit_count:02d}",
                    guest=eval_plan["guest"],
                    app_dir=prepared[hit_count],
                    make_tool=make_tool,
                    toolprefix=toolprefix,
                    challenge=challenge,
                )
                builds.append({key: value for key, value in build.items() if key not in {"image", "kernel"}})
                run_boot(
                    recorder,
                    repo,
                    scratch,
                    qemu=qemu,
                    build=build,
                    boot_label=f"agenteval-hit-{hit_count:02d}-boot-01",
                    serial_path=staging
                    / "raw"
                    / "serial"
                    / f"agenteval-hit-{hit_count:02d}-boot-01.txt",
                    case_timeout=str(eval_plan["case_timeout"]),
                )

            neutral_challenge = eval_plan["challenge_by_hit_count"]["1"]
            task_plan = plan["collections"]["task"]
            task_build = build_guest_image(
                recorder,
                repo,
                scratch,
                label="agenttask",
                guest=task_plan["guest"],
                app_dir=prepared[1],
                make_tool=make_tool,
                toolprefix=toolprefix,
                challenge=neutral_challenge,
            )
            builds.append({key: value for key, value in task_build.items() if key not in {"image", "kernel"}})
            for boot in range(1, int(task_plan["boots"]) + 1):
                run_boot(
                    recorder,
                    repo,
                    scratch,
                    qemu=qemu,
                    build=task_build,
                    boot_label=f"agenttask-boot-{boot:02d}",
                    serial_path=staging / "raw" / "serial" / f"agenttask-boot-{boot:02d}.txt",
                    case_timeout=str(task_plan["case_timeout"]),
                )

            eevdf_plan = plan["collections"]["eevdf"]
            eevdf_build = build_guest_image(
                recorder,
                repo,
                scratch,
                label="agent-eevdf",
                guest=eevdf_plan["guest"],
                app_dir=prepared[1],
                make_tool=make_tool,
                toolprefix=toolprefix,
                challenge=neutral_challenge,
            )
            builds.append({key: value for key, value in eevdf_build.items() if key not in {"image", "kernel"}})
            for boot in range(1, int(eevdf_plan["boots"]) + 1):
                run_boot(
                    recorder,
                    repo,
                    scratch,
                    qemu=qemu,
                    build=eevdf_build,
                    boot_label=f"agent-eevdf-boot-{boot:02d}",
                    serial_path=staging / "raw" / "serial" / f"agent-eevdf-boot-{boot:02d}.txt",
                    case_timeout=str(eevdf_plan["case_timeout"]),
                )
            manifest["builds"] = builds

            pipeline = plan["pipeline"]
            recorder.run(
                "extract",
                (
                    sys.executable,
                    "-B",
                    str(frozen_scripts / Path(pipeline["extract"]).name),
                    "--serial",
                    str(staging / "raw" / "serial"),
                    "--contest-csv",
                    str(contest_csv),
                    "--output-dir",
                    str(staging / pipeline["tables_dir"]),
                    "--campaign-id",
                    str(plan["campaign_id"]),
                    "--source-root",
                    str(staging),
                ),
            )
            recorder.run(
                "validate",
                (
                    sys.executable,
                    "-B",
                    str(frozen_scripts / Path(pipeline["validate"]).name),
                    "--tables",
                    str(staging / pipeline["tables_dir"]),
                    "--output",
                    str(staging / pipeline["validation_report"]),
                ),
            )
            if not args.skip_plots:
                try:
                    recorder.run(
                        "plot",
                        (
                            plot_python,
                            "-B",
                            interpreter_path(
                                frozen_scripts / Path(pipeline["plot"]).name,
                                plot_python,
                                repo,
                            ),
                            "--tables",
                            interpreter_path(
                                staging / pipeline["tables_dir"],
                                plot_python,
                                repo,
                            ),
                            "--output-dir",
                            interpreter_path(
                                staging / pipeline["figures_dir"],
                                plot_python,
                                repo,
                            ),
                            "--format",
                            str(pipeline["plot_formats"]),
                        ),
                        env_updates={
                            "MPLCONFIGDIR": interpreter_path(
                                scratch / "matplotlib-config",
                                plot_python,
                                repo,
                            ),
                            "PYTHONUTF8": "1",
                            "PYTHONDONTWRITEBYTECODE": "1",
                        },
                    )
                except CommandFailure as error:
                    plot_failure = str(error)

        end_git = git_metadata(repo, script_dir)
        if end_git["source_commit"] != git["source_commit"]:
            raise CampaignError(
                "repository HEAD changed during the one-shot campaign; "
                "refusing to publish mixed-source measurements"
            )
        manifest["repository"]["verified_unchanged_at_utc"] = utc_now()
        manifest["commands"] = recorder.records
        manifest["finished_at_utc"] = utc_now()
        manifest["files"] = inventory(staging)
        if plot_failure is not None:
            manifest["status"] = "plot_failed"
            manifest["plot_error"] = plot_failure
        elif args.skip_plots:
            manifest["status"] = "plots_skipped"
        else:
            manifest["status"] = "completed"
        atomic_write_json(staging / "manifest.json", manifest)

        if manifest["status"] == "completed":
            manifest_digest = sha256_file(staging / "manifest.json")
            atomic_write_json(
                staging / completion_name,
                {
                    "schema_version": 1,
                    "campaign_id": plan["campaign_id"],
                    "run_id": run_id,
                    "source_commit": git["source_commit"],
                    "manifest_sha256": manifest_digest,
                    "completed_at_utc": manifest["finished_at_utc"],
                },
            )
        fsync_directory(staging)
        promote(staging, output)
        published = True
        print(f"[one-shot] published {output}")
        if plot_failure is not None:
            print(f"[one-shot] data preserved, but plotting failed: {plot_failure}", file=sys.stderr)
            return 3
        if args.skip_plots:
            print("[one-shot] plots skipped; COMPLETED was intentionally not written")
        return 0
    except BaseException as error:
        if staging.exists() and not published:
            try:
                normalize_contest_logs(staging)
                manifest["status"] = "failed"
                manifest["finished_at_utc"] = utc_now()
                manifest["error"] = f"{type(error).__name__}: {error}"
                manifest["files"] = inventory(staging)
                atomic_write_json(staging / "manifest.json", manifest)
                fsync_directory(staging)
                promote(staging, output)
                published = True
                print(f"[one-shot] partial data preserved at {output}", file=sys.stderr)
            except BaseException as preserve_error:
                print(
                    f"[one-shot] could not publish partial data; staging remains at {staging}: "
                    f"{preserve_error}",
                    file=sys.stderr,
                )
        raise


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    try:
        repo = discover_repo_root(script_dir)
        plan = load_plan(script_dir)
        if args.plan_only:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        if args.dry_run:
            dry_run(plan, repo, args)
            return 0
        return run_campaign(args, plan, repo, script_dir)
    except CampaignError as error:
        print(f"[one-shot] error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
