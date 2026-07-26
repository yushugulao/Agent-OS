#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_internal.h"
#include "agent_metadata_scan.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "fs.h"
#include "vfs_security.h"

#define SCAN_INTERVAL 20
#define SCAN_STEP 16
#define SCAN_REST_MULTIPLIER 4

static struct {
	uint64 offset, next_tick, last_step_tick, started_tick;
	uint64 runs, entries, added, updated, removed;
	int seen[AGENT_FILE_META_MAX];
} scan;
static struct {
	int enabled, pending, active;
} scan_control;

void
agent_metadata_scan_init(void)
{
	memset(&scan, 0, sizeof(scan));
	memset(&scan_control, 0, sizeof(scan_control));
}

static void
scan_infer_kind(char *name, char *out, int n)
{
	if (agent_metadata_catalog_field_contains(name, "md") ||
	    agent_metadata_catalog_field_contains(name, "txt"))
		safestrcpy(out, "document", n);
	else if (agent_metadata_catalog_field_contains(name, "log") ||
		 agent_metadata_catalog_field_contains(name, "err"))
		safestrcpy(out, "log", n);
	else if (agent_metadata_catalog_field_contains(name, "status") ||
		 agent_metadata_catalog_field_contains(name, "ok"))
		safestrcpy(out, "status", n);
	else if (agent_metadata_catalog_field_contains(name, "data") ||
		 agent_metadata_catalog_field_contains(name, "csv"))
		safestrcpy(out, "dataset", n);
	else
		safestrcpy(out, "file", n);
}

static void
scan_infer_status(char *name, char *out, int n)
{
	if (agent_metadata_catalog_field_contains(name, "fail") ||
	    agent_metadata_catalog_field_contains(name, "err"))
		safestrcpy(out, "failed", n);
	else if (agent_metadata_catalog_field_contains(name, "ok") ||
		 agent_metadata_catalog_field_contains(name, "pass"))
		safestrcpy(out, "ok", n);
	else
		safestrcpy(out, "present", n);
}

uint
agent_metadata_scan_apply_defaults(struct agent_file_meta *meta, char *path,
				   int *changed_out)
{
	int changed = 0;
	uint changes = 0;

	if (meta->physical_name[0] == 0 ||
	    (meta->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		if (strncmp(meta->physical_name, path,
			    sizeof(meta->physical_name)) != 0) {
			safestrcpy(meta->physical_name, path,
				   sizeof(meta->physical_name));
			changed = 1;
			changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
		}
	}
	if (meta->logical_path[0] == 0 ||
	    (meta->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		if (strncmp(meta->logical_path, path,
			    sizeof(meta->logical_path)) != 0) {
			safestrcpy(meta->logical_path, path,
				   sizeof(meta->logical_path));
			changed = 1;
			changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
		}
	}
	if (meta->project[0] == 0 &&
	    (meta->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(meta->project, "root", sizeof(meta->project));
		changed = 1;
		changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
	}
	if (meta->workflow[0] == 0 &&
	    (meta->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(meta->workflow, "background-scan",
			   sizeof(meta->workflow));
		changed = 1;
		changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
	}
	if (meta->run_id[0] == 0 &&
	    (meta->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(meta->run_id, "ROOT", sizeof(meta->run_id));
		changed = 1;
		changes |= AGENT_FILE_CHANGE_SCOPE_KEYS;
	}
	if (meta->stage[0] == 0 &&
	    (meta->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(meta->stage, "scan", sizeof(meta->stage));
		changed = 1;
		changes |= AGENT_FILE_CHANGE_STAGE;
	}
	if (meta->kind[0] == 0 &&
	    (meta->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		scan_infer_kind(path, meta->kind, sizeof(meta->kind));
		changed = 1;
		changes |= AGENT_FILE_CHANGE_KIND;
	}
	if (meta->status[0] == 0 &&
	    (meta->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		scan_infer_status(path, meta->status, sizeof(meta->status));
		changed = 1;
		changes |= AGENT_FILE_CHANGE_STATUS;
	}
	if (meta->summary[0] == 0 &&
	    (meta->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(meta->summary, "auto scanned root file",
			   sizeof(meta->summary));
		changed = 1;
	}
	if (changed_out)
		*changed_out = changed;
	return changes;
}

void
agent_metadata_scan_catalog_sync(const struct agent_catalog_delta *delta)
{
	if (!scan_control.active)
		return;
	if (delta->full_reset) {
		scan.offset = 0;
		scan.started_tick = agent_file_state_now();
		memset(scan.seen, 0, sizeof(scan.seen));
	}
	agent_metadata_txn_work_charge(AGENT_META_STALE_BYTES);
	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		if (delta->applied_slots[i / 8] & (1U << (i % 8)))
			scan.seen[i] = 1;
}

void
agent_metadata_scan_note_slot(int slot)
{
	if (scan_control.active)
		scan.seen[slot] = 1;
}

int
agent_metadata_scan_query_stable(void)
{
	return !scan_control.active;
}

static uint64
scan_rest_deadline(uint64 started_tick, uint64 now)
{
	uint64 duration = now > started_tick ? now - started_tick : 0;
	uint64 rest = SCAN_INTERVAL;

	if (duration > ~0ULL / SCAN_REST_MULTIPLIER)
		return ~0ULL;
	duration *= SCAN_REST_MULTIPLIER;
	if (duration > rest)
		rest = duration;
	if (rest > ~0ULL - now)
		return ~0ULL;
	return now + rest;
}

static void
scan_pause(int retry)
{
	uint64 now = agent_file_state_now();
	uint64 started;
	uint64 deadline;
	int enabled = intr_save();

	started = scan_control.active ? scan.started_tick : now;
	deadline = scan_rest_deadline(started, now);
	scan_control.active = 0;
	scan.started_tick = 0;
	if (retry)
		scan_control.pending = 1;
	if (scan.next_tick < deadline)
		scan.next_tick = deadline;
	intr_restore(enabled);
}

void
agent_file_request_scan(void)
{
	uint64 now = agent_file_state_now();
	int enabled = intr_save();

	if (!scan_control.enabled) {
		scan_control.enabled = 1;
		scan_control.pending = 1;
		scan.next_tick = now;
	} else if (!scan_control.pending) {
		scan_control.pending = 1;
		if (scan_control.active)
			scan.next_tick =
				scan_rest_deadline(now, now);
		else if (now >= scan.next_tick)
			scan.next_tick = now;
	}
	intr_restore(enabled);
}

int
agent_metadata_scan_plan(uint64 now)
{
	int plan = AGENT_METADATA_SCAN_IDLE;
	int enabled = intr_save();

	if (scan_control.enabled) {
		if (!scan_control.active && !scan_control.pending &&
		    now >= scan.next_tick)
			scan_control.pending = 1;
		if (scan_control.active && scan.last_step_tick != now)
			plan = AGENT_METADATA_SCAN_CONTINUE;
		else if (!scan_control.active && scan_control.pending &&
			 now >= scan.next_tick)
			plan = AGENT_METADATA_SCAN_START;
	}
	intr_restore(enabled);
	return plan;
}

static uint
scan_bind_inode(struct inode *ip, char *path)
{
	struct agent_catalog_view view;
	struct agent_catalog_edit edit;
	struct agent_file_meta *meta;
	uint64 fid = 0;
	int slot;
	int added = 0;
	int changed = 0;
	int defaults_changed = 0;
	int inode_changed = 0;
	int persistent;
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
	if (agent_metadata_catalog_borrow(0, slot, &view) <= 0 ||
	    view.scope_id != ip->vfs_scope_id ||
	    view.meta->dev != ip->dev || view.meta->inum != ip->inum ||
	    view.meta->incarnation != ip->vfs_incarnation) {
		slot = agent_metadata_catalog_find(ip->vfs_scope_id, 0, path);
		if (agent_metadata_catalog_borrow(0, slot, &view) <= 0)
			slot = -1;
	}
	if (slot >= 0 && view.meta->dev != 0 &&
	    (view.meta->dev != ip->dev || view.meta->inum != ip->inum ||
	     view.meta->incarnation != ip->vfs_incarnation) &&
	    (view.meta->flags & AGENT_FILE_META_F_AUTOSCAN) == 0)
		slot = -1;
	if (slot < 0) {
		slot = agent_metadata_catalog_alloc_slot(ip->vfs_scope_id);
		if (slot < 0)
			return 0;
		fid = agent_metadata_catalog_alloc_fid(ip->vfs_scope_id);
		if (fid == 0)
			return 0;
	}
	if (agent_metadata_catalog_edit_begin(
		    slot, ip->vfs_scope_id, &edit) < 0)
		return 0;
	meta = edit.meta;
	if (!meta->used) {
		memset(meta, 0, sizeof(*meta));
		meta->used = 1;
		meta->fid = fid;
		meta->flags = AGENT_FILE_META_F_PERSIST |
			      AGENT_FILE_META_F_AUTOSCAN;
		edit.scope_id = ip->vfs_scope_id;
		added = 1;
		changes |= AGENT_FILE_CHANGE_MEMBERSHIP;
	}
	if (edit.scope_id != ip->vfs_scope_id) {
		agent_metadata_catalog_edit_abort(&edit);
		return 0;
	}
	changes |= agent_metadata_scan_apply_defaults(
		meta, path, &defaults_changed);
	changed |= defaults_changed;
	if (meta->dev != ip->dev || meta->inum != ip->inum ||
	    meta->incarnation != ip->vfs_incarnation ||
	    meta->size != ip->size) {
		meta->dev = ip->dev;
		meta->inum = ip->inum;
		meta->incarnation = ip->vfs_incarnation;
		meta->size = ip->size;
		changed = 1;
	}
	if (ip->agent_meta_slot != slot + 1 ||
	    ip->agent_meta_version != AGENT_INODE_META_VERSION)
		inode_changed = 1;
	if (inode_changed)
		changed = 1;
	if (changed || added) {
		meta->updated_tick = agent_file_state_now();
		meta->fs_generation =
			agent_file_state_generation_next(edit.scope_id);
		persistent = meta->flags & AGENT_FILE_META_F_PERSIST;
		if (agent_metadata_catalog_edit_commit(&edit, changes) < 0)
			return 0;
		if (inode_changed) {
			ip->agent_meta_slot = slot + 1;
			ip->agent_meta_flags = persistent;
			ip->agent_meta_version = AGENT_INODE_META_VERSION;
			iupdate(ip);
		}
		if (persistent)
			agent_metadata_store_mark_dirty(ip->vfs_scope_id);
		if (added)
			scan.added++;
		else
			scan.updated++;
	} else {
		agent_metadata_catalog_edit_abort(&edit);
	}
	scan.seen[slot] = 1;
	return changes;
}

uint
agent_metadata_scan_step(uint64 now, int plan, int load_ok)
{
	struct inode *root;
	struct inode *ip;
	struct dirent de;
	struct vfs_cred kernel_cred;
	char name[DIRSIZ + 1];
	uint64 off;
	uint changes = 0;
	int scan_failed = 0;
	int steps = 0;
	int enabled;

	if (plan == AGENT_METADATA_SCAN_START) {
		if (!load_ok) {
			scan_pause(1);
			return 0;
		}
		enabled = intr_save();
		scan_control.pending = 0;
		scan_control.active = 1;
		scan.started_tick = now;
		scan.offset = 0;
		memset(scan.seen, 0, sizeof(scan.seen));
		scan.runs++;
		intr_restore(enabled);
	} else if (plan != AGENT_METADATA_SCAN_CONTINUE) {
		return 0;
	}
	scan.last_step_tick = now;
	vfs_cred_kernel(&kernel_cred);
	root = root_dir();
	if (root == 0) {
		scan_pause(1);
		return 0;
	}
	for (off = scan.offset;
	     off < root->size && steps < SCAN_STEP;
	     off += sizeof(de), steps++) {
		if (readi(root, &kernel_cred, 0, (uint64)&de, off,
			  sizeof(de)) != sizeof(de)) {
			scan_failed = 1;
			break;
		}
		scan.entries++;
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
		changes |= scan_bind_inode(ip, name);
		iput(ip);
	}
	scan.offset = off;
	if (scan_failed) {
		scan_pause(1);
		iput(root);
		return changes;
	}
	if (scan.offset >= root->size) {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			struct agent_catalog_view view;
			int removed_persistent;
			uint removed_scope;

			if (agent_metadata_catalog_borrow(0, i, &view) <= 0)
				continue;
			if (!scan.seen[i]) {
				removed_scope = view.scope_id;
				removed_persistent =
					view.meta->flags & AGENT_FILE_META_F_PERSIST;
				agent_metadata_catalog_clear_slot(i);
				scan.removed++;
				if (removed_persistent)
					agent_metadata_store_mark_dirty(removed_scope);
				changes |= AGENT_FILE_CHANGE_ALL;
			}
		}
		scan_pause(0);
	}
	iput(root);
	return changes;
}

void
agent_metadata_scan_fill_info(struct agent_info *info)
{
	info->file_scan_runs = scan.runs;
	info->file_scan_entries = scan.entries;
	info->file_scan_added = scan.added;
	info->file_scan_updated = scan.updated;
	info->file_scan_removed = scan.removed;
	info->file_scan_pending =
		scan_control.pending || scan_control.active;
}
