#!/usr/bin/env python3
"""AgentOS mechanism-contract evidence and retired disk-profile registry."""

from __future__ import annotations

from evidence_semantic_common import (
    EvidenceSemanticError,
    ValidationContext,
    _reject_tokens,
    _text,
)


MECHANISM_CONTRACT_MARKERS = (
    "[mechanism-contract] workflow-credit-domain static=passed",
    "[mechanism-contract] fence-sealed-evidence-ring static=passed",
    "[mechanism-contract] agent-live-query-fs static=passed",
    "[mechanism-contract] workflow-fence static=passed",
    "[mechanism-contract] metadata-recovery retired_by_design replacement=none current=agent-live-query-fs",
    "[mechanism-contract] observe-recovery retired_by_design replacement=none current=fence-sealed-evidence-ring",
    "[mechanism-contract] raw-bank-image retired_by_design replacement=none current=workflow-fence-receipt",
)

# These selectors are kept as explicit tombstones so an old evidence bundle is
# diagnosed as retired rather than silently becoming an unknown profile.
RETIRED_BY_DESIGN = {
    "metadata-recovery": "agent-live-query-fs",
    "metadata-recovery.log": "agent-live-query-fs",
    "observe-recovery": "fence-sealed-evidence-ring",
    "observe-recovery.log": "fence-sealed-evidence-ring",
    "observe-recovery-before-reap.img": "workflow-fence-receipt",
    "raw-bank-image": "workflow-fence-receipt",
}


def validate_mechanism_contracts(ctx: ValidationContext) -> None:
    text = _text(
        ctx.raw_dir / "agent-mechanism-contracts.log",
        "Agent mechanism contract log",
    )
    lines = text.splitlines()
    for marker in MECHANISM_CONTRACT_MARKERS:
        if lines.count(marker) != 1:
            raise EvidenceSemanticError(
                f"mechanism contract marker must be one complete line: {marker}"
            )
    positions = [lines.index(marker) for marker in MECHANISM_CONTRACT_MARKERS]
    if positions != sorted(positions):
        raise EvidenceSemanticError("mechanism contract markers are out of order")
    _reject_tokens(
        text,
        ("FAILED", "Traceback", "check failed", "contracts: failed", "status=failed"),
        "Agent mechanism contract log",
    )


def validate_metadata(_ctx: ValidationContext) -> None:
    """Compatibility entry point with an explicit retirement diagnostic."""
    raise EvidenceSemanticError(
        "metadata-recovery retired_by_design; current evidence is agent-live-query-fs"
    )


__all__ = [
    "MECHANISM_CONTRACT_MARKERS",
    "RETIRED_BY_DESIGN",
    "validate_mechanism_contracts",
    "validate_metadata",
]
