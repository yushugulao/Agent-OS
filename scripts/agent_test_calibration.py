#!/usr/bin/env python3
"""Collect and verify one commit-bound Agent test calibration campaign."""

import argparse
import gzip
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import secrets
import shutil
import stat
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
import zlib
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from functools import lru_cache
from pathlib import Path, PurePosixPath


# The collector dynamically loads the reviewed budget checker before its child
# environment exists.  Keep every invocation style from modifying the source
# checkout, including Python -I which ignores PYTHONDONTWRITEBYTECODE.
sys.dont_write_bytecode = True


class CalibrationError(ValueError):
    pass


EVIDENCE_SCOPE = "local_e3_unsigned"
ATTESTATION_FORMAT = "agentos-qemu-execution-attestation-v2"
CAMPAIGN_PURPOSE = "agent_test_suite_duration_calibration"
ROUND_COUNT = 3
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_OBJECT_RE = re.compile(r"[0-9a-f]{40}\Z")
UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
PRELUDE_KEY = "agent-mechanism:context-sync-atomicity"
FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
)
GIT_REDIRECT_ENVIRONMENT = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_REPLACE_REF_BASE",
        "GIT_WORK_TREE",
    }
)
GIT_REPOSITORY_REDIRECT_ENVIRONMENT = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_REPLACE_REF_BASE",
        "GIT_WORK_TREE",
    }
)
CALIBRATION_TOOL_NAMES = (
    "qemu",
    "toolchain_cc",
    "toolchain_ld",
    "toolchain_objcopy",
    "toolchain_objdump",
    "toolchain_as",
    "host_cc",
    "python",
    "bash",
    "make",
    "git",
)
CALIBRATION_HOST_ENV_ALLOWLIST = frozenset(
    {
        "ALLUSERSPROFILE",
        "COMSPEC",
        "HOME",
        "MSYSTEM",
        "PATHEXT",
        "PROGRAMDATA",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "USERPROFILE",
        "WINDIR",
    }
)
CALIBRATION_GENERATED_PREFIXES = (
    "build/",
    "user/asm/",
    "user/build/",
    "user/target/",
)
CALIBRATION_GENERATED_FILES = frozenset(
    {
        "nfs/fs",
        "nfs/fs-copy.img",
        "nfs/fs.img",
        "os/initproc.S",
    }
)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def calibrated_limit(totals):
    values = tuple(Decimal(str(value)) for value in totals)
    median = statistics.median(values)
    candidate = max(max(values), median * Decimal("1.05"))
    return float(candidate.quantize(Decimal("0.001"), rounding=ROUND_CEILING))


@lru_cache(maxsize=1)
def msys_native_path_api():
    if os.name != "posix" or sys.platform != "cygwin":
        return None
    import ctypes

    runtime = None
    for name in ("msys-2.0.dll", "cygwin1.dll"):
        try:
            candidate = ctypes.CDLL(name)
        except OSError:
            continue
        if hasattr(candidate, "cygwin_conv_path"):
            runtime = candidate
            break
    if runtime is None:
        return None
    converter = runtime.cygwin_conv_path
    converter.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    converter.restype = ctypes.c_ssize_t
    attributes = ctypes.CDLL("Kernel32.dll").GetFileAttributesW
    attributes.argtypes = [ctypes.c_wchar_p]
    attributes.restype = ctypes.c_uint32
    return ctypes, converter, attributes


def msys_native_file_attributes(path):
    api = msys_native_path_api()
    if api is None:
        return None
    ctypes, converter, attributes = api
    encoded = os.fsencode(os.path.abspath(os.fspath(path)))
    required = converter(1, encoded, None, 0)
    if required <= 0 or required % 2:
        raise CalibrationError(f"cannot convert path for reparse check: {path}")
    buffer = ctypes.create_string_buffer(required)
    if converter(1, encoded, buffer, required) != 0:
        raise CalibrationError(f"cannot convert path for reparse check: {path}")
    native = buffer.raw.decode("utf-16-le").split("\0", 1)[0]
    value = int(attributes(native))
    if value == 0xFFFFFFFF:
        raise CalibrationError(f"cannot inspect path reparse attributes: {path}")
    return value


def path_is_link(path, info=None):
    try:
        info = path.lstat() if info is None else info
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    if int(getattr(info, "st_file_attributes", 0)) & FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    junction = getattr(path, "is_junction", None)
    try:
        if junction is not None and junction():
            return True
    except OSError:
        return True
    native = msys_native_file_attributes(path)
    return bool(native is not None and native & FILE_ATTRIBUTE_REPARSE_POINT)


def reject_link_components(path, label):
    absolute = Path(os.path.abspath(os.fspath(path)))
    components = [absolute]
    while components[-1].parent != components[-1]:
        components.append(components[-1].parent)
    for component in reversed(components):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if path_is_link(component, info):
            raise CalibrationError(f"{label} traverses a link or junction")
    return absolute


def strict_json_bytes(data, label):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CalibrationError(
                    f"{label} contains duplicate key {key!r}"
                )
            result[key] = value
        return result

    def reject_constant(value):
        raise CalibrationError(f"{label} contains non-finite value {value}")

    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CalibrationError(f"cannot parse {label}: {error}") from error


def read_strict_json(path, label):
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise CalibrationError(f"cannot read {label}: {error}") from error
    return strict_json_bytes(data, label)


def exact_object(value, fields, label):
    if not isinstance(value, dict):
        raise CalibrationError(f"{label} must be an object")
    actual = set(value)
    expected = set(fields)
    if actual != expected:
        raise CalibrationError(
            f"{label} fields mismatch: missing={sorted(expected - actual)!r}, "
            f"extra={sorted(actual - expected)!r}"
        )
    return value


def require_text(value, label, pattern=None):
    if not isinstance(value, str) or not value:
        raise CalibrationError(f"{label} must be non-empty text")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise CalibrationError(f"{label} has an invalid format")
    return value


def require_int(value, label, positive=False):
    if not isinstance(value, int) or isinstance(value, bool):
        raise CalibrationError(f"{label} must be an integer")
    if positive and value <= 0:
        raise CalibrationError(f"{label} must be positive")
    return value


def regular_file(path, label):
    path = reject_link_components(path, label)
    try:
        lexical = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise CalibrationError(f"cannot inspect {label}: {error}") from error
    if path_is_link(path, lexical) or not resolved.is_file():
        raise CalibrationError(f"{label} must be a regular non-symlink file")
    digest, size = sha256_file(resolved)
    if size != lexical.st_size:
        raise CalibrationError(f"{label} changed while it was hashed")
    return resolved, {
        "path": str(resolved),
        "bytes": size,
        "sha256": digest,
    }


def executable_identity(path, label, version_args=("--version",)):
    requested = str(path)
    candidate = shutil.which(requested) if not os.path.dirname(requested) else requested
    if candidate is None:
        raise CalibrationError(f"{label} executable is not resolvable")
    resolved = Path(candidate).resolve(strict=True)
    _, identity = regular_file(resolved, label)
    try:
        result = subprocess.run(
            [str(resolved), *version_args],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CalibrationError(f"cannot inspect {label} version: {error}") from error
    output = result.stdout.strip() or result.stderr.strip()
    first_line = output.splitlines()[0] if output else ""
    if result.returncode != 0 or not first_line:
        raise CalibrationError(f"cannot inspect {label} version")
    return {
        "requested_path": requested,
        "executable": identity,
        "version_argv": [str(resolved), *version_args],
        "version_first_line": first_line,
    }


def cpu_model():
    try:
        for line in Path("/proc/cpuinfo").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if line.lower().startswith("model name") and ":" in line:
                value = line.split(":", 1)[1].strip()
                if value:
                    return value
    except OSError:
        pass
    value = platform.processor().strip() or os.environ.get(
        "PROCESSOR_IDENTIFIER", ""
    ).strip()
    if not value:
        raise CalibrationError("cannot determine calibration CPU model")
    return value


def runtime_profile():
    system = platform.system()
    release = platform.release()
    if system.startswith("MSYS_NT-"):
        build = system.removeprefix("MSYS_NT-").rsplit("-", 1)[-1]
        version = re.match(r"\d+\.\d+\.\d+", release)
        if not build.isdigit() or version is None:
            raise CalibrationError("cannot normalize the MSYS2 runtime profile")
        return f"MSYS2 {version.group(0)} on Windows build {build}; QEMU TCG"
    return f"{system} {release}; QEMU TCG"


def capture_host_identity():
    return {
        "platform": runtime_profile(),
        "machine": cpu_model(),
        "python_runtime": sys.version,
    }


def validate_local_calibration_profile_structure(profile):
    profile = exact_object(
        profile,
        {
            "schema_version",
            "profile_id",
            "cpu",
            "runtime",
            "toolchain_prefix",
            "tool_versions",
        },
        "local calibration profile",
    )
    if profile["schema_version"] != 1:
        raise CalibrationError("local calibration profile schema is unsupported")
    require_text(
        profile["profile_id"],
        "local calibration profile id",
        re.compile(r"[A-Za-z0-9_.-]+\Z"),
    )
    require_text(profile["cpu"], "local calibration CPU")
    require_text(profile["runtime"], "local calibration runtime")
    require_text(
        profile["toolchain_prefix"],
        "local calibration toolchain prefix",
        re.compile(r"[A-Za-z0-9_.+-]+-\Z"),
    )
    versions = exact_object(
        profile["tool_versions"],
        set(CALIBRATION_TOOL_NAMES),
        "local calibration tool versions",
    )
    for name, version in versions.items():
        require_text(version, f"local calibration {name} version")
    return profile


def version_token_matches(line, version):
    return len(
        re.findall(rf"(?<![0-9.]){re.escape(version)}(?![0-9.])", line)
    ) == 1


def validate_recorded_calibration_profile(profile, tools, host):
    profile = validate_local_calibration_profile_structure(profile)
    host = exact_object(
        host,
        {"platform", "machine", "python_runtime"},
        "calibration host",
    )
    if (
        host["platform"] != profile["runtime"]
        or host["machine"] != profile["cpu"]
        or host["python_runtime"].split()[0]
        != profile["tool_versions"]["python"]
    ):
        raise CalibrationError("calibration host does not match the local profile")
    if not isinstance(tools, dict) or set(tools) != set(CALIBRATION_TOOL_NAMES):
        raise CalibrationError("calibration executable inventory mismatch")
    versions = profile["tool_versions"]
    for name in CALIBRATION_TOOL_NAMES:
        validate_executable_identity(
            tools[name], tools[name], f"calibration executable {name}"
        )
        observed = tools[name]["version_first_line"]
        expected = versions[name]
        exact = {
            "qemu": f"QEMU emulator version {expected}",
            "python": f"Python {expected}",
            "make": f"GNU Make {expected}",
            "git": f"git version {expected}",
        }
        if name in exact:
            matches = observed == exact[name]
        else:
            matches = version_token_matches(observed, expected)
        if not matches:
            raise CalibrationError(
                f"calibration {name} version does not match the local profile"
            )
    prefix = profile["toolchain_prefix"]
    compiler_path = Path(tools["toolchain_cc"]["executable"]["path"])
    for name, suffix in (
        ("toolchain_cc", "gcc"),
        ("toolchain_ld", "ld"),
        ("toolchain_objcopy", "objcopy"),
        ("toolchain_objdump", "objdump"),
        ("toolchain_as", "as"),
    ):
        basename = tools[name]["requested_path"].replace("\\", "/").rsplit(
            "/", 1
        )[-1]
        if basename not in {f"{prefix}{suffix}", f"{prefix}{suffix}.exe"}:
            raise CalibrationError(
                f"calibration {name} does not use the profile toolchain prefix"
            )
        resolved = Path(tools[name]["executable"]["path"])
        if resolved.parent != compiler_path.parent or resolved.name not in {
            f"{prefix}{suffix}",
            f"{prefix}{suffix}.exe",
        }:
            raise CalibrationError(
                f"calibration {name} is outside the compiler toolchain directory"
            )
    return profile["profile_id"]


def validate_live_calibration_profile(profile, tools, host):
    profile_id = validate_recorded_calibration_profile(profile, tools, host)
    compiler = tools["toolchain_cc"]["executable"]["path"]
    version = run_command(
        [compiler, "-dumpfullversion", "-dumpversion"],
        Path.cwd(),
        "calibration compiler semantic version",
    ).stdout.strip()
    if version != profile["tool_versions"]["toolchain_cc"]:
        raise CalibrationError(
            "calibration compiler semantic version does not match the local profile"
        )
    return profile_id


def canonical_toolchain_prefix(profile, tools):
    validate_recorded_calibration_profile(profile, tools, capture_host_identity())
    compiler = Path(tools["toolchain_cc"]["executable"]["path"])
    suffix = f"{profile['toolchain_prefix']}gcc"
    if compiler.name not in {suffix, f"{suffix}.exe"}:
        raise CalibrationError("cannot derive canonical calibration toolchain prefix")
    return str(compiler.parent / profile["toolchain_prefix"])


def locked_tool_path(tools):
    directories = []
    for name in CALIBRATION_TOOL_NAMES:
        directory = str(Path(tools[name]["executable"]["path"]).parent)
        if directory not in directories:
            directories.append(directory)
    for directory in ("/usr/bin", "/bin"):
        if directory not in directories:
            directories.append(directory)
    return os.pathsep.join(directories)


def run_command(argv, root, label, allow_failure=False, environment=None):
    try:
        result = subprocess.run(
            argv,
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise CalibrationError(f"cannot run {label}: {error}") from error
    if result.returncode != 0 and not allow_failure:
        details = result.stderr.strip() or result.stdout.strip()
        raise CalibrationError(f"{label} failed: {details}")
    return result


def isolated_git_environment():
    redirected = sorted(
        name
        for name, value in os.environ.items()
        if value and name.upper() in GIT_REPOSITORY_REDIRECT_ENVIRONMENT
    )
    if redirected:
        raise CalibrationError(
            "Git repository redirection environment is forbidden: "
            + ", ".join(redirected)
        )
    environment = os.environ.copy()
    for name in tuple(environment):
        upper = name.upper()
        if upper.startswith("GIT_"):
            environment.pop(name, None)
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def calibration_child_environment():
    isolated = isolated_git_environment()
    if sys.platform in {"cygwin", "msys"} or os.name == "nt":
        required = {
            "ALLUSERSPROFILE": r"^[A-Za-z]:[\\/]",
            "PROGRAMDATA": r"^[A-Za-z]:[\\/]",
            "SYSTEMDRIVE": r"^[A-Za-z]:$",
        }
        inherited = {name.upper(): value for name, value in isolated.items()}
        invalid = sorted(
            name
            for name, pattern in required.items()
            if not re.match(pattern, inherited.get(name, ""))
            or "%" in inherited.get(name, "")
        )
        if invalid:
            raise CalibrationError(
                "Windows calibration environment lacks canonical system paths: "
                + ", ".join(invalid)
            )
    environment = {
        name: value
        for name, value in isolated.items()
        if name.upper() in CALIBRATION_HOST_ENV_ALLOWLIST
        or name.upper().startswith("GIT_")
    }
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "TEMP": "/tmp",
            "TMP": "/tmp",
            "TMPDIR": "/tmp",
            "TZ": "UTC",
        }
    )
    return environment


def git_executable():
    candidate = shutil.which("git")
    if candidate is None:
        raise CalibrationError("Git executable is not resolvable")
    try:
        result = Path(candidate).resolve(strict=True)
    except OSError as error:
        raise CalibrationError(f"cannot resolve Git executable: {error}") from error
    if not result.is_file() or result.is_symlink():
        raise CalibrationError("Git executable must be a regular non-symlink file")
    return result


def run_git(root, label, *arguments, allow_failure=False):
    return run_command(
        [str(git_executable()), *arguments],
        root,
        label,
        allow_failure=allow_failure,
        environment=isolated_git_environment(),
    )


def require_git_toplevel(root):
    root = reject_link_components(root, "calibration root").resolve(strict=True)
    inside = run_git(
        root, "Git worktree lookup", "rev-parse", "--is-inside-work-tree"
    ).stdout.strip()
    prefix = run_git(
        root, "Git top-level prefix lookup", "rev-parse", "--show-prefix"
    ).stdout
    if inside != "true" or prefix not in {"", "\n"}:
        raise CalibrationError("Git top-level does not match calibration root")
    return root


def validate_source_checkout(root, source_commit):
    root = require_git_toplevel(root)
    require_text(source_commit, "source commit", GIT_OBJECT_RE)
    obj = run_git(
        root, "Git commit lookup", "cat-file", "-t", source_commit
    )
    if obj.stdout.strip() != "commit":
        raise CalibrationError("calibration source is not a Git commit")
    head = run_git(
        root, "Git HEAD lookup", "rev-parse", "--verify", "HEAD"
    ).stdout.strip()
    if head != source_commit:
        raise CalibrationError("calibration HEAD does not match source commit")
    symbolic = run_git(
        root,
        "detached HEAD check",
        "symbolic-ref",
        "-q",
        "HEAD",
        allow_failure=True,
    )
    if symbolic.returncode == 0:
        raise CalibrationError("calibration worktree must be detached")
    if symbolic.returncode != 1:
        raise CalibrationError("cannot prove calibration worktree is detached")
    status = run_git(
        root,
        "clean worktree check",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if status.stdout:
        raise CalibrationError("calibration worktree must be clean")
    tree = run_git(
        root,
        "Git tree lookup",
        "rev-parse",
        f"{source_commit}^{{tree}}",
    ).stdout.strip()
    require_text(tree, "source tree", GIT_OBJECT_RE)
    return root, tree


def git_blob_hash(path, algorithm, expected_executable=None):
    try:
        before = path.lstat()
    except OSError as error:
        raise CalibrationError(f"cannot inspect tracked path {path}: {error}") from error
    if path_is_link(path, before) or not stat.S_ISREG(before.st_mode):
        raise CalibrationError(f"tracked path is not a regular file: {path}")
    attest_posix_mode = (
        os.name == "posix" and sys.platform not in {"cygwin", "msys"}
    )
    if (
        attest_posix_mode
        and expected_executable is not None
        and bool(before.st_mode & stat.S_IXUSR) != expected_executable
    ):
        raise CalibrationError(f"tracked path executable mode differs: {path}")
    digest = hashlib.new(algorithm)
    digest.update(f"blob {before.st_size}\0".encode("ascii"))
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(handle.fileno())
    if (
        not stat.S_ISREG(after.st_mode)
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or (before.st_ino and after.st_ino != before.st_ino)
        or (
            attest_posix_mode
            and expected_executable is not None
            and bool(after.st_mode & stat.S_IXUSR) != expected_executable
        )
    ):
        raise CalibrationError(f"tracked path changed while hashing: {path}")
    return digest.hexdigest()


def verify_source_worktree_bytes(root, commit):
    root = require_git_toplevel(root)
    require_text(commit, "tracked source commit", GIT_OBJECT_RE)
    environment = isolated_git_environment()
    git = git_executable()
    try:
        listing = subprocess.run(
            [str(git), "ls-tree", "-rz", "--full-tree", commit],
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise CalibrationError(f"cannot enumerate tracked source: {error}") from error
    if listing.returncode:
        raise CalibrationError("cannot enumerate committed source blobs")
    object_format = run_command(
        [str(git), "rev-parse", "--show-object-format"],
        root,
        "Git object format lookup",
        environment=environment,
    ).stdout.strip()
    if object_format not in {"sha1", "sha256"}:
        raise CalibrationError("Git object format is unsupported")
    count = 0
    portable_prefixes = {}
    for entry in listing.stdout.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_name = entry.split(b"\t", 1)
            mode, kind, expected = metadata.split(b" ", 2)
        except ValueError as error:
            raise CalibrationError("committed source inventory is malformed") from error
        components = raw_name.split(b"/")
        decoded_components = [os.fsdecode(part) for part in components]
        if (
            kind != b"blob"
            or mode not in {b"100644", b"100755"}
            or b"\\" in raw_name
            or b":" in raw_name
            or any(
                part in {b"", b".", b".."} or part.lower() == b".git"
                for part in components
            )
            or any(
                part.endswith((" ", "."))
                or re.fullmatch(
                    r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?",
                    part,
                )
                is not None
                for part in decoded_components
            )
        ):
            raise CalibrationError("committed source contains an unsafe entry")
        normalized_prefix = []
        decoded_prefix = []
        for component in decoded_components:
            normalized_prefix.append(
                unicodedata.normalize("NFC", component).casefold()
            )
            decoded_prefix.append(component)
            key = tuple(normalized_prefix)
            spelling = "/".join(decoded_prefix)
            previous = portable_prefixes.setdefault(key, spelling)
            if previous != spelling:
                raise CalibrationError(
                    "committed source contains a Windows-equivalent "
                    "path collision"
                )
        path = root.joinpath(*decoded_components)
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise CalibrationError("tracked source path escapes the worktree") from error
        cursor = root
        for component in components[:-1]:
            cursor /= os.fsdecode(component)
            try:
                info = cursor.lstat()
            except OSError as error:
                raise CalibrationError(
                    f"tracked source parent is unavailable: {cursor}"
                ) from error
            if path_is_link(cursor, info) or not stat.S_ISDIR(info.st_mode):
                raise CalibrationError("tracked source parent is unsafe")
        if git_blob_hash(
            path,
            object_format,
            expected_executable=mode == b"100755",
        ).encode("ascii") != expected:
            raise CalibrationError(
                "tracked worktree bytes differ from commit blob: "
                + os.fsdecode(raw_name)
            )
        count += 1
    if count == 0:
        raise CalibrationError("committed source inventory is empty")
    return count


def verify_no_untracked_worktree_entries(root, allow_generated=False):
    root = require_git_toplevel(root)
    environment = isolated_git_environment()
    git = git_executable()
    try:
        result = subprocess.run(
            [str(git), "ls-tree", "-rz", "--name-only", "HEAD"],
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise CalibrationError(
            f"cannot enumerate committed source entries: {error}"
        ) from error
    if result.returncode:
        raise CalibrationError("cannot enumerate committed source entries")
    tracked_files = set()
    tracked_directories = set()
    for raw_entry in result.stdout.split(b"\0"):
        if not raw_entry:
            continue
        entry = os.fsdecode(raw_entry)
        parts = entry.split("/")
        if (
            "\\" in entry
            or entry.startswith("/")
            or ":" in entry
            or PurePosixPath(entry).as_posix() != entry
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise CalibrationError("committed source entry path is unsafe")
        tracked_files.add(entry)
        for depth in range(1, len(parts)):
            tracked_directories.add("/".join(parts[:depth]))

    def generated(relative, directory):
        if directory:
            return any(
                relative == prefix.removesuffix("/")
                or relative.startswith(prefix)
                for prefix in CALIBRATION_GENERATED_PREFIXES
            ) or any(
                output.startswith(relative + "/")
                for output in CALIBRATION_GENERATED_FILES
            )
        return relative in CALIBRATION_GENERATED_FILES or relative.startswith(
            CALIBRATION_GENERATED_PREFIXES
        )

    git_metadata = root / ".git"
    try:
        git_info = git_metadata.lstat()
    except OSError as error:
        raise CalibrationError("calibration worktree lacks Git metadata") from error
    if path_is_link(git_metadata, git_info) or not (
        stat.S_ISDIR(git_info.st_mode) or stat.S_ISREG(git_info.st_mode)
    ):
        raise CalibrationError("calibration Git metadata path is unsafe")

    extra_files = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise CalibrationError(
                f"cannot enumerate calibration worktree: {error}"
            ) from error
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            if relative == ".git":
                continue
            try:
                info = path.lstat()
            except OSError as error:
                raise CalibrationError(
                    f"cannot inspect calibration worktree entry: {path}"
                ) from error
            if path_is_link(path, info):
                raise CalibrationError(
                    "calibration worktree contains a link or junction"
                )
            if stat.S_ISDIR(info.st_mode):
                if relative not in tracked_directories and not (
                    allow_generated and generated(relative, True)
                ):
                    raise CalibrationError(
                        "calibration worktree contains untracked or ignored entries"
                    )
                pending.append(path)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise CalibrationError(
                    "calibration worktree contains a special entry"
                )
            if relative in tracked_files:
                continue
            if allow_generated and generated(relative, False):
                extra_files += 1
                continue
            raise CalibrationError(
                "calibration worktree contains untracked or ignored entries"
            )
    return extra_files


def load_budget_contract(root):
    checker_path = Path(root) / "scripts" / "check-kernel-budgets.py"
    spec = importlib.util.spec_from_file_location(
        "agentos_kernel_budget_for_calibration", checker_path
    )
    if spec is None or spec.loader is None:
        raise CalibrationError("cannot load kernel budget checker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = module.load_config(Path(root) / "ci" / "kernel-budgets.json")
    tests = config["agent_test_suite"]
    if tests["calibration_status"] != module.AGENT_TEST_CALIBRATION_PROVISIONAL:
        raise CalibrationError("collection requires a provisional duration policy")
    fingerprint, paths = module.agent_test_source_fingerprint(root, config)
    return module, config, fingerprint, len(paths)


def expected_attestation_cases(expected_cases):
    return (
        ("00-context-sync-atomicity", PRELUDE_KEY, "agentfinal_ucore"),
        *(
            (f"{index:02d}-{case}", f"agent-case:{case}", case)
            for index, case in enumerate(expected_cases, start=1)
        ),
    )


def option_value(argv, option, label):
    positions = [index for index, value in enumerate(argv) if value == option]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise CalibrationError(f"{label} invocation lacks unique {option}")
    return argv[positions[0] + 1]


def optional_option_value(argv, option, default, label):
    positions = [index for index, value in enumerate(argv) if value == option]
    if not positions:
        return default
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise CalibrationError(f"{label} invocation has invalid {option}")
    return argv[positions[0] + 1]


def repeated_option_values(argv, option, label):
    values = []
    for index, value in enumerate(argv):
        if value != option:
            continue
        if index + 1 >= len(argv):
            raise CalibrationError(f"{label} invocation has invalid {option}")
        values.append(argv[index + 1])
    return values


def duration_seconds(value, label):
    require_text(value, label)
    unit = value[-1:]
    number = value[:-1] if unit.isalpha() else value
    multipliers = {"s": 1, "S": 1, "m": 60, "M": 60, "h": 3600, "H": 3600}
    if unit.isalpha() and unit not in multipliers:
        raise CalibrationError(f"{label} has an invalid unit")
    try:
        result = float(number) * multipliers.get(unit, 1)
    except ValueError as error:
        raise CalibrationError(f"{label} is invalid") from error
    if not math.isfinite(result) or result < 0:
        raise CalibrationError(f"{label} must be finite and non-negative")
    return result


def parse_guest_sections(path, expected_cases):
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise CalibrationError(f"cannot read aggregate Guest log: {error}") from error
    sections = {}
    cursor = 0
    for _, tag, _ in expected_attestation_cases(expected_cases):
        header = f"===== guest:{tag} =====\n".encode("ascii")
        footer = f"\n===== end-guest:{tag} =====\n".encode("ascii")
        if not data.startswith(header, cursor):
            raise CalibrationError("aggregate Guest section order mismatch")
        start = cursor + len(header)
        end = data.find(footer, start)
        if end < 0:
            raise CalibrationError("aggregate Guest section is incomplete")
        sections[tag] = data[start:end]
        cursor = end + len(footer)
    if cursor != len(data):
        raise CalibrationError("aggregate Guest log contains extra sections")
    return sections


def validate_identity_descriptor(value, label):
    value = exact_object(value, {"path", "bytes", "sha256"}, label)
    require_text(value["path"], f"{label}.path")
    require_int(value["bytes"], f"{label}.bytes", positive=True)
    require_text(value["sha256"], f"{label}.sha256", HEX64_RE)
    return value


def canonical_repo_relative(value, label):
    require_text(value, label)
    path = PurePosixPath(value)
    parts = value.split("/")
    if (
        "\\" in value
        or ":" in value
        or value.startswith("/")
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise CalibrationError(f"{label} is not a canonical repo-relative path")
    return path


def validate_repo_file_descriptor(
    value,
    *,
    root,
    relative,
    label,
    recorded_root=None,
    compare_current=False,
):
    value = validate_identity_descriptor(value, label)
    relative_path = canonical_repo_relative(relative, f"{label} relative path")
    recorded = value["path"]
    recorded_path = PurePosixPath(recorded)
    if (
        "\\" in recorded
        or ":" in recorded
        or not recorded.startswith("/")
        or not recorded_path.is_absolute()
        or any(part in ("", ".", "..") for part in recorded.split("/")[1:])
        or ".." in recorded_path.parts
        or "." in recorded_path.parts
    ):
        raise CalibrationError(f"{label} recorded path is not canonical")
    suffix = "/" + relative_path.as_posix()
    if recorded_root is None:
        if not recorded.endswith(suffix):
            raise CalibrationError(f"{label} repo-relative suffix mismatch")
        recorded_root = recorded[: -len(suffix)]
        if not recorded_root or not recorded_root.startswith("/"):
            raise CalibrationError(f"{label} recorded root is invalid")
    elif recorded != f"{recorded_root}{suffix}":
        raise CalibrationError(f"{label} recorded source root mismatch")
    if compare_current:
        _, current = regular_file(Path(root) / relative_path, label)
        if (
            value["bytes"] != current["bytes"]
            or value["sha256"] != current["sha256"]
        ):
            raise CalibrationError(f"{label} content identity mismatch")
    return value, recorded_root


def validate_executable_identity(value, expected, label):
    value = exact_object(
        value,
        {"requested_path", "executable", "version_argv", "version_first_line"},
        label,
    )
    require_text(value["requested_path"], f"{label}.requested_path")
    validate_identity_descriptor(value["executable"], f"{label}.executable")
    if (
        not isinstance(value["version_argv"], list)
        or not value["version_argv"]
        or any(not isinstance(item, str) or not item for item in value["version_argv"])
    ):
        raise CalibrationError(f"{label}.version_argv is invalid")
    require_text(value["version_first_line"], f"{label}.version_first_line")
    if value != expected:
        raise CalibrationError(f"{label} differs from the predeclared tool")


def validate_attestation(
    value,
    *,
    root,
    plan,
    plan_sha256,
    round_record,
    case_key,
    tag,
    init_proc,
    guest_bytes,
    attestation_path,
    production,
):
    value = exact_object(
        value,
        {
            "schema_version",
            "format",
            "evidence_scope",
            "source",
            "identity",
            "runner",
            "executables",
            "invocation_argv",
            "qemu_argv",
            "request",
            "inputs",
            "outputs",
            "time",
            "result",
            "run_id",
            "execution_id",
        },
        f"attestation {case_key}",
    )
    if (
        value["schema_version"] != 2
        or value["format"] != ATTESTATION_FORMAT
        or value["evidence_scope"] != EVIDENCE_SCOPE
    ):
        raise CalibrationError(f"attestation {case_key} scope/schema mismatch")
    source = exact_object(
        value["source"],
        {"commit", "tree", "calibration_plan_sha256"},
        f"attestation {case_key}.source",
    )
    if source != {
        "commit": plan["source"]["commit"],
        "tree": plan["source"]["tree"],
        "calibration_plan_sha256": plan_sha256,
    }:
        raise CalibrationError(f"attestation {case_key} source mismatch")
    identity = exact_object(
        value["identity"],
        {"campaign_nonce", "round_nonce", "session_nonce", "execution_nonce"},
        f"attestation {case_key}.identity",
    )
    for name, nonce in identity.items():
        require_text(nonce, f"attestation {case_key}.{name}", HEX64_RE)
    if (
        identity["campaign_nonce"] != plan["campaign_nonce"]
        or identity["round_nonce"] != round_record["round_nonce"]
        or value["run_id"] != identity["session_nonce"]
        or value["execution_id"] != identity["execution_nonce"]
        or len(set(identity.values())) != 4
    ):
        raise CalibrationError(f"attestation {case_key} nonce binding mismatch")
    _, recorded_source_root = validate_repo_file_descriptor(
        value["runner"],
        root=root,
        relative="scripts/agent_test_runner.py",
        label=f"attestation {case_key}.runner",
        compare_current=True,
    )
    if production and recorded_source_root != Path(root).resolve().as_posix():
        raise CalibrationError(
            f"attestation {case_key} recorded source root mismatch"
        )
    executables = exact_object(
        value["executables"],
        {"qemu", "toolchain_cc", "python"},
        f"attestation {case_key}.executables",
    )
    for name in executables:
        validate_executable_identity(
            executables[name],
            plan["executables"][name],
            f"attestation {case_key}.executables.{name}",
        )

    argv = value["invocation_argv"]
    qemu_argv = value["qemu_argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(item, str) or not item for item in argv)
        or not isinstance(qemu_argv, list)
        or any(not isinstance(item, str) or not item for item in qemu_argv)
    ):
        raise CalibrationError(f"attestation {case_key} argv is invalid")
    expected_options = {
        "--init-proc": init_proc,
        "--marker": f"{init_proc}: parent passed",
        "--run-id": identity["session_nonce"],
        "--execution-id": identity["execution_nonce"],
        "--evidence-scope": EVIDENCE_SCOPE,
        "--source-commit": source["commit"],
        "--source-tree": source["tree"],
        "--campaign-nonce": identity["campaign_nonce"],
        "--calibration-plan-sha256": plan_sha256,
        "--round-nonce": identity["round_nonce"],
        "--session-nonce": identity["session_nonce"],
        "--execution-nonce": identity["execution_nonce"],
        "--toolchain-cc": plan["executables"]["toolchain_cc"]["requested_path"],
    }
    for option, expected in expected_options.items():
        if option_value(argv, option, f"attestation {case_key}") != expected:
            raise CalibrationError(
                f"attestation {case_key} invocation {option} mismatch"
            )
    declared_attestation = option_value(
        argv, "--attestation-file", f"attestation {case_key}"
    )
    expected_attestation_suffix = (
        "attestations",
        f"{round_record['round']:02d}",
        f"{case_key}.json",
    )
    declared_attestation_path = Path(declared_attestation)
    actual_attestation_path = Path(attestation_path)
    declared_suffix = "/" + "/".join(expected_attestation_suffix)
    declared_stage_root = (
        declared_attestation[: -len(declared_suffix)]
        if declared_attestation.endswith(declared_suffix)
        else ""
    )
    if (
        argv[0] != plan["executables"]["python"]["executable"]["path"]
        or len(argv) < 2
        or argv[1] != "scripts/agent_test_runner.py"
        or "\\" in declared_attestation
        or ":" in declared_attestation
        or not declared_attestation_path.is_absolute()
        or any(
            part in ("", ".", "..")
            for part in declared_attestation.split("/")[1:]
        )
        or ".." in declared_attestation_path.parts
        or not declared_stage_root.startswith("/")
        or tuple(declared_attestation_path.parts[-3:])
        != expected_attestation_suffix
        or tuple(actual_attestation_path.parts[-3:])
        != expected_attestation_suffix
        or option_value(argv, "--qemu", f"attestation {case_key}")
        != plan["executables"]["qemu"]["requested_path"]
    ):
        raise CalibrationError(f"attestation {case_key} invocation path mismatch")
    if production and os.path.abspath(declared_attestation) != os.path.abspath(
        attestation_path
    ):
        raise CalibrationError(
            f"attestation {case_key} declared attestation path mismatch"
        )
    if "--timing-file" in argv:
        raise CalibrationError("calibration runner must not publish timing text")

    request = exact_object(
        value["request"],
        {
            "init_proc",
            "marker",
            "marker_mode",
            "expected_bad_addr_markers",
            "expected_fault_marker_mode",
            "completion_mode",
            "case_timeout_seconds",
            "idle_notice_seconds",
            "marker_grace_seconds",
        },
        f"attestation {case_key}.request",
    )
    expected_fault_markers = (
        ["iobudget_ucore: fault_exit_armed=1"]
        if init_proc == "iobudget_ucore"
        else []
    )
    invocation_fault_markers = repeated_option_values(
        argv, "--expected-bad-addr-after", f"attestation {case_key}"
    )
    marker_mode = option_value(argv, "--marker-mode", f"attestation {case_key}")
    fault_marker_mode = option_value(
        argv, "--expected-bad-addr-marker-mode", f"attestation {case_key}"
    )
    completion_mode = optional_option_value(
        argv, "--completion-mode", "natural", f"attestation {case_key}"
    )
    case_timeout = duration_seconds(
        option_value(argv, "--case-timeout", f"attestation {case_key}"),
        f"attestation {case_key} case timeout",
    )
    idle_notice = duration_seconds(
        option_value(argv, "--idle-notice-seconds", f"attestation {case_key}"),
        f"attestation {case_key} idle notice",
    )
    marker_grace = duration_seconds(
        option_value(argv, "--marker-grace-seconds", f"attestation {case_key}"),
        f"attestation {case_key} marker grace",
    )
    if (
        request["init_proc"] != init_proc
        or request["marker"] != f"{init_proc}: parent passed"
        or request["marker_mode"] != marker_mode
        or marker_mode != "exact-line"
        or request["expected_bad_addr_markers"] != expected_fault_markers
        or invocation_fault_markers != expected_fault_markers
        or request["expected_fault_marker_mode"] != fault_marker_mode
        or fault_marker_mode != "exact-line"
        or request["completion_mode"] != completion_mode
        or completion_mode != "natural"
        or request["case_timeout_seconds"] != case_timeout
        or request["idle_notice_seconds"] != idle_notice
        or request["marker_grace_seconds"] != marker_grace
    ):
        raise CalibrationError(f"attestation {case_key} request mismatch")
    inputs = exact_object(
        value["inputs"], {"kernel", "image"}, f"attestation {case_key}.inputs"
    )
    outputs = exact_object(
        value["outputs"],
        {"kernel", "image", "log"},
        f"attestation {case_key}.outputs",
    )
    kernel_argument = optional_option_value(
        argv, "--kernel", "build/kernel", f"attestation {case_key}"
    )
    image_argument = optional_option_value(
        argv, "--image", "nfs/fs-copy.img", f"attestation {case_key}"
    )
    canonical_repo_relative(
        kernel_argument, f"attestation {case_key} kernel argument"
    )
    canonical_repo_relative(
        image_argument, f"attestation {case_key} image argument"
    )
    for name, relative in (("kernel", kernel_argument), ("image", image_argument)):
        validate_repo_file_descriptor(
            inputs[name],
            root=root,
            relative=relative,
            label=f"attestation {case_key}.{name} input",
            recorded_root=recorded_source_root,
        )
        validate_repo_file_descriptor(
            outputs[name],
            root=root,
            relative=relative,
            label=f"attestation {case_key}.{name} output",
            recorded_root=recorded_source_root,
        )
    if inputs["kernel"] != outputs["kernel"]:
        raise CalibrationError(f"attestation {case_key} kernel changed")
    if (
        inputs["image"]["path"] != outputs["image"]["path"]
        or inputs["image"]["bytes"] != outputs["image"]["bytes"]
    ):
        raise CalibrationError(f"attestation {case_key} image identity changed")
    log_identity = validate_identity_descriptor(
        outputs["log"], f"attestation {case_key}.log"
    )
    if (
        log_identity["bytes"] != len(guest_bytes)
        or log_identity["sha256"] != sha256_bytes(guest_bytes)
    ):
        raise CalibrationError(f"attestation {case_key} Guest log mismatch")
    if option_value(
        argv, "--log-file", f"attestation {case_key}"
    ) != log_identity["path"]:
        raise CalibrationError(f"attestation {case_key} log path mismatch")
    expected_qemu_argv = [
        executables["qemu"]["requested_path"],
        "-nographic",
        "-machine",
        "virt",
        "-bios",
        "default",
        "-kernel",
        kernel_argument,
        "-drive",
        f"file={image_argument},if=none,format=raw,id=x0",
        "-device",
        "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
    ]
    if (
        qemu_argv != expected_qemu_argv
    ):
        raise CalibrationError(f"attestation {case_key} QEMU argv mismatch")

    timing = exact_object(
        value["time"],
        {
            "clock",
            "started_monotonic_ns",
            "finished_monotonic_ns",
            "elapsed_ns",
            "started_wall_time_ns",
            "finished_wall_time_ns",
        },
        f"attestation {case_key}.time",
    )
    started = require_int(
        timing["started_monotonic_ns"], f"attestation {case_key}.start", True
    )
    finished = require_int(
        timing["finished_monotonic_ns"], f"attestation {case_key}.finish", True
    )
    elapsed_ns = require_int(
        timing["elapsed_ns"], f"attestation {case_key}.elapsed", True
    )
    wall_start = require_int(
        timing["started_wall_time_ns"], f"attestation {case_key}.wall start", True
    )
    wall_finish = require_int(
        timing["finished_wall_time_ns"], f"attestation {case_key}.wall finish", True
    )
    if (
        timing["clock"] != "time.monotonic_ns"
        or finished <= started
        or elapsed_ns != finished - started
        or wall_finish <= wall_start
    ):
        raise CalibrationError(f"attestation {case_key} time relation mismatch")
    result = exact_object(
        value["result"],
        {
            "succeeded",
            "reason",
            "returncode",
            "supervisor_returncode",
            "signals_sent",
            "output_eof",
            "expected_faults_satisfied",
            "process_tree_gone",
            "process_tree_contained",
            "completion_signal_attested",
            "control_endpoint_restored",
            "supervisor_control_healthy",
            "elapsed_seconds",
        },
        f"attestation {case_key}.result",
    )
    expected_elapsed = round(elapsed_ns / 1_000_000_000, 9)
    if (
        result["succeeded"] is not True
        or result["reason"] != "process_exit"
        or result["returncode"] != 0
        or result["signals_sent"] != []
        or result["output_eof"] is not True
        or result["expected_faults_satisfied"] is not True
        or result["process_tree_gone"] is not True
        or not isinstance(result["process_tree_contained"], bool)
        or result["completion_signal_attested"] is not False
        or result["supervisor_returncode"] is not None
        or result["control_endpoint_restored"] is not True
        or result["supervisor_control_healthy"] is not True
        or not isinstance(result["elapsed_seconds"], (int, float))
        or isinstance(result["elapsed_seconds"], bool)
        or not math.isclose(
            result["elapsed_seconds"], expected_elapsed, rel_tol=0, abs_tol=5e-10
        )
    ):
        raise CalibrationError(f"attestation {case_key} result mismatch")
    return {
        "case_key": case_key,
        "tag": tag,
        "init_proc": init_proc,
        "session_nonce": identity["session_nonce"],
        "execution_nonce": identity["execution_nonce"],
        "started_monotonic_ns": started,
        "finished_monotonic_ns": finished,
        "started_wall_time_ns": wall_start,
        "finished_wall_time_ns": wall_finish,
        "elapsed_seconds": expected_elapsed,
        "recorded_source_root": recorded_source_root,
        "recorded_stage_root": declared_stage_root,
    }


def validate_plan_structure(plan):
    plan = exact_object(
        plan,
        {
            "schema",
            "purpose",
            "evidence_scope",
            "remote_ci_attestation",
            "source",
            "campaign_nonce",
            "rounds",
            "expected_cases",
            "attestation_case_count",
            "executables",
            "created_utc",
        },
        "calibration plan",
    )
    if (
        plan["schema"] != 1
        or plan["purpose"] != CAMPAIGN_PURPOSE
        or plan["evidence_scope"] != EVIDENCE_SCOPE
        or plan["remote_ci_attestation"] is not False
    ):
        raise CalibrationError("calibration plan scope/contract mismatch")
    source = exact_object(
        plan["source"],
        {"commit", "tree", "fingerprint_sha256", "fingerprint_inputs"},
        "calibration plan source",
    )
    require_text(source["commit"], "calibration plan commit", GIT_OBJECT_RE)
    require_text(source["tree"], "calibration plan tree", GIT_OBJECT_RE)
    require_text(
        source["fingerprint_sha256"], "calibration plan fingerprint", HEX64_RE
    )
    require_int(source["fingerprint_inputs"], "calibration plan inputs", True)
    campaign_nonce = require_text(
        plan["campaign_nonce"], "calibration campaign nonce", HEX64_RE
    )
    if (
        not isinstance(plan["expected_cases"], list)
        or not plan["expected_cases"]
        or any(
            not isinstance(case, str) or not re.fullmatch(r"[A-Za-z0-9_]+", case)
            for case in plan["expected_cases"]
        )
        or len(set(plan["expected_cases"])) != len(plan["expected_cases"])
        or plan["attestation_case_count"] != len(plan["expected_cases"]) + 1
    ):
        raise CalibrationError("calibration expected cases are invalid")
    if not isinstance(plan["rounds"], list) or len(plan["rounds"]) != ROUND_COUNT:
        raise CalibrationError("calibration plan must contain exactly three rounds")
    nonces = {campaign_nonce}
    for index, record in enumerate(plan["rounds"], start=1):
        record = exact_object(
            record, {"round", "round_nonce"}, f"calibration round plan {index}"
        )
        nonce = require_text(
            record["round_nonce"], f"calibration round {index} nonce", HEX64_RE
        )
        if record["round"] != index or nonce in nonces:
            raise CalibrationError("calibration round identities are invalid")
        nonces.add(nonce)
    if (
        not isinstance(plan["executables"], dict)
        or set(plan["executables"]) != set(CALIBRATION_TOOL_NAMES)
    ):
        raise CalibrationError("calibration executable inventory mismatch")
    for name, identity in plan["executables"].items():
        validate_executable_identity(
            identity, identity, f"calibration executable {name}"
        )
    require_text(plan["created_utc"], "calibration creation time", UTC_RE)
    return source


def validate_round_attestations(
    root,
    plan_path,
    round_number,
    attestation_dir,
    guest_log,
    production=True,
):
    plan_path = Path(plan_path)
    plan_data = plan_path.read_bytes()
    plan_sha256 = sha256_bytes(plan_data)
    plan = read_strict_json(plan_path, "calibration plan")
    plan = exact_object(
        plan,
        {
            "schema",
            "purpose",
            "evidence_scope",
            "remote_ci_attestation",
            "source",
            "campaign_nonce",
            "rounds",
            "expected_cases",
            "attestation_case_count",
            "executables",
            "created_utc",
        },
        "calibration plan",
    )
    if (
        plan["schema"] != 1
        or plan["purpose"] != CAMPAIGN_PURPOSE
        or plan["evidence_scope"] != EVIDENCE_SCOPE
        or plan["remote_ci_attestation"] is not False
        or not isinstance(plan["expected_cases"], list)
        or plan["attestation_case_count"] != len(plan["expected_cases"]) + 1
        or len(plan["rounds"]) != ROUND_COUNT
    ):
        raise CalibrationError("calibration plan contract mismatch")
    source = validate_plan_structure(plan)
    if production:
        source_root, actual_tree = validate_source_checkout(
            root, source["commit"]
        )
        verify_no_untracked_worktree_entries(
            source_root, allow_generated=True
        )
        verify_source_worktree_bytes(source_root, source["commit"])
        _, config, fingerprint, source_inputs = load_budget_contract(source_root)
        profile = config["agent_test_suite"]["local_calibration_profile"]
        tools = capture_environment_tools(profile)
        host = capture_host_identity()
        validate_live_calibration_profile(profile, tools, host)
        if (
            actual_tree != source["tree"]
            or fingerprint != source["fingerprint_sha256"]
            or source_inputs != source["fingerprint_inputs"]
            or config["agent_test_suite"]["expected_cases"]
            != plan["expected_cases"]
            or tools != plan["executables"]
        ):
            raise CalibrationError("calibration plan differs from the live checkout")
    if round_number not in (1, 2, 3):
        raise CalibrationError("calibration round must be 1, 2, or 3")
    round_record = plan["rounds"][round_number - 1]
    if round_record.get("round") != round_number:
        raise CalibrationError("calibration round plan mismatch")
    guest_sections = parse_guest_sections(guest_log, plan["expected_cases"])
    expected = expected_attestation_cases(plan["expected_cases"])
    attestation_dir = Path(attestation_dir)
    actual_files = {
        path.name for path in attestation_dir.iterdir() if path.is_file()
    }
    expected_files = {f"{case_key}.json" for case_key, _, _ in expected}
    if actual_files != expected_files:
        raise CalibrationError("calibration attestation inventory mismatch")
    records = []
    attestation_hashes = []
    previous_finish = None
    previous_wall_finish = None
    recorded_source_root = None
    recorded_stage_root = None
    nonces = {
        plan["campaign_nonce"],
        *(item["round_nonce"] for item in plan["rounds"]),
    }
    for case_key, tag, init_proc in expected:
        path = attestation_dir / f"{case_key}.json"
        if path.is_symlink() or not path.is_file():
            raise CalibrationError("calibration attestation must be a regular file")
        data = path.read_bytes()
        value = strict_json_bytes(data, f"attestation {case_key}")
        record = validate_attestation(
            value,
            root=root,
            plan=plan,
            plan_sha256=plan_sha256,
            round_record=round_record,
            case_key=case_key,
            tag=tag,
            init_proc=init_proc,
            guest_bytes=guest_sections[tag],
            attestation_path=path,
            production=production,
        )
        if recorded_source_root is None:
            recorded_source_root = record["recorded_source_root"]
            recorded_stage_root = record["recorded_stage_root"]
        elif (
            record["recorded_source_root"] != recorded_source_root
            or record["recorded_stage_root"] != recorded_stage_root
        ):
            raise CalibrationError(
                "calibration attestations do not share historical roots"
            )
        if (
            record["session_nonce"] in nonces
            or record["execution_nonce"] in nonces
            or record["session_nonce"] == record["execution_nonce"]
        ):
            raise CalibrationError("calibration nonces must all be distinct")
        nonces.update((record["session_nonce"], record["execution_nonce"]))
        if previous_finish is not None and record["started_monotonic_ns"] < previous_finish:
            raise CalibrationError("calibration cases overlap or run out of order")
        if (
            previous_wall_finish is not None
            and record["started_wall_time_ns"] < previous_wall_finish
        ):
            raise CalibrationError("calibration wall-clock cases overlap")
        previous_finish = record["finished_monotonic_ns"]
        previous_wall_finish = record["finished_wall_time_ns"]
        digest = sha256_bytes(data)
        if digest in attestation_hashes:
            raise CalibrationError("calibration attestation hashes must be distinct")
        attestation_hashes.append(digest)
        record["path"] = path
        record["sha256"] = digest
        record["bytes"] = len(data)
        records.append(record)
    round_digest = sha256_bytes("\n".join(attestation_hashes).encode("ascii"))
    return plan, plan_sha256, records, round_digest


def derive_round_timing(
    root,
    plan_path,
    round_number,
    attestation_dir,
    guest_log,
    timing_file,
    production=True,
):
    plan, _, records, _ = validate_round_attestations(
        root,
        plan_path,
        round_number,
        attestation_dir,
        guest_log,
        production=production,
    )
    case_records = records[1:]
    if [record["init_proc"] for record in case_records] != plan["expected_cases"]:
        raise CalibrationError("calibration case order mismatch")
    payload = "".join(
        f"{record['init_proc']} {record['elapsed_seconds']:.9f}\n"
        for record in case_records
    ).encode("ascii")
    timing_file = Path(timing_file)
    timing_file.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(timing_file, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as error:
        raise CalibrationError("derived timing output already exists") from error
    return sum(record["elapsed_seconds"] for record in case_records)


def write_json_exclusive(path, value):
    path = Path(path)
    data = canonical_json_bytes(value)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    return data


def file_descriptor(path, base, raw=None):
    path = Path(path)
    digest, size = sha256_file(path)
    descriptor = {
        "path": path.relative_to(base).as_posix(),
        "bytes": size,
        "sha256": digest,
    }
    if raw is not None:
        descriptor.update(
            {
                "raw_bytes": len(raw),
                "raw_sha256": sha256_bytes(raw),
            }
        )
    return descriptor


def resolve_package_member(package, relative, label):
    try:
        canonical_repo_relative(relative, f"{label} path")
    except CalibrationError as error:
        raise CalibrationError(f"{label} path is not canonical") from error
    cursor = package
    for part in relative.split("/"):
        cursor /= part
        if path_is_link(cursor):
            raise CalibrationError(f"{label} traverses a symlink")
    try:
        resolved = cursor.resolve(strict=True)
        resolved.relative_to(package)
    except (OSError, ValueError) as error:
        raise CalibrationError(f"{label} escapes the package") from error
    if not resolved.is_file():
        raise CalibrationError(f"{label} is not a regular file")
    return resolved


def verify_file_descriptor(package, value, label, compressed=False):
    fields = {"path", "bytes", "sha256"}
    if compressed:
        fields.update(("raw_bytes", "raw_sha256"))
    value = exact_object(value, fields, label)
    path = resolve_package_member(package, value["path"], label)
    data = path.read_bytes()
    if (
        require_int(value["bytes"], f"{label}.bytes", True) != len(data)
        or require_text(value["sha256"], f"{label}.sha256", HEX64_RE)
        != sha256_bytes(data)
    ):
        raise CalibrationError(f"{label} descriptor mismatch")
    if not compressed:
        return path, data
    if (
        len(data) < 10
        or data[:2] != b"\x1f\x8b"
        or data[3] != 0
        or data[4:8] != b"\0\0\0\0"
    ):
        raise CalibrationError(f"{label} is not deterministic gzip")
    try:
        raw = gzip.decompress(data)
    except (OSError, EOFError, zlib.error) as error:
        raise CalibrationError(f"{label} is invalid gzip") from error
    if (
        require_int(value["raw_bytes"], f"{label}.raw_bytes", True) != len(raw)
        or require_text(value["raw_sha256"], f"{label}.raw_sha256", HEX64_RE)
        != sha256_bytes(raw)
    ):
        raise CalibrationError(f"{label} raw descriptor mismatch")
    return path, raw


def package_file_inventory(package):
    package = reject_link_components(package, "calibration package")
    files = set()
    pending = [package]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = directory / entry.name
                info = entry.stat(follow_symlinks=False)
                if path_is_link(path, info):
                    raise CalibrationError(
                        "calibration package contains a link or junction"
                    )
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    files.add(path.relative_to(package).as_posix())
                else:
                    raise CalibrationError(
                        "calibration package contains a special file"
                    )
    return files


def capture_environment_tools(profile):
    profile = validate_local_calibration_profile_structure(profile)
    qemu = os.environ.get("QEMU")
    toolprefix = os.environ.get("TOOLPREFIX")
    if not qemu or toolprefix is None:
        raise CalibrationError("QEMU and TOOLPREFIX must be explicit")
    tools = {
        "qemu": executable_identity(qemu, "QEMU"),
        "toolchain_cc": executable_identity(
            f"{toolprefix}gcc", "toolchain compiler"
        ),
        "toolchain_ld": executable_identity(
            f"{toolprefix}ld", "toolchain linker"
        ),
        "toolchain_objcopy": executable_identity(
            f"{toolprefix}objcopy", "toolchain objcopy"
        ),
        "toolchain_objdump": executable_identity(
            f"{toolprefix}objdump", "toolchain objdump"
        ),
        "toolchain_as": executable_identity(
            f"{toolprefix}as", "toolchain assembler"
        ),
        "host_cc": executable_identity(
            os.environ.get("HOST_CC", os.environ.get("HOSTCC", "cc")),
            "host C compiler",
        ),
        "python": executable_identity(sys.executable, "Python"),
        "bash": executable_identity(
            os.environ.get("BASH_BIN", "bash"), "Bash"
        ),
        "make": executable_identity(
            os.environ.get("MAKE_TOOL", "make"), "Make"
        ),
        "git": executable_identity("git", "Git"),
    }
    for identity in tools.values():
        resolved = identity["executable"]["path"]
        identity["requested_path"] = resolved
        identity["version_argv"][0] = resolved
    return tools


def validate_runner_log(raw, expected_cases, fingerprint, source_inputs, round_number):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CalibrationError("calibration runner log is not UTF-8") from error
    lines = text.splitlines()
    final_marker = "[agent-tests] all Agent-OS uCore checks passed"
    if not lines or lines[-1] != final_marker or lines.count(final_marker) != 1:
        raise CalibrationError("calibration runner lacks a final unique pass marker")
    pass_sequence = re.findall(
        r"(?m)^\[agent-tests\] ([A-Za-z0-9_]+) passed\r?$", text
    )
    if pass_sequence != ["agentfinal_ucore", *expected_cases]:
        raise CalibrationError("calibration runner pass sequence mismatch")
    source_marker = (
        "[kernel-budget] Agent calibration source/contract: "
        f"sha256={fingerprint}, inputs={source_inputs}"
    )
    derive_prefix = (
        "[agent-calibration] attestation-derived timing: "
        f"round={round_number:02d} total="
    )
    source_check = re.compile(
        r"\[agent-calibration\] source-check: "
        r"tracked=[1-9][0-9]* untracked=0"
    )
    if (
        lines.count(source_marker) != 1
        or sum(source_check.fullmatch(line) is not None for line in lines) != 1
        or sum(
        line.startswith(derive_prefix) for line in lines
        )
        != 1
    ):
        raise CalibrationError("calibration runner source/timing receipt mismatch")


def build_plan(
    source_commit,
    source_tree,
    fingerprint,
    source_inputs,
    expected_cases,
    campaign_nonce,
    round_nonces,
    tools,
    created_utc,
):
    return {
        "schema": 1,
        "purpose": CAMPAIGN_PURPOSE,
        "evidence_scope": EVIDENCE_SCOPE,
        "remote_ci_attestation": False,
        "source": {
            "commit": source_commit,
            "tree": source_tree,
            "fingerprint_sha256": fingerprint,
            "fingerprint_inputs": source_inputs,
        },
        "campaign_nonce": campaign_nonce,
        "rounds": [
            {"round": index, "round_nonce": nonce}
            for index, nonce in enumerate(round_nonces, start=1)
        ],
        "expected_cases": list(expected_cases),
        "attestation_case_count": len(expected_cases) + 1,
        "executables": tools,
        "created_utc": created_utc,
    }


def ensure_output_location(root, output, campaign_nonce):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise CalibrationError("calibration output must not already exist")
    parent = reject_link_components(
        output.parent, "calibration output parent"
    ).resolve(strict=True)
    if path_is_link(parent) or not parent.is_dir():
        raise CalibrationError("calibration output parent must be a real directory")
    resolved_target = parent / output.name
    try:
        resolved_target.relative_to(root)
    except ValueError:
        pass
    else:
        raise CalibrationError("calibration output must be outside the source worktree")
    stage = parent / f".{output.name}.partial-{campaign_nonce[:16]}"
    if stage.exists() or stage.is_symlink():
        raise CalibrationError("calibration staging path already exists")
    stage.mkdir(mode=0o700)
    return resolved_target, stage


def collect_campaign(args):
    root, source_tree = validate_source_checkout(args.root, args.source_commit)
    verify_no_untracked_worktree_entries(root)
    tracked_source_inputs = verify_source_worktree_bytes(root, args.source_commit)
    budget, config, fingerprint, source_inputs = load_budget_contract(root)
    tests = config["agent_test_suite"]
    profile = tests["local_calibration_profile"]
    expected_cases = tuple(tests["expected_cases"])
    campaign_nonce = secrets.token_hex(32)
    round_nonces = []
    nonce_set = {campaign_nonce}
    while len(round_nonces) != ROUND_COUNT:
        nonce = secrets.token_hex(32)
        if nonce not in nonce_set:
            nonce_set.add(nonce)
            round_nonces.append(nonce)
    output, stage = ensure_output_location(root, args.output, campaign_nonce)
    config_output = output.parent / f"{output.name}.calibration-config.json"
    if config_output.exists() or config_output.is_symlink():
        stage.rmdir()
        raise CalibrationError("calibration config output must not already exist")
    campaign_started_utc = utc_now()
    campaign_started_monotonic_ns = time.monotonic_ns()
    campaign_started_wall_time_ns = time.time_ns()
    tools_before = capture_environment_tools(profile)
    host = capture_host_identity()
    profile_id = validate_live_calibration_profile(profile, tools_before, host)
    toolchain_prefix = canonical_toolchain_prefix(profile, tools_before)
    plan = build_plan(
        args.source_commit,
        source_tree,
        fingerprint,
        source_inputs,
        expected_cases,
        campaign_nonce,
        round_nonces,
        tools_before,
        campaign_started_utc,
    )
    plan_data = write_json_exclusive(stage / "plan.json", plan)
    plan_sha256 = sha256_bytes(plan_data)
    round_records = []
    all_attestation_hashes = set()
    all_case_nonces = set(nonce_set)
    recorded_source_roots = set()
    recorded_stage_roots = set()
    previous_round_finish = None
    for round_number, round_nonce in enumerate(round_nonces, start=1):
        round_text = f"{round_number:02d}"
        attestation_dir = stage / "attestations" / round_text
        attestation_dir.mkdir(parents=True)
        timing_file = stage / f"{round_text}.timing"
        guest_log = stage / f"{round_text}.guest.log"
        runner_log = stage / f"{round_text}.runner.log"
        round_started_monotonic_ns = time.monotonic_ns()
        round_started_wall_time_ns = time.time_ns()
        round_started_utc = utc_now()
        if (
            previous_round_finish is not None
            and round_started_monotonic_ns < previous_round_finish
        ):
            raise CalibrationError("calibration rounds are not serial")
        environment = calibration_child_environment()
        environment.update(
            {
                "REQUIRE_FULL_SUITE": "1",
                "AGENT_TEST_CALIBRATE": "1",
                "AGENT_TEST_TIMING_FILE": str(timing_file),
                "AGENT_TEST_GUEST_LOG_FILE": str(guest_log),
                "AGENT_TEST_CALIBRATION_PLAN": str(stage / "plan.json"),
                "AGENT_TEST_CALIBRATION_ROUND": round_text,
                "AGENT_TEST_CAMPAIGN_NONCE": campaign_nonce,
                "AGENT_TEST_ROUND_NONCE": round_nonce,
                "AGENT_TEST_SOURCE_COMMIT": args.source_commit,
                "AGENT_TEST_SOURCE_TREE": source_tree,
                "AGENT_TEST_ATTESTATION_DIR": str(attestation_dir),
                "CASE_TIMEOUT": args.case_timeout,
                "IDLE_NOTICE_SECONDS": "20",
                "MARKER_GRACE_SECONDS": "2s",
                "LOG": "error",
                "CHAPTER": "agent",
                "AGENT_TEST_DURATION_PROFILE": "local-e3",
                "QEMU": tools_before["qemu"]["executable"]["path"],
                "PYTHON_BIN": tools_before["python"]["executable"]["path"],
                "BASH_BIN": tools_before["bash"]["executable"]["path"],
                "MAKE_TOOL": tools_before["make"]["executable"]["path"],
                "GIT_BIN": tools_before["git"]["executable"]["path"],
                "HOST_CC": tools_before["host_cc"]["executable"]["path"],
                "HOSTCC": tools_before["host_cc"]["executable"]["path"],
                "TOOLPREFIX": toolchain_prefix,
                "PATH": locked_tool_path(tools_before),
            }
        )
        environment.pop("AGENT_TEST_CASE", None)
        with runner_log.open("xb") as output_stream:
            try:
                result = subprocess.run(
                    [tools_before["bash"]["executable"]["path"], "scripts/run-agent-tests.sh"],
                    cwd=root,
                    env=environment,
                    stdout=output_stream,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            except OSError as error:
                raise CalibrationError(
                    f"cannot execute calibration round {round_text}: {error}"
                ) from error
        round_finished_monotonic_ns = time.monotonic_ns()
        round_finished_wall_time_ns = time.time_ns()
        round_finished_utc = utc_now()
        previous_round_finish = round_finished_monotonic_ns
        if result.returncode != 0:
            raise CalibrationError(
                f"calibration round {round_text} failed with {result.returncode}"
            )
        _, current_tree = validate_source_checkout(root, args.source_commit)
        if current_tree != source_tree:
            raise CalibrationError("source tree changed during calibration")
        if verify_source_worktree_bytes(root, args.source_commit) != tracked_source_inputs:
            raise CalibrationError("tracked source inventory changed during calibration")
        _, observed_plan_sha, attestations, round_digest = (
            validate_round_attestations(
                root, stage / "plan.json", round_number, attestation_dir, guest_log
            )
        )
        recorded_source_roots.update(
            record["recorded_source_root"] for record in attestations
        )
        recorded_stage_roots.update(
            record["recorded_stage_root"] for record in attestations
        )
        if len(recorded_source_roots) != 1 or len(recorded_stage_roots) != 1:
            raise CalibrationError("calibration historical roots changed")
        if observed_plan_sha != plan_sha256:
            raise CalibrationError("calibration plan changed during collection")
        if (
            attestations[0]["started_monotonic_ns"] < round_started_monotonic_ns
            or attestations[-1]["finished_monotonic_ns"]
            > round_finished_monotonic_ns
            or attestations[0]["started_wall_time_ns"] < round_started_wall_time_ns
            or attestations[-1]["finished_wall_time_ns"]
            > round_finished_wall_time_ns
        ):
            raise CalibrationError("case timestamps escape their harness round")
        round_hashes = {record["sha256"] for record in attestations}
        round_case_nonces = {
            nonce
            for record in attestations
            for nonce in (record["session_nonce"], record["execution_nonce"])
        }
        if all_attestation_hashes.intersection(round_hashes):
            raise CalibrationError("attestation hashes repeat across rounds")
        if all_case_nonces.intersection(round_case_nonces):
            raise CalibrationError("case nonces repeat across rounds")
        all_attestation_hashes.update(round_hashes)
        all_case_nonces.update(round_case_nonces)
        timing_rows, total = budget.read_agent_timing_file(
            timing_file, list(expected_cases)
        )
        derived_total = sum(record["elapsed_seconds"] for record in attestations[1:])
        if not math.isclose(total, derived_total, rel_tol=0, abs_tol=1e-9):
            raise CalibrationError("timing file was not derived from attestations")
        runner_raw = runner_log.read_bytes()
        guest_raw = guest_log.read_bytes()
        validate_runner_log(
            runner_raw, expected_cases, fingerprint, source_inputs, round_number
        )
        runner_gzip = gzip.compress(runner_raw, compresslevel=9, mtime=0)
        guest_gzip = gzip.compress(guest_raw, compresslevel=9, mtime=0)
        runner_gzip_path = stage / f"{round_text}.runner.log.gz"
        guest_gzip_path = stage / f"{round_text}.guest.log.gz"
        runner_gzip_path.write_bytes(runner_gzip)
        guest_gzip_path.write_bytes(guest_gzip)
        runner_log.unlink()
        guest_log.unlink()
        round_records.append(
            {
                "round": round_number,
                "sample_id": (
                    f"agent18-{args.source_commit[:12]}-{round_number:02d}"
                ),
                "round_nonce": round_nonce,
                "started_utc": round_started_utc,
                "completed_utc": round_finished_utc,
                "started_monotonic_ns": round_started_monotonic_ns,
                "finished_monotonic_ns": round_finished_monotonic_ns,
                "started_wall_time_ns": round_started_wall_time_ns,
                "finished_wall_time_ns": round_finished_wall_time_ns,
                "exit_status": 0,
                "total_seconds": total,
                "timing_rows": len(timing_rows),
                "attestation_digest_sha256": round_digest,
                "timing": file_descriptor(timing_file, stage),
                "runner_log": file_descriptor(
                    runner_gzip_path, stage, raw=runner_raw
                ),
                "guest_log": file_descriptor(
                    guest_gzip_path, stage, raw=guest_raw
                ),
                "attestations": [
                    {
                        "case_key": record["case_key"],
                        "init_proc": record["init_proc"],
                        "path": record["path"].relative_to(stage).as_posix(),
                        "bytes": record["bytes"],
                        "sha256": record["sha256"],
                    }
                    for record in attestations
                ],
            }
        )

    tools_after = capture_environment_tools(profile)
    if tools_after != tools_before:
        raise CalibrationError("calibration executable identity changed")
    if capture_host_identity() != host:
        raise CalibrationError("calibration host identity changed")
    validate_live_calibration_profile(profile, tools_after, host)
    campaign_completed_monotonic_ns = time.monotonic_ns()
    campaign_completed_wall_time_ns = time.time_ns()
    campaign_completed_utc = utc_now()
    if len({record["attestation_digest_sha256"] for record in round_records}) != 3:
        raise CalibrationError("calibration round hashes must be distinct")
    environment_record = {
        "schema": 1,
        "evidence_scope": EVIDENCE_SCOPE,
        "remote_ci_attestation": False,
        "source": plan["source"],
        "host": host,
        "executables": tools_after,
        "captured_before_and_after": True,
    }
    write_json_exclusive(stage / "environment.json", environment_record)
    session_record = {
        "schema": 1,
        "evidence_scope": EVIDENCE_SCOPE,
        "remote_ci_attestation": False,
        "campaign_nonce": campaign_nonce,
        "plan_sha256": plan_sha256,
        "source": plan["source"],
        "started_utc": campaign_started_utc,
        "completed_utc": campaign_completed_utc,
        "started_monotonic_ns": campaign_started_monotonic_ns,
        "finished_monotonic_ns": campaign_completed_monotonic_ns,
        "started_wall_time_ns": campaign_started_wall_time_ns,
        "finished_wall_time_ns": campaign_completed_wall_time_ns,
        "serialized": True,
        "predeclared_round_count": ROUND_COUNT,
        "rounds": round_records,
    }
    write_json_exclusive(stage / "session.json", session_record)
    totals = [record["total_seconds"] for record in round_records]
    baseline = statistics.median(totals)
    if max(totals) > baseline * 1.10:
        raise CalibrationError("calibration samples exceed the 10% spread bound")
    maximum = budget.calibrated_agent_test_limit(totals)
    if not math.isclose(maximum, calibrated_limit(totals), rel_tol=0, abs_tol=1e-12):
        raise CalibrationError("calibration limit implementations disagree")
    validation_record = {
        "schema": 1,
        "status": "reviewed_local_e3",
        "evidence_scope": EVIDENCE_SCOPE,
        "remote_ci_attestation": False,
        "source": plan["source"],
        "sample_count": ROUND_COUNT,
        "attestation_count": ROUND_COUNT * (len(expected_cases) + 1),
        "baseline_seconds": baseline,
        "max_seconds": maximum,
        "max_to_median_ratio": maximum / baseline,
        "all_exit_status_zero": True,
        "serialized": True,
    }
    write_json_exclusive(stage / "validation.json", validation_record)
    manifest = {
        "schema": 3,
        "purpose": CAMPAIGN_PURPOSE,
        "evidence_scope": EVIDENCE_SCOPE,
        "remote_ci_attestation": False,
        "source": plan["source"],
        "expected_cases": list(expected_cases),
        "collection": {
            "detached_clean_worktree": True,
            "serialized": True,
            "predeclared_sample_count": ROUND_COUNT,
            "started_utc": campaign_started_utc,
            "completed_utc": campaign_completed_utc,
        },
        "result": {
            "status": "reviewed_local_e3",
            "baseline_seconds": baseline,
            "max_seconds": maximum,
            "max_to_median_ratio": maximum / baseline,
            "limit_policy": budget.AGENT_TEST_CALIBRATION_LIMIT_POLICY,
            "attestation_count": ROUND_COUNT * (len(expected_cases) + 1),
        },
        "plan": file_descriptor(stage / "plan.json", stage),
        "environment": file_descriptor(stage / "environment.json", stage),
        "session": file_descriptor(stage / "session.json", stage),
        "validation": file_descriptor(stage / "validation.json", stage),
        "rounds": round_records,
        "review_boundary": (
            "Local E3 reproduction evidence only; it is unsigned and is not "
            "a GitLab Runner, CI, E4 attestation, or proof of operator honesty."
        ),
    }
    manifest_data = write_json_exclusive(stage / "manifest.json", manifest)
    config_fragment = {
        "calibration_status": "calibrated_full_suite",
        "calibration_source_commit": args.source_commit,
        "calibration_source_tree": source_tree,
        "calibration_manifest_file": (
            f"evidence/calibrations/{args.source_commit[:12]}/manifest.json"
        ),
        "calibration_manifest_sha256": sha256_bytes(manifest_data),
        "source_fingerprint_sha256": fingerprint,
        "baseline_seconds": baseline,
        "max_seconds": maximum,
        "calibration_samples": [
            {
                "sample_id": record["sample_id"],
                "total_seconds": record["total_seconds"],
                "timing_file": (
                    f"evidence/calibrations/{args.source_commit[:12]}/"
                    f"{record['round']:02d}.timing"
                ),
                "timing_file_sha256": record["timing"]["sha256"],
                "attestation_digest_sha256": (
                    record["attestation_digest_sha256"]
                ),
            }
            for record in round_records
        ],
        "calibration_profile_id": profile_id,
    }
    write_json_exclusive(
        stage / "calibration-config.json",
        config_fragment,
    )
    os.replace(stage, output)
    os.replace(output / "calibration-config.json", config_output)
    print(
        "[agent-calibration] local E3 campaign complete: "
        f"output={output} manifest_sha256={sha256_bytes(manifest_data)}"
    )
    return 0


def verify_calibration_package(
    root, tests, expected_source_inputs, policy_config=None
):
    root = reject_link_components(root, "calibration root").resolve(strict=True)
    manifest_relative = tests["calibration_manifest_file"]
    manifest_path = resolve_package_member(
        root, manifest_relative, "calibration manifest"
    )
    package = manifest_path.parent.resolve(strict=True)
    manifest_data = manifest_path.read_bytes()
    if sha256_bytes(manifest_data) != tests["calibration_manifest_sha256"]:
        raise CalibrationError("calibration manifest hash mismatch")
    manifest = exact_object(
        strict_json_bytes(manifest_data, "calibration manifest"),
        {
            "schema",
            "purpose",
            "evidence_scope",
            "remote_ci_attestation",
            "source",
            "expected_cases",
            "collection",
            "result",
            "plan",
            "environment",
            "session",
            "validation",
            "rounds",
            "review_boundary",
        },
        "calibration manifest",
    )
    if (
        manifest["schema"] != 3
        or manifest["purpose"] != CAMPAIGN_PURPOSE
        or manifest["evidence_scope"] != EVIDENCE_SCOPE
        or manifest["remote_ci_attestation"] is not False
        or manifest["expected_cases"] != tests["expected_cases"]
        or manifest["review_boundary"]
        != (
            "Local E3 reproduction evidence only; it is unsigned and is not "
            "a GitLab Runner, CI, E4 attestation, or proof of operator honesty."
        )
    ):
        raise CalibrationError("calibration manifest scope/contract mismatch")
    source = exact_object(
        manifest["source"],
        {"commit", "tree", "fingerprint_sha256", "fingerprint_inputs"},
        "calibration manifest source",
    )
    expected_source = {
        "commit": tests["calibration_source_commit"],
        "tree": tests["calibration_source_tree"],
        "fingerprint_sha256": tests["source_fingerprint_sha256"],
        "fingerprint_inputs": expected_source_inputs,
    }
    if source != expected_source:
        raise CalibrationError("calibration manifest source mismatch")
    require_git_toplevel(root)
    commit_type = run_git(
        root,
        "calibration commit lookup",
        "cat-file",
        "-t",
        source["commit"],
    )
    tree = run_git(
        root,
        "calibration tree lookup",
        "rev-parse",
        f"{source['commit']}^{{tree}}",
    )
    if commit_type.stdout.strip() != "commit" or tree.stdout.strip() != source["tree"]:
        raise CalibrationError("calibration Git commit/tree is unavailable")
    collection = exact_object(
        manifest["collection"],
        {
            "detached_clean_worktree",
            "serialized",
            "predeclared_sample_count",
            "started_utc",
            "completed_utc",
        },
        "calibration collection",
    )
    if (
        collection["detached_clean_worktree"] is not True
        or collection["serialized"] is not True
        or collection["predeclared_sample_count"] != ROUND_COUNT
        or not isinstance(collection["started_utc"], str)
        or UTC_RE.fullmatch(collection["started_utc"]) is None
        or not isinstance(collection["completed_utc"], str)
        or UTC_RE.fullmatch(collection["completed_utc"]) is None
        or collection["started_utc"] > collection["completed_utc"]
    ):
        raise CalibrationError("calibration collection relation mismatch")

    descriptor_names = {
        "plan": "plan.json",
        "environment": "environment.json",
        "session": "session.json",
        "validation": "validation.json",
    }
    if any(
        not isinstance(manifest[name], dict)
        or manifest[name].get("path") != expected
        for name, expected in descriptor_names.items()
    ):
        raise CalibrationError("calibration package descriptor path mismatch")
    plan_path, plan_data = verify_file_descriptor(
        package, manifest["plan"], "calibration plan"
    )
    plan = strict_json_bytes(plan_data, "calibration plan")
    plan_source = validate_plan_structure(plan)
    if (
        plan_source != source
        or plan["expected_cases"] != tests["expected_cases"]
        or plan["evidence_scope"] != EVIDENCE_SCOPE
        or plan["remote_ci_attestation"] is not False
    ):
        raise CalibrationError("calibration plan differs from manifest")
    environment_path, environment_data = verify_file_descriptor(
        package, manifest["environment"], "calibration environment"
    )
    environment = exact_object(
        strict_json_bytes(environment_data, "calibration environment"),
        {
            "schema",
            "evidence_scope",
            "remote_ci_attestation",
            "source",
            "host",
            "executables",
            "captured_before_and_after",
        },
        "calibration environment",
    )
    if (
        environment["schema"] != 1
        or environment["evidence_scope"] != EVIDENCE_SCOPE
        or environment["remote_ci_attestation"] is not False
        or environment["source"] != source
        or environment["executables"] != plan["executables"]
        or environment["captured_before_and_after"] is not True
    ):
        raise CalibrationError("calibration environment mismatch")
    host = exact_object(
        environment["host"],
        {"platform", "machine", "python_runtime"},
        "calibration host",
    )
    for name, value in host.items():
        require_text(value, f"calibration host.{name}")
    if policy_config is not None:
        profile = policy_config["agent_test_suite"][
            "local_calibration_profile"
        ]
        profile_id = validate_recorded_calibration_profile(
            profile, plan["executables"], host
        )
        if tests.get("calibration_profile_id") != profile_id:
            raise CalibrationError("calibration profile id mismatch")
    session_path, session_data = verify_file_descriptor(
        package, manifest["session"], "calibration session"
    )
    session = exact_object(
        strict_json_bytes(session_data, "calibration session"),
        {
            "schema",
            "evidence_scope",
            "remote_ci_attestation",
            "campaign_nonce",
            "plan_sha256",
            "source",
            "started_utc",
            "completed_utc",
            "started_monotonic_ns",
            "finished_monotonic_ns",
            "started_wall_time_ns",
            "finished_wall_time_ns",
            "serialized",
            "predeclared_round_count",
            "rounds",
        },
        "calibration session",
    )
    if (
        session["schema"] != 1
        or session["evidence_scope"] != EVIDENCE_SCOPE
        or session["remote_ci_attestation"] is not False
        or session["campaign_nonce"] != plan["campaign_nonce"]
        or session["plan_sha256"] != sha256_bytes(plan_data)
        or session["source"] != source
        or session["started_utc"] != collection["started_utc"]
        or session["completed_utc"] != collection["completed_utc"]
        or session["serialized"] is not True
        or session["predeclared_round_count"] != ROUND_COUNT
        or session["rounds"] != manifest["rounds"]
    ):
        raise CalibrationError("calibration session mismatch")
    session_start = require_int(
        session["started_monotonic_ns"], "calibration session start", True
    )
    session_finish = require_int(
        session["finished_monotonic_ns"], "calibration session finish", True
    )
    session_wall_start = require_int(
        session["started_wall_time_ns"], "calibration session wall start", True
    )
    session_wall_finish = require_int(
        session["finished_wall_time_ns"], "calibration session wall finish", True
    )
    if session_finish <= session_start or session_wall_finish <= session_wall_start:
        raise CalibrationError("calibration session time relation mismatch")

    rounds = manifest["rounds"]
    samples = tests["calibration_samples"]
    if (
        not isinstance(rounds, list)
        or len(rounds) != ROUND_COUNT
        or len(samples) != ROUND_COUNT
    ):
        raise CalibrationError("calibration requires exactly three rounds")
    all_hashes = set()
    recorded_source_roots = set()
    recorded_stage_roots = set()
    all_nonces = {
        plan["campaign_nonce"],
        *(item["round_nonce"] for item in plan["rounds"]),
    }
    previous_finish = session_start
    previous_wall_finish = session_wall_start
    previous_completed_utc = collection["started_utc"]
    verified_totals = []
    expected_files = {
        "manifest.json",
        "plan.json",
        "environment.json",
        "session.json",
        "validation.json",
    }
    for index, (record, sample) in enumerate(zip(rounds, samples), start=1):
        label = f"calibration round {index}"
        record = exact_object(
            record,
            {
                "round",
                "sample_id",
                "round_nonce",
                "started_utc",
                "completed_utc",
                "started_monotonic_ns",
                "finished_monotonic_ns",
                "started_wall_time_ns",
                "finished_wall_time_ns",
                "exit_status",
                "total_seconds",
                "timing_rows",
                "attestation_digest_sha256",
                "timing",
                "runner_log",
                "guest_log",
                "attestations",
            },
            label,
        )
        if (
            record["round"] != index
            or record["sample_id"] != sample["sample_id"]
            or record["round_nonce"] != plan["rounds"][index - 1]["round_nonce"]
            or record["round_nonce"] not in all_nonces
            or record["exit_status"] != 0
            or record["timing_rows"] != len(tests["expected_cases"])
            or not isinstance(record["started_utc"], str)
            or UTC_RE.fullmatch(record["started_utc"]) is None
            or not isinstance(record["completed_utc"], str)
            or UTC_RE.fullmatch(record["completed_utc"]) is None
            or record["started_utc"] < previous_completed_utc
            or record["completed_utc"] < record["started_utc"]
            or record["completed_utc"] > collection["completed_utc"]
        ):
            raise CalibrationError(f"{label} identity/status mismatch")
        previous_completed_utc = record["completed_utc"]
        round_start = require_int(
            record["started_monotonic_ns"], f"{label} start", True
        )
        round_finish = require_int(
            record["finished_monotonic_ns"], f"{label} finish", True
        )
        round_wall_start = require_int(
            record["started_wall_time_ns"], f"{label} wall start", True
        )
        round_wall_finish = require_int(
            record["finished_wall_time_ns"], f"{label} wall finish", True
        )
        if (
            round_start < previous_finish
            or round_finish <= round_start
            or round_wall_start < previous_wall_finish
            or round_wall_finish <= round_wall_start
            or round_finish > session_finish
            or round_wall_finish > session_wall_finish
        ):
            raise CalibrationError(f"{label} is not serial within the session")
        previous_finish = round_finish
        previous_wall_finish = round_wall_finish
        timing_path, timing_data = verify_file_descriptor(
            package, record["timing"], f"{label} timing"
        )
        if (
            record["timing"]["sha256"] != sample["timing_file_sha256"]
            or sample["attestation_digest_sha256"]
            != record["attestation_digest_sha256"]
            or sample["timing_file"]
            != f"evidence/calibrations/{source['commit'][:12]}/{index:02d}.timing"
            or record["timing"]["path"] != f"{index:02d}.timing"
            or not isinstance(record["runner_log"], dict)
            or record["runner_log"].get("path")
            != f"{index:02d}.runner.log.gz"
            or not isinstance(record["guest_log"], dict)
            or record["guest_log"].get("path")
            != f"{index:02d}.guest.log.gz"
        ):
            raise CalibrationError(f"{label} is not bound to budget config")
        _, runner_raw = verify_file_descriptor(
            package, record["runner_log"], f"{label} runner log", compressed=True
        )
        _, guest_raw = verify_file_descriptor(
            package, record["guest_log"], f"{label} Guest log", compressed=True
        )
        expected_files.update(
            (
                record["timing"]["path"],
                record["runner_log"]["path"],
                record["guest_log"]["path"],
            )
        )
        with tempfile.TemporaryDirectory(prefix="agent-calibration-guest-") as temp:
            guest_path = Path(temp) / "guest.log"
            guest_path.write_bytes(guest_raw)
            _, observed_plan_hash, attestations, round_digest = (
                validate_round_attestations(
                    root,
                    plan_path,
                    index,
                    package / "attestations" / f"{index:02d}",
                    guest_path,
                    production=False,
                )
            )
        if observed_plan_hash != sha256_bytes(plan_data):
            raise CalibrationError(f"{label} plan hash mismatch")
        recorded_source_roots.update(
            item["recorded_source_root"] for item in attestations
        )
        recorded_stage_roots.update(
            item["recorded_stage_root"] for item in attestations
        )
        if len(recorded_source_roots) != 1 or len(recorded_stage_roots) != 1:
            raise CalibrationError("calibration historical roots changed")
        if (
            attestations[0]["started_monotonic_ns"] < round_start
            or attestations[-1]["finished_monotonic_ns"] > round_finish
            or attestations[0]["started_wall_time_ns"] < round_wall_start
            or attestations[-1]["finished_wall_time_ns"] > round_wall_finish
        ):
            raise CalibrationError(f"{label} case timestamps escape the round")
        expected_attestation_descriptors = []
        for attestation in attestations:
            descriptor = {
                "case_key": attestation["case_key"],
                "init_proc": attestation["init_proc"],
                "path": attestation["path"].relative_to(package).as_posix(),
                "bytes": attestation["bytes"],
                "sha256": attestation["sha256"],
            }
            expected_attestation_descriptors.append(descriptor)
            expected_files.add(descriptor["path"])
            if descriptor["sha256"] in all_hashes:
                raise CalibrationError("attestation hash repeats across rounds")
            all_hashes.add(descriptor["sha256"])
            for nonce in (
                attestation["session_nonce"],
                attestation["execution_nonce"],
            ):
                if nonce in all_nonces:
                    raise CalibrationError("case nonce repeats across rounds")
                all_nonces.add(nonce)
        if (
            record["attestations"] != expected_attestation_descriptors
            or round_digest != record["attestation_digest_sha256"]
        ):
            raise CalibrationError(f"{label} attestation digest mismatch")
        expected_timing = "".join(
            f"{attestation['init_proc']} {attestation['elapsed_seconds']:.9f}\n"
            for attestation in attestations[1:]
        ).encode("ascii")
        total = sum(attestation["elapsed_seconds"] for attestation in attestations[1:])
        if timing_data != expected_timing or not math.isclose(
            record["total_seconds"], total, rel_tol=0, abs_tol=1e-9
        ) or not math.isclose(
            sample["total_seconds"], total, rel_tol=0, abs_tol=1e-9
        ):
            raise CalibrationError(f"{label} timing is not attestation-derived")
        verified_totals.append(total)
        validate_runner_log(
            runner_raw,
            tests["expected_cases"],
            source["fingerprint_sha256"],
            source["fingerprint_inputs"],
            index,
        )
    actual_files = package_file_inventory(package)
    if actual_files != expected_files:
        raise CalibrationError("calibration package file inventory mismatch")
    if len({record["attestation_digest_sha256"] for record in rounds}) != 3:
        raise CalibrationError("calibration round digests must be distinct")

    result = exact_object(
        manifest["result"],
        {
            "status",
            "baseline_seconds",
            "max_seconds",
            "max_to_median_ratio",
            "limit_policy",
            "attestation_count",
        },
        "calibration result",
    )
    baseline = statistics.median(verified_totals)
    if max(verified_totals) > baseline * 1.10:
        raise CalibrationError("calibration samples exceed the 10% spread bound")
    expected_limit = calibrated_limit(verified_totals)
    if (
        result["status"] != "reviewed_local_e3"
        or result["limit_policy"]
        != "ceil(max(max_observed, median * 1.05) * 1000) / 1000"
        or result["attestation_count"]
        != ROUND_COUNT * (len(tests["expected_cases"]) + 1)
        or not math.isclose(result["baseline_seconds"], baseline, rel_tol=0, abs_tol=1e-9)
        or not math.isclose(result["max_seconds"], expected_limit, rel_tol=0, abs_tol=1e-9)
        or not math.isclose(tests["max_seconds"], expected_limit, rel_tol=0, abs_tol=1e-9)
        or not math.isclose(
            result["max_to_median_ratio"],
            tests["max_seconds"] / baseline,
            rel_tol=0,
            abs_tol=1e-12,
        )
        or not math.isclose(tests["baseline_seconds"], baseline, rel_tol=0, abs_tol=1e-9)
    ):
        raise CalibrationError("calibration result mismatch")
    _, validation_data = verify_file_descriptor(
        package, manifest["validation"], "calibration validation"
    )
    validation = strict_json_bytes(validation_data, "calibration validation")
    expected_validation = {
        "schema": 1,
        "status": "reviewed_local_e3",
        "evidence_scope": EVIDENCE_SCOPE,
        "remote_ci_attestation": False,
        "source": source,
        "sample_count": ROUND_COUNT,
        "attestation_count": ROUND_COUNT * (len(tests["expected_cases"]) + 1),
        "baseline_seconds": result["baseline_seconds"],
        "max_seconds": result["max_seconds"],
        "max_to_median_ratio": result["max_to_median_ratio"],
        "all_exit_status_zero": True,
        "serialized": True,
    }
    if validation != expected_validation:
        raise CalibrationError("calibration validation summary mismatch")
    return {
        "source_commit": source["commit"],
        "source_tree": source["tree"],
        "sample_count": ROUND_COUNT,
        "attestation_count": result["attestation_count"],
        "baseline_seconds": result["baseline_seconds"],
        "max_seconds": result["max_seconds"],
        "evidence_scope": EVIDENCE_SCOPE,
    }


def build_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    derive = subparsers.add_parser("derive-round")
    derive.add_argument("--root", default=".")
    derive.add_argument("--plan", required=True)
    derive.add_argument("--round", type=int, required=True)
    derive.add_argument("--attestation-dir", required=True)
    derive.add_argument("--guest-log", required=True)
    derive.add_argument("--timing-file", required=True)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--root", default=".")
    collect.add_argument("--source-commit", required=True)
    collect.add_argument("--output", required=True)
    collect.add_argument("--case-timeout", default="300s")

    source_check = subparsers.add_parser("check-source")
    source_check.add_argument("--root", default=".")
    source_check.add_argument("--source-commit", required=True)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        if args.command == "derive-round":
            total = derive_round_timing(
                args.root,
                args.plan,
                args.round,
                args.attestation_dir,
                args.guest_log,
                args.timing_file,
            )
            print(
                "[agent-calibration] attestation-derived timing: "
                f"round={args.round:02d} total={total:.9f}"
            )
            return 0
        if args.command == "collect":
            return collect_campaign(args)
        if args.command == "check-source":
            root, _ = validate_source_checkout(args.root, args.source_commit)
            verify_no_untracked_worktree_entries(root)
            count = verify_source_worktree_bytes(root, args.source_commit)
            print(
                "[agent-calibration] source-check: "
                f"tracked={count} untracked=0"
            )
            return 0
        raise CalibrationError("unknown calibration command")
    except CalibrationError as error:
        print(f"agent-test-calibration: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
