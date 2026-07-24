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
#define IO_RATE_GLOBAL_SHARED 0U
#define IO_RATE_GLOBAL_DEVICE 1U
#define IO_CACHE_CLEANUP_FLOOR 3U
#define IO_CACHE_CLEANUP_CAP 8U
_Static_assert(RESOURCE_RATE_LANE_CAP == IO_POLICY_CLASS_COUNT,
	       "rate lanes must match the exported I/O classes");
_Static_assert(RESOURCE_RATE_GLOBAL_CAP >= 2,
	       "I/O policy needs shared and device rate pools");
_Static_assert(RESOURCE_RATE_LEASE_CAP >= NPROC * NTHREAD + 1,
	       "every live thread plus background cleanup needs a rate lease");
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
	struct resource_account_handle principal;
	int principal_member;
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

struct io_device_wait_state {
	uint debt_waiters;
	struct wait_queue debt_queue;
};

static struct {
	struct io_owner_state owners[IO_OWNER_SLOTS];
	struct io_device_wait_state device;
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
static int bio_cache_assign(struct buf *, uint, int, int);
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

static void io_owner_init(struct io_owner_state *state, uint owner,
			  struct resource_account_handle principal,
			  int principal_member)
{
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
			if (!state->principal_member ||
			    resource_account_member_release(
				    state->principal, 0) < 0)
				panic("I/O principal member release");
			memset(state, 0, sizeof(*state));
		}
	}
	intr_restore(enabled);
}

static int io_scope_owner_live(uint owner)
{
	if (!FS_OWNER_IS_SCOPE(owner))
		return owner == FS_OWNER_SYSTEM || owner == FS_OWNER_PUBLIC;
	for (uint i = 0; i < IO_OWNER_SLOTS; i++) {
		struct io_owner_state *state = &io_policy.owners[i];

		if (state->used && state->owner == owner)
			return state->cache_live && !state->retiring &&
			       resource_account_state_get(state->principal) ==
				       RESOURCE_ACCOUNT_ACTIVE;
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

static struct resource_rate_lease_handle
io_rate_lease(uint slot, uint generation)
{
	struct resource_rate_lease_handle lease = {
		.slot = slot,
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

static uint io_rate_bundle(
	const struct io_owner_state *state, uint io_class, int shared,
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
	endpoints[0].amount = 1;
	endpoints[1].scope = RESOURCE_RATE_GLOBAL;
	endpoints[1].index = IO_RATE_GLOBAL_DEVICE;
	endpoints[1].amount = 1;
	if (io_device_protected(state->owner, io_class))
		endpoints[1].flags =
			RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;
	return 2;
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
	struct resource_rate_lease_handle lease,
	uint *slot, uint *generation)
{
	*slot = lease.slot;
	*generation = lease.generation;
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

static void io_rate_charge_transfer(
	struct io_owner_state *state, uint io_class)
{
	struct resource_rate_endpoint
		endpoints[RESOURCE_RATE_BUNDLE_CAP];
	struct resource_rate_snapshot lane;

	(void)io_rate_bundle(state, io_class, 0, endpoints);
	endpoints[0].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;
	endpoints[1].flags |= RESOURCE_RATE_ENDPOINT_ALLOW_DEBT;
	if (io_rate_snapshot(state, io_class, &lane) == 0 &&
	    lane.debt == 0 && lane.tokens != 0)
		state->reserved_grants++;
	else
		state->throttles++;
	if (resource_rate_charge_many(endpoints, 2) < 0)
		panic("I/O rate charge");
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
		io_rate_lease_store(lease, source, device_source);
		return 1;
	}
	if (io_rate_snapshot(state, io_class, &lane) == 0 &&
	    lane.debt == 0 && io_class != IO_POLICY_CLASS_BACKGROUND &&
	    !io_any_admission_waiters()) {
		(void)io_rate_bundle(state, io_class, 1, endpoints);
		if (resource_rate_reserve_many(
			    endpoints, 2, &lease) == 0) {
			state->shared_grants++;
			io_rate_lease_store(
				lease, source, device_source);
			return 1;
		}
	}
	return 0;
}

static int io_grant_waiter(struct io_owner_state *state, uint io_class,
			   int shared)
{
	struct io_bucket *bucket = &state->buckets[io_class];
	struct thread *head;
	struct resource_rate_endpoint
		endpoints[RESOURCE_RATE_BUNDLE_CAP];
	struct resource_rate_lease_handle lease =
		resource_rate_lease_none();
	struct resource_rate_snapshot lane;

	if (state->retiring ||
	    (state->quiesced && io_class != IO_POLICY_CLASS_BACKGROUND) ||
	    bucket->grantee != 0 ||
	    (head = bucket->admission_queue.head) == 0)
		return 0;
	if (shared) {
		if (io_class == IO_POLICY_CLASS_BACKGROUND ||
		    io_rate_snapshot(state, io_class, &lane) < 0 ||
		    lane.debt != 0)
			return 0;
	}
	(void)io_rate_bundle(state, io_class, shared, endpoints);
	if (resource_rate_reserve_many(endpoints, 2, &lease) < 0)
		return 0;
	if (shared)
		state->shared_grants++;
	else
		state->reserved_grants++;
	bucket->grantee = head;
	io_rate_lease_store(
		lease, &bucket->grant_source,
		&bucket->grant_device_source);
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
			(void)io_grant_waiter(state, c, 0);
	}
	while (slots != 0) {
		struct resource_rate_snapshot shared;
		int granted = 0;

		if (io_rate_global_snapshot(
			    IO_RATE_GLOBAL_SHARED, &shared) < 0 ||
		    shared.tokens == 0 || shared.debt != 0)
			break;
		for (uint scanned = 0; scanned < slots; scanned++) {
			uint index = (io_policy.shared_cursor + scanned) % slots;
			uint owner_slot = index / IO_POLICY_CLASS_COUNT;
			uint io_class = index % IO_POLICY_CLASS_COUNT;
			struct io_owner_state *state =
				&io_policy.owners[owner_slot];

			if (!state->used || state->retiring ||
			    io_class == IO_POLICY_CLASS_BACKGROUND)
				continue;
			if (io_grant_waiter(state, io_class, 1)) {
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

	for (;;) {
		struct resource_rate_snapshot snapshot;

		if (io_rate_snapshot(
			    state, io_class, &snapshot) < 0) {
			intr_restore(enabled);
			return -1;
		}
		if (snapshot.debt == 0)
			break;
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
	for (;;) {
		struct resource_rate_snapshot snapshot;

		if (io_rate_global_snapshot(
			    IO_RATE_GLOBAL_DEVICE, &snapshot) < 0) {
			intr_restore(enabled);
			return -1;
		}
		if (snapshot.debt == 0)
			break;
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
	struct resource_rate_snapshot snapshot;

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
		if (source != IO_RESERVATION_NONE)
			io_rate_lease_refund(source, device_source);
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
		struct resource_rate_snapshot lane;
		struct resource_rate_snapshot device;
		int ready = state != 0 &&
			io_rate_snapshot(
				state, IO_POLICY_CLASS_BACKGROUND,
				&lane) == 0 &&
			lane.debt == 0 &&
			(io_device_protected(io_policy.background.owner,
					     IO_POLICY_CLASS_BACKGROUND) ||
			 (io_rate_global_snapshot(
				  IO_RATE_GLOBAL_DEVICE,
				  &device) == 0 &&
			  device.debt == 0));

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

		if (source != IO_RESERVATION_NONE)
			io_rate_lease_refund(source, device_source);
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
		if (thread->io_request_transfers == 0 &&
		    thread->io_request_reservation !=
			    IO_RESERVATION_NONE)
			io_rate_lease_refund(
				thread->io_request_reservation,
				thread->io_request_device_reservation);
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
		if (!bio_cache_assign(candidate, owner, 1, 1)) {
			bio_background_release_buffers(owner);
			return 0;
		}
		candidate->valid = 0;
		candidate->dev = 0;
		candidate->blockno = 0;
		candidate->lru_promote = 0;
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
			lease, &source, &device_source);
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
		if (io_policy.background.reservation != IO_RESERVATION_NONE)
			io_rate_lease_refund(
				io_policy.background.reservation,
				io_policy.background.device_reservation);
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
		io_rate_charge_transfer(state, io_class);
	} else {
		(*transfers)++;
		if (*transfers == 1) {
			if (reservation != IO_RESERVATION_NONE)
				io_rate_lease_commit(
					reservation,
					device_reservation);
			else
				io_rate_charge_transfer(
					state, io_class);
			intr_restore(enabled);
			return;
		}
		io_rate_charge_transfer(state, io_class);
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
	struct io_owner_state *state = io_state_find(owner, 0);
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
	if (!bio_cache_assign(
		    b, owner, !transient,
		    io_policy.background.active)) {
		if (io_policy.background.active)
			panic("background buffer-cache controller admission");
		(void)bio_cache_assign(b, owner, 0, 0);
		transient = 1;
	}
	b->dev = dev;
	b->blockno = blockno;
	b->valid = 0;
	b->refcnt = 1;
	b->lru_promote = 1;
	if (b->transient != transient)
		panic("buffer-cache charge mode mismatch");
	b->background_reserved = 0;
	b->holder = holder;
	b->hold_depth = 1;
	bio_cache_hold_acquire(holder);
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
	bcache.head.prev = &bcache.head;
	bcache.head.next = &bcache.head;
	for (b = bcache.buf; b < bcache.buf + NBUF; b++) {
		b->cache_owner = FS_OWNER_NONE;
		b->cache_principal = resource_account_none();
		b->cache_charge_class = RESOURCE_CHARGE_ORDINARY;
		b->cache_charged = 0;
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
		int stable =
			bio_cache_count(owner) < bio_cache_cap(owner);

		if (!bio_cache_assign(
			    b, owner, stable,
			    io_policy.background.active)) {
			if (io_policy.background.active)
				panic("background cache claim admission");
			(void)bio_cache_assign(b, owner, 0, 0);
		}
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
		io_owner_reap_retired();
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
	if (b->refcnt == 0) {
		wait_queue_wake_all(&cache_waiters);
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
	struct resource_rate_snapshot lane;
	struct resource_rate_snapshot shared;
	struct resource_rate_snapshot device;
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
	info->cache_hits = state->cache_hits;
	info->cache_misses = state->cache_misses;
	info->cache_evictions = state->cache_evictions;
	info->unreserved_transfers = state->unreserved_transfers;
	info->completion_sequence = state->completion_sequence;
	intr_restore(enabled);
	return 0;
}
