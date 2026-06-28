from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import check_host_test_alignment as checker


def write_fixture(root: Path, tests: list[str], plain_extra: str = "", agentos_extra: str = "") -> Path:
    host = root / "host"
    (host / "tests").mkdir(parents=True)
    body = ["import unittest", "", "class Tests(unittest.TestCase):"]
    for name in tests:
        body.append(f"    def {name}(self):")
        body.append("        pass")
        body.append("")
    (host / "tests" / "test_platform.py").write_text("\n".join(body), encoding="utf-8")

    plain_dir = root / "user" / "src"
    agentos_dir = root / "agentos_ucore" / "user" / "src"
    plain_dir.mkdir(parents=True)
    agentos_dir.mkdir(parents=True)
    tokens = "\n".join(token for theme in checker.TEST_THEMES for token in theme.evidence_tokens)
    (plain_dir / "rp_test_suite.c").write_text(tokens + "\n" + plain_extra, encoding="utf-8")
    (agentos_dir / "rp_test_suite.c").write_text(tokens + "\n" + agentos_extra, encoding="utf-8")
    return host


class HostTestAlignmentTests(unittest.TestCase):
    def test_all_themes_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tests = [
                "test_json_store_save_retries_temporary_permission_error",
                "test_host_workflow_runner_executes_real_files_retry_cache_and_agents",
                "test_usable_research_task_runs_real_files_agents_llm_and_export",
                "test_data_ingestion_files_snapshots_and_api",
                "test_agentos_adapter_contract_api_search_site_package_and_provenance",
                "test_live_web_ui_routes_show_run_agent_evidence_and_compare",
                "test_provenance_query_builder_api_search_site_package_and_provenance",
            ]
            host = write_fixture(root, tests)

            result = checker.run_check(root, host, True)

            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["unclassified_tests"], 0)

    def test_unclassified_test_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = write_fixture(root, ["test_new_unknown_surface"])

            result = checker.run_check(root, host, True)

            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("not mapped" in item for item in result["failures"]))

    def test_missing_agentos_token_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = write_fixture(
                root,
                [
                    "test_json_store_save_retries_temporary_permission_error",
                    "test_host_workflow_runner_executes_real_files_retry_cache_and_agents",
                    "test_usable_research_task_runs_real_files_agents_llm_and_export",
                    "test_data_ingestion_files_snapshots_and_api",
                    "test_agentos_adapter_contract_api_search_site_package_and_provenance",
                    "test_live_web_ui_routes_show_run_agent_evidence_and_compare",
                    "test_provenance_query_builder_api_search_site_package_and_provenance",
                ],
            )
            evidence = (root / "agentos_ucore" / "user" / "src" / "rp_test_suite.c").read_text(encoding="utf-8")
            (root / "agentos_ucore" / "user" / "src" / "rp_test_suite.c").write_text(
                evidence.replace("agent_compare=passed", ""),
                encoding="utf-8",
            )

            result = checker.run_check(root, host, True)

            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("missing AgentOS evidence" in item for item in result["failures"]))

    def test_missing_host_can_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "user" / "src").mkdir(parents=True)
            (root / "agentos_ucore" / "user" / "src").mkdir(parents=True)

            result = checker.run_check(root, root / "missing-host", False)

            self.assertEqual(result["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
