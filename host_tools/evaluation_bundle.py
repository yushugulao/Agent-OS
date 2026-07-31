#!/usr/bin/env python3
"""Create and portably verify an immutable AgentOS evaluation evidence bundle."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

from evaluation_campaign import (
    CampaignError,
    export_run_plan,
    validate_campaign,
    validate_scenario_campaign,
)
from evaluation_contract import (
    EvaluationError,
    derive_acceptance_gates,
    verify as verify_contract,
)
from evaluation_kernel_cost import (
    KernelCostError,
    build_dashboard_fragment as build_kernel_cost_fragment,
    require_complete as require_complete_kernel_cost,
    verify_portable as verify_kernel_cost,
)
from agenteval_measurement_source_contract import (
    EVALUATION_SUITE_SOURCE_PATH,
    validate_measurement_source_receipt_shape,
    verify_measurement_source_receipt,
)
from evidence_delivery_contract import (
    DeliveryContractError,
    controlled_git_environment,
    make_manifest_binding,
    publish_bundle_and_index,
    validate_delivery_field,
    verify_historical_committed_delivery,
)
from render_evaluation_dashboard import DashboardError, render as render_dashboard
from compatibility_overhead import (
    CompatibilityRunError,
    verify_campaign_artifacts as verify_compatibility_artifacts,
)
from compatibility_overhead_contract import CompatibilityContractError
from full_verification_payload import (
    FullVerificationError,
    verify_payload as verify_full_verification_payload,
)
from evaluation_scenario import (
    ScenarioEvidenceError,
    read_expected_programs,
    validate_task6_artifact_provenance,
)

try:
    from .plain_ucore_fs_extract import extract_state_files
    from .research_state_manifest import StateManifestError, load_manifest
    from .safe_host_paths import (
        absolute_lexical_path,
        atomic_write_bytes,
        ensure_safe_directory,
        path_is_link,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
        walk_regular_files_no_links,
    )
except ImportError:
    from plain_ucore_fs_extract import extract_state_files
    from research_state_manifest import StateManifestError, load_manifest
    from safe_host_paths import (
        absolute_lexical_path,
        atomic_write_bytes,
        ensure_safe_directory,
        path_is_link,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
        walk_regular_files_no_links,
    )


KIND = "agentos-evaluation-evidence-bundle"
SCHEMA_VERSION = 5
FORMAL_PROFILE = "formal"
DEVELOPMENT_PROFILE = "development"
PROFILES = {FORMAL_PROFILE, DEVELOPMENT_PROFILE}
DEVELOPMENT_WARNING = (
    "DEVELOPMENT EVIDENCE ONLY: this bundle may omit the research scenario and "
    "must not be presented as formal competition evidence."
)
DEVELOPMENT_FULL_VERIFICATION = {
    "status": "unavailable",
    "reason": "development-profile",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
# Source/replay limits cover the complete logical evidence.  Stored delivery
# limits are deliberately much smaller and are independently rechecked from
# the committed Git tree by evidence_delivery_contract.py.
MAX_FILES = 20000
MAX_FILE_BYTES = 1 << 30
MAX_TOTAL_BYTES = 1 << 30
MAX_STORED_FILES = 1000
MAX_STORED_FILE_BYTES = 64 << 20
MAX_STORED_TOTAL_BYTES = 256 << 20
MAX_CONTROL_FILE_BYTES = 16 << 20
MAX_ARCHIVES = 64
MAX_ARCHIVE_MEMBERS = 2048
MAX_ARCHIVE_DEPTH = 12
MAX_ARCHIVE_MEMBER_BYTES = 64 << 20
MAX_ARCHIVE_RAW_BYTES = 1 << 30
MAX_COMPRESSION_RATIO = 4096
MIN_RATIO_STORED_BYTES = 1024
ARCHIVE_FORMAT = "gzip+ustar-v1"
ARCHIVE_MODE = 0o644
SOURCE_TREE_ROOT = Path(__file__).resolve().parents[1]
MEASUREMENT_SOURCE_SNAPSHOT_ROOT = "measurement-sources"
FIXED_RUN_FILES = {
    "campaign.json",
    "measurement-source-receipt.json",
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


def _compression_record() -> dict[str, object]:
    return {
        "schema": ARCHIVE_FORMAT,
        "stream": "gzip",
        "container": "ustar",
        "compresslevel": 9,
        "gzip_filename": "",
        "gzip_mtime": 0,
        "gzip_os": 255,
        "member_order": "lexicographic",
        "member_mtime": 0,
        "member_uid": 0,
        "member_gid": 0,
        "member_mode": ARCHIVE_MODE,
        "ratio_stored_floor_bytes": MIN_RATIO_STORED_BYTES,
    }


def _reject_link_chain(path: Path, label: str) -> None:
    """Reject a link at the leaf or at any existing lexical ancestor."""
    try:
        reject_link_components(path)
    except (OSError, ValueError) as error:
        raise BundleError(f"{label} is link-backed: {path}") from error


def _safe_directory(path: Path, label: str) -> Path:
    try:
        return require_safe_directory(path)
    except (OSError, ValueError) as error:
        raise BundleError(f"{label} is missing or link-backed: {path}") from error


def _safe_regular_file(path: Path, label: str) -> Path:
    try:
        return require_regular_file(path)
    except (OSError, ValueError) as error:
        raise BundleError(f"{label} is missing or link-backed: {path}") from error


def _ensure_directory(path: Path, label: str) -> Path:
    try:
        return ensure_safe_directory(path)
    except (OSError, ValueError) as error:
        raise BundleError(f"{label} is link-backed: {path}") from error


def _new_file_path(path: Path, label: str) -> Path:
    """Return a missing file path below a verified ordinary directory."""
    absolute = absolute_lexical_path(path)
    _ensure_directory(absolute.parent, f"{label} parent")
    try:
        absolute.lstat()
    except FileNotFoundError:
        return absolute
    raise BundleError(f"{label} already exists or is unsafe: {absolute}")


def _copy_regular_file(source: Path, destination: Path, label: str) -> None:
    source = _safe_regular_file(source, f"{label} source")
    destination = _new_file_path(destination, f"{label} destination")
    try:
        shutil.copyfile(source, destination)
    except OSError as error:
        raise BundleError(f"cannot copy {label}: {error}") from error


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _strict_json(path: Path) -> dict[str, Any]:
    path = _safe_regular_file(path, "JSON file")
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
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        or re.match(r"^[A-Za-z]:", value) is not None
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise BundleError(f"{label} is not a canonical relative path: {value!r}")
    return path


def _regular_files(root: Path) -> list[Path]:
    try:
        files = walk_regular_files_no_links(
            root,
            max_files=MAX_FILES,
            max_directories=MAX_FILES + 1,
            max_total_bytes=MAX_TOTAL_BYTES,
        )
    except (OSError, ValueError) as error:
        raise BundleError(f"bundle tree is unsafe or exceeds its budget: {root}") from error
    for candidate in files:
        if candidate.stat().st_size > MAX_FILE_BYTES:
            raise BundleError(f"bundle artifact exceeds size limit: {candidate}")
    return files


def _stored_files(root: Path) -> list[Path]:
    """Inventory a delivery tree using the public, Git-delivery budgets."""
    files = _regular_files(root)
    total = 0
    for path in files:
        size = path.stat().st_size
        if size > MAX_STORED_FILE_BYTES:
            raise BundleError(f"stored bundle file exceeds 64 MiB: {path}")
        total += size
        if total > MAX_STORED_TOTAL_BYTES:
            raise BundleError("stored bundle exceeds 256 MiB")
    if len(files) > MAX_STORED_FILES:
        raise BundleError("stored bundle contains more than 1000 files")
    return files


class _HashingReader:
    def __init__(self, handle: Any) -> None:
        self.handle = handle
        self.digest = hashlib.sha256()
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        data = self.handle.read(size)
        self.digest.update(data)
        self.count += len(data)
        return data


def _write_canonical_ustar(
    source_root: Path, logical_paths: list[str], output: Any
) -> list[dict[str, object]]:
    """Write the compressor-independent canonical USTAR member stream."""
    if not logical_paths or logical_paths != sorted(set(logical_paths)):
        raise BundleError("archive member inventory is empty, duplicated, or unsorted")
    members: list[dict[str, object]] = []
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for relative in logical_paths:
            safe = _safe_relative(relative, "archive logical member")
            source = source_root.joinpath(*safe.parts)
            source = _safe_regular_file(source, "archive source member")
            size = source.stat().st_size
            if size > MAX_ARCHIVE_MEMBER_BYTES:
                raise BundleError(f"archive member exceeds size limit: {relative}")
            info = tarfile.TarInfo(safe.as_posix())
            info.size = size
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = ARCHIVE_MODE
            info.type = tarfile.REGTYPE
            with source.open("rb") as handle:
                reader = _HashingReader(handle)
                archive.addfile(info, reader)
            if reader.count != size:
                raise BundleError(f"archive source changed while read: {relative}")
            members.append({
                "path": f"run/{safe.as_posix()}",
                "raw_bytes": size,
                "raw_sha256": reader.digest.hexdigest(),
            })
    return members


def _write_deterministic_archive(
    source_root: Path,
    logical_paths: list[str],
    destination: Path,
) -> list[dict[str, object]]:
    """Write a canonical-header gzip containing canonical USTAR members."""
    destination = _new_file_path(destination, "archive output")
    with destination.open("xb") as raw:
        with gzip.GzipFile(
            filename="", mode="wb", compresslevel=9, fileobj=raw, mtime=0
        ) as compressed:
            return _write_canonical_ustar(source_root, logical_paths, compressed)


def _make_archive_record(
    source_root: Path,
    logical_paths: list[str],
    destination: Path,
    *,
    archive_id: str,
    stored_path: str,
) -> dict[str, object]:
    members = _write_deterministic_archive(source_root, logical_paths, destination)
    stored_bytes = destination.stat().st_size
    raw_total = sum(int(member["raw_bytes"]) for member in members)
    if raw_total > MAX_ARCHIVE_RAW_BYTES:
        raise BundleError(f"archive expands beyond its raw-byte budget: {archive_id}")
    return {
        "archive_id": archive_id,
        "stored_path": stored_path,
        "stored_bytes": stored_bytes,
        "stored_sha256": _sha256(destination),
        "raw_total_bytes": raw_total,
        "stored_total_bytes": stored_bytes,
        "member_count": len(members),
        "compression": _compression_record(),
        "members": members,
    }


def _archive_specs(
    micro_paths: set[str], scenario_paths: set[str]
) -> list[tuple[str, str, list[str]]]:
    groups: dict[tuple[str, ...], list[str]] = {}
    for relative in sorted(micro_paths):
        parts = _safe_relative(relative, "micro archive source").parts
        if len(parts) != 3 or parts[0] != "raw" or not parts[1].startswith("boot-"):
            raise BundleError(f"micro artifact cannot be sharded canonically: {relative}")
        groups.setdefault(("micro", parts[1]), []).append(relative)
    for relative in sorted(scenario_paths):
        parts = _safe_relative(relative, "scenario archive source").parts
        if len(parts) < 4 or parts[:2] != ("scenario", "raw"):
            continue
        if len(parts) == 4:
            groups.setdefault(("scenario", parts[2], "control"), []).append(relative)
        elif parts[3] in {"plain", "agentos"}:
            groups.setdefault(("scenario", parts[2], parts[3]), []).append(relative)
        else:
            raise BundleError(f"scenario artifact cannot be sharded canonically: {relative}")
    specs: list[tuple[str, str, list[str]]] = []
    for key, members in sorted(groups.items()):
        if key[0] == "micro":
            archive_id = "/".join(key)
            stored = f"run/archives/micro/{key[1]}.tar.gz"
        else:
            archive_id = "/".join(key)
            stored = f"run/archives/scenario/{key[1]}/{key[2]}.tar.gz"
        specs.append((archive_id, stored, sorted(members)))
    return specs


def _archive_summary(archives: list[dict[str, object]]) -> dict[str, object]:
    return {
        "compression": _compression_record(),
        "raw_total_bytes": sum(int(item["raw_total_bytes"]) for item in archives),
        "stored_total_bytes": sum(int(item["stored_total_bytes"]) for item in archives),
        "member_count": sum(int(item["member_count"]) for item in archives),
        "archive_count": len(archives),
    }


def _dashboard_evidence_inventory(run_dir: Path) -> set[str]:
    """Return and verify the loose evidence copies used by offline links."""
    summary = _strict_json(run_dir / "summary.json")
    evidence = summary.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise BundleError("evaluation summary has no Dashboard evidence inventory")
    expected: dict[str, str] = {}
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise BundleError(f"summary evidence[{index}] is invalid")
        reference = item.get("path")
        digest = item.get("sha256")
        if not isinstance(reference, str) or not isinstance(digest, str):
            raise BundleError(f"summary evidence[{index}] has no path/hash binding")
        safe = _safe_relative(reference, f"summary evidence[{index}].path")
        if SHA256_RE.fullmatch(digest) is None:
            raise BundleError(f"summary evidence[{index}] has an invalid hash")
        previous = expected.get(safe.as_posix())
        if previous is not None and previous != digest:
            raise BundleError("Dashboard evidence path has conflicting hashes")
        expected[safe.as_posix()] = digest

    dashboard_paths: set[str] = set()
    for reference, digest in expected.items():
        safe = _safe_relative(reference, "Dashboard evidence path")
        source = _safe_regular_file(
            run_dir.joinpath(*safe.parts), "Dashboard evidence source"
        )
        relative = f"dashboard/evidence/{safe.as_posix()}"
        portable = _safe_regular_file(
            run_dir.joinpath(*_safe_relative(relative, "portable Dashboard evidence").parts),
            "portable Dashboard evidence",
        )
        if _sha256(source) != digest or _sha256(portable) != digest:
            raise BundleError(
                f"portable Dashboard evidence differs from its summary binding: {reference}"
            )
        dashboard_paths.add(relative)
    return dashboard_paths


def _validate_source_inventory(
    run_dir: Path,
    *,
    profile: str,
    micro_paths: set[str],
    scenario_paths: set[str],
    compatibility_paths: set[str],
    full_verification_paths: set[str],
    packaged: bool = False,
    measurement_source_paths: set[str] | None = None,
) -> list[Path]:
    files = _regular_files(run_dir)
    relative = {path.relative_to(run_dir).as_posix() for path in files}
    dashboard_evidence = _dashboard_evidence_inventory(run_dir)
    missing = (FIXED_RUN_FILES | FIXED_DASHBOARD_FILES | dashboard_evidence) - relative
    if missing:
        raise BundleError(f"evaluation run lacks required artifacts: {sorted(missing)}")
    cost_present = relative & KERNEL_COST_FILES
    if cost_present and cost_present != KERNEL_COST_FILES:
        raise BundleError(
            f"kernel-cost evidence is incomplete: {sorted(KERNEL_COST_FILES - cost_present)}"
        )
    if profile == FORMAL_PROFILE and cost_present != KERNEL_COST_FILES:
        raise BundleError(
            "formal evidence requires the complete kernel-cost sidecar"
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
        | dashboard_evidence
        | cost_present
        | micro_paths
        | scenario_paths
        | compatibility_paths
        | full_verification_paths
    )
    if packaged:
        allowed.add("suite.json")
        allowed |= measurement_source_paths or set()
    elif measurement_source_paths:
        raise BundleError("source snapshots may only appear in a packaged payload")
    if scenario_paths:
        allowed |= {"scenario-preflight.log", "scenario/collector.log"}
    forbidden = sorted(relative - allowed)
    if forbidden:
        raise BundleError(f"evaluation run contains unpublishable artifacts: {forbidden}")
    return files


def _verify_full_verification(
    run_dir: Path, *, expected_commit: str, profile: str, contract_root: Path
) -> tuple[dict[str, object], set[str]]:
    """Replay the detached-C full-verify stage and expose its exact inventory."""

    root = run_dir / "full-verification"
    present = root.exists() or path_is_link(root)
    if profile == DEVELOPMENT_PROFILE:
        if present:
            raise BundleError(
                "development evidence must declare full-verification unavailable"
            )
        return dict(DEVELOPMENT_FULL_VERIFICATION), set()
    if not present:
        raise BundleError("formal evidence requires a full-verification payload")
    try:
        binding, nested_paths = verify_full_verification_payload(
            root, expected_commit=expected_commit, contract_root=contract_root
        )
    except (FullVerificationError, OSError, ValueError) as error:
        raise BundleError(f"full-verification payload failed: {error}") from error
    paths = {f"full-verification/{path}" for path in nested_paths}
    if not paths:
        raise BundleError("full-verification payload inventory is empty")
    return binding, paths


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
    path = _safe_regular_file(path, f"artifact {artifact_id}")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES or (nonempty and size == 0):
        raise BundleError(f"referenced artifact has an invalid size: {relative}")
    digest = _sha256(path)
    if expected_sha256 is not None and digest != expected_sha256:
        raise BundleError(f"referenced artifact hash differs: {relative}")
    identity = artifact_id.split("/", 2)
    if len(identity) < 2 or identity[0] not in {"campaign", "micro", "scenario"}:
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


def _verify_measurement_source_receipt(
    run_root: Path,
    campaign: dict[str, Any],
    plan: dict[str, Any],
    source_commit: str,
    *,
    source_tree: Path,
    suite_path: Path,
) -> tuple[dict[str, object], dict[str, Any]]:
    """Bind the receipt and replay its source contracts from a local snapshot."""
    relative = "measurement-source-receipt.json"
    try:
        receipt = _strict_json(run_root / relative)
    except BundleError as error:
        raise BundleError(
            "measurement source receipt is missing or unsafe"
        ) from error
    try:
        validate_measurement_source_receipt_shape(
            receipt, expected_commit=source_commit
        )
    except ValueError as error:
        raise BundleError(f"measurement source receipt is invalid: {error}") from error
    if (
        campaign.get("measurement_source_receipt") != receipt
        or plan.get("measurement_source_receipt") != receipt
        or plan.get("stop_rule") != receipt.get("stop_rule")
    ):
        raise BundleError(
            "measurement source receipt differs from the campaign or run plan"
        )
    try:
        verify_measurement_source_receipt(
            receipt,
            _safe_directory(source_tree, "measurement source tree"),
            expected_commit=source_commit,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise BundleError(
            f"measurement source snapshot cannot replay its contracts: {error}"
        ) from error
    suite_records = [
        record
        for record in receipt["sources"]
        if record["path"] == EVALUATION_SUITE_SOURCE_PATH
    ]
    suite_path = _safe_regular_file(suite_path, "evaluation suite")
    if len(suite_records) != 1 or (
        suite_path.stat().st_size != suite_records[0]["bytes"]
        or _sha256(suite_path) != suite_records[0]["sha256"]
    ):
        raise BundleError(
            "evaluation suite differs from the versioned source policy inventory"
        )
    return (
        _artifact_record(
            run_root,
            relative,
            artifact_id="campaign/measurement-source-receipt",
            nonempty=True,
        ),
        receipt,
    )


def _measurement_source_snapshot_paths(receipt: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for index, record in enumerate(receipt["sources"]):
        source = _safe_relative(
            record["path"], f"measurement source receipt sources[{index}].path"
        )
        relative = PurePosixPath(MEASUREMENT_SOURCE_SNAPSHOT_ROOT, *source.parts)
        paths.add(relative.as_posix())
    if len(paths) != len(receipt["sources"]):
        raise BundleError("measurement source receipt repeats a snapshot path")
    return paths


def _copy_measurement_source_snapshot(
    source_tree: Path, payload: Path, receipt: dict[str, Any]
) -> set[str]:
    source_tree = _safe_directory(source_tree, "measurement source tree")
    paths = _measurement_source_snapshot_paths(receipt)
    for record in receipt["sources"]:
        source = _safe_relative(record["path"], "measurement source path")
        relative = PurePosixPath(MEASUREMENT_SOURCE_SNAPSHOT_ROOT, *source.parts)
        _copy_regular_file(
            source_tree.joinpath(*source.parts),
            payload.joinpath(*relative.parts),
            "measurement source snapshot",
        )
    return paths


def _git_capture(repo: Path, *arguments: str) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise BundleError("Git is required to authenticate measurement sources")
    completed = subprocess.run(
        [executable, "-C", str(repo), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=controlled_git_environment(),
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise BundleError(f"Git source-C verification failed: {detail}")
    return completed.stdout


def _verify_committed_measurement_sources(
    repo: Path, source_commit: str, receipt: dict[str, Any]
) -> None:
    """Authenticate receipt hashes against blobs in the immutable source C."""
    repo = _safe_directory(absolute_lexical_path(repo), "source repository")
    if COMMIT_RE.fullmatch(source_commit) is None:
        raise BundleError("measurement source commit is invalid")
    resolved = _git_capture(repo, "rev-parse", "--verify", f"{source_commit}^{{commit}}")
    if resolved.decode("ascii", errors="strict").strip() != source_commit:
        raise BundleError("measurement source commit does not resolve exactly")
    for index, record in enumerate(receipt["sources"]):
        source = _safe_relative(
            record["path"], f"measurement source receipt sources[{index}].path"
        ).as_posix()
        object_name = f"{source_commit}:{source}"
        size_raw = _git_capture(repo, "cat-file", "-s", object_name)
        try:
            size = int(size_raw.decode("ascii").strip())
        except (UnicodeError, ValueError) as error:
            raise BundleError(f"Git source-C size is invalid: {source}") from error
        if size != record["bytes"] or size > MAX_FILE_BYTES:
            raise BundleError(f"Git source-C size differs from receipt: {source}")
        data = _git_capture(repo, "cat-file", "blob", object_name)
        if len(data) != size or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise BundleError(f"Git source-C blob differs from receipt: {source}")


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


def _verify_compatibility_campaign(
    run_root: Path,
    micro_campaign: dict[str, Any],
    *,
    required: bool,
) -> tuple[list[dict[str, object]], set[str]]:
    compatibility_root = run_root / "compatibility"
    summary_path = compatibility_root / "compatibility-overhead.json"
    if not compatibility_root.exists():
        if required:
            raise BundleError("formal evidence lacks compatibility-overhead artifacts")
        return [], set()
    try:
        require_safe_directory(compatibility_root)
        value = verify_compatibility_artifacts(
            summary_path, micro_manifest=run_root / "campaign.json"
        )
    except (
        CompatibilityContractError,
        CompatibilityRunError,
        OSError,
        ValueError,
    ) as error:
        raise BundleError(f"compatibility-overhead evidence is invalid: {error}") from error
    context = value.get("formal_context")
    run = micro_campaign.get("run")
    if (
        value.get("formal_bundle_eligible") is not True
        or not isinstance(context, dict)
        or not isinstance(run, dict)
        or context.get("source_commit") != run.get("commit")
        or context.get("micro_run_id") != run.get("id")
    ):
        raise BundleError("compatibility-overhead formal identity differs")
    paths = {
        path.relative_to(run_root).as_posix()
        for path in _regular_files(compatibility_root)
    }
    if "compatibility/compatibility-overhead.json" not in paths:
        raise BundleError("compatibility-overhead summary is unavailable")
    receipt = _artifact_record(
        run_root,
        "compatibility/compatibility-overhead.json",
        artifact_id="campaign/compatibility-overhead",
        nonempty=True,
    )
    return [receipt], paths


def _scenario_target_inventory(
    report: dict[str, Any], boot_id: str, target: str
) -> dict[str, tuple[int, str]]:
    """Return the exact target files explicitly bound by a scenario receipt."""
    samples = report.get("samples")
    if not isinstance(samples, list):
        raise BundleError("scenario report has no sample inventory")
    matches = [
        item for item in samples
        if isinstance(item, dict)
        and isinstance(item.get("binding"), dict)
        and item["binding"].get("boot_id") == boot_id
    ]
    if len(matches) != 1:
        raise BundleError(f"scenario report does not bind exactly one {boot_id} sample")
    targets = matches[0].get("targets")
    if not isinstance(targets, dict) or not isinstance(targets.get(target), dict):
        raise BundleError(f"scenario report lacks {boot_id}/{target} evidence")
    receipt = targets[target].get("raw_source_receipt")
    if not isinstance(receipt, dict):
        raise BundleError(f"scenario report lacks {boot_id}/{target} raw receipt")

    inventory: dict[str, tuple[int, str]] = {}

    def add(record: object, expected: str, label: str) -> None:
        if not isinstance(record, dict):
            raise BundleError(f"scenario {label} receipt is invalid")
        path = record.get("path")
        size = record.get("bytes")
        digest = record.get("sha256")
        if (
            path != expected
            or type(size) is not int
            or size < 0
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
        ):
            raise BundleError(f"scenario {label} receipt is invalid")
        existing = inventory.get(expected)
        value = (size, digest)
        if existing is not None and existing != value:
            raise BundleError(f"scenario receipts disagree for {boot_id}/{target}/{expected}")
        inventory[expected] = value

    state = receipt.get("state_inventory")
    if not isinstance(state, dict) or not isinstance(state.get("files"), list):
        raise BundleError(f"scenario {boot_id}/{target} state inventory is invalid")
    add(
        state.get("extract_summary"),
        "state-extracted/extract-summary.json",
        "extract summary",
    )
    for entry in state["files"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise BundleError(f"scenario {boot_id}/{target} state entry is invalid")
        name = _safe_relative(entry["path"], "scenario state member")
        if len(name.parts) != 1:
            raise BundleError("scenario state member is not a canonical file name")
        canonical = f"state-extracted/{name.as_posix()}"
        add({**entry, "path": canonical}, canonical, "state member")
    add(receipt.get("qemu_log"), "ucore-run.log", "QEMU log")
    add(receipt.get("run_summary"), "ucore-run-summary.json", "run summary")
    runtime = receipt.get("runtime_artifacts")
    if not isinstance(runtime, dict) or set(runtime) != {
        "kernel", "image_input", "image_final"
    }:
        raise BundleError(f"scenario {boot_id}/{target} runtime inventory is invalid")
    for name in sorted(runtime):
        add(runtime[name], f"artifacts/{name}", f"runtime {name}")
    challenge = receipt.get("challenge_source")
    if not isinstance(challenge, dict):
        raise BundleError(f"scenario {boot_id}/{target} challenge receipt is invalid")
    add(challenge.get("actions"), "actions.json", "challenge actions")
    add(
        challenge.get("receipt"),
        "challenge-input-receipt.json",
        "challenge input",
    )
    sealed = receipt.get("sealed_inventory")
    if not isinstance(sealed, dict) or set(sealed) != {
        "schema", "files", "file_count", "sha256"
    }:
        raise BundleError(f"scenario {boot_id}/{target} has no sealed inventory")
    files = sealed.get("files")
    if (
        sealed.get("schema") != "scenario-sealed-inventory-v1"
        or not isinstance(files, list)
        or type(sealed.get("file_count")) is not int
        or sealed["file_count"] != len(files)
    ):
        raise BundleError(f"scenario {boot_id}/{target} sealed inventory is invalid")
    body = {
        "schema": sealed["schema"],
        "files": files,
        "file_count": sealed["file_count"],
    }
    expected_binding = hashlib.sha256(
        b"scenario-sealed-inventory-v1\0" + _canonical_bytes(body)
    ).hexdigest()
    if sealed.get("sha256") != expected_binding:
        raise BundleError(f"scenario {boot_id}/{target} sealed inventory hash differs")
    state_names = {
        path.removeprefix("state-extracted/")
        for path in inventory
        if path.startswith("state-extracted/")
        and path != "state-extracted/extract-summary.json"
    }
    permitted = set(inventory) | {
        "runner-summary.json",
        "ucore-build.log",
        "host-input/rp_host_action_seed",
        "state-next/rp_host_run_result",
        *{f"state-next/{name}" for name in state_names},
    }
    sealed_inventory: dict[str, tuple[int, str]] = {}
    paths: list[str] = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256"}:
            raise BundleError("scenario sealed member receipt is invalid")
        path = entry["path"]
        if not isinstance(path, str) or path not in permitted:
            raise BundleError(f"scenario sealed inventory contains an unknown path: {path!r}")
        if (
            type(entry["bytes"]) is not int
            or entry["bytes"] < 0
            or not isinstance(entry["sha256"], str)
            or SHA256_RE.fullmatch(entry["sha256"]) is None
        ):
            raise BundleError("scenario sealed member receipt is invalid")
        paths.append(path)
        sealed_inventory[path] = (entry["bytes"], entry["sha256"])
    if paths != sorted(set(paths)) or not set(inventory).issubset(sealed_inventory):
        raise BundleError("scenario sealed inventory is incomplete or non-canonical")
    if any(sealed_inventory[path] != value for path, value in inventory.items()):
        raise BundleError("scenario sealed inventory differs from semantic receipts")
    return sealed_inventory


def _same_file_bytes(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_handle, right.open("rb") as right_handle:
        while True:
            left_chunk = left_handle.read(1024 * 1024)
            right_chunk = right_handle.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _verify_scenario_image_state(
    run_root: Path,
    boot_id: str,
    target: str,
    *,
    host_state_names: set[str],
) -> None:
    target_root = run_root / "scenario" / "raw" / boot_id / target
    packaged_state = _safe_directory(
        target_root / "state-extracted",
        f"scenario {boot_id}/{target} extracted state",
    )
    final_image = _safe_regular_file(
        target_root / "artifacts" / "image_final",
        f"scenario {boot_id}/{target} final filesystem image",
    )
    packaged_files = {
        path.relative_to(packaged_state).as_posix(): path
        for path in _regular_files(packaged_state)
    }
    summary_name = "extract-summary.json"
    expected_state_names = sorted(set(packaged_files) - {summary_name})
    if (
        summary_name not in packaged_files
        or not expected_state_names
        or any("/" in name for name in expected_state_names)
    ):
        raise BundleError(
            f"scenario {boot_id}/{target} packaged state inventory is invalid"
        )
    forbidden_host_state = sorted(set(expected_state_names) & host_state_names)
    if forbidden_host_state:
        raise BundleError(
            f"scenario {boot_id}/{target} packaged Guest state contains Host files: "
            f"{forbidden_host_state}"
        )

    with tempfile.TemporaryDirectory(
        prefix=f"agentos-scenario-state-{boot_id}-{target}-"
    ) as temporary:
        replay_state = Path(temporary)
        try:
            os.chmod(replay_state, 0o700)
        except OSError:
            pass
        try:
            extract_state_files(
                final_image,
                replay_state,
                require_single_scope=True,
                expected_state_names=expected_state_names,
                excluded_state_names=host_state_names,
            )
        except (OSError, ValueError) as error:
            raise BundleError(
                f"scenario {boot_id}/{target} final filesystem cannot be re-extracted: {error}"
            ) from error

        packaged = packaged_files
        replayed = {
            path.relative_to(replay_state).as_posix(): path
            for path in _regular_files(replay_state)
        }
        if set(packaged) != set(replayed):
            raise BundleError(
                f"scenario {boot_id}/{target} final filesystem state inventory differs: "
                f"missing={sorted(set(replayed) - set(packaged))} "
                f"extra={sorted(set(packaged) - set(replayed))}"
            )

        if summary_name not in packaged:
            raise BundleError(
                f"scenario {boot_id}/{target} final filesystem lacks an extract summary"
            )
        packaged_summary = dict(_strict_json(packaged[summary_name]))
        replayed_summary = dict(_strict_json(replayed[summary_name]))
        packaged_image = packaged_summary.pop("image", None)
        replayed_image = replayed_summary.pop("image", None)
        if (
            not isinstance(packaged_image, str)
            or not packaged_image
            or not isinstance(replayed_image, str)
            or not replayed_image
            or _canonical_bytes(packaged_summary)
            != _canonical_bytes(replayed_summary)
        ):
            raise BundleError(
                f"scenario {boot_id}/{target} final filesystem extract summary differs"
            )

        for relative in sorted(set(packaged) - {summary_name}):
            if not _same_file_bytes(packaged[relative], replayed[relative]):
                raise BundleError(
                    f"scenario {boot_id}/{target} final filesystem state bytes differ: "
                    f"{relative}"
                )


def _verify_scenario_campaign(
    run_root: Path,
    micro: dict[str, Any],
    *,
    profile: str,
    source_tree: Path = SOURCE_TREE_ROOT,
) -> tuple[list[dict[str, object]], set[str]]:
    plan_path = run_root / "scenario" / "scenario-plan.json"
    report_path = run_root / "scenario" / "report.json"
    present = any(path.exists() or path_is_link(path) for path in (plan_path, report_path))
    if not present:
        if profile == FORMAL_PROFILE:
            raise BundleError("formal evidence requires a collected scenario campaign")
        return [], set()
    try:
        _safe_regular_file(plan_path, "scenario plan")
        _safe_regular_file(report_path, "scenario report")
    except BundleError as error:
        raise BundleError("scenario report and plan must be packaged together") from error

    try:
        source_tree = _safe_directory(
            source_tree, "scenario extraction source tree"
        )
        run_identity = micro.get("run")
        source_commit = (
            run_identity.get("commit") if isinstance(run_identity, dict) else None
        )
        verify_measurement_source_receipt(
            micro.get("measurement_source_receipt"),
            source_tree,
            expected_commit=source_commit if isinstance(source_commit, str) else None,
        )
        state_manifest = load_manifest(source_tree)
    except (OSError, UnicodeError, ValueError, StateManifestError) as error:
        raise BundleError(
            f"scenario source-C semantic replay failed: {error}"
        ) from error
    host_state_names = set(state_manifest.host_state_files)

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
        or scenario["platform"] != micro["platform"]
        or scenario["run"]["platform_sha256"]
        != hashlib.sha256(_canonical_bytes(micro["platform"])).hexdigest()
        or scenario["measurement_source_receipt"]
        != micro["measurement_source_receipt"]
    ):
        raise BundleError("scenario campaign identity differs from the micro campaign")
    report_relative = _campaign_artifact_relative(
        scenario["report"]["path"], PurePosixPath("scenario", "report.json"),
        "scenario report path", artifact_root=scenario["run"]["artifact_root"],
    )
    report = _strict_json(report_path)
    try:
        expected_programs, _expected_roles = read_expected_programs(source_tree)
        samples = report.get("samples") if isinstance(report, dict) else None
        if not isinstance(samples, list):
            raise ScenarioEvidenceError("scenario report samples are invalid")
        corpus_path = (
            source_tree / "evaluation_guest" / "fixtures" / "task6-count-corpus.csv"
        )
        for index, sample in enumerate(samples):
            binding = sample.get("binding") if isinstance(sample, dict) else None
            targets = sample.get("targets") if isinstance(sample, dict) else None
            if (
                not isinstance(binding, dict)
                or binding.get("program_order") != list(expected_programs)
                or not isinstance(binding.get("challenge"), str)
                or not isinstance(targets, dict)
            ):
                raise ScenarioEvidenceError(
                    f"scenario sample {index} differs from source-C program manifests"
                )
            for target in ("plain", "agentos"):
                target_record = targets.get(target)
                raw_receipt = (
                    target_record.get("raw_source_receipt")
                    if isinstance(target_record, dict)
                    else None
                )
                provenance = (
                    raw_receipt.get("artifact_provenance")
                    if isinstance(raw_receipt, dict)
                    else None
                )
                validate_task6_artifact_provenance(
                    provenance, binding["challenge"], corpus_path
                )
    except (OSError, UnicodeError, ValueError, ScenarioEvidenceError) as error:
        raise BundleError(
            f"scenario source-C semantic replay failed: {error}"
        ) from error
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
    referenced: dict[str, tuple[str, str, bool, int | None]] = {}
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
            referenced[relative] = (
                f"scenario/{boot_id}/{kind}", boot[hash_key], True, None
            )
        for target in ("plain", "agentos"):
            target_inventory = _scenario_target_inventory(report, boot_id, target)
            image_size, image_digest = target_inventory.get(
                "artifacts/image_final", (None, None)
            )
            if (
                type(image_size) is not int
                or image_size <= 0
                or image_size > MAX_ARCHIVE_MEMBER_BYTES
                or not isinstance(image_digest, str)
            ):
                raise BundleError(
                    f"scenario {boot_id}/{target} final filesystem receipt exceeds its budget"
                )
            image_relative = (work / target / "artifacts/image_final").as_posix()
            image_record = _artifact_record(
                run_root,
                image_relative,
                artifact_id=f"scenario/{boot_id}/{target}/image-final-preflight",
                expected_sha256=image_digest,
                nonempty=True,
            )
            if image_record["bytes"] != image_size:
                raise BundleError(
                    f"scenario {boot_id}/{target} final filesystem size differs"
                )
            _verify_scenario_image_state(
                run_root,
                boot_id,
                target,
                host_state_names=host_state_names,
            )
            for target_relative, (size, digest) in sorted(target_inventory.items()):
                relative = (work / target / target_relative).as_posix()
                if relative in referenced:
                    raise BundleError(
                        f"scenario artifact is referenced more than once: {relative}"
                    )
                referenced[relative] = (
                    f"scenario/{boot_id}/{target}/{target_relative}",
                    digest,
                    size > 0,
                    size,
                )

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
    extra = actual_raw - set(referenced)
    if extra:
        raise BundleError(
            "scenario raw inventory contains files not explicitly bound by the plan/report: "
            f"{sorted(extra)}"
        )
    for relative in sorted(actual_raw):
        artifact_id, digest, nonempty, expected_size = referenced[relative]
        record = _artifact_record(
            run_root, relative, artifact_id=artifact_id,
            expected_sha256=digest, nonempty=nonempty,
        )
        if expected_size is not None and record["bytes"] != expected_size:
            raise BundleError(f"scenario artifact size differs: {relative}")
        receipts.append(record)
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


def _verify_evaluation_contract(
    run_root: Path, suite_path: Path | None = None
) -> dict[str, Any]:
    report, plan = _contract_paths(run_root)
    try:
        return verify_contract(
            suite_path if suite_path is not None else run_root / "suite.json",
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
        expected_root = _safe_directory(run_root / "dashboard", "packaged Dashboard")
        expected_files = {
            path.relative_to(expected_root).as_posix(): path
            for path in _regular_files(expected_root)
        }
        actual_files = {
            path.relative_to(replay).as_posix(): path
            for path in _regular_files(replay)
        }
        if set(expected_files) != set(actual_files):
            raise BundleError("Dashboard inventory differs from deterministic replay")
        for relative in sorted(expected_files):
            if expected_files[relative].read_bytes() != actual_files[relative].read_bytes():
                raise BundleError(f"dashboard differs from deterministic replay: {relative}")


def _verify_kernel_cost(run_root: Path, *, require_complete: bool = False) -> None:
    report = run_root / "kernel-cost-report.json"
    present = [run_root / relative for relative in KERNEL_COST_FILES]
    if not any(path.exists() or path_is_link(path) for path in present):
        if require_complete:
            raise BundleError("formal evidence requires complete kernel-cost evidence")
        return
    try:
        for path in present:
            _safe_regular_file(path, "kernel-cost evidence")
    except BundleError as error:
        raise BundleError("kernel-cost evidence is incomplete or unsafe") from error
    config = run_root / "kernel-cost-config.json"
    fragment = run_root / "kernel-cost-fragment.json"
    try:
        verified_report, _environment, _build = verify_kernel_cost(
            report, config, run_root
        )
        if require_complete:
            require_complete_kernel_cost(verified_report)
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
    if by_task["task6"].get("performance_status") not in {
        "supported", "regressed", "inconclusive"
    } or not isinstance(by_task["task6"].get("performance"), dict) or not by_task[
        "task6"
    ]["performance"]:
        raise BundleError(
            "formal evidence requires a measured Task 6 performance conclusion"
        )
    claims = summary.get("claims")
    if not isinstance(claims, list):
        raise BundleError("formal summary has no performance claims")
    methodology = summary.get("methodology")
    competition_claims = (
        methodology.get("competition_claims")
        if isinstance(methodology, dict)
        else None
    )
    if not isinstance(competition_claims, dict):
        raise BundleError("formal summary lacks competition claim registration")
    task4_registration = competition_claims.get("task4")
    if not isinstance(task4_registration, dict):
        raise BundleError("formal summary lacks the Task 4 competition claim")
    task4_benchmark_id = task4_registration.get("benchmark_id")
    if (
        not isinstance(task4_benchmark_id, str)
        or not task4_benchmark_id
        or task4_registration.get("required_status") != "supported"
    ):
        raise BundleError("formal summary has an invalid Task 4 competition claim")
    task4_claims = [
        item for item in claims
        if isinstance(item, dict)
        and item.get("benchmark_id") == task4_benchmark_id
    ]
    if len(task4_claims) != 1:
        raise BundleError(
            "formal evidence requires exactly one registered Task 4 claim"
        )
    if task4_claims[0].get("status") not in {"supported", "not_supported"}:
        raise BundleError(
            "formal evidence requires a measured registered Task 4 claim; "
            "not_supported is publishable but unavailable is incomplete"
        )
    try:
        expected_acceptance = derive_acceptance_gates(
            scenarios, claims, competition_claims
        )
    except EvaluationError as error:
        raise BundleError(f"formal competition claim registration is invalid: {error}") from error
    if summary.get("acceptance") != expected_acceptance:
        raise BundleError(
            "formal summary acceptance gates differ from the evidence-derived gates"
        )
    if expected_acceptance["scientific_evidence"]["status"] != "publishable":
        raise BundleError("formal scientific evidence is incomplete")
    regressed_tasks = {
        task
        for task, item in by_task.items()
        if item.get("performance_status") == "regressed"
    }
    if regressed_tasks and (
        expected_acceptance["competition_ready"]
        or any(
            expected_acceptance["tasks"].get(task) != "not_ready"
            for task in regressed_tasks
        )
    ):
        raise BundleError(
            "formal scenario regressions must remain publishable negative evidence "
            "and cannot be competition-ready"
        )
    # A negative result for the preregistered Task 4 claim is a complete
    # scientific result.  It remains publishable but cannot pass the rubric.
    expected_task4 = (
        "pass"
        if task4_claims[0]["status"] == "supported"
        else "not_ready"
    )
    if expected_acceptance["tasks"]["task4"] != expected_task4:
        raise BundleError("Task 4 competition gate is inconsistent")


def _archive_members(record: object) -> list[dict[str, object]]:
    expected_fields = {
        "archive_id", "stored_path", "stored_bytes", "stored_sha256",
        "raw_total_bytes", "stored_total_bytes", "member_count",
        "compression", "members",
    }
    if not isinstance(record, dict) or set(record) != expected_fields:
        raise BundleError("archive manifest record is invalid")
    archive_id = record["archive_id"]
    stored_path = record["stored_path"]
    archive_parts = (
        _safe_relative(archive_id, "archive id").parts
        if isinstance(archive_id, str)
        else ()
    )
    if (
        not isinstance(archive_id, str)
        or (
            len(archive_parts) == 2
            and not (
                archive_parts[0] == "micro"
                and archive_parts[1].startswith("boot-")
            )
        )
        or (
            len(archive_parts) == 3
            and not (
                archive_parts[0] == "scenario"
                and archive_parts[1].startswith("boot-")
                and archive_parts[2] in {"control", "plain", "agentos"}
            )
        )
        or len(archive_parts) not in {2, 3}
        or not isinstance(stored_path, str)
        or _safe_relative(stored_path, "archive stored path").parts[:2]
        != ("run", "archives")
        or not stored_path.endswith(".tar.gz")
        or type(record["stored_bytes"]) is not int
        or record["stored_bytes"] <= 0
        or record["stored_bytes"] > MAX_STORED_FILE_BYTES
        or record["stored_total_bytes"] != record["stored_bytes"]
        or not isinstance(record["stored_sha256"], str)
        or SHA256_RE.fullmatch(record["stored_sha256"]) is None
        or record["compression"] != _compression_record()
        or type(record["member_count"]) is not int
        or not (1 <= record["member_count"] <= MAX_ARCHIVE_MEMBERS)
        or type(record["raw_total_bytes"]) is not int
        or not (0 <= record["raw_total_bytes"] <= MAX_ARCHIVE_RAW_BYTES)
        or not isinstance(record["members"], list)
        or len(record["members"]) != record["member_count"]
    ):
        raise BundleError("archive manifest record is invalid")
    expected_stored_path = (
        f"run/archives/micro/{archive_parts[1]}.tar.gz"
        if archive_parts[0] == "micro"
        else "run/archives/scenario/{}/{}.tar.gz".format(
            archive_parts[1], archive_parts[2]
        )
    )
    if stored_path != expected_stored_path:
        raise BundleError("archive stored path differs from its shard identity")
    members: list[dict[str, object]] = []
    paths: list[str] = []
    raw_total = 0
    for member in record["members"]:
        if not isinstance(member, dict) or set(member) != {
            "path", "raw_bytes", "raw_sha256"
        }:
            raise BundleError("archive member receipt is invalid")
        path = member["path"]
        if not isinstance(path, str):
            raise BundleError("archive member path is invalid")
        safe = _safe_relative(path, "archive member path")
        if archive_parts[0] == "micro":
            expected_prefix = ("run", "raw", archive_parts[1])
        elif archive_parts[2] == "control":
            expected_prefix = ("run", "scenario", "raw", archive_parts[1])
        else:
            expected_prefix = (
                "run", "scenario", "raw", archive_parts[1], archive_parts[2]
            )
        if (
            safe.parts[0] != "run"
            or safe.parts[1:2] not in {("raw",), ("scenario",)}
            or (safe.parts[1] == "scenario" and safe.parts[1:3] != ("scenario", "raw"))
            or (
                safe.parts[1:3] == ("scenario", "raw")
                and len(safe.parts) < 5
            )
            or (
                safe.parts[1] == "raw" and len(safe.parts) != 4
            )
            or safe.parts[:len(expected_prefix)] != expected_prefix
            or (
                archive_parts[0] == "scenario"
                and archive_parts[2] == "control"
                and len(safe.parts) != len(expected_prefix) + 1
            )
            or len(safe.parts) - 1 > MAX_ARCHIVE_DEPTH
            or type(member["raw_bytes"]) is not int
            or not (0 <= member["raw_bytes"] <= MAX_ARCHIVE_MEMBER_BYTES)
            or not isinstance(member["raw_sha256"], str)
            or SHA256_RE.fullmatch(member["raw_sha256"]) is None
        ):
            raise BundleError(f"archive member receipt is invalid: {path}")
        paths.append(path)
        raw_total += member["raw_bytes"]
        members.append(member)
    if paths != sorted(set(paths)) or raw_total != record["raw_total_bytes"]:
        raise BundleError("archive member inventory is not canonical")
    ratio_base = max(record["stored_bytes"], MIN_RATIO_STORED_BYTES)
    if raw_total > ratio_base * MAX_COMPRESSION_RATIO:
        raise BundleError("archive compression ratio exceeds the expansion budget")
    return members


def _files_equal(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as one, right.open("rb") as two:
        while True:
            first = one.read(1024 * 1024)
            second = two.read(1024 * 1024)
            if first != second:
                return False
            if not first:
                return True


def _canonical_ustar_size(members: list[dict[str, object]]) -> int:
    blocks = 2
    for member in members:
        size = int(member["raw_bytes"])
        blocks += 1 + (size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE
    unpadded = blocks * tarfile.BLOCKSIZE
    return (
        (unpadded + tarfile.RECORDSIZE - 1) // tarfile.RECORDSIZE
    ) * tarfile.RECORDSIZE


def _decompress_single_gzip(
    archive_path: Path, output_path: Path, expected_bytes: int
) -> None:
    """Decode exactly one bounded gzip member without accepting concatenation."""
    decoder = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    count = 0
    with archive_path.open("rb") as raw, output_path.open("xb") as output:
        while not decoder.eof:
            chunk = raw.read(64 * 1024)
            if not chunk:
                break
            pending = chunk
            while pending and not decoder.eof:
                remaining = expected_bytes - count + 1
                if remaining <= 0:
                    raise BundleError(
                        "archive expands beyond its canonical USTAR size"
                    )
                data = decoder.decompress(pending, min(1024 * 1024, remaining))
                next_pending = decoder.unconsumed_tail
                if data:
                    count += len(data)
                    if count > expected_bytes:
                        raise BundleError(
                            "archive expands beyond its canonical USTAR size"
                        )
                    output.write(data)
                if decoder.eof:
                    if decoder.unused_data or raw.read(1):
                        raise BundleError(
                            "archive must contain exactly one canonical gzip member"
                        )
                    break
                if next_pending == pending and not data:
                    raise BundleError("archive gzip decoder made no progress")
                pending = next_pending
        if not decoder.eof:
            raise BundleError("archive gzip member is truncated")
        flushed = decoder.flush()
        if flushed:
            count += len(flushed)
            if count > expected_bytes:
                raise BundleError("archive expands beyond its canonical USTAR size")
            output.write(flushed)
    if count != expected_bytes:
        raise BundleError("archive USTAR stream has a non-canonical size")


def _extract_archive(
    archive_path: Path,
    destination_root: Path,
    record: dict[str, object],
    expansion: dict[str, int] | None = None,
) -> None:
    """Safely materialize one untrusted shard and prove canonical USTAR bytes."""
    members = _archive_members(record)
    try:
        archive_path = _safe_regular_file(archive_path, "stored archive")
    except BundleError as error:
        raise BundleError(f"archive stored bytes differ: {record['stored_path']}") from error
    if (
        archive_path.stat().st_size != record["stored_bytes"]
        or _sha256(archive_path) != record["stored_sha256"]
    ):
        raise BundleError(f"archive stored bytes differ: {record['stored_path']}")
    with archive_path.open("rb") as header_file:
        header = header_file.read(10)
    if (
        len(header) != 10
        or header[:4] != b"\x1f\x8b\x08\x00"
        or header[4:8] != b"\0\0\0\0"
        or header[8] != 2
        or header[9] != 255
    ):
        raise BundleError("archive gzip header is not canonical")
    budget = expansion if expansion is not None else {"members": 0, "bytes": 0}
    expected = {
        str(member["path"])[4:]: member
        for member in members
    }
    observed: list[str] = []
    expected_tar_bytes = _canonical_ustar_size(members)
    with tempfile.TemporaryDirectory(prefix="agentos-archive-ustar-") as temporary:
        temporary_root = Path(temporary)
        tar_stream = temporary_root / "observed.tar"
        try:
            _decompress_single_gzip(
                archive_path, tar_stream, expected_tar_bytes
            )
            with tar_stream.open("rb") as stream:
                with tarfile.open(fileobj=stream, mode="r:") as archive:
                    for info in archive:
                        if len(observed) >= MAX_ARCHIVE_MEMBERS:
                            raise BundleError("archive contains too many members")
                        name = info.name
                        safe = _safe_relative(name, "archive header member")
                        if len(safe.parts) > MAX_ARCHIVE_DEPTH:
                            raise BundleError("archive member nesting is too deep")
                        if (
                            info.type != tarfile.REGTYPE
                            or info.islnk()
                            or info.issym()
                            or info.isdev()
                            or info.mode != ARCHIVE_MODE
                            or info.uid != 0
                            or info.gid != 0
                            or info.uname != ""
                            or info.gname != ""
                            or info.mtime != 0
                            or info.linkname != ""
                            or info.pax_headers
                            or info.sparse is not None
                            or info.offset_data != info.offset + tarfile.BLOCKSIZE
                        ):
                            raise BundleError(
                                f"archive member is not canonical regular data: {name}"
                            )
                        if name in observed:
                            raise BundleError(f"archive repeats a member: {name}")
                        declared = expected.get(name)
                        if declared is None:
                            raise BundleError(f"archive contains an undeclared member: {name}")
                        if info.size != declared["raw_bytes"]:
                            raise BundleError(f"archive member size differs: {name}")
                        budget["members"] += 1
                        budget["bytes"] += info.size
                        if budget["members"] > MAX_FILES:
                            raise BundleError("archive expansion contains too many members")
                        if budget["bytes"] > MAX_ARCHIVE_RAW_BYTES:
                            raise BundleError("archive expansion exceeds its global byte budget")
                        destination = destination_root.joinpath(*safe.parts)
                        try:
                            destination = _new_file_path(
                                destination, "archive materialization member"
                            )
                        except BundleError as error:
                            raise BundleError(
                                f"archive member collides during materialization: {name}"
                            ) from error
                        source = archive.extractfile(info)
                        if source is None:
                            raise BundleError(f"archive member has no data: {name}")
                        digest = hashlib.sha256()
                        count = 0
                        with source, destination.open("xb") as output:
                            while True:
                                chunk = source.read(1024 * 1024)
                                if not chunk:
                                    break
                                count += len(chunk)
                                if count > info.size:
                                    raise BundleError(
                                        f"archive member expands beyond its header: {name}"
                                    )
                                digest.update(chunk)
                                output.write(chunk)
                        if (
                            count != info.size
                            or digest.hexdigest() != declared["raw_sha256"]
                        ):
                            raise BundleError(f"archive member bytes differ: {name}")
                        observed.append(name)
        except BundleError:
            raise
        except (OSError, EOFError, gzip.BadGzipFile, tarfile.TarError, zlib.error) as error:
            raise BundleError(
                f"archive stream is invalid: {record['stored_path']}: {error}"
            ) from error
        if observed != sorted(expected):
            raise BundleError("archive header order or inventory differs from its manifest")

        canonical = temporary_root / "canonical.tar"
        canonical = _new_file_path(canonical, "canonical USTAR replay")
        with canonical.open("xb") as output:
            _write_canonical_ustar(destination_root, observed, output)
        if not _files_equal(tar_stream, canonical):
            raise BundleError("archive USTAR member stream is not canonical")


def _materialize_payload(
    root: Path,
    records: dict[str, dict[str, Any]],
    archives: list[dict[str, object]],
    destination: Path,
) -> Path:
    replay = destination / "run"
    replay = _ensure_directory(replay, "materialized bundle payload")
    archive_paths = {str(record["stored_path"]) for record in archives}
    logical_paths: set[str] = set()
    for archive in archives:
        for member in _archive_members(archive):
            path = str(member["path"])
            if path in logical_paths or path in records:
                raise BundleError(f"archived logical path is duplicated: {path}")
            logical_paths.add(path)
    for relative in sorted(records):
        if relative in archive_paths:
            continue
        if relative.startswith("run/archives/"):
            raise BundleError(f"stored archive is undeclared: {relative}")
        safe = _safe_relative(relative, "stored payload path")
        destination_path = destination.joinpath(*safe.parts)
        _copy_regular_file(root / relative, destination_path, "stored payload")
    expansion = {"members": 0, "bytes": 0}
    for archive in archives:
        _extract_archive(
            root / str(archive["stored_path"]), replay, archive, expansion
        )
    return replay


def _manifest_body(
    *, run_id: str, source_commit: str, suite_sha256: str,
    campaign_sha256: str, summary_sha256: str,
    files: list[dict[str, object]], artifacts: list[dict[str, object]],
    archives: list[dict[str, object]], archive_summary: dict[str, object],
    profile: dict[str, object], generated_at: str,
    delivery: dict[str, object] | None,
    full_verification: dict[str, object],
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
        "delivery": delivery,
        "full_verification": full_verification,
        "artifacts": artifacts,
        "archive_summary": archive_summary,
        "archives": archives,
        "files": files,
    }


def _write_text(path: Path, value: str) -> None:
    try:
        atomic_write_bytes(path, value.encode("utf-8"), replace=False)
    except (OSError, ValueError) as error:
        raise BundleError(f"cannot publish bundle control file: {path}") from error


def create_bundle(
    *,
    run_dir: Path,
    suite_path: Path,
    output: Path,
    profile: str = FORMAL_PROFILE,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    profile_record = _profile_record(profile)
    lexical_run = _safe_directory(
        absolute_lexical_path(run_dir), "evaluation run directory"
    )
    lexical_suite = _safe_regular_file(
        absolute_lexical_path(suite_path), "evaluation suite"
    )
    run_dir = lexical_run.resolve(strict=True)
    suite_path = lexical_suite.resolve(strict=True)
    output = absolute_lexical_path(output)
    _reject_link_chain(output, "bundle output")
    if output.exists() or path_is_link(output):
        raise BundleError(f"refusing to replace an existing bundle: {output}")
    try:
        output.relative_to(run_dir)
    except ValueError:
        pass
    else:
        raise BundleError("bundle output cannot be inside the source run")

    campaign, micro_receipts, micro_paths = _verify_micro_campaign(run_dir, suite_path)
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
    if run_id != campaign["run"]["id"] or commit != campaign["run"]["commit"]:
        raise BundleError("evaluation summary identity differs from the campaign")
    plan = _strict_json(run_dir / "run-plan.json")
    measurement_source_receipt, measurement_receipt = _verify_measurement_source_receipt(
        run_dir,
        campaign,
        plan,
        commit,
        source_tree=SOURCE_TREE_ROOT,
        suite_path=suite_path,
    )
    full_verification, full_verification_paths = _verify_full_verification(
        run_dir,
        expected_commit=commit,
        profile=profile,
        contract_root=SOURCE_TREE_ROOT,
    )
    compatibility_receipts, compatibility_paths = _verify_compatibility_campaign(
        run_dir, campaign, required=profile == FORMAL_PROFILE
    )
    scenario_receipts, scenario_paths = _verify_scenario_campaign(
        run_dir, campaign, profile=profile
    )
    source_files = _validate_source_inventory(
        run_dir,
        profile=profile,
        micro_paths=micro_paths,
        scenario_paths=scenario_paths,
        compatibility_paths=compatibility_paths,
        full_verification_paths=full_verification_paths,
    )
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

    verified_summary = _verify_evaluation_contract(run_dir, suite_path)
    if profile == FORMAL_PROFILE:
        _verify_formal_summary(verified_summary)
    _verify_kernel_cost(
        run_dir, require_complete=profile == FORMAL_PROFILE
    )
    _compare_dashboard(run_dir)
    delivery: dict[str, object] | None = None
    if profile == FORMAL_PROFILE:
        try:
            delivery = make_manifest_binding(commit, output.name)
        except DeliveryContractError as error:
            raise BundleError(f"formal evidence release binding failed: {error}") from error
    if repo_root is not None:
        repo_root = _safe_directory(
            absolute_lexical_path(repo_root), "evidence repository root"
        ).resolve(strict=True)
        if profile != FORMAL_PROFILE or delivery is None:
            raise BundleError("only formal evidence can use committed delivery")
        expected_output = repo_root / delivery["release"]["path"]
        if output.resolve(strict=False) != expected_output.resolve(strict=False):
            raise BundleError(
                "committed formal evidence output must be evidence/releases/<bundle>"
            )
        _verify_committed_measurement_sources(
            repo_root, commit, measurement_receipt
        )
    _ensure_directory(output.parent, "bundle output parent")
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        payload = stage / "run"
        payload = _ensure_directory(payload, "staged bundle payload")
        archive_specs = _archive_specs(micro_paths, scenario_paths)
        archived_paths = {
            relative
            for _archive_id, _stored_path, members in archive_specs
            for relative in members
        }
        for source in source_files:
            relative = source.relative_to(run_dir)
            if relative.as_posix() in archived_paths:
                continue
            destination = payload / relative
            _copy_regular_file(source, destination, "bundle payload")
        _copy_regular_file(suite_path, payload / "suite.json", "evaluation suite")
        _copy_measurement_source_snapshot(
            SOURCE_TREE_ROOT, payload, measurement_receipt
        )
        archives = [
            _make_archive_record(
                run_dir,
                members,
                stage / stored_path,
                archive_id=archive_id,
                stored_path=stored_path,
            )
            for archive_id, stored_path, members in archive_specs
        ]
        payload_files = _stored_files(payload)
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
            delivery=delivery,
            full_verification=full_verification,
            artifacts=[
                measurement_source_receipt,
                *micro_receipts,
                *scenario_receipts,
                *compatibility_receipts,
            ],
            archives=archives,
            archive_summary=_archive_summary(archives),
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
        if repo_root is None:
            _ensure_directory(output.parent, "bundle output parent")
            if output.exists() or path_is_link(output):
                raise BundleError(f"refusing to replace an existing bundle: {output}")
            os.replace(stage, output)
        else:
            try:
                publish_bundle_and_index(
                    repo_root,
                    stage,
                    output,
                    commit,
                    delivery["release"],
                )
            except DeliveryContractError as error:
                raise BundleError(f"formal evidence delivery failed: {error}") from error
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def verify_bundle(root: Path) -> dict[str, Any]:
    """Replay portable evidence; Git authenticity requires verify_committed_bundle."""
    lexical_root = _safe_directory(absolute_lexical_path(root), "bundle root")
    root = lexical_root.resolve(strict=True)
    for name in ("manifest.json", "checksums.sha256"):
        control = root / name
        try:
            _safe_regular_file(control, f"bundle {name}")
        except BundleError as error:
            raise BundleError(
                f"bundle {name} is missing, unsafe, or oversized"
            ) from error
        if control.stat().st_size > MAX_CONTROL_FILE_BYTES:
            raise BundleError(f"bundle {name} is missing, unsafe, or oversized")
    manifest = _strict_json(root / "manifest.json")
    expected_fields = {
        "schema_version", "kind", "run_id", "source_commit", "generated_at",
        "payload_root", "suite_sha256", "campaign_sha256", "summary_sha256",
        "profile", "delivery", "artifacts", "archive_summary", "archives",
        "files", "full_verification", "binding_sha256",
    }
    if set(manifest) != expected_fields:
        raise BundleError("bundle manifest fields differ")
    if manifest["schema_version"] != SCHEMA_VERSION or manifest["kind"] != KIND:
        raise BundleError("bundle manifest schema is unsupported")
    if manifest["payload_root"] != "run":
        raise BundleError("bundle payload root is invalid")
    profile = _validate_profile_record(manifest["profile"])
    if profile["name"] == FORMAL_PROFILE:
        try:
            validate_delivery_field(manifest["delivery"], manifest["source_commit"])
        except DeliveryContractError as error:
            raise BundleError(f"formal evidence delivery binding failed: {error}") from error
    elif manifest["delivery"] is not None:
        raise BundleError("development evidence must not carry a release delivery binding")
    full_verification_record = manifest["full_verification"]
    if profile["name"] == FORMAL_PROFILE:
        if (
            not isinstance(full_verification_record, dict)
            or set(full_verification_record)
            != {
                "status",
                "source_commit",
                "payload_root",
                "receipt_sha256",
                "summary_sha256",
                "checksums_sha256",
                "file_count",
                "total_bytes",
                "tree_sha256",
            }
            or full_verification_record.get("status") != "verified"
            or full_verification_record.get("source_commit")
            != manifest["source_commit"]
        ):
            raise BundleError("formal full-verification binding is invalid")
    elif full_verification_record != DEVELOPMENT_FULL_VERIFICATION:
        raise BundleError("development full-verification must be unavailable")
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
        if path.startswith("run/raw/") or path.startswith("run/scenario/raw/"):
            raise BundleError("raw evidence must be stored in canonical archive shards")
        if path in records:
            raise BundleError(f"duplicate bundle file record: {path}")
        if type(record["bytes"]) is not int or record["bytes"] < 0:
            raise BundleError(f"invalid bundle file size: {path}")
        if not isinstance(record["sha256"], str) or SHA256_RE.fullmatch(record["sha256"]) is None:
            raise BundleError(f"invalid bundle file hash: {path}")
        records[path] = record

    archives_value = manifest["archives"]
    if (
        not isinstance(archives_value, list)
        or not archives_value
        or len(archives_value) > MAX_ARCHIVES
    ):
        raise BundleError("bundle manifest has no archive shards")
    archives: list[dict[str, object]] = []
    archive_ids: set[str] = set()
    archive_paths: set[str] = set()
    logical_records: dict[str, dict[str, object]] = {
        path: {
            "path": path,
            "bytes": record["bytes"],
            "sha256": record["sha256"],
        }
        for path, record in records.items()
    }
    archived_logical_paths: set[str] = set()
    for archive in archives_value:
        members = _archive_members(archive)
        archive_id = str(archive["archive_id"])
        stored_path = str(archive["stored_path"])
        if archive_id in archive_ids or stored_path in archive_paths:
            raise BundleError("archive manifest repeats an id or stored path")
        archive_ids.add(archive_id)
        archive_paths.add(stored_path)
        stored_record = records.get(stored_path)
        if stored_record is None or (
            stored_record["bytes"] != archive["stored_bytes"]
            or stored_record["sha256"] != archive["stored_sha256"]
        ):
            raise BundleError(f"archive differs from stored inventory: {stored_path}")
        for member in members:
            path = str(member["path"])
            if path in logical_records:
                raise BundleError(f"archive logical member is duplicated: {path}")
            archived_logical_paths.add(path)
            logical_records[path] = {
                "path": path,
                "bytes": member["raw_bytes"],
                "sha256": member["raw_sha256"],
            }
        archives.append(archive)
    if [item["archive_id"] for item in archives] != sorted(archive_ids):
        raise BundleError("archive manifest order is not canonical")
    expected_archive_summary = _archive_summary(archives)
    if manifest["archive_summary"] != expected_archive_summary:
        raise BundleError("bundle archive summary differs from its shards")
    if expected_archive_summary["raw_total_bytes"] > MAX_ARCHIVE_RAW_BYTES:
        raise BundleError("bundle archive expansion exceeds its global byte budget")
    if expected_archive_summary["member_count"] > MAX_FILES:
        raise BundleError("bundle archives contain too many logical members")

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
        file_record = logical_records.get(path)
        if file_record is None or any(
            artifact[key] != file_record[key] for key in ("path", "bytes", "sha256")
        ):
            raise BundleError(f"campaign artifact receipt differs from file inventory: {path}")
    if not archived_logical_paths.issubset(artifact_paths):
        raise BundleError("archive contains logical members outside campaign receipts")

    actual_files = _stored_files(root)
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

    with tempfile.TemporaryDirectory(prefix="agentos-bundle-materialize-") as temporary:
        materialized = Path(temporary)
        try:
            os.chmod(materialized, 0o700)
        except OSError:
            pass
        payload = _materialize_payload(root, records, archives, materialized)
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
        measurement_source_receipt, measurement_receipt = _verify_measurement_source_receipt(
            payload,
            campaign,
            plan,
            str(manifest["source_commit"]),
            source_tree=payload / MEASUREMENT_SOURCE_SNAPSHOT_ROOT,
            suite_path=payload / "suite.json",
        )
        replayed_full_verification, full_verification_paths = _verify_full_verification(
            payload,
            expected_commit=str(manifest["source_commit"]),
            profile=str(profile["name"]),
            contract_root=payload / MEASUREMENT_SOURCE_SNAPSHOT_ROOT,
        )
        if replayed_full_verification != manifest["full_verification"]:
            raise BundleError(
                "full-verification binding differs from packaged raw evidence"
            )
        compatibility_receipts, compatibility_paths = _verify_compatibility_campaign(
            payload, campaign, required=profile["name"] == FORMAL_PROFILE
        )
        scenario_receipts, scenario_paths = _verify_scenario_campaign(
            payload,
            campaign,
            profile=str(profile["name"]),
            source_tree=payload / MEASUREMENT_SOURCE_SNAPSHOT_ROOT,
        )
        expected_artifacts = [
            measurement_source_receipt,
            *micro_receipts,
            *scenario_receipts,
            *compatibility_receipts,
        ]
        if manifest["artifacts"] != expected_artifacts:
            raise BundleError("campaign artifact receipts differ from packaged raw evidence")
        _validate_source_inventory(
            payload,
            profile=str(profile["name"]),
            micro_paths=micro_paths,
            scenario_paths=scenario_paths,
            compatibility_paths=compatibility_paths,
            full_verification_paths=full_verification_paths,
            packaged=True,
            measurement_source_paths=_measurement_source_snapshot_paths(
                measurement_receipt
            ),
        )
        summary = _verify_evaluation_contract(payload)
        if profile["name"] == FORMAL_PROFILE:
            _verify_formal_summary(summary)
        _verify_kernel_cost(
            payload, require_complete=profile["name"] == FORMAL_PROFILE
        )
        _compare_dashboard(payload)
        if (
            summary["run"]["id"] != manifest["run_id"]
            or summary["run"]["commit"] != manifest["source_commit"]
        ):
            raise BundleError("packaged summary identity differs from manifest")
    return manifest


def verify_committed_bundle(root: Path, repo_root: Path) -> dict[str, Any]:
    """Verify portable bytes and the source-C to evidence-E Git delivery."""
    manifest = verify_bundle(root)
    if manifest["profile"]["name"] != FORMAL_PROFILE:
        raise BundleError("committed delivery is only valid for formal evidence")
    try:
        delivery = validate_delivery_field(
            manifest["delivery"], manifest["source_commit"]
        )
        verify_historical_committed_delivery(
            root,
            delivery["source_commit"],
            delivery["evidence_commit"],
            delivery["release"],
            repo_root=repo_root,
            require_committed=True,
        )
    except DeliveryContractError as error:
        raise BundleError(f"committed evidence delivery failed: {error}") from error
    receipt = _strict_json(
        _safe_directory(absolute_lexical_path(root), "bundle root")
        / "run"
        / "measurement-source-receipt.json"
    )
    _verify_committed_measurement_sources(
        repo_root, str(manifest["source_commit"]), receipt
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--run-dir", type=Path, required=True)
    create.add_argument("--suite", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument(
        "--repo-root",
        type=Path,
        help="publish formal evidence and its append-only release index atomically",
    )
    create.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default=FORMAL_PROFILE,
        help="formal is the default; development must be selected explicitly",
    )
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--repo-root", type=Path)
    verify.add_argument("--require-committed", action="store_true")
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
                repo_root=args.repo_root,
            )
            if result["profile"]["name"] == DEVELOPMENT_PROFILE:
                print(f"WARNING: {result['profile']['warning']}", file=__import__("sys").stderr)
            print(f"evaluation_bundle: created run={result['run_id']} output={args.output}")
        else:
            if args.require_committed:
                if args.repo_root is None:
                    raise BundleError("--require-committed requires --repo-root")
                result = verify_committed_bundle(args.bundle, args.repo_root)
            else:
                result = verify_bundle(args.bundle)
            if result["profile"]["name"] == DEVELOPMENT_PROFILE:
                print(f"WARNING: {result['profile']['warning']}", file=__import__("sys").stderr)
            elif not args.require_committed:
                print(
                    "NOTICE: portable verification proves internal integrity and "
                    "replayability only; use --require-committed for source-C Git "
                    "authenticity.",
                    file=__import__("sys").stderr,
                )
            print(f"evaluation_bundle: verified run={result['run_id']} commit={result['source_commit']}")
    except BundleError as error:
        raise SystemExit(f"evaluation bundle failed: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
