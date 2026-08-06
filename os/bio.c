// Buffer cache and block-I/O admission policy.

#include "bio.h"
#include "agent.h"
#include "defs.h"
#include "fs.h"
#include "fs_epoch.h"
#include "performance_stats.h"
#include "riscv.h"
#include "types.h"
#include "vfs_security.h"
#include "virtio.h"

/* Processes in one workflow share its lifecycle-scoped I/O owner. */
#define IO_OWNER_SLOTS (VFS_SCOPE_LIFECYCLE_CAP + 2)
_Static_assert(IO_OWNER_SLOTS >= VFS_SCOPE_MAX_ACTIVE + 2,
	       "I/O owner table must cover every active workflow");
#define IO_ADMISSION_READY_SLOTS \
	(IO_OWNER_SLOTS * IO_POLICY_CLASS_COUNT)
_Static_assert(IO_ADMISSION_READY_SLOTS <= 64,
	       "I/O admission ready set must fit one machine bitmap");
#define IO_RESERVATION_NONE 0U
#define IO_RESERVATION_SHARED (1U << 31)
#define IO_RESERVATION_SLOT_MASK (~IO_RESERVATION_SHARED)
#define IO_RATE_LOCAL_BATCH 32U
#define BIO_REQUEST_ACTIVE (1U << 0)
#define BIO_REQUEST_LAZY (1U << 1)
#define BIO_REQUEST_CLEANUP (1U << 2)
#define BIO_REQUEST_TRANSFERRED (1U << 3)
#define BIO_REQUEST_KNOWN_FLAGS \
	(BIO_REQUEST_ACTIVE | BIO_REQUEST_LAZY | BIO_REQUEST_CLEANUP | \
	 BIO_REQUEST_TRANSFERRED)
#define IO_RATE_GLOBAL_SHARED 0U
#define IO_RATE_GLOBAL_DEVICE 1U
#define IO_CACHE_CLEANUP_FLOOR 3U
_Static_assert(RESOURCE_RATE_LANE_CAP == IO_POLICY_CLASS_COUNT,
	       "rate lanes must match the exported I/O classes");
_Static_assert(RESOURCE_RATE_GLOBAL_CAP >= 2,
	       "I/O policy needs shared and device rate pools");
_Static_assert(RESOURCE_RATE_LEASE_CAP >= NPROC * NTHREAD + 1,
	       "every live thread plus background cleanup needs a rate lease");
_Static_assert(RESOURCE_RATE_LEASE_CAP < IO_RESERVATION_SHARED,
	       "I/O lease slot must leave room for the shared-source tag");
_Static_assert(IO_CACHE_PUBLIC_FLOOR >= 3 &&
	       IO_CACHE_WORKFLOW_FLOOR >= 3,
	       "each untrusted cache partition must support nested FS buffers");
_Static_assert(IO_CACHE_SYSTEM_FLOOR + IO_CACHE_PUBLIC_FLOOR +
	       VFS_SCOPE_MAX_ACTIVE * IO_CACHE_WORKFLOW_FLOOR +
	       VFS_SCOPE_MAX_RETIRING * IO_CACHE_CLEANUP_FLOOR <= NBUF,
	       "buffer-cache floors must fit NBUF");
_Static_assert(IO_POLICY_OWNER_SYSTEM == FS_OWNER_SYSTEM &&
	       IO_POLICY_OWNER_PUBLIC == FS_OWNER_PUBLIC &&
	       IO_POLICY_OWNER_SCOPE_FLAG == FS_OWNER_SCOPE_FLAG,
	       "I/O policy owner ABI must match filesystem principals");
_Static_assert(IO_POLICY_SYSTEM_REFILL +
	       IO_POLICY_SYSTEM_BACKGROUND_REFILL +
	       IO_POLICY_PUBLIC_NORMAL_REFILL +
	       VFS_SCOPE_MAX_ACTIVE *
		       (IO_POLICY_WORKFLOW_NORMAL_REFILL +
			IO_POLICY_WORKFLOW_CONTROL_REFILL +
			IO_POLICY_WORKFLOW_BACKGROUND_REFILL) +
	       VFS_SCOPE_MAX_RETIRING *
		       IO_POLICY_WORKFLOW_BACKGROUND_REFILL <=
		       IO_POLICY_DEVICE_REFILL,
	       "owner I/O guarantees must fit the device envelope");
_Static_assert(IO_POLICY_SYSTEM_BURST +
	       IO_POLICY_SYSTEM_BACKGROUND_BURST +
	       IO_POLICY_PUBLIC_NORMAL_BURST +
	       VFS_SCOPE_MAX_ACTIVE *
		       (IO_POLICY_WORKFLOW_NORMAL_BURST +
			IO_POLICY_WORKFLOW_CONTROL_BURST +
			IO_POLICY_WORKFLOW_BACKGROUND_BURST) +
	       VFS_SCOPE_MAX_RETIRING *
		       IO_POLICY_WORKFLOW_BACKGROUND_BURST <=
		       IO_POLICY_DEVICE_BURST,
	       "owner I/O bursts must fit the device envelope");
_Static_assert(IO_POLICY_SHARED_REFILL <= IO_POLICY_DEVICE_REFILL &&
	       IO_POLICY_SHARED_BURST <= IO_POLICY_DEVICE_BURST,
	       "opportunistic I/O gate must fit the device envelope");

struct io_bucket {
	uint admission_waiters;
	uint debt_waiters;
	struct thread *grantee;
	uint grant_source;
	uint grant_device_source;
	struct wait_queue admission_queue;
	struct wait_queue debt_queue;
};

struct bio_idle_queue {
	struct buf *head;
	struct buf *tail;
	uint count;
};

struct io_owner_state {
	int used;
	int retiring;
	int quiesced;
	int cache_live;
	uint owner;
	struct resource_account_handle principal;
	int principal_member;
	uint active_requests;
	uint deferred_references;
	struct io_bucket buckets[IO_POLICY_CLASS_COUNT];
	uint64 admissions;
	uint64 throttles;
	uint64 waits;
	uint64 refills;
	uint64 reserved_grants;
	uint64 shared_grants;
	uint64 physical_reads;
	uint64 physical_writes;
	uint64 physical_flushes;
	uint64 failed_transfers;
	uint64 cache_hits;
	uint64 cache_misses;
	uint64 cache_evictions;
	uint64 unreserved_transfers;
	uint64 completion_sequence;
	uint64 lazy_started;
	uint64 upgraded;
	uint64 cache_only;
	struct bio_idle_queue idle;
};

struct io_background_context {
	int active;
	struct thread *executor;
	uint64 executor_generation;
	int boot_executor;
	int cache_wait_pending;
	uint64 cache_wait_sequence;
	uint owner;
	uint reservation;
	uint device_reservation;
	uint transfers;
	uint buffer_holds;
	uint fs_atomic_depth;
};

struct io_deferred_sponsor {
	int active;
	int reuse_request_lease;
	int independent_lease;
	uint depth;
	struct thread *executor;
	uint64 executor_generation;
	uint64 origin_request_id;
	uint owner;
	uint retained_class;
	uint io_class;
	uint reservation;
	uint device_reservation;
	uint transfers;
	struct bio_cleanup_token token;
};

#define BIO_CLEANUP_TOKEN_INDEPENDENT (1U << 0)
#define BIO_CLEANUP_TOKEN_BACKGROUND (1U << 1)
#define BIO_CLEANUP_TOKEN_POLLING (1U << 2)
#define BIO_CLEANUP_TOKEN_SETTLING (1U << 3)
#define BIO_CLEANUP_TOKEN_TRANSFERRED (1U << 4)

enum bio_cleanup_token_state {
	BIO_CLEANUP_TOKEN_EMPTY = 0,
	BIO_CLEANUP_TOKEN_PREPARED,
	BIO_CLEANUP_TOKEN_ACTIVE,
	BIO_CLEANUP_TOKEN_ENDED,
};

/* One transferable receipt per physical thread, plus epoch/kernel executors. */
#define BIO_CLEANUP_TOKEN_CAP (NPROC * NTHREAD + 2U)
#define BIO_CLEANUP_SLOT_NONE ((uint16)0xffffU)

struct bio_cleanup_record {
	uint64 principal_generation;
	uint owner;
	uint reservation;
	uint device_reservation;
	uint generation;
	uint16 principal_slot;
	uint16 next_free;
	uchar state;
	uchar retained_class;
	uchar effective_class;
	uchar flags;
};

static struct {
	struct bio_cleanup_record records[BIO_CLEANUP_TOKEN_CAP];
	uint16 free_head;
	int initialized;
} bio_cleanup_pool;

_Static_assert(BIO_CLEANUP_TOKEN_CAP < 0xffffU,
	       "cleanup token slot must fit in the freelist link");
_Static_assert(RESOURCE_ACCOUNT_CAP < 0xffffU,
	       "cleanup principal slot must fit in its compact receipt");
_Static_assert(sizeof(struct bio_cleanup_token) == 8,
	       "cleanup token handle must stay stack-small");
_Static_assert(sizeof(struct bio_cleanup_record) <= 40,
	       "cleanup token pool must stay compact");

struct io_device_wait_state {
	uint debt_waiters;
	struct wait_queue debt_queue;
};

static struct {
	struct io_owner_state owners[IO_OWNER_SLOTS];
	struct io_device_wait_state device;
	struct io_background_context background;
	struct io_deferred_sponsor deferred;
	uint64 ready_bitmap;
	uint shared_cursor;
	uint cache_donor_cursor;
	uint64 physical_reads;
	uint64 physical_writes;
	uint64 physical_flushes;
	uint64 successful_writes;
	uint64 successful_flushes;
	uint64 failed_transfers;
	uint64 completion_sequence;
	uint64 victim_candidates_examined;
	uint64 lazy_started;
	uint64 upgraded;
	uint64 cache_only;
	uint max_victim_candidates_per_miss;
	int runtime_ready;
} io_policy;

static struct wait_queue cache_waiters;
static struct wait_queue background_cache_waiter;
static uint64 cache_progress_sequence;
static char bio_boot_holder_token;
static uint bio_boot_buffer_holds;
static uint bio_boot_fs_atomic_depth;
static uint64 bio_request_next_id;

#define BIO_CACHE_HASH_BUCKETS 64U
#define BIO_CACHE_VICTIM_PROBE_OVERHEAD 3U
_Static_assert((BIO_CACHE_HASH_BUCKETS & (BIO_CACHE_HASH_BUCKETS - 1)) == 0,
	       "buffer-cache hash size must be a power of two");

struct {
	struct buf buf[NBUF];
	struct bio_idle_queue free_idle;
	struct bio_idle_queue reserved_idle;
	struct buf *hash_head[BIO_CACHE_HASH_BUCKETS];
} bcache;
static uint64 bio_cache_integrity_seen[(NBUF + 63U) / 64U];

enum bio_idle_class {
	BIO_IDLE_NONE = 0,
	BIO_IDLE_FREE,
	BIO_IDLE_OWNER,
	BIO_IDLE_RESERVED,
};

static uint bio_cache_floor(uint owner);
static uint bio_cache_cap(uint owner);
static uint bio_cache_count(uint owner);
static int bio_cache_assign(struct buf *, uint, int, int);
static void bio_cache_invalidate(struct buf *b);
static void bio_cache_record(uint owner, int hit, int eviction);
static void bio_cache_idle_remove(struct buf *b);
static void bio_cache_idle_enqueue(struct buf *b, int promote);
static struct buf *bio_cache_donor_candidate(uint owner, uint *examined);

static uint bio_cache_hash_bucket(uint dev, uint blockno)
{
	return ((dev * 16777619U) ^ blockno) & (BIO_CACHE_HASH_BUCKETS - 1);
}

static struct buf *bio_cache_hash_find(uint dev, uint blockno)
{
	uint scanned = 0;

	for (struct buf *b = bcache.hash_head[
		     bio_cache_hash_bucket(dev, blockno)];
	     b != 0; b = b->hash_next) {
		if (scanned++ >= NBUF)
			panic("buffer-cache hash cycle");
		if (b->dev == dev && b->blockno == blockno)
			return b;
	}
	return 0;
}

static void bio_cache_hash_remove(struct buf *b)
{
	struct buf **link;
	uint scanned = 0;

	if (b->dev == 0)
		return;
	link = &bcache.hash_head[bio_cache_hash_bucket(b->dev, b->blockno)];
	while (*link != 0 && *link != b) {
		if (scanned++ >= NBUF)
			panic("buffer-cache hash cycle");
		link = &(*link)->hash_next;
	}
	if (*link != b)
		panic("buffer-cache hash unlink");
	*link = b->hash_next;
	b->hash_next = 0;
}

static void bio_cache_hash_insert(struct buf *b)
{
	uint bucket;

	if (b->dev == 0 || b->hash_next != 0 ||
	    bio_cache_hash_find(b->dev, b->blockno) != 0)
		panic("buffer-cache hash insert");
	bucket = bio_cache_hash_bucket(b->dev, b->blockno);
	b->hash_next = bcache.hash_head[bucket];
	bcache.hash_head[bucket] = b;
}

static int bio_background_current(void)
{
	struct thread *thread = curr_thread();

	if (!io_policy.background.active ||
	    io_policy.background.executor == 0 ||
	    io_policy.background.executor != thread ||
	    io_policy.background.executor_generation !=
		    thread->identity_generation)
		return 0;
	/* Generation zero belongs only to the permanent pre-runtime idle token. */
	if (io_policy.background.boot_executor)
		return !io_policy.runtime_ready &&
		       io_policy.background.executor_generation == 0;
	return io_policy.runtime_ready &&
	       io_policy.background.executor_generation != 0;
}

static int bio_deferred_sponsor_current(void)
{
	struct thread *thread = curr_thread();

	return io_policy.deferred.active && thread != 0 &&
	       io_policy.deferred.executor == thread &&
	       io_policy.deferred.executor_generation ==
		       thread->identity_generation;
}

int bio_deferred_polling_current(void)
{
	struct thread *thread = curr_thread();

	return bio_deferred_sponsor_current() && thread->tid < 0 &&
	       thread->identity_generation == 0;
}

static void bio_cache_advance_progress_locked(void)
{
	cache_progress_sequence++;
	if (cache_progress_sequence == 0)
		cache_progress_sequence = 1;
}

static void bio_cache_note_progress(void)
{
	int enabled = intr_save();

	bio_cache_advance_progress_locked();
	wait_queue_wake_all(&cache_waiters);
	wait_queue_wake_one(&background_cache_waiter);
	intr_restore(enabled);
}

void bio_cache_retry_notify(void)
{
	int enabled = intr_save();

	if (io_policy.background.cache_wait_pending) {
		bio_cache_advance_progress_locked();
		wait_queue_wake_one(&background_cache_waiter);
	}
	intr_restore(enabled);
}

static void bio_background_cache_retry_start(void)
{
	if (bio_background_current())
		io_policy.background.cache_wait_pending = 0;
}

static void bio_background_cache_blocked(void)
{
	if (!bio_background_current())
		panic("foreground cache retry classification");
	io_policy.background.cache_wait_pending = 1;
	io_policy.background.cache_wait_sequence = cache_progress_sequence;
}

static int bio_background_wait_for_cache_progress(void)
{
	int enabled = intr_save();

	if (!bio_background_current()) {
		intr_restore(enabled);
		return -1;
	}
	while (io_policy.background.cache_wait_pending &&
	       io_policy.background.cache_wait_sequence ==
		       cache_progress_sequence) {
		if (wait_queue_sleep_irq_uninterruptible(
			    &background_cache_waiter) !=
		    WAIT_QUEUE_OK) {
			intr_restore(enabled);
			return -1;
		}
	}
	io_policy.background.cache_wait_pending = 0;
	intr_restore(enabled);
	return 0;
}

static uint io_bucket_burst(uint owner, uint io_class)
{
	if (owner == FS_OWNER_SYSTEM) {
		if (io_class == IO_POLICY_CLASS_SYSTEM)
			return IO_POLICY_SYSTEM_BURST;
		if (io_class == IO_POLICY_CLASS_BACKGROUND)
			return IO_POLICY_SYSTEM_BACKGROUND_BURST;
		return 0;
	}
	if (owner == FS_OWNER_PUBLIC)
		return io_class == IO_POLICY_CLASS_NORMAL ?
			IO_POLICY_PUBLIC_NORMAL_BURST : 0;
	if (!FS_OWNER_IS_SCOPE(owner))
		return 0;
	if (io_class == IO_POLICY_CLASS_NORMAL)
		return IO_POLICY_WORKFLOW_NORMAL_BURST;
	if (io_class == IO_POLICY_CLASS_CONTROL)
		return IO_POLICY_WORKFLOW_CONTROL_BURST;
	if (io_class == IO_POLICY_CLASS_BACKGROUND)
		return IO_POLICY_WORKFLOW_BACKGROUND_BURST;
	return 0;
}

static uint io_bucket_refill(uint owner, uint io_class)
{
	if (owner == FS_OWNER_SYSTEM) {
		if (io_class == IO_POLICY_CLASS_SYSTEM)
			return IO_POLICY_SYSTEM_REFILL;
		if (io_class == IO_POLICY_CLASS_BACKGROUND)
			return IO_POLICY_SYSTEM_BACKGROUND_REFILL;
		return 0;
	}
	if (owner == FS_OWNER_PUBLIC)
		return io_class == IO_POLICY_CLASS_NORMAL ?
			IO_POLICY_PUBLIC_NORMAL_REFILL : 0;
	if (!FS_OWNER_IS_SCOPE(owner))
		return 0;
	if (io_class == IO_POLICY_CLASS_NORMAL)
		return IO_POLICY_WORKFLOW_NORMAL_REFILL;
	if (io_class == IO_POLICY_CLASS_CONTROL)
		return IO_POLICY_WORKFLOW_CONTROL_REFILL;
	if (io_class == IO_POLICY_CLASS_BACKGROUND)
		return IO_POLICY_WORKFLOW_BACKGROUND_REFILL;
	return 0;
}

static void io_rate_profiles(
	uint owner,
	struct resource_rate_profile profiles[RESOURCE_RATE_LANE_CAP])
{
	memset(profiles, 0,
	       sizeof(*profiles) * RESOURCE_RATE_LANE_CAP);
	for (uint lane = 0; lane < RESOURCE_RATE_LANE_CAP; lane++) {
		profiles[lane].burst = io_bucket_burst(owner, lane);
		profiles[lane].refill = io_bucket_refill(owner, lane);
		if (lane == IO_POLICY_CLASS_BACKGROUND &&
		    profiles[lane].burst != 0)
			profiles[lane].flags =
				RESOURCE_RATE_PROFILE_ALLOW_CLOSING;
	}
}

static int io_principal_prepare(
	uint owner, struct resource_account_handle principal)
{
	struct resource_rate_profile profiles[RESOURCE_RATE_LANE_CAP];

	io_rate_profiles(owner, profiles);
	if (resource_rate_account_configure(
		    principal, profiles, RESOURCE_RATE_LANE_CAP) < 0 ||
	    resource_account_member_acquire(principal) < 0)
		return -1;
	return 0;
}

static uint io_ready_index(const struct io_owner_state *state, uint io_class)
{
	return (uint)(state - io_policy.owners) * IO_POLICY_CLASS_COUNT +
	       io_class;
}

static uint64 io_ready_bit(const struct io_owner_state *state, uint io_class)
{
	return 1ULL << io_ready_index(state, io_class);
}

static void io_ready_set(const struct io_owner_state *state, uint io_class)
{
	io_policy.ready_bitmap |= io_ready_bit(state, io_class);
}

static void io_ready_clear(const struct io_owner_state *state, uint io_class)
{
	io_policy.ready_bitmap &= ~io_ready_bit(state, io_class);
}

static void io_ready_clear_owner(const struct io_owner_state *state)
{
	uint shift = io_ready_index(state, 0);
	uint64 lanes = (1ULL << IO_POLICY_CLASS_COUNT) - 1;

	io_policy.ready_bitmap &= ~(lanes << shift);
}

static void io_ready_refresh(const struct io_owner_state *state,
			     uint io_class)
{
	const struct io_bucket *bucket = &state->buckets[io_class];

	if (state->used && !state->retiring &&
	    (!state->quiesced || io_class == IO_POLICY_CLASS_BACKGROUND) &&
	    bucket->grantee == 0 && bucket->admission_waiters != 0 &&
	    bucket->admission_queue.head != 0)
		io_ready_set(state, io_class);
	else
		io_ready_clear(state, io_class);
}

static uint io_ready_first(uint64 pending)
{
	uint index = 0;

	if ((pending & 0xffffffffULL) == 0) {
		index += 32;
		pending >>= 32;
	}
	if ((pending & 0xffffULL) == 0) {
		index += 16;
		pending >>= 16;
	}
	if ((pending & 0xffULL) == 0) {
		index += 8;
		pending >>= 8;
	}
	if ((pending & 0xfULL) == 0) {
		index += 4;
		pending >>= 4;
	}
	if ((pending & 0x3ULL) == 0) {
		index += 2;
		pending >>= 2;
	}
	return index + ((pending & 1) == 0);
}

static uint io_ready_next(uint64 pending, uint cursor)
{
	uint64 tail = pending & (~0ULL << cursor);

	return io_ready_first(tail != 0 ? tail : pending);
}

static void io_owner_init(struct io_owner_state *state, uint owner,
			  struct resource_account_handle principal,
			  int principal_member)
{
	io_ready_clear_owner(state);
	memset(state, 0, sizeof(*state));
	state->owner = owner;
	state->principal = principal;
	state->principal_member = principal_member;
	state->cache_live = 1;
	for (uint i = 0; i < IO_POLICY_CLASS_COUNT; i++) {
		wait_queue_init(&state->buckets[i].admission_queue,
				WAIT_REASON_IO_BUDGET);
		wait_queue_init(&state->buckets[i].debt_queue,
				WAIT_REASON_IO_BUDGET);
	}
	// Publish only after every queue and bucket is initialized.
	state->used = 1;
}

static int io_owner_has_waiters(const struct io_owner_state *state)
{
	for (uint i = 0; i < IO_POLICY_CLASS_COUNT; i++)
		if (state->buckets[i].admission_waiters != 0 ||
		    state->buckets[i].debt_waiters != 0 ||
		    state->buckets[i].grantee != 0)
			return 1;
	return 0;
}

static void io_active_request_acquire(struct io_owner_state *state)
{
	if (state->active_requests == (uint)-1)
		panic("I/O active request overflow");
	state->active_requests++;
}

static void io_active_request_release(struct io_owner_state *state)
{
	if (state->active_requests == 0)
		panic("I/O active request underflow");
	state->active_requests--;
}

static void io_owner_reap_retired(void)
{
	int enabled = intr_save();

	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (state->used && state->retiring &&
		    state->active_requests == 0 &&
		    !io_owner_has_waiters(state) &&
		    resource_rate_account_idle(state->principal) &&
		    resource_account_usage(
			    state->principal,
			    RESOURCE_BUFFER_CACHE) == 0) {
			if (state->idle.head != 0 || state->idle.tail != 0 ||
			    state->idle.count != 0 ||
			    state->deferred_references != 0)
				panic("retired owner idle buffers");
			if (!state->principal_member ||
			    resource_account_member_release(
				    state->principal, 0) < 0)
				panic("I/O principal member release");
			io_ready_clear_owner(state);
			memset(state, 0, sizeof(*state));
		}
	}
	intr_restore(enabled);
}

static struct io_owner_state *bio_cache_owner_state(uint owner)
{
	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (state->used && state->owner == owner)
			return state;
	}
	return 0;
}

// A quiesced workflow normally gives up its cache partition immediately, but
// admitted background and deferred cleanup references keep a small partition
// for bounded forward progress. Victim selection and lifecycle release must
// share this predicate so the advertised cleanup floor is actually retained.
static int bio_cache_state_retained(const struct io_owner_state *state)
{
	if (state == 0)
		return 0;
	if (state->owner == FS_OWNER_SYSTEM ||
	    state->owner == FS_OWNER_PUBLIC)
		return 1;
	if (!FS_OWNER_IS_SCOPE(state->owner))
		return 0;
	if (state->cache_live && !state->retiring &&
	    resource_account_state_get(state->principal) ==
		    RESOURCE_ACCOUNT_ACTIVE)
		return 1;
	if (state->deferred_references != 0)
		return 1;
	return io_policy.background.active &&
	       io_policy.background.owner == state->owner;
}

static int bio_cache_owner_retained(uint owner)
{
	return bio_cache_state_retained(bio_cache_owner_state(owner));
}

static void bio_idle_push(struct bio_idle_queue *queue, struct buf *b,
			  int promote)
{
	if (queue == 0 || b == 0 || b->prev != 0 || b->next != 0 ||
	    b->idle_class == BIO_IDLE_NONE || b->refcnt != 0 ||
	    b->hold_depth != 0)
		panic("buffer idle enqueue");
	if (queue->count >= NBUF)
		panic("buffer idle queue overflow");
	if (promote) {
		b->prev = 0;
		b->next = queue->head;
		if (queue->head != 0)
			queue->head->prev = b;
		else
			queue->tail = b;
		queue->head = b;
	} else {
		b->next = 0;
		b->prev = queue->tail;
		if (queue->tail != 0)
			queue->tail->next = b;
		else
			queue->head = b;
		queue->tail = b;
	}
	queue->count++;
}

static struct bio_idle_queue *bio_cache_idle_queue(struct buf *b)
{
	struct io_owner_state *state;

	switch (b->idle_class) {
	case BIO_IDLE_FREE:
		return &bcache.free_idle;
	case BIO_IDLE_RESERVED:
		return &bcache.reserved_idle;
	case BIO_IDLE_OWNER:
		state = bio_cache_owner_state(b->cache_owner);
		if (state == 0)
			panic("buffer idle owner vanished");
		return &state->idle;
	default:
		panic("buffer idle class");
	}
}

static void bio_cache_idle_remove(struct buf *b)
{
	struct bio_idle_queue *queue;

	if (b == 0 || b->idle_class == BIO_IDLE_NONE)
		panic("buffer idle remove");
	queue = bio_cache_idle_queue(b);
	if (queue->count == 0 ||
	    (b->prev == 0 && queue->head != b) ||
	    (b->next == 0 && queue->tail != b) ||
	    (b->prev != 0 && b->prev->next != b) ||
	    (b->next != 0 && b->next->prev != b))
		panic("buffer idle link");
	if (b->prev != 0)
		b->prev->next = b->next;
	else
		queue->head = b->next;
	if (b->next != 0)
		b->next->prev = b->prev;
	else
		queue->tail = b->prev;
	queue->count--;
	b->prev = 0;
	b->next = 0;
	b->idle_class = BIO_IDLE_NONE;
}

static void bio_cache_idle_enqueue(struct buf *b, int promote)
{
	struct io_owner_state *state;

	if (b == 0 || b->idle_class != BIO_IDLE_NONE || b->prev != 0 ||
	    b->next != 0 || b->refcnt != 0 || b->hold_depth != 0 ||
	    b->transient)
		panic("buffer idle state");
	if (b->background_reserved) {
		if (b->valid || b->dev != 0 || b->hash_next != 0 ||
		    !bio_cache_owner_retained(b->cache_owner))
			panic("reserved buffer idle state");
		b->idle_class = BIO_IDLE_RESERVED;
		bio_idle_push(&bcache.reserved_idle, b, promote);
		return;
	}
	if (!b->valid) {
		if (b->dev != 0 || b->blockno != 0 ||
		    b->cache_owner != FS_OWNER_NONE || b->cache_charged ||
		    b->hash_next != 0)
			panic("free buffer idle state");
		b->idle_class = BIO_IDLE_FREE;
		bio_idle_push(&bcache.free_idle, b, promote);
		return;
	}
	state = bio_cache_owner_state(b->cache_owner);
	if (state == 0 || !b->cache_charged || b->dev == 0)
		panic("owned buffer idle state");
	b->idle_class = BIO_IDLE_OWNER;
	bio_idle_push(&state->idle, b, promote);
}

static uint bio_cache_assert_queue(struct bio_idle_queue *queue,
				   uint idle_class, uint owner)
{
	struct buf *prior = 0;
	uint count = 0;

	for (struct buf *b = queue->head; b != 0; b = b->next) {
		uint64 address = (uint64)b;
		uint64 base = (uint64)&bcache.buf[0];
		uint index;

		if (address < base || address >= (uint64)&bcache.buf[NBUF] ||
		    (address - base) % sizeof(*b) != 0)
			panic("buffer idle pointer");
		index = (uint)((address - base) / sizeof(*b));
		if (bio_cache_integrity_seen[index / 64U] &
		    (1ULL << (index % 64U)))
			panic("buffer idle duplicate");
		bio_cache_integrity_seen[index / 64U] |=
			1ULL << (index % 64U);
		if (count++ >= NBUF || b->prev != prior ||
		    b->idle_class != idle_class || b->refcnt != 0 ||
		    b->hold_depth != 0 ||
		    (idle_class == BIO_IDLE_OWNER &&
		     (b->cache_owner != owner || !b->valid ||
		      !b->cache_charged || b->transient ||
		      b->background_reserved || b->dev == 0)) ||
		    (idle_class == BIO_IDLE_FREE &&
		     (b->valid || b->dev != 0 || b->blockno != 0 ||
		      b->cache_owner != FS_OWNER_NONE || b->cache_charged ||
		      b->transient || b->background_reserved)) ||
		    (idle_class == BIO_IDLE_RESERVED &&
		     (!io_policy.background.active ||
		      b->cache_owner != io_policy.background.owner ||
		      b->valid || b->dev != 0 || b->transient ||
		      !b->background_reserved)))
			panic("buffer idle queue corrupt");
		prior = b;
	}
	if (prior != queue->tail || count != queue->count ||
	    ((queue->head == 0) != (queue->tail == 0)))
		panic("buffer idle queue count");
	return count;
}

static void bio_cache_assert_integrity(void)
{
	uint linked = 0;
	uint marked = 0;

	memset(bio_cache_integrity_seen, 0,
	       sizeof(bio_cache_integrity_seen));
	linked += bio_cache_assert_queue(
		&bcache.free_idle, BIO_IDLE_FREE, FS_OWNER_NONE);
	linked += bio_cache_assert_queue(
		&bcache.reserved_idle, BIO_IDLE_RESERVED, FS_OWNER_NONE);
	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (!state->used) {
			if (state->idle.head != 0 || state->idle.tail != 0 ||
			    state->idle.count != 0)
				panic("unused owner idle queue");
			continue;
		}
		linked += bio_cache_assert_queue(
			&state->idle, BIO_IDLE_OWNER, state->owner);
	}
	for (uint i = 0; i < NBUF; i++) {
		struct buf *b = &bcache.buf[i];

		if (b->idle_class != BIO_IDLE_NONE)
			marked++;
		if ((b->idle_class != BIO_IDLE_NONE) !=
		    ((bio_cache_integrity_seen[i / 64U] &
		      (1ULL << (i % 64U))) != 0))
			panic("buffer idle reachability");
		if ((b->refcnt == 0) !=
		    (b->idle_class != BIO_IDLE_NONE) ||
		    (b->idle_class == BIO_IDLE_NONE &&
		     (b->prev != 0 || b->next != 0)) ||
		    b->hold_depth > b->refcnt ||
		    ((b->hold_depth == 0) != (b->holder == 0)))
			panic("buffer idle membership");
	}
	if (linked != marked || marked > NBUF)
		panic("buffer idle duplicate");
}

static void bio_cache_release_closed_owner(uint owner)
{
	struct io_owner_state *state;

	if (bio_cache_owner_retained(owner))
		return;
	state = bio_cache_owner_state(owner);
	if (state == 0)
		return;
	while (state->idle.tail != 0)
		bio_cache_invalidate(state->idle.tail);
}

static struct io_owner_state *io_state_find(uint owner, int create)
{
	struct io_owner_state *free_state = 0;

	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (state->used && state->owner == owner)
			return state;
		if (!state->used && free_state == 0)
			free_state = state;
	}
	if (!create || free_state == 0)
		return 0;
	{
		struct resource_account_handle principal;

		if (resource_account_find(
			    RESOURCE_ACCOUNT_STORAGE, owner,
			    &principal) < 0 ||
		    !resource_account_active(principal) ||
		    io_principal_prepare(owner, principal) < 0)
			return 0;
		io_owner_init(free_state, owner, principal, 1);
	}
	return free_state;
}

uint bio_process_owner(const struct proc *p)
{
	struct workflow_lifecycle_key lifecycle;
	uint scope_id;

	/*
	 * I/O accounting follows the immutable workflow membership, not the
	 * mutable VFS credential.  Exec may intentionally drop all filesystem
	 * authority while the process remains charged to, and revocable with,
	 * its original workflow resource domain.
	 */
	lifecycle = vfs_proc_lifecycle(p);
	if (workflow_lifecycle_key_valid(lifecycle) &&
	    workflow_lifecycle_scope(lifecycle, &scope_id) == 0 &&
	    scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
	    scope_id < FS_OWNER_SCOPE_FLAG)
		return FS_OWNER_SCOPE(scope_id);
	return FS_OWNER_PUBLIC;
}

static uint io_class_from_proc(const struct proc *p, uint owner)
{
	if (owner == FS_OWNER_SYSTEM)
		return IO_POLICY_CLASS_SYSTEM;
	if (FS_OWNER_IS_SCOPE(owner) && p != 0 && p->is_agent &&
	    (p->agent_role == AGENT_ROLE_RECOVERY ||
	     p->agent_role == AGENT_ROLE_ORCHESTRATOR))
		return IO_POLICY_CLASS_CONTROL;
	return IO_POLICY_CLASS_NORMAL;
}

static void io_schedule_grants(void);

static struct resource_rate_lease_handle
io_rate_lease(uint slot, uint generation)
{
	struct resource_rate_lease_handle lease = {
		.slot = slot & IO_RESERVATION_SLOT_MASK,
		.generation = generation,
	};

	return lease;
}

static int io_device_protected(uint owner, uint io_class)
{
	return owner == FS_OWNER_SYSTEM ||
	       io_class == IO_POLICY_CLASS_CONTROL ||
	       io_class == IO_POLICY_CLASS_SYSTEM;
}

static uint io_rate_bundle_amount(
	const struct io_owner_state *state, uint io_class, int shared,
	uint64 amount,
	struct resource_rate_endpoint
		endpoints[RESOURCE_RATE_BUNDLE_CAP])
{
	memset(endpoints, 0,
	       sizeof(*endpoints) * RESOURCE_RATE_BUNDLE_CAP);
	if (shared) {
		endpoints[0].scope = RESOURCE_RATE_GLOBAL;
		endpoints[0].index = IO_RATE_GLOBAL_SHARED;
	} else {
		endpoints[0].scope = RESOURCE_RATE_ACCOUNT;
		endpoints[0].account = state->principal;
		endpoints[0].index = io_class;
	}
	endpoints[0].amount = amount;
	endpoints[1].scope = RESOURCE_RATE_GLOBAL;
	endpoints[1].index = IO_RATE_GLOBAL_DEVICE;
	endpoints[1].amount = amount;
	/*
	 * A reserved lane is a guarantee, so a previous opportunistic borrower
	 * cannot block it by draining the device root. Shared traffic is always
	 * bounded by real shared and device tokens, including protected callers.
	 */
	if (!shared)
		endpoints[1].flags =
			RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;
	return 2;
}

static uint io_rate_bundle(
	const struct io_owner_state *state, uint io_class, int shared,
	struct resource_rate_endpoint
		endpoints[RESOURCE_RATE_BUNDLE_CAP])
{
	return io_rate_bundle_amount(state, io_class, shared, 1, endpoints);
}

static int io_rate_snapshot(
	const struct io_owner_state *state, uint io_class,
	struct resource_rate_snapshot *snapshot)
{
	return resource_rate_account_snapshot(
		state->principal, io_class, snapshot);
}

static int io_rate_global_snapshot(
	uint index, struct resource_rate_snapshot *snapshot)
{
	return resource_rate_global_snapshot(index, snapshot);
}

static void io_rate_lease_store(
	struct resource_rate_lease_handle lease, int shared,
	uint *slot, uint *generation)
{
	*slot = lease.slot | (shared ? IO_RESERVATION_SHARED : 0);
	*generation = lease.generation;
}

static int io_rate_lease_shared(uint slot)
{
	return (slot & IO_RESERVATION_SHARED) != 0;
}

static void io_rate_lease_commit(uint slot, uint generation)
{
	if (resource_rate_lease_commit(
		    io_rate_lease(slot, generation)) < 0)
		panic("I/O rate lease commit");
}

static void io_rate_lease_refund(uint slot, uint generation)
{
	struct resource_rate_lease_handle lease =
		io_rate_lease(slot, generation);

	if (resource_rate_lease_valid(lease))
		resource_rate_lease_cancel(lease);
	io_schedule_grants();
}

static int io_can_use_shared_capacity(
	const struct io_owner_state *state, uint io_class)
{
	return io_class != IO_POLICY_CLASS_BACKGROUND &&
	       !state->retiring && !state->quiesced;
}

static void io_rate_charge_transfers(
	struct io_owner_state *state, uint io_class, uint64 amount)
{
	struct resource_rate_endpoint
		endpoints[RESOURCE_RATE_BUNDLE_CAP];
	struct resource_rate_snapshot lane;
	uint64 reserved = 0;
	uint64 shared = 0;

	if (amount == 0)
		return;
	if (io_rate_snapshot(state, io_class, &lane) < 0)
		panic("I/O rate snapshot");
	if (lane.debt == 0)
		reserved = MIN(amount, lane.tokens);
	if (reserved != 0) {
		struct resource_rate_snapshot device;

		/* The common case charges an entire completed device batch once. */
		if (io_rate_global_snapshot(
			    IO_RATE_GLOBAL_DEVICE, &device) == 0 &&
		    device.debt == 0 && device.tokens >= reserved) {
			(void)io_rate_bundle_amount(
				state, io_class, 0, reserved, endpoints);
			if (resource_rate_charge_many(endpoints, 2) < 0)
				panic("I/O reserved batch charge");
		} else {
			/* Preserve token-before-debt semantics at a device boundary. */
			for (uint64 i = 0; i < reserved; i++) {
				(void)io_rate_bundle(
					state, io_class, 0, endpoints);
				if (resource_rate_charge_many(endpoints, 2) < 0)
					panic("I/O reserved charge");
			}
		}
		state->reserved_grants += reserved;
		amount -= reserved;
	}
	if (amount != 0 && lane.debt == 0 && lane.pending_debt == 0 &&
	    io_can_use_shared_capacity(state, io_class)) {
		struct resource_rate_snapshot capacity;

		if (io_rate_global_snapshot(
			    IO_RATE_GLOBAL_SHARED, &capacity) == 0 &&
		    capacity.debt == 0) {
			shared = MIN(amount, capacity.tokens);
			if (shared != 0) {
				if (io_rate_global_snapshot(
					    IO_RATE_GLOBAL_DEVICE,
					    &capacity) < 0 ||
				    capacity.debt != 0)
					shared = 0;
				else if (shared > capacity.tokens)
					shared = capacity.tokens;
			}
		}
		if (shared != 0) {
			(void)io_rate_bundle_amount(
				state, io_class, 1, shared, endpoints);
			if (resource_rate_charge_many(endpoints, 2) < 0)
				panic("I/O shared batch charge");
			state->shared_grants += shared;
			amount -= shared;
		}
	}
	/* Debt is an already-issued slow path, so exact per-credit splitting is
	 * preferable to making the resource controller's atomic API ambiguous. */
	for (uint64 i = 0; i < amount; i++) {
		(void)io_rate_bundle(state, io_class, 0, endpoints);
		endpoints[0].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;
		endpoints[1].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;
		if (resource_rate_charge_many(endpoints, 2) < 0)
			panic("I/O rate charge");
	}
	state->throttles += amount;
}

static void io_rate_charge_transfer(
	struct io_owner_state *state, uint io_class)
{
	io_rate_charge_transfers(state, io_class, 1);
}

static int io_reserve_direct(struct io_owner_state *state, uint io_class,
			     uint *source, uint *device_source)
{
	struct io_bucket *bucket = &state->buckets[io_class];
	struct resource_rate_endpoint
		endpoints[RESOURCE_RATE_BUNDLE_CAP];
	struct resource_rate_lease_handle lease =
		resource_rate_lease_none();
	struct resource_rate_snapshot lane;

	if (bucket->admission_waiters != 0 || state->retiring ||
	    (state->quiesced && io_class != IO_POLICY_CLASS_BACKGROUND))
		return 0;
	(void)io_rate_bundle(state, io_class, 0, endpoints);
	if (resource_rate_reserve_many(endpoints, 2, &lease) == 0) {
		state->reserved_grants++;
		io_rate_lease_store(lease, 0, source, device_source);
		return 1;
	}
	if (io_rate_snapshot(state, io_class, &lane) == 0 &&
	    lane.debt == 0 && lane.pending_debt == 0 &&
	    io_class != IO_POLICY_CLASS_BACKGROUND &&
	    io_can_use_shared_capacity(state, io_class)) {
		(void)io_rate_bundle(state, io_class, 1, endpoints);
		if (resource_rate_reserve_many(
			    endpoints, 2, &lease) == 0) {
			io_rate_lease_store(
				lease, 1, source, device_source);
			return 1;
		}
	}
	return 0;
}

static int io_grant_waiter(struct io_owner_state *state, uint io_class,
			   int shared)
{
	struct io_bucket *bucket = &state->buckets[io_class];
	struct thread *grantee;
	struct resource_rate_endpoint
		endpoints[RESOURCE_RATE_BUNDLE_CAP];
	struct resource_rate_lease_handle lease =
		resource_rate_lease_none();
	struct resource_rate_snapshot lane;

	if (!state->used || state->retiring ||
	    (state->quiesced && io_class != IO_POLICY_CLASS_BACKGROUND)) {
		io_ready_clear(state, io_class);
		return 0;
	}
	if (bucket->grantee != 0 || bucket->admission_queue.head == 0) {
		io_ready_clear(state, io_class);
		return 0;
	}
	if (shared) {
		if (io_class == IO_POLICY_CLASS_BACKGROUND ||
		    io_rate_snapshot(state, io_class, &lane) < 0 ||
		    lane.debt != 0 || lane.pending_debt != 0)
			return 0;
	}
	(void)io_rate_bundle(state, io_class, shared, endpoints);
	if (resource_rate_reserve_many(endpoints, 2, &lease) < 0)
		return 0;
	/*
	 * Bind the lease to the thread actually removed from the wait queue.
	 * A canceled or otherwise stale queue head may be skipped by the generic
	 * wake primitive; binding to a raw head would then strand the real waiter
	 * behind a grantee that can never consume the reservation.
	 */
	grantee = wait_queue_wake_one_thread(&bucket->admission_queue);
	if (grantee == 0) {
		resource_rate_lease_cancel(lease);
		io_ready_refresh(state, io_class);
		return 0;
	}
	if (!shared)
		state->reserved_grants++;
	bucket->grantee = grantee;
	io_rate_lease_store(
		lease, shared, &bucket->grant_source,
		&bucket->grant_device_source);
	io_ready_clear(state, io_class);
	return 1;
}

static void io_schedule_grants(void)
{
	uint64 pending;
	int enabled = intr_save();

	/* Dispatch only lanes with published backlog. */
	pending = io_policy.ready_bitmap;
	while (pending != 0) {
		uint index = io_ready_first(pending);
		uint owner_slot = index / IO_POLICY_CLASS_COUNT;
		uint io_class = index % IO_POLICY_CLASS_COUNT;

		pending &= pending - 1;
		(void)io_grant_waiter(
			&io_policy.owners[owner_slot], io_class, 0);
	}
	{
		struct resource_rate_snapshot shared;

		if (io_rate_global_snapshot(
			    IO_RATE_GLOBAL_SHARED, &shared) < 0 ||
		    shared.tokens == 0 || shared.debt != 0)
			goto out;
		pending = io_policy.ready_bitmap;
		while (pending != 0) {
			uint index = io_ready_next(
				pending, io_policy.shared_cursor);
			uint owner_slot = index / IO_POLICY_CLASS_COUNT;
			uint io_class = index % IO_POLICY_CLASS_COUNT;
			struct io_owner_state *state =
				&io_policy.owners[owner_slot];

			pending &= ~((uint64)1 << index);
			/* Background guarantees remain ready for the reserved pass. */
			if (io_class == IO_POLICY_CLASS_BACKGROUND)
				continue;
			if (io_grant_waiter(state, io_class, 1)) {
				io_policy.shared_cursor =
					(index + 1) % IO_ADMISSION_READY_SLOTS;
				if (--shared.tokens == 0)
					break;
			}
		}
	}
out:
	intr_restore(enabled);
}

static int io_wait_until_admitted(struct io_owner_state *state,
				  uint io_class, uint *source,
				  uint *device_source, int cleanup)
{
	struct io_bucket *bucket = &state->buckets[io_class];
	struct thread *thread = curr_thread();
	int enabled = intr_save();

	if (state->retiring ||
	    (state->quiesced && io_class != IO_POLICY_CLASS_BACKGROUND) ||
	    thread == 0 ||
	    io_bucket_burst(state->owner, io_class) == 0) {
		intr_restore(enabled);
		return -1;
	}
	if (io_reserve_direct(state, io_class, source, device_source)) {
		state->admissions++;
		intr_restore(enabled);
		return 0;
	}
	state->throttles++;
	state->waits++;
	bucket->admission_waiters++;
	for (;;) {
		/* Publish demand before the IRQ-atomic queue-and-sleep transition. */
		io_ready_set(state, io_class);
		int sleep_status = cleanup ?
			wait_queue_sleep_irq_uninterruptible(
				&bucket->admission_queue) :
			wait_queue_sleep_irq(&bucket->admission_queue);

		if (bucket->grantee == thread) {
			uint granted_source = bucket->grant_source;
			uint granted_device = bucket->grant_device_source;

			bucket->grantee = 0;
			bucket->grant_source = IO_RESERVATION_NONE;
			bucket->grant_device_source = IO_RESERVATION_NONE;
			if (bucket->admission_waiters == 0)
				panic("I/O admission waiter underflow");
			bucket->admission_waiters--;
			io_ready_refresh(state, io_class);
			if (sleep_status != WAIT_QUEUE_OK || state->retiring ||
			    (state->quiesced &&
			     io_class != IO_POLICY_CLASS_BACKGROUND)) {
				io_rate_lease_refund(
					granted_source,
					granted_device);
				intr_restore(enabled);
				return -1;
			}
			*source = granted_source;
			*device_source = granted_device;
			state->admissions++;
			io_schedule_grants();
			intr_restore(enabled);
			return 0;
		}
		if (sleep_status != WAIT_QUEUE_OK || state->retiring ||
		    (state->quiesced &&
		     io_class != IO_POLICY_CLASS_BACKGROUND)) {
			if (bucket->admission_waiters == 0)
				panic("I/O admission waiter underflow");
			bucket->admission_waiters--;
			io_ready_refresh(state, io_class);
			io_schedule_grants();
			intr_restore(enabled);
			return -1;
		}
	}
}

static int io_wait_for_debt_mode(struct io_owner_state *state, uint io_class,
				 int cleanup, int retained_cleanup,
				 int polling_executor)
{
	struct io_bucket *bucket = &state->buckets[io_class];
	int closing_background = cleanup &&
		io_class == IO_POLICY_CLASS_BACKGROUND &&
		bio_background_current() && state->active_requests != 0 &&
		state->owner == io_policy.background.owner;
	int closing_deferred = retained_cleanup ||
		(cleanup && bio_deferred_sponsor_current() &&
		state->active_requests != 0 &&
		state->owner == io_policy.deferred.owner &&
		io_class == io_policy.deferred.io_class);
	int enabled = intr_save();

	for (;;) {
		struct resource_rate_snapshot snapshot;

		if (io_rate_snapshot(
			    state, io_class, &snapshot) < 0) {
			intr_restore(enabled);
			return -1;
		}
		if (snapshot.debt == 0)
			break;
		/*
		 * An admitted cleanup request pins both the owner state and its rate
		 * account.  Let that request settle debt after lifecycle quiesce (and
		 * even a concurrent retire) so a forward-only filesystem transaction
		 * cannot be abandoned halfway through durable publication.  No new
		 * background request is admitted once the owner is retiring.
		 */
		if ((state->retiring || state->quiesced) &&
		    !closing_background && !closing_deferred) {
			intr_restore(enabled);
			return -1;
		}
		if (polling_executor || bio_deferred_polling_current()) {
			intr_restore(enabled);
			intr_on();
			asm volatile("wfi");
			intr_off();
			enabled = intr_save();
			continue;
		}
		state->waits++;
		bucket->debt_waiters++;
		int sleep_status = cleanup ?
			wait_queue_sleep_irq_uninterruptible(
				&bucket->debt_queue) :
			wait_queue_sleep_irq(&bucket->debt_queue);

		if (sleep_status != WAIT_QUEUE_OK) {
			bucket->debt_waiters--;
			intr_restore(enabled);
			return -1;
		}
		if (bucket->debt_waiters == 0)
			panic("I/O debt waiter underflow");
		bucket->debt_waiters--;
	}
	intr_restore(enabled);
	return 0;
}

static int io_wait_for_debt(struct io_owner_state *state, uint io_class,
			    int cleanup)
{
	return io_wait_for_debt_mode(state, io_class, cleanup, 0, 0);
}

static int io_wait_for_device_debt_mode(uint owner, uint io_class,
					int cleanup, int polling_executor)
{
	int enabled;

	if (io_device_protected(owner, io_class))
		return 0;
	enabled = intr_save();
	for (;;) {
		struct resource_rate_snapshot snapshot;

		if (io_rate_global_snapshot(
			    IO_RATE_GLOBAL_DEVICE, &snapshot) < 0) {
			intr_restore(enabled);
			return -1;
		}
		if (snapshot.debt == 0)
			break;
		if (polling_executor || bio_deferred_polling_current()) {
			intr_restore(enabled);
			intr_on();
			asm volatile("wfi");
			intr_off();
			enabled = intr_save();
			continue;
		}
		io_policy.device.debt_waiters++;
		int sleep_status = cleanup ?
			wait_queue_sleep_irq_uninterruptible(
				&io_policy.device.debt_queue) :
			wait_queue_sleep_irq(&io_policy.device.debt_queue);

		if (sleep_status != WAIT_QUEUE_OK) {
			io_policy.device.debt_waiters--;
			intr_restore(enabled);
			return -1;
		}
		if (io_policy.device.debt_waiters == 0)
			panic("I/O device debt waiter underflow");
		io_policy.device.debt_waiters--;
	}
	intr_restore(enabled);
	return 0;
}

static int io_wait_for_device_debt(uint owner, uint io_class, int cleanup)
{
	return io_wait_for_device_debt_mode(owner, io_class, cleanup, 0);
}

void bio_policy_start(void)
{
	int enabled = intr_save();
	struct resource_rate_snapshot snapshot;

	if (io_policy.background.active)
		panic("I/O policy started during boot background I/O");
	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (!state->used)
			continue;
		if (state->retiring) {
			for (uint c = 0; c < IO_POLICY_CLASS_COUNT; c++)
				wait_queue_wake_all(
					&state->buckets[c].admission_queue);
			continue;
		}
		for (uint c = 0; c < IO_POLICY_CLASS_COUNT; c++) {
			if (state->buckets[c].admission_waiters != 0 ||
			    state->buckets[c].debt_waiters != 0)
				panic("I/O policy started with live leases");
			if (io_bucket_burst(state->owner, c) == 0)
				continue;
			if (io_rate_snapshot(state, c, &snapshot) < 0 ||
			    snapshot.tokens != snapshot.burst ||
			    snapshot.leased != 0 ||
			    snapshot.debt != 0 ||
			    snapshot.pending_debt != 0)
				panic("I/O owner controller start");
		}
	}
	if (io_policy.device.debt_waiters != 0)
		panic("I/O device policy started with live leases");
	if (io_rate_global_snapshot(
		    IO_RATE_GLOBAL_SHARED, &snapshot) < 0 ||
	    snapshot.tokens != snapshot.burst ||
	    snapshot.leased != 0 || snapshot.debt != 0 ||
	    snapshot.pending_debt != 0)
		panic("I/O shared controller start");
	if (io_rate_global_snapshot(
		    IO_RATE_GLOBAL_DEVICE, &snapshot) < 0 ||
	    snapshot.tokens != snapshot.burst ||
	    snapshot.leased != 0 || snapshot.debt != 0 ||
	    snapshot.pending_debt != 0)
		panic("I/O device controller start");
	io_policy.runtime_ready = 1;
	intr_restore(enabled);
}

void bio_policy_tick(void)
{
	int enabled = intr_save();

	if (!io_policy.runtime_ready) {
		intr_restore(enabled);
		return;
	}
	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (!state->used)
			continue;
		if (state->retiring || state->quiesced) {
			for (uint c = 0; c < IO_POLICY_CLASS_COUNT; c++) {
				if (state->quiesced &&
				    c == IO_POLICY_CLASS_BACKGROUND)
					continue;
				wait_queue_wake_all(
					&state->buckets[c].admission_queue);
			}
		}
		for (uint c = 0; c < IO_POLICY_CLASS_COUNT; c++) {
			struct io_bucket *bucket = &state->buckets[c];
			struct resource_rate_snapshot before;
			struct resource_rate_snapshot after;
			uint64 applied;

			if (io_bucket_burst(state->owner, c) == 0)
				continue;
			if (io_rate_snapshot(state, c, &before) < 0 ||
			    resource_rate_account_refill(
				    state->principal, c,
				    &applied) < 0 ||
			    io_rate_snapshot(state, c, &after) < 0)
				panic("I/O owner controller refill");
			state->refills += applied;
			if (before.debt != 0 && after.debt == 0)
				wait_queue_wake_all(&bucket->debt_queue);
		}
	}
	{
		struct resource_rate_snapshot before;
		struct resource_rate_snapshot after;
		uint64 applied;

		if (resource_rate_global_refill(
			    IO_RATE_GLOBAL_SHARED, &applied) < 0 ||
		    io_rate_global_snapshot(
			    IO_RATE_GLOBAL_DEVICE, &before) < 0 ||
		    resource_rate_global_refill(
			    IO_RATE_GLOBAL_DEVICE, &applied) < 0 ||
		    io_rate_global_snapshot(
			    IO_RATE_GLOBAL_DEVICE, &after) < 0)
			panic("I/O global controller refill");
		if (before.debt != 0 && after.debt == 0)
			wait_queue_wake_all(&io_policy.device.debt_queue);
	}
	io_schedule_grants();
	io_owner_reap_retired();
	intr_restore(enabled);
}

static int bio_thread_request_flags_valid(const struct thread *thread)
{
	uint flags;

	if (thread == 0)
		return 0;
	flags = thread->io_request_flags;
	if ((flags & ~BIO_REQUEST_KNOWN_FLAGS) != 0)
		return 0;
	if (thread->io_request_depth == 0)
		return flags == 0 && thread->io_request_id == 0;
	if (thread->io_request_id == 0)
		return 0;
	if ((flags & BIO_REQUEST_TRANSFERRED) != 0 &&
	    (flags & BIO_REQUEST_ACTIVE) == 0)
		return 0;
	return (flags & (BIO_REQUEST_ACTIVE | BIO_REQUEST_LAZY)) != 0;
}

static int bio_thread_request_active(const struct thread *thread)
{
	return thread != 0 && thread->io_request_depth != 0 &&
	       (thread->io_request_flags & BIO_REQUEST_ACTIVE) != 0;
}

static void bio_thread_request_clear(struct thread *thread)
{
	thread->io_request_flags = 0;
	thread->io_request_id = 0;
	thread->io_request_depth = 0;
	thread->io_request_owner = FS_OWNER_NONE;
	thread->io_request_class = IO_POLICY_CLASS_NORMAL;
	thread->io_request_reservation = IO_RESERVATION_NONE;
	thread->io_request_device_reservation = IO_RESERVATION_NONE;
	thread->io_request_transfers = 0;
}

static uint64 bio_request_identity_allocate(void)
{
	if (bio_request_next_id == (uint64)-1)
		panic("I/O request identity exhausted");
	bio_request_next_id++;
	if (bio_request_next_id == 0)
		panic("I/O request identity wrapped");
	return bio_request_next_id;
}

static int bio_request_begin_current_mode(int cleanup)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state = 0;
	uint owner;
	uint io_class;
	uint source = IO_RESERVATION_NONE;
	uint device_source = IO_RESERVATION_NONE;

	if (thread == 0 || thread->process == 0 || thread->state != RUNNING)
		return -1;
	if (thread->io_request_depth != 0) {
		if (!bio_thread_request_flags_valid(thread))
			panic("nested I/O request identity");
		if (thread->io_request_depth == (uint)-1)
			panic("I/O request depth overflow");
		thread->io_request_depth++;
		return 0;
	}
	if (!bio_thread_request_flags_valid(thread))
		panic("idle I/O request identity");
	owner = bio_process_owner(thread->process);
	io_class = io_class_from_proc(thread->process, owner);
	state = io_state_find(owner, 0);
	if (state == 0)
		return -1;
	if (io_policy.runtime_ready &&
	    io_wait_until_admitted(state, io_class, &source,
				   &device_source, cleanup) < 0)
		return -1;
	int enabled = intr_save();

	if (state->retiring || state->quiesced) {
		if (source != IO_RESERVATION_NONE)
			io_rate_lease_refund(source, device_source);
		intr_restore(enabled);
		return -1;
	}
	io_active_request_acquire(state);
	thread->io_request_flags = BIO_REQUEST_ACTIVE |
		(cleanup ? BIO_REQUEST_CLEANUP : 0);
	thread->io_request_id = bio_request_identity_allocate();
	thread->io_request_owner = owner;
	thread->io_request_class = io_class;
	thread->io_request_reservation = source;
	thread->io_request_device_reservation = device_source;
	thread->io_request_transfers = 0;
	thread->io_request_depth = 1;
	intr_restore(enabled);
	return 0;
}

static int bio_request_begin_current_lazy_mode(int cleanup)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	uint owner;
	uint io_class;
	int enabled;

	if (thread == 0 || thread->process == 0 || thread->state != RUNNING)
		return -1;
	if (thread->io_request_depth != 0) {
		if (!bio_thread_request_flags_valid(thread))
			panic("nested lazy I/O request identity");
		if (thread->io_request_depth == (uint)-1)
			panic("lazy I/O request depth overflow");
		thread->io_request_depth++;
		return 0;
	}
	if (!bio_thread_request_flags_valid(thread))
		panic("idle lazy I/O request identity");
	owner = bio_process_owner(thread->process);
	io_class = io_class_from_proc(thread->process, owner);
	enabled = intr_save();
	state = io_state_find(owner, 0);
	if (state == 0 || state->retiring || state->quiesced ||
	    io_bucket_burst(owner, io_class) == 0) {
		intr_restore(enabled);
		return -1;
	}
	thread->io_request_flags = BIO_REQUEST_LAZY |
		(cleanup ? BIO_REQUEST_CLEANUP : 0);
	thread->io_request_id = bio_request_identity_allocate();
	thread->io_request_owner = owner;
	thread->io_request_class = io_class;
	thread->io_request_reservation = IO_RESERVATION_NONE;
	thread->io_request_device_reservation = IO_RESERVATION_NONE;
	thread->io_request_transfers = 0;
	thread->io_request_depth = 1;
	state->lazy_started++;
	io_policy.lazy_started++;
	intr_restore(enabled);
	return 0;
}

int bio_request_begin_current(void)
{
	return bio_request_begin_current_mode(0);
}

int bio_request_begin_current_cleanup(void)
{
	struct thread *thread = curr_thread();

	/* exit() is terminal, so reuse rather than nest a syscall lease. */
	if (thread != 0 && thread->io_request_depth != 0) {
		if (thread->io_request_id == 0)
			panic("cleanup I/O request identity");
		thread->io_request_flags |= BIO_REQUEST_CLEANUP;
		return 0;
	}
	return bio_request_begin_current_mode(1);
}

int bio_request_begin_current_lazy(void)
{
	return bio_request_begin_current_lazy_mode(0);
}

int bio_request_begin_current_lazy_cleanup(void)
{
	struct thread *thread = curr_thread();

	if (thread != 0 && thread->io_request_depth != 0) {
		if (!bio_thread_request_flags_valid(thread))
			panic("cleanup lazy I/O request identity");
		thread->io_request_flags |= BIO_REQUEST_CLEANUP;
		return 0;
	}
	return bio_request_begin_current_lazy_mode(1);
}

static int bio_request_upgrade_current_mode(int cleanup)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	uint source = IO_RESERVATION_NONE;
	uint device_source = IO_RESERVATION_NONE;
	uint64 request_id;
	int enabled;
	int result;

	if (!io_policy.runtime_ready)
		return 0;
	if (bio_background_current() || bio_deferred_sponsor_current())
		return bio_request_active_current() ? 0 : -1;
	if (thread == 0 || thread->process == 0 || thread->state != RUNNING ||
	    !bio_thread_request_flags_valid(thread) ||
	    (thread->io_request_flags & BIO_REQUEST_LAZY) == 0)
		return -1;
	if ((thread->io_request_flags & BIO_REQUEST_ACTIVE) != 0)
		return 0;
	if (thread->bio_buffer_holds != 0 || thread->bio_fs_atomic_depth != 0)
		return -1;
	request_id = thread->io_request_id;
	cleanup = cleanup ||
		(thread->io_request_flags & BIO_REQUEST_CLEANUP) != 0;
	enabled = intr_save();
	state = io_state_find(thread->io_request_owner, 0);
	if (state == 0 || state->retiring || state->quiesced ||
	    io_bucket_burst(thread->io_request_owner,
			    thread->io_request_class) == 0) {
		intr_restore(enabled);
		return -1;
	}
	/* Pin the owner while admission sleeps; quiesce still wakes the waiter. */
	io_active_request_acquire(state);
	intr_restore(enabled);
	result = io_wait_until_admitted(
		state, thread->io_request_class, &source, &device_source, cleanup);
	enabled = intr_save();
	if (result < 0 || thread->io_request_id != request_id ||
	    (thread->io_request_flags & BIO_REQUEST_LAZY) == 0 ||
	    (thread->io_request_flags & BIO_REQUEST_ACTIVE) != 0 ||
	    state->retiring || state->quiesced) {
		if (result == 0 && source != IO_RESERVATION_NONE)
			io_rate_lease_refund(source, device_source);
		io_active_request_release(state);
		io_owner_reap_retired();
		intr_restore(enabled);
		return -1;
	}
	thread->io_request_reservation = source;
	thread->io_request_device_reservation = device_source;
	thread->io_request_flags |= BIO_REQUEST_ACTIVE;
	state->upgraded++;
	io_policy.upgraded++;
	intr_restore(enabled);
	return 0;
}

int bio_request_upgrade_current(void)
{
	return bio_request_upgrade_current_mode(0);
}

int bio_request_upgrade_current_cleanup(void)
{
	return bio_request_upgrade_current_mode(1);
}

int bio_request_active_current(void)
{
	struct thread *thread = curr_thread();

	if (!io_policy.runtime_ready)
		return 1;
	if (bio_background_current())
		return 1;
	if (bio_deferred_sponsor_current()) {
		if (io_policy.deferred.independent_lease)
			return 1;
		return io_policy.deferred.reuse_request_lease &&
		       bio_thread_request_active(thread);
	}
	return bio_thread_request_active(thread);
}

static struct bio_checkpoint_result
bio_request_checkpoint_mode(int cleanup, int quiescent)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	int enabled;

	if (!io_policy.runtime_ready)
		return bio_checkpoint_make(BIO_CHECKPOINT_READY);
	if (bio_deferred_sponsor_current()) {
		uint owner = io_policy.deferred.owner;
		uint io_class = io_policy.deferred.io_class;

		enabled = intr_save();
		state = io_state_find(owner, 0);
		intr_restore(enabled);
		if (state == 0)
			return bio_checkpoint_make(BIO_CHECKPOINT_INTERRUPTED);
		if (!cleanup || !quiescent)
			return bio_checkpoint_make(BIO_CHECKPOINT_DEFERRED);
		if (io_wait_for_debt(state, io_class, 1) < 0 ||
		    io_wait_for_device_debt(owner, io_class, 1) < 0)
			return bio_checkpoint_make(BIO_CHECKPOINT_INTERRUPTED);
		return bio_checkpoint_make(BIO_CHECKPOINT_READY);
	}
	// Resumable scheduler maintenance returns after its bounded quantum.
	// Forward-only cleanup is the sole background mode allowed to settle debt.
	if (bio_background_current()) {
		uint owner = io_policy.background.owner;

		if (io_policy.background.buffer_holds != 0) {
			if (quiescent)
				panic("background quiescent checkpoint holds buffer");
			return bio_checkpoint_make(BIO_CHECKPOINT_DEFERRED);
		}
		enabled = intr_save();
		state = io_state_find(owner, 0);
		intr_restore(enabled);
		if (state == 0)
			panic("background I/O owner vanished");
		/*
		 * Resumable background scans never sleep.  A cleanup checkpoint is
		 * different: its caller has already published an irreversible intent,
		 * so the admitted executor must finish paying its debt before it can
		 * continue the forward transaction.  active_requests keeps state alive
		 * while the executor sleeps, including across lifecycle quiesce.
		 */
		if (cleanup && quiescent) {
			if (io_wait_for_debt(
				    state, IO_POLICY_CLASS_BACKGROUND, 1) < 0 ||
			    io_wait_for_device_debt(
				    owner, IO_POLICY_CLASS_BACKGROUND, 1) < 0 ||
			    bio_background_wait_for_cache_progress() < 0)
				return bio_checkpoint_make(
					BIO_CHECKPOINT_INTERRUPTED);
			return bio_checkpoint_make(BIO_CHECKPOINT_READY);
		}
		enabled = intr_save();
		struct resource_rate_snapshot lane;
		struct resource_rate_snapshot device;
		int ready = io_rate_snapshot(
				state, IO_POLICY_CLASS_BACKGROUND,
				&lane) == 0 &&
			lane.debt == 0 &&
			(io_device_protected(owner,
					     IO_POLICY_CLASS_BACKGROUND) ||
			 (io_rate_global_snapshot(
				  IO_RATE_GLOBAL_DEVICE,
				  &device) == 0 &&
			  device.debt == 0));

		intr_restore(enabled);
		return bio_checkpoint_make(ready ? BIO_CHECKPOINT_READY :
						 BIO_CHECKPOINT_DEFERRED);
	}
	if (thread == 0 || thread->io_request_depth == 0)
		return bio_checkpoint_make(BIO_CHECKPOINT_READY);
	if (!bio_thread_request_flags_valid(thread))
		panic("checkpoint I/O request identity");
	if (!cleanup && proc_thread_exit_requested())
		return bio_checkpoint_make(BIO_CHECKPOINT_INTERRUPTED);
	enabled = intr_save();
	state = io_state_find(thread->io_request_owner, 0);
	if (state == 0 || state->retiring || state->quiesced) {
		intr_restore(enabled);
		return bio_checkpoint_make(BIO_CHECKPOINT_INTERRUPTED);
	}
	if ((thread->io_request_flags & BIO_REQUEST_ACTIVE) == 0) {
		intr_restore(enabled);
		return bio_checkpoint_make(BIO_CHECKPOINT_READY);
	}
	struct resource_rate_snapshot lane;
	struct resource_rate_snapshot device;
	int ready = io_rate_snapshot(
			    state, thread->io_request_class,
			    &lane) == 0 &&
		lane.debt == 0 &&
		(io_device_protected(thread->io_request_owner,
				     thread->io_request_class) ||
		 (io_rate_global_snapshot(
			  IO_RATE_GLOBAL_DEVICE, &device) == 0 &&
		  device.debt == 0));

	intr_restore(enabled);
	if (ready)
		return bio_checkpoint_make(BIO_CHECKPOINT_READY);
	/*
	 * Filesystem primitives publish several related in-memory and on-disk
	 * fields as one single-CPU transaction. Never sleep halfway through that
	 * transaction; report a short operation and let its outer request boundary
	 * pay the accumulated debt after every buffer has been released.
	 */
	if (thread->bio_buffer_holds != 0) {
		if (quiescent)
			panic("I/O quiescent checkpoint holds buffer");
		return bio_checkpoint_make(BIO_CHECKPOINT_DEFERRED);
	}
	if (!quiescent && thread->bio_fs_atomic_depth != 0)
		return bio_checkpoint_make(BIO_CHECKPOINT_DEFERRED);
	if (io_wait_for_debt(state, thread->io_request_class, cleanup) < 0)
		return bio_checkpoint_make(BIO_CHECKPOINT_INTERRUPTED);
	return bio_checkpoint_make(
		io_wait_for_device_debt(thread->io_request_owner,
					thread->io_request_class, cleanup) < 0 ?
			BIO_CHECKPOINT_INTERRUPTED : BIO_CHECKPOINT_READY);
}

struct bio_checkpoint_result bio_request_checkpoint(void)
{
	return bio_request_checkpoint_mode(0, 0);
}

struct bio_checkpoint_result bio_request_checkpoint_cleanup(void)
{
	return bio_request_checkpoint_mode(1, 0);
}

/*
 * A filesystem transaction may explicitly expose a safe scheduling point
 * while an outer atomic section is still open. The caller must have released
 * every buffer and must not expose an uncommitted in-memory inode/directory
 * mutation. The cleanup variant is for a forward-only durable commit that
 * cannot be rolled back after its first block has been published.
 */
struct bio_checkpoint_result bio_request_checkpoint_quiescent(void)
{
	return bio_request_checkpoint_mode(0, 1);
}

int bio_request_settle_quiescent_cleanup(void)
{
	if (!bio_io_quiescent_current())
		return -1;
	struct bio_checkpoint_result result =
		bio_request_checkpoint_mode(1, 1);

	if (result.state == BIO_CHECKPOINT_DEFERRED)
		panic("forward cleanup checkpoint deferred");
	return result.state == BIO_CHECKPOINT_READY ? 0 : -1;
}

void bio_fs_atomic_enter(void)
{
	uint *depth;

	if (bio_background_current())
		depth = &io_policy.background.fs_atomic_depth;
	else {
		struct thread *thread = curr_thread();

		depth = thread != 0 && thread->state == RUNNING ?
			&thread->bio_fs_atomic_depth : &bio_boot_fs_atomic_depth;
	}
	if (*depth == (uint)-1)
		panic("filesystem atomic depth overflow");
	(*depth)++;
}

void bio_fs_atomic_leave(void)
{
	uint *depth;

	if (bio_background_current())
		depth = &io_policy.background.fs_atomic_depth;
	else {
		struct thread *thread = curr_thread();

		depth = thread != 0 && thread->state == RUNNING ?
			&thread->bio_fs_atomic_depth : &bio_boot_fs_atomic_depth;
	}
	if (*depth == 0)
		panic("filesystem atomic depth underflow");
	(*depth)--;
}

int bio_io_quiescent_current(void)
{
	struct thread *thread = curr_thread();
	int quiescent;
	int enabled = intr_save();

	if (bio_background_current())
		quiescent = io_policy.background.buffer_holds == 0 &&
			io_policy.background.fs_atomic_depth == 0;
	else if (thread != 0 && thread->state == RUNNING)
		quiescent = thread->bio_buffer_holds == 0 &&
			thread->bio_fs_atomic_depth == 0;
	else
		quiescent = bio_boot_buffer_holds == 0 &&
			bio_boot_fs_atomic_depth == 0;
	intr_restore(enabled);
	return quiescent;
}

static int bio_request_end_current_mode(int wait_for_budget, int cleanup,
					int terminal)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	uint owner;
	uint io_class;
	uint source;
	uint device_source;
	uint transfers;
	uint flags;
	int result = 0;

	if (thread == 0)
		return 0;
	if (thread->io_request_depth == 0) {
		if (!bio_thread_request_flags_valid(thread))
			panic("idle I/O request identity");
		return 0;
	}
	if (!bio_thread_request_flags_valid(thread))
		panic("active I/O request identity");
	if (thread->io_request_depth > 1 && !terminal) {
		thread->io_request_depth--;
		return 0;
	}
	owner = thread->io_request_owner;
	io_class = thread->io_request_class;
	source = thread->io_request_reservation;
	device_source = thread->io_request_device_reservation;
	transfers = thread->io_request_transfers;
	flags = thread->io_request_flags;
	if ((flags & BIO_REQUEST_ACTIVE) == 0) {
		if ((flags & BIO_REQUEST_LAZY) == 0 || transfers != 0 ||
		    (flags & BIO_REQUEST_TRANSFERRED) != 0 ||
		    source != IO_RESERVATION_NONE ||
		    device_source != IO_RESERVATION_NONE)
			panic("inactive lazy I/O request state");
		bio_thread_request_clear(thread);
		int enabled = intr_save();

		state = io_state_find(owner, 0);
		if (state != 0)
			state->cache_only++;
		io_policy.cache_only++;
		intr_restore(enabled);
		return 0;
	}
	state = io_state_find(owner, 0);
	bio_thread_request_clear(thread);
	if (state == 0)
		return -1;
	if ((flags & BIO_REQUEST_TRANSFERRED) == 0) {
		int enabled = intr_save();

		if (transfers != 0)
			panic("uncommitted I/O request batch");
		if (source != IO_RESERVATION_NONE)
			io_rate_lease_refund(source, device_source);
		intr_restore(enabled);
	} else {
		int enabled = intr_save();

		io_rate_charge_transfers(state, io_class, transfers);
		intr_restore(enabled);
		if (wait_for_budget && io_policy.runtime_ready) {
			result = io_wait_for_debt(state, io_class, cleanup);
			if (result == 0)
				result = io_wait_for_device_debt(
					owner, io_class, cleanup);
		}
	}
	int enabled = intr_save();

	io_active_request_release(state);
	io_owner_reap_retired();
	intr_restore(enabled);
	return result;
}

int bio_request_end_current(int wait_for_budget)
{
	return bio_request_end_current_mode(wait_for_budget, 0, 0);
}

int bio_request_end_current_cleanup(void)
{
	/* No caller returns past process teardown; settle the entire lease. */
	return bio_request_end_current_mode(1, 1, 1);
}

void bio_request_abort_thread(struct thread *thread)
{
	struct io_owner_state *state;
	int enabled;

	if (thread == 0)
		return;
	enabled = intr_save();
	if (io_policy.deferred.active &&
	    io_policy.deferred.executor == thread &&
	    io_policy.deferred.executor_generation ==
		    thread->identity_generation)
		panic("abort active deferred I/O sponsor");
	if (io_policy.background.active &&
	    io_policy.background.executor == thread &&
	    io_policy.background.executor_generation ==
		    thread->identity_generation)
		panic("abort active background I/O executor");
	if (thread->io_request_depth == 0) {
		if (!bio_thread_request_flags_valid(thread))
			panic("aborted I/O request identity");
		intr_restore(enabled);
		return;
	}
	if (!bio_thread_request_flags_valid(thread))
		panic("active I/O request identity");
	if ((thread->io_request_flags & BIO_REQUEST_ACTIVE) == 0) {
		if ((thread->io_request_flags & BIO_REQUEST_LAZY) == 0 ||
		    thread->io_request_transfers != 0 ||
		    (thread->io_request_flags &
		     BIO_REQUEST_TRANSFERRED) != 0 ||
		    thread->io_request_reservation != IO_RESERVATION_NONE ||
		    thread->io_request_device_reservation != IO_RESERVATION_NONE)
			panic("aborted inactive lazy I/O request");
		bio_thread_request_clear(thread);
		intr_restore(enabled);
		return;
	}
	state = io_state_find(thread->io_request_owner, 0);
	if (state != 0) {
		if ((thread->io_request_flags &
		     BIO_REQUEST_TRANSFERRED) == 0 &&
		    thread->io_request_reservation !=
			    IO_RESERVATION_NONE)
			io_rate_lease_refund(
				thread->io_request_reservation,
				thread->io_request_device_reservation);
		else if ((thread->io_request_flags &
			  BIO_REQUEST_TRANSFERRED) != 0)
			io_rate_charge_transfers(
				state, thread->io_request_class,
				thread->io_request_transfers);
		io_active_request_release(state);
	}
	bio_thread_request_clear(thread);
	io_owner_reap_retired();
	intr_restore(enabled);
}

static void bio_background_release_buffers(uint owner)
{
	while (bcache.reserved_idle.tail != 0) {
		struct buf *b = bcache.reserved_idle.tail;

		if (!b->background_reserved || b->cache_owner != owner ||
		    b->refcnt != 0 || b->hold_depth != 0)
			panic("release active background buffer");
		bio_cache_invalidate(b);
	}
}

static int bio_background_reserve_buffers(uint owner)
{
	for (uint reserved = 0; reserved < IO_CACHE_CLEANUP_FLOOR;
	     reserved++) {
		struct io_owner_state *owner_state =
			bio_cache_owner_state(owner);
		struct buf *candidate = 0;
		uint owner_count = bio_cache_count(owner);
		uint owner_cap = bio_cache_cap(owner);

		if (owner_state == 0)
			panic("background cache owner vanished");
		if (owner_count < owner_cap)
			candidate = bcache.free_idle.tail;
		if (owner_count < owner_cap && candidate == 0)
			candidate = bio_cache_donor_candidate(owner, 0);
		if (candidate == 0)
			candidate = owner_state->idle.tail;
		if (candidate == 0) {
			bio_background_release_buffers(owner);
			return 0;
		}
		bio_cache_idle_remove(candidate);
		if (candidate->valid)
			bio_cache_record(owner, -1, 1);
		if (!bio_cache_assign(candidate, owner, 1, 1)) {
			bio_cache_idle_enqueue(candidate, 0);
			bio_background_release_buffers(owner);
			return 0;
		}
		bio_cache_hash_remove(candidate);
		candidate->valid = 0;
		candidate->dev = 0;
		candidate->blockno = 0;
		candidate->lru_promote = 0;
		candidate->background_reserved = 1;
		bio_cache_idle_enqueue(candidate, 1);
	}
	bio_cache_assert_integrity();
	return 1;
}

int bio_background_begin(uint owner)
{
	struct io_owner_state *state;
	struct thread *executor = curr_thread();
	uint source = IO_RESERVATION_NONE;
	uint device_source = IO_RESERVATION_NONE;
	int enabled = intr_save();

	if (io_policy.background.active || executor == 0 ||
	    (!io_policy.runtime_ready && executor->identity_generation != 0) ||
	    (io_policy.runtime_ready &&
	     (executor->state != RUNNING || executor->tid < 0 ||
	      executor->process == 0 || executor->identity_generation == 0 ||
	      executor->bio_buffer_holds != 0 ||
	      executor->bio_fs_atomic_depth != 0))) {
		intr_restore(enabled);
		return 0;
	}
	if (owner != FS_OWNER_SYSTEM && !FS_OWNER_IS_SCOPE(owner)) {
		intr_restore(enabled);
		return 0;
	}
	state = io_state_find(owner, 0);
	if (state == 0 || state->retiring ||
	    io_bucket_burst(owner, IO_POLICY_CLASS_BACKGROUND) == 0) {
		if (state != 0)
			state->throttles++;
		intr_restore(enabled);
		return 0;
	}
	memset(&io_policy.background, 0, sizeof(io_policy.background));
	io_policy.background.active = 1;
	io_policy.background.executor = executor;
	io_policy.background.executor_generation =
		executor->identity_generation;
	io_policy.background.boot_executor = !io_policy.runtime_ready;
	io_policy.background.owner = owner;
	if (!bio_background_reserve_buffers(owner)) {
		memset(&io_policy.background, 0,
		       sizeof(io_policy.background));
		state->throttles++;
		intr_restore(enabled);
		return 0;
	}
	if (io_policy.runtime_ready) {
		struct resource_rate_endpoint
			endpoints[RESOURCE_RATE_BUNDLE_CAP];
		struct resource_rate_lease_handle lease =
			resource_rate_lease_none();

		(void)io_rate_bundle(
			state, IO_POLICY_CLASS_BACKGROUND, 0,
			endpoints);
		if (resource_rate_reserve_many(
			    endpoints, 2, &lease) < 0) {
			bio_background_release_buffers(owner);
			memset(&io_policy.background, 0,
			       sizeof(io_policy.background));
			state->throttles++;
			intr_restore(enabled);
			return 0;
		}
		io_rate_lease_store(
			lease, 0, &source, &device_source);
		state->reserved_grants++;
	}
	state->admissions++;
	io_active_request_acquire(state);
	io_policy.background.reservation = source;
	io_policy.background.device_reservation = device_source;
	io_policy.background.transfers = 0;
	intr_restore(enabled);
	return 1;
}

int bio_background_active(uint owner)
{
	int enabled = intr_save();
	int active = bio_background_current() &&
		     io_policy.background.owner == owner;

	intr_restore(enabled);
	return active;
}

void bio_background_end(void)
{
	struct io_owner_state *state;
	int enabled = intr_save();
	uint owner;

	if (!io_policy.background.active) {
		intr_restore(enabled);
		return;
	}
	if (!bio_background_current())
		panic("background I/O executor");
	owner = io_policy.background.owner;
	if (io_policy.background.buffer_holds != 0)
		panic("background buffer hold leak");
	if (io_policy.background.fs_atomic_depth != 0)
		panic("background filesystem atomic leak");
	state = io_state_find(owner, 0);
	if (state == 0)
		panic("background I/O owner vanished at end");
	if (io_policy.background.transfers == 0) {
		if (io_policy.background.reservation != IO_RESERVATION_NONE)
			io_rate_lease_refund(
				io_policy.background.reservation,
				io_policy.background.device_reservation);
	}
	bio_background_release_buffers(owner);
	memset(&io_policy.background, 0, sizeof(io_policy.background));
	bio_cache_release_closed_owner(owner);
	bio_cache_assert_integrity();
	io_active_request_release(state);
	// Cleanup buffers lose their temporary floor when the background lease
	// ends. Re-evaluate every foreground cache waiter immediately.
	bio_cache_note_progress();
	io_owner_reap_retired();
	intr_restore(enabled);
}

uint bio_current_owner(void)
{
	struct thread *thread = curr_thread();

	if (bio_deferred_sponsor_current())
		return io_policy.deferred.owner;
	if (bio_background_current())
		return io_policy.background.owner;
	if (thread != 0 && thread->state == RUNNING &&
	    thread->io_request_depth != 0)
		return thread->io_request_owner;
	if (thread != 0 && thread->state == RUNNING && thread->process != 0)
		return bio_process_owner(thread->process);
	return FS_OWNER_SYSTEM;
}

static uint bio_current_class(uint owner)
{
	struct thread *thread = curr_thread();

	if (bio_deferred_sponsor_current())
		return io_policy.deferred.io_class;
	if (bio_background_current())
		return IO_POLICY_CLASS_BACKGROUND;
	if (thread != 0 && thread->state == RUNNING &&
	    thread->io_request_depth != 0)
		return thread->io_request_class;
	if (thread != 0 && thread->state == RUNNING && thread->process != 0)
		return io_class_from_proc(thread->process, owner);
	return IO_POLICY_CLASS_SYSTEM;
}

void bio_current_sponsor(uint *owner, uint *io_class)
{
	if (owner == 0 || io_class == 0)
		return;
	*owner = bio_current_owner();
	*io_class = bio_current_class(*owner);
}

int bio_deferred_owner_retain_current(uint owner, uint *io_class,
				      uint64 *origin_request_id)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	uint selected;
	int enabled;

	if (thread == 0 || io_class == 0 || origin_request_id == 0 ||
	    thread->state != RUNNING ||
	    bio_deferred_sponsor_current() || bio_background_current() ||
	    thread->io_request_depth == 0 ||
	    thread->io_request_id == 0 ||
	    thread->io_request_owner != owner)
		return -1;
	selected = thread->io_request_class;
	if (selected >= IO_POLICY_CLASS_COUNT ||
	    io_bucket_burst(owner, selected) == 0)
		return -1;
	enabled = intr_save();
	state = io_state_find(owner, 0);
	if (state == 0 || state->retiring || state->quiesced ||
	    resource_account_state_get(state->principal) !=
		    RESOURCE_ACCOUNT_ACTIVE) {
		intr_restore(enabled);
		return -1;
	}
	io_active_request_acquire(state);
	if (state->deferred_references == (uint)-1)
		panic("deferred I/O owner overflow");
	state->deferred_references++;
	*io_class = selected;
	*origin_request_id = thread->io_request_id;
	intr_restore(enabled);
	return 0;
}

int bio_deferred_owner_retain(uint owner, uint *io_class)
{
	uint64 origin_request_id;

	return bio_deferred_owner_retain_current(
		owner, io_class, &origin_request_id);
}

int
bio_deferred_owner_retain_cleanup(uint owner, uint *io_class)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	uint selected;
	int caller_holds;
	int enabled;

	if (thread == 0 || io_class == 0)
		return -1;
	caller_holds = (thread->io_request_depth != 0 &&
			thread->io_request_id != 0 &&
			thread->io_request_owner == owner) ||
			(bio_deferred_sponsor_current() &&
			 io_policy.deferred.owner == owner) ||
			(bio_background_current() &&
			 io_policy.background.owner == owner);
	if (!caller_holds)
		return -1;
	enabled = intr_save();
	state = io_state_find(owner, 0);
	/* A lazy identity is live without pinning the owner until this transfer. */
	if (state == 0 ||
	    (state->active_requests == 0 &&
	     !(thread->io_request_depth != 0 &&
	       thread->io_request_id != 0 &&
	       thread->io_request_owner == owner &&
	       (thread->io_request_flags & BIO_REQUEST_LAZY) != 0 &&
	       !state->retiring && !state->quiesced))) {
		intr_restore(enabled);
		return -1;
	}
	if (bio_deferred_sponsor_current() &&
	    io_policy.deferred.owner == owner)
		selected = io_policy.deferred.io_class;
	else if (bio_background_current() &&
		 io_policy.background.owner == owner)
		selected = IO_POLICY_CLASS_BACKGROUND;
	else
		selected = IO_POLICY_CLASS_BACKGROUND;
	if (io_bucket_burst(owner, selected) == 0) {
		if (thread->io_request_depth == 0 ||
		    thread->io_request_id == 0 ||
		    thread->io_request_owner != owner) {
			intr_restore(enabled);
			return -1;
		}
		selected = thread->io_request_class;
	}
	if (selected >= IO_POLICY_CLASS_COUNT ||
	    io_bucket_burst(owner, selected) == 0) {
		intr_restore(enabled);
		return -1;
	}
	io_active_request_acquire(state);
	if (state->deferred_references == (uint)-1)
		panic("deferred cleanup owner overflow");
	state->deferred_references++;
	*io_class = selected;
	intr_restore(enabled);
	return 0;
}

void bio_deferred_owner_release(uint owner)
{
	struct io_owner_state *state;
	int enabled = intr_save();

	state = io_state_find(owner, 0);
	if (state == 0 || state->active_requests == 0 ||
	    state->deferred_references == 0)
		panic("deferred I/O owner release");
	state->deferred_references--;
	bio_cache_release_closed_owner(owner);
	bio_cache_note_progress();
	io_active_request_release(state);
	io_owner_reap_retired();
	intr_restore(enabled);
}

static int
bio_cleanup_class_select(uint owner, uint *io_class)
{
	static const uint preference[] = {
		IO_POLICY_CLASS_BACKGROUND,
		IO_POLICY_CLASS_NORMAL,
		IO_POLICY_CLASS_SYSTEM,
	};

	if (io_class == 0)
		return -1;
	for (uint i = 0; i < sizeof(preference) / sizeof(preference[0]); i++)
		if (io_bucket_burst(owner, preference[i]) != 0) {
			*io_class = preference[i];
			return 0;
		}
	return -1;
}

static int
bio_cleanup_lease_reserve_locked(struct io_owner_state *state, uint io_class,
				 uint *reservation,
				 uint *device_reservation)
{
	struct resource_rate_endpoint endpoints[RESOURCE_RATE_BUNDLE_CAP];
	struct resource_rate_lease_handle lease = resource_rate_lease_none();

	if (!io_policy.runtime_ready) {
		*reservation = IO_RESERVATION_NONE;
		*device_reservation = IO_RESERVATION_NONE;
		return 0;
	}
	(void)io_rate_bundle(state, io_class, 0, endpoints);
	/* A retained cleanup must finish after lifecycle close or token depletion. */
	endpoints[0].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;
	endpoints[1].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;
	if (resource_rate_reserve_many(endpoints, 2, &lease) < 0)
		return -1;
	io_rate_lease_store(lease, 0, reservation, device_reservation);
	state->reserved_grants++;
	state->admissions++;
	return 0;
}

static int
bio_cleanup_handle_empty(const struct bio_cleanup_token *token)
{
	return token != 0 && token->slot == 0 && token->generation == 0;
}

int
bio_cleanup_sponsor_covers(uint owner, uint io_class,
			   uint64 origin_request_id)
{
	int enabled = intr_save();
	int covered = origin_request_id == 0 &&
		bio_deferred_sponsor_current() &&
		!bio_cleanup_handle_empty(&io_policy.deferred.token) &&
		io_policy.deferred.owner == owner &&
		io_policy.deferred.io_class == io_class;

	intr_restore(enabled);
	return covered;
}

static int
bio_cleanup_handle_equal(const struct bio_cleanup_token *left,
			 const struct bio_cleanup_token *right)
{
	return left != 0 && right != 0 && left->slot == right->slot &&
	       left->generation == right->generation;
}

static struct resource_account_handle
bio_cleanup_record_principal(const struct bio_cleanup_record *record)
{
	struct resource_account_handle principal = resource_account_none();

	if (record != 0) {
		principal.slot = record->principal_slot;
		principal.generation = record->principal_generation;
	}
	return principal;
}

static int
bio_cleanup_resolve_sponsor_locked(const struct bio_cleanup_record *record,
				   uint *effective_class,
				   uint *execution_flag)
{
	int background_executor = bio_background_current() &&
		io_policy.background.owner == record->owner;

	*effective_class = record->retained_class;
	if (io_bucket_burst(record->owner, *effective_class) == 0)
		return -1;
	if (background_executor &&
	    *effective_class == IO_POLICY_CLASS_BACKGROUND) {
		*execution_flag = BIO_CLEANUP_TOKEN_BACKGROUND;
		return 0;
	}
	*execution_flag = BIO_CLEANUP_TOKEN_INDEPENDENT;
	return 0;
}

static struct bio_cleanup_record *
bio_cleanup_record_lookup_locked(const struct bio_cleanup_token *token)
{
	struct bio_cleanup_record *record;

	if (token == 0 || token->slot == 0 ||
	    token->slot > BIO_CLEANUP_TOKEN_CAP || token->generation == 0)
		return 0;
	record = &bio_cleanup_pool.records[token->slot - 1];
	if (record->state == BIO_CLEANUP_TOKEN_EMPTY ||
	    record->generation != token->generation)
		return 0;
	return record;
}

static struct bio_cleanup_record *
bio_cleanup_record_allocate_locked(struct bio_cleanup_token *token)
{
	struct bio_cleanup_record *record;
	uint16 index;
	uint16 next;
	uint generation;

	if (!bio_cleanup_pool.initialized ||
	    !bio_cleanup_handle_empty(token) ||
	    bio_cleanup_pool.free_head == BIO_CLEANUP_SLOT_NONE)
		return 0;
	index = bio_cleanup_pool.free_head;
	if (index == BIO_CLEANUP_SLOT_NONE ||
	    index >= BIO_CLEANUP_TOKEN_CAP)
		panic("cleanup token freelist");
	record = &bio_cleanup_pool.records[index];
	if (record->state != BIO_CLEANUP_TOKEN_EMPTY)
		panic("cleanup token allocated slot");
	next = record->next_free;
	if (record->generation == (uint)-1)
		panic("cleanup token generation exhausted");
	generation = record->generation + 1;
	if (generation == 0)
		panic("cleanup token generation wrapped");
	memset(record, 0, sizeof(*record));
	record->generation = generation;
	record->next_free = BIO_CLEANUP_SLOT_NONE;
	record->state = BIO_CLEANUP_TOKEN_PREPARED;
	bio_cleanup_pool.free_head = next;
	token->slot = index + 1;
	token->generation = generation;
	return record;
}

static void
bio_cleanup_record_free_locked(struct bio_cleanup_record *record,
			       struct bio_cleanup_token *token)
{
	uint index;
	uint generation;

	if (record == 0 || token == 0 ||
	    record != bio_cleanup_record_lookup_locked(token) ||
	    record->state != BIO_CLEANUP_TOKEN_PREPARED)
		panic("cleanup token free");
	index = token->slot - 1;
	generation = record->generation;
	memset(record, 0, sizeof(*record));
	record->generation = generation;
	record->next_free = bio_cleanup_pool.free_head;
	bio_cleanup_pool.free_head = (uint16)index;
	token->slot = 0;
	token->generation = 0;
}

int
bio_cleanup_token_prepare(uint owner, struct bio_cleanup_token *token)
{
	struct io_owner_state *state;
	struct bio_cleanup_record *record;
	enum resource_account_state principal_state;
	uint retained_class;
	int enabled;

	if (!bio_cleanup_handle_empty(token) ||
	    owner == FS_OWNER_NONE || bio_deferred_sponsor_current())
		return -1;
	enabled = intr_save();
	state = io_state_find(owner, 0);
	principal_state = state == 0 ? RESOURCE_ACCOUNT_FREE :
		resource_account_state_get(state->principal);
	/*
	 * The owner-state principal generation is the trusted identity.  A final
	 * destructor may retain it after lifecycle quiesce/retire, but never after
	 * the state was reaped or rebound to a different storage principal.
	 */
	if (state == 0 || !state->principal_member ||
	    !resource_account_matches(state->principal,
				RESOURCE_ACCOUNT_STORAGE, owner) ||
	    (principal_state != RESOURCE_ACCOUNT_ACTIVE &&
	     principal_state != RESOURCE_ACCOUNT_CLOSING)) {
		intr_restore(enabled);
		return -1;
	}
	if (bio_cleanup_class_select(owner, &retained_class) < 0) {
		intr_restore(enabled);
		return -1;
	}
	if (state->deferred_references == (uint)-1) {
		intr_restore(enabled);
		panic("cleanup token owner overflow");
	}
	record = bio_cleanup_record_allocate_locked(token);
	if (record == 0) {
		intr_restore(enabled);
		return -1;
	}
	io_active_request_acquire(state);
	state->deferred_references++;
	record->principal_slot = (uint16)state->principal.slot;
	record->principal_generation = state->principal.generation;
	record->owner = owner;
	record->retained_class = retained_class;
	record->effective_class = retained_class;
	intr_restore(enabled);
	return 0;
}

int
bio_cleanup_token_sponsor(const struct bio_cleanup_token *token,
			  uint *owner, uint *io_class)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	struct bio_cleanup_record *record;
	uint execution_flag;
	int polling_executor;
	int enabled;

	if (token == 0 || owner == 0 || io_class == 0 || thread == 0)
		return -1;
	polling_executor = thread->tid < 0 && thread->identity_generation == 0;
	if ((!polling_executor && thread->state != RUNNING) ||
	    !bio_io_quiescent_current())
		return -1;
	enabled = intr_save();
	record = bio_cleanup_record_lookup_locked(token);
	state = record == 0 ? 0 : io_state_find(record->owner, 0);
	if (record == 0 || record->state != BIO_CLEANUP_TOKEN_PREPARED ||
	    io_policy.deferred.active || state == 0 ||
	    state->active_requests == 0 || state->deferred_references == 0 ||
	    !resource_account_handle_equal(state->principal,
					   bio_cleanup_record_principal(record)) ||
	    !resource_account_matches(bio_cleanup_record_principal(record),
				RESOURCE_ACCOUNT_STORAGE, record->owner) ||
	    bio_cleanup_resolve_sponsor_locked(
		    record, io_class, &execution_flag) < 0) {
		intr_restore(enabled);
		return -1;
	}
	*owner = record->owner;
	intr_restore(enabled);
	return 0;
}

int
bio_cleanup_token_begin(struct bio_cleanup_token *token)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	struct bio_cleanup_record *record;
	uint effective_class;
	uint execution_flag;
	int polling_executor;
	int enabled;

	if (token == 0 || thread == 0)
		return -1;
	polling_executor = thread->tid < 0 && thread->identity_generation == 0;
	if (!polling_executor && thread->state != RUNNING)
		return -1;
	if (!bio_io_quiescent_current())
		return -1;
	enabled = intr_save();
	record = bio_cleanup_record_lookup_locked(token);
	if (record != 0 && record->state == BIO_CLEANUP_TOKEN_ACTIVE) {
		if (!bio_deferred_sponsor_current() ||
		    !bio_cleanup_handle_equal(&io_policy.deferred.token, token) ||
		    io_policy.deferred.depth == (uint)-1) {
			intr_restore(enabled);
			return -1;
		}
		io_policy.deferred.depth++;
		intr_restore(enabled);
		return 0;
	}
	if (record == 0 || record->state != BIO_CLEANUP_TOKEN_PREPARED ||
	    io_policy.deferred.active) {
		intr_restore(enabled);
		return -1;
	}
	state = io_state_find(record->owner, 0);
	if (state == 0 || state->active_requests == 0 ||
	    state->deferred_references == 0 ||
	    !resource_account_handle_equal(state->principal,
					   bio_cleanup_record_principal(record)) ||
	    !resource_account_matches(bio_cleanup_record_principal(record),
				RESOURCE_ACCOUNT_STORAGE, record->owner)) {
		intr_restore(enabled);
		return -1;
	}
	if (bio_cleanup_resolve_sponsor_locked(
		    record, &effective_class, &execution_flag) < 0) {
		intr_restore(enabled);
		return -1;
	}
	if (execution_flag == BIO_CLEANUP_TOKEN_INDEPENDENT &&
	    bio_cleanup_lease_reserve_locked(
		    state, effective_class, &record->reservation,
		    &record->device_reservation) < 0) {
		intr_restore(enabled);
		return -1;
	}
	record->flags = execution_flag;
	if (polling_executor)
		record->flags |= BIO_CLEANUP_TOKEN_POLLING;
	record->effective_class = effective_class;
	record->flags &= ~BIO_CLEANUP_TOKEN_TRANSFERRED;
	record->state = BIO_CLEANUP_TOKEN_ACTIVE;
	io_policy.deferred.active = 1;
	io_policy.deferred.reuse_request_lease = 0;
	io_policy.deferred.independent_lease =
		(record->flags & BIO_CLEANUP_TOKEN_INDEPENDENT) != 0;
	io_policy.deferred.depth = 1;
	io_policy.deferred.executor = thread;
	io_policy.deferred.executor_generation = thread->identity_generation;
	io_policy.deferred.origin_request_id = 0;
	io_policy.deferred.owner = record->owner;
	io_policy.deferred.retained_class = record->retained_class;
	io_policy.deferred.io_class = effective_class;
	io_policy.deferred.reservation = record->reservation;
	io_policy.deferred.device_reservation = record->device_reservation;
	io_policy.deferred.transfers = 0;
	io_policy.deferred.token = *token;
	intr_restore(enabled);
	return 0;
}

int
bio_cleanup_token_end(struct bio_cleanup_token *token)
{
	int enabled = intr_save();
	struct bio_cleanup_record *record =
		bio_cleanup_record_lookup_locked(token);

	if (record == 0 || record->state != BIO_CLEANUP_TOKEN_ACTIVE ||
	    !bio_deferred_sponsor_current() ||
	    !bio_cleanup_handle_equal(&io_policy.deferred.token, token) ||
	    io_policy.deferred.depth == 0) {
		intr_restore(enabled);
		return -1;
	}
	if (--io_policy.deferred.depth != 0) {
		intr_restore(enabled);
		return 0;
	}
	if (!bio_io_quiescent_current())
		panic("cleanup token active I/O");
	if (io_policy.deferred.transfers != 0)
		record->flags |= BIO_CLEANUP_TOKEN_TRANSFERRED;
	record->state = BIO_CLEANUP_TOKEN_ENDED;
	memset(&io_policy.deferred, 0, sizeof(io_policy.deferred));
	intr_restore(enabled);
	return 0;
}

int
bio_cleanup_token_release(struct bio_cleanup_token *token, int final)
{
	struct io_owner_state *state;
	struct bio_cleanup_record *record;
	uint owner;
	uint io_class;
	uint flags;
	uint reservation;
	uint device_reservation;
	int enabled;
	int settlement = 0;

	if (token == 0 || (final != 0 && final != 1))
		return -1;
	enabled = intr_save();
	record = bio_cleanup_record_lookup_locked(token);
	if (record == 0 || record->state == BIO_CLEANUP_TOKEN_ACTIVE ||
	    (record->flags & BIO_CLEANUP_TOKEN_SETTLING) != 0) {
		intr_restore(enabled);
		return -1;
	}
	owner = record->owner;
	io_class = record->effective_class;
	flags = record->flags;
	reservation = record->reservation;
	device_reservation = record->device_reservation;
	if (record->state == BIO_CLEANUP_TOKEN_ENDED &&
	    (flags & BIO_CLEANUP_TOKEN_INDEPENDENT) != 0) {
		int polling = (flags & BIO_CLEANUP_TOKEN_POLLING) != 0;

		if (fs_epoch_request_held()) {
			intr_restore(enabled);
			return BIO_CLEANUP_NEEDS_SETTLEMENT;
		}
		state = io_state_find(owner, 0);
		if (state == 0 ||
		    !resource_account_handle_equal(state->principal,
						   bio_cleanup_record_principal(record))) {
			intr_restore(enabled);
			return -1;
		}
		record->flags |= BIO_CLEANUP_TOKEN_SETTLING;
		intr_restore(enabled);
		if ((flags & BIO_CLEANUP_TOKEN_TRANSFERRED) == 0) {
			if (reservation != IO_RESERVATION_NONE)
				io_rate_lease_refund(
					reservation, device_reservation);
		} else if (io_wait_for_debt_mode(
				   state, io_class, 1, 1, polling) < 0 ||
			   io_wait_for_device_debt_mode(
				   owner, io_class, 1, polling) < 0) {
			settlement = -1;
		}
		enabled = intr_save();
		record = bio_cleanup_record_lookup_locked(token);
		if (record == 0 || record->state != BIO_CLEANUP_TOKEN_ENDED ||
		    (record->flags & BIO_CLEANUP_TOKEN_SETTLING) == 0)
			panic("cleanup token settlement identity");
		record->flags &= ~BIO_CLEANUP_TOKEN_SETTLING;
		if (settlement < 0) {
			intr_restore(enabled);
			return -1;
		}
	}
	if (record->state == BIO_CLEANUP_TOKEN_ENDED) {
		record->reservation = IO_RESERVATION_NONE;
		record->device_reservation = IO_RESERVATION_NONE;
		record->flags = 0;
		record->effective_class = record->retained_class;
		record->state = BIO_CLEANUP_TOKEN_PREPARED;
	}
	if (!final) {
		intr_restore(enabled);
		return 0;
	}
	bio_cleanup_record_free_locked(record, token);
	intr_restore(enabled);
	bio_deferred_owner_release(owner);
	return BIO_CLEANUP_RELEASED;
}

int bio_deferred_sponsor_begin(uint owner, uint io_class,
			       uint64 origin_request_id)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	uint effective_class;
	int background_executor;
	int independent_lease = 0;
	int reuse_request_lease = 0;
	int polling_executor;
	int enabled;

	if (thread == 0 || io_class >= IO_POLICY_CLASS_COUNT)
		return -1;
	polling_executor = thread->tid < 0 && thread->identity_generation == 0;
	if (!polling_executor && thread->state != RUNNING)
		return -1;
	if (!bio_io_quiescent_current())
		return -1;
	enabled = intr_save();
	state = io_state_find(owner, 0);
	if (io_policy.deferred.active) {
		if (bio_deferred_sponsor_current() &&
		    io_policy.deferred.owner == owner &&
		    io_policy.deferred.io_class == io_class &&
		    io_policy.deferred.origin_request_id == origin_request_id &&
		    bio_cleanup_handle_empty(&io_policy.deferred.token) &&
		    io_policy.deferred.depth != (uint)-1) {
			io_policy.deferred.depth++;
			intr_restore(enabled);
			return 0;
		}
		intr_restore(enabled);
		return -1;
	}
	if (state == 0 || state->active_requests == 0 ||
	    state->deferred_references == 0 ||
	    io_bucket_burst(owner, io_class) == 0) {
		intr_restore(enabled);
		return -1;
	}
	background_executor = bio_background_current() &&
		io_policy.background.owner == owner;
	if (origin_request_id != 0 &&
	    !background_executor && !polling_executor &&
	    thread->state == RUNNING &&
	    thread->io_request_depth != 0 &&
	    thread->io_request_id == origin_request_id &&
	    thread->io_request_owner == owner &&
	    thread->io_request_class == io_class &&
	    bio_thread_request_active(thread)) {
		effective_class = io_class;
		reuse_request_lease = 1;
	} else if (background_executor) {
		effective_class = IO_POLICY_CLASS_BACKGROUND;
		if (io_bucket_burst(owner, effective_class) == 0) {
			intr_restore(enabled);
			return -1;
		}
	} else {
		if (bio_cleanup_class_select(owner, &effective_class) < 0 ||
		    bio_cleanup_lease_reserve_locked(
			    state, effective_class,
			    &io_policy.deferred.reservation,
			    &io_policy.deferred.device_reservation) < 0) {
			intr_restore(enabled);
			return -1;
		}
		independent_lease = 1;
	}
	io_policy.deferred.active = 1;
	io_policy.deferred.reuse_request_lease = reuse_request_lease;
	io_policy.deferred.independent_lease = independent_lease;
	io_policy.deferred.depth = 1;
	io_policy.deferred.executor = thread;
	io_policy.deferred.executor_generation = thread->identity_generation;
	io_policy.deferred.origin_request_id = origin_request_id;
	io_policy.deferred.owner = owner;
	io_policy.deferred.retained_class = io_class;
	io_policy.deferred.io_class = effective_class;
	io_policy.deferred.transfers = 0;
	io_policy.deferred.token = (struct bio_cleanup_token)
		BIO_CLEANUP_TOKEN_INIT;
	intr_restore(enabled);
	return 0;
}

void bio_deferred_sponsor_end(void)
{
	struct io_owner_state *state;
	uint device_reservation;
	uint reservation;
	uint transfers;
	uint io_class;
	uint owner;
	int independent;
	int polling;
	int enabled = intr_save();

	if (!bio_deferred_sponsor_current() ||
	    !bio_cleanup_handle_empty(&io_policy.deferred.token))
		panic("deferred I/O sponsor end");
	if (io_policy.deferred.depth == 0)
		panic("deferred I/O sponsor depth");
	if (--io_policy.deferred.depth != 0) {
		intr_restore(enabled);
		return;
	}
	if (!bio_io_quiescent_current())
		panic("deferred sponsor active I/O");
	independent = io_policy.deferred.independent_lease;
	owner = io_policy.deferred.owner;
	io_class = io_policy.deferred.io_class;
	reservation = io_policy.deferred.reservation;
	device_reservation = io_policy.deferred.device_reservation;
	transfers = io_policy.deferred.transfers;
	polling = bio_deferred_polling_current();
	state = io_state_find(owner, 0);
	if (state == 0)
		panic("deferred sponsor owner vanished");
	if (independent) {
		intr_restore(enabled);
		if (transfers == 0) {
			if (reservation != IO_RESERVATION_NONE)
				io_rate_lease_refund(
					reservation, device_reservation);
		} else if (io_wait_for_debt_mode(
				   state, io_class, 1, 1, polling) < 0 ||
			   io_wait_for_device_debt_mode(
				   owner, io_class, 1, polling) < 0) {
			panic("deferred sponsor debt settlement");
		}
		enabled = intr_save();
		if (!bio_deferred_sponsor_current() ||
		    io_policy.deferred.depth != 0 ||
		    !bio_cleanup_handle_empty(&io_policy.deferred.token))
			panic("deferred sponsor settlement identity");
	}
	memset(&io_policy.deferred, 0, sizeof(io_policy.deferred));
	intr_restore(enabled);
}

static void bio_account_transfers(uint owner, uint io_class,
			  enum bio_transfer_type transfer,
			  const int *results, uint count)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	uint64 completion_sequence;
	uint *transfers = 0;
	uint *request_flags = 0;
	uint reservation = IO_RESERVATION_NONE;
	uint device_reservation = IO_RESERVATION_NONE;
	uint failed = 0;
	uint successful;
	int unreserved = 1;
	int enabled;

	if (results == 0 || count == 0 ||
	    (transfer != BIO_TRANSFER_READ &&
	     transfer != BIO_TRANSFER_WRITE &&
	     transfer != BIO_TRANSFER_FLUSH))
		return;
	for (uint i = 0; i < count; i++)
		if (results[i] < 0)
			failed++;
	successful = count - failed;
	enabled = intr_save();
	if (transfer == BIO_TRANSFER_READ)
		io_policy.physical_reads += count;
	else if (transfer == BIO_TRANSFER_WRITE)
		io_policy.physical_writes += count;
	else
		io_policy.physical_flushes += count;
	io_policy.failed_transfers += failed;
	if (transfer == BIO_TRANSFER_WRITE)
		io_policy.successful_writes += successful;
	else if (transfer == BIO_TRANSFER_FLUSH)
		io_policy.successful_flushes += successful;
	io_policy.completion_sequence += count;
	completion_sequence = io_policy.completion_sequence;

	state = io_state_find(owner, 0);
	if (state == 0) {
		/* A lifecycle race stays in the untrusted aggregate. */
		state = io_state_find(FS_OWNER_PUBLIC, 0);
		owner = FS_OWNER_PUBLIC;
		io_class = IO_POLICY_CLASS_NORMAL;
	}
	if (state == 0) {
		intr_restore(enabled);
		return;
	}
	if (transfer == BIO_TRANSFER_READ)
		state->physical_reads += count;
	else if (transfer == BIO_TRANSFER_WRITE)
		state->physical_writes += count;
	else
		state->physical_flushes += count;
	state->failed_transfers += failed;
	state->completion_sequence = completion_sequence;
	if (bio_deferred_sponsor_current() &&
	    io_policy.deferred.independent_lease &&
	    owner == io_policy.deferred.owner &&
	    io_class == io_policy.deferred.io_class) {
		transfers = &io_policy.deferred.transfers;
		reservation = io_policy.deferred.reservation;
		device_reservation =
			io_policy.deferred.device_reservation;
		unreserved = 0;
	} else if (bio_deferred_sponsor_current() &&
	    io_policy.deferred.reuse_request_lease && thread != 0 &&
	    thread->state == RUNNING &&
	    thread->io_request_depth != 0 &&
	    io_policy.deferred.origin_request_id != 0 &&
	    thread->io_request_id == io_policy.deferred.origin_request_id &&
	    thread->io_request_owner == io_policy.deferred.owner &&
	    thread->io_request_class == io_policy.deferred.io_class &&
	    owner == io_policy.deferred.owner &&
	    io_class == io_policy.deferred.io_class) {
		transfers = &thread->io_request_transfers;
		request_flags = &thread->io_request_flags;
		reservation = thread->io_request_reservation;
		device_reservation =
			thread->io_request_device_reservation;
		unreserved = 0;
	} else if (bio_background_current() &&
	    io_policy.background.owner == owner &&
	    io_class == IO_POLICY_CLASS_BACKGROUND) {
		transfers = &io_policy.background.transfers;
		reservation = io_policy.background.reservation;
		device_reservation =
			io_policy.background.device_reservation;
		unreserved = 0;
	} else if (thread != 0 && thread->state == RUNNING &&
	    thread->io_request_depth != 0 &&
	    thread->io_request_id != 0 &&
	    thread->io_request_owner == owner &&
	    thread->io_request_class == io_class &&
	    bio_thread_request_active(thread)) {
		transfers = &thread->io_request_transfers;
		request_flags = &thread->io_request_flags;
		reservation = thread->io_request_reservation;
		device_reservation =
			thread->io_request_device_reservation;
		unreserved = 0;
	}
	if (!io_policy.runtime_ready) {
		if (transfers != 0) {
			if (count > (uint)-1 - *transfers)
				panic("I/O transfer count overflow");
			*transfers += count;
		}
		intr_restore(enabled);
		return;
	}
	if (unreserved) {
		state->unreserved_transfers += count;
		io_rate_charge_transfers(state, io_class, count);
	} else if (request_flags != 0) {
		if ((*request_flags & BIO_REQUEST_TRANSFERRED) == 0) {
			if (reservation != IO_RESERVATION_NONE)
				io_rate_lease_commit(
					reservation,
					device_reservation);
			else
				io_rate_charge_transfer(state, io_class);
			if (io_rate_lease_shared(reservation))
				state->shared_grants++;
			*request_flags |= BIO_REQUEST_TRANSFERRED;
			count--;
		}
		if (count > (uint)-1 - *transfers)
			panic("I/O request batch overflow");
		*transfers += count;
		if (*transfers >= IO_RATE_LOCAL_BATCH) {
			io_rate_charge_transfers(
				state, io_class, *transfers);
			*transfers = 0;
		}
	} else {
		uint previous = *transfers;

		if (count > (uint)-1 - previous)
			panic("I/O sponsor transfer overflow");
		*transfers = previous + count;
		if (previous == 0) {
			if (reservation != IO_RESERVATION_NONE)
				io_rate_lease_commit(
					reservation,
					device_reservation);
			else
				io_rate_charge_transfer(state, io_class);
			if (io_rate_lease_shared(reservation))
				state->shared_grants++;
			count--;
		}
		io_rate_charge_transfers(state, io_class, count);
	}
	intr_restore(enabled);
}

void bio_account_transfer(uint owner, uint io_class,
			  enum bio_transfer_type transfer, int result)
{
	bio_account_transfers(owner, io_class, transfer, &result, 1);
}

void bio_account_transfer_batch(uint owner, uint io_class,
				enum bio_transfer_type transfer,
				const int *results, uint count)
{
	bio_account_transfers(owner, io_class, transfer, results, count);
}

int bio_physical_snapshot(struct bio_physical_stats *stats)
{
	int enabled;

	if (stats == 0)
		return -1;
	enabled = intr_save();
	memset(stats, 0, sizeof(*stats));
	stats->version = BIO_PHYSICAL_STATS_VERSION;
	stats->size = sizeof(*stats);
	stats->reads = io_policy.physical_reads;
	stats->writes = io_policy.physical_writes;
	stats->flushes = io_policy.physical_flushes;
	stats->successful_writes = io_policy.successful_writes;
	stats->successful_flushes = io_policy.successful_flushes;
	stats->failed_transfers = io_policy.failed_transfers;
	stats->completion_sequence = io_policy.completion_sequence;
	stats->victim_candidates_examined =
		io_policy.victim_candidates_examined;
	stats->max_victim_candidates_per_miss =
		io_policy.max_victim_candidates_per_miss;
	stats->lazy_started = io_policy.lazy_started;
	stats->upgraded = io_policy.upgraded;
	stats->cache_only = io_policy.cache_only;
	intr_restore(enabled);
	return 0;
}

static uint bio_cache_state_floor(const struct io_owner_state *state)
{
	if (state == 0)
		return 0;
	if (state->owner == FS_OWNER_SYSTEM)
		return IO_CACHE_SYSTEM_FLOOR;
	if (state->owner == FS_OWNER_PUBLIC)
		return IO_CACHE_PUBLIC_FLOOR;
	if (FS_OWNER_IS_SCOPE(state->owner) && state->cache_live &&
	    !state->retiring &&
	    resource_account_state_get(state->principal) ==
		    RESOURCE_ACCOUNT_ACTIVE)
		return IO_CACHE_WORKFLOW_FLOOR;
	if (FS_OWNER_IS_SCOPE(state->owner) &&
	    bio_cache_state_retained(state))
		return IO_CACHE_CLEANUP_FLOOR;
	return 0;
}

static uint bio_cache_floor(uint owner)
{
	return bio_cache_state_floor(bio_cache_owner_state(owner));
}

static uint bio_cache_state_cap(const struct io_owner_state *state)
{
	if (state == 0)
		return 0;
	if (state->owner == FS_OWNER_SYSTEM)
		return IO_CACHE_SYSTEM_CAP;
	if (state->owner == FS_OWNER_PUBLIC)
		return IO_CACHE_PUBLIC_CAP;
	if (FS_OWNER_IS_SCOPE(state->owner) && state->cache_live &&
	    !state->retiring &&
	    resource_account_state_get(state->principal) ==
		    RESOURCE_ACCOUNT_ACTIVE)
		return IO_CACHE_WORKFLOW_CAP;
	if (FS_OWNER_IS_SCOPE(state->owner) &&
	    bio_cache_state_retained(state))
		return BIO_CACHE_CLEANUP_CAP;
	return 0;
}

static uint bio_cache_cap(uint owner)
{
	return bio_cache_state_cap(bio_cache_owner_state(owner));
}

static uint bio_cache_state_count(const struct io_owner_state *state)
{
	uint64 count;

	if (state == 0 ||
	    !resource_account_handle_valid(state->principal))
		return 0;
	count = resource_account_usage(
		state->principal, RESOURCE_BUFFER_CACHE);
	if (count > NBUF)
		panic("buffer-cache controller overflow");
	return count;
}

static uint bio_cache_count(uint owner)
{
	return bio_cache_state_count(bio_cache_owner_state(owner));
}

static struct buf *bio_cache_donor_candidate(uint owner, uint *examined)
{
	for (uint offset = 0; offset < IO_OWNER_SLOTS; offset++) {
		uint slot = (io_policy.cache_donor_cursor + offset) %
			IO_OWNER_SLOTS;
		struct io_owner_state *state = &io_policy.owners[slot];
		uint64 count;

		if (examined != 0)
			(*examined)++;
		if (!state->used || state->owner == owner ||
		    state->idle.tail == 0 ||
		    !bio_cache_state_retained(state) ||
		    !resource_account_handle_valid(state->principal))
			continue;
		count = resource_account_usage(
			state->principal, RESOURCE_BUFFER_CACHE);
		if (count > NBUF)
			panic("buffer-cache donor overflow");
		if (count <= bio_cache_state_floor(state))
			continue;
		io_policy.cache_donor_cursor =
			(slot + 1) % IO_OWNER_SLOTS;
		return state->idle.tail;
	}
	return 0;
}

static void bio_cache_record_victim_probe(uint examined)
{
	if (examined > IO_OWNER_SLOTS + BIO_CACHE_VICTIM_PROBE_OVERHEAD)
		panic("buffer victim probe unbounded");
	if (io_policy.victim_candidates_examined + examined <
	    io_policy.victim_candidates_examined)
		panic("buffer victim counter overflow");
	io_policy.victim_candidates_examined += examined;
	if (examined > io_policy.max_victim_candidates_per_miss)
		io_policy.max_victim_candidates_per_miss = examined;
}

static enum resource_charge_class
bio_cache_charge_class(uint owner)
{
	return owner == FS_OWNER_PUBLIC ?
		RESOURCE_CHARGE_ORDINARY :
		RESOURCE_CHARGE_RESERVED;
}

static void bio_cache_uncharge(struct buf *b)
{
	struct resource_request request = {
		.kind = RESOURCE_BUFFER_CACHE,
		.amount = 1,
	};

	if (!b->cache_charged)
		return;
	if (resource_release_many(
		    b->cache_principal,
		    (enum resource_charge_class)b->cache_charge_class,
		    &request, 1) < 0)
		panic("buffer-cache release");
	b->cache_charged = 0;
	b->cache_principal = resource_account_none();
	b->cache_charge_class = RESOURCE_CHARGE_ORDINARY;
}

static int bio_cache_assign(struct buf *b, uint owner, int stable,
			    int allow_closing)
{
	struct io_owner_state *target = io_state_find(owner, 0);
	enum resource_charge_class target_class =
		bio_cache_charge_class(owner);
	struct resource_request request = {
		.kind = RESOURCE_BUFFER_CACHE,
		.amount = 1,
	};
	uint flags = allow_closing ?
		RESOURCE_RESERVE_ALLOW_CLOSING : 0;

	if (b == 0 || b->idle_class != BIO_IDLE_NONE ||
	    b->prev != 0 || b->next != 0)
		panic("assign queued buffer");

	if (!stable) {
		bio_cache_uncharge(b);
		b->cache_owner = owner;
		b->cache_principal = resource_account_none();
		b->cache_charge_class = target_class;
		b->cache_charged = 0;
		b->transient = 1;
		return 1;
	}
	if (target == 0 ||
	    !resource_account_handle_valid(target->principal))
		return 0;
	if (b->cache_charged &&
	    resource_account_handle_equal(
		    b->cache_principal, target->principal) &&
	    b->cache_charge_class == (uint)target_class) {
		b->cache_owner = owner;
		b->transient = 0;
		return 1;
	}
	if (b->cache_charged) {
		if (resource_transfer_usage_flags(
			    b->cache_principal,
			    (enum resource_charge_class)
				    b->cache_charge_class,
			    target->principal, target_class,
			    &request, 1, flags) < 0)
			return 0;
	} else {
		struct resource_reservation reservation;

		if (resource_reserve_many_flags(
			    target->principal, target_class,
			    &request, 1, flags, &reservation) < 0)
			return 0;
		if (resource_reservation_commit(&reservation) < 0)
			panic("buffer-cache commit");
	}
	b->cache_owner = owner;
	b->cache_principal = target->principal;
	b->cache_charge_class = target_class;
	b->cache_charged = 1;
	b->transient = 0;
	return 1;
}

static void bio_cache_invalidate(struct buf *b)
{
	if (b->refcnt != 0 || b->hold_depth != 0)
		panic("invalidate held buffer");
	if (b->idle_class != BIO_IDLE_NONE)
		bio_cache_idle_remove(b);
	bio_cache_hash_remove(b);
	bio_cache_uncharge(b);
	b->valid = 0;
	b->dev = 0;
	b->blockno = 0;
	b->cache_owner = FS_OWNER_NONE;
	b->cache_principal = resource_account_none();
	b->cache_charge_class = RESOURCE_CHARGE_ORDINARY;
	b->cache_charged = 0;
	b->transient = 0;
	b->lru_promote = 0;
	b->background_reserved = 0;
	bio_cache_idle_enqueue(b, 1);
	bio_cache_note_progress();
}

static void bio_cache_record(uint owner, int hit, int eviction)
{
	struct io_owner_state *state = io_state_find(owner, 0);

	if (state == 0 && owner != FS_OWNER_SYSTEM)
		state = io_state_find(FS_OWNER_PUBLIC, 0);

	if (state == 0)
		return;
	if (hit > 0)
		state->cache_hits++;
	else if (hit == 0)
		state->cache_misses++;
	if (eviction)
		state->cache_evictions++;
}

static uint bio_current_cache_owner(void)
{
	if (bio_background_current())
		return io_policy.background.owner;
	return bio_current_owner();
}

static void *bio_cache_holder_token(void)
{
	struct thread *thread;

	if (bio_background_current())
		return &io_policy.background;
	thread = curr_thread();
	if (thread != 0 && thread->state == RUNNING)
		return thread;
	return &bio_boot_holder_token;
}

static void bio_cache_hold_acquire(void *token)
{
	if (token == &io_policy.background)
		io_policy.background.buffer_holds++;
	else if (token == &bio_boot_holder_token)
		bio_boot_buffer_holds++;
	else
		((struct thread *)token)->bio_buffer_holds++;
}

static void bio_cache_hold_release(void *token)
{
	uint *holds;

	if (token == &io_policy.background)
		holds = &io_policy.background.buffer_holds;
	else if (token == &bio_boot_holder_token)
		holds = &bio_boot_buffer_holds;
	else
		holds = &((struct thread *)token)->bio_buffer_holds;
	if (*holds == 0)
		panic("buffer holder underflow");
	(*holds)--;
}

static int bio_cache_lazy_owner_live(uint owner)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;

	if (!io_policy.runtime_ready || bio_request_active_current())
		return 1;
	if (thread == 0 || thread->state != RUNNING ||
	    thread->io_request_depth == 0 ||
	    (thread->io_request_flags & BIO_REQUEST_LAZY) == 0 ||
	    thread->io_request_owner != owner)
		return 0;
	state = io_state_find(owner, 0);
	return state != 0 && !state->retiring && !state->quiesced &&
	       state->cache_live;
}

static int bio_cache_batch_preflight(uint dev, const uint *blocknos,
				     uint count)
{
	void *holder = bio_cache_holder_token();
	uint owner = bio_current_cache_owner();
	int needs_upgrade = 0;
	int enabled;

	if (!io_policy.runtime_ready || bio_request_active_current())
		return VIRTIO_DISK_OK;
	enabled = intr_save();
	if (!bio_cache_lazy_owner_live(owner)) {
		intr_restore(enabled);
		return VIRTIO_DISK_ERR_BUSY;
	}
	for (uint i = 0; i < count; i++) {
		struct buf *b = bio_cache_hash_find(dev, blocknos[i]);

		if (b == 0 || !b->valid ||
		    (b->hold_depth != 0 && b->holder != holder)) {
			needs_upgrade = 1;
			break;
		}
	}
	intr_restore(enabled);
	if (!needs_upgrade)
		return VIRTIO_DISK_OK;
	return bio_request_upgrade_current() == 0 ?
		VIRTIO_DISK_OK : VIRTIO_DISK_ERR_BUSY;
}

// Look through buffer cache for block on device dev. If not found, select an
// idle victim without crossing an active principal's guaranteed floor.
static struct buf *bget(uint dev, uint blockno, int *result, int *fresh_line)
{
	struct buf *b;
	struct io_owner_state *owner_state;
	void *holder = bio_cache_holder_token();
	uint owner = bio_current_cache_owner();
	uint owner_count;
	uint owner_cap;
	int enabled = intr_save();

	if (result == 0)
		panic("bget result");
	*result = VIRTIO_DISK_ERR_IO;
	if (fresh_line != 0)
		*fresh_line = 0;
	bio_background_cache_retry_start();

	retry:
	b = bio_cache_hash_find(dev, blockno);
	if (!bio_request_active_current()) {
		if (!bio_cache_lazy_owner_live(owner)) {
			*result = VIRTIO_DISK_ERR_BUSY;
			intr_restore(enabled);
			return 0;
		}
		if (b == 0 || !b->valid) {
			intr_restore(enabled);
			if (bio_request_upgrade_current() < 0) {
				*result = VIRTIO_DISK_ERR_BUSY;
				return 0;
			}
			enabled = intr_save();
			goto retry;
		}
	}
	if (b != 0) {
		if (b->background_reserved)
			panic("reserved buffer in hash");
		if (b->hold_depth != 0 && b->holder != holder) {
			if (bio_background_current()) {
				bio_background_cache_blocked();
				*result = VIRTIO_DISK_ERR_BUSY;
				intr_restore(enabled);
				return 0;
			}
			if (wait_queue_sleep_irq_uninterruptible(
				    &b->holder_waiters) != WAIT_QUEUE_OK)
				panic("buffer holder wait invariant");
			goto retry;
		}
		if (b->refcnt == 0)
			bio_cache_idle_remove(b);
		else if (b->idle_class != BIO_IDLE_NONE)
			panic("active buffer in idle queue");
		b->refcnt++;
		if (b->hold_depth == 0)
			b->holder = holder;
		b->hold_depth++;
		bio_cache_hold_acquire(holder);
		if (b->cache_owner == owner)
			b->lru_promote = 1;
		bio_cache_record(owner, 1, 0);
		*result = VIRTIO_DISK_OK;
		intr_restore(enabled);
		return b;
	}
	bio_cache_record(owner, 0, 0);
	owner_state = bio_cache_owner_state(owner);
	owner_count = bio_cache_state_count(owner_state);
	owner_cap = bio_cache_state_cap(owner_state);
	uint examined = 0;
	int donor_scanned = 0;
	int free_scanned = 0;
	int transient = 0;
	b = 0;

	if (bio_background_current()) {
		examined++;
		if (bcache.reserved_idle.tail != 0) {
			if (bcache.reserved_idle.tail->cache_owner != owner)
				panic("reserved cache owner mismatch");
			b = bcache.reserved_idle.tail;
		}
	}
	if (b == 0 && owner_count < owner_cap) {
		examined++;
		free_scanned = 1;
		b = bcache.free_idle.tail;
	}
	if (b == 0 && owner_count < owner_cap) {
		b = bio_cache_donor_candidate(owner, &examined);
		donor_scanned = 1;
	}
	if (b == 0) {
		examined++;
		if (owner_state != 0)
			b = owner_state->idle.tail;
	}
	if (b == 0 && !free_scanned) {
		examined++;
		b = bcache.free_idle.tail;
		if (b != 0)
			transient = 1;
	}
	if (b == 0 && !donor_scanned) {
		b = bio_cache_donor_candidate(owner, &examined);
		if (b != 0)
			transient = 1;
	}
	bio_cache_record_victim_probe(examined);
	if (b == 0) {
		if (bio_background_current()) {
			bio_background_cache_blocked();
			*result = VIRTIO_DISK_ERR_BUSY;
			intr_restore(enabled);
			return 0;
		}
		if (wait_queue_sleep_irq_uninterruptible(&cache_waiters) !=
		    WAIT_QUEUE_OK)
			panic("buffer-cache wait invariant");
		goto retry;
	}
	bio_cache_idle_remove(b);
	if (b->valid)
		bio_cache_record(owner, -1, 1);
	if (!bio_cache_assign(
		    b, owner, !transient,
		    bio_background_current())) {
		if (bio_background_current()) {
			bio_cache_idle_enqueue(b, 0);
			bio_background_cache_blocked();
			*result = VIRTIO_DISK_ERR_BUSY;
			intr_restore(enabled);
			return 0;
		}
		(void)bio_cache_assign(b, owner, 0, 0);
		transient = 1;
	}
	bio_cache_hash_remove(b);
	b->dev = dev;
	b->blockno = blockno;
	bio_cache_hash_insert(b);
	b->valid = 0;
	b->refcnt = 1;
	b->lru_promote = 1;
	if (b->transient != transient)
		panic("buffer-cache charge mode mismatch");
	b->background_reserved = 0;
	b->holder = holder;
	b->hold_depth = 1;
	bio_cache_hold_acquire(holder);
	if (fresh_line != 0)
		*fresh_line = 1;
	*result = VIRTIO_DISK_OK;
	intr_restore(enabled);
	return b;
}

void binit(void)
{
	struct buf *b;
	struct resource_rate_profile shared = {
		.burst = IO_POLICY_SHARED_BURST,
		.refill = IO_POLICY_SHARED_REFILL,
	};
	struct resource_rate_profile device = {
		.burst = IO_POLICY_DEVICE_BURST,
		.refill = IO_POLICY_DEVICE_REFILL,
	};

	memset(&io_policy, 0, sizeof(io_policy));
	memset(&bio_cleanup_pool, 0, sizeof(bio_cleanup_pool));
	for (uint i = 0; i < BIO_CLEANUP_TOKEN_CAP; i++)
		bio_cleanup_pool.records[i].next_free =
			i + 1 < BIO_CLEANUP_TOKEN_CAP ?
				(uint16)(i + 1) : BIO_CLEANUP_SLOT_NONE;
	bio_cleanup_pool.free_head = 0;
	bio_cleanup_pool.initialized = 1;
	memset(bcache.hash_head, 0, sizeof(bcache.hash_head));
	if (resource_policy_configure(
		    RESOURCE_BUFFER_CACHE, NBUF,
		    IO_CACHE_PUBLIC_CAP, NBUF) < 0 ||
	    resource_rate_global_configure(
		    IO_RATE_GLOBAL_SHARED, &shared) < 0 ||
	    resource_rate_global_configure(
		    IO_RATE_GLOBAL_DEVICE, &device) < 0)
		panic("I/O resource controller policy");
	io_owner_init(&io_policy.owners[0], FS_OWNER_SYSTEM,
		      resource_account_none(), 0);
	io_owner_init(&io_policy.owners[1], FS_OWNER_PUBLIC,
		      resource_account_none(), 0);
	wait_queue_init(&io_policy.device.debt_queue,
			WAIT_REASON_IO_BUDGET);
	wait_queue_init(&cache_waiters, WAIT_REASON_BUFFER_CACHE);
	wait_queue_init(&background_cache_waiter,
			WAIT_REASON_BUFFER_CACHE);
	memset(&bcache.free_idle, 0, sizeof(bcache.free_idle));
	memset(&bcache.reserved_idle, 0, sizeof(bcache.reserved_idle));
	for (b = bcache.buf; b < bcache.buf + NBUF; b++) {
		b->valid = 0;
		b->dev = 0;
		b->blockno = 0;
		b->refcnt = 0;
		b->cache_owner = FS_OWNER_NONE;
		b->cache_principal = resource_account_none();
		b->cache_charge_class = RESOURCE_CHARGE_ORDINARY;
		b->cache_charged = 0;
		b->lru_promote = 0;
		b->transient = 0;
		b->background_reserved = 0;
		b->holder = 0;
		b->hold_depth = 0;
		b->hash_next = 0;
		b->prev = 0;
		b->next = 0;
		b->idle_class = BIO_IDLE_NONE;
		wait_queue_init(&b->holder_waiters,
				WAIT_REASON_BUFFER_CACHE);
		bio_cache_idle_enqueue(b, 1);
	}
	bio_cache_assert_integrity();
}

const int R = 0;
const int W = 1;

int bread(uint dev, uint blockno, struct buf **out)
{
	struct buf *b;
	int result = VIRTIO_DISK_OK;

	if (out == 0)
		return VIRTIO_DISK_ERR_IO;
	*out = 0;
	b = bget(dev, blockno, &result, 0);
	if (b == 0)
		return result;

	if (!b->valid) {
		result = virtio_disk_rw(b, R);
		b->disk_result = result;
		if (result == VIRTIO_DISK_OK)
			b->valid = 1;
		else {
			b->valid = 0;
			memset(b->data, 0, sizeof(b->data));
			brelse(b);
			return result;
		}
	} else {
		b->disk_result = VIRTIO_DISK_OK;
	}
	*out = b;
	return VIRTIO_DISK_OK;
}

int bread_batch(uint dev, const uint *blocknos, struct buf **out, uint count)
{
	struct buf *pending[VIRTIO_DISK_READ_BATCH_MAX];
	uint pending_count = 0;
	uint acquired = 0;
	int result = VIRTIO_DISK_OK;

	if (blocknos == 0 || out == 0 || count == 0 ||
	    count > VIRTIO_DISK_READ_BATCH_MAX)
		return VIRTIO_DISK_ERR_IO;
	for (uint i = 0; i < count; i++)
		out[i] = 0;
	result = bio_cache_batch_preflight(dev, blocknos, count);
	if (result != VIRTIO_DISK_OK)
		return result;
	for (; acquired < count; acquired++) {
		struct buf *b = bget(
			dev, blocknos[acquired], &result, 0);
		int queued = 0;

		if (b == 0)
			goto fail;
		out[acquired] = b;
		if (b->valid) {
			b->disk_result = VIRTIO_DISK_OK;
			continue;
		}
		for (uint j = 0; j < pending_count; j++)
			if (pending[j] == b) {
				queued = 1;
				break;
			}
		if (!queued)
			pending[pending_count++] = b;
	}
	for (uint i = 0; i < pending_count; i++)
		pending[i]->disk_result = VIRTIO_DISK_ERR_IO;
	if (pending_count != 0)
		result = virtio_disk_read_batch(pending, pending_count);
	for (uint i = 0; i < pending_count; i++) {
		struct buf *b = pending[i];

		if (b->disk_result == VIRTIO_DISK_OK)
			b->valid = 1;
		else {
			b->valid = 0;
			memset(b->data, 0, sizeof(b->data));
		}
	}
	if (result == VIRTIO_DISK_OK)
		return result;
fail:
	while (acquired != 0) {
		acquired--;
		if (out[acquired] != 0) {
			brelse(out[acquired]);
			out[acquired] = 0;
		}
	}
	return result;
}

/* Force a device transfer even when the requested block is cache-resident. */
int bread_device(uint dev, uint blockno, struct buf **out)
{
	struct buf *b;
	int result;

	if (out == 0)
		return VIRTIO_DISK_ERR_IO;
	*out = 0;
	if (fs_epoch_buffer_dirty(dev, blockno))
		return VIRTIO_DISK_ERR_BUSY;
	if (!bio_request_active_current() &&
	    bio_request_upgrade_current() < 0)
		return VIRTIO_DISK_ERR_BUSY;
	b = bget(dev, blockno, &result, 0);
	if (b == 0)
		return result;
	result = virtio_disk_rw(b, R);
	b->disk_result = result;
	if (result != VIRTIO_DISK_OK) {
		b->valid = 0;
		memset(b->data, 0, sizeof(b->data));
		brelse(b);
		return result;
	}
	b->valid = 1;
	*out = b;
	return VIRTIO_DISK_OK;
}

int bprepare_overwrite(uint dev, uint blockno,
		       struct bio_overwrite_receipt *receipt)
{
	struct buf *b;
	int fresh_line = 0;
	int result;

	if (receipt == 0 || receipt->active || receipt->buf != 0)
		return VIRTIO_DISK_ERR_IO;
	memset(receipt, 0, sizeof(*receipt));
	/* A staged line is authoritative until its epoch publishes. */
	if (fs_epoch_buffer_dirty(dev, blockno))
		return BIO_OVERWRITE_FALLBACK;
	b = bget(dev, blockno, &result, &fresh_line);
	if (b == 0)
		return result;
	if (!fresh_line || b->valid ||
	    fs_epoch_buffer_dirty(dev, blockno)) {
		brelse(b);
		return BIO_OVERWRITE_FALLBACK;
	}
	receipt->buf = b;
	receipt->dev = dev;
	receipt->blockno = blockno;
	receipt->active = 1;
	receipt->skipped_preread = 1;
	return VIRTIO_DISK_OK;
}

int bpublish_overwrite(struct bio_overwrite_receipt *receipt,
		       uint initialized, struct buf **out)
{
	struct buf *b;

	if (out != 0)
		*out = 0;
	if (receipt == 0 || out == 0 || !receipt->active ||
	    !receipt->skipped_preread || receipt->buf == 0 ||
	    initialized != BSIZE)
		return VIRTIO_DISK_ERR_IO;
	b = receipt->buf;
	if (b->holder != bio_cache_holder_token() ||
	    b->hold_depth == 0 || b->refcnt == 0 || b->valid ||
	    b->dev != receipt->dev || b->blockno != receipt->blockno)
		panic("overwrite publish receipt");
	if (fs_epoch_buffer_dirty(receipt->dev, receipt->blockno))
		return VIRTIO_DISK_ERR_BUSY;
	b->valid = 1;
	b->disk_result = VIRTIO_DISK_OK;
	kernel_performance_overwrite_preread_skipped(1);
	*out = b;
	memset(receipt, 0, sizeof(*receipt));
	return VIRTIO_DISK_OK;
}

void bcancel_overwrite(struct bio_overwrite_receipt *receipt)
{
	struct buf *b;

	if (receipt == 0 || !receipt->active)
		return;
	b = receipt->buf;
	if (b == 0 || !receipt->skipped_preread ||
	    b->holder != bio_cache_holder_token() ||
	    b->hold_depth == 0 || b->refcnt == 0 || b->valid ||
	    b->dev != receipt->dev || b->blockno != receipt->blockno)
		panic("overwrite cancel receipt");
	memset(b->data, 0, sizeof(b->data));
	b->disk_result = VIRTIO_DISK_ERR_IO;
	memset(receipt, 0, sizeof(*receipt));
	/* An unpublished miss is removed from the hash before waiters run. */
	brelse(b);
}

int bwrite(struct buf *b)
{
	if (b == 0 || b->hold_depth == 0 ||
	    b->holder != bio_cache_holder_token())
		panic("bwrite unlocked buffer");
	if (!bio_request_active_current())
		return VIRTIO_DISK_ERR_BUSY;
	b->disk_result = virtio_disk_rw(b, W);
	if (b->disk_result < 0) {
		b->valid = 0;
		memset(b->data, 0, sizeof(b->data));
	}
	return b->disk_result;
}

int bio_durable_flush(void)
{
	if (!bio_request_active_current() &&
	    bio_request_upgrade_current() < 0)
		return VIRTIO_DISK_ERR_BUSY;
	return virtio_disk_durability_barrier();
}

// Reallocated data blocks start a new sponsorship lifetime. Metadata callers
// deliberately do not use this operation, so shared filesystem structures
// cannot be stolen from their current protected partition by a reader/writer.
int bclaim(struct buf *b)
{
	uint owner;

	if (b == 0 || b->refcnt == 0 || b->hold_depth == 0 ||
	    b->holder != bio_cache_holder_token())
		panic("bclaim invalid buffer");
	owner = bio_current_cache_owner();
	if (b->cache_owner != owner) {
		int stable =
			bio_cache_count(owner) < bio_cache_cap(owner);

		if (!bio_cache_assign(
			    b, owner, stable,
			    bio_background_current())) {
			if (bio_background_current()) {
				bio_background_cache_blocked();
				return VIRTIO_DISK_ERR_BUSY;
			}
			if (!bio_cache_assign(b, owner, 0, 0))
				return VIRTIO_DISK_ERR_IO;
		}
	}
	b->lru_promote = 1;
	return VIRTIO_DISK_OK;
}

void brelse(struct buf *b)
{
	void *holder = bio_cache_holder_token();

	if (b == 0 || b->refcnt == 0 || b->hold_depth == 0 ||
	    b->holder != holder)
		panic("brelse underflow");
	b->hold_depth--;
	b->refcnt--;
	bio_cache_hold_release(holder);
	if (b->hold_depth == 0) {
		b->holder = 0;
		wait_queue_wake_all(&b->holder_waiters);
		bio_cache_note_progress();
	}
	if (b->refcnt == 0) {
		int promote = b->lru_promote;

		b->lru_promote = 0;
		if (!b->valid || b->transient ||
		    !bio_cache_owner_retained(b->cache_owner)) {
			bio_cache_invalidate(b);
		} else
			bio_cache_idle_enqueue(b, promote);
		// Cache eligibility depends on the requester's sponsor and on donor
		// floors. Wake every waiter so an ineligible head cannot hide a
		// newly usable buffer from another owner.
		wait_queue_wake_all(&cache_waiters);
		io_owner_reap_retired();
	}
}

void bpin(struct buf *b)
{
	if (b == 0)
		panic("bpin null");
	if (b->refcnt == 0)
		bio_cache_idle_remove(b);
	else if (b->idle_class != BIO_IDLE_NONE)
		panic("pinned buffer in idle queue");
	b->refcnt++;
}

void bunpin(struct buf *b)
{
	if (b == 0 || b->refcnt == 0)
		panic("bunpin underflow");
	b->refcnt--;
	if (b->refcnt == 0 && b->hold_depth != 0)
		panic("bunpin holder invariant");
	if (b->refcnt == 0 &&
	    (!b->valid || b->transient ||
	     !bio_cache_owner_retained(b->cache_owner))) {
		bio_cache_invalidate(b);
	} else if (b->refcnt == 0)
		bio_cache_idle_enqueue(b, 0);
	if (b->refcnt == 0) {
		bio_cache_note_progress();
		io_owner_reap_retired();
	}
}

int bio_principal_bind(uint owner,
		       struct resource_account_handle principal)
{
	struct io_owner_state *state;
	int enabled;

	if (!resource_account_matches(
		    principal, RESOURCE_ACCOUNT_STORAGE, owner))
		return -1;
	enabled = intr_save();
	state = io_state_find(owner, 0);
	if (state == 0) {
		intr_restore(enabled);
		return -1;
	}
	if (resource_account_handle_valid(state->principal) &&
	    !resource_account_handle_equal(state->principal, principal)) {
		intr_restore(enabled);
		return -1;
	}
	if (!state->principal_member &&
	    io_principal_prepare(owner, principal) < 0) {
		intr_restore(enabled);
		return -1;
	}
	state->principal = principal;
	state->principal_member = 1;
	intr_restore(enabled);
	return 0;
}

int bio_scope_acquire(uint scope_id,
		      struct resource_account_handle principal)
{
	struct io_owner_state *state = 0;
	uint owner;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG)
		return -1;
	owner = FS_OWNER_SCOPE(scope_id);
	if (!resource_account_matches(
		    principal, RESOURCE_ACCOUNT_STORAGE, owner) ||
	    !resource_account_active(principal))
		return -1;
	enabled = intr_save();
	io_owner_reap_retired();
	state = io_state_find(owner, 0);
	if (state != 0) {
		int valid = !state->retiring && !state->quiesced &&
			resource_account_handle_equal(state->principal,
						      principal);

		intr_restore(enabled);
		return valid ? 0 : -1;
	}
	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		if (!io_policy.owners[i].used) {
			state = &io_policy.owners[i];
			if (io_principal_prepare(
				    owner, principal) < 0) {
				state = 0;
				break;
			}
			io_owner_init(
				state, owner, principal, 1);
			break;
		}
	}
	intr_restore(enabled);
	return state != 0 ? 0 : -1;
}

void bio_scope_quiesce(uint scope_id)
{
	uint owner = FS_OWNER_SCOPE(scope_id);
	int enabled = intr_save();

	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (state->used && state->owner == owner) {
			state->quiesced = 1;
			state->cache_live = 0;
			for (uint c = 0; c < IO_POLICY_CLASS_COUNT; c++) {
				if (c != IO_POLICY_CLASS_BACKGROUND) {
					io_ready_clear(state, c);
					wait_queue_wake_all(
						&state->buckets[c].admission_queue);
					wait_queue_wake_all(
						&state->buckets[c].debt_queue);
				}
			}
			break;
		}
	}
	bio_cache_release_closed_owner(owner);
	bio_cache_assert_integrity();
	bio_cache_note_progress();
	intr_restore(enabled);
}

void bio_scope_retire(uint scope_id)
{
	uint owner = FS_OWNER_SCOPE(scope_id);
	int enabled = intr_save();

	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (!state->used || state->owner != owner)
			continue;
		state->retiring = 1;
		state->cache_live = 0;
		io_ready_clear_owner(state);
		for (uint c = 0; c < IO_POLICY_CLASS_COUNT; c++) {
			wait_queue_wake_all(
				&state->buckets[c].admission_queue);
			wait_queue_wake_all(&state->buckets[c].debt_queue);
		}
		break;
	}
	bio_cache_release_closed_owner(owner);
	bio_cache_assert_integrity();
	bio_cache_note_progress();
	io_owner_reap_retired();
	intr_restore(enabled);
}

int bio_policy_snapshot(const struct proc *p, struct io_policy_info *info)
{
	struct io_owner_state *state;
	struct io_bucket *bucket;
	struct resource_rate_snapshot lane;
	struct resource_rate_snapshot shared;
	struct resource_rate_snapshot device;
	uint owner;
	uint io_class;
	int enabled;

	if (p == 0 || info == 0)
		return -1;
	owner = bio_process_owner(p);
	io_class = io_class_from_proc(p, owner);
	enabled = intr_save();
	state = io_state_find(owner, 0);
	if (state == 0) {
		intr_restore(enabled);
		return -1;
	}
	bucket = &state->buckets[io_class];
	if (io_rate_snapshot(state, io_class, &lane) < 0 ||
	    io_rate_global_snapshot(
		    IO_RATE_GLOBAL_SHARED, &shared) < 0 ||
	    io_rate_global_snapshot(
		    IO_RATE_GLOBAL_DEVICE, &device) < 0) {
		intr_restore(enabled);
		return -1;
	}
	memset(info, 0, sizeof(*info));
	info->version = IO_POLICY_VERSION;
	info->struct_size = sizeof(*info);
	info->owner = owner;
	info->io_class = io_class;
	info->tokens = lane.tokens;
	info->debt = lane.debt;
	info->waiters = bucket->admission_waiters + bucket->debt_waiters;
	info->cache_resident = bio_cache_count(owner);
	info->cache_floor = bio_cache_floor(owner);
	info->cache_cap = bio_cache_cap(owner);
	info->shared_tokens = shared.tokens;
	info->leased = lane.leased;
	info->shared_leased = shared.leased;
	info->class_burst = io_bucket_burst(owner, io_class);
	info->class_refill = io_bucket_refill(owner, io_class);
	info->device_burst = IO_POLICY_DEVICE_BURST;
	info->device_refill = IO_POLICY_DEVICE_REFILL;
	info->device_tokens = device.tokens;
	info->device_debt = device.debt;
	info->device_leased = device.leased;
	info->admission_waiters = bucket->admission_waiters;
	info->debt_waiters = bucket->debt_waiters;
	info->admission_granted = bucket->grantee != 0;
	info->admissions = state->admissions;
	info->throttles = state->throttles;
	info->waits = state->waits;
	info->refills = state->refills;
	info->reserved_grants = state->reserved_grants;
	info->shared_grants = state->shared_grants;
	info->physical_reads = state->physical_reads;
	info->physical_writes = state->physical_writes;
	info->physical_flushes = state->physical_flushes;
	info->failed_transfers = state->failed_transfers;
	info->cache_hits = state->cache_hits;
	info->cache_misses = state->cache_misses;
	info->cache_evictions = state->cache_evictions;
	info->unreserved_transfers = state->unreserved_transfers;
	info->completion_sequence = state->completion_sequence;
	info->lazy_started = state->lazy_started;
	info->upgraded = state->upgraded;
	info->cache_only = state->cache_only;
	intr_restore(enabled);
	return 0;
}
