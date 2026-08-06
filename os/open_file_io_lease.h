#ifndef OPEN_FILE_IO_LEASE_H
#define OPEN_FILE_IO_LEASE_H

#include "resource_controller.h"
#include "types.h"
#include "vfs_security.h"

struct file;
struct inode;
struct proc;

/* Kernel-only, typed authority carried by one read/write syscall. */
struct open_file_io_token {
	struct file *file;
	struct inode *inode;
	struct proc *subject;
	struct resource_account_handle account;
	struct workflow_lifecycle_key lifecycle;
	const struct vfs_cred *cred;
	uint64 edit_authority_generation;
	uint64 edit_deadline_tick;
	uint64 thread_generation;
	uint64 receipt_generation;
	uint inode_incarnation;
	uint inode_checksum;
	uint inode_policy_generation;
	uint inode_exec_size;
	uint inode_exec_flags;
	uint inode_exec_generation;
	uint inode_exec_role_mask;
	uint inode_exec_layout_version;
	uint inode_exec_rw_offset;
	uint inode_exec_profile;
	enum vfs_operation operation;
	uchar valid;
};

#define OPEN_FILE_IO_TOKEN_INIT { 0 }

struct open_file_io_lease_stats {
	uint64 full_auth;
	uint64 lease_hit;
	uint64 revalidation;
};

void open_file_io_lease_seed_authorized(struct file *, enum vfs_operation,
					const struct vfs_cred *);
int open_file_io_lease_acquire(struct file *, enum vfs_operation,
			       struct open_file_io_token *, struct vfs_cred *);
int open_file_io_token_validate(const struct open_file_io_token *,
				struct inode *, enum vfs_operation);
void open_file_io_token_end(struct open_file_io_token *);
void open_file_io_lease_stats_snapshot(struct open_file_io_lease_stats *);

#endif
