#include "file.h"
#include "defs.h"
#include "fcntl.h"
#include "fs.h"
#include "kernel_work.h"
#include "proc.h"

//This is a system-level open file table that holds open files of all process.
struct file filepool[FILEPOOLSIZE];

//Abstract the stdio into a file.
struct file *stdio_init(int fd)
{
	struct file *f = filealloc();
	if (f == 0)
		return 0;
	f->type = FD_STDIO;
	f->ref = 1;
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

	if (fd_is_reserved(f))
		return;
	if (f->ref < 1)
		panic("fileclose");
	if (--f->ref > 0) {
		return;
	}

	// Publish the slot as free before cleanup can reschedule. Cleanup uses only
	// the snapshots below, so a later filealloc() may safely reuse this slot.
	type = f->type;
	writable = f->writable;
	pipe = f->pipe;
	ip = f->ip;
	f->off = 0;
	f->readable = 0;
	f->writable = 0;
	f->pipe = 0;
	f->ip = 0;
	f->type = FD_NONE;
	f->ref = 0;

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
	if (f == 0 || fd_is_reserved(f) || f->ref < 1)
		return 0;
	f->ref++;
	return f;
}

//Add a new system-level table entry for the open file table
struct file *filealloc()
{
	for (int i = 0; i < FILEPOOLSIZE; ++i) {
		if (filepool[i].ref == 0) {
			struct file *f = &filepool[i];
			f->type = FD_NONE;
			f->ref = 1;
			f->readable = 0;
			f->writable = 0;
			f->pipe = 0;
			f->ip = 0;
			f->off = 0;
			return f;
		}
	}
	return 0;
}

//Show names of all files in the root_dir.
int show_all_files()
{
	struct inode *root = root_dir();
	int result;

	if (root == 0)
		return -1;
	result = dirls(root);
	iput(root);
	return result;
}

//A process creates or opens a file according to its path, returning the file descriptor of the created or opened file.
//If omode is O_CREATE, create a new file
//if omode if the others,open a created file.
int fileopen(char *path, uint64 omode)
{
	int fd = -1;
	struct file *f = 0;
	struct inode *ip = 0;
	uint owner = proc_storage_cookie(curr_proc());

	// Reserve the process-local descriptor before any operation that may
	// reschedule. The sentinel is neither usable nor inherited by fork().
	if ((f = filealloc()) == 0)
		return -1;
	if ((fd = fdreserve()) < 0) {
		fileclose(f);
		return -1;
	}
	if (omode & O_CREATE) {
		ip = fs_create(path, T_FILE, 0, owner);
		if (ip == 0)
			goto fail;
	} else {
		if ((ip = namei(path)) == 0)
			goto fail;
		ivalid(ip);
	}
	if (ip->type != T_FILE)
		goto fail;
	if ((omode & O_TRUNC) && ip->type == T_FILE)
		itrunc(ip);
	// only support FD_INODE
	f->type = FD_INODE;
	f->off = 0;
	f->ip = ip;
	f->readable = !(omode & O_WRONLY);
	f->writable = (omode & O_WRONLY) || (omode & O_RDWR);
	if (fdinstall(fd, f) < 0)
		goto fail;
	return fd;

fail:
	if (ip != 0 && f->ip == 0)
		iput(ip);
	fdrelease(fd);
	fileclose(f);
	return -1;
}

int fileunlink(char *path)
{
	struct inode *dp;
	struct inode *ip;
	uint owner = proc_storage_cookie(curr_proc());

	dp = root_dir();
	if (dp == 0)
		return -1;
	if ((ip = dirlookup(dp, path, 0)) == 0) {
		iput(dp);
		return -1;
	}
	ivalid(ip);
	if (ip->type != T_FILE || dirunlink(dp, path, 0, owner) < 0) {
		iput(ip);
		iput(dp);
		return -1;
	}
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
	uint chunk;
	uint off;
	int checkpoint;
	int r;

	if (len == 0)
		return 0;
	ivalid(f->ip);
	if (f->ip->type != T_FILE)
		return (uint64)-1;
	while (done < len) {
		off = f->off;
		remaining = len - done;
		chunk = BSIZE - off % BSIZE;
		if (remaining < chunk)
			chunk = (uint)remaining;
		if (checked_user_offset(va, done, 1, &user_addr) < 0)
			return done != 0 ? done : (uint64)-1;
		r = writei(f->ip, 1, user_addr, off, chunk,
			   proc_storage_cookie(curr_proc()));
		if (r < 0)
			return done != 0 ? done : (uint64)-1;
		if (r == 0)
			break;
		f->off = off + r;
		done += r;
		checkpoint = kernel_work_checkpoint((uint)r);
		// Once another thread has run, do not continue mutating a shared
		// open-file offset in this syscall. POSIX short writes let the caller
		// resume explicitly from the now-published offset.
		if (checkpoint != 0 || (uint)r < chunk)
			break;
	}
	return done;
}

//Read data from inode.
uint64 inoderead(struct file *f, uint64 va, uint64 len)
{
	uint64 done = 0;
	uint64 user_addr;
	uint64 remaining;
	uint chunk;
	uint off;
	int checkpoint;
	int r;

	if (len == 0)
		return 0;
	ivalid(f->ip);
	if (f->ip->type != T_FILE)
		return (uint64)-1;
	while (done < len) {
		off = f->off;
		remaining = len - done;
		chunk = BSIZE - off % BSIZE;
		if (remaining < chunk)
			chunk = (uint)remaining;
		if (checked_user_offset(va, done, 1, &user_addr) < 0)
			return done != 0 ? done : (uint64)-1;
		r = readi(f->ip, 1, user_addr, off, chunk);
		if (r < 0)
			return done != 0 ? done : (uint64)-1;
		if (r == 0)
			break;
		f->off = off + r;
		done += r;
		checkpoint = kernel_work_checkpoint((uint)r);
		if (checkpoint != 0 || (uint)r < chunk)
			break;
	}
	return done;
}
