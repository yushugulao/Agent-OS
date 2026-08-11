#ifndef AGENT_METADATA_ACTIONS_H
#define AGENT_METADATA_ACTIONS_H

#include "agent.h"

#define AGENT_METADATA_DEPENDENCY_MAX 64
#define AGENT_METADATA_DEPENDENCY_SCOPE_LIMIT 16

struct agent_metadata_dependency_view {
	int used;
	uint scope_id;
	uint64 flags;
	char namespace[AGENT_FILE_PROJECT_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
	char source[AGENT_FILE_FIELD_SIZE];
	char target[AGENT_FILE_FIELD_SIZE];
	char relation[AGENT_FILE_FIELD_SIZE];
	char summary[AGENT_FILE_SUMMARY_SIZE];
};

void agent_metadata_actions_init(void);
void agent_metadata_actions_generation_advance(void);
void agent_metadata_actions_note_changes(uint);
void agent_metadata_actions_reclaim_scope(uint);
void agent_metadata_actions_clear_history(uint);
uint64 agent_metadata_actions_label_bit(const char *);
int agent_metadata_actions_dependency_mask(
	uint, char *, char *, char *, uint64 *);
int agent_metadata_actions_dependency_query(
	uint, char *, char *, char *, struct agent_result *);
int agent_metadata_actions_dependency_update(
	uint, char *, struct agent_result *);
int agent_metadata_actions_seen(
	uint, int, char *, char *, char *, uint64);
void agent_metadata_actions_remember(
	uint, int, char *, char *, char *, uint64);
int agent_metadata_actions_update_status_locked(
	uint, char *, char *, char *, char *, char *, uint64, int);

#endif
