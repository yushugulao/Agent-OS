#include "agent.h"
#include "agent_file_name_policy.h"
#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "fs.h"
#include "timer.h"
#include "trap.h"
#include "vfs_security.h"

/*
 * Incarnation-bound state for workflow files. Catalog slots remain owned by
 * agent_metadata_objects; this module owns only versions, leases, published
 * sizes, digest cache entries, and the generation used to invalidate readers.
 */
#define AGENT_FILE_DIGEST_CACHE_MAX 8
#define AGENT_FILE_EDIT_MAX 32
#define AGENT_FILE_VERSION_MAX NINODE
#define AGENT_EDIT_SCOPE_LIMIT 8
#define AGENT_FILE_CACHE_SCOPE_MAX NPROC

_Static_assert(AGENT_FILE_VERSION_MAX == NINODE,
	       "inode version sidecar must cover every filesystem inode");
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_EDIT_SCOPE_LIMIT <=
	       AGENT_FILE_EDIT_MAX,
	       "edit table must reserve every workflow partition");
_Static_assert(AGENT_FILE_CACHE_SCOPE_MAX > VFS_SCOPE_MAX_ACTIVE,
	       "cache table must include the system metadata owner");

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

struct agent_file_cache_scope_state {
	int used;
	uint scope_id;
	uint64 cache_generation;
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

static struct agent_file_digest_cache_entry
	agent_file_digest_cache[AGENT_FILE_DIGEST_CACHE_MAX];
static struct agent_file_cache_scope_state
	agent_file_cache_scopes[AGENT_FILE_CACHE_SCOPE_MAX];
static struct agent_file_version_entry
	agent_file_versions[AGENT_FILE_VERSION_MAX];
static struct agent_file_edit_entry agent_file_edits[AGENT_FILE_EDIT_MAX];
static int agent_file_digest_cache_head;
static uint64 agent_file_digest_cache_hits;
static uint64 agent_file_digest_cache_misses;
static uint64 agent_file_generation;
static uint64 agent_file_content_generation;
static uint64 agent_file_size_sequence;
static volatile int agent_file_edit_guard;
static uint64 agent_file_edit_next_lease;

static uint64
agent_file_state_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

void
agent_file_state_init(void)
{
	memset(agent_file_digest_cache, 0, sizeof(agent_file_digest_cache));
	agent_file_digest_cache_head = 0;
	agent_file_digest_cache_hits = 0;
	agent_file_digest_cache_misses = 0;
	memset(agent_file_cache_scopes, 0, sizeof(agent_file_cache_scopes));
	memset(agent_file_versions, 0, sizeof(agent_file_versions));
	memset(agent_file_edits, 0, sizeof(agent_file_edits));
	agent_file_generation = 0;
	agent_file_content_generation = 0;
	agent_file_size_sequence = 0;
	agent_file_edit_guard = 0;
	agent_file_edit_next_lease = 1;
}

static int
agent_file_state_reserved_path(char *path)
{
	return path != 0 &&
	       (strncmp(path, AGENT_META_STORE_NAME_0, DIRSIZ) == 0 ||
		strncmp(path, AGENT_META_STORE_NAME_1, DIRSIZ) == 0);
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

uint64
agent_file_state_scope_generation(uint scope_id)
{
	struct agent_file_cache_scope_state *state;
	uint64 generation;
	int enabled = intr_save();

	state = agent_file_cache_scope_locked(scope_id, 1);
	generation = state ? state->cache_generation : agent_file_generation;
	intr_restore(enabled);
	return generation;
}

uint64
agent_file_state_generation_next(uint scope_id)
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

static int
agent_edit_lock(void)
{
	int enabled = intr_save();

	while (__sync_lock_test_and_set(&agent_file_edit_guard, 1) != 0)
		;
	__sync_synchronize();
	return enabled;
}

static void
agent_edit_unlock(int enabled)
{
	__sync_synchronize();
	__sync_lock_release(&agent_file_edit_guard);
	intr_restore(enabled);
}

static int
agent_file_version_index(uint64 dev, uint64 inum)
{
	if (dev != ROOTDEV || inum == 0 || inum >= AGENT_FILE_VERSION_MAX)
		return -1;
	return inum;
}

static int
agent_file_version_matches_identity(struct agent_file_version_entry *entry,
				    uint64 dev, uint64 inum,
				    uint64 incarnation)
{
	return entry->used && entry->dev == dev && entry->inum == inum &&
	       entry->incarnation == incarnation;
}

static int
agent_file_version_matches_inode(struct agent_file_version_entry *entry,
				 struct inode *ip)
{
	return agent_file_version_matches_identity(
		       entry, ip->dev, ip->inum, ip->vfs_incarnation) &&
	       entry->scope_id == ip->vfs_scope_id &&
	       entry->storage_owner == ip->fs_owner_domain &&
	       entry->vfs_policy == ip->vfs_policy;
}

static void
agent_file_version_clear_locked(int slot)
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

static int
agent_file_version_slot_locked(struct inode *ip, int create)
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

	/* A new inode incarnation retires every prior transient sidecar. */
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

static int
agent_file_version_identity_slot_locked(uint64 dev, uint64 inum,
					uint64 incarnation)
{
	int slot = agent_file_version_index(dev, inum);

	if (slot < 0 || !agent_file_version_matches_identity(
				 &agent_file_versions[slot], dev, inum, incarnation))
		return -1;
	return slot;
}

static int
agent_file_content_version_locked(struct inode *ip, uint64 *version,
				  int create)
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

void
agent_file_state_content_bump(struct inode *ip)
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

int
agent_file_state_size_publish(struct inode *ip, int force)
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
				agent_file_state_generation_next(ip->vfs_scope_id);
			entry->published_size_tick = agent_file_state_ticks();
			changed = 1;
		} else {
			changed = 0;
		}
	}
	agent_edit_unlock(enabled);
	return changed;
}

static void
agent_file_overlay_published_size_locked(struct agent_file_meta *meta,
					 uint scope_id)
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

void
agent_file_state_overlay_published_size(struct agent_file_meta *meta,
					uint scope_id)
{
	int enabled = agent_edit_lock();

	agent_file_overlay_published_size_locked(meta, scope_id);
	agent_edit_unlock(enabled);
}

void
agent_file_state_sizes_persisted(uint scope_id, uint64 sequence)
{
	int enabled = agent_edit_lock();

	for (int i = 0; i < AGENT_FILE_VERSION_MAX; i++)
		if (agent_file_versions[i].published_size_dirty &&
		    agent_file_versions[i].scope_id == scope_id &&
		    agent_file_versions[i].published_size_sequence <= sequence)
			agent_file_versions[i].published_size_dirty = 0;
	agent_edit_unlock(enabled);
}

int
agent_file_state_snapshot_begin(uint64 *size_sequence)
{
	int enabled = agent_edit_lock();

	*size_sequence = agent_file_size_sequence;
	return enabled;
}

void
agent_file_state_snapshot_overlay(struct agent_file_meta *meta, uint scope_id)
{
	agent_file_overlay_published_size_locked(meta, scope_id);
}

void
agent_file_state_snapshot_end(int enabled)
{
	agent_edit_unlock(enabled);
}

void
agent_file_version_reclaim(struct inode *ip)
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

static uint64
agent_edit_version_locked(uint64 dev, uint64 inum, uint64 incarnation,
			  int *ok)
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

static uint64
agent_edit_version_inode_locked(struct inode *ip, int create, int *ok)
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

static int
agent_edit_set_version_locked(uint64 dev, uint64 inum, uint64 incarnation,
			      uint64 version)
{
	int slot = agent_file_version_identity_slot_locked(dev, inum,
							  incarnation);

	if (slot < 0)
		return -1;
	agent_file_versions[slot].edit_version = version;
	return 0;
}

static void
agent_edit_bump_version_locked(struct inode *ip)
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

static struct agent_file_edit_entry *
agent_edit_find_lease_locked(uint scope_id, uint64 lease)
{
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_file_edits[i].active &&
		    agent_file_edits[i].scope_id == scope_id &&
		    agent_file_edits[i].lease_id == lease)
			return &agent_file_edits[i];
	return 0;
}

static struct agent_file_edit_entry *
agent_edit_free_locked(uint scope_id)
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

static int
agent_edit_expired(struct agent_file_edit_entry *e, uint64 now)
{
	return e->active && e->deadline_tick != 0 && now >= e->deadline_tick;
}

static void
agent_edit_release_locked(struct agent_file_edit_entry *e, int publish_dirty)
{
	if (e == 0 || !e->active)
		return;
	if (publish_dirty && e->dirty)
		agent_edit_set_version_locked(e->dev, e->inum, e->incarnation,
					      e->base_version + 1);
	memset(e, 0, sizeof(*e));
}

static void
agent_edit_cleanup_expired_locked(uint64 now)
{
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_edit_expired(&agent_file_edits[i], now))
			agent_edit_release_locked(&agent_file_edits[i], 1);
}

static int
agent_edit_owner(struct agent_file_edit_entry *e, struct proc *p)
{
	return e && p && p->is_agent &&
	       e->scope_id == agent_identity_proc_scope(p) &&
	       e->owner_control_id != 0 &&
	       e->owner_control_id == p->agent_control_id;
}

static int
agent_edit_can_write(struct proc *p)
{
	return agent_identity_has_any_cap(p, AGENT_CAP_ARTIFACT_WRITE |
				    AGENT_CAP_ORCHESTRATE);
}

static void
agent_edit_fill_state_locked(struct agent_file_edit_state *state,
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

static void
agent_edit_audit(struct proc *p, int status, char *text,
		 struct agent_file_edit_state *state, int authority_effect)
{
	if (p == 0 || !p->is_agent || state == 0)
		return;
	agent_observe_record_effect(p, AGENT_TOOL_QUERY_FILE, status, text,
				    state->lease_id, state->current_version,
				    state->owner_pid, state->dev,
				    authority_effect);
}

static int
agent_edit_modify_allowed(struct inode *ip, char *action)
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
	now = agent_file_state_ticks();
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

int
agent_edit_write_allowed(struct inode *ip)
{
	return agent_edit_modify_allowed(ip, "edit_write_conflict");
}

int
agent_edit_truncate_allowed(struct inode *ip)
{
	return agent_edit_modify_allowed(ip, "edit_trunc_conflict");
}

int
agent_edit_unlink_allowed(struct inode *ip)
{
	return agent_edit_modify_allowed(ip, "edit_unlink_conflict");
}

static void
agent_edit_note_modify(struct inode *ip)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;
	int enabled;

	if (ip == 0)
		return;
	enabled = agent_edit_lock();
	agent_edit_cleanup_expired_locked(agent_file_state_ticks());
	edit = agent_edit_find_locked(ip->vfs_scope_id, ip->dev, ip->inum,
				      ip->vfs_incarnation);
	if (edit && agent_edit_owner(edit, p))
		edit->dirty = 1;
	else if (edit == 0)
		agent_edit_bump_version_locked(ip);
	agent_edit_unlock(enabled);
}

void
agent_edit_note_write(struct inode *ip)
{
	agent_edit_note_modify(ip);
}

void
agent_edit_note_truncate(struct inode *ip)
{
	agent_edit_note_modify(ip);
}

void
agent_edit_note_delete(struct inode *ip)
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

void
agent_file_state_scope_reclaim(uint scope_id)
{
	int enabled = agent_edit_lock();

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
}

int
agent_file_state_digest_cacheable(struct inode *ip)
{
	return ip != 0 && ip->agent_meta_slot > 0 &&
	       ip->agent_meta_version == AGENT_INODE_META_VERSION;
}

int
agent_file_state_digest_cache_lookup(struct inode *ip,
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
		safestrcpy(res->result, e->preview[0] ? e->preview :
						   "empty_file",
			   sizeof(res->result));
		agent_file_digest_cache_hits++;
		found = 1;
		break;
	}
	if (!found)
		agent_file_digest_cache_misses++;
	agent_edit_unlock(enabled);
	return found;
}

void
agent_file_state_digest_cache_store(struct inode *ip,
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

static int
agent_file_edit_lookup_path(struct proc *p, uint64 pathaddr, char *path,
			    struct inode **out,
			    enum vfs_operation operation)
{
	struct inode *ip;
	struct vfs_cred cred;

	if (copyinstr(p->pagetable, path, pathaddr, MAXPATH) < 0)
		return -1;
	path[MAXPATH - 1] = 0;
	if (path[0] == 0 || agent_file_state_reserved_path(path))
		return AGENT_STATUS_BAD_PARAM;
	ip = namei_scope(path, VFS_POLICY_WORKFLOW,
			 agent_identity_proc_scope(p));
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

static int
agent_file_edit_copy_state(struct proc *p, uint64 stateaddr,
			   struct agent_file_edit_state *state)
{
	if (stateaddr == 0)
		return 0;
	if (user_range_check(p->pagetable, stateaddr, sizeof(*state), PTE_W) < 0)
		return -1;
	return copyout(p->pagetable, stateaddr, (char *)state,
		       sizeof(*state));
}

int
sys_agent_file_edit_begin(uint64 pathaddr, uint64 flags, int ttl_ticks,
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

	now = agent_file_state_ticks();
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
	edit = agent_edit_find_locked(agent_identity_proc_scope(p), ip->dev,
				      ip->inum, ip->vfs_incarnation);
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
			if (agent_file_edit_copy_state(p, stateaddr, &state) < 0)
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

int
sys_agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
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
	now = agent_file_state_ticks();
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

int
sys_agent_file_edit_abort(uint64 lease_id)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;
	struct agent_file_edit_state state;
	int enabled;

	if (!p->is_agent)
		return -1;
	enabled = agent_edit_lock();
	agent_edit_cleanup_expired_locked(agent_file_state_ticks());
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

int
sys_agent_file_edit_state(uint64 pathaddr, uint64 stateaddr)
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
	agent_edit_cleanup_expired_locked(agent_file_state_ticks());
	agent_edit_version_inode_locked(ip, 1, &ok);
	if (!ok) {
		agent_edit_unlock(enabled);
		iput(ip);
		return AGENT_STATUS_NO_SPACE;
	}
	edit = agent_edit_find_locked(agent_identity_proc_scope(p), ip->dev,
				      ip->inum, ip->vfs_incarnation);
	agent_edit_fill_state_locked(&state, edit, ip->dev, ip->inum,
				     ip->vfs_incarnation, path);
	agent_edit_unlock(enabled);
	iput(ip);
	return agent_file_edit_copy_state(p, stateaddr, &state);
}

void
agent_file_state_fill_info(struct agent_info *info)
{
	if (info == 0)
		return;
	info->file_scan_generation = agent_file_generation;
	info->file_digest_cache_hits = agent_file_digest_cache_hits;
	info->file_digest_cache_misses = agent_file_digest_cache_misses;
}
