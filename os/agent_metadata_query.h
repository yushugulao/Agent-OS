#ifndef AGENT_METADATA_QUERY_H
#define AGENT_METADATA_QUERY_H

#include "agent.h"

static inline void agent_metadata_query_init(void) {}
int agent_metadata_query_from_payload(struct agent_file_query *, char *);
void agent_metadata_query_invalidate_locked(uint, int);
int agent_metadata_query_has_filter(const struct agent_file_query *);
int agent_metadata_query_matches(uint, uint, const struct agent_file_query *,
				 const struct agent_file_meta *);
int agent_metadata_query_execute_locked(
	uint, const struct agent_file_query *, struct agent_file_query_result *, int);
int agent_metadata_query_execute_snapshot(
	uint, const struct agent_file_query *, struct agent_file_query_result *);

#endif
