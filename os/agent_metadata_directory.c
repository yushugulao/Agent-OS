#include "agent_file_state_internal.h"
#include "agent_internal.h"
#include "agent_live_query_events.h"
#include "agent_metadata_directory.h"
#include "agent_metadata_internal.h"
#include "defs.h"
#include "vfs_security.h"

#define FS_META_UNBOUND(ip) ((ip) && !(ip)->agent_meta_slot && !(ip)->agent_meta_flags && !(ip)->agent_meta_version)

static void agent_fs_publish_content(struct inode *ip) {
	struct agent_file_content_receipt receipt;

	if (!agent_metadata_inode_trackable(ip))
		return;
	if (ip->agent_meta_slot <= 0 ||
	    ip->agent_meta_version != AGENT_INODE_META_VERSION ||
	    !agent_file_state_content_publish(ip, &receipt))
		return;
	(void)agent_live_query_content_enqueue(
		&receipt, agent_file_state_scope_generation(receipt.scope_id));
	agent_background_request();
}

static void agent_fs_remove_inode(struct inode *ip) {
	struct workflow_lifecycle_key lifecycle = workflow_lifecycle_none();
	struct agent_file_meta previous;
	uint64 generation = 0;
	uint scope_id;
	uint dev, inum, incarnation;
	int removed;

	if (FS_META_UNBOUND(ip)) {
		agent_file_version_reclaim(ip);
		return;
	}
	if (!agent_metadata_inode_trackable(ip))
		return;
	scope_id = ip->vfs_scope_id;
	if (ip->agent_meta_slot <= 0 ||
	    ip->agent_meta_version != AGENT_INODE_META_VERSION) {
		agent_file_version_reclaim(ip);
		return;
	}
	dev = ip->dev;
	inum = ip->inum;
	incarnation = ip->vfs_incarnation;
	if (vfs_scope_lifecycle(scope_id, &lifecycle) < 0 ||
	    !workflow_lifecycle_key_valid(lifecycle))
		return;
	agent_file_state_content_bump(ip);
	if (!agent_metadata_txn_try_external()) {
		(void)agent_live_query_tombstone_enqueue(
			lifecycle, scope_id, dev, inum, incarnation);
		agent_background_request();
		return;
	}
	removed = agent_metadata_catalog_remove_identity_exact(
		lifecycle, scope_id, dev, inum, incarnation,
		&previous, &generation);
	if (removed > 0) {
		agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_ALL);
		(void)agent_live_query_publish_transition(
			curr_proc(), lifecycle, scope_id, &previous, 0, generation);
	} else if (removed < 0) {
		(void)agent_live_query_tombstone_enqueue(
			lifecycle, scope_id, dev, inum, incarnation);
		agent_background_request();
	}
	(void)agent_live_query_content_drain(4);
	(void)agent_live_query_tombstone_drain(4);
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
