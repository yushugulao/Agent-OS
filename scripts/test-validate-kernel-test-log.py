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
            "fsquota_ucore: public_domain_limited=1 blocks=16 inodes=8",
        )
        domain_text += "\nfsquota_ucore: parent passed"
        self.assertIn(
            "domain blocks=16",
            validator.validate_fs(
                domain_text,
                "domain",
                "fsquota_ucore: parent passed",
            ),
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
