#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from check_host_platform_alignment import CAPABILITY_GROUPS, run_check, runtime_candidates


class HostPlatformAlignmentTests(unittest.TestCase):
    def _write_minimal_tree(self, root: Path, host: Path) -> None:
        (host / "agent_platform").mkdir(parents=True)
        for group in CAPABILITY_GROUPS:
            for module in group.host_modules:
                (host / "agent_platform" / module).write_text("# fixture\n", encoding="utf-8")

        (root / "baseline_ucore" / "user" / "src").mkdir(parents=True)
        (root / "user" / "src").mkdir(parents=True)
        plain_sources = {source for group in CAPABILITY_GROUPS for source in group.plain_sources}
        agentos_sources = {source for group in CAPABILITY_GROUPS for source in group.agentos_sources}
        for source in plain_sources:
            (root / "baseline_ucore" / "user" / "src" / source).write_text("int main(void) { return 0; }\n", encoding="utf-8")
        for source in agentos_sources:
            (root / "user" / "src" / source).write_text("int main(void) { return 0; }\n", encoding="utf-8")

        (root / "host_tools").mkdir()
        reader_keywords = sorted({keyword for group in CAPABILITY_GROUPS for keyword in group.reader_keywords})
        (root / "host_tools" / "plain_ucore_reader.py").write_text("\n".join(reader_keywords), encoding="utf-8")

    def _write_state_dirs(self, root: Path) -> tuple[Path, Path]:
        plain_state = root / "plain-state"
        agentos_state = root / "agentos-state"
        plain_state.mkdir()
        agentos_state.mkdir()
        for group in CAPABILITY_GROUPS:
            for name in runtime_candidates(group, group.plain_sources)[:1]:
                (plain_state / name).write_text("status=ready\n", encoding="utf-8")
            for name in runtime_candidates(group, group.agentos_sources)[:1]:
                (agentos_state / name).write_text("status=ready\n", encoding="utf-8")
        return plain_state, agentos_state

    def test_alignment_passes_when_required_groups_are_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)

            summary = run_check(root, host, require_host=True)

            self.assertEqual(summary["status"], "ready")
            self.assertEqual(summary["groups_ok"], summary["groups_total"])

    def test_alignment_checks_runtime_state_when_directories_are_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, agentos_state = self._write_state_dirs(root)

            summary = run_check(root, host, require_host=True, plain_state_dir=plain_state, agentos_state_dir=agentos_state)

            self.assertEqual(summary["status"], "ready")
            self.assertTrue(summary["runtime_state_checked"])
            self.assertEqual(summary["groups_ok"], summary["groups_total"])

    def test_alignment_reports_missing_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, agentos_state = self._write_state_dirs(root)
            missing = runtime_candidates(CAPABILITY_GROUPS[0], CAPABILITY_GROUPS[0].agentos_sources)[0]
            (agentos_state / missing).unlink()

            summary = run_check(root, host, require_host=True, plain_state_dir=plain_state, agentos_state_dir=agentos_state)

            self.assertEqual(summary["status"], "failed")
            self.assertTrue(any("no successful AgentOS runtime state file" in failure for failure in summary["failures"]))

    def test_alignment_rejects_runtime_state_without_success_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, agentos_state = self._write_state_dirs(root)
            weak = runtime_candidates(CAPABILITY_GROUPS[0], CAPABILITY_GROUPS[0].plain_sources)[0]
            (plain_state / weak).write_text("draft=present\n", encoding="utf-8")

            summary = run_check(root, host, require_host=True, plain_state_dir=plain_state, agentos_state_dir=agentos_state)

            self.assertEqual(summary["status"], "failed")
            self.assertTrue(any("no successful plain runtime state file" in failure for failure in summary["failures"]))

    def test_alignment_reports_missing_agentos_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            missing = CAPABILITY_GROUPS[-1].agentos_sources[-1]
            (root / "user" / "src" / missing).unlink()

            summary = run_check(root, host, require_host=True)

            self.assertEqual(summary["status"], "failed")
            self.assertTrue(any(missing in failure for failure in summary["failures"]))

    def test_alignment_reports_unmapped_host_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            (host / "agent_platform" / "new_surface.py").write_text("# fixture\n", encoding="utf-8")

            summary = run_check(root, host, require_host=True)

            self.assertEqual(summary["status"], "failed")
            self.assertTrue(any("new_surface.py" in failure for failure in summary["failures"]))

    def test_alignment_can_skip_when_host_platform_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()

            summary = run_check(root, Path(tmp) / "missing-host", require_host=False)

            self.assertEqual(summary["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
