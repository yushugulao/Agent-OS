#ifndef AGENT_LIVE_QUERY_EVENTS_H
#define AGENT_LIVE_QUERY_EVENTS_H

#include "agent.h"
#include "workflow_lifecycle.h"

struct proc;
struct agent_file_content_receipt;

enum agent_live_query_change {
	AGENT_LIVE_QUERY_ENTER = 1,
	AGENT_LIVE_QUERY_UPDATE,
	AGENT_LIVE_QUERY_LEAVE,
	AGENT_LIVE_QUERY_RESYNC_REQUIRED,
};

void agent_live_query_events_init(void);

/* Deferred unlink records retain the complete non-reusable file identity. */
int agent_live_query_tombstone_enqueue(
	struct workflow_lifecycle_key, uint, uint, uint, uint);
int agent_live_query_tombstone_drain(uint);
int agent_live_query_content_enqueue(
	const struct agent_file_content_receipt *, uint64);
int agent_live_query_content_drain(uint);

/* The caller owns the metadata transaction for the complete fence drain. */
int agent_live_query_fence_drain(struct workflow_lifecycle_key, uint);

int agent_live_query_publish_transition(
	struct proc *, struct workflow_lifecycle_key, uint,
	const struct agent_file_meta *, const struct agent_file_meta *, uint64);

/* IPC lifecycle hooks keep snapshot/watch overflow state generation-safe. */
void agent_live_query_watch_installed(struct proc *);
void agent_live_query_watch_removed(struct proc *);
int agent_live_query_watch_install_typed(
	struct proc *, struct agent_file_live_watch *);
int agent_live_query_watch_remove_typed(
	struct proc *, struct agent_file_live_watch *);
void agent_live_query_proc_reset(struct proc *);

int agent_live_query_resync_ack(
	struct workflow_lifecycle_key, uint, uint64);
void agent_live_query_reclaim(struct workflow_lifecycle_key, uint);

#endif
