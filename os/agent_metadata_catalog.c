#include "agent_file_name_policy.h"
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
#define AGENT_CATALOG_USAGE_MAX (AGENT_CATALOG_SCOPE_PLAN_MAX + 1)
#define AGENT_CATALOG_PREPARE_STEP 32
#define AGENT_CATALOG_PLAN_HASH 1469598103934665603ULL
#define AGENT_CATALOG_PUBLISH_GENERATION (1U << 0)
#define AGENT_CATALOG_PUBLISH_JOURNAL    (1U << 1)
#define AGENT_CATALOG_JOURNAL_CONTENT_TAG (1ULL << 63)

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
_Static_assert(VFS_SCOPE_LIFECYCLE_CAP == AGENT_CATALOG_SCOPE_PLAN_MAX,
	       "catalog prepare scope accounting must cover retained workflows");
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
static struct agent_catalog_usage
	agent_catalog_usage[AGENT_CATALOG_USAGE_MAX];
static uchar agent_catalog_slot_usage[AGENT_FILE_META_MAX];
static uchar agent_catalog_states[AGENT_FILE_META_MAX];
static struct agent_file_meta agent_catalog_edit_buffer;
static struct agent_catalog_edit *agent_catalog_active_edit;
static uint64 agent_catalog_generation;
static void *agent_catalog_mutation_owner;
static uint64 agent_catalog_mutation_sequence, agent_catalog_mutation_token;
static uint agent_catalog_live, agent_catalog_system, agent_catalog_ordinary;
static uint agent_catalog_pending_count;
struct agent_catalog_dirty_scope {
	uint used, scope_id, count;
	struct workflow_lifecycle_key lifecycle;
	uint64 overflow_sequence;
	ushort slots[AGENT_CATALOG_JOURNAL_CHANGE_MAX];
	uint64 sequences[AGENT_CATALOG_JOURNAL_CHANGE_MAX];
};
static struct agent_catalog_dirty_scope
	agent_catalog_journal_dirty[AGENT_CATALOG_SCOPE_PLAN_MAX + 1];
static volatile int agent_catalog_journal_guard;
static uint64 agent_catalog_journal_sequence;
static int agent_catalog_unbind(int, struct agent_file_meta *, uint);
static void agent_catalog_normalize_physical(int, struct agent_file_meta *);
static void agent_catalog_require_txn(void);
static int agent_catalog_mutation_allowed(void);
static void agent_catalog_journal_note(
	uint, struct workflow_lifecycle_key, int);
static void agent_catalog_slot_publish(
	int, const struct agent_file_meta *, uint, uint,
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
static int agent_catalog_bit(const uchar *bits, uint slot) {
	return (bits[slot / 8] & (1U << (slot % 8))) != 0;
}
static void agent_catalog_bit_set(uchar *bits, uint slot) {
	bits[slot / 8] |= 1U << (slot % 8);
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
		    agent_catalog_scopes[slot] != scope_id)
			continue;
		matched = agent_catalog_key_matches(
			selector, &agent_catalog_files[slot]);
		if (matched == 0)
			continue;
		result->matched |= matched;
		result->states |= agent_catalog_states[slot];
		if (result->slot == -1)
			result->slot = slot;
		else if (result->slot != slot)
			result->slot = AGENT_CATALOG_CONFLICT;
	}
}
int agent_metadata_catalog_record_base_valid(
	const struct agent_file_meta *meta, uint scope_id, uint slot) {
	return meta != 0 && slot < AGENT_FILE_META_MAX && meta->used == 1 &&
	       meta->fid > 0 && agent_object_scope_valid(scope_id) &&
	       meta->physical_name[0] != 0 &&
	       meta->physical_name[sizeof(meta->physical_name) - 1] == 0 &&
	       meta->logical_path[sizeof(meta->logical_path) - 1] == 0 &&
	       meta->project[sizeof(meta->project) - 1] == 0 &&
	       meta->workflow[sizeof(meta->workflow) - 1] == 0 &&
	       meta->run_id[sizeof(meta->run_id) - 1] == 0 &&
	       meta->stage[sizeof(meta->stage) - 1] == 0 &&
	       meta->kind[sizeof(meta->kind) - 1] == 0 &&
	       meta->status[sizeof(meta->status) - 1] == 0 &&
	       meta->summary[sizeof(meta->summary) - 1] == 0 &&
	       meta->update_mask == 0 &&
	       (meta->flags & AGENT_FILE_META_F_PERSIST) != 0 &&
	       (meta->flags & ~(AGENT_FILE_META_F_PERSIST |
			 AGENT_FILE_META_F_AUTOSCAN)) == 0;
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
	hash = agent_catalog_hash_bytes(hash, &agent_catalog_states[slot],
					sizeof(agent_catalog_states[slot]));
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
	memset(agent_catalog_states, 0, sizeof(agent_catalog_states));
	memset(agent_catalog_index_bits, 0, sizeof(agent_catalog_index_bits));
	memset(agent_catalog_free_bits, 0xff, sizeof(agent_catalog_free_bits));
	if (AGENT_FILE_META_MAX % 64 != 0)
		agent_catalog_free_bits[AGENT_CATALOG_BITMAP_WORDS - 1] &=
			(1ULL << (AGENT_FILE_META_MAX % 64)) - 1;
	memset(agent_catalog_ready_bits, 0, sizeof(agent_catalog_ready_bits));
	memset(agent_catalog_usage, 0, sizeof(agent_catalog_usage));
	memset(agent_catalog_slot_usage, 0, sizeof(agent_catalog_slot_usage));
	agent_catalog_live = 0;
	agent_catalog_system = 0;
	agent_catalog_ordinary = 0;
	agent_catalog_pending_count = 0;
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
		slot, &agent_catalog_files[slot], agent_catalog_states[slot], 0);
	agent_catalog_bitmap_clear(agent_catalog_ready_bits, slot);
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
	if (agent_catalog_states[slot] & AGENT_CATALOG_STATE_PENDING) {
		if (agent_catalog_pending_count == 0)
			panic("Agent catalog pending invariant");
		agent_catalog_pending_count--;
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
	const struct agent_file_meta *meta, uint state)
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
	if (state & AGENT_CATALOG_STATE_PENDING)
		agent_catalog_pending_count++;
	if (state == 0)
		agent_catalog_bitmap_set(agent_catalog_ready_bits, slot);
	fid = meta->fid;
	if (fid > 0 && fid <= AGENT_FILE_META_MAX) {
		if (agent_catalog_bitmap_test(usage->fids, fid - 1))
			panic("Agent catalog duplicate fid invariant");
		agent_catalog_bitmap_set(usage->fids, fid - 1);
	}
	agent_catalog_index_update(slot, meta, state, 1);
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

static struct agent_catalog_dirty_scope *
agent_catalog_journal_scope(uint scope_id,
			    struct workflow_lifecycle_key lifecycle, int create)
{
	struct agent_catalog_dirty_scope *free = 0;

	for (uint i = 0; i < AGENT_CATALOG_SCOPE_PLAN_MAX + 1; i++) {
		struct agent_catalog_dirty_scope *state =
			&agent_catalog_journal_dirty[i];

		if (state->used && state->scope_id == scope_id &&
		    workflow_lifecycle_key_equal(state->lifecycle, lifecycle))
			return state;
		if (!state->used && free == 0)
			free = state;
	}
	if (!create || free == 0 || !agent_object_scope_valid(scope_id) ||
	    (scope_id == VFS_SCOPE_SYSTEM ?
	     !workflow_lifecycle_key_equal(
		     lifecycle, workflow_lifecycle_none()) :
	     !workflow_lifecycle_key_valid(lifecycle)))
		return 0;
	memset(free, 0, sizeof(*free));
	free->used = 1;
	free->scope_id = scope_id;
	free->lifecycle = lifecycle;
	return free;
}

static int
agent_catalog_journal_lock(void)
{
	int enabled = intr_save();

	while (__sync_lock_test_and_set(&agent_catalog_journal_guard, 1) != 0)
		;
	__sync_synchronize();
	return enabled;
}

static void
agent_catalog_journal_unlock(int enabled)
{
	__sync_synchronize();
	__sync_lock_release(&agent_catalog_journal_guard);
	intr_restore(enabled);
}

static uint64
agent_catalog_journal_sequence_next(void)
{
	agent_catalog_journal_sequence++;
	if (agent_catalog_journal_sequence == 0 ||
	    agent_catalog_journal_sequence >= AGENT_CATALOG_JOURNAL_CONTENT_TAG)
		agent_catalog_journal_sequence = 1;
	return agent_catalog_journal_sequence;
}

static uint64
agent_catalog_journal_content_sequence(uint64 sequence)
{
	sequence &= ~AGENT_CATALOG_JOURNAL_CONTENT_TAG;
	return AGENT_CATALOG_JOURNAL_CONTENT_TAG | (sequence ? sequence : 1);
}

static void
agent_catalog_journal_note_sequence(
	uint scope_id, struct workflow_lifecycle_key lifecycle, int slot,
	uint64 sequence)
{
	struct agent_catalog_dirty_scope *state;
	int enabled;

	if (!agent_object_scope_valid(scope_id) || slot < 0 ||
	    slot >= AGENT_FILE_META_MAX)
		panic("Agent catalog journal slot invariant");
	if (scope_id == VFS_SCOPE_SYSTEM ?
	    !workflow_lifecycle_key_equal(
		    lifecycle, workflow_lifecycle_none()) :
	    !workflow_lifecycle_key_valid(lifecycle))
		panic("Agent catalog journal lifecycle invariant");
	enabled = agent_catalog_journal_lock();
	if (sequence == 0)
		sequence = agent_catalog_journal_sequence_next();
	state = agent_catalog_journal_scope(scope_id, lifecycle, 1);
	if (state == 0)
		panic("Agent catalog journal scope invariant");
	for (uint i = 0; i < state->count; i++)
		if (state->slots[i] == slot) {
			if ((sequence & AGENT_CATALOG_JOURNAL_CONTENT_TAG) &&
			    (state->sequences[i] &
			     AGENT_CATALOG_JOURNAL_CONTENT_TAG) &&
			    sequence < state->sequences[i]) {
				agent_catalog_journal_unlock(enabled);
				return;
			}
			state->sequences[i] = sequence;
			agent_catalog_journal_unlock(enabled);
			return;
		}
	if (state->count >= AGENT_CATALOG_JOURNAL_CHANGE_MAX) {
		state->overflow_sequence = sequence;
		agent_catalog_journal_unlock(enabled);
		return;
	}
	state->slots[state->count] = slot;
	state->sequences[state->count] = sequence;
	state->count++;
	agent_catalog_journal_unlock(enabled);
}

static void agent_catalog_journal_note(
	uint scope_id, struct workflow_lifecycle_key lifecycle, int slot)
{
	agent_catalog_journal_note_sequence(scope_id, lifecycle, slot, 0);
}

int
agent_metadata_catalog_journal_note_content(
	const struct agent_file_content_receipt *receipt)
{
	if (receipt == 0 || receipt->sequence == 0 ||
	    receipt->slot >= AGENT_FILE_META_MAX ||
	    !agent_object_scope_valid(receipt->scope_id) ||
	    receipt->dev != ROOTDEV || receipt->inum == 0 ||
	    receipt->incarnation == 0 ||
	    (receipt->scope_id == VFS_SCOPE_SYSTEM ?
	     !workflow_lifecycle_key_equal(
		     receipt->lifecycle, workflow_lifecycle_none()) :
	     !workflow_lifecycle_key_valid(receipt->lifecycle)))
		return -1;
	agent_catalog_journal_note_sequence(
		receipt->scope_id, receipt->lifecycle, receipt->slot,
		agent_catalog_journal_content_sequence(receipt->sequence));
	return 0;
}

static void agent_catalog_slot_publish(
	int slot, const struct agent_file_meta *next, uint next_scope,
	uint next_state, struct workflow_lifecycle_key next_lifecycle,
	uint changes, uint flags)
{
	struct workflow_lifecycle_key old_lifecycle = workflow_lifecycle_none();
	uint old_scope = VFS_SCOPE_NONE;
	int old_persistent = 0;

	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		panic("Agent catalog publish slot invariant");
	if (agent_catalog_files[slot].used) {
		old_scope = agent_catalog_scopes[slot];
		old_lifecycle = agent_catalog_slot_lifecycle(slot);
		old_persistent = (agent_catalog_files[slot].flags &
				  AGENT_FILE_META_F_PERSIST) != 0;
	}
	agent_catalog_derived_remove(slot);
	if (next != 0 && next->used) {
		agent_catalog_files[slot] = *next;
		agent_catalog_scopes[slot] = next_scope;
		agent_catalog_states[slot] = next_state;
		agent_catalog_derived_add(
			slot, next_scope, next_lifecycle,
			&agent_catalog_files[slot], next_state);
	} else {
		memset(&agent_catalog_files[slot], 0,
		       sizeof(agent_catalog_files[slot]));
		agent_catalog_scopes[slot] = VFS_SCOPE_NONE;
		agent_catalog_states[slot] = 0;
	}
	if (flags & AGENT_CATALOG_PUBLISH_GENERATION)
		agent_catalog_changed(changes);
	if ((flags & AGENT_CATALOG_PUBLISH_JOURNAL) == 0)
		return;
	if (old_persistent)
		agent_catalog_journal_note(old_scope, old_lifecycle, slot);
	if (agent_catalog_files[slot].used &&
	    (agent_catalog_files[slot].flags &
	     AGENT_FILE_META_F_PERSIST) != 0)
		agent_catalog_journal_note(
			agent_catalog_scopes[slot], next_lifecycle, slot);
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
	agent_catalog_journal_guard = 0;
	agent_catalog_journal_sequence = 0;
	memset(agent_catalog_journal_dirty, 0,
	       sizeof(agent_catalog_journal_dirty));
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
	view->state = 0;
	if (!agent_catalog_files[slot].used)
		return 0;
	view->meta = &agent_catalog_files[slot];
	view->scope_id = agent_catalog_scopes[slot];
	view->state = agent_catalog_states[slot];
	return 1;
}

int agent_metadata_catalog_borrow(
	uint64 generation, int slot, struct agent_catalog_view *view) {
	int result;
	agent_catalog_require_txn();
	if (generation != 0 && generation != agent_catalog_generation)
		return AGENT_CATALOG_STALE;
	result = agent_metadata_catalog_borrow_scan(slot, view);
	if (result > 0 && view->state != 0) {
		view->meta = 0;
		view->state = 0;
		return 0;
	}
	return result;
}

int
agent_metadata_catalog_read_begin(
	uint scope_id, int index, const char *key, int force_scan,
	struct agent_catalog_read_snapshot *snapshot, int *bucket_out)
{
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
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
	if (vfs_scope_lifecycle(scope_id, &lifecycle) < 0) {
		intr_restore(enabled);
		return AGENT_CATALOG_STALE;
	}
	snapshot->generation = agent_catalog_generation;
	snapshot->scope_id = scope_id;
	snapshot->lifecycle = lifecycle;
	if (force_scan) {
		memset(snapshot->candidates, 0xff,
		       sizeof(snapshot->candidates));
		if (AGENT_FILE_META_MAX % 64 != 0)
			snapshot->candidates[AGENT_CATALOG_BITMAP_WORDS - 1] &=
				(1ULL << (AGENT_FILE_META_MAX % 64)) - 1;
	} else {
		const uint64 *candidates = index == 0 ?
			agent_catalog_ready_bits :
			agent_catalog_index_bits[bitmap_index][bucket];

		for (uint word = 0; word < AGENT_CATALOG_BITMAP_WORDS; word++)
			snapshot->candidates[word] =
				candidates[word] & agent_catalog_ready_bits[word];
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
	if (snapshot->generation != agent_catalog_generation) {
		intr_restore(enabled);
		return AGENT_CATALOG_STALE;
	}
	if (!agent_catalog_files[slot].used || agent_catalog_states[slot] != 0) {
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
	stable = vfs_scope_lifecycle(snapshot->scope_id, &lifecycle) == 0 &&
		 workflow_lifecycle_key_equal(lifecycle, snapshot->lifecycle) &&
		 snapshot->generation == agent_catalog_generation;
	intr_restore(enabled);
	return stable;
}

static int agent_catalog_edit_begin(
	int slot, uint scope_id, struct agent_catalog_edit *edit, int scanner) {
	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	if (edit == 0 || agent_catalog_active_edit != 0 || slot < 0 ||
	    slot >= AGENT_FILE_META_MAX)
		return -1;
	if (!scanner && agent_catalog_states[slot] != 0)
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
	return agent_catalog_edit_begin(slot, scope_id, edit, 0);
}

int agent_metadata_catalog_edit_begin_scan(int slot, uint scope_id,
	struct agent_catalog_edit *edit) {
	return agent_catalog_edit_begin(slot, scope_id, edit, 1);
}

int agent_metadata_catalog_edit_commit(struct agent_catalog_edit *edit, uint changes) {
	struct workflow_lifecycle_key lifecycle;
	int admission = 1;
	int growth = 0;
	int slot;
	uint scope_id, state;
	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed()) {
		agent_metadata_catalog_edit_abort(edit);
		return AGENT_CATALOG_CONFLICT;
	}
	if (edit == 0 || agent_catalog_active_edit != edit)
		return -1;
	if (edit->meta == &agent_catalog_edit_buffer && edit->meta->used &&
	    edit->slot >= 0 && edit->slot < AGENT_FILE_META_MAX) {
		edit->meta->physical_name[
			sizeof(edit->meta->physical_name) - 1] = 0;
		agent_catalog_normalize_physical(edit->slot, edit->meta);
	}
	if (edit->meta == &agent_catalog_edit_buffer && edit->meta->used &&
	    agent_object_scope_valid(edit->scope_id) &&
	    agent_metadata_catalog_identity_state(edit->meta) >= 0 &&
	    edit->slot >= 0 && edit->slot < AGENT_FILE_META_MAX) {
		growth = !agent_catalog_files[edit->slot].used;
		admission = agent_catalog_admission(
			edit->scope_id, edit->slot, edit->meta,
			growth ? 0 : agent_catalog_files[edit->slot].flags,
			edit->meta->flags, growth);
	}
	if (edit->meta != &agent_catalog_edit_buffer ||
	    edit->slot < 0 || edit->slot >= AGENT_FILE_META_MAX ||
	    (edit->meta->used &&
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
	state = agent_catalog_states[slot];
	if (agent_catalog_target_lifecycle(slot, scope_id, &lifecycle) < 0) {
		agent_metadata_catalog_edit_abort(edit);
		return AGENT_CATALOG_INTERRUPTED;
	}
	agent_metadata_catalog_edit_abort(edit);
	agent_catalog_slot_publish(
		slot, &agent_catalog_edit_buffer, scope_id, state, lifecycle,
		changes, AGENT_CATALOG_PUBLISH_GENERATION |
			 AGENT_CATALOG_PUBLISH_JOURNAL);
	return 0;
}

void agent_metadata_catalog_edit_abort(struct agent_catalog_edit *edit) {
	agent_catalog_require_txn();
	if (edit != 0 && agent_catalog_active_edit == edit) {
		agent_catalog_active_edit = 0;
		edit->meta = 0;
	}
}

int agent_metadata_catalog_index_seek(
	uint64 generation, int index, char *key, int slot, int *bucket_out,
	int *rebuild_records)
{
	const uint64 *bits;
	uint bucket;
	int index_slot = index - 1;
	int bitmap_index = AGENT_CATALOG_PRIMARY_INDEX_COUNT + index_slot;

	agent_catalog_require_txn();
	if (generation != agent_catalog_generation)
		return AGENT_CATALOG_STALE;
	if (index_slot < 0 ||
	    index_slot >= AGENT_CATALOG_SECONDARY_INDEX_COUNT)
		return -1;
	if (rebuild_records)
		*rebuild_records = 0;
	if (slot < 0) {
		if (key == 0 || key[0] == 0)
			return -1;
		bucket = agent_catalog_hash_bytes(
			AGENT_CATALOG_PLAN_HASH, key, strlen(key)) %
			 AGENT_CATALOG_INDEX_BUCKETS;
		if (bucket_out)
			*bucket_out = bucket;
		bits = agent_catalog_index_bits[bitmap_index][bucket];
		return agent_catalog_bitmap_next(bits, -1);
	}
	if (slot >= AGENT_FILE_META_MAX ||
	    !agent_catalog_bitmap_test(agent_catalog_ready_bits, slot))
		return -1;
	bucket = agent_catalog_index_bucket(
		bitmap_index, &agent_catalog_files[slot]);
	bits = agent_catalog_index_bits[bitmap_index][bucket];
	return agent_catalog_bitmap_next(bits, slot);
}

int agent_metadata_catalog_live_seek(uint64 generation, int slot)
{
	agent_catalog_require_txn();
	if (generation != agent_catalog_generation)
		return AGENT_CATALOG_STALE;
	if (slot >= AGENT_FILE_META_MAX)
		return -1;
	return agent_catalog_bitmap_next(agent_catalog_ready_bits, slot);
}

static void agent_catalog_normalize_physical(int slot, struct agent_file_meta *meta) {
	if (meta->physical_name[0] == 0 ||
	    strlen(meta->physical_name) > DIRSIZ ||
	    strncmp(meta->physical_name, AGENT_META_STORE_NAME_0, DIRSIZ) == 0 ||
	    strncmp(meta->physical_name, AGENT_META_STORE_NAME_1, DIRSIZ) == 0) {
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
	int result = 0;
	int lookup_status = FS_LOOKUP_ERROR;
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX || meta == 0 ||
	    !meta->used || meta->physical_name[0] == 0)
		return 0;
	if ((agent_catalog_states[slot] & AGENT_CATALOG_STATE_QUARANTINE) ||
	    agent_metadata_catalog_identity_state(meta) == 0)
		return 0;
	ip = meta->dev != 0 && meta->inum != 0 ?
		inode_get(meta->dev, meta->inum) : 0;
	if (ip == 0) {
		ip = namei_scope_status(meta->physical_name,
					VFS_POLICY_WORKFLOW, scope_id,
					&lookup_status);
		if (ip == 0)
			return lookup_status == FS_LOOKUP_ABSENT ? 0 : -1;
	}
	if (ivalid(ip) < 0) {
		iput(ip);
		return -1;
	}
	if (ip->agent_meta_slot == slot + 1 &&
	    ip->dev == meta->dev && ip->inum == meta->inum &&
	    ip->vfs_incarnation == meta->incarnation)
		result = agent_file_state_set_index(ip, 0, 0, 0);
	iput(ip);
	return result;
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
	    strlen(meta->physical_name) > DIRSIZ ||
	    strncmp(meta->physical_name, AGENT_META_STORE_NAME_0, DIRSIZ) == 0 ||
	    strncmp(meta->physical_name, AGENT_META_STORE_NAME_1, DIRSIZ) == 0)
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
	if (agent_file_state_set_index(
		ip, slot + 1,
		meta->flags & (AGENT_FILE_META_F_PERSIST |
			       AGENT_FILE_META_F_AUTOSCAN), 0) < 0)
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

int agent_metadata_catalog_bind(int slot, int create, struct proc *actor) {
	struct workflow_lifecycle_key lifecycle;
	int result;
	uint scope_id, state;

	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX ||
	    !agent_catalog_files[slot].used)
		return -1;
	scope_id = agent_catalog_scopes[slot];
	state = agent_catalog_states[slot];
	if (agent_catalog_target_lifecycle(slot, scope_id, &lifecycle) < 0)
		return AGENT_CATALOG_INTERRUPTED;
	agent_catalog_edit_buffer = agent_catalog_files[slot];
	result = agent_catalog_bind_status(
		slot, &agent_catalog_edit_buffer, scope_id, state,
		create, actor, 0);
	if (result >= 0)
		agent_catalog_slot_publish(
			slot, &agent_catalog_edit_buffer, scope_id, state,
			lifecycle, 0, AGENT_CATALOG_PUBLISH_GENERATION |
				      AGENT_CATALOG_PUBLISH_JOURNAL);
	return result;
}

int agent_metadata_catalog_clear_slot(int slot) {
	int was_used;
	uint scope_id;
	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return -1;
	was_used = agent_catalog_files[slot].used;
	scope_id = agent_catalog_scopes[slot];
	if (agent_catalog_unbind(slot, &agent_catalog_files[slot], scope_id) < 0)
		return -1;
	agent_catalog_slot_publish(
		slot, 0, VFS_SCOPE_NONE, 0, workflow_lifecycle_none(),
		AGENT_FILE_CHANGE_MEMBERSHIP,
		AGENT_CATALOG_PUBLISH_GENERATION |
		AGENT_CATALOG_PUBLISH_JOURNAL);
	if (was_used)
		agent_file_state_generation_next(scope_id);
	return 0;
}

int agent_metadata_catalog_restore(
	const struct agent_catalog_mutation_fence *fence,
	const struct agent_catalog_undo_token *undo,
	const struct agent_file_meta *previous, uint previous_scope, int had_previous) {
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
	if (had_previous != 0 && had_previous != 1)
		return -1;
	if (had_previous) {
		if (previous == 0 || !previous->used ||
		    !agent_object_scope_valid(previous_scope) ||
		    previous->physical_name[0] == 0 ||
		    agent_metadata_catalog_identity_state(previous) < 0)
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
		had_previous ? previous_scope : VFS_SCOPE_NONE, 0, lifecycle,
		AGENT_FILE_CHANGE_MEMBERSHIP,
		AGENT_CATALOG_PUBLISH_GENERATION |
		AGENT_CATALOG_PUBLISH_JOURNAL);
	return 0;
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

int agent_metadata_catalog_live_count(void) {
	agent_catalog_require_txn();
	return agent_catalog_live;
}

int agent_metadata_catalog_reconcile_pending(void) {
	return agent_catalog_pending_count != 0;
}

int agent_metadata_catalog_reconcile_slot(int slot) {
	struct workflow_lifecycle_key lifecycle;
	uint scope_id;

	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX ||
	    !agent_catalog_files[slot].used)
		return -1;
	if ((agent_catalog_states[slot] & AGENT_CATALOG_STATE_PENDING) == 0)
		return 0;
	scope_id = agent_catalog_scopes[slot];
	lifecycle = agent_catalog_slot_lifecycle(slot);
	agent_catalog_slot_publish(
		slot, &agent_catalog_files[slot], scope_id, 0, lifecycle,
		AGENT_FILE_CHANGE_MEMBERSHIP,
		AGENT_CATALOG_PUBLISH_GENERATION);
	return 1;
}

int agent_metadata_catalog_reclaim_scope(uint scope_id) {
	int cleared = 0;
	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_catalog_files[i].used ||
		    agent_catalog_scopes[i] != scope_id)
			continue;
		if (agent_metadata_catalog_clear_slot(i) < 0)
			return -1;
		cleared++;
	}
	return cleared;
}

static struct agent_catalog_plan_key agent_catalog_plan_key(
	const struct agent_meta_record *records, uint count, int reload_one_scope,
	uint reload_scope, uint64 candidate_epoch, uint64 catalog_generation,
	struct workflow_lifecycle_key lifecycle) {
	struct agent_catalog_plan_key key;
	memset(&key, 0, sizeof(key));
	key.records = records;
	key.count = count;
	key.reload_one_scope = reload_one_scope;
	key.reload_scope = reload_scope;
	key.candidate_epoch = candidate_epoch;
	key.catalog_generation = catalog_generation;
	key.lifecycle_id = lifecycle.id;
	key.lifecycle_generation = lifecycle.generation;
	return key;
}

static uint64 agent_catalog_plan_binding(
	const struct agent_catalog_plan_key *key) {
	return agent_catalog_hash_bytes(AGENT_CATALOG_PLAN_HASH, key, sizeof(*key));
}

static uint64 agent_catalog_plan_final_token(
	uint64 binding, uint64 plan_hash) {
	uint64 token = agent_catalog_hash_bytes(binding, &plan_hash, sizeof(plan_hash));
	return token == 0 ? AGENT_CATALOG_PLAN_HASH : token;
}

static int agent_catalog_record_valid(
	const struct agent_meta_record *record) {
	const struct agent_file_meta *meta = &record->meta;
	return agent_metadata_catalog_record_base_valid(
		       meta, record->scope_id, record->slot) &&
	       strlen(meta->physical_name) <= DIRSIZ &&
	       strncmp(meta->physical_name, AGENT_META_STORE_NAME_0,
		       DIRSIZ) != 0 &&
	       strncmp(meta->physical_name, AGENT_META_STORE_NAME_1,
		       DIRSIZ) != 0 &&
	       agent_metadata_catalog_identity_state(meta) >= 0;
}

static int agent_catalog_plan_count(
	struct agent_metadata_apply_result *result, uint scope_id) {
	uint scope_index;

	if (scope_id == VFS_SCOPE_SYSTEM)
		return ++result->plan_system_count <= AGENT_FILE_SYSTEM_LIMIT ? 0 : -1;
	scope_index = 0;
	while (scope_index < result->plan_scope_used &&
	       result->plan_scope_ids[scope_index] != scope_id)
		scope_index++;
	if (scope_index == result->plan_scope_used) {
		if (result->plan_scope_used >= AGENT_CATALOG_SCOPE_PLAN_MAX)
			return -1;
		result->plan_scope_ids[scope_index] = scope_id;
		result->plan_scope_used++;
	}
	if (++result->plan_ordinary_count > AGENT_FILE_ORDINARY_LIMIT ||
	    ++result->plan_scope_counts[scope_index] > AGENT_FILE_SCOPE_LIMIT)
		return -1;
	return 0;
}
static int agent_catalog_plan_slot_free(
	const struct agent_metadata_apply_result *result, uint slot) {
	return slot < AGENT_FILE_META_MAX &&
	       !agent_catalog_bit(result->blocked_slots, slot) &&
	       !agent_catalog_bit(result->selected_slots, slot);
}

static int agent_catalog_plan_ready(
	const struct agent_meta_record *record, uint slot) {
	const struct agent_file_meta *live = &agent_catalog_files[slot];
	uint keys = AGENT_CATALOG_KEY_FID | AGENT_CATALOG_KEY_PHYSICAL |
		    AGENT_CATALOG_KEY_IDENTITY;
	return live->used && agent_catalog_states[slot] == 0 &&
	       agent_catalog_scopes[slot] == record->scope_id &&
	       workflow_lifecycle_key_equal(
		       agent_catalog_slot_lifecycle(slot), record->lifecycle) &&
	       agent_metadata_catalog_identity_state(&record->meta) > 0 &&
	       (agent_catalog_key_matches(&record->meta, live) & keys) == keys;
}

void agent_metadata_catalog_prepare_abort(struct agent_metadata_apply_result *result) {
	agent_catalog_require_txn();
	memset(result, 0, sizeof(*result));
}

static int agent_catalog_prepare_fail(
	struct agent_metadata_apply_result *result, int status) {
	agent_metadata_catalog_prepare_abort(result);
	return status;
}

int agent_metadata_catalog_prepare_snapshot(
	struct agent_meta_record *records, uint count, int reload_one_scope,
	uint reload_scope, uint64 candidate_epoch,
	struct agent_metadata_apply_result *result) {
	uint limit;
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	struct agent_catalog_plan_key key;
	uint64 binding;

	agent_catalog_require_txn();
	if (records == 0 || result == 0 || count > AGENT_FILE_META_MAX ||
	    candidate_epoch == 0 ||
	    (reload_one_scope && !agent_scope_valid(reload_scope)))
		return AGENT_METADATA_LOAD_CORRUPT;
	if (reload_one_scope &&
	    !agent_catalog_scope_admissible(reload_scope, &lifecycle))
		return AGENT_METADATA_LOAD_INTERRUPTED;
	key = agent_catalog_plan_key(records, count, reload_one_scope,
				     reload_scope, candidate_epoch,
				     agent_catalog_generation, lifecycle);
	if (!result->plan_active) {
		memset(result, 0, sizeof(*result));
		result->plan_active = 1;
		result->plan_key = key;
		result->plan_catalog_cursor = reload_one_scope ? 0 :
							 AGENT_FILE_META_MAX;
		result->plan_hash = AGENT_CATALOG_PLAN_HASH;
		result->plan_token = agent_catalog_plan_binding(&key);
	}
	key.catalog_generation = result->plan_catalog_generation;
	if (reload_one_scope &&
	    (result->plan_lifecycle_id != lifecycle.id ||
	     result->plan_lifecycle_generation != lifecycle.generation))
		return agent_catalog_prepare_fail(result, AGENT_METADATA_LOAD_INTERRUPTED);
	if (memcmp(&result->plan_key, &key, sizeof(key)) != 0)
		return agent_catalog_prepare_fail(result, AGENT_METADATA_LOAD_CORRUPT);
	if (result->plan_catalog_generation != agent_catalog_generation)
		return agent_catalog_prepare_fail(result, AGENT_METADATA_LOAD_INTERRUPTED);
	if (result->prepared)
		return 0;
	binding = agent_catalog_plan_binding(&key);
	if (result->plan_token != binding)
		return agent_catalog_prepare_fail(result, AGENT_METADATA_LOAD_CORRUPT);
	limit = result->plan_catalog_cursor +
		(reload_one_scope ? AGENT_FILE_META_MAX :
				    AGENT_CATALOG_PREPARE_STEP);
	if (limit > AGENT_FILE_META_MAX)
		limit = AGENT_FILE_META_MAX;
	while (result->plan_catalog_cursor < limit) {
		uint slot = result->plan_catalog_cursor++;

		if (!agent_catalog_files[slot].used ||
		    agent_catalog_scopes[slot] == reload_scope)
			continue;
		agent_catalog_bit_set(result->blocked_slots, slot);
		if (agent_catalog_scopes[slot] != VFS_SCOPE_SYSTEM &&
		    ++result->plan_ordinary_count > AGENT_FILE_ORDINARY_LIMIT)
			return agent_catalog_prepare_fail(result, AGENT_METADATA_LOAD_CORRUPT);
	}
	if (result->plan_catalog_cursor < AGENT_FILE_META_MAX)
		return AGENT_METADATA_LOAD_PROGRESS;
	limit = result->plan_cursor +
		(reload_one_scope ? count : AGENT_CATALOG_PREPARE_STEP);
	if (limit > count)
		limit = count;
	while (result->plan_cursor < limit) {
		uint index = result->plan_cursor++;
		struct agent_meta_record *record = &records[index];
		struct workflow_lifecycle_key lifecycle =
			workflow_lifecycle_none();
		uint original_slot = record->slot;
		uint slot;
		int identity;

		if (!agent_catalog_record_valid(record) ||
		    (record->scope_id == VFS_SCOPE_SYSTEM ?
		     !workflow_lifecycle_key_equal(
			     record->lifecycle, workflow_lifecycle_none()) :
		     !workflow_lifecycle_key_valid(record->lifecycle)))
			return agent_catalog_prepare_fail(result, AGENT_METADATA_LOAD_CORRUPT);
		if (reload_one_scope && record->scope_id != reload_scope)
			goto hash_record;
		if (record->scope_id != VFS_SCOPE_SYSTEM &&
		    (vfs_scope_lifecycle(record->scope_id, &lifecycle) < 0 ||
		     !workflow_lifecycle_key_equal(lifecycle,
					   record->lifecycle))) {
			agent_catalog_bit_set(result->missing_slots,
					      original_slot);
			goto hash_record;
		}
		if (agent_catalog_plan_count(result, record->scope_id) < 0)
			return agent_catalog_prepare_fail(result, AGENT_METADATA_LOAD_CORRUPT);
		slot = original_slot;
		if (!agent_catalog_plan_slot_free(result, slot)) {
			if (!reload_one_scope)
				return agent_catalog_prepare_fail(result, AGENT_METADATA_LOAD_CORRUPT);
			while (result->plan_next_slot < AGENT_FILE_META_MAX &&
			       !agent_catalog_plan_slot_free(
				       result, result->plan_next_slot))
				result->plan_next_slot++;
			if (result->plan_next_slot >= AGENT_FILE_META_MAX)
				return agent_catalog_prepare_fail(result, AGENT_METADATA_LOAD_CORRUPT);
			slot = result->plan_next_slot++;
			result->layout_changed = 1;
		}
		record->slot = slot;
		agent_catalog_normalize_physical(slot, &record->meta);
		agent_catalog_bit_set(result->selected_slots, slot);
		agent_catalog_bit_set(result->included_records, index);
		identity = agent_metadata_catalog_identity_state(&record->meta);
		if (identity == 0 && record->scope_id != VFS_SCOPE_SYSTEM &&
		    (record->meta.flags & AGENT_FILE_META_F_AUTOSCAN) == 0) {
			agent_catalog_bit_set(result->quarantine_slots, slot);
		} else if (!reload_one_scope ||
			   !agent_catalog_plan_ready(record, slot)) {
			if (identity == 0)
				agent_catalog_bit_set(result->missing_slots,
						      original_slot);
			agent_catalog_bit_set(result->pending_slots, slot);
		}
hash_record:
		result->plan_hash = agent_catalog_hash_bytes(
			result->plan_hash, record, sizeof(*record));
	}
	if (result->plan_cursor < count)
		return AGENT_METADATA_LOAD_PROGRESS;
	result->plan_token = agent_catalog_plan_final_token(
		binding, result->plan_hash);
	result->prepared = 1;
	return 0;
}

int agent_metadata_catalog_apply_snapshot(
	const struct agent_meta_record *records, uint count, int reload_one_scope,
	uint reload_scope, uint64 candidate_epoch,
	struct agent_metadata_apply_result *result) {
	uchar verified_slots[AGENT_META_STALE_BYTES];
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	struct agent_catalog_plan_key key;
	uint64 binding, hash = AGENT_CATALOG_PLAN_HASH;
	uint live_pending = 0;

	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed())
		return AGENT_METADATA_LOAD_INTERRUPTED;
	if (records == 0 || result == 0 || !result->plan_active ||
	    !result->prepared)
		panic("Agent catalog apply input invariant");
	if (reload_one_scope &&
	    (!agent_catalog_scope_admissible(reload_scope, &lifecycle) ||
	     result->plan_lifecycle_id != lifecycle.id ||
	     result->plan_lifecycle_generation != lifecycle.generation))
		return agent_catalog_prepare_fail(result, AGENT_METADATA_LOAD_INTERRUPTED);
	memset(verified_slots, 0, sizeof(verified_slots));
	key = agent_catalog_plan_key(records, count, reload_one_scope,
				     reload_scope, candidate_epoch,
				     agent_catalog_generation, lifecycle);
	if (memcmp(&result->plan_key, &key, sizeof(key)) != 0)
		panic("Agent catalog apply binding invariant");
	binding = agent_catalog_plan_binding(&key);
	for (uint i = 0; i < count; i++) {
		hash = agent_catalog_hash_bytes(hash, &records[i],
						sizeof(records[i]));
		if (agent_catalog_bit(result->included_records, i)) {
			uint slot = records[i].slot;
			int pending, quarantine;

			if (slot >= AGENT_FILE_META_MAX ||
			    agent_catalog_bit(verified_slots, slot) ||
			    !agent_catalog_bit(result->selected_slots, slot) ||
			    (reload_one_scope && agent_catalog_files[slot].used &&
			     agent_catalog_scopes[slot] != reload_scope))
				panic("Agent catalog apply slot invariant");
			pending = agent_catalog_bit(result->pending_slots, slot);
			quarantine = agent_catalog_bit(
				result->quarantine_slots, slot);
			if (pending && quarantine)
				panic("Agent catalog apply state invariant");
			agent_catalog_bit_set(verified_slots, slot);
		}
	}
	if (hash != result->plan_hash || result->plan_token !=
	    agent_catalog_plan_final_token(binding, hash))
		panic("Agent catalog apply plan invariant");
	for (uint i = 0; i < AGENT_META_STALE_BYTES; i++)
		if (verified_slots[i] != result->selected_slots[i] ||
		    ((result->pending_slots[i] |
		      result->quarantine_slots[i]) & ~verified_slots[i]) != 0)
			panic("Agent catalog apply bitmap invariant");
	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		if (agent_catalog_states[i] & AGENT_CATALOG_STATE_PENDING)
			live_pending++;
	if (live_pending != agent_catalog_pending_count ||
	    agent_catalog_active_edit != 0)
		panic("Agent catalog apply live invariant");
	result->prepared = result->plan_active = 0;
	memset(&result->delta, 0, sizeof(result->delta));
	result->delta.full_reset = !reload_one_scope;
	result->delta.scope_id = reload_one_scope ? reload_scope : VFS_SCOPE_NONE;
	agent_metadata_txn_projection_begin();
	if (reload_one_scope) {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			if (agent_catalog_files[i].used &&
			    agent_catalog_scopes[i] == reload_scope)
				agent_catalog_slot_publish(
					i, 0, VFS_SCOPE_NONE, 0,
					workflow_lifecycle_none(), 0, 0);
	} else {
		agent_catalog_storage_reset();
	}
	for (uint i = 0; i < count; i++) {
		uint slot;
		uint state = 0;

		if (!agent_catalog_bit(result->included_records, i))
			continue;
		slot = records[i].slot;
		if (agent_catalog_bit(result->pending_slots, slot))
			state |= AGENT_CATALOG_STATE_PENDING;
		if (agent_catalog_bit(result->quarantine_slots, slot))
			state |= AGENT_CATALOG_STATE_QUARANTINE;
		agent_catalog_slot_publish(
			slot, &records[i].meta, records[i].scope_id, state,
			records[i].lifecycle, 0, 0);
		agent_catalog_bit_set(result->delta.applied_slots, slot);
	}
	agent_catalog_changed(AGENT_FILE_CHANGE_MEMBERSHIP);
	result->used = agent_metadata_catalog_live_count();
	return result->used;
}

int agent_metadata_catalog_export_scope(uint scope_id, struct agent_meta_record *records,
		int capacity, uint64 *size_sequence) {
	int count = 0;
	int enabled;
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();

	agent_catalog_require_txn();
	if (!agent_object_scope_valid(scope_id) ||
	    records == 0 || capacity < 0 || size_sequence == 0)
		return -1;
	if (scope_id != VFS_SCOPE_SYSTEM &&
	    vfs_scope_lifecycle(scope_id, &lifecycle) < 0)
		return -1;
	enabled = agent_file_state_snapshot_begin(size_sequence);
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		struct agent_meta_record *record;

		if (!agent_catalog_files[i].used ||
		    agent_catalog_scopes[i] != scope_id ||
		    !workflow_lifecycle_key_equal(
			    agent_catalog_slot_lifecycle(i), lifecycle) ||
		    (agent_catalog_files[i].flags &
		     AGENT_FILE_META_F_PERSIST) == 0)
			continue;
		if (count >= capacity) {
			agent_file_state_snapshot_end(enabled);
			return -1;
		}
		record = &records[count++];
		memset(record, 0, sizeof(*record));
		record->meta = agent_catalog_files[i];
		agent_file_state_snapshot_overlay_receipt(
			&record->meta, scope_id, i, lifecycle, 0);
		record->meta.update_mask = 0;
		record->scope_id = scope_id;
		record->slot = i;
		record->lifecycle = lifecycle;
	}
	agent_file_state_snapshot_end(enabled);
	return count;
}

int
agent_metadata_catalog_journal_capture(
	uint scope_id, struct workflow_lifecycle_key lifecycle,
	struct agent_catalog_journal_receipt *receipt,
	uint64 *size_sequence)
{
	struct agent_catalog_dirty_scope *state;
	int enabled, journal_enabled;

	agent_catalog_require_txn();
	if (!agent_object_scope_valid(scope_id) || receipt == 0 ||
	    (scope_id == VFS_SCOPE_SYSTEM ?
	     !workflow_lifecycle_key_equal(
		     lifecycle, workflow_lifecycle_none()) :
	     !workflow_lifecycle_key_valid(lifecycle)) ||
	    size_sequence == 0)
		return -1;
	memset(receipt, 0, sizeof(*receipt));
	receipt->scope_id = scope_id;
	receipt->lifecycle = lifecycle;
	receipt->catalog_generation = agent_catalog_generation;
	journal_enabled = agent_catalog_journal_lock();
	state = agent_catalog_journal_scope(scope_id, lifecycle, 0);
	if (state != 0 &&
	    (state->overflow_sequence != 0 ||
	     state->count > AGENT_CATALOG_JOURNAL_RECEIPT_MAX)) {
		agent_catalog_journal_unlock(journal_enabled);
		return AGENT_CATALOG_NO_SPACE;
	}
	enabled = agent_file_state_snapshot_begin(size_sequence);
	for (uint i = 0; state != 0 && i < state->count; i++) {
		struct agent_catalog_journal_change *change =
			&receipt->changes[receipt->count++];
		uint slot = state->slots[i];

		change->sequence = state->sequences[i];
		change->slot = slot;
		if (!agent_catalog_files[slot].used ||
		    agent_catalog_scopes[slot] != scope_id ||
		    !workflow_lifecycle_key_equal(
			    agent_catalog_slot_lifecycle(slot), lifecycle) ||
		    (agent_catalog_files[slot].flags &
		     AGENT_FILE_META_F_PERSIST) == 0)
			continue;
		change->present = 1;
		change->record.meta = agent_catalog_files[slot];
		agent_file_state_snapshot_overlay_receipt(
			&change->record.meta, scope_id, slot, lifecycle,
			&change->content);
		change->record.meta.update_mask = 0;
		change->record.scope_id = scope_id;
		change->record.slot = slot;
		change->record.lifecycle = lifecycle;
	}
	agent_file_state_snapshot_end(enabled);
	agent_catalog_journal_unlock(journal_enabled);
	return 0;
}

void
agent_metadata_catalog_journal_commit(
	const struct agent_catalog_journal_receipt *receipt)
{
	struct agent_catalog_dirty_scope *state;
	int enabled;

	agent_catalog_require_txn();
	if (receipt == 0 ||
	    !agent_object_scope_valid(receipt->scope_id) ||
	    receipt->count > AGENT_CATALOG_JOURNAL_RECEIPT_MAX)
		panic("catalog journal receipt invariant");
	enabled = agent_catalog_journal_lock();
	state = agent_catalog_journal_scope(
		receipt->scope_id, receipt->lifecycle, 0);
	if (state != 0) {
		for (uint captured = 0; captured < receipt->count; captured++) {
			const struct agent_catalog_journal_change *change =
				&receipt->changes[captured];
			uint64 content_sequence = change->content.sequence ?
				agent_catalog_journal_content_sequence(
					change->content.sequence) : 0;

			for (uint i = 0; i < state->count; i++) {
				if (state->slots[i] != change->slot ||
				    (state->sequences[i] != change->sequence &&
				     state->sequences[i] != content_sequence))
					continue;
				state->count--;
				if (i != state->count) {
					state->slots[i] = state->slots[state->count];
					state->sequences[i] =
						state->sequences[state->count];
				}
				break;
			}
		}
		if (state->count == 0 && state->overflow_sequence == 0)
			memset(state, 0, sizeof(*state));
	}
	agent_catalog_journal_unlock(enabled);
	for (uint captured = 0; captured < receipt->count; captured++)
		agent_file_state_content_settle(
			&receipt->changes[captured].content);
}

int
agent_metadata_catalog_journal_settle_capture(
	uint scope_id, struct workflow_lifecycle_key lifecycle,
	struct agent_catalog_journal_settle *settle)
{
	struct agent_catalog_dirty_scope *state;
	int enabled;

	agent_catalog_require_txn();
	if (settle == 0 || !agent_object_scope_valid(scope_id) ||
	    (scope_id == VFS_SCOPE_SYSTEM ?
	     !workflow_lifecycle_key_equal(
		     lifecycle, workflow_lifecycle_none()) :
	     !workflow_lifecycle_key_valid(lifecycle)))
		return -1;
	memset(settle, 0, sizeof(*settle));
	settle->scope_id = scope_id;
	settle->lifecycle = lifecycle;
	enabled = agent_catalog_journal_lock();
	state = agent_catalog_journal_scope(scope_id, lifecycle, 0);
	if (state == 0) {
		agent_catalog_journal_unlock(enabled);
		return 0;
	}
	settle->count = state->count;
	settle->overflow_sequence = state->overflow_sequence;
	for (uint i = 0; i < state->count; i++) {
		settle->entries[i].slot = state->slots[i];
		settle->entries[i].sequence = state->sequences[i];
	}
	agent_catalog_journal_unlock(enabled);
	return 0;
}

void
agent_metadata_catalog_journal_settle_commit(
	const struct agent_catalog_journal_settle *settle)
{
	struct agent_catalog_dirty_scope *state;
	int enabled;

	agent_catalog_require_txn();
	if (settle == 0 || settle->count > AGENT_CATALOG_JOURNAL_CHANGE_MAX)
		panic("catalog settle invariant");
	enabled = agent_catalog_journal_lock();
	state = agent_catalog_journal_scope(
		settle->scope_id, settle->lifecycle, 0);
	if (state == 0) {
		agent_catalog_journal_unlock(enabled);
		return;
	}
	for (uint captured = 0; captured < settle->count; captured++)
		for (uint i = 0; i < state->count; i++) {
			if (state->slots[i] != settle->entries[captured].slot ||
			    state->sequences[i] !=
				    settle->entries[captured].sequence)
				continue;
			state->count--;
			if (i != state->count) {
				state->slots[i] = state->slots[state->count];
				state->sequences[i] =
					state->sequences[state->count];
			}
			break;
		}
	if (settle->overflow_sequence != 0 &&
	    state->overflow_sequence == settle->overflow_sequence)
		state->overflow_sequence = 0;
	if (state->count == 0 && state->overflow_sequence == 0)
		memset(state, 0, sizeof(*state));
	agent_catalog_journal_unlock(enabled);
}
