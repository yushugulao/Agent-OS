#include "agent_internal.h"
#include "agent_identity_lease.h"
#include "defs.h"
#include "exec_policy.h"
#include "vfs_security.h"

#define AGENT_CAP_ALL \
	(AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ | \
	 AGENT_CAP_PROCESS_READ | AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH | \
	 AGENT_CAP_ACTION_WRITE | AGENT_CAP_ARTIFACT_WRITE | \
	 AGENT_CAP_AUDIT_WRITE | AGENT_CAP_META_WRITE | \
	 AGENT_CAP_ORCHESTRATE | AGENT_CAP_LLM_RELAY | \
	 AGENT_CAP_WAIT_CANCEL | AGENT_CAP_ROUTE_MANAGE)

static int next_agent_id;

extern struct proc pool[NPROC];

void
agent_identity_loop_refresh_locked(struct proc *p)
{
	int idle = 0;
	int waiting = 0;
	int running = 0;

	if (intr_get())
		panic("Agent loop refresh unlocked");
	if (p == 0 || !p->is_agent) {
		if (p != 0)
			p->loop_state = AGENT_LOOP_NONE;
		return;
	}
	for (int tid = 0; tid < NTHREAD; tid++) {
		struct thread *t = &p->threads[tid];

		if (t->state == T_UNUSED || t->identity_generation == 0)
			continue;
		if (t->agent_loop_state == AGENT_LOOP_RUNNING)
			running = 1;
		else if (t->agent_loop_state == AGENT_LOOP_WAITING ||
			 t->agent_timeline_wait_state != 0)
			waiting = 1;
		else if (t->agent_loop_state == AGENT_LOOP_IDLE)
			idle = 1;
	}
	p->loop_state = running ? AGENT_LOOP_RUNNING :
			waiting ? AGENT_LOOP_WAITING :
			idle ? AGENT_LOOP_IDLE : AGENT_LOOP_NONE;
}

void
agent_identity_thread_loop_set(struct thread *t, int loop_state)
{
	int enabled = intr_save();

	if (t == 0 || t->process == 0 || t->identity_generation == 0 ||
	    (loop_state != AGENT_LOOP_IDLE &&
	     loop_state != AGENT_LOOP_RUNNING &&
	     loop_state != AGENT_LOOP_WAITING)) {
		intr_restore(enabled);
		panic("Agent thread loop state");
	}
	t->agent_loop_state = loop_state;
	agent_identity_loop_refresh_locked(t->process);
	intr_restore(enabled);
}

static const struct agent_role_policy role_policies[] = {
	{ AGENT_ROLE_SENTINEL,
	  AGENT_CAP_META_READ | AGENT_CAP_PROCESS_READ |
		  AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		  AGENT_CAP_AUDIT_WRITE,
	  0, 70 },
	{ AGENT_ROLE_INVESTIGATOR,
	  AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		  AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		  AGENT_CAP_AUDIT_WRITE,
	  0, 90 },
	{ AGENT_ROLE_RECOVERY,
	  AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		  AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		  AGENT_CAP_ACTION_WRITE | AGENT_CAP_ARTIFACT_WRITE |
		  AGENT_CAP_AUDIT_WRITE,
	  0, 120 },
	{ AGENT_ROLE_ARTIFACT,
	  AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		  AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		  AGENT_CAP_ARTIFACT_WRITE | AGENT_CAP_AUDIT_WRITE,
	  0, 100 },
	{ AGENT_ROLE_ORCHESTRATOR, AGENT_CAP_ALL, AGENT_ROLE_GRANT_ALL, 110 },
};

void
agent_identity_init(void)
{
	next_agent_id = 1;
}

int
agent_identity_alloc_id(void)
{
	int enabled = intr_save();
	int id = next_agent_id;

	if (id <= 0) {
		intr_restore(enabled);
		return 0;
	}
	if (agent_identity_lease_allocator_contains(
		    AGENT_IDENTITY_ALLOCATOR_AGENT, (uint)id)) {
		if (id == 0x7fffffff) {
			next_agent_id = 0;
			intr_restore(enabled);
			return 0;
		}
		next_agent_id++;
		intr_restore(enabled);
		agent_identity_lease_allocator_note_next(
			AGENT_IDENTITY_ALLOCATOR_AGENT, (uint)next_agent_id);
		return id;
	}
	intr_restore(enabled);
	(void)agent_identity_lease_allocator_renew(
		AGENT_IDENTITY_ALLOCATOR_AGENT);
	return 0;
}

uint
agent_identity_next_id_get(void)
{
	return next_agent_id > 0 ? (uint)next_agent_id : 0;
}

void
agent_identity_id_floor(uint floor)
{
	if (floor == 0 || floor > 0x7fffffffU) {
		next_agent_id = 0;
		return;
	}
	if (next_agent_id > 0 && floor > (uint)next_agent_id)
		next_agent_id = floor;
}

const struct agent_role_policy *
agent_identity_role_policy(int role)
{
	for (uint i = 0; i < NELEM(role_policies); i++)
		if (role_policies[i].role == role)
			return &role_policies[i];
	return 0;
}

int
agent_identity_role_valid(int role)
{
	return agent_identity_role_policy(role) != 0;
}

int
agent_identity_role_sched_weight(int role)
{
	const struct agent_role_policy *policy =
		agent_identity_role_policy(role);

	return policy ? policy->sched_weight : 50;
}

void
agent_identity_authority_bootstrap(struct proc *p)
{
	uint64 grants = 0;

	if (p == 0)
		return;
	if (exec_policy_process_bootstrap(p))
		for (uint i = 0; i < NELEM(role_policies); i++)
			if (exec_policy_process_allows_role(
				    p, role_policies[i].role))
				grants |= AGENT_ROLE_GRANT_BIT(
					role_policies[i].role);
	p->agent_role_grant_mask = grants;
}

void
agent_identity_authority_on_exec(struct proc *p)
{
	if (p != 0 && !p->is_agent)
		p->agent_role_grant_mask = 0;
}

void
agent_identity_proc_reset(struct proc *p, int preserve_controller)
{
	uint64 controller = preserve_controller ? p->agent_controller_id : 0;

	p->is_agent = 0;
	p->agent_type = AGENT_TYPE_NONE;
	p->agent_id = 0;
	p->agent_role = 0;
	p->agent_control_id = 0;
	p->agent_controller_id = controller;
	p->agent_capability_mask = 0;
	p->agent_role_grant_mask = 0;
}

int
agent_identity_authority_check(struct proc *p, int role)
{
	if (!agent_identity_role_valid(role))
		return AGENT_STATUS_BAD_PARAM;
	if (p == 0 ||
	    (p->is_agent ?
	     (!exec_policy_process_allows_role(p, p->agent_role) ||
	      !exec_policy_process_allows_role(p, role)) :
	     (!exec_policy_process_bootstrap(p) ||
	      !exec_policy_process_allows_role(p, role))) ||
	    (p->agent_role_grant_mask & AGENT_ROLE_GRANT_BIT(role)) == 0)
		return AGENT_STATUS_DENIED;
	return AGENT_STATUS_OK;
}

uint
agent_identity_proc_scope(struct proc *p)
{
	if (p == 0 || !p->is_agent ||
	    p->agent_control_state != AGENT_CONTROL_OPEN ||
	    p->vfs_scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    p->vfs_scope_id >= FS_OWNER_SCOPE_FLAG ||
	    !vfs_proc_lifecycle_active(p) ||
	    !vfs_scope_active(p->vfs_scope_id))
		return VFS_SCOPE_NONE;
	return p->vfs_scope_id;
}

int
agent_identity_has_cap(struct proc *p, uint64 cap)
{
	return p != 0 && p->is_agent &&
	       agent_identity_proc_scope(p) != VFS_SCOPE_NONE &&
	       exec_policy_process_allows_role(p, p->agent_role) &&
	       (p->agent_capability_mask & cap) == cap;
}

int
agent_identity_has_any_cap(struct proc *p, uint64 caps)
{
	return p != 0 && p->is_agent &&
	       agent_identity_proc_scope(p) != VFS_SCOPE_NONE &&
	       exec_policy_process_allows_role(p, p->agent_role) &&
	       (p->agent_capability_mask & caps) != 0;
}

int
agent_identity_authorize_object(struct proc *p, uint64 capability,
				uint object_scope, int allow_system)
{
	uint subject_scope = agent_identity_proc_scope(p);

	return agent_identity_has_cap(p, capability) &&
	       (object_scope == subject_scope ||
		(allow_system && object_scope == VFS_SCOPE_SYSTEM));
}

int
agent_identity_controls_target(struct proc *controller, struct proc *target)
{
	return controller != 0 && target != 0 && target->is_agent &&
	       agent_identity_proc_scope(controller) != VFS_SCOPE_NONE &&
	       agent_identity_proc_scope(controller) ==
		       agent_identity_proc_scope(target) &&
	       controller->agent_control_id != 0 &&
	       target->agent_controller_id == controller->agent_control_id;
}

int
agent_identity_controls_or_self(struct proc *controller, struct proc *target)
{
	return controller == target ||
	       agent_identity_controls_target(controller, target);
}

static int
agent_identity_lifecycle_matches(const struct proc *p,
				 struct workflow_lifecycle_key lifecycle,
				 uint scope_id)
{
	return p != 0 && p->workflow_lifecycle_charged &&
	       p->workflow_lifecycle_id == lifecycle.id &&
	       p->workflow_lifecycle_generation == lifecycle.generation &&
	       (p->vfs_scope_id == scope_id ||
		p->vfs_pending_scope_id == scope_id);
}

static struct proc *
agent_identity_controller_find_locked(
	uint64 control_id, struct workflow_lifecycle_key lifecycle, uint scope_id)
{
	for (struct proc *p = pool; p < &pool[NPROC]; p++)
		if (proc_teardown_live(p) && p->is_agent &&
		    p->agent_control_state == AGENT_CONTROL_OPEN &&
		    p->agent_control_id == control_id &&
		    agent_identity_lifecycle_matches(p, lifecycle, scope_id))
			return p;
	return 0;
}

static struct proc *
agent_identity_controller_successor_locked(
	struct proc *departing, struct workflow_lifecycle_key lifecycle,
	uint scope_id, uint64 parent_id)
{
	struct proc *root = 0;

	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (!proc_teardown_live(p) || !p->is_agent || p == departing ||
		    p->agent_control_state != AGENT_CONTROL_OPEN ||
		    p->agent_control_id == 0 || p->vfs_scope_id != scope_id ||
		    p->workflow_lifecycle_id != lifecycle.id ||
		    p->workflow_lifecycle_generation != lifecycle.generation ||
		    !agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE))
			continue;
		if (parent_id != 0 && p->agent_control_id == parent_id)
			return p;
		if (root == 0 && p->vfs_scope_controller)
			root = p;
	}
	return root;
}

int
agent_identity_controller_active_locked(
	uint64 control_id, struct workflow_lifecycle_key lifecycle, uint scope_id)
{
	if (intr_get())
		panic("controller lookup unlocked");
	return control_id != 0 &&
	       agent_identity_controller_find_locked(control_id, lifecycle,
						     scope_id) != 0;
}

/*
 * Bind every process published inside an Agent-controlled workflow to its
 * nearest controller, including pending workers and PUBLIC descendants.  The
 * caller holds the same interrupt-off boundary that publishes the child into
 * its parent table and scheduler, so retirement can neither miss the child
 * nor race it runnable.
 */
int
agent_identity_spawn_publish_locked(struct proc *parent, struct proc *child)
{
	struct workflow_lifecycle_key child_lifecycle;
	struct workflow_lifecycle_key parent_lifecycle;
	uint64 controller_id;
	uint scope_id;

	if (intr_get())
		panic("Agent child publication unlocked");
	if (!proc_teardown_live(parent) || !proc_teardown_live(child))
		return -1;
	child_lifecycle = vfs_proc_lifecycle(child);
	if (!workflow_lifecycle_key_valid(child_lifecycle))
		return child->agent_controller_id == 0 ? 0 : -1;
	/* PUBLIC descendants may carry the key without a raw VFS scope field. */
	if (workflow_lifecycle_scope(child_lifecycle, &scope_id) < 0 ||
	    scope_id < VFS_SCOPE_FIRST_DYNAMIC)
		return -1;
	parent_lifecycle = vfs_proc_lifecycle(parent);
	if (!workflow_lifecycle_key_equal(parent_lifecycle,
					 child_lifecycle)) {
		/* Fresh workflow roots own, rather than inherit, their edge. */
		return child->is_agent && child->vfs_scope_controller &&
		       child->agent_control_state == AGENT_CONTROL_OPEN &&
		       child->agent_control_id != 0 &&
		       child->agent_controller_id == 0 ? 0 : -1;
	}
	controller_id = parent->is_agent ? parent->agent_control_id :
					 parent->agent_controller_id;
	/* The trusted boot lifecycle is system-owned and has no Agent edge. */
	if (controller_id == 0)
		return child->agent_controller_id == 0 ? 0 : -1;
	if (agent_identity_controller_find_locked(controller_id,
						 child_lifecycle, scope_id) == 0 ||
	    (child->agent_controller_id != 0 &&
	     child->agent_controller_id != controller_id))
		return -1;
	child->agent_controller_id = controller_id;
	return 0;
}

/*
 * OPEN -> QUIESCING closes authority and child publication. In the same
 * interrupt-off boundary, live trusted child controllers move to an active
 * successor; non-transferable descendants retain the old edge for subtree
 * cancellation. RETIRED waits until sibling Context and metadata commits are
 * quiescent. Exec downgrade is single-threaded and completes both phases.
 */
int
agent_identity_controller_depart(
	struct proc *departing, int finish,
	struct agent_controller_departure *departure)
{
	struct proc *successor = 0;
	struct workflow_lifecycle_key lifecycle;
	uint64 parent_id;
	uint scope_id;
	int opened = 0;
	int enabled;

	if (departure == 0)
		return 0;
	memset(departure, 0, sizeof(*departure));
	if (departing == 0 || !departing->is_agent ||
	    departing->agent_control_id == 0)
		return 0;
	lifecycle = vfs_proc_lifecycle(departing);
	scope_id = departing->vfs_scope_id;
	parent_id = departing->agent_controller_id;
	if (!workflow_lifecycle_key_valid(lifecycle) ||
	    scope_id < VFS_SCOPE_FIRST_DYNAMIC)
		return 0;

	enabled = intr_save();
	if (departing->agent_control_state == AGENT_CONTROL_RETIRED ||
	    !agent_identity_lifecycle_matches(departing, lifecycle, scope_id)) {
		intr_restore(enabled);
		return 0;
	}
	departure->lifecycle = lifecycle;
	departure->scope_id = scope_id;
	departure->control_id = departing->agent_control_id;
	departure->scope_controller = departing->vfs_scope_controller != 0;
	if (departing->agent_control_state == AGENT_CONTROL_OPEN) {
		departing->agent_control_state = AGENT_CONTROL_QUIESCING;
		opened = 1;
	} else if (!finish) {
		intr_restore(enabled);
		return 0;
	}
	if (opened && !departure->scope_controller) {
		successor = agent_identity_controller_successor_locked(
			departing, lifecycle, scope_id, parent_id);
		if (successor != 0)
			for (struct proc *child = pool;
			     child < &pool[NPROC]; child++)
				if (proc_teardown_live(child) &&
				    child != departing && child->is_agent &&
				    child->agent_control_state ==
					    AGENT_CONTROL_OPEN &&
				    child->agent_control_id != 0 &&
				    child->agent_controller_id ==
					    departure->control_id &&
				    agent_identity_proc_scope(child) == scope_id &&
				    agent_identity_lifecycle_matches(
					    child, lifecycle, scope_id))
					child->agent_controller_id =
						successor->agent_control_id;
	}
	if (!finish) {
		intr_restore(enabled);
		return 1;
	}
	if (departure->scope_controller) {
		intr_restore(enabled);
		return 1;
	}
	departing->agent_control_state = AGENT_CONTROL_RETIRED;
	intr_restore(enabled);
	return 1;
}

/* Commit root retirement only after lifecycle ACTIVE -> CLOSING succeeded. */
int
agent_identity_controller_close_commit(
	struct proc *p, const struct agent_controller_departure *departure)
{
	if (intr_get())
		panic("controller close commit unlocked");
	if (p == 0 || departure == 0 || !departure->scope_controller ||
	    p->agent_control_state != AGENT_CONTROL_QUIESCING ||
	    !p->vfs_scope_controller ||
	    p->agent_control_id != departure->control_id ||
	    p->vfs_scope_id != departure->scope_id ||
	    p->workflow_lifecycle_id != departure->lifecycle.id ||
	    p->workflow_lifecycle_generation !=
		    departure->lifecycle.generation)
		return -1;
	p->agent_control_state = AGENT_CONTROL_RETIRED;
	p->vfs_scope_controller = 0;
	return 0;
}
