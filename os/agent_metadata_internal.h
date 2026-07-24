#ifndef AGENT_METADATA_INTERNAL_H
#define AGENT_METADATA_INTERNAL_H

#include "agent.h"

#define AGENT_META_STALE_BYTES ((AGENT_FILE_META_MAX + 7) / 8)

/*
 * On-disk records are shared as immutable values between the store and the
 * live catalog.  Neither module exports a pointer to its authoritative state.
 */
struct agent_meta_record {
	struct agent_file_meta meta;
	uint scope_id;
	uint slot;
};

struct agent_metadata_apply_result {
	int used;
	int layout_changed;
	uchar missing_slots[AGENT_META_STALE_BYTES];
};

/* Store-owned durable state and writeback scheduling. */
void agent_metadata_store_init(void);
void agent_metadata_store_storage_init(void);
void agent_metadata_store_background_maintain(void);
int agent_metadata_store_load(void);
int agent_metadata_store_reload(uint);
int agent_metadata_store_install_empty(void);
int agent_metadata_store_loaded(void);
int agent_metadata_store_available(void);
int agent_metadata_store_has_durable_bank(void);
int agent_metadata_store_shadow_has_scope(uint);
int agent_metadata_store_submit_wait_locked(void);
int agent_metadata_store_reload_wait_locked(void);
void agent_metadata_store_mark_dirty(uint);
void agent_metadata_store_expedite(uint);
int agent_metadata_store_persist(void);
int agent_metadata_store_persist_system(void);
int agent_metadata_store_scope_pending(uint);
int agent_metadata_store_scope_busy(uint);
void agent_metadata_store_scope_retire(uint);
void agent_metadata_store_fill_info(uint, struct agent_info *);

/* Catalog-owned projections used only through bounded value-copy bridges. */
int agent_metadata_objects_live_count(void);
void agent_metadata_objects_clear_catalog(void);
int agent_metadata_objects_apply_snapshot(
	const struct agent_meta_record *, uint, int, uint,
	struct agent_metadata_apply_result *);
int agent_metadata_objects_export_scope(uint, struct agent_meta_record *, int,
					uint64 *);
void agent_metadata_objects_sizes_persisted(uint, uint64);

#endif
