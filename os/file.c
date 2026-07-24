#include "file.h"
#include "bio.h"
#include "defs.h"
#include "exec_policy.h"
#include "fcntl.h"
#include "fs.h"
#include "kernel_work.h"
#include "proc.h"
#include "vfs_security.h"

//This is a system-level open file table that holds open files of all process.
struct file filepool[FILEPOOLSIZE];

//Abstract the stdio into a file.
struct file *stdio_init(int fd, struct proc *owner)
{
	struct file *f = filealloc(owner);
	if (f == 0)
		return 0;
	f->type = FD_STDIO;
	f->inherit_class = FD_INHERIT_STDIO;
	f->readable = (fd == STDIN || fd == STDERR);
	f->writable = (fd == STDOUT || fd == STDERR);
	return f;
}

//The operation performed on the system-level open file table entry after some process closes a file.
void fileclose(struct file *f)
{
	int type;
	int writable;
	struct pipe *pipe;
	struct inode *ip;
	int domain_id;
	int reserved;
	int enabled;

	if (fd_is_reserved(f))
		return;
	enabled = intr_save();
	if (f->ref < 1)
		panic("fileclose");
	if (--f->ref > 0) {
		intr_restore(enabled);
		return;
	}

	// Publish the free slot before cleanup, which may cross safe points.
	type = f->type;
	writable = f->writable;
	pipe = f->pipe;
	ip = f->ip;
	domain_id = f->resource_domain_id;
	reserved = f->resource_reserved;
	f->off = 0;
	f->readable = 0;
	f->writable = 0;
	f->pipe = 0;
	f->ip = 0;
	f->ref = 0;
	f->type = FD_NONE;
	f->inherit_class = FD_INHERIT_DENY;
	f->resource_domain_id = -1;
	f->resource_reserved = 0;
	proc_file_slot_release(domain_id, reserved);
	intr_restore(enabled);

	switch (type) {
	case FD_NONE:
		// A reserved file slot may be released before it is initialized.
		break;
	case FD_STDIO:
		// Do nothing
		break;
	case FD_PIPE:
		pipeclose(pipe, writable);
		break;
	case FD_INODE:
		iput(ip);
		break;
	default:
		panic("unknown file type %d\n", type);
	}
}

// Pin an open-file entry across operations that may yield.
struct file *filedup(struct file *f)
{
	int enabled = intr_save();

	if (f == 0 || fd_is_reserved(f) || f->ref < 1) {
		intr_restore(enabled);
		return 0;
	}
	f->ref++;
	intr_restore(enabled);
	return f;
}

//Add a new system-level table entry for the open file table
struct file *filealloc(struct proc *owner)
{
	struct file *result = 0;
	int enabled = intr_save();

	for (int i = 0; i < FILEPOOLSIZE; ++i) {
		if (filepool[i].ref == 0) {
			struct file *f = &filepool[i];
			int domain_id;
			int reserved;

			if (proc_file_slot_reserve(owner, &domain_id, &reserved) < 0)
				break;
			f->type = FD_NONE;
			f->inherit_class = FD_INHERIT_DENY;
			f->ref = 1;
			f->readable = 0;
			f->writable = 0;
			f->pipe = 0;
			f->ip = 0;
			f->off = 0;
			f->resource_domain_id = domain_id;
			f->resource_reserved = reserved;
			result = f;
			break;
		}
	}
	intr_restore(enabled);
	return result;
}

//Show names of all files in the root_dir.
int show_all_files()
{
	struct inode *root = root_dir();
	struct vfs_cred cred;
	int result;

	if (root == 0)
		return -1;
	vfs_cred_from_proc(curr_proc(), &cred);
	result = dirls(root, &cred);
	iput(root);
	return result;
}

static int filetruncate(struct inode *ip, const struct vfs_cred *cred)
{
	struct inode_reclaim reclaim;

	if (!agent_edit_truncate_allowed(ip) ||
	    itruncate_detach(ip, cred, 0, &reclaim) < 0)
		return -1;
	// Commit both version domains before reclaim or persistence may yield.
	agent_edit_note_truncate(ip);
	agent_fs_note_truncate(ip);
	itruncate_reclaim(&reclaim);
	return 0;
}

//A process creates or opens a file according to its path, returning the file descriptor of the created or opened file.
//If omode is O_CREATE, create a new file
//if omode if the others,open a created file.
int fileopen(char *path, uint64 omode)
{
	int fd = -1;
	struct file *f = 0;
	struct inode *ip = 0;
	struct vfs_cred cred;
	uint policy;
	int created = 0;

	if (agent_file_is_meta_store_name(path))
		return -1;
	vfs_cred_from_proc(curr_proc(), &cred);
	policy = vfs_default_create_policy(&cred);
	if ((omode & O_CREATE) && policy == 0)
		return -1;
	if ((omode & O_CREATE) &&
	    !vfs_create_request_authorize(
		    &cred, policy, !(omode & O_WRONLY),
		    (omode & (O_WRONLY | O_RDWR)) != 0,
		    (omode & O_TRUNC) != 0))
		return -1;
	if ((f = filealloc(curr_proc())) == 0)
		return -1;
	if ((fd = fdreserve()) < 0) {
		fileclose(f);
		return -1;
	}

	if (omode & O_CREATE) {
		ip = fs_create(path, T_FILE, &created, &cred, policy);
		if (ip == 0)
			goto fail;
	} else {
		if ((ip = namei_scope(path, vfs_cred_lookup_policy(&cred),
				      cred.scope_id)) == 0) {
			if ((omode & (O_WRONLY | O_RDWR | O_TRUNC)) != 0 ||
			    cred.scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
			    (ip = namei_scope(path, VFS_POLICY_WORKFLOW,
					      VFS_SCOPE_SYSTEM)) == 0)
				goto fail;
		}
		ivalid(ip);
	}
	if (ip->type != T_FILE)
		goto fail;
	if (!created) {
		if (!(omode & O_WRONLY) &&
		    !vfs_inode_authorize(ip, &cred, VFS_OP_READ))
			goto fail;
		if ((omode & (O_WRONLY | O_RDWR)) &&
		    !vfs_inode_authorize(ip, &cred, VFS_OP_WRITE))
			goto fail;
		if ((omode & O_TRUNC) &&
		    !vfs_inode_authorize(ip, &cred, VFS_OP_TRUNCATE))
			goto fail;
		if ((omode & (O_WRONLY | O_RDWR | O_TRUNC)) &&
		    !exec_policy_inode_mutable(ip))
			goto fail;
		if ((omode & O_TRUNC) && filetruncate(ip, &cred) < 0)
			goto fail;
	}
	// only support FD_INODE
	f->type = FD_INODE;
	f->inherit_class = FD_INHERIT_REAUTHORIZE;
	f->off = 0;
	f->ip = ip;
	f->readable = !(omode & O_WRONLY);
	f->writable = (omode & O_WRONLY) || (omode & O_RDWR);
	if (created)
		agent_fs_note_create(ip, path);
	if (fdinstall(fd, f) < 0)
		goto fail;
	return fd;

fail:
	if (ip && f->ip == 0)
		iput(ip);
	fdrelease(fd);
	fileclose(f);
	return -1;
}

int fileunlink(char *path)
{
	struct inode *dp;
	struct inode *ip;
	struct vfs_cred cred;
	uint policy;
	uint offset;

	if (agent_file_is_meta_store_name(path))
		return -1;
	vfs_cred_from_proc(curr_proc(), &cred);
	policy = vfs_cred_lookup_policy(&cred);

	dp = root_dir();
	if (dp == 0)
		return -1;
	ivalid(dp);
	if ((ip = dirlookup(dp, path, &offset, policy, cred.scope_id, 0)) == 0) {
		iput(dp);
		return -1;
	}
	ivalid(ip);
	if (ip->type != T_FILE) {
		iput(ip);
		iput(dp);
		return -1;
	}
	if (!exec_policy_inode_mutable(ip)) {
		iput(ip);
		iput(dp);
		return -1;
	}
	if (!vfs_inode_authorize(ip, &cred, VFS_OP_DELETE)) {
		iput(ip);
		iput(dp);
		return -1;
	}
	if (!agent_edit_unlink_allowed(ip)) {
		iput(ip);
		iput(dp);
		return -1;
	}
	if (dirunlink(dp, path, offset, ip->inum, ip->vfs_incarnation,
		      &cred, policy) < 0) {
		iput(ip);
		iput(dp);
		return -1;
	}
	agent_fs_note_delete(ip);
	agent_edit_note_delete(ip);
	ip->removed = 1;
	iput(ip);
	iput(dp);
	return 0;
}

// Write data to inode.
uint64 inodewrite(struct file *f, uint64 va, uint64 len)
{
	uint64 done = 0;
	uint64 user_addr;
	uint64 remaining;
	uint offset;
	uint chunk;
	int checkpoint;
	int noted = 0;
	int r;
	struct vfs_cred cred;

	if (len == 0)
		return 0;
	ivalid(f->ip);
	if (f->ip->type != T_FILE)
		return (uint64)-1;
	if (!exec_policy_inode_mutable(f->ip))
		return (uint64)-1;
	vfs_cred_from_proc(curr_proc(), &cred);
	if (!vfs_inode_authorize(f->ip, &cred, VFS_OP_WRITE))
		return (uint64)-1;
	if (!agent_edit_write_allowed(f->ip))
		return (uint64)-1;

	while (done < len) {
		offset = f->off;
		remaining = len - done;
		chunk = BSIZE - offset % BSIZE;
		if (remaining < chunk)
			chunk = (uint)remaining;
		if (checked_user_offset(va, done, 1, &user_addr) < 0)
			return done == 0 ? (uint64)-1 : done;
		r = writei(f->ip, &cred, 1, user_addr, offset, chunk);
		if (r <= 0)
			return done == 0 ? (uint64)r : done;

		f->off = offset + r;
		done += r;
		if (!noted) {
			agent_edit_note_write(f->ip);
			agent_fs_note_write(f->ip);
			noted = 1;
		} else {
			agent_fs_sync_write(f->ip);
		}
		if (bio_request_checkpoint() < 0)
			return done;
		checkpoint = kernel_work_checkpoint((uint)r);
		if (checkpoint != 0 || (uint)r < chunk)
			return done;
	}
	return done;
}

//Read data from inode.
uint64 inoderead(struct file *f, uint64 va, uint64 len)
{
	uint64 done = 0;
	uint64 user_addr;
	uint64 remaining;
	uint offset;
	uint chunk;
	int checkpoint;
	int r;
	struct vfs_cred cred;

	if (len == 0)
		return 0;
	ivalid(f->ip);
	if (f->ip->type != T_FILE)
		return (uint64)-1;
	vfs_cred_from_proc(curr_proc(), &cred);

	while (done < len) {
		offset = f->off;
		remaining = len - done;
		chunk = BSIZE - offset % BSIZE;
		if (remaining < chunk)
			chunk = (uint)remaining;
		if (checked_user_offset(va, done, 1, &user_addr) < 0)
			return done == 0 ? (uint64)-1 : done;
		r = readi(f->ip, &cred, 1, user_addr, offset, chunk);
		if (r <= 0)
			return done == 0 ? (uint64)r : done;

		f->off = offset + r;
		done += r;
		if (bio_request_checkpoint() < 0)
			return done;
		checkpoint = kernel_work_checkpoint((uint)r);
		if (checkpoint != 0 || (uint)r < chunk)
			return done;
	}
	return done;
}
