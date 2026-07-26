#!/usr/bin/env python3
"""Fast tests for the Agent QEMU output monitor."""

import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_test_runner


class AgentTestRunnerTests(unittest.TestCase):
    marker = "case: parent passed"

    def assert_process_gone(self, pid, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                state = Path(f"/proc/{pid}/stat").read_text().split()[2]
            except (FileNotFoundError, IndexError, OSError):
                state = None
            if state == "Z":
                return
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail(f"descendant process {pid} survived runner cleanup")

    def monitor(
        self,
        source,
        *,
        timeout=1.0,
        grace=0.1,
        completion_mode=agent_test_runner.COMPLETION_NATURAL,
        expected_bad_addr_markers=(),
        marker_mode=agent_test_runner.MARKER_EXACT_LINE,
        expected_fault_marker_mode=agent_test_runner.MARKER_EXACT_LINE,
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
            marker_mode=marker_mode,
            expected_fault_marker_mode=expected_fault_marker_mode,
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
            "    os.write(1, b'\\x1b[31m[PANIC 1-0] "
            "os/test.c:7: checkpoint teardown failed\\x1b[0m\\n')\n"
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
            "os.write(1, b'case: parent passed\\n[PANIC 1-0] "
            "os/test.c:7: teardown failed\\n')\n"
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

    def test_exact_line_marker_accepts_fragmented_complete_record(self):
        result = self.monitor(
            "import os, time\n"
            "for part in (b'case: par', b'ent passed', b'\\n'):\n"
            "    os.write(1, part)\n"
            "    time.sleep(0.01)\n",
            marker_mode=agent_test_runner.MARKER_EXACT_LINE,
        )
        self.assertTrue(result.succeeded)
        self.assertTrue(result.marker_seen)

    def test_exact_line_marker_rejects_diagnostic_substring(self):
        result = self.monitor(
            "print('diagnostic: case: parent passed (not completion)')\n",
            marker_mode=agent_test_runner.MARKER_EXACT_LINE,
        )
        self.assertFalse(result.marker_seen)
        self.assertEqual(result.reason, "eof_without_marker")
        self.assertFalse(result.succeeded)

    def test_checkpoint_default_rejects_diagnostic_substring(self):
        result = self.monitor(
            "import time\n"
            "print('diagnostic: case: parent passed (not completion)', "
            "flush=True)\n"
            "time.sleep(10)\n",
            timeout=0.05,
            completion_mode=agent_test_runner.COMPLETION_CHECKPOINT,
        )
        self.assertFalse(result.marker_seen)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.reason, "timeout")
        self.assertFalse(result.checkpoint_succeeded)

    def test_substring_marker_requires_explicit_opt_in(self):
        result = self.monitor(
            "print('diagnostic: case: parent passed (legacy completion)')\n",
            marker_mode=agent_test_runner.MARKER_SUBSTRING,
        )
        self.assertTrue(result.marker_seen)
        self.assertTrue(result.succeeded)

    def test_exact_line_marker_waits_for_record_boundary(self):
        result = self.monitor(
            "import os, time\n"
            "os.write(1, b'case: parent passed')\n"
            "time.sleep(10)\n",
            timeout=0.05,
            completion_mode=agent_test_runner.COMPLETION_CHECKPOINT,
            marker_mode=agent_test_runner.MARKER_EXACT_LINE,
        )
        self.assertTrue(result.timed_out)
        self.assertEqual(result.reason, "timeout")
        self.assertFalse(result.checkpoint_succeeded)

    def test_partial_panic_is_drained_after_marker_grace(self):
        result = self.monitor(
            "import os, time\n"
            "os.write(1, b'case: parent passed\\n[PANIC 1-0] "
            "os/test.c:7: la')\n"
            "time.sleep(0.01)\n"
            "os.write(1, b'te failure')\n"
            "time.sleep(10)\n",
            grace=0.05,
        )
        self.assertTrue(result.marker_seen)
        self.assertTrue(result.failure_seen)
        self.assertFalse(result.succeeded)

    def test_expected_bad_addr_requires_explicit_marker_and_fault(self):
        arm = "case: expected_fault_armed=1"
        fault = (
            "\\x1b[31m[ERROR 3-0]15 in application, bad addr = 0x1, "
            "bad instruction = 0x2, core dumped.\\x1b[0m"
        )
        result = self.monitor(
            "print('case: expected_fault_armed=1')\n"
            f"print('{fault}')\n"
            "print('case: parent passed')\n",
            expected_bad_addr_markers=(arm,),
        )
        self.assertFalse(result.failure_seen)
        self.assertTrue(result.expected_faults_satisfied)
        self.assertTrue(result.succeeded)

        unscoped = self.monitor(
            "print('case: expected_fault_armed=1')\n"
            f"print('{fault}')\n"
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

    def test_expected_fault_marker_defaults_to_exact_line(self):
        arm = "case: expected_fault_armed=1"
        fault = (
            "[ERROR 3-0]15 in application, bad addr = 0x1, "
            "bad instruction = 0x2, core dumped."
        )
        result = self.monitor(
            "print('diagnostic: case: expected_fault_armed=1 (ignored)')\n"
            f"print({fault!r})\n"
            "print('case: parent passed')\n",
            expected_bad_addr_markers=(arm,),
        )
        self.assertTrue(result.failure_seen)
        self.assertFalse(result.expected_faults_satisfied)
        self.assertFalse(result.succeeded)

    def test_expected_fault_substring_mode_is_explicit(self):
        arm = "case: expected_fault_armed=1"
        fault = (
            "[ERROR 3-0]15 in application, bad addr = 0x1, "
            "bad instruction = 0x2, core dumped."
        )
        result = self.monitor(
            "print('diagnostic: case: expected_fault_armed=1 (legacy)')\n"
            f"print({fault!r})\n"
            "print('case: parent passed')\n",
            expected_bad_addr_markers=(arm,),
            expected_fault_marker_mode=agent_test_runner.MARKER_SUBSTRING,
        )
        self.assertFalse(result.failure_seen)
        self.assertTrue(result.expected_faults_satisfied)
        self.assertTrue(result.succeeded)

    def test_negative_unknown_syscall_is_a_structured_failure(self):
        result = self.monitor(
            "print('[ERROR 4-0]unknown syscall -1')\n"
            "print('case: parent passed')\n"
        )
        self.assertTrue(result.failure_seen)
        self.assertFalse(result.succeeded)

    def test_all_kernel_fault_record_shapes_fail_closed(self):
        records = (
            "[PANIC -1--1] os/main.c:7: boot failed",
            "[ERROR 2-0]IllegalInstruction in application, core dumped.",
            "[ERROR 2-0]unknown trap: 0x2, stval = 0x0",
            "[ERROR -1--1]invalid trap from kernel: 0x2, "
            "stval = 0x0 sepc = 0x80000000",
        )
        for record in records:
            with self.subTest(record=record):
                result = self.monitor(
                    f"print({record!r})\nprint('case: parent passed')\n"
                )
                self.assertTrue(result.failure_seen)
                self.assertFalse(result.succeeded)

    def test_failure_words_inside_diagnostics_are_not_failures(self):
        result = self.monitor(
            "print('diagnostic: /tmp/panic-report unknown syscall notes')\n"
            "print('case: parent passed')\n"
        )
        self.assertFalse(result.failure_seen)
        self.assertTrue(result.succeeded)

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

    def test_signal_handlers_are_restored_after_normal_run(self):
        previous = {
            signum: signal.getsignal(signum)
            for signum in agent_test_runner.CONTROL_SIGNALS
        }
        result = self.monitor("print('case: parent passed')")
        self.assertTrue(result.succeeded)
        self.assertEqual(
            previous,
            {
                signum: signal.getsignal(signum)
                for signum in agent_test_runner.CONTROL_SIGNALS
            },
        )

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    def test_output_exception_cleans_descendant_process_group(self):
        class ExplodingStream:
            def write(self, _text):
                raise RuntimeError("synthetic output failure")

            def flush(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "descendant.pid"
            child = (
                "import time\n"
                "time.sleep(60)\n"
            )
            source = (
                "import subprocess, sys, time\n"
                f"child = {child!r}\n"
                "proc = subprocess.Popen([sys.executable, '-u', '-c', child])\n"
                f"open({str(pid_file)!r}, 'w').write(str(proc.pid))\n"
                "print('trigger output exception', flush=True)\n"
                "time.sleep(60)\n"
            )
            with self.assertRaisesRegex(
                RuntimeError, "synthetic output failure"
            ):
                self.monitor(source, output_stream=ExplodingStream())
            descendant_pid = int(pid_file.read_text())
            self.assert_process_gone(descendant_pid)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    def test_normal_leader_exit_cleans_lingering_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "descendant.pid"
            child = "import time; time.sleep(60)"
            source = (
                "import subprocess, sys\n"
                f"child = {child!r}\n"
                "proc = subprocess.Popen([sys.executable, '-u', '-c', child])\n"
                f"open({str(pid_file)!r}, 'w').write(str(proc.pid))\n"
                "print('case: parent passed', flush=True)\n"
            )
            result = self.monitor(source, grace=0.05)
            descendant_pid = int(pid_file.read_text())
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.signals_sent, (signal.SIGTERM,))
            self.assertTrue(result.output_eof)
            self.assertFalse(result.succeeded)
            self.assert_process_gone(descendant_pid)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    def test_checkpoint_rejects_exited_leader_and_cleans_descendant(self):
        class SlowStream:
            def write(self, _text):
                time.sleep(0.1)

            def flush(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "descendant.pid"
            child = "import time; time.sleep(60)"
            source = (
                "import os, subprocess, sys\n"
                f"child = {child!r}\n"
                "proc = subprocess.Popen([sys.executable, '-u', '-c', child])\n"
                f"open({str(pid_file)!r}, 'w').write(str(proc.pid))\n"
                "os.write(1, b'case: parent passed\\n')\n"
                "os._exit(0)\n"
            )
            result = self.monitor(
                source,
                completion_mode=agent_test_runner.COMPLETION_CHECKPOINT,
                output_stream=SlowStream(),
            )
            descendant_pid = int(pid_file.read_text())
            self.assertEqual(result.returncode, 0)
            self.assertFalse(result.checkpoint_leader_signaled)
            self.assertFalse(result.checkpoint_succeeded)
            self.assert_process_gone(descendant_pid)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    def test_sigterm_cleans_descendants_and_preserves_cancel_status(self):
        self.assert_cancel_signal_cleanup(signal.SIGTERM)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    def test_sigint_cleans_descendants_and_preserves_cancel_status(self):
        self.assert_cancel_signal_cleanup(signal.SIGINT)

    @unittest.skipUnless(
        os.name == "posix" and hasattr(signal, "SIGHUP"),
        "POSIX SIGHUP required",
    )
    def test_sighup_cleans_descendants_and_preserves_cancel_status(self):
        self.assert_cancel_signal_cleanup(signal.SIGHUP)

    def assert_cancel_signal_cleanup(self, cancel_signal):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "descendant.pid"
            success_file = Path(directory) / "returned-success"
            child = (
                "import os, signal, time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
                "time.sleep(60)\n"
            )
            qemu = (
                "import subprocess, sys, time\n"
                f"child = {child!r}\n"
                "subprocess.Popen([sys.executable, '-u', '-c', child])\n"
                "print('fake qemu started', flush=True)\n"
                "time.sleep(60)\n"
            )
            runner_dir = str(Path(agent_test_runner.__file__).resolve().parent)
            helper = (
                "import io, sys\n"
                f"sys.path.insert(0, {runner_dir!r})\n"
                "import agent_test_runner\n"
                f"source = {qemu!r}\n"
                "result = agent_test_runner.monitor_command(\n"
                "    [sys.executable, '-u', '-c', source],\n"
                "    init_proc='signal-case',\n"
                "    marker='never reached',\n"
                "    case_timeout=60,\n"
                "    idle_notice=0,\n"
                "    marker_grace=1,\n"
                "    output_stream=io.StringIO(),\n"
                "    diagnostic_stream=io.StringIO(),\n"
                ")\n"
                f"open({str(success_file)!r}, 'w').write(str(result.succeeded))\n"
            )
            proc = subprocess.Popen(
                [sys.executable, "-u", "-c", helper],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 5.0
            while not pid_file.exists() and time.monotonic() < deadline:
                if proc.poll() is not None:
                    stdout, stderr = proc.communicate()
                    self.fail(
                        "runner exited before descendant startup: "
                        f"rc={proc.returncode} stdout={stdout!r} stderr={stderr!r}"
                    )
                time.sleep(0.05)
            self.assertTrue(pid_file.exists(), "descendant did not start")
            descendant_pid = int(pid_file.read_text())
            proc.send_signal(cancel_signal)
            stdout, stderr = proc.communicate(timeout=10)
            self.assertEqual(
                proc.returncode,
                -cancel_signal,
                (stdout, stderr),
            )
            self.assertFalse(success_file.exists())
            self.assert_process_gone(descendant_pid)


if __name__ == "__main__":
    unittest.main()
