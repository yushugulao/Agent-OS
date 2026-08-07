#!/usr/bin/env python3
"""BIO 局部工作保守型 I/O 调节器的变异防护。"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIO = (ROOT / "os/bio.c").read_text(encoding="utf-8")
RESOURCE = (ROOT / "os/resource_controller.c").read_text(encoding="utf-8")
POLICY = (ROOT / "io_policy.h").read_text(encoding="utf-8")
IOBUDGET = (ROOT / "user/src/iobudget_ucore.c").read_text(encoding="utf-8")


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


def validate(bio: str, resource: str, policy: str, iobudget: str) -> None:
    if define(policy, "IO_POLICY_VERSION") != 6:
        raise ContractError("work-conserving policy version is not published")
    if define(policy, "IO_POLICY_SHARED_BURST") != define(
            policy, "IO_POLICY_DEVICE_BURST"):
        raise ContractError("shared burst does not mirror the device envelope")
    if define(policy, "IO_POLICY_SHARED_REFILL") != define(
            policy, "IO_POLICY_DEVICE_REFILL"):
        raise ContractError("shared refill does not mirror the device envelope")
    if define(bio, "IO_RATE_LOCAL_BATCH") <= 1:
        raise ContractError("physical transfers are not locally batched")
    if "struct io_rate_state" in resource:
        raise ContractError("BIO-local buckets leaked into generic accounts")

    for token in (
        "#define IO_RESERVATION_OWNER 1U",
        "#define IO_RESERVATION_SHARED 2U",
        "#define IO_RESERVATION_OWNER_DEBT 3U",
        "#define IO_DEVICE_RESERVATION_TOKEN 1U",
        "#define IO_DEVICE_RESERVATION_DEBT 2U",
        "#define BIO_REQUEST_TRANSFERRED (1U << 3)",
        "uint64 ready_bitmap;",
        "uint64 debt_bitmap;",
        "uint64 retiring_bitmap;",
    ):
        if token not in bio:
            raise ContractError(f"compact reservation state is missing {token}")
    reserve_pair = compact(function_body(bio, "io_rate_reserve_pair"))
    if "else if (!shared" not in reserve_pair:
        raise ContractError("shared admission can create device debt")

    borrow = compact(function_body(bio, "io_can_use_shared_capacity"))
    for token in (
        "io_class != IO_POLICY_CLASS_BACKGROUND",
        "!state->retiring && !state->quiesced",
    ):
        if token not in borrow:
            raise ContractError(f"idle-capacity gate missing {token}")

    charge = compact(function_body(bio, "io_rate_charge_transfers"))
    if not ordered(
        charge,
        "if (lane->debt == 0) reserved = MIN(amount, lane->tokens);",
        "if (reserved != 0)",
        "device->debt == 0 && device->tokens >= reserved",
        "panic(\"I/O reserved batch charge\");",
        "state->reserved_grants += reserved;",
        "amount -= reserved;",
        "if (amount != 0 && lane->debt == 0",
        "lane->pending_debt == 0",
        "io_can_use_shared_capacity(state, io_class)",
        "shared = MIN(amount, capacity->tokens);",
        "else if (shared > device->tokens)",
        "io_rate_charge_pair( state, io_class, 1, 0, 0, shared)",
        "state->shared_grants += shared;",
        "amount -= shared;",
        "for (uint64 i = 0; i < amount; i++)",
        "io_rate_charge_pair( state, io_class, 0, 1, 1, 1)",
        "state->throttles += amount;",
    ):
        raise ContractError("batch charge is not reserved, shared, then debt")

    direct = compact(function_body(bio, "io_reserve_direct"))
    if not ordered(
        direct,
        "io_rate_reserve_pair( state, io_class, 0, 0, source, device_source)",
        "state->reserved_grants++;",
        "lane->debt == 0",
        "lane->pending_debt == 0",
        "io_can_use_shared_capacity(state, io_class)",
        "io_rate_reserve_pair( state, io_class, 1, 0, source, device_source)",
    ):
        raise ContractError("direct admission does not prefer reserved capacity")

    waiter = compact(function_body(bio, "io_grant_waiter"))
    if not ordered(
        waiter,
        "io_rate_reserve_pair(",
        "grantee = wait_queue_wake_one_thread(&bucket->admission_queue);",
        "if (grantee == 0)",
        "io_rate_reservation_cancel_locked(",
        "bucket->grantee = grantee;",
        "bucket->grant_source = source;",
        "bucket->grant_device_source = device_source;",
    ):
        raise ContractError("grant reservation is not bound to the actual waiter")
    success = waiter[waiter.find("bucket->grantee = grantee;"):]
    if not ordered(success, "bucket->grantee = grantee;",
                   "io_ready_clear(state, io_class);"):
        raise ContractError("successful grant leaves its ready bit published")
    if "state->shared_grants" in waiter:
        raise ContractError("unused shared reservations count as completed work")

    scheduler = compact(function_body(
        bio[bio.rfind("static void io_schedule_grants(void)") :],
        "io_schedule_grants"))
    if scheduler.count("while (pending != 0)") != 2 or "for (" in scheduler:
        raise ContractError("grant scheduler scans idle owner/class slots")
    if scheduler.count("pending &= pending - 1;") != 1 or \
            scheduler.count(
                "pending &= ~((uint64)1 << index);") != 1:
        raise ContractError("grant scheduler does not advance both bitmap passes")
    if not ordered(
        scheduler,
        "pending = io_policy.ready_bitmap;",
        "uint index = io_ready_first(pending);",
        "pending &= pending - 1;",
        "io_grant_waiter( &io_policy.owners[owner_slot], io_class, 0)",
        "if (io_rate_shared_refresh()->tokens != 0)",
        "io_ready_next( pending, io_policy.shared_cursor)",
        "pending &= ~((uint64)1 << index);",
        "if (io_class == IO_POLICY_CLASS_BACKGROUND) continue;",
        "io_grant_waiter(state, io_class, 1)",
        "io_policy.shared_cursor = (index + 1) % IO_ADMISSION_READY_SLOTS;",
    ):
        raise ContractError("reserved/shared ready-bit passes are not fair")

    tick = compact(function_body(bio, "bio_policy_tick"))
    if "for (" in tick or "IO_OWNER_SLOTS" in tick:
        raise ContractError("timer refill scans all owner slots")
    if not ordered(
        tick,
        "pending = io_policy.debt_bitmap;",
        "while (pending != 0)",
        "io_rate_owner_refresh(state, io_class);",
        "io_rate_device_refresh()",
        "io_schedule_grants();",
        "if (io_policy.retiring_bitmap != 0)",
        "io_owner_reap_retired();",
    ):
        raise ContractError("timer does not target debt and ready bitmaps")

    reaper = compact(function_body(bio, "io_owner_reap_retired"))
    if not ordered(
        reaper,
        "pending = io_policy.retiring_bitmap;",
        "while (pending != 0)",
        "!io_owner_has_waiters(state)",
        "io_owner_rate_idle(state)",
        "io_retiring_clear(state);",
    ):
        raise ContractError("retiring owners are not kept on a bounded worklist")

    owner_refill = compact(function_body(bio, "io_rate_owner_refresh"))
    resolved = compact(function_body(bio, "io_owner_debt_resolved"))
    if not ordered(
        owner_refill,
        "debt_before = rate->debt;",
        "io_rate_state_refill(",
        "debt_before != 0 && rate->debt == 0",
        "io_owner_debt_resolved(state, io_class);",
    ) or not ordered(
        resolved,
        "io_debt_clear(state, io_class);",
        "bucket->debt_waiters != 0",
        "wait_queue_wake_all(&bucket->debt_queue);",
    ):
        raise ContractError("lazy debt resolution can lose its waiter wakeup")

    admission = compact(function_body(bio, "io_wait_until_admitted"))
    if not ordered(
        admission,
        "bucket->admission_waiters++;",
        "io_ready_set(state, io_class);",
        "wait_queue_sleep_irq",
        "bucket->grantee = 0;",
        "io_admission_waiter_release_locked(state, io_class);",
        "io_rate_reservation_refund(",
        "io_owner_reap_retired();",
    ):
        raise ContractError("admission sleep/refund loses published demand")
    if admission.count("io_admission_waiter_release_locked(") != 2 or \
            admission.count("io_owner_reap_retired();") != 2:
        raise ContractError("retirement does not reap both waiter release paths")

    account = compact(function_body(bio, "bio_account_transfer_batch"))
    request_path = account[account.find("else if (request_flags != 0)"):]
    if not ordered(
        request_path,
        "if ((*request_flags & BIO_REQUEST_TRANSFERRED) == 0)",
        "io_rate_reservation_shared(*reservation)",
        "io_rate_reservation_commit(",
        "state->shared_grants++;",
        "*request_flags |= BIO_REQUEST_TRANSFERRED;",
        "count--;",
        "if (*transfers >= IO_RATE_LOCAL_BATCH)",
        "io_rate_charge_transfers(",
        "*transfers = 0;",
    ):
        raise ContractError("request receipt is not committed exactly once")
    if bio.count("state->shared_grants++;") != 2:
        raise ContractError("shared reservation work is counted outside commit")
    if bio.count("state->shared_grants += shared;") != 1:
        raise ContractError("shared batch accounting is not unique")

    end = compact(function_body(bio, "bio_request_end_current_mode"))
    abort = compact(function_body(bio, "bio_request_abort_thread"))
    if not ordered(
        end,
        "if ((flags & BIO_REQUEST_TRANSFERRED) == 0)",
        "io_rate_reservation_refund(",
        "else",
        "io_rate_charge_transfers(state, io_class, transfers);",
    ):
        raise ContractError("request end does not refund-or-flush")
    abort_settlement = abort[abort.find(
        "state = io_state_find(thread->io_request_owner, 0);"):]
    if not ordered(
        abort_settlement,
        "BIO_REQUEST_TRANSFERRED) == 0",
        "io_rate_reservation_refund(",
        "BIO_REQUEST_TRANSFERRED) != 0",
        "io_rate_charge_transfers(",
    ):
        raise ContractError("request abort does not refund-or-flush")

    if not re.search(
            r"#define\s+COLD_RATE_BLOCKS\s*\\\s*"
            r"\(IO_POLICY_WORKFLOW_NORMAL_BURST\s*\+\s*1\)",
            iobudget):
        raise ContractError("cold-read extent does not cross the owner burst")
    for token in (
        "#define COLD_PRESSURE_BYTES "
        "(COLD_PRESSURE_BLOCKS * IO_BLOCK_SIZE)",
        "static char cold_pressure_data[COLD_PRESSURE_BYTES];",
    ):
        if token not in iobudget:
            raise ContractError("cold-read buffer is detached from its extent")
    idle = compact(function_body(iobudget, "wait_for_idle_loan_window"))
    for token in (
        "snapshot->tokens == snapshot->class_burst",
        "snapshot->shared_tokens == IO_POLICY_SHARED_BURST",
        "snapshot->device_tokens == snapshot->device_burst",
        "snapshot->leased == 0 && snapshot->shared_leased == 0",
        "snapshot->device_leased == 0 && snapshot->debt == 0",
        "snapshot->device_debt == 0 && snapshot->waiters == 0",
    ):
        if token not in idle:
            raise ContractError("dynamic probe lacks a clean idle baseline")
    pressure = compact(function_body(iobudget, "run_lineage_attacker"))
    if not ordered(
        pressure,
        "setup_lineage_pressure();",
        "wait_for_idle_loan_window(&before);",
        'cold_fd = open("iocold", O_RDONLY);',
        "read_transfers = after.physical_reads - before.physical_reads;",
        "shared_decisions = after.shared_grants - before.shared_grants;",
        "read_transfers > (unsigned long long)before.class_burst",
        "borrowed = shared_decisions > 0;",
        "check(borrowed,",
        "check(throttle_decisions == 0,",
    ):
        raise ContractError("dynamic evidence is not a cold shared-capacity read")


validate(BIO, RESOURCE, POLICY, IOBUDGET)

MUTATIONS = (
    (BIO, RESOURCE, mutate_once(POLICY, "#define IO_POLICY_VERSION 6U",
                                "#define IO_POLICY_VERSION 5U"), IOBUDGET),
    (mutate_once(BIO, "#define IO_RATE_LOCAL_BATCH 32U",
                 "#define IO_RATE_LOCAL_BATCH 1U"), RESOURCE, POLICY, IOBUDGET),
    (mutate_once(BIO, "else if (!shared &&",
                 "else if (shared &&"), RESOURCE, POLICY, IOBUDGET),
    (mutate_once(BIO, "state, io_class, 1, 0, 0, shared",
                 "state, io_class, 0, 0, 0, shared"),
     RESOURCE, POLICY, IOBUDGET),
    (mutate_once(BIO, "for (uint64 i = 0; i < amount; i++)",
                 "if (amount != 0)"), RESOURCE, POLICY, IOBUDGET),
    (mutate_once(BIO, "pending = io_policy.debt_bitmap;",
                 "pending = ~0ULL;"), RESOURCE, POLICY, IOBUDGET),
    (mutate_once(BIO, "pending = io_policy.retiring_bitmap;",
                 "pending = 0;"), RESOURCE, POLICY, IOBUDGET),
    (mutate_function_once(BIO, "io_schedule_grants",
                          "pending &= pending - 1;",
                          "pending = pending;"),
     RESOURCE, POLICY, IOBUDGET),
    (mutate_function_once(BIO, "io_schedule_grants",
                          "pending &= ~((uint64)1 << index);",
                          "pending = pending;"),
     RESOURCE, POLICY, IOBUDGET),
    (mutate_once(BIO, "io_owner_debt_resolved(state, io_class);",
                 "(void)state;"), RESOURCE, POLICY, IOBUDGET),
    (mutate_once(BIO, "grantee = wait_queue_wake_one_thread("
                 "&bucket->admission_queue);",
                 "grantee = bucket->admission_queue.head;"),
     RESOURCE, POLICY, IOBUDGET),
    (mutate_once(BIO, "*request_flags |= BIO_REQUEST_TRANSFERRED;",
                 "*request_flags |= BIO_REQUEST_ACTIVE;"),
     RESOURCE, POLICY, IOBUDGET),
    (mutate_once(BIO, "\t\t\t\tio_owner_reap_retired();\n"
                 "\t\t\t\tintr_restore(enabled);",
                 "\t\t\t\tintr_restore(enabled);"),
     RESOURCE, POLICY, IOBUDGET),
    (BIO, RESOURCE + "\nstruct io_rate_state misplaced;", POLICY, IOBUDGET),
    (BIO, RESOURCE, POLICY, mutate_once(
        IOBUDGET, "borrowed = shared_decisions > 0;", "borrowed = 1;")),
)

for index, mutation in enumerate(MUTATIONS):
    try:
        validate(*mutation)
    except ContractError:
        continue
    raise AssertionError(f"mutation {index} unexpectedly passed")

print("BIO-local work-conserving I/O checks passed")
