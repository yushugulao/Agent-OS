#include "agent_lifecycle.h"
#include "agent_internal.h"
#include "defs.h"
#include "vfs_security.h"

static uint64 next_control_id;

#define AGENT_CONTEXT_LANE_OWNER_NONE (-1)

/*
 * Context commits are process-wide transactions. The lane is sleepable:
 * tool execution may block on metadata or I/O, while contenders sleep in the
 * ordinary FIFO wait queue instead of holding interrupts off. A zero depth
 * means ownership has been handed to a woken thread that has not resumed yet.
 */
void
agent_lifecycle_context_lane_init(struct proc *p)
{
	if (p == 0)
		return;
	wait_queue_init(&p->agent_context_lane_waiters,
			WAIT_REASON_AGENT_CONTEXT);
	p->agent_context_lane_owner_tid = AGENT_CONTEXT_LANE_OWNER_NONE;
	p->agent_context_lane_depth = 0;
}

static void
agent_lifecycle_context_lane_handoff_locked(struct proc *p)
{
	struct thread *next;

	p->agent_context_lane_depth = 0;
	if (!proc_teardown_live(p)) {
		p->agent_context_lane_owner_tid =
			AGENT_CONTEXT_LANE_OWNER_NONE;
		wait_queue_wake_all(&p->agent_context_lane_waiters);
		return;
	}
	next = p->agent_context_lane_waiters.head;
	if (next == 0) {
		p->agent_context_lane_owner_tid =
			AGENT_CONTEXT_LANE_OWNER_NONE;
		return;
	}
	if (next->process != p || next->state != SLEEPING ||
	    next->wait_channel != &p->agent_context_lane_waiters ||
	    next->tid < 0 || next->tid >= NTHREAD)
		panic("Agent context lane waiter");
	p->agent_context_lane_owner_tid = next->tid;
	if (wait_queue_wake_one(&p->agent_context_lane_waiters) != 1)
		panic("Agent context lane handoff");
}

int
agent_lifecycle_context_lane_enter(struct proc *p)
{
	struct thread *t = curr_thread();
	int enabled;
	int wait_status;

	if (p == 0 || t == 0 || t->process != p ||
	    t->tid < 0 || t->tid >= NTHREAD)
		return -1;
	for (;;) {
		enabled = intr_save();
		if (p->agent_context_lane_owner_tid == t->tid) {
			if (p->agent_context_lane_depth == 0) {
				if (!proc_teardown_live(p)) {
					agent_lifecycle_context_lane_handoff_locked(p);
					intr_restore(enabled);
					return -1;
				}
				p->agent_context_lane_depth = 1;
			} else {
				if (p->agent_context_lane_depth == ~0U)
					panic("Agent context lane depth");
				p->agent_context_lane_depth++;
			}
			intr_restore(enabled);
			return 0;
		}
		if (agent_metadata_txn_owned(0))
			panic("metadata acquired before Agent context lane");
		if (!proc_teardown_live(p)) {
			intr_restore(enabled);
			return -1;
		}
		if (p->agent_context_lane_owner_tid ==
		    AGENT_CONTEXT_LANE_OWNER_NONE) {
			p->agent_context_lane_owner_tid = t->tid;
			p->agent_context_lane_depth = 1;
			intr_restore(enabled);
			return 0;
		}
		/*
		 * Keep interrupts disabled from the ownership check through queue
		 * publication so an unlock cannot miss this waiter.
		 */
		wait_status = wait_queue_sleep_irq(
			&p->agent_context_lane_waiters);
		/*
		 * A direct handoff reserves owner_tid before the target resumes.
		 * Teardown may make wait_queue_sleep report INTERRUPTED even after
		 * that wake. Consume or cancel the reservation before returning.
		 * wait_queue_sleep_irq preserves our outer interrupt-off boundary,
		 * so the reservation cannot change during this decision.
		 */
		if (p->agent_context_lane_owner_tid == t->tid &&
		    p->agent_context_lane_depth == 0) {
			if (wait_status != WAIT_QUEUE_OK ||
			    !proc_teardown_live(p)) {
				agent_lifecycle_context_lane_handoff_locked(p);
				intr_restore(enabled);
				return -1;
			}
			p->agent_context_lane_depth = 1;
			intr_restore(enabled);
			return 0;
		}
		intr_restore(enabled);
		if (wait_status != WAIT_QUEUE_OK)
			return -1;
	}
}

void
agent_lifecycle_context_lane_leave(struct proc *p)
{
	struct thread *t = curr_thread();
	int enabled = intr_save();

	if (p == 0 || t == 0 || t->process != p ||
	    p->agent_context_lane_owner_tid != t->tid ||
	    p->agent_context_lane_depth == 0)
		panic("Agent context lane owner");
	if (p->agent_context_lane_depth == 1 &&
	    agent_metadata_txn_owned(0))
		panic("Agent context lane released with metadata");
	p->agent_context_lane_depth--;
	if (p->agent_context_lane_depth == 0)
		agent_lifecycle_context_lane_handoff_locked(p);
	intr_restore(enabled);
}

int
agent_lifecycle_context_lane_quiescent(struct proc *p)
{
	int enabled = intr_save();
	int quiescent = p != 0 &&
		p->agent_context_lane_owner_tid ==
			AGENT_CONTEXT_LANE_OWNER_NONE &&
		p->agent_context_lane_depth == 0 &&
		p->agent_context_lane_waiters.head == 0 &&
		p->agent_context_lane_waiters.tail == 0;

	intr_restore(enabled);
	return quiescent;
}

void
agent_lifecycle_init(void)
{
	next_control_id = 1;
}

uint64
agent_lifecycle_alloc_control_id(void)
{
	uint64 id = next_control_id;

	if (id == 0)
		return 0;
	if (id == ~0ULL)
		next_control_id = 0;
	else
		next_control_id = id + 1;
	return id;
}

void
agent_lifecycle_controller_departing(struct proc *p)
{
	struct workflow_lifecycle_key lifecycle;
	struct workflow_lifecycle_key closed;
	uint scope_id;
	uint64 control_id;

	if (p == 0 || !p->vfs_scope_controller || !p->is_agent ||
	    p->vfs_scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    p->agent_control_id == 0)
		return;
	scope_id = p->vfs_scope_id;
	control_id = p->agent_control_id;
	lifecycle = vfs_proc_lifecycle(p);
	/*
	 * This one-shot ownership attachment is only a close trigger.  The
	 * immutable lifecycle key remains authoritative for descendant teardown.
	 */
	p->vfs_scope_controller = 0;
	if (vfs_scope_close_owned(scope_id, lifecycle, control_id, &closed) == 0)
		proc_request_workflow_exit(closed, AGENT_STATUS_CANCELLED);
}
