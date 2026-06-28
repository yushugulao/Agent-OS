from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import check_host_surface_alignment as checker


def write_fixture(
    root: Path,
    *,
    api_routes: tuple[str, ...] = ("/api/status", "/api/dashboard"),
    action_routes: tuple[str, ...] = ("/actions/research/run", "/actions/agentcompare/run"),
    source_api: int | None = None,
    source_actions: int | None = None,
    runtime_api: int | None = None,
    runtime_actions: int | None = None,
) -> Path:
    host = root / "host"
    (host / "agent_platform").mkdir(parents=True)
    source_lines = ["class PlatformApi:", "    def route(self, method, path, query):"]
    for route in api_routes:
        source_lines.append(f'        if path == "{route}":')
        source_lines.append("            return 200, {}")
    source_lines.append("    def route_action(self, method, path, form):")
    for route in action_routes:
        source_lines.append(f'        if path == "{route}":')
        source_lines.append('            return 303, {"Location": "/"}, ""')
    source_lines.append('        return 303, {"Location": "/download/research-bundle/example"}, ""')
    (host / "agent_platform" / "api_server.py").write_text("\n".join(source_lines), encoding="utf-8")

    api_count = source_api if source_api is not None else len(api_routes)
    action_count = source_actions if source_actions is not None else len(action_routes)
    for relative in ("user/src/rp_web_export.c", "agentos_ucore/user/src/rp_web_export.c"):
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_text(
            f"host_api_routes={api_count}\\nhost_action_routes={action_count}\\nreader_actions={action_count + 3}\\n",
            encoding="utf-8",
        )

    runtime_api_count = runtime_api if runtime_api is not None else len(api_routes)
    runtime_action_count = runtime_actions if runtime_actions is not None else len(action_routes)
    for state_name in ("plain-state", "agentos-state"):
        state_dir = root / state_name
        state_dir.mkdir()
        (state_dir / "rp_api_catalog").write_text(
            f"host_api_routes={runtime_api_count}\nhost_action_routes={runtime_action_count}\n",
            encoding="utf-8",
        )
        (state_dir / "rp_web_bundle").write_text(
            f"reader_actions={runtime_action_count + 3}\n",
            encoding="utf-8",
        )
    return host


class HostSurfaceAlignmentTests(unittest.TestCase):
    def test_source_and_runtime_counts_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = write_fixture(root)

            result = checker.run_check(root, host, True, root / "plain-state", root / "agentos-state")

            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["host_api_routes"], 2)
            self.assertEqual(result["host_action_routes"], 2)

    def test_source_count_lag_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = write_fixture(root, api_routes=("/api/status", "/api/dashboard", "/api/new"), source_api=2)

            result = checker.run_check(root, host, True)

            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("host_api_routes" in item for item in result["failures"]))

    def test_runtime_count_lag_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = write_fixture(root, runtime_actions=1)

            result = checker.run_check(root, host, True, root / "plain-state", root / "agentos-state")

            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("runtime host_action_routes" in item for item in result["failures"]))

    def test_missing_host_can_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = checker.run_check(root, root / "missing", False)

            self.assertEqual(result["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
