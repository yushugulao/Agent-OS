#include "fs_epoch.h"

#include "bio.h"
#include "defs.h"
#include "timer.h"
#include "virtio.h"
#include "wait.h"

struct fs_epoch_entry {
	struct buf *buf;
	uint dev;
	uint blockno;
	enum fs_epoch_phase phase;
};

#define FS_EPOCH_INDEX_CAP 64U

struct fs_epoch_state {
	struct fs_epoch_entry entries[FS_EPOCH_BUFFER_CAP];
	uint64 index_generation[FS_EPOCH_INDEX_CAP];
	uchar index_entry_plus_one[FS_EPOCH_INDEX_CAP];
	uint count;
	uint phase_count[FS_EPOCH_PHASE_COUNT];
	uint data_notices;
	uint owner;
	uint sponsor_class;
	uint forward_only;
	uint runtime_enabled;
	uint committing;
	uint rollover_pending;
	uint bypass_depth;
	uint64 active_generation;
	uint64 next_generation;
	uint64 committed_generation;
	uint64 opened_cycle;
	uint64 deadline_cycle;
	void *request_owner;
	uint request_depth;
	uint request_waiters;
	struct wait_queue request_queue;
	uint64 sponsor_request_id;
	struct fs_epoch_stats totals;
};

static struct fs_epoch_state epoch;
static char boot_request_token;

#define FS_EPOCH_MAX_AGE_CYCLES \
	((uint64)FS_EPOCH_MAX_AGE_TICKS * CPU_FREQ / TICKS_PER_SEC)

_Static_assert(FS_EPOCH_HIGH_WATER > 0 &&
	       FS_EPOCH_HIGH_WATER < FS_EPOCH_BUFFER_CAP,
	       "filesystem epoch high water must precede capacity");
_Static_assert((FS_EPOCH_INDEX_CAP & (FS_EPOCH_INDEX_CAP - 1)) == 0 &&
	       FS_EPOCH_BUFFER_CAP < FS_EPOCH_INDEX_CAP,
	       "filesystem epoch index must be sparse and power-of-two");
_Static_assert(FS_EPOCH_MAX_AGE_CYCLES > 0,
	       "filesystem epoch deadline must be positive");

static uint
fs_epoch_index_hash(uint dev, uint blockno)
{
	uint key = blockno ^ dev * 0x9e3779b9U;

	key ^= key >> 16;
	key *= 0x7feb352dU;
	key ^= key >> 15;
	return key & (FS_EPOCH_INDEX_CAP - 1);
}

/* 匹配返回一；未命中时返回零并给出发布槽位。 */
static int
fs_epoch_index_lookup_locked(uint dev, uint blockno, uint *entry_index,
			     uint *publication_slot)
{
	uint bucket;

	if (epoch.active_generation == 0)
		return 0;
	bucket = fs_epoch_index_hash(dev, blockno);
	for (uint probe = 1; probe <= FS_EPOCH_INDEX_CAP; probe++) {
		uint slot_index = (bucket + probe - 1) &
				  (FS_EPOCH_INDEX_CAP - 1);
		uint entry_plus_one;

		if (epoch.totals.max_lookup_probes < probe)
			epoch.totals.max_lookup_probes = probe;
		if (epoch.index_generation[slot_index] !=
		    epoch.active_generation) {
			if (publication_slot != 0)
				*publication_slot = slot_index;
			return 0;
		}
		entry_plus_one = epoch.index_entry_plus_one[slot_index];
		if (entry_plus_one == 0 || entry_plus_one > epoch.count)
			panic("filesystem epoch index entry");
		uint index = entry_plus_one - 1;

		if (epoch.entries[index].dev == dev &&
		    epoch.entries[index].blockno == blockno) {
			if (entry_index != 0)
				*entry_index = index;
			return 1;
		}
	}
	panic("filesystem epoch index saturated");
}

static void
fs_epoch_index_publish_locked(uint slot_index, uint entry_index)
{
	if (epoch.active_generation == 0 ||
	    slot_index >= FS_EPOCH_INDEX_CAP || entry_index >= epoch.count)
		panic("filesystem epoch index publication");
	if (epoch.index_generation[slot_index] == epoch.active_generation)
		panic("filesystem epoch duplicate index publication");
/* 代际是本轮次的发布标记。 */
	epoch.index_entry_plus_one[slot_index] = entry_index + 1;
	epoch.index_generation[slot_index] = epoch.active_generation;
}

static void *
fs_epoch_request_token(void)
{
	struct thread *thread = curr_thread();

	if (thread != 0 && thread->state == RUNNING)
		return thread;
	if (thread != 0 && thread->tid < 0 &&
	    thread->identity_generation == 0)
		return &boot_request_token;
	return epoch.runtime_enabled ? 0 : &boot_request_token;
}

static int
fs_epoch_request_held_locked(void)
{
	void *token = fs_epoch_request_token();

	return token != 0 && epoch.request_owner == token &&
	       epoch.request_depth != 0;
}

static int
fs_epoch_dirty_locked(void)
{
	return epoch.owner != 0 || epoch.count != 0 ||
	       epoch.data_notices != 0;
}

static int
fs_epoch_start_locked(uint owner)
{
	uint64 now;
	uint64 sponsor_request_id = 0;
	uint sponsor_class;

	if (owner == 0 || fs_epoch_dirty_locked())
		panic("filesystem epoch start invariant");
	if (bio_deferred_owner_retain_current(
		    owner, &sponsor_class, &sponsor_request_id) < 0) {
		sponsor_request_id = 0;
		if (bio_deferred_owner_retain_cleanup(
			    owner, &sponsor_class) < 0)
			return FS_EPOCH_ERROR;
	}
	if (epoch.next_generation == (uint64)-1)
		panic("filesystem epoch generation exhausted");
	epoch.next_generation++;
	if (epoch.next_generation == 0)
		panic("filesystem epoch generation wrapped");
	now = get_cycle();
	epoch.owner = owner;
	epoch.rollover_pending = 0;
	epoch.sponsor_class = sponsor_class;
	epoch.sponsor_request_id = sponsor_request_id;
	epoch.forward_only = 0;
	epoch.active_generation = epoch.next_generation;
	epoch.opened_cycle = now;
	epoch.deadline_cycle = now + FS_EPOCH_MAX_AGE_CYCLES;
	return FS_EPOCH_CACHED;
}

static int
fs_epoch_bind_owner_locked(uint owner)
{
	if (owner == 0)
		return FS_EPOCH_ERROR;
	if (!fs_epoch_dirty_locked() &&
	    fs_epoch_start_locked(owner) != FS_EPOCH_CACHED)
		return FS_EPOCH_ERROR;
	if (epoch.owner == owner)
		return FS_EPOCH_CACHED;
	epoch.totals.owner_conflicts++;
	return FS_EPOCH_OWNER_MISMATCH;
}

void
fs_epoch_init(void)
{
	memset(&epoch, 0, sizeof(epoch));
	wait_queue_init(&epoch.request_queue, WAIT_REASON_FS_CLAIM);
}

void
fs_epoch_runtime_enable(void)
{
	int enabled = intr_save();

	if (epoch.runtime_enabled || fs_epoch_dirty_locked() ||
	    epoch.request_owner != 0)
		panic("filesystem epoch runtime transition");
	epoch.runtime_enabled = 1;
	intr_restore(enabled);
}

int
fs_epoch_runtime_enabled(void)
{
	int enabled = intr_save();
	int runtime_enabled = epoch.runtime_enabled;

	intr_restore(enabled);
	return runtime_enabled;
}

int
fs_epoch_request_begin(void)
{
	void *token = fs_epoch_request_token();
	int queued = 0;
	int enabled;

	if (token == 0)
		return FS_EPOCH_ERROR;
	enabled = intr_save();
	if (epoch.request_owner == token) {
		if (epoch.request_depth == (uint)-1)
			panic("filesystem epoch request depth overflow");
		epoch.request_depth++;
		intr_restore(enabled);
		return 0;
	}
	for (;;) {
		if (epoch.request_owner == 0) {
			epoch.request_owner = token;
			epoch.request_depth = 1;
			epoch.totals.request_acquisitions++;
			if (queued) {
				if (epoch.request_waiters == 0)
					panic("filesystem epoch waiter underflow");
				epoch.request_waiters--;
			}
			intr_restore(enabled);
			return 0;
		}
		if (token == &boot_request_token) {
			intr_restore(enabled);
			return FS_EPOCH_ERROR;
		}
		if (!queued) {
			queued = 1;
			epoch.request_waiters++;
			epoch.totals.request_contentions++;
		}
		if (wait_queue_sleep_irq_uninterruptible(
			    &epoch.request_queue) != WAIT_QUEUE_OK) {
			if (epoch.request_waiters == 0)
				panic("filesystem epoch waiter underflow");
			epoch.request_waiters--;
			intr_restore(enabled);
			return FS_EPOCH_ERROR;
		}
	}
}

void
fs_epoch_request_end(void)
{
	int enabled = intr_save();

	if (!fs_epoch_request_held_locked() || epoch.bypass_depth != 0)
		panic("filesystem epoch request invariant");
	epoch.request_depth--;
	if (epoch.request_depth == 0) {
		epoch.request_owner = 0;
		/*
		 * 不把所有权交给裸线程指针。被唤醒线程可能在重新获取门锁前拆除，
		 * 造成永久无主的移交；应唤醒全部等待者，由调度器选择下一个存活请求者。
		 */
		wait_queue_wake_all(&epoch.request_queue);
	}
	intr_restore(enabled);
}

int
fs_epoch_request_held(void)
{
	int enabled = intr_save();
	int held = fs_epoch_request_held_locked();

	intr_restore(enabled);
	return held;
}

int
fs_epoch_bypass_begin(void)
{
	int enabled = intr_save();

	if (!fs_epoch_request_held_locked() || fs_epoch_dirty_locked() ||
	    epoch.committing) {
		intr_restore(enabled);
		return FS_EPOCH_ERROR;
	}
	if (epoch.bypass_depth == (uint)-1)
		panic("filesystem epoch bypass depth overflow");
	epoch.bypass_depth++;
	intr_restore(enabled);
	return 0;
}

void
fs_epoch_bypass_end(void)
{
	int enabled = intr_save();

	if (!fs_epoch_request_held_locked() || epoch.bypass_depth == 0)
		panic("filesystem epoch bypass invariant");
	epoch.bypass_depth--;
	intr_restore(enabled);
}

int
fs_epoch_bypass_active(void)
{
	int enabled = intr_save();
	int active = fs_epoch_request_held_locked() &&
		     epoch.bypass_depth != 0;

	intr_restore(enabled);
	return active;
}

int
fs_epoch_note_data(uint owner)
{
	int result;
	int enabled = intr_save();

	if (!epoch.runtime_enabled ||
	    (fs_epoch_request_held_locked() && epoch.bypass_depth != 0)) {
		intr_restore(enabled);
		return FS_EPOCH_SYNC_REQUIRED;
	}
	if (!fs_epoch_request_held_locked() || epoch.committing) {
		intr_restore(enabled);
		return FS_EPOCH_ERROR;
	}
	if (epoch.totals.last_error < 0) {
		result = epoch.totals.last_error;
		intr_restore(enabled);
		return result;
	}
	result = fs_epoch_bind_owner_locked(owner);
	if (result == FS_EPOCH_CACHED) {
		if (epoch.data_notices == (uint)-1)
			panic("filesystem epoch data count overflow");
		epoch.data_notices++;
	}
	intr_restore(enabled);
	return result;
}

int
fs_epoch_stage(struct buf *bp, enum fs_epoch_phase phase)
{
	uint entry_index;
	uint owner;
	uint publication_slot;
	int result;
	int enabled;

	if (bp == 0 || phase < FS_EPOCH_PREPARE ||
	    phase >= FS_EPOCH_PHASE_COUNT || bp->hold_depth == 0 ||
	    bp->refcnt == 0 || !bp->valid)
		return FS_EPOCH_ERROR;
	enabled = intr_save();
	if (!epoch.runtime_enabled ||
	    (fs_epoch_request_held_locked() && epoch.bypass_depth != 0)) {
		intr_restore(enabled);
		return FS_EPOCH_SYNC_REQUIRED;
	}
	if (!fs_epoch_request_held_locked() || epoch.committing) {
		intr_restore(enabled);
		return FS_EPOCH_ERROR;
	}
	if (epoch.totals.last_error < 0) {
		result = epoch.totals.last_error;
		intr_restore(enabled);
		return result;
	}
	owner = bio_current_owner();
	result = fs_epoch_bind_owner_locked(owner);
	if (result != FS_EPOCH_CACHED) {
		intr_restore(enabled);
		return result;
	}
	if (fs_epoch_index_lookup_locked(bp->dev, bp->blockno,
					 &entry_index, &publication_slot)) {
		struct fs_epoch_entry *entry = &epoch.entries[entry_index];

		if (entry->buf != bp) {
			intr_restore(enabled);
			return FS_EPOCH_ERROR;
		}
		if ((entry->phase == FS_EPOCH_NAMESPACE_DETACH &&
		     phase == FS_EPOCH_NAMESPACE_ATTACH) ||
		    (entry->phase == FS_EPOCH_NAMESPACE_ATTACH &&
		     phase == FS_EPOCH_NAMESPACE_DETACH)) {
			intr_restore(enabled);
			return FS_EPOCH_ERROR;
		}
		if (phase > entry->phase) {
			epoch.phase_count[entry->phase]--;
			entry->phase = phase;
			epoch.phase_count[phase]++;
		}
		epoch.totals.deduplicated_stages++;
		intr_restore(enabled);
		return FS_EPOCH_CACHED;
	}
	if (epoch.count == FS_EPOCH_BUFFER_CAP) {
		epoch.totals.capacity_rejections++;
		intr_restore(enabled);
		return FS_EPOCH_FULL;
	}
	if (bp->refcnt == (uint)-1)
		panic("filesystem epoch buffer ref overflow");
	entry_index = epoch.count++;
	struct fs_epoch_entry *entry = &epoch.entries[entry_index];

	entry->buf = bp;
	entry->dev = bp->dev;
	entry->blockno = bp->blockno;
	entry->phase = phase;
	fs_epoch_index_publish_locked(publication_slot, entry_index);
	bpin(bp);
	epoch.phase_count[phase]++;
	epoch.totals.staged_buffers++;
	intr_restore(enabled);
	return FS_EPOCH_CACHED;
}

static int
fs_epoch_forward_wait(void)
{
	int result = bio_request_settle_quiescent_cleanup();
	int enabled = intr_save();

	epoch.totals.forward_busy_retries++;
	intr_restore(enabled);
	return result;
}

static int
fs_epoch_acquire_entry(struct fs_epoch_entry *entry, struct buf **out)
{
	for (;;) {
		struct buf *bp = 0;
		int result = bread(entry->dev, entry->blockno, &bp);

		if (result == VIRTIO_DISK_ERR_BUSY) {
			if (!epoch.forward_only)
				return result;
			if (fs_epoch_forward_wait() < 0)
				return result;
			continue;
		}
		if (result < 0)
			return result;
		if (bp != entry->buf) {
			brelse(bp);
			return VIRTIO_DISK_ERR_IO;
		}
		*out = bp;
		return 0;
	}
}

static int
fs_epoch_write_phase(enum fs_epoch_phase phase)
{
	struct buf *batch[VIRTIO_DISK_WRITE_BATCH_MAX];
	uint cursor = 0;

	while (cursor < epoch.count) {
		uint batch_start = cursor;
		uint batch_count = 0;
		uint next = cursor;
		int result;

		while (next < epoch.count &&
		       batch_count < VIRTIO_DISK_WRITE_BATCH_MAX) {
			struct fs_epoch_entry *entry = &epoch.entries[next++];

			if (entry->phase != phase)
				continue;
			result = fs_epoch_acquire_entry(
				entry, &batch[batch_count]);
			if (result < 0) {
				while (batch_count != 0)
					brelse(batch[--batch_count]);
				return result;
			}
			batch_count++;
		}
		cursor = next;
		if (batch_count == 0)
			continue;
		result = virtio_disk_write_batch(batch, batch_count);
		for (uint i = 0; i < batch_count; i++)
			brelse(batch[i]);
		if (result == VIRTIO_DISK_ERR_BUSY) {
			if (!epoch.forward_only)
				return result;
			if (fs_epoch_forward_wait() < 0)
				return result;
			cursor = batch_start;
			continue;
		}
/* 任何非 BUSY 结果都已抵达设备，不能回滚。 */
		epoch.forward_only = 1;
		if (result < 0)
			return result;
		int enabled = intr_save();

		epoch.totals.metadata_writes += batch_count;
		intr_restore(enabled);
	}
	return 0;
}

static int
fs_epoch_flush_forward(void)
{
	for (;;) {
		int result = bio_durable_flush();

		if (result != VIRTIO_DISK_ERR_BUSY)
			return result;
		if (!epoch.forward_only)
			return result;
		if (fs_epoch_forward_wait() < 0)
			return result;
	}
}

static int
fs_epoch_commit_fail(int result)
{
	int enabled = intr_save();

	epoch.committing = 0;
	epoch.totals.failed_commits++;
	epoch.totals.last_error = result;
	intr_restore(enabled);
	return result < 0 ? result : FS_EPOCH_ERROR;
}

static int
fs_epoch_commit_sponsored_fail(int result, int sponsor_started)
{
	if (sponsor_started)
		bio_deferred_sponsor_end();
	return fs_epoch_commit_fail(result);
}

int
fs_epoch_commit(void)
{
	uint commit_owner;
	uint64 sponsor_request_id;
	uint sponsor_class;
	int sponsor_started = 0;
	int enabled = intr_save();

	if (!fs_epoch_request_held_locked())
		panic("filesystem epoch commit outside request");
	if (epoch.bypass_depth != 0 || epoch.committing)
		panic("filesystem epoch commit state");
	if (!epoch.runtime_enabled || !fs_epoch_dirty_locked()) {
		if (!fs_epoch_dirty_locked())
			epoch.rollover_pending = 0;
		intr_restore(enabled);
		return 0;
	}
/* 设备发布和债务结算可以休眠。持有缓存行或文件系统原子单元的调用方必须
	 * 推迟轮换，不得将瞬态状态带入提交代办者。 */
	if (!bio_io_quiescent_current()) {
		epoch.rollover_pending = 1;
		intr_restore(enabled);
		return VIRTIO_DISK_ERR_BUSY;
	}
	epoch.committing = 1;
	epoch.totals.commit_attempts++;
	commit_owner = epoch.owner;
	sponsor_class = epoch.sponsor_class;
	sponsor_request_id = epoch.sponsor_request_id;
	intr_restore(enabled);
	if (!bio_cleanup_sponsor_covers(
		    commit_owner, sponsor_class, sponsor_request_id)) {
		if (bio_deferred_sponsor_begin(
			    commit_owner, sponsor_class,
			    sponsor_request_id) < 0)
			return fs_epoch_commit_fail(FS_EPOCH_ERROR);
		sponsor_started = 1;
	}

	for (uint phase = FS_EPOCH_PREPARE;
	     phase < FS_EPOCH_PHASE_COUNT; phase++) {
		int phase_dirty = phase == FS_EPOCH_PREPARE &&
				  epoch.data_notices != 0;
		int result;

		if (epoch.phase_count[phase] != 0)
			phase_dirty = 1;
		result = fs_epoch_write_phase((enum fs_epoch_phase)phase);
		if (result < 0)
			return fs_epoch_commit_sponsored_fail(
				result, sponsor_started);
		if (phase_dirty) {
			result = fs_epoch_flush_forward();

			if (result < 0)
				return fs_epoch_commit_sponsored_fail(
					result, sponsor_started);
			enabled = intr_save();
			epoch.totals.durable_flushes++;
			intr_restore(enabled);
		}
	}
	if (sponsor_started)
		bio_deferred_sponsor_end();

	for (uint i = 0; i < epoch.count; i++)
		bunpin(epoch.entries[i].buf);
	enabled = intr_save();
	epoch.committed_generation = epoch.active_generation;
	memset(epoch.entries, 0, sizeof(epoch.entries));
	memset(epoch.phase_count, 0, sizeof(epoch.phase_count));
	epoch.count = 0;
	epoch.data_notices = 0;
	epoch.owner = 0;
	epoch.sponsor_class = 0;
	epoch.sponsor_request_id = 0;
	epoch.forward_only = 0;
	epoch.rollover_pending = 0;
	epoch.active_generation = 0;
	epoch.opened_cycle = 0;
	epoch.deadline_cycle = 0;
	epoch.committing = 0;
	epoch.totals.successful_commits++;
	epoch.totals.last_error = 0;
	intr_restore(enabled);
	bio_deferred_owner_release(commit_owner);
	return 0;
}

int
fs_epoch_commit_polling(void)
{
	int result = VIRTIO_DISK_ERR_BUSY;
	int enabled = intr_save();

	if (!fs_epoch_request_held_locked() ||
	    fs_epoch_request_token() != &boot_request_token)
		goto out;
	/* 空闲执行器不能睡眠；发布前确认固定缓存行均可立即取得。 */
	for (uint i = 0; i < epoch.count; i++) {
		if (epoch.entries[i].buf == 0 ||
		    epoch.entries[i].buf->hold_depth != 0)
			goto out;
	}
	result = fs_epoch_commit();
out:
	intr_restore(enabled);
	return result;
}

int
fs_epoch_prepare_cleanup_sponsor(uint owner, uint io_class)
{
	int compatible;
	int dirty;
	int enabled = intr_save();

	if (!fs_epoch_request_held_locked() || epoch.bypass_depth != 0 ||
	    epoch.committing || owner == 0 ||
	    io_class >= IO_POLICY_CLASS_COUNT) {
		intr_restore(enabled);
		return FS_EPOCH_ERROR;
	}
	dirty = fs_epoch_dirty_locked();
	compatible = dirty && epoch.owner == owner &&
		epoch.sponsor_class == io_class &&
		epoch.sponsor_request_id == 0;
	intr_restore(enabled);
	if (!dirty || compatible)
		return 0;
	return fs_epoch_commit();
}

int
fs_epoch_should_commit(void)
{
	int enabled = intr_save();
	int should = fs_epoch_dirty_locked() &&
		     (epoch.rollover_pending ||
		      epoch.count >= FS_EPOCH_HIGH_WATER ||
		      get_cycle() - epoch.opened_cycle >=
			      FS_EPOCH_MAX_AGE_CYCLES);

	intr_restore(enabled);
	return should;
}

int
fs_epoch_dirty(void)
{
	int enabled = intr_save();
	int dirty = fs_epoch_dirty_locked();

	intr_restore(enabled);
	return dirty;
}

static int
fs_epoch_reserve_ordered(uint owner, uint worst_case_buffers,
			 enum fs_epoch_phase phase)
{
	int hard_rollover = 0;
	int quiescent;
	int soft_rollover = 0;
	int enabled;

	if (owner == 0 || worst_case_buffers > FS_EPOCH_BUFFER_CAP ||
	    phase > FS_EPOCH_PHASE_COUNT)
		return FS_EPOCH_ERROR;
	enabled = intr_save();
	if (!epoch.runtime_enabled ||
	    (fs_epoch_request_held_locked() && epoch.bypass_depth != 0)) {
		intr_restore(enabled);
		return 0;
	}
	if (!fs_epoch_request_held_locked() || epoch.committing) {
		intr_restore(enabled);
		return FS_EPOCH_ERROR;
	}
	if (epoch.totals.last_error < 0)
		hard_rollover = 1;
	else if (fs_epoch_dirty_locked()) {
		hard_rollover = epoch.owner != owner ||
			(phase == FS_EPOCH_NAMESPACE_DETACH &&
			 epoch.phase_count[FS_EPOCH_NAMESPACE_ATTACH] != 0) ||
			(phase == FS_EPOCH_NAMESPACE_ATTACH &&
			 epoch.phase_count[FS_EPOCH_NAMESPACE_DETACH] != 0) ||
			worst_case_buffers > FS_EPOCH_BUFFER_CAP - epoch.count;
		if (!hard_rollover)
			soft_rollover = epoch.count >= FS_EPOCH_HIGH_WATER ||
				worst_case_buffers >
					FS_EPOCH_HIGH_WATER - epoch.count ||
				get_cycle() - epoch.opened_cycle >=
					FS_EPOCH_MAX_AGE_CYCLES;
	}
	quiescent = bio_io_quiescent_current();
	if (hard_rollover && !quiescent)
		epoch.rollover_pending = 1;
	intr_restore(enabled);
	if (!hard_rollover && !soft_rollover)
		return 0;
/* 绝对容量仍足够时，兼容的软轮换可以等待；属主、阶段或容量冲突须在
	 * 下一变更单元前停止。 */
	if (!quiescent)
		return hard_rollover ? VIRTIO_DISK_ERR_BUSY : 0;
	return fs_epoch_commit();
}

int
fs_epoch_reserve(uint owner, uint worst_case_buffers)
{
	return fs_epoch_reserve_ordered(owner, worst_case_buffers,
					FS_EPOCH_PHASE_COUNT);
}

int
fs_epoch_reserve_phase(uint owner, uint worst_case_buffers,
		       enum fs_epoch_phase phase)
{
	if (phase != FS_EPOCH_NAMESPACE_DETACH &&
	    phase != FS_EPOCH_NAMESPACE_ATTACH)
		return FS_EPOCH_ERROR;
	return fs_epoch_reserve_ordered(owner, worst_case_buffers, phase);
}

int
fs_epoch_generation_fence(uint owner, uint64 *generation)
{
	int enabled;

	if (owner == 0 || generation == 0)
		return FS_EPOCH_ERROR;
	enabled = intr_save();
	if (!fs_epoch_request_held_locked() || epoch.committing ||
	    !fs_epoch_dirty_locked() || epoch.active_generation == 0 ||
	    epoch.owner != owner) {
		intr_restore(enabled);
		return FS_EPOCH_ERROR;
	}
	*generation = epoch.active_generation;
	intr_restore(enabled);
	return 0;
}

int
fs_epoch_generation_committed(uint64 generation)
{
	int enabled = intr_save();
	int committed = epoch.committed_generation >= generation;

	intr_restore(enabled);
	return committed;
}

int
fs_epoch_buffer_dirty(uint dev, uint blockno)
{
	int enabled = intr_save();
	int dirty = fs_epoch_index_lookup_locked(dev, blockno, 0, 0);

	intr_restore(enabled);
	return dirty;
}

void
fs_epoch_stats_snapshot(struct fs_epoch_stats *stats)
{
	int enabled;

	if (stats == 0)
		return;
	enabled = intr_save();
	*stats = epoch.totals;
	stats->version = FS_EPOCH_STATS_VERSION;
	stats->size = sizeof(*stats);
	stats->runtime_enabled = epoch.runtime_enabled;
	stats->dirty = fs_epoch_dirty_locked();
	stats->owner = epoch.owner;
	stats->pinned_buffers = epoch.count;
	for (uint phase = 0; phase < FS_EPOCH_PHASE_COUNT; phase++)
		stats->phase_buffers[phase] = epoch.phase_count[phase];
	stats->data_notices = epoch.data_notices;
	stats->request_depth = epoch.request_depth;
	stats->request_waiters = epoch.request_waiters;
	stats->bypass_depth = epoch.bypass_depth;
	stats->committing = epoch.committing;
	stats->active_generation = epoch.active_generation;
	stats->committed_generation = epoch.committed_generation;
	stats->deadline_cycle = epoch.deadline_cycle;
	intr_restore(enabled);
}
