#!/usr/bin/env python3
"""Fast tests for the Agent QEMU output monitor."""

import io
import signal
import sys
import time
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_test_runner


class AgentTestRunnerTests(unittest.TestCase):
    marker = "case: parent passed"

    def monitor(
        self,
        source,
        *,
        timeout=1.0,
        grace=0.1,
        completion_mode=agent_test_runner.COMPLETION_NATURAL,
        expected_bad_addr_markers=(),
        output_stream=None,
        max_output_bytes=agent_test_runner.MAX_OUTPUT_BYTES,
        max_pending_chars=agent_test_runner.MAX_PENDING_CHARS,
    ):
        if output_stream is None:
            output_stream = io.StringIO()
        return agent_test_runner.monitor_command(
            [sys.executable, "-u", "-c", source],
            init_proc="case",
            marker=self.marker,
            case_timeout=timeout,
            idle_notice=0,
            marker_grace=grace,
            completion_mode=completion_mode,
            expected_bad_addr_markers=expected_bad_addr_markers,
            output_stream=output_stream,
            diagnostic_stream=io.StringIO(),
            max_output_bytes=max_output_bytes,
            max_pending_chars=max_pending_chars,
        )

    def test_normal_pass_at_eof_succeeds(self):
        result = self.monitor("print('case: parent passed')")
        self.assertTrue(result.succeeded)
        self.assertEqual(result.reason, "process_exit")

    def test_pipe_eof_before_exit_status_does_not_trigger_signal(self):
        result = self.monitor(
            "import os, time\n"
            "print('case: parent passed', flush=True)\n"
            "os.close(1)\n"
            "os.close(2)\n"
            "time.sleep(0.05)\n"
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.signals_sent, ())
        self.assertEqual(result.returncode, 0)

    def test_pipe_eof_does_not_extend_marker_grace(self):
        result = self.monitor(
            "import os, time\n"
            "print('case: parent passed', flush=True)\n"
            "os.close(1)\n"
            "os.close(2)\n"
            "time.sleep(10)\n",
            grace=0.05,
        )
        self.assertFalse(result.succeeded)
        self.assertIn(signal.SIGTERM, result.signals_sent)

    def test_marker_followed_by_nonzero_exit_fails(self):
        result = self.monitor(
            "import os\n"
            "print('case: parent passed', flush=True)\n"
            "os._exit(7)\n"
        )
        self.assertTrue(result.marker_seen)
        self.assertEqual(result.returncode, 7)
        self.assertFalse(result.succeeded)

    def test_marker_grace_signal_handler_nonzero_exit_fails(self):
        result = self.monitor(
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(7))\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(10)\n",
            grace=0.05,
        )
        self.assertEqual(result.reason, "marker_grace")
        self.assertIn(signal.SIGTERM, result.signals_sent)
        self.assertEqual(result.returncode, 7)
        self.assertFalse(result.succeeded)

    def test_marker_grace_signal_handler_zero_exit_still_fails(self):
        result = self.monitor(
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(10)\n",
            grace=0.05,
        )
        self.assertEqual(result.reason, "marker_grace")
        self.assertEqual(result.signals_sent, (signal.SIGTERM,))
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.succeeded)

    def test_marker_followed_by_permanent_hang_fails(self):
        result = self.monitor(
            "import time\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(10)\n",
            grace=0.05,
        )
        self.assertEqual(result.reason, "marker_grace")
        self.assertEqual(result.signals_sent, (signal.SIGTERM,))
        self.assertFalse(result.succeeded)

    def test_marker_grace_sigkill_is_not_a_clean_teardown(self):
        result = self.monitor(
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(10)\n",
            grace=0.05,
        )
        self.assertEqual(result.reason, "marker_grace")
        self.assertEqual(
            result.signals_sent, (signal.SIGTERM, signal.SIGKILL)
        )
        self.assertEqual(result.returncode, -signal.SIGKILL)
        self.assertFalse(result.succeeded)

    def test_explicit_checkpoint_accepts_sigterm_only(self):
        result = self.monitor(
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(10)\n",
            completion_mode=agent_test_runner.COMPLETION_CHECKPOINT,
        )
        self.assertEqual(result.reason, "expected_checkpoint")
        self.assertEqual(result.signals_sent, (signal.SIGTERM,))
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.checkpoint_succeeded)
        self.assertFalse(result.succeeded)

    def test_explicit_checkpoint_accepts_default_sigterm_exit(self):
        result = self.monitor(
            "import time\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(10)\n",
            completion_mode=agent_test_runner.COMPLETION_CHECKPOINT,
        )
        self.assertEqual(result.signals_sent, (signal.SIGTERM,))
        self.assertEqual(result.returncode, -signal.SIGTERM)
        self.assertTrue(result.checkpoint_succeeded)

    def test_explicit_checkpoint_rejects_sigkill(self):
        result = self.monitor(
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(10)\n",
            completion_mode=agent_test_runner.COMPLETION_CHECKPOINT,
        )
        self.assertEqual(
            result.signals_sent, (signal.SIGTERM, signal.SIGKILL)
        )
        self.assertFalse(result.checkpoint_succeeded)

    def test_explicit_checkpoint_drains_failure_after_sigterm(self):
        result = self.monitor(
            "import os, signal, time\n"
            "def stop(*_):\n"
            "    os.write(1, b'PANIC: checkpoint teardown failed\\n')\n"
            "    raise SystemExit(0)\n"
            "signal.signal(signal.SIGTERM, stop)\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(10)\n",
            completion_mode=agent_test_runner.COMPLETION_CHECKPOINT,
        )
        self.assertTrue(result.failure_seen)
        self.assertFalse(result.checkpoint_succeeded)

    def test_pass_and_uppercase_panic_in_one_write_fails(self):
        result = self.monitor(
            "import os\n"
            "os.write(1, b'case: parent passed\\nPANIC: teardown failed\\n')\n"
        )
        self.assertTrue(result.marker_seen)
        self.assertTrue(result.failure_seen)
        self.assertFalse(result.succeeded)

    def test_fragmented_marker_is_reassembled(self):
        result = self.monitor(
            "import os, time\n"
            "for part in (b'case: par', b'ent pass', b'ed\\n'):\n"
            "    os.write(1, part)\n"
            "    time.sleep(0.01)\n"
        )
        self.assertTrue(result.succeeded)
        self.assertTrue(result.marker_seen)

    def test_partial_panic_is_drained_after_marker_grace(self):
        result = self.monitor(
            "import os, time\n"
            "os.write(1, b'case: parent passed\\nPA')\n"
            "time.sleep(0.01)\n"
            "os.write(1, b'NIC: late failure')\n"
            "time.sleep(10)\n",
            grace=0.05,
        )
        self.assertTrue(result.marker_seen)
        self.assertTrue(result.failure_seen)
        self.assertFalse(result.succeeded)

    def test_expected_bad_addr_requires_explicit_marker_and_fault(self):
        arm = "case: expected_fault_armed=1"
        result = self.monitor(
            "print('case: expected_fault_armed=1')\n"
            "print('bad addr')\n"
            "print('case: parent passed')\n",
            expected_bad_addr_markers=(arm,),
        )
        self.assertFalse(result.failure_seen)
        self.assertTrue(result.expected_faults_satisfied)
        self.assertTrue(result.succeeded)

        unscoped = self.monitor(
            "print('case: expected_fault_armed=1')\n"
            "print('bad addr')\n"
            "print('case: parent passed')\n"
        )
        self.assertTrue(unscoped.failure_seen)
        self.assertFalse(unscoped.succeeded)

        missing_fault = self.monitor(
            "print('case: expected_fault_armed=1')\n"
            "print('case: parent passed')\n",
            expected_bad_addr_markers=(arm,),
        )
        self.assertFalse(missing_fault.expected_faults_satisfied)
        self.assertFalse(missing_fault.succeeded)

    def test_eof_without_marker_fails(self):
        result = self.monitor("print('boot stopped early')")
        self.assertFalse(result.succeeded)
        self.assertFalse(result.marker_seen)
        self.assertEqual(result.reason, "eof_without_marker")

    def test_timeout_without_marker_fails(self):
        result = self.monitor("import time; time.sleep(10)", timeout=0.05)
        self.assertFalse(result.succeeded)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.reason, "timeout")

    def test_continuous_output_cannot_bypass_case_timeout(self):
        result = self.monitor(
            "import os\n"
            "chunk = b'x' * 65535 + b'\\n'\n"
            "while True:\n"
            "    os.write(1, chunk)\n",
            timeout=0.05,
            max_output_bytes=64 * 1024 * 1024,
        )
        self.assertFalse(result.succeeded)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.reason, "timeout")
        self.assertLess(result.elapsed, 1.0)

    def test_continuous_output_cannot_bypass_marker_grace(self):
        result = self.monitor(
            "import os\n"
            "os.write(1, b'case: parent passed\\n')\n"
            "chunk = b'x' * 65535 + b'\\n'\n"
            "while True:\n"
            "    os.write(1, chunk)\n",
            timeout=1.0,
            grace=0.05,
            max_output_bytes=64 * 1024 * 1024,
        )
        self.assertFalse(result.succeeded)
        self.assertEqual(result.reason, "marker_grace")
        self.assertIn(signal.SIGTERM, result.signals_sent)
        self.assertLess(result.elapsed, 1.0)

    def test_unterminated_output_is_bounded_and_fails_closed(self):
        result = self.monitor(
            "import os, time\n"
            "os.write(1, b'x' * 131072)\n"
            "time.sleep(10)\n",
            timeout=1.0,
            max_pending_chars=4096,
        )
        self.assertFalse(result.succeeded)
        self.assertEqual(result.reason, "output_limit")
        self.assertTrue(result.failure_seen)
        self.assertLessEqual(
            max(map(len, result.lines)),
            agent_test_runner.MAX_DIAGNOSTIC_LINE_CHARS,
        )

    def test_total_output_budget_bounds_streaming_logs(self):
        output = io.StringIO()
        result = self.monitor(
            "import os\n"
            "chunk = b'x\\n' * 4096\n"
            "while True:\n"
            "    os.write(1, chunk)\n",
            timeout=1.0,
            output_stream=output,
            max_output_bytes=8192,
        )
        self.assertFalse(result.succeeded)
        self.assertEqual(result.reason, "output_limit")
        self.assertLessEqual(len(output.getvalue()), 8192)

    def test_slow_output_sink_cannot_accept_late_checkpoint(self):
        class SlowStream:
            def write(self, _text):
                time.sleep(0.1)

            def flush(self):
                pass

        result = self.monitor(
            "import time\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(10)\n",
            timeout=0.05,
            completion_mode=agent_test_runner.COMPLETION_CHECKPOINT,
            output_stream=SlowStream(),
        )
        self.assertFalse(result.checkpoint_succeeded)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.reason, "timeout")

    def test_unknown_completion_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported completion mode"):
            self.monitor("print('case: parent passed')", completion_mode="typo")


if __name__ == "__main__":
    unittest.main()
