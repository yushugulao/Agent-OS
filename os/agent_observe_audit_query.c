#include "agent_context.h"
#include "agent_internal.h"
#include "defs.h"
#include "kernel_work.h"
#include "timer.h"
#include "trap.h"
#include "vfs_security.h"
#include "agent_observe_internal.h"

/* 一次有界等待足以覆盖完整的双组元数据检查点。 */
#define AGENT_AUDIT_RECEIPT_WAIT_ATTEMPTS 128U

/* 审计账本查询读取写入端发布的不可变索引副本。 */
static uint agent_audit_scope_visible(uint scope_id)
{
	return agent_observe_audit_scope_visible_locked(scope_id);
}

static uint64
agent_audit_receipt_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

int
sys_agent_audit_receipt(uint64 requestaddr)
{
	struct proc *p = curr_proc();
	struct agent_audit_receipt_request request;
	struct workflow_lifecycle_key requested;
	struct workflow_lifecycle_key current;
	uint64 deadline = 0;
	uint64 receipt_id = 0;
	uint64 supplied_receipt;
	uint scope_id;
	uint durability = AGENT_AUDIT_DURABILITY_NOT_FOUND;
	uint attempts = 0;
	int copy_status;
	int status = AGENT_STATUS_OK;

	if (p == 0 ||
	    user_range_check(p->pagetable, requestaddr, sizeof(request), PTE_W) < 0 ||
	    copyin(p->pagetable, (char *)&request, requestaddr,
		   sizeof(request)) < 0)
		return -1;
	if (request.version != AGENT_AUDIT_RECEIPT_VERSION)
		return AGENT_STATUS_BAD_VERSION;
	if (request.size != sizeof(request))
		return AGENT_STATUS_BAD_SIZE;
	if (request.operation != AGENT_AUDIT_RECEIPT_STATUS &&
	    request.operation != AGENT_AUDIT_RECEIPT_WAIT
#ifdef AGENT_OBSERVE_TEST_PROFILE
	    && request.operation !=
		    AGENT_AUDIT_RECEIPT_TEST_EVICT_BEFORE_PERSIST
#endif
	    )
		return AGENT_STATUS_BAD_PARAM;
	if (request.flags != AGENT_AUDIT_RECEIPT_F_NONE ||
	    request.lifecycle.reserved != 0 || request.sequence == 0 ||
	    request.record_hash == 0 || request.timeout_ticks < 0 ||
	    request.timeout_ticks > AGENT_AUDIT_RECEIPT_WAIT_MAX_TICKS ||
	    request.durability != AGENT_AUDIT_DURABILITY_NOT_FOUND ||
	    request.status != 0 || request.reserved != 0 ||
	    (request.operation == AGENT_AUDIT_RECEIPT_STATUS &&
	     request.timeout_ticks != 0) ||
	    (request.operation == AGENT_AUDIT_RECEIPT_WAIT &&
	     request.receipt_id == 0)
#ifdef AGENT_OBSERVE_TEST_PROFILE
	    || (request.operation ==
			AGENT_AUDIT_RECEIPT_TEST_EVICT_BEFORE_PERSIST &&
		(request.timeout_ticks != 0 || request.receipt_id == 0))
#endif
	    )
		return AGENT_STATUS_BAD_PARAM;
	if (!p->is_agent)
		return AGENT_STATUS_NOT_AGENT;
	if (!agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	requested.id = request.lifecycle.id;
	requested.generation = request.lifecycle.generation;
	current = vfs_proc_lifecycle(p);
	if (!workflow_lifecycle_key_valid(requested) ||
	    !vfs_proc_lifecycle_active(p) ||
	    !workflow_lifecycle_key_equal(requested, current))
		return AGENT_STATUS_STALE;
	/* 查询过程不得生成观测记录，以免逐出自身证据。 */
	if (agent_observe_recording_suppress_begin(p) < 0)
		return AGENT_STATUS_INDETERMINATE;
	scope_id = agent_identity_proc_scope(p);
	supplied_receipt = request.receipt_id;
#ifdef AGENT_OBSERVE_TEST_PROFILE
	if (request.operation ==
	    AGENT_AUDIT_RECEIPT_TEST_EVICT_BEFORE_PERSIST) {
		if (agent_observe_query_reserve(
			    2U * AGENT_OBSERVE_AUDIT_SCOPE_LIMIT) < 0)
			status = AGENT_STATUS_CANCELLED;
		else
			status = agent_observe_receipt_status(
				scope_id, requested, request.sequence,
				request.record_hash, supplied_receipt,
				&receipt_id, &durability);
		if (status != AGENT_STATUS_OK ||
		    durability != AGENT_AUDIT_DURABILITY_PENDING ||
		    agent_observe_test_evict_checkpoint_window(p) < 0) {
			if (status == AGENT_STATUS_OK)
				status = AGENT_STATUS_INDETERMINATE;
			goto complete;
		}
	}
#endif
	if (request.operation == AGENT_AUDIT_RECEIPT_WAIT &&
	    request.timeout_ticks > 0) {
		uint64 now = agent_audit_receipt_ticks();
		deadline = now > ~0ULL - (uint64)request.timeout_ticks ?
			   ~0ULL : now + (uint64)request.timeout_ticks;
	}
	for (;;) {
		if (agent_observe_query_reserve(
			    2U * AGENT_OBSERVE_AUDIT_SCOPE_LIMIT) < 0) {
			status = AGENT_STATUS_CANCELLED;
			break;
		}
		status = agent_observe_receipt_status(
			scope_id, requested, request.sequence,
			request.record_hash, supplied_receipt,
			&receipt_id, &durability);
		request.receipt_id = receipt_id;
		request.durability = durability;
		if (status != AGENT_STATUS_OK ||
		    request.durability != AGENT_AUDIT_DURABILITY_PENDING ||
		    request.operation != AGENT_AUDIT_RECEIPT_WAIT ||
		    request.timeout_ticks == 0)
			break;
		if (proc_thread_exit_requested()) {
			status = AGENT_STATUS_CANCELLED;
			break;
		}
		if (agent_audit_receipt_ticks() >= deadline) {
			status = AGENT_STATUS_TIMEOUT;
			break;
		}
		if (attempts++ >= AGENT_AUDIT_RECEIPT_WAIT_ATTEMPTS) {
			status = AGENT_STATUS_RETRY;
			break;
		}
		(void)agent_observe_receipt_persist(scope_id);
		if (kernel_work_checkpoint(KERNEL_WORK_OPERATION_UNITS) < 0) {
			status = AGENT_STATUS_CANCELLED;
			break;
		}
	}
#ifdef AGENT_OBSERVE_TEST_PROFILE
complete:
#endif
	request.status = status;
	copy_status = copyout(p->pagetable, requestaddr, (char *)&request,
			      sizeof(request));
	agent_observe_recording_suppress_end(p);
	if (copy_status < 0)
		return -1;
	return status;
}

int sys_agent_audit_snapshot(uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_observe_audit_view view;
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
	agent_observe_audit_view_open_locked(scope_id, &view);
	visible = view.visible_records;
	limit = max < (int)visible ? max : (int)visible;
	for (int i = 0; i < limit; i++) {
		if (!agent_observe_audit_view_record_locked(
			    &view, i, 0, &record, 0))
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
	struct agent_observe_audit_view view;
	struct agent_ledger_summary summary;
	struct agent_audit_record oldest_record;
	struct agent_audit_record latest_record;
	uint64 visible;
	uint scope_id;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (summaryaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	scope_id = agent_identity_proc_scope(p);
	agent_observe_audit_view_open_locked(scope_id, &view);
	memset(&summary, 0, sizeof(summary));
	visible = view.visible_records;
	summary.version = AGENT_LEDGER_VERSION;
	summary.visible_records = visible;
	summary.total_records = view.total_records;
	summary.other_records = view.admission_drops +
		view.kind_counts[AGENT_AUDIT_KIND_PREFETCH];
	summary.dropped_records =
		summary.total_records > visible ?
			summary.total_records - visible :
			0;
	if (visible > 0 &&
	    agent_observe_audit_view_record_locked(
		    &view, 0, 0, &oldest_record, 0) &&
	    agent_observe_audit_view_record_locked(
		    &view, visible - 1, 0, &latest_record, 0)) {
		summary.oldest_sequence = oldest_record.sequence;
		summary.latest_sequence = latest_record.sequence;
	}
	summary.ledger_hash = view.ledger_hash;
	summary.context_records =
		view.kind_counts[AGENT_AUDIT_KIND_CONTEXT];
	summary.event_records =
		view.kind_counts[AGENT_AUDIT_KIND_EVENT_ENQUEUE] +
		view.kind_counts[AGENT_AUDIT_KIND_EVENT_CONSUME];
	summary.sched_records = view.kind_counts[AGENT_AUDIT_KIND_SCHED];
	summary.timeline_total = summary.total_records;
	summary.observe_epoch = view.observe_epoch;
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
	struct agent_observe_audit_view view;
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
	agent_observe_audit_view_open_locked(scope_id, &view);
	visible = view.visible_records;
	for (uint i = 0; i < visible; i++) {
		if (!agent_observe_audit_view_record_locked(
			    &view, i, 0, &record, 0))
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
	struct agent_observe_audit_view view;
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
	agent_observe_audit_view_open_locked(scope_id, &view);
	visible = view.visible_records;
	for (uint i = 0; i < visible; i++) {
		if (!agent_observe_audit_view_record_locked(
			    &view, i, 0, &record, &record_span_owner))
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
