#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import check_host_test_alignment as manifest_checker
from check_host_platform_alignment import (
    CAPABILITY_GROUPS,
    read_expected_programs,
    read_program_ledger,
    run_check,
    runtime_candidates,
)

FIXTURE_PROGRAMS = ("rp_catalog", "rp_backend", "rp_consistency")


class HostPlatformAlignmentTests(unittest.TestCase):
    def _write_minimal_tree(self, root: Path, host: Path) -> None:
        (host / "agent_platform").mkdir(parents=True)
        for group in CAPABILITY_GROUPS:
            for module in group.host_modules:
                (host / "agent_platform" / module).write_text("# fixture\n", encoding="utf-8")

        (root / "baseline_ucore" / "user" / "src").mkdir(parents=True)
        (root / "user" / "src").mkdir(parents=True)
        manifest = (
            "#ifndef __RP_PROGRAM_MANIFEST_H__\n"
            "#define __RP_PROGRAM_MANIFEST_H__\n"
            "#define RP_PLATFORM_PROGRAMS(APPLY) \\\n"
            + "\n".join(
                f'    APPLY("{program}")' + (" \\" if index + 1 < len(FIXTURE_PROGRAMS) else "")
                for index, program in enumerate(FIXTURE_PROGRAMS)
            )
            + "\n#define RP_AGENTOS_ROLE_PROGRAMS(APPLY) \\\n"
            + '    APPLY("rp_backend", "orchestrator")\n'
            + "#endif\n"
        )
        for include_dir in (
            root / "baseline_ucore" / "user" / "include",
            root / "user" / "include",
        ):
            include_dir.mkdir(parents=True)
            (include_dir / "rp_program_manifest.h").write_text(manifest, encoding="utf-8")
        plain_sources = {source for group in CAPABILITY_GROUPS for source in group.plain_sources}
        agentos_sources = {source for group in CAPABILITY_GROUPS for source in group.agentos_sources}
        for source in plain_sources:
            (root / "baseline_ucore" / "user" / "src" / source).write_text("int main(void) { return 0; }\n", encoding="utf-8")
        for source in agentos_sources:
            (root / "user" / "src" / source).write_text("int main(void) { return 0; }\n", encoding="utf-8")

        (root / "host_tools").mkdir()
        reader_keywords = sorted({keyword for group in CAPABILITY_GROUPS for keyword in group.reader_keywords})
        (root / "host_tools" / "plain_ucore_reader.py").write_text("\n".join(reader_keywords), encoding="utf-8")

    def _write_backend_runtime_state(self, state_dir: Path) -> None:
        source_fields: dict[str, list[tuple[str, str]]] = {}
        for source, key, value in manifest_checker.BACKEND_RUNTIME_CASES.values():
            source_fields.setdefault(source, []).append((key, value))
        for source, key, value in manifest_checker.EXPECTED_RUNTIME_ASSERTIONS.values():
            if source != "rp_backend_exec":
                source_fields.setdefault(source, []).append((key, value))
        for source, key, value in manifest_checker.COMPARATOR_RUNTIME_CASES.values():
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
            "status=reference_ready"
        ]
        nested_digest = manifest_checker.FNV_OFFSET
        for case_name, (source, _key, _value) in manifest_checker.BACKEND_RUNTIME_CASES.items():
            data = (state_dir / source).read_bytes()
            source_hash = manifest_checker.fnv1a64(data)
            nested_digest = manifest_checker.fold_backend_measurement(
                nested_digest, case_name, source, source_hash, len(data)
            )
            lines.append(
                f"evidence_role=runtime_verified;runtime_case={case_name};source={source};"
                f"source_bytes={len(data)};source_hash={source_hash};"
                "assertions_executed=1;assertions_passed=1;generation=runtime;status=verified"
            )
        count = len(manifest_checker.BACKEND_RUNTIME_CASES)
        lines.append(
            "evidence_role=runtime_verified;"
            f"runtime_cases_executed={count};runtime_cases_verified={count};"
            f"runtime_assertions_executed={count};runtime_assertions_passed={count};"
            f"runtime_source_digest={nested_digest};echo_request_id=4401;echo_status=0;"
            "context_latest_sequence=2;query_returned=1;query_scanned=1;query_used_index=1;"
            "edit_base_version=1;edit_current_version=2;edit_active=0;"
            "generation=runtime;status=verified"
        )
        (state_dir / "rp_backend_exec").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_state_dirs(self, root: Path) -> tuple[Path, Path]:
        plain_state = root / "plain-state"
        agentos_state = root / "agentos-state"
        plain_state.mkdir()
        agentos_state.mkdir()
        for group in CAPABILITY_GROUPS:
            for name in runtime_candidates(group, group.plain_sources)[:1]:
                (plain_state / name).write_text(
                    "source=guest_runtime;records=1;status=ready\n", encoding="utf-8"
                )
            for name in runtime_candidates(group, group.agentos_sources)[:1]:
                (agentos_state / name).write_text(
                    "source=guest_runtime;records=1;generation=runtime;status=verified\n",
                    encoding="utf-8",
                )
        (plain_state / "rp_tests").write_text(
            "suite=plain-ucore-demo-reference\n"
            "evidence_file_role=demo_reference\n"
            "evidence_file_generation=demo_expected\n"
            "evidence_file_status=reference_ready\n",
            encoding="utf-8",
        )
        self._write_backend_runtime_state(agentos_state)
        records: list[str] = []
        digest = manifest_checker.FNV_OFFSET
        for theme in manifest_checker.TEST_THEMES:
            source = manifest_checker.EXPECTED_RUNTIME_SOURCES[theme.name]
            data = (agentos_state / source).read_bytes()
            source_hash = manifest_checker.fnv1a64(data)
            digest = manifest_checker.fnv1a64(source.encode(), digest)
            digest = manifest_checker.fnv1a64(
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
        programs = FIXTURE_PROGRAMS
        plain_ledger = "orchestrator=rp_orch\nlauncher=fork\n" + "".join(
            f"program={program};launcher=fork;ok=1;code=0;elapsed_ms={index + 1}\n"
            for index, program in enumerate(programs)
        )
        agentos_ledger = "orchestrator=rp_orch\nlauncher=mixed_attested\n" + "".join(
            (
                f"program={program};role={'orchestrator' if program == 'rp_backend' else 'plain'};"
                f"launcher={'agent_create_role' if program == 'rp_backend' else 'agent_worker_create'};"
                "identity_source=child_after_exec;"
                f"is_agent={'1' if program == 'rp_backend' else '0'};"
                f"agent_role={'4' if program == 'rp_backend' else '0'};"
                "filesystem_domain=3;filesystem_capabilities=66;"
                f"ok=1;code=0;elapsed_ms={index + 1}\n"
            )
            for index, program in enumerate(programs)
        )
        (plain_state / "rp_orch_timing").write_bytes(plain_ledger.encode())
        (agentos_state / "rp_orch_timing").write_bytes(agentos_ledger.encode())
        program_digest = manifest_checker.FNV_OFFSET
        for program in programs:
            program_digest = manifest_checker.fnv1a64(program.encode() + b"\0", program_digest)
        plain_data = (plain_state / "rp_orch_timing").read_bytes()
        agentos_data = (agentos_state / "rp_orch_timing").read_bytes()
        (plain_state / "rp_agentcmp").write_text(
            "evidence_role=demo_reference;observation_source=guest_runtime;"
            f"program_source=rp_orch_timing;program_source_bytes={len(plain_data)};"
            f"program_source_hash={manifest_checker.fnv1a64(plain_data)};"
            f"program_names_digest={program_digest};programs_observed={len(programs)};"
            "status=reference_observed\n",
            encoding="utf-8",
        )
        (agentos_state / "rp_agentcmp").write_text(
            "evidence_role=runtime_verified;evidence_generation=runtime;"
            f"program_source=rp_orch_timing;program_source_bytes={len(agentos_data)};"
            f"program_source_hash={manifest_checker.fnv1a64(agentos_data)};"
            f"program_names_digest={program_digest};programs_observed={len(programs)};"
            "status=verified\n",
            encoding="utf-8",
        )
        comparator_lines: list[str] = []
        comparator_digest = manifest_checker.FNV_OFFSET
        for case_name, (source, _key, _value) in manifest_checker.COMPARATOR_RUNTIME_CASES.items():
            data = (agentos_state / source).read_bytes()
            source_hash = manifest_checker.fnv1a64(data)
            comparator_digest = manifest_checker.fold_file_measurement(
                comparator_digest, source, source_hash, len(data)
            )
            comparator_lines.append(
                f"evidence_role=runtime_verified;runtime_compare_case={case_name};source={source};"
                f"source_bytes={len(data)};source_hash={source_hash};claim_protocol=exact-field-v1;"
                "assertions_executed=1;assertions_passed=1;generation=runtime;status=verified"
            )
        comparator_count = len(manifest_checker.COMPARATOR_RUNTIME_CASES)
        comparator_lines.append(
            "evidence_role=runtime_verified;evidence_generation=runtime;"
            f"claim_protocol=exact-field-v1;runtime_compare_cases={comparator_count};"
            f"runtime_assertions_executed={comparator_count};runtime_assertions_passed={comparator_count};"
            "catalog_assertions_executed=10;catalog_assertions_passed=10;"
            f"source_digest={comparator_digest};status=verified"
        )
        with (agentos_state / "rp_agentcmp").open("a", encoding="utf-8") as handle:
            handle.write("\n".join(comparator_lines) + "\n")
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
            self.assertTrue(summary["runtime_evidence_verified"])
            self.assertEqual(summary["plain_evidence_role"], "demo_reference")
            self.assertEqual(summary["groups_ok"], summary["groups_total"])

    def test_alignment_reports_missing_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, agentos_state = self._write_state_dirs(root)
            for missing in runtime_candidates(
                CAPABILITY_GROUPS[0], CAPABILITY_GROUPS[0].agentos_sources
            ):
                path = agentos_state / missing
                if path.exists():
                    path.unlink()

            summary = run_check(root, host, require_host=True, plain_state_dir=plain_state, agentos_state_dir=agentos_state)

            self.assertEqual(summary["status"], "failed")
            self.assertTrue(any("no AgentOS runtime state file" in failure for failure in summary["failures"]))

    def test_alignment_rejects_runtime_manifest_without_measurements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, agentos_state = self._write_state_dirs(root)
            (agentos_state / "rp_tests").write_text(
                "evidence_generation=runtime\nstatus=verified\n", encoding="utf-8"
            )

            summary = run_check(root, host, require_host=True, plain_state_dir=plain_state, agentos_state_dir=agentos_state)

            self.assertEqual(summary["status"], "failed")
            self.assertTrue(any("runtime manifest" in failure for failure in summary["failures"]))

    def test_alignment_rejects_embedded_or_unrelated_passed_substrings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, agentos_state = self._write_state_dirs(root)
            (agentos_state / "rp_tests").write_text(
                "note=contains_status=ready\nfoo=passed\nok=1\n", encoding="utf-8"
            )

            summary = run_check(
                root,
                host,
                require_host=True,
                plain_state_dir=plain_state,
                agentos_state_dir=agentos_state,
            )

            self.assertEqual(summary["status"], "failed")
            self.assertTrue(any("runtime manifest" in item for item in summary["failures"]))

    def test_alignment_rejects_demo_expected_as_runtime_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, agentos_state = self._write_state_dirs(root)
            (agentos_state / "rp_tests").write_text(
                "generation=demo_expected;cases=8;status=passed\n", encoding="utf-8"
            )

            summary = run_check(
                root,
                host,
                require_host=True,
                plain_state_dir=plain_state,
                agentos_state_dir=agentos_state,
            )

            self.assertEqual(summary["status"], "failed")

    def test_alignment_rejects_fixed_program_count_not_derived_from_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, agentos_state = self._write_state_dirs(root)
            evidence = (agentos_state / "rp_agentcmp").read_text(encoding="utf-8")
            (agentos_state / "rp_agentcmp").write_text(
                evidence.replace("programs_observed=3", "programs_observed=70"),
                encoding="utf-8",
            )

            summary = run_check(
                root,
                host,
                require_host=True,
                plain_state_dir=plain_state,
                agentos_state_dir=agentos_state,
            )

            self.assertEqual(summary["status"], "failed")
            self.assertTrue(any("programs_observed" in item for item in summary["failures"]))

    def test_program_ledger_rejects_launcher_and_post_exec_identity_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            _plain_state, agentos_state = self._write_state_dirs(root)
            programs, roles, manifest_errors = read_expected_programs(root)
            self.assertEqual(manifest_errors, [])
            ledger = agentos_state / "rp_orch_timing"
            original = ledger.read_text(encoding="utf-8")

            ledger.write_bytes(
                original.replace(
                    "launcher=agent_worker_create", "launcher=fork", 1
                ).encode()
            )
            _measured, errors = read_program_ledger(
                agentos_state, programs, roles, "agentos"
            )
            self.assertTrue(any("invalid AgentOS launcher" in error for error in errors))

            ledger.write_bytes(
                original.replace(
                    "is_agent=0;agent_role=0", "is_agent=1;agent_role=1", 1
                ).encode()
            )
            _measured, errors = read_program_ledger(
                agentos_state, programs, roles, "agentos"
            )
            self.assertTrue(any("mismatched attested identity" in error for error in errors))

    def test_alignment_rejects_self_bound_program_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, agentos_state = self._write_state_dirs(root)
            ledger_path = agentos_state / "rp_orch_timing"
            ledger = ledger_path.read_text(encoding="utf-8").replace(
                "program=rp_backend;", "program=rp_attacker;"
            )
            ledger_path.write_bytes(ledger.encode())
            data = ledger_path.read_bytes()
            digest = manifest_checker.FNV_OFFSET
            for program in ("rp_catalog", "rp_attacker", "rp_consistency"):
                digest = manifest_checker.fnv1a64(program.encode() + b"\0", digest)
            records = (agentos_state / "rp_agentcmp").read_text(encoding="utf-8").splitlines()
            records[0] = (
                "evidence_role=runtime_verified;evidence_generation=runtime;"
                f"program_source=rp_orch_timing;program_source_bytes={len(data)};"
                f"program_source_hash={manifest_checker.fnv1a64(data)};"
                f"program_names_digest={digest};programs_observed={len(FIXTURE_PROGRAMS)};"
                "status=verified"
            )
            (agentos_state / "rp_agentcmp").write_text(
                "\n".join(records) + "\n", encoding="utf-8"
            )

            summary = run_check(
                root,
                host,
                require_host=True,
                plain_state_dir=plain_state,
                agentos_state_dir=agentos_state,
            )

            self.assertEqual(summary["status"], "failed")
            self.assertTrue(any("expected rp_backend" in item for item in summary["failures"]))

    def test_alignment_rejects_plain_reference_impersonating_agentos_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, agentos_state = self._write_state_dirs(root)
            plain_evidence = (plain_state / "rp_agentcmp").read_text(encoding="utf-8")
            (plain_state / "rp_agentcmp").write_text(
                plain_evidence
                + plain_evidence.replace(
                    "evidence_role=demo_reference;observation_source=guest_runtime",
                    "evidence_role=runtime_verified;evidence_generation=runtime",
                ).replace("status=reference_observed", "status=verified"),
                encoding="utf-8",
            )

            summary = run_check(
                root,
                host,
                require_host=True,
                plain_state_dir=plain_state,
                agentos_state_dir=agentos_state,
            )

            self.assertEqual(summary["status"], "failed")
            self.assertTrue(any("impersonates" in item for item in summary["failures"]))

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
