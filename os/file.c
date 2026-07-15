#include "file.h"
#include "defs.h"
#include "fcntl.h"
#include "fs.h"
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
	if (f->ref < 1)
		panic("fileclose");
	if (--f->ref > 0) {
		return;
	}
	switch (f->type) {
	case FD_NONE:
		// A reserved file slot may be released before it is initialized.
		break;
	case FD_STDIO:
		// Do nothing
		break;
	case FD_PIPE:
		pipeclose(f->pipe, f->writable);
		break;
	case FD_INODE:
		iput(f->ip);
		break;
	default:
		panic("unknown file type %d\n", f->type);
	}

	f->off = 0;
	f->readable = 0;
	f->writable = 0;
	f->pipe = 0;
	f->ip = 0;
	f->ref = 0;
	f->type = FD_NONE;
}

// Pin an open-file entry across operations that may yield.
struct file *filedup(struct file *f)
{
	if (f == 0 || f->ref < 1)
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
	int fd;
	struct file *f;
	struct inode *ip;
	int created = 0;

	if (agent_file_is_meta_store_name(path))
		return -1;

	if (omode & O_CREATE) {
		ip = fs_create(path, T_FILE, &created);
		if (ip == 0) {
			return -1;
		}
		if (created)
			agent_fs_note_create(ip, path);
	} else {
		if ((ip = namei(path)) == 0) {
			return -1;
		}
		ivalid(ip);
	}
	if (ip->type != T_FILE) {
		iput(ip);
		return -1;
	}
	if ((omode & O_TRUNC) && ip->type == T_FILE &&
	    !agent_edit_truncate_allowed(ip)) {
		iput(ip);
		return -1;
	}
	if ((f = filealloc()) == 0 || (fd = fdalloc(f)) < 0) {
		//Assign a system-level table entry to a newly created or opened file
		//and then create a file descriptor that points to it
		if (f)
			fileclose(f);
		iput(ip);
		return -1;
	}
	// only support FD_INODE
	f->type = FD_INODE;
	f->off = 0;
	f->ip = ip;
	f->readable = !(omode & O_WRONLY);
	f->writable = (omode & O_WRONLY) || (omode & O_RDWR);
	if ((omode & O_TRUNC) && ip->type == T_FILE) {
		itrunc(ip);
		agent_fs_note_truncate(ip);
		agent_edit_note_truncate(ip);
	}
	return fd;
}

int fileunlink(char *path)
{
	struct inode *dp;
	struct inode *ip;
	uint inum = 0;

	if (agent_file_is_meta_store_name(path))
		return -1;

	dp = root_dir();
	if (dp == 0)
		return -1;
	ivalid(dp);
	if ((ip = dirlookup(dp, path, 0)) == 0) {
		iput(dp);
		return -1;
	}
	ivalid(ip);
	if (ip->type != T_FILE) {
		iput(ip);
		iput(dp);
		return -1;
	}
	if (!agent_edit_unlink_allowed(ip)) {
		iput(ip);
		iput(dp);
		return -1;
	}
	if (dirunlink(dp, path, &inum) < 0) {
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
	int r;
	ivalid(f->ip);
	if (f->ip->type != T_FILE)
		return (uint64)-1;
	if (!agent_edit_write_allowed(f->ip))
		return (uint64)-1;
	if ((r = writei(f->ip, 1, va, f->off, len)) > 0) {
		f->off += r;
		agent_fs_note_write(f->ip);
		agent_edit_note_write(f->ip);
	}
	return r;
}

//Read data from inode.
uint64 inoderead(struct file *f, uint64 va, uint64 len)
{
	int r;
	ivalid(f->ip);
	if (f->ip->type != T_FILE)
		return (uint64)-1;
	if ((r = readi(f->ip, 1, va, f->off, len)) > 0)
		f->off += r;
	return r;
}
