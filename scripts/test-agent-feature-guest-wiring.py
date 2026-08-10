#!/usr/bin/env python3
"""Freeze release routing for the contract, EEVDF, and Task Channel Guests."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
USER_MAKEFILE = ROOT / "user" / "Makefile"
EXEC_MANIFEST = ROOT / "user" / "include" / "exec_policy_manifest.h"
RUNNER = ROOT / "scripts" / "run-agent-tests.sh"
VALIDATOR = ROOT / "scripts" / "validate-kernel-test-log.py"
EVIDENCE_FIXTURE = ROOT / "host_tools" / "test_capture_final_evidence.py"
AGENTTASK_SOURCE = ROOT / "user" / "src" / "agenttask_ucore.c"
EEVDF_SOURCE = ROOT / "user" / "src" / "agent_eevdf_ucore.c"

GUESTS = (
    "agentcontract_ucore",
    "agent_eevdf_ucore",
    "agenttask_ucore",
)

MARKERS = {
    "agentcontract_ucore": (
        "agentcontract_ucore: dag24=1 lifecycle=1 schema=1 capability=1",
        "agentcontract_ucore: dependency_sequence=1 provenance_file=1 provenance_cross_agent=1",
        "agentcontract_ucore: planned_effect=1 unplanned_effect_denied=1 evidence=1",
        "agentcontract_ucore: replay=1 retry=1 deadline=1 phase_atomic=1 phase_zero_leak=1",
        "agentcontract_ucore: legacy_v2=1 enforce_bypass_denied=1",
    ),
    "agent_eevdf_ucore": (
        "agent_eevdf_ucore: topology one_way=bootstrap four_way=bootstrap+3fresh amplification=bootstrap_peer+fresh4thread+2fresh_peers",
        "agent_eevdf_ucore: wake_bucket_map=0:le1,1:le2,2:le8,3:gt8 p50_p99=histogram_approx probes=fresh_agents_only",
        "agent_eevdf_ucore: thread_amplification scenario=44 amplified_threads=4 fresh_peers=2 bootstrap_peers=1 accounting=workflow",
        "agent_eevdf_ucore: sixteen_arrivals=1 logical_samples=16 concurrency_cap=4 bootstrap_samples=4 fresh_samples=12 initial_fresh_attempts=15 initial_admitted=3 stable_no_space=12 waves=4 retry_policy=retry_only",
    ),
    "agenttask_ucore": (
        "agenttask_ucore: perf_contract=steady_state_n16 quantiles=nearest_rank sample_semantics=pre_effect_context_service_start interval_origin=sequence_start_boundary service_metric=service_start_tick_intervals sequence_metric=agent_info_boundary_elapsed_ticks wall_clock=unavailable raw_cycles=not_claimed syscall_source=guest_call_sites",
        "agenttask_ucore: perf_observers=agent_info:2 boundary_overhead=start_return+end_entry_included context_query:16 post_sequence_excluded=1 kernel_path_syscall_counter=unavailable",
        "agenttask_ucore: perf_excluded batch=lifecycle_info:1 scalar_v3=lifecycle_info:1+contract:2 sq_cq=lifecycle_info:1+contract:2+channel_setup:1",
        "agenttask_ucore: sq_cq_copy_scope=sqe_private_copy+cqe_publish ack_clear_bytes=2048 user_ring_descriptor_bytes=4096 setup_abi_control_bytes=160 setup_copied_control_bytes=256",
        "agenttask_ucore: provider=synchronous_echo running_cancel_latency=unavailable terminal_pending_saturation=unavailable",
        "agenttask_ucore: perf_fp path=batch value=31",
        "agenttask_ucore: perf_fp path=scalar_v3 value=31",
        "agenttask_ucore: perf_fp path=sq_cq value=31",
        "agenttask_ucore: cq_full=1 backpressure=1 pending_preserved=1 recovery_enter_calls=2 resync_recovery=1",
        "agenttask_ucore: setup=1 single_issuer=1 resource_import_denied=1",
        "agenttask_ucore: submit=1 cq_ack=1 monotonic=1 resync=1",
        "agenttask_ucore: target_cancel_exactly_once=1 hard_deadline=1",
        "agenttask_ucore: batch_fp=31 scalar_v3_fp=31 task_fp=31",
    ),
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def make_assignment(text: str, variable: str) -> tuple[str, ...]:
    lines = text.splitlines()
    prefix = f"{variable} :="
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        logical = line[len(prefix) :].strip()
        while logical.endswith("\\"):
            logical = logical[:-1] + " " + lines[index + 1].strip()
            index += 1
        return tuple(logical.split())
    raise AssertionError(f"missing Make assignment: {variable}")


class AgentFeatureGuestWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.makefile = read(USER_MAKEFILE)
        cls.manifest = read(EXEC_MANIFEST)
        cls.runner = read(RUNNER)
        cls.validator = read(VALIDATOR)
        cls.fixture = read(EVIDENCE_FIXTURE)
        cls.agenttask_source = read(AGENTTASK_SOURCE)
        cls.eevdf_source = read(EEVDF_SOURCE)

    def test_agent_chapter_contains_each_guest_once(self) -> None:
        agent_tests = make_assignment(self.makefile, "AGENT_TESTS")
        for guest in GUESTS:
            with self.subTest(guest=guest):
                self.assertEqual(agent_tests.count(guest), 1)

    def test_exec_manifest_uses_only_exercised_roles(self) -> None:
        normalized = re.sub(r"\s+", " ", re.sub(r"\\\s*", " ", self.manifest))
        expected = {
            "agentcontract_ucore": (
                'X("agentcontract_ucore", "agentcontract_ucore", '
                "EXEC_MANIFEST_F_BOOT_SEALED, "
                "EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR) | "
                "EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_SENTINEL), 0, "
                "EXEC_MANIFEST_VFS_PROFILE_WORKFLOW)"
            ),
            "agent_eevdf_ucore": (
                'X("agent_eevdf_ucore", "agent_eevdf_ucore", '
                "EXEC_MANIFEST_F_BOOT_SEALED, "
                "EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), 0, "
                "EXEC_MANIFEST_VFS_PROFILE_CONTENT_READ)"
            ),
            "agenttask_ucore": (
                'X("agenttask_ucore", "agenttask_ucore", '
                "EXEC_MANIFEST_F_BOOT_SEALED, "
                "EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), 0, "
                "EXEC_MANIFEST_VFS_PROFILE_CONTENT_READ)"
            ),
        }
        for guest, entry in expected.items():
            with self.subTest(guest=guest):
                self.assertIn(entry, normalized)
                start = normalized.index(f'X("{guest}",')
                end = normalized.find(' X("', start + 2)
                manifest_entry = normalized[start : end if end >= 0 else None]
                self.assertNotIn("EXEC_MANIFEST_ROLE_ALL", manifest_entry)

    def test_runner_routes_each_case_and_freezes_calibration_count(self) -> None:
        for guest in GUESTS:
            with self.subTest(guest=guest):
                self.assertEqual(
                    self.runner.count(
                        f'run_case {guest} "{guest}: parent passed"'
                    ),
                    1,
                )
                self.assertIn(f"\t{guest})", self.runner)
        self.assertIn(
            'if [[ "${calibration_case_ordinal}" != "21" ]]; then',
            self.runner,
        )
        self.assertIn(
            "calibration did not execute exactly 21 cases", self.runner
        )

    def test_exact_markers_match_guest_runner_and_validator(self) -> None:
        for guest, markers in MARKERS.items():
            source = read(ROOT / "user" / "src" / f"{guest}.c")
            joined_source = re.sub(r'"\s*"', "", source)
            for marker in markers:
                with self.subTest(guest=guest, marker=marker):
                    if marker.endswith(
                        "batch_fp=31 scalar_v3_fp=31 task_fp=31"
                    ):
                        quoted = (
                            '"agenttask_ucore: batch_fp=%u scalar_v3_fp=%u '
                            'task_fp=%u\\n"'
                        )
                        self.assertEqual(source.count(quoted), 1)
                        semantic_bits = (
                            "SEMANTIC_STATUS_OK",
                            "SEMANTIC_TOOL_ECHO",
                            "SEMANTIC_CONTEXT_PROOF",
                            "SEMANTIC_EVIDENCE_PROOF",
                            "SEMANTIC_ZERO_PAYLOAD",
                        )
                        for bit, name in enumerate(semantic_bits):
                            self.assertRegex(
                                source,
                                rf"(?m)^#define {name}\s+\(1U << {bit}\)$",
                            )
                    elif marker.startswith("agenttask_ucore: perf_fp path="):
                        path = marker.split("path=", 1)[1].split(" ", 1)[0]
                        quoted = (
                            f'"agenttask_ucore: perf_fp path={path} '
                            'value=%u\\n"'
                        )
                        self.assertEqual(source.count(quoted), 1)
                    else:
                        quoted = f'"{marker}\\n"'
                        self.assertEqual(joined_source.count(quoted), 1)
                    self.assertEqual(self.runner.count(f'"{marker}"'), 1)
                    self.assertEqual(self.validator.count(f'"{marker}"'), 1)
            self.assertEqual(source.count(f'"{guest}: parent passed\\n"'), 1)

    def test_agenttask_perf_uses_fresh_workflows_with_bounded_retry(self) -> None:
        source = self.agenttask_source
        self.assertRegex(
            source,
            r"(?m)^#define PERF_OPERATION_COUNT\s+16U$",
        )
        self.assertEqual(
            source.count(
                "agent_workflow_create(AGENT_ROLE_ORCHESTRATOR)"
            ),
            1,
        )
        self.assertNotIn("agent_create_role(", source)
        self.assertIn(
            "for (int attempt = 0; attempt < 2000; attempt++)", source
        )
        self.assertIn("sleep(1);", source)
        self.assertIn("pid = create_isolated_workflow();", source)
        self.assertEqual(source.count("run_child(CHILD_"), 4)
        self.assertIn("pre_effect_context_service_start", source)
        self.assertIn("interval_origin=sequence_start_boundary", source)
        self.assertIn("service_start_tick_intervals", source)
        self.assertIn("agent_info_boundary_elapsed_ticks", source)
        self.assertIn(
            "boundary_overhead=start_return+end_entry_included", source
        )
        self.assertIn("quantiles=nearest_rank", source)
        self.assertIn("running_cancel_latency=unavailable", source)
        self.assertIn("terminal_pending_saturation=unavailable", source)

    def test_eevdf_guest_reserves_bootstrap_slot_and_freezes_topology(self) -> None:
        source = self.eevdf_source
        main = source[source.index("int main(void)") :]

        self.assertRegex(
            source,
            r"(?m)^#define MAX_CONCURRENT_WORKFLOWS 4$",
        )
        self.assertRegex(
            source,
            r"(?m)^#define MAX_FRESH_WORKFLOWS "
            r"\(MAX_CONCURRENT_WORKFLOWS - 1\)$",
        )
        self.assertRegex(
            source,
            r"(?m)^#define INITIAL_FRESH_ATTEMPTS "
            r"\(MAX_SCENARIO_WORKFLOWS - 1\)$",
        )
        self.assertIn(
            "_Static_assert(MAX_FRESH_WORKFLOWS == 3,", source
        )
        self.assertIn(
            "_Static_assert(INITIAL_FRESH_ATTEMPTS == 15,", source
        )
        self.assertRegex(
            source,
            r"(?m)^#define SCHEDULER_TICK_MILLISECONDS 10$",
        )
        self.assertRegex(
            source,
            r"(?m)^#define MEASUREMENT_TICKS 12$",
        )
        self.assertIn(
            "_Static_assert(MEASUREMENT_MILLISECONDS == 120,", source
        )
        ordered_calls = (
            '"ordinary worker reached its measurement deadline"',
            "run_simple_scenario(1, 0, 0);",
            "run_sixteen_arrivals();",
            "run_simple_scenario(4, MAX_FRESH_WORKFLOWS, 0);",
            "run_simple_scenario(44, MAX_FRESH_WORKFLOWS, 1);",
        )
        positions = [main.index(call) for call in ordered_calls]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("run_simple_scenario(4, 4", main)
        self.assertNotIn("run_simple_scenario(44, 4", main)

    def test_eevdf_guest_retries_only_transient_admission(self) -> None:
        source = self.eevdf_source
        retry = source[
            source.index("static int spawn_workflow_retry") :
            source.index("static uint latency_percentile_bucket")
        ]
        arrivals = source[
            source.index("static void run_sixteen_arrivals") :
            source.index("int main(void)")
        ]

        self.assertIn("if (pid != AGENT_STATUS_RETRY)", retry)
        self.assertNotIn("AGENT_STATUS_NO_SPACE", retry)
        self.assertIn(
            "for (uint i = 0; i < INITIAL_FRESH_ATTEMPTS; i++)",
            arrivals,
        )
        self.assertIn(
            "check(admitted == MAX_FRESH_WORKFLOWS && summary.rejected == 12,",
            arrivals,
        )
        self.assertIn(
            "check(summary.rejected_no_space == 12 &&", arrivals
        )
        self.assertIn("summary.rejected_retry == 0", arrivals)
        self.assertIn("summary.rejected_other == 0", arrivals)
        self.assertIn("summary.bootstrap_samples == 4", arrivals)
        self.assertIn("summary.fresh_samples == 12", arrivals)

    def test_eevdf_guest_handshakes_workers_and_scopes_wake_probes(self) -> None:
        source = self.eevdf_source
        summary_add = source[
            source.index("static void summary_add") :
            source.index("static void print_summary")
        ]

        for field in ("worker_started", "worker_paused"):
            with self.subTest(field=field):
                self.assertIn(f"static volatile int {field}", source)
                self.assertIn(
                    f"while (!workers_reached({field},", source
                )
        for token in ('"R"', '"B"', '"P"'):
            with self.subTest(token=token):
                self.assertGreaterEqual(source.count(token), 1)
        self.assertIn("if (result->fresh_agent) {", summary_add)
        self.assertIn(
            "summary->deadline_misses += result->deadline_misses;",
            summary_add,
        )
        self.assertIn(
            "summary->buckets[i] += result->wakeup_buckets[i];",
            summary_add,
        )
        self.assertRegex(
            source,
            r"result\.fresh_agent &&\s+"
            r"result\.wake_probes == WAKE_PROBES",
        )
        self.assertIn(
            "scope=fresh_agents_only fresh_samples=%u", source
        )

    def test_eevdf_measurement_interval_and_probe_boundary_are_honest(self) -> None:
        source = self.eevdf_source
        execute_wave = source[
            source.index("static void execute_wave") :
            source.index("static void close_wave_pipes")
        ]
        main = source[source.index("int main(void)") :]
        child = source[
            source.index("static void workflow_child") :
            source.index("static void bootstrap_prepare")
        ]
        fill = source[
            source.index("static void fill_result") :
            source.index("static void workflow_child")
        ]
        busy = source[
            source.index("static void busy_worker") :
            source.index("static void ordinary_worker")
        ]

        self.assertNotIn("sleep(", execute_wave)
        self.assertNotIn("sleep(", main)
        self.assertEqual(main.count("waittid(ordinary_tid)"), 1)
        self.assertIn(
            "deadline = get_mtime() + MEASUREMENT_MILLISECONDS", busy
        )
        self.assertIn("workload_stop_peer_count", busy)
        self.assertIn(
            'write_exact(workload_stop_fd, "X", 1,', busy
        )
        self.assertNotIn('write_exact(stop[1], "X", 1,', execute_wave)
        measurement_prepare = child.index(
            'check(token == \'M\', "workflow measurement prepare token")'
        )
        start_snapshot = child.index(
            "agent_workflow_lifecycle_info(&before, &lifecycle)"
        )
        measurement_armed = child.index(
            'write_exact(next_spawn.ready_fd, "A", 1'
        )
        measurement_go = child.index(
            'check(token == \'G\', "workflow measurement go token")'
        )
        self.assertLess(measurement_prepare, start_snapshot)
        self.assertLess(start_snapshot, measurement_armed)
        self.assertLess(measurement_armed, measurement_go)
        self.assertIn(
            'read_exact(next_spawn.go_fd, &token, 1,\n'
            '\t\t   "receive measurement go");',
            child,
        )
        self.assertIn(
            'read_exact(next_spawn.measure_fd, &token, 1,\n'
            '\t\t   "receive measurement prepare");',
            child,
        )
        self.assertNotIn(
            'write_exact(start[1], "M", 1,', execute_wave
        )
        self.assertIn(
            'write_exact(measure[1], "M", 1,', execute_wave
        )
        self.assertNotIn(
            'write_exact(start[1], "G", 1,', execute_wave
        )
        self.assertIn('write_exact(go[1], "G", 1,', execute_wave)
        workload_snapshot = child.index(
            "agent_workflow_lifecycle_info(&workload_after, &before.key)"
        )
        paused_token = child.index(
            'write_exact(next_spawn.ready_fd, "P", 1'
        )
        wake_probe = child.index("agent_wait(&event, 1)")
        final_snapshot = child.index(
            "agent_workflow_lifecycle_info(&probe_after, &before.key)"
        )
        self.assertLess(workload_snapshot, paused_token)
        self.assertLess(paused_token, wake_probe)
        self.assertLess(wake_probe, final_snapshot)
        self.assertIn(
            "workload_after->scheduler_service_cycles", fill
        )
        self.assertIn(
            "probe_after->scheduler_wakeup_latency_buckets[i]", fill
        )
        self.assertIn(
            "workload_after->scheduler_wakeup_latency_buckets[i]", fill
        )

    def test_eevdf_phase_pipes_are_delegated_and_closed(self) -> None:
        def assert_phase_pipe_contract(source: str) -> None:
            config = source[
                source.index("struct spawn_config") :
                source.index("struct cohort_summary")
            ]
            child = source[
                source.index("static void workflow_child") :
                source.index("static void bootstrap_prepare")
            ]
            delegation = source[
                source.index("static void delegate_workflow_fds") :
                source.index("static int spawn_workflow(")
            ]
            execute_wave = source[
                source.index("static void execute_wave") :
                source.index("static void close_wave_pipes")
            ]
            run_wave = source[
                source.index("static void run_wave") :
                source.index("static void run_simple_scenario")
            ]
            arrivals = source[
                source.index("static void run_sixteen_arrivals") :
                source.index("int main(void)")
            ]

            self.assertIn("int measure_fd;", config)
            self.assertIn("int go_fd;", config)
            self.assertIn(
                'read_exact(next_spawn.measure_fd, &token, 1,', child
            )
            self.assertIn(
                'read_exact(next_spawn.go_fd, &token, 1,', child
            )
            self.assertNotIn(
                'write_exact(start[1], "M", 1,', execute_wave
            )
            self.assertNotIn(
                'write_exact(start[1], "G", 1,', execute_wave
            )
            self.assertIn(
                'write_exact(measure[1], "M", 1,', execute_wave
            )
            self.assertIn('write_exact(go[1], "G", 1,', execute_wave)
            self.assertIn("int measure_read", delegation)
            self.assertIn("int go_read", delegation)
            self.assertIn(
                "agent_scope_delegate_fd(measure_read)", delegation
            )
            self.assertIn("agent_scope_delegate_fd(go_read)", delegation)
            self.assertIn("pipe(measure) == 0", run_wave)
            self.assertIn("pipe(go) == 0", run_wave)
            self.assertIn(
                "start[0], measure[0], go[0], stop[0]", run_wave
            )
            self.assertIn(
                "close(measure[0]); close(measure[1]);", source
            )
            self.assertIn("close(go[0]); close(go[1]);", source)
            self.assertIn("pipe(measure) == 0", arrivals)
            self.assertIn("pipe(go) == 0", arrivals)
            self.assertIn(
                "start[0],\n\t\t\t\t\t       measure[0], go[0]",
                arrivals,
            )

        source = self.eevdf_source
        assert_phase_pipe_contract(source)
        mutations = (
            source.replace(
                "read_exact(next_spawn.measure_fd, &token, 1,",
                "read_exact(next_spawn.start_fd, &token, 1,",
                1,
            ),
            source.replace(
                "read_exact(next_spawn.go_fd, &token, 1,",
                "read_exact(next_spawn.start_fd, &token, 1,",
                1,
            ),
            source.replace(
                'write_exact(measure[1], "M", 1,',
                'write_exact(start[1], "M", 1,',
                1,
            ),
            source.replace(
                'write_exact(go[1], "G", 1,',
                'write_exact(start[1], "G", 1,',
                1,
            ),
            source.replace(
                "agent_scope_delegate_fd(measure_read)",
                "agent_scope_delegate_fd(start_read)",
                1,
            ),
            source.replace(
                "agent_scope_delegate_fd(go_read)",
                "agent_scope_delegate_fd(start_read)",
                1,
            ),
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated):
                self.assertNotEqual(mutated, source)
                with self.assertRaises(AssertionError):
                    assert_phase_pipe_contract(mutated)

    def test_evidence_fixture_tracks_all_three_cases(self) -> None:
        generated_cases = re.search(
            r"agent_cases = \((.*?)\)\nAGENTTASK_METRIC_FIXTURE_LINES =",
            self.fixture,
            re.S,
        )
        self.assertIsNotNone(generated_cases)
        for guest in GUESTS:
            with self.subTest(guest=guest):
                self.assertEqual(generated_cases.group(1).count(f'"{guest}"'), 1)
        self.assertIn("AGENTTASK_METRIC_FIXTURE_LINES = (", self.fixture)
        self.assertIn(
            "lines.extend(AGENTTASK_METRIC_FIXTURE_LINES)", self.fixture
        )
        self.assertIn("EEVDF_METRIC_FIXTURE_LINES =", self.fixture)
        self.assertIn(
            "lines.extend(EEVDF_METRIC_FIXTURE_LINES)", self.fixture
        )
        for token in (
            "service_start_interval_tick_p50=",
            "service_start_interval_tick_p99=",
            "service_start_span_ticks=",
            "sequence_elapsed_ticks=",
            "cancel_latency scope=retained_terminal",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.fixture)

    def test_runner_delegates_dynamic_metrics_to_semantic_validator(self) -> None:
        self.assertEqual(self.runner.count("--profile agent-case"), 1)
        self.assertEqual(self.runner.count('--case "${init_proc}"'), 1)
        route = re.search(
            r'\tif \[\[ "\$\{init_proc\}" == "agent_eevdf_ucore" \|\|\n'
            r'\t      "\$\{init_proc\}" == "agenttask_ucore" \]\]; then\n'
            r'(?P<body>.*?)\n\tfi',
            self.runner,
            re.S,
        )
        self.assertIsNotNone(route)
        body = route.group("body")
        self.assertEqual(body.count("scripts/validate-kernel-test-log.py"), 1)
        self.assertEqual(body.count('--tag "${init_proc}"'), 1)
        self.assertEqual(body.count("--profile agent-case"), 1)
        self.assertEqual(body.count('--case "${init_proc}"'), 1)
        self.assertIn('elif args.profile == "agent-case":', self.validator)
        self.assertIn("validate_agent_case(text, args.case)", self.validator)
        self.assertIn("def validate_agent_eevdf_metrics(text):", self.validator)
        self.assertIn("validate_agent_eevdf_metrics(text)", self.validator)


if __name__ == "__main__":
    unittest.main(verbosity=2)
