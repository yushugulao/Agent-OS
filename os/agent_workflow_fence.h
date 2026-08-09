#ifndef AGENT_WORKFLOW_FENCE_H
#define AGENT_WORKFLOW_FENCE_H

#include "agent.h"

struct proc;

int agent_workflow_fence_execute(
	struct proc *, const struct agent_workflow_fence_request *,
	struct agent_workflow_fence_receipt *);
void agent_workflow_fence_receipt_delivered(
	struct workflow_lifecycle_key, uint64 request_id);

#endif
