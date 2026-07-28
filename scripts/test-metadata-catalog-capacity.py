#!/usr/bin/env python3
"""Mutation and arithmetic tests for catalog capacity/fair scan contracts."""

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


def mutate(sources: dict[str, str], name: str, old: str, new: str) -> dict[str, str]:
    changed = dict(sources)
    if old not in changed[name]:
        raise AssertionError(f"mutation anchor is missing: {name}: {old}")
    changed[name] = changed[name].replace(old, new, 1)
    return changed


def workflow_admissible(usages: list[tuple[str, int]]) -> bool:
    active = sum(1 for state, _ in usages if state == "active")
    used = sum(value for state, value in usages if state != "preserved")
    deficit = sum(max(0, 16 - value) for state, value in usages
                  if state == "active")
    return used + deficit + (4 - active) * 16 <= 448


def workflow_inode_allocation_allowed(
    usages: list[tuple[str, str, int]], target: str
) -> bool:
    active = sum(1 for _, state, _ in usages if state == "active")
    used = sum(value for _, state, value in usages if state != "preserved")
    reserve = sum(max(0, 16 - value) for name, state, value in usages
                  if state == "active" and name != target)
    reserve += (4 - active) * 16
    return used < 448 and reserve < 448 - used


def catalog_slot_allowed(system: bool, owned: int, ordinary: int) -> bool:
    if system:
        return owned < 64 and owned + ordinary < 512
    return owned < 112 and ordinary < 448


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

    def test_active_retiring_and_system_reserve_model(self) -> None:
        self.assertFalse(workflow_admissible([("retiring", 112)] * 4))
        self.assertTrue(workflow_admissible([("retiring", 112)] * 3))
        self.assertTrue(workflow_admissible([("active", 16)] * 4))
        self.assertFalse(workflow_admissible(
            [("active", 112)] * 3 + [("retiring", 112)]))
        self.assertTrue(workflow_admissible([("preserved", 112)] * 4))
        self.assertFalse(catalog_slot_allowed(False, 111, 448))
        self.assertTrue(catalog_slot_allowed(True, 63, 448))
        self.assertFalse(catalog_slot_allowed(True, 64, 448))

    def test_fid_bitmap_covers_the_inclusive_boundary(self) -> None:
        bitmap = bytearray((512 + 7) // 8)
        for fid in (1, 8, 9, 511, 512):
            bitmap[(fid - 1) // 8] |= 1 << ((fid - 1) % 8)
        for fid in (1, 8, 9, 511, 512):
            self.assertTrue(bitmap[(fid - 1) // 8] &
                            (1 << ((fid - 1) % 8)))

    def test_allocation_preserves_guarantees_until_lifecycle_refund(self) -> None:
        pressure = [(f"old-{i}", "retiring", 112) for i in range(3)]
        self.assertTrue(workflow_inode_allocation_allowed(
            pressure + [("target", "active", 63)], "target"))
        self.assertFalse(workflow_inode_allocation_allowed(
            pressure + [("target", "active", 64)], "target"))
        self.assertTrue(workflow_inode_allocation_allowed(
            pressure[1:] + [("target", "active", 64)], "target"))
        self.assertTrue(workflow_inode_allocation_allowed(
            pressure + [("archive", "preserved", 112),
                        ("target", "active", 63)], "target"))

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
        self.assertFalse(retained_scope_plan_allowed([3] * 113))

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
            ("catalog", "result.ordinary < AGENT_FILE_ORDINARY_LIMIT",
             "result.ordinary < AGENT_FILE_META_MAX"),
            ("catalog", "edit->scope_id, edit->slot, edit->meta)",
             "edit->scope_id, -1, edit->meta)"),
            ("catalog", "agent_catalog_normalize_physical(edit->slot, edit->meta);",
             "edit->meta->physical_name[0] = 0;"),
            ("catalog", "strlen(meta->physical_name) > DIRSIZ",
             "strlen(meta->physical_name) >= DIRSIZ"),
            ("catalog", "strlen(meta->physical_name) <= DIRSIZ",
             "strlen(meta->physical_name) < DIRSIZ"),
            ("fs", "strlen(path) > DIRSIZ",
             "strlen(path) >= DIRSIZ"),
            ("catalog", "uchar used_fids[(AGENT_FILE_META_MAX + 7) / 8]",
             "int used_fids[AGENT_FILE_META_MAX]"),
            ("catalog", "used_fids[(fid - 1) / 8]",
             "used_fids[fid / 8]"),
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
            ("fs", "fs_storage.workflow_inode_domain_limit < AGENT_FILE_SCOPE_LIMIT ?",
             "fs_storage.workflow_inode_domain_limit < AGENT_FILE_META_MAX ?"),
            ("fs", "metadata_reserve >=\n\t\t\t    AGENT_FILE_ORDINARY_LIMIT - metadata_used",
             "metadata_reserve >\n\t\t\t    AGENT_FILE_ORDINARY_LIMIT - metadata_used"),
            ("vfs", "if (!ref->used ||\n\t\t    (!ref->retiring &&",
             "if (!ref->used || ref->retiring ||\n\t\t    (!ref->retiring &&"),
            ("scan", "*failed = SCAN_BIND_DEFERRED;",
             "*failed = SCAN_BIND_RETRY;"),
            ("scan", "scan.offset = off;\n\t\t\tscan_pause(1, 1);",
             "scan.offset = 0;\n\t\t\tscan_pause(1, 0);"),
            ("scan", "(void)scan_scope_failed(ip->vfs_scope_id, 1);",
             "scan.sweep_uncertain = 0;"),
            ("scan", "uchar seen[AGENT_FILE_META_MAX]",
             "int seen[AGENT_FILE_META_MAX]"),
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
             "agent_catalog_state_clear(slot);\n"
             "\tagent_catalog_changed(AGENT_FILE_CHANGE_MEMBERSHIP);",
             "agent_catalog_states[slot] = 0;\n"
             "\tagent_catalog_changed(AGENT_FILE_CHANGE_MEMBERSHIP);"),
            ("scan", "if (view.state & AGENT_CATALOG_STATE_QUARANTINE)\n\t\t\t\tcontinue;",
             "if (0)\n\t\t\t\tcontinue;"),
            ("objects", "agent_metadata_store_take_reconcile_request())\n\t\tagent_file_request_scan();",
             "agent_metadata_store_take_reconcile_request())\n\t\tagent_background_request();"),
        )
        for name, old, new in cases:
            with self.subTest(name=name, mutation=old):
                altered = mutate(self.sources, name, old, new)
                with self.assertRaises(CONTRACT.ContractError):
                    CONTRACT.validate_sources(altered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
