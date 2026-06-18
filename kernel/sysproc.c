#include "types.h"
#include "riscv.h"
#include "defs.h"
#include "param.h"
#include "memlayout.h"
#include "spinlock.h"
#include "proc.h"
#include "vm.h"

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
