#!/usr/bin/env python3
"""Source contract for demo/reference products and their exported catalog rows."""

from __future__ import annotations

from pathlib import Path

from benchmark_source_contract import _extract_call, _split_arguments


class ReferenceCatalogError(RuntimeError):
    pass


PRODUCTS = {
    "rp_coherenceplane.c": ("rp_coherence",),
    "rp_decsupport.c": (
        "rp_decsupport",
        "rp_decopt",
        "rp_deccrit",
        "rp_decscore",
        "rp_decpacket",
    ),
}
EXPORTS = {
    "rp_coherenceplane.c": {
        "marker": "coherence",
        "destinations": {"rp_web_bundle", "rp_review_dashboard", "rp_agentcmp"},
    },
    "rp_decsupport.c": {
        "marker": "decision_support",
        "destinations": {
            "rp_package",
            "rp_web_bundle",
            "rp_review_dashboard",
            "rp_agentcmp",
        },
    },
}
FILE_ENVELOPE = (
    "evidence_file_role=demo_reference",
    "evidence_file_generation=demo_expected",
    "evidence_file_status=reference_ready",
)
RECORD_ENVELOPE = (
    "evidence_role=demo_reference",
    "catalog_generation=demo_expected",
    "status=reference_ready",
)


def _calls(source: str, function: str) -> list[list[str]]:
    calls: list[list[str]] = []
    start = 0
    needle = function + "("
    while True:
        position = source.find(needle, start)
        if position < 0:
            return calls
        opening = position + len(function)
        try:
            calls.append(_split_arguments(_extract_call(source, opening)))
        except ValueError as error:
            raise ReferenceCatalogError(str(error)) from error
        start = opening + 1


def _string_argument(value: str) -> str:
    return value.replace('" "', "").replace("\n", "")


def validate_reference_source(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ReferenceCatalogError(f"reference source is missing or unsafe: {path}")
    name = path.name
    if name not in PRODUCTS:
        raise ReferenceCatalogError(f"unsupported reference source: {name}")
    source = path.read_text(encoding="utf-8")
    writes = _calls(source, "rp_write_file")
    by_name: dict[str, list[str]] = {}
    for arguments in writes:
        if len(arguments) != 2:
            continue
        destination = arguments[0].strip().strip('"')
        if destination in PRODUCTS[name]:
            if destination in by_name:
                raise ReferenceCatalogError(f"duplicate reference product: {destination}")
            by_name[destination] = arguments
    missing = set(PRODUCTS[name]) - set(by_name)
    if missing:
        raise ReferenceCatalogError(
            "missing reference products: " + ", ".join(sorted(missing))
        )
    for destination, arguments in by_name.items():
        body = _string_argument(arguments[1])
        for field in FILE_ENVELOPE:
            if body.count(field) != 1:
                raise ReferenceCatalogError(
                    f"{destination} must declare {field} exactly once"
                )

    export_spec = EXPORTS[name]
    seen: set[str] = set()
    for arguments in _calls(source, "rp_append_file"):
        if len(arguments) != 2:
            continue
        destination = arguments[0].strip().strip('"')
        body = _string_argument(arguments[1])
        if destination not in export_spec["destinations"]:
            continue
        if export_spec["marker"] not in body:
            continue
        for field in RECORD_ENVELOPE:
            if body.count(field) != 1:
                raise ReferenceCatalogError(
                    f"{destination} export must declare {field} exactly once"
                )
        if "result=passed" in body or ";status=passed" in body:
            raise ReferenceCatalogError(
                f"{destination} export promotes reference data to a pass claim"
            )
        seen.add(destination)
    missing_exports = export_spec["destinations"] - seen
    if missing_exports:
        raise ReferenceCatalogError(
            "missing tagged reference exports: " + ", ".join(sorted(missing_exports))
        )

    marker = f"rp_{name.removeprefix('rp_').removesuffix('.c')}: evidence_role=demo_reference"
    if source.count(marker) != 1 or "status=reference_ready" not in source[source.index(marker):]:
        raise ReferenceCatalogError("reference Guest marker is missing or malformed")
