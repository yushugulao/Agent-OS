#include "agent_internal.h"
#include "agent_file_name_policy.h"
#include "agent_metadata_internal.h"
#include "bio.h"
#include "defs.h"
#include "file.h"
#include "fs.h"
#include "timer.h"
#include "vfs_security.h"

/*
 * Durable owner for Agent metadata.  COW bank buffers, writeback generations,
 * submit tickets, and recovery state never leave this module; catalog exchange
 * is by bounded record copies declared in agent_metadata_internal.h.
 */

#define AGENT_FILE_SYSTEM_LIMIT 64
#define AGENT_FILE_SCOPE_LIMIT 112
#define AGENT_META_STORE_BANKS 2
#define AGENT_META_STORE_MAGIC 0x41474d4554413036ULL
#define AGENT_META_STORE_VERSION 5
#define AGENT_META_WRITEBACK_COALESCE_TICKS TICKS_PER_SEC
#define AGENT_META_WRITEBACK_SCOPE_MAX NPROC
#define AGENT_META_PERSIST_IDLE 0U
#define AGENT_META_PERSIST_INVALIDATE 1U
#define AGENT_META_PERSIST_WRITE 2U
#define AGENT_META_PERSIST_PUBLISH 3U
#define AGENT_META_PERSIST_VERIFY_HEADER 4U
#define AGENT_META_PERSIST_VERIFY_PAYLOAD 5U
#define AGENT_META_PERSIST_COMMIT 6U
#define AGENT_META_PERSIST_DIRTY_BYTES ((MAXFILE + 7) / 8)
#define AGENT_META_PERSIST_FAILURE_LIMIT 8U
#define AGENT_META_PERSIST_DEFERRED (-2)
#define AGENT_META_BANK_VALID 0
#define AGENT_META_BANK_ABSENT 1
#define AGENT_META_BANK_CORRUPT (-1)
#define AGENT_META_BANK_INTERRUPTED (-2)

struct agent_meta_store_header {
	uint64 magic;
	uint64 version;
	uint64 count;
	uint64 generation;
	uint64 payload_hash;
};

struct agent_meta_store {
	struct agent_meta_store_header header;
	struct agent_meta_record records[AGENT_FILE_META_MAX];
};

struct agent_file_scope_state {
	int used;
	uint scope_id;
	uint64 dirty_generation;
	uint64 durable_generation;
	uint64 due_tick;
	uint64 request_count;
	uint64 coalesced_count;
	uint64 commit_count;
};

struct agent_file_writeback_capture {
	uint scope_id;
	uint64 dirty_generation;
};

struct agent_meta_persist_state {
	uint phase;
	uint owner;
	uint scope_id;
	uint64 job_id;
	int published;
	int irrevocable;
	int primary_verified;
	int mirroring;
	int restart_target;
	int target_bank;
	uint target_dev;
	uint target_inum;
	uint target_incarnation;
	uint store_bytes;
	uint write_limit;
	uint write_offset;
	uint verify_offset;
	uint64 expected_generation;
	uint64 expected_hash;
	uint64 verify_hash;
	uint64 size_sequence;
	uint64 started_tick;
	uint64 retry_tick;
	uint retry_failures;
	uchar dirty_blocks[AGENT_META_PERSIST_DIRTY_BYTES];
	char verify_block[BSIZE];
};

_Static_assert(sizeof(struct agent_meta_store) <= MAXFILE * BSIZE,
	       "Agent metadata store exceeds maximum file size");
#define AGENT_META_STORE_MAX_DATA_BLOCKS \
	((sizeof(struct agent_meta_store) + BSIZE - 1) / BSIZE)
#define AGENT_META_STORE_MAX_INDEX_BLOCKS \
	(AGENT_META_STORE_MAX_DATA_BLOCKS > NDIRECT ? 1 : 0)
_Static_assert(FS_STORAGE_TINY_TEST_PROFILE ||
	       FS_SYSTEM_BLOCK_MIN_RESERVE >=
	       AGENT_META_STORE_BANKS *
		       (AGENT_META_STORE_MAX_DATA_BLOCKS +
			AGENT_META_STORE_MAX_INDEX_BLOCKS),
	       "SYSTEM reserve must fund both metadata banks");
_Static_assert(FS_STORAGE_TINY_TEST_PROFILE ||
	       FS_SYSTEM_INODE_MIN_RESERVE >= AGENT_META_STORE_BANKS,
	       "SYSTEM inode reserve must fund both metadata banks");
#undef AGENT_META_STORE_MAX_INDEX_BLOCKS
#undef AGENT_META_STORE_MAX_DATA_BLOCKS

static struct agent_file_scope_state
	agent_file_scope_states[AGENT_META_WRITEBACK_SCOPE_MAX];
static struct agent_file_writeback_capture
	agent_file_writeback_capture[AGENT_META_WRITEBACK_SCOPE_MAX];
static struct agent_meta_store agent_meta_store_buf;
static struct agent_meta_persist_state agent_meta_persist;
static struct agent_meta_store
	agent_meta_bank_shadow[AGENT_META_STORE_BANKS];
static struct agent_meta_record agent_meta_sort_record;
static int agent_meta_sort_index[AGENT_FILE_META_MAX];
static uint agent_meta_bank_write_limit[AGENT_META_STORE_BANKS];
static int agent_meta_bank_shadow_valid[AGENT_META_STORE_BANKS];
static uchar agent_meta_stale_slots[AGENT_META_STALE_BYTES];
static int agent_file_loaded;
static int agent_meta_store_busy;
static struct wait_queue agent_meta_submit_waiters;
static void *agent_meta_sync_submit_owner;
static uint64 agent_meta_next_job_id;
static uint64 agent_meta_submit_next_ticket;
static uint64 agent_meta_submit_serving_ticket;
static int agent_meta_store_active_bank;
static uint64 agent_meta_store_generation;
static int agent_meta_store_failed_closed;
static int agent_meta_store_recovery_required;
static uint64 agent_file_writeback_next_tick;
static uint agent_file_writeback_owner_cursor;

static int agent_file_persist(void);
static int agent_file_persist_system(void);
static int agent_meta_persist_drain_owner(uint owner);

static uint64
agent_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static int
agent_scope_valid(uint scope_id)
{
	return scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
	       scope_id < FS_OWNER_SCOPE_FLAG;
}

static int
agent_object_scope_valid(uint scope_id)
{
	return scope_id == VFS_SCOPE_SYSTEM || agent_scope_valid(scope_id);
}

static uint64
agent_hash_mix(uint64 h, uint64 v)
{
	for (int i = 0; i < 8; i++) {
		h ^= (uchar)(v & 0xff);
		h *= 1099511628211ULL;
		v >>= 8;
	}
	return h;
}

static uint64
agent_hash_bytes(uint64 h, char *buf, int n)
{
	for (int i = 0; i < n; i++) {
		h ^= (uchar)buf[i];
		h *= 1099511628211ULL;
	}
	return h;
}

static struct agent_file_scope_state *
agent_file_scope_state_locked(uint scope_id, int create)
{
	struct agent_file_scope_state *free_state = 0;

	if (scope_id != VFS_SCOPE_SYSTEM && !agent_scope_valid(scope_id))
		scope_id = VFS_SCOPE_SYSTEM;
	for (int i = 0; i < AGENT_META_WRITEBACK_SCOPE_MAX; i++) {
		struct agent_file_scope_state *state =
			&agent_file_scope_states[i];

		if (state->used && state->scope_id == scope_id)
			return state;
		if (!state->used && free_state == 0)
			free_state = state;
	}
	if (!create || free_state == 0)
		return 0;
	memset(free_state, 0, sizeof(*free_state));
	free_state->used = 1;
	free_state->scope_id = scope_id;
	return free_state;
}

static struct inode *
agent_metadata_store_lookup_bank(char *name, int create)
{
	struct inode *ip;
	struct vfs_cred kernel_cred;
	int status = FS_LOOKUP_ERROR;

	ip = namei_scope_status(name, VFS_POLICY_KERNEL_PRIVATE,
				VFS_SCOPE_NONE, &status);
	if (ip != 0) {
		ivalid(ip);
		if (ip->type == T_FILE && vfs_inode_label_valid(ip) &&
		    ip->vfs_policy == VFS_POLICY_KERNEL_PRIVATE)
			return ip;
		iput(ip);
		return 0;
	}
	if (!create || status != FS_LOOKUP_ABSENT)
		return 0;
	vfs_cred_kernel(&kernel_cred);
	return fs_create(name, T_FILE, 0, &kernel_cred,
			 VFS_POLICY_KERNEL_PRIVATE);
}
void
agent_metadata_store_init(void)
{
	memset(agent_file_scope_states, 0, sizeof(agent_file_scope_states));
	memset(agent_file_writeback_capture, 0,
	       sizeof(agent_file_writeback_capture));
	memset(&agent_meta_store_buf, 0, sizeof(agent_meta_store_buf));
	memset(&agent_meta_persist, 0, sizeof(agent_meta_persist));
	memset(agent_meta_bank_shadow, 0, sizeof(agent_meta_bank_shadow));
	memset(agent_meta_bank_write_limit, 0,
	       sizeof(agent_meta_bank_write_limit));
	memset(agent_meta_bank_shadow_valid, 0,
	       sizeof(agent_meta_bank_shadow_valid));
	memset(agent_meta_stale_slots, 0, sizeof(agent_meta_stale_slots));
	agent_file_loaded = 0;
	agent_meta_store_busy = 0;
	wait_queue_init(&agent_meta_submit_waiters, WAIT_REASON_AGENT_META);
	agent_meta_sync_submit_owner = 0;
	agent_meta_next_job_id = 1;
	agent_meta_submit_next_ticket = 1;
	agent_meta_submit_serving_ticket = 1;
	agent_meta_store_active_bank = -1;
	agent_meta_store_generation = 0;
	agent_meta_store_failed_closed = 0;
	agent_meta_store_recovery_required = 0;
	agent_file_writeback_next_tick = 0;
	agent_file_writeback_owner_cursor = 0;
}
static void agent_file_writeback_mark(uint scope_id)
{
	struct agent_file_scope_state *state;
	uint64 now = agent_ticks();
	int enabled = intr_save();

	state = agent_file_scope_state_locked(scope_id, 1);
	if (state == 0) {
		/* Fail closed: force the scanner to reconstruct untracked state. */
		intr_restore(enabled);
		agent_file_request_scan();
		return;
	}
	if (state->dirty_generation != state->durable_generation)
		state->coalesced_count++;
	else
		state->due_tick = now + AGENT_META_WRITEBACK_COALESCE_TICKS;
	state->dirty_generation++;
	if (state->dirty_generation == 0)
		state->dirty_generation = 1;
	state->request_count++;
	intr_restore(enabled);
}

static void agent_file_writeback_expedite(uint scope_id)
{
	struct agent_file_scope_state *state;
	uint64 now = agent_ticks();
	int enabled = intr_save();

	state = agent_file_scope_state_locked(scope_id, 0);
	if (state != 0 &&
	    state->dirty_generation != state->durable_generation) {
		state->due_tick = now;
		if (agent_file_writeback_next_tick > now)
			agent_file_writeback_next_tick = now;
	}
	intr_restore(enabled);
}

static int agent_file_writeback_scope_pending(uint scope_id)
{
	struct agent_file_scope_state *state;
	int pending;
	int enabled = intr_save();

	state = agent_file_scope_state_locked(scope_id, 0);
	pending = state &&
		  state->dirty_generation != state->durable_generation;
	intr_restore(enabled);
	return pending;
}

static int agent_file_writeback_scope_busy(uint scope_id)
{
	return agent_meta_persist.phase != AGENT_META_PERSIST_IDLE &&
	       FS_OWNER_IS_SCOPE(agent_meta_persist.owner) &&
	       FS_OWNER_SCOPE_ID(agent_meta_persist.owner) == scope_id;
}

static int agent_file_writeback_due(uint64 now)
{
	int due = 0;
	int enabled = intr_save();

	if (now < agent_file_writeback_next_tick)
		goto out;
	for (int i = 0; i < AGENT_META_WRITEBACK_SCOPE_MAX; i++) {
		struct agent_file_scope_state *state =
			&agent_file_scope_states[i];

		if (state->used &&
		    state->dirty_generation != state->durable_generation &&
		    now >= state->due_tick) {
			due = 1;
			break;
		}
	}
out:
	intr_restore(enabled);
	return due;
}

static uint64 agent_file_writeback_scope_target(uint scope_id)
{
	struct agent_file_scope_state *state;
	uint64 target = 0;
	int enabled = intr_save();

	state = agent_file_scope_state_locked(scope_id, 0);
	if (state != 0 &&
	    state->dirty_generation != state->durable_generation)
		target = state->dirty_generation;
	intr_restore(enabled);
	return target;
}

static int agent_file_writeback_generation_reached(uint64 generation,
						    uint64 target)
{
	return target == 0 || (long)(generation - target) >= 0;
}

static int agent_file_writeback_scope_reached(uint scope_id, uint64 target)
{
	struct agent_file_scope_state *state;
	int reached = target == 0;
	int enabled = intr_save();

	state = agent_file_scope_state_locked(scope_id, 0);
	if (state != 0)
		reached = agent_file_writeback_generation_reached(
			state->durable_generation, target);
	intr_restore(enabled);
	return reached;
}

// Select the inducing persistent principal in round-robin order. A global
// snapshot may coalesce several scopes, but no one workflow is permanently
// chosen to sponsor every later writeback.
static uint agent_file_writeback_owner(uint64 now)
{
	uint owner = FS_OWNER_SYSTEM;
	int enabled = intr_save();

	for (uint scanned = 0; scanned < AGENT_META_WRITEBACK_SCOPE_MAX;
	     scanned++) {
		uint i = (agent_file_writeback_owner_cursor + scanned) %
			 AGENT_META_WRITEBACK_SCOPE_MAX;
		struct agent_file_scope_state *state =
			&agent_file_scope_states[i];

		if (!state->used ||
		    state->dirty_generation == state->durable_generation ||
		    now < state->due_tick)
			continue;
		owner = state->scope_id == VFS_SCOPE_SYSTEM ?
			FS_OWNER_SYSTEM : FS_OWNER_SCOPE(state->scope_id);
		agent_file_writeback_owner_cursor =
			(i + 1) % AGENT_META_WRITEBACK_SCOPE_MAX;
		break;
	}
	intr_restore(enabled);
	return owner;
}

static void agent_file_writeback_capture_state(uint scope_id)
{
	int enabled = intr_save();

	for (int i = 0; i < AGENT_META_WRITEBACK_SCOPE_MAX; i++) {
		struct agent_file_scope_state *state =
			&agent_file_scope_states[i];

		agent_file_writeback_capture[i].scope_id =
			state->used && state->scope_id == scope_id ?
				scope_id : VFS_SCOPE_NONE;
		agent_file_writeback_capture[i].dirty_generation =
			state->used && state->scope_id == scope_id ?
				state->dirty_generation : 0;
	}
	intr_restore(enabled);
}

static uint64 agent_file_writeback_rest_deadline(uint64 started_tick,
						 uint64 now)
{
	uint64 rest = AGENT_META_WRITEBACK_COALESCE_TICKS;

	(void)started_tick;
	if (rest > ~0ULL - now)
		return ~0ULL;
	return now + rest;
}

static void agent_file_writeback_complete(uint64 started_tick)
{
	uint64 now = agent_ticks();
	int enabled = intr_save();

	for (int i = 0; i < AGENT_META_WRITEBACK_SCOPE_MAX; i++) {
		struct agent_file_scope_state *state =
			&agent_file_scope_states[i];
		struct agent_file_writeback_capture *capture =
			&agent_file_writeback_capture[i];

		if (!state->used || capture->scope_id != state->scope_id ||
		    capture->dirty_generation == 0 ||
		    agent_file_writeback_generation_reached(
			state->durable_generation,
			capture->dirty_generation))
			continue;
		state->durable_generation = capture->dirty_generation;
		if (state->dirty_generation == state->durable_generation) {
			state->due_tick = 0;
			// Public telemetry counts completed coalescing batches. An
			// intermediate snapshot advances the durable barrier but
			// remains part of the still-open batch.
			state->commit_count++;
		}
	}
	agent_file_writeback_next_tick =
		agent_file_writeback_rest_deadline(started_tick, now);
	intr_restore(enabled);
}

static void agent_file_writeback_defer(uint64 started_tick)
{
	uint64 now = agent_ticks();
	int enabled = intr_save();

	agent_file_writeback_next_tick =
		agent_file_writeback_rest_deadline(started_tick, now);
	intr_restore(enabled);
}

static void agent_file_scope_state_retire(uint scope_id)
{
	int enabled = intr_save();

	for (int i = 0; i < AGENT_META_WRITEBACK_SCOPE_MAX; i++) {
		struct agent_file_scope_state *state =
			&agent_file_scope_states[i];

		if (state->used && state->scope_id == scope_id &&
		    state->dirty_generation == state->durable_generation) {
			memset(state, 0, sizeof(*state));
			break;
		}
	}
	intr_restore(enabled);
}

static void agent_file_writeback_info(uint scope_id, struct agent_info *info)
{
	struct agent_file_scope_state *state;
	int enabled;

	if (info == 0)
		return;
	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG)
		return;
	enabled = intr_save();
	state = agent_file_scope_state_locked(scope_id, 0);
	if (state) {
		info->metadata_writeback_dirty = state->dirty_generation;
		info->metadata_writeback_durable = state->durable_generation;
		info->metadata_writeback_requests = state->request_count;
		info->metadata_writeback_coalesced = state->coalesced_count;
		info->metadata_writeback_commits = state->commit_count;
		info->metadata_writeback_pending =
			state->dirty_generation != state->durable_generation ||
			agent_file_writeback_scope_busy(scope_id);
	}
	intr_restore(enabled);
}

/*
 * The COW metadata store has one physical commit lane. Enter that lane before
 * changing the in-memory image, so a temporary lack of background tokens for
 * an older mirror is never reported as failure after a new mutation. Dropping
 * the transaction lets scheduler maintenance advance the immutable job; the
 * caller reacquires it before performing any lookup or validation.
 */
static int agent_meta_submit_wait_locked(void)
{
	void *token = agent_metadata_txn_token();
	uint64 ticket;

	if (!agent_metadata_txn_owned(1))
		panic("Agent metadata submit invariant");
	ticket = agent_meta_submit_next_ticket++;
	for (;;) {
		int enabled = intr_save();

		if (ticket == agent_meta_submit_serving_ticket &&
		    agent_meta_persist.phase == AGENT_META_PERSIST_IDLE &&
		    (agent_meta_sync_submit_owner == 0 ||
		     agent_meta_sync_submit_owner == token) &&
		    agent_metadata_reload_available()) {
			agent_meta_submit_serving_ticket++;
			wait_queue_wake_all(&agent_meta_submit_waiters);
			intr_restore(enabled);
			return 1;
		}
		/*
		 * Keep interrupts disabled from the failed condition check through
		 * transaction release and queue insertion. Otherwise the lane owner
		 * can publish its wake between unlock and sleep, stranding this
		 * ticket even though its condition is already true.
		 */
		agent_metadata_txn_unlock();
		/*
		 * A ticket may not be abandoned: doing so would strand every later
		 * submitter. Process teardown therefore waits for this bounded COW
		 * lane turn, then observes the exit request at the syscall boundary.
		 */
		if (wait_queue_sleep_irq_uninterruptible(
			    &agent_meta_submit_waiters) !=
		    WAIT_QUEUE_OK)
			panic("metadata submit wait failed");
		intr_restore(enabled);
		agent_metadata_txn_relock_uninterruptible();
	}
}

static int agent_meta_reload_wait_locked(void)
{
	if (!agent_metadata_txn_owned(1))
		panic("Agent metadata reload wait invariant");
	for (;;) {
		int enabled = intr_save();
		int wait_result;

		if (agent_metadata_reload_available()) {
			intr_restore(enabled);
			return 1;
		}
		/*
		 * This is the same atomic condition-wait protocol as submit: the
		 * reload owner cannot clear the gate and wake us until our thread is
		 * visible on the condition queue.
		 */
		agent_metadata_txn_unlock();
		wait_result =
			wait_queue_sleep_irq(&agent_meta_submit_waiters);
		intr_restore(enabled);
		if (wait_result != WAIT_QUEUE_OK)
			return 0;
		if (!agent_metadata_txn_lock(1))
			return 0;
	}
}

/*
 * A synchronous submitter owns an immutable COW image, not the global
 * in-memory metadata lock. Drop every recursive transaction level while the
 * I/O governor services debt, then restore the caller's lock depth. The job id
 * tells the caller whether maintenance completed or aborted that exact image
 * while it slept.
 */
static int agent_meta_persist_checkpoint_unlocked(uint64 job_id,
						  int *same_job)
{
	void *token = agent_metadata_txn_token();
	int depth;
	int checkpoint;

	if (same_job == 0 || job_id == 0 ||
	    !agent_metadata_txn_owned(0) ||
	    agent_meta_sync_submit_owner != token)
		panic("metadata persist checkpoint invariant");
	depth = agent_metadata_txn_depth();
	checkpoint = agent_metadata_txn_checkpoint_unlocked();
	if (!agent_metadata_txn_owned(depth))
		panic("metadata checkpoint depth");
	*same_job = agent_meta_persist.job_id == job_id;
	return checkpoint;
}

static uint agent_meta_persist_segment_end(uint offset)
{
	uint end = (offset / BSIZE + 1) * BSIZE;

	return MIN(end, (uint)sizeof(struct agent_meta_store));
}

static int agent_meta_persist_block_dirty(uint offset)
{
	uint block = offset / BSIZE;

	if (block >= MAXFILE)
		panic("metadata persist block range");
	return (agent_meta_persist.dirty_blocks[block / 8] &
		(1U << (block % 8))) != 0;
}

static void agent_meta_persist_force_full_write(void)
{
	memset(agent_meta_persist.dirty_blocks, 0xff,
	       sizeof(agent_meta_persist.dirty_blocks));
	agent_meta_bank_shadow_valid[agent_meta_persist.target_bank] = 0;
}

static uint agent_meta_store_write_limit(uint store_bytes)
{
	if (store_bytes <= sizeof(struct agent_meta_store_header))
		return store_bytes;
	return agent_meta_persist_segment_end(store_bytes - 1);
}

static void agent_meta_persist_prepare_blocks(struct agent_meta_store *store,
					      int target_bank)
{
	uint offset = sizeof(store->header);
	uint write_limit = agent_meta_store_write_limit(
		agent_meta_persist.store_bytes);

	memset(agent_meta_persist.dirty_blocks, 0,
	       sizeof(agent_meta_persist.dirty_blocks));
	agent_meta_persist.write_limit = write_limit;
	while (offset < write_limit) {
		uint end = agent_meta_persist_segment_end(offset);
		uint block = offset / BSIZE;

		if (!agent_meta_bank_shadow_valid[target_bank] ||
		    agent_meta_bank_write_limit[target_bank] < end ||
		    memcmp((char *)&agent_meta_bank_shadow[target_bank] + offset,
			   (char *)store + offset, end - offset) != 0) {
			agent_meta_persist.dirty_blocks[block / 8] |=
				1U << (block % 8);
		}
		offset = end;
	}
}

static void agent_meta_bank_shadow_install(struct agent_meta_store *store,
					   int target_bank,
					   uint write_limit)
{
	if (store == 0 || target_bank < 0 ||
	    target_bank >= AGENT_META_STORE_BANKS ||
	    write_limit > sizeof(*store))
		panic("metadata shadow range");
	memset(&agent_meta_bank_shadow[target_bank], 0,
	       sizeof(agent_meta_bank_shadow[target_bank]));
	memmove(&agent_meta_bank_shadow[target_bank], store, write_limit);
	agent_meta_bank_write_limit[target_bank] = write_limit;
	agent_meta_bank_shadow_valid[target_bank] = 1;
}

static int agent_meta_store_bytes(uint64 count, uint *bytes)
{
	uint64 total;

	if (bytes == 0 || count > AGENT_FILE_META_MAX)
		return -1;
	total = sizeof(struct agent_meta_store_header) +
		count * sizeof(struct agent_meta_record);
	if (total > sizeof(struct agent_meta_store) ||
	    total > MAXFILE * BSIZE)
		return -1;
	*bytes = total;
	return 0;
}

static uint64 agent_meta_store_hash(struct agent_meta_store *store)
{
	uint64 h = 1469598103934665603ULL;
	uint bytes;

	if (store == 0 ||
	    agent_meta_store_bytes(store->header.count, &bytes) < 0)
		return 0;
	h = agent_hash_mix(h, store->header.magic);
	h = agent_hash_mix(h, store->header.version);
	h = agent_hash_mix(h, store->header.count);
	h = agent_hash_mix(h, store->header.generation);
	return agent_hash_bytes(
		h, (char *)store->records,
		bytes - sizeof(struct agent_meta_store_header));
}

static int agent_meta_store_enter(void)
{
	int enabled = intr_save();
	int entered = 0;

	if (!agent_meta_store_busy) {
		agent_meta_store_busy = 1;
		entered = 1;
	}
	intr_restore(enabled);
	return entered;
}

static void agent_meta_store_leave(void)
{
	int enabled = intr_save();

	if (!agent_meta_store_busy)
		panic("Agent metadata store lock invariant");
	agent_meta_store_busy = 0;
	intr_restore(enabled);
}

static char *agent_meta_store_name(int bank)
{
	if (bank == 0)
		return AGENT_META_STORE_NAME_0;
	if (bank == 1)
		return AGENT_META_STORE_NAME_1;
	return 0;
}

int agent_file_is_meta_store_name(char *path)
{
	return path &&
	       (strncmp(path, AGENT_META_STORE_NAME_0, DIRSIZ) == 0 ||
		strncmp(path, AGENT_META_STORE_NAME_1, DIRSIZ) == 0);
}

static int agent_meta_record_strings_valid(struct agent_file_meta *meta)
{
	return meta != 0 && meta->physical_name[0] != 0 &&
	       meta->physical_name[sizeof(meta->physical_name) - 1] == 0 &&
	       meta->logical_path[sizeof(meta->logical_path) - 1] == 0 &&
	       meta->project[sizeof(meta->project) - 1] == 0 &&
	       meta->workflow[sizeof(meta->workflow) - 1] == 0 &&
	       meta->run_id[sizeof(meta->run_id) - 1] == 0 &&
	       meta->stage[sizeof(meta->stage) - 1] == 0 &&
	       meta->kind[sizeof(meta->kind) - 1] == 0 &&
	       meta->status[sizeof(meta->status) - 1] == 0 &&
	       meta->summary[sizeof(meta->summary) - 1] == 0;
}

static int agent_meta_store_records_valid(struct agent_meta_store *store)
{
	for (uint64 i = 0; i < store->header.count; i++) {
		struct agent_meta_record *record = &store->records[i];
		int owned = 0;
		int limit;

		if (record->slot >= AGENT_FILE_META_MAX ||
		    record->meta.used != 1 || record->meta.fid <= 0 ||
		    !agent_object_scope_valid(record->scope_id) ||
		    !agent_meta_record_strings_valid(&record->meta) ||
		    record->meta.update_mask != 0 ||
		    (record->meta.flags & AGENT_FILE_META_F_PERSIST) == 0 ||
		    (record->meta.flags &
		     ~(AGENT_FILE_META_F_PERSIST |
		       AGENT_FILE_META_F_AUTOSCAN)) != 0)
			return 0;
		limit = record->scope_id == VFS_SCOPE_SYSTEM ?
			AGENT_FILE_SYSTEM_LIMIT : AGENT_FILE_SCOPE_LIMIT;
		for (uint64 j = 0; j < i; j++) {
			struct agent_meta_record *prior = &store->records[j];

			if (prior->slot == record->slot)
				return 0;
			if (prior->scope_id != record->scope_id)
				continue;
			owned++;
			if (prior->meta.fid == record->meta.fid ||
			    strncmp(prior->meta.physical_name,
				    record->meta.physical_name,
				    sizeof(record->meta.physical_name)) == 0 ||
			    (record->meta.dev != 0 &&
			     prior->meta.dev == record->meta.dev &&
			     prior->meta.inum == record->meta.inum &&
			     prior->meta.incarnation == record->meta.incarnation))
				return 0;
		}
		if (owned >= limit)
			return 0;
	}
	return 1;
}

/*
 * readi() deliberately returns the prefix completed before the I/O governor
 * asks the filesystem atomic section to unwind. Pay that debt only after the
 * atomic section has ended, then continue from the committed offset. A
 * temporary interruption must not be confused with durable bank corruption.
 */
static int agent_meta_store_read_exact(struct inode *ip,
				       const struct vfs_cred *cred,
				       char *dst, uint off, uint length)
{
	uint done = 0;

	while (done < length) {
		int n = readi(ip, cred, 0, (uint64)(dst + done),
			      off + done, length - done);
		int checkpoint;

		if (n < 0)
			return proc_thread_exit_requested() ?
				       AGENT_META_BANK_INTERRUPTED :
				       AGENT_META_BANK_CORRUPT;
		if (n == 0 || (uint)n > length - done)
			return AGENT_META_BANK_CORRUPT;
		done += n;
		checkpoint = agent_metadata_txn_checkpoint_unlocked();
		if (!agent_metadata_reload_is_current() ||
		    !agent_meta_store_busy)
			return AGENT_META_BANK_INTERRUPTED;
		if (checkpoint == BIO_CHECKPOINT_INTERRUPTED ||
		    checkpoint == BIO_CHECKPOINT_DEFERRED)
			return AGENT_META_BANK_INTERRUPTED;
		if (checkpoint < 0)
			return AGENT_META_BANK_INTERRUPTED;
	}
	return AGENT_META_BANK_VALID;
}

static int agent_meta_store_read_bank(int bank,
				      struct agent_meta_store *store,
				      uint64 *generation,
				      uint64 *payload_hash)
{
	struct inode *ip;
	struct vfs_cred kernel_cred;
	char *name = agent_meta_store_name(bank);
	uint64 count;
	uint64 expected_generation;
	uint store_bytes;
	int lookup_status = FS_LOOKUP_ERROR;
	int read_status;

	if (store == 0 || generation == 0 || payload_hash == 0 || name == 0)
		return AGENT_META_BANK_CORRUPT;
	ip = namei_scope_status(name, VFS_POLICY_KERNEL_PRIVATE,
				VFS_SCOPE_NONE, &lookup_status);
	if (ip == 0)
		return lookup_status == FS_LOOKUP_ABSENT ?
			       AGENT_META_BANK_ABSENT :
			       AGENT_META_BANK_INTERRUPTED;
	ivalid(ip);
	if (ip->type != T_FILE || !vfs_inode_label_valid(ip) ||
	    ip->vfs_policy != VFS_POLICY_KERNEL_PRIVATE) {
		iput(ip);
		return AGENT_META_BANK_CORRUPT;
	}
	vfs_cred_kernel(&kernel_cred);
	memset(store, 0, sizeof(*store));
	read_status = agent_meta_store_read_exact(
		ip, &kernel_cred, (char *)&store->header, 0,
		sizeof(store->header));
	if (read_status != AGENT_META_BANK_VALID) {
		iput(ip);
		return read_status;
	}
	if (store->header.magic != AGENT_META_STORE_MAGIC ||
	    store->header.version != AGENT_META_STORE_VERSION ||
	    store->header.generation == 0 ||
	    agent_meta_store_bytes(store->header.count, &store_bytes) < 0 ||
	    ip->size < store_bytes) {
		iput(ip);
		return AGENT_META_BANK_CORRUPT;
	}
	count = store->header.count;
	expected_generation = store->header.generation;
	read_status = agent_meta_store_read_exact(
		ip, &kernel_cred, (char *)store + sizeof(store->header),
		sizeof(store->header), store_bytes - sizeof(store->header));
	if (read_status != AGENT_META_BANK_VALID) {
		iput(ip);
		return read_status;
	}
	if (store->header.count != count ||
	    store->header.generation != expected_generation ||
	    store->header.payload_hash != agent_meta_store_hash(store) ||
	    !agent_meta_store_records_valid(store)) {
		iput(ip);
		return AGENT_META_BANK_CORRUPT;
	}
	*generation = store->header.generation;
	*payload_hash = store->header.payload_hash;
	iput(ip);
	return AGENT_META_BANK_VALID;
}

static int agent_meta_store_select(struct agent_meta_store *store,
				   int *selected_bank,
				   uint64 *selected_generation,
				   int *needs_recovery)
{
	uint64 generations[AGENT_META_STORE_BANKS];
	uint64 hashes[AGENT_META_STORE_BANKS];
	int status[AGENT_META_STORE_BANKS];
	int selected = -1;

	if (needs_recovery == 0)
		return -1;
	*needs_recovery = 0;
	for (int bank = 0; bank < AGENT_META_STORE_BANKS; bank++)
		status[bank] = agent_meta_store_read_bank(
			bank, store, &generations[bank], &hashes[bank]);
	for (int bank = 0; bank < AGENT_META_STORE_BANKS; bank++)
		if (status[bank] == AGENT_META_BANK_INTERRUPTED)
			return AGENT_META_BANK_INTERRUPTED;
	for (int bank = 0; bank < AGENT_META_STORE_BANKS; bank++) {
		if (status[bank] != AGENT_META_BANK_VALID)
			continue;
		if (selected < 0 || generations[bank] > generations[selected])
			selected = bank;
		else if (generations[bank] == generations[selected]) {
			if (hashes[bank] != hashes[selected])
				return -1;
			if (bank < selected)
				selected = bank;
		}
	}
	if (selected < 0) {
		if (status[0] == AGENT_META_BANK_ABSENT &&
		    status[1] == AGENT_META_BANK_ABSENT)
			return AGENT_META_BANK_ABSENT;
		return AGENT_META_BANK_CORRUPT;
	}
	for (int bank = 0; bank < AGENT_META_STORE_BANKS; bank++)
		if (status[bank] != AGENT_META_BANK_VALID) {
			agent_meta_bank_shadow_valid[bank] = 0;
			agent_meta_bank_write_limit[bank] = 0;
		}
	if (status[0] != AGENT_META_BANK_VALID ||
	    status[1] != AGENT_META_BANK_VALID ||
	    generations[0] != generations[1])
		*needs_recovery = 1;
	for (int bank = 0; bank < AGENT_META_STORE_BANKS; bank++) {
		uint64 generation;
		uint64 payload_hash;
		uint store_bytes;

		if (bank == selected || status[bank] != AGENT_META_BANK_VALID)
			continue;
		int read_status = agent_meta_store_read_bank(
			bank, store, &generation, &payload_hash);

		if (read_status == AGENT_META_BANK_INTERRUPTED)
			return AGENT_META_BANK_INTERRUPTED;
		if (read_status != AGENT_META_BANK_VALID ||
		    generation != generations[bank] ||
		    payload_hash != hashes[bank] ||
		    agent_meta_store_bytes(store->header.count,
					   &store_bytes) < 0)
			return -1;
		agent_meta_bank_shadow_install(
			store, bank, agent_meta_store_write_limit(store_bytes));
	}
	{
		int read_status = agent_meta_store_read_bank(
			selected, store, selected_generation, &hashes[selected]);

		if (read_status == AGENT_META_BANK_INTERRUPTED)
			return AGENT_META_BANK_INTERRUPTED;
		if (read_status != AGENT_META_BANK_VALID)
			return AGENT_META_BANK_CORRUPT;
	}
	{
		uint store_bytes;

		if (*selected_generation != generations[selected] ||
		    agent_meta_store_bytes(store->header.count, &store_bytes) < 0)
			return AGENT_META_BANK_CORRUPT;
		agent_meta_bank_shadow_install(
			store, selected,
			agent_meta_store_write_limit(store_bytes));
	}
	if (status[0] == AGENT_META_BANK_VALID &&
	    status[1] == AGENT_META_BANK_VALID &&
	    generations[0] == generations[1] &&
	    memcmp(&agent_meta_bank_shadow[0],
		   &agent_meta_bank_shadow[1],
		   sizeof(struct agent_meta_store)) != 0)
		return AGENT_META_BANK_CORRUPT;
	*selected_bank = selected;
	return AGENT_META_BANK_VALID;
}

static int
agent_meta_store_missing(const struct agent_metadata_apply_result *apply)
{
	for (uint i = 0; i < AGENT_META_STALE_BYTES; i++)
		if (apply->missing_slots[i] != 0)
			return 1;
	return 0;
}

static int
agent_meta_store_classify_missing(const struct agent_meta_store *store,
				  int reload_one_scope, uint reload_scope,
				  struct agent_metadata_apply_result *apply)
{
	int orphan_stale = 0;

	if (reload_one_scope) {
		if (apply->layout_changed || agent_meta_store_missing(apply))
			agent_file_writeback_mark(reload_scope);
		return 0;
	}
	for (uint i = 0; i < store->header.count; i++) {
		const struct agent_meta_record *record = &store->records[i];

		if ((apply->missing_slots[record->slot / 8] &
		     (1U << (record->slot % 8))) == 0)
			continue;
		if (record->scope_id == VFS_SCOPE_SYSTEM ||
		    vfs_scope_retained(record->scope_id)) {
			agent_file_writeback_mark(record->scope_id);
			continue;
		}
		agent_meta_stale_slots[record->slot / 8] |=
			1U << (record->slot % 8);
		orphan_stale = 1;
	}
	return orphan_stale;
}

static int
agent_file_load_snapshot(int force, uint reload_scope)
{
	struct agent_meta_store *store = &agent_meta_store_buf;
	struct agent_metadata_apply_result apply;
	uint store_bytes;
	int selected_bank;
	uint64 selected_generation;
	int reload_one_scope;
	int needs_recovery = 0;
	int orphan_stale = 0;
	int select_status;
	int store_locked = 0;
	int reload_owned = 0;
	int result = -1;

	if (!agent_metadata_txn_lock(1))
		return -1;
	if (agent_meta_store_failed_closed && !force)
		goto out_txn;
	if (!force && agent_file_loaded) {
		if (agent_meta_store_recovery_required) {
			if (agent_file_persist_system() < 0 ||
			    agent_meta_store_recovery_required)
				goto out_txn;
		}
		result = agent_metadata_objects_live_count();
		goto out_txn;
	}
	if (!agent_metadata_reload_claim())
		goto out_txn;
	reload_owned = 1;
	if (!agent_meta_store_enter())
		goto out_txn;
	store_locked = 1;
	select_status = agent_meta_store_select(
		store, &selected_bank, &selected_generation, &needs_recovery);
	if (select_status != AGENT_META_BANK_VALID) {
		agent_meta_store_leave();
		store_locked = 0;
		agent_meta_store_failed_closed =
			select_status == AGENT_META_BANK_CORRUPT;
		if (select_status == AGENT_META_BANK_ABSENT)
			agent_meta_store_recovery_required = 0;
		if (select_status == AGENT_META_BANK_INTERRUPTED || force ||
		    select_status == AGENT_META_BANK_CORRUPT)
			goto out_txn;
		agent_metadata_objects_clear_catalog();
		agent_meta_store_active_bank = -1;
		agent_meta_store_generation = 0;
		agent_file_loaded = 1;
		result = 0;
		goto out_txn;
	}
	agent_meta_store_failed_closed = 0;
	agent_meta_store_recovery_required = needs_recovery;
	if (agent_meta_store_bytes(store->header.count, &store_bytes) < 0)
		goto out_store;
	agent_meta_bank_shadow_install(
		store, selected_bank, agent_meta_store_write_limit(store_bytes));
	reload_one_scope = force && agent_file_loaded;
	if (reload_one_scope && !agent_scope_valid(reload_scope))
		goto out_store;
	if (!reload_one_scope)
		memset(agent_meta_stale_slots, 0,
		       sizeof(agent_meta_stale_slots));
	if (agent_metadata_objects_apply_snapshot(
		    store->records, store->header.count, reload_one_scope,
		    reload_scope, &apply) < 0)
		goto out_store;
	orphan_stale = agent_meta_store_classify_missing(
		store, reload_one_scope, reload_scope, &apply);
	agent_meta_store_active_bank = selected_bank;
	agent_meta_store_generation = selected_generation;
	agent_file_loaded = 1;
	agent_meta_store_leave();
	store_locked = 0;
	if (orphan_stale) {
		agent_meta_store_recovery_required = 1;
		agent_file_writeback_mark(VFS_SCOPE_SYSTEM);
		agent_file_writeback_expedite(VFS_SCOPE_SYSTEM);
	}
	if (needs_recovery && !orphan_stale) {
		agent_file_writeback_mark(VFS_SCOPE_SYSTEM);
		agent_file_writeback_expedite(VFS_SCOPE_SYSTEM);
	}
	if ((needs_recovery || orphan_stale) &&
	    agent_file_persist_system() < 0)
		goto out_txn;
	result = apply.used;
	goto out_txn;

out_store:
	if (store_locked)
		agent_meta_store_leave();
out_txn:
	if (reload_owned) {
		agent_metadata_reload_release();
		wait_queue_wake_all(&agent_meta_submit_waiters);
	}
	agent_metadata_txn_unlock();
	return result;
}

int
agent_metadata_store_load(void)
{
	return agent_file_load_snapshot(0, VFS_SCOPE_NONE);
}

int
agent_metadata_store_reload(uint scope_id)
{
	return agent_file_load_snapshot(1, scope_id);
}

void
agent_metadata_store_storage_init(void)
{
	if (agent_metadata_store_load() < 0) {
		agent_meta_store_failed_closed = 1;
		errorf("Agent metadata storage failed closed at boot\n");
	}
}

static int agent_meta_store_append(struct agent_meta_store *store,
				   const struct agent_meta_record *record,
				   uchar *used_slots)
{
	struct agent_meta_record copy;

	if (store->header.count >= AGENT_FILE_META_MAX)
		return -1;
	copy = *record;
	if (copy.slot >= AGENT_FILE_META_MAX ||
	    (used_slots[copy.slot / 8] & (1U << (copy.slot % 8))) != 0) {
		for (copy.slot = 0; copy.slot < AGENT_FILE_META_MAX;
		     copy.slot++)
			if ((used_slots[copy.slot / 8] &
			     (1U << (copy.slot % 8))) == 0)
				break;
		if (copy.slot >= AGENT_FILE_META_MAX)
			return -1;
	}
	store->records[store->header.count++] = copy;
	used_slots[copy.slot / 8] |= 1U << (copy.slot % 8);
	return 0;
}

static void agent_meta_store_sort(struct agent_meta_store *store)
{
	uint target = 0;

	for (int slot = 0; slot < AGENT_FILE_META_MAX; slot++)
		agent_meta_sort_index[slot] = -1;
	for (uint i = 0; i < store->header.count; i++)
		agent_meta_sort_index[store->records[i].slot] = i;
	for (int slot = 0; slot < AGENT_FILE_META_MAX; slot++) {
		int source = agent_meta_sort_index[slot];

		if (source < 0)
			continue;
		if ((uint)source != target) {
			agent_meta_sort_record = store->records[target];
			store->records[target] = store->records[source];
			store->records[source] = agent_meta_sort_record;
			agent_meta_sort_index[
				agent_meta_sort_record.slot] = source;
			agent_meta_sort_index[slot] = target;
		}
		target++;
	}
}

// Build a new full bank from the last durable image, replacing exactly one
// persistent scope. Dirty state owned by other workflows remains in memory
// until that workflow wins its own budgeted checkpoint.
static int agent_meta_store_build_scope(struct agent_meta_store *store,
					uint scope_id,
					uint64 *size_sequence)
{
	struct agent_meta_store *base = 0;
	uchar used_slots[(AGENT_FILE_META_MAX + 7) / 8];
	uint base_count;
	int exported;

	if (!agent_object_scope_valid(scope_id))
		return -1;
	if (agent_meta_store_active_bank >= 0) {
		if (agent_meta_store_active_bank >= AGENT_META_STORE_BANKS ||
		    !agent_meta_bank_shadow_valid[agent_meta_store_active_bank])
			return -1;
		base = &agent_meta_bank_shadow[agent_meta_store_active_bank];
		if (base->header.magic != AGENT_META_STORE_MAGIC ||
		    base->header.version != AGENT_META_STORE_VERSION ||
		    base->header.generation != agent_meta_store_generation ||
		    base->header.count > AGENT_FILE_META_MAX)
			return -1;
	}
	memset(store, 0, sizeof(*store));
	memset(used_slots, 0, sizeof(used_slots));
	store->header.magic = AGENT_META_STORE_MAGIC;
	store->header.version = AGENT_META_STORE_VERSION;
	store->header.generation = agent_meta_store_generation + 1;
	if (base != 0)
		for (uint64 i = 0; i < base->header.count; i++) {
			struct agent_meta_record *record = &base->records[i];

			if (scope_id == VFS_SCOPE_SYSTEM &&
			    (agent_meta_stale_slots[record->slot / 8] &
			     (1U << (record->slot % 8))) != 0)
				continue;
			if (record->scope_id != scope_id &&
			    agent_meta_store_append(store, record,
						    used_slots) < 0)
				return -1;
		}
	base_count = store->header.count;
	exported = agent_metadata_objects_export_scope(
		scope_id, &store->records[base_count],
		AGENT_FILE_META_MAX - base_count, size_sequence);
	if (exported < 0)
		return -1;
	for (int i = 0; i < exported; i++)
		if (agent_meta_store_append(
			    store, &store->records[base_count + i],
			    used_slots) < 0)
			return -1;
	agent_meta_store_sort(store);
	return 0;
}

static int agent_meta_shadow_has_scope(uint scope_id)
{
	struct agent_meta_store *store;

	if (agent_meta_store_active_bank < 0 ||
	    agent_meta_store_active_bank >= AGENT_META_STORE_BANKS ||
	    !agent_meta_bank_shadow_valid[agent_meta_store_active_bank])
		return 0;
	store = &agent_meta_bank_shadow[agent_meta_store_active_bank];
	for (uint64 i = 0; i < store->header.count; i++)
		if (store->records[i].scope_id == scope_id)
			return 1;
	return 0;
}

static int agent_meta_persist_target_locked(int target_bank, int force_full)
{
	struct agent_meta_store *store = &agent_meta_store_buf;
	struct inode *ip;

	if (target_bank < 0 || target_bank >= AGENT_META_STORE_BANKS)
		return -1;
	ip = agent_metadata_store_lookup_bank(
		agent_meta_store_name(target_bank), 1);
	if (ip == 0)
		return -1;
	agent_meta_persist.target_bank = target_bank;
	agent_meta_persist.target_dev = ip->dev;
	agent_meta_persist.target_inum = ip->inum;
	agent_meta_persist.target_incarnation = ip->vfs_incarnation;
	iput(ip);
	agent_meta_persist_prepare_blocks(store, target_bank);
	if (force_full)
		agent_meta_persist_force_full_write();
	agent_meta_persist.write_offset = sizeof(store->header);
	agent_meta_persist.verify_offset = sizeof(store->header);
	agent_meta_persist.verify_hash = 1469598103934665603ULL;
	agent_meta_persist.verify_hash = agent_hash_mix(
		agent_meta_persist.verify_hash, store->header.magic);
	agent_meta_persist.verify_hash = agent_hash_mix(
		agent_meta_persist.verify_hash, store->header.version);
	agent_meta_persist.verify_hash = agent_hash_mix(
		agent_meta_persist.verify_hash, store->header.count);
	agent_meta_persist.verify_hash = agent_hash_mix(
		agent_meta_persist.verify_hash, store->header.generation);
	agent_meta_persist.phase = AGENT_META_PERSIST_INVALIDATE;
	return 0;
}

static int agent_meta_store_prepare_banks_locked(void)
{
	for (int bank = 0; bank < AGENT_META_STORE_BANKS; bank++) {
		struct inode *ip = agent_metadata_store_lookup_bank(
			agent_meta_store_name(bank), 1);

		if (ip == 0)
			return -1;
		iput(ip);
	}
	return 0;
}

static int agent_meta_persist_start_locked(uint owner)
{
	struct agent_meta_store *store = &agent_meta_store_buf;
	uint store_bytes;
	uint scope_id;
	uint64 size_sequence;
	int target_bank;

	if (agent_meta_persist.phase != AGENT_META_PERSIST_IDLE)
		return 0;
	if (agent_meta_store_generation == ~0ULL ||
	    !agent_meta_store_enter())
		return -1;
	if (agent_meta_store_prepare_banks_locked() < 0) {
		agent_meta_store_leave();
		return -1;
	}
	if (owner != FS_OWNER_SYSTEM && !FS_OWNER_IS_SCOPE(owner))
		owner = FS_OWNER_SYSTEM;
	scope_id = FS_OWNER_IS_SCOPE(owner) ? FS_OWNER_SCOPE_ID(owner) :
		VFS_SCOPE_SYSTEM;
	agent_file_writeback_capture_state(scope_id);
	if (agent_meta_store_build_scope(
		    store, scope_id, &size_sequence) < 0) {
		agent_meta_store_leave();
		return -1;
	}
	agent_meta_persist.size_sequence = size_sequence;
	if (agent_meta_store_bytes(store->header.count, &store_bytes) < 0) {
		agent_meta_store_leave();
		return -1;
	}
	store->header.payload_hash = agent_meta_store_hash(store);
	target_bank = agent_meta_store_active_bank == 0 ? 1 : 0;
	agent_meta_persist.owner = owner;
	agent_meta_persist.scope_id = scope_id;
	if (agent_meta_next_job_id == 0)
		agent_meta_next_job_id = 1;
	agent_meta_persist.job_id = agent_meta_next_job_id++;
	agent_meta_persist.published = 0;
	agent_meta_persist.irrevocable = 0;
	agent_meta_persist.primary_verified = 0;
	agent_meta_persist.mirroring = 0;
	agent_meta_persist.restart_target = 0;
	agent_meta_persist.retry_failures = 0;
	agent_meta_persist.retry_tick = 0;
	agent_meta_persist.store_bytes = store_bytes;
	agent_meta_persist.expected_generation = store->header.generation;
	agent_meta_persist.expected_hash = store->header.payload_hash;
	agent_meta_persist.started_tick = agent_ticks();
	if (agent_meta_persist_target_locked(target_bank, 0) < 0) {
		agent_meta_store_leave();
		memset(&agent_meta_persist, 0, sizeof(agent_meta_persist));
		return -1;
	}
	return 0;
}

static void agent_meta_persist_abort_locked(void)
{
	if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE)
		return;
	if (!agent_meta_persist.published ||
	    agent_meta_persist.target_bank != agent_meta_store_active_bank)
		agent_meta_bank_shadow_valid[
			agent_meta_persist.target_bank] = 0;
	agent_meta_store_leave();
	memset(&agent_meta_persist, 0, sizeof(agent_meta_persist));
	wait_queue_wake_all(&agent_meta_submit_waiters);
}

static void agent_meta_persist_primary_publish_locked(void)
{
	agent_meta_bank_shadow_install(
		&agent_meta_store_buf, agent_meta_persist.target_bank,
		agent_meta_persist.write_limit);
	agent_meta_store_active_bank = agent_meta_persist.target_bank;
	agent_meta_store_generation = agent_meta_persist.expected_generation;
	agent_meta_persist.published = 1;
	agent_metadata_objects_sizes_persisted(
		agent_meta_persist.scope_id,
		agent_meta_persist.size_sequence);
	agent_file_writeback_complete(agent_meta_persist.started_tick);
}

static int agent_meta_persist_begin_mirror_locked(int force_full)
{
	int mirror_bank = agent_meta_store_active_bank == 0 ? 1 : 0;

	if (agent_meta_persist_target_locked(mirror_bank, force_full) < 0) {
		agent_meta_bank_shadow_valid[mirror_bank] = 0;
		return -1;
	}
	// Publish the phase switch only after the mirror target is fully bound.
	agent_meta_persist.mirroring = 1;
	agent_meta_persist.retry_failures = 0;
	agent_meta_persist.retry_tick = 0;
	return 0;
}

// Pre-publication errors can still be rolled back. Once the header write has
// completed, preserve the immutable job and move repeated recovery work onto
// the SYSTEM background budget; the old verified bank is never overwritten
// until the new primary has itself been verified.
static int agent_meta_persist_note_failure_locked(void)
{
	if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE)
		return -1;
	if (agent_meta_persist.retry_failures != (uint)-1)
		agent_meta_persist.retry_failures++;
	if (!agent_meta_persist.published &&
	    !agent_meta_persist.irrevocable) {
		agent_meta_persist_abort_locked();
		return -1;
	}
	if (agent_meta_persist.retry_failures <
	    AGENT_META_PERSIST_FAILURE_LIMIT)
		return 0;
	agent_meta_persist.owner = FS_OWNER_SYSTEM;
	agent_meta_persist.restart_target = 1;
	agent_meta_persist.retry_failures = 0;
	agent_meta_persist.retry_tick = 0;
	return 0;
}

// Advance one block-sized COW-bank step. The inactive bank stays invalid until
// every changed payload block has been written and checked. Header publication
// is the commit point; the same immutable image is then mirrored to the other
// bank under the inducing owner's background budget.
static int agent_meta_persist_step_locked(void)
{
	struct agent_meta_store *store = &agent_meta_store_buf;
	struct agent_meta_store_header invalid_header;
	struct agent_meta_store_header verified_header;
	struct inode *ip;
	struct vfs_cred kernel_cred;
	uint chunk;
	int result = 0;
	int n = 0;

	if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE)
		return 1;
	if (agent_meta_persist.target_bank < 0 ||
	    agent_meta_persist.target_bank >= AGENT_META_STORE_BANKS)
		panic("invalid metadata persist bank");
	if (agent_meta_persist.restart_target) {
		int target_bank = agent_meta_persist.target_bank;

		agent_meta_persist.restart_target = 0;
		if (agent_meta_persist_target_locked(target_bank, 1) < 0) {
			agent_meta_persist.restart_target = 1;
			return -1;
		}
		return 0;
	}
	if (agent_meta_persist.phase == AGENT_META_PERSIST_COMMIT) {
		if (!agent_meta_persist.mirroring) {
			if (!agent_meta_persist.primary_verified)
				return -1;
			if (agent_meta_persist_begin_mirror_locked(0) < 0)
				return -1;
			return 0;
		}
		agent_meta_store_leave();
		memset(&agent_meta_persist, 0, sizeof(agent_meta_persist));
		wait_queue_wake_all(&agent_meta_submit_waiters);
		return 1;
	}
	if (agent_meta_persist.phase == AGENT_META_PERSIST_WRITE &&
	    agent_meta_persist.write_offset < agent_meta_persist.write_limit &&
	    !agent_meta_persist_block_dirty(agent_meta_persist.write_offset)) {
		agent_meta_persist.write_offset = agent_meta_persist_segment_end(
			agent_meta_persist.write_offset);
		return 0;
	}
	if (agent_meta_persist.phase == AGENT_META_PERSIST_VERIFY_PAYLOAD &&
	    agent_meta_persist.verify_offset < agent_meta_persist.store_bytes &&
	    !agent_meta_persist_block_dirty(agent_meta_persist.verify_offset)) {
		chunk = MIN(agent_meta_persist_segment_end(
				    agent_meta_persist.verify_offset) -
				    agent_meta_persist.verify_offset,
			    agent_meta_persist.store_bytes -
				    agent_meta_persist.verify_offset);
		agent_meta_persist.verify_hash = agent_hash_bytes(
			agent_meta_persist.verify_hash,
			(char *)store + agent_meta_persist.verify_offset, chunk);
		agent_meta_persist.verify_offset += chunk;
		return 0;
	}
	ip = inode_get(agent_meta_persist.target_dev,
		       agent_meta_persist.target_inum);
	if (ip == 0)
		return -1;
	ivalid(ip);
	if (ip->type != T_FILE || !vfs_inode_label_valid(ip) ||
	    ip->vfs_policy != VFS_POLICY_KERNEL_PRIVATE ||
	    ip->vfs_incarnation != agent_meta_persist.target_incarnation) {
		iput(ip);
		return -1;
	}
	vfs_cred_kernel(&kernel_cred);
	switch (agent_meta_persist.phase) {
	case AGENT_META_PERSIST_INVALIDATE:
		memset(&invalid_header, 0, sizeof(invalid_header));
		n = writei(ip, &kernel_cred, 0, (uint64)&invalid_header, 0,
			   sizeof(invalid_header));
		if (n == (int)sizeof(invalid_header)) {
			agent_meta_persist.write_offset = sizeof(store->header);
			agent_meta_persist.phase = AGENT_META_PERSIST_WRITE;
		} else if (n == 0)
			result = AGENT_META_PERSIST_DEFERRED;
		else
			result = -1;
		break;
	case AGENT_META_PERSIST_WRITE:
		if (agent_meta_persist.write_offset >=
		    agent_meta_persist.write_limit) {
			agent_meta_persist.verify_offset =
				sizeof(store->header);
			agent_meta_persist.phase =
				AGENT_META_PERSIST_VERIFY_PAYLOAD;
			break;
		}
		chunk = agent_meta_persist_segment_end(
			agent_meta_persist.write_offset) -
			agent_meta_persist.write_offset;
		n = writei(ip, &kernel_cred, 0,
			   (uint64)((char *)store +
				    agent_meta_persist.write_offset),
			   agent_meta_persist.write_offset, chunk);
		if (n > 0) {
			agent_meta_persist.write_offset += n;
		} else if (n == 0)
			result = AGENT_META_PERSIST_DEFERRED;
		break;
	case AGENT_META_PERSIST_PUBLISH:
		n = writei(ip, &kernel_cred, 0, (uint64)&store->header, 0,
			   sizeof(store->header));
		if (n == (int)sizeof(store->header)) {
			if (!agent_meta_persist.mirroring)
				agent_meta_persist.published = 1;
			agent_meta_persist.phase =
				AGENT_META_PERSIST_VERIFY_HEADER;
		} else if (n == 0)
			result = AGENT_META_PERSIST_DEFERRED;
		else
			result = -1;
		break;
	case AGENT_META_PERSIST_VERIFY_HEADER:
		memset(&verified_header, 0, sizeof(verified_header));
		n = readi(ip, &kernel_cred, 0,
			  (uint64)&verified_header, 0,
			  sizeof(store->header));
		if (n == 0) {
			result = AGENT_META_PERSIST_DEFERRED;
			break;
		}
		if (n != (int)sizeof(store->header) ||
		    memcmp(&verified_header, &store->header,
			   sizeof(store->header)) != 0 ||
		    ip->size < agent_meta_persist.store_bytes) {
			iput(ip);
			return -1;
		}
		if (!agent_meta_persist.mirroring) {
			agent_meta_persist_primary_publish_locked();
			agent_meta_persist.primary_verified = 1;
			if (agent_meta_persist.scope_id == VFS_SCOPE_SYSTEM)
				memset(agent_meta_stale_slots, 0,
				       sizeof(agent_meta_stale_slots));
		} else {
			if (!agent_meta_persist.primary_verified) {
				iput(ip);
				return -1;
			}
			agent_meta_bank_shadow_install(
				store, agent_meta_persist.target_bank,
				agent_meta_persist.write_limit);
			agent_meta_store_recovery_required = 0;
		}
		agent_meta_persist.phase = AGENT_META_PERSIST_COMMIT;
		break;
	case AGENT_META_PERSIST_VERIFY_PAYLOAD:
		if (ip->size < agent_meta_persist.store_bytes) {
			iput(ip);
			return -1;
		}
		if (agent_meta_persist.verify_offset >=
		    agent_meta_persist.store_bytes) {
			if (agent_meta_persist.verify_hash ==
			    agent_meta_persist.expected_hash)
				agent_meta_persist.phase =
					AGENT_META_PERSIST_PUBLISH;
			else {
				iput(ip);
				return -1;
			}
			break;
		}
		chunk = MIN(BSIZE - agent_meta_persist.verify_offset % BSIZE,
			    agent_meta_persist.store_bytes -
				    agent_meta_persist.verify_offset);
		n = readi(ip, &kernel_cred, 0,
			  (uint64)agent_meta_persist.verify_block,
			  agent_meta_persist.verify_offset, chunk);
		if (n > 0) {
			if (memcmp(agent_meta_persist.verify_block,
				   (char *)store +
					   agent_meta_persist.verify_offset,
				   n) != 0) {
				iput(ip);
				return -1;
			}
			agent_meta_persist.verify_hash = agent_hash_bytes(
				agent_meta_persist.verify_hash,
				agent_meta_persist.verify_block, n);
			agent_meta_persist.verify_offset += n;
		} else if (n == 0)
			result = AGENT_META_PERSIST_DEFERRED;
		break;
	default:
		iput(ip);
		panic("invalid metadata persist phase");
	}
	iput(ip);
	if (n < 0)
		return -1;
	return result;
}

static int agent_file_persist(void)
{
	void *token = agent_metadata_txn_token();
	uint owner;
	uint scope_id;
	uint64 target_generation;
	uint step_limit = MAXFILE * 2 + 16;
	int started_here = 0;
	int status = -1;

	if (!agent_metadata_txn_lock(1))
		return -1;
	if (agent_meta_sync_submit_owner != 0 &&
	    agent_meta_sync_submit_owner != token)
		goto out;
	agent_meta_sync_submit_owner = token;
	owner = bio_current_owner();
	scope_id = FS_OWNER_IS_SCOPE(owner) ? FS_OWNER_SCOPE_ID(owner) :
		VFS_SCOPE_SYSTEM;
	if (agent_meta_store_failed_closed ||
	    (agent_meta_store_recovery_required && owner != FS_OWNER_SYSTEM))
		goto out;
	target_generation = agent_file_writeback_scope_target(scope_id);
	if (target_generation == 0) {
		status = 0;
		goto out;
	}
	for (uint steps = 0; steps < step_limit; steps++) {
		uint64 job_id;
		int same_job;
		int checkpoint;

		if (started_here && agent_meta_persist.published &&
		    !agent_meta_persist.mirroring &&
		    agent_meta_persist.scope_id == scope_id) {
			status = 0;
			break;
		}
		if (agent_file_writeback_scope_reached(
			    scope_id, target_generation)) {
			status = 0;
			break;
		}
		if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE) {
			if (agent_meta_persist_start_locked(owner) < 0)
				break;
			started_here = 1;
		} else if (!started_here) {
			/* Submitters enter the physical lane before mutating. */
			break;
		} else if (agent_meta_persist.owner != owner) {
			break;
		}
		job_id = agent_meta_persist.job_id;
		int step = agent_meta_persist_step_locked();

		if (step < 0) {
			int failure =
				agent_meta_persist_note_failure_locked();

			if (failure < 0)
				break;
			if (agent_meta_persist.retry_tick == 0) {
				uint64 retry_tick = agent_ticks();

				if (retry_tick != (uint64)-1)
					retry_tick++;
				agent_meta_persist.retry_tick = retry_tick;
			}
			if (agent_meta_persist.irrevocable &&
			    agent_meta_persist.scope_id == scope_id)
				status = 0;
			break;
		}
		agent_meta_persist.retry_failures = 0;
		/*
		 * The COW buffer remains owned by job_id while the global metadata
		 * transaction is dropped. Maintenance may advance that same immutable
		 * image, but no new submitter may replace it until this caller returns.
		 */
		agent_meta_persist.irrevocable = 1;
		checkpoint = agent_meta_persist_checkpoint_unlocked(
			job_id, &same_job);
		if (agent_file_writeback_scope_reached(
			    scope_id, target_generation)) {
			status = 0;
			break;
		}
		if (!same_job) {
			status = 0;
			break;
		}
		if (checkpoint < 0) {
			if (agent_meta_persist.scope_id == scope_id)
				status = 0;
			break;
		}
	}
out:
	if (status < 0 && started_here &&
	    agent_meta_persist.irrevocable &&
	    agent_meta_persist.scope_id == scope_id)
		status = 0;
	if (agent_meta_sync_submit_owner == token) {
		agent_meta_sync_submit_owner = 0;
		wait_queue_wake_all(&agent_meta_submit_waiters);
	}
	agent_metadata_txn_unlock();
	return status;
}

static int agent_meta_persist_drain_owner(uint owner)
{
	uint limit = MAXFILE * 2 + 16;
	int result = -1;

	for (uint steps = 0; steps < limit; steps++) {
		int started_background = 0;
		int step;

		if (!agent_metadata_txn_lock(1))
			break;
		if (agent_meta_sync_submit_owner != 0 &&
		    agent_meta_sync_submit_owner != agent_metadata_txn_token()) {
			agent_metadata_txn_unlock();
			break;
		}
		if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE) {
			uint scope_id = FS_OWNER_IS_SCOPE(owner) ?
				FS_OWNER_SCOPE_ID(owner) : VFS_SCOPE_SYSTEM;

			if (agent_file_writeback_scope_target(scope_id) == 0) {
				result = agent_meta_store_recovery_required ? -1 : 0;
				agent_metadata_txn_unlock();
				break;
			}
		} else if (agent_meta_persist.owner != owner ||
			   agent_ticks() < agent_meta_persist.retry_tick) {
			agent_metadata_txn_unlock();
			break;
		}
		if (!bio_background_active(owner)) {
			if (!bio_background_begin(owner)) {
				agent_metadata_txn_unlock();
				break;
			}
			started_background = 1;
		}
		if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE &&
		    agent_meta_persist_start_locked(owner) < 0) {
			step = -1;
			goto out_step;
		}
		step = agent_meta_persist_step_locked();
		if (step == AGENT_META_PERSIST_DEFERRED)
			goto out_step;
		if (step < 0) {
			agent_meta_persist_note_failure_locked();
			goto out_step;
		}
		agent_meta_persist.retry_failures = 0;
		agent_meta_persist.retry_tick = 0;
	out_step:
		if (started_background)
			bio_background_end();
		agent_metadata_txn_unlock();
		if (step == AGENT_META_PERSIST_DEFERRED || step < 0)
			break;
		if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE) {
			result = 0;
			break;
		}
		// A caller-owned lease is one scheduler quantum. Reacquiring leases
		// here would bypass the background bucket reserved by its owner.
		if (!started_background)
			break;
	}
	return result;
}

static int agent_file_persist_system(void)
{
	return agent_meta_persist_drain_owner(FS_OWNER_SYSTEM);
}

static void agent_file_writeback_maintain(void)
{
	uint64 now = agent_ticks();
	uint64 started_tick = now;
	uint owner;
	int step = 0;

	if (!agent_file_loaded)
		return;
	if (agent_meta_sync_submit_owner != 0)
		return;
	if (agent_meta_persist.phase != AGENT_META_PERSIST_IDLE &&
	    now < agent_meta_persist.retry_tick)
		return;
	if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE &&
	    !agent_file_writeback_due(now))
		return;
	owner = agent_meta_persist.phase == AGENT_META_PERSIST_IDLE ?
		(agent_meta_store_recovery_required ? FS_OWNER_SYSTEM :
		 agent_file_writeback_owner(now)) : agent_meta_persist.owner;
	if (!bio_background_begin(owner))
		return;
	if (!agent_metadata_txn_try_external())
		goto out_io;
	if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE &&
	    agent_meta_persist_start_locked(owner) < 0) {
		step = -1;
		goto out_txn;
	}
	step = agent_meta_persist_step_locked();
	if (step == AGENT_META_PERSIST_DEFERRED) {
		uint64 retry_tick = agent_ticks();

		if (retry_tick != (uint64)-1)
			retry_tick++;
		agent_meta_persist.retry_tick = retry_tick;
	} else if (step >= 0) {
		agent_meta_persist.retry_failures = 0;
		agent_meta_persist.retry_tick = 0;
	} else {
		agent_meta_persist_note_failure_locked();
	}
out_txn:
	agent_metadata_txn_unlock();
	if (step < 0 && step != AGENT_META_PERSIST_DEFERRED) {
		if (agent_meta_persist.phase != AGENT_META_PERSIST_IDLE)
			agent_meta_persist.retry_tick =
				agent_file_writeback_rest_deadline(
					started_tick, agent_ticks());
		agent_file_writeback_defer(started_tick);
	}
out_io:
	bio_background_end();
}

int
agent_metadata_store_install_empty(void)
{
	agent_metadata_objects_clear_catalog();
	agent_file_loaded = 1;
	agent_meta_store_recovery_required = 1;
	agent_file_writeback_mark(VFS_SCOPE_SYSTEM);
	agent_file_writeback_expedite(VFS_SCOPE_SYSTEM);
	return agent_file_persist_system();
}

int
agent_metadata_store_loaded(void)
{
	return agent_file_loaded;
}

int
agent_metadata_store_available(void)
{
	return !agent_meta_store_failed_closed;
}

int
agent_metadata_store_has_durable_bank(void)
{
	return agent_meta_store_active_bank >= 0;
}

int
agent_metadata_store_shadow_has_scope(uint scope_id)
{
	return agent_meta_shadow_has_scope(scope_id);
}

int
agent_metadata_store_submit_wait_locked(void)
{
	return agent_meta_submit_wait_locked();
}

int
agent_metadata_store_reload_wait_locked(void)
{
	return agent_meta_reload_wait_locked();
}

void
agent_metadata_store_mark_dirty(uint scope_id)
{
	agent_file_writeback_mark(scope_id);
}

void
agent_metadata_store_expedite(uint scope_id)
{
	agent_file_writeback_expedite(scope_id);
}

int
agent_metadata_store_persist(void)
{
	return agent_file_persist();
}

int
agent_metadata_store_persist_system(void)
{
	return agent_file_persist_system();
}

int
agent_metadata_store_scope_pending(uint scope_id)
{
	return agent_file_writeback_scope_pending(scope_id);
}

int
agent_metadata_store_scope_busy(uint scope_id)
{
	return agent_file_writeback_scope_busy(scope_id);
}

void
agent_metadata_store_scope_retire(uint scope_id)
{
	agent_file_scope_state_retire(scope_id);
}

void
agent_metadata_store_background_maintain(void)
{
	agent_file_writeback_maintain();
}

void
agent_metadata_store_fill_info(uint scope_id, struct agent_info *info)
{
	agent_file_writeback_info(scope_id, info);
}
