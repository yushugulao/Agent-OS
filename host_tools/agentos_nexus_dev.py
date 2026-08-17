#!/usr/bin/env python3
"""Controlled Host broker for Nexus workspace mutation and uCore program tests."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Final, Mapping, Sequence

try:
    import agentos_workspace as workspace
except ModuleNotFoundError:  # pragma: no cover - package import fallback
    from . import agentos_workspace as workspace


MAX_WRITE_BYTES: Final = 12 * 1024
MAX_SESSION_WRITE_BYTES: Final = 128 * 1024
MAX_WRITE_CHUNK_BYTES: Final = 8_000
MAX_STAGED_WRITES: Final = 8
MAX_PATCH_BYTES: Final = 12 * 1024
MAX_BUILD_DIAGNOSTIC_BYTES: Final = 48 * 1024
MAX_RUN_LOG_BYTES: Final = 8 * 1024
MAX_RUN_INPUT_BYTES: Final = 512
MAX_RUN_CASES: Final = 6
BUILD_TIMEOUT_SECONDS: Final = 180
RUN_TIMEOUT_SECONDS: Final = 30
BUILD_RECIPE_VERSION: Final = "agentos-nexus-build-v2"
TOOLCHAIN_PREFIX: Final = "riscv64-linux-gnu-"
QEMU_BINARY: Final = "qemu-system-riscv64"
MISSING_REVISION: Final = "missing"
CASE_KINDS: Final = frozenset(("normal", "invalid", "failure"))

_PROGRAM_PATH_RE = re.compile(
    r"user/src/nexus_[a-z][a-z0-9_]{0,31}_ucore\.c\Z"
)
_TARGET_RE = re.compile(r"nexus_[a-z][a-z0-9_]{0,31}_ucore\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_HUNK_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?\Z")
_COPY_NAMES: Final = (
    "Makefile",
    "include",
    "os",
    "user",
    "nfs",
    "scripts",
)
_COPY_IGNORED: Final = frozenset(
    (
        ".git",
        "__pycache__",
        "asm",
        "build",
        "fs",
        "fs-copy.img",
        "fs.img",
        "target",
    )
)


class NexusDevelopmentError(ValueError):
    """Rejected development request or failed controlled operation."""


@dataclass(frozen=True, slots=True)
class DevelopmentResult:
    status: str
    workspace_generation: str
    content: str


@dataclass(slots=True)
class _BuildRecord:
    build_id: str
    source_path: str
    source_revision: str
    target: str
    root: Path
    kernel: Path
    image: Path
    toolchain_id: str
    kernel_sha256: str
    image_sha256: str
    created_monotonic: float


@dataclass(slots=True)
class _WriteStage:
    path: str
    expected_revision: str
    content: str


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _bounded_text(value: str, maximum: int) -> str:
    raw = value.encode("utf-8", errors="replace")
    if len(raw) <= maximum:
        return value
    marker = b"\n...[truncated]"
    prefix = raw[: max(0, maximum - len(marker))]
    while prefix:
        try:
            return prefix.decode("utf-8") + marker.decode("ascii")
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return marker[:maximum].decode("ascii", errors="ignore")


def _bounded_diagnostic(value: str, maximum: int) -> str:
    raw = value.encode("utf-8", errors="replace")
    if len(raw) <= maximum:
        return value
    marker = b"\n...[diagnostic middle truncated]...\n"
    head_size = min(600, max(0, (maximum - len(marker)) // 3))
    tail_size = max(0, maximum - len(marker) - head_size)
    head = raw[:head_size]
    tail = raw[-tail_size:] if tail_size else b""
    while head:
        try:
            head_text = head.decode("utf-8")
            break
        except UnicodeDecodeError:
            head = head[:-1]
    else:
        head_text = ""
    while tail:
        try:
            tail_text = tail.decode("utf-8")
            break
        except UnicodeDecodeError:
            tail = tail[1:]
    else:
        tail_text = ""
    return head_text + marker.decode("ascii") + tail_text


def _decode_process_output(value: bytes) -> str:
    text = value.decode("utf-8", errors="replace")
    return "\n".join(line for line in text.splitlines() if "\0" not in line)


def _one_line(value: str, maximum: int = 240) -> str:
    return _bounded_text(value.replace("\0", "").replace("\r", "").replace("\n", "\\n"), maximum)


def _result_content(kind: str, fields: Sequence[tuple[str, object]], body: str = "") -> str:
    lines = [kind, "content_untrusted=1"]
    lines.extend(f"{key}={_one_line(str(value))}" for key, value in fields)
    if body:
        lines.extend(("content_begin", body, "content_end"))
    return "\n".join(lines)


def _validate_program_path(value: object) -> str:
    if not isinstance(value, str) or not _PROGRAM_PATH_RE.fullmatch(value):
        raise NexusDevelopmentError("path_not_allowed")
    if "\0" in value or PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts:
        raise NexusDevelopmentError("path_not_allowed")
    return value


def _validate_target(value: object, source_path: str | None = None) -> str:
    if not isinstance(value, str):
        raise NexusDevelopmentError("target_not_allowed")
    if source_path is not None:
        stem = Path(source_path).stem
        if stem.endswith("_ucore") and value == stem[: -len("_ucore")]:
            value = stem
        if stem != value:
            raise NexusDevelopmentError("target_source_mismatch")
    if not _TARGET_RE.fullmatch(value):
        raise NexusDevelopmentError("target_not_allowed")
    return value


def _validate_revision(value: object) -> str:
    if not isinstance(value, str) or not (
        value == MISSING_REVISION or _DIGEST_RE.fullmatch(value)
    ):
        raise NexusDevelopmentError("revision_invalid")
    return value


def _decode_utf8(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise NexusDevelopmentError(f"{label}_invalid")
    try:
        raw = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise NexusDevelopmentError(f"{label}_invalid") from error
    if len(raw) > maximum:
        raise NexusDevelopmentError(f"{label}_too_large")
    return value


def _unified_patch(original: str, patch: str, relative: str) -> str:
    lines = patch.splitlines(keepends=True)
    if lines and lines[0].rstrip("\r\n") == "*** Begin Patch":
        return _codex_patch(original, lines, relative)
    if len(lines) < 3 or not lines[0].startswith("--- ") or not lines[1].startswith("+++ "):
        raise NexusDevelopmentError("patch_invalid")

    def header_path(line: str) -> str:
        value = line[4:].strip().split("\t", 1)[0]
        if value.startswith(("a/", "b/")):
            value = value[2:]
        return value

    if header_path(lines[0]) not in (relative, "/dev/null") or header_path(lines[1]) != relative:
        raise NexusDevelopmentError("patch_path_mismatch")
    source = original.splitlines(keepends=True)
    output: list[str] = []
    source_index = 0
    index = 2
    while index < len(lines):
        header = lines[index].rstrip("\r\n")
        match = _HUNK_RE.fullmatch(header)
        if match is None:
            raise NexusDevelopmentError("patch_invalid")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_count = int(match.group(4) or "1")
        expected_index = old_start - 1
        if expected_index < source_index or expected_index > len(source):
            raise NexusDevelopmentError("patch_conflict")
        output.extend(source[source_index:expected_index])
        source_index = expected_index
        consumed = 0
        produced = 0
        index += 1
        while index < len(lines) and not lines[index].startswith("@@ "):
            line = lines[index]
            if line.startswith("\\ No newline at end of file"):
                index += 1
                continue
            if not line or line[0] not in " +-":
                raise NexusDevelopmentError("patch_invalid")
            payload = line[1:]
            if line[0] in " -":
                if source_index >= len(source) or source[source_index] != payload:
                    raise NexusDevelopmentError("patch_conflict")
                source_index += 1
                consumed += 1
            if line[0] in " +":
                output.append(payload)
                produced += 1
            index += 1
        if consumed != old_count or produced != new_count:
            raise NexusDevelopmentError("patch_count_mismatch")
    output.extend(source[source_index:])
    return "".join(output)


def _codex_patch(original: str, lines: Sequence[str], relative: str) -> str:
    if (
        len(lines) < 5
        or lines[-1].rstrip("\r\n") != "*** End Patch"
        or lines[1].rstrip("\r\n") != f"*** Update File: {relative}"
    ):
        raise NexusDevelopmentError("patch_path_mismatch")
    source_cursor = 0
    output: list[str] = []
    index = 2
    saw_hunk = False
    while index < len(lines) - 1:
        if not lines[index].startswith("@@"):
            raise NexusDevelopmentError("patch_invalid")
        saw_hunk = True
        index += 1
        old: list[str] = []
        new: list[str] = []
        while index < len(lines) - 1 and not lines[index].startswith("@@"):
            line = lines[index]
            if not line or line[0] not in " +-":
                raise NexusDevelopmentError("patch_invalid")
            payload = line[1:]
            if line[0] in " -":
                old.append(payload)
            if line[0] in " +":
                new.append(payload)
            index += 1
        old_text = "".join(old)
        if not old_text:
            raise NexusDevelopmentError("patch_invalid")
        location = original.find(old_text, source_cursor)
        if location < 0 or original.find(old_text, location + 1) >= 0:
            raise NexusDevelopmentError("patch_conflict")
        output.append(original[source_cursor:location])
        output.extend(new)
        source_cursor = location + len(old_text)
    if not saw_hunk:
        raise NexusDevelopmentError("patch_invalid")
    output.append(original[source_cursor:])
    return "".join(output)


class NexusDevelopmentBroker:
    """One Nexus session's bounded mutation, build, and QEMU execution state."""

    def __init__(
        self,
        root: Path,
        *,
        toolchain_prefix: str = TOOLCHAIN_PREFIX,
        qemu: str = QEMU_BINARY,
        temporary_parent: Path | None = None,
        progress_callback: Callable[[str, Mapping[str, object]], None] | None = None,
    ) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("development workspace root must be a directory")
        if toolchain_prefix != TOOLCHAIN_PREFIX:
            raise ValueError("Nexus development uses the fixed RISC-V toolchain")
        self.toolchain_prefix = toolchain_prefix
        self.qemu = qemu
        self._progress_callback = progress_callback
        self._temporary = tempfile.TemporaryDirectory(
            prefix="agentos-nexus-dev-",
            dir=None if temporary_parent is None else str(temporary_parent),
        )
        self.temporary_root = Path(self._temporary.name)
        self._builds: dict[str, _BuildRecord] = {}
        self._build_sequence = 0
        self._writes: dict[str, _WriteStage] = {}
        self._write_sequence = 0
        self._session_written_bytes = 0

    def _progress(self, kind: str, **fields: object) -> None:
        if self._progress_callback is None:
            return
        try:
            self._progress_callback(kind, fields)
        except Exception:
            # Progress reporting cannot alter a broker decision.
            pass

    @staticmethod
    def _wsl_path(path: Path) -> str:
        completed = subprocess.run(
            ["wsl.exe", "-e", "wslpath", "-a", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            text=True,
        )
        value = completed.stdout.strip()
        if completed.returncode != 0 or not value.startswith("/") or "\0" in value:
            raise NexusDevelopmentError("wsl_path_failed")
        return value

    def close(self) -> None:
        self._builds.clear()
        self._writes.clear()
        self._temporary.cleanup()

    def __enter__(self) -> "NexusDevelopmentBroker":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _target(self, relative: str) -> Path:
        target = self.root.joinpath(*PurePosixPath(relative).parts)
        current = self.root
        for part in PurePosixPath(relative).parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise NexusDevelopmentError("path_symlink")
        if target.is_symlink():
            raise NexusDevelopmentError("path_symlink")
        parent = target.parent.resolve(strict=True)
        if self.root != parent and self.root not in parent.parents:
            raise NexusDevelopmentError("path_outside_root")
        return target

    @staticmethod
    def _revision(target: Path) -> str:
        if not target.exists():
            return MISSING_REVISION
        if not target.is_file() or target.is_symlink():
            raise NexusDevelopmentError("path_not_regular")
        return _sha256_bytes(target.read_bytes())

    def _atomic_replace(self, target: Path, content: str) -> tuple[str, str]:
        raw = content.encode("utf-8", errors="strict")
        if self._session_written_bytes + len(raw) > MAX_SESSION_WRITE_BYTES:
            raise NexusDevelopmentError("session_write_quota")
        previous = self._revision(target)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            try:
                directory = os.open(str(target.parent), os.O_RDONLY)
            except OSError:
                directory = -1
            if directory >= 0:
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
        self._session_written_bytes += len(raw)
        return previous, _sha256_bytes(raw)

    def write_file(self, path: object, content: object, expected_revision: object) -> DevelopmentResult:
        try:
            relative = _validate_program_path(path)
            body = _decode_utf8(content, "content", MAX_WRITE_BYTES)
            expected = _validate_revision(expected_revision)
            target = self._target(relative)
        except NexusDevelopmentError as error:
            return self._operation_error(str(error))
        current = self._revision(target)
        if current != expected:
            return self._operation_error("revision_conflict", current)
        try:
            previous, revision = self._atomic_replace(target, body)
        except (OSError, NexusDevelopmentError) as error:
            code = error.args[0] if isinstance(error, NexusDevelopmentError) else "atomic_commit_failed"
            return self._operation_error(str(code), current)
        generation = revision
        return DevelopmentResult(
            "ok",
            generation,
            _result_content(
                "workspace_write",
                (
                    ("path", relative),
                    ("previous_revision", previous),
                    ("revision", revision),
                    ("bytes", len(body.encode("utf-8"))),
                    ("atomic_commit", 1),
                ),
            ),
        )

    def write_file_chunk(
        self,
        path: object,
        content: object,
        expected_revision: object,
        write_id: object,
        commit: object,
    ) -> DevelopmentResult:
        """Stage bounded chunks and expose the file only through one atomic commit."""

        try:
            relative = _validate_program_path(path)
            body = _decode_utf8(content, "content", MAX_WRITE_CHUNK_BYTES)
            expected = _validate_revision(expected_revision)
            if not isinstance(write_id, str) or len(write_id) > 64 or "\0" in write_id:
                raise NexusDevelopmentError("write_id_invalid")
            if not isinstance(commit, int) or isinstance(commit, bool) or commit not in (0, 1):
                raise NexusDevelopmentError("commit_invalid")
            target = self._target(relative)
        except NexusDevelopmentError as error:
            return self._operation_error(str(error))

        current = self._revision(target)
        if current != expected:
            if write_id:
                self._writes.pop(write_id, None)
            return self._operation_error("revision_conflict", current)

        if not write_id:
            if commit:
                return self.write_file(relative, body, expected)
            if not body or len(self._writes) >= MAX_STAGED_WRITES:
                return self._operation_error("write_stage_capacity", current)
            self._write_sequence += 1
            write_id = _sha256_bytes(
                f"{self._write_sequence}:{relative}:{expected}".encode("utf-8")
            )
            self._writes[write_id] = _WriteStage(relative, expected, body)
            return DevelopmentResult(
                "ok",
                current if _DIGEST_RE.fullmatch(current) else "0" * 64,
                _result_content(
                    "workspace_write_stage",
                    (
                        ("path", relative),
                        ("write_id", write_id),
                        ("staged_bytes", len(body.encode("utf-8"))),
                        ("atomic_commit", 0),
                    ),
                ),
            )

        stage = self._writes.get(write_id)
        if (
            stage is None
            or stage.path != relative
            or stage.expected_revision != expected
        ):
            return self._operation_error("write_stage_not_found", current)
        combined = stage.content + body
        if len(combined.encode("utf-8")) > MAX_WRITE_BYTES:
            self._writes.pop(write_id, None)
            return self._operation_error("content_too_large", current)
        if not commit:
            stage.content = combined
            return DevelopmentResult(
                "ok",
                current if _DIGEST_RE.fullmatch(current) else "0" * 64,
                _result_content(
                    "workspace_write_stage",
                    (
                        ("path", relative),
                        ("write_id", write_id),
                        ("staged_bytes", len(combined.encode("utf-8"))),
                        ("atomic_commit", 0),
                    ),
                ),
            )
        self._writes.pop(write_id, None)
        return self.write_file(relative, combined, expected)

    def apply_patch(self, path: object, patch: object, expected_revision: object) -> DevelopmentResult:
        try:
            relative = _validate_program_path(path)
            delta = _decode_utf8(patch, "patch", MAX_PATCH_BYTES)
            expected = _validate_revision(expected_revision)
            target = self._target(relative)
        except NexusDevelopmentError as error:
            return self._operation_error(str(error))
        current = self._revision(target)
        if current != expected:
            return self._operation_error("revision_conflict", current)
        original = "" if current == MISSING_REVISION else target.read_text(encoding="utf-8")
        try:
            updated = _unified_patch(original, delta, relative)
        except (UnicodeError, OSError, NexusDevelopmentError) as error:
            code = error.args[0] if isinstance(error, NexusDevelopmentError) else "patch_invalid"
            return self._operation_error(str(code), current)
        if len(updated.encode("utf-8")) > MAX_WRITE_BYTES:
            return self._operation_error("content_too_large", current)
        try:
            previous, revision = self._atomic_replace(target, updated)
        except (OSError, NexusDevelopmentError) as error:
            code = error.args[0] if isinstance(error, NexusDevelopmentError) else "atomic_commit_failed"
            return self._operation_error(str(code), current)
        return DevelopmentResult(
            "ok",
            revision,
            _result_content(
                "workspace_patch",
                (
                    ("path", relative),
                    ("previous_revision", previous),
                    ("revision", revision),
                    ("bytes", len(updated.encode("utf-8"))),
                    ("atomic_commit", 1),
                ),
            ),
        )

    @staticmethod
    def _operation_error(code: str, generation: str = "") -> DevelopmentResult:
        digest = generation if _DIGEST_RE.fullmatch(generation) else "0" * 64
        fields: list[tuple[str, object]] = [("code", code)]
        if _DIGEST_RE.fullmatch(generation):
            fields.append(("current_revision", generation))
        return DevelopmentResult(
            "ok",
            digest,
            _result_content("development_error", fields),
        )

    def program_revision(self, path: object) -> str:
        """Return the revision of one path that the mutation broker may change."""

        relative = _validate_program_path(path)
        return self._revision(self._target(relative))

    def _copy_worktree(self, destination: Path) -> None:
        destination.mkdir(parents=True)

        def ignored(_directory: str, names: list[str]) -> set[str]:
            return {name for name in names if name in _COPY_IGNORED or name.endswith(".tmp")}

        for name in _COPY_NAMES:
            source = self.root / name
            target = destination / name
            if source.is_symlink():
                raise NexusDevelopmentError("build_source_symlink")
            if source.is_dir():
                if any(candidate.is_symlink() for candidate in source.rglob("*")):
                    raise NexusDevelopmentError("build_source_symlink")
                shutil.copytree(source, target, ignore=ignored, symlinks=False)
            elif source.is_file():
                shutil.copy2(source, target)
            else:
                raise NexusDevelopmentError("build_source_missing")

    @staticmethod
    def _resource_limiter() -> None:  # pragma: no cover - exercised in WSL integration
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (BUILD_TIMEOUT_SECONDS, BUILD_TIMEOUT_SECONDS + 5))
            resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
            resource.setrlimit(resource.RLIMIT_FSIZE, (256 << 20, 256 << 20))
            resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
        except (ImportError, OSError, ValueError):
            pass
        os.setsid()

    @staticmethod
    def _run_resource_limiter() -> None:  # pragma: no cover - exercised in WSL integration
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (RUN_TIMEOUT_SECONDS, RUN_TIMEOUT_SECONDS + 5))
            resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
            resource.setrlimit(resource.RLIMIT_FSIZE, (64 << 20, 64 << 20))
            resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
        except (ImportError, OSError, ValueError):
            pass

    def _run_build_command(
        self, command: Sequence[str], cwd: Path, timeout_seconds: float
    ) -> tuple[int, str]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.temporary_root),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        actual_command = list(command)
        actual_cwd = cwd
        if os.name == "nt":
            try:
                linux_cwd = self._wsl_path(cwd)
                linux_home = self._wsl_path(self.temporary_root)
            except (OSError, subprocess.SubprocessError, NexusDevelopmentError):
                return 127, "build_broker_error=wsl_path_failed"
            actual_command = [
                "wsl.exe",
                "--cd",
                linux_cwd,
                "-e",
                "/usr/bin/env",
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                f"HOME={linux_home}",
                "LANG=C.UTF-8",
                "LC_ALL=C.UTF-8",
                "/bin/sh",
                "-lc",
                (
                    f"ulimit -t {max(1, int(timeout_seconds))}; "
                    "ulimit -v 2097152; ulimit -f 524288; "
                    f"exec /usr/bin/timeout --signal=TERM --kill-after=2 "
                    f"{max(1, int(timeout_seconds))} \"$@\""
                ),
                "sh",
                "/usr/bin/make",
                *list(command)[1:],
            ]
            actual_cwd = self.root
            environment = None
        try:
            completed = subprocess.run(
                actual_command,
                cwd=actual_cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds + (10 if os.name == "nt" else 0),
                check=False,
                text=False,
                preexec_fn=self._resource_limiter if os.name == "posix" else None,
            )
            text = _decode_process_output(completed.stdout)
            return completed.returncode, text
        except subprocess.TimeoutExpired as error:
            output = _decode_process_output(error.stdout or b"")
            return 124, output + "\nbuild_timeout"
        except OSError as error:
            return 127, f"build_broker_error={type(error).__name__}"

    def _toolchain_identity(self) -> str:
        compiler = f"{self.toolchain_prefix}gcc"
        if os.name == "nt":
            command = [
                "wsl.exe", "-e", "/bin/sh", "-c",
                'p=$(command -v "$1") || exit 1; '
                'printf "%s\\n" "$p"; "$p" --version | head -n 1; '
                'sha256sum "$p"',
                "sh", compiler,
            ]
        else:
            command = ["/bin/sh", "-c",
                       'p=$(command -v "$1") || exit 1; '
                       'printf "%s\\n" "$p"; "$p" --version | head -n 1; '
                       'sha256sum "$p"', "sh", compiler]
        try:
            completed = subprocess.run(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise NexusDevelopmentError("toolchain_identity_failed") from error
        if completed.returncode != 0 or not completed.stdout:
            raise NexusDevelopmentError("toolchain_identity_failed")
        return _sha256_bytes(completed.stdout)

    def build_ucore_program(
        self, source_path: object, source_revision: object, target: object
    ) -> DevelopmentResult:
        try:
            relative = _validate_program_path(source_path)
            expected_revision = _validate_revision(source_revision)
            if expected_revision == MISSING_REVISION:
                raise NexusDevelopmentError("source_revision_invalid")
            program = _validate_target(target, relative)
            source = self._target(relative)
        except NexusDevelopmentError as error:
            return self._operation_error(str(error))
        if not source.is_file() or source.is_symlink():
            return self._operation_error("source_missing")
        current_revision = self._revision(source)
        if current_revision != expected_revision:
            return self._operation_error("revision_conflict", current_revision)
        source_revision = current_revision
        try:
            toolchain_id = self._toolchain_identity()
        except NexusDevelopmentError as error:
            return self._operation_error(str(error), source_revision)
        self._build_sequence += 1
        build_root = self.temporary_root / f"build-{self._build_sequence:04d}"
        self._progress(
            "build_worktree_started",
            source_path=relative,
            source_revision=source_revision,
            target=program,
            build_sequence=self._build_sequence,
        )
        try:
            self._copy_worktree(build_root)
        except (OSError, NexusDevelopmentError):
            self._progress(
                "build_worktree_failed",
                source_path=relative,
                target=program,
            )
            return self._operation_error("worktree_copy_failed", source_revision)
        self._progress(
            "build_worktree_completed",
            source_path=relative,
            target=program,
        )
        runner = f"{program}_nexus_runner"
        runner_source = build_root / "user" / "src" / f"{runner}.c"
        runner_source.write_text(
            "#include <stdio.h>\n"
            "#include <stdlib.h>\n"
            "#include <unistd.h>\n"
            f"#define main {program}_main\n"
            f"#include \"{program}.c\"\n"
            "#undef main\n"
            "int main(void)\n"
            "{\n"
            "    int pid;\n"
            "    int status = 127;\n"
            "    printf(\"NEXUS_PROGRAM_START\\n\");\n"
            "    fflush(stdout);\n"
            "    pid = fork();\n"
            "    if (pid == 0)\n"
            f"        exit({program}_main());\n"
            "    if (pid < 0 || waitpid(pid, &status) != pid)\n"
            "        status = 127;\n"
            "    printf(\"NEXUS_PROGRAM_EXIT status=%d\\n\", status);\n"
            "    fflush(stdout);\n"
            "    return status;\n"
            "}\n",
            encoding="utf-8",
            newline="\n",
        )
        make = "/usr/bin/make" if os.name == "nt" else (shutil.which("make") or "make")
        commands = (
            (make, "-j2", "-rR", "-C", "user", "-f", "Makefile", "clean"),
            (
                make, "-j2", "-rR", "-C", "user", "-f", "Makefile",
                f"TOOLPREFIX={self.toolchain_prefix}", "CHAPTER=agent", f"CH_TESTS={runner}",
            ),
            (make, "-j2", "-rR", "-C", "nfs", "-f", "Makefile", "clean"),
            (make, "-j2", "-rR", "-C", "nfs", "-f", "Makefile"),
            (
                make, "-j2", "--no-print-directory", "build",
                f"TOOLPREFIX={self.toolchain_prefix}", "LOG=error",
                f"INIT_PROC={runner}", "CHAPTER=agent",
            ),
        )
        diagnostics: list[str] = []
        return_code = 0
        build_started = time.monotonic()
        for command_index, command in enumerate(commands, 1):
            self._progress(
                "build_command_started",
                source_path=relative,
                target=program,
                command_index=command_index,
                command_count=len(commands),
                timeout_seconds=BUILD_TIMEOUT_SECONDS,
            )
            remaining = BUILD_TIMEOUT_SECONDS - (time.monotonic() - build_started)
            if remaining <= 0:
                return_code, output = 124, "build_timeout"
            else:
                return_code, output = self._run_build_command(
                    command, build_root, remaining
                )
            diagnostics.append(f"$ {' '.join(command)}\n{output}")
            self._progress(
                "build_command_completed",
                source_path=relative,
                target=program,
                command_index=command_index,
                command_count=len(commands),
                exit_status=return_code,
                duration_ms=int((time.monotonic() - build_started) * 1000),
            )
            if return_code != 0:
                break
        diagnostic_text = "\n".join(diagnostics).replace(
            str(build_root), "<build-root>"
        ).replace(str(self.root), "<workspace-root>")
        joined = _bounded_diagnostic(diagnostic_text, MAX_BUILD_DIAGNOSTIC_BYTES)
        if return_code != 0:
            failure_excerpt = _bounded_diagnostic(output, 1400)
            failure_body = (
                "diagnostic_summary_begin\n"
                + failure_excerpt
                + "\ndiagnostic_summary_end\nfull_diagnostic_begin\n"
                + joined
                + "\nfull_diagnostic_end"
            )
            self._progress(
                "build_failed",
                source_path=relative,
                source_revision=source_revision,
                target=program,
                exit_status=return_code,
                duration_ms=int((time.monotonic() - build_started) * 1000),
            )
            return DevelopmentResult(
                "ok",
                source_revision,
                _result_content(
                    "ucore_build",
                    (
                        ("status", "failed"),
                        ("source_path", relative),
                        ("source_revision", source_revision),
                        ("target", program),
                        ("toolchain_id", toolchain_id),
                        ("recipe", BUILD_RECIPE_VERSION),
                        ("exit_status", return_code),
                        ("diagnostic_sha256", _sha256_bytes(diagnostic_text.encode("utf-8", errors="replace"))),
                    ),
                    failure_body,
                ),
            )
        kernel = build_root / "build" / "kernel"
        image = build_root / "nfs" / "fs.img"
        if not kernel.is_file() or not image.is_file():
            return self._operation_error("build_output_missing", source_revision)
        kernel_sha256 = _sha256_bytes(kernel.read_bytes())
        image_sha256 = _sha256_bytes(image.read_bytes())
        build_id = _sha256_bytes(
            (
                f"{BUILD_RECIPE_VERSION}\0{relative}\0{source_revision}\0{program}\0"
                f"{toolchain_id}\0{kernel_sha256}\0{image_sha256}"
            ).encode("ascii")
        )
        record = _BuildRecord(
            build_id,
            relative,
            source_revision,
            program,
            build_root,
            kernel,
            image,
            toolchain_id,
            kernel_sha256,
            image_sha256,
            time.monotonic(),
        )
        self._builds[build_id] = record
        self._progress(
            "build_completed",
            source_path=relative,
            source_revision=source_revision,
            target=program,
            build_id=build_id,
            exit_status=0,
            duration_ms=int((time.monotonic() - build_started) * 1000),
        )
        return DevelopmentResult(
            "ok",
            source_revision,
            _result_content(
                "ucore_build",
                (
                    ("status", "passed"),
                    ("source_path", relative),
                    ("source_revision", source_revision),
                    ("target", program),
                    ("build_id", build_id),
                    ("toolchain_id", toolchain_id),
                    ("kernel_sha256", kernel_sha256),
                    ("image_sha256", image_sha256),
                    ("recipe", BUILD_RECIPE_VERSION),
                    ("exit_status", 0),
                    ("diagnostic_sha256", _sha256_bytes(diagnostic_text.encode("utf-8", errors="replace"))),
                    ("diagnostic_truncated", int(len(diagnostic_text.encode("utf-8", errors="replace")) > MAX_BUILD_DIAGNOSTIC_BYTES)),
                ),
                joined,
            ),
        )

    def _run_ucore_case(
        self,
        build_id: object,
        case_name: str,
        stdin: object,
        expected_output: object,
        expected_exit: object,
        case_kind: object,
    ) -> DevelopmentResult:
        if not isinstance(build_id, str) or not _DIGEST_RE.fullmatch(build_id):
            return self._operation_error("build_id_invalid")
        record = self._builds.get(build_id)
        if record is None:
            return self._operation_error("build_id_unknown")
        try:
            input_text = _decode_utf8(stdin, "stdin", MAX_RUN_INPUT_BYTES)
            expected = _decode_utf8(expected_output, "expected_output", MAX_RUN_INPUT_BYTES)
        except NexusDevelopmentError as error:
            return self._operation_error(str(error), record.source_revision)
        if not isinstance(expected_exit, int) or isinstance(expected_exit, bool) or not 0 <= expected_exit <= 255:
            return self._operation_error("expected_exit_invalid", record.source_revision)
        if not isinstance(case_kind, str) or case_kind not in CASE_KINDS:
            return self._operation_error("case_kind_invalid", record.source_revision)
        case_token = _sha256_bytes(case_name.encode("utf-8"))[:12]
        run_image = record.root / f"run-{case_kind}-{case_token}-{time.monotonic_ns()}.img"
        input_file = record.root / f"run-{case_kind}-{case_token}-{time.monotonic_ns()}.stdin"
        shutil.copy2(record.image, run_image)
        input_bytes = input_text.encode("utf-8")
        if input_text and not input_text.endswith("\n"):
            input_bytes += b"\n"
        input_file.write_bytes(input_bytes)
        qemu = shutil.which(self.qemu) or self.qemu
        kernel_path = str(record.kernel)
        image_path = str(run_image)
        if os.name == "nt":
            try:
                linux_root = self._wsl_path(record.root)
                kernel_path = self._wsl_path(record.kernel)
                image_path = self._wsl_path(run_image)
                input_path = self._wsl_path(input_file)
                helper_path = self._wsl_path(Path(__file__).resolve())
            except (OSError, subprocess.SubprocessError, NexusDevelopmentError):
                run_image.unlink(missing_ok=True)
                input_file.unlink(missing_ok=True)
                return self._operation_error("wsl_path_failed", record.source_revision)
            command = [
                "wsl.exe",
                "--cd",
                linux_root,
                "-e",
                "/usr/bin/env",
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "/usr/bin/python3",
                helper_path,
                "--guest-driver",
                kernel_path,
                image_path,
                input_path,
            ]
        else:
            command = _qemu_command(qemu, kernel_path, image_path)
        output = bytearray()
        exit_status: int | None = None
        timed_out = False
        process: subprocess.Popen[bytes] | None = None
        run_started = time.monotonic()
        self._progress(
            "run_guest_started",
            build_id=build_id,
            source_revision=record.source_revision,
            target=record.target,
            case_kind=case_kind,
            case_name=case_name,
            timeout_seconds=RUN_TIMEOUT_SECONDS,
            input_bytes=len(input_bytes),
        )
        try:
            process = subprocess.Popen(
                command,
                cwd=record.root,
                stdin=subprocess.DEVNULL if os.name == "nt" else subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=(os.name == "posix"),
                preexec_fn=self._run_resource_limiter if os.name == "posix" else None,
            )
            assert process.stdout is not None
            if os.name == "nt":
                try:
                    captured, _unused = process.communicate(
                        timeout=RUN_TIMEOUT_SECONDS + 10
                    )
                    output.extend(captured[: 64 * 1024])
                    process = None
                except subprocess.TimeoutExpired as error:
                    timed_out = True
                    output.extend((error.output or b"")[: 64 * 1024])
                text = output.decode("utf-8", errors="replace")
                matches = re.findall(r"NEXUS_PROGRAM_EXIT status=([0-9]+)", text)
                if matches:
                    exit_status = int(matches[-1])
                elif "NEXUS_GUEST_DRIVER_TIMEOUT" in text:
                    timed_out = True
            else:
                assert process.stdin is not None
                selector = selectors.DefaultSelector()
                selector.register(process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
                input_sent = False
                while time.monotonic() < deadline:
                    events = selector.select(timeout=0.05)
                    if not events and process.poll() is not None:
                        break
                    for key, _mask in events:
                        chunk = os.read(key.fileobj.fileno(), 4096)
                        if not chunk:
                            continue
                        if len(output) < 64 * 1024:
                            output.extend(chunk[: 64 * 1024 - len(output)])
                    text = output.decode("utf-8", errors="replace")
                    if not input_sent and "NEXUS_PROGRAM_START" in text:
                        process.stdin.write(input_text.encode("utf-8"))
                        if input_text and not input_text.endswith("\n"):
                            process.stdin.write(b"\n")
                        process.stdin.flush()
                        input_sent = True
                        self._progress(
                            "run_input_sent",
                            build_id=build_id,
                            case_kind=case_kind,
                            case_name=case_name,
                            input_bytes=len(input_bytes),
                        )
                    matches = re.findall(r"NEXUS_PROGRAM_EXIT status=([0-9]+)", text)
                    if matches:
                        exit_status = int(matches[-1])
                        break
                else:
                    timed_out = True
        except OSError as error:
            output.extend(f"run_broker_error={type(error).__name__}".encode("ascii"))
        finally:
            if process is not None and process.poll() is None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait(timeout=2)
            run_image.unlink(missing_ok=True)
            input_file.unlink(missing_ok=True)
        serial = output.decode("utf-8", errors="replace")
        marker_index = serial.find("NEXUS_PROGRAM_START")
        actual = serial[marker_index + len("NEXUS_PROGRAM_START") :] if marker_index >= 0 else serial
        exit_index = actual.rfind("NEXUS_PROGRAM_EXIT status=")
        if exit_index >= 0:
            actual = actual[:exit_index]
        actual = actual.strip("\r\n")
        passed = bool(
            not timed_out
            and exit_status == expected_exit
            and expected in actual
        )
        self._progress(
            "run_guest_completed",
            build_id=build_id,
            source_revision=record.source_revision,
            target=record.target,
            case_kind=case_kind,
            case_name=case_name,
            actual_exit=-1 if exit_status is None else exit_status,
            expected_exit=expected_exit,
            passed=int(passed),
            timed_out=int(timed_out),
            output_bytes=len(output),
            duration_ms=int((time.monotonic() - run_started) * 1000),
        )
        exit_marker = (
            f"NEXUS_PROGRAM_EXIT status={exit_status}"
            if exit_status is not None
            else "NEXUS_PROGRAM_EXIT status=missing"
        )
        stable_log = f"NEXUS_PROGRAM_START\n{actual}\n{exit_marker}"
        log = _bounded_text(stable_log, MAX_RUN_LOG_BYTES)
        return DevelopmentResult(
            "ok",
            record.source_revision,
            _result_content(
                "ucore_run_case",
                (
                    ("status", "passed" if passed else "failed"),
                    ("build_id", build_id),
                    ("source_revision", record.source_revision),
                    ("target", record.target),
                    ("case_name", case_name),
                    ("case_kind", case_kind),
                    ("expected_exit", expected_exit),
                    ("actual_exit", -1 if exit_status is None else exit_status),
                    ("output_match", int(expected in actual)),
                    ("timed_out", int(timed_out)),
                    ("output_sha256", _sha256_bytes(actual.encode("utf-8", errors="replace"))),
                    ("log_sha256", _sha256_bytes(stable_log.encode("utf-8", errors="replace"))),
                    ("log_truncated", int(len(stable_log.encode("utf-8", errors="replace")) > MAX_RUN_LOG_BYTES)),
                ),
                log,
            ),
        )

    def run_ucore_program(
        self, build_id: object, cases: object
    ) -> DevelopmentResult:
        if not isinstance(build_id, str) or not _DIGEST_RE.fullmatch(build_id):
            return self._operation_error("build_id_invalid")
        record = self._builds.get(build_id)
        if record is None:
            return self._operation_error("build_id_unknown")
        if (
            not isinstance(cases, Sequence)
            or isinstance(cases, (str, bytes, bytearray))
            or not 1 <= len(cases) <= MAX_RUN_CASES
        ):
            return self._operation_error("run_cases_invalid", record.source_revision)
        names: set[str] = set()
        normalized: list[tuple[str, str, str, int, str]] = []
        for item in cases:
            if not isinstance(item, Mapping) or set(item) != {
                "name", "stdin", "expected_output", "expected_exit", "case_kind"
            }:
                return self._operation_error("run_case_invalid", record.source_revision)
            name = item.get("name")
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name)
                or name in names
            ):
                return self._operation_error("run_case_name_invalid", record.source_revision)
            names.add(name)
            normalized.append((
                name,
                item.get("stdin"),
                item.get("expected_output"),
                item.get("expected_exit"),
                item.get("case_kind"),
            ))
        results: list[DevelopmentResult] = []
        passed = 0
        kinds: set[str] = set()
        for name, stdin, expected, expected_exit, case_kind in normalized:
            result = self._run_ucore_case(
                build_id, name, stdin, expected, expected_exit, case_kind
            )
            results.append(result)
            if "\nstatus=passed\n" in f"\n{result.content}\n":
                passed += 1
            if isinstance(case_kind, str):
                kinds.add(case_kind)
        body_parts = []
        for index, result in enumerate(results, 1):
            body_parts.extend((f"case_{index}_begin", result.content, f"case_{index}_end"))
        body = _bounded_text("\n".join(body_parts), 56 * 1024)
        return DevelopmentResult(
            "ok",
            record.source_revision,
            _result_content(
                "ucore_run_suite",
                (
                    ("status", "passed" if passed == len(results) else "failed"),
                    ("build_id", build_id),
                    ("source_revision", record.source_revision),
                    ("target", record.target),
                    ("case_count", len(results)),
                    ("passed_count", passed),
                    ("case_kinds", ",".join(sorted(kinds))),
                    ("independent_guest_count", len(results)),
                ),
                body,
            ),
        )


def _qemu_command(qemu: str, kernel: str, image: str) -> list[str]:
    return [
        qemu,
        "-display", "none",
        "-monitor", "none",
        "-machine", "virt",
        "-bios", "default",
        "-m", "128M",
        "-smp", "1",
        "-kernel", kernel,
        "-drive", f"file={image},if=none,format=raw,id=x0",
        "-device", "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
        "-chardev", "stdio,id=nexusdev,signal=off,mux=off",
        "-serial", "chardev:nexusdev",
        "-no-reboot",
    ]


def _run_guest_driver(kernel: str, image: str, input_path: str) -> int:
    """Drive one isolated Guest from inside WSL so serial input stays interactive."""

    payload = Path(input_path).read_bytes()
    output = bytearray()
    exit_seen = False
    process = subprocess.Popen(
        _qemu_command(f"/usr/bin/{QEMU_BINARY}", kernel, image),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=NexusDevelopmentBroker._run_resource_limiter,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
        input_sent = False
        while time.monotonic() < deadline:
            events = selector.select(timeout=0.05)
            if not events and process.poll() is not None:
                break
            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 4096)
                if chunk and len(output) < 64 * 1024:
                    output.extend(chunk[: 64 * 1024 - len(output)])
            text = output.decode("utf-8", errors="replace")
            if not input_sent and "NEXUS_PROGRAM_START" in text:
                process.stdin.write(payload)
                process.stdin.flush()
                input_sent = True
            if re.search(r"NEXUS_PROGRAM_EXIT status=[0-9]+", text):
                exit_seen = True
                break
        if not exit_seen:
            output.extend(b"\nNEXUS_GUEST_DRIVER_TIMEOUT\n")
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
    return 0 if exit_seen else 124


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--guest-driver":
        raise SystemExit(_run_guest_driver(sys.argv[2], sys.argv[3], sys.argv[4]))
    raise SystemExit("agentos_nexus_dev.py is a library; use --guest-driver internally")


__all__ = [
    "BUILD_TIMEOUT_SECONDS",
    "CASE_KINDS",
    "DevelopmentResult",
    "MAX_BUILD_DIAGNOSTIC_BYTES",
    "MAX_PATCH_BYTES",
    "MAX_RUN_INPUT_BYTES",
    "MAX_RUN_LOG_BYTES",
    "MAX_WRITE_CHUNK_BYTES",
    "MAX_WRITE_BYTES",
    "MISSING_REVISION",
    "NexusDevelopmentBroker",
    "NexusDevelopmentError",
    "QEMU_BINARY",
    "RUN_TIMEOUT_SECONDS",
    "TOOLCHAIN_PREFIX",
]
