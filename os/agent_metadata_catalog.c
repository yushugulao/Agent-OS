#include "agent_file_name_policy.h"
#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_catalog.h"
#include "agent_metadata_internal.h"
#include "defs.h"
#include "file.h"
#include "fs.h"
#include "vfs_security.h"
#define AGENT_CATALOG_INDEX_BUCKETS 16
#define AGENT_CATALOG_INDEX_COUNT 3
#define AGENT_CATALOG_PREPARE_STEP 32
#define AGENT_CATALOG_PLAN_HASH 1469598103934665603ULL
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
static struct agent_file_meta agent_catalog_files[AGENT_FILE_META_MAX];
static uint agent_catalog_scopes[AGENT_FILE_META_MAX];
static short agent_catalog_index_heads[AGENT_CATALOG_INDEX_COUNT]
				      [AGENT_CATALOG_INDEX_BUCKETS];
static short agent_catalog_index_next[AGENT_CATALOG_INDEX_COUNT]
				     [AGENT_FILE_META_MAX];
static const ushort agent_catalog_index_offsets[AGENT_CATALOG_INDEX_COUNT] = {
	__builtin_offsetof(struct agent_file_meta, status),
	__builtin_offsetof(struct agent_file_meta, stage),
	__builtin_offsetof(struct agent_file_meta, kind),
};
static uchar agent_catalog_states[AGENT_FILE_META_MAX];
static struct agent_file_meta agent_catalog_edit_buffer;
static struct agent_catalog_edit *agent_catalog_active_edit;
static uint64 agent_catalog_generation;
static void *agent_catalog_mutation_owner;
static uint64 agent_catalog_mutation_sequence, agent_catalog_mutation_token;
static uint agent_catalog_dirty_indexes, agent_catalog_pending_count;
static int agent_catalog_unbind(int, struct agent_file_meta *, uint);
static void agent_catalog_normalize_physical(int, struct agent_file_meta *);
static void agent_catalog_require_txn(void);
static int agent_catalog_mutation_allowed(void);
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
void agent_metadata_catalog_resolve(
	uint scope_id, const struct agent_file_meta *selector, int except_slot,
	struct agent_catalog_resolution *result) {
	agent_catalog_require_txn();
	memset(result, 0, sizeof(*result));
	result->slot = -1;
	if (selector)
		result->provided = agent_catalog_key_matches(selector, selector);
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		uint matched;
		agent_metadata_txn_work_charge(1);
		if (!agent_catalog_files[i].used || i == except_slot)
			continue;
		if (agent_catalog_scopes[i] != VFS_SCOPE_SYSTEM)
			result->ordinary++;
		if (agent_catalog_scopes[i] != scope_id)
			continue;
		result->owned++;
		if (agent_catalog_files[i].flags & AGENT_FILE_META_F_AUTOSCAN)
			result->autoscan++;
		matched = selector ?
			agent_catalog_key_matches(selector, &agent_catalog_files[i]) : 0;
		if (matched == 0)
			continue;
		result->matched |= matched;
		result->states |= agent_catalog_states[i];
		if (result->slot == -1)
			result->slot = i;
		else if (result->slot != i)
			result->slot = AGENT_CATALOG_CONFLICT;
	}
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

static void agent_catalog_storage_reset(void) {
	memset(agent_catalog_files, 0, sizeof(agent_catalog_files));
	memset(agent_catalog_scopes, 0, sizeof(agent_catalog_scopes));
	memset(agent_catalog_states, 0, sizeof(agent_catalog_states));
	agent_catalog_pending_count = 0;
}

static void agent_catalog_state_clear(int slot) {
	if (agent_catalog_states[slot] & AGENT_CATALOG_STATE_PENDING) {
		if (agent_catalog_pending_count == 0)
			panic("Agent catalog pending invariant");
		agent_catalog_pending_count--;
	}
	agent_catalog_states[slot] = 0;
}

static void agent_catalog_changed(uint changes) {
	if (agent_catalog_active_edit != 0 || !agent_catalog_mutation_allowed())
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
	agent_catalog_storage_reset();
	agent_catalog_active_edit = 0;
	agent_catalog_reset_indexes();
	agent_catalog_generation = 1;
	agent_catalog_mutation_owner = 0;
	agent_catalog_mutation_sequence = 0;
	agent_catalog_mutation_token = 0;
	agent_catalog_dirty_indexes = 0;
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
	int admission = 1;
	int growth = 0;
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
	agent_catalog_files[edit->slot] = *edit->meta;
	agent_catalog_scopes[edit->slot] = edit->meta->used ? edit->scope_id : VFS_SCOPE_NONE;
	if (!edit->meta->used)
		agent_catalog_state_clear(edit->slot);
	agent_metadata_catalog_edit_abort(edit);
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
	return agent_catalog_hash_bytes(
		AGENT_CATALOG_PLAN_HASH, text, strlen(text)) %
	       AGENT_CATALOG_INDEX_BUCKETS;
}

static int agent_catalog_flush_indexes(void) {
	uint64 bucket;
	int visited = 0;
	agent_catalog_require_txn();
	if (agent_catalog_dirty_indexes == 0)
		return 0;
	agent_catalog_reset_indexes();
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		const char *text;
		visited++;
		if (!agent_catalog_files[i].used || agent_catalog_states[i] != 0) {
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
	return visited;
}

int agent_metadata_catalog_index_seek(
	uint64 generation, int index, char *key, int slot, int *bucket_out,
	int *rebuild_records) {
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
		if (rebuild_records)
			*rebuild_records = agent_catalog_flush_indexes();
		else
			(void)agent_catalog_flush_indexes();
		bucket = agent_catalog_bucket(key);
		if (bucket_out)
			*bucket_out = bucket;
		return agent_catalog_index_heads[index_slot][bucket];
	}
	if (rebuild_records)
		*rebuild_records = 0;
	if (slot >= AGENT_FILE_META_MAX)
		return -1;
	return agent_catalog_index_next[index_slot][slot];
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
	int slot, int create, struct proc *actor, int *lookup_status) {
	struct agent_file_meta *meta;
	struct inode *ip;
	if (lookup_status)
		*lookup_status = FS_LOOKUP_ERROR;
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return -1;
	meta = &agent_catalog_files[slot];
	if (!meta->used || agent_catalog_states[slot] != 0 ||
	    !agent_object_scope_valid(agent_catalog_scopes[slot]))
		return -1;
	if (meta->physical_name[0] == 0 ||
	    strlen(meta->physical_name) > DIRSIZ ||
	    strncmp(meta->physical_name, AGENT_META_STORE_NAME_0, DIRSIZ) == 0 ||
	    strncmp(meta->physical_name, AGENT_META_STORE_NAME_1, DIRSIZ) == 0)
		return -1;
	ip = agent_catalog_lookup_or_create_status(meta->physical_name, create,
					   agent_catalog_scopes[slot], actor,
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
		ip, slot + 1, meta->flags & AGENT_FILE_META_F_PERSIST, 0) < 0)
		goto out;
	meta->dev = ip->dev;
	meta->inum = ip->inum;
	meta->incarnation = ip->vfs_incarnation;
	meta->size = ip->size;
	meta->fs_generation = agent_file_state_generation_next(agent_catalog_scopes[slot]);
	iput(ip);
	return create;
out:
	if (create) {
		uint dev = ip->dev, inum = ip->inum;
		uint incarnation = ip->vfs_incarnation;
		iput(ip);
		return fs_rollback_created_workflow(meta->physical_name, dev, inum,
			incarnation, agent_catalog_scopes[slot]) < 0 ?
			AGENT_CATALOG_INDETERMINATE : -1;
	}
	iput(ip);
	return -1;
}

int agent_metadata_catalog_bind(int slot, int create, struct proc *actor) {
	int result;
	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	result = agent_catalog_bind_status(slot, create, actor, 0);
	if (result >= 0)
		agent_catalog_changed(0);
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
	agent_catalog_state_clear(slot);
	memset(&agent_catalog_files[slot], 0, sizeof(agent_catalog_files[slot]));
	agent_catalog_scopes[slot] = VFS_SCOPE_NONE;
	agent_catalog_changed(AGENT_FILE_CHANGE_MEMBERSHIP);
	if (was_used)
		agent_file_state_generation_next(scope_id);
	return 0;
}

int agent_metadata_catalog_restore(
	const struct agent_catalog_mutation_fence *fence,
	const struct agent_catalog_undo_token *undo,
	const struct agent_file_meta *previous, uint previous_scope, int had_previous) {
	struct agent_catalog_resolution result;
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
	agent_catalog_state_clear(slot);
	if (!had_previous) {
		memset(&agent_catalog_files[slot], 0, sizeof(agent_catalog_files[slot]));
		agent_catalog_scopes[slot] = VFS_SCOPE_NONE;
	} else {
		agent_catalog_files[slot] = *previous;
		agent_catalog_scopes[slot] = previous_scope;
		if (agent_catalog_bind_status(slot, 0, 0, 0) < 0)
			return -1;
		agent_catalog_files[slot] = *previous;
	}
	agent_catalog_changed(AGENT_FILE_CHANGE_MEMBERSHIP);
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
	agent_catalog_require_txn();
	admission = agent_catalog_admission(scope_id, -1, 0, 0, flags, 1);
	if (admission <= 0)
		return admission;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_metadata_txn_work_charge(1);
		if (!agent_catalog_files[i].used)
			return i;
	}
	return AGENT_CATALOG_NO_SPACE;
}

uint64 agent_metadata_catalog_alloc_fid(uint scope_id) {
	uchar used_fids[(AGENT_FILE_META_MAX + 7) / 8];
	agent_catalog_require_txn();
	memset(used_fids, 0, sizeof(used_fids));
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		uint64 fid = agent_catalog_files[i].fid;
		agent_metadata_txn_work_charge(1);
		if (agent_catalog_files[i].used &&
		    agent_catalog_scopes[i] == scope_id && fid > 0 &&
		    fid <= AGENT_FILE_META_MAX)
			used_fids[(fid - 1) / 8] |= 1U << ((fid - 1) % 8);
	}
	for (uint64 candidate = 1; candidate <= AGENT_FILE_META_MAX; candidate++) {
		agent_metadata_txn_work_charge(1);
		if ((used_fids[(candidate - 1) / 8] &
		     (1U << ((candidate - 1) % 8))) == 0)
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

int agent_metadata_catalog_reconcile_pending(void) {
	return agent_catalog_pending_count != 0;
}

int agent_metadata_catalog_reconcile_slot(int slot) {
	agent_catalog_require_txn();
	if (!agent_catalog_mutation_allowed())
		return AGENT_CATALOG_CONFLICT;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX ||
	    !agent_catalog_files[slot].used)
		return -1;
	if ((agent_catalog_states[slot] & AGENT_CATALOG_STATE_PENDING) == 0)
		return 0;
	agent_catalog_state_clear(slot);
	agent_catalog_changed(AGENT_FILE_CHANGE_MEMBERSHIP);
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
	uint live_pending = 0, removed_pending = 0;

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
		if (agent_catalog_states[i] & AGENT_CATALOG_STATE_PENDING) {
			live_pending++;
			if (reload_one_scope && agent_catalog_files[i].used &&
			    agent_catalog_scopes[i] == reload_scope)
				removed_pending++;
		}
	if (live_pending != agent_catalog_pending_count ||
	    agent_catalog_active_edit != 0)
		panic("Agent catalog apply live invariant");
	result->prepared = result->plan_active = 0;
	memset(&result->delta, 0, sizeof(result->delta));
	result->delta.full_reset = !reload_one_scope;
	result->delta.scope_id = reload_one_scope ? reload_scope : VFS_SCOPE_NONE;
	agent_metadata_txn_projection_begin();
	if (reload_one_scope) {
		agent_catalog_pending_count -= removed_pending;
		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			if (agent_catalog_files[i].used &&
			    agent_catalog_scopes[i] == reload_scope) {
				memset(&agent_catalog_files[i], 0,
				       sizeof(agent_catalog_files[i]));
				agent_catalog_scopes[i] = VFS_SCOPE_NONE;
				agent_catalog_states[i] = 0;
			}
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
		agent_catalog_files[slot] = records[i].meta;
		agent_catalog_scopes[slot] = records[i].scope_id;
		agent_catalog_states[slot] = state;
		if (state & AGENT_CATALOG_STATE_PENDING)
			agent_catalog_pending_count++;
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
		if (scope_id != VFS_SCOPE_SYSTEM &&
		    !workflow_lifecycle_key_valid(lifecycle) &&
		    vfs_scope_lifecycle(scope_id, &lifecycle) < 0) {
			agent_file_state_snapshot_end(enabled);
			return -1;
		}
		record->lifecycle = lifecycle;
	}
	agent_file_state_snapshot_end(enabled);
	return count;
}
