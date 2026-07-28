#ifndef AGENT_OBSERVE_RECOVERY_H
#define AGENT_OBSERVE_RECOVERY_H

#include "agent.h"

struct proc;

void agent_observe_recovery_init(void);
int agent_observe_recovery_bind(struct proc *, const struct proc *);
void agent_observe_recovery_unbind_proc(const struct proc *);
int sys_agent_observe_recovery(uint64, uint64);

#endif
