#!/usr/bin/env python3
"""No-QEMU regressions for the shared dual-platform backend contract."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend_evidence_contract import (
    ContractError,
    parse_log,
    summary,
    validate_source,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "plain": ROOT / "baseline_ucore" / "user" / "src" / "rp_backend.c",
    "agentos": ROOT / "user" / "src" / "rp_backend.c",
}
MARKERS = {
    "plain": (
        "rp_backend: evidence_role=demo_reference "
        "catalog_generation=demo_expected cases=7 status=reference_ready"
    ),
    "agentos": (
        "rp_backend: evidence_generation=runtime runtime_cases=8 "
        "source_reads=8 kernel_checks=4 context_sequence=41 "
        "query_returned=3 query_used_index=1 status=verified"
    ),
}


class BackendEvidenceContractTests(unittest.TestCase):
    def test_repository_sources_and_summaries_share_the_contract(self) -> None:
        for target, source in SOURCES.items():
            validate_source(target, source)
        self.assertEqual(
            summary("plain"),
            "plain backend reference: expected_cases=7 runtime_cases=0",
        )
        self.assertEqual(
            summary("agentos"),
            "AgentOS backend runtime: cases=8 source_reads=8 kernel_checks=4",
        )

    def test_plain_reference_consumers_do_not_promote_it_to_runtime_ready(self) -> None:
        consumers = (
            ROOT / "baseline_ucore" / "user" / "src" / "rp_consistency.c",
            ROOT / "baseline_ucore" / "user" / "src" / "rp_coherenceplane.c",
        )
        for consumer in consumers:
            source = consumer.read_text(encoding="utf-8")
            self.assertNotIn(
                '("rp_backend_exec", "status=ready")',
                source,
                consumer.name,
            )
            for field in (
                "evidence_file_role=demo_reference",
                "evidence_file_generation=demo_expected",
                "evidence_file_status=reference_ready",
            ):
                self.assertIn(
                    f'("rp_backend_exec", "{field}")',
                    source,
                    consumer.name,
                )

        report_consumers = (
            ROOT / "baseline_ucore" / "user" / "src" / "rp_decsupport.c",
            ROOT / "baseline_ucore" / "user" / "src" / "rp_reldossier.c",
        )
        for consumer in report_consumers:
            source = consumer.read_text(encoding="utf-8")
            self.assertNotIn("runner_report_rows=7", source, consumer.name)
            self.assertIn("reference_report_rows=7", source, consumer.name)

    def test_exact_guest_markers_are_parsed_not_substring_matched(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for target, marker in MARKERS.items():
                log = root / f"{target}.log"
                log.write_text(f"boot\n{marker}\ndone\n", encoding="utf-8")
                values = parse_log(target, log)
                self.assertEqual(values["cases"], 7 if target == "plain" else 8)
                self.assertIn(f"cases={values['cases']}", summary(target, values["cases"]))

                for suffix in (" trailing", f"\n{marker}"):
                    log.write_text(f"{marker}{suffix}\n", encoding="utf-8")
                    with self.assertRaises(ContractError):
                        parse_log(target, log)

    def test_wrong_counts_and_unmeasured_runtime_values_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mutations = {
                "plain": MARKERS["plain"].replace("cases=7", "cases=8"),
                "agentos": MARKERS["agentos"].replace("context_sequence=41", "context_sequence=0"),
            }
            for target, marker in mutations.items():
                log = root / f"bad-{target}.log"
                log.write_text(marker + "\n", encoding="utf-8")
                with self.assertRaises(ContractError):
                    parse_log(target, log)
            with self.assertRaises(ContractError):
                summary("agentos", 7)

    def test_plain_reference_cannot_claim_runtime_pass_or_performance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plain = SOURCES["plain"].read_text(encoding="utf-8")
            for index, forged in enumerate(
                ("runner_case=fake;result=passed", "input_check=pass", "ticks=3")
            ):
                bad_plain = root / f"plain-{index}.c"
                bad_plain.write_text(
                    plain.replace('"runtime_pass_rows=0\\n"', f'"{forged}\\n"', 1),
                    encoding="utf-8",
                )
                with self.assertRaises(ContractError):
                    validate_source("plain", bad_plain)

    def test_plain_reference_file_envelope_is_complete_and_unique(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plain = SOURCES["plain"].read_text(encoding="utf-8")
            mutations = (
                plain.replace('"evidence_file_role=demo_reference\\n"', "", 1),
                plain.replace(
                    '"evidence_file_status=reference_ready\\n"',
                    '"evidence_file_status=reference_ready\\n"'
                    '"evidence_file_status=reference_ready\\n"',
                    1,
                ),
            )
            for index, mutation in enumerate(mutations):
                bad_plain = root / f"plain-envelope-{index}.c"
                bad_plain.write_text(mutation, encoding="utf-8")
                with self.assertRaises(ContractError):
                    validate_source("plain", bad_plain)

    def test_agentos_receipt_binding_and_control_flow_mutations_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agentos = SOURCES["agentos"].read_text(encoding="utf-8")
            mutations = {
                "marker-hardcode": agentos.replace(
                    "backend_receipt.runtime_cases, backend_receipt.source_reads,",
                    "8, backend_receipt.source_reads,",
                    1,
                ),
                "post-overwrite": agentos.replace(
                    '\tprintf("rp_backend: evidence_generation=runtime ',
                    "\tbackend_receipt.runtime_cases = 8;\n"
                    '\tprintf("rp_backend: evidence_generation=runtime ',
                    1,
                ),
                "early-goto": agentos.replace(
                    "\tconst struct backend_runtime_receipt backend_receipt =",
                    "\tgoto forged_marker;\n\tconst struct backend_runtime_receipt backend_receipt =",
                    1,
                ),
                "early-success": agentos.replace(
                    "int main(void)\n{",
                    "int main(void)\n{\n\treturn 0;",
                    1,
                ),
                "constant-receipt": agentos.replace(
                    "receipt.source_reads = backend_runtime_source_reads;",
                    "receipt.source_reads = 8;",
                    1,
                ),
                "forged-source-count": agentos.replace(
                    "backend_runtime_source_reads++;",
                    "backend_runtime_source_reads = 8;",
                    1,
                ),
                "post-source-count": agentos.replace(
                    "backend_runtime_source_reads++;",
                    "backend_runtime_source_reads++;\n\tbackend_runtime_source_reads = 8;",
                    1,
                ),
                "kernel-output-overwrite": agentos.replace(
                    "\tbackend_runtime_kernel_checks++;\n\tif (context_snapshot",
                    "\tbackend_runtime_kernel_checks++;\n"
                    "\tbackend_result.status = AGENT_STATUS_OK;\n"
                    "\tif (context_snapshot",
                    1,
                ),
                "source-read-bypass": agentos.replace(
                    "rp_evidence_measure_file_field(spec->source, spec->key, spec->value,",
                    "rp_evidence_accept_file_field(spec->source, spec->key, spec->value,",
                    1,
                ),
                "kernel-call-bypass": agentos.replace(
                    "agent_run(&backend_op, &backend_result, 1, 0)",
                    "1",
                    1,
                ),
            }
            printf_binding_mutations = {
                "printf-global-counter": agentos.replace(
                    "backend_receipt.runtime_cases, backend_receipt.source_reads,",
                    "backend_runtime_cases, backend_receipt.source_reads,",
                    1,
                ),
                "printf-swapped-fields": agentos.replace(
                    "backend_receipt.kernel_checks, backend_receipt.context_sequence,",
                    "backend_receipt.context_sequence, backend_receipt.kernel_checks,",
                    1,
                ),
                "printf-wrong-query-field": agentos.replace(
                    "backend_receipt.query_used_index);",
                    "backend_receipt.query_scanned);",
                    1,
                ),
            }
            mutations.update(printf_binding_mutations)
            for name, mutated in mutations.items():
                self.assertNotEqual(mutated, agentos, name)
                path = root / f"agentos-{name}.c"
                path.write_text(mutated, encoding="utf-8")
                with self.assertRaises(ContractError, msg=name):
                    validate_source("agentos", path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
