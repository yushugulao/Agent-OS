#include "resource_controller.h"
#include "defs.h"
#include "proc.h"
#include "riscv.h"
#include "workflow_credit_domain.h"

struct resource_policy {
	uint64 capacity;
	uint64 ordinary_limit;
	uint64 reserved_limit;
	uint64 held;
	uint64 ordinary_held;
	uint64 reserved_held;
	uint64 free[RESOURCE_CHARGE_CLASS_COUNT];
	uint64 reserved_promised;
	int configured;
	int reserved_guaranteed;
};
struct resource_account {
	enum resource_account_state state;
	enum resource_account_kind kind;
	uint charge_grants;
	uint64 external_id;
	uint64 generation;
	uint members;
	uint free_mask;
	struct resource_account_limits limits;
	struct workflow_credit_domain credit;
};
#define RESOURCE_PHASE_ACCOUNT_LOCK_CAP \
	(WORKFLOW_LIFECYCLE_CAP * RESOURCE_PHASE_ACCOUNT_COUNT)
struct resource_phase_account_lock {
	struct resource_account *account;
	struct resource_account_handle handle;
	uint refs;
	uint64 locked[RESOURCE_CHARGE_CLASS_COUNT][RESOURCE_KIND_COUNT];
	uint64 claimed[RESOURCE_CHARGE_CLASS_COUNT][RESOURCE_KIND_COUNT];
};
struct resource_phase_lease_account_record {
	struct resource_account_handle handle;
	uint kind_mask;
	uint remaining[RESOURCE_KIND_COUNT];
};
struct resource_phase_lease_record {
	struct workflow_lifecycle_key lifecycle;
	struct resource_phase_lease_account_record
		account[RESOURCE_PHASE_ACCOUNT_COUNT];
	uint64 generation;
	uint64 request_id;
	uint64 owner_thread_generation;
	uint node_id;
	enum resource_charge_class charge_class;
	uint outstanding_claims;
	enum resource_phase_lease_state state;
};
struct resource_phase_claim_record {
	uint64 lease_generation;
	uint64 nonce;
	uint amounts[RESOURCE_KIND_COUNT];
	uint lease_slot;
	uint kind_mask;
	uint role;
	int active;
};
struct resource_phase_registry_entry {
	struct resource_phase_lease_record lease;
	struct thread *owner;
	int used;
};
static struct resource_policy resource_policies[RESOURCE_KIND_COUNT];
static struct resource_account resource_accounts[RESOURCE_ACCOUNT_CAP];
static struct resource_phase_account_lock
	resource_phase_account_locks[RESOURCE_PHASE_ACCOUNT_LOCK_CAP];
static signed char resource_phase_lock_by_account[RESOURCE_ACCOUNT_CAP];
static struct resource_phase_registry_entry
	resource_phase_registry[RESOURCE_PHASE_LEASE_CAP];
static struct resource_phase_claim_record
	resource_phase_claims[RESOURCE_PHASE_MAX_CLAIMS];
static uint64 resource_account_generations[RESOURCE_ACCOUNT_CAP];
static uchar resource_account_generation_exhausted[RESOURCE_ACCOUNT_CAP];
static uint64 resource_credit_epoch;
static uint64 resource_phase_generation;
static uint64 resource_phase_claim_generation;
_Static_assert(RESOURCE_PHASE_ACCOUNT_LOCK_CAP <= 127,
	       "resource phase lock index width");
_Static_assert(sizeof(resource_phase_registry) +
	       sizeof(resource_phase_claims) +
	       sizeof(resource_phase_account_locks) +
	       sizeof(resource_phase_lock_by_account) <= 41U * 1024U,
	       "resource phase fixed-state budget");
static int resource_phase_claim_vector_locked(
	struct resource_account_handle, enum resource_charge_class,
	const uint64 [RESOURCE_KIND_COUNT], uint, int, uint64 *, uint64 *);
static int resource_phase_publish_vector_locked(
	struct resource_account_handle, enum resource_charge_class,
	const uint64 [RESOURCE_KIND_COUNT], uint, uint64, uint64);
static int resource_phase_refund_vector_locked(
	struct resource_account_handle, enum resource_charge_class,
	const uint64 [RESOURCE_KIND_COUNT], uint, uint64, uint64);
static uint resource_phase_claim_count_locked(
	const struct resource_phase_registry_entry *);
static const uint resource_kind_attribute_table[RESOURCE_KIND_COUNT] = {
	[RESOURCE_PROCESS] = RESOURCE_KIND_COUNT_TRANSFERABLE,
	[RESOURCE_THREAD] = RESOURCE_KIND_COUNT_TRANSFERABLE,
	[RESOURCE_FILE_OBJECT] = RESOURCE_KIND_COUNT_TRANSFERABLE,
	[RESOURCE_FS_BLOCK] = RESOURCE_KIND_COUNT_TRANSFERABLE,
	[RESOURCE_FS_INODE] = RESOURCE_KIND_COUNT_TRANSFERABLE,
	[RESOURCE_BUFFER_CACHE] = RESOURCE_KIND_COUNT_TRANSFERABLE,
	[RESOURCE_AGENT_STATE_PAGE] = RESOURCE_KIND_COUNT_TRANSFERABLE,
	[RESOURCE_PHYSICAL_PAGE] = RESOURCE_KIND_POOL_AFFINE,
};
_Static_assert(RESOURCE_KIND_COUNT <= 8, "resource kind mask scan width");
_Static_assert(RESOURCE_CHARGE_CLASS_COUNT == 2,
	       "resource pressure scan expects two charge classes");
_Static_assert(RESOURCE_CHARGE_CLASS_COUNT * RESOURCE_KIND_COUNT <= 32,
	       "resource free mask width");
static int resource_kind_valid(enum resource_kind kind)
{
	return kind >= RESOURCE_PROCESS && kind < RESOURCE_KIND_COUNT;
}
uint resource_kind_attributes(enum resource_kind kind)
{
	return resource_kind_valid(kind) ? resource_kind_attribute_table[kind] : 0;
}
static uint resource_kind_first(uint kind_mask)
{
	uint kind = 0;

	if ((kind_mask & 0xfU) == 0) {
		kind += 4;
		kind_mask >>= 4;
	}
	if ((kind_mask & 0x3U) == 0) {
		kind += 2;
		kind_mask >>= 2;
	}
	return kind + ((kind_mask & 1U) == 0);
}

static int resource_count_only_mutation_allowed(uint kind_mask)
{
	while (kind_mask != 0) {
		uint kind = resource_kind_first(kind_mask);

		kind_mask &= kind_mask - 1;
		if (!(resource_kind_attribute_table[kind] &
		      RESOURCE_KIND_COUNT_TRANSFERABLE))
			return 0;
	}
	return 1;
}
static int resource_charge_class_valid(enum resource_charge_class charge_class)
{
	return charge_class >= RESOURCE_CHARGE_ORDINARY &&
	       charge_class < RESOURCE_CHARGE_CLASS_COUNT;
}
static int resource_u64_add(uint64 left, uint64 right, uint64 *sum)
{
	if (sum == 0 || right > RESOURCE_LIMIT_UNBOUNDED - left)
		return -1;
	*sum = left + right;
	return 0;
}

static void resource_credit_changed(void)
{
	if (resource_credit_epoch == RESOURCE_LIMIT_UNBOUNDED)
		panic("resource credit epoch exhausted");
	resource_credit_epoch++;
}

static struct workflow_credit_counter *
resource_account_counter(struct resource_account *account,
			 enum resource_charge_class charge_class,
			 enum resource_kind kind)
{
	return &account->credit.counter[charge_class][kind];
}

static const struct workflow_credit_counter *
resource_account_counter_const(const struct resource_account *account,
			       enum resource_charge_class charge_class,
			       enum resource_kind kind)
{
	return &account->credit.counter[charge_class][kind];
}

static uint64 *resource_policy_class_held(
	struct resource_policy *policy,
	enum resource_charge_class charge_class)
{
	return charge_class == RESOURCE_CHARGE_ORDINARY ?
		&policy->ordinary_held : &policy->reserved_held;
}

static uint64 resource_policy_class_limit(
	const struct resource_policy *policy,
	enum resource_charge_class charge_class)
{
	return charge_class == RESOURCE_CHARGE_ORDINARY ?
		policy->ordinary_limit : policy->reserved_limit;
}

static uint64 resource_credit_refill_quantum(enum resource_kind kind)
{
	static const uchar quanta[RESOURCE_KIND_COUNT] = {
		[RESOURCE_PROCESS] = 2,
		[RESOURCE_THREAD] = 4,
		[RESOURCE_FILE_OBJECT] = 32,
		[RESOURCE_FS_BLOCK] = 16,
		[RESOURCE_FS_INODE] = 4,
		[RESOURCE_BUFFER_CACHE] = 8,
		[RESOURCE_AGENT_STATE_PAGE] = 1,
		[RESOURCE_PHYSICAL_PAGE] = 16,
	};

	return quanta[kind];
}

static uint resource_credit_free_bit(
	enum resource_charge_class charge_class, enum resource_kind kind)
{
	return 1U << ((uint)charge_class * RESOURCE_KIND_COUNT + (uint)kind);
}

static __attribute__((noreturn, noinline, optimize("Os"))) void
resource_controller_invariant(const char *message)
{
	static const char prefix[] = "PANIC resource_controller: ";

	for (const char *p = prefix; *p != 0; p++)
		console_putchar(*p);
	for (const char *p = message; *p != 0; p++)
		console_putchar(*p);
	console_putchar('\n');
	shutdown();
	__builtin_unreachable();
}

static void resource_credit_free_add(
	struct resource_account *account,
	enum resource_charge_class charge_class, enum resource_kind kind,
	uint64 amount)
{
	struct workflow_credit_counter *counter =
		resource_account_counter(account, charge_class, kind);
	struct resource_policy *policy = &resource_policies[kind];

	if (amount > (uint)-1 - counter->free)
		resource_controller_invariant("resource free credit overflow");
	if (amount > RESOURCE_LIMIT_UNBOUNDED - policy->free[charge_class])
		resource_controller_invariant(
			"resource global free credit overflow");
	counter->free += amount;
	policy->free[charge_class] += amount;
	if (amount != 0)
		account->free_mask |=
			resource_credit_free_bit(charge_class, kind);
}

static void resource_credit_free_take(
	struct resource_account *account,
	enum resource_charge_class charge_class, enum resource_kind kind,
	uint64 amount)
{
	struct workflow_credit_counter *counter =
		resource_account_counter(account, charge_class, kind);
	struct resource_policy *policy = &resource_policies[kind];

	if (counter->free < amount || policy->free[charge_class] < amount)
		resource_controller_invariant("resource free credit underflow");
	counter->free -= amount;
	policy->free[charge_class] -= amount;
	if (counter->free == 0)
		account->free_mask &=
			~resource_credit_free_bit(charge_class, kind);
}
struct resource_account_handle resource_account_none(void)
{
	struct resource_account_handle handle = {
		.slot = (uint)-1,
		.generation = 0,
	};
	return handle;
}
static struct resource_account *
resource_account_lookup(struct resource_account_handle handle)
{
	struct resource_account *account;

	if (handle.slot >= RESOURCE_ACCOUNT_CAP || handle.generation == 0)
		return 0;
	account = &resource_accounts[handle.slot];
	if (account->state == RESOURCE_ACCOUNT_FREE ||
	    account->generation != handle.generation)
		return 0;
	return account;
}
static struct resource_phase_account_lock *
resource_phase_account_lock_lookup(struct resource_account_handle handle)
{
	int slot;
	struct resource_phase_account_lock *lock;

	if (handle.slot >= RESOURCE_ACCOUNT_CAP)
		return 0;
	slot = resource_phase_lock_by_account[handle.slot];
	if (slot < 0 || slot >= RESOURCE_PHASE_ACCOUNT_LOCK_CAP)
		return 0;
	lock = &resource_phase_account_locks[slot];
	return lock->refs != 0 &&
	       resource_account_handle_equal(lock->handle, handle) ? lock : 0;
}

static struct resource_phase_account_lock *
resource_phase_account_lock_for_account(const struct resource_account *account)
{
	uint account_slot;
	int lock_slot;
	struct resource_phase_account_lock *lock;

	if (account < resource_accounts ||
	    account >= &resource_accounts[RESOURCE_ACCOUNT_CAP])
		return 0;
	account_slot = (uint)(account - resource_accounts);
	lock_slot = resource_phase_lock_by_account[account_slot];
	if (lock_slot < 0 || lock_slot >= RESOURCE_PHASE_ACCOUNT_LOCK_CAP)
		return 0;
	lock = &resource_phase_account_locks[lock_slot];
	return lock->refs != 0 && lock->account == account ? lock : 0;
}

static uint64 resource_phase_locked_amount(
	const struct resource_account *account,
	enum resource_charge_class charge_class, enum resource_kind kind)
{
	struct resource_phase_account_lock *lock =
		resource_phase_account_lock_for_account(account);

	return lock == 0 ? 0 :
		lock->locked[charge_class][kind] +
		lock->claimed[charge_class][kind];
}

static int resource_phase_account_lock_empty(
	const struct resource_phase_account_lock *lock)
{
	for (uint charge_class = 0;
	     charge_class < RESOURCE_CHARGE_CLASS_COUNT; charge_class++)
		for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
			if (lock->locked[charge_class][kind] != 0 ||
			    lock->claimed[charge_class][kind] != 0)
				return 0;
	return 1;
}

static struct resource_phase_registry_entry *
resource_phase_registry_lookup(struct resource_phase_lease *lease,
			       uint64 expected_generation)
{
	struct resource_phase_registry_entry *entry;

	if (lease == 0 || lease->registry_slot >= RESOURCE_PHASE_LEASE_CAP ||
	    expected_generation == 0)
		return 0;
	entry = &resource_phase_registry[lease->registry_slot];
	if (!entry->used ||
	    entry->lease.generation != expected_generation ||
	    lease->generation != expected_generation)
		return 0;
	return entry;
}

static int resource_phase_registry_free_slot(void)
{
	for (uint i = 0; i < RESOURCE_PHASE_LEASE_CAP; i++)
		if (!resource_phase_registry[i].used)
			return (int)i;
	return -1;
}

static int resource_phase_registry_owner_busy(struct thread *owner)
{
	for (uint i = 0; i < RESOURCE_PHASE_LEASE_CAP; i++)
		if (resource_phase_registry[i].used &&
		    resource_phase_registry[i].owner == owner)
			return 1;
	return 0;
}

static int resource_phase_lifecycle_pair_admissible(
	struct workflow_lifecycle_key lifecycle,
	const struct resource_account_handle
		handles[RESOURCE_PHASE_ACCOUNT_COUNT])
{
	uint leases = 0;

	for (uint i = 0; i < RESOURCE_PHASE_LEASE_CAP; i++) {
		const struct resource_phase_registry_entry *entry =
			&resource_phase_registry[i];

		if (!entry->used ||
		    !workflow_lifecycle_key_equal(
			    entry->lease.lifecycle, lifecycle))
			continue;
		leases++;
		for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT; role++)
			if (!resource_account_handle_equal(
				    entry->lease.account[role].handle,
				    handles[role]))
				return 0;
	}
	return leases < RESOURCE_PHASE_MAX_LEASES_PER_LIFECYCLE;
}

static int resource_account_empty(const struct resource_account *account)
{
	return resource_phase_account_lock_for_account(account) == 0 &&
	       workflow_credit_domain_empty(&account->credit);
}

static int resource_account_has_phase_credit(
	const struct resource_account *account)
{
	return resource_phase_account_lock_for_account(account) != 0;
}
static int resource_promises_replace(
	const struct resource_account *old_account, uint new_grants,
	const struct resource_account_limits *new_limits, int publish)
{
	for (int pass = 0; pass <= publish; pass++) {
		for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
			struct resource_policy *policy = &resource_policies[kind];
			uint64 old_limit = 0, new_limit = 0, promised;
			if (!policy->reserved_guaranteed)
				continue;
			if (old_account != 0 &&
			    (old_account->charge_grants &
			     RESOURCE_CHARGE_GRANT(RESOURCE_CHARGE_RESERVED)))
				old_limit = old_account->limits.class_limit
					[RESOURCE_CHARGE_RESERVED][kind];
			if (new_grants & RESOURCE_CHARGE_GRANT(
					 RESOURCE_CHARGE_RESERVED))
				new_limit = new_limits->class_limit
					[RESOURCE_CHARGE_RESERVED][kind];
			if (policy->reserved_promised < old_limit)
				resource_controller_invariant(
					"reserved promise underflow");
			promised = policy->reserved_promised - old_limit;
			if (resource_u64_add(promised, new_limit, &promised) < 0 ||
			    promised > policy->reserved_limit)
				return 0;
			if (pass != 0)
				policy->reserved_promised = promised;
		}
	}
	return 1;
}

static void resource_credit_trim_counter_amount(
	struct resource_account *account,
	enum resource_charge_class charge_class, enum resource_kind kind,
	uint64 amount)
{
	struct workflow_credit_counter *counter =
		resource_account_counter(account, charge_class, kind);
	struct resource_policy *policy = &resource_policies[kind];
	uint64 *class_held =
		resource_policy_class_held(policy, charge_class);

	if (amount == 0)
		return;
	if (counter->free < amount || policy->held < amount ||
	    *class_held < amount)
		resource_controller_invariant("resource credit trim underflow");
	resource_credit_free_take(account, charge_class, kind, amount);
	policy->held -= amount;
	*class_held -= amount;
}

static void resource_credit_trim_counter(
	struct resource_account *account,
	enum resource_charge_class charge_class, enum resource_kind kind)
{
	struct workflow_credit_counter *counter =
		resource_account_counter(account, charge_class, kind);
	struct resource_policy *policy = &resource_policies[kind];
	uint64 *class_held =
		resource_policy_class_held(policy, charge_class);
	uint64 amount = counter->free;
	uint bit = resource_credit_free_bit(charge_class, kind);

	if (amount == 0) {
		account->free_mask &= ~bit;
		return;
	}
	if (policy->held < amount || *class_held < amount)
		resource_controller_invariant("resource credit trim underflow");
	resource_credit_free_take(account, charge_class, kind, amount);
	policy->held -= amount;
	*class_held -= amount;
}

static void resource_account_trim_locked(struct resource_account *account)
{
	for (uint charge_class = 0;
	     charge_class < RESOURCE_CHARGE_CLASS_COUNT; charge_class++)
		for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
			resource_credit_trim_counter(
				account,
				(enum resource_charge_class)charge_class,
				(enum resource_kind)kind);
}

static void resource_account_trim_cached_locked(
	struct resource_account *account)
{
	while (account->free_mask != 0) {
		uint kind_mask = account->free_mask &
			((1U << RESOURCE_KIND_COUNT) - 1);
		enum resource_charge_class charge_class =
			RESOURCE_CHARGE_ORDINARY;
		enum resource_kind kind;

		if (kind_mask == 0) {
			charge_class = RESOURCE_CHARGE_RESERVED;
			kind_mask = account->free_mask >> RESOURCE_KIND_COUNT;
		}
		kind = (enum resource_kind)resource_kind_first(kind_mask);

		resource_credit_trim_counter(account, charge_class, kind);
	}
}

static void resource_account_advance(struct resource_account *account);

static int resource_credit_refill_available(
	const struct resource_policy *policy,
	enum resource_charge_class charge_class, uint64 amount)
{
	uint64 class_held = charge_class == RESOURCE_CHARGE_ORDINARY ?
		policy->ordinary_held : policy->reserved_held;
	uint64 class_limit = resource_policy_class_limit(policy, charge_class);

	return policy->held <= policy->capacity &&
	       class_held <= class_limit &&
	       amount <= policy->capacity - policy->held &&
	       amount <= class_limit - class_held;
}

static void resource_credit_reclaim_pressure_locked(
	struct resource_account *exclude,
	enum resource_charge_class target_class, enum resource_kind kind,
	uint64 amount)
{
	struct resource_policy *policy = &resource_policies[kind];

	/* Reclaim target-class credit first because it repairs both limits. */
	for (uint pass = 0; pass < RESOURCE_CHARGE_CLASS_COUNT; pass++) {
		enum resource_charge_class scanned_class = pass == 0 ?
			target_class :
			(enum resource_charge_class)(1U - (uint)target_class);

		for (uint i = 0; i < RESOURCE_ACCOUNT_CAP; i++) {
			struct resource_account *account = &resource_accounts[i];
			const struct workflow_credit_counter *counter;

			if (resource_credit_refill_available(
				    policy, target_class, amount))
				return;
			if (account->state == RESOURCE_ACCOUNT_FREE ||
			    (account == exclude && scanned_class == target_class))
				continue;
			counter = resource_account_counter_const(
				account, scanned_class, kind);
			if (counter->free == 0)
				continue;
			resource_credit_trim_counter(
				account, scanned_class, kind);
			if (account != exclude &&
			    account->state != RESOURCE_ACCOUNT_ACTIVE)
				resource_account_advance(account);
		}
	}
}

static void resource_credit_reclaim_class_pressure_locked(
	struct resource_account *exclude,
	enum resource_charge_class target_class, enum resource_kind kind,
	uint64 amount)
{
	struct resource_policy *policy = &resource_policies[kind];
	uint64 class_limit = resource_policy_class_limit(policy, target_class);

	for (uint i = 0; i < RESOURCE_ACCOUNT_CAP; i++) {
		struct resource_account *account = &resource_accounts[i];
		const struct workflow_credit_counter *counter;
		uint64 class_held =
			*resource_policy_class_held(policy, target_class);

		if (class_held <= class_limit && amount <= class_limit - class_held)
			return;
		if (account->state == RESOURCE_ACCOUNT_FREE || account == exclude)
			continue;
		counter = resource_account_counter_const(
			account, target_class, kind);
		if (counter->free == 0)
			continue;
		resource_credit_trim_counter(account, target_class, kind);
		if (account->state != RESOURCE_ACCOUNT_ACTIVE)
			resource_account_advance(account);
	}
}

static void resource_credit_reclaimable_locked(
	const struct resource_account *exclude, enum resource_kind kind,
	enum resource_charge_class charge_class, uint64 *total,
	uint64 *class_total)
{
	const struct resource_policy *policy = &resource_policies[kind];
	uint64 own = resource_account_counter_const(
		exclude, charge_class, kind)->free;

	if (policy->free[charge_class] < own)
		resource_controller_invariant(
			"resource reclaimable free invariant");
	*class_total = policy->free[charge_class] - own;
	*total = *class_total;
	for (uint scanned_class = 0;
	     scanned_class < RESOURCE_CHARGE_CLASS_COUNT; scanned_class++)
		if (scanned_class != (uint)charge_class)
			*total += policy->free[scanned_class];
}

static void resource_account_advance(struct resource_account *account)
{
	if (account->state == RESOURCE_ACCOUNT_ACTIVE)
		return;
	if (resource_account_has_phase_credit(account))
		return;
	resource_account_trim_locked(account);
	if (account->state == RESOURCE_ACCOUNT_CLOSING &&
	    account->members == 0)
		account->state = RESOURCE_ACCOUNT_DRAINING;
	if (account->state == RESOURCE_ACCOUNT_DRAINING &&
	    resource_account_empty(account)) {
		uint64 generation = account->generation;
		if (!resource_promises_replace(account, 0, &account->limits, 1))
			resource_controller_invariant("reserved promise release");
		memset(account, 0, sizeof(*account));
		account->generation = generation;
		account->state = RESOURCE_ACCOUNT_FREE;
	}
}
void resource_controller_init(void)
{
	memset(resource_policies, 0, sizeof(resource_policies));
	memset(resource_accounts, 0, sizeof(resource_accounts));
	memset(resource_phase_account_locks, 0,
	       sizeof(resource_phase_account_locks));
	memset(resource_phase_lock_by_account, -1,
	       sizeof(resource_phase_lock_by_account));
	memset(resource_phase_registry, 0,
	       sizeof(resource_phase_registry));
	memset(resource_phase_claims, 0, sizeof(resource_phase_claims));
	memset(resource_account_generations, 0,
	       sizeof(resource_account_generations));
	memset(resource_account_generation_exhausted, 0,
	       sizeof(resource_account_generation_exhausted));
	resource_credit_epoch = 1;
	resource_phase_generation = 0;
	resource_phase_claim_generation = 0;
}
int resource_policy_configure(enum resource_kind kind, uint64 capacity,
			      uint64 ordinary_limit,
			      uint64 reserved_limit)
{
	struct resource_policy *policy;
	int enabled;
	int result = -1;

	if (!resource_kind_valid(kind) || capacity == 0 ||
	    capacity > (uint)-1 ||
	    ordinary_limit > capacity || reserved_limit > capacity)
		return -1;
	enabled = intr_save();
	policy = &resource_policies[kind];
	if (policy->held != 0 &&
	    (!policy->configured || policy->capacity != capacity ||
	     policy->ordinary_limit != ordinary_limit ||
	     policy->reserved_limit != reserved_limit))
		goto out;
	if (policy->reserved_guaranteed &&
	    reserved_limit < policy->reserved_promised)
		goto out;
	policy->capacity = capacity;
	policy->ordinary_limit = ordinary_limit;
	policy->reserved_limit = reserved_limit;
	policy->configured = 1;
	result = 0;
out:
	intr_restore(enabled);
	return result;
}

int resource_policy_guarantee_reserved(enum resource_kind kind)
{
	struct resource_policy *policy;
	int enabled, result = -1;
	if (!resource_kind_valid(kind))
		return -1;
	enabled = intr_save();
	policy = &resource_policies[kind];
	if (!policy->configured || policy->reserved_limit == 0 ||
	    policy->reserved_guaranteed)
		goto out;
	/* 先建立保证，再允许账户认领。 */
	for (uint i = 0; i < RESOURCE_ACCOUNT_CAP; i++) {
		struct resource_account *account = &resource_accounts[i];
		if (account->state != RESOURCE_ACCOUNT_FREE &&
		    (account->charge_grants & RESOURCE_CHARGE_GRANT(
					      RESOURCE_CHARGE_RESERVED)) &&
		    account->limits.class_limit[RESOURCE_CHARGE_RESERVED][kind])
			goto out;
	}
	policy->reserved_guaranteed = 1;
	result = 0;
out:
	intr_restore(enabled);
	return result;
}

uint resource_policy_snapshot_all(struct resource_policy_snapshot *snapshots,
				  uint count)
{
	uint measured = 0;
	int enabled;

	if (snapshots == 0 || count < RESOURCE_KIND_COUNT)
		return 0;
	memset(snapshots, 0, sizeof(*snapshots) * count);
	enabled = intr_save();
	/* Exact snapshot is the rstat-style aggregation point. */
	for (uint i = 0; i < RESOURCE_ACCOUNT_CAP; i++) {
		struct resource_account *account = &resource_accounts[i];

		if (account->state == RESOURCE_ACCOUNT_FREE)
			continue;
		resource_account_trim_locked(account);
		for (uint charge_class = 0;
		     charge_class < RESOURCE_CHARGE_CLASS_COUNT;
		     charge_class++)
			for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
				const struct workflow_credit_counter *counter =
					resource_account_counter_const(
						account,
						(enum resource_charge_class)
							charge_class,
						(enum resource_kind)kind);
				struct resource_policy_snapshot *snapshot =
					&snapshots[kind];

				snapshot->used += counter->used;
				snapshot->pending += counter->pending;
				if (charge_class ==
				    RESOURCE_CHARGE_ORDINARY) {
					snapshot->ordinary_used += counter->used;
					snapshot->ordinary_pending +=
						counter->pending;
				} else {
					snapshot->reserved_used += counter->used;
					snapshot->reserved_pending +=
						counter->pending;
				}
			}
	}
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		const struct resource_policy *policy = &resource_policies[kind];
		struct resource_policy_snapshot *snapshot = &snapshots[kind];

		if (!policy->configured)
			continue;
		snapshot->capacity = policy->capacity;
		if (policy->free[RESOURCE_CHARGE_ORDINARY] != 0 ||
		    policy->free[RESOURCE_CHARGE_RESERVED] != 0 ||
		    policy->held != snapshot->used + snapshot->pending ||
		    policy->ordinary_held !=
			    snapshot->ordinary_used +
				    snapshot->ordinary_pending ||
		    policy->reserved_held !=
			    snapshot->reserved_used +
				    snapshot->reserved_pending)
			panic("resource exact snapshot invariant");
		measured |= 1U << kind;
	}
	intr_restore(enabled);
	return measured;
}
#ifdef PHYSICAL_PAGE_TEST_HOOKS
int resource_policy_reserved_snapshot(enum resource_kind kind,
				      uint64 *promised, uint64 *limit)
{
	int enabled;
	if (!resource_kind_valid(kind) || promised == 0 || limit == 0)
		return -1;
	enabled = intr_save();
	if (!resource_policies[kind].configured ||
	    !resource_policies[kind].reserved_guaranteed) {
		intr_restore(enabled);
		return -1;
	}
	*promised = resource_policies[kind].reserved_promised;
	*limit = resource_policies[kind].reserved_limit;
	intr_restore(enabled);
	return 0;
}
#endif

int resource_account_handle_valid(struct resource_account_handle handle)
{
	int enabled = intr_save();
	int valid = resource_account_lookup(handle) != 0;

	intr_restore(enabled);
	return valid;
}

int resource_account_handle_equal(struct resource_account_handle left,
				  struct resource_account_handle right)
{
	return left.slot == right.slot &&
	       left.generation == right.generation;
}

int resource_account_promise_admissible(
	uint charge_grants, const struct resource_account_limits *limits)
{
	int admissible;
	int enabled;

	if (limits == 0 || charge_grants == 0 ||
	    (charge_grants &
	     ~(RESOURCE_CHARGE_GRANT(RESOURCE_CHARGE_ORDINARY) |
	       RESOURCE_CHARGE_GRANT(RESOURCE_CHARGE_RESERVED))) != 0)
		return 0;
	enabled = intr_save();
	/* Dry-run the same complete promise vector used by account creation. */
	admissible = resource_promises_replace(0, charge_grants, limits, 0);
	intr_restore(enabled);
	return admissible;
}

int resource_account_create(enum resource_account_kind kind,
			    uint64 external_id,
			    uint charge_grants,
			    const struct resource_account_limits *limits,
			    struct resource_account_handle *out)
{
	int enabled;
	int free_slot = -1;

	if (out == 0 || limits == 0 ||
	    (kind != RESOURCE_ACCOUNT_EXEC &&
	     kind != RESOURCE_ACCOUNT_STORAGE &&
	     kind != RESOURCE_ACCOUNT_CACHE) ||
	    charge_grants == 0 ||
	    (charge_grants &
	     ~(RESOURCE_CHARGE_GRANT(RESOURCE_CHARGE_ORDINARY) |
	       RESOURCE_CHARGE_GRANT(RESOURCE_CHARGE_RESERVED))) != 0)
		return -1;
	*out = resource_account_none();
	enabled = intr_save();
	for (uint i = 0; i < RESOURCE_ACCOUNT_CAP; i++) {
		struct resource_account *account = &resource_accounts[i];

		if (account->state != RESOURCE_ACCOUNT_FREE &&
		    account->kind == kind &&
		    account->external_id == external_id)
			goto fail;
		if (account->state == RESOURCE_ACCOUNT_FREE &&
		    !resource_account_generation_exhausted[i] &&
		    free_slot < 0)
			free_slot = (int)i;
	}
	if (free_slot < 0)
		goto fail;
	if (!resource_promises_replace(0, charge_grants, limits, 0))
		goto fail;
	{
		struct resource_account *account =
			&resource_accounts[free_slot];
		uint64 previous =
			resource_account_generations[free_slot];
		uint64 generation;

		if (previous == RESOURCE_LIMIT_UNBOUNDED) {
			resource_account_generation_exhausted[free_slot] = 1;
			goto fail;
		}
		generation = previous + 1;
		resource_account_generations[free_slot] = generation;
		if (generation == RESOURCE_LIMIT_UNBOUNDED)
			resource_account_generation_exhausted[free_slot] = 1;
		memset(account, 0, sizeof(*account));
		account->state = RESOURCE_ACCOUNT_ACTIVE;
		account->kind = kind;
		account->charge_grants = charge_grants;
		account->external_id = external_id;
		account->generation = generation;
		memmove(&account->limits, limits, sizeof(account->limits));
		if (!resource_promises_replace(0, charge_grants, limits, 1))
			panic("reserved promise create");
		out->slot = (uint)free_slot;
		out->generation = generation;
	}
	intr_restore(enabled);
	return 0;
fail:
	intr_restore(enabled);
	return -1;
}

int resource_account_find(enum resource_account_kind kind,
			  uint64 external_id,
			  struct resource_account_handle *out)
{
	int enabled;

	if (out == 0)
		return -1;
	*out = resource_account_none();
	enabled = intr_save();
	for (uint i = 0; i < RESOURCE_ACCOUNT_CAP; i++) {
		struct resource_account *account = &resource_accounts[i];

		if (account->state == RESOURCE_ACCOUNT_FREE ||
		    account->kind != kind ||
		    account->external_id != external_id)
			continue;
		out->slot = i;
		out->generation = account->generation;
		intr_restore(enabled);
		return 0;
	}
	intr_restore(enabled);
	return -1;
}

int resource_account_matches(struct resource_account_handle handle,
			     enum resource_account_kind kind,
			     uint64 external_id)
{
	int enabled = intr_save();
	struct resource_account *account = resource_account_lookup(handle);
	int matches = account != 0 && account->kind == kind &&
		      account->external_id == external_id;

	intr_restore(enabled);
	return matches;
}

enum resource_account_state
resource_account_state_get(struct resource_account_handle handle)
{
	int enabled = intr_save();
	struct resource_account *account = resource_account_lookup(handle);
	enum resource_account_state state = account == 0 ?
		RESOURCE_ACCOUNT_FREE : account->state;

	intr_restore(enabled);
	return state;
}

int resource_account_active(struct resource_account_handle handle)
{
	return resource_account_state_get(handle) == RESOURCE_ACCOUNT_ACTIVE;
}

int resource_account_member_acquire(struct resource_account_handle handle)
{
	int enabled = intr_save();
	struct resource_account *account = resource_account_lookup(handle);

	if (account == 0 || account->state != RESOURCE_ACCOUNT_ACTIVE ||
	    account->members == (uint)-1) {
		intr_restore(enabled);
		return -1;
	}
	account->members++;
	intr_restore(enabled);
	return 0;
}

int resource_account_member_release(struct resource_account_handle handle,
				    int close_when_empty)
{
	int enabled = intr_save();
	struct resource_account *account = resource_account_lookup(handle);

	if (account == 0 || account->members == 0) {
		intr_restore(enabled);
		return -1;
	}
	account->members--;
	if (close_when_empty && account->members == 0 &&
	    account->state == RESOURCE_ACCOUNT_ACTIVE)
		account->state = RESOURCE_ACCOUNT_CLOSING;
	if (account->state != RESOURCE_ACCOUNT_ACTIVE)
		resource_account_advance(account);
	intr_restore(enabled);
	return 0;
}

int resource_account_close(struct resource_account_handle handle)
{
	int enabled = intr_save();
	struct resource_account *account = resource_account_lookup(handle);

	if (account == 0) {
		intr_restore(enabled);
		return -1;
	}
	if (account->state == RESOURCE_ACCOUNT_ACTIVE)
		account->state = RESOURCE_ACCOUNT_CLOSING;
	if (account->state != RESOURCE_ACCOUNT_ACTIVE)
		resource_account_advance(account);
	intr_restore(enabled);
	return 0;
}

static uint resource_requests_normalize(
	const struct resource_request *requests, uint count,
	uint64 amounts[RESOURCE_KIND_COUNT])
{
	uint kind_mask = 0;

	if (requests == 0 || amounts == 0 || count == 0 ||
	    count > RESOURCE_KIND_COUNT)
		return 0;
	memset(amounts, 0, sizeof(uint64) * RESOURCE_KIND_COUNT);
	for (uint i = 0; i < count; i++) {
		enum resource_kind kind = requests[i].kind;

		if (!resource_kind_valid(kind) || requests[i].amount == 0 ||
		    resource_u64_add(amounts[kind], requests[i].amount,
				     &amounts[kind]) < 0)
			return 0;
		kind_mask |= 1U << kind;
	}
	return kind_mask;
}

/*
 * Admission is exact: global held credit is charged before any U/P/F move.
 * Refill batching only changes which account owns idle F; it never weakens a
 * hard account, class, or global limit.
 */
static int resource_credit_acquire_vector_locked(
	struct resource_account *account,
	enum resource_charge_class charge_class,
	const uint64 amounts[RESOURCE_KIND_COUNT], uint kind_mask,
	int pending, int allow_refill)
{
	uint64 refill[RESOURCE_KIND_COUNT];

	if (account == 0 || amounts == 0 || kind_mask == 0 ||
	    (kind_mask >> RESOURCE_KIND_COUNT) != 0 ||
	    !resource_charge_class_valid(charge_class) ||
	    (account->charge_grants &
	     RESOURCE_CHARGE_GRANT(charge_class)) == 0)
		return 0;
	memset(refill, 0, sizeof(refill));
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		uint kind = resource_kind_first(selected);
		struct workflow_credit_counter *counter =
			resource_account_counter(account, charge_class, kind);
		struct resource_policy *policy = &resource_policies[kind];
		uint64 limit =
			account->limits.class_limit[charge_class][kind];
		uint64 held = workflow_credit_counter_held(counter);
		uint64 amount = amounts[kind];
		uint64 missing, class_available, available;
		uint64 reclaimable, class_reclaimable;

		if (!policy->configured || amount == 0 || held > limit)
			return 0;
		missing = amount > counter->free ? amount - counter->free : 0;
		if (missing == 0)
			continue;
		if (!allow_refill || missing > limit - held)
			return 0;

		if (policy->held > policy->capacity ||
		    *resource_policy_class_held(policy, charge_class) >
			    resource_policy_class_limit(policy, charge_class))
			resource_controller_invariant("resource held invariant");
		available = policy->capacity - policy->held;
		class_available =
			resource_policy_class_limit(policy, charge_class) -
			*resource_policy_class_held(policy, charge_class);
		if (missing <= available && missing <= class_available)
			continue;
		resource_credit_reclaimable_locked(
			account, kind, charge_class, &reclaimable,
			&class_reclaimable);
		if (missing > available + reclaimable ||
		    missing > class_available + class_reclaimable)
			return 0;
	}

	/* No failed vector mutates another domain's idle credit. */
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		uint kind = resource_kind_first(selected);
		struct workflow_credit_counter *counter =
			resource_account_counter(account, charge_class, kind);
		struct resource_policy *policy = &resource_policies[kind];
		uint64 limit =
			account->limits.class_limit[charge_class][kind];
		uint64 held = workflow_credit_counter_held(counter);
		uint64 amount = amounts[kind];
		uint64 missing = amount > counter->free ?
			amount - counter->free : 0;
		uint64 available, class_available, quantum;

		if (missing == 0)
			continue;
		available = policy->capacity - policy->held;
		class_available =
			resource_policy_class_limit(policy, charge_class) -
			*resource_policy_class_held(policy, charge_class);
		if (missing > available || missing > class_available) {
			resource_credit_reclaim_pressure_locked(
				account, charge_class, kind, missing);
			available = policy->capacity - policy->held;
			class_available =
				resource_policy_class_limit(policy, charge_class) -
				*resource_policy_class_held(policy, charge_class);
		}
		if (missing > available || missing > class_available)
			resource_controller_invariant(
				"resource credit dry-run drift");
		quantum = account->state == RESOURCE_ACCOUNT_ACTIVE ?
			resource_credit_refill_quantum(kind) : missing;
		refill[kind] = missing > quantum ? missing : quantum;
		if (refill[kind] > limit - held)
			refill[kind] = limit - held;
		if (refill[kind] > available)
			refill[kind] = available;
		if (refill[kind] > class_available)
			refill[kind] = class_available;
		if (refill[kind] < missing)
			resource_controller_invariant(
				"resource credit refill drift");
	}

	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		uint kind = resource_kind_first(selected);
		struct workflow_credit_counter *counter =
			resource_account_counter(account, charge_class, kind);
		struct resource_policy *policy = &resource_policies[kind];
		uint64 amount = amounts[kind];

		if (refill[kind] != 0) {
			resource_credit_free_add(
				account, charge_class, kind, refill[kind]);
			policy->held += refill[kind];
			*resource_policy_class_held(policy, charge_class) +=
				refill[kind];
		}
		if (counter->free < amount)
			resource_controller_invariant(
				"resource free credit invariant");
		resource_credit_free_take(
			account, charge_class, kind, amount);
		if (pending)
			counter->pending += amount;
		else
			counter->used += amount;
	}
	return 1;
}

int resource_reserve_many(struct resource_account_handle handle,
			  enum resource_charge_class charge_class,
			  const struct resource_request *requests, uint count,
			  struct resource_reservation *reservation)
{
	return resource_reserve_many_flags(
		handle, charge_class, requests, count, 0, reservation);
}

int resource_reserve_many_flags(struct resource_account_handle handle,
				enum resource_charge_class charge_class,
				const struct resource_request *requests, uint count,
				uint flags,
				struct resource_reservation *reservation)
{
	uint64 amounts[RESOURCE_KIND_COUNT];
	uint kind_mask;
	int enabled, phase_result;
	struct resource_account *account;

	if (reservation == 0 ||
	    (flags & ~RESOURCE_RESERVE_ALLOW_CLOSING) != 0 ||
	    !resource_charge_class_valid(charge_class) ||
	    (kind_mask = resource_requests_normalize(
		     requests, count, amounts)) == 0)
		return -1;
	memset(reservation, 0, sizeof(*reservation));
	reservation->account = resource_account_none();
	enabled = intr_save();
	phase_result = resource_phase_claim_vector_locked(
		handle, charge_class, amounts, kind_mask, 1,
		&reservation->phase_lease_generation,
		&reservation->phase_claim_nonce);
	if (phase_result > 0) {
		reservation->account = handle;
		reservation->charge_class = charge_class;
		reservation->kind_mask = kind_mask;
		memmove(reservation->amounts, amounts,
			sizeof(reservation->amounts));
		reservation->active = 1;
		reservation->phase_claimed = 1;
		intr_restore(enabled);
		return 0;
	}
	if (phase_result < 0) {
		intr_restore(enabled);
		return -1;
	}
	account = resource_account_lookup(handle);
	if (account == 0 ||
	    (account->state != RESOURCE_ACCOUNT_ACTIVE &&
	     (!(flags & RESOURCE_RESERVE_ALLOW_CLOSING) ||
	      account->state != RESOURCE_ACCOUNT_CLOSING)) ||
	    !resource_credit_acquire_vector_locked(
		    account, charge_class, amounts, kind_mask, 1,
		    account->state == RESOURCE_ACCOUNT_ACTIVE ||
			    (flags & RESOURCE_RESERVE_ALLOW_CLOSING))) {
		intr_restore(enabled);
		return -1;
	}
	reservation->account = handle;
	reservation->charge_class = charge_class;
	reservation->kind_mask = kind_mask;
	memmove(reservation->amounts, amounts,
		sizeof(reservation->amounts));
	reservation->active = 1;
	intr_restore(enabled);
	return 0;
}

int resource_reservation_commit(struct resource_reservation *reservation)
{
	int enabled;
	struct resource_account *account;

	if (reservation == 0 || !reservation->active)
		return -1;
	enabled = intr_save();
	if (reservation->phase_claimed) {
		if (resource_phase_publish_vector_locked(
			    reservation->account,
			    reservation->charge_class,
			    reservation->amounts,
			    reservation->kind_mask,
			    reservation->phase_lease_generation,
			    reservation->phase_claim_nonce) < 0) {
			intr_restore(enabled);
			return -1;
		}
		reservation->active = 0;
		reservation->phase_claimed = 0;
		intr_restore(enabled);
		return 0;
	}
	account = resource_account_lookup(reservation->account);
	if (account == 0)
		panic("resource reservation account vanished");
	for (uint selected = reservation->kind_mask; selected != 0;
	     selected &= selected - 1) {
		uint kind = resource_kind_first(selected);
		uint64 amount = reservation->amounts[kind];
		struct workflow_credit_counter *counter =
			resource_account_counter(account,
				reservation->charge_class, kind);

		if (counter->pending < amount)
			panic("resource reservation commit");
		counter->pending -= amount;
		counter->used += amount;
	}
	reservation->active = 0;
	intr_restore(enabled);
	return 0;
}

void resource_reservation_cancel(struct resource_reservation *reservation)
{
	int enabled;
	struct resource_account *account;

	if (reservation == 0 || !reservation->active)
		return;
	enabled = intr_save();
	if (reservation->phase_claimed) {
		if (resource_phase_refund_vector_locked(
			    reservation->account,
			    reservation->charge_class,
			    reservation->amounts,
			    reservation->kind_mask,
			    reservation->phase_lease_generation,
			    reservation->phase_claim_nonce) < 0)
			panic("resource phase reservation cancel");
		reservation->active = 0;
		reservation->phase_claimed = 0;
		intr_restore(enabled);
		return;
	}
	account = resource_account_lookup(reservation->account);
	if (account == 0)
		panic("resource cancellation account vanished");
	for (uint selected = reservation->kind_mask; selected != 0;
	     selected &= selected - 1) {
		uint kind = resource_kind_first(selected);
		uint64 amount = reservation->amounts[kind];
		struct workflow_credit_counter *counter =
			resource_account_counter(account,
				reservation->charge_class, kind);

		if (counter->pending < amount)
			panic("resource reservation cancel");
		counter->pending -= amount;
		resource_credit_free_add(
			account, reservation->charge_class, kind, amount);
	}
	reservation->active = 0;
	if (account->state != RESOURCE_ACCOUNT_ACTIVE)
		resource_account_advance(account);
	intr_restore(enabled);
}

int resource_acquire_many(struct resource_account_handle handle,
			  enum resource_charge_class charge_class,
			  const struct resource_request *requests, uint count)
{
	return resource_acquire_many_flags(
		handle, charge_class, requests, count, 0);
}

int resource_acquire_many_flags(struct resource_account_handle handle,
				enum resource_charge_class charge_class,
				const struct resource_request *requests, uint count,
				uint flags)
{
	uint64 amounts[RESOURCE_KIND_COUNT];
	uint kind_mask;
	int enabled, phase_result;
	struct resource_account *account;

	if ((flags & ~RESOURCE_RESERVE_ALLOW_CLOSING) != 0 ||
	    !resource_charge_class_valid(charge_class) ||
	    (kind_mask = resource_requests_normalize(
		     requests, count, amounts)) == 0)
		return -1;
	enabled = intr_save();
	phase_result = resource_phase_claim_vector_locked(
		handle, charge_class, amounts, kind_mask, 0,
		0, 0);
	if (phase_result != 0) {
		intr_restore(enabled);
		return phase_result > 0 ? 0 : -1;
	}
	account = resource_account_lookup(handle);
	if (account == 0 ||
	    (account->state != RESOURCE_ACCOUNT_ACTIVE &&
	     (!(flags & RESOURCE_RESERVE_ALLOW_CLOSING) ||
	      account->state != RESOURCE_ACCOUNT_CLOSING)) ||
	    !resource_credit_acquire_vector_locked(
		    account, charge_class, amounts, kind_mask, 0,
		    account->state == RESOURCE_ACCOUNT_ACTIVE ||
			    (flags & RESOURCE_RESERVE_ALLOW_CLOSING))) {
		intr_restore(enabled);
		return -1;
	}
	intr_restore(enabled);
	return 0;
}

int resource_release_many(struct resource_account_handle handle,
			  enum resource_charge_class charge_class,
			  const struct resource_request *requests, uint count)
{
	uint64 amounts[RESOURCE_KIND_COUNT];
	uint kind_mask;
	int enabled;
	struct resource_account *account;

	if (!resource_charge_class_valid(charge_class) ||
	    (kind_mask = resource_requests_normalize(
		     requests, count, amounts)) == 0)
		return -1;
	enabled = intr_save();
	account = resource_account_lookup(handle);
	if (account == 0) {
		intr_restore(enabled);
		return -1;
	}
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		uint kind = resource_kind_first(selected);
		uint64 amount = amounts[kind];
		struct workflow_credit_counter *counter =
			resource_account_counter(account, charge_class, kind);
		uint64 locked = resource_phase_locked_amount(
			account, charge_class, kind);

		if (counter->used < locked ||
		    amount > counter->used - locked) {
			intr_restore(enabled);
			return -1;
		}
	}
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		uint kind = resource_kind_first(selected);
		uint64 amount = amounts[kind];
		struct workflow_credit_counter *counter =
			resource_account_counter(account, charge_class, kind);

		counter->used -= amount;
		resource_credit_free_add(
			account, charge_class, kind, amount);
	}
	if (account->state != RESOURCE_ACCOUNT_ACTIVE)
		resource_account_advance(account);
	intr_restore(enabled);
	return 0;
}

static uint resource_phase_amount_mask(
	const uint64 amounts[RESOURCE_KIND_COUNT])
{
	uint mask = 0;

	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
		if (amounts[kind] != 0)
			mask |= 1U << kind;
	return mask;
}

static uint64 resource_phase_preserved_free_locked(
	struct resource_account *candidate,
	enum resource_charge_class scanned_class, enum resource_kind kind,
	struct resource_account *accounts[RESOURCE_PHASE_ACCOUNT_COUNT],
	const uint64 amounts[RESOURCE_PHASE_ACCOUNT_COUNT][RESOURCE_KIND_COUNT],
	enum resource_charge_class phase_class)
{
	uint64 preserve = 0;

	if (scanned_class != phase_class)
		return 0;
	for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT; role++) {
		const struct workflow_credit_counter *counter;
		uint64 amount;

		if (candidate != accounts[role])
			continue;
		counter = resource_account_counter_const(
			candidate, scanned_class, kind);
		amount = amounts[role][kind];
		preserve = amount < counter->free ? amount : counter->free;
		break;
	}
	return preserve;
}

static int resource_phase_lock_records_locked(
	struct resource_account *accounts[RESOURCE_PHASE_ACCOUNT_COUNT],
	const struct resource_account_handle
		handles[RESOURCE_PHASE_ACCOUNT_COUNT],
	struct resource_phase_account_lock
		*locks[RESOURCE_PHASE_ACCOUNT_COUNT])
{
	for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT; role++) {
		locks[role] = resource_phase_account_lock_lookup(handles[role]);
		if (locks[role] != 0) {
			if (locks[role]->account != accounts[role] ||
			    locks[role]->refs == (uint)-1)
				return 0;
			continue;
		}
		for (uint i = 0; i < RESOURCE_PHASE_ACCOUNT_LOCK_CAP; i++) {
			struct resource_phase_account_lock *candidate =
				&resource_phase_account_locks[i];

			if (candidate->refs != 0 ||
			    (role != 0 && candidate == locks[0]))
				continue;
			locks[role] = candidate;
			break;
		}
		if (locks[role] == 0)
			return 0;
	}
	return locks[0] != locks[1];
}

static int resource_phase_pair_admissible_locked(
	struct resource_account *accounts[RESOURCE_PHASE_ACCOUNT_COUNT],
	enum resource_charge_class charge_class,
	const uint64 amounts[RESOURCE_PHASE_ACCOUNT_COUNT][RESOURCE_KIND_COUNT],
	const uint masks[RESOURCE_PHASE_ACCOUNT_COUNT],
	uint union_mask)
{
	if (accounts[RESOURCE_PHASE_EXEC] == accounts[RESOURCE_PHASE_STORAGE])
		return 0;
	for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT; role++)
		if (accounts[role]->state != RESOURCE_ACCOUNT_ACTIVE ||
		    (masks[role] != 0 &&
		     (accounts[role]->charge_grants &
		      RESOURCE_CHARGE_GRANT(charge_class)) == 0))
			return 0;
	for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT; role++)
		for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
			if (resource_account_counter_const(
				    accounts[role], charge_class,
				    (enum resource_kind)kind)->pending != 0)
				return 0;

	for (uint selected = union_mask; selected != 0;
	     selected &= selected - 1) {
		enum resource_kind kind =
			(enum resource_kind)resource_kind_first(selected);
		struct resource_policy *policy = &resource_policies[kind];
		uint64 missing = 0, preserved = 0;
		uint64 all_free, reclaimable, class_reclaimable;
		uint64 available, class_available, class_held, class_limit;

		if (!policy->configured || policy->held > policy->capacity)
			return 0;
		class_held =
			*resource_policy_class_held(policy, charge_class);
		class_limit = resource_policy_class_limit(policy, charge_class);
		if (class_held > class_limit)
			panic("resource phase class held invariant");
		for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT;
		     role++) {
			struct resource_account *account = accounts[role];
			const struct workflow_credit_counter *counter =
				resource_account_counter_const(
					account, charge_class, kind);
			uint64 held = workflow_credit_counter_held(counter);
			uint64 limit = account->limits.class_limit
				[charge_class][kind];
			uint64 amount = amounts[role][kind];
			uint64 own = amount < counter->free ?
				amount : counter->free;

			if (held > limit || amount - own > limit - held ||
			    resource_u64_add(missing, amount - own,
					     &missing) < 0 ||
			    resource_u64_add(preserved, own,
					     &preserved) < 0)
				return 0;
		}
		if (resource_u64_add(
			    policy->free[RESOURCE_CHARGE_ORDINARY],
			    policy->free[RESOURCE_CHARGE_RESERVED],
			    &all_free) < 0 || all_free < preserved ||
		    policy->free[charge_class] < preserved)
			panic("resource phase free invariant");
		reclaimable = all_free - preserved;
		class_reclaimable = policy->free[charge_class] - preserved;
		available = policy->capacity - policy->held;
		class_available = class_limit - class_held;
		if (missing > available + reclaimable ||
		    missing > class_available + class_reclaimable)
			return 0;
	}
	return 1;
}

static uint64 resource_phase_trim_scan_locked(
	struct resource_account *accounts[RESOURCE_PHASE_ACCOUNT_COUNT],
	const uint64 amounts[RESOURCE_PHASE_ACCOUNT_COUNT][RESOURCE_KIND_COUNT],
	enum resource_charge_class phase_class,
	enum resource_charge_class scanned_class, enum resource_kind kind,
	uint64 needed)
{
	for (uint i = 0; i < RESOURCE_ACCOUNT_CAP && needed != 0; i++) {
		struct resource_account *account = &resource_accounts[i];
		struct workflow_credit_counter *counter;
		uint64 preserve, reclaim;

		if (account->state == RESOURCE_ACCOUNT_FREE)
			continue;
		counter = resource_account_counter(
			account, scanned_class, kind);
		preserve = resource_phase_preserved_free_locked(
			account, scanned_class, kind, accounts, amounts,
			phase_class);
		if (counter->free < preserve)
			panic("resource phase preserve invariant");
		reclaim = counter->free - preserve;
		if (reclaim > needed)
			reclaim = needed;
		if (reclaim == 0)
			continue;
		resource_credit_trim_counter_amount(
			account, scanned_class, kind, reclaim);
		needed -= reclaim;
		if (account != accounts[RESOURCE_PHASE_EXEC] &&
		    account != accounts[RESOURCE_PHASE_STORAGE] &&
		    account->state != RESOURCE_ACCOUNT_ACTIVE)
			resource_account_advance(account);
	}
	return needed;
}

static void resource_phase_pair_reclaim_locked(
	struct resource_account *accounts[RESOURCE_PHASE_ACCOUNT_COUNT],
	enum resource_charge_class charge_class,
	const uint64 amounts[RESOURCE_PHASE_ACCOUNT_COUNT][RESOURCE_KIND_COUNT],
	uint union_mask)
{
	for (uint selected = union_mask; selected != 0;
	     selected &= selected - 1) {
		enum resource_kind kind =
			(enum resource_kind)resource_kind_first(selected);
		struct resource_policy *policy = &resource_policies[kind];
		uint64 missing = 0, needed, available, class_available;
		uint64 class_held =
			*resource_policy_class_held(policy, charge_class);

		for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT;
		     role++) {
			const struct workflow_credit_counter *counter =
				resource_account_counter_const(
					accounts[role], charge_class, kind);
			uint64 amount = amounts[role][kind];
			uint64 own = amount < counter->free ?
				amount : counter->free;

			missing += amount - own;
		}
		available = policy->capacity - policy->held;
		class_available =
			resource_policy_class_limit(policy, charge_class) -
			class_held;
		needed = missing > available ? missing - available : 0;
		if (missing > class_available &&
		    missing - class_available > needed)
			needed = missing - class_available;
		needed = resource_phase_trim_scan_locked(
			accounts, amounts, charge_class, charge_class, kind,
			needed);
		if (needed != 0)
			panic("resource phase class reclaim drift");

		available = policy->capacity - policy->held;
		class_held =
			*resource_policy_class_held(policy, charge_class);
		class_available =
			resource_policy_class_limit(policy, charge_class) -
			class_held;
		if (missing > class_available)
			panic("resource phase class admission drift");
		needed = missing > available ? missing - available : 0;
		if (needed != 0) {
			enum resource_charge_class other =
				(enum resource_charge_class)(1U -
						     (uint)charge_class);

			needed = resource_phase_trim_scan_locked(
				accounts, amounts, charge_class, other, kind,
				needed);
		}
		if (needed != 0)
			panic("resource phase capacity reclaim drift");
	}
}

static void resource_phase_pair_commit_locked(
	struct resource_account *accounts[RESOURCE_PHASE_ACCOUNT_COUNT],
	const struct resource_account_handle
		handles[RESOURCE_PHASE_ACCOUNT_COUNT],
	struct resource_phase_account_lock
		*locks[RESOURCE_PHASE_ACCOUNT_COUNT],
	enum resource_charge_class charge_class,
	const uint64 amounts[RESOURCE_PHASE_ACCOUNT_COUNT][RESOURCE_KIND_COUNT],
	const uint masks[RESOURCE_PHASE_ACCOUNT_COUNT])
{
	for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT; role++) {
		struct resource_account *account = accounts[role];
		struct resource_phase_account_lock *lock = locks[role];

		if (lock->refs == 0) {
			uint account_slot = (uint)(account - resource_accounts);
			uint lock_slot =
				(uint)(lock - resource_phase_account_locks);

			if (resource_phase_lock_by_account[account_slot] != -1)
				panic("resource phase lock map occupied");
			memset(lock, 0, sizeof(*lock));
			lock->account = account;
			lock->handle = handles[role];
			resource_phase_lock_by_account[account_slot] =
				(signed char)lock_slot;
		}
		lock->refs++;
		for (uint selected = masks[role]; selected != 0;
		     selected &= selected - 1) {
			enum resource_kind kind =
				(enum resource_kind)resource_kind_first(selected);
			struct workflow_credit_counter *counter =
				resource_account_counter(
					account, charge_class, kind);
			struct resource_policy *policy =
				&resource_policies[kind];
			uint64 amount = amounts[role][kind];
			uint64 from_free = amount < counter->free ?
				amount : counter->free;
			uint64 missing = amount - from_free;

			resource_credit_free_take(
				account, charge_class, kind, from_free);
			counter->used += amount;
			lock->locked[charge_class][kind] += amount;
			policy->held += missing;
			*resource_policy_class_held(policy, charge_class) +=
				missing;
		}
	}
}

static int resource_phase_owner_binding_matches(
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle exec_handle, struct thread *thread)
{
	struct proc *process;
	uint scope_id;

	if (thread == 0 || (process = thread->process) == 0 ||
	    !process->workflow_lifecycle_charged ||
	    process->workflow_lifecycle_id != lifecycle.id ||
	    process->workflow_lifecycle_generation !=
		    lifecycle.generation ||
	    !resource_account_handle_equal(
		    thread->resource_account, exec_handle) ||
	    !resource_account_handle_equal(
		    process->resource_account, exec_handle) ||
	    workflow_lifecycle_scope(lifecycle, &scope_id) < 0 ||
	    process->vfs_scope_id != scope_id)
		return 0;
	return 1;
}

static int resource_phase_owner_matches(
	const struct resource_phase_lease_record *lease, struct thread *thread)
{
	return resource_phase_owner_binding_matches(
		lease->lifecycle,
		lease->account[RESOURCE_PHASE_EXEC].handle, thread);
}

int resource_phase_lease_begin(
	struct resource_phase_lease *lease,
	struct workflow_lifecycle_key lifecycle,
	struct resource_account_handle exec_handle,
	struct resource_account_handle storage_handle,
	enum resource_charge_class charge_class,
	const uint64 exec_amounts[RESOURCE_KIND_COUNT],
	const uint64 storage_amounts[RESOURCE_KIND_COUNT], uint node_id,
	uint64 request_id)
{
	struct resource_account *accounts[RESOURCE_PHASE_ACCOUNT_COUNT];
	struct resource_phase_account_lock
		*locks[RESOURCE_PHASE_ACCOUNT_COUNT];
	struct resource_account_handle handles[RESOURCE_PHASE_ACCOUNT_COUNT] = {
		[RESOURCE_PHASE_EXEC] = exec_handle,
		[RESOURCE_PHASE_STORAGE] = storage_handle,
	};
	uint64 amounts[RESOURCE_PHASE_ACCOUNT_COUNT][RESOURCE_KIND_COUNT];
	uint masks[RESOURCE_PHASE_ACCOUNT_COUNT], union_mask;
	struct resource_phase_registry_entry *entry;
	struct thread *owner;
	int registry_slot;
	int enabled;

	if (lease == 0 || lease->state != RESOURCE_PHASE_LEASE_EMPTY ||
	    exec_amounts == 0 || storage_amounts == 0 || request_id == 0 ||
	    !workflow_lifecycle_key_valid(lifecycle) ||
	    !resource_charge_class_valid(charge_class) ||
	    resource_account_handle_equal(exec_handle, storage_handle))
		return -1;
	memmove(amounts[RESOURCE_PHASE_EXEC], exec_amounts,
		sizeof(amounts[RESOURCE_PHASE_EXEC]));
	memmove(amounts[RESOURCE_PHASE_STORAGE], storage_amounts,
		sizeof(amounts[RESOURCE_PHASE_STORAGE]));
	for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT; role++) {
		for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
			if (amounts[role][kind] > (uint)-1)
				return -1;
		masks[role] = resource_phase_amount_mask(amounts[role]);
	}
	union_mask = masks[RESOURCE_PHASE_EXEC] |
		     masks[RESOURCE_PHASE_STORAGE];
	if (union_mask == 0)
		return -1;

	enabled = intr_save();
	owner = curr_thread();
	registry_slot = resource_phase_registry_free_slot();
	accounts[RESOURCE_PHASE_EXEC] = resource_account_lookup(exec_handle);
	accounts[RESOURCE_PHASE_STORAGE] =
		resource_account_lookup(storage_handle);
	if (accounts[RESOURCE_PHASE_EXEC] == 0 ||
	    accounts[RESOURCE_PHASE_STORAGE] == 0 ||
	    accounts[RESOURCE_PHASE_EXEC]->kind != RESOURCE_ACCOUNT_EXEC ||
	    accounts[RESOURCE_PHASE_STORAGE]->kind !=
		    RESOURCE_ACCOUNT_STORAGE ||
	    registry_slot < 0 || owner == 0 || owner->trapframe == 0 ||
	    owner->identity_generation == 0 || owner->process == 0 ||
	    !proc_teardown_live(owner->process) ||
	    resource_phase_registry_owner_busy(owner) ||
	    charge_class !=
		    (owner->process->resource_slot_reserved ?
			    RESOURCE_CHARGE_RESERVED :
			    RESOURCE_CHARGE_ORDINARY) ||
	    resource_phase_generation == RESOURCE_LIMIT_UNBOUNDED ||
	    !workflow_lifecycle_active(lifecycle) ||
	    !resource_phase_owner_binding_matches(
		    lifecycle, exec_handle, owner) ||
	    !resource_phase_lifecycle_pair_admissible(
		    lifecycle, handles) ||
	    !resource_phase_lock_records_locked(accounts, handles, locks) ||
	    !resource_phase_pair_admissible_locked(
		    accounts, charge_class, amounts, masks, union_mask)) {
		intr_restore(enabled);
		return -1;
	}
	resource_phase_pair_reclaim_locked(
		accounts, charge_class, amounts, union_mask);
	resource_phase_pair_commit_locked(
		accounts, handles, locks, charge_class, amounts, masks);
	resource_phase_generation++;
	entry = &resource_phase_registry[registry_slot];
	memset(entry, 0, sizeof(*entry));
	entry->owner = owner;
	entry->lease.lifecycle = lifecycle;
	entry->lease.generation = resource_phase_generation;
	entry->lease.request_id = request_id;
	entry->lease.owner_thread_generation = owner->identity_generation;
	entry->lease.node_id = node_id;
	entry->lease.charge_class = charge_class;
	entry->lease.state = RESOURCE_PHASE_LEASE_ADMITTED;
	for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT; role++) {
		entry->lease.account[role].handle = handles[role];
		entry->lease.account[role].kind_mask = masks[role];
		for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
			entry->lease.account[role].remaining[kind] =
				(uint)amounts[role][kind];
	}
	entry->used = 1;
	lease->generation = resource_phase_generation;
	lease->request_id = request_id;
	lease->node_id = node_id;
	lease->registry_slot = (uint)registry_slot;
	lease->state = RESOURCE_PHASE_LEASE_ADMITTED;
	intr_restore(enabled);
	return 0;
}

int resource_phase_lease_activate(struct resource_phase_lease *lease,
				  uint64 expected_generation)
{
	struct thread *thread;
	struct resource_account *account;
	struct resource_phase_registry_entry *entry;
	struct resource_phase_lease_record *record;
	int enabled;

	if (lease == 0 || expected_generation == 0)
		return -1;
	enabled = intr_save();
	thread = curr_thread();
	entry = resource_phase_registry_lookup(
		lease, expected_generation);
	record = entry == 0 ? 0 : &entry->lease;
	if (entry == 0 || record->state != RESOURCE_PHASE_LEASE_ADMITTED ||
	    thread == 0 || entry->owner != thread ||
	    record->owner_thread_generation != thread->identity_generation ||
	    thread->trapframe == 0 ||
	    thread->identity_generation == 0 ||
	    thread_trap_cold(thread)->resource_phase_lease_generation != 0 ||
	    thread_trap_cold(thread)->resource_phase_claim_depth != 0 ||
	    !proc_teardown_live(thread->process) ||
	    !workflow_lifecycle_active(record->lifecycle) ||
	    !resource_phase_owner_matches(record, thread))
		goto fail;
	for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT; role++) {
		account = resource_account_lookup(record->account[role].handle);
		if (account == 0 || account->state != RESOURCE_ACCOUNT_ACTIVE ||
		    resource_phase_account_lock_lookup(
			    record->account[role].handle) == 0)
			goto fail;
	}
	record->owner_thread_generation = thread->identity_generation;
	record->state = RESOURCE_PHASE_LEASE_ACTIVE;
	lease->state = RESOURCE_PHASE_LEASE_ACTIVE;
	thread_trap_cold(thread)->resource_phase_lease_slot =
		lease->registry_slot;
	thread_trap_cold(thread)->resource_phase_lease_generation =
		expected_generation;
	intr_restore(enabled);
	return 0;
fail:
	intr_restore(enabled);
	return -1;
}

int resource_phase_lease_deactivate(struct resource_phase_lease *lease,
				    uint64 expected_generation)
{
	struct thread *thread;
	struct resource_phase_registry_entry *entry;
	struct resource_phase_lease_record *record;
	int enabled;

	if (lease == 0 || expected_generation == 0)
		return -1;
	enabled = intr_save();
	thread = curr_thread();
	entry = resource_phase_registry_lookup(
		lease, expected_generation);
	record = entry == 0 ? 0 : &entry->lease;
	if (entry == 0 || record->state != RESOURCE_PHASE_LEASE_ACTIVE ||
	    thread == 0 || entry->owner != thread ||
	    thread->trapframe == 0 ||
	    thread_trap_cold(thread)->resource_phase_lease_slot !=
		    lease->registry_slot ||
	    thread_trap_cold(thread)->resource_phase_lease_generation !=
		    expected_generation ||
	    record->owner_thread_generation != thread->identity_generation ||
	    record->outstanding_claims != 0 ||
	    thread_trap_cold(thread)->resource_phase_claim_depth != 0) {
		intr_restore(enabled);
		return -1;
	}
	thread_trap_cold(thread)->resource_phase_lease_slot =
		RESOURCE_PHASE_REGISTRY_SLOT_NONE;
	thread_trap_cold(thread)->resource_phase_lease_generation = 0;
	record->state = RESOURCE_PHASE_LEASE_DEACTIVATED;
	lease->state = RESOURCE_PHASE_LEASE_DEACTIVATED;
	intr_restore(enabled);
	return 0;
}

static int resource_phase_lease_settle_entry_locked(
	struct resource_phase_registry_entry *entry, struct thread *thread,
	uint64 expected_generation)
{
	struct resource_account *accounts[RESOURCE_PHASE_ACCOUNT_COUNT];
	struct resource_phase_account_lock
		*locks[RESOURCE_PHASE_ACCOUNT_COUNT];
	struct resource_phase_lease_record *lease;

	if (entry == 0 || !entry->used || thread == 0)
		return -1;
	lease = &entry->lease;
	if ((lease->state != RESOURCE_PHASE_LEASE_ADMITTED &&
	     lease->state != RESOURCE_PHASE_LEASE_DEACTIVATED) ||
	    lease->generation != expected_generation ||
	    lease->outstanding_claims != 0 || entry->owner != thread ||
	    lease->owner_thread_generation != thread->identity_generation)
		goto fail;
	for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT; role++) {
		accounts[role] =
			resource_account_lookup(lease->account[role].handle);
		locks[role] = resource_phase_account_lock_lookup(
			lease->account[role].handle);
		if (accounts[role] == 0 || locks[role] == 0 ||
		    locks[role]->account != accounts[role] ||
		    locks[role]->refs == 0)
			goto fail;
		for (uint selected = lease->account[role].kind_mask;
		     selected != 0; selected &= selected - 1) {
			enum resource_kind kind =
				(enum resource_kind)resource_kind_first(selected);
			struct workflow_credit_counter *counter =
				resource_account_counter(
					accounts[role], lease->charge_class,
					kind);
			uint64 remaining =
				lease->account[role].remaining[kind];

			if (locks[role]->locked[lease->charge_class][kind] <
				    remaining ||
			    counter->used < remaining)
				goto fail;
		}
	}
	for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT; role++) {
		struct resource_account *account = accounts[role];
		struct resource_phase_account_lock *lock = locks[role];

		for (uint selected = lease->account[role].kind_mask;
		     selected != 0; selected &= selected - 1) {
			enum resource_kind kind =
				(enum resource_kind)resource_kind_first(selected);
			struct workflow_credit_counter *counter =
				resource_account_counter(
					account, lease->charge_class, kind);
			uint64 remaining =
				lease->account[role].remaining[kind];

			counter->used -= remaining;
			lock->locked[lease->charge_class][kind] -=
				remaining;
			resource_credit_free_add(
				account, lease->charge_class, kind, remaining);
			lease->account[role].remaining[kind] = 0;
		}
		lock->refs--;
		if (lock->refs == 0) {
			uint account_slot =
				(uint)(account - resource_accounts);

			if (!resource_phase_account_lock_empty(lock))
				panic("resource phase lock leak");
			if (resource_phase_lock_by_account[account_slot] !=
			    lock - resource_phase_account_locks)
				panic("resource phase lock map mismatch");
			resource_phase_lock_by_account[account_slot] = -1;
			memset(lock, 0, sizeof(*lock));
		}
		if (account->state != RESOURCE_ACCOUNT_ACTIVE)
			resource_account_advance(account);
	}
	memset(entry, 0, sizeof(*entry));
	return 0;
fail:
	return -1;
}

int resource_phase_lease_settle(struct resource_phase_lease *lease,
				uint64 expected_generation)
{
	struct thread *thread;
	int enabled, result;

	if (lease == 0 || expected_generation == 0)
		return -1;
	enabled = intr_save();
	thread = curr_thread();
	{
		struct resource_phase_registry_entry *entry =
			resource_phase_registry_lookup(
				lease, expected_generation);

		if (entry == 0 ||
		    !resource_phase_owner_matches(&entry->lease, thread))
			result = -1;
		else
			result = resource_phase_lease_settle_entry_locked(
				entry, thread, expected_generation);
	}
	if (result == 0) {
		lease->state = RESOURCE_PHASE_LEASE_SETTLED;
		lease->registry_slot = RESOURCE_PHASE_REGISTRY_SLOT_NONE;
	}
	intr_restore(enabled);
	return result;
}

static int resource_phase_active_lease_locked(
	struct resource_phase_registry_entry **entry_out)
{
	struct thread *thread = curr_thread();
	struct thread_trap_cold *cold;
	struct resource_phase_registry_entry *entry;

	if (entry_out == 0)
		return -1;
	*entry_out = 0;
	if (thread == 0 || thread->trapframe == 0)
		return 0;
	cold = thread_trap_cold(thread);
	if (cold->resource_phase_lease_generation == 0)
		return cold->resource_phase_claim_depth == 0 ? 0 : -1;
	if (cold->resource_phase_lease_slot >= RESOURCE_PHASE_LEASE_CAP)
		return -1;
	entry = &resource_phase_registry[cold->resource_phase_lease_slot];
	if (entry->owner != thread || !entry->used ||
	    entry->lease.state != RESOURCE_PHASE_LEASE_ACTIVE ||
	    entry->lease.generation != cold->resource_phase_lease_generation ||
	    entry->lease.owner_thread_generation != thread->identity_generation ||
	    entry->lease.outstanding_claims !=
		    cold->resource_phase_claim_depth)
		return -1;
	*entry_out = entry;
	return 1;
}

static int resource_phase_lease_role(
	const struct resource_phase_lease_record *lease,
	struct resource_account_handle account)
{
	for (uint role = 0; role < RESOURCE_PHASE_ACCOUNT_COUNT; role++)
		if (resource_account_handle_equal(
			    lease->account[role].handle, account))
			return (int)role;
	return -1;
}

static struct resource_phase_claim_record *
resource_phase_claim_record_find(
	const struct resource_phase_registry_entry *entry,
				 uint64 nonce)
{
	uint lease_slot;

	if (nonce == 0)
		return 0;
	lease_slot = (uint)(entry - resource_phase_registry);
	for (uint i = 0; i < RESOURCE_PHASE_MAX_CLAIMS; i++)
		if (resource_phase_claims[i].active &&
		    resource_phase_claims[i].lease_slot == lease_slot &&
		    resource_phase_claims[i].lease_generation ==
			    entry->lease.generation &&
		    resource_phase_claims[i].nonce == nonce)
			return &resource_phase_claims[i];
	return 0;
}

static struct resource_phase_claim_record *
resource_phase_claim_record_free(void)
{
	for (uint i = 0; i < RESOURCE_PHASE_MAX_CLAIMS; i++)
		if (!resource_phase_claims[i].active)
			return &resource_phase_claims[i];
	return 0;
}

static uint resource_phase_claim_count_locked(
	const struct resource_phase_registry_entry *entry)
{
	uint count = 0;
	uint lease_slot;

	if (entry == 0 || !entry->used)
		return 0;
	lease_slot = (uint)(entry - resource_phase_registry);
	for (uint i = 0; i < RESOURCE_PHASE_MAX_CLAIMS; i++)
		if (resource_phase_claims[i].active &&
		    resource_phase_claims[i].lease_slot == lease_slot &&
		    resource_phase_claims[i].lease_generation ==
			    entry->lease.generation)
			count++;
	return count;
}

static int resource_phase_claim_record_matches(
	const struct resource_phase_claim_record *record,
	const struct resource_phase_registry_entry *entry,
	struct resource_account_handle handle,
	enum resource_charge_class charge_class,
	const uint64 amounts[RESOURCE_KIND_COUNT], uint kind_mask)
{
	if (record == 0 || entry == 0 || !entry->used || !record->active ||
	    record->lease_slot != (uint)(entry - resource_phase_registry) ||
	    record->lease_generation != entry->lease.generation ||
	    record->role >= RESOURCE_PHASE_ACCOUNT_COUNT ||
	    !resource_account_handle_equal(
		    entry->lease.account[record->role].handle, handle) ||
	    entry->lease.charge_class != charge_class ||
	    record->kind_mask != kind_mask)
		return 0;
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
		if ((uint64)record->amounts[kind] != amounts[kind])
			return 0;
	return 1;
}

/*
 * Returns 1 when the active phase supplied the complete vector, 0 when the
 * current thread has no phase, and -1 when an active phase cannot satisfy or
 * own it.  Active requests never fall back to ordinary F -> U/P admission.
 */
static int resource_phase_claim_vector_locked(
	struct resource_account_handle handle,
	enum resource_charge_class charge_class,
	const uint64 amounts[RESOURCE_KIND_COUNT], uint kind_mask,
	int tentative, uint64 *lease_generation, uint64 *claim_nonce)
{
	struct resource_phase_registry_entry *entry;
	struct resource_phase_lease_record *lease;
	struct thread_trap_cold *cold;
	struct resource_account *account;
	struct resource_phase_account_lock *lock;
	struct resource_phase_claim_record *record = 0;
	int phase_state;
	int role;

	phase_state = resource_phase_active_lease_locked(&entry);
	if (phase_state <= 0)
		return phase_state;
	lease = &entry->lease;
	cold = thread_trap_cold(entry->owner);
	role = resource_phase_lease_role(lease, handle);
	if (role < 0)
		return -1;
	account = resource_account_lookup(handle);
	lock = resource_phase_account_lock_lookup(handle);
	if (account == 0 || lock == 0 || lock->account != account ||
	    lease->charge_class != charge_class ||
	    (tentative &&
	     (lease->outstanding_claims >= RESOURCE_PHASE_MAX_CLAIMS ||
	      cold->resource_phase_claim_depth >= RESOURCE_PHASE_MAX_CLAIMS ||
	      resource_phase_claim_generation == RESOURCE_LIMIT_UNBOUNDED ||
	      (record = resource_phase_claim_record_free()) == 0)))
		return -1;
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		enum resource_kind kind =
			(enum resource_kind)resource_kind_first(selected);
		uint64 amount = amounts[kind];

		if ((lease->account[role].kind_mask & (1U << kind)) == 0 ||
		    lease->account[role].remaining[kind] < amount ||
		    lock->locked[charge_class][kind] < amount)
			return -1;
	}
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		enum resource_kind kind =
			(enum resource_kind)resource_kind_first(selected);
		uint64 amount = amounts[kind];

		lease->account[role].remaining[kind] -= amount;
		lock->locked[charge_class][kind] -= amount;
		if (tentative)
			lock->claimed[charge_class][kind] += amount;
	}
	if (tentative) {
		uint64 nonce = ++resource_phase_claim_generation;

		memset(record, 0, sizeof(*record));
		record->lease_slot = (uint)(entry - resource_phase_registry);
		record->lease_generation = lease->generation;
		record->nonce = nonce;
		record->kind_mask = kind_mask;
		record->role = (uint)role;
		for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
			record->amounts[kind] = (uint)amounts[kind];
		record->active = 1;
		lease->outstanding_claims++;
		cold->resource_phase_claim_depth++;
		if (claim_nonce != 0)
			*claim_nonce = nonce;
	} else if (claim_nonce != 0) {
		*claim_nonce = 0;
	}
	if (lease_generation != 0)
		*lease_generation = lease->generation;
	return 1;
}

static int resource_phase_publish_vector_locked(
	struct resource_account_handle handle,
	enum resource_charge_class charge_class,
	const uint64 amounts[RESOURCE_KIND_COUNT], uint kind_mask,
	uint64 lease_generation, uint64 claim_nonce)
{
	struct resource_phase_registry_entry *entry;
	struct resource_phase_lease_record *lease;
	struct thread_trap_cold *cold;
	struct resource_account *account;
	struct resource_phase_account_lock *lock;
	struct resource_phase_claim_record *record;

	if (resource_phase_active_lease_locked(&entry) != 1)
		return -1;
	lease = &entry->lease;
	cold = thread_trap_cold(entry->owner);
	if (lease->generation != lease_generation ||
	    lease->charge_class != charge_class ||
	    resource_phase_lease_role(lease, handle) < 0 ||
	    lease->outstanding_claims == 0 ||
	    cold->resource_phase_claim_depth == 0)
		return -1;
	account = resource_account_lookup(handle);
	lock = resource_phase_account_lock_lookup(handle);
	record = resource_phase_claim_record_find(entry, claim_nonce);
	if (account == 0 || lock == 0 || lock->account != account ||
	    !resource_phase_claim_record_matches(
			  record, entry, handle, charge_class, amounts,
			  kind_mask))
		return -1;
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		enum resource_kind kind =
			(enum resource_kind)resource_kind_first(selected);
		struct workflow_credit_counter *counter =
			resource_account_counter(account, charge_class, kind);
		uint64 locked = lock->locked[charge_class][kind];
		uint64 claimed = lock->claimed[charge_class][kind];

		if (claimed < amounts[kind] || locked > counter->used ||
		    claimed > counter->used - locked)
			return -1;
	}
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		enum resource_kind kind =
			(enum resource_kind)resource_kind_first(selected);

		lock->claimed[charge_class][kind] -= amounts[kind];
	}
	memset(record, 0, sizeof(*record));
	lease->outstanding_claims--;
	cold->resource_phase_claim_depth--;
	return 0;
}

static int resource_phase_refund_vector_locked(
	struct resource_account_handle handle,
	enum resource_charge_class charge_class,
	const uint64 amounts[RESOURCE_KIND_COUNT], uint kind_mask,
	uint64 lease_generation, uint64 claim_nonce)
{
	struct resource_phase_registry_entry *entry;
	struct resource_phase_lease_record *lease;
	struct thread_trap_cold *cold;
	struct resource_account *account;
	struct resource_phase_account_lock *lock;
	struct resource_phase_claim_record *record;
	int role;

	if (resource_phase_active_lease_locked(&entry) != 1)
		return -1;
	lease = &entry->lease;
	cold = thread_trap_cold(entry->owner);
	role = resource_phase_lease_role(lease, handle);
	account = resource_account_lookup(handle);
	lock = resource_phase_account_lock_lookup(handle);
	record = resource_phase_claim_record_find(entry, claim_nonce);
	if (role < 0 || account == 0 || lock == 0 ||
	    lock->account != account || lease->generation != lease_generation ||
	    lease->charge_class != charge_class ||
	    lease->outstanding_claims == 0 ||
	    cold->resource_phase_claim_depth == 0 ||
	    !resource_phase_claim_record_matches(
		    record, entry, handle, charge_class, amounts, kind_mask))
		return -1;
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		enum resource_kind kind =
			(enum resource_kind)resource_kind_first(selected);
		struct workflow_credit_counter *counter =
			resource_account_counter(account, charge_class, kind);
		uint64 amount = amounts[kind];

		if ((lease->account[role].kind_mask & (1U << kind)) == 0 ||
		    amount > (uint)-1 -
			    lease->account[role].remaining[kind] ||
		    lock->locked[charge_class][kind] > counter->used ||
		    lock->claimed[charge_class][kind] < amount ||
		    lock->claimed[charge_class][kind] > counter->used -
			    lock->locked[charge_class][kind])
			return -1;
	}
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		enum resource_kind kind =
			(enum resource_kind)resource_kind_first(selected);
		uint64 amount = amounts[kind];

		lease->account[role].remaining[kind] += amount;
		lock->claimed[charge_class][kind] -= amount;
		lock->locked[charge_class][kind] += amount;
	}
	memset(record, 0, sizeof(*record));
	lease->outstanding_claims--;
	cold->resource_phase_claim_depth--;
	return 0;
}

int resource_phase_claim_acquire(
	struct resource_account_handle handle,
	enum resource_charge_class charge_class,
	const struct resource_request *requests, uint count,
	struct resource_phase_claim *claim)
{
	uint64 amounts[RESOURCE_KIND_COUNT];
	uint kind_mask;
	uint64 lease_generation;
	uint64 claim_nonce;
	int enabled, result;

	if (claim == 0 || claim->active ||
	    !resource_charge_class_valid(charge_class) ||
	    (kind_mask = resource_requests_normalize(
		    requests, count, amounts)) == 0)
		return -1;
	enabled = intr_save();
	result = resource_phase_claim_vector_locked(
		handle, charge_class, amounts, kind_mask, 1,
		&lease_generation, &claim_nonce);
	if (result <= 0) {
		intr_restore(enabled);
		return -1;
	}
	memset(claim, 0, sizeof(*claim));
	claim->account = handle;
	claim->lease_generation = lease_generation;
	claim->nonce = claim_nonce;
	claim->charge_class = charge_class;
	claim->kind_mask = kind_mask;
	memmove(claim->amounts, amounts, sizeof(claim->amounts));
	claim->active = 1;
	intr_restore(enabled);
	return 0;
}

int resource_phase_claim_publish(struct resource_phase_claim *claim)
{
	int enabled;

	if (claim == 0 || !claim->active)
		return -1;
	enabled = intr_save();
	if (resource_phase_publish_vector_locked(
		    claim->account, claim->charge_class,
		    claim->amounts, claim->kind_mask,
		    claim->lease_generation, claim->nonce) < 0) {
		intr_restore(enabled);
		return -1;
	}
	claim->active = 0;
	intr_restore(enabled);
	return 0;
}

int resource_phase_claim_refund(struct resource_phase_claim *claim)
{
	int enabled;

	if (claim == 0 || !claim->active)
		return -1;
	enabled = intr_save();
	if (resource_phase_refund_vector_locked(
		    claim->account, claim->charge_class, claim->amounts,
		    claim->kind_mask, claim->lease_generation,
		    claim->nonce) < 0) {
		intr_restore(enabled);
		return -1;
	}
	claim->active = 0;
	intr_restore(enabled);
	return 0;
}

static int resource_phase_lease_abort_entry_locked(
	struct resource_phase_registry_entry *entry, struct thread *owner,
	uint64 expected_generation)
{
	struct resource_phase_lease_record *lease;
	struct thread_trap_cold *cold;

	if (entry == 0 || !entry->used || owner == 0 ||
	    entry->owner != owner)
		return -1;
	lease = &entry->lease;
	if (lease->generation != expected_generation ||
	    lease->owner_thread_generation != owner->identity_generation ||
	    lease->outstanding_claims != 0 ||
	    resource_phase_claim_count_locked(entry) != 0)
		return -1;
	if (lease->state == RESOURCE_PHASE_LEASE_ADMITTED ||
	    lease->state == RESOURCE_PHASE_LEASE_DEACTIVATED)
		return resource_phase_lease_settle_entry_locked(
			entry, owner, expected_generation);
	if (lease->state != RESOURCE_PHASE_LEASE_ACTIVE ||
	    owner->trapframe == 0)
		return -1;
	cold = thread_trap_cold(owner);
	if (cold->resource_phase_lease_slot !=
		    (uint)(entry - resource_phase_registry) ||
	    cold->resource_phase_lease_generation != expected_generation ||
	    cold->resource_phase_claim_depth != 0)
		return -1;
	cold->resource_phase_lease_slot = RESOURCE_PHASE_REGISTRY_SLOT_NONE;
	cold->resource_phase_lease_generation = 0;
	lease->state = RESOURCE_PHASE_LEASE_DEACTIVATED;
	return resource_phase_lease_settle_entry_locked(
		entry, owner, expected_generation);
}

int resource_phase_lease_abort(struct resource_phase_lease *lease,
			       uint64 expected_generation)
{
	int enabled, result;

	if (lease == 0 || expected_generation == 0)
		return -1;
	enabled = intr_save();
	{
		struct resource_phase_registry_entry *entry =
			resource_phase_registry_lookup(lease, expected_generation);

		result = resource_phase_lease_abort_entry_locked(
			entry, curr_thread(), expected_generation);
	}
	if (result == 0) {
		lease->state = RESOURCE_PHASE_LEASE_SETTLED;
		lease->registry_slot = RESOURCE_PHASE_REGISTRY_SLOT_NONE;
	}
	intr_restore(enabled);
	return result;
}

static int resource_phase_thread_cleanup_locked(struct thread *thread)
{
	if (thread == 0)
		return -1;
	for (uint i = 0; i < RESOURCE_PHASE_LEASE_CAP; i++) {
		struct resource_phase_registry_entry *entry =
			&resource_phase_registry[i];
		uint64 generation;

		if (!entry->used || entry->owner != thread)
			continue;
		generation = entry->lease.generation;
		if (entry->lease.owner_thread_generation !=
			    thread->identity_generation ||
		    resource_phase_lease_abort_entry_locked(
			    entry, thread, generation) < 0)
			return -1;
	}
	if (thread->trapframe != 0 &&
	    (thread_trap_cold(thread)->resource_phase_lease_generation != 0 ||
	     thread_trap_cold(thread)->resource_phase_claim_depth != 0))
		return -1;
	return 0;
}

int resource_phase_thread_cleanup(struct thread *thread)
{
	int enabled, result;

	enabled = intr_save();
	result = resource_phase_thread_cleanup_locked(thread);
	intr_restore(enabled);
	return result;
}

int resource_phase_process_cleanup(struct proc *process)
{
	int enabled, result = 0;

	if (process == 0)
		return -1;
	enabled = intr_save();
	for (uint tid = 0; tid < NTHREAD; tid++)
		if (resource_phase_thread_cleanup_locked(
			    &process->threads[tid]) < 0) {
			result = -1;
			break;
		}
	if (result == 0)
		for (uint i = 0; i < RESOURCE_PHASE_LEASE_CAP; i++)
			for (uint tid = 0; tid < NTHREAD; tid++)
				if (resource_phase_registry[i].used &&
				    resource_phase_registry[i].owner ==
					    &process->threads[tid])
					result = -1;
	intr_restore(enabled);
	return result;
}

int resource_phase_thread_can_block(struct thread *thread)
{
	struct thread_trap_cold *cold;
	struct resource_phase_registry_entry *entry;
	int enabled, result;

	if (thread == 0)
		return 0;
	enabled = intr_save();
	if (thread->trapframe == 0) {
		result = 1;
		goto out;
	}
	cold = thread_trap_cold(thread);
	if (cold->resource_phase_claim_depth != 0) {
		result = 0;
		goto out;
	}
	if (cold->resource_phase_lease_generation == 0) {
		result = 1;
		goto out;
	}
	if (cold->resource_phase_lease_slot >= RESOURCE_PHASE_LEASE_CAP) {
		result = 0;
		goto out;
	}
	entry = &resource_phase_registry[cold->resource_phase_lease_slot];
	result = entry->used && entry->owner == thread &&
		 entry->lease.state == RESOURCE_PHASE_LEASE_ACTIVE &&
		 entry->lease.generation ==
			 cold->resource_phase_lease_generation &&
		 entry->lease.owner_thread_generation ==
			 thread->identity_generation &&
		 entry->lease.outstanding_claims == 0;
out:
	intr_restore(enabled);
	return result;
}

int resource_import_usage(struct resource_account_handle handle,
			  enum resource_charge_class charge_class,
			  const struct resource_request *requests, uint count)
{
	uint64 amounts[RESOURCE_KIND_COUNT];
	uint kind_mask;
	int enabled, phase_result;
	struct resource_account *account;

	if (!resource_charge_class_valid(charge_class) ||
	    (kind_mask = resource_requests_normalize(
		     requests, count, amounts)) == 0 ||
	    !resource_count_only_mutation_allowed(kind_mask))
		return -1;
	enabled = intr_save();
	phase_result = resource_phase_claim_vector_locked(
		handle, charge_class, amounts, kind_mask, 0, 0, 0);
	if (phase_result != 0) {
		intr_restore(enabled);
		return phase_result > 0 ? 0 : -1;
	}
	account = resource_account_lookup(handle);
	if (account == 0 || account->state != RESOURCE_ACCOUNT_ACTIVE ||
	    !resource_credit_acquire_vector_locked(
		    account, charge_class, amounts, kind_mask, 0, 1)) {
		intr_restore(enabled);
		return -1;
	}
	intr_restore(enabled);
	return 0;
}

int resource_transfer_usage(struct resource_account_handle from,
			    enum resource_charge_class from_charge_class,
			    struct resource_account_handle to,
			    enum resource_charge_class to_charge_class,
			    const struct resource_request *requests, uint count)
{
	return resource_transfer_usage_flags(
		from, from_charge_class, to, to_charge_class,
		requests, count, 0);
}

int resource_transfer_usage_flags(
	struct resource_account_handle from,
	enum resource_charge_class from_charge_class,
	struct resource_account_handle to,
	enum resource_charge_class to_charge_class,
	const struct resource_request *requests, uint count, uint flags)
{
	uint64 amounts[RESOURCE_KIND_COUNT];
	uint64 reuse[RESOURCE_KIND_COUNT];
	uint kind_mask;
	int enabled, phase_result;
	struct resource_account *source;
	struct resource_account *target;

	if (!resource_charge_class_valid(from_charge_class) ||
	    !resource_charge_class_valid(to_charge_class) ||
	    (flags & ~RESOURCE_RESERVE_ALLOW_CLOSING) != 0 ||
	    (kind_mask = resource_requests_normalize(
		     requests, count, amounts)) == 0 ||
	    !resource_count_only_mutation_allowed(kind_mask))
		return -1;
	memset(reuse, 0, sizeof(reuse));
	enabled = intr_save();
	source = resource_account_lookup(from);
	target = resource_account_lookup(to);
	if (source == 0 || target == 0 ||
	    (target->state != RESOURCE_ACCOUNT_ACTIVE &&
	     (!(flags & RESOURCE_RESERVE_ALLOW_CLOSING) ||
	      target->state != RESOURCE_ACCOUNT_CLOSING)) ||
	    (target->charge_grants &
	     RESOURCE_CHARGE_GRANT(to_charge_class)) == 0) {
		intr_restore(enabled);
		return -1;
	}
	if (resource_account_handle_equal(from, to) &&
	    from_charge_class == to_charge_class) {
		for (uint selected = kind_mask; selected != 0;
		     selected &= selected - 1) {
			uint kind = resource_kind_first(selected);
			struct workflow_credit_counter *counter =
				resource_account_counter(
					source, from_charge_class, kind);

			if (!resource_policies[kind].configured ||
			    counter->used < amounts[kind])
				goto transfer_fail;
		}
		intr_restore(enabled);
		return 0;
	}
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		uint kind = resource_kind_first(selected);
		struct workflow_credit_counter *counter =
			resource_account_counter(source, from_charge_class, kind);
		uint64 locked = resource_phase_locked_amount(
			source, from_charge_class, (enum resource_kind)kind);

		if (!resource_policies[kind].configured ||
		    counter->used < locked ||
		    amounts[kind] > counter->used - locked)
			goto transfer_fail;
	}
	phase_result = resource_phase_claim_vector_locked(
		to, to_charge_class, amounts, kind_mask, 0, 0, 0);
	if (phase_result > 0) {
		for (uint selected = kind_mask; selected != 0;
		     selected &= selected - 1) {
			uint kind = resource_kind_first(selected);
			struct workflow_credit_counter *counter =
				resource_account_counter(
					source, from_charge_class, kind);

			counter->used -= amounts[kind];
			resource_credit_free_add(
				source, from_charge_class, kind, amounts[kind]);
		}
		if (source->state != RESOURCE_ACCOUNT_ACTIVE)
			resource_account_advance(source);
		intr_restore(enabled);
		return 0;
	}
	if (phase_result < 0)
		goto transfer_fail;
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		uint kind = resource_kind_first(selected);
		struct resource_policy *policy = &resource_policies[kind];
		struct workflow_credit_counter *source_counter =
			resource_account_counter(source, from_charge_class, kind);
		struct workflow_credit_counter *target_counter =
			resource_account_counter(target, to_charge_class, kind);
		uint64 amount = amounts[kind];
		uint64 target_held = workflow_credit_counter_held(target_counter);
		int same_account = resource_account_handle_equal(from, to);
		int same_class = from_charge_class == to_charge_class;
		uint64 missing;

		if (!policy->configured)
			goto transfer_fail;
		if (same_account && same_class) {
			if (source_counter->used < amount)
				goto transfer_fail;
			continue;
		}
		{
			uint64 locked = resource_phase_locked_amount(
				source, from_charge_class,
				(enum resource_kind)kind);

			if (source_counter->used < locked ||
			    amount > source_counter->used - locked)
				goto transfer_fail;
		}
		reuse[kind] = amount < target_counter->free ?
			amount : target_counter->free;
		missing = amount - reuse[kind];
		if (missing >
			    target->limits.class_limit[to_charge_class][kind] ||
		    target_held >
			target->limits.class_limit[to_charge_class][kind] -
				missing)
			goto transfer_fail;
		if (!same_class) {
			uint64 target_class_held =
				*resource_policy_class_held(policy,
						     to_charge_class);
			uint64 target_class_limit =
				resource_policy_class_limit(policy,
						    to_charge_class);
			uint64 reclaimable;
			uint64 class_reclaimable;

			if (*resource_policy_class_held(
				    policy, from_charge_class) < missing)
				panic("resource transfer class held");
			if (missing > target_class_limit)
				goto transfer_fail;
			if (target_class_held > target_class_limit - missing) {
				resource_credit_reclaimable_locked(
					target, kind, to_charge_class,
					&reclaimable, &class_reclaimable);
				if (target_class_held -
					    (target_class_limit - missing) >
				    class_reclaimable)
					goto transfer_fail;
			}
		}
		if (same_account &&
		    workflow_credit_counter_held(source_counter) < amount)
			panic("resource transfer account held");
	}
	/* Validation above is side-effect free across the complete vector. */
	if (from_charge_class != to_charge_class)
		for (uint selected = kind_mask; selected != 0;
		     selected &= selected - 1) {
			uint kind = resource_kind_first(selected);
			struct resource_policy *policy = &resource_policies[kind];
			struct workflow_credit_counter *target_counter =
				resource_account_counter(target, to_charge_class, kind);
			uint64 reused = amounts[kind] < target_counter->free ?
				amounts[kind] : target_counter->free;
			uint64 missing = amounts[kind] - reused;
			uint64 class_limit =
				resource_policy_class_limit(policy, to_charge_class);
			uint64 class_held =
				*resource_policy_class_held(policy, to_charge_class);

			if (missing == 0 || class_held <= class_limit - missing)
				continue;
			resource_credit_reclaim_class_pressure_locked(
				target, to_charge_class,
				(enum resource_kind)kind, missing);
			class_held =
				*resource_policy_class_held(policy, to_charge_class);
			if (class_held > class_limit - missing)
				panic("resource transfer dry-run drift");
		}
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		uint kind = resource_kind_first(selected);
		struct resource_policy *policy = &resource_policies[kind];
		struct workflow_credit_counter *source_counter =
			resource_account_counter(source, from_charge_class, kind);
		struct workflow_credit_counter *target_counter =
			resource_account_counter(target, to_charge_class, kind);
		uint64 amount = amounts[kind];
		uint64 reused = reuse[kind];
		uint64 missing = amount - reused;

		if (resource_account_handle_equal(from, to) &&
		    from_charge_class == to_charge_class)
			continue;
		source_counter->used -= amount;
		resource_credit_free_add(
			source, from_charge_class, kind, reused);
		if (target_counter->free < reused)
			panic("resource transfer target free");
		resource_credit_free_take(
			target, to_charge_class, kind, reused);
		target_counter->used += amount;
		if (from_charge_class != to_charge_class) {
			*resource_policy_class_held(policy, from_charge_class) -=
				missing;
			*resource_policy_class_held(policy, to_charge_class) +=
				missing;
		}
	}
	if (source->state != RESOURCE_ACCOUNT_ACTIVE)
		resource_account_advance(source);
	intr_restore(enabled);
	return 0;

transfer_fail:
	intr_restore(enabled);
	return -1;
}

/* 以外部权威扫描替换已选计数；存在 pending 时拒绝不稳定快照。 */
int resource_reconcile_usage(struct resource_account_handle handle,
			     enum resource_charge_class charge_class,
			     const struct resource_request *requests, uint count)
{
	uchar selected[RESOURCE_KIND_COUNT];
	uint64 target[RESOURCE_KIND_COUNT];
	struct resource_account *account;
	int enabled;

	if (!resource_charge_class_valid(charge_class) || requests == 0 ||
	    count == 0 || count > RESOURCE_KIND_COUNT)
		return -1;
	memset(selected, 0, sizeof(selected));
	memset(target, 0, sizeof(target));
	for (uint i = 0; i < count; i++) {
		enum resource_kind kind = requests[i].kind;

		if (!resource_kind_valid(kind) || selected[kind] ||
		    !(resource_kind_attribute_table[kind] &
		      RESOURCE_KIND_COUNT_TRANSFERABLE))
			return -1;
		selected[kind] = 1;
		target[kind] = requests[i].amount;
	}
	enabled = intr_save();
	account = resource_account_lookup(handle);
	if (account == 0 || account->state != RESOURCE_ACCOUNT_ACTIVE ||
	    (account->charge_grants &
	     RESOURCE_CHARGE_GRANT(charge_class)) == 0 ||
	    resource_account_has_phase_credit(account)) {
		intr_restore(enabled);
		return -1;
	}
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct resource_policy *policy;
		struct workflow_credit_counter *counter;
		uint64 old, delta, class_limit, class_held;
		uint64 available, class_available;
		uint64 reclaimable, class_reclaimable;

		if (!selected[kind])
			continue;
		policy = &resource_policies[kind];
		counter = resource_account_counter(account, charge_class, kind);
		old = counter->used;
		if (!policy->configured ||
		    counter->pending != 0 ||
		    target[kind] < resource_phase_locked_amount(
			    account, charge_class, (enum resource_kind)kind) ||
		    target[kind] >
			    account->limits.class_limit[charge_class][kind])
			goto fail;
		if (target[kind] <= old)
			continue;
		delta = target[kind] - old;
		class_limit = resource_policy_class_limit(policy, charge_class);
		class_held = *resource_policy_class_held(policy, charge_class);
		if (policy->held > policy->capacity || class_held > class_limit)
			panic("resource reconcile held invariant");
		available = policy->capacity - policy->held + counter->free;
		class_available = class_limit - class_held + counter->free;
		if (delta <= available && delta <= class_available)
			continue;
		resource_credit_reclaimable_locked(
			account, kind, charge_class, &reclaimable,
			&class_reclaimable);
		if (delta > available + reclaimable ||
		    delta > class_available + class_reclaimable)
			goto fail;
	}
	/* Validation above is side-effect free across the complete vector. */
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct resource_policy *policy;
		struct workflow_credit_counter *counter;
		uint64 old, delta, class_limit, class_held;

		if (!selected[kind])
			continue;
		policy = &resource_policies[kind];
		counter = resource_account_counter(account, charge_class, kind);
		resource_credit_trim_counter(account, charge_class, kind);
		old = counter->used;
		if (target[kind] <= old)
			continue;
		delta = target[kind] - old;
		class_limit = resource_policy_class_limit(policy, charge_class);
		class_held = *resource_policy_class_held(policy, charge_class);
		if (delta > policy->capacity - policy->held ||
		    delta > class_limit - class_held) {
			resource_credit_reclaim_pressure_locked(
				account, charge_class, kind, delta);
			class_held =
				*resource_policy_class_held(policy, charge_class);
		}
		if (delta > policy->capacity - policy->held ||
		    delta > class_limit - class_held)
			panic("resource reconcile dry-run drift");
	}
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct resource_policy *policy;
		struct workflow_credit_counter *counter;
		uint64 old, delta;

		if (!selected[kind])
			continue;
		policy = &resource_policies[kind];
		counter = resource_account_counter(account, charge_class, kind);
		old = counter->used;
		counter->used = target[kind];
		if (target[kind] > old) {
			delta = target[kind] - old;
			policy->held += delta;
			*resource_policy_class_held(policy, charge_class) += delta;
		} else {
			delta = old - target[kind];
			if (policy->held < delta ||
			    *resource_policy_class_held(
				    policy, charge_class) < delta)
				panic("resource reconcile release held");
			policy->held -= delta;
			*resource_policy_class_held(policy, charge_class) -= delta;
		}
	}
	intr_restore(enabled);
	return 0;

fail:
	intr_restore(enabled);
	return -1;
}

uint64 resource_account_usage(struct resource_account_handle handle,
			      enum resource_kind kind)
{
	int enabled;
	struct resource_account *account;
	uint64 used = 0;

	if (!resource_kind_valid(kind))
		return 0;
	enabled = intr_save();
	account = resource_account_lookup(handle);
	if (account != 0)
		for (uint charge_class = 0;
		     charge_class < RESOURCE_CHARGE_CLASS_COUNT;
		     charge_class++)
			used += resource_account_counter_const(
				account,
				(enum resource_charge_class)charge_class,
				kind)->used;
	intr_restore(enabled);
	return used;
}

uint64 resource_account_class_usage(struct resource_account_handle handle,
				    enum resource_charge_class charge_class,
				    enum resource_kind kind)
{
	int enabled;
	struct resource_account *account;
	uint64 used = 0;

	if (!resource_kind_valid(kind) ||
	    !resource_charge_class_valid(charge_class))
		return 0;
	enabled = intr_save();
	account = resource_account_lookup(handle);
	if (account != 0)
		used = resource_account_counter_const(
			account, charge_class, kind)->used;
	intr_restore(enabled);
	return used;
}

int resource_account_kind_snapshot(
	struct resource_account_handle handle, enum resource_kind kind,
	struct resource_account_kind_snapshot *snapshot)
{
	int enabled;
	struct resource_account *account;

	if (!resource_kind_valid(kind) || snapshot == 0)
		return -1;
	enabled = intr_save();
	account = resource_account_lookup(handle);
	if (account == 0) {
		intr_restore(enabled);
		return -1;
	}
	if (!resource_account_has_phase_credit(account))
		resource_account_trim_locked(account);
	memset(snapshot, 0, sizeof(*snapshot));
	snapshot->handle = handle;
	snapshot->state = account->state;
	snapshot->account_kind = account->kind;
	snapshot->charge_grants = account->charge_grants;
	snapshot->members = account->members;
	snapshot->external_id = account->external_id;
	for (uint charge_class = 0;
	     charge_class < RESOURCE_CHARGE_CLASS_COUNT; charge_class++) {
		snapshot->limit[charge_class] =
			account->limits.class_limit[charge_class][kind];
		snapshot->used[charge_class] =
			resource_account_counter_const(
				account,
				(enum resource_charge_class)charge_class,
				kind)->used;
		snapshot->pending[charge_class] =
			resource_account_counter_const(
				account,
				(enum resource_charge_class)charge_class,
				kind)->pending;
	}
	intr_restore(enabled);
	return 0;
}

int resource_account_trim(struct resource_account_handle handle)
{
	int enabled = intr_save();
	struct resource_account *account = resource_account_lookup(handle);

	if (account == 0 || resource_account_has_phase_credit(account)) {
		intr_restore(enabled);
		return -1;
	}
	resource_account_trim_cached_locked(account);
	if (account->state != RESOURCE_ACCOUNT_ACTIVE)
		resource_account_advance(account);
	intr_restore(enabled);
	return 0;
}

static void resource_credit_account_snapshot_locked(
	struct resource_account_handle handle,
	struct resource_account *account,
	struct workflow_credit_account_snapshot *snapshot)
{
	memset(snapshot, 0, sizeof(*snapshot));
	snapshot->handle = handle;
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
		for (uint charge_class = 0;
		     charge_class < RESOURCE_CHARGE_CLASS_COUNT;
		     charge_class++) {
			const struct workflow_credit_counter *counter =
				resource_account_counter_const(
					account,
					(enum resource_charge_class)charge_class,
					(enum resource_kind)kind);

			snapshot->used[kind] += counter->used;
			snapshot->pending[kind] += counter->pending;
			snapshot->free[kind] += counter->free;
			snapshot->held[kind] +=
				workflow_credit_counter_held(counter);
		}
}

int resource_credit_snapshot_pair_trim(
	struct resource_account_handle exec_handle,
	struct resource_account_handle storage_handle,
	struct workflow_credit_snapshot *snapshot)
{
	struct resource_account *accounts[WORKFLOW_CREDIT_ACCOUNT_COUNT];
	struct resource_account_handle handles[WORKFLOW_CREDIT_ACCOUNT_COUNT] = {
		[WORKFLOW_CREDIT_EXEC] = exec_handle,
		[WORKFLOW_CREDIT_STORAGE] = storage_handle,
	};
	int enabled;

	if (snapshot == 0)
		return -1;
	enabled = intr_save();
	for (uint role = 0; role < WORKFLOW_CREDIT_ACCOUNT_COUNT; role++) {
		accounts[role] = resource_account_lookup(handles[role]);
		if (accounts[role] == 0 ||
		    resource_account_has_phase_credit(accounts[role])) {
			intr_restore(enabled);
			return -1;
		}
	}
	if (accounts[WORKFLOW_CREDIT_EXEC]->kind != RESOURCE_ACCOUNT_EXEC ||
	    accounts[WORKFLOW_CREDIT_STORAGE]->kind !=
		    RESOURCE_ACCOUNT_STORAGE) {
		intr_restore(enabled);
		return -1;
	}
	memset(snapshot, 0, sizeof(*snapshot));
	resource_account_trim_locked(accounts[WORKFLOW_CREDIT_EXEC]);
	if (accounts[WORKFLOW_CREDIT_STORAGE] !=
	    accounts[WORKFLOW_CREDIT_EXEC])
		resource_account_trim_locked(
			accounts[WORKFLOW_CREDIT_STORAGE]);
	for (uint role = 0; role < WORKFLOW_CREDIT_ACCOUNT_COUNT; role++)
		resource_credit_account_snapshot_locked(
			handles[role], accounts[role], &snapshot->account[role]);
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
		for (uint role = 0; role < WORKFLOW_CREDIT_ACCOUNT_COUNT;
		     role++) {
			if (role == WORKFLOW_CREDIT_STORAGE &&
			    accounts[role] == accounts[WORKFLOW_CREDIT_EXEC])
				continue;
			snapshot->used[kind] +=
				snapshot->account[role].used[kind];
			snapshot->pending[kind] +=
				snapshot->account[role].pending[kind];
			snapshot->free[kind] +=
				snapshot->account[role].free[kind];
			snapshot->held[kind] +=
				snapshot->account[role].held[kind];
		}
	resource_credit_changed();
	snapshot->epoch = resource_credit_epoch;
	intr_restore(enabled);
	return 0;
}
