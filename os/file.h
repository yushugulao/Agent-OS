#ifndef FILE_H
#define FILE_H

#include "bio.h"
#include "fs.h"
#include "proc.h"
#include "types.h"

#define PIPESIZE (512)
#define FILEPOOLSIZE FILE_RESOURCE_POOL_SIZE

// in-memory copy of an inode,it can be used to quickly locate file entities on disk
struct inode {
	uint dev; // Device number
	uint inum; // Inode number
	int ref; // Reference count
	int valid; // inode has been read from disk?
	int removed; // directory entry is gone; reclaim after the last reference
	short type; // copy of disk inode
	short agent_meta_slot;
	short agent_meta_flags;
	short agent_meta_version;
	uint size;
	uint addrs[NDIRECT + 1];
	uint exec_flags;
	uint exec_generation;
	uint exec_role_mask;
	uint exec_layout_version;
	uint exec_rw_offset;
	uint vfs_magic;
	uint vfs_version;
	uint vfs_flags;
	uint vfs_scope_id;
	uint vfs_policy;
	uint vfs_exec_profile;
	uint vfs_policy_generation;
	uint vfs_incarnation;
	uint fs_owner_domain;
	uint fs_owner_version;
	uint vfs_checksum;
	// LAB4: You may need to add link count here
};

//a struct for pipe
struct pipe {
	char data[PIPESIZE];
	uint nread; // number of bytes read
	uint nwrite; // number of bytes written
	int readopen; // read fd is still open
	int writeopen; // write fd is still open
	struct resource_account_handle page_account;
	enum resource_charge_class page_charge_class;
	struct wait_queue read_waiters;
	struct wait_queue write_waiters;
};

// Descriptor inheritance is deny-by-default at a security-principal boundary.
// Reauthorizing objects are checked against the child credential on every use;
// held capabilities require an explicit one-shot delegation ticket.
enum fd_inherit_class {
	FD_INHERIT_DENY = 0,
	FD_INHERIT_STDIO,
	FD_INHERIT_REAUTHORIZE,
	FD_INHERIT_DELEGATE,
};

// file.h
// Defines a file in memory that provides information about the current use of the file and the corresponding inode location
struct file {
	enum { FD_NONE = 0, FD_PIPE, FD_INODE, FD_STDIO } type;
	enum fd_inherit_class inherit_class;
	int ref; // reference count
	char readable;
	char writable;
	struct pipe *pipe; // FD_PIPE
	struct inode *ip; // FD_INODE
	uint off;
	// This charge follows the unique object until its final reference closes.
	struct resource_account_handle resource_account;
	int resource_reserved;
	uint cleanup_owner;
};

/*
 * The final reference is unpublished with interrupts disabled, but its
 * destructor may sleep.  A prepared receipt owns every field needed to finish
 * that destructor after the caller has entered the appropriate slow path.
 */
enum file_close_receipt_state {
	FILE_CLOSE_RECEIPT_EMPTY = 0,
	FILE_CLOSE_RECEIPT_PREPARED,
	FILE_CLOSE_RECEIPT_FINALIZING,
	FILE_CLOSE_RECEIPT_SETTLEMENT,
	FILE_CLOSE_RECEIPT_CONSUMED,
};

struct file_close_receipt {
	enum file_close_receipt_state state;
	int type;
	int writable;
	struct pipe *pipe;
	struct inode *ip;
	struct resource_account_handle resource_account;
	int resource_reserved;
	uint cleanup_owner;
	int result;
	struct bio_cleanup_token cleanup_token;
};

#define FILE_CLOSE_RECEIPT_INIT { 0 }

/*
 * Teardown transfers at most one cleanup lease across the filesystem gate.
 * Settling each destructive close incrementally bounds stack use and prevents
 * a teardown from hoarding global cleanup admission.
 */
#define FILE_CLOSE_BATCH_CAP 1U

struct file_close_batch {
	struct bio_cleanup_token pending[FILE_CLOSE_BATCH_CAP];
	uint count;
};

#define FILE_CLOSE_BATCH_INIT { 0 }

//A few specific fd
enum {
	STDIN = 0,
	STDOUT = 1,
	STDERR = 2,
};

extern struct file filepool[FILEPOOLSIZE];

void filepool_init(void);
int pipealloc(struct file *, struct file *);
void pipeclose(struct pipe *, int);
int piperead(struct pipe *, uint64, uint64);
int pipewrite(struct pipe *, uint64, uint64);
int fileclose_prepare(struct file *, struct file_close_receipt *);
int fileclose_receipt_is_inode(const struct file_close_receipt *);
int fileclose_finish_drop_only(struct file_close_receipt *);
int fileclose_finish_epoch(struct file_close_receipt *);
int fileclose_finish_settle(struct file_close_receipt *);
int fileclose_finish_result(const struct file_close_receipt *);
void fileclose_finish(struct file_close_receipt *);
int fileclose_batch_add(struct file_close_batch *, struct file *);
int fileclose_batch_settle(struct file_close_batch *);
void fileclose(struct file *);
struct file *filedup(struct file *);
struct file *filealloc(struct proc *);
int filealloc_many(struct proc *, struct file **, uint);
int fileopen(char *, uint64);
int fileunlink(char *);
uint64 inodewrite(struct file *, uint64, uint64);
uint64 inoderead(struct file *, uint64, uint64);
struct file *stdio_init(int, struct proc *);

#endif // FILE_H
