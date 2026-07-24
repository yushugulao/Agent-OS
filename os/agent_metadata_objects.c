#include "agent.h"
#include "agent_context.h"
#include "agent_internal.h"
#include "agent_metadata_internal.h"
#include "bio.h"
#include "defs.h"
#include "kernel_work.h"
#include "exec_policy.h"
#include "file.h"
#include "fs.h"
#include "loader.h"
#include "timer.h"
#include "trap.h"
#include "vfs_security.h"

/*
 * Authoritative Agent object catalog.  The live object tables, version and
 * edit state, indexes, dependency graph, and scan projections share this
 * owner. Durable COW banks and writeback scheduling live in metadata_store.
 */
#define AGENT_FILE_INDEX_BUCKETS 16
#define AGENT_ACTION_HISTORY_MAX 32
#define AGENT_FILE_QUERY_CACHE_MAX 8
#define AGENT_FILE_DIGEST_CACHE_MAX 8
#define AGENT_FILE_EDIT_MAX 32
#define AGENT_FILE_VERSION_MAX NINODE
#define AGENT_DEPENDENCY_MAX 64
#define AGENT_FILE_SYSTEM_LIMIT 64
#define AGENT_FILE_SCOPE_LIMIT 112
#define AGENT_DEPENDENCY_SCOPE_LIMIT 16
#define AGENT_ACTION_SCOPE_LIMIT 8
#define AGENT_EDIT_SCOPE_LIMIT 8
#define AGENT_INODE_META_VERSION 2
#define AGENT_FS_SCAN_INTERVAL 20
#define AGENT_FS_SCAN_STEP 16
#define AGENT_FS_SCAN_REST_MULTIPLIER 4
#define AGENT_FILE_CACHE_SCOPE_MAX NPROC
#define AGENT_FILE_CHANGE_STATUS       (1U << 0)
#define AGENT_FILE_CHANGE_STAGE        (1U << 1)
#define AGENT_FILE_CHANGE_KIND         (1U << 2)
#define AGENT_FILE_CHANGE_SCOPE_KEYS   (1U << 3)
#define AGENT_FILE_CHANGE_DEPENDENCY   (1U << 4)
#define AGENT_FILE_CHANGE_MEMBERSHIP   (1U << 5)
#define AGENT_FILE_CHANGE_INDEX_ALL					\
	(AGENT_FILE_CHANGE_STATUS | AGENT_FILE_CHANGE_STAGE |		\
	 AGENT_FILE_CHANGE_KIND | AGENT_FILE_CHANGE_MEMBERSHIP)
#define AGENT_FILE_CHANGE_ALL						\
	(AGENT_FILE_CHANGE_INDEX_ALL | AGENT_FILE_CHANGE_SCOPE_KEYS |	\
	 AGENT_FILE_CHANGE_DEPENDENCY)

_Static_assert(AGENT_FILE_VERSION_MAX == NINODE,
	       "inode version sidecar must cover every filesystem inode");
_Static_assert(AGENT_FILE_SYSTEM_LIMIT +
	       VFS_SCOPE_MAX_ACTIVE * AGENT_FILE_SCOPE_LIMIT <=
	       AGENT_FILE_META_MAX,
	       "metadata table must reserve every admitted workflow partition");
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_DEPENDENCY_SCOPE_LIMIT <=
	       AGENT_DEPENDENCY_MAX,
	       "dependency table must reserve every workflow partition");
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_ACTION_SCOPE_LIMIT <=
	       AGENT_ACTION_HISTORY_MAX,
	       "action table must reserve every workflow partition");
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_EDIT_SCOPE_LIMIT <=
	       AGENT_FILE_EDIT_MAX,
	       "edit table must reserve every workflow partition");
_Static_assert(AGENT_FILE_CACHE_SCOPE_MAX > VFS_SCOPE_MAX_ACTIVE,
	       "cache table must include the system metadata owner");

struct agent_file_query_cache_entry {
	int valid;
	uint scope_id;
	uint64 fs_generation;
	struct agent_file_query key;
	struct agent_file_query_result result;
	int hit_slots[AGENT_FILE_QUERY_MAX_HITS];
};

static struct agent_file_query_cache_entry
	agent_file_query_cache[AGENT_FILE_QUERY_CACHE_MAX];
static int agent_file_query_cache_head;

struct agent_file_digest_cache_entry {
	int valid;
	uint scope_id;
	uint64 dev;
	uint64 inum;
	uint64 incarnation;
	uint64 size;
	uint64 content_generation;
	uint64 bytes;
	uint64 hash;
	char preview[AGENT_FAST_RESULT_SIZE];
};

static struct agent_file_digest_cache_entry
	agent_file_digest_cache[AGENT_FILE_DIGEST_CACHE_MAX];
static int agent_file_digest_cache_head;
static uint64 agent_file_digest_cache_hits;
static uint64 agent_file_digest_cache_misses;

struct agent_file_cache_scope_state {
	int used;
	uint scope_id;
	uint64 cache_generation;
};

static struct agent_file_cache_scope_state
	agent_file_cache_scopes[AGENT_FILE_CACHE_SCOPE_MAX];

static struct agent_file_meta agent_files[AGENT_FILE_META_MAX];
static uint agent_file_scopes[AGENT_FILE_META_MAX];
static int agent_file_status_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_stage_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_kind_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_status_next[AGENT_FILE_META_MAX];
static int agent_file_stage_next[AGENT_FILE_META_MAX];
static int agent_file_kind_next[AGENT_FILE_META_MAX];
static int agent_metadata_apply_slots[AGENT_FILE_META_MAX];
struct agent_metadata_binding {
	uint dev;
	uint inum;
	uint incarnation;
};
static struct agent_metadata_binding
	agent_metadata_apply_bindings[AGENT_FILE_META_MAX];
struct agent_action_history_entry {
	int tool_id;
	uint scope_id;
	uint64 sequence;
	uint64 request_id;
	char project[AGENT_FILE_PROJECT_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
	char stage[AGENT_FILE_FIELD_SIZE];
};

struct agent_file_version_entry {
	int used;
	int published_size_valid;
	int published_size_dirty;
	uint scope_id;
	uint64 dev;
	uint64 inum;
	uint64 incarnation;
	uint storage_owner;
	uint vfs_policy;
	uint64 edit_version;
	uint64 content_version;
	uint64 published_size;
	uint64 published_size_sequence;
	uint64 published_size_generation;
	uint64 published_size_tick;
};

struct agent_file_edit_entry {
	int active;
	int dirty;
	uint scope_id;
	int owner_pid;
	int owner_agent_id;
	int owner_role;
	uint64 owner_control_id;
	uint64 lease_id;
	uint64 dev;
	uint64 inum;
	uint64 incarnation;
	uint64 base_version;
	uint64 deadline_tick;
	uint64 conflict_count;
	char path[AGENT_FILE_LOGICAL_SIZE];
};

struct agent_dependency_entry {
	int used;
	uint scope_id;
	uint64 flags;
	char namespace[AGENT_FILE_PROJECT_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
	char source[AGENT_FILE_FIELD_SIZE];
	char target[AGENT_FILE_FIELD_SIZE];
	char relation[AGENT_FILE_FIELD_SIZE];
	char summary[AGENT_FILE_SUMMARY_SIZE];
};

static struct agent_action_history_entry
	agent_action_history[AGENT_ACTION_HISTORY_MAX];
static struct agent_file_version_entry
	agent_file_versions[AGENT_FILE_VERSION_MAX];
static struct agent_file_edit_entry agent_file_edits[AGENT_FILE_EDIT_MAX];
/* Explicit user edges only; file dependency masks are resolved on demand. */
static struct agent_dependency_entry agent_dependencies[AGENT_DEPENDENCY_MAX];
static int agent_action_history_count;
static uint64 agent_action_next_sequence;
static uint64 agent_file_generation;
static uint64 agent_file_content_generation;
static uint64 agent_file_size_sequence;
static int agent_file_scan_enabled;
static int agent_file_scan_pending;
static int agent_file_scan_active;
static uint64 agent_file_scan_offset;
static int agent_file_scan_seen[AGENT_FILE_META_MAX];
static uint64 agent_file_scan_next_tick;
static uint64 agent_file_scan_last_step_tick;
static uint64 agent_file_scan_started_tick;
static uint64 agent_file_scan_runs;
static uint64 agent_file_scan_entries;
static uint64 agent_file_scan_added;
static uint64 agent_file_scan_updated;
static uint64 agent_file_scan_removed;
static uint64 agent_dependency_generation;
static volatile int agent_file_edit_guard;
static uint64 agent_file_edit_next_lease;

static void agent_file_reset_indexes(void);
static void agent_file_maintain(uint changes);
static int agent_file_bind_slot(int slot, int create, struct proc *actor);
static int agent_file_bind_slot_status(int slot, int create,
				       struct proc *actor, int *lookup_status);
static void agent_file_clear_slot(int slot);
static int agent_query_from_payload(struct agent_file_query *q, char *payload);
static void agent_text_append(char *dst, int n, char *src);
static void agent_file_enable_scan(void);
static void agent_action_history_clear_scope(uint scope_id);
static int agent_dependency_for_label(uint scope_id, char *label,
				      char *namespace, char *run_id,
				      uint64 *mask);
static uint64 agent_label_bit(char *label);

static uint64
agent_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static int
agent_scope_valid(uint scope_id)
{
	return scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
	       scope_id < FS_OWNER_SCOPE_FLAG;
}

static int
agent_object_scope_valid(uint scope_id)
{
	return scope_id == VFS_SCOPE_SYSTEM || agent_scope_valid(scope_id);
}

static int
agent_object_scope_visible(uint requester_scope, uint object_scope)
{
	return agent_scope_valid(requester_scope) &&
	       (object_scope == requester_scope ||
		object_scope == VFS_SCOPE_SYSTEM);
}

static void
agent_result_text(struct agent_result *res, char *text)
{
	safestrcpy(res->result, text, sizeof(res->result));
}
void
agent_metadata_objects_init(void)
{
	memset(agent_file_query_cache, 0, sizeof(agent_file_query_cache));
	agent_file_query_cache_head = 0;
	memset(agent_file_digest_cache, 0, sizeof(agent_file_digest_cache));
	agent_file_digest_cache_head = 0;
	agent_file_digest_cache_hits = 0;
	agent_file_digest_cache_misses = 0;
	agent_action_history_count = 0;
	agent_action_next_sequence = 1;
	memset(agent_action_history, 0, sizeof(agent_action_history));
	memset(agent_file_versions, 0, sizeof(agent_file_versions));
	memset(agent_file_edits, 0, sizeof(agent_file_edits));
	agent_file_generation = 0;
	agent_file_content_generation = 0;
	agent_file_size_sequence = 0;
	agent_file_scan_enabled = 0;
	agent_file_scan_pending = 0;
	agent_file_scan_active = 0;
	agent_file_scan_offset = 0;
	memset(agent_file_scan_seen, 0, sizeof(agent_file_scan_seen));
	agent_file_scan_next_tick = 0;
	agent_file_scan_last_step_tick = 0;
	agent_file_scan_started_tick = 0;
	agent_file_scan_runs = 0;
	agent_file_scan_entries = 0;
	agent_file_scan_added = 0;
	agent_file_scan_updated = 0;
	agent_file_scan_removed = 0;
	agent_dependency_generation = 0;
	agent_file_edit_guard = 0;
	agent_file_edit_next_lease = 1;
	memset(agent_file_cache_scopes, 0, sizeof(agent_file_cache_scopes));
	memset(agent_files, 0, sizeof(agent_files));
	memset(agent_file_scopes, 0, sizeof(agent_file_scopes));
	memset(agent_dependencies, 0, sizeof(agent_dependencies));
	agent_file_reset_indexes();
	agent_metadata_store_init();
}

void
agent_metadata_storage_init(void)
{
	agent_metadata_store_storage_init();
}

static struct agent_file_cache_scope_state *
agent_file_cache_scope_locked(uint scope_id, int create)
{
	struct agent_file_cache_scope_state *free_state = 0;

	if (scope_id != VFS_SCOPE_SYSTEM &&
	    (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	     scope_id >= FS_OWNER_SCOPE_FLAG))
		scope_id = VFS_SCOPE_SYSTEM;
	for (int i = 0; i < AGENT_FILE_CACHE_SCOPE_MAX; i++) {
		struct agent_file_cache_scope_state *state =
			&agent_file_cache_scopes[i];

		if (state->used && state->scope_id == scope_id)
			return state;
		if (!state->used && free_state == 0)
			free_state = state;
	}
	if (!create || free_state == 0)
		return 0;
	memset(free_state, 0, sizeof(*free_state));
	free_state->used = 1;
	free_state->scope_id = scope_id;
	return free_state;
}

static uint64 agent_file_scope_generation(uint scope_id)
{
	struct agent_file_cache_scope_state *state;
	uint64 generation;
	int enabled = intr_save();

	state = agent_file_cache_scope_locked(scope_id, 1);
	generation = state ? state->cache_generation : agent_file_generation;
	intr_restore(enabled);
	return generation;
}

static uint64 agent_file_generation_next(uint scope_id)
{
	struct agent_file_cache_scope_state *state;
	uint64 generation;
	int enabled = intr_save();

	agent_file_generation++;
	if (agent_file_generation == 0)
		agent_file_generation = 1;
	generation = agent_file_generation;
	state = agent_file_cache_scope_locked(scope_id, 1);
	if (state && state->scope_id == VFS_SCOPE_SYSTEM) {
		/* SYSTEM objects are visible in every workflow query. */
		for (int i = 0; i < AGENT_FILE_CACHE_SCOPE_MAX; i++) {
			if (!agent_file_cache_scopes[i].used)
				continue;
			agent_file_cache_scopes[i].cache_generation++;
			if (agent_file_cache_scopes[i].cache_generation == 0)
				agent_file_cache_scopes[i].cache_generation = 1;
		}
	} else if (state) {
		state->cache_generation++;
		if (state->cache_generation == 0)
			state->cache_generation = 1;
	}
	intr_restore(enabled);
	return generation;
}

static int agent_file_slot_of(struct agent_file_meta *meta)
{
	if (meta == 0 || meta < agent_files ||
	    meta >= agent_files + AGENT_FILE_META_MAX)
		return -1;
	return meta - agent_files;
}

static uint agent_file_scope(struct agent_file_meta *meta)
{
	int slot = agent_file_slot_of(meta);

	return slot < 0 ? VFS_SCOPE_NONE : agent_file_scopes[slot];
}

static int agent_text_empty(char *s)
{
	return s == 0 || s[0] == 0;
}

static int agent_contains(char *haystack, char *needle)
{
	int hlen;
	int nlen;
	int i;

	if (needle == 0 || needle[0] == 0)
		return 1;
	if (haystack == 0)
		return 0;
	hlen = strlen(haystack);
	nlen = strlen(needle);
	if (nlen > hlen)
		return 0;
	for (i = 0; i <= hlen - nlen; i++)
		if (strncmp(haystack + i, needle, nlen) == 0)
			return 1;
	return 0;
}

static uint64 agent_bucket(char *s)
{
	uint64 h = 1469598103934665603ULL;

	while (*s) {
		h ^= (uchar)*s++;
		h *= 1099511628211ULL;
	}
	return h % AGENT_FILE_INDEX_BUCKETS;
}

static void agent_slot_name(int slot, char *out, int n)
{
	if (n < 6)
		return;
	memset(out, 0, n);
	out[0] = 'a';
	out[1] = 'f';
	out[2] = '0' + (slot / 100) % 10;
	out[3] = '0' + (slot / 10) % 10;
	out[4] = '0' + slot % 10;
}

static int agent_edit_lock(void)
{
	int enabled = intr_save();

	while (__sync_lock_test_and_set(&agent_file_edit_guard, 1) != 0)
		;
	__sync_synchronize();
	return enabled;
}

static void agent_edit_unlock(int enabled)
{
	__sync_synchronize();
	__sync_lock_release(&agent_file_edit_guard);
	intr_restore(enabled);
}

static int agent_file_version_index(uint64 dev, uint64 inum)
{
	if (dev != ROOTDEV || inum == 0 || inum >= AGENT_FILE_VERSION_MAX)
		return -1;
	return inum;
}

static int agent_file_version_matches_identity(
	struct agent_file_version_entry *entry, uint64 dev, uint64 inum,
	uint64 incarnation)
{
	return entry->used && entry->dev == dev && entry->inum == inum &&
	       entry->incarnation == incarnation;
}

static int agent_file_version_matches_inode(
	struct agent_file_version_entry *entry, struct inode *ip)
{
	return agent_file_version_matches_identity(
		       entry, ip->dev, ip->inum, ip->vfs_incarnation) &&
	       entry->scope_id == ip->vfs_scope_id &&
	       entry->storage_owner == ip->fs_owner_domain &&
	       entry->vfs_policy == ip->vfs_policy;
}

static void agent_file_version_clear_locked(int slot)
{
	struct agent_file_version_entry *entry;
	uint64 dev;
	uint64 inum;
	uint64 incarnation;

	if (slot < 0 || slot >= AGENT_FILE_VERSION_MAX)
		return;
	entry = &agent_file_versions[slot];
	if (!entry->used)
		return;
	dev = entry->dev;
	inum = entry->inum;
	incarnation = entry->incarnation;
	memset(entry, 0, sizeof(*entry));
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_file_edits[i].active && agent_file_edits[i].dev == dev &&
		    agent_file_edits[i].inum == inum &&
		    agent_file_edits[i].incarnation == incarnation)
			memset(&agent_file_edits[i], 0,
			       sizeof(agent_file_edits[i]));
	for (int i = 0; i < AGENT_FILE_DIGEST_CACHE_MAX; i++)
		if (agent_file_digest_cache[i].valid &&
		    agent_file_digest_cache[i].dev == dev &&
		    agent_file_digest_cache[i].inum == inum &&
		    agent_file_digest_cache[i].incarnation == incarnation)
			memset(&agent_file_digest_cache[i], 0,
			       sizeof(agent_file_digest_cache[i]));
}

static int agent_file_version_slot_locked(struct inode *ip, int create)
{
	struct agent_file_version_entry *entry;
	int slot;

	if (ip == 0 || !ip->valid || ip->type != T_FILE ||
	    ip->vfs_incarnation == 0 || ip->vfs_policy == VFS_POLICY_FREE ||
	    ip->fs_owner_domain < FS_OWNER_SYSTEM ||
	    ip->fs_owner_version != FS_OWNER_VERSION)
		return -1;
	slot = agent_file_version_index(ip->dev, ip->inum);
	if (slot < 0)
		return -1;
	entry = &agent_file_versions[slot];
	if (agent_file_version_matches_inode(entry, ip))
		return slot;
	if (!create)
		return -1;

	// An inode number owns its sidecar slot. A new incarnation retires every
	// transient state associated with the previous inode lifetime.
	agent_file_version_clear_locked(slot);
	memset(entry, 0, sizeof(*entry));
	entry->used = 1;
	entry->scope_id = ip->vfs_scope_id;
	entry->dev = ip->dev;
	entry->inum = ip->inum;
	entry->incarnation = ip->vfs_incarnation;
	entry->storage_owner = ip->fs_owner_domain;
	entry->vfs_policy = ip->vfs_policy;
	return slot;
}

static int agent_file_version_identity_slot_locked(uint64 dev, uint64 inum,
						    uint64 incarnation)
{
	int slot = agent_file_version_index(dev, inum);

	if (slot < 0 || !agent_file_version_matches_identity(
				 &agent_file_versions[slot], dev, inum, incarnation))
		return -1;
	return slot;
}

static int agent_file_content_version_locked(struct inode *ip,
					     uint64 *version, int create)
{
	int slot;

	if (version == 0)
		return -1;
	slot = agent_file_version_slot_locked(ip, create);
	if (slot < 0)
		return -1;
	*version = agent_file_versions[slot].content_version;
	return 0;
}

static void agent_file_content_bump(struct inode *ip)
{
	int slot;
	int enabled;

	enabled = agent_edit_lock();
	slot = agent_file_version_slot_locked(ip, 1);
	if (slot >= 0) {
		agent_file_content_generation++;
		if (agent_file_content_generation == 0)
			agent_file_content_generation = 1;
		agent_file_versions[slot].content_version =
			agent_file_content_generation;
	}
	agent_edit_unlock(enabled);
}

// File data and Agent metadata use different transaction domains. Publish the
// committed inode size in the incarnation sidecar before either domain may
// sleep, then let the metadata transaction reconcile and persist it later.
static int agent_file_size_publish(struct inode *ip, int force)
{
	struct agent_file_version_entry *entry;
	int changed = -1;
	int enabled;
	int slot;

	if (ip == 0)
		return -1;
	enabled = agent_edit_lock();
	slot = agent_file_version_slot_locked(ip, 1);
	if (slot >= 0) {
		entry = &agent_file_versions[slot];
		if (force || !entry->published_size_valid ||
		    entry->published_size != ip->size) {
			entry->published_size_valid = 1;
			entry->published_size_dirty = 1;
			entry->published_size = ip->size;
			entry->published_size_sequence =
				__sync_add_and_fetch(&agent_file_size_sequence, 1);
			entry->published_size_generation =
				agent_file_generation_next(ip->vfs_scope_id);
			entry->published_size_tick = agent_ticks();
			changed = 1;
		} else {
			changed = 0;
		}
	}
	agent_edit_unlock(enabled);
	return changed;
}

static void agent_file_overlay_published_size_locked(
	struct agent_file_meta *meta, uint scope_id)
{
	struct agent_file_version_entry *entry;
	int slot;

	if (meta == 0)
		return;
	slot = agent_file_version_identity_slot_locked(
		meta->dev, meta->inum, meta->incarnation);
	if (slot < 0)
		return;
	entry = &agent_file_versions[slot];
	if (!entry->published_size_valid || entry->scope_id != scope_id)
		return;
	meta->size = entry->published_size;
	if (entry->published_size_generation > meta->fs_generation)
		meta->fs_generation = entry->published_size_generation;
	if (entry->published_size_tick > meta->updated_tick)
		meta->updated_tick = entry->published_size_tick;
}

static void agent_file_sizes_persisted_locked(uint scope_id, uint64 sequence)
{
	for (int i = 0; i < AGENT_FILE_VERSION_MAX; i++)
		if (agent_file_versions[i].published_size_dirty &&
		    agent_file_versions[i].scope_id == scope_id &&
		    agent_file_versions[i].published_size_sequence <= sequence)
			agent_file_versions[i].published_size_dirty = 0;
}

void agent_file_version_reclaim(struct inode *ip)
{
	int slot;
	int enabled;

	if (ip == 0)
		return;
	slot = agent_file_version_index(ip->dev, ip->inum);
	if (slot < 0)
		return;
	enabled = agent_edit_lock();
	if (agent_file_version_matches_identity(
		    &agent_file_versions[slot], ip->dev, ip->inum,
		    ip->vfs_incarnation))
		agent_file_version_clear_locked(slot);
	agent_edit_unlock(enabled);
}

static uint64 agent_edit_version_locked(uint64 dev, uint64 inum,
					uint64 incarnation, int *ok)
{
	int slot = agent_file_version_identity_slot_locked(dev, inum,
							  incarnation);

	if (slot < 0) {
		if (ok)
			*ok = 0;
		return 0;
	}
	if (ok)
		*ok = 1;
	return agent_file_versions[slot].edit_version;
}

static uint64 agent_edit_version_inode_locked(struct inode *ip, int create,
					      int *ok)
{
	int slot = agent_file_version_slot_locked(ip, create);

	if (slot < 0) {
		if (ok)
			*ok = 0;
		return 0;
	}
	if (ok)
		*ok = 1;
	return agent_file_versions[slot].edit_version;
}

static int agent_edit_set_version_locked(uint64 dev, uint64 inum,
					 uint64 incarnation, uint64 version)
{
	int slot = agent_file_version_identity_slot_locked(dev, inum,
							  incarnation);

	if (slot < 0)
		return -1;
	agent_file_versions[slot].edit_version = version;
	return 0;
}

static void agent_edit_bump_version_locked(struct inode *ip)
{
	int slot = agent_file_version_slot_locked(ip, 1);

	if (slot >= 0)
		agent_file_versions[slot].edit_version++;
}

static struct agent_file_edit_entry *
agent_edit_find_locked(uint scope_id, uint64 dev, uint64 inum,
		       uint64 incarnation)
{
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_file_edits[i].active &&
		    agent_file_edits[i].scope_id == scope_id &&
		    agent_file_edits[i].dev == dev &&
		    agent_file_edits[i].inum == inum &&
		    agent_file_edits[i].incarnation == incarnation)
			return &agent_file_edits[i];
	return 0;
}

static struct agent_file_edit_entry *agent_edit_find_lease_locked(
	uint scope_id, uint64 lease)
{
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_file_edits[i].active &&
		    agent_file_edits[i].scope_id == scope_id &&
		    agent_file_edits[i].lease_id == lease)
			return &agent_file_edits[i];
	return 0;
}

static struct agent_file_edit_entry *agent_edit_free_locked(uint scope_id)
{
	int owned = 0;

	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_file_edits[i].active &&
		    agent_file_edits[i].scope_id == scope_id)
			owned++;
	if (owned >= AGENT_EDIT_SCOPE_LIMIT)
		return 0;
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (!agent_file_edits[i].active)
			return &agent_file_edits[i];
	return 0;
}

static int agent_edit_expired(struct agent_file_edit_entry *e, uint64 now)
{
	return e->active && e->deadline_tick != 0 && now >= e->deadline_tick;
}

static void agent_edit_release_locked(struct agent_file_edit_entry *e,
				      int publish_dirty)
{
	if (e == 0 || !e->active)
		return;
	if (publish_dirty && e->dirty)
		agent_edit_set_version_locked(e->dev, e->inum, e->incarnation,
					      e->base_version + 1);
	memset(e, 0, sizeof(*e));
}

static void agent_edit_cleanup_expired_locked(uint64 now)
{
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_edit_expired(&agent_file_edits[i], now))
			agent_edit_release_locked(&agent_file_edits[i], 1);
}

static int agent_edit_owner(struct agent_file_edit_entry *e, struct proc *p)
{
	return e && p && p->is_agent && e->scope_id == agent_identity_proc_scope(p) &&
	       e->owner_control_id != 0 &&
	       e->owner_control_id == p->agent_control_id;
}

static int agent_edit_can_write(struct proc *p)
{
	return agent_identity_has_any_cap(p, AGENT_CAP_ARTIFACT_WRITE |
				    AGENT_CAP_ORCHESTRATE);
}

static void agent_edit_fill_state_locked(struct agent_file_edit_state *state,
					 struct agent_file_edit_entry *e,
					 uint64 dev, uint64 inum,
					 uint64 incarnation, char *path)
{
	int ok;

	memset(state, 0, sizeof(*state));
	state->dev = dev;
	state->inum = inum;
	state->incarnation = incarnation;
	state->current_version =
		agent_edit_version_locked(dev, inum, incarnation, &ok);
	if (path)
		safestrcpy(state->path, path, sizeof(state->path));
	if (e == 0 || !e->active)
		return;
	state->active = 1;
	state->owner_pid = e->owner_pid;
	state->owner_agent_id = e->owner_agent_id;
	state->owner_role = e->owner_role;
	state->dirty = e->dirty;
	state->lease_id = e->lease_id;
	state->base_version = e->base_version;
	state->deadline_tick = e->deadline_tick;
	state->conflict_count = e->conflict_count;
	if (e->path[0])
		safestrcpy(state->path, e->path, sizeof(state->path));
}

static void agent_direct_effect_audit(struct proc *p, int tool_id, int status,
				      char *text, uint64 value0,
				      uint64 value1, uint64 value2,
				      uint64 flags, int authority_effect)
{
	if (p == 0 || !p->is_agent)
		return;
	agent_observe_record_effect(p, tool_id, status, text, value0, value1,
				    value2, flags, authority_effect);
}

static void agent_edit_audit(struct proc *p, int status, char *text,
			     struct agent_file_edit_state *state,
			     int authority_effect)
{
	if (p == 0 || !p->is_agent || state == 0)
		return;
	agent_direct_effect_audit(p, AGENT_TOOL_QUERY_FILE, status, text,
				  state->lease_id, state->current_version,
				  state->owner_pid, state->dev,
				  authority_effect);
}

static void agent_file_normalize_physical(int slot, struct agent_file_meta *m)
{
	char generated[DIRSIZ];

	if (m->physical_name[0] == 0 ||
	    strlen(m->physical_name) >= DIRSIZ ||
	    agent_file_is_meta_store_name(m->physical_name)) {
		agent_slot_name(slot, generated, sizeof(generated));
		safestrcpy(m->physical_name, generated,
			   sizeof(m->physical_name));
	}
}

static struct inode *agent_fs_lookup_or_create_status(char *name, int create,
						      uint policy,
						      uint scope_id,
						      struct proc *actor,
						      int *status)
{
	struct inode *ip;
	struct vfs_cred kernel_cred;
	struct vfs_cred actor_cred;
	int lookup_status = FS_LOOKUP_ERROR;

	if (status)
		*status = FS_LOOKUP_ERROR;

	if (policy == VFS_POLICY_WORKFLOW) {
		if (scope_id == VFS_SCOPE_SYSTEM && create)
			return 0;
		if (scope_id != VFS_SCOPE_SYSTEM &&
		    (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
		     scope_id >= FS_OWNER_SCOPE_FLAG ||
		     (create ? !vfs_scope_active(scope_id) :
			       !vfs_scope_retained(scope_id))))
			return 0;
	} else {
		vfs_cred_kernel(&kernel_cred);
		scope_id = VFS_SCOPE_NONE;
	}
	if ((ip = namei_scope_status(name, policy, scope_id,
				     &lookup_status)) != 0) {
		ivalid(ip);
		if (ip->type == T_FILE && vfs_inode_label_valid(ip) &&
		    ip->vfs_policy == policy &&
		    (policy != VFS_POLICY_WORKFLOW ||
		     ip->vfs_scope_id == scope_id)) {
			if (status)
				*status = FS_LOOKUP_FOUND;
			return ip;
		}
		iput(ip);
		return 0;
	}
	if (lookup_status != FS_LOOKUP_ABSENT)
		return 0;
	if (!create)
	{
		if (status)
			*status = FS_LOOKUP_ABSENT;
		return 0;
	}
	if (policy == VFS_POLICY_WORKFLOW) {
		vfs_cred_from_proc(actor, &actor_cred);
		if (actor == 0 || actor_cred.scope_id != scope_id)
			return 0;
		ip = fs_create(name, T_FILE, 0, &actor_cred, policy);
	} else {
		ip = fs_create(name, T_FILE, 0, &kernel_cred, policy);
	}
	if (ip != 0 && status)
		*status = FS_LOOKUP_FOUND;
	return ip;
}

static struct inode *agent_fs_lookup_or_create(char *name, int create,
					       uint policy, uint scope_id,
					       struct proc *actor)
{
	return agent_fs_lookup_or_create_status(name, create, policy, scope_id,
					      actor, 0);
}

static int
agent_metadata_apply_slot_available(const struct agent_meta_record *records,
				    uint record_index, uint scope_id,
				    int slot)
{
	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return 0;
	if (agent_files[slot].used && agent_file_scopes[slot] != scope_id)
		return 0;
	for (uint i = 0; i < record_index; i++)
		if (records[i].scope_id == scope_id &&
		    agent_metadata_apply_slots[i] == slot)
			return 0;
	return 1;
}

static int agent_file_bind_slot_status(int slot, int create,
				       struct proc *actor, int *lookup_status)
{
	struct agent_file_meta *m;
	struct inode *ip;

	if (lookup_status)
		*lookup_status = FS_LOOKUP_ERROR;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return -1;
	m = &agent_files[slot];
	if (!m->used || !agent_object_scope_valid(agent_file_scopes[slot]))
		return -1;
	agent_file_normalize_physical(slot, m);
	ip = agent_fs_lookup_or_create_status(
		m->physical_name, create, VFS_POLICY_WORKFLOW,
		agent_file_scopes[slot], actor, lookup_status);
	if (ip == 0)
		return -1;
	if ((m->dev != 0 || m->inum != 0 || m->incarnation != 0) &&
	    (m->dev != ip->dev || m->inum != ip->inum ||
	     m->incarnation != ip->vfs_incarnation)) {
		iput(ip);
		if (lookup_status)
			*lookup_status = FS_LOOKUP_ABSENT;
		return -1;
	}
	ip->agent_meta_slot = slot + 1;
	ip->agent_meta_flags = m->flags & AGENT_FILE_META_F_PERSIST;
	ip->agent_meta_version = AGENT_INODE_META_VERSION;
	iupdate(ip);
	m->dev = ip->dev;
	m->inum = ip->inum;
	m->incarnation = ip->vfs_incarnation;
	m->size = ip->size;
	m->fs_generation = agent_file_generation_next(agent_file_scopes[slot]);
	iput(ip);
	return 0;
}

static int agent_file_bind_slot(int slot, int create, struct proc *actor)
{
	return agent_file_bind_slot_status(slot, create, actor, 0);
}

static void agent_file_unbind_meta(int slot, struct agent_file_meta *meta,
				   uint scope_id)
{
	struct inode *ip;

	if (slot < 0 || slot >= AGENT_FILE_META_MAX || meta == 0 ||
	    !meta->used || meta->physical_name[0] == 0)
		return;
	ip = namei_scope(meta->physical_name, VFS_POLICY_WORKFLOW, scope_id);
	if (ip == 0)
		return;
	ivalid(ip);
	if (ip->agent_meta_slot == slot + 1 && ip->dev == meta->dev &&
	    ip->inum == meta->inum &&
	    ip->vfs_incarnation == meta->incarnation) {
		ip->agent_meta_slot = 0;
		ip->agent_meta_flags = 0;
		ip->agent_meta_version = 0;
		iupdate(ip);
	}
	iput(ip);
}

static void agent_file_clear_slot(int slot)
{
	int was_used;
	uint scope_id;

	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return;
	was_used = agent_files[slot].used;
	scope_id = agent_file_scopes[slot];
	agent_file_unbind_meta(slot, &agent_files[slot],
			       agent_file_scopes[slot]);
	memset(&agent_files[slot], 0, sizeof(agent_files[slot]));
	agent_file_scopes[slot] = VFS_SCOPE_NONE;
	if (was_used)
		agent_file_generation_next(scope_id);
}

static void agent_file_restore_slot(int slot,
				    struct agent_file_meta *previous,
				    uint previous_scope, int had_previous)
{
	agent_file_unbind_meta(slot, &agent_files[slot],
			       agent_file_scopes[slot]);
	if (!had_previous) {
		memset(&agent_files[slot], 0, sizeof(agent_files[slot]));
		agent_file_scopes[slot] = VFS_SCOPE_NONE;
	} else {
		agent_files[slot] = *previous;
		agent_file_scopes[slot] = previous_scope;
		if (agent_file_bind_slot(slot, 0, 0) == 0)
			agent_files[slot] = *previous;
	}
	agent_file_maintain(AGENT_FILE_CHANGE_ALL);
}

int
agent_metadata_objects_live_count(void)
{
	int used = 0;

	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		if (agent_files[i].used)
			used++;
	return used;
}

void
agent_metadata_objects_clear_catalog(void)
{
	memset(agent_files, 0, sizeof(agent_files));
	memset(agent_file_scopes, 0, sizeof(agent_file_scopes));
	agent_file_maintain(AGENT_FILE_CHANGE_ALL);
}

static int
agent_metadata_objects_preflight(const struct agent_meta_record *records,
				 uint count, int reload_one_scope,
				 uint reload_scope,
				 struct agent_metadata_apply_result *result)
{
	for (uint i = 0; i < count; i++) {
		struct agent_file_meta meta;
		struct inode *ip;
		int lookup_status = FS_LOOKUP_ERROR;
		int slot;

		agent_metadata_apply_slots[i] = -1;
		memset(&agent_metadata_apply_bindings[i], 0,
		       sizeof(agent_metadata_apply_bindings[i]));
		if (reload_one_scope && records[i].scope_id != reload_scope)
			continue;
		if (!agent_object_scope_valid(records[i].scope_id) ||
		    records[i].slot >= AGENT_FILE_META_MAX)
			return -1;
		slot = records[i].slot;
		if (reload_one_scope &&
		    !agent_metadata_apply_slot_available(
			    records, i, reload_scope, slot)) {
			for (slot = 0; slot < AGENT_FILE_META_MAX; slot++)
				if (agent_metadata_apply_slot_available(
					    records, i, reload_scope, slot))
					break;
			if (slot == AGENT_FILE_META_MAX)
				return -1;
			result->layout_changed = 1;
		}

		meta = records[i].meta;
		agent_file_normalize_physical(slot, &meta);
		ip = agent_fs_lookup_or_create_status(
			meta.physical_name, 0, VFS_POLICY_WORKFLOW,
			records[i].scope_id, 0, &lookup_status);
		if (ip == 0) {
			if (lookup_status == FS_LOOKUP_ERROR)
				return -1;
			result->missing_slots[records[i].slot / 8] |=
				1U << (records[i].slot % 8);
			continue;
		}
		if ((meta.dev != 0 || meta.inum != 0 ||
		     meta.incarnation != 0) &&
		    (meta.dev != ip->dev || meta.inum != ip->inum ||
		     meta.incarnation != ip->vfs_incarnation)) {
			result->missing_slots[records[i].slot / 8] |=
				1U << (records[i].slot % 8);
			iput(ip);
			continue;
		}
		agent_metadata_apply_bindings[i].dev = ip->dev;
		agent_metadata_apply_bindings[i].inum = ip->inum;
		agent_metadata_apply_bindings[i].incarnation =
			ip->vfs_incarnation;
		iput(ip);
		agent_metadata_apply_slots[i] = slot;
	}
	return 0;
}

int
agent_metadata_objects_apply_snapshot(
	const struct agent_meta_record *records, uint count,
	int reload_one_scope, uint reload_scope,
	struct agent_metadata_apply_result *result)
{
	if (records == 0 || result == 0 || count > AGENT_FILE_META_MAX ||
	    (reload_one_scope && !agent_scope_valid(reload_scope)))
		return -1;
	memset(result, 0, sizeof(*result));
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_apply_slots[i] = -1;
		memset(&agent_metadata_apply_bindings[i], 0,
		       sizeof(agent_metadata_apply_bindings[i]));
	}
	if (agent_metadata_objects_preflight(
		    records, count, reload_one_scope, reload_scope, result) < 0)
		return -1;

	if (reload_one_scope) {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			if (agent_files[i].used &&
			    agent_file_scopes[i] == reload_scope)
				agent_file_clear_slot(i);
	} else {
		memset(agent_files, 0, sizeof(agent_files));
		memset(agent_file_scopes, 0, sizeof(agent_file_scopes));
	}
	for (uint i = 0; i < count; i++) {
		struct agent_metadata_binding *binding =
			&agent_metadata_apply_bindings[i];
		struct inode *ip;
		int slot = agent_metadata_apply_slots[i];

		if (slot < 0 || binding->dev == 0 || binding->inum == 0)
			continue;
		ip = inode_get(binding->dev, binding->inum);
		if (ip != 0)
			ivalid(ip);
		if (ip == 0 || ip->type != T_FILE ||
		    !vfs_inode_label_valid(ip) ||
		    ip->vfs_policy != VFS_POLICY_WORKFLOW ||
		    ip->vfs_scope_id != records[i].scope_id ||
		    ip->vfs_incarnation != binding->incarnation) {
			if (ip != 0)
				iput(ip);
			result->missing_slots[records[i].slot / 8] |=
				1U << (records[i].slot % 8);
			continue;
		}
		agent_files[slot] = records[i].meta;
		agent_files[slot].update_mask = 0;
		agent_file_scopes[slot] = records[i].scope_id;
		agent_file_normalize_physical(slot, &agent_files[slot]);
		ip->agent_meta_slot = slot + 1;
		ip->agent_meta_flags =
			agent_files[slot].flags & AGENT_FILE_META_F_PERSIST;
		ip->agent_meta_version = AGENT_INODE_META_VERSION;
		iupdate(ip);
		agent_files[slot].dev = ip->dev;
		agent_files[slot].inum = ip->inum;
		agent_files[slot].incarnation = ip->vfs_incarnation;
		agent_files[slot].size = ip->size;
		agent_files[slot].fs_generation =
			agent_file_generation_next(records[i].scope_id);
		iput(ip);
		if (agent_file_scan_active)
			agent_file_scan_seen[slot] = 1;
	}
	agent_file_maintain(AGENT_FILE_CHANGE_ALL);
	result->used = agent_metadata_objects_live_count();
	return result->used;
}

int
agent_metadata_objects_export_scope(uint scope_id,
				    struct agent_meta_record *records,
				    int capacity, uint64 *size_sequence)
{
	int count = 0;
	int enabled;

	if (!agent_object_scope_valid(scope_id) || records == 0 ||
	    capacity < 0 || size_sequence == 0)
		return -1;
	enabled = agent_edit_lock();
	*size_sequence = agent_file_size_sequence;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		struct agent_meta_record *record;

		if (!agent_files[i].used || agent_file_scopes[i] != scope_id ||
		    (agent_files[i].flags & AGENT_FILE_META_F_PERSIST) == 0)
			continue;
		if (count >= capacity) {
			agent_edit_unlock(enabled);
			return -1;
		}
		record = &records[count++];
		memset(record, 0, sizeof(*record));
		record->meta = agent_files[i];
		agent_file_overlay_published_size_locked(&record->meta, scope_id);
		record->meta.update_mask = 0;
		record->scope_id = scope_id;
		record->slot = i;
	}
	agent_edit_unlock(enabled);
	return count;
}

void
agent_metadata_objects_sizes_persisted(uint scope_id, uint64 size_sequence)
{
	int enabled = agent_edit_lock();

	agent_file_sizes_persisted_locked(scope_id, size_sequence);
	agent_edit_unlock(enabled);
}

static int agent_file_find_slot_by_physical(char *path, uint scope_id)
{
	if (path == 0 || path[0] == 0)
		return -1;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used || agent_file_scopes[i] != scope_id)
			continue;
		if (strncmp(agent_files[i].physical_name, path,
			    sizeof(agent_files[i].physical_name)) == 0)
			return i;
	}
	return -1;
}

static int agent_file_alloc_slot(uint scope_id)
{
	int owned = 0;
	int limit = scope_id == VFS_SCOPE_SYSTEM ?
			AGENT_FILE_SYSTEM_LIMIT : AGENT_FILE_SCOPE_LIMIT;

	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		if (agent_files[i].used && agent_file_scopes[i] == scope_id)
			owned++;
	if (owned >= limit)
		return -1;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		if (!agent_files[i].used)
			return i;
	return -1;
}

static uint64 agent_file_alloc_fid(uint scope_id)
{
	for (uint64 candidate = 1;
	     candidate <= AGENT_FILE_META_MAX; candidate++) {
		int used = 0;

		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			if (agent_files[i].used &&
			    agent_file_scopes[i] == scope_id &&
			    agent_files[i].fid == candidate) {
				used = 1;
				break;
			}
		if (!used)
			return candidate;
	}
	return 0;
}

static void agent_file_infer_kind(char *name, char *out, int n)
{
	if (agent_contains(name, "md") || agent_contains(name, "txt"))
		safestrcpy(out, "document", n);
	else if (agent_contains(name, "log") || agent_contains(name, "err"))
		safestrcpy(out, "log", n);
	else if (agent_contains(name, "status") || agent_contains(name, "ok"))
		safestrcpy(out, "status", n);
	else if (agent_contains(name, "data") || agent_contains(name, "csv"))
		safestrcpy(out, "dataset", n);
	else
		safestrcpy(out, "file", n);
}

static void agent_file_infer_status(char *name, char *out, int n)
{
	if (agent_contains(name, "fail") || agent_contains(name, "err"))
		safestrcpy(out, "failed", n);
	else if (agent_contains(name, "ok") || agent_contains(name, "pass"))
		safestrcpy(out, "ok", n);
	else
		safestrcpy(out, "present", n);
}

static uint agent_file_scan_bind_inode(struct inode *ip, char *path,
				       int account_scan)
{
	struct agent_file_meta *m;
	int slot;
	int added = 0;
	int changed = 0;
	uint changes = 0;

	if (ip == 0 || path == 0 || path[0] == 0 ||
	    agent_file_is_meta_store_name(path))
		return 0;
	ivalid(ip);
	if (!vfs_inode_label_valid(ip) ||
	    ip->vfs_policy != VFS_POLICY_WORKFLOW || ip->type != T_FILE ||
	    !agent_object_scope_valid(ip->vfs_scope_id))
		return 0;
	if (ip->vfs_scope_id == VFS_SCOPE_SYSTEM) {
		if (exec_policy_inode_mutable(ip))
			return 0;
	} else if (!vfs_scope_active(ip->vfs_scope_id) ||
		   !exec_policy_inode_mutable(ip)) {
		return 0;
	}
	slot = ip->agent_meta_slot - 1;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX ||
	    !agent_files[slot].used ||
	    agent_file_scopes[slot] != ip->vfs_scope_id ||
	    agent_files[slot].dev != ip->dev ||
	    agent_files[slot].inum != ip->inum ||
	    agent_files[slot].incarnation != ip->vfs_incarnation)
		slot = agent_file_find_slot_by_physical(path,
						 ip->vfs_scope_id);
	if (slot >= 0 && agent_files[slot].dev != 0 &&
	    (agent_files[slot].dev != ip->dev ||
	     agent_files[slot].inum != ip->inum ||
	     agent_files[slot].incarnation != ip->vfs_incarnation) &&
	    (agent_files[slot].flags & AGENT_FILE_META_F_AUTOSCAN) == 0)
		slot = -1;
	if (slot < 0)
		slot = agent_file_alloc_slot(ip->vfs_scope_id);
	if (slot < 0)
		return 0;
	m = &agent_files[slot];
	if (!m->used) {
		uint64 fid = agent_file_alloc_fid(ip->vfs_scope_id);

		if (fid == 0)
			return 0;
		memset(m, 0, sizeof(*m));
		m->used = 1;
		m->fid = fid;
		m->flags = AGENT_FILE_META_F_PERSIST |
			   AGENT_FILE_META_F_AUTOSCAN;
		agent_file_scopes[slot] = ip->vfs_scope_id;
		added = 1;
		changes |= AGENT_FILE_CHANGE_MEMBERSHIP;
	}
	if (agent_file_scopes[slot] != ip->vfs_scope_id)
		return 0;
	if (m->physical_name[0] == 0 ||
	    (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		if (strncmp(m->physical_name, path,
			    sizeof(m->physical_name)) != 0) {
			safestrcpy(m->physical_name, path,
				   sizeof(m->physical_name));
			changed = 1;
			changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
		}
	}
	if (m->logical_path[0] == 0 ||
	    (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		if (strncmp(m->logical_path, path, sizeof(m->logical_path)) !=
		    0) {
			safestrcpy(m->logical_path, path,
				   sizeof(m->logical_path));
			changed = 1;
			changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
		}
	}
	if (m->project[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(m->project, "root", sizeof(m->project));
		changed = 1;
		changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
	}
	if (m->workflow[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(m->workflow, "background-scan",
			   sizeof(m->workflow));
		changed = 1;
		changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
	}
	if (m->run_id[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(m->run_id, "ROOT", sizeof(m->run_id));
		changed = 1;
		changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
	}
	if (m->stage[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(m->stage, "scan", sizeof(m->stage));
		changed = 1;
		changes |= AGENT_FILE_CHANGE_STAGE;
	}
	if (m->kind[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		agent_file_infer_kind(path, m->kind, sizeof(m->kind));
		changed = 1;
		changes |= AGENT_FILE_CHANGE_KIND;
	}
	if (m->status[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		agent_file_infer_status(path, m->status, sizeof(m->status));
		changed = 1;
		changes |= AGENT_FILE_CHANGE_STATUS;
	}
	if (m->summary[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(m->summary, "auto scanned root file",
			   sizeof(m->summary));
		changed = 1;
	}
	if (m->dev != ip->dev || m->inum != ip->inum ||
	    m->incarnation != ip->vfs_incarnation || m->size != ip->size) {
		m->dev = ip->dev;
		m->inum = ip->inum;
		m->incarnation = ip->vfs_incarnation;
		m->size = ip->size;
		changed = 1;
	}
	if (ip->agent_meta_slot != slot + 1 ||
	    ip->agent_meta_version != AGENT_INODE_META_VERSION) {
		ip->agent_meta_slot = slot + 1;
		ip->agent_meta_flags = m->flags & AGENT_FILE_META_F_PERSIST;
		ip->agent_meta_version = AGENT_INODE_META_VERSION;
		iupdate(ip);
		changed = 1;
	}
	if (changed || added) {
		m->updated_tick = agent_ticks();
		m->fs_generation = agent_file_generation_next(
			agent_file_scopes[slot]);
		if (m->flags & AGENT_FILE_META_F_PERSIST)
			agent_metadata_store_mark_dirty(agent_file_scopes[slot]);
		if (account_scan) {
			if (added)
				agent_file_scan_added++;
			else
				agent_file_scan_updated++;
		}
	}
	agent_file_scan_seen[slot] = 1;
	return changes;
}

static uint64 agent_file_scan_rest_deadline(uint64 started_tick, uint64 now)
{
	uint64 duration = now > started_tick ? now - started_tick : 0;
	uint64 rest = AGENT_FS_SCAN_INTERVAL;

	if (duration > ~0ULL / AGENT_FS_SCAN_REST_MULTIPLIER)
		return ~0ULL;
	duration *= AGENT_FS_SCAN_REST_MULTIPLIER;
	if (duration > rest)
		rest = duration;
	if (rest > ~0ULL - now)
		return ~0ULL;
	return now + rest;
}

static int agent_file_scan_due(uint64 now)
{
	int due;
	int enabled = intr_save();

	due = agent_file_scan_enabled &&
		(agent_file_scan_active ?
		 agent_file_scan_last_step_tick != now :
		 agent_file_scan_pending && now >= agent_file_scan_next_tick);
	intr_restore(enabled);
	return due;
}

static void agent_file_scan_pause(int retry)
{
	uint64 now = agent_ticks();
	uint64 started;
	uint64 deadline;
	int enabled = intr_save();

	started = agent_file_scan_active ? agent_file_scan_started_tick : now;
	deadline = agent_file_scan_rest_deadline(started, now);
	agent_file_scan_active = 0;
	agent_file_scan_started_tick = 0;
	if (retry)
		agent_file_scan_pending = 1;
	if (agent_file_scan_next_tick < deadline)
		agent_file_scan_next_tick = deadline;
	intr_restore(enabled);
}

void agent_file_request_scan(void)
{
	uint64 now = agent_ticks();
	int enabled = intr_save();

	if (!agent_file_scan_enabled) {
		agent_file_scan_enabled = 1;
		agent_file_scan_pending = 1;
		agent_file_scan_next_tick = now;
	} else if (!agent_file_scan_pending) {
		agent_file_scan_pending = 1;
		/*
		 * Requests coalesce behind the current scan or cooldown. Repeated
		 * reconciliation faults therefore cannot slide the deadline forward
		 * or bypass the scanner's global duty-cycle bound.
		 */
		if (agent_file_scan_active)
			agent_file_scan_next_tick =
				agent_file_scan_rest_deadline(now, now);
		else if (now >= agent_file_scan_next_tick)
			agent_file_scan_next_tick = now;
	}
	intr_restore(enabled);
}

static void agent_file_enable_scan(void)
{
	agent_file_request_scan();
}

void agent_metadata_background_maintain(void)
{
	struct inode *root;
	struct inode *ip;
	struct dirent de;
	char name[DIRSIZ + 1];
	uint64 now;
	uint64 off;
	int steps = 0;
	uint changes = 0;
	int scan_failed = 0;
	int enabled;
	struct vfs_cred kernel_cred;

	vfs_scope_reap_pending();
	/* A scan storm must not starve an already due durable checkpoint. */
	agent_metadata_store_background_maintain();
	now = agent_ticks();
	if (!agent_file_scan_due(now))
		return;
	if (!bio_background_begin(FS_OWNER_SYSTEM))
		return;
	if (!agent_metadata_txn_try_external())
		goto out_io;
	vfs_cred_kernel(&kernel_cred);
	now = agent_ticks();
	if (!agent_file_scan_active) {
		if (!agent_file_scan_pending || now < agent_file_scan_next_tick)
			goto out_txn;
		if (agent_metadata_store_load() < 0) {
			agent_file_scan_pause(1);
			goto out_txn;
		}
		enabled = intr_save();
		agent_file_scan_pending = 0;
		agent_file_scan_active = 1;
		agent_file_scan_started_tick = now;
		agent_file_scan_offset = 0;
		memset(agent_file_scan_seen, 0, sizeof(agent_file_scan_seen));
		agent_file_scan_runs++;
		intr_restore(enabled);
	} else if (agent_file_scan_last_step_tick == now) {
		goto out_txn;
	}
	agent_file_scan_last_step_tick = now;
	root = root_dir();
	if (root == 0) {
		agent_file_scan_pause(1);
		goto out_txn;
	}
	for (off = agent_file_scan_offset;
	     off < root->size && steps < AGENT_FS_SCAN_STEP;
	     off += sizeof(de), steps++) {
		if (readi(root, &kernel_cred, 0, (uint64)&de, off,
			  sizeof(de)) != sizeof(de)) {
			scan_failed = 1;
			break;
		}
		agent_file_scan_entries++;
		if (de.inum == 0)
			continue;
		memset(name, 0, sizeof(name));
		memmove(name, de.name, DIRSIZ);
		name[DIRSIZ] = 0;
		if (name[0] == 0 || agent_file_is_meta_store_name(name))
			continue;
		ip = inode_get(root->dev, de.inum);
		if (ip == 0)
			continue;
		ivalid(ip);
		changes |= agent_file_scan_bind_inode(ip, name, 1);
		iput(ip);
	}
	agent_file_scan_offset = off;
	if (changes)
		agent_file_maintain(changes);
	if (scan_failed) {
		agent_file_scan_pause(1);
		iput(root);
		goto out_txn;
	}
	if (agent_file_scan_offset >= root->size) {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			int removed_persistent;
			uint removed_scope;

			if (!agent_files[i].used)
				continue;
			if (!agent_file_scan_seen[i]) {
				removed_scope = agent_file_scopes[i];
				removed_persistent =
					agent_files[i].flags & AGENT_FILE_META_F_PERSIST;
				agent_file_clear_slot(i);
				agent_file_scan_removed++;
				if (removed_persistent)
					agent_metadata_store_mark_dirty(removed_scope);
				changes |= AGENT_FILE_CHANGE_ALL;
			}
		}
		if (changes)
			agent_file_maintain(changes);
		agent_file_scan_pause(0);
	}
	iput(root);
out_txn:
	agent_metadata_txn_unlock();
out_io:
	bio_background_end();
}

static int agent_fs_inode_trackable(struct inode *ip)
{
	if (ip == 0)
		return 0;
	ivalid(ip);
	return vfs_inode_label_valid(ip) &&
	       ip->vfs_policy == VFS_POLICY_WORKFLOW && ip->type == T_FILE &&
	       agent_object_scope_valid(ip->vfs_scope_id);
}

static int agent_file_path_autopersist(uint scope_id, char *path,
				       struct agent_file_meta *binding)
{
	struct inode *ip;
	int eligible;

	if (!agent_scope_valid(scope_id) || path == 0 || path[0] == 0 ||
	    agent_file_is_meta_store_name(path))
		return 0;
	ip = agent_fs_lookup_or_create(path, 0, VFS_POLICY_WORKFLOW,
				       scope_id, 0);
	if (ip == 0)
		return 0;
	eligible = agent_fs_inode_trackable(ip) &&
		   ip->vfs_scope_id == scope_id && vfs_scope_active(scope_id) &&
		   exec_policy_inode_mutable(ip);
	if (eligible && binding) {
		binding->dev = ip->dev;
		binding->inum = ip->inum;
		binding->incarnation = ip->vfs_incarnation;
		binding->size = ip->size;
	}
	iput(ip);
	return eligible;
}

void agent_fs_note_create(struct inode *ip, char *path)
{
	int slot = -1;
	int reconcile = 0;
	uint scope_id;
	uint64 fid;

	if (ip == 0 || path == 0 ||
	    agent_file_is_meta_store_name(path))
		return;
	if (!agent_fs_inode_trackable(ip) ||
	    !agent_scope_valid(ip->vfs_scope_id) || ip->agent_meta_slot > 0)
		return;
	scope_id = ip->vfs_scope_id;
	if (!agent_metadata_txn_try_external()) {
		agent_file_request_scan();
		return;
	}
	if (!agent_fs_inode_trackable(ip) ||
	    !agent_scope_valid(ip->vfs_scope_id) ||
	    ip->agent_meta_slot > 0 || !agent_metadata_store_loaded()) {
		reconcile = 1;
		goto out_txn;
	}
	agent_file_content_bump(ip);
	slot = agent_file_alloc_slot(scope_id);
	if (slot < 0) {
		reconcile = 1;
		goto out_txn;
	}
	fid = agent_file_alloc_fid(scope_id);
	if (fid == 0) {
		reconcile = 1;
		goto out_txn;
	}
	memset(&agent_files[slot], 0, sizeof(agent_files[slot]));
	agent_files[slot].used = 1;
	agent_files[slot].fid = fid;
	agent_file_scopes[slot] = scope_id;
	agent_files[slot].flags = AGENT_FILE_META_F_PERSIST |
				  AGENT_FILE_META_F_AUTOSCAN;
	safestrcpy(agent_files[slot].physical_name, path,
		   sizeof(agent_files[slot].physical_name));
	safestrcpy(agent_files[slot].logical_path, path,
		   sizeof(agent_files[slot].logical_path));
	safestrcpy(agent_files[slot].kind, "file",
		   sizeof(agent_files[slot].kind));
	safestrcpy(agent_files[slot].status, "created",
		   sizeof(agent_files[slot].status));
	safestrcpy(agent_files[slot].summary, "created by fileopen",
		   sizeof(agent_files[slot].summary));
	agent_files[slot].updated_tick = agent_ticks();
	if (agent_file_scan_active)
		agent_file_scan_seen[slot] = 1;
	if (agent_file_bind_slot(slot, 0, 0) < 0) {
		agent_file_clear_slot(slot);
		reconcile = 1;
		goto out_txn;
	}
	agent_file_maintain(AGENT_FILE_CHANGE_ALL);
	agent_metadata_store_mark_dirty(scope_id);
out_txn:
	if (reconcile)
		agent_file_request_scan();
	agent_metadata_txn_unlock();
}

static void agent_fs_update_inode_meta(struct inode *ip, char *summary,
				       int published)
{
	int slot;
	int reconcile = 0;
	int enabled;
	uint scope_id;

	if (!agent_fs_inode_trackable(ip))
		return;
	scope_id = ip->vfs_scope_id;
	if (!agent_metadata_txn_try_external()) {
		/*
		 * A successful sidecar publication already carries the latest size,
		 * generation and tick into queries and checkpoints. Contention must
		 * not turn an ordinary write into a global directory scan.
		 */
		if (published > 0 &&
		    (ip->agent_meta_flags & AGENT_FILE_META_F_PERSIST))
			agent_metadata_store_mark_dirty(scope_id);
		else if (published < 0)
			agent_file_request_scan();
		return;
	}
	if (!agent_fs_inode_trackable(ip) ||
	    !agent_metadata_store_loaded()) {
		reconcile = 1;
		goto out_txn;
	}
	slot = ip->agent_meta_slot - 1;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX) {
		reconcile = 1;
		goto out_txn;
	}
	if (!agent_files[slot].used) {
		reconcile = 1;
		goto out_txn;
	}
	if (agent_file_scopes[slot] != scope_id ||
	    agent_files[slot].dev != ip->dev ||
	    agent_files[slot].inum != ip->inum ||
	    agent_files[slot].incarnation != ip->vfs_incarnation) {
		reconcile = 1;
		goto out_txn;
	}
	agent_files[slot].size = ip->size;
	if (published > 0) {
		enabled = agent_edit_lock();
		agent_file_overlay_published_size_locked(&agent_files[slot], scope_id);
		agent_edit_unlock(enabled);
	} else {
		agent_files[slot].updated_tick = agent_ticks();
		agent_files[slot].fs_generation =
			agent_file_generation_next(scope_id);
	}
	if (summary && summary[0] &&
	    (agent_files[slot].flags & AGENT_FILE_META_F_AUTOSCAN))
		safestrcpy(agent_files[slot].summary, summary,
			   sizeof(agent_files[slot].summary));
	/* Content-derived fields do not participate in status/stage/kind indexes. */
	if (agent_files[slot].flags & AGENT_FILE_META_F_PERSIST)
		agent_metadata_store_mark_dirty(scope_id);
out_txn:
	if (reconcile)
		agent_file_request_scan();
	agent_metadata_txn_unlock();
}

void agent_fs_note_write(struct inode *ip)
{
	int published;

	if (!agent_fs_inode_trackable(ip))
		return;
	agent_file_content_bump(ip);
	published = agent_file_size_publish(ip, 1);
	agent_fs_update_inode_meta(ip, "file content updated", published);
}

// A logical write invalidates cached content once, but a block-bounded write
// must publish its latest committed size before every scheduling safe point.
void agent_fs_sync_write(struct inode *ip)
{
	int published;

	if (!agent_fs_inode_trackable(ip))
		return;
	published = agent_file_size_publish(ip, 0);
	if (published != 0)
		agent_fs_update_inode_meta(ip, "file content updated", published);
}

void agent_fs_note_truncate(struct inode *ip)
{
	int published;

	if (!agent_fs_inode_trackable(ip))
		return;
	agent_file_content_bump(ip);
	published = agent_file_size_publish(ip, 1);
	agent_fs_update_inode_meta(ip, "file truncated", published);
}

void agent_fs_note_delete(struct inode *ip)
{
	int slot;
	int persistent;
	int reconcile = 0;
	uint scope_id;

	if (!agent_fs_inode_trackable(ip))
		return;
	scope_id = ip->vfs_scope_id;
	agent_file_content_bump(ip);
	if (!agent_metadata_txn_try_external()) {
		agent_file_request_scan();
		return;
	}
	if (!agent_fs_inode_trackable(ip) ||
	    !agent_metadata_store_loaded()) {
		reconcile = 1;
		goto out_txn;
	}
	slot = ip->agent_meta_slot - 1;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX) {
		reconcile = 1;
		goto out_txn;
	}
	if (!agent_files[slot].used ||
	    agent_file_scopes[slot] != scope_id ||
	    agent_files[slot].dev != ip->dev ||
	    agent_files[slot].inum != ip->inum ||
	    agent_files[slot].incarnation != ip->vfs_incarnation) {
		reconcile = 1;
		goto out_txn;
	}
	persistent = agent_files[slot].flags & AGENT_FILE_META_F_PERSIST;
	agent_file_clear_slot(slot);
	ip->agent_meta_slot = 0;
	ip->agent_meta_flags = 0;
	ip->agent_meta_version = 0;
	iupdate(ip);
	agent_file_maintain(AGENT_FILE_CHANGE_ALL);
	if (persistent)
		agent_metadata_store_mark_dirty(scope_id);
out_txn:
	if (reconcile)
		agent_file_request_scan();
	agent_metadata_txn_unlock();
}

static int agent_edit_modify_allowed(struct inode *ip, char *action)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;
	struct agent_file_edit_state state;
	uint64 now;
	int allowed = 1;
	int enabled;

	if (ip == 0)
		return 0;
	enabled = agent_edit_lock();
	now = agent_ticks();
	agent_edit_cleanup_expired_locked(now);
	edit = agent_edit_find_locked(ip->vfs_scope_id, ip->dev, ip->inum,
				      ip->vfs_incarnation);
	if (edit && !agent_edit_owner(edit, p)) {
		edit->conflict_count++;
		agent_edit_fill_state_locked(&state, edit, ip->dev, ip->inum,
					     ip->vfs_incarnation, 0);
		allowed = 0;
	}
	agent_edit_unlock(enabled);
	if (!allowed)
		agent_edit_audit(p, AGENT_STATUS_CONFLICT, action, &state, 0);
	return allowed;
}

int agent_edit_write_allowed(struct inode *ip)
{
	return agent_edit_modify_allowed(ip, "edit_write_conflict");
}

int agent_edit_truncate_allowed(struct inode *ip)
{
	return agent_edit_modify_allowed(ip, "edit_trunc_conflict");
}

int agent_edit_unlink_allowed(struct inode *ip)
{
	return agent_edit_modify_allowed(ip, "edit_unlink_conflict");
}

static void agent_edit_note_modify(struct inode *ip)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;
	int enabled;

	if (ip == 0)
		return;
	enabled = agent_edit_lock();
	agent_edit_cleanup_expired_locked(agent_ticks());
	edit = agent_edit_find_locked(ip->vfs_scope_id, ip->dev, ip->inum,
				      ip->vfs_incarnation);
	if (edit && agent_edit_owner(edit, p))
		edit->dirty = 1;
	else if (edit == 0)
		agent_edit_bump_version_locked(ip);
	agent_edit_unlock(enabled);
}

void agent_edit_note_write(struct inode *ip)
{
	agent_edit_note_modify(ip);
}

void agent_edit_note_truncate(struct inode *ip)
{
	agent_edit_note_modify(ip);
}

void agent_edit_note_delete(struct inode *ip)
{
	struct agent_file_edit_entry *edit;
	int enabled;

	if (ip == 0)
		return;
	enabled = agent_edit_lock();
	edit = agent_edit_find_locked(ip->vfs_scope_id, ip->dev, ip->inum,
				      ip->vfs_incarnation);
	if (edit)
		agent_edit_release_locked(edit, 1);
	else
		agent_edit_bump_version_locked(ip);
	agent_edit_unlock(enabled);
}

static void agent_file_reset_indexes(void)
{
	for (int i = 0; i < AGENT_FILE_INDEX_BUCKETS; i++) {
		agent_file_status_head[i] = -1;
		agent_file_stage_head[i] = -1;
		agent_file_kind_head[i] = -1;
	}
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_file_status_next[i] = -1;
		agent_file_stage_next[i] = -1;
		agent_file_kind_next[i] = -1;
	}
}

static void agent_file_rebuild_indexes(uint changes)
{
	uint64 b;

	if (changes & (AGENT_FILE_CHANGE_STATUS |
		       AGENT_FILE_CHANGE_MEMBERSHIP)) {
		for (int i = 0; i < AGENT_FILE_INDEX_BUCKETS; i++)
			agent_file_status_head[i] = -1;
		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			agent_file_status_next[i] = -1;
	}
	if (changes & (AGENT_FILE_CHANGE_STAGE |
		       AGENT_FILE_CHANGE_MEMBERSHIP)) {
		for (int i = 0; i < AGENT_FILE_INDEX_BUCKETS; i++)
			agent_file_stage_head[i] = -1;
		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			agent_file_stage_next[i] = -1;
	}
	if (changes & (AGENT_FILE_CHANGE_KIND |
		       AGENT_FILE_CHANGE_MEMBERSHIP)) {
		for (int i = 0; i < AGENT_FILE_INDEX_BUCKETS; i++)
			agent_file_kind_head[i] = -1;
		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			agent_file_kind_next[i] = -1;
	}
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if ((changes & (AGENT_FILE_CHANGE_STATUS |
				AGENT_FILE_CHANGE_MEMBERSHIP)) &&
		    agent_files[i].status[0]) {
			b = agent_bucket(agent_files[i].status);
			agent_file_status_next[i] = agent_file_status_head[b];
			agent_file_status_head[b] = i;
		}
		if ((changes & (AGENT_FILE_CHANGE_STAGE |
				AGENT_FILE_CHANGE_MEMBERSHIP)) &&
		    agent_files[i].stage[0]) {
			b = agent_bucket(agent_files[i].stage);
			agent_file_stage_next[i] = agent_file_stage_head[b];
			agent_file_stage_head[b] = i;
		}
		if ((changes & (AGENT_FILE_CHANGE_KIND |
				AGENT_FILE_CHANGE_MEMBERSHIP)) &&
		    agent_files[i].kind[0]) {
			b = agent_bucket(agent_files[i].kind);
			agent_file_kind_next[i] = agent_file_kind_head[b];
			agent_file_kind_head[b] = i;
		}
		agent_metadata_txn_work_charge(1);
	}
}

/*
 * Metadata mutations declare the fields they changed. Secondary indexes are
 * rebuilt only for affected fields. Legacy dependency masks stay canonical in
 * the file records and are interpreted by consumers, so topology changes only
 * advance a generation and never materialize a global derived graph.
 */
static void agent_file_maintain(uint changes)
{
	uint index_changes = changes &
		(AGENT_FILE_CHANGE_STATUS | AGENT_FILE_CHANGE_STAGE |
		 AGENT_FILE_CHANGE_KIND | AGENT_FILE_CHANGE_MEMBERSHIP);

	if (index_changes)
		agent_file_rebuild_indexes(index_changes);
	if ((changes & (AGENT_FILE_CHANGE_STAGE |
			AGENT_FILE_CHANGE_SCOPE_KEYS |
			AGENT_FILE_CHANGE_DEPENDENCY |
			AGENT_FILE_CHANGE_MEMBERSHIP)) == 0)
		return;
	agent_dependency_generation++;
}

static int agent_file_install_empty_store(void)
{
	if (agent_metadata_store_install_empty() < 0)
		return -1;
	agent_file_enable_scan();
	return 0;
}

int agent_scope_reclaim(uint scope_id, int preserve_files)
{
	int enabled;
	int files_status;
	int persist_status;
	int result;
	int changed;
	int dependency_changed = 0;
	int metadata_available;

	if (!agent_scope_valid(scope_id))
		return -1;
	if (!agent_metadata_txn_try_external())
		return -1;
	metadata_available = agent_metadata_store_available();
	if (metadata_available && agent_metadata_store_load() < 0) {
		result = -1;
		goto out_txn;
	}
	/* A corrupt global bank blocks metadata APIs, not VFS-labelled cleanup. */
	changed = metadata_available ?
		agent_metadata_store_shadow_has_scope(scope_id) : 0;
	if (metadata_available)
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			if (!agent_files[i].used ||
			    agent_file_scopes[i] != scope_id)
				continue;
			agent_file_clear_slot(i);
			agent_file_scan_seen[i] = 0;
			changed = 1;
		}
	for (int i = 0; i < AGENT_DEPENDENCY_MAX; i++)
		if (agent_dependencies[i].used &&
		    agent_dependencies[i].scope_id == scope_id) {
			memset(&agent_dependencies[i], 0,
			       sizeof(agent_dependencies[i]));
			dependency_changed = 1;
		}
	if (dependency_changed)
		agent_dependency_generation++;
	agent_action_history_clear_scope(scope_id);
	for (int i = 0; i < AGENT_FILE_QUERY_CACHE_MAX; i++)
		if (agent_file_query_cache[i].valid &&
		    agent_file_query_cache[i].scope_id == scope_id)
			memset(&agent_file_query_cache[i], 0,
			       sizeof(agent_file_query_cache[i]));
	agent_observe_scope_reclaim(scope_id);
	enabled = agent_edit_lock();
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_file_edits[i].active &&
		    agent_file_edits[i].scope_id == scope_id)
			memset(&agent_file_edits[i], 0,
			       sizeof(agent_file_edits[i]));
	for (int i = 0; i < AGENT_FILE_DIGEST_CACHE_MAX; i++)
		if (agent_file_digest_cache[i].valid &&
		    agent_file_digest_cache[i].scope_id == scope_id)
			memset(&agent_file_digest_cache[i], 0,
			       sizeof(agent_file_digest_cache[i]));
	for (int i = 0; i < AGENT_FILE_VERSION_MAX; i++)
		if (agent_file_versions[i].used &&
		    agent_file_versions[i].scope_id == scope_id)
			memset(&agent_file_versions[i], 0,
			       sizeof(agent_file_versions[i]));
	agent_edit_unlock(enabled);
	if (metadata_available)
		agent_file_maintain(AGENT_FILE_CHANGE_STATUS |
				    AGENT_FILE_CHANGE_STAGE |
				    AGENT_FILE_CHANGE_KIND);
	files_status = preserve_files ? 0 : fs_reclaim_scope_files(scope_id);
	if (changed) {
		agent_metadata_store_mark_dirty(scope_id);
		// A quiesced scope cannot create another write burst. Do not hold its
		// identity and I/O owner for the interactive coalescing window.
		agent_metadata_store_expedite(scope_id);
	}
	persist_status = metadata_available &&
		(agent_metadata_store_scope_pending(scope_id) ||
		 agent_metadata_store_scope_busy(scope_id)) ? -1 : 0;
	if (metadata_available)
		agent_file_request_scan();
	result = files_status < 0 || persist_status < 0 ? -1 : 0;
out_txn:
	agent_metadata_txn_unlock();
	if (result == 0)
		agent_metadata_store_scope_retire(scope_id);
	return result;
}

static int agent_field_match(char *want, char *have)
{
	return want[0] == 0 ||
	       strncmp(want, have, AGENT_FILE_LOGICAL_SIZE) == 0;
}

static int agent_file_matches(uint scope_id, struct agent_file_query *q,
			      struct agent_file_meta *m)
{
	if (!m->used ||
	    !agent_object_scope_visible(scope_id, agent_file_scope(m)))
		return 0;
	if (!agent_field_match(q->physical_name, m->physical_name))
		return 0;
	if (!agent_field_match(q->logical_path, m->logical_path))
		return 0;
	if (!agent_field_match(q->project, m->project))
		return 0;
	if (!agent_field_match(q->workflow, m->workflow))
		return 0;
	if (!agent_field_match(q->run_id, m->run_id))
		return 0;
	if (!agent_field_match(q->stage, m->stage))
		return 0;
	if (!agent_field_match(q->kind, m->kind))
		return 0;
	if (!agent_field_match(q->status, m->status))
		return 0;
	if (q->summary_contains[0] &&
	    !agent_contains(m->summary, q->summary_contains))
		return 0;
	return 1;
}

static int agent_file_query_has_filter(struct agent_file_query *q)
{
	return q->physical_name[0] || q->logical_path[0] || q->project[0] ||
	       q->workflow[0] || q->run_id[0] || q->stage[0] ||
	       q->kind[0] || q->status[0] || q->summary_contains[0];
}

static int agent_file_query_cacheable(struct agent_file_query *q)
{
	/* Each completed scan mutation advances its owning scope generation. */
	return (q->flags & AGENT_FILE_QUERY_SCAN) == 0;
}

static int agent_file_query_key_equal(struct agent_file_query *a,
				      struct agent_file_query *b)
{
	return a->flags == b->flags && a->max_hits == b->max_hits &&
	       strncmp(a->physical_name, b->physical_name,
		       sizeof(a->physical_name)) == 0 &&
	       strncmp(a->logical_path, b->logical_path,
		       sizeof(a->logical_path)) == 0 &&
	       strncmp(a->project, b->project, sizeof(a->project)) == 0 &&
	       strncmp(a->workflow, b->workflow, sizeof(a->workflow)) == 0 &&
	       strncmp(a->run_id, b->run_id, sizeof(a->run_id)) == 0 &&
	       strncmp(a->stage, b->stage, sizeof(a->stage)) == 0 &&
	       strncmp(a->kind, b->kind, sizeof(a->kind)) == 0 &&
	       strncmp(a->status, b->status, sizeof(a->status)) == 0 &&
	       strncmp(a->summary_contains, b->summary_contains,
		       sizeof(a->summary_contains)) == 0;
}

static int agent_file_query_cache_lookup(uint scope_id,
					 struct agent_file_query *key,
					 struct agent_file_query_result *r,
					 int *hit_slots)
{
	struct agent_file_query_cache_entry *e;

	for (int i = 0; i < AGENT_FILE_QUERY_CACHE_MAX; i++) {
		e = &agent_file_query_cache[i];
		if (!e->valid)
			continue;
		if (e->scope_id != scope_id)
			continue;
		if (e->fs_generation !=
		    agent_file_scope_generation(scope_id))
			continue;
		if (!agent_file_query_key_equal(&e->key, key))
			continue;
		memmove(r, &e->result, sizeof(*r));
		memmove(hit_slots, e->hit_slots, sizeof(e->hit_slots));
		r->plan_reason |= AGENT_FILE_QUERY_REASON_CACHE_HIT;
		r->query_ticks = 0;
		return 1;
	}
	return 0;
}

static void agent_file_query_cache_store(uint scope_id,
					 struct agent_file_query *key,
					 struct agent_file_query_result *r,
					 int *hit_slots)
{
	struct agent_file_query_cache_entry *e;

	if (r->total_hits <= 0 || agent_file_scan_active)
		return;
	e = &agent_file_query_cache[agent_file_query_cache_head %
				    AGENT_FILE_QUERY_CACHE_MAX];
	agent_file_query_cache_head =
		(agent_file_query_cache_head + 1) %
		AGENT_FILE_QUERY_CACHE_MAX;
	memset(e, 0, sizeof(*e));
	e->valid = 1;
	e->scope_id = scope_id;
	e->fs_generation = agent_file_scope_generation(scope_id);
	memmove(&e->key, key, sizeof(e->key));
	memmove(&e->result, r, sizeof(e->result));
	memmove(e->hit_slots, hit_slots, sizeof(e->hit_slots));
}

static void agent_file_make_hit(struct agent_file_hit *hit,
				struct agent_file_meta *m)
{
	struct agent_file_meta snapshot;
	uint scope_id = agent_file_scope(m);
	int enabled;

	snapshot = *m;
	enabled = agent_edit_lock();
	agent_file_overlay_published_size_locked(&snapshot, scope_id);
	agent_edit_unlock(enabled);
	m = &snapshot;
	memset(hit, 0, sizeof(*hit));
	hit->fid = m->fid;
	safestrcpy(hit->physical_name, m->physical_name,
		   sizeof(hit->physical_name));
	safestrcpy(hit->logical_path, m->logical_path,
		   sizeof(hit->logical_path));
	safestrcpy(hit->stage, m->stage, sizeof(hit->stage));
	safestrcpy(hit->kind, m->kind, sizeof(hit->kind));
	safestrcpy(hit->status, m->status, sizeof(hit->status));
	safestrcpy(hit->summary, m->summary, sizeof(hit->summary));
	hit->dependency_mask = m->dependency_mask;
	hit->dev = m->dev;
	hit->inum = m->inum;
	hit->incarnation = m->incarnation;
	hit->size = m->size;
	hit->fs_generation = m->fs_generation;
}

static int agent_file_query_internal(uint scope_id,
				     struct agent_file_query *q,
				     struct agent_file_query_result *r,
				     int *hit_slots)
{
	int cursor = -1;
	int *next = 0;
	int use_index = 0;
	int bucket = -1;
	int max_hits;
	uint64 start;
	uint64 reason = 0;
	struct agent_file_query key;
	int result;

	memset(r, 0, sizeof(*r));
	for (int i = 0; i < AGENT_FILE_QUERY_MAX_HITS; i++)
		hit_slots[i] = -1;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	if (!agent_scope_valid(scope_id)) {
		result = AGENT_STATUS_DENIED;
		goto out_txn;
	}
	r->plan = AGENT_FILE_QUERY_PLAN_SCAN;
	r->index_bucket = -1;
	if (agent_metadata_store_load() < 0) {
		result = AGENT_STATUS_NO_SPACE;
		goto out_txn;
	}
	max_hits = q->max_hits;
	if (max_hits <= 0 || max_hits > AGENT_FILE_QUERY_MAX_HITS)
		max_hits = AGENT_FILE_QUERY_MAX_HITS;
	memmove(&key, q, sizeof(key));
	key.max_hits = max_hits;
	if (agent_file_query_cacheable(&key) &&
	    agent_file_query_cache_lookup(scope_id, &key, r, hit_slots)) {
		result = r->returned;
		goto out_txn;
	}
	start = agent_ticks();
	if (q->flags & AGENT_FILE_QUERY_SCAN) {
		reason |= AGENT_FILE_QUERY_REASON_FORCED_SCAN;
	} else if (q->flags & AGENT_FILE_QUERY_USE_INDEX) {
		if (q->status[0]) {
			bucket = agent_bucket(q->status);
			cursor = agent_file_status_head[bucket];
			next = agent_file_status_next;
			use_index = 1;
			r->plan = AGENT_FILE_QUERY_PLAN_STATUS_INDEX;
			reason |= AGENT_FILE_QUERY_REASON_STATUS_INDEX;
		} else if (q->stage[0]) {
			bucket = agent_bucket(q->stage);
			cursor = agent_file_stage_head[bucket];
			next = agent_file_stage_next;
			use_index = 1;
			r->plan = AGENT_FILE_QUERY_PLAN_STAGE_INDEX;
			reason |= AGENT_FILE_QUERY_REASON_STAGE_INDEX;
		} else if (q->kind[0]) {
			bucket = agent_bucket(q->kind);
			cursor = agent_file_kind_head[bucket];
			next = agent_file_kind_next;
			use_index = 1;
			r->plan = AGENT_FILE_QUERY_PLAN_KIND_INDEX;
			reason |= AGENT_FILE_QUERY_REASON_KIND_INDEX;
		} else {
			reason |= AGENT_FILE_QUERY_REASON_NO_INDEX_KEY;
		}
	} else {
		reason |= AGENT_FILE_QUERY_REASON_INDEX_OFF;
	}
	if (use_index) {
		r->index_bucket = bucket;
		for (int i = cursor; i >= 0; i = next[i]) {
			agent_metadata_txn_work_charge(1);
			if (!agent_object_scope_visible(scope_id,
						agent_file_scopes[i]))
				continue;
			r->scanned_records++;
			if (agent_file_matches(scope_id, q, &agent_files[i])) {
				r->total_hits++;
				if (r->returned < max_hits) {
					hit_slots[r->returned] = i;
					agent_file_make_hit(
						&r->hits[r->returned++],
						&agent_files[i]);
				} else {
					r->truncated = 1;
				}
			}
		}
	} else {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			agent_metadata_txn_work_charge(1);
			if (!agent_files[i].used ||
			    !agent_object_scope_visible(
				    scope_id, agent_file_scopes[i]))
				continue;
			r->scanned_records++;
			if (agent_file_matches(scope_id, q, &agent_files[i])) {
				r->total_hits++;
				if (r->returned < max_hits) {
					hit_slots[r->returned] = i;
					agent_file_make_hit(
						&r->hits[r->returned++],
						&agent_files[i]);
				} else {
					r->truncated = 1;
				}
			}
		}
	}
	r->used_index = use_index;
	r->candidate_records = r->scanned_records;
	r->query_ticks = agent_ticks() - start;
	r->plan_reason = reason;
	r->fs_generation = agent_file_scope_generation(scope_id);
	if (agent_file_query_cacheable(&key))
		agent_file_query_cache_store(scope_id, &key, r, hit_slots);
	result = r->returned;
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

static uint64 agent_label_bit(char *label)
{
	uint64 hash = 1469598103934665603ULL;
	int bit;

	if (label == 0 || label[0] == 0)
		return 0;
	for (int i = 0; label[i] && i < AGENT_FILE_FIELD_SIZE; i++) {
		hash ^= (unsigned char)label[i];
		hash *= 1099511628211ULL;
	}
	bit = hash % 60;
	return 1ULL << bit;
}

static int agent_key_is(char *key, char *want)
{
	return strncmp(key, want, AGENT_FILE_FIELD_SIZE) == 0;
}

static int agent_dependency_scope_count(uint scope_id)
{
	int count = 0;

	for (int i = 0; i < AGENT_DEPENDENCY_MAX; i++) {
		if (agent_dependencies[i].used &&
		    agent_dependencies[i].scope_id == scope_id)
			count++;
		agent_metadata_txn_work_charge(1);
	}
	return count;
}

static int agent_dependency_update_from_payload(uint scope_id, char *payload,
						struct agent_result *res)
{
	struct agent_dependency_entry dep;
	char key[AGENT_FILE_FIELD_SIZE];
	char val[AGENT_FILE_SUMMARY_SIZE];
	int free_slot = -1;
	int slot = -1;
	int i = 0;
	int k;
	int v;

	memset(&dep, 0, sizeof(dep));
	if (!agent_scope_valid(scope_id))
		return AGENT_STATUS_DENIED;
	dep.used = 1;
	dep.scope_id = scope_id;
	dep.flags = AGENT_DEPENDENCY_F_USER;
	safestrcpy(dep.relation, "depends_on", sizeof(dep.relation));

	while (payload[i]) {
		while (payload[i] == ' ' || payload[i] == ';' ||
		       payload[i] == ',')
			i++;
		if (!payload[i])
			break;
		k = 0;
		memset(key, 0, sizeof(key));
		while (payload[i] && payload[i] != '=' &&
		       payload[i] != ':' && payload[i] != ';' &&
		       payload[i] != ',' && k < (int)sizeof(key) - 1)
			key[k++] = payload[i++];
		if (payload[i] != '=' && payload[i] != ':')
			return AGENT_STATUS_BAD_PARAM;
		i++;
		v = 0;
		memset(val, 0, sizeof(val));
		while (payload[i] && payload[i] != ';' &&
		       payload[i] != ',' && v < (int)sizeof(val) - 1)
			val[v++] = payload[i++];
		if (agent_key_is(key, "source") || agent_key_is(key, "from") ||
		    agent_key_is(key, "label"))
			safestrcpy(dep.source, val, sizeof(dep.source));
		else if (agent_key_is(key, "target") || agent_key_is(key, "to"))
			safestrcpy(dep.target, val, sizeof(dep.target));
		else if (agent_key_is(key, "namespace") ||
			 agent_key_is(key, "project"))
			safestrcpy(dep.namespace, val, sizeof(dep.namespace));
		else if (agent_key_is(key, "run_id") || agent_key_is(key, "run"))
			safestrcpy(dep.run_id, val, sizeof(dep.run_id));
		else if (agent_key_is(key, "relation"))
			safestrcpy(dep.relation, val, sizeof(dep.relation));
		else if (agent_key_is(key, "summary"))
			safestrcpy(dep.summary, val, sizeof(dep.summary));
		else
			return AGENT_STATUS_BAD_PARAM;
	}

	if (!dep.source[0] || !dep.target[0])
		return AGENT_STATUS_BAD_PARAM;
	if (!dep.summary[0])
		safestrcpy(dep.summary, dep.target, sizeof(dep.summary));

	for (int d = 0; d < AGENT_DEPENDENCY_MAX; d++) {
		if (!agent_dependencies[d].used) {
			if (free_slot < 0)
				free_slot = d;
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (agent_dependencies[d].scope_id == scope_id &&
		    strncmp(agent_dependencies[d].namespace, dep.namespace,
			    sizeof(dep.namespace)) == 0 &&
		    strncmp(agent_dependencies[d].run_id, dep.run_id,
			    sizeof(dep.run_id)) == 0 &&
		    strncmp(agent_dependencies[d].source, dep.source,
			    sizeof(dep.source)) == 0 &&
		    strncmp(agent_dependencies[d].target, dep.target,
			    sizeof(dep.target)) == 0) {
			slot = d;
			agent_metadata_txn_work_charge(1);
			break;
		}
		agent_metadata_txn_work_charge(1);
	}
	if (slot < 0)
		slot = free_slot;
	if (slot >= 0 && !agent_dependencies[slot].used &&
	    agent_dependency_scope_count(scope_id) >=
		    AGENT_DEPENDENCY_SCOPE_LIMIT)
		return AGENT_STATUS_NO_SPACE;
	if (slot < 0)
		return AGENT_STATUS_NO_SPACE;

	memmove(&agent_dependencies[slot], &dep, sizeof(dep));
	agent_dependency_generation++;
	res->value0 = agent_dependency_generation;
	res->value1 = agent_label_bit(dep.source);
	res->value2 = agent_label_bit(dep.target);
	agent_result_text(res, "dependency_updated");
	return AGENT_STATUS_OK;
}

static int agent_mask_count(uint64 mask)
{
	int count = 0;

	while (mask) {
		count += mask & 1;
		mask >>= 1;
	}
	return count;
}

static int agent_file_find_fid(uint scope_id, int fid)
{
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_txn_work_charge(1);
		if (agent_files[i].used &&
		    agent_file_scopes[i] == scope_id &&
		    agent_files[i].fid == fid)
			return i;
	}
	return -1;
}

static int agent_file_hit_slot_valid(uint scope_id, int slot,
				     struct agent_file_hit *hit)
{
	if (hit == 0 || slot < 0 || slot >= AGENT_FILE_META_MAX)
		return 0;
	return agent_files[slot].used &&
	       agent_object_scope_visible(scope_id, agent_file_scopes[slot]) &&
	       agent_files[slot].dev == hit->dev &&
	       agent_files[slot].inum == hit->inum &&
	       agent_files[slot].incarnation == hit->incarnation &&
	       agent_files[slot].fid == hit->fid;
}

static int agent_file_prefetch_count_stage(struct agent_file_meta *source,
					   char *stage)
{
	int bucket;
	int count = 0;

	if (!stage[0])
		return 0;
	bucket = agent_bucket(stage);
	for (int i = agent_file_stage_head[bucket]; i >= 0;
	     i = agent_file_stage_next[i]) {
		agent_metadata_txn_work_charge(1);
		if (!agent_files[i].used ||
		    agent_file_scope(source) != agent_file_scopes[i])
			continue;
		if (strncmp(agent_files[i].stage, stage,
			    sizeof(agent_files[i].stage)) != 0)
			continue;
		if (source->project[0] &&
		    strncmp(agent_files[i].project, source->project,
			    sizeof(agent_files[i].project)) != 0)
			continue;
		if (source->workflow[0] &&
		    strncmp(agent_files[i].workflow, source->workflow,
			    sizeof(agent_files[i].workflow)) != 0)
			continue;
		if (source->run_id[0] &&
		    strncmp(agent_files[i].run_id, source->run_id,
			    sizeof(agent_files[i].run_id)) != 0)
			continue;
		count++;
	}
	return count;
}

static void agent_file_prefetch_store(struct proc *p,
				      struct agent_file_meta *source,
				      struct agent_file_meta *target,
				      uint64 source_sequence, uint64 reason,
				      int source_pid, uint64 span_id,
				      uint64 span_owner, int candidates)
{
	struct agent_file_prefetch_hint *hint;
	int slot;
	int visible;

	if (!p || !p->is_agent || !source || !source->used || !target ||
	    !target->used || agent_identity_proc_scope(p) != agent_file_scope(source) ||
	    agent_file_scope(source) != agent_file_scope(target))
		return;
	if (span_id == 0) {
		span_id = p->agent_current_span_id;
		span_owner = p->agent_current_span_owner;
	}
	if (span_id == 0 || span_owner == 0)
		return;
	for (int i = 0; i < p->agent_prefetch_count; i++) {
		slot = (p->agent_prefetch_head +
			AGENT_FILE_PREFETCH_MAX_HINTS -
			p->agent_prefetch_count + i) %
		       AGENT_FILE_PREFETCH_MAX_HINTS;
		agent_metadata_txn_work_charge(1);
		if (p->agent_prefetch_hints[slot].fid == target->fid)
			goto fill;
	}
	slot = p->agent_prefetch_head % AGENT_FILE_PREFETCH_MAX_HINTS;
	p->agent_prefetch_head =
		(p->agent_prefetch_head + 1) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	if (p->agent_prefetch_count < AGENT_FILE_PREFETCH_MAX_HINTS)
		p->agent_prefetch_count++;

fill:
	hint = &p->agent_prefetch_hints[slot];
	memset(hint, 0, sizeof(*hint));
	hint->sequence = ++p->agent_prefetch_sequence;
	hint->source_sequence = source_sequence;
	hint->span_id = span_id;
	p->agent_prefetch_span_owner[slot] = span_owner;
	hint->reason = reason;
	hint->tick = agent_ticks();
	hint->fs_generation = agent_file_scope_generation(
		agent_identity_proc_scope(p));
	hint->fid = target->fid;
	hint->source_fid = source->fid;
	hint->source_pid = source_pid ? source_pid : p->pid;
	hint->target_pid = p->pid;
	hint->plan = AGENT_FILE_QUERY_PLAN_STAGE_INDEX;
	hint->candidate_records = candidates;
	hint->total_hits = candidates;
	hint->score = 1000 + candidates;
	if (strncmp(target->status, "pending", AGENT_FILE_FIELD_SIZE) == 0) {
		hint->reason |= AGENT_FILE_PREFETCH_REASON_PENDING;
		hint->score += 100;
	}
	if (target->stage[0]) {
		hint->reason |= AGENT_FILE_PREFETCH_REASON_STAGE_INDEX;
		hint->score += 50;
	}
	visible = p->agent_prefetch_count;
	if (visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	hint->score += visible;
	agent_file_make_hit(&hint->hit, target);
	agent_observe_record_prefetch(
		p, hint, span_owner, target->stage,
		(reason & AGENT_FILE_PREFETCH_REASON_HANDOFF) == 0);
	/*
	 * Audit allocation and observe fan-out intentionally remain atomic: they
	 * are shared outside the metadata gate. Charge their fixed upper bound
	 * only after publication, so the next hint cannot amplify that work
	 * without crossing a scheduler checkpoint.
	 */
	agent_metadata_txn_work_charge(
		(reason & AGENT_FILE_PREFETCH_REASON_HANDOFF) ?
			2U * NPROC :
			2U * AGENT_AUDIT_MAX_RECORDS + 5U * NPROC);
}

static void agent_file_prefetch_update(struct proc *p,
				       struct agent_file_query *q,
				       struct agent_file_query_result *r,
				       int *hit_slots,
				       uint64 source_sequence)
{
	uint64 fallback_targets[AGENT_FILE_QUERY_MAX_HITS]
			       [(AGENT_FILE_META_MAX + 63) / 64];
	uint64 selected_targets[(AGENT_FILE_META_MAX + 63) / 64];
	uint64 explicit_target_masks[AGENT_FILE_QUERY_MAX_HITS];
	int dependency_slots[AGENT_FILE_QUERY_MAX_HITS]
			    [AGENT_DEPENDENCY_SCOPE_LIMIT];
	int dependency_counts[AGENT_FILE_QUERY_MAX_HITS];
	int source_slots[AGENT_FILE_QUERY_MAX_HITS];
	int selected_source_slots[AGENT_FILE_PREFETCH_MAX_HINTS];
	int selected_target_slots[AGENT_FILE_PREFETCH_MAX_HINTS];
	struct agent_file_meta *source;
	struct agent_file_meta *target;
	uint64 target_bit;
	uint64 reason;
	uint scope_id;
	int source_count;
	int selected_count = 0;
	int explicit_found[AGENT_FILE_QUERY_MAX_HITS];

	if (!p || !p->is_agent || !q || !r || !hit_slots ||
	    r->returned <= 0)
		return;
	if (!agent_metadata_txn_lock(1))
		return;
	scope_id = agent_identity_proc_scope(p);
	source_count = r->returned;
	if (source_count > AGENT_FILE_QUERY_MAX_HITS)
		source_count = AGENT_FILE_QUERY_MAX_HITS;
	memset(fallback_targets, 0, sizeof(fallback_targets));
	memset(selected_targets, 0, sizeof(selected_targets));
	memset(explicit_target_masks, 0, sizeof(explicit_target_masks));
	memset(dependency_slots, 0, sizeof(dependency_slots));
	memset(dependency_counts, 0, sizeof(dependency_counts));
	memset(source_slots, -1, sizeof(source_slots));
	memset(explicit_found, 0, sizeof(explicit_found));

	/*
	 * Query execution records the exact slots behind each returned hit,
	 * including cache hits. Validate those O(1) identities here instead of
	 * rescanning the global file table once per hit.
	 */
	for (int h = 0; h < source_count; h++)
		if (agent_file_hit_slot_valid(scope_id, hit_slots[h],
					     &r->hits[h]) &&
		    agent_file_scopes[hit_slots[h]] == scope_id)
			source_slots[h] = hit_slots[h];

	/*
	 * Build a fixed, scope-quota-bounded selector set once. The hash mask is
	 * only a prefilter; exact stage/namespace/run comparisons below preserve
	 * dependency semantics even when label hashes collide.
	 */
	for (int d = 0; d < AGENT_DEPENDENCY_MAX; d++) {
		if (!agent_dependencies[d].used ||
		    agent_dependencies[d].scope_id != scope_id)
			goto next_dependency;
		for (int h = 0; h < source_count; h++) {
			if (source_slots[h] < 0)
				continue;
			source = &agent_files[source_slots[h]];
			if (strncmp(agent_dependencies[d].source, source->stage,
				    sizeof(agent_dependencies[d].source)) != 0)
				continue;
			if (agent_dependencies[d].namespace[0] &&
			    strncmp(agent_dependencies[d].namespace,
				    source->project,
				    sizeof(agent_dependencies[d].namespace)) != 0)
				continue;
			if (agent_dependencies[d].run_id[0] &&
			    strncmp(agent_dependencies[d].run_id,
				    source->run_id,
				    sizeof(agent_dependencies[d].run_id)) != 0)
				continue;
			if (dependency_counts[h] >=
			    AGENT_DEPENDENCY_SCOPE_LIMIT)
				continue;
			dependency_slots[h][dependency_counts[h]++] = d;
			explicit_target_masks[h] |=
				agent_label_bit(agent_dependencies[d].target);
		}
next_dependency:
		agent_metadata_txn_work_charge(1);
	}

	/*
	 * Scan the file table exactly once. Explicit edges are selected
	 * immediately and fallback candidates are kept in per-source bitmaps
	 * until we know that no explicit target exists. A target slot can be
	 * published only once and side effects are capped by the hint ring.
	 */
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used || agent_file_scopes[i] != scope_id ||
		    agent_files[i].stage[0] == 0)
			goto next_target;
		target = &agent_files[i];
		target_bit = agent_label_bit(target->stage);
		for (int h = 0; h < source_count; h++) {
			int explicit_match = 0;
			int source_slot = source_slots[h];

			if (source_slot < 0 || source_slot == i)
				goto next_source;
			source = &agent_files[source_slot];
			if ((explicit_target_masks[h] & target_bit) != 0)
				for (int k = 0; k < dependency_counts[h]; k++) {
					struct agent_dependency_entry *dep =
						&agent_dependencies[
							dependency_slots[h][k]];

					agent_metadata_txn_work_charge(1);
					if (strncmp(target->stage, dep->target,
						    sizeof(target->stage)) != 0)
						continue;
					if (dep->namespace[0] &&
					    strncmp(target->project,
						    dep->namespace,
						    sizeof(target->project)) != 0)
						continue;
					if (dep->run_id[0] &&
					    strncmp(target->run_id, dep->run_id,
						    sizeof(target->run_id)) != 0)
						continue;
					if (source->workflow[0] &&
					    strncmp(target->workflow,
						    source->workflow,
						    sizeof(target->workflow)) != 0)
						continue;
					explicit_match = 1;
					break;
				}
			if (explicit_match) {
				explicit_found[h] = 1;
				if (selected_count <
					    AGENT_FILE_PREFETCH_MAX_HINTS &&
				    (selected_targets[i / 64] &
				     (1ULL << (i % 64))) == 0) {
					selected_targets[i / 64] |=
						1ULL << (i % 64);
					selected_source_slots[selected_count] =
						source_slot;
					selected_target_slots[selected_count++] = i;
				}
			}
			if (target_bit != 0 &&
			    (source->dependency_mask & target_bit) != 0 &&
			    (!source->project[0] ||
			     strncmp(source->project, target->project,
				     sizeof(source->project)) == 0) &&
			    (!source->workflow[0] ||
			     strncmp(source->workflow, target->workflow,
				     sizeof(source->workflow)) == 0) &&
			    (!source->run_id[0] ||
			     strncmp(source->run_id, target->run_id,
				     sizeof(source->run_id)) == 0))
				fallback_targets[h][i / 64] |=
					1ULL << (i % 64);
next_source:
			agent_metadata_txn_work_charge(1);
		}
next_target:
		agent_metadata_txn_work_charge(1);
	}

	for (int h = 0; h < source_count &&
				selected_count < AGENT_FILE_PREFETCH_MAX_HINTS; h++) {
		if (source_slots[h] < 0 || explicit_found[h])
			continue;
		for (int word = 0;
		     word < (AGENT_FILE_META_MAX + 63) / 64 &&
			     selected_count < AGENT_FILE_PREFETCH_MAX_HINTS;
		     word++) {
			uint64 bits = fallback_targets[h][word];

			agent_metadata_txn_work_charge(1);
			for (int bit = 0; bit < 64 &&
				     selected_count <
					     AGENT_FILE_PREFETCH_MAX_HINTS;
			     bit++) {
				int slot = word * 64 + bit;

				if (slot >= AGENT_FILE_META_MAX ||
				    (bits & (1ULL << bit)) == 0 ||
				    (selected_targets[word] &
				     (1ULL << bit)) != 0)
					continue;
				selected_targets[word] |= 1ULL << bit;
				selected_source_slots[selected_count] =
					source_slots[h];
				selected_target_slots[selected_count++] = slot;
			}
		}
	}

	reason = AGENT_FILE_PREFETCH_REASON_DEPENDENCY |
		 AGENT_FILE_PREFETCH_REASON_SAME_RUN;
	if ((q->flags & AGENT_FILE_QUERY_USE_INDEX) || r->used_index)
		reason |= AGENT_FILE_PREFETCH_REASON_STAGE_INDEX;
	for (int i = 0; i < selected_count; i++) {
		int candidates;

		source = &agent_files[selected_source_slots[i]];
		target = &agent_files[selected_target_slots[i]];
		candidates = agent_file_prefetch_count_stage(source,
						     target->stage);
		agent_file_prefetch_store(
			p, source, target, source_sequence, reason, p->pid,
			p->agent_current_span_id, p->agent_current_span_owner,
			candidates);
	}
	agent_metadata_txn_unlock();
}

static int
agent_file_prefetch_handoff(struct agent_endpoint_handle *target_handle,
			    struct agent_endpoint_handle *source_handle)
{
	struct agent_file_prefetch_hint source_hint;
	struct agent_file_prefetch_hint published;
	struct agent_file_meta *source_meta;
	struct agent_file_meta *target_meta;
	struct proc *source;
	struct proc *target;
	uint64 reason;
	uint64 span_id;
	uint64 span_owner;
	int source_pid;
	int candidates;
	int visible;
	int start;
	int slot;
	int source_slot;
	int target_slot;
	int copied = 0;
	int enabled;

	if (target_handle == 0 || source_handle == 0 ||
	    target_handle->slot == source_handle->slot ||
	    target_handle->scope_id != source_handle->scope_id ||
	    !agent_scope_valid(source_handle->scope_id))
		return 0;
	if (!agent_metadata_txn_lock(0))
		return 0;

	enabled = intr_save();
	source = agent_ipc_endpoint_resolve_locked(source_handle);
	target = agent_ipc_endpoint_resolve_locked(target_handle);
	if (source == 0 || target == 0) {
		intr_restore(enabled);
		goto out;
	}
	visible = source->agent_prefetch_count;
	if (visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	start = (source->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
		 visible) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	intr_restore(enabled);

	for (int i = 0; i < visible; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_MAX_HINTS;
		/*
		 * Copy one bounded source record under a short endpoint check.
		 * No proc pointer survives the following budget checkpoints.
		 */
		enabled = intr_save();
		source = agent_ipc_endpoint_resolve_locked(source_handle);
		if (source == 0) {
			intr_restore(enabled);
			break;
		}
		memmove(&source_hint, &source->agent_prefetch_hints[slot],
			sizeof(source_hint));
		source_pid = source_hint.source_pid ?
				     source_hint.source_pid : source_handle->pid;
		if (source_hint.span_id) {
			span_id = source_hint.span_id;
			span_owner = source->agent_prefetch_span_owner[slot];
		} else {
			span_id = source->agent_current_span_id;
			span_owner = source->agent_current_span_owner;
		}
		intr_restore(enabled);

		agent_metadata_txn_work_charge(1);
		if (span_id == 0 || span_owner == 0)
			continue;
		source_slot = agent_file_find_fid(source_handle->scope_id,
						 source_hint.source_fid);
		target_slot = agent_file_find_fid(source_handle->scope_id,
						 source_hint.fid);
		if (source_slot < 0 || target_slot < 0)
			continue;
		source_meta = &agent_files[source_slot];
		target_meta = &agent_files[target_slot];
		if (!source_meta->used || !target_meta->used ||
		    agent_file_scope(source_meta) != source_handle->scope_id ||
		    agent_file_scope(target_meta) != source_handle->scope_id)
			continue;

		reason = source_hint.reason |
			 AGENT_FILE_PREFETCH_REASON_HANDOFF;
		candidates = source_hint.candidate_records > 0 ?
				     source_hint.candidate_records : 1;
		memset(&published, 0, sizeof(published));
		published.source_sequence = source_hint.source_sequence;
		published.span_id = span_id;
		published.reason = reason;
		published.fs_generation = agent_file_scope_generation(
			source_handle->scope_id);
		published.fid = target_meta->fid;
		published.source_fid = source_meta->fid;
		published.source_pid = source_pid;
		published.target_pid = target_handle->pid;
		published.plan = AGENT_FILE_QUERY_PLAN_STAGE_INDEX;
		published.candidate_records = candidates;
		published.total_hits = candidates;
		published.score = 1000 + candidates;
		if (strncmp(target_meta->status, "pending",
			    AGENT_FILE_FIELD_SIZE) == 0) {
			published.reason |= AGENT_FILE_PREFETCH_REASON_PENDING;
			published.score += 100;
		}
		if (target_meta->stage[0]) {
			published.reason |=
				AGENT_FILE_PREFETCH_REASON_STAGE_INDEX;
			published.score += 50;
		}
		agent_file_make_hit(&published.hit, target_meta);

		/*
		 * Prepay every fixed-size publication scan. The following
		 * endpoint revalidation and commit cannot schedule.
		 */
		agent_metadata_txn_work_charge(
			2U * AGENT_FILE_PREFETCH_SPAN_MAX +
			2U * AGENT_AUDIT_MAX_RECORDS + 5U * NPROC);
		enabled = intr_save();
		target = agent_ipc_endpoint_resolve_locked(target_handle);
		if (target == 0) {
			intr_restore(enabled);
			continue;
		}
		for (int j = 0; j < target->agent_prefetch_count; j++) {
			slot = (target->agent_prefetch_head +
				AGENT_FILE_PREFETCH_MAX_HINTS -
				target->agent_prefetch_count + j) %
			       AGENT_FILE_PREFETCH_MAX_HINTS;
			if (target->agent_prefetch_hints[slot].fid ==
			    published.fid)
				goto fill;
		}
		slot = target->agent_prefetch_head %
		       AGENT_FILE_PREFETCH_MAX_HINTS;
		target->agent_prefetch_head =
			(target->agent_prefetch_head + 1) %
			AGENT_FILE_PREFETCH_MAX_HINTS;
		if (target->agent_prefetch_count <
		    AGENT_FILE_PREFETCH_MAX_HINTS)
			target->agent_prefetch_count++;

fill:
		published.sequence = ++target->agent_prefetch_sequence;
		published.tick = agent_ticks();
		published.score += target->agent_prefetch_count;
		memmove(&target->agent_prefetch_hints[slot], &published,
			sizeof(published));
		target->agent_prefetch_span_owner[slot] = span_owner;
		agent_observe_record_prefetch_handoff_locked(
			source_handle->pid, source_handle->control_id, target,
			&published, span_owner, target_meta->stage,
			published.reason);
		intr_restore(enabled);
		copied++;
	}
out:
	agent_metadata_txn_unlock();
	return copied;
}

int agent_metadata_prefetch_handoff(
	struct agent_endpoint_handle *target_handle,
	struct agent_endpoint_handle *source_handle)
{
	return agent_file_prefetch_handoff(target_handle, source_handle);
}

static int agent_file_find(uint scope_id, char *selector)
{
	agent_metadata_store_load();
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used || agent_file_scopes[i] != scope_id)
			continue;
		if (strncmp(selector, agent_files[i].physical_name,
			    sizeof(agent_files[i].physical_name)) == 0 ||
		    strncmp(selector, agent_files[i].logical_path,
			    sizeof(agent_files[i].logical_path)) == 0 ||
		    strncmp(selector, agent_files[i].stage,
			    sizeof(agent_files[i].stage)) == 0)
			return i;
	}
	return -1;
}

static int agent_file_digest_select(uint scope_id, char *selector,
				    char *physical, int n)
{
	struct agent_file_query query;
	int found;

	if (agent_text_empty(selector))
		return AGENT_STATUS_BAD_PARAM;
	memset(physical, 0, n);
	if (agent_contains(selector, "=") || agent_contains(selector, ":")) {
		if (agent_query_from_payload(&query, selector) < 0)
			return AGENT_STATUS_BAD_PARAM;
		if (!agent_file_query_has_filter(&query))
			return AGENT_STATUS_BAD_PARAM;
		agent_metadata_store_load();
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			if (!agent_files[i].used ||
			    agent_file_scopes[i] != scope_id)
				continue;
			if (agent_file_matches(scope_id, &query,
					       &agent_files[i])) {
				safestrcpy(physical,
					   agent_files[i].physical_name, n);
				return 0;
			}
		}
		return AGENT_STATUS_NOT_FOUND;
	}
	found = agent_file_find(scope_id, selector);
	if (found >= 0) {
		safestrcpy(physical, agent_files[found].physical_name, n);
		return 0;
	}
	safestrcpy(physical, selector, n);
	return 0;
}

static void agent_file_digest_preview(char *preview, int *pos, char c)
{
	if (*pos >= AGENT_FAST_RESULT_SIZE - 1)
		return;
	if (c == '\n' || c == '\r' || c == '\t')
		c = ' ';
	if (c < 32 || c > 126)
		c = '.';
	preview[*pos] = c;
	(*pos)++;
	preview[*pos] = 0;
}

static int agent_file_digest_cacheable(struct inode *ip)
{
	return ip != 0 && ip->agent_meta_slot > 0 &&
	       ip->agent_meta_version == AGENT_INODE_META_VERSION;
}

static int agent_file_digest_cache_lookup(struct inode *ip,
					  struct agent_result *res,
					  uint64 *content_generation)
{
	struct agent_file_digest_cache_entry *e;
	int found = 0;
	int enabled;

	enabled = agent_edit_lock();
	if (agent_file_content_version_locked(ip, content_generation, 1) < 0) {
		agent_file_digest_cache_misses++;
		agent_edit_unlock(enabled);
		return 0;
	}
	for (int i = 0; i < AGENT_FILE_DIGEST_CACHE_MAX; i++) {
		e = &agent_file_digest_cache[i];
		if (!e->valid)
			continue;
		if (e->dev != ip->dev || e->inum != ip->inum ||
		    e->incarnation != ip->vfs_incarnation ||
		    e->scope_id != ip->vfs_scope_id)
			continue;
		if (e->size != ip->size)
			continue;
		if (e->content_generation != *content_generation)
			continue;
		res->value0 = e->size;
		res->value1 = e->bytes;
		res->value2 = e->hash;
		agent_result_text(res, e->preview[0] ? e->preview :
						"empty_file");
		agent_file_digest_cache_hits++;
		found = 1;
		break;
	}
	if (!found)
		agent_file_digest_cache_misses++;
	agent_edit_unlock(enabled);
	return found;
}

static void agent_file_digest_cache_store(struct inode *ip,
					  uint64 expected_generation,
					  uint64 expected_size, uint64 bytes,
					  uint64 hash, char *preview)
{
	struct agent_file_digest_cache_entry *e;
	uint64 content_generation;
	int enabled;

	if (ip == 0 || ip->agent_meta_slot <= 0 ||
	    ip->agent_meta_version != AGENT_INODE_META_VERSION)
		return;
	enabled = agent_edit_lock();
	if (agent_file_content_version_locked(ip, &content_generation, 0) < 0 ||
	    content_generation != expected_generation ||
	    ip->size != expected_size) {
		agent_edit_unlock(enabled);
		return;
	}
	e = &agent_file_digest_cache[agent_file_digest_cache_head %
				     AGENT_FILE_DIGEST_CACHE_MAX];
	agent_file_digest_cache_head =
		(agent_file_digest_cache_head + 1) %
		AGENT_FILE_DIGEST_CACHE_MAX;
	memset(e, 0, sizeof(*e));
	e->valid = 1;
	e->scope_id = ip->vfs_scope_id;
	e->dev = ip->dev;
	e->inum = ip->inum;
	e->incarnation = ip->vfs_incarnation;
	e->size = expected_size;
	e->content_generation = content_generation;
	e->bytes = bytes;
	e->hash = hash;
	safestrcpy(e->preview, preview[0] ? preview : "empty_file",
		   sizeof(e->preview));
	agent_edit_unlock(enabled);
}

static void agent_file_digest_read(struct proc *p, char *selector,
				   struct agent_result *res)
{
	char physical[AGENT_FILE_NAME_SIZE];
	char preview[AGENT_FAST_RESULT_SIZE];
	char buf[AGENT_FILE_DIGEST_CHUNK];
	struct inode *ip;
	uint64 hash = 1469598103934665603ULL;
	uint64 content_generation = 0;
	uint64 digest_size;
	uint64 limit;
	uint64 total = 0;
	uint off = 0;
	int pos = 0;
	int rc;
	int cacheable;
	struct vfs_cred cred;

	rc = agent_file_digest_select(agent_identity_proc_scope(p), selector, physical,
				      sizeof(physical));
	if (rc < 0) {
		res->status = rc;
		agent_result_text(res, rc == AGENT_STATUS_NOT_FOUND ?
					   "digest_not_found" :
					   "bad_selector");
		return;
	}
	if (agent_file_is_meta_store_name(physical)) {
		res->status = AGENT_STATUS_DENIED;
		agent_result_text(res, "denied");
		return;
	}
	if ((ip = namei_scope(physical, VFS_POLICY_WORKFLOW,
			      agent_identity_proc_scope(p))) == 0) {
		res->status = AGENT_STATUS_NOT_FOUND;
		agent_result_text(res, "digest_not_found");
		return;
	}
	vfs_cred_from_proc(p, &cred);
	ivalid(ip);
	if (ip->type != T_FILE) {
		iput(ip);
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "not_file");
		return;
	}
	if (!vfs_inode_authorize(ip, &cred, VFS_OP_READ)) {
		iput(ip);
		res->status = AGENT_STATUS_DENIED;
		agent_result_text(res, "denied");
		return;
	}
	cacheable = agent_file_digest_cacheable(ip);
	if (cacheable && agent_file_digest_cache_lookup(
			 ip, res, &content_generation)) {
		iput(ip);
		return;
	}
	memset(preview, 0, sizeof(preview));
	digest_size = ip->size;
	limit = digest_size < AGENT_FILE_DIGEST_MAX_BYTES ?
			digest_size :
			AGENT_FILE_DIGEST_MAX_BYTES;
	while (total < limit) {
		uint want = MIN((uint)(limit - total),
				(uint)sizeof(buf));
		int got = readi(ip, &cred, 0, (uint64)buf, off, want);
		if (got < 0) {
			iput(ip);
			res->status = AGENT_STATUS_BAD_REQUEST;
			agent_result_text(res, "digest_read_error");
			return;
		}
		if (got == 0)
			break;
		for (int i = 0; i < got; i++) {
			hash ^= (unsigned char)buf[i];
			hash *= 1099511628211ULL;
			agent_file_digest_preview(preview, &pos, buf[i]);
		}
		total += got;
		off += got;
	}
	res->value0 = digest_size;
	res->value1 = total;
	res->value2 = hash;
	if (cacheable)
		agent_file_digest_cache_store(ip, content_generation,
					      digest_size, total, hash,
					      preview);
	iput(ip);
	agent_result_text(res, preview[0] ? preview : "empty_file");
}

static int agent_dependency_for_label(uint scope_id, char *label,
				      char *namespace, char *run_id,
				      uint64 *mask)
{
	uint64 found = 0;

	for (int i = 0; i < AGENT_DEPENDENCY_MAX; i++) {
		if (!agent_dependencies[i].used ||
		    agent_dependencies[i].scope_id != scope_id) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (strncmp(agent_dependencies[i].source, label,
			    sizeof(agent_dependencies[i].source)) != 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (namespace && namespace[0] &&
		    strncmp(agent_dependencies[i].namespace, namespace,
			    sizeof(agent_dependencies[i].namespace)) != 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (run_id && run_id[0] &&
		    strncmp(agent_dependencies[i].run_id, run_id,
			    sizeof(agent_dependencies[i].run_id)) != 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (agent_dependencies[i].target[0]) {
			found |= agent_label_bit(agent_dependencies[i].source);
			found |= agent_label_bit(agent_dependencies[i].target);
		}
		agent_metadata_txn_work_charge(1);
	}
	if (found) {
		*mask = found;
		return 0;
	}
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used || agent_file_scopes[i] != scope_id ||
		    agent_files[i].dependency_mask == 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (namespace && namespace[0] &&
		    strncmp(agent_files[i].project, namespace,
			    sizeof(agent_files[i].project)) != 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (run_id && run_id[0] &&
		    strncmp(agent_files[i].run_id, run_id,
			    sizeof(agent_files[i].run_id)) != 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (strncmp(agent_files[i].stage, label,
			    sizeof(agent_files[i].stage)) == 0 ||
		    strncmp(agent_files[i].physical_name, label,
			    sizeof(agent_files[i].physical_name)) == 0 ||
		    strncmp(agent_files[i].logical_path, label,
			    sizeof(agent_files[i].logical_path)) == 0)
			found |= agent_label_bit(agent_files[i].stage) |
				 agent_files[i].dependency_mask;
		agent_metadata_txn_work_charge(1);
	}
	if (found) {
		*mask = found;
		return 0;
	}
	return -1;
}

static void agent_stage_text(uint scope_id, uint64 mask, char *out, int n)
{
	int first = 1;
	uint64 bit;
	uint64 emitted = 0;

	memset(out, 0, n);
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used || agent_file_scopes[i] != scope_id ||
		    agent_files[i].stage[0] == 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		bit = agent_label_bit(agent_files[i].stage);
		if ((mask & bit) == 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if ((emitted & bit) != 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		emitted |= bit;
		if (!first)
			agent_text_append(out, n, "+");
		agent_text_append(out, n, agent_files[i].stage);
		first = 0;
		agent_metadata_txn_work_charge(1);
	}
	if (!out[0])
		safestrcpy(out, "none", n);
}

static int agent_action_seen(uint scope_id, int tool_id, char *project,
			     char *run_id,
			     char *stage, uint64 request_id)
{
	struct agent_action_history_entry *e;

	if (request_id == 0)
		return 0;
	for (int i = 0; i < agent_action_history_count; i++) {
		e = &agent_action_history[i];
		if (e->scope_id == scope_id && e->tool_id == tool_id &&
		    e->request_id == request_id &&
		    strncmp(e->project, project, sizeof(e->project)) == 0 &&
		    strncmp(e->run_id, run_id, sizeof(e->run_id)) == 0 &&
		    strncmp(e->stage, stage, sizeof(e->stage)) == 0)
			return 1;
	}
	return 0;
}

static void agent_action_remember(uint scope_id, int tool_id, char *project,
				  char *run_id,
				  char *stage, uint64 request_id)
{
	struct agent_action_history_entry *e;
	int owned = 0;
	int replace = -1;
	uint64 oldest = ~0ULL;

	if (request_id == 0)
		return;
	for (int i = 0; i < agent_action_history_count; i++)
		if (agent_action_history[i].scope_id == scope_id) {
			if (agent_action_history[i].sequence < oldest) {
				replace = i;
				oldest = agent_action_history[i].sequence;
			}
			owned++;
		}
	if (owned >= AGENT_ACTION_SCOPE_LIMIT) {
		e = &agent_action_history[replace];
	} else if (agent_action_history_count < AGENT_ACTION_HISTORY_MAX) {
		e = &agent_action_history[agent_action_history_count++];
	} else {
		return;
	}
	memset(e, 0, sizeof(*e));
	e->tool_id = tool_id;
	e->scope_id = scope_id;
	e->sequence = agent_action_next_sequence++;
	e->request_id = request_id;
	safestrcpy(e->project, project, sizeof(e->project));
	safestrcpy(e->run_id, run_id, sizeof(e->run_id));
	safestrcpy(e->stage, stage, sizeof(e->stage));
}

static void agent_action_history_clear_scope(uint scope_id)
{
	int out = 0;

	for (int i = 0; i < agent_action_history_count; i++) {
		if (agent_action_history[i].scope_id == scope_id)
			continue;
		if (out != i)
			agent_action_history[out] = agent_action_history[i];
		out++;
	}
	while (agent_action_history_count > out)
		memset(&agent_action_history[--agent_action_history_count], 0,
		       sizeof(agent_action_history[0]));
}

static void agent_text_append(char *dst, int n, char *src)
{
	int len;

	if (n <= 0 || src == 0)
		return;
	len = strlen(dst);
	if (len >= n - 1)
		return;
	safestrcpy(dst + len, src, n - len);
}

static void agent_file_event_payload(struct agent_file_meta *meta, char *out,
				     int n)
{
	memset(out, 0, n);
	if (meta->status[0]) {
		agent_text_append(out, n, "status=");
		agent_text_append(out, n, meta->status);
	}
	if (meta->stage[0]) {
		agent_text_append(out, n, ";stage=");
		agent_text_append(out, n, meta->stage);
	}
	if (meta->run_id[0]) {
		agent_text_append(out, n, ";run_id=");
		agent_text_append(out, n, meta->run_id);
	}
	if (meta->project[0]) {
		agent_text_append(out, n, ";project=");
		agent_text_append(out, n, meta->project);
	}
	if (!out[0])
		safestrcpy(out, "status=changed", n);
}

static int agent_parse_selector(char *payload, char *stage, int stage_n,
				char *project, int project_n, char *run_id,
				int run_id_n)
{
	struct agent_file_query query;

	memset(stage, 0, stage_n);
	memset(project, 0, project_n);
	memset(run_id, 0, run_id_n);
	if (agent_contains(payload, "=") || agent_contains(payload, ":")) {
		if (agent_query_from_payload(&query, payload) < 0)
			return -1;
		safestrcpy(stage, query.stage, stage_n);
		safestrcpy(project, query.project, project_n);
		safestrcpy(run_id, query.run_id, run_id_n);
	} else {
		safestrcpy(stage, payload, stage_n);
	}
	return 0;
}

static int agent_file_update_status_batch(uint scope_id, char *stage,
					  char *project, char *run_id,
					  char *status, char *summary,
					  uint64 dependency_mask,
					  int propagate_dependencies)
{
	uchar selected[(AGENT_FILE_META_MAX + 7) / 8];
	uchar primary[(AGENT_FILE_META_MAX + 7) / 8];
	int persistent_updated = 0;
	int primary_updated = 0;
	int updated = 0;

	memset(selected, 0, sizeof(selected));
	memset(primary, 0, sizeof(primary));
	if (!agent_metadata_txn_lock(1))
		return 0;
	if (agent_metadata_store_load() < 0)
		goto out_txn;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used || agent_file_scopes[i] != scope_id) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		if (strncmp(agent_files[i].stage, stage,
			    sizeof(agent_files[i].stage)) == 0 &&
		    (!project[0] ||
		     strncmp(agent_files[i].project, project,
			     sizeof(agent_files[i].project)) == 0) &&
		    (!run_id[0] ||
		     strncmp(agent_files[i].run_id, run_id,
			     sizeof(agent_files[i].run_id)) == 0)) {
			selected[i / 8] |= 1U << (i % 8);
			primary[i / 8] |= 1U << (i % 8);
			primary_updated++;
		}
		agent_metadata_txn_work_charge(1);
	}
	if (primary_updated == 0)
		goto out_txn;
	if (propagate_dependencies && dependency_mask != 0)
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			uint64 target_bit;

			if (!agent_files[i].used ||
			    agent_file_scopes[i] != scope_id)
				goto next_dependency;
			if (project[0] &&
			    strncmp(agent_files[i].project, project,
				    sizeof(agent_files[i].project)) != 0)
				goto next_dependency;
			if (run_id[0] &&
			    strncmp(agent_files[i].run_id, run_id,
				    sizeof(agent_files[i].run_id)) != 0)
				goto next_dependency;
			target_bit = agent_label_bit(agent_files[i].stage);
			if (dependency_mask & target_bit)
				selected[i / 8] |= 1U << (i % 8);
next_dependency:
			agent_metadata_txn_work_charge(1);
		}
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if ((selected[i / 8] & (1U << (i % 8))) == 0) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		safestrcpy(agent_files[i].status, status,
			   sizeof(agent_files[i].status));
		if (primary[i / 8] & (1U << (i % 8))) {
			if (summary && summary[0])
				safestrcpy(agent_files[i].summary, summary,
					   sizeof(agent_files[i].summary));
		} else {
			safestrcpy(agent_files[i].summary,
				   "dependency refreshed",
				   sizeof(agent_files[i].summary));
		}
		agent_files[i].updated_tick = agent_ticks();
		agent_files[i].fs_generation =
			agent_file_generation_next(scope_id);
		if (agent_files[i].flags & AGENT_FILE_META_F_PERSIST)
			persistent_updated = 1;
		updated++;
		agent_metadata_txn_work_charge(1);
	}
	agent_file_maintain(AGENT_FILE_CHANGE_STATUS);
	if (persistent_updated)
		agent_metadata_store_mark_dirty(scope_id);
out_txn:
	agent_metadata_txn_unlock();
	return updated;
}

static int agent_query_from_payload(struct agent_file_query *q, char *payload)
{
	char key[AGENT_FILE_FIELD_SIZE];
	char val[AGENT_FILE_LOGICAL_SIZE];
	int i = 0;
	int k;
	int v;

	memset(q, 0, sizeof(*q));
	q->flags = AGENT_FILE_QUERY_USE_INDEX;
	q->max_hits = AGENT_FILE_QUERY_MAX_HITS;
	while (payload[i]) {
		while (payload[i] == ' ' || payload[i] == ';' ||
		       payload[i] == ',')
			i++;
		k = 0;
		memset(key, 0, sizeof(key));
		while (payload[i] && payload[i] != '=' &&
		       payload[i] != ':' && payload[i] != ';' &&
		       payload[i] != ',' && k < (int)sizeof(key) - 1)
			key[k++] = payload[i++];
		if (payload[i] != '=' && payload[i] != ':') {
			return -1;
		}
		i++;
		v = 0;
		memset(val, 0, sizeof(val));
		while (payload[i] && payload[i] != ';' &&
		       payload[i] != ',' && v < (int)sizeof(val) - 1)
			val[v++] = payload[i++];
		if (strncmp(key, "path", sizeof(key)) == 0 ||
		    strncmp(key, "physical", sizeof(key)) == 0)
			safestrcpy(q->physical_name, val,
				   sizeof(q->physical_name));
		else if (strncmp(key, "logical", sizeof(key)) == 0 ||
			 strncmp(key, "object", sizeof(key)) == 0 ||
			 strncmp(key, "object_id", sizeof(key)) == 0)
			safestrcpy(q->logical_path, val,
				   sizeof(q->logical_path));
		else if (strncmp(key, "project", sizeof(key)) == 0 ||
			 strncmp(key, "namespace", sizeof(key)) == 0)
			safestrcpy(q->project, val, sizeof(q->project));
		else if (strncmp(key, "workflow", sizeof(key)) == 0)
			safestrcpy(q->workflow, val, sizeof(q->workflow));
		else if (strncmp(key, "run", sizeof(key)) == 0 ||
			 strncmp(key, "run_id", sizeof(key)) == 0)
			safestrcpy(q->run_id, val, sizeof(q->run_id));
		else if (strncmp(key, "stage", sizeof(key)) == 0 ||
			 strncmp(key, "label", sizeof(key)) == 0)
			safestrcpy(q->stage, val, sizeof(q->stage));
		else if (strncmp(key, "kind", sizeof(key)) == 0 ||
			 strncmp(key, "type", sizeof(key)) == 0)
			safestrcpy(q->kind, val, sizeof(q->kind));
		else if (strncmp(key, "status", sizeof(key)) == 0 ||
			 strncmp(key, "state", sizeof(key)) == 0)
			safestrcpy(q->status, val, sizeof(q->status));
		else if (strncmp(key, "summary", sizeof(key)) == 0)
			safestrcpy(q->summary_contains, val,
				   sizeof(q->summary_contains));
		else
			return -1;
	}
	return 0;
}

static void agent_object_state_update(struct proc *p, struct agent_op *op,
				      struct agent_result *res,
				      char *ok_text, char *event_action,
				      char *summary, int propagate_deps,
				      int require_selector,
				      int history_tool_id)
{
	uint64 deps = 0;
	int action_tool_id;
	int updated;
	int delivered;
	char event_payload[AGENT_EVENT_PAYLOAD_SIZE];
	char selector_label[AGENT_FILE_FIELD_SIZE];
	char selector_project[AGENT_FILE_PROJECT_SIZE];
	char selector_run_id[AGENT_FILE_FIELD_SIZE];

	action_tool_id = history_tool_id ? history_tool_id : op->tool_id;
	if (require_selector && !agent_contains(op->payload, "=") &&
	    !agent_contains(op->payload, ":")) {
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "selector_required");
		return;
	}
	if (agent_parse_selector(op->payload, selector_label,
				 sizeof(selector_label), selector_project,
				 sizeof(selector_project), selector_run_id,
				 sizeof(selector_run_id)) < 0) {
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "bad_selector");
		return;
	}
	if (!selector_label[0]) {
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "label_required");
		return;
	}
	if (propagate_deps &&
	    agent_dependency_for_label(agent_identity_proc_scope(p), selector_label,
				       selector_project,
				       selector_run_id, &deps) < 0)
		deps = 0;
	if (agent_action_seen(agent_identity_proc_scope(p), action_tool_id,
			      selector_project, selector_run_id,
			      selector_label, op->request_id)) {
		res->status = AGENT_STATUS_DUPLICATE;
		agent_result_text(res, "duplicate");
		return;
	}
	updated = agent_file_update_status_batch(
		agent_identity_proc_scope(p), selector_label, selector_project,
		selector_run_id, "ok", summary, deps, propagate_deps);
	if (updated == 0) {
		res->status = AGENT_STATUS_NOT_FOUND;
		agent_result_text(res, "target_not_found");
		return;
	}
	agent_action_remember(agent_identity_proc_scope(p), action_tool_id,
			      selector_project, selector_run_id,
			      selector_label, op->request_id);
	res->value0 = deps;
	res->value1 = op->request_id;
	agent_result_text(res, ok_text);
	memset(event_payload, 0, sizeof(event_payload));
	agent_text_append(event_payload, sizeof(event_payload),
			  "state=ok;label=");
	agent_text_append(event_payload, sizeof(event_payload), selector_label);
	if (selector_run_id[0]) {
		agent_text_append(event_payload, sizeof(event_payload),
				  ";run_id=");
		agent_text_append(event_payload, sizeof(event_payload),
				  selector_run_id);
	}
	if (event_action && event_action[0]) {
		agent_text_append(event_payload, sizeof(event_payload),
				  ";action=");
		agent_text_append(event_payload, sizeof(event_payload),
				  event_action);
	}
	delivered = agent_ipc_deliver_watchers(p, AGENT_EVENT_JOB_DONE,
					   op->request_id,
					   p->agent_call_count + 1,
					   event_payload);
	res->value2 = delivered;
}

static int agent_tool_uses_file_metadata(int tool_id)
{
	switch (tool_id) {
	case AGENT_TOOL_QUERY_FILE:
	case AGENT_TOOL_SEND_MESSAGE:
	case AGENT_TOOL_FILE_META_INIT:
	case AGENT_TOOL_READ_FILE_SUMMARY:
	case AGENT_TOOL_DEPENDENCY_QUERY:
	case AGENT_TOOL_RERUN_STAGE:
	case AGENT_TOOL_WRITE_REPORT:
	case AGENT_TOOL_READ_FILE_DIGEST:
	case AGENT_TOOL_ACTION_COMMIT:
	case AGENT_TOOL_ARTIFACT_UPDATE:
	case AGENT_TOOL_LLM_REQUEST:
	case AGENT_TOOL_LLM_RESPONSE:
	case AGENT_TOOL_DEPENDENCY_UPDATE:
		return 1;
	default:
		return 0;
	}
}

int
agent_metadata_execute_tool(struct proc *p, struct agent_op *op,
			    struct agent_result *res)
{
	struct inode *ip;
	struct agent_file_query query;
	struct agent_file_query_result query_result;
	int query_hit_slots[AGENT_FILE_QUERY_MAX_HITS];
	uint64 deps;
	int found;
	char dependency_label[AGENT_FILE_FIELD_SIZE];
	char dependency_project[AGENT_FILE_PROJECT_SIZE];
	char dependency_run_id[AGENT_FILE_FIELD_SIZE];

	switch (op->tool_id) {
	case AGENT_TOOL_QUERY_FILE:
		if (!agent_identity_has_cap(p, AGENT_CAP_META_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (agent_text_empty(op->payload)) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "path_required");
			break;
		}
		if (agent_contains(op->payload, "=") ||
		    agent_contains(op->payload, ":")) {
			if (agent_query_from_payload(&query, op->payload) < 0) {
				res->status = AGENT_STATUS_BAD_PARAM;
				agent_result_text(res, "bad_selector");
				break;
			}
			if (!agent_file_query_has_filter(&query)) {
				res->status = AGENT_STATUS_BAD_PARAM;
				agent_result_text(res, "empty_selector");
				break;
			}
			found = agent_file_query_internal(agent_identity_proc_scope(p),
						       &query, &query_result,
						       query_hit_slots);
			if (found < 0) {
				res->status = found;
				agent_result_text(res, "metadata_unavailable");
				break;
			}
			agent_file_prefetch_update(p, &query, &query_result,
						   query_hit_slots,
						   p->agent_call_count);
			res->value0 = query_result.total_hits;
			res->value1 = query_result.scanned_records;
			res->value2 = (uint64)query_result.used_index |
				      ((uint64)query_result.truncated << 1);
			if (query_result.returned > 0)
				agent_result_text(
					res,
					query_result.hits[0].physical_name);
			else
				agent_result_text(res, "empty");
			break;
		}
		if ((ip = namei_scope(op->payload, VFS_POLICY_WORKFLOW,
				      agent_identity_proc_scope(p))) == 0) {
			res->status = AGENT_STATUS_NOT_FOUND;
			agent_result_text(res, "file_not_found");
			break;
		}
		ivalid(ip);
		res->value0 = ip->type;
		res->value1 = ip->inum;
		res->value2 = ip->size;
		iput(ip);
		agent_result_text(res, "query_file");
		break;
	case AGENT_TOOL_FILE_META_INIT:
		if (!agent_identity_has_cap(p, AGENT_CAP_META_WRITE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_action_history_clear_scope(agent_identity_proc_scope(p));
		res->value0 = agent_metadata_store_load();
		if ((long)res->value0 < 0) {
			res->status = AGENT_STATUS_NO_SPACE;
			agent_result_text(res, "metadata_unavailable");
			break;
		}
		agent_file_enable_scan();
		agent_result_text(res, "file_meta_init");
		break;
	case AGENT_TOOL_READ_FILE_SUMMARY:
		if (!agent_identity_has_cap(p, AGENT_CAP_CONTENT_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		found = agent_file_find(agent_identity_proc_scope(p), op->payload);
		if (found < 0) {
			res->status = AGENT_STATUS_NOT_FOUND;
			agent_result_text(res, "summary_not_found");
		} else {
			res->value0 = agent_files[found].fid;
			res->value1 = agent_files[found].dependency_mask;
			res->value2 = agent_files[found].updated_tick;
			agent_result_text(res, agent_files[found].summary);
		}
		break;
	case AGENT_TOOL_READ_FILE_DIGEST:
		if (!agent_identity_has_cap(p, AGENT_CAP_CONTENT_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_file_digest_read(p, op->payload, res);
		break;
	case AGENT_TOOL_DEPENDENCY_QUERY:
		if (!agent_identity_has_cap(p, AGENT_CAP_META_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		memset(dependency_label, 0, sizeof(dependency_label));
		memset(dependency_project, 0, sizeof(dependency_project));
		memset(dependency_run_id, 0, sizeof(dependency_run_id));
		if (agent_contains(op->payload, "=") ||
		    agent_contains(op->payload, ":")) {
			if (agent_parse_selector(op->payload, dependency_label,
						 sizeof(dependency_label),
						 dependency_project,
						 sizeof(dependency_project),
						 dependency_run_id,
						 sizeof(dependency_run_id)) < 0) {
				res->status = AGENT_STATUS_BAD_PARAM;
				agent_result_text(res, "bad_selector");
				break;
			}
		} else {
			safestrcpy(dependency_label, op->payload,
				   sizeof(dependency_label));
		}
		if (agent_dependency_for_label(agent_identity_proc_scope(p),
					       dependency_label,
					       dependency_project,
					       dependency_run_id, &deps) < 0) {
			res->status = AGENT_STATUS_NOT_FOUND;
			agent_result_text(res, "dependency_not_found");
			break;
		}
		res->value0 = deps;
		res->value1 = agent_mask_count(deps);
		res->value2 = agent_dependency_generation;
		agent_stage_text(agent_identity_proc_scope(p), deps, res->result,
				 sizeof(res->result));
		break;
	case AGENT_TOOL_DEPENDENCY_UPDATE:
		if (!agent_identity_has_cap(p, AGENT_CAP_DEPENDENCY_UPDATE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (agent_text_empty(op->payload)) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "selector_required");
			break;
		}
		res->status = agent_dependency_update_from_payload(
			agent_identity_proc_scope(p), op->payload, res);
		if (res->status == AGENT_STATUS_BAD_PARAM)
			agent_result_text(res, "bad_selector");
		else if (res->status == AGENT_STATUS_NO_SPACE)
			agent_result_text(res, "dependency_full");
		break;
	case AGENT_TOOL_RERUN_STAGE:
		if (!agent_identity_has_cap(p, AGENT_CAP_ACTION_WRITE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			agent_ipc_deliver_watchers(p, AGENT_EVENT_POLICY_DENIED,
					       op->request_id,
					       p->agent_call_count + 1,
					       "action=action_commit;compat=rerun_stage");
			break;
		}
		agent_object_state_update(p, op, res, "action_committed",
					  "action_commit", "action completed",
					  1, 0, AGENT_TOOL_ACTION_COMMIT);
		break;
	case AGENT_TOOL_WRITE_REPORT:
		if (!agent_identity_has_cap(p, AGENT_CAP_ARTIFACT_WRITE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_object_state_update(p, op, res, "artifact_updated",
					  "artifact_update",
					  "artifact updated", 0, 1,
					  AGENT_TOOL_ARTIFACT_UPDATE);
		break;
	case AGENT_TOOL_ACTION_COMMIT:
		if (!agent_identity_has_cap(p, AGENT_CAP_ACTION_WRITE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_object_state_update(p, op, res, "action_committed",
					  "action_commit",
					  "action completed", 1, 1, 0);
		break;
	case AGENT_TOOL_ARTIFACT_UPDATE:
		if (!agent_identity_has_cap(p, AGENT_CAP_ARTIFACT_WRITE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_object_state_update(p, op, res, "artifact_updated",
					  "artifact_update",
					  "artifact updated", 0, 1, 0);
		break;
	default:
		return 0;
	}
	return 1;
}
static int agent_file_edit_lookup_path(struct proc *p, uint64 pathaddr,
				       char *path, struct inode **out,
				       enum vfs_operation operation)
{
	struct inode *ip;
	struct vfs_cred cred;

	if (copyinstr(p->pagetable, path, pathaddr, MAXPATH) < 0)
		return -1;
	path[MAXPATH - 1] = 0;
	if (path[0] == 0 || agent_file_is_meta_store_name(path))
		return AGENT_STATUS_BAD_PARAM;
	ip = namei_scope(path, VFS_POLICY_WORKFLOW, agent_identity_proc_scope(p));
	if (ip == 0)
		return AGENT_STATUS_NOT_FOUND;
	ivalid(ip);
	if (ip->type != T_FILE) {
		iput(ip);
		return AGENT_STATUS_BAD_PARAM;
	}
	vfs_cred_from_proc(p, &cred);
	if (!vfs_inode_authorize(ip, &cred, operation) ||
	    (operation == VFS_OP_WRITE && !exec_policy_inode_mutable(ip))) {
		iput(ip);
		return AGENT_STATUS_DENIED;
	}
	*out = ip;
	return 0;
}

static int agent_file_edit_copy_state(struct proc *p, uint64 stateaddr,
				      struct agent_file_edit_state *state)
{
	if (stateaddr == 0)
		return 0;
	if (user_range_check(p->pagetable, stateaddr, sizeof(*state), PTE_W) < 0)
		return -1;
	return copyout(p->pagetable, stateaddr, (char *)state,
		       sizeof(*state));
}

int sys_agent_file_edit_begin(uint64 pathaddr, uint64 flags, int ttl_ticks,
			      uint64 stateaddr)
{
	struct proc *p = curr_proc();
	struct inode *ip;
	struct agent_file_edit_entry *edit;
	struct agent_file_edit_entry *slot;
	struct agent_file_edit_state state;
	char path[MAXPATH];
	uint64 now;
	uint64 version;
	int ok;
	int rc;
	int ttl;
	int enabled;

	if (!p->is_agent)
		return -1;
	if (!agent_edit_can_write(p))
		return AGENT_STATUS_DENIED;
	if (stateaddr &&
	    user_range_check(p->pagetable, stateaddr, sizeof(state), PTE_W) < 0)
		return -1;
	rc = agent_file_edit_lookup_path(p, pathaddr, path, &ip, VFS_OP_WRITE);
	if (rc < 0)
		return rc;

	now = agent_ticks();
	ttl = ttl_ticks <= 0 ? AGENT_FILE_EDIT_DEFAULT_TTL : ttl_ticks;
	if (ttl > AGENT_FILE_EDIT_MAX_TTL)
		ttl = AGENT_FILE_EDIT_MAX_TTL;

	enabled = agent_edit_lock();
	agent_edit_cleanup_expired_locked(now);
	version = agent_edit_version_inode_locked(ip, 1, &ok);
	if (!ok) {
		agent_edit_unlock(enabled);
		iput(ip);
		return AGENT_STATUS_NO_SPACE;
	}
	edit = agent_edit_find_locked(agent_identity_proc_scope(p), ip->dev, ip->inum,
				      ip->vfs_incarnation);
	if (edit) {
		if ((flags & AGENT_FILE_EDIT_F_ORCHESTRATOR_BREAK) &&
		    agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE)) {
			agent_edit_release_locked(edit, 1);
			version = agent_edit_version_locked(
				ip->dev, ip->inum, ip->vfs_incarnation, &ok);
			if (!ok) {
				agent_edit_unlock(enabled);
				iput(ip);
				return AGENT_STATUS_NO_SPACE;
			}
		} else {
			edit->conflict_count++;
			agent_edit_fill_state_locked(
				&state, edit, ip->dev, ip->inum,
				ip->vfs_incarnation, path);
			agent_edit_unlock(enabled);
			iput(ip);
			agent_edit_audit(p, AGENT_STATUS_CONFLICT,
					 "edit_begin_conflict", &state, 0);
			if (agent_file_edit_copy_state(p, stateaddr, &state) <
			    0)
				return -1;
			return AGENT_STATUS_CONFLICT;
		}
	}
	slot = agent_edit_free_locked(agent_identity_proc_scope(p));
	if (slot == 0) {
		agent_edit_unlock(enabled);
		iput(ip);
		return AGENT_STATUS_NO_SPACE;
	}
	memset(slot, 0, sizeof(*slot));
	slot->active = 1;
	slot->scope_id = agent_identity_proc_scope(p);
	slot->owner_pid = p->pid;
	slot->owner_agent_id = p->agent_id;
	slot->owner_role = p->agent_role;
	slot->owner_control_id = p->agent_control_id;
	slot->lease_id = agent_file_edit_next_lease++;
	if (agent_file_edit_next_lease == 0)
		agent_file_edit_next_lease = 1;
	slot->dev = ip->dev;
	slot->inum = ip->inum;
	slot->incarnation = ip->vfs_incarnation;
	slot->base_version = version;
	slot->deadline_tick = now + ttl;
	safestrcpy(slot->path, path, sizeof(slot->path));
	agent_edit_fill_state_locked(&state, slot, ip->dev, ip->inum,
				     ip->vfs_incarnation, path);
	agent_edit_unlock(enabled);
	iput(ip);
	agent_edit_audit(p, AGENT_STATUS_OK, "edit_begin", &state, 1);
	return agent_file_edit_copy_state(p, stateaddr, &state);
}

int sys_agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
			       uint64 stateaddr)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;
	struct agent_file_edit_state state;
	char path[AGENT_FILE_LOGICAL_SIZE];
	uint64 dev;
	uint64 inum;
	uint64 incarnation;
	uint64 base;
	uint64 now;
	uint64 current;
	uint64 new_version;
	int ok;
	int dirty;
	int rc;
	int enabled;

	if (!p->is_agent)
		return -1;
	if (stateaddr &&
	    user_range_check(p->pagetable, stateaddr, sizeof(state), PTE_W) < 0)
		return -1;
	now = agent_ticks();
	enabled = agent_edit_lock();
	agent_edit_cleanup_expired_locked(now);
	edit = agent_edit_find_lease_locked(agent_identity_proc_scope(p), lease_id);
	if (edit == 0) {
		agent_edit_unlock(enabled);
		return AGENT_STATUS_NOT_FOUND;
	}
	if (!agent_edit_owner(edit, p) &&
	    !agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE)) {
		agent_edit_fill_state_locked(&state, edit, edit->dev,
					     edit->inum, edit->incarnation,
					     edit->path);
		agent_edit_unlock(enabled);
		agent_edit_audit(p, AGENT_STATUS_DENIED,
				 "edit_commit_denied", &state, 0);
		if (agent_file_edit_copy_state(p, stateaddr, &state) < 0)
			return -1;
		return AGENT_STATUS_DENIED;
	}
	current = agent_edit_version_locked(edit->dev, edit->inum,
				    edit->incarnation, &ok);
	if (!ok || current != edit->base_version ||
	    expected_version != edit->base_version) {
		agent_edit_fill_state_locked(&state, edit, edit->dev,
					     edit->inum, edit->incarnation,
					     edit->path);
		agent_edit_unlock(enabled);
		agent_edit_audit(p, AGENT_STATUS_STALE,
				 "edit_commit_stale", &state, 0);
		if (agent_file_edit_copy_state(p, stateaddr, &state) < 0)
			return -1;
		return AGENT_STATUS_STALE;
	}
	dev = edit->dev;
	inum = edit->inum;
	incarnation = edit->incarnation;
	base = edit->base_version;
	dirty = edit->dirty;
	safestrcpy(path, edit->path, sizeof(path));
	new_version = dirty ? base + 1 : base;
	rc = agent_edit_set_version_locked(dev, inum, incarnation, new_version);
	memset(edit, 0, sizeof(*edit));
	memset(&state, 0, sizeof(state));
	state.active = 0;
	state.lease_id = lease_id;
	state.dev = dev;
	state.inum = inum;
	state.incarnation = incarnation;
	state.base_version = base;
	state.current_version = new_version;
	state.dirty = dirty;
	safestrcpy(state.path, path, sizeof(state.path));
	agent_edit_unlock(enabled);
	if (rc < 0)
		return AGENT_STATUS_NO_SPACE;
	agent_edit_audit(p, AGENT_STATUS_OK, "edit_commit", &state, 1);
	return agent_file_edit_copy_state(p, stateaddr, &state);
}

int sys_agent_file_edit_abort(uint64 lease_id)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;
	struct agent_file_edit_state state;
	int enabled;

	if (!p->is_agent)
		return -1;
	enabled = agent_edit_lock();
	agent_edit_cleanup_expired_locked(agent_ticks());
	edit = agent_edit_find_lease_locked(agent_identity_proc_scope(p), lease_id);
	if (edit == 0) {
		agent_edit_unlock(enabled);
		return AGENT_STATUS_NOT_FOUND;
	}
	if (!agent_edit_owner(edit, p) &&
	    !agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE)) {
		agent_edit_fill_state_locked(&state, edit, edit->dev,
					     edit->inum, edit->incarnation,
					     edit->path);
		agent_edit_unlock(enabled);
		agent_edit_audit(p, AGENT_STATUS_DENIED,
				 "edit_abort_denied", &state, 0);
		return AGENT_STATUS_DENIED;
	}
	agent_edit_fill_state_locked(&state, edit, edit->dev, edit->inum,
				     edit->incarnation, edit->path);
	agent_edit_release_locked(edit, 1);
	agent_edit_unlock(enabled);
	agent_edit_audit(p, AGENT_STATUS_OK, "edit_abort", &state, 1);
	return 0;
}

int sys_agent_file_edit_state(uint64 pathaddr, uint64 stateaddr)
{
	struct proc *p = curr_proc();
	struct inode *ip;
	struct agent_file_edit_entry *edit;
	struct agent_file_edit_state state;
	char path[MAXPATH];
	int ok;
	int rc;
	int enabled;

	if (!p->is_agent)
		return -1;
	if (stateaddr == 0 ||
	    user_range_check(p->pagetable, stateaddr, sizeof(state), PTE_W) < 0)
		return -1;
	rc = agent_file_edit_lookup_path(p, pathaddr, path, &ip, VFS_OP_LOOKUP);
	if (rc < 0)
		return rc;
	enabled = agent_edit_lock();
	agent_edit_cleanup_expired_locked(agent_ticks());
	agent_edit_version_inode_locked(ip, 1, &ok);
	if (!ok) {
		agent_edit_unlock(enabled);
		iput(ip);
		return AGENT_STATUS_NO_SPACE;
	}
	edit = agent_edit_find_locked(agent_identity_proc_scope(p), ip->dev, ip->inum,
				      ip->vfs_incarnation);
	agent_edit_fill_state_locked(&state, edit, ip->dev, ip->inum,
				     ip->vfs_incarnation, path);
	agent_edit_unlock(enabled);
	iput(ip);
	return agent_file_edit_copy_state(p, stateaddr, &state);
}

int sys_agent_file_meta_init(void)
{
	struct proc *p = curr_proc();
	int loaded;
	int result = AGENT_STATUS_NO_SPACE;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_WRITE,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	if (!agent_metadata_store_submit_wait_locked())
		return AGENT_STATUS_NO_SPACE;
	if (agent_metadata_store_loaded() &&
	    agent_metadata_store_scope_pending(agent_identity_proc_scope(p)) &&
	    agent_metadata_store_persist() < 0)
		goto out_txn;
	if (!agent_metadata_store_submit_wait_locked())
		return AGENT_STATUS_NO_SPACE;
	loaded = agent_metadata_store_reload(agent_identity_proc_scope(p));
	if (loaded < 0) {
		if (!agent_metadata_store_available() ||
		    agent_metadata_store_has_durable_bank())
			goto out_txn;
		if (!agent_metadata_store_loaded()) {
			if (agent_file_install_empty_store() < 0)
				goto out_txn;
		} else if (agent_metadata_store_persist_system() < 0) {
			goto out_txn;
		}
	}
	agent_action_history_clear_scope(agent_identity_proc_scope(p));
	agent_file_enable_scan();
	agent_direct_effect_audit(p, AGENT_TOOL_FILE_META_INIT,
				  AGENT_STATUS_OK, "meta_init", 0, 0, 0,
				  0, 1);
	result = 0;
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

int sys_agent_file_meta_set(uint64 metaaddr)
{
	struct proc *p = curr_proc();
	struct agent_file_meta meta;
	struct agent_file_meta previous;
	struct agent_file_meta auto_binding;
	uint previous_scope;
	uint scope_id;
	int slot = -1;
	int fid_slot = -1;
	int physical_slot = -1;
	int logical_slot = -1;
	int identity_slot = -1;
	int had_previous;
	int status_changed = 0;
	int auto_persist;
	int result = AGENT_STATUS_NO_SPACE;
	uint changes = 0;
	uint64 audit_fid;
	uint64 mask;
	char event_payload[AGENT_EVENT_PAYLOAD_SIZE];

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_WRITE,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	scope_id = agent_identity_proc_scope(p);
	if (!agent_scope_valid(scope_id))
		return AGENT_STATUS_DENIED;
	if (copyin(p->pagetable, (char *)&meta, metaaddr, sizeof(meta)) < 0)
		return -1;
	meta.physical_name[sizeof(meta.physical_name) - 1] = 0;
	meta.logical_path[sizeof(meta.logical_path) - 1] = 0;
	meta.project[sizeof(meta.project) - 1] = 0;
	meta.workflow[sizeof(meta.workflow) - 1] = 0;
	meta.run_id[sizeof(meta.run_id) - 1] = 0;
	meta.stage[sizeof(meta.stage) - 1] = 0;
	meta.kind[sizeof(meta.kind) - 1] = 0;
	meta.status[sizeof(meta.status) - 1] = 0;
	meta.summary[sizeof(meta.summary) - 1] = 0;
	if ((meta.dev != 0 || meta.inum != 0 || meta.incarnation != 0) &&
	    (meta.dev == 0 || meta.inum == 0 || meta.incarnation == 0))
		return AGENT_STATUS_BAD_PARAM;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	if (!agent_metadata_store_submit_wait_locked())
		return AGENT_STATUS_NO_SPACE;
	if (agent_metadata_store_load() < 0)
		goto out_txn;
	/*
	 * Preserve auto-track persistence for an existing VFS object without
	 * mutating metadata before request validation or scanning the directory.
	 * Metadata-only creation remains volatile.
	 */
	memset(&auto_binding, 0, sizeof(auto_binding));
	auto_persist = agent_file_path_autopersist(scope_id,
					   meta.physical_name,
					   &auto_binding);
	mask = meta.update_mask;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used || agent_file_scopes[i] != scope_id)
			continue;
		if (meta.fid > 0 && agent_files[i].fid == meta.fid)
			fid_slot = i;
		if (meta.physical_name[0] &&
		    strncmp(agent_files[i].physical_name, meta.physical_name,
			    sizeof(meta.physical_name)) == 0)
			physical_slot = i;
		if (meta.logical_path[0] &&
		    strncmp(agent_files[i].logical_path, meta.logical_path,
			    sizeof(meta.logical_path)) == 0)
			logical_slot = i;
		if (meta.dev != 0 && agent_files[i].dev == meta.dev &&
		    agent_files[i].inum == meta.inum &&
		    agent_files[i].incarnation == meta.incarnation)
			identity_slot = i;
	}
	{
		int candidates[] = {
			fid_slot, physical_slot, logical_slot, identity_slot,
		};

		for (uint i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++) {
			if (candidates[i] < 0)
				continue;
			if (slot >= 0 && slot != candidates[i]) {
				result = AGENT_STATUS_CONFLICT;
				goto out_txn;
			}
			slot = candidates[i];
		}
	}
	/* The immutable inode identity is always a guard, never an update value. */
	if (meta.dev != 0 && identity_slot < 0) {
		result = slot >= 0 ? AGENT_STATUS_CONFLICT :
				     AGENT_STATUS_NOT_FOUND;
		goto out_txn;
	}
	if (meta.flags & AGENT_FILE_META_F_DELETE) {
		/* DELETE treats every supplied key as a conjunctive selector. */
		if ((meta.fid > 0 && fid_slot < 0) ||
		    (meta.physical_name[0] && physical_slot < 0) ||
		    (meta.logical_path[0] && logical_slot < 0) ||
		    (meta.dev != 0 && identity_slot < 0)) {
			result = slot >= 0 ? AGENT_STATUS_CONFLICT :
					     AGENT_STATUS_NOT_FOUND;
			goto out_txn;
		}
		if (slot < 0) {
			result = AGENT_STATUS_NOT_FOUND;
			goto out_txn;
		}
		previous = agent_files[slot];
		previous_scope = agent_file_scopes[slot];
		had_previous = 1;
		audit_fid = agent_files[slot].fid;
		agent_file_clear_slot(slot);
		agent_file_maintain(AGENT_FILE_CHANGE_ALL);
		agent_metadata_store_mark_dirty(scope_id);
		if (agent_metadata_store_persist() < 0) {
			agent_file_restore_slot(slot, &previous,
						previous_scope, had_previous);
			agent_file_request_scan();
			goto out_txn;
		}
		agent_file_request_scan();
		agent_direct_effect_audit(p, AGENT_TOOL_FILE_META_INIT,
					  AGENT_STATUS_OK, "meta_delete",
					  audit_fid, mask, slot,
					  meta.flags, 1);
		result = 0;
		goto out_txn;
	}
	if (slot < 0) {
		slot = agent_file_alloc_slot(scope_id);
	}
	if (slot < 0)
		goto out_txn;
	if (meta.fid > 0)
		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			if (i != slot && agent_files[i].used &&
			    agent_file_scopes[i] == scope_id &&
			    agent_files[i].fid == meta.fid) {
				result = AGENT_STATUS_CONFLICT;
				goto out_txn;
			}
	previous = agent_files[slot];
	previous_scope = agent_file_scopes[slot];
	had_previous = agent_files[slot].used;
	if (!agent_files[slot].used) {
		uint64 fid = meta.fid ? meta.fid :
			       agent_file_alloc_fid(scope_id);

		if (fid == 0)
			goto out_txn;
		memset(&agent_files[slot], 0, sizeof(agent_files[slot]));
		agent_files[slot].used = 1;
		agent_files[slot].fid = fid;
		agent_file_scopes[slot] = scope_id;
		if (auto_persist) {
			agent_files[slot].flags = AGENT_FILE_META_F_PERSIST |
					  AGENT_FILE_META_F_AUTOSCAN;
			safestrcpy(agent_files[slot].physical_name,
				   meta.physical_name,
				   sizeof(agent_files[slot].physical_name));
			safestrcpy(agent_files[slot].logical_path,
				   meta.physical_name,
				   sizeof(agent_files[slot].logical_path));
			safestrcpy(agent_files[slot].project, "root",
				   sizeof(agent_files[slot].project));
			safestrcpy(agent_files[slot].workflow,
				   "background-scan",
				   sizeof(agent_files[slot].workflow));
			safestrcpy(agent_files[slot].run_id, "ROOT",
				   sizeof(agent_files[slot].run_id));
			safestrcpy(agent_files[slot].stage, "scan",
				   sizeof(agent_files[slot].stage));
			agent_file_infer_kind(meta.physical_name,
					      agent_files[slot].kind,
					      sizeof(agent_files[slot].kind));
			agent_file_infer_status(meta.physical_name,
						agent_files[slot].status,
						sizeof(agent_files[slot].status));
			safestrcpy(agent_files[slot].summary,
				   "auto scanned root file",
				   sizeof(agent_files[slot].summary));
			agent_files[slot].dev = auto_binding.dev;
			agent_files[slot].inum = auto_binding.inum;
			agent_files[slot].incarnation =
				auto_binding.incarnation;
			agent_files[slot].size = auto_binding.size;
		}
	}
	if (agent_file_scopes[slot] != scope_id) {
		result = AGENT_STATUS_NOT_FOUND;
		goto out_txn;
	}
	if (meta.fid > 0)
		agent_files[slot].fid = meta.fid;
	if ((mask & AGENT_FILE_META_UPDATE_PHYSICAL) ||
	    (!mask && meta.physical_name[0]))
		safestrcpy(agent_files[slot].physical_name, meta.physical_name,
			   sizeof(agent_files[slot].physical_name));
	if ((mask & AGENT_FILE_META_UPDATE_LOGICAL) ||
	    (!mask && meta.logical_path[0]))
		safestrcpy(agent_files[slot].logical_path, meta.logical_path,
			   sizeof(agent_files[slot].logical_path));
	if ((mask & AGENT_FILE_META_UPDATE_PROJECT) ||
	    (!mask && meta.project[0]))
		safestrcpy(agent_files[slot].project, meta.project,
			   sizeof(agent_files[slot].project));
	if ((mask & AGENT_FILE_META_UPDATE_WORKFLOW) ||
	    (!mask && meta.workflow[0]))
		safestrcpy(agent_files[slot].workflow, meta.workflow,
			   sizeof(agent_files[slot].workflow));
	if ((mask & AGENT_FILE_META_UPDATE_RUN_ID) ||
	    (!mask && meta.run_id[0]))
		safestrcpy(agent_files[slot].run_id, meta.run_id,
			   sizeof(agent_files[slot].run_id));
	if ((mask & AGENT_FILE_META_UPDATE_STAGE) ||
	    (!mask && meta.stage[0]))
		safestrcpy(agent_files[slot].stage, meta.stage,
			   sizeof(agent_files[slot].stage));
	if ((mask & AGENT_FILE_META_UPDATE_KIND) ||
	    (!mask && meta.kind[0]))
		safestrcpy(agent_files[slot].kind, meta.kind,
			   sizeof(agent_files[slot].kind));
	if ((mask & AGENT_FILE_META_UPDATE_STATUS) ||
	    (!mask && meta.status[0])) {
		if (strncmp(agent_files[slot].status, meta.status,
			    sizeof(meta.status)) != 0)
			status_changed = 1;
		safestrcpy(agent_files[slot].status, meta.status,
			   sizeof(agent_files[slot].status));
	}
	if ((mask & AGENT_FILE_META_UPDATE_SUMMARY) ||
	    (!mask && meta.summary[0]))
		safestrcpy(agent_files[slot].summary, meta.summary,
			   sizeof(agent_files[slot].summary));
	if ((mask & AGENT_FILE_META_UPDATE_DEPENDENCY) ||
	    (!mask && meta.dependency_mask))
		agent_files[slot].dependency_mask = meta.dependency_mask;
	agent_files[slot].flags &= ~AGENT_FILE_META_F_AUTOSCAN;
	if (meta.flags & AGENT_FILE_META_F_AUTOSCAN)
		agent_files[slot].flags |= AGENT_FILE_META_F_AUTOSCAN;
	if (meta.flags & AGENT_FILE_META_F_PERSIST)
		agent_files[slot].flags |= AGENT_FILE_META_F_PERSIST;
	agent_files[slot].updated_tick = agent_ticks();
	if (agent_file_bind_slot(slot, 1, p) < 0) {
		agent_file_restore_slot(slot, &previous, previous_scope,
					had_previous);
		result = AGENT_STATUS_NOT_FOUND;
		goto out_txn;
	}
	if (agent_file_scan_active)
		agent_file_scan_seen[slot] = 1;
	if (!had_previous) {
		changes = AGENT_FILE_CHANGE_ALL;
	} else {
		if (strncmp(previous.status, agent_files[slot].status,
			    sizeof(previous.status)) != 0)
			changes |= AGENT_FILE_CHANGE_STATUS;
		if (strncmp(previous.stage, agent_files[slot].stage,
			    sizeof(previous.stage)) != 0)
			changes |= AGENT_FILE_CHANGE_STAGE;
		if (strncmp(previous.kind, agent_files[slot].kind,
			    sizeof(previous.kind)) != 0)
			changes |= AGENT_FILE_CHANGE_KIND;
		if (strncmp(previous.project, agent_files[slot].project,
			    sizeof(previous.project)) != 0 ||
		    strncmp(previous.workflow, agent_files[slot].workflow,
			    sizeof(previous.workflow)) != 0 ||
		    strncmp(previous.run_id, agent_files[slot].run_id,
			    sizeof(previous.run_id)) != 0 ||
		    strncmp(previous.physical_name,
			    agent_files[slot].physical_name,
			    sizeof(previous.physical_name)) != 0 ||
		    strncmp(previous.logical_path,
			    agent_files[slot].logical_path,
			    sizeof(previous.logical_path)) != 0)
			changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
		if (previous.dependency_mask !=
		    agent_files[slot].dependency_mask)
			changes |= AGENT_FILE_CHANGE_DEPENDENCY;
	}
	if (changes)
		agent_file_maintain(changes);
	if (agent_files[slot].flags & AGENT_FILE_META_F_PERSIST)
		agent_metadata_store_mark_dirty(scope_id);
	if ((agent_files[slot].flags & AGENT_FILE_META_F_PERSIST) &&
	    agent_metadata_store_persist() < 0) {
		agent_file_restore_slot(slot, &previous, previous_scope,
					had_previous);
		agent_file_request_scan();
		goto out_txn;
	}
	agent_file_event_payload(&agent_files[slot], event_payload,
				 sizeof(event_payload));
	if (status_changed && meta.status[0])
		agent_ipc_deliver_watchers(p, AGENT_EVENT_FILE_STATUS, meta.fid,
				       p->context_path_latest,
				       event_payload);
	agent_file_request_scan();
	agent_direct_effect_audit(p, AGENT_TOOL_FILE_META_INIT,
				  AGENT_STATUS_OK, "meta_set",
				  agent_files[slot].fid, mask, slot,
				  meta.flags, 1);
	result = 0;
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

int sys_agent_file_query(uint64 queryaddr, uint64 resultaddr)
{
	struct proc *p = curr_proc();
	struct agent_file_query query;
	struct agent_file_query_result result;
	int query_hit_slots[AGENT_FILE_QUERY_MAX_HITS];
	int returned;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_READ,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	if (copyin(p->pagetable, (char *)&query, queryaddr,
		   sizeof(query)) < 0)
		return -1;
	query.physical_name[sizeof(query.physical_name) - 1] = 0;
	query.logical_path[sizeof(query.logical_path) - 1] = 0;
	query.project[sizeof(query.project) - 1] = 0;
	query.workflow[sizeof(query.workflow) - 1] = 0;
	query.run_id[sizeof(query.run_id) - 1] = 0;
	query.stage[sizeof(query.stage) - 1] = 0;
	query.kind[sizeof(query.kind) - 1] = 0;
	query.status[sizeof(query.status) - 1] = 0;
	query.summary_contains[sizeof(query.summary_contains) - 1] = 0;
	if (user_range_check(p->pagetable, resultaddr, sizeof(result), PTE_W) < 0)
		return -1;
	if (!agent_file_query_has_filter(&query))
		return AGENT_STATUS_BAD_PARAM;
	/*
	 * Context serialization is the outer lock everywhere: agent_run holds it
	 * while metadata tools execute.  Taking the same order here prevents the
	 * query's system-context append from inverting lane -> metadata.
	 */
	if (agent_lifecycle_context_lane_enter(p) < 0)
		return -1;
	if (!agent_metadata_txn_lock(1)) {
		agent_lifecycle_context_lane_leave(p);
		return AGENT_STATUS_NO_SPACE;
	}
	returned = agent_file_query_internal(agent_identity_proc_scope(p), &query,
					     &result, query_hit_slots);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0) {
		returned = -1;
		goto out_txn;
	}
	if (returned < 0)
		goto out_txn;
	if (agent_context_append_system(
		    p, AGENT_TOOL_QUERY_FILE, 0, query.flags,
		    query.status[0] ? query.status : query.stage,
		    result.returned ? result.hits[0].physical_name : "empty",
		    AGENT_STATUS_OK, result.total_hits, result.scanned_records,
		    result.used_index) == 0)
		agent_file_prefetch_update(p, &query, &result,
					   query_hit_slots,
					   p->agent_call_count);
out_txn:
	agent_metadata_txn_unlock();
	agent_lifecycle_context_lane_leave(p);
	return returned;
}

int sys_agent_file_prefetch_snapshot(uint64 hintsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_file_prefetch_hint hint;
	int visible;
	int n;
	int start;
	int slot;
	int result;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_READ,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	visible = p->agent_prefetch_count;
	if (visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	if (max == 0) {
		result = visible;
		goto out_txn;
	}
	if (hintsaddr == 0) {
		result = AGENT_STATUS_BAD_PARAM;
		goto out_txn;
	}
	n = visible < max ? visible : max;
	start = (p->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
		 visible) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	for (int i = 0; i < n; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_MAX_HINTS;
		memmove(&hint, &p->agent_prefetch_hints[slot], sizeof(hint));
		if (copyout(p->pagetable,
			    hintsaddr +
				    i * sizeof(struct agent_file_prefetch_hint),
			    (char *)&hint, sizeof(hint)) < 0) {
			result = -1;
			goto out_txn;
		}
	}
	result = n;
out_txn:
	agent_metadata_txn_unlock();
	return result;
}

int sys_agent_file_prefetch_span_snapshot(uint64 hintsaddr, int max)
{
	struct proc *p = curr_proc();
	int result;

	if (!p->is_agent)
		return -1;
	if (!agent_identity_authorize_object(p, AGENT_CAP_META_READ,
				    agent_identity_proc_scope(p), 0))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (!agent_metadata_txn_lock(1))
		return AGENT_STATUS_NO_SPACE;
	result = agent_observe_prefetch_span_snapshot(p, hintsaddr, max);
	agent_metadata_txn_unlock();
	return result;
}

int
agent_metadata_tool_enter(int tool_id)
{
	if (!agent_tool_uses_file_metadata(tool_id))
		return 0;
	if (!agent_metadata_txn_lock(1))
		return -1;
	if (!agent_metadata_store_reload_wait_locked())
		return -1;
	return 1;
}

void
agent_metadata_tool_exit(int locked)
{
	if (locked > 0)
		agent_metadata_txn_unlock();
}

void
agent_metadata_fill_info(uint scope_id, struct agent_info *info)
{
	agent_metadata_store_fill_info(scope_id, info);
	if (info == 0)
		return;
	info->file_scan_runs = agent_file_scan_runs;
	info->file_scan_entries = agent_file_scan_entries;
	info->file_scan_added = agent_file_scan_added;
	info->file_scan_updated = agent_file_scan_updated;
	info->file_scan_removed = agent_file_scan_removed;
	info->file_scan_generation = agent_file_generation;
	info->file_scan_pending =
		agent_file_scan_pending || agent_file_scan_active;
	info->file_digest_cache_hits = agent_file_digest_cache_hits;
	info->file_digest_cache_misses = agent_file_digest_cache_misses;
}

void
agent_metadata_tick(uint64 now)
{
	if (agent_file_scan_enabled && !agent_file_scan_active &&
	    !agent_file_scan_pending && now >= agent_file_scan_next_tick)
		agent_file_scan_pending = 1;
}
