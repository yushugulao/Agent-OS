#include "file.h"
#include "bio.h"
#include "defs.h"
#include "exec_policy.h"
#include "fcntl.h"
#include "fs.h"
#include "fs_epoch.h"
#include "kernel_work.h"
#include "agent_metadata_directory.h"
#include "open_file_io_lease.h"
#include "proc.h"
#include "vfs_security.h"

//This is a system-level open file table that holds open files of all process.
struct file filepool[FILEPOOLSIZE];

/* 一次 read/write 在有界文件系统批次间携带固定主体凭据；每批仍复核凭据，
 * 既能及时发现工作流撤销，又免去逐块构造与准入开销。 */
struct inode_io_transaction {
	struct file *file;
	struct inode *inode;
	struct vfs_cred cred;
	struct open_file_io_token lease;
	uint64 user_base;
	uint64 total;
	uint64 done;
};

#define FILEPOOL_INDEX_NONE ((uint16)0xffffU)

enum filepool_slot_state {
	FILEPOOL_SLOT_FREE = 0,
	FILEPOOL_SLOT_LIVE,
};

struct filepool_allocator_state {
	uint16 free_head;
	uint16 next[FILEPOOLSIZE];
	uchar slot_state[FILEPOOLSIZE];
	uchar offset_busy[FILEPOOLSIZE];
	uint16 offset_waiters[FILEPOOLSIZE];
	struct wait_queue offset_wait_queue;
	uint free_count;
	int initialized;
	volatile uint64 allocation_probes;
	volatile uint max_slot_pop_probes;
};

static struct filepool_allocator_state filepool_allocator;

_Static_assert(FILEPOOLSIZE < 0xffffU,
	       "filepool index must fit in the freelist link");
_Static_assert((uint64)NPROC *
	       (FD_BUFFER_SIZE + NTHREAD * (FD_BUFFER_SIZE + 1U)) < 0xffffU,
	       "all possible pinned file references must fit uint16");

static uint filepool_index_locked(struct file *f)
{
	uint64 base = (uint64)&filepool[0];
	uint64 address = (uint64)f;
	uint64 offset;

	if (address < base || address >= (uint64)&filepool[FILEPOOLSIZE])
		panic("filepool pointer");
	offset = address - base;
	if (offset % sizeof(struct file) != 0)
		panic("filepool alignment");
	return (uint)(offset / sizeof(struct file));
}

/* 仅串行化共享打开文件的偏移，不使用全局 VFS 门。 */
static int file_offset_lock(struct file *f)
{
	uint index;
	int queued = 0;
	int enabled = intr_save();

	if (!filepool_allocator.initialized) {
		intr_restore(enabled);
		return -1;
	}
	index = filepool_index_locked(f);
	for (;;) {
		if (filepool_allocator.slot_state[index] != FILEPOOL_SLOT_LIVE ||
		    f->ref < 1) {
			if (queued) {
				if (filepool_allocator.offset_waiters[index] == 0)
					panic("file offset waiter underflow");
				filepool_allocator.offset_waiters[index]--;
			}
			intr_restore(enabled);
			return -1;
		}
		if (!filepool_allocator.offset_busy[index]) {
			filepool_allocator.offset_busy[index] = 1;
			if (queued) {
				if (filepool_allocator.offset_waiters[index] == 0)
					panic("file offset waiter underflow");
				filepool_allocator.offset_waiters[index]--;
			}
			intr_restore(enabled);
			return 0;
		}
		if (!queued) {
			if (filepool_allocator.offset_waiters[index] ==
			    (uint16)0xffffU)
				panic("file offset waiter overflow");
			filepool_allocator.offset_waiters[index]++;
			queued = 1;
		}
		if (wait_queue_sleep_irq(&filepool_allocator.offset_wait_queue) !=
		    WAIT_QUEUE_OK) {
			if (filepool_allocator.offset_waiters[index] == 0)
				panic("file offset waiter underflow");
			filepool_allocator.offset_waiters[index]--;
			intr_restore(enabled);
			return -1;
		}
	}
}

static void file_offset_unlock(struct file *f)
{
	uint index;
	int enabled = intr_save();

	index = filepool_index_locked(f);
	if (filepool_allocator.slot_state[index] != FILEPOOL_SLOT_LIVE ||
	    f->ref < 1 || !filepool_allocator.offset_busy[index])
		panic("file offset unlock");
	filepool_allocator.offset_busy[index] = 0;
	/* 单队列承载紧凑的逐槽条件；无竞争快路不扫描调度器。 */
	if (filepool_allocator.offset_waiters[index] != 0)
		wait_queue_wake_all(&filepool_allocator.offset_wait_queue);
	intr_restore(enabled);
}

static void filepool_assert_locked(void)
{
#ifdef FILEPOOL_DEBUG
	uchar seen[(FILEPOOLSIZE + 7) / 8];
	uint16 index = filepool_allocator.free_head;
	uint count = 0;

	memset(seen, 0, sizeof(seen));
	while (index != FILEPOOL_INDEX_NONE) {
		uint byte;
		uchar bit;

		if (index >= FILEPOOLSIZE)
			panic("filepool free index");
		byte = index / 8;
		bit = (uchar)(1U << (index % 8));
		if (seen[byte] & bit)
			panic("filepool free cycle");
		seen[byte] |= bit;
		if (filepool_allocator.slot_state[index] !=
			    FILEPOOL_SLOT_FREE ||
		    filepool[index].ref != 0)
			panic("filepool free state");
		index = filepool_allocator.next[index];
		count++;
	}
	if (count != filepool_allocator.free_count)
		panic("filepool free count");
	for (uint i = 0; i < FILEPOOLSIZE; i++) {
		int listed = (seen[i / 8] & (1U << (i % 8))) != 0;

		if ((filepool_allocator.slot_state[i] ==
		     FILEPOOL_SLOT_FREE) != listed)
			panic("filepool membership");
		if (!listed &&
		    (filepool_allocator.slot_state[i] != FILEPOOL_SLOT_LIVE ||
		     filepool[i].ref < 1))
			panic("filepool live state");
	}
#endif
}

void filepool_init(void)
{
	int enabled = intr_save();

	if (filepool_allocator.initialized)
		panic("filepool reinit");
	for (uint i = 0; i < FILEPOOLSIZE; i++) {
		if (filepool[i].ref != 0)
			panic("filepool init live");
		filepool_allocator.next[i] =
			i + 1 < FILEPOOLSIZE ? (uint16)(i + 1) :
						 FILEPOOL_INDEX_NONE;
		filepool_allocator.slot_state[i] = FILEPOOL_SLOT_FREE;
		filepool_allocator.offset_busy[i] = 0;
		filepool_allocator.offset_waiters[i] = 0;
	}
	filepool_allocator.free_head = 0;
	filepool_allocator.free_count = FILEPOOLSIZE;
	filepool_allocator.allocation_probes = 0;
	filepool_allocator.max_slot_pop_probes = 0;
	wait_queue_init(&filepool_allocator.offset_wait_queue,
			WAIT_REASON_MUTEX);
	filepool_allocator.initialized = 1;
	filepool_assert_locked();
	intr_restore(enabled);
}

static uint filepool_pop_locked(void)
{
	uint16 index = filepool_allocator.free_head;

	if (index == FILEPOOL_INDEX_NONE || index >= FILEPOOLSIZE ||
	    filepool_allocator.free_count == 0)
		panic("filepool empty");
	if (filepool_allocator.slot_state[index] != FILEPOOL_SLOT_FREE ||
	    filepool[index].ref != 0)
		panic("filepool corrupt pop");
	if (filepool_allocator.offset_busy[index] != 0 ||
	    filepool_allocator.offset_waiters[index] != 0)
		panic("filepool offset state on pop");
	filepool_allocator.free_head = filepool_allocator.next[index];
	filepool_allocator.next[index] = FILEPOOL_INDEX_NONE;
	filepool_allocator.slot_state[index] = FILEPOOL_SLOT_LIVE;
	filepool_allocator.free_count--;
	filepool_allocator.allocation_probes++;
	if (filepool_allocator.max_slot_pop_probes < 1)
		filepool_allocator.max_slot_pop_probes = 1;
	return index;
}

static void filepool_push_locked(uint index)
{
	if (index >= FILEPOOLSIZE ||
	    filepool_allocator.slot_state[index] != FILEPOOL_SLOT_LIVE ||
	    filepool[index].ref != 0 ||
	    filepool_allocator.offset_busy[index] != 0 ||
	    filepool_allocator.offset_waiters[index] != 0 ||
	    filepool_allocator.free_count >= FILEPOOLSIZE)
		panic("filepool corrupt push");
	filepool_allocator.slot_state[index] = FILEPOOL_SLOT_FREE;
	filepool_allocator.next[index] = filepool_allocator.free_head;
	filepool_allocator.free_head = (uint16)index;
	filepool_allocator.free_count++;
}

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

static int fileclose_cleanup_owner_valid(uint owner)
{
	if (owner == FS_OWNER_SYSTEM || owner == FS_OWNER_PUBLIC)
		return 1;
	if (FS_OWNER_IS_SCOPE(owner) &&
	    FS_OWNER_SCOPE_ID(owner) >= VFS_SCOPE_FIRST_DYNAMIC &&
	    FS_OWNER_SCOPE_ID(owner) <= FS_OWNER_MAX_PERSISTENT_ID)
		return 1;
	return 0;
}

static int fileclose_cleanup_token_empty(
	const struct bio_cleanup_token *token)
{
	return token->slot == 0 && token->generation == 0;
}

static int fileclose_cleanup_token_prepare(
	uint owner, struct bio_cleanup_token *token)
{
	if (!fileclose_cleanup_token_empty(token))
		return 0;
	if (!fileclose_cleanup_owner_valid(owner))
		panic("fileclose cleanup owner");
	return bio_cleanup_token_prepare(owner, token);
}

/* 释放文件引用并原子摘除最后一个文件表槽；inode 引用保留在收据中统一结算，
 * 仅已确认的破坏性关闭会在发布前保留清理所有权。 */
int fileclose_prepare(struct file *f, struct file_close_receipt *receipt)
{
	uint index;
	int enabled;

	if (receipt == 0 ||
	    receipt->state != FILE_CLOSE_RECEIPT_EMPTY)
		panic("fileclose receipt prepare");
	if (fd_is_reserved(f))
		return 0;
	enabled = intr_save();
	if (!filepool_allocator.initialized)
		panic("filepool uninitialized");
	index = filepool_index_locked(f);
	if (filepool_allocator.slot_state[index] != FILEPOOL_SLOT_LIVE ||
	    f->ref < 1)
		panic("fileclose");
	if (f->ref > 1) {
		f->ref--;
		intr_restore(enabled);
		return 0;
	}
	/* 普通减引用与末引用析构分离；仅继承析构的所有者初始化冷收据状态。 */
	receipt->type = FD_NONE;
	receipt->writable = 0;
	receipt->pipe = 0;
	receipt->ip = 0;
	receipt->resource_account = resource_account_none();
	receipt->resource_reserved = 0;
	receipt->cleanup_owner = FS_OWNER_NONE;
	receipt->result = 0;
	receipt->cleanup_token =
		(struct bio_cleanup_token)BIO_CLEANUP_TOKEN_INIT;
	if (f->type == FD_INODE && f->ip->ref == 1 &&
	    f->ip->valid && f->ip->removed &&
	    fileclose_cleanup_token_prepare(
		    f->cleanup_owner, &receipt->cleanup_token) < 0) {
		intr_restore(enabled);
		return -1;
	}
	f->ref = 0;

	// 清理可能跨越安全点，因此先发布空闲槽。
	receipt->type = f->type;
	receipt->writable = f->writable;
	receipt->pipe = f->pipe;
	receipt->ip = f->ip;
	receipt->resource_account = f->resource_account;
	receipt->resource_reserved = f->resource_reserved;
	receipt->cleanup_owner = f->cleanup_owner;
	receipt->state = FILE_CLOSE_RECEIPT_PREPARED;
	f->off = 0;
	f->readable = 0;
	f->writable = 0;
	f->pipe = 0;
	f->ref = 0;
	f->type = FD_NONE;
	f->inherit_class = FD_INHERIT_DENY;
	f->resource_account = resource_account_none();
	f->resource_reserved = 0;
	f->cleanup_owner = FS_OWNER_NONE;
	filepool_push_locked(index);
	filepool_assert_locked();
	intr_restore(enabled);
	return 1;
}

int fileclose_receipt_is_inode(const struct file_close_receipt *receipt)
{
	return receipt != 0 &&
	       receipt->state == FILE_CLOSE_RECEIPT_PREPARED &&
	       receipt->type == FD_INODE;
}

static void fileclose_receipt_complete(struct file_close_receipt *receipt)
{
	receipt->state = FILE_CLOSE_RECEIPT_CONSUMED;
	proc_file_slot_release(receipt->resource_account,
			       receipt->resource_reserved);
}

static void fileclose_finish_direct(struct file_close_receipt *receipt)
{
	if (receipt == 0 ||
	    receipt->state != FILE_CLOSE_RECEIPT_PREPARED ||
	    receipt->type == FD_INODE)
		panic("fileclose direct receipt");
	/* pipe 析构可能 yield，须先发布一次性消费。 */
	receipt->state = FILE_CLOSE_RECEIPT_FINALIZING;

	switch (receipt->type) {
	case FD_NONE:
		// 保留文件槽可能在初始化前释放。
		break;
	case FD_STDIO:
		// Do nothing
		break;
	case FD_PIPE:
		pipeclose(receipt->pipe, receipt->writable);
		break;
	default:
		panic("unknown file type %d\n", receipt->type);
	}
	fileclose_receipt_complete(receipt);
}

int fileclose_finish_drop_only(struct file_close_receipt *receipt)
{
	int dropped;

	if (!fileclose_receipt_is_inode(receipt))
		return -1;
	receipt->state = FILE_CLOSE_RECEIPT_FINALIZING;
	dropped = iput_drop_only(receipt->ip);
	if (dropped <= 0) {
		receipt->state = FILE_CLOSE_RECEIPT_PREPARED;
		return dropped;
	}
	bio_cache_retry_notify();
	if (!fileclose_cleanup_token_empty(&receipt->cleanup_token) &&
	    bio_cleanup_token_release(&receipt->cleanup_token, 1) !=
		    BIO_CLEANUP_RELEASED)
		panic("fileclose drop token release");
	fileclose_receipt_complete(receipt);
	return 1;
}

int fileclose_finish_epoch(struct file_close_receipt *receipt)
{
	uint sponsor_class;
	uint sponsor_owner;
	int release_result;

	if (!fileclose_receipt_is_inode(receipt) ||
	    !fs_epoch_request_held())
		return -1;
	if (fileclose_cleanup_token_prepare(
		    receipt->cleanup_owner, &receipt->cleanup_token) < 0)
		return -1;
	/* 兼容的异步清理共享 epoch；前台或外来工作须在令牌生效前提交。 */
	if (bio_cleanup_token_sponsor(
		    &receipt->cleanup_token, &sponsor_owner, &sponsor_class) < 0 ||
	    fs_epoch_prepare_cleanup_sponsor(
		    sponsor_owner, sponsor_class) < 0)
		return -1;
	if (bio_cleanup_token_begin(&receipt->cleanup_token) < 0)
		return -1;
	receipt->state = FILE_CLOSE_RECEIPT_FINALIZING;
	iput(receipt->ip);
	if (fs_epoch_should_commit() && fs_epoch_commit() < 0)
		receipt->result = -1;
	if (bio_cleanup_token_end(&receipt->cleanup_token) < 0)
		panic("fileclose cleanup token end");
	receipt->state = FILE_CLOSE_RECEIPT_SETTLEMENT;
	release_result = bio_cleanup_token_release(
		&receipt->cleanup_token, 1);
	if (release_result == BIO_CLEANUP_RELEASED)
		fileclose_receipt_complete(receipt);
	return release_result < 0 ? BIO_CLEANUP_NEEDS_SETTLEMENT :
				    release_result;
}

int fileclose_finish_settle(struct file_close_receipt *receipt)
{
	int result;

	if (receipt == 0 ||
	    receipt->state != FILE_CLOSE_RECEIPT_SETTLEMENT ||
	    fs_epoch_request_held())
		return -1;
	result = bio_cleanup_token_release(&receipt->cleanup_token, 1);
	if (result == BIO_CLEANUP_RELEASED)
		fileclose_receipt_complete(receipt);
	return result;
}

int fileclose_finish_result(const struct file_close_receipt *receipt)
{
	if (receipt == 0 || receipt->state != FILE_CLOSE_RECEIPT_CONSUMED)
		return -1;
	return receipt->result;
}

static void fileclose_batch_transfer(
	struct file_close_batch *batch, struct file_close_receipt *receipt)
{
	if (batch == 0 || receipt == 0 ||
	    receipt->state != FILE_CLOSE_RECEIPT_SETTLEMENT ||
	    fileclose_cleanup_token_empty(&receipt->cleanup_token) ||
	    batch->count >= FILE_CLOSE_BATCH_CAP)
		panic("fileclose batch transfer");
	batch->pending[batch->count++] = receipt->cleanup_token;
	receipt->cleanup_token =
		(struct bio_cleanup_token)BIO_CLEANUP_TOKEN_INIT;
	fileclose_receipt_complete(receipt);
}

int fileclose_batch_add(struct file_close_batch *batch, struct file *f)
{
	struct file_close_receipt receipt = FILE_CLOSE_RECEIPT_INIT;
	int prepared;
	int result;

	if (batch == 0 || f == 0 ||
	    batch->count > FILE_CLOSE_BATCH_CAP)
		return -1;
	if (batch->count != 0)
		return 1;
	prepared = fileclose_prepare(f, &receipt);
	if (prepared < 0)
		return 1;
	if (prepared == 0)
		return 0;
	if (receipt.type != FD_INODE) {
		fileclose_finish_direct(&receipt);
		return 0;
	}
	result = fileclose_finish_drop_only(&receipt);
	if (result < 0)
		panic("fileclose batch drop-only");
	if (result > 0)
		return 0;
	if (!fs_epoch_request_held())
		panic("fileclose batch without epoch");
	while (fileclose_finish_epoch(&receipt) < 0 &&
	       receipt.state == FILE_CLOSE_RECEIPT_PREPARED)
		(void)kernel_work_checkpoint_cleanup(
			KERNEL_WORK_OPERATION_UNITS);
	if (receipt.state == FILE_CLOSE_RECEIPT_SETTLEMENT)
		fileclose_batch_transfer(batch, &receipt);
	if (receipt.state != FILE_CLOSE_RECEIPT_CONSUMED)
		panic("fileclose batch incomplete");
	return 0;
}

int fileclose_batch_settle(struct file_close_batch *batch)
{
	if (batch == 0 || batch->count > FILE_CLOSE_BATCH_CAP ||
	    fs_epoch_request_held())
		return -1;
	while (batch->count != 0) {
		struct bio_cleanup_token *token =
			&batch->pending[batch->count - 1];

		if (bio_cleanup_token_release(token, 1) ==
		    BIO_CLEANUP_RELEASED) {
			batch->count--;
			continue;
		}
		(void)kernel_work_checkpoint_cleanup(
			KERNEL_WORK_OPERATION_UNITS);
	}
	return 0;
}

void fileclose_finish(struct file_close_receipt *receipt)
{
	int borrowed_epoch;
	int result;

	if (receipt == 0 ||
	    receipt->state != FILE_CLOSE_RECEIPT_PREPARED)
		panic("fileclose receipt finish");
	if (receipt->type != FD_INODE) {
		fileclose_finish_direct(receipt);
		return;
	}
	result = fileclose_finish_drop_only(receipt);
	if (result > 0)
		return;
	if (result < 0)
		panic("fileclose drop-only finalizer");
	borrowed_epoch = fs_epoch_request_held();
	if (!borrowed_epoch && fs_epoch_request_begin() < 0)
		panic("fileclose filesystem epoch");
	for (;;) {
		result = fileclose_finish_epoch(receipt);
		if (result >= 0 ||
		    receipt->state != FILE_CLOSE_RECEIPT_PREPARED)
			break;
		(void)kernel_work_checkpoint_cleanup(
			KERNEL_WORK_OPERATION_UNITS);
	}
	if (!borrowed_epoch)
		fs_epoch_request_end();
	if (receipt->state == FILE_CLOSE_RECEIPT_SETTLEMENT) {
		if (borrowed_epoch)
			panic("fileclose settlement inside borrowed epoch");
		while (fileclose_finish_settle(receipt) !=
		       BIO_CLEANUP_RELEASED)
			(void)kernel_work_checkpoint_cleanup(
				KERNEL_WORK_OPERATION_UNITS);
	}
	if (receipt->state != FILE_CLOSE_RECEIPT_CONSUMED)
		panic("fileclose receipt incomplete");
}

// 释放一个文件引用，必要时同步析构。
void fileclose(struct file *f)
{
	struct file_close_receipt receipt = FILE_CLOSE_RECEIPT_INIT;

	int prepared;

	do {
		prepared = fileclose_prepare(f, &receipt);
		if (prepared < 0)
			(void)kernel_work_checkpoint_cleanup(
				KERNEL_WORK_OPERATION_UNITS);
	} while (prepared < 0);
	if (prepared > 0)
		fileclose_finish(&receipt);
}

// 在可能让出处理器的操作之间固定打开文件项。
struct file *filedup(struct file *f)
{
	int enabled = intr_save();

	if (f == 0 || fd_is_reserved(f) || f->ref < 1) {
		intr_restore(enabled);
		return 0;
	}
	if (f->ref == (uint16)0xffffU)
		panic("file reference overflow");
	f->ref++;
	intr_restore(enabled);
	return f;
}

int filealloc_many(struct proc *owner, struct file **files, uint count)
{
	int enabled = intr_save();
	struct resource_account_handle account;
	int reserved;

	if (owner == 0 || files == 0 || count == 0 ||
	    count > FILEPOOLSIZE)
		goto fail;
	if (!filepool_allocator.initialized)
		panic("filepool uninitialized");
	for (uint i = 0; i < count; i++)
		files[i] = 0;
	if (filepool_allocator.free_count < count ||
	    proc_file_slots_reserve(owner, count, &account, &reserved) < 0)
		goto fail;
	for (uint i = 0; i < count; i++) {
		uint index = filepool_pop_locked();
		struct file *f = &filepool[index];

		files[i] = f;
		f->type = FD_NONE;
		f->inherit_class = FD_INHERIT_DENY;
		f->ref = 1;
		f->readable = 0;
		f->writable = 0;
		f->pipe = 0;
		f->off = 0;
		f->resource_account = account;
		f->resource_reserved = reserved;
		f->cleanup_owner = bio_process_owner(owner);
	}
	filepool_assert_locked();
	intr_restore(enabled);
	return 0;
fail:
	if (files != 0 && count <= FILEPOOLSIZE)
		for (uint i = 0; i < count; i++)
			files[i] = 0;
	intr_restore(enabled);
	return -1;
}

struct file *filealloc(struct proc *owner)
{
	struct file *file = 0;

	return filealloc_many(owner, &file, 1) == 0 ? file : 0;
}

static int filetruncate(struct inode *ip, const struct vfs_cred *cred)
{
	struct inode_reclaim reclaim;

	if (!agent_edit_truncate_allowed(ip) ||
	    itruncate_detach(ip, cred, 0, &reclaim) < 0)
		return -1;
	// 回收或持久化可能 yield，须先提交两个版本域。
	agent_edit_note_truncate(ip);
	agent_fs_note_truncate(ip);
	return itruncate_reclaim(&reclaim);
}

static int inode_io_transaction_begin(struct inode_io_transaction *transaction,
				      struct file *file, uint64 user_base,
				      uint64 total, enum vfs_operation operation)
{
	if (transaction == 0 || file == 0 || file->ip == 0)
		return -1;
	memset(transaction, 0, sizeof(*transaction));
	transaction->file = file;
	transaction->inode = file->ip;
	transaction->user_base = user_base;
	transaction->total = total;
	if (open_file_io_lease_acquire(file, operation,
				       &transaction->lease,
				       &transaction->cred) < 0)
		return -1;
	return 0;
}

static uint inode_io_transaction_batch(
	const struct inode_io_transaction *transaction)
{
	uint64 remaining = transaction->total - transaction->done;
	uint alignment = transaction->file->off % BSIZE;
	uint limit = KERNEL_WORK_IO_BATCH_BYTES - alignment;

	return (uint)MIN(remaining, limit);
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
	int lookup_status = FS_LOOKUP_ERROR;

	vfs_cred_from_proc(curr_proc(), &cred);
	policy = vfs_default_create_policy(&cred);
	if ((omode & O_CREATE) && policy == 0)
		return -1;
	if ((omode & O_CREATE) &&
	    !vfs_create_request_authorize(
		    &cred, policy, !(omode & O_WRONLY),
		    (omode & (O_WRONLY | O_RDWR)) != 0,
		    (omode & O_TRUNC) != 0)) {
		return -1;
	}
	if ((f = filealloc(curr_proc())) == 0)
		return -1;
	if ((fd = fdreserve()) < 0) {
		fileclose(f);
		return -1;
	}

	if (omode & O_CREATE) {
		ip = fs_create(path, T_FILE, &created, &cred, policy,
			       &lookup_status);
		if (ip == 0)
			goto fail;
	} else {
		ip = namei_scope_status(path, vfs_cred_lookup_policy(&cred),
				       cred.scope_id, &lookup_status);
		if (ip == 0) {
			if (lookup_status != FS_LOOKUP_ABSENT)
				goto fail;
			if ((omode & (O_WRONLY | O_RDWR | O_TRUNC)) != 0 ||
			    cred.scope_id < VFS_SCOPE_FIRST_DYNAMIC)
				goto fail;
			ip = namei_scope_status(path, VFS_POLICY_WORKFLOW,
					       VFS_SCOPE_SYSTEM, &lookup_status);
			if (ip == 0 || lookup_status != FS_LOOKUP_FOUND)
				goto fail;
		}
		if (ivalid(ip) < 0)
			goto fail;
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
	if (!created && f->readable)
		open_file_io_lease_seed_authorized(f, VFS_OP_READ, &cred);
	if (!created && f->writable)
		open_file_io_lease_seed_authorized(f, VFS_OP_WRITE, &cred);
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
	int lookup_status;
	int root_status;

	vfs_cred_from_proc(curr_proc(), &cred);
	policy = vfs_cred_lookup_policy(&cred);

	dp = root_dir_status(&root_status);
	if (dp == 0 || root_status != FS_LOOKUP_FOUND) {
		if (dp)
			iput(dp);
		return -1;
	}
	if (ivalid(dp) < 0) {
		iput(dp);
		return -1;
	}
	if ((ip = dirlookup(dp, path, &offset, policy, cred.scope_id,
			    &lookup_status)) == 0) {
		iput(dp);
		return -1;
	}
	if (ivalid(ip) < 0) {
		iput(ip);
		iput(dp);
		return -1;
	}
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
	struct inode_io_transaction transaction;
	uint64 user_addr;
	uint offset;
	uint chunk;
	int checkpoint;
	int r;
	uint64 result = 0;

	if (len == 0)
		return 0;
	if (file_offset_lock(f) < 0)
		return (uint64)-1;
	if (inode_io_transaction_begin(&transaction, f, va, len,
				       VFS_OP_WRITE) < 0) {
		result = (uint64)-1;
		goto out;
	}

	while (transaction.done < transaction.total) {
		offset = f->off;
		chunk = inode_io_transaction_batch(&transaction);
		if (checked_user_offset(transaction.user_base,
					transaction.done, chunk, &user_addr) < 0) {
			result = transaction.done == 0 ? (uint64)-1 :
				 transaction.done;
			break;
		}
		r = writei_lease(transaction.inode, &transaction.cred,
				 &transaction.lease, 1, user_addr, offset, chunk);
		if (r <= 0) {
			result = transaction.done == 0 ? (uint64)r :
				 transaction.done;
			break;
		}

		f->off = offset + r;
		transaction.done += r;
		if (bio_checkpoint_should_stop(bio_request_checkpoint())) {
			result = transaction.done;
			break;
		}
		checkpoint = kernel_work_checkpoint_bytes((uint)r);
		if (checkpoint != 0 || (uint)r < chunk) {
			result = transaction.done;
			break;
		}
	}
	if (transaction.done != 0) {
		agent_edit_note_write(transaction.inode);
		agent_fs_note_write(transaction.inode);
	}
	result = result == 0 ? transaction.done : result;
out:
	open_file_io_token_end(&transaction.lease);
	file_offset_unlock(f);
	return result;
}

//Read data from inode.
uint64 inoderead(struct file *f, uint64 va, uint64 len)
{
	struct inode_io_transaction transaction;
	uint64 user_addr;
	uint offset;
	uint chunk;
	int checkpoint;
	int r;
	uint64 result;

	if (len == 0)
		return 0;
	if (file_offset_lock(f) < 0)
		return (uint64)-1;
	if (inode_io_transaction_begin(&transaction, f, va, len,
				       VFS_OP_READ) < 0) {
		result = (uint64)-1;
		goto out;
	}

	while (transaction.done < transaction.total) {
		offset = f->off;
		chunk = inode_io_transaction_batch(&transaction);
		if (checked_user_offset(transaction.user_base,
					transaction.done, chunk, &user_addr) < 0) {
			result = transaction.done == 0 ? (uint64)-1 :
				 transaction.done;
			goto out;
		}
		r = readi_lease(transaction.inode, &transaction.cred,
				&transaction.lease, 1, user_addr, offset, chunk);
		if (r <= 0) {
			result = transaction.done == 0 ? (uint64)r :
				 transaction.done;
			goto out;
		}

		f->off = offset + r;
		transaction.done += r;
		if (bio_checkpoint_should_stop(bio_request_checkpoint())) {
			result = transaction.done;
			goto out;
		}
		checkpoint = kernel_work_checkpoint_bytes((uint)r);
		if (checkpoint != 0 || (uint)r < chunk) {
			result = transaction.done;
			goto out;
		}
	}
	result = transaction.done;
out:
	open_file_io_token_end(&transaction.lease);
	file_offset_unlock(f);
	return result;
}
