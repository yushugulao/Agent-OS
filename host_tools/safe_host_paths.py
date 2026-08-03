#!/usr/bin/env python3
"""Link-safe host path primitives shared by acceptance tools."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from functools import lru_cache
from pathlib import Path


_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_CCP_POSIX_TO_WIN_W = 1
DEFAULT_MAX_WALK_FILES = 10_000
DEFAULT_MAX_WALK_DIRECTORIES = 10_000
DEFAULT_MAX_WALK_BYTES = 1 << 30
DEFAULT_MAX_WALK_DEPTH = 64


def _regular_file_identity(file_info: os.stat_result) -> tuple[int, int, int]:
    """Return a stable path/descriptor identity on the current host."""

    # CPython 3.12+ exposes Windows creation time as st_birthtime.  On some
    # Windows releases Path.lstat() reports that value through st_ctime while
    # os.fstat() reports the last-write time through st_ctime.  Comparing the
    # two ctime values therefore rejects an unchanged file.  Creation time is
    # the stable replacement marker on Windows; POSIX keeps the stronger ctime
    # marker, which also changes when an inode is relinked.
    identity_time = (
        file_info.st_birthtime_ns
        if os.name == "nt" and hasattr(file_info, "st_birthtime_ns")
        else file_info.st_ctime_ns
    )
    return (file_info.st_dev, file_info.st_ino, identity_time)


def _regular_file_content_stamp(file_info: os.stat_result) -> tuple[int, int]:
    """Return metadata that changes when an opened file is rewritten."""

    return (file_info.st_size, file_info.st_mtime_ns)


def absolute_lexical_path(path: Path) -> Path:
    """Return an absolute path without resolving a link component."""

    expanded = path.expanduser()
    # Preserve the caller's concrete path flavour.  Platform probes may mock
    # ``os.name`` while still handling an already-created POSIX path.
    return type(expanded)(os.path.abspath(os.fspath(expanded)))


def path_components(path: Path) -> list[Path]:
    current = absolute_lexical_path(path)
    components = [current]
    while current.parent != current:
        current = current.parent
        components.append(current)
    components.reverse()
    return components


@lru_cache(maxsize=1)
def loaded_msys_path_api():
    """Return MSYS path conversion and Win32 attribute functions, if applicable."""

    if os.name != "posix" or sys.platform != "cygwin":
        return None

    import ctypes

    kernel32 = ctypes.CDLL("Kernel32.dll", use_last_error=True)
    get_module_handle = kernel32.GetModuleHandleW
    get_module_handle.argtypes = [ctypes.c_wchar_p]
    get_module_handle.restype = ctypes.c_void_p
    loaded = []
    for name in ("msys-2.0.dll", "cygwin1.dll"):
        handle = get_module_handle(name)
        if not handle:
            continue
        runtime = ctypes.CDLL(None, handle=handle, use_errno=True)
        if hasattr(runtime, "cygwin_conv_path"):
            loaded.append((runtime, name))
    if not loaded:
        return None
    if len(loaded) != 1:
        raise OSError("multiple POSIX runtimes are loaded")

    runtime, runtime_name = loaded[0]
    converter = runtime.cygwin_conv_path
    converter.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    converter.restype = ctypes.c_ssize_t
    attributes = kernel32.GetFileAttributesW
    attributes.argtypes = [ctypes.c_wchar_p]
    attributes.restype = ctypes.c_uint32
    return ctypes, converter, attributes, runtime_name


def _msys_native_file_attributes(path: Path) -> int | None:
    """Inspect a lexical MSYS path through Win32 without opening its target."""

    api = loaded_msys_path_api()
    if api is None:
        return None
    ctypes, converter, get_attributes, _runtime_name = api
    encoded = os.fsencode(absolute_lexical_path(path))
    required = converter(_CCP_POSIX_TO_WIN_W, encoded, None, 0)
    if required <= 0 or required % 2:
        raise OSError(f"cannot convert MSYS path for reparse inspection: {path}")
    native_buffer = ctypes.create_string_buffer(required)
    if converter(_CCP_POSIX_TO_WIN_W, encoded, native_buffer, required) != 0:
        raise OSError(f"cannot convert MSYS path for reparse inspection: {path}")
    native_path = native_buffer.raw.decode("utf-16-le").split("\0", 1)[0]
    value = int(get_attributes(native_path))
    if value == _INVALID_FILE_ATTRIBUTES:
        raise OSError(f"cannot inspect Windows file attributes: {path}")
    return value


def path_is_link(
    path: Path,
    mode: int | None = None,
    *,
    file_info: os.stat_result | None = None,
) -> bool:
    """Detect POSIX links, Windows junctions, and otherwise-hidden reparse points."""

    if file_info is None:
        try:
            file_info = path.lstat()
        except FileNotFoundError:
            return bool(mode is not None and stat.S_ISLNK(mode))
    if mode is None:
        mode = file_info.st_mode
    if stat.S_ISLNK(mode):
        return True
    if int(getattr(file_info, "st_file_attributes", 0)) & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    junction_test = getattr(path, "is_junction", None)
    try:
        if junction_test and junction_test():
            return True
    except OSError:
        return True
    native_attributes = _msys_native_file_attributes(path)
    return bool(
        native_attributes is not None
        and native_attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def reject_link_components(path: Path) -> Path:
    """Reject every existing symlink or junction in a lexical path."""

    absolute = absolute_lexical_path(path)
    for component in path_components(absolute):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if path_is_link(component, info.st_mode, file_info=info):
            raise ValueError(f"Path contains a symbolic link or junction: {component}")
    return absolute


def ensure_safe_directory(path: Path, mode: int = 0o700) -> Path:
    """Create a directory tree without accepting a link component."""

    absolute = absolute_lexical_path(path)
    for component in path_components(absolute):
        try:
            info = component.lstat()
        except FileNotFoundError:
            try:
                os.mkdir(component, mode)
            except FileExistsError:
                info = component.lstat()
            else:
                info = component.lstat()
        if path_is_link(component, info.st_mode, file_info=info):
            raise ValueError(f"Path contains a symbolic link or junction: {component}")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Path component is not a directory: {component}")
    return absolute


def require_safe_directory(path: Path) -> Path:
    """Require one existing, ordinary directory with no link-backed component."""

    absolute = reject_link_components(path)
    try:
        info = absolute.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"Directory is missing: {absolute}") from error
    if path_is_link(absolute, info.st_mode, file_info=info) or not stat.S_ISDIR(
        info.st_mode
    ):
        raise ValueError(f"Path is not a safe directory: {absolute}")
    return absolute


def require_regular_file(
    path: Path, *, nonempty: bool = False, maximum_bytes: int | None = None
) -> Path:
    """Require one ordinary file whose complete lexical path is link-free."""

    absolute = reject_link_components(path)
    try:
        info = absolute.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"Regular file is missing: {absolute}") from error
    if path_is_link(absolute, info.st_mode, file_info=info) or not stat.S_ISREG(
        info.st_mode
    ):
        raise ValueError(f"Path is not a safe regular file: {absolute}")
    if nonempty and info.st_size == 0:
        raise ValueError(f"Regular file is empty: {absolute}")
    if maximum_bytes is not None and (
        maximum_bytes < 0 or info.st_size > maximum_bytes
    ):
        raise ValueError(f"Regular file exceeds its byte limit: {absolute}")
    return absolute


def read_regular_file(
    path: Path, *, nonempty: bool = False, maximum_bytes: int | None = None
) -> bytes:
    """Read a bounded regular file after validating its complete lexical path."""

    absolute = require_regular_file(
        path, nonempty=nonempty, maximum_bytes=maximum_bytes
    )
    expected = absolute.lstat()
    flags = os.O_RDONLY
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= int(getattr(os, name, 0))
    descriptor = os.open(os.fspath(absolute), flags)
    try:
        opened_before = os.fstat(descriptor)
        expected_identity = _regular_file_identity(expected)
        expected_content = _regular_file_content_stamp(expected)
        opened_identity = _regular_file_identity(opened_before)
        if (
            not stat.S_ISREG(opened_before.st_mode)
            or opened_identity != expected_identity
            or _regular_file_content_stamp(opened_before) != expected_content
        ):
            raise ValueError(f"Regular file changed before it was read: {absolute}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            if maximum_bytes is None:
                data = handle.read()
            else:
                data = handle.read(maximum_bytes + 1)
            opened_after = os.fstat(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        final = absolute.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"Regular file disappeared while being read: {absolute}") from error
    if (
        not stat.S_ISREG(opened_after.st_mode)
        or len(data) != expected.st_size
        or _regular_file_identity(opened_after) != expected_identity
        or _regular_file_content_stamp(opened_after) != expected_content
        or path_is_link(absolute, final.st_mode, file_info=final)
        or not stat.S_ISREG(final.st_mode)
        or _regular_file_identity(final) != expected_identity
        or _regular_file_content_stamp(final) != expected_content
        or (nonempty and len(data) == 0)
        or (maximum_bytes is not None and len(data) > maximum_bytes)
    ):
        raise ValueError(f"Regular file changed or exceeded its byte limit: {absolute}")
    return data


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    replace: bool = True,
    mode: int = 0o600,
) -> Path:
    """Atomically publish bytes below a verified, link-free parent directory."""

    absolute = absolute_lexical_path(path)
    parent = ensure_safe_directory(absolute.parent)
    try:
        existing = absolute.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if path_is_link(absolute, existing.st_mode, file_info=existing) or not stat.S_ISREG(
            existing.st_mode
        ):
            raise ValueError(f"Output path is not a safe regular file: {absolute}")
        if not replace:
            raise FileExistsError(absolute)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{absolute.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        reject_link_components(parent)
        if path_is_link(absolute):
            raise ValueError(f"Output path became link-backed: {absolute}")
        if not replace and absolute.exists():
            raise FileExistsError(absolute)
        os.replace(temporary, absolute)
        return absolute
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def walk_directory_tree_no_links(
    root: Path,
    *,
    max_files: int = DEFAULT_MAX_WALK_FILES,
    max_directories: int = DEFAULT_MAX_WALK_DIRECTORIES,
    max_total_bytes: int = DEFAULT_MAX_WALK_BYTES,
    max_depth: int = DEFAULT_MAX_WALK_DEPTH,
) -> tuple[list[Path], list[Path]]:
    """Return bounded directory and file inventories without following links."""

    budgets = (max_files, max_directories, max_total_bytes, max_depth)
    if any(type(value) is not int or value < 0 for value in budgets):
        raise ValueError("Recursive path budgets must be nonnegative integers")
    absolute = require_safe_directory(root)
    pending: list[tuple[Path, int]] = [(absolute, 0)]
    directories: list[Path] = []
    files: list[Path] = []
    directory_count = 0
    total_bytes = 0
    seen_directories: set[tuple[int, int] | str] = set()

    while pending:
        directory, depth = pending.pop()
        if depth > max_depth:
            raise ValueError(f"Directory tree exceeds its depth limit: {directory}")
        info = directory.lstat()
        if path_is_link(directory, info.st_mode, file_info=info) or not stat.S_ISDIR(
            info.st_mode
        ):
            raise ValueError(f"Directory tree contains an unsafe directory: {directory}")
        identity: tuple[int, int] | str
        if info.st_ino:
            identity = (info.st_dev, info.st_ino)
        else:
            identity = os.path.normcase(os.fspath(directory))
        if identity in seen_directories:
            raise ValueError(f"Directory tree contains a cycle: {directory}")
        seen_directories.add(identity)
        directories.append(directory)
        directory_count += 1
        if directory_count > max_directories:
            raise ValueError("Directory tree exceeds its directory limit")

        children: list[tuple[Path, os.stat_result]] = []
        with os.scandir(directory) as entries:
            for entry in entries:
                child = directory / entry.name
                child_info = entry.stat(follow_symlinks=False)
                if path_is_link(child, child_info.st_mode, file_info=child_info):
                    raise ValueError(f"Directory tree contains a link: {child}")
                children.append((child, child_info))
        for child, child_info in sorted(children, key=lambda item: item[0].name):
            if stat.S_ISDIR(child_info.st_mode):
                pending.append((child, depth + 1))
            elif stat.S_ISREG(child_info.st_mode):
                files.append(child)
                total_bytes += child_info.st_size
                if len(files) > max_files:
                    raise ValueError("Directory tree exceeds its file limit")
                if total_bytes > max_total_bytes:
                    raise ValueError("Directory tree exceeds its byte limit")
            else:
                raise ValueError(f"Directory tree contains a special file: {child}")
    def relative_name(item: Path) -> str:
        return item.relative_to(absolute).as_posix()

    return sorted(directories, key=relative_name), sorted(files, key=relative_name)


def walk_regular_files_no_links(
    root: Path,
    *,
    max_files: int = DEFAULT_MAX_WALK_FILES,
    max_directories: int = DEFAULT_MAX_WALK_DIRECTORIES,
    max_total_bytes: int = DEFAULT_MAX_WALK_BYTES,
    max_depth: int = DEFAULT_MAX_WALK_DEPTH,
) -> list[Path]:
    """Return only files from a bounded, link-free recursive inventory."""

    _directories, files = walk_directory_tree_no_links(
        root,
        max_files=max_files,
        max_directories=max_directories,
        max_total_bytes=max_total_bytes,
        max_depth=max_depth,
    )
    return files


def _require_owned_directory(path: Path) -> Path:
    absolute = reject_link_components(path)
    try:
        info = absolute.stat()
    except FileNotFoundError as error:
        raise ValueError(f"Private directory is missing: {absolute}") from error
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"Private directory is unsafe: {absolute}")
    if os.name != "nt" and hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise ValueError(f"Private directory owner is unsafe: {absolute}")
    return absolute


def create_private_directory(path: Path) -> Path:
    """Atomically claim a new owner-only directory at an unlinked path."""

    absolute = absolute_lexical_path(path)
    ensure_safe_directory(absolute.parent)
    try:
        os.mkdir(absolute, 0o700)
    except FileExistsError as error:
        reject_link_components(absolute)
        raise ValueError(f"Private directory already exists: {absolute}") from error
    try:
        os.chmod(absolute, 0o700)
        return _require_owned_directory(absolute)
    except Exception:
        try:
            absolute.rmdir()
        except OSError:
            pass
        raise


def require_private_directory(path: Path) -> Path:
    """Validate an existing owner-controlled directory."""

    return _require_owned_directory(path)


def ensure_private_directory(path: Path) -> Path:
    """Create or tighten an owner-controlled directory without following links."""

    absolute = ensure_safe_directory(path, 0o700)
    absolute = _require_owned_directory(absolute)
    os.chmod(absolute, 0o700)
    return _require_owned_directory(absolute)
