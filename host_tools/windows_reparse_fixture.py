#!/usr/bin/env python3
"""Host 合同测试使用的 PATH 无关 Windows junction 夹具。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


FILE_ATTRIBUTE_REPARSE_POINT = 0x400
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_CMD_META = frozenset("&|<>^%!\r\n")


def _native_windows_path(path: Path) -> str | None:
    if os.name == "nt":
        return str(path.absolute())
    if sys.platform != "cygwin":
        return None
    completed = subprocess.run(
        ["/usr/bin/cygpath", "-w", os.path.abspath(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _native_system_executable(name: str) -> str:
    import ctypes

    if not name or any(character in name for character in "\\/:"):
        raise ValueError("Windows system executable name is invalid")
    loader = getattr(ctypes, "WinDLL", ctypes.CDLL)
    kernel32 = loader("Kernel32.dll")
    get_system_directory = kernel32.GetSystemDirectoryW
    get_system_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    get_system_directory.restype = ctypes.c_uint32
    system_directory = ctypes.create_unicode_buffer(32_768)
    length = int(get_system_directory(system_directory, len(system_directory)))
    if length <= 0 or length >= len(system_directory):
        raise OSError("cannot locate the native Windows system directory")
    executable = system_directory.value + "\\" + name
    if sys.platform == "cygwin":
        return executable.replace("\\", "/")
    return executable


def _run_cmd(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    if any(any(character in value for character in _CMD_META) for value in arguments):
        raise ValueError("junction fixture path contains cmd metacharacters")
    previous = os.environ.get("MSYS2_ARG_CONV_EXCL")
    os.environ["MSYS2_ARG_CONV_EXCL"] = "*"
    try:
        return subprocess.run(
            [
                _native_system_executable("cmd.exe"),
                "/d",
                "/v:off",
                "/c",
                *arguments,
            ],
            check=False,
            capture_output=True,
        )
    finally:
        if previous is None:
            os.environ.pop("MSYS2_ARG_CONV_EXCL", None)
        else:
            os.environ["MSYS2_ARG_CONV_EXCL"] = previous


def _native_file_attributes(native_path: str) -> int:
    import ctypes

    loader = getattr(ctypes, "WinDLL", ctypes.CDLL)
    kernel32 = loader("Kernel32.dll")
    get_attributes = kernel32.GetFileAttributesW
    get_attributes.argtypes = [ctypes.c_wchar_p]
    get_attributes.restype = ctypes.c_uint32
    return int(get_attributes(native_path))


def create_directory_junction(target: Path, link: Path) -> bool:
    """创建并独立确认一个原生目录 junction。"""

    native_target = _native_windows_path(target)
    native_link = _native_windows_path(link)
    if native_target is None or native_link is None:
        return False
    if os.path.lexists(link):
        raise ValueError(f"junction fixture already exists: {link}")
    completed = _run_cmd("mklink", "/J", native_link, native_target)
    if completed.returncode != 0:
        return False
    attributes = _native_file_attributes(native_link)
    detected = bool(
        attributes != INVALID_FILE_ATTRIBUTES
        and attributes & FILE_ATTRIBUTE_REPARSE_POINT
    )
    if not detected:
        raise AssertionError(f"created junction lacks a reparse attribute: {link}")
    return True


def remove_directory_junction(link: Path) -> None:
    """仅移除已验证 junction 条目，不触碰其目标。"""

    native_link = _native_windows_path(link)
    if native_link is None:
        raise AssertionError(f"cannot convert test junction path: {link}")
    attributes = _native_file_attributes(native_link)
    if (
        attributes == INVALID_FILE_ATTRIBUTES
        or not attributes & FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise AssertionError(f"refusing to remove a non-reparse test path: {link}")
    completed = _run_cmd("rmdir", native_link)
    if completed.returncode != 0 or os.path.lexists(link):
        diagnostic = completed.stderr.decode("utf-8", errors="replace").strip()
        raise AssertionError(
            f"cannot remove test junction {link}: {diagnostic}"
        )
