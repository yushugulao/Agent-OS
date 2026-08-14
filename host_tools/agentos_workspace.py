#!/usr/bin/env python3
"""Bounded, read-only access to one explicitly configured Host workspace."""

from __future__ import annotations

import codecs
import errno
import os
from pathlib import Path, PurePosixPath
import stat
import sys

try:
    import safe_host_paths
except ModuleNotFoundError:  # pragma: no cover - package import fallback
    from . import safe_host_paths


MAX_QUERY_BYTES = 95
MAX_PREFIX_BYTES = 111
MAX_PATH_BYTES = 255
MAX_READ_LINES = 64
MAX_RESULTS = 8
MAX_PROJECTION_BYTES = 2800
MAX_FILE_BYTES = 1 << 20
MAX_SCAN_BYTES = 16 << 20
MAX_SCAN_FILES = 10_000
MAX_SCAN_DIRECTORIES = 2_000
MAX_SCAN_DEPTH = 64
MAX_SCAN_ENTRIES = 20_000
MAX_SCAN_ENTRIES_PER_DIRECTORY = 4_096
MAX_BINARY_SNIFF_BYTES = 4_096
MAX_SNIPPET_BYTES = 240

_SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".cache",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "out",
        "target",
        "venv",
    }
)


class _WorkspaceInputError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _ScanState:
    def __init__(self) -> None:
        self.files = 0
        self.directories = 0
        self.entries = 0
        self.bytes = 0
        self.truncated = False
        self.stop = False


class _OpenedRegularFile:
    def __init__(
        self,
        descriptor: int,
        before: os.stat_result,
        relative: str,
        *,
        parent_descriptor: int = -1,
        leaf: str = "",
        windows_record=None,
    ) -> None:
        self.descriptor = descriptor
        self.before = before
        self.relative = relative
        self.parent_descriptor = parent_descriptor
        self.leaf = leaf
        self.windows_record = windows_record

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1
        if self.parent_descriptor >= 0:
            os.close(self.parent_descriptor)
            self.parent_descriptor = -1


def _error(code: str) -> str:
    return f"workspace_error={code}"


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8", errors="strict"))


def _truncate_utf8(text: str, maximum: int) -> tuple[str, bool]:
    raw = text.encode("utf-8", errors="strict")
    if len(raw) <= maximum:
        return text, False
    if maximum <= 0:
        return "", True
    marker = b"..."
    if maximum <= len(marker):
        return marker[:maximum].decode("ascii"), True
    prefix = raw[: maximum - len(marker)]
    while prefix:
        try:
            return prefix.decode("utf-8") + marker.decode("ascii"), True
        except UnicodeDecodeError as error:
            prefix = prefix[: error.start]
    return marker.decode("ascii"), True


def _one_line(text: str) -> str:
    rendered = []
    for character in text:
        codepoint = ord(character)
        if character == "\t":
            rendered.append("    ")
        elif codepoint < 0x20 or codepoint == 0x7F:
            rendered.append(" ")
        else:
            rendered.append(character)
    return "".join(rendered)


def _validate_text(value: object, label: str, maximum: int, *, empty: bool) -> str:
    if not isinstance(value, str):
        raise _WorkspaceInputError(f"invalid_{label}")
    try:
        encoded_length = _utf8_size(value)
    except UnicodeEncodeError as error:
        raise _WorkspaceInputError(f"invalid_{label}") from error
    # Public schema maxLength is measured in Unicode code points.  The second
    # bound keeps encoded work explicit while allowing every valid scalar.
    if (
        (not empty and not value)
        or len(value) > maximum
        or encoded_length > 4 * maximum
    ):
        raise _WorkspaceInputError(f"invalid_{label}")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise _WorkspaceInputError(f"invalid_{label}")
    return value


def _validate_relative_path(
    value: object,
    label: str,
    maximum: int,
    *,
    empty: bool,
    trailing_slash: bool,
) -> str:
    text = _validate_text(value, label, maximum, empty=empty)
    if not text:
        return ""
    if "\\" in text or ":" in text or "\x00" in text or text.startswith("/"):
        raise _WorkspaceInputError(f"invalid_{label}")
    if len(text) >= 2 and text[0].isalpha() and text[1] == ":":
        raise _WorkspaceInputError(f"invalid_{label}")
    body = text[:-1] if trailing_slash and text.endswith("/") else text
    if not body or any(part in ("", ".", "..") for part in body.split("/")):
        raise _WorkspaceInputError(f"invalid_{label}")
    if PurePosixPath(body).is_absolute() or PurePosixPath(body).as_posix() != body:
        raise _WorkspaceInputError(f"invalid_{label}")
    return text


def _text_lines(text: str) -> list[str]:
    if not text:
        return []
    return text.splitlines()


def _identity_stamp(info: os.stat_result) -> tuple[int, int, int]:
    identity_time = (
        info.st_birthtime_ns
        if os.name == "nt" and hasattr(info, "st_birthtime_ns")
        else info.st_ctime_ns
    )
    return (info.st_dev, info.st_ino, identity_time)


def _file_stamp(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (*_identity_stamp(info), info.st_size, info.st_mtime_ns)


def _read_fd_bounded(descriptor: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_fd_up_to(descriptor: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _prefix_is_binary(data: bytes, *, complete: bool) -> bool:
    if b"\x00" in data:
        return True
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        decoder.decode(data, final=complete)
    except UnicodeDecodeError:
        return True
    return False


if os.name == "posix" and sys.platform.startswith("linux"):
    import ctypes

    class _OpenHow(ctypes.Structure):
        _fields_ = (
            ("flags", ctypes.c_uint64),
            ("mode", ctypes.c_uint64),
            ("resolve", ctypes.c_uint64),
        )

    _LIBC = ctypes.CDLL(None, use_errno=True)
    _LIBC.syscall.restype = ctypes.c_long
    _OPENAT2_NR_437_ARCHES = frozenset(
        {
            "aarch64",
            "arm64",
            "armv7l",
            "i386",
            "i686",
            "ppc64le",
            "riscv64",
            "s390x",
            "x86_64",
        }
    )
    _SYS_OPENAT2 = 437 if os.uname().machine.lower() in _OPENAT2_NR_437_ARCHES else None
    _RESOLVE_NO_XDEV = 0x01
    _RESOLVE_NO_MAGICLINKS = 0x02
    _RESOLVE_NO_SYMLINKS = 0x04
    _RESOLVE_BENEATH = 0x08


    def _linux_openat2(
        root_descriptor: int,
        relative: str,
        flags: int,
        *,
        allow_xdev: bool = False,
    ) -> int:
        if _SYS_OPENAT2 is None:
            raise OSError(errno.ENOSYS, "openat2 syscall number is unknown")
        how = _OpenHow(
            flags=flags,
            mode=0,
            resolve=(
                (0 if allow_xdev else _RESOLVE_NO_XDEV)
                | _RESOLVE_BENEATH
                | _RESOLVE_NO_MAGICLINKS
                | _RESOLVE_NO_SYMLINKS
            ),
        )
        encoded = os.fsencode(relative or ".")
        descriptor = int(
            _LIBC.syscall(
                _SYS_OPENAT2,
                root_descriptor,
                ctypes.c_char_p(encoded),
                ctypes.byref(how),
                ctypes.sizeof(how),
            )
        )
        if descriptor < 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number), relative)
        return descriptor


else:

    def _linux_openat2(
        root_descriptor: int,
        relative: str,
        flags: int,
        *,
        allow_xdev: bool = False,
    ) -> int:
        del root_descriptor, relative, flags, allow_xdev
        raise OSError(errno.ENOSYS, "atomic beneath-root open is unavailable")


if os.name == "nt":  # pragma: no branch - definitions are platform-specific
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = (
            ("attributes", wintypes.DWORD),
            ("creation_time", _FILETIME),
            ("access_time", _FILETIME),
            ("write_time", _FILETIME),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        )

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CREATE_FILE = _KERNEL32.CreateFileW
    _CREATE_FILE.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _CREATE_FILE.restype = wintypes.HANDLE
    _CLOSE_HANDLE = _KERNEL32.CloseHandle
    _CLOSE_HANDLE.argtypes = (wintypes.HANDLE,)
    _CLOSE_HANDLE.restype = wintypes.BOOL
    _GET_FILE_INFORMATION = _KERNEL32.GetFileInformationByHandle
    _GET_FILE_INFORMATION.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    )
    _GET_FILE_INFORMATION.restype = wintypes.BOOL
    _GET_FINAL_PATH = _KERNEL32.GetFinalPathNameByHandleW
    _GET_FINAL_PATH.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    _GET_FINAL_PATH.restype = wintypes.DWORD

    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _GENERIC_READ = 0x80000000
    _FILE_READ_ATTRIBUTES = 0x0080
    _FILE_SHARE_ALL = 0x0001 | 0x0002 | 0x0004
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_DIRECTORY = 0x0010
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000


    def _windows_open_handle(path: Path, *, read: bool):
        access = _GENERIC_READ if read else _FILE_READ_ATTRIBUTES
        handle = _CREATE_FILE(
            os.fspath(path),
            access,
            _FILE_SHARE_ALL,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle


    def _windows_close_handle(handle) -> None:
        if handle not in (None, _INVALID_HANDLE_VALUE):
            _CLOSE_HANDLE(handle)


    def _windows_normalize_final_path(value: str) -> str:
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return os.path.normcase(os.path.normpath(value))


    def _windows_handle_record(handle) -> tuple[tuple[int, int], tuple[int, int, int], int, str]:
        information = _BY_HANDLE_FILE_INFORMATION()
        if not _GET_FILE_INFORMATION(handle, ctypes.byref(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        capacity = 512
        while True:
            buffer = ctypes.create_unicode_buffer(capacity)
            length = int(_GET_FINAL_PATH(handle, buffer, capacity, 0))
            if not length:
                raise ctypes.WinError(ctypes.get_last_error())
            if length < capacity:
                final_path = _windows_normalize_final_path(buffer.value)
                break
            capacity = length + 1
        identity = (
            int(information.volume_serial),
            (int(information.index_high) << 32) | int(information.index_low),
        )
        content = (
            (int(information.creation_time.high) << 32) | int(information.creation_time.low),
            (int(information.size_high) << 32) | int(information.size_low),
            (int(information.write_time.high) << 32) | int(information.write_time.low),
        )
        return identity, content, int(information.attributes), final_path


def _windows_path_within(candidate: str, root: str) -> bool:
    try:
        return os.path.commonpath((candidate, root)) == root
    except (OSError, ValueError):
        return False


def _windows_same_object(left, right) -> bool:
    """Compare stable identity/type/path without treating directory mtime as identity."""
    return (
        left[0] == right[0]
        and (left[2] & 0x0410) == (right[2] & 0x0410)
        and left[3] == right[3]
    )


class WorkspaceReader:
    """Read a pinned workspace without granting access outside its root."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        if not isinstance(root, (str, os.PathLike)):
            raise TypeError("workspace root must be path-like")
        self._root_descriptor = -1
        self._root_handle = None
        try:
            configured = safe_host_paths.absolute_lexical_path(Path(root).expanduser())
            before = configured.lstat()
            if safe_host_paths.path_is_link(configured, before.st_mode, file_info=before):
                raise ValueError("linked workspace root")
            if os.name == "nt":
                handle = _windows_open_handle(configured, read=False)
                try:
                    record = _windows_handle_record(handle)
                    _identity, _content, attributes, final_path = record
                    if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                        raise ValueError("reparse workspace root")
                    if not attributes & _FILE_ATTRIBUTE_DIRECTORY:
                        raise ValueError("workspace root is not a directory")
                    after = configured.lstat()
                    safe_host_paths.reject_link_components(configured)
                    checked_resolved = configured.resolve(strict=True)
                    resolved_info = checked_resolved.lstat()
                    if _file_stamp(before) != _file_stamp(after):
                        raise ValueError("workspace root changed")
                    if _identity_stamp(resolved_info) != _identity_stamp(after):
                        raise ValueError("workspace root resolution changed")
                    if before.st_ino and before.st_ino != record[0][1]:
                        raise ValueError("workspace root identity changed")
                    if final_path != _windows_normalize_final_path(
                        os.fspath(checked_resolved)
                    ):
                        raise ValueError("workspace root escaped during open")
                    probe = _windows_open_handle(configured, read=False)
                    try:
                        if _windows_handle_record(probe) != record:
                            raise ValueError("workspace root changed")
                    finally:
                        _windows_close_handle(probe)
                except Exception:
                    _windows_close_handle(handle)
                    raise
                self._root_handle = handle
                self._root_windows_record = record
                self._root_final_path = final_path
                resolved = Path(final_path)
            else:
                flags = os.O_RDONLY
                for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"):
                    flags |= int(getattr(os, name, 0))
                system_root = os.open(os.path.sep, flags)
                try:
                    root_relative = os.path.relpath(configured, os.path.sep)
                    if root_relative == os.pardir or root_relative.startswith(
                        os.pardir + os.path.sep
                    ):
                        raise ValueError("workspace root is not beneath filesystem root")
                    # Resolve the configured root in one kernel operation from
                    # the trusted filesystem-root fd. Mount crossings are
                    # allowed only while acquiring this initial capability;
                    # traversal inside it uses RESOLVE_NO_XDEV.
                    descriptor = _linux_openat2(
                        system_root,
                        root_relative,
                        flags,
                        allow_xdev=True,
                    )
                finally:
                    os.close(system_root)
                try:
                    opened = os.fstat(descriptor)
                    after = configured.lstat()
                    safe_host_paths.reject_link_components(configured)
                    resolved = configured.resolve(strict=True)
                    resolved_info = resolved.lstat()
                    if (
                        not stat.S_ISDIR(opened.st_mode)
                        or _identity_stamp(before) != _identity_stamp(opened)
                        or _identity_stamp(after) != _identity_stamp(opened)
                        or _identity_stamp(resolved_info) != _identity_stamp(opened)
                    ):
                        raise ValueError("workspace root changed")
                    # No pathname fallback is permitted: platforms/kernels
                    # without an atomic beneath-root primitive fail closed here.
                    atomic_probe = _linux_openat2(descriptor, ".", flags)
                    try:
                        if _identity_stamp(os.fstat(atomic_probe)) != _identity_stamp(opened):
                            raise ValueError("atomic workspace root probe changed")
                    finally:
                        os.close(atomic_probe)
                except Exception:
                    os.close(descriptor)
                    raise
                self._root_descriptor = descriptor
        except (OSError, ValueError) as error:
            raise ValueError("workspace root is missing or invalid") from error
        self.root = resolved
        self._access_root = configured

    def close(self) -> None:
        if self._root_descriptor >= 0:
            os.close(self._root_descriptor)
            self._root_descriptor = -1
        if self._root_handle is not None:
            _windows_close_handle(self._root_handle)
            self._root_handle = None

    def __del__(self) -> None:  # pragma: no cover - deterministic close is preferred
        try:
            self.close()
        except Exception:
            pass

    def _lexical_candidate(self, relative: str) -> Path:
        body = relative[:-1] if relative.endswith("/") else relative
        candidate = self._access_root.joinpath(*body.split("/")) if body else self._access_root
        try:
            candidate.relative_to(self._access_root)
        except ValueError as error:
            raise _WorkspaceInputError("unsafe_path") from error
        return candidate

    def _reject_linked_prefix(self, relative: str) -> None:
        if not relative:
            return
        try:
            safe_host_paths.reject_link_components(self._lexical_candidate(relative))
        except (OSError, ValueError) as error:
            raise _WorkspaceInputError("unsafe_path") from error

    @staticmethod
    def _relative_join(directory: str, name: str) -> str:
        return f"{directory}/{name}" if directory else name

    @staticmethod
    def _search_base(prefix: str) -> str:
        if not prefix:
            return ""
        if prefix.endswith("/"):
            return prefix[:-1]
        parent, separator, _name = prefix.rpartition("/")
        return parent if separator else ""

    def _open_posix_directory(self, relative: str) -> int:
        if self._root_descriptor < 0:
            raise _WorkspaceInputError("io_error")
        flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= int(getattr(os, name, 0))
        descriptor = -1
        try:
            descriptor = _linux_openat2(self._root_descriptor, relative, flags)
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                raise _WorkspaceInputError("not_file")
            return descriptor
        except _WorkspaceInputError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except FileNotFoundError as error:
            if descriptor >= 0:
                os.close(descriptor)
            raise _WorkspaceInputError("not_found") from error
        except OSError as error:
            if descriptor >= 0:
                os.close(descriptor)
            if error.errno in (errno.ELOOP, errno.EXDEV):
                code = "unsafe_path"
            elif error.errno == errno.ENOENT:
                code = "not_found"
            elif error.errno == errno.ENOTDIR:
                code = "not_file"
            else:
                code = "io_error"
            raise _WorkspaceInputError(code) from error

    def _validate_windows_root_path(self) -> None:
        handle = None
        try:
            safe_host_paths.reject_link_components(self._access_root)
            handle = _windows_open_handle(self._access_root, read=False)
            record = _windows_handle_record(handle)
            if not _windows_same_object(record, self._root_windows_record):
                raise _WorkspaceInputError("unsafe_path")
        except _WorkspaceInputError:
            raise
        except (OSError, ValueError) as error:
            raise _WorkspaceInputError("unsafe_path") from error
        finally:
            if handle is not None:
                _windows_close_handle(handle)

    def _open_windows_directory(self, relative: str):
        # CPython exposes neither openat nor handle-relative scandir on Windows.
        # Enumeration is therefore path based (and strictly entry bounded), but
        # no name or byte is projected until its leaf is opened with
        # OPEN_REPARSE_POINT, proven non-reparse, and its handle-final path is
        # under the still-identical pinned root.  A transient reparse can at
        # worst make this operation incomplete; it cannot authorize reading an
        # outside handle.  The opened handle, not the pathname, supplies bytes.
        candidate = self._lexical_candidate(relative)
        handle = None
        try:
            self._validate_windows_root_path()
            safe_host_paths.reject_link_components(candidate)
            handle = _windows_open_handle(candidate, read=False)
            record = _windows_handle_record(handle)
            _identity, _content, attributes, final_path = record
            if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise _WorkspaceInputError("unsafe_path")
            if not attributes & _FILE_ATTRIBUTE_DIRECTORY:
                raise _WorkspaceInputError("not_file")
            if not _windows_path_within(final_path, self._root_final_path):
                raise _WorkspaceInputError("unsafe_path")
            self._validate_windows_root_path()
            return handle, record
        except _WorkspaceInputError:
            if handle is not None:
                _windows_close_handle(handle)
            raise
        except FileNotFoundError as error:
            if handle is not None:
                _windows_close_handle(handle)
            raise _WorkspaceInputError("not_found") from error
        except (OSError, ValueError) as error:
            if handle is not None:
                _windows_close_handle(handle)
            raise _WorkspaceInputError("io_error") from error

    def _verify_windows_named_handle(self, candidate: Path, expected) -> None:
        probe = None
        try:
            safe_host_paths.reject_link_components(candidate)
            probe = _windows_open_handle(candidate, read=False)
            actual = _windows_handle_record(probe)
            if actual != expected:
                raise _WorkspaceInputError("io_error")
            self._validate_windows_root_path()
        except _WorkspaceInputError:
            raise
        except (OSError, ValueError) as error:
            raise _WorkspaceInputError("io_error") from error
        finally:
            if probe is not None:
                _windows_close_handle(probe)

    def _open_posix_regular_descriptor(
        self, relative: str
    ) -> tuple[int, os.stat_result]:
        flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= int(getattr(os, name, 0))
        descriptor = -1
        try:
            descriptor = _linux_openat2(self._root_descriptor, relative, flags)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise _WorkspaceInputError("not_file")
            return descriptor, info
        except _WorkspaceInputError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError as error:
            if descriptor >= 0:
                os.close(descriptor)
            if error.errno in (errno.ELOOP, errno.EXDEV):
                code = "unsafe_path"
            elif error.errno == errno.ENOENT:
                code = "not_found"
            elif error.errno == errno.ENOTDIR:
                code = "not_file"
            else:
                code = "io_error"
            raise _WorkspaceInputError(code) from error

    def _open_regular_file(self, relative: str) -> _OpenedRegularFile:
        # The authorization boundary is an ordinary directory entry reachable
        # beneath the pinned root. A hardlink inside the workspace is therefore
        # readable even when the same inode also has a name outside it: neither
        # POSIX nor Windows records a unique "origin path" on the file handle.
        # Native POSIX O_NOFOLLOW is authoritative.  This additional probe is
        # needed for POSIX compatibility layers whose Windows reparse metadata
        # is visible only through safe_host_paths.
        try:
            safe_host_paths.reject_link_components(self._lexical_candidate(relative))
        except (OSError, ValueError) as error:
            raise _WorkspaceInputError("unsafe_path") from error
        if os.name == "nt":
            candidate = self._lexical_candidate(relative)
            handle = None
            try:
                self._validate_windows_root_path()
                safe_host_paths.reject_link_components(candidate)
                handle = _windows_open_handle(candidate, read=True)
                record = _windows_handle_record(handle)
                _identity, content, attributes, final_path = record
                if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                    raise _WorkspaceInputError("unsafe_path")
                if attributes & _FILE_ATTRIBUTE_DIRECTORY:
                    raise _WorkspaceInputError("not_file")
                if not _windows_path_within(final_path, self._root_final_path):
                    raise _WorkspaceInputError("unsafe_path")
                self._verify_windows_named_handle(candidate, record)
                descriptor = msvcrt.open_osfhandle(
                    int(handle), os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
                )
                handle = None
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode) or before.st_size != content[1]:
                    os.close(descriptor)
                    raise _WorkspaceInputError("io_error")
                return _OpenedRegularFile(
                    descriptor, before, relative, windows_record=record
                )
            except _WorkspaceInputError:
                if handle is not None:
                    _windows_close_handle(handle)
                raise
            except FileNotFoundError as error:
                if handle is not None:
                    _windows_close_handle(handle)
                raise _WorkspaceInputError("not_found") from error
            except (OSError, ValueError) as error:
                if handle is not None:
                    _windows_close_handle(handle)
                raise _WorkspaceInputError("io_error") from error

        parent_relative = relative.rpartition("/")[0]
        # The preflight is not the security decision; it keeps errors precise.
        # The subsequent single openat2 resolves the complete path atomically
        # from the pinned root and defeats a parent renamed after this return.
        parent_descriptor = self._open_posix_directory(parent_relative)
        os.close(parent_descriptor)
        descriptor = -1
        try:
            descriptor, before = self._open_posix_regular_descriptor(relative)
            probe, named = self._open_posix_regular_descriptor(relative)
            os.close(probe)
            if _file_stamp(before) != _file_stamp(named):
                raise _WorkspaceInputError("io_error")
            return _OpenedRegularFile(descriptor, before, relative)
        except _WorkspaceInputError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except (OSError, ValueError) as error:
            if descriptor >= 0:
                os.close(descriptor)
            raise _WorkspaceInputError("io_error") from error

    def _verify_opened_file(self, opened: _OpenedRegularFile) -> None:
        try:
            after = os.fstat(opened.descriptor)
            if not stat.S_ISREG(after.st_mode) or _file_stamp(after) != _file_stamp(opened.before):
                raise _WorkspaceInputError("io_error")
            if os.name == "nt":
                handle = msvcrt.get_osfhandle(opened.descriptor)
                if _windows_handle_record(handle) != opened.windows_record:
                    raise _WorkspaceInputError("io_error")
                self._verify_windows_named_handle(
                    self._lexical_candidate(opened.relative), opened.windows_record
                )
            else:
                probe, named = self._open_posix_regular_descriptor(opened.relative)
                os.close(probe)
                if _file_stamp(named) != _file_stamp(opened.before):
                    raise _WorkspaceInputError("io_error")
        except _WorkspaceInputError:
            raise
        except (OSError, ValueError) as error:
            raise _WorkspaceInputError("io_error") from error

    def _read_opened_file(self, opened: _OpenedRegularFile) -> bytes:
        try:
            data = _read_fd_bounded(opened.descriptor, MAX_FILE_BYTES)
            self._verify_opened_file(opened)
        except _WorkspaceInputError:
            raise
        except (OSError, ValueError) as error:
            raise _WorkspaceInputError("io_error") from error
        if len(data) != opened.before.st_size or len(data) > MAX_FILE_BYTES:
            raise _WorkspaceInputError("io_error")
        return data

    def _read_search_text(
        self, opened: _OpenedRegularFile, state: _ScanState
    ) -> str | None:
        size = opened.before.st_size
        sniff_size = min(size, MAX_BINARY_SNIFF_BYTES)
        if state.bytes + sniff_size > MAX_SCAN_BYTES:
            state.truncated = state.stop = True
            self._verify_opened_file(opened)
            return None

        try:
            prefix = _read_fd_up_to(opened.descriptor, sniff_size)
        except OSError as error:
            raise _WorkspaceInputError("io_error") from error
        state.bytes += len(prefix)
        if len(prefix) != sniff_size:
            self._verify_opened_file(opened)
            raise _WorkspaceInputError("io_error")
        if _prefix_is_binary(prefix, complete=sniff_size == size):
            self._verify_opened_file(opened)
            return None

        if size > MAX_FILE_BYTES:
            state.truncated = True
            self._verify_opened_file(opened)
            return None
        remaining = size - sniff_size
        if state.bytes + remaining > MAX_SCAN_BYTES:
            state.truncated = state.stop = True
            self._verify_opened_file(opened)
            return None
        try:
            tail = _read_fd_up_to(opened.descriptor, remaining)
        except OSError as error:
            raise _WorkspaceInputError("io_error") from error
        state.bytes += len(tail)
        self._verify_opened_file(opened)
        if len(tail) != remaining:
            raise _WorkspaceInputError("io_error")
        try:
            return (prefix + tail).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None

    def _bounded_directory_entries(self, relative: str, state: _ScanState):
        directory_handle = None
        directory_record = None
        descriptor = -1
        snapshots: list[tuple[str, os.stat_result, bool]] = []
        overflow = False
        try:
            if os.name == "nt":
                directory_handle, directory_record = self._open_windows_directory(relative)
                scan_target = self._lexical_candidate(relative)
            else:
                descriptor = self._open_posix_directory(relative)
                scan_target = descriptor
            with os.scandir(scan_target) as iterator:
                for entry in iterator:
                    if (
                        len(snapshots) >= MAX_SCAN_ENTRIES_PER_DIRECTORY
                        or state.entries >= MAX_SCAN_ENTRIES
                    ):
                        overflow = True
                        break
                    state.entries += 1
                    try:
                        info = entry.stat(follow_symlinks=False)
                        child = self._lexical_candidate(self._relative_join(relative, entry.name))
                        linked = safe_host_paths.path_is_link(
                            child, info.st_mode, file_info=info
                        )
                    except (OSError, ValueError):
                        state.truncated = True
                        continue
                    snapshots.append((entry.name, info, linked))
            if os.name == "nt":
                if _windows_handle_record(directory_handle) != directory_record:
                    raise _WorkspaceInputError("io_error")
                probe = None
                try:
                    probe = _windows_open_handle(self._lexical_candidate(relative), read=False)
                    if _windows_handle_record(probe) != directory_record:
                        raise _WorkspaceInputError("io_error")
                finally:
                    if probe is not None:
                        _windows_close_handle(probe)
        except _WorkspaceInputError:
            raise
        except (OSError, ValueError) as error:
            raise _WorkspaceInputError("io_error") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if directory_handle is not None:
                _windows_close_handle(directory_handle)
        if overflow:
            # We inspect one sentinel but process none of an oversized directory,
            # so filesystem enumeration order cannot choose a visible subset.
            state.truncated = state.stop = True
            return []
        snapshots.sort(key=lambda item: os.fsencode(item[0]))
        return snapshots

    def _walk_files(self, state: _ScanState, base: str, prefix: str):
        base_depth = len(base.split("/")) if base else 0

        def directory_can_match(relative: str) -> bool:
            if not prefix:
                return True
            subtree = relative + "/"
            return subtree.startswith(prefix) or prefix.startswith(subtree)

        def visit(directory: str, depth: int, *, initial: bool = False):
            if state.stop:
                return
            if depth > MAX_SCAN_DEPTH:
                state.truncated = True
                return
            if state.directories >= MAX_SCAN_DIRECTORIES:
                state.truncated = state.stop = True
                return
            state.directories += 1
            try:
                entries = self._bounded_directory_entries(directory, state)
            except _WorkspaceInputError as error:
                if initial:
                    raise
                state.truncated = True
                return
            for name, info, linked in entries:
                if state.stop:
                    return
                if linked:
                    continue
                relative = self._relative_join(directory, name)
                if stat.S_ISDIR(info.st_mode):
                    if (
                        name.casefold() not in _SKIPPED_DIRECTORIES
                        and directory_can_match(relative)
                    ):
                        yield from visit(relative, depth + 1)
                elif stat.S_ISREG(info.st_mode):
                    if prefix and not relative.startswith(prefix):
                        continue
                    if state.files >= MAX_SCAN_FILES:
                        state.truncated = state.stop = True
                        return
                    state.files += 1
                    try:
                        _validate_relative_path(
                            relative,
                            "path",
                            MAX_PATH_BYTES,
                            empty=False,
                            trailing_slash=False,
                        )
                    except _WorkspaceInputError:
                        state.truncated = True
                        continue
                    yield relative

        yield from visit(base, base_depth, initial=True)

    @staticmethod
    def _render_search(
        query: str,
        prefix: str,
        matches: list[tuple[str, str, int, str]],
        truncated: bool,
    ) -> str:
        visible = list(matches)
        snippet_limit = MAX_SNIPPET_BYTES
        projection_truncated = truncated
        while True:
            rendered_matches: list[tuple[str, str, int, str]] = []
            shortened = False
            for kind, path, line, snippet in visible:
                compact, did_shorten = _truncate_utf8(_one_line(snippet), snippet_limit)
                shortened |= did_shorten
                rendered_matches.append((kind, path, line, compact))
            effective_truncated = projection_truncated or shortened
            lines = [
                "workspace_search",
                "content_untrusted=1",
                f"query={query}",
                f"path_prefix={prefix}",
                f"match_count={len(rendered_matches)}",
                f"truncated={int(effective_truncated)}",
            ]
            for index, (kind, path, line, snippet) in enumerate(rendered_matches, 1):
                lines.extend(
                    (
                        f"match[{index}].kind={kind}",
                        f"match[{index}].path={path}",
                        f"match[{index}].line={line}",
                        f"match[{index}].snippet={snippet}",
                    )
                )
            projection = "\n".join(lines)
            if _utf8_size(projection) <= MAX_PROJECTION_BYTES:
                return projection
            projection_truncated = True
            if snippet_limit:
                snippet_limit = max(0, snippet_limit - 32)
            elif visible:
                visible.pop()
            else:
                return _error("projection_too_large")

    def search_files(self, query: str, path_prefix: str | None = "") -> str:
        try:
            query = _validate_text(query, "query", MAX_QUERY_BYTES, empty=True)
            if path_prefix is None:
                path_prefix = ""
            prefix = _validate_relative_path(
                path_prefix,
                "prefix",
                MAX_PREFIX_BYTES,
                empty=True,
                trailing_slash=True,
            )
            self._reject_linked_prefix(prefix)
        except _WorkspaceInputError as error:
            return _error(error.code)

        state = _ScanState()
        matches: list[tuple[str, str, int, str]] = []
        folded_query = query.casefold()

        def append_match(item: tuple[str, str, int, str]) -> bool:
            if len(matches) >= MAX_RESULTS:
                state.truncated = state.stop = True
                return False
            matches.append(item)
            return True

        try:
            for relative in self._walk_files(
                state, self._search_base(prefix), prefix
            ):
                opened = None
                preview: tuple[str, str, int, str] | None = None
                path_preview = (
                    ("path", relative, 0, relative)
                    if query and folded_query in relative.casefold()
                    else None
                )
                try:
                    opened = self._open_regular_file(relative)
                    if not query:
                        self._verify_opened_file(opened)
                        preview = ("file", relative, 0, "file")
                    else:
                        text = self._read_search_text(opened, state)
                        if text is not None:
                            for line_number, line in enumerate(_text_lines(text), 1):
                                if folded_query in line.casefold():
                                    snippet, shortened = _truncate_utf8(
                                        _one_line(line), MAX_SNIPPET_BYTES
                                    )
                                    state.truncated |= shortened
                                    preview = (
                                        "content",
                                        relative,
                                        line_number,
                                        snippet,
                                    )
                                    break
                        if preview is None:
                            preview = path_preview
                except _WorkspaceInputError:
                    state.truncated = True
                    if opened is not None and preview is None:
                        preview = path_preview
                finally:
                    if opened is not None:
                        opened.close()
                if preview is not None:
                    append_match(preview)
                if state.stop:
                    break
        except _WorkspaceInputError as error:
            if error.code not in ("not_found", "not_file"):
                return _error(error.code)

        if not matches and not state.truncated:
            return _error("no_matches")
        return self._render_search(query, prefix, matches, state.truncated)

    def read_file(self, path: str, start_line: int, max_lines: int) -> str:
        if type(start_line) is not int or type(max_lines) is not int:
            return _error("invalid_line_range")
        opened = None
        try:
            relative = _validate_relative_path(
                path,
                "path",
                MAX_PATH_BYTES,
                empty=False,
                trailing_slash=False,
            )
            if start_line < 1 or start_line > 0xFFFFFFFF or not 1 <= max_lines <= MAX_READ_LINES:
                raise _WorkspaceInputError("invalid_line_range")
            opened = self._open_regular_file(relative)
            if opened.before.st_size > MAX_FILE_BYTES:
                return _error("file_too_large")
            data = self._read_opened_file(opened)
        except _WorkspaceInputError as error:
            return _error(error.code)
        except (OSError, ValueError):
            return _error("io_error")
        finally:
            if opened is not None:
                opened.close()
        if b"\x00" in data:
            return _error("binary_file")
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return _error("binary_file")
        lines = _text_lines(text)
        if start_line > len(lines):
            return _error("start_line_out_of_range")
        selected = lines[start_line - 1 : start_line - 1 + max_lines]
        while selected:
            end_line = start_line + len(selected) - 1
            has_more = end_line < len(lines)
            projection_lines = [
                "workspace_read",
                "content_untrusted=1",
                f"path={relative}",
                f"start_line={start_line}",
                f"end_line={end_line}",
                f"total_lines={len(lines)}",
                f"returned_lines={len(selected)}",
                f"has_more={int(has_more)}",
                f"next_start_line={end_line + 1 if has_more else 0}",
                f"content={selected[0]}",
            ]
            projection_lines.extend(selected[1:])
            projection = "\n".join(projection_lines)
            if _utf8_size(projection) <= MAX_PROJECTION_BYTES:
                return projection
            selected.pop()
        return _error("line_too_large")


__all__ = [
    "MAX_FILE_BYTES",
    "MAX_PATH_BYTES",
    "MAX_PREFIX_BYTES",
    "MAX_PROJECTION_BYTES",
    "MAX_QUERY_BYTES",
    "MAX_READ_LINES",
    "MAX_RESULTS",
    "MAX_SCAN_BYTES",
    "MAX_SCAN_DIRECTORIES",
    "MAX_SCAN_ENTRIES",
    "MAX_SCAN_ENTRIES_PER_DIRECTORY",
    "MAX_SCAN_FILES",
    "WorkspaceReader",
]
