#!/usr/bin/env python3
"""Mutation tests for fail-closed GitLab CI effective-job resolution."""
from __future__ import annotations

import tempfile
import unittest
import re
from pathlib import Path

from gitlab_ci_contract import (
    CIContractError,
    parse_ci,
    validate_repository_ci,
)


ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".gitlab-ci.yml"
BUDGET_PATH = ROOT / "ci" / "kernel-budgets.json"


def replace_in_definition(text: str, name: str, old: str, new: str) -> str:
    start = text.index(f"{name}:\n")
    body_start = text.index("\n", start) + 1
    match = re.search(r"(?m)^[A-Za-z0-9_.-]+:\s*$", text[body_start:])
    end = len(text) if match is None else body_start + match.start()
    block = text[start:end]
    if block.count(old) != 1:
        raise AssertionError((name, old))
    return text[:start] + block.replace(old, new, 1) + text[end:]


class GitLabCIContractTests(unittest.TestCase):
    def test_recursive_extends_and_child_override(self) -> None:
        config = parse_ci(
            ".base:\n"
            "  tags:\n"
            "    - parent\n"
            "  resource_group: qemu\n"
            ".middle:\n"
            "  extends: .base\n"
            "  dependencies: []\n"
            "job:\n"
            "  extends: .middle\n"
            "  tags: [child]\n"
        )
        config.resolve_all()
        job = config.effective("job")
        self.assertEqual(job.field("tags").items(), ("child",))
        self.assertEqual(job.field("resource_group").scalar(), "qemu")
        self.assertEqual(job.field("dependencies").items(), ())
        self.assertEqual(job.lineage, (".base", ".middle", "job"))

    def test_unknown_parent_cycle_and_duplicate_field_fail_closed(self) -> None:
        fixtures = (
            ".base:\n  stage: test\njob:\n  extends: .missing\n",
            ".a:\n  extends: .b\n.b:\n  extends: .a\njob:\n  extends: .a\n",
            "job:\n  stage: test\n  stage: deploy\n",
        )
        for text in fixtures:
            with self.subTest(text=text):
                with self.assertRaises(CIContractError):
                    parse_ci(text).resolve_all()

    def test_repository_effective_jobs_validate(self) -> None:
        config = validate_repository_ci(CI_PATH, BUDGET_PATH)
        mechanism = config.effective("kernel-mechanism-regression")
        self.assertEqual(mechanism.field("tags").items(), ("agentos-qemu-calibrated",))
        self.assertEqual(mechanism.field("resource_group").scalar(), "agentos-qemu-performance")
        self.assertEqual(mechanism.field("dependencies").items(), ())
        self.assertIn("when: always", mechanism.field("artifacts").text())
        self.assertIn("remote-ci-attestation.json",
                      config.effective("reader-e2e").field("artifacts").text())
        fs_allocator = config.effective("fs-allocator-fault-regression")
        self.assertEqual(fs_allocator.field("timeout").scalar(), "45m")
        self.assertIn(
            "run-fs-allocator-fault-tests.sh",
            fs_allocator.field("script").text(),
        )
        self.assertIn("fs-allocator-evidence.py verify-archive",
                      fs_allocator.field("script").text())

    def test_anchor_policy_mutations_and_child_duplication_are_rejected(self) -> None:
        source = CI_PATH.read_text(encoding="utf-8")
        mutations = (
            replace_in_definition(
                source,
                ".agentos-mechanism",
                "    - agentos-qemu-calibrated",
                "    - wrong-qemu-runner",
            ),
            replace_in_definition(
                source,
                ".agentos-mechanism",
                "    when: always",
                "    when: on_success",
            ),
            replace_in_definition(
                source,
                "kernel-mechanism-regression",
                "  extends: .agentos-mechanism",
                "  extends: .agentos-mechanism\n  tags:\n    - agentos-qemu-calibrated",
            ),
            replace_in_definition(
                source,
                "physical-resource-regression",
                "  extends: .agentos-mechanism",
                "  extends: .missing-mechanism",
            ),
            replace_in_definition(
                source,
                "fs-allocator-fault-regression",
                "    - python3 scripts/fs-allocator-evidence.py verify-archive --archive \"${CI_PROJECT_DIR}/ci-artifacts/fs-allocator-evidence.tar\"",
                "    - echo archive-verification-removed",
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, mutation in enumerate(mutations):
                path = root / f"mutation-{index}.yml"
                path.write_text(mutation, encoding="utf-8")
                with self.subTest(index=index), self.assertRaises(CIContractError):
                    validate_repository_ci(path, BUDGET_PATH)

    def test_skip_capable_root_and_job_policies_are_rejected(self) -> None:
        source = CI_PATH.read_text(encoding="utf-8")
        mutations = (
            replace_in_definition(
                source, "reader-e2e", "  timeout: 15m",
                "  timeout: 15m\n  only:\n    - never",
            ),
            replace_in_definition(
                source, "reader-e2e", "  timeout: 15m",
                "  timeout: 15m\n  except:\n    - main",
            ),
            replace_in_definition(
                source, "reader-e2e", "  timeout: 15m",
                "  timeout: 15m\n  when: manual",
            ),
            replace_in_definition(
                source, ".agentos-mechanism", "  stage: test",
                "  stage: test\n  when: delayed\n  start_in: 1 hour",
            ),
            source.replace(
                "stages:\n",
                "workflow:\n  rules:\n    - when: never\n\nstages:\n",
                1,
            ),
            source.replace(
                "stages:\n",
                "default:\n  allow_failure: true\n\nstages:\n",
                1,
            ),
            source.replace(
                "stages:\n",
                "include:\n  - local: ci/skip.yml\n\nstages:\n",
                1,
            ),
        )
        self.assert_mutations_rejected(mutations)

    def test_wrapper_extra_command_and_environment_hijacks_are_rejected(self) -> None:
        source = CI_PATH.read_text(encoding="utf-8")
        mutations = (
            source.replace(
                "bash scripts/verify-dual-target-structure.sh 2>&1 |",
                "bash scripts/always-pass.sh scripts/verify-dual-target-structure.sh 2>&1 |",
                1,
            ),
            source.replace("make ci-check 2>&1 |", "make -f /tmp/always-pass.mk ci-check 2>&1 |", 1),
            source.replace(
                "python3 host_tools/test_plain_ucore_reader_e2e.py 2>&1 |",
                "python3 host_tools/always-pass.py host_tools/test_plain_ucore_reader_e2e.py 2>&1 |",
                1,
            ),
            source.replace(
                "bash scripts/run-agent-tests.sh 2>&1 |",
                "bash scripts/always-pass.sh scripts/run-agent-tests.sh 2>&1 |",
                1,
            ),
            source.replace(
                "bash scripts/run-ci-mechanism.sh physical-resource scripts/run-physical-resource-tests.sh",
                "bash scripts/always-pass.sh scripts/run-ci-mechanism.sh physical-resource scripts/run-physical-resource-tests.sh",
                1,
            ),
            source.replace(
                "python3 scripts/fs-allocator-evidence.py verify-archive --archive",
                "python3 host_tools/always-pass.py scripts/fs-allocator-evidence.py verify-archive --archive",
                1,
            ),
            replace_in_definition(
                source, "agent-regression",
                "    - python3 host_tools/remote_ci_evidence.py attest --job agent-regression --artifact-root ci-artifacts",
                "    - sed -i s/panic/passed/ ci-artifacts/agent-regression-job.log\n"
                "    - python3 host_tools/remote_ci_evidence.py attest --job agent-regression --artifact-root ci-artifacts",
            ),
            replace_in_definition(
                source, "reader-e2e", "    - mkdir -p ci-artifacts",
                "    - cd /tmp\n    - mkdir -p ci-artifacts",
            ),
            replace_in_definition(
                source, "kernel-budgets",
                "    - python3 host_tools/remote_ci_evidence.py attest --job kernel-budgets --artifact-root ci-artifacts",
                "    - PYTHONPATH=/tmp python3 host_tools/remote_ci_evidence.py attest --job kernel-budgets --artifact-root ci-artifacts",
            ),
            source.replace(
                "  DEBIAN_FRONTEND: noninteractive",
                "  DEBIAN_FRONTEND: noninteractive\n  BASH_ENV: /tmp/ci-hook",
                1,
            ),
            replace_in_definition(
                source, ".agentos-toolchain", "    - apt-get update",
                "    - apt-get update\n    - export PATH=/tmp/fake:${PATH}",
            ),
            source.replace(
                "      AGENT_TEST_CALIBRATE=0",
                "      BASH_ENV=/tmp/ci-hook AGENT_TEST_CALIBRATE=0",
                1,
            ),
        )
        self.assert_mutations_rejected(mutations)

    def test_artifact_and_definition_allowlists_are_structural(self) -> None:
        source = CI_PATH.read_text(encoding="utf-8")
        mutations = (
            replace_in_definition(
                source, "reader-e2e", "    when: always",
                "    when: on_success\n    # when: always",
            ),
            replace_in_definition(
                source, "reader-e2e",
                "      - ci-artifacts/remote-ci-attestation.json",
                "      # ci-artifacts/remote-ci-attestation.json",
            ),
            source + "\noptional-proof:\n  stage: test\n  script:\n    - exit 0\n",
            source.replace(
                "  TOOLPREFIX: riscv64-linux-gnu-",
                "  TOOLPREFIX: riscv64-linux-gnu-\n  PYTHONPATH: /tmp/fake",
                1,
            ),
        )
        self.assert_mutations_rejected(mutations)

    def assert_mutations_rejected(self, mutations: tuple[str, ...]) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, mutation in enumerate(mutations):
                path = root / f"mutation-{index}.yml"
                path.write_text(mutation, encoding="utf-8")
                with self.subTest(index=index), self.assertRaises(CIContractError):
                    validate_repository_ci(path, BUDGET_PATH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
