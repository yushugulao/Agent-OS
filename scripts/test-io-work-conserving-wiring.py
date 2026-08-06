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


def mutate_once(source: str, old: str, new: str) -> str:
    if old not in source:
        raise RuntimeError(f"mutation anchor disappeared: {old!r}")
    return source.replace(old, new, 1)


def validate(bio: str, policy: str, iobudget: str) -> None:
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
    for token in (
        "#define IO_RESERVATION_SHARED (1U << 31)",
        "#define IO_RESERVATION_SLOT_MASK (~IO_RESERVATION_SHARED)",
        "#define BIO_REQUEST_TRANSFERRED (1U << 3)",
        "BIO_REQUEST_TRANSFERRED)",
        "RESOURCE_RATE_LEASE_CAP < IO_RESERVATION_SHARED",
    ):
        if token not in bio:
            raise ContractError(f"batched lease state is missing {token}")

    bundle = compact(function_body(bio, "io_rate_bundle_amount"))
    if not ordered(
        bundle,
        "if (shared)",
        "endpoints[0].scope = RESOURCE_RATE_GLOBAL;",
        "endpoints[0].index = IO_RATE_GLOBAL_SHARED;",
        "else",
        "endpoints[0].scope = RESOURCE_RATE_ACCOUNT;",
        "endpoints[0].account = state->principal;",
        "endpoints[0].index = io_class;",
        "endpoints[0].amount = amount;",
        "endpoints[1].scope = RESOURCE_RATE_GLOBAL;",
        "endpoints[1].index = IO_RATE_GLOBAL_DEVICE;",
        "endpoints[1].amount = amount;",
        "if (!shared)",
        "endpoints[1].flags = RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;",
    ):
        raise ContractError("rate bundle does not preserve shared/reserved semantics")
    if (bundle.count("RESOURCE_RATE_ENDPOINT_ALLOW_DEBT") != 1 or
            "endpoints[0].flags" in bundle or
            "io_device_protected" in bundle):
        raise ContractError("shared traffic can acquire debt")
    scalar_bundle = compact(function_body(bio, "io_rate_bundle"))
    if scalar_bundle != (
            "return io_rate_bundle_amount(state, io_class, shared, 1, "
            "endpoints);"):
        raise ContractError("single-credit bundle bypasses the amount primitive")

    lease = compact(function_body(bio, "io_rate_lease"))
    store = compact(function_body(bio, "io_rate_lease_store"))
    shared_lease = compact(function_body(bio, "io_rate_lease_shared"))
    if "slot & IO_RESERVATION_SLOT_MASK" not in lease:
        raise ContractError("tagged lease slot reaches the resource controller")
    if "lease.slot | (shared ? IO_RESERVATION_SHARED : 0)" not in store:
        raise ContractError("shared admission does not retain its lease source")
    if "(slot & IO_RESERVATION_SHARED) != 0" not in shared_lease:
        raise ContractError("shared lease source cannot be recovered at commit")

    borrow = compact(function_body(bio, "io_can_use_shared_capacity"))
    for token in (
        "io_class != IO_POLICY_CLASS_BACKGROUND",
        "!state->retiring && !state->quiesced",
    ):
        if token not in borrow:
            raise ContractError(f"idle-capacity gate missing {token}")
    if ("active_owner_states" in bio or "io_has_competing_owner" in bio or
            "io_any_admission_waiters" in bio):
        raise ContractError("fast-path lending still performs global demand scans")

    charge = compact(function_body(bio, "io_rate_charge_transfers"))
    if not ordered(
        charge,
        "if (lane.debt == 0) reserved = MIN(amount, lane.tokens);",
        "if (reserved != 0)",
        "device.debt == 0 && device.tokens >= reserved",
        "io_rate_bundle_amount( state, io_class, 0, reserved, endpoints);",
        "panic(\"I/O reserved batch charge\");",
        "state->reserved_grants += reserved;",
        "amount -= reserved;",
        "if (amount != 0 && lane.debt == 0 && lane.pending_debt == 0",
        "io_can_use_shared_capacity(state, io_class)",
        "shared = MIN(amount, capacity.tokens);",
        "io_rate_bundle_amount( state, io_class, 1, shared, endpoints);",
        "panic(\"I/O shared batch charge\");",
        "state->shared_grants += shared;",
        "amount -= shared;",
        "for (uint64 i = 0; i < amount; i++)",
        "io_rate_bundle(state, io_class, 0, endpoints);",
        "endpoints[0].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;",
        "endpoints[1].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;",
        "panic(\"I/O rate charge\");",
        "state->throttles += amount;",
    ):
        raise ContractError("batch charge is not split into reserved/shared/debt")
    for token in (
        "capacity.debt != 0) shared = 0;",
        "else if (shared > capacity.tokens) shared = capacity.tokens;",
    ):
        if token not in charge:
            raise ContractError("shared batch ignores the device envelope")

    direct = compact(function_body(bio, "io_reserve_direct"))
    waiter = compact(function_body(bio, "io_grant_waiter"))
    scheduler_definition = bio.rfind("static void io_schedule_grants(void)")
    if scheduler_definition < 0:
        raise ContractError("missing admission grant scheduler")
    scheduler = compact(function_body(
        bio[scheduler_definition:], "io_schedule_grants"))
    if ("lane.debt == 0 && lane.pending_debt == 0" not in direct or
            "io_can_use_shared_capacity(state, io_class)" not in direct):
        raise ContractError("admission can borrow while indebted or quiesced")
    if "state->shared_grants" in direct or "state->shared_grants" in waiter:
        raise ContractError("an unused shared reservation is counted as work")
    if not ordered(
        waiter,
        "resource_rate_reserve_many(endpoints, 2, &lease)",
        "grantee = wait_queue_wake_one_thread(&bucket->admission_queue);",
        "if (grantee == 0)",
        "resource_rate_lease_cancel(lease);",
        "bucket->grantee = grantee;",
        "io_rate_lease_store(",
    ):
        raise ContractError("admission lease is not bound to the actual waiter")
    if not ordered(
        scheduler,
        "int enabled = intr_save();",
        "io_grant_waiter(state, c, 0)",
        "io_grant_waiter(state, io_class, 1)",
        "intr_restore(enabled);",
    ):
        raise ContractError("admission grant scheduling is not IRQ-atomic")

    account = compact(function_body(bio, "bio_account_transfers"))
    request_path = account[account.find("else if (request_flags != 0)"):]
    if not ordered(
        request_path,
        "if ((*request_flags & BIO_REQUEST_TRANSFERRED) == 0)",
        "io_rate_lease_commit(",
        "if (io_rate_lease_shared(reservation))",
        "state->shared_grants++;",
        "*request_flags |= BIO_REQUEST_TRANSFERRED;",
        "count--;",
        "if (count > (uint)-1 - *transfers)",
        "*transfers += count;",
        "if (*transfers >= IO_RATE_LOCAL_BATCH)",
        "io_rate_charge_transfers(",
        "*transfers = 0;",
    ):
        raise ContractError("request lease is not committed and batched on transfer")
    if account.count("state->shared_grants++;") != 2:
        raise ContractError("shared lease commits are not counted exactly once")
    if (bio.count("state->shared_grants++;") != 2 or
            bio.count("state->shared_grants += shared;") != 1):
        raise ContractError("shared work is counted outside transfer commit")
    valid_flags = compact(function_body(bio, "bio_thread_request_flags_valid"))
    transferred_validity = valid_flags[valid_flags.find(
        "if ((flags & BIO_REQUEST_TRANSFERRED) != 0"):]
    if not ordered(
        transferred_validity,
        "if ((flags & BIO_REQUEST_TRANSFERRED) != 0",
        "(flags & BIO_REQUEST_ACTIVE) == 0",
        "return 0;",
    ):
        raise ContractError("transferred requests can exist without an active lease")
    end = compact(function_body(bio, "bio_request_end_current_mode"))
    abort = compact(function_body(bio, "bio_request_abort_thread"))
    if not ordered(
        end,
        "if ((flags & BIO_REQUEST_TRANSFERRED) == 0)",
        "io_rate_lease_refund(source, device_source);",
        "else",
        "io_rate_charge_transfers(state, io_class, transfers);",
    ):
        raise ContractError("request end does not refund-or-flush by transfer state")
    abort_settlement = abort[abort.find(
        "state = io_state_find(thread->io_request_owner, 0);"):]
    if not ordered(
        abort_settlement,
        "BIO_REQUEST_TRANSFERRED) == 0",
        "io_rate_lease_refund(",
        "BIO_REQUEST_TRANSFERRED) != 0",
        "io_rate_charge_transfers(",
    ):
        raise ContractError("request abort does not refund-or-flush by transfer state")

    batch = compact(function_body(bio, "bio_account_transfer_batch"))
    if (batch != "bio_account_transfers(owner, io_class, transfer, results, count);" or
            "for (" in batch or "bio_account_transfer(" in batch):
        raise ContractError("driver batch accounting re-enters the scalar path")

    if not re.search(
            r"#define\s+COLD_RATE_BLOCKS\s*\\\s*"
            r"\(IO_POLICY_WORKFLOW_NORMAL_BURST\s*\+\s*1\)",
            iobudget):
        raise ContractError("cold-read extent does not cross the owner burst")
    if not re.search(
            r"#define\s+COLD_PRESSURE_BLOCKS\s*\\\s*"
            r"\(COLD_CACHE_BLOCKS\s*>\s*COLD_RATE_BLOCKS\s*\?\s*\\\s*"
            r"COLD_CACHE_BLOCKS\s*:\s*COLD_RATE_BLOCKS\)",
            iobudget):
        raise ContractError("cold-read extent does not cover cache and rate limits")
    for token in (
        "#define COLD_PRESSURE_BYTES "
        "(COLD_PRESSURE_BLOCKS * IO_BLOCK_SIZE)",
        "static char cold_pressure_data[COLD_PRESSURE_BYTES];",
    ):
        if token not in iobudget:
            raise ContractError("cold-read buffer is detached from its extent")
    setup = compact(function_body(iobudget, "setup_lineage_pressure"))
    if not ordered(
        setup,
        "memset(cold_pressure_data, 'C', sizeof(cold_pressure_data));",
        'create_file("iocold", cold_pressure_data, sizeof(cold_pressure_data));',
        "memset(cold_pressure_data, 'E', sizeof(cold_pressure_data));",
        'create_file("ioevict", cold_pressure_data, sizeof(cold_pressure_data));',
    ):
        raise ContractError("dynamic probe does not prepare and evict a cold file")

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
            raise ContractError("dynamic probe lacks a clean idle-loan baseline")

    pressure = compact(function_body(iobudget, "run_lineage_attacker"))
    if not ordered(
        pressure,
        "setup_lineage_pressure();",
        "wait_for_idle_loan_window(&before);",
        "memset(cold_pressure_data, 0, sizeof(cold_pressure_data));",
        'cold_fd = open("iocold", O_RDONLY);',
        "read_exact(cold_fd, cold_pressure_data, sizeof(cold_pressure_data),",
        "check(close(cold_fd) == 0",
        "cold_pressure_data[0] == 'C'",
        "check(io_policy_info(&after) == 0",
        "read_transfers = after.physical_reads - before.physical_reads;",
        "shared_decisions = after.shared_grants - before.shared_grants;",
        "throttle_decisions = after.throttles - before.throttles;",
        "check(read_transfers > (unsigned long long)before.class_burst,",
        "borrowed = shared_decisions > 0;",
        "check(borrowed,",
        "check(throttle_decisions == 0,",
    ):
        raise ContractError("dynamic evidence is not a cold shared-capacity read")
    if "refill_covered" in pressure or "borrowed = 1;" in pressure:
        raise ContractError("dynamic evidence can be satisfied without shared work")

    main = compact(function_body(iobudget, "main"))
    if not ordered(
        main,
        "attacker = fork();",
        "read_exact(attacker_ready[0], &signal, sizeof(signal),",
        "workflow = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);",
        "read_exact(workflow_report[0], &report, sizeof(report),",
        "check(report.cache_isolated,",
        "waitpid(attacker, &status)",
        "waitpid(workflow, &status)",
        "check_lazy_cache_admission();",
        "check_thread_exit_lease_cleanup();",
        "check_scheduler_interrupt_progress();",
        "check_fault_exit_cleanup(lineage.owner);",
    ):
        raise ContractError("attacker/workflow proof is not isolated from late probes")


validate(BIO, POLICY, IOBUDGET)

MUTATIONS = (
    (BIO, mutate_once(POLICY, "#define IO_POLICY_VERSION 6U",
                      "#define IO_POLICY_VERSION 5U"), IOBUDGET),
    (BIO, mutate_once(POLICY, "#define IO_POLICY_SHARED_REFILL 280U",
                      "#define IO_POLICY_SHARED_REFILL 16U"), IOBUDGET),
    (mutate_once(BIO, "#define IO_RATE_LOCAL_BATCH 32U",
                 "#define IO_RATE_LOCAL_BATCH 1U"), POLICY, IOBUDGET),
    (mutate_once(BIO, "endpoints[0].amount = amount;",
                 "endpoints[0].amount = 1;"), POLICY, IOBUDGET),
    (mutate_once(BIO, "if (!shared)\n\t\tendpoints[1].flags =",
                 "if (shared)\n\t\tendpoints[1].flags ="), POLICY, IOBUDGET),
    (mutate_once(BIO, "endpoints[1].flags =",
                 "endpoints[0].flags ="), POLICY, IOBUDGET),
    (mutate_once(BIO, "if (!shared)",
                 "if (!shared || io_device_protected(state->owner, io_class))"),
     POLICY, IOBUDGET),
    (mutate_once(BIO, "lease.slot | (shared ? IO_RESERVATION_SHARED : 0)",
                 "lease.slot"), POLICY, IOBUDGET),
    (mutate_once(BIO, "slot & IO_RESERVATION_SLOT_MASK", "slot"),
     POLICY, IOBUDGET),
    (mutate_once(BIO, "(slot & IO_RESERVATION_SHARED) != 0", "slot != 0"),
     POLICY, IOBUDGET),
    (mutate_once(BIO, "lane.debt == 0 && lane.pending_debt == 0",
                 "lane.debt == 0"), POLICY, IOBUDGET),
    (mutate_once(BIO, "state->shared_grants += shared;",
                 "state->reserved_grants += shared;"), POLICY, IOBUDGET),
    (mutate_once(BIO, "state, io_class, 1, shared, endpoints",
                 "state, io_class, 0, shared, endpoints"), POLICY, IOBUDGET),
    (mutate_once(BIO, "for (uint64 i = 0; i < amount; i++)",
                 "if (amount != 0)"), POLICY, IOBUDGET),
    (mutate_once(BIO, "endpoints[0].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;",
                 ""), POLICY, IOBUDGET),
    (mutate_once(BIO, "endpoints[1].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;",
                 ""), POLICY, IOBUDGET),
    (mutate_once(BIO, "!state->retiring && !state->quiesced",
                 "!state->retiring && !state->quiesced && "
                 "!io_any_admission_waiters()"), POLICY, IOBUDGET),
    (mutate_once(BIO, "uint cache_donor_cursor;",
                 "uint cache_donor_cursor;\n\tuint active_owner_states;"),
     POLICY, IOBUDGET),
    (mutate_once(BIO, "io_rate_lease_store(\n\t\t\t\tlease, 1, source, device_source);",
                 "state->shared_grants++;\n\t\t\tio_rate_lease_store(\n"
                 "\t\t\t\tlease, 1, source, device_source);"), POLICY, IOBUDGET),
    (mutate_once(BIO,
                 "\tif (!shared)\n\t\tstate->reserved_grants++;\n"
                 "\tbucket->grantee = grantee;",
                 "\tif (!shared)\n\t\tstate->reserved_grants++;\n"
                 "\telse\n\t\tstate->shared_grants++;\n"
                 "\tbucket->grantee = grantee;"), POLICY, IOBUDGET),
    (mutate_once(BIO,
                 "\tgrantee = wait_queue_wake_one_thread("
                 "&bucket->admission_queue);",
                 "\tgrantee = bucket->admission_queue.head;"),
     POLICY, IOBUDGET),
    (mutate_once(BIO, "*request_flags |= BIO_REQUEST_TRANSFERRED;",
                 "*request_flags |= BIO_REQUEST_ACTIVE;"), POLICY, IOBUDGET),
    (mutate_once(BIO, "if (*transfers >= IO_RATE_LOCAL_BATCH)",
                 "if (*transfers >= 1)"), POLICY, IOBUDGET),
    (mutate_once(BIO,
                 "bio_account_transfers(owner, io_class, transfer, results, count);",
                 "for (uint i = 0; i < count; i++)\n\t\tbio_account_transfer("
                 "owner, io_class, transfer, results[i]);"), POLICY, IOBUDGET),
    (mutate_once(BIO, "io_rate_charge_transfers(state, io_class, transfers);",
                 "io_rate_charge_transfers(state, io_class, 0);"),
     POLICY, IOBUDGET),
    (BIO, POLICY, mutate_once(IOBUDGET,
        'create_file("iocold", cold_pressure_data,\n\t\t    sizeof(cold_pressure_data));',
        'create_file("iohot", cold_pressure_data,\n\t\t    sizeof(cold_pressure_data));')),
    (BIO, POLICY, mutate_once(IOBUDGET,
        "(COLD_CACHE_BLOCKS > COLD_RATE_BLOCKS ? \\\n\t COLD_CACHE_BLOCKS : COLD_RATE_BLOCKS)",
        "COLD_CACHE_BLOCKS")),
    (BIO, POLICY, mutate_once(IOBUDGET,
        'create_file("ioevict", cold_pressure_data,\n\t\t    sizeof(cold_pressure_data));',
        'create_file("iocold", cold_pressure_data,\n\t\t    sizeof(cold_pressure_data));')),
    (BIO, POLICY, mutate_once(IOBUDGET,
        "wait_for_idle_loan_window(&before);", "io_policy_info(&before);")),
    (BIO, POLICY, mutate_once(IOBUDGET,
        'cold_fd = open("iocold", O_RDONLY);',
        'cold_fd = open("ioevict", O_RDONLY);')),
    (BIO, POLICY, mutate_once(IOBUDGET,
        "check(read_transfers > (unsigned long long)before.class_burst,",
        "check(read_transfers > 0,")),
    (BIO, POLICY, mutate_once(IOBUDGET,
        "borrowed = shared_decisions > 0;", "borrowed = 1;")),
    (BIO, POLICY, mutate_once(IOBUDGET,
        "check(borrowed,", "check(1,")),
    (BIO, POLICY, mutate_once(IOBUDGET,
        "check(throttle_decisions == 0,", "check(1,")),
    (BIO, POLICY, mutate_once(IOBUDGET,
        "snapshot->shared_tokens == IO_POLICY_SHARED_BURST", "1")),
    (BIO, POLICY, mutate_once(IOBUDGET,
        "read_exact(attacker_ready[0], &signal, sizeof(signal),",
        "read_exact_disabled(attacker_ready[0], &signal, sizeof(signal),")),
)

fault_block = (
    "\tcheck_fault_exit_cleanup(lineage.owner);\n"
    "\tprintf(\"iobudget_ucore: fault_exit_cleanup=1\\n\");\n"
)
fault_early = mutate_once(IOBUDGET, fault_block, "")
fault_early = mutate_once(
    fault_early, "\tcheck_lazy_cache_admission();\n",
    fault_block + "\tcheck_lazy_cache_admission();\n")
MUTATIONS += ((BIO, POLICY, fault_early),)

for mutated_bio, mutated_policy, mutated_iobudget in MUTATIONS:
    try:
        validate(mutated_bio, mutated_policy, mutated_iobudget)
    except ContractError:
        continue
    raise SystemExit("work-conserving I/O mutation survived")

print(f"[io-work-conserving] {len(MUTATIONS)} mutations passed")
