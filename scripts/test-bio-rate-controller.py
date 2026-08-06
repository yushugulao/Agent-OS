#!/usr/bin/env python3
"""Mutation guards for the compact BIO-local hierarchical rate controller."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIO = (ROOT / "os/bio.c").read_text(encoding="utf-8")
RESOURCE_C = (ROOT / "os/resource_controller.c").read_text(encoding="utf-8")
RESOURCE_H = (ROOT / "os/resource_controller.h").read_text(encoding="utf-8")


class ContractError(RuntimeError):
    pass


def function_body(source: str, name: str) -> str:
    opening = -1
    for match in re.finditer(rf"\b{re.escape(name)}\s*\(", source):
        candidate = source.find("{", match.end())
        terminator = source.find(";", match.end(), candidate)
        if candidate >= 0 and terminator < 0:
            opening = candidate
            break
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
    raise ContractError(f"unterminated function {name}")


def compact(value: str) -> str:
    return " ".join(value.split())


def ordered(body: str, *tokens: str) -> bool:
    positions = [body.find(token) for token in tokens]
    return all(position >= 0 for position in positions) and positions == sorted(positions)


def mutate_once(source: str, old: str, new: str) -> str:
    if old not in source:
        raise RuntimeError(f"mutation anchor disappeared: {old!r}")
    return source.replace(old, new, 1)


def mutate_function_once(source: str, name: str, old: str, new: str) -> str:
    body = function_body(source, name)
    if body.count(old) != 1:
        raise RuntimeError(
            f"function mutation anchor is not unique in {name}: {old!r}")
    mutated = body.replace(old, new, 1)
    if source.count(body) != 1:
        raise RuntimeError(f"function body is not unique: {name}")
    return source.replace(body, mutated, 1)


def exercise_liveness_model() -> None:
    rate = {
        "debt": 1,
        "tracked": True,
        "debt_waiters": 1,
        "wakeups": 0,
    }

    debt_before = rate["debt"]
    rate["debt"] = 0
    if debt_before and not rate["debt"]:
        tracked = rate["tracked"]
        rate["tracked"] = False
        if tracked and rate["debt_waiters"]:
            rate["wakeups"] += rate["debt_waiters"]
    if rate != {
        "debt": 0,
        "tracked": False,
        "debt_waiters": 1,
        "wakeups": 1,
    }:
        raise AssertionError("lazy debt resolution did not wake its sleeper")

    owner = {
        "retiring": True,
        "active_requests": 0,
        "admission_waiters": 1,
        "grantee": True,
        "rate_idle": True,
        "cache_usage": 0,
        "reaped": False,
    }
    owner["grantee"] = False
    owner["admission_waiters"] -= 1
    owner["reaped"] = (
        owner["retiring"] and not owner["active_requests"] and
        not owner["admission_waiters"] and not owner["grantee"] and
        owner["rate_idle"] and not owner["cache_usage"]
    )
    if not owner["reaped"]:
        raise AssertionError("last retiring grantee did not release its owner")

    retired = {
        "retiring_tracked": True,
        "debt": 1,
        "active_requests": 0,
        "waiters": 0,
        "reaped": False,
    }
    retired["debt"] = 0
    if retired["retiring_tracked"] and not retired["debt"] and \
            not retired["active_requests"] and not retired["waiters"]:
        retired["retiring_tracked"] = False
        retired["reaped"] = True
    if not retired["reaped"] or retired["retiring_tracked"]:
        raise AssertionError("lazy final debt lost the retirement reap signal")


def validate(bio: str, resource_c: str, resource_h: str) -> None:
    if "resource_account_empty" not in resource_c:
        raise ContractError("durable resource lifecycle disappeared")
    if "struct io_rate_state" in resource_c or \
            "struct io_rate_state" in resource_h:
        raise ContractError("BIO-local bucket state leaked into generic accounts")

    for token in (
        "struct io_rate_state {",
        "uint64 last_refill_tick;",
        "sizeof(struct io_rate_state) == 40U",
        "struct io_rate_state rate;",
        "struct io_shared_bucket shared;",
        "uint64 debt_bitmap;",
        "uint64 retiring_bitmap;",
    ):
        if token not in bio:
            raise ContractError(f"BIO-local rate layout missing {token}")
    source = compact(bio)
    if "_Static_assert(IO_ADMISSION_READY_SLOTS <= 64," not in source:
        raise ContractError("owner bitmap lacks a compile-time slot bound")
    owner_bit = compact(function_body(bio, "io_owner_bit"))
    if owner_bit != \
            "return 1ULL << (uint)(state - io_policy.owners);":
        raise ContractError("owner slot does not map exactly to its bitmap bit")

    refill = compact(function_body(bio, "io_rate_state_refill"))
    if not ordered(
        refill,
        "now = io_rate_now();",
        "elapsed = now - rate->last_refill_tick;",
        "rate->last_refill_tick = now;",
        "budget = elapsed > RESOURCE_LIMIT_UNBOUNDED / refill ?",
        "paid = MIN(rate->debt, budget);",
        "rate->debt -= paid;",
        "room = burst - rate->tokens - rate->leased;",
        "added = MIN(room, budget);",
        "rate->tokens += added;",
    ):
        raise ContractError("lazy refill no longer pays debt before bounded tokens")

    owner_refill = compact(function_body(bio, "io_rate_owner_refresh"))
    if not ordered(
        owner_refill,
        "debt_before = rate->debt;",
        "applied = io_rate_state_refill(",
        "if (debt_before != 0 && rate->debt == 0)",
        "io_owner_debt_resolved(state, io_class);",
    ):
        raise ContractError("lazy owner refill can strand a resolved debt waiter")
    resolved = compact(function_body(bio, "io_owner_debt_resolved"))
    if not ordered(
        resolved,
        "tracked = (io_policy.debt_bitmap & bit) != 0;",
        "if (bucket->rate.debt != 0)",
        "io_debt_clear(state, io_class);",
        "if (tracked && bucket->debt_waiters != 0)",
        "wait_queue_wake_all(&bucket->debt_queue);",
    ):
        raise ContractError("debt resolution does not clear tracking before wakeup")

    reserve = compact(function_body(bio, "io_rate_reserve_pair"))
    if not ordered(
        reserve,
        "first = io_rate_source_refresh(state, io_class, selected_source);",
        "device = io_rate_device_refresh();",
        "if (first == 0)",
        "if (first->debt != 0 || first->tokens == 0)",
        "if (device->debt == 0 && device->tokens != 0)",
        "else if (!shared",
        "if (selected_source == IO_RESERVATION_OWNER_DEBT)",
        "first->pending_debt++;",
        "first->tokens--;",
        "first->leased++;",
        "device->tokens--;",
        "device->leased++;",
        "*source = selected_source;",
        "*device_source = selected_device;",
    ):
        raise ContractError("pair reservation is not atomic owner/shared then device")
    if "for (" in reserve or "while (" in reserve:
        raise ContractError("pair reservation reintroduced endpoint sorting or scanning")

    commit = compact(function_body(bio, "io_rate_reservation_commit"))
    cancel = compact(function_body(bio, "io_rate_reservation_cancel_locked"))
    for body, label in ((commit, "commit"), (cancel, "cancel")):
        if not ordered(
            body,
            "first = io_rate_source_refresh(state, io_class, first_source);",
            "device = io_rate_device_refresh();",
            "*source = IO_RESERVATION_NONE;",
            "*device_source = IO_RESERVATION_NONE;",
        ):
            raise ContractError(f"reservation {label} does not consume its receipt once")
    if not ordered(
        commit,
        "first->pending_debt--;",
        "if (first->debt == 0 && first->tokens != 0)",
        "first->tokens--;",
        "first->debt++;",
        "device->pending_debt--;",
        "if (device->debt == 0 && device->tokens != 0)",
        "device->tokens--;",
        "device->debt++;",
    ):
        raise ContractError("commit changed token-before-debt semantics")
    if not ordered(
        cancel,
        "first->pending_debt--;",
        "first->leased--;",
        "first->tokens++;",
        "device->leased--;",
        "device->tokens++;",
        "device->pending_debt--;",
    ):
        raise ContractError("cancel no longer refunds both levels atomically")

    tick = compact(function_body(bio, "bio_policy_tick"))
    if "for (" in tick or "IO_OWNER_SLOTS" in tick:
        raise ContractError("timer tick scans every owner or lane")
    for token in (
        "pending = io_policy.debt_bitmap;",
        "while (pending != 0)",
        "io_rate_owner_refresh(state, io_class);",
        "io_rate_device_refresh()",
        "io_schedule_grants();",
        "if (io_policy.retiring_bitmap != 0)",
        "io_owner_reap_retired();",
    ):
        if token not in tick:
            raise ContractError(f"targeted lazy tick missing {token}")
    if tick.count("pending &= pending - 1;") != 1 or not ordered(
        tick,
        "pending = io_policy.debt_bitmap;",
        "while (pending != 0)",
        "pending &= pending - 1;",
        "io_rate_owner_refresh(state, io_class);",
    ):
        raise ContractError("debt tick does not clear each visited bitmap bit")

    idle = compact(function_body(bio, "io_owner_rate_idle"))
    if "rate->leased != 0" not in idle or "rate->pending_debt != 0" not in idle:
        raise ContractError("owner reuse can race a live reservation")
    reap = compact(function_body(bio, "io_owner_reap_retired"))
    if not ordered(
        reap,
        "pending = io_policy.retiring_bitmap;",
        "while (pending != 0)",
        "pending &= pending - 1;",
        "state->active_requests == 0",
        "!io_owner_has_waiters(state)",
        "io_owner_rate_idle(state)",
        "resource_account_member_release(",
        "io_retiring_clear(state);",
    ):
        raise ContractError("active requests no longer pin the BIO owner")
    if reap.count("pending &= pending - 1;") != 1:
        raise ContractError("retirement reaper does not advance its bitmap")
    retire = compact(function_body(bio, "bio_scope_retire"))
    if not ordered(
        retire,
        "state->retiring = 1;",
        "io_retiring_set(state);",
        "wait_queue_wake_all(",
        "io_owner_reap_retired();",
    ):
        raise ContractError("scope retirement is not kept on the reap worklist")

    waiter_release = compact(function_body(
        bio, "io_admission_waiter_release_locked"))
    if not ordered(
        waiter_release,
        "if (bucket->admission_waiters == 0)",
        "bucket->admission_waiters--;",
        "io_ready_refresh(state, io_class);",
    ):
        raise ContractError("admission waiter release is no longer centralized")
    admission = compact(function_body(bio, "io_wait_until_admitted"))
    if admission.count("io_admission_waiter_release_locked(") != 2 or \
            admission.count("io_owner_reap_retired();") != 2:
        raise ContractError("retiring grantee/waiter release can leak its owner")
    begin = compact(function_body(bio, "bio_request_begin_current_mode"))
    retiring = begin[begin.find("if (state->retiring || state->quiesced)"):]
    if not ordered(
        retiring,
        "io_rate_reservation_refund(",
        "io_owner_reap_retired();",
        "return -1;",
    ):
        raise ContractError("direct admission retire race does not reap after refund")

    account = compact(function_body(bio, "bio_account_transfers"))
    if not ordered(
        account,
        "if ((*request_flags & BIO_REQUEST_TRANSFERRED) == 0)",
        "io_rate_reservation_shared(*reservation)",
        "io_rate_reservation_commit(",
        "*request_flags |= BIO_REQUEST_TRANSFERRED;",
    ):
        raise ContractError("physical transfer does not commit one stored receipt")


exercise_liveness_model()
validate(BIO, RESOURCE_C, RESOURCE_H)

MUTATIONS = (
    (mutate_once(BIO, "rate->last_refill_tick = now;",
                 "rate->last_refill_tick = 0;"), RESOURCE_C, RESOURCE_H),
    (mutate_once(BIO, "paid = MIN(rate->debt, budget);",
                 "paid = 0;"), RESOURCE_C, RESOURCE_H),
    (mutate_once(BIO, "io_owner_debt_resolved(state, io_class);",
                 "(void)state;"), RESOURCE_C, RESOURCE_H),
    (mutate_once(BIO, "if (tracked && bucket->debt_waiters != 0)",
                 "if (0 && bucket->debt_waiters != 0)"),
     RESOURCE_C, RESOURCE_H),
    (mutate_once(BIO, "room = burst - rate->tokens - rate->leased;",
                 "room = burst - rate->tokens;"), RESOURCE_C, RESOURCE_H),
    (mutate_once(BIO, "else if (!shared &&",
                 "else if (shared &&"), RESOURCE_C, RESOURCE_H),
    (mutate_once(BIO,
                 "panic(\"I/O device debt overflow\");\n\t}\n"
                 "\t*source = IO_RESERVATION_NONE;",
                 "panic(\"I/O device debt overflow\");\n\t}\n"
                 "\t*source = first_source;"), RESOURCE_C, RESOURCE_H),
    (mutate_once(BIO, "pending = io_policy.debt_bitmap;",
                 "pending = ~0ULL;"), RESOURCE_C, RESOURCE_H),
    (mutate_function_once(BIO, "bio_policy_tick",
                          "pending &= pending - 1;",
                          "pending = pending;"), RESOURCE_C, RESOURCE_H),
    (mutate_once(BIO, "pending = io_policy.retiring_bitmap;",
                 "pending = 0;"), RESOURCE_C, RESOURCE_H),
    (mutate_function_once(BIO, "io_owner_reap_retired",
                          "pending &= pending - 1;",
                          "pending = pending;"), RESOURCE_C, RESOURCE_H),
    (mutate_function_once(
        BIO, "io_owner_bit",
        "return 1ULL << (uint)(state - io_policy.owners);",
        "return 1ULL << ((uint)(state - io_policy.owners) + 1);"),
     RESOURCE_C, RESOURCE_H),
    (mutate_once(BIO, "_Static_assert(IO_ADMISSION_READY_SLOTS <= 64,",
                 "_Static_assert(IO_ADMISSION_READY_SLOTS <= 65,"),
     RESOURCE_C, RESOURCE_H),
    (mutate_once(BIO, "state->active_requests == 0 &&",
                 "state->active_requests != 0 &&"), RESOURCE_C, RESOURCE_H),
    (mutate_once(BIO, "\t\tio_retiring_set(state);",
                 "\t\tio_retiring_clear(state);"), RESOURCE_C, RESOURCE_H),
    (mutate_once(BIO, "\t\t\t\tio_owner_reap_retired();\n"
                 "\t\t\t\tintr_restore(enabled);",
                 "\t\t\t\tintr_restore(enabled);"),
     RESOURCE_C, RESOURCE_H),
    (BIO, RESOURCE_C + "\nstruct io_rate_state misplaced;", RESOURCE_H),
)

for index, mutation in enumerate(MUTATIONS):
    try:
        validate(*mutation)
    except ContractError:
        continue
    raise AssertionError(f"mutation {index} unexpectedly passed")

print("BIO-local lazy rate controller checks and liveness scenarios passed")
