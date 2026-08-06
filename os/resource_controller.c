#include "resource_controller.h"
#include "defs.h"
#include "riscv.h"

struct resource_policy {
	uint64 capacity;
	uint64 ordinary_limit;
	uint64 reserved_limit;
	uint64 used;
	uint64 pending;
	uint64 ordinary_used;
	uint64 ordinary_pending;
	uint64 reserved_used;
	uint64 reserved_pending;
	uint64 reserved_promised;
	int configured;
	int reserved_guaranteed;
};
struct resource_rate_state {
	struct resource_rate_profile profile;
	uint64 tokens;
	uint64 leased;
	uint64 debt;
	uint64 pending_debt;
};
struct resource_account {
	enum resource_account_state state;
	enum resource_account_kind kind;
	uint charge_grants;
	uint64 external_id;
	uint64 generation;
	uint members;
	struct resource_account_limits limits;
	uint64 used[RESOURCE_CHARGE_CLASS_COUNT][RESOURCE_KIND_COUNT];
	uint64 pending[RESOURCE_CHARGE_CLASS_COUNT][RESOURCE_KIND_COUNT];
	struct resource_rate_state rate_lanes[RESOURCE_RATE_LANE_CAP];
};
struct resource_rate_lease_entry {
	struct resource_rate_endpoint endpoint;
	uint64 leased_amount;
	uint64 debt_amount;
};
enum resource_rate_lease_tag {
	RESOURCE_RATE_LEASE_FREE = 0,
	RESOURCE_RATE_LEASE_LIVE,
	RESOURCE_RATE_LEASE_RETIRED,
};
struct resource_rate_lease {
	enum resource_rate_lease_tag tag;
	uint generation;
	uint count;
	uint16 next_free;
	struct resource_rate_lease_entry entries[RESOURCE_RATE_BUNDLE_CAP];
};
#define RESOURCE_RATE_LEASE_INDEX_NONE ((uint16)-1)
_Static_assert(RESOURCE_RATE_LEASE_CAP < RESOURCE_RATE_LEASE_INDEX_NONE,
	       "rate lease freelist index budget");
_Static_assert(sizeof(struct resource_rate_lease) == 128U,
	       "rate lease slot budget");
static struct resource_policy resource_policies[RESOURCE_KIND_COUNT];
static struct resource_account resource_accounts[RESOURCE_ACCOUNT_CAP];
static uint64 resource_account_generations[RESOURCE_ACCOUNT_CAP];
static uchar resource_account_generation_exhausted[RESOURCE_ACCOUNT_CAP];
static struct resource_rate_state
	resource_rate_globals[RESOURCE_RATE_GLOBAL_CAP];
static struct resource_rate_lease
	resource_rate_leases[RESOURCE_RATE_LEASE_CAP];
static uint16 resource_rate_lease_free_head;
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
static int resource_kind_valid(enum resource_kind kind)
{
	return kind >= RESOURCE_PROCESS && kind < RESOURCE_KIND_COUNT;
}
uint resource_kind_attributes(enum resource_kind kind)
{
	return resource_kind_valid(kind) ? resource_kind_attribute_table[kind] : 0;
}

static int resource_count_only_mutation_allowed(
	const uint64 amounts[RESOURCE_KIND_COUNT])
{
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
		if (amounts[kind] != 0 &&
		    !(resource_kind_attribute_table[kind] &
		      RESOURCE_KIND_COUNT_TRANSFERABLE))
			return 0;
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
	for (uint charge_class = 0;
	     charge_class < RESOURCE_CHARGE_CLASS_COUNT; charge_class++)
		for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
			if (account->used[charge_class][kind] != 0 ||
			    account->pending[charge_class][kind] != 0)
				return 0;
	for (uint lane = 0; lane < RESOURCE_RATE_LANE_CAP; lane++) {
		const struct resource_rate_state *state =
			&account->rate_lanes[lane];

		if (state->leased != 0 || state->debt != 0 ||
		    state->pending_debt != 0 ||
		    state->tokens != state->profile.burst)
			return 0;
	}
	return 1;
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
static void resource_account_advance(struct resource_account *account)
{
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
	memset(resource_rate_globals, 0, sizeof(resource_rate_globals));
	memset(resource_rate_leases, 0, sizeof(resource_rate_leases));
	for (uint i = 0; i < RESOURCE_RATE_LEASE_CAP; i++) {
		resource_rate_leases[i].tag = RESOURCE_RATE_LEASE_FREE;
		resource_rate_leases[i].next_free =
			i + 1 < RESOURCE_RATE_LEASE_CAP ?
			(uint16)(i + 1) : RESOURCE_RATE_LEASE_INDEX_NONE;
	}
	resource_rate_lease_free_head = 0;
}
int resource_policy_configure(enum resource_kind kind, uint64 capacity,
			      uint64 ordinary_limit,
			      uint64 reserved_limit)
{
	struct resource_policy *policy;
	int enabled;
	int result = -1;

	if (!resource_kind_valid(kind) || capacity == 0 ||
	    ordinary_limit > capacity || reserved_limit > capacity)
		return -1;
	enabled = intr_save();
	policy = &resource_policies[kind];
	if ((policy->used != 0 || policy->pending != 0) &&
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
	/* The guarantee must precede every account that may claim it. */
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
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		const struct resource_policy *policy = &resource_policies[kind];
		struct resource_policy_snapshot *snapshot = &snapshots[kind];

		if (!policy->configured)
			continue;
		snapshot->capacity = policy->capacity;
		snapshot->used = policy->used;
		snapshot->pending = policy->pending;
		snapshot->ordinary_used = policy->ordinary_used;
		snapshot->ordinary_pending = policy->ordinary_pending;
		snapshot->reserved_used = policy->reserved_used;
		snapshot->reserved_pending = policy->reserved_pending;
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
	resource_account_advance(account);
	intr_restore(enabled);
	return 0;
}

static int resource_requests_normalize(
	const struct resource_request *requests, uint count,
	uint64 amounts[RESOURCE_KIND_COUNT])
{
	if (requests == 0 || amounts == 0 || count == 0 ||
	    count > RESOURCE_KIND_COUNT)
		return -1;
	memset(amounts, 0, sizeof(uint64) * RESOURCE_KIND_COUNT);
	for (uint i = 0; i < count; i++) {
		enum resource_kind kind = requests[i].kind;

		if (!resource_kind_valid(kind) || requests[i].amount == 0 ||
		    resource_u64_add(amounts[kind], requests[i].amount,
				     &amounts[kind]) < 0)
			return -1;
	}
	return 0;
}

static int resource_can_add(struct resource_account *account,
			    enum resource_charge_class charge_class,
			    const uint64 amounts[RESOURCE_KIND_COUNT])
{
	if (!resource_charge_class_valid(charge_class) ||
	    (account->charge_grants &
	     RESOURCE_CHARGE_GRANT(charge_class)) == 0)
		return 0;
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct resource_policy *policy = &resource_policies[kind];
		uint64 account_total;
		uint64 global_total;
		uint64 class_total;

		if (amounts[kind] == 0)
			continue;
		if (!policy->configured ||
		    resource_u64_add(account->used[charge_class][kind],
				     account->pending[charge_class][kind],
				     &account_total) < 0 ||
		    resource_u64_add(account_total, amounts[kind],
				     &account_total) < 0 ||
		    account_total >
			    account->limits.class_limit[charge_class][kind] ||
		    resource_u64_add(policy->used, policy->pending,
				     &global_total) < 0 ||
		    resource_u64_add(global_total, amounts[kind],
				     &global_total) < 0 ||
		    global_total > policy->capacity)
			return 0;
		if (charge_class == RESOURCE_CHARGE_ORDINARY) {
			if (resource_u64_add(policy->ordinary_used,
					     policy->ordinary_pending,
					     &class_total) < 0 ||
			    resource_u64_add(class_total, amounts[kind],
					     &class_total) < 0 ||
			    class_total > policy->ordinary_limit)
				return 0;
		} else {
			if (resource_u64_add(policy->reserved_used,
					     policy->reserved_pending,
					     &class_total) < 0 ||
			    resource_u64_add(class_total, amounts[kind],
					     &class_total) < 0 ||
			    class_total > policy->reserved_limit)
				return 0;
		}
	}
	return 1;
}

static void resource_add_pending(
	struct resource_account *account,
	enum resource_charge_class charge_class,
	const uint64 amounts[RESOURCE_KIND_COUNT])
{
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct resource_policy *policy = &resource_policies[kind];

		if (amounts[kind] == 0)
			continue;
		account->pending[charge_class][kind] += amounts[kind];
		policy->pending += amounts[kind];
		if (charge_class == RESOURCE_CHARGE_ORDINARY)
			policy->ordinary_pending += amounts[kind];
		else
			policy->reserved_pending += amounts[kind];
	}
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
	int enabled;
	struct resource_account *account;

	if (reservation == 0 ||
	    (flags & ~RESOURCE_RESERVE_ALLOW_CLOSING) != 0 ||
	    !resource_charge_class_valid(charge_class) ||
	    resource_requests_normalize(requests, count, amounts) < 0)
		return -1;
	memset(reservation, 0, sizeof(*reservation));
	reservation->account = resource_account_none();
	enabled = intr_save();
	account = resource_account_lookup(handle);
	if (account == 0 ||
	    (account->state != RESOURCE_ACCOUNT_ACTIVE &&
	     (!(flags & RESOURCE_RESERVE_ALLOW_CLOSING) ||
	      account->state != RESOURCE_ACCOUNT_CLOSING)) ||
	    !resource_can_add(account, charge_class, amounts)) {
		intr_restore(enabled);
		return -1;
	}
	resource_add_pending(account, charge_class, amounts);
	reservation->account = handle;
	reservation->charge_class = charge_class;
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
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct resource_policy *policy = &resource_policies[kind];
		uint64 amount = reservation->amounts[kind];
		enum resource_charge_class charge_class =
			reservation->charge_class;

		if (amount == 0)
			continue;
		if (!resource_charge_class_valid(charge_class) ||
		    account->pending[charge_class][kind] < amount ||
		    policy->pending < amount)
			panic("resource reservation underflow");
		account->pending[charge_class][kind] -= amount;
		account->used[charge_class][kind] += amount;
		policy->pending -= amount;
		policy->used += amount;
		if (charge_class == RESOURCE_CHARGE_ORDINARY) {
			if (policy->ordinary_pending < amount)
				panic("ordinary reservation underflow");
			policy->ordinary_pending -= amount;
			policy->ordinary_used += amount;
		} else {
			if (policy->reserved_pending < amount)
				panic("reserved reservation underflow");
			policy->reserved_pending -= amount;
			policy->reserved_used += amount;
		}
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
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct resource_policy *policy = &resource_policies[kind];
		uint64 amount = reservation->amounts[kind];
		enum resource_charge_class charge_class =
			reservation->charge_class;

		if (amount == 0)
			continue;
		if (!resource_charge_class_valid(charge_class) ||
		    account->pending[charge_class][kind] < amount ||
		    policy->pending < amount)
			panic("resource cancellation underflow");
		account->pending[charge_class][kind] -= amount;
		policy->pending -= amount;
		if (charge_class == RESOURCE_CHARGE_ORDINARY) {
			if (policy->ordinary_pending < amount)
				panic("ordinary cancellation underflow");
			policy->ordinary_pending -= amount;
		} else {
			if (policy->reserved_pending < amount)
				panic("reserved cancellation underflow");
			policy->reserved_pending -= amount;
		}
	}
	reservation->active = 0;
	resource_account_advance(account);
	intr_restore(enabled);
}

static void resource_sub_used(
	struct resource_account *account,
	enum resource_charge_class charge_class,
	const uint64 amounts[RESOURCE_KIND_COUNT])
{
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct resource_policy *policy = &resource_policies[kind];
		uint64 amount = amounts[kind];

		if (amount == 0)
			continue;
		if (!resource_charge_class_valid(charge_class) ||
		    account->used[charge_class][kind] < amount ||
		    policy->used < amount)
			panic("resource usage underflow");
		account->used[charge_class][kind] -= amount;
		policy->used -= amount;
		if (charge_class == RESOURCE_CHARGE_ORDINARY) {
			if (policy->ordinary_used < amount)
				panic("ordinary usage underflow");
			policy->ordinary_used -= amount;
		} else {
			if (policy->reserved_used < amount)
				panic("reserved usage underflow");
			policy->reserved_used -= amount;
		}
	}
}

int resource_release_many(struct resource_account_handle handle,
			  enum resource_charge_class charge_class,
			  const struct resource_request *requests, uint count)
{
	uint64 amounts[RESOURCE_KIND_COUNT];
	int enabled;
	struct resource_account *account;

	if (!resource_charge_class_valid(charge_class) ||
	    resource_requests_normalize(requests, count, amounts) < 0)
		return -1;
	enabled = intr_save();
	account = resource_account_lookup(handle);
	if (account == 0) {
		intr_restore(enabled);
		return -1;
	}
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++)
		if (account->used[charge_class][kind] < amounts[kind]) {
			intr_restore(enabled);
			return -1;
		}
	resource_sub_used(account, charge_class, amounts);
	resource_account_advance(account);
	intr_restore(enabled);
	return 0;
}

int resource_import_usage(struct resource_account_handle handle,
			  enum resource_charge_class charge_class,
			  const struct resource_request *requests, uint count)
{
	uint64 amounts[RESOURCE_KIND_COUNT];
	int enabled;
	struct resource_account *account;

	if (!resource_charge_class_valid(charge_class) ||
	    resource_requests_normalize(requests, count, amounts) < 0 ||
	    !resource_count_only_mutation_allowed(amounts))
		return -1;
	enabled = intr_save();
	account = resource_account_lookup(handle);
	if (account == 0 || account->state != RESOURCE_ACCOUNT_ACTIVE ||
	    !resource_can_add(account, charge_class, amounts)) {
		intr_restore(enabled);
		return -1;
	}
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct resource_policy *policy = &resource_policies[kind];
		uint64 amount = amounts[kind];

		if (amount == 0)
			continue;
		account->used[charge_class][kind] += amount;
		policy->used += amount;
		if (charge_class == RESOURCE_CHARGE_ORDINARY)
			policy->ordinary_used += amount;
		else
			policy->reserved_used += amount;
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
	int enabled;
	struct resource_account *source;
	struct resource_account *target;

	if (!resource_charge_class_valid(from_charge_class) ||
	    !resource_charge_class_valid(to_charge_class) ||
	    (flags & ~RESOURCE_RESERVE_ALLOW_CLOSING) != 0 ||
	    resource_requests_normalize(requests, count, amounts) < 0 ||
	    !resource_count_only_mutation_allowed(amounts))
		return -1;
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
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct resource_policy *policy = &resource_policies[kind];
		uint64 target_account_total;
		uint64 target_class_total;
		uint64 net_account = amounts[kind];
		uint64 net_class = amounts[kind];

		if (source->used[from_charge_class][kind] <
		    amounts[kind]) {
			intr_restore(enabled);
			return -1;
		}
		if (amounts[kind] == 0)
			continue;
		if (resource_account_handle_equal(from, to) &&
		    from_charge_class == to_charge_class)
			net_account = 0;
		if (from_charge_class == to_charge_class)
			net_class = 0;
		if (!policy->configured ||
		    resource_u64_add(
			    target->used[to_charge_class][kind],
			    target->pending[to_charge_class][kind],
			    &target_account_total) < 0 ||
		    resource_u64_add(target_account_total, net_account,
				     &target_account_total) < 0 ||
		    target_account_total >
			    target->limits
				    .class_limit[to_charge_class][kind]) {
			intr_restore(enabled);
			return -1;
		}
		if (resource_u64_add(
			    to_charge_class == RESOURCE_CHARGE_ORDINARY ?
				    policy->ordinary_used :
				    policy->reserved_used,
			    to_charge_class == RESOURCE_CHARGE_ORDINARY ?
				    policy->ordinary_pending :
				    policy->reserved_pending,
			    &target_class_total) < 0 ||
		    resource_u64_add(target_class_total, net_class,
				     &target_class_total) < 0) {
			intr_restore(enabled);
			return -1;
		}
		if ((to_charge_class == RESOURCE_CHARGE_ORDINARY &&
		     target_class_total > policy->ordinary_limit) ||
		    (to_charge_class == RESOURCE_CHARGE_RESERVED &&
		     target_class_total > policy->reserved_limit)) {
			intr_restore(enabled);
			return -1;
		}
	}
	resource_sub_used(source, from_charge_class, amounts);
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct resource_policy *policy = &resource_policies[kind];
		uint64 amount = amounts[kind];

		if (amount == 0)
			continue;
		target->used[to_charge_class][kind] += amount;
		policy->used += amount;
		if (to_charge_class == RESOURCE_CHARGE_ORDINARY)
			policy->ordinary_used += amount;
		else
			policy->reserved_used += amount;
	}
	resource_account_advance(source);
	intr_restore(enabled);
	return 0;
}

/*
 * Replace selected committed counters with an authoritative external scan.
 * This is intended for bounded recovery/rebuild code: pending reservations
 * make the snapshot unstable and therefore reject reconciliation.
 */
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
		uint64 old;
		uint64 global_total;
		uint64 class_total;

		if (!selected[kind])
			continue;
		policy = &resource_policies[kind];
		old = account->used[charge_class][kind];
		if (!policy->configured ||
		    account->pending[charge_class][kind] != 0 ||
		    target[kind] >
			    account->limits.class_limit[charge_class][kind] ||
		    policy->used < old)
			goto fail;
		global_total = policy->used - old;
		if (resource_u64_add(global_total, target[kind],
				     &global_total) < 0 ||
		    resource_u64_add(global_total, policy->pending,
				     &global_total) < 0 ||
		    global_total > policy->capacity)
			goto fail;
		if (charge_class == RESOURCE_CHARGE_ORDINARY) {
			if (policy->ordinary_used < old)
				goto fail;
			class_total = policy->ordinary_used - old;
			if (resource_u64_add(class_total, target[kind],
					     &class_total) < 0 ||
			    resource_u64_add(class_total,
					     policy->ordinary_pending,
					     &class_total) < 0 ||
			    class_total > policy->ordinary_limit)
				goto fail;
		} else {
			if (policy->reserved_used < old)
				goto fail;
			class_total = policy->reserved_used - old;
			if (resource_u64_add(class_total, target[kind],
					     &class_total) < 0 ||
			    resource_u64_add(class_total,
					     policy->reserved_pending,
					     &class_total) < 0 ||
			    class_total > policy->reserved_limit)
				goto fail;
		}
	}
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct resource_policy *policy;
		uint64 old;

		if (!selected[kind])
			continue;
		policy = &resource_policies[kind];
		old = account->used[charge_class][kind];
		account->used[charge_class][kind] = target[kind];
		policy->used = policy->used - old + target[kind];
		if (charge_class == RESOURCE_CHARGE_ORDINARY)
			policy->ordinary_used =
				policy->ordinary_used - old + target[kind];
		else
			policy->reserved_used =
				policy->reserved_used - old + target[kind];
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
			used += account->used[charge_class][kind];
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
		used = account->used[charge_class][kind];
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
		snapshot->used[charge_class] = account->used[charge_class][kind];
		snapshot->pending[charge_class] =
			account->pending[charge_class][kind];
	}
	intr_restore(enabled);
	return 0;
}

static int resource_rate_profile_valid(
	const struct resource_rate_profile *profile)
{
	if (profile == 0 ||
	    (profile->flags & ~RESOURCE_RATE_PROFILE_ALLOW_CLOSING) != 0)
		return 0;
	if (profile->burst == 0)
		return profile->refill == 0 && profile->flags == 0;
	return profile->refill != 0 && profile->refill <= profile->burst;
}

static int resource_rate_state_idle(
	const struct resource_rate_state *state)
{
	return state->leased == 0 && state->debt == 0 &&
	       state->pending_debt == 0 &&
	       state->tokens == state->profile.burst;
}

struct resource_rate_lease_handle resource_rate_lease_none(void)
{
	struct resource_rate_lease_handle handle = {
		.slot = 0,
		.generation = 0,
	};

	return handle;
}

static struct resource_rate_lease *
resource_rate_lease_lookup(struct resource_rate_lease_handle handle)
{
	struct resource_rate_lease *lease;

	if (handle.slot == 0 || handle.slot > RESOURCE_RATE_LEASE_CAP ||
	    handle.generation == 0)
		return 0;
	lease = &resource_rate_leases[handle.slot - 1];
	if (lease->tag != RESOURCE_RATE_LEASE_LIVE ||
	    lease->generation != handle.generation)
		return 0;
	return lease;
}

int resource_rate_lease_valid(struct resource_rate_lease_handle handle)
{
	int enabled = intr_save();
	int valid = resource_rate_lease_lookup(handle) != 0;

	intr_restore(enabled);
	return valid;
}

int resource_rate_account_configure(
	struct resource_account_handle handle,
	const struct resource_rate_profile *profiles, uint count)
{
	struct resource_rate_profile normalized[RESOURCE_RATE_LANE_CAP];
	struct resource_account *account;
	int enabled;

	if (count > RESOURCE_RATE_LANE_CAP ||
	    (count != 0 && profiles == 0))
		return -1;
	memset(normalized, 0, sizeof(normalized));
	for (uint lane = 0; lane < count; lane++) {
		if (!resource_rate_profile_valid(&profiles[lane]))
			return -1;
		normalized[lane] = profiles[lane];
	}
	enabled = intr_save();
	account = resource_account_lookup(handle);
	if (account == 0 || account->state != RESOURCE_ACCOUNT_ACTIVE) {
		intr_restore(enabled);
		return -1;
	}
	for (uint lane = 0; lane < RESOURCE_RATE_LANE_CAP; lane++)
		if (!resource_rate_state_idle(&account->rate_lanes[lane])) {
			intr_restore(enabled);
			return -1;
		}
	memset(account->rate_lanes, 0, sizeof(account->rate_lanes));
	for (uint lane = 0; lane < RESOURCE_RATE_LANE_CAP; lane++) {
		account->rate_lanes[lane].profile = normalized[lane];
		account->rate_lanes[lane].tokens =
			normalized[lane].burst;
	}
	intr_restore(enabled);
	return 0;
}

int resource_rate_global_configure(
	uint index, const struct resource_rate_profile *profile)
{
	struct resource_rate_state *state;
	int enabled;

	if (index >= RESOURCE_RATE_GLOBAL_CAP ||
	    !resource_rate_profile_valid(profile) || profile->burst == 0)
		return -1;
	enabled = intr_save();
	state = &resource_rate_globals[index];
	if (!resource_rate_state_idle(state)) {
		intr_restore(enabled);
		return -1;
	}
	memset(state, 0, sizeof(*state));
	state->profile = *profile;
	state->tokens = profile->burst;
	intr_restore(enabled);
	return 0;
}

static struct resource_rate_state *
resource_rate_endpoint_lookup(const struct resource_rate_endpoint *endpoint,
			      int admission,
			      struct resource_account **account_out)
{
	struct resource_account *account = 0;
	struct resource_rate_state *state;

	if (account_out != 0)
		*account_out = 0;
	if (endpoint == 0 || endpoint->amount == 0 ||
	    (endpoint->flags & ~RESOURCE_RATE_ENDPOINT_ALLOW_DEBT) != 0)
		return 0;
	if (endpoint->scope == RESOURCE_RATE_ACCOUNT) {
		if (endpoint->index >= RESOURCE_RATE_LANE_CAP)
			return 0;
		account = resource_account_lookup(endpoint->account);
		if (account == 0)
			return 0;
		state = &account->rate_lanes[endpoint->index];
		if (admission &&
		    account->state != RESOURCE_ACCOUNT_ACTIVE &&
		    (account->state != RESOURCE_ACCOUNT_CLOSING ||
		     (state->profile.flags &
		      RESOURCE_RATE_PROFILE_ALLOW_CLOSING) == 0))
			return 0;
		if (account_out != 0)
			*account_out = account;
	} else if (endpoint->scope == RESOURCE_RATE_GLOBAL) {
		if (endpoint->index >= RESOURCE_RATE_GLOBAL_CAP)
			return 0;
		state = &resource_rate_globals[endpoint->index];
	} else {
		return 0;
	}
	return state->profile.burst != 0 ? state : 0;
}

static int resource_rate_endpoints_distinct(
	const struct resource_rate_endpoint *endpoints, uint count)
{
	for (uint i = 0; i < count; i++)
		for (uint j = i + 1; j < count; j++) {
			if (endpoints[i].scope != endpoints[j].scope ||
			    endpoints[i].index != endpoints[j].index)
				continue;
			if (endpoints[i].scope == RESOURCE_RATE_GLOBAL ||
			    resource_account_handle_equal(
				    endpoints[i].account,
				    endpoints[j].account))
				return 0;
		}
	return 1;
}

static int resource_rate_lease_allocate(
	struct resource_rate_lease_handle *out,
	struct resource_rate_lease **lease_out)
{
	struct resource_rate_lease *lease;
	uint generation, index;

	index = resource_rate_lease_free_head;
	if (index == RESOURCE_RATE_LEASE_INDEX_NONE)
		return -1;
	if (index >= RESOURCE_RATE_LEASE_CAP)
		panic("rate lease freelist");
	lease = &resource_rate_leases[index];
	if (lease->tag != RESOURCE_RATE_LEASE_FREE ||
	    lease->generation == (uint)-1)
		panic("rate lease freelist");
	resource_rate_lease_free_head = lease->next_free;
	generation = lease->generation + 1;
	memset(lease, 0, sizeof(*lease));
	lease->tag = RESOURCE_RATE_LEASE_LIVE;
	lease->generation = generation;
	out->slot = index + 1;
	out->generation = generation;
	*lease_out = lease;
	return 0;
}

static void resource_rate_lease_release(
	struct resource_rate_lease *lease)
{
	uint index = (uint)(lease - resource_rate_leases);
	uint generation = lease->generation;

	if (index >= RESOURCE_RATE_LEASE_CAP ||
	    lease->tag != RESOURCE_RATE_LEASE_LIVE ||
	    (resource_rate_lease_free_head != RESOURCE_RATE_LEASE_INDEX_NONE &&
	     resource_rate_lease_free_head >= RESOURCE_RATE_LEASE_CAP))
		panic("rate lease release freelist");
	memset(lease, 0, sizeof(*lease));
	lease->generation = generation;
	if (generation == (uint)-1) {
		lease->tag = RESOURCE_RATE_LEASE_RETIRED;
		return;
	}
	lease->tag = RESOURCE_RATE_LEASE_FREE;
	lease->next_free = resource_rate_lease_free_head;
	resource_rate_lease_free_head = (uint16)index;
}

int resource_rate_reserve_many(
	const struct resource_rate_endpoint *endpoints, uint count,
	struct resource_rate_lease_handle *out)
{
	struct resource_rate_lease_entry
		entries[RESOURCE_RATE_BUNDLE_CAP];
	struct resource_rate_lease *lease;
	int enabled;

	if (out == 0 || endpoints == 0 || count == 0 ||
	    count > RESOURCE_RATE_BUNDLE_CAP ||
	    !resource_rate_endpoints_distinct(endpoints, count))
		return -1;
	*out = resource_rate_lease_none();
	memset(entries, 0, sizeof(entries));
	enabled = intr_save();
	for (uint i = 0; i < count; i++) {
		struct resource_rate_state *state =
			resource_rate_endpoint_lookup(
				&endpoints[i], 1, 0);

		if (state == 0)
			goto fail;
		entries[i].endpoint = endpoints[i];
		if (state->debt == 0 &&
		    state->tokens >= endpoints[i].amount) {
			uint64 leased;

			if (resource_u64_add(state->leased,
					     endpoints[i].amount,
					     &leased) < 0)
				goto fail;
			entries[i].leased_amount = endpoints[i].amount;
		} else if (endpoints[i].flags &
			   RESOURCE_RATE_ENDPOINT_ALLOW_DEBT) {
			uint64 pending;

			if (resource_u64_add(
				    state->pending_debt,
				    endpoints[i].amount,
				    &pending) < 0)
				goto fail;
			entries[i].debt_amount = endpoints[i].amount;
		} else {
			goto fail;
		}
	}
	if (resource_rate_lease_allocate(out, &lease) < 0)
		goto fail;
	lease->count = count;
	memmove(lease->entries, entries,
		sizeof(entries[0]) * count);
	for (uint i = 0; i < count; i++) {
		struct resource_rate_state *state =
			resource_rate_endpoint_lookup(
				&entries[i].endpoint, 0, 0);

		if (entries[i].leased_amount == 0)
			state->pending_debt += entries[i].debt_amount;
		else {
			state->tokens -= entries[i].leased_amount;
			state->leased += entries[i].leased_amount;
		}
	}
	intr_restore(enabled);
	return 0;

fail:
	intr_restore(enabled);
	return -1;
}

int resource_rate_lease_commit(
	struct resource_rate_lease_handle handle)
{
	struct resource_rate_lease *lease;
	int enabled = intr_save();

	lease = resource_rate_lease_lookup(handle);
	if (lease == 0) {
		intr_restore(enabled);
		return -1;
	}
	for (uint i = 0; i < lease->count; i++) {
		struct resource_rate_lease_entry *entry =
			&lease->entries[i];
		struct resource_rate_state *state =
			resource_rate_endpoint_lookup(
				&entry->endpoint, 0, 0);
		uint64 debt;

		if (state == 0 || state->leased < entry->leased_amount ||
		    state->pending_debt < entry->debt_amount ||
		    resource_u64_add(state->debt, entry->debt_amount,
				     &debt) < 0)
			panic("rate lease commit invariant");
	}
	for (uint i = 0; i < lease->count; i++) {
		struct resource_rate_lease_entry *entry =
			&lease->entries[i];
		struct resource_account *account = 0;
		struct resource_rate_state *state =
			resource_rate_endpoint_lookup(
				&entry->endpoint, 0, &account);

		state->leased -= entry->leased_amount;
		state->pending_debt -= entry->debt_amount;
		if (entry->debt_amount != 0) {
			if (state->debt == 0 &&
			    state->tokens >= entry->debt_amount)
				state->tokens -= entry->debt_amount;
			else
				state->debt += entry->debt_amount;
		}
		if (account != 0)
			resource_account_advance(account);
	}
	resource_rate_lease_release(lease);
	intr_restore(enabled);
	return 0;
}

void resource_rate_lease_cancel(
	struct resource_rate_lease_handle handle)
{
	struct resource_rate_lease *lease;
	int enabled = intr_save();

	lease = resource_rate_lease_lookup(handle);
	if (lease == 0) {
		intr_restore(enabled);
		return;
	}
	for (uint i = 0; i < lease->count; i++) {
		struct resource_rate_lease_entry *entry =
			&lease->entries[i];
		struct resource_rate_state *state =
			resource_rate_endpoint_lookup(
				&entry->endpoint, 0, 0);

		if (state == 0 || state->leased < entry->leased_amount ||
		    state->pending_debt < entry->debt_amount ||
		    state->tokens >
			    state->profile.burst - entry->leased_amount)
			panic("rate lease cancel invariant");
	}
	for (uint i = 0; i < lease->count; i++) {
		struct resource_rate_lease_entry *entry =
			&lease->entries[i];
		struct resource_account *account = 0;
		struct resource_rate_state *state =
			resource_rate_endpoint_lookup(
				&entry->endpoint, 0, &account);

		state->leased -= entry->leased_amount;
		state->pending_debt -= entry->debt_amount;
		state->tokens += entry->leased_amount;
		if (account != 0)
			resource_account_advance(account);
	}
	resource_rate_lease_release(lease);
	intr_restore(enabled);
}

int resource_rate_charge_many(
	const struct resource_rate_endpoint *endpoints, uint count)
{
	uchar use_token[RESOURCE_RATE_BUNDLE_CAP];
	int enabled;

	if (endpoints == 0 || count == 0 ||
	    count > RESOURCE_RATE_BUNDLE_CAP ||
	    !resource_rate_endpoints_distinct(endpoints, count))
		return -1;
	memset(use_token, 0, sizeof(use_token));
	enabled = intr_save();
	for (uint i = 0; i < count; i++) {
		struct resource_rate_state *state =
			resource_rate_endpoint_lookup(
				&endpoints[i], 0, 0);
		uint64 debt;

		if (state == 0)
			goto fail;
		if (state->debt == 0 &&
		    state->tokens >= endpoints[i].amount) {
			use_token[i] = 1;
			continue;
		}
		if ((endpoints[i].flags &
		     RESOURCE_RATE_ENDPOINT_ALLOW_DEBT) == 0)
			goto fail;
		if (resource_u64_add(state->debt, endpoints[i].amount,
				     &debt) < 0)
			goto fail;
	}
	for (uint i = 0; i < count; i++) {
		struct resource_rate_state *state =
			resource_rate_endpoint_lookup(
				&endpoints[i], 0, 0);

		if (use_token[i])
			state->tokens -= endpoints[i].amount;
		else
			state->debt += endpoints[i].amount;
	}
	intr_restore(enabled);
	return 0;

fail:
	intr_restore(enabled);
	return -1;
}

static uint64 resource_rate_refill_state(
	struct resource_rate_state *state)
{
	uint64 budget = state->profile.refill;
	uint64 paid = MIN(state->debt, budget);
	uint64 occupied;
	uint64 room;
	uint64 added;

	state->debt -= paid;
	budget -= paid;
	if (resource_u64_add(state->tokens, state->leased,
			     &occupied) < 0 ||
	    occupied > state->profile.burst)
		panic("rate refill capacity invariant");
	room = state->profile.burst - occupied;
	added = MIN(room, budget);
	state->tokens += added;
	return paid + added;
}

int resource_rate_account_refill(
	struct resource_account_handle handle, uint lane,
	uint64 *applied)
{
	struct resource_account *account;
	struct resource_rate_state *state;
	int enabled;

	if (lane >= RESOURCE_RATE_LANE_CAP || applied == 0)
		return -1;
	*applied = 0;
	enabled = intr_save();
	account = resource_account_lookup(handle);
	if (account == 0) {
		intr_restore(enabled);
		return -1;
	}
	state = &account->rate_lanes[lane];
	if (state->profile.burst == 0) {
		intr_restore(enabled);
		return -1;
	}
	*applied = resource_rate_refill_state(state);
	resource_account_advance(account);
	intr_restore(enabled);
	return 0;
}

int resource_rate_global_refill(uint index, uint64 *applied)
{
	struct resource_rate_state *state;
	int enabled;

	if (index >= RESOURCE_RATE_GLOBAL_CAP || applied == 0)
		return -1;
	*applied = 0;
	enabled = intr_save();
	state = &resource_rate_globals[index];
	if (state->profile.burst == 0) {
		intr_restore(enabled);
		return -1;
	}
	*applied = resource_rate_refill_state(state);
	intr_restore(enabled);
	return 0;
}

static void resource_rate_snapshot_copy(
	const struct resource_rate_state *state,
	struct resource_rate_snapshot *snapshot)
{
	snapshot->tokens = state->tokens;
	snapshot->leased = state->leased;
	snapshot->debt = state->debt;
	snapshot->pending_debt = state->pending_debt;
	snapshot->burst = state->profile.burst;
	snapshot->refill = state->profile.refill;
	snapshot->flags = state->profile.flags;
}

int resource_rate_account_snapshot(
	struct resource_account_handle handle, uint lane,
	struct resource_rate_snapshot *snapshot)
{
	struct resource_account *account;
	int enabled;

	if (lane >= RESOURCE_RATE_LANE_CAP || snapshot == 0)
		return -1;
	memset(snapshot, 0, sizeof(*snapshot));
	enabled = intr_save();
	account = resource_account_lookup(handle);
	if (account == 0 ||
	    account->rate_lanes[lane].profile.burst == 0) {
		intr_restore(enabled);
		return -1;
	}
	resource_rate_snapshot_copy(
		&account->rate_lanes[lane], snapshot);
	intr_restore(enabled);
	return 0;
}

int resource_rate_global_snapshot(
	uint index, struct resource_rate_snapshot *snapshot)
{
	int enabled;

	if (index >= RESOURCE_RATE_GLOBAL_CAP || snapshot == 0)
		return -1;
	memset(snapshot, 0, sizeof(*snapshot));
	enabled = intr_save();
	if (resource_rate_globals[index].profile.burst == 0) {
		intr_restore(enabled);
		return -1;
	}
	resource_rate_snapshot_copy(
		&resource_rate_globals[index], snapshot);
	intr_restore(enabled);
	return 0;
}

int resource_rate_account_idle(
	struct resource_account_handle handle)
{
	struct resource_account *account;
	int idle = 1;
	int enabled = intr_save();

	account = resource_account_lookup(handle);
	if (account == 0) {
		intr_restore(enabled);
		return 0;
	}
	for (uint lane = 0; lane < RESOURCE_RATE_LANE_CAP; lane++)
		if (!resource_rate_state_idle(
			    &account->rate_lanes[lane])) {
			idle = 0;
			break;
		}
	intr_restore(enabled);
	return idle;
}
