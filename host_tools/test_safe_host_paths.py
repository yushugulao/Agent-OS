#!/usr/bin/env python3
"""Regression tests for link-safe host acceptance paths."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import evaluation_bundle
import evidence_delivery_contract
import safe_host_paths


HOST_TOOLS = Path(__file__).resolve().parent


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


def _windows_command(*arguments: str) -> subprocess.CompletedProcess[str]:
    import ctypes

    kernel32 = ctypes.CDLL("Kernel32.dll")
    get_system_directory = kernel32.GetSystemDirectoryW
    get_system_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    get_system_directory.restype = ctypes.c_uint32
    system_directory = ctypes.create_unicode_buffer(32_768)
    length = int(get_system_directory(system_directory, len(system_directory)))
    if length <= 0 or length >= len(system_directory):
        raise OSError("cannot locate the native Windows system directory")
    command = system_directory.value.replace(chr(92), "/") + "/cmd.exe"
    previous = os.environ.get("MSYS2_ARG_CONV_EXCL")
    os.environ["MSYS2_ARG_CONV_EXCL"] = "*"
    try:
        return subprocess.run(
            [command, "/d", "/c", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        if previous is None:
            os.environ.pop("MSYS2_ARG_CONV_EXCL", None)
        else:
            os.environ["MSYS2_ARG_CONV_EXCL"] = previous


def _create_junction(target: Path, link: Path) -> bool:
    import ctypes

    native_target = _native_windows_path(target)
    native_link = _native_windows_path(link)
    if native_target is None or native_link is None:
        return False
    completed = _windows_command("mklink", "/J", native_link, native_target)
    if completed.returncode != 0:
        return False
    kernel32 = ctypes.CDLL("Kernel32.dll")
    get_attributes = kernel32.GetFileAttributesW
    get_attributes.argtypes = [ctypes.c_wchar_p]
    get_attributes.restype = ctypes.c_uint32
    attributes = int(get_attributes(native_link))
    detected = bool(
        attributes != safe_host_paths._INVALID_FILE_ATTRIBUTES
        and attributes & safe_host_paths._FILE_ATTRIBUTE_REPARSE_POINT
    )
    if not detected:
        _remove_junction(link)
    return detected


def _remove_junction(link: Path) -> None:
    native_link = _native_windows_path(link)
    if native_link is None:
        raise AssertionError(f"cannot convert test junction path: {link}")
    completed = _windows_command("rmdir", native_link)
    if completed.returncode != 0 or os.path.lexists(link):
        raise AssertionError(f"cannot remove test junction: {link}")


class SafeHostPathTests(unittest.TestCase):
    def test_hidden_reparse_attribute_is_rejected_by_all_shared_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hidden = root / "hidden"
            hidden.mkdir()
            (hidden / "payload").write_bytes(b"payload")
            hidden_absolute = safe_host_paths.absolute_lexical_path(hidden)

            def attributes(candidate: Path) -> int:
                if safe_host_paths.absolute_lexical_path(candidate) == hidden_absolute:
                    return safe_host_paths._FILE_ATTRIBUTE_REPARSE_POINT
                return 0

            with mock.patch(
                "safe_host_paths._msys_native_file_attributes",
                side_effect=attributes,
            ):
                self.assertTrue(safe_host_paths.path_is_link(hidden))
                with self.assertRaises(ValueError):
                    safe_host_paths.reject_link_components(hidden / "payload")
                with self.assertRaises(ValueError):
                    safe_host_paths.ensure_safe_directory(hidden / "child")
                with self.assertRaises(ValueError):
                    safe_host_paths.atomic_write_bytes(hidden / "new", b"new")
                with self.assertRaises(ValueError):
                    safe_host_paths.walk_regular_files_no_links(root)
                with self.assertRaises(evaluation_bundle.BundleError):
                    evaluation_bundle._regular_files(root)
                with self.assertRaises(
                    evidence_delivery_contract.DeliveryContractError
                ):
                    evidence_delivery_contract._worktree_files(root)
            self.assertFalse((hidden / "new").exists())

    def test_atomic_write_and_bounded_walk_accept_ordinary_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            safe_host_paths.atomic_write_bytes(root / "b", b"bb", replace=False)
            safe_host_paths.atomic_write_bytes(root / "a", b"a", replace=False)
            files = safe_host_paths.walk_regular_files_no_links(
                root, max_files=2, max_directories=1, max_total_bytes=3
            )
            self.assertEqual([path.name for path in files], ["a", "b"])
            with self.assertRaises(ValueError):
                safe_host_paths.walk_regular_files_no_links(root, max_files=1)
            with self.assertRaises(ValueError):
                safe_host_paths.walk_regular_files_no_links(root, max_total_bytes=2)

    @unittest.skipUnless(
        os.name == "nt" or sys.platform == "cygwin", "requires Windows reparse points"
    )
    def test_real_windows_junction_is_rejected_without_following(self) -> None:
        with tempfile.TemporaryDirectory(dir=HOST_TOOLS) as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            (target / "payload").write_bytes(b"payload")
            link = root / "junction"
            created = _create_junction(target, link)
            if not created:
                if sys.platform == "cygwin":
                    self.fail("native MSYS test could not create a detectable junction")
                self.skipTest("runtime cannot create a detectable Windows junction")
            try:
                self.assertTrue(safe_host_paths.path_is_link(link))
                with self.assertRaises(ValueError):
                    safe_host_paths.reject_link_components(link / "payload")
                with self.assertRaises(evaluation_bundle.BundleError):
                    evaluation_bundle._regular_files(link)
                with self.assertRaises(
                    evidence_delivery_contract.DeliveryContractError
                ):
                    evidence_delivery_contract._worktree_files(link)
            finally:
                _remove_junction(link)
            self.assertEqual((target / "payload").read_bytes(), b"payload")


if __name__ == "__main__":
    unittest.main(verbosity=2)
