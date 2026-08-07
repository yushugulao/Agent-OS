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
 * 工作流文件的临时状态绑定到完整 inode 身份。目录槽仍由
 * agent_metadata_objects 管理；本模块只保存版本、租约、已发布大小、
 * 摘要缓存，以及用于使读者缓存失效的代际。
 */
#define AGENT_FILE_DIGEST_CACHE_MAX 8
#define AGENT_FILE_EDIT_MAX 32
#define AGENT_FILE_VERSION_MAX AGENT_FILE_META_MAX
#define AGENT_FILE_VERSION_SYSTEM_RESIDENT (AGENT_FILE_VERSION_MAX / 8U)
#define AGENT_FILE_VERSION_SCOPE_RESIDENT \
	((AGENT_FILE_VERSION_MAX - AGENT_FILE_VERSION_SYSTEM_RESIDENT) / \
	 VFS_SCOPE_MAX_ACTIVE)
#define AGENT_EDIT_SCOPE_LIMIT 8
#define AGENT_FILE_CACHE_SYSTEM_SLOT 0U
#define AGENT_FILE_CACHE_SCOPE_MAX (VFS_SCOPE_MAX_ACTIVE + 1U)

_Static_assert(AGENT_FILE_VERSION_MAX > 0,
	       "文件版本表必须保留驻留槽");
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_EDIT_SCOPE_LIMIT <=
	       AGENT_FILE_EDIT_MAX,
	       "编辑表必须为每个工作流分区保留容量");
_Static_assert(AGENT_FILE_VERSION_SYSTEM_RESIDENT +
	       VFS_SCOPE_MAX_ACTIVE * AGENT_FILE_VERSION_SCOPE_RESIDENT ==
	       AGENT_FILE_VERSION_MAX,
	       "文件版本分区必须完整覆盖系统和工作流槽");
_Static_assert(AGENT_FILE_CACHE_SCOPE_MAX == VFS_SCOPE_MAX_ACTIVE + 1U,
	       "版本 bank 必须覆盖全部已接纳工作流和 SYSTEM");

struct agent_file_digest_cache_entry {
	int valid;
	uint scope_id;
	struct workflow_lifecycle_key lifecycle;
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
	uint version_count;
	uint version_cursor;
};

struct file_version {
	int published_size_valid;
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
	struct workflow_lifecycle_key identity_lifecycle;
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
	struct workflow_lifecycle_key lifecycle;
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
static uint64 agent_file_edit_version_generation;
static uint64 agent_file_edit_authority_generation;
static volatile int agent_file_edit_guard;
static uint64 agent_file_edit_next_lease;

static int agent_edit_lock(void);
static void agent_edit_unlock(int enabled);

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

static uint64
file_version_edit_next_locked(struct file_version *entry)
{
	uint64 next = agent_file_counter_next(&entry->edit_version);

	if (next > agent_file_edit_version_generation)
		agent_file_edit_version_generation = next;
	return next;
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
	agent_file_edit_version_generation = 0;
	agent_file_edit_authority_generation = 0;
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
file_version_scope_state_locked(uint scope_id,
				struct workflow_lifecycle_key lifecycle,
				int create)
{
	struct agent_file_cache_scope_state *state, *free_state = 0;

	if (scope_id == VFS_SCOPE_SYSTEM) {
		if (!workflow_lifecycle_key_equal(
			    lifecycle, workflow_lifecycle_none()))
			return 0;
		state = &agent_file_cache_scopes[AGENT_FILE_CACHE_SYSTEM_SLOT];
	} else {
		if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
		    scope_id >= FS_OWNER_SCOPE_FLAG ||
		    !workflow_lifecycle_key_valid(lifecycle))
			return 0;
		state = 0;
		for (uint slot = 1; slot < AGENT_FILE_CACHE_SCOPE_MAX; slot++) {
			struct agent_file_cache_scope_state *candidate =
				&agent_file_cache_scopes[slot];

			if (candidate->used && candidate->scope_id == scope_id &&
			    workflow_lifecycle_key_equal(
				    candidate->lifecycle, lifecycle))
				return candidate;
			if (!candidate->used && free_state == 0)
				free_state = candidate;
		}
		state = free_state;
	}
	if (state == 0)
		return 0;
	if (state->used && state->scope_id == scope_id &&
	    workflow_lifecycle_key_equal(state->lifecycle, lifecycle))
		return state;
	if (!create || state->used)
		return 0;
	memset(state, 0, sizeof(*state));
	state->used = 1;
	state->scope_id = scope_id;
	state->lifecycle = lifecycle;
	state->cache_generation = scope_id == VFS_SCOPE_SYSTEM ?
		agent_file_system_generation : agent_file_generation;
	return state;
}

static struct agent_file_cache_scope_state *
agent_file_cache_scope_locked(uint scope_id, int create)
{
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();

	if (scope_id != VFS_SCOPE_SYSTEM &&
	    (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	     scope_id >= FS_OWNER_SCOPE_FLAG))
		scope_id = VFS_SCOPE_SYSTEM;
	if (scope_id != VFS_SCOPE_SYSTEM &&
	    vfs_scope_lifecycle(scope_id, &lifecycle) < 0)
		return 0;
	return file_version_scope_state_locked(scope_id, lifecycle, create);
}

uint64
agent_file_state_scope_generation(uint scope_id)
{
	struct agent_file_cache_scope_state *state;
	uint64 generation;
	int enabled = agent_edit_lock();

	state = agent_file_cache_scope_locked(scope_id, 1);
	if (state == 0) {
		generation = agent_file_generation;
	} else if (state->scope_id == VFS_SCOPE_SYSTEM) {
		generation = agent_file_system_generation;
	} else {
		generation = MAX(state->cache_generation,
				 agent_file_system_generation);
	}
	agent_edit_unlock(enabled);
	return generation;
}

static uint64
agent_file_state_generation_next_capture_locked(
	uint scope_id, struct workflow_lifecycle_key *lifecycle)
{
	struct agent_file_cache_scope_state *state;
	uint64 generation;

	if (lifecycle)
		*lifecycle = workflow_lifecycle_none();
	generation = agent_file_counter_next(&agent_file_generation);
	state = agent_file_cache_scope_locked(scope_id, 1);
	if (state && lifecycle)
		*lifecycle = state->lifecycle;
	if (state && state->scope_id == VFS_SCOPE_SYSTEM) {
		/* SYSTEM 对象对全部工作流查询可见。 */
		agent_file_system_generation = generation;
	} else if (state) {
		state->cache_generation = generation;
	}
	return generation;
}

uint64
agent_file_state_generation_next(uint scope_id)
{
	uint64 generation;
	int enabled = agent_edit_lock();

	generation = agent_file_state_generation_next_capture_locked(scope_id, 0);
	agent_edit_unlock(enabled);
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
file_version_current_lifecycle(uint scope_id,
			       struct workflow_lifecycle_key *lifecycle)
{
	*lifecycle = workflow_lifecycle_none();
	if (scope_id == VFS_SCOPE_NONE || scope_id == VFS_SCOPE_SYSTEM)
		return 0;
	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG ||
	    vfs_scope_lifecycle(scope_id, lifecycle) < 0 ||
	    !workflow_lifecycle_key_valid(*lifecycle))
		return -1;
	return 0;
}

static int
file_version_identity_valid(uint64 dev, uint64 inum, uint64 incarnation,
			     uint scope_id,
			     struct workflow_lifecycle_key lifecycle)
{
	if (dev != ROOTDEV || inum == 0 || incarnation == 0)
		return 0;
	if (scope_id == VFS_SCOPE_NONE || scope_id == VFS_SCOPE_SYSTEM)
		return workflow_lifecycle_key_equal(
			lifecycle, workflow_lifecycle_none());
	return scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
	       scope_id < FS_OWNER_SCOPE_FLAG &&
	       workflow_lifecycle_key_valid(lifecycle);
}

static void
file_version_bank_bounds(const struct agent_file_cache_scope_state *state,
			 uint *start, uint *capacity)
{
	uint bank = state - agent_file_cache_scopes;

	if (bank >= AGENT_FILE_CACHE_SCOPE_MAX)
		panic("Agent file version bank");
	*start = bank == AGENT_FILE_CACHE_SYSTEM_SLOT ? 0 :
		 AGENT_FILE_VERSION_SYSTEM_RESIDENT +
		 (bank - 1) * AGENT_FILE_VERSION_SCOPE_RESIDENT;
	*capacity = bank == AGENT_FILE_CACHE_SYSTEM_SLOT ?
		AGENT_FILE_VERSION_SYSTEM_RESIDENT :
		AGENT_FILE_VERSION_SCOPE_RESIDENT;
}

static int
file_version_compare(const struct file_version *entry, uint64 dev,
		     uint64 inum, uint64 incarnation)
{
	if (entry->dev != dev)
		return entry->dev < dev ? -1 : 1;
	if (entry->inum != inum)
		return entry->inum < inum ? -1 : 1;
	if (entry->incarnation != incarnation)
		return entry->incarnation < incarnation ? -1 : 1;
	return 0;
}

/* 每个已接纳 scope 独占一个有序 bank；热查找只需二分本 bank。 */
static struct file_version *
file_version_search_locked(struct agent_file_cache_scope_state *state,
			   uint64 dev, uint64 inum, uint64 incarnation,
			   uint *position)
{
	uint start, capacity, low = 0, high = state->version_count;

	file_version_bank_bounds(state, &start, &capacity);
	if (!state->used || high > capacity)
		panic("Agent file version count");
	while (low < high) {
		uint middle = low + (high - low) / 2;
		int order = file_version_compare(
			&agent_file_versions[start + middle],
			dev, inum, incarnation);

		if (order < 0)
			low = middle + 1;
		else
			high = middle;
	}
	if (position)
		*position = low;
	if (low < state->version_count &&
	    file_version_compare(&agent_file_versions[start + low],
				 dev, inum, incarnation) == 0)
		return &agent_file_versions[start + low];
	return 0;
}

static struct file_version *
file_version_identity_locked(uint64 dev, uint64 inum, uint64 incarnation,
			     uint scope_id,
			     struct workflow_lifecycle_key lifecycle)
{
	struct agent_file_cache_scope_state *state;

	if (!file_version_identity_valid(
		    dev, inum, incarnation, scope_id, lifecycle))
		return 0;
	state = file_version_scope_state_locked(scope_id, lifecycle, 0);
	return state ? file_version_search_locked(
		state, dev, inum, incarnation, 0) : 0;
}

static struct file_version *
file_version_current_identity_locked(uint64 dev, uint64 inum,
				     uint64 incarnation, uint scope_id)
{
	struct workflow_lifecycle_key lifecycle;

	if (file_version_current_lifecycle(scope_id, &lifecycle) < 0)
		return 0;
	return file_version_identity_locked(
		dev, inum, incarnation, scope_id, lifecycle);
}

static void
file_version_digest_clear_locked(const struct file_version *entry)
{
	for (int i = 0; i < AGENT_FILE_DIGEST_CACHE_MAX; i++)
		if (agent_file_digest_cache[i].valid &&
		    agent_file_digest_cache[i].scope_id == entry->scope_id &&
		    workflow_lifecycle_key_equal(
			    agent_file_digest_cache[i].lifecycle,
			    entry->identity_lifecycle) &&
		    agent_file_digest_cache[i].dev == entry->dev &&
		    agent_file_digest_cache[i].inum == entry->inum &&
		    agent_file_digest_cache[i].incarnation == entry->incarnation)
			memset(&agent_file_digest_cache[i], 0,
			       sizeof(agent_file_digest_cache[i]));
}

static int
file_version_has_edit_locked(const struct file_version *entry)
{
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_file_edits[i].active &&
		    agent_file_edits[i].scope_id == entry->scope_id &&
		    workflow_lifecycle_key_equal(
			    agent_file_edits[i].lifecycle,
			    entry->identity_lifecycle) &&
		    agent_file_edits[i].dev == entry->dev &&
		    agent_file_edits[i].inum == entry->inum &&
		    agent_file_edits[i].incarnation == entry->incarnation)
			return 1;
	return 0;
}

static int
file_version_clear_locked(int slot)
{
	struct agent_file_cache_scope_state *scope_state;
	struct file_version *entry;
	uint start, capacity, position;

	if (slot < 0 || slot >= AGENT_FILE_VERSION_MAX)
		return -1;
	entry = &agent_file_versions[slot];
	scope_state = file_version_scope_state_locked(
		entry->scope_id, entry->identity_lifecycle, 0);
	if (scope_state == 0 || scope_state->version_count == 0)
		panic("Agent file version accounting");
	file_version_bank_bounds(scope_state, &start, &capacity);
	if ((uint)slot < start || (uint)slot >= start + scope_state->version_count)
		panic("Agent file version slot");
	position = (uint)slot - start;
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_file_edits[i].active &&
		    agent_file_edits[i].scope_id == entry->scope_id &&
		    workflow_lifecycle_key_equal(
			    agent_file_edits[i].lifecycle,
			    entry->identity_lifecycle) &&
		    agent_file_edits[i].dev == entry->dev &&
		    agent_file_edits[i].inum == entry->inum &&
		    agent_file_edits[i].incarnation == entry->incarnation)
			memset(&agent_file_edits[i], 0,
			       sizeof(agent_file_edits[i]));
	file_version_digest_clear_locked(entry);
	scope_state->version_count--;
	if (position < scope_state->version_count)
		memmove(entry, entry + 1,
			(scope_state->version_count - position) * sizeof(*entry));
	memset(&agent_file_versions[start + scope_state->version_count], 0,
	       sizeof(*entry));
	if (scope_state->version_cursor > position)
		scope_state->version_cursor--;
	if (scope_state->version_count == 0 ||
	    scope_state->version_cursor >= scope_state->version_count)
		scope_state->version_cursor = 0;
	return 0;
}

/*
 * 版本表是可回收驻留缓存，不是文件数上限。时钟指针只驱逐同一资源域中
 * 没有编辑租约、也没有待发布目录状态的冷项，避免第 N+1 个文件被误判
 * 为资源耗尽。
 */
static int
file_version_evict_locked(struct agent_file_cache_scope_state *state)
{
	uint start, capacity, count = state->version_count;

	file_version_bank_bounds(state, &start, &capacity);
	for (uint walked = 0; walked < count; walked++) {
		uint position = (state->version_cursor + walked) % count;
		struct file_version *entry =
			&agent_file_versions[start + position];

		if (entry->published_size_valid ||
		    file_version_has_edit_locked(entry))
			continue;
		state->version_cursor = (position + 1) % count;
		return file_version_clear_locked((int)(start + position));
	}
	return -1;
}

static struct file_version *
file_version_allocate_locked(uint64 dev, uint64 inum, uint64 incarnation,
			     uint scope_id,
			     struct workflow_lifecycle_key lifecycle)
{
	struct file_version *entry;
	struct agent_file_cache_scope_state *scope_state;
	uint start, capacity, position;

	if (!file_version_identity_valid(
		    dev, inum, incarnation, scope_id, lifecycle))
		return 0;
	scope_state = file_version_scope_state_locked(
		scope_id, lifecycle, 1);
	if (scope_state == 0)
		return 0;
	file_version_bank_bounds(scope_state, &start, &capacity);
	if (file_version_search_locked(
		    scope_state, dev, inum, incarnation, &position))
		return 0;
	if (scope_state->version_count >= capacity &&
	    file_version_evict_locked(scope_state) < 0)
		return 0;
	if (file_version_search_locked(
		    scope_state, dev, inum, incarnation, &position))
		panic("Agent file version duplicate");
	if (position < scope_state->version_count)
		memmove(&agent_file_versions[start + position + 1],
			&agent_file_versions[start + position],
			(scope_state->version_count - position) * sizeof(*entry));
	if (scope_state->version_count != 0 &&
	    position <= scope_state->version_cursor)
		scope_state->version_cursor++;
	entry = &agent_file_versions[start + position];
	memset(entry, 0, sizeof(*entry));
	entry->scope_id = scope_id;
	entry->dev = dev;
	entry->inum = inum;
	entry->incarnation = incarnation;
	entry->identity_lifecycle = lifecycle;
	entry->published_meta_slot = AGENT_FILE_META_MAX;
	/* 驻留项重建时继承全局单调版本，避免回收造成版本倒退。 */
	entry->edit_version = agent_file_edit_version_generation;
	entry->edit_authority_generation =
		agent_file_edit_authority_generation;
	/* 新绑定从唯一内容代际起步，阻止解绑前的异步摘要回填。 */
	entry->content_version =
		agent_file_counter_next(&agent_file_content_generation);
	scope_state->version_count++;
	if (scope_state->version_cursor >= scope_state->version_count)
		scope_state->version_cursor = 0;
	return entry;
}

static struct file_version *
file_version_inode_locked(struct inode *ip, int create)
{
	struct workflow_lifecycle_key lifecycle;
	struct file_version *entry;

	if (ip == 0 || !ip->valid || ip->type != T_FILE ||
	    ip->dev != ROOTDEV || ip->inum == 0 || ip->vfs_incarnation == 0 ||
	    ip->vfs_policy == VFS_POLICY_FREE ||
	    ip->fs_owner_domain < FS_OWNER_SYSTEM ||
	    ip->fs_owner_version != FS_OWNER_VERSION ||
	    file_version_current_lifecycle(
		    ip->vfs_scope_id, &lifecycle) < 0)
		return 0;
	entry = file_version_identity_locked(
		ip->dev, ip->inum, ip->vfs_incarnation,
		ip->vfs_scope_id, lifecycle);
	if (entry) {
		if (entry->storage_owner == ip->fs_owner_domain &&
		    entry->vfs_policy == ip->vfs_policy)
			return entry;
		if (!create ||
		    file_version_clear_locked(
			    (int)(entry - agent_file_versions)) < 0)
			return 0;
	}
	if (!create)
		return 0;
	entry = file_version_allocate_locked(
		ip->dev, ip->inum, ip->vfs_incarnation,
		ip->vfs_scope_id, lifecycle);
	if (entry) {
		entry->storage_owner = ip->fs_owner_domain;
		entry->vfs_policy = ip->vfs_policy;
	}
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
		entry->published_size = ip->size;
		entry->published_size_sequence =
			agent_file_counter_next(&agent_file_size_sequence);
		entry->published_size_generation =
			agent_file_state_generation_next_capture_locked(
				ip->vfs_scope_id, &lifecycle);
		entry->published_size_tick = agent_file_state_now();
		lifecycle_valid = ip->vfs_scope_id == VFS_SCOPE_SYSTEM ||
			workflow_lifecycle_key_valid(lifecycle);
		entry->published_meta_slot = AGENT_FILE_META_MAX;
		entry->published_lifecycle = workflow_lifecycle_none();
		if (lifecycle_valid && ip->agent_meta_slot > 0 &&
		    ip->agent_meta_slot <= AGENT_FILE_META_MAX &&
		    ip->agent_meta_version == AGENT_INODE_META_VERSION) {
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
	entry = file_version_current_identity_locked(
		meta->dev, meta->inum, meta->incarnation, scope_id);
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
		meta->dev, meta->inum, meta->incarnation,
		scope_id, lifecycle);
	if (entry == 0 || !entry->published_size_valid ||
	    entry->scope_id != scope_id || entry->published_meta_slot != slot ||
	    !workflow_lifecycle_key_equal(
		    entry->published_lifecycle, lifecycle))
		return;
	agent_file_overlay_published_size_locked(meta, scope_id);
	if (receipt == 0 || !entry->published_size_valid)
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

static void
file_version_size_absorb_locked(struct file_version *entry,
				struct agent_file_meta *meta)
{
	meta->size = entry->published_size;
	if (entry->published_size_generation > meta->fs_generation)
		meta->fs_generation = entry->published_size_generation;
	if (entry->published_size_tick > meta->updated_tick)
		meta->updated_tick = entry->published_size_tick;
	entry->published_size_valid = 0;
}

int
agent_file_state_content_settle(
	const struct agent_file_content_receipt *receipt,
	struct agent_file_meta *meta)
{
	struct file_version *entry;
	int settled = 0;
	int enabled;

	if (receipt == 0 || meta == 0 || receipt->sequence == 0)
		return 0;
	enabled = agent_edit_lock();
	entry = file_version_identity_locked(
		receipt->dev, receipt->inum, receipt->incarnation,
		receipt->scope_id, receipt->lifecycle);
	if (entry != 0 && entry->published_size_valid &&
	    entry->scope_id == receipt->scope_id &&
	    entry->published_meta_slot == receipt->slot &&
	    workflow_lifecycle_key_equal(
		    entry->published_lifecycle, receipt->lifecycle) &&
	    entry->published_size_sequence == receipt->sequence &&
	    meta->used && meta->dev == receipt->dev &&
	    meta->inum == receipt->inum &&
	    meta->incarnation == receipt->incarnation) {
		file_version_size_absorb_locked(entry, meta);
		settled = 1;
	}
	agent_edit_unlock(enabled);
	return settled;
}

int
agent_file_state_size_settle(
	struct agent_file_meta *meta, uint scope_id, uint slot,
	struct workflow_lifecycle_key lifecycle, uint64 sequence)
{
	struct file_version *entry;
	int settled = 0;
	int enabled;

	if (meta == 0 || !meta->used || sequence == 0)
		return 0;
	enabled = agent_edit_lock();
	entry = file_version_identity_locked(
		meta->dev, meta->inum, meta->incarnation, scope_id, lifecycle);
	if (entry != 0 && entry->published_size_valid &&
	    entry->published_meta_slot == slot &&
	    workflow_lifecycle_key_equal(
		    entry->published_lifecycle, lifecycle) &&
	    entry->published_size_sequence <= sequence) {
		file_version_size_absorb_locked(entry, meta);
		settled = 1;
	}
	agent_edit_unlock(enabled);
	return settled;
}

void
agent_file_state_content_absorb_volatile(struct inode *ip, uint slot)
{
	struct file_version *entry;
	int enabled;

	if (ip == 0 || slot >= AGENT_FILE_META_MAX)
		return;
	enabled = agent_edit_lock();
	entry = file_version_inode_locked(ip, 0);
	if (entry != 0 && entry->published_size_valid &&
	    entry->published_meta_slot == slot &&
	    entry->published_size == ip->size) {
		entry->published_size_valid = 0;
	}
	agent_edit_unlock(enabled);
}

void
agent_file_state_unbind_catalog_identity(uint64 dev, uint64 inum,
					 uint64 incarnation, uint scope_id)
{
	int enabled;

	if (dev != ROOTDEV || inum == 0 || incarnation == 0)
		return;
	enabled = agent_edit_lock();
	for (uint i = 0; i < AGENT_FILE_CACHE_SCOPE_MAX; i++) {
		struct agent_file_cache_scope_state *state =
			&agent_file_cache_scopes[i];
		struct file_version *entry;

		if (!state->used || state->scope_id != scope_id)
			continue;
		entry = file_version_search_locked(
			state, dev, inum, incarnation, 0);
		if (entry == 0)
			continue;
		/* 目录解绑只撤销目录派生缓存；inode 版本和编辑租约继续保护活文件。 */
		entry->content_version =
			agent_file_counter_next(&agent_file_content_generation);
		entry->published_size_valid = 0;
		entry->published_meta_slot = AGENT_FILE_META_MAX;
		entry->published_lifecycle = workflow_lifecycle_none();
		file_version_digest_clear_locked(entry);
	}
	agent_edit_unlock(enabled);
}

void
agent_file_version_reclaim(struct inode *ip)
{
	int enabled;

	if (ip == 0)
		return;
	enabled = agent_edit_lock();
	for (uint i = 0; i < AGENT_FILE_CACHE_SCOPE_MAX; i++) {
		struct agent_file_cache_scope_state *state =
			&agent_file_cache_scopes[i];
		struct file_version *entry;

		if (!state->used || state->scope_id != ip->vfs_scope_id)
			continue;
		entry = file_version_search_locked(
			state, ip->dev, ip->inum, ip->vfs_incarnation, 0);
		if (entry)
			(void)file_version_clear_locked(
				(int)(entry - agent_file_versions));
	}
	agent_edit_unlock(enabled);
}

static struct agent_file_edit_entry *
agent_edit_find_locked(uint scope_id, uint64 lease_id, struct inode *ip)
{
	struct workflow_lifecycle_key lifecycle;

	if (file_version_current_lifecycle(scope_id, &lifecycle) < 0)
		return 0;
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++) {
		struct agent_file_edit_entry *e = &agent_file_edits[i];

		/* inode 为空时按当前生命周期内的租约键查找。 */
		if (e->active && e->scope_id == scope_id &&
		    workflow_lifecycle_key_equal(e->lifecycle, lifecycle) &&
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
	version = file_version_identity_locked(
		e->dev, e->inum, e->incarnation, e->scope_id, e->lifecycle);
	if (version) {
		if (publish_dirty && e->dirty)
			(void)file_version_edit_next_locked(version);
		version->edit_authority_generation = agent_file_counter_next(
			&agent_file_edit_authority_generation);
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
	       workflow_lifecycle_key_equal(e->lifecycle,
				    vfs_proc_lifecycle(p)) &&
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
	version = e ? file_version_identity_locked(
			      state->dev, state->inum, state->incarnation,
			      e->scope_id, e->lifecycle) :
		      file_version_inode_locked(ip, 0);
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
	return agent_edit_modify_allowed(
		ip, 0, authority_generation, valid_until_tick);
}

static void
agent_edit_note(struct inode *ip, int deleting)
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
	if (!deleting)
		agent_edit_cleanup_expired_locked(agent_file_state_now());
	edit = agent_edit_find_locked(ip->vfs_scope_id, 0, ip);
	if (edit) {
		if (deleting)
			agent_edit_release_locked(edit, 1);
		else if (agent_edit_owner(edit, p))
			edit->dirty = 1;
	} else {
		version = file_version_inode_locked(ip, 1);
		if (version)
			(void)file_version_edit_next_locked(version);
	}
	agent_edit_unlock(enabled);
}

#define DEFINE_AGENT_EDIT_NOTE(operation, deleting) \
	void agent_edit_note_##operation(struct inode *ip) \
	{ agent_edit_note(ip, deleting); }

DEFINE_AGENT_EDIT_NOTE(write, 0)
DEFINE_AGENT_EDIT_NOTE(truncate, 0)
DEFINE_AGENT_EDIT_NOTE(delete, 1)
#undef DEFINE_AGENT_EDIT_NOTE

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
	for (uint i = 0; i < AGENT_FILE_CACHE_SCOPE_MAX; i++) {
		struct agent_file_cache_scope_state *state =
			&agent_file_cache_scopes[i];
		uint start, capacity;

		if (!state->used || state->scope_id != scope_id)
			continue;
		file_version_bank_bounds(state, &start, &capacity);
		memset(&agent_file_versions[start], 0,
		       capacity * sizeof(agent_file_versions[0]));
		memset(state, 0, sizeof(*state));
	}
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
		    e->scope_id != ip->vfs_scope_id ||
		    !workflow_lifecycle_key_equal(
			    e->lifecycle, version->identity_lifecycle))
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
	e->lifecycle = version->identity_lifecycle;
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
	/* 只有成功的编辑事件才表示权限状态发生变化。 */
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
			version_entry = file_version_inode_locked(call.inode, 0);
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
	call.entry->lifecycle = version_entry->identity_lifecycle;
	call.entry->base_version = version;
	call.entry->deadline_tick = now + ttl;
	safestrcpy(call.entry->path, path, sizeof(call.entry->path));
	version_entry->edit_authority_generation = agent_file_counter_next(
		&agent_file_edit_authority_generation);
	edit_state_locked(&call.state, call.entry, call.inode, path, 0);
	return edit_reply(&call, AGENT_STATUS_OK, "edit_begin", stateaddr);
}

int
sys_agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
			   uint64 stateaddr)
{
	struct edit_call call;
	struct file_version *version;
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
		call.entry->dev, call.entry->inum, call.entry->incarnation,
		call.entry->scope_id, call.entry->lifecycle);
	if (version == 0 || version->edit_version != call.entry->base_version ||
	    expected_version != call.entry->base_version) {
		edit_state_locked(
			&call.state, call.entry, 0, call.entry->path, 0);
		return edit_reply(&call, AGENT_STATUS_STALE,
			"edit_commit_stale", stateaddr);
	}
	if (call.entry->dirty)
		(void)file_version_edit_next_locked(version);
	version->edit_authority_generation = agent_file_counter_next(
		&agent_file_edit_authority_generation);
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
