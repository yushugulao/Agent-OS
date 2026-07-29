#!/usr/bin/env python3
"""Deterministic complete-state archives for offline dual evidence replay."""
from __future__ import annotations

import argparse
import io
import json
import re
import stat
import tempfile
import zipfile
from pathlib import Path

from dual_state_evidence_contract import (
    BACKEND_REPORT_ARTIFACTS,
    HOST_RUN_RESULT_STATE_NAME,
    MAIN_FLOW_SOURCE_ARTIFACTS,
    MAIN_FLOW_SOURCE_SPECS,
    MAIN_FLOW_TELEMETRY_ARTIFACT,
    PROGRAM_LEDGER_ARTIFACTS,
    RUN_RESULT_ARTIFACTS,
    SEEDED_ACTION_SUMMARY_ARTIFACT,
    STATE_ARCHIVE_ARTIFACTS,
)
from evidence_semantic_common import EvidenceSemanticError, ValidationContext, _regular_bytes
from research_state_manifest import StateManifestError, load_manifest, validate_archive_state_inventory
from result_bundle_publication import ResultPublicationError, atomic_write_bytes

MAX_FILES = 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
STATE_NAME = re.compile(r"rp_[a-z0-9_]+\Z")


def _is_link(path: Path) -> bool:
    junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(junction) and junction())


def _canonical_extract_summary(names: list[str]) -> bytes:
    payload = {
        "extracted_state_files": len(names),
        "files": sorted(names),
        "status": "ready",
    }
    return (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")

def _state_names(state_dir: Path) -> list[str]:
    from compare_dual_platform_state import read_summary

    summary = read_summary(state_dir)
    names = sorted(summary["files"])
    if len(names) > MAX_FILES:
        raise ValueError("state archive exceeds file-count limit")
    return names

def _canonical_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.comment = b""
        for name, data in files.items():
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return output.getvalue()


def pack_state(state_dir: Path, output: Path) -> None:
    if _is_link(state_dir) or not state_dir.is_dir():
        raise ValueError("state archive source is missing or unsafe")
    state_root = state_dir.resolve(strict=True)
    summary_path = state_dir / "extract-summary.json"
    if _is_link(summary_path) or not summary_path.is_file():
        raise ValueError("state archive extract summary is missing or unsafe")
    summary_stat = summary_path.lstat()
    if not stat.S_ISREG(summary_stat.st_mode) or not 0 < summary_stat.st_size <= MAX_FILE_BYTES:
        raise ValueError("state archive extract summary has an invalid size")

    names = _state_names(state_dir)
    canonical_summary = _canonical_extract_summary(names)
    sizes: dict[str, int] = {}
    total_bytes = len(canonical_summary)
    for name in names:
        path = state_dir / name
        if _is_link(path):
            raise ValueError(f"state archive member is unsafe: {name}")
        try:
            member_stat = path.lstat()
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise ValueError(f"state archive member is unavailable: {name}") from error
        if (
            not stat.S_ISREG(member_stat.st_mode)
            or resolved.parent != state_root
            or not 0 < member_stat.st_size <= MAX_FILE_BYTES
        ):
            raise ValueError(f"state archive member has an invalid size: {name}")
        sizes[name] = member_stat.st_size
        total_bytes += member_stat.st_size
        if total_bytes > MAX_ARCHIVE_BYTES:
            raise ValueError("state archive exceeds total-size limit")

    files: dict[str, bytes] = {"extract-summary.json": canonical_summary}
    for name in names:
        path = state_dir / name
        with path.open("rb") as handle:
            data = handle.read(sizes[name] + 1)
            trailing = handle.read(1)
        if len(data) != sizes[name] or trailing:
            raise ValueError(f"state archive member changed while reading: {name}")
        files[name] = data
    archive_bytes = _canonical_zip(files)
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise ValueError("state archive exceeds encoded-size limit")

    try:
        atomic_write_bytes(output, archive_bytes)
    except ResultPublicationError as error:
        raise ValueError(f"state archive output is unsafe: {error}") from error


def _read_archive(path: Path, label: str) -> dict[str, bytes]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise EvidenceSemanticError(f"{label} is missing, unsafe, or oversized")
    try:
        raw = path.read_bytes()
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            if archive.comment:
                raise EvidenceSemanticError(f"{label} has a non-canonical comment")
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if (
                not 2 <= len(infos) <= MAX_FILES + 1
                or names[0] != "extract-summary.json"
                or names[1:] != sorted(names[1:])
                or len(names) != len(set(names))
                or HOST_RUN_RESULT_STATE_NAME in names[1:]
                or any(STATE_NAME.fullmatch(name) is None for name in names[1:])
                or any(info.is_dir() or info.file_size <= 0 or info.file_size > MAX_FILE_BYTES
                       or info.compress_type != zipfile.ZIP_STORED or info.flag_bits != 0
                       for info in infos)
                or sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES
            ):
                raise EvidenceSemanticError(f"{label} member inventory differs")
            files = {info.filename: archive.read(info) for info in infos}
            if files["extract-summary.json"] != _canonical_extract_summary(names[1:]):
                raise EvidenceSemanticError(
                    f"{label} extract summary is not canonical"
                )
            if _canonical_zip(files) != raw:
                raise EvidenceSemanticError(f"{label} is not canonical")
            return files
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise EvidenceSemanticError(f"{label} is not a valid state archive") from error


def _materialize(root: Path, target: str, files: dict[str, bytes]) -> Path:
    state_dir = root / target
    state_dir.mkdir()
    for name, data in files.items():
        (state_dir / name).write_bytes(data)
    return state_dir


def validate_state_archives(
    ctx: ValidationContext, state: dict[str, object]
) -> dict[str, set[str]]:
    archives = {
        target: _read_archive(
            ctx.raw_dir / STATE_ARCHIVE_ARTIFACTS[target], f"{target} complete state archive"
        )
        for target in ("plain", "agentos")
    }
    try:
        manifest = load_manifest(ctx.repo_root)
        for target, files in archives.items():
            validate_archive_state_inventory(ctx.repo_root, manifest, target,
                                             set(files) - {"extract-summary.json"})
    except StateManifestError as error:
        raise EvidenceSemanticError(f"trusted state manifest is invalid: {error}") from error
    with tempfile.TemporaryDirectory(prefix="agentos-evidence-state-") as temporary:
        root = Path(temporary)
        plain = _materialize(root, "plain", archives["plain"])
        agentos = _materialize(root, "agentos", archives["agentos"])
        try:
            from compare_dual_platform_state import compare_state
            replayed = compare_state(
                plain,
                agentos,
                min_common_files=240,
                plain_run_result=ctx.raw_dir / RUN_RESULT_ARTIFACTS["plain"],
                agentos_run_result=ctx.raw_dir / RUN_RESULT_ARTIFACTS["agentos"],
                plain_log=ctx.raw_dir / "dual-plain-qemu.log",
                agentos_log=ctx.raw_dir / "dual-agentos-qemu.log",
                seeded_summary=ctx.raw_dir / SEEDED_ACTION_SUMMARY_ARTIFACT,
            )
        except (OSError, UnicodeError, ValueError) as error:
            raise EvidenceSemanticError(f"complete dual state replay failed: {error}") from error
    if replayed != state:
        raise EvidenceSemanticError("dual state summary differs from complete state replay")
    bindings = {
        "plain": {
            "rp_orch_timing": PROGRAM_LEDGER_ARTIFACTS["plain"],
            "rp_backend_exec": BACKEND_REPORT_ARTIFACTS["plain"],
        },
        "agentos": {
            "rp_orch_timing": PROGRAM_LEDGER_ARTIFACTS["agentos"],
            "rp_backend_exec": BACKEND_REPORT_ARTIFACTS["agentos"],
            "rp_agentos_mainflow": MAIN_FLOW_TELEMETRY_ARTIFACT,
            **{spec.source: MAIN_FLOW_SOURCE_ARTIFACTS[spec.source]
               for spec in MAIN_FLOW_SOURCE_SPECS},
        },
    }
    for target, mapping in bindings.items():
        for source, artifact in mapping.items():
            if archives[target].get(source) != _regular_bytes(
                ctx.raw_dir / artifact, f"{target} selected state source {source}"
            ):
                raise EvidenceSemanticError(
                    f"{target} selected state source differs from complete archive: {source}"
                )
    return {
        target: set(files) - {"extract-summary.json"}
        for target, files in archives.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pack_state(args.state_dir, args.output)
    print(json.dumps({"output": str(args.output), "status": "ready"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
__all__ = ["pack_state", "validate_state_archives"]
