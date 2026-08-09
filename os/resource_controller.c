#include "resource_controller.h"
#include "defs.h"
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
static struct resource_policy resource_policies[RESOURCE_KIND_COUNT];
static struct resource_account resource_accounts[RESOURCE_ACCOUNT_CAP];
static uint64 resource_account_generations[RESOURCE_ACCOUNT_CAP];
static uchar resource_account_generation_exhausted[RESOURCE_ACCOUNT_CAP];
static uint64 resource_credit_epoch;
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

static void resource_credit_free_add(
	struct resource_account *account,
	enum resource_charge_class charge_class, enum resource_kind kind,
	uint64 amount)
{
	struct workflow_credit_counter *counter =
		resource_account_counter(account, charge_class, kind);
	struct resource_policy *policy = &resource_policies[kind];

	if (amount > (uint)-1 - counter->free)
		panic("resource free credit overflow");
	if (amount > RESOURCE_LIMIT_UNBOUNDED - policy->free[charge_class])
		panic("resource global free credit overflow");
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
		panic("resource free credit underflow");
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
static int resource_account_empty(const struct resource_account *account)
{
	return workflow_credit_domain_empty(&account->credit);
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
				panic("reserved promise underflow");
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
		panic("resource credit trim underflow");
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
		panic("resource reclaimable free invariant");
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
	resource_account_trim_locked(account);
	if (account->state == RESOURCE_ACCOUNT_CLOSING &&
	    account->members == 0)
		account->state = RESOURCE_ACCOUNT_DRAINING;
	if (account->state == RESOURCE_ACCOUNT_DRAINING &&
	    resource_account_empty(account)) {
		uint64 generation = account->generation;
		if (!resource_promises_replace(account, 0, &account->limits, 1))
			panic("reserved promise release");
		memset(account, 0, sizeof(*account));
		account->generation = generation;
		account->state = RESOURCE_ACCOUNT_FREE;
	}
}
void resource_controller_init(void)
{
	memset(resource_policies, 0, sizeof(resource_policies));
	memset(resource_accounts, 0, sizeof(resource_accounts));
	memset(resource_account_generations, 0,
	       sizeof(resource_account_generations));
	memset(resource_account_generation_exhausted, 0,
	       sizeof(resource_account_generation_exhausted));
	resource_credit_epoch = 1;
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
			panic("resource held invariant");
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
			panic("resource credit dry-run drift");
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
			panic("resource credit refill drift");
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
			panic("resource free credit invariant");
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
	int enabled;
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
	int enabled;
	struct resource_account *account;

	if ((flags & ~RESOURCE_RESERVE_ALLOW_CLOSING) != 0 ||
	    !resource_charge_class_valid(charge_class) ||
	    (kind_mask = resource_requests_normalize(
		     requests, count, amounts)) == 0)
		return -1;
	enabled = intr_save();
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

		if (counter->used < amount) {
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

int resource_import_usage(struct resource_account_handle handle,
			  enum resource_charge_class charge_class,
			  const struct resource_request *requests, uint count)
{
	uint64 amounts[RESOURCE_KIND_COUNT];
	uint kind_mask;
	int enabled;
	struct resource_account *account;

	if (!resource_charge_class_valid(charge_class) ||
	    (kind_mask = resource_requests_normalize(
		     requests, count, amounts)) == 0 ||
	    !resource_count_only_mutation_allowed(kind_mask))
		return -1;
	enabled = intr_save();
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
	int enabled;
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

		if (!policy->configured || source_counter->used < amount)
			goto transfer_fail;
		if (same_account && same_class)
			continue;
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
	     RESOURCE_CHARGE_GRANT(charge_class)) == 0) {
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

	if (account == 0) {
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
		if (accounts[role] == 0) {
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
