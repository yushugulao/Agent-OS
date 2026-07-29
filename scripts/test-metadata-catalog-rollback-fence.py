#!/usr/bin/env python3
"""Model and mutation tests for catalog rollback fencing."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "metadata_catalog_rollback_fence_contract",
    ROOT / "scripts/check-metadata-catalog-rollback-fence.py",
)
assert SPEC and SPEC.loader
CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT)


def mutate(sources: dict[str, str], name: str,
           old: str, new: str) -> dict[str, str]:
    changed = dict(sources)
    if old not in changed[name]:
        raise AssertionError(f"mutation anchor missing: {name}: {old}")
    changed[name] = changed[name].replace(old, new, 1)
    return changed


def record(fid: int, path: str, scope: int = 7,
           state: str = "ready") -> dict[str, object]:
    return {"fid": fid, "path": path, "logical": path,
            "scope": scope, "state": state, "identity": (1, fid, 1)}


class CatalogModel:
    def __init__(self) -> None:
        self.records: list[dict[str, object] | None] = [None, None, None]
        self.generation = 1
        self.owner: str | None = None
        self.sequence = 0

    def begin(self, owner: str) -> int | None:
        if self.owner is not None:
            return None
        self.sequence += 1
        self.owner = owner
        return self.sequence

    def end(self, owner: str, fence: int) -> bool:
        if self.owner != owner or fence != self.sequence:
            return False
        self.owner = None
        return True

    def read(self, slot: int) -> dict[str, object] | None:
        return copy.deepcopy(self.records[slot])

    def write(self, owner: str, slot: int,
              value: dict[str, object] | None) -> bool:
        if self.owner not in (None, owner):
            return False
        self.records[slot] = copy.deepcopy(value)
        self.generation += 1
        return True

    @staticmethod
    def binding(fence: int, generation: int, slot: int,
                value: dict[str, object] | None) -> tuple[object, ...]:
        if value is None:
            payload: tuple[object, ...] = ()
        else:
            payload = tuple(sorted(value.items()))
        return fence, generation, slot, payload

    def capture(self, owner: str, fence: int,
                slot: int) -> tuple[object, ...] | None:
        if self.owner != owner or fence != self.sequence:
            return None
        return self.binding(fence, self.generation, slot,
                            self.records[slot])

    def restore(self, owner: str, fence: int, slot: int,
                token: tuple[object, ...],
                previous: dict[str, object] | None) -> bool:
        if self.owner != owner or fence != self.sequence:
            return False
        saved_generation = int(token[1])
        if token != self.binding(fence, saved_generation, slot,
                                 self.records[slot]):
            return False
        if previous is not None:
            for index, current in enumerate(self.records):
                if index == slot or current is None:
                    continue
                if current["scope"] != previous["scope"]:
                    continue
                if any(current[key] == previous[key]
                       for key in ("fid", "path", "logical", "identity")):
                    return False
        self.records[slot] = copy.deepcopy(previous)
        self.generation += 1
        return True


class CreatedRollbackModel:
    def __init__(self, path: str, identity: tuple[int, int, int]) -> None:
        self.path = path
        self.identity = identity
        self.fail_closed = False

    def rollback(self, receipt: tuple[str, tuple[int, int, int]],
                 cleanup_ok: bool = True, active_refs: int = 1) -> str:
        if (not cleanup_ok or active_refs != 1 or
                receipt != (self.path, self.identity)):
            self.fail_closed = True
            return "indeterminate"
        self.path = ""
        self.identity = (0, 0, 0)
        return "rolled_back"


class NamespaceCreateModel:
    def __init__(self) -> None:
        self.inode_preserved = True

    def finish(self, dirent_written: bool, barrier_ok: bool) -> str:
        if not dirent_written:
            self.inode_preserved = False
            return "reverted_error"
        if barrier_ok:
            return "created"
        return "indeterminate"


class PersistCompletionModel:
    @staticmethod
    def device_error(result: str) -> tuple[str, bool, str]:
        if result == "fs_indeterminate":
            return "fail_closed", True, "agent_indeterminate"
        return "io", False, "agent_io_error"


class FixedPartitionModel:
    SCOPE_LIMIT = 112
    ACTIVE_SLOTS = 4

    @classmethod
    def can_grow(cls, owned: int, state: str) -> bool:
        return state in ("active", "closing") and owned < cls.SCOPE_LIMIT

    @classmethod
    def can_create_scope(cls, active: int, closing: int, retiring: int) -> bool:
        return active + closing + retiring < cls.ACTIVE_SLOTS


class ScopedReloadModel:
    def __init__(self, lifecycle_id: int, generation: int,
                 catalog_generation: int) -> None:
        self.plan = (lifecycle_id, generation, catalog_generation)

    def apply(self, lifecycle_id: int, generation: int,
              catalog_generation: int, state: str) -> str:
        if state not in ("active", "closing"):
            return "interrupted"
        if self.plan != (lifecycle_id, generation, catalog_generation):
            return "interrupted"
        return "ok"


class CatalogRollbackFenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = CONTRACT.load_sources(ROOT)

    def test_current_tree_passes(self) -> None:
        CONTRACT.validate_sources(self.sources)

    def test_foreign_writer_is_retried_and_owner_restores(self) -> None:
        catalog = CatalogModel()
        before = record(1, "before")
        after = record(2, "after")
        catalog.records[0] = before
        fence = catalog.begin("A")
        self.assertIsNotNone(fence)
        assert fence is not None
        self.assertTrue(catalog.write("A", 0, after))
        token = catalog.capture("A", fence, 0)
        self.assertIsNotNone(token)
        self.assertFalse(catalog.write("B", 1, record(3, "foreign")))
        self.assertEqual(catalog.read(0), after)
        assert token is not None
        self.assertTrue(catalog.restore("A", fence, 0, token, before))
        self.assertEqual(catalog.read(0), before)
        self.assertTrue(catalog.end("A", fence))
        self.assertTrue(catalog.write("B", 1, record(3, "foreign")))

    def test_reads_remain_available_during_fence(self) -> None:
        catalog = CatalogModel()
        catalog.records[0] = record(1, "visible")
        fence = catalog.begin("A")
        self.assertEqual(catalog.read(0), record(1, "visible"))
        assert fence is not None
        self.assertFalse(catalog.end("B", fence))
        self.assertTrue(catalog.end("A", fence))

    def test_post_state_binding_preserves_reused_slot(self) -> None:
        catalog = CatalogModel()
        before = record(1, "before")
        after = record(2, "after")
        replacement = record(3, "replacement")
        catalog.records[0] = before
        fence = catalog.begin("A")
        assert fence is not None
        self.assertTrue(catalog.write("A", 0, after))
        token = catalog.capture("A", fence, 0)
        assert token is not None
        # Simulate a missing guard to exercise the independent undo identity.
        catalog.records[0] = replacement
        catalog.generation += 1
        self.assertFalse(catalog.restore("A", fence, 0, token, before))
        self.assertEqual(catalog.read(0), replacement)

    def test_restore_rechecks_central_duplicate_admission(self) -> None:
        catalog = CatalogModel()
        before = record(1, "before")
        after = record(2, "after")
        catalog.records[0] = before
        fence = catalog.begin("A")
        assert fence is not None
        self.assertTrue(catalog.write("A", 0, after))
        token = catalog.capture("A", fence, 0)
        assert token is not None
        # Again bypass the guard: the slot token remains valid, but admission
        # must preserve the foreign owner of the old unique keys.
        catalog.records[1] = before
        catalog.generation += 1
        self.assertFalse(catalog.restore("A", fence, 0, token, before))
        self.assertEqual(catalog.read(0), after)
        self.assertEqual(catalog.read(1), before)

    def test_unrelated_generation_is_not_a_global_failure_switch(self) -> None:
        catalog = CatalogModel()
        before = record(1, "before")
        after = record(2, "after")
        catalog.records[0] = before
        fence = catalog.begin("A")
        assert fence is not None
        self.assertTrue(catalog.write("A", 0, after))
        token = catalog.capture("A", fence, 0)
        assert token is not None
        # A defensive model of an unrelated mutation: it advances the global
        # diagnostic generation but must not veto an exact, conflict-free undo.
        catalog.records[2] = record(9, "unrelated", scope=8)
        catalog.generation += 1
        self.assertTrue(catalog.restore("A", fence, 0, token, before))
        self.assertEqual(catalog.read(2), record(9, "unrelated", scope=8))

    def test_created_inode_exact_receipt_removes_only_its_identity(self) -> None:
        identity = (1, 44, 9)
        model = CreatedRollbackModel("auto", identity)
        self.assertEqual(model.rollback(("auto", identity)), "rolled_back")
        self.assertEqual(model.path, "")
        self.assertFalse(model.fail_closed)

    def test_created_inode_replacement_race_is_preserved_and_indeterminate(self) -> None:
        receipt = ("auto", (1, 44, 9))
        replacement = (1, 45, 10)
        model = CreatedRollbackModel("auto", replacement)
        self.assertEqual(model.rollback(receipt), "indeterminate")
        self.assertEqual(model.identity, replacement)
        self.assertTrue(model.fail_closed)

    def test_created_inode_cleanup_failure_is_indeterminate(self) -> None:
        identity = (1, 44, 9)
        model = CreatedRollbackModel("auto", identity)
        self.assertEqual(
            model.rollback(("auto", identity), cleanup_ok=False),
            "indeterminate",
        )
        self.assertEqual(model.identity, identity)
        self.assertTrue(model.fail_closed)

    def test_bind_local_reference_must_be_released_before_cleanup(self) -> None:
        identity = (1, 44, 9)
        held = CreatedRollbackModel("auto", identity)
        self.assertEqual(
            held.rollback(("auto", identity), active_refs=2),
            "indeterminate",
        )
        released = CreatedRollbackModel("auto", identity)
        self.assertEqual(
            released.rollback(("auto", identity), active_refs=1),
            "rolled_back",
        )

    def test_post_dirent_barrier_failure_preserves_inode_and_is_indeterminate(self) -> None:
        model = NamespaceCreateModel()
        self.assertEqual(model.finish(True, False), "indeterminate")
        self.assertTrue(model.inode_preserved)

    def test_store_create_indeterminate_is_irrevocable_to_syscall(self) -> None:
        cause, irrevocable, status = PersistCompletionModel.device_error(
            "fs_indeterminate"
        )
        self.assertEqual(cause, "fail_closed")
        self.assertTrue(irrevocable)
        self.assertEqual(status, "agent_indeterminate")

    def test_fixed_partition_holds_retiring_identity_until_reclaim(self) -> None:
        self.assertTrue(FixedPartitionModel.can_grow(111, "active"))
        self.assertFalse(FixedPartitionModel.can_grow(112, "active"))
        self.assertFalse(FixedPartitionModel.can_grow(0, "retiring"))
        self.assertFalse(FixedPartitionModel.can_create_scope(3, 0, 1))
        self.assertTrue(FixedPartitionModel.can_create_scope(3, 0, 0))

    def test_scoped_reload_revalidates_immutable_lifecycle(self) -> None:
        plan = ScopedReloadModel(3, 9, 41)
        self.assertEqual(plan.apply(3, 9, 41, "active"), "ok")
        self.assertEqual(plan.apply(3, 9, 41, "closing"), "ok")
        self.assertEqual(plan.apply(3, 10, 41, "active"), "interrupted")
        self.assertEqual(plan.apply(4, 9, 41, "active"), "interrupted")
        self.assertEqual(plan.apply(3, 9, 42, "active"), "interrupted")
        self.assertEqual(plan.apply(3, 9, 41, "retiring"), "interrupted")

    def test_batch_locked_transaction_contract_mutations_are_rejected(self) -> None:
        altered = mutate(
            self.sources,
            "objects",
            'agent_metadata_txn_require_owned(1, '
            '"Agent metadata action transaction");',
            'agent_metadata_txn_require_owned(0, '
            '"Agent metadata action transaction");',
        )
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.validate_sources(altered)

        altered = mutate(
            self.sources,
            "objects",
            "if (!agent_metadata_store_submit_wait_locked()) {",
            "if (0) {",
        )
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.validate_sources(altered)

        submit_block = (
            "if (!agent_metadata_store_submit_wait_locked()) {\n"
            "\t\tpersist->durable = 0;\n"
            "\t\tpersist->status = -1;\n"
            "\t\tpersist->cause = AGENT_METADATA_PERSIST_FAIL_CLOSED;\n"
            "\t\treturn 0;\n"
            "\t}"
        )
        load_block = (
            "load_status = agent_file_store_load();\n"
            "\tif (load_status < 0)\n"
            "\t\treturn agent_metadata_load_agent_status(load_status);"
        )
        altered = mutate(
            self.sources,
            "objects",
            submit_block + "\n\t" + load_block,
            load_block + "\n\t" + submit_block,
        )
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.validate_sources(altered)

        altered = mutate(
            self.sources,
            "objects",
            'agent_metadata_txn_require_owned(1, '
            '"Agent metadata action transaction");',
            "if (!agent_metadata_txn_lock(1))\n\t\treturn 0;",
        )
        altered = mutate(
            altered,
            "objects",
            "\treturn updated;\n}\nstatic void agent_object_state_update",
            "\tagent_metadata_txn_unlock();\n\treturn updated;\n}"
            "\nstatic void agent_object_state_update",
        )
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.validate_sources(altered)

        for old, new in (
            ("persist->cause = AGENT_METADATA_PERSIST_FAIL_CLOSED;\n"
             "\t\treturn 0;",
             "persist->cause = AGENT_METADATA_PERSIST_RETRY;\n"
             "\t\treturn 0;"),
            ("persist->cause = AGENT_METADATA_PERSIST_RETRY;\n"
             "\t\treturn 0;",
             "persist->cause = AGENT_METADATA_PERSIST_FAIL_CLOSED;\n"
             "\t\treturn 0;"),
            ("persist->irrevocable = 1;", "persist->irrevocable = 0;"),
        ):
            altered = mutate(self.sources, "objects", old, new)
            with self.assertRaises(CONTRACT.ContractError):
                CONTRACT.validate_sources(altered)

        altered = mutate(
            self.sources,
            "actions",
            "memset(primary, 0, sizeof(primary));\n\tprimary_updated =",
            "memset(primary, 0, sizeof(primary));\n"
            "\tif (!agent_metadata_store_submit_wait_locked())\n"
            "\t\treturn 0;\n\tprimary_updated =",
        )
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.validate_sources(altered)

    def test_guard_mutations_are_rejected(self) -> None:
        cases = (
            ("catalog",
             "if (!agent_catalog_mutation_allowed())\n\t\treturn AGENT_CATALOG_CONFLICT;\n\tif (edit == 0",
             "if (0)\n\t\treturn AGENT_CATALOG_CONFLICT;\n\tif (edit == 0"),
            ("catalog",
             "int agent_metadata_catalog_clear_slot(int slot) {\n\tint was_used;\n\tuint scope_id;\n\tagent_catalog_require_txn();\n\tif (!agent_catalog_mutation_allowed())",
             "int agent_metadata_catalog_clear_slot(int slot) {\n\tint was_used;\n\tuint scope_id;\n\tagent_catalog_require_txn();\n\tif (0)"),
            ("catalog",
             "if (!agent_catalog_mutation_allowed())\n\t\treturn AGENT_METADATA_LOAD_INTERRUPTED;",
             "if (0)\n\t\treturn AGENT_METADATA_LOAD_INTERRUPTED;"),
            ("catalog",
             "agent_catalog_active_edit != 0 || !agent_catalog_mutation_allowed()",
             "agent_catalog_active_edit != 0"),
            ("catalog",
             "if (agent_catalog_hard_admission(\n"
             "\t\t\t    previous_scope, slot, previous, &result) <= 0)",
             "if (0)"),
            ("objects",
             "if (agent_metadata_catalog_mutation_begin(&mutation_fence) < 0)",
             "if (0)"),
            ("objects",
             "agent_metadata_catalog_mutation_end(&mutation_fence) < 0",
             "0"),
        )
        for name, old, new in cases:
            with self.subTest(name=name, anchor=old):
                altered = mutate(self.sources, name, old, new)
                with self.assertRaises(CONTRACT.ContractError):
                    CONTRACT.validate_sources(altered)

    def test_fixed_partition_and_lifecycle_mutations_are_rejected(self) -> None:
        cases = (
            ("agent_h",
             "#define AGENT_FILE_SCOPE_LIMIT    112",
             "#define AGENT_FILE_SCOPE_LIMIT    400"),
            ("catalog",
             "++result->plan_scope_counts[scope_index] > "
             "AGENT_FILE_SCOPE_LIMIT",
             "++result->plan_scope_counts[scope_index] > "
             "AGENT_FILE_ORDINARY_LIMIT"),
            ("vfs",
             "allocated + retiring < VFS_SCOPE_MAX_ACTIVE",
             "allocated < VFS_SCOPE_MAX_ACTIVE"),
            ("catalog",
             "workflow_lifecycle_active(*lifecycle) ||\n"
             "\t\tworkflow_lifecycle_closing(*lifecycle)",
             "workflow_lifecycle_active(*lifecycle)"),
            ("catalog",
             "memcmp(&result->plan_key, &key, sizeof(key)) != 0",
             "memcmp(&result->plan_key, &result->plan_key, sizeof(key)) != 0"),
            ("catalog",
             "\tif (reload_one_scope &&\n"
             "\t    !agent_catalog_scope_admissible(reload_scope, &lifecycle))",
             "\tif (0)"),
            ("catalog",
             "\t    (result->plan_lifecycle_id != lifecycle.id ||\n"
             "\t     result->plan_lifecycle_generation != lifecycle.generation))",
             "\t    (0)"),
            ("catalog",
             "\t    (!agent_catalog_scope_admissible(reload_scope, &lifecycle) ||\n"
             "\t     result->plan_lifecycle_id != lifecycle.id ||",
             "\t    (0 ||\n"
             "\t     result->plan_lifecycle_id != lifecycle.id ||"),
        )
        for name, old, new in cases:
            with self.subTest(name=name, anchor=old):
                altered = mutate(self.sources, name, old, new)
                with self.assertRaises(CONTRACT.ContractError):
                    CONTRACT.validate_sources(altered)

    def test_creation_receipt_and_summary_mutations_are_rejected(self) -> None:
        cases = (
            ("catalog",
             "hash = agent_catalog_hash_bytes(hash, &undo->reserved,",
             "hash = agent_catalog_hash_bytes(hash, &undo->slot,"),
            ("catalog",
             "(undo->reserved & ~AGENT_CATALOG_UNDO_CREATED) != 0",
             "undo->reserved != 0"),
            ("catalog",
             "undo->reserved = AGENT_CATALOG_UNDO_CREATED;",
             "undo->reserved = 0;"),
            ("catalog",
             "return create;",
             "return 0;"),
            ("catalog",
             "out:\n\tif (create) {",
             "out:\n\tif (0 && create) {"),
            ("catalog",
             "if (lookup_status)\n\t\t\t*lookup_status = FS_LOOKUP_ABSENT;",
             "iput(ip);\n\t\tif (lookup_status)\n\t\t\t*lookup_status = FS_LOOKUP_ABSENT;"),
            ("catalog",
             "return create == FS_CREATE_INDETERMINATE ?\n\t\t\tAGENT_CATALOG_INDETERMINATE : -1;",
             "return -1;"),
            ("catalog",
             "else if (ip == 0 && status)\n\t\t*status = lookup_status;",
             "else if (ip == 0 && status)\n\t\t*status = FS_LOOKUP_ABSENT;"),
            ("catalog",
             "agent_catalog_files[slot].incarnation,\n\t\tagent_catalog_scopes[slot]) < 0)\n\t\treturn -1;",
             "agent_catalog_files[slot].incarnation,\n\t\tagent_catalog_scopes[slot]) >= 0)\n\t\treturn -1;"),
            ("fs",
             "ip->vfs_incarnation != expected_incarnation || ip->type != T_FILE",
             "ip->type != T_FILE"),
            ("fs",
             "ip->agent_meta_version != 0 || !agent_edit_unlink_allowed(ip))",
             "ip->agent_meta_version != 0)"),
            ("fs",
             "dirunlink(dp, key, offset, expected_inum, expected_incarnation,",
             "dirunlink(dp, key, offset, ip->inum, ip->vfs_incarnation,"),
            ("fs",
             "if (fs_dirent_canonicalize(path, key) < 0 || expected_dev == 0 ||",
             "if (expected_dev == 0 ||"),
            ("fs",
             "status = fs_put_removed_checked(ip);",
             "iput(ip);\n\tstatus = 0;"),
            ("fs",
             "return itruncate_reclaim(&reclaim);",
             "(void)itruncate_reclaim(&reclaim);\n\treturn 0;"),
            ("fs",
             "iput(ip);\n\t\treturn -1;\n\t}\n\tdetached = inode_remove_detach",
             "iput(ip);\n\t\treturn 0;\n\t}\n\tdetached = inode_remove_detach"),
            ("fs",
             "result = fs_durable_barrier_forward();\n\tif (result < 0)\n\t\treturn FS_LOOKUP_INDETERMINATE;",
             "result = fs_durable_barrier_forward();\n\tif (result < 0)\n\t\treturn result;"),
            ("fs",
             "if (result != sizeof(de))\n\t\treturn fs_io_health == FS_IO_INDETERMINATE ?",
             "if (result != sizeof(de))\n\t\treturn result < 0 ? result : -1;\n\tif (0)"),
            ("fs",
             "if (result == FS_LOOKUP_INDETERMINATE)\n\t\tiput(ip);",
             "if (0)\n\t\tiput(ip);"),
            ("fs",
             "if (created && result == FS_LOOKUP_INDETERMINATE)\n\t\t*created = FS_CREATE_INDETERMINATE;",
             "if (created && result == FS_LOOKUP_INDETERMINATE)\n\t\t*created = 0;"),
            ("fs",
             "return fs_io_health == FS_IO_INDETERMINATE ?\n\t\tFS_LOOKUP_INDETERMINATE :",
             "return 0 ?\n\t\tFS_LOOKUP_INDETERMINATE :"),
            ("fs",
             "lookup_status = fs_create_failure_status(lookup_status);",
             "lookup_status = lookup_status;"),
            ("fs",
             "if (fs_put_removed_checked(ip) < 0)\n\t\t\tresult = FS_LOOKUP_INDETERMINATE;",
             "iput(ip);\n\t\tif (0)\n\t\t\tresult = FS_LOOKUP_INDETERMINATE;"),
            ("fs",
             "if (detached <= 0)\n\t\treturn -1;",
             "if (detached <= 0)\n\t\treturn 0;"),
            ("file",
             "if (ip == 0)\n\t\t\tgoto fail;",
             "if (0)\n\t\t\tgoto fail;"),
            ("store_io",
             "if (ip == 0) {\n\t\t*status_out = status;\n\t\treturn 0;\n\t}",
             "if (ip == 0) {\n\t\t*status_out = FS_LOOKUP_ABSENT;\n\t\treturn 0;\n\t}"),
            ("store",
             "if (result == FS_LOOKUP_INDETERMINATE) {",
             "if (0) {"),
            ("store",
             "agent_metadata_store_fail_closed_runtime();\n\t\tagent_meta_persist.error_cause",
             "agent_meta_persist.error_cause"),
            ("store",
             "agent_meta_persist.error_cause =\n\t\t\tAGENT_METADATA_PERSIST_FAIL_CLOSED;",
             "agent_meta_persist.error_cause = AGENT_METADATA_PERSIST_IO;"),
            ("store",
             "agent_meta_persist.irrevocable = 1;\n\t\treturn result;",
             "agent_meta_persist.irrevocable = 0;\n\t\treturn result;"),
            ("store",
             "failure_irrevocable = agent_meta_persist.irrevocable;\n\t\t\t\tbreak;",
             "failure_irrevocable = 0;\n\t\t\t\tbreak;"),
            ("store",
             "completion->irrevocable = failure_irrevocable;",
             "completion->irrevocable = 0;"),
            ("objects",
             "bind_status == AGENT_CATALOG_INDETERMINATE ?",
             "0 ?"),
            ("objects",
             "(bind_status > 0 && agent_metadata_catalog_undo_note_created(",
             "(bind_status >= 0 && agent_metadata_catalog_undo_note_created("),
            ("objects",
             "if (persist->irrevocable)\n\t\treturn AGENT_STATUS_INDETERMINATE;",
             "if (persist->irrevocable)\n\t\treturn AGENT_STATUS_IO_ERROR;"),
            ("actions",
             "!persist->irrevocable &&\n\t    agent_file_status_batch_rollback",
             "agent_file_status_batch_rollback"),
            ("actions",
             "char text[AGENT_FILE_FIELD_SIZE + AGENT_FILE_SUMMARY_SIZE];",
             "char text[AGENT_FILE_FIELD_SIZE];"),
            ("actions",
             "memmove(undo->text, meta->status, sizeof(undo->text));",
             "memmove(undo->text, meta->status, AGENT_FILE_FIELD_SIZE);"),
            ("actions",
             "memmove(edit.meta->status, undo->text, sizeof(undo->text));",
             "memmove(edit.meta->status, undo->text, AGENT_FILE_FIELD_SIZE);"),
        )
        for name, old, new in cases:
            with self.subTest(name=name, anchor=old):
                altered = mutate(self.sources, name, old, new)
                with self.assertRaises(CONTRACT.ContractError):
                    CONTRACT.validate_sources(altered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
