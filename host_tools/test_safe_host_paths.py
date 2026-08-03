#!/usr/bin/env python3
"""Regression tests for link-safe host acceptance paths."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import evaluation_bundle
import evidence_delivery_contract
import safe_host_paths
from windows_reparse_fixture import (
    create_directory_junction,
    remove_directory_junction,
)


HOST_TOOLS = Path(__file__).resolve().parent


class SafeHostPathTests(unittest.TestCase):
    def test_regular_read_accepts_unchanged_ordinary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.write_bytes(b"public")
            self.assertEqual(
                safe_host_paths.read_regular_file(source, maximum_bytes=6),
                b"public",
            )

    def test_nonempty_read_rejects_empty_replacement_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            replacement = root / "replacement"
            source.write_bytes(b"public")
            replacement.write_bytes(b"")
            real_require = safe_host_paths.require_regular_file

            def replace_after_validation(path, **kwargs):
                absolute = real_require(path, **kwargs)
                os.replace(replacement, source)
                return absolute

            with mock.patch(
                "safe_host_paths.require_regular_file",
                side_effect=replace_after_validation,
            ):
                with self.assertRaises(ValueError):
                    safe_host_paths.read_regular_file(
                        source, nonempty=True, maximum_bytes=6
                    )

    def test_regular_read_rejects_replacement_at_descriptor_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            replacement = root / "replacement"
            source.write_bytes(b"public")
            replacement.write_bytes(b"secret")
            real_open = os.open

            def replace_then_open(path, flags, *args, **kwargs):
                os.replace(replacement, source)
                return real_open(path, flags, *args, **kwargs)

            with mock.patch(
                "safe_host_paths.os.open", side_effect=replace_then_open
            ):
                with self.assertRaises(ValueError):
                    safe_host_paths.read_regular_file(source, maximum_bytes=6)
            self.assertEqual(source.read_bytes(), b"secret")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO support")
    def test_regular_read_rejects_fifo_replacement_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            fifo = root / "fifo"
            source.write_bytes(b"public")
            os.mkfifo(fifo)
            real_open = os.open

            def replace_then_open(path, flags, *args, **kwargs):
                source.unlink()
                fifo.rename(source)
                return real_open(path, flags, *args, **kwargs)

            with mock.patch(
                "safe_host_paths.os.open", side_effect=replace_then_open
            ):
                with self.assertRaises(ValueError):
                    safe_host_paths.read_regular_file(source, maximum_bytes=6)

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
            created = create_directory_junction(target, link)
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
                remove_directory_junction(link)
            self.assertEqual((target / "payload").read_bytes(), b"payload")


if __name__ == "__main__":
    unittest.main(verbosity=2)
