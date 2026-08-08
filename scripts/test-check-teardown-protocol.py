#!/usr/bin/env python3
"""工作流拆除源码契约的负向回归测试。"""

import copy
import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-teardown-protocol.py")
ROOT = SCRIPT.parent.parent
SPEC = importlib.util.spec_from_file_location("teardown_protocol", SCRIPT)
teardown_protocol = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(teardown_protocol)


class TeardownProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good = teardown_protocol.load_sources(ROOT)

    def changed(self, key, old, new):
        sources = copy.deepcopy(self.good)
        self.assertEqual(sources[key].count(old), 1, f"mutation anchor drift: {old}")
        sources[key] = sources[key].replace(old, new, 1)
        return sources

    def assert_rejected(self, sources, pattern):
        with self.assertRaisesRegex(teardown_protocol.ProtocolError, pattern):
            teardown_protocol.validate_protocol(sources)

    def assert_terminal_rejected(self, sources, pattern):
        with self.assertRaisesRegex(teardown_protocol.ProtocolError, pattern):
            teardown_protocol.validate_terminal_teardown(sources["proc"])

    def test_repository_protocol_is_valid(self):
        teardown_protocol.validate_protocol(copy.deepcopy(self.good))

    def test_terminal_teardown_releases_gate_before_io_settlement(self):
        sources = self.changed(
            "proc",
            "\t\tfs_epoch_request_end();\n"
            "\t\tif (fileclose_batch_settle(&close_batch) < 0)\n"
            "\t\t\tpanic(\"process teardown file settlement\");\n"
            "\t\tif (bio_request_end_current_cleanup() < 0)",
            "\t\tif (fileclose_batch_settle(&close_batch) < 0)\n"
            "\t\t\tpanic(\"process teardown file settlement\");\n"
            "\t\tfs_epoch_request_end();\n"
            "\t\tif (bio_request_end_current_cleanup() < 0)",
        )
        self.assert_terminal_rejected(sources, "release FS gate, settle files")

    def test_terminal_teardown_cannot_drop_gate_release(self):
        before = (
            "\tif (terminal_current) {\n"
            "\t\tfs_epoch_request_end();\n"
            "\t\tif (fileclose_batch_settle(&close_batch) < 0)\n"
            "\t\t\tpanic(\"process teardown file settlement\");\n"
            "\t\tif (bio_request_end_current_cleanup() < 0)"
        )
        after = (
            "\tif (terminal_current) {\n"
            "\t\tif (fileclose_batch_settle(&close_batch) < 0)\n"
            "\t\t\tpanic(\"process teardown file settlement\");\n"
            "\t\tif (bio_request_end_current_cleanup() < 0)"
        )
        sources = self.changed("proc", before, after)
        self.assert_terminal_rejected(sources, "filesystem gate release")

    def test_terminal_teardown_cannot_drop_file_settlement(self):
        sources = self.changed(
            "proc",
            "\t\tif (fileclose_batch_settle(&close_batch) < 0)\n"
            "\t\t\tpanic(\"process teardown file settlement\");\n",
            "",
        )
        self.assert_terminal_rejected(sources, "deferred file settlement")

    def test_teardown_progress_settles_outside_gate(self):
        sources = self.changed(
            "proc",
            "\tfs_epoch_request_end();\n"
            "\tif (fileclose_batch_settle(batch) < 0)",
            "\tif (fileclose_batch_settle(batch) < 0)\n"
            "\tfs_epoch_request_end();",
        )
        self.assert_terminal_rejected(sources, "release the FS gate")

    def test_teardown_progress_must_yield_outside_gate(self):
        sources = self.changed(
            "proc",
            "\t(void)kernel_work_checkpoint_cleanup(KERNEL_WORK_OPERATION_UNITS);\n",
            "",
        )
        self.assert_terminal_rejected(sources, "settle and yield")

    def test_terminal_teardown_settles_before_lifecycle_release(self):
        before = (
            "\tvfs_proc_terminal_clear(p);\n"
            "\tvfs_proc_lifecycle_release(p);"
        )
        after = (
            "\tvfs_proc_lifecycle_release(p);\n"
            "\tvfs_proc_terminal_clear(p);"
        )
        sources = self.changed("proc", before, after)
        self.assert_terminal_rejected(sources, "release FS gate, settle files")

    def test_exit_cannot_release_gate_after_terminal_teardown(self):
        sources = self.changed(
            "proc",
            "\tif (proc_teardown_run(p, t, 1) < 0)\n"
            "\t\tpanic(\"active process exit\");",
            "\tif (proc_teardown_run(p, t, 1) < 0)\n"
            "\t\tpanic(\"active process exit\");\n"
            "\tfs_epoch_request_end();",
        )
        self.assert_terminal_rejected(sources, "releases the filesystem gate twice")

    def test_last_reference_must_request_reaper(self):
        sources = self.changed(
            "vfs",
            "\t\tagent_background_request();\n"
            "\t}\n"
            "\treturn last;\n",
            "\t}\n\treturn last;\n",
        )
        self.assert_rejected(sources, "last workflow reference")

    def test_last_reference_must_quiesce_before_request(self):
        before = (
            "\t\tfs_storage_scope_account_close(storage);\n"
            "\t\tbio_scope_quiesce(scope_id);\n"
        )
        after = (
            "\t\tagent_background_request();\n"
            "\t\tfs_storage_scope_account_close(storage);\n"
            "\t\tbio_scope_quiesce(scope_id);\n"
        )
        sources = self.changed("vfs", before, after)
        self.assert_rejected(sources, "exactly one maintenance edge|quiesce")

    def test_unfinished_reaper_phase_must_wait_for_timer(self):
        sources = self.changed(
            "vfs",
            "\t\tregistry->reap_next_tick = now + 1;\n",
            "",
        )
        self.assert_rejected(sources, "one-bounded-phase-per-tick")

    def test_reaper_timer_must_be_level_guarded(self):
        sources = self.changed(
            "vfs",
            "\tif (__atomic_load_n(&registry->retiring_count, "
            "__ATOMIC_ACQUIRE) != 0 &&\n"
            "\t    (next == 0 || now >= next))\n"
            "\t\tagent_background_request();",
            "\tagent_background_request();",
        )
        self.assert_rejected(sources, "timer lost pending")

    def test_reaper_cannot_busy_loop(self):
        sources = self.changed(
            "vfs",
            "\tif (__atomic_load_n(&registry->retiring_count, "
            "__ATOMIC_ACQUIRE) != 0 &&\n"
            "\t    (next == 0 || now >= next))\n"
            "\t\tagent_background_request();",
            "\twhile (__atomic_load_n(&registry->retiring_count,\n"
            "\t\t\t       __ATOMIC_ACQUIRE) != 0)\n"
            "\t\tagent_background_request();",
        )
        self.assert_rejected(sources, "timer lost|must remain O\\(1\\)")

    def test_reaper_pending_must_use_retiring_counter(self):
        sources = self.changed(
            "vfs",
            "\tif (registry->retiring_count == 0)\n"
            "\t\tregistry->reap_next_tick = 0;",
            "\tif (registry->used_count == 0)\n"
            "\t\tregistry->reap_next_tick = 0;",
        )
        self.assert_rejected(sources, "one-bounded-phase-per-tick")

    def test_scope_lookup_must_use_scope_hash(self):
        sources = self.changed(
            "vfs",
            "\tlink = registry->hash_heads[vfs_scope_hash(scope_id)];",
            "\tlink = registry->hash_heads[0];",
        )
        self.assert_rejected(sources, "hash lookup")

    def test_scope_lookup_must_match_exact_scope(self):
        sources = self.changed(
            "vfs",
            "\t\tif (ref->scope_id == scope_id)\n\t\t\treturn ref;",
            "\t\tif (ref->scope_id != scope_id)\n\t\t\treturn ref;",
        )
        self.assert_rejected(sources, "hash lookup")

    def test_phase_publication_must_use_direct_scope_lookup(self):
        before = (
            "\t\tstruct vfs_scope_ref *ref = vfs_scope_find_locked(scope_id);\n\n"
            "\t\tif (ref != 0 && ref->retiring &&\n"
            "\t\t    workflow_lifecycle_key_equal(ref->lifecycle, lifecycle) &&\n"
            "\t\t    ref->reclaim_phase == expected &&"
        )
        after = before.replace(
            "vfs_scope_find_locked(scope_id)", "&vfs_scope_registry.refs[0]", 1
        )
        sources = self.changed("vfs", before, after)
        self.assert_rejected(sources, "direct hashed scope match|lost guard")

    def test_background_edge_take_must_remain_atomic(self):
        sources = self.changed(
            "background",
            "return __atomic_exchange_n(&agent_background_pending, 0,\n"
            "\t\t\t\t   __ATOMIC_ACQ_REL);",
            "return agent_background_pending;",
        )
        self.assert_rejected(sources, "atomically merge concurrent requests")

    def test_background_checkpoint_cannot_drain_in_a_loop(self):
        sources = self.changed(
            "agent_core",
            "\tagent_background_maintain();",
            "\tdo {\n"
            "\t\tagent_background_maintain();\n"
            "\t} while (agent_background_take());",
        )
        self.assert_rejected(sources, "exactly 1|one maintenance pass")

    def test_vfs_bridge_must_admit_neutral_background_latch(self):
        sources = copy.deepcopy(self.good)
        bridge = next(
            entry
            for entry in sources["budget"]["agent_modules"][
                "integration_bridges"
            ]
            if entry["name"] == "vfs_security"
        )
        bridge["allowed_dependencies"].remove("background")
        self.assert_rejected(sources, "teardown dependency")

    def test_same_pass_metadata_commit_cannot_consume_reaper_edge(self):
        sources = copy.deepcopy(self.good)
        reap = "vfs_scope_reap_pending(now);"
        store = "agent_metadata_store_background_maintain(0)"
        self.assertEqual(sources["objects"].count(reap), 1)
        self.assertEqual(sources["objects"].count(store), 1)
        sources["objects"] = sources["objects"].replace(
            reap, "REAPER_ORDER_MUTATION();", 1
        )
        sources["objects"] = sources["objects"].replace(
            store, "vfs_scope_reap_pending(now)", 1
        )
        sources["objects"] = sources["objects"].replace(
            "REAPER_ORDER_MUTATION();", store + ";", 1
        )
        self.assert_rejected(sources, "same-pass commit")

    def test_wrong_phase_transition_is_rejected(self):
        sources = self.changed(
            "vfs",
            "scope_id, lifecycle, phase, VFS_SCOPE_RECLAIM_METADATA,\n"
            "\t\t\tmetadata_target",
            "scope_id, lifecycle, phase, VFS_SCOPE_RECLAIM_RETIRE,\n"
            "\t\t\tmetadata_target",
        )
        self.assert_rejected(sources, "FILES must advance once to METADATA")

    def test_duplicate_dirty_generation_is_rejected(self):
        assignment = (
            "\t\t*metadata_target = "
            "agent_metadata_store_mark_dirty(scope_id);"
        )
        sources = self.changed(
            "objects", assignment, assignment + "\n" + assignment
        )
        self.assert_rejected(sources, "mark_dirty exactly 1|dirty generation")

    def test_pending_files_cannot_advance(self):
        sources = self.changed(
            "vfs",
            "\t\tif (status == FS_RECLAIM_PENDING)\n\t\t\treturn;",
            "\t\tif (status == FS_RECLAIM_PENDING) {\n"
            "\t\t\t(void)vfs_scope_reclaim_advance(\n"
            "\t\t\t\tscope_id, lifecycle, phase,\n"
            "\t\t\t\tVFS_SCOPE_RECLAIM_METADATA, metadata_target);\n"
            "\t\t\treturn;\n\t\t}",
        )
        self.assert_rejected(sources, "must not advance while pending")

    def test_bio_retirement_in_files_is_rejected(self):
        sources = self.changed(
            "vfs",
            "\tif (phase == VFS_SCOPE_RECLAIM_FILES) {\n"
            "\t\tint status = fs_reclaim_scope_files(scope_id);",
            "\tif (phase == VFS_SCOPE_RECLAIM_FILES) {\n"
            "\t\tbio_scope_retire(scope_id);\n"
            "\t\tint status = fs_reclaim_scope_files(scope_id);",
        )
        self.assert_rejected(sources, "FILES phase crosses phase ownership")

    def test_metadata_failure_cannot_retire_state(self):
        sources = self.changed(
            "store",
            "\tif (agent_meta_store_failed_closed ||\n"
            "\t    agent_durable_section_scope_pending(scope_id))",
            "\tif (agent_durable_section_scope_pending(scope_id))",
        )
        self.assert_rejected(sources, "target retirement must contain one if")

    def test_metadata_pending_durable_state_blocks_target_zero(self):
        sources = self.changed(
            "store",
            "\tif (agent_meta_store_failed_closed ||\n"
            "\t    agent_durable_section_scope_pending(scope_id))",
            "\tif (agent_meta_store_failed_closed)",
        )
        self.assert_rejected(sources, "target retirement must contain one if")

    def test_metadata_dirty_must_reach_durable_generation(self):
        sources = self.changed(
            "store",
            "\t    state->dirty_generation != state->durable_generation ||\n",
            "",
        )
        self.assert_rejected(sources, "target retirement must contain one if")

    def test_metadata_durable_must_reach_replicated_generation(self):
        sources = self.changed(
            "store",
            "\t    state->dirty_generation != state->replicated_generation ||\n",
            "",
        )
        self.assert_rejected(sources, "target retirement must contain one if")

    def test_metadata_busy_lane_cannot_retire_state(self):
        sources = self.changed(
            "store",
            "\t    agent_file_writeback_scope_busy(scope_id))",
            "\t    0)",
        )
        self.assert_rejected(sources, "target retirement must contain one if")

    def test_metadata_missing_slot_rejects_nonzero_generation(self):
        sources = self.changed(
            "store", "\tretired = target == 0;", "\tretired = 1;"
        )
        self.assert_rejected(sources, "absent slot only for target zero")

    def test_metadata_target_zero_cannot_bypass_failure_barriers(self):
        absent = (
            "\tstate = agent_file_scope_state_locked(scope_id, 0);\n"
            "\tif (state == 0) {\n"
            "\t\tretired = target == 0;\n"
            "\t\tgoto out;\n\t}\n"
        )
        barrier = (
            "\tif (agent_meta_store_failed_closed ||\n"
            "\t    agent_durable_section_scope_pending(scope_id))\n"
            "\t\tgoto out;\n"
        )
        sources = self.changed(
            "store", barrier + absent, absent + barrier,
        )
        self.assert_rejected(sources, "retired before its target settled")

    def test_metadata_retirement_requires_transaction_lock(self):
        sources = self.changed(
            "store",
            "\tif (!agent_metadata_txn_lock(0))\n\t\treturn 0;\n",
            "",
        )
        self.assert_rejected(sources, "atomically check and retire")

    def test_metadata_background_retries_unnotified_durable_state(self):
        sources = self.changed(
            "store",
            "\t\t(void)agent_durable_section_retry_pending();",
            "\t\t(void)0;",
        )
        self.assert_rejected(sources, "timer no longer .*publishes due durable work")

    def test_metadata_timer_wakes_fifo_continuation(self):
        sources = self.changed(
            "store",
            "\t\twait_queue_wake_all(&waiters);\n"
            "\t\tagent_background_request();",
            "\t\tagent_background_request();",
        )
        self.assert_rejected(sources, "timer no longer .*publishes due durable work")

    def test_metadata_state_cannot_retire_before_settlement(self):
        guard = (
            "\tif (!agent_file_writeback_generation_reached(\n"
            "\t\t    state->replicated_generation, target) ||\n"
            "\t    state->dirty_generation != state->durable_generation ||\n"
            "\t    state->dirty_generation != state->replicated_generation ||\n"
            "\t    agent_file_writeback_scope_busy(scope_id))\n"
            "\t\tgoto out;\n"
            "\tmemset(state, 0, sizeof(*state));"
        )
        early = (
            "\tmemset(state, 0, sizeof(*state));\n"
            "\tif (!agent_file_writeback_generation_reached(\n"
            "\t\t    state->replicated_generation, target) ||\n"
            "\t    state->dirty_generation != state->durable_generation ||\n"
            "\t    state->dirty_generation != state->replicated_generation ||\n"
            "\t    agent_file_writeback_scope_busy(scope_id))\n"
            "\t\tgoto out;"
        )
        sources = self.changed("store", guard, early)
        self.assert_rejected(sources, "retired before its target settled")

    def test_missing_begin_cleanup_is_rejected(self):
        sources = self.changed(
            "objects", "agent_observe_scope_reclaim(scope_id)", "0"
        )
        self.assert_rejected(sources, "lost observability cleanup ownership")

    def test_legacy_all_in_one_entry_is_rejected(self):
        sources = copy.deepcopy(self.good)
        sources["os_sources"]["os/legacy_reclaim.h"] = (
            "int agent_scope_reclaim(uint scope_id, int preserve_files);\n"
        )
        self.assert_rejected(sources, "legacy all-in-one reclaim entry")

    def test_vfs_cannot_bypass_metadata_owner(self):
        sources = copy.deepcopy(self.good)
        bridge = next(
            entry
            for entry in sources["budget"]["agent_modules"]["integration_bridges"]
            if entry["name"] == "vfs_security"
        )
        bridge["allowed_dependencies"].append("metadata_store")
        self.assert_rejected(sources, "dependency bypasses metadata_objects")

    def test_global_trapframe_pool_is_rejected(self):
        sources = copy.deepcopy(self.good)
        sources["proc"] = (
            "char trapframe[NPROC][NTHREAD][TRAP_PAGE_SIZE];\n"
            + sources["proc"]
        )
        self.assert_rejected(sources, "global NPROC x NTHREAD trapframe pool")

    def test_unaccounted_trapframe_allocation_is_rejected(self):
        sources = self.changed(
            "proc", "kalloc_account_page(t->resource_account,", "kalloc("
        )
        self.assert_rejected(sources, "must call kalloc_account_page")

    def test_trapframe_must_precede_thread_account_release(self):
        sources = self.changed(
            "proc",
            "\tthread_trapframe_release(t);\n"
            "\tproc_thread_resource_release(t);",
            "\tproc_thread_resource_release(t);\n"
            "\tthread_trapframe_release(t);",
        )
        self.assert_rejected(sources, "thread account released before")

    def test_exec_requires_trapframe_mapping_validation(self):
        sources = self.changed(
            "proc",
            "if (!proc_user_image_trapframe_valid(p, image) ||\n"
            "\t    vfs_proc_exec_prepare(p, image, live_exec, &transition) < 0)",
            "if (vfs_proc_exec_prepare(p, image, live_exec, &transition) < 0)",
        )
        self.assert_rejected(sources, "lost trapframe image validation")

    def test_public_exec_identity_must_precede_fd_detach(self):
        identity = (
            "\t\tif (transition.identity_policy == VFS_EXEC_IDENTITY_PUBLIC &&\n"
            "\t\t    agent_exec_public_identity_commit(p) < 0) {\n"
            "\t\t\tintr_restore(enabled);\n"
            "\t\t\tvfs_proc_exec_abort(&transition);\n"
            "\t\t\treturn -1;\n"
            "\t\t}\n"
        )
        detach = (
            "\t\tif (transition.drop_to_public)\n"
            "\t\t\tproc_detach_vfs_scope_fds_locked(p, revoked_files);\n"
        )
        sources = self.changed("proc", identity + detach, detach + identity)
        self.assert_rejected(sources, "publication order")

    def test_public_exec_vfs_commit_must_precede_vm_swap(self):
        before = (
            "\t\tif (vfs_proc_exec_commit(p, &transition) < 0)\n"
            "\t\t\tpanic(\"validated exec credential commit\");\n"
            "\t}\n"
            "\tagent_process_image_install_locked(p);\n"
            "\tsync_proc_exec_reset_locked(p, &p->threads[0]);\n"
            "\tp->pagetable = image->pagetable;"
        )
        after = (
            "\t}\n"
            "\tagent_process_image_install_locked(p);\n"
            "\tsync_proc_exec_reset_locked(p, &p->threads[0]);\n"
            "\tp->pagetable = image->pagetable;\n"
            "\t\tif (vfs_proc_exec_commit(p, &transition) < 0)\n"
            "\t\t\tpanic(\"validated exec credential commit\");"
        )
        sources = self.changed("proc", before, after)
        self.assert_rejected(sources, "publication order")

    def test_public_exec_image_reset_is_required(self):
        sources = self.changed(
            "proc", "\tagent_process_image_install_locked(p);\n", ""
        )
        self.assert_rejected(
            sources, "must call agent_process_image_install_locked exactly 1"
        )

    def test_public_exec_image_reset_must_precede_vm_swap(self):
        before = (
            "\tagent_process_image_install_locked(p);\n"
            "\tsync_proc_exec_reset_locked(p, &p->threads[0]);\n"
            "\tp->pagetable = image->pagetable;"
        )
        after = (
            "\tsync_proc_exec_reset_locked(p, &p->threads[0]);\n"
            "\tp->pagetable = image->pagetable;\n"
            "\tagent_process_image_install_locked(p);"
        )
        sources = self.changed("proc", before, after)
        self.assert_rejected(sources, "publication order")

    def test_exec_context_alias_failure_requires_abort(self):
        failure = (
            "\t\tif (agent_alias_exec_context(p, image->pagetable) < 0) {\n"
            "\t\t\tvfs_proc_exec_abort(&transition);\n"
            "\t\t\treturn -1;\n"
            "\t\t}"
        )
        sources = self.changed(
            "proc", failure, failure.replace("\t\t\tvfs_proc_exec_abort(&transition);\n", "")
        )
        self.assert_rejected(sources, "Context alias failure must abort")

    def test_exec_locked_validation_failure_requires_abort(self):
        failure = (
            "\tif (!proc_teardown_live(p) ||\n"
            "\t    !proc_image_install_state_valid_locked(p, mode) ||\n"
            "\t    sync_proc_exec_validate_locked(p, &p->threads[0]) < 0 ||\n"
            "\t    vfs_proc_exec_validate_locked(p, &transition) < 0) {\n"
            "\t\tintr_restore(enabled);\n"
            "\t\tvfs_proc_exec_abort(&transition);\n"
            "\t\treturn -1;\n"
            "\t}"
        )
        sources = self.changed(
            "proc", failure, failure.replace("\t\tvfs_proc_exec_abort(&transition);\n", "")
        )
        self.assert_rejected(sources, "locked validation failure must abort")

    def test_exec_locked_validation_rejects_missing_predicate(self):
        predicates = (
            (
                "!proc_teardown_live(p) ||\n\t    ",
                "",
            ),
            (
                "!proc_image_install_state_valid_locked(p, mode) ||\n\t    ",
                "",
            ),
            (
                "sync_proc_exec_validate_locked(p, &p->threads[0]) < 0 ||\n\t    ",
                "",
            ),
            (
                " ||\n\t    vfs_proc_exec_validate_locked(p, &transition) < 0",
                "",
            ),
        )
        for old, new in predicates:
            with self.subTest(predicate=old):
                sources = self.changed("proc", old, new)
                self.assert_rejected(
                    sources, "independent predicates|must call"
                )

    def test_exec_locked_validation_rejects_moved_predicate(self):
        original = (
            "\tif (!proc_teardown_live(p) ||\n"
            "\t    !proc_image_install_state_valid_locked(p, mode) ||\n"
            "\t    sync_proc_exec_validate_locked(p, &p->threads[0]) < 0 ||\n"
            "\t    vfs_proc_exec_validate_locked(p, &transition) < 0) {"
        )
        moved = (
            "\tif (!proc_teardown_live(p) ||\n"
            "\t    sync_proc_exec_validate_locked(p, &p->threads[0]) < 0 ||\n"
            "\t    vfs_proc_exec_validate_locked(p, &transition) < 0) {"
            "\n\t\tif (!proc_image_install_state_valid_locked(p, mode))\n"
            "\t\t\treturn -1;"
        )
        sources = self.changed("proc", original, moved)
        self.assert_rejected(sources, "independent predicates")

    def test_exec_locked_validation_rejects_short_circuit_predicate(self):
        guard = (
            "\tif (!proc_teardown_live(p) ||\n"
            "\t    !proc_image_install_state_valid_locked(p, mode) ||\n"
            "\t    sync_proc_exec_validate_locked(p, &p->threads[0]) < 0 ||\n"
            "\t    vfs_proc_exec_validate_locked(p, &transition) < 0) {"
        )
        predicates = (
            "!proc_teardown_live(p)",
            "!proc_image_install_state_valid_locked(p, mode)",
            "sync_proc_exec_validate_locked(p, &p->threads[0]) < 0",
            "vfs_proc_exec_validate_locked(p, &transition) < 0",
        )
        for predicate in predicates:
            with self.subTest(predicate=predicate):
                sources = self.changed(
                    "proc", guard, guard.replace(
                        predicate, f"(0 && ({predicate}))", 1
                    )
                )
                self.assert_rejected(sources, "independent predicates")

    def test_exec_locked_validation_rejects_irq_guard_movement(self):
        guard = (
            "\tenabled = intr_save();\n"
            "\tif (!proc_teardown_live(p) ||\n"
            "\t    !proc_image_install_state_valid_locked(p, mode) ||\n"
            "\t    sync_proc_exec_validate_locked(p, &p->threads[0]) < 0 ||\n"
            "\t    vfs_proc_exec_validate_locked(p, &transition) < 0) {"
        )
        sources = self.changed(
            "proc", guard, guard.replace("\tenabled = intr_save();\n", "", 1)
        )
        self.assert_rejected(sources, "IRQ publication boundary")

    def test_exec_reserved_commit_failure_requires_abort(self):
        failure = (
            "\t\tif (vfs_proc_exec_commit(p, &transition) < 0) {\n"
            "\t\t\tintr_restore(enabled);\n"
            "\t\t\tvfs_proc_exec_abort(&transition);\n"
            "\t\t\treturn -1;\n"
            "\t\t}"
        )
        sources = self.changed(
            "proc", failure, failure.replace("\t\t\tvfs_proc_exec_abort(&transition);\n", "")
        )
        self.assert_rejected(sources, "reserved lifecycle commit failure must abort")

    def test_exec_public_identity_failure_requires_abort(self):
        failure = (
            "\t\tif (transition.identity_policy == VFS_EXEC_IDENTITY_PUBLIC &&\n"
            "\t\t    agent_exec_public_identity_commit(p) < 0) {\n"
            "\t\t\tintr_restore(enabled);\n"
            "\t\t\tvfs_proc_exec_abort(&transition);\n"
            "\t\t\treturn -1;\n"
            "\t\t}"
        )
        sources = self.changed(
            "proc", failure, failure.replace("\t\t\tvfs_proc_exec_abort(&transition);\n", "")
        )
        self.assert_rejected(sources, "PUBLIC identity failure must abort")

    def test_exec_failure_must_abort_before_return(self):
        before = (
            "\t\tif (agent_alias_exec_context(p, image->pagetable) < 0) {\n"
            "\t\t\tvfs_proc_exec_abort(&transition);\n"
            "\t\t\treturn -1;\n"
            "\t\t}"
        )
        after = (
            "\t\tif (agent_alias_exec_context(p, image->pagetable) < 0) {\n"
            "\t\t\treturn -1;\n"
            "\t\t\tvfs_proc_exec_abort(&transition);\n"
            "\t\t}"
        )
        sources = self.changed("proc", before, after)
        self.assert_rejected(sources, "Context alias failure must abort")

    def test_process_prepare_requires_recycled_clean_guard(self):
        sources = self.changed(
            "agent_core",
            "p->agent_controller_id != 0 ||",
            "0 ||",
        )
        self.assert_rejected(sources, "RECYCLED_CLEAN process prepare")

    def test_process_prepare_cannot_repeat_reset(self):
        sources = self.changed(
            "agent_core",
            "\tagent_ipc_proc_prepare(p);",
            "\tagent_observe_proc_reset(p);\n\tagent_ipc_proc_prepare(p);",
        )
        self.assert_rejected(sources, "RECYCLED_CLEAN process prepare")

    def test_process_prepare_cannot_rewrite_state(self):
        sources = self.changed(
            "agent_core",
            "\tagent_ipc_proc_prepare(p);",
            "\tp->agent_id = 0;\n\tagent_ipc_proc_prepare(p);",
        )
        self.assert_rejected(sources, "must not rewrite recycled process state")

    def test_process_prepare_rejects_unowned_call(self):
        sources = self.changed(
            "agent_core",
            "\tagent_ipc_proc_prepare(p);",
            "\t(void)kalloc();\n\tagent_ipc_proc_prepare(p);",
        )
        self.assert_rejected(sources, "unowned calls")

    def test_ipc_prepare_cannot_run_full_reset(self):
        sources = self.changed(
            "agent_ipc",
            "\tenabled = intr_save();\n"
            "\tagent_ipc_event_baton_clear_locked(p);\n"
            "\tintr_restore(enabled);\n"
            "}\n\nvoid\nagent_ipc_proc_reset",
            "\tenabled = intr_save();\n"
            "\tagent_ipc_proc_reset(p);\n"
            "\tintr_restore(enabled);\n"
            "}\n\nvoid\nagent_ipc_proc_reset",
        )
        self.assert_rejected(sources, "IPC process prepare")

    def test_ipc_teardown_must_broadcast_before_reset(self):
        sources = self.changed(
            "agent_ipc",
            "\tagent_ipc_broadcast_event_teardown_locked(p);\n"
            "\tagent_ipc_proc_reset(p);",
            "\tagent_ipc_proc_reset(p);\n"
            "\tagent_ipc_broadcast_event_teardown_locked(p);",
        )
        self.assert_rejected(sources, "IPC process teardown call order")



    def test_retired_mailread_must_fail_closed(self):
        sources = self.changed(
            "syscall",
            "int sys_mailread(uint64 buf, int len)\n{\n\t(void)buf;\n\t(void)len;\n\treturn -1;\n}",
            "int sys_mailread(uint64 buf, int len)\n{\n\t(void)buf;\n\t(void)len;\n\treturn 0;\n}",
        )
        self.assert_rejected(
            sources, "sys_mailread must only discard arguments and return -1"
        )

    def test_retired_mailwrite_cannot_touch_user_memory(self):
        before = (
            "int sys_mailwrite(int pid, uint64 buf, int len)\n"
            "{\n"
            "\t(void)pid;\n"
            "\t(void)buf;\n"
            "\t(void)len;\n"
            "\treturn -1;\n"
            "}"
        )
        after = (
            "int sys_mailwrite(int pid, uint64 buf, int len)\n"
            "{\n"
            "\tchar byte;\n"
            "\tif (copyin(curr_proc()->pagetable, &byte, buf, 1) < 0)\n"
            "\t\treturn -1;\n"
            "\treturn -1;\n"
            "}"
        )
        sources = self.changed("syscall", before, after)
        self.assert_rejected(sources, "retired legacy mail syscalls")

    def test_retired_mailwrite_cannot_have_hidden_side_effect(self):
        before = (
            "int sys_mailwrite(int pid, uint64 buf, int len)\n"
            "{\n"
            "\t(void)pid;\n"
            "\t(void)buf;\n"
            "\t(void)len;\n"
            "\treturn -1;\n"
            "}"
        )
        sources = self.changed(
            "syscall",
            before,
            before.replace(
                "\treturn -1;", "\t(void)kalloc();\n\treturn -1;", 1
            ),
        )
        self.assert_rejected(sources, "must only discard arguments")


if __name__ == "__main__":
    unittest.main()
