#ifndef AGENT_CONTEXT_PATH_H
#define AGENT_CONTEXT_PATH_H

#include "types.h"

struct agent_context_record;
struct proc;

uint64 agent_context_record_hash(const struct agent_context_record *);
int agent_context_read_record(struct proc *, uint64,
			      struct agent_context_record *);
int agent_context_active_measure(struct proc *, uint64, uint64 *, uint64 *);
int agent_context_active_rebuild(struct proc *, uint64, uint64 *, uint64,
				 uint64 *, uint64 *);
#endif
