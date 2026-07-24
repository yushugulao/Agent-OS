#include "agent_internal.h"
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
	return next_agent_id++;
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
