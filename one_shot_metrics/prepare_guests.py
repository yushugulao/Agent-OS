#!/usr/bin/env python3
"""Prepare deterministic one-shot metric Guest sources.

The canonical Guests remain untouched.  This tool copies exact, instrumented
variants into a caller-provided application directory and records the source
and output digests used for the campaign.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Callable


ALLOWED_HIT_COUNTS = (1, 2, 4, 8)
CANONICAL_GUESTS = (
    "agenteval_ucore.c",
    "agenttask_ucore.c",
    "agent_eevdf_ucore.c",
)


class TransformError(RuntimeError):
    """Raised when a canonical source no longer matches an exact needle."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _replace_exact(
    text: str,
    old: str,
    new: str,
    *,
    label: str,
    expected: int = 1,
) -> str:
    count = text.count(old)
    if count != expected:
        raise TransformError(
            f"{label}: expected {expected} exact source needle(s), found {count}"
        )
    return text.replace(old, new)


def _transform_agenteval(text: str, hit_count: int) -> str:
    text = _replace_exact(
        text,
        "#define EVAL_PAIRS 7\n",
        (
            "#define EVAL_PAIRS 15\n"
            f"#define FIGURE_HIT_COUNT {hit_count}\n"
        ),
        label="agenteval pair count",
    )

    start_needle = "static void run_file_query_table_ablation(int load)\n{"
    end_needle = "\n}\n\nstatic int path_operations_for_load"
    if text.count(start_needle) != 1 or text.count(end_needle) != 1:
        raise TransformError("agenteval table ablation function boundary drifted")
    start = text.index(start_needle)
    end = text.index(end_needle, start) + 2
    block = text[start:end]
    if block.count("EVAL_FILE_QUERIES") != 17:
        raise TransformError(
            "agenteval table ablation: expected 17 operation-count needles"
        )
    transformed = block.replace("EVAL_FILE_QUERIES", "FIGURE_HIT_COUNT")
    return text[:start] + transformed + text[end:]


def _transform_agenttask(text: str, _hit_count: int) -> str:
    text = _replace_exact(
        text,
        "#define TOOL_V3_DISPATCH_HEADER_BYTES (2U * sizeof(unsigned int))\n",
        (
            "#define TOOL_V3_DISPATCH_HEADER_BYTES (2U * sizeof(unsigned int))\n"
            "#define FIGURE_ROUNDS 8U\n"
            "#define FIGURE_PATH_COUNT 3U\n"
        ),
        label="agenttask figure constants",
    )
    text = _replace_exact(
        text,
        "struct ablation_metrics {\n\tuint64 start_tick;\n",
        (
            "struct ablation_metrics {\n"
            "\tuint64 start_us;\n"
            "\tuint64 end_us;\n"
            "\tuint64 sequence_elapsed_us;\n"
            "\tuint64 start_tick;\n"
        ),
        label="agenttask microsecond metric fields",
    )
    text = _replace_exact(
        text,
        "static uint64 next_request_id = 100000;\n",
        (
            "static uint64 next_request_id = 100000;\n"
            "static uint figure_round;\n"
            "static uint figure_order_slot;\n"
        ),
        label="agenttask round metadata",
    )

    check_function = """static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agenttask_ucore: check failed: %s\\n", message);
		exit(1);
	}
}
"""
    now_function = check_function + """
static uint64 figure_now_us(void)
{
	TimeVal now;

	check(sys_get_time(&now, 0) == 0,
	      "sample performance sequence microsecond clock");
	return now.sec * 1000000ULL + now.usec;
}
"""
    text = _replace_exact(
        text,
        check_function,
        now_function,
        label="agenttask clock helper insertion",
    )

    begin_end = """static void ablation_begin(struct ablation_metrics *metrics)
{
	struct agent_info info;

	memset(metrics, 0, sizeof(*metrics));
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0,
	      "sample performance sequence start boundary tick");
	metrics->start_tick = info.current_tick;
	metrics->last_service_start_tick = info.current_tick;
	metrics->start_dispatch_count = info.sched_dispatch_count;
}

static void ablation_end(struct ablation_metrics *metrics)
{
	struct agent_info info;

	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0 &&
	      info.current_tick >= metrics->start_tick &&
	      info.sched_dispatch_count >= metrics->start_dispatch_count,
	      "sample monotonic performance sequence end counters");
	metrics->sequence_elapsed_ticks = info.current_tick - metrics->start_tick;
	metrics->dispatch_delta =
		info.sched_dispatch_count - metrics->start_dispatch_count;
}
"""
    instrumented_begin_end = """static void ablation_begin(struct ablation_metrics *metrics)
{
	struct agent_info info;

	memset(metrics, 0, sizeof(*metrics));
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0,
	      "sample performance sequence start boundary tick");
	metrics->start_tick = info.current_tick;
	metrics->last_service_start_tick = info.current_tick;
	metrics->start_dispatch_count = info.sched_dispatch_count;
	metrics->start_us = figure_now_us();
}

static void ablation_end(struct ablation_metrics *metrics)
{
	struct agent_info info;

	metrics->end_us = figure_now_us();
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0 &&
	      info.current_tick >= metrics->start_tick &&
	      info.sched_dispatch_count >= metrics->start_dispatch_count &&
	      metrics->end_us >= metrics->start_us,
	      "sample monotonic performance sequence end counters");
	metrics->sequence_elapsed_us = metrics->end_us - metrics->start_us;
	metrics->sequence_elapsed_ticks = info.current_tick - metrics->start_tick;
	metrics->dispatch_delta =
		info.sched_dispatch_count - metrics->start_dispatch_count;
}
"""
    text = _replace_exact(
        text,
        begin_end,
        instrumented_begin_end,
        label="agenttask sequence boundary instrumentation",
    )

    report_tail = """\t       metrics->sequence_elapsed_ticks, metrics->dispatch_delta);
}
"""
    instrumented_report_tail = """\t       metrics->sequence_elapsed_ticks, metrics->dispatch_delta);
	printf("agenttask_ucore: one_shot_sequence schema=1 boot_round=%u "
	       "order=%u path=%s operations=%u syscalls=%u "
	       "start_us=%llu end_us=%llu "
	       "duration_us=%llu service_start_span_ticks=%llu "
	       "sequence_elapsed_ticks=%llu sched_dispatch_delta=%llu "
	       "status=measured\\n",
	       figure_round, figure_order_slot, path, PERF_OPERATION_COUNT, syscalls,
	       metrics->start_us, metrics->end_us, metrics->sequence_elapsed_us,
	       metrics->service_start_span_ticks,
	       metrics->sequence_elapsed_ticks, metrics->dispatch_delta);
	for (uint i = 0; i < PERF_OPERATION_COUNT; i++)
		printf("agenttask_ucore: one_shot_op schema=1 boot_round=%u "
		       "order=%u path=%s operation_index=%u "
		       "service_start_interval_tick=%llu "
		       "status=measured\\n",
		       figure_round, figure_order_slot, path, i,
		       metrics->service_start_tick_intervals[i]);
}
"""
    text = _replace_exact(
        text,
        report_tail,
        instrumented_report_tail,
        label="agenttask raw row emission",
    )

    text = _replace_exact(
        text,
        "static void run_child(int mode)\n",
        "static void run_child(int mode, uint round, uint order_slot)\n",
        label="agenttask child signature",
    )
    text = _replace_exact(
        text,
        "\tint status = 0;\n\n\tpid = create_isolated_workflow();\n",
        (
            "\tint status = 0;\n\n"
            "\tfigure_round = round;\n"
            "\tfigure_order_slot = order_slot;\n"
            "\tpid = create_isolated_workflow();\n"
        ),
        label="agenttask child round assignment",
    )
    text = _replace_exact(
        text,
        """\trun_child(CHILD_BATCH);
\trun_child(CHILD_SCALAR);
\trun_child(CHILD_TASK_PERF);
\trun_child(CHILD_TASK_STRESS);
""",
        """\tfor (uint round = 0; round < FIGURE_ROUNDS; round++) {
\t\tconst int modes[FIGURE_PATH_COUNT] = {
\t\t\tCHILD_BATCH, CHILD_SCALAR, CHILD_TASK_PERF
\t\t};

\t\tfor (uint slot = 0; slot < FIGURE_PATH_COUNT; slot++)
\t\t\trun_child(modes[(round + slot) % FIGURE_PATH_COUNT],
\t\t\t\t  round + 1U, slot);
\t}
\trun_child(CHILD_TASK_STRESS, 0, 0);
""",
        label="agenttask rotated campaign rounds",
    )
    return text


def _transform_eevdf(text: str, _hit_count: int) -> str:
    text = _replace_exact(
        text,
        """\tuint64 total = 0;
\tint wait_status = AGENT_STATUS_OK;
""",
        """\tuint64 total = 0;
\tuint64 last_wakeup_dispatch = 0;
\tint wait_status = AGENT_STATUS_OK;
""",
        label="EEVDF wake capture storage",
    )

    probe_loop = """\tfor (int i = 0; i < WAKE_PROBES; i++) {
\t\tstruct agent_event event;

\t\tmemset(&event, 0, sizeof(event));
\t\twait_status = agent_wait(&event, 1);
\t\tcheck(wait_status == AGENT_STATUS_TIMEOUT,
\t\t      "deadline wake probe times out");
\t}
"""
    exact_probe_loop = """\tfor (int i = 0; i < WAKE_PROBES; i++) {
\t\tstruct agent_sched_record records[8];
\t\tstruct agent_event event;
\t\tuint histogram_bucket;
\t\tint record_count;
\t\tint selected = -1;

\t\tmemset(&event, 0, sizeof(event));
\t\twait_status = agent_wait(&event, 1);
\t\tcheck(wait_status == AGENT_STATUS_TIMEOUT,
\t\t      "deadline wake probe times out");
\t\tmemset(records, 0, sizeof(records));
\t\trecord_count = agent_sched_snapshot(records, 8);
\t\tcheck(record_count > 0 && record_count <= 8,
\t\t      "deadline wake probe scheduler snapshot");
\t\tfor (int record = record_count - 1; record >= 0; record--) {
\t\t\tif (records[record].dispatch_count > last_wakeup_dispatch &&
\t\t\t    (records[record].reason_flags &
\t\t\t     AGENT_SCHED_REASON_DEADLINE_NOW) != 0) {
\t\t\t\tselected = record;
\t\t\t\tbreak;
\t\t\t}
\t\t}
\t\tcheck(selected >= 0,
\t\t      "deadline wake probe has a new deadline-now dispatch");
\t\tlast_wakeup_dispatch = records[selected].dispatch_count;
\t\tif (records[selected].ready_age <= 1)
\t\t\thistogram_bucket = 0;
\t\telse if (records[selected].ready_age <= 2)
\t\t\thistogram_bucket = 1;
\t\telse if (records[selected].ready_age <= 8)
\t\t\thistogram_bucket = 2;
\t\telse
\t\t\thistogram_bucket = 3;
\t\tprintf("agent_eevdf_ucore: one_shot_wakeup schema=1 "
\t\t       "scenario=%u index=%u probe=%u wakeup_latency_ticks=%llu "
\t\t       "dispatch_tick=%llu reason_flags=%llu histogram_bucket=%u "
\t\t       "status=measured\\n",
\t\t       next_spawn.scenario, next_spawn.index, i,
\t\t       records[selected].ready_age, records[selected].tick,
\t\t       records[selected].reason_flags, histogram_bucket);
\t}
"""
    text = _replace_exact(
        text,
        probe_loop,
        exact_probe_loop,
        label="EEVDF exact post-timeout snapshots",
    )
    text = _replace_exact(
        text,
        """\t       "wake_samples=%llu wake_max=%llu\\n",
""",
        """\t       "wake_samples=%llu wake_max=%llu "
\t       "wake_bucket_0=%llu wake_bucket_1=%llu "
\t       "wake_bucket_2=%llu wake_bucket_3=%llu\\n",
""",
        label="EEVDF sample wake bucket fields",
    )
    text = _replace_exact(
        text,
        """\t       result->deadline_misses, result->wakeup_samples,
\t       result->max_wakeup_ticks);
""",
        """\t       result->deadline_misses, result->wakeup_samples,
\t       result->max_wakeup_ticks, result->wakeup_buckets[0],
\t       result->wakeup_buckets[1], result->wakeup_buckets[2],
\t       result->wakeup_buckets[3]);
""",
        label="EEVDF sample wake bucket arguments",
    )
    text = _replace_exact(
        text,
        """\trun_simple_scenario(1, 0, 0);
\trun_sixteen_arrivals();
""",
        """\trun_simple_scenario(1, 0, 0);
\trun_simple_scenario(2, 1, 0);
\trun_simple_scenario(3, 2, 0);
\trun_sixteen_arrivals();
""",
        label="EEVDF concurrency sweep scenarios",
    )
    return text


TRANSFORMS: dict[str, Callable[[str, int], str]] = {
    "agenteval_ucore.c": _transform_agenteval,
    "agenttask_ucore.c": _transform_agenttask,
    "agent_eevdf_ucore.c": _transform_eevdf,
}


def prepare(repo_root: Path, app_dir: Path, hit_count: int) -> Path:
    if hit_count not in ALLOWED_HIT_COUNTS:
        raise TransformError(
            f"hit count must be one of {', '.join(map(str, ALLOWED_HIT_COUNTS))}"
        )

    repo_root = repo_root.resolve()
    source_dir = (repo_root / "user" / "src").resolve()
    app_dir = app_dir.resolve()
    if app_dir == source_dir:
        raise TransformError("refusing to write transformed Guests into user/src")
    app_dir.mkdir(parents=True, exist_ok=True)

    manifest_files: list[dict[str, object]] = []
    prepared: list[tuple[Path, bytes]] = []
    for filename in CANONICAL_GUESTS:
        source_path = source_dir / filename
        try:
            source_bytes = source_path.read_bytes()
            source_text = source_bytes.decode("utf-8")
        except FileNotFoundError as exc:
            raise TransformError(f"missing canonical Guest: {source_path}") from exc
        except UnicodeDecodeError as exc:
            raise TransformError(f"canonical Guest is not valid UTF-8: {source_path}") from exc

        transformed_text = TRANSFORMS[filename](source_text, hit_count)
        transformed_bytes = transformed_text.encode("utf-8")
        output_path = app_dir / filename
        if output_path.resolve() == source_path.resolve():
            raise TransformError(f"refusing to overwrite canonical Guest: {source_path}")
        prepared.append((output_path, transformed_bytes))
        manifest_files.append(
            {
                "output": filename,
                "source": f"user/src/{filename}",
                "source_bytes": len(source_bytes),
                "source_sha256": _sha256(source_bytes),
                "transformed_bytes": len(transformed_bytes),
                "transformed_sha256": _sha256(transformed_bytes),
            }
        )

    for output_path, transformed_bytes in prepared:
        output_path.write_bytes(transformed_bytes)

    manifest = {
        "figure_hit_count": hit_count,
        "generator": "one_shot_metrics/prepare_guests.py",
        "guest_schema": 1,
        "files": manifest_files,
    }
    manifest_path = app_dir / "guest-manifest.json"
    manifest_path.write_bytes(
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("ascii")
    )
    return manifest_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "app_dir",
        type=Path,
        help="output application directory for the transformed Guest sources",
    )
    parser.add_argument(
        "--hit-count",
        required=True,
        type=int,
        choices=ALLOWED_HIT_COUNTS,
        help="table-ablation operation count; path/index measurements are unchanged",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root containing user/src (defaults to this script's parent)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        manifest_path = prepare(args.repo_root, args.app_dir, args.hit_count)
    except TransformError as exc:
        raise SystemExit(f"prepare_guests.py: {exc}") from exc
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
