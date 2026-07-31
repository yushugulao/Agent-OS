#!/usr/bin/env python3
"""Resolve the repository's GitLab CI inheritance and validate effective jobs."""
from __future__ import annotations

import argparse
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from strict_json import read_strict_json


class CIContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class Field:
    inline: str
    body: tuple[str, ...]

    def scalar(self) -> str:
        if not self.inline or any(line.strip() for line in self.body):
            raise CIContractError("CI field is not a scalar")
        value = self.inline.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value

    def items(self) -> tuple[str, ...]:
        value = self.inline.strip()
        if value:
            if value == "[]":
                return ()
            if value.startswith("[") and value.endswith("]"):
                return tuple(item.strip().strip("\"'") for item in value[1:-1].split(",") if item.strip())
            raise CIContractError(f"CI field is not a list: {value}")
        items: list[str] = []
        for line in self.body:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = re.fullmatch(r" {4}-[ ]+([^#]+?)[ ]*", line)
            if match is None:
                raise CIContractError("CI list has nested or malformed content")
            items.append(match.group(1).strip().strip("\"'"))
        return tuple(items)

    def text(self) -> str:
        return "\n".join(part for part in (self.inline, *(line.strip() for line in self.body)) if part)

    def script_items(self) -> tuple[str, ...]:
        """Decode the small, deliberately restricted YAML script sequence."""
        if self.inline.strip():
            raise CIContractError("CI script must use a block sequence")
        result: list[str] = []
        index = 0
        while index < len(self.body):
            line = self.body[index]
            if not line.strip() or line.lstrip().startswith("#"):
                index += 1
                continue
            match = re.fullmatch(r" {4}-[ ]+(.+?)[ ]*", line)
            if match is None:
                raise CIContractError("CI script has nested or malformed content")
            value = match.group(1)
            index += 1
            if value in {">", ">-"}:
                folded: list[str] = []
                while index < len(self.body) and not re.match(r"^ {4}-[ ]+", self.body[index]):
                    continuation = self.body[index]
                    if continuation.strip() and not continuation.startswith("      "):
                        raise CIContractError("CI folded script has invalid indentation")
                    folded.append(continuation[6:] if continuation.startswith("      ") else "")
                    index += 1
                value = " ".join(part.strip() for part in folded if part.strip())
            elif value in {"|", "|-"}:
                raise CIContractError("literal multiline CI scripts are forbidden")
            elif index < len(self.body) and self.body[index].startswith("      "):
                raise CIContractError("plain CI script item has an unexpected continuation")
            if not value:
                raise CIContractError("CI script contains an empty command")
            result.append(value)
        if not result:
            raise CIContractError("CI script is empty")
        return tuple(result)


@dataclass(frozen=True)
class Definition:
    name: str
    fields: dict[str, Field]

    def parents(self) -> tuple[str, ...]:
        field = self.fields.get("extends")
        if field is None:
            return ()
        try:
            return (field.scalar(),)
        except CIContractError:
            return field.items()


@dataclass(frozen=True)
class EffectiveJob:
    name: str
    fields: dict[str, Field]
    lineage: tuple[str, ...]

    def field(self, name: str) -> Field:
        if name not in self.fields:
            raise CIContractError(f"effective job {self.name} lacks {name}")
        return self.fields[name]


@dataclass(frozen=True)
class ShellCall:
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    unset: tuple[str, ...]

    def env(self) -> dict[str, str]:
        return dict(self.environment)


class CIConfig:
    def __init__(self, definitions: dict[str, Definition], roots: dict[str, tuple[str, ...]]):
        self.definitions = definitions
        self.roots = roots
        self._cache: dict[str, EffectiveJob] = {}

    def effective(self, name: str, stack: tuple[str, ...] = ()) -> EffectiveJob:
        if name in self._cache:
            return self._cache[name]
        if name not in self.definitions:
            raise CIContractError(f"unknown CI parent or job: {name}")
        if name in stack:
            raise CIContractError(f"CI extends cycle: {' -> '.join((*stack, name))}")
        definition = self.definitions[name]
        merged: dict[str, Field] = {}
        lineage: list[str] = []
        for parent in definition.parents():
            resolved = self.effective(parent, (*stack, name))
            merged.update(resolved.fields)
            lineage.extend(resolved.lineage)
        merged.update({key: value for key, value in definition.fields.items() if key != "extends"})
        result = EffectiveJob(name, merged, tuple(dict.fromkeys((*lineage, name))))
        self._cache[name] = result
        return result

    def resolve_all(self) -> None:
        for name in self.definitions:
            self.effective(name)


ROOT_FIELDS = {"stages", "variables"}
FORBIDDEN_ROOT_FIELDS = {"workflow", "default", "include"}


def _parse_fields(name: str, lines: list[str]) -> dict[str, Field]:
    fields: dict[str, Field] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        match = re.match(r"^ {2}([A-Za-z_][A-Za-z0-9_-]*):(?:[ ]*(.*))?$", line)
        if match is None:
            raise CIContractError(f"malformed top-level field in {name}: {line}")
        key, inline = match.group(1), (match.group(2) or "")
        if key in fields:
            raise CIContractError(f"duplicate CI field {name}.{key}")
        index += 1
        body: list[str] = []
        while index < len(lines) and not re.match(r"^ {2}[A-Za-z_][A-Za-z0-9_-]*:", lines[index]):
            body.append(lines[index])
            index += 1
        fields[key] = Field(inline, tuple(body))
    return fields


def parse_ci(text: str) -> CIConfig:
    if "\t" in text:
        raise CIContractError("tabs are forbidden in the CI contract")
    lines = text.splitlines()
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+):[ ]*", line)
        if match:
            current = match.group(1)
            if current in blocks:
                raise CIContractError(f"duplicate CI definition: {current}")
            blocks[current] = []
        elif line and not line[0].isspace() and not line.startswith("#"):
            raise CIContractError(f"unsupported top-level CI syntax: {line}")
        elif current is not None:
            blocks[current].append(line)
    forbidden = FORBIDDEN_ROOT_FIELDS.intersection(blocks)
    if forbidden:
        raise CIContractError(f"unsupported root CI policy: {sorted(forbidden)}")
    definitions = {
        name: Definition(name, _parse_fields(name, body))
        for name, body in blocks.items()
        if name not in ROOT_FIELDS
    }
    if not definitions:
        raise CIContractError("CI file contains no job definitions")
    return CIConfig(
        definitions,
        {name: tuple(blocks[name]) for name in ROOT_FIELDS if name in blocks},
    )


def load_ci(path: Path) -> CIConfig:
    if not path.is_file() or path.is_symlink():
        raise CIContractError(f"CI file is missing or unsafe: {path}")
    try:
        config = parse_ci(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise CIContractError(f"CI file is not readable UTF-8: {error}") from error
    config.resolve_all()
    return config


_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.DOTALL)
_FORBIDDEN_SHELL_WORDS = {
    "break", "continue", "eval", "exec", "exit", "return", "set", "source", "trap", ".",
}


def _shell_tokens(command: str) -> list[str]:
    if "\n" in command or "\r" in command or "$(" in command or "`" in command:
        raise CIContractError("CI command contains dynamic or multiline shell control")
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";|")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        tokens = list(lexer)
    except ValueError as error:
        raise CIContractError(f"CI command is not valid shell syntax: {error}") from error
    if not tokens:
        raise CIContractError("CI command is empty after shell parsing")
    forbidden = {";", "&&", "||", "&"}
    if forbidden.intersection(tokens):
        raise CIContractError("CI command may not short-circuit or detach required work")
    return tokens


def _split_pipeline(tokens: list[str], allow_pipeline: bool) -> list[list[str]]:
    positions = [index for index, token in enumerate(tokens) if token == "|"]
    if positions and (not allow_pipeline or len(positions) != 1):
        raise CIContractError("CI command has an unsupported pipeline")
    if not positions:
        return [tokens]
    position = positions[0]
    if position == 0 or position == len(tokens) - 1:
        raise CIContractError("CI command has an incomplete pipeline")
    return [tokens[:position], tokens[position + 1:]]


def _simple_call(tokens: list[str], inherited_env: dict[str, str] | None = None,
                 inherited_unset: tuple[str, ...] = ()) -> tuple[ShellCall, list[ShellCall]]:
    environment = dict(inherited_env or {})
    unset = list(inherited_unset)
    index = 0
    while index < len(tokens):
        assignment = _ASSIGNMENT.fullmatch(tokens[index])
        if assignment is None:
            break
        environment[assignment.group(1)] = assignment.group(2)
        index += 1
    if index >= len(tokens):
        raise CIContractError("CI script item only assigns variables")
    if tokens[index] == "env":
        index += 1
        while index < len(tokens):
            if tokens[index] == "-u":
                if index + 1 >= len(tokens) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tokens[index + 1]):
                    raise CIContractError("CI env -u is malformed")
                unset.append(tokens[index + 1])
                index += 2
                continue
            assignment = _ASSIGNMENT.fullmatch(tokens[index])
            if assignment is None:
                break
            environment[assignment.group(1)] = assignment.group(2)
            index += 1
        if index >= len(tokens):
            raise CIContractError("CI env command lacks an executable")
    executable = tokens[index]
    argv = tuple(tokens[index:])
    if executable in _FORBIDDEN_SHELL_WORDS:
        raise CIContractError(f"CI script uses forbidden shell control: {executable}")
    if executable == "export":
        if len(argv) < 2 or any(_ASSIGNMENT.fullmatch(word) is None for word in argv[1:]):
            raise CIContractError("CI export must contain only explicit assignments")
        call = ShellCall(argv, tuple(sorted(environment.items())), tuple(unset))
        return call, [call]
    call = ShellCall(argv, tuple(sorted(environment.items())), tuple(unset))
    if executable != "bash" or "-c" not in argv:
        return call, [call]
    c_index = argv.index("-c")
    if c_index + 2 != len(argv) or tuple(argv[1:c_index]) != ("-o", "pipefail"):
        raise CIContractError("nested bash must use exactly -o pipefail -c")
    nested = _analyse_shell(argv[c_index + 1], allow_pipeline=True, environment=environment,
                            unset=tuple(unset))
    return call, nested


def _analyse_shell(command: str, *, allow_pipeline: bool = False,
                   environment: dict[str, str] | None = None,
                   unset: tuple[str, ...] = ()) -> list[ShellCall]:
    calls: list[ShellCall] = []
    for segment in _split_pipeline(_shell_tokens(command), allow_pipeline):
        _outer, inner = _simple_call(segment, environment, unset)
        calls.extend(inner)
    return calls


def _job_calls(job: EffectiveJob) -> tuple[tuple[ShellCall, ...], tuple[ShellCall, ...]]:
    before: list[ShellCall] = []
    if "before_script" in job.fields:
        for command in job.field("before_script").script_items():
            before.extend(_analyse_shell(command))
    script: list[ShellCall] = []
    for command in job.field("script").script_items():
        script.extend(_analyse_shell(command))
    return tuple(before), tuple(script)


def _require_call(calls: tuple[ShellCall, ...], executable: str, *arguments: str,
                  after: int = -1) -> int:
    for index in range(after + 1, len(calls)):
        argv = calls[index].argv
        if argv and argv[0] == executable and all(argument in argv[1:] for argument in arguments):
            return index
    raise CIContractError(f"effective script lacks executable call: {executable} {' '.join(arguments)}")


def _validate_execution_policy(job: EffectiveJob) -> tuple[ShellCall, ...]:
    if "allow_failure" in job.fields and job.field("allow_failure").scalar() != "false":
        raise CIContractError(f"effective job {job.name} may not allow failure")
    if "rules" in job.fields:
        raise CIContractError(f"effective job {job.name} uses unsupported rule-level failure policy")
    forbidden = {"only", "except", "when", "start_in", "trigger", "parallel"}
    present = sorted(forbidden.intersection(job.fields))
    if present:
        raise CIContractError(f"effective job {job.name} uses skip-capable policy: {present}")
    if "after_script" in job.fields and job.field("after_script").script_items():
        raise CIContractError(f"effective job {job.name} may not mutate artifacts after attestation")
    _before, script = _job_calls(job)
    marker_argv = ("python3", "host_tools/remote_ci_evidence.py", "attest", "--job", job.name,
                   "--artifact-root", "ci-artifacts")
    if not script or script[-1].argv != marker_argv:
        raise CIContractError(f"effective job {job.name} lacks terminal artifact attestation")
    return script


QEMU_JOBS = (
    "reader-e2e", "agent-regression", "kernel-mechanism-regression",
    "physical-resource-regression", "metadata-recovery-regression",
    "observe-recovery-regression", "virtio-disk-regression",
    "fs-allocator-fault-regression",
)
MECHANISM_COMMANDS = {
    "kernel-mechanism-regression": (
        "run-proc-reap-tests.sh", "run-syscall-fairness-tests.sh",
        "run-file-resource-tests.sh", "run-thread-resource-tests.sh",
        "run-workflow-teardown-race-tests.sh", "run-fs-enospc-tests.sh",
    ),
    "physical-resource-regression": ("run-physical-resource-tests.sh",),
    "metadata-recovery-regression": ("run-metadata-recovery-tests.sh",),
    "observe-recovery-regression": ("run-observe-recovery-tests.sh",),
    "virtio-disk-regression": ("run-virtio-disk-tests.sh",),
    "fs-allocator-fault-regression": ("run-fs-allocator-fault-tests.sh",),
}


REQUIRED_DEFINITIONS = {
    ".agentos-toolchain",
    ".agentos-mechanism",
    "kernel-budgets",
    "reader-e2e",
    "agent-regression",
    *QEMU_JOBS[2:],
}
ROOT_VARIABLES = {
    "DEBIAN_FRONTEND": "noninteractive",
    "TOOLPREFIX": "riscv64-linux-gnu-",
}
MECHANISM_VARIABLES = {
    "QEMU": "qemu-system-riscv64",
    "PYTHON_BIN": "python3",
    "CASE_TIMEOUT": "300s",
    "IDLE_NOTICE_SECONDS": "20s",
    "MARKER_GRACE_SECONDS": "5s",
}
TOOLCHAIN_FIELDS = {"image", "before_script"}
MECHANISM_ANCHOR_FIELDS = {
    "extends", "stage", "tags", "resource_group", "dependencies", "variables", "artifacts"
}
STANDALONE_FIELDS = {
    "extends", "stage", "tags", "timeout", "script", "artifacts"
}
QEMU_STANDALONE_FIELDS = STANDALONE_FIELDS | {"resource_group", "dependencies"}
MECHANISM_JOB_FIELDS = {"extends", "timeout", "script"}


def _call(*argv: str, env: dict[str, str] | None = None,
          unset: tuple[str, ...] = ()) -> ShellCall:
    return ShellCall(tuple(argv), tuple(sorted((env or {}).items())), unset)


TOOLCHAIN_CALLS = (
    _call("apt-get", "update"),
    _call(
        "apt-get", "install", "-y", "--no-install-recommends",
        "build-essential", "make", "python3", "git",
        "gcc-riscv64-linux-gnu=4:15.2.0-5ubuntu1",
        "gcc-15-riscv64-linux-gnu=15.2.0-16ubuntu1cross1",
        "cpp-15-riscv64-linux-gnu=15.2.0-16ubuntu1cross1",
        "libgcc-15-dev-riscv64-cross=15.2.0-16ubuntu1cross1",
        "binutils-riscv64-linux-gnu=2.46-3ubuntu2",
        "qemu-system-riscv=1:10.2.1+ds-1ubuntu3",
        "opensbi=1.8.1-1",
    ),
)


def _attest_call(job: str) -> ShellCall:
    return _call(
        "python3", "host_tools/remote_ci_evidence.py", "attest",
        "--job", job, "--artifact-root", "ci-artifacts",
    )


EXPECTED_SCRIPT_CALLS: dict[str, tuple[ShellCall, ...]] = {
    "kernel-budgets": (
        _call("mkdir", "-p", "ci-artifacts"),
        _call("bash", "scripts/verify-dual-target-structure.sh", "2>&1"),
        _call("tee", "ci-artifacts/kernel-budgets.log"),
        _call(
            "make", "ci-check", "2>&1",
            env={
                "AGENTOS_ALLOW_UNSANITIZED_HOST_PROBES": "0",
                "HOST_CC": "cc",
                "HOSTCC": "cc",
                "CC": "cc",
            },
        ),
        _call("tee", "-a", "ci-artifacts/kernel-budgets.log"),
        _attest_call("kernel-budgets"),
    ),
    "reader-e2e": (
        _call("mkdir", "-p", "ci-artifacts"),
        _call(
            "python3", "host_tools/test_plain_ucore_reader_e2e.py", "2>&1",
            env={
                "QEMU": "qemu-system-riscv64",
                "PYTHONUNBUFFERED": "1",
                "PLAIN_UCORE_READER_E2E_LOG_DIR": "ci-artifacts/reader-e2e-raw",
            },
        ),
        _call("tee", "ci-artifacts/reader-e2e.log"),
        _attest_call("reader-e2e"),
    ),
    "agent-regression": (
        _call("mkdir", "-p", "ci-artifacts"),
        _call(": > ci-artifacts/agent-suite-timings.log"),
        _call(": > ci-artifacts/agent-suite-guest.log"),
        _call(
            "bash", "scripts/run-agent-tests.sh", "2>&1",
            env={
                "REQUIRE_FULL_SUITE": "1",
                "AGENT_TEST_CALIBRATE": "0",
                "AGENT_TEST_DURATION_PROFILE": "none",
                "AGENT_TEST_TIMING_FILE": "ci-artifacts/agent-suite-timings.log",
                "AGENT_TEST_GUEST_LOG_FILE": "ci-artifacts/agent-suite-guest.log",
                "TOOLPREFIX": "riscv64-linux-gnu-",
                "LOG": "error",
                "CHAPTER": "agent",
                "QEMU": "qemu-system-riscv64",
                "PYTHON_BIN": "python3",
                "CASE_TIMEOUT": "300s",
                "IDLE_NOTICE_SECONDS": "20",
                "MARKER_GRACE_SECONDS": "2s",
            },
            unset=("AGENT_TEST_CASE",),
        ),
        _call("tee", "ci-artifacts/agent-regression-job.log"),
        _attest_call("agent-regression"),
    ),
    "kernel-mechanism-regression": (
        _call("bash", "scripts/run-ci-mechanism.sh", "proc-reap", "scripts/run-proc-reap-tests.sh"),
        _call("bash", "scripts/run-ci-mechanism.sh", "syscall-fairness", "scripts/run-syscall-fairness-tests.sh"),
        _call("bash", "scripts/run-ci-mechanism.sh", "file-resource", "scripts/run-file-resource-tests.sh"),
        _call("bash", "scripts/run-ci-mechanism.sh", "thread-resource", "scripts/run-thread-resource-tests.sh"),
        _call(
            "bash", "scripts/run-ci-mechanism.sh", "workflow-teardown-race",
            "scripts/run-workflow-teardown-race-tests.sh",
            env={
                "WORKFLOW_TEARDOWN_LOG_DIR": "${CI_PROJECT_DIR}/.workflow-teardown-race",
                "WORKFLOW_TEARDOWN_STABILITY_RUNS": "3",
            },
        ),
        _call("bash", "scripts/run-ci-mechanism.sh", "fs-enospc", "scripts/run-fs-enospc-tests.sh"),
        _attest_call("kernel-mechanism-regression"),
    ),
    "physical-resource-regression": (
        _call("bash", "scripts/run-ci-mechanism.sh", "physical-resource", "scripts/run-physical-resource-tests.sh"),
        _attest_call("physical-resource-regression"),
    ),
    "metadata-recovery-regression": (
        _call("bash", "scripts/run-ci-mechanism.sh", "metadata-recovery", "scripts/run-metadata-recovery-tests.sh"),
        _attest_call("metadata-recovery-regression"),
    ),
    "observe-recovery-regression": (
        _call(
            "bash", "scripts/run-ci-mechanism.sh", "observe-recovery",
            "scripts/run-observe-recovery-tests.sh",
            env={
                "OBSERVE_RECOVERY_SNAPSHOT_FILE":
                    "ci-artifacts/observe-recovery-before-reap.img",
            },
        ),
        _attest_call("observe-recovery-regression"),
    ),
    "virtio-disk-regression": (
        _call("bash", "scripts/run-ci-mechanism.sh", "virtio-disk", "scripts/run-virtio-disk-tests.sh"),
        _attest_call("virtio-disk-regression"),
    ),
    "fs-allocator-fault-regression": (
        _call(
            "bash", "scripts/run-ci-mechanism.sh", "fs-allocator-fault",
            "scripts/run-fs-allocator-fault-tests.sh",
            env={
                "FS_ALLOCATOR_ARTIFACT_DIR": "${CI_PROJECT_DIR}/.fs-allocator-evidence",
                "FS_ALLOCATOR_EVIDENCE_ARCHIVE": "${CI_PROJECT_DIR}/ci-artifacts/fs-allocator-evidence.tar",
            },
        ),
        _call(
            "python3", "scripts/fs-allocator-evidence.py", "verify-archive", "--archive",
            "${CI_PROJECT_DIR}/ci-artifacts/fs-allocator-evidence.tar",
        ),
        _attest_call("fs-allocator-fault-regression"),
    ),
}


EXPECTED_TIMEOUTS = {
    "kernel-budgets": "20m",
    "reader-e2e": "15m",
    "agent-regression": "15m",
    "kernel-mechanism-regression": "35m",
    "physical-resource-regression": "20m",
    "metadata-recovery-regression": "60m",
    "observe-recovery-regression": "25m",
    "virtio-disk-regression": "20m",
    "fs-allocator-fault-regression": "45m",
}


EXPECTED_ARTIFACT_PATHS = {
    "kernel-budgets": (
        "ci-artifacts/kernel-budgets.log",
        "ci-artifacts/remote-ci-attestation.json",
    ),
    "reader-e2e": (
        "ci-artifacts/reader-e2e.log",
        "ci-artifacts/reader-e2e-raw/",
        "ci-artifacts/remote-ci-attestation.json",
    ),
    "agent-regression": (
        "ci-artifacts/agent-suite-timings.log",
        "ci-artifacts/agent-suite-guest.log",
        "ci-artifacts/agent-regression-job.log",
        "ci-artifacts/remote-ci-attestation.json",
    ),
}


def _root_stages(lines: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r" {2}-[ ]+([a-z][a-z0-9_-]*)[ ]*", line)
        if match is None:
            raise CIContractError("root stages list is malformed")
        result.append(match.group(1))
    return tuple(result)


def _root_variables(lines: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r" {2}([A-Za-z_][A-Za-z0-9_]*):[ ]+(.+?)[ ]*", line)
        if match is None or match.group(1) in result:
            raise CIContractError("root variables mapping is malformed or duplicated")
        result[match.group(1)] = Field(match.group(2), ()).scalar()
    return result


def _nested_mapping(field: Field, label: str) -> dict[str, str | tuple[str, ...]]:
    if field.inline.strip():
        raise CIContractError(f"{label} must use a block mapping")
    result: dict[str, str | tuple[str, ...]] = {}
    index = 0
    while index < len(field.body):
        line = field.body[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        match = re.fullmatch(r" {4}([A-Za-z_][A-Za-z0-9_-]*):(?:[ ]+(.+?))?[ ]*", line)
        if match is None or match.group(1) in result:
            raise CIContractError(f"{label} is malformed or duplicated")
        key, scalar = match.group(1), match.group(2)
        index += 1
        if scalar is not None:
            result[key] = Field(scalar, ()).scalar()
            continue
        items: list[str] = []
        while index < len(field.body):
            nested = field.body[index]
            if not nested.strip() or nested.lstrip().startswith("#"):
                index += 1
                continue
            item = re.fullmatch(r" {6}-[ ]+(.+?)[ ]*", nested)
            if item is None:
                break
            items.append(item.group(1).strip().strip("\"'"))
            index += 1
        result[key] = tuple(items)
    return result


def _scalar_mapping(field: Field, label: str) -> dict[str, str]:
    value = _nested_mapping(field, label)
    if any(not isinstance(item, str) for item in value.values()):
        raise CIContractError(f"{label} must contain only scalar values")
    return {key: item for key, item in value.items() if isinstance(item, str)}


def _require_fields(definition: Definition, expected: set[str]) -> None:
    if set(definition.fields) != expected:
        raise CIContractError(
            f"CI definition {definition.name} field set differs: "
            f"{sorted(definition.fields)} != {sorted(expected)}"
        )


def _validate_artifacts(job: EffectiveJob, expected_paths: tuple[str, ...]) -> None:
    value = _nested_mapping(job.field("artifacts"), f"{job.name}.artifacts")
    expected = {"when": "always", "expire_in": "14 days", "paths": expected_paths}
    if value != expected:
        raise CIContractError(f"effective job {job.name} artifact contract differs")


def _validate_exact_job(config: CIConfig, name: str, qemu_tag: str) -> None:
    definition = config.definitions[name]
    job = config.effective(name)
    expected_fields = MECHANISM_JOB_FIELDS if name in MECHANISM_COMMANDS else (
        STANDALONE_FIELDS if name == "kernel-budgets" else QEMU_STANDALONE_FIELDS
    )
    _require_fields(definition, expected_fields)
    expected_parent = ".agentos-mechanism" if name in MECHANISM_COMMANDS else ".agentos-toolchain"
    if definition.parents() != (expected_parent,):
        raise CIContractError(f"{name} extends the wrong policy anchor")
    if job.field("timeout").scalar() != EXPECTED_TIMEOUTS[name]:
        raise CIContractError(f"effective job {name} timeout differs")
    before, script = _job_calls(job)
    if before != TOOLCHAIN_CALLS:
        raise CIContractError(f"effective job {name} toolchain setup differs")
    if script != EXPECTED_SCRIPT_CALLS[name]:
        raise CIContractError(f"effective job {name} command sequence differs")
    _validate_execution_policy(job)
    if name == "kernel-budgets":
        if job.field("stage").scalar() != "budget" or job.field("tags").items() != (
            "agentos-host-calibrated",
        ):
            raise CIContractError("kernel-budgets execution placement differs")
        _validate_artifacts(job, EXPECTED_ARTIFACT_PATHS[name])
        return
    if name in {"reader-e2e", "agent-regression"}:
        expected_stage = "reader" if name == "reader-e2e" else "test"
        if job.field("stage").scalar() != expected_stage:
            raise CIContractError(f"effective job {name} stage differs")
        _validate_artifacts(job, EXPECTED_ARTIFACT_PATHS[name])
    if job.field("tags").items() != (qemu_tag,):
        raise CIContractError(f"effective job {name} does not use the calibrated QEMU runner")
    if job.field("resource_group").scalar() != "agentos-qemu-performance":
        raise CIContractError(f"effective job {name} lacks QEMU serialization")
    if job.field("dependencies").items() != ():
        raise CIContractError(f"effective job {name} must disable dependencies")


def validate_repository_ci(path: Path, budget_path: Path) -> CIConfig:
    config = load_ci(path)
    try:
        budget = read_strict_json(budget_path)
        qemu_tag = budget["agent_test_suite"]["runner_tag"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError) as error:
        raise CIContractError(f"calibrated runner tag is unavailable: {error}") from error
    if qemu_tag != "agentos-qemu-calibrated":
        raise CIContractError("unexpected calibrated QEMU runner tag")
    if set(config.roots) != ROOT_FIELDS:
        raise CIContractError("CI root must define exactly stages and variables")
    if _root_stages(config.roots["stages"]) != ("budget", "reader", "test"):
        raise CIContractError("CI stage order differs from the required pipeline")
    if _root_variables(config.roots["variables"]) != ROOT_VARIABLES:
        raise CIContractError("CI root variables differ from the exact allowlist")
    if set(config.definitions) != REQUIRED_DEFINITIONS:
        raise CIContractError("CI definition inventory differs from the exact allowlist")
    toolchain = config.definitions[".agentos-toolchain"]
    _require_fields(toolchain, TOOLCHAIN_FIELDS)
    if toolchain.parents() or toolchain.fields["image"].scalar() != "ubuntu:26.04":
        raise CIContractError("CI toolchain image or inheritance differs")
    if tuple(
        call
        for command in toolchain.fields["before_script"].script_items()
        for call in _analyse_shell(command)
    ) != TOOLCHAIN_CALLS:
        raise CIContractError("CI toolchain installation sequence differs")
    mechanism = config.definitions[".agentos-mechanism"]
    _require_fields(mechanism, MECHANISM_ANCHOR_FIELDS)
    if mechanism.parents() != (".agentos-toolchain",):
        raise CIContractError("mechanism anchor inheritance differs")
    effective_mechanism = config.effective(".agentos-mechanism")
    if (
        effective_mechanism.field("stage").scalar() != "test"
        or effective_mechanism.field("tags").items() != (qemu_tag,)
        or effective_mechanism.field("resource_group").scalar() != "agentos-qemu-performance"
        or effective_mechanism.field("dependencies").items() != ()
        or _scalar_mapping(effective_mechanism.field("variables"), ".agentos-mechanism.variables")
        != MECHANISM_VARIABLES
    ):
        raise CIContractError("mechanism anchor execution policy differs")
    _validate_artifacts(effective_mechanism, ("ci-artifacts/",))
    for name in EXPECTED_SCRIPT_CALLS:
        _validate_exact_job(config, name, qemu_tag)
    return config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("verify",))
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--budget-config", type=Path, required=True)
    args = parser.parse_args()
    try:
        validate_repository_ci(args.path, args.budget_config)
    except CIContractError as error:
        parser.error(str(error))
    print("GitLab CI effective job contract: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
