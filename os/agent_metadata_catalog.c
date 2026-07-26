#include "agent_file_name_policy.h"
#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_catalog.h"
#include "defs.h"
#include "file.h"
#include "fs.h"
#include "vfs_security.h"

/*
 * Authoritative live directory for Agent workflow files. Read borrows are
 * transaction-local; writes use one catalog-owned edit lease and commit.
 */
#define AGENT_CATALOG_INDEX_BUCKETS 16
#define AGENT_CATALOG_INDEX_COUNT 3
#define AGENT_FILE_SYSTEM_LIMIT 64
#define AGENT_FILE_SCOPE_LIMIT 112

_Static_assert(AGENT_FILE_SYSTEM_LIMIT +
	       VFS_SCOPE_MAX_ACTIVE * AGENT_FILE_SCOPE_LIMIT <=
	       AGENT_FILE_META_MAX,
	       "file catalog must reserve every workflow partition");
_Static_assert(AGENT_FILE_META_MAX <= 32767,
	       "file catalog cursors must fit their signed short storage");

static struct agent_file_meta agent_catalog_files[AGENT_FILE_META_MAX];
static uint agent_catalog_scopes[AGENT_FILE_META_MAX];
/* Slots are bounded to 512, so signed 16-bit cursors preserve -1 sentinels. */
static short agent_catalog_index_heads[AGENT_CATALOG_INDEX_COUNT]
				      [AGENT_CATALOG_INDEX_BUCKETS];
static short agent_catalog_index_next[AGENT_CATALOG_INDEX_COUNT]
				     [AGENT_FILE_META_MAX];
static const ushort agent_catalog_index_offsets[AGENT_CATALOG_INDEX_COUNT] = {
	__builtin_offsetof(struct agent_file_meta, status),
	__builtin_offsetof(struct agent_file_meta, stage),
	__builtin_offsetof(struct agent_file_meta, kind),
};
static short agent_catalog_apply_slots[AGENT_FILE_META_MAX];
static struct agent_file_meta agent_catalog_edit_buffer;
static struct agent_catalog_edit *agent_catalog_active_edit;
static uint64 agent_catalog_generation;
static uint agent_catalog_dirty_indexes;

static void agent_catalog_require_txn(void) {
	if (!agent_metadata_txn_owned(0))
		panic("Agent catalog transaction invariant");
}

static void agent_catalog_changed(uint changes) {
	if (agent_catalog_active_edit != 0)
		panic("Agent catalog edit invariant");
	agent_catalog_dirty_indexes |= changes & AGENT_FILE_CHANGE_INDEX_ALL;
	agent_catalog_generation++;
	if (agent_catalog_generation == 0)
		agent_catalog_generation = 1;
}

static void agent_catalog_reset_indexes(void) {
	memset(agent_catalog_index_heads, 0xff, sizeof(agent_catalog_index_heads));
	memset(agent_catalog_index_next, 0xff, sizeof(agent_catalog_index_next));
}

void
agent_metadata_catalog_init(void)
{
	memset(agent_catalog_files, 0, sizeof(agent_catalog_files));
	memset(agent_catalog_scopes, 0, sizeof(agent_catalog_scopes));
	agent_catalog_active_edit = 0;
	agent_catalog_reset_indexes();
	agent_catalog_generation = 1;
	agent_catalog_dirty_indexes = 0;
}

uint64 agent_metadata_catalog_generation(void) {
	agent_catalog_require_txn();
	return agent_catalog_generation;
}

int agent_metadata_catalog_borrow(uint64 generation, int slot, struct agent_catalog_view *view) {
	agent_catalog_require_txn();
	if (view == 0 || slot < 0 || slot >= AGENT_FILE_META_MAX)
		return -1;
	view->meta = 0;
	if (generation != 0 && generation != agent_catalog_generation)
		return AGENT_CATALOG_STALE;
	if (!agent_catalog_files[slot].used)
		return 0;
	view->meta = &agent_catalog_files[slot];
	view->scope_id = agent_catalog_scopes[slot];
	return 1;
}

int agent_metadata_catalog_edit_begin(int slot, uint scope_id, struct agent_catalog_edit *edit) {
	agent_catalog_require_txn();
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

int agent_metadata_catalog_edit_commit(struct agent_catalog_edit *edit, uint changes) {
	agent_catalog_require_txn();
	if (edit == 0 || agent_catalog_active_edit != edit)
		return -1;
	if (edit->meta != &agent_catalog_edit_buffer ||
	    edit->slot < 0 || edit->slot >= AGENT_FILE_META_MAX ||
	    (edit->meta->used && !agent_object_scope_valid(edit->scope_id))) {
		agent_catalog_active_edit = 0;
		edit->meta = 0;
		return -1;
	}
	agent_catalog_files[edit->slot] = *edit->meta;
	agent_catalog_scopes[edit->slot] = edit->meta->used ? edit->scope_id : VFS_SCOPE_NONE;
	agent_catalog_active_edit = 0;
	edit->meta = 0;
	agent_catalog_changed(changes);
	return 0;
}

void agent_metadata_catalog_edit_abort(struct agent_catalog_edit *edit) {
	agent_catalog_require_txn();
	if (edit != 0 && agent_catalog_active_edit == edit) {
		agent_catalog_active_edit = 0;
		edit->meta = 0;
	}
}

static uint64 agent_catalog_bucket(const char *text) {
	uint64 hash = 1469598103934665603ULL;

	while (*text) {
		hash ^= (uchar)*text++;
		hash *= 1099511628211ULL;
	}
	return hash % AGENT_CATALOG_INDEX_BUCKETS;
}

static void agent_catalog_flush_indexes(void) {
	uint64 bucket;

	agent_catalog_require_txn();
	if (agent_catalog_dirty_indexes == 0)
		return;
	agent_catalog_reset_indexes();
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		const char *text;

		if (!agent_catalog_files[i].used) {
			agent_metadata_txn_work_charge(1);
			continue;
		}
		for (int index = 0; index < AGENT_CATALOG_INDEX_COUNT; index++) {
			text = (const char *)&agent_catalog_files[i] +
			       agent_catalog_index_offsets[index];
			if (text[0] == 0)
				continue;
			bucket = agent_catalog_bucket(text);
			agent_catalog_index_next[index][i] =
				agent_catalog_index_heads[index][bucket];
			agent_catalog_index_heads[index][bucket] = i;
		}
		agent_metadata_txn_work_charge(1);
	}
	agent_catalog_dirty_indexes = 0;
}

int
agent_metadata_catalog_index_seek(uint64 generation, int index, char *key,
				  int slot, int *bucket_out)
{
	uint64 bucket;
	int index_slot = index - 1;

	agent_catalog_require_txn();
	if (generation != agent_catalog_generation)
		return AGENT_CATALOG_STALE;
	if (index_slot < 0 || index_slot >= AGENT_CATALOG_INDEX_COUNT)
		return -1;
	if (slot < 0) {
		if (key == 0 || key[0] == 0)
			return -1;
		agent_catalog_flush_indexes();
		bucket = agent_catalog_bucket(key);
		if (bucket_out)
			*bucket_out = bucket;
		return agent_catalog_index_heads[index_slot][bucket];
	}
	if (slot >= AGENT_FILE_META_MAX)
		return -1;
	return agent_catalog_index_next[index_slot][slot];
}

static void agent_catalog_normalize_physical(int slot, struct agent_file_meta *meta) {
	if (meta->physical_name[0] == 0 ||
	    strlen(meta->physical_name) >= DIRSIZ ||
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

static struct inode *
agent_catalog_lookup_or_create_status(char *name, int create, uint scope_id,
				      struct proc *actor, int *status)
{
	struct inode *ip;
	struct vfs_cred actor_cred;
	int lookup_status = FS_LOOKUP_ERROR;

	if (status)
		*status = FS_LOOKUP_ERROR;
	if (scope_id == VFS_SCOPE_SYSTEM && create)
		return 0;
	if (scope_id != VFS_SCOPE_SYSTEM &&
	    (!agent_scope_valid(scope_id) ||
	     (create ? !vfs_scope_active(scope_id) : !vfs_scope_retained(scope_id))))
		return 0;
	if ((ip = namei_scope_status(name, VFS_POLICY_WORKFLOW, scope_id,
				     &lookup_status)) != 0) {
		ivalid(ip);
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
	ip = fs_create(name, T_FILE, 0, &actor_cred, VFS_POLICY_WORKFLOW);
	if (ip != 0 && status)
		*status = FS_LOOKUP_FOUND;
	return ip;
}

static void agent_catalog_unbind(int slot, struct agent_file_meta *meta, uint scope_id) {
	struct inode *ip;

	if (slot < 0 || slot >= AGENT_FILE_META_MAX || meta == 0 ||
	    !meta->used || meta->physical_name[0] == 0)
		return;
	ip = namei_scope(meta->physical_name, VFS_POLICY_WORKFLOW, scope_id);
	if (ip == 0)
		return;
	ivalid(ip);
	if (ip->agent_meta_slot == slot + 1 && ip->dev == meta->dev && ip->inum == meta->inum &&
	    ip->vfs_incarnation == meta->incarnation) {
		ip->agent_meta_slot = 0;
		ip->agent_meta_flags = 0;
		ip->agent_meta_version = 0;
		iupdate(ip);
	}
	iput(ip);
}

static int agent_catalog_bind_status(int slot, int create, struct proc *actor, int *lookup_status) {
	struct agent_file_meta *meta;
	struct inode *ip;

	if (lookup_status)
		*lookup_status = FS_LOOKUP_ERROR;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return -1;
	meta = &agent_catalog_files[slot];
	if (!meta->used || !agent_object_scope_valid(agent_catalog_scopes[slot]))
		return -1;
	agent_catalog_normalize_physical(slot, meta);
	ip = agent_catalog_lookup_or_create_status(meta->physical_name, create,
					   agent_catalog_scopes[slot], actor,
					   lookup_status);
	if (ip == 0)
		return -1;
	if ((meta->dev != 0 || meta->inum != 0 || meta->incarnation != 0) &&
	    (meta->dev != ip->dev || meta->inum != ip->inum || meta->incarnation != ip->vfs_incarnation)) {
		iput(ip);
		if (lookup_status)
			*lookup_status = FS_LOOKUP_ABSENT;
		return -1;
	}
	ip->agent_meta_slot = slot + 1;
	ip->agent_meta_flags = meta->flags & AGENT_FILE_META_F_PERSIST;
	ip->agent_meta_version = AGENT_INODE_META_VERSION;
	iupdate(ip);
	meta->dev = ip->dev;
	meta->inum = ip->inum;
	meta->incarnation = ip->vfs_incarnation;
	meta->size = ip->size;
	meta->fs_generation = agent_file_state_generation_next(agent_catalog_scopes[slot]);
	iput(ip);
	return 0;
}

int agent_metadata_catalog_bind(int slot, int create, struct proc *actor) {
	int result;

	agent_catalog_require_txn();
	result = agent_catalog_bind_status(slot, create, actor, 0);
	if (result == 0)
		agent_catalog_changed(0);
	return result;
}

void agent_metadata_catalog_clear_slot(int slot) {
	int was_used;
	uint scope_id;

	agent_catalog_require_txn();
	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return;
	was_used = agent_catalog_files[slot].used;
	scope_id = agent_catalog_scopes[slot];
	agent_catalog_unbind(slot, &agent_catalog_files[slot], scope_id);
	memset(&agent_catalog_files[slot], 0, sizeof(agent_catalog_files[slot]));
	agent_catalog_scopes[slot] = VFS_SCOPE_NONE;
	agent_catalog_changed(AGENT_FILE_CHANGE_MEMBERSHIP);
	if (was_used)
		agent_file_state_generation_next(scope_id);
}

void agent_metadata_catalog_restore(int slot, const struct agent_file_meta *previous,
				    uint previous_scope, int had_previous) {
	agent_catalog_require_txn();
	if (slot < 0 || slot >= AGENT_FILE_META_MAX || previous == 0)
		return;
	agent_catalog_unbind(slot, &agent_catalog_files[slot], agent_catalog_scopes[slot]);
	if (!had_previous) {
		memset(&agent_catalog_files[slot], 0, sizeof(agent_catalog_files[slot]));
		agent_catalog_scopes[slot] = VFS_SCOPE_NONE;
	} else {
		agent_catalog_files[slot] = *previous;
		agent_catalog_scopes[slot] = previous_scope;
		if (agent_catalog_bind_status(slot, 0, 0, 0) == 0)
			agent_catalog_files[slot] = *previous;
	}
	agent_catalog_changed(AGENT_FILE_CHANGE_MEMBERSHIP);
}

int agent_metadata_catalog_find(uint scope_id, uint64 fid, char *path) {
	agent_catalog_require_txn();
	if ((fid == 0) == (path == 0 || path[0] == 0))
		return -1;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_txn_work_charge(1);
		if (agent_catalog_files[i].used &&
		    agent_catalog_scopes[i] == scope_id &&
		    ((fid != 0 && agent_catalog_files[i].fid == fid) ||
		     (fid == 0 &&
		      strncmp(agent_catalog_files[i].physical_name, path,
			      sizeof(agent_catalog_files[i].physical_name)) == 0)))
			return i;
	}
	return -1;
}

int agent_metadata_catalog_alloc_slot(uint scope_id) {
	int owned = 0;
	int limit = scope_id == VFS_SCOPE_SYSTEM ? AGENT_FILE_SYSTEM_LIMIT : AGENT_FILE_SCOPE_LIMIT;

	agent_catalog_require_txn();
	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		if (agent_catalog_files[i].used &&
		    agent_catalog_scopes[i] == scope_id)
			owned++;
	if (owned >= limit)
		return -1;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		if (!agent_catalog_files[i].used)
			return i;
	return -1;
}

uint64 agent_metadata_catalog_alloc_fid(uint scope_id) {
	agent_catalog_require_txn();
	for (uint64 candidate = 1; candidate <= AGENT_FILE_META_MAX; candidate++) {
		int used = 0;

		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			if (agent_catalog_files[i].used &&
			    agent_catalog_scopes[i] == scope_id &&
			    agent_catalog_files[i].fid == candidate) {
				used = 1;
				break;
			}
		if (!used)
			return candidate;
	}
	return 0;
}

int agent_metadata_catalog_live_count(void) {
	int used = 0;

	agent_catalog_require_txn();
	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		if (agent_catalog_files[i].used)
			used++;
	return used;
}

void agent_metadata_catalog_clear(struct agent_catalog_delta *delta) {
	agent_catalog_require_txn();
	if (delta == 0)
		panic("Agent catalog delta invariant");
	memset(delta, 0, sizeof(*delta));
	delta->full_reset = 1;
	delta->scope_id = VFS_SCOPE_NONE;
	agent_metadata_txn_projection_begin();
	memset(agent_catalog_files, 0, sizeof(agent_catalog_files));
	memset(agent_catalog_scopes, 0, sizeof(agent_catalog_scopes));
	agent_catalog_changed(AGENT_FILE_CHANGE_MEMBERSHIP);
}

int agent_metadata_catalog_reclaim_scope(uint scope_id) {
	int cleared = 0;

	agent_catalog_require_txn();
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_catalog_files[i].used ||
		    agent_catalog_scopes[i] != scope_id)
			continue;
		agent_metadata_catalog_clear_slot(i);
		cleared++;
	}
	return cleared;
}

static int agent_catalog_apply_slot_available(const struct agent_meta_record *records,
		uint record_index, uint scope_id, int slot) {
	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return 0;
	if (agent_catalog_files[slot].used &&
	    agent_catalog_scopes[slot] != scope_id)
		return 0;
	for (uint i = 0; i < record_index; i++)
		if (records[i].scope_id == scope_id &&
		    agent_catalog_apply_slots[i] == slot)
			return 0;
	return 1;
}

static int agent_catalog_preflight(const struct agent_meta_record *records, uint count,
		int reload_one_scope, uint reload_scope, struct agent_metadata_apply_result *result) {
	for (uint i = 0; i < count; i++) {
		struct agent_file_meta meta;
		struct inode *ip;
		int lookup_status = FS_LOOKUP_ERROR;
		int slot;

		if (reload_one_scope && records[i].scope_id != reload_scope)
			continue;
		if (!agent_object_scope_valid(records[i].scope_id) ||
		    records[i].slot >= AGENT_FILE_META_MAX)
			return -1;
		slot = records[i].slot;
		if (reload_one_scope && !agent_catalog_apply_slot_available(records, i, reload_scope, slot)) {
			for (slot = 0; slot < AGENT_FILE_META_MAX; slot++)
				if (agent_catalog_apply_slot_available(records, i, reload_scope, slot))
					break;
			if (slot == AGENT_FILE_META_MAX)
				return -1;
			result->layout_changed = 1;
		}
		meta = records[i].meta;
		agent_catalog_normalize_physical(slot, &meta);
		ip = agent_catalog_lookup_or_create_status(meta.physical_name, 0,
						   records[i].scope_id, 0,
						   &lookup_status);
		if (ip == 0) {
			if (lookup_status == FS_LOOKUP_ERROR)
				return -1;
			result->missing_slots[records[i].slot / 8] |= 1U << (records[i].slot % 8);
			continue;
		}
		if ((meta.dev != 0 || meta.inum != 0 || meta.incarnation != 0) &&
		    (meta.dev != ip->dev || meta.inum != ip->inum || meta.incarnation != ip->vfs_incarnation)) {
			result->missing_slots[records[i].slot / 8] |= 1U << (records[i].slot % 8);
			iput(ip);
			continue;
		}
		iput(ip);
		agent_catalog_apply_slots[i] = slot;
	}
	return 0;
}

int agent_metadata_catalog_apply_snapshot(const struct agent_meta_record *records, uint count,
		int reload_one_scope, uint reload_scope, struct agent_metadata_apply_result *result) {
	agent_catalog_require_txn();
	if (records == 0 || result == 0 || count > AGENT_FILE_META_MAX ||
	    (reload_one_scope && !agent_scope_valid(reload_scope)))
		return -1;
	memset(result, 0, sizeof(*result));
	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		agent_catalog_apply_slots[i] = -1;
	if (agent_catalog_preflight(records, count, reload_one_scope, reload_scope, result) < 0)
		return -1;
	agent_metadata_txn_projection_begin();
	if (reload_one_scope) {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			if (agent_catalog_files[i].used &&
			    agent_catalog_scopes[i] == reload_scope)
				agent_metadata_catalog_clear_slot(i);
	} else {
		memset(agent_catalog_files, 0, sizeof(agent_catalog_files));
		memset(agent_catalog_scopes, 0, sizeof(agent_catalog_scopes));
	}
	for (uint i = 0; i < count; i++) {
		int slot = agent_catalog_apply_slots[i];

		if (slot < 0)
			continue;
		agent_catalog_files[slot] = records[i].meta;
		agent_catalog_files[slot].update_mask = 0;
		agent_catalog_scopes[slot] = records[i].scope_id;
		if (agent_catalog_bind_status(slot, 0, 0, 0) < 0) {
			result->missing_slots[records[i].slot / 8] |= 1U << (records[i].slot % 8);
			memset(&agent_catalog_files[slot], 0, sizeof(agent_catalog_files[slot]));
			agent_catalog_scopes[slot] = VFS_SCOPE_NONE;
			continue;
		}
		result->delta.applied_slots[slot / 8] |= 1U << (slot % 8);
	}
	result->delta.full_reset = !reload_one_scope;
	result->delta.scope_id = reload_one_scope ? reload_scope : VFS_SCOPE_NONE;
	agent_catalog_changed(AGENT_FILE_CHANGE_MEMBERSHIP);
	result->used = agent_metadata_catalog_live_count();
	return result->used;
}

int agent_metadata_catalog_export_scope(uint scope_id, struct agent_meta_record *records,
		int capacity, uint64 *size_sequence) {
	int count = 0;
	int enabled;

	agent_catalog_require_txn();
	if (!agent_object_scope_valid(scope_id) ||
	    records == 0 || capacity < 0 || size_sequence == 0)
		return -1;
	enabled = agent_file_state_snapshot_begin(size_sequence);
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		struct agent_meta_record *record;

		if (!agent_catalog_files[i].used || agent_catalog_scopes[i] != scope_id ||
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
		agent_file_state_snapshot_overlay(&record->meta, scope_id);
		record->meta.update_mask = 0;
		record->scope_id = scope_id;
		record->slot = i;
	}
	agent_file_state_snapshot_end(enabled);
	return count;
}
