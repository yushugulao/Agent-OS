// Buffer cache and block-I/O admission policy.

#include "bio.h"
#include "agent.h"
#include "defs.h"
#include "fs.h"
#include "riscv.h"
#include "types.h"
#include "vfs_security.h"
#include "virtio.h"

#define IO_OWNER_SLOTS (NPROC + 2)
#define IO_RESERVATION_NONE 0U
#define IO_RESERVATION_OWNER 1U
#define IO_RESERVATION_SHARED 2U
#define IO_CACHE_CLEANUP_FLOOR 3U
#define IO_CACHE_CLEANUP_CAP 8U
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
		       IO_POLICY_WORKFLOW_BACKGROUND_REFILL +
	       IO_POLICY_SHARED_REFILL <= IO_POLICY_DEVICE_REFILL,
	       "owner I/O guarantees must fit the device envelope");
_Static_assert(IO_POLICY_SYSTEM_BURST +
	       IO_POLICY_SYSTEM_BACKGROUND_BURST +
	       IO_POLICY_PUBLIC_NORMAL_BURST +
	       VFS_SCOPE_MAX_ACTIVE *
		       (IO_POLICY_WORKFLOW_NORMAL_BURST +
			IO_POLICY_WORKFLOW_CONTROL_BURST +
			IO_POLICY_WORKFLOW_BACKGROUND_BURST) +
	       VFS_SCOPE_MAX_RETIRING *
		       IO_POLICY_WORKFLOW_BACKGROUND_BURST +
	       IO_POLICY_SHARED_BURST <= IO_POLICY_DEVICE_BURST,
	       "owner I/O bursts must fit the device envelope");

struct io_bucket {
	uint tokens;
	uint leased;
	uint debt;
	uint admission_waiters;
	uint debt_waiters;
	struct thread *grantee;
	uint grant_source;
	uint grant_device_source;
	struct wait_queue admission_queue;
	struct wait_queue debt_queue;
};

struct io_owner_state {
	int used;
	int retiring;
	int quiesced;
	int cache_live;
	uint owner;
	uint active_requests;
	struct io_bucket buckets[IO_POLICY_CLASS_COUNT];
	uint64 admissions;
	uint64 throttles;
	uint64 waits;
	uint64 refills;
	uint64 reserved_grants;
	uint64 shared_grants;
	uint64 physical_reads;
	uint64 physical_writes;
	uint64 cache_hits;
	uint64 cache_misses;
	uint64 cache_evictions;
	uint64 unreserved_transfers;
	uint64 completion_sequence;
};

struct io_background_context {
	int active;
	uint owner;
	uint reservation;
	uint device_reservation;
	uint transfers;
	uint buffer_holds;
	uint fs_atomic_depth;
};

static struct {
	struct io_owner_state owners[IO_OWNER_SLOTS];
	struct io_bucket shared;
	struct io_bucket device;
	struct io_background_context background;
	uint shared_cursor;
	uint64 completion_sequence;
	int runtime_ready;
} io_policy;

static struct wait_queue cache_waiters;
static char bio_boot_holder_token;
static uint bio_boot_fs_atomic_depth;

struct {
	struct buf buf[NBUF];
	struct buf head;
} bcache;

static uint bio_cache_floor(uint owner);
static uint bio_cache_cap(uint owner);
static uint bio_cache_count(uint owner);
static void bio_cache_invalidate(struct buf *b);
static void bio_cache_record(uint owner, int hit, int eviction);

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

static void io_owner_init(struct io_owner_state *state, uint owner)
{
	memset(state, 0, sizeof(*state));
	state->owner = owner;
	state->cache_live = 1;
	for (uint i = 0; i < IO_POLICY_CLASS_COUNT; i++) {
		state->buckets[i].tokens = io_bucket_burst(owner, i);
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
		    state->buckets[i].grantee != 0 ||
		    state->buckets[i].leased != 0 ||
		    state->buckets[i].debt != 0)
			return 1;
	return 0;
}

static void io_owner_reap_retired(void)
{
	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (state->used && state->retiring &&
		    state->active_requests == 0 &&
		    !io_owner_has_waiters(state))
			memset(state, 0, sizeof(*state));
	}
}

static int io_scope_owner_live(uint owner)
{
	if (!FS_OWNER_IS_SCOPE(owner))
		return owner == FS_OWNER_SYSTEM || owner == FS_OWNER_PUBLIC;
	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (state->used && state->owner == owner)
			return state->cache_live && !state->retiring;
	}
	return 0;
}

// A quiesced workflow normally gives up its cache partition immediately, but
// the one admitted cleanup request keeps a small partition for its bounded
// reclaim pass. This predicate must be shared by victim selection and release
// invalidation so the advertised cleanup floor is actually retained.
static int bio_cache_owner_retained(uint owner)
{
	if (io_scope_owner_live(owner))
		return 1;
	return FS_OWNER_IS_SCOPE(owner) && io_policy.background.active &&
	       io_policy.background.owner == owner;
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
	io_owner_init(free_state, owner);
	return free_state;
}

static uint io_owner_from_proc(const struct proc *p)
{
	if (p != 0 && p->vfs_scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
	    p->vfs_scope_id < FS_OWNER_SCOPE_FLAG &&
	    p->storage_principal_id == p->vfs_scope_id)
		return FS_OWNER_SCOPE(p->vfs_scope_id);
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

static int io_reserve_bucket(struct io_bucket *bucket, uint burst)
{
	if (burst == 0 || bucket->debt != 0 || bucket->tokens == 0)
		return 0;
	if (bucket->tokens + bucket->leased > burst)
		panic("I/O bucket capacity invariant");
	bucket->tokens--;
	bucket->leased++;
	return 1;
}

static int io_device_protected(uint owner, uint io_class)
{
	return owner == FS_OWNER_SYSTEM ||
	       io_class == IO_POLICY_CLASS_CONTROL ||
	       io_class == IO_POLICY_CLASS_SYSTEM;
}

static int io_reserve_device(uint owner, uint io_class, uint *source)
{
	if (io_reserve_bucket(&io_policy.device, IO_POLICY_DEVICE_BURST)) {
		*source = IO_RESERVATION_OWNER;
		return 1;
	}
	if (io_device_protected(owner, io_class)) {
		*source = IO_RESERVATION_NONE;
		return 1;
	}
	return 0;
}

static void io_commit_device(uint source)
{
	if (source == IO_RESERVATION_NONE)
		return;
	if (io_policy.device.leased == 0)
		panic("I/O device lease underflow");
	io_policy.device.leased--;
}

static void io_refund_device(uint source)
{
	if (source == IO_RESERVATION_NONE)
		return;
	if (io_policy.device.leased == 0)
		panic("I/O device refund underflow");
	io_policy.device.leased--;
	if (io_policy.device.tokens >= IO_POLICY_DEVICE_BURST)
		panic("I/O device refund overflow");
	io_policy.device.tokens++;
}

static void io_charge_device(void)
{
	if (io_policy.device.debt == 0 && io_policy.device.tokens != 0) {
		io_policy.device.tokens--;
		return;
	}
	if (io_policy.device.debt != (uint)-1)
		io_policy.device.debt++;
}

static void io_commit_credit(struct io_owner_state *state, uint io_class,
			     uint source)
{
	struct io_bucket *bucket = &state->buckets[io_class];

	if (source == IO_RESERVATION_OWNER) {
		if (bucket->leased == 0)
			panic("I/O owner lease underflow");
		bucket->leased--;
	} else if (source == IO_RESERVATION_SHARED) {
		if (io_policy.shared.leased == 0)
			panic("I/O shared lease underflow");
		io_policy.shared.leased--;
	}
}

static void io_refund_credit(struct io_owner_state *state, uint io_class,
			     uint source)
{
	struct io_bucket *bucket = &state->buckets[io_class];

	if (source == IO_RESERVATION_OWNER) {
		if (bucket->leased == 0)
			panic("I/O owner refund underflow");
		bucket->leased--;
		if (bucket->tokens >= io_bucket_burst(state->owner, io_class))
			panic("I/O owner refund overflow");
		bucket->tokens++;
	} else if (source == IO_RESERVATION_SHARED) {
		if (io_policy.shared.leased == 0)
			panic("I/O shared refund underflow");
		io_policy.shared.leased--;
		if (io_policy.shared.tokens >= IO_POLICY_SHARED_BURST)
			panic("I/O shared refund overflow");
		io_policy.shared.tokens++;
	}
	io_schedule_grants();
}

static void io_charge_credit(struct io_owner_state *state, uint io_class)
{
	struct io_bucket *bucket = &state->buckets[io_class];

	if (bucket->debt == 0 && bucket->tokens != 0) {
		bucket->tokens--;
		state->reserved_grants++;
		return;
	}
	if (bucket->debt != (uint)-1)
		bucket->debt++;
	state->throttles++;
}

static int io_any_admission_waiters(void)
{
	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (!state->used)
			continue;
		for (uint c = 0; c < IO_POLICY_CLASS_COUNT; c++)
			if (state->buckets[c].admission_waiters != 0)
				return 1;
	}
	return 0;
}

static int io_reserve_direct(struct io_owner_state *state, uint io_class,
			     uint *source, uint *device_source)
{
	struct io_bucket *bucket = &state->buckets[io_class];
	uint device = IO_RESERVATION_NONE;

	if (bucket->admission_waiters != 0 || state->retiring ||
	    (state->quiesced && io_class != IO_POLICY_CLASS_BACKGROUND))
		return 0;
	if (!io_reserve_device(state->owner, io_class, &device))
		return 0;
	if (io_reserve_bucket(bucket,
			      io_bucket_burst(state->owner, io_class))) {
		state->reserved_grants++;
		*source = IO_RESERVATION_OWNER;
		*device_source = device;
		return 1;
	}
	if (bucket->debt == 0 && io_class != IO_POLICY_CLASS_BACKGROUND &&
	    !io_any_admission_waiters() &&
	    io_reserve_bucket(&io_policy.shared, IO_POLICY_SHARED_BURST)) {
		state->shared_grants++;
		*source = IO_RESERVATION_SHARED;
		*device_source = device;
		return 1;
	}
	io_refund_device(device);
	return 0;
}

static int io_grant_waiter(struct io_owner_state *state, uint io_class,
			   uint source)
{
	struct io_bucket *bucket = &state->buckets[io_class];
	struct thread *head;
	uint device_source = IO_RESERVATION_NONE;

	if (state->retiring ||
	    (state->quiesced && io_class != IO_POLICY_CLASS_BACKGROUND) ||
	    bucket->grantee != 0 ||
	    (head = bucket->admission_queue.head) == 0)
		return 0;
	if (!io_reserve_device(state->owner, io_class, &device_source))
		return 0;
	if (source == IO_RESERVATION_OWNER) {
		if (!io_reserve_bucket(
			    bucket, io_bucket_burst(state->owner, io_class))) {
			io_refund_device(device_source);
			return 0;
		}
		state->reserved_grants++;
	} else {
		if (io_class == IO_POLICY_CLASS_BACKGROUND ||
		    bucket->debt != 0 ||
		    !io_reserve_bucket(&io_policy.shared,
				       IO_POLICY_SHARED_BURST)) {
			io_refund_device(device_source);
			return 0;
		}
		state->shared_grants++;
	}
	bucket->grantee = head;
	bucket->grant_source = source;
	bucket->grant_device_source = device_source;
	if (!wait_queue_wake_one(&bucket->admission_queue))
		panic("I/O admission grant lost waiter");
	return 1;
}

static void io_schedule_grants(void)
{
	uint slots = IO_OWNER_SLOTS * IO_POLICY_CLASS_COUNT;

	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (!state->used || state->retiring)
			continue;
		for (uint c = 0; c < IO_POLICY_CLASS_COUNT; c++)
			(void)io_grant_waiter(state, c,
					      IO_RESERVATION_OWNER);
	}
	while (io_policy.shared.tokens != 0 && slots != 0) {
		int granted = 0;

		for (uint scanned = 0; scanned < slots; scanned++) {
			uint index = (io_policy.shared_cursor + scanned) % slots;
			uint owner_slot = index / IO_POLICY_CLASS_COUNT;
			uint io_class = index % IO_POLICY_CLASS_COUNT;
			struct io_owner_state *state =
				&io_policy.owners[owner_slot];

			if (!state->used || state->retiring ||
			    io_class == IO_POLICY_CLASS_BACKGROUND)
				continue;
			if (io_grant_waiter(state, io_class,
					    IO_RESERVATION_SHARED)) {
				io_policy.shared_cursor = (index + 1) % slots;
				granted = 1;
				break;
			}
		}
		if (!granted)
			break;
	}
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
			if (sleep_status != WAIT_QUEUE_OK || state->retiring ||
			    (state->quiesced &&
			     io_class != IO_POLICY_CLASS_BACKGROUND)) {
				io_refund_credit(state, io_class,
						 granted_source);
				io_refund_device(granted_device);
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
			io_schedule_grants();
			intr_restore(enabled);
			return -1;
		}
	}
}

static int io_wait_for_debt(struct io_owner_state *state, uint io_class,
			    int cleanup)
{
	struct io_bucket *bucket = &state->buckets[io_class];
	int enabled = intr_save();

	while (bucket->debt != 0) {
		if (state->retiring || state->quiesced) {
			intr_restore(enabled);
			return -1;
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

static int io_wait_for_device_debt(uint owner, uint io_class, int cleanup)
{
	int enabled;

	if (io_device_protected(owner, io_class))
		return 0;
	enabled = intr_save();
	while (io_policy.device.debt != 0) {
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

void bio_policy_start(void)
{
	int enabled = intr_save();

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
			if (state->buckets[c].leased != 0 ||
			    state->buckets[c].admission_waiters != 0 ||
			    state->buckets[c].debt_waiters != 0)
				panic("I/O policy started with live leases");
			state->buckets[c].tokens =
				io_bucket_burst(state->owner, c);
			state->buckets[c].debt = 0;
		}
	}
	if (io_policy.shared.leased != 0)
		panic("I/O shared policy started with live leases");
	if (io_policy.device.leased != 0 ||
	    io_policy.device.admission_waiters != 0 ||
	    io_policy.device.debt_waiters != 0)
		panic("I/O device policy started with live leases");
	io_policy.shared.tokens = IO_POLICY_SHARED_BURST;
	io_policy.shared.debt = 0;
	io_policy.device.tokens = IO_POLICY_DEVICE_BURST;
	io_policy.device.debt = 0;
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
			uint burst = io_bucket_burst(state->owner, c);
			uint refill = io_bucket_refill(state->owner, c);
			uint paid = MIN(bucket->debt, refill);
			uint old_debt = bucket->debt;

			bucket->debt -= paid;
			refill -= paid;
			if (refill != 0 && !state->retiring &&
			    (!state->quiesced ||
			     c == IO_POLICY_CLASS_BACKGROUND)) {
				uint occupied = bucket->tokens + bucket->leased;
				uint room;

				if (occupied > burst)
					panic("I/O refill capacity invariant");
				room = burst - occupied;
				uint added = MIN(room, refill);

				bucket->tokens += added;
				state->refills += paid + added;
			} else {
				state->refills += paid;
			}
			if (old_debt != 0 && bucket->debt == 0)
				wait_queue_wake_all(&bucket->debt_queue);
		}
	}
	if (io_policy.shared.tokens + io_policy.shared.leased <
	    IO_POLICY_SHARED_BURST) {
		uint room = IO_POLICY_SHARED_BURST - io_policy.shared.tokens -
			    io_policy.shared.leased;
		io_policy.shared.tokens += MIN(room, IO_POLICY_SHARED_REFILL);
	}
	{
		uint refill = IO_POLICY_DEVICE_REFILL;
		uint old_debt = io_policy.device.debt;
		uint paid = MIN(old_debt, refill);

		io_policy.device.debt -= paid;
		refill -= paid;
		if (refill != 0) {
			uint occupied = io_policy.device.tokens +
					io_policy.device.leased;
			uint room;

			if (occupied > IO_POLICY_DEVICE_BURST)
				panic("I/O device refill capacity invariant");
			room = IO_POLICY_DEVICE_BURST - occupied;
			io_policy.device.tokens += MIN(room, refill);
		}
		if (old_debt != 0 && io_policy.device.debt == 0)
			wait_queue_wake_all(&io_policy.device.debt_queue);
	}
	io_schedule_grants();
	io_owner_reap_retired();
	intr_restore(enabled);
}

static int bio_request_begin_current_mode(int cleanup)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	uint owner;
	uint io_class;
	uint source = IO_RESERVATION_NONE;
	uint device_source = IO_RESERVATION_NONE;

	if (thread == 0 || thread->process == 0 || thread->state != RUNNING)
		return -1;
	if (thread->io_request_depth != 0) {
		if (thread->io_request_depth == (uint)-1)
			panic("I/O request depth overflow");
		thread->io_request_depth++;
		return 0;
	}
	owner = io_owner_from_proc(thread->process);
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
		io_refund_device(device_source);
		if (source != IO_RESERVATION_NONE)
			io_refund_credit(state, io_class, source);
		intr_restore(enabled);
		return -1;
	}
	state->active_requests++;
	thread->io_request_owner = owner;
	thread->io_request_class = io_class;
	thread->io_request_reservation = source;
	thread->io_request_device_reservation = device_source;
	thread->io_request_transfers = 0;
	thread->io_request_depth = 1;
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
	if (thread != 0 && thread->io_request_depth != 0)
		return 0;
	return bio_request_begin_current_mode(1);
}

static int bio_request_checkpoint_mode(int cleanup, int quiescent)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	int enabled;

	if (!io_policy.runtime_ready)
		return 0;
	// Scheduler maintenance cannot sleep. Once it consumes its bounded
	// background quantum, resumable work returns a short result.
	if (io_policy.background.active) {
		if (io_policy.background.buffer_holds != 0)
			return BIO_CHECKPOINT_DEFERRED;
		enabled = intr_save();
		state = io_state_find(io_policy.background.owner, 0);
		int ready = state != 0 &&
			state->buckets[IO_POLICY_CLASS_BACKGROUND].debt == 0 &&
			(io_device_protected(io_policy.background.owner,
					     IO_POLICY_CLASS_BACKGROUND) ||
			 io_policy.device.debt == 0);

		intr_restore(enabled);
		return ready ? 0 : BIO_CHECKPOINT_DEFERRED;
	}
	if (thread == 0 || thread->io_request_depth == 0)
		return 0;
	if (!cleanup && proc_thread_exit_requested())
		return BIO_CHECKPOINT_INTERRUPTED;
	enabled = intr_save();
	state = io_state_find(thread->io_request_owner, 0);
	if (state == 0) {
		intr_restore(enabled);
		return BIO_CHECKPOINT_INTERRUPTED;
	}
	int ready = state->buckets[thread->io_request_class].debt == 0 &&
		(io_device_protected(thread->io_request_owner,
				     thread->io_request_class) ||
		 io_policy.device.debt == 0);

	intr_restore(enabled);
	if (ready)
		return 0;
	/*
	 * Filesystem primitives publish several related in-memory and on-disk
	 * fields as one single-CPU transaction. Never sleep halfway through that
	 * transaction; report a short operation and let its outer request boundary
	 * pay the accumulated debt after every buffer has been released.
	 */
	if (thread->bio_buffer_holds != 0) {
		if (quiescent)
			panic("I/O quiescent checkpoint holds buffer");
		return BIO_CHECKPOINT_DEFERRED;
	}
	if (!quiescent && thread->bio_fs_atomic_depth != 0)
		return BIO_CHECKPOINT_DEFERRED;
	if (io_wait_for_debt(state, thread->io_request_class, cleanup) < 0)
		return BIO_CHECKPOINT_INTERRUPTED;
	return io_wait_for_device_debt(thread->io_request_owner,
				       thread->io_request_class, cleanup) < 0 ?
		       BIO_CHECKPOINT_INTERRUPTED : 0;
}

int bio_request_checkpoint(void)
{
	return bio_request_checkpoint_mode(0, 0);
}

int bio_request_checkpoint_cleanup(void)
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
int bio_request_checkpoint_quiescent(void)
{
	return bio_request_checkpoint_mode(0, 1);
}

int bio_request_checkpoint_quiescent_cleanup(void)
{
	return bio_request_checkpoint_mode(1, 1);
}

void bio_fs_atomic_enter(void)
{
	uint *depth;

	if (io_policy.background.active)
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

	if (io_policy.background.active)
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
	int result = 0;

	if (thread == 0 || thread->io_request_depth == 0)
		return 0;
	if (thread->io_request_depth > 1 && !terminal) {
		thread->io_request_depth--;
		return 0;
	}
	owner = thread->io_request_owner;
	io_class = thread->io_request_class;
	source = thread->io_request_reservation;
	device_source = thread->io_request_device_reservation;
	transfers = thread->io_request_transfers;
	state = io_state_find(owner, 0);
	thread->io_request_depth = 0;
	thread->io_request_owner = FS_OWNER_NONE;
	thread->io_request_class = IO_POLICY_CLASS_NORMAL;
	thread->io_request_reservation = IO_RESERVATION_NONE;
	thread->io_request_device_reservation = IO_RESERVATION_NONE;
	thread->io_request_transfers = 0;
	if (state == 0)
		return -1;
	if (transfers == 0) {
		int enabled = intr_save();

		io_refund_device(device_source);
		if (source != IO_RESERVATION_NONE)
			io_refund_credit(state, io_class, source);
		intr_restore(enabled);
	} else if (wait_for_budget && io_policy.runtime_ready) {
		result = io_wait_for_debt(state, io_class, cleanup);
		if (result == 0)
			result = io_wait_for_device_debt(owner, io_class, cleanup);
	}
	int enabled = intr_save();

	if (state->active_requests == 0)
		panic("I/O active request underflow");
	state->active_requests--;
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

	if (thread == 0 || thread->io_request_depth == 0)
		return;
	enabled = intr_save();
	state = io_state_find(thread->io_request_owner, 0);
	if (state != 0) {
		if (thread->io_request_transfers == 0) {
			io_refund_device(
				thread->io_request_device_reservation);
			if (thread->io_request_reservation !=
			    IO_RESERVATION_NONE)
				io_refund_credit(state,
						 thread->io_request_class,
						 thread->io_request_reservation);
		}
		if (state->active_requests == 0)
			panic("I/O abort request underflow");
		state->active_requests--;
	}
	thread->io_request_depth = 0;
	thread->io_request_owner = FS_OWNER_NONE;
	thread->io_request_class = IO_POLICY_CLASS_NORMAL;
	thread->io_request_reservation = IO_RESERVATION_NONE;
	thread->io_request_device_reservation = IO_RESERVATION_NONE;
	thread->io_request_transfers = 0;
	io_owner_reap_retired();
	intr_restore(enabled);
}

static void bio_background_release_buffers(uint owner)
{
	for (uint i = 0; i < NBUF; i++) {
		struct buf *b = &bcache.buf[i];

		if (b->background_reserved && b->cache_owner == owner) {
			if (b->refcnt != 0 || b->hold_depth != 0)
				panic("release active background buffer");
			bio_cache_invalidate(b);
		}
	}
}

static int bio_background_reserve_buffers(uint owner)
{
	for (uint reserved = 0; reserved < IO_CACHE_CLEANUP_FLOOR;
	     reserved++) {
		struct buf *free_candidate = 0;
		struct buf *donor_candidate = 0;
		struct buf *own_candidate = 0;
		struct buf *candidate = 0;
		uint owner_count = bio_cache_count(owner);
		uint owner_cap = bio_cache_cap(owner);

		for (struct buf *b = bcache.head.prev; b != &bcache.head;
		     b = b->prev) {
			uint victim_count;
			uint victim_floor;

			if (b->refcnt != 0 || b->hold_depth != 0 ||
			    b->background_reserved)
				continue;
			if (!b->valid || b->cache_owner == FS_OWNER_NONE ||
			    !bio_cache_owner_retained(b->cache_owner)) {
				if (free_candidate == 0)
					free_candidate = b;
				continue;
			}
			if (b->cache_owner == owner) {
				if (own_candidate == 0)
					own_candidate = b;
				continue;
			}
			victim_count = bio_cache_count(b->cache_owner);
			victim_floor = bio_cache_floor(b->cache_owner);
			if (victim_count > victim_floor &&
			    donor_candidate == 0)
				donor_candidate = b;
		}
		if (owner_count < owner_cap && free_candidate != 0)
			candidate = free_candidate;
		else if (owner_count < owner_cap && donor_candidate != 0)
			candidate = donor_candidate;
		else if (own_candidate != 0)
			candidate = own_candidate;
		if (candidate == 0) {
			bio_background_release_buffers(owner);
			return 0;
		}
		if (candidate->valid)
			bio_cache_record(owner, -1, 1);
		bio_cache_invalidate(candidate);
		candidate->cache_owner = owner;
		candidate->background_reserved = 1;
	}
	return 1;
}

int bio_background_begin(uint owner)
{
	struct io_owner_state *state;
	uint source = IO_RESERVATION_NONE;
	uint device_source = IO_RESERVATION_NONE;
	int enabled = intr_save();

	if (io_policy.background.active) {
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
	io_policy.background.owner = owner;
	if (!bio_background_reserve_buffers(owner)) {
		memset(&io_policy.background, 0,
		       sizeof(io_policy.background));
		state->throttles++;
		intr_restore(enabled);
		return 0;
	}
	if (io_policy.runtime_ready) {
		if (!io_reserve_device(owner, IO_POLICY_CLASS_BACKGROUND,
				       &device_source) ||
		    !io_reserve_bucket(
			    &state->buckets[IO_POLICY_CLASS_BACKGROUND],
			    io_bucket_burst(owner,
					    IO_POLICY_CLASS_BACKGROUND))) {
			io_refund_device(device_source);
			bio_background_release_buffers(owner);
			memset(&io_policy.background, 0,
			       sizeof(io_policy.background));
			state->throttles++;
			intr_restore(enabled);
			return 0;
		}
		source = IO_RESERVATION_OWNER;
		state->reserved_grants++;
	}
	state->admissions++;
	state->active_requests++;
	io_policy.background.reservation = source;
	io_policy.background.device_reservation = device_source;
	io_policy.background.transfers = 0;
	intr_restore(enabled);
	return 1;
}

int bio_background_active(uint owner)
{
	int enabled = intr_save();
	int active = io_policy.background.active &&
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
	owner = io_policy.background.owner;
	if (io_policy.background.buffer_holds != 0)
		panic("background buffer hold leak");
	state = io_state_find(owner, 0);
	if (state != 0 && io_policy.background.transfers == 0) {
		io_refund_device(io_policy.background.device_reservation);
		if (io_policy.background.reservation != IO_RESERVATION_NONE)
			io_refund_credit(state, IO_POLICY_CLASS_BACKGROUND,
					 io_policy.background.reservation);
	}
	if (state != 0) {
		if (state->active_requests == 0)
			panic("background I/O request underflow");
		state->active_requests--;
	}
	bio_background_release_buffers(owner);
	memset(&io_policy.background, 0, sizeof(io_policy.background));
	// Cleanup buffers lose their temporary floor when the background lease
	// ends. Re-evaluate every foreground cache waiter immediately.
	wait_queue_wake_all(&cache_waiters);
	io_owner_reap_retired();
	intr_restore(enabled);
}

uint bio_current_owner(void)
{
	struct thread *thread = curr_thread();

	if (io_policy.background.active)
		return io_policy.background.owner;
	if (thread != 0 && thread->state == RUNNING &&
	    thread->io_request_depth != 0)
		return thread->io_request_owner;
	if (thread != 0 && thread->state == RUNNING && thread->process != 0)
		return io_owner_from_proc(thread->process);
	return FS_OWNER_SYSTEM;
}

static uint bio_current_class(uint owner)
{
	struct thread *thread = curr_thread();

	if (io_policy.background.active)
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

void bio_account_transfer(uint owner, uint io_class, int write)
{
	struct thread *thread = curr_thread();
	struct io_owner_state *state;
	uint *transfers = 0;
	uint reservation = IO_RESERVATION_NONE;
	uint device_reservation = IO_RESERVATION_NONE;
	int unreserved = 1;
	int enabled = intr_save();

	state = io_state_find(owner, 0);
	if (state == 0) {
		// A lifecycle race must fail closed into the untrusted aggregate;
		// it must never borrow the protected SYSTEM reserve.
		state = io_state_find(FS_OWNER_PUBLIC, 0);
		owner = FS_OWNER_PUBLIC;
		io_class = IO_POLICY_CLASS_NORMAL;
	}
	if (state == 0) {
		intr_restore(enabled);
		return;
	}
	if (write)
		state->physical_writes++;
	else
		state->physical_reads++;
	state->completion_sequence = ++io_policy.completion_sequence;
	if (io_policy.background.active &&
	    io_policy.background.owner == owner &&
	    io_class == IO_POLICY_CLASS_BACKGROUND) {
		transfers = &io_policy.background.transfers;
		reservation = io_policy.background.reservation;
		device_reservation =
			io_policy.background.device_reservation;
		unreserved = 0;
	} else if (thread != 0 && thread->state == RUNNING &&
	    thread->io_request_depth != 0 &&
	    thread->io_request_owner == owner &&
	    thread->io_request_class == io_class) {
		transfers = &thread->io_request_transfers;
		reservation = thread->io_request_reservation;
		device_reservation =
			thread->io_request_device_reservation;
		unreserved = 0;
	}
	if (!io_policy.runtime_ready) {
		if (transfers != 0)
			(*transfers)++;
		intr_restore(enabled);
		return;
	}
	if (unreserved) {
		state->unreserved_transfers++;
		io_charge_credit(state, io_class);
		io_charge_device();
	} else {
		(*transfers)++;
		if (*transfers == 1) {
			if (reservation != IO_RESERVATION_NONE)
				io_commit_credit(state, io_class, reservation);
			else
				io_charge_credit(state, io_class);
			if (device_reservation != IO_RESERVATION_NONE)
				io_commit_device(device_reservation);
			else
				io_charge_device();
			intr_restore(enabled);
			return;
		}
		io_charge_credit(state, io_class);
		io_charge_device();
	}
	intr_restore(enabled);
}

static uint bio_cache_floor(uint owner)
{
	if (owner == FS_OWNER_SYSTEM)
		return IO_CACHE_SYSTEM_FLOOR;
	if (owner == FS_OWNER_PUBLIC)
		return IO_CACHE_PUBLIC_FLOOR;
	if (FS_OWNER_IS_SCOPE(owner) && io_scope_owner_live(owner))
		return IO_CACHE_WORKFLOW_FLOOR;
	if (FS_OWNER_IS_SCOPE(owner) && bio_cache_owner_retained(owner))
		return IO_CACHE_CLEANUP_FLOOR;
	return 0;
}

static uint bio_cache_cap(uint owner)
{
	if (owner == FS_OWNER_SYSTEM)
		return IO_CACHE_SYSTEM_CAP;
	if (owner == FS_OWNER_PUBLIC)
		return IO_CACHE_PUBLIC_CAP;
	if (FS_OWNER_IS_SCOPE(owner) && io_scope_owner_live(owner))
		return IO_CACHE_WORKFLOW_CAP;
	if (FS_OWNER_IS_SCOPE(owner) && bio_cache_owner_retained(owner))
		return IO_CACHE_CLEANUP_CAP;
	return 0;
}

static uint bio_cache_count(uint owner)
{
	uint count = 0;
	uint scanned = 0;

	for (struct buf *b = bcache.head.next; b != &bcache.head; b = b->next) {
		if (scanned++ >= NBUF)
			panic("buffer-cache list cycle");
		if (b->cache_owner == owner)
			count++;
	}
	return count;
}

static void bio_cache_invalidate(struct buf *b)
{
	if (b->refcnt != 0 || b->hold_depth != 0)
		panic("invalidate held buffer");
	b->valid = 0;
	b->dev = 0;
	b->blockno = 0;
	b->cache_owner = FS_OWNER_NONE;
	b->transient = 0;
	b->lru_promote = 0;
	b->background_reserved = 0;
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
	if (io_policy.background.active)
		return io_policy.background.owner;
	return bio_current_owner();
}

static void *bio_cache_holder_token(void)
{
	struct thread *thread;

	if (io_policy.background.active)
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
	else if (token != &bio_boot_holder_token)
		((struct thread *)token)->bio_buffer_holds++;
}

static void bio_cache_hold_release(void *token)
{
	uint *holds;

	if (token == &io_policy.background)
		holds = &io_policy.background.buffer_holds;
	else if (token != &bio_boot_holder_token)
		holds = &((struct thread *)token)->bio_buffer_holds;
	else
		return;
	if (*holds == 0)
		panic("buffer holder underflow");
	(*holds)--;
}

// Look through buffer cache for block on device dev. If not found, select an
// idle victim without crossing an active principal's guaranteed floor.
static struct buf *bget(uint dev, uint blockno)
{
	struct buf *b;
	struct buf *free_candidate;
	struct buf *donor_candidate;
	struct buf *own_candidate;
	struct buf *reserved_candidate;
	void *holder = bio_cache_holder_token();
	uint owner = bio_current_cache_owner();
	uint owner_count;
	uint owner_cap;
	uint scanned;

	retry:
	free_candidate = 0;
	donor_candidate = 0;
	own_candidate = 0;
	reserved_candidate = 0;
	scanned = 0;
	for (b = bcache.head.next; b != &bcache.head; b = b->next) {
		if (scanned++ >= NBUF)
			panic("buffer-cache list cycle");
		if (b->dev == dev && b->blockno == blockno) {
			if (b->hold_depth != 0 && b->holder != holder) {
				if (io_policy.background.active)
					panic("background hit busy buffer");
				if (wait_queue_sleep_irq_uninterruptible(
					    &b->holder_waiters) != WAIT_QUEUE_OK)
					panic("buffer holder wait invariant");
				goto retry;
			}
			b->refcnt++;
			if (b->hold_depth == 0)
				b->holder = holder;
			b->hold_depth++;
			bio_cache_hold_acquire(holder);
			if (b->cache_owner == owner)
				b->lru_promote = 1;
			bio_cache_record(owner, 1, 0);
			return b;
		}
	}
	bio_cache_record(owner, 0, 0);
	owner_count = bio_cache_count(owner);
	owner_cap = bio_cache_cap(owner);
	scanned = 0;
	for (b = bcache.head.prev; b != &bcache.head; b = b->prev) {
		uint victim_count;
		uint victim_floor;

		if (scanned++ >= NBUF)
			panic("buffer-cache list cycle");
		if (b->refcnt != 0 || b->hold_depth != 0)
			continue;
		if (io_policy.background.active && b->background_reserved &&
		    b->cache_owner == owner) {
			if (reserved_candidate == 0)
				reserved_candidate = b;
			continue;
		}
		if (!b->valid || b->cache_owner == FS_OWNER_NONE ||
		    !bio_cache_owner_retained(b->cache_owner)) {
			if (free_candidate == 0)
				free_candidate = b;
			continue;
		}
		if (b->cache_owner == owner) {
			if (own_candidate == 0)
				own_candidate = b;
			continue;
		}
		victim_count = bio_cache_count(b->cache_owner);
		victim_floor = bio_cache_floor(b->cache_owner);
		if (victim_count > victim_floor && donor_candidate == 0)
			donor_candidate = b;
	}
	int transient = 0;
	b = 0;

	if (reserved_candidate != 0)
		b = reserved_candidate;
	else if (owner_count < owner_cap && free_candidate != 0)
		b = free_candidate;
	else if (owner_count < owner_cap && donor_candidate != 0)
		b = donor_candidate;
	else if (own_candidate != 0)
		b = own_candidate;
	else if (free_candidate != 0) {
		b = free_candidate;
		transient = 1;
	} else if (donor_candidate != 0) {
		b = donor_candidate;
		transient = 1;
	}
	if (b == 0) {
		if (io_policy.background.active)
			panic("background buffer-cache reservation invariant");
		if (wait_queue_sleep_irq_uninterruptible(&cache_waiters) !=
		    WAIT_QUEUE_OK)
			panic("buffer-cache wait invariant");
		goto retry;
	}
	if (b->valid)
		bio_cache_record(owner, -1, 1);
	b->dev = dev;
	b->blockno = blockno;
	b->valid = 0;
	b->refcnt = 1;
	b->cache_owner = owner;
	b->lru_promote = 1;
	b->transient = transient;
	b->background_reserved = 0;
	b->holder = holder;
	b->hold_depth = 1;
	bio_cache_hold_acquire(holder);
	return b;
}

void binit(void)
{
	struct buf *b;

	memset(&io_policy, 0, sizeof(io_policy));
	io_owner_init(&io_policy.owners[0], FS_OWNER_SYSTEM);
	io_owner_init(&io_policy.owners[1], FS_OWNER_PUBLIC);
	io_policy.shared.tokens = IO_POLICY_SHARED_BURST;
	io_policy.device.tokens = IO_POLICY_DEVICE_BURST;
	wait_queue_init(&io_policy.device.debt_queue,
			WAIT_REASON_IO_BUDGET);
	wait_queue_init(&cache_waiters, WAIT_REASON_BUFFER_CACHE);
	bcache.head.prev = &bcache.head;
	bcache.head.next = &bcache.head;
	for (b = bcache.buf; b < bcache.buf + NBUF; b++) {
		b->cache_owner = FS_OWNER_NONE;
		b->lru_promote = 0;
		b->transient = 0;
		b->background_reserved = 0;
		b->holder = 0;
		b->hold_depth = 0;
		wait_queue_init(&b->holder_waiters,
				WAIT_REASON_BUFFER_CACHE);
		b->next = bcache.head.next;
		b->prev = &bcache.head;
		bcache.head.next->prev = b;
		bcache.head.next = b;
	}
}

const int R = 0;
const int W = 1;

struct buf *bread(uint dev, uint blockno)
{
	struct buf *b = bget(dev, blockno);

	if (!b->valid) {
		virtio_disk_rw(b, R);
		b->valid = 1;
	}
	return b;
}

void bwrite(struct buf *b)
{
	if (b == 0 || b->hold_depth == 0 ||
	    b->holder != bio_cache_holder_token())
		panic("bwrite unlocked buffer");
	virtio_disk_rw(b, W);
}

// Reallocated data blocks start a new sponsorship lifetime. Metadata callers
// deliberately do not use this operation, so shared filesystem structures
// cannot be stolen from their current protected partition by a reader/writer.
void bclaim(struct buf *b)
{
	uint owner;

	if (b == 0 || b->refcnt == 0 || b->hold_depth == 0 ||
	    b->holder != bio_cache_holder_token())
		panic("bclaim invalid buffer");
	owner = bio_current_cache_owner();
	if (b->cache_owner != owner) {
		b->transient = bio_cache_count(owner) >= bio_cache_cap(owner);
		b->cache_owner = owner;
	}
	b->lru_promote = 1;
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
	}
	if (b->refcnt == 0) {
		if (b->transient ||
		    !bio_cache_owner_retained(b->cache_owner)) {
			bio_cache_invalidate(b);
		}
		if (b->lru_promote) {
			b->next->prev = b->prev;
			b->prev->next = b->next;
			b->next = bcache.head.next;
			b->prev = &bcache.head;
			bcache.head.next->prev = b;
			bcache.head.next = b;
		}
		b->lru_promote = 0;
		// Cache eligibility depends on the requester's sponsor and on donor
		// floors. Wake every waiter so an ineligible head cannot hide a
		// newly usable buffer from another owner.
		wait_queue_wake_all(&cache_waiters);
	}
}

void bpin(struct buf *b)
{
	if (b == 0)
		panic("bpin null");
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
	    (b->transient ||
	     !bio_cache_owner_retained(b->cache_owner))) {
		bio_cache_invalidate(b);
	}
	if (b->refcnt == 0)
		wait_queue_wake_all(&cache_waiters);
}

int bio_scope_acquire(uint scope_id)
{
	struct io_owner_state *state;
	uint owner;
	int enabled;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG)
		return -1;
	owner = FS_OWNER_SCOPE(scope_id);
	enabled = intr_save();
	io_owner_reap_retired();
	state = io_state_find(owner, 0);
	if (state != 0) {
		int valid = !state->retiring && !state->quiesced;

		intr_restore(enabled);
		return valid ? 0 : -1;
	}
	state = io_state_find(owner, 1);
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
					state->buckets[c].tokens = 0;
					wait_queue_wake_all(
						&state->buckets[c].admission_queue);
					wait_queue_wake_all(
						&state->buckets[c].debt_queue);
				}
			}
			break;
		}
	}
	for (uint i = 0; i < NBUF; i++)
		if (bcache.buf[i].cache_owner == owner &&
		    bcache.buf[i].refcnt == 0)
			bio_cache_invalidate(&bcache.buf[i]);
	wait_queue_wake_all(&cache_waiters);
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
		for (uint c = 0; c < IO_POLICY_CLASS_COUNT; c++) {
			wait_queue_wake_all(
				&state->buckets[c].admission_queue);
			wait_queue_wake_all(&state->buckets[c].debt_queue);
		}
		break;
	}
	for (uint i = 0; i < NBUF; i++) {
		if (bcache.buf[i].cache_owner == owner &&
		    bcache.buf[i].refcnt == 0) {
			bio_cache_invalidate(&bcache.buf[i]);
		}
	}
	wait_queue_wake_all(&cache_waiters);
	io_owner_reap_retired();
	intr_restore(enabled);
}

int bio_policy_snapshot(const struct proc *p, struct io_policy_info *info)
{
	struct io_owner_state *state;
	struct io_bucket *bucket;
	uint owner;
	uint io_class;
	int enabled;

	if (p == 0 || info == 0)
		return -1;
	owner = io_owner_from_proc(p);
	io_class = io_class_from_proc(p, owner);
	enabled = intr_save();
	state = io_state_find(owner, 0);
	if (state == 0) {
		intr_restore(enabled);
		return -1;
	}
	bucket = &state->buckets[io_class];
	memset(info, 0, sizeof(*info));
	info->version = IO_POLICY_VERSION;
	info->struct_size = sizeof(*info);
	info->owner = owner;
	info->io_class = io_class;
	info->tokens = bucket->tokens;
	info->debt = bucket->debt;
	info->waiters = bucket->admission_waiters + bucket->debt_waiters;
	info->cache_resident = bio_cache_count(owner);
	info->cache_floor = bio_cache_floor(owner);
	info->cache_cap = bio_cache_cap(owner);
	info->shared_tokens = io_policy.shared.tokens;
	info->leased = bucket->leased;
	info->shared_leased = io_policy.shared.leased;
	info->class_burst = io_bucket_burst(owner, io_class);
	info->class_refill = io_bucket_refill(owner, io_class);
	info->device_burst = IO_POLICY_DEVICE_BURST;
	info->device_refill = IO_POLICY_DEVICE_REFILL;
	info->device_tokens = io_policy.device.tokens;
	info->device_debt = io_policy.device.debt;
	info->device_leased = io_policy.device.leased;
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
	info->cache_hits = state->cache_hits;
	info->cache_misses = state->cache_misses;
	info->cache_evictions = state->cache_evictions;
	info->unreserved_transfers = state->unreserved_transfers;
	info->completion_sequence = state->completion_sequence;
	intr_restore(enabled);
	return 0;
}
