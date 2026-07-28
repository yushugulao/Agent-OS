#!/usr/bin/env python3
"""Fail closed if pool-affine physical pages become count-transferable."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "os/resource_controller.c").read_text(encoding="utf-8")
HEADER = (ROOT / "os/resource_controller.h").read_text(encoding="utf-8")
PROFILE = (ROOT / "os/physical_page_test.c").read_text(encoding="utf-8")
GUEST = (ROOT / "user/src/physicalresource_ucore.c").read_text(encoding="utf-8")
ABI = (ROOT / "physical_page_test_abi.h").read_text(encoding="utf-8")


class ContractError(RuntimeError):
    pass


def section(text: str, begin: str, end: str) -> str:
    start = text.find(begin)
    stop = text.find(end, start + len(begin))
    if start < 0 or stop < 0:
        raise ContractError(f"missing section {begin}")
    return text[start:stop]


def validate(source: str) -> None:
    required = (
        "RESOURCE_KIND_COUNT_TRANSFERABLE",
        "RESOURCE_KIND_POOL_AFFINE",
        "[RESOURCE_PHYSICAL_PAGE] = RESOURCE_KIND_POOL_AFFINE",
        "resource_count_only_mutation_allowed",
    )
    for token in required:
        if token not in HEADER + source:
            raise ContractError(f"missing resource policy token {token}")
    imported = section(source, "int resource_import_usage(",
                       "int resource_transfer_usage(")
    transferred = section(source, "int resource_transfer_usage_flags(",
                           "int resource_reconcile_usage(")
    reconciled = section(source, "int resource_reconcile_usage(",
                         "uint64 resource_account_usage(")
    if "resource_count_only_mutation_allowed(amounts)" not in imported:
        raise ContractError("physical import is not rejected")
    if "resource_count_only_mutation_allowed(amounts)" not in transferred:
        raise ContractError("physical transfer is not rejected")
    if "RESOURCE_KIND_COUNT_TRANSFERABLE" not in reconciled:
        raise ContractError("physical reconciliation is not rejected")


validate(SOURCE)
mutated = SOURCE.replace(
    "[RESOURCE_PHYSICAL_PAGE] = RESOURCE_KIND_POOL_AFFINE",
    "[RESOURCE_PHYSICAL_PAGE] = RESOURCE_KIND_COUNT_TRANSFERABLE",
    1,
)
try:
    validate(mutated)
except ContractError:
    pass
else:
    raise SystemExit("mutation survived: PHYSICAL_PAGE became transferable")

for token in (
    "physical_page_transfer_receipts",
    "resource_transfer_usage(",
    "resource_import_usage(",
    "resource_reconcile_usage(",
    "PHYSICAL_PAGE_STEP_TRANSFER",
    "PHYSICAL_PAGE_STEP_SOURCE_USAGE",
):
    if token not in PROFILE + ABI:
        raise SystemExit(f"missing runtime resource selftest token {token}")
if "physical_transfer_rejected=1 mixed_atomic=1" not in GUEST:
    raise SystemExit("missing Guest transfer-policy assertion")

print("[physical-resource] pool-affine transfer policy passed")
