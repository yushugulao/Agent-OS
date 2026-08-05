#ifndef OPEN_FILE_IO_LEASE_H
#define OPEN_FILE_IO_LEASE_H

#include "types.h"
#include "vfs_security.h"

struct file;
struct inode;

struct open_file_io_token {
	uint64 opaque[4];
};

#define OPEN_FILE_IO_TOKEN_INIT { { 0 } }

struct open_file_io_lease_stats {
	uint64 full_auth;
	uint64 lease_hit;
	uint64 revalidation;
};

void open_file_io_lease_file_init(struct file *);
void open_file_io_lease_file_retire(struct file *);
void open_file_io_lease_edit_changed(void);

int open_file_io_lease_acquire(struct file *, enum vfs_operation,
			       struct open_file_io_token *, struct vfs_cred *);
int open_file_io_token_validate(const struct open_file_io_token *,
				struct inode *, enum vfs_operation);
void open_file_io_token_end(struct open_file_io_token *);
void open_file_io_lease_stats_snapshot(struct open_file_io_lease_stats *);

#endif
