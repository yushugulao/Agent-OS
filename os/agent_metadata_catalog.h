#ifndef AGENT_METADATA_CATALOG_H
#define AGENT_METADATA_CATALOG_H

#include "agent.h"
#include "fs.h"

#define AGENT_META_STALE_BYTES ((AGENT_FILE_META_MAX + 7) / 8)

#define AGENT_FILE_CHANGE_STATUS       (1U << 0)
#define AGENT_FILE_CHANGE_STAGE        (1U << 1)
#define AGENT_FILE_CHANGE_KIND         (1U << 2)
#define AGENT_FILE_CHANGE_SCOPE_KEYS   (1U << 3)
#define AGENT_FILE_CHANGE_DEPENDENCY   (1U << 4)
#define AGENT_FILE_CHANGE_MEMBERSHIP   (1U << 5)
#define AGENT_FILE_CHANGE_INDEX_ALL \
	(AGENT_FILE_CHANGE_STATUS | AGENT_FILE_CHANGE_STAGE | \
	 AGENT_FILE_CHANGE_KIND | AGENT_FILE_CHANGE_MEMBERSHIP)
#define AGENT_FILE_CHANGE_ALL \
	(AGENT_FILE_CHANGE_INDEX_ALL | AGENT_FILE_CHANGE_SCOPE_KEYS | \
	 AGENT_FILE_CHANGE_DEPENDENCY)

#define AGENT_CATALOG_INDEX_STATUS 1
#define AGENT_CATALOG_INDEX_STAGE  2
#define AGENT_CATALOG_INDEX_KIND   3
#define AGENT_CATALOG_STALE       -2

/* Views are transaction-local and must be dropped before a work checkpoint. */
struct agent_catalog_view {
	const struct agent_file_meta *meta;
	uint scope_id;
};

/* Edits use catalog-owned scratch; commit is the only mutation boundary. */
struct agent_catalog_edit {
	struct agent_file_meta *meta;
	uint scope_id;
	int slot;
};

/* Immutable values exchanged between the live catalog and durable store. */
struct agent_meta_record {
	struct agent_file_meta meta;
	uint scope_id, slot;
};

/* Store-to-projection commit record; slot bits describe the final snapshot. */
struct agent_catalog_delta {
	int full_reset;
	uint scope_id;
	uchar applied_slots[AGENT_META_STALE_BYTES];
};

struct agent_metadata_apply_result {
	int used, layout_changed;
	uchar missing_slots[AGENT_META_STALE_BYTES];
	struct agent_catalog_delta delta;
};

static inline int agent_scope_valid(uint scope_id) {
	return scope_id >= VFS_SCOPE_FIRST_DYNAMIC && scope_id < FS_OWNER_SCOPE_FLAG;
}

static inline int agent_object_scope_valid(uint scope_id) {
	return scope_id == VFS_SCOPE_SYSTEM || agent_scope_valid(scope_id);
}

static inline int agent_object_scope_visible(uint requester_scope, uint object_scope) {
	return agent_scope_valid(requester_scope) &&
	       (object_scope == requester_scope || object_scope == VFS_SCOPE_SYSTEM);
}

void agent_metadata_catalog_init(void);
uint64 agent_metadata_catalog_generation(void);
int agent_metadata_catalog_borrow(uint64, int, struct agent_catalog_view *);
int agent_metadata_catalog_edit_begin(int, uint, struct agent_catalog_edit *);
int agent_metadata_catalog_edit_commit(struct agent_catalog_edit *, uint);
void agent_metadata_catalog_edit_abort(struct agent_catalog_edit *);
int agent_metadata_catalog_bind(int, int, struct proc *);
void agent_metadata_catalog_clear_slot(int);
void agent_metadata_catalog_restore(int, const struct agent_file_meta *, uint, int);
int agent_metadata_catalog_find(uint, uint64, char *);
int agent_metadata_catalog_alloc_slot(uint);
uint64 agent_metadata_catalog_alloc_fid(uint);
int agent_metadata_catalog_index_seek(uint64, int, char *, int, int *);
int agent_metadata_catalog_reclaim_scope(uint);

int agent_metadata_catalog_live_count(void);
void agent_metadata_catalog_clear(struct agent_catalog_delta *);
int agent_metadata_catalog_apply_snapshot(const struct agent_meta_record *, uint, int, uint,
					  struct agent_metadata_apply_result *);
int agent_metadata_catalog_export_scope(uint, struct agent_meta_record *, int, uint64 *);

#endif
