#include "agent_context.h"
#include "agent_internal.h"
#include "agent_observe_recovery.h"
#include "defs.h"
#include "kernel_work.h"
#include "timer.h"
#include "trap.h"
#include "vfs_security.h"
#include "agent_observe_internal.h"

extern struct proc pool[NPROC];

static uint64
agent_observe_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
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
					  int cause_pid, uint64 cause_control,
					  uint64 cause_branch, int actor_tid,
					  int actor_role, int actor_loop_state,
					  struct agent_provenance_edge *edge)
{
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
	edge->workflow_lifecycle_id = p->workflow_lifecycle_id;
	edge->workflow_lifecycle_generation =
		p->workflow_lifecycle_generation;
	edge->source_branch_generation = cause_branch;
	edge->target_branch_generation = record->branch_generation;
	edge->source_control_id = cause_control;
	edge->target_control_id = p->agent_control_id;
	edge->source_record_hash = cause_control == p->agent_control_id ?
					  record->prev_hash : 0;
	edge->target_record_hash = record->record_hash;
	edge->flags = record->flags;
	edge->value0 = record->value0;
	edge->value1 = record->value1;
	edge->value2 = actor_tid;
	edge->role = actor_role;
	edge->loop_state = actor_loop_state;
	edge->tid = actor_tid;
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
	edge->workflow_lifecycle_id = record->workflow_lifecycle_id;
	edge->workflow_lifecycle_generation =
		record->workflow_lifecycle_generation;
	edge->source_branch_generation = record->cause_branch_generation;
	edge->target_branch_generation = record->branch_generation;
	edge->source_control_id = record->cause_control_id;
	edge->target_control_id = record->actor_control_id;
	edge->source_record_hash = record->cause_record_hash;
	edge->target_record_hash = record->record_hash;
	edge->flags = record->flags;
	edge->value0 = record->value0;
	edge->value1 = record->value1;
	edge->value2 = record->value2;
	edge->role = record->role;
	edge->loop_state = record->loop_state;
	edge->tid = record->tid;
	edge->tool_id = record->tool_id;
	edge->event_type = record->event_type;
	edge->status = record->status;
	safestrcpy(edge->text, record->text, sizeof(edge->text));
}

int sys_agent_provenance_snapshot(uint64 edgesaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_observe_audit_view audit_view;
	struct agent_context_record context_record;
	struct agent_audit_record audit_record;
	struct agent_provenance_edge edge;
	uint64 context_visible;
	uint64 audit_visible;
	uint64 reserved = 0;
	uint64 scan_visible;
	uint64 seq;
	uint64 slot;
	uint64 span_id;
	uint64 span_owner;
	uint64 audit_span_owner;
	uint64 context_span_owner;
	uint64 context_cause_control;
	uint64 context_cause_branch;
	int audit_global;
	int audit_allowed;
	int context_cause_pid;
	int context_actor_tid;
	int context_actor_role;
	int context_actor_loop_state;
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
		audit_visible = audit_allowed ?
					agent_observe_audit_scope_visible_locked(
						agent_identity_proc_scope(p)) :
					0;
		scan_visible = context_visible + audit_visible;
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
			    &context_cause_pid, &context_cause_control,
			    &context_cause_branch) < 0 ||
		    agent_context_load_actor(p, slot, &context_actor_tid,
					     &context_actor_role,
					     &context_actor_loop_state) < 0)
			return AGENT_STATUS_NO_SPACE;
		if (context_cause_pid <= 0 || context_cause_control == 0)
			continue;
		agent_provenance_from_context(
			p, &context_record, context_cause_pid,
			context_cause_control, context_cause_branch,
			context_actor_tid, context_actor_role,
			context_actor_loop_state, &edge);
		if (agent_provenance_emit(p, edgesaddr, max, &matched,
					  &copied, &edge) < 0)
			return -1;
		if (max > 0 && copied >= max)
			goto provenance_done;
	}

	memset(&audit_view, 0, sizeof(audit_view));
	if (audit_allowed)
		agent_observe_audit_view_open_locked(
			agent_identity_proc_scope(p), &audit_view);
	audit_visible = audit_view.visible_records;
	for (uint i = 0; i < audit_visible; i++) {
		if (!agent_observe_audit_view_record_locked(
			    &audit_view, i, 0, &audit_record,
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

provenance_done:
	if (max == 0)
		return matched;
	return copied;
}

static void agent_timeline_from_context(struct proc *p,
					struct agent_context_record *record,
					struct agent_timeline_record *timeline)
{
	uint64 span_owner, cause_control, cause_branch;
	uint64 slot = (record->sequence - 1) % p->context_path_capacity;
	int cause_pid, actor_tid, actor_role, actor_loop_state;

	memset(timeline, 0, sizeof(*timeline));
	if (agent_context_load_attribution(p, slot, &span_owner, &cause_pid,
					   &cause_control, &cause_branch) < 0 ||
	    agent_context_load_actor(p, slot, &actor_tid, &actor_role,
				     &actor_loop_state) < 0)
		return;
	timeline->source = AGENT_TIMELINE_SOURCE_CONTEXT;
	timeline->kind = AGENT_TRACE_KIND_CONTEXT;
	timeline->tick = record->tick;
	timeline->sequence = record->sequence;
	timeline->cause_sequence = record->cause_sequence;
	timeline->span_id = record->span_id;
	timeline->workflow_lifecycle_id = p->workflow_lifecycle_id;
	timeline->workflow_lifecycle_generation =
		p->workflow_lifecycle_generation;
	timeline->branch_generation = record->branch_generation;
	timeline->cause_branch_generation = cause_branch;
	timeline->actor_control_id = p->agent_control_id;
	timeline->cause_control_id = cause_control;
	timeline->cause_record_hash = cause_control == p->agent_control_id ?
					      record->prev_hash : 0;
	timeline->value0 = record->value0;
	timeline->value1 = record->value1;
	timeline->value2 = record->value2;
	timeline->flags = record->flags;
	timeline->pid = p->pid;
	timeline->source_pid = p->pid;
	timeline->target_pid = p->pid;
	timeline->role = actor_role;
	timeline->loop_state = actor_loop_state;
	timeline->tool_id = record->tool_id;
	timeline->status = record->status;
	timeline->tid = actor_tid;
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
	memmove(&record, &p->agent_ipc_observe_cold->sched_records[slot],
		sizeof(record));
	agent_timeline_from_sched(&record, timeline);
	return 1;
}

static int agent_timeline_load_audit(
				     struct agent_observe_audit_view *view,
				     uint64 *cursor, int global,
				     uint64 span_id, uint64 span_owner,
				     struct agent_timeline_record *timeline)
{
	struct agent_audit_record record;
	uint64 record_span_owner;

	while (view != 0 && *cursor < view->visible_records) {
		uint index = (*cursor)++;

		if (!agent_observe_audit_view_record_locked(
			    view, index, 1, &record, &record_span_owner))
			return 0;
		if (!global &&
		    (record.span_id != span_id ||
		     record_span_owner != span_owner))
			continue;
		agent_observe_timeline_from_audit(&record, timeline);
		return 1;
	}
	return 0;
}

void
agent_observe_timeline_record_context(
	struct proc *p, struct agent_context_record *record)
{
	struct agent_timeline_record timeline;

	if (p == 0 || record == 0)
		return;
	agent_timeline_from_context(p, record, &timeline);
	agent_observe_timeline_publish_locked(
		agent_identity_proc_scope(p), &timeline,
		p->agent_current_span_owner);
}

void
agent_observe_timeline_record_sched(
	struct proc *p, struct agent_sched_record *record)
{
	struct agent_timeline_record timeline;
	uint64 slot;

	if (p == 0 || record == 0)
		return;
	slot = p->agent_sched_trace_head % AGENT_SCHED_TRACE_CAP;
	memmove(&p->agent_ipc_observe_cold->sched_records[slot], record,
		sizeof(*record));
	p->agent_sched_trace_head =
		(p->agent_sched_trace_head + 1) % AGENT_SCHED_TRACE_CAP;
	p->agent_sched_trace_count++;
	agent_timeline_from_sched(record, &timeline);
	agent_observe_timeline_publish_locked(
		agent_identity_proc_scope(p), &timeline, 0);
}

static int agent_timeline_export(struct proc *p,
				 struct agent_timeline_filter *filter,
				 uint64 recordsaddr, int max,
				 uint64 *scan_epoch_out)
{
	struct agent_observe_audit_view audit_view;
	struct agent_timeline_record context_timeline;
	struct agent_timeline_record sched_timeline;
	struct agent_timeline_record audit_timeline;
	struct agent_timeline_record *selected;
	uint64 context_visible;
	uint64 sched_visible;
	uint64 audit_scan_visible;
	uint64 reserved = 0;
	uint64 scan_visible;
	uint64 context_oldest;
	uint64 sched_start;
	uint64 ci = 0;
	uint64 si = 0;
	uint64 ai = 0;
	uint64 best_tick;
	uint64 candidate_epoch;
	uint64 span_id;
	uint64 span_owner;
	int audit_global;
	int audit_allowed;
	int have_context;
	int have_sched;
	int have_audit;
	int copied = 0;
	int matched = 0;
	int total;
	int pick;

	context_visible = agent_observe_timeline_source_enabled(
				  filter, AGENT_TIMELINE_SOURCE_CONTEXT) ?
				  p->context_path_count :
				  0;
	if (context_visible > p->context_path_capacity)
		context_visible = p->context_path_capacity;
	sched_visible = agent_observe_timeline_source_enabled(
				filter, AGENT_TIMELINE_SOURCE_SCHED) ?
				p->agent_sched_trace_count :
				0;
	if (sched_visible > AGENT_SCHED_TRACE_CAP)
		sched_visible = AGENT_SCHED_TRACE_CAP;
	audit_global = agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE);
	audit_allowed = agent_observe_timeline_source_enabled(
				filter, AGENT_TIMELINE_SOURCE_AUDIT) &&
				(audit_global ||
				 agent_identity_has_cap(p, AGENT_CAP_AUDIT_WRITE));
	audit_scan_visible = audit_allowed ?
		agent_observe_audit_scope_visible_locked(
			agent_identity_proc_scope(p)) : 0;
	total = (int)(context_visible + sched_visible + audit_scan_visible);
	if (scan_epoch_out == 0 && max == 0 &&
	    (filter == 0 || filter->flags == 0) &&
	    (!audit_allowed || audit_global))
		return total;

	for (;;) {
		candidate_epoch = scan_epoch_out != 0 ?
			agent_observe_scope_epoch(agent_identity_proc_scope(p)) : 0;
		if (scan_epoch_out != 0 && candidate_epoch == 0)
			return AGENT_STATUS_NO_SPACE;
		context_visible = agent_observe_timeline_source_enabled(
					  filter,
					  AGENT_TIMELINE_SOURCE_CONTEXT) ?
					  p->context_path_count :
					  0;
		if (context_visible > p->context_path_capacity)
			context_visible = p->context_path_capacity;
		sched_visible = agent_observe_timeline_source_enabled(
					filter, AGENT_TIMELINE_SOURCE_SCHED) ?
					p->agent_sched_trace_count :
					0;
		if (sched_visible > AGENT_SCHED_TRACE_CAP)
			sched_visible = AGENT_SCHED_TRACE_CAP;
		audit_scan_visible = audit_allowed ?
			agent_observe_audit_scope_visible_locked(
				agent_identity_proc_scope(p)) : 0;
		scan_visible = context_visible + sched_visible +
			       audit_scan_visible;
		if (scan_visible <= reserved) {
			if (scan_epoch_out != 0)
				*scan_epoch_out = candidate_epoch;
			break;
		}
		if (agent_observe_query_reserve_to(scan_visible, &reserved) < 0)
			return -1;
	}

	span_id = p->agent_current_span_id;
	span_owner = p->agent_current_span_owner;
	memset(&audit_view, 0, sizeof(audit_view));
	if (audit_allowed)
		agent_observe_audit_view_open_locked(
			agent_identity_proc_scope(p), &audit_view);
	context_oldest = p->context_path_oldest;
	sched_start = p->agent_sched_trace_count > AGENT_SCHED_TRACE_CAP ?
			      p->agent_sched_trace_head :
			      0;
	have_context = agent_timeline_load_context(
		p, &ci, context_visible, context_oldest, &context_timeline);
	if (have_context < 0)
		return AGENT_STATUS_NO_SPACE;
	have_sched = agent_timeline_load_sched(p, &si, sched_visible,
					       sched_start,
					       &sched_timeline);
	have_audit = audit_allowed ?
			     agent_timeline_load_audit(&audit_view, &ai,
						       audit_global,
						       span_id, span_owner,
						       &audit_timeline) :
			     0;
	while ((max == 0 || copied < max) &&
	       (have_context || have_sched || have_audit)) {
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
		if (selected == 0)
			break;
		if (agent_observe_timeline_match(filter, selected)) {
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
				&audit_view, &ai, audit_global, span_id,
				span_owner,
				&audit_timeline);
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
	return agent_timeline_export(p, 0, recordsaddr, max, 0);
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
	return agent_timeline_export(p, &filter, recordsaddr, max, 0);
}
#define AGENT_TIMELINE_WAIT_RETRY 1
#ifdef AGENT_OBSERVE_TEST_PROFILE
static void agent_timeline_test_point(uint operation, uint scope_id,
				      uint64 scan_epoch, uint64 current_epoch)
{
	struct agent_observe_recovery_request request = {0};
	uint64 bank_generation = 0;
	uint returned = 0;
	int status = AGENT_STATUS_OK;
	request.operation = operation;
	request.evidence.id = scope_id;
	request.after_sequence = scan_epoch;
	request.bank_generation = current_epoch;
	(void)agent_observe_test_execute(&request, 0, &bank_generation,
					 &returned, &status);
}
#endif
static void agent_timeline_wait_finish(
	struct proc *p, struct thread *t, struct agent_timeline_wait_state *state)
{
	(void)p;
	agent_observe_timeline_waiter_unpublish(t, state);
	memset(state, 0, sizeof(*state));
}
static int agent_timeline_wait_enqueue_atomic(
	struct proc *p, struct thread *t, struct agent_timeline_wait_state *state,
	struct agent_timeline_filter *filter, uint scope_id,
	uint64 scan_epoch, uint64 start, uint64 now, int timeout_ticks,
	int *deadline_rescan_used)
{
	uint64 current_epoch;
	int enabled = intr_save();
	int expired = timeout_ticks >= 0 &&
		now - start >= (uint64)timeout_ticks;
	int wait_status;
	/* 仅封闭代次校验到入队发布之间的窗口。 */
	current_epoch = agent_observe_scope_epoch(scope_id);
	if (current_epoch != scan_epoch) {
		if (expired && *deadline_rescan_used)
			goto timeline_timeout;
		if (expired)
			*deadline_rescan_used = 1;
#ifdef AGENT_OBSERVE_TEST_PROFILE
		agent_timeline_test_point(AGENT_OBSERVE_TEST_TIMELINE_RECHECK,
					  scope_id, scan_epoch, current_epoch);
#endif
		intr_restore(enabled);
		return AGENT_TIMELINE_WAIT_RETRY;
	}
	if (expired)
		goto timeline_timeout;
	memset(state, 0, sizeof(*state));
	memmove(&state->filter, filter, sizeof(state->filter));
	state->thread_generation = t->identity_generation;
	state->observe_epoch = scan_epoch;
	state->scope_id = scope_id;
	if (timeout_ticks >= 0) {
		state->deadline_valid = 1;
		state->deadline = start + timeout_ticks;
	}
	if (agent_observe_timeline_waiter_publish(t, state) < 0) {
		intr_restore(enabled);
		return -1;
	}
	p->agent_timeline_wait_sleep_count++;
	wait_status = wait_queue_sleep_key_irq(
		&p->agent_timeline_waiters, state->thread_generation);
	if (state->observe_epoch > p->agent_observe_epoch)
		p->agent_observe_epoch = state->observe_epoch;
	agent_observe_timeline_waiter_unpublish(t, state);
	if (wait_status != WAIT_QUEUE_OK)
		agent_timeline_wait_finish(p, t, state);
	else
		memset(state, 0, sizeof(*state));
	intr_restore(enabled);
	return wait_status == WAIT_QUEUE_OK ? 0 : -1;
timeline_timeout:
	agent_timeline_wait_finish(p, t, state);
	p->agent_timeline_wait_timeout_count++;
	intr_restore(enabled);
	return AGENT_STATUS_TIMEOUT;
}
static int agent_timeline_wait_for_match(struct proc *p,
					 struct agent_timeline_filter *filter,
					 int timeout_ticks)
{
	uint scope_id = agent_identity_proc_scope(p);
	struct agent_timeline_wait_state state = {0};
	struct thread *t = curr_thread();
	uint64 start;
	uint64 scan_epoch;
	uint64 now;
	int matched;
	int deadline_rescan_used = 0;
	int wait_status;

	if (t == 0 || t->process != p || t->identity_generation == 0)
		return -1;
	start = agent_observe_ticks();
	p->agent_timeline_wait_count++;
	for (;;) {
		matched = agent_timeline_export(p, filter, 0, 0,
					       &scan_epoch);
		if (matched < 0) {
			agent_timeline_wait_finish(p, t, &state);
			return matched;
		}
		if (matched > 0) {
			agent_timeline_wait_finish(p, t, &state);
			p->agent_observe_epoch = scan_epoch;
			return matched;
		}
		now = agent_observe_ticks();
#ifdef AGENT_OBSERVE_TEST_PROFILE
		agent_timeline_test_point(AGENT_OBSERVE_TEST_TIMELINE_WINDOW,
					  scope_id, scan_epoch, 0);
#endif
		wait_status = agent_timeline_wait_enqueue_atomic(
			p, t, &state, filter, scope_id, scan_epoch, start, now,
			timeout_ticks, &deadline_rescan_used);
		if (wait_status == AGENT_TIMELINE_WAIT_RETRY)
			continue;
		if (wait_status < 0)
			return wait_status;
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
	return agent_timeline_export(p, &filter, recordsaddr, max, 0);
}


void
agent_observe_proc_init(struct proc *p, int sched_weight, uint64 now)
{
	if (p == 0)
		return;
	p->agent_sched_policy = AGENT_SCHED_POLICY_ADAPTIVE;
	p->agent_sched_weight = sched_weight;
	p->agent_sched_priority = 0;
	p->agent_sched_ready_tick = now;
	p->agent_sched_last_dispatch_tick = 0;
	p->agent_sched_dispatch_count = 0;
	p->agent_sched_event_dispatch_count = 0;
	p->agent_sched_deadline_dispatch_count = 0;
	p->agent_sched_vruntime = 0;
	p->agent_sched_preemptions = 0;
	p->agent_sched_budget = AGENT_SCHED_DEFAULT_BUDGET;
	p->agent_sched_budget_used = 0;
	p->agent_sched_last_score = 0;
	p->agent_sched_last_reason = 0;
	p->agent_sched_trace_count = 0;
	p->agent_sched_trace_head = 0;
	if (p->agent_ipc_observe_cold != 0)
		memset(p->agent_ipc_observe_cold->sched_records, 0,
		       sizeof(p->agent_ipc_observe_cold->sched_records));
	for (int tid = 0; tid < NTHREAD; tid++)
		agent_observe_thread_reset(&p->threads[tid]);
	p->agent_observe_epoch =
		agent_observe_scope_epoch(agent_identity_proc_scope(p));
	p->agent_timeline_wait_count = 0;
	p->agent_timeline_wait_sleep_count = 0;
	p->agent_timeline_wait_wakeup_count = 0;
	p->agent_timeline_wait_timeout_count = 0;
}

void
agent_observe_proc_reset(struct proc *p)
{
	if (p == 0)
		return;
	agent_observe_recovery_unbind_proc(p);
	p->agent_sched_policy = 0;
	p->agent_sched_weight = 0;
	p->agent_sched_priority = 0;
	p->agent_sched_ready_tick = 0;
	p->agent_sched_last_dispatch_tick = 0;
	p->agent_sched_dispatch_count = 0;
	p->agent_sched_event_dispatch_count = 0;
	p->agent_sched_deadline_dispatch_count = 0;
	p->agent_sched_vruntime = 0;
	p->agent_sched_preemptions = 0;
	p->agent_sched_budget = 0;
	p->agent_sched_budget_used = 0;
	p->agent_sched_last_score = 0;
	p->agent_sched_last_reason = 0;
	p->agent_sched_trace_count = 0;
	p->agent_sched_trace_head = 0;
	if (p->agent_ipc_observe_cold != 0)
		memset(p->agent_ipc_observe_cold->sched_records, 0,
		       sizeof(p->agent_ipc_observe_cold->sched_records));
	for (int tid = 0; tid < NTHREAD; tid++)
		agent_observe_thread_reset(&p->threads[tid]);
	p->agent_provenance_edges = 0;
	p->agent_observe_epoch = 0;
	p->agent_timeline_wait_count = 0;
	p->agent_timeline_wait_sleep_count = 0;
	p->agent_timeline_wait_wakeup_count = 0;
	p->agent_timeline_wait_timeout_count = 0;
}

void
agent_observe_tick_proc(struct proc *p, uint64 now)
{
	int enabled = intr_save();

	if (p == 0) {
		intr_restore(enabled);
		return;
	}
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *t = &p->threads[tid];
		struct agent_timeline_wait_state *state =
			t->agent_timeline_wait_state;

		if (state == 0 ||
		    state->thread_generation != t->identity_generation ||
		    !state->deadline_valid || now < state->deadline)
			continue;
		state->deadline_valid = 0;
		(void)agent_observe_timeline_waiter_wake(t);
	}
	intr_restore(enabled);
}

static void
agent_observe_trace_from_context(struct proc *p,
				 struct agent_context_record *record,
				 struct agent_trace_record *trace)
{
	uint64 slot = (record->sequence - 1) % p->context_path_capacity;
	int actor_tid = 0, actor_role = 0, actor_loop_state = 0;

	agent_context_load_actor(p, slot, &actor_tid, &actor_role,
				 &actor_loop_state);

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
	trace->role = actor_role;
	trace->loop_state = actor_loop_state;
	trace->pid = p->pid;
	trace->tid = actor_tid;
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
			memmove(&sched_record,
				&p->agent_ipc_observe_cold->sched_records[slot],
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
		memmove(&record,
			&p->agent_ipc_observe_cold->sched_records[slot],
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
	int enabled;
	int status = AGENT_STATUS_NOT_FOUND;

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
	enabled = intr_save();
	for (target = pool; target < &pool[NPROC]; target++) {
		if (!proc_teardown_live(target) ||
		    target->pid != config.target_pid)
			continue;
		if (!target->is_agent ||
		    agent_identity_proc_scope(target) !=
			    agent_identity_proc_scope(p)) {
			status = AGENT_STATUS_NOT_FOUND;
			break;
		}
		if (!agent_identity_controls_or_self(p, target)) {
			status = AGENT_STATUS_DENIED;
			break;
		}
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
		status = AGENT_STATUS_OK;
		break;
	}
	intr_restore(enabled);
	return status;
}
