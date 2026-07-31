#!/usr/bin/env python3
"""Stable facade for AgentOS measurement-source policy and receipts."""
from __future__ import annotations

import sys as _entry_sys


def _isolate_direct_entry_imports() -> None:
    """Use only interpreter-owned paths for top-level import resolution."""

    if __name__ != "__main__":
        return
    prefixes = {
        value.replace("\\", "/").rstrip("/").casefold()
        for value in (
            _entry_sys.base_prefix, _entry_sys.base_exec_prefix,
            _entry_sys.prefix, _entry_sys.exec_prefix,
        )
        if value
    }
    executable = _entry_sys.executable.replace("\\", "/").rstrip("/")
    if "/" in executable:
        prefixes.add(executable.rsplit("/", 1)[0].casefold())
    _entry_sys.path[:] = [
        value for value in _entry_sys.path
        if value and any(
            (normalized := value.replace("\\", "/").rstrip("/").casefold())
            == prefix or normalized.startswith(f"{prefix}/")
            for prefix in prefixes
        )
    ]


_isolate_direct_entry_imports()

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(
        Path(tempfile.gettempdir()) / f"agentos-pycache-{os.urandom(16).hex()}"
    )
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.append(str(Path(__file__).resolve().parent))

if __package__:
    from .agenteval_measurement_source_policy import (
        CONTROL_PLANE_POLICY,
        EVALUATION_SUITE_SOURCE_PATH,
        GUEST_POLICY_ROLES,
        POLICY_INVENTORY_SCHEMA,
        SEMANTIC_REPLAY_COMMON_SOURCES,
        SEMANTIC_REPLAY_SOURCE_POLICY,
        SOURCE_RELATIVE,
        _policy_entries,
        _receipt_source_paths,
        measurement_source_policy_inventory,
    )
    from .agenteval_measurement_source_receipt import (
        FORMAL_BOOT_COUNT,
        RECEIPT_SCHEMA,
        STOP_RULE,
        _strict_receipt_json,
        _write_receipt,
        build_measurement_source_receipt,
        validate_measurement_source_receipt_shape,
        verify_measurement_source_files,
        verify_measurement_source_receipt,
    )
    from .agenteval_measurement_source_validator import (
        APPROVED_PREPROCESSOR_DIRECTIVES,
        CONTRACT_VERSION,
        DURATION,
        POSTPROCESSING_CALLS,
        PRINT_FORMAT,
        ROOT,
        SOURCE,
        START,
        validate_source,
        validate_source_text,
    )
    from .functional_acceptance_source_contract import (
        CONTRACT_VERSION as FUNCTIONAL_CONTRACT_VERSION,
    )
    from .functional_acceptance_compile_contract import (
        CONTRACT_VERSION as FUNCTIONAL_COMPILE_CONTRACT_VERSION,
        validate_functional_compile_sources,
    )
else:
    from agenteval_measurement_source_policy import (
        CONTROL_PLANE_POLICY,
        EVALUATION_SUITE_SOURCE_PATH,
        GUEST_POLICY_ROLES,
        POLICY_INVENTORY_SCHEMA,
        SEMANTIC_REPLAY_COMMON_SOURCES,
        SEMANTIC_REPLAY_SOURCE_POLICY,
        SOURCE_RELATIVE,
        _policy_entries,
        _receipt_source_paths,
        measurement_source_policy_inventory,
    )
    from agenteval_measurement_source_receipt import (
        FORMAL_BOOT_COUNT,
        RECEIPT_SCHEMA,
        STOP_RULE,
        _strict_receipt_json,
        _write_receipt,
        build_measurement_source_receipt,
        validate_measurement_source_receipt_shape,
        verify_measurement_source_files,
        verify_measurement_source_receipt,
    )
    from agenteval_measurement_source_validator import (
        APPROVED_PREPROCESSOR_DIRECTIVES,
        CONTRACT_VERSION,
        DURATION,
        POSTPROCESSING_CALLS,
        PRINT_FORMAT,
        ROOT,
        SOURCE,
        START,
        validate_source,
        validate_source_text,
    )
    from functional_acceptance_source_contract import (
        CONTRACT_VERSION as FUNCTIONAL_CONTRACT_VERSION,
    )
    from functional_acceptance_compile_contract import (
        CONTRACT_VERSION as FUNCTIONAL_COMPILE_CONTRACT_VERSION,
        validate_functional_compile_sources,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--repo", type=Path, default=ROOT)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--write-receipt", type=Path)
    modes.add_argument("--verify-receipt", type=Path)
    parser.add_argument("--source-commit")
    args = parser.parse_args()
    try:
        if args.write_receipt is not None:
            if args.source_commit is None:
                raise ValueError("--write-receipt requires --source-commit")
            receipt = build_measurement_source_receipt(
                args.repo, source_commit=args.source_commit
            )
            _write_receipt(args.write_receipt, receipt)
        elif args.verify_receipt is not None:
            receipt = _strict_receipt_json(args.verify_receipt)
            verify_measurement_source_receipt(
                receipt, args.repo, expected_commit=args.source_commit
            )
        else:
            if args.source_commit is not None:
                raise ValueError("--source-commit requires a receipt mode")
            validate_source(args.source)
            validate_functional_compile_sources(args.repo)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    print("agenteval measurement source contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
