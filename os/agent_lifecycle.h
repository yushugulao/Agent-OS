#ifndef AGENT_LIFECYCLE_H
#define AGENT_LIFECYCLE_H

#include "types.h"

struct proc;

void agent_lifecycle_init(void);
uint64 agent_lifecycle_alloc_control_id(void);
uint64 agent_lifecycle_controller_departing_locked(struct proc *);
int agent_lifecycle_spawn_publish_locked(struct proc *, struct proc *);
void agent_lifecycle_context_lane_init(struct proc *);
int agent_lifecycle_context_lane_enter(struct proc *);
int agent_lifecycle_context_lane_enter_accepted_task(struct proc *);
void agent_lifecycle_context_lane_leave(struct proc *);
int agent_lifecycle_context_lane_quiescent(struct proc *);

#endif
