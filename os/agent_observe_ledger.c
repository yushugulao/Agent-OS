#include "agent_context.h"
#include "agent_identity_lease.h"
#include "agent_internal.h"
#include "agent_observe_store.h"
#include "defs.h"
#include "kernel_work.h"
#include "timer.h"
#include "trap.h"
#include "vfs_security.h"
#include "agent_observe_internal.h"

#define AGENT_OBSERVE_QUERY_WORK_GRANULE 16U
#define AGENT_AUDIT_SCOPE_LIMIT AGENT_OBSERVE_AUDIT_SCOPE_LIMIT
#define AGENT_AUDIT_LOW_SCOPE_LIMIT (AGENT_AUDIT_SCOPE_LIMIT / 2)
#define AGENT_AUDIT_HIGH_SCOPE_LIMIT (AGENT_AUDIT_SCOPE_LIMIT - AGENT_AUDIT_LOW_SCOPE_LIMIT)
#define AGENT_AUDIT_SCOPE_PRINCIPALS (PROC_RESERVED_SLOTS / VFS_SCOPE_MAX_ACTIVE)
#define AGENT_AUDIT_LOW_PRINCIPAL_RESERVE \
	(AGENT_AUDIT_LOW_SCOPE_LIMIT / AGENT_AUDIT_SCOPE_PRINCIPALS)
#define AGENT_AUDIT_LOW_PRINCIPAL_LIMIT \
	(2 * AGENT_AUDIT_LOW_PRINCIPAL_RESERVE)
#define AGENT_AUDIT_HIGH_PRINCIPAL_LIMIT \
	(AGENT_AUDIT_HIGH_SCOPE_LIMIT / AGENT_AUDIT_SCOPE_PRINCIPALS)
#define AGENT_AUDIT_PRINCIPAL_SCAN_LIMIT AGENT_AUDIT_LOW_PRINCIPAL_LIMIT
#define AGENT_AUDIT_CAUSAL_ID_RESERVE AGENT_AUDIT_LOW_SCOPE_LIMIT
#define AGENT_PREFETCH_SCOPE_LIMIT (AGENT_FILE_PREFETCH_SPAN_MAX / 4)

extern struct proc pool[NPROC];

_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_AUDIT_SCOPE_LIMIT <=
	       AGENT_AUDIT_MAX_RECORDS,
	       "audit table must reserve every workflow partition");
_Static_assert(AGENT_AUDIT_LOW_PRINCIPAL_LIMIT <=
		       AGENT_AUDIT_LOW_SCOPE_LIMIT &&
	       AGENT_AUDIT_LOW_PRINCIPAL_LIMIT ==
		       AGENT_AUDIT_LOW_PRINCIPAL_MAX &&
	       AGENT_AUDIT_LOW_PRINCIPAL_RESERVE > AGENT_AUDIT_KIND_PREFETCH &&
	       AGENT_AUDIT_LOW_PRINCIPAL_LIMIT >
		       AGENT_OBSERVE_CHECKPOINT_PER_SCOPE &&
	       PROC_RESERVED_SLOTS % VFS_SCOPE_MAX_ACTIVE == 0 &&
	       AGENT_AUDIT_LOW_SCOPE_LIMIT %
		       AGENT_AUDIT_SCOPE_PRINCIPALS == 0 &&
	       AGENT_AUDIT_HIGH_SCOPE_LIMIT %
		       AGENT_AUDIT_SCOPE_PRINCIPALS == 0 &&
	       AGENT_AUDIT_HIGH_PRINCIPAL_LIMIT <=
		       AGENT_AUDIT_HIGH_SCOPE_LIMIT &&
	       AGENT_AUDIT_HIGH_PRINCIPAL_LIMIT <=
		       AGENT_AUDIT_PRINCIPAL_SCAN_LIMIT &&
	       AGENT_AUDIT_LOW_SCOPE_LIMIT < AGENT_AUDIT_SCOPE_LIMIT,
	       "audit table must reserve privileged workflow evidence");
_Static_assert(AGENT_AUDIT_CAUSAL_ID_RESERVE <=
	       AGENT_IDENTITY_LEASE_LOW_WATER &&
	       AGENT_AUDIT_CAUSAL_ID_RESERVE < AGENT_IDENTITY_LEASE_CHUNK,
	       "audit lease must reserve one complete causal partition");
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_PREFETCH_SCOPE_LIMIT <=
	       AGENT_FILE_PREFETCH_SPAN_MAX,
	       "prefetch table must reserve every workflow partition");
_Static_assert(sizeof(struct agent_observe_checkpoint) <= 8U * 1024U,
	       "observation checkpoint must remain a bounded durable section");
_Static_assert(KERNEL_WORK_OPERATION_UNITS <= KERNEL_WORK_BUDGET_UNITS,
	       "observation query work must fit one scheduling checkpoint");

struct agent_audit_scope_state {
	int used;
	uint scope_id;
	uint visible_records;
	uint64 total_records;
	uint64 admission_drops;
	uint64 ledger_hash;
	uint64 kind_counts[AGENT_AUDIT_KIND_PREFETCH + 1];
	uint64 observe_epoch;
	ushort sequence_slots[AGENT_AUDIT_SCOPE_LIMIT];
	ushort timeline_slots[AGENT_AUDIT_SCOPE_LIMIT];
};

enum agent_audit_receipt_state_kind {
	AGENT_AUDIT_RECEIPT_NONE = 0,
	AGENT_AUDIT_RECEIPT_PENDING,
	AGENT_AUDIT_RECEIPT_FAILED,
	AGENT_AUDIT_RECEIPT_RECOVERED,
};

enum agent_audit_identity_class {
	AGENT_AUDIT_ID_TELEMETRY = AGENT_OBSERVE_IDENTITY_TELEMETRY,
	AGENT_AUDIT_ID_CAUSAL = AGENT_OBSERVE_IDENTITY_CAUSAL,
	AGENT_AUDIT_ID_AUTHORITY = AGENT_OBSERVE_IDENTITY_AUTHORITY,
};

struct agent_audit_receipt_state {
	uint64 receipt_id;
	uint64 persist_target;
	uint state;
};

static uint64 next_span_id;
static uint64 next_event_id;
static uint64 agent_audit_next_sequence;
static uint64 agent_audit_head;
static uint64 agent_audit_count;
static uint64 agent_audit_ledger_hash;
static uint64 agent_observe_checkpoint_generation;
static uint64 agent_audit_kind_counts[AGENT_AUDIT_KIND_PREFETCH + 1];
static struct agent_audit_record agent_audit_records[AGENT_AUDIT_MAX_RECORDS];
static uint agent_audit_scopes[AGENT_AUDIT_MAX_RECORDS];
static uint64 agent_audit_principals[AGENT_AUDIT_MAX_RECORDS];
static uint64 agent_audit_span_owners[AGENT_AUDIT_MAX_RECORDS];
static uchar agent_audit_low_class[AGENT_AUDIT_MAX_RECORDS];
static uchar agent_audit_identity_classes[AGENT_AUDIT_MAX_RECORDS];
static struct agent_audit_receipt_state agent_audit_receipts[AGENT_AUDIT_MAX_RECORDS];
static struct agent_audit_scope_state agent_audit_scope_states[NPROC];
static uint64 agent_span_prefetch_next_sequence;
static uint64 agent_span_prefetch_head;
static uint64 agent_span_prefetch_count;
static struct agent_file_prefetch_hint agent_span_prefetch_hints[AGENT_FILE_PREFETCH_SPAN_MAX];
static uint agent_span_prefetch_scopes[AGENT_FILE_PREFETCH_SPAN_MAX];
static uint64 agent_span_prefetch_owners[AGENT_FILE_PREFETCH_SPAN_MAX];
static int agent_timeline_waiting_threads;

static int
agent_observe_scope_valid(uint scope_id)
{
	return scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
	       scope_id < FS_OWNER_SCOPE_FLAG;
}

static uint64
agent_observe_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static uint64
agent_observe_hash_mix(uint64 h, uint64 v)
{
	for (int i = 0; i < 8; i++) {
		h ^= (uchar)(v & 0xff);
		h *= 1099511628211ULL;
		v >>= 8;
	}
	return h;
}

static uint64
agent_observe_hash_bytes(uint64 h, char *buf, int n)
{
	for (int i = 0; i < n; i++) {
		h ^= (uchar)buf[i];
		h *= 1099511628211ULL;
	}
	return h;
}

static uint64
agent_audit_receipt_id(const struct agent_audit_record *record, uint scope_id,
		       uint64 serial, uint64 target)
{
	uint64 h = 1469598103934665603ULL;

	h = agent_observe_hash_mix(h, 0x4155444954524350ULL);
	h = agent_observe_hash_mix(h, scope_id);
	h = agent_observe_hash_mix(h, record->workflow_lifecycle_id);
	h = agent_observe_hash_mix(
		h, record->workflow_lifecycle_generation);
	h = agent_observe_hash_mix(h, record->sequence);
	h = agent_observe_hash_mix(h, record->record_hash);
	h = agent_observe_hash_mix(h, serial);
	h = agent_observe_hash_mix(h, target);
	return h ? h : 1;
}

static uint64
agent_observe_alloc_id(uint64 *next, uint kind, uint64 reserve)
{
	int enabled = intr_save();
	uint64 id = *next;
	uint64 following;

	if (id == 0) {
		intr_restore(enabled);
		return 0;
	}
	if (agent_identity_lease_allocator_admit(kind, id, reserve)) {
		following = id == ~0ULL ? 0 : id + 1;
		*next = following;
		intr_restore(enabled);
		agent_identity_lease_allocator_note_next(kind, following);
		return id;
	}
	intr_restore(enabled);
	(void)agent_identity_lease_allocator_renew(kind);
	return 0;
}

void
agent_observe_ledger_init(void)
{
	next_span_id = 1;
	next_event_id = 1;
	agent_audit_next_sequence = 1;
	agent_audit_head = 0;
	agent_audit_count = 0;
	agent_audit_ledger_hash = 0;
	agent_observe_checkpoint_generation = 1;
	memset(agent_audit_kind_counts, 0, sizeof(agent_audit_kind_counts));
	memset(agent_audit_records, 0, sizeof(agent_audit_records));
	memset(agent_audit_scopes, 0, sizeof(agent_audit_scopes));
	memset(agent_audit_principals, 0, sizeof(agent_audit_principals));
	memset(agent_audit_span_owners, 0, sizeof(agent_audit_span_owners));
	memset(agent_audit_low_class, 0, sizeof(agent_audit_low_class));
	memset(agent_audit_identity_classes, 0,
	       sizeof(agent_audit_identity_classes));
	memset(agent_audit_receipts, 0, sizeof(agent_audit_receipts));
	memset(agent_audit_scope_states, 0,
	       sizeof(agent_audit_scope_states));
	agent_span_prefetch_next_sequence = 1;
	agent_span_prefetch_head = 0;
	agent_span_prefetch_count = 0;
	memset(agent_span_prefetch_hints, 0,
	       sizeof(agent_span_prefetch_hints));
	memset(agent_span_prefetch_scopes, 0,
	       sizeof(agent_span_prefetch_scopes));
	memset(agent_span_prefetch_owners, 0,
	       sizeof(agent_span_prefetch_owners));
	agent_timeline_waiting_threads = 0;
}

uint64 agent_observe_alloc_span_id(void)
{
	return agent_observe_alloc_id(
		&next_span_id, AGENT_IDENTITY_ALLOCATOR_SPAN, 0);
}

uint64
agent_observe_alloc_event_id(void)
{
	return agent_observe_alloc_id(
		&next_event_id, AGENT_IDENTITY_ALLOCATOR_EVENT, 0);
}

#ifdef AGENT_OBSERVE_TEST_PROFILE
int
agent_observe_test_allocate_identity_ids(struct agent_observe_test_identity_ids *ids)
{
	uint64 lifecycle_generation = 0;
	uint lifecycle_slot = 0;

	if (ids == 0)
		return -1;
	memset(ids, 0, sizeof(*ids));
	ids->audit_sequence = agent_observe_alloc_id(&agent_audit_next_sequence,
		AGENT_IDENTITY_ALLOCATOR_AUDIT, 0);
	ids->span_id = agent_observe_alloc_span_id();
	ids->event_id = agent_observe_alloc_event_id();
	ids->control_id = agent_lifecycle_alloc_control_id();
	ids->agent_id = agent_identity_alloc_id();
	if (workflow_lifecycle_test_consume_generation(
		    &lifecycle_slot, &lifecycle_generation) < 0 ||
	    ids->audit_sequence == 0 || ids->span_id == 0 ||
	    ids->event_id == 0 || ids->control_id == 0 || ids->agent_id == 0)
		return -1;
	ids->lifecycle_slot = lifecycle_slot;
	ids->lifecycle_generation = lifecycle_generation;
	return 0;
}
#endif

int
agent_observe_query_reserve(uint64 records)
{
	struct proc *p = curr_proc();
	uint64 batches;
	uint64 checkpoint_batches;
	uint64 max_checkpoint_batches;
	int result = 0;

	if (records == 0)
		return 0;
	/* Introspection must yield fairly without evicting the evidence it reads. */
	if (agent_observe_recording_suppress_begin(p) < 0)
		return -1;
	batches = (records + AGENT_OBSERVE_QUERY_WORK_GRANULE - 1) /
		  AGENT_OBSERVE_QUERY_WORK_GRANULE;
	max_checkpoint_batches = KERNEL_WORK_BUDGET_UNITS /
				 KERNEL_WORK_OPERATION_UNITS;
	while (batches > 0) {
		checkpoint_batches = batches < max_checkpoint_batches ?
					     batches :
					     max_checkpoint_batches;
		if (kernel_work_checkpoint(
			    (uint)(checkpoint_batches *
				   KERNEL_WORK_OPERATION_UNITS)) < 0) {
			result = -1;
			break;
		}
		batches -= checkpoint_batches;
	}
	agent_observe_recording_suppress_end(p);
	return result;
}

int
agent_observe_query_reserve_to(uint64 records, uint64 *reserved)
{
	uint64 additional;

	if (reserved == 0 || records <= *reserved)
		return 0;
	additional = records - *reserved;
	/* Publish before a possible yield; callers recount after checkpointing. */
	*reserved = records;
	return agent_observe_query_reserve(additional);
}

/* Authoritative observation state and operations. */

/* Audit record hashing. */
static uint64 agent_audit_record_hash(struct agent_audit_record *record)
{
	uint64 h = 1469598103934665603ULL;

	h = agent_observe_hash_mix(h, record->prev_hash);
	h = agent_observe_hash_mix(h, record->sequence);
	h = agent_observe_hash_mix(h, record->tick);
	h = agent_observe_hash_mix(h, record->cause_sequence);
	h = agent_observe_hash_mix(h, record->span_id);
	h = agent_observe_hash_mix(h, record->workflow_lifecycle_generation);
	h = agent_observe_hash_mix(h, record->branch_generation);
	h = agent_observe_hash_mix(h, record->cause_branch_generation);
	h = agent_observe_hash_mix(h, record->actor_control_id);
	h = agent_observe_hash_mix(h, record->cause_control_id);
	h = agent_observe_hash_mix(h, record->cause_record_hash);
	h = agent_observe_hash_mix(h, record->value0);
	h = agent_observe_hash_mix(h, record->value1);
	h = agent_observe_hash_mix(h, record->value2);
	h = agent_observe_hash_mix(h, record->flags);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->kind);
	h = agent_observe_hash_mix(h, record->workflow_lifecycle_id);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->pid);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->tid);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->source_pid);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->target_pid);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->agent_id);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->role);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->loop_state);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->tool_id);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->event_type);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->status);
	h = agent_observe_hash_bytes(h, record->text, sizeof(record->text));
	return h ? h : 1;
}



/* Audit storage and recording. */
static struct agent_audit_scope_state *
agent_observe_audit_scope_state_get(uint scope_id, int create)
{
	struct agent_audit_scope_state *free_state = 0;

	if (!agent_observe_scope_valid(scope_id))
		return 0;
	for (int i = 0; i < NPROC; i++) {
		if (agent_audit_scope_states[i].used &&
		    agent_audit_scope_states[i].scope_id == scope_id)
			return &agent_audit_scope_states[i];
		if (!agent_audit_scope_states[i].used && free_state == 0)
			free_state = &agent_audit_scope_states[i];
	}
	if (!create || free_state == 0)
		return 0;
	memset(free_state, 0, sizeof(*free_state));
	free_state->used = 1;
	free_state->scope_id = scope_id;
	free_state->observe_epoch = 1;
	return free_state;
}

int
agent_observe_audit_view_open_locked(
	uint scope_id, struct agent_observe_audit_view *view)
{
	struct agent_audit_scope_state *state;

	if (view == 0)
		return -1;
	memset(view, 0, sizeof(*view));
	state = agent_observe_audit_scope_state_get(scope_id, 0);
	if (state == 0)
		return 0;
	view->scope_id = state->scope_id;
	view->visible_records = state->visible_records;
	view->total_records = state->total_records;
	view->ledger_hash = state->ledger_hash;
	view->observe_epoch = state->observe_epoch;
	memmove(view->kind_counts, state->kind_counts,
		sizeof(view->kind_counts));
	memmove(view->sequence_slots, state->sequence_slots,
		sizeof(view->sequence_slots));
	memmove(view->timeline_slots, state->timeline_slots,
		sizeof(view->timeline_slots));
	return 1;
}

uint
agent_observe_audit_scope_visible_locked(uint scope_id)
{
	struct agent_audit_scope_state *state;

	state = agent_observe_audit_scope_state_get(scope_id, 0);
	return state == 0 ? 0 : state->visible_records;
}

int
agent_observe_audit_view_record_locked(
	const struct agent_observe_audit_view *view, uint index,
	int timeline_order, struct agent_audit_record *out,
	uint64 *span_owner)
{
	int slot;

	if (view == 0 || index >= view->visible_records ||
	    view->visible_records > AGENT_AUDIT_SCOPE_LIMIT)
		return 0;
	slot = timeline_order ? view->timeline_slots[index] :
				view->sequence_slots[index];
	if (slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS ||
	    agent_audit_scopes[slot] != view->scope_id)
		return 0;
	if (out != 0)
		*out = agent_audit_records[slot];
	if (span_owner != 0)
		*span_owner = agent_audit_span_owners[slot];
	return 1;
}

static int
agent_observe_receipt_snapshot(
	uint scope_id, struct workflow_lifecycle_key lifecycle, uint64 sequence,
	uint64 record_hash, uint64 supplied_receipt,
	struct agent_observe_receipt_view *view)
{
	struct agent_audit_scope_state *scope;
	int status = supplied_receipt == 0 ? AGENT_STATUS_NOT_FOUND :
					      AGENT_STATUS_STALE;
	int enabled;

	if (view == 0)
		return AGENT_STATUS_BAD_PARAM;
	memset(view, 0, sizeof(*view));
	enabled = intr_save();
	scope = agent_observe_audit_scope_state_get(scope_id, 0);
	for (uint i = 0; scope != 0 && i < scope->visible_records; i++) {
		int slot = scope->sequence_slots[i];
		const struct agent_audit_record *record;
		const struct agent_audit_receipt_state *receipt;

		if (slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS ||
		    agent_audit_scopes[slot] != scope_id)
			continue;
		record = &agent_audit_records[slot];
		if (record->sequence != sequence)
			continue;
		if (record->workflow_lifecycle_id != lifecycle.id ||
		    record->workflow_lifecycle_generation != lifecycle.generation ||
		    record->record_hash != record_hash) {
			status = AGENT_STATUS_STALE;
			break;
		}
		receipt = &agent_audit_receipts[slot];
		if (receipt->receipt_id == 0 ||
		    receipt->state == AGENT_AUDIT_RECEIPT_NONE) {
			status = AGENT_STATUS_INDETERMINATE;
			break;
		}
		if (supplied_receipt != 0 &&
		    supplied_receipt != receipt->receipt_id) {
			status = AGENT_STATUS_STALE;
			break;
		}
		view->receipt_id = receipt->receipt_id;
		view->persist_target = receipt->persist_target;
		view->state = receipt->state;
		status = AGENT_STATUS_OK;
		break;
	}
	intr_restore(enabled);
	return status;
}

int
agent_observe_receipt_status(
	uint scope_id, struct workflow_lifecycle_key lifecycle, uint64 sequence,
	uint64 record_hash, uint64 supplied_receipt, uint64 *receipt_id,
	uint *durability)
{
	struct agent_observe_receipt_view before;
	struct agent_observe_receipt_view after;
	int persisted;
	int status;

	if (receipt_id == 0 || durability == 0)
		return AGENT_STATUS_BAD_PARAM;
	*receipt_id = 0;
	*durability = AGENT_AUDIT_DURABILITY_NOT_FOUND;
	status = agent_observe_receipt_snapshot(
		scope_id, lifecycle, sequence, record_hash, supplied_receipt,
		&before);
	if (status != AGENT_STATUS_OK) {
		if (supplied_receipt != 0 &&
		    agent_obsstore_receipt_record_status(
			    scope_id, lifecycle, sequence, record_hash,
			    supplied_receipt, 0) > 0) {
			*receipt_id = supplied_receipt;
			*durability = AGENT_AUDIT_DURABILITY_DURABLE;
			return AGENT_STATUS_OK;
		}
		return status;
	}
	*receipt_id = before.receipt_id;
	if (before.state == AGENT_AUDIT_RECEIPT_FAILED) {
		*durability = AGENT_AUDIT_DURABILITY_FAILED;
		return AGENT_STATUS_OK;
	}
	if (before.state != AGENT_AUDIT_RECEIPT_PENDING &&
	    before.state != AGENT_AUDIT_RECEIPT_RECOVERED) {
		*durability = AGENT_AUDIT_DURABILITY_FAILED;
		return AGENT_STATUS_INDETERMINATE;
	}
	persisted = agent_obsstore_receipt_record_status(
		scope_id, lifecycle, sequence, record_hash, before.receipt_id,
		before.persist_target);
	status = agent_observe_receipt_snapshot(
		scope_id, lifecycle, sequence, record_hash, before.receipt_id,
		&after);
	if (status != AGENT_STATUS_OK)
		return status;
	if (after.receipt_id != before.receipt_id ||
	    after.persist_target != before.persist_target ||
	    after.state != before.state)
		return AGENT_STATUS_STALE;
	*durability = persisted > 0 ? AGENT_AUDIT_DURABILITY_DURABLE :
		      persisted < 0 ? AGENT_AUDIT_DURABILITY_FAILED :
			      AGENT_AUDIT_DURABILITY_PENDING;
	return AGENT_STATUS_OK;
}

int
agent_observe_recording_suppress_begin(struct proc *p)
{
	struct thread *t = curr_thread();
	int enabled = intr_save();

	if (p == 0 || t == 0 || t->process != p || t->state != RUNNING ||
	    t->agent_observe_suppress_depth == (uchar)~0U)
		goto fail;
	t->agent_observe_suppress_depth++;
	intr_restore(enabled);
	return 0;

fail:
	intr_restore(enabled);
	return -1;
}

void
agent_observe_recording_suppress_end(struct proc *p)
{
	struct thread *t = curr_thread();
	int enabled = intr_save();

	if (p == 0 || t == 0 || t->process != p || t->state != RUNNING ||
	    t->agent_observe_suppress_depth == 0)
		panic("observation suppression underflow");
	t->agent_observe_suppress_depth--;
	intr_restore(enabled);
}

int
agent_observe_receipt_persist(uint scope_id)
{
	struct proc *p = curr_proc();
	int status;

	if (agent_observe_recording_suppress_begin(p) < 0)
		return -1;
	status = agent_obsstore_receipt_persist(scope_id);
	agent_observe_recording_suppress_end(p);
	return status;
}

int
agent_observe_recording_suppressed(struct proc *p)
{
	struct thread *t = curr_thread();

	return p != 0 && t != 0 && t->process == p &&
	       t->agent_observe_suppress_depth != 0;
}

uint64
agent_observe_scope_epoch_advance_locked(uint scope_id)
{
	struct agent_audit_scope_state *state;

	state = agent_observe_audit_scope_state_get(scope_id, 1);
	if (state == 0)
		return 0;
	state->observe_epoch++;
	if (state->observe_epoch == 0)
		state->observe_epoch = 1;
	return state->observe_epoch;
}

int
agent_observe_timeline_waiter_publish(
	struct thread *t, struct agent_timeline_wait_state *state)
{
	struct proc *p;
	int enabled = intr_save();
	if (t == 0 || state == 0 || t != curr_thread() ||
	    t->state != RUNNING || (p = t->process) == 0 ||
	    t->agent_timeline_wait_state != 0 || t->identity_generation == 0 ||
	    state->thread_generation != t->identity_generation ||
	    state->scope_id != agent_identity_proc_scope(p)) {
		intr_restore(enabled);
		return -1;
	}
	t->agent_timeline_wait_state = state;
	agent_timeline_waiting_threads++;
	agent_identity_thread_loop_set(t, AGENT_LOOP_WAITING);
	intr_restore(enabled);
	return 0;
}

void
agent_observe_timeline_waiter_unpublish(
	struct thread *t, struct agent_timeline_wait_state *state)
{
	struct proc *p;
	int enabled = intr_save();
	if (t != 0 && state != 0 && t->agent_timeline_wait_state == state) {
		p = t->process;
		t->agent_timeline_wait_state = 0;
		if (agent_timeline_waiting_threads <= 0)
			panic("timeline waiter count");
		agent_timeline_waiting_threads--;
		if (p != 0)
			agent_identity_thread_loop_set(t, AGENT_LOOP_IDLE);
	}
	intr_restore(enabled);
}

int
agent_observe_timeline_waiter_wake(struct thread *t)
{
	struct agent_timeline_wait_state *state;
	struct proc *p;
	int enabled = intr_save();
	int woken = 0;
	if (t != 0 && (p = t->process) != 0 &&
	    (state = t->agent_timeline_wait_state) != 0 &&
	    state->thread_generation == t->identity_generation)
		woken = wait_queue_wake_key_all(
			&p->agent_timeline_waiters, state->thread_generation);
	intr_restore(enabled);
	return woken;
}

void
agent_observe_thread_reset(struct thread *t)
{
	struct agent_timeline_wait_state *state;
	struct proc *p;
	int enabled = intr_save();

	if (t == 0) {
		intr_restore(enabled);
		return;
	}
	if (t->agent_observe_suppress_depth != 0)
		panic("observation suppression active");
	t->agent_observe_suppress_depth = 0;
	state = t->agent_timeline_wait_state;
	if (state == 0) {
		intr_restore(enabled);
		return;
	}
	p = t->process;
	t->agent_timeline_wait_state = 0;
	if (agent_timeline_waiting_threads <= 0)
		panic("timeline waiter reset count");
	agent_timeline_waiting_threads--;
	if (p != 0 && t->wait_channel == &p->agent_timeline_waiters)
		wait_queue_cancel(t);
	memset(state, 0, sizeof(*state));
	if (p != 0)
		agent_identity_thread_loop_set(t, AGENT_LOOP_IDLE);
	intr_restore(enabled);
}

int
agent_observe_timeline_source_enabled(
	struct agent_timeline_filter *filter, int source)
{
	if (filter == 0 ||
	    (filter->flags & AGENT_TIMELINE_FILTER_SOURCE_MASK) == 0)
		return 1;
	if (source <= 0 || source >= 64)
		return 0;
	return (filter->source_mask & (1ULL << source)) != 0;
}

static int
agent_observe_timeline_after_cursor(
	struct agent_timeline_filter *filter,
	struct agent_timeline_record *record)
{
	if (record->tick > filter->after_tick)
		return 1;
	if (record->tick < filter->after_tick)
		return 0;
	if (record->source > filter->after_source)
		return 1;
	if (record->source < filter->after_source)
		return 0;
	return record->sequence > filter->after_sequence;
}

int
agent_observe_timeline_match(
	struct agent_timeline_filter *filter,
	struct agent_timeline_record *record)
{
	if (filter == 0 || filter->flags == 0)
		return 1;
	if (!agent_observe_timeline_source_enabled(filter, record->source))
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_START_TICK) &&
	    record->tick < filter->start_tick)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_AFTER_CURSOR) &&
	    !agent_observe_timeline_after_cursor(filter, record))
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_SPAN_ID) &&
	    record->span_id != filter->span_id)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_KIND) &&
	    record->kind != filter->kind)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_PID) &&
	    record->pid != filter->pid)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_SOURCE_PID) &&
	    record->source_pid != filter->source_pid)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_TARGET_PID) &&
	    record->target_pid != filter->target_pid)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_ROLE) &&
	    record->role != filter->role)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_TOOL_ID) &&
	    record->tool_id != filter->tool_id)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_EVENT_TYPE) &&
	    record->event_type != filter->event_type)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_STATUS) &&
	    record->status != filter->status)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_FLAGS_ALL) &&
	    (record->flags & filter->require_flags) != filter->require_flags)
		return 0;
	return 1;
}

static int
agent_observe_record_visible(struct proc *p,
			     struct agent_timeline_record *record,
			     uint64 span_owner)
{
	if (p == 0 || record == 0 || !p->is_agent)
		return 0;
	if (record->source == AGENT_TIMELINE_SOURCE_AUDIT) {
		if (agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE))
			return 1;
		return agent_identity_has_cap(p, AGENT_CAP_AUDIT_WRITE) &&
		       record->span_id != 0 &&
		       record->span_id == p->agent_current_span_id &&
		       span_owner != 0 &&
		       span_owner == p->agent_current_span_owner;
	}
	if (record->source == AGENT_TIMELINE_SOURCE_CONTEXT ||
	    record->source == AGENT_TIMELINE_SOURCE_SCHED)
		return record->pid == p->pid;
	if (record->source == AGENT_TIMELINE_SOURCE_PREFETCH)
		return agent_identity_has_cap(p, AGENT_CAP_META_READ) &&
		       record->target_pid == p->pid;
	return 0;
}

void
agent_observe_timeline_publish_locked(
	uint scope_id, struct agent_timeline_record *record,
	uint64 span_owner)
{
	uint64 observe_epoch;

	if (record == 0)
		return;
	observe_epoch = agent_observe_scope_epoch_advance_locked(scope_id);
	if (observe_epoch == 0 || agent_timeline_waiting_threads <= 0)
		return;
	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (p->state == P_UNUSED || !p->is_agent ||
		    agent_identity_proc_scope(p) != scope_id)
			continue;
		if (!agent_observe_record_visible(p, record, span_owner))
			continue;
		for (int tid = 0; tid < NTHREAD; tid++) {
			struct thread *t = &p->threads[tid];
			struct agent_timeline_wait_state *state =
				t->agent_timeline_wait_state;

			if (state == 0 || state->scope_id != scope_id ||
			    state->thread_generation != t->identity_generation ||
			    !agent_observe_timeline_match(&state->filter, record))
				continue;
			if (agent_observe_timeline_waiter_wake(t) > 0) {
				state->observe_epoch = observe_epoch;
				p->agent_observe_epoch = observe_epoch;
				p->agent_timeline_wait_wakeup_count++;
			}
		}
	}
}

uint64
agent_observe_prefetch_scope_count_locked(uint scope_id)
{
	uint64 count = 0;

	for (int i = 0; i < AGENT_FILE_PREFETCH_SPAN_MAX; i++)
		if (agent_span_prefetch_scopes[i] == scope_id)
			count++;
	return count;
}

uint64 agent_observe_scope_epoch(uint scope_id)
{
	struct agent_audit_scope_state *state;

	state = agent_observe_audit_scope_state_get(scope_id, 1);
	return state ? state->observe_epoch : 0;
}

#define AGENT_AUDIT_PRIVILEGED_CAPS \
	(AGENT_CAP_ACTION_WRITE | AGENT_CAP_ARTIFACT_WRITE | \
	 AGENT_CAP_META_WRITE | AGENT_CAP_ORCHESTRATE | AGENT_CAP_LLM_RELAY | \
	 AGENT_CAP_WAIT_CANCEL | AGENT_CAP_ROUTE_MANAGE | \
	 AGENT_CAP_DEPENDENCY_UPDATE)

static int
agent_audit_live_principals(
	uint scope_id, uint64 principals[AGENT_AUDIT_SCOPE_PRINCIPALS],
	uint *principal_count)
{
	uint count = 0;

	if (principal_count == 0)
		return -1;
	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		uint64 principal;
		int known = 0;

		if (!proc_teardown_live(p) || !p->is_agent ||
		    agent_identity_proc_scope(p) != scope_id ||
		    (principal = p->agent_control_id) == 0)
			continue;
		for (uint i = 0; i < count; i++)
			if (principals[i] == principal) {
				known = 1;
				break;
			}
		if (known)
			continue;
		if (count >= AGENT_AUDIT_SCOPE_PRINCIPALS)
			return -1;
		principals[count++] = principal;
	}
	*principal_count = count;
	return 0;
}

static int
agent_observe_audit_time_before(int left_slot, int right_slot)
{
	struct agent_audit_record *left = &agent_audit_records[left_slot];
	struct agent_audit_record *right = &agent_audit_records[right_slot];

	return left->tick < right->tick ||
	       (left->tick == right->tick &&
		left->sequence < right->sequence);
}

static int
agent_observe_audit_index_remove(struct agent_audit_scope_state *state,
				 int slot)
{
	uint visible;
	int sequence_pos = -1;
	int timeline_pos = -1;

	if (state == 0 || slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS)
		return -1;
	visible = state->visible_records;
	for (uint i = 0; i < visible; i++) {
		if (state->sequence_slots[i] == slot)
			sequence_pos = i;
		if (state->timeline_slots[i] == slot)
			timeline_pos = i;
	}
	if (sequence_pos < 0 || timeline_pos < 0)
		return -1;
	for (uint i = sequence_pos; i + 1 < visible; i++)
		state->sequence_slots[i] = state->sequence_slots[i + 1];
	for (uint i = timeline_pos; i + 1 < visible; i++)
		state->timeline_slots[i] = state->timeline_slots[i + 1];
	if (visible > 0) {
		state->sequence_slots[visible - 1] = 0;
		state->timeline_slots[visible - 1] = 0;
		state->visible_records--;
	}
	return 0;
}

static int
agent_observe_audit_index_insert(struct agent_audit_scope_state *state,
				 int slot)
{
	uint lo;
	uint hi;
	uint pos;
	uint visible;

	if (state == 0 || slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS ||
	    state->visible_records >= AGENT_AUDIT_SCOPE_LIMIT)
		return -1;
	visible = state->visible_records;

	lo = 0;
	hi = visible;
	while (lo < hi) {
		uint mid = lo + (hi - lo) / 2;
		int current = state->sequence_slots[mid];

		if (agent_audit_records[current].sequence <
		    agent_audit_records[slot].sequence)
			lo = mid + 1;
		else
			hi = mid;
	}
	pos = lo;
	for (uint i = visible; i > pos; i--)
		state->sequence_slots[i] = state->sequence_slots[i - 1];
	state->sequence_slots[pos] = slot;

	lo = 0;
	hi = visible;
	while (lo < hi) {
		uint mid = lo + (hi - lo) / 2;
		int current = state->timeline_slots[mid];

		if (agent_observe_audit_time_before(current, slot))
			lo = mid + 1;
		else
			hi = mid;
	}
	pos = lo;
	for (uint i = visible; i > pos; i--)
		state->timeline_slots[i] = state->timeline_slots[i - 1];
	state->timeline_slots[pos] = slot;
	state->visible_records++;
	return 0;
}

static void agent_audit_slot_clear(int slot)
{
	if (slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS)
		return;
	agent_audit_scopes[slot] = VFS_SCOPE_NONE;
	agent_audit_principals[slot] = 0;
	agent_audit_span_owners[slot] = 0;
	agent_audit_low_class[slot] = 0;
	agent_audit_identity_classes[slot] = AGENT_OBSERVE_IDENTITY_TELEMETRY;
	memset(&agent_audit_receipts[slot], 0,
	       sizeof(agent_audit_receipts[slot]));
	memset(&agent_audit_records[slot], 0,
	       sizeof(agent_audit_records[slot]));
}

static int agent_audit_slot_unpublish(int slot)
{
	struct agent_audit_scope_state *state;
	uint scope_id;

	if (slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS)
		return -1;
	scope_id = agent_audit_scopes[slot];
	if (scope_id == VFS_SCOPE_NONE)
		return 0;
	state = agent_observe_audit_scope_state_get(scope_id, 0);
	if (state == 0 || agent_observe_audit_index_remove(state, slot) < 0)
		return -1;
	agent_audit_slot_clear(slot);
	return 0;
}

/*
 * A rolling principal partition must not let high-rate telemetry erase the
 * only causal anchor of the span that telemetry describes.  When the
 * partition is full, correlated records replace spanless telemetry first,
 * then refresh the same causal bucket or another redundant bucket before
 * sacrificing a unique anchor.  Spanless records may replace only spanless
 * records.  This keeps the policy bounded while retaining span/kind diversity.
 */
static int
agent_observe_audit_principal_victim(
	struct agent_audit_scope_state *state, uint64 principal, int low_class,
	uint64 span_id, uint64 span_owner, int kind)
{
	ushort principal_slots[AGENT_AUDIT_PRINCIPAL_SCAN_LIMIT];
	uint principal_slot_count = 0;
	int oldest_spanless = -1;
	int oldest_other_span = -1;
	int oldest_same_kind = -1;
	int oldest_other_same_kind = -1;
	int oldest_same_span = -1;
	int oldest_principal = -1;
	int correlated = span_id != 0 && span_owner != 0;

	for (uint j = 0; j < state->visible_records; j++) {
		int slot = state->sequence_slots[j];
		struct agent_audit_record *record;
		int record_correlated;

		if (slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS ||
		    agent_audit_low_class[slot] != low_class ||
		    agent_audit_principals[slot] != principal)
			continue;
		if (oldest_principal < 0)
			oldest_principal = slot;
		if (principal_slot_count < AGENT_AUDIT_PRINCIPAL_SCAN_LIMIT)
			principal_slots[principal_slot_count++] = slot;
		record = &agent_audit_records[slot];
		record_correlated = record->span_id != 0 &&
			agent_audit_span_owners[slot] != 0;
		if (!record_correlated) {
			if (oldest_spanless < 0)
				oldest_spanless = slot;
			continue;
		}
		if (!correlated || record->span_id != span_id ||
		    agent_audit_span_owners[slot] != span_owner) {
			if (oldest_other_span < 0)
				oldest_other_span = slot;
			if (correlated && record->kind == kind &&
			    oldest_other_same_kind < 0)
				oldest_other_same_kind = slot;
			continue;
		}
		if (oldest_same_span < 0)
			oldest_same_span = slot;
		if (record->kind == kind && oldest_same_kind < 0)
			oldest_same_kind = slot;
	}
	/* Protected authority effects retain the original per-principal FIFO. */
	if (!low_class)
		return oldest_principal;
	if (!correlated)
		return oldest_spanless;
	if (oldest_spanless >= 0)
		return oldest_spanless;
	if (oldest_same_kind >= 0)
		return oldest_same_kind;
	for (uint i = 0; i < principal_slot_count; i++) {
		int left_slot = principal_slots[i];
		struct agent_audit_record *left =
			&agent_audit_records[left_slot];

		if (left->span_id == 0 ||
		    agent_audit_span_owners[left_slot] == 0)
			continue;
		for (uint j = i + 1; j < principal_slot_count; j++) {
			int right_slot = principal_slots[j];
			struct agent_audit_record *right =
				&agent_audit_records[right_slot];

			if (left->span_id == right->span_id &&
			    agent_audit_span_owners[left_slot] ==
				    agent_audit_span_owners[right_slot] &&
			    left->kind == right->kind)
				return left_slot;
		}
	}
	if (oldest_other_same_kind >= 0)
		return oldest_other_same_kind;
	for (uint i = 0; i < principal_slot_count; i++) {
		int left_slot = principal_slots[i];
		struct agent_audit_record *left =
			&agent_audit_records[left_slot];
		uint span_records = 0;

		if (left->span_id == 0 ||
		    agent_audit_span_owners[left_slot] == 0)
			continue;
		for (uint j = 0; j < principal_slot_count; j++) {
			int right_slot = principal_slots[j];

			if (left->span_id == agent_audit_records[right_slot].span_id &&
			    agent_audit_span_owners[left_slot] ==
				    agent_audit_span_owners[right_slot])
				span_records++;
		}
		if (span_records > 1)
			return left_slot;
	}
	return oldest_other_span >= 0 ? oldest_other_span : oldest_same_span;
}

/*
 * A full scope is not permission to steal a live principal's partition.
 * Existing principals roll only their own records.  A principal entering a
 * full scope may reuse a departed principal's record, but spanless telemetry
 * still cannot erase correlated evidence.
 */
static int
agent_observe_audit_departed_principal_victim(
	struct agent_audit_scope_state *state, uint64 principal, int low_class,
	uint64 span_id, uint64 span_owner, int kind)
{
	uint64 live_principals[AGENT_AUDIT_SCOPE_PRINCIPALS];
	uint64 departed_principal = 0;
	uint live_principal_count;
	int oldest_spanless = -1;
	int correlated = span_id != 0 && span_owner != 0;

	if (agent_audit_live_principals(state->scope_id, live_principals,
					&live_principal_count) < 0)
		return -1;

	for (uint j = 0; j < state->visible_records; j++) {
		int slot = state->sequence_slots[j];
		uint64 record_principal;
		int principal_live = 0;

		if (slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS ||
		    agent_audit_low_class[slot] != low_class)
			continue;
		record_principal = agent_audit_principals[slot];
		for (uint i = 0; i < live_principal_count; i++)
			if (live_principals[i] == record_principal) {
				principal_live = 1;
				break;
			}
		if (record_principal == 0 || record_principal == principal ||
		    principal_live)
			continue;
		if (departed_principal == 0)
			departed_principal = record_principal;
		if (low_class &&
		    (agent_audit_records[slot].span_id == 0 ||
		     agent_audit_span_owners[slot] == 0) &&
		    oldest_spanless < 0)
			oldest_spanless = slot;
	}
	if (low_class && oldest_spanless >= 0)
		return oldest_spanless;
	if ((low_class && !correlated) || departed_principal == 0)
		return -1;
	return agent_observe_audit_principal_victim(
		state, departed_principal, low_class, span_id, span_owner, kind);
}

/*
 * Low-class principals may borrow idle slots above their guaranteed share.
 * A newly active principal can reclaim only that borrowed overflow; the
 * guaranteed partition and the causal-victim policy remain intact.
 */
static int
agent_observe_audit_overflow_principal_victim(
	struct agent_audit_scope_state *state, uint64 principal,
	uint64 span_id, uint64 span_owner, int kind)
{
	for (uint i = 0; i < state->visible_records; i++) {
		int slot = state->sequence_slots[i];
		uint64 candidate;
		uint owned = 0;
		int victim;

		if (slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS ||
		    !agent_audit_low_class[slot])
			continue;
		candidate = agent_audit_principals[slot];
		if (candidate == 0 || candidate == principal)
			continue;
		for (uint j = 0; j < state->visible_records; j++) {
			int other = state->sequence_slots[j];

			if (other >= 0 && other < AGENT_AUDIT_MAX_RECORDS &&
			    agent_audit_low_class[other] &&
			    agent_audit_principals[other] == candidate)
				owned++;
		}
		if (owned <= AGENT_AUDIT_LOW_PRINCIPAL_RESERVE)
			continue;
		victim = agent_observe_audit_principal_victim(
			state, candidate, 1, span_id, span_owner, kind);
		if (victim >= 0)
			return victim;
	}
	return -1;
}

static int
agent_observe_audit_slot_alloc(struct agent_audit_scope_state *state,
			       uint64 principal, int low_class,
			       uint64 span_id, uint64 span_owner, int kind)
{
	uint scope_id;
	int high_owned = 0;
	int low_owned = 0;
	int principal_owned = 0;
	int victim;

	if (state == 0 || principal == 0)
		return -1;
	scope_id = state->scope_id;
	for (uint j = 0; j < state->visible_records; j++) {
		int i = state->sequence_slots[j];

		if (i < 0 || i >= AGENT_AUDIT_MAX_RECORDS ||
		    agent_audit_scopes[i] != scope_id)
			return -1;
		if (agent_audit_low_class[i]) {
			low_owned++;
		} else {
			high_owned++;
		}
		if (agent_audit_low_class[i] == low_class &&
		    agent_audit_principals[i] == principal) {
			principal_owned++;
		}
	}
	// The two authority classes have independent rolling partitions. Every
	// principal first rolls its own history, so neither telemetry nor a noisy
	// writer can evict another live principal's workflow evidence.
	if (principal_owned >= (low_class ?
			       AGENT_AUDIT_LOW_PRINCIPAL_LIMIT :
			       AGENT_AUDIT_HIGH_PRINCIPAL_LIMIT))
		return agent_observe_audit_principal_victim(
			state, principal, low_class, span_id, span_owner, kind);
	if (low_class && low_owned >= AGENT_AUDIT_LOW_SCOPE_LIMIT) {
		victim = agent_observe_audit_departed_principal_victim(
			state, principal, low_class, span_id, span_owner, kind);
		if (victim >= 0)
			return victim;
		victim = agent_observe_audit_overflow_principal_victim(
			state, principal, span_id, span_owner, kind);
		if (victim >= 0)
			return victim;
		return principal_owned != 0 ?
			agent_observe_audit_principal_victim(
				state, principal, low_class, span_id, span_owner,
				kind) : -1;
	}
	if (!low_class && high_owned >= AGENT_AUDIT_HIGH_SCOPE_LIMIT) {
		victim = agent_observe_audit_departed_principal_victim(
			state, principal, low_class, span_id, span_owner, kind);
		if (victim >= 0)
			return victim;
		return principal_owned != 0 ?
			agent_observe_audit_principal_victim(
				state, principal, low_class, span_id, span_owner,
				kind) : -1;
	}
	for (int i = 0; i < AGENT_AUDIT_MAX_RECORDS; i++) {
		int slot = (agent_audit_head + i) %
			   AGENT_AUDIT_MAX_RECORDS;

		if (agent_audit_scopes[slot] == VFS_SCOPE_NONE)
			return slot;
	}
	return -1;
}

uint64
agent_observe_checkpoint_record_hash(const struct agent_audit_record *record)
{
	return record == 0 ? 0 :
		agent_audit_record_hash((struct agent_audit_record *)record);
}

int
agent_observe_checkpoint_entry_validate(
	const struct agent_observe_checkpoint_scope *scope, uint index,
	const struct agent_observe_checkpoint_entry *entry,
	const struct agent_observe_checkpoint_entry *prior, int *gap)
{
	const struct agent_audit_record *record;
	uint tail_start;
	int direct;
	int latest_tail;

	if (scope == 0 || entry == 0 || gap == 0 ||
	    scope->record_count == 0 ||
	    scope->record_count > AGENT_OBSERVE_CHECKPOINT_PER_SCOPE ||
	    index >= scope->record_count || (index == 0) != (prior == 0))
		return -1;
	record = &entry->record;
	tail_start = scope->record_count > AGENT_OBSERVE_CHECKPOINT_LATEST_TAIL ?
			     scope->record_count -
				     AGENT_OBSERVE_CHECKPOINT_LATEST_TAIL :
			     0;
	latest_tail = index >= tail_start;
	direct = index != 0 &&
		 record->prev_hash == prior->record.record_hash;
	if (entry->scope_id != scope->scope_id || entry->principal == 0 ||
	    entry->receipt_id == 0 ||
	    entry->identity_class > AGENT_OBSERVE_IDENTITY_MAX ||
	    entry->reserved[0] != 0 || entry->reserved[1] != 0 ||
	    (entry->link_flags & ~AGENT_OBSERVE_LINK_FLAGS_ALL) != 0 ||
	    !!(entry->link_flags & AGENT_OBSERVE_LINK_LATEST_TAIL) !=
		    latest_tail ||
	    (index == 0 &&
	     (entry->link_flags & AGENT_OBSERVE_LINK_PREV_RETAINED)) ||
	    (index != 0 &&
	     !!(entry->link_flags & AGENT_OBSERVE_LINK_PREV_RETAINED) !=
		     direct) ||
	    (index != 0 && record->prev_hash == 0) ||
	    ((record->span_id == 0) != (entry->span_owner == 0)) ||
	    (entry->identity_class == AGENT_OBSERVE_IDENTITY_CAUSAL &&
	     (record->span_id == 0 || entry->span_owner == 0)) ||
	    (entry->identity_class == AGENT_OBSERVE_IDENTITY_AUTHORITY &&
	     (record->actor_control_id == 0 ||
	      entry->principal != record->actor_control_id)) ||
	    record->sequence == 0 ||
	    (prior != 0 && record->sequence <= prior->record.sequence) ||
	    record->workflow_lifecycle_id != scope->lifecycle_id ||
	    record->workflow_lifecycle_generation !=
		    scope->lifecycle_generation ||
	    record->kind < 0 || record->kind > AGENT_AUDIT_KIND_PREFETCH ||
	    record->agent_id < 0 ||
	    record->record_hash != agent_observe_checkpoint_record_hash(record))
		return -1;
	if ((index == 0 && record->prev_hash != 0) ||
	    (index != 0 && !direct))
		*gap = 1;
	return 0;
}

static int
agent_observe_checkpoint_slot_selected(const int *selected, uint count,
				       int slot)
{
	for (uint i = 0; i < count; i++)
		if (selected[i] == slot)
			return 1;
	return 0;
}

static uint
agent_observe_checkpoint_selection_score(int slot, const int *selected,
					 uint count)
{
	const struct agent_audit_record *record = &agent_audit_records[slot];
	uint64 principal = agent_audit_principals[slot];
	uint64 span_owner = agent_audit_span_owners[slot];
	uint identity_class = agent_audit_identity_classes[slot];
	int class_seen = 0;
	int kind_seen = 0;
	int principal_seen = 0;
	int span_seen = 0;

	for (uint i = 0; i < count; i++) {
		int prior_slot = selected[i];
		const struct agent_audit_record *prior =
			&agent_audit_records[prior_slot];

		if (agent_audit_identity_classes[prior_slot] == identity_class)
			class_seen = 1;
		if (prior->kind == record->kind)
			kind_seen = 1;
		if (agent_audit_principals[prior_slot] == principal)
			principal_seen = 1;
		if (prior->span_id == record->span_id &&
		    agent_audit_span_owners[prior_slot] == span_owner)
			span_seen = 1;
	}
	return (!class_seen << 7) | (!kind_seen << 6) |
	       (!principal_seen << 5) | (!span_seen << 4) | identity_class;
}

static int
agent_observe_checkpoint_select(struct agent_audit_scope_state *state,
				int selected[AGENT_OBSERVE_CHECKPOINT_PER_SCOPE],
				uint *selected_count)
{
	uint tail_count;
	uint tail_start;
	uint anchors = 0;
	uint count = 0;

	if (state == 0 || selected == 0 || selected_count == 0 ||
	    state->visible_records == 0 ||
	    state->visible_records > AGENT_AUDIT_SCOPE_LIMIT)
		return -1;
	tail_count = state->visible_records <
			     AGENT_OBSERVE_CHECKPOINT_LATEST_TAIL ?
			     state->visible_records :
			     AGENT_OBSERVE_CHECKPOINT_LATEST_TAIL;
	tail_start = state->visible_records - tail_count;
	for (uint i = tail_start; i < state->visible_records; i++)
		selected[count++] = state->sequence_slots[i];
	while (anchors < AGENT_OBSERVE_CHECKPOINT_DIVERSITY_ANCHORS &&
	       count < state->visible_records) {
		uint best_score = 0;
		uint64 best_sequence = 0;
		int best_slot = -1;

		for (uint i = 0; i < tail_start; i++) {
			int slot = state->sequence_slots[i];
			uint score;
			uint64 sequence;

			if (slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS ||
			    agent_observe_checkpoint_slot_selected(selected, count,
							 slot))
				continue;
			score = agent_observe_checkpoint_selection_score(
				slot, selected, count);
			sequence = agent_audit_records[slot].sequence;
			if (best_slot < 0 || score > best_score ||
			    (score == best_score && sequence > best_sequence)) {
				best_slot = slot;
				best_score = score;
				best_sequence = sequence;
			}
		}
		if (best_slot < 0)
			break;
		selected[count++] = best_slot;
		anchors++;
	}
	if (count != (state->visible_records <
			      AGENT_OBSERVE_CHECKPOINT_PER_SCOPE ?
			      state->visible_records :
			      AGENT_OBSERVE_CHECKPOINT_PER_SCOPE))
		return -1;
	for (uint i = 1; i < count; i++) {
		int slot = selected[i];
		uint j = i;

		while (j > 0 && agent_audit_records[selected[j - 1]].sequence >
					 agent_audit_records[slot].sequence) {
			selected[j] = selected[j - 1];
			j--;
		}
		selected[j] = slot;
	}
	*selected_count = count;
	return 0;
}

uint64
agent_observe_checkpoint_generation_get(void)
{
	return agent_observe_checkpoint_generation;
}

void
agent_observe_checkpoint_generation_floor(uint64 generation)
{
	if (generation > agent_observe_checkpoint_generation)
		agent_observe_checkpoint_generation = generation;
}

void
agent_observe_checkpoint_raise_highwater(uint64 sequence, uint64 span,
					 uint64 event, uint agent_id)
{
	if (sequence != 0 && agent_audit_next_sequence != 0 &&
	    sequence > agent_audit_next_sequence)
		agent_audit_next_sequence = sequence;
	if (span != 0 && next_span_id != 0 && span > next_span_id)
		next_span_id = span;
	if (event != 0 && next_event_id != 0 && event > next_event_id)
		next_event_id = event;
	if (agent_id != 0)
		agent_identity_id_floor(agent_id);
}

void
agent_observe_checkpoint_exhaust_highwater(uint exhausted)
{
	if (exhausted & AGENT_OBSERVE_ALLOC_AUDIT_EXHAUSTED) {
		agent_audit_next_sequence = 0;
		agent_identity_lease_allocator_force_exhausted(
			AGENT_IDENTITY_ALLOCATOR_AUDIT);
	}
	if (exhausted & AGENT_OBSERVE_ALLOC_SPAN_EXHAUSTED) {
		next_span_id = 0;
		agent_identity_lease_allocator_force_exhausted(
			AGENT_IDENTITY_ALLOCATOR_SPAN);
	}
	if (exhausted & AGENT_OBSERVE_ALLOC_EVENT_EXHAUSTED) {
		next_event_id = 0;
		agent_identity_lease_allocator_force_exhausted(
			AGENT_IDENTITY_ALLOCATOR_EVENT);
	}
	if (exhausted & AGENT_OBSERVE_ALLOC_CONTROL_EXHAUSTED) {
		agent_identity_lease_allocator_force_exhausted(
			AGENT_IDENTITY_ALLOCATOR_CONTROL);
		agent_lifecycle_control_id_floor(0);
	}
	if (exhausted & AGENT_OBSERVE_ALLOC_AGENT_EXHAUSTED) {
		agent_identity_lease_allocator_force_exhausted(
			AGENT_IDENTITY_ALLOCATOR_AGENT);
		agent_identity_id_floor(0);
	}
}

int
agent_observe_checkpoint_capture_scope(
	uint scope_id, struct workflow_lifecycle_key lifecycle,
	struct agent_observe_checkpoint_scope *saved)
{
	struct agent_audit_scope_state *state;
	int selected[AGENT_OBSERVE_CHECKPOINT_PER_SCOPE];
	uint selected_count = 0;
	uint tail_start;

	if (saved == 0 || !agent_observe_scope_valid(scope_id) ||
	    !workflow_lifecycle_key_valid(lifecycle))
		return -1;
	memset(saved, 0, sizeof(*saved));
	state = agent_observe_audit_scope_state_get(scope_id, 0);
	if (state == 0)
		return 0;
	if (state->visible_records == 0) {
		if (state->total_records == 0)
			return 0;
		if (state->ledger_hash != 0 ||
		    state->admission_drops != state->total_records)
			return -1;
		saved->used = AGENT_OBSERVE_SCOPE_USED;
		saved->scope_id = scope_id;
		saved->lifecycle_id = lifecycle.id;
		saved->lifecycle_generation = lifecycle.generation;
		saved->total_records = state->total_records;
		saved->admission_drops = state->admission_drops;
#ifdef AGENT_OBSERVE_TEST_PROFILE
		agent_observe_test_drop_only_captured(
			state->scope_id, state->total_records,
			state->admission_drops);
#endif
		return 1;
	}
	if (state->total_records < state->visible_records ||
	    state->admission_drops >
		    state->total_records - state->visible_records ||
	    agent_observe_checkpoint_select(state, selected,
					    &selected_count) < 0)
		return -1;
	saved->used = AGENT_OBSERVE_SCOPE_USED;
	saved->scope_id = scope_id;
	saved->lifecycle_id = lifecycle.id;
	saved->lifecycle_generation = lifecycle.generation;
	saved->total_records = state->total_records;
	saved->admission_drops = state->admission_drops;
	saved->ledger_hash = state->ledger_hash;
	tail_start = selected_count > AGENT_OBSERVE_CHECKPOINT_LATEST_TAIL ?
			     selected_count -
				     AGENT_OBSERVE_CHECKPOINT_LATEST_TAIL :
			     0;
	for (uint j = 0; j < selected_count; j++) {
		int slot = selected[j];
		struct agent_observe_checkpoint_entry *entry =
			&saved->records[saved->record_count++];
		struct agent_audit_record *record = &agent_audit_records[slot];
		uint identity_class = agent_audit_identity_classes[slot];

		if (record->workflow_lifecycle_id != lifecycle.id ||
		    record->workflow_lifecycle_generation != lifecycle.generation ||
		    identity_class > AGENT_OBSERVE_IDENTITY_MAX ||
		    agent_audit_low_class[slot] !=
			    (identity_class != AGENT_OBSERVE_IDENTITY_AUTHORITY))
			return -1;
		entry->record = *record;
		entry->scope_id = scope_id;
		entry->principal = agent_audit_principals[slot];
		entry->span_owner = agent_audit_span_owners[slot];
		entry->identity_class = identity_class;
		if (j >= tail_start)
			entry->link_flags |= AGENT_OBSERVE_LINK_LATEST_TAIL;
		if (j != 0 && record->prev_hash ==
			      saved->records[j - 1].record.record_hash)
			entry->link_flags |= AGENT_OBSERVE_LINK_PREV_RETAINED;
		entry->receipt_id = agent_audit_receipts[slot].receipt_id;
		if (entry->receipt_id == 0)
			return -1;
	}
	return 1;
}

int
agent_observe_checkpoint_restore_scope(
	const struct agent_observe_checkpoint_scope *saved)
{
	struct agent_audit_scope_state *state;
	int restore_slots[AGENT_OBSERVE_CHECKPOINT_PER_SCOPE];
	uint64 successful_records;
	uint64 largest_sequence = 0;
	uint64 largest_hash = 0;
	uint64 old_head;
	uint restored = 0;
	int enabled;
	int gap = 0;
	int result = -1;

	if (saved == 0 ||
	    !(saved->used & AGENT_OBSERVE_SCOPE_USED))
		return 0;
	if (saved->total_records == 0 ||
	    saved->record_count > AGENT_OBSERVE_CHECKPOINT_PER_SCOPE ||
	    saved->admission_drops >
		    saved->total_records - saved->record_count)
		return -1;
	successful_records = saved->total_records - saved->admission_drops;
	if (saved->record_count == 0) {
		if (successful_records != 0 ||
		    saved->ledger_hash != 0)
			return -1;
	} else if (successful_records < saved->record_count ||
		   saved->ledger_hash == 0) {
		return -1;
	}
	if (saved->record_count != 0) {
		for (uint i = 0; i < saved->record_count; i++)
			if (agent_observe_checkpoint_entry_validate(
				    saved, i, &saved->records[i],
				    i == 0 ? 0 : &saved->records[i - 1],
				    &gap) < 0)
				return -1;
		if (saved->records[saved->record_count - 1]
				    .record.record_hash != saved->ledger_hash ||
		    ((successful_records - saved->record_count == 0) ?
			     (gap || saved->records[0].record.prev_hash != 0) :
			     !gap))
			return -1;
	}

	/* Validate the whole image before entering one atomic publication window. */
	enabled = intr_save();
	state = agent_observe_audit_scope_state_get(saved->scope_id, 1);
	if (state == 0)
		goto out;
	/* A live metadata reload must not replace newer in-memory evidence. */
	if (state->visible_records != 0 || state->total_records != 0 ||
	    state->admission_drops != 0 || state->ledger_hash != 0) {
		result = 0;
		goto out;
	}
	if (saved->record_count == 0) {
		state->total_records = saved->total_records;
		state->admission_drops = saved->admission_drops;
		state->ledger_hash = 0;
		state->observe_epoch++;
		result = 0;
		goto out;
	}
	old_head = agent_audit_head;
	for (uint i = 0; i < AGENT_AUDIT_MAX_RECORDS &&
			 restored < saved->record_count; i++) {
		int slot = (agent_audit_head + i) % AGENT_AUDIT_MAX_RECORDS;

		if (agent_audit_scopes[slot] == VFS_SCOPE_NONE)
			restore_slots[restored++] = slot;
	}
	if (restored != saved->record_count) {
		restored = 0;
		goto out;
	}
	restored = 0;
	for (uint i = 0; i < saved->record_count; i++) {
		const struct agent_observe_checkpoint_entry *entry =
			&saved->records[i];
		int slot = restore_slots[i];

		if (slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS ||
		    agent_audit_scopes[slot] != VFS_SCOPE_NONE)
			goto rollback;
		agent_audit_records[slot] = entry->record;
		agent_audit_scopes[slot] = saved->scope_id;
		agent_audit_principals[slot] = entry->principal;
		agent_audit_span_owners[slot] = entry->span_owner;
		agent_audit_low_class[slot] =
			entry->identity_class != AGENT_OBSERVE_IDENTITY_AUTHORITY;
		agent_audit_identity_classes[slot] = entry->identity_class;
		agent_audit_receipts[slot].receipt_id = entry->receipt_id;
		agent_audit_receipts[slot].persist_target = 0;
		agent_audit_receipts[slot].state =
			AGENT_AUDIT_RECEIPT_RECOVERED;
		if (agent_observe_audit_index_insert(state, slot) < 0) {
			agent_audit_slot_clear(slot);
			goto rollback;
		}
		if (entry->record.kind >= 0 &&
		    entry->record.kind <= AGENT_AUDIT_KIND_PREFETCH) {
			state->kind_counts[entry->record.kind]++;
			agent_audit_kind_counts[entry->record.kind]++;
		}
		if (entry->record.sequence > largest_sequence) {
			largest_sequence = entry->record.sequence;
			largest_hash = entry->record.record_hash;
		}
		agent_audit_count++;
		agent_audit_head = (slot + 1) % AGENT_AUDIT_MAX_RECORDS;
		restored++;
	}
	state->total_records = saved->total_records;
	state->admission_drops = saved->admission_drops;
	state->ledger_hash = saved->ledger_hash;
	state->observe_epoch++;
	if (largest_sequence != 0 &&
	    (agent_audit_next_sequence == 0 || largest_sequence + 1 == 0)) {
		agent_audit_next_sequence = 0;
	} else if (largest_sequence >= agent_audit_next_sequence) {
		agent_audit_next_sequence = largest_sequence + 1;
		agent_audit_ledger_hash = largest_hash;
	}
	result = 0;
	goto out;

rollback:
	/* The scope was empty on entry, so these are the only published slots. */
	for (uint i = 0; i < restored; i++) {
		int slot = restore_slots[i];
		int kind = agent_audit_records[slot].kind;

		if (kind >= 0 && kind <= AGENT_AUDIT_KIND_PREFETCH) {
			if (state->kind_counts[kind] > 0)
				state->kind_counts[kind]--;
			if (agent_audit_kind_counts[kind] > 0)
				agent_audit_kind_counts[kind]--;
		}
		agent_audit_slot_clear(slot);
		if (agent_audit_count > 0)
			agent_audit_count--;
	}
	memset(state->sequence_slots, 0, sizeof(state->sequence_slots));
	memset(state->timeline_slots, 0, sizeof(state->timeline_slots));
	state->visible_records = 0;
	agent_audit_head = old_head;
out:
	intr_restore(enabled);
	return result;
}

void
agent_observe_timeline_from_audit(
	struct agent_audit_record *record,
	struct agent_timeline_record *timeline)
{
	memset(timeline, 0, sizeof(*timeline));
	timeline->source = AGENT_TIMELINE_SOURCE_AUDIT;
	timeline->kind = record->kind;
	timeline->tick = record->tick;
	timeline->sequence = record->sequence;
	timeline->cause_sequence = record->cause_sequence;
	timeline->span_id = record->span_id;
	timeline->workflow_lifecycle_id = record->workflow_lifecycle_id;
	timeline->workflow_lifecycle_generation =
		record->workflow_lifecycle_generation;
	timeline->branch_generation = record->branch_generation;
	timeline->cause_branch_generation = record->cause_branch_generation;
	timeline->actor_control_id = record->actor_control_id;
	timeline->cause_control_id = record->cause_control_id;
	timeline->cause_record_hash = record->cause_record_hash;
	timeline->value0 = record->value0;
	timeline->value1 = record->value1;
	timeline->value2 = record->value2;
	timeline->flags = record->flags;
	timeline->pid = record->pid;
	timeline->source_pid = record->source_pid;
	timeline->target_pid = record->target_pid;
	timeline->role = record->role;
	timeline->loop_state = record->loop_state;
	timeline->tool_id = record->tool_id;
	timeline->event_type = record->event_type;
	timeline->status = record->status;
	safestrcpy(timeline->text, record->text, sizeof(timeline->text));
}

void
agent_observe_timeline_record_audit(
	uint scope_id, struct agent_audit_record *record, uint64 span_owner)
{
	struct agent_timeline_record timeline;

	if (record == 0)
		return;
	agent_observe_timeline_from_audit(record, &timeline);
	agent_observe_timeline_publish_locked(scope_id, &timeline, span_owner);
}

static void
agent_audit_note_drop(struct agent_audit_scope_state *state)
{
	if (state == 0)
		return;
	if (state->total_records == ~0ULL)
		return;
	state->total_records++;
	state->admission_drops++;
	if (agent_observe_checkpoint_generation != ~0ULL)
		agent_observe_checkpoint_generation++;
	/* Drops have no per-record receipt, but their monotonic counters are part
	 * of the durable scope checkpoint and must not wait for a later record. */
	agent_obsstore_mark_dirty(state->scope_id);
}

static void agent_audit_emit(int kind, uint64 tick, struct proc *actor,
			     int source_pid, int target_pid, int event_type,
			     int tool_id, int status, uint64 cause_sequence,
			     uint64 span_id, uint64 span_owner,
			     uint64 audit_principal, int identity_class,
			     int authority_effect,
			     uint64 value0, uint64 value1, uint64 value2,
			     uint64 flags, char *text)
{
	struct agent_audit_record *record;
	struct agent_audit_scope_state *scope_state;
	struct thread *thread = curr_thread();
	uint scope_id = agent_identity_proc_scope(actor);
	uint64 principal;
	uint64 sequence;
	uint64 receipt_serial = 0;
	uint64 receipt_target = 0;
	uint64 identity_reserve;
	struct workflow_lifecycle_key receipt_lifecycle;
	int low_class;
	int slot;

	if (span_id == 0 || span_owner == 0) {
		span_id = 0;
		span_owner = 0;
	}
	if (authority_effect && actor != 0 && actor->agent_control_id != 0 &&
	    agent_identity_has_any_cap(actor, AGENT_AUDIT_PRIVILEGED_CAPS))
		identity_class = AGENT_AUDIT_ID_AUTHORITY;
	else if (identity_class != AGENT_AUDIT_ID_CAUSAL)
		identity_class = AGENT_AUDIT_ID_TELEMETRY;
	authority_effect = identity_class == AGENT_AUDIT_ID_AUTHORITY;
	identity_reserve = identity_class == AGENT_AUDIT_ID_TELEMETRY ?
				   AGENT_AUDIT_CAUSAL_ID_RESERVE : 0;
	principal = authority_effect ? actor->agent_control_id : audit_principal;
	if (principal == 0 && actor != 0)
		principal = actor->agent_control_id;
	// Only an explicit kernel-confirmed privileged state transition enters
	// the protected partition. Telemetry, IPC and user-written Context are
	// always general records, irrespective of the public span identifier.
	low_class = identity_class != AGENT_AUDIT_ID_AUTHORITY;
	if (!agent_observe_scope_valid(scope_id) ||
	    (scope_state =
		     agent_observe_audit_scope_state_get(scope_id, 1)) == 0)
		return;
	if (scope_state->total_records == ~0ULL)
		return;
#ifdef AGENT_OBSERVE_TEST_PROFILE
	if (agent_observe_test_drop_audit(
		    actor, scope_id, kind, tool_id, status, authority_effect)) {
		agent_audit_note_drop(scope_state);
		return;
	}
#endif
	if (agent_audit_next_sequence == 0) {
		agent_audit_note_drop(scope_state);
		return;
	}
	slot = agent_observe_audit_slot_alloc(scope_state, principal, low_class,
					      span_id, span_owner, kind);
	if (slot < 0) {
		agent_audit_note_drop(scope_state);
		return;
	}
	sequence = agent_observe_alloc_id(
		&agent_audit_next_sequence, AGENT_IDENTITY_ALLOCATOR_AUDIT,
		identity_reserve);
	if (sequence == 0) {
		agent_audit_note_drop(scope_state);
		return;
	}
	if (agent_audit_slot_unpublish(slot) < 0)
		return;
	record = &agent_audit_records[slot];
	record->sequence = sequence;
	record->tick = tick;
	record->kind = kind;
	record->source_pid = source_pid;
	record->target_pid = target_pid;
	record->event_type = event_type;
	record->tool_id = tool_id;
	record->status = status;
	record->cause_sequence = cause_sequence;
	record->span_id = span_id;
	if (actor) {
		record->workflow_lifecycle_id = actor->workflow_lifecycle_id;
		record->workflow_lifecycle_generation =
			actor->workflow_lifecycle_generation;
		record->branch_generation = actor->context_branch_generation;
		record->cause_branch_generation =
			actor->context_cause_branch_generation;
		record->actor_control_id = actor->agent_control_id;
		record->cause_control_id = actor->agent_current_cause_control;
		if (record->cause_control_id == 0 && cause_sequence != 0)
			record->cause_control_id = audit_principal;
		if (cause_sequence == actor->agent_current_cause_sequence &&
		    record->cause_control_id == actor->agent_current_cause_control)
			record->cause_record_hash = actor->agent_context_chain_hash;
	}
	record->value0 = value0;
	record->value1 = value1;
	record->value2 = value2;
	record->flags = flags;
	if (actor) {
		record->pid = actor->pid;
		record->tid = thread && thread->process == actor ? thread->tid : 0;
		record->agent_id = actor->agent_id;
		record->role = actor->agent_role;
		record->loop_state = thread && thread->process == actor ?
					    thread->agent_loop_state :
					    actor->loop_state;
	}
	safestrcpy(record->text, text ? text : "", sizeof(record->text));
	record->prev_hash = scope_state->ledger_hash;
	record->record_hash = agent_audit_record_hash(record);
	receipt_lifecycle.id = record->workflow_lifecycle_id;
	receipt_lifecycle.generation =
		record->workflow_lifecycle_generation;
	agent_audit_receipts[slot].state =
		agent_obsstore_mark_dirty_receipt(
			scope_id, receipt_lifecycle, &receipt_serial,
			&receipt_target) == 0 ?
			AGENT_AUDIT_RECEIPT_PENDING :
			AGENT_AUDIT_RECEIPT_FAILED;
	agent_audit_receipts[slot].persist_target = receipt_target;
	agent_audit_receipts[slot].receipt_id = agent_audit_receipt_id(
		record, scope_id, receipt_serial, receipt_target);
	agent_audit_scopes[slot] = scope_id;
	agent_audit_principals[slot] = principal;
	agent_audit_span_owners[slot] = span_owner;
	agent_audit_low_class[slot] = low_class;
	agent_audit_identity_classes[slot] = identity_class;
	if (agent_observe_audit_index_insert(scope_state, slot) < 0) {
		agent_audit_slot_clear(slot);
		return;
	}
	scope_state->ledger_hash = record->record_hash;
	scope_state->total_records++;
	if (agent_observe_checkpoint_generation != ~0ULL)
		agent_observe_checkpoint_generation++;
	agent_audit_ledger_hash = record->record_hash;
	if (kind >= 0 && kind <= AGENT_AUDIT_KIND_PREFETCH) {
		scope_state->kind_counts[kind]++;
		agent_audit_kind_counts[kind]++;
	}
	agent_audit_head = (slot + 1) % AGENT_AUDIT_MAX_RECORDS;
	agent_audit_count++;
	agent_observe_timeline_record_audit(scope_id, record, span_owner);
}

static void agent_audit_context(struct proc *p,
				struct agent_context_record *record,
				uint64 span_owner, int source_pid,
				uint64 cause_control,
				int authority_effect, int causal_audit)
{
	uint64 principal;
	int identity_class;

	if (p == 0 || record == 0 || record->sequence == 0)
		return;
	source_pid = cause_control != 0 ? source_pid : p->pid;
	principal = cause_control != 0 ? cause_control : p->agent_control_id;
	if (source_pid <= 0)
		source_pid = p->pid;
	if (principal == 0)
		principal = p->agent_control_id;
	identity_class = causal_audit && record->span_id != 0 &&
			 span_owner != 0 ?
				 AGENT_AUDIT_ID_CAUSAL :
				 AGENT_AUDIT_ID_TELEMETRY;
	agent_audit_emit(AGENT_AUDIT_KIND_CONTEXT, record->tick, p, source_pid,
			 p->pid, 0, record->tool_id, record->status,
			 record->cause_sequence, record->span_id,
			 span_owner, principal, identity_class,
			 authority_effect,
			 record->value0, record->value1, record->value2,
			 record->flags,
			 record->result[0] ? record->result : record->payload);
}

static void agent_audit_event(int kind, struct proc *actor,
			      struct agent_event *event, uint64 span_owner,
			      uint64 audit_principal)
{
	int identity_class;

	if (event == 0)
		return;
	identity_class = event->span_id != 0 && span_owner != 0 ?
				 AGENT_AUDIT_ID_CAUSAL :
				 AGENT_AUDIT_ID_TELEMETRY;
	agent_audit_emit(kind, event->tick, actor, event->source_pid,
			 event->target_pid, event->type, 0, event->status,
			 event->cause_sequence, event->span_id, span_owner,
			 audit_principal, identity_class, 0,
			 event->event_id, event->corr_id, event->target_pid,
			 0, event->payload);
}

static void agent_audit_sched(struct proc *p, struct agent_sched_record *record)
{
	if (p == 0 || record == 0)
		return;
	agent_audit_emit(AGENT_AUDIT_KIND_SCHED, record->tick, p, p->pid,
			 p->pid, 0, 0, AGENT_STATUS_OK, 0, 0,
			 0, p->agent_control_id, AGENT_AUDIT_ID_TELEMETRY, 0,
			 record->dispatch_count, record->score,
			 record->event_queue_count, record->reason_flags,
			 "sched");
}

static void agent_audit_prefetch_handoff(int source_pid,
					 uint64 source_control_id,
					 struct proc *target,
					 struct agent_file_prefetch_hint *hint,
					 uint64 span_owner,
					 char *target_stage,
					 uint64 reason)
{
	if (source_pid <= 0 || source_control_id == 0 || target == 0 ||
	    hint == 0 || target_stage == 0)
		return;
	agent_audit_emit(AGENT_AUDIT_KIND_PREFETCH, agent_observe_ticks(), target,
			 source_pid, target->pid, AGENT_EVENT_MESSAGE,
			 AGENT_TOOL_QUERY_FILE, AGENT_STATUS_OK,
			 hint->source_sequence, hint->span_id,
			 span_owner, source_control_id,
			 hint->span_id != 0 && span_owner != 0 ?
				 AGENT_AUDIT_ID_CAUSAL :
				 AGENT_AUDIT_ID_TELEMETRY,
			 0,
			 hint->source_sequence, hint->source_fid, hint->fid,
			 reason, target_stage);
}


/* Span-prefetch observation bus. */
/*
 * The shared span bus has a fixed-size search domain. Callers prepay that
 * bound before entering any endpoint commit, so this helper never schedules.
 */
static void
agent_observe_prefetch_bus_store_commit(
	struct agent_file_prefetch_hint *hint, uint scope_id,
	uint64 span_owner)
{
	struct agent_file_prefetch_hint copy;
	int slot;
	int visible;
	int start;
	int owned = 0;
	int oldest_slot = -1;
	int was_free;
	uint64 oldest_sequence = ~0ULL;

	if (hint == 0 || hint->span_id == 0 || span_owner == 0 || hint->fid == 0 ||
	    !agent_observe_scope_valid(scope_id))
		return;
	visible = AGENT_FILE_PREFETCH_SPAN_MAX;
	start = 0;
	for (int i = 0; i < visible; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_SPAN_MAX;
		if (agent_span_prefetch_scopes[slot] == scope_id) {
			owned++;
			if (agent_span_prefetch_hints[slot].sequence <
			    oldest_sequence) {
				oldest_sequence =
					agent_span_prefetch_hints[slot].sequence;
				oldest_slot = slot;
			}
		}
		if (agent_span_prefetch_hints[slot].span_id ==
			    hint->span_id &&
		    agent_span_prefetch_owners[slot] == span_owner &&
		    agent_span_prefetch_scopes[slot] == scope_id &&
		    agent_span_prefetch_hints[slot].fid == hint->fid &&
		    agent_span_prefetch_hints[slot].source_fid ==
			    hint->source_fid &&
		    agent_span_prefetch_hints[slot].source_pid ==
			    hint->source_pid &&
		    agent_span_prefetch_hints[slot].target_pid ==
			    hint->target_pid)
			goto fill;
	}
	if (owned >= AGENT_PREFETCH_SCOPE_LIMIT) {
		slot = oldest_slot;
	} else {
		slot = -1;
		for (int i = 0; i < AGENT_FILE_PREFETCH_SPAN_MAX; i++) {
			int candidate = (agent_span_prefetch_head + i) %
					AGENT_FILE_PREFETCH_SPAN_MAX;

			if (agent_span_prefetch_scopes[candidate] ==
			    VFS_SCOPE_NONE) {
				slot = candidate;
				break;
			}
		}
		if (slot < 0)
			slot = oldest_slot;
	}
	if (slot < 0)
		return;
	was_free = agent_span_prefetch_scopes[slot] == VFS_SCOPE_NONE;
	agent_span_prefetch_head =
		(slot + 1) % AGENT_FILE_PREFETCH_SPAN_MAX;
	if (was_free &&
	    agent_span_prefetch_count < AGENT_FILE_PREFETCH_SPAN_MAX)
		agent_span_prefetch_count++;

fill:
	memmove(&copy, hint, sizeof(copy));
	copy.sequence = agent_span_prefetch_next_sequence++;
	copy.reason |= AGENT_FILE_PREFETCH_REASON_SPAN_BUS;
	memmove(&agent_span_prefetch_hints[slot], &copy, sizeof(copy));
	agent_span_prefetch_scopes[slot] = scope_id;
	agent_span_prefetch_owners[slot] = span_owner;
}

static void agent_file_prefetch_bus_store(struct agent_file_prefetch_hint *hint,
					  uint scope_id,
					  uint64 span_owner)
{
	int enabled;

	agent_metadata_txn_work_charge(2U * AGENT_FILE_PREFETCH_SPAN_MAX);
	enabled = intr_save();
	agent_observe_prefetch_bus_store_commit(hint, scope_id, span_owner);
	intr_restore(enabled);
}

int agent_observe_prefetch_scope_next_locked(
	uint scope_id, uint64 after_sequence,
	struct agent_file_prefetch_hint *out, uint64 *span_owner)
{
	uint64 best = ~0ULL;
	int slot = -1;

	for (int i = 0; i < AGENT_FILE_PREFETCH_SPAN_MAX; i++) {
		agent_metadata_txn_work_charge(1);
		if (agent_span_prefetch_scopes[i] != scope_id ||
		    agent_span_prefetch_hints[i].sequence <= after_sequence ||
		    agent_span_prefetch_hints[i].sequence >= best)
			continue;
		best = agent_span_prefetch_hints[i].sequence;
		slot = i;
	}
	if (slot < 0)
		return 0;
	if (out)
		memmove(out, &agent_span_prefetch_hints[slot], sizeof(*out));
	if (span_owner)
		*span_owner = agent_span_prefetch_owners[slot];
	return 1;
}


int
agent_observe_scope_reclaim(uint scope_id)
{
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	int changed = 0;

	if (!agent_observe_scope_valid(scope_id))
		return -1;
	(void)vfs_scope_lifecycle(scope_id, &lifecycle);
	for (int i = 0; i < AGENT_FILE_PREFETCH_SPAN_MAX; i++) {
		if (agent_span_prefetch_scopes[i] != scope_id)
			continue;
		changed = 1;
		agent_span_prefetch_scopes[i] = VFS_SCOPE_NONE;
		agent_span_prefetch_owners[i] = 0;
		memset(&agent_span_prefetch_hints[i], 0,
		       sizeof(agent_span_prefetch_hints[i]));
		if (agent_span_prefetch_count > 0)
			agent_span_prefetch_count--;
	}
	{
		struct agent_audit_scope_state *state =
			agent_observe_audit_scope_state_get(scope_id, 0);

		if (state != 0 && state->visible_records > 0)
			changed = 1;
		while (state != 0 && state->visible_records > 0) {
			int slot = state->sequence_slots[
				state->visible_records - 1];

			if (agent_audit_slot_unpublish(slot) < 0)
				break;
		}
		/*
		 * The ordered index is authoritative. The fallback only prevents
		 * stale evidence from surviving an internal index inconsistency.
		 */
		for (int i = 0; i < AGENT_AUDIT_MAX_RECORDS; i++)
			if (agent_audit_scopes[i] == scope_id) {
				changed = 1;
				agent_audit_slot_clear(i);
			}
	}
	for (int i = 0; i < NPROC; i++)
		if (agent_audit_scope_states[i].used &&
		    agent_audit_scope_states[i].scope_id == scope_id) {
			changed = 1;
			memset(&agent_audit_scope_states[i], 0,
			       sizeof(agent_audit_scope_states[i]));
		}
	if (changed && agent_observe_checkpoint_generation != ~0ULL)
		agent_observe_checkpoint_generation++;
	return agent_obsstore_mark_reap(scope_id, lifecycle);
}

void
agent_observe_ledger_record_context(struct proc *p,
				    struct agent_context_record *record,
				    uint64 span_owner, int source_pid,
				    uint64 cause_control,
				    int authority_effect, int causal_audit)
{
	if (p == 0 || record == 0)
		return;
	agent_audit_context(p, record, span_owner, source_pid,
			    cause_control, authority_effect, causal_audit);
}

void
agent_observe_ledger_record_sched(struct proc *p,
				  struct agent_sched_record *record)
{
	if (p == 0 || record == 0 ||
	    agent_identity_lease_maintenance_pending() ||
	    record->dispatch_count == 0 ||
	    (record->dispatch_count & (record->dispatch_count - 1)) != 0)
		return;
	agent_audit_sched(p, record);
}

void
agent_observe_ledger_record_event(int kind, struct proc *actor,
				  struct agent_event *event,
				  uint64 span_owner,
				  uint64 audit_principal)
{
	agent_audit_event(kind, actor, event, span_owner, audit_principal);
}

void
agent_observe_ledger_record_effect(struct proc *p, int tool_id, int status,
				   char *text, uint64 value0,
				   uint64 value1, uint64 value2,
				   uint64 flags, int authority_effect)
{
	if (p == 0 || !p->is_agent)
		return;
	agent_audit_emit(AGENT_AUDIT_KIND_CONTEXT, agent_observe_ticks(), p,
			 p->pid, p->pid, 0, tool_id, status,
			 p->agent_current_cause_sequence,
			 p->agent_current_span_id,
			 p->agent_current_span_owner, p->agent_control_id,
			 AGENT_AUDIT_ID_TELEMETRY,
			 authority_effect, value0, value1, value2, flags,
			 text);
}

int
agent_observe_ledger_record_prefetch(struct proc *p,
				     struct agent_file_prefetch_hint *hint,
				     uint64 span_owner, char *target_stage,
				     int publish_audit)
{
	if (p == 0 || hint == 0 || target_stage == 0)
		return 0;
	hint->workflow_lifecycle_id = p->workflow_lifecycle_id;
	hint->workflow_lifecycle_generation = p->workflow_lifecycle_generation;
	hint->branch_generation = p->context_branch_generation;
	hint->actor_control_id = p->agent_control_id;
	hint->cause_branch_generation = p->context_cause_branch_generation;
	hint->cause_control_id = p->agent_current_cause_control;
	hint->cause_record_hash = p->agent_context_chain_hash;
	hint->actor_tid = curr_thread() ? curr_thread()->tid : 0;
	hint->actor_role = p->agent_role;
	hint->actor_loop_state = curr_thread() &&
				 curr_thread()->process == p ?
				 curr_thread()->agent_loop_state : p->loop_state;
	agent_file_prefetch_bus_store(
		hint, agent_identity_proc_scope(p), span_owner);
	if (publish_audit)
		agent_audit_emit(
			AGENT_AUDIT_KIND_PREFETCH, hint->tick, p,
			hint->source_pid, hint->target_pid, AGENT_EVENT_NONE,
			AGENT_TOOL_QUERY_FILE, AGENT_STATUS_OK,
			hint->source_sequence, hint->span_id, span_owner,
			p->agent_control_id,
			hint->span_id != 0 && span_owner != 0 ?
				AGENT_AUDIT_ID_CAUSAL :
				AGENT_AUDIT_ID_TELEMETRY,
			0, hint->source_sequence,
			hint->source_fid, hint->fid, hint->reason, target_stage);
	return 1;
}

int
agent_observe_ledger_record_prefetch_handoff_locked(
	int source_pid, uint64 source_control_id, struct proc *target,
	struct agent_file_prefetch_hint *hint, uint64 span_owner,
	char *target_stage, uint64 reason)
{
	uint scope_id;

	if (target == 0 || hint == 0 || target_stage == 0)
		return 0;
	hint->workflow_lifecycle_id = target->workflow_lifecycle_id;
	hint->workflow_lifecycle_generation =
		target->workflow_lifecycle_generation;
	hint->branch_generation = target->context_branch_generation;
	hint->actor_control_id = target->agent_control_id;
	hint->cause_branch_generation = target->context_cause_branch_generation;
	hint->cause_control_id = target->agent_current_cause_control;
	hint->cause_record_hash = target->agent_context_chain_hash;
	hint->actor_tid = 0;
	hint->actor_role = target->agent_role;
	hint->actor_loop_state = target->loop_state;
	scope_id = agent_identity_proc_scope(target);
	agent_observe_prefetch_bus_store_commit(hint, scope_id, span_owner);
	agent_audit_prefetch_handoff(source_pid, source_control_id, target,
				     hint, span_owner, target_stage, reason);
	return 1;
}
