#include "agent.h"
#include "agent_internal.h"
#include "agent_task_bridge.h"
#include "defs.h"
#include "timer.h"

/*
 * AgentOS 内核稳定门面。子系统状态与策略由各实现模块维护；本文件保持
 * 面向进程、陷阱、文件系统和调度器的既有内核入口稳定。
 */
void
agentinit(void)
{
	agent_core_init();
	agent_task_bridge_init();
}

void
agent_proc_prepare(struct proc *p)
{
	agent_core_proc_prepare(p);
}

int
agent_proc_teardown(struct proc *p)
{
	int status;

	if (p == 0)
		return -1;
	if (p->teardown_state == PROC_TEARDOWN_QUIESCING) {
		/* Publish controller/lifecycle departure before waiting on providers. */
		agent_core_proc_teardown(p);
		status = agent_task_bridge_reclaim(p);
		if (status == AGENT_TASK_CHANNEL_RETRY)
			return -1;
		if (status != AGENT_TASK_CHANNEL_OK)
			panic("Task Channel teardown");
		return 0;
	} else {
		if (agent_task_bridge_active(p))
			panic("Task Channel teardown phase");
		/* Sibling user execution is quiescent before RECLAIMING begins. */
		if (agent_task_bridge_endpoint_active(p)) {
			status = agent_task_bridge_reclaim(p);
			if (status != AGENT_TASK_CHANNEL_OK)
				panic("delegated Task endpoint teardown");
		}
	}
	agent_core_proc_teardown(p);
	return 0;
}

void agent_thread_runtime_transition(struct thread *t, int transition)
{
	agent_ipc_thread_runtime_transition(t, transition);
	agent_task_bridge_thread_runtime_transition(t, transition);
}

void agent_process_image_install_locked(struct proc *p)
{ agent_ipc_process_image_install_locked(p); }

int
agent_exec_public_identity_commit(struct proc *p)
{
	if (agent_task_bridge_active(p) ||
	    agent_task_bridge_endpoint_active(p))
		return -1;
	return agent_core_exec_public_commit(p);
}

int
agent_exec_image_install_allowed(const struct proc *p)
{
	return p != 0 && !agent_task_bridge_endpoint_active(p);
}

void
agent_storage_init(void)
{
	agent_core_storage_init();
}

void
agent_tick(void)
{
	agent_core_tick();
	if (agent_task_bridge_tick(get_cycle() / (CPU_FREQ / TICKS_PER_SEC)) != 0)
		agent_background_request();
}

int
agent_task_deadline_due_current(void)
{
	return agent_task_bridge_current_deadline_due();
}

int
agent_task_deadline_checkpoint(void)
{
	return agent_task_bridge_current_deadline_safe_point();
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
