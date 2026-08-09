#ifndef __AGENT_OBSERVE_INTERNAL_H__
#define __AGENT_OBSERVE_INTERNAL_H__

#include "agent_evidence_ring.h"
#include "agent_internal.h"

#define AGENT_OBSERVE_AUDIT_SCOPE_LIMIT 128
#define AGENT_AUDIT_KIND_SLOT_COUNT 6U

/* 单个作用域的有序审计索引只读快照。 */
struct agent_observe_audit_view {
	uint scope_id;
	uint visible_records;
	uint legacy_visible_records;
	uint evidence_visible_records;
	uint64 total_records;
	uint64 admission_drops;
	uint64 ledger_hash;
	uint64 kind_counts[AGENT_AUDIT_KIND_SLOT_COUNT];
	uint64 observe_epoch;
	ushort sequence_slots[AGENT_OBSERVE_AUDIT_SCOPE_LIMIT];
	ushort timeline_slots[AGENT_OBSERVE_AUDIT_SCOPE_LIMIT];
	struct agent_evidence_view evidence;
};

/* 该状态驻留内核栈，仅在所属线程休眠时发布。 */
struct agent_timeline_wait_state {
	struct agent_timeline_filter filter;
	uint64 thread_generation;
	uint64 observe_epoch;
	uint64 deadline;
	uint scope_id;
	int deadline_valid;
};

struct agent_observe_receipt_view {
	uint64 receipt_id;
	uint64 evidence_ticket;
	uint state;
};

void agent_observe_ledger_init(void);
uint agent_observe_audit_scope_visible_locked(uint);
int agent_observe_audit_view_open_locked(
	uint, struct agent_observe_audit_view *);
int agent_observe_audit_view_record_locked(
	const struct agent_observe_audit_view *, uint, int,
	struct agent_audit_record *, uint64 *);
int agent_observe_audit_view_record_source_locked(
	const struct agent_observe_audit_view *, uint, int,
	struct agent_audit_record *, uint64 *, int *);
int agent_observe_receipt_status(
	uint, struct workflow_lifecycle_key, uint64, uint64, uint64,
	uint64 *, uint *);
int agent_observe_receipt_persist(uint);
int agent_observe_recording_suppress_begin(struct proc *);
void agent_observe_recording_suppress_end(struct proc *);
int agent_observe_recording_suppressed(struct proc *);
uint64 agent_observe_scope_epoch_advance_locked(uint);

uint64 agent_observe_alloc_audit_sequence(void);
int agent_observe_ledger_record_context(
	struct proc *, struct agent_context_record *, uint64, int, uint64, int,
	int, uint64);
void agent_observe_ledger_record_sched(
	struct proc *, struct agent_sched_record *);
void agent_observe_ledger_record_event(
	int, struct proc *, struct agent_event *, uint64, uint64);
void agent_observe_ledger_record_effect(
	struct proc *, int, int, char *, uint64, uint64, uint64, uint64, int);

int agent_observe_timeline_waiter_publish(
	struct thread *, struct agent_timeline_wait_state *);
void agent_observe_timeline_waiter_unpublish(
	struct thread *, struct agent_timeline_wait_state *);
int agent_observe_timeline_waiter_wake(struct thread *);
int agent_observe_timeline_source_enabled(
	struct agent_timeline_filter *, int);
int agent_observe_timeline_match(
	struct agent_timeline_filter *, struct agent_timeline_record *);
void agent_observe_timeline_publish_locked(
	uint, struct agent_timeline_record *, uint64);
void agent_observe_timeline_from_audit(
	struct agent_audit_record *, struct agent_timeline_record *);
void agent_observe_timeline_record_context(
	struct proc *, struct agent_context_record *);
void agent_observe_timeline_record_sched(
	struct proc *, struct agent_sched_record *);
void agent_observe_timeline_record_audit(
	uint, struct agent_audit_record *, uint64);

#ifdef AGENT_OBSERVE_TEST_PROFILE
#define AGENT_OBSERVE_TEST_TIMELINE_WINDOW 0x81000001U
#define AGENT_OBSERVE_TEST_TIMELINE_RECHECK 0x81000002U
int agent_observe_test_evict_checkpoint_window(struct proc *);
int agent_observe_test_allocate_identity_ids(
	struct agent_observe_test_identity_ids *);
int agent_observe_test_drop_audit(struct proc *, uint, int, int, int, int);
void agent_observe_test_drop_only_captured(uint, uint64, uint64);
int agent_observe_test_execute(
	struct agent_observe_recovery_request *, uint64, uint64 *, uint *, int *);
#endif

#endif
