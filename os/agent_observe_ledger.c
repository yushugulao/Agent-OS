#include "agent_context.h"
#include "agent_identity_lease.h"
#include "agent_internal.h"
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
extern struct proc pool[NPROC];

_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_AUDIT_SCOPE_LIMIT <=
	       AGENT_AUDIT_MAX_RECORDS,
	       "audit table must reserve every workflow partition");
_Static_assert(AGENT_AUDIT_LOW_PRINCIPAL_LIMIT <=
	       AGENT_AUDIT_LOW_SCOPE_LIMIT &&
	       AGENT_AUDIT_LOW_PRINCIPAL_LIMIT ==
		       AGENT_AUDIT_LOW_PRINCIPAL_MAX &&
	       AGENT_AUDIT_LOW_PRINCIPAL_RESERVE > AGENT_AUDIT_KIND_SCHED &&
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
_Static_assert(KERNEL_WORK_OPERATION_UNITS <= KERNEL_WORK_BUDGET_UNITS,
	       "observation query work must fit one scheduling checkpoint");

struct agent_audit_scope_state {
	uint scope_id;
	uint visible_records;
	uint64 lifecycle_generation;
	uint64 total_records;
	uint64 evidence_linked_records;
	uint64 admission_drops;
	uint64 ledger_hash;
	uint64 kind_counts[AGENT_AUDIT_KIND_SLOT_COUNT];
	uint64 observe_epoch;
	ushort sequence_slots[AGENT_AUDIT_SCOPE_LIMIT];
	ushort timeline_slots[AGENT_AUDIT_SCOPE_LIMIT];
};

enum agent_audit_receipt_state_kind {
	AGENT_AUDIT_RECEIPT_NONE = 0,
	AGENT_AUDIT_RECEIPT_PENDING,
	AGENT_AUDIT_RECEIPT_FAILED,
};

enum agent_audit_identity_class {
	AGENT_AUDIT_ID_TELEMETRY = 0,
	AGENT_AUDIT_ID_CAUSAL = 1,
	AGENT_AUDIT_ID_AUTHORITY = 2,
};

struct agent_audit_receipt_state {
	uint64 receipt_id;
	uint state;
};

static uint64 next_span_id;
static uint64 next_event_id;
static uint64 agent_audit_next_sequence;
static uint64 agent_audit_head;
static struct agent_audit_record agent_audit_records[AGENT_AUDIT_MAX_RECORDS];
static uint agent_audit_scopes[AGENT_AUDIT_MAX_RECORDS];
static uint64 agent_audit_principals[AGENT_AUDIT_MAX_RECORDS];
static uint64 agent_audit_span_owners[AGENT_AUDIT_MAX_RECORDS];
static uint64 agent_audit_evidence_tickets[AGENT_AUDIT_MAX_RECORDS];
static uchar agent_audit_low_class[AGENT_AUDIT_MAX_RECORDS];
static struct agent_audit_receipt_state agent_audit_receipts[AGENT_AUDIT_MAX_RECORDS];
static struct agent_audit_scope_state agent_audit_scope_states[WORKFLOW_LIFECYCLE_CAP];
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
	memset(agent_audit_records, 0, sizeof(agent_audit_records));
	memset(agent_audit_scopes, 0, sizeof(agent_audit_scopes));
	memset(agent_audit_principals, 0, sizeof(agent_audit_principals));
	memset(agent_audit_span_owners, 0, sizeof(agent_audit_span_owners));
	memset(agent_audit_evidence_tickets, 0,
	       sizeof(agent_audit_evidence_tickets));
	memset(agent_audit_low_class, 0, sizeof(agent_audit_low_class));
	memset(agent_audit_receipts, 0, sizeof(agent_audit_receipts));
	memset(agent_audit_scope_states, 0,
	       sizeof(agent_audit_scope_states));
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

uint64
agent_observe_alloc_audit_sequence(void)
{
	return agent_observe_alloc_id(
		&agent_audit_next_sequence, AGENT_IDENTITY_ALLOCATOR_AUDIT, 0);
}

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
	/* 内省公平让出处理器，且不驱逐正在读取的证据。 */
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
	/* 先发布再让出；调用方在检查点后重新计数。 */
	*reserved = records;
	return agent_observe_query_reserve(additional);
}

/* 权威观测状态与操作。 */

/* 审计记录哈希。 */
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
/* 审计存储与记录。 */
static int agent_observe_audit_lifecycle_matches_scope(
	uint scope_id, struct workflow_lifecycle_key lifecycle)
{
	uint bound_scope = VFS_SCOPE_NONE;
	return agent_observe_scope_valid(scope_id) &&
	       workflow_lifecycle_key_valid(lifecycle) &&
	       lifecycle.id <= WORKFLOW_LIFECYCLE_CAP &&
	       workflow_lifecycle_scope(lifecycle, &bound_scope) == 0 &&
	       bound_scope == scope_id;
}

static struct agent_audit_scope_state *
agent_observe_audit_scope_state_get(
	uint scope_id, struct workflow_lifecycle_key lifecycle, int create)
{
	struct agent_audit_scope_state *state;
	if (!agent_observe_audit_lifecycle_matches_scope(scope_id, lifecycle))
		return 0;
	state = &agent_audit_scope_states[lifecycle.id - 1];
	if (state->lifecycle_generation != 0) {
		if (state->scope_id != scope_id ||
		    state->lifecycle_generation != lifecycle.generation)
			return 0;
		return state;
	}
	if (!create || !workflow_lifecycle_active(lifecycle))
		return 0;
	memset(state, 0, sizeof(*state));
	state->scope_id = scope_id;
	state->lifecycle_generation = lifecycle.generation;
	state->observe_epoch = 1;
	return state;
}

static struct agent_audit_scope_state *
agent_observe_audit_scope_state_current(uint scope_id, int create)
{
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	if (vfs_scope_lifecycle(scope_id, &lifecycle) < 0)
		return 0;
	return agent_observe_audit_scope_state_get(scope_id, lifecycle, create);
}

#define AGENT_OBSERVE_VIEW_EVIDENCE_REF 0x8000U
#define AGENT_OBSERVE_VIEW_REF_INDEX    0x7fffU

static int
agent_observe_view_ref_record(const struct agent_observe_audit_view *view,
			      ushort ref, struct agent_audit_record *record,
			      uint64 *span_owner, int *from_evidence)
{
	uint index = ref & AGENT_OBSERVE_VIEW_REF_INDEX;

	if ((ref & AGENT_OBSERVE_VIEW_EVIDENCE_REF) != 0) {
		if (from_evidence != 0)
			*from_evidence = 1;
		return agent_evidence_view_record(
			&view->evidence, index, record, span_owner);
	}
	if (from_evidence != 0)
		*from_evidence = 0;
	if (index >= AGENT_AUDIT_MAX_RECORDS ||
	    agent_audit_scopes[index] != view->scope_id)
		return 0;
	if (record != 0)
		*record = agent_audit_records[index];
	if (span_owner != 0)
		*span_owner = agent_audit_span_owners[index];
	return 1;
}

static int
agent_observe_view_ref_after(const struct agent_observe_audit_view *view,
			     ushort left_ref, ushort right_ref,
			     int timeline_order)
{
	struct agent_audit_record left;
	struct agent_audit_record right;

	if (!agent_observe_view_ref_record(
		    view, left_ref, &left, 0, 0))
		return 0;
	if (!agent_observe_view_ref_record(
		    view, right_ref, &right, 0, 0))
		return 1;
	if (timeline_order && left.tick != right.tick)
		return left.tick > right.tick;
	return left.sequence > right.sequence;
}

static void
agent_observe_view_ref_insert(struct agent_observe_audit_view *view,
			      ushort slots[AGENT_OBSERVE_AUDIT_SCOPE_LIMIT],
			      uint *count, ushort ref, int timeline_order)
{
	uint pos;

	if (*count == AGENT_OBSERVE_AUDIT_SCOPE_LIMIT) {
		if (!agent_observe_view_ref_after(
			    view, ref, slots[0], timeline_order))
			return;
		for (uint i = 1; i < *count; i++)
			slots[i - 1U] = slots[i];
		(*count)--;
	}
	pos = *count;
	while (pos > 0 && agent_observe_view_ref_after(
				  view, slots[pos - 1U], ref,
				  timeline_order)) {
		slots[pos] = slots[pos - 1U];
		pos--;
	}
	slots[pos] = ref;
	(*count)++;
}

static uint64
agent_observe_evidence_hash_tag(const struct agent_evidence_view *view)
{
	uchar digest[AGENT_SHA256_DIGEST_SIZE];
	uint64 tag = 0;

	if (agent_evidence_view_digest(view, digest) < 0)
		return 0;
	for (uint i = 0; i < sizeof(tag); i++)
		tag = (tag << 8) | digest[i];
	memset(digest, 0, sizeof(digest));
	return tag == 0 ? 1 : tag;
}

static int
agent_observe_legacy_shadowed_by_evidence(
	const struct agent_evidence_view *view, int slot)
{
	const struct agent_audit_record *legacy;
	uint64 evidence_ticket;

	if (view == 0 || slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS)
		return 0;
	legacy = &agent_audit_records[slot];
	evidence_ticket = agent_audit_evidence_tickets[slot];
	if (evidence_ticket == 0 || legacy->kind != AGENT_AUDIT_KIND_CONTEXT ||
	    legacy->workflow_lifecycle_id != view->key.id ||
	    legacy->workflow_lifecycle_generation != view->key.generation)
		return 0;
	for (uint i = 0; i < view->visible_records; i++)
		if (view->entries[i].ticket == evidence_ticket)
			return 1;
	return 0;
}

int
agent_observe_audit_view_open_locked(
	uint scope_id, struct agent_observe_audit_view *view)
{
	struct agent_audit_scope_state *state;
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	uint sequence_count = 0;
	uint timeline_count = 0;
	uint64 linked_records = 0;
	uint64 evidence_tag;
	int evidence_committed;

	if (view == 0)
		return -1;
	memset(view, 0, sizeof(*view));
	view->scope_id = scope_id;
	if (vfs_scope_lifecycle(scope_id, &lifecycle) == 0)
		(void)agent_evidence_view_open(lifecycle, &view->evidence);
	state = agent_observe_audit_scope_state_current(scope_id, 0);
	if (state != 0) {
		view->total_records = state->total_records;
		view->admission_drops = state->admission_drops;
		view->ledger_hash = state->ledger_hash;
		view->observe_epoch = state->observe_epoch;
		if (view->evidence.total_records != 0)
			linked_records = state->evidence_linked_records;
		memmove(view->kind_counts, state->kind_counts,
			sizeof(view->kind_counts));
		for (uint i = 0; i < state->visible_records; i++) {
			if (agent_observe_legacy_shadowed_by_evidence(
				    &view->evidence, state->sequence_slots[i]))
				continue;
			agent_observe_view_ref_insert(
				view, view->sequence_slots, &sequence_count,
				state->sequence_slots[i], 0);
			agent_observe_view_ref_insert(
				view, view->timeline_slots, &timeline_count,
				state->sequence_slots[i], 1);
		}
	}
	/* Only successfully linked compatibility writes duplicate ring evidence. */
	if (linked_records > view->evidence.total_records ||
	    linked_records > view->total_records ||
	    linked_records > view->kind_counts[AGENT_AUDIT_KIND_CONTEXT])
		return -1;
	view->total_records -= linked_records;
	if (view->kind_counts[AGENT_AUDIT_KIND_CONTEXT] >=
	    linked_records)
		view->kind_counts[AGENT_AUDIT_KIND_CONTEXT] -=
			linked_records;
	else
		view->kind_counts[AGENT_AUDIT_KIND_CONTEXT] = 0;
	view->total_records += view->evidence.total_records;
	view->admission_drops += view->evidence.gap_count;
	if (view->evidence.observe_epoch > view->observe_epoch)
		view->observe_epoch = view->evidence.observe_epoch;
	evidence_committed = view->evidence.total_records != 0;
	for (uint i = 0; !evidence_committed &&
			 i < AGENT_SHA256_DIGEST_SIZE; i++)
		if (view->evidence.sealed_root[i] != 0)
			evidence_committed = 1;
	if (evidence_committed) {
		view->kind_counts[AGENT_AUDIT_KIND_CONTEXT] +=
			view->evidence.total_records;
		evidence_tag = agent_observe_evidence_hash_tag(&view->evidence);
		if (evidence_tag == 0)
			return -1;
		view->ledger_hash = evidence_tag;
	}
	for (uint i = 0; i < view->evidence.visible_records; i++) {
		ushort ref = (ushort)(AGENT_OBSERVE_VIEW_EVIDENCE_REF | i);

		agent_observe_view_ref_insert(
			view, view->sequence_slots, &sequence_count, ref, 0);
		agent_observe_view_ref_insert(
			view, view->timeline_slots, &timeline_count, ref, 1);
	}
	view->visible_records = sequence_count;
	for (uint i = 0; i < sequence_count; i++) {
		if ((view->sequence_slots[i] &
		     AGENT_OBSERVE_VIEW_EVIDENCE_REF) != 0)
			view->evidence_visible_records++;
		else
			view->legacy_visible_records++;
	}
	if (sequence_count != timeline_count)
		return -1;
	return state != 0 || view->evidence.total_records != 0;
}

uint
agent_observe_audit_scope_visible_locked(uint scope_id)
{
	struct agent_observe_audit_view view;

	return agent_observe_audit_view_open_locked(scope_id, &view) < 0 ?
	       0 : view.visible_records;
}

int
agent_observe_audit_view_record_source_locked(
	const struct agent_observe_audit_view *view, uint index,
	int timeline_order, struct agent_audit_record *out,
	uint64 *span_owner, int *from_evidence)
{
	ushort ref;

	if (view == 0 || index >= view->visible_records ||
	    view->visible_records > AGENT_AUDIT_SCOPE_LIMIT)
		return 0;
	ref = timeline_order ? view->timeline_slots[index] :
			       view->sequence_slots[index];
	return agent_observe_view_ref_record(
		view, ref, out, span_owner, from_evidence);
}

int
agent_observe_audit_view_record_locked(
	const struct agent_observe_audit_view *view, uint index,
	int timeline_order, struct agent_audit_record *out,
	uint64 *span_owner)
{
	return agent_observe_audit_view_record_source_locked(
		view, index, timeline_order, out, span_owner, 0);
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
	scope = agent_observe_audit_scope_state_get(scope_id, lifecycle, 0);
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
		view->evidence_ticket = agent_audit_evidence_tickets[slot];
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
	uint64 fence_sequence = 0;
	int status;

	if (receipt_id == 0 || durability == 0)
		return AGENT_STATUS_BAD_PARAM;
	*receipt_id = 0;
	*durability = AGENT_AUDIT_DURABILITY_NOT_FOUND;
	status = agent_observe_receipt_snapshot(
		scope_id, lifecycle, sequence, record_hash, supplied_receipt,
		&before);
	if (status != AGENT_STATUS_OK)
		return status;
	*receipt_id = before.receipt_id;
	if (before.state == AGENT_AUDIT_RECEIPT_FAILED) {
		*durability = AGENT_AUDIT_DURABILITY_FAILED;
		return AGENT_STATUS_OK;
	}
	if (before.state != AGENT_AUDIT_RECEIPT_PENDING) {
		*durability = AGENT_AUDIT_DURABILITY_FAILED;
		return AGENT_STATUS_INDETERMINATE;
	}
	if (before.evidence_ticket != 0 &&
	    agent_evidence_ticket_fence_sealed(
		    lifecycle, before.evidence_ticket, &fence_sequence))
		*durability = AGENT_AUDIT_DURABILITY_FENCE_SEALED;
	else
		*durability = AGENT_AUDIT_DURABILITY_PENDING;
	status = agent_observe_receipt_snapshot(
		scope_id, lifecycle, sequence, record_hash, before.receipt_id,
		&after);
	if (status != AGENT_STATUS_OK)
		return status;
	if (after.receipt_id != before.receipt_id ||
	    after.evidence_ticket != before.evidence_ticket ||
	    after.state != before.state)
		return AGENT_STATUS_STALE;
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

	state = agent_observe_audit_scope_state_current(scope_id, 1);
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

uint64 agent_observe_scope_epoch(uint scope_id)
{
	struct agent_audit_scope_state *state;

	state = agent_observe_audit_scope_state_current(scope_id, 1);
	return state ? state->observe_epoch : 0;
}

#define AGENT_AUDIT_PRIVILEGED_CAPS \
	(AGENT_CAP_ACTION_WRITE | AGENT_CAP_ARTIFACT_WRITE | \
	 AGENT_CAP_META_WRITE | AGENT_CAP_ORCHESTRATE | AGENT_CAP_LLM_RELAY | \
	 AGENT_CAP_WAIT_CANCEL | AGENT_CAP_ROUTE_MANAGE | \
	 AGENT_CAP_DEPENDENCY_UPDATE | AGENT_CAP_TASK_ACCEPT | \
	 AGENT_CAP_WORKSPACE_WRITE)

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
	agent_audit_evidence_tickets[slot] = 0;
	agent_audit_low_class[slot] = 0;
	memset(&agent_audit_receipts[slot], 0,
	       sizeof(agent_audit_receipts[slot]));
	memset(&agent_audit_records[slot], 0,
	       sizeof(agent_audit_records[slot]));
}

static int agent_audit_slot_unpublish(int slot)
{
	struct agent_audit_scope_state *state;
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	uint scope_id;

	if (slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS)
		return -1;
	scope_id = agent_audit_scopes[slot];
	if (scope_id == VFS_SCOPE_NONE)
		return 0;
	lifecycle.id = agent_audit_records[slot].workflow_lifecycle_id;
	lifecycle.generation = agent_audit_records[slot].workflow_lifecycle_generation;
	state = agent_observe_audit_scope_state_get(scope_id, lifecycle, 0);
	if (state == 0 || agent_observe_audit_index_remove(state, slot) < 0)
		return -1;
	agent_audit_slot_clear(slot);
	return 0;
}

/* 分区满时先淘汰无 span 遥测；无 span 记录不得抹除唯一因果锚点。 */
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
	/* 受保护的权限效果仍按主体 FIFO。 */
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

/* scope 满载时只滚动自身记录；新主体仅复用已离场主体记录。 */
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

/* 普通主体可借用保证份额外的空槽；新活跃主体仅回收借用溢出，
 * 不动保证分区和因果淘汰策略。 */
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
	// 两类权限各自滚动；主体先淘汰自身历史，不能挤掉其他活跃主体证据。
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
	/* The next workflow fence binds the monotonic gap counter. */
}

static int agent_audit_emit(int kind, uint64 tick, struct proc *actor,
			    int source_pid, int target_pid, int event_type,
			    int tool_id, int status, uint64 cause_sequence,
			    uint64 span_id, uint64 span_owner,
			    uint64 audit_principal, int identity_class,
			    int authority_effect,
			    uint64 value0, uint64 value1, uint64 value2,
			    uint64 flags, char *text, uint64 evidence_ticket)
{
	struct agent_audit_record *record;
	struct agent_audit_scope_state *scope_state;
	struct thread *thread = curr_thread();
	uint scope_id = agent_identity_proc_scope(actor);
	uint64 principal;
	uint64 sequence;
	uint64 identity_reserve;
	struct workflow_lifecycle_key receipt_lifecycle =
		vfs_proc_lifecycle(actor);
	int low_class;
	int slot;

	if (evidence_ticket != 0 && kind != AGENT_AUDIT_KIND_CONTEXT)
		return -1;
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
	// 只有内核确认的特权状态迁移进入保护分区；遥测、IPC 和用户 Context
	// 始终属于普通记录，不能借公开 span 标识抬高等级。
	low_class = identity_class != AGENT_AUDIT_ID_AUTHORITY;
	if (!agent_observe_scope_valid(scope_id) ||
	    (scope_state =
		     agent_observe_audit_scope_state_get(
			     scope_id, receipt_lifecycle, 1)) == 0)
		return -1;
	if (scope_state->total_records == ~0ULL)
		return -1;
	if (agent_audit_next_sequence == 0) {
		agent_audit_note_drop(scope_state);
		return -1;
	}
	slot = agent_observe_audit_slot_alloc(scope_state, principal, low_class,
					      span_id, span_owner, kind);
	if (slot < 0) {
		agent_audit_note_drop(scope_state);
		return -1;
	}
	sequence = agent_observe_alloc_id(
		&agent_audit_next_sequence, AGENT_IDENTITY_ALLOCATOR_AUDIT,
		identity_reserve);
	if (sequence == 0) {
		agent_audit_note_drop(scope_state);
		return -1;
	}
	if (agent_audit_slot_unpublish(slot) < 0)
		return -1;
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
		record->workflow_lifecycle_id = receipt_lifecycle.id;
		record->workflow_lifecycle_generation =
			receipt_lifecycle.generation;
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
	agent_audit_receipts[slot].state = evidence_ticket != 0 ?
		AGENT_AUDIT_RECEIPT_PENDING : AGENT_AUDIT_RECEIPT_FAILED;
	agent_audit_receipts[slot].receipt_id = agent_audit_receipt_id(
		record, scope_id, evidence_ticket, 0);
	agent_audit_scopes[slot] = scope_id;
	agent_audit_principals[slot] = principal;
	agent_audit_span_owners[slot] = span_owner;
	agent_audit_evidence_tickets[slot] = evidence_ticket;
	agent_audit_low_class[slot] = low_class;
	if (agent_observe_audit_index_insert(scope_state, slot) < 0) {
		agent_audit_slot_clear(slot);
		return -1;
	}
	if (evidence_ticket != 0)
		scope_state->evidence_linked_records++;
	scope_state->ledger_hash = record->record_hash;
	scope_state->total_records++;
	if (kind >= 0 && (uint)kind < AGENT_AUDIT_KIND_SLOT_COUNT) {
		scope_state->kind_counts[kind]++;
	}
	agent_audit_head = (slot + 1) % AGENT_AUDIT_MAX_RECORDS;
	agent_observe_timeline_record_audit(scope_id, record, span_owner);
	return 0;
}

static int agent_audit_context(struct proc *p,
			       struct agent_context_record *record,
			       uint64 span_owner, int source_pid,
			       uint64 cause_control,
			       int authority_effect, int causal_audit,
			       uint64 evidence_ticket)
{
	uint64 principal;
	int identity_class;

	if (p == 0 || record == 0 || record->sequence == 0)
		return -1;
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
	return agent_audit_emit(
		AGENT_AUDIT_KIND_CONTEXT, record->tick, p, source_pid,
			 p->pid, 0, record->tool_id, record->status,
			 record->cause_sequence, record->span_id,
			 span_owner, principal, identity_class,
			 authority_effect,
			 record->value0, record->value1, record->value2,
			 record->flags,
			 record->result[0] ? record->result : record->payload,
			 evidence_ticket);
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
			 0, event->payload, 0);
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
			 "sched", 0);
}

int
agent_observe_scope_reclaim(uint scope_id)
{
	struct agent_audit_scope_state *state;
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();

	if (!agent_observe_scope_valid(scope_id) ||
	    vfs_scope_lifecycle(scope_id, &lifecycle) < 0)
		return -1;
	if (agent_evidence_reclaim(lifecycle) < 0)
		return -1;
	state = agent_observe_audit_scope_state_get(scope_id, lifecycle, 0);
	while (state != 0 && state->visible_records > 0) {
		int slot = state->sequence_slots[state->visible_records - 1];
		if (agent_audit_slot_unpublish(slot) < 0)
			break;
	}
	/* 有序索引为准；兜底清除内部索引失配后遗留的证据。 */
	for (int i = 0; i < AGENT_AUDIT_MAX_RECORDS; i++)
		if (agent_audit_scopes[i] == scope_id) {
			agent_audit_slot_clear(i);
		}
	if (state != 0) {
		memset(state, 0, sizeof(*state));
	}
	return 0;
}

int
agent_observe_ledger_record_context(struct proc *p,
				    struct agent_context_record *record,
				    uint64 span_owner, int source_pid,
				    uint64 cause_control,
				    int authority_effect, int causal_audit,
				    uint64 evidence_ticket)
{
	if (p == 0 || record == 0)
		return -1;
	return agent_audit_context(p, record, span_owner, source_pid,
				   cause_control, authority_effect, causal_audit,
				   evidence_ticket);
}

void
agent_observe_ledger_record_sched(struct proc *p,
				  struct agent_sched_record *record)
{
	if (p == 0 || record == 0 ||
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
			 text, 0);
}
