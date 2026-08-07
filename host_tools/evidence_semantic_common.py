#!/usr/bin/env python3
"""离线证据验证器共享的严格解析原语。"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType


class EvidenceSemanticError(ValueError):
    """工件存在且已哈希，但不能证明其声明。"""


SAFE_TAG = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")
BEGIN_GUEST = re.compile(r"^===== guest:([A-Za-z0-9:_-]{1,128}) =====$")
END_GUEST = re.compile(r"^===== end-guest:([A-Za-z0-9:_-]{1,128}) =====$")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True)
class CombinedLog:
    stdout: str
    guests: dict[str, str]


@dataclass
class ValidationContext:
    raw_dir: Path
    repo_root: Path
    modules: dict[str, ModuleType] = field(default_factory=dict)
    allowed_files: set[str] = field(default_factory=set)


def _regular_bytes(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise EvidenceSemanticError(f"{label} is missing or unsafe: {path}")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise EvidenceSemanticError(f"{label} is unreadable: {error}") from error
    if not raw:
        raise EvidenceSemanticError(f"{label} is empty")
    return raw


def _text(path: Path, label: str) -> str:
    raw = _regular_bytes(path, label)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceSemanticError(f"{label} is not UTF-8: {error}") from error
    if "\x00" in text:
        raise EvidenceSemanticError(f"{label} contains NUL bytes")
    return text


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceSemanticError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> object:
    raise EvidenceSemanticError(f"non-finite JSON number: {value}")


def _json(path: Path, label: str) -> object:
    raw = _regular_bytes(path, label)
    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceSemanticError(f"{label} is invalid JSON: {error}") from error


def _normalized_lines(text: str) -> list[str]:
    return [ANSI.sub("", line).rstrip("\r") for line in text.splitlines()]


def _require_line(text: str, expected: str, label: str) -> None:
    hits = [line for line in _normalized_lines(text) if line == expected]
    if len(hits) != 1:
        raise EvidenceSemanticError(
            f"{label} must contain exactly one line {expected!r}; got {len(hits)}"
        )


def _require_regex(text: str, pattern: re.Pattern[str], label: str) -> re.Match[str]:
    matches = [match for line in _normalized_lines(text) if (match := pattern.fullmatch(line))]
    if len(matches) != 1:
        raise EvidenceSemanticError(f"{label} marker count differs: {len(matches)}")
    return matches[0]


def _reject_tokens(text: str, tokens: tuple[str, ...], label: str) -> None:
    present = [token for token in tokens if token in text]
    if present:
        raise EvidenceSemanticError(f"{label} contains failure marker(s): {present!r}")


def _parse_guest_lines(lines: list[str], label: str) -> dict[str, str]:
    guests: dict[str, str] = {}
    index = 0
    while index < len(lines):
        begin = BEGIN_GUEST.fullmatch(lines[index])
        if begin is None:
            raise EvidenceSemanticError(
                f"{label} contains unframed Guest content at line {index + 1}"
            )
        tag = begin.group(1)
        if tag in guests:
            raise EvidenceSemanticError(f"{label} repeats Guest frame {tag}")
        index += 1
        body: list[str] = []
        while index < len(lines):
            end = END_GUEST.fullmatch(lines[index])
            if end is not None:
                if end.group(1) != tag:
                    raise EvidenceSemanticError(
                        f"{label} closes Guest frame {tag} as {end.group(1)}"
                    )
                break
            if BEGIN_GUEST.fullmatch(lines[index]) is not None:
                raise EvidenceSemanticError(f"{label} nests Guest frame inside {tag}")
            body.append(lines[index])
            index += 1
        if index >= len(lines):
            raise EvidenceSemanticError(f"{label} does not close Guest frame {tag}")
        if not any(line.strip() for line in body):
            raise EvidenceSemanticError(f"{label} Guest frame {tag} is empty")
        guests[tag] = "\n".join(body) + "\n"
        index += 1
    if not guests:
        raise EvidenceSemanticError(f"{label} contains no Guest frames")
    return guests


def _parse_guest_stream(path: Path, label: str) -> dict[str, str]:
    return _parse_guest_lines(_text(path, label).splitlines(), label)


def _parse_combined(path: Path, label: str, runner_label: str) -> CombinedLog:
    lines = _text(path, label).splitlines()
    stdout_header = f"===== runner-stdout:{runner_label} ====="
    guest_header = f"===== runner-guest-logs:{runner_label} ====="
    if not lines or lines[0] != stdout_header or lines.count(stdout_header) != 1:
        raise EvidenceSemanticError(f"{label} has an invalid runner stdout envelope")
    positions = [index for index, line in enumerate(lines) if line == guest_header]
    if len(positions) != 1 or positions[0] <= 1:
        raise EvidenceSemanticError(f"{label} has an invalid runner Guest envelope")
    split = positions[0]
    stdout = "\n".join(lines[1:split]) + "\n"
    guests = _parse_guest_lines(lines[split + 1 :], label)
    return CombinedLog(stdout=stdout, guests=guests)


def _expect_tags(guests: dict[str, str], expected: set[str], label: str) -> None:
    if set(guests) != expected:
        missing = sorted(expected - set(guests))
        extra = sorted(set(guests) - expected)
        raise EvidenceSemanticError(
            f"{label} Guest tag inventory differs: missing={missing} extra={extra}"
        )


def _load_module(ctx: ValidationContext, relative: str) -> ModuleType:
    if relative in ctx.modules:
        return ctx.modules[relative]
    path = ctx.repo_root / relative
    if path.is_symlink() or not path.is_file():
        raise EvidenceSemanticError(f"semantic validator is missing or unsafe: {relative}")
    module_name = "_agentos_evidence_" + re.sub(r"[^A-Za-z0-9_]", "_", relative)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise EvidenceSemanticError(f"cannot load semantic validator: {relative}")
    module = importlib.util.module_from_spec(spec)
    parent = str(path.parent)
    inserted = not sys.path or sys.path[0] != parent
    if inserted:
        sys.path.insert(0, parent)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise EvidenceSemanticError(f"cannot load semantic validator {relative}: {error}") from error
    finally:
        if inserted and sys.path and sys.path[0] == parent:
            sys.path.pop(0)
    ctx.modules[relative] = module
    return module


def _call(module: ModuleType, name: str, label: str, *args: object) -> object:
    action = getattr(module, name, None)
    if not callable(action):
        raise EvidenceSemanticError(f"{label} validator lacks callable {name}")
    try:
        return action(*args)
    except Exception as error:
        raise EvidenceSemanticError(f"{label} semantic validation failed: {error}") from error


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
