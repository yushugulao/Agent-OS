#!/usr/bin/env python3
"""运行正式 Python 子命令，并阻止 site 或路径启动注入。"""

from __future__ import annotations

import os
import runpy
import stat
import sys
from pathlib import Path


ALLOWED_MODULES = frozenset({"py_compile", "unittest"})
IGNORED_SAFE_FLAGS = frozenset({"-B", "-E", "-I", "-P", "-S", "-s", "-u"})


def _fail(message: str) -> "NoReturn":
    print(f"trusted-python-child: {message}", file=sys.stderr)
    raise SystemExit(2)


def _safe_startup_path() -> bool:
    if getattr(sys, "_agentos_safe_path", False) is True:
        return bool(sys.flags.isolated and sys.flags.no_site)
    flag = getattr(sys.flags, "safe_path", None)
    if flag is not None:
        return bool(flag)
    blocked = {Path.cwd().resolve(), Path(__file__).resolve().parent}
    if not sys.flags.isolated or any(not value for value in sys.path):
        return False
    return all(Path(value).resolve() not in blocked for value in sys.path)


def _absolute_directory(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        _fail(f"{label} is not absolute")
    try:
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except OSError as error:
        _fail(f"{label} is unavailable: {error}")
    if resolved != path or not stat.S_ISDIR(info.st_mode):
        _fail(f"{label} is link-backed or not a directory")
    return path


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_local_path(value: str, roots: tuple[Path, ...], label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        path = path.absolute()
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except OSError as error:
        _fail(f"{label} is unavailable: {error}")
    if not stat.S_ISREG(info.st_mode):
        _fail(f"{label} is link-backed, non-regular, or outside trusted roots")
    current = path.parent
    while True:
        try:
            parent_info = current.lstat()
            if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
                _fail(f"{label} has a link-backed parent")
            if any(os.path.samefile(current, root) for root in roots):
                break
        except OSError as error:
            _fail(f"{label} parent is unavailable: {error}")
        parent = current.parent
        if parent == current:
            _fail(f"{label} is outside trusted roots")
        current = parent
    return resolved


def _append_import_paths(repository: Path, temporary: Path) -> None:
    candidates = [repository / "host_tools", repository / "scripts", repository]
    current = Path.cwd().absolute()
    if _under(current, repository) or _under(current, temporary):
        candidates.append(current)
    for path in candidates:
        value = str(path)
        if path.is_dir() and value not in sys.path:
            sys.path.append(value)


def _reset_import_paths() -> None:
    """跨嵌套调度器恢复精确的隔离启动路径。"""

    retained = getattr(sys, "_agentos_stdlib_path", None)
    if retained is None:
        retained = tuple(sys.path)
        sys._agentos_stdlib_path = retained
    if (
        not isinstance(retained, tuple) or not retained
        or any(not isinstance(value, str) or not value for value in retained)
        or any(
            marker in value.replace("\\", "/").casefold()
            for value in retained for marker in ("site-packages", "dist-packages")
        )
    ):
        _fail("interpreter-owned standard-library path is unavailable")
    sys.path[:] = list(retained)


def _execute(arguments: list[str], repository: Path, temporary: Path) -> None:
    while arguments and arguments[0] in IGNORED_SAFE_FLAGS:
        arguments.pop(0)
    if not arguments:
        _fail("child command is empty")
    _append_import_paths(repository, temporary)
    if arguments[0] in {"--version", "-V"}:
        print(f"Python {sys.version.split()[0]}")
        return
    if arguments[0] == "-c":
        if len(arguments) < 2:
            _fail("-c requires code")
        sys.argv = ["-c", *arguments[2:]]
        namespace = {"__name__": "__main__", "__package__": None}
        exec(compile(arguments[1], "<string>", "exec"), namespace, namespace)
        return
    if arguments[0] == "-m":
        if len(arguments) < 2 or arguments[1] not in ALLOWED_MODULES:
            _fail("module execution is not allowlisted")
        sys.argv = [arguments[1], *arguments[2:]]
        runpy.run_module(arguments[1], run_name="__main__", alter_sys=True)
        return
    if arguments[0] == "-":
        sys.argv = ["-", *arguments[1:]]
        namespace = {"__name__": "__main__", "__package__": None}
        exec(compile(sys.stdin.buffer.read(), "<stdin>", "exec"), namespace, namespace)
        return
    script = _safe_local_path(
        arguments[0], (repository, temporary), "child script"
    )
    if script.suffix.casefold() != ".py":
        _fail("child script is not Python source")
    if str(script.parent) not in sys.path:
        sys.path.append(str(script.parent))
    sys.argv = [str(script), *arguments[1:]]
    runpy.run_path(str(script), run_name="__main__")


def main() -> None:
    if not sys.flags.isolated or not _safe_startup_path() or not sys.flags.no_site:
        _fail("backing Python lacks -I -S")
    if len(sys.argv) < 5 or sys.argv[1] != "--shim" or sys.argv[3] != "--repo":
        _fail("private runtime arguments are malformed")
    backing_executable = sys.executable
    shim = Path(sys.argv[2])
    if not shim.is_absolute() or not shim.is_file():
        _fail("private shim is unavailable")
    repository = _absolute_directory(sys.argv[4], "repository")
    temporary = _absolute_directory(os.environ.get("TMPDIR", ""), "temporary root")
    _reset_import_paths()
    sys._agentos_backing_executable = backing_executable
    sys._agentos_temporary_root = str(temporary)
    sys.executable = str(shim)
    sys._base_executable = str(shim)
    sys._agentos_safe_path = True
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(temporary / f"agentos-pycache-{os.urandom(16).hex()}")
    _execute(sys.argv[5:], repository, temporary)


if __name__ == "__main__":
    main()
