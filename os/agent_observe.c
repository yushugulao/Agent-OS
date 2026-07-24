#include "agent_context.h"
#include "agent_internal.h"
#include "defs.h"
#include "kernel_work.h"
#include "timer.h"
#include "trap.h"
#include "vfs_security.h"

#define AGENT_OBSERVE_QUERY_WORK_GRANULE 16U
#define AGENT_AUDIT_SCOPE_LIMIT 128
#define AGENT_AUDIT_LOW_SCOPE_LIMIT (AGENT_AUDIT_SCOPE_LIMIT / 2)
#define AGENT_AUDIT_HIGH_SCOPE_LIMIT \
	(AGENT_AUDIT_SCOPE_LIMIT - AGENT_AUDIT_LOW_SCOPE_LIMIT)
#define AGENT_AUDIT_LOW_PRINCIPAL_LIMIT 16
#define AGENT_AUDIT_SCOPE_PRINCIPALS \
	(PROC_RESERVED_SLOTS / VFS_SCOPE_MAX_ACTIVE)
#define AGENT_AUDIT_HIGH_PRINCIPAL_LIMIT \
	(AGENT_AUDIT_HIGH_SCOPE_LIMIT / AGENT_AUDIT_SCOPE_PRINCIPALS)
#define AGENT_PREFETCH_SCOPE_LIMIT (AGENT_FILE_PREFETCH_SPAN_MAX / 4)

extern struct proc pool[NPROC];

_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_AUDIT_SCOPE_LIMIT <=
	       AGENT_AUDIT_MAX_RECORDS,
	       "audit table must reserve every workflow partition");
_Static_assert(AGENT_AUDIT_LOW_PRINCIPAL_LIMIT <=
	       AGENT_AUDIT_LOW_SCOPE_LIMIT &&
	       PROC_RESERVED_SLOTS % VFS_SCOPE_MAX_ACTIVE == 0 &&
	       AGENT_AUDIT_HIGH_SCOPE_LIMIT %
		       AGENT_AUDIT_SCOPE_PRINCIPALS == 0 &&
	       AGENT_AUDIT_HIGH_PRINCIPAL_LIMIT <=
		       AGENT_AUDIT_HIGH_SCOPE_LIMIT &&
	       AGENT_AUDIT_LOW_SCOPE_LIMIT < AGENT_AUDIT_SCOPE_LIMIT,
	       "audit table must reserve privileged workflow evidence");
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_PREFETCH_SCOPE_LIMIT <=
	       AGENT_FILE_PREFETCH_SPAN_MAX,
	       "prefetch table must reserve every workflow partition");
_Static_assert(KERNEL_WORK_OPERATION_UNITS <= KERNEL_WORK_BUDGET_UNITS,
	       "observation query work must fit one scheduling checkpoint");

struct agent_audit_scope_state {
	int used;
	uint scope_id;
	uint visible_records;
	uint64 total_records;
	uint64 ledger_hash;
	uint64 kind_counts[AGENT_AUDIT_KIND_PREFETCH + 1];
	uint64 observe_epoch;
	ushort sequence_slots[AGENT_AUDIT_SCOPE_LIMIT];
	ushort timeline_slots[AGENT_AUDIT_SCOPE_LIMIT];
};

static uint64 next_span_id;
static uint64 next_event_id;
static uint64 agent_audit_next_sequence;
static uint64 agent_audit_head;
static uint64 agent_audit_count;
static uint64 agent_audit_ledger_hash;
static uint64 agent_audit_kind_counts[AGENT_AUDIT_KIND_PREFETCH + 1];
static struct agent_audit_record agent_audit_records[AGENT_AUDIT_MAX_RECORDS];
static uint agent_audit_scopes[AGENT_AUDIT_MAX_RECORDS];
static uint64 agent_audit_principals[AGENT_AUDIT_MAX_RECORDS];
static uint64 agent_audit_span_owners[AGENT_AUDIT_MAX_RECORDS];
static uchar agent_audit_low_class[AGENT_AUDIT_MAX_RECORDS];
static struct agent_audit_scope_state agent_audit_scope_states[NPROC];
static uint64 agent_span_prefetch_next_sequence;
static uint64 agent_span_prefetch_head;
static uint64 agent_span_prefetch_count;
static struct agent_file_prefetch_hint
	agent_span_prefetch_hints[AGENT_FILE_PREFETCH_SPAN_MAX];
static uint agent_span_prefetch_scopes[AGENT_FILE_PREFETCH_SPAN_MAX];
static uint64 agent_span_prefetch_owners[AGENT_FILE_PREFETCH_SPAN_MAX];
static int agent_timeline_waiting_agents;

static void agent_observe_record(uint, struct agent_timeline_record *, uint64);
static void agent_timeline_from_audit(struct agent_audit_record *,
				      struct agent_timeline_record *);
static void agent_timeline_from_context(struct proc *,
					struct agent_context_record *,
					struct agent_timeline_record *);
static void agent_timeline_from_prefetch(
	struct proc *, struct agent_file_prefetch_hint *,
	struct agent_timeline_record *);
static void agent_timeline_from_sched(struct agent_sched_record *,
				      struct agent_timeline_record *);

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
agent_observe_alloc_id(uint64 *next)
{
	uint64 id = *next;

	if (id == 0)
		return 0;
	if (id == ~0ULL)
		*next = 0;
	else
		*next = id + 1;
	return id;
}

void
agent_observe_init(void)
{
	next_span_id = 1;
	next_event_id = 1;
	agent_audit_next_sequence = 1;
	agent_audit_head = 0;
	agent_audit_count = 0;
	agent_audit_ledger_hash = 0;
	memset(agent_audit_kind_counts, 0, sizeof(agent_audit_kind_counts));
	memset(agent_audit_records, 0, sizeof(agent_audit_records));
	memset(agent_audit_scopes, 0, sizeof(agent_audit_scopes));
	memset(agent_audit_principals, 0, sizeof(agent_audit_principals));
	memset(agent_audit_span_owners, 0, sizeof(agent_audit_span_owners));
	memset(agent_audit_low_class, 0, sizeof(agent_audit_low_class));
	memset(agent_audit_scope_states, 0,
	       sizeof(agent_audit_scope_states));
	agent_span_prefetch_next_sequence = 1;
	agent_span_prefetch_head = 0;
	agent_span_prefetch_count = 0;
	agent_timeline_waiting_agents = 0;
	memset(agent_span_prefetch_hints, 0,
	       sizeof(agent_span_prefetch_hints));
	memset(agent_span_prefetch_scopes, 0,
	       sizeof(agent_span_prefetch_scopes));
	memset(agent_span_prefetch_owners, 0,
	       sizeof(agent_span_prefetch_owners));
}

uint64
agent_observe_alloc_span_id(void)
{
	return agent_observe_alloc_id(&next_span_id);
}

uint64
agent_observe_alloc_event_id(void)
{
	uint64 id = next_event_id;

	next_event_id++;
	return id;
}

int
agent_observe_query_reserve(uint64 records)
{
	uint64 batches;
	uint64 checkpoint_batches;
	uint64 max_checkpoint_batches;

	if (records == 0)
		return 0;
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
				   KERNEL_WORK_OPERATION_UNITS)) < 0)
			return -1;
		batches -= checkpoint_batches;
	}
	return 0;
}

int
agent_observe_query_reserve_to(uint64 records, uint64 *reserved)
{
	uint64 additional;

	if (reserved == 0 || records <= *reserved)
		return 0;
	additional = records - *reserved;
	/*
	 * Publish the reservation before a possible yield.  The caller recounts
	 * after each checkpoint and tops up if another thread grew a source.
	 */
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
	h = agent_observe_hash_mix(h, record->value0);
	h = agent_observe_hash_mix(h, record->value1);
	h = agent_observe_hash_mix(h, record->value2);
	h = agent_observe_hash_mix(h, record->flags);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->kind);
	h = agent_observe_hash_mix(h, (uint64)(uint)record->pid);
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


/* Timeline waiter accounting. */
static void agent_timeline_waiting_set(struct proc *p, int waiting)
{
	if (p == 0)
		return;
	waiting = waiting ? 1 : 0;
	if (p->agent_timeline_waiting == waiting)
		return;
	if (waiting)
		agent_timeline_waiting_agents++;
	else if (agent_timeline_waiting_agents > 0)
		agent_timeline_waiting_agents--;
	p->agent_timeline_waiting = waiting;
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

static int agent_audit_principal_live(uint scope_id, uint64 principal)
{
	if (principal == 0)
		return 0;
	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (proc_teardown_live(p) && p->is_agent &&
		    p->agent_control_id == principal &&
		    agent_identity_proc_scope(p) == scope_id)
			return 1;
	}
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

static int
agent_observe_audit_slot_alloc(struct agent_audit_scope_state *state,
			       uint64 principal, int low_class)
{
	uint scope_id;
	int high_owned = 0;
	int low_owned = 0;
	int principal_owned = 0;
	int oldest_low_slot = -1;
	int oldest_principal_slot = -1;

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
			if (oldest_low_slot < 0)
				oldest_low_slot = i;
		} else {
			high_owned++;
		}
		if (agent_audit_low_class[i] == low_class &&
		    agent_audit_principals[i] == principal) {
			principal_owned++;
			if (oldest_principal_slot < 0)
				oldest_principal_slot = i;
		}
	}
	// The two authority classes have independent rolling partitions. Every
	// principal first rolls its own history, so neither telemetry nor a noisy
	// writer can evict another principal's privileged workflow evidence.
	if (principal_owned >= (low_class ?
			       AGENT_AUDIT_LOW_PRINCIPAL_LIMIT :
			       AGENT_AUDIT_HIGH_PRINCIPAL_LIMIT))
		return oldest_principal_slot;
	if (low_class && low_owned >= AGENT_AUDIT_LOW_SCOPE_LIMIT)
		return oldest_low_slot;
	if (!low_class && high_owned >= AGENT_AUDIT_HIGH_SCOPE_LIMIT) {
		for (uint j = 0; j < state->visible_records; j++) {
			int i = state->sequence_slots[j];

			if (agent_audit_low_class[i] ||
			    agent_audit_principal_live(
				    scope_id, agent_audit_principals[i]))
				continue;
			return i;
		}
		return -1;
	}
	for (int i = 0; i < AGENT_AUDIT_MAX_RECORDS; i++) {
		int slot = (agent_audit_head + i) %
			   AGENT_AUDIT_MAX_RECORDS;

		if (agent_audit_scopes[slot] == VFS_SCOPE_NONE)
			return slot;
	}
	return low_class ? oldest_low_slot : -1;
}

static void agent_audit_emit(int kind, uint64 tick, struct proc *actor,
			     int source_pid, int target_pid, int event_type,
			     int tool_id, int status, uint64 cause_sequence,
			     uint64 span_id, uint64 span_owner,
			     uint64 audit_principal, int authority_effect,
			     uint64 value0, uint64 value1, uint64 value2,
			     uint64 flags, char *text)
{
	struct agent_audit_record *record;
	struct agent_audit_scope_state *scope_state;
	struct agent_timeline_record timeline;
	uint scope_id = agent_identity_proc_scope(actor);
	uint64 principal;
	int low_class;
	int slot;

	if (span_id == 0 || span_owner == 0) {
		span_id = 0;
		span_owner = 0;
	}
	authority_effect = authority_effect && actor != 0 &&
			   actor->agent_control_id != 0 &&
			   agent_identity_has_any_cap(actor, AGENT_AUDIT_PRIVILEGED_CAPS);
	principal = authority_effect ? actor->agent_control_id : audit_principal;
	if (principal == 0 && actor != 0)
		principal = actor->agent_control_id;
	// Only an explicit kernel-confirmed privileged state transition enters
	// the protected partition. Telemetry, IPC and user-written Context are
	// always general records, irrespective of the public span identifier.
	low_class = !authority_effect;
	if (!agent_observe_scope_valid(scope_id) ||
	    (scope_state =
		     agent_observe_audit_scope_state_get(scope_id, 1)) == 0 ||
	    (slot = agent_observe_audit_slot_alloc(scope_state, principal,
						   low_class)) < 0)
		return;
	if (agent_audit_slot_unpublish(slot) < 0)
		return;
	record = &agent_audit_records[slot];
	record->sequence = agent_audit_next_sequence++;
	record->tick = tick;
	record->kind = kind;
	record->source_pid = source_pid;
	record->target_pid = target_pid;
	record->event_type = event_type;
	record->tool_id = tool_id;
	record->status = status;
	record->cause_sequence = cause_sequence;
	record->span_id = span_id;
	record->value0 = value0;
	record->value1 = value1;
	record->value2 = value2;
	record->flags = flags;
	if (actor) {
		record->pid = actor->pid;
		record->agent_id = actor->agent_id;
		record->role = actor->agent_role;
		record->loop_state = actor->loop_state;
	}
	safestrcpy(record->text, text ? text : "", sizeof(record->text));
	record->prev_hash = scope_state->ledger_hash;
	record->record_hash = agent_audit_record_hash(record);
	agent_audit_scopes[slot] = scope_id;
	agent_audit_principals[slot] = principal;
	agent_audit_span_owners[slot] = span_owner;
	agent_audit_low_class[slot] = low_class;
	if (agent_observe_audit_index_insert(scope_state, slot) < 0) {
		agent_audit_slot_clear(slot);
		return;
	}
	scope_state->ledger_hash = record->record_hash;
	scope_state->total_records++;
	agent_audit_ledger_hash = record->record_hash;
	if (kind >= 0 && kind <= AGENT_AUDIT_KIND_PREFETCH) {
		scope_state->kind_counts[kind]++;
		agent_audit_kind_counts[kind]++;
	}
	agent_audit_head = (slot + 1) % AGENT_AUDIT_MAX_RECORDS;
	agent_audit_count++;
	agent_timeline_from_audit(record, &timeline);
	agent_observe_record(scope_id, &timeline, span_owner);
}

static void agent_audit_context(struct proc *p,
				struct agent_context_record *record,
				int authority_effect)
{
	uint64 principal;
	uint64 span_owner;
	uint64 cause_control;
	uint64 slot;
	int source_pid;

	if (p == 0 || record == 0 || record->sequence == 0 ||
	    p->context_path_capacity == 0)
		return;
	slot = (record->sequence - 1) % p->context_path_capacity;
	if (agent_context_load_attribution(p, slot, &span_owner, &source_pid,
					   &cause_control) < 0)
		return;
	source_pid = cause_control != 0 ? source_pid : p->pid;
	principal = cause_control != 0 ? cause_control : p->agent_control_id;
	if (source_pid <= 0)
		source_pid = p->pid;
	if (principal == 0)
		principal = p->agent_control_id;
	agent_audit_emit(AGENT_AUDIT_KIND_CONTEXT, record->tick, p, source_pid,
			 p->pid, 0, record->tool_id, record->status,
			 record->cause_sequence, record->span_id,
			 span_owner, principal,
			 authority_effect,
			 record->value0, record->value1, record->value2,
			 record->flags,
			 record->result[0] ? record->result : record->payload);
}

static void agent_audit_event(int kind, struct proc *actor,
			      struct agent_event *event, uint64 span_owner,
			      uint64 audit_principal)
{
	if (event == 0)
		return;
	agent_audit_emit(kind, event->tick, actor, event->source_pid,
			 event->target_pid, event->type, 0, event->status,
			 event->cause_sequence, event->span_id, span_owner,
			 audit_principal, 0,
			 event->event_id, event->corr_id, event->target_pid,
			 0, event->payload);
}

static void agent_audit_sched(struct proc *p, struct agent_sched_record *record)
{
	if (p == 0 || record == 0)
		return;
	agent_audit_emit(AGENT_AUDIT_KIND_SCHED, record->tick, p, p->pid,
			 p->pid, 0, 0, AGENT_STATUS_OK, 0, 0,
			 0, p->agent_control_id, 0,
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
			 span_owner, source_control_id, 0,
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

static int agent_span_prefetch_scope_next(
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


/* Timeline wake operation. */
void agent_observe_wake_timeline_waiters(struct proc *p)
{
	wait_queue_wake_all(&p->agent_timeline_waiters);
}


/* Audit, provenance, and timeline queries. */
static uint agent_audit_scope_visible(uint scope_id)
{
	struct agent_audit_scope_state *state;

	state = agent_observe_audit_scope_state_get(scope_id, 0);
	return state ? state->visible_records : 0;
}

static int
agent_observe_audit_scope_record(struct agent_audit_scope_state *state,
				 uint index, int timeline_order,
				 struct agent_audit_record *out,
				 uint64 *span_owner)
{
	int slot;

	if (state == 0 || index >= state->visible_records)
		return 0;
	slot = timeline_order ? state->timeline_slots[index] :
				state->sequence_slots[index];
	if (slot < 0 || slot >= AGENT_AUDIT_MAX_RECORDS ||
	    agent_audit_scopes[slot] != state->scope_id)
		return 0;
	if (out)
		*out = agent_audit_records[slot];
	if (span_owner)
		*span_owner = agent_audit_span_owners[slot];
	return 1;
}

int sys_agent_audit_snapshot(uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_audit_scope_state *state;
	struct agent_audit_record record;
	uint64 reserved = 0;
	uint64 visible;
	uint scope_id;
	int copied = 0;
	int limit;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	scope_id = agent_identity_proc_scope(p);
	visible = agent_audit_scope_visible(scope_id);
	if (max == 0)
		return visible;
	if (recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	for (;;) {
		visible = agent_audit_scope_visible(scope_id);
		limit = max < (int)visible ? max : (int)visible;
		if ((uint64)limit <= reserved)
			break;
		if (agent_observe_query_reserve_to(limit, &reserved) < 0)
			return -1;
	}
	state = agent_observe_audit_scope_state_get(scope_id, 0);
	visible = state ? state->visible_records : 0;
	limit = max < (int)visible ? max : (int)visible;
	for (int i = 0; i < limit; i++) {
		if (!agent_observe_audit_scope_record(state, i, 0, &record, 0))
			break;
		if (copyout(p->pagetable,
			    recordsaddr +
				    i * sizeof(struct agent_audit_record),
			    (char *)&record, sizeof(record)) < 0)
			return -1;
		copied++;
	}
	return copied;
}

int sys_agent_ledger_snapshot(uint64 summaryaddr)
{
	struct proc *p = curr_proc();
	struct agent_audit_scope_state *scope_state;
	struct agent_ledger_summary summary;
	uint64 visible;
	uint64 prefetch_visible = 0;
	uint scope_id;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (summaryaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	scope_id = agent_identity_proc_scope(p);
	scope_state = agent_observe_audit_scope_state_get(scope_id, 0);
	memset(&summary, 0, sizeof(summary));
	visible = scope_state ? scope_state->visible_records : 0;
	for (int i = 0; i < AGENT_FILE_PREFETCH_SPAN_MAX; i++)
		if (agent_span_prefetch_scopes[i] == scope_id)
			prefetch_visible++;
	summary.version = AGENT_LEDGER_VERSION;
	summary.visible_records = visible;
	summary.total_records = scope_state ? scope_state->total_records : 0;
	summary.dropped_records =
		summary.total_records > visible ?
			summary.total_records - visible :
			0;
	if (visible > 0) {
		int oldest_slot = scope_state->sequence_slots[0];
		int latest_slot =
			scope_state->sequence_slots[visible - 1];

		summary.oldest_sequence =
			agent_audit_records[oldest_slot].sequence;
		summary.latest_sequence =
			agent_audit_records[latest_slot].sequence;
	}
	summary.ledger_hash = scope_state ? scope_state->ledger_hash : 0;
	summary.context_records = scope_state ?
		scope_state->kind_counts[AGENT_AUDIT_KIND_CONTEXT] : 0;
	summary.event_records =
		scope_state ?
			scope_state->kind_counts[AGENT_AUDIT_KIND_EVENT_ENQUEUE] +
				scope_state->kind_counts[AGENT_AUDIT_KIND_EVENT_CONSUME] :
			0;
	summary.sched_records = scope_state ?
		scope_state->kind_counts[AGENT_AUDIT_KIND_SCHED] : 0;
	summary.prefetch_records =
		scope_state ?
			scope_state->kind_counts[AGENT_AUDIT_KIND_PREFETCH] : 0;
	summary.timeline_total = summary.total_records + prefetch_visible;
	summary.observe_epoch = scope_state ? scope_state->observe_epoch : 0;
	return copyout(p->pagetable, summaryaddr, (char *)&summary,
		       sizeof(summary));
}

static int agent_audit_match(struct agent_audit_record *record,
			     struct agent_audit_filter *filter)
{
	uint64 flags = filter->flags;

	if ((flags & AGENT_AUDIT_FILTER_START_SEQUENCE) &&
	    record->sequence < filter->start_sequence)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_SPAN_ID) &&
	    record->span_id != filter->span_id)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_KIND) && record->kind != filter->kind)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_PID) && record->pid != filter->pid)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_SOURCE_PID) &&
	    record->source_pid != filter->source_pid)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_TARGET_PID) &&
	    record->target_pid != filter->target_pid)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_ROLE) && record->role != filter->role)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_TOOL_ID) &&
	    record->tool_id != filter->tool_id)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_EVENT_TYPE) &&
	    record->event_type != filter->event_type)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_STATUS) &&
	    record->status != filter->status)
		return 0;
	return 1;
}

int sys_agent_audit_query(uint64 filteraddr, uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_audit_scope_state *state;
	struct agent_audit_filter filter;
	struct agent_audit_record record;
	uint64 reserved = 0;
	uint scope_id;
	uint visible;
	int matched = 0;
	int copied = 0;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	memset(&filter, 0, sizeof(filter));
	if (filteraddr &&
	    copyin(p->pagetable, (char *)&filter, filteraddr,
		   sizeof(filter)) < 0)
		return -1;
	if (max > 0 && recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	scope_id = agent_identity_proc_scope(p);
	for (;;) {
		visible = agent_audit_scope_visible(scope_id);
		if (visible <= reserved)
			break;
		if (agent_observe_query_reserve_to(visible, &reserved) < 0)
			return -1;
	}
	state = agent_observe_audit_scope_state_get(scope_id, 0);
	visible = state ? state->visible_records : 0;
	for (uint i = 0; i < visible; i++) {
		if (!agent_observe_audit_scope_record(state, i, 0, &record, 0))
			break;
		if (!agent_audit_match(&record, &filter))
			continue;
		matched++;
		if (max == 0)
			continue;
		if (copyout(p->pagetable,
			    recordsaddr +
				    copied * sizeof(struct agent_audit_record),
			    (char *)&record, sizeof(record)) < 0)
			return -1;
		copied++;
		if (copied >= max)
			break;
	}
	if (max == 0)
		return matched;
	return copied;
}

int sys_agent_span_trace_snapshot(uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_audit_scope_state *state;
	struct agent_audit_record record;
	uint64 span_id;
	uint64 span_owner;
	uint64 record_span_owner;
	uint64 reserved = 0;
	uint scope_id;
	uint visible;
	int matched = 0;
	int copied = 0;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_AUDIT_WRITE))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (max > 0 && recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	span_id = p->agent_current_span_id;
	span_owner = p->agent_current_span_owner;
	if (span_id == 0 || span_owner == 0)
		return 0;
	scope_id = agent_identity_proc_scope(p);
	for (;;) {
		visible = agent_audit_scope_visible(scope_id);
		if (visible <= reserved)
			break;
		if (agent_observe_query_reserve_to(visible, &reserved) < 0)
			return -1;
	}
	span_id = p->agent_current_span_id;
	span_owner = p->agent_current_span_owner;
	if (span_id == 0 || span_owner == 0)
		return 0;
	state = agent_observe_audit_scope_state_get(scope_id, 0);
	visible = state ? state->visible_records : 0;
	for (uint i = 0; i < visible; i++) {
		if (!agent_observe_audit_scope_record(state, i, 0, &record,
					      &record_span_owner))
			break;
		if (record.span_id != span_id || record_span_owner != span_owner)
			continue;
		matched++;
		if (max == 0)
			continue;
		if (copyout(p->pagetable,
			    recordsaddr +
				    copied * sizeof(struct agent_audit_record),
			    (char *)&record, sizeof(record)) < 0)
			return -1;
		copied++;
		if (copied >= max)
			break;
	}
	if (max == 0)
		return matched;
	return copied;
}

static int agent_provenance_emit(struct proc *p, uint64 edgesaddr, int max,
				 int *matched, int *copied,
				 struct agent_provenance_edge *edge)
{
	(*matched)++;
	if (max == 0 || *copied >= max)
		return 0;
	if (copyout(p->pagetable,
		    edgesaddr + *copied * sizeof(struct agent_provenance_edge),
		    (char *)edge, sizeof(*edge)) < 0)
		return -1;
	(*copied)++;
	return 0;
}

static void agent_provenance_from_context(struct proc *p,
					  struct agent_context_record *record,
					  int cause_pid,
					  struct agent_provenance_edge *edge)
{
	struct thread *t = curr_thread();

	memset(edge, 0, sizeof(*edge));
	edge->kind = AGENT_PROVENANCE_EDGE_CONTEXT;
	edge->source_type = AGENT_PROVENANCE_NODE_CONTEXT;
	edge->target_type = AGENT_PROVENANCE_NODE_CONTEXT;
	edge->source_pid = cause_pid;
	edge->target_pid = p->pid;
	edge->source_sequence = record->cause_sequence;
	edge->target_sequence = record->sequence;
	edge->span_id = record->span_id;
	edge->tick = record->tick;
	edge->flags = record->flags;
	edge->value0 = record->value0;
	edge->value1 = record->value1;
	edge->value2 = t ? t->tid : 0;
	edge->role = p->agent_role;
	edge->tool_id = record->tool_id;
	edge->status = record->status;
	safestrcpy(edge->text,
		   record->result[0] ? record->result : record->payload,
		   sizeof(edge->text));
}

static void agent_provenance_from_audit(struct agent_audit_record *record,
					struct agent_provenance_edge *edge)
{
	memset(edge, 0, sizeof(*edge));
	edge->kind = AGENT_PROVENANCE_EDGE_AUDIT;
	edge->source_type = AGENT_PROVENANCE_NODE_CONTEXT;
	edge->target_type = AGENT_PROVENANCE_NODE_AUDIT;
	edge->source_pid = record->source_pid ? record->source_pid :
						 record->pid;
	edge->target_pid = record->target_pid ? record->target_pid :
						 record->pid;
	edge->source_sequence = record->cause_sequence;
	edge->target_sequence = record->sequence;
	edge->span_id = record->span_id;
	edge->tick = record->tick;
	edge->flags = record->flags;
	edge->value0 = record->value0;
	edge->value1 = record->value1;
	edge->value2 = record->value2;
	edge->role = record->role;
	edge->tool_id = record->tool_id;
	edge->event_type = record->event_type;
	edge->status = record->status;
	safestrcpy(edge->text, record->text, sizeof(edge->text));
}

static void agent_provenance_from_prefetch(
	struct proc *p, struct agent_file_prefetch_hint *hint,
	struct agent_provenance_edge *edge)
{
	memset(edge, 0, sizeof(*edge));
	edge->kind = AGENT_PROVENANCE_EDGE_PREFETCH;
	edge->source_type = AGENT_PROVENANCE_NODE_CONTEXT;
	edge->target_type = AGENT_PROVENANCE_NODE_PREFETCH;
	edge->source_pid = hint->source_pid ? hint->source_pid : p->pid;
	edge->target_pid = hint->target_pid ? hint->target_pid : p->pid;
	edge->source_sequence = hint->source_sequence;
	edge->target_sequence = hint->sequence;
	edge->span_id = hint->span_id;
	edge->tick = hint->tick;
	edge->flags = hint->reason;
	edge->value0 = hint->source_fid;
	edge->value1 = hint->fid;
	edge->value2 = hint->candidate_records;
	edge->role = p->agent_role;
	edge->tool_id = AGENT_TOOL_QUERY_FILE;
	edge->status = AGENT_STATUS_OK;
	safestrcpy(edge->text,
		   hint->hit.stage[0] ? hint->hit.stage :
					hint->hit.physical_name,
		   sizeof(edge->text));
}

int sys_agent_provenance_snapshot(uint64 edgesaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_audit_scope_state *audit_state;
	struct agent_context_record context_record;
	struct agent_audit_record audit_record;
	struct agent_file_prefetch_hint hint;
	struct agent_provenance_edge edge;
	uint64 context_visible;
	uint64 audit_visible;
	uint64 prefetch_visible;
	uint64 reserved = 0;
	uint64 scan_visible;
	uint64 seq;
	uint64 slot;
	uint64 start;
	uint64 span_id;
	uint64 span_owner;
	uint64 audit_span_owner;
	uint64 context_span_owner;
	uint64 context_cause_control;
	int audit_global;
	int audit_allowed;
	int context_cause_pid;
	int matched = 0;
	int copied = 0;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (max > 0 && edgesaddr == 0)
		return AGENT_STATUS_BAD_PARAM;

	audit_global = agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE);
	audit_allowed = audit_global || agent_identity_has_cap(p, AGENT_CAP_AUDIT_WRITE);
	for (;;) {
		context_visible = p->context_path_count;
		if (context_visible > p->context_path_capacity)
			context_visible = p->context_path_capacity;
		prefetch_visible = p->agent_prefetch_count;
		if (prefetch_visible > AGENT_FILE_PREFETCH_MAX_HINTS)
			prefetch_visible = AGENT_FILE_PREFETCH_MAX_HINTS;
		audit_visible = audit_allowed ?
					agent_audit_scope_visible(
						agent_identity_proc_scope(p)) :
					0;
		scan_visible = context_visible + audit_visible +
			       prefetch_visible;
		if (scan_visible <= reserved)
			break;
		if (agent_observe_query_reserve_to(scan_visible, &reserved) < 0)
			return -1;
	}

	span_id = p->agent_current_span_id;
	span_owner = p->agent_current_span_owner;
	context_visible = p->context_path_count;
	if (context_visible > p->context_path_capacity)
		context_visible = p->context_path_capacity;
	for (int i = 0; i < (int)context_visible; i++) {
		seq = p->context_path_oldest + i;
		slot = (seq - 1) % p->context_path_capacity;
		if (agent_context_read_record(p, slot, &context_record) < 0)
			return AGENT_STATUS_NO_SPACE;
		if (context_record.sequence != seq)
			continue;
		if (agent_context_load_attribution(
			    p, slot, &context_span_owner,
			    &context_cause_pid, &context_cause_control) < 0)
			return AGENT_STATUS_NO_SPACE;
		if (context_cause_pid <= 0 || context_cause_control == 0)
			continue;
		agent_provenance_from_context(
			p, &context_record, context_cause_pid, &edge);
		if (agent_provenance_emit(p, edgesaddr, max, &matched,
					  &copied, &edge) < 0)
			return -1;
		if (max > 0 && copied >= max)
			goto provenance_done;
	}

	audit_state = audit_allowed ?
			      agent_observe_audit_scope_state_get(agent_identity_proc_scope(p), 0) :
			      0;
	audit_visible = audit_state ? audit_state->visible_records : 0;
	for (uint i = 0; i < audit_visible; i++) {
		if (!agent_observe_audit_scope_record(audit_state, i, 0, &audit_record,
					      &audit_span_owner))
			break;
		if (!audit_global &&
		    (audit_record.span_id != span_id ||
		     audit_span_owner != span_owner))
			continue;
		if (audit_record.cause_sequence == 0)
			continue;
		agent_provenance_from_audit(&audit_record, &edge);
		if (agent_provenance_emit(p, edgesaddr, max, &matched,
					  &copied, &edge) < 0)
			return -1;
		if (max > 0 && copied >= max)
			goto provenance_done;
	}

	prefetch_visible = p->agent_prefetch_count;
	if (prefetch_visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		prefetch_visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	start = (p->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
		 prefetch_visible) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	for (int i = 0; i < (int)prefetch_visible; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_MAX_HINTS;
		memmove(&hint, &p->agent_prefetch_hints[slot], sizeof(hint));
		if (hint.source_sequence == 0)
			continue;
		agent_provenance_from_prefetch(p, &hint, &edge);
		if (agent_provenance_emit(p, edgesaddr, max, &matched,
					  &copied, &edge) < 0)
			return -1;
		if (max > 0 && copied >= max)
			break;
	}

provenance_done:
	if (max == 0)
		return matched;
	return copied;
}

static void agent_timeline_from_context(struct proc *p,
					struct agent_context_record *record,
					struct agent_timeline_record *timeline)
{
	struct thread *t = curr_thread();

	memset(timeline, 0, sizeof(*timeline));
	timeline->source = AGENT_TIMELINE_SOURCE_CONTEXT;
	timeline->kind = AGENT_TRACE_KIND_CONTEXT;
	timeline->tick = record->tick;
	timeline->sequence = record->sequence;
	timeline->cause_sequence = record->cause_sequence;
	timeline->span_id = record->span_id;
	timeline->value0 = record->value0;
	timeline->value1 = record->value1;
	timeline->value2 = record->value2;
	timeline->flags = record->flags;
	timeline->pid = p->pid;
	timeline->source_pid = p->pid;
	timeline->target_pid = p->pid;
	timeline->role = p->agent_role;
	timeline->loop_state = p->loop_state;
	timeline->tool_id = record->tool_id;
	timeline->status = record->status;
	timeline->tid = t ? t->tid : 0;
	safestrcpy(timeline->text,
		   record->result[0] ? record->result : record->payload,
		   sizeof(timeline->text));
}

static void agent_timeline_from_sched(struct agent_sched_record *record,
				      struct agent_timeline_record *timeline)
{
	memset(timeline, 0, sizeof(*timeline));
	timeline->source = AGENT_TIMELINE_SOURCE_SCHED;
	timeline->kind = AGENT_TRACE_KIND_SCHED;
	timeline->tick = record->tick;
	timeline->sequence = record->dispatch_count;
	timeline->value0 = record->score;
	timeline->value1 = record->event_queue_count;
	timeline->value2 = record->vruntime;
	timeline->flags = record->reason_flags;
	timeline->pid = record->pid;
	timeline->tid = record->tid;
	timeline->source_pid = record->pid;
	timeline->target_pid = record->pid;
	timeline->role = record->role;
	timeline->loop_state = record->loop_state;
	timeline->status = AGENT_STATUS_OK;
	safestrcpy(timeline->text, "sched", sizeof(timeline->text));
}

static void agent_timeline_from_audit(struct agent_audit_record *record,
				      struct agent_timeline_record *timeline)
{
	memset(timeline, 0, sizeof(*timeline));
	timeline->source = AGENT_TIMELINE_SOURCE_AUDIT;
	timeline->kind = record->kind;
	timeline->tick = record->tick;
	timeline->sequence = record->sequence;
	timeline->cause_sequence = record->cause_sequence;
	timeline->span_id = record->span_id;
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

static void agent_timeline_from_prefetch(struct proc *p,
					 struct agent_file_prefetch_hint *hint,
					 struct agent_timeline_record *timeline)
{
	memset(timeline, 0, sizeof(*timeline));
	timeline->source = AGENT_TIMELINE_SOURCE_PREFETCH;
	timeline->kind = hint->plan;
	timeline->tick = hint->tick;
	timeline->sequence = hint->sequence;
	timeline->cause_sequence = hint->source_sequence;
	timeline->span_id = hint->span_id;
	timeline->value0 = hint->fid;
	timeline->value1 = hint->source_fid;
	timeline->value2 = hint->candidate_records;
	timeline->flags = hint->reason;
	timeline->pid = p->pid;
	timeline->source_pid = hint->source_pid;
	timeline->target_pid = hint->target_pid;
	timeline->role = p->agent_role;
	timeline->loop_state = p->loop_state;
	timeline->tool_id = AGENT_TOOL_QUERY_FILE;
	timeline->status = AGENT_STATUS_OK;
	safestrcpy(timeline->text,
		   hint->hit.stage[0] ? hint->hit.stage : hint->hit.physical_name,
		   sizeof(timeline->text));
}

static int agent_timeline_load_context(struct proc *p, uint64 *cursor,
				       uint64 visible, uint64 oldest,
				       struct agent_timeline_record *timeline)
{
	struct agent_context_record record;
	uint64 seq;
	uint64 slot;

	while (*cursor < visible && p->context_path_capacity > 0) {
		seq = oldest + *cursor;
		(*cursor)++;
		slot = (seq - 1) % p->context_path_capacity;
		if (agent_context_read_record(p, slot, &record) < 0)
			return -1;
		if (record.sequence != seq)
			continue;
		agent_timeline_from_context(p, &record, timeline);
		return 1;
	}
	return 0;
}

static int agent_timeline_load_sched(struct proc *p, uint64 *cursor,
				     uint64 visible, uint64 start,
				     struct agent_timeline_record *timeline)
{
	struct agent_sched_record record;
	uint64 slot;

	if (*cursor >= visible)
		return 0;
	slot = (start + *cursor) % AGENT_SCHED_TRACE_CAP;
	(*cursor)++;
	memmove(&record, &p->agent_sched_records[slot], sizeof(record));
	agent_timeline_from_sched(&record, timeline);
	return 1;
}

static int agent_timeline_load_audit(
				     struct agent_audit_scope_state *state,
				     uint64 *cursor, int global,
				     uint64 span_id, uint64 span_owner,
				     struct agent_timeline_record *timeline)
{
	struct agent_audit_record record;
	uint64 record_span_owner;

	while (state != 0 && *cursor < state->visible_records) {
		uint index = (*cursor)++;

		if (!agent_observe_audit_scope_record(state, index, 1, &record,
					      &record_span_owner))
			return 0;
		if (!global &&
		    (record.span_id != span_id ||
		     record_span_owner != span_owner))
			continue;
		agent_timeline_from_audit(&record, timeline);
		return 1;
	}
	return 0;
}

static int agent_timeline_load_prefetch(struct proc *p, uint64 *cursor,
					uint64 visible, uint64 start,
					struct agent_timeline_record *timeline)
{
	struct agent_file_prefetch_hint hint;
	uint64 slot;

	if (*cursor >= visible)
		return 0;
	slot = (start + *cursor) % AGENT_FILE_PREFETCH_MAX_HINTS;
	(*cursor)++;
	memmove(&hint, &p->agent_prefetch_hints[slot], sizeof(hint));
	agent_timeline_from_prefetch(p, &hint, timeline);
	return 1;
}

static int agent_timeline_source_enabled(struct agent_timeline_filter *filter,
					 int source)
{
	if (filter == 0 || (filter->flags & AGENT_TIMELINE_FILTER_SOURCE_MASK) == 0)
		return 1;
	if (source <= 0 || source >= 64)
		return 0;
	return (filter->source_mask & (1ULL << source)) != 0;
}

static int agent_timeline_after_cursor(struct agent_timeline_filter *filter,
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

static int agent_timeline_match(struct agent_timeline_filter *filter,
				struct agent_timeline_record *record)
{
	if (filter == 0 || filter->flags == 0)
		return 1;
	if (!agent_timeline_source_enabled(filter, record->source))
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_START_TICK) &&
	    record->tick < filter->start_tick)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_AFTER_CURSOR) &&
	    !agent_timeline_after_cursor(filter, record))
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

static int agent_observe_record_visible(struct proc *p,
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

static void agent_observe_record(uint scope_id,
				 struct agent_timeline_record *record,
				 uint64 span_owner)
{
	struct agent_audit_scope_state *scope_state;

	if (record == 0 || !agent_observe_scope_valid(scope_id))
		return;
	scope_state = agent_observe_audit_scope_state_get(scope_id, 1);
	if (scope_state == 0)
		return;
	scope_state->observe_epoch++;
	if (scope_state->observe_epoch == 0)
		scope_state->observe_epoch = 1;
	if (agent_timeline_waiting_agents <= 0)
		return;
	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (p->state == P_UNUSED || !p->is_agent ||
		    !p->agent_timeline_waiting ||
		    agent_identity_proc_scope(p) != scope_id)
			continue;
		if (!agent_observe_record_visible(p, record, span_owner))
			continue;
		if (!agent_timeline_match(&p->agent_timeline_wait_filter,
					  record))
			continue;
		p->agent_observe_epoch = scope_state->observe_epoch;
		p->agent_timeline_wait_wakeup_count++;
		agent_observe_wake_timeline_waiters(p);
	}
}

static int agent_timeline_export(struct proc *p,
				 struct agent_timeline_filter *filter,
				 uint64 recordsaddr, int max)
{
	struct agent_audit_scope_state *audit_state;
	struct agent_timeline_record context_timeline;
	struct agent_timeline_record sched_timeline;
	struct agent_timeline_record audit_timeline;
	struct agent_timeline_record prefetch_timeline;
	struct agent_timeline_record *selected;
	uint64 context_visible;
	uint64 sched_visible;
	uint64 audit_scan_visible;
	uint64 prefetch_visible;
	uint64 reserved = 0;
	uint64 scan_visible;
	uint64 context_oldest;
	uint64 sched_start;
	uint64 prefetch_start;
	uint64 ci = 0;
	uint64 si = 0;
	uint64 ai = 0;
	uint64 pi = 0;
	uint64 best_tick;
	uint64 span_id;
	uint64 span_owner;
	int audit_global;
	int audit_allowed;
	int have_context;
	int have_sched;
	int have_audit;
	int have_prefetch;
	int copied = 0;
	int matched = 0;
	int total;
	int pick;

	context_visible = agent_timeline_source_enabled(
				  filter, AGENT_TIMELINE_SOURCE_CONTEXT) ?
				  p->context_path_count :
				  0;
	if (context_visible > p->context_path_capacity)
		context_visible = p->context_path_capacity;
	sched_visible = agent_timeline_source_enabled(
				filter, AGENT_TIMELINE_SOURCE_SCHED) ?
				p->agent_sched_trace_count :
				0;
	if (sched_visible > AGENT_SCHED_TRACE_CAP)
		sched_visible = AGENT_SCHED_TRACE_CAP;
	prefetch_visible = agent_timeline_source_enabled(
				   filter, AGENT_TIMELINE_SOURCE_PREFETCH) &&
				   agent_identity_has_cap(p, AGENT_CAP_META_READ) ?
				   p->agent_prefetch_count :
				   0;
	if (prefetch_visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		prefetch_visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	audit_global = agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE);
	audit_allowed = agent_timeline_source_enabled(
				filter, AGENT_TIMELINE_SOURCE_AUDIT) &&
				(audit_global ||
				 agent_identity_has_cap(p, AGENT_CAP_AUDIT_WRITE));
	audit_state = audit_allowed ?
			      agent_observe_audit_scope_state_get(agent_identity_proc_scope(p), 0) :
			      0;
	audit_scan_visible = audit_state ? audit_state->visible_records : 0;
	total = (int)(context_visible + sched_visible + prefetch_visible +
		      audit_scan_visible);
	if (max == 0 && (filter == 0 || filter->flags == 0) &&
	    (!audit_allowed || audit_global))
		return total;

	for (;;) {
		context_visible = agent_timeline_source_enabled(
					  filter,
					  AGENT_TIMELINE_SOURCE_CONTEXT) ?
					  p->context_path_count :
					  0;
		if (context_visible > p->context_path_capacity)
			context_visible = p->context_path_capacity;
		sched_visible = agent_timeline_source_enabled(
					filter, AGENT_TIMELINE_SOURCE_SCHED) ?
					p->agent_sched_trace_count :
					0;
		if (sched_visible > AGENT_SCHED_TRACE_CAP)
			sched_visible = AGENT_SCHED_TRACE_CAP;
		prefetch_visible = agent_timeline_source_enabled(
					   filter,
					   AGENT_TIMELINE_SOURCE_PREFETCH) &&
					   agent_identity_has_cap(p,
							 AGENT_CAP_META_READ) ?
					   p->agent_prefetch_count :
					   0;
		if (prefetch_visible > AGENT_FILE_PREFETCH_MAX_HINTS)
			prefetch_visible = AGENT_FILE_PREFETCH_MAX_HINTS;
		audit_state = audit_allowed ?
				      agent_observe_audit_scope_state_get(
					      agent_identity_proc_scope(p), 0) :
				      0;
		audit_scan_visible =
			audit_state ? audit_state->visible_records : 0;
		scan_visible = context_visible + sched_visible +
			       audit_scan_visible + prefetch_visible;
		if (scan_visible <= reserved)
			break;
		if (agent_observe_query_reserve_to(scan_visible, &reserved) < 0)
			return -1;
	}

	span_id = p->agent_current_span_id;
	span_owner = p->agent_current_span_owner;
	context_oldest = p->context_path_oldest;
	sched_start = p->agent_sched_trace_count > AGENT_SCHED_TRACE_CAP ?
			      p->agent_sched_trace_head :
			      0;
	prefetch_start =
		(p->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
		 prefetch_visible) %
		AGENT_FILE_PREFETCH_MAX_HINTS;

	have_context = agent_timeline_load_context(
		p, &ci, context_visible, context_oldest, &context_timeline);
	if (have_context < 0)
		return AGENT_STATUS_NO_SPACE;
	have_sched = agent_timeline_load_sched(p, &si, sched_visible,
					       sched_start,
					       &sched_timeline);
	have_audit = audit_allowed ?
			     agent_timeline_load_audit(audit_state, &ai,
						       audit_global,
						       span_id, span_owner,
						       &audit_timeline) :
			     0;
	have_prefetch = agent_timeline_load_prefetch(
		p, &pi, prefetch_visible, prefetch_start, &prefetch_timeline);

	while ((max == 0 || copied < max) &&
	       (have_context || have_sched || have_audit || have_prefetch)) {
		best_tick = (uint64)-1;
		pick = 0;
		selected = 0;
		if (have_context && context_timeline.tick <= best_tick) {
			best_tick = context_timeline.tick;
			selected = &context_timeline;
			pick = AGENT_TIMELINE_SOURCE_CONTEXT;
		}
		if (have_sched && sched_timeline.tick < best_tick) {
			best_tick = sched_timeline.tick;
			selected = &sched_timeline;
			pick = AGENT_TIMELINE_SOURCE_SCHED;
		}
		if (have_audit && audit_timeline.tick < best_tick) {
			best_tick = audit_timeline.tick;
			selected = &audit_timeline;
			pick = AGENT_TIMELINE_SOURCE_AUDIT;
		}
		if (have_prefetch && prefetch_timeline.tick < best_tick) {
			selected = &prefetch_timeline;
			pick = AGENT_TIMELINE_SOURCE_PREFETCH;
		}
		if (selected == 0)
			break;
		if (agent_timeline_match(filter, selected)) {
			matched++;
			if (max > 0) {
				if (copyout(p->pagetable,
					    recordsaddr +
						    copied *
							    sizeof(struct agent_timeline_record),
					    (char *)selected,
					    sizeof(*selected)) < 0)
					return -1;
				copied++;
			}
		}
		if (pick == AGENT_TIMELINE_SOURCE_CONTEXT) {
			have_context = agent_timeline_load_context(
				p, &ci, context_visible, context_oldest,
				&context_timeline);
			if (have_context < 0)
				return AGENT_STATUS_NO_SPACE;
		} else if (pick == AGENT_TIMELINE_SOURCE_SCHED) {
			have_sched = agent_timeline_load_sched(
				p, &si, sched_visible, sched_start,
				&sched_timeline);
		} else if (pick == AGENT_TIMELINE_SOURCE_AUDIT) {
			have_audit = agent_timeline_load_audit(
				audit_state, &ai, audit_global, span_id,
				span_owner,
				&audit_timeline);
		} else if (pick == AGENT_TIMELINE_SOURCE_PREFETCH) {
			have_prefetch = agent_timeline_load_prefetch(
				p, &pi, prefetch_visible, prefetch_start,
				&prefetch_timeline);
		}
	}
	return max == 0 ? matched : copied;
}

int sys_agent_timeline_snapshot(uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (max > 0 && recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	return agent_timeline_export(p, 0, recordsaddr, max);
}

int sys_agent_timeline_query(uint64 filteraddr, uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_timeline_filter filter;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (max > 0 && recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	memset(&filter, 0, sizeof(filter));
	if (filteraddr != 0 &&
	    copyin(p->pagetable, (char *)&filter, filteraddr,
		   sizeof(filter)) < 0)
		return -1;
	if ((filter.flags & ~AGENT_TIMELINE_FILTER_ALL_FLAGS) != 0)
		return AGENT_STATUS_BAD_PARAM;
	return agent_timeline_export(p, &filter, recordsaddr, max);
}

static int agent_timeline_wait_for_match(struct proc *p,
					 struct agent_timeline_filter *filter,
					 int timeout_ticks)
{
	uint64 start;
	uint64 now;
	int matched;

	start = agent_observe_ticks();
	p->agent_timeline_wait_count++;
	for (;;) {
		matched = agent_timeline_export(p, filter, 0, 0);
		if (matched < 0)
			return matched;
		if (matched > 0) {
			agent_timeline_waiting_set(p, 0);
			p->agent_timeline_wait_deadline_valid = 0;
			memset(&p->agent_timeline_wait_filter, 0,
			       sizeof(p->agent_timeline_wait_filter));
			p->agent_observe_epoch = agent_observe_scope_epoch(
				agent_identity_proc_scope(p));
			p->loop_state = AGENT_LOOP_IDLE;
			return matched;
		}
		now = agent_observe_ticks();
		if (timeout_ticks >= 0 &&
		    now - start >= (uint64)timeout_ticks) {
			agent_timeline_waiting_set(p, 0);
			p->agent_timeline_wait_deadline_valid = 0;
			memset(&p->agent_timeline_wait_filter, 0,
			       sizeof(p->agent_timeline_wait_filter));
			p->agent_timeline_wait_timeout_count++;
			p->loop_state = AGENT_LOOP_IDLE;
			return AGENT_STATUS_TIMEOUT;
		}
		p->loop_state = AGENT_LOOP_WAITING;
		agent_timeline_waiting_set(p, 1);
		memmove(&p->agent_timeline_wait_filter, filter,
			sizeof(p->agent_timeline_wait_filter));
		p->agent_observe_epoch = agent_observe_scope_epoch(
			agent_identity_proc_scope(p));
		if (timeout_ticks >= 0) {
			p->agent_timeline_wait_deadline_valid = 1;
			p->agent_timeline_wait_deadline = start + timeout_ticks;
		} else {
			p->agent_timeline_wait_deadline_valid = 0;
			p->agent_timeline_wait_deadline = 0;
		}
		p->agent_timeline_wait_sleep_count++;
		if (wait_queue_sleep(&p->agent_timeline_waiters) < 0) {
			agent_timeline_waiting_set(p, 0);
			p->agent_timeline_wait_deadline_valid = 0;
			p->loop_state = AGENT_LOOP_IDLE;
			return -1;
		}
	}
}

static int agent_timeline_copy_filter(struct proc *p, uint64 filteraddr,
				      struct agent_timeline_filter *filter)
{
	memset(filter, 0, sizeof(*filter));
	if (filteraddr != 0 &&
	    copyin(p->pagetable, (char *)filter, filteraddr,
		   sizeof(*filter)) < 0)
		return -1;
	if ((filter->flags & ~AGENT_TIMELINE_FILTER_ALL_FLAGS) != 0)
		return AGENT_STATUS_BAD_PARAM;
	return 0;
}

int sys_agent_timeline_wait(uint64 filteraddr, int timeout_ticks)
{
	struct proc *p = curr_proc();
	struct agent_timeline_filter filter;
	int rc;

	if (!p->is_agent)
		return -1;
	if (timeout_ticks < -1)
		return AGENT_STATUS_BAD_PARAM;
	rc = agent_timeline_copy_filter(p, filteraddr, &filter);
	if (rc < 0)
		return rc;
	return agent_timeline_wait_for_match(p, &filter, timeout_ticks);
}

int sys_agent_timeline_read(uint64 filteraddr, uint64 recordsaddr, int max,
			    int timeout_ticks)
{
	struct proc *p = curr_proc();
	struct agent_timeline_filter filter;
	uint64 bytes;
	int matched;
	int rc;

	if (!p->is_agent)
		return -1;
	if (max < 0 || max > AGENT_TIMELINE_MAX_RECORDS)
		return AGENT_STATUS_BAD_PARAM;
	if (timeout_ticks < -1)
		return AGENT_STATUS_BAD_PARAM;
	if (max > 0 && recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	bytes = (uint64)max * sizeof(struct agent_timeline_record);
	if (max > 0 &&
	    user_range_check(p->pagetable, recordsaddr, bytes, PTE_W) < 0)
		return -1;
	rc = agent_timeline_copy_filter(p, filteraddr, &filter);
	if (rc < 0)
		return rc;
	matched = agent_timeline_wait_for_match(p, &filter, timeout_ticks);
	if (matched <= 0 || max == 0)
		return matched;
	return agent_timeline_export(p, &filter, recordsaddr, max);
}

void
agent_observe_proc_init(struct proc *p)
{
	if (p == 0)
		return;
	agent_timeline_waiting_set(p, 0);
	p->agent_observe_epoch =
		agent_observe_scope_epoch(agent_identity_proc_scope(p));
	p->agent_timeline_wait_count = 0;
	p->agent_timeline_wait_sleep_count = 0;
	p->agent_timeline_wait_wakeup_count = 0;
	p->agent_timeline_wait_timeout_count = 0;
	p->agent_timeline_wait_deadline_valid = 0;
	p->agent_timeline_wait_deadline = 0;
	memset(&p->agent_timeline_wait_filter, 0,
	       sizeof(p->agent_timeline_wait_filter));
}

void
agent_observe_proc_reset(struct proc *p)
{
	if (p == 0)
		return;
	agent_timeline_waiting_set(p, 0);
	p->agent_provenance_edges = 0;
	p->agent_observe_epoch = 0;
	p->agent_timeline_wait_count = 0;
	p->agent_timeline_wait_sleep_count = 0;
	p->agent_timeline_wait_wakeup_count = 0;
	p->agent_timeline_wait_timeout_count = 0;
	p->agent_timeline_wait_deadline_valid = 0;
	p->agent_timeline_wait_deadline = 0;
	memset(&p->agent_timeline_wait_filter, 0,
	       sizeof(p->agent_timeline_wait_filter));
}

void
agent_observe_scope_reclaim(uint scope_id)
{
	if (!agent_observe_scope_valid(scope_id))
		return;
	for (int i = 0; i < AGENT_FILE_PREFETCH_SPAN_MAX; i++) {
		if (agent_span_prefetch_scopes[i] != scope_id)
			continue;
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
			if (agent_audit_scopes[i] == scope_id)
				agent_audit_slot_clear(i);
	}
	for (int i = 0; i < NPROC; i++)
		if (agent_audit_scope_states[i].used &&
		    agent_audit_scope_states[i].scope_id == scope_id)
			memset(&agent_audit_scope_states[i], 0,
			       sizeof(agent_audit_scope_states[i]));
}

void
agent_observe_record_context(struct proc *p,
			     struct agent_context_record *record,
			     int authority_effect)
{
	struct agent_timeline_record timeline;

	if (p == 0 || record == 0)
		return;
	agent_timeline_from_context(p, record, &timeline);
	agent_observe_record(agent_identity_proc_scope(p), &timeline,
			     p->agent_current_span_owner);
	agent_audit_context(p, record, authority_effect);
}

void
agent_observe_record_sched(struct proc *p, struct agent_sched_record *record)
{
	struct agent_timeline_record timeline;

	if (p == 0 || record == 0)
		return;
	agent_timeline_from_sched(record, &timeline);
	agent_observe_record(agent_identity_proc_scope(p), &timeline, 0);
	agent_audit_sched(p, record);
}

void
agent_observe_record_event(int kind, struct proc *actor,
			   struct agent_event *event, uint64 span_owner,
			   uint64 audit_principal)
{
	agent_audit_event(kind, actor, event, span_owner, audit_principal);
}

void
agent_observe_record_effect(struct proc *p, int tool_id, int status,
			    char *text, uint64 value0, uint64 value1,
			    uint64 value2, uint64 flags,
			    int authority_effect)
{
	if (p == 0 || !p->is_agent)
		return;
	agent_audit_emit(AGENT_AUDIT_KIND_CONTEXT, agent_observe_ticks(), p,
			 p->pid, p->pid, 0, tool_id, status,
			 p->agent_current_cause_sequence,
			 p->agent_current_span_id,
			 p->agent_current_span_owner, p->agent_control_id,
			 authority_effect, value0, value1, value2, flags,
			 text);
}

void
agent_observe_record_prefetch(struct proc *p,
			      struct agent_file_prefetch_hint *hint,
			      uint64 span_owner, char *target_stage,
			      int publish_audit)
{
	struct agent_timeline_record timeline;

	if (p == 0 || hint == 0 || target_stage == 0)
		return;
	agent_file_prefetch_bus_store(
		hint, agent_identity_proc_scope(p), span_owner);
	if (publish_audit)
		agent_audit_emit(
			AGENT_AUDIT_KIND_PREFETCH, hint->tick, p,
			hint->source_pid, hint->target_pid, AGENT_EVENT_NONE,
			AGENT_TOOL_QUERY_FILE, AGENT_STATUS_OK,
			hint->source_sequence, hint->span_id, span_owner,
			p->agent_control_id, 0, hint->source_sequence,
			hint->source_fid, hint->fid, hint->reason, target_stage);
	agent_timeline_from_prefetch(p, hint, &timeline);
	agent_observe_record(agent_identity_proc_scope(p), &timeline,
			     span_owner);
}

void
agent_observe_record_prefetch_handoff_locked(
	int source_pid, uint64 source_control_id, struct proc *target,
	struct agent_file_prefetch_hint *hint, uint64 span_owner,
	char *target_stage, uint64 reason)
{
	struct agent_timeline_record timeline;
	uint scope_id;

	if (target == 0 || hint == 0 || target_stage == 0)
		return;
	scope_id = agent_identity_proc_scope(target);
	agent_observe_prefetch_bus_store_commit(hint, scope_id, span_owner);
	agent_audit_prefetch_handoff(source_pid, source_control_id, target,
				     hint, span_owner, target_stage, reason);
	agent_timeline_from_prefetch(target, hint, &timeline);
	agent_observe_record(scope_id, &timeline, span_owner);
}

int
agent_observe_prefetch_span_snapshot(struct proc *p, uint64 hintsaddr, int max)
{
	struct agent_file_prefetch_hint hint;
	uint64 span_id;
	uint64 span_owner;
	uint64 hint_span_owner;
	uint64 sequence = 0;
	int matched = 0;
	int copied = 0;

	if (p == 0)
		return -1;
	span_id = p->agent_current_span_id;
	span_owner = p->agent_current_span_owner;
	if (span_id == 0 || span_owner == 0)
		return 0;
	while (agent_span_prefetch_scope_next(agent_identity_proc_scope(p),
					      sequence, &hint,
					      &hint_span_owner)) {
		sequence = hint.sequence;
		if (hint.span_id != span_id || hint_span_owner != span_owner)
			continue;
		matched++;
		if (max == 0 || copied >= max)
			continue;
		if (hintsaddr == 0)
			return AGENT_STATUS_BAD_PARAM;
		if (copyout(p->pagetable,
			    hintsaddr +
				    copied *
					    sizeof(struct agent_file_prefetch_hint),
			    (char *)&hint, sizeof(hint)) < 0)
			return -1;
		copied++;
	}
	return max == 0 ? matched : copied;
}

static void
agent_observe_trace_from_context(struct proc *p,
				 struct agent_context_record *record,
				 struct agent_trace_record *trace)
{
	struct thread *t = curr_thread();

	memset(trace, 0, sizeof(*trace));
	trace->kind = AGENT_TRACE_KIND_CONTEXT;
	trace->tick = record->tick;
	trace->sequence = record->sequence;
	trace->cause_sequence = record->cause_sequence;
	trace->span_id = record->span_id;
	trace->value0 = record->value0;
	trace->value1 = record->value1;
	trace->value2 = record->value2;
	trace->flags = record->flags;
	trace->tool_id = record->tool_id;
	trace->status = record->status;
	trace->role = p->agent_role;
	trace->loop_state = p->loop_state;
	trace->pid = p->pid;
	trace->tid = t ? t->tid : 0;
	safestrcpy(trace->text,
		   record->result[0] ? record->result : record->payload,
		   sizeof(trace->text));
}

static void
agent_observe_trace_from_sched(struct agent_sched_record *record,
			       struct agent_trace_record *trace)
{
	memset(trace, 0, sizeof(*trace));
	trace->kind = AGENT_TRACE_KIND_SCHED;
	trace->tick = record->tick;
	trace->sequence = record->dispatch_count;
	trace->value0 = record->score;
	trace->value1 = record->event_queue_count;
	trace->value2 = record->vruntime;
	trace->flags = record->reason_flags;
	trace->role = record->role;
	trace->loop_state = record->loop_state;
	trace->pid = record->pid;
	trace->tid = record->tid;
	safestrcpy(trace->text, "sched", sizeof(trace->text));
}

int
sys_agent_trace_snapshot(uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_context_record context_record;
	struct agent_sched_record sched_record;
	struct agent_trace_record trace;
	uint64 context_visible;
	uint64 sched_visible;
	uint64 sched_start;
	uint64 ci = 0;
	uint64 si = 0;
	uint64 total;
	uint64 seq;
	uint64 slot;
	int limit;
	int copied = 0;
	int have_context;
	int have_sched;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	context_visible = p->context_path_count;
	if (context_visible > p->context_path_capacity)
		context_visible = p->context_path_capacity;
	sched_visible = p->agent_sched_trace_count;
	if (sched_visible > AGENT_SCHED_TRACE_CAP)
		sched_visible = AGENT_SCHED_TRACE_CAP;
	total = context_visible + sched_visible;
	if (total > AGENT_TRACE_MAX_RECORDS)
		total = AGENT_TRACE_MAX_RECORDS;
	if (max == 0)
		return total;
	if (recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	limit = max < (int)total ? max : (int)total;
	sched_start = p->agent_sched_trace_count > AGENT_SCHED_TRACE_CAP ?
			      p->agent_sched_trace_head :
			      0;
	while (copied < limit &&
	       (ci < context_visible || si < sched_visible)) {
		have_context = 0;
		have_sched = 0;
		if (ci < context_visible && p->context_path_capacity > 0) {
			seq = p->context_path_oldest + ci;
			slot = (seq - 1) % p->context_path_capacity;
			if (agent_context_read_record(p, slot,
						      &context_record) < 0)
				return AGENT_STATUS_NO_SPACE;
			if (context_record.sequence == seq)
				have_context = 1;
			else {
				ci++;
				continue;
			}
		}
		if (si < sched_visible) {
			slot = (sched_start + si) % AGENT_SCHED_TRACE_CAP;
			memmove(&sched_record, &p->agent_sched_records[slot],
				sizeof(sched_record));
			have_sched = 1;
		}
		if (have_context &&
		    (!have_sched || context_record.tick <= sched_record.tick)) {
			agent_observe_trace_from_context(
				p, &context_record, &trace);
			ci++;
		} else if (have_sched) {
			agent_observe_trace_from_sched(&sched_record, &trace);
			si++;
		} else {
			break;
		}
		if (copyout(p->pagetable,
			    recordsaddr +
				    copied * sizeof(struct agent_trace_record),
			    (char *)&trace, sizeof(trace)) < 0)
			return -1;
		copied++;
	}
	return copied;
}

int
sys_agent_sched_snapshot(uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_sched_record record;
	uint64 visible;
	uint64 start;
	uint64 slot;
	int n;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	visible = p->agent_sched_trace_count;
	if (visible > AGENT_SCHED_TRACE_CAP)
		visible = AGENT_SCHED_TRACE_CAP;
	if (max == 0)
		return visible;
	if (recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	n = max < (int)visible ? max : (int)visible;
	start = p->agent_sched_trace_count > AGENT_SCHED_TRACE_CAP ?
			p->agent_sched_trace_head :
			0;
	for (int i = 0; i < n; i++) {
		slot = (start + i) % AGENT_SCHED_TRACE_CAP;
		memmove(&record, &p->agent_sched_records[slot],
			sizeof(record));
		if (copyout(p->pagetable,
			    recordsaddr +
				    i * sizeof(struct agent_sched_record),
			    (char *)&record, sizeof(record)) < 0)
			return -1;
	}
	return n;
}

int
sys_agent_sched_config(uint64 configaddr)
{
	struct proc *p = curr_proc();
	struct proc *target;
	struct agent_sched_config config;
	uint64 valid_mask = AGENT_SCHED_CONFIG_POLICY |
			    AGENT_SCHED_CONFIG_WEIGHT |
			    AGENT_SCHED_CONFIG_PRIORITY |
			    AGENT_SCHED_CONFIG_BUDGET;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (configaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	if (copyin(p->pagetable, (char *)&config, configaddr,
		   sizeof(config)) < 0)
		return -1;
	if (config.target_pid <= 0 || config.update_mask == 0 ||
	    (config.update_mask & ~valid_mask) != 0)
		return AGENT_STATUS_BAD_PARAM;
	if ((config.update_mask & AGENT_SCHED_CONFIG_POLICY) &&
	    config.policy != AGENT_SCHED_POLICY_ADAPTIVE)
		return AGENT_STATUS_BAD_PARAM;
	if ((config.update_mask & AGENT_SCHED_CONFIG_WEIGHT) &&
	    (config.weight < AGENT_SCHED_WEIGHT_MIN ||
	     config.weight > AGENT_SCHED_WEIGHT_MAX))
		return AGENT_STATUS_BAD_PARAM;
	if ((config.update_mask & AGENT_SCHED_CONFIG_PRIORITY) &&
	    (config.priority < AGENT_SCHED_PRIORITY_MIN ||
	     config.priority > AGENT_SCHED_PRIORITY_MAX))
		return AGENT_STATUS_BAD_PARAM;
	if ((config.update_mask & AGENT_SCHED_CONFIG_BUDGET) &&
	    (config.budget < AGENT_SCHED_BUDGET_MIN ||
	     config.budget > AGENT_SCHED_BUDGET_MAX))
		return AGENT_STATUS_BAD_PARAM;
	for (target = pool; target < &pool[NPROC]; target++) {
		if (target->state == P_UNUSED ||
		    target->pid != config.target_pid)
			continue;
		if (!target->is_agent ||
		    agent_identity_proc_scope(target) !=
			    agent_identity_proc_scope(p))
			return AGENT_STATUS_NOT_FOUND;
		if (config.update_mask & AGENT_SCHED_CONFIG_POLICY)
			target->agent_sched_policy = config.policy;
		if (config.update_mask & AGENT_SCHED_CONFIG_WEIGHT)
			target->agent_sched_weight = config.weight;
		if (config.update_mask & AGENT_SCHED_CONFIG_PRIORITY)
			target->agent_sched_priority = config.priority;
		if (config.update_mask & AGENT_SCHED_CONFIG_BUDGET) {
			target->agent_sched_budget = config.budget;
			if (target->agent_sched_budget_used >= config.budget)
				target->agent_sched_budget_used = config.budget;
		}
		target->agent_sched_ready_tick = agent_observe_ticks();
		return 0;
	}
	return AGENT_STATUS_NOT_FOUND;
}
