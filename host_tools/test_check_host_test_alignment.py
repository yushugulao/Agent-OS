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

    plain_dir = root / "baseline_ucore" / "user" / "src"
    agentos_dir = root / "user" / "src"
    plain_dir.mkdir(parents=True)
    agentos_dir.mkdir(parents=True)
    tokens = "\n".join(token for theme in checker.TEST_THEMES for token in theme.evidence_tokens)
    (plain_dir / "rp_test_suite.c").write_text(tokens + "\n" + plain_extra, encoding="utf-8")
    (agentos_dir / "rp_test_suite.c").write_text(tokens + "\n" + agentos_extra, encoding="utf-8")
    return host


def write_backend_runtime_state(state_dir: Path) -> None:
    source_fields: dict[str, list[tuple[str, str]]] = {}
    for source, key, value in checker.BACKEND_RUNTIME_CASES.values():
        source_fields.setdefault(source, []).append((key, value))
    for source, key, value in checker.EXPECTED_RUNTIME_ASSERTIONS.values():
        if source != "rp_backend_exec":
            source_fields.setdefault(source, []).append((key, value))
    for source, key, value in checker.COMPARATOR_RUNTIME_CASES.values():
        if source != "rp_backend_exec":
            source_fields.setdefault(source, []).append((key, value))
    for source, fields in source_fields.items():
        unique_fields = list(dict.fromkeys(fields))
        (state_dir / source).write_text(
            ";".join(f"{key}={value}" for key, value in unique_fields) + "\n",
            encoding="utf-8",
        )

    lines = [
        "evidence_role=demo_reference;catalog_generation=demo_expected;"
        "runtime_claim_protocol=source-bound-v1;runtime_claim_scope=file;"
        "status=reference_ready",
        "evidence_role=demo_reference;catalog_generation=demo_expected;"
        "runner_cases=8;status=reference_ready",
    ]
    nested_digest = checker.FNV_OFFSET
    for case_name, (source, _key, _value) in checker.BACKEND_RUNTIME_CASES.items():
        data = (state_dir / source).read_bytes()
        source_hash = checker.fnv1a64(data)
        nested_digest = checker.fold_backend_measurement(
            nested_digest, case_name, source, source_hash, len(data)
        )
        lines.append(
            f"evidence_role=runtime_verified;runtime_case={case_name};source={source};"
            f"source_bytes={len(data)};source_hash={source_hash};"
            "assertions_executed=1;assertions_passed=1;generation=runtime;status=verified"
        )
    case_count = len(checker.BACKEND_RUNTIME_CASES)
    lines.append(
        "evidence_role=runtime_verified;"
        f"runtime_cases_executed={case_count};runtime_cases_verified={case_count};"
        f"runtime_assertions_executed={case_count};runtime_assertions_passed={case_count};"
        f"runtime_source_digest={nested_digest};echo_request_id=4401;echo_status=0;"
        "context_latest_sequence=2;query_returned=1;query_scanned=1;query_used_index=1;"
        "edit_base_version=1;edit_current_version=2;edit_active=0;"
        "generation=runtime;status=verified"
    )
    (state_dir / "rp_backend_exec").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_comparator_runtime_state(state_dir: Path) -> None:
    lines: list[str] = []
    digest = checker.FNV_OFFSET
    for case_name, (source, _key, _value) in checker.COMPARATOR_RUNTIME_CASES.items():
        data = (state_dir / source).read_bytes()
        source_hash = checker.fnv1a64(data)
        digest = checker.fold_file_measurement(digest, source, source_hash, len(data))
        lines.append(
            f"evidence_role=runtime_verified;runtime_compare_case={case_name};source={source};"
            f"source_bytes={len(data)};source_hash={source_hash};claim_protocol=exact-field-v1;"
            "assertions_executed=1;assertions_passed=1;generation=runtime;status=verified"
        )
    count = len(checker.COMPARATOR_RUNTIME_CASES)
    lines.append(
        "evidence_role=runtime_verified;evidence_generation=runtime;"
        f"claim_protocol=exact-field-v1;runtime_compare_cases={count};"
        f"runtime_assertions_executed={count};runtime_assertions_passed={count};"
        "catalog_assertions_executed=10;catalog_assertions_passed=10;"
        f"source_digest={digest};status=verified"
    )
    (state_dir / "rp_agentcmp").write_text("\n".join(lines) + "\n", encoding="utf-8")


def refresh_runtime_manifest(agentos_state: Path) -> None:
    records: list[str] = []
    digest = checker.FNV_OFFSET
    for theme in checker.TEST_THEMES:
        source = checker.EXPECTED_RUNTIME_SOURCES[theme.name]
        data = (agentos_state / source).read_bytes()
        source_hash = checker.fnv1a64(data)
        digest = checker.fnv1a64(source.encode(), digest)
        digest = checker.fnv1a64(
            source_hash.to_bytes(8, "little") + len(data).to_bytes(8, "little"), digest
        )
        records.append(
            f"assertion_set={theme.name};source={source};source_bytes={len(data)};"
            f"source_hash={source_hash};claim_protocol=exact-field-v1;"
            "assertions=2;generation=runtime;status=verified"
        )
    manifest = [
        "suite=agentos-runtime-acceptance",
        "manifest_version=1",
        "evidence_generation=runtime",
        "catalog_generation=demo_expected",
        "catalog_assertions_executed=10",
        "catalog_assertions_passed=10",
        f"runtime_source_digest={digest}",
        *records,
        "runtime_assertions_executed=14;runtime_assertions_passed=14;assertion_sets=7;status=verified",
    ]
    (agentos_state / "rp_tests").write_text("\n".join(manifest) + "\n", encoding="utf-8")


def write_runtime_state(root: Path) -> tuple[Path, Path]:
    plain_state = root / "plain-state"
    agentos_state = root / "agentos-state"
    plain_state.mkdir()
    agentos_state.mkdir()
    (plain_state / "rp_tests").write_text(
        "catalog=passed\nstatus=passed\n", encoding="utf-8"
    )
    write_backend_runtime_state(agentos_state)
    write_comparator_runtime_state(agentos_state)
    refresh_runtime_manifest(agentos_state)
    return plain_state, agentos_state


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

    def test_runtime_state_evidence_is_checked_when_supplied(self) -> None:
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
            plain_state, agentos_state = write_runtime_state(root)

            result = checker.run_check(root, host, True, plain_state, agentos_state)

            self.assertEqual(result["status"], "ready")
            self.assertTrue(result["runtime_state_checked"])
            self.assertTrue(result["runtime_evidence_verified"])
            self.assertEqual(result["plain_evidence_role"], "demo_reference")

    def test_unclassified_test_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = write_fixture(root, ["test_new_unknown_surface"])

            result = checker.run_check(root, host, True)

            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("not mapped" in item for item in result["failures"]))

    def test_c_source_tokens_are_not_used_as_evidence(self) -> None:
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
            (root / "user" / "src" / "rp_test_suite.c").write_text(
                "agent_compare=passed\nstatus=passed\n",
                encoding="utf-8",
            )

            result = checker.run_check(root, host, True)

            self.assertEqual(result["status"], "ready")
            self.assertFalse(result["runtime_state_checked"])

    def test_missing_runtime_token_fails(self) -> None:
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
            plain_state, agentos_state = write_runtime_state(root)
            runtime = (agentos_state / "rp_tests").read_text(encoding="utf-8")
            line = next(line for line in runtime.splitlines() if "assertion_set=agents_llm_compare" in line)
            (agentos_state / "rp_tests").write_text(runtime.replace(line + "\n", ""), encoding="utf-8")

            result = checker.run_check(root, host, True, plain_state, agentos_state)

            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("missing runtime assertion sets" in item for item in result["failures"]))

    def test_arbitrary_passed_text_is_not_a_runtime_manifest(self) -> None:
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
            plain_state, agentos_state = write_runtime_state(root)
            (agentos_state / "rp_tests").write_text(
                "message=everything status=passed\nfoo=passed\n", encoding="utf-8"
            )

            result = checker.run_check(root, host, True, plain_state, agentos_state)

            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["runtime_evidence_verified"])

    def test_tampered_runtime_source_fails_hash_validation(self) -> None:
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
            plain_state, agentos_state = write_runtime_state(root)
            (agentos_state / "rp_agentos_kernel").write_text("tampered\n", encoding="utf-8")

            result = checker.run_check(root, host, True, plain_state, agentos_state)

            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("source measurement" in item for item in result["failures"]))

    def test_hash_bound_manifest_rejects_hardcoded_backend_pass_rows(self) -> None:
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
            plain_state, agentos_state = write_runtime_state(root)
            (agentos_state / "rp_backend_exec").write_text(
                "evidence_role=demo_reference;catalog_generation=demo_expected;"
                "runtime_claim_protocol=source-bound-v1;runtime_claim_scope=file;"
                "status=reference_ready\n"
                "runner_case=fake;result=passed\n"
                "runner_report=fake;status=passed\n"
                "runner_passed=8\n",
                encoding="utf-8",
            )
            refresh_runtime_manifest(agentos_state)

            result = checker.run_check(root, host, True, plain_state, agentos_state)

            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("unbound passed claim" in item for item in result["failures"]))

    def test_backend_exact_assertion_is_rechecked_beyond_outer_hash(self) -> None:
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
            plain_state, agentos_state = write_runtime_state(root)
            (agentos_state / "rp_wfio").write_text(
                "execution_plan=attacker-selected-plan\n", encoding="utf-8"
            )
            refresh_runtime_manifest(agentos_state)

            result = checker.run_check(root, host, True, plain_state, agentos_state)

            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("exact runtime assertion" in item for item in result["failures"]))

    def test_each_runtime_set_requires_its_full_assertion_count(self) -> None:
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
            plain_state, agentos_state = write_runtime_state(root)
            manifest = (agentos_state / "rp_tests").read_text(encoding="utf-8")
            manifest = manifest.replace(";assertions=2;generation=runtime", ";generation=runtime", 1)
            manifest = manifest.replace(
                "runtime_assertions_executed=14;runtime_assertions_passed=14",
                "runtime_assertions_executed=12;runtime_assertions_passed=12",
            )
            (agentos_state / "rp_tests").write_text(manifest, encoding="utf-8")

            result = checker.run_check(root, host, True, plain_state, agentos_state)

            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("assertions must equal 2" in item for item in result["failures"]))

    def test_exact_runtime_field_rejects_matching_substring(self) -> None:
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
            plain_state, agentos_state = write_runtime_state(root)
            (agentos_state / "rp_agentos_kernel").write_text(
                "junk=context_snapshot=present_fake\n", encoding="utf-8"
            )
            refresh_runtime_manifest(agentos_state)

            result = checker.run_check(root, host, True, plain_state, agentos_state)

            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("exact runtime assertion failed" in item for item in result["failures"]))

    def test_missing_host_can_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "baseline_ucore" / "user" / "src").mkdir(parents=True)
            (root / "user" / "src").mkdir(parents=True)

            result = checker.run_check(root, root / "missing-host", False)

            self.assertEqual(result["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
