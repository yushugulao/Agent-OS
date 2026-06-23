#include "types.h"
#include "riscv.h"
#include "defs.h"
#include "param.h"
#include "memlayout.h"
#include "spinlock.h"
#include "proc.h"
#include "vm.h"
#include "agent.h"

extern struct proc proc[NPROC];

uint64
sys_exit(void)
{
  int n;
  argint(0, &n);
  kexit(n);
  return 0; // not reached
}

uint64
sys_getpid(void)
{
  return myproc()->pid;
}

uint64
sys_fork(void)
{
  return kfork();
}

uint64
sys_agent_fork(void)
{
  return kagentfork();
}

uint64
sys_agent_create(void)
{
  return kagentfork();
}

uint64
sys_agent_create_role(void)
{
  struct proc *p = myproc();
  struct proc *pp;
  int role;
  int orchestrator_alive = 0;

  argint(0, &role);
  if (role < AGENT_ROLE_SENTINEL || role > AGENT_ROLE_ORCHESTRATOR)
    return -1;
  if (p->is_agent) {
    if (p->agent_role != AGENT_ROLE_ORCHESTRATOR &&
        role != AGENT_ROLE_SENTINEL)
      return AGENT_STATUS_DENIED;
  } else if (role != AGENT_ROLE_SENTINEL &&
             role != AGENT_ROLE_ORCHESTRATOR) {
    return -1;
  } else if (role == AGENT_ROLE_ORCHESTRATOR) {
    for (pp = proc; pp < &proc[NPROC]; pp++) {
      acquire(&pp->lock);
      if (pp->state != UNUSED && pp->is_agent &&
          pp->agent_role == AGENT_ROLE_ORCHESTRATOR)
        orchestrator_alive = 1;
      release(&pp->lock);
      if (orchestrator_alive)
        return -1;
    }
  }
  return kagentfork_role(role);
}

uint64
sys_agent_info(void)
{
  uint64 addr;

  argaddr(0, &addr);
  return agent_info_copyout(addr);
}

uint64
sys_agent_call(void)
{
  uint64 reqaddr;
  uint64 respaddr;

  argaddr(0, &reqaddr);
  argaddr(1, &respaddr);
  return agent_tool_call(reqaddr, respaddr);
}

uint64
sys_agent_run(void)
{
  uint64 opsaddr;
  uint64 resultsaddr;
  uint64 flags;
  int count;

  argaddr(0, &opsaddr);
  argaddr(1, &resultsaddr);
  argint(2, &count);
  argaddr(3, &flags);
  return agent_run(opsaddr, resultsaddr, count, flags);
}

uint64
sys_agent_tool_list(void)
{
  uint64 addr;
  int max;

  argaddr(0, &addr);
  argint(1, &max);
  return agent_tool_list(addr, max);
}

uint64
sys_context_push(void)
{
  uint64 recordaddr;

  argaddr(0, &recordaddr);
  return agent_context_push(recordaddr);
}

uint64
sys_context_query(void)
{
  uint64 start_sequence;
  uint64 outaddr;
  int max;

  argaddr(0, &start_sequence);
  argaddr(1, &outaddr);
  argint(2, &max);
  return agent_context_query(start_sequence, outaddr, max);
}

uint64
sys_context_snapshot(void)
{
  uint64 headeraddr;
  uint64 recordsaddr;
  int max;

  argaddr(0, &headeraddr);
  argaddr(1, &recordsaddr);
  argint(2, &max);
  return agent_context_snapshot(headeraddr, recordsaddr, max);
}

uint64
sys_context_rollback(void)
{
  uint64 sequence;

  argaddr(0, &sequence);
  return agent_context_rollback(sequence);
}

uint64
sys_context_clear(void)
{
  return agent_context_clear();
}

uint64
sys_agent_watch(void)
{
  int event_type;
  uint64 filteraddr;

  argint(0, &event_type);
  argaddr(1, &filteraddr);
  return agent_watch(event_type, filteraddr);
}

uint64
sys_agent_unwatch(void)
{
  int event_type;
  uint64 filteraddr;

  argint(0, &event_type);
  argaddr(1, &filteraddr);
  return agent_unwatch(event_type, filteraddr);
}

uint64
sys_agent_wait(void)
{
  uint64 eventaddr;
  int timeout_ticks;

  argaddr(0, &eventaddr);
  argint(1, &timeout_ticks);
  return agent_wait(eventaddr, timeout_ticks);
}

uint64
sys_agent_heartbeat(void)
{
  int interval_ticks;

  argint(0, &interval_ticks);
  return agent_heartbeat(interval_ticks);
}

uint64
sys_agent_heartbeat_stop(void)
{
  return agent_heartbeat_stop();
}

uint64
sys_agent_wake(void)
{
  int pid;
  uint64 eventaddr;

  argint(0, &pid);
  argaddr(1, &eventaddr);
  return agent_wake(pid, eventaddr);
}

uint64
sys_agent_file_meta_init(void)
{
  return agent_file_meta_init();
}

uint64
sys_agent_file_meta_set(void)
{
  uint64 metaaddr;

  argaddr(0, &metaaddr);
  return agent_file_meta_set(metaaddr);
}

uint64
sys_agent_file_query(void)
{
  uint64 queryaddr;
  uint64 resultaddr;

  argaddr(0, &queryaddr);
  argaddr(1, &resultaddr);
  return agent_file_query(queryaddr, resultaddr);
}

uint64
sys_agent_set_role(void)
{
  int role;

  argint(0, &role);
  return agent_set_role(role);
}

uint64
sys_wait(void)
{
  uint64 p;
  argaddr(0, &p);
  return kwait(p);
}

uint64
sys_sbrk(void)
{
  uint64 addr;
  int t;
  int n;

  argint(0, &n);
  argint(1, &t);
  addr = myproc()->sz;

  if (t == SBRK_EAGER || n < 0) {
    if (growproc(n) < 0) {
      return -1;
    }
  } else {
    uint64 limit = myproc()->is_agent ? AGENT_CONTEXT_BASE : TRAPFRAME;
    if (addr + n < addr)
      return -1;
    if (addr + n > limit)
      return -1;
    myproc()->sz += n;
  }
  return addr;
}

uint64
sys_pause(void)
{
  int n;
  uint ticks0;

  argint(0, &n);
  if (n < 0)
    n = 0;
  acquire(&tickslock);
  ticks0 = ticks;
  while (ticks - ticks0 < n) {
    if (killed(myproc())) {
      release(&tickslock);
      return -1;
    }
    sleep(&ticks, &tickslock);
  }
  release(&tickslock);
  return 0;
}

uint64
sys_kill(void)
{
  int pid;

  argint(0, &pid);
  return kkill(pid);
}

uint64
sys_uptime(void)
{
  uint xticks;

  acquire(&tickslock);
  xticks = ticks;
  release(&tickslock);
  return xticks;
}
