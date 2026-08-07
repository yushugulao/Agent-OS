#!/usr/bin/env python3
"""构建并验证不可变评测源回执。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

if __package__:
    from .agenteval_measurement_source_policy import (
        POLICY_INVENTORY_SCHEMA,
        SOURCE_RELATIVE,
        _receipt_source_paths,
        measurement_source_policy_inventory,
    )
    from .agenteval_measurement_source_validator import (
        CONTRACT_VERSION,
        validate_source,
    )
    from .functional_acceptance_source_contract import (
        CONTRACT_VERSION as FUNCTIONAL_CONTRACT_VERSION,
    )
    from .functional_acceptance_compile_contract import (
        CONTRACT_VERSION as FUNCTIONAL_COMPILE_CONTRACT_VERSION,
        validate_functional_compile_sources,
    )
    from .scenario_timing_source_contract import (
        CONTRACT_VERSION as SCENARIO_VERSION,
        validate_sources as validate_scenario_sources,
    )
else:
    from agenteval_measurement_source_policy import (
        POLICY_INVENTORY_SCHEMA,
        SOURCE_RELATIVE,
        _receipt_source_paths,
        measurement_source_policy_inventory,
    )
    from agenteval_measurement_source_validator import (
        CONTRACT_VERSION,
        validate_source,
    )
    from functional_acceptance_source_contract import (
        CONTRACT_VERSION as FUNCTIONAL_CONTRACT_VERSION,
    )
    from functional_acceptance_compile_contract import (
        CONTRACT_VERSION as FUNCTIONAL_COMPILE_CONTRACT_VERSION,
        validate_functional_compile_sources,
    )
    from scenario_timing_source_contract import (
        CONTRACT_VERSION as SCENARIO_VERSION,
        validate_sources as validate_scenario_sources,
    )


ROOT = Path(__file__).resolve().parents[1]
RECEIPT_SCHEMA = "agentos-measurement-source-receipt-v6"
FORMAL_BOOT_COUNT = 7
STOP_RULE = f"fixed_{FORMAL_BOOT_COUNT}_boots_per_source_commit"


def validate_measurement_source_receipt_shape(
    receipt: object, *, expected_commit: str | None = None
) -> dict[str, Any]:
    if not isinstance(receipt, dict) or set(receipt) != {
        "contract_versions", "formal_boot_count", "policy_inventory", "schema",
        "source_commit", "sources", "stop_rule",
    }:
        raise ValueError("measurement source receipt fields differ")
    if (
        receipt["schema"] != RECEIPT_SCHEMA
        or receipt["stop_rule"] != STOP_RULE
        or receipt["formal_boot_count"] != FORMAL_BOOT_COUNT
        or type(receipt["formal_boot_count"]) is not int
    ):
        raise ValueError("measurement source receipt header is invalid")
    commit = receipt["source_commit"]
    if (
        not isinstance(commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", commit) is None
        or (expected_commit is not None and commit != expected_commit)
    ):
        raise ValueError("measurement source receipt commit is invalid")
    if receipt["contract_versions"] != {
        "functional": FUNCTIONAL_CONTRACT_VERSION,
        "functional_compile": FUNCTIONAL_COMPILE_CONTRACT_VERSION,
        "micro": CONTRACT_VERSION,
        "policy": POLICY_INVENTORY_SCHEMA,
        "scenario": SCENARIO_VERSION,
    }:
        raise ValueError("measurement source contract versions differ")
    if receipt["policy_inventory"] != measurement_source_policy_inventory():
        raise ValueError("measurement source policy inventory differs")
    sources = receipt["sources"]
    expected_paths = _receipt_source_paths()
    if not isinstance(sources, list) or len(sources) != len(expected_paths):
        raise ValueError("measurement source receipt source count differs")
    for index, (record, expected_path) in enumerate(zip(sources, expected_paths)):
        if not isinstance(record, dict) or set(record) != {
            "bytes", "path", "sha256",
        }:
            raise ValueError(f"measurement source record {index} fields differ")
        if record["path"] != expected_path:
            raise ValueError(f"measurement source record {index} path differs")
        if (
            type(record["bytes"]) is not int
            or record["bytes"] <= 0
            or not isinstance(record["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None
        ):
            raise ValueError(f"measurement source record {index} receipt is invalid")
    return receipt


def build_measurement_source_receipt(
    repo: Path = ROOT, *, source_commit: str
) -> dict[str, Any]:
    repo = repo.resolve(strict=True)
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ValueError("measurement source commit must be lowercase 40-hex")
    validate_source(repo / SOURCE_RELATIVE)
    validate_functional_compile_sources(repo)
    validate_scenario_sources(repo)
    records = []
    for relative in _receipt_source_paths():
        raw = (repo / relative).read_bytes()
        records.append({
            "bytes": len(raw),
            "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    receipt = {
        "contract_versions": {
            "functional": FUNCTIONAL_CONTRACT_VERSION,
            "functional_compile": FUNCTIONAL_COMPILE_CONTRACT_VERSION,
            "micro": CONTRACT_VERSION,
            "policy": POLICY_INVENTORY_SCHEMA,
            "scenario": SCENARIO_VERSION,
        },
        "formal_boot_count": FORMAL_BOOT_COUNT,
        "policy_inventory": measurement_source_policy_inventory(),
        "schema": RECEIPT_SCHEMA,
        "source_commit": source_commit,
        "sources": records,
        "stop_rule": STOP_RULE,
    }
    return validate_measurement_source_receipt_shape(
        receipt, expected_commit=source_commit
    )


def verify_measurement_source_receipt(
    receipt: object, repo: Path = ROOT, *, expected_commit: str | None = None
) -> dict[str, Any]:
    parsed = verify_measurement_source_files(
        receipt, repo, expected_commit=expected_commit
    )
    validate_source(repo / SOURCE_RELATIVE)
    validate_functional_compile_sources(repo)
    validate_scenario_sources(repo)
    return parsed


def verify_measurement_source_files(
    receipt: object, repo: Path = ROOT, *, expected_commit: str | None = None
) -> dict[str, Any]:
    """验证已通过校验的回执所绑定的不可变源码字节。"""
    parsed = validate_measurement_source_receipt_shape(
        receipt, expected_commit=expected_commit
    )
    repo = repo.resolve(strict=True)
    for record in parsed["sources"]:
        raw = (repo / record["path"]).read_bytes()
        if (
            len(raw) != record["bytes"]
            or hashlib.sha256(raw).hexdigest() != record["sha256"]
        ):
            raise ValueError(
                f"measurement source differs from receipt: {record['path']}"
            )
    return parsed


def _strict_receipt_json(path: Path) -> dict[str, Any]:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate receipt key: {key}")
            value[key] = item
        return value

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate,
        parse_constant=lambda item: (_ for _ in ()).throw(
            ValueError(f"non-finite receipt number: {item}")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError("measurement source receipt must be an object")
    return value


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace measurement source receipt: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError(f"temporary receipt path already exists: {temporary}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(
                receipt, handle, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise ValueError(
                f"refusing to replace measurement source receipt: {path}"
            ) from error
        temporary.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()
