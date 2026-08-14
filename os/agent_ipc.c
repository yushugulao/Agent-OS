#include "agent_context.h"
#include "agent_internal.h"
#include "agent_live_query_events.h"
#include "agent_provenance.h"
#include "defs.h"
#include "timer.h"
#include "trap.h"
#ifdef WAIT_ATOMIC_TEST_PROFILE
#include "wait_atomic_test.h"
#endif

extern struct proc pool[NPROC];

static int agent_ipc_handoff_event_locked(struct proc *p);
static struct agent_file_live_watch agent_ipc_live_watch_scratch;

static struct thread *
agent_ipc_event_baton_owner_locked(struct proc *p)
{
	struct thread *owner = 0;

	if (intr_get())
		panic("Agent event baton unlocked");
	if (p == 0 || p < pool || p >= &pool[NPROC])
		panic("Agent event baton process");
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *t = &p->threads[tid];

		if (!t->agent_event_baton)
			continue;
		if (t->process != p || t->tid != tid ||
		    t->identity_generation == 0) {
			t->agent_event_baton = 0;
			continue;
		}
		if (owner != 0)
			panic("Agent event baton duplicate");
		owner = t;
	}
	return owner;
}

static void
agent_ipc_event_baton_clear_locked(struct proc *p)
{
	if (intr_get())
		panic("Agent event baton unlocked");
	if (p == 0 || p < pool || p >= &pool[NPROC])
		panic("Agent event baton process");
	for (int tid = 0; tid < NTHREAD; tid++)
		p->threads[tid].agent_event_baton = 0;
}

static int
agent_ipc_event_baton_active_locked(struct proc *p)
{
	return agent_ipc_event_baton_owner_locked(p) != 0;
}

static int
agent_ipc_event_baton_owned_locked(struct proc *p, struct thread *t)
{
	return t != 0 && agent_ipc_event_baton_owner_locked(p) == t;
}

static int
agent_ipc_event_baton_release_locked(struct proc *p, struct thread *t)
{
	if (!agent_ipc_event_baton_owned_locked(p, t))
		return 0;
	t->agent_event_baton = 0;
	return 1;
}

static void
agent_ipc_thread_state_clear_locked(struct thread *t, int state)
{
	t->agent_wait_deadline = 0;
	t->agent_wait_deadline_valid = 0;
	t->agent_loop_state = state;
	t->agent_event_baton = 0;
}

void
agent_ipc_thread_runtime_transition(struct thread *t, int transition)
{
	struct proc *p;
	int pass_baton = 0;
	int enabled = intr_save();

	if (t == 0 || (p = t->process) == 0)
		goto out;
	if (transition != AGENT_THREAD_RUNTIME_ACTIVATE &&
	    transition != AGENT_THREAD_RUNTIME_RELEASE)
		panic("Agent thread runtime transition");
	if (transition == AGENT_THREAD_RUNTIME_ACTIVATE &&
	    t->identity_generation == 0)
		panic("Agent thread activation state");
	if (transition == AGENT_THREAD_RUNTIME_RELEASE)
		pass_baton = agent_ipc_event_baton_release_locked(p, t);
	agent_ipc_thread_state_clear_locked(
		t, transition != AGENT_THREAD_RUNTIME_RELEASE && p->is_agent ?
			AGENT_LOOP_IDLE : AGENT_LOOP_NONE);
	agent_identity_loop_refresh_locked(p);
	if (pass_baton && p->is_agent && proc_teardown_live(p))
		agent_ipc_handoff_event_locked(p);
out:
	intr_restore(enabled);
}

void
agent_ipc_process_image_install_locked(struct proc *p)
{
	if (intr_get())
		panic("Agent image install unlocked");
	agent_ipc_event_baton_clear_locked(p);
	for (int tid = 0; tid < NTHREAD; tid++)
		agent_ipc_thread_state_clear_locked(&p->threads[tid],
						    AGENT_LOOP_NONE);
	agent_identity_loop_refresh_locked(p);
}

#define AGENT_IPC_PROC_STATE_BYTES \
	(__builtin_offsetof(struct proc, agent_last_heartbeat_tick) + \
	 sizeof(((struct proc *)0)->agent_last_heartbeat_tick) - \
	 __builtin_offsetof(struct proc, agent_mailbox_valid))

static struct proc *
agent_ipc_find_live_proc_locked(int pid, int agent_endpoint)
{
	for (struct proc *p = pool; p < &pool[NPROC]; p++)
		if (proc_teardown_live(p) && p->pid == pid &&
		    !!p->is_agent == !!agent_endpoint)
			return p;
	return 0;
}

static int
agent_ipc_same_scope(struct proc *left, struct proc *right)
{
	struct workflow_lifecycle_key left_key;
	struct workflow_lifecycle_key right_key;
	uint scope;

	if (left == 0 || right == 0)
		return 0;
	scope = agent_identity_proc_scope(left);
	if (scope == VFS_SCOPE_NONE || scope != agent_identity_proc_scope(right) ||
	    !left->workflow_lifecycle_charged ||
	    !right->workflow_lifecycle_charged)
		return 0;
	left_key.id = left->workflow_lifecycle_id;
	left_key.generation = left->workflow_lifecycle_generation;
	right_key.id = right->workflow_lifecycle_id;
	right_key.generation = right->workflow_lifecycle_generation;
	return workflow_lifecycle_key_valid(left_key) &&
	       workflow_lifecycle_key_equal(left_key, right_key);
}

static int
agent_ipc_route_find(struct proc *target, uint64 source_control_id)
{
	struct agent_ipc_observe_cold_state *cold;

	if (target == 0 || source_control_id == 0 ||
	    (cold = target->agent_ipc_observe_cold) == 0)
		return -1;
	for (int i = 0; i < AGENT_IPC_ROUTE_MAX; i++)
		if (cold->ipc_route_source[i] == source_control_id)
			return i;
	return -1;
}

static int
agent_ipc_route_allows(struct proc *source, struct proc *target,
		       int event_type)
{
	struct agent_ipc_observe_cold_state *cold;
	int slot;

	if (source == 0 || target == 0 || source->agent_control_id == 0 ||
	    target->agent_control_id == 0 || event_type <= AGENT_EVENT_NONE ||
	    event_type > AGENT_EVENT_MAX || !agent_ipc_same_scope(source, target))
		return 0;
	if (source == target)
		return 1;
	slot = agent_ipc_route_find(target, source->agent_control_id);
	cold = target->agent_ipc_observe_cold;
	return slot >= 0 && (cold->ipc_route_events[slot] &
			     AGENT_EVENT_MASK(event_type)) != 0;
}

int
agent_ipc_task_route_allows_locked(struct proc *source, struct proc *target)
{
	struct agent_ipc_observe_cold_state *cold;
	int slot;

	if (intr_get())
		panic("Agent task route unlocked");
	if (source == 0 || target == 0 || source->agent_control_id == 0 ||
	    target->agent_control_id == 0 ||
	    !agent_ipc_same_scope(source, target))
		return 0;
	if (source == target)
		return 1;
	slot = agent_ipc_route_find(target, source->agent_control_id);
	cold = target->agent_ipc_observe_cold;
	return slot >= 0 && (cold->ipc_route_events[slot] &
			     AGENT_IPC_ROUTE_TASK) != 0;
}

static void
agent_ipc_remove_source_locked(uint64 source_control_id)
{
	if (source_control_id == 0)
		return;
	for (struct proc *target = pool; target < &pool[NPROC]; target++) {
		struct agent_ipc_observe_cold_state *cold =
			target->agent_ipc_observe_cold;

		if (cold == 0)
			continue;
		for (int i = 0; i < AGENT_IPC_ROUTE_MAX; i++) {
			if (cold->ipc_route_source[i] != source_control_id)
				continue;
			cold->ipc_route_source[i] = 0;
			cold->ipc_route_events[i] = 0;
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
	struct agent_ipc_observe_cold_state *cold =
		target->agent_ipc_observe_cold;
	int free_slot = -1;
	int slot = agent_ipc_route_find(target, source_control_id);

	if (operation == AGENT_IPC_ROUTE_REVOKE) {
		if (slot < 0)
			return AGENT_STATUS_OK;
		cold->ipc_route_events[slot] &= ~event_mask;
		if (cold->ipc_route_events[slot] == 0) {
			cold->ipc_route_source[slot] = 0;
			if (target->agent_ipc_route_count > 0)
				target->agent_ipc_route_count--;
		}
		return AGENT_STATUS_OK;
	}
	if (slot >= 0) {
		cold->ipc_route_events[slot] |= event_mask;
		return AGENT_STATUS_OK;
	}
	for (int i = 0; i < AGENT_IPC_ROUTE_MAX; i++)
		if (cold->ipc_route_source[i] == 0) {
			free_slot = i;
			break;
		}
	if (free_slot < 0)
		return AGENT_STATUS_NO_SPACE;
	cold->ipc_route_source[free_slot] = source_control_id;
	cold->ipc_route_events[free_slot] = event_mask;
	target->agent_ipc_route_count++;
	return AGENT_STATUS_OK;
}

/* 事件端点、队列、投递与等待。 */

static uint64
agent_ipc_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static int
agent_ipc_contains(char *haystack, char *needle)
{
	int nlen;

	if (needle == 0 || needle[0] == 0)
		return 1;
	if (haystack == 0)
		return 0;
	nlen = strlen(needle);
	for (; haystack[0] != 0; haystack++)
		if (strncmp(haystack, needle, nlen) == 0)
			return 1;
	return 0;
}

#define AGENT_EVENT_ACCOUNT_EXTERNAL   (1ULL << 0)
#define AGENT_EVENT_ACCOUNT_IPC        (1ULL << 1)
#define AGENT_EVENT_ACCOUNT_ATTRIBUTED (1ULL << 2)
#define AGENT_EVENT_ACCOUNT_COALESCED  (1ULL << 3)
#define AGENT_EVENT_ACCOUNT_RESERVED   (1ULL << 4)

#define AGENT_WAIT_CANCEL_PENDING  1
#define AGENT_WAIT_CANCEL_RESERVED 2

#define AGENT_WAIT_CANCEL_SLOT (-1)

static int
agent_ipc_event_consumable_locked(struct proc *p)
{
	struct agent_ipc_observe_cold_state *cold = p->agent_ipc_observe_cold;

	if (intr_get())
		panic("Agent event predicate unlocked");
	if (cold == 0)
		return 0;
	/* 队首或取消事件已被保留时，必须等持有者提交或回滚后再传棒。 */
	if (p->agent_event_count_queued > 0 &&
	    (cold->event_accounting[p->agent_event_head] &
	     AGENT_EVENT_ACCOUNT_RESERVED) != 0)
		return 0;
	if (p->agent_wait_cancel_pending != 0)
		return p->agent_wait_cancel_pending == AGENT_WAIT_CANCEL_PENDING;
	return p->agent_event_count_queued > 0;
}

static int
agent_ipc_handoff_event_locked(struct proc *p)
{
	struct thread *selected;
	int woken;

	if (intr_get())
		panic("Agent event handoff unlocked");
	/* 等待队列决定接收者；不得从无关的 WAITING 线程推断所有者。 */
	if (p->agent_event_waiters.head == 0 ||
	    !agent_ipc_event_consumable_locked(p) ||
	    agent_ipc_event_baton_active_locked(p))
		return 0;
	selected = wait_queue_wake_one_thread(&p->agent_event_waiters);
	woken = selected != 0;
	if (selected != 0) {
		if (selected->tid < 0 || selected->tid >= NTHREAD ||
		    selected->identity_generation == 0)
			panic("Agent event wake identity");
		if (selected != &p->threads[selected->tid] ||
		    selected->process != p || selected->agent_event_baton)
			panic("Agent event wake owner");
		selected->agent_event_baton = 1;
	}
	p->agent_wait_wakeup_count += woken;
	return woken;
}

static void
agent_ipc_broadcast_event_teardown_locked(struct proc *p)
{
	int woken;

	if (intr_get())
		panic("Agent event teardown unlocked");
	agent_ipc_event_baton_clear_locked(p);
	woken = wait_queue_wake_all(&p->agent_event_waiters);
	p->agent_wait_wakeup_count += woken;
}

static int agent_ipc_filter_matches(struct proc *target, int type, char *payload)
{
	struct agent_ipc_observe_cold_state *cold =
		target->agent_ipc_observe_cold;

	for (int i = 0; i < AGENT_WATCH_MAX; i++) {
		if (!cold->watch_valid[i])
			continue;
		if (cold->watch_event_type[i] != AGENT_EVENT_NONE &&
		    cold->watch_event_type[i] != type)
			continue;
		if (cold->watch_filter[i][0] &&
		    !agent_ipc_contains(payload, cold->watch_filter[i]))
			continue;
		return 1;
	}
	return 0;
}

int agent_ipc_watch_set(struct proc *p, int event_type, char *filter)
{
	struct agent_ipc_observe_cold_state *cold = p->agent_ipc_observe_cold;
	int free_slot = -1;

	for (int i = 0; i < AGENT_WATCH_MAX; i++) {
		if (cold->watch_valid[i] &&
		    cold->watch_event_type[i] == event_type &&
		    strncmp(cold->watch_filter[i], filter,
			    AGENT_WATCH_FILTER_SIZE) == 0)
			return 0;
		if (!cold->watch_valid[i] && free_slot < 0)
			free_slot = i;
	}
	if (free_slot < 0)
		return AGENT_STATUS_NO_SPACE;
	cold->watch_valid[free_slot] = 1;
	cold->watch_event_type[free_slot] = event_type;
	safestrcpy(cold->watch_filter[free_slot], filter,
		   sizeof(cold->watch_filter[free_slot]));
	p->agent_watch_count++;
	return 0;
}

static int agent_ipc_watch_clear(struct proc *p, int event_type, char *filter)
{
	struct agent_ipc_observe_cold_state *cold = p->agent_ipc_observe_cold;
	int removed = 0;
	int clear_all = event_type == AGENT_EVENT_NONE && filter[0] == 0;

	for (int i = 0; i < AGENT_WATCH_MAX; i++) {
		if (!cold->watch_valid[i])
			continue;
		if (!clear_all) {
			if (event_type != AGENT_EVENT_NONE &&
			    cold->watch_event_type[i] != event_type)
				continue;
			if (filter[0] &&
			    strncmp(cold->watch_filter[i], filter,
				    AGENT_WATCH_FILTER_SIZE) != 0)
				continue;
		}
		cold->watch_valid[i] = 0;
		cold->watch_event_type[i] = AGENT_EVENT_NONE;
		memset(cold->watch_filter[i], 0, sizeof(cold->watch_filter[i]));
		removed++;
	}
	p->agent_watch_count -= removed;
	if (p->agent_watch_count < 0)
		p->agent_watch_count = 0;
	return removed;
}

struct agent_ipc_wait_reservation {
	int slot;
	uint64 cookie;
	uint64 span_owner;
	uint64 source_control;
	uint64 audit_principal;
	uint64 provenance_labels;
};

static int
agent_ipc_wait_reserve_locked(struct proc *p, struct thread *t,
			      struct agent_event *event,
			      struct agent_ipc_wait_reservation *reservation)
{
	struct agent_ipc_observe_cold_state *cold = p->agent_ipc_observe_cold;
	int owns_baton = agent_ipc_event_baton_owned_locked(p, t);
	int slot;

	if (intr_get())
		panic("Agent wait reservation unlocked");
	if (agent_ipc_event_baton_active_locked(p) && !owns_baton)
		return 0;
	if (p->agent_event_count_queued > 0 &&
	    (cold->event_accounting[p->agent_event_head] &
	     AGENT_EVENT_ACCOUNT_RESERVED) != 0)
		return 0;
	if (p->agent_wait_cancel_pending != 0) {
		if (p->agent_wait_cancel_pending == AGENT_WAIT_CANCEL_RESERVED)
			return 0;
		if (p->agent_wait_cancel_pending != AGENT_WAIT_CANCEL_PENDING ||
		    p->agent_wait_cancel_event_id == 0)
			panic("Agent wait cancel reservation state");
		reservation->span_owner = p->agent_wait_cancel_span_owner;
		reservation->source_control = p->agent_wait_cancel_source_control;
		reservation->audit_principal =
			p->agent_wait_cancel_audit_principal;
		reservation->provenance_labels =
			AGENT_PROVENANCE_AGENT_DERIVED |
			AGENT_PROVENANCE_CROSS_AGENT_DATA;
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
		p->agent_wait_cancel_pending = AGENT_WAIT_CANCEL_RESERVED;
		reservation->slot = AGENT_WAIT_CANCEL_SLOT;
		reservation->cookie = event->event_id;
		if (owns_baton && !agent_ipc_event_baton_release_locked(p, t))
			panic("Agent cancel baton release");
		return 1;
	}
	if (p->agent_event_count_queued <= 0)
		return 0;
	slot = p->agent_event_head;
	if (cold->events[slot].event_id == 0)
		panic("Agent event reservation state");
	*event = cold->events[slot];
	reservation->span_owner = cold->event_span_owner[slot];
	reservation->source_control = cold->event_source_control[slot];
	reservation->audit_principal = cold->event_audit_principal[slot];
	reservation->provenance_labels = AGENT_CONTEXT_PROVENANCE_DECODE(
		cold->event_accounting[slot]);
	cold->event_accounting[slot] |= AGENT_EVENT_ACCOUNT_RESERVED;
	reservation->slot = slot;
	reservation->cookie = event->event_id;
	if (owns_baton && !agent_ipc_event_baton_release_locked(p, t))
		panic("Agent event baton release");
	return 1;
}

static void
agent_ipc_event_account_refund(int *count, uint64 accounting, uint64 kind)
{
	if ((accounting & kind) == 0)
		return;
	if (*count <= 0)
		panic("Agent event accounting");
	(*count)--;
}

static void
agent_ipc_wait_finish(struct proc *p,
		      struct agent_ipc_wait_reservation *reservation, int commit)
{
	struct agent_ipc_observe_cold_state *cold = p->agent_ipc_observe_cold;
	uint64 accounting;
	int enabled = intr_save();
	int slot = reservation->slot;

	if (slot == AGENT_WAIT_CANCEL_SLOT) {
		if (p->agent_wait_cancel_pending != AGENT_WAIT_CANCEL_RESERVED ||
		    p->agent_wait_cancel_event_id != reservation->cookie)
			panic("Agent wait cancel reservation changed");
	} else {
		if (slot < 0 || slot >= AGENT_EVENT_QUEUE_CAP ||
		    p->agent_event_count_queued <= 0 ||
		    p->agent_event_head != slot ||
		    cold->events[slot].event_id != reservation->cookie ||
		    (cold->event_accounting[slot] &
		     AGENT_EVENT_ACCOUNT_RESERVED) == 0)
			panic("Agent event reservation changed");
	}
	if (!commit) {
		if (slot == AGENT_WAIT_CANCEL_SLOT)
			p->agent_wait_cancel_pending = AGENT_WAIT_CANCEL_PENDING;
		else
			cold->event_accounting[slot] &=
				~AGENT_EVENT_ACCOUNT_RESERVED;
		agent_ipc_handoff_event_locked(p);
		goto out;
	}
	if (slot == AGENT_WAIT_CANCEL_SLOT) {
		p->agent_wait_cancel_pending = 0;
		p->agent_wait_cancel_span_id = 0;
		p->agent_wait_cancel_span_owner = 0;
		p->agent_wait_cancel_source_control = 0;
		p->agent_wait_cancel_audit_principal = 0;
	} else {
		accounting = cold->event_accounting[slot] &
			     ~AGENT_EVENT_ACCOUNT_RESERVED;
		memset(&cold->events[slot], 0, sizeof(cold->events[slot]));
		cold->event_source_control[slot] = 0;
		cold->event_span_owner[slot] = 0;
		cold->event_audit_principal[slot] = 0;
		cold->event_accounting[slot] = 0;
		p->agent_event_head = (slot + 1) % AGENT_EVENT_QUEUE_CAP;
		p->agent_event_count_queued--;
		agent_ipc_event_account_refund(
			&p->agent_external_event_count_queued, accounting,
			AGENT_EVENT_ACCOUNT_EXTERNAL);
		agent_ipc_event_account_refund(&p->agent_ipc_count_queued,
			accounting, AGENT_EVENT_ACCOUNT_IPC);
		agent_ipc_event_account_refund(
			&p->agent_attributed_event_count_queued, accounting,
			AGENT_EVENT_ACCOUNT_ATTRIBUTED);
	}
	if (p->agent_wait_cancel_pending == AGENT_WAIT_CANCEL_PENDING ||
	    p->agent_event_count_queued > 0)
		agent_ipc_handoff_event_locked(p);
out:
	intr_restore(enabled);
}

enum agent_event_origin {
	AGENT_EVENT_ORIGIN_KERNEL = 1,
	AGENT_EVENT_ORIGIN_DIRECTED,
	AGENT_EVENT_ORIGIN_ATTRIBUTED,
};

enum agent_event_delivery {
	AGENT_EVENT_REQUIRE_WATCH = 1,
	AGENT_EVENT_INTRINSIC_COALESCED,
	AGENT_EVENT_LIVE_QUERY_TARGETED,
};

#define AGENT_EVENT_SET(type) AGENT_EVENT_MASK(type)
#define AGENT_EVENT_ATTRIBUTED_SET \
	(AGENT_EVENT_SET(AGENT_EVENT_FILE_STATUS) | \
	 AGENT_EVENT_SET(AGENT_EVENT_FILE_QUERY) | \
	 AGENT_EVENT_SET(AGENT_EVENT_JOB_DONE) | \
	 AGENT_EVENT_SET(AGENT_EVENT_POLICY_DENIED) | \
	 AGENT_EVENT_SET(AGENT_EVENT_CONTEXT_LIMIT) | \
	 AGENT_EVENT_SET(AGENT_EVENT_DASHBOARD_EXPORT))

struct agent_ipc_origin_policy {
	uint64 events;
	uint64 accounting;
};

static const struct agent_ipc_origin_policy agent_ipc_origin_policy[] = {
	[AGENT_EVENT_ORIGIN_KERNEL] = {
		AGENT_EVENT_ATTRIBUTED_SET | AGENT_EVENT_SET(AGENT_EVENT_TIMER) |
			AGENT_EVENT_SET(AGENT_EVENT_CANCELLED), 0 },
	[AGENT_EVENT_ORIGIN_DIRECTED] = {
		AGENT_EVENT_SET(AGENT_EVENT_MESSAGE) |
			AGENT_EVENT_SET(AGENT_EVENT_LLM_DONE),
		AGENT_EVENT_ACCOUNT_EXTERNAL | AGENT_EVENT_ACCOUNT_IPC },
	[AGENT_EVENT_ORIGIN_ATTRIBUTED] = { AGENT_EVENT_ATTRIBUTED_SET,
		AGENT_EVENT_ACCOUNT_EXTERNAL | AGENT_EVENT_ACCOUNT_ATTRIBUTED },
};

static int agent_ipc_queued_event_count(struct proc *target, int type,
					uint64 source_control_id)
{
	struct agent_ipc_observe_cold_state *cold =
		target->agent_ipc_observe_cold;
	int count = 0;
	int slot = target->agent_event_head;

	for (int i = 0; i < target->agent_event_count_queued; i++) {
		if ((source_control_id != 0 &&
		     cold->event_source_control[slot] == source_control_id) ||
		    (source_control_id == 0 &&
		     cold->events[slot].type == type &&
		     (cold->event_accounting[slot] &
		      AGENT_EVENT_ACCOUNT_COALESCED) != 0))
			count++;
		slot = (slot + 1) % AGENT_EVENT_QUEUE_CAP;
	}
	return count;
}

static int
agent_ipc_event_id_alloc(uint64 *event_id)
{
	if (event_id == 0)
		return -1;
	*event_id = agent_observe_alloc_event_id();
	return *event_id == 0 ? -1 : 0;
}

static int agent_ipc_queue_event_locked(struct proc *target, struct proc *source,
				    int origin, int type, uint64 corr_id,
				    uint64 cause_sequence, char *payload,
				    int delivery)
{
	struct agent_ipc_observe_cold_state *cold;
	struct agent_event *event;
	uint64 accounting = 0;
	uint64 event_id;
	uint64 source_control_id = 0;
	uint64 span_id = 0;
	uint64 span_owner = 0;
	int source_pid = 0;
	int slot;

	if (!proc_teardown_live(target) || !target->is_agent)
		return 0;
	cold = target->agent_ipc_observe_cold;
	if (cold == 0)
		panic("Agent event cold state");
	if (origin == AGENT_EVENT_ORIGIN_DIRECTED ||
	    origin == AGENT_EVENT_ORIGIN_ATTRIBUTED) {
		if (!proc_teardown_live(source) || !source->is_agent ||
		    source->agent_control_id == 0 ||
		    !agent_ipc_same_scope(source, target))
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
	if (delivery != AGENT_EVENT_REQUIRE_WATCH &&
	    delivery != AGENT_EVENT_INTRINSIC_COALESCED &&
	    delivery != AGENT_EVENT_LIVE_QUERY_TARGETED)
		return 0;
	if (delivery == AGENT_EVENT_INTRINSIC_COALESCED &&
	    origin != AGENT_EVENT_ORIGIN_KERNEL)
		return 0;
	if (delivery == AGENT_EVENT_LIVE_QUERY_TARGETED &&
	    type != AGENT_EVENT_FILE_STATUS && type != AGENT_EVENT_FILE_QUERY)
		return 0;
	if (delivery == AGENT_EVENT_REQUIRE_WATCH &&
	    !agent_ipc_filter_matches(target, type, payload))
		return 0;
	if (type <= AGENT_EVENT_NONE || type > AGENT_EVENT_MAX ||
	    (agent_ipc_origin_policy[origin].events & AGENT_EVENT_MASK(type)) == 0)
		return 0;
	if (origin == AGENT_EVENT_ORIGIN_DIRECTED &&
	    !agent_ipc_route_allows(source, target, type))
		return 0;
	if (delivery == AGENT_EVENT_INTRINSIC_COALESCED &&
	    agent_ipc_queued_event_count(target, type, 0) != 0)
		return 0;
	accounting = agent_ipc_origin_policy[origin].accounting;
	if (delivery == AGENT_EVENT_INTRINSIC_COALESCED)
		accounting = AGENT_EVENT_ACCOUNT_COALESCED;
	accounting |= AGENT_CONTEXT_PROVENANCE_ENCODE(
		agent_provenance_ipc_output_labels(
			source, origin == AGENT_EVENT_ORIGIN_KERNEL));
	if (target->agent_event_count_queued >= AGENT_EVENT_QUEUE_CAP ||
	    ((accounting & AGENT_EVENT_ACCOUNT_EXTERNAL) != 0 &&
	    (target->agent_external_event_count_queued >=
		     AGENT_EVENT_EXTERNAL_LIMIT ||
	     agent_ipc_queued_event_count(target, AGENT_EVENT_NONE,
					  source_control_id) >=
		     AGENT_EVENT_SOURCE_LIMIT ||
	     ((accounting & AGENT_EVENT_ACCOUNT_IPC) != 0 &&
	      target->agent_ipc_count_queued >= AGENT_EVENT_IPC_LIMIT) ||
	     ((accounting & AGENT_EVENT_ACCOUNT_ATTRIBUTED) != 0 &&
	      target->agent_attributed_event_count_queued >=
		      AGENT_EVENT_ATTRIBUTED_LIMIT))) ||
	    agent_ipc_event_id_alloc(&event_id) < 0) {
		target->agent_event_dropped++;
		return -1;
	}
	slot = target->agent_event_tail;
	event = &cold->events[slot];
	memset(event, 0, sizeof(*event));
	event->type = type;
	event->source_pid = source_pid;
	event->target_pid = target->pid;
	event->status = AGENT_STATUS_OK;
	event->event_id = event_id;
	event->tick = agent_ipc_ticks();
	event->corr_id = corr_id;
	event->cause_sequence = cause_sequence;
	event->span_id = span_id;
	safestrcpy(event->payload, payload, sizeof(event->payload));
	cold->event_source_control[slot] = source_control_id;
	cold->event_span_owner[slot] = span_owner;
	cold->event_audit_principal[slot] = source_control_id != 0 ?
					     source_control_id :
					     target->agent_control_id;
	cold->event_accounting[slot] = accounting;
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
		cold->event_audit_principal[slot]);
	agent_ipc_handoff_event_locked(target);
	return 1;
}

static int agent_ipc_queue_event(struct proc *target, struct proc *source,
			     int origin, int type, uint64 corr_id,
			     uint64 cause_sequence, char *payload)
{
	int enabled = intr_save();
	int delivered = agent_ipc_queue_event_locked(target, source, origin, type,
						 corr_id, cause_sequence, payload,
						 AGENT_EVENT_REQUIRE_WATCH);

	intr_restore(enabled);
	return delivered;
}

int
agent_ipc_deliver_live_event(struct proc *target, struct proc *source,
			     int type, uint64 fid, uint64 generation,
			     char *payload, int coalesced)
{
	int enabled;
	int origin;
	int result;

	if (target == 0 || payload == 0 ||
	    (type != AGENT_EVENT_FILE_STATUS &&
	     type != AGENT_EVENT_FILE_QUERY))
		return 0;
	origin = source == 0 ? AGENT_EVENT_ORIGIN_KERNEL :
			      AGENT_EVENT_ORIGIN_ATTRIBUTED;
	enabled = intr_save();
	result = agent_ipc_queue_event_locked(
		target, source, origin, type, fid, generation, payload,
		coalesced ? AGENT_EVENT_INTRINSIC_COALESCED :
			    AGENT_EVENT_LIVE_QUERY_TARGETED);
	intr_restore(enabled);
	return result;
}

static int
agent_ipc_queue_intrinsic_timer_locked(struct proc *p, uint64 corr_id,
				       char *payload)
{
	return agent_ipc_queue_event_locked(
		p, 0, AGENT_EVENT_ORIGIN_KERNEL, AGENT_EVENT_TIMER, corr_id,
		p->context_path_latest, payload, AGENT_EVENT_INTRINSIC_COALESCED);
}

static int agent_ipc_queue_heartbeat_if_due(struct proc *p, uint64 now)
{
	int enabled = intr_save();
	int due = p->heartbeat_interval > 0 &&
		  now - p->agent_last_heartbeat_tick >=
			  (uint64)p->heartbeat_interval;

	if (due) {
		p->agent_last_heartbeat_tick = now;
		(void)agent_ipc_queue_intrinsic_timer_locked(
			p, now, "timer=heartbeat");
	}
	intr_restore(enabled);
	return due;
}

int agent_ipc_deliver_pid(int pid, struct proc *source, int type,
			  uint64 corr_id, uint64 cause_sequence,
			  char *payload, int mirror_mailbox,
			  int *delivered)
{
	struct proc *target;
	int enabled;
	int queued;
	int status;

	if (delivered)
		*delivered = 0;
	if (pid <= 0 || source == 0 || type <= AGENT_EVENT_NONE ||
	    type > AGENT_EVENT_MAX)
		return AGENT_STATUS_BAD_PARAM;
	if ((AGENT_EVENT_MASK(type) & AGENT_IPC_EVENT_MASK) == 0)
		return AGENT_STATUS_BAD_PARAM;
	enabled = intr_save();
	if (!proc_teardown_live(source) || !source->is_agent ||
	    source->agent_control_id == 0 ||
	    (target = agent_ipc_find_live_proc_locked(pid, 1)) == 0) {
		status = AGENT_STATUS_NOT_FOUND;
		goto out;
	}
	if (!agent_ipc_route_allows(source, target, type)) {
		status = AGENT_STATUS_DENIED;
		goto out;
	}
	queued = agent_ipc_queue_event_locked(target, source,
					  AGENT_EVENT_ORIGIN_DIRECTED, type, corr_id,
					  cause_sequence, payload,
					  AGENT_EVENT_REQUIRE_WATCH);
	status = queued < 0 ? AGENT_STATUS_NO_SPACE :
		 queued == 0 ? AGENT_STATUS_NOT_FOUND : AGENT_STATUS_OK;
	if (status != AGENT_STATUS_OK)
		goto out;
	if (type == AGENT_EVENT_MESSAGE && mirror_mailbox) {
		target->agent_mailbox_valid = 1;
		target->agent_mailbox_from = source->pid;
		safestrcpy(target->agent_mailbox, payload,
			   sizeof(target->agent_mailbox));
		agent_provenance_mailbox_publish(
			target, agent_provenance_ipc_output_labels(source, 0));
	}
	if (delivered)
		*delivered = 1;
out:
	intr_restore(enabled);
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
		    !agent_ipc_same_scope(p, source))
			continue;
		rc = agent_ipc_queue_event(p, source, AGENT_EVENT_ORIGIN_ATTRIBUTED,
				       type, corr_id, cause_sequence, payload);
		if (rc > 0)
			delivered += rc;
	}
	return delivered;
}

static void
agent_ipc_wait_state_locked(struct thread *t, int state)
{
	agent_ipc_thread_state_clear_locked(t, state);
	agent_identity_loop_refresh_locked(t->process);
}

void
agent_ipc_proc_prepare(struct proc *p)
{
	int enabled;

	if (p == 0)
		return;
	enabled = intr_save();
	agent_ipc_event_baton_clear_locked(p);
	intr_restore(enabled);
}

void
agent_ipc_proc_reset(struct proc *p)
{
	int inactive;
	int enabled;

	if (p == 0)
		return;
	agent_live_query_proc_reset(p);
	inactive = !p->is_agent && p->agent_control_id == 0;
	/* 仅在激活 Agent 角色时初始化事件面。 */
	if (!inactive) {
		memset(&p->agent_mailbox_valid, 0, AGENT_IPC_PROC_STATE_BYTES);
		if (p->agent_ipc_observe_cold != 0)
			memset(p->agent_ipc_observe_cold, 0,
			       sizeof(*p->agent_ipc_observe_cold));
	}
	p->heartbeat_interval = 0;
	enabled = intr_save();
	agent_ipc_event_baton_clear_locked(p);
	for (int tid = 0; tid < NTHREAD; tid++)
		agent_ipc_thread_state_clear_locked(&p->threads[tid], AGENT_LOOP_NONE);
	agent_identity_loop_refresh_locked(p);
	intr_restore(enabled);
}

void
agent_ipc_proc_activate(struct proc *p)
{
	if (p == 0)
		return;
	agent_ipc_proc_reset(p);
	for (int tid = 0; tid < NTHREAD; tid++)
		if (p->threads[tid].state != T_UNUSED &&
		    p->threads[tid].identity_generation != 0)
			agent_ipc_thread_runtime_transition(
				&p->threads[tid], AGENT_THREAD_RUNTIME_ACTIVATE);
	(void)agent_ipc_heartbeat_configure(p, 0, 0);
}

void
agent_ipc_proc_teardown(struct proc *p)
{
	int enabled;

	if (p == 0)
		return;
	agent_ipc_remove_source(p->agent_control_id);
	enabled = intr_save();
	/* 撤销必须广播；普通事件只走单接收者传棒。 */
	agent_ipc_broadcast_event_teardown_locked(p);
	agent_ipc_proc_reset(p);
	intr_restore(enabled);
}

void
agent_ipc_exec_public(struct proc *p)
{
	if (p == 0)
		return;
	agent_ipc_remove_source(p->agent_control_id);
	agent_ipc_proc_reset(p);
}

static void
agent_ipc_sched_snapshot_take(struct proc *p, struct thread *t,
			      struct agent_ipc_sched_snapshot *snapshot)
{
	uint64 earliest = 0;
	int enabled;

	if (snapshot == 0)
		return;
	memset(snapshot, 0, sizeof(*snapshot));
	if (p == 0)
		return;
	enabled = intr_save();
	snapshot->event_count_queued = p->agent_event_count_queued;
	snapshot->last_heartbeat_tick = p->agent_last_heartbeat_tick;
	snapshot->heartbeat_interval = p->heartbeat_interval;
	if (t != 0) {
		snapshot->loop_state = t->agent_loop_state;
		if (t->agent_wait_deadline_valid && t->identity_generation != 0) {
			snapshot->wait_deadline = t->agent_wait_deadline;
			snapshot->wait_deadline_valid = 1;
		}
		goto out;
	}
	snapshot->loop_state = p->loop_state;
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *waiter = &p->threads[tid];

		if (!waiter->agent_wait_deadline_valid ||
		    waiter->identity_generation == 0)
			continue;
		if (earliest == 0 || waiter->agent_wait_deadline < earliest)
			earliest = waiter->agent_wait_deadline;
	}
	snapshot->wait_deadline = earliest;
	snapshot->wait_deadline_valid = earliest != 0;
out:
	intr_restore(enabled);
}

void
agent_ipc_thread_sched_snapshot(struct thread *t,
				struct agent_ipc_sched_snapshot *snapshot)
{
	agent_ipc_sched_snapshot_take(t == 0 ? 0 : t->process, t, snapshot);
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
		(void)agent_provenance_mailbox_take(p);
	}
	intr_restore(enabled);
	return available;
}

int
agent_ipc_heartbeat_configure(struct proc *p, uint64 interval, uint64 *tick_out)
{
	uint64 now;
	int enabled;

	if (p == 0 || interval > AGENT_HEARTBEAT_MAX_TICKS)
		return AGENT_STATUS_BAD_PARAM;
	enabled = intr_save();
	now = agent_ipc_ticks();
	p->heartbeat_interval = (int)interval;
	p->agent_last_heartbeat_tick = now;
	intr_restore(enabled);
	if (tick_out != 0)
		*tick_out = now;
	return AGENT_STATUS_OK;
}

void
agent_ipc_tick_proc(struct proc *p, uint64 now)
{
	int woken;

	if (p == 0 || p->state == P_UNUSED || !p->is_agent ||
	    !proc_teardown_live(p))
		return;
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *t = &p->threads[tid];

		if (!t->agent_wait_deadline_valid ||
		    now < t->agent_wait_deadline || t->identity_generation == 0 ||
		    t->state != SLEEPING ||
		    t->wait_channel != &p->agent_event_waiters ||
		    t->wait_reason != WAIT_REASON_EVENT ||
		    t->wait_key != t->identity_generation)
			continue;
		woken = wait_queue_wake_key_all(&p->agent_event_waiters,
					 t->identity_generation);
		if (woken != 1)
			panic("Agent deadline keyed wake");
		p->agent_wait_wakeup_count += woken;
	}
	(void)agent_ipc_queue_heartbeat_if_due(p, now);
}


/* IPC 系统调用。 */

#ifdef WAIT_ATOMIC_TEST_PROFILE
int agent_ipc_wait_test_publish(struct proc *p) { return agent_ipc_queue_intrinsic_timer_locked(p, 0, "wait=atomic-injected"); }
#endif

static int
agent_ipc_live_watch_update(uint64 watchaddr, int remove)
{
	struct proc *p = curr_proc();
	uint64 watch_id = 0;
	int status;

	if (p == 0 || !p->is_agent || watchaddr == 0)
		return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_WATCH))
		return AGENT_STATUS_DENIED;
	if (!remove && user_range_check(
			       p->pagetable, watchaddr,
			       sizeof(agent_ipc_live_watch_scratch), PTE_W) < 0)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	if (!agent_metadata_txn_lock(1)) {
		status = AGENT_STATUS_RETRY;
		goto out_lane;
	}
	memset(&agent_ipc_live_watch_scratch, 0,
	       sizeof(agent_ipc_live_watch_scratch));
	if (copyin(p->pagetable, (char *)&agent_ipc_live_watch_scratch,
		   watchaddr, sizeof(agent_ipc_live_watch_scratch)) < 0) {
		status = -1;
		goto out_txn;
	}
	status = remove ? agent_live_query_watch_remove_typed(
				  p, &agent_ipc_live_watch_scratch) :
			  agent_live_query_watch_install_typed(
				  p, &agent_ipc_live_watch_scratch);
	watch_id = agent_ipc_live_watch_scratch.watch_id;
	if (!remove && status == AGENT_STATUS_OK &&
	    copyout(p->pagetable, watchaddr,
		    (char *)&agent_ipc_live_watch_scratch,
		    sizeof(agent_ipc_live_watch_scratch)) < 0) {
		(void)agent_live_query_watch_remove_typed(
			p, &agent_ipc_live_watch_scratch);
		status = -1;
	}
out_txn:
	memset(&agent_ipc_live_watch_scratch, 0,
	       sizeof(agent_ipc_live_watch_scratch));
	agent_metadata_txn_unlock();
	if (status == AGENT_STATUS_OK)
		agent_context_append_system(
			p, AGENT_TOOL_AGENT_WATCH, watch_id,
			AGENT_EVENT_FILE_QUERY, "live_query",
			remove ? "unwatch" : "watch", AGENT_STATUS_OK,
			watch_id, 0, 0);
out_lane:
	agent_lifecycle_context_lane_leave(p);
	return status;
}

static int agent_ipc_watch_update(int event_type, uint64 filteraddr, int remove)
{
	struct proc *p = curr_proc();
	char filter[AGENT_WATCH_FILTER_SIZE] = {0}; int result = 0;

	if (!p->is_agent) return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_WATCH)) return AGENT_STATUS_DENIED;
	if (event_type < AGENT_EVENT_NONE || event_type > AGENT_EVENT_MAX) return AGENT_STATUS_BAD_PARAM;
	if (event_type == AGENT_EVENT_FILE_QUERY)
		return agent_ipc_live_watch_update(filteraddr, remove);
	if (filteraddr != 0 && copyinstr(p->pagetable, filter, filteraddr, sizeof(filter)) < 0) return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0) return -1;
	if (!remove && agent_ipc_watch_set(p, event_type, filter) < 0) { result = AGENT_STATUS_NO_SPACE; goto out; }
	if (remove) {
		result = agent_ipc_watch_clear(p, event_type, filter);
		agent_live_query_watch_removed(p);
	} else {
		agent_identity_thread_loop_set(curr_thread(), AGENT_LOOP_IDLE);
		agent_live_query_watch_installed(p);
	}
	agent_context_append_system(p, AGENT_TOOL_AGENT_WATCH, 0, event_type, filter,
				    remove ? "unwatch" : "watch", AGENT_STATUS_OK,
				    remove ? result : event_type, 0, 0);
out:
	agent_lifecycle_context_lane_leave(p);
	return result;
}

int sys_agent_watch(int event_type, uint64 filteraddr) { return agent_ipc_watch_update(event_type, filteraddr, 0); }

int sys_agent_unwatch(int event_type, uint64 filteraddr) { return agent_ipc_watch_update(event_type, filteraddr, 1); }

int sys_agent_wait(uint64 eventaddr, int timeout_ticks)
{
	struct proc *p = curr_proc();
	struct thread *t = curr_thread();
	struct agent_event event;
	struct agent_ipc_wait_reservation reservation = {0};
	uint64 start = agent_ipc_ticks();
	uint64 now;
	int enabled;
	int owns_baton;
	int wait_status;
	int status;

	if (!p->is_agent || t == 0 || t->process != p ||
	    t->identity_generation == 0)
		return -1;
	if (eventaddr &&
	    user_range_check(p->pagetable, eventaddr, sizeof(event), PTE_W) < 0)
		return -1;
	memset(&event, 0, sizeof(event));
	enabled = intr_save();
	p->agent_wait_count++;
	agent_ipc_wait_state_locked(t, AGENT_LOOP_RUNNING);
	intr_restore(enabled);
	for (;;) {
#ifdef WAIT_ATOMIC_TEST_PROFILE
		wait_status = wait_atomic_test_agent_wait(p);
		if (wait_status < 0) {
			status = -1;
			break;
		}
#endif
		/* 谓词复查与等待者发布必须处在同一个关中断窗口。 */
		enabled = intr_save();
recheck_locked:
		p->agent_wait_loop_count++;
		owns_baton = agent_ipc_event_baton_owned_locked(p, t);
		status = AGENT_STATUS_NOT_FOUND;
		if (agent_ipc_wait_reserve_locked(p, t, &event, &reservation))
			status = reservation.slot == AGENT_WAIT_CANCEL_SLOT ?
				 AGENT_STATUS_CANCELLED : AGENT_STATUS_OK;
		if (owns_baton && status == AGENT_STATUS_NOT_FOUND) {
			if (!agent_ipc_event_baton_release_locked(p, t))
				panic("Agent event recheck baton");
			agent_ipc_handoff_event_locked(p);
		}
		if (status != AGENT_STATUS_NOT_FOUND) {
			agent_ipc_wait_state_locked(t, AGENT_LOOP_RUNNING);
			intr_restore(enabled);
			break;
		}
		now = agent_ipc_ticks();
		if (agent_ipc_queue_heartbeat_if_due(p, now)) {
			intr_restore(enabled);
			continue;
		}
		if (timeout_ticks >= 0 && now - start >= (uint64)timeout_ticks) {
			p->agent_timeout_count++;
			agent_ipc_wait_state_locked(t, AGENT_LOOP_RUNNING);
			event.type = AGENT_EVENT_TIMER;
			event.target_pid = p->pid;
			event.status = AGENT_STATUS_TIMEOUT;
			event.tick = now;
			safestrcpy(event.payload, "timeout",
				   sizeof(event.payload));
			status = AGENT_STATUS_TIMEOUT;
			intr_restore(enabled);
			break;
		}
		t->agent_loop_state = AGENT_LOOP_WAITING;
		agent_identity_loop_refresh_locked(p);
		if (timeout_ticks >= 0) {
			t->agent_wait_deadline_valid = 1;
			t->agent_wait_deadline = start + timeout_ticks;
		} else {
			t->agent_wait_deadline_valid = 0;
			t->agent_wait_deadline = 0;
		}
		p->agent_wait_sleep_count++;
		wait_status = wait_queue_sleep_key_irq(
			&p->agent_event_waiters, t->identity_generation);
		if (wait_status == WAIT_QUEUE_OK)
			goto recheck_locked;
		if (wait_status != WAIT_QUEUE_OK) {
			if (agent_ipc_event_baton_release_locked(p, t) &&
			    proc_teardown_live(p))
				agent_ipc_handoff_event_locked(p);
			agent_ipc_wait_state_locked(t, AGENT_LOOP_RUNNING);
			status = -1;
			intr_restore(enabled);
			break;
		}
	}
	enabled = intr_save();
	agent_ipc_wait_state_locked(t,
		(status == AGENT_STATUS_OK || status == AGENT_STATUS_CANCELLED) ?
			AGENT_LOOP_RUNNING : AGENT_LOOP_IDLE);
	intr_restore(enabled);
	if (status == AGENT_STATUS_OK || status == AGENT_STATUS_CANCELLED) {
		/*
		 * Provenance is monotonic and must become visible before payload
		 * bytes do.  A sibling thread may observe the shared destination as
		 * soon as copyout starts, so merging after copyout would briefly let
		 * cross-Agent data retain the caller's previous (cleaner) labels.
		 * A failed copyout deliberately keeps the conservative over-taint;
		 * the event reservation itself is still rolled back for retry.
		 */
		if (agent_lifecycle_context_lane_enter(p) < 0) {
			agent_ipc_wait_finish(p, &reservation, 0);
			status = -1;
			goto out;
		}
		if (agent_provenance_merge_current(
			    p, reservation.provenance_labels) != AGENT_STATUS_OK) {
			agent_ipc_wait_finish(p, &reservation, 0);
			agent_lifecycle_context_lane_leave(p);
			status = -1;
			goto out;
		}
		if (eventaddr &&
		    copyout(p->pagetable, eventaddr, (char *)&event,
			    sizeof(event)) < 0) {
			agent_ipc_wait_finish(p, &reservation, 0);
			agent_lifecycle_context_lane_leave(p);
			status = -1;
			goto out;
		}
		if (event.span_id != 0 && reservation.span_owner != 0) {
			p->agent_current_span_id = event.span_id;
			p->agent_current_span_owner = reservation.span_owner;
		}
		p->agent_current_cause_sequence = event.cause_sequence;
		p->agent_current_cause_pid = event.source_pid > 0 ?
						 event.source_pid : p->pid;
		p->agent_current_cause_control = reservation.source_control != 0 ?
						     reservation.source_control :
						     p->agent_control_id;
		agent_observe_record_event(
			AGENT_AUDIT_KIND_EVENT_CONSUME, p, &event,
			reservation.span_owner, reservation.audit_principal);
		agent_context_append_system_causal(
			p, AGENT_TOOL_AGENT_WAIT, event.event_id, event.type,
			event.payload,
			status == AGENT_STATUS_CANCELLED ? "cancelled" : "event",
			status, event.type, event.source_pid, event.corr_id);
		agent_ipc_wait_finish(p, &reservation, 1);
		agent_lifecycle_context_lane_leave(p);
	} else if (eventaddr &&
		   copyout(p->pagetable, eventaddr, (char *)&event,
			   sizeof(event)) < 0) {
		status = -1;
	}
out:
	enabled = intr_save();
	agent_ipc_wait_state_locked(t,
		p->is_agent && proc_teardown_live(p) ?
			AGENT_LOOP_IDLE : AGENT_LOOP_NONE);
	intr_restore(enabled);
	return status;
}

int sys_agent_wait_cancel(int pid, uint64 reasonaddr)
{
	struct proc *p = curr_proc();
	struct proc *target;
	char reason[AGENT_EVENT_PAYLOAD_SIZE] = {0};
	uint64 event_id;
	int enabled;
	int status = AGENT_STATUS_NOT_FOUND;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_has_cap(p, AGENT_CAP_WAIT_CANCEL))
		return AGENT_STATUS_DENIED;
	if (reasonaddr != 0 &&
	    copyinstr(p->pagetable, reason, reasonaddr, sizeof(reason)) < 0)
		return -1;
	if (reason[0] == 0)
		safestrcpy(reason, "cancel", sizeof(reason));
	enabled = intr_save();
	target = agent_ipc_find_live_proc_locked(pid, 1);
	if (target != 0) {
		if (!agent_identity_controls_target(p, target))
			status = AGENT_STATUS_DENIED;
		else if (target->agent_wait_cancel_pending)
			status = AGENT_STATUS_DUPLICATE;
		else if (agent_ipc_event_id_alloc(&event_id) < 0)
			status = AGENT_STATUS_NO_SPACE;
		else {
			target->agent_wait_cancel_pending =
				AGENT_WAIT_CANCEL_PENDING;
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
			agent_ipc_handoff_event_locked(target);
			status = AGENT_STATUS_OK;
		}
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
	    (event_mask & ~AGENT_IPC_ROUTE_MASK) != 0 ||
	    (operation != AGENT_IPC_ROUTE_GRANT &&
	     operation != AGENT_IPC_ROUTE_REVOKE))
		return AGENT_STATUS_BAD_PARAM;
	enabled = intr_save();
	source = agent_ipc_find_live_proc_locked(source_pid, 1);
	target = agent_ipc_find_live_proc_locked(target_pid, 1);
	if (source == 0 || target == 0)
		status = AGENT_STATUS_NOT_FOUND;
	else if (!agent_ipc_same_scope(source, target))
		status = AGENT_STATUS_DENIED;
	else if ((p != target &&
		  (!agent_identity_has_cap(p, AGENT_CAP_ROUTE_MANAGE) ||
		   !agent_identity_controls_or_self(p, source) ||
		   !agent_identity_controls_or_self(p, target))) ||
		 (p == target && !agent_identity_has_cap(p, AGENT_CAP_WATCH)))
		status = AGENT_STATUS_DENIED;
	else if (source == target)
		status = AGENT_STATUS_OK;
	else
		status = agent_ipc_route_update(target, source->agent_control_id,
						event_mask, operation);
	intr_restore(enabled);
	return status;
}

static int agent_ipc_heartbeat_syscall(uint64 interval_ticks, char *action)
{
	struct proc *p = curr_proc();
	uint64 now;
	int status;

	if (!p->is_agent)
		return -1;
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	status = agent_ipc_heartbeat_configure(p, interval_ticks, &now);
	if (status != AGENT_STATUS_OK) {
		agent_lifecycle_context_lane_leave(p);
		return status;
	}
	agent_context_append_system(p, AGENT_TOOL_AGENT_HEARTBEAT, 0,
				    interval_ticks, action, action,
				    AGENT_STATUS_OK, interval_ticks,
				    now, 0);
	agent_lifecycle_context_lane_leave(p);
	return 0;
}

int sys_agent_heartbeat(uint64 interval_ticks)
{
	return agent_ipc_heartbeat_syscall(interval_ticks, "heartbeat_legacy");
}

int sys_agent_heartbeat_set(uint64 interval_ticks)
{
	return agent_ipc_heartbeat_syscall(interval_ticks, "heartbeat_set");
}

int sys_agent_heartbeat_stop(void)
{
	return agent_ipc_heartbeat_syscall(0, "heartbeat_stop");
}

int sys_agent_wake(int pid, uint64 eventaddr)
{
	struct proc *p = curr_proc();
	struct agent_event event;
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
	status = agent_ipc_deliver_pid(pid, p, event.type, event.corr_id,
				   p->context_path_latest, event.payload, 0,
				   0);
	return status;
}
