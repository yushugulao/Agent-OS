#ifndef AGENT_INTERNAL_H
#define AGENT_INTERNAL_H

#include "agent.h"
#include "agent_context.h"
#include "agent_lifecycle.h"
#include "bio.h"
#include "proc.h"

/*
 * Private contracts between AgentOS implementation modules.  No writable
 * subsystem state is exported: each owner exposes operations over proc-local
 * state or read-only policy records.
 */
struct agent_role_policy {
	int role;
	uint64 capability_mask;
	uint64 role_grant_mask;
	int sched_weight;
};

struct agent_endpoint_handle {
	int slot;
	int pid;
	uint scope_id;
	uint64 control_id;
};

struct agent_controller_departure {
	struct workflow_lifecycle_key lifecycle;
	uint scope_id;
	uint64 control_id;
	int scope_controller;
};

struct agent_metadata_runtime_snapshot {
	uint metadata_txn_owned;
	uint metadata_txn_waiters;
};

struct agent_ipc_sched_snapshot {
	uint64 event_count_queued;
	uint64 wait_deadline;
	uint64 last_heartbeat_tick;
	int wait_deadline_valid;
	int heartbeat_interval;
	int loop_state;
};

struct agent_legacy_read_receipt {
	uint64 endpoint_generation;
	uint64 token;
	int pid;
	int slot;
};

/* Thin facade targets implemented by the core owner module. */
void agent_core_init(void);
void agent_core_clear_metadata(struct proc *);
void agent_core_proc_prepare(struct proc *);
void agent_core_proc_teardown(struct proc *);
int agent_core_exec_public_commit(struct proc *);
void agent_core_storage_init(void);
void agent_core_tick(void);
void agent_free_proc_context(struct proc *);

/* Edge-triggered publication into the schedulable background coordinator. */
int agent_background_take(void);

/* Identity and authorization policy. */
void agent_identity_init(void);
int agent_identity_alloc_id(void);
void agent_identity_id_floor(uint);
const struct agent_role_policy *agent_identity_role_policy(int);
int agent_identity_role_valid(int);
int agent_identity_role_sched_weight(int);
uint agent_identity_proc_scope(struct proc *);
void agent_identity_authority_bootstrap(struct proc *);
void agent_identity_authority_on_exec(struct proc *);
void agent_identity_proc_reset(struct proc *, int);
void agent_identity_loop_refresh_locked(struct proc *);
void agent_identity_thread_loop_set(struct thread *, int);
int agent_identity_authority_check(struct proc *, int);
int agent_identity_has_cap(struct proc *, uint64);
int agent_identity_has_any_cap(struct proc *, uint64);
int agent_identity_authorize_object(struct proc *, uint64, uint, int);
int agent_identity_controls_target(struct proc *, struct proc *);
int agent_identity_controls_or_self(struct proc *, struct proc *);
int agent_identity_controller_active_locked(
	uint64, struct workflow_lifecycle_key, uint);
int agent_identity_spawn_publish_locked(struct proc *, struct proc *);
int agent_identity_controller_depart(struct proc *, int,
				     struct agent_controller_departure *);
int agent_identity_controller_close_commit(
	struct proc *, const struct agent_controller_departure *);

/* Workflow-controller lifetime and generation-safe control identities. */
/* Trusted endpoint routing; callers hold the process-table interrupt guard. */
void agent_ipc_init(void);
void agent_ipc_endpoint_capture_locked(struct agent_endpoint_handle *,
				       struct proc *);
struct proc *agent_ipc_endpoint_resolve_locked(
	struct agent_endpoint_handle *);
void agent_ipc_remove_source(uint64);
void agent_ipc_proc_prepare(struct proc *);
void agent_ipc_proc_reset(struct proc *);
void agent_ipc_proc_activate(struct proc *);
void agent_ipc_proc_teardown(struct proc *);
void agent_ipc_exec_public(struct proc *);
void agent_ipc_thread_runtime_transition(struct thread *, int);
void agent_ipc_process_image_install_locked(struct proc *);
void agent_ipc_thread_sched_snapshot(struct thread *,
				     struct agent_ipc_sched_snapshot *);
int agent_ipc_legacy_public_send(struct proc *, int, char *, int);
int agent_ipc_legacy_public_read_begin(struct proc *, char *, int,
				       struct agent_legacy_read_receipt *);
int agent_ipc_legacy_public_read_finish(
	struct proc *, const struct agent_legacy_read_receipt *, int);
int agent_ipc_legacy_mailbox_empty(const struct proc *);
void agent_ipc_legacy_fill_info(struct proc *, struct agent_info *);
int agent_ipc_watch_set(struct proc *, int, char *);
int agent_ipc_deliver_pid(int, struct proc *, int, uint64, uint64, char *,
			  int, int *);
int agent_ipc_deliver_watchers(struct proc *, int, uint64, uint64, char *);
int agent_ipc_mailbox_take(struct proc *, int *, char *, int);
int agent_ipc_heartbeat_configure(struct proc *, uint64, uint64 *);
void agent_ipc_tick_proc(struct proc *, uint64);

/* Metadata transaction gate. */
void agent_metadata_init(void);
void *agent_metadata_txn_token(void);
int agent_metadata_txn_lock(int);
int agent_metadata_txn_try_external(void);
void agent_metadata_txn_unlock(void);
void agent_metadata_txn_relock_uninterruptible(void);
void agent_metadata_txn_projection_transition(int);
#define agent_metadata_txn_projection_begin() \
	agent_metadata_txn_projection_transition(1)
#define agent_metadata_txn_projection_ack() \
	agent_metadata_txn_projection_transition(0)
void agent_metadata_txn_projection_require_idle(void);
void agent_metadata_txn_work_charge(uint);
struct bio_checkpoint_result agent_metadata_txn_checkpoint_unlocked(void);
struct bio_checkpoint_result
agent_metadata_txn_checkpoint_cleanup_unlocked(void);
int agent_metadata_txn_owned(int);
void agent_metadata_txn_require_owned(int, const char *);
int agent_metadata_txn_depth(void);
void agent_metadata_proc_runtime_snapshot(
	struct proc *, struct agent_metadata_runtime_snapshot *);
int agent_metadata_reload_available(void);
int agent_metadata_reload_is_current(void);
int agent_metadata_reload_claim(void);
void agent_metadata_reload_release(void);

/* Authoritative file-object catalog and its durable metadata image. */
void agent_metadata_objects_init(void);
void agent_metadata_storage_init(void);
int agent_metadata_durable_status(void);
int agent_metadata_admission_status(void);
void agent_metadata_background_maintain(void);
void agent_metadata_tick(uint64);
void agent_metadata_fill_info(uint, struct agent_info *);
int agent_metadata_tool_enter(int);
void agent_metadata_tool_exit(int);
int agent_metadata_execute_tool(struct proc *, struct agent_op *,
				struct agent_result *);
int agent_metadata_prefetch_handoff(struct agent_endpoint_handle *,
				    struct agent_endpoint_handle *);

/* Observation identities and bounded-query scheduling. */
void agent_observe_init(void);
void agent_observe_proc_init(struct proc *, int, uint64);
void agent_observe_proc_reset(struct proc *);
void agent_observe_tick_proc(struct proc *, uint64);
int agent_observe_scope_reclaim(uint);
uint64 agent_observe_scope_epoch(uint);
uint64 agent_observe_alloc_span_id(void);
uint64 agent_observe_alloc_event_id(void);
int agent_observe_query_reserve(uint64);
int agent_observe_query_reserve_to(uint64, uint64 *);
int agent_observe_recording_suppress_begin(struct proc *);
void agent_observe_recording_suppress_end(struct proc *);
int agent_observe_recording_suppressed(struct proc *);
void agent_observe_record_context(struct proc *,
				  struct agent_context_record *, int, int);
void agent_observe_record_sched(struct thread *, struct agent_sched_record *);
void agent_observe_record_event(int, struct proc *, struct agent_event *,
				uint64, uint64);
void agent_observe_record_effect(struct proc *, int, int, char *, uint64,
				 uint64, uint64, uint64, int);
void agent_observe_record_prefetch(struct proc *,
				   struct agent_file_prefetch_hint *,
				   uint64, char *, int);
void agent_observe_record_prefetch_handoff_locked(
	int, uint64, struct proc *, struct agent_file_prefetch_hint *,
	uint64, char *, uint64);
int agent_observe_prefetch_span_snapshot(struct proc *, uint64, int);

#endif
