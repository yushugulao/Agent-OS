#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_directory.h"
#include "agent_metadata_internal.h"
#include "agent_metadata_scan.h"
#include "defs.h"

void
agent_fs_note_create(struct inode *ip, char *path)
{
	struct agent_catalog_edit edit;
	struct agent_file_meta *meta;
	int slot = -1;
	int reconcile = 0;
	uint scope_id;
	uint64 fid;

	if (ip == 0 || path == 0 || agent_file_is_meta_store_name(path))
		return;
	if (!agent_metadata_inode_trackable(ip) ||
	    !agent_scope_valid(ip->vfs_scope_id) || ip->agent_meta_slot > 0)
		return;
	scope_id = ip->vfs_scope_id;
	if (!agent_metadata_txn_try_external()) {
		agent_file_request_scan();
		return;
	}
	if (!agent_metadata_inode_trackable(ip) ||
	    !agent_scope_valid(ip->vfs_scope_id) ||
	    ip->agent_meta_slot > 0 || !agent_metadata_store_loaded()) {
		reconcile = 1;
		goto out_txn;
	}
	agent_file_state_content_bump(ip);
	slot = agent_metadata_catalog_alloc_slot(scope_id);
	if (slot < 0) {
		reconcile = 1;
		goto out_txn;
	}
	fid = agent_metadata_catalog_alloc_fid(scope_id);
	if (fid == 0) {
		reconcile = 1;
		goto out_txn;
	}
	if (agent_metadata_catalog_edit_begin(slot, scope_id, &edit) < 0) {
		reconcile = 1;
		goto out_txn;
	}
	meta = edit.meta;
	memset(meta, 0, sizeof(*meta));
	meta->used = 1;
	meta->fid = fid;
	meta->flags = AGENT_FILE_META_F_PERSIST |
		      AGENT_FILE_META_F_AUTOSCAN;
	safestrcpy(meta->physical_name, path, sizeof(meta->physical_name));
	safestrcpy(meta->logical_path, path, sizeof(meta->logical_path));
	safestrcpy(meta->kind, "file", sizeof(meta->kind));
	safestrcpy(meta->status, "created", sizeof(meta->status));
	safestrcpy(meta->summary, "created by fileopen", sizeof(meta->summary));
	meta->updated_tick = agent_file_state_now();
	if (agent_metadata_catalog_edit_commit(
		    &edit, AGENT_FILE_CHANGE_ALL) < 0) {
		reconcile = 1;
		goto out_txn;
	}
	agent_metadata_scan_note_slot(slot);
	if (agent_metadata_catalog_bind(slot, 0, 0) < 0) {
		agent_metadata_catalog_clear_slot(slot);
		reconcile = 1;
		goto out_txn;
	}
	agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_ALL);
	agent_metadata_store_mark_dirty(scope_id);
out_txn:
	if (reconcile)
		agent_file_request_scan();
	agent_metadata_txn_unlock();
}

static void
agent_fs_update_inode_meta(struct inode *ip, char *summary, int published)
{
	struct agent_catalog_view view;
	struct agent_catalog_edit edit;
	struct agent_file_meta *meta;
	int slot;
	int persistent;
	int reconcile = 0;
	uint scope_id;

	scope_id = ip->vfs_scope_id;
	if (!agent_metadata_txn_try_external()) {
		/* Published state avoids a contention scan. */
		if (published > 0 &&
		    (ip->agent_meta_flags & AGENT_FILE_META_F_PERSIST))
			agent_metadata_store_mark_dirty(scope_id);
		else if (published < 0)
			agent_file_request_scan();
		return;
	}
	if (!agent_metadata_inode_trackable(ip) ||
	    !agent_metadata_store_loaded()) {
		reconcile = 1;
		goto out_txn;
	}
	slot = ip->agent_meta_slot - 1;
	if (agent_metadata_catalog_borrow(0, slot, &view) <= 0 ||
	    view.scope_id != scope_id || view.meta->dev != ip->dev ||
	    view.meta->inum != ip->inum ||
	    view.meta->incarnation != ip->vfs_incarnation ||
	    agent_metadata_catalog_edit_begin(slot, scope_id, &edit) < 0) {
		reconcile = 1;
		goto out_txn;
	}
	meta = edit.meta;
	meta->size = ip->size;
	if (published > 0)
		agent_file_state_overlay_published_size(meta, scope_id);
	else {
		meta->updated_tick = agent_file_state_now();
		meta->fs_generation =
			agent_file_state_generation_next(scope_id);
	}
	if (summary && summary[0] &&
	    (meta->flags & AGENT_FILE_META_F_AUTOSCAN))
		safestrcpy(meta->summary, summary, sizeof(meta->summary));
	persistent = meta->flags & AGENT_FILE_META_F_PERSIST;
	if (agent_metadata_catalog_edit_commit(&edit, 0) < 0) {
		reconcile = 1;
		goto out_txn;
	}
	if (persistent)
		agent_metadata_store_mark_dirty(scope_id);
out_txn:
	if (reconcile)
		agent_file_request_scan();
	agent_metadata_txn_unlock();
}

void
agent_fs_note_write(struct inode *ip)
{
	int published;

	if (!agent_metadata_inode_trackable(ip))
		return;
	agent_file_state_content_bump(ip);
	published = agent_file_state_size_publish(ip, 1);
	agent_fs_update_inode_meta(ip, "file content updated", published);
}

void
agent_fs_sync_write(struct inode *ip)
{
	int published;

	if (!agent_metadata_inode_trackable(ip))
		return;
	published = agent_file_state_size_publish(ip, 0);
	if (published != 0)
		agent_fs_update_inode_meta(ip, "file content updated", published);
}

void
agent_fs_note_truncate(struct inode *ip)
{
	int published;

	if (!agent_metadata_inode_trackable(ip))
		return;
	agent_file_state_content_bump(ip);
	published = agent_file_state_size_publish(ip, 1);
	agent_fs_update_inode_meta(ip, "file truncated", published);
}

void
agent_fs_note_delete(struct inode *ip)
{
	struct agent_catalog_view view;
	int slot;
	int persistent;
	int reconcile = 0;
	uint scope_id;

	if (!agent_metadata_inode_trackable(ip))
		return;
	scope_id = ip->vfs_scope_id;
	agent_file_state_content_bump(ip);
	if (!agent_metadata_txn_try_external()) {
		agent_file_request_scan();
		return;
	}
	if (!agent_metadata_inode_trackable(ip) ||
	    !agent_metadata_store_loaded()) {
		reconcile = 1;
		goto out_txn;
	}
	slot = ip->agent_meta_slot - 1;
	if (agent_metadata_catalog_borrow(0, slot, &view) <= 0 ||
	    view.scope_id != scope_id || view.meta->dev != ip->dev ||
	    view.meta->inum != ip->inum ||
	    view.meta->incarnation != ip->vfs_incarnation) {
		reconcile = 1;
		goto out_txn;
	}
	persistent = view.meta->flags & AGENT_FILE_META_F_PERSIST;
	agent_metadata_catalog_clear_slot(slot);
	ip->agent_meta_slot = 0;
	ip->agent_meta_flags = 0;
	ip->agent_meta_version = 0;
	iupdate(ip);
	agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_ALL);
	if (persistent)
		agent_metadata_store_mark_dirty(scope_id);
out_txn:
	if (reconcile)
		agent_file_request_scan();
	agent_metadata_txn_unlock();
}
