#ifndef AGENT_METADATA_CATALOG_H
#define AGENT_METADATA_CATALOG_H

#include "agent.h"
#include "agent_file_state_internal.h"
#include "fs.h"

#define AGENT_META_STALE_BYTES ((AGENT_FILE_META_MAX + 7) / 8)
/* A reload can retain both active and retiring workflow generations. */
#define AGENT_CATALOG_SCOPE_PLAN_MAX 8
#define AGENT_FILE_EXPLICIT_RESERVE 16
#define AGENT_FILE_AUTOSCAN_SCOPE_LIMIT \
	(AGENT_FILE_SCOPE_LIMIT - AGENT_FILE_EXPLICIT_RESERVE)
#define AGENT_CATALOG_JOURNAL_CHANGE_MAX AGENT_FILE_SCOPE_LIMIT
#define AGENT_CATALOG_JOURNAL_RECEIPT_MAX 15
#define AGENT_CATALOG_READ_WORDS ((AGENT_FILE_META_MAX + 63) / 64)

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
#define AGENT_CATALOG_CONFLICT    -3
#define AGENT_CATALOG_INDETERMINATE -4
#define AGENT_CATALOG_NO_SPACE    -5
#define AGENT_CATALOG_INTERRUPTED -6

#define AGENT_CATALOG_STATE_PENDING    (1U << 0)
#define AGENT_CATALOG_STATE_QUARANTINE (1U << 1)
#define AGENT_CATALOG_KEY_FID          (1U << 0)
#define AGENT_CATALOG_KEY_PHYSICAL     (1U << 1)
#define AGENT_CATALOG_KEY_LOGICAL      (1U << 2)
#define AGENT_CATALOG_KEY_IDENTITY     (1U << 3)
#define AGENT_CATALOG_KEY_PATH \
	(AGENT_CATALOG_KEY_PHYSICAL | AGENT_CATALOG_KEY_LOGICAL)

static inline int
agent_metadata_catalog_identity_state(const struct agent_file_meta *meta) {
	int present = meta->dev != 0 && meta->inum != 0 && meta->incarnation != 0;
	int absent = meta->dev == 0 && meta->inum == 0 && meta->incarnation == 0;
	return present ? 1 : absent ? 0 : -1;
}

/* Views are transaction-local and must be dropped before a work checkpoint. */
struct agent_catalog_view {
	const struct agent_file_meta *meta;
	uint scope_id;
	uint state;
};

/*
 * Read-mostly queries snapshot only the bounded candidate bitmap.  Individual
 * records are copied under a short IRQ exclusion and the generation/lifecycle
 * fence is checked before publication, so readers never retain catalog
 * pointers while sleeping or waiting behind durable metadata I/O.
 */
struct agent_catalog_read_snapshot {
	uint64 generation;
	uint scope_id;
	struct workflow_lifecycle_key lifecycle;
	uint64 candidates[AGENT_CATALOG_READ_WORDS];
};

/* Edits use catalog-owned scratch; commit is the only mutation boundary. */
struct agent_catalog_edit {
	struct agent_file_meta *meta;
	uint scope_id;
	int slot;
};

/*
 * A synchronous durable mutation keeps this catalog-only fence while the
 * metadata transaction temporarily drops its gate for bounded device work.
 * The cookie is issued and verified by the catalog; callers only carry it.
 */
struct agent_catalog_mutation_fence {
	uint64 token;
};

/* Exact post-mutation identity required before an in-memory rollback. */
struct agent_catalog_undo_token {
	uint64 fence_token, catalog_generation, slot_binding;
	int slot;
	uint reserved;
};
#define AGENT_CATALOG_UNDO_CREATED (1U << 0)

struct agent_catalog_resolution {
	int slot, owned, ordinary, autoscan;
	uint provided, matched, states;
};

/* Immutable values exchanged between the live catalog and durable store. */
struct agent_meta_record {
	struct agent_file_meta meta;
	uint scope_id, slot;
	struct workflow_lifecycle_key lifecycle;
};

/*
 * Catalog commits coalesce by stable slot until the durable store consumes
 * them.  Sequence guards let a commit retire only the exact snapshot it
 * wrote while a newer mutation to the same slot remains pending.
 */
struct agent_catalog_journal_change {
	uint64 sequence;
	uint slot, present;
	struct agent_meta_record record;
	struct agent_file_content_receipt content;
};

struct agent_catalog_journal_receipt {
	uint scope_id, count;
	struct workflow_lifecycle_key lifecycle;
	uint64 catalog_generation;
	struct agent_catalog_journal_change
		changes[AGENT_CATALOG_JOURNAL_RECEIPT_MAX];
};

struct agent_catalog_journal_settle_entry {
	uint64 sequence;
	uint slot;
};

struct agent_catalog_journal_settle {
	uint scope_id, count;
	struct workflow_lifecycle_key lifecycle;
	uint64 overflow_sequence;
	struct agent_catalog_journal_settle_entry
		entries[AGENT_CATALOG_JOURNAL_CHANGE_MAX];
};

/* Store-to-projection commit record; slot bits describe the final snapshot. */
struct agent_catalog_delta {
	int full_reset;
	uint scope_id;
	uchar applied_slots[AGENT_META_STALE_BYTES];
};

struct agent_catalog_plan_key {
	const struct agent_meta_record *records;
	uint64 candidate_epoch, catalog_generation, lifecycle_generation;
	uint count, reload_scope;
	int reload_one_scope;
	uint lifecycle_id;
};

struct agent_metadata_apply_result {
	int used, layout_changed, prepared, plan_active;
	union {
		struct agent_catalog_plan_key plan_key;
		struct {
			const struct agent_meta_record *plan_records;
			uint64 plan_candidate_epoch, plan_catalog_generation,
				plan_lifecycle_generation;
			uint plan_count, plan_reload_scope;
			int plan_reload_one_scope;
			uint plan_lifecycle_id;
		};
	};
	uint64 plan_token, plan_hash;
	uint plan_cursor, plan_catalog_cursor, plan_next_slot;
	uint plan_ordinary_count, plan_system_count;
	uint plan_scope_ids[AGENT_CATALOG_SCOPE_PLAN_MAX];
	uint plan_scope_counts[AGENT_CATALOG_SCOPE_PLAN_MAX], plan_scope_used;
	uchar missing_slots[AGENT_META_STALE_BYTES];
	uchar blocked_slots[AGENT_META_STALE_BYTES];
	uchar selected_slots[AGENT_META_STALE_BYTES];
	uchar included_records[AGENT_META_STALE_BYTES];
	uchar pending_slots[AGENT_META_STALE_BYTES];
	uchar quarantine_slots[AGENT_META_STALE_BYTES];
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
int agent_metadata_catalog_field_contains(const char *, const char *);
int agent_metadata_catalog_record_base_valid(
	const struct agent_file_meta *, uint, uint);
uint64 agent_metadata_catalog_generation(void);
int agent_metadata_catalog_borrow(uint64, int, struct agent_catalog_view *);
int agent_metadata_catalog_borrow_scan(int, struct agent_catalog_view *);
int agent_metadata_catalog_read_begin(
	uint, int, const char *, int, struct agent_catalog_read_snapshot *, int *);
int agent_metadata_catalog_read_next(
	const struct agent_catalog_read_snapshot *, int);
int agent_metadata_catalog_read_copy(
	const struct agent_catalog_read_snapshot *, int,
	struct agent_file_meta *, uint *);
int agent_metadata_catalog_read_end(
	const struct agent_catalog_read_snapshot *);
int agent_metadata_catalog_edit_begin(int, uint, struct agent_catalog_edit *);
int agent_metadata_catalog_edit_begin_scan(int, uint, struct agent_catalog_edit *);
int agent_metadata_catalog_edit_commit(struct agent_catalog_edit *, uint);
void agent_metadata_catalog_edit_abort(struct agent_catalog_edit *);
int agent_metadata_catalog_mutation_begin(
	struct agent_catalog_mutation_fence *);
int agent_metadata_catalog_mutation_end(
	struct agent_catalog_mutation_fence *);
int agent_metadata_catalog_undo_capture(
	const struct agent_catalog_mutation_fence *, int,
	struct agent_catalog_undo_token *);
int agent_metadata_catalog_undo_note_created(
	const struct agent_catalog_mutation_fence *,
	struct agent_catalog_undo_token *);
int agent_metadata_catalog_bind(int, int, struct proc *);
int agent_metadata_catalog_clear_slot(int);
int agent_metadata_catalog_restore(
	const struct agent_catalog_mutation_fence *,
	const struct agent_catalog_undo_token *,
	const struct agent_file_meta *, uint, int);
void agent_metadata_catalog_resolve(uint, const struct agent_file_meta *, int,
				    struct agent_catalog_resolution *);
int agent_metadata_catalog_alloc_slot(uint, uint);
uint64 agent_metadata_catalog_alloc_fid(uint);
int agent_metadata_catalog_index_seek(uint64, int, char *, int, int *, int *);
int agent_metadata_catalog_live_seek(uint64, int);
int agent_metadata_catalog_reclaim_scope(uint);

int agent_metadata_catalog_live_count(void);
int agent_metadata_catalog_reconcile_pending(void);
int agent_metadata_catalog_reconcile_slot(int);
int agent_metadata_catalog_prepare_snapshot(struct agent_meta_record *, uint,
					    int, uint, uint64,
					    struct agent_metadata_apply_result *);
int agent_metadata_catalog_apply_snapshot(const struct agent_meta_record *, uint,
					  int, uint, uint64,
					  struct agent_metadata_apply_result *);
void agent_metadata_catalog_prepare_abort(struct agent_metadata_apply_result *);
int agent_metadata_catalog_export_scope(uint, struct agent_meta_record *, int, uint64 *);
int agent_metadata_catalog_journal_capture(
	uint, struct workflow_lifecycle_key,
	struct agent_catalog_journal_receipt *, uint64 *);
int agent_metadata_catalog_journal_note_content(
	const struct agent_file_content_receipt *);
void agent_metadata_catalog_journal_commit(
	const struct agent_catalog_journal_receipt *);
int agent_metadata_catalog_journal_settle_capture(
	uint, struct workflow_lifecycle_key,
	struct agent_catalog_journal_settle *);
void agent_metadata_catalog_journal_settle_commit(
	const struct agent_catalog_journal_settle *);

#endif
