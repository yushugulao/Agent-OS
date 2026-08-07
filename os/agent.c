#include "agent.h"
#include "agent_internal.h"
#include "defs.h"

/*
 * AgentOS 内核稳定门面。子系统状态与策略由各实现模块维护；本文件保持
 * 面向进程、陷阱、文件系统和调度器的既有内核入口稳定。
 */
void
agentinit(void)
{
	agent_core_init();
}

void
agent_proc_prepare(struct proc *p)
{
	agent_core_proc_prepare(p);
}

void
agent_proc_teardown(struct proc *p)
{
	agent_core_proc_teardown(p);
}

void agent_thread_runtime_transition(struct thread *t, int transition)
{
	agent_ipc_thread_runtime_transition(t, transition);
}

void agent_process_image_install_locked(struct proc *p)
{ agent_ipc_process_image_install_locked(p); }

int
agent_exec_public_identity_commit(struct proc *p)
{
	return agent_core_exec_public_commit(p);
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
