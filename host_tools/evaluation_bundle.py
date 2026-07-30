#!/usr/bin/env python3
"""Create and portably verify an immutable AgentOS evaluation evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from evaluation_campaign import (
    CampaignError,
    export_run_plan,
    validate_campaign,
    validate_scenario_campaign,
)
from evaluation_contract import EvaluationError, verify as verify_contract
from evaluation_kernel_cost import (
    KernelCostError,
    build_dashboard_fragment as build_kernel_cost_fragment,
    verify_portable as verify_kernel_cost,
)
from render_evaluation_dashboard import DashboardError, render as render_dashboard


KIND = "agentos-evaluation-evidence-bundle"
SCHEMA_VERSION = 2
FORMAL_PROFILE = "formal"
DEVELOPMENT_PROFILE = "development"
PROFILES = {FORMAL_PROFILE, DEVELOPMENT_PROFILE}
DEVELOPMENT_WARNING = (
    "DEVELOPMENT EVIDENCE ONLY: this bundle may omit the research scenario and "
    "must not be presented as formal competition evidence."
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
MAX_FILES = 20000
MAX_FILE_BYTES = 1 << 30
MAX_TOTAL_BYTES = 32 << 30
FIXED_RUN_FILES = {
    "campaign.json",
    "run-plan.json",
    "metrics.jsonl",
    "summary.json",
    "preflight.log",
}
OPTIONAL_RUN_FILES = {
    "scenario-preflight.log",
}
KERNEL_COST_FILES = {
    "kernel-cost-config.json",
    "kernel-cost-report.json",
    "kernel-cost-fragment.json",
    "kernel-build/environment.json",
    "kernel-build/kernel-build-config.json",
    "kernel-build/kernel-build.json",
    "kernel-build/raw/kernel-build.log",
}
FIXED_DASHBOARD_FILES = {
    "dashboard/index.html",
    "dashboard/evaluation-summary.json",
    "dashboard/dashboard-verification.json",
    "dashboard/metrics.csv",
    "dashboard/assets/evaluation-dashboard.css",
    "dashboard/assets/evaluation-dashboard.js",
}


class BundleError(ValueError):
    """Raised when an evaluation package is incomplete or has been modified."""


def _is_link(path: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return path.is_symlink() or bool(isjunction and isjunction(path))


def _reject_link_chain(path: Path, label: str) -> None:
    """Reject a link at the leaf or at any existing lexical ancestor."""
    current = path.absolute()
    while True:
        if _is_link(current):
            raise BundleError(f"{label} is link-backed: {current}")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _strict_json(path: Path) -> dict[str, Any]:
    _reject_link_chain(path, "JSON file")
    if _is_link(path) or not path.is_file():
        raise BundleError(f"missing or unsafe JSON file: {path}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                BundleError(f"non-finite JSON number {value!r}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BundleError(f"invalid JSON file {path}: {error}") from error
    if not isinstance(value, dict):
        raise BundleError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _binding_sha256(value: object) -> str:
    return hashlib.sha256(b"agentos-evaluation-bundle-v1\0" + _canonical_bytes(value)).hexdigest()


def _safe_relative(value: str, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value) is not None
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise BundleError(f"{label} is not a canonical relative path: {value!r}")
    return path


def _regular_files(root: Path) -> list[Path]:
    _reject_link_chain(root, "bundle tree")
    files: list[Path] = []
    total = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in [*dirnames, *filenames]:
            candidate = base / name
            if _is_link(candidate):
                raise BundleError(f"bundle tree contains a symlink: {candidate}")
        for name in filenames:
            candidate = base / name
            if not candidate.is_file():
                raise BundleError(f"bundle tree contains a non-file: {candidate}")
            size = candidate.stat().st_size
            if size > MAX_FILE_BYTES:
                raise BundleError(f"bundle artifact exceeds size limit: {candidate}")
            total += size
            if total > MAX_TOTAL_BYTES:
                raise BundleError("bundle payload exceeds total size limit")
            files.append(candidate)
            if len(files) > MAX_FILES:
                raise BundleError("bundle payload contains too many files")
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _validate_source_inventory(
    run_dir: Path,
    *,
    profile: str,
    micro_paths: set[str],
    scenario_paths: set[str],
    packaged: bool = False,
) -> list[Path]:
    files = _regular_files(run_dir)
    relative = {path.relative_to(run_dir).as_posix() for path in files}
    missing = (FIXED_RUN_FILES | FIXED_DASHBOARD_FILES) - relative
    if missing:
        raise BundleError(f"evaluation run lacks required artifacts: {sorted(missing)}")
    cost_present = relative & KERNEL_COST_FILES
    if cost_present and cost_present != KERNEL_COST_FILES:
        raise BundleError(
            f"kernel-cost evidence is incomplete: {sorted(KERNEL_COST_FILES - cost_present)}"
        )
    scenario_required = {
        "scenario-preflight.log",
        "scenario/scenario-plan.json",
        "scenario/report.json",
    }
    if profile == FORMAL_PROFILE:
        missing_scenario = scenario_required - relative
        if missing_scenario:
            raise BundleError(
                "formal evidence lacks required scenario artifacts: "
                f"{sorted(missing_scenario)}"
            )
        if (run_dir / "scenario-preflight.log").stat().st_size == 0:
            raise BundleError("formal scenario preflight log is empty")
    allowed = (
        FIXED_RUN_FILES
        | FIXED_DASHBOARD_FILES
        | cost_present
        | micro_paths
        | scenario_paths
    )
    if packaged:
        allowed.add("suite.json")
    if scenario_paths:
        allowed |= {"scenario-preflight.log", "scenario/collector.log"}
    forbidden = sorted(relative - allowed)
    if forbidden:
        raise BundleError(f"evaluation run contains unpublishable artifacts: {forbidden}")
    return files


def _profile_record(profile: str) -> dict[str, object]:
    if profile not in PROFILES:
        raise BundleError(f"unsupported evidence profile: {profile!r}")
    if profile == FORMAL_PROFILE:
        return {"name": FORMAL_PROFILE, "formal": True, "warning": None}
    return {
        "name": DEVELOPMENT_PROFILE,
        "formal": False,
        "warning": DEVELOPMENT_WARNING,
    }


def _validate_profile_record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"name", "formal", "warning"}:
        raise BundleError("bundle profile record is invalid")
    expected = _profile_record(value.get("name") if isinstance(value.get("name"), str) else "")
    if value != expected:
        raise BundleError("bundle profile record differs from its declared profile")
    return expected


def _campaign_artifact_relative(
    value: object,
    expected: PurePosixPath,
    label: str,
    *,
    artifact_root: object,
) -> str:
    if not isinstance(value, str):
        raise BundleError(f"{label} is not a path")
    source = _safe_relative(value, label)
    if not isinstance(artifact_root, str):
        raise BundleError("campaign artifact root is not a path")
    canonical_root = _safe_relative(artifact_root, "campaign artifact root")
    canonical_source = canonical_root / expected
    if source != canonical_source:
        raise BundleError(
            f"{label} is not bound to canonical source "
            f"{canonical_source.as_posix()}"
        )
    return expected.as_posix()


def _artifact_record(
    run_root: Path,
    relative: str,
    *,
    artifact_id: str,
    expected_sha256: str | None = None,
    nonempty: bool = False,
) -> dict[str, object]:
    safe = _safe_relative(relative, "artifact path")
    path = run_root.joinpath(*safe.parts)
    _reject_link_chain(path, f"artifact {artifact_id}")
    if _is_link(path) or not path.is_file():
        raise BundleError(f"referenced artifact is missing or unsafe: {relative}")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES or (nonempty and size == 0):
        raise BundleError(f"referenced artifact has an invalid size: {relative}")
    digest = _sha256(path)
    if expected_sha256 is not None and digest != expected_sha256:
        raise BundleError(f"referenced artifact hash differs: {relative}")
    identity = artifact_id.split("/", 2)
    if len(identity) < 2 or identity[0] not in {"micro", "scenario"}:
        raise BundleError(f"artifact identity is invalid: {artifact_id}")
    boot_id = identity[1] if identity[1].startswith("boot-") else "campaign"
    kind = identity[2].split("/", 1)[0] if len(identity) == 3 else identity[1]
    return {
        "artifact_id": artifact_id,
        "boot_id": boot_id,
        "kind": kind,
        "status": "verified",
        "path": f"run/{safe.as_posix()}",
        "bytes": size,
        "sha256": digest,
    }


def _environment_sha256(campaign: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(campaign["environment"])).hexdigest()


def _verify_micro_campaign(
    run_root: Path,
    suite_path: Path,
) -> tuple[dict[str, Any], list[dict[str, object]], set[str]]:
    campaign_path = run_root / "campaign.json"
    campaign = _strict_json(campaign_path)
    try:
        validate_campaign(campaign)
    except CampaignError as error:
        raise BundleError(f"micro campaign is invalid: {error}") from error
    if campaign["phase"] != "collected":
        raise BundleError("micro campaign is not collected")
    suite_sha256 = _sha256(suite_path)
    if campaign["protocol"]["suite_sha256"] != suite_sha256:
        raise BundleError("micro campaign is not bound to the packaged suite")

    specs = (
        ("guest_log", "guest_log_sha256", "guest.log", "guest", False),
        ("runner_log", "runner_log_sha256", "runner.log", "runner", True),
        ("kernel_path", "kernel_sha256", "kernel", "kernel", True),
        ("image_input_path", "image_input_sha256", "fs.img", "image-input", True),
        ("image_final_path", "image_final_sha256", "fs-copy.img", "image-final", True),
    )
    receipts: list[dict[str, object]] = []
    expected_raw: set[str] = set()
    for boot in campaign["boots"]:
        boot_id = boot["boot_id"]
        for path_key, hash_key, filename, kind, nonempty in specs:
            expected = PurePosixPath("raw", boot_id, filename)
            relative = _campaign_artifact_relative(
                boot[path_key], expected, path_key,
                artifact_root=campaign["run"]["artifact_root"],
            )
            expected_raw.add(relative)
            receipts.append(
                _artifact_record(
                    run_root,
                    relative,
                    artifact_id=f"micro/{boot_id}/{kind}",
                    expected_sha256=boot[hash_key],
                    nonempty=nonempty,
                )
            )

    raw_root = run_root / "raw"
    actual_raw = {
        path.relative_to(run_root).as_posix()
        for path in _regular_files(raw_root)
    } if raw_root.is_dir() else set()
    if actual_raw != expected_raw:
        raise BundleError(
            "micro raw inventory differs from the collected campaign: "
            f"missing={sorted(expected_raw - actual_raw)} "
            f"extra={sorted(actual_raw - expected_raw)}"
        )

    plan_path = run_root / "run-plan.json"
    observed_plan = _strict_json(plan_path)
    with tempfile.TemporaryDirectory(prefix="agentos-run-plan-replay-") as temporary:
        replay_path = Path(temporary) / "run-plan.json"
        try:
            expected_plan = export_run_plan(campaign_path, replay_path)
        except CampaignError as error:
            raise BundleError(f"micro run plan cannot be replayed: {error}") from error
    if observed_plan != expected_plan:
        raise BundleError("run plan differs from the collected campaign")
    return campaign, sorted(receipts, key=lambda item: str(item["artifact_id"])), expected_raw


def _verify_scenario_campaign(
    run_root: Path,
    micro: dict[str, Any],
    *,
    profile: str,
) -> tuple[list[dict[str, object]], set[str]]:
    plan_path = run_root / "scenario" / "scenario-plan.json"
    report_path = run_root / "scenario" / "report.json"
    present = plan_path.exists() or plan_path.is_symlink() or report_path.exists() or report_path.is_symlink()
    if not present:
        if profile == FORMAL_PROFILE:
            raise BundleError("formal evidence requires a collected scenario campaign")
        return [], set()
    if not plan_path.is_file() or _is_link(plan_path) or not report_path.is_file() or _is_link(report_path):
        raise BundleError("scenario report and plan must be packaged together")

    scenario = _strict_json(plan_path)
    try:
        validate_scenario_campaign(scenario)
    except CampaignError as error:
        raise BundleError(f"scenario campaign is invalid: {error}") from error
    if scenario["phase"] != "collected" or scenario["report"]["status"] != "recorded":
        raise BundleError("scenario campaign is not collected")
    if (
        scenario["run"]["id"] != micro["run"]["id"]
        or scenario["run"]["commit"] != micro["run"]["commit"]
        or scenario["run"]["artifact_root"] != micro["run"]["artifact_root"]
        or scenario["run"]["environment_sha256"] != _environment_sha256(micro)
    ):
        raise BundleError("scenario campaign identity differs from the micro campaign")
    report_relative = _campaign_artifact_relative(
        scenario["report"]["path"], PurePosixPath("scenario", "report.json"),
        "scenario report path", artifact_root=scenario["run"]["artifact_root"],
    )
    receipts = [
        _artifact_record(
            run_root,
            "scenario/scenario-plan.json",
            artifact_id="scenario/plan",
            nonempty=True,
        ),
        _artifact_record(
            run_root,
            report_relative,
            artifact_id="scenario/report",
            expected_sha256=scenario["report"]["sha256"],
            nonempty=True,
        ),
    ]
    referenced: dict[str, tuple[str, str, bool]] = {}
    allowed_boots: set[str] = set()
    for boot in scenario["boots"]:
        boot_id = boot["boot_id"]
        work = PurePosixPath("scenario", "raw", boot_id)
        _campaign_artifact_relative(
            boot["work_dir"], work, "scenario work directory",
            artifact_root=scenario["run"]["artifact_root"],
        )
        allowed_boots.add(boot_id)
        for path_key, hash_key, filename, kind in (
            ("runner_log", "runner_log_sha256", "runner.log", "runner"),
            ("host_summary", "host_summary_sha256", "host-summary.json", "host-summary"),
        ):
            relative = _campaign_artifact_relative(
                boot[path_key], work / filename, f"scenario {path_key}",
                artifact_root=scenario["run"]["artifact_root"],
            )
            if relative in referenced:
                raise BundleError(f"scenario artifact is referenced more than once: {relative}")
            referenced[relative] = (f"scenario/{boot_id}/{kind}", boot[hash_key], True)

    raw_root = run_root / "scenario" / "raw"
    actual_raw = {
        path.relative_to(run_root).as_posix()
        for path in _regular_files(raw_root)
    } if raw_root.is_dir() else set()
    for relative in actual_raw:
        parts = _safe_relative(relative, "scenario raw path").parts
        if len(parts) < 4 or parts[:2] != ("scenario", "raw") or parts[2] not in allowed_boots:
            raise BundleError(f"scenario raw artifact is outside a planned boot: {relative}")
    missing = set(referenced) - actual_raw
    if missing:
        raise BundleError(f"scenario campaign lacks referenced raw artifacts: {sorted(missing)}")
    for relative in sorted(actual_raw):
        if relative in referenced:
            artifact_id, digest, nonempty = referenced[relative]
            receipts.append(
                _artifact_record(
                    run_root, relative, artifact_id=artifact_id,
                    expected_sha256=digest, nonempty=nonempty,
                )
            )
        else:
            boot_id = PurePosixPath(relative).parts[2]
            suffix = "/".join(PurePosixPath(relative).parts[3:])
            receipts.append(
                _artifact_record(
                    run_root, relative,
                    artifact_id=f"scenario/{boot_id}/raw/{suffix}",
                )
            )
    scenario_paths = actual_raw | {
        "scenario/scenario-plan.json",
        "scenario/report.json",
    }
    return sorted(receipts, key=lambda item: str(item["artifact_id"])), scenario_paths


def _contract_paths(run_root: Path) -> tuple[Path | None, Path | None]:
    scenario_report = run_root / "scenario" / "report.json"
    scenario_plan = run_root / "scenario" / "scenario-plan.json"
    if scenario_report.exists() != scenario_plan.exists():
        raise BundleError("scenario report and plan must be packaged together")
    if scenario_report.exists():
        return scenario_report, scenario_plan
    return None, None


def _verify_evaluation_contract(run_root: Path) -> dict[str, Any]:
    report, plan = _contract_paths(run_root)
    try:
        return verify_contract(
            run_root / "suite.json",
            run_root / "run-plan.json",
            run_root / "raw",
            run_root / "summary.json",
            run_root / "metrics.jsonl",
            report,
            plan,
        )
    except EvaluationError as error:
        raise BundleError(f"packaged evaluation contract failed: {error}") from error


def _compare_dashboard(run_root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="agentos-evaluation-dashboard-verify-") as temporary:
        replay = Path(temporary)
        try:
            render_dashboard(run_root / "summary.json", replay)
        except DashboardError as error:
            raise BundleError(f"packaged dashboard cannot be replayed: {error}") from error
        for relative in sorted(path.removeprefix("dashboard/") for path in FIXED_DASHBOARD_FILES):
            expected = run_root / "dashboard" / relative
            actual = replay / relative
            if not actual.is_file() or expected.read_bytes() != actual.read_bytes():
                raise BundleError(f"dashboard differs from deterministic replay: {relative}")


def _verify_kernel_cost(run_root: Path) -> None:
    report = run_root / "kernel-cost-report.json"
    present = [run_root / relative for relative in KERNEL_COST_FILES]
    if not any(path.exists() or path.is_symlink() for path in present):
        return
    if not all(path.is_file() and not path.is_symlink() for path in present):
        raise BundleError("kernel-cost evidence is incomplete or unsafe")
    config = run_root / "kernel-cost-config.json"
    fragment = run_root / "kernel-cost-fragment.json"
    try:
        verify_kernel_cost(report, config, run_root)
        expected = build_kernel_cost_fragment(report, config, run_root)
    except KernelCostError as error:
        raise BundleError(f"packaged kernel-cost evidence failed: {error}") from error
    if _strict_json(fragment) != expected:
        raise BundleError("kernel-cost Dashboard fragment differs from portable evidence")


def _verify_formal_summary(summary: dict[str, Any]) -> None:
    scenarios = summary.get("scenarios")
    if not isinstance(scenarios, list):
        raise BundleError("formal summary has no scenario results")
    by_task: dict[str, dict[str, Any]] = {}
    for item in scenarios:
        if not isinstance(item, dict) or not isinstance(item.get("task"), str):
            raise BundleError("formal summary contains an invalid task result")
        task = item["task"]
        if task in by_task:
            raise BundleError(f"formal summary contains duplicate {task} results")
        by_task[task] = item
    expected = {f"task{number}" for number in range(1, 7)}
    if set(by_task) != expected:
        raise BundleError("formal evidence requires exactly one Task 1-6 result")
    failed = sorted(
        task for task, item in by_task.items()
        if item.get("functional_status") != "pass"
    )
    if failed:
        raise BundleError(
            "formal evidence requires passing Task 1-6 functional acceptance: "
            + ", ".join(failed)
        )


def _manifest_body(
    *, run_id: str, source_commit: str, suite_sha256: str,
    campaign_sha256: str, summary_sha256: str,
    files: list[dict[str, object]], artifacts: list[dict[str, object]],
    profile: dict[str, object], generated_at: str,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "run_id": run_id,
        "source_commit": source_commit,
        "generated_at": generated_at,
        "payload_root": "run",
        "suite_sha256": suite_sha256,
        "campaign_sha256": campaign_sha256,
        "summary_sha256": summary_sha256,
        "profile": profile,
        "artifacts": artifacts,
        "files": files,
    }


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8", newline="\n")


def create_bundle(
    *,
    run_dir: Path,
    suite_path: Path,
    output: Path,
    profile: str = FORMAL_PROFILE,
) -> dict[str, Any]:
    profile_record = _profile_record(profile)
    lexical_run = run_dir.absolute()
    lexical_suite = suite_path.absolute()
    _reject_link_chain(lexical_run, "evaluation run directory")
    _reject_link_chain(lexical_suite, "evaluation suite")
    run_dir = lexical_run.resolve(strict=True)
    suite_path = lexical_suite.resolve(strict=True)
    output = output.absolute()
    if not run_dir.is_dir():
        raise BundleError("evaluation run directory is missing or unsafe")
    if not suite_path.is_file():
        raise BundleError("evaluation suite is missing or unsafe")
    if output.exists() or output.is_symlink():
        raise BundleError(f"refusing to replace an existing bundle: {output}")
    _reject_link_chain(output, "bundle output")
    try:
        output.relative_to(run_dir)
    except ValueError:
        pass
    else:
        raise BundleError("bundle output cannot be inside the source run")

    campaign, micro_receipts, micro_paths = _verify_micro_campaign(run_dir, suite_path)
    scenario_receipts, scenario_paths = _verify_scenario_campaign(
        run_dir, campaign, profile=profile
    )
    source_files = _validate_source_inventory(
        run_dir,
        profile=profile,
        micro_paths=micro_paths,
        scenario_paths=scenario_paths,
    )
    summary = _strict_json(run_dir / "summary.json")
    run = summary.get("run")
    if not isinstance(run, dict):
        raise BundleError("evaluation summary has no run identity")
    run_id = run.get("id")
    commit = run.get("commit")
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise BundleError("evaluation summary run id is invalid")
    if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None:
        raise BundleError("evaluation summary source commit is invalid")
    plan = _strict_json(run_dir / "run-plan.json")
    campaign_run = campaign.get("run")
    generated_at = (
        campaign_run.get("completed_at_utc")
        if isinstance(campaign_run, dict)
        else None
    )
    if not isinstance(generated_at, str) or not generated_at:
        raise BundleError("evaluation campaign completion time is invalid")
    campaign_sha256 = _sha256(run_dir / "campaign.json")
    if plan.get("campaign_sha256") != campaign_sha256:
        raise BundleError("run plan is not bound to the packaged campaign")
    suite_sha256 = _sha256(suite_path)
    if plan.get("suite_sha256") != suite_sha256:
        raise BundleError("run plan is not bound to the supplied suite")

    if run_id != campaign["run"]["id"] or commit != campaign["run"]["commit"]:
        raise BundleError("evaluation summary identity differs from the campaign")
    _reject_link_chain(output.parent, "bundle output parent")
    output.parent.mkdir(parents=True, exist_ok=True)
    _reject_link_chain(output.parent, "bundle output parent")
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        payload = stage / "run"
        payload.mkdir()
        for source in source_files:
            relative = source.relative_to(run_dir)
            destination = payload / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        shutil.copyfile(suite_path, payload / "suite.json")

        replay_campaign, replay_micro, _ = _verify_micro_campaign(payload, payload / "suite.json")
        replay_scenario, _ = _verify_scenario_campaign(
            payload, replay_campaign, profile=profile
        )
        if replay_micro + replay_scenario != micro_receipts + scenario_receipts:
            raise BundleError("copied campaign artifacts differ from their source receipts")
        verified_summary = _verify_evaluation_contract(payload)
        if profile == FORMAL_PROFILE:
            _verify_formal_summary(verified_summary)
        _verify_kernel_cost(payload)
        _compare_dashboard(payload)
        payload_files = _regular_files(payload)
        records = [
            {
                "path": f"run/{path.relative_to(payload).as_posix()}",
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in payload_files
        ]
        body = _manifest_body(
            run_id=run_id,
            source_commit=commit,
            generated_at=generated_at,
            suite_sha256=suite_sha256,
            campaign_sha256=campaign_sha256,
            summary_sha256=_sha256(payload / "summary.json"),
            profile=profile_record,
            artifacts=micro_receipts + scenario_receipts,
            files=records,
        )
        manifest = {**body, "binding_sha256": _binding_sha256(body)}
        _write_text(
            stage / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        )
        inventory_paths = [stage / "manifest.json", *payload_files]
        _write_text(
            stage / "checksums.sha256",
            "".join(
                f"{_sha256(path)}  {path.relative_to(stage).as_posix()}\n"
                for path in sorted(inventory_paths, key=lambda item: item.relative_to(stage).as_posix())
            ),
        )
        verify_bundle(stage)
        os.replace(stage, output)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def verify_bundle(root: Path) -> dict[str, Any]:
    lexical_root = root.absolute()
    _reject_link_chain(lexical_root, "bundle root")
    root = lexical_root.resolve(strict=True)
    if not root.is_dir():
        raise BundleError("bundle root is missing or unsafe")
    manifest = _strict_json(root / "manifest.json")
    expected_fields = {
        "schema_version", "kind", "run_id", "source_commit", "generated_at",
        "payload_root", "suite_sha256", "campaign_sha256", "summary_sha256",
        "profile", "artifacts", "files", "binding_sha256",
    }
    if set(manifest) != expected_fields:
        raise BundleError("bundle manifest fields differ")
    if manifest["schema_version"] != SCHEMA_VERSION or manifest["kind"] != KIND:
        raise BundleError("bundle manifest schema is unsupported")
    if manifest["payload_root"] != "run":
        raise BundleError("bundle payload root is invalid")
    profile = _validate_profile_record(manifest["profile"])
    body = {key: manifest[key] for key in expected_fields - {"binding_sha256"}}
    if manifest["binding_sha256"] != _binding_sha256(body):
        raise BundleError("bundle manifest binding differs")
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        raise BundleError("bundle manifest has no payload files")
    records: dict[str, dict[str, Any]] = {}
    for record in manifest["files"]:
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise BundleError("bundle file record is invalid")
        path = record["path"]
        if not isinstance(path, str) or _safe_relative(path, "bundle file path").parts[0] != "run":
            raise BundleError("bundle file path is outside the payload")
        if path in records:
            raise BundleError(f"duplicate bundle file record: {path}")
        if type(record["bytes"]) is not int or record["bytes"] < 0:
            raise BundleError(f"invalid bundle file size: {path}")
        if not isinstance(record["sha256"], str) or SHA256_RE.fullmatch(record["sha256"]) is None:
            raise BundleError(f"invalid bundle file hash: {path}")
        records[path] = record

    if not isinstance(manifest["artifacts"], list) or not manifest["artifacts"]:
        raise BundleError("bundle manifest has no campaign artifact receipts")
    artifact_ids: set[str] = set()
    artifact_paths: set[str] = set()
    for artifact in manifest["artifacts"]:
        if not isinstance(artifact, dict) or set(artifact) != {
            "artifact_id", "boot_id", "kind", "status",
            "path", "bytes", "sha256",
        }:
            raise BundleError("bundle campaign artifact receipt is invalid")
        artifact_id = artifact["artifact_id"]
        path = artifact["path"]
        if (
            not isinstance(artifact_id, str)
            or not artifact_id
            or artifact_id in artifact_ids
            or not isinstance(artifact["boot_id"], str)
            or not artifact["boot_id"]
            or not isinstance(artifact["kind"], str)
            or not artifact["kind"]
            or artifact["status"] != "verified"
            or not isinstance(path, str)
            or _safe_relative(path, "campaign artifact path").parts[0] != "run"
            or path in artifact_paths
            or type(artifact["bytes"]) is not int
            or artifact["bytes"] < 0
            or not isinstance(artifact["sha256"], str)
            or SHA256_RE.fullmatch(artifact["sha256"]) is None
        ):
            raise BundleError("bundle campaign artifact receipt is invalid")
        artifact_ids.add(artifact_id)
        artifact_paths.add(path)
        file_record = records.get(path)
        if file_record is None or any(
            artifact[key] != file_record[key] for key in ("path", "bytes", "sha256")
        ):
            raise BundleError(f"campaign artifact receipt differs from file inventory: {path}")

    actual_files = _regular_files(root)
    actual_relative = {path.relative_to(root).as_posix() for path in actual_files}
    expected_relative = set(records) | {"manifest.json", "checksums.sha256"}
    if actual_relative != expected_relative:
        raise BundleError(
            f"bundle inventory differs: missing={sorted(expected_relative - actual_relative)} "
            f"extra={sorted(actual_relative - expected_relative)}"
        )
    for relative, record in records.items():
        path = root / relative
        if path.stat().st_size != record["bytes"] or _sha256(path) != record["sha256"]:
            raise BundleError(f"bundle payload differs: {relative}")

    checksum_lines = (root / "checksums.sha256").read_text(encoding="ascii").splitlines()
    expected_checksum_paths = sorted(set(records) | {"manifest.json"})
    observed_checksum_paths: list[str] = []
    for line in checksum_lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise BundleError("checksum inventory line is invalid")
        relative = match.group(2)
        _safe_relative(relative, "checksum path")
        if relative in observed_checksum_paths or _sha256(root / relative) != match.group(1):
            raise BundleError(f"checksum inventory differs: {relative}")
        observed_checksum_paths.append(relative)
    if observed_checksum_paths != expected_checksum_paths:
        raise BundleError("checksum inventory paths differ")

    payload = root / "run"
    plan = _strict_json(payload / "run-plan.json")
    if _sha256(payload / "suite.json") != manifest["suite_sha256"]:
        raise BundleError("packaged suite differs from manifest")
    if _sha256(payload / "campaign.json") != manifest["campaign_sha256"]:
        raise BundleError("packaged campaign differs from manifest")
    if plan.get("campaign_sha256") != manifest["campaign_sha256"]:
        raise BundleError("packaged run plan differs from campaign")
    if _sha256(payload / "summary.json") != manifest["summary_sha256"]:
        raise BundleError("packaged summary differs from manifest")
    campaign, micro_receipts, micro_paths = _verify_micro_campaign(
        payload, payload / "suite.json"
    )
    scenario_receipts, scenario_paths = _verify_scenario_campaign(
        payload, campaign, profile=str(profile["name"])
    )
    if manifest["artifacts"] != micro_receipts + scenario_receipts:
        raise BundleError("campaign artifact receipts differ from packaged raw evidence")
    _validate_source_inventory(
        payload,
        profile=str(profile["name"]),
        micro_paths=micro_paths,
        scenario_paths=scenario_paths,
        packaged=True,
    )
    summary = _verify_evaluation_contract(payload)
    if profile["name"] == FORMAL_PROFILE:
        _verify_formal_summary(summary)
    _verify_kernel_cost(payload)
    _compare_dashboard(payload)
    if summary["run"]["id"] != manifest["run_id"] or summary["run"]["commit"] != manifest["source_commit"]:
        raise BundleError("packaged summary identity differs from manifest")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--run-dir", type=Path, required=True)
    create.add_argument("--suite", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default=FORMAL_PROFILE,
        help="formal is the default; development must be selected explicitly",
    )
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_bundle(
                run_dir=args.run_dir,
                suite_path=args.suite,
                output=args.output,
                profile=args.profile,
            )
            if result["profile"]["name"] == DEVELOPMENT_PROFILE:
                print(f"WARNING: {result['profile']['warning']}", file=__import__("sys").stderr)
            print(f"evaluation_bundle: created run={result['run_id']} output={args.output}")
        else:
            result = verify_bundle(args.bundle)
            if result["profile"]["name"] == DEVELOPMENT_PROFILE:
                print(f"WARNING: {result['profile']['warning']}", file=__import__("sys").stderr)
            print(f"evaluation_bundle: verified run={result['run_id']} commit={result['source_commit']}")
    except BundleError as error:
        raise SystemExit(f"evaluation bundle failed: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
