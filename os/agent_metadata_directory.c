#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_directory.h"
#include "agent_metadata_internal.h"
#include "agent_metadata_scan.h"
#include "defs.h"

#define FS_META_UNBOUND(ip) ((ip) && !(ip)->agent_meta_slot && !(ip)->agent_meta_flags && !(ip)->agent_meta_version)

static void agent_fs_publish_content(struct inode *ip) {
	struct agent_file_content_receipt receipt;
	int reconcile = 0;

	if (!agent_metadata_inode_trackable(ip))
		return;
	if (ip->agent_meta_slot <= 0 ||
	    ip->agent_meta_version != AGENT_INODE_META_VERSION ||
	    !agent_file_state_content_publish(ip, &receipt)) {
		agent_file_request_scan();
		return;
	}
	if (ip->agent_meta_flags & AGENT_FILE_META_F_PERSIST) {
		if (agent_metadata_catalog_journal_note_content(&receipt) < 0)
			reconcile = 1;
		agent_metadata_store_mark_dirty(ip->vfs_scope_id);
	}
	if (reconcile ||
	    (ip->agent_meta_flags & AGENT_FILE_META_F_AUTOSCAN))
		agent_file_request_scan();
}

static void agent_fs_remove_inode(struct inode *ip) {
	struct agent_catalog_view view;
	int slot, persist;
	uint scope_id;

	if (!agent_metadata_inode_trackable(ip))
		return;
	scope_id = ip->vfs_scope_id;
	if (FS_META_UNBOUND(ip)) {
		agent_file_version_reclaim(ip);
		return;
	}
	if (ip->agent_meta_slot <= 0 ||
	    ip->agent_meta_version != AGENT_INODE_META_VERSION) {
		agent_file_version_reclaim(ip);
		agent_file_request_scan();
		return;
	}
	agent_file_state_content_bump(ip);
	if (!agent_metadata_txn_try_external()) {
		agent_file_request_scan();
		return;
	}
	if (!agent_metadata_inode_trackable(ip) ||
	    !agent_metadata_store_loaded())
		goto rescan;
	slot = ip->agent_meta_slot - 1;
	if (agent_metadata_catalog_borrow(0, slot, &view) <= 0 ||
	    view.scope_id != scope_id || view.meta->dev != ip->dev ||
	    view.meta->inum != ip->inum ||
	    view.meta->incarnation != ip->vfs_incarnation)
		goto rescan;
	persist = view.meta->flags & AGENT_FILE_META_F_PERSIST;
	if (agent_metadata_catalog_clear_slot(slot) < 0)
		goto rescan;
	agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_ALL);
	agent_metadata_scan_slot_freed(scope_id);
	if (persist)
		agent_metadata_store_mark_dirty(scope_id);
	goto out;
rescan:
	agent_file_request_scan();
out:
	agent_metadata_txn_unlock();
}

void agent_fs_note_write(struct inode *ip) {
	if (FS_META_UNBOUND(ip)) return;
	agent_fs_publish_content(ip);
}

void agent_fs_note_truncate(struct inode *ip) {
	if (FS_META_UNBOUND(ip)) return;
	agent_fs_publish_content(ip);
}

void agent_fs_note_delete(struct inode *ip) {
	agent_fs_remove_inode(ip);
}
