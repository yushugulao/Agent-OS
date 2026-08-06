#!/usr/bin/env python3
"""Mutation tests for the fixed no-Runner GitLab contract."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gitlab_ci_contract import CIContractError, validate_repository_ci


ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".gitlab-ci.yml"
BUDGET_PATH = ROOT / "ci" / "kernel-budgets.json"


class GitLabCIContractTests(unittest.TestCase):
    def test_repository_disables_remote_execution(self) -> None:
        contract = validate_repository_ci(CI_PATH, BUDGET_PATH)
        self.assertTrue(contract.remote_execution_disabled)
        self.assertEqual(contract.visible_job, "local-validation-reference")

    def test_execution_enabling_mutations_are_rejected(self) -> None:
        source = CI_PATH.read_text(encoding="utf-8")
        mutations = (
            source.replace("workflow:\n  rules:\n    - when: never\n\n", "", 1),
            source.replace("    - when: never", "    - when: always", 1),
            source.replace("    - when: never", "    - when: on_success", 1),
            source.replace(
                "local-validation-reference:\n  rules:\n    - when: never\n",
                "local-validation-reference:\n",
                1,
            ),
            source + "\nschedulable-job:\n  script:\n    - echo scheduled\n",
        )
        self.assert_mutations_rejected(mutations)

    def test_remote_runner_surfaces_are_rejected(self) -> None:
        source = CI_PATH.read_text(encoding="utf-8")
        insert = "local-validation-reference:\n"
        mutations = (
            "include:\n  - local: ci/jobs.yml\n\n" + source,
            source.replace(insert, insert + "  tags:\n    - runner\n", 1),
            source.replace(insert, insert + "  image: ubuntu:latest\n", 1),
            source.replace(insert, insert + "  before_script:\n    - make doctor\n", 1),
            source.replace(insert, insert + "  artifacts:\n    paths:\n      - output.log\n", 1),
            source.replace(
                "    - echo \"Remote execution is disabled. Run make full-verify locally.\"",
                "    - python3 host_tools/remote_ci_evidence.py attest --job fake "
                "--artifact-root ci-artifacts",
                1,
            ),
        )
        self.assert_mutations_rejected(mutations)

    def test_missing_and_symlinked_contracts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "missing.yml"
            with self.assertRaises(CIContractError):
                validate_repository_ci(missing, BUDGET_PATH)
            link = root / "linked.yml"
            try:
                link.symlink_to(CI_PATH)
            except OSError:
                return
            if not link.is_symlink():
                return
            with self.assertRaises(CIContractError):
                validate_repository_ci(link, BUDGET_PATH)

    def assert_mutations_rejected(self, mutations: tuple[str, ...]) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, mutation in enumerate(mutations):
                path = root / f"mutation-{index}.yml"
                path.write_text(mutation, encoding="utf-8")
                with self.subTest(index=index), self.assertRaises(CIContractError):
                    validate_repository_ci(path, BUDGET_PATH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
