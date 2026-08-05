#!/usr/bin/env python3
"""Mutation and model tests for catalog reserves and durable deferral."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "metadata_catalog_capacity_contract",
    ROOT / "scripts/check-metadata-catalog-capacity.py",
)
assert SPEC and SPEC.loader
CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT)

CATALOG_SCOPE_LIMIT = 112
AUTOSCAN_SCOPE_LIMIT = 96
EXPLICIT_RESERVE = 16
WORKFLOW_INODE_DOMAIN_LIMIT = 320
DEFERRED_META_SLOT = -1


def mutate(sources: dict[str, str], name: str, old: str, new: str) -> dict[str, str]:
    changed = dict(sources)
    if old not in changed[name]:
        raise AssertionError(f"mutation anchor is missing: {name}: {old}")
    changed[name] = changed[name].replace(old, new, 1)
    return changed


def workflow_scope_create_allowed(states: list[str]) -> bool:
    occupied = sum(state in {"active", "closing", "retiring"}
                   for state in states)
    return occupied < 4


def storage_guarantee_required(
    scopes: list[tuple[str, str, int]], exempt: str | None,
    guarantee: int = 16,
) -> int:
    retained = [(name, used) for name, state, used in scopes
                if state in {"active", "closing", "retiring"}]
    required = sum(max(guarantee - used, 0) for name, used in retained
                   if name != exempt)
    return required + max(4 - len(retained), 0) * guarantee


def workflow_inode_allocation_allowed(
    owned: int, domain_limit: int = WORKFLOW_INODE_DOMAIN_LIMIT,
) -> bool:
    return owned < domain_limit


def catalog_slot_allowed(
    system: bool, owned: int, ordinary: int, *, old_autoscan: bool = False,
    new_autoscan: bool = False, autoscan_owned: int = 0,
) -> bool:
    if system:
        return owned < 64 and owned + ordinary < 512
    if owned >= CATALOG_SCOPE_LIMIT or ordinary >= 448:
        return False
    return not (new_autoscan and not old_autoscan and
                autoscan_owned >= AUTOSCAN_SCOPE_LIMIT)


def catalog_admission_allowed(
    system: bool, owned: int, ordinary: int, *, growth: bool,
    lifecycle_admitted: bool, old_autoscan: bool = False,
    new_autoscan: bool = False, autoscan_owned: int = 0,
) -> bool:
    if not catalog_slot_allowed(
        system, owned, ordinary, old_autoscan=old_autoscan,
        new_autoscan=new_autoscan, autoscan_owned=autoscan_owned,
    ):
        return False
    if not growth or system:
        return True
    return lifecycle_admitted


def catalog_workflow_allocation_allowed(
    records: dict[str, int], live_scopes: set[str], target: str,
) -> bool:
    return catalog_admission_allowed(
        False, records.get(target, 0), sum(records.values()), growth=True,
        lifecycle_admitted=target in live_scopes,
    )


def defer_sidecar(sidecar_slot: int, stale_validated: bool) -> tuple[bool, int]:
    if sidecar_slot > 0 and not stale_validated:
        return False, sidecar_slot
    return True, DEFERRED_META_SLOT


def deferred_scan_state(scan_on: bool, autoscan_owned: int) -> str:
    if not scan_on:
        return "stranded"
    if autoscan_owned >= AUTOSCAN_SCOPE_LIMIT:
        return "deferred"
    return "indexed"


def boot_reconcile_deferred_sidecar(
    snapshot_status: str, catalog_used: int, sidecar_slot: int,
) -> tuple[str, int, int]:
    if snapshot_status != "trusted-complete":
        return "fail-closed", catalog_used, sidecar_slot
    if sidecar_slot != DEFERRED_META_SLOT:
        return "scan-complete", catalog_used, sidecar_slot
    if catalog_used >= AUTOSCAN_SCOPE_LIMIT:
        return "deferred", catalog_used, sidecar_slot
    return "indexed", catalog_used + 1, catalog_used + 1


def stale_sweep(records: list[tuple[int, bool]], failed: set[int],
                uncertain: bool) -> list[int]:
    return [scope for scope, seen in records
            if not seen and not uncertain and scope not in failed]


def prepare_rounds(count: int, reload_one_scope: bool) -> int:
    if reload_one_scope:
        return 1
    return max(1, (count + 31) // 32)


def retained_scope_plan_allowed(scopes: list[int]) -> bool:
    counts: dict[int, int] = {}
    for scope in scopes:
        counts[scope] = counts.get(scope, 0) + 1
        if len(counts) > 8 or counts[scope] > 112:
            return False
    return len(scopes) <= 448


def scoped_reload_plan_allowed(
    records: dict[str, int], live_scopes: set[str], target: str,
) -> bool:
    if target not in live_scopes or len(live_scopes) > 4:
        return False
    if len(records) > 8 or any(count > 112 for count in records.values()):
        return False
    ordinary = sum(records.values())
    return ordinary <= 448


def cold_boot_snapshot_status(
    records: list[tuple[str, bool]], live_scopes: set[str],
) -> tuple[str, int]:
    counts: dict[str, int] = {}
    discarded = 0
    for scope, _is_autoscan in records:
        if scope not in live_scopes:
            discarded += 1
            continue
        counts[scope] = counts.get(scope, 0) + 1
        if counts[scope] > CATALOG_SCOPE_LIMIT:
            return "corrupt", discarded
    return "ok", discarded


def catalog_flag_transaction(
    records: list[bool], index: int, new_autoscan: bool, persist: bool,
) -> tuple[list[bool], str]:
    before = list(records)
    old_autoscan = records[index]
    autoscan_owned = sum(records) - int(old_autoscan)
    if not catalog_admission_allowed(
        False, len(records) - 1, len(records) - 1, growth=False,
        lifecycle_admitted=False, old_autoscan=old_autoscan,
        new_autoscan=new_autoscan, autoscan_owned=autoscan_owned,
    ):
        return before, "rejected"
    records[index] = new_autoscan
    if persist:
        return records, "committed"
    # Receipt-authorized undo applies the old image against hard bounds only.
    if not catalog_slot_allowed(False, len(records) - 1, len(records) - 1):
        raise AssertionError("hard rollback admission failed")
    return before, "rolled_back"


def classify_identity(scope: str, autoscan: bool,
                      identity: tuple[int, int, int], ready: bool = False
                      ) -> tuple[str, bool]:
    present = all(identity)
    absent = not any(identity)
    if not present and not absent:
        raise ValueError("partial identity")
    if present:
        return ("ready" if ready else "pending", False)
    if scope == "system" or autoscan:
        return ("pending", True)
    return ("quarantine", False)


def catalog_visible(state: str, scanner: bool) -> bool:
    return scanner or state == "ready"


def reconcile_sweep(records: list[tuple[int, bool, str]], failed: set[int],
                    uncertain: bool) -> list[int]:
    return [slot for slot, (scope, seen, state) in enumerate(records)
            if state != "quarantine" and not seen and not uncertain and
            scope not in failed]


def duplicate_conflict(records: list[dict[str, object]],
                       candidate: dict[str, object]) -> bool:
    for record in records:
        if record["scope"] != candidate["scope"]:
            continue
        if record["fid"] == candidate["fid"]:
            return True
        if record["path"] == candidate["path"]:
            return True
        if candidate.get("logical") and \
                record.get("logical") == candidate["logical"]:
            return True
        if candidate["identity"] != (0, 0, 0) and \
                record["identity"] == candidate["identity"]:
            return True
    return False


def resolve_selector(records: list[dict[str, object]],
                     candidate: dict[str, object]) -> tuple[int, set[str], set[str]]:
    provided = {key for key in ("fid", "path", "logical", "identity")
                if candidate.get(key) not in (None, 0, "", (0, 0, 0))}
    matched: set[str] = set()
    selected = -1
    states: set[str] = set()
    for slot, record in enumerate(records):
        if record["scope"] != candidate["scope"]:
            continue
        keys = {key for key in provided if record.get(key) == candidate.get(key)}
        if not keys:
            continue
        matched |= keys
        if record.get("state") != "ready":
            states.add(str(record["state"]))
        selected = slot if selected == -1 else (-3 if selected != slot else slot)
    return selected, matched, states


def normalize_physical(slot: int, path: str) -> str:
    if not path or len(path) > 14 or path in (".agentmeta", ".agentmeta1"):
        return f"af{slot:03d}"
    return path


def scanner_rebind_slot(records: list[dict[str, object]], scope: int,
                        path: str, identity: tuple[int, int, int]) -> int:
    if not path or not all(identity):
        return -1
    candidate = {
        "scope": scope, "path": path, "logical": path,
        "identity": identity,
    }
    slot, matched, _ = resolve_selector(records, candidate)
    path_matched = bool(matched & {"path", "logical"})
    if slot == -3 or ("identity" in matched and not path_matched):
        return -3
    if not path_matched:
        return -1
    record_identity = tuple(records[slot]["identity"])
    return -3 if any(record_identity) and not all(record_identity) else slot


def scanner_identity_action(record: tuple[int, int, int],
                            inode: tuple[int, int, int]) -> str:
    if not any(record):
        return "initialize"
    return "reconcile" if record == inode else "fresh-record"


class CatalogCapacityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = CONTRACT.load_sources(ROOT)

    def test_current_tree_passes(self) -> None:
        CONTRACT.validate_sources(self.sources)

    def test_system_scan_requires_trusted_image_identity(self) -> None:
        broken = mutate(
            self.sources,
            "scan",
            "scope == VFS_SCOPE_SYSTEM ? mut || !exec_policy_inode_trusted(ip) :",
            "scope == VFS_SCOPE_SYSTEM ? mut :",
        )
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.validate_sources(broken)

    def test_active_closing_and_retiring_share_four_slots(self) -> None:
        self.assertTrue(workflow_scope_create_allowed(["retiring"] * 3))
        self.assertFalse(workflow_scope_create_allowed(["retiring"] * 4))
        self.assertFalse(workflow_scope_create_allowed(
            ["active", "closing", "retiring", "active"]))
        self.assertTrue(workflow_scope_create_allowed(
            ["active", "retiring", "reclaimed"]))
        self.assertFalse(catalog_slot_allowed(False, 111, 448))
        self.assertTrue(catalog_slot_allowed(False, 111, 447))
        self.assertFalse(catalog_slot_allowed(False, 112, 112))
        self.assertTrue(catalog_slot_allowed(True, 63, 448))
        self.assertFalse(catalog_slot_allowed(True, 64, 448))

    def test_retiring_usage_offsets_its_guarantee_slot(self) -> None:
        self.assertEqual(storage_guarantee_required([], None), 64)
        self.assertEqual(storage_guarantee_required(
            [("old", "retiring", 0)], None), 64)
        self.assertEqual(storage_guarantee_required(
            [("old", "retiring", 8)], None), 56)
        self.assertEqual(storage_guarantee_required(
            [("old", "retiring", 16)], None), 48)
        self.assertEqual(storage_guarantee_required(
            [("old", "retiring", 112)], None), 48)
        self.assertEqual(storage_guarantee_required(
            [("old", "retiring", 8)], "old"), 48)

    def test_active_allocation_does_not_double_count_retiring_slot(self) -> None:
        scopes = [
            ("target", "active", 0),
            ("old", "retiring", 112),
        ]
        # target is exempt from its own allocation. The retiring scope occupies
        # one slot and its existing usage fully offsets that slot's guarantee;
        # only the two genuinely empty future slots remain.
        self.assertEqual(storage_guarantee_required(scopes, "target"), 32)
        # The obsolete skip-retiring model treated old as an empty future slot.
        legacy_skip_retiring = (4 - 1) * 16
        self.assertEqual(legacy_skip_retiring, 48)
        self.assertNotEqual(storage_guarantee_required(scopes, "target"),
                            legacy_skip_retiring)

    def test_fixed_catalog_partitions_isolate_scopes(self) -> None:
        self.assertTrue(catalog_workflow_allocation_allowed(
            {"a": 111}, {"a"}, "a"))
        self.assertFalse(catalog_workflow_allocation_allowed(
            {"a": 112}, {"a"}, "a"))
        # A full partition cannot consume B's independent 112-slot partition.
        self.assertTrue(catalog_workflow_allocation_allowed(
            {"a": 112, "b": 111}, {"a", "b"}, "b"))
        self.assertFalse(catalog_workflow_allocation_allowed(
            {"a": 112, "b": 112}, {"a", "b"}, "b"))
        self.assertFalse(catalog_workflow_allocation_allowed(
            {"retiring": 1}, set(), "retiring"))
        # All four fixed partitions exactly fill the ordinary table.
        self.assertFalse(catalog_workflow_allocation_allowed(
            {"a": 112, "b": 112, "c": 112, "d": 112},
            {"a", "b", "c", "d"}, "a"))
        # Receipt-bound replacement/undo remains possible without a live owner
        # when it does not grow the fixed partition.
        self.assertTrue(catalog_admission_allowed(
            False, 111, 447, growth=False, lifecycle_admitted=False))
        self.assertFalse(catalog_admission_allowed(
            False, 111, 447, growth=True, lifecycle_admitted=False))

    def test_autoscan_cache_preserves_sixteen_explicit_slots(self) -> None:
        self.assertEqual(AUTOSCAN_SCOPE_LIMIT + EXPLICIT_RESERVE,
                         CATALOG_SCOPE_LIMIT)
        self.assertTrue(catalog_admission_allowed(
            False, 95, 95, growth=True, lifecycle_admitted=True,
            new_autoscan=True, autoscan_owned=95))
        self.assertFalse(catalog_admission_allowed(
            False, 96, 96, growth=True, lifecycle_admitted=True,
            new_autoscan=True, autoscan_owned=96))
        # Only a transition into AUTOSCAN consumes the new-growth reserve.
        self.assertFalse(catalog_admission_allowed(
            False, 96, 96, growth=False, lifecycle_admitted=False,
            new_autoscan=True, autoscan_owned=96))
        self.assertTrue(catalog_admission_allowed(
            False, 111, 111, growth=False, lifecycle_admitted=False,
            old_autoscan=True, new_autoscan=True, autoscan_owned=111))
        self.assertTrue(catalog_admission_allowed(
            False, 111, 111, growth=False, lifecycle_admitted=False,
            old_autoscan=True, new_autoscan=False, autoscan_owned=111))
        self.assertTrue(catalog_admission_allowed(
            False, 111, 111, growth=True, lifecycle_admitted=True,
            new_autoscan=False, autoscan_owned=96))
        self.assertFalse(catalog_admission_allowed(
            False, 112, 112, growth=True, lifecycle_admitted=True,
            new_autoscan=False, autoscan_owned=96))

    def test_reload_binds_lifecycle_and_fixed_partitions(self) -> None:
        # Full boot must be able to load bounded retiring history so the reaper
        # can observe and release it.
        self.assertTrue(retained_scope_plan_allowed([3] * 112))
        self.assertFalse(retained_scope_plan_allowed([3] * 113))
        self.assertFalse(scoped_reload_plan_allowed(
            {"a": 113, "b": 1}, {"a", "b"}, "a"))
        self.assertTrue(scoped_reload_plan_allowed(
            {"a": 112, "b": 112}, {"a", "b"}, "a"))
        self.assertFalse(scoped_reload_plan_allowed(
            {"retiring": 112}, set(), "retiring"))
        # Disk compatibility is bounded by the v7 representation, not today's
        # stricter AUTOSCAN growth policy.
        self.assertTrue(scoped_reload_plan_allowed(
            {"a": 112}, {"a"}, "a"))

    def test_cold_boot_preserves_legacy_autoscan_within_hard_bound(self) -> None:
        old = [("old", True)] * CATALOG_SCOPE_LIMIT
        self.assertEqual(cold_boot_snapshot_status(old, set()),
                         ("ok", CATALOG_SCOPE_LIMIT))
        self.assertEqual(cold_boot_snapshot_status(old, {"old"}),
                         ("ok", 0))
        self.assertEqual(cold_boot_snapshot_status(
            old + [("old", True)], {"old"}), ("corrupt", 0))

    def test_loaded_overquota_scope_can_only_reduce_autoscan(self) -> None:
        records = [True] * CATALOG_SCOPE_LIMIT
        records, status = catalog_flag_transaction(records, 0, True, True)
        self.assertEqual((sum(records), status), (112, "committed"))
        records, status = catalog_flag_transaction(records, 0, False, True)
        self.assertEqual((sum(records), status), (111, "committed"))
        records, status = catalog_flag_transaction(records, 0, True, True)
        self.assertEqual((sum(records), status), (111, "rejected"))
        for index in range(1, 17):
            records, status = catalog_flag_transaction(
                records, index, False, True)
            self.assertEqual(status, "committed")
        self.assertEqual(sum(records), 95)
        records, status = catalog_flag_transaction(records, 0, True, True)
        self.assertEqual((sum(records), status), (96, "committed"))
        shrinking = [True] * CATALOG_SCOPE_LIMIT
        del shrinking[:16]
        self.assertFalse(catalog_admission_allowed(
            False, len(shrinking), len(shrinking), growth=True,
            lifecycle_admitted=True, new_autoscan=True,
            autoscan_owned=sum(shrinking)))
        shrinking.pop()
        self.assertTrue(catalog_admission_allowed(
            False, len(shrinking), len(shrinking), growth=True,
            lifecycle_admitted=True, new_autoscan=True,
            autoscan_owned=sum(shrinking)))

    def test_failed_overquota_reduction_restores_exact_prestate(self) -> None:
        records = [True] * CATALOG_SCOPE_LIMIT
        restored, status = catalog_flag_transaction(records, 0, False, False)
        self.assertEqual(status, "rolled_back")
        self.assertEqual(restored, [True] * CATALOG_SCOPE_LIMIT)

    def test_fid_bitmap_covers_the_inclusive_boundary(self) -> None:
        bitmap = bytearray((512 + 7) // 8)
        for fid in (1, 8, 9, 511, 512):
            bitmap[(fid - 1) // 8] |= 1 << ((fid - 1) % 8)
        for fid in (1, 8, 9, 511, 512):
            self.assertTrue(bitmap[(fid - 1) // 8] &
                            (1 << ((fid - 1) % 8)))

    def test_inode_account_is_independent_of_catalog_capacity(self) -> None:
        self.assertTrue(workflow_inode_allocation_allowed(111))
        self.assertTrue(workflow_inode_allocation_allowed(112))
        self.assertTrue(workflow_inode_allocation_allowed(319))
        self.assertFalse(workflow_inode_allocation_allowed(320))
        self.assertTrue(workflow_inode_allocation_allowed(0, domain_limit=1))
        self.assertFalse(workflow_inode_allocation_allowed(1, domain_limit=1))
        # Peer usage is deliberately irrelevant to this account-local bound.
        self.assertTrue(workflow_inode_allocation_allowed(0))

    def test_full_catalog_defers_index_without_rejecting_vfs_create(self) -> None:
        created = workflow_inode_allocation_allowed(112)
        indexed = catalog_admission_allowed(
            False, 112, 112, growth=True, lifecycle_admitted=True,
            new_autoscan=True, autoscan_owned=96)
        self.assertEqual((created, indexed), (True, False))
        self.assertEqual(defer_sidecar(0, False),
                         (True, DEFERRED_META_SLOT))

    def test_successful_deferral_keeps_reindex_progress_live(self) -> None:
        self.assertEqual(deferred_scan_state(False, 96), "stranded")
        self.assertEqual(deferred_scan_state(True, 96), "deferred")
        self.assertEqual(deferred_scan_state(True, 95), "indexed")

    def test_only_validated_stale_positive_sidecars_can_be_deferred(self) -> None:
        self.assertEqual(defer_sidecar(7, False), (False, 7))
        self.assertEqual(defer_sidecar(7, True),
                         (True, DEFERRED_META_SLOT))

    def test_failed_scope_isolated_from_stale_sweep(self) -> None:
        records = [(3, False), (4, False), (5, True)]
        self.assertEqual(stale_sweep(records, {3}, False), [4])
        self.assertEqual(stale_sweep(records, set(), True), [])
        # Capacity deferral has no failed scope, so stale slots can be reclaimed.
        self.assertEqual(stale_sweep(records, set(), False), [3, 4])

    def test_prepare_cadence_covers_over_32_and_full_512(self) -> None:
        self.assertEqual(prepare_rounds(32, False), 1)
        self.assertEqual(prepare_rounds(33, False), 2)
        self.assertEqual(prepare_rounds(512, False), 16)
        # Foreground scoped reload has a fixed 1024-item memory-only bound.
        self.assertEqual(prepare_rounds(512, True), 1)

    def test_prepare_accounts_for_active_and_retiring_scopes(self) -> None:
        # Four active plus four retiring generations are a legal retained set.
        self.assertTrue(retained_scope_plan_allowed(list(range(3, 11))))
        self.assertFalse(retained_scope_plan_allowed(list(range(3, 12))))
        self.assertTrue(retained_scope_plan_allowed(
            [scope for scope in range(3, 11) for _ in range(56)]))
        self.assertTrue(retained_scope_plan_allowed([3] * 112))
        self.assertFalse(retained_scope_plan_allowed([3] * 113))
        self.assertFalse(retained_scope_plan_allowed(
            [3] * 112 + [4] * 112 + [5] * 112 + [6] * 113))

    def test_zero_identity_classification_and_visibility(self) -> None:
        self.assertEqual(classify_identity("workflow", False, (0, 0, 0)),
                         ("quarantine", False))
        self.assertEqual(classify_identity("workflow", True, (0, 0, 0)),
                         ("pending", True))
        self.assertEqual(classify_identity("system", False, (0, 0, 0)),
                         ("pending", True))
        self.assertEqual(classify_identity("workflow", False, (1, 2, 3)),
                         ("pending", False))
        self.assertEqual(classify_identity(
            "workflow", False, (1, 2, 3), ready=True), ("ready", False))
        with self.assertRaises(ValueError):
            classify_identity("workflow", False, (1, 0, 3))
        for state in ("pending", "quarantine"):
            self.assertFalse(catalog_visible(state, scanner=False))
            self.assertTrue(catalog_visible(state, scanner=True))

    def test_hidden_records_still_reserve_all_unique_keys(self) -> None:
        hidden = [{"scope": 7, "fid": 11, "path": "artifact",
                   "logical": "result", "identity": (1, 2, 3),
                   "state": "quarantine"}]
        self.assertTrue(duplicate_conflict(hidden, {
            "scope": 7, "fid": 11, "path": "other",
            "identity": (4, 5, 6)}))
        self.assertTrue(duplicate_conflict(hidden, {
            "scope": 7, "fid": 12, "path": "artifact",
            "identity": (4, 5, 6)}))
        self.assertTrue(duplicate_conflict(hidden, {
            "scope": 7, "fid": 12, "path": "other",
            "identity": (1, 2, 3)}))
        self.assertTrue(duplicate_conflict(hidden, {
            "scope": 7, "fid": 12, "path": "other",
            "logical": "result", "identity": (4, 5, 6)}))
        self.assertFalse(duplicate_conflict(hidden, {
            "scope": 8, "fid": 11, "path": "artifact",
            "identity": (1, 2, 3)}))
        ready = {"scope": 7, "fid": 12, "path": "other",
                 "logical": "other-result", "identity": (4, 5, 6),
                 "state": "ready"}
        slot, matched, states = resolve_selector(hidden + [ready], hidden[0])
        self.assertEqual((slot, len(matched), states), (0, 4, {"quarantine"}))
        split = dict(hidden[0], fid=12)
        slot, matched, states = resolve_selector(hidden + [ready], split)
        self.assertEqual(slot, -3)
        self.assertEqual(matched, {"fid", "path", "logical", "identity"})
        self.assertEqual(states, {"quarantine"})
        pending = dict(hidden[0], state="pending")
        self.assertEqual(resolve_selector([hidden[0], pending], split)[2],
                         {"pending", "quarantine"})

    def test_reconcile_sweep_reclaims_pending_but_keeps_quarantine(self) -> None:
        records = [(3, False, "pending"), (3, False, "quarantine"),
                   (4, False, "ready"), (5, True, "pending")]
        self.assertEqual(reconcile_sweep(records, set(), False), [0, 2])
        self.assertEqual(reconcile_sweep(records, {3}, False), [2])
        self.assertEqual(reconcile_sweep(records, set(), True), [])

    def test_empty_snapshot_reconciles_persistent_deferred_sidecar(self) -> None:
        self.assertEqual(
            boot_reconcile_deferred_sidecar("trusted-complete", 0, -1),
            ("indexed", 1, 1),
        )
        self.assertEqual(
            boot_reconcile_deferred_sidecar("corrupt", 0, -1),
            ("fail-closed", 0, -1),
        )

    def test_pending_is_the_only_global_admission_barrier(self) -> None:
        self.assertFalse(any(state == "pending" for state in
                             ("ready", "quarantine")))
        self.assertTrue(any(state == "pending" for state in
                            ("ready", "pending", "quarantine")))

    def test_normalized_physical_key_is_admitted_centrally(self) -> None:
        existing = [{"scope": 7, "fid": 1, "path": "af005",
                     "identity": (1, 2, 3), "state": "quarantine"}]
        candidate = {"scope": 7, "fid": 2,
                     "path": normalize_physical(5, ""),
                     "identity": (0, 0, 0)}
        self.assertEqual(candidate["path"], "af005")
        self.assertTrue(duplicate_conflict(existing, candidate))

    def test_pending_record_selects_path_with_identity_crosscheck(self) -> None:
        path = "agentobsreboot"
        self.assertEqual(len(path), 14)
        self.assertEqual(normalize_physical(0, path), path)
        self.assertEqual(normalize_physical(0, path + "x"), "af000")
        records = [{"scope": 1, "path": "af000", "logical": path,
                    "identity": (1, 2, 1), "state": "pending"}]
        self.assertFalse(catalog_visible("pending", scanner=False))
        self.assertEqual(scanner_rebind_slot(
            records, 1, path, (1, 2, 1)), 0)
        self.assertEqual(scanner_rebind_slot(
            records, 2, path, (1, 2, 1)), -1)
        self.assertEqual(scanner_rebind_slot(
            records, 1, path, (1, 3, 1)), 0)
        self.assertEqual(scanner_rebind_slot(
            records, 1, "other", (1, 2, 1)), -3)
        self.assertEqual(scanner_rebind_slot(
            records + [dict(records[0])], 1, path, (1, 2, 1)), -3)
        legacy = [{"scope": 1, "path": path, "logical": path,
                   "identity": (0, 0, 0), "state": "pending"}]
        self.assertEqual(scanner_rebind_slot(
            legacy, 1, path, (1, 2, 1)), 0)
        split = records + [{"scope": 1, "path": path, "logical": "other",
                            "identity": (1, 3, 1), "state": "pending"}]
        self.assertEqual(scanner_rebind_slot(
            split, 1, path, (1, 2, 1)), -3)
        self.assertEqual(scanner_identity_action((0, 0, 0), (1, 2, 1)),
                         "initialize")
        self.assertEqual(scanner_identity_action((1, 2, 1), (1, 2, 1)),
                         "reconcile")
        self.assertEqual(scanner_identity_action((1, 2, 1), (1, 2, 2)),
                         "fresh-record")

    def test_mutations_are_rejected(self) -> None:
        cases = (
            ("agent", "#define AGENT_FILE_SYSTEM_LIMIT   64",
             "#define AGENT_FILE_SYSTEM_LIMIT   0"),
            ("catalog", "agent_catalog_files[AGENT_FILE_META_MAX]",
             "agent_catalog_files[AGENT_FILE_META_MAX + 64]"),
            ("catalog_h", "#define AGENT_CATALOG_SCOPE_PLAN_MAX 8",
             "#define AGENT_CATALOG_SCOPE_PLAN_MAX 4"),
            ("catalog_h", "#define AGENT_FILE_EXPLICIT_RESERVE 16",
             "#define AGENT_FILE_EXPLICIT_RESERVE 8"),
            ("agent", "#define AGENT_FILE_SCOPE_LIMIT    112",
             "#define AGENT_FILE_SCOPE_LIMIT    448"),
            ("catalog", "result->ordinary >= AGENT_FILE_ORDINARY_LIMIT",
             "result->ordinary >= AGENT_FILE_META_MAX"),
            ("catalog",
             "agent_catalog_scope_counts(\n"
             "\t\tscope_id, lifecycle, &owned, &autoscan)",
             "agent_catalog_scope_counts(\n"
             "\t\tscope_id, workflow_lifecycle_none(), &owned, &autoscan)"),
            ("catalog",
             "result.autoscan >= AGENT_FILE_AUTOSCAN_SCOPE_LIMIT",
             "result.autoscan > AGENT_FILE_AUTOSCAN_SCOPE_LIMIT"),
            ("catalog", "if (result->owned >= limit ||",
             "if (result->autoscan >= AGENT_FILE_AUTOSCAN_SCOPE_LIMIT ||\n"
             "\t    result->owned >= limit ||"),
            ("catalog", "static struct agent_file_meta agent_catalog_files",
             "/* RESOURCE_AGENT_CATALOG */\n"
             "static struct agent_file_meta agent_catalog_files"),
            ("catalog", "static int agent_catalog_admission(\n",
             "static int agent_catalog_pressure_admissible(void);\n"
             "static int agent_catalog_admission(\n"),
            ("vfs", "registry->free_count > 0",
             "registry->free_count >= 0"),
            ("vfs", "!workflow_lifecycle_key_valid(ref->lifecycle)",
             "workflow_lifecycle_key_valid(ref->lifecycle)"),
            ("vfs",
             "registry->active_count + registry->retiring_count <\n"
             "\t\t    VFS_SCOPE_MAX_ACTIVE",
             "registry->active_count < VFS_SCOPE_MAX_ACTIVE"),
            ("vfs",
             "registry->active_count + registry->retiring_count <\n"
             "\t\t    VFS_SCOPE_LIFECYCLE_CAP",
             "registry->active_count < VFS_SCOPE_LIFECYCLE_CAP"),
            ("vfs", "if (!ref->used ||\n\t\t    (!ref->retiring &&",
             "if (!ref->used || ref->retiring ||\n"
             "\t\t    (!ref->retiring &&"),
            ("vfs",
             "allocated = registry->active_count + registry->retiring_count;",
             "allocated = registry->active_count;"),
            ("vfs", "used = resource_account_usage(",
             "used = 0; /* resource_account_usage( */"),
            ("vfs", "if (used < guarantee)",
             "if (used == 0)"),
            ("vfs", "required += guarantee - used;",
             "required += guarantee;"),
            ("vfs", "if (allocated < VFS_SCOPE_MAX_ACTIVE)",
             "if (1)"),
            ("vfs", "(VFS_SCOPE_MAX_ACTIVE - allocated) * guarantee",
             "VFS_SCOPE_MAX_ACTIVE * guarantee"),
            ("catalog", "if (!growth || scope_id == VFS_SCOPE_SYSTEM)",
             "if (scope_id == VFS_SCOPE_SYSTEM)"),
            ("catalog", "if (!agent_catalog_scope_admissible(scope_id, &lifecycle))",
             "if (0)"),
            ("catalog", "AGENT_FILE_SYSTEM_LIMIT : AGENT_FILE_SCOPE_LIMIT",
             "AGENT_FILE_SYSTEM_LIMIT : AGENT_FILE_META_MAX"),
            ("catalog",
             "growth ? 0 : agent_catalog_files[edit->slot].flags",
             "0"),
            ("catalog",
             "agent_catalog_hard_admission(\n"
             "\t\t\t    previous_scope, slot, previous, &result)",
             "agent_catalog_admission(\n"
             "\t\t\t    previous_scope, slot, previous, 0, 0, 0)"),
            ("catalog", "agent_catalog_admission(scope_id, -1, 0, 0, flags, 1)",
             "agent_catalog_admission(scope_id, -1, 0, 0, flags, 0)"),
            ("catalog",
             "if (scope_id != VFS_SCOPE_SYSTEM &&\n"
             "\t    (flags & AGENT_FILE_META_F_AUTOSCAN) &&\n"
             "\t    !(old_flags & AGENT_FILE_META_F_AUTOSCAN)",
             "if (scope_id != VFS_SCOPE_SYSTEM &&\n"
             "\t    (flags & AGENT_FILE_META_F_PERSIST) &&\n"
             "\t    !(old_flags & AGENT_FILE_META_F_AUTOSCAN)"),
            ("catalog", "!(old_flags & AGENT_FILE_META_F_AUTOSCAN)",
             "(old_flags & AGENT_FILE_META_F_AUTOSCAN)"),
            ("catalog_h",
             "uint64 candidate_epoch, catalog_generation, lifecycle_generation;",
             "uint64 candidate_epoch, catalog_generation;"),
            ("catalog_h", "uint lifecycle_id;", "uint reserved;"),
            ("catalog", "memset(&key, 0, sizeof(key));",
             "key.records = 0;"),
            ("catalog", "key.lifecycle_id = lifecycle.id;",
             "key.lifecycle_id = 0;"),
            ("catalog", "key.lifecycle_generation = lifecycle.generation;",
             "key.lifecycle_generation = 0;"),
            ("catalog", "memcmp(&result->plan_key, &key, sizeof(key)) != 0",
             "memcmp(&result->plan_key, &result->plan_key, sizeof(key)) != 0"),
            ("catalog",
             "reload_one_scope &&\n\t    !agent_catalog_scope_admissible(reload_scope, &lifecycle)",
             "!agent_catalog_scope_admissible(reload_scope, &lifecycle)"),
            ("catalog",
             "result->plan_lifecycle_id != lifecycle.id ||",
             "result->plan_lifecycle_id == lifecycle.id ||"),
            ("catalog",
             "!agent_catalog_scope_admissible(reload_scope, &lifecycle) ||",
             "0 ||"),
            ("catalog",
             "if (reload_one_scope &&\n\t    (!agent_catalog_scope_admissible(reload_scope, &lifecycle)",
             "if (1 &&\n\t    (!agent_catalog_scope_admissible(reload_scope, &lifecycle)"),
            ("catalog", "agent_catalog_normalize_physical(edit->slot, edit->meta);",
             "edit->meta->physical_name[0] = 0;"),
            ("catalog", "strlen(meta->physical_name) > DIRSIZ",
             "strlen(meta->physical_name) >= DIRSIZ"),
            ("catalog", "strlen(meta->physical_name) <= DIRSIZ",
             "strlen(meta->physical_name) < DIRSIZ"),
            ("fs", "i < DIRSIZ && input[i] != 0",
             "i <= DIRSIZ && input[i] != 0"),
            ("fs", "input == 0 || out == 0 || input[0] == 0",
             "input == 0 || out == 0 || input[0] != 0"),
            ("fs",
             "if (fs_dirent_canonicalize(path, key) < 0)\n\t\treturn 0;",
             "if (0)\n\t\treturn 0;"),
            ("fs",
             "fs_dirent_canonicalize(name, key) < 0 || dp == 0 ||\n"
             "\t    dp->type != T_DIR || policy == 0",
             "dp == 0 || dp->type != T_DIR || policy == 0"),
            ("fs",
             "if (fs_dirent_canonicalize(path, key) < 0 || expected_dev == 0",
             "if (expected_dev == 0"),
            ("fs", "strncmp(key, de.name, DIRSIZ) == 0",
             "strncmp(name, de.name, DIRSIZ) == 0"),
            ("directory",
             "agent_metadata_scan_index_inode(ip, key, &failed)",
             "agent_metadata_scan_index_inode(ip, path, &failed)"),
            ("fs_program",
             "dirent_name_bound=14 legacy_alias=1 metadata_canonical=1",
             "dirent_name_bound=14 legacy_alias=0 metadata_canonical=1"),
            ("catalog", "~usage->fids[word]", "~0ULL"),
            ("catalog",
             "agent_catalog_first_bit(candidates) + 1;",
             "agent_catalog_first_bit(~candidates) + 1;"),
            ("catalog",
             "return agent_metadata_catalog_record_base_valid(\n"
             "\t\t       meta, record->scope_id, record->slot) &&",
             "return 1 &&"),
            ("catalog", "agent_catalog_require_txn();",
             "agent_meta_format_store_hash(0);\n\tagent_catalog_require_txn();"),
            ("store_format", "!agent_metadata_catalog_record_base_valid(",
             "!agent_meta_record_base_bypass("),
            ("store_format", "AGENT_FILE_SYSTEM_LIMIT : AGENT_FILE_SCOPE_LIMIT",
             "AGENT_FILE_META_MAX : AGENT_FILE_META_MAX"),
            ("store_format", "i * stride", "i * sizeof(struct agent_meta_record)"),
            ("store_format", "if (!legacy) {", "if (0) {"),
            ("store_format", "workflow_lifecycle_key_valid(lifecycle)",
             "lifecycle.id != 0"),
            ("store_format",
             "records_valid(store, sizeof(struct agent_meta_record), 0);",
             "records_valid(store, sizeof(struct agent_meta_record_v5), 0);"),
            ("store_format", "!legacy && record->meta.logical_path[0]",
             "0 && record->meta.logical_path[0]"),
            ("store_format",
             "return records_valid(store, sizeof(struct agent_meta_record_v5), 1);",
             "return records_valid(store, sizeof(struct agent_meta_record_v5), 0);"),
            ("fs", "\t\tfs_storage.workflow_inode_domain_limit;",
             "\t\tAGENT_FILE_SCOPE_LIMIT;"),
            ("storage_policy", "#define FS_WORKFLOW_INODE_MIN_PER_SCOPE 320U",
             "#define FS_WORKFLOW_INODE_MIN_PER_SCOPE 112U"),
            ("file_state_h", "#define AGENT_INODE_META_DEFERRED_SLOT (-1)",
             "#define AGENT_INODE_META_DEFERRED_SLOT 0"),
            ("file_state",
             "ip->agent_meta_slot == AGENT_INODE_META_DEFERRED_SLOT",
             "ip->agent_meta_slot == 0"),
            ("file_state", "!stale && ip->agent_meta_slot > 0",
             "ip->agent_meta_slot > 0"),
            ("file_state", "short version = slot ? AGENT_INODE_META_VERSION : 0;",
             "short version = AGENT_INODE_META_VERSION;"),
            ("file_state", "slot <= 0 && flags", "slot < 0 && flags"),
            ("directory", "agent_metadata_scan_index_inode(ip, key, &failed)",
             "0"),
            ("file_state", "if (iupdate(ip) >= 0)", "if (1)"),
            ("directory", "failed || agent_file_state_index_deferred(ip)",
             "failed"),
            ("directory", "rescan:\n\tagent_file_request_scan();",
             "rescan:\n\t;"),
            ("directory",
             "if (agent_file_state_index_deferred(ip)) {\n"
             "\t\tagent_file_request_scan();\n"
             "\t\treturn;\n\t}",
             "if (0) {\n\t\tagent_file_request_scan();\n"
             "\t\treturn;\n\t}"),
            ("directory",
             "if (agent_file_state_index_deferred(ip)) {\n"
             "\t\tagent_file_request_scan();\n"
             "\t\treturn;\n\t}\n"
             "\tif (!agent_metadata_txn_try_external()) {\n"
             "\t\tagent_file_request_scan();\n"
             "\t\treturn;\n\t}",
             "if (agent_file_state_index_deferred(ip)) {\n"
             "\t\tagent_file_request_scan();\n"
             "\t\treturn;\n\t}\n"
             "\tif (!agent_metadata_txn_try_external()) {\n"
             "\t\tagent_background_request();\n"
             "\t\treturn;\n\t}"),
            ("directory",
             "if (agent_metadata_catalog_clear_slot(slot) < 0)\n"
             "\t\tgoto rescan;",
             "if (0)\n\t\tgoto rescan;"),
            ("directory",
             "agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_ALL);\n"
             "\tagent_metadata_scan_slot_freed(scope_id);",
             "agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_ALL);"),
            ("scan_h", "void agent_metadata_scan_slot_freed(uint);",
             "void agent_metadata_scan_slot_freed(void);"),
            ("scan", "if (!scan_scope_full(scope, 0)) {",
             "if (0) {"),
            ("scan",
             "scan_ctl.pending = SCAN_URGENT;",
             "scan_ctl.pending = 1;"),
            ("scan", "if (scan_ctl.pending == SCAN_URGENT) {",
             "if (0) {"),
            ("scan",
             "if (scan_ctl.pending == SCAN_URGENT) {\n"
             "\t\tscan.start = 0;\n"
             "\t\tscan_ctl.pending = 1;",
             "if (scan_ctl.pending == SCAN_URGENT) {\n"
             "\t\tscan.start = 0;\n"
             "\t\tscan_ctl.pending = -1;"),
            ("scan", "if (!resume || scan_ctl.pending > 0)",
             "if (!resume)"),
            ("scan",
             "scan.removed++;\n\tagent_metadata_scan_slot_freed(scope);",
             "scan.removed++;"),
            ("file", "if (created)\n\t\tagent_fs_note_create(ip, path);",
             "if (0)\n\t\tagent_fs_note_create(ip, path);"),
            ("actions", "count >= AGENT_FILE_STATUS_BATCH_LIMIT",
             "count > AGENT_FILE_STATUS_BATCH_LIMIT"),
            ("actions", "if (primary_updated < 0)",
             "if (primary_updated == 0)"),
            ("actions", "if (selected_count < 0)",
             "if (selected_count == 0)"),
            ("objects", "if (updated < 0)",
             "if (updated == 0)"),
            ("catalog_h", "#define AGENT_CATALOG_NO_SPACE    -5",
             "#define AGENT_CATALOG_NO_SPACE    -1"),
            ("objects", "case AGENT_CATALOG_NO_SPACE:\n"
             "\t\treturn AGENT_STATUS_NO_SPACE;",
             "case AGENT_CATALOG_NO_SPACE:\n"
             "\t\treturn AGENT_STATUS_IO_ERROR;"),
            ("objects", "case AGENT_CATALOG_INTERRUPTED:\n"
             "\tcase AGENT_CATALOG_STALE:\n"
             "\t\treturn AGENT_STATUS_RETRY;",
             "case AGENT_CATALOG_INTERRUPTED:\n"
             "\tcase AGENT_CATALOG_STALE:\n"
             "\t\treturn AGENT_STATUS_IO_ERROR;"),
            ("objects", "result = agent_catalog_error_status(slot);",
             "result = AGENT_STATUS_IO_ERROR;"),
            ("objects", "result = agent_catalog_error_status(commit_status);",
             "result = AGENT_STATUS_IO_ERROR;"),
            ("scan", "*failed = SCAN_BIND_DEFERRED;",
             "*failed = SCAN_BIND_RETRY;"),
            ("scan", "uint marked[SCOPE_MAX * 2], nmarked;",
             "uint marked[1], nmarked;"),
            ("scan", "scan.nmarked = 0;", "scan.nmarked = NELEM(scan.marked);"),
            ("scan", "scope, AGENT_FILE_META_F_AUTOSCAN);",
             "scope, 0);"),
            ("scan", "(void)scan_scope_full(scope, 1);",
             "(void)scan_scope_full(scope, 0);"),
            ("scan",
             "if (slot < 0 || !(fid = agent_metadata_catalog_alloc_fid(scope))) {\n"
             "\t\t\t(void)scan_scope_full(scope, 1);",
             "if (slot < 0 || !(fid = agent_metadata_catalog_alloc_fid(scope))) {"),
            ("scan", "AGENT_INODE_META_DEFERRED_SLOT, 0, stale_sidecar",
             "AGENT_INODE_META_DEFERRED_SLOT, 0, 0"),
            ("scan", "ip->agent_meta_flags != persist",
             "ip->agent_meta_flags == persist"),
            ("scan",
             "if (agent_file_state_index_deferred(ip) &&\n"
             "\t    scan_scope_full(scope, 0)) {",
             "if (agent_file_state_index_deferred(ip) &&\n"
             "\t    scan_scope_full(scope, 1)) {"),
            ("scan", "scan.offset = off;\n\t\t\tscan_pause(1, 1);",
             "scan.offset = 0;\n\t\t\tscan_pause(1, 0);"),
            ("scan", "(void)scan_scope_failed(ip->vfs_scope_id, 1);",
             "scan.sweep_uncertain = 0;"),
            ("scan", "uchar seen[AGENT_META_STALE_BYTES]",
             "uchar seen[AGENT_FILE_META_MAX]"),
            ("scan", "info->file_scan_failures = scan.failures;",
             "info->file_scan_failures = 0;"),
            ("scan", 'SCAN_DEFAULT(summary, 0, "auto scanned root file")',
             'SCAN_DEFAULT(summary, AGENT_FILE_CHANGE_ALL, "")'),
            ("scan", "\t*failed = 0;", "\tif (failed)\n\t\t*failed = 0;"),
            ("scan", "safestrcpy(selector.physical_name, path,",
             "safestrcpy(selector.logical_path, path,"),
            ("scan", "agent_metadata_catalog_borrow_scan(slot, &view)",
             "agent_metadata_catalog_borrow(0, slot, &view)"),
            ("scan", "safestrcpy(selector.logical_path, path,",
             "safestrcpy(selector.physical_name, path,"),
            ("scan", "selector.incarnation = ip->vfs_incarnation;",
             "selector.incarnation = 0;"),
            ("catalog_h",
             "AGENT_CATALOG_KEY_PHYSICAL | AGENT_CATALOG_KEY_LOGICAL",
             "AGENT_CATALOG_KEY_PHYSICAL & AGENT_CATALOG_KEY_LOGICAL"),
            ("catalog", "result->slot = AGENT_CATALOG_CONFLICT;",
             "result->slot = i;"),
            ("scan", "slot = ip->agent_meta_slot - 1;", "slot = 0;"),
            ("scan", "agent_metadata_catalog_borrow_scan(slot, &view)",
             "agent_metadata_catalog_borrow_scan(ip->agent_meta_slot - 1, &view)"),
            ("scan", "resolution.slot == AGENT_CATALOG_CONFLICT",
             "resolution.slot >= 0"),
            ("scan", "(resolution.matched & AGENT_CATALOG_KEY_PATH) == 0",
             "(resolution.matched & AGENT_CATALOG_KEY_PATH) != 0"),
            ("scan", "agent_metadata_catalog_identity_state(view.meta) < 0",
             "agent_metadata_catalog_identity_state(view.meta) == 0"),
            ("scan", "strncmp(view.meta->logical_path, path,",
             "strncmp(view.meta->physical_name, path,"),
            ("scan", "resolution.slot : -1;", "-1 : resolution.slot;"),
            ("scan", "!scan_matches(view.meta, ip)) {",
             "!scan_matches(view.meta, ip) &&\n"
             "\t    !(view.meta->flags & AGENT_FILE_META_F_AUTOSCAN)) {"),
            ("catalog", "candidate_epoch == 0 ||",
             "namei_scope_status(\"candidate\", 0, 0, 0); candidate_epoch == 0 ||"),
            ("catalog", "agent_catalog_plan_count(result, record->scope_id) < 0",
             "agent_catalog_plan_count(result, record->scope_id, "
             "record->meta.flags) < 0"),
            ("catalog",
             "if (++result->plan_ordinary_count > AGENT_FILE_ORDINARY_LIMIT ||",
             "if (result->plan_scope_counts[scope_index] >=\n"
             "\t    AGENT_FILE_AUTOSCAN_SCOPE_LIMIT)\n"
             "\t\treturn -1;\n"
             "\tif (++result->plan_ordinary_count > AGENT_FILE_ORDINARY_LIMIT ||"),
            ("scope_program",
             "check(unlink(released_name) == 0, \"release autoscan catalog slot\");",
             "check(1, \"release autoscan catalog slot\");"),
            ("scope_program", "info.file_scan_deferred > deferred_before",
             "info.file_scan_deferred >= deferred_before"),
            ("scope_program",
             "check(agent_file_meta_init() == 0,\n"
             "\t      \"reload rebuilt autoscan catalog checkpoint\");",
             "check(1, \"reload rebuilt autoscan catalog checkpoint\");"),
            ("scope_program",
             "remove_quota_files_from('a', state->first_removed, state->created)",
             "remove_quota_files_from('a', 0, state->created)"),
            ("catalog", "agent_metadata_txn_projection_begin();\n\tif (reload_one_scope)",
             "agent_metadata_txn_projection_begin();\n\treturn AGENT_METADATA_LOAD_IO;\n\tif (reload_one_scope)"),
            ("catalog", "key.candidate_epoch = candidate_epoch;",
             "key.candidate_epoch = 0;"),
            ("catalog", "result->plan_catalog_generation != agent_catalog_generation",
             "result->plan_catalog_generation == agent_catalog_generation"),
            ("catalog", "reload_one_scope ? count : AGENT_CATALOG_PREPARE_STEP",
             "AGENT_CATALOG_PREPARE_STEP"),
            ("catalog", "if (identity == 0)\n\t\t\t\tagent_catalog_bit_set(result->missing_slots,",
             "if (identity == 0)\n\t\t\t\tagent_catalog_bit_set(result->selected_slots,"),
            ("catalog", "panic(\"Agent catalog apply binding invariant\");",
             "return AGENT_METADATA_LOAD_CORRUPT;"),
            ("catalog", "if (result > 0 && view->state != 0)",
             "if (result > 0 && view->state == 0)"),
            ("store", "struct agent_metadata_apply_result *apply =\n\t\t&agent_meta_workspace.load.result;\n\tstruct agent_meta_record *apply_plan = 0;",
             "struct agent_metadata_apply_result apply_local;\n\tstruct agent_metadata_apply_result *apply = &apply_local;\n\tstruct agent_meta_record *apply_plan = 0;"),
            ("store", "if (result != AGENT_METADATA_LOAD_PROGRESS)\n\t\tagent_meta_store_apply_abort();",
             "agent_meta_store_apply_abort();"),
            ("objects", "return agent_metadata_catalog_reconcile_pending() ?",
             "return 0 ?"),
            ("objects", "return agent_metadata_catalog_reconcile_pending() ?",
             "agent_metadata_catalog_find_scan (1, \"x\", 1, 1, 1);\n\treturn agent_metadata_catalog_reconcile_pending() ?"),
            ("objects", "return agent_metadata_catalog_reconcile_pending() ?",
             "agent_metadata_catalog_borrow_scan (0, 0);\n\treturn agent_metadata_catalog_reconcile_pending() ?"),
            ("objects", "return agent_metadata_catalog_reconcile_pending() ?",
             "agent_metadata_catalog_edit_begin_scan (0, 1, 0);\n\treturn agent_metadata_catalog_reconcile_pending() ?"),
            ("scan", "scope = ip->vfs_scope_id;",
             "scope = ip->vfs_scope_id;\n\t(void)agent_metadata_catalog_find_scan "
             "(scope, \"x\", 1, 1, 1);"),
            ("objects",
             "agent_metadata_catalog_resolve(scope_id, &meta, -1, &selector);",
             "memset(&selector, 0, sizeof(selector));"),
            ("scan", "agent_metadata_catalog_reconcile_slot(slot)",
             "agent_metadata_catalog_reconcile_slot(-1)"),
            ("catalog",
             "agent_catalog_slot_publish(\n"
             "\t\tslot, &agent_catalog_files[slot], scope_id, 0, lifecycle,",
             "agent_catalog_states[slot] = 0;\n"
             "\t(void)(slot, scope_id, lifecycle,"),
            ("scan", "if (view.state & AGENT_CATALOG_STATE_QUARANTINE)\n\t\t\t\tcontinue;",
             "if (0)\n\t\t\t\tcontinue;"),
            ("objects", "agent_metadata_store_take_reconcile_request())\n\t\tagent_file_request_scan();",
             "agent_metadata_store_take_reconcile_request())\n\t\tagent_background_request();"),
            ("store",
             "\tagent_meta_reconcile_required = 1;\n"
             "\tagent_background_request();",
             "\tif (apply->used != 0) {\n"
             "\t\tagent_meta_reconcile_required = 1;\n"
             "\t\tagent_background_request();\n"
             "\t}"),
        )
        for name, old, new in cases:
            with self.subTest(name=name, mutation=old):
                altered = mutate(self.sources, name, old, new)
                with self.assertRaises(CONTRACT.ContractError):
                    CONTRACT.validate_sources(altered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
