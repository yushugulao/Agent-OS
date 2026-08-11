#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_catalog.h"
#include "agent_metadata_internal.h"
#include "defs.h"
#include "file.h"
#include "fs.h"
#include "trap.h"
#include "vfs_security.h"
#define AGENT_CATALOG_INDEX_BUCKETS 16
#define AGENT_CATALOG_PRIMARY_INDEX_COUNT 4
#define AGENT_CATALOG_SECONDARY_INDEX_COUNT 3
#define AGENT_CATALOG_INDEX_COUNT \
	(AGENT_CATALOG_PRIMARY_INDEX_COUNT + AGENT_CATALOG_SECONDARY_INDEX_COUNT)
#define AGENT_CATALOG_BITMAP_WORDS AGENT_CATALOG_READ_WORDS
#define AGENT_CATALOG_USAGE_MAX (AGENT_CATALOG_SCOPE_MAX + 1)
#define AGENT_CATALOG_PLAN_HASH 1469598103934665603ULL
#define AGENT_CATALOG_PUBLISH_GENERATION (1U << 0)

enum agent_catalog_bitmap_index {
	AGENT_CATALOG_BITMAP_FID = 0,
	AGENT_CATALOG_BITMAP_PHYSICAL,
	AGENT_CATALOG_BITMAP_LOGICAL,
	AGENT_CATALOG_BITMAP_IDENTITY,
	AGENT_CATALOG_BITMAP_STATUS,
	AGENT_CATALOG_BITMAP_STAGE,
	AGENT_CATALOG_BITMAP_KIND,
};
_Static_assert(AGENT_FILE_SYSTEM_LIMIT +
	       AGENT_FILE_ORDINARY_LIMIT == AGENT_FILE_META_MAX,
	       "file catalog partitions must cover the fixed table");
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_FILE_SCOPE_LIMIT ==
	       AGENT_FILE_ORDINARY_LIMIT,
	       "workflow catalog partitions must cover the ordinary table");
_Static_assert(AGENT_FILE_EXPLICIT_RESERVE > 0 &&
	       AGENT_FILE_EXPLICIT_RESERVE < AGENT_FILE_SCOPE_LIMIT,
	       "autoscan cache must preserve explicit metadata headroom");
_Static_assert(AGENT_FILE_META_MAX <= 32767,
	       "file catalog cursors must fit their signed short storage");

struct agent_catalog_usage {
	uint used, scope_id, live, autoscan;
	struct workflow_lifecycle_key lifecycle;
	uint64 slots[AGENT_CATALOG_BITMAP_WORDS];
	uint64 fids[AGENT_CATALOG_BITMAP_WORDS];
};

static struct agent_file_meta agent_catalog_files[AGENT_FILE_META_MAX];
static uint agent_catalog_scopes[AGENT_FILE_META_MAX];
static const ushort
agent_catalog_secondary_offsets[AGENT_CATALOG_SECONDARY_INDEX_COUNT] = {
	__builtin_offsetof(struct agent_file_meta, status),
	__builtin_offsetof(struct agent_file_meta, stage),
	__builtin_offsetof(struct agent_file_meta, kind),
};
static uint64 agent_catalog_index_bits[AGENT_CATALOG_INDEX_COUNT]
				       [AGENT_CATALOG_INDEX_BUCKETS]
				       [AGENT_CATALOG_BITMAP_WORDS];
static uint64 agent_catalog_free_bits[AGENT_CATALOG_BITMAP_WORDS];
static uint64 agent_catalog_ready_bits[AGENT_CATALOG_BITMAP_WORDS];
/* Live queries expose only explicitly managed records, never scan cache rows. */
static uint64 agent_catalog_live_query_bits[AGENT_CATALOG_BITMAP_WORDS];
static struct agent_catalog_usage
	agent_catalog_usage[AGENT_CATALOG_USAGE_MAX];
static uchar agent_catalog_slot_usage[AGENT_FILE_META_MAX];
static struct agent_file_meta agent_catalog_edit_buffer;
static struct agent_catalog_edit *agent_catalog_active_edit;
static uint64 agent_catalog_generation;
static void *agent_catalog_mutation_owner;
static uint64 agent_catalog_mutation_sequence, agent_catalog_mutation_token;
static uint agent_catalog_live, agent_catalog_system, agent_catalog_ordinary;
static int agent_catalog_unbind(int, struct agent_file_meta *, uint);
static void agent_catalog_normalize_physical(int, struct agent_file_meta *);
static void agent_catalog_require_txn(void);
static int agent_catalog_mutation_allowed(void);
static void agent_catalog_slot_publish(
	int, const struct agent_file_meta *, uint,
	struct workflow_lifecycle_key, uint, uint);
static int agent_catalog_hard_admission(
	uint, int, const struct agent_file_meta *, struct agent_catalog_resolution *);
static int agent_catalog_admission(
	uint, int, const struct agent_file_meta *, uint, uint, int);
static uint agent_catalog_key_matches(
	const struct agent_file_meta *selector,
	const struct agent_file_meta *meta) {
	uint keys = 0;
	if (selector->fid > 0 && selector->fid == meta->fid)
		keys |= AGENT_CATALOG_KEY_FID;
	if (selector->physical_name[0] &&
	    strncmp(selector->physical_name, meta->physical_name,
		    sizeof(selector->physical_name)) == 0)
		keys |= AGENT_CATALOG_KEY_PHYSICAL;
	if (selector->logical_path[0] &&
	    strncmp(selector->logical_path, meta->logical_path,
		    sizeof(selector->logical_path)) == 0)
		keys |= AGENT_CATALOG_KEY_LOGICAL;
	if (agent_metadata_catalog_identity_state(selector) > 0 &&
	    selector->dev == meta->dev && selector->inum == meta->inum &&
	    selector->incarnation == meta->incarnation)
		keys |= AGENT_CATALOG_KEY_IDENTITY;
	return keys;
}
static uint64 agent_catalog_hash_bytes(
	uint64 hash, const void *data, uint size) {
	const uchar *bytes = data;
	for (uint i = 0; i < size; i++) {
		hash ^= bytes[i];
		hash *= 1099511628211ULL;
	}
	return hash;
}

static int agent_catalog_bitmap_test(const uint64 *bits, uint slot)
{
	return (bits[slot / 64] & (1ULL << (slot % 64))) != 0;
}

static uint agent_catalog_first_bit(uint64 value)
{
	uint index = 0;

	if (value == 0)
		panic("Agent catalog empty bitmap word");
	if ((value & 0xffffffffULL) == 0) {
		value >>= 32;
		index += 32;
	}
	if ((value & 0xffffULL) == 0) {
		value >>= 16;
		index += 16;
	}
	if ((value & 0xffULL) == 0) {
		value >>= 8;
		index += 8;
	}
	if ((value & 0xfULL) == 0) {
		value >>= 4;
		index += 4;
	}
	if ((value & 0x3ULL) == 0) {
		value >>= 2;
		index += 2;
	}
	if ((value & 0x1ULL) == 0)
		index++;
	return index;
}

static void agent_catalog_bitmap_set(uint64 *bits, uint slot)
{
	bits[slot / 64] |= 1ULL << (slot % 64);
}

static void agent_catalog_bitmap_clear(uint64 *bits, uint slot)
{
	bits[slot / 64] &= ~(1ULL << (slot % 64));
}

static int agent_catalog_bitmap_next(const uint64 *bits, int after)
{
	uint slot = after < 0 ? 0 : (uint)after + 1;
	uint word = slot / 64;
	uint offset = slot % 64;

	while (word < AGENT_CATALOG_BITMAP_WORDS) {
		uint64 candidates = bits[word] & (~0ULL << offset);

		if (candidates != 0) {
			uint found = word * 64 +
				     agent_catalog_first_bit(candidates);

			return found < AGENT_FILE_META_MAX ? (int)found : -1;
		}
		word++;
		offset = 0;
	}
	return -1;
}

static int agent_catalog_primary_present(
	int index, const struct agent_file_meta *meta)
{
	switch (index) {
	case AGENT_CATALOG_BITMAP_FID:
		return meta->fid > 0;
	case AGENT_CATALOG_BITMAP_PHYSICAL:
		return meta->physical_name[0] != 0;
	case AGENT_CATALOG_BITMAP_LOGICAL:
		return meta->logical_path[0] != 0;
	case AGENT_CATALOG_BITMAP_IDENTITY:
		return agent_metadata_catalog_identity_state(meta) > 0;
	default:
		return 0;
	}
}

static uint agent_catalog_index_bucket(
	int index, const struct agent_file_meta *meta)
{
	uint64 hash = AGENT_CATALOG_PLAN_HASH;

	switch (index) {
	case AGENT_CATALOG_BITMAP_FID:
		hash = agent_catalog_hash_bytes(hash, &meta->fid,
					 sizeof(meta->fid));
		break;
	case AGENT_CATALOG_BITMAP_PHYSICAL:
		hash = agent_catalog_hash_bytes(hash, meta->physical_name,
					 strlen(meta->physical_name));
		break;
	case AGENT_CATALOG_BITMAP_LOGICAL:
		hash = agent_catalog_hash_bytes(hash, meta->logical_path,
					 strlen(meta->logical_path));
		break;
	case AGENT_CATALOG_BITMAP_IDENTITY:
		hash = agent_catalog_hash_bytes(hash, &meta->dev,
					 sizeof(meta->dev));
		hash = agent_catalog_hash_bytes(hash, &meta->inum,
					 sizeof(meta->inum));
		hash = agent_catalog_hash_bytes(hash, &meta->incarnation,
					 sizeof(meta->incarnation));
		break;
	default: {
		int secondary = index - AGENT_CATALOG_PRIMARY_INDEX_COUNT;
		const char *text = (const char *)meta +
			agent_catalog_secondary_offsets[secondary];

		hash = agent_catalog_hash_bytes(hash, text, strlen(text));
		break;
	}
	}
	return hash % AGENT_CATALOG_INDEX_BUCKETS;
}

static void agent_catalog_index_update(
	int slot, const struct agent_file_meta *meta, uint state, int present)
{
	if (meta == 0 || !meta->used)
		return;
	for (int index = 0; index < AGENT_CATALOG_PRIMARY_INDEX_COUNT;
	     index++) {
		uint bucket;
		uint64 *bits;

		if (!agent_catalog_primary_present(index, meta))
			continue;
		bucket = agent_catalog_index_bucket(index, meta);
		bits = agent_catalog_index_bits[index][bucket];
		if (present)
			agent_catalog_bitmap_set(bits, slot);
		else
			agent_catalog_bitmap_clear(bits, slot);
	}
	if (state != 0)
		return;
	for (int secondary = 0;
	     secondary < AGENT_CATALOG_SECONDARY_INDEX_COUNT; secondary++) {
		int index = AGENT_CATALOG_PRIMARY_INDEX_COUNT + secondary;
		const char *text = (const char *)meta +
			agent_catalog_secondary_offsets[secondary];
		uint bucket;
		uint64 *bits;

		if (text[0] == 0)
			continue;
		bucket = agent_catalog_index_bucket(index, meta);
		bits = agent_catalog_index_bits[index][bucket];
		if (present)
			agent_catalog_bitmap_set(bits, slot);
		else
			agent_catalog_bitmap_clear(bits, slot);
	}
}

static struct agent_catalog_usage *agent_catalog_usage_find(
	uint scope_id, struct workflow_lifecycle_key lifecycle, int create)
{
	struct agent_catalog_usage *free = 0;

	for (uint i = 0; i < AGENT_CATALOG_USAGE_MAX; i++) {
		struct agent_catalog_usage *usage = &agent_catalog_usage[i];

		if (usage->used && usage->scope_id == scope_id &&
		    workflow_lifecycle_key_equal(usage->lifecycle, lifecycle))
			return usage;
		if (!usage->used && free == 0)
			free = usage;
	}
	if (!create || free == 0)
		return 0;
	memset(free, 0, sizeof(*free));
	free->used = 1;
	free->scope_id = scope_id;
	free->lifecycle = lifecycle;
	return free;
}

static struct workflow_lifecycle_key agent_catalog_slot_lifecycle(int slot)
{
	uint usage = agent_catalog_slot_usage[slot];

	if (usage == 0 || usage > AGENT_CATALOG_USAGE_MAX)
		return workflow_lifecycle_none();
	return agent_catalog_usage[usage - 1].lifecycle;
}

static int agent_catalog_target_lifecycle(
	int slot, uint scope_id, struct workflow_lifecycle_key *lifecycle)
{
	*lifecycle = workflow_lifecycle_none();
	if (scope_id == VFS_SCOPE_SYSTEM)
		return 0;
	if (!agent_scope_valid(scope_id))
		return -1;
	if (slot >= 0 && slot < AGENT_FILE_META_MAX &&
	    agent_catalog_files[slot].used &&
	    agent_catalog_scopes[slot] == scope_id) {
		*lifecycle = agent_catalog_slot_lifecycle(slot);
		return workflow_lifecycle_key_valid(*lifecycle) ? 0 : -1;
	}
	return vfs_scope_lifecycle(scope_id, lifecycle);
}

static void agent_catalog_scope_counts(
	uint scope_id, struct workflow_lifecycle_key lifecycle,
	uint *live, uint *autoscan)
{
	struct agent_catalog_usage *usage;

	*live = 0;
	*autoscan = 0;
	usage = agent_catalog_usage_find(scope_id, lifecycle, 0);
	if (usage != 0) {
		*live = usage->live;
		*autoscan = usage->autoscan;
	}
}

static void agent_catalog_primary_candidates(
	const struct agent_file_meta *selector, uint provided,
	uint64 candidates[AGENT_CATALOG_BITMAP_WORDS])
{
	static const uint keys[AGENT_CATALOG_PRIMARY_INDEX_COUNT] = {
		AGENT_CATALOG_KEY_FID,
		AGENT_CATALOG_KEY_PHYSICAL,
		AGENT_CATALOG_KEY_LOGICAL,
		AGENT_CATALOG_KEY_IDENTITY,
	};

	memset(candidates, 0,
	       sizeof(uint64) * AGENT_CATALOG_BITMAP_WORDS);
	for (int index = 0; index < AGENT_CATALOG_PRIMARY_INDEX_COUNT;
	     index++) {
		uint bucket;

		if ((provided & keys[index]) == 0)
			continue;
		bucket = agent_catalog_index_bucket(index, selector);
		for (uint word = 0; word < AGENT_CATALOG_BITMAP_WORDS; word++)
			candidates[word] |=
				agent_catalog_index_bits[index][bucket][word];
	}
}

void agent_metadata_catalog_resolve(
	uint scope_id, const struct agent_file_meta *selector, int except_slot,
	struct agent_catalog_resolution *result)
{
	uint64 candidates[AGENT_CATALOG_BITMAP_WORDS];
	struct workflow_lifecycle_key lifecycle;
	uint owned, autoscan;
	int slot;

	agent_catalog_require_txn();
	memset(result, 0, sizeof(*result));
	result->slot = -1;
	if (agent_catalog_target_lifecycle(
		    except_slot, scope_id, &lifecycle) < 0)
		lifecycle = workflow_lifecycle_none();
	agent_catalog_scope_counts(
		scope_id, lifecycle, &owned, &autoscan);
	result->owned = owned;
	result->autoscan = autoscan;
	result->ordinary = agent_catalog_ordinary;
	if (except_slot >= 0 && except_slot < AGENT_FILE_META_MAX &&
	    agent_catalog_files[except_slot].used) {
		if (agent_catalog_scopes[except_slot] != VFS_SCOPE_SYSTEM)
			result->ordinary--;
		if (agent_catalog_scopes[except_slot] == scope_id &&
		    workflow_lifecycle_key_equal(
			    agent_catalog_slot_lifecycle(except_slot), lifecycle)) {
			result->owned--;
			if (agent_catalog_files[except_slot].flags &
			    AGENT_FILE_META_F_AUTOSCAN)
				result->autoscan--;
		}
	}
	if (selector == 0)
		return;
	result->provided = agent_catalog_key_matches(selector, selector);
	if (result->provided == 0)
		return;
	agent_catalog_primary_candidates(
		selector, result->provided, candidates);
	for (slot = agent_catalog_bitmap_next(candidates, -1); slot >= 0;
	     slot = agent_catalog_bitmap_next(candidates, slot)) {
		uint matched;

		agent_metadata_txn_work_charge(1);
		if (slot == except_slot ||
		    agent_catalog_scopes[slot] != scope_id ||
		    !workflow_lifecycle_key_equal(
			    agent_catalog_slot_lifecycle(slot), lifecycle))
			continue;
		matched = agent_catalog_key_matches(
			selector, &agent_catalog_files[slot]);
		if (matched == 0)
			continue;
		result->matched |= matched;
		if (result->slot == -1)
			result->slot = slot;
		else if (result->slot != slot)
			result->slot = AGENT_CATALOG_CONFLICT;
	}
}
int agent_metadata_catalog_field_contains(
	const char *haystack, const char *needle) {
	int hlen, nlen;
	if (needle == 0 || needle[0] == 0)
		return 1;
	if (haystack == 0)
		return 0;
	hlen = strlen(haystack);
	nlen = strlen(needle);
	if (nlen > hlen)
		return 0;
	for (int i = 0; i <= hlen - nlen; i++)
		if (strncmp(haystack + i, needle, nlen) == 0)
			return 1;
	return 0;
}
static void agent_catalog_require_txn(void) {
	if (!agent_metadata_txn_owned(0))
		panic("Agent catalog transaction invariant");
}
static int agent_catalog_mutation_allowed(void) {
	return agent_catalog_mutation_owner == 0 ||
	       agent_catalog_mutation_owner == agent_metadata_txn_token();
}
static int agent_catalog_fence_owned(
	const struct agent_catalog_mutation_fence *fence) {
	return fence != 0 && fence->token != 0 &&
	       agent_catalog_mutation_owner == agent_metadata_txn_token() &&
	       agent_catalog_mutation_token == fence->token;
}
int agent_metadata_catalog_mutation_begin(
	struct agent_catalog_mutation_fence *fence) {
	agent_catalog_require_txn();
	if (fence == 0 || fence->token != 0 ||
	    agent_catalog_mutation_owner != 0 || agent_catalog_active_edit != 0)
		return AGENT_CATALOG_CONFLICT;
	agent_catalog_mutation_sequence++;
	if (agent_catalog_mutation_sequence == 0)
		agent_catalog_mutation_sequence = 1;
	agent_catalog_mutation_owner = agent_metadata_txn_token();
	agent_catalog_mutation_token = agent_catalog_mutation_sequence;
	fence->token = agent_catalog_mutation_token;
	return 0;
}

int agent_metadata_catalog_mutation_end(
	struct agent_catalog_mutation_fence *fence) {
	int clean;
	agent_catalog_require_txn();
	if (!agent_catalog_fence_owned(fence))
		return AGENT_CATALOG_CONFLICT;
	clean = agent_catalog_active_edit == 0;
	agent_catalog_active_edit = 0;
	agent_catalog_mutation_owner = 0;
	agent_catalog_mutation_token = 0;
	fence->token = 0;
	return clean ? 0 : AGENT_CATALOG_CONFLICT;
}

static uint64 agent_catalog_undo_binding(
	const struct agent_catalog_undo_token *undo, int slot) {
	struct workflow_lifecycle_key lifecycle =
		agent_catalog_slot_lifecycle(slot);
	uint64 hash = AGENT_CATALOG_PLAN_HASH;
	hash = agent_catalog_hash_bytes(hash, &undo->fence_token,
					sizeof(undo->fence_token));
	hash = agent_catalog_hash_bytes(hash, &undo->catalog_generation,
					sizeof(undo->catalog_generation));
	hash = agent_catalog_hash_bytes(hash, &slot, sizeof(slot));
	hash = agent_catalog_hash_bytes(hash, &undo->reserved,
					sizeof(undo->reserved));
	hash = agent_catalog_hash_bytes(hash, &agent_catalog_scopes[slot],
					sizeof(agent_catalog_scopes[slot]));
	hash = agent_catalog_hash_bytes(hash, &lifecycle, sizeof(lifecycle));
	hash = agent_catalog_hash_bytes(hash, &agent_catalog_files[slot],
					sizeof(agent_catalog_files[slot]));
	return hash == 0 ? AGENT_CATALOG_PLAN_HASH : hash;
}

int agent_metadata_catalog_undo_capture(
	const struct agent_catalog_mutation_fence *fence, int slot,
	struct agent_catalog_undo_token *undo) {
	agent_catalog_require_txn();
	if (!agent_catalog_fence_owned(fence) || undo == 0 ||
	    slot < 0 || slot >= AGENT_FILE_META_MAX)
		return AGENT_CATALOG_CONFLICT;
	memset(undo, 0, sizeof(*undo));
	undo->fence_token = fence->token;
	undo->catalog_generation = agent_catalog_generation;
	undo->slot = slot;
	undo->slot_binding = agent_catalog_undo_binding(undo, slot);
	return 0;
}

int agent_metadata_catalog_undo_note_created(
	const struct agent_catalog_mutation_fence *fence,
	struct agent_catalog_undo_token *undo) {
	int slot = undo ? undo->slot : -1;
	agent_catalog_require_txn();
	if (!agent_catalog_fence_owned(fence) || undo == 0 ||
	    undo->reserved != 0 || undo->fence_token != fence->token)
		return AGENT_CATALOG_CONFLICT;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX ||
	    agent_metadata_catalog_identity_state(&agent_catalog_files[slot]) <= 0 ||
	    undo->slot_binding != agent_catalog_undo_binding(undo, slot))
		return AGENT_CATALOG_CONFLICT;
	undo->reserved = AGENT_CATALOG_UNDO_CREATED;
	undo->slot_binding = agent_catalog_undo_binding(undo, slot);
	return 0;
}

static void agent_catalog_storage_reset(void)
{
	memset(agent_catalog_files, 0, sizeof(agent_catalog_files));
	memset(agent_catalog_scopes, 0, sizeof(agent_catalog_scopes));
	memset(agent_catalog_index_bits, 0, sizeof(agent_catalog_index_bits));
	memset(agent_catalog_free_bits, 0xff, sizeof(agent_catalog_free_bits));
	if (AGENT_FILE_META_MAX % 64 != 0)
		agent_catalog_free_bits[AGENT_CATALOG_BITMAP_WORDS - 1] &=
			(1ULL << (AGENT_FILE_META_MAX % 64)) - 1;
	memset(agent_catalog_ready_bits, 0, sizeof(agent_catalog_ready_bits));
	memset(agent_catalog_live_query_bits, 0,
	       sizeof(agent_catalog_live_query_bits));
	memset(agent_catalog_usage, 0, sizeof(agent_catalog_usage));
	memset(agent_catalog_slot_usage, 0, sizeof(agent_catalog_slot_usage));
	agent_catalog_live = 0;
	agent_catalog_system = 0;
	agent_catalog_ordinary = 0;
}

static void agent_catalog_derived_remove(int slot)
{
	struct agent_catalog_usage *usage;
	uint usage_slot;
	uint64 fid;

	if (!agent_catalog_files[slot].used)
		return;
	usage_slot = agent_catalog_slot_usage[slot];
	if (usage_slot == 0 || usage_slot > AGENT_CATALOG_USAGE_MAX)
		panic("Agent catalog usage invariant");
	usage = &agent_catalog_usage[usage_slot - 1];
	if (!usage->used || usage->scope_id != agent_catalog_scopes[slot] ||
	    usage->live == 0 || agent_catalog_live == 0 ||
	    !agent_catalog_bitmap_test(usage->slots, slot))
		panic("Agent catalog derived remove invariant");
	agent_catalog_index_update(
		slot, &agent_catalog_files[slot], 0, 0);
	agent_catalog_bitmap_clear(agent_catalog_ready_bits, slot);
	agent_catalog_bitmap_clear(agent_catalog_live_query_bits, slot);
	agent_catalog_bitmap_set(agent_catalog_free_bits, slot);
	agent_catalog_bitmap_clear(usage->slots, slot);
	agent_catalog_live--;
	usage->live--;
	if (agent_catalog_scopes[slot] == VFS_SCOPE_SYSTEM) {
		if (agent_catalog_system == 0)
			panic("Agent catalog system count invariant");
		agent_catalog_system--;
	} else {
		if (agent_catalog_ordinary == 0)
			panic("Agent catalog ordinary count invariant");
		agent_catalog_ordinary--;
	}
	if (agent_catalog_files[slot].flags & AGENT_FILE_META_F_AUTOSCAN) {
		if (usage->autoscan == 0)
			panic("Agent catalog autoscan count invariant");
		usage->autoscan--;
	}
	fid = agent_catalog_files[slot].fid;
	if (fid > 0 && fid <= AGENT_FILE_META_MAX)
		agent_catalog_bitmap_clear(usage->fids, fid - 1);
	agent_catalog_slot_usage[slot] = 0;
	if (usage->live == 0)
		memset(usage, 0, sizeof(*usage));
}

static void agent_catalog_derived_add(
	int slot, uint scope_id, struct workflow_lifecycle_key lifecycle,
	const struct agent_file_meta *meta)
{
	struct agent_catalog_usage *usage;
	uint usage_slot;
	uint64 fid;

	if (meta == 0 || !meta->used)
		return;
	if (!agent_object_scope_valid(scope_id) ||
	    (scope_id == VFS_SCOPE_SYSTEM ?
	     !workflow_lifecycle_key_equal(
		     lifecycle, workflow_lifecycle_none()) :
	     !workflow_lifecycle_key_valid(lifecycle)))
		panic("Agent catalog lifecycle index invariant");
	usage = agent_catalog_usage_find(scope_id, lifecycle, 1);
	if (usage == 0)
		panic("Agent catalog usage capacity invariant");
	usage_slot = (uint)(usage - agent_catalog_usage) + 1;
	if (agent_catalog_slot_usage[slot] != 0 ||
	    !agent_catalog_bitmap_test(agent_catalog_free_bits, slot))
		panic("Agent catalog derived add invariant");
	agent_catalog_slot_usage[slot] = usage_slot;
	agent_catalog_bitmap_clear(agent_catalog_free_bits, slot);
	agent_catalog_bitmap_set(usage->slots, slot);
	usage->live++;
	agent_catalog_live++;
	if (scope_id == VFS_SCOPE_SYSTEM)
		agent_catalog_system++;
	else
		agent_catalog_ordinary++;
	if (meta->flags & AGENT_FILE_META_F_AUTOSCAN)
		usage->autoscan++;
	agent_catalog_bitmap_set(agent_catalog_ready_bits, slot);
	if ((meta->flags & AGENT_FILE_META_F_AUTOSCAN) == 0)
		agent_catalog_bitmap_set(agent_catalog_live_query_bits, slot);
	fid = meta->fid;
	if (fid > 0 && fid <= AGENT_FILE_META_MAX) {
		if (agent_catalog_bitmap_test(usage->fids, fid - 1))
			panic("Agent catalog duplicate fid invariant");
		agent_catalog_bitmap_set(usage->fids, fid - 1);
	}
	agent_catalog_index_update(slot, meta, 0, 1);
}

static void agent_catalog_changed(uint changes)
{
	if (agent_catalog_active_edit != 0 || !agent_catalog_mutation_allowed())
		panic("Agent catalog edit invariant");
	(void)changes;
	agent_catalog_generation++;
	if (agent_catalog_generation == 0)
		agent_catalog_generation = 1;
}

static void agent_catalog_slot_publish(
	int slot, const struct agent_file_meta *next, uint next_scope,
	struct workflow_lifecycle_key next_lifecycle,
	uint changes, uint flags)
{
	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		panic("Agent catalog publish slot invariant");
	agent_catalog_derived_remove(slot);
	if (next != 0 && next->used) {
		agent_catalog_files[slot] = *next;
		agent_catalog_scopes[slot] = next_scope;
		agent_catalog_derived_add(
			slot, next_scope, next_lifecycle,
			&agent_catalog_files[slot]);
	} else {
		memset(&agent_catalog_files[slot], 0,
		       sizeof(agent_catalog_files[slot]));
		agent_catalog_scopes[slot] = VFS_SCOPE_NONE;
	}
	if (flags & AGENT_CATALOG_PUBLISH_GENERATION)
		agent_catalog_changed(changes);
}

void
agent_metadata_catalog_init(void)
{
	agent_catalog_storage_reset();
	agent_catalog_active_edit = 0;
	agent_catalog_generation = 1;
	agent_catalog_mutation_owner = 0;
	agent_catalog_mutation_sequence = 0;
	agent_catalog_mutation_token = 0;
}

uint64 agent_metadata_catalog_generation(void) {
	agent_catalog_require_txn();
	return agent_catalog_generation;
}

int agent_metadata_catalog_borrow_scan(
	int slot, struct agent_catalog_view *view) {
	agent_catalog_require_txn();
	if (view == 0 || slot < 0 || slot >= AGENT_FILE_META_MAX)
		return -1;
	view->meta = 0;
	view->lifecycle = workflow_lifecycle_none();
	if (!agent_catalog_files[slot].used)
		return 0;
	view->meta = &agent_catalog_files[slot];
	view->scope_id = agent_catalog_scopes[slot];
	view->lifecycle = agent_catalog_slot_lifecycle(slot);
	return 1;
}

int agent_metadata_catalog_borrow(
	uint64 generation, int slot, struct agent_catalog_view *view) {
	agent_catalog_require_txn();
	if (generation != 0 && generation != agent_catalog_generation)
		return AGENT_CATALOG_STALE;
	return agent_metadata_catalog_borrow_scan(slot, view);
}

int
agent_metadata_catalog_read_begin(
	uint scope_id, int index, const char *key, int force_scan,
	struct agent_catalog_read_snapshot *snapshot, int *bucket_out)
{
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	struct agent_catalog_usage *scope_usage;
	struct agent_catalog_usage *system_usage;
	int index_slot = index - 1;
	int bitmap_index = AGENT_CATALOG_PRIMARY_INDEX_COUNT + index_slot;
	uint bucket = 0;
	int enabled;

	if (snapshot == 0 || !agent_scope_valid(scope_id) ||
	    (!force_scan && index != 0 &&
	     (index_slot < 0 ||
	      index_slot >= AGENT_CATALOG_SECONDARY_INDEX_COUNT)) ||
	    (!force_scan && index != 0 && (key == 0 || key[0] == 0)))
		return AGENT_CATALOG_STALE;
	if (!force_scan && index != 0)
		bucket = agent_catalog_hash_bytes(
			AGENT_CATALOG_PLAN_HASH, key, strlen(key)) %
			 AGENT_CATALOG_INDEX_BUCKETS;
	memset(snapshot, 0, sizeof(*snapshot));
	enabled = intr_save();
	if (agent_catalog_mutation_owner != 0) {
		intr_restore(enabled);
		return AGENT_CATALOG_CONFLICT;
	}
	if (vfs_scope_lifecycle(scope_id, &lifecycle) < 0) {
		intr_restore(enabled);
		return AGENT_CATALOG_STALE;
	}
	snapshot->generation = agent_catalog_generation;
	snapshot->fs_generation =
		agent_file_state_scope_generation(scope_id);
	snapshot->scope_id = scope_id;
	snapshot->lifecycle = lifecycle;
	scope_usage = agent_catalog_usage_find(scope_id, lifecycle, 0);
	system_usage = agent_catalog_usage_find(
		VFS_SCOPE_SYSTEM, workflow_lifecycle_none(), 0);
	for (uint word = 0; word < AGENT_CATALOG_BITMAP_WORDS; word++) {
		uint64 visible =
			(scope_usage ? scope_usage->slots[word] : 0) |
			(system_usage ? system_usage->slots[word] : 0);
		uint64 candidates = force_scan || index == 0 ?
			agent_catalog_ready_bits[word] :
			agent_catalog_index_bits[bitmap_index][bucket][word];

		snapshot->candidates[word] =
			candidates & agent_catalog_ready_bits[word] &
			agent_catalog_live_query_bits[word] & visible;
	}
	intr_restore(enabled);
	if (bucket_out)
		*bucket_out = index == 0 || force_scan ? -1 : (int)bucket;
	return 0;
}

int
agent_metadata_catalog_read_next(
	const struct agent_catalog_read_snapshot *snapshot, int after)
{
	if (snapshot == 0 || snapshot->generation == 0)
		return -1;
	return agent_catalog_bitmap_next(snapshot->candidates, after);
}

int
agent_metadata_catalog_read_copy(
	const struct agent_catalog_read_snapshot *snapshot, int slot,
	struct agent_file_meta *meta, uint *scope_id)
{
	struct workflow_lifecycle_key lifecycle;
	uint owner;
	int enabled;

	if (snapshot == 0 || snapshot->generation == 0 || meta == 0 ||
	    scope_id == 0 || slot < 0 || slot >= AGENT_FILE_META_MAX ||
	    !agent_catalog_bitmap_test(snapshot->candidates, slot))
		return -1;
	enabled = intr_save();
	if (agent_catalog_mutation_owner != 0) {
		intr_restore(enabled);
		return AGENT_CATALOG_CONFLICT;
	}
	if (snapshot->generation != agent_catalog_generation) {
		intr_restore(enabled);
		return AGENT_CATALOG_STALE;
	}
	if (!agent_catalog_files[slot].used) {
		intr_restore(enabled);
		return 0;
	}
	owner = agent_catalog_scopes[slot];
	lifecycle = agent_catalog_slot_lifecycle(slot);
	if ((owner == snapshot->scope_id &&
	     !workflow_lifecycle_key_equal(
		     lifecycle, snapshot->lifecycle)) ||
	    (owner == VFS_SCOPE_SYSTEM &&
	     !workflow_lifecycle_key_equal(
		     lifecycle, workflow_lifecycle_none()))) {
		intr_restore(enabled);
		return AGENT_CATALOG_STALE;
	}
	*meta = agent_catalog_files[slot];
	*scope_id = owner;
	intr_restore(enabled);
	agent_file_state_overlay_published_size(meta, owner);
	return 1;
}

int
agent_metadata_catalog_read_end(
	const struct agent_catalog_read_snapshot *snapshot)
{
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	int stable;
	int enabled;

	if (snapshot == 0 || snapshot->generation == 0)
		return 0;
	enabled = intr_save();
	stable = agent_catalog_mutation_owner == 0 &&
		 vfs_scope_lifecycle(snapshot->scope_id, &lifecycle) == 0 &&
		 workflow_lifecycle_key_equal(lifecycle, snapshot->lifecycle) &&
		 snapshot->generation == agent_catalog_generation &&
		 snapshot->fs_generation ==
			agent_file_state_scope_generation(snapshot->scope_id);
	intr_restore(enabled);
	return stable;
}

static int agent_catalog_edit_begin(
	int slot, uint scope_id, struct agent_catalog_edit *edit) {
	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	if (edit == 0 || agent_catalog_active_edit != 0 || slot < 0 ||
	    slot >= AGENT_FILE_META_MAX)
		return -1;
	if (agent_catalog_files[slot].used)
		scope_id = agent_catalog_scopes[slot];
	else if (!agent_object_scope_valid(scope_id))
		return -1;
	agent_catalog_edit_buffer = agent_catalog_files[slot];
	edit->meta = &agent_catalog_edit_buffer;
	edit->scope_id = scope_id;
	edit->slot = slot;
	agent_catalog_active_edit = edit;
	return agent_catalog_edit_buffer.used ? 1 : 0;
}

int agent_metadata_catalog_edit_begin(int slot, uint scope_id,
	struct agent_catalog_edit *edit) {
	return agent_catalog_edit_begin(slot, scope_id, edit);
}

static int agent_catalog_edit_commit(
	struct agent_catalog_edit *edit, uint changes)
{
	struct workflow_lifecycle_key lifecycle;
	int admission = 1;
	int growth = 0;
	int slot;
	uint scope_id;
	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed()) {
		agent_metadata_catalog_edit_abort(edit);
		return AGENT_CATALOG_CONFLICT;
	}
	if (edit == 0 || agent_catalog_active_edit != edit)
		return -1;
	if (edit->meta != &agent_catalog_edit_buffer || edit->slot < 0 ||
	    edit->slot >= AGENT_FILE_META_MAX) {
		agent_metadata_catalog_edit_abort(edit);
		return -1;
	}
	if ((agent_catalog_files[edit->slot].used &&
	      (agent_catalog_files[edit->slot].flags &
	       (AGENT_FILE_META_F_PERSIST | AGENT_FILE_META_F_AUTOSCAN))) ||
	     (edit->meta->used &&
	      (edit->meta->flags &
	       (AGENT_FILE_META_F_PERSIST | AGENT_FILE_META_F_AUTOSCAN)))) {
		agent_metadata_catalog_edit_abort(edit);
		return AGENT_CATALOG_CONFLICT;
	}
	if (edit->meta->used) {
		edit->meta->physical_name[
			sizeof(edit->meta->physical_name) - 1] = 0;
		agent_catalog_normalize_physical(edit->slot, edit->meta);
	}
	if (edit->meta->used &&
	    agent_object_scope_valid(edit->scope_id) &&
	    agent_metadata_catalog_identity_state(edit->meta) >= 0 &&
	    edit->slot >= 0 && edit->slot < AGENT_FILE_META_MAX) {
		growth = !agent_catalog_files[edit->slot].used;
		admission = agent_catalog_admission(
			edit->scope_id, edit->slot, edit->meta,
			growth ? 0 : agent_catalog_files[edit->slot].flags,
			edit->meta->flags, growth);
	}
	if ((edit->meta->used &&
	     (!agent_object_scope_valid(edit->scope_id) ||
	      agent_metadata_catalog_identity_state(edit->meta) < 0 ||
	      admission <= 0 ||
	      (agent_catalog_files[edit->slot].used &&
	       agent_catalog_scopes[edit->slot] != edit->scope_id))) ||
	    (agent_catalog_files[edit->slot].used && !edit->meta->used)) {
		agent_metadata_catalog_edit_abort(edit);
		return admission < 0 ? admission : -1;
	}
	if (agent_catalog_files[edit->slot].used && edit->meta->used &&
	    (agent_catalog_files[edit->slot].dev != edit->meta->dev ||
	     agent_catalog_files[edit->slot].inum != edit->meta->inum ||
	     agent_catalog_files[edit->slot].incarnation !=
		     edit->meta->incarnation) &&
	    agent_catalog_unbind(edit->slot,
				 &agent_catalog_files[edit->slot],
				 agent_catalog_scopes[edit->slot]) < 0) {
		agent_metadata_catalog_edit_abort(edit);
		return -1;
	}
	slot = edit->slot;
	scope_id = edit->scope_id;
	if (agent_catalog_target_lifecycle(slot, scope_id, &lifecycle) < 0) {
		agent_metadata_catalog_edit_abort(edit);
		return AGENT_CATALOG_INTERRUPTED;
	}
	agent_metadata_catalog_edit_abort(edit);
	agent_catalog_slot_publish(
		slot, &agent_catalog_edit_buffer, scope_id, lifecycle,
		changes, AGENT_CATALOG_PUBLISH_GENERATION);
	return 0;
}

int
agent_metadata_catalog_edit_commit_volatile(
	struct agent_catalog_edit *edit, uint changes)
{
	return agent_catalog_edit_commit(edit, changes);
}

void agent_metadata_catalog_edit_abort(struct agent_catalog_edit *edit) {
	agent_catalog_require_txn();
	if (edit != 0 && agent_catalog_active_edit == edit) {
		agent_catalog_active_edit = 0;
		edit->meta = 0;
	}
}

static void agent_catalog_normalize_physical(int slot, struct agent_file_meta *meta) {
	if (meta->physical_name[0] == 0 ||
	    strlen(meta->physical_name) > DIRSIZ) {
		memset(meta->physical_name, 0, sizeof(meta->physical_name));
		meta->physical_name[0] = 'a';
		meta->physical_name[1] = 'f';
		meta->physical_name[2] = '0' + (slot / 100) % 10;
		meta->physical_name[3] = '0' + (slot / 10) % 10;
		meta->physical_name[4] = '0' + slot % 10;
	}
}

static struct inode *agent_catalog_lookup_or_create_status(
	char *name, int create, uint scope_id, struct proc *actor, int *status,
	int *created) {
	struct inode *ip;
	struct vfs_cred actor_cred;
	int lookup_status = FS_LOOKUP_ERROR;
	if (status)
		*status = FS_LOOKUP_ERROR;
	if (created)
		*created = 0;
	if (scope_id == VFS_SCOPE_SYSTEM && create)
		return 0;
	if (scope_id != VFS_SCOPE_SYSTEM &&
	    (!agent_scope_valid(scope_id) ||
	     (create ? !vfs_scope_active(scope_id) : !vfs_scope_retained(scope_id))))
		return 0;
	if ((ip = namei_scope_status(name, VFS_POLICY_WORKFLOW, scope_id,
				     &lookup_status)) != 0) {
		lookup_status = ivalid(ip);
		if (lookup_status < 0) {
			if (status)
				*status = lookup_status;
			iput(ip);
			return 0;
		}
		if (ip->type == T_FILE && vfs_inode_label_valid(ip) &&
		    ip->vfs_policy == VFS_POLICY_WORKFLOW &&
		    ip->vfs_scope_id == scope_id) {
			if (status)
				*status = FS_LOOKUP_FOUND;
			return ip;
		}
		iput(ip);
		return 0;
	}
	if (lookup_status != FS_LOOKUP_ABSENT)
		return 0;
	if (!create) {
		if (status)
			*status = FS_LOOKUP_ABSENT;
		return 0;
	}
	if (actor == 0)
		return 0;
	vfs_cred_from_proc(actor, &actor_cred);
	if (actor_cred.scope_id != scope_id)
		return 0;
	ip = fs_create(name, T_FILE, created, &actor_cred, VFS_POLICY_WORKFLOW,
		       &lookup_status);
	if (ip != 0 && status)
		*status = FS_LOOKUP_FOUND;
	else if (ip == 0 && status)
		*status = lookup_status;
	return ip;
}

static int agent_catalog_unbind(int slot, struct agent_file_meta *meta,
				uint scope_id) {
	struct inode *ip;
	int identity;
	int result = 0;
	int lookup_status = FS_LOOKUP_ERROR;
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX || meta == 0 ||
	    !meta->used || meta->physical_name[0] == 0)
		return 0;
	identity = agent_metadata_catalog_identity_state(meta);
	if (identity == 0)
		goto invalidated;
	ip = meta->dev != 0 && meta->inum != 0 ?
		inode_get(meta->dev, meta->inum) : 0;
	if (ip == 0) {
		ip = namei_scope_status(meta->physical_name,
					VFS_POLICY_WORKFLOW, scope_id,
					&lookup_status);
		if (ip == 0) {
			if (lookup_status == FS_LOOKUP_ABSENT)
				goto invalidated;
			return -1;
		}
	}
	if (ivalid(ip) < 0) {
		iput(ip);
		return -1;
	}
	if (ip->agent_meta_slot == slot + 1 &&
	    ip->dev == meta->dev && ip->inum == meta->inum &&
	    ip->vfs_incarnation == meta->incarnation)
		result = agent_file_state_set_index(ip, 0, 0);
	iput(ip);
	if (result < 0)
		return result;
invalidated:
	if (identity > 0)
		agent_file_state_unbind_catalog_identity(
			meta->dev, meta->inum, meta->incarnation, scope_id);
	return 0;
}

static int agent_catalog_bind_status(
	int slot, struct agent_file_meta *meta, uint scope_id, uint state,
	int create, struct proc *actor, int *lookup_status) {
	struct inode *ip;
	if (lookup_status)
		*lookup_status = FS_LOOKUP_ERROR;
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX || meta == 0)
		return -1;
	if (!meta->used || state != 0 || !agent_object_scope_valid(scope_id))
		return -1;
	if (meta->physical_name[0] == 0 ||
	    strlen(meta->physical_name) > DIRSIZ)
		return -1;
	ip = agent_catalog_lookup_or_create_status(meta->physical_name, create,
					   scope_id, actor,
					   lookup_status, &create);
	if (ip == 0)
		return create == FS_CREATE_INDETERMINATE ?
			AGENT_CATALOG_INDETERMINATE : -1;
	if ((meta->dev != 0 || meta->inum != 0 || meta->incarnation != 0) &&
	    (meta->dev != ip->dev || meta->inum != ip->inum || meta->incarnation != ip->vfs_incarnation)) {
		if (lookup_status)
			*lookup_status = FS_LOOKUP_ABSENT;
		goto out;
	}
	if (agent_file_state_set_index(ip, slot + 1, 0) < 0)
		goto out;
	meta->dev = ip->dev;
	meta->inum = ip->inum;
	meta->incarnation = ip->vfs_incarnation;
	meta->size = ip->size;
	meta->fs_generation = agent_file_state_generation_next(scope_id);
	iput(ip);
	return create;
out:
	if (create) {
		uint dev = ip->dev, inum = ip->inum;
		uint incarnation = ip->vfs_incarnation;
		iput(ip);
		return fs_rollback_created_workflow(meta->physical_name, dev, inum,
			incarnation, scope_id) < 0 ?
			AGENT_CATALOG_INDETERMINATE : -1;
	}
	iput(ip);
	return -1;
}

static int
agent_catalog_bind(int slot, int create, struct proc *actor)
{
	struct workflow_lifecycle_key lifecycle;
	int result;
	uint scope_id;

	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX ||
	    !agent_catalog_files[slot].used)
		return -1;
	if (agent_catalog_files[slot].flags &
	    (AGENT_FILE_META_F_PERSIST | AGENT_FILE_META_F_AUTOSCAN))
		return AGENT_CATALOG_CONFLICT;
	scope_id = agent_catalog_scopes[slot];
	if (agent_catalog_target_lifecycle(slot, scope_id, &lifecycle) < 0)
		return AGENT_CATALOG_INTERRUPTED;
	agent_catalog_edit_buffer = agent_catalog_files[slot];
	result = agent_catalog_bind_status(
		slot, &agent_catalog_edit_buffer, scope_id, 0,
		create, actor, 0);
	if (result >= 0)
		agent_catalog_slot_publish(
			slot, &agent_catalog_edit_buffer, scope_id, lifecycle,
			0, AGENT_CATALOG_PUBLISH_GENERATION);
	return result;
}

int
agent_metadata_catalog_bind_volatile(int slot, int create, struct proc *actor)
{
	return agent_catalog_bind(slot, create, actor);
}

static int agent_catalog_clear_slot(int slot)
{
	int was_used;
	uint scope_id;
	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return -1;
	if (agent_catalog_files[slot].used &&
	    (agent_catalog_files[slot].flags &
	     (AGENT_FILE_META_F_PERSIST | AGENT_FILE_META_F_AUTOSCAN)))
		return AGENT_CATALOG_CONFLICT;
	was_used = agent_catalog_files[slot].used;
	scope_id = agent_catalog_scopes[slot];
	if (agent_catalog_unbind(slot, &agent_catalog_files[slot], scope_id) < 0)
		return -1;
	agent_catalog_slot_publish(
		slot, 0, VFS_SCOPE_NONE, workflow_lifecycle_none(),
		AGENT_FILE_CHANGE_MEMBERSHIP,
		AGENT_CATALOG_PUBLISH_GENERATION);
	if (was_used)
		agent_file_state_generation_next(scope_id);
	return 0;
}

int
agent_metadata_catalog_clear_slot_volatile(int slot)
{
	return agent_catalog_clear_slot(slot);
}

static int
agent_catalog_forget_slot_volatile(int slot)
{
	uint scope_id;

	if (slot < 0 || slot >= AGENT_FILE_META_MAX ||
	    !agent_catalog_files[slot].used ||
	    (agent_catalog_files[slot].flags &
	     (AGENT_FILE_META_F_PERSIST | AGENT_FILE_META_F_AUTOSCAN)))
		return AGENT_CATALOG_CONFLICT;
	scope_id = agent_catalog_scopes[slot];
	agent_catalog_slot_publish(
		slot, 0, VFS_SCOPE_NONE, workflow_lifecycle_none(),
		AGENT_FILE_CHANGE_MEMBERSHIP,
		AGENT_CATALOG_PUBLISH_GENERATION);
	agent_file_state_generation_next(scope_id);
	return 0;
}

int
agent_metadata_catalog_remove_identity_exact(
	struct workflow_lifecycle_key lifecycle, uint scope_id,
	uint dev, uint inum, uint incarnation,
	struct agent_file_meta *previous, uint64 *generation)
{
	struct agent_catalog_resolution resolution;
	struct workflow_lifecycle_key current = workflow_lifecycle_none();
	struct agent_file_meta selector;
	int slot;

	agent_catalog_require_txn();
	if (previous == 0 || generation == 0)
		return -1;
	memset(previous, 0, sizeof(*previous));
	*generation = 0;
	if (!agent_catalog_mutation_allowed() || !agent_scope_valid(scope_id) ||
	    !workflow_lifecycle_key_valid(lifecycle) || dev == 0 || inum == 0 ||
	    incarnation == 0 || vfs_scope_lifecycle(scope_id, &current) < 0 ||
	    !workflow_lifecycle_key_equal(current, lifecycle))
		return AGENT_CATALOG_CONFLICT;
	memset(&selector, 0, sizeof(selector));
	selector.dev = dev;
	selector.inum = inum;
	selector.incarnation = incarnation;
	agent_metadata_catalog_resolve(scope_id, &selector, -1, &resolution);
	if (resolution.slot == AGENT_CATALOG_CONFLICT)
		return AGENT_CATALOG_CONFLICT;
	if (resolution.slot < 0)
		return 0;
	slot = resolution.slot;
	if ((resolution.matched & AGENT_CATALOG_KEY_IDENTITY) == 0 ||
	    !agent_catalog_files[slot].used ||
	    agent_catalog_scopes[slot] != scope_id ||
	    !workflow_lifecycle_key_equal(
		agent_catalog_slot_lifecycle(slot), lifecycle) ||
	    agent_catalog_files[slot].dev != dev ||
	    agent_catalog_files[slot].inum != inum ||
	    agent_catalog_files[slot].incarnation != incarnation ||
	    (agent_catalog_files[slot].flags &
	     (AGENT_FILE_META_F_PERSIST | AGENT_FILE_META_F_AUTOSCAN)) != 0)
		return AGENT_CATALOG_CONFLICT;
	*previous = agent_catalog_files[slot];
	if (agent_catalog_clear_slot(slot) < 0) {
		memset(previous, 0, sizeof(*previous));
		return -1;
	}
	*generation = agent_file_state_scope_generation(scope_id);
	return 1;
}

static int agent_catalog_restore(
	const struct agent_catalog_mutation_fence *fence,
	const struct agent_catalog_undo_token *undo,
	const struct agent_file_meta *previous, uint previous_scope,
	int had_previous)
{
	struct agent_catalog_resolution result;
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	int slot;
	agent_catalog_require_txn();
	if (!agent_catalog_fence_owned(fence) || undo == 0 ||
	    (undo->reserved & ~AGENT_CATALOG_UNDO_CREATED) != 0 ||
	    undo->fence_token != fence->token)
		return AGENT_CATALOG_CONFLICT;
	slot = undo->slot;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX ||
	    undo->slot_binding != agent_catalog_undo_binding(undo, slot))
		return AGENT_CATALOG_CONFLICT;
	if (agent_catalog_files[slot].used &&
	    (agent_catalog_files[slot].flags &
	     (AGENT_FILE_META_F_PERSIST | AGENT_FILE_META_F_AUTOSCAN)))
		return AGENT_CATALOG_CONFLICT;
	if (had_previous != 0 && had_previous != 1)
		return -1;
	if (had_previous) {
		if (previous == 0 || !previous->used ||
		    !agent_object_scope_valid(previous_scope) ||
		    previous->physical_name[0] == 0 ||
		    agent_metadata_catalog_identity_state(previous) < 0 ||
		    (previous->flags & (AGENT_FILE_META_F_PERSIST |
				       AGENT_FILE_META_F_AUTOSCAN)))
			return -1;
		if (agent_catalog_hard_admission(
			    previous_scope, slot, previous, &result) <= 0)
			return AGENT_CATALOG_CONFLICT;
		if (agent_catalog_target_lifecycle(
			    slot, previous_scope, &lifecycle) < 0)
			return AGENT_CATALOG_INTERRUPTED;
	}
	if (agent_catalog_unbind(slot, &agent_catalog_files[slot],
				 agent_catalog_scopes[slot]) < 0)
		return -1;
	if ((undo->reserved & AGENT_CATALOG_UNDO_CREATED) &&
	    fs_rollback_created_workflow(
		agent_catalog_files[slot].physical_name,
		agent_catalog_files[slot].dev, agent_catalog_files[slot].inum,
		agent_catalog_files[slot].incarnation,
		agent_catalog_scopes[slot]) < 0)
		return -1;
	if (had_previous) {
		agent_catalog_edit_buffer = *previous;
		if (agent_catalog_bind_status(
			    slot, &agent_catalog_edit_buffer, previous_scope, 0,
			    0, 0, 0) < 0)
			return -1;
	}
	agent_catalog_slot_publish(
		slot, had_previous ? &agent_catalog_edit_buffer : 0,
		had_previous ? previous_scope : VFS_SCOPE_NONE, lifecycle,
		AGENT_FILE_CHANGE_MEMBERSHIP,
		AGENT_CATALOG_PUBLISH_GENERATION);
	return 0;
}

int
agent_metadata_catalog_restore_volatile(
	const struct agent_catalog_mutation_fence *fence,
	const struct agent_catalog_undo_token *undo,
	const struct agent_file_meta *previous, uint previous_scope,
	int had_previous)
{
	return agent_catalog_restore(
		fence, undo, previous, previous_scope, had_previous);
}

static int agent_catalog_scope_admissible(
	uint scope_id, struct workflow_lifecycle_key *lifecycle) {
	return vfs_scope_lifecycle(scope_id, lifecycle) >= 0 &&
	       (workflow_lifecycle_active(*lifecycle) ||
		workflow_lifecycle_closing(*lifecycle));
}

static int agent_catalog_hard_admission(
	uint scope_id, int except_slot, const struct agent_file_meta *candidate,
	struct agent_catalog_resolution *result) {
	int limit = scope_id == VFS_SCOPE_SYSTEM ?
		AGENT_FILE_SYSTEM_LIMIT : AGENT_FILE_SCOPE_LIMIT;
	agent_metadata_catalog_resolve(scope_id, candidate, except_slot, result);
	if (result->matched)
		return AGENT_CATALOG_CONFLICT;
	if (result->owned >= limit ||
	    (scope_id != VFS_SCOPE_SYSTEM &&
	     result->ordinary >= AGENT_FILE_ORDINARY_LIMIT))
		return AGENT_CATALOG_NO_SPACE;
	return 1;
}

static int agent_catalog_admission(
	uint scope_id, int except_slot, const struct agent_file_meta *candidate,
	uint old_flags, uint flags, int growth) {
	struct agent_catalog_resolution result;
	struct workflow_lifecycle_key lifecycle;
	int admission;

	agent_catalog_require_txn();
	admission = agent_catalog_hard_admission(
		scope_id, except_slot, candidate, &result);
	if (admission <= 0)
		return admission;
	if (scope_id != VFS_SCOPE_SYSTEM &&
	    (flags & AGENT_FILE_META_F_AUTOSCAN) &&
	    !(old_flags & AGENT_FILE_META_F_AUTOSCAN) &&
	    result.autoscan >= AGENT_FILE_AUTOSCAN_SCOPE_LIMIT)
		return AGENT_CATALOG_NO_SPACE;
	if (!growth || scope_id == VFS_SCOPE_SYSTEM)
		return 1;
	if (!agent_catalog_scope_admissible(scope_id, &lifecycle))
		return AGENT_CATALOG_INTERRUPTED;
	return 1;
}
int agent_metadata_catalog_alloc_slot(uint scope_id, uint flags) {
	int admission;
	int slot;

	agent_catalog_require_txn();
	admission = agent_catalog_admission(scope_id, -1, 0, 0, flags, 1);
	if (admission <= 0)
		return admission;
	slot = agent_catalog_bitmap_next(agent_catalog_free_bits, -1);
	agent_metadata_txn_work_charge(1);
	return slot >= 0 ? slot : AGENT_CATALOG_NO_SPACE;
}

uint64 agent_metadata_catalog_alloc_fid(uint scope_id) {
	struct workflow_lifecycle_key lifecycle;
	struct agent_catalog_usage *usage;

	agent_catalog_require_txn();
	if (agent_catalog_target_lifecycle(-1, scope_id, &lifecycle) < 0)
		return 0;
	usage = agent_catalog_usage_find(scope_id, lifecycle, 0);
	for (uint word = 0; word < AGENT_CATALOG_BITMAP_WORDS; word++) {
		uint64 candidates = usage == 0 ? ~0ULL : ~usage->fids[word];

		if (word + 1 == AGENT_CATALOG_BITMAP_WORDS &&
		    AGENT_FILE_META_MAX % 64 != 0)
			candidates &=
				(1ULL << (AGENT_FILE_META_MAX % 64)) - 1;
		agent_metadata_txn_work_charge(1);
		if (candidates != 0)
			return word * 64 +
			       agent_catalog_first_bit(candidates) + 1;
	}
	return 0;
}

int
agent_metadata_catalog_reclaim_scope(
	uint scope_id, struct workflow_lifecycle_key lifecycle)
{
	int cleared = 0;
	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed() || !agent_scope_valid(scope_id) ||
	    !workflow_lifecycle_key_valid(lifecycle))
		return AGENT_CATALOG_CONFLICT;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_catalog_files[i].used ||
		    agent_catalog_scopes[i] != scope_id ||
		    !workflow_lifecycle_key_equal(
			    agent_catalog_slot_lifecycle(i), lifecycle))
			continue;
		if ((agent_catalog_files[i].flags &
		     (AGENT_FILE_META_F_PERSIST | AGENT_FILE_META_F_AUTOSCAN)) ||
		    agent_catalog_forget_slot_volatile(i) < 0)
			return -1;
		cleared++;
	}
	return cleared;
}

int
agent_metadata_catalog_fence_generation(
	uint scope_id, struct workflow_lifecycle_key lifecycle,
	uint64 *generation)
{
	struct workflow_lifecycle_key current = workflow_lifecycle_none();
	uint64 scope_generation;
	uint64 system_generation;
	uint64 value = AGENT_CATALOG_PLAN_HASH;

	agent_catalog_require_txn();
	if (generation == 0)
		return -1;
	*generation = 0;
	if (!agent_scope_valid(scope_id) ||
	    !workflow_lifecycle_key_valid(lifecycle) ||
	    agent_catalog_mutation_owner != 0 || agent_catalog_active_edit != 0 ||
	    vfs_scope_lifecycle(scope_id, &current) < 0 ||
	    !workflow_lifecycle_key_equal(current, lifecycle))
		return AGENT_CATALOG_CONFLICT;
	scope_generation = agent_file_state_scope_generation(scope_id);
	system_generation = agent_file_state_scope_generation(VFS_SCOPE_SYSTEM);
	value = agent_catalog_hash_bytes(value, &lifecycle, sizeof(lifecycle));
	value = agent_catalog_hash_bytes(value, &scope_id, sizeof(scope_id));
	value = agent_catalog_hash_bytes(
		value, &agent_catalog_generation, sizeof(agent_catalog_generation));
	value = agent_catalog_hash_bytes(
		value, &scope_generation, sizeof(scope_generation));
	value = agent_catalog_hash_bytes(
		value, &system_generation, sizeof(system_generation));
	*generation = value == 0 ? AGENT_CATALOG_PLAN_HASH : value;
	return 0;
}
