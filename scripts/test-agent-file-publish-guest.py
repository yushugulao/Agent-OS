#!/usr/bin/env python3
"""Check that the atomic file-publish contract has a real Guest route."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "user" / "src" / "agentpublish_ucore.c"
USER_MAKEFILE = ROOT / "user" / "Makefile"
MANIFEST = ROOT / "user" / "include" / "exec_policy_manifest.h"
RUNNER = ROOT / "scripts" / "run-agent-tests.sh"
VALIDATOR = ROOT / "scripts" / "validate-kernel-test-log.py"

MARKERS = (
    "agentpublish_ucore: invalid_requests=1 bad_pointer=1 bad_path=1 bad_size=1 bad_abi=1 zero_namespace_side_effect=1",
    "agentpublish_ucore: publish_image=1 header=32 payload=96 eof=1",
    "agentpublish_ucore: same_scope_race=1 ok=1 duplicate=1 no_overwrite=1",
    "agentpublish_ucore: nexus_duplicate=1 exact_readback=1 mismatch_rejected=1",
    "agentpublish_ucore: resources=1 invalid_no_leak=1 duplicate_no_leak=1 unlink_reclaimed=1",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class AgentFilePublishGuestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = read(SOURCE)
        cls.makefile = read(USER_MAKEFILE)
        cls.manifest = read(MANIFEST)
        cls.runner = read(RUNNER)
        cls.validator = read(VALIDATOR)

    def test_guest_is_built_and_boot_authorized(self) -> None:
        agent_tests = re.search(
            r"(?m)^AGENT_TESTS := (?P<tests>.*)$", self.makefile
        )
        self.assertIsNotNone(agent_tests)
        self.assertEqual(
            agent_tests.group("tests").split().count("agentpublish_ucore"),
            1,
        )
        normalized = re.sub(r"\s+", " ", re.sub(r"\\\s*", " ", self.manifest))
        self.assertIn(
            'X("agentpublish_ucore", "agentpublish_ucore", '
            "EXEC_MANIFEST_F_BOOT_SEALED, "
            "EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR), 0, "
            "EXEC_MANIFEST_VFS_PROFILE_WORKFLOW)",
            normalized,
        )

    def test_runner_and_validator_require_guest_evidence(self) -> None:
        joined_source = re.sub(r'"\s*"', "", self.source)
        self.assertEqual(
            self.runner.count(
                'run_case agentpublish_ucore "agentpublish_ucore: parent passed"'
            ),
            1,
        )
        self.assertEqual(self.runner.count("\tagentpublish_ucore)"), 1)
        for marker in MARKERS:
            with self.subTest(marker=marker):
                self.assertEqual(joined_source.count(f'"{marker}\\n"'), 1)
                self.assertEqual(self.runner.count(f'"{marker}"'), 1)
                self.assertEqual(self.validator.count(f'"{marker}"'), 1)
        self.assertEqual(
            self.source.count('"agentpublish_ucore: parent passed\\n"'), 1
        )

    def test_guest_runs_the_behavioral_contract(self) -> None:
        ordered = (
            "exercise_invalid_requests();",
            "exercise_same_name_race();",
            "exercise_nexus_convergence();",
            'unlink(race_path) == 0',
            'printf("agentpublish_ucore: parent passed\\n")',
        )
        positions = [self.source.index(token) for token in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("agent_create_role(AGENT_ROLE_ORCHESTRATOR)", self.source)
        self.assertIn("ok_count == 1 && duplicate_count == 1", self.source)
        self.assertIn("read(fd, &tail, 1) == 0", self.source)
        self.assertIn("AGENT_STATUS_BAD_SIZE", self.source)
        self.assertIn("AGENT_STATUS_BAD_VERSION", self.source)
        self.assertIn("AGENT_STATUS_BAD_PARAM", self.source)
        self.assertIn("AGENT_STATUS_DUPLICATE", self.source)
        self.assertEqual(
            self.source.count("agent_nexus_artifact_publish_owned("), 3
        )
        self.assertIn("agent_nexus_artifact_read_verify(", self.source)
        self.assertNotIn("powercut=1", self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
