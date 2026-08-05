#ifndef AGENT_CONTEXT_PATH_H
#define AGENT_CONTEXT_PATH_H

#include "types.h"

struct agent_context_record;
struct proc;

struct agent_context_path_stats {
	uint64 append_fast_commits;
	uint64 append_history_records_examined;
	uint64 history_walks;
	uint64 history_records_examined;
	uint64 forward_query_calls;
	uint64 forward_query_active_records;
	uint64 forward_query_records_examined;
};

uint64 agent_context_record_hash(const struct agent_context_record *);
int agent_context_read_record(struct proc *, uint64,
			      struct agent_context_record *);
int agent_context_active_measure(struct proc *, uint64, uint64 *, uint64 *);
int agent_context_active_rebuild(struct proc *, uint64, uint64 *, uint64,
				 uint64 *, uint64 *);
int agent_context_active_record(struct proc *, uint64,
				struct agent_context_record *);
void agent_context_path_note_append(uint64);
void agent_context_path_note_forward_query(uint64, uint64);
void agent_context_path_stats_snapshot(struct agent_context_path_stats *);

#endif
