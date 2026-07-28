#include "agent_internal.h"
#include "agent_durable_section.h"
#include "agent_file_name_policy.h"
#include "agent_file_state_internal.h"
#include "agent_metadata_internal.h"
#include "agent_metadata_probe.h"
#include "agent_metadata_recovery.h"
#include "agent_metadata_recovery_test.h"
#include "agent_metadata_store_format.h"
#include "agent_metadata_store_io.h"
#include "metadata_crash_test.h"
#include "bio.h"
#include "defs.h"
#include "file.h"
#include "fs.h"
#include "kernel_work.h"
#include "timer.h"
#include "vfs_security.h"
#include "virtio.h"

/* Durable COW coordinator. */

#define AGENT_META_WRITEBACK_COALESCE_TICKS TICKS_PER_SEC
#define AGENT_META_WRITEBACK_SCOPE_MAX NPROC
#define AGENT_META_PERSIST_IDLE 0U
#define AGENT_META_PERSIST_INVALIDATE 1U
#define AGENT_META_PERSIST_FLUSH_INVALID 2U
#define AGENT_META_PERSIST_WRITE 3U
#define AGENT_META_PERSIST_FLUSH_PAYLOAD 4U
#define AGENT_META_PERSIST_VERIFY_PAYLOAD 5U
#define AGENT_META_PERSIST_PUBLISH 6U
#define AGENT_META_PERSIST_FLUSH_HEADER 7U
#define AGENT_META_PERSIST_VERIFY_HEADER 8U
#define AGENT_META_PERSIST_COMMIT 9U
#define AGENT_META_PERSIST_DIRTY_BYTES ((MAXFILE + 7) / 8)
#define AGENT_META_PERSIST_DEFERRED (-4096)
#define AGENT_META_REPAIR_NONE 0
#define AGENT_META_REPAIR_MIRROR 1
#define AGENT_META_PRIMARY_STEP_LIMIT (2U * MAXFILE + 16U)
#define AGENT_META_REPLICATED_STEP_LIMIT \
	(AGENT_META_STORE_BANKS * (2U * MAXFILE + 10U) + 2U)

_Static_assert(AGENT_META_WRITEBACK_SCOPE_MAX >=
	       VFS_SCOPE_LIFECYCLE_CAP + 1,
	       "metadata writeback must cover every workflow and system scope");
_Static_assert(AGENT_META_PERSIST_DEFERRED < VIRTIO_DISK_ERR_RANGE,
	       "metadata deferred sentinel must not alias a device status");

struct agent_file_scope_state {
	int used;
	uint scope_id;
	uint64 dirty_generation;
	uint64 durable_generation;
	uint64 replicated_generation;
	uint64 due_tick;
	uint64 request_count;
	uint64 coalesced_count;
	uint64 commit_count;
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
	uint64 captured_generation;
	uint64 verify_hash;
	uint64 size_sequence;
	uint64 durable_serial;
	uint64 retry_tick;
	uint retry_failures;
	int error_cause;
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
	scope_states[AGENT_META_WRITEBACK_SCOPE_MAX];
static struct agent_meta_store store_buf;
static struct agent_meta_persist_state agent_meta_persist;
static struct agent_meta_store
	agent_meta_bank_shadow[AGENT_META_STORE_BANKS];
static union {
	struct { struct agent_meta_record record; int index[AGENT_FILE_META_MAX]; } sort;
	struct { struct agent_metadata_apply_result result; int scratch_bank; } load;
} agent_meta_workspace;
#define agent_meta_sort_record agent_meta_workspace.sort.record
#define agent_meta_sort_index agent_meta_workspace.sort.index
static uint agent_meta_bank_write_limit[AGENT_META_STORE_BANKS];
static int agent_meta_bank_shadow_valid[AGENT_META_STORE_BANKS];
static int agent_meta_bank_delta_valid[AGENT_META_STORE_BANKS];
static uchar stale_slots[AGENT_META_STALE_BYTES];
static int agent_file_loaded;
static struct wait_queue waiters;
static void *submitter;
static uint64 next_job_id;
static uint64 next_ticket;
static uint64 serving_ticket;
static int agent_meta_store_active_bank;
static uint64 agent_meta_store_generation;
static uint64 agent_meta_store_replicated_generation;
static int agent_meta_store_failed_closed;
static int store_failure;
static int agent_meta_store_recovery_required;
static int pending_repair_mode;
static int pending_repair_bank;
static int agent_meta_reconcile_required;
static int banks_prepared;
static uint64 next_write_tick;
static uint owner_cursor;

static void
agent_meta_store_set_replicated_generation(uint64 generation)
{
	int enabled = intr_save();

	agent_meta_store_replicated_generation = generation;
	intr_restore(enabled);
}

static int agent_file_persist(struct agent_metadata_persist_result *);
static int agent_file_persist_system(void);
static int agent_meta_persist_drain_owner(uint owner);
static uint64 agent_meta_durable_dirty(uint scope_id);
static int agent_meta_durable_replicated(uint scope_id, uint64 target);
static int agent_meta_durable_active_replicated(uint64 generation);
static int agent_meta_durable_persist_scope(uint scope_id);

static const struct agent_durable_store_ops agent_meta_durable_store = {
	.mark_dirty = agent_meta_durable_dirty,
	.expedite = agent_metadata_store_expedite,
	.replicated = agent_meta_durable_replicated,
	.active_replicated = agent_meta_durable_active_replicated,
	.persist_scope = agent_meta_durable_persist_scope,
};

static uint64
agent_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static void
agent_meta_crash_checkpoint(uint phase)
{
	agent_metadata_test_checkpoint(
		agent_meta_persist.scope_id, agent_meta_persist.job_id,
		agent_meta_persist.mirroring, phase);
}

static void
agent_meta_eio_checkpoint(uint phase)
{
	agent_metadata_test_eio_pre_io(
		agent_meta_persist.scope_id, agent_meta_persist.job_id,
		agent_meta_persist.mirroring, phase);
}

static struct agent_file_scope_state *
agent_file_scope_state_locked(uint scope_id, int create)
{
	struct agent_file_scope_state *free_state = 0;

	if (scope_id != VFS_SCOPE_SYSTEM && !agent_scope_valid(scope_id))
		scope_id = VFS_SCOPE_SYSTEM;
	for (int i = 0; i < AGENT_META_WRITEBACK_SCOPE_MAX; i++) {
		struct agent_file_scope_state *state =
			&scope_states[i];

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

void
agent_metadata_store_init(void)
{
	memset(scope_states, 0, sizeof(scope_states));
	memset(&store_buf, 0, sizeof(store_buf));
	memset(&agent_meta_persist, 0, sizeof(agent_meta_persist));
	memset(&agent_meta_workspace, 0, sizeof(agent_meta_workspace));
	agent_meta_workspace.load.scratch_bank = -1;
	memset(agent_meta_bank_shadow, 0, sizeof(agent_meta_bank_shadow));
	memset(agent_meta_bank_write_limit, 0,
	       sizeof(agent_meta_bank_write_limit));
	memset(agent_meta_bank_shadow_valid, 0,
	       sizeof(agent_meta_bank_shadow_valid));
	memset(agent_meta_bank_delta_valid, 0,
	       sizeof(agent_meta_bank_delta_valid));
	memset(stale_slots, 0, sizeof(stale_slots));
	agent_file_loaded = 0;
	agent_meta_store_io_init();
	agent_metadata_probe_init();
	wait_queue_init(&waiters, WAIT_REASON_AGENT_META);
	submitter = 0;
	next_job_id = 1;
	next_ticket = 1;
	serving_ticket = 1;
	agent_meta_store_active_bank = -1;
	agent_meta_store_generation = 0;
	agent_meta_store_replicated_generation = 0;
	agent_meta_store_failed_closed = 0;
	store_failure = AGENT_METADATA_PERSIST_NONE;
	agent_meta_store_recovery_required = 0;
	pending_repair_mode = AGENT_META_REPAIR_NONE;
	pending_repair_bank = -1;
	agent_meta_reconcile_required = 0;
	banks_prepared = 0;
	agent_metadata_recovery_init();
	agent_metadata_test_init();
	next_write_tick = 0;
	owner_cursor = 0;
	agent_durable_section_set_store_provider(&agent_meta_durable_store);
}
uint64 agent_metadata_store_mark_dirty(uint scope_id)
{
	struct agent_file_scope_state *state;
	uint64 now = agent_ticks();
	uint64 target;
	int enabled = intr_save();

	state = agent_file_scope_state_locked(scope_id, 1);
	if (state == 0) {
		/* Edge-triggered catalog reconciliation. */
		agent_meta_reconcile_required = 1;
		intr_restore(enabled);
		agent_background_request();
		return 0;
	}
	if (state->dirty_generation != state->durable_generation)
		state->coalesced_count++;
	else
		state->due_tick = now + AGENT_META_WRITEBACK_COALESCE_TICKS;
	state->dirty_generation++;
	if (state->dirty_generation == 0)
		state->dirty_generation = 1;
	state->request_count++;
	target = state->dirty_generation;
	intr_restore(enabled);
	agent_background_request();
	return target;
}

static uint64
agent_meta_durable_dirty(uint scope_id)
{
	return agent_metadata_store_mark_dirty(scope_id);
}

void agent_metadata_store_expedite(uint scope_id)
{
	struct agent_file_scope_state *state;
	uint64 now = agent_ticks();
	int enabled = intr_save();

	state = agent_file_scope_state_locked(scope_id, 0);
	if (state != 0 &&
	    state->dirty_generation != state->durable_generation) {
		state->due_tick = now;
		if (next_write_tick > now)
			next_write_tick = now;
	}
	intr_restore(enabled);
}

static int agent_file_writeback_scope_busy(uint scope_id)
{
	return agent_meta_persist.phase != AGENT_META_PERSIST_IDLE &&
	       agent_meta_persist.scope_id == scope_id;
}

static int scope_snapshot(uint scope_id,
			  struct agent_file_scope_state *snapshot)
{
	struct agent_file_scope_state *state;
	int busy;
	int enabled = intr_save();

	memset(snapshot, 0, sizeof(*snapshot));
	state = agent_file_scope_state_locked(scope_id, 0);
	if (state)
		*snapshot = *state;
	busy = agent_file_writeback_scope_busy(scope_id);
	intr_restore(enabled);
	return busy;
}

int agent_metadata_store_scope_pending(uint scope_id)
{
	struct agent_file_scope_state state;

	scope_snapshot(scope_id, &state);
	return state.used &&
	       state.dirty_generation != state.durable_generation;
}

#ifdef AGENT_METADATA_CRASH_PHASE
int
agent_metadata_store_test_quiet_generation(uint scope_id, uint64 *generation)
{
	struct agent_file_scope_state state;
	int busy;

	if (generation == 0 || !agent_metadata_txn_owned(1))
		return -1;
	busy = scope_snapshot(scope_id, &state);
	if (!state.used || state.dirty_generation == 0 ||
	    state.dirty_generation == ~0ULL ||
	    state.dirty_generation != state.durable_generation ||
	    state.dirty_generation != state.replicated_generation || busy)
		return -1;
	*generation = state.dirty_generation;
	return 0;
}
#endif

static int agent_file_writeback_due(uint64 now)
{
	int due = 0;
	int enabled = intr_save();

	if (now < next_write_tick)
		goto out;
	for (int i = 0; i < AGENT_META_WRITEBACK_SCOPE_MAX; i++) {
		struct agent_file_scope_state *state =
			&scope_states[i];

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

static int agent_file_writeback_pending(void)
{
	int pending;
	int enabled = intr_save();

	pending = agent_meta_persist.phase != AGENT_META_PERSIST_IDLE;
	for (int i = 0; !pending && i < AGENT_META_WRITEBACK_SCOPE_MAX; i++)
		pending = scope_states[i].used &&
			scope_states[i].dirty_generation !=
				scope_states[i].durable_generation;
	intr_restore(enabled);
	return pending;
}

static uint64 scope_target(uint scope_id)
{
	struct agent_file_scope_state state;

	scope_snapshot(scope_id, &state);
	return state.used && state.dirty_generation != state.durable_generation ?
		state.dirty_generation : 0;
}

static int agent_file_writeback_generation_reached(uint64 generation,
						    uint64 target)
{
	return target == 0 || (long)(generation - target) >= 0;
}

static int scope_reached(uint scope_id, uint64 target, int settled)
{
	struct agent_file_scope_state state;
	int reached = target == 0 || settled;
	int busy = scope_snapshot(scope_id, &state);

	if (state.used)
		reached = agent_file_writeback_generation_reached(
				  state.durable_generation, target) &&
			  (!settled || state.dirty_generation ==
					       state.durable_generation);
	if (settled && busy)
		reached = 0;
	return reached;
}

static int
scope_replicated(uint scope_id, uint64 target)
{
	struct agent_file_scope_state state;
	int reached = target == 0;
	int busy = scope_snapshot(scope_id, &state);

	if (state.used)
		reached = agent_file_writeback_generation_reached(
			state.replicated_generation, target);
	if (busy)
		reached = 0;
	return reached;
}

static int
agent_meta_durable_replicated(uint scope_id, uint64 target)
{
	if (agent_meta_store_failed_closed) {
		if (store_failure ==
		    AGENT_METADATA_PERSIST_DURABILITY)
			return AGENT_STATUS_DURABILITY;
		if (store_failure ==
		    AGENT_METADATA_PERSIST_IO)
			return AGENT_STATUS_IO_ERROR;
		return AGENT_STATUS_INDETERMINATE;
	}
	return scope_replicated(scope_id, target);
}

static int
agent_meta_durable_active_replicated(uint64 generation)
{
	int result;
	int enabled = intr_save();

	if (agent_meta_store_failed_closed) {
		if (store_failure == AGENT_METADATA_PERSIST_DURABILITY)
			result = AGENT_STATUS_DURABILITY;
		else if (store_failure == AGENT_METADATA_PERSIST_IO)
			result = AGENT_STATUS_IO_ERROR;
		else
			result = AGENT_STATUS_INDETERMINATE;
	} else {
		result = generation != 0 &&
			 generation == agent_meta_store_generation &&
			 generation == agent_meta_store_replicated_generation;
	}
	intr_restore(enabled);
	return result;
}

// Rotate coalesced writeback sponsorship across scopes.
static uint agent_file_writeback_owner(uint64 now)
{
	uint owner = FS_OWNER_SYSTEM;
	int enabled = intr_save();

	for (uint scanned = 0; scanned < AGENT_META_WRITEBACK_SCOPE_MAX;
	     scanned++) {
		uint i = (owner_cursor + scanned) %
			 AGENT_META_WRITEBACK_SCOPE_MAX;
		struct agent_file_scope_state *state =
			&scope_states[i];

		if (!state->used ||
		    state->dirty_generation == state->durable_generation ||
		    now < state->due_tick)
			continue;
		owner = state->scope_id == VFS_SCOPE_SYSTEM ?
			FS_OWNER_SYSTEM : FS_OWNER_SCOPE(state->scope_id);
		owner_cursor =
			(i + 1) % AGENT_META_WRITEBACK_SCOPE_MAX;
		break;
	}
	intr_restore(enabled);
	return owner;
}

static uint64 agent_file_writeback_capture_state(uint scope_id)
{
	struct agent_file_scope_state *state;
	uint64 generation = 0;
	int enabled = intr_save();

	state = agent_file_scope_state_locked(scope_id, 0);
	if (state && state->scope_id == scope_id)
		generation = state->dirty_generation;
	intr_restore(enabled);
	return generation;
}

static uint64 agent_file_writeback_rest_deadline(uint64 now)
{
	uint64 rest = AGENT_META_WRITEBACK_COALESCE_TICKS;

	if (rest > ~0ULL - now)
		return ~0ULL;
	return now + rest;
}

static void agent_file_writeback_advance(int replicated)
{
	struct agent_file_scope_state *state;
	uint64 now = replicated ? 0 : agent_ticks();
	uint64 *generation;
	int advanced = 0;
	int enabled = intr_save();

	state = agent_file_scope_state_locked(agent_meta_persist.scope_id, 0);
	if (state && state->scope_id == agent_meta_persist.scope_id &&
	    agent_meta_persist.captured_generation != 0) {
		generation = replicated ? &state->replicated_generation :
			&state->durable_generation;
		if (!agent_file_writeback_generation_reached(
			    *generation, agent_meta_persist.captured_generation)) {
			*generation = agent_meta_persist.captured_generation;
			advanced = 1;
		}
		if (advanced && !replicated &&
		    state->dirty_generation == state->durable_generation) {
			state->due_tick = 0;
			// Count only a coalescing batch that reached its current head.
			state->commit_count++;
		}
	}
	if (!replicated)
		next_write_tick =
			agent_file_writeback_rest_deadline(now);
	intr_restore(enabled);
}

static void agent_file_writeback_defer(void)
{
	uint64 now = agent_ticks();
	int enabled = intr_save();

	next_write_tick =
		agent_file_writeback_rest_deadline(now);
	intr_restore(enabled);
}

static int
agent_file_scope_state_retire(uint scope_id, uint64 target)
{
	struct agent_file_scope_state *state;
	int retired = 0;
	int enabled = intr_save();

	if (agent_meta_store_failed_closed ||
	    agent_durable_section_scope_pending(scope_id))
		goto out;
	state = agent_file_scope_state_locked(scope_id, 0);
	if (state == 0) {
		retired = target == 0;
		goto out;
	}
	if (!agent_file_writeback_generation_reached(
		    state->replicated_generation, target) ||
	    state->dirty_generation != state->durable_generation ||
	    state->dirty_generation != state->replicated_generation ||
	    agent_file_writeback_scope_busy(scope_id))
		goto out;
	memset(state, 0, sizeof(*state));
	retired = 1;
out:
	intr_restore(enabled);
	return retired;
}

void agent_metadata_store_fill_info(uint scope_id, struct agent_info *info)
{
	struct agent_file_scope_state state;
	int busy;

	if (info == 0)
		return;
	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG)
		return;
	busy = scope_snapshot(scope_id, &state);
	if (state.used) {
		info->metadata_writeback_dirty = state.dirty_generation;
		info->metadata_writeback_durable = state.durable_generation;
		info->metadata_writeback_requests = state.request_count;
		info->metadata_writeback_coalesced = state.coalesced_count;
		info->metadata_writeback_commits = state.commit_count;
		info->metadata_writeback_pending =
			state.dirty_generation != state.durable_generation || busy;
	}
}

/* Serialize mutation behind the immutable COW image. */
int agent_metadata_store_submit_wait_locked(void)
{
	void *token = agent_metadata_txn_token();
	uint64 ticket;

	agent_metadata_txn_require_owned(1, "Agent metadata submit invariant");
	ticket = next_ticket++;
	for (;;) {
		int enabled = intr_save();

		if (ticket == serving_ticket &&
		    (agent_meta_store_failed_closed ||
		     agent_metadata_recovery_pending())) {
			serving_ticket++;
			wait_queue_wake_all(&waiters);
			intr_restore(enabled);
			return 0;
		}
		if (ticket == serving_ticket &&
		    agent_meta_persist.phase == AGENT_META_PERSIST_IDLE &&
		    (submitter == 0 || submitter == token) &&
		    agent_metadata_reload_available()) {
			serving_ticket++;
			wait_queue_wake_all(&waiters);
			intr_restore(enabled);
			return 1;
		}
		if (ticket == serving_ticket &&
		    agent_meta_persist.phase != AGENT_META_PERSIST_IDLE &&
		    submitter == 0 &&
		    agent_metadata_reload_available()) {
			uint owner = agent_meta_persist.owner;

			// The FIFO head assists under the inducing owner's I/O charge.
			intr_restore(enabled);
			agent_metadata_txn_unlock();
			(void)agent_meta_persist_drain_owner(owner);
			(void)kernel_work_checkpoint_cleanup(
				KERNEL_WORK_OPERATION_UNITS);
			agent_metadata_txn_relock_uninterruptible();
			continue;
		}
		// Hold IRQ state through unlock-and-sleep.
		agent_metadata_txn_unlock();
		// Tickets cannot be abandoned.
		if (wait_queue_sleep_irq_uninterruptible(
			    &waiters) !=
		    WAIT_QUEUE_OK)
			panic("metadata submit wait failed");
		intr_restore(enabled);
		agent_metadata_txn_relock_uninterruptible();
	}
}

static int
agent_meta_persist_device_error(int result)
{
	if (result == FS_LOOKUP_INDETERMINATE) {
		agent_metadata_store_fail_closed_runtime();
		agent_meta_persist.error_cause =
			AGENT_METADATA_PERSIST_FAIL_CLOSED;
		agent_meta_persist.irrevocable = 1;
		return result;
	}
	if (result == VIRTIO_DISK_ERR_BUSY ||
	    result == VIRTIO_DISK_ERR_TIMEOUT) {
		agent_meta_persist.error_cause = AGENT_METADATA_PERSIST_RETRY;
		return AGENT_META_PERSIST_DEFERRED;
	}
	if (result == VIRTIO_DISK_ERR_UNSUPPORTED)
		agent_meta_persist.error_cause =
			AGENT_METADATA_PERSIST_DURABILITY;
	else
		agent_meta_persist.error_cause = AGENT_METADATA_PERSIST_IO;
	return result < 0 ? result : VIRTIO_DISK_ERR_IO;
}

int agent_metadata_store_reload_wait_locked(void)
{
	agent_metadata_txn_require_owned(
		1, "Agent metadata reload wait invariant");
	for (;;) {
		int enabled = intr_save();
		int wait_result;

		if (agent_metadata_reload_available()) {
			intr_restore(enabled);
			return 1;
		}
		// Publish the waiter before clearing the gate.
		agent_metadata_txn_unlock();
		wait_result =
			wait_queue_sleep_irq(&waiters);
		intr_restore(enabled);
		if (wait_result != WAIT_QUEUE_OK)
			return 0;
		if (!agent_metadata_txn_lock(1))
			return 0;
	}
}

/* Restore transaction depth after servicing I/O debt. */
static struct bio_checkpoint_result
agent_meta_persist_checkpoint_unlocked(uint64 job_id, int *same_job)
{
	void *token = agent_metadata_txn_token();
	int depth;
	struct bio_checkpoint_result checkpoint;

	if (same_job == 0 || job_id == 0 ||
	    !agent_metadata_txn_owned(0) ||
	    submitter != token)
		panic("metadata persist checkpoint invariant");
	depth = agent_metadata_txn_depth();
	checkpoint = agent_meta_persist.irrevocable ?
		agent_metadata_txn_checkpoint_cleanup_unlocked() :
		agent_metadata_txn_checkpoint_unlocked();
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

static void agent_meta_bank_delta_invalidate(int bank)
{
	if (bank < 0 || bank >= AGENT_META_STORE_BANKS)
		panic("metadata delta bank range");
	agent_meta_bank_delta_valid[bank] = 0;
	agent_meta_bank_write_limit[bank] = 0;
}

static void agent_meta_bank_shadow_invalidate(int bank)
{
	agent_meta_bank_delta_invalidate(bank);
	agent_meta_bank_shadow_valid[bank] = 0;
}

static void agent_meta_persist_prepare_blocks(struct agent_meta_store *store,
					      int target_bank)
{
	uint offset = sizeof(store->header);
	uint write_limit = agent_meta_persist_segment_end(
		agent_meta_persist.store_bytes - 1);

	memset(agent_meta_persist.dirty_blocks, 0,
	       sizeof(agent_meta_persist.dirty_blocks));
	agent_meta_persist.write_limit = write_limit;
	while (offset < write_limit) {
		uint end = agent_meta_persist_segment_end(offset);
		uint block = offset / BSIZE;

		if (!agent_meta_bank_shadow_valid[target_bank] ||
		    !agent_meta_bank_delta_valid[target_bank] ||
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
					   uint write_limit,
					   int physical_delta_valid)
{
	if (store == 0 || target_bank < 0 ||
	    target_bank >= AGENT_META_STORE_BANKS ||
	    write_limit > sizeof(*store) ||
	    (physical_delta_valid != 0 && physical_delta_valid != 1))
		panic("metadata shadow range");
	memset(&agent_meta_bank_shadow[target_bank], 0,
	       sizeof(agent_meta_bank_shadow[target_bank]));
	memmove(&agent_meta_bank_shadow[target_bank], store, write_limit);
	agent_meta_bank_shadow_valid[target_bank] = 1;
	agent_meta_bank_delta_valid[target_bank] = physical_delta_valid;
	agent_meta_bank_write_limit[target_bank] =
		physical_delta_valid ? write_limit : 0;
}

int agent_file_is_meta_store_name(char *path)
{
	return path &&
	       (strncmp(path, AGENT_META_STORE_NAME_0, DIRSIZ) == 0 ||
		strncmp(path, AGENT_META_STORE_NAME_1, DIRSIZ) == 0);
}

static uint64
store_authority_cookie(void)
{
	uint64 cookie = AGENT_META_STORE_HASH_INITIAL;

	cookie = agent_meta_format_hash_mix(cookie, agent_file_loaded);
	cookie = agent_meta_format_hash_mix(
		cookie, (uint64)(agent_meta_store_active_bank + 2));
	cookie = agent_meta_format_hash_mix(cookie, agent_meta_store_generation);
	if (agent_meta_store_active_bank >= 0 &&
	    agent_meta_store_active_bank < AGENT_META_STORE_BANKS &&
	    agent_meta_bank_shadow_valid[agent_meta_store_active_bank])
		cookie = agent_meta_format_hash_mix(
			cookie, agent_meta_bank_shadow[
				agent_meta_store_active_bank].header.payload_hash);
	return cookie;
}

static int agent_meta_store_select(struct agent_meta_store *store,
				   int *selected_bank,
				   uint64 *selected_generation,
				   int *repair_mode, int *repair_bank,
				   int force, uint reload_scope,
				   uint64 *candidate_epoch,
				   int *selected_migrated)
{
	struct agent_metadata_probe_key key = {
		.authority_cookie = store_authority_cookie(),
		.reload_scope = reload_scope,
		.force = force,
		.resumable = !agent_file_loaded,
	};
	uint64 generations[AGENT_META_STORE_BANKS];
	uint64 hashes[AGENT_META_STORE_BANKS];
	int status[AGENT_META_STORE_BANKS];
	int migration[AGENT_META_STORE_BANKS];
	int selected = -1;

	if (store == 0 || selected_bank == 0 || selected_generation == 0 ||
	    repair_mode == 0 || repair_bank == 0 || candidate_epoch == 0 ||
	    selected_migrated == 0)
		return -1;
	memset(generations, 0, sizeof(generations));
	memset(hashes, 0, sizeof(hashes));
	memset(migration, 0, sizeof(migration));
	*repair_mode = AGENT_META_REPAIR_NONE;
	*repair_bank = -1;
	for (int bank = 0; bank < AGENT_META_STORE_BANKS; bank++) {
		status[bank] = agent_metadata_probe_summary(
			&key, bank, store, &generations[bank], &hashes[bank],
			&migration[bank], !agent_file_loaded);
		if (status[bank] == AGENT_META_BANK_PROGRESS ||
		    status[bank] == AGENT_META_BANK_INTERRUPTED ||
		    status[bank] == AGENT_META_BANK_BUSY ||
		    status[bank] == AGENT_META_BANK_IO)
			return status[bank];
		if (status[bank] != AGENT_META_BANK_VALID)
			continue;
		if (selected < 0 || generations[bank] > generations[selected])
			selected = bank;
		else if (generations[bank] == generations[selected]) {
			if (hashes[bank] != hashes[selected])
				return AGENT_META_BANK_CORRUPT;
			if (bank < selected)
				selected = bank;
		}
	}
	if (selected < 0)
		return AGENT_META_BANK_CORRUPT;
	int peer = selected == 0 ? 1 : 0;

	if (status[peer] != AGENT_META_BANK_VALID ||
		 generations[peer] != generations[selected] ||
		 migration[peer] || migration[selected])
		*repair_mode = AGENT_META_REPAIR_MIRROR;
	if (*repair_mode != AGENT_META_REPAIR_NONE)
		*repair_bank = peer;
	int confirmed = agent_metadata_probe_confirm(
		&key, selected, store, generations[selected], hashes[selected],
		migration[selected]);

	if (confirmed != AGENT_META_BANK_VALID)
		return confirmed < 0 ? confirmed : AGENT_META_BANK_CORRUPT;
	*selected_generation = generations[selected];
	*selected_bank = selected;
	*selected_migrated = migration[selected];
	*candidate_epoch = agent_metadata_probe_epoch();
	return AGENT_META_BANK_VALID;
}

static int
store_missing(const struct agent_metadata_apply_result *apply)
{
	for (uint i = 0; i < AGENT_META_STALE_BYTES; i++)
		if (apply->missing_slots[i] != 0)
			return 1;
	return 0;
}

static int
classify_missing(const struct agent_meta_store *store,
		 int reload_one_scope, uint reload_scope,
		 struct agent_metadata_apply_result *apply)
{
	int orphan_stale = 0;

	if (reload_one_scope) {
		if (apply->layout_changed || store_missing(apply))
			agent_metadata_store_mark_dirty(reload_scope);
		return 0;
	}
	for (uint i = 0; i < store->header.count; i++) {
		const struct agent_meta_record *record = &store->records[i];

		if ((apply->missing_slots[record->slot / 8] &
		     (1U << (record->slot % 8))) == 0)
			continue;
		if (record->scope_id != VFS_SCOPE_SYSTEM) {
			struct workflow_lifecycle_key lifecycle =
				workflow_lifecycle_none();

			if (vfs_scope_lifecycle(record->scope_id, &lifecycle) < 0 ||
			    !workflow_lifecycle_key_equal(lifecycle,
						  record->lifecycle)) {
				stale_slots[record->slot / 8] |=
					1U << (record->slot % 8);
				orphan_stale = 1;
				continue;
			}
		}
		if (record->scope_id == VFS_SCOPE_SYSTEM ||
		    vfs_scope_retained(record->scope_id)) {
			agent_metadata_store_mark_dirty(record->scope_id);
			continue;
		}
		stale_slots[record->slot / 8] |=
			1U << (record->slot % 8);
		orphan_stale = 1;
	}
	return orphan_stale;
}
static void agent_meta_store_apply_abort(void)
{
	int bank = agent_meta_workspace.load.scratch_bank;
	agent_metadata_catalog_prepare_abort(&agent_meta_workspace.load.result);
	if (bank >= 0 && bank < AGENT_META_STORE_BANKS &&
	    bank != agent_meta_store_active_bank)
		agent_meta_bank_shadow_invalidate(bank);
	agent_meta_workspace.load.scratch_bank = -1;
}
static int agent_meta_store_apply_prepare(const struct agent_meta_store *store,
		int selected_bank, int reload_one_scope, uint reload_scope,
		uint64 candidate_epoch, struct agent_meta_record **plan)
{
	struct agent_metadata_apply_result *apply = &agent_meta_workspace.load.result;
	int bank = agent_meta_workspace.load.scratch_bank;
	if (apply->plan_active &&
	    (apply->plan_candidate_epoch != candidate_epoch ||
	     apply->plan_count != store->header.count || bank < 0 ||
	     bank >= AGENT_META_STORE_BANKS ||
	     apply->plan_records != agent_meta_bank_shadow[bank].records))
		agent_meta_store_apply_abort();
	if (!apply->plan_active) {
		bank = agent_meta_store_active_bank == 0 ? 1 : 0;
		if (agent_meta_store_active_bank < 0)
			bank = selected_bank == 0 ? 1 : 0;
		if (bank == agent_meta_store_active_bank)
			panic("metadata apply scratch aliases authority");
		agent_meta_bank_shadow_invalidate(bank);
		memmove(agent_meta_bank_shadow[bank].records, store->records,
			store->header.count * sizeof(store->records[0]));
		agent_meta_workspace.load.scratch_bank = bank;
	}
	*plan = agent_meta_bank_shadow[bank].records;
	return agent_metadata_catalog_prepare_snapshot(
		*plan, store->header.count, reload_one_scope, reload_scope,
		candidate_epoch, apply);
}
static int
agent_file_load_snapshot(int force, uint reload_scope,
			 struct agent_metadata_store_commit *commit)
{
	struct agent_meta_store *store = &store_buf;
	struct agent_metadata_apply_result *apply =
		&agent_meta_workspace.load.result;
	struct agent_meta_record *apply_plan = 0;
	uint store_bytes;
	int selected_bank;
	int selected_migrated = 0;
	uint64 selected_generation;
	uint64 candidate_epoch = 0;
	int reload_one_scope;
	int repair_mode = AGENT_META_REPAIR_NONE;
	int repair_bank = -1;
	int orphan_stale = 0;
	int select_status;
	int store_locked = 0;
	int result = -1;

	if (commit == 0)
		return -1;
	memset(commit, 0, sizeof(*commit));
	if (!agent_metadata_txn_lock(1))
		return -1;
	if (agent_meta_store_failed_closed && !force)
		goto out_txn;
	if (!force && agent_file_loaded) {
		if (agent_meta_store_recovery_required)
			commit->repair_required = 1;
		result = agent_metadata_catalog_live_count();
		goto out_txn;
	}
	if (agent_file_loaded) {
		memset(&agent_meta_workspace.load, 0, sizeof(agent_meta_workspace.load));
		agent_meta_workspace.load.scratch_bank = -1;
	}
	if (!agent_metadata_reload_claim())
		goto out_txn;
	commit->reload_owned = 1;
	if (!agent_meta_store_io_enter()) {
		result = AGENT_METADATA_LOAD_BUSY;
		goto out_txn;
	}
	store_locked = 1;
	// Classify both banks before creating either one.
	select_status = agent_meta_store_select(
		store, &selected_bank, &selected_generation, &repair_mode,
		&repair_bank, force, reload_scope, &candidate_epoch,
		&selected_migrated);
	if (select_status != AGENT_META_BANK_VALID) {
		if (apply->plan_active)
			agent_meta_store_apply_abort();
		agent_meta_store_io_leave();
		store_locked = 0;
		result = select_status;
		if (select_status == AGENT_META_BANK_PROGRESS ||
		    select_status == AGENT_META_BANK_INTERRUPTED ||
		    select_status == AGENT_META_BANK_BUSY ||
		    select_status == AGENT_META_BANK_IO)
			goto out_txn;
		agent_metadata_probe_reset();
		agent_meta_store_failed_closed = 1;
		store_failure =
			AGENT_METADATA_PERSIST_RECOVERY;
		result = AGENT_METADATA_LOAD_CORRUPT;
		goto out_txn;
	}
	if (agent_meta_format_store_bytes(store->header.count, &store_bytes) < 0)
		goto out_store;
	reload_one_scope = force && agent_file_loaded;
	if (reload_one_scope && !agent_scope_valid(reload_scope))
		goto out_store;
	result = agent_meta_store_apply_prepare(
		store, selected_bank, reload_one_scope, reload_scope,
		candidate_epoch, &apply_plan);
	if (result == AGENT_METADATA_LOAD_PROGRESS)
		agent_metadata_probe_catalog_progress(selected_bank, apply->plan_catalog_cursor + apply->plan_cursor);
	if (result < 0)
		goto out_store;
	/* Identifier recovery publishes monotonic floors only on first load. */
	if (!agent_file_loaded && agent_meta_format_recover_identifiers(store) < 0) {
		result = AGENT_METADATA_LOAD_CORRUPT;
		agent_meta_store_failed_closed = 1;
		store_failure =
			AGENT_METADATA_PERSIST_RECOVERY;
		goto out_store;
	}
	result = agent_metadata_catalog_apply_snapshot(
		apply_plan, store->header.count, reload_one_scope,
		reload_scope, candidate_epoch, apply);
	if (result < 0)
		goto out_store;
	commit->delta = apply->delta;
	if (!reload_one_scope)
		memset(stale_slots, 0, sizeof(stale_slots));
	orphan_stale = classify_missing(
		store, reload_one_scope, reload_scope, apply);
	agent_meta_bank_shadow_invalidate(selected_bank == 0 ? 1 : 0);
	agent_meta_bank_shadow_install(
		store, selected_bank, agent_meta_persist_segment_end(store_bytes - 1),
		!selected_migrated);
	agent_meta_store_active_bank = selected_bank;
	agent_meta_store_generation = selected_generation;
	agent_meta_store_set_replicated_generation(
		repair_mode == AGENT_META_REPAIR_NONE ? selected_generation : 0);
	agent_durable_section_active_bind(
		&agent_meta_bank_shadow[selected_bank].durable,
		selected_generation);
	agent_file_loaded = 1;
	agent_meta_store_recovery_required =
		repair_mode != AGENT_META_REPAIR_NONE;
	pending_repair_mode = repair_mode;
	pending_repair_bank = repair_bank;
	store_failure = AGENT_METADATA_PERSIST_NONE;
	agent_meta_store_failed_closed = 0;
	agent_metadata_probe_finish(candidate_epoch);
	if (apply->used != 0) {
		agent_meta_reconcile_required = 1;
		agent_background_request();
	}
	agent_meta_store_io_leave();
	store_locked = 0;
	if (orphan_stale) {
		agent_meta_store_recovery_required = 1;
		pending_repair_mode = AGENT_META_REPAIR_MIRROR;
		pending_repair_bank = selected_bank == 0 ? 1 : 0;
		agent_metadata_store_mark_dirty(VFS_SCOPE_SYSTEM);
		agent_metadata_store_expedite(VFS_SCOPE_SYSTEM);
	}
	if (repair_mode != AGENT_META_REPAIR_NONE && !orphan_stale) {
		agent_metadata_store_mark_dirty(VFS_SCOPE_SYSTEM);
		agent_metadata_store_expedite(VFS_SCOPE_SYSTEM);
	}
	if (repair_mode != AGENT_META_REPAIR_NONE || orphan_stale)
		commit->repair_required = 1;
	result = apply->used;
	agent_meta_store_apply_abort();
	goto out_txn;

out_store:
	if (result != AGENT_METADATA_LOAD_PROGRESS)
		agent_meta_store_apply_abort();
	if (store_locked)
		agent_meta_store_io_leave();
	if (agent_file_loaded || !agent_metadata_recovery_retryable(result))
		agent_metadata_probe_reset();
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

int
agent_metadata_store_load(struct agent_metadata_store_commit *commit)
{
	return agent_file_load_snapshot(0, VFS_SCOPE_NONE, commit);
}

int
agent_metadata_store_reload(uint scope_id,
			    struct agent_metadata_store_commit *commit)
{
	return agent_file_load_snapshot(1, scope_id, commit);
}

int
agent_metadata_store_finish(struct agent_metadata_store_commit *commit,
			    int result)
{
	if (commit == 0)
		return -1;
	agent_metadata_txn_projection_require_idle();
	if (!!commit->reload_owned != agent_metadata_reload_is_current())
		panic("metadata reload finish invariant");
	if (commit->repair_required && agent_file_persist_system() < 0) {
		if (agent_meta_store_failed_closed ||
		    agent_meta_store_active_bank < 0)
			result = -1;
		else
			agent_background_request();
	}
	if (commit->reload_owned) {
		agent_metadata_reload_release();
		wait_queue_wake_all(&waiters);
	}
	return result;
}

void
agent_metadata_store_fail_closed_at_boot(void)
{
	agent_metadata_recovery_cancel();
	agent_meta_store_failed_closed = 1;
	agent_meta_store_set_replicated_generation(0);
	store_failure =
		AGENT_METADATA_PERSIST_RECOVERY;
	wait_queue_wake_all(&waiters);
	errorf("Agent metadata storage is corrupt; failed closed at boot\n");
}

static void
agent_meta_boot_reprobe_cause(int status)
{
	store_failure =
		status == AGENT_METADATA_LOAD_IO ? AGENT_METADATA_PERSIST_IO :
						 AGENT_METADATA_PERSIST_RETRY;
}

void
agent_metadata_store_defer_boot_reprobe(int status)
{
	if (agent_file_loaded ||
	    agent_metadata_recovery_defer(status, agent_ticks()) < 0)
		panic("metadata boot reprobe classification");
	agent_meta_store_failed_closed = 1;
	agent_meta_store_recovery_required = 1;
	agent_meta_boot_reprobe_cause(status);
	agent_background_request();
	errorf("Agent metadata storage temporarily unreadable; admission closed pending reprobe\n");
}

void
agent_metadata_store_boot_reprobe_complete(int result)
{
	uint failures = 0;
	uint64 deadline = 0;
	uint64 now = agent_ticks();
	int outcome = agent_metadata_recovery_complete(
		result, now, &failures, &deadline);

	if (outcome == AGENT_METADATA_RECOVERY_READY) {
		agent_meta_store_failed_closed = 0;
		store_failure =
			AGENT_METADATA_PERSIST_NONE;
		wait_queue_wake_all(&waiters);
		printf("agentmeta_boot_reprobe: recovered=1 retries=%d\n",
		       failures);
	} else if (outcome == AGENT_METADATA_RECOVERY_RETRY) {
		agent_meta_store_failed_closed = 1;
		agent_meta_boot_reprobe_cause(result);
		agent_metadata_recovery_test_retry(result, failures, now, deadline);
	} else {
		agent_meta_store_failed_closed = 1;
		store_failure =
			AGENT_METADATA_PERSIST_RECOVERY;
		wait_queue_wake_all(&waiters);
		errorf("Agent metadata reprobe confirmed corruption; admission remains closed\n");
	}
}

static int store_append(struct agent_meta_store *store,
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

static void store_sort(struct agent_meta_store *store)
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

// Replace one scope; leave other dirty scopes pending.
static int agent_meta_store_build_scope(struct agent_meta_store *store,
					uint scope_id,
					uint64 *size_sequence,
					uint64 *durable_serial)
{
	struct agent_meta_store *base;
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	uchar used_slots[(AGENT_FILE_META_MAX + 7) / 8];
	uint base_count;
	int exported;

	if (!agent_object_scope_valid(scope_id) || durable_serial == 0 ||
	    agent_meta_store_active_bank < 0 ||
	    agent_meta_store_active_bank >= AGENT_META_STORE_BANKS ||
	    !agent_meta_bank_shadow_valid[agent_meta_store_active_bank])
		return -1;
	base = &agent_meta_bank_shadow[agent_meta_store_active_bank];
	if (base->header.magic != AGENT_META_STORE_MAGIC ||
	    base->header.version != AGENT_META_STORE_VERSION ||
	    base->header.generation != agent_meta_store_generation ||
	    base->header.count > AGENT_FILE_META_MAX)
		return -1;
	memset(store, 0, sizeof(*store));
	memset(used_slots, 0, sizeof(used_slots));
	store->header.magic = AGENT_META_STORE_MAGIC;
	store->header.version = AGENT_META_STORE_VERSION;
	store->header.generation = agent_meta_store_generation + 1;
	store->durable = base->durable;
	if (scope_id != VFS_SCOPE_SYSTEM &&
	    vfs_scope_lifecycle(scope_id, &lifecycle) < 0)
		lifecycle = workflow_lifecycle_none();
	if (agent_durable_arena_update_scope(
		    &store->durable, scope_id, lifecycle, durable_serial) < 0)
		return -1;
	for (uint64 i = 0; i < base->header.count; i++) {
		struct agent_meta_record *record = &base->records[i];

		if (scope_id == VFS_SCOPE_SYSTEM &&
		    (stale_slots[record->slot / 8] &
		     (1U << (record->slot % 8))) != 0)
			continue;
		if (record->scope_id != scope_id &&
		    store_append(store, record, used_slots) < 0)
			return -1;
	}
	base_count = store->header.count;
	exported = agent_metadata_catalog_export_scope(
		scope_id, &store->records[base_count],
		AGENT_FILE_META_MAX - base_count, size_sequence);
	if (exported < 0)
		return -1;
	for (int i = 0; i < exported; i++)
		if (store_append(
			    store, &store->records[base_count + i],
			    used_slots) < 0)
			return -1;
	store_sort(store);
	return 0;
}

int agent_metadata_store_shadow_has_scope(uint scope_id)
{
	struct agent_meta_store *store;

	if (agent_meta_store_active_bank < 0 ||
	    agent_meta_store_active_bank >= AGENT_META_STORE_BANKS ||
	    !agent_meta_bank_shadow_valid[agent_meta_store_active_bank])
		return agent_durable_section_scope_pending(scope_id);
	store = &agent_meta_bank_shadow[agent_meta_store_active_bank];
	for (uint64 i = 0; i < store->header.count; i++)
		if (store->records[i].scope_id == scope_id)
			return 1;
	return agent_durable_arena_has_scope(&store->durable, scope_id) ||
	       agent_durable_section_scope_pending(scope_id);
}

static int agent_meta_persist_target_locked(int target_bank, int force_full)
{
	struct agent_meta_store *store = &store_buf;
	struct inode *ip;
	int lookup_status;

	if (target_bank < 0 || target_bank >= AGENT_META_STORE_BANKS)
		return -1;
	ip = agent_meta_store_io_lookup_bank(
		agent_meta_store_io_name(target_bank), 1, &lookup_status);
	if (ip == 0)
		return lookup_status < 0 ? lookup_status : VIRTIO_DISK_ERR_IO;
	agent_meta_persist.target_bank = target_bank;
	agent_meta_persist.target_dev = ip->dev;
	agent_meta_persist.target_inum = ip->inum;
	agent_meta_persist.target_incarnation = ip->vfs_incarnation;
	iput(ip);
	agent_meta_persist_prepare_blocks(store, target_bank);
	if (force_full) {
		memset(agent_meta_persist.dirty_blocks, 0xff, sizeof(agent_meta_persist.dirty_blocks));
		agent_meta_bank_delta_invalidate(agent_meta_persist.target_bank);
	}
	agent_meta_persist.write_offset = sizeof(store->header);
	agent_meta_persist.verify_offset = sizeof(store->header);
	agent_meta_persist.verify_hash = AGENT_META_STORE_HASH_INITIAL;
	agent_meta_persist.verify_hash = agent_meta_format_hash_mix(
		agent_meta_persist.verify_hash, store->header.magic);
	agent_meta_persist.verify_hash = agent_meta_format_hash_mix(
		agent_meta_persist.verify_hash, store->header.version);
	agent_meta_persist.verify_hash = agent_meta_format_hash_mix(
		agent_meta_persist.verify_hash, store->header.count);
	agent_meta_persist.verify_hash = agent_meta_format_hash_mix(
		agent_meta_persist.verify_hash, store->header.generation);
	/* Binding an overwrite target ends the two-bank proof for the active
	 * generation before INVALIDATE can make that fact externally visible. */
	agent_meta_store_set_replicated_generation(0);
	agent_meta_persist.phase = AGENT_META_PERSIST_INVALIDATE;
	return 0;
}

static int agent_meta_store_prepare_banks_locked(void)
{
	struct vfs_cred kernel_cred;
	int flush_status;

	if (banks_prepared)
		return 0;
	vfs_cred_kernel(&kernel_cred);
	for (int bank = 0; bank < AGENT_META_STORE_BANKS; bank++) {
		int lookup_status;
		int inode_status;
		struct inode *ip = agent_meta_store_io_lookup_bank(
			agent_meta_store_io_name(bank), 1, &lookup_status);

		if (ip == 0)
			return lookup_status < 0 ? lookup_status :
						 VIRTIO_DISK_ERR_IO;
		inode_status = ivalid(ip);
		if (inode_status >= 0 &&
		    (ip->type != T_FILE || !vfs_inode_label_valid(ip) ||
		     ip->vfs_policy != VFS_POLICY_KERNEL_PRIVATE))
			inode_status = VIRTIO_DISK_ERR_IO;
		if (inode_status >= 0)
			inode_status = fs_preallocate_inode(
				ip, &kernel_cred, sizeof(struct agent_meta_store));
		iput(ip);
		if (inode_status < 0)
			return inode_status;
	}
	flush_status = bio_durable_flush();
	if (flush_status < 0)
		return flush_status;
	banks_prepared = 1;
	return 0;
}

static int agent_meta_store_active_verified(void)
{
	int bank = agent_meta_store_active_bank;
	struct agent_meta_store *store;

	if (bank < 0 || bank >= AGENT_META_STORE_BANKS ||
	    !agent_meta_bank_shadow_valid[bank])
		return 0;
	store = &agent_meta_bank_shadow[bank];
	return store->header.magic == AGENT_META_STORE_MAGIC &&
	       store->header.version == AGENT_META_STORE_VERSION &&
	       store->header.generation == agent_meta_store_generation &&
	       store->header.payload_hash == agent_meta_format_store_hash(store);
}

static void agent_meta_persist_retry_next_tick(void)
{
	uint64 retry_tick = agent_ticks();

	if (retry_tick != ~0ULL)
		retry_tick++;
	agent_meta_persist.retry_tick = retry_tick;
}

static void agent_meta_store_require_mirror(int bank, int invalidate)
{
	if (invalidate)
		agent_meta_bank_shadow_invalidate(bank);
	agent_meta_store_set_replicated_generation(0);
	agent_meta_store_recovery_required = 1;
	pending_repair_mode = AGENT_META_REPAIR_MIRROR;
	pending_repair_bank = bank;
}

static void agent_meta_persist_release_locked(int cancel_test)
{
	if (cancel_test)
		agent_metadata_test_eio_cancel(agent_meta_persist.scope_id,
					       agent_meta_persist.job_id);
	agent_meta_store_io_leave();
	memset(&agent_meta_persist, 0, sizeof(agent_meta_persist));
	wait_queue_wake_all(&waiters);
}

static int agent_meta_persist_start_locked(uint owner)
{
	struct agent_meta_store *store = &store_buf;
	uint store_bytes;
	uint scope_id;
	uint64 captured_generation;
	uint64 size_sequence;
	uint64 durable_serial;
	int target_bank;
	int operation_status;

	agent_metadata_txn_projection_require_idle();

	if (agent_meta_persist.phase != AGENT_META_PERSIST_IDLE)
		return 0;
	agent_meta_persist.error_cause = AGENT_METADATA_PERSIST_NONE;
	if (agent_meta_store_generation == ~0ULL) {
		agent_meta_persist.error_cause = AGENT_METADATA_PERSIST_DURABILITY;
		return -1;
	}
	if (!agent_file_loaded || !agent_meta_store_active_verified()) {
		agent_meta_persist.error_cause = AGENT_METADATA_PERSIST_RECOVERY;
		return -1;
	}
	if (!agent_meta_store_io_enter())
		return AGENT_META_PERSIST_DEFERRED;
	operation_status = agent_meta_store_prepare_banks_locked();
	if (operation_status < 0) {
		agent_meta_store_io_leave();
		return agent_meta_persist_device_error(operation_status);
	}
	if (owner != FS_OWNER_SYSTEM && !FS_OWNER_IS_SCOPE(owner))
		owner = FS_OWNER_SYSTEM;
	scope_id = FS_OWNER_IS_SCOPE(owner) ? FS_OWNER_SCOPE_ID(owner) :
		VFS_SCOPE_SYSTEM;
	captured_generation = agent_file_writeback_capture_state(scope_id);
	if (agent_meta_store_build_scope(
		    store, scope_id, &size_sequence, &durable_serial) < 0 ||
	    agent_meta_format_store_bytes(store->header.count, &store_bytes) < 0)
		goto fail;
	store->header.payload_hash = agent_meta_format_store_hash(store);
	target_bank = agent_meta_store_active_bank == 0 ? 1 : 0;
	memset(&agent_meta_persist, 0, sizeof(agent_meta_persist));
	agent_meta_persist.owner = owner;
	agent_meta_persist.scope_id = scope_id;
	if (next_job_id == 0)
		next_job_id = 1;
	agent_meta_persist.job_id = next_job_id++;
	agent_meta_persist.store_bytes = store_bytes;
	agent_meta_persist.expected_generation = store->header.generation;
	agent_meta_persist.expected_hash = store->header.payload_hash;
	agent_meta_persist.captured_generation = captured_generation;
	agent_meta_persist.size_sequence = size_sequence;
	agent_meta_persist.durable_serial = durable_serial;
	operation_status = agent_meta_persist_target_locked(target_bank, 0);
	if (operation_status < 0) {
		agent_meta_store_io_leave();
		memset(&agent_meta_persist, 0, sizeof(agent_meta_persist));
		return agent_meta_persist_device_error(operation_status);
	}
	agent_metadata_test_bind(scope_id, captured_generation,
				 agent_meta_persist.job_id);
	agent_metadata_test_eio_start(scope_id, agent_meta_persist.job_id);
	return 0;
fail:
	agent_meta_store_io_leave();
	return -1;
}

static void agent_meta_persist_abort_locked(void)
{
	if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE)
		return;
	agent_meta_store_require_mirror(
		agent_meta_persist.target_bank,
		!agent_meta_persist.published ||
		agent_meta_persist.target_bank != agent_meta_store_active_bank);
	// Repair the peer under the SYSTEM budget.
	agent_metadata_store_mark_dirty(VFS_SCOPE_SYSTEM);
	agent_metadata_store_expedite(VFS_SCOPE_SYSTEM);
	agent_meta_persist_release_locked(1);
}

/* Post-publication errors fail closed. */
static void agent_meta_persist_fail_closed_locked(void)
{
	if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE)
		return;
	agent_meta_bank_shadow_invalidate(agent_meta_persist.target_bank);
	agent_metadata_recovery_cancel();
	agent_meta_store_failed_closed = 1;
	store_failure =
		agent_meta_persist.error_cause != AGENT_METADATA_PERSIST_NONE ?
			agent_meta_persist.error_cause :
			AGENT_METADATA_PERSIST_FAIL_CLOSED;
	agent_meta_store_recovery_required = 1;
	agent_meta_persist_release_locked(1);
}

static void agent_meta_persist_primary_publish_locked(void)
{
	agent_meta_bank_shadow_install(
		&store_buf, agent_meta_persist.target_bank,
		agent_meta_persist.write_limit, 1);
	agent_meta_store_active_bank = agent_meta_persist.target_bank;
	agent_meta_store_generation = agent_meta_persist.expected_generation;
	agent_durable_section_active_bind(
		&agent_meta_bank_shadow[agent_meta_store_active_bank].durable,
		agent_meta_store_generation);
	agent_meta_persist.published = 1;
	agent_file_state_sizes_persisted(
		agent_meta_persist.scope_id,
		agent_meta_persist.size_sequence);
	agent_file_writeback_advance(0);
	agent_durable_section_commit_scope(
		agent_meta_persist.scope_id,
		agent_meta_persist.durable_serial);
	/* One verified copy ends the foreground contract. */
	agent_background_request();
}

static int agent_meta_persist_begin_mirror_locked(int force_full)
{
	int mirror_bank = agent_meta_store_active_bank == 0 ? 1 : 0;
	int result;

	result = agent_meta_persist_target_locked(mirror_bank, force_full);
	if (result < 0) {
		agent_meta_bank_shadow_invalidate(mirror_bank);
		return result;
	}
	// Bind the mirror before publishing its phase.
	agent_meta_persist.mirroring = 1;
	agent_meta_persist.retry_failures = 0;
	agent_meta_persist.retry_tick = 0;
	return 0;
}

// Irrevocable jobs become SYSTEM repair; earlier jobs abort.
static int agent_meta_persist_note_failure_locked(void)
{
	if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE)
		return -1;
	if (!agent_meta_persist.published &&
	    !agent_meta_persist.irrevocable) {
		agent_meta_persist_abort_locked();
		return -1;
	}
	if (agent_meta_store_active_verified()) {
		if (agent_meta_persist.primary_verified &&
		    !agent_meta_persist.mirroring) {
			agent_meta_persist.mirroring = 1;
			agent_meta_persist.target_bank =
				agent_meta_store_active_bank == 0 ? 1 : 0;
		}
		agent_meta_store_require_mirror(agent_meta_persist.target_bank, 1);
		agent_meta_persist.owner = FS_OWNER_SYSTEM;
		agent_meta_persist.restart_target = 1;
		agent_meta_persist.retry_failures = 0;
		agent_metadata_test_eio_cancel(agent_meta_persist.scope_id,
					       agent_meta_persist.job_id);
		agent_background_request();
		return 0;
	}
	agent_meta_persist_fail_closed_locked();
	return -1;
}

// Only a verified header publishes a bank.
static int agent_meta_persist_step_locked(void)
{
	struct agent_meta_store *store = &store_buf;
	struct agent_meta_persist_state *state = &agent_meta_persist;
	struct agent_meta_store_header invalid_header;
	struct agent_meta_store_header verified_header;
	struct inode *ip;
	struct vfs_cred kernel_cred;
	uint chunk;
	int result = 0;
	int n = 0;

	agent_metadata_txn_projection_require_idle();
	state->error_cause = AGENT_METADATA_PERSIST_NONE;

	if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE)
		return 1;
	if (state->target_bank < 0 || state->target_bank >= AGENT_META_STORE_BANKS)
		panic("invalid metadata persist bank");
	if (agent_meta_persist.restart_target) {
		int target_bank = state->target_bank;

		state->restart_target = 0;
		n = agent_meta_persist_target_locked(target_bank, 1);
		if (n < 0) {
			state->restart_target = 1;
			return agent_meta_persist_device_error(n);
		}
		return 0;
	}
	if (state->phase == AGENT_META_PERSIST_COMMIT) {
		if (!state->mirroring) {
			if (!state->primary_verified)
				return agent_meta_persist_device_error(
					VIRTIO_DISK_ERR_IO);
			n = agent_meta_persist_begin_mirror_locked(0);
			if (n < 0)
				return agent_meta_persist_device_error(n);
			return 0;
		}
		uint scope_id = state->scope_id;
		uint64 replicated_generation = state->expected_generation;

		agent_meta_store_set_replicated_generation(replicated_generation);
		agent_meta_persist_release_locked(0);
		/* Completion requires replication and an idle lane. */
		agent_durable_section_mirror_scope(scope_id);
		return 1;
	}
	if (state->phase == AGENT_META_PERSIST_WRITE &&
	    state->write_offset < state->write_limit &&
	    !agent_meta_persist_block_dirty(state->write_offset)) {
		state->write_offset = agent_meta_persist_segment_end(
			state->write_offset);
		return 0;
	}
	if (state->phase == AGENT_META_PERSIST_VERIFY_PAYLOAD &&
	    state->verify_offset < state->store_bytes &&
	    !agent_meta_persist_block_dirty(state->verify_offset)) {
		chunk = MIN(agent_meta_persist_segment_end(
				    state->verify_offset) - state->verify_offset,
			    state->store_bytes - state->verify_offset);
		state->verify_hash = agent_meta_format_hash_bytes(
			state->verify_hash, (char *)store + state->verify_offset,
			chunk);
		state->verify_offset += chunk;
		return 0;
	}
	ip = inode_get(state->target_dev, state->target_inum);
	if (ip == 0)
		return agent_meta_persist_device_error(VIRTIO_DISK_ERR_IO);
	n = ivalid(ip);
	if (n < 0) {
		iput(ip);
		return agent_meta_persist_device_error(n);
	}
	if (ip->type != T_FILE || !vfs_inode_label_valid(ip) ||
	    ip->vfs_policy != VFS_POLICY_KERNEL_PRIVATE ||
	    ip->vfs_incarnation != state->target_incarnation) {
		iput(ip);
		return agent_meta_persist_device_error(VIRTIO_DISK_ERR_IO);
	}
	vfs_cred_kernel(&kernel_cred);
	switch (state->phase) {
	case AGENT_META_PERSIST_INVALIDATE:
	case AGENT_META_PERSIST_PUBLISH:
	{
		uint checkpoint = agent_meta_persist.phase;
		struct agent_meta_store_header *header = &store->header;

		if (checkpoint == 1)
			agent_meta_crash_checkpoint(checkpoint);
		agent_meta_eio_checkpoint(checkpoint);
		if (checkpoint == 1) {
			memset(&invalid_header, 0, sizeof(invalid_header));
			header = &invalid_header;
		} else
			/* Header writes make disk state unknown. */
			state->irrevocable = 1;
		n = writei(ip, &kernel_cred, 0, (uint64)header, 0,
			   sizeof(*header));
		if (n == (int)sizeof(*header)) {
			if (checkpoint == 1)
				state->write_offset = sizeof(*header);
			else
				agent_meta_crash_checkpoint(checkpoint);
			state->phase++;
		} else
			result = n == 0 ? AGENT_META_PERSIST_DEFERRED : -1;
		break;
	}
	case AGENT_META_PERSIST_FLUSH_INVALID:
	case AGENT_META_PERSIST_FLUSH_PAYLOAD:
	case AGENT_META_PERSIST_FLUSH_HEADER:
	{
		uint checkpoint = agent_meta_persist.phase;

		agent_meta_eio_checkpoint(checkpoint);
		n = bio_durable_flush();
		if (n < 0)
			result = agent_meta_persist_device_error(n);
		else {
			if (checkpoint == 4)
				state->verify_offset = sizeof(store->header);
			state->phase++;
			agent_meta_crash_checkpoint(checkpoint);
		}
		break;
	}
	case AGENT_META_PERSIST_WRITE:
		if (state->write_offset >= state->write_limit) {
			agent_meta_crash_checkpoint(3);
			state->phase = AGENT_META_PERSIST_FLUSH_PAYLOAD;
			break;
		}
		chunk = agent_meta_persist_segment_end(
			state->write_offset) - state->write_offset;
		agent_meta_eio_checkpoint(3);
		n = writei(ip, &kernel_cred, 0,
			   (uint64)((char *)store + state->write_offset),
			   state->write_offset, chunk);
		if (n > 0)
			state->write_offset += n;
		else if (n == 0)
			result = AGENT_META_PERSIST_DEFERRED;
		break;
	case AGENT_META_PERSIST_VERIFY_HEADER:
		memset(&verified_header, 0, sizeof(verified_header));
		agent_meta_eio_checkpoint(8);
		n = readi_device(ip, &kernel_cred, 0,
				 (uint64)&verified_header, 0,
				 sizeof(store->header));
		if (n == 0) {
			result = AGENT_META_PERSIST_DEFERRED;
			break;
		}
		if (n < 0) {
			iput(ip);
			return agent_meta_persist_device_error(n);
		}
		if (n != (int)sizeof(store->header) ||
		    memcmp(&verified_header, &store->header,
			   sizeof(store->header)) != 0 ||
		    ip->size < state->store_bytes)
			goto invalid_target;
		if (!state->mirroring) {
			agent_meta_persist_primary_publish_locked();
			state->primary_verified = 1;
			agent_meta_crash_checkpoint(8);
			if (state->scope_id == VFS_SCOPE_SYSTEM)
				memset(stale_slots, 0, sizeof(stale_slots));
		} else {
			if (!state->primary_verified)
				goto invalid_target;
			agent_meta_crash_checkpoint(8);
			agent_meta_bank_shadow_install(
				store, state->target_bank,
				agent_meta_persist.write_limit, 1);
			agent_meta_store_recovery_required = 0;
			pending_repair_mode = AGENT_META_REPAIR_NONE;
			pending_repair_bank = -1;
			agent_file_writeback_advance(1);
			agent_metadata_test_eio_commit(
				state->scope_id, state->job_id);
		}
		state->phase = AGENT_META_PERSIST_COMMIT;
		break;
	case AGENT_META_PERSIST_VERIFY_PAYLOAD:
		if (ip->size < state->store_bytes)
			goto invalid_target;
		if (state->verify_offset >= state->store_bytes) {
			if (state->verify_hash == state->expected_hash) {
				agent_meta_crash_checkpoint(5);
				state->phase = AGENT_META_PERSIST_PUBLISH;
			} else
				goto invalid_target;
			break;
		}
		chunk = MIN(BSIZE - state->verify_offset % BSIZE,
			    state->store_bytes - state->verify_offset);
		agent_meta_eio_checkpoint(5);
		n = readi_device(ip, &kernel_cred, 0,
				 (uint64)state->verify_block,
				 state->verify_offset, chunk);
		if (n > 0) {
			if (memcmp(state->verify_block,
				   (char *)store + state->verify_offset, n) != 0)
				goto invalid_target;
			state->verify_hash = agent_meta_format_hash_bytes(
				state->verify_hash, state->verify_block, n);
			state->verify_offset += n;
		} else if (n == 0)
			result = AGENT_META_PERSIST_DEFERRED;
		break;
	default:
		iput(ip);
		panic("invalid metadata persist phase");
	}
	iput(ip);
	if (n < 0 && agent_meta_persist.error_cause ==
			     AGENT_METADATA_PERSIST_NONE)
		result = agent_meta_persist_device_error(n);
	if (result < 0 && agent_meta_persist.error_cause ==
			  AGENT_METADATA_PERSIST_NONE)
		agent_meta_persist.error_cause = AGENT_METADATA_PERSIST_IO;
	return result;
invalid_target:
	iput(ip);
	return agent_meta_persist_device_error(VIRTIO_DISK_ERR_IO);
}

static int agent_meta_persist_background_step_locked(uint owner,
						     int defer_retry)
{
	int step = 0;

	if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE)
		step = agent_meta_persist_start_locked(owner);
	if (step >= 0)
		step = agent_meta_persist_step_locked();
	if (step == AGENT_META_PERSIST_DEFERRED) {
		if (defer_retry)
			agent_meta_persist_retry_next_tick();
	} else if (step < 0) {
		agent_meta_persist_note_failure_locked();
	} else {
		agent_meta_persist.retry_failures = 0;
		agent_meta_persist.retry_tick = 0;
	}
	return step;
}

static int agent_file_persist(struct agent_metadata_persist_result *completion)
{
	void *token = agent_metadata_txn_token();
	uint owner;
	uint scope_id = VFS_SCOPE_SYSTEM;
	uint64 target_generation = 0;
	uint step_limit = AGENT_META_PRIMARY_STEP_LIMIT;
	int started_here = 0;
	int status = -1;
	int failure_cause = AGENT_METADATA_PERSIST_RETRY;
	int failure_irrevocable = 0;

	if (completion) {
		memset(completion, 0, sizeof(*completion));
		completion->status = -1;
	}

	if (!agent_metadata_txn_lock(1)) {
		if (completion)
			completion->cause = AGENT_METADATA_PERSIST_INTERRUPTED;
		return -1;
	}
	if (submitter != 0 && submitter != token)
		goto out;
	submitter = token;
	owner = bio_current_owner();
	scope_id = FS_OWNER_IS_SCOPE(owner) ? FS_OWNER_SCOPE_ID(owner) :
		VFS_SCOPE_SYSTEM;
	if (agent_meta_store_failed_closed ||
	    agent_metadata_recovery_pending()) {
		failure_cause = AGENT_METADATA_PERSIST_FAIL_CLOSED;
		goto out;
	}
	if (agent_meta_store_recovery_required && owner != FS_OWNER_SYSTEM) {
		failure_cause = AGENT_METADATA_PERSIST_RECOVERY;
		goto out;
	}
	target_generation = scope_target(scope_id);
	if (completion)
		completion->completion_token = target_generation;
	if (target_generation == 0) {
		status = 0;
		goto out;
	}
	for (uint steps = 0; steps < step_limit; steps++) {
		uint64 job_id;
		int same_job;
		struct bio_checkpoint_result checkpoint =
			bio_checkpoint_make(BIO_CHECKPOINT_READY);

		if (scope_reached(
			    scope_id, target_generation, 0)) {
			status = 0;
			break;
		}
		if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE) {
			int start_status = agent_meta_persist_start_locked(owner);

			if (start_status == AGENT_META_PERSIST_DEFERRED) {
				failure_cause = AGENT_METADATA_PERSIST_RETRY;
				break;
			}
			if (start_status < 0) {
				failure_cause = agent_meta_persist.error_cause ==
						AGENT_METADATA_PERSIST_NONE ?
						AGENT_METADATA_PERSIST_IO :
						agent_meta_persist.error_cause;
				failure_irrevocable = agent_meta_persist.irrevocable;
				break;
			}
			started_here = 1;
		} else if (!started_here) {
			/* Enter the physical lane before mutation. */
			break;
		} else if (agent_meta_persist.owner != owner) {
			break;
		}
		job_id = agent_meta_persist.job_id;
		if (completion)
			completion->job_id = job_id;
		int step = agent_meta_persist_step_locked();

		if (step == AGENT_META_PERSIST_DEFERRED) {
			failure_cause = AGENT_METADATA_PERSIST_RETRY;
			if (agent_meta_persist.retry_tick == 0)
				agent_meta_persist_retry_next_tick();
			break;
		}
		if (step < 0) {
			int failure;

			failure_cause = agent_meta_persist.error_cause ==
						AGENT_METADATA_PERSIST_NONE ?
					AGENT_METADATA_PERSIST_IO :
					agent_meta_persist.error_cause;
			failure_irrevocable = agent_meta_persist.irrevocable &&
				agent_meta_persist.scope_id == scope_id;
			failure = agent_meta_persist_note_failure_locked();

			if (failure < 0)
				break;
			if (agent_meta_persist.retry_tick == 0)
				agent_meta_persist_retry_next_tick();
			break;
		}
		agent_meta_persist.retry_failures = 0;
		// job_id detects replacement across the checkpoint.
		checkpoint = agent_meta_persist_checkpoint_unlocked(
			job_id, &same_job);
		if (scope_reached(
			    scope_id, target_generation, 0)) {
			status = 0;
			break;
		}
		if (!same_job) {
			failure_cause = AGENT_METADATA_PERSIST_RETRY;
			break;
		}
		if (bio_checkpoint_should_stop(checkpoint)) {
			failure_cause = checkpoint.state == BIO_CHECKPOINT_DEFERRED ?
				AGENT_METADATA_PERSIST_RETRY :
				AGENT_METADATA_PERSIST_INTERRUPTED;
			failure_irrevocable = agent_meta_persist.irrevocable &&
				agent_meta_persist.scope_id == scope_id;
			if (!agent_meta_persist.irrevocable && same_job)
				agent_meta_persist_abort_locked();
			break;
		}
	}
out:
	if (status < 0 && started_here &&
	    agent_meta_persist.phase != AGENT_META_PERSIST_IDLE &&
	    !agent_meta_persist.irrevocable &&
	    agent_meta_persist.scope_id == scope_id)
		agent_meta_persist_abort_locked();
	if (completion) {
		completion->status = status;
		completion->cause = status == 0 ? AGENT_METADATA_PERSIST_NONE :
						 failure_cause;
		completion->durable = status == 0;
		completion->irrevocable = failure_irrevocable;
		if (status < 0 && !completion->irrevocable && started_here &&
		    agent_meta_persist.irrevocable &&
		    agent_meta_persist.scope_id == scope_id)
			completion->irrevocable = 1;
	}
	if (submitter == token) {
		submitter = 0;
		wait_queue_wake_all(&waiters);
	}
	agent_metadata_txn_unlock();
	return status;
}

static int agent_meta_persist_drain_owner(uint owner)
{
	uint limit = AGENT_META_REPLICATED_STEP_LIMIT;
	int result = -1;

	for (uint steps = 0; steps < limit; steps++) {
		int started_background = 0;
		int step;

		if (!agent_metadata_txn_lock(1))
			break;
		if (agent_meta_store_failed_closed ||
		    agent_metadata_recovery_pending() ||
		    (submitter != 0 &&
		     submitter != agent_metadata_txn_token())) {
			agent_metadata_txn_unlock();
			break;
		}
		if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE) {
			uint scope_id = FS_OWNER_IS_SCOPE(owner) ?
				FS_OWNER_SCOPE_ID(owner) : VFS_SCOPE_SYSTEM;

			if (scope_target(scope_id) == 0) {
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
		step = agent_meta_persist_background_step_locked(owner, 0);
		if (started_background)
			bio_background_end();
		agent_metadata_txn_unlock();
		if (step == AGENT_META_PERSIST_DEFERRED || step < 0)
			break;
		if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE) {
			result = 0;
			break;
		}
		// A caller-owned lease is one scheduler quantum.
		if (!started_background)
			break;
	}
	return result;
}

static int agent_file_persist_system(void)
{
	return agent_meta_persist_drain_owner(FS_OWNER_SYSTEM);
}

static int
agent_meta_durable_persist_scope(uint scope_id)
{
	if (scope_id == VFS_SCOPE_SYSTEM)
		return agent_file_persist_system();
	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG)
		return -1;
	return agent_meta_persist_drain_owner(FS_OWNER_SCOPE(scope_id));
}

static void agent_file_writeback_maintain(void)
{
	uint64 now = agent_ticks();
	uint owner;
	int step = 0;

	if (!agent_file_loaded || agent_meta_store_failed_closed ||
	    agent_metadata_recovery_pending())
		return;
	if (submitter != 0)
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
	if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE)
		step = agent_meta_persist_start_locked(owner);
	if (step >= 0)
		step = agent_meta_persist_background_step_locked(owner, 1);
	agent_metadata_txn_unlock();
	if (step == AGENT_META_PERSIST_DEFERRED &&
	    agent_meta_persist.phase == AGENT_META_PERSIST_IDLE) {
		agent_file_writeback_defer();
	} else if (step < 0) {
		if (agent_meta_persist.phase != AGENT_META_PERSIST_IDLE)
			agent_meta_persist.retry_tick =
				agent_file_writeback_rest_deadline(agent_ticks());
		agent_file_writeback_defer();
	}
out_io:
	bio_background_end();
}

int
agent_metadata_store_loaded(void)
{
	return agent_file_loaded;
}

int
agent_metadata_store_available(void)
{
	return !agent_meta_store_failed_closed &&
	       !agent_metadata_recovery_pending();
}

void
agent_metadata_store_fail_closed_runtime(void)
{
	if (agent_meta_persist.phase != AGENT_META_PERSIST_IDLE)
		agent_metadata_test_eio_cancel(agent_meta_persist.scope_id,
					       agent_meta_persist.job_id);
	agent_metadata_recovery_cancel();
	agent_meta_store_failed_closed = 1;
	agent_meta_store_set_replicated_generation(0);
	store_failure =
		AGENT_METADATA_PERSIST_FAIL_CLOSED;
	agent_meta_store_recovery_required = 1;
	wait_queue_wake_all(&waiters);
}

int
agent_metadata_store_take_reconcile_request(void)
{
	int requested;
	int enabled = intr_save();

	requested = agent_meta_reconcile_required;
	agent_meta_reconcile_required = 0;
	intr_restore(enabled);
	return requested;
}

int
agent_metadata_store_persist(void)
{
	return agent_file_persist(0);
}

int
agent_metadata_store_persist_commit(
	struct agent_metadata_persist_result *completion)
{
	if (completion == 0)
		return -1;
	return agent_file_persist(completion);
}

int
agent_metadata_store_persist_system(void)
{
	return agent_file_persist_system();
}

int
agent_metadata_store_scope_target_done(uint scope_id, uint64 target)
{
	int retired;

	if (!agent_metadata_txn_lock(0))
		return 0;
	retired = agent_file_scope_state_retire(scope_id, target);
	agent_metadata_txn_unlock();
	return retired;
}

void
agent_metadata_store_background_maintain(void)
{
	int durable_pending = agent_durable_section_retry_pending();

	agent_file_writeback_maintain();
	if (durable_pending || agent_file_writeback_pending())
		agent_background_request();
}
