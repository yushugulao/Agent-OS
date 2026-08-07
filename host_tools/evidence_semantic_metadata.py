#!/usr/bin/env python3
"""Offline semantic replay for the 45-leg metadata recovery profile."""

from __future__ import annotations

import re

from evidence_semantic_common import (
    EvidenceSemanticError,
    ValidationContext,
    _call,
    _expect_tags,
    _load_module,
    _parse_combined,
    _require_line,
)


FINAL_MARKER = (
    "[metadata-recovery] power-cut, bounded boot reprobe, "
    "over-burst terminal-peer recovery, and EIO recovery passed"
)
AUTHORITY = re.compile(
    r"metadata_authority_check: kind=(?P<kind>busy|io|interrupted) "
    r"newer_bank=(?P<bank>[01]) before=(?P<before>[1-9][0-9]*) "
    r"after=(?P<after>[1-9][0-9]*) rollback=0"
)
LARGE = re.compile(
    r"metadata_large_bank_check: "
    r"peer=(?P<peer>valid|absent|uncommitted|corrupt) "
    r"selected=valid over_burst=1"
)
FAULT = re.compile(
    r"agentmeta_boot_fault: kind=(?:busy|io|interrupted) "
    r"bank=(?P<bank>[01]) remaining=[0-9]+"
)


def _exact_stdout_matches(text: str, pattern: re.Pattern[str], label: str):
    matches = []
    prefix = pattern.pattern.split(":", 1)[0] + ":"
    for line in text.splitlines():
        match = pattern.fullmatch(line)
        if match is not None:
            matches.append(match)
        elif line.startswith(prefix):
            raise EvidenceSemanticError(f"malformed {label} marker")
    return matches


def validate_metadata(ctx: ValidationContext) -> None:
    result = _parse_combined(
        ctx.raw_dir / "metadata-recovery.log",
        "metadata-recovery.log",
        "metadata-recovery",
    )
    _require_line(result.stdout, FINAL_MARKER, "metadata-recovery.log runner output")
    expected = {
        f"metadata-agentmeta{program}_ucore-{bank}-{phase}"
        for bank in ("primary", "mirror")
        for phase in range(1, 9)
        for program in ("crash", "recover")
    }
    expected.add("metadata-agentmetarecover_ucore-select-baseline")
    expected.update(
        f"metadata-agentmetatransient_ucore-boot-{kind}-{target}"
        for kind in ("busy", "io", "interrupted")
        for target in ("all", "newer")
    )
    expected.add("metadata-agentmetalarge_ucore-large-seed")
    expected.update(
        f"metadata-agentmetatransient_ucore-large-{terminal}"
        for terminal in ("absent", "uncommitted", "corrupt")
    )
    expected.update(
        {
            "metadata-agentmetarecover_ucore-eio-baseline",
            "metadata-agentmetaeio_ucore-eio",
        }
    )
    if len(expected) != 45:
        raise EvidenceSemanticError("metadata recovery registry count drifted")
    _expect_tags(result.guests, expected, "metadata-recovery.log")

    crash_validator = _load_module(ctx, "scripts/validate-metadata-crash-log.py")
    for bank in ("primary", "mirror"):
        for phase in range(1, 9):
            crash_tag = f"metadata-agentmetacrash_ucore-{bank}-{phase}"
            _call(
                crash_validator,
                "validate_log",
                crash_tag,
                result.guests[crash_tag].encode("utf-8"),
                bank,
                phase,
            )
            recovery_tag = f"metadata-agentmetarecover_ucore-{bank}-{phase}"
            recovery = result.guests[recovery_tag]
            for marker in (
                "agentmetarecover_ucore: readonly_recovery=1 metadata_available=1",
                "agentmetarecover_ucore: query_found=0 returned=0",
                "agentmetarecover_ucore: parent passed",
            ):
                _require_line(recovery, marker, recovery_tag)

    baseline_tag = "metadata-agentmetarecover_ucore-select-baseline"
    for marker in (
        "agentmetarecover_ucore: query_found=0 returned=0",
        "agentmetarecover_ucore: parent passed",
    ):
        _require_line(result.guests[baseline_tag], marker, baseline_tag)

    reprobe = _load_module(ctx, "scripts/validate-metadata-reprobe-log.py")
    for kind in ("busy", "io", "interrupted"):
        for target in ("all", "newer"):
            tag = f"metadata-agentmetatransient_ucore-boot-{kind}-{target}"
            raw = result.guests[tag].encode("utf-8")
            bank = "all"
            if target == "newer":
                banks = {
                    match.group("bank")
                    for line in result.guests[tag].splitlines()
                    if (match := FAULT.fullmatch(line)) is not None
                }
                if len(banks) != 1:
                    raise EvidenceSemanticError(
                        f"{tag} does not identify exactly one newer bank"
                    )
                bank = next(iter(banks))
            _call(reprobe, "validate_log", tag, raw, kind, bank, False)

    authority = _exact_stdout_matches(
        result.stdout, AUTHORITY, "metadata authority"
    )
    if (
        len(authority) != 3
        or {match.group("kind") for match in authority}
        != {"busy", "io", "interrupted"}
        or any(int(match.group("after")) <= int(match.group("before")) for match in authority)
    ):
        raise EvidenceSemanticError("metadata newer-bank authority evidence differs")

    seed_tag = "metadata-agentmetalarge_ucore-large-seed"
    for marker in (
        "agentmetalarge_ucore: runtime_reload_completed=1",
        "agentmetalarge_ucore: seed_ready=1 records=32",
    ):
        _require_line(result.guests[seed_tag], marker, seed_tag)
    for terminal in ("absent", "uncommitted", "corrupt"):
        tag = f"metadata-agentmetatransient_ucore-large-{terminal}"
        _call(
            reprobe,
            "validate_log",
            tag,
            result.guests[tag].encode("utf-8"),
            "busy",
            "1",
            True,
        )
    large = _exact_stdout_matches(result.stdout, LARGE, "metadata large-bank")
    if (
        len(large) != 4
        or {match.group("peer") for match in large}
        != {"valid", "absent", "uncommitted", "corrupt"}
    ):
        raise EvidenceSemanticError("metadata over-burst peer evidence differs")

    eio_baseline = result.guests["metadata-agentmetarecover_ucore-eio-baseline"]
    for marker in (
        "agentmetarecover_ucore: query_found=0 returned=0",
        "agentmetarecover_ucore: parent passed",
    ):
        _require_line(eio_baseline, marker, "metadata EIO baseline")
    eio = result.guests["metadata-agentmetaeio_ucore-eio"]
    for marker in (
        "agentmetaeio_ucore: transient_eio_repaired=1",
        "agentmetaeio_ucore: parent passed",
    ):
        _require_line(eio, marker, "metadata EIO Guest log")


__all__ = ["FINAL_MARKER", "validate_metadata"]
