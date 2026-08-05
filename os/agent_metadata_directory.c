#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_metadata_directory.h"
#include "agent_metadata_internal.h"
#include "agent_metadata_scan.h"
#include "defs.h"

static int fs_create_indexable(struct inode *ip) {
	return agent_metadata_inode_trackable(ip) &&
	       agent_scope_valid(ip->vfs_scope_id) && ip->agent_meta_slot <= 0;
}

void agent_fs_note_create(struct inode *ip, char *path) {
	uint changes;
	char key[DIRSIZ + 1];
	int failed = 0;
	if (!ip || fs_dirent_canonicalize(path, key) < 0 ||
	    agent_file_is_meta_store_name(key))
		return;
	if (!fs_create_indexable(ip))
		return;
	if (!agent_metadata_txn_try_external()) {
		agent_file_request_scan();
		return;
	}
	if (!fs_create_indexable(ip) || !agent_metadata_store_loaded())
		goto rescan;
	agent_file_state_content_bump(ip);
	changes = agent_metadata_scan_index_inode(ip, key, &failed);
	if (changes)
		agent_metadata_note_catalog_changes(changes);
	if (failed || agent_file_state_index_deferred(ip))
		goto rescan;
	goto out;
rescan:
	agent_file_request_scan();
out:
	agent_metadata_txn_unlock();
}

static void agent_fs_publish_content(struct inode *ip) {
	struct agent_file_content_receipt receipt;
	int reconcile = 0;

	if (!agent_metadata_inode_trackable(ip))
		return;
	if (!agent_file_state_content_publish(ip, &receipt) ||
	    ip->agent_meta_slot <= 0 ||
	    ip->agent_meta_version != AGENT_INODE_META_VERSION) {
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
	agent_file_state_content_bump(ip);
	scope_id = ip->vfs_scope_id;
	if (agent_file_state_index_deferred(ip)) {
		agent_file_request_scan();
		return;
	}
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
	agent_fs_publish_content(ip);
}

void agent_fs_note_truncate(struct inode *ip) {
	agent_fs_publish_content(ip);
}

void agent_fs_note_delete(struct inode *ip) {
	agent_fs_remove_inode(ip);
}
