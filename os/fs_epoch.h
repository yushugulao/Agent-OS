#ifndef FS_EPOCH_H
#define FS_EPOCH_H

#include "types.h"

struct buf;

/*
 * An epoch publishes ordinary filesystem mutations in durable order.  A
 * caller stages the final image of every modified buffer after binding the
 * persistent storage owner with fs_epoch_note_data().  Foreground I/O may be
 * reused only by the exact top-level request that opened the epoch.
 */
enum fs_epoch_phase {
	FS_EPOCH_PREPARE = 0,
	FS_EPOCH_NAMESPACE_DETACH,
	FS_EPOCH_INODE,
	FS_EPOCH_NAMESPACE_ATTACH,
	FS_EPOCH_PHASE_COUNT,
};

#define FS_EPOCH_BUFFER_CAP 48U
#define FS_EPOCH_HIGH_WATER 36U
#define FS_EPOCH_MAX_AGE_TICKS 2U

enum fs_epoch_status {
	FS_EPOCH_ERROR = -1,
	FS_EPOCH_OWNER_MISMATCH = -2,
	FS_EPOCH_FULL = -3,
	FS_EPOCH_SYNC_REQUIRED = 0,
	FS_EPOCH_CACHED = 1,
};

#define FS_EPOCH_STATS_VERSION 3U
struct fs_epoch_stats {
	uint version;
	uint size;
	uint runtime_enabled;
	uint dirty;
	uint owner;
	uint pinned_buffers;
	uint phase_buffers[FS_EPOCH_PHASE_COUNT];
	uint data_notices;
	uint request_depth;
	uint request_waiters;
	uint bypass_depth;
	uint committing;
	uint64 active_generation;
	uint64 committed_generation;
	uint64 deadline_cycle;
	uint64 staged_buffers;
	uint64 deduplicated_stages;
	uint64 owner_conflicts;
	uint64 capacity_rejections;
	uint64 commit_attempts;
	uint64 successful_commits;
	uint64 failed_commits;
	uint64 metadata_writes;
	uint64 durable_flushes;
	uint64 forward_busy_retries;
	uint64 request_acquisitions;
	uint64 request_contentions;
	int last_error;
	uint max_lookup_probes;
};

void fs_epoch_init(void);
void fs_epoch_runtime_enable(void);
int fs_epoch_runtime_enabled(void);

/*
 * stage returns CACHED when the epoch owns a pin, SYNC_REQUIRED before
 * runtime enable or in a bypass section, and a negative status on failure.
 * A later mutation of the same buffer must be staged again.
 */
int fs_epoch_stage(struct buf *, enum fs_epoch_phase);

/* Binds the single persistent owner and records data ordered before INODE. */
int fs_epoch_note_data(uint owner);

/*
 * BUSY before the first published write is returned without settling debt.
 * After publication starts, BUSY is settled forward and every pin is retained
 * on failure.  Request debt remains with the outer I/O lease, so callers must
 * end that lease only after releasing the epoch request gate.
 */
int fs_epoch_commit(void);
int fs_epoch_prepare_cleanup_sponsor(uint, uint);
int fs_epoch_should_commit(void);
int fs_epoch_dirty(void);
int fs_epoch_buffer_dirty(uint dev, uint blockno);

/* Commit before a mutation would cross owner, capacity, or age bounds. */
int fs_epoch_reserve(uint owner, uint worst_case_buffers);

/*
 * Directory detach and attach images cannot share an epoch: the buffer cache
 * only retains the newest image, while their crash-safe publish order is
 * opposite.  Reserve the namespace class before changing the directory
 * buffer so an incompatible epoch is committed before that image exists.
 */
int fs_epoch_reserve_phase(uint owner, uint worst_case_buffers,
			   enum fs_epoch_phase);

/* Fence deferred reclaim behind the epoch that detached its last reference. */
int fs_epoch_generation_fence(uint owner, uint64 *generation);
int fs_epoch_generation_committed(uint64 generation);

/* Fair, sleepable global serialization for filesystem mutators. */
int fs_epoch_request_begin(void);
void fs_epoch_request_end(void);
int fs_epoch_request_held(void);

/* Destructive paths commit first, then use a nested raw-write section. */
int fs_epoch_bypass_begin(void);
void fs_epoch_bypass_end(void);
int fs_epoch_bypass_active(void);

void fs_epoch_stats_snapshot(struct fs_epoch_stats *);

#endif // FS_EPOCH_H
