#include "agent_context.h"
#include "agent_internal.h"
#include "defs.h"
#include "timer.h"
#include "trap.h"
#include "vfs_security.h"

extern struct proc pool[NPROC];

static struct proc *
agent_ipc_find_live_proc_locked(int pid)
{
	for (struct proc *p = pool; p < &pool[NPROC]; p++)
		if (proc_teardown_live(p) && p->pid == pid && p->is_agent)
			return p;
	return 0;
}

void
agent_ipc_endpoint_capture_locked(struct agent_endpoint_handle *handle,
				  struct proc *p)
{
	/* The caller keeps the process slot and identity fields stable. */
	memset(handle, 0, sizeof(*handle));
	if (p == 0 || p < pool || p >= &pool[NPROC])
		return;
	handle->slot = p - pool;
	handle->pid = p->pid;
	handle->scope_id = agent_identity_proc_scope(p);
	handle->control_id = p->agent_control_id;
}

struct proc *
agent_ipc_endpoint_resolve_locked(struct agent_endpoint_handle *handle)
{
	struct proc *p;

	/* Validation and subsequent proc access remain one caller-held section. */
	if (handle == 0 || handle->slot < 0 || handle->slot >= NPROC ||
	    handle->pid <= 0 || handle->control_id == 0 ||
	    handle->scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    handle->scope_id >= FS_OWNER_SCOPE_FLAG)
		return 0;
	p = &pool[handle->slot];
	if (!proc_teardown_live(p) || p->pid != handle->pid || !p->is_agent ||
	    p->agent_control_id != handle->control_id ||
	    agent_identity_proc_scope(p) != handle->scope_id)
		return 0;
	return p;
}

static int
agent_ipc_route_find(struct proc *target, uint64 source_control_id)
{
	if (target == 0 || source_control_id == 0)
		return -1;
	for (int i = 0; i < AGENT_IPC_ROUTE_MAX; i++)
		if (target->agent_ipc_route_source[i] == source_control_id)
			return i;
	return -1;
}

static int
agent_ipc_route_allows(struct proc *source, struct proc *target,
		       int event_type)
{
	uint64 event_mask;
	int slot;

	if (source == 0 || target == 0 || source->agent_control_id == 0 ||
	    target->agent_control_id == 0 || event_type <= AGENT_EVENT_NONE ||
	    event_type > AGENT_EVENT_MAX ||
	    agent_identity_proc_scope(source) == VFS_SCOPE_NONE ||
	    agent_identity_proc_scope(source) !=
		    agent_identity_proc_scope(target))
		return 0;
	if (source == target)
		return 1;
	event_mask = AGENT_EVENT_MASK(event_type);
	slot = agent_ipc_route_find(target, source->agent_control_id);
	return slot >= 0 &&
	       (target->agent_ipc_route_events[slot] & event_mask) != 0;
}

static void
agent_ipc_remove_source_locked(uint64 source_control_id)
{
	if (source_control_id == 0)
		return;
	for (struct proc *target = pool; target < &pool[NPROC]; target++) {
		for (int i = 0; i < AGENT_IPC_ROUTE_MAX; i++) {
			if (target->agent_ipc_route_source[i] !=
			    source_control_id)
				continue;
			target->agent_ipc_route_source[i] = 0;
			target->agent_ipc_route_events[i] = 0;
			if (target->agent_ipc_route_count > 0)
				target->agent_ipc_route_count--;
		}
	}
}

void
agent_ipc_remove_source(uint64 source_control_id)
{
	int enabled;

	if (source_control_id == 0)
		return;
	enabled = intr_save();
	agent_ipc_remove_source_locked(source_control_id);
	intr_restore(enabled);
}

static int
agent_ipc_route_update(struct proc *target, uint64 source_control_id,
		       uint64 event_mask, int operation)
{
	int free_slot = -1;
	int slot = agent_ipc_route_find(target, source_control_id);

	if (operation == AGENT_IPC_ROUTE_REVOKE) {
		if (slot < 0)
			return AGENT_STATUS_OK;
		target->agent_ipc_route_events[slot] &= ~event_mask;
		if (target->agent_ipc_route_events[slot] == 0) {
			target->agent_ipc_route_source[slot] = 0;
			if (target->agent_ipc_route_count > 0)
				target->agent_ipc_route_count--;
		}
		return AGENT_STATUS_OK;
	}
	if (slot >= 0) {
		target->agent_ipc_route_events[slot] |= event_mask;
		return AGENT_STATUS_OK;
	}
	for (int i = 0; i < AGENT_IPC_ROUTE_MAX; i++)
		if (target->agent_ipc_route_source[i] == 0) {
			free_slot = i;
			break;
		}
	if (free_slot < 0)
		return AGENT_STATUS_NO_SPACE;
	target->agent_ipc_route_source[free_slot] = source_control_id;
	target->agent_ipc_route_events[free_slot] = event_mask;
	target->agent_ipc_route_count++;
	return AGENT_STATUS_OK;
}

/* Event endpoints, queues, delivery, and wait operations. */

static uint64
agent_ipc_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static int
agent_ipc_contains(char *haystack, char *needle)
{
	int hlen;
	int nlen;

	if (needle == 0 || needle[0] == 0)
		return 1;
	if (haystack == 0)
		return 0;
	hlen = strlen(haystack);
	nlen = strlen(needle);
	if (nlen > hlen)
		return 0;
	for (int i = 0; i <= hlen - nlen; i++)
		if (strncmp(haystack + i, needle, nlen) == 0)
			return 1;
	return 0;
}

static void agent_ipc_wake_event_waiters(struct proc *p)
{
	wait_queue_wake_all(&p->agent_event_waiters);
	p->agent_wait_wakeup_count++;
}

static int agent_ipc_filter_matches(struct proc *target, int type, char *payload)
{
	for (int i = 0; i < AGENT_WATCH_MAX; i++) {
		if (!target->agent_watch_valid[i])
			continue;
		if (target->agent_watch_event_type[i] != AGENT_EVENT_NONE &&
		    target->agent_watch_event_type[i] != type)
			continue;
		if (target->agent_watch_filter[i][0] &&
		    !agent_ipc_contains(payload, target->agent_watch_filter[i]))
			continue;
		return 1;
	}
	return 0;
}

int agent_ipc_watch_set(struct proc *p, int event_type, char *filter)
{
	int free_slot = -1;

	for (int i = 0; i < AGENT_WATCH_MAX; i++) {
		if (p->agent_watch_valid[i] &&
		    p->agent_watch_event_type[i] == event_type &&
		    strncmp(p->agent_watch_filter[i], filter,
			    AGENT_WATCH_FILTER_SIZE) == 0)
			return 0;
		if (!p->agent_watch_valid[i] && free_slot < 0)
			free_slot = i;
	}
	if (free_slot < 0)
		return AGENT_STATUS_NO_SPACE;
	p->agent_watch_valid[free_slot] = 1;
	p->agent_watch_event_type[free_slot] = event_type;
	safestrcpy(p->agent_watch_filter[free_slot], filter,
		   sizeof(p->agent_watch_filter[free_slot]));
	p->agent_watch_count++;
	return 0;
}

static int agent_ipc_watch_clear(struct proc *p, int event_type, char *filter)
{
	int removed = 0;
	int clear_all = event_type == AGENT_EVENT_NONE && filter[0] == 0;

	for (int i = 0; i < AGENT_WATCH_MAX; i++) {
		if (!p->agent_watch_valid[i])
			continue;
		if (!clear_all) {
			if (event_type != AGENT_EVENT_NONE &&
			    p->agent_watch_event_type[i] != event_type)
				continue;
			if (filter[0] &&
			    strncmp(p->agent_watch_filter[i], filter,
				    AGENT_WATCH_FILTER_SIZE) != 0)
				continue;
		}
		p->agent_watch_valid[i] = 0;
		p->agent_watch_event_type[i] = AGENT_EVENT_NONE;
		memset(p->agent_watch_filter[i], 0,
		       sizeof(p->agent_watch_filter[i]));
		removed++;
	}
	p->agent_watch_count -= removed;
	if (p->agent_watch_count < 0)
		p->agent_watch_count = 0;
	return removed;
}

#define AGENT_EVENT_ACCOUNT_EXTERNAL   (1ULL << 0)
#define AGENT_EVENT_ACCOUNT_IPC        (1ULL << 1)
#define AGENT_EVENT_ACCOUNT_ATTRIBUTED (1ULL << 2)

static int agent_ipc_event_dequeue(struct proc *p, struct agent_event *event,
			       uint64 *span_owner, uint64 *source_control,
			       uint64 *audit_principal)
{
	uint64 accounting;
	int enabled;
	int slot;

	if (span_owner)
		*span_owner = 0;
	if (source_control)
		*source_control = 0;
	if (audit_principal)
		*audit_principal = 0;
	enabled = intr_save();
	if (p->agent_event_count_queued <= 0) {
		intr_restore(enabled);
		return 0;
	}
	slot = p->agent_event_head;
	memmove(event, &p->agent_events[slot], sizeof(*event));
	if (span_owner)
		*span_owner = p->agent_event_span_owner[slot];
	if (source_control)
		*source_control = p->agent_event_source_control[slot];
	if (audit_principal)
		*audit_principal = p->agent_event_audit_principal[slot];
	accounting = p->agent_event_accounting[slot];
	memset(&p->agent_events[slot], 0, sizeof(*event));
	p->agent_event_source_control[slot] = 0;
	p->agent_event_span_owner[slot] = 0;
	p->agent_event_audit_principal[slot] = 0;
	p->agent_event_accounting[slot] = 0;
	p->agent_event_head =
		(p->agent_event_head + 1) % AGENT_EVENT_QUEUE_CAP;
	p->agent_event_count_queued--;
	if ((accounting & AGENT_EVENT_ACCOUNT_EXTERNAL) != 0 &&
	    p->agent_external_event_count_queued > 0)
		p->agent_external_event_count_queued--;
	if ((accounting & AGENT_EVENT_ACCOUNT_IPC) != 0 &&
	    p->agent_ipc_count_queued > 0)
		p->agent_ipc_count_queued--;
	if ((accounting & AGENT_EVENT_ACCOUNT_ATTRIBUTED) != 0 &&
	    p->agent_attributed_event_count_queued > 0)
		p->agent_attributed_event_count_queued--;
	intr_restore(enabled);
	return 1;
}

enum agent_event_origin {
	AGENT_EVENT_ORIGIN_KERNEL = 1,
	AGENT_EVENT_ORIGIN_DIRECTED,
	AGENT_EVENT_ORIGIN_ATTRIBUTED,
};

enum agent_ipc_event_resource_class {
	AGENT_EVENT_RESOURCE_INVALID = 0,
	AGENT_EVENT_RESOURCE_IPC,
	AGENT_EVENT_RESOURCE_SYSTEM,
};

static int agent_ipc_event_resource_class(int type)
{
	switch (type) {
	case AGENT_EVENT_MESSAGE:
	case AGENT_EVENT_LLM_DONE:
		return AGENT_EVENT_RESOURCE_IPC;
	case AGENT_EVENT_FILE_STATUS:
	case AGENT_EVENT_TIMER:
	case AGENT_EVENT_JOB_DONE:
	case AGENT_EVENT_POLICY_DENIED:
	case AGENT_EVENT_CONTEXT_LIMIT:
	case AGENT_EVENT_DASHBOARD_EXPORT:
	case AGENT_EVENT_CANCELLED:
		return AGENT_EVENT_RESOURCE_SYSTEM;
	default:
		return AGENT_EVENT_RESOURCE_INVALID;
	}
}

static int agent_ipc_event_attributed_type(int type)
{
	switch (type) {
	case AGENT_EVENT_FILE_STATUS:
	case AGENT_EVENT_JOB_DONE:
	case AGENT_EVENT_POLICY_DENIED:
	case AGENT_EVENT_CONTEXT_LIMIT:
	case AGENT_EVENT_DASHBOARD_EXPORT:
		return 1;
	default:
		return 0;
	}
}

static int agent_ipc_source_event_count(struct proc *target,
				    uint64 source_control_id)
{
	int count = 0;
	int slot = target->agent_event_head;

	for (int i = 0; i < target->agent_event_count_queued; i++) {
		if (target->agent_event_source_control[slot] == source_control_id)
			count++;
		slot = (slot + 1) % AGENT_EVENT_QUEUE_CAP;
	}
	return count;
}

static int agent_ipc_queue_event_locked(struct proc *target, struct proc *source,
				    int origin, int type, uint64 corr_id,
				    uint64 cause_sequence, char *payload)
{
	struct agent_event *event;
	uint64 accounting = 0;
	uint64 source_control_id = 0;
	uint64 span_id = 0;
	uint64 span_owner = 0;
	int source_pid = 0;
	int event_class;
	int slot;

	if (!proc_teardown_live(target) || !target->is_agent)
		return 0;
	if (origin == AGENT_EVENT_ORIGIN_DIRECTED ||
	    origin == AGENT_EVENT_ORIGIN_ATTRIBUTED) {
		if (!proc_teardown_live(source) || !source->is_agent ||
		    source->agent_control_id == 0 ||
		    agent_identity_proc_scope(source) == VFS_SCOPE_NONE ||
		    agent_identity_proc_scope(source) != agent_identity_proc_scope(target))
			return 0;
		source_control_id = source->agent_control_id;
		source_pid = source->pid;
		span_id = source->agent_current_span_id;
		span_owner = source->agent_current_span_owner;
	} else if (origin != AGENT_EVENT_ORIGIN_KERNEL || source != 0) {
		return 0;
	} else {
		span_id = target->agent_current_span_id;
		span_owner = target->agent_current_span_owner;
	}
	if (span_id == 0 || span_owner == 0) {
		span_id = 0;
		span_owner = 0;
	}
	if (!agent_ipc_filter_matches(target, type, payload))
		return 0;
	event_class = agent_ipc_event_resource_class(type);
	if (event_class == AGENT_EVENT_RESOURCE_INVALID)
		return 0;
	if ((origin == AGENT_EVENT_ORIGIN_DIRECTED &&
	     event_class != AGENT_EVENT_RESOURCE_IPC) ||
	    (origin == AGENT_EVENT_ORIGIN_ATTRIBUTED &&
	     !agent_ipc_event_attributed_type(type)) ||
	    (origin == AGENT_EVENT_ORIGIN_KERNEL &&
	     event_class != AGENT_EVENT_RESOURCE_SYSTEM))
		return 0;
	if (origin == AGENT_EVENT_ORIGIN_DIRECTED &&
	    !agent_ipc_route_allows(source, target, type))
		return 0;
	if (origin == AGENT_EVENT_ORIGIN_DIRECTED)
		accounting = AGENT_EVENT_ACCOUNT_EXTERNAL |
			     AGENT_EVENT_ACCOUNT_IPC;
	else if (origin == AGENT_EVENT_ORIGIN_ATTRIBUTED)
		accounting = AGENT_EVENT_ACCOUNT_EXTERNAL |
			     AGENT_EVENT_ACCOUNT_ATTRIBUTED;
	if (target->agent_event_count_queued >= AGENT_EVENT_QUEUE_CAP) {
		target->agent_event_dropped++;
		return -1;
	}
	if ((accounting & AGENT_EVENT_ACCOUNT_EXTERNAL) != 0 &&
	    (target->agent_external_event_count_queued >=
		     AGENT_EVENT_EXTERNAL_LIMIT ||
	     agent_ipc_source_event_count(target, source_control_id) >=
		     AGENT_EVENT_SOURCE_LIMIT ||
	     ((accounting & AGENT_EVENT_ACCOUNT_IPC) != 0 &&
	      target->agent_ipc_count_queued >= AGENT_EVENT_IPC_LIMIT) ||
	     ((accounting & AGENT_EVENT_ACCOUNT_ATTRIBUTED) != 0 &&
	      target->agent_attributed_event_count_queued >=
		      AGENT_EVENT_ATTRIBUTED_LIMIT))) {
		target->agent_event_dropped++;
		return -1;
	}
	slot = target->agent_event_tail;
	event = &target->agent_events[slot];
	memset(event, 0, sizeof(*event));
	event->type = type;
	event->source_pid = source_pid;
	event->target_pid = target->pid;
	event->status = AGENT_STATUS_OK;
	event->event_id = agent_observe_alloc_event_id();
	event->tick = agent_ipc_ticks();
	event->corr_id = corr_id;
	event->cause_sequence = cause_sequence;
	event->span_id = span_id;
	safestrcpy(event->payload, payload, sizeof(event->payload));
	target->agent_event_source_control[slot] = source_control_id;
	target->agent_event_span_owner[slot] = span_owner;
	target->agent_event_audit_principal[slot] = source_control_id != 0 ?
						 source_control_id :
						 target->agent_control_id;
	target->agent_event_accounting[slot] = accounting;
	target->agent_event_tail =
		(target->agent_event_tail + 1) % AGENT_EVENT_QUEUE_CAP;
	target->agent_event_count_queued++;
	if ((accounting & AGENT_EVENT_ACCOUNT_EXTERNAL) != 0)
		target->agent_external_event_count_queued++;
	if ((accounting & AGENT_EVENT_ACCOUNT_IPC) != 0)
		target->agent_ipc_count_queued++;
	if ((accounting & AGENT_EVENT_ACCOUNT_ATTRIBUTED) != 0)
		target->agent_attributed_event_count_queued++;
	target->agent_event_count++;
	agent_observe_record_event(
		AGENT_AUDIT_KIND_EVENT_ENQUEUE, target, event, span_owner,
		target->agent_event_audit_principal[slot]);
	agent_ipc_wake_event_waiters(target);
	return 1;
}

static int agent_ipc_queue_event(struct proc *target, struct proc *source,
			     int origin, int type, uint64 corr_id,
			     uint64 cause_sequence, char *payload)
{
	int enabled = intr_save();
	int delivered = agent_ipc_queue_event_locked(target, source, origin, type,
						 corr_id, cause_sequence, payload);

	intr_restore(enabled);
	return delivered;
}

int agent_ipc_deliver_pid(int pid, struct proc *source, int type,
			  uint64 corr_id, uint64 cause_sequence,
			  char *payload, int mirror_mailbox,
			  int *delivered)
{
	struct agent_endpoint_handle source_handle;
	struct agent_endpoint_handle target_handle;
	struct proc *target;
	uint64 event_mask;
	int handoff_ready = 0;
	int enabled;
	int queued;
	int status;

	if (delivered)
		*delivered = 0;
	if (pid <= 0 || source == 0 || type <= AGENT_EVENT_NONE ||
	    type > AGENT_EVENT_MAX)
		return AGENT_STATUS_BAD_PARAM;
	event_mask = AGENT_EVENT_MASK(type);
	if ((event_mask & AGENT_IPC_EVENT_MASK) == 0)
		return AGENT_STATUS_BAD_PARAM;
	enabled = intr_save();
	if (!proc_teardown_live(source) || !source->is_agent ||
	    source->agent_control_id == 0) {
		status = AGENT_STATUS_NOT_FOUND;
		goto out;
	}
	target = agent_ipc_find_live_proc_locked(pid);
	if (target == 0) {
		status = AGENT_STATUS_NOT_FOUND;
		goto out;
	}
	if (!agent_ipc_route_allows(source, target, type)) {
		status = AGENT_STATUS_DENIED;
		goto out;
	}
	queued = agent_ipc_queue_event_locked(target, source,
					  AGENT_EVENT_ORIGIN_DIRECTED, type, corr_id,
					  cause_sequence, payload);
	if (queued < 0) {
		status = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	if (queued == 0) {
		status = AGENT_STATUS_NOT_FOUND;
		goto out;
	}
	if (type == AGENT_EVENT_MESSAGE && mirror_mailbox) {
		target->agent_mailbox_valid = 1;
		target->agent_mailbox_from = source->pid;
		safestrcpy(target->agent_mailbox, payload,
			   sizeof(target->agent_mailbox));
	}
	if (type == AGENT_EVENT_MESSAGE && target != source) {
		agent_ipc_endpoint_capture_locked(&source_handle, source);
		agent_ipc_endpoint_capture_locked(&target_handle, target);
		handoff_ready = source_handle.control_id != 0 &&
				target_handle.control_id != 0;
	}
	if (delivered)
		*delivered = 1;
	status = AGENT_STATUS_OK;
out:
	intr_restore(enabled);
	if (handoff_ready)
		agent_metadata_prefetch_handoff(&target_handle, &source_handle);
	return status;
}

int agent_ipc_deliver_watchers(struct proc *source, int type,
			       uint64 corr_id, uint64 cause_sequence,
			       char *payload)
{
	int delivered = 0;
	int rc;

	if (source == 0)
		return 0;

	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (p->state == P_UNUSED || !p->is_agent ||
		    agent_identity_proc_scope(p) != agent_identity_proc_scope(source))
			continue;
		rc = agent_ipc_queue_event(p, source, AGENT_EVENT_ORIGIN_ATTRIBUTED,
				       type, corr_id, cause_sequence, payload);
		if (rc > 0)
			delivered += rc;
	}
	return delivered;
}

static int agent_ipc_wait_take_cancel(struct proc *p, struct agent_event *event,
				  uint64 *span_owner, uint64 *source_control,
				  uint64 *audit_principal)
{
	if (!p->agent_wait_cancel_pending)
		return 0;
	if (span_owner)
		*span_owner = p->agent_wait_cancel_span_owner;
	if (source_control)
		*source_control = p->agent_wait_cancel_source_control;
	if (audit_principal)
		*audit_principal = p->agent_wait_cancel_audit_principal;
	memset(event, 0, sizeof(*event));
	event->type = AGENT_EVENT_CANCELLED;
	event->source_pid = p->agent_wait_cancel_source_pid;
	event->target_pid = p->pid;
	event->status = AGENT_STATUS_CANCELLED;
	event->event_id = p->agent_wait_cancel_event_id;
	event->tick = p->agent_wait_cancel_tick;
	event->corr_id = p->agent_wait_cancel_corr_id;
	event->cause_sequence = p->agent_wait_cancel_cause_sequence;
	event->span_id = p->agent_wait_cancel_span_id;
	safestrcpy(event->payload, p->agent_wait_cancel_reason,
		   sizeof(event->payload));
	p->agent_wait_cancel_pending = 0;
	p->agent_wait_cancel_span_id = 0;
	p->agent_wait_cancel_span_owner = 0;
	p->agent_wait_cancel_source_control = 0;
	p->agent_wait_cancel_audit_principal = 0;
	p->agent_wait_deadline_valid = 0;
	p->agent_wait_deadline = 0;
	p->loop_state = AGENT_LOOP_RUNNING;
	return 1;
}

void
agent_ipc_proc_reset(struct proc *p)
{
	if (p == 0)
		return;
	p->agent_mailbox_valid = 0;
	p->agent_mailbox_from = 0;
	memset(p->agent_mailbox, 0, sizeof(p->agent_mailbox));
	p->agent_watch_count = 0;
	memset(p->agent_watch_valid, 0, sizeof(p->agent_watch_valid));
	memset(p->agent_watch_event_type, 0,
	       sizeof(p->agent_watch_event_type));
	memset(p->agent_watch_filter, 0, sizeof(p->agent_watch_filter));
	p->agent_ipc_route_count = 0;
	memset(p->agent_ipc_route_source, 0,
	       sizeof(p->agent_ipc_route_source));
	memset(p->agent_ipc_route_events, 0,
	       sizeof(p->agent_ipc_route_events));
	memset(p->agent_events, 0, sizeof(p->agent_events));
	memset(p->agent_event_source_control, 0,
	       sizeof(p->agent_event_source_control));
	memset(p->agent_event_span_owner, 0,
	       sizeof(p->agent_event_span_owner));
	memset(p->agent_event_audit_principal, 0,
	       sizeof(p->agent_event_audit_principal));
	memset(p->agent_event_accounting, 0,
	       sizeof(p->agent_event_accounting));
	p->agent_event_head = 0;
	p->agent_event_tail = 0;
	p->agent_event_count_queued = 0;
	p->agent_external_event_count_queued = 0;
	p->agent_ipc_count_queued = 0;
	p->agent_attributed_event_count_queued = 0;
	p->agent_event_count = 0;
	p->agent_event_dropped = 0;
	p->agent_wait_count = 0;
	p->agent_wait_loop_count = 0;
	p->agent_wait_sleep_count = 0;
	p->agent_wait_wakeup_count = 0;
	p->agent_wait_cancel_count = 0;
	p->agent_timeout_count = 0;
	p->agent_wait_deadline_valid = 0;
	p->agent_wait_deadline = 0;
	p->agent_wait_cancel_pending = 0;
	p->agent_wait_cancel_source_pid = 0;
	p->agent_wait_cancel_event_id = 0;
	p->agent_wait_cancel_corr_id = 0;
	p->agent_wait_cancel_tick = 0;
	p->agent_wait_cancel_cause_sequence = 0;
	p->agent_wait_cancel_span_id = 0;
	p->agent_wait_cancel_span_owner = 0;
	p->agent_wait_cancel_source_control = 0;
	p->agent_wait_cancel_audit_principal = 0;
	memset(p->agent_wait_cancel_reason, 0,
	       sizeof(p->agent_wait_cancel_reason));
	p->heartbeat_interval = 0;
	p->agent_last_heartbeat_tick = 0;
}

int
agent_ipc_mailbox_take(struct proc *p, int *source_pid, char *message, int n)
{
	int enabled;
	int available;

	if (p == 0 || message == 0 || n <= 0)
		return 0;
	enabled = intr_save();
	available = p->agent_mailbox_valid;
	if (available) {
		if (source_pid)
			*source_pid = p->agent_mailbox_from;
		safestrcpy(message, p->agent_mailbox, n);
		p->agent_mailbox_valid = 0;
	}
	intr_restore(enabled);
	return available;
}

uint64
agent_ipc_heartbeat_set(struct proc *p, uint64 interval)
{
	uint64 now = agent_ipc_ticks();

	if (p == 0)
		return 0;
	p->heartbeat_interval = interval;
	p->agent_last_heartbeat_tick = now;
	return now;
}

void
agent_ipc_tick_proc(struct proc *p, uint64 now)
{
	char payload[AGENT_EVENT_PAYLOAD_SIZE];

	if (p == 0 || p->state == P_UNUSED || !p->is_agent)
		return;
	if (p->agent_wait_deadline_valid &&
	    now >= p->agent_wait_deadline) {
		p->agent_wait_deadline_valid = 0;
		agent_ipc_wake_event_waiters(p);
	}
	if (p->heartbeat_interval > 0 &&
	    now - p->agent_last_heartbeat_tick >=
		    (uint64)p->heartbeat_interval) {
		p->agent_last_heartbeat_tick = now;
		safestrcpy(payload, "timer=heartbeat", sizeof(payload));
		agent_ipc_queue_event(p, 0, AGENT_EVENT_ORIGIN_KERNEL,
				      AGENT_EVENT_TIMER, now,
				      p->context_path_latest, payload);
	}
}


/* IPC-facing system calls. */

int sys_agent_watch(int event_type, uint64 filteraddr)
{
	struct proc *p = curr_proc();
	char filter[AGENT_WATCH_FILTER_SIZE];

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_WATCH))
		return AGENT_STATUS_DENIED;
	if (event_type < AGENT_EVENT_NONE ||
	    event_type > AGENT_EVENT_MAX)
		return AGENT_STATUS_BAD_PARAM;
	memset(filter, 0, sizeof(filter));
	if (filteraddr != 0 &&
	    copyinstr(p->pagetable, filter, filteraddr, sizeof(filter)) < 0)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	if (agent_ipc_watch_set(p, event_type, filter) < 0) {
		agent_lifecycle_context_lane_leave(p);
		return AGENT_STATUS_NO_SPACE;
	}
	p->loop_state = AGENT_LOOP_IDLE;
	agent_context_append_system(p, AGENT_TOOL_AGENT_WATCH, 0, event_type,
				    filter, "watch", AGENT_STATUS_OK,
				    event_type, 0, 0);
	agent_lifecycle_context_lane_leave(p);
	return 0;
}

int sys_agent_unwatch(int event_type, uint64 filteraddr)
{
	struct proc *p = curr_proc();
	char filter[AGENT_WATCH_FILTER_SIZE];
	int removed;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_WATCH))
		return AGENT_STATUS_DENIED;
	if (event_type < AGENT_EVENT_NONE ||
	    event_type > AGENT_EVENT_MAX)
		return AGENT_STATUS_BAD_PARAM;
	memset(filter, 0, sizeof(filter));
	if (filteraddr != 0 &&
	    copyinstr(p->pagetable, filter, filteraddr, sizeof(filter)) < 0)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	removed = agent_ipc_watch_clear(p, event_type, filter);
	agent_context_append_system(p, AGENT_TOOL_AGENT_WATCH, 0, event_type,
				    filter, "unwatch", AGENT_STATUS_OK,
				    removed, 0, 0);
	agent_lifecycle_context_lane_leave(p);
	return removed;
}

int sys_agent_wait(uint64 eventaddr, int timeout_ticks)
{
	struct proc *p = curr_proc();
	struct agent_event event;
	uint64 start = agent_ipc_ticks();
	uint64 now;
	uint64 span_owner = 0;
	uint64 source_control = 0;
	uint64 audit_principal = 0;
	int status;

	if (!p->is_agent)
		return -1;
	if (eventaddr &&
	    user_range_check(p->pagetable, eventaddr, sizeof(event), PTE_W) < 0)
		return -1;
	memset(&event, 0, sizeof(event));
	p->agent_wait_count++;
	for (;;) {
		p->agent_wait_loop_count++;
		if (agent_ipc_wait_take_cancel(p, &event, &span_owner,
					   &source_control, &audit_principal)) {
			status = AGENT_STATUS_CANCELLED;
			break;
		}
		if (agent_ipc_event_dequeue(p, &event, &span_owner,
					&source_control, &audit_principal)) {
			p->loop_state = AGENT_LOOP_RUNNING;
			p->agent_wait_deadline_valid = 0;
			status = AGENT_STATUS_OK;
			break;
		}
		now = agent_ipc_ticks();
		if (p->heartbeat_interval > 0 &&
		    now - p->agent_last_heartbeat_tick >=
			    (uint64)p->heartbeat_interval) {
			p->agent_last_heartbeat_tick = now;
			agent_ipc_queue_event(p, 0, AGENT_EVENT_ORIGIN_KERNEL,
					  AGENT_EVENT_TIMER, now,
					  p->context_path_latest,
					  "timer=heartbeat");
			continue;
		}
		if (timeout_ticks >= 0 && now - start >= (uint64)timeout_ticks) {
			p->agent_timeout_count++;
			p->loop_state = AGENT_LOOP_IDLE;
			p->agent_wait_deadline_valid = 0;
			event.type = AGENT_EVENT_TIMER;
			event.target_pid = p->pid;
			event.status = AGENT_STATUS_TIMEOUT;
			event.tick = now;
			safestrcpy(event.payload, "timeout",
				   sizeof(event.payload));
			status = AGENT_STATUS_TIMEOUT;
			break;
		}
		p->loop_state = AGENT_LOOP_WAITING;
		if (timeout_ticks >= 0) {
			p->agent_wait_deadline_valid = 1;
			p->agent_wait_deadline = start + timeout_ticks;
		} else {
			p->agent_wait_deadline_valid = 0;
			p->agent_wait_deadline = 0;
		}
		p->agent_wait_sleep_count++;
		if (wait_queue_sleep(&p->agent_event_waiters) < 0) {
			p->agent_wait_deadline_valid = 0;
			status = -1;
			break;
		}
	}
	if (eventaddr &&
	    copyout(p->pagetable, eventaddr, (char *)&event,
		    sizeof(event)) < 0)
		return -1;
	if (status == AGENT_STATUS_OK || status == AGENT_STATUS_CANCELLED) {
		/*
		 * Waiting itself must not pin the context lane.  Once an event has
		 * been selected, serialize attribution and its context commit as one
		 * operation so another Agent thread cannot splice into the chain.
		 */
		if (agent_lifecycle_context_lane_enter(p) < 0)
			return -1;
		if (event.span_id != 0 && span_owner != 0) {
			p->agent_current_span_id = event.span_id;
			p->agent_current_span_owner = span_owner;
		}
		p->agent_current_cause_sequence = event.cause_sequence;
		p->agent_current_cause_pid = event.source_pid > 0 ?
						 event.source_pid : p->pid;
		p->agent_current_cause_control = source_control != 0 ?
						     source_control :
						     p->agent_control_id;
		agent_observe_record_event(
			AGENT_AUDIT_KIND_EVENT_CONSUME, p, &event,
			span_owner, audit_principal);
		agent_context_append_system(p, AGENT_TOOL_AGENT_WAIT,
					    event.event_id, event.type,
					    event.payload,
					    status == AGENT_STATUS_CANCELLED ?
						    "cancelled" :
						    "event",
					    status, event.type,
					    event.source_pid, event.corr_id);
		agent_lifecycle_context_lane_leave(p);
	}
	p->loop_state = AGENT_LOOP_IDLE;
	return status;
}

int sys_agent_wait_cancel(int pid, uint64 reasonaddr)
{
	struct proc *p = curr_proc();
	struct proc *target;
	char reason[AGENT_EVENT_PAYLOAD_SIZE];
	uint64 event_id;
	int enabled;
	int status = AGENT_STATUS_NOT_FOUND;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_WAIT_CANCEL))
		return AGENT_STATUS_DENIED;
	memset(reason, 0, sizeof(reason));
	if (reasonaddr != 0 &&
	    copyinstr(p->pagetable, reason, reasonaddr, sizeof(reason)) < 0)
		return -1;
	if (reason[0] == 0)
		safestrcpy(reason, "cancel", sizeof(reason));
	enabled = intr_save();
	for (target = pool; target < &pool[NPROC]; target++) {
		if (target->state == P_UNUSED || target->pid != pid)
			continue;
		if (!proc_teardown_live(target) || !target->is_agent) {
			status = AGENT_STATUS_NOT_FOUND;
			break;
		}
		if (!agent_identity_controls_target(p, target)) {
			status = AGENT_STATUS_DENIED;
			break;
		}
		if (target->agent_wait_cancel_pending) {
			status = AGENT_STATUS_DUPLICATE;
			break;
		}
		event_id = agent_observe_alloc_event_id();
		target->agent_wait_cancel_pending = 1;
		target->agent_wait_cancel_source_pid = p->pid;
		target->agent_wait_cancel_event_id = event_id;
		target->agent_wait_cancel_corr_id = event_id;
		target->agent_wait_cancel_tick = agent_ipc_ticks();
		target->agent_wait_cancel_cause_sequence =
			p->context_path_latest;
		target->agent_wait_cancel_span_id = p->agent_current_span_id;
		target->agent_wait_cancel_span_owner =
			p->agent_current_span_owner;
		target->agent_wait_cancel_source_control = p->agent_control_id;
		target->agent_wait_cancel_audit_principal =
			p->agent_control_id;
		safestrcpy(target->agent_wait_cancel_reason, reason,
			   sizeof(target->agent_wait_cancel_reason));
		target->agent_wait_cancel_count++;
		target->agent_wait_deadline_valid = 0;
		agent_ipc_wake_event_waiters(target);
		status = AGENT_STATUS_OK;
		break;
	}
	intr_restore(enabled);
	return status;
}

int sys_agent_route_config(int source_pid, int target_pid, uint64 event_mask,
			   int operation)
{
	struct proc *p = curr_proc();
	struct proc *source;
	struct proc *target;
	int enabled;
	int status;

	if (!p->is_agent)
		return -1;
	if (source_pid <= 0 || target_pid <= 0 || event_mask == 0 ||
	    (event_mask & ~AGENT_IPC_EVENT_MASK) != 0 ||
	    (operation != AGENT_IPC_ROUTE_GRANT &&
	     operation != AGENT_IPC_ROUTE_REVOKE))
		return AGENT_STATUS_BAD_PARAM;
	enabled = intr_save();
	source = agent_ipc_find_live_proc_locked(source_pid);
	target = agent_ipc_find_live_proc_locked(target_pid);
	if (source == 0 || target == 0) {
		status = AGENT_STATUS_NOT_FOUND;
		goto out;
	}
	if (agent_identity_proc_scope(source) == VFS_SCOPE_NONE ||
	    agent_identity_proc_scope(source) != agent_identity_proc_scope(target)) {
		status = AGENT_STATUS_DENIED;
		goto out;
	}
	if (p != target) {
		if (!agent_identity_has_cap(p, AGENT_CAP_ROUTE_MANAGE) ||
		    !agent_identity_controls_or_self(p, source) ||
		    !agent_identity_controls_or_self(p, target)) {
			status = AGENT_STATUS_DENIED;
			goto out;
		}
	} else if (!agent_identity_has_cap(p, AGENT_CAP_WATCH)) {
		status = AGENT_STATUS_DENIED;
		goto out;
	}
	if (source == target) {
		status = AGENT_STATUS_OK;
		goto out;
	}
	status = agent_ipc_route_update(target, source->agent_control_id,
					event_mask, operation);
out:
	intr_restore(enabled);
	return status;
}

int sys_agent_heartbeat(int interval_ticks)
{
	struct proc *p = curr_proc();
	uint64 now;

	if (!p->is_agent)
		return -1;
	if (interval_ticks < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	now = agent_ipc_heartbeat_set(p, interval_ticks);
	agent_context_append_system(p, AGENT_TOOL_AGENT_HEARTBEAT, 0,
				    interval_ticks, "heartbeat", "heartbeat",
				    AGENT_STATUS_OK, interval_ticks,
				    now, 0);
	agent_lifecycle_context_lane_leave(p);
	return 0;
}

int sys_agent_wake(int pid, uint64 eventaddr)
{
	struct proc *p = curr_proc();
	struct agent_event event;
	char payload[AGENT_EVENT_PAYLOAD_SIZE];
	int delivered;
	int status;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_any_cap(p, AGENT_CAP_MESSAGE_SEND | AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (eventaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	if (copyin(p->pagetable, (char *)&event, eventaddr, sizeof(event)) < 0)
		return -1;
	if (event.type <= AGENT_EVENT_NONE || event.type > AGENT_EVENT_MAX)
		return AGENT_STATUS_BAD_PARAM;
	if (event.type != AGENT_EVENT_MESSAGE)
		return AGENT_STATUS_DENIED;
	event.payload[sizeof(event.payload) - 1] = 0;
	safestrcpy(payload, event.payload, sizeof(payload));
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	status = agent_ipc_deliver_pid(pid, p, event.type, event.corr_id,
				   p->context_path_latest, payload, 0,
				   &delivered);
	agent_metadata_txn_unlock();
	return status;
}
