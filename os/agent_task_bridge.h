#ifndef AGENT_TASK_BRIDGE_H
#define AGENT_TASK_BRIDGE_H

#include "types.h"

struct proc;
struct file;

void agent_task_bridge_init(void);

/* IRQ-safe. The caller schedules background work when the result is nonzero. */
uint agent_task_bridge_tick(uint64 now);

/* O(1) current-process check intended for the user-return fast path. */
int agent_task_bridge_current_deadline_due(void);

/*
 * Drain all currently due deadlines for the current process. A nonnegative
 * result is the number completed; a negative result is a hard drain failure.
 */
int agent_task_bridge_current_deadline_safe_point(void);

/* Preserve the Task Channel core status so teardown can distinguish RETRY. */
int agent_task_bridge_reclaim(struct proc *p);
int agent_task_bridge_active(const struct proc *p);

int sys_agent_task_channel_setup(uint64 setupaddr, uint64 resultaddr);
int sys_agent_task_channel_enter(uint64 enteraddr, uint64 resultaddr);
int sys_agent_task_channel_resource(uint64 controladdr, uint64 resultaddr,
				    struct file *source_file, int source_fd);

#endif
