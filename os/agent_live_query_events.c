#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_live_query_events.h"
#include "agent_metadata_catalog.h"
#include "agent_metadata_query.h"
#include "defs.h"

extern struct proc pool[NPROC];

#define AGENT_LIVE_QUERY_TOMBSTONE_CAP AGENT_FILE_META_MAX
#define AGENT_LIVE_QUERY_DOMAIN_CAP (WORKFLOW_LIFECYCLE_CAP + 1)
#define AGENT_LIVE_QUERY_DEFAULT_DRAIN 16U
#define AGENT_LIVE_QUERY_SUBSCRIPTION_CAP 32U
#define AGENT_LIVE_QUERY_FIELD_COUNT 9U
#define AGENT_LIVE_QUERY_PREDICATE_BYTES \
	(AGENT_FILE_NAME_SIZE + AGENT_FILE_LOGICAL_SIZE + \
	 AGENT_FILE_PROJECT_SIZE + AGENT_FILE_WORKFLOW_SIZE + \
	 4 * AGENT_FILE_FIELD_SIZE + AGENT_FILE_SUMMARY_SIZE)
#define AGENT_LIVE_QUERY_PREDICATE_ARENA_BYTES \
	(AGENT_LIVE_QUERY_SUBSCRIPTION_CAP * AGENT_LIVE_QUERY_PREDICATE_BYTES)
#define AGENT_LIVE_QUERY_OFFSET_NONE ((ushort)~0U)

struct agent_live_query_tombstone {
	int used;
	struct workflow_lifecycle_key key;
	uint scope_id;
	uint dev;
	uint inum;
	uint incarnation;
};

struct agent_live_query_content {
	int used;
	struct agent_file_content_receipt receipt;
	uint64 generation;
};

struct agent_live_query_domain_resync {
	int used;
	struct workflow_lifecycle_key key;
	uint scope_id;
	uint64 generation;
};

struct agent_live_query_proc_resync {
	int pending;
	struct proc *target;
	uint64 control_id;
	struct workflow_lifecycle_key key;
	uint scope_id;
	uint event_mask;
	uint64 generation;
};

struct agent_live_query_subscription {
	int used;
	struct proc *target;
	uint64 control_id;
	struct workflow_lifecycle_key key;
	uint scope_id;
	uint cold_slot;
	uint64 watch_id;
	uint64 initial_generation;
	uint64 catalog_generation;
	uint64 resync_generation;
	uint predicate_offset;
	ushort predicate_bytes;
	ushort value_offset[AGENT_LIVE_QUERY_FIELD_COUNT];
};

struct agent_live_query_cold_token {
	uint64 watch_id;
	uint64 initial_generation;
};

_Static_assert(sizeof(struct agent_live_query_cold_token) <=
	       AGENT_WATCH_FILTER_SIZE,
	       "live query token must fit the existing watch slot");

static struct agent_live_query_tombstone
	agent_live_query_tombstones[AGENT_LIVE_QUERY_TOMBSTONE_CAP];
static struct agent_live_query_content
	agent_live_query_content_pending[AGENT_FILE_META_MAX];
static struct agent_live_query_domain_resync
	agent_live_query_domain_resync[AGENT_LIVE_QUERY_DOMAIN_CAP];
static struct agent_live_query_proc_resync
	agent_live_query_proc_resync[NPROC];
static struct agent_live_query_subscription
	agent_live_query_subscriptions[AGENT_LIVE_QUERY_SUBSCRIPTION_CAP];
static char
	agent_live_query_predicate_arena[AGENT_LIVE_QUERY_PREDICATE_ARENA_BYTES];
static char agent_live_query_predicate_scratch[
	AGENT_LIVE_QUERY_PREDICATE_BYTES];
static uint agent_live_query_tombstone_cursor;
static uint agent_live_query_content_cursor;
static uint agent_live_query_predicate_arena_used;
static uint64 agent_live_query_next_watch_id;
static uint64 agent_live_query_global_resync_generation;
static uchar agent_live_query_file_watch_present[NPROC];
static uint agent_live_query_file_watch_processes;

static int
agent_live_query_key_equal(struct workflow_lifecycle_key left,
			   struct workflow_lifecycle_key right)
{
	return workflow_lifecycle_key_equal(left, right);
}

static int
agent_live_query_domain_valid(struct workflow_lifecycle_key key, uint scope_id)
{
	if (!agent_object_scope_valid(scope_id))
		return 0;
	if (scope_id == VFS_SCOPE_SYSTEM)
		return workflow_lifecycle_key_valid(key) ||
		       agent_live_query_key_equal(key, workflow_lifecycle_none());
	return workflow_lifecycle_key_valid(key);
}

static int
agent_live_query_domain_equal(struct workflow_lifecycle_key left_key,
			      uint left_scope,
			      struct workflow_lifecycle_key right_key,
			      uint right_scope)
{
	return left_scope == right_scope &&
	       agent_live_query_key_equal(left_key, right_key);
}

static struct workflow_lifecycle_key
agent_live_query_proc_key(const struct proc *p)
{
	struct workflow_lifecycle_key key = workflow_lifecycle_none();

	if (p != 0 && p->workflow_lifecycle_charged) {
		key.id = p->workflow_lifecycle_id;
		key.generation = p->workflow_lifecycle_generation;
	}
	return key;
}

static int
agent_live_query_target_valid(struct proc *p,
			      struct workflow_lifecycle_key *key,
			      uint *scope_id)
{
	struct workflow_lifecycle_key current;
	uint scope;

	if (p == 0 || p < pool || p >= &pool[NPROC] ||
	    !proc_teardown_live(p) || !p->is_agent ||
	    p->agent_control_id == 0 || p->agent_ipc_observe_cold == 0 ||
	    !agent_identity_has_cap(p, AGENT_CAP_WATCH))
		return 0;
	current = agent_live_query_proc_key(p);
	scope = agent_identity_proc_scope(p);
	if (!workflow_lifecycle_key_valid(current) ||
	    !agent_scope_valid(scope))
		return 0;
	if (key != 0)
		*key = current;
	if (scope_id != 0)
		*scope_id = scope;
	return 1;
}

static int
agent_live_query_target_visible(struct proc *p,
				struct workflow_lifecycle_key object_key,
				uint object_scope)
{
	struct workflow_lifecycle_key target_key;
	uint target_scope;

	if (!agent_live_query_target_valid(p, &target_key, &target_scope))
		return 0;
	if (object_scope == VFS_SCOPE_SYSTEM)
		return 1;
	return target_scope == object_scope &&
	       agent_live_query_key_equal(target_key, object_key);
}

static int
agent_live_query_has_watch_type(struct proc *p, int event_type)
{
	struct agent_ipc_observe_cold_state *cold;

	if (p == 0 || (cold = p->agent_ipc_observe_cold) == 0)
		return 0;
	for (int slot = 0; slot < AGENT_WATCH_MAX; slot++)
		if (cold->watch_valid[slot] &&
		    (cold->watch_event_type[slot] == event_type ||
		     (event_type == AGENT_EVENT_FILE_STATUS &&
		      cold->watch_event_type[slot] == AGENT_EVENT_NONE)))
			return 1;
	return 0;
}

static int
agent_live_query_has_file_watch(struct proc *p)
{
	return agent_live_query_has_watch_type(p, AGENT_EVENT_FILE_STATUS) ||
	       agent_live_query_has_watch_type(p, AGENT_EVENT_FILE_QUERY);
}

static void
agent_live_query_file_watch_refresh_locked(struct proc *p)
{
	uint slot;
	int present;

	if (intr_get())
		panic("Agent live query watch cache unlocked");
	if (p == 0 || p < pool || p >= &pool[NPROC])
		return;
	slot = (uint)(p - pool);
	present = agent_live_query_has_file_watch(p);
	if (present && !agent_live_query_file_watch_present[slot]) {
		agent_live_query_file_watch_present[slot] = 1;
		agent_live_query_file_watch_processes++;
	} else if (!present && agent_live_query_file_watch_present[slot]) {
		agent_live_query_file_watch_present[slot] = 0;
		if (agent_live_query_file_watch_processes == 0)
			panic("Agent live query watch cache underflow");
		agent_live_query_file_watch_processes--;
	}
}

static void
agent_live_query_file_watch_clear_locked(struct proc *p)
{
	uint slot;

	if (intr_get())
		panic("Agent live query watch clear unlocked");
	if (p == 0 || p < pool || p >= &pool[NPROC])
		return;
	slot = (uint)(p - pool);
	if (!agent_live_query_file_watch_present[slot])
		return;
	agent_live_query_file_watch_present[slot] = 0;
	if (agent_live_query_file_watch_processes == 0)
		panic("Agent live query watch clear underflow");
	agent_live_query_file_watch_processes--;
}

void
agent_live_query_file_watch_changed(struct proc *p)
{
	int enabled = intr_save();

	agent_live_query_file_watch_refresh_locked(p);
	intr_restore(enabled);
}

static int
agent_live_query_file_watches_present(void)
{
	int enabled = intr_save();
	int present = agent_live_query_file_watch_processes != 0;

	intr_restore(enabled);
	return present;
}

struct agent_live_query_field {
	ushort query_offset;
	ushort meta_offset;
	ushort size;
	ushort contains;
};

#define AGENT_LIVE_QUERY_FIELD(member, contains_value) \
	{ (ushort)__builtin_offsetof(struct agent_file_query, member), \
	  (ushort)__builtin_offsetof(struct agent_file_meta, member), \
	  (ushort)sizeof(((struct agent_file_query *)0)->member), \
	  contains_value }
static const struct agent_live_query_field agent_live_query_fields[] = {
	AGENT_LIVE_QUERY_FIELD(physical_name, 0),
	AGENT_LIVE_QUERY_FIELD(logical_path, 0),
	AGENT_LIVE_QUERY_FIELD(project, 0),
	AGENT_LIVE_QUERY_FIELD(workflow, 0),
	AGENT_LIVE_QUERY_FIELD(run_id, 0),
	AGENT_LIVE_QUERY_FIELD(status, 0),
	AGENT_LIVE_QUERY_FIELD(stage, 0),
	AGENT_LIVE_QUERY_FIELD(kind, 0),
	{ (ushort)__builtin_offsetof(
		  struct agent_file_query, summary_contains),
	  (ushort)__builtin_offsetof(struct agent_file_meta, summary),
	  (ushort)sizeof(((struct agent_file_query *)0)->summary_contains), 1 },
};
#undef AGENT_LIVE_QUERY_FIELD

_Static_assert(NELEM(agent_live_query_fields) ==
	       AGENT_LIVE_QUERY_FIELD_COUNT,
	       "live query predicate field count");

static int
agent_live_query_subscription_valid(
	const struct agent_live_query_subscription *subscription)
{
	struct agent_live_query_cold_token token;
	struct agent_ipc_observe_cold_state *cold;
	struct workflow_lifecycle_key key;
	uint scope_id;

	if (subscription == 0 || !subscription->used ||
	    !agent_live_query_target_valid(
		    subscription->target, &key, &scope_id) ||
	    subscription->target->agent_control_id != subscription->control_id ||
	    !agent_live_query_key_equal(key, subscription->key) ||
	    scope_id != subscription->scope_id ||
	    subscription->cold_slot >= AGENT_WATCH_MAX ||
	    subscription->predicate_offset + subscription->predicate_bytes >
		    agent_live_query_predicate_arena_used)
		return 0;
	cold = subscription->target->agent_ipc_observe_cold;
	if (!cold->watch_valid[subscription->cold_slot] ||
	    cold->watch_event_type[subscription->cold_slot] !=
		    AGENT_EVENT_FILE_QUERY)
		return 0;
	memmove(&token, cold->watch_filter[subscription->cold_slot],
		sizeof(token));
	return token.watch_id == subscription->watch_id &&
	       token.initial_generation == subscription->initial_generation;
}

static void
agent_live_query_predicate_remove_locked(uint offset, uint bytes)
{
	if (intr_get())
		panic("Agent live query predicate unlocked");
	if (bytes == 0)
		return;
	if (offset > agent_live_query_predicate_arena_used ||
	    bytes > agent_live_query_predicate_arena_used - offset)
		panic("Agent live query predicate range");
	memmove(agent_live_query_predicate_arena + offset,
		agent_live_query_predicate_arena + offset + bytes,
		agent_live_query_predicate_arena_used - offset - bytes);
	agent_live_query_predicate_arena_used -= bytes;
	memset(agent_live_query_predicate_arena +
		       agent_live_query_predicate_arena_used,
	       0, bytes);
	for (uint slot = 0; slot < AGENT_LIVE_QUERY_SUBSCRIPTION_CAP; slot++) {
		struct agent_live_query_subscription *subscription =
			&agent_live_query_subscriptions[slot];

		if (subscription->used && subscription->predicate_offset > offset)
			subscription->predicate_offset -= bytes;
	}
}

static void
agent_live_query_subscription_remove_locked(
	struct agent_live_query_subscription *subscription, int clear_cold)
{
	struct proc *target;
	uint cold_slot;
	uint offset;
	uint bytes;

	if (intr_get())
		panic("Agent live query subscription unlocked");
	if (subscription == 0 || !subscription->used)
		return;
	target = subscription->target;
	cold_slot = subscription->cold_slot;
	offset = subscription->predicate_offset;
	bytes = subscription->predicate_bytes;
	if (clear_cold && target != 0 && target >= pool &&
	    target < &pool[NPROC] && target->agent_ipc_observe_cold != 0 &&
	    cold_slot < AGENT_WATCH_MAX) {
		struct agent_ipc_observe_cold_state *cold =
			target->agent_ipc_observe_cold;

		cold->watch_valid[cold_slot] = 0;
		cold->watch_event_type[cold_slot] = AGENT_EVENT_NONE;
		memset(cold->watch_filter[cold_slot], 0,
		       sizeof(cold->watch_filter[cold_slot]));
		if (target->agent_watch_count > 0)
			target->agent_watch_count--;
	}
	memset(subscription, 0, sizeof(*subscription));
	agent_live_query_predicate_remove_locked(offset, bytes);
}

static int
agent_live_query_predicate_compile(const struct agent_file_query *query,
				   char *compiled, ushort *compiled_bytes,
				   ushort value_offset[
					   AGENT_LIVE_QUERY_FIELD_COUNT])
{
	uint used = 0;

	if (query == 0 || compiled == 0 || compiled_bytes == 0 ||
	    value_offset == 0 ||
	    (query->flags & ~(AGENT_FILE_QUERY_USE_INDEX |
			       AGENT_FILE_QUERY_SCAN)) != 0)
		return AGENT_STATUS_BAD_PARAM;
	memset(compiled, 0, AGENT_LIVE_QUERY_PREDICATE_BYTES);
	for (uint field = 0; field < AGENT_LIVE_QUERY_FIELD_COUNT; field++) {
		const struct agent_live_query_field *descriptor =
			&agent_live_query_fields[field];
		const char *value = (const char *)query +
				    descriptor->query_offset;
		uint bytes;

		value_offset[field] = AGENT_LIVE_QUERY_OFFSET_NONE;
		if (value[descriptor->size - 1] != 0)
			return AGENT_STATUS_BAD_PARAM;
		if (value[0] == 0)
			continue;
		bytes = strlen(value) + 1;
		if (bytes > descriptor->size ||
		    used + bytes > AGENT_LIVE_QUERY_PREDICATE_BYTES)
			return AGENT_STATUS_BAD_PARAM;
		value_offset[field] = (ushort)used;
		memmove(compiled + used, value, bytes);
		used += bytes;
	}
	*compiled_bytes = (ushort)used;
	return AGENT_STATUS_OK;
}

static int
agent_live_query_subscription_matches(
	const struct agent_live_query_subscription *subscription,
	uint owner_scope, const struct agent_file_meta *meta)
{
	if (!agent_live_query_subscription_valid(subscription) || meta == 0 ||
	    !meta->used || !agent_object_scope_visible(
			  subscription->scope_id, owner_scope))
		return 0;
	for (uint field = 0; field < AGENT_LIVE_QUERY_FIELD_COUNT; field++) {
		const struct agent_live_query_field *descriptor =
			&agent_live_query_fields[field];
		ushort relative = subscription->value_offset[field];
		const char *value;
		const char *actual;

		if (relative == AGENT_LIVE_QUERY_OFFSET_NONE)
			continue;
		if (relative >= subscription->predicate_bytes)
			return 0;
		value = agent_live_query_predicate_arena +
			subscription->predicate_offset + relative;
		actual = (const char *)meta + descriptor->meta_offset;
		if (descriptor->contains) {
			if (!agent_metadata_catalog_field_contains(actual, value))
				return 0;
		} else if (strncmp(value, actual, descriptor->size) != 0) {
			return 0;
		}
	}
	return 1;
}

static struct agent_live_query_subscription *
agent_live_query_subscription_find(struct proc *target, uint64 watch_id)
{
	for (uint slot = 0; slot < AGENT_LIVE_QUERY_SUBSCRIPTION_CAP; slot++) {
		struct agent_live_query_subscription *subscription =
			&agent_live_query_subscriptions[slot];

		if (subscription->used && subscription->target == target &&
		    subscription->watch_id == watch_id)
			return subscription;
	}
	return 0;
}

static uint64
agent_live_query_watch_id_alloc_locked(void)
{
	for (uint attempt = 0; attempt <= AGENT_LIVE_QUERY_SUBSCRIPTION_CAP;
	     attempt++) {
		uint64 candidate = ++agent_live_query_next_watch_id;
		int collision = 0;

		if (candidate == 0)
			candidate = ++agent_live_query_next_watch_id;
		for (uint slot = 0; slot < AGENT_LIVE_QUERY_SUBSCRIPTION_CAP;
		     slot++)
			if (agent_live_query_subscriptions[slot].used &&
			    agent_live_query_subscriptions[slot].watch_id == candidate) {
				collision = 1;
				break;
			}
		if (!collision)
			return candidate;
	}
	return 0;
}

static void
agent_live_query_hex(char *out, uint64 value, uint digits)
{
	static const char hex[] = "0123456789abcdef";

	for (uint i = 0; i < digits; i++) {
		uint shift = (digits - i - 1) * 4;

		out[i] = hex[(value >> shift) & 0xf];
	}
}

static void
agent_live_query_payload(char payload[AGENT_EVENT_PAYLOAD_SIZE], int change,
			 struct workflow_lifecycle_key key)
{
	const char *prefix;
	uint offset;

	switch (change) {
	case AGENT_LIVE_QUERY_ENTER:
		prefix = "change=ENTER;lc=";
		break;
	case AGENT_LIVE_QUERY_UPDATE:
		prefix = "change=UPDATE;lc=";
		break;
	case AGENT_LIVE_QUERY_LEAVE:
		prefix = "change=LEAVE;lc=";
		break;
	default:
		prefix = "change=RESYNC_REQUIRED;lc=";
		break;
	}
	memset(payload, 0, AGENT_EVENT_PAYLOAD_SIZE);
	safestrcpy(payload, prefix, AGENT_EVENT_PAYLOAD_SIZE);
	offset = strlen(payload);
	agent_live_query_hex(payload + offset, key.id, 8);
	offset += 8;
	payload[offset++] = ':';
	agent_live_query_hex(payload + offset, key.generation, 16);
	offset += 16;
	payload[offset] = 0;
}

static int
agent_live_query_meta_contains(const struct agent_file_meta *meta,
			       const char *needle)
{
	if (meta == 0 || !meta->used)
		return 0;
	return agent_metadata_catalog_field_contains(meta->physical_name, needle) ||
	       agent_metadata_catalog_field_contains(meta->logical_path, needle) ||
	       agent_metadata_catalog_field_contains(meta->project, needle) ||
	       agent_metadata_catalog_field_contains(meta->workflow, needle) ||
	       agent_metadata_catalog_field_contains(meta->run_id, needle) ||
	       agent_metadata_catalog_field_contains(meta->stage, needle) ||
	       agent_metadata_catalog_field_contains(meta->kind, needle) ||
	       agent_metadata_catalog_field_contains(meta->status, needle) ||
	       agent_metadata_catalog_field_contains(meta->summary, needle);
}

static int
agent_live_query_watch_matches(struct proc *target, uint owner_scope,
			       const char *filter,
			       const struct agent_file_meta *meta)
{
	struct agent_file_query query;
	char parsed[AGENT_WATCH_FILTER_SIZE];
	uint requester_scope = agent_identity_proc_scope(target);

	if (meta == 0 || !meta->used ||
	    !agent_object_scope_visible(requester_scope, owner_scope))
		return 0;
	if (filter == 0 || filter[0] == 0)
		return 1;
	safestrcpy(parsed, filter, sizeof(parsed));
	if (agent_metadata_query_from_payload(&query, parsed) == 0)
		return agent_metadata_query_matches(
			requester_scope, owner_scope, &query, meta);
	return agent_live_query_meta_contains(meta, filter);
}

static uint
agent_live_query_target_changes(struct proc *target, uint owner_scope,
				const struct agent_file_meta *before,
				const struct agent_file_meta *after)
{
	struct agent_ipc_observe_cold_state *cold =
		target->agent_ipc_observe_cold;
	uint changes = 0;

	for (int slot = 0; slot < AGENT_WATCH_MAX; slot++) {
		int before_matches;
		int after_matches;
		int change;

		if (!cold->watch_valid[slot] ||
		    (cold->watch_event_type[slot] != AGENT_EVENT_NONE &&
		     cold->watch_event_type[slot] != AGENT_EVENT_FILE_STATUS))
			continue;
		before_matches = agent_live_query_watch_matches(
			target, owner_scope, cold->watch_filter[slot], before);
		after_matches = agent_live_query_watch_matches(
			target, owner_scope, cold->watch_filter[slot], after);
		if (!before_matches && after_matches)
			change = AGENT_LIVE_QUERY_ENTER;
		else if (before_matches && after_matches)
			change = AGENT_LIVE_QUERY_UPDATE;
		else if (before_matches)
			change = AGENT_LIVE_QUERY_LEAVE;
		else
			continue;
		changes |= 1U << change;
	}
	return changes;
}

static uint
agent_live_query_typed_target_changes(
	struct proc *target, uint owner_scope,
	const struct agent_file_meta *before,
	const struct agent_file_meta *after)
{
	uint changes = 0;

	for (uint slot = 0; slot < AGENT_LIVE_QUERY_SUBSCRIPTION_CAP; slot++) {
		struct agent_live_query_subscription *subscription =
			&agent_live_query_subscriptions[slot];
		int before_matches;
		int after_matches;
		int change;

		if (!subscription->used || subscription->target != target)
			continue;
		if (!agent_live_query_subscription_valid(subscription)) {
			agent_live_query_subscription_remove_locked(subscription, 0);
			continue;
		}
		before_matches = agent_live_query_subscription_matches(
			subscription, owner_scope, before);
		after_matches = agent_live_query_subscription_matches(
			subscription, owner_scope, after);
		if (!before_matches && after_matches)
			change = AGENT_LIVE_QUERY_ENTER;
		else if (before_matches && after_matches)
			change = AGENT_LIVE_QUERY_UPDATE;
		else if (before_matches)
			change = AGENT_LIVE_QUERY_LEAVE;
		else
			continue;
		changes |= 1U << change;
	}
	return changes;
}

static int
agent_live_query_proc_resync_valid(
	struct agent_live_query_proc_resync *pending, struct proc *target)
{
	struct workflow_lifecycle_key key;
	uint scope_id;

	return pending->pending && pending->target == target &&
	       pending->event_mask != 0 &&
	       target != 0 && target >= pool && target < &pool[NPROC] &&
	       target->agent_control_id == pending->control_id &&
	       agent_live_query_target_valid(target, &key, &scope_id) &&
	       scope_id == pending->scope_id &&
	       agent_live_query_key_equal(key, pending->key);
}

static void
agent_live_query_proc_resync_clear(struct agent_live_query_proc_resync *pending)
{
	memset(pending, 0, sizeof(*pending));
}

static void
agent_live_query_proc_resync_mark_locked(struct proc *target, uint64 generation,
					 uint event_mask)
{
	struct agent_live_query_proc_resync *pending;
	struct workflow_lifecycle_key key;
	uint scope_id;
	uint available = 0;
	uint slot;

	if (intr_get())
		panic("Agent live query resync unlocked");
	if (!agent_live_query_target_valid(target, &key, &scope_id))
		return;
	if ((event_mask & (1U << AGENT_EVENT_FILE_STATUS)) != 0 &&
	    agent_live_query_has_watch_type(target, AGENT_EVENT_FILE_STATUS))
		available |= 1U << AGENT_EVENT_FILE_STATUS;
	if ((event_mask & (1U << AGENT_EVENT_FILE_QUERY)) != 0 &&
	    agent_live_query_has_watch_type(target, AGENT_EVENT_FILE_QUERY))
		available |= 1U << AGENT_EVENT_FILE_QUERY;
	if (available == 0)
		return;
	slot = target - pool;
	pending = &agent_live_query_proc_resync[slot];
	if (!agent_live_query_proc_resync_valid(pending, target)) {
		agent_live_query_proc_resync_clear(pending);
		pending->pending = 1;
		pending->target = target;
		pending->control_id = target->agent_control_id;
		pending->key = key;
		pending->scope_id = scope_id;
	}
	pending->event_mask |= available;
	if (generation > pending->generation)
		pending->generation = generation;
	if ((available & (1U << AGENT_EVENT_FILE_QUERY)) != 0)
		for (uint subscription_slot = 0;
		     subscription_slot < AGENT_LIVE_QUERY_SUBSCRIPTION_CAP;
		     subscription_slot++) {
			struct agent_live_query_subscription *subscription =
				&agent_live_query_subscriptions[subscription_slot];

			if (subscription->used && subscription->target == target &&
			    generation > subscription->resync_generation)
				subscription->resync_generation = generation;
		}
}

static int
agent_live_query_proc_resync_flush(struct proc *target)
{
	struct agent_live_query_proc_resync snapshot;
	struct agent_live_query_proc_resync *pending;
	char payload[AGENT_EVENT_PAYLOAD_SIZE];
	uint slot;
	int delivered = 0;
	int failed = 0;
	int enabled;

	if (target == 0 || target < pool || target >= &pool[NPROC])
		return 0;
	slot = target - pool;
	enabled = intr_save();
	pending = &agent_live_query_proc_resync[slot];
	if (!agent_live_query_proc_resync_valid(pending, target)) {
		agent_live_query_proc_resync_clear(pending);
		intr_restore(enabled);
		return 0;
	}
	snapshot = *pending;
	intr_restore(enabled);
	agent_live_query_payload(
		payload, AGENT_LIVE_QUERY_RESYNC_REQUIRED, snapshot.key);
	static const int event_types[] = {
		AGENT_EVENT_FILE_STATUS,
		AGENT_EVENT_FILE_QUERY,
	};
	for (uint event_index = 0; event_index < NELEM(event_types);
	     event_index++) {
		int event_type = event_types[event_index];
		uint bit = 1U << event_type;
		int result;

		if ((snapshot.event_mask & bit) == 0)
			continue;
		enabled = intr_save();
		pending = &agent_live_query_proc_resync[slot];
		if (!agent_live_query_proc_resync_valid(pending, target) ||
		    pending->control_id != snapshot.control_id ||
		    !agent_live_query_key_equal(pending->key, snapshot.key) ||
		    pending->scope_id != snapshot.scope_id) {
			intr_restore(enabled);
			continue;
		}
		if (!agent_live_query_has_watch_type(target, event_type)) {
			pending->event_mask &= ~bit;
			if (pending->event_mask == 0)
				agent_live_query_proc_resync_clear(pending);
			intr_restore(enabled);
			continue;
		}
		intr_restore(enabled);
		result = agent_ipc_deliver_live_event(
			target, 0, event_type, 0, snapshot.generation,
			payload, 1);
		if (result < 0) {
			failed = 1;
			continue;
		}
		if (result > 0)
			delivered += result;
		enabled = intr_save();
		pending = &agent_live_query_proc_resync[slot];
		if (pending->pending && pending->target == snapshot.target &&
		    pending->control_id == snapshot.control_id &&
		    agent_live_query_key_equal(pending->key, snapshot.key) &&
		    pending->scope_id == snapshot.scope_id &&
		    pending->generation <= snapshot.generation)
			pending->event_mask &= ~bit;
		if (pending->pending && pending->event_mask == 0)
			agent_live_query_proc_resync_clear(pending);
		intr_restore(enabled);
	}
	return failed ? -1 : delivered;
}

static uint64
agent_live_query_domain_generation_locked(struct workflow_lifecycle_key key,
					  uint scope_id)
{
	uint64 generation = agent_live_query_global_resync_generation;

	if (intr_get())
		panic("Agent live query domain unlocked");
	for (uint slot = 0; slot < AGENT_LIVE_QUERY_DOMAIN_CAP; slot++) {
		struct agent_live_query_domain_resync *state =
			&agent_live_query_domain_resync[slot];

		if (!state->used)
			continue;
		if ((state->scope_id == VFS_SCOPE_SYSTEM ||
		     agent_live_query_domain_equal(
			     state->key, state->scope_id, key, scope_id)) &&
		    state->generation > generation)
			generation = state->generation;
	}
	return generation;
}

static void
agent_live_query_broadcast_resync_locked(struct workflow_lifecycle_key key,
					 uint scope_id,
					 uint64 generation)
{
	if (intr_get())
		panic("Agent live query broadcast unlocked");
	for (struct proc *target = pool; target < &pool[NPROC]; target++) {
		if (!agent_live_query_target_visible(target, key, scope_id) ||
		    !agent_live_query_has_file_watch(target))
			continue;
		agent_live_query_proc_resync_mark_locked(
			target, generation,
			(1U << AGENT_EVENT_FILE_STATUS) |
			(1U << AGENT_EVENT_FILE_QUERY));
	}
}

static void
agent_live_query_domain_resync_mark_locked(
	struct workflow_lifecycle_key key, uint scope_id, uint64 generation)
{
	struct agent_live_query_domain_resync *free_state = 0;

	if (intr_get())
		panic("Agent live query domain mark unlocked");
	for (uint slot = 0; slot < AGENT_LIVE_QUERY_DOMAIN_CAP; slot++) {
		struct agent_live_query_domain_resync *state =
			&agent_live_query_domain_resync[slot];
		uint ignored_scope;

		if (state->used && agent_live_query_domain_equal(
				   state->key, state->scope_id, key, scope_id)) {
			if (generation > state->generation)
				state->generation = generation;
			return;
		}
		if (!state->used && free_state == 0)
			free_state = state;
		else if (free_state == 0 &&
			 state->scope_id != VFS_SCOPE_SYSTEM &&
			 workflow_lifecycle_scope(
				 state->key, &ignored_scope) < 0)
			free_state = state;
	}
	if (free_state == 0) {
		if (generation > agent_live_query_global_resync_generation)
			agent_live_query_global_resync_generation = generation;
		return;
	}
	memset(free_state, 0, sizeof(*free_state));
	free_state->used = 1;
	free_state->key = key;
	free_state->scope_id = scope_id;
	free_state->generation = generation;
}

void
agent_live_query_events_init(void)
{
	int enabled = intr_save();

	memset(agent_live_query_tombstones, 0,
	       sizeof(agent_live_query_tombstones));
	memset(agent_live_query_content_pending, 0,
	       sizeof(agent_live_query_content_pending));
	memset(agent_live_query_domain_resync, 0,
	       sizeof(agent_live_query_domain_resync));
	memset(agent_live_query_proc_resync, 0,
	       sizeof(agent_live_query_proc_resync));
	memset(agent_live_query_subscriptions, 0,
	       sizeof(agent_live_query_subscriptions));
	memset(agent_live_query_predicate_arena, 0,
	       sizeof(agent_live_query_predicate_arena));
	memset(agent_live_query_predicate_scratch, 0,
	       sizeof(agent_live_query_predicate_scratch));
	agent_live_query_tombstone_cursor = 0;
	agent_live_query_content_cursor = 0;
	agent_live_query_predicate_arena_used = 0;
	agent_live_query_next_watch_id = 0;
	agent_live_query_global_resync_generation = 0;
	memset(agent_live_query_file_watch_present, 0,
	       sizeof(agent_live_query_file_watch_present));
	agent_live_query_file_watch_processes = 0;
	intr_restore(enabled);
}

int
agent_live_query_watch_install_typed(struct proc *p,
				     struct agent_file_live_watch *watch)
{
	struct agent_live_query_subscription *subscription = 0;
	struct agent_ipc_observe_cold_state *cold;
	struct workflow_lifecycle_key key;
	struct agent_live_query_cold_token token;
	ushort value_offset[AGENT_LIVE_QUERY_FIELD_COUNT];
	ushort predicate_bytes = 0;
	uint64 catalog_generation = 0;
	uint64 initial_generation;
	uint64 resync_generation;
	uint scope_id;
	uint64 watch_id;
	int cold_slot = -1;
	int status;
	int enabled;

	if (watch == 0 || watch->version != AGENT_FILE_LIVE_WATCH_VERSION ||
	    (watch->flags & ~(AGENT_FILE_LIVE_WATCH_F_ACK_RESYNC)) != 0 ||
	    watch->watch_id != 0 || watch->initial_generation != 0 ||
	    watch->catalog_generation != 0 ||
	    ((watch->flags & AGENT_FILE_LIVE_WATCH_F_ACK_RESYNC) != 0 &&
	     watch->resync_generation == 0) ||
	    !agent_metadata_txn_owned(0) ||
	    !agent_live_query_target_valid(p, &key, &scope_id) ||
	    watch->query.max_hits < 0 ||
	    watch->query.max_hits > AGENT_FILE_QUERY_MAX_HITS)
		return AGENT_STATUS_BAD_PARAM;
	memset(agent_live_query_predicate_scratch, 0,
	       sizeof(agent_live_query_predicate_scratch));
	status = agent_live_query_predicate_compile(
		&watch->query, agent_live_query_predicate_scratch,
		&predicate_bytes, value_offset);
	if (status != AGENT_STATUS_OK)
		return status;
	enabled = intr_save();
	if (!agent_live_query_target_valid(p, &key, &scope_id) ||
	    agent_metadata_catalog_fence_generation(
		    scope_id, key, &catalog_generation) != 0 ||
	    catalog_generation == 0) {
		status = AGENT_STATUS_RETRY;
		goto out;
	}
	cold = p->agent_ipc_observe_cold;
	for (int slot = 0; slot < AGENT_WATCH_MAX; slot++)
		if (!cold->watch_valid[slot]) {
			cold_slot = slot;
			break;
		}
	for (uint slot = 0; slot < AGENT_LIVE_QUERY_SUBSCRIPTION_CAP; slot++)
		if (!agent_live_query_subscriptions[slot].used) {
			subscription = &agent_live_query_subscriptions[slot];
			break;
		}
	if (cold_slot < 0 || subscription == 0 ||
	    predicate_bytes > sizeof(agent_live_query_predicate_arena) -
			      agent_live_query_predicate_arena_used ||
	    (watch_id = agent_live_query_watch_id_alloc_locked()) == 0) {
		status = AGENT_STATUS_NO_SPACE;
		goto out;
	}
	if ((watch->flags & AGENT_FILE_LIVE_WATCH_F_ACK_RESYNC) != 0)
		(void)agent_live_query_resync_ack(
			key, scope_id, watch->resync_generation);
	initial_generation = MAX(
		agent_file_state_scope_generation(scope_id),
		agent_file_state_scope_generation(VFS_SCOPE_SYSTEM));
	resync_generation =
		agent_live_query_domain_generation_locked(key, scope_id);
	memset(subscription, 0, sizeof(*subscription));
	subscription->used = 1;
	subscription->target = p;
	subscription->control_id = p->agent_control_id;
	subscription->key = key;
	subscription->scope_id = scope_id;
	subscription->cold_slot = (uint)cold_slot;
	subscription->watch_id = watch_id;
	subscription->initial_generation = initial_generation;
	subscription->catalog_generation = catalog_generation;
	subscription->resync_generation = resync_generation;
	subscription->predicate_offset = agent_live_query_predicate_arena_used;
	subscription->predicate_bytes = predicate_bytes;
	memmove(subscription->value_offset, value_offset,
		sizeof(subscription->value_offset));
	if (predicate_bytes != 0) {
		memmove(agent_live_query_predicate_arena +
				agent_live_query_predicate_arena_used,
			agent_live_query_predicate_scratch, predicate_bytes);
		agent_live_query_predicate_arena_used += predicate_bytes;
	}
	memset(&token, 0, sizeof(token));
	token.watch_id = watch_id;
	token.initial_generation = initial_generation;
	cold->watch_valid[cold_slot] = 1;
	cold->watch_event_type[cold_slot] = AGENT_EVENT_FILE_QUERY;
	memset(cold->watch_filter[cold_slot], 0,
	       sizeof(cold->watch_filter[cold_slot]));
	memmove(cold->watch_filter[cold_slot], &token, sizeof(token));
	p->agent_watch_count++;
	agent_live_query_file_watch_refresh_locked(p);
	watch->flags = resync_generation == 0 ? 0 :
			 AGENT_FILE_LIVE_WATCH_F_RESYNC_REQUIRED;
	watch->watch_id = watch_id;
	watch->initial_generation = initial_generation;
	watch->catalog_generation = catalog_generation;
	watch->resync_generation = resync_generation;
	status = AGENT_STATUS_OK;
out:
	intr_restore(enabled);
	memset(agent_live_query_predicate_scratch, 0,
	       sizeof(agent_live_query_predicate_scratch));
	return status;
}

int
agent_live_query_watch_remove_typed(struct proc *p,
				    struct agent_file_live_watch *watch)
{
	struct agent_live_query_subscription *subscription;
	struct workflow_lifecycle_key key;
	uint scope_id;
	int enabled;
	int status = AGENT_STATUS_NOT_FOUND;

	if (watch == 0 || watch->version != AGENT_FILE_LIVE_WATCH_VERSION ||
	    watch->watch_id == 0 ||
	    (watch->flags & ~(AGENT_FILE_LIVE_WATCH_F_ACK_RESYNC |
			      AGENT_FILE_LIVE_WATCH_F_RESYNC_REQUIRED)) != 0 ||
	    !agent_metadata_txn_owned(0) ||
	    !agent_live_query_target_valid(p, &key, &scope_id))
		return AGENT_STATUS_BAD_PARAM;
	enabled = intr_save();
	subscription = agent_live_query_subscription_find(p, watch->watch_id);
	if (subscription != 0 && agent_live_query_subscription_valid(subscription)) {
		agent_live_query_subscription_remove_locked(subscription, 1);
		agent_live_query_file_watch_refresh_locked(p);
		status = AGENT_STATUS_OK;
	}
	if ((watch->flags & AGENT_FILE_LIVE_WATCH_F_ACK_RESYNC) != 0 &&
	    watch->resync_generation != 0)
		(void)agent_live_query_resync_ack(
			key, scope_id, watch->resync_generation);
	intr_restore(enabled);
	return status;
}

int
agent_live_query_tombstone_enqueue(struct workflow_lifecycle_key key,
				   uint scope_id, uint dev, uint inum,
				   uint incarnation)
{
	struct agent_live_query_tombstone *free_state = 0;
	uint64 generation;
	int enabled;

	if (!agent_live_query_domain_valid(key, scope_id) ||
	    dev == 0 || inum == 0 || incarnation == 0)
		return -1;
	enabled = intr_save();
	for (uint slot = 0; slot < AGENT_LIVE_QUERY_TOMBSTONE_CAP; slot++) {
		struct agent_live_query_tombstone *state =
			&agent_live_query_tombstones[slot];

		if (!state->used) {
			if (free_state == 0)
				free_state = state;
			continue;
		}
		if (agent_live_query_domain_equal(
			    state->key, state->scope_id, key, scope_id) &&
		    state->dev == dev && state->inum == inum &&
		    state->incarnation == incarnation) {
			intr_restore(enabled);
			return 0;
		}
	}
	if (free_state != 0) {
		free_state->used = 1;
		free_state->key = key;
		free_state->scope_id = scope_id;
		free_state->dev = dev;
		free_state->inum = inum;
		free_state->incarnation = incarnation;
		intr_restore(enabled);
		return 1;
	}
	generation = agent_file_state_generation_next(scope_id);
	agent_live_query_domain_resync_mark_locked(key, scope_id, generation);
	agent_live_query_broadcast_resync_locked(key, scope_id, generation);
	intr_restore(enabled);
	for (struct proc *target = pool; target < &pool[NPROC]; target++)
		(void)agent_live_query_proc_resync_flush(target);
	return -1;
}

static int
agent_live_query_tombstone_process(uint slot)
{
	struct agent_live_query_tombstone snapshot;
	struct agent_file_meta before;
	uint64 generation = 0;
	int enabled;
	int removed;

	if (slot >= AGENT_LIVE_QUERY_TOMBSTONE_CAP)
		return -1;
	enabled = intr_save();
	snapshot = agent_live_query_tombstones[slot];
	intr_restore(enabled);
	if (!snapshot.used)
		return 0;
	memset(&before, 0, sizeof(before));
	removed = agent_metadata_catalog_remove_identity_exact(
		snapshot.key, snapshot.scope_id, snapshot.dev, snapshot.inum,
		snapshot.incarnation, &before, &generation);
	if (removed < 0)
		return -1;
	enabled = intr_save();
	if (agent_live_query_tombstones[slot].used &&
	    agent_live_query_domain_equal(
		    agent_live_query_tombstones[slot].key,
		    agent_live_query_tombstones[slot].scope_id,
		    snapshot.key, snapshot.scope_id) &&
	    agent_live_query_tombstones[slot].dev == snapshot.dev &&
	    agent_live_query_tombstones[slot].inum == snapshot.inum &&
	    agent_live_query_tombstones[slot].incarnation ==
		    snapshot.incarnation)
		memset(&agent_live_query_tombstones[slot], 0,
		       sizeof(agent_live_query_tombstones[slot]));
	intr_restore(enabled);
	if (removed > 0 && before.used) {
		if (generation == 0)
			generation = agent_file_state_scope_generation(
				snapshot.scope_id);
		(void)agent_live_query_publish_transition(
			0, snapshot.key, snapshot.scope_id, &before, 0,
			generation);
	}
	return 1;
}

int
agent_live_query_tombstone_drain(uint budget)
{
	uint attempted = 0;
	uint visited = 0;
	int completed = 0;

	if (!agent_metadata_txn_owned(0))
		return AGENT_STATUS_RETRY;
	if (budget == 0)
		budget = AGENT_LIVE_QUERY_DEFAULT_DRAIN;
	while (attempted < budget &&
	       visited < AGENT_LIVE_QUERY_TOMBSTONE_CAP) {
		uint slot;
		int present;
		int enabled = intr_save();

		slot = agent_live_query_tombstone_cursor;
		agent_live_query_tombstone_cursor =
			(slot + 1) % AGENT_LIVE_QUERY_TOMBSTONE_CAP;
		present = agent_live_query_tombstones[slot].used;
		intr_restore(enabled);
		visited++;
		if (!present)
			continue;
		attempted++;
		if (agent_live_query_tombstone_process(slot) > 0)
			completed++;
	}
	return completed;
}

int
agent_live_query_content_enqueue(
	const struct agent_file_content_receipt *receipt, uint64 generation)
{
	struct agent_live_query_content *pending;
	int enabled;

	if (receipt == 0 || receipt->sequence == 0 ||
	    receipt->slot >= AGENT_FILE_META_MAX || generation == 0 ||
	    receipt->dev == 0 || receipt->inum == 0 ||
	    receipt->incarnation == 0 ||
	    !agent_live_query_domain_valid(
		    receipt->lifecycle, receipt->scope_id))
		return -1;
	enabled = intr_save();
	pending = &agent_live_query_content_pending[receipt->slot];
	if (pending->used &&
	    agent_live_query_domain_equal(
		    pending->receipt.lifecycle, pending->receipt.scope_id,
		    receipt->lifecycle, receipt->scope_id) &&
	    pending->receipt.dev == receipt->dev &&
	    pending->receipt.inum == receipt->inum &&
	    pending->receipt.incarnation == receipt->incarnation &&
	    pending->receipt.sequence > receipt->sequence) {
		intr_restore(enabled);
		return 0;
	}
	pending->used = 1;
	pending->receipt = *receipt;
	pending->generation = generation;
	intr_restore(enabled);
	return 1;
}

static int
agent_live_query_content_process(uint slot)
{
	struct agent_live_query_content snapshot;
	struct agent_catalog_view view;
	struct agent_file_meta meta;
	uint64 generation;
	int enabled;
	int found;

	if (slot >= AGENT_FILE_META_MAX)
		return -1;
	enabled = intr_save();
	snapshot = agent_live_query_content_pending[slot];
	intr_restore(enabled);
	if (!snapshot.used)
		return 0;
	memset(&view, 0, sizeof(view));
	found = agent_metadata_catalog_borrow(0, slot, &view);
	if (found > 0 && view.scope_id == snapshot.receipt.scope_id &&
	    agent_live_query_key_equal(
		    view.lifecycle, snapshot.receipt.lifecycle) &&
	    view.meta->dev == snapshot.receipt.dev &&
	    view.meta->inum == snapshot.receipt.inum &&
	    view.meta->incarnation == snapshot.receipt.incarnation) {
		meta = *view.meta;
		view.meta = 0;
		agent_file_state_overlay_published_size(
			&meta, snapshot.receipt.scope_id);
		generation = MAX(snapshot.generation, meta.fs_generation);
		enabled = intr_save();
		if (agent_live_query_content_pending[slot].used &&
		    agent_live_query_content_pending[slot].receipt.sequence ==
			    snapshot.receipt.sequence &&
		    agent_live_query_domain_equal(
			    agent_live_query_content_pending[slot]
				    .receipt.lifecycle,
			    agent_live_query_content_pending[slot]
				    .receipt.scope_id,
			    snapshot.receipt.lifecycle,
			    snapshot.receipt.scope_id))
			memset(&agent_live_query_content_pending[slot], 0,
			       sizeof(agent_live_query_content_pending[slot]));
		intr_restore(enabled);
		(void)agent_live_query_publish_transition(
			0, snapshot.receipt.lifecycle, snapshot.receipt.scope_id,
			&meta, &meta, generation);
		return 1;
	}
	view.meta = 0;
	enabled = intr_save();
	if (agent_live_query_content_pending[slot].used &&
	    agent_live_query_content_pending[slot].receipt.sequence ==
		    snapshot.receipt.sequence)
		memset(&agent_live_query_content_pending[slot], 0,
		       sizeof(agent_live_query_content_pending[slot]));
	intr_restore(enabled);
	return 1;
}

int
agent_live_query_content_drain(uint budget)
{
	uint attempted = 0;
	uint visited = 0;
	int completed = 0;

	if (!agent_metadata_txn_owned(0))
		return AGENT_STATUS_RETRY;
	if (budget == 0)
		budget = AGENT_LIVE_QUERY_DEFAULT_DRAIN;
	while (attempted < budget && visited < AGENT_FILE_META_MAX) {
		uint slot;
		int present;
		int enabled = intr_save();

		slot = agent_live_query_content_cursor;
		agent_live_query_content_cursor =
			(slot + 1) % AGENT_FILE_META_MAX;
		present = agent_live_query_content_pending[slot].used;
		intr_restore(enabled);
		visited++;
		if (!present)
			continue;
		attempted++;
		if (agent_live_query_content_process(slot) > 0)
			completed++;
	}
	return completed;
}

int
agent_live_query_publish_transition(
	struct proc *source, struct workflow_lifecycle_key key, uint owner_scope,
	const struct agent_file_meta *before,
	const struct agent_file_meta *after, uint64 generation)
{
	const struct agent_file_meta *identity =
		after != 0 && after->used ? after : before;
	struct workflow_lifecycle_key source_key =
		agent_live_query_proc_key(source);
	uint source_scope = agent_identity_proc_scope(source);
	uint64 fid;
	int delivered = 0;

	if (!agent_live_query_domain_valid(key, owner_scope) ||
	    identity == 0 || !identity->used || identity->fid <= 0)
		return 0;
	if (!agent_live_query_file_watches_present())
		return 0;
	if (generation == 0)
		generation = agent_file_state_scope_generation(owner_scope);
	fid = (uint64)(uint)identity->fid;
	for (struct proc *target = pool; target < &pool[NPROC]; target++) {
		struct workflow_lifecycle_key target_key;
		struct proc *attributed = 0;
		uint target_scope;
		uint legacy_changes;
		uint typed_changes;
		int enabled = intr_save();

		if (!agent_live_query_target_visible(target, key, owner_scope) ||
		    !agent_live_query_target_valid(
			    target, &target_key, &target_scope)) {
			intr_restore(enabled);
			continue;
		}
		legacy_changes = agent_live_query_target_changes(
			target, owner_scope, before, after);
		typed_changes = agent_live_query_typed_target_changes(
			target, owner_scope, before, after);
		if (legacy_changes == 0 && typed_changes == 0) {
			intr_restore(enabled);
			continue;
		}
		if (source != 0 && source_scope == target_scope &&
		    agent_live_query_key_equal(source_key, target_key))
			attributed = source;
		intr_restore(enabled);
		for (uint stream = 0; stream < 2; stream++) {
			uint changes = stream == 0 ? legacy_changes : typed_changes;
			int event_type = stream == 0 ? AGENT_EVENT_FILE_STATUS :
						       AGENT_EVENT_FILE_QUERY;

			for (int change = AGENT_LIVE_QUERY_ENTER;
			     change <= AGENT_LIVE_QUERY_LEAVE; change++) {
				char payload[AGENT_EVENT_PAYLOAD_SIZE];
				int result;

				if ((changes & (1U << change)) == 0)
					continue;
				agent_live_query_payload(payload, change, key);
				result = agent_ipc_deliver_live_event(
					target, attributed, event_type, fid,
					generation, payload, 0);
				if (result > 0) {
					delivered += result;
					continue;
				}
				if (result < 0) {
					enabled = intr_save();
					agent_live_query_proc_resync_mark_locked(
						target, generation,
						1U << event_type);
					intr_restore(enabled);
					(void)agent_live_query_proc_resync_flush(
						target);
				}
			}
		}
	}
	return delivered;
}

void
agent_live_query_watch_installed(struct proc *p)
{
	struct workflow_lifecycle_key key;
	uint64 generation;
	uint scope_id;
	int enabled = intr_save();

	agent_live_query_file_watch_refresh_locked(p);
	if (!agent_live_query_target_valid(p, &key, &scope_id) ||
	    !agent_live_query_has_file_watch(p))
		goto out;
	generation = agent_file_state_scope_generation(scope_id);
	generation = MAX(generation,
		agent_live_query_domain_generation_locked(key, scope_id));
	agent_live_query_proc_resync_mark_locked(
		p, generation, 1U << AGENT_EVENT_FILE_STATUS);
	(void)agent_live_query_proc_resync_flush(p);
out:
	intr_restore(enabled);
}

void
agent_live_query_watch_removed(struct proc *p)
{
	int enabled;
	uint slot;

	if (p == 0 || p < pool || p >= &pool[NPROC])
		return;
	enabled = intr_save();
	agent_live_query_file_watch_refresh_locked(p);
	if (!agent_live_query_has_file_watch(p)) {
		slot = p - pool;
		agent_live_query_proc_resync_clear(
			&agent_live_query_proc_resync[slot]);
	}
	intr_restore(enabled);
}

void
agent_live_query_proc_reset(struct proc *p)
{
	int enabled;

	if (p == 0 || p < pool || p >= &pool[NPROC])
		return;
	enabled = intr_save();
	for (uint slot = 0; slot < AGENT_LIVE_QUERY_SUBSCRIPTION_CAP; slot++)
		if (agent_live_query_subscriptions[slot].used &&
		    agent_live_query_subscriptions[slot].target == p)
			agent_live_query_subscription_remove_locked(
				&agent_live_query_subscriptions[slot], 0);
	agent_live_query_proc_resync_clear(
		&agent_live_query_proc_resync[p - pool]);
	agent_live_query_file_watch_clear_locked(p);
	intr_restore(enabled);
}

int
agent_live_query_resync_ack(struct workflow_lifecycle_key key,
			    uint scope_id, uint64 generation)
{
	int acknowledged = 0;
	int enabled;

	if (!agent_live_query_domain_valid(key, scope_id) || generation == 0)
		return 0;
	enabled = intr_save();
	for (uint slot = 0; slot < AGENT_LIVE_QUERY_DOMAIN_CAP; slot++) {
		struct agent_live_query_domain_resync *state =
			&agent_live_query_domain_resync[slot];

		if (!state->used || state->generation > generation ||
		    !agent_live_query_domain_equal(
			    state->key, state->scope_id, key, scope_id))
			continue;
		memset(state, 0, sizeof(*state));
		acknowledged = 1;
	}
	if (scope_id == VFS_SCOPE_SYSTEM &&
	    agent_live_query_global_resync_generation != 0 &&
	    agent_live_query_global_resync_generation <= generation) {
		agent_live_query_global_resync_generation = 0;
		acknowledged = 1;
	}
	intr_restore(enabled);
	return acknowledged;
}

static int
agent_live_query_proc_resync_pending_domain(
	struct workflow_lifecycle_key key, uint scope_id)
{
	for (struct proc *target = pool; target < &pool[NPROC]; target++) {
		struct agent_live_query_proc_resync *pending =
			&agent_live_query_proc_resync[target - pool];

		if (!agent_live_query_proc_resync_valid(pending, target))
			continue;
		if (scope_id == VFS_SCOPE_SYSTEM ||
		    agent_live_query_domain_equal(
			    pending->key, pending->scope_id, key, scope_id))
			return 1;
	}
	return 0;
}

int
agent_live_query_fence_drain(struct workflow_lifecycle_key key, uint scope_id)
{
	int retry = 0;

	if (!agent_live_query_domain_valid(key, scope_id) ||
	    !agent_metadata_txn_owned(0))
		return AGENT_STATUS_RETRY;
	for (uint slot = 0; slot < AGENT_LIVE_QUERY_TOMBSTONE_CAP; slot++) {
		int pending;
		int enabled = intr_save();

		pending = agent_live_query_tombstones[slot].used &&
			  agent_live_query_domain_equal(
				  agent_live_query_tombstones[slot].key,
				  agent_live_query_tombstones[slot].scope_id,
				  key, scope_id);
		intr_restore(enabled);
		if (!pending)
			continue;
		if (agent_live_query_tombstone_process(slot) < 0)
			retry = 1;
	}
	for (uint slot = 0; slot < AGENT_FILE_META_MAX; slot++) {
		int pending;
		int enabled = intr_save();

		pending = agent_live_query_content_pending[slot].used &&
			  agent_live_query_domain_equal(
				  agent_live_query_content_pending[slot]
					  .receipt.lifecycle,
				  agent_live_query_content_pending[slot]
					  .receipt.scope_id,
				  key, scope_id);
		intr_restore(enabled);
		if (!pending)
			continue;
		if (agent_live_query_content_process(slot) < 0)
			retry = 1;
	}
	for (struct proc *target = pool; target < &pool[NPROC]; target++) {
		int pending;
		int enabled = intr_save();
		struct agent_live_query_proc_resync *state =
			&agent_live_query_proc_resync[target - pool];

		pending = agent_live_query_proc_resync_valid(state, target) &&
			  (scope_id == VFS_SCOPE_SYSTEM ||
			   agent_live_query_domain_equal(
				   state->key, state->scope_id, key, scope_id));
		intr_restore(enabled);
		if (pending)
			(void)agent_live_query_proc_resync_flush(target);
	}
	{
		int enabled = intr_save();

		if (agent_live_query_domain_generation_locked(key, scope_id) != 0 ||
		    agent_live_query_proc_resync_pending_domain(key, scope_id))
			retry = 1;
		intr_restore(enabled);
	}
	return retry ? AGENT_STATUS_RETRY : AGENT_STATUS_OK;
}

void
agent_live_query_reclaim(struct workflow_lifecycle_key key, uint scope_id)
{
	int enabled;

	if (!agent_live_query_domain_valid(key, scope_id))
		return;
	enabled = intr_save();
	for (uint slot = 0; slot < AGENT_LIVE_QUERY_TOMBSTONE_CAP; slot++)
		if (agent_live_query_tombstones[slot].used &&
		    agent_live_query_domain_equal(
			    agent_live_query_tombstones[slot].key,
			    agent_live_query_tombstones[slot].scope_id,
			    key, scope_id))
			memset(&agent_live_query_tombstones[slot], 0,
			       sizeof(agent_live_query_tombstones[slot]));
	for (uint slot = 0; slot < AGENT_FILE_META_MAX; slot++)
		if (agent_live_query_content_pending[slot].used &&
		    agent_live_query_domain_equal(
			    agent_live_query_content_pending[slot]
				    .receipt.lifecycle,
			    agent_live_query_content_pending[slot]
				    .receipt.scope_id,
			    key, scope_id))
			memset(&agent_live_query_content_pending[slot], 0,
			       sizeof(agent_live_query_content_pending[slot]));
	for (uint slot = 0; slot < AGENT_LIVE_QUERY_DOMAIN_CAP; slot++)
		if (agent_live_query_domain_resync[slot].used &&
		    agent_live_query_domain_equal(
			    agent_live_query_domain_resync[slot].key,
			    agent_live_query_domain_resync[slot].scope_id,
			    key, scope_id))
			memset(&agent_live_query_domain_resync[slot], 0,
			       sizeof(agent_live_query_domain_resync[slot]));
	for (struct proc *target = pool; target < &pool[NPROC]; target++) {
		struct agent_live_query_proc_resync *pending =
			&agent_live_query_proc_resync[target - pool];

		if (pending->pending && agent_live_query_domain_equal(
				 pending->key, pending->scope_id, key, scope_id))
			agent_live_query_proc_resync_clear(pending);
	}
	intr_restore(enabled);
}
