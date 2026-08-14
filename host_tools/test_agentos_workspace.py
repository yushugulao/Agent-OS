#!/usr/bin/env python3
"""Read-only Host workspace boundary tests for the Nexus file tools."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_workspace as workspace
import safe_host_paths


MAX_RESULT_BYTES = 2_800


class WorkspaceReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "workspace"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, relative: str, content: str | bytes) -> Path:
        target = self.root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8", newline="")
        else:
            target.write_bytes(content)
        return target

    def _reader(self) -> workspace.WorkspaceReader:
        return workspace.WorkspaceReader(self.root)

    def assert_bounded(self, result: object) -> str:
        self.assertIsInstance(result, str)
        text = str(result)
        self.assertLessEqual(len(text.encode("utf-8")), MAX_RESULT_BYTES)
        self.assertNotIn("\0", text)
        return text

    def assert_success(self, result: object, operation: str) -> str:
        text = self.assert_bounded(result)
        self.assertEqual(text.splitlines()[0], operation)
        self.assertIn("\ncontent_untrusted=1\n", text)
        return text

    def assert_error(self, result: object, code: str | None = None) -> str:
        text = self.assert_bounded(result)
        first = text.splitlines()[0]
        self.assertRegex(first, r"^workspace_error=[a-z][a-z0-9_]{0,63}$")
        if code is not None:
            self.assertEqual(first, f"workspace_error={code}")
        return text

    def _field(self, result: str, name: str) -> str:
        match = re.search(rf"(?m)^{re.escape(name)}=(.*)$", result)
        self.assertIsNotNone(match, f"missing {name!r} in {result!r}")
        return str(match.group(1))

    def _match_paths(self, result: str) -> list[str]:
        return re.findall(r"(?m)^match\[[0-9]+\]\.path=(.*)$", result)

    def _match_kinds(self, result: str) -> list[str]:
        return re.findall(r"(?m)^match\[[0-9]+\]\.kind=(.*)$", result)

    def _match_lines(self, result: str) -> list[str]:
        return re.findall(r"(?m)^match\[[0-9]+\]\.line=(.*)$", result)

    def test_search_finds_case_insensitive_literal_in_path_and_line(self) -> None:
        self._write("src/alpha.txt", "first\nMiXeD Needle value\nlast\n")
        self._write("src/NeedleName.md", "unrelated\n")
        self._write("other/outside.txt", "needle must stay outside the prefix\n")

        result = self.assert_success(
            self._reader().search_files("needle", "src/"), "workspace_search"
        )

        paths = self._match_paths(result)
        self.assertIn("src/alpha.txt", paths)
        self.assertIn("src/NeedleName.md", paths)
        self.assertNotIn("other/outside.txt", paths)
        self.assertIn("MiXeD Needle value", result)
        self.assertEqual(self._field(result, "truncated"), "0")

    def test_empty_query_lists_files_once_in_deterministic_order(self) -> None:
        self._write("b.txt", "b\n")
        self._write("a.txt", "a\n")
        self._write("nested/c.txt", "c\n")
        reader = self._reader()

        first = self.assert_success(reader.search_files(""), "workspace_search")
        second = self.assert_success(reader.search_files("", ""), "workspace_search")

        self.assertEqual(first, second)
        self.assertEqual(
            self._match_paths(first), ["a.txt", "b.txt", "nested/c.txt"]
        )
        self.assertEqual(self._field(first, "match_count"), "3")
        self.assertEqual(self._field(first, "truncated"), "0")

    def test_search_eight_distinct_file_boundary_is_exact(self) -> None:
        for index in range(8):
            self._write(f"src/f{index:02d}.txt", f"needle {index}\n")

        exact = self.assert_success(
            self._reader().search_files("needle", "src/"), "workspace_search"
        )
        self.assertEqual(
            self._match_paths(exact),
            [f"src/f{index:02d}.txt" for index in range(8)],
        )
        self.assertEqual(self._field(exact, "match_count"), "8")
        self.assertEqual(self._field(exact, "truncated"), "0")

        self._write("src/f08.txt", "needle 8\n")
        overflow = self.assert_success(
            self._reader().search_files("needle", "src/"), "workspace_search"
        )
        self.assertEqual(self._match_paths(overflow), self._match_paths(exact))
        self.assertEqual(self._field(overflow, "match_count"), "8")
        self.assertEqual(self._field(overflow, "truncated"), "1")

    def test_search_result_budget_is_enforced_without_splitting_utf8(self) -> None:
        for index in range(8):
            self._write(
                f"long/f{index:02d}.txt",
                "needle " + ("界" * 500) + f" {index}\n",
            )

        result = self.assert_success(
            self._reader().search_files("needle", "long/"), "workspace_search"
        )

        self.assertEqual(self._field(result, "truncated"), "1")
        self.assertGreaterEqual(len(self._match_paths(result)), 1)
        self.assertLessEqual(len(self._match_paths(result)), 8)

    def test_no_search_matches_is_a_stable_operation_error(self) -> None:
        self._write("src/file.txt", "ordinary text\n")
        self.assert_error(
            self._reader().search_files("missing literal", "src/"), "no_matches"
        )

    def test_incomplete_zero_match_search_is_not_reported_as_no_matches(self) -> None:
        self._write("src/file.txt", "ordinary text that cannot fit the scan budget\n")
        with mock.patch.object(workspace, "MAX_SCAN_BYTES", 1):
            result = self.assert_success(
                self._reader().search_files("missing", "src/"), "workspace_search"
            )

        self.assertEqual(self._field(result, "match_count"), "0")
        self.assertEqual(self._field(result, "truncated"), "1")

    def test_prefix_prunes_unrelated_files_before_all_scan_budgets(self) -> None:
        for index in range(5):
            self._write(f"aaa/noise-{index}.txt", "x" * 100)
        self._write("zzz/target.txt", "needle\n")

        with (
            mock.patch.object(workspace, "MAX_SCAN_FILES", 1),
            mock.patch.object(workspace, "MAX_SCAN_DIRECTORIES", 1),
            mock.patch.object(workspace, "MAX_SCAN_BYTES", 7),
        ):
            result = self.assert_success(
                self._reader().search_files("needle", "zzz/"), "workspace_search"
            )

        self.assertEqual(self._match_paths(result), ["zzz/target.txt"])
        self.assertEqual(self._field(result, "truncated"), "0")

        with (
            mock.patch.object(workspace, "MAX_SCAN_FILES", 1),
            mock.patch.object(workspace, "MAX_SCAN_DIRECTORIES", 2),
            mock.patch.object(workspace, "MAX_SCAN_BYTES", 7),
        ):
            no_slash = self.assert_success(
                self._reader().search_files("needle", "zzz"), "workspace_search"
            )
        self.assertEqual(self._match_paths(no_slash), ["zzz/target.txt"])
        self.assertEqual(self._field(no_slash, "truncated"), "0")

    def test_directory_entry_collection_consumes_only_limit_plus_sentinel(self) -> None:
        for index in range(6):
            self._write(f"f{index}.txt", "ordinary\n")
        real_scandir = os.scandir
        consumed = 0

        class CountingScandir:
            def __init__(self, target) -> None:
                self.inner = real_scandir(target)

            def __enter__(self):
                self.iterator = self.inner.__enter__()
                return self

            def __exit__(self, *args):
                return self.inner.__exit__(*args)

            def __iter__(self):
                return self

            def __next__(self):
                nonlocal consumed
                consumed += 1
                if consumed > 4:
                    raise AssertionError("scandir consumed beyond limit plus sentinel")
                return next(self.iterator)

        with (
            mock.patch.object(workspace, "MAX_SCAN_ENTRIES_PER_DIRECTORY", 3),
            mock.patch("agentos_workspace.os.scandir", side_effect=CountingScandir),
        ):
            result = self.assert_success(
                self._reader().search_files("missing"), "workspace_search"
            )

        self.assertEqual(consumed, 4)
        self.assertEqual(self._field(result, "match_count"), "0")
        self.assertEqual(self._field(result, "truncated"), "1")

    def test_file_and_directory_limits_make_zero_match_search_incomplete(self) -> None:
        self._write("nested/file.txt", "ordinary\n")
        for name, value in (("MAX_SCAN_FILES", 0), ("MAX_SCAN_DIRECTORIES", 0)):
            with self.subTest(limit=name), mock.patch.object(workspace, name, value):
                result = self.assert_success(
                    self._reader().search_files("missing"), "workspace_search"
                )
                self.assertEqual(self._field(result, "match_count"), "0")
                self.assertEqual(self._field(result, "truncated"), "1")

    def test_depth_limit_prunes_only_deep_branch_and_marks_result(self) -> None:
        self._write("a/deep/hidden.txt", "needle hidden\n")
        self._write("z.txt", "needle visible\n")
        with mock.patch.object(workspace, "MAX_SCAN_DEPTH", 1):
            result = self.assert_success(
                self._reader().search_files("needle"), "workspace_search"
            )

        self.assertIn("z.txt", self._match_paths(result))
        self.assertNotIn("a/deep/hidden.txt", self._match_paths(result))
        self.assertEqual(self._field(result, "truncated"), "1")

    def test_many_hits_in_one_file_do_not_hide_later_files(self) -> None:
        self._write("many.txt", "\n".join(f"needle {index}" for index in range(100)))
        self._write("next-a.txt", "needle next a\n")
        self._write("next-b.txt", "needle next b\n")
        result = self.assert_success(
            self._reader().search_files("needle"), "workspace_search"
        )
        self.assertEqual(
            self._match_paths(result), ["many.txt", "next-a.txt", "next-b.txt"]
        )
        self.assertEqual(self._match_kinds(result), ["content"] * 3)
        self.assertEqual(self._match_lines(result), ["1", "1", "1"])
        self.assertEqual(self._field(result, "match_count"), "3")
        self.assertEqual(self._field(result, "truncated"), "0")

    def test_content_preview_wins_over_path_preview_for_the_same_file(self) -> None:
        self._write("needle-name.txt", "ordinary\nneedle body\nneedle later\n")
        result = self.assert_success(
            self._reader().search_files("needle"), "workspace_search"
        )
        self.assertEqual(self._match_paths(result), ["needle-name.txt"])
        self.assertEqual(self._match_kinds(result), ["content"])
        self.assertEqual(self._match_lines(result), ["2"])
        self.assertIn("snippet=needle body", result)

    def test_result_limit_counts_distinct_files_not_matching_lines(self) -> None:
        self._write("a-many.txt", "\n".join("needle" for _ in range(100)))
        self._write("b.txt", "needle\n")
        self._write("c.txt", "needle\n")
        with mock.patch.object(workspace, "MAX_RESULTS", 2):
            result = self.assert_success(
                self._reader().search_files("needle"), "workspace_search"
            )
        self.assertEqual(self._match_paths(result), ["a-many.txt", "b.txt"])
        self.assertEqual(self._field(result, "match_count"), "2")
        self.assertEqual(self._field(result, "truncated"), "1")

    def test_content_preview_stops_after_first_hit_and_obeys_byte_budget(self) -> None:
        content = "needle first\nneedle second\n"
        self._write("stream.txt", content)

        def guarded_lines(_text: str):
            yield "needle first"
            raise AssertionError("search consumed lines after the first preview hit")

        with (
            mock.patch("agentos_workspace._text_lines", side_effect=guarded_lines),
            mock.patch.object(workspace, "MAX_SCAN_BYTES", len(content)),
        ):
            result = self.assert_success(
                self._reader().search_files("needle"), "workspace_search"
            )
        self.assertEqual(self._match_paths(result), ["stream.txt"])
        self.assertEqual(self._match_kinds(result), ["content"])

        with mock.patch.object(workspace, "MAX_SCAN_BYTES", len(content) - 1):
            incomplete = self.assert_success(
                self._reader().search_files("needle"), "workspace_search"
            )
        self.assertEqual(self._field(incomplete, "match_count"), "0")
        self.assertEqual(self._field(incomplete, "truncated"), "1")

    def test_binary_prefix_sniff_does_not_hide_later_text_match(self) -> None:
        binary_count = 20
        binary_size = 64 * 1024
        for index in range(binary_count):
            self._write(
                f"a-images/image-{index:02d}.png",
                b"\x89PNG\r\n\x1a\n\x00" + b"x" * (binary_size - 9),
            )
        target = "needle survives binary files\n"
        self._write("z-source.txt", target)
        sniff_budget = binary_count * workspace.MAX_BINARY_SNIFF_BYTES + len(target)

        with mock.patch.object(workspace, "MAX_SCAN_BYTES", sniff_budget):
            result = self.assert_success(
                self._reader().search_files("needle"), "workspace_search"
            )
        self.assertEqual(self._match_paths(result), ["z-source.txt"])
        self.assertEqual(self._match_kinds(result), ["content"])
        self.assertEqual(self._field(result, "truncated"), "0")

    def test_read_returns_exact_neighboring_lines_and_navigation(self) -> None:
        self._write("src/sample.txt", "first\nsecond\n第三行\nfourth")

        result = self.assert_success(
            self._reader().read_file("src/sample.txt", 2, 2), "workspace_read"
        )

        expected = {
            "path": "src/sample.txt",
            "start_line": "2",
            "end_line": "3",
            "total_lines": "4",
            "returned_lines": "2",
            "has_more": "1",
            "next_start_line": "4",
        }
        for name, value in expected.items():
            self.assertEqual(self._field(result, name), value)
        self.assertIn("\ncontent=second\n第三行", result)
        self.assertNotIn("\nfirst\n", result)
        self.assertNotIn("\nfourth\n", result)

    def test_read_at_last_line_reports_end_of_file(self) -> None:
        self._write("plain", "one\ntwo\nthree\n")

        result = self.assert_success(
            self._reader().read_file("plain", 3, 64), "workspace_read"
        )

        self.assertEqual(self._field(result, "end_line"), "3")
        self.assertEqual(self._field(result, "returned_lines"), "1")
        self.assertEqual(self._field(result, "has_more"), "0")
        self.assertEqual(self._field(result, "next_start_line"), "0")
        self.assertIn("\ncontent=three\n", result + "\n")

    def test_read_accepts_sixty_four_lines_and_u32_start_lines(self) -> None:
        self._write("lines.txt", "\n".join(f"line-{index}" for index in range(70)))
        result = self.assert_success(
            self._reader().read_file("lines.txt", 1, 64), "workspace_read"
        )
        self.assertEqual(self._field(result, "returned_lines"), "64")

        self._write("million-lines.txt", b"\n" * 1_000_001)
        high = self.assert_success(
            self._reader().read_file("million-lines.txt", 1_000_001, 1),
            "workspace_read",
        )
        self.assertEqual(self._field(high, "start_line"), "1000001")
        self.assert_error(
            self._reader().read_file("lines.txt", 0x1_0000_0000, 1),
            "invalid_line_range",
        )

    def test_read_reduces_the_returned_range_to_fit_the_result_budget(self) -> None:
        lines = [f"line-{index:02d}-" + ("界" * 70) for index in range(64)]
        self._write("wide.txt", "\n".join(lines))

        result = self.assert_success(
            self._reader().read_file("wide.txt", 1, 64), "workspace_read"
        )

        returned = int(self._field(result, "returned_lines"))
        end_line = int(self._field(result, "end_line"))
        self.assertGreaterEqual(returned, 1)
        self.assertLess(returned, 64)
        self.assertEqual(end_line, returned)
        self.assertEqual(self._field(result, "has_more"), "1")
        self.assertEqual(self._field(result, "next_start_line"), str(end_line + 1))
        self.assertIn(f"content={lines[0]}", result)

    def test_unicode_relative_paths_are_supported(self) -> None:
        self._write("资料/说明.txt", "第一行\n关键内容\n")
        reader = self._reader()

        search = self.assert_success(
            reader.search_files("关键", "资料/"), "workspace_search"
        )
        self.assertIn("资料/说明.txt", self._match_paths(search))
        read = self.assert_success(
            reader.read_file("资料/说明.txt", 2, 1), "workspace_read"
        )
        self.assertIn("content=关键内容", read)

    def test_schema_text_limits_count_unicode_codepoints_not_utf8_bytes(self) -> None:
        self._write("ordinary.txt", "ordinary\n")
        reader = self._reader()

        self.assert_error(reader.search_files("界" * 95), "no_matches")
        self.assertEqual(
            workspace._validate_relative_path(
                "界" * 111,
                "prefix",
                workspace.MAX_PREFIX_BYTES,
                empty=True,
                trailing_slash=True,
            ),
            "界" * 111,
        )
        self.assertEqual(
            workspace._validate_relative_path(
                "界" * 255,
                "path",
                workspace.MAX_PATH_BYTES,
                empty=False,
                trailing_slash=False,
            ),
            "界" * 255,
        )
        for value, label, maximum in (
            ("界" * 96, "query", workspace.MAX_QUERY_BYTES),
            ("界" * 112, "prefix", workspace.MAX_PREFIX_BYTES),
            ("界" * 256, "path", workspace.MAX_PATH_BYTES),
            ("\ud800", "query", workspace.MAX_QUERY_BYTES),
        ):
            with self.subTest(label=label, length=len(value)):
                with self.assertRaises(workspace._WorkspaceInputError):
                    workspace._validate_text(value, label, maximum, empty=True)

    def test_invalid_search_arguments_are_rejected_without_throwing(self) -> None:
        reader = self._reader()
        invalid = (
            (None, ""),
            (7, ""),
            ("bad\0query", ""),
            ("bad\nquery", ""),
            ("x" * 96, ""),
            ("界" * 96, ""),
            ("ok", "/absolute"),
            ("ok", "../escape"),
            ("ok", "a/../escape"),
            ("ok", "a//b"),
            ("ok", "a\\b"),
            ("ok", "C:/escape"),
            ("ok", "//server/share"),
            ("ok", "bad\0prefix"),
            ("ok", "p" * 112),
            ("ok", "界" * 112),
        )
        for query, prefix in invalid:
            with self.subTest(query=query, prefix=prefix):
                self.assert_error(reader.search_files(query, prefix))

    def test_invalid_read_arguments_are_rejected_without_throwing(self) -> None:
        self._write("file.txt", "one\ntwo\n")
        reader = self._reader()
        invalid = (
            ("", 1, 1),
            ("/absolute", 1, 1),
            ("../escape", 1, 1),
            ("a/../escape", 1, 1),
            ("./file.txt", 1, 1),
            ("a//file.txt", 1, 1),
            ("a\\file.txt", 1, 1),
            ("C:/escape", 1, 1),
            ("//server/share", 1, 1),
            ("file.txt:stream", 1, 1),
            ("bad\0path", 1, 1),
            ("p" * 256, 1, 1),
            ("界" * 256, 1, 1),
            ("file.txt", 0, 1),
            ("file.txt", -1, 1),
            ("file.txt", True, 1),
            ("file.txt", 1.0, 1),
            ("file.txt", 1, 0),
            ("file.txt", 1, 65),
            ("file.txt", 1, True),
        )
        for path, start_line, max_lines in invalid:
            with self.subTest(path=path, start_line=start_line, max_lines=max_lines):
                self.assert_error(reader.read_file(path, start_line, max_lines))

    def test_missing_directory_and_out_of_range_reads_are_errors(self) -> None:
        self._write("dir/file.txt", "one\ntwo\n")
        reader = self._reader()
        for path, line in (
            ("missing.txt", 1),
            ("dir", 1),
            ("dir/file.txt", 3),
        ):
            with self.subTest(path=path, line=line):
                self.assert_error(reader.read_file(path, line, 1))

    def test_invalid_utf8_and_nul_content_are_not_projected(self) -> None:
        self._write("invalid.bin", b"public\xffneedle\n")
        self._write("nul.bin", b"public\0needle\n")
        reader = self._reader()

        for path in ("invalid.bin", "nul.bin"):
            with self.subTest(path=path):
                self.assert_error(reader.read_file(path, 1, 1))
        result = self.assert_error(reader.search_files("needle"), "no_matches")
        self.assertNotIn("public", result)

    def test_symlink_target_is_never_read_or_searched(self) -> None:
        secret = self.base / "outside-secret.txt"
        secret.write_text("OUTSIDE_SECRET_NEEDLE\n", encoding="utf-8")
        link = self.root / "linked.txt"
        try:
            link.symlink_to(secret)
        except (NotImplementedError, OSError):
            self.skipTest("runtime cannot create a file symlink")
        reader = self._reader()

        read = self.assert_error(reader.read_file("linked.txt", 1, 1))
        search = self.assert_error(reader.search_files("OUTSIDE_SECRET_NEEDLE"))
        self.assertNotIn("OUTSIDE_SECRET_NEEDLE", read)
        self.assertNotIn("OUTSIDE_SECRET_NEEDLE", search)

    @unittest.skipIf(os.name == "nt", "POSIX openat/O_NOFOLLOW test")
    def test_leaf_changed_to_symlink_at_open_is_rejected(self) -> None:
        source = self._write("source.txt", "public\n")
        secret = self.base / "secret.txt"
        secret.write_text("OUTSIDE_SECRET\n", encoding="utf-8")
        real_openat2 = workspace._linux_openat2
        swapped = False

        def swap_before_open(
            root_descriptor: int, path: str, flags: int, **kwargs
        ) -> int:
            nonlocal swapped
            if path == "source.txt" and not swapped:
                source.unlink()
                source.symlink_to(secret)
                swapped = True
            return real_openat2(root_descriptor, path, flags, **kwargs)

        with mock.patch(
            "agentos_workspace._linux_openat2", side_effect=swap_before_open
        ):
            result = self.assert_error(self._reader().read_file("source.txt", 1, 1))
        self.assertNotIn("OUTSIDE_SECRET", result)

    @unittest.skipUnless(
        os.name == "posix" and sys.platform.startswith("linux"),
        "Linux openat2 ancestry test",
    )
    def test_parent_renamed_outside_after_directory_open_is_rejected(self) -> None:
        self._write("dir/secret.txt", "MOVED_OUTSIDE_SECRET\n")
        outside = self.base / "outside"
        outside.mkdir()
        reader = self._reader()
        real_open_directory = reader._open_posix_directory
        moved = False

        def move_after_open(relative: str) -> int:
            nonlocal moved
            descriptor = real_open_directory(relative)
            if relative == "dir" and not moved:
                os.rename(self.root / "dir", outside / "dir")
                moved = True
            return descriptor

        with mock.patch.object(
            reader, "_open_posix_directory", side_effect=move_after_open
        ):
            result = self.assert_error(reader.read_file("dir/secret.txt", 1, 1))
        self.assertTrue(moved)
        self.assertNotIn("MOVED_OUTSIDE_SECRET", result)

    @unittest.skipUnless(
        os.name == "posix" and sys.platform.startswith("linux"),
        "Linux openat2 availability test",
    )
    def test_missing_atomic_beneath_open_primitive_fails_closed(self) -> None:
        unavailable = OSError(getattr(os, "ENOSYS", 38), "openat2 unavailable")
        with mock.patch("agentos_workspace._linux_openat2", side_effect=unavailable):
            with self.assertRaises(ValueError):
                self._reader()

    @unittest.skipUnless(
        os.name == "posix" and sys.platform.startswith("linux"),
        "Linux atomic root pin test",
    )
    def test_root_changed_to_symlink_before_atomic_pin_is_rejected(self) -> None:
        moved = self.base / "original-workspace"
        outside = self.base / "outside-root"
        outside.mkdir()
        (outside / "secret.txt").write_text("OUTSIDE_ROOT_SECRET\n", encoding="utf-8")
        real_openat2 = workspace._linux_openat2
        swapped = False

        def swap_before_pin(root_descriptor, relative, flags, **kwargs):
            nonlocal swapped
            if kwargs.get("allow_xdev") and not swapped:
                os.rename(self.root, moved)
                self.root.symlink_to(outside, target_is_directory=True)
                swapped = True
            return real_openat2(root_descriptor, relative, flags, **kwargs)

        try:
            with mock.patch(
                "agentos_workspace._linux_openat2", side_effect=swap_before_pin
            ):
                with self.assertRaises(ValueError):
                    self._reader()
        finally:
            if self.root.is_symlink():
                self.root.unlink()
            if moved.exists():
                os.rename(moved, self.root)
        self.assertTrue(swapped)

    def test_in_root_hardlink_is_authorized_as_a_workspace_entry(self) -> None:
        outside = self.base / "outside-hardlink-source.txt"
        outside.write_text("HARDLINK_CONTENT\n", encoding="utf-8")
        linked = self.root / "inside-hardlink.txt"
        try:
            os.link(outside, linked)
        except (NotImplementedError, OSError):
            self.skipTest("runtime cannot create a hardlink")

        result = self.assert_success(
            self._reader().read_file("inside-hardlink.txt", 1, 1),
            "workspace_read",
        )
        self.assertIn("content=HARDLINK_CONTENT", result)

    @unittest.skipUnless(os.name == "nt", "Windows path-based enumeration test")
    def test_windows_redirected_enumeration_never_projects_unopened_names(self) -> None:
        inside = self.root / "inside"
        inside.mkdir()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "leaked-name.txt").write_text(
            "OUTSIDE_SECRET_NEEDLE\n", encoding="utf-8"
        )
        real_scandir = os.scandir

        def redirect(target):
            if os.path.normcase(os.fspath(target)) == os.path.normcase(os.fspath(inside)):
                return real_scandir(outside)
            return real_scandir(target)

        with mock.patch("agentos_workspace.os.scandir", side_effect=redirect):
            result = self.assert_success(
                self._reader().search_files("needle", "inside/"), "workspace_search"
            )
        self.assertEqual(self._field(result, "match_count"), "0")
        self.assertEqual(self._field(result, "truncated"), "1")
        self.assertNotIn("leaked-name.txt", result)
        self.assertNotIn("OUTSIDE_SECRET_NEEDLE", result)

    def test_hidden_reparse_component_is_rejected(self) -> None:
        self._write("hidden/secret.txt", "HIDDEN_REPARSE_SECRET\n")
        hidden = safe_host_paths.absolute_lexical_path(self.root / "hidden")

        def attributes(candidate: Path) -> int:
            if safe_host_paths.absolute_lexical_path(candidate) == hidden:
                return safe_host_paths._FILE_ATTRIBUTE_REPARSE_POINT
            return 0

        reader = self._reader()
        with mock.patch(
            "safe_host_paths._msys_native_file_attributes", side_effect=attributes
        ):
            read = self.assert_error(reader.read_file("hidden/secret.txt", 1, 1))
            search = self.assert_error(reader.search_files("HIDDEN_REPARSE_SECRET"))
        self.assertNotIn("HIDDEN_REPARSE_SECRET", read)
        self.assertNotIn("HIDDEN_REPARSE_SECRET", search)

    def test_replacement_during_read_is_rejected(self) -> None:
        source = self._write("source.txt", "public\n")
        replacement = self.base / "replacement.txt"
        replacement.write_text("replacement secret\n", encoding="utf-8")
        replaced = False
        real_read = workspace._read_fd_bounded

        def replace_after_open(descriptor: int, maximum: int) -> bytes:
            nonlocal replaced
            if not replaced:
                os.replace(replacement, source)
                replaced = True
            return real_read(descriptor, maximum)

        with mock.patch(
            "agentos_workspace._read_fd_bounded",
            side_effect=replace_after_open,
        ):
            result = self.assert_error(self._reader().read_file("source.txt", 1, 1))
        self.assertNotIn("replacement secret", result)

    def test_constructor_rejects_missing_nondirectory_and_linked_roots(self) -> None:
        missing = self.base / "missing"
        ordinary_file = self.base / "ordinary"
        ordinary_file.write_text("file\n", encoding="utf-8")
        for candidate in (missing, ordinary_file):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    workspace.WorkspaceReader(candidate)

        linked_root = self.base / "linked-root"
        try:
            linked_root.symlink_to(self.root, target_is_directory=True)
        except (NotImplementedError, OSError):
            return
        with self.assertRaises(ValueError):
            workspace.WorkspaceReader(linked_root)


class VersionedWorkspaceReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "workspace"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, relative: str, content: str) -> Path:
        target = self.root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
        return target

    @staticmethod
    def _manifest_values(content: str) -> dict[str, str]:
        return {
            line.split("=", 1)[0]: line.split("=", 1)[1]
            for line in content.splitlines()[1:]
        }

    def test_manifest_pages_are_generation_bound_and_eof_keeps_offset(self) -> None:
        for index in range(33):
            self._write(f"f{index:02d}.txt", f"value {index}\n")
        reader = workspace.WorkspaceReader(self.root)
        first = reader.manifest("", 0, 32)
        self.assertEqual(first.status, "ok")
        first_values = self._manifest_values(first.content)
        self.assertEqual(first_values["entry_count"], "32")
        self.assertEqual(first_values["next_cursor"], "32")
        self.assertEqual(first_values["eof"], "0")

        second = reader.manifest(first.workspace_generation, 32, 32)
        self.assertEqual(second.status, "ok")
        second_values = self._manifest_values(second.content)
        self.assertEqual(second_values["entry_count"], "1")
        self.assertEqual(second_values["next_cursor"], "33")
        self.assertEqual(second_values["eof"], "1")

    def test_manifest_page_also_fits_guest_search_arguments(self) -> None:
        reader = workspace.WorkspaceReader(self.root)
        entries = tuple(
            workspace.WorkspaceObject(
                object_id=f"{index + 1:064x}",
                path=(
                    f"d{index:02d}/"
                    + ('"' * 39)
                    + ("\U00010000" * 204)
                    + "suffix00"
                ),
                revision=f"{index + 101:064x}",
                size=1,
            )
            for index in range(workspace.MAX_MANIFEST_PAGE)
        )
        snapshot = workspace._WorkspaceSnapshot("a" * 64, entries)
        with mock.patch.object(reader, "_workspace_snapshot", return_value=snapshot):
            result = reader.manifest("", 0, workspace.MAX_MANIFEST_PAGE)
        self.assertEqual(result.status, "ok")
        count = int(self._manifest_values(result.content)["entry_count"])
        self.assertGreater(count, 0)
        self.assertLess(count, workspace.MAX_MANIFEST_PAGE)
        self.assertTrue(reader._search_arguments_fit(entries[:count]))
        self.assertFalse(reader._search_arguments_fit(entries[: count + 1]))

        def search_argument_bytes(selected) -> int:
            return len(
                json.dumps(
                    {
                        "query": "\\" * workspace.MAX_QUERY_BYTES,
                        "candidates": [entry.candidate() for entry in selected],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )

        self.assertLessEqual(
            search_argument_bytes(entries[:count]),
            workspace.MAX_WORKSPACE_ARGUMENT_BYTES,
        )
        self.assertGreater(
            search_argument_bytes(entries[: count + 1]),
            workspace.MAX_WORKSPACE_ARGUMENT_BYTES,
        )
        next_projection = reader._render_manifest_page(
            0, entries[: count + 1], len(entries)
        )
        self.assertTrue(reader._manifest_projection_fits(next_projection))

    def test_new_cursor_zero_refresh_changes_generation_and_content_revision(self) -> None:
        target = self._write("same.txt", "alpha\n")
        reader = workspace.WorkspaceReader(self.root)
        before = reader.manifest("", 0, 32)
        old = target.stat()
        target.write_text("bravo\n", encoding="utf-8", newline="")
        os.utime(target, ns=(old.st_atime_ns, old.st_mtime_ns))
        after = reader.manifest("", 0, 32)
        self.assertEqual(after.status, "ok")
        self.assertNotEqual(after.workspace_generation, before.workspace_generation)
        self.assertNotEqual(
            self._manifest_values(after.content)["entry[1].revision"],
            self._manifest_values(before.content)["entry[1].revision"],
        )

    def test_search_uses_only_manifest_candidates_and_zero_match_is_structured(self) -> None:
        self._write("a.txt", "ordinary\n")
        self._write("secret.txt", "needle\n")
        reader = workspace.WorkspaceReader(self.root)
        manifest = reader.manifest("", 0, 32)
        values = self._manifest_values(manifest.content)
        candidate = {
            "object_id": values["entry[1].object_id"],
            "path": values["entry[1].path"],
            "revision": values["entry[1].revision"],
        }
        result = reader.search_candidates(
            manifest.workspace_generation, "needle", [candidate]
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.content.startswith("workspace_search_v1\n"))
        self.assertIn("match_count=0", result.content)
        self.assertNotIn("secret.txt", result.content)

        unauthorized = dict(candidate)
        unauthorized["path"] = "secret.txt"
        denied = reader.search_candidates(
            manifest.workspace_generation, "needle", [unauthorized]
        )
        self.assertEqual(denied.status, "error")
        self.assertEqual(denied.content, "workspace_error=unauthorized_candidate")

    def test_read_returns_only_bytes_used_for_revision_check(self) -> None:
        target = self._write("source.txt", "public\n")
        reader = workspace.WorkspaceReader(self.root)
        manifest = reader.manifest("", 0, 32)
        values = self._manifest_values(manifest.content)
        replacement = self.root / "replacement.txt"
        replacement.write_text("secret\n", encoding="utf-8", newline="")
        os.replace(replacement, target)
        result = reader.read_versioned(
            manifest.workspace_generation,
            values["entry[1].object_id"],
            values["entry[1].path"],
            values["entry[1].revision"],
            1,
            1,
        )
        self.assertEqual(result.status, "stale")
        self.assertEqual(result.content, "")

    def test_binary_file_is_versioned_but_not_decoded_for_search_or_read(self) -> None:
        target = self.root / "data.bin"
        target.write_bytes(b"\x00alpha")
        reader = workspace.WorkspaceReader(self.root)
        manifest = reader.manifest("", 0, 32)
        self.assertEqual(manifest.status, "ok")
        values = self._manifest_values(manifest.content)
        candidate = {
            "object_id": values["entry[1].object_id"],
            "path": values["entry[1].path"],
            "revision": values["entry[1].revision"],
        }

        searched = reader.search_candidates(
            manifest.workspace_generation, "alpha", [candidate]
        )
        self.assertEqual(searched.status, "ok")
        self.assertIn("match_count=0", searched.content)
        read = reader.read_versioned(
            manifest.workspace_generation,
            candidate["object_id"],
            candidate["path"],
            candidate["revision"],
            1,
            1,
        )
        self.assertEqual(read.status, "ok")
        self.assertEqual(read.content, "workspace_error=binary_file")

        old = target.stat()
        target.write_bytes(b"\x00bravo")
        os.utime(target, ns=(old.st_atime_ns, old.st_mtime_ns))
        refreshed = reader.manifest("", 0, 32)
        self.assertNotEqual(
            self._manifest_values(refreshed.content)["entry[1].revision"],
            candidate["revision"],
        )

    def test_manifest_hashing_has_a_total_byte_budget(self) -> None:
        self._write("large.txt", "abcdefgh\n")
        reader = workspace.WorkspaceReader(self.root)
        with mock.patch.object(workspace, "MAX_SCAN_BYTES", 4):
            result = reader.manifest("", 0, 32)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.content, "workspace_error=manifest_incomplete")

    def test_manifest_accepts_more_than_sixteen_mib_within_hard_budget(self) -> None:
        payload = b"x" * workspace.MAX_FILE_BYTES
        for index in range(17):
            (self.root / f"large-{index:02}.txt").write_bytes(payload)
        reader = workspace.WorkspaceReader(self.root)
        result = reader.manifest("", 0, 32)
        self.assertEqual(result.status, "ok")
        self.assertEqual(self._manifest_values(result.content)["entry_count"], "17")

    def test_manifest_fails_closed_above_sixty_four_mib_hard_budget(self) -> None:
        payload = b"x" * workspace.MAX_FILE_BYTES
        file_count = workspace.MAX_SCAN_BYTES // workspace.MAX_FILE_BYTES + 1
        for index in range(file_count):
            (self.root / f"large-{index:02}.txt").write_bytes(payload)
        reader = workspace.WorkspaceReader(self.root)
        result = reader.manifest("", 0, 32)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.content, "workspace_error=manifest_incomplete")


if __name__ == "__main__":
    unittest.main(verbosity=2)
