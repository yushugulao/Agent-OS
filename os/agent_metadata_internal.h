#ifndef AGENT_METADATA_INTERNAL_H
#define AGENT_METADATA_INTERNAL_H

#include "agent_metadata_catalog.h"

/* Apply returns this token; projection sync must precede finish/persistence. */
struct agent_metadata_store_commit {
	struct agent_catalog_delta delta;
	int repair_required;
	int reload_owned;
};

/* Store-owned durable state and writeback scheduling. */
void agent_metadata_store_init(void);
void agent_metadata_store_fail_closed_at_boot(void);
void agent_metadata_store_background_maintain(void);
int agent_metadata_store_take_reconcile_request(void);
int agent_metadata_store_load(struct agent_metadata_store_commit *);
int agent_metadata_store_reload(uint, struct agent_metadata_store_commit *);
int agent_metadata_store_install_empty(struct agent_metadata_store_commit *);
int agent_metadata_store_finish(struct agent_metadata_store_commit *, int);
int agent_metadata_store_loaded(void);
int agent_metadata_store_available(void);
int agent_metadata_store_has_durable_bank(void);
int agent_metadata_store_shadow_has_scope(uint);
int agent_metadata_store_submit_wait_locked(void);
int agent_metadata_store_reload_wait_locked(void);
uint64 agent_metadata_store_mark_dirty(uint);
void agent_metadata_store_expedite(uint);
int agent_metadata_store_persist(void);
int agent_metadata_store_persist_system(void);
int agent_metadata_store_scope_target_done(uint, uint64);
int agent_metadata_store_scope_pending(uint);
void agent_metadata_store_fill_info(uint, struct agent_info *);

#endif
