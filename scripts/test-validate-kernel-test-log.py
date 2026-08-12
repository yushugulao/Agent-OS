#!/usr/bin/env python3
"""已完全排空的专用内核日志契约单元测试。"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("validate-kernel-test-log.py")
SPEC = importlib.util.spec_from_file_location("kernel_log_validator", MODULE_PATH)
validator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(validator)


def eevdf_metric_fixture_lines():
    lines = ["agent_eevdf_ucore: ordinary_baseline_ticks=12 progress=100"]
    fresh_generation = 0
    service_sequence = 0
    cohorts = {
        1: (1, 1, 0, 0, 0, 0, 1, 1, 0, 0),
        16: (16, 16, 12, 12, 0, 0, 4, 4, 12, 15),
        4: (4, 4, 0, 0, 0, 0, 1, 1, 3, 0),
        44: (4, 4, 0, 0, 0, 0, 1, 1, 3, 0),
    }
    scenario_samples = {
        1: ((0, "bootstrap", 1),),
        16: tuple(
            (index, "bootstrap" if index % 4 == 0 else "fresh", 1)
            for index in range(16)
        ),
        4: ((0, "bootstrap", 1),)
        + tuple((index, "fresh", 1) for index in range(1, 4)),
        44: (
            (0, "bootstrap", 1),
            (1, "fresh", 4),
            (2, "fresh", 1),
            (3, "fresh", 1),
        ),
    }

    for scenario in (1, 16, 4, 44):
        services = {}
        scaled_services = []
        deadline_misses = 0
        dispatches = 0
        fallbacks = 0
        fresh_samples = 0
        for index, source, threads in scenario_samples[scenario]:
            if source == "bootstrap":
                lifecycle = "1:1"
                wake_probes = 0
            else:
                fresh_generation += 1
                lifecycle = f"2:{fresh_generation}"
                wake_probes = 4
                fresh_samples += 1
            service_sequence += 1
            service = service_sequence * 1024
            services[index] = service
            scaled_services.append(service_sequence)
            dispatch = 1
            fallback = 0
            deadline_miss = 0
            dispatches += dispatch
            fallbacks += fallback
            deadline_misses += deadline_miss
            lines.append(
                "agent_eevdf_ucore: sample "
                f"scenario={scenario} index={index} source={source} "
                f"threads={threads} wake_probes={wake_probes} mode=1 flags=0 "
                "latency_class=0 weight=1024 request_ticks=1 "
                f"lifecycle={lifecycle} work={100 + service_sequence} "
                f"service={service} dispatch={dispatch} fallback={fallback} "
                f"deadline_miss={deadline_miss} wake_samples={wake_probes} "
                f"wake_max={1 if wake_probes else 0}"
            )
        (
            requested,
            admitted,
            rejected,
            no_space,
            retry,
            other,
            waves,
            bootstrap_samples,
            expected_fresh,
            initial_attempts,
        ) = cohorts[scenario]
        lines.append(
            "agent_eevdf_ucore: cohort "
            f"scenario={scenario} requested={requested} admitted={admitted} "
            f"rejected={rejected} no_space={no_space} retry={retry} "
            f"other={other} waves={waves} concurrency_cap=4 "
            f"bootstrap_samples={bootstrap_samples} "
            f"fresh_samples={expected_fresh} "
            f"initial_fresh_attempts={initial_attempts} ordinary_progress=0"
        )
        lines.append(
            "agent_eevdf_ucore: jain_inputs "
            f"scenario={scenario} n={admitted} sum={sum(scaled_services)} "
            f"sum_sq={sum(value * value for value in scaled_services)} "
            "basis=service_cycles_div_1024"
        )
        bucket_zero = fresh_samples * 4
        percentile = 0 if fresh_samples else 4
        lines.append(
            "agent_eevdf_ucore: wake "
            f"scenario={scenario} scope=fresh_agents_only "
            f"fresh_samples={fresh_samples} buckets={bucket_zero},0,0,0 "
            f"p50_bucket={percentile} p99_bucket={percentile} "
            f"deadline_miss={deadline_misses} dispatch={dispatches} "
            f"fallback={fallbacks}"
        )
        if scenario == 44:
            lines.append(
                "agent_eevdf_ucore: amplification_inputs "
                f"amplified_threads=4 amplified_service={services[1]} "
                "peer_threads=1 fresh_peer_count=2 bootstrap_peer_count=1 "
                f"peer_count=3 peer_service_sum="
                f"{services[0] + services[2] + services[3]} "
                "accounting=workflow"
            )
    return tuple(lines)


EEVDF_METRIC_FIXTURE_LINES = eevdf_metric_fixture_lines()


class KernelLogValidatorTests(unittest.TestCase):
    def test_cli_rejects_passed_only_proc_and_generic_logs(self):
        cases = (
            ("proc-reap", "[proc-reap] both targets passed\n", ""),
            ("generic", "fsenospc_ucore: parent passed\n",
             "fsenospc_ucore: parent passed"),
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "passed-only.log"
            for profile, text, marker in cases:
                with self.subTest(profile=profile):
                    path.write_text(text, encoding="utf-8")
                    command = [
                        sys.executable, str(MODULE_PATH), "--log-file", str(path),
                        "--tag", "mutation", "--profile", profile,
                    ]
                    if marker:
                        command.extend(("--marker", marker))
                    result = subprocess.run(
                        command, text=True, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, check=False,
                    )
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("[mutation] profile validation failed:", result.stderr)

    def test_proc_reap_rejects_completion_only_logs(self):
        standard = "\n".join(validator.PROC_REAP_MARKERS)
        adversarial = "\n".join(validator.PROC_REAP_AGENT_MARKERS)
        self.assertIn("standard", validator.validate_proc_reap(standard))
        self.assertIn("adversarial", validator.validate_proc_reap(adversarial))
        with self.assertRaisesRegex(validator.ValidationError, "exactly one"):
            validator.validate_proc_reap("[proc-reap] both targets passed\n")
        with self.assertRaisesRegex(validator.ValidationError, "complete line"):
            validator.validate_proc_reap(validator.PROC_REAP_MARKERS[-1])

    def test_wait_atomic_contract_requires_exact_ordered_evidence(self):
        text = "\n".join(validator.WAIT_ATOMIC_MARKERS)
        self.assertIn("positions=", validator.validate_wait_atomic(text))
        with self.assertRaisesRegex(validator.ValidationError, "complete line"):
            validator.validate_wait_atomic(
                text.replace(validator.WAIT_ATOMIC_MARKERS[0], "")
            )
        with self.assertRaisesRegex(validator.ValidationError, "complete line"):
            validator.validate_wait_atomic(
                text + "\n" + validator.WAIT_ATOMIC_MARKERS[0]
            )
        with self.assertRaisesRegex(validator.ValidationError, "out of order"):
            validator.validate_wait_atomic(
                "\n".join(reversed(validator.WAIT_ATOMIC_MARKERS))
            )

        handoff = "agentfinal_ucore: event_wake_handoff waiters=1,4,8,15 wakeups=28 herd=0"
        self.assertIn(handoff, validator.WAIT_ATOMIC_MARKERS)
        for forged in (f"forged {handoff}", f"{handoff} forged"):
            with self.subTest(forged=forged), self.assertRaisesRegex(
                validator.ValidationError, "complete line"
            ):
                validator.validate_wait_atomic(text.replace(handoff, forged))

    def test_ch3_trace_requires_exact_unique_ordered_guest_lines(self):
        text = "boot noise\n" + "\n".join(validator.CH3_TRACE_MARKERS) + "\nexit noise\n"
        self.assertIn("positions=", validator.validate_ch3_trace(text))

        for marker in validator.CH3_TRACE_MARKERS:
            with self.subTest(missing=marker), self.assertRaisesRegex(
                validator.ValidationError, "complete line"
            ):
                validator.validate_ch3_trace(text.replace(marker + "\n", "", 1))
            for forged in (f"forged {marker}", f"{marker} forged"):
                with self.subTest(forged=forged), self.assertRaisesRegex(
                    validator.ValidationError, "complete line"
                ):
                    validator.validate_ch3_trace(text.replace(marker, forged, 1))

        with self.assertRaisesRegex(validator.ValidationError, "complete line"):
            validator.validate_ch3_trace(text + validator.CH3_TRACE_MARKERS[-1] + "\n")
        with self.assertRaisesRegex(validator.ValidationError, "out of order"):
            validator.validate_ch3_trace("\n".join(reversed(validator.CH3_TRACE_MARKERS)))

    def test_agent_case_contract_rejects_completion_without_mechanism_facts(self):
        case = "agentsecurity_ucore"
        text = "\n".join(
            (*validator.AGENT_CASE_MARKERS[case], f"{case}: parent passed")
        )
        self.assertIn(f"case={case}", validator.validate_agent_case(text, case))
        with self.assertRaisesRegex(validator.ValidationError, "complete line"):
            validator.validate_agent_case(f"{case}: parent passed", case)

        context = "\n".join(
            dict.fromkeys((
                "agentfinal_ucore: context_sync_atomic=1 append=1 rollback=1 clear=1 recovery=1",
                *validator.WAIT_ATOMIC_MARKERS,
                *validator.AGENT_CASE_MARKERS["agentfinal_ucore"],
            ))
        )
        self.assertIn(
            "context_sync=1",
            validator.validate_agent_case(context, "agentfinal_ucore", True),
        )
        with self.assertRaisesRegex(validator.ValidationError, "atomicity marker"):
            validator.validate_agent_case(
                context.replace("context_sync_atomic=1", "context_sync_atomic=0"),
                "agentfinal_ucore",
                True,
            )

    def test_ch8_agent_case_requires_its_real_completion_marker(self):
        case = "ch8_cow_ucore"
        completion = "ch8_cow_ucore: passed"
        self.assertIn(
            f"case={case}", validator.validate_agent_case(completion, case)
        )
        for mutated in (
            "ch8_cow_ucore: parent passed",
            completion + "\n" + completion,
            completion + " forged",
        ):
            with self.subTest(mutated=mutated), self.assertRaisesRegex(
                validator.ValidationError, "complete line"
            ):
                validator.validate_agent_case(mutated, case)

    def test_contract_scheduler_and_task_cases_require_all_exact_markers(self):
        for case in (
            "agentcontract_ucore",
            "agent_eevdf_ucore",
            "agenttask_ucore",
        ):
            markers = validator.AGENT_CASE_MARKERS[case]
            completion = f"{case}: parent passed"
            dynamic = ()
            if case == "agent_eevdf_ucore":
                dynamic = EEVDF_METRIC_FIXTURE_LINES
            elif case == "agenttask_ucore":
                dynamic = (
                    "agenttask_ucore: perf path=batch operations=16 syscalls=1 abi_descriptor_bytes=3584 copied_descriptor_bytes=3584 dispatch_header_bytes=0 control_abi_bytes=0 control_copied_bytes=0 service_start_interval_tick_p50=0 service_start_interval_tick_p99=1 service_start_span_ticks=1 sequence_elapsed_ticks=1 sched_dispatch_delta=0",
                    "agenttask_ucore: perf path=scalar_v3 operations=16 syscalls=16 abi_descriptor_bytes=12288 copied_descriptor_bytes=12288 dispatch_header_bytes=128 control_abi_bytes=0 control_copied_bytes=0 service_start_interval_tick_p50=0 service_start_interval_tick_p99=2 service_start_span_ticks=2 sequence_elapsed_ticks=2 sched_dispatch_delta=1",
                    "agenttask_ucore: perf path=sq_cq operations=16 syscalls=2 abi_descriptor_bytes=4096 copied_descriptor_bytes=4096 dispatch_header_bytes=0 control_abi_bytes=336 control_copied_bytes=544 service_start_interval_tick_p50=0 service_start_interval_tick_p99=1 service_start_span_ticks=1 sequence_elapsed_ticks=1 sched_dispatch_delta=0",
                    "agenttask_ucore: cancel_latency scope=retained_terminal metric=service_tick ticks=0 enter_calls=1 pending_provider=unavailable observer_syscalls=2",
                )
            text = "\n".join((*markers, *dynamic, completion))
            with self.subTest(case=case):
                self.assertIn(
                    f"case={case}", validator.validate_agent_case(text, case)
                )
                for marker in markers:
                    with self.subTest(case=case, missing=marker):
                        with self.assertRaisesRegex(
                            validator.ValidationError, "complete line"
                        ):
                            validator.validate_agent_case(
                                text.replace(marker + "\n", "", 1), case
                            )

    def test_eevdf_metrics_reject_dishonest_topology_and_aggregation(self):
        case = "agent_eevdf_ucore"
        text = "\n".join(
            (
                *validator.AGENT_CASE_MARKERS[case],
                *EEVDF_METRIC_FIXTURE_LINES,
                f"{case}: parent passed",
            )
        )

        self.assertIn(f"case={case}", validator.validate_agent_case(text, case))
        clamped_service = text.replace(
            "work=101 service=1024", "work=101 service=1", 1
        )
        self.assertNotEqual(clamped_service, text)
        self.assertIn(
            f"case={case}",
            validator.validate_agent_case(clamped_service, case),
        )
        jain_line = (
            "agent_eevdf_ucore: jain_inputs scenario=16 n=16 "
            "sum=152 sum_sq=1784 basis=service_cycles_div_1024"
        )
        cohort_line = (
            "agent_eevdf_ucore: cohort scenario=16 requested=16 admitted=16 "
            "rejected=12 no_space=12 retry=0 other=0 waves=4 "
            "concurrency_cap=4 bootstrap_samples=4 fresh_samples=12 "
            "initial_fresh_attempts=15 ordinary_progress=0"
        )
        mutations = (
            text.replace(
                "scenario=16 index=4 source=bootstrap threads=1 "
                "wake_probes=0 mode=1 flags=0 latency_class=0 weight=1024 "
                "request_ticks=1 lifecycle=1:1",
                "scenario=16 index=4 source=bootstrap threads=1 "
                "wake_probes=0 mode=1 flags=0 latency_class=0 weight=1024 "
                "request_ticks=1 lifecycle=1:2",
                1,
            ),
            text.replace("scope=fresh_agents_only", "scope=all_agents", 1),
            text.replace(
                "scenario=16 requested=16 admitted=16 rejected=12 "
                "no_space=12",
                "scenario=16 requested=16 admitted=16 rejected=11 "
                "no_space=11",
                1,
            ),
            text.replace(
                "scenario=16 scope=fresh_agents_only fresh_samples=12",
                "scenario=16 scope=fresh_agents_only fresh_samples=11",
                1,
            ),
            text.replace(
                "scenario=44 index=1 source=fresh threads=4",
                "scenario=44 index=1 source=fresh threads=1",
                1,
            ),
            text.replace(
                "scenario=16 requested=16 admitted=16 rejected=12 "
                "no_space=12 retry=0",
                "scenario=16 requested=16 admitted=16 rejected=12 "
                "no_space=12 retry=1",
                1,
            ),
            text.replace(jain_line + "\n", "", 1),
            text.replace(jain_line, jain_line + "\n" + jain_line, 1),
            text.replace("scenario=16 n=16 sum=152", "scenario=16 n=15 sum=152", 1),
            text.replace("scenario=16 n=16 sum=152", "scenario=16 n=16 sum=153", 1),
            text.replace("sum=152 sum_sq=1784", "sum=152 sum_sq=1785", 1),
            text.replace(
                "basis=service_cycles_div_1024",
                "basis=service_cycles",
                1,
            ),
            text.replace(
                cohort_line + "\n" + jain_line,
                jain_line + "\n" + cohort_line,
                1,
            ),
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated):
                self.assertNotEqual(mutated, text)
                with self.assertRaises(validator.ValidationError):
                    validator.validate_agent_case(mutated, case)

    def test_task_metrics_reject_dishonest_or_inexact_accounting(self):
        case = "agenttask_ucore"
        markers = validator.AGENT_CASE_MARKERS[case]
        metrics = (
            "agenttask_ucore: perf path=batch operations=16 syscalls=1 abi_descriptor_bytes=3584 copied_descriptor_bytes=3584 dispatch_header_bytes=0 control_abi_bytes=0 control_copied_bytes=0 service_start_interval_tick_p50=0 service_start_interval_tick_p99=1 service_start_span_ticks=1 sequence_elapsed_ticks=1 sched_dispatch_delta=0",
            "agenttask_ucore: perf path=scalar_v3 operations=16 syscalls=16 abi_descriptor_bytes=12288 copied_descriptor_bytes=12288 dispatch_header_bytes=128 control_abi_bytes=0 control_copied_bytes=0 service_start_interval_tick_p50=0 service_start_interval_tick_p99=2 service_start_span_ticks=2 sequence_elapsed_ticks=2 sched_dispatch_delta=1",
            "agenttask_ucore: perf path=sq_cq operations=16 syscalls=2 abi_descriptor_bytes=4096 copied_descriptor_bytes=4096 dispatch_header_bytes=0 control_abi_bytes=336 control_copied_bytes=544 service_start_interval_tick_p50=0 service_start_interval_tick_p99=1 service_start_span_ticks=1 sequence_elapsed_ticks=1 sched_dispatch_delta=0",
            "agenttask_ucore: cancel_latency scope=retained_terminal metric=service_tick ticks=0 enter_calls=1 pending_provider=unavailable observer_syscalls=2",
        )
        completion = f"{case}: parent passed"
        text = "\n".join((*markers, *metrics, completion))

        self.assertIn(f"case={case}", validator.validate_agent_case(text, case))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "agenttask.log"
            path.write_text(text, encoding="utf-8")
            command = [
                sys.executable,
                str(MODULE_PATH),
                "--log-file",
                str(path),
                "--tag",
                "agenttask",
                "--profile",
                "agent-case",
                "--case",
                case,
            ]
            result = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("profile validation passed: case=agenttask_ucore", result.stdout)
        mutations = (
            text.replace("operations=16 syscalls=1", "operations=15 syscalls=1", 1),
            text.replace(
                "abi_descriptor_bytes=12288 copied_descriptor_bytes=12288",
                "abi_descriptor_bytes=7680 copied_descriptor_bytes=7680",
                1,
            ),
            text.replace("dispatch_header_bytes=128", "dispatch_header_bytes=0", 1),
            text.replace("control_copied_bytes=544", "control_copied_bytes=336", 1),
            text.replace("service_start_interval_tick_p99=2 service_start_span_ticks=2", "service_start_interval_tick_p99=3 service_start_span_ticks=2", 1),
            text.replace("service_start_span_ticks=2 sequence_elapsed_ticks=2", "service_start_span_ticks=2 sequence_elapsed_ticks=1", 1),
            text.replace("scope=retained_terminal", "scope=running", 1),
            text + "\n" + metrics[0].replace("syscalls=1", "syscalls=99", 1),
            text + "\n" + metrics[3].replace("ticks=0", "ticks=forged", 1),
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated):
                with self.assertRaises(validator.ValidationError):
                    validator.validate_agent_case(mutated, case)

    def test_usersafety_argv_contract_requires_exact_unique_evidence(self):
        case = "usersafety_ucore"
        mechanism = validator.AGENT_CASE_MARKERS[case][0]
        completion = f"{case}: parent passed"
        text = mechanism + "\n" + completion

        self.assertIn(f"case={case}", validator.validate_agent_case(text, case))
        mutations = (
            completion,
            mechanism.replace("over_limit_rejected=1", "over_limit_rejected=0")
            + "\n"
            + completion,
            mechanism + "\n" + mechanism + "\n" + completion,
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated):
                with self.assertRaisesRegex(validator.ValidationError, "complete line"):
                    validator.validate_agent_case(mutated, case)

    def test_iobudget_rejects_stale_child_stdio_mutex(self):
        case = "iobudget_ucore"
        text = "\n".join(
            (*validator.AGENT_CASE_MARKERS[case], f"{case}: parent passed")
        )

        self.assertIn(f"case={case}", validator.validate_agent_case(text, case))
        with self.assertRaisesRegex(validator.ValidationError, "stale stdio"):
            validator.validate_agent_case(
                text + "\n[ERROR 5-0]Unexpected mutex id 0", case
            )

    def test_agentscope_rejects_missing_or_zero_peer_fairness_evidence(self):
        case = "agentscope_ucore"
        fairness = (
            "agentscope_ucore: observe_fairness_workset=32 "
            "span_preemptions=1 timeline_preemptions=3 "
            "provenance_preemptions=5 peer_turns_delta=6"
        )
        bounded = (
            "agentscope_ucore: observe_query_bounded=1 "
            "context=128 loops=1 preemptions=9"
        )
        completion = f"{case}: parent passed"
        text = "\n".join(
            (*validator.AGENT_CASE_MARKERS[case], fairness, bounded, completion)
        )

        self.assertIn(f"case={case}", validator.validate_agent_case(text, case))
        mutations = (
            text.replace(fairness + "\n", "", 1),
            text.replace(fairness, fairness + "\n" + fairness, 1),
            text.replace("observe_fairness_workset=32", "observe_fairness_workset=31"),
            text.replace("observe_fairness_workset=32", "observe_fairness_workset=513"),
            text.replace("span_preemptions=1", "span_preemptions=0"),
            text.replace("timeline_preemptions=3", "timeline_preemptions=0"),
            text.replace("provenance_preemptions=5", "provenance_preemptions=0"),
            text.replace("peer_turns_delta=6", "peer_turns_delta=0"),
            text.replace(bounded + "\n", "", 1),
            text.replace(bounded, bounded + "\n" + bounded, 1),
            text.replace("context=128", "context=127", 1),
            text.replace("loops=1", "loops=0", 1),
            text.replace("preemptions=9", "preemptions=10", 1),
            text.replace(fairness, fairness + " forged", 1),
            text.replace(fairness + "\n" + bounded, bounded + "\n" + fairness),
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated):
                self.assertNotEqual(mutated, text)
                with self.assertRaises(validator.ValidationError):
                    validator.validate_agent_case(mutated, case)

    def test_thread_contract_checks_order_uniqueness_and_counts(self):
        lines = [
            (
                marker
                if marker != "threadresource_ucore: domain_fairness=1"
                else marker + " hog=575 victim=512 bound=576"
            )
            for marker in validator.THREAD_MARKERS
        ]
        self.assertIn("hog=575", validator.validate_thread("\n".join(lines)))

        with self.assertRaisesRegex(
            validator.ValidationError, "not unique"
        ):
            validator.validate_thread("\n".join(lines + [lines[0]]))
        with self.assertRaisesRegex(
            validator.ValidationError, "fairness mismatch"
        ):
            validator.validate_thread(
                "\n".join(lines).replace("hog=575", "hog=577")
            )

    def test_file_contract_checks_all_seven_markers(self):
        text = "\n".join(validator.FILE_MARKERS)
        self.assertIn("positions=", validator.validate_file(text))
        with self.assertRaisesRegex(validator.ValidationError, "missing"):
            validator.validate_file(text.replace(validator.FILE_MARKERS[2], ""))

    def test_physical_contract_requires_real_receipts(self):
        lines = [
            (
                marker
                if marker
                != "physicalresource_ucore: reserved_domain_fairness=1"
                else marker
                + " pressure_pages=8 pressure_pipes=2 physical_usage=48 physical_limit=48"
            )
            for marker in validator.PHYSICAL_RESOURCE_MARKERS
        ]
        lines = [
            line.replace(
                "physicalresource_ucore: reserved_promise_lifecycle=1",
                "physicalresource_ucore: reserved_promise_lifecycle=1 promised=64 limit=64",
            )
            for line in lines
        ]
        raw = (
            [(0, 2, 0)]
            + [(0, 0, 0)] * 4
            + [(-1, 0, 0)] * 3
            + [(0, 1, 1), (0, 0, 0), (0, 16, 64)]
            + [(0, 0, 0)] * 5
            + [(0, 64, 64), (-1, 0, 0), (0, 2, 0), (-1, 0, 0)]
            + [(0, 3, 0), (-1, 0, 0), (0, 0, 3), (-1, 0, 0)]
            + [(0, 0, 0), (0, 16, 64), (0, 0, 0), (0, 64, 64)]
            + [(0, 0, 0), (0, 16, 64)]
        )
        raw_lines = [
            "physicalresource_ucore: raw "
            f"step={step} result={result} value0={value0} value1={value1}"
            for step, (result, value0, value1) in enumerate(raw, 1)
        ]
        lines[1:1] = raw_lines
        text = "\n".join(lines)
        self.assertIn("usage=48", validator.validate_physical_resource(text))
        with self.assertRaisesRegex(validator.ValidationError, "complete line"):
            validator.validate_physical_resource(
                text.replace(
                    "physicalresource_ucore: system_reserve=1",
                    "FORGED physicalresource_ucore: system_reserve=1 SUFFIX",
                )
            )
        without_raw = [line for line in lines if line not in raw_lines]
        relocated = "\n".join([*without_raw, *raw_lines])
        with self.assertRaisesRegex(validator.ValidationError, "contiguous"):
            validator.validate_physical_resource(relocated)

    def test_workflow_teardown_contract_uses_complete_ordered_lines(self):
        lines = list(validator.workflow_teardown_expected_lines(14, 64))
        text = "\n".join(lines)
        self.assertIn(
            "cycles=65",
            validator.validate_workflow_teardown(text, 14, 64),
        )
        with self.assertRaisesRegex(validator.ValidationError, "once"):
            validator.validate_workflow_teardown(
                text + "\n" + validator.WORKFLOW_TEARDOWN_MARKERS[0],
                14,
                64,
            )
        with self.assertRaisesRegex(validator.ValidationError, "out of order"):
            validator.validate_workflow_teardown(
                "\n".join(
                    (
                        lines[1],
                        lines[0],
                        *lines[2:],
                    )
                ),
                14,
                64,
            )
        with self.assertRaisesRegex(validator.ValidationError, "once"):
            validator.validate_workflow_teardown(
                text.replace(validator.WORKFLOW_TEARDOWN_MARKERS[4], ""),
                14,
                64,
            )
        with self.assertRaisesRegex(validator.ValidationError, "once"):
            validator.validate_workflow_teardown(
                text.replace(validator.WORKFLOW_TEARDOWN_MARKERS[5], ""),
                14,
                64,
            )
        old_exec_marker = validator.WORKFLOW_TEARDOWN_MARKERS[0].replace(
            " physical_io_charged=1 normal_class=1", ""
        )
        with self.assertRaisesRegex(validator.ValidationError, "once"):
            validator.validate_workflow_teardown(
                text.replace(
                    validator.WORKFLOW_TEARDOWN_MARKERS[0], old_exec_marker
                ),
                14,
                64,
            )
        with self.assertRaisesRegex(validator.ValidationError, "once"):
            validator.validate_workflow_teardown(
                text.replace(validator.WORKFLOW_TEARDOWN_MARKERS[2], ""),
                14,
                64,
            )
        with self.assertRaisesRegex(
            validator.ValidationError, "capacity mismatch"
        ):
            validator.validate_workflow_teardown(
                text,
                14,
                63,
            )

    def test_workflow_teardown_fixture_order_comes_from_validator_sequence(self):
        lines = validator.workflow_teardown_expected_lines(14, 64)
        slot = validator.WORKFLOW_TEARDOWN_SEQUENCE.index(
            validator.WORKFLOW_TEARDOWN_CAPACITY_SLOT
        )
        capacity = (
            validator.WORKFLOW_TEARDOWN_CAPACITY_PREFIX
            + "65 domain_cap=14 global_reserved_cap=64"
        )
        self.assertEqual(lines.count(capacity), 1)
        self.assertEqual(lines[slot], capacity)
        self.assertEqual(
            lines[:slot] + lines[slot + 1 :],
            validator.WORKFLOW_TEARDOWN_MARKERS,
        )

    def test_syscall_contract_checks_phase_and_short_marker_order(self):
        lines = []
        for name, begin, peer, end in validator.SYSCALL_PHASES:
            lines.extend((begin, peer))
            if name == "inode":
                lines.append("SYSCALLFAIR_INODE_SHORT")
            elif name == "trunc":
                lines.append(
                    "syscallfair_ucore: "
                    "truncate_preemptions=3 peer_progress=1"
                )
            lines.append(end)
        lines.append("syscallfair_ucore: parent passed")
        text = "\n".join(lines)
        self.assertIn("trunc=", validator.validate_syscall(text))

        with self.assertRaisesRegex(
            validator.ValidationError, "completion marker mismatch"
        ):
            validator.validate_syscall(
                text + "\nsyscallfair_ucore: parent passed"
            )

        for field, replacement in (
            ("truncate_preemptions=3", "truncate_preemptions=0"),
            ("peer_progress=1", "peer_progress=0"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                validator.ValidationError, "lacked a real"
            ):
                validator.validate_syscall(text.replace(field, replacement))

        metric = (
            "syscallfair_ucore: truncate_preemptions=3 peer_progress=1"
        )
        with self.assertRaisesRegex(
            validator.ValidationError, "must occur once"
        ):
            validator.validate_syscall(text + "\n" + metric)

    def test_fs_domain_and_reserve_contracts_preserve_boundaries(self):
        domain = list(validator.FS_QUOTA_MARKERS)
        domain.append("fsquota_ucore: quota_reuse=1")
        domain_text = "\n".join(domain)
        domain_text = domain_text.replace(
            "fsquota_ucore: public_version_churn=1",
            "fsquota_ucore: public_version_churn=1 cycles=513",
        ).replace(
            "fsquota_ucore: public_domain_limited=1",
            "fsquota_ucore: public_domain_limited=1 blocks=15 inodes=8",
        )
        domain_text += "\nfsquota_ucore: parent passed"
        self.assertIn(
            "domain blocks=15",
            validator.validate_fs(
                domain_text,
                "domain",
                "fsquota_ucore: parent passed",
            ),
        )
        for invalid in (
            "blocks=16 inodes=8",
            "blocks=15 inodes=7",
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                validator.ValidationError, "domain boundary mismatch"
            ):
                validator.validate_fs(
                    domain_text.replace("blocks=15 inodes=8", invalid),
                    "domain",
                    "fsquota_ucore: parent passed",
                )

        reserve_text = "\n".join(validator.FS_QUOTA_MARKERS)
        reserve_text = reserve_text.replace(
            "fsquota_ucore: public_version_churn=1",
            "fsquota_ucore: public_version_churn=1 cycles=700",
        ).replace(
            "fsquota_ucore: public_domain_limited=1",
            "fsquota_ucore: public_domain_limited=1 blocks=64 inodes=16",
        )
        reserve_text += "\nfsquota_ucore: parent passed"
        self.assertIn(
            "reserve blocks=64",
            validator.validate_fs(
                reserve_text,
                "reserve",
                "fsquota_ucore: parent passed",
            ),
        )

    def test_fs_generic_rejects_parent_passed_without_exhaustion_facts(self):
        text = "\n".join(validator.FS_GENERIC_MARKERS)
        self.assertEqual(
            validator.validate_fs(text, "generic", validator.FS_GENERIC_MARKERS[-1]),
            "generic",
        )
        with self.assertRaisesRegex(validator.ValidationError, "complete line"):
            validator.validate_fs(
                validator.FS_GENERIC_MARKERS[-1],
                "generic",
                validator.FS_GENERIC_MARKERS[-1],
            )

    def test_fs_persistent_profiles_preserve_exact_values_and_order(self):
        seed = "\n".join(
            (
                "fspquota_ucore: sponsored_object_charged=1 blocks=14",
                (
                    "fspquota_ucore: durable_fixture=1 blocks=18 "
                    "inodes=8 owner_exited=1"
                ),
            )
        )
        self.assertEqual(
            validator.validate_fs(
                seed,
                "persistent-seed",
                "fspquota_ucore: durable_fixture=1",
            ),
            "persistent-seed",
        )

        marker = "fspquota_ucore: parent passed"
        verify = "\n".join(validator.FS_PERSISTENT_MARKERS + (marker,))
        self.assertEqual(
            validator.validate_fs(verify, "persistent-verify", marker),
            "persistent-verify",
        )
        with self.assertRaisesRegex(validator.ValidationError, "out of order"):
            validator.validate_fs(
                "\n".join(
                    (
                        validator.FS_PERSISTENT_MARKERS[1],
                        validator.FS_PERSISTENT_MARKERS[0],
                        *validator.FS_PERSISTENT_MARKERS[2:],
                        marker,
                    )
                ),
                "persistent-verify",
                marker,
            )


if __name__ == "__main__":
    unittest.main()
