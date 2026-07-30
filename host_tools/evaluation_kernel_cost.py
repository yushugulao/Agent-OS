#!/usr/bin/env python3
"""Collect portable, read-only kernel cost evidence for AgentOS evaluation.

The collector never builds or transforms a kernel.  A formal run must provide
separate environment and build manifests, including the exact commit, build
commands, build log, configuration, artifact paths, and artifact hashes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Sequence

try:
    from .strict_json import strict_json_loads
except ImportError:
    from strict_json import strict_json_loads


SCHEMA_VERSION = 1
CONFIG_KIND = "agentos-kernel-cost-config"
ENVIRONMENT_KIND = "agentos-evaluation-environment"
BUILD_KIND = "agentos-kernel-build-manifest"
REPORT_KIND = "agentos-kernel-cost-report"
FRAGMENT_KIND = "agentos-evaluation-benchmark-fragment"
TRUSTED_BUILD_SCHEMA_VERSION = 1
TRUSTED_BUILD_CONFIG_KIND = "agentos-trusted-kernel-build-config"
TRUSTED_BUILD_LOG_KIND = "agentos-trusted-kernel-build-log"
TRUSTED_BUILD_TIMEOUT_SECONDS = 900
TRUSTED_BUILD_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
UINT_RE = re.compile(r"(?:0|[1-9][0-9]{0,19})\Z")
HEX_RE = re.compile(r"(?:0|[0-9a-fA-F][0-9a-fA-F]{0,15})\Z")
METRIC_IDS = ("elf_file_bytes", "text_bytes", "data_bytes", "bss_bytes")
COMMAND_ENVIRONMENT = {"LANG": "C", "LC_ALL": "C"}
MAX_JSON_BYTES = 1024 * 1024

CONFIG_FIELDS = {"schema_version", "kind", "targets", "metrics", "tool", "limits"}
CONFIG_TARGET_FIELDS = {"id", "role", "label", "required_relative_path"}
CONFIG_METRIC_FIELDS = {"id", "label", "unit", "direction", "source", "task"}
CONFIG_TOOL_FIELDS = {"output_format", "version_arguments", "measurement_arguments"}
CONFIG_LIMIT_FIELDS = {"timeout_seconds", "max_output_bytes"}
ENVIRONMENT_FIELDS = {
    "schema_version",
    "kind",
    "run_id",
    "source_commit",
    "environment_id",
    "facts",
}
ENVIRONMENT_FACT_FIELDS = {"name", "value"}
BUILD_FIELDS = {
    "schema_version",
    "kind",
    "run_id",
    "source_commit",
    "environment_sha256",
    "build_config",
    "build_log",
    "targets",
}
FILE_RECEIPT_FIELDS = {"path", "sha256"}
BUILD_TARGET_FIELDS = {"id", "path", "sha256", "command_argv"}
REPORT_FIELDS = {
    "schema_version",
    "kind",
    "binding",
    "tool",
    "targets",
    "content_sha256",
}
BINDING_FIELDS = {
    "run_id",
    "source_commit",
    "environment_sha256",
    "config",
    "environment_manifest",
    "build_manifest",
}
TOOL_FIELDS = {"status", "sha256", "version", "version_command", "path_hint", "reason"}
COMMAND_FIELDS = {"argv", "returncode", "stdout_base64", "stderr_base64", "error"}
TARGET_FIELDS = {"id", "status", "reason", "source", "size_command", "metrics"}
SOURCE_FIELDS = {
    "path",
    "expected_sha256",
    "observed_sha256",
    "file_size_bytes",
    "elf_identity",
    "status",
}
ELF_FIELDS = {"class", "endianness", "machine", "type", "header_size"}
METRIC_FIELDS = {"id", "status", "value", "reason"}
TRUSTED_BUILD_CONFIG_FIELDS = {
    "schema_version",
    "kind",
    "run_id",
    "source_commit",
    "kernel_cost_config",
    "make_tool",
    "command_policy",
    "environment",
    "environment_sha256",
    "targets",
}
TRUSTED_BUILD_CONFIG_RECEIPT_FIELDS = {"path", "sha256"}
TRUSTED_BUILD_MAKE_FIELDS = {"path", "sha256", "version_argv", "version"}
TRUSTED_BUILD_POLICY_FIELDS = {"timeout_seconds", "max_output_bytes"}
TRUSTED_BUILD_TARGET_FIELDS = {
    "id", "role", "path", "clean_argv", "build_argv",
}
TRUSTED_BUILD_LOG_FIELDS = {
    "schema_version", "kind", "run_id", "source_commit",
    "environment_sha256", "commands",
}
TRUSTED_BUILD_COMMAND_FIELDS = {
    "sequence", "target_id", "phase", "cwd", "argv",
    "environment_sha256", "returncode", "duration_ms", "error",
    "stdout_base64", "stdout_sha256", "stderr_base64", "stderr_sha256",
}
TRUSTED_ENVIRONMENT_REQUIRED = {
    "LANG", "LC_ALL", "PYTHONHASHSEED", "SOURCE_DATE_EPOCH", "TZ",
}
TRUSTED_ENVIRONMENT_OPTIONAL = {
    "PATH", "SystemRoot", "COMSPEC", "PATHEXT", "HOME", "TMP", "TEMP",
}


class KernelCostError(ValueError):
    """Raised when kernel cost evidence is incomplete or inconsistent."""


@dataclass(frozen=True)
class ToolExecution:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    error: str | None = None


ToolRunner = Callable[[Sequence[str], int, int], ToolExecution]
RepositoryReader = Callable[[Path], tuple[str, bool]]


def _expect_keys(value: dict[str, Any], expected: Iterable[str], label: str) -> None:
    required = set(expected)
    observed = set(value)
    if observed != required:
        raise KernelCostError(
            f"{label} fields differ: missing={sorted(required - observed)} "
            f"extra={sorted(observed - required)}"
        )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise KernelCostError(f"{label} must be an object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise KernelCostError(f"{label} must be an array")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise KernelCostError(f"{label} must be a non-empty string")
    return value


def _identifier(value: Any, label: str) -> str:
    text = _string(value, label)
    if ID_RE.fullmatch(text) is None:
        raise KernelCostError(f"{label} is not a canonical identifier")
    return text


def _sha(value: Any, label: str) -> str:
    text = _string(value, label)
    if SHA256_RE.fullmatch(text) is None:
        raise KernelCostError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise KernelCostError(f"{label} must be an integer >= {minimum}")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _bytes_sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    if _is_link(path) or not path.is_file():
        raise KernelCostError(f"{label} is not a regular file: {path}")
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise KernelCostError(f"{label} exceeds 1 MiB")
    try:
        value = strict_json_loads(raw)
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise KernelCostError(f"{label} is not strict JSON: {error}") from error
    return _object(value, label), raw


def _safe_relative(value: Any, label: str) -> str:
    text = _string(value, label)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != text
    ):
        raise KernelCostError(f"{label} must be a canonical relative path")
    return text


def _relative_to(root: Path, path: Path, label: str) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise KernelCostError(f"{label} escapes the evidence root: {path}") from error
    return _safe_relative(relative, label)


def _root_path(root: Path, relative: str) -> Path:
    return root / Path(*PurePosixPath(relative).parts)


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _reject_link_components(root: Path, path: Path, label: str) -> None:
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as error:
        raise KernelCostError(f"{label} escapes its root: {path}") from error
    current = root.absolute()
    for part in relative.parts:
        current /= part
        if current.exists() and _is_link(current):
            raise KernelCostError(f"{label} is link-backed: {current}")


def _receipt(root: Path, path: Path, label: str) -> dict[str, str]:
    relative = _relative_to(root, path, label)
    _reject_link_components(root, path, label)
    if not path.is_file():
        raise KernelCostError(f"{label} is missing: {path}")
    return {"path": relative, "sha256": _file_sha(path)}


def _verify_receipt(root: Path, receipt: Any, label: str) -> Path:
    item = _object(receipt, label)
    _expect_keys(item, FILE_RECEIPT_FIELDS, label)
    path = _root_path(root, _safe_relative(item["path"], f"{label}.path"))
    _reject_link_components(root, path, label)
    if not path.is_file() or _file_sha(path) != _sha(item["sha256"], f"{label}.sha256"):
        raise KernelCostError(f"{label} is missing or its SHA-256 changed")
    return path


def load_config(path: Path) -> tuple[dict[str, Any], bytes]:
    root, raw = _read_json(path, "kernel cost config")
    _expect_keys(root, CONFIG_FIELDS, "kernel cost config")
    if root["schema_version"] != SCHEMA_VERSION or root["kind"] != CONFIG_KIND:
        raise KernelCostError("unsupported kernel cost config")
    targets = _array(root["targets"], "config.targets")
    if len(targets) != 2:
        raise KernelCostError("config must define exactly two targets")
    ids: set[str] = set()
    roles: set[str] = set()
    paths: set[str] = set()
    for index, raw_target in enumerate(targets):
        target = _object(raw_target, f"config.targets[{index}]")
        _expect_keys(target, CONFIG_TARGET_FIELDS, f"config.targets[{index}]")
        target_id = _identifier(target["id"], f"config.targets[{index}].id")
        role = _string(target["role"], f"config.targets[{index}].role")
        if role not in {"baseline", "treatment"}:
            raise KernelCostError("target role must be baseline or treatment")
        _string(target["label"], f"config.targets[{index}].label")
        relative = _safe_relative(
            target["required_relative_path"],
            f"config.targets[{index}].required_relative_path",
        )
        if target_id in ids or role in roles or relative in paths:
            raise KernelCostError("target ids, roles, and paths must be unique")
        ids.add(target_id)
        roles.add(role)
        paths.add(relative)
    if roles != {"baseline", "treatment"}:
        raise KernelCostError("config needs one baseline and one treatment")

    metrics = _array(root["metrics"], "config.metrics")
    if len(metrics) != len(METRIC_IDS):
        raise KernelCostError("config must define four cost metrics")
    for index, (raw_metric, expected_id) in enumerate(zip(metrics, METRIC_IDS)):
        metric = _object(raw_metric, f"config.metrics[{index}]")
        _expect_keys(metric, CONFIG_METRIC_FIELDS, f"config.metrics[{index}]")
        if metric["id"] != expected_id:
            raise KernelCostError("config metric order is not canonical")
        _string(metric["label"], f"config.metrics[{index}].label")
        if (
            metric["unit"] != "bytes"
            or metric["direction"] != "lower_is_better"
            or metric["task"] != "task6"
        ):
            raise KernelCostError("kernel cost metric metadata is invalid")
        source = "filesystem" if expected_id == "elf_file_bytes" else "gnu-size"
        if metric["source"] != source:
            raise KernelCostError(f"{expected_id} must use {source}")

    tool = _object(root["tool"], "config.tool")
    _expect_keys(tool, CONFIG_TOOL_FIELDS, "config.tool")
    if (
        tool["output_format"] != "gnu-berkeley-decimal"
        or tool["version_arguments"] != ["--version"]
        or tool["measurement_arguments"] != ["-B"]
    ):
        raise KernelCostError("unsupported size invocation")
    limits = _object(root["limits"], "config.limits")
    _expect_keys(limits, CONFIG_LIMIT_FIELDS, "config.limits")
    timeout = _integer(limits["timeout_seconds"], "config timeout", 1)
    maximum = _integer(limits["max_output_bytes"], "config output limit", 1024)
    if timeout > 60 or maximum > MAX_JSON_BYTES // 4:
        raise KernelCostError("tool limits exceed the allowed maximum")
    return root, raw


def load_environment(path: Path) -> tuple[dict[str, Any], bytes]:
    value, raw = _read_json(path, "environment manifest")
    _expect_keys(value, ENVIRONMENT_FIELDS, "environment manifest")
    if value["schema_version"] != 1 or value["kind"] != ENVIRONMENT_KIND:
        raise KernelCostError("unsupported environment manifest")
    _identifier(value["run_id"], "environment run id")
    if COMMIT_RE.fullmatch(_string(value["source_commit"], "environment commit")) is None:
        raise KernelCostError("environment commit is invalid")
    _identifier(value["environment_id"], "environment id")
    facts = _array(value["facts"], "environment facts")
    if not facts:
        raise KernelCostError("environment facts must not be empty")
    names: list[str] = []
    for index, raw_fact in enumerate(facts):
        fact = _object(raw_fact, f"environment.facts[{index}]")
        _expect_keys(fact, ENVIRONMENT_FACT_FIELDS, f"environment.facts[{index}]")
        names.append(_identifier(fact["name"], f"environment.facts[{index}].name"))
        _string(fact["value"], f"environment.facts[{index}].value")
    if names != sorted(set(names)):
        raise KernelCostError("environment facts must be unique and sorted")
    return value, raw


def load_build_manifest(
    path: Path,
    config: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    value, raw = _read_json(path, "build manifest")
    _expect_keys(value, BUILD_FIELDS, "build manifest")
    if value["schema_version"] != 1 or value["kind"] != BUILD_KIND:
        raise KernelCostError("unsupported build manifest")
    _identifier(value["run_id"], "build run id")
    if COMMIT_RE.fullmatch(_string(value["source_commit"], "build commit")) is None:
        raise KernelCostError("build commit is invalid")
    _sha(value["environment_sha256"], "build environment SHA-256")
    for field in ("build_config", "build_log"):
        receipt = _object(value[field], f"build.{field}")
        _expect_keys(receipt, FILE_RECEIPT_FIELDS, f"build.{field}")
        _safe_relative(receipt["path"], f"build.{field}.path")
        _sha(receipt["sha256"], f"build.{field}.sha256")
    targets = _array(value["targets"], "build.targets")
    if len(targets) != len(config["targets"]):
        raise KernelCostError("build target count differs from config")
    for index, (raw_target, expected) in enumerate(zip(targets, config["targets"])):
        target = _object(raw_target, f"build.targets[{index}]")
        _expect_keys(target, BUILD_TARGET_FIELDS, f"build.targets[{index}]")
        if (
            target["id"] != expected["id"]
            or target["path"] != expected["required_relative_path"]
        ):
            raise KernelCostError("build target violates its trusted role/path binding")
        _sha(target["sha256"], f"build.targets[{index}].sha256")
        command = _array(target["command_argv"], f"build.targets[{index}].command_argv")
        if not command or any(not isinstance(item, str) or not item for item in command):
            raise KernelCostError("build command must contain non-empty strings")
    return value, raw


def parse_elf_identity(path: Path) -> dict[str, Any]:
    """Validate a structurally bounded little-endian RISC-V ELF64 executable."""

    size = path.stat().st_size
    if size < 64:
        raise KernelCostError("ELF is shorter than its 64-bit header")
    with path.open("rb") as handle:
        header = handle.read(64)
    try:
        (
            ident,
            elf_type,
            machine,
            version,
            entry,
            phoff,
            shoff,
            _flags,
            ehsize,
            phentsize,
            phnum,
            shentsize,
            shnum,
            _shstrndx,
        ) = struct.unpack("<16sHHIQQQIHHHHHH", header)
    except struct.error as error:
        raise KernelCostError(f"invalid ELF64 header: {error}") from error
    if ident[:4] != b"\x7fELF" or ident[4:7] != b"\x02\x01\x01":
        raise KernelCostError("ELF identity is not ELF64 little-endian version 1")
    if elf_type != 2 or machine != 243 or version != 1 or ehsize != 64:
        raise KernelCostError("ELF is not a RISC-V ELF64 executable")
    if (
        phnum == 0
        or phentsize != 56
        or phoff < 64
        or phoff > size
        or phnum > (size - phoff) // phentsize
    ):
        raise KernelCostError("ELF program header table escapes the file")
    if shnum and (
        shentsize != 64
        or shoff < 64
        or shoff > size
        or shnum > (size - shoff) // shentsize
    ):
        raise KernelCostError("ELF section header table escapes the file")
    with path.open("rb") as handle:
        handle.seek(phoff)
        program_headers = handle.read(phnum * phentsize)
    executable_load = False
    for index in range(phnum):
        (
            segment_type,
            flags,
            offset,
            virtual_address,
            _physical_address,
            file_bytes,
            memory_bytes,
            _alignment,
        ) = struct.unpack_from("<IIQQQQQQ", program_headers, index * phentsize)
        if file_bytes > memory_bytes or offset > size or file_bytes > size - offset:
            raise KernelCostError("ELF segment escapes the file")
        if (
            segment_type == 1
            and flags & 1
            and virtual_address <= entry < virtual_address + memory_bytes
        ):
            executable_load = True
    if not executable_load:
        raise KernelCostError("ELF entry is not covered by an executable PT_LOAD segment")
    return {
        "class": "ELF64",
        "endianness": "little",
        "machine": "RISC-V",
        "type": "EXEC",
        "header_size": 64,
    }


def parse_size_output(output: bytes, expected_filename: str) -> dict[str, int]:
    try:
        text = output.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise KernelCostError("size output is not UTF-8") from error
    lines = text.splitlines()
    if len(lines) != 2:
        raise KernelCostError("size output must contain exactly two lines")
    if lines[0].split() != ["text", "data", "bss", "dec", "hex", "filename"]:
        raise KernelCostError("size output header is invalid")
    fields = lines[1].split(maxsplit=5)
    if (
        len(fields) != 6
        or any(UINT_RE.fullmatch(item) is None for item in fields[:4])
        or HEX_RE.fullmatch(fields[4]) is None
    ):
        raise KernelCostError("size output row is not canonical and bounded")
    if fields[5] != expected_filename:
        raise KernelCostError("size output filename differs from the requested ELF")
    try:
        text_size, data_size, bss_size, total = (int(item, 10) for item in fields[:4])
        hexadecimal_total = int(fields[4], 16)
    except ValueError as error:
        raise KernelCostError(f"size output integer is invalid: {error}") from error
    if text_size + data_size + bss_size != total or hexadecimal_total != total:
        raise KernelCostError("size output totals are inconsistent")
    return {
        "text_bytes": text_size,
        "data_bytes": data_size,
        "bss_bytes": bss_size,
    }


def _run_tool(argv: Sequence[str], timeout: int, maximum: int) -> ToolExecution:
    """Run a trusted tool with bounded disk-backed stdout and stderr capture."""

    environment = os.environ.copy()
    environment.update(COMMAND_ENVIRONMENT)
    with tempfile.TemporaryDirectory(prefix="agentos-size-output-") as temporary:
        stdout_path = Path(temporary) / "stdout"
        stderr_path = Path(temporary) / "stderr"
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    list(argv),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    env=environment,
                )
                deadline = time.monotonic() + timeout
                error: str | None = None
                while process.poll() is None:
                    if stdout_path.stat().st_size > maximum or stderr_path.stat().st_size > maximum:
                        error = "output_limit_exceeded"
                        process.kill()
                        break
                    if time.monotonic() >= deadline:
                        error = "timeout"
                        process.kill()
                        break
                    time.sleep(0.005)
                returncode = process.wait()
        except OSError as execution_error:
            return ToolExecution(
                None,
                b"",
                str(execution_error).encode("utf-8")[:maximum],
                "execution_error",
            )
        stdout_raw = stdout_path.read_bytes()[: maximum + 1]
        stderr_raw = stderr_path.read_bytes()[: maximum + 1]
        if len(stdout_raw) > maximum or len(stderr_raw) > maximum:
            error = "output_limit_exceeded"
        return ToolExecution(returncode, stdout_raw, stderr_raw, error)


def _command(argv: Sequence[str], execution: ToolExecution) -> dict[str, Any]:
    return {
        "argv": list(argv),
        "returncode": execution.returncode,
        "stdout_base64": base64.b64encode(execution.stdout).decode("ascii"),
        "stderr_base64": base64.b64encode(execution.stderr).decode("ascii"),
        "error": execution.error,
    }


def _command_bytes(command: dict[str, Any], stream: str) -> bytes:
    try:
        encoded = command[f"{stream}_base64"]
        if not isinstance(encoded, str):
            raise KernelCostError(f"command.{stream}_base64 must be a string")
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeError, ValueError) as error:
        raise KernelCostError(f"command {stream} is not canonical base64") from error
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise KernelCostError(f"command {stream} base64 is not canonical")
    return raw


def _validate_command(value: Any, maximum: int, label: str) -> dict[str, Any]:
    command = _object(value, label)
    _expect_keys(command, COMMAND_FIELDS, label)
    argv = _array(command["argv"], f"{label}.argv")
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise KernelCostError(f"{label}.argv is invalid")
    returncode = command["returncode"]
    if returncode is not None and (
        isinstance(returncode, bool) or not isinstance(returncode, int)
    ):
        raise KernelCostError(f"{label}.returncode is invalid")
    for stream in ("stdout", "stderr"):
        if len(_command_bytes(command, stream)) > maximum:
            raise KernelCostError(f"{label}.{stream} exceeds the configured limit")
    if command["error"] is not None:
        _string(command["error"], f"{label}.error")
    return command


def _version(output: bytes) -> str:
    try:
        text = output.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise KernelCostError("size version output is not UTF-8") from error
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first or len(first) > 512 or any(ord(character) < 32 for character in first):
        raise KernelCostError("size version first line is not bounded printable text")
    return first


def _trusted_text(value: Any, label: str, maximum: int = 4096) -> str:
    text = _string(value, label)
    if len(text) > maximum or "\x00" in text or "\r" in text or "\n" in text:
        raise KernelCostError(f"{label} is not bounded single-line text")
    return text


def _trusted_argv(value: Any, label: str) -> list[str]:
    argv = _array(value, label)
    if not argv or len(argv) > 16:
        raise KernelCostError(f"{label} has an invalid argument count")
    return [_trusted_text(item, f"{label}[{index}]") for index, item in enumerate(argv)]


def _trusted_stream(command: dict[str, Any], stream: str, maximum: int, label: str) -> bytes:
    encoded = command[f"{stream}_base64"]
    if not isinstance(encoded, str):
        raise KernelCostError(f"{label}.{stream}_base64 must be a string")
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeError, ValueError) as error:
        raise KernelCostError(f"{label}.{stream}_base64 is invalid") from error
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise KernelCostError(f"{label}.{stream}_base64 is not canonical")
    if len(raw) > maximum:
        raise KernelCostError(f"{label}.{stream} exceeds the trusted build limit")
    if _sha(command[f"{stream}_sha256"], f"{label}.{stream}_sha256") != _bytes_sha(raw):
        raise KernelCostError(f"{label}.{stream} SHA-256 differs from its bytes")
    return raw


def validate_trusted_build_config(
    value: Any,
    kernel_cost_config: dict[str, Any],
    kernel_cost_config_sha256: str,
) -> dict[str, Any]:
    """Validate the public, portable contract emitted by the trusted builder."""

    root = _object(value, "trusted build config")
    _expect_keys(root, TRUSTED_BUILD_CONFIG_FIELDS, "trusted build config")
    if (
        root["schema_version"] != TRUSTED_BUILD_SCHEMA_VERSION
        or root["kind"] != TRUSTED_BUILD_CONFIG_KIND
    ):
        raise KernelCostError("unsupported trusted build config")
    _identifier(root["run_id"], "trusted build config run id")
    if COMMIT_RE.fullmatch(
        _string(root["source_commit"], "trusted build config source commit")
    ) is None:
        raise KernelCostError("trusted build config source commit is invalid")

    config_receipt = _object(
        root["kernel_cost_config"], "trusted build config kernel cost config"
    )
    _expect_keys(
        config_receipt,
        TRUSTED_BUILD_CONFIG_RECEIPT_FIELDS,
        "trusted build config kernel cost config",
    )
    if (
        _safe_relative(config_receipt["path"], "trusted build config path")
        != "ci/evaluation-kernel-cost.json"
        or _sha(config_receipt["sha256"], "trusted build config SHA-256")
        != _sha(kernel_cost_config_sha256, "kernel cost config SHA-256")
    ):
        raise KernelCostError("trusted build config is bound to another kernel cost config")

    make_tool = _object(root["make_tool"], "trusted build config make tool")
    _expect_keys(make_tool, TRUSTED_BUILD_MAKE_FIELDS, "trusted build config make tool")
    make_path = _trusted_text(make_tool["path"], "trusted build make path")
    _sha(make_tool["sha256"], "trusted build make SHA-256")
    make_version_argv = _trusted_argv(
        make_tool["version_argv"], "trusted build make version argv"
    )
    if make_version_argv != [make_path, "--version"]:
        raise KernelCostError("trusted build make version command is not fixed")
    make_version = _trusted_text(make_tool["version"], "trusted build make version", 512)
    if any(ord(character) < 32 for character in make_version):
        raise KernelCostError("trusted build make version is not printable")

    policy = _object(root["command_policy"], "trusted build command policy")
    _expect_keys(policy, TRUSTED_BUILD_POLICY_FIELDS, "trusted build command policy")
    if policy != {
        "timeout_seconds": TRUSTED_BUILD_TIMEOUT_SECONDS,
        "max_output_bytes": TRUSTED_BUILD_MAX_OUTPUT_BYTES,
    }:
        raise KernelCostError("trusted build command policy is not fixed")

    environment = _object(root["environment"], "trusted build environment")
    names = set(environment)
    if (
        not TRUSTED_ENVIRONMENT_REQUIRED.issubset(names)
        or not names.issubset(TRUSTED_ENVIRONMENT_REQUIRED | TRUSTED_ENVIRONMENT_OPTIONAL)
    ):
        raise KernelCostError("trusted build environment variable set is invalid")
    for name, raw in environment.items():
        if not isinstance(name, str) or not name or len(name) > 64:
            raise KernelCostError("trusted build environment name is invalid")
        if not isinstance(raw, str) or len(raw) > 32768 or "\x00" in raw:
            raise KernelCostError(f"trusted build environment {name} is invalid")
    if (
        environment["LANG"] != "C"
        or environment["LC_ALL"] != "C"
        or environment["PYTHONHASHSEED"] != "0"
        or environment["TZ"] != "UTC"
        or not environment["SOURCE_DATE_EPOCH"].isdigit()
    ):
        raise KernelCostError("trusted build deterministic environment is invalid")
    environment_sha = _bytes_sha(_canonical_json(environment))
    if _sha(root["environment_sha256"], "trusted build environment SHA-256") != environment_sha:
        raise KernelCostError("trusted build environment SHA-256 differs")

    configured_targets = _array(kernel_cost_config.get("targets"), "kernel cost config targets")
    if [target.get("role") for target in configured_targets] != ["baseline", "treatment"]:
        raise KernelCostError("trusted build target order must be baseline then treatment")
    targets = _array(root["targets"], "trusted build targets")
    if len(targets) != 2:
        raise KernelCostError("trusted build config must contain two targets")
    for index, (raw_target, configured) in enumerate(zip(targets, configured_targets)):
        label = f"trusted build targets[{index}]"
        target = _object(raw_target, label)
        _expect_keys(target, TRUSTED_BUILD_TARGET_FIELDS, label)
        expected_role = "baseline" if index == 0 else "treatment"
        expected_path = (
            "baseline_ucore/build/kernel" if expected_role == "baseline" else "build/kernel"
        )
        if (
            target["id"] != configured["id"]
            or target["role"] != expected_role
            or target["path"] != configured["required_relative_path"]
            or target["path"] != expected_path
        ):
            raise KernelCostError(f"{label} identity/order/path is not fixed")
        expected_clean = (
            [make_path, "-C", "baseline_ucore", "clean"]
            if expected_role == "baseline"
            else [make_path, "clean"]
        )
        expected_build = (
            [make_path, "-C", "baseline_ucore", "build/kernel"]
            if expected_role == "baseline"
            else [make_path, "build/kernel"]
        )
        if (
            _trusted_argv(target["clean_argv"], f"{label}.clean_argv") != expected_clean
            or _trusted_argv(target["build_argv"], f"{label}.build_argv") != expected_build
        ):
            raise KernelCostError(f"{label} clean/build command is not fixed")
    return root


def validate_trusted_build_log(
    value: Any,
    trusted_config: dict[str, Any],
    build_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the exact five-command transcript and all embedded byte receipts."""

    root = _object(value, "trusted build log")
    _expect_keys(root, TRUSTED_BUILD_LOG_FIELDS, "trusted build log")
    if (
        root["schema_version"] != TRUSTED_BUILD_SCHEMA_VERSION
        or root["kind"] != TRUSTED_BUILD_LOG_KIND
    ):
        raise KernelCostError("unsupported trusted build log")
    for field in ("run_id", "source_commit", "environment_sha256"):
        if root[field] != trusted_config[field]:
            raise KernelCostError(f"trusted build log {field} differs from config")

    make_tool = trusted_config["make_tool"]
    expected_commands: list[tuple[str, str, list[str]]] = [
        ("builder", "make_version", list(make_tool["version_argv"])),
    ]
    for target in trusted_config["targets"]:
        expected_commands.extend(
            [
                (target["id"], "clean", list(target["clean_argv"])),
                (target["id"], "build", list(target["build_argv"])),
            ]
        )
    commands = _array(root["commands"], "trusted build log commands")
    if len(commands) != len(expected_commands) or len(commands) != 5:
        raise KernelCostError("trusted build log must contain exactly five commands")
    for sequence, (raw_command, expected) in enumerate(zip(commands, expected_commands)):
        target_id, phase, argv = expected
        label = f"trusted build log commands[{sequence}]"
        command = _object(raw_command, label)
        _expect_keys(command, TRUSTED_BUILD_COMMAND_FIELDS, label)
        if (
            command["sequence"] != sequence
            or isinstance(command["sequence"], bool)
            or command["target_id"] != target_id
            or command["phase"] != phase
            or command["cwd"] != "."
            or _trusted_argv(command["argv"], f"{label}.argv") != argv
            or command["environment_sha256"] != trusted_config["environment_sha256"]
        ):
            raise KernelCostError(f"{label} sequence/cwd/phase/target/argv differs")
        if (
            type(command["returncode"]) is not int
            or command["returncode"] != 0
            or command["error"] is not None
        ):
            raise KernelCostError(f"{label} is not a successful command")
        duration = _integer(command["duration_ms"], f"{label}.duration_ms")
        if duration > (TRUSTED_BUILD_TIMEOUT_SECONDS + 60) * 1000:
            raise KernelCostError(f"{label}.duration_ms exceeds the command policy")
        stdout = _trusted_stream(
            command, "stdout", TRUSTED_BUILD_MAX_OUTPUT_BYTES, label
        )
        _trusted_stream(command, "stderr", TRUSTED_BUILD_MAX_OUTPUT_BYTES, label)
        if phase == "make_version" and _version(stdout) != make_tool["version"]:
            raise KernelCostError("trusted build make version output differs from config")

    if build_manifest is not None:
        if (
            build_manifest["run_id"] != trusted_config["run_id"]
            or build_manifest["source_commit"] != trusted_config["source_commit"]
        ):
            raise KernelCostError("trusted build manifest identity differs from config")
        build_commands = {
            command["target_id"]: command["argv"]
            for command in commands
            if command["phase"] == "build"
        }
        for target, configured in zip(
            build_manifest["targets"], trusted_config["targets"]
        ):
            if (
                target["id"] != configured["id"]
                or target["command_argv"] != configured["build_argv"]
                or target["command_argv"] != build_commands.get(target["id"])
            ):
                raise KernelCostError(
                    "build manifest command_argv differs from the trusted build log"
                )
    return root


def validate_trusted_build_environment(
    environment_manifest: dict[str, Any],
    trusted_config: dict[str, Any],
) -> dict[str, Any]:
    """Bind the public environment receipt to deterministic inputs and Make."""

    facts = {
        fact["name"]: fact["value"]
        for fact in _array(environment_manifest.get("facts"), "environment facts")
    }
    expected_names = {
        "build_environment_sha256", "builder", "git", "make", "make_path",
        "make_sha256", "platform", "python", "source_date_epoch",
    }
    if set(facts) != expected_names:
        raise KernelCostError("trusted build environment fact set is invalid")
    make_tool = trusted_config["make_tool"]
    expected = {
        "build_environment_sha256": trusted_config["environment_sha256"],
        "builder": f"evaluation_kernel_build.py/{TRUSTED_BUILD_SCHEMA_VERSION}",
        "make": make_tool["version"],
        "make_path": make_tool["path"],
        "make_sha256": make_tool["sha256"],
        "source_date_epoch": trusted_config["environment"]["SOURCE_DATE_EPOCH"],
    }
    if any(facts.get(name) != value for name, value in expected.items()):
        raise KernelCostError("trusted build environment/Make identity differs")
    return environment_manifest


def _verify_trusted_build_sidecars(
    *,
    evidence_root: Path,
    kernel_cost_config: dict[str, Any],
    kernel_cost_config_raw: bytes,
    environment_manifest: dict[str, Any],
    build_manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    config_path = _verify_receipt(
        evidence_root, build_manifest["build_config"], "bound build config"
    )
    log_path = _verify_receipt(
        evidence_root, build_manifest["build_log"], "bound build log"
    )
    trusted_config, _ = _read_json(config_path, "trusted build config")
    trusted_log, _ = _read_json(log_path, "trusted build log")
    validate_trusted_build_config(
        trusted_config,
        kernel_cost_config,
        _bytes_sha(kernel_cost_config_raw),
    )
    if (
        trusted_config["run_id"] != environment_manifest["run_id"]
        or trusted_config["source_commit"] != environment_manifest["source_commit"]
    ):
        raise KernelCostError("trusted build config identity differs from environment")
    validate_trusted_build_log(trusted_log, trusted_config, build_manifest)
    validate_trusted_build_environment(environment_manifest, trusted_config)
    return trusted_config, trusted_log


def _repository_state(root: Path) -> tuple[str, bool]:
    environment = os.environ.copy()
    environment.update(COMMAND_ENVIRONMENT)
    commands = (
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
    )
    outputs: list[str] = []
    for command in commands:
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise KernelCostError(f"cannot inspect repository state: {error}") from error
        if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
            raise KernelCostError("cannot inspect repository state")
        outputs.append(result.stdout.decode("utf-8", errors="strict"))
    head = outputs[0].strip()
    if COMMIT_RE.fullmatch(head) is None:
        raise KernelCostError("repository HEAD is invalid")
    return head, outputs[1] == ""


def _metric(metric_id: str, status: str, value: int | None, reason: str | None) -> dict[str, Any]:
    return {"id": metric_id, "status": status, "value": value, "reason": reason}


def _unavailable_target(target_id: str, path: str, expected_sha: str) -> dict[str, Any]:
    return {
        "id": target_id,
        "status": "unavailable",
        "reason": "source_missing",
        "source": {
            "path": path,
            "expected_sha256": expected_sha,
            "observed_sha256": None,
            "file_size_bytes": None,
            "elf_identity": None,
            "status": "unavailable",
        },
        "size_command": None,
        "metrics": [
            _metric(metric_id, "unavailable", None, "source_missing")
            for metric_id in METRIC_IDS
        ],
    }


def collect_report(
    *,
    config_path: Path,
    repository_root: Path,
    environment_manifest_path: Path,
    build_manifest_path: Path,
    size_tool: Path,
    evidence_root: Path | None = None,
    runner: ToolRunner = _run_tool,
    repository_reader: RepositoryReader = _repository_state,
) -> dict[str, Any]:
    """Collect evidence from prebuilt files bound by strict external manifests."""

    root = repository_root.resolve()
    receipt_root = root if evidence_root is None else evidence_root.resolve()
    if _is_link(evidence_root or repository_root) or not receipt_root.is_dir():
        raise KernelCostError("evidence root must be an existing non-link directory")
    config_path = config_path.resolve()
    environment_manifest_path = environment_manifest_path.resolve()
    build_manifest_path = build_manifest_path.resolve()
    config, config_raw = load_config(config_path)
    environment, environment_raw = load_environment(environment_manifest_path)
    build, build_raw = load_build_manifest(build_manifest_path, config)
    environment_sha = _bytes_sha(environment_raw)
    if (
        build["run_id"] != environment["run_id"]
        or build["source_commit"] != environment["source_commit"]
        or build["environment_sha256"] != environment_sha
    ):
        raise KernelCostError("environment and build manifests are not bound together")
    head, clean = repository_reader(root)
    if head != environment["source_commit"] or clean is not True:
        raise KernelCostError("formal kernel cost collection requires the bound clean HEAD")

    config_receipt = _receipt(receipt_root, config_path, "kernel cost config")
    environment_receipt = _receipt(
        receipt_root, environment_manifest_path, "environment manifest"
    )
    build_receipt = _receipt(receipt_root, build_manifest_path, "build manifest")
    if config_receipt["sha256"] != _bytes_sha(config_raw):
        raise KernelCostError("config changed while being read")
    if environment_receipt["sha256"] != environment_sha:
        raise KernelCostError("environment manifest changed while being read")
    if build_receipt["sha256"] != _bytes_sha(build_raw):
        raise KernelCostError("build manifest changed while being read")
    _verify_trusted_build_sidecars(
        evidence_root=receipt_root,
        kernel_cost_config=config,
        kernel_cost_config_raw=config_raw,
        environment_manifest=environment,
        build_manifest=build,
    )

    limits = config["limits"]
    resolved_tool: Path | None = None
    if not size_tool.is_absolute():
        raise KernelCostError("--size-tool must be an explicit absolute path")
    if size_tool.exists():
        resolved_tool = size_tool.resolve()
        if not resolved_tool.is_file():
            raise KernelCostError("size tool is not a regular file")
        tool_sha = _file_sha(resolved_tool)
        argv = [str(resolved_tool), *config["tool"]["version_arguments"]]
        execution = runner(argv, limits["timeout_seconds"], limits["max_output_bytes"])
        version_command = _command(argv, execution)
        try:
            version = _version(execution.stdout)
        except KernelCostError as error:
            version = None
            tool_reason = str(error)
        else:
            tool_reason = None
        if execution.error is not None or execution.returncode != 0 or version is None:
            tool_status = "unavailable"
            tool_reason = execution.error or tool_reason or "version_command_failed"
            resolved_tool = None
        else:
            tool_status = "available"
    else:
        tool_sha = None
        version = None
        version_command = None
        tool_status = "unavailable"
        tool_reason = "size_tool_missing"
    tool = {
        "status": tool_status,
        "sha256": tool_sha,
        "version": version,
        "version_command": version_command,
        "path_hint": str(size_tool),
        "reason": tool_reason,
    }

    targets: list[dict[str, Any]] = []
    for target_manifest in build["targets"]:
        target_id = target_manifest["id"]
        relative = target_manifest["path"]
        source_path = _root_path(root, relative)
        expected_sha = target_manifest["sha256"]
        if not source_path.exists():
            targets.append(_unavailable_target(target_id, relative, expected_sha))
            continue
        _reject_link_components(root, source_path, f"target {target_id}")
        if not source_path.is_file():
            raise KernelCostError(f"target {target_id} is not a regular file")
        observed_sha = _file_sha(source_path)
        if observed_sha != expected_sha:
            raise KernelCostError(f"target {target_id} differs from its build manifest")
        file_size = source_path.stat().st_size
        try:
            elf_identity = parse_elf_identity(source_path)
        except KernelCostError as error:
            targets.append(
                {
                    "id": target_id,
                    "status": "failed",
                    "reason": str(error),
                    "source": {
                        "path": relative,
                        "expected_sha256": expected_sha,
                        "observed_sha256": observed_sha,
                        "file_size_bytes": file_size,
                        "elf_identity": None,
                        "status": "failed",
                    },
                    "size_command": None,
                    "metrics": [
                        _metric(metric_id, "failed", None, "invalid_elf")
                        for metric_id in METRIC_IDS
                    ],
                }
            )
            continue

        metrics = [_metric("elf_file_bytes", "measured", file_size, None)]
        if resolved_tool is None:
            metrics.extend(
                _metric(metric_id, "unavailable", None, tool_reason)
                for metric_id in METRIC_IDS[1:]
            )
            target_status = "partial"
            target_reason = tool_reason
            size_command = None
        else:
            argv = [
                str(resolved_tool),
                *config["tool"]["measurement_arguments"],
                str(source_path),
            ]
            execution = runner(argv, limits["timeout_seconds"], limits["max_output_bytes"])
            size_command = _command(argv, execution)
            try:
                sizes = parse_size_output(execution.stdout, str(source_path))
            except KernelCostError as error:
                sizes = None
                target_reason = str(error)
            else:
                target_reason = None
            if execution.error is not None or execution.returncode != 0 or sizes is None:
                target_reason = execution.error or target_reason or "size_command_failed"
                metrics.extend(
                    _metric(metric_id, "failed", None, target_reason)
                    for metric_id in METRIC_IDS[1:]
                )
                target_status = "failed"
            else:
                if _file_sha(source_path) != observed_sha:
                    raise KernelCostError(f"target {target_id} changed during collection")
                metrics.extend(
                    _metric(metric_id, "measured", sizes[metric_id], None)
                    for metric_id in METRIC_IDS[1:]
                )
                target_status = "measured"
        targets.append(
            {
                "id": target_id,
                "status": target_status,
                "reason": target_reason,
                "source": {
                    "path": relative,
                    "expected_sha256": expected_sha,
                    "observed_sha256": observed_sha,
                    "file_size_bytes": file_size,
                    "elf_identity": elf_identity,
                    "status": "verified",
                },
                "size_command": size_command,
                "metrics": metrics,
            }
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": REPORT_KIND,
        "binding": {
            "run_id": environment["run_id"],
            "source_commit": environment["source_commit"],
            "environment_sha256": environment_sha,
            "config": config_receipt,
            "environment_manifest": environment_receipt,
            "build_manifest": build_receipt,
        },
        "tool": tool,
        "targets": targets,
        "content_sha256": "",
    }
    report["content_sha256"] = _bytes_sha(
        _canonical_json({key: value for key, value in report.items() if key != "content_sha256"})
    )
    validate_report(report, config)
    return report


def _validate_metric(value: Any, expected_id: str, label: str) -> dict[str, Any]:
    metric = _object(value, label)
    _expect_keys(metric, METRIC_FIELDS, label)
    if metric["id"] != expected_id:
        raise KernelCostError(f"{label}.id is not canonical")
    if metric["status"] == "measured":
        _integer(metric["value"], f"{label}.value")
        if metric["reason"] is not None:
            raise KernelCostError(f"{label}.reason must be null when measured")
    elif metric["status"] in {"unavailable", "failed"}:
        if metric["value"] is not None:
            raise KernelCostError(f"{label}.value must be null when not measured")
        _string(metric["reason"], f"{label}.reason")
    else:
        raise KernelCostError(f"{label}.status is invalid")
    return metric


def validate_report(report: Any, config: dict[str, Any]) -> dict[str, Any]:
    root = _object(report, "report")
    _expect_keys(root, REPORT_FIELDS, "report")
    if root["schema_version"] != 1 or root["kind"] != REPORT_KIND:
        raise KernelCostError("unsupported kernel cost report")
    binding = _object(root["binding"], "report.binding")
    _expect_keys(binding, BINDING_FIELDS, "report.binding")
    _identifier(binding["run_id"], "report run id")
    if COMMIT_RE.fullmatch(_string(binding["source_commit"], "report commit")) is None:
        raise KernelCostError("report commit is invalid")
    _sha(binding["environment_sha256"], "report environment SHA-256")
    for field in ("config", "environment_manifest", "build_manifest"):
        receipt = _object(binding[field], f"report.binding.{field}")
        _expect_keys(receipt, FILE_RECEIPT_FIELDS, f"report.binding.{field}")
        _safe_relative(receipt["path"], f"report.binding.{field}.path")
        _sha(receipt["sha256"], f"report.binding.{field}.sha256")

    maximum = config["limits"]["max_output_bytes"]
    tool = _object(root["tool"], "report.tool")
    _expect_keys(tool, TOOL_FIELDS, "report.tool")
    _string(tool["path_hint"], "report.tool.path_hint")
    if tool["status"] == "available":
        _sha(tool["sha256"], "report.tool.sha256")
        _string(tool["version"], "report.tool.version")
        command = _validate_command(tool["version_command"], maximum, "tool version command")
        if command["returncode"] != 0 or command["error"] is not None:
            raise KernelCostError("available tool has a failed version command")
        if _version(_command_bytes(command, "stdout")) != tool["version"]:
            raise KernelCostError("tool version differs from raw output")
        if tool["reason"] is not None:
            raise KernelCostError("available tool cannot have a reason")
    elif tool["status"] == "unavailable":
        if tool["sha256"] is not None:
            _sha(tool["sha256"], "report.tool.sha256")
        if tool["version_command"] is not None:
            _validate_command(tool["version_command"], maximum, "tool version command")
        if tool["version"] is not None:
            raise KernelCostError("unavailable tool cannot have a version")
        _string(tool["reason"], "report.tool.reason")
    else:
        raise KernelCostError("report.tool.status is invalid")

    targets = _array(root["targets"], "report.targets")
    if len(targets) != len(config["targets"]):
        raise KernelCostError("report target count differs from config")
    for index, (raw_target, target_config) in enumerate(zip(targets, config["targets"])):
        label = f"report.targets[{index}]"
        target = _object(raw_target, label)
        _expect_keys(target, TARGET_FIELDS, label)
        if target["id"] != target_config["id"]:
            raise KernelCostError(f"{label}.id differs from config")
        if target["status"] not in {"measured", "partial", "unavailable", "failed"}:
            raise KernelCostError(f"{label}.status is invalid")
        if target["status"] == "measured":
            if target["reason"] is not None:
                raise KernelCostError(f"{label}.reason must be null when measured")
        else:
            _string(target["reason"], f"{label}.reason")
        source = _object(target["source"], f"{label}.source")
        _expect_keys(source, SOURCE_FIELDS, f"{label}.source")
        if source["path"] != target_config["required_relative_path"]:
            raise KernelCostError(f"{label}.source path violates role binding")
        _sha(source["expected_sha256"], f"{label}.source.expected_sha256")
        if source["status"] == "unavailable":
            if any(
                source[field] is not None
                for field in ("observed_sha256", "file_size_bytes", "elf_identity")
            ):
                raise KernelCostError(f"{label} unavailable source contains values")
        elif source["status"] == "failed":
            _sha(source["observed_sha256"], f"{label}.source.observed_sha256")
            _integer(source["file_size_bytes"], f"{label}.source.file_size_bytes")
            if source["elf_identity"] is not None:
                raise KernelCostError(f"{label} failed source has an ELF identity")
        elif source["status"] == "verified":
            if source["observed_sha256"] != source["expected_sha256"]:
                raise KernelCostError(f"{label} source hash differs from build manifest")
            _sha(source["observed_sha256"], f"{label}.source.observed_sha256")
            _integer(source["file_size_bytes"], f"{label}.source.file_size_bytes")
            identity = _object(source["elf_identity"], f"{label}.source.elf_identity")
            _expect_keys(identity, ELF_FIELDS, f"{label}.source.elf_identity")
            if identity != {
                "class": "ELF64",
                "endianness": "little",
                "machine": "RISC-V",
                "type": "EXEC",
                "header_size": 64,
            }:
                raise KernelCostError(f"{label} ELF identity is invalid")
        else:
            raise KernelCostError(f"{label}.source.status is invalid")

        metrics = _array(target["metrics"], f"{label}.metrics")
        if len(metrics) != len(METRIC_IDS):
            raise KernelCostError(f"{label} metric count is invalid")
        parsed_metrics = [
            _validate_metric(raw_metric, metric_id, f"{label}.metrics[{metric_index}]")
            for metric_index, (raw_metric, metric_id) in enumerate(zip(metrics, METRIC_IDS))
        ]
        statuses = [metric["status"] for metric in parsed_metrics]
        if source["status"] == "unavailable":
            expected_status = "unavailable"
            if statuses != ["unavailable"] * 4 or target["size_command"] is not None:
                raise KernelCostError(f"{label} unavailable relations are invalid")
        elif source["status"] == "failed":
            expected_status = "failed"
            if statuses != ["failed"] * 4 or target["size_command"] is not None:
                raise KernelCostError(f"{label} failed ELF relations are invalid")
        else:
            if (
                parsed_metrics[0]["status"] != "measured"
                or parsed_metrics[0]["value"] != source["file_size_bytes"]
            ):
                raise KernelCostError(f"{label} ELF file size is not source-derived")
            runtime_statuses = set(statuses[1:])
            if runtime_statuses == {"measured"}:
                expected_status = "measured"
                command = _validate_command(target["size_command"], maximum, f"{label}.size_command")
                if command["returncode"] != 0 or command["error"] is not None:
                    raise KernelCostError(f"{label} measured command failed")
                sizes = parse_size_output(
                    _command_bytes(command, "stdout"), command["argv"][-1]
                )
                for metric in parsed_metrics[1:]:
                    if metric["value"] != sizes[metric["id"]]:
                        raise KernelCostError(f"{label} metric differs from raw size output")
            elif runtime_statuses == {"unavailable"}:
                expected_status = "partial"
                if target["size_command"] is not None:
                    raise KernelCostError(f"{label} unavailable runtime has a command")
            elif runtime_statuses == {"failed"}:
                expected_status = "failed"
                command = _validate_command(target["size_command"], maximum, f"{label}.size_command")
                if command["returncode"] == 0 and command["error"] is None:
                    try:
                        parse_size_output(_command_bytes(command, "stdout"), command["argv"][-1])
                    except KernelCostError:
                        pass
                    else:
                        raise KernelCostError(f"{label} marks valid size output failed")
            else:
                raise KernelCostError(f"{label} runtime statuses are inconsistent")
        if target["status"] != expected_status:
            raise KernelCostError(f"{label}.status is not metric-derived")

    content_sha = _sha(root["content_sha256"], "report.content_sha256")
    expected = _bytes_sha(
        _canonical_json({key: value for key, value in root.items() if key != "content_sha256"})
    )
    if content_sha != expected:
        raise KernelCostError("report content SHA-256 differs from its payload")
    return root


def verify_portable(
    report_path: Path,
    config_path: Path,
    evidence_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Verify a relocated evidence package without requiring ELF files or tools."""

    root = evidence_root.resolve()
    config, config_raw = load_config(config_path)
    report, _ = _read_json(report_path, "kernel cost report")
    validate_report(report, config)
    binding = report["binding"]
    config_receipt_path = _verify_receipt(root, binding["config"], "bound config")
    if config_receipt_path.resolve() != config_path.resolve() or _bytes_sha(config_raw) != binding["config"]["sha256"]:
        raise KernelCostError("supplied config differs from the report binding")
    environment_path = _verify_receipt(
        root, binding["environment_manifest"], "bound environment manifest"
    )
    build_path = _verify_receipt(root, binding["build_manifest"], "bound build manifest")
    environment, environment_raw = load_environment(environment_path)
    build, _ = load_build_manifest(build_path, config)
    if (
        binding["run_id"] != environment["run_id"]
        or binding["source_commit"] != environment["source_commit"]
        or binding["environment_sha256"] != _bytes_sha(environment_raw)
        or build["run_id"] != binding["run_id"]
        or build["source_commit"] != binding["source_commit"]
        or build["environment_sha256"] != binding["environment_sha256"]
    ):
        raise KernelCostError("report, environment, and build bindings differ")
    _verify_trusted_build_sidecars(
        evidence_root=root,
        kernel_cost_config=config,
        kernel_cost_config_raw=config_raw,
        environment_manifest=environment,
        build_manifest=build,
    )
    for target, manifest_target in zip(report["targets"], build["targets"]):
        if (
            target["id"] != manifest_target["id"]
            or target["source"]["path"] != manifest_target["path"]
            or target["source"]["expected_sha256"] != manifest_target["sha256"]
        ):
            raise KernelCostError("report target differs from the build manifest")
    return report, environment, build


def verify_local(
    report_path: Path,
    config_path: Path,
    evidence_root: Path,
    repository_root: Path,
    size_tool: Path,
    *,
    runner: ToolRunner = _run_tool,
    repository_reader: RepositoryReader = _repository_state,
) -> dict[str, Any]:
    """Add clean-HEAD, artifact, ELF, tool-hash, and command replay checks."""

    report, _environment, _build = verify_portable(
        report_path, config_path, evidence_root
    )
    config, _ = load_config(config_path)
    head, clean = repository_reader(repository_root.resolve())
    if head != report["binding"]["source_commit"] or clean is not True:
        raise KernelCostError("local repository is not the bound clean HEAD")
    for target in report["targets"]:
        path = _root_path(repository_root.resolve(), target["source"]["path"])
        if target["source"]["status"] == "unavailable":
            if path.exists():
                raise KernelCostError(f"previously unavailable target now exists: {path}")
            continue
        _reject_link_components(repository_root.resolve(), path, f"local target {target['id']}")
        if not path.is_file() or _file_sha(path) != target["source"]["observed_sha256"]:
            raise KernelCostError(f"local target changed: {target['id']}")
        if target["source"]["status"] == "verified":
            if parse_elf_identity(path) != target["source"]["elf_identity"]:
                raise KernelCostError(f"local ELF identity changed: {target['id']}")

    if report["tool"]["status"] == "available":
        resolved_tool = size_tool.resolve()
        if not resolved_tool.is_file() or _file_sha(resolved_tool) != report["tool"]["sha256"]:
            raise KernelCostError("local size tool differs from the report")
        limits = config["limits"]
        for target in report["targets"]:
            if target["status"] != "measured":
                continue
            source = _root_path(repository_root.resolve(), target["source"]["path"])
            argv = [str(resolved_tool), *config["tool"]["measurement_arguments"], str(source)]
            execution = runner(argv, limits["timeout_seconds"], limits["max_output_bytes"])
            if execution.returncode != 0 or execution.error is not None:
                raise KernelCostError(f"local size replay failed: {target['id']}")
            current = parse_size_output(execution.stdout, str(source))
            recorded = {metric["id"]: metric["value"] for metric in target["metrics"]}
            if any(current[metric_id] != recorded[metric_id] for metric_id in METRIC_IDS[1:]):
                raise KernelCostError(f"local size replay differs: {target['id']}")
    return report


def build_dashboard_fragment(
    report_path: Path,
    config_path: Path,
    evidence_root: Path,
) -> dict[str, Any]:
    report, _environment, _build = verify_portable(
        report_path, config_path, evidence_root
    )
    config, _ = load_config(config_path)
    baseline = next(item["id"] for item in config["targets"] if item["role"] == "baseline")
    treatment = next(item["id"] for item in config["targets"] if item["role"] == "treatment")
    target_map = {target["id"]: target for target in report["targets"]}
    benchmarks: list[dict[str, Any]] = []
    for metric_config in config["metrics"]:
        metric_id = metric_config["id"]
        values: dict[str, int] = {}
        statuses: list[str] = []
        for target_id in (baseline, treatment):
            metric = next(
                item for item in target_map[target_id]["metrics"] if item["id"] == metric_id
            )
            statuses.append(metric["status"])
            if metric["status"] == "measured":
                values[target_id] = metric["value"]
        if statuses == ["measured", "measured"]:
            status = "measured"
            loads = ["kernel"]
            estimates = [
                {
                    "target_id": target_id,
                    "load": "kernel",
                    "value": values[target_id],
                    "lower": values[target_id],
                    "upper": values[target_id],
                    "n": 1,
                    "p95": values[target_id],
                }
                for target_id in (baseline, treatment)
            ]
            samples = [
                {
                    "target_id": target_id,
                    "load": "kernel",
                    "trial": 1,
                    "value": values[target_id],
                    "evidence_id": "kernel-cost-report",
                }
                for target_id in (baseline, treatment)
            ]
            improvement = (
                values[baseline] - values[treatment]
                if metric_config["direction"] == "lower_is_better"
                else values[treatment] - values[baseline]
            )
            relative = (
                improvement * 100.0 / values[baseline]
                if values[baseline] != 0
                else None
            )
            wins = int(improvement > 0)
            losses = int(improvement < 0)
            ties = int(improvement == 0)
            sign_n = wins + losses
            if wins == 1:
                numerator, denominator, p_value = 1, 2, 0.5
            else:
                numerator, denominator, p_value = 1, 1, 1.0
            paired = [
                {
                    "load": "kernel",
                    "status": "measured",
                    "n": 1,
                    "median": improvement,
                    "p95": improvement,
                    "ci_low": improvement,
                    "ci_high": improvement,
                    "relative_median_percent": relative,
                    "relative_ci_low": relative,
                    "relative_ci_high": relative,
                    "sign_test": {
                        "alternative": "treatment_better",
                        "wins": wins,
                        "losses": losses,
                        "ties": ties,
                        "n": sign_n,
                        "p_value": p_value,
                        "numerator": numerator,
                        "denominator": denominator,
                    },
                    "samples": [
                        {
                            "trial": 1,
                            "baseline_value": values[baseline],
                            "treatment_value": values[treatment],
                            "value": improvement,
                            "relative_percent": relative,
                            "inner_pairs": [
                                {
                                    "pair": 1,
                                    "baseline_value": values[baseline],
                                    "treatment_value": values[treatment],
                                    "value": improvement,
                                    "relative_percent": relative,
                                }
                            ],
                        }
                    ],
                }
            ]
        else:
            status = "failed" if "failed" in statuses else "unavailable"
            loads, estimates, samples = [], [], []
            paired = []
        benchmarks.append(
            {
                "id": f"kernel-cost-{metric_id.replace('_', '-')}",
                "label": metric_config["label"],
                "task": metric_config["task"],
                "baseline": baseline,
                "treatment": treatment,
                "unit": metric_config["unit"],
                "direction": metric_config["direction"],
                "claim_gate": None,
                "loads": loads,
                "estimates": estimates,
                "samples": samples,
                "paired": paired,
                "diagnostics": [],
                "evidence_ids": ["kernel-cost-report"],
                "status": status,
                "cache_policy": "not-applicable-artifact-measurement",
            }
        )
    report_relative = _relative_to(
        evidence_root.resolve(), report_path.resolve(), "kernel cost report"
    )
    benchmark_statuses = {benchmark["status"] for benchmark in benchmarks}
    if "failed" in benchmark_statuses:
        run_status = "failed"
    elif "unavailable" in benchmark_statuses:
        run_status = "unavailable"
    else:
        run_status = "measured"
    return {
        "schema_version": 1,
        "kind": FRAGMENT_KIND,
        "run": {
            "id": report["binding"]["run_id"],
            "commit": report["binding"]["source_commit"],
            "environment_sha256": report["binding"]["environment_sha256"],
            "run_plan_sha256": report["binding"]["build_manifest"]["sha256"],
            "status": run_status,
        },
        "targets": [
            {"id": item["id"], "label": item["label"], "role": item["role"]}
            for item in config["targets"]
        ],
        "benchmarks": benchmarks,
        "evidence": [
            {
                "id": "kernel-cost-report",
                "label": "Portable kernel cost evidence",
                "status": "verified",
                "kind": "kernel-cost-report",
                "source": report_relative,
                "path": report_relative,
                "sha256": _file_sha(report_path),
            }
        ],
        "methodology": {
            "design": "same-commit prebuilt artifact cost comparison",
            "unit_of_observation": "one build-manifest-bound kernel ELF per target",
            "limitations": [
                "Artifact size is a system cost, not evidence of CPU performance.",
                "A single deterministic build does not establish cross-toolchain behavior.",
            ],
        },
    }


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    data = json.dumps(
        value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") == data:
            return
        raise KernelCostError(f"refusing to replace evidence output: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect or verify portable, read-only kernel cost evidence."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect")
    collect.add_argument("--config", required=True, type=Path)
    collect.add_argument("--repository-root", required=True, type=Path)
    collect.add_argument("--environment-manifest", required=True, type=Path)
    collect.add_argument("--build-manifest", required=True, type=Path)
    collect.add_argument("--size-tool", required=True, type=Path)
    collect.add_argument("--evidence-root", type=Path)
    collect.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--config", required=True, type=Path)
    verify.add_argument("--report", required=True, type=Path)
    verify.add_argument("--evidence-root", required=True, type=Path)
    local = commands.add_parser("verify-local")
    local.add_argument("--config", required=True, type=Path)
    local.add_argument("--report", required=True, type=Path)
    local.add_argument("--evidence-root", required=True, type=Path)
    local.add_argument("--repository-root", required=True, type=Path)
    local.add_argument("--size-tool", required=True, type=Path)
    fragment = commands.add_parser("fragment")
    fragment.add_argument("--config", required=True, type=Path)
    fragment.add_argument("--report", required=True, type=Path)
    fragment.add_argument("--evidence-root", required=True, type=Path)
    fragment.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "collect":
            report = collect_report(
                config_path=args.config,
                repository_root=args.repository_root,
                environment_manifest_path=args.environment_manifest,
                build_manifest_path=args.build_manifest,
                size_tool=args.size_tool,
                evidence_root=args.evidence_root,
            )
            _atomic_json(args.output, report)
            print(f"kernel cost report: {args.output.resolve()}")
        elif args.command == "verify":
            report, _, _ = verify_portable(
                args.report, args.config, args.evidence_root
            )
            print(f"kernel cost report verified: {report['content_sha256']}")
        elif args.command == "verify-local":
            report = verify_local(
                args.report,
                args.config,
                args.evidence_root,
                args.repository_root,
                args.size_tool,
            )
            print(f"kernel cost local replay verified: {report['content_sha256']}")
        else:
            fragment = build_dashboard_fragment(
                args.report, args.config, args.evidence_root
            )
            _atomic_json(args.output, fragment)
            print(f"kernel cost dashboard fragment: {args.output.resolve()}")
    except (KernelCostError, OSError, UnicodeError) as error:
        print(f"kernel cost evidence error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
