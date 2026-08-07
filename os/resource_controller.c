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
};
static struct resource_policy resource_policies[RESOURCE_KIND_COUNT];
static struct resource_account resource_accounts[RESOURCE_ACCOUNT_CAP];
static uint64 resource_account_generations[RESOURCE_ACCOUNT_CAP];
static uchar resource_account_generation_exhausted[RESOURCE_ACCOUNT_CAP];
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

static int resource_pair_can_delta(uint64 used, uint64 pending, uint64 amount,
				   int used_delta, int pending_delta,
				   uint64 limit, int check_limit)
{
	uint64 total;

	if ((used_delta < 0 && used < amount) ||
	    (pending_delta < 0 && pending < amount))
		return 0;
	if (used_delta > 0) {
		if (resource_u64_add(used, amount, &used) < 0)
			return 0;
	} else if (used_delta < 0) {
		used -= amount;
	}
	if (pending_delta > 0) {
		if (resource_u64_add(pending, amount, &pending) < 0)
			return 0;
	} else if (pending_delta < 0) {
		pending -= amount;
	}
	return resource_u64_add(used, pending, &total) == 0 &&
	       (!check_limit || total <= limit);
}

static void resource_pair_publish_delta(uint64 *used, uint64 *pending,
					uint64 amount, int used_delta,
					int pending_delta)
{
	if (used_delta > 0)
		*used += amount;
	else if (used_delta < 0)
		*used -= amount;
	if (pending_delta > 0)
		*pending += amount;
	else if (pending_delta < 0)
		*pending -= amount;
}

static int resource_vector_delta(
	struct resource_account *account,
	enum resource_charge_class charge_class,
	const uint64 amounts[RESOURCE_KIND_COUNT], uint kind_mask,
	int used_delta, int pending_delta)
{
	uint selected = kind_mask;
	int admission = used_delta + pending_delta > 0;

	if (account == 0 || amounts == 0 || kind_mask == 0 ||
	    (kind_mask >> RESOURCE_KIND_COUNT) != 0 ||
	    !resource_charge_class_valid(charge_class) ||
	    (admission && (account->charge_grants &
			  RESOURCE_CHARGE_GRANT(charge_class)) == 0))
		return 0;
	while (selected != 0) {
		uint kind = resource_kind_first(selected);
		struct resource_policy *policy = &resource_policies[kind];
		uint64 class_used, class_pending, class_limit;

		selected &= selected - 1;
		if (charge_class == RESOURCE_CHARGE_ORDINARY) {
			class_used = policy->ordinary_used;
			class_pending = policy->ordinary_pending;
			class_limit = policy->ordinary_limit;
		} else {
			class_used = policy->reserved_used;
			class_pending = policy->reserved_pending;
			class_limit = policy->reserved_limit;
		}
		if ((admission && !policy->configured) || amounts[kind] == 0 ||
		    !resource_pair_can_delta(
			    account->used[charge_class][kind],
			    account->pending[charge_class][kind], amounts[kind],
			    used_delta, pending_delta,
			    account->limits.class_limit[charge_class][kind],
			    admission) ||
		    !resource_pair_can_delta(policy->used, policy->pending,
			    amounts[kind], used_delta, pending_delta,
			    policy->capacity, admission) ||
		    !resource_pair_can_delta(class_used, class_pending,
			    amounts[kind], used_delta, pending_delta, class_limit,
			    admission))
			return 0;
	}
	selected = kind_mask;
	while (selected != 0) {
		uint kind = resource_kind_first(selected);
		struct resource_policy *policy = &resource_policies[kind];
		uint64 *class_used, *class_pending;

		selected &= selected - 1;
		class_used = charge_class == RESOURCE_CHARGE_ORDINARY ?
			&policy->ordinary_used : &policy->reserved_used;
		class_pending = charge_class == RESOURCE_CHARGE_ORDINARY ?
			&policy->ordinary_pending : &policy->reserved_pending;
		resource_pair_publish_delta(
			&account->used[charge_class][kind],
			&account->pending[charge_class][kind], amounts[kind],
			used_delta, pending_delta);
		resource_pair_publish_delta(&policy->used, &policy->pending,
			amounts[kind], used_delta, pending_delta);
		resource_pair_publish_delta(class_used, class_pending,
			amounts[kind], used_delta, pending_delta);
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
	    !resource_vector_delta(
		    account, charge_class, amounts, kind_mask, 0, 1)) {
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
	if (!resource_vector_delta(
		    account, reservation->charge_class, reservation->amounts,
		    reservation->kind_mask, 1, -1))
		panic("resource reservation commit");
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
	if (!resource_vector_delta(
		    account, reservation->charge_class, reservation->amounts,
		    reservation->kind_mask, 0, -1))
		panic("resource reservation cancel");
	reservation->active = 0;
	resource_account_advance(account);
	intr_restore(enabled);
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
	if (!resource_vector_delta(
		    account, charge_class, amounts, kind_mask, -1, 0)) {
		intr_restore(enabled);
		return -1;
	}
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
	    !resource_vector_delta(
		    account, charge_class, amounts, kind_mask, 1, 0)) {
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
	if (!resource_vector_delta(
		    source, from_charge_class, amounts, kind_mask, -1, 0))
		panic("resource transfer source");
	for (uint selected = kind_mask; selected != 0;
	     selected &= selected - 1) {
		uint kind = resource_kind_first(selected);
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
