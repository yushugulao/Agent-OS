#ifndef AGENT_METADATA_PREFETCH_H
#define AGENT_METADATA_PREFETCH_H

#include "agent.h"

void agent_metadata_prefetch_update(
	struct proc *, struct agent_file_query *,
	struct agent_file_query_result *,
	int *, uint64);

#endif
