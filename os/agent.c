#include "agent.h"
#include "agent_internal.h"
#include "defs.h"

/*
 * Stable AgentOS kernel facade.  Subsystem state and policy live in the
 * implementation modules; this file keeps the historical kernel entry points
 * stable for proc, trap, filesystem, and scheduler integration.
 */
void
agentinit(void)
{
	agent_core_init();
}

void
agent_scope_controller_departing(struct proc *p)
{
	agent_lifecycle_controller_departing(p);
}

/*
 * Process allocation and destruction enter Agent state through these two
 * phase-aware operations.  The process teardown state is authoritative: a
 * caller cannot clear the control identity before controller revocation, nor
 * free Context state while another thread can still publish into it.
 */
void
agent_proc_prepare(struct proc *p)
{
	if (p == 0 || !proc_teardown_live(p))
		panic("Agent prepare outside live process");
	agent_core_clear_metadata(p);
}

void
agent_proc_teardown(struct proc *p)
{
	if (p == 0 ||
	    p->teardown_state < PROC_TEARDOWN_QUIESCING ||
	    p->teardown_state > PROC_TEARDOWN_SETTLING)
		panic("Agent teardown phase");

	/*
	 * QUIESCING closes authority before sibling syscalls unwind.  Repeating
	 * this step in RECLAIMING is intentional and keeps the operation
	 * idempotent if an unpublished process skipped the early call.
	 */
	if (p->is_agent || p->agent_control_id != 0)
		agent_lifecycle_controller_departing(p);
	if (p->teardown_state == PROC_TEARDOWN_QUIESCING)
		return;
	if (p->teardown_state == PROC_TEARDOWN_SETTLING) {
		if (p->is_agent || !agent_context_is_empty(p))
			panic("Agent teardown incomplete");
		return;
	}
	if (p->teardown_state != PROC_TEARDOWN_RECLAIMING)
		panic("Agent teardown transition");
	if (p->is_agent || !agent_context_is_empty(p))
		agent_free_proc_context(p);
	agent_core_clear_metadata(p);
}

void
agent_storage_init(void)
{
	agent_core_storage_init();
}

void
agent_background_maintain(void)
{
	agent_core_background_maintain();
}

void
agent_tick(void)
{
	agent_core_tick();
}

void
agent_authority_bootstrap(struct proc *p)
{
	agent_identity_authority_bootstrap(p);
}

void
agent_authority_on_exec(struct proc *p)
{
	agent_identity_authority_on_exec(p);
}

int
agent_authority_check(struct proc *p, int role)
{
	return agent_identity_authority_check(p, role);
}
