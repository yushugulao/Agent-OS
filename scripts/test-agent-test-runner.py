#!/usr/bin/env python3
"""Fast tests for the Agent QEMU output monitor."""

import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_test_runner


DEFAULT_TEST_CASE_TIMEOUT = 5.0 if sys.platform == "cygwin" else 1.0
DEFAULT_TEST_MARKER_GRACE = 2.0 if sys.platform == "cygwin" else 0.1
STREAM_DEADLINE_TOTAL_LIMIT = 4.0 if sys.platform == "cygwin" else 1.0


class AgentTestRunnerTests(unittest.TestCase):
    marker = "case: parent passed"

    def test_isolated_entrypoint_loads_shared_failure_classifier(self):
        runner = Path(agent_test_runner.__file__).resolve()
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, "-I", "-S", "-B", str(runner), "--help"],
                cwd=directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=10,
            )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr.decode("utf-8", errors="replace"),
        )

    def test_attested_invocation_uses_captured_canonical_python(self):
        identities = {
            "python": {
                "executable": {"path": "/canonical/tools/python3"}
            }
        }
        with mock.patch.object(sys, "argv", ["runner.py", "--marker", "ok"]):
            self.assertEqual(
                agent_test_runner._attested_invocation_argv(identities),
                [
                    "/canonical/tools/python3",
                    "runner.py",
                    "--marker",
                    "ok",
                ],
            )

    def test_powercut_supervisor_preserves_isolated_python(self):
        command = agent_test_runner._powercut_supervisor_command(
            7, 9, "a" * 64, ["qemu-system-riscv64", "-nographic"]
        )
        self.assertEqual(
            command[:5],
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(Path(agent_test_runner.__file__).resolve()),
            ],
        )
        self.assertEqual(
            command[5:],
            [
                "--internal-powercut-supervisor",
                "7",
                "9",
                "a" * 64,
                "--",
                "qemu-system-riscv64",
                "-nographic",
            ],
        )

    def calibration_args(self, **changes):
        values = {
            "attestation_file": "/tmp/case.json",
            "run_id": "2" * 64,
            "execution_id": "3" * 64,
            "evidence_scope": agent_test_runner.LOCAL_EVIDENCE_SCOPE,
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "campaign_nonce": "0" * 64,
            "calibration_plan_sha256": "c" * 64,
            "round_nonce": "1" * 64,
            "session_nonce": "2" * 64,
            "execution_nonce": "3" * 64,
            "toolchain_cc": "riscv64-linux-gnu-gcc",
        }
        values.update(changes)
        return SimpleNamespace(**values)

    def test_calibration_attestation_arguments_fail_closed(self):
        absent = self.calibration_args(
            **{
                name: None
                for name in vars(self.calibration_args())
            }
        )
        self.assertFalse(
            agent_test_runner.validate_calibration_attestation_args(absent)
        )
        with self.assertRaisesRegex(ValueError, "supplied together"):
            agent_test_runner.validate_calibration_attestation_args(
                self.calibration_args(source_tree=None)
            )
        self.assertTrue(
            agent_test_runner.validate_calibration_attestation_args(
                self.calibration_args()
            )
        )
        generic = self.calibration_args(
            **{
                name: None
                for name in (
                    "evidence_scope",
                    "source_commit",
                    "source_tree",
                    "campaign_nonce",
                    "calibration_plan_sha256",
                    "round_nonce",
                    "session_nonce",
                    "execution_nonce",
                    "toolchain_cc",
                )
            }
        )
        self.assertFalse(
            agent_test_runner.validate_calibration_attestation_args(generic)
        )
        for changes in (
            {"evidence_scope": "ci_e4_signed"},
            {"session_nonce": "0" * 64, "run_id": "0" * 64},
            {"execution_id": "4" * 64},
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(
                ValueError, "invalid calibration attestation identity"
            ):
                agent_test_runner.validate_calibration_attestation_args(
                    self.calibration_args(**changes)
                )

    @unittest.skipUnless(os.name == "posix", "POSIX executable paths required")
    def test_qemu_path_is_resolved_once_before_path_or_symlink_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            for target, marker in (
                (first_dir / "fixture-qemu", "first"),
                (second_dir / "fixture-qemu", "second"),
            ):
                target.write_text(
                    f"#!/bin/sh\necho {marker}\n", encoding="ascii"
                )
                target.chmod(0o755)
            with mock.patch.dict(
                os.environ, {"PATH": str(first_dir)}, clear=False
            ):
                resolved = agent_test_runner._resolve_executable_once(
                    "fixture-qemu", "fixture QEMU"
                )
            with mock.patch.dict(
                os.environ, {"PATH": str(second_dir)}, clear=False
            ):
                command = agent_test_runner.build_qemu_command(resolved)
            self.assertEqual(Path(command[0]), (first_dir / "fixture-qemu").resolve())

            alias = root / "qemu-link"
            alias.symlink_to(first_dir / "fixture-qemu")
            real_symlink = alias.is_symlink()
            alias_stamp = (
                None
                if real_symlink
                else agent_test_runner._file_stamp(
                    alias, "fixture QEMU alias"
                )
            )
            resolved_alias = agent_test_runner._resolve_executable_once(
                str(alias), "fixture QEMU alias"
            )
            alias.unlink()
            alias.symlink_to(second_dir / "fixture-qemu")
            if real_symlink:
                self.assertEqual(
                    Path(resolved_alias), (first_dir / "fixture-qemu").resolve()
                )
            else:
                self.assertNotEqual(
                    agent_test_runner._file_stamp(alias, "fixture QEMU alias"),
                    alias_stamp,
                )

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

    def reset_dumpability_test_state(self, original, real_set):
        real_set(original)
        with agent_test_runner._DUMPABILITY_CONDITION:
            agent_test_runner._DUMPABILITY_SESSION = None
            agent_test_runner._DUMPABILITY_POISONED = False
            agent_test_runner._DUMPABILITY_CONDITION.notify_all()

    def monitor(
        self,
        source,
        *,
        timeout=DEFAULT_TEST_CASE_TIMEOUT,
        grace=DEFAULT_TEST_MARKER_GRACE,
        powercut_delay=0,
        completion_mode=agent_test_runner.COMPLETION_NATURAL,
        expected_bad_addr_markers=(),
        marker_mode=agent_test_runner.MARKER_EXACT_LINE,
        expected_fault_marker_mode=agent_test_runner.MARKER_EXACT_LINE,
        output_stream=None,
        log_file=None,
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
            powercut_delay=powercut_delay,
            completion_mode=completion_mode,
            expected_bad_addr_markers=expected_bad_addr_markers,
            marker_mode=marker_mode,
            expected_fault_marker_mode=expected_fault_marker_mode,
            output_stream=output_stream,
            diagnostic_stream=io.StringIO(),
            log_file=log_file,
            max_output_bytes=max_output_bytes,
            max_pending_chars=max_pending_chars,
        )

    def test_normal_pass_at_eof_succeeds(self):
        result = self.monitor("print('case: parent passed')")
        self.assertTrue(result.succeeded)
        self.assertEqual(result.reason, "process_exit")

    def test_natural_exit_before_grace_survives_slow_cleanup(self):
        original_stop_and_drain = agent_test_runner._stop_and_drain

        def delayed_cleanup(*args, **kwargs):
            result = original_stop_and_drain(*args, **kwargs)
            time.sleep(5.1)
            return result

        with mock.patch.object(
            agent_test_runner,
            "_stop_and_drain",
            side_effect=delayed_cleanup,
        ):
            result = self.monitor(
                "print('case: parent passed', flush=True)",
                timeout=10.0,
                grace=5.0,
            )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.reason, "process_exit")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.signals_sent, ())
        self.assertTrue(result.output_eof)
        self.assertTrue(result.process_tree_gone)

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

    def test_process_still_alive_at_marker_grace_must_fail(self):
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
        self.assertTrue(result.process_tree_gone)
        self.assertTrue(result.checkpoint_succeeded)
        self.assertFalse(result.succeeded)

    def test_cygwin_signals_exclusive_process_group_once(self):
        proc = mock.Mock(pid=1234, returncode=None)
        with mock.patch.object(sys, "platform", "cygwin"), mock.patch.object(
            agent_test_runner,
            "_leader_exited",
            return_value=False,
        ), mock.patch.object(
            agent_test_runner.os,
            "killpg",
            create=True,
        ) as killpg, mock.patch.object(
            agent_test_runner,
            "_signal_leader",
        ) as signal_leader:
            delivered = agent_test_runner._signal_process_tree(
                proc,
                None,
                signal.SIGTERM,
            )
        self.assertEqual(delivered, (True, True, True))
        killpg.assert_called_once_with(proc.pid, signal.SIGTERM)
        signal_leader.assert_not_called()

    def test_cygwin_cleans_group_after_leader_exit(self):
        proc = mock.Mock(pid=1234, returncode=0)
        with mock.patch.object(sys, "platform", "cygwin"), mock.patch.object(
            agent_test_runner,
            "_leader_exited",
            return_value=True,
        ), mock.patch.object(
            agent_test_runner.os,
            "killpg",
            create=True,
        ) as killpg:
            delivered = agent_test_runner._signal_process_tree(
                proc,
                None,
                signal.SIGTERM,
            )
        self.assertEqual(delivered, (True, False, False))
        killpg.assert_called_once_with(proc.pid, signal.SIGTERM)

    def test_cygwin_checks_group_liveness_without_linux_procfs(self):
        proc = mock.Mock(pid=1234, returncode=0)
        with mock.patch.object(sys, "platform", "cygwin"), mock.patch.object(
            agent_test_runner.os,
            "name",
            "posix",
        ), mock.patch.object(
            agent_test_runner,
            "_leader_exited",
            return_value=True,
        ), mock.patch.object(
            agent_test_runner,
            "_linux_process_group_has_live_member",
        ) as linux_probe, mock.patch.object(
            agent_test_runner.os,
            "killpg",
            create=True,
        ) as killpg:
            self.assertTrue(agent_test_runner._process_tree_alive(proc))
        linux_probe.assert_not_called()
        killpg.assert_called_once_with(proc.pid, 0)

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

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux powercut containment required",
    )
    def test_explicit_powercut_uses_sigkill_without_sigterm(self):
        dumpability_before = agent_test_runner._get_linux_dumpability()
        with tempfile.TemporaryDirectory() as directory:
            term_file = Path(directory) / "sigterm-delivered"
            result = self.monitor(
                "import signal, time\n"
                f"term_file = {str(term_file)!r}\n"
                "signal.signal(signal.SIGTERM, lambda *_: "
                "open(term_file, 'w').write('term'))\n"
                "print('case: parent passed', flush=True)\n"
                "time.sleep(10)\n",
                completion_mode=agent_test_runner.COMPLETION_POWERCUT,
            )
            self.assertEqual(result.reason, "expected_powercut")
            self.assertEqual(result.signals_sent, (signal.SIGKILL,))
            self.assertEqual(result.returncode, -signal.SIGKILL)
            self.assertEqual(result.supervisor_returncode, 0)
            self.assertTrue(result.completion_leader_signaled)
            self.assertTrue(result.process_tree_gone)
            self.assertTrue(result.process_tree_contained)
            self.assertTrue(result.completion_signal_attested)
            self.assertTrue(result.powercut_succeeded)
            self.assertFalse(result.checkpoint_succeeded)
            self.assertFalse(result.succeeded)
            self.assertFalse(term_file.exists())
        self.assertEqual(
            agent_test_runner._get_linux_dumpability(),
            dumpability_before,
        )

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux powercut containment required",
    )
    def test_powercut_failure_after_marker_still_never_sends_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            term_file = Path(directory) / "sigterm-delivered"
            result = self.monitor(
                "import os, signal, time\n"
                f"term_file = {str(term_file)!r}\n"
                "signal.signal(signal.SIGTERM, lambda *_: "
                "open(term_file, 'w').write('term'))\n"
                "os.write(1, b'case: parent passed\\n[PANIC 1-0] "
                "os/test.c:7: injected\\n')\n"
                "time.sleep(10)\n",
                completion_mode=agent_test_runner.COMPLETION_POWERCUT,
            )
            self.assertTrue(result.marker_seen)
            self.assertTrue(result.failure_seen)
            self.assertEqual(result.signals_sent, (signal.SIGKILL,))
            self.assertFalse(result.powercut_succeeded)
            self.assertFalse(term_file.exists())

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux dumpability hardening required",
    )
    def test_powercut_restore_failure_is_an_explicit_failed_result(self):
        original = agent_test_runner._get_linux_dumpability()
        self.assertIsNotNone(original)
        real_set = agent_test_runner._set_linux_dumpability
        calls = 0

        def fail_restore(value):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_set(value)
            return False

        try:
            with mock.patch.object(
                agent_test_runner,
                "_set_linux_dumpability",
                side_effect=fail_restore,
            ):
                result = self.monitor(
                    "import time\n"
                    "print('case: parent passed', flush=True)\n"
                    "time.sleep(60)\n",
                    completion_mode=agent_test_runner.COMPLETION_POWERCUT,
                )
            self.assertFalse(result.control_endpoint_restored)
            self.assertFalse(result.powercut_succeeded)
            self.assertTrue(agent_test_runner._DUMPABILITY_POISONED)
            with self.assertRaisesRegex(
                RuntimeError,
                "control endpoint hardening unavailable",
            ):
                self.monitor(
                    "print('case: parent passed', flush=True)\n",
                    completion_mode=agent_test_runner.COMPLETION_POWERCUT,
                )
        finally:
            self.reset_dumpability_test_state(original, real_set)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux dumpability hardening required",
    )
    def test_powercut_restore_failure_overrides_exception_teardown(self):
        class ExplodingStream:
            def write(self, _text):
                raise ValueError("output exploded")

            def flush(self):
                pass

        original = agent_test_runner._get_linux_dumpability()
        self.assertIsNotNone(original)
        real_set = agent_test_runner._set_linux_dumpability
        calls = 0

        def fail_restore(value):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_set(value)
            return False

        try:
            with mock.patch.object(
                agent_test_runner,
                "_set_linux_dumpability",
                side_effect=fail_restore,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "control endpoint restoration failed",
                ):
                    self.monitor(
                        "import time\n"
                        "print('case: parent passed', flush=True)\n"
                        "time.sleep(60)\n",
                        completion_mode=agent_test_runner.COMPLETION_POWERCUT,
                        output_stream=ExplodingStream(),
                    )
            self.assertTrue(agent_test_runner._DUMPABILITY_POISONED)
        finally:
            self.reset_dumpability_test_state(original, real_set)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux dumpability hardening required",
    )
    def test_concurrent_powercuts_share_restore_failure(self):
        original = agent_test_runner._get_linux_dumpability()
        self.assertIsNotNone(original)
        real_set = agent_test_runner._set_linux_dumpability
        calls = 0
        results = {}
        errors = {}

        def fail_restore(value):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_set(value)
            return False

        with tempfile.TemporaryDirectory() as directory:
            ready = [Path(directory) / f"ready-{index}" for index in range(2)]

            def run_case(index):
                source = (
                    "import os, time\n"
                    f"open({str(ready[index])!r}, 'w').write('ready')\n"
                    f"while not os.path.exists({str(ready[1 - index])!r}):\n"
                    "    time.sleep(0.01)\n"
                    "print('case: parent passed', flush=True)\n"
                    "time.sleep(60)\n"
                )
                try:
                    results[index] = self.monitor(
                        source,
                        timeout=5,
                        completion_mode=(
                            agent_test_runner.COMPLETION_POWERCUT
                        ),
                    )
                except BaseException as error:
                    errors[index] = error

            threads = [
                threading.Thread(target=run_case, args=(index,))
                for index in range(2)
            ]
            try:
                with mock.patch.object(
                    agent_test_runner,
                    "_set_linux_dumpability",
                    side_effect=fail_restore,
                ):
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join(timeout=10)
                self.assertFalse(any(thread.is_alive() for thread in threads))
                self.assertEqual(errors, {})
                self.assertEqual(set(results), {0, 1})
                for result in results.values():
                    self.assertFalse(result.control_endpoint_restored)
                    self.assertFalse(result.powercut_succeeded)
                self.assertTrue(agent_test_runner._DUMPABILITY_POISONED)
            finally:
                self.reset_dumpability_test_state(original, real_set)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux powercut containment required",
    )
    def test_powercut_contract_rejects_wrong_signal_sequences(self):
        result = self.monitor(
            "import time\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(10)\n",
            completion_mode=agent_test_runner.COMPLETION_POWERCUT,
        )
        wrong_results = (
            replace(
                result,
                signals_sent=(signal.SIGTERM,),
                returncode=-signal.SIGTERM,
            ),
            replace(
                result,
                signals_sent=(signal.SIGTERM, signal.SIGKILL),
            ),
            replace(
                result,
                signals_sent=(signal.SIGKILL, signal.SIGKILL),
            ),
            replace(
                result,
                supervisor_returncode=-signal.SIGKILL,
            ),
        )
        for wrong in wrong_results:
            with self.subTest(signals=wrong.signals_sent):
                self.assertFalse(wrong.powercut_succeeded)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux powercut supervisor required",
    )
    def test_powercut_supervisor_does_not_attest_sigterm_as_powercut(self):
        scanner = agent_test_runner.OutputScanner(
            "case",
            self.marker,
            io.StringIO(),
            None,
        )
        lifecycle = agent_test_runner._ProcessLifecycle()
        try:
            proc, fd = lifecycle.start(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    "import time; print('case: parent passed', flush=True); "
                    "time.sleep(60)",
                ],
                scanner,
                agent_test_runner.COMPLETION_POWERCUT,
            )
            deadline = time.monotonic() + 5
            while not scanner.marker_seen and time.monotonic() < deadline:
                agent_test_runner._read_available_chunk(fd, scanner)
                time.sleep(0.01)
            self.assertTrue(scanner.marker_seen)
            sent, leader_sent, confirmed = (
                agent_test_runner._signal_process_tree(
                    proc,
                    lifecycle.pidfd,
                    signal.SIGTERM,
                )
            )
            self.assertTrue(sent)
            self.assertTrue(leader_sent)
            self.assertTrue(confirmed)
            agent_test_runner._wait_for_tree_exit_and_eof(
                proc,
                lifecycle.pidfd,
                fd,
                scanner,
                time.monotonic() + 5,
            )
            proc.wait(timeout=5)
            self.assertEqual(proc.supervisor_returncode, 0)
            self.assertEqual(proc.returncode, -signal.SIGTERM)
            self.assertTrue(proc.cleanup_attested)
            self.assertFalse(proc.signal_attested)
        finally:
            if not lifecycle.released:
                lifecycle.abort()

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux powercut containment required",
    )
    def test_powercut_rejects_leader_that_exited_naturally(self):
        class SlowStream:
            def write(self, _text):
                time.sleep(0.1)

            def flush(self):
                pass

        result = self.monitor(
            "import os\n"
            "os.write(1, b'case: parent passed\\n')\n"
            "os._exit(0)\n",
            completion_mode=agent_test_runner.COMPLETION_POWERCUT,
            output_stream=SlowStream(),
        )
        self.assertEqual(result.reason, "expected_powercut")
        self.assertEqual(result.signals_sent, ())
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.completion_leader_signaled)
        self.assertFalse(result.powercut_succeeded)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux powercut containment required",
    )
    def test_powercut_rejects_matching_self_sigkill_exit(self):
        class SlowStream:
            def write(self, _text):
                time.sleep(0.1)

            def flush(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "descendant.pid"
            child = "import time; time.sleep(60)"
            source = (
                "import os, signal, subprocess, sys\n"
                f"child = {child!r}\n"
                "proc = subprocess.Popen([sys.executable, '-u', '-c', child])\n"
                f"open({str(pid_file)!r}, 'w').write(str(proc.pid))\n"
                "os.write(1, b'case: parent passed\\n')\n"
                "os.kill(os.getpid(), signal.SIGKILL)\n"
            )
            result = self.monitor(
                source,
                completion_mode=agent_test_runner.COMPLETION_POWERCUT,
                output_stream=SlowStream(),
            )
            descendant_pid = int(pid_file.read_text())
            self.assertIn(result.signals_sent, ((), (signal.SIGKILL,)))
            self.assertEqual(result.returncode, -signal.SIGKILL)
            self.assertFalse(result.completion_leader_signaled)
            self.assertFalse(result.powercut_succeeded)
            self.assert_process_gone(descendant_pid)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux zombie-state identity check required",
    )
    def test_powercut_repeated_self_sigkill_never_gets_attested(self):
        class SlowStream:
            def write(self, _text):
                time.sleep(0.03)

            def flush(self):
                pass

        for attempt in range(12):
            with self.subTest(attempt=attempt):
                result = self.monitor(
                    "import os, signal\n"
                    "os.write(1, b'case: parent passed\\n')\n"
                    "os.kill(os.getpid(), signal.SIGKILL)\n",
                    completion_mode=agent_test_runner.COMPLETION_POWERCUT,
                    output_stream=SlowStream(),
                )
                self.assertEqual(result.returncode, -signal.SIGKILL)
                self.assertEqual(result.supervisor_returncode, 0)
                self.assertFalse(result.completion_signal_attested)
                self.assertFalse(result.powercut_succeeded)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux powercut containment required",
    )
    def test_powercut_kills_and_reaps_descendant_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "descendant.pid"
            child = "import time; time.sleep(60)"
            source = (
                "import subprocess, sys, time\n"
                f"child = {child!r}\n"
                "proc = subprocess.Popen([sys.executable, '-u', '-c', child])\n"
                f"open({str(pid_file)!r}, 'w').write(str(proc.pid))\n"
                "print('case: parent passed', flush=True)\n"
                "time.sleep(60)\n"
            )
            result = self.monitor(
                source,
                completion_mode=agent_test_runner.COMPLETION_POWERCUT,
            )
            descendant_pid = int(pid_file.read_text())
            self.assertTrue(result.process_tree_gone)
            self.assertTrue(result.output_eof)
            self.assertTrue(result.powercut_succeeded)
            self.assert_process_gone(descendant_pid)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux subreaper containment required",
    )
    def test_powercut_contains_setsid_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "escaped.pid"
            child = (
                "import os, time\n"
                f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
                "time.sleep(60)\n"
            )
            source = (
                "import os, subprocess, sys, time\n"
                f"child = {child!r}\n"
                "subprocess.Popen(\n"
                "    [sys.executable, '-u', '-c', child],\n"
                "    start_new_session=True,\n"
                "    stdout=subprocess.DEVNULL,\n"
                "    stderr=subprocess.DEVNULL,\n"
                ")\n"
                f"while not os.path.exists({str(pid_file)!r}):\n"
                "    time.sleep(0.01)\n"
                "print('case: parent passed', flush=True)\n"
                "time.sleep(60)\n"
            )
            result = self.monitor(
                source,
                completion_mode=agent_test_runner.COMPLETION_POWERCUT,
            )
            descendant_pid = int(pid_file.read_text())
            self.assertTrue(result.process_tree_contained)
            self.assertTrue(result.process_tree_gone)
            self.assertTrue(result.powercut_succeeded)
            self.assert_process_gone(descendant_pid)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux procfs descriptor inspection required",
    )
    def test_powercut_workload_cannot_forge_done_or_inherit_control_fds(self):
        source = (
            "import os, time\n"
            "try:\n"
            "    cmdline = open(\n"
            "        f'/proc/{os.getppid()}/cmdline', 'rb'\n"
            "    ).read()\n"
            "    args = [\n"
            "        part.decode() for part in cmdline.split(b'\\0') if part\n"
            "    ]\n"
            "    nonce = next(part for part in args if len(part) == 64 "
            "and all(c in '0123456789abcdef' for c in part))\n"
            "    nonce_visible = True\n"
            "except (OSError, StopIteration):\n"
            "    nonce = '0' * 64\n"
            "    nonce_visible = False\n"
            "stat = open(f'/proc/{os.getpid()}/stat').read()\n"
            "start_time = stat[stat.rfind(')') + 2:].split()[19]\n"
            "fake = (f'DONE {nonce} {os.getpid()} {start_time} "
            "-9 CLEAN 9 1\\n').encode()\n"
            "writable = []\n"
            "for fd in range(3, 64):\n"
            "    try:\n"
            "        os.write(fd, fake)\n"
            "    except OSError:\n"
            "        continue\n"
            "    writable.append(fd)\n"
            "parent_writable = []\n"
            "try:\n"
            "    parent_fd_names = os.listdir(f'/proc/{os.getppid()}/fd')\n"
            "except OSError:\n"
            "    parent_fd_names = []\n"
            "for name in parent_fd_names:\n"
            "    if not name.isdigit() or int(name) <= 2:\n"
            "        continue\n"
            "    try:\n"
            "        reopened = os.open(\n"
            "            f'/proc/{os.getppid()}/fd/{name}',\n"
            "            os.O_WRONLY | os.O_NONBLOCK,\n"
            "        )\n"
            "        os.write(reopened, fake)\n"
            "        os.close(reopened)\n"
            "    except OSError:\n"
            "        continue\n"
            "    parent_writable.append(int(name))\n"
            "parent_stat = open(\n"
            "    f'/proc/{os.getppid()}/stat'\n"
            ").read()\n"
            "grandparent = int(\n"
            "    parent_stat[parent_stat.rfind(')') + 2:].split()[1]\n"
            ")\n"
            "grandparent_writable = []\n"
            "try:\n"
            "    grandparent_fd_names = os.listdir(\n"
            "        f'/proc/{grandparent}/fd'\n"
            "    )\n"
            "except OSError:\n"
            "    grandparent_fd_names = []\n"
            "for name in grandparent_fd_names:\n"
            "    if not name.isdigit() or int(name) <= 2:\n"
            "        continue\n"
            "    try:\n"
            "        reopened = os.open(\n"
            "            f'/proc/{grandparent}/fd/{name}',\n"
            "            os.O_WRONLY | os.O_NONBLOCK,\n"
            "        )\n"
            "        os.write(reopened, fake)\n"
            "        os.close(reopened)\n"
            "    except OSError:\n"
            "        continue\n"
            "    grandparent_writable.append(int(name))\n"
            "os.write(1, fake)\n"
            "print(f'case: parent nonce visible {nonce_visible}', flush=True)\n"
            "print(f'case: inherited writable fds {writable}', flush=True)\n"
            "print(f'case: parent writable fds {parent_writable}', flush=True)\n"
            "print(\n"
            "    f'case: grandparent writable fds {grandparent_writable}',\n"
            "    flush=True,\n"
            ")\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(60)\n"
        )
        result = self.monitor(
            source,
            completion_mode=agent_test_runner.COMPLETION_POWERCUT,
        )
        self.assertTrue(any(line.startswith("DONE ") for line in result.lines))
        self.assertIn("case: parent nonce visible True", result.lines)
        self.assertIn("case: inherited writable fds []", result.lines)
        self.assertIn("case: parent writable fds []", result.lines)
        self.assertIn("case: grandparent writable fds []", result.lines)
        self.assertEqual(result.supervisor_returncode, 0)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        self.assertTrue(result.completion_signal_attested)
        self.assertTrue(result.powercut_succeeded)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux powercut containment required",
    )
    def test_powercut_contract_rejects_residual_process_tree(self):
        result = self.monitor(
            "import time\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(10)\n",
            completion_mode=agent_test_runner.COMPLETION_POWERCUT,
        )
        residual = replace(result, process_tree_gone=False)
        self.assertFalse(residual.powercut_succeeded)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux powercut containment required",
    )
    def test_powercut_delay_monitors_guest_until_cut_deadline(self):
        result = self.monitor(
            "import time\n"
            "print('case: parent passed', flush=True)\n"
            "time.sleep(0.03)\n"
            "print('case: commit entered', flush=True)\n"
            "time.sleep(10)\n",
            timeout=2,
            grace=0,
            powercut_delay=0.08,
            completion_mode=agent_test_runner.COMPLETION_POWERCUT,
        )
        self.assertTrue(result.powercut_succeeded)
        self.assertIn("case: commit entered", result.lines)
        self.assertGreaterEqual(result.elapsed, 0.07)

    def test_powercut_delay_requires_powercut_completion(self):
        with self.assertRaisesRegex(ValueError, "requires powercut"):
            self.monitor(
                "print('case: parent passed')\n",
                powercut_delay=0.01,
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux supervisor containment required",
    )
    def test_powercut_does_not_claim_concurrent_unrelated_child(self):
        with tempfile.TemporaryDirectory() as directory:
            trigger = Path(directory) / "launch-unrelated"
            ready = Path(directory) / "unrelated-ready"
            holder = {}

            def launch_unrelated():
                deadline = time.monotonic() + 5.0
                while not trigger.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not trigger.exists():
                    return
                holder["proc"] = subprocess.Popen(
                    [sys.executable, "-u", "-c", "import time; time.sleep(60)"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                ready.write_text("ready")

            launcher = threading.Thread(target=launch_unrelated)
            launcher.start()
            source = (
                "import os, time\n"
                f"open({str(trigger)!r}, 'w').write('go')\n"
                f"while not os.path.exists({str(ready)!r}):\n"
                "    time.sleep(0.01)\n"
                "print('case: parent passed', flush=True)\n"
                "time.sleep(60)\n"
            )
            try:
                result = self.monitor(
                    source,
                    completion_mode=agent_test_runner.COMPLETION_POWERCUT,
                )
                launcher.join(timeout=5)
                unrelated = holder.get("proc")
                self.assertIsNotNone(unrelated)
                self.assertIsNone(unrelated.poll())
                self.assertTrue(result.powercut_succeeded)
            finally:
                launcher.join(timeout=5)
                unrelated = holder.get("proc")
                if unrelated is not None and unrelated.poll() is None:
                    unrelated.terminate()
                    unrelated.wait(timeout=5)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux supervisor containment required",
    )
    def test_powercut_rejects_killed_supervisor_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "escaped.pid"
            child = (
                "import os, time\n"
                f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
                "time.sleep(60)\n"
            )
            source = (
                "import os, signal, subprocess, sys, time\n"
                f"child = {child!r}\n"
                "subprocess.Popen(\n"
                "    [sys.executable, '-u', '-c', child],\n"
                "    start_new_session=True,\n"
                "    stdout=subprocess.DEVNULL,\n"
                "    stderr=subprocess.DEVNULL,\n"
                ")\n"
                f"while not os.path.exists({str(pid_file)!r}):\n"
                "    time.sleep(0.01)\n"
                "os.kill(os.getppid(), signal.SIGKILL)\n"
                "print('case: parent passed', flush=True)\n"
                "time.sleep(60)\n"
            )
            escaped_pid = None
            try:
                result = self.monitor(
                    source,
                    completion_mode=agent_test_runner.COMPLETION_POWERCUT,
                )
                escaped_pid = int(pid_file.read_text())
                self.assertEqual(result.signals_sent, (signal.SIGKILL,))
                self.assertIsNone(result.returncode)
                self.assertEqual(
                    result.supervisor_returncode,
                    -signal.SIGKILL,
                )
                self.assertTrue(result.completion_leader_signaled)
                self.assertFalse(result.completion_signal_attested)
                self.assertFalse(result.process_tree_contained)
                self.assertFalse(result.process_tree_gone)
                self.assertFalse(result.powercut_succeeded)
                os.kill(escaped_pid, 0)
            finally:
                if escaped_pid is None and pid_file.exists():
                    escaped_pid = int(pid_file.read_text())
                if escaped_pid is not None:
                    try:
                        os.kill(escaped_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.assert_process_gone(escaped_pid)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux supervisor recovery required",
    )
    def test_powercut_sigstopped_supervisor_fails_closed_and_is_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor_file = Path(directory) / "supervisor.pid"
            child_file = Path(directory) / "escaped.pid"
            child = (
                "import os, time\n"
                f"open({str(child_file)!r}, 'w').write(str(os.getpid()))\n"
                "time.sleep(60)\n"
            )
            source = (
                "import os, signal, subprocess, sys, time\n"
                f"child = {child!r}\n"
                "subprocess.Popen(\n"
                "    [sys.executable, '-u', '-c', child],\n"
                "    start_new_session=True,\n"
                "    stdout=subprocess.DEVNULL,\n"
                "    stderr=subprocess.DEVNULL,\n"
                ")\n"
                f"while not os.path.exists({str(child_file)!r}):\n"
                "    time.sleep(0.01)\n"
                f"open({str(supervisor_file)!r}, 'w').write(str(os.getppid()))\n"
                "os.kill(os.getppid(), signal.SIGSTOP)\n"
                "print('case: parent passed', flush=True)\n"
                "time.sleep(60)\n"
            )
            result = self.monitor(
                source,
                timeout=5,
                completion_mode=agent_test_runner.COMPLETION_POWERCUT,
            )
            supervisor_pid = int(supervisor_file.read_text())
            child_pid = int(child_file.read_text())
            self.assertEqual(result.signals_sent, (signal.SIGKILL,))
            self.assertEqual(result.returncode, -signal.SIGKILL)
            self.assertEqual(result.supervisor_returncode, 0)
            self.assertFalse(result.supervisor_control_healthy)
            self.assertFalse(result.process_tree_contained)
            self.assertTrue(result.process_tree_gone)
            self.assertFalse(result.powercut_succeeded)
            self.assert_process_gone(supervisor_pid)
            self.assert_process_gone(child_pid)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Linux supervisor recovery required",
    )
    def test_powercut_repeated_supervisor_stop_never_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor_file = Path(directory) / "supervisor.pid"
            stopper_file = Path(directory) / "stopper.pid"
            stopper = (
                "import os, signal, time\n"
                f"open({str(stopper_file)!r}, 'w').write(str(os.getpid()))\n"
                "target = int(os.environ['SUPERVISOR_PID'])\n"
                "while True:\n"
                "    try:\n"
                "        os.kill(target, signal.SIGSTOP)\n"
                "    except ProcessLookupError:\n"
                "        break\n"
                "    time.sleep(0.002)\n"
            )
            source = (
                "import os, subprocess, sys, time\n"
                f"stopper = {stopper!r}\n"
                "env = dict(os.environ)\n"
                "env['SUPERVISOR_PID'] = str(os.getppid())\n"
                "subprocess.Popen(\n"
                "    [sys.executable, '-u', '-c', stopper],\n"
                "    env=env,\n"
                "    start_new_session=True,\n"
                "    stdout=subprocess.DEVNULL,\n"
                "    stderr=subprocess.DEVNULL,\n"
                ")\n"
                f"while not os.path.exists({str(stopper_file)!r}):\n"
                "    time.sleep(0.01)\n"
                f"open({str(supervisor_file)!r}, 'w').write(str(os.getppid()))\n"
                "print('case: parent passed', flush=True)\n"
                "time.sleep(60)\n"
            )
            stopper_pid = None
            try:
                result = self.monitor(
                    source,
                    timeout=8,
                    completion_mode=agent_test_runner.COMPLETION_POWERCUT,
                )
                supervisor_pid = int(supervisor_file.read_text())
                stopper_pid = int(stopper_file.read_text())
                self.assertFalse(result.supervisor_control_healthy)
                self.assertFalse(result.process_tree_contained)
                self.assertFalse(result.powercut_succeeded)
                self.assert_process_gone(supervisor_pid)
            finally:
                if stopper_pid is None and stopper_file.exists():
                    stopper_pid = int(stopper_file.read_text())
                if stopper_pid is not None:
                    try:
                        os.kill(stopper_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.assert_process_gone(stopper_pid)

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

    def test_guest_log_canonicalizes_fragmented_crlf(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "guest.log"
            result = self.monitor(
                "import os, time\n"
                "os.write(1, b'prefix\\r')\n"
                "time.sleep(0.01)\n"
                "os.write(1, b'\\ncase: parent passed\\r\\n')\n",
                log_file=log,
            )
            content = log.read_bytes()
        self.assertTrue(result.succeeded)
        self.assertNotIn(b"\r", content)
        self.assertIn(b"prefix\ncase: parent passed\n", content)

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
        self.assertTrue(result.timed_out)
        self.assertEqual(result.reason, "timeout")
        self.assertFalse(result.checkpoint_succeeded)
        self.assertFalse(result.checkpoint_succeeded)

    def test_substring_marker_requires_explicit_opt_in(self):
        result = self.monitor(
            "print('diagnostic: case: parent passed (legacy completion)')\n",
            marker_mode=agent_test_runner.MARKER_SUBSTRING,
        )
        self.assertTrue(result.marker_seen)
        self.assertTrue(result.succeeded)

    def test_line_prefix_marker_preserves_pure_complete_guest_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "guest.log"
            output = io.StringIO()
            result = self.monitor(
                "import os, time\n"
                "os.write(1, b'case: parent passed ')\n"
                "time.sleep(0.01)\n"
                "os.write(1, b'audit=7\\n')\n",
                marker_mode=agent_test_runner.MARKER_LINE_PREFIX,
                output_stream=output,
                log_file=log,
            )
            content = log.read_text(encoding="utf-8")
        self.assertTrue(result.marker_seen)
        self.assertTrue(result.succeeded)
        self.assertEqual(content, "case: parent passed audit=7\n")
        self.assertNotIn("[agent-tests]", content)
        self.assertIn("marker observed", output.getvalue())

    def test_line_prefix_marker_waits_for_record_boundary(self):
        result = self.monitor(
            "import os, time\n"
            "os.write(1, b'case: parent passed dynamic')\n"
            "time.sleep(10)\n",
            timeout=0.05,
            completion_mode=agent_test_runner.COMPLETION_CHECKPOINT,
            marker_mode=agent_test_runner.MARKER_LINE_PREFIX,
        )
        self.assertTrue(result.timed_out)
        self.assertEqual(result.reason, "timeout")
        self.assertFalse(result.checkpoint_succeeded)

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

    def test_shared_classifier_only_interprets_post_launch_guest_lines(self):
        classifier = agent_test_runner._GUEST_FAILURE_CLASSIFIER
        build_line = "CC -o build/riscv64/ch6b_panic user/ch6b_panic.c"
        self.assertIsNone(
            classifier.classify_output_line(
                build_line, phase=classifier.PHASE_BUILD
            )
        )
        self.assertIsNone(
            classifier.classify_output_line(
                build_line, phase=classifier.PHASE_GUEST
            )
        )
        records = {
            "panic": "[PANIC -1--1] os/main.c:7: boot failed",
            "error": "[ERROR 4-0]unknown syscall -1",
            "user": "fsallocfault_ucore: child_failed: worker 2",
            "user-check": "ch8_cow_ucore: check failed: fork",
            "orchestrator": "rp_orch: failed code=1",
            "legacy-panic": "panic: synthetic legacy failure",
            "assertion": "kernel: assertion failed in recovery",
        }
        for label, record in records.items():
            with self.subTest(label=label):
                self.assertIsNotNone(
                    classifier.classify_output_line(
                        record, phase=classifier.PHASE_GUEST
                    )
                )
                scanner = agent_test_runner.OutputScanner(
                    "case", self.marker, io.StringIO(), None
                )
                scanner._process_line(record)
                self.assertTrue(scanner.failure_seen)

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
        self.assertLess(result.elapsed, STREAM_DEADLINE_TOTAL_LIMIT)

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
        self.assertLess(result.elapsed, STREAM_DEADLINE_TOTAL_LIMIT)

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
