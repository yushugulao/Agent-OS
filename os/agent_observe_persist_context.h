#ifndef AGENT_OBSERVE_PERSIST_CONTEXT_H
#define AGENT_OBSERVE_PERSIST_CONTEXT_H

#include "types.h"

struct agent_observe_persist_context {
	int running;
	uint kernel_work_depth;
	uint io_request_depth;
	uint buffer_holds;
	uint fs_atomic_depth;
	uint64 sstatus;
	uint64 supervisor_previous_mask;
	int metadata_txn_owned;
	int exit_requested;
};

static inline int
agent_observe_receipt_persist_context_safe(
	const struct agent_observe_persist_context *context)
{
	return context != 0 && context->running &&
	       context->kernel_work_depth != 0 &&
	       context->io_request_depth != 0 && context->buffer_holds == 0 &&
	       context->fs_atomic_depth == 0 &&
	       context->supervisor_previous_mask != 0 &&
	       (context->sstatus & context->supervisor_previous_mask) == 0 &&
	       !context->metadata_txn_owned && !context->exit_requested;
}

#endif
