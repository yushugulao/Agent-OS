#!/usr/bin/env python3
"""Strict Observation v8 acceptance policy layered over the disk parser."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Callable
if __package__:
    from . import agent_observe_disk_contract as _contract
else:
    import agent_observe_disk_contract as _contract
DEFAULT_OBSERVE_CONTRACT = _contract.DEFAULT_OBSERVE_CONTRACT
ObservationEvidenceError = _contract.ObservationEvidenceError
ObservationLayout = _contract.ObservationLayout
load_observation_contract = _contract.load_observation_contract
DEFAULT_METADATA_CONTRACT = Path(__file__).resolve().parents[1] / "ci" / "agent-metadata-disk-format.json"
ACCEPTANCE_GEOMETRY = (6, 4, 2)
Verifier = Callable[..., dict[str, Any]]
IDENTITY_MARKER = re.compile(
    r"^agentobsreboot_ucore: boot1_durable_identity "
    r"scope=(?P<scope>[1-9][0-9]*) "
    r"lifecycle_id=(?P<lifecycle_id>[1-9][0-9]*) "
    r"lifecycle_generation=(?P<lifecycle_generation>[1-9][0-9]*) "
    r"agent_id=(?P<agent_id>[1-9][0-9]*) "
    r"receipt_sequence=(?P<receipt_sequence>[1-9][0-9]*) "
    r"receipt_record_hash=(?P<receipt_record_hash>[1-9][0-9]*) "
    r"receipt_id=(?P<receipt_id>[1-9][0-9]*)$"
)


def parse_boot1_identity(guest_log: str | bytes) -> dict[str, int]:
    if isinstance(guest_log, bytes):
        try:
            guest_log = guest_log.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ObservationEvidenceError("boot1 Guest log is not UTF-8") from error
    if not isinstance(guest_log, str):
        raise ObservationEvidenceError("boot1 Guest log has an invalid type")
    matches = [
        match for line in guest_log.splitlines()
        if (match := IDENTITY_MARKER.fullmatch(line))
    ]
    if len(matches) != 1:
        raise ObservationEvidenceError(
            f"boot1 Guest log contains {len(matches)} durable identity markers"
        )
    return {name: int(value, 10) for name, value in matches[0].groupdict().items()}


def validate_observation_acceptance(
    result: dict[str, Any], layout: ObservationLayout
) -> dict[str, Any]:
    """Require the v8 full-retention workload selected by the Guest identity."""
    geometry = (layout.records_per_scope, layout.latest_tail, layout.diversity_anchors)
    if geometry != ACCEPTANCE_GEOMETRY:
        raise ObservationEvidenceError("observation v8 acceptance geometry differs from 6/4/2")
    try:
        matched = result["arena"]["observation"]["matched_scope"]
        actual = (
            matched["record_count"], matched["retained_tail_count"],
            matched["retained_anchor_count"],
        )
        successful = matched["successful_records"]
        classes = set(matched["anchor_identity_classes"])
        kinds = matched["anchor_kinds"]
    except (KeyError, TypeError) as error:
        raise ObservationEvidenceError(
            "observation acceptance result lacks matched-scope evidence"
        ) from error
    failures = [
        f"{name}={value}, expected {wanted}"
        for name, value, wanted in zip(("record_count", "tail", "anchor"), actual, ACCEPTANCE_GEOMETRY)
        if value != wanted
    ]
    if successful <= ACCEPTANCE_GEOMETRY[0]:
        failures.append(f"successful_records={successful}, expected more than 6")
    failures.extend(
        f"anchor lacks {name} identity"
        for name in ("causal", "authority") if name not in classes
    )
    if not isinstance(kinds, list) or len(kinds) <= 1:
        failures.append("anchor audit kind is not diverse")
    if failures:
        raise ObservationEvidenceError(
            "observation v8 acceptance failed: " + "; ".join(failures)
        )
    accepted = dict(result)
    accepted["acceptance"] = {
        "profile": "observation-v8-tail-diversity", "matched_scope": matched["scope"],
        "record_count": actual[0], "successful_records": successful,
        "tail_count": actual[1], "anchor_count": actual[2],
        "anchor_has_causal": "causal" in classes,
        "anchor_has_authority": "authority" in classes,
        "anchor_kind_count": len(kinds), "status": "verified",
    }
    return accepted


def _disk_verifier() -> Verifier:
    if __package__:
        from .agent_observe_disk_evidence import verify_observation_image
    else:
        from agent_observe_disk_evidence import verify_observation_image
    return verify_observation_image


def verify_observation_acceptance(
    image_path: Path | str, guest_log: str | bytes,
    metadata_contract: Path | str = DEFAULT_METADATA_CONTRACT,
    observation_contract: Path | str = DEFAULT_OBSERVE_CONTRACT,
    verifier: Verifier | None = None,
) -> dict[str, Any]:
    result = (verifier or _disk_verifier())(
        image_path, guest_log, metadata_contract, observation_contract
    )
    return validate_observation_acceptance(result, load_observation_contract(observation_contract))


def main(verifier: Verifier | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--guest-log", type=Path, required=True)
    parser.add_argument("--metadata-contract", type=Path, default=DEFAULT_METADATA_CONTRACT)
    parser.add_argument("--observation-contract", type=Path, default=DEFAULT_OBSERVE_CONTRACT)
    args = parser.parse_args()
    try:
        result = verify_observation_acceptance(
            args.image, args.guest_log.read_bytes(), args.metadata_contract,
            args.observation_contract, verifier,
        )
    except (OSError, ObservationEvidenceError) as error:
        parser.error(str(error))
    print("observation_disk_evidence: " + json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


__all__ = [
    "DEFAULT_METADATA_CONTRACT", "IDENTITY_MARKER", "main",
    "parse_boot1_identity", "validate_observation_acceptance",
    "verify_observation_acceptance",
]
