#!/usr/bin/env python3
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import check_host_test_alignment as manifest_checker
from check_host_platform_alignment import (
    CAPABILITY_GROUPS,
    MAINFLOW_RUNTIME_SPECS,
    read_expected_programs,
    read_program_ledger,
    run_check,
    runtime_candidates,
    validate_mainflow_runtime_evidence,
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
        for spec in MAINFLOW_RUNTIME_SPECS:
            source_fields.setdefault(spec.source, []).extend(
                (
                    (spec.claim_key, spec.claim_value),
                    ("status", spec.source_status),
                )
            )
        for source, fields in source_fields.items():
            unique_fields = list(dict.fromkeys(fields))
            (state_dir / source).write_bytes(
                (
                    ";".join(f"{key}={value}" for key, value in unique_fields)
                    + "\n"
                ).encode("utf-8")
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
        mainflow_lines = [
            ";".join(
                [f"stage={spec.stage}"]
                + [f"{key}={value}" for key, value in spec.telemetry_fields]
                + ["status=ready"]
            )
            for spec in MAINFLOW_RUNTIME_SPECS
        ]
        (state_dir / "rp_agentos_mainflow").write_bytes(
            ("\n".join(mainflow_lines) + "\n").encode()
        )

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
        plain_inventory = (
            "evidence_role=demo_reference;evidence_generation=runtime;observation_source=guest_runtime;"
            f"program_source=rp_orch_timing;program_source_bytes={len(plain_data)};"
            f"program_source_hash={manifest_checker.fnv1a64(plain_data)};"
            f"program_names_digest={program_digest};programs_observed={len(programs)};"
            "status=reference_observed\n"
        )
        agentos_inventory = (
            "evidence_role=runtime_verified;evidence_generation=runtime;"
            f"program_source=rp_orch_timing;program_source_bytes={len(agentos_data)};"
            f"program_source_hash={manifest_checker.fnv1a64(agentos_data)};"
            f"program_names_digest={program_digest};programs_observed={len(programs)};"
            "status=verified\n"
        )
        (plain_state / "rp_agentcmp").write_bytes(plain_inventory.encode())
        (agentos_state / "rp_agentcmp").write_bytes(agentos_inventory.encode())
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
        with (agentos_state / "rp_agentcmp").open("ab") as handle:
            handle.write(("\n".join(comparator_lines) + "\n").encode())
        return plain_state, agentos_state

    def _set_seeded_plain_profile(self, plain_state: Path) -> None:
        ledger_path = plain_state / "rp_orch_timing"
        ledger = ledger_path.read_text(encoding="ascii").replace(
            "orchestrator=rp_orch\nlauncher=fork\n",
            "orchestrator=rp_seed_orch\nlauncher=fork_seeded\n",
        ).replace(";launcher=fork;", ";launcher=fork_seeded;")
        ledger_path.write_bytes(ledger.encode())
        data = ledger_path.read_bytes()
        records = (plain_state / "rp_agentcmp").read_bytes().decode("ascii").splitlines()
        digest = manifest_checker.FNV_OFFSET
        for program in FIXTURE_PROGRAMS:
            digest = manifest_checker.fnv1a64(program.encode() + b"\0", digest)
        records[0] = (
            "evidence_role=demo_reference;evidence_generation=runtime;"
            "observation_source=guest_runtime;program_source=rp_orch_timing;"
            f"program_source_bytes={len(data)};"
            f"program_source_hash={manifest_checker.fnv1a64(data)};"
            f"program_names_digest={digest};programs_observed={len(FIXTURE_PROGRAMS)};"
            "status=reference_observed"
        )
        (plain_state / "rp_agentcmp").write_bytes(("\n".join(records) + "\n").encode())

    def _write_inventory_logs(
        self, root: Path, plain_state: Path, agentos_state: Path
    ) -> tuple[Path, Path]:
        logs: list[Path] = []
        for label, state in (("plain", plain_state), ("agentos", agentos_state)):
            record = next(
                line
                for line in (state / "rp_agentcmp").read_bytes().decode("ascii").splitlines()
                if "program_source=rp_orch_timing" in line
            )
            path = root / f"{label}.log"
            path.write_bytes(("boot\nrp_orch: " + record.replace(";", " ") + "\n").encode())
            logs.append(path)
        return logs[0], logs[1]

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
            self.assertTrue(summary["mainflow_host_verified"])
            self.assertEqual(summary["mainflow_verification_origin"], "host_inventory")
            self.assertEqual(
                summary["mainflow_host_stages"], len(MAINFLOW_RUNTIME_SPECS)
            )
            self.assertEqual(
                summary["mainflow_host_assertions_executed"],
                2 * len(MAINFLOW_RUNTIME_SPECS),
            )
            self.assertEqual(
                len(summary["mainflow_host_sources"]), len(MAINFLOW_RUNTIME_SPECS)
            )
            self.assertEqual(
                summary["mainflow_host_assertions_passed"],
                2 * len(MAINFLOW_RUNTIME_SPECS),
            )
            self.assertEqual(
                summary["mainflow_host_telemetry_sequence"],
                [spec.stage for spec in MAINFLOW_RUNTIME_SPECS],
            )
            telemetry = (agentos_state / "rp_agentos_mainflow").read_bytes()
            self.assertEqual(
                summary["mainflow_host_telemetry_source"], "rp_agentos_mainflow"
            )
            self.assertEqual(summary["mainflow_host_telemetry_bytes"], len(telemetry))
            self.assertEqual(
                summary["mainflow_host_telemetry_hash"],
                manifest_checker.fnv1a64(telemetry),
            )
            self.assertEqual(summary["plain_evidence_role"], "demo_reference")
            self.assertEqual(summary["groups_ok"], summary["groups_total"])


    def test_mainflow_host_rejects_any_guest_verified_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            _plain_state, agentos_state = self._write_state_dirs(root)
            mainflow = agentos_state / "rp_agentos_mainflow"
            original = mainflow.read_bytes()

            probe = root / "user" / "src" / "rp_probe.c"
            probe.write_text(
                "typedef int (*writer_t)(const char *, const char *);\n"
                "int forged(writer_t writer) {\n"
                "  char destination[] = {114,112,95,97,103,101,110,116,111,115,95,"
                "109,97,105,110,102,108,111,119,0};\n"
                "  char receipt[] = {101,118,105,100,101,110,99,101,95,114,111,108,"
                "101,61,114,117,110,116,105,109,101,95,118,101,114,105,102,105,101,"
                "100,0};\n"
                "  return writer(destination, receipt);\n"
                "}\n",
                encoding="utf-8",
            )
            forged_receipts = (
                "evidence_role=runtime_verified;stage=entry;source=rp_agentos_kernel;"
                "claim_key=context_snapshot;claim_value=present;source_status=ready;"
                "source_bytes=1;source_hash=1;claim_protocol=exact-fields-v2;"
                "assertions_executed=2;assertions_passed=2;generation=runtime;status=verified",
                "stage=forged;generation=runtime;status=verified",
            )
            for receipt in forged_receipts:
                with self.subTest(receipt=receipt.split(";", 1)[0]):
                    mainflow.write_bytes(original + receipt.encode("ascii") + b"\n")
                    summary, errors = validate_mainflow_runtime_evidence(
                        agentos_state
                    )
                    self.assertFalse(summary["verified"])
                    self.assertTrue(any("Guest runtime verification is forbidden" in error for error in errors))

    def test_mainflow_host_rejects_conflicting_claim_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            _plain_state, agentos_state = self._write_state_dirs(root)
            spec = MAINFLOW_RUNTIME_SPECS[0]
            source = agentos_state / spec.source
            original = source.read_bytes()

            source.write_bytes(
                original.rstrip(b"\n")
                + f";{spec.claim_key}={spec.claim_value}_mutant\n".encode()
            )
            summary, errors = validate_mainflow_runtime_evidence(agentos_state)
            self.assertFalse(summary["verified"])
            self.assertTrue(any("exact-field assertion failed" in error for error in errors))

            source.write_bytes(original.rstrip(b"\n") + b";status=failed\n")
            summary, errors = validate_mainflow_runtime_evidence(agentos_state)
            self.assertFalse(summary["verified"])
            self.assertTrue(any("source-status assertion failed" in error for error in errors))

            source.write_bytes(original.rstrip(b"\n") + b";unrelated=a=b\n")
            summary, errors = validate_mainflow_runtime_evidence(agentos_state)
            self.assertFalse(summary["verified"])
            self.assertTrue(any("source records are not canonical" in error for error in errors))

    def test_mainflow_host_accepts_guest_multiline_source_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            _plain_state, agentos_state = self._write_state_dirs(root)

            (agentos_state / "rp_agentos_kernel").write_bytes(
                b"target=agentos_ucore\n"
                b"mode=kernel_agent_orchestrated\n"
                b"context_snapshot=present\n"
                b"status=ready\n"
                b"dependency_update=generic_record\n"
                b"dependency_query=generic_record\n"
                b"metadata_query=stage_index\n"
                b"prefetch_hint=dependency_driven\n"
            )
            (agentos_state / "rp_agentos_collab_ack").write_bytes(
                b"agent=sentinel\n"
                b"event=handoff\n"
                b"route=recovery-auditor\n"
                b"delivery=kernel_event_queue\n"
                b"permission_control=sentinel_action_denied\n"
                b"status=ready\n"
            )

            summary, errors = validate_mainflow_runtime_evidence(agentos_state)
            self.assertTrue(summary["verified"])
            self.assertEqual(errors, [])

    def test_mainflow_host_rejects_empty_and_ambiguous_guest_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            _plain_state, agentos_state = self._write_state_dirs(root)

            entry = agentos_state / "rp_agentos_kernel"
            entry.write_bytes(entry.read_bytes() + b"\n")
            summary, errors = validate_mainflow_runtime_evidence(agentos_state)
            entry_row = next(
                row for row in summary["sources"] if row["stage"] == "entry"
            )
            self.assertFalse(summary["verified"])
            self.assertFalse(entry_row["claim_verified"])
            self.assertFalse(entry_row["status_verified"])
            self.assertTrue(
                any("stage entry source records are not canonical" in error for error in errors)
            )

            self._write_backend_runtime_state(agentos_state)
            collaboration = agentos_state / "rp_agentos_collab_ack"
            collaboration.write_bytes(
                b"agent=sentinel\n"
                b"event=handoff=recovery-auditor\n"
                b"delivery=kernel_event_queue\n"
                b"permission_control=sentinel_action_denied\n"
                b"status=ready\n"
            )
            summary, errors = validate_mainflow_runtime_evidence(agentos_state)
            collaboration_row = next(
                row for row in summary["sources"] if row["stage"] == "collaboration"
            )
            self.assertFalse(summary["verified"])
            self.assertTrue(collaboration_row["claim_verified"])
            self.assertTrue(collaboration_row["status_verified"])
            self.assertTrue(
                any(
                    "stage collaboration source records are not canonical" in error
                    for error in errors
                )
            )

    def test_mainflow_host_requires_canonical_telemetry_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            _plain_state, agentos_state = self._write_state_dirs(root)
            mainflow = agentos_state / "rp_agentos_mainflow"
            lines = mainflow.read_text(encoding="ascii").splitlines()
            all_facts = [
                f"{key}={value}"
                for spec in MAINFLOW_RUNTIME_SPECS
                for key, value in spec.telemetry_fields
            ]
            mutations = {
                "missing": lines[:-1],
                "duplicate": lines + [lines[0]],
                "out_of_order": list(reversed(lines)),
                "extra_failed_record": lines
                + ["diagnostic=guest_failure;status=failed"],
                "unknown_field": [lines[0] + ";debug=forged", *lines[1:]],
                "single_line_all_facts": [
                    ";".join(["stage=entry", *all_facts, "status=ready"])
                ],
            }
            for name, mutated in mutations.items():
                with self.subTest(name=name):
                    mainflow.write_bytes(("\n".join(mutated) + "\n").encode())
                    summary, errors = validate_mainflow_runtime_evidence(
                        agentos_state
                    )
                    self.assertFalse(summary["verified"])
                    self.assertNotEqual(errors, [])

    def test_mainflow_host_rejects_source_outside_safe_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            _plain_state, agentos_state = self._write_state_dirs(root)
            (agentos_state / MAINFLOW_RUNTIME_SPECS[0].source).unlink()

            summary, errors = validate_mainflow_runtime_evidence(agentos_state)
            self.assertFalse(summary["verified"])
            self.assertTrue(any("source is missing or unsafe" in error for error in errors))

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
            (agentos_state / "rp_agentcmp").write_bytes(
                evidence.replace("programs_observed=3", "programs_observed=70").encode()
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

            ledger.write_bytes(
                original.replace(
                    "filesystem_domain=3", "filesystem_domain=18446744073709551616", 1
                ).encode()
            )
            _measured, errors = read_program_ledger(
                agentos_state, programs, roles, "agentos"
            )
            self.assertTrue(any("invalid attested domain" in error for error in errors))

    def test_program_ledger_accepts_only_bound_seeded_plain_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, _agentos_state = self._write_state_dirs(root)
            programs, roles, manifest_errors = read_expected_programs(root)
            self.assertEqual(manifest_errors, [])
            ledger = plain_state / "rp_orch_timing"
            seeded = ledger.read_text(encoding="utf-8").replace(
                "orchestrator=rp_orch\nlauncher=fork\n",
                "orchestrator=rp_seed_orch\nlauncher=fork_seeded\n",
            ).replace(";launcher=fork;", ";launcher=fork_seeded;")
            ledger.write_bytes(seeded.encode())
            _measured, errors = read_program_ledger(
                plain_state, programs, roles, "plain", "seeded"
            )
            self.assertEqual(errors, [])

            _measured, errors = read_program_ledger(
                plain_state, programs, roles, "plain"
            )
            self.assertTrue(any("standard profile" in error for error in errors))

            ledger.write_bytes(
                seeded.replace(
                    "orchestrator=rp_seed_orch", "orchestrator=rp_orch", 1
                ).encode()
            )
            _measured, errors = read_program_ledger(
                plain_state, programs, roles, "plain", "seeded"
            )
            self.assertTrue(any("seeded profile" in error for error in errors))

    def test_program_ledger_matches_guest_canonical_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, _agentos_state = self._write_state_dirs(root)
            programs, roles, manifest_errors = read_expected_programs(root)
            self.assertEqual(manifest_errors, [])
            ledger = plain_state / "rp_orch_timing"
            original = ledger.read_bytes()

            lines = original.decode("ascii").splitlines()
            elapsed_prefix = lines[2].rsplit("=", 1)[0] + "="
            lines[2] = elapsed_prefix + "1" * (255 - len(elapsed_prefix))
            ledger.write_bytes(("\n".join(lines) + "\n").encode())
            _measured, errors = read_program_ledger(
                plain_state, programs, roles, "plain"
            )
            self.assertEqual(errors, [])

            mutations = {
                "overlong": ("\n".join(
                    [*lines[:2], elapsed_prefix + "1" * (256 - len(elapsed_prefix)), *lines[3:]]
                ) + "\n").encode(),
                "unicode_digit": original.replace(
                    b"elapsed_ms=1", "elapsed_ms=\u0661".encode(), 1
                ),
                "unicode_separator": original.replace(b"\n", "\u2028".encode(), 1),
                "incomplete_final_record": original[:-1],
            }
            for name, data in mutations.items():
                with self.subTest(name=name):
                    ledger.write_bytes(data)
                    _measured, errors = read_program_ledger(
                        plain_state, programs, roles, "plain"
                    )
                    self.assertNotEqual(errors, [])

    def test_seeded_inventory_is_profile_and_log_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            host = Path(tmp) / "host"
            root.mkdir()
            self._write_minimal_tree(root, host)
            plain_state, agentos_state = self._write_state_dirs(root)
            self._set_seeded_plain_profile(plain_state)
            plain_log, agentos_log = self._write_inventory_logs(
                root, plain_state, agentos_state
            )

            summary = run_check(
                root,
                host,
                require_host=True,
                plain_state_dir=plain_state,
                agentos_state_dir=agentos_state,
                plain_profile="seeded",
                plain_log=plain_log,
                agentos_log=agentos_log,
            )
            self.assertEqual(summary["status"], "ready")

            without_logs = run_check(
                root,
                host,
                require_host=True,
                plain_state_dir=plain_state,
                agentos_state_dir=agentos_state,
                plain_profile="seeded",
            )
            self.assertTrue(
                any("requires QEMU log binding" in item for item in without_logs["failures"])
            )

            plain_log.write_bytes(
                plain_log.read_bytes().replace(b"programs_observed=3", b"programs_observed=4")
            )
            mismatched = run_check(
                root,
                host,
                require_host=True,
                plain_state_dir=plain_state,
                agentos_state_dir=agentos_state,
                plain_profile="seeded",
                plain_log=plain_log,
                agentos_log=agentos_log,
            )
            self.assertTrue(
                any("does not match extracted state" in item for item in mismatched["failures"])
            )

    def test_program_inventory_rejects_noncanonical_evidence(self) -> None:
        mutations = {
            "incomplete_final_record": lambda data: data[:-1],
            "extra_field": lambda data: data.replace(
                b";status=reference_observed", b";extra=1;status=reference_observed", 1
            ),
            "unicode_number": lambda data: data.replace(
                b"programs_observed=3", "programs_observed=\u0663".encode(), 1
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "repo"
                host = Path(tmp) / "host"
                root.mkdir()
                self._write_minimal_tree(root, host)
                plain_state, agentos_state = self._write_state_dirs(root)
                evidence = plain_state / "rp_agentcmp"
                evidence.write_bytes(mutate(evidence.read_bytes()))
                summary = run_check(
                    root,
                    host,
                    require_host=True,
                    plain_state_dir=plain_state,
                    agentos_state_dir=agentos_state,
                )
                self.assertEqual(summary["status"], "failed")
                self.assertTrue(any("program inventory" in item for item in summary["failures"]))

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
            (agentos_state / "rp_agentcmp").write_bytes(
                ("\n".join(records) + "\n").encode()
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
            impersonating = (
                plain_evidence
                + plain_evidence.replace(
                    "evidence_role=demo_reference;evidence_generation=runtime;observation_source=guest_runtime",
                    "evidence_role=runtime_verified;evidence_generation=runtime",
                ).replace("status=reference_observed", "status=verified")
            )
            (plain_state / "rp_agentcmp").write_bytes(impersonating.encode())

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
