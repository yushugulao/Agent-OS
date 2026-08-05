#!/usr/bin/env python3
"""Mutation guards for the work-conserving hierarchical I/O governor."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIO = (ROOT / "os/bio.c").read_text(encoding="utf-8")
POLICY = (ROOT / "io_policy.h").read_text(encoding="utf-8")
IOBUDGET = (ROOT / "user/src/iobudget_ucore.c").read_text(encoding="utf-8")


class ContractError(RuntimeError):
    pass


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\(", source)
    if match is None:
        raise ContractError(f"missing function {name}")
    opening = source.find("{", match.end())
    if opening < 0:
        raise ContractError(f"missing body {name}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    raise ContractError(f"unterminated body {name}")


def compact(value: str) -> str:
    return " ".join(value.split())


def ordered(body: str, *tokens: str) -> bool:
    positions = [body.find(token) for token in tokens]
    return all(position >= 0 for position in positions) and positions == sorted(positions)


def define(source: str, name: str) -> int:
    match = re.search(rf"^#define\s+{name}\s+(\d+)U$", source, re.MULTILINE)
    if match is None:
        raise ContractError(f"missing numeric policy {name}")
    return int(match.group(1))


def validate(bio: str, policy: str, iobudget: str) -> None:
    if define(policy, "IO_POLICY_VERSION") != 5:
        raise ContractError("work-conserving policy version is not published")
    if define(policy, "IO_POLICY_SHARED_BURST") != define(
            policy, "IO_POLICY_DEVICE_BURST"):
        raise ContractError("shared burst does not mirror the device envelope")
    if define(policy, "IO_POLICY_SHARED_REFILL") != define(
            policy, "IO_POLICY_DEVICE_REFILL"):
        raise ContractError("shared refill does not mirror the device envelope")

    bundle = compact(function_body(bio, "io_rate_bundle"))
    reserved_device_debt = (
        "if (!shared) endpoints[1].flags = "
        "RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;"
    )
    if (reserved_device_debt not in bundle or
            bundle.count("RESOURCE_RATE_ENDPOINT_ALLOW_DEBT") != 1 or
            "endpoints[0].flags" in bundle or
            "io_device_protected" in bundle):
        raise ContractError(
            "shared bundle can acquire debt or reserved device debt is missing"
        )

    competing = compact(function_body(bio, "io_has_competing_owner"))
    if "for (" in competing or "active_owner_states" not in competing:
        raise ContractError("per-transfer competition detection is not constant-time")
    borrow = compact(function_body(bio, "io_can_borrow_idle_capacity"))
    for token in (
        "io_class != IO_POLICY_CLASS_BACKGROUND",
        "!state->retiring && !state->quiesced",
        "!io_any_admission_waiters()",
        "!io_has_competing_owner(state)",
    ):
        if token not in borrow:
            raise ContractError(f"idle-capacity gate missing {token}")

    charge = compact(function_body(bio, "io_rate_charge_transfer"))
    reserved_bundle = "io_rate_bundle(state, io_class, 0, endpoints)"
    shared_bundle = "io_rate_bundle(state, io_class, 1, endpoints)"
    if not ordered(
        charge,
        reserved_bundle,
        "resource_rate_charge_many(endpoints, 2) == 0",
        "state->reserved_grants++",
        "lane.debt == 0 && lane.pending_debt == 0",
        "io_can_borrow_idle_capacity(state, io_class)",
        shared_bundle,
        "state->shared_grants++",
        "endpoints[0].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT",
        "endpoints[1].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT",
        "state->throttles++",
    ):
        raise ContractError("transfer charge order is not reserved/shared/debt")
    if charge.count(reserved_bundle) != 2 or charge.count(shared_bundle) != 1:
        raise ContractError("transfer fallback does not rebuild a reserved bundle")
    fallback = charge.rsplit(reserved_bundle, 1)[1]
    if not ordered(
        fallback,
        "endpoints[0].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT",
        "endpoints[1].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT",
        "state->throttles++",
        "resource_rate_charge_many(endpoints, 2)",
    ):
        raise ContractError("bounded fallback debt is not owner plus device")

    direct = compact(function_body(bio, "io_reserve_direct"))
    if ("lane.debt == 0 && lane.pending_debt == 0" not in direct or
            "io_can_borrow_idle_capacity(state, io_class)" not in direct):
        raise ContractError("admission can borrow while indebted or contended")

    acquire = compact(function_body(bio, "io_active_request_acquire"))
    release = compact(function_body(bio, "io_active_request_release"))
    if ("state->active_requests++ == 0" not in acquire or
            "io_policy.active_owner_states++" not in acquire or
            "--state->active_requests == 0" not in release or
            "io_policy.active_owner_states--" not in release):
        raise ContractError("active-owner accounting is not transition based")
    if bio.count("io_active_request_acquire(state);") != 2:
        raise ContractError("an I/O request begin bypasses active-owner accounting")
    if bio.count("io_active_request_release(state);") != 3:
        raise ContractError("an I/O request end bypasses active-owner accounting")

    if "IO_POLICY_SHARED_BURST + 1" in iobudget:
        raise ContractError("dynamic pressure scales with the elastic envelope")
    pressure = compact(function_body(iobudget, "run_lineage_attacker"))
    for token in (
        "borrowed = shared_decisions > 0;",
        "refill_covered = after.refills > before.refills && throttle_decisions == 0;",
        "check(borrowed || refill_covered,",
    ):
        if token not in pressure:
            raise ContractError(
                "dynamic probe does not distinguish idle loans from sustained refill"
            )


validate(BIO, POLICY, IOBUDGET)

MUTATIONS = (
    (BIO, POLICY.replace("#define IO_POLICY_VERSION 5U", "#define IO_POLICY_VERSION 4U", 1), IOBUDGET),
    (BIO, POLICY.replace("#define IO_POLICY_SHARED_REFILL 280U", "#define IO_POLICY_SHARED_REFILL 16U", 1), IOBUDGET),
    (BIO.replace("if (!shared)", "if (!shared || io_device_protected(state->owner, io_class))", 1), POLICY, IOBUDGET),
    (BIO.replace("if (!shared)", "if (shared)", 1), POLICY, IOBUDGET),
    (BIO.replace("endpoints[1].flags =", "endpoints[0].flags =", 1), POLICY, IOBUDGET),
    (BIO.replace("\t(void)io_rate_bundle(state, io_class, 0, endpoints);\n\tendpoints[0].flags", "\tendpoints[0].flags", 1), POLICY, IOBUDGET),
    (BIO.replace("endpoints[1].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;", "", 1), POLICY, IOBUDGET),
    (BIO.replace("!io_any_admission_waiters()", "1", 1), POLICY, IOBUDGET),
    (BIO.replace("!io_has_competing_owner(state)", "1", 1), POLICY, IOBUDGET),
    (BIO.replace("lane.debt == 0 && lane.pending_debt == 0", "lane.debt == 0", 1), POLICY, IOBUDGET),
    (BIO.replace("state->shared_grants++;", "state->reserved_grants++;", 1), POLICY, IOBUDGET),
    (BIO.replace("io_active_request_acquire(state);", "state->active_requests++;", 1), POLICY, IOBUDGET),
    (BIO.replace("io_active_request_release(state);", "state->active_requests--;", 1), POLICY, IOBUDGET),
    (BIO, POLICY, IOBUDGET.replace(
        "borrowed = shared_decisions > 0;", "borrowed = 1;", 1)),
    (BIO, POLICY, IOBUDGET.replace(
        "after.refills > before.refills &&\n\t\t\t throttle_decisions == 0",
        "1", 1)),
    (BIO, POLICY, IOBUDGET.replace(
        "check(borrowed || refill_covered,", "check(1,", 1)),
)

for mutated_bio, mutated_policy, mutated_iobudget in MUTATIONS:
    try:
        validate(mutated_bio, mutated_policy, mutated_iobudget)
    except ContractError:
        continue
    raise SystemExit("work-conserving I/O mutation survived")

print(f"[io-work-conserving] {len(MUTATIONS)} mutations passed")
