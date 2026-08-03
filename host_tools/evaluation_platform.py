#!/usr/bin/env python3
"""Fail-closed execution-domain preflight for formal AgentOS evaluation.

Formal collection has one execution domain.  Linux runs natively; a Windows
Host normally proves a named WSL distribution and then re-executes the complete
evaluation command there.  A deliberately provisioned MSYS2 environment is
also accepted as one native POSIX domain, but only after its runtime, namespace,
Python implementation and complete tool set have been bound.  Native Windows
Python and mixed Windows/WSL collection remain forbidden.
"""

from __future__ import annotations

import sys as _entry_sys


def _isolate_direct_entry_imports() -> None:
    """Use only interpreter-owned paths for top-level import resolution."""

    if __name__ != "__main__":
        return
    prefixes = {
        value.replace("\\", "/").rstrip("/").casefold()
        for value in (
            _entry_sys.base_prefix, _entry_sys.base_exec_prefix,
            _entry_sys.prefix, _entry_sys.exec_prefix,
        )
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

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(
        Path(tempfile.gettempdir()) / f"agentos-pycache-{os.urandom(16).hex()}"
    )
    if not __package__:
        import types as _entry_types

        _entry_package = _entry_types.ModuleType("host_tools")
        _entry_package.__path__ = [str(Path(__file__).resolve().parent)]
        _entry_package.__package__ = "host_tools"
        sys.modules["host_tools"] = _entry_package
        __package__ = "host_tools"
        sys.path.append(_entry_package.__path__[0])

try:
    from .safe_host_paths import (
        absolute_lexical_path,
        path_is_link,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
    )
    from .strict_json import read_strict_json
except ImportError:
    from safe_host_paths import (
        absolute_lexical_path,
        path_is_link,
        reject_link_components,
        require_regular_file,
        require_safe_directory,
    )
    from strict_json import read_strict_json


SCHEMA_VERSION = 5
KIND = "agentos-evaluation-platform-preflight"
MINIMUM_PYTHON = (3, 10)
HARDWARE_SOURCE = "procfs:/proc/cpuinfo+/proc/meminfo"
MAX_PROC_IDENTITY_BYTES = 16 * 1024 * 1024
MAX_LOGICAL_CPU_COUNT = 65536
MAX_MEMORY_TOTAL_BYTES = (1 << 63) - 1
DISTRO_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
TOOL_LABELS = (
    "bash",
    "env",
    "git",
    "make",
    "python",
    "assembler",
    "compiler",
    "host_cc",
    "linker",
    "objcopy",
    "objdump",
    "size",
    "qemu",
    "timeout",
    "readlink",
    "sha256sum",
)
MSYS_EXTRA_TOOL_LABELS = ("cygpath", "host_objdump", "uname")
MSYS_TOOL_LABELS = TOOL_LABELS + MSYS_EXTRA_TOOL_LABELS
MSYS_SYSTEM_RE = re.compile(r"^MSYS_NT-(?P<windows>[0-9]+\.[0-9]+-[0-9]+)$")
MSYS_RUNTIME_PATH = PurePosixPath("/usr/bin/msys-2.0.dll")
FORMAL_MODES = frozenset(
    {
        "run", "verify", "kernel-cost", "full-verify", "dashboard", "package",
        "verify-package",
    }
)
MSYS_REENTRY_MARKER = "native-msys2"
NATIVE_REENTRY_MARKER = "native-linux"
DURATION_PROFILES = frozenset({"local-e3", "none"})
PATH_ENVIRONMENT_NAMES = (
    "EVALUATION_OUTPUT_ROOT",
    "EVALUATION_RUN_DIR",
    "EVALUATION_BUNDLE_DIR",
)
FORWARDED_ENVIRONMENT_NAMES = (
    "EVALUATION_BOOTS",
    "EVALUATION_FULL_VERIFY_TIMEOUT",
    "EVALUATION_INCLUDE_SCENARIO",
    "EVALUATION_SCENARIO_BOOTS",
    "EVALUATION_SCENARIO_TIMEOUT",
    "EVALUATION_MICRO_TIMEOUT",
    "EVALUATION_RUN_ID",
)


class PlatformPreflightError(ValueError):
    """Raised when no trustworthy formal execution domain is available."""


def _safe_directory(path: Path, label: str) -> Path:
    try:
        return require_safe_directory(path)
    except (OSError, ValueError) as error:
        raise PlatformPreflightError(f"{label} is unavailable or link-backed") from error


def _safe_regular_file(path: Path, label: str) -> Path:
    try:
        return require_regular_file(path)
    except (OSError, ValueError) as error:
        raise PlatformPreflightError(f"{label} is unavailable or link-backed") from error


def _resolved_safe_directory(path: Path, label: str) -> Path:
    return _safe_directory(absolute_lexical_path(path), label).resolve(strict=True)


def _resolved_safe_file(path: Path, label: str) -> Path:
    return _safe_regular_file(absolute_lexical_path(path), label).resolve(strict=True)


def _normalized_hardware_text(value: str, label: str, *, limit: int = 512) -> str:
    normalized = " ".join(value.split())
    if (
        not normalized
        or len(normalized) > limit
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized)
    ):
        raise PlatformPreflightError(f"{label} is malformed")
    return normalized


def _validate_hardware_identity(value: object) -> dict[str, Any]:
    """Validate the stable, public subset of Host hardware identity."""

    expected = {
        "cpu_model", "logical_cpu_count", "memory_total_bytes", "source",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise PlatformPreflightError("hardware identity is incomplete")
    cpu_model = value.get("cpu_model")
    logical_cpu_count = value.get("logical_cpu_count")
    memory_total_bytes = value.get("memory_total_bytes")
    source = value.get("source")
    if not isinstance(cpu_model, str):
        raise PlatformPreflightError("hardware CPU model is malformed")
    normalized_cpu_model = _normalized_hardware_text(
        cpu_model, "hardware CPU model"
    )
    if cpu_model != normalized_cpu_model:
        raise PlatformPreflightError("hardware CPU model is not canonical")
    if (
        type(logical_cpu_count) is not int
        or not 1 <= logical_cpu_count <= MAX_LOGICAL_CPU_COUNT
    ):
        raise PlatformPreflightError("hardware logical CPU count is malformed")
    if (
        type(memory_total_bytes) is not int
        or not 1024 <= memory_total_bytes <= MAX_MEMORY_TOTAL_BYTES
        or memory_total_bytes % 1024 != 0
    ):
        raise PlatformPreflightError("hardware total memory is malformed")
    if source != HARDWARE_SOURCE:
        raise PlatformPreflightError("hardware identity source is invalid")
    return {
        "cpu_model": cpu_model,
        "logical_cpu_count": logical_cpu_count,
        "memory_total_bytes": memory_total_bytes,
        "source": HARDWARE_SOURCE,
    }


def _parse_proc_hardware_identity(
    cpuinfo_text: str, meminfo_text: str
) -> dict[str, Any]:
    """Derive stable hardware fields while deliberately ignoring dynamic MHz."""

    if not cpuinfo_text or not meminfo_text or "\x00" in cpuinfo_text + meminfo_text:
        raise PlatformPreflightError("procfs hardware identity is unavailable")
    fields: dict[str, list[str]] = {}
    processor_ids: list[int] = []
    for raw_line in cpuinfo_text.splitlines():
        if not raw_line.strip() or ":" not in raw_line:
            continue
        raw_key, raw_value = raw_line.split(":", 1)
        key = " ".join(raw_key.casefold().split())
        value = " ".join(raw_value.split())
        if not key or not value:
            continue
        fields.setdefault(key, []).append(value)
        if raw_key.strip() == "processor":
            if re.fullmatch(r"[0-9]{1,10}", value) is None:
                raise PlatformPreflightError(
                    "/proc/cpuinfo processor identifier is malformed"
                )
            processor_ids.append(int(value))
    if (
        not processor_ids
        or len(set(processor_ids)) != len(processor_ids)
        or len(processor_ids) > MAX_LOGICAL_CPU_COUNT
    ):
        raise PlatformPreflightError("/proc/cpuinfo logical CPU records are malformed")

    model_values: list[str] = []
    for key in ("model name", "hardware", "cpu", "machine"):
        candidates = fields.get(key, [])
        if candidates:
            model_values = candidates
            break
    if not model_values:
        model_values = [
            value
            for value in fields.get("processor", [])
            if re.fullmatch(r"[0-9]+", value) is None
        ]
    if model_values:
        models = sorted(
            {
                _normalized_hardware_text(value, "/proc/cpuinfo CPU model", limit=256)
                for value in model_values
            }
        )
        cpu_model = " | ".join(models)
    else:
        descriptor_parts: list[str] = []
        for key in (
            "vendor_id", "cpu implementer", "cpu architecture", "cpu part",
            "model",
        ):
            values = fields.get(key, [])
            if not values:
                continue
            normalized_values = sorted(
                {
                    _normalized_hardware_text(
                        value, f"/proc/cpuinfo {key}", limit=64
                    )
                    for value in values
                }
            )
            descriptor_parts.append(f"{key}={','.join(normalized_values)}")
        cpu_model = "; ".join(descriptor_parts)
    cpu_model = _normalized_hardware_text(cpu_model, "/proc/cpuinfo CPU model")

    memory_matches = re.findall(
        r"(?m)^MemTotal:[ \t]*([0-9]{1,20})[ \t]+kB[ \t]*$", meminfo_text
    )
    if len(memory_matches) != 1:
        raise PlatformPreflightError("/proc/meminfo MemTotal is malformed")
    memory_kib = int(memory_matches[0])
    memory_total_bytes = memory_kib * 1024
    return _validate_hardware_identity(
        {
            "cpu_model": cpu_model,
            "logical_cpu_count": len(processor_ids),
            "memory_total_bytes": memory_total_bytes,
            "source": HARDWARE_SOURCE,
        }
    )


def _read_bounded_proc_file(path: Path, label: str) -> str:
    # ``/proc`` is a trusted kernel/runtime namespace rather than an evidence
    # tree.  In native MSYS2 it is virtual and has no Win32 file attributes, so
    # the general reparse-point checker cannot inspect it.  Bind the two exact
    # procfs inputs and use O_NOFOLLOW plus fstat instead of weakening the
    # link-safe rules used for repository and evidence paths.
    expected_paths = {
        "/proc/cpuinfo": "/proc/cpuinfo",
        "/proc/meminfo": "/proc/meminfo",
    }
    expected = expected_paths.get(label)
    if expected is None or path.as_posix() != expected:
        raise PlatformPreflightError(f"{label} is not a bound procfs input")
    lexical = absolute_lexical_path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lexical, flags)
    except OSError as error:
        raise PlatformPreflightError(f"cannot read {label}: {error}") from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise PlatformPreflightError(f"{label} is not a regular procfs file")
        chunks: list[bytes] = []
        remaining = MAX_PROC_IDENTITY_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if not raw or len(raw) > MAX_PROC_IDENTITY_BYTES:
        raise PlatformPreflightError(f"{label} is empty or unbounded")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise PlatformPreflightError(f"{label} is not valid UTF-8") from error


def _probe_proc_hardware_identity(
    *,
    cpuinfo_path: Path = Path("/proc/cpuinfo"),
    meminfo_path: Path = Path("/proc/meminfo"),
) -> dict[str, Any]:
    """Collect hardware identity only from the controlled procfs namespace."""

    return _parse_proc_hardware_identity(
        _read_bounded_proc_file(cpuinfo_path, "/proc/cpuinfo"),
        _read_bounded_proc_file(meminfo_path, "/proc/meminfo"),
    )


def _verify_hardware_identity(expected: object) -> None:
    expected_identity = _validate_hardware_identity(expected)
    if _probe_proc_hardware_identity() != expected_identity:
        raise PlatformPreflightError("Host hardware identity changed after preflight")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_output(raw: bytes) -> str:
    if not raw:
        return ""
    # Console programs such as wsl.exe use UTF-16 on some Windows releases.
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in raw[:64]:
        for encoding in ("utf-16", "utf-16-le"):
            try:
                return raw.decode(encoding).replace("\x00", "")
            except UnicodeError:
                pass
    return raw.decode("utf-8", errors="replace").replace("\x00", "")


def _run_bytes(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(argv),
            cwd=cwd,
            env=None if environment is None else dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PlatformPreflightError(
            f"cannot execute platform probe {list(argv)!r}: {error}"
        ) from error


def _first_line(raw: bytes, label: str) -> str:
    line = next((item.strip() for item in _decode_output(raw).splitlines() if item.strip()), "")
    if not line or len(line) > 1024:
        raise PlatformPreflightError(f"{label} version output is unavailable")
    return line


def _regular_executable(name: str) -> Path:
    candidate = shutil.which(name)
    if candidate is None:
        raise PlatformPreflightError(f"required executable is unavailable: {name}")
    return _resolved_safe_file(Path(candidate), f"required executable {name}")


def _tool_identity(
    name: str,
    version_args: Sequence[str] = ("--version",),
    *,
    label: str,
    cwd: Path,
) -> dict[str, str]:
    path = _regular_executable(name)
    result = _run_bytes([str(path), *version_args], cwd=cwd)
    if result.returncode != 0:
        detail = _decode_output(result.stdout).strip()[-1000:]
        raise PlatformPreflightError(
            f"{label} version probe failed with {result.returncode}: {detail}"
        )
    return {
        "argv0": name,
        "path": str(path),
        "sha256": _sha256(path),
        "version": _first_line(result.stdout, label),
    }


def _python_identity(name: str, *, cwd: Path) -> dict[str, str]:
    path = _regular_executable(name)
    result = _run_bytes(
        [
            str(path),
            "-I",
            "-S",
            "-c",
            "import os,platform,sys; print('%d.%d.%d' % sys.version_info[:3]); "
            "print(platform.python_implementation()); "
            "print('%d%d%d%d' % (sys.flags.isolated,sys.flags.no_site,"
            "int(bool(getattr(sys.flags,'safe_path',False) or "
            "(sys.flags.isolated and '' not in sys.path and "
            "os.path.abspath(os.getcwd()) not in "
            "[os.path.abspath(p) for p in sys.path]))),"
            "sys.flags.ignore_environment))",
        ],
        cwd=cwd,
    )
    if result.returncode != 0:
        raise PlatformPreflightError("Python version probe failed")
    lines = [line.strip() for line in _decode_output(result.stdout).splitlines() if line.strip()]
    if (
        len(lines) != 3
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", lines[0]) is None
        or lines[1] != "CPython"
        or lines[2] != "1111"
    ):
        raise PlatformPreflightError("Python version probe returned malformed output")
    version = tuple(int(part) for part in lines[0].split("."))
    if version < MINIMUM_PYTHON:
        raise PlatformPreflightError(
            f"formal evaluation requires Python >= 3.10, observed {lines[0]}"
        )
    return {
        "argv0": name,
        "path": str(path),
        "sha256": _sha256(path),
        "version": f"{lines[1]} {lines[0]}",
    }


def _toolprefix_from_compiler(compiler_path: str) -> str:
    if compiler_path.endswith("gcc"):
        return compiler_path[:-3]
    if compiler_path.endswith("gcc.exe"):
        return compiler_path[:-7]
    raise PlatformPreflightError("compiler path cannot derive TOOLPREFIX")


def _verify_riscv64_compiler(
    *, compiler: Path, objdump: Path, cwd: Path
) -> str:
    machine = _run_bytes([str(compiler), "-dumpmachine"], cwd=cwd)
    target = _decode_output(machine.stdout).strip()
    if machine.returncode != 0 or re.fullmatch(r"riscv(?:64)?(?:[-_].+)?", target) is None:
        raise PlatformPreflightError(
            f"compiler is not a RISC-V cross compiler: {target!r}"
        )
    try:
        with tempfile.TemporaryDirectory(prefix="agentos-rv64-probe-") as temporary:
            directory = Path(temporary)
            source = directory / "probe.c"
            output = directory / "probe.o"
            source.write_text("int agentos_rv64_probe;\n", encoding="ascii")
            compile_result = _run_bytes(
                [
                    str(compiler),
                    "-march=rv64imac_zicsr_zifencei",
                    "-mabi=lp64",
                    "-ffreestanding",
                    "-c",
                    str(source),
                    "-o",
                    str(output),
                ],
                cwd=cwd,
            )
            inspect = _run_bytes([str(objdump), "-f", str(output)], cwd=cwd)
    except OSError as error:
        raise PlatformPreflightError(f"RISC-V 64 compiler probe failed: {error}") from error
    inspection = _decode_output(inspect.stdout)
    if (
        compile_result.returncode != 0
        or inspect.returncode != 0
        or "file format elf64-littleriscv" not in inspection
        or re.search(r"(?m)^architecture:\s+riscv:rv64(?:,|\s|$)", inspection) is None
    ):
        detail = (_decode_output(compile_result.stdout) + inspection).strip()[-1000:]
        raise PlatformPreflightError(
            f"compiler cannot produce the required RISC-V 64 ABI object: {detail}"
        )
    return target


def _canonical_posix_path(value: str, label: str) -> PurePosixPath:
    pure = PurePosixPath(value)
    if (
        not value.startswith("/")
        or "\\" in value
        or not pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise PlatformPreflightError(
            f"{label} is not in the MSYS2 POSIX namespace: {value!r}"
        )
    return pure


def _current_msys2_identity(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Prove this is MSYS2, not native Windows or a Cygwin installation.

    Upstream MSYS2 Python reports ``sys.platform == 'cygwin'``.  The runtime
    identity therefore comes from the conjunction of that value, ``os.name``,
    ``MSYSTEM`` and the kernel name.  A real Cygwin kernel reports
    ``CYGWIN_NT-*`` and is rejected by the strict ``MSYS_NT-*`` match.
    """

    values = os.environ if environment is None else environment
    try:
        uname = os.uname()
    except (AttributeError, OSError) as error:
        raise PlatformPreflightError("MSYS2 uname identity is unavailable") from error
    match = MSYS_SYSTEM_RE.fullmatch(uname.sysname)
    if (
        os.name != "posix"
        or sys.platform != "cygwin"
        or values.get("MSYSTEM") != "MSYS"
        or match is None
    ):
        raise PlatformPreflightError(
            "native-msys2 requires os.name=posix, the MSYS2 Python runtime, "
            "MSYSTEM=MSYS and an MSYS_NT kernel; Cygwin and Windows Python "
            "are forbidden"
        )
    return {
        "system": uname.sysname,
        "release": uname.release,
        "version": uname.version,
        "machine": uname.machine,
        "windows_version": match.group("windows"),
    }


def _canonical_windows_system_drive(
    environment: Mapping[str, str] | None = None,
) -> str:
    values = os.environ if environment is None else environment
    system_drive = values.get("SYSTEMDRIVE", "")
    system_root = values.get("SYSTEMROOT", "")
    if (
        re.fullmatch(r"[A-Za-z]:", system_drive) is None
        or not PureWindowsPath(system_root).is_absolute()
        or PureWindowsPath(system_root).drive.casefold() != system_drive.casefold()
    ):
        raise PlatformPreflightError(
            "MSYS2 Windows system drive is unavailable or inconsistent"
        )
    return system_drive[0].upper() + ":"


def _parse_msys_python_observation(
    raw: bytes, *, expected_path: Path
) -> tuple[str, dict[str, str]]:
    marker = "__AGENTOS_MSYS_PYTHON__"
    payloads = [
        line.removeprefix(marker)
        for line in _decode_output(raw).splitlines()
        if line.startswith(marker)
    ]
    try:
        if len(payloads) != 1:
            raise json.JSONDecodeError("missing unique MSYS Python marker", "", 0)
        observed = json.loads(payloads[0])
    except json.JSONDecodeError as error:
        raise PlatformPreflightError("MSYS2 Python probe returned invalid JSON") from error
    required = {
        "implementation", "runtime", "msystem", "os_name", "platform", "python",
        "system", "isolated", "no_site", "safe_path", "ignore_environment",
    }
    if not isinstance(observed, dict) or set(observed) != required:
        raise PlatformPreflightError("MSYS2 Python identity is incomplete")
    version_text = observed.get("python", "")
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version_text) is None:
        raise PlatformPreflightError("MSYS2 Python version is malformed")
    version = tuple(int(part) for part in version_text.split("."))
    if version < MINIMUM_PYTHON:
        raise PlatformPreflightError(
            f"formal evaluation requires Python >= 3.10, observed {version_text}"
        )
    executable = str(observed.get("implementation", ""))
    _canonical_posix_path(executable, "MSYS2 Python executable")
    try:
        same_python = os.path.samefile(executable, expected_path)
    except OSError:
        same_python = False
    if (
        observed.get("os_name") != "posix"
        or observed.get("runtime") != "CPython"
        or observed.get("platform") != "cygwin"
        or observed.get("msystem") != "MSYS"
        or MSYS_SYSTEM_RE.fullmatch(str(observed.get("system", ""))) is None
        or any(observed.get(name) != 1 for name in (
            "isolated", "no_site", "safe_path", "ignore_environment",
        ))
        or not same_python
    ):
        raise PlatformPreflightError(
            "requested Python is not the bound MSYS2 POSIX interpreter; "
            "Cygwin and Windows Python are forbidden"
        )
    return version_text, {key: str(value) for key, value in observed.items()}


def _msys_python_identity(
    name: str, *, cwd: Path
) -> tuple[dict[str, str], dict[str, str]]:
    path = _regular_executable(name)
    program = (
        "import json,os,platform,sys; "
        "print('__AGENTOS_MSYS_PYTHON__'+json.dumps({"
        "'implementation':sys.executable,'msystem':os.environ.get('MSYSTEM',''),"
        "'runtime':platform.python_implementation(),"
        "'os_name':os.name,'platform':sys.platform,"
        "'python':'%d.%d.%d' % sys.version_info[:3],"
        "'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,"
        "'safe_path':int(bool(getattr(sys.flags,'safe_path',False) or "
        "(sys.flags.isolated and '' not in sys.path and os.path.abspath(os.getcwd()) "
        "not in [os.path.abspath(p) for p in sys.path]))),"
        "'ignore_environment':sys.flags.ignore_environment,"
        "'system':os.uname().sysname},sort_keys=True,separators=(',',':')))"
    )
    result = _run_bytes([str(path), "-I", "-S", "-c", program], cwd=cwd)
    if result.returncode != 0:
        raise PlatformPreflightError("MSYS2 Python identity probe failed")
    version, observed = _parse_msys_python_observation(
        result.stdout, expected_path=path
    )
    return (
        {
            "argv0": name,
            "path": str(path),
            "sha256": _sha256(path),
            "version": f"CPython {version} (MSYS2)",
        },
        observed,
    )


def _msys_namespace_roundtrip(
    value: Path, *, cygpath: Path, cwd: Path, label: str
) -> None:
    text = str(value)
    _canonical_posix_path(text, label)
    windows = _run_bytes([str(cygpath), "-a", "-w", text], cwd=cwd)
    windows_text = _decode_output(windows.stdout).strip()
    if windows.returncode != 0 or not PureWindowsPath(windows_text).is_absolute():
        raise PlatformPreflightError(f"{label} cannot map into the Windows namespace")
    posix = _run_bytes([str(cygpath), "-a", "-u", windows_text], cwd=cwd)
    posix_text = _decode_output(posix.stdout).strip()
    try:
        same = posix.returncode == 0 and os.path.samefile(text, posix_text)
    except OSError:
        same = False
    if not same:
        raise PlatformPreflightError(
            f"{label} does not round-trip through the bound MSYS2 namespace"
        )


def _msys_windows_path(value: Path, *, cygpath: Path, cwd: Path, label: str) -> str:
    result = _run_bytes([str(cygpath), "-a", "-w", str(value)], cwd=cwd)
    converted = _decode_output(result.stdout).strip()
    if (
        result.returncode != 0
        or not PureWindowsPath(converted).is_absolute()
        or "\n" in converted
        or "\r" in converted
    ):
        raise PlatformPreflightError(f"{label} has no canonical Windows mapping")
    return converted


def _msys_controlled_path(tools: Mapping[str, Mapping[str, str]]) -> str:
    directories: list[str] = []
    for label in MSYS_TOOL_LABELS:
        directory = str(PurePosixPath(tools[label]["path"]).parent)
        _canonical_posix_path(directory, f"tool directory {label}")
        if directory not in directories:
            directories.append(directory)
    return ":".join(directories)


def _validate_msys_clean_entry(
    *,
    tools: Mapping[str, Mapping[str, str]],
    toolprefix: str,
    duration_profile_name: str,
    temporary_directory: Path,
    windows_temporary_directory: str,
    windows_system_drive: str,
) -> None:
    """Reject a forged re-entry marker or inherited host build settings."""

    if os.environ.get("AGENTOS_EVALUATION_EXECUTION_DOMAIN") != MSYS_REENTRY_MARKER:
        raise PlatformPreflightError("formal MSYS2 collection did not use clean re-entry")
    expected = {
        "AGENT_TEST_DURATION_PROFILE": duration_profile_name,
        "AGENTOS_EVALUATION_EXECUTION_DOMAIN": MSYS_REENTRY_MARKER,
        "BASH_BIN": tools["bash"]["path"],
        "CC": tools["host_cc"]["path"],
        "HOME": "/tmp",
        "HOSTCC": tools["host_cc"]["path"],
        "HOST_CC": tools["host_cc"]["path"],
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "MAKE_TOOL": tools["make"]["path"],
        "MSYSTEM": "MSYS",
        "PATH": _msys_controlled_path(tools),
        "PYTHONHASHSEED": "0",
        "PYTHON_BIN": tools["python"]["path"],
        "QEMU": tools["qemu"]["path"],
        "SIZE_TOOL": tools["size"]["path"],
        "SYSTEMDRIVE": windows_system_drive,
        "TOOLPREFIX": toolprefix,
        "TMPDIR": str(temporary_directory),
        "TEMP": windows_temporary_directory,
        "TMP": windows_temporary_directory,
        "TZ": "UTC",
    }
    mismatched = [
        name for name, value in expected.items() if os.environ.get(name) != value
    ]
    allowed = set(expected) | set(FORWARDED_ENVIRONMENT_NAMES) | set(
        PATH_ENVIRONMENT_NAMES
    ) | {
        "AGENTOS_EVALUATION_CAMPAIGN_TOKEN",
        "EVALUATION_WSL_DISTRO",
        "OLDPWD",
        "PWD",
        "SHLVL",
        "SYSTEMROOT",
        "WINDIR",
        "_",
    }
    system_root = os.environ.get("SYSTEMROOT", "")
    if (
        not PureWindowsPath(system_root).is_absolute()
        or os.environ.get("WINDIR", "").casefold() != system_root.casefold()
        or PureWindowsPath(system_root).name.casefold() != "windows"
        or _canonical_windows_system_drive() != windows_system_drive
    ):
        mismatched.extend(["SYSTEMDRIVE", "SYSTEMROOT", "WINDIR"])
    unexpected = sorted(set(os.environ) - allowed)
    if mismatched or unexpected:
        raise PlatformPreflightError(
            "native-msys2 re-entry is not the controlled env -i domain: "
            f"mismatched={sorted(mismatched)} unexpected={unexpected}"
        )


def _canonical_repository(root: Path) -> tuple[Path, str]:
    repository = _resolved_safe_directory(root, "repository root")
    git = _regular_executable("git")
    result = _run_bytes(
        [str(git), "-C", str(repository), "rev-parse", "--show-toplevel"],
        cwd=repository,
    )
    if result.returncode != 0:
        raise PlatformPreflightError("repository identity probe failed")
    observed = _decode_output(result.stdout).strip()
    observed_root = _resolved_safe_directory(Path(observed), "observed repository root")
    if observed_root != repository:
        raise PlatformPreflightError(
            f"repository root differs in execution domain: {observed!r}"
        )
    return repository, observed


def _load_profile_component(repository: Path, relative: str, module_name: str) -> Any:
    path = _resolved_safe_file(repository / relative, f"profile component {relative}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PlatformPreflightError(f"cannot load profile component: {relative}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError, ValueError) as error:
        raise PlatformPreflightError(
            f"cannot initialize profile component: {relative}"
        ) from error
    return module


def _duration_profile_binding(
    repository: Path, proof: Mapping[str, Any], profile_name: str
) -> dict[str, str]:
    if profile_name not in DURATION_PROFILES:
        raise PlatformPreflightError(
            "Agent duration profile must be exactly local-e3 or none"
        )
    if profile_name == "none":
        return {
            "calibration_status": "not-applicable",
            "name": "none",
            "profile_id": "none",
            "status": "disabled-different-runner",
        }

    def mismatch(detail: str) -> None:
        raise PlatformPreflightError(f"local-e3 duration profile mismatch: {detail}")

    if proof.get("domain") != "native-msys2":
        mismatch("execution domain is not native-msys2")
    tools = proof.get("tools")
    if not isinstance(tools, dict):
        mismatch("tool inventory is unavailable")
    calibration = _load_profile_component(
        repository, "scripts/agent_test_calibration.py",
        "agentos_evaluation_profile_calibration",
    )
    budget = _load_profile_component(
        repository, "scripts/check-kernel-budgets.py",
        "agentos_evaluation_profile_budget",
    )
    try:
        config = budget.load_config(repository / "ci" / "kernel-budgets.json")
        suite = config["agent_test_suite"]
        profile = suite["local_calibration_profile"]
        compiler_prefix = _toolprefix_from_compiler(tools["compiler"]["path"])
        commands = {
            "qemu": tools["qemu"]["path"],
            "toolchain_cc": tools["compiler"]["path"],
            "toolchain_ld": tools["linker"]["path"],
            "toolchain_objcopy": tools["objcopy"]["path"],
            "toolchain_objdump": tools["objdump"]["path"],
            "toolchain_as": tools["assembler"]["path"],
            "host_cc": tools["host_cc"]["path"],
            "python": tools["python"]["path"],
            "bash": tools["bash"]["path"],
            "make": tools["make"]["path"],
            "git": tools["git"]["path"],
        }
        calibration_tools = {
            name: calibration.executable_identity(command, name)
            for name, command in commands.items()
        }
        for identity in calibration_tools.values():
            resolved = identity["executable"]["path"]
            identity["requested_path"] = resolved
            identity["version_argv"][0] = resolved
        profile_id = calibration.validate_live_calibration_profile(
            profile, calibration_tools, calibration.capture_host_identity()
        )

        budget_tools = {
            "gcc": Path(tools["compiler"]["path"]),
            "ld": Path(tools["linker"]["path"]),
            "objcopy": Path(tools["objcopy"]["path"]),
            "objdump": Path(tools["objdump"]["path"]),
            "nm": budget.resolve_executable_once(f"{compiler_prefix}nm", "profile nm"),
            "size": Path(tools["size"]["path"]),
        }
        profile_kind, budget_profile = budget.select_kernel_budget_toolchain(
            config, budget_tools
        )
        budget_tools["cc1"] = budget.resolve_gcc_subprogram(budget_tools["gcc"], "cc1")
        budget_tools["as"] = Path(tools["assembler"]["path"])
        if profile_kind != "local" or budget_profile.get("profile_id") != profile_id:
            mismatch("kernel toolchain profile differs")
        budget.attest_local_kernel_budget_tools(budget_profile, budget_tools)
        calibration_status = suite["calibration_status"]
        if calibration_status not in {
            "provisional_requires_full_suite", "calibrated_full_suite"
        }:
            mismatch("calibration status is invalid")
    except (KeyError, OSError, subprocess.SubprocessError, ValueError) as error:
        mismatch(str(error))
    return {
        "calibration_status": calibration_status,
        "name": "local-e3",
        "profile_id": profile_id,
        "status": "matched",
    }


def _finalize_platform_proof(
    repository: Path,
    proof: dict[str, Any],
    *,
    requested_host_cc: str,
    duration_profile: str,
) -> dict[str, Any]:
    if (
        not isinstance(requested_host_cc, str)
        or not requested_host_cc
        or len(requested_host_cc) > 1024
        or any(character in requested_host_cc for character in "\x00\r\n")
    ):
        raise PlatformPreflightError("requested Host C compiler is invalid")
    tools = proof.get("tools")
    host_cc = tools.get("host_cc") if isinstance(tools, dict) else None
    if not isinstance(host_cc, dict) or host_cc.get("argv0") != requested_host_cc:
        raise PlatformPreflightError("Host C compiler request is not bound to its identity")
    proof["requested_host_cc"] = requested_host_cc
    proof["duration_profile"] = _duration_profile_binding(
        repository, proof, duration_profile
    )
    validate_duration_profile_binding(proof, repository=repository)
    return proof


def _recorded_duration_profile(
    repository: Path,
) -> tuple[str, str, dict[str, Any], Any]:
    try:
        root = _resolved_safe_directory(repository, "duration profile repository")
        config_path = _resolved_safe_file(
            root / "ci" / "kernel-budgets.json", "duration profile configuration"
        )
        config = read_strict_json(config_path)
        suite = config["agent_test_suite"]
        profile = suite["local_calibration_profile"]
        profile_id = profile["profile_id"]
        calibration_status = suite["calibration_status"]
        calibration = _load_profile_component(
            root, "scripts/agent_test_calibration.py",
            "agentos_evaluation_recorded_profile_calibration",
        )
    except (KeyError, OSError, TypeError, UnicodeDecodeError, ValueError) as error:
        raise PlatformPreflightError(
            f"recorded duration profile is invalid: {error}"
        ) from error
    if (
        not isinstance(profile, dict)
        or not isinstance(profile_id, str)
        or re.fullmatch(r"[A-Za-z0-9_.-]+", profile_id) is None
        or calibration_status
        not in {"provisional_requires_full_suite", "calibrated_full_suite"}
    ):
        raise PlatformPreflightError("recorded duration profile is invalid")
    return profile_id, str(calibration_status), profile, calibration


def validate_duration_profile_binding(
    preflight: Mapping[str, Any], *, repository: Path | None = None
) -> str:
    """Validate the profile against its execution domain and recorded tool profile."""

    binding = preflight.get("duration_profile")
    none = {
        "calibration_status": "not-applicable", "name": "none",
        "profile_id": "none", "status": "disabled-different-runner",
    }
    local_valid = isinstance(binding, dict) and (
        binding.get("name") == "local-e3"
        and binding.get("status") == "matched"
        and binding.get("calibration_status") in {
            "provisional_requires_full_suite", "calibrated_full_suite"
        }
        and re.fullmatch(r"[A-Za-z0-9_.-]+", str(binding.get("profile_id", "")))
        is not None
    )
    if not isinstance(binding, dict) or set(binding) != set(none):
        raise PlatformPreflightError("duration profile binding is invalid")
    domain = preflight.get("domain")
    entry_domain = preflight.get("entry_domain")
    if binding == none:
        if domain == "native-msys2" and entry_domain not in {None, "native-msys2"}:
            raise PlatformPreflightError(
                "duration profile differs from its execution domain"
            )
        if domain == "native-linux" and entry_domain is not None and not (
            entry_domain == "native-linux"
            or (
                isinstance(entry_domain, str)
                and re.fullmatch(r"windows-wsl:[A-Za-z0-9._-]{1,64}", entry_domain)
                is not None
            )
        ):
            raise PlatformPreflightError(
                "duration profile differs from its execution domain"
            )
        return "none"
    if not local_valid:
        raise PlatformPreflightError("duration profile binding is invalid")
    if domain != "native-msys2" or entry_domain not in {None, "native-msys2"}:
        raise PlatformPreflightError(
            "local-e3 duration profile requires native-msys2"
        )
    tools = preflight.get("tools")
    if not isinstance(tools, dict) or set(tools) != set(MSYS_TOOL_LABELS):
        raise PlatformPreflightError(
            "local-e3 duration profile lacks its MSYS2 tool proof"
        )
    if repository is None:
        raise PlatformPreflightError(
            "local-e3 duration profile requires a trusted contract root"
        )

    profile_id, calibration_status, profile, calibration = (
        _recorded_duration_profile(repository)
    )
    if (
        binding.get("profile_id") != profile_id
        or binding.get("calibration_status") != calibration_status
    ):
        raise PlatformPreflightError(
            "local-e3 duration profile differs from recorded configuration"
        )
    hardware = preflight.get("hardware")
    uname = preflight.get("uname")
    if not isinstance(hardware, dict) or not isinstance(uname, dict):
        raise PlatformPreflightError("local-e3 duration host proof is unavailable")
    release_match = re.match(r"[0-9]+\.[0-9]+\.[0-9]+", str(uname.get("release", "")))
    system_match = MSYS_SYSTEM_RE.fullmatch(str(uname.get("system", "")))
    python_match = re.fullmatch(
        r"CPython ([0-9]+\.[0-9]+\.[0-9]+)(?: \(MSYS2\))?",
        str(tools["python"].get("version", "")),
    )
    runtime = (
        f"MSYS2 {release_match.group(0)} on Windows build "
        f"{system_match.group('windows').rsplit('-', 1)[-1]}; QEMU TCG"
        if release_match is not None and system_match is not None
        else ""
    )
    labels = {
        "qemu": "qemu", "toolchain_cc": "compiler",
        "toolchain_ld": "linker", "toolchain_objcopy": "objcopy",
        "toolchain_objdump": "objdump", "toolchain_as": "assembler",
        "host_cc": "host_cc",
        "python": "python", "bash": "bash", "make": "make", "git": "git",
    }
    try:
        observed_profile_id = calibration.validate_recorded_calibration_profile(
            profile,
            {name: tools[label] for name, label in labels.items()},
            {
                "platform": runtime, "machine": hardware.get("cpu_model"),
                "python_runtime": (
                    f"{python_match.group(1)} platform-proof"
                    if python_match is not None else "invalid"
                ),
            },
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PlatformPreflightError(
            f"local-e3 recorded profile validation failed: {error}"
        ) from error
    if observed_profile_id != profile_id:
        raise PlatformPreflightError("local-e3 profile identity differs")
    return "local-e3"


def _bound_duration_profile_name(
    preflight: Mapping[str, Any], *, repository: Path | None = None
) -> str:
    return validate_duration_profile_binding(preflight, repository=repository)


def probe_native_linux_domain(
    *,
    repo: Path,
    toolprefix: str,
    qemu: str,
    python_bin: str,
    host_cc: str,
    duration_profile: str,
    shell_bin: str = "bash",
) -> dict[str, Any]:
    """Probe the exact native Linux domain used by formal collection."""

    if not sys.platform.startswith("linux") or os.name != "posix":
        raise PlatformPreflightError(
            "formal evaluation must run in native Linux or be fully re-executed "
            "inside the selected WSL distribution"
        )
    root, _ = _canonical_repository(repo)
    requested = {
        "bash": shell_bin,
        "env": "env",
        "git": "git",
        "make": "make",
        "assembler": f"{toolprefix}as",
        "compiler": f"{toolprefix}gcc",
        "host_cc": host_cc,
        "linker": f"{toolprefix}ld",
        "objcopy": f"{toolprefix}objcopy",
        "objdump": f"{toolprefix}objdump",
        "size": f"{toolprefix}size",
        "qemu": qemu,
        "timeout": "timeout",
        "readlink": "readlink",
        "sha256sum": "sha256sum",
    }
    tools: dict[str, dict[str, str]] = {
        "python": _python_identity(python_bin, cwd=root)
    }
    for label, name in requested.items():
        tools[label] = _tool_identity(name, label=label, cwd=root)

    _verify_riscv64_compiler(
        compiler=Path(tools["compiler"]["path"]),
        objdump=Path(tools["objdump"]["path"]),
        cwd=root,
    )
    qemu_probe = _run_bytes([tools["qemu"]["path"], "-machine", "help"], cwd=root)
    if qemu_probe.returncode != 0 or re.search(
        r"(?m)^virt(?:\s|$)", _decode_output(qemu_probe.stdout)
    ) is None:
        raise PlatformPreflightError("QEMU does not provide the required RISC-V virt machine")

    compiler_invocation = shutil.which(f"{toolprefix}gcc")
    if compiler_invocation is None or not compiler_invocation.endswith("gcc"):
        raise PlatformPreflightError("compiler invocation cannot derive TOOLPREFIX")
    observed_prefix = str(Path(compiler_invocation).absolute())[:-3]
    proof = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "status": "ready",
        "domain": "native-linux",
        "distribution": None,
        "hardware": _probe_proc_hardware_identity(),
        "repository": {"host_path": str(root), "execution_path": str(root)},
        "launcher": tools["bash"],
        "toolprefix": observed_prefix,
        "tools": {label: tools[label] for label in TOOL_LABELS},
    }
    return _finalize_platform_proof(
        root,
        proof,
        requested_host_cc=host_cc,
        duration_profile=duration_profile,
    )


def _require_msys_runtime_import(
    executable: Path, *, host_objdump: Path, cwd: Path, label: str
) -> None:
    result = _run_bytes([str(host_objdump), "-p", str(executable)], cwd=cwd)
    output = _decode_output(result.stdout)
    imports = {
        item.casefold()
        for item in re.findall(r"(?im)^\s*DLL Name:\s*([^\s]+)\s*$", output)
    }
    if (
        result.returncode != 0
        or "msys-2.0.dll" not in imports
        or "cygwin1.dll" in imports
    ):
        raise PlatformPreflightError(
            f"MSYS2 control tool does not bind the required runtime: {label}"
        )


def probe_native_msys2_domain(
    *,
    repo: Path,
    toolprefix: str,
    qemu: str,
    python_bin: str,
    host_cc: str,
    duration_profile: str,
    shell_bin: str = "bash",
) -> dict[str, Any]:
    """Probe a complete formal domain hosted by one native MSYS2 runtime."""

    uname_identity = _current_msys2_identity()
    windows_system_drive = _canonical_windows_system_drive()
    root, _ = _canonical_repository(repo)
    _canonical_posix_path(str(root), "repository")
    temporary_directory = _resolved_safe_directory(
        Path(os.environ.get("TMPDIR", "/tmp")), "MSYS2 temporary directory"
    )
    _canonical_posix_path(str(temporary_directory), "MSYS2 temporary directory")
    requested = {
        "bash": shell_bin,
        "cygpath": "cygpath",
        "env": "env",
        "git": "git",
        "host_objdump": "objdump",
        "make": "make",
        "assembler": f"{toolprefix}as",
        "compiler": f"{toolprefix}gcc",
        "host_cc": host_cc,
        "linker": f"{toolprefix}ld",
        "objcopy": f"{toolprefix}objcopy",
        "objdump": f"{toolprefix}objdump",
        "size": f"{toolprefix}size",
        "qemu": qemu,
        "timeout": "timeout",
        "readlink": "readlink",
        "sha256sum": "sha256sum",
        "uname": "uname",
    }
    python_tool, python_observation = _msys_python_identity(python_bin, cwd=root)
    tools: dict[str, dict[str, str]] = {"python": python_tool}
    for label, name in requested.items():
        tools[label] = _tool_identity(name, label=label, cwd=root)
    if set(tools) != set(MSYS_TOOL_LABELS):
        raise PlatformPreflightError("MSYS2 tool identity set is incomplete")

    runtime = _resolved_safe_file(Path(str(MSYS_RUNTIME_PATH)), "MSYS2 runtime")
    _canonical_posix_path(str(runtime), "MSYS2 runtime")
    runtime_identity = {
        "path": str(runtime),
        "sha256": _sha256(runtime),
        "version": uname_identity["release"],
    }

    cygpath = Path(tools["cygpath"]["path"])
    windows_temporary_directory = _msys_windows_path(
        temporary_directory,
        cygpath=cygpath,
        cwd=root,
        label="MSYS2 temporary directory",
    )
    for label, path in (
        ("repository", root),
        ("runtime", runtime),
        ("temporary directory", temporary_directory),
        *((f"tool {name}", Path(tool["path"])) for name, tool in tools.items()),
    ):
        _msys_namespace_roundtrip(
            path, cygpath=cygpath, cwd=root, label=label
        )

    host_objdump = Path(tools["host_objdump"]["path"])
    # Build payload tools may be native PE; only the MSYS2 control plane must
    # import the single bound POSIX runtime.
    for label in (
        "bash", "cygpath", "env", "git", "host_objdump", "make", "python",
        "readlink", "sha256sum", "timeout", "uname",
    ):
        _require_msys_runtime_import(
            Path(tools[label]["path"]),
            host_objdump=host_objdump,
            cwd=root,
            label=label,
        )

    _verify_riscv64_compiler(
        compiler=Path(tools["compiler"]["path"]),
        objdump=Path(tools["objdump"]["path"]),
        cwd=root,
    )
    qemu_probe = _run_bytes([tools["qemu"]["path"], "-machine", "help"], cwd=root)
    if qemu_probe.returncode != 0 or re.search(
        r"(?m)^virt(?:\s|$)", _decode_output(qemu_probe.stdout)
    ) is None:
        raise PlatformPreflightError("QEMU does not provide the required RISC-V virt machine")

    uname_probe = _run_bytes([tools["uname"]["path"], "-srmv"], cwd=root)
    uname_text = _first_line(uname_probe.stdout, "uname -srmv")
    if uname_probe.returncode != 0 or not uname_text.startswith(
        uname_identity["system"] + " "
    ):
        raise PlatformPreflightError("bound uname differs from Python's MSYS2 kernel")
    if python_observation["system"] != uname_identity["system"]:
        raise PlatformPreflightError("MSYS2 Python and kernel identities differ")

    compiler_invocation = shutil.which(f"{toolprefix}gcc")
    if compiler_invocation is None:
        raise PlatformPreflightError("compiler invocation cannot derive TOOLPREFIX")
    _canonical_posix_path(compiler_invocation, "compiler invocation")
    proof = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "status": "ready",
        "domain": "native-msys2",
        "distribution": None,
        "hardware": _probe_proc_hardware_identity(),
        "repository": {"host_path": str(root), "execution_path": str(root)},
        "launcher": tools["bash"],
        "runtime": runtime_identity,
        "temporary_directory": str(temporary_directory),
        "windows_system_drive": windows_system_drive,
        "windows_temporary_directory": windows_temporary_directory,
        "uname": {**uname_identity, "command": uname_text},
        # Caller-selected prefix invocation is intentional: PATH below contains
        # only the individually hashed compiler/binutils directories.  This
        # supports a GCC wrapper and native PE binutils living in separate
        # directories without admitting any unbound executable.
        "toolprefix": toolprefix,
        "tools": {label: tools[label] for label in MSYS_TOOL_LABELS},
    }
    return _finalize_platform_proof(
        root,
        proof,
        requested_host_cc=host_cc,
        duration_profile=duration_profile,
    )


def _validate_posix_clean_entry(
    proof: Mapping[str, Any], *, repository: Path
) -> None:
    """Require a native/WSL collector to match the env -i launch contract."""

    tools = proof.get("tools")
    marker = os.environ.get("AGENTOS_EVALUATION_EXECUTION_DOMAIN", "")
    if not isinstance(tools, dict) or set(tools) != set(TOOL_LABELS):
        raise PlatformPreflightError("formal POSIX tool binding is incomplete")
    if marker == NATIVE_REENTRY_MARKER:
        language = "C.UTF-8"
        path_directories: list[str] = []
        required = {"TMPDIR": "/tmp"}
    elif marker.startswith("windows-wsl:"):
        distro = marker.removeprefix("windows-wsl:")
        if DISTRO_RE.fullmatch(distro) is None:
            raise PlatformPreflightError("formal WSL re-entry marker is invalid")
        language = "C"
        path_directories = ["/usr/bin", "/bin"]
        required = {}
    else:
        raise PlatformPreflightError("formal POSIX collection did not use clean re-entry")
    for tool in tools.values():
        directory = str(PurePosixPath(tool["path"]).parent)
        if directory not in path_directories:
            path_directories.append(directory)
    expected = {
        "AGENT_TEST_DURATION_PROFILE": _bound_duration_profile_name(
            proof, repository=repository
        ),
        "AGENTOS_EVALUATION_EXECUTION_DOMAIN": marker,
        "BASH_BIN": tools["bash"]["path"], "CC": tools["host_cc"]["path"],
        "HOME": "/tmp", "HOSTCC": tools["host_cc"]["path"],
        "HOST_CC": tools["host_cc"]["path"], "LANG": language,
        "LC_ALL": language, "MAKE_TOOL": tools["make"]["path"],
        "PATH": ":".join(path_directories), "PYTHONHASHSEED": "0",
        "PYTHON_BIN": tools["python"]["path"], "QEMU": tools["qemu"]["path"],
        "SIZE_TOOL": tools["size"]["path"], "TOOLPREFIX": proof["toolprefix"],
        "TZ": "UTC", **required,
    }
    if marker.startswith("windows-wsl:"):
        expected["EVALUATION_WSL_DISTRO"] = marker.removeprefix("windows-wsl:")
    else:
        expected["EVALUATION_WSL_DISTRO"] = os.environ.get(
            "EVALUATION_WSL_DISTRO", "Ubuntu"
        )
    mismatched = [name for name, value in expected.items() if os.environ.get(name) != value]
    allowed = set(expected) | set(FORWARDED_ENVIRONMENT_NAMES) | set(
        PATH_ENVIRONMENT_NAMES
    ) | {"AGENTOS_EVALUATION_CAMPAIGN_TOKEN", "OLDPWD", "PWD", "SHLVL", "_"}
    unexpected = sorted(set(os.environ) - allowed)
    for name in (*FORWARDED_ENVIRONMENT_NAMES, *PATH_ENVIRONMENT_NAMES):
        value = os.environ.get(name)
        if value is not None and any(character in value for character in "\x00\r\n"):
            mismatched.append(name)
    if mismatched or unexpected:
        raise PlatformPreflightError(
            "formal POSIX re-entry is not the controlled env -i domain: "
            f"mismatched={sorted(set(mismatched))} unexpected={unexpected}"
        )


def probe_native_collection_domain(
    *,
    repo: Path,
    toolprefix: str,
    qemu: str,
    python_bin: str,
    host_cc: str,
    duration_profile: str,
    shell_bin: str = "bash",
) -> dict[str, Any]:
    """Probe a domain already entered by the formal collection process."""

    if os.name == "posix" and sys.platform == "cygwin":
        proof = probe_native_msys2_domain(
            repo=repo,
            toolprefix=toolprefix,
            qemu=qemu,
            python_bin=python_bin,
            host_cc=host_cc,
            duration_profile=duration_profile,
            shell_bin=shell_bin,
        )
        _validate_msys_clean_entry(
            tools=proof["tools"], toolprefix=proof["toolprefix"],
            duration_profile_name=_bound_duration_profile_name(
                proof, repository=repo
            ),
            temporary_directory=Path(proof["temporary_directory"]),
            windows_temporary_directory=proof["windows_temporary_directory"],
            windows_system_drive=proof["windows_system_drive"],
        )
        return proof
    proof = probe_native_linux_domain(
        repo=repo,
        toolprefix=toolprefix,
        qemu=qemu,
        python_bin=python_bin,
        host_cc=host_cc,
        duration_profile=duration_profile,
        shell_bin=shell_bin,
    )
    _validate_posix_clean_entry(proof, repository=repo)
    return proof


_WSL_PROBE_PROGRAM = r"""
import hashlib,json,os,platform,re,shutil,subprocess,sys
from pathlib import Path

repo=Path(sys.argv[1]).resolve()
prefix=sys.argv[2]
qemu=sys.argv[3]
requested_distro=sys.argv[4]
host_cc=sys.argv[5]
hardware_source="procfs:/proc/cpuinfo+/proc/meminfo"
names={
 "bash":"bash","env":"env","git":"git","make":"make","python":"python3",
 "assembler":prefix+"as","compiler":prefix+"gcc","host_cc":host_cc,"linker":prefix+"ld","objcopy":prefix+"objcopy",
 "objdump":prefix+"objdump","size":prefix+"size","qemu":qemu,
 "timeout":"timeout","readlink":"readlink","sha256sum":"sha256sum",
}
def sha(path):
 h=hashlib.sha256()
 with open(path,"rb") as f:
  for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
 return h.hexdigest()
def run(argv):
 return subprocess.run(argv,cwd=repo,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
  stderr=subprocess.STDOUT,timeout=30,check=False)
def first(raw):
 return next((x.strip() for x in raw.decode("utf-8","replace").splitlines() if x.strip()),"")
def normalized(value,limit=512):
 value=" ".join(value.split())
 if not value or len(value)>limit or any(ord(c)<32 or ord(c)==127 for c in value):
  raise SystemExit("malformed procfs hardware field")
 return value
def hardware():
 paths=(Path("/proc/cpuinfo"),Path("/proc/meminfo"))
 texts=[]
 for path in paths:
  if path.is_symlink() or not path.is_file(): raise SystemExit("procfs hardware source unavailable")
  raw=path.read_bytes()
  if not raw or len(raw)>16*1024*1024: raise SystemExit("procfs hardware source unbounded")
  try: texts.append(raw.decode("utf-8","strict"))
  except UnicodeError: raise SystemExit("procfs hardware source is not UTF-8")
 cpuinfo,meminfo=texts
 if "\0" in cpuinfo+meminfo: raise SystemExit("procfs hardware source contains NUL")
 fields={}
 processor_ids=[]
 for line in cpuinfo.splitlines():
  if ":" not in line: continue
  raw_key,raw_value=line.split(":",1)
  key=" ".join(raw_key.casefold().split())
  value=" ".join(raw_value.split())
  if not key or not value: continue
  fields.setdefault(key,[]).append(value)
  if raw_key.strip()=="processor":
   if not re.fullmatch(r"[0-9]{1,10}",value): raise SystemExit("malformed procfs processor identifier")
   processor_ids.append(int(value))
 if not processor_ids or len(set(processor_ids))!=len(processor_ids) or len(processor_ids)>65536:
  raise SystemExit("malformed procfs logical CPU records")
 models=[]
 for key in ("model name","hardware","cpu","machine"):
  if fields.get(key): models=fields[key]; break
 if not models:
  models=[value for value in fields.get("processor",[]) if not value.isdecimal()]
 if models:
  cpu_model=" | ".join(sorted({normalized(value,256) for value in models}))
 else:
  parts=[]
  for key in ("vendor_id","cpu implementer","cpu architecture","cpu part","model"):
   values=fields.get(key,[])
   if values: parts.append(key+"="+",".join(sorted({normalized(value,64) for value in values})))
  cpu_model="; ".join(parts)
 cpu_model=normalized(cpu_model)
 matches=re.findall(r"(?m)^MemTotal:[ \t]*([0-9]{1,20})[ \t]+kB[ \t]*$",meminfo)
 if len(matches)!=1: raise SystemExit("malformed procfs MemTotal")
 memory_total_bytes=int(matches[0])*1024
 if not 1024<=memory_total_bytes<=(1<<63)-1: raise SystemExit("malformed procfs total memory")
 return {"cpu_model":cpu_model,"logical_cpu_count":len(processor_ids),
  "memory_total_bytes":memory_total_bytes,"source":hardware_source}
if platform.system()!="Linux": raise SystemExit("selected WSL domain is not Linux")
observed_distro=os.environ.get("WSL_DISTRO_NAME","")
if not observed_distro or observed_distro.casefold()!=requested_distro.casefold():
 raise SystemExit("selected WSL distribution identity differs")
git=shutil.which("git")
if not git: raise SystemExit("git unavailable")
root=run([git,"rev-parse","--show-toplevel"])
if root.returncode or Path(first(root.stdout)).resolve()!=repo:
 raise SystemExit("repository root differs inside WSL")
tools={}
compiler_invocation=""
for label,name in names.items():
 invocation=shutil.which(name)
 if not invocation: raise SystemExit("required executable unavailable: "+name)
 if label=="compiler": compiler_invocation=invocation
 path=str(Path(invocation).resolve())
 if label=="python":
  result=run([path,"-I","-S","-c","import os,platform,sys; print('%d.%d.%d' % sys.version_info[:3]); print(platform.python_implementation()); print('%d%d%d%d' % (sys.flags.isolated,sys.flags.no_site,int(bool(getattr(sys.flags,'safe_path',False) or (sys.flags.isolated and '' not in sys.path and os.path.abspath(os.getcwd()) not in [os.path.abspath(p) for p in sys.path]))),sys.flags.ignore_environment))"])
  lines=[x.strip() for x in result.stdout.decode("utf-8","replace").splitlines() if x.strip()]
  if result.returncode or len(lines)!=3 or lines[1]!="CPython" or lines[2]!="1111": raise SystemExit("Python version probe failed")
  version=tuple(map(int,lines[0].split(".")))
  if version<(3,10): raise SystemExit("formal evaluation requires Python >= 3.10")
  version_text=lines[1]+" "+lines[0]
 else:
  result=run([path,"--version"])
  version_text=first(result.stdout)
  if result.returncode or not version_text: raise SystemExit(label+" version probe failed")
 tools[label]={"argv0":name,"path":path,"sha256":sha(path),"version":version_text}
machine=run([tools["compiler"]["path"],"-dumpmachine"])
target=first(machine.stdout)
if machine.returncode or re.fullmatch(r"riscv64(?:[-_].+)?",target) is None:
 raise SystemExit("compiler is not RISC-V 64")
machine_help=run([tools["qemu"]["path"],"-machine","help"])
if machine_help.returncode or re.search(r"(?m)^virt(?:\s|$)",machine_help.stdout.decode("utf-8","replace")) is None:
 raise SystemExit("QEMU lacks the RISC-V virt machine")
if not compiler_invocation.endswith("gcc"): raise SystemExit("compiler cannot derive TOOLPREFIX")
resolved_prefix=str(Path(compiler_invocation).absolute())[:-3]
print("__AGENTOS_PLATFORM_JSON__"+json.dumps({"distribution":observed_distro,"kernel":platform.release(),
 "hardware":hardware(),"repository":str(repo),"toolprefix":resolved_prefix,"tools":tools},
 sort_keys=True,separators=(",",":")))
"""


def _wsl_launcher_identity(wsl: Path, *, cwd: Path) -> dict[str, str]:
    result = _run_bytes([str(wsl), "--version"], cwd=cwd)
    if result.returncode == 0:
        try:
            version = _first_line(result.stdout, "WSL")
        except PlatformPreflightError:
            version = "unavailable; executable identity bound by SHA256"
    else:
        # Old inbox WSL has no --version.  Successful distro execution below is
        # the usability proof; the launcher file hash remains its identity.
        version = "unavailable; executable identity bound by SHA256"
    return {
        "argv0": "wsl.exe",
        "path": str(wsl),
        "sha256": _sha256(wsl),
        "version": version,
    }


def _wsl_path(wsl: Path, distro: str, path: Path, *, cwd: Path) -> str:
    path = _safe_directory(path, "WSL source directory")
    result = _run_bytes(
        [
            str(wsl),
            "-d",
            distro,
            "--",
            "/usr/bin/env",
            "-i",
            "HOME=/tmp",
            "LANG=C.UTF-8",
            "LC_ALL=C.UTF-8",
            "PATH=/usr/bin:/bin",
            f"WSL_DISTRO_NAME={distro}",
            "/usr/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            'value=$(wslpath -a -u "$1") || exit; '
            'printf "__AGENTOS_WSLPATH__%s\\n" "$value"',
            "agentos-wslpath",
            str(path),
        ],
        cwd=cwd,
    )
    output = _decode_output(result.stdout)
    matches = [
        line.removeprefix("__AGENTOS_WSLPATH__")
        for line in output.splitlines()
        if line.startswith("__AGENTOS_WSLPATH__")
    ]
    converted = matches[0] if len(matches) == 1 else ""
    pure = PurePosixPath(converted)
    if (
        result.returncode != 0
        or not converted.startswith("/")
        or "\\" in converted
        or not pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        detail = output.strip()[-1000:]
        raise PlatformPreflightError(
            f"wslpath could not map the repository in distribution {distro}: {detail}"
        )
    return converted


def probe_windows_wsl_domain(
    *,
    repo: Path,
    distro: str,
    toolprefix: str,
    qemu: str,
    host_cc: str,
    duration_profile: str,
) -> dict[str, Any]:
    if os.name != "nt":
        raise PlatformPreflightError("Windows WSL preflight requires a Windows Host")
    if DISTRO_RE.fullmatch(distro) is None:
        raise PlatformPreflightError("invalid WSL distribution name")
    root = _resolved_safe_directory(repo, "repository root")
    wsl = _regular_executable("wsl.exe")
    launcher = _wsl_launcher_identity(wsl, cwd=root)
    execution_root = _wsl_path(wsl, distro, root, cwd=root)
    result = _run_bytes(
        [
            str(wsl),
            "-d",
            distro,
            "--",
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            'for tool in bash env python3 git make timeout readlink sha256sum wslpath; do '
            'command -v -- "$tool" >/dev/null || exit 127; done; '
            'exec python3 -I -S -c "$1" "$2" "$3" "$4" "$5" "$6"',
            "agentos-platform-probe",
            _WSL_PROBE_PROGRAM,
            execution_root,
            toolprefix,
            qemu,
            distro,
            host_cc,
        ],
        cwd=root,
        timeout=120,
    )
    if result.returncode != 0:
        detail = _decode_output(result.stdout).strip()[-2000:]
        raise PlatformPreflightError(
            f"selected WSL distribution is not a complete evaluation domain: {detail}"
        )
    output = _decode_output(result.stdout)
    payloads = [
        line.removeprefix("__AGENTOS_PLATFORM_JSON__")
        for line in output.splitlines()
        if line.startswith("__AGENTOS_PLATFORM_JSON__")
    ]
    try:
        if len(payloads) != 1:
            raise json.JSONDecodeError("missing unique platform marker", output, 0)
        observed = json.loads(payloads[0])
    except json.JSONDecodeError as error:
        raise PlatformPreflightError("WSL platform probe returned invalid JSON") from error
    if (
        not isinstance(observed, dict)
        or set(observed) != {
            "distribution", "hardware", "kernel", "repository", "toolprefix",
            "tools",
        }
        or observed.get("repository") != execution_root
        or observed.get("distribution", "").casefold() != distro.casefold()
        or set(observed.get("tools", {})) != set(TOOL_LABELS)
    ):
        raise PlatformPreflightError("WSL platform probe identity is incomplete")
    proof = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "status": "ready",
        "domain": "windows-wsl",
        "distribution": observed["distribution"],
        "hardware": _validate_hardware_identity(observed.get("hardware")),
        "repository": {"host_path": str(root), "execution_path": execution_root},
        "launcher": launcher,
        "kernel": observed.get("kernel", ""),
        "toolprefix": observed["toolprefix"],
        "tools": observed["tools"],
    }
    return _finalize_platform_proof(
        root,
        proof,
        requested_host_cc=host_cc,
        duration_profile=duration_profile,
    )


def probe_execution_domain(
    *,
    repo: Path,
    distro: str,
    toolprefix: str,
    qemu: str,
    python_bin: str,
    shell_bin: str,
    host_cc: str,
    duration_profile: str,
) -> dict[str, Any]:
    if os.name == "nt":
        return probe_windows_wsl_domain(
            repo=repo, distro=distro, toolprefix=toolprefix, qemu=qemu,
            host_cc=host_cc, duration_profile=duration_profile,
        )
    if sys.platform == "cygwin":
        return probe_native_msys2_domain(
            repo=repo, toolprefix=toolprefix, qemu=qemu,
            python_bin=python_bin, host_cc=host_cc,
            duration_profile=duration_profile, shell_bin=shell_bin,
        )
    return probe_native_linux_domain(
        repo=repo,
        toolprefix=toolprefix,
        qemu=qemu,
        python_bin=python_bin,
        host_cc=host_cc,
        duration_profile=duration_profile,
        shell_bin=shell_bin,
    )


def _is_windows_absolute(value: str) -> bool:
    return PureWindowsPath(value).is_absolute()


def _convert_forwarded_path(
    *, wsl: Path, distro: str, repo: Path, value: str
) -> str:
    if not value:
        return value
    if _is_windows_absolute(value):
        return _wsl_path(wsl, distro, Path(value), cwd=repo)
    if value.startswith("/"):
        raise PlatformPreflightError(
            "Windows Host path settings must be relative or Windows-absolute"
        )
    pure = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise PlatformPreflightError("evaluation path setting is not canonical")
    return value


def execute_in_wsl(
    *,
    preflight: dict[str, Any],
    repo: Path,
    distro: str,
    script_relative: str,
    mode: str,
    host_environment: Mapping[str, str],
) -> int:
    if preflight.get("domain") != "windows-wsl":
        raise PlatformPreflightError("WSL re-exec requires a verified Windows WSL domain")
    launcher = _safe_regular_file(
        Path(preflight["launcher"]["path"]), "WSL launcher"
    )
    if _sha256(launcher) != preflight["launcher"]["sha256"]:
        raise PlatformPreflightError("WSL launcher changed after preflight")
    tools = preflight["tools"]
    duration_name = _bound_duration_profile_name(preflight, repository=repo)
    root = _resolved_safe_directory(repo, "repository root")
    execution_root = preflight["repository"]["execution_path"]
    script = (PurePosixPath(execution_root) / script_relative).as_posix()
    environment = {
        "AGENT_TEST_DURATION_PROFILE": duration_name,
        "AGENTOS_EVALUATION_EXECUTION_DOMAIN": f"windows-wsl:{preflight['distribution']}",
        "BASH_BIN": tools["bash"]["path"],
        "CC": tools["host_cc"]["path"],
        "EVALUATION_WSL_DISTRO": preflight["distribution"],
        "HOSTCC": tools["host_cc"]["path"],
        "HOST_CC": tools["host_cc"]["path"],
        "MAKE_TOOL": tools["make"]["path"],
        "PYTHON_BIN": tools["python"]["path"],
        "QEMU": tools["qemu"]["path"],
        "SIZE_TOOL": tools["size"]["path"],
        "TOOLPREFIX": preflight["toolprefix"],
    }
    for name in FORWARDED_ENVIRONMENT_NAMES:
        value = host_environment.get(name)
        if value is not None and value != "":
            if "\x00" in value or "\n" in value or "\r" in value:
                raise PlatformPreflightError(f"invalid environment value: {name}")
            environment[name] = value
    for name in PATH_ENVIRONMENT_NAMES:
        value = host_environment.get(name)
        if value is not None and value != "":
            environment[name] = _convert_forwarded_path(
                wsl=launcher, distro=distro, repo=root, value=value
            )
    path_directories: list[str] = ["/usr/bin", "/bin"]
    for tool in tools.values():
        directory = str(PurePosixPath(tool["path"]).parent)
        if directory not in path_directories:
            path_directories.append(directory)
    argv = [
        str(launcher),
        "-d",
        distro,
        "--",
        tools["env"]["path"],
        "-i",
        "HOME=/tmp",
        "LANG=C",
        "LC_ALL=C",
        f"PATH={':'.join(path_directories)}",
        "PYTHONHASHSEED=0",
        "TZ=UTC",
        *(f"{name}={value}" for name, value in sorted(environment.items())),
        tools["bash"]["path"],
        "--noprofile",
        "--norc",
        script,
        mode,
    ]
    try:
        return int(subprocess.run(
            argv, cwd=root, env=_bootstrap_environment(host_environment), check=False
        ).returncode)
    except OSError as error:
        raise PlatformPreflightError(f"cannot enter verified WSL domain: {error}") from error


def _verify_bound_msys2_preflight(
    preflight: Mapping[str, Any], *, repo: Path
) -> None:
    _current_msys2_identity()
    if preflight.get("domain") != "native-msys2":
        raise PlatformPreflightError("MSYS2 re-exec requires a verified native-msys2 domain")
    _bound_duration_profile_name(preflight, repository=repo)
    root = _resolved_safe_directory(repo, "MSYS2 repository")
    repository = preflight.get("repository")
    if not isinstance(repository, dict) or repository.get("execution_path") != str(
        root
    ):
        raise PlatformPreflightError("MSYS2 repository changed after preflight")
    runtime = preflight.get("runtime")
    if not isinstance(runtime, dict):
        raise PlatformPreflightError("MSYS2 runtime binding is unavailable")
    runtime_path = _safe_regular_file(
        Path(str(runtime.get("path", ""))), "MSYS2 runtime"
    )
    if _sha256(runtime_path) != runtime.get("sha256"):
        raise PlatformPreflightError("MSYS2 runtime changed after preflight")
    _safe_directory(
        Path(str(preflight.get("temporary_directory", ""))),
        "MSYS2 temporary directory",
    )
    windows_temporary_directory = str(
        preflight.get("windows_temporary_directory", "")
    )
    windows_system_drive = str(preflight.get("windows_system_drive", ""))
    if not PureWindowsPath(windows_temporary_directory).is_absolute():
        raise PlatformPreflightError("MSYS2 Windows temporary mapping is invalid")
    if (
        re.fullmatch(r"[A-Z]:", windows_system_drive) is None
        or _canonical_windows_system_drive() != windows_system_drive
    ):
        raise PlatformPreflightError("MSYS2 Windows system drive binding changed")
    tools = preflight.get("tools")
    if not isinstance(tools, dict) or set(tools) != set(MSYS_TOOL_LABELS):
        raise PlatformPreflightError("MSYS2 tool binding is incomplete")
    for label, value in tools.items():
        if not isinstance(value, dict):
            raise PlatformPreflightError(f"MSYS2 tool binding is invalid: {label}")
        path = _safe_regular_file(
            Path(str(value.get("path", ""))), f"MSYS2 tool {label}"
        )
        if _sha256(path) != value.get("sha256"):
            raise PlatformPreflightError(f"MSYS2 tool changed after preflight: {label}")
    _verify_hardware_identity(preflight.get("hardware"))


def _convert_msys_forwarded_path(value: str) -> str:
    if not value:
        return value
    if _is_windows_absolute(value) or "\\" in value:
        raise PlatformPreflightError(
            "native-msys2 path settings must use its POSIX namespace"
        )
    pure = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise PlatformPreflightError("evaluation path setting is not canonical")
    return value


def _bootstrap_environment(host_environment: Mapping[str, str]) -> dict[str, str]:
    """Clear loader/startup injection before the attested env tool begins."""

    if os.name != "nt" and sys.platform != "cygwin":
        return {}
    environment = {
        name: host_environment[name]
        for name in ("SYSTEMROOT", "WINDIR") if host_environment.get(name)
    }
    return environment


def execute_in_msys2(
    *,
    preflight: dict[str, Any],
    repo: Path,
    script_relative: str,
    mode: str,
    host_environment: Mapping[str, str],
) -> int:
    """Re-enter every formal stage through the bound MSYS2 clean environment."""

    if mode not in FORMAL_MODES:
        raise PlatformPreflightError(f"unsupported MSYS2 formal mode: {mode}")
    root = _resolved_safe_directory(repo, "repository root")
    _verify_bound_msys2_preflight(preflight, repo=root)
    tools = preflight["tools"]
    duration_name = _bound_duration_profile_name(preflight, repository=root)
    script_path = _resolved_safe_file(
        root / script_relative, "MSYS2 evaluation script"
    )
    script = str(script_path)
    _canonical_posix_path(script, "evaluation script")

    environment = {
        "AGENT_TEST_DURATION_PROFILE": duration_name,
        "AGENTOS_EVALUATION_EXECUTION_DOMAIN": MSYS_REENTRY_MARKER,
        "BASH_BIN": tools["bash"]["path"],
        "CC": tools["host_cc"]["path"],
        "EVALUATION_WSL_DISTRO": host_environment.get(
            "EVALUATION_WSL_DISTRO", "Ubuntu"
        ),
        "HOSTCC": tools["host_cc"]["path"],
        "HOST_CC": tools["host_cc"]["path"],
        "MAKE_TOOL": tools["make"]["path"],
        "PYTHON_BIN": tools["python"]["path"],
        "QEMU": tools["qemu"]["path"],
        "SIZE_TOOL": tools["size"]["path"],
        "SYSTEMDRIVE": preflight["windows_system_drive"],
        "TOOLPREFIX": preflight["toolprefix"],
        "TMPDIR": preflight["temporary_directory"],
        "TEMP": preflight["windows_temporary_directory"],
        "TMP": preflight["windows_temporary_directory"],
    }
    distro = environment["EVALUATION_WSL_DISTRO"]
    if DISTRO_RE.fullmatch(distro) is None:
        raise PlatformPreflightError("invalid compatibility distribution label")
    for name in FORWARDED_ENVIRONMENT_NAMES:
        value = host_environment.get(name)
        if value is not None and value != "":
            if "\x00" in value or "\n" in value or "\r" in value:
                raise PlatformPreflightError(f"invalid environment value: {name}")
            environment[name] = value
    for name in PATH_ENVIRONMENT_NAMES:
        value = host_environment.get(name)
        if value is not None and value != "":
            environment[name] = _convert_msys_forwarded_path(value)

    controlled_path = _msys_controlled_path(tools)
    argv = [
        tools["env"]["path"],
        "-i",
        "HOME=/tmp",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        f"PATH={controlled_path}",
        "PYTHONHASHSEED=0",
        "TZ=UTC",
        "MSYSTEM=MSYS",
        *(f"{name}={value}" for name, value in sorted(environment.items())),
        tools["bash"]["path"],
        "--noprofile",
        "--norc",
        script,
        mode,
    ]
    try:
        return int(subprocess.run(
            argv, cwd=root, env=_bootstrap_environment(host_environment), check=False
        ).returncode)
    except OSError as error:
        raise PlatformPreflightError(f"cannot enter verified MSYS2 domain: {error}") from error


def execute_in_native_linux(
    *,
    preflight: dict[str, Any],
    repo: Path,
    script_relative: str,
    mode: str,
    host_environment: Mapping[str, str],
) -> int:
    """Enter a hash-bound native Linux Bash through an empty environment."""

    if mode not in FORMAL_MODES or preflight.get("domain") != "native-linux":
        raise PlatformPreflightError("unsupported native Linux formal execution")
    root = _resolved_safe_directory(repo, "repository root")
    repository = preflight.get("repository")
    if not isinstance(repository, dict) or repository.get("execution_path") != str(root):
        raise PlatformPreflightError("native Linux repository changed after preflight")
    tools = preflight.get("tools")
    if not isinstance(tools, dict) or set(tools) != set(TOOL_LABELS):
        raise PlatformPreflightError("native Linux tool binding is incomplete")
    for label, value in tools.items():
        if not isinstance(value, dict):
            raise PlatformPreflightError(f"native Linux tool binding is invalid: {label}")
        path = _safe_regular_file(Path(str(value.get("path", ""))), f"native tool {label}")
        if _sha256(path) != value.get("sha256"):
            raise PlatformPreflightError(f"native Linux tool changed after preflight: {label}")
    _verify_hardware_identity(preflight.get("hardware"))
    duration_name = _bound_duration_profile_name(preflight, repository=root)
    relative = PurePosixPath(script_relative)
    if (
        relative.is_absolute()
        or relative.as_posix() != script_relative
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise PlatformPreflightError("native evaluation script path is not canonical")
    script = _resolved_safe_file(root.joinpath(*relative.parts), "native evaluation script")
    environment = {
        "AGENT_TEST_DURATION_PROFILE": duration_name,
        "AGENTOS_EVALUATION_EXECUTION_DOMAIN": NATIVE_REENTRY_MARKER,
        "BASH_BIN": tools["bash"]["path"],
        "CC": tools["host_cc"]["path"],
        "EVALUATION_WSL_DISTRO": host_environment.get("EVALUATION_WSL_DISTRO", "Ubuntu"),
        "HOSTCC": tools["host_cc"]["path"],
        "HOST_CC": tools["host_cc"]["path"],
        "MAKE_TOOL": tools["make"]["path"],
        "PYTHON_BIN": tools["python"]["path"],
        "QEMU": tools["qemu"]["path"],
        "SIZE_TOOL": tools["size"]["path"],
        "TOOLPREFIX": preflight["toolprefix"],
    }
    if DISTRO_RE.fullmatch(environment["EVALUATION_WSL_DISTRO"]) is None:
        raise PlatformPreflightError("invalid compatibility distribution label")
    for name in FORWARDED_ENVIRONMENT_NAMES:
        value = host_environment.get(name)
        if value:
            if any(character in value for character in "\x00\r\n"):
                raise PlatformPreflightError(f"invalid environment value: {name}")
            environment[name] = value
    for name in PATH_ENVIRONMENT_NAMES:
        value = host_environment.get(name)
        if value:
            environment[name] = _convert_msys_forwarded_path(value)
    path_directories: list[str] = []
    for value in tools.values():
        directory = str(PurePosixPath(value["path"]).parent)
        if directory not in path_directories:
            path_directories.append(directory)
    argv = [
        tools["env"]["path"], "-i", "HOME=/tmp", "TMPDIR=/tmp",
        "LANG=C.UTF-8", "LC_ALL=C.UTF-8", f"PATH={':'.join(path_directories)}",
        "PYTHONHASHSEED=0", "TZ=UTC",
        *(f"{name}={value}" for name, value in sorted(environment.items())),
        tools["bash"]["path"], "--noprofile", "--norc", str(script), mode,
    ]
    try:
        return int(subprocess.run(
            argv, cwd=root, env=_bootstrap_environment(host_environment), check=False
        ).returncode)
    except OSError as error:
        raise PlatformPreflightError(f"cannot enter native Linux domain: {error}") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preflight the formal evaluation domain.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "formal-exec", "wsl-exec", "msys-exec"):
        command = commands.add_parser(name)
        command.add_argument("--repo", type=Path, required=True)
        command.add_argument("--distro", default="Ubuntu")
        command.add_argument("--toolprefix", default="riscv64-linux-gnu-")
        command.add_argument("--qemu", default="qemu-system-riscv64")
        command.add_argument("--python-bin", default="python3")
        command.add_argument("--shell-bin", default="bash")
        command.add_argument("--host-cc", required=True)
        command.add_argument(
            "--duration-profile", choices=sorted(DURATION_PROFILES), required=True
        )
        if name in {"formal-exec", "wsl-exec", "msys-exec"}:
            command.add_argument("--script-relative", required=True)
            command.add_argument("--mode", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        preflight = probe_execution_domain(
            repo=args.repo,
            distro=args.distro,
            toolprefix=args.toolprefix,
            qemu=args.qemu,
            python_bin=args.python_bin,
            shell_bin=args.shell_bin,
            host_cc=args.host_cc,
            duration_profile=args.duration_profile,
        )
        print(json.dumps(preflight, indent=2, sort_keys=True, ensure_ascii=True))
        if args.command == "formal-exec":
            if preflight["domain"] == "windows-wsl":
                return execute_in_wsl(
                    preflight=preflight, repo=args.repo, distro=args.distro,
                    script_relative=args.script_relative, mode=args.mode,
                    host_environment=os.environ,
                )
            if preflight["domain"] == "native-msys2":
                return execute_in_msys2(
                    preflight=preflight, repo=args.repo,
                    script_relative=args.script_relative, mode=args.mode,
                    host_environment=os.environ,
                )
            return execute_in_native_linux(
                preflight=preflight, repo=args.repo,
                script_relative=args.script_relative, mode=args.mode,
                host_environment=os.environ,
            )
        if args.command == "wsl-exec":
            if os.name != "nt":
                raise PlatformPreflightError("wsl-exec is only valid on a Windows Host")
            return execute_in_wsl(
                preflight=preflight,
                repo=args.repo,
                distro=args.distro,
                script_relative=args.script_relative,
                mode=args.mode,
                host_environment=os.environ,
            )
        if args.command == "msys-exec":
            if os.name != "posix" or sys.platform != "cygwin":
                raise PlatformPreflightError(
                    "msys-exec is only valid inside the native MSYS2 runtime"
                )
            return execute_in_msys2(
                preflight=preflight,
                repo=args.repo,
                script_relative=args.script_relative,
                mode=args.mode,
                host_environment=os.environ,
            )
    except (PlatformPreflightError, OSError, UnicodeError) as error:
        print(f"evaluation platform preflight failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
