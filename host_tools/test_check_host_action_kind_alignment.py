from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import check_host_action_kind_alignment as checker


def write_fixture(
    root: Path,
    *,
    action_routes: tuple[str, ...] = ("/actions/research/run", "/actions/research/rerun"),
    plain_kinds: tuple[str, ...] = ("research_run", "research_rerun"),
    agentos_kinds: tuple[str, ...] = ("research_run", "research_rerun"),
) -> Path:
    host = root / "host"
    (host / "agent_platform").mkdir(parents=True)
    source_lines = ["class PlatformApi:", "    def route_action(self, method, path, form):"]
    for route in action_routes:
        source_lines.append(f'        if path == "{route}":')
        source_lines.append('            return 303, {"Location": "/"}, ""')
    (host / "agent_platform" / "api_server.py").write_text("\n".join(source_lines), encoding="utf-8")

    for relative, kinds in (("user/src", plain_kinds), ("agentos_ucore/user/src", agentos_kinds)):
        source_dir = root / relative
        source_dir.mkdir(parents=True)
        source_dir.joinpath("rp_actions.c").write_text(
            "\n".join(f'if (rp_host_seed_has("kind={kind}")) return 0;' for kind in kinds),
            encoding="utf-8",
        )
    return host


class HostActionKindAlignmentTests(unittest.TestCase):
    def test_known_routes_have_user_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = write_fixture(root)

            result = checker.run_check(root, host, True)

            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["host_action_routes"], 2)
            self.assertEqual(result["host_action_kinds"], 2)
            self.assertEqual(result["plain_missing_runtime_handlers"], [])
            self.assertEqual(result["agentos_missing_runtime_handlers"], [])
            self.assertIn("rp_actions.c", result["plain_handler_files"])

    def test_missing_user_handler_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = write_fixture(root, plain_kinds=("research_run",))

            result = checker.run_check(root, host, True)

            self.assertEqual(result["status"], "failed")
            self.assertIn("research_rerun", result["plain_missing_kinds"])
            self.assertIn("research_rerun", result["plain_missing_runtime_handlers"])

    def test_kind_only_in_evidence_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = write_fixture(root)
            plain_dir = root / "user/src"
            plain_dir.joinpath("rp_actions.c").write_text('if (rp_host_seed_has("kind=research_run")) return 0;', encoding="utf-8")
            plain_dir.joinpath("rp_compare_plain.c").write_text('if (rp_host_seed_has("kind=research_rerun")) return 0;', encoding="utf-8")

            result = checker.run_check(root, host, True)

            self.assertEqual(result["status"], "failed")
            self.assertNotIn("research_rerun", result["plain_missing_kinds"])
            self.assertIn("research_rerun", result["plain_missing_runtime_handlers"])

    def test_unknown_route_mapping_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = write_fixture(root, action_routes=("/actions/research/new-action",), plain_kinds=("generic",), agentos_kinds=("generic",))

            result = checker.run_check(root, host, True)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["generic_routes"], ["/actions/research/new-action"])

    def test_missing_host_can_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = checker.run_check(root, root / "missing", False)

            self.assertEqual(result["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
