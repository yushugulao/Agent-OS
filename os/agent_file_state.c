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
#define AGENT_FILE_CACHE_SYSTEM_SLOT 0U
#define AGENT_FILE_CACHE_SCOPE_MAX (VFS_SCOPE_LIFECYCLE_CAP + 1U)

_Static_assert(AGENT_FILE_VERSION_MAX == NINODE,
	       "inode version sidecar must cover every filesystem inode");
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_EDIT_SCOPE_LIMIT <=
	       AGENT_FILE_EDIT_MAX,
	       "edit table must reserve every workflow partition");
_Static_assert(AGENT_FILE_CACHE_SCOPE_MAX ==
	       VFS_SCOPE_LIFECYCLE_CAP + 1U,
	       "cache index must cover every lifecycle and SYSTEM");

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
	struct workflow_lifecycle_key lifecycle;
	uint64 cache_generation;
};

struct file_version {
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
	uint64 edit_authority_generation;
	uint64 content_version;
	uint64 published_size;
	uint64 published_size_sequence;
	uint64 published_size_generation;
	uint64 published_size_tick;
	uint published_meta_slot;
	struct workflow_lifecycle_key published_lifecycle;
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

struct edit_call {
	struct proc *proc;
	struct inode *inode;
	struct agent_file_edit_entry *entry;
	struct agent_file_edit_state state;
	int enabled;
};

static struct agent_file_digest_cache_entry
	agent_file_digest_cache[AGENT_FILE_DIGEST_CACHE_MAX];
static struct agent_file_cache_scope_state
	agent_file_cache_scopes[AGENT_FILE_CACHE_SCOPE_MAX];
static struct file_version
	agent_file_versions[AGENT_FILE_VERSION_MAX];
static struct agent_file_edit_entry agent_file_edits[AGENT_FILE_EDIT_MAX];
static int agent_file_digest_cache_head;
static uint64 agent_file_digest_cache_hits;
static uint64 agent_file_digest_cache_misses;
static uint64 agent_file_generation;
static uint64 agent_file_system_generation;
static uint64 agent_file_content_generation;
static uint64 agent_file_size_sequence;
static volatile int agent_file_edit_guard;
static uint64 agent_file_edit_next_lease;

uint64
agent_file_state_now(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static uint64
agent_file_counter_next(uint64 *counter)
{
	return ++*counter ? *counter : (*counter = 1);
}

void
agent_file_state_project_hit(struct agent_file_hit *hit,
			     const struct agent_file_meta *meta, uint scope_id)
{
	struct agent_file_meta snapshot = *meta;

	agent_file_state_overlay_published_size(&snapshot, scope_id);
	memset(hit, 0, sizeof(*hit));
	hit->fid = snapshot.fid;
	safestrcpy(hit->physical_name, snapshot.physical_name,
		   sizeof(hit->physical_name));
	safestrcpy(hit->logical_path, snapshot.logical_path,
		   sizeof(hit->logical_path));
	safestrcpy(hit->stage, snapshot.stage, sizeof(hit->stage));
	safestrcpy(hit->kind, snapshot.kind, sizeof(hit->kind));
	safestrcpy(hit->status, snapshot.status, sizeof(hit->status));
	safestrcpy(hit->summary, snapshot.summary, sizeof(hit->summary));
	hit->dependency_mask = snapshot.dependency_mask;
	hit->dev = snapshot.dev;
	hit->inum = snapshot.inum;
	hit->incarnation = snapshot.incarnation;
	hit->size = snapshot.size;
	hit->fs_generation = snapshot.fs_generation;
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
	agent_file_system_generation = 0;
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
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	struct agent_file_cache_scope_state *state;
	uint slot;

	if (scope_id != VFS_SCOPE_SYSTEM &&
	    (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	     scope_id >= FS_OWNER_SCOPE_FLAG))
		scope_id = VFS_SCOPE_SYSTEM;
	if (scope_id == VFS_SCOPE_SYSTEM) {
		slot = AGENT_FILE_CACHE_SYSTEM_SLOT;
	} else {
		/* Lifecycle ids are immutable, bounded slots; the VFS remains the
		 * authority for their current scope and generation binding. */
		if (vfs_scope_lifecycle(scope_id, &lifecycle) < 0 ||
		    lifecycle.id == WORKFLOW_LIFECYCLE_ID_NONE ||
		    lifecycle.id > VFS_SCOPE_LIFECYCLE_CAP)
			return 0;
		slot = lifecycle.id;
	}
	state = &agent_file_cache_scopes[slot];
	if (state->used && state->scope_id == scope_id &&
	    workflow_lifecycle_key_equal(state->lifecycle, lifecycle))
		return state;
	if (!create)
		return 0;
	memset(state, 0, sizeof(*state));
	state->used = 1;
	state->scope_id = scope_id;
	state->lifecycle = lifecycle;
	state->cache_generation = scope_id == VFS_SCOPE_SYSTEM ?
		agent_file_system_generation : agent_file_generation;
	return state;
}

uint64
agent_file_state_scope_generation(uint scope_id)
{
	struct agent_file_cache_scope_state *state;
	uint64 generation;
	int enabled = intr_save();

	state = agent_file_cache_scope_locked(scope_id, 1);
	if (state == 0) {
		generation = agent_file_generation;
	} else if (state->scope_id == VFS_SCOPE_SYSTEM) {
		generation = agent_file_system_generation;
	} else {
		generation = MAX(state->cache_generation,
				 agent_file_system_generation);
	}
	intr_restore(enabled);
	return generation;
}

static uint64
agent_file_state_generation_next_capture(
	uint scope_id, struct workflow_lifecycle_key *lifecycle)
{
	struct agent_file_cache_scope_state *state;
	uint64 generation;
	int enabled = intr_save();

	if (lifecycle)
		*lifecycle = workflow_lifecycle_none();
	generation = agent_file_counter_next(&agent_file_generation);
	state = agent_file_cache_scope_locked(scope_id, 1);
	if (state && lifecycle)
		*lifecycle = state->lifecycle;
	if (state && state->scope_id == VFS_SCOPE_SYSTEM) {
		/* SYSTEM objects are visible in every workflow query. */
		agent_file_system_generation = generation;
	} else if (state) {
		state->cache_generation = generation;
	}
	intr_restore(enabled);
	return generation;
}

uint64
agent_file_state_generation_next(uint scope_id)
{
	return agent_file_state_generation_next_capture(scope_id, 0);
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

static void
file_version_clear_locked(int slot)
{
	struct file_version *entry;
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
		    agent_file_edits[i].incarnation == incarnation) {
			memset(&agent_file_edits[i], 0,
			       sizeof(agent_file_edits[i]));
		}
	for (int i = 0; i < AGENT_FILE_DIGEST_CACHE_MAX; i++)
		if (agent_file_digest_cache[i].valid &&
		    agent_file_digest_cache[i].dev == dev &&
		    agent_file_digest_cache[i].inum == inum &&
		    agent_file_digest_cache[i].incarnation == incarnation)
			memset(&agent_file_digest_cache[i], 0,
			       sizeof(agent_file_digest_cache[i]));
}

static struct file_version *
file_version_identity_locked(uint64 dev, uint64 inum, uint64 incarnation)
{
	struct file_version *entry;

	if (dev != ROOTDEV || inum == 0 || inum >= AGENT_FILE_VERSION_MAX)
		return 0;
	entry = &agent_file_versions[inum];
	if (!entry->used || entry->dev != dev || entry->inum != inum ||
	    entry->incarnation != incarnation)
		return 0;
	return entry;
}

static struct file_version *
file_version_inode_locked(struct inode *ip, int create)
{
	struct file_version *entry;

	if (ip == 0 || !ip->valid || ip->type != T_FILE ||
	    ip->dev != ROOTDEV || ip->inum == 0 ||
	    ip->inum >= AGENT_FILE_VERSION_MAX || ip->vfs_incarnation == 0 ||
	    ip->vfs_policy == VFS_POLICY_FREE ||
	    ip->fs_owner_domain < FS_OWNER_SYSTEM ||
	    ip->fs_owner_version != FS_OWNER_VERSION)
		return 0;
	entry = file_version_identity_locked(
		ip->dev, ip->inum, ip->vfs_incarnation);
	if (entry && entry->scope_id == ip->vfs_scope_id &&
	    entry->storage_owner == ip->fs_owner_domain &&
	    entry->vfs_policy == ip->vfs_policy)
		return entry;
	if (!create)
		return 0;

	/* A new identity retires every prior transient sidecar in this slot. */
	file_version_clear_locked(ip->inum);
	entry = &agent_file_versions[ip->inum];
	memset(entry, 0, sizeof(*entry));
	entry->used = 1;
	entry->scope_id = ip->vfs_scope_id;
	entry->dev = ip->dev;
	entry->inum = ip->inum;
	entry->incarnation = ip->vfs_incarnation;
	entry->storage_owner = ip->fs_owner_domain;
	entry->vfs_policy = ip->vfs_policy;
	return entry;
}

void
agent_file_state_content_bump(struct inode *ip)
{
	struct file_version *entry;
	int enabled;

	enabled = agent_edit_lock();
	entry = file_version_inode_locked(ip, 1);
	if (entry) {
		entry->content_version =
			agent_file_counter_next(&agent_file_content_generation);
	}
	agent_edit_unlock(enabled);
}

int
agent_file_state_content_publish(
	struct inode *ip, struct agent_file_content_receipt *receipt)
{
	struct file_version *entry;
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	int lifecycle_valid;
	int enabled;

	if (receipt)
		memset(receipt, 0, sizeof(*receipt));
	if (ip == 0)
		return 0;
	enabled = agent_edit_lock();
	entry = file_version_inode_locked(ip, 1);
	if (entry) {
		entry->content_version =
			agent_file_counter_next(&agent_file_content_generation);
		entry->published_size_valid = 1;
		entry->published_size_dirty = 1;
		entry->published_size = ip->size;
		entry->published_size_sequence =
			agent_file_counter_next(&agent_file_size_sequence);
		entry->published_size_generation =
			agent_file_state_generation_next_capture(
				ip->vfs_scope_id, &lifecycle);
		entry->published_size_tick = agent_file_state_now();
		lifecycle_valid = ip->vfs_scope_id == VFS_SCOPE_SYSTEM ||
			workflow_lifecycle_key_valid(lifecycle);
		entry->published_meta_slot = AGENT_FILE_META_MAX;
		entry->published_lifecycle = workflow_lifecycle_none();
		if (lifecycle_valid && ip->agent_meta_slot > 0 &&
		    ip->agent_meta_slot <= AGENT_FILE_META_MAX &&
		    ip->agent_meta_version == AGENT_INODE_META_VERSION &&
		    (ip->agent_meta_flags & AGENT_FILE_META_F_PERSIST) != 0) {
			entry->published_meta_slot = ip->agent_meta_slot - 1;
			entry->published_lifecycle = lifecycle;
			if (receipt) {
				receipt->sequence =
					entry->published_size_sequence;
				receipt->dev = entry->dev;
				receipt->inum = entry->inum;
				receipt->incarnation = entry->incarnation;
				receipt->scope_id = entry->scope_id;
				receipt->slot = entry->published_meta_slot;
				receipt->lifecycle = lifecycle;
			}
		}
	}
	agent_edit_unlock(enabled);
	return entry != 0;
}

static void
agent_file_overlay_published_size_locked(struct agent_file_meta *meta,
					 uint scope_id)
{
	struct file_version *entry;

	if (meta == 0)
		return;
	entry = file_version_identity_locked(
		meta->dev, meta->inum, meta->incarnation);
	if (entry == 0)
		return;
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
agent_file_state_snapshot_overlay_receipt(
	struct agent_file_meta *meta, uint scope_id, uint slot,
	struct workflow_lifecycle_key lifecycle,
	struct agent_file_content_receipt *receipt)
{
	struct file_version *entry;

	if (receipt)
		memset(receipt, 0, sizeof(*receipt));
	if (meta == 0)
		return;
	entry = file_version_identity_locked(
		meta->dev, meta->inum, meta->incarnation);
	if (entry == 0 || !entry->published_size_valid ||
	    entry->scope_id != scope_id || entry->published_meta_slot != slot ||
	    !workflow_lifecycle_key_equal(
		    entry->published_lifecycle, lifecycle))
		return;
	agent_file_overlay_published_size_locked(meta, scope_id);
	if (receipt == 0 || !entry->published_size_dirty)
		return;
	receipt->sequence = entry->published_size_sequence;
	receipt->dev = entry->dev;
	receipt->inum = entry->inum;
	receipt->incarnation = entry->incarnation;
	receipt->scope_id = scope_id;
	receipt->slot = slot;
	receipt->lifecycle = lifecycle;
}

void
agent_file_state_snapshot_end(int enabled)
{
	agent_edit_unlock(enabled);
}

void
agent_file_state_content_settle(
	const struct agent_file_content_receipt *receipt)
{
	struct file_version *entry;
	int enabled;

	if (receipt == 0 || receipt->sequence == 0)
		return;
	enabled = agent_edit_lock();
	entry = file_version_identity_locked(
		receipt->dev, receipt->inum, receipt->incarnation);
	if (entry != 0 && entry->published_size_dirty &&
	    entry->scope_id == receipt->scope_id &&
	    entry->published_meta_slot == receipt->slot &&
	    workflow_lifecycle_key_equal(
		    entry->published_lifecycle, receipt->lifecycle) &&
	    entry->published_size_sequence == receipt->sequence)
		entry->published_size_dirty = 0;
	agent_edit_unlock(enabled);
}

void
agent_file_version_reclaim(struct inode *ip)
{
	struct file_version *entry;
	int enabled;

	if (ip == 0)
		return;
	enabled = agent_edit_lock();
	entry = file_version_identity_locked(
		ip->dev, ip->inum, ip->vfs_incarnation);
	if (entry)
		file_version_clear_locked(ip->inum);
	agent_edit_unlock(enabled);
}

static struct agent_file_edit_entry *
agent_edit_find_locked(uint scope_id, uint64 lease_id, struct inode *ip)
{
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++) {
		struct agent_file_edit_entry *e = &agent_file_edits[i];

		/* NULL inode selects the scoped lease key instead. */
		if (e->active && e->scope_id == scope_id &&
		    (ip ? e->dev == ip->dev && e->inum == ip->inum &&
			  e->incarnation == ip->vfs_incarnation :
			  e->lease_id == lease_id))
			return e;
	}
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

static void
agent_edit_release_locked(struct agent_file_edit_entry *e, int publish_dirty)
{
	struct file_version *version;

	if (e == 0 || !e->active)
		return;
	version = file_version_identity_locked(e->dev, e->inum, e->incarnation);
	if (version) {
		if (publish_dirty && e->dirty)
			version->edit_version = e->base_version + 1;
		agent_file_counter_next(&version->edit_authority_generation);
	}
	memset(e, 0, sizeof(*e));
}

static void
agent_edit_cleanup_expired_locked(uint64 now)
{
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_file_edits[i].active &&
		    agent_file_edits[i].deadline_tick != 0 &&
		    now >= agent_file_edits[i].deadline_tick)
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

static void
edit_state_locked(struct agent_file_edit_state *state,
		  struct agent_file_edit_entry *e, struct inode *ip,
		  char *path, int finished)
{
	struct file_version *version;

	memset(state, 0, sizeof(*state));
	if (e) {
		state->dev = e->dev;
		state->inum = e->inum;
		state->incarnation = e->incarnation;
	} else {
		state->dev = ip->dev;
		state->inum = ip->inum;
		state->incarnation = ip->vfs_incarnation;
	}
	version = file_version_identity_locked(
		state->dev, state->inum, state->incarnation);
	state->current_version = version ? version->edit_version : 0;
	if (e && e->active && e->path[0])
		path = e->path;
	if (path)
		safestrcpy(state->path, path, sizeof(state->path));
	if (e == 0 || !e->active)
		return;
	state->lease_id = e->lease_id;
	state->base_version = e->base_version;
	state->dirty = e->dirty;
	if (finished)
		return;
	state->active = 1;
	state->owner_pid = e->owner_pid;
	state->owner_agent_id = e->owner_agent_id;
	state->owner_role = e->owner_role;
	state->deadline_tick = e->deadline_tick;
	state->conflict_count = e->conflict_count;
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
agent_edit_modify_allowed(struct inode *ip, char *action,
			  uint64 *authority_generation,
			  uint64 *valid_until_tick)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;
	struct agent_file_edit_state state;
	struct file_version *version;
	uint64 now;
	int allowed = 1;
	int enabled;

	if (authority_generation)
		*authority_generation = 0;
	if (valid_until_tick)
		*valid_until_tick = 0;
	if (ip == 0)
		return 0;
	if (ip->vfs_policy != VFS_POLICY_WORKFLOW)
		return 1;
	enabled = agent_edit_lock();
	now = agent_file_state_now();
	agent_edit_cleanup_expired_locked(now);
	version = file_version_inode_locked(ip, 0);
	if (authority_generation && version)
		*authority_generation = version->edit_authority_generation;
	edit = agent_edit_find_locked(ip->vfs_scope_id, 0, ip);
	if (edit && !agent_edit_owner(edit, p)) {
		if (action) {
			edit->conflict_count++;
			edit_state_locked(&state, edit, ip, 0, 0);
		}
		allowed = 0;
	} else if (edit && valid_until_tick) {
		*valid_until_tick = edit->deadline_tick;
	}
	agent_edit_unlock(enabled);
	if (!allowed && action)
		agent_edit_audit(p, AGENT_STATUS_CONFLICT, action, &state, 0);
	return allowed;
}

#define DEFINE_AGENT_EDIT_ALLOWED(operation, event) \
	int agent_edit_##operation##_allowed(struct inode *ip) \
	{ return agent_edit_modify_allowed(ip, event, 0, 0); }

DEFINE_AGENT_EDIT_ALLOWED(write, "edit_write_conflict")
DEFINE_AGENT_EDIT_ALLOWED(truncate, "edit_trunc_conflict")
DEFINE_AGENT_EDIT_ALLOWED(unlink, "edit_unlink_conflict")
#undef DEFINE_AGENT_EDIT_ALLOWED

int
agent_edit_write_lease_allowed(struct inode *ip,
			       uint64 *authority_generation,
			       uint64 *valid_until_tick)
{
	return agent_edit_modify_allowed(
		ip, "edit_write_conflict", authority_generation,
		valid_until_tick);
}

int
agent_edit_write_lease_snapshot(struct inode *ip,
				uint64 *authority_generation,
				uint64 *valid_until_tick)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;
	struct file_version *version;
	int allowed = 1;
	int enabled;

	if (authority_generation)
		*authority_generation = 0;
	if (valid_until_tick)
		*valid_until_tick = 0;
	if (ip == 0)
		return 0;
	if (ip->vfs_policy != VFS_POLICY_WORKFLOW)
		return 1;
	enabled = agent_edit_lock();
	agent_edit_cleanup_expired_locked(agent_file_state_now());
	version = file_version_inode_locked(ip, 0);
	if (authority_generation && version)
		*authority_generation = version->edit_authority_generation;
	edit = agent_edit_find_locked(ip->vfs_scope_id, 0, ip);
	if (edit && !agent_edit_owner(edit, p))
		allowed = 0;
	else if (edit && valid_until_tick)
		*valid_until_tick = edit->deadline_tick;
	agent_edit_unlock(enabled);
	return allowed;
}

static void
agent_edit_note_modify(struct inode *ip)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;
	struct file_version *version;
	int enabled;

	if (ip == 0)
		return;
	if (ip->vfs_policy != VFS_POLICY_WORKFLOW)
		return;
	enabled = agent_edit_lock();
	agent_edit_cleanup_expired_locked(agent_file_state_now());
	edit = agent_edit_find_locked(ip->vfs_scope_id, 0, ip);
	if (edit && agent_edit_owner(edit, p))
		edit->dirty = 1;
	else if (edit == 0) {
		version = file_version_inode_locked(ip, 1);
		if (version)
			version->edit_version++;
	}
	agent_edit_unlock(enabled);
}

#define DEFINE_AGENT_EDIT_NOTE(operation) \
	void agent_edit_note_##operation(struct inode *ip) \
	{ agent_edit_note_modify(ip); }

DEFINE_AGENT_EDIT_NOTE(write)
DEFINE_AGENT_EDIT_NOTE(truncate)
#undef DEFINE_AGENT_EDIT_NOTE

void
agent_edit_note_delete(struct inode *ip)
{
	struct agent_file_edit_entry *edit;
	struct file_version *version;
	int enabled;

	if (ip == 0)
		return;
	if (ip->vfs_policy != VFS_POLICY_WORKFLOW)
		return;
	enabled = agent_edit_lock();
	edit = agent_edit_find_locked(ip->vfs_scope_id, 0, ip);
	if (edit)
		agent_edit_release_locked(edit, 1);
	else {
		version = file_version_inode_locked(ip, 1);
		if (version)
			version->edit_version++;
	}
	agent_edit_unlock(enabled);
}

void
agent_file_state_scope_reclaim(uint scope_id)
{
	struct agent_file_cache_scope_state *scope_state;
	int enabled = agent_edit_lock();

	scope_state = agent_file_cache_scope_locked(scope_id, 0);
	if (scope_state != 0)
		memset(scope_state, 0, sizeof(*scope_state));

#define CLEAR_SCOPED(array, count, present) do { \
	for (int i = 0; i < (count); i++) \
		if ((array)[i].present && (array)[i].scope_id == scope_id) \
			memset(&(array)[i], 0, sizeof((array)[i])); \
} while (0)
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_file_edits[i].active &&
		    agent_file_edits[i].scope_id == scope_id) {
			memset(&agent_file_edits[i], 0,
			       sizeof(agent_file_edits[i]));
		}
	CLEAR_SCOPED(agent_file_digest_cache, AGENT_FILE_DIGEST_CACHE_MAX, valid);
	CLEAR_SCOPED(agent_file_versions, AGENT_FILE_VERSION_MAX, used);
#undef CLEAR_SCOPED
	agent_edit_unlock(enabled);
}

int
agent_file_state_index_deferred(struct inode *ip)
{
	return ip != 0 &&
	       ip->agent_meta_slot == AGENT_INODE_META_DEFERRED_SLOT &&
	       ip->agent_meta_flags == 0 &&
	       ip->agent_meta_version == AGENT_INODE_META_VERSION;
}

int
agent_file_state_set_index(struct inode *ip, short slot, short flags, int stale)
{
	short old_slot, old_flags, old_version;
	short version = slot ? AGENT_INODE_META_VERSION : 0;

	if (ip == 0 || slot < AGENT_INODE_META_DEFERRED_SLOT ||
	    slot > AGENT_FILE_META_MAX || (slot <= 0 && flags) ||
	    (slot == AGENT_INODE_META_DEFERRED_SLOT &&
	    !stale && ip->agent_meta_slot > 0))
		return -1;
	if (ip->agent_meta_slot == slot && ip->agent_meta_flags == flags &&
	    ip->agent_meta_version == version)
		return 0;
	old_slot = ip->agent_meta_slot;
	old_flags = ip->agent_meta_flags;
	old_version = ip->agent_meta_version;
	ip->agent_meta_slot = slot;
	ip->agent_meta_flags = flags;
	ip->agent_meta_version = version;
	if (iupdate(ip) >= 0)
		return 0;
	ip->agent_meta_slot = old_slot;
	ip->agent_meta_flags = old_flags;
	ip->agent_meta_version = old_version;
	return -1;
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
	struct file_version *version;
	int found = 0;
	int enabled;

	enabled = agent_edit_lock();
	version = content_generation ? file_version_inode_locked(ip, 1) : 0;
	if (version == 0) {
		agent_file_digest_cache_misses++;
		agent_edit_unlock(enabled);
		return 0;
	}
	*content_generation = version->content_version;
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
	struct file_version *version;
	int enabled;

	if (ip == 0 || ip->agent_meta_slot <= 0 ||
	    ip->agent_meta_version != AGENT_INODE_META_VERSION)
		return;
	enabled = agent_edit_lock();
	version = file_version_inode_locked(ip, 0);
	if (version == 0 || version->content_version != expected_generation ||
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
	e->content_generation = version->content_version;
	e->bytes = bytes;
	e->hash = hash;
	safestrcpy(e->preview, preview[0] ? preview : "empty_file",
		   sizeof(e->preview));
	agent_edit_unlock(enabled);
}

static int
edit_lookup_path(struct proc *p, uint64 pathaddr, char *path,
		 struct inode **out, enum vfs_operation operation)
{
	struct inode *ip;
	struct vfs_cred cred;
	int lookup_status;
	int result;

	if (copyinstr(p->pagetable, path, pathaddr, MAXPATH) < 0)
		return -1;
	path[MAXPATH - 1] = 0;
	if (path[0] == 0 || agent_file_state_reserved_path(path))
		return AGENT_STATUS_BAD_PARAM;
	ip = namei_scope_status(path, VFS_POLICY_WORKFLOW,
			       agent_identity_proc_scope(p), &lookup_status);
	if (ip == 0) {
		if (lookup_status == FS_LOOKUP_ABSENT)
			return AGENT_STATUS_NOT_FOUND;
		return lookup_status == FS_LOOKUP_BUSY ? AGENT_STATUS_RETRY :
						       AGENT_STATUS_IO_ERROR;
	}
	result = ivalid(ip);
	if (result < 0) {
		iput(ip);
		return result == FS_LOOKUP_BUSY ? AGENT_STATUS_RETRY :
						 AGENT_STATUS_IO_ERROR;
	}
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
edit_copy_state(struct proc *p, uint64 stateaddr,
		struct agent_file_edit_state *state)
{
	if (stateaddr == 0)
		return 0;
	if (user_range_check(p->pagetable, stateaddr, sizeof(*state), PTE_W) < 0)
		return -1;
	return copyout(p->pagetable, stateaddr, (char *)state,
		       sizeof(*state));
}

static int
edit_call_init(struct edit_call *call)
{
	call->proc = curr_proc();
	call->inode = 0;
	return call->proc->is_agent ? 0 : -1;
}

static int
edit_call_output(struct edit_call *call, uint64 stateaddr,
		 int required)
{
	if (stateaddr == 0)
		return required ? -1 : 0;
	return user_range_check(call->proc->pagetable, stateaddr,
				sizeof(call->state), PTE_W);
}

static int
edit_reply(struct edit_call *call, int status, char *event,
	   uint64 stateaddr)
{
	agent_edit_unlock(call->enabled);
	if (call->inode)
		iput(call->inode);
	/* Only successful edit events describe an authority-changing effect. */
	if (event)
		agent_edit_audit(call->proc, status, event, &call->state,
				 status == AGENT_STATUS_OK);
	if (edit_copy_state(call->proc, stateaddr, &call->state) < 0)
		return -1;
	return status;
}

static int
edit_lease_locked(struct edit_call *call, uint64 lease_id,
		  char *denied_event, uint64 stateaddr)
{
	call->enabled = agent_edit_lock();
	agent_edit_cleanup_expired_locked(agent_file_state_now());
	call->entry = agent_edit_find_locked(
		agent_identity_proc_scope(call->proc), lease_id, 0);
	if (call->entry == 0)
		return edit_reply(call, AGENT_STATUS_NOT_FOUND, 0, 0);
	if (agent_edit_owner(call->entry, call->proc) ||
	    agent_identity_has_cap(call->proc, AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_OK;
	edit_state_locked(&call->state, call->entry, 0, call->entry->path, 0);
	return edit_reply(call, AGENT_STATUS_DENIED, denied_event, stateaddr);
}

int
sys_agent_file_edit_begin(uint64 pathaddr, uint64 flags, int ttl_ticks,
			  uint64 stateaddr)
{
	struct edit_call call;
	struct file_version *version_entry;
	char path[MAXPATH];
	uint64 now;
	uint64 version;
	int rc;
	int ttl;

	if (edit_call_init(&call) < 0)
		return -1;
	if (!agent_identity_has_any_cap(call.proc,
		AGENT_CAP_ARTIFACT_WRITE | AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (edit_call_output(&call, stateaddr, 0) < 0)
		return -1;
	rc = edit_lookup_path(call.proc, pathaddr, path, &call.inode,
			      VFS_OP_WRITE);
	if (rc < 0)
		return rc;

	now = agent_file_state_now();
	ttl = ttl_ticks <= 0 ? AGENT_FILE_EDIT_DEFAULT_TTL : ttl_ticks;
	if (ttl > AGENT_FILE_EDIT_MAX_TTL)
		ttl = AGENT_FILE_EDIT_MAX_TTL;

	call.enabled = agent_edit_lock();
	agent_edit_cleanup_expired_locked(now);
	version_entry = file_version_inode_locked(call.inode, 1);
	if (version_entry == 0)
		return edit_reply(&call, AGENT_STATUS_NO_SPACE, 0, 0);
	version = version_entry->edit_version;
	call.entry = agent_edit_find_locked(
		agent_identity_proc_scope(call.proc), 0, call.inode);
	if (call.entry) {
		if ((flags & AGENT_FILE_EDIT_F_ORCHESTRATOR_BREAK) &&
		    agent_identity_has_cap(call.proc, AGENT_CAP_ORCHESTRATE)) {
			agent_edit_release_locked(call.entry, 1);
			version_entry = file_version_identity_locked(
				call.inode->dev, call.inode->inum,
				call.inode->vfs_incarnation);
			if (version_entry == 0)
				return edit_reply(
					&call, AGENT_STATUS_NO_SPACE, 0, 0);
			version = version_entry->edit_version;
		} else {
			call.entry->conflict_count++;
			edit_state_locked(
				&call.state, call.entry, call.inode, path, 0);
			return edit_reply(&call,
				AGENT_STATUS_CONFLICT, "edit_begin_conflict",
				stateaddr);
		}
	}
	call.entry = agent_edit_free_locked(agent_identity_proc_scope(call.proc));
	if (call.entry == 0)
		return edit_reply(&call, AGENT_STATUS_NO_SPACE, 0, 0);
	memset(call.entry, 0, sizeof(*call.entry));
	call.entry->active = 1;
	call.entry->scope_id = agent_identity_proc_scope(call.proc);
	call.entry->owner_pid = call.proc->pid;
	call.entry->owner_agent_id = call.proc->agent_id;
	call.entry->owner_role = call.proc->agent_role;
	call.entry->owner_control_id = call.proc->agent_control_id;
	call.entry->lease_id = agent_file_edit_next_lease++;
	if (agent_file_edit_next_lease == 0)
		agent_file_edit_next_lease = 1;
	call.entry->dev = call.inode->dev;
	call.entry->inum = call.inode->inum;
	call.entry->incarnation = call.inode->vfs_incarnation;
	call.entry->base_version = version;
	call.entry->deadline_tick = now + ttl;
	safestrcpy(call.entry->path, path, sizeof(call.entry->path));
	agent_file_counter_next(&version_entry->edit_authority_generation);
	edit_state_locked(&call.state, call.entry, call.inode, path, 0);
	return edit_reply(&call, AGENT_STATUS_OK, "edit_begin", stateaddr);
}

int
sys_agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
			   uint64 stateaddr)
{
	struct edit_call call;
	struct file_version *version;
	uint64 new_version;
	int rc;

	if (edit_call_init(&call) < 0)
		return -1;
	if (edit_call_output(&call, stateaddr, 0) < 0)
		return -1;
	rc = edit_lease_locked(
		&call, lease_id, "edit_commit_denied", stateaddr);
	if (rc != AGENT_STATUS_OK)
		return rc;
	version = file_version_identity_locked(
		call.entry->dev, call.entry->inum, call.entry->incarnation);
	if (version == 0 || version->edit_version != call.entry->base_version ||
	    expected_version != call.entry->base_version) {
		edit_state_locked(
			&call.state, call.entry, 0, call.entry->path, 0);
		return edit_reply(&call, AGENT_STATUS_STALE,
			"edit_commit_stale", stateaddr);
	}
	new_version = call.entry->dirty ? call.entry->base_version + 1 :
					call.entry->base_version;
	version->edit_version = new_version;
	agent_file_counter_next(&version->edit_authority_generation);
	edit_state_locked(&call.state, call.entry, 0, call.entry->path, 1);
	memset(call.entry, 0, sizeof(*call.entry));
	return edit_reply(&call, AGENT_STATUS_OK, "edit_commit", stateaddr);
}

int
sys_agent_file_edit_abort(uint64 lease_id)
{
	struct edit_call call;
	int rc;

	if (edit_call_init(&call) < 0)
		return -1;
	rc = edit_lease_locked(&call, lease_id, "edit_abort_denied", 0);
	if (rc != AGENT_STATUS_OK)
		return rc;
	edit_state_locked(&call.state, call.entry, 0, call.entry->path, 0);
	agent_edit_release_locked(call.entry, 1);
	return edit_reply(&call, AGENT_STATUS_OK, "edit_abort", 0);
}

int
sys_agent_file_edit_state(uint64 pathaddr, uint64 stateaddr)
{
	struct edit_call call;
	char path[MAXPATH];
	int rc;

	if (edit_call_init(&call) < 0)
		return -1;
	if (edit_call_output(&call, stateaddr, 1) < 0)
		return -1;
	rc = edit_lookup_path(call.proc, pathaddr, path, &call.inode,
			      VFS_OP_LOOKUP);
	if (rc < 0)
		return rc;
	call.enabled = agent_edit_lock();
	agent_edit_cleanup_expired_locked(agent_file_state_now());
	if (file_version_inode_locked(call.inode, 1) == 0)
		return edit_reply(&call, AGENT_STATUS_NO_SPACE, 0, 0);
	call.entry = agent_edit_find_locked(
		agent_identity_proc_scope(call.proc), 0, call.inode);
	edit_state_locked(&call.state, call.entry, call.inode, path, 0);
	return edit_reply(&call, AGENT_STATUS_OK, 0, stateaddr);
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
