#!/usr/bin/env python3
"""Validate the repository's deliberately disabled GitLab pipeline."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


class CIContractError(RuntimeError):
    pass


CANONICAL_CI = """workflow:
  rules:
    - when: never

local-validation-reference:
  rules:
    - when: never
  script:
    - echo \"Remote execution is disabled. Run make full-verify locally.\"
"""


@dataclass(frozen=True)
class RunnerlessCIContract:
    remote_execution_disabled: bool = True
    visible_job: str = "local-validation-reference"


def _read_ci(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise CIContractError(f"CI file is missing or unsafe: {path}")
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CIContractError(f"CI file is not readable UTF-8: {error}") from error
    if "\r" in text or "\t" in text:
        raise CIContractError("CI file must use canonical LF-only indentation")
    return text


def validate_repository_ci(path: Path, budget_path: Path) -> RunnerlessCIContract:
    """Require the exact no-Runner policy while preserving the public API."""
    del budget_path
    text = _read_ci(path)
    if text != CANONICAL_CI:
        raise CIContractError(
            "GitLab CI must contain only the canonical runnerless workflow and "
            "its never-instantiated visible reference job"
        )
    return RunnerlessCIContract()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("verify",))
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--budget-config", type=Path, required=True)
    args = parser.parse_args()
    try:
        contract = validate_repository_ci(args.path, args.budget_config)
    except CIContractError as error:
        parser.error(str(error))
    if not contract.remote_execution_disabled:
        parser.error("remote execution is not disabled")
    print("GitLab CI runnerless contract: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
