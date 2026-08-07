#ifndef FILE_H
#define FILE_H

#include "bio.h"
#include "fs.h"
#include "proc.h"
#include "types.h"

#define PIPESIZE (512)
#define FILEPOOLSIZE FILE_RESOURCE_POOL_SIZE

// 索引节点的内存副本，用于快速定位磁盘文件实体。
struct inode {
	uint dev; // Device number
	uint inum; // Inode number
	int ref; // Reference count
	int valid; // inode has been read from disk?
	int removed; // 目录项已移除；最后一个引用释放后回收
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

// 描述符跨安全主体时默认禁止继承。需重新授权的对象每次使用都校验子进程
// 凭据；持有型能力必须提供明确的一次性委派票据。
enum fd_inherit_class {
	FD_INHERIT_DENY = 0,
	FD_INHERIT_STDIO,
	FD_INHERIT_REAUTHORIZE,
	FD_INHERIT_DELEGATE,
};

enum file_type {
	FD_NONE = 0,
	FD_PIPE,
	FD_INODE,
	FD_STDIO,
};

// file.h
// 文件的内存表示，记录当前使用状态和对应索引节点位置。
struct file {
	union {
		struct pipe *pipe; // FD_PIPE
		struct inode *ip; // FD_INODE
	};
	// 该计费随唯一对象保持，直到最后一个引用关闭。
	struct resource_account_handle resource_account;
	uint off;
	uint cleanup_owner;
	uint16 ref; // reference count
	uchar type;
	uchar inherit_class;
	uchar readable;
	uchar writable;
	uchar resource_reserved;
};

_Static_assert(FD_STDIO <= 0xff && FD_INHERIT_DELEGATE <= 0xff,
	       "file tags must fit compact fields");
_Static_assert(sizeof(struct file) == 40,
	       "open-file entries must remain cache compact");

/*
 * 最后一个引用在关中断区内撤销发布，但析构过程可以休眠。预备收据持有
 * 所需全部字段，供调用方进入对应慢路径后完成析构。
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
 * 拆除过程跨文件系统门锁最多传递一个清理租约。逐次结算破坏性关闭可限制
 * 栈用量，并防止单次拆除长期占用全局清理准入。
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
