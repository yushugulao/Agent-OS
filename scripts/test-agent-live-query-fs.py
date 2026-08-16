#!/usr/bin/env python3
"""Mutation tests for the Agent Live-Query FS source contract."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "agent_live_query_contract", ROOT / "scripts" / "check-agent-live-query-fs.py"
)
assert SPEC is not None and SPEC.loader is not None
CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT)


class AgentLiveQueryMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = CONTRACT.load_sources(ROOT)
        CONTRACT.validate_sources(cls.sources)

    def rejected(self, file_name: str, old: str, new: str = "") -> None:
        mutated = dict(self.sources)
        self.assertIn(old, mutated[file_name], f"mutation anchor missing: {old}")
        mutated[file_name] = mutated[file_name].replace(old, new, 1)
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.validate_sources(mutated)

    def rejected_in_function(
        self, file_name: str, function_name: str, old: str, new: str = ""
    ) -> None:
        mutated = dict(self.sources)
        body = CONTRACT.function(mutated[file_name], function_name)
        self.assertIn(old, body, f"mutation anchor missing in {function_name}: {old}")
        changed = body.replace(old, new, 1)
        mutated[file_name] = mutated[file_name].replace(body, changed, 1)
        with self.assertRaises(CONTRACT.ContractError):
            CONTRACT.validate_sources(mutated)

    def test_query_must_intersect_live_bitmap(self) -> None:
        self.rejected("catalog", "agent_catalog_live_query_bits[word]&visible", "visible")

    def test_selector_requires_full_lifecycle(self) -> None:
        self.rejected("catalog", "agent_catalog_slot_lifecycle(slot),lifecycle", "workflow_lifecycle_none(),lifecycle")

    def test_selector_checks_incarnation(self) -> None:
        self.rejected("catalog", "selector->incarnation==meta->incarnation", "1")

    def test_legacy_persistence_flags_are_rejected(self) -> None:
        self.rejected(
            "objects",
            "meta.flags&(AGENT_FILE_META_F_PERSIST|AGENT_FILE_META_F_AUTOSCAN)",
            "0",
        )

    def test_set_uses_volatile_commit(self) -> None:
        self.rejected("objects", "agent_metadata_catalog_edit_commit_volatile", "agent_metadata_catalog_edit_commit")

    def test_batch_and_scalar_share_per_item_core(self) -> None:
        self.rejected_in_function(
            "objects",
            "agent_file_meta_set_batch_execute",
            "agent_file_meta_set_execute",
            "agent_file_meta_set_batch_private",
        )

    def test_batch_records_are_staged_off_stack(self) -> None:
        self.rejected(
            "objects",
            "staticstructagent_file_metaagent_file_meta_batch_scratch[AGENT_FILE_META_BATCH_MAX]",
            "",
        )

    def test_batch_has_one_outer_metadata_transaction(self) -> None:
        self.rejected_in_function(
            "objects",
            "agent_file_meta_set_batch_execute",
            "agent_metadata_txn_unlock();returnprocessed;",
            "",
        )

    def test_batch_prefix_advances_after_status_copyout(self) -> None:
        self.rejected_in_function(
            "objects",
            "agent_file_meta_set_batch_execute",
            "if(copyout(p->pagetable,statusesaddr+(uint64)i*sizeof(item_status),(char*)&item_status,sizeof(item_status))<0){processed=AGENT_STATUS_INDETERMINATE;break;}processed++;",
            "processed++;if(copyout(p->pagetable,statusesaddr+(uint64)i*sizeof(item_status),(char*)&item_status,sizeof(item_status))<0){processed=AGENT_STATUS_INDETERMINATE;break;}",
        )

    def test_batch_rejects_overlapping_input_and_status_ranges(self) -> None:
        self.rejected_in_function(
            "objects",
            "agent_file_meta_set_batch_execute",
            "if(itemsaddr<statuses_end&&statusesaddr<items_end)return-1;",
            "",
        )

    def test_batch_snapshot_protects_staging(self) -> None:
        self.rejected_in_function(
            "objects",
            "agent_file_meta_set_batch_execute",
            "if(proc_vm_snapshot_begin(p)<0)gotoout_txn_error;",
            "",
        )

    def test_batch_materializes_status_cow_before_commit(self) -> None:
        self.rejected_in_function(
            "objects",
            "agent_file_meta_set_batch_execute",
            "copyout(p->pagetable,statusaddr,(char*)&preserved,sizeof(preserved))",
            "agent_status_slot_not_materialized()",
        )

    def test_batch_post_preflight_delivery_failure_is_indeterminate(self) -> None:
        self.rejected_in_function(
            "objects",
            "agent_file_meta_set_batch_execute",
            "processed=AGENT_STATUS_INDETERMINATE;",
            "processed=0;",
        )

    def test_batch_stops_at_fatal_item_status(self) -> None:
        self.rejected_in_function(
            "objects",
            "agent_file_meta_set_batch_execute",
            "if(stop_batch||item_status==AGENT_STATUS_INDETERMINATE)break;",
            "",
        )

    def test_batch_uses_one_lifecycle_operation(self) -> None:
        self.rejected_in_function(
            "objects",
            "sys_agent_file_meta_set_batch",
            "workflow_lifecycle_operation_leave(lifecycle);",
            "",
        )

    def test_admission_is_not_recovery_gated(self) -> None:
        self.rejected(
            "objects",
            "agent_metadata_admission_status(void){returnAGENT_STATUS_OK;}",
            "agent_metadata_admission_status(void){returnAGENT_STATUS_RETRY;}",
        )

    def test_fence_drains_before_generation_capture(self) -> None:
        self.rejected("objects", "agent_live_query_fence_drain(lifecycle,scope_id)", "AGENT_STATUS_OK")

    def test_fence_generation_uses_full_lifecycle(self) -> None:
        self.rejected(
            "objects",
            "agent_metadata_catalog_fence_generation(scope_id,lifecycle,metadata_generation)",
            "agent_metadata_catalog_fence_generation(scope_id,workflow_lifecycle_none(),metadata_generation)",
        )

    def test_catalog_fence_rejects_active_mutation(self) -> None:
        self.rejected_in_function(
            "catalog", "agent_metadata_catalog_fence_generation",
            "agent_catalog_mutation_owner!=0", "0",
        )

    def test_catalog_fence_binds_system_generation(self) -> None:
        self.rejected("catalog", "agent_file_state_scope_generation(VFS_SCOPE_SYSTEM)", "0")

    def test_unbound_write_remains_constant_time(self) -> None:
        self.rejected("directory", "if(FS_META_UNBOUND(ip))return", "")

    def test_contended_unlink_queues_tombstone(self) -> None:
        self.rejected_in_function(
            "directory", "agent_fs_remove_inode",
            "agent_live_query_tombstone_enqueue", "",
        )

    def test_deferred_delete_checks_incarnation(self) -> None:
        self.rejected_in_function(
            "catalog", "agent_metadata_catalog_remove_identity_exact",
            "agent_catalog_files[slot].incarnation!=incarnation",
            "0",
        )

    def test_tombstone_catalog_work_is_irq_on(self) -> None:
        self.rejected(
            "events",
            "snapshot=agent_live_query_tombstones[slot];intr_restore(enabled);",
            "snapshot=agent_live_query_tombstones[slot];",
        )

    def test_content_projection_is_irq_on(self) -> None:
        self.rejected(
            "events",
            "snapshot=agent_live_query_content_pending[slot];intr_restore(enabled);",
            "snapshot=agent_live_query_content_pending[slot];",
        )

    def test_typed_watch_is_transactional(self) -> None:
        self.rejected("events", "!agent_metadata_txn_owned(0)", "0",)

    def test_typed_watch_compiles_complete_predicate(self) -> None:
        self.rejected_in_function(
            "events", "agent_live_query_watch_install_typed",
            "agent_live_query_predicate_compile", "agent_live_query_predicate_ignore",
        )

    def test_watch_install_returns_generation_handshake(self) -> None:
        self.rejected(
            "events",
            "agent_metadata_catalog_fence_generation",
            "agent_metadata_catalog_generation",
        )

    def test_typed_delta_has_enter(self) -> None:
        self.rejected_in_function(
            "events", "agent_live_query_typed_target_changes",
            "!before_matches&&after_matches", "before_matches&&after_matches",
        )

    def test_typed_delta_has_leave(self) -> None:
        self.rejected_in_function(
            "events", "agent_live_query_typed_target_changes",
            "elseif(before_matches)", "elseif(0)",
        )

    def test_zero_watch_fast_path_precedes_process_scan(self) -> None:
        self.rejected_in_function(
            "events",
            "agent_live_query_publish_transition",
            "if(!agent_live_query_file_watches_present())return0;",
            "",
        )

    def test_zero_watch_count_starts_empty(self) -> None:
        self.rejected_in_function(
            "events",
            "agent_live_query_events_init",
            "agent_live_query_file_watch_processes=0;",
            "agent_live_query_file_watch_processes=1;",
        )

    def test_typed_unwatch_updates_zero_watch_count(self) -> None:
        self.rejected_in_function(
            "events",
            "agent_live_query_watch_remove_typed",
            "agent_live_query_file_watch_refresh_locked(p);",
            "",
        )

    def test_process_reset_clears_zero_watch_count(self) -> None:
        self.rejected_in_function(
            "events",
            "agent_live_query_proc_reset",
            "agent_live_query_file_watch_clear_locked(p);",
            "",
        )

    def test_generic_unwatch_updates_zero_watch_count(self) -> None:
        self.rejected_in_function(
            "ipc",
            "agent_ipc_watch_clear",
            "agent_live_query_file_watch_changed(p);",
            "",
        )

    def test_generic_clear_all_preserves_typed_watch_slots(self) -> None:
        self.rejected_in_function(
            "ipc",
            "agent_ipc_watch_clear",
            "if(cold->watch_event_type[i]==AGENT_EVENT_FILE_QUERY)continue;",
            "",
        )

    def test_generic_tool_watch_rejects_typed_file_query(self) -> None:
        self.rejected_in_function(
            "core",
            "agent_execute_op",
            "if(op->arg0>AGENT_EVENT_MAX||op->arg0==AGENT_EVENT_FILE_QUERY)",
            "if(op->arg0>AGENT_EVENT_MAX)",
        )

    def test_overflow_sets_sticky_resync(self) -> None:
        self.rejected_in_function(
            "events", "agent_live_query_publish_transition",
            "agent_live_query_proc_resync_mark_locked", "agent_live_query_proc_resync_clear",
        )

    def test_delivery_releases_snapshot_irq(self) -> None:
        self.rejected_in_function(
            "events", "agent_live_query_publish_transition",
            "typed_changes=agent_live_query_typed_target_changes(target,owner_scope,before,after);",
            "intr_restore(enabled);typed_changes=agent_live_query_typed_target_changes(target,owner_scope,before,after);",
        )

    def test_fence_rejects_unacked_resync(self) -> None:
        self.rejected_in_function(
            "events", "agent_live_query_fence_drain",
            "agent_live_query_proc_resync_pending_domain", "agent_live_query_no_pending",
        )

    def test_ipc_scope_includes_lifecycle_generation(self) -> None:
        self.rejected("ipc", "workflow_lifecycle_key_equal(left_key,right_key)", "1")

    def test_typed_delivery_bypasses_only_legacy_substring_branch(self) -> None:
        self.rejected("ipc", "delivery==AGENT_EVENT_LIVE_QUERY_TARGETED", "delivery==AGENT_EVENT_REQUIRE_WATCH")

    def test_typed_watch_cannot_fall_through_to_string_filter(self) -> None:
        self.rejected("ipc", "if(event_type==AGENT_EVENT_FILE_QUERY)returnagent_ipc_live_watch_update", "if(0)returnagent_ipc_live_watch_update")

    def test_proc_reuse_clears_subscriptions(self) -> None:
        self.rejected("ipc", "agent_live_query_proc_reset(p)", "")

    def test_content_size_projection_is_generation_bound(self) -> None:
        self.rejected("file_state", "hit->fs_generation=snapshot.fs_generation", "hit->fs_generation=0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
