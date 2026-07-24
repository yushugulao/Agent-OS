#ifndef AGENT_LIFECYCLE_H
#define AGENT_LIFECYCLE_H

#include "types.h"

struct proc;

void agent_lifecycle_init(void);
uint64 agent_lifecycle_alloc_control_id(void);
void agent_lifecycle_controller_departing(struct proc *);
void agent_lifecycle_context_lane_init(struct proc *);
int agent_lifecycle_context_lane_enter(struct proc *);
void agent_lifecycle_context_lane_leave(struct proc *);
int agent_lifecycle_context_lane_quiescent(struct proc *);

#endif
