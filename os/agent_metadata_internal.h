#ifndef AGENT_METADATA_INTERNAL_H
#define AGENT_METADATA_INTERNAL_H

#include "agent_metadata_catalog.h"

/* Apply returns this token; projection sync must precede finish/persistence. */
struct agent_metadata_store_commit {
	struct agent_catalog_delta delta;
	int repair_required;
	int reload_owned;
};

struct agent_metadata_persist_result {
	uint64 completion_token;
	uint64 job_id;
	int status;
	int cause;
	int durable;
	int irrevocable;
};

enum agent_metadata_persist_cause {
	AGENT_METADATA_PERSIST_NONE = 0,
	AGENT_METADATA_PERSIST_RETRY,
	AGENT_METADATA_PERSIST_INTERRUPTED,
	AGENT_METADATA_PERSIST_IO,
	AGENT_METADATA_PERSIST_DURABILITY,
	AGENT_METADATA_PERSIST_RECOVERY,
	AGENT_METADATA_PERSIST_FAIL_CLOSED,
};

/*
 * A load result describes whether the durable image is trustworthy, not just
 * whether a particular read completed.  Keep transient scheduler/device
 * states distinct so boot can deny metadata admission without turning a
 * recoverable outage into a permanent same-boot failure.
 */
enum agent_metadata_load_status {
	AGENT_METADATA_LOAD_CORRUPT = -1,
	AGENT_METADATA_LOAD_INTERRUPTED = -2,
	AGENT_METADATA_LOAD_BUSY = -3,
	AGENT_METADATA_LOAD_IO = -4,
	AGENT_METADATA_LOAD_PROGRESS = -5,
};

int agent_metadata_inode_trackable(struct inode *);
void agent_metadata_note_catalog_changes(uint);

/* Store-owned durable state and writeback scheduling. */
void agent_metadata_store_init(void);
#ifdef AGENT_METADATA_CRASH_PHASE
int agent_metadata_store_test_quiet_generation(uint, uint64 *);
#endif
void agent_metadata_store_fail_closed_at_boot(void);
void agent_metadata_store_defer_boot_reprobe(int);
void agent_metadata_store_boot_reprobe_complete(int);
void agent_metadata_store_background_maintain(void);
int agent_metadata_store_take_reconcile_request(void);
int agent_metadata_store_load(struct agent_metadata_store_commit *);
int agent_metadata_store_reload(uint, struct agent_metadata_store_commit *);
int agent_metadata_store_finish(struct agent_metadata_store_commit *, int);
int agent_metadata_store_loaded(void);
int agent_metadata_store_available(void);
void agent_metadata_store_fail_closed_runtime(void);
int agent_metadata_store_shadow_has_scope(uint);
int agent_metadata_store_submit_wait_locked(void);
int agent_metadata_store_reload_wait_locked(void);
uint64 agent_metadata_store_mark_dirty(uint);
void agent_metadata_store_expedite(uint);
int agent_metadata_store_persist(void);
int agent_metadata_store_persist_commit(
	struct agent_metadata_persist_result *);
int agent_metadata_store_persist_system(void);
int agent_metadata_store_scope_target_done(uint, uint64);
int agent_metadata_store_scope_pending(uint);
void agent_metadata_store_fill_info(uint, struct agent_info *);

#endif
