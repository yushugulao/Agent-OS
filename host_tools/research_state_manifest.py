#!/usr/bin/env python3
"""从一个严格清单派生 Guest 与文件系统状态清单。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MANIFEST_RELATIVE_PATH = Path("ci/research-state-manifest.json")
STATE_NAME_RE = re.compile(r"rp_[A-Za-z0-9_]+\Z")
SOURCE_SUFFIXES = (".c", ".h")
TARGET_NAMES = ("plain", "agentos")
TOP_LEVEL_KEYS = {
    "schema_version",
    "targets",
    "state_file_calls",
    "host_state_files",
    "archive_optional_state_files",
    "opaque_guest_state_files",
}
TARGET_KEYS = {"source_roots"}
SUPPORTED_STATE_FILE_CALLS = {
    "consistency_runtime_contains",
    "file_contains_silent",
    "load_graph_stats",
    "optional_file_contains",
    "require_file_token",
    "require_token",
    "rp_append_file",
    "rp_append_host_action_line",
    "rp_count_lines",
    "rp_count_token",
    "rp_evidence_count_prefixed_lines",
    "rp_evidence_get_u64",
    "rp_evidence_measure_file",
    "rp_file_contains",
    "rp_get_int_value",
    "rp_read_file",
    "rp_write_file",
}
REQUIRED_STATE_FILE_CALLS = {"rp_append_file", "rp_read_file", "rp_write_file"}
GUEST_STATE_RECEIPT_SCHEMA = "sha256-inventory-v1"
_GUEST_STATE_DIGEST_DOMAIN = b"agentos-guest-state\0sha256-inventory-v1\0"
_DIGEST_READ_CHUNK_BYTES = 1024 * 1024


class StateManifestError(ValueError):
    """无法无歧义地派生状态清单。"""


def _path_is_link(path: Path) -> bool:
    junction_test = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(junction_test and junction_test())


def guest_state_inventory_sha256(
    state_dir: Path, *, excluded_names: Iterable[str] = ()
) -> tuple[int, str]:
    """返回一个 Guest 快照的规范数量与内容摘要。

    摘要绑定排序后的文件名清单、各文件大小及全部内容字节。只有规范 ``rp_*``
    常规文件参与；``excluded_names`` 指定的 Host sidecar 不属于该 Guest 状态承诺。
    """
    try:
        directory_status = state_dir.lstat()
    except OSError as error:
        raise StateManifestError(f"Guest state directory is unavailable: {state_dir}") from error
    if _path_is_link(state_dir) or stat.S_ISLNK(
        directory_status.st_mode
    ) or not stat.S_ISDIR(
        directory_status.st_mode
    ):
        raise StateManifestError(f"Guest state directory is unsafe: {state_dir}")

    excluded = set(excluded_names)
    invalid_exclusions = sorted(
        name for name in excluded if STATE_NAME_RE.fullmatch(name) is None
    )
    if invalid_exclusions:
        raise StateManifestError(
            "Guest state digest has invalid exclusions: "
            + ", ".join(invalid_exclusions)
        )

    entries: list[tuple[str, Path, os.stat_result]] = []
    try:
        candidates = sorted(state_dir.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise StateManifestError(f"Guest state inventory is unreadable: {state_dir}") from error
    for path in candidates:
        name = path.name
        if not name.startswith("rp_"):
            continue
        if STATE_NAME_RE.fullmatch(name) is None:
            raise StateManifestError(f"unsafe state filename in Guest inventory: {name}")
        if name in excluded:
            continue
        try:
            path_status = path.lstat()
        except OSError as error:
            raise StateManifestError(f"Guest state file is unavailable: {name}") from error
        if _path_is_link(path) or stat.S_ISLNK(
            path_status.st_mode
        ) or not stat.S_ISREG(path_status.st_mode):
            raise StateManifestError(f"Guest state file is unsafe: {name}")
        entries.append((name, path, path_status))

    digest = hashlib.sha256()
    digest.update(_GUEST_STATE_DIGEST_DOMAIN)
    digest.update(len(entries).to_bytes(8, "big"))
    for name, path, expected_status in entries:
        name_bytes = name.encode("ascii")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise StateManifestError(f"Guest state file cannot be opened safely: {name}") from error
        try:
            opened_status = os.fstat(descriptor)
            same_identity = (
                opened_status.st_dev == expected_status.st_dev
                and opened_status.st_ino == expected_status.st_ino
            )
            if not stat.S_ISREG(opened_status.st_mode) or not same_identity:
                raise StateManifestError(f"Guest state file changed while hashing: {name}")
            digest.update(len(name_bytes).to_bytes(4, "big"))
            digest.update(name_bytes)
            digest.update(opened_status.st_size.to_bytes(8, "big"))
            observed_size = 0
            while True:
                block = os.read(descriptor, _DIGEST_READ_CHUNK_BYTES)
                if not block:
                    break
                observed_size += len(block)
                digest.update(block)
            final_status = os.fstat(descriptor)
            if (
                observed_size != opened_status.st_size
                or final_status.st_size != opened_status.st_size
                or final_status.st_mtime_ns != opened_status.st_mtime_ns
            ):
                raise StateManifestError(f"Guest state file changed while hashing: {name}")
        finally:
            os.close(descriptor)

    try:
        final_names = sorted(
            path.name
            for path in state_dir.iterdir()
            if path.name.startswith("rp_") and path.name not in excluded
        )
    except OSError as error:
        raise StateManifestError(f"Guest state inventory changed while hashing: {state_dir}") from error
    if final_names != [name for name, _path, _status in entries]:
        raise StateManifestError(f"Guest state inventory changed while hashing: {state_dir}")
    for name, path, expected_status in entries:
        try:
            final_status = path.lstat()
        except OSError as error:
            raise StateManifestError(
                f"Guest state file changed while hashing: {name}"
            ) from error
        if (
            _path_is_link(path)
            or stat.S_ISLNK(final_status.st_mode)
            or not stat.S_ISREG(final_status.st_mode)
            or final_status.st_dev != expected_status.st_dev
            or final_status.st_ino != expected_status.st_ino
            or final_status.st_size != expected_status.st_size
            or final_status.st_mtime_ns != expected_status.st_mtime_ns
        ):
            raise StateManifestError(f"Guest state file changed while hashing: {name}")
    return len(entries), digest.hexdigest()


@dataclass(frozen=True)
class ResearchStateManifest:
    source_roots: dict[str, tuple[Path, ...]]
    state_file_calls: tuple[str, ...]
    host_state_files: tuple[str, ...]
    archive_optional_state_files: tuple[str, ...]
    opaque_guest_state_files: tuple[str, ...]


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StateManifestError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def _require_exact_keys(value: dict[str, object], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise StateManifestError(f"{label} is missing keys: {', '.join(missing)}")
    if unknown:
        raise StateManifestError(f"{label} has unknown keys: {', '.join(unknown)}")


def _require_unique_strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise StateManifestError(f"{label} must be a non-empty array")
    if any(not isinstance(item, str) or not item for item in value):
        raise StateManifestError(f"{label} must contain non-empty strings")
    items = tuple(value)
    if len(set(items)) != len(items):
        raise StateManifestError(f"{label} contains duplicate entries")
    return items


def _require_state_names(value: object, label: str) -> tuple[str, ...]:
    names = _require_unique_strings(value, label)
    invalid = sorted(name for name in names if STATE_NAME_RE.fullmatch(name) is None)
    if invalid:
        raise StateManifestError(f"{label} has invalid state names: {', '.join(invalid)}")
    return names


def _require_relative_roots(value: object, label: str) -> tuple[Path, ...]:
    roots = _require_unique_strings(value, label)
    result: list[Path] = []
    for raw in roots:
        path = Path(raw)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise StateManifestError(f"{label} has an unsafe source root: {raw}")
        result.append(path)
    return tuple(result)


def parse_manifest_text(text: str) -> ResearchStateManifest:
    try:
        raw = json.loads(text, object_pairs_hook=_strict_object)
    except json.JSONDecodeError as error:
        raise StateManifestError(f"state manifest is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise StateManifestError("state manifest root must be an object")
    _require_exact_keys(raw, TOP_LEVEL_KEYS, "state manifest")
    if raw["schema_version"] != 4:
        raise StateManifestError("state manifest schema_version must be 4")

    raw_targets = raw["targets"]
    if not isinstance(raw_targets, dict):
        raise StateManifestError("state manifest targets must be an object")
    if set(raw_targets) != set(TARGET_NAMES):
        missing = sorted(set(TARGET_NAMES) - set(raw_targets))
        unknown = sorted(set(raw_targets) - set(TARGET_NAMES))
        detail = []
        if missing:
            detail.append(f"missing={','.join(missing)}")
        if unknown:
            detail.append(f"unknown={','.join(unknown)}")
        raise StateManifestError("state manifest targets are invalid: " + " ".join(detail))
    source_roots: dict[str, tuple[Path, ...]] = {}
    for target in TARGET_NAMES:
        entry = raw_targets[target]
        if not isinstance(entry, dict):
            raise StateManifestError(f"target {target} must be an object")
        _require_exact_keys(entry, TARGET_KEYS, f"target {target}")
        source_roots[target] = _require_relative_roots(
            entry["source_roots"], f"target {target} source_roots"
        )

    calls = _require_unique_strings(raw["state_file_calls"], "state_file_calls")
    unknown_calls = sorted(set(calls) - SUPPORTED_STATE_FILE_CALLS)
    missing_calls = sorted(REQUIRED_STATE_FILE_CALLS - set(calls))
    if unknown_calls:
        raise StateManifestError(
            "state_file_calls has unsupported entries: " + ", ".join(unknown_calls)
        )
    if missing_calls:
        raise StateManifestError(
            "state_file_calls is missing core operations: " + ", ".join(missing_calls)
        )
    host_files = _require_state_names(raw["host_state_files"], "host_state_files")
    archive_optional_files = _require_state_names(
        raw["archive_optional_state_files"], "archive_optional_state_files"
    )
    opaque_guest_files = _require_state_names(
        raw["opaque_guest_state_files"], "opaque_guest_state_files"
    )
    inventories = (
        ("host", set(host_files)),
        ("archive optional", set(archive_optional_files)),
    )
    for index, (left_label, left) in enumerate(inventories):
        for right_label, right in inventories[index + 1 :]:
            overlap = sorted(left & right)
            if overlap:
                raise StateManifestError(
                    f"{left_label} and {right_label} state inventories overlap: "
                    + ", ".join(overlap)
                )
    opaque_host_overlap = sorted(set(opaque_guest_files) & set(host_files))
    if opaque_host_overlap:
        raise StateManifestError(
            "opaque Guest and Host state inventories overlap: "
            + ", ".join(opaque_host_overlap)
        )
    return ResearchStateManifest(
        source_roots, calls, host_files, archive_optional_files, opaque_guest_files,
    )


def load_manifest(root: Path) -> ResearchStateManifest:
    path = root / MANIFEST_RELATIVE_PATH
    if not path.is_file():
        raise StateManifestError(f"state manifest is missing: {path}")
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise StateManifestError(f"state manifest is not valid UTF-8: {path}") from error
    return parse_manifest_text(text)


def _without_c_comments(text: str) -> str:
    output: list[str] = []
    index = 0
    quote = ""
    while index < len(text):
        current = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if quote:
            output.append(current)
            if current == "\\" and following:
                output.append(following)
                index += 2
                continue
            if current == quote:
                quote = ""
            index += 1
            continue
        if current in ('"', "'"):
            quote = current
            output.append(current)
            index += 1
            continue
        if current == "/" and following == "/":
            newline = text.find("\n", index + 2)
            if newline < 0:
                break
            output.append("\n")
            index = newline + 1
            continue
        if current == "/" and following == "*":
            end = text.find("*/", index + 2)
            if end < 0:
                raise StateManifestError("unterminated C block comment")
            output.extend("\n" for char in text[index : end + 2] if char == "\n")
            index = end + 2
            continue
        output.append(current)
        index += 1
    return "".join(output)


def discover_state_names(source_roots: Iterable[Path], calls: Iterable[str]) -> set[str]:
    call_names = tuple(calls)
    call_pattern = "|".join(re.escape(name) for name in sorted(call_names, key=len, reverse=True))
    pattern = re.compile(
        rf"\b(?:{call_pattern})\s*\(\s*\"(?P<name>rp_[A-Za-z0-9_]+)\""
    )
    names: set[str] = set()
    source_count = 0
    for root in source_roots:
        if not root.is_dir():
            raise StateManifestError(f"state source root is missing: {root}")
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            source_count += 1
            try:
                source = path.read_text(encoding="utf-8", errors="strict")
            except UnicodeDecodeError as error:
                raise StateManifestError(f"state source is not valid UTF-8: {path}") from error
            names.update(match.group("name") for match in pattern.finditer(_without_c_comments(source)))
    if source_count == 0:
        raise StateManifestError("state source inventory is empty")
    if not names:
        raise StateManifestError("state filename inventory is empty")
    return names


def target_state_names(
    root: Path, manifest: ResearchStateManifest, target: str
) -> set[str]:
    if target not in manifest.source_roots:
        raise StateManifestError(f"unknown state target: {target}")
    return discover_state_names(
        (root / relative for relative in manifest.source_roots[target]),
        manifest.state_file_calls,
    )


def archive_state_names(
    root: Path, manifest: ResearchStateManifest, target: str
) -> set[str]:
    names = target_state_names(root, manifest, target)
    optional = set(manifest.archive_optional_state_files)
    missing_optional = sorted(optional - names)
    if missing_optional:
        raise StateManifestError(
            f"target {target} does not declare archive-optional state files: "
            + ", ".join(missing_optional)
        )
    return names - set(manifest.host_state_files) - optional


def validate_archive_state_inventory(
    root: Path, manifest: ResearchStateManifest, target: str, actual: Iterable[str]
) -> set[str]:
    observed = set(actual)
    expected = archive_state_names(root, manifest, target)
    if observed != expected:
        raise StateManifestError(
            f"{target} complete state archive Guest inventory differs from trusted "
            f"manifest: missing={sorted(expected - observed)} "
            f"extra={sorted(observed - expected)}"
        )
    return observed


def repository_state_inventory(
    root: Path, manifest: ResearchStateManifest | None = None
) -> set[str]:
    contract = manifest or load_manifest(root)
    names = set(contract.host_state_files)
    for target in TARGET_NAMES:
        names.update(target_state_names(root, contract, target))
    return names


def repo_state_names(repo_dir: Path, root: Path | None = None) -> set[str]:
    contract_root = root or Path(__file__).resolve().parents[1]
    manifest = load_manifest(contract_root)
    return discover_state_names(
        (repo_dir / "user" / "src", repo_dir / "user" / "include"),
        manifest.state_file_calls,
    )


def short_name_map(
    names: Iterable[str], *, excluded_names: Iterable[str] = (), dir_size: int = 14
) -> dict[str, str]:
    excluded = set(excluded_names)
    grouped: dict[str, list[str]] = {}
    for name in sorted(set(names) - excluded):
        if STATE_NAME_RE.fullmatch(name) is None:
            raise StateManifestError(f"unsafe state filename in inventory: {name}")
        grouped.setdefault(name[:dir_size], []).append(name)
    collisions = {key: values for key, values in grouped.items() if len(values) != 1}
    if collisions:
        detail = "; ".join(
            f"{key}={','.join(values)}" for key, values in sorted(collisions.items())
        )
        raise StateManifestError(f"state filename prefixes are ambiguous: {detail}")
    return {short: values[0] for short, values in grouped.items()}


def validate_repository_state_contract(root: Path) -> dict[str, object]:
    manifest = load_manifest(root)
    plain = target_state_names(root, manifest, "plain")
    agentos = target_state_names(root, manifest, "agentos")
    plain_archive = archive_state_names(root, manifest, "plain")
    agentos_archive = archive_state_names(root, manifest, "agentos")
    if not plain <= agentos:
        raise StateManifestError(
            "AgentOS state inventory dropped plain target names: "
            + ", ".join(sorted(plain - agentos))
        )
    unknown_opaque = sorted(set(manifest.opaque_guest_state_files) - (plain | agentos))
    if unknown_opaque:
        raise StateManifestError(
            "opaque Guest state files are absent from target inventories: "
            + ", ".join(unknown_opaque)
        )
    for target, names in (("plain", plain), ("agentos", agentos)):
        short_name_map(
            names,
            excluded_names=manifest.host_state_files,
        )
    return {
        "plain_state_names": len(plain),
        "agentos_state_names": len(agentos),
        "agentos_only_state_names": len(agentos - plain),
        "repository_state_names": len(repository_state_inventory(root, manifest)),
        "plain_archive_state_names": len(plain_archive),
        "agentos_archive_state_names": len(agentos_archive),
        "archive_optional_state_files": len(manifest.archive_optional_state_files),
        "opaque_guest_state_files": len(manifest.opaque_guest_state_files),
        "status": "ready",
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    summary = validate_repository_state_contract(root)
    print(
        "research_state_manifest: "
        + " ".join(f"{key}={value}" for key, value in summary.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
