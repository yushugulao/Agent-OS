#!/usr/bin/env python3
"""Static and model checks for the U/P/F workflow credit controller."""

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = (ROOT / "os/resource_controller.c").read_text(encoding="utf-8")
CONTROLLER_H = (ROOT / "os/resource_controller.h").read_text(encoding="utf-8")
DOMAIN_H = (ROOT / "os/workflow_credit_domain.h").read_text(encoding="utf-8")
DOMAIN_C = (ROOT / "os/workflow_credit_domain.c").read_text(encoding="utf-8")
PROC = (ROOT / "os/proc.c").read_text(encoding="utf-8")
FS = (ROOT / "os/fs.c").read_text(encoding="utf-8")
BIO = (ROOT / "os/bio.c").read_text(encoding="utf-8")
KALLOC = (ROOT / "os/kalloc.c").read_text(encoding="utf-8")
CONTEXT = (ROOT / "os/agent_context.c").read_text(encoding="utf-8")
VFS = (ROOT / "os/vfs_security.c").read_text(encoding="utf-8")


def function_body(source: str, signature: str) -> str:
    start = source.find(signature)
    assert start >= 0, f"missing function: {signature}"
    opening = source.find("{", start)
    assert opening >= 0
    depth = 0
    for pos in range(opening, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[opening : pos + 1]
    raise AssertionError(f"unterminated function: {signature}")


def static_contracts() -> None:
    assert "uint used;" in DOMAIN_H
    assert "uint pending;" in DOMAIN_H
    assert "uint free;" in DOMAIN_H
    assert "workflow_credit_counter_held" in DOMAIN_H
    assert "struct workflow_credit_snapshot" in DOMAIN_H
    assert "uint free_mask;" in CONTROLLER

    promise_preflight = function_body(
        CONTROLLER, "int resource_account_promise_admissible("
    )
    assert "resource_account_promise_admissible(" in CONTROLLER_H
    assert "resource_promises_replace(0, charge_grants, limits, 0)" in \
        promise_preflight
    assert "resource_promises_replace(0, charge_grants, limits, 1)" not in \
        promise_preflight
    storage_limits = function_body(FS, "fs_storage_scope_account_limits(")
    for kind in (
        "RESOURCE_FS_BLOCK",
        "RESOURCE_FS_INODE",
        "RESOURCE_BUFFER_CACHE",
        "RESOURCE_PHYSICAL_PAGE",
    ):
        assert kind in storage_limits
    storage_create = function_body(FS, "int fs_storage_scope_account_create(")
    storage_admission = function_body(FS, "int fs_storage_scope_admissible(")
    assert "fs_storage_scope_account_limits(&limits)" in storage_create
    assert "fs_storage_scope_account_limits(&limits)" in storage_admission
    assert "resource_account_promise_admissible(" in storage_admission
    fresh_admission = function_body(
        VFS, "int vfs_scope_fresh_admission_status("
    )
    assert "reclaimable = !ready && registry->retiring_count != 0" in \
        fresh_admission
    assert fresh_admission.index("vfs_scope_reap_pending(0)") < \
        fresh_admission.rindex("fs_storage_scope_admissible()")
    fence_wrapper = function_body(
        DOMAIN_C, "workflow_credit_domain_fence("
    )
    assert "out->pending[kind] != 0" in fence_wrapper
    assert fence_wrapper.index("resource_credit_snapshot_pair_trim") < \
        fence_wrapper.index("out->pending[kind] != 0")

    switch = function_body(
        DOMAIN_C, "workflow_credit_domain_switch("
    )
    assert "workflow_lifecycle_key_equal(previous_key, next_key)" in switch
    assert "resource_account_handle_equal(previous_exec, next_exec)" in switch
    assert switch.index("workflow_lifecycle_key_equal") < switch.index(
        "resource_account_trim(previous_exec)"
    )
    scheduler = function_body(PROC, "void scheduler()")
    assert scheduler.count("workflow_credit_domain_switch(") == 1
    assert scheduler.index("workflow_credit_domain_switch(") < scheduler.index(
        "agent_sched_on_dispatch(t)"
    )

    fork_common = function_body(PROC, "static int fork_common(")
    assert fork_common.count("workflow_lifecycle_operation_enter(") == 1
    assert fork_common.count("workflow_lifecycle_operation_leave(") == 3
    assert fork_common.index("workflow_lifecycle_operation_enter(") < \
        fork_common.index("proc_vm_snapshot_begin(p)")
    assert "if (lifecycle_entered)" in fork_common

    acquire = function_body(CONTROLLER, "resource_credit_acquire_vector_locked(")
    assert "policy->held += refill[kind]" in acquire
    assert "resource_credit_free_take(" in acquire
    assert "counter->pending += amount" in acquire
    assert "counter->used += amount" in acquire
    assert acquire.index("missing > available") < acquire.index("policy->held +=")
    assert "resource_credit_reclaim_pressure_locked(" in acquire
    assert acquire.index("resource_credit_reclaimable_locked(") < acquire.index(
        "resource_credit_reclaim_pressure_locked("
    )
    assert "policy->used" not in acquire
    assert "resource_credit_changed" not in acquire

    reconcile = function_body(CONTROLLER, "int resource_reconcile_usage(")
    assert reconcile.index("resource_credit_reclaimable_locked(") < \
        reconcile.index("resource_credit_trim_counter(")
    assert reconcile.index("goto fail;") < reconcile.index(
        "resource_credit_trim_counter("
    )

    transfer = function_body(CONTROLLER, "int resource_transfer_usage_flags(")
    assert "reuse[kind]" in transfer
    assert "resource_credit_reclaimable_locked(" in transfer
    assert "resource_credit_reclaim_class_pressure_locked(" in transfer
    assert transfer.index("resource_credit_reclaimable_locked(") < \
        transfer.index("resource_credit_reclaim_class_pressure_locked(") < \
        transfer.index("source_counter->used -= amount")
    assert "target, to_charge_class, kind, reused" in transfer
    assert "source, from_charge_class, kind, reused" in transfer

    class_pressure = function_body(
        CONTROLLER, "resource_credit_reclaim_class_pressure_locked("
    )
    assert "resource_credit_trim_counter(account, target_class, kind)" in \
        class_pressure
    assert "resource_credit_refill_available(" not in class_pressure

    reserve = function_body(CONTROLLER, "int resource_reserve_many_flags(")
    immediate = function_body(CONTROLLER, "int resource_acquire_many_flags(")
    assert "amounts, kind_mask, 1," in reserve
    assert "amounts, kind_mask, 0," in immediate

    commit = function_body(CONTROLLER, "int resource_reservation_commit(")
    cancel = function_body(CONTROLLER, "void resource_reservation_cancel(")
    release = function_body(CONTROLLER, "int resource_release_many(")
    assert commit.index("counter->pending -= amount") < commit.index(
        "counter->used += amount"
    )
    assert cancel.index("counter->pending -= amount") < cancel.index(
        "resource_credit_free_add("
    )
    assert release.index("counter->used -= amount") < release.index(
        "resource_credit_free_add("
    )
    for hot_path in (commit, cancel, release):
        assert "resource_credit_changed" not in hot_path
        assert "policy->used" not in hot_path

    trim = function_body(CONTROLLER, "resource_credit_trim_counter(")
    assert "policy->held -= amount" in trim
    assert "*class_held -= amount" in trim
    assert "resource_credit_free_take(" in trim
    assert "resource_credit_changed" not in trim

    reclaimable = function_body(
        CONTROLLER, "resource_credit_reclaimable_locked("
    )
    assert "policy->free[charge_class] - own" in reclaimable
    assert "RESOURCE_ACCOUNT_CAP" not in reclaimable
    pressure = function_body(
        CONTROLLER, "resource_credit_reclaim_pressure_locked("
    )
    assert "account == exclude && scanned_class == target_class" in pressure
    assert "resource_credit_refill_available(" in pressure

    cached_trim = function_body(
        CONTROLLER, "resource_account_trim_cached_locked("
    )
    assert "kind_mask = account->free_mask" in cached_trim
    assert "resource_kind_first(kind_mask)" in cached_trim
    account_trim = function_body(CONTROLLER, "int resource_account_trim(")
    assert "resource_account_trim_cached_locked(account)" in account_trim
    advance = function_body(
        CONTROLLER,
        "static void resource_account_advance(struct resource_account *account)\n{",
    )
    assert "resource_account_trim_locked(account)" in advance

    fence = function_body(CONTROLLER, "int resource_credit_snapshot_pair_trim(")
    assert fence.count("enabled = intr_save()") == 1
    assert fence.index("resource_account_trim_locked") < fence.index(
        "resource_credit_account_snapshot_locked"
    )
    assert fence.index("resource_credit_changed()") < fence.index(
        "snapshot->epoch = resource_credit_epoch"
    )

    for signature in (
        "proc_thread_resource_charge_locked(",
        "proc_resource_reserve(",
        "proc_file_slots_reserve(",
    ):
        body = function_body(PROC, signature)
        assert "resource_acquire_many(" in body
        assert "resource_reservation_commit" not in body
    assert "resource_acquire_many(account, charge_class, &request, 1)" in FS
    assert "resource_acquire_many_flags(" in BIO

    for source, signature in (
        (KALLOC, "void *kalloc_account_page("),
        (CONTEXT, "agent_context_alloc("),
    ):
        body = function_body(source, signature)
        assert "resource_reserve_many(" in body
        assert "resource_reservation_commit(" in body
        assert "resource_reservation_cancel(" in body

    page_release = function_body(KALLOC, "static int account_page_release(")
    real_death = max(
        page_release.index("kfree_reserved_page_validated(pa)"),
        page_release.index("kfree_system_page(pa)"),
    )
    assert real_death < page_release.index("resource_release_many(")


@dataclass
class Counter:
    used: int = 0
    pending: int = 0
    free: int = 0

    @property
    def held(self) -> int:
        return self.used + self.pending + self.free


class CreditModel:
    def __init__(self, capacity: int, quantum: int) -> None:
        self.capacity = capacity
        self.quantum = quantum
        self.accounts: dict[str, Counter] = {}

    @property
    def held(self) -> int:
        return sum(counter.held for counter in self.accounts.values())

    def account(self, name: str) -> Counter:
        return self.accounts.setdefault(name, Counter())

    def trim(self, name: str) -> None:
        self.account(name).free = 0

    def take(self, name: str, amount: int, limit: int, pending: bool) -> bool:
        counter = self.account(name)
        missing = max(0, amount - counter.free)
        if counter.held + missing > limit:
            return False
        if self.held + missing > self.capacity:
            for other in self.accounts:
                if other != name:
                    self.trim(other)
        if self.held + missing > self.capacity:
            return False
        refill = min(max(missing, self.quantum), limit - counter.held)
        refill = min(refill, self.capacity - self.held)
        if refill < missing:
            return False
        counter.free += refill
        counter.free -= amount
        if pending:
            counter.pending += amount
        else:
            counter.used += amount
        return True

    def commit(self, name: str, amount: int) -> None:
        counter = self.account(name)
        assert counter.pending >= amount
        counter.pending -= amount
        counter.used += amount

    def cancel(self, name: str, amount: int) -> None:
        counter = self.account(name)
        assert counter.pending >= amount
        counter.pending -= amount
        counter.free += amount

    def release(self, name: str, amount: int) -> None:
        counter = self.account(name)
        assert counter.used >= amount
        counter.used -= amount
        counter.free += amount


def model_contracts() -> None:
    model = CreditModel(capacity=8, quantum=8)
    assert model.take("a", 1, limit=8, pending=False)
    assert model.account("a") == Counter(used=1, pending=0, free=7)
    model.release("a", 1)
    assert model.account("a") == Counter(used=0, pending=0, free=8)

    # Pressure reclaims only idle F, then gives B an exact globally held batch.
    assert model.take("b", 2, limit=8, pending=False)
    assert model.account("a").held == 0
    assert model.held == 8
    assert model.account("b") == Counter(used=2, pending=0, free=6)

    model.release("b", 2)
    model.trim("b")
    assert model.held == 0

    assert model.take("p", 3, limit=4, pending=True)
    assert model.account("p") == Counter(used=0, pending=3, free=1)
    model.commit("p", 2)
    assert model.account("p") == Counter(used=2, pending=1, free=1)
    model.cancel("p", 1)
    assert model.account("p") == Counter(used=2, pending=0, free=2)
    assert not model.take("p", 3, limit=4, pending=False)
    model.release("p", 2)
    model.trim("p")
    assert model.held == 0

    def switch_trims(
        previous_key: tuple[int, int] | None,
        previous_account: tuple[int, int],
        next_key: tuple[int, int] | None,
        next_account: tuple[int, int],
    ) -> bool:
        if previous_key is not None and next_key is not None:
            return previous_key != next_key
        if previous_key is None and next_key is None:
            return previous_account != next_account
        return True

    # Sibling threads in one full lifecycle retain their hot credit batch.
    assert not switch_trims((7, 3), (2, 9), (7, 3), (2, 9))
    # Generation is part of identity; recycled workflow ids must flush.
    assert switch_trims((7, 3), (2, 9), (7, 4), (2, 10))
    # PUBLIC work uses the generation-safe EXEC handle as its pseudo-domain.
    assert not switch_trims(None, (4, 11), None, (4, 11))
    assert switch_trims(None, (4, 11), None, (5, 2))

    # A cross-class transfer does not grow global held credit.  Idle target-
    # class stock must therefore be reclaimable before rejecting real usage.
    target_class_limit = 8
    target_class_used = 4
    unrelated_target_free = 4
    transfer_amount = 2
    assert target_class_used + unrelated_target_free + transfer_amount > \
        target_class_limit
    unrelated_target_free = 0
    assert target_class_used + unrelated_target_free + transfer_amount <= \
        target_class_limit


def main() -> None:
    static_contracts()
    model_contracts()
    print("workflow credit domain contracts: ok")


if __name__ == "__main__":
    main()
