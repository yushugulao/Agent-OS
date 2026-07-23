#ifndef VFS_SECURITY_H
#define VFS_SECURITY_H

#include "types.h"

struct inode;
struct proc;
struct user_image;

struct vfs_cred {
	uint scope_id;
	uint storage_principal_id;
	uint64 capabilities;
	int kernel;
};

enum vfs_spawn_scope_mode {
	VFS_SPAWN_SCOPE_DROP = 0,
	VFS_SPAWN_SCOPE_INHERIT,
	VFS_SPAWN_SCOPE_FRESH,
};

enum vfs_operation {
	VFS_OP_LOOKUP = 1,
	VFS_OP_READ,
	VFS_OP_CREATE,
	VFS_OP_WRITE,
	VFS_OP_TRUNCATE,
	VFS_OP_DELETE,
	VFS_OP_EXEC,
};

#define VFS_CAP_CONTENT_READ   (1ULL << 1)
#define VFS_CAP_ARTIFACT_WRITE (1ULL << 6)
#define VFS_CAP_WORKFLOW \
	(VFS_CAP_CONTENT_READ | VFS_CAP_ARTIFACT_WRITE)

// At most four workflows may be active. Retiring identities remain in the
// lifecycle ledger until reclamation ends, but no longer consume an active
// admission slot.
#define VFS_SCOPE_MAX_ACTIVE 4
// The resumable filesystem reclaimer owns one cursor per retiring scope.
// Active and retiring identities share this bounded lifecycle ledger, so a
// burst of exits cannot overrun the reclaimer after admission has succeeded.
#define VFS_SCOPE_LIFECYCLE_CAP (VFS_SCOPE_MAX_ACTIVE * 2)
#define VFS_SCOPE_MAX_RETIRING VFS_SCOPE_LIFECYCLE_CAP

void vfs_cred_kernel(struct vfs_cred *);
void vfs_cred_from_proc(const struct proc *, struct vfs_cred *);
uint vfs_cred_lookup_policy(const struct vfs_cred *);
void vfs_proc_reset(struct proc *);
void vfs_proc_drop_to_public(struct proc *);
int vfs_scope_active(uint scope_id);
int vfs_scope_retiring(uint scope_id);
int vfs_scope_retained(uint scope_id);
uint vfs_scope_storage_guarantee(uint exempt_scope, int inode,
				 uint guarantee);
int vfs_scope_storage_reserve(uint scope_id, int inode, uint limit);
int vfs_scope_storage_release(uint scope_id, int inode);
void vfs_scope_reap_pending(void);
int vfs_proc_spawn_scope(const struct proc *, struct proc *,
			 enum vfs_spawn_scope_mode);
void vfs_proc_limit_capabilities(struct proc *, uint64);
int vfs_proc_delegate_exec(const struct proc *, struct proc *, struct inode *,
			   uint64);
void vfs_proc_install_image(struct proc *, const struct user_image *, int);

int vfs_exec_profile_valid(uint);
uint64 vfs_exec_profile_capabilities(uint);
int vfs_inode_label_valid(struct inode *);
int vfs_inode_authorize(struct inode *, const struct vfs_cred *,
			 enum vfs_operation);
uint vfs_default_create_policy(const struct vfs_cred *);
int vfs_create_request_authorize(const struct vfs_cred *, uint, int, int,
				 int);
int vfs_inode_init_label(struct inode *, const struct vfs_cred *, uint);
int vfs_inode_create_matches(struct inode *, const struct vfs_cred *, uint);
void vfs_inode_mark_free(struct inode *);
uint vfs_label_checksum(uint, uint, uint, uint, uint, uint, uint, uint, uint,
			uint, uint);

#endif
