#!/usr/bin/env python3
"""来源绑定 Guest 实验提取的回归测试。"""

from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_measured_experiments as extractor

from benchmark_source_contract import (
    FIELD_BINDINGS,
    FIELD_ORDER,
    MARKER_PREFIX,
    _extract_call,
    _split_arguments,
)
from measured_experiments import (
    BENCHMARK_SOURCE,
    MeasurementError,
    extract_file_query_measurements,
    validate_benchmark_source,
    verify_manifest,
    verify_measurement_artifact_set,
    write_csv,
    write_manifest,
)


MARKER = (
    "agentbench_ucore: file_query_benchmark schema=2 unit=us load=143 "
    "traversal_ops=64 traversal_records=512 traversal_duration_us=36 "
    "cold_index_ops=1 cold_index_records=6 cold_index_duration_us=2 "
    "cold_rebuild_records=512 cold_rebuild_included=1 "
    "warm_index_ops=64 warm_index_records=6 warm_index_duration_us=20 "
    "status=measured"
)


def expect_rejected(action, message: str) -> None:
    try:
        action()
    except MeasurementError as error:
        assert message in str(error), error
    else:
        raise AssertionError(f"accepted invalid measurement: {message}")


def mutate_marker_field(source: str, field: str, replacement: str) -> str:
    marker_at = source.index(MARKER_PREFIX)
    call_at = source.rfind("printf(", 0, marker_at)
    opening = call_at + len("printf")
    call = _extract_call(source, opening)
    arguments = _split_arguments(call)
    kind, _ = FIELD_BINDINGS[field]
    if kind == "argument":
        argument_fields = [
            name for name in FIELD_ORDER if FIELD_BINDINGS[name][0] == "argument"
        ]
        arguments[argument_fields.index(field) + 1] = replacement
    else:
        old = f"{field}={FIELD_BINDINGS[field][1]}"
        assert arguments[0].count(old) == 1, (field, arguments[0])
        arguments[0] = arguments[0].replace(old, f"{field}={replacement}", 1)
    closing = opening + len(call) + 1
    return source[: opening + 1] + ", ".join(arguments) + source[closing:]


def mutate_function(source: str, function: str, old: str, new: str) -> str:
    match = re.search(rf"(?m)^static [^;{{]*\b{re.escape(function)}\s*\(", source)
    assert match is not None, function
    start = match.start()
    end = source.find("\nstatic ", start + 1)
    if end < 0:
        end = len(source)
    body = source[start:end]
    assert body.count(old) == 1, (function, old)
    return source[:start] + body.replace(old, new, 1) + source[end:]


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        validate_benchmark_source(BENCHMARK_SOURCE)
        source_text = BENCHMARK_SOURCE.read_text(encoding="utf-8")
        measured_fields = (
            "schema",
            "unit",
            "load",
            "traversal_ops",
            "traversal_records",
            "traversal_duration_us",
            "cold_index_ops",
            "cold_index_records",
            "cold_index_duration_us",
            "cold_rebuild_records",
            "cold_rebuild_included",
            "warm_index_ops",
            "warm_index_records",
            "warm_index_duration_us",
        )
        for field in measured_fields:
            bad_source = root / f"agentbench-hardcoded-{field}.c"
            bad_source.write_text(
                mutate_marker_field(source_text, field, "777"),
                encoding="utf-8",
            )
            expect_rejected(
                lambda path=bad_source: validate_benchmark_source(path),
                (f"benchmark field {field}"
                 if FIELD_BINDINGS[field][0] == "argument"
                 else "benchmark marker fields"),
            )

        formula_source = root / "agentbench-formula.c"
        formula_source.write_text(
            mutate_marker_field(
                source_text,
                "warm_index_duration_us",
                "file_query_receipt.warm_index_duration_us + 1",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(formula_source),
            "benchmark field warm_index_duration_us",
        )

        loop_source = root / "agentbench-short-loop.c"
        loop_source.write_text(
            mutate_function(
                source_text,
                "bench_file_query_traversal_us",
                "for (int i = 0; i < FILE_OPS; i++)",
                "for (int i = 0; i < FILE_OPS - 1; i++)",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(loop_source),
            "bench_file_query_traversal_us",
        )

        untimed_source = root / "agentbench-untimed-cold.c"
        untimed_source.write_text(
            mutate_function(
                source_text,
                "bench_file_query_cold_us",
                "return raw_elapsed_us(start, now_us());",
                "return 1;",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(untimed_source),
            "bench_file_query_cold_us raw duration",
        )

        late_start = root / "agentbench-late-start.c"
        late_start.write_text(
            mutate_function(
                source_text,
                "bench_file_query_cold_us",
                "\tstart = now_us();\n"
                "\tcheck(agent_file_query(&bench_file_query_arg, result) >= 1,\n"
                "\t      \"file query once\");\n"
                "\tcheck(result->total_hits >= 1, \"file query once hits\");",
                "\tcheck(agent_file_query(&bench_file_query_arg, result) >= 1,\n"
                "\t      \"file query once\");\n"
                "\tcheck(result->total_hits >= 1, \"file query once hits\");\n"
                "\tstart = now_us();",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(late_start),
            "timestamps do not enclose the measured kernel query",
        )

        post_return_query = root / "agentbench-post-return-query.c"
        post_return_query.write_text(
            mutate_function(
                source_text,
                "bench_file_query_cold_us",
                "\treturn raw_elapsed_us(start, now_us());",
                "\treturn raw_elapsed_us(start, now_us());\n"
                "\tstart = start;",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(post_return_query),
            "raw duration must be the final statement",
        )

        wrong_warm_count = root / "agentbench-warm-count.c"
        wrong_warm_count.write_text(
            source_text.replace(
                "bench_file_query_warm_us(FILE_OPS,\n"
                "\t\t\t\t\t &bench_scratch.file_query_result)",
                "bench_file_query_warm_us(FILE_OPS - 1,\n"
                "\t\t\t\t\t &bench_scratch.file_query_result)",
                1,
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(wrong_warm_count),
            "receipt field warm_index_duration_us provenance",
        )

        provenance_swaps = (
            (
                "load",
                "\treceipt.load =\n"
                "\t\tbench_scratch.file_query_result.candidate_records;",
                "\treceipt.load =\n"
                "\t\tbench_scratch.file_query_result.scanned_records;",
            ),
            (
                "traversal_records",
                "\treceipt.traversal_records =\n"
                "\t\tbench_scratch.file_query_result.scanned_records;",
                "\treceipt.traversal_records =\n"
                "\t\tbench_scratch.file_query_result.candidate_records;",
            ),
            (
                "cold_index_records",
                "\treceipt.cold_index_records =\n"
                "\t\tbench_scratch.file_query_result.scanned_records;",
                "\treceipt.cold_index_records =\n"
                "\t\tbench_scratch.file_query_result.candidate_records;",
            ),
            (
                "warm_index_records",
                "\treceipt.warm_index_records =\n"
                "\t\tbench_scratch.file_query_result.scanned_records;",
                "\treceipt.warm_index_records =\n"
                "\t\tbench_scratch.file_query_result.candidate_records;",
            ),
            (
                "warm_index_candidates",
                "\treceipt.warm_index_candidates =\n"
                "\t\tbench_scratch.file_query_result.candidate_records;",
                "\treceipt.warm_index_candidates =\n"
                "\t\tbench_scratch.file_query_result.scanned_records;",
            ),
        )
        for field, original, swapped in provenance_swaps:
            swapped_source = root / f"agentbench-swapped-{field}.c"
            swapped_source.write_text(
                mutate_function(
                    source_text, "measure_file_query_paths", original, swapped
                ),
                encoding="utf-8",
            )
            expect_rejected(
                lambda path=swapped_source: validate_benchmark_source(path),
                f"receipt field {field} provenance",
            )

        floored_clock = root / "agentbench-floored-clock.c"
        floored_clock.write_text(
            mutate_function(
                source_text,
                "raw_elapsed_us",
                "return (int)delta;",
                "return delta == 0 ? 1 : (int)delta;",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(floored_clock),
            "raw duration return",
        )

        constant_clock = root / "agentbench-constant-clock.c"
        constant_clock.write_text(
            mutate_function(
                source_text,
                "now_us",
                "return now.sec * 1000000ULL + now.usec;",
                "return 1;",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(constant_clock),
            "microsecond clock value",
        )

        post_overwrite = root / "agentbench-post-overwrite.c"
        post_overwrite.write_text(
            mutate_function(
                source_text,
                "measure_file_query_paths",
                "\treturn receipt;",
                "\treceipt.traversal_duration_us = 1;\n\treturn receipt;",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(post_overwrite),
            "receipt field traversal_duration_us must be assigned exactly once",
        )

        kernel_output_overwrite = root / "agentbench-kernel-output-overwrite.c"
        kernel_output_overwrite.write_text(
            mutate_function(
                source_text,
                "measure_file_query_paths",
                "\treceipt.warm_index_records =\n"
                "\t\tbench_scratch.file_query_result.scanned_records;",
                "\tbench_scratch.file_query_result.scanned_records = 1;\n"
                "\treceipt.warm_index_records =\n"
                "\t\tbench_scratch.file_query_result.scanned_records;",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(kernel_output_overwrite),
            "kernel query scratch output must not be overwritten",
        )

        early_scratch_reuse = root / "agentbench-early-scratch-reuse.c"
        early_scratch_reuse.write_text(
            mutate_function(
                source_text,
                "measure_file_query_paths",
                "\treceipt.load =\n"
                "\t\tbench_scratch.file_query_result.candidate_records;",
                "\tmemset(&bench_scratch.file_query_result, 0,\n"
                "\t       sizeof(bench_scratch.file_query_result));\n"
                "\treceipt.load =\n"
                "\t\tbench_scratch.file_query_result.candidate_records;",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(early_scratch_reuse),
            "snapshotted before scratch reuse",
        )

        early_return = root / "agentbench-early-return.c"
        early_return.write_text(
            mutate_function(
                source_text,
                "measure_file_query_paths",
                "\tmemset(&receipt, 0, sizeof(receipt));",
                "\tmemset(&receipt, 0, sizeof(receipt));\n\tif (receipt.schema == 0) return receipt;",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(early_return),
            "must have one final return",
        )

        early_goto = root / "agentbench-early-goto.c"
        early_goto.write_text(
            mutate_function(
                source_text,
                "measure_file_query_paths",
                "\tmemset(&receipt, 0, sizeof(receipt));",
                "\tmemset(&receipt, 0, sizeof(receipt));\n\tgoto publish_receipt;",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(early_goto),
            "early control-flow escape",
        )

        run_bypass = root / "agentbench-run-bypass.c"
        run_bypass.write_text(
            mutate_function(
                source_text,
                "run_agent_bench",
                "\tconst struct file_query_measurement_receipt file_query_receipt =",
                "\tgoto benchmark_done;\n"
                "\tconst struct file_query_measurement_receipt file_query_receipt =",
            ),
            encoding="utf-8",
        )
        expect_rejected(
            lambda: validate_benchmark_source(run_bypass),
            "early control-flow escape",
        )
        log = root / "logs" / "guest.log"
        log.parent.mkdir()
        log.write_text(
            "boot\n" + MARKER + "\nagentbench_ucore: parent passed\n" + MARKER
            .replace("cold_rebuild_records=512", "cold_rebuild_records=257")
            + "\nagentbench_ucore: parent passed\n",
            encoding="utf-8",
        )
        command = ["make", "run-agent-tests"]
        commit = "a" * 40
        value = extract_file_query_measurements(
            log, "logs/guest.log", command, commit, "run-001"
        )
        assert len(value["rows"]) == 6, value
        assert {row["trial"] for row in value["rows"]} == {1, 2}, value
        assert {row["path"] for row in value["rows"]} == {
            "traversal",
            "cold_index",
            "warm_index",
        }, value
        traversal = next(
            row for row in value["rows"] if row["path"] == "traversal"
        )
        assert traversal["load"] == 143, traversal
        assert traversal["primary_value"] == 512, traversal
        cold = next(row for row in value["rows"] if row["path"] == "cold_index")
        assert cold["duration_unit"] == "us", cold
        assert cold["duration_value"] == 2, cold
        assert cold["rebuild_records"] == 512, cold
        assert {
            row["rebuild_records"] for row in value["rows"] if row["path"] == "cold_index"
        } == {512, 257}, value
        assert cold["source_line"] == 2, cold
        manifest = root / "measurements.json"
        csv_path = root / "measurements.csv"
        write_manifest(manifest, value)
        write_csv(csv_path, value["rows"])
        assert verify_manifest(manifest, root) == value
        assert verify_measurement_artifact_set(
            manifest, csv_path, root, commit, "logs/guest.log"
        ) == value

        publication = root / "private-publication"
        publication.mkdir(mode=0o700)
        publication_log = publication / "dual-targeted-agentbench-guest.log"
        publication_log.write_text(
            MARKER + "\nagentbench_ucore: parent passed\n", encoding="utf-8"
        )
        publication_args = SimpleNamespace(
            guest_log=publication_log,
            source_ref=publication_log.name,
            commit=commit,
            run_id="dual-aaaaaaaaaaaa-g-0123456789abcdef01234567",
            generation="g-0123456789abcdef01234567",
            command_json=json.dumps(command),
            manifest_out=publication / "measured-experiments.json",
            csv_out=publication / "file-query-benchmark.csv",
            receipt_out=publication / "measurement-set.json",
        )
        published = extractor.publish_measurement_set(publication_args)
        assert published["run_id"] == publication_args.run_id
        receipt = json.loads(
            publication_args.receipt_out.read_text(encoding="utf-8")
        )
        assert receipt["status"] == "complete", receipt
        assert receipt["generation"] == publication_args.generation, receipt
        assert receipt["commit"] == commit, receipt
        assert [item["role"] for item in receipt["files"]] == [
            "guest_log",
            "measurement_manifest",
            "measurement_csv",
        ], receipt

        interrupted = root / "interrupted-publication"
        interrupted.mkdir(mode=0o700)
        interrupted_log = interrupted / publication_log.name
        interrupted_log.write_text(
            MARKER + "\nagentbench_ucore: parent passed\n", encoding="utf-8"
        )
        interrupted_args = copy.copy(publication_args)
        interrupted_args.guest_log = interrupted_log
        interrupted_args.manifest_out = interrupted / "measured-experiments.json"
        interrupted_args.csv_out = interrupted / "file-query-benchmark.csv"
        interrupted_args.receipt_out = interrupted / "measurement-set.json"
        original_write_csv = extractor.write_csv
        try:
            def fail_csv(*_args, **_kwargs):
                raise OSError("injected CSV publication failure")

            extractor.write_csv = fail_csv
            try:
                extractor.publish_measurement_set(interrupted_args)
            except OSError as error:
                assert "injected CSV" in str(error), error
            else:
                raise AssertionError("accepted an interrupted measurement publication")
        finally:
            extractor.write_csv = original_write_csv
        assert interrupted_log.is_file()
        assert not interrupted_args.manifest_out.exists()
        assert not interrupted_args.csv_out.exists()
        assert not interrupted_args.receipt_out.exists()

        target_parent = root / "linked-publication-target"
        target_parent.mkdir(mode=0o700)
        linked_parent = root / "linked-publication"
        try:
            linked_parent.symlink_to(target_parent, target_is_directory=True)
        except (NotImplementedError, OSError):
            pass
        else:
            linked_log = target_parent / publication_log.name
            linked_log.write_text(
                MARKER + "\nagentbench_ucore: parent passed\n", encoding="utf-8"
            )
            expect_rejected(
                lambda: extract_file_query_measurements(
                    linked_parent / publication_log.name,
                    publication_log.name,
                    command,
                    commit,
                    "run-linked",
                ),
                "missing or unsafe",
            )
        original_csv = csv_path.read_text(encoding="utf-8")
        csv_path.write_text(original_csv.splitlines()[0] + "\n", encoding="utf-8")
        expect_rejected(
            lambda: verify_measurement_artifact_set(
                manifest, csv_path, root, commit, "logs/guest.log"
            ),
            "CSV differs",
        )
        csv_path.write_text(original_csv, encoding="utf-8")

        tampered = copy.deepcopy(value)
        tampered["rows"][0]["duration_value"] += 1
        write_manifest(root / "tampered.json", tampered)
        expect_rejected(
            lambda: verify_manifest(root / "tampered.json", root),
            "do not match",
        )
        original = log.read_text(encoding="utf-8")
        log.write_text(
            original.replace("traversal_duration_us=36", "traversal_duration_us=35", 1)
        )
        expect_rejected(
            lambda: verify_manifest(manifest, root),
            "source log differs",
        )
        log.write_text(original, encoding="utf-8")

        zero_duration = root / "zero-duration.log"
        zero_duration.write_text(
            MARKER.replace("cold_index_duration_us=2", "cold_index_duration_us=0")
            + "\nagentbench_ucore: parent passed\n",
            encoding="utf-8",
        )
        zero_value = extract_file_query_measurements(
            zero_duration, "zero-duration.log", command, commit, "run-zero"
        )
        zero_cold = next(
            row for row in zero_value["rows"] if row["path"] == "cold_index"
        )
        assert zero_cold["duration_value"] == 0, zero_cold

        excessive_load = root / "excessive-load.log"
        excessive_load.write_text(
            MARKER.replace("load=143", "load=513")
            + "\nagentbench_ucore: parent passed\n",
            encoding="utf-8",
        )
        expect_rejected(
            lambda: extract_file_query_measurements(
                excessive_load, "excessive-load.log", command, commit,
                "run-excessive-load"
            ),
            "load exceeds traversal work",
        )

        excessive_index = root / "excessive-index.log"
        excessive_index.write_text(
            MARKER.replace("cold_index_records=6", "cold_index_records=513")
            .replace("warm_index_records=6", "warm_index_records=513")
            + "\nagentbench_ucore: parent passed\n",
            encoding="utf-8",
        )
        expect_rejected(
            lambda: extract_file_query_measurements(
                excessive_index, "excessive-index.log", command, commit,
                "run-excessive-index"
            ),
            "cold index work exceeds traversal work",
        )

        no_pass = root / "no-pass.log"
        no_pass.write_text(MARKER + "\n", encoding="utf-8")
        expect_rejected(
            lambda: extract_file_query_measurements(
                no_pass, "no-pass.log", command, commit, "run-002"
            ),
            "not followed by a pass marker",
        )
        child_only = root / "child-only.log"
        child_only.write_text(
            MARKER + "\nagentbench_ucore: passed\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: extract_file_query_measurements(
                child_only, "child-only.log", command, commit, "run-child-only"
            ),
            "not followed by a pass marker",
        )
        synthetic = root / "synthetic.log"
        synthetic.write_text(
            MARKER.replace("status=measured", "status=synthetic")
            + "\nagentbench_ucore: parent passed\n",
            encoding="utf-8",
        )
        expect_rejected(
            lambda: extract_file_query_measurements(
                synthetic, "synthetic.log", command, commit, "run-003"
            ),
            "not measured",
        )

    print("test_measured_experiments: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
