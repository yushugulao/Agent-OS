#!/usr/bin/env python3
"""Create and attest the transitive no-site Python runtime for formal runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


DISPATCHER_PATH = "scripts/trusted-python-child.py"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORMAL_TOOL_ORDER = ("make", "git", "bash", "python", "host_cc", "compiler", "qemu")
# The C locale is the POSIX producer and historical Cygwin default. New Cygwin
# producers override the pair with FORMAL_CYGWIN_LOCALE below.
FORMAL_ENVIRONMENT_FIXED = {
    "LC_ALL": "C", "LANG": "C", "TZ": "UTC", "PYTHONNOUSERSITE": "1",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0",
}
FORMAL_CYGWIN_LOCALE = "C.UTF-8"
FORMAL_ENVIRONMENT_DYNAMIC = {
    "PATH", "HOME", "TMPDIR", "TEMP", "TMP", "FINAL_EVIDENCE_STAGE",
    "QEMU", "PYTHON_BIN",
    "AGENT_TEST_DURATION_PROFILE",
    "CASE_TIMEOUT", "IDLE_NOTICE_SECONDS", "MARKER_GRACE_SECONDS",
    "MECHANISM_MARKER_GRACE_SECONDS", "HOST_CC", "HOSTCC", "CC", "SYSTEMDRIVE",
}
FORMAL_EXECUTION_OVERRIDE_KEYS = frozenset({
    "FINAL_EVIDENCE_STAGE", "QEMU", "PYTHON_BIN", "CASE_TIMEOUT",
    "AGENT_TEST_DURATION_PROFILE",
    "IDLE_NOTICE_SECONDS", "MARKER_GRACE_SECONDS",
    "MECHANISM_MARKER_GRACE_SECONDS", "HOST_CC", "HOSTCC", "CC",
})
DURATION_PROFILE_POLICY_MARKERS = {
    "local-e3": "[full-verify] Agent duration policy profile=local-e3 status=enforced",
    "none": (
        "[full-verify] Agent duration policy profile=none "
        "status=skipped-different-runner"
    ),
}
POSIX_SYSTEM_PATHS = (
    "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin",
)


class FormalPythonRuntimeError(ValueError):
    """Raised when the private Python runtime cannot be trusted."""


def validate_duration_profile_policy_marker(
    environment: object, log_lines: list[str]
) -> str:
    """Bind the formal duration profile to its single named policy decision."""

    profile = (
        environment.get("AGENT_TEST_DURATION_PROFILE")
        if isinstance(environment, dict)
        else None
    )
    if profile not in DURATION_PROFILE_POLICY_MARKERS or any(
        not isinstance(line, str) for line in log_lines
    ):
        raise FormalPythonRuntimeError("formal duration profile is invalid")
    expected = DURATION_PROFILE_POLICY_MARKERS[profile]
    if log_lines.count(expected) != 1 or any(
        marker in log_lines
        for name, marker in DURATION_PROFILE_POLICY_MARKERS.items()
        if name != profile
    ):
        raise FormalPythonRuntimeError(
            "formal duration profile differs from its policy marker"
        )
    return str(profile)


def controlled_search_path(
    tool_directories: list[Path], separator: str, system_paths: list[str] | tuple[str, ...]
) -> str:
    """Build the canonical nested-tool PATH used by producer and verifier."""

    tools = list(dict.fromkeys(str(path) for path in tool_directories))
    return separator.join((*tools, *system_paths))


def formal_execution_overrides(
    evidence_stage: Path,
    tools: dict[str, Path],
    python: Path,
    case_timeout: str,
    idle_notice: str,
    duration_profile: str,
) -> dict[str, str]:
    """Build the sole allowlist of dynamic full-verification variables."""

    if duration_profile not in {"local-e3", "none"}:
        raise FormalPythonRuntimeError(
            "formal Agent duration profile must be local-e3 or none"
        )
    host_cc = str(tools["host_cc"])
    overrides = {
        "AGENT_TEST_DURATION_PROFILE": duration_profile,
        "FINAL_EVIDENCE_STAGE": str(evidence_stage), "QEMU": str(tools["qemu"]),
        "PYTHON_BIN": str(python), "CASE_TIMEOUT": case_timeout,
        "IDLE_NOTICE_SECONDS": idle_notice, "MARKER_GRACE_SECONDS": "2s",
        "MECHANISM_MARKER_GRACE_SECONDS": "5s", "HOST_CC": host_cc,
        "HOSTCC": host_cc, "CC": host_cc,
    }
    if set(overrides) != FORMAL_EXECUTION_OVERRIDE_KEYS:
        raise FormalPythonRuntimeError("formal execution override schema differs")
    return overrides


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path: Path, label: str) -> Path:
    try:
        path = path.resolve(strict=True)
        info = path.lstat()
    except OSError as error:
        raise FormalPythonRuntimeError(f"{label} is unavailable") from error
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise FormalPythonRuntimeError(f"{label} is link-backed or not regular")
    return path


def _canonical_posix_absolute(value: object, label: str) -> PurePosixPath:
    if (
        not isinstance(value, str) or not value or "\\" in value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise FormalPythonRuntimeError(f"{label} is not an absolute POSIX path")
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise FormalPythonRuntimeError(f"{label} is not an absolute POSIX path")
    return path


def _canonical_windows_absolute(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Z]:/(?:[^\\/:\x00-\x1f\x7f]+(?:/[^\\/:\x00-\x1f\x7f]+)*)?", value)
        is None
        or any(part in {"", ".", ".."} for part in value[3:].split("/") if value[3:])
    ):
        raise FormalPythonRuntimeError(f"{label} is not a canonical Windows path")
    return value


def _write_exclusive(path: Path, value: bytes, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _dispatcher_blob(
    git: Path,
    repository: Path,
    worktree: Path,
    commit: str,
    environment: dict[str, str],
) -> bytes:
    if COMMIT_RE.fullmatch(commit) is None:
        raise FormalPythonRuntimeError("source commit is invalid")
    result = subprocess.run(
        [str(git), "-c", "core.fsmonitor=false", "-c",
         "core.untrackedCache=false", "cat-file", "blob",
         f"{commit}:{DISPATCHER_PATH}"],
        cwd=repository,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode or not result.stdout or len(result.stdout) > 256 * 1024:
        raise FormalPythonRuntimeError("trusted child dispatcher blob is unavailable")
    source = _regular(worktree / DISPATCHER_PATH, "trusted child dispatcher")
    if source.read_bytes() != result.stdout:
        raise FormalPythonRuntimeError("trusted child dispatcher differs from source commit")
    return result.stdout


def _shim_bytes(
    shell: Path, real_python: Path, dispatcher: Path, shim: Path, worktree: Path
) -> bytes:
    values = (shell, real_python, dispatcher, shim, worktree)
    if any("\n" in str(value) or "\r" in str(value) for value in values):
        raise FormalPythonRuntimeError("formal Python runtime path contains a newline")
    if any(character.isspace() for character in str(shell)):
        raise FormalPythonRuntimeError("formal sh path cannot be used in a shebang")
    command = " ".join(
        shlex.quote(str(value))
        for value in (real_python, "-I", "-S", "-B", "-u", dispatcher)
    )
    return (
        f"#!{shell}\nexec {command} --shim {shlex.quote(str(shim))} "
        f"--repo {shlex.quote(str(worktree))} \"$@\"\n"
    ).encode("utf-8")


def _probe(executable: Path, environment: dict[str, str]) -> dict[str, object]:
    program = (
        "import json,os,sys;print(json.dumps({"
        "'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,"
        "'safe_path':int(bool(getattr(sys,'_agentos_safe_path',False) or "
        "getattr(sys.flags,'safe_path',False) or (sys.flags.isolated and '' not in "
        "sys.path and os.path.abspath(os.getcwd()) not in "
        "[os.path.abspath(p) for p in sys.path]))),"
        "'ignore_environment':sys.flags.ignore_environment,"
        "'no_user_site':sys.flags.no_user_site,'dont_write_bytecode':sys.flags.dont_write_bytecode,"
        "'executable':sys.executable,'base_executable':getattr(sys,'_base_executable',sys.executable),"
        "'path':sys.path},sort_keys=True))"
    )
    result = subprocess.run(
        [str(executable), "-c", program],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    try:
        value = json.loads(result.stdout.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        detail = result.stderr.decode("utf-8", "replace")[-1000:]
        raise FormalPythonRuntimeError(
            f"formal Python runtime probe is malformed: {detail}"
        ) from error
    expected = {
        "isolated", "no_site", "safe_path", "ignore_environment",
        "no_user_site", "dont_write_bytecode", "executable", "base_executable", "path",
    }
    if (
        result.returncode
        or not isinstance(value, dict)
        or set(value) != expected
        or any(value[name] != 1 for name in (
            "isolated", "no_site", "safe_path", "ignore_environment",
            "no_user_site", "dont_write_bytecode",
        ))
        or value["executable"] != str(executable)
        or value["base_executable"] != str(executable)
        or not isinstance(value["path"], list)
        or any(
            marker in str(item).casefold()
            for item in value["path"]
            for marker in ("site-packages", "dist-packages")
        )
    ):
        detail = result.stderr.decode("utf-8", "replace")[-1000:]
        raise FormalPythonRuntimeError(f"formal Python runtime probe failed: {detail}")
    return value


def _probe_backing(executable: Path, environment: dict[str, str]) -> dict[str, object]:
    program = (
        "import json,os,platform,sys;print(json.dumps({"
        "'implementation':platform.python_implementation(),"
        "'version':list(sys.version_info[:3]),'cache_tag':sys.implementation.cache_tag,"
        "'abi_flags':getattr(sys,'abiflags',''),'executable':sys.executable,"
        "'base_executable':getattr(sys,'_base_executable',sys.executable),"
        "'path':sys.path,"
        "'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,"
        "'safe_path':int(bool(getattr(sys,'_agentos_safe_path',False) or "
        "getattr(sys.flags,'safe_path',False) or (sys.flags.isolated and '' not in "
        "sys.path and os.path.abspath(os.getcwd()) not in "
        "[os.path.abspath(p) for p in sys.path]))),"
        "'ignore_environment':sys.flags.ignore_environment,"
        "'no_user_site':sys.flags.no_user_site,'dont_write_bytecode':sys.flags.dont_write_bytecode}"
        ",sort_keys=True))"
    )
    result = subprocess.run(
        [str(executable), "-I", "-S", "-B", "-c", program],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    try:
        value = json.loads(result.stdout.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        detail = result.stderr.decode("utf-8", "replace")[-1000:]
        raise FormalPythonRuntimeError(
            f"backing Python probe is malformed: {detail}"
        ) from error
    flags = (
        "isolated", "no_site", "safe_path", "ignore_environment",
        "no_user_site", "dont_write_bytecode",
    )
    try:
        executable_identity = Path(str(value["executable"])).resolve(strict=True)
        base_identity = Path(str(value["base_executable"])).resolve(strict=True)
        version = tuple(value["version"])
    except (KeyError, OSError, TypeError) as error:
        raise FormalPythonRuntimeError("backing Python identity is malformed") from error
    if (
        result.returncode
        or set(value) != {
            "implementation", "version", "cache_tag", "abi_flags", "executable",
            "base_executable", "path", *flags,
        }
        or any(value[name] != 1 for name in flags)
        or not os.path.samefile(executable_identity, executable)
        or not os.path.samefile(base_identity, executable)
        or len(version) != 3
        or any(isinstance(item, bool) or not isinstance(item, int) for item in version)
        or version < (3, 10, 0)
        or value["implementation"] != "CPython"
        or not isinstance(value["cache_tag"], str)
        or not value["cache_tag"]
        or not isinstance(value["path"], list)
        or any(
            marker in str(item).casefold()
            for item in value["path"]
            for marker in ("site-packages", "dist-packages")
        )
    ):
        detail = result.stderr.decode("utf-8", "replace")[-1000:]
        raise FormalPythonRuntimeError(f"backing Python probe failed: {detail}")
    # Some POSIX compatibility layers expose the same executable both with and
    # without a platform suffix. Bind the record to the already-attested path
    # after proving file identity instead of trusting either spelling.
    value["executable"] = str(executable)
    value["base_executable"] = str(executable)
    return value


@dataclass(frozen=True)
class FormalPythonRuntime:
    executable: Path
    directory: Path
    record: dict[str, object]
    _paths: tuple[Path, ...]
    _hashes: tuple[str, ...]
    _environment: dict[str, str]

    def verify(self, stage: str) -> None:
        if not stage.strip():
            raise FormalPythonRuntimeError("runtime verification stage is empty")
        for path, expected in zip(self._paths, self._hashes, strict=True):
            if _regular(path, "formal Python runtime file") != path or _sha256_file(path) != expected:
                raise FormalPythonRuntimeError(f"formal Python runtime changed {stage}")
        if _probe(self.executable, self._environment) != self.record["probe"]:
            raise FormalPythonRuntimeError(f"formal Python runtime behavior changed {stage}")

    def path_resolution(self, environment: dict[str, str]) -> dict[str, str]:
        aliases = self.record["shim"]["aliases"]
        resolved: dict[str, str] = {}
        for name, expected in zip(("python", "python3"), aliases, strict=True):
            found = shutil.which(name, path=environment.get("PATH", ""))
            if found is None or Path(found).resolve(strict=True) != Path(expected):
                raise FormalPythonRuntimeError(f"formal {name} does not resolve to its shim")
            resolved[name] = str(Path(found).resolve(strict=True))
        return resolved


def create_formal_python_runtime(
    *,
    root: Path,
    real_python: Path,
    shell: Path,
    git: Path,
    repository: Path,
    worktree: Path,
    commit: str,
    environment: dict[str, str],
) -> FormalPythonRuntime:
    """Create a private recursive shim from the authenticated commit bytes."""

    real_python = _regular(real_python, "backing Python")
    git = _regular(git, "Git executable")
    shell = _regular(shell, "formal shell")
    directory = root / "python-runtime"
    try:
        directory.mkdir(mode=0o700)
    except OSError as error:
        raise FormalPythonRuntimeError("private Python runtime directory is unavailable") from error
    dispatcher_blob = _dispatcher_blob(
        git, repository, worktree, commit, environment
    )
    dispatcher = directory / "trusted-python-child.py"
    _write_exclusive(dispatcher, dispatcher_blob, 0o400)
    executable = directory / "python"
    shim = _shim_bytes(shell, real_python, dispatcher, executable, worktree)
    aliases = tuple(directory / name for name in ("python", "python3"))
    for alias in aliases:
        _write_exclusive(alias, shim, 0o500)
    probe_environment = dict(environment)
    probe_environment["PATH"] = os.pathsep.join(
        (str(directory), environment.get("PATH", ""))
    )
    backing_probe = _probe_backing(real_python, probe_environment)
    probe = _probe(executable, probe_environment)
    paths = (real_python, shell, dispatcher, *aliases)
    hashes = tuple(_sha256_file(path) for path in paths)
    record = {
        "schema_version": 1,
        "backing_python": {
            "path": str(real_python), "sha256": hashes[0], "probe": backing_probe,
        },
        "shell": {"path": str(shell), "sha256": hashes[1]},
        "dispatcher": {
            "source_path": DISPATCHER_PATH,
            "runtime_path": str(dispatcher),
            "repository_path": str(worktree),
            "sha256": hashes[2],
        },
        "shim": {
            "path": str(executable),
            "aliases": [str(path) for path in aliases],
            "sha256": _sha256_bytes(shim),
            "exec_argv_prefix": [
                str(real_python), "-I", "-S", "-B", "-u", str(dispatcher),
            ],
        },
        "probe": probe,
    }
    runtime = FormalPythonRuntime(
        executable, directory, record, paths, hashes, probe_environment
    )
    runtime.verify("after creation")
    return runtime


def validate_formal_python_runtime_record(
    value: object, contract_root: Path
) -> None:
    """Validate the portable fields against the authenticated source snapshot."""

    if not isinstance(value, dict) or set(value) != {
        "schema_version", "backing_python", "shell", "dispatcher", "shim", "probe"
    } or value.get("schema_version") != 1:
        raise FormalPythonRuntimeError("formal Python runtime record schema differs")
    backing = value.get("backing_python")
    shell = value.get("shell")
    if (
        not isinstance(backing, dict)
        or set(backing) != {"path", "sha256", "probe"}
        or not isinstance(shell, dict)
        or set(shell) != {"path", "sha256"}
        or any(not isinstance(item.get("path"), str) for item in (backing, shell))
        or any(SHA256_RE.fullmatch(str(item.get("sha256", ""))) is None for item in (backing, shell))
    ):
        raise FormalPythonRuntimeError("formal Python backing or shell record is invalid")
    _canonical_posix_absolute(backing["path"], "formal Python backing")
    _canonical_posix_absolute(shell["path"], "formal Python shell")
    backing_probe = backing.get("probe")
    flag_names = (
        "isolated", "no_site", "safe_path", "ignore_environment",
        "no_user_site", "dont_write_bytecode",
    )
    if (
        not isinstance(backing_probe, dict)
        or set(backing_probe) != {
            "implementation", "version", "cache_tag", "abi_flags", "executable",
            "base_executable", "path", *flag_names,
        }
        or any(backing_probe.get(name) != 1 for name in flag_names)
        or backing_probe.get("implementation") != "CPython"
        or not isinstance(backing_probe.get("cache_tag"), str)
        or not backing_probe.get("cache_tag")
        or not isinstance(backing_probe.get("abi_flags"), str)
        or backing_probe.get("executable") != backing.get("path")
        or backing_probe.get("base_executable") != backing.get("path")
        or not isinstance(backing_probe.get("version"), list)
        or len(backing_probe.get("version", [])) != 3
        or any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in backing_probe.get("version", [])
        )
        or tuple(backing_probe["version"]) < (3, 10, 0)
        or not isinstance(backing_probe.get("path"), list)
        or any(
            marker in str(item).casefold()
            for item in backing_probe.get("path", [])
            for marker in ("site-packages", "dist-packages")
        )
    ):
        raise FormalPythonRuntimeError("formal Python backing probe is invalid")
    for path in backing_probe["path"]:
        _canonical_posix_absolute(path, "formal Python backing search path")
    dispatcher = value.get("dispatcher")
    if (
        not isinstance(dispatcher, dict)
        or set(dispatcher) != {
            "source_path", "runtime_path", "repository_path", "sha256"
        }
        or dispatcher.get("source_path") != DISPATCHER_PATH
        or SHA256_RE.fullmatch(str(dispatcher.get("sha256", ""))) is None
        or _sha256_file(_regular(contract_root / DISPATCHER_PATH, "contract dispatcher"))
        != dispatcher.get("sha256")
    ):
        raise FormalPythonRuntimeError("formal Python dispatcher record is invalid")
    _canonical_posix_absolute(dispatcher["runtime_path"], "formal dispatcher runtime")
    _canonical_posix_absolute(dispatcher["repository_path"], "formal dispatcher repository")
    shim = value.get("shim")
    probe = value.get("probe")
    if not isinstance(shim, dict) or set(shim) != {
        "path", "aliases", "sha256", "exec_argv_prefix"
    } or SHA256_RE.fullmatch(str(shim.get("sha256", ""))) is None:
        raise FormalPythonRuntimeError("formal Python shim record is invalid")
    _canonical_posix_absolute(shim["path"], "formal Python shim")
    if not isinstance(shim.get("aliases"), list):
        raise FormalPythonRuntimeError("formal Python aliases are invalid")
    for alias in shim["aliases"]:
        _canonical_posix_absolute(alias, "formal Python alias")
    expected_prefix = [
        backing["path"], "-I", "-S", "-B", "-u", dispatcher["runtime_path"],
    ]
    aliases = shim.get("aliases")
    shim_path = PurePosixPath(str(shim.get("path")))
    if (
        shim_path.name != "python"
        or shim_path.parent.name != "python-runtime"
        or PurePosixPath(dispatcher["runtime_path"]) != shim_path.with_name(
            "trusted-python-child.py"
        )
        or shim.get("exec_argv_prefix") != expected_prefix
        or aliases != [
            shim.get("path"),
            str(PurePosixPath(str(shim.get("path"))).with_name("python3")),
        ]
        or _sha256_bytes(_shim_bytes(
            PurePosixPath(shell["path"]), PurePosixPath(backing["path"]),
            PurePosixPath(dispatcher["runtime_path"]), PurePosixPath(shim["path"]),
            PurePosixPath(dispatcher["repository_path"]),
        )) != shim.get("sha256")
    ):
        raise FormalPythonRuntimeError("formal Python shim binding is invalid")
    if not isinstance(probe, dict) or set(probe) != {
        "isolated", "no_site", "safe_path", "ignore_environment",
        "no_user_site", "dont_write_bytecode", "executable", "base_executable", "path",
    } or any(probe.get(name) != 1 for name in (
        "isolated", "no_site", "safe_path", "ignore_environment",
        "no_user_site", "dont_write_bytecode",
    )) or probe.get("executable") != shim.get("path") or probe.get(
        "base_executable"
    ) != shim.get("path") or not isinstance(
        probe.get("path"), list
    ) or any(
        marker in str(item).casefold()
        for item in probe["path"] for marker in ("site-packages", "dist-packages")
    ):
        raise FormalPythonRuntimeError("formal Python runtime probe record is invalid")
    for path in probe["path"]:
        _canonical_posix_absolute(path, "formal Python runtime search path")


def validate_formal_python_tool_binding(
    value: dict[str, object],
    python_tool: dict[str, object],
    shell_tool: dict[str, object],
    python_bin: object,
    path_resolution: object,
) -> None:
    """Cross-bind the portable launch record to the executed tool and PATH."""

    backing = value["backing_python"]
    shim = value["shim"]
    expected_resolution = dict(
        zip(("python", "python3"), shim["aliases"], strict=True)
    )
    if (
        backing["path"] != python_tool.get("path")
        or backing["sha256"] != python_tool.get("executable_sha256")
        or value["shell"]["path"] != shell_tool.get("path")
        or value["shell"]["sha256"] != shell_tool.get("executable_sha256")
        or python_bin != shim["path"]
        or path_resolution != expected_resolution
    ):
        raise FormalPythonRuntimeError("formal Python tool binding differs")


def validate_formal_execution_environment(
    value: object,
    launch: dict[str, object],
    tools: dict[str, dict[str, object]],
    temporary_binding: object,
) -> None:
    """Validate the exact POSIX environment used by a formal full verify."""

    expected_keys = set(FORMAL_ENVIRONMENT_FIXED) | FORMAL_ENVIRONMENT_DYNAMIC
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or any(not isinstance(item, str) or not item for item in value.values())
    ):
        raise FormalPythonRuntimeError("formal execution environment schema differs")
    validate_formal_temporary_binding(temporary_binding, value)
    platform_name = temporary_binding["execution_platform"]
    fixed_without_locale = {
        name: expected
        for name, expected in FORMAL_ENVIRONMENT_FIXED.items()
        if name not in {"LANG", "LC_ALL"}
    }
    locale = (
        value.get("LANG"), value.get("LC_ALL")
    ) if isinstance(value, dict) else (None, None)
    allowed_locales = {("C", "C")}
    if platform_name == "cygwin":
        # Historical payloads used C. New Cygwin executions require UTF-8 so
        # native compiler paths survive argv conversion through POSIX tools.
        allowed_locales.add((FORMAL_CYGWIN_LOCALE, FORMAL_CYGWIN_LOCALE))
    if (
        locale not in allowed_locales
        or any(value.get(name) != expected for name, expected in fixed_without_locale.items())
    ):
        raise FormalPythonRuntimeError("formal execution environment schema differs")
    for name in ("HOME", "TMPDIR", "FINAL_EVIDENCE_STAGE", "QEMU", "PYTHON_BIN", "HOST_CC", "HOSTCC", "CC"):
        _canonical_posix_absolute(value[name], f"formal environment {name}")
    launch_root = PurePosixPath(str(launch["shim"]["path"])).parent.parent
    directories = [PurePosixPath(str(launch["shim"]["path"])).parent]
    for label in FORMAL_TOOL_ORDER:
        record = tools.get(label)
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise FormalPythonRuntimeError("formal execution tool inventory differs")
        directory = PurePosixPath(record["path"]).parent
        if directory not in directories:
            directories.append(directory)
    expected_path = controlled_search_path(directories, ":", POSIX_SYSTEM_PATHS)
    if (
        value["PATH"] != expected_path
        or value["HOME"] != str(launch_root / "home")
        or value["TMPDIR"] != str(launch_root / "tmp")
        or value["PYTHON_BIN"] != launch["shim"]["path"]
        or value["QEMU"] != tools["qemu"].get("path")
        or any(value[name] != tools["host_cc"].get("path") for name in ("HOST_CC", "HOSTCC", "CC"))
        or re.fullmatch(r"[1-9][0-9]*s", value["CASE_TIMEOUT"]) is None
        or re.fullmatch(r"[1-9][0-9]*", value["IDLE_NOTICE_SECONDS"]) is None
        or value["MARKER_GRACE_SECONDS"] != "2s"
        or value["MECHANISM_MARKER_GRACE_SECONDS"] != "5s"
        or value["AGENT_TEST_DURATION_PROFILE"] not in {"local-e3", "none"}
        or re.fullmatch(r"(?:/|[A-Z]:)", value["SYSTEMDRIVE"]) is None
        or not PurePosixPath(value["FINAL_EVIDENCE_STAGE"]).is_absolute()
    ):
        raise FormalPythonRuntimeError("formal execution environment binding differs")


def validate_formal_temporary_binding(
    binding: object,
    environment: dict[str, str],
) -> None:
    """Validate the portable receipt for the POSIX/native temp identity check."""

    expected_keys = {
        "schema_version", "kind", "execution_platform", "conversion_api",
        "posix_path", "native_path", "roundtrip_path", "identities", "checks",
    }
    if (
        not isinstance(binding, dict)
        or set(binding) != expected_keys
        or binding.get("schema_version") != 1
        or binding.get("kind") != "formal-temporary-directory-binding"
        or binding.get("checks") != [
            "posix-native-samefile", "posix-roundtrip-samefile",
        ]
        or binding.get("posix_path") != environment.get("TMPDIR")
        or binding.get("native_path") != environment.get("TEMP")
        or binding.get("roundtrip_path") != environment.get("TMPDIR")
        or environment.get("TMP") != environment.get("TEMP")
    ):
        raise FormalPythonRuntimeError("formal temporary directory binding differs")
    identities = binding.get("identities")
    if not isinstance(identities, dict) or set(identities) != {
        "posix", "native", "roundtrip",
    }:
        raise FormalPythonRuntimeError("formal temporary directory identity is invalid")
    normalized_identities = []
    for identity in identities.values():
        if (
            not isinstance(identity, dict)
            or set(identity) != {"device", "inode"}
            or any(
                isinstance(identity.get(name), bool)
                or not isinstance(identity.get(name), int)
                or identity[name] < 0
                for name in ("device", "inode")
            )
        ):
            raise FormalPythonRuntimeError(
                "formal temporary directory identity is invalid"
            )
        normalized_identities.append((identity["device"], identity["inode"]))
    if len(set(normalized_identities)) != 1:
        raise FormalPythonRuntimeError("formal temporary directory identities differ")
    platform_name = binding.get("execution_platform")
    if platform_name == "posix":
        if (
            binding.get("conversion_api") != "identity"
            or binding.get("roundtrip_path") != environment["TMPDIR"]
            or environment.get("TEMP") != environment["TMPDIR"]
            or environment.get("SYSTEMDRIVE") != "/"
        ):
            raise FormalPythonRuntimeError("formal POSIX temporary binding differs")
    elif platform_name == "cygwin":
        if (
            binding.get("conversion_api")
            not in {
                "msys-2.0.dll:cygwin_conv_path",
                "cygwin1.dll:cygwin_conv_path",
            }
            or re.fullmatch(r"[A-Z]:", environment.get("SYSTEMDRIVE", "")) is None
        ):
            raise FormalPythonRuntimeError("formal Cygwin temporary binding differs")
        _canonical_windows_absolute(
            binding.get("native_path"), "formal native temporary directory"
        )
        _canonical_posix_absolute(
            binding.get("roundtrip_path"), "formal temporary roundtrip path"
        )
    else:
        raise FormalPythonRuntimeError("formal execution platform is invalid")


def validate_formal_evidence_binding(
    environment_record: object,
    execution_environment: object,
    python_bin: object,
    path_resolution: object,
    contract_root: Path,
    expected_labels: set[str],
) -> dict[str, dict[str, object]]:
    """Validate a portable launch, tool and controlled-environment binding."""

    if not isinstance(environment_record, dict) or set(environment_record) != {
        "captured_at_utc", "platform", "machine", "python_runtime", "python_launch",
        "python_path_resolution", "execution_environment",
        "temporary_directory_binding", "tools",
    } or environment_record.get("execution_environment") != execution_environment or (
        environment_record.get("python_path_resolution") != path_resolution
    ):
        raise FormalPythonRuntimeError("formal environment record schema differs")
    launch = environment_record["python_launch"]
    validate_formal_python_runtime_record(launch, contract_root)
    records = environment_record["tools"]
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise FormalPythonRuntimeError("formal tool inventory is invalid")
    labels = [item.get("label") for item in records]
    if len(labels) != len(set(labels)) or set(labels) != expected_labels:
        raise FormalPythonRuntimeError("formal tool labels are missing or duplicated")
    tools = {str(item["label"]): item for item in records}
    for label, record in tools.items():
        _canonical_posix_absolute(record.get("path"), f"formal tool {label}")
    validate_formal_python_tool_binding(
        launch, tools["python"], tools["bash"], python_bin, path_resolution
    )
    validate_formal_execution_environment(
        execution_environment, launch, tools,
        environment_record["temporary_directory_binding"],
    )
    return tools
