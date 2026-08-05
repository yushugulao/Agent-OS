// File system implementation.  Five layers:
//   + Blocks: allocator for raw disk blocks.
//   + Log: crash recovery for multi-step updates.
//   + Files: inode allocator, reading, writing, metadata.
//   + Directories: inode with special contents (list of other inodes!)
//   + Names: paths like /usr/rtm/kernel/fs.c for convenient naming.
//
// This file contains the low-level file system manipulation
// routines.  The (higher-level) system call implementations
// are in sysfile.c.

#include "fs.h"
#include "agent.h"
#include "bio.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "fs_epoch.h"
#include "kernel_work.h"
#include "open_file_io_lease.h"
#include "performance_stats.h"
#include "proc.h"
#include "riscv.h"
#include "types.h"
#include "virtio.h"
#include "../fs_allocator_test_abi.h"
#include "../physical_page_policy.h"
#include "vfs_security.h"
#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
#include "fs_allocator_test.h"
#endif
// there should be one superblock per disk device, but we run with
// only one device
struct superblock sb;

struct fs_storage_state {
	uint free_blocks;
	uint free_inodes;
	uint block_alloc_cursor;
	uint inode_alloc_cursor;
	uint public_principal_id;
	uint block_domain_limit;
	uint inode_domain_limit;
	uint workflow_block_domain_limit;
	uint workflow_inode_domain_limit;
	uint workflow_block_reserve;
	uint workflow_block_guarantee;
	uint system_block_reserve;
	uint system_block_reserve_remaining;
	uint workflow_inode_reserve;
	uint workflow_inode_guarantee;
	uint system_inode_reserve;
	uint system_inode_reserve_remaining;
	int ready;
};

static struct fs_storage_state fs_storage;
static struct resource_account_handle fs_system_account;
static struct resource_account_handle fs_public_account;
enum fs_io_health {
	FS_IO_HEALTHY = 0,
	FS_IO_UNAVAILABLE,
	FS_IO_INDETERMINATE,
};

enum fs_failure_class {
	FS_FAILURE_OPERATION = 0,
	FS_FAILURE_TRANSIENT_READ,
	FS_FAILURE_SCHEDULING_UNAVAILABLE,
	FS_FAILURE_MOUNT_UNAVAILABLE,
	FS_FAILURE_METADATA_WRITE_INDETERMINATE,
};

static enum fs_io_health fs_io_health;

#define FS_READ_BATCH_MAX 4U
_Static_assert(FS_READ_BATCH_MAX <= VIRTIO_DISK_READ_BATCH_MAX,
	       "filesystem read batch must fit the device queue");
_Static_assert(FS_READ_BATCH_MAX <= VM_COPY_SEGMENT_MAX &&
	       FS_READ_BATCH_MAX * BSIZE <= VM_COPYOUTV_MAX_BYTES,
	       "filesystem read batch must fit the VM copy window");

#define FS_TRANSIENT_PAGE_SYSTEM_CAP \
	PHYSICAL_PAGE_STORAGE_SYSTEM_RESERVED_LIMIT
#define FS_TRANSIENT_PAGE_PUBLIC_CAP 16U
#define FS_TRANSIENT_PAGE_WORKFLOW_CAP \
	PHYSICAL_PAGE_STORAGE_DOMAIN_RESERVED_LIMIT
static int fs_storage_accounts_ready;
static struct wait_queue fs_claim_waiters;
static struct thread *fs_claim_owner;
static struct wait_queue fs_allocator_waiters;
static struct thread *fs_allocator_owner;
static struct wait_queue fs_namespace_waiters;
static void *fs_namespace_owner;
static uint64 fs_namespace_owner_generation;
static struct wait_queue fs_dentry_waiters;
static void *fs_dentry_owner;
static uint64 fs_dentry_owner_generation;
static char fs_dentry_boot_token;

/*
 * File contents use an inode-local mapping guard rather than the filesystem
 * request gate.  Readers may therefore proceed in parallel across unrelated
 * inodes while truncate, allocation and detach publish one mapping image at a
 * time.  The wait queue is shared; the indexed state remains compact.
 */
struct inode_mapping_guard {
	uint readers;
	uint reader_waiters;
	uint writer_waiters;
	uint writer_depth;
	void *writer;
	uint64 writer_generation;
};

static struct inode_mapping_guard
	inode_mapping_guards[FS_ICACHE_SIZE];
static struct wait_queue inode_mapping_waiters;
static char inode_mapping_boot_token;

/* Free-block candidates amortize bitmap scans without allocating ahead. */
#define FS_BLOCK_MAGAZINE_SLOTS (VFS_SCOPE_MAX_ACTIVE + 2U)
#define FS_BLOCK_MAGAZINE_CAP 24U
#define FS_BLOCK_CANDIDATE_BYTES ((FSSIZE + 7U) / 8U)

struct fs_block_magazine {
	uint owner;
	uint count;
	uint blocks[FS_BLOCK_MAGAZINE_CAP];
};

static struct fs_block_magazine
	fs_block_magazines[FS_BLOCK_MAGAZINE_SLOTS];
static uchar fs_block_candidate_reserved[FS_BLOCK_CANDIDATE_BYTES];

/*
 * Foreground unlink/truncate publishes only the unreachable mapping image.
 * This bounded queue owns the detached token until a generation fence makes
 * batched allocator-map reclamation safe.
 */
#define FS_DEFERRED_RECLAIM_CAP 128U
#define FS_DEFERRED_RECLAIM_BATCH_UNITS 64U
#define FS_DEFERRED_RECLAIM_UNIQUE_BUFFERS 6U
#define FS_DEFERRED_RECLAIM_OWNER_CAP 32U
#define FS_DEFERRED_RECLAIM_SYSTEM_RESERVE 16U

struct fs_deferred_reclaim_entry {
	int reserved;
	int published;
	uint sponsor_owner;
	uint sponsor_class;
	uint64 fence_generation;
	struct inode_reclaim reclaim;
};

struct fs_deferred_reclaim_action {
	uint slot;
	uint block;
	uint advance;
	uint cursor;
	uint inode;
};

static struct fs_deferred_reclaim_entry
	fs_deferred_reclaims[FS_DEFERRED_RECLAIM_CAP];
static struct fs_deferred_reclaim_action
	fs_deferred_reclaim_plan[FS_DEFERRED_RECLAIM_BATCH_UNITS];
static struct inode_reclaim fs_deferred_reclaim_shadow;
static uint fs_deferred_reclaim_unique[
	FS_DEFERRED_RECLAIM_UNIQUE_BUFFERS];
static uint fs_deferred_reclaim_count;
static uint fs_deferred_reclaim_cursor;

/* Derived namespace indexes never authorize an object; they only locate the
 * dirent that the normal inode/label path must revalidate. */
#define FS_DENTRY_INDEX_CAP 512U
#define FS_DIRECTORY_INDEX_CAP 8U
#define FS_DIRECTORY_INDEX_MAX_ENTRIES NINODE
#define FS_DIRECTORY_INDEX_BITMAP_BYTES \
	((FS_DIRECTORY_INDEX_MAX_ENTRIES + 7U) / 8U)

enum fs_dentry_index_slot_state {
	FS_DENTRY_INDEX_EMPTY = 0,
	FS_DENTRY_INDEX_USED,
	FS_DENTRY_INDEX_TOMBSTONE,
};

struct fs_dentry_index_entry {
	uint hash;
	uint offset;
	uint dir_incarnation;
	uint target_incarnation;
	ushort dev;
	ushort dir_inum;
	ushort target_inum;
	uchar state;
};

struct fs_directory_index_state {
	uint dev;
	uint inum;
	uint incarnation;
	uint size;
	uint entries;
	uint first_free_entry;
	uint complete;
	uint overflow;
	uint used;
	uchar occupied[FS_DIRECTORY_INDEX_BITMAP_BYTES];
};

static struct fs_dentry_index_entry
	fs_dentry_index[FS_DENTRY_INDEX_CAP];
static struct fs_directory_index_state
	fs_directory_indexes[FS_DIRECTORY_INDEX_CAP];
static uint fs_dentry_index_used;
static uint fs_dentry_index_tombstones;
static uint fs_directory_index_cursor;
static uint64 fs_dentry_index_generation;
// The claim gate serializes this bounded workspace after mount recovery.
// Keeping it in BSS avoids adding a full indirect map to the syscall stack.
static uint fs_claim_blocks[MAXFILE + 1];
_Static_assert(MAXFILE + 1 == NDIRECT + 1 + NINDIRECT,
	       "claim workspace must include data and indirect-map blocks");

#define FS_SCRUB_BITMAP_BYTES(limit) (((limit) + 7U) / 8U)

// Mount recovery runs before the allocator is ready.  Keep its reachability
// maps in BSS rather than spending several KiB of the small kernel stack.
static uchar fs_scrub_reachable_blocks[FS_SCRUB_BITMAP_BYTES(FSSIZE)];
static uchar fs_scrub_reachable_inodes[FS_SCRUB_BITMAP_BYTES(NINODE)];

_Static_assert(VFS_SCOPE_MAX_ACTIVE == FS_WORKFLOW_SCOPE_SLOTS,
	       "storage and workflow admission slots must match");
_Static_assert(FS_LOOKUP_BUSY == VIRTIO_DISK_ERR_BUSY,
	       "filesystem lookup BUSY must preserve the device result");
_Static_assert(FS_OWNER_SYSTEM <= FS_QMAP_OWNER_PAYLOAD_MASK &&
	       FS_OWNER_PUBLIC <= FS_QMAP_OWNER_PAYLOAD_MASK &&
	       VFS_SCOPE_FIRST_DYNAMIC <= FS_QMAP_OWNER_PAYLOAD_MASK,
	       "persistent storage owners must fit the qmap recovery payload");
_Static_assert(VFS_SCOPE_MAX_ACTIVE ==
	       PHYSICAL_PAGE_RESERVED_DOMAIN_CAP,
	       "storage and physical reserve partitions must match");
_Static_assert(FS_TRANSIENT_PAGE_SYSTEM_CAP +
		       VFS_SCOPE_MAX_ACTIVE *
			       FS_TRANSIENT_PAGE_WORKFLOW_CAP ==
	       PHYSICAL_PAGE_STORAGE_RESERVED_BUDGET,
	       "storage physical promises must match reserve policy");
_Static_assert(FS_WORKFLOW_SCOPE_SLOTS > 0 &&
	       FS_WORKFLOW_MAX_FREE_NUMERATOR > 0 &&
	       FS_WORKFLOW_MAX_FREE_NUMERATOR <
		       FS_WORKFLOW_MAX_FREE_DENOMINATOR &&
	       FS_SYSTEM_BLOCK_MIN_RESERVE > 0 &&
	       FS_SYSTEM_INODE_MIN_RESERVE > 0 &&
	       FS_WORKFLOW_BLOCK_MIN_PER_SCOPE > 0 &&
	       FS_WORKFLOW_INODE_MIN_PER_SCOPE > 0,
	       "workflow storage policy must keep bounded shared capacity");
_Static_assert((FS_DENTRY_INDEX_CAP & (FS_DENTRY_INDEX_CAP - 1)) == 0 &&
	       FS_DENTRY_INDEX_CAP >= 2,
	       "dentry index must use power-of-two open addressing");
_Static_assert(NINODE <= 65535U && NDEV <= 65535U,
	       "compact dentry identities must fit on-disk limits");
_Static_assert(FS_DIRECTORY_INDEX_MAX_ENTRIES > 0,
	       "directory occupancy index must cover a full inode");
_Static_assert(sizeof(struct fs_dentry_index_entry) <= 24,
	       "dentry entry must retain only derived location identity");
_Static_assert(sizeof(fs_dentry_index) + sizeof(fs_directory_indexes) <=
	       16U * 1024U,
	       "dentry derived index BSS must stay within 16 KiB");

static int writei_charged(struct inode *, const struct vfs_cred *,
			  const struct fs_storage_charge *, int, uint64, uint,
			  uint, enum fs_epoch_phase);
static int fs_qmap_write_forward(int dev, uint block, uint state);
static int fs_bitmap_write_forward(int dev, uint block, int allocated);
static int bfree(int dev, uint block);
static int fs_block_candidate_drain(uint owner);
static int fs_block_candidate_is_reserved(uint block);
static int fs_block_candidate_reclaim(uint block);
#ifndef FS_ALLOCATOR_FAULT_TEST_PROFILE
static int fs_deferred_reclaim_reserve(uint, struct inode_reclaim *);
static void fs_deferred_reclaim_cancel(struct inode_reclaim *);
static int fs_deferred_reclaim_maintain_owner(uint, int);
#endif
static void itruncate_reclaim_finish(struct inode_reclaim *);
static int itruncate_detach_all(struct inode *, struct inode_reclaim *);
static int fs_directory_index_rebuild(struct inode *);
static void fs_directory_index_invalidate(struct inode *);
static void fs_dentry_index_invalidate_directory(struct inode *);
static int fs_dentry_gate_lock(void);
static void fs_dentry_gate_lock_uninterruptible(void);
static void fs_dentry_gate_unlock(void);
static int fs_namespace_gate_lock(void);
static void fs_namespace_gate_unlock(void);

static int fs_io_fail(enum fs_failure_class failure)
{
	if (failure == FS_FAILURE_SCHEDULING_UNAVAILABLE)
		return VIRTIO_DISK_ERR_BUSY;
	if (failure == FS_FAILURE_METADATA_WRITE_INDETERMINATE) {
		fs_io_health = FS_IO_INDETERMINATE;
		fs_storage.ready = 0;
	} else if (failure == FS_FAILURE_MOUNT_UNAVAILABLE) {
		if (fs_io_health != FS_IO_INDETERMINATE)
			fs_io_health = FS_IO_UNAVAILABLE;
		fs_storage.ready = 0;
	}
	return -1;
}

/* Reads and pre-submit BUSY failures do not make persistent state ambiguous. */
static int fs_read_block(uint dev, uint blockno, struct buf **out)
{
	struct buf *bp;
	int result;

	if (out == 0)
		return fs_io_fail(FS_FAILURE_OPERATION);
	*out = 0;
	if (fs_io_health != FS_IO_HEALTHY)
		return -1;
	result = bread(dev, blockno, &bp);
	if (result >= 0) {
		*out = bp;
		return 0;
	}
	return fs_io_fail(result == VIRTIO_DISK_ERR_BUSY ?
			      FS_FAILURE_SCHEDULING_UNAVAILABLE :
			      FS_FAILURE_TRANSIENT_READ);
}

static int fs_read_blocks_batch(uint dev, const uint *blocknos,
				struct buf **out, uint count)
{
	int result;

	if (blocknos == 0 || out == 0 || count < 2 ||
	    count > FS_READ_BATCH_MAX)
		return fs_io_fail(FS_FAILURE_OPERATION);
	if (fs_io_health != FS_IO_HEALTHY)
		return -1;
	result = bread_batch(dev, blocknos, out, count);
	if (result >= 0)
		return 0;
	return fs_io_fail(result == VIRTIO_DISK_ERR_BUSY ?
			      FS_FAILURE_SCHEDULING_UNAVAILABLE :
			      FS_FAILURE_TRANSIENT_READ);
}

static int fs_read_device_block(uint dev, uint blockno, struct buf **out)
{
	struct buf *bp;
	int result;

	if (out == 0)
		return fs_io_fail(FS_FAILURE_OPERATION);
	*out = 0;
	if (fs_io_health != FS_IO_HEALTHY)
		return -1;
	result = bread_device(dev, blockno, &bp);
	if (result >= 0) {
		*out = bp;
		return 0;
	}
	return fs_io_fail(result == VIRTIO_DISK_ERR_BUSY ?
			      FS_FAILURE_SCHEDULING_UNAVAILABLE :
			      FS_FAILURE_TRANSIENT_READ);
}

static enum fs_failure_class fs_write_failure(int result)
{
	if (result == VIRTIO_DISK_ERR_BUSY)
		return FS_FAILURE_SCHEDULING_UNAVAILABLE;
	if (result == VIRTIO_DISK_ERR_OFFLINE ||
	    result == VIRTIO_DISK_ERR_RANGE)
		return FS_FAILURE_OPERATION;
	return FS_FAILURE_METADATA_WRITE_INDETERMINATE;
}

static int fs_epoch_preflight(uint worst_case_buffers)
{
#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
	(void)worst_case_buffers;
	return 0;
#else
	int result = fs_epoch_reserve(bio_current_owner(),
				      worst_case_buffers);

	if (result >= 0)
		return 0;
	return fs_io_fail(result == VIRTIO_DISK_ERR_BUSY ?
			      FS_FAILURE_SCHEDULING_UNAVAILABLE :
			      FS_FAILURE_METADATA_WRITE_INDETERMINATE);
#endif
}

static int
fs_epoch_preflight_phase(uint worst_case_buffers, enum fs_epoch_phase phase)
{
#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
	(void)phase;
	return fs_epoch_preflight(worst_case_buffers);
#else
	int result;

	result = fs_epoch_reserve_phase(bio_current_owner(),
					worst_case_buffers, phase);
	if (result >= 0)
		return 0;
	return fs_io_fail(result == VIRTIO_DISK_ERR_BUSY ?
			      FS_FAILURE_SCHEDULING_UNAVAILABLE :
			      FS_FAILURE_METADATA_WRITE_INDETERMINATE);
#endif
}

static int fs_epoch_destructive_begin(int *entered)
{
	int result;

	if (entered == 0)
		return -1;
	*entered = 0;
	if (!fs_epoch_runtime_enabled() || fs_epoch_bypass_active())
		return 0;
	if (!fs_epoch_request_held())
		return -1;
	if (fs_epoch_dirty()) {
		result = fs_epoch_commit();
		if (result < 0)
			return fs_io_fail(
				FS_FAILURE_METADATA_WRITE_INDETERMINATE);
	}
	if (fs_epoch_bypass_begin() < 0)
		return -1;
	*entered = 1;
	return 0;
}

static void fs_epoch_destructive_end(int entered)
{
	if (entered)
		fs_epoch_bypass_end();
}

/* Allocation maps, directories, indirect maps and dinodes are FS metadata. */
static int fs_write_ordered_block(struct buf *bp,
				  enum fs_epoch_phase phase, int data)
{
	int result;

	if (fs_io_health != FS_IO_HEALTHY)
		return -1;
#ifndef FS_ALLOCATOR_FAULT_TEST_PROFILE
	if (data) {
		result = fs_epoch_note_data(bio_current_owner());
		if (result == FS_EPOCH_OWNER_MISMATCH ||
		    result == FS_EPOCH_FULL)
			return VIRTIO_DISK_ERR_BUSY;
		if (result < 0)
			return result;
	}
	result = fs_epoch_stage(bp, phase);
	if (result == FS_EPOCH_CACHED)
		return 0;
	if (result == FS_EPOCH_OWNER_MISMATCH ||
	    result == FS_EPOCH_FULL)
		return VIRTIO_DISK_ERR_BUSY;
	if (result < 0)
		return result;
#else
	(void)phase;
#endif
	result = bwrite(bp);
	if (result >= 0)
		return 0;
	if (data)
		return fs_io_fail(result == VIRTIO_DISK_ERR_BUSY ?
				      FS_FAILURE_SCHEDULING_UNAVAILABLE :
				      FS_FAILURE_OPERATION);
	return fs_io_fail(fs_write_failure(result));
}

/* Allocation maps, zeroed blocks and indirect maps precede inode publish. */
static int fs_write_metadata_block(struct buf *bp)
{
	return fs_write_ordered_block(bp, FS_EPOCH_PREPARE, 0);
}

static int fs_write_inode_block(struct buf *bp)
{
	return fs_write_ordered_block(bp, FS_EPOCH_INODE, 0);
}

static int fs_write_namespace_block(struct buf *bp,
				    enum fs_epoch_phase phase)
{
	if (phase != FS_EPOCH_NAMESPACE_DETACH &&
	    phase != FS_EPOCH_NAMESPACE_ATTACH)
		return -1;
	return fs_write_ordered_block(bp, phase, 0);
}

/* File payload failures are contained by the owning object/protocol. */
static int fs_write_data_block(struct buf *bp)
{
	return fs_write_ordered_block(bp, FS_EPOCH_PREPARE, 1);
}

/* A completed metadata write is not a power-loss ordering point by itself. */
static int fs_durable_barrier(void)
{
	int result;

	if (fs_io_health != FS_IO_HEALTHY)
		return -1;
#ifndef FS_ALLOCATOR_FAULT_TEST_PROFILE
	if (fs_epoch_request_held() && fs_epoch_dirty() &&
	    !fs_epoch_bypass_active())
		return 0;
#endif
	result = bio_durable_flush();
	if (result >= 0)
		return 0;
	if (result == VIRTIO_DISK_ERR_BUSY)
		return result;
	return fs_io_fail(FS_FAILURE_METADATA_WRITE_INDETERMINATE);
}

/*
 * Once an allocator intent is durable it must run forward.  The cleanup
 * checkpoint pays I/O debt without allowing thread teardown to strand a
 * half-published block state.
 */
static int fs_forward_checkpoint(void)
{
	if (fs_epoch_request_held() && fs_epoch_dirty() &&
	    !fs_epoch_bypass_active()) {
		int result = fs_epoch_commit();

		if (result < 0)
			return fs_io_fail(
				FS_FAILURE_METADATA_WRITE_INDETERMINATE);
	}
	if (bio_request_settle_quiescent_cleanup() == 0)
		return 0;
	return fs_io_fail(FS_FAILURE_METADATA_WRITE_INDETERMINATE);
}

#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
static int fs_allocator_fault_before(uint operation, uint phase, int forward)
{
	int result = fs_allocator_test_before(operation, phase);

	if (result == 0)
		return 0;
	if (result == VIRTIO_DISK_ERR_BUSY && !forward)
		return result;
	if (result == VIRTIO_DISK_ERR_BUSY)
		return fs_forward_checkpoint();
	return fs_io_fail(FS_FAILURE_METADATA_WRITE_INDETERMINATE);
}

#define FS_ALLOCATOR_FAULT_AFTER(operation, phase) \
	fs_allocator_test_after((operation), (phase))
#else
static int fs_allocator_fault_before(uint operation, uint phase, int forward)
{
	(void)operation;
	(void)phase;
	(void)forward;
	return 0;
}

#define FS_ALLOCATOR_FAULT_AFTER(operation, phase) do { \
	(void)(operation); \
	(void)(phase); \
} while (0)
#endif

static int fs_durable_barrier_forward(void)
{
	for (;;) {
		int result = fs_durable_barrier();

		if (result != VIRTIO_DISK_ERR_BUSY)
			return result;
		result = fs_forward_checkpoint();
		if (result < 0)
			return result;
	}
}

// Read the super block.
static int readsb(int dev, struct superblock *sb)
{
	struct buf *bp;

	if (fs_read_block(dev, 1, &bp) < 0)
		return -1;
	memmove(sb, bp->data, sizeof(*sb));
	brelse(bp);
	return 0;
}

static uint fs_div_round_up(uint value, uint divisor)
{
	return fs_policy_div_round_up(value, divisor);
}

static uint fs_unreserved_capacity(uint free_count, uint system_reserve,
				   uint other_guarantees)
{
	uint reserved = system_reserve + other_guarantees;

	return free_count > reserved ? free_count - reserved : 0;
}

static int fs_layout_valid(void)
{
	uint inode_blocks;
	uint bitmap_blocks;
	uint owner_blocks;

	if (sb.magic != FSMAGIC || sb.size < 2 || sb.size > FSSIZE ||
	    sb.ninodes <= ROOTINO ||
	    sb.ninodes > NINODE)
		return 0;
	inode_blocks = fs_div_round_up(sb.ninodes, IPB);
	bitmap_blocks = fs_div_round_up(sb.size, BPB);
	owner_blocks = fs_div_round_up(sb.size, QPB);
	return sb.inodestart == 2 &&
	       sb.bmapstart == sb.inodestart + inode_blocks &&
	       sb.qmapstart == sb.bmapstart + bitmap_blocks &&
	       sb.datastart == sb.qmapstart + owner_blocks &&
	       sb.datastart < sb.size &&
	       sb.nblocks == sb.size - sb.datastart &&
	       fs_policy_contract_geometry_valid(
		       sb.nblocks, sb.ninodes - 1,
		       sb.storage_policy_version, sb.storage_scope_slots,
		       sb.public_principal_id,
		       sb.workflow_block_guarantee,
		       sb.workflow_inode_guarantee,
		       sb.system_block_reserve, sb.system_inode_reserve,
		       sb.storage_policy_checksum);
}

static int fs_qmap_read(int dev, uint block, uint *owner_out)
{
	struct buf *bp;
	int result;

	if (owner_out == 0)
		return fs_io_fail(FS_FAILURE_OPERATION);
	result = fs_read_block(dev, QBLOCK(block, sb), &bp);
	if (result < 0)
		return result;
	*owner_out = ((uint *)bp->data)[block % QPB];
	brelse(bp);
	return 0;
}

static int fs_qmap_write(int dev, uint block, uint owner)
{
	struct buf *bp;
	int result;

	result = fs_read_block(dev, QBLOCK(block, sb), &bp);
	if (result < 0)
		return result;
	((uint *)bp->data)[block % QPB] = owner;
	result = fs_write_metadata_block(bp);
	brelse(bp);
	return result;
}

static int fs_storage_owner_valid(uint owner)
{
	if (owner == FS_OWNER_SYSTEM || owner == sb.public_principal_id)
		return 1;
	return FS_OWNER_IS_SCOPE(owner) &&
	       (owner & FS_QMAP_ALLOCATING_FLAG) == 0 &&
	       FS_OWNER_SCOPE_ID(owner) >= VFS_SCOPE_FIRST_DYNAMIC &&
	       FS_OWNER_SCOPE_ID(owner) <= FS_OWNER_MAX_PERSISTENT_ID;
}

static uint fs_qmap_owner_payload(uint owner)
{
	return FS_OWNER_IS_SCOPE(owner) ? FS_OWNER_SCOPE_ID(owner) : owner;
}

static uint fs_qmap_transition(uint flag, uint owner)
{
	if (!fs_storage_owner_valid(owner) ||
	    (flag != FS_QMAP_ALLOCATING_FLAG && flag != FS_QMAP_FREEING_FLAG))
		return FS_OWNER_NONE;
	return flag | fs_qmap_owner_payload(owner);
}

static int fs_qmap_transition_owner(uint state, uint flag, uint *owner_out)
{
	uint payload;
	uint owner;

	if (owner_out == 0 || (state & FS_QMAP_STATE_MASK) != flag)
		return -1;
	payload = state & FS_QMAP_OWNER_PAYLOAD_MASK;
	owner = payload >= VFS_SCOPE_FIRST_DYNAMIC ?
		FS_OWNER_SCOPE(payload) : payload;
	if (!fs_storage_owner_valid(owner))
		return -1;
	*owner_out = owner;
	return 0;
}

static int fs_bitmap_write(int dev, uint block, int allocated)
{
	struct buf *bp;
	uint bit;
	int result;

	if (block < sb.datastart || block >= sb.size)
		return fs_io_fail(FS_FAILURE_OPERATION);
	result = fs_read_block(dev, BBLOCK(block, sb), &bp);
	if (result < 0)
		return result;
	bit = block % BPB;
	if (allocated)
		bp->data[bit / 8] |= 1U << (bit % 8);
	else
		bp->data[bit / 8] &= ~(1U << (bit % 8));
	result = fs_write_metadata_block(bp);
	brelse(bp);
	return result;
}

static int fs_scrub_bit_test(const uchar *map, uint bit)
{
	return (map[bit / 8] & (1U << (bit % 8))) != 0;
}

static void fs_scrub_bit_set(uchar *map, uint bit)
{
	map[bit / 8] |= 1U << (bit % 8);
}

static int fs_scrub_block_allocated(int dev, uint block, int *allocated_out)
{
	struct buf *bp;
	uint bit;
	int result;

	if (block < sb.datastart || block >= sb.size)
		return -1;
	if (allocated_out == 0)
		return fs_io_fail(FS_FAILURE_OPERATION);
	result = fs_read_block(dev, BBLOCK(block, sb), &bp);
	if (result < 0)
		return result;
	bit = block % BPB;
	*allocated_out =
		(bp->data[bit / 8] & (1U << (bit % 8))) != 0;
	brelse(bp);
	return 0;
}

static int fs_scrub_mark_block(int dev, uint block, uint expected_owner)
{
	int allocated = 0;
	uint qstate;
	uint allocating;

	if (block < sb.datastart || block >= sb.size ||
	    !fs_storage_owner_valid(expected_owner))
		return -1;
	if (fs_scrub_bit_test(fs_scrub_reachable_blocks, block))
		return -1;
	if (fs_scrub_block_allocated(dev, block, &allocated) < 0 ||
	    fs_qmap_read(dev, block, &qstate) < 0)
		return -1;
	if (allocated && qstate == expected_owner) {
		fs_scrub_bit_set(fs_scrub_reachable_blocks, block);
		return 0;
	}

	/*
	 * Reachability is authoritative at mount.  Replay the same ordered
	 * allocator protocol so a second reset at any point remains recoverable.
	 */
	allocating = fs_qmap_transition(FS_QMAP_ALLOCATING_FLAG,
					expected_owner);
	if (allocating == FS_OWNER_NONE ||
	    fs_qmap_write_forward(dev, block, allocating) < 0)
		return -1;
	if (!allocated && fs_bitmap_write_forward(dev, block, 1) < 0)
		return -1;
	if (fs_qmap_write_forward(dev, block, expected_owner) < 0)
		return -1;
	fs_scrub_bit_set(fs_scrub_reachable_blocks, block);
	return 0;
}

/*
 * Clear an indirect-map suffix by replaying the write after a pre-submit BUSY.
 * The caller has already published a shorter EOF, so the suffix is no longer
 * part of the file.  Its blocks deliberately remain unmarked and the orphan
 * sweep can reclaim them only after this map update reaches stable storage.
 */
static int fs_scrub_clear_indirect_suffix_forward(int dev, uint block,
						   uint first)
{
	for (;;) {
		struct buf *bp;
		uint *entries;
		int changed = 0;
		int result;

		result = fs_read_block(dev, block, &bp);
		if (result == VIRTIO_DISK_ERR_BUSY) {
			if (fs_forward_checkpoint() < 0)
				return -1;
			continue;
		}
		if (result < 0)
			return result;
		entries = (uint *)bp->data;
		for (uint i = first; i < NINDIRECT; i++) {
			if (entries[i] == 0)
				continue;
			entries[i] = 0;
			changed = 1;
		}
		if (!changed) {
			brelse(bp);
			return 0;
		}
		result = fs_write_metadata_block(bp);
		brelse(bp);
		if (result == VIRTIO_DISK_ERR_BUSY) {
			if (fs_forward_checkpoint() < 0)
				return -1;
			continue;
		}
		if (result < 0)
			return result;
		return fs_durable_barrier_forward();
	}
}

// EOF is the commit record for the separately persisted indirect map.  Mark
// only its required prefix; mount recovery removes a stale suffix before the
// orphan sweep is allowed to release those blocks.
static int fs_scrub_mark_inode_blocks(int dev, const struct dinode *dip)
{
	struct buf *bp;
	uint *entries;
	uint indirect_needed;
	uint needed;
	uint owner;
	int result;

	owner = dip->fs_owner_domain;
	if (dip->size > MAXFILE * BSIZE ||
	    dip->fs_owner_version != FS_OWNER_VERSION ||
	    !fs_storage_owner_valid(owner))
		return -1;
	needed = fs_div_round_up(dip->size, BSIZE);
	for (uint i = 0; i < NDIRECT; i++) {
		if (i < needed && dip->addrs[i] == 0)
			return -1;
		if (dip->addrs[i] != 0 &&
		    fs_scrub_mark_block(dev, dip->addrs[i], owner) < 0)
			return -1;
	}
	if (dip->addrs[NDIRECT] == 0) {
		if (needed > NDIRECT)
			return -1;
		return 0;
	}
	if (fs_scrub_mark_block(dev, dip->addrs[NDIRECT], owner) < 0)
		return -1;
	for (;;) {
		result = fs_read_block(dev, dip->addrs[NDIRECT], &bp);
		if (result != VIRTIO_DISK_ERR_BUSY)
			break;
		if (fs_forward_checkpoint() < 0)
			return -1;
	}
	if (result < 0)
		return result;
	entries = (uint *)bp->data;
	indirect_needed = needed > NDIRECT ? needed - NDIRECT : 0;
	for (uint i = 0; i < indirect_needed; i++) {
		if (entries[i] == 0) {
			brelse(bp);
			return -1;
		}
		if (fs_scrub_mark_block(dev, entries[i], owner) < 0) {
			brelse(bp);
			return -1;
		}
	}
	brelse(bp);
	if (indirect_needed < NINDIRECT &&
	    fs_scrub_clear_indirect_suffix_forward(
		    dev, dip->addrs[NDIRECT], indirect_needed) < 0)
		return -1;
	return 0;
}

static int fs_scrub_read_dinode(int dev, uint inum, struct dinode *out)
{
	struct buf *bp;
	struct dinode *dip;

	if (out == 0)
		return fs_io_fail(FS_FAILURE_OPERATION);
	if (fs_read_block(dev, IBLOCK(inum, sb), &bp) < 0)
		return -1;
	dip = (struct dinode *)bp->data + inum % IPB;
	memmove(out, dip, sizeof(*out));
	brelse(bp);
	return 0;
}

static int fs_scrub_inode_block(int dev, const struct dinode *dip, uint bn,
				uint *block_out)
{
	struct buf *bp;
	uint block;

	if (block_out == 0)
		return fs_io_fail(FS_FAILURE_OPERATION);
	if (bn < NDIRECT) {
		*block_out = dip->addrs[bn];
		return 0;
	}
	bn -= NDIRECT;
	if (bn >= NINDIRECT || dip->addrs[NDIRECT] == 0) {
		*block_out = 0;
		return 0;
	}
	if (fs_read_block(dev, dip->addrs[NDIRECT], &bp) < 0)
		return -1;
	block = ((uint *)bp->data)[bn];
	brelse(bp);
	*block_out = block;
	return 0;
}

static int fs_scrub_mark_root_entries(int dev, const struct dinode *root)
{
	uint blocks;
	uint entries_seen = 0;

	if (root->size == 0 || root->size % sizeof(struct dirent) != 0 ||
	    root->size > sb.ninodes * sizeof(struct dirent))
		return -1;
	blocks = fs_div_round_up(root->size, BSIZE);
	for (uint bn = 0; bn < blocks; bn++) {
		struct buf *bp;
		uint block = 0;
		uint bytes = MIN(root->size - bn * BSIZE, BSIZE);

		if (fs_scrub_inode_block(dev, root, bn, &block) < 0)
			return -1;
		if (block == 0)
			return -1;
		if (fs_read_block(dev, block, &bp) < 0)
			return -1;
		for (uint off = 0; off < bytes; off += sizeof(struct dirent)) {
			struct dirent de;
			struct dinode child;

			memmove(&de, bp->data + off, sizeof(de));
			entries_seen++;
			if (de.inum == 0)
				continue;
			if (de.inum >= sb.ninodes) {
				brelse(bp);
				return -1;
			}
			if (fs_scrub_bit_test(fs_scrub_reachable_inodes,
					      de.inum)) {
				brelse(bp);
				return -1;
			}
			if (fs_scrub_read_dinode(dev, de.inum, &child) < 0) {
				brelse(bp);
				return -1;
			}
			if (child.type != T_FILE) {
				brelse(bp);
				return -1;
			}
			fs_scrub_bit_set(fs_scrub_reachable_inodes, de.inum);
			if (fs_scrub_mark_inode_blocks(dev, &child) < 0) {
				brelse(bp);
				return -1;
			}
		}
		brelse(bp);
	}
	if (entries_seen != root->size / sizeof(struct dirent))
		return -1;
	return 0;
}

static void fs_scrub_retire_dinode(struct dinode *dip, uint inum)
{
	uint incarnation = dip->vfs_incarnation;

	memset(dip, 0, sizeof(*dip));
	dip->vfs_magic = VFS_LABEL_MAGIC;
	dip->vfs_version = VFS_LABEL_VERSION;
	dip->vfs_flags = VFS_LABEL_F_FREE;
	dip->vfs_policy = VFS_POLICY_FREE;
	dip->vfs_exec_profile = VFS_EXEC_PROFILE_NONE;
	dip->vfs_policy_generation = VFS_POLICY_GENERATION;
	dip->vfs_incarnation = incarnation ? incarnation : 1;
	dip->vfs_checksum = vfs_label_checksum(
		inum, dip->vfs_magic, dip->vfs_version, dip->vfs_flags,
		dip->vfs_scope_id, dip->vfs_policy, dip->vfs_exec_profile,
		dip->vfs_policy_generation, dip->vfs_incarnation,
		dip->fs_owner_domain, dip->fs_owner_version);
}

static int fs_scrub_retire_inode_forward(int dev, uint inum, int *changed)
{
	for (;;) {
		struct buf *bp;
		struct dinode *dip;
		int result;

		result = fs_read_block(dev, IBLOCK(inum, sb), &bp);
		if (result == VIRTIO_DISK_ERR_BUSY) {
			if (fs_forward_checkpoint() < 0)
				return -1;
			continue;
		}
		if (result < 0)
			return result;
		dip = (struct dinode *)bp->data + inum % IPB;
		if (dip->type == 0 && dip->fs_owner_domain == FS_OWNER_NONE &&
		    dip->fs_owner_version == 0) {
			brelse(bp);
			return 0;
		}
		fs_scrub_retire_dinode(dip, inum);
		result = fs_write_inode_block(bp);
		brelse(bp);
		if (result == VIRTIO_DISK_ERR_BUSY) {
			if (fs_forward_checkpoint() < 0)
				return -1;
			continue;
		}
		if (result < 0)
			return result;
		if (changed)
			*changed = 1;
		return 0;
	}
}

// The filesystem has one flat, fixed-size root namespace.  Reconstructing
// reachability from that root makes interrupted unlink/allocation cleanup
// independent of process lifetime and keeps persistent quota usage bounded.
static int fs_mount_scrub(int dev)
{
	struct dinode root;
	int inodes_changed = 0;

	memset(fs_scrub_reachable_blocks, 0,
	       sizeof(fs_scrub_reachable_blocks));
	memset(fs_scrub_reachable_inodes, 0,
	       sizeof(fs_scrub_reachable_inodes));
	if (fs_scrub_read_dinode(dev, ROOTINO, &root) < 0)
		return -1;
	if (root.type != T_DIR)
		return -1;
	fs_scrub_bit_set(fs_scrub_reachable_inodes, ROOTINO);
	if (fs_scrub_mark_inode_blocks(dev, &root) < 0 ||
	    fs_scrub_mark_root_entries(dev, &root) < 0)
		return -1;

	// Publish every orphan inode as a valid FREE object before releasing its
	// blocks.  A reset during either pass is therefore safe to retry.
	for (uint inum = 1; inum < sb.ninodes; inum++) {
		if (fs_scrub_bit_test(fs_scrub_reachable_inodes, inum))
			continue;
		if (fs_scrub_retire_inode_forward(
			    dev, inum, &inodes_changed) < 0)
			return -1;
	}
	if (inodes_changed && fs_durable_barrier_forward() < 0)
		return -1;

	for (uint block = sb.datastart; block < sb.size; block++) {
		uint owner = FS_OWNER_NONE;
		uint qstate;
		uint freeing;
		int allocated;

		if (fs_scrub_bit_test(fs_scrub_reachable_blocks, block))
			continue;
		if (fs_scrub_block_allocated(dev, block, &allocated) < 0 ||
		    fs_qmap_read(dev, block, &qstate) < 0)
			return -1;
		if (!allocated && qstate == FS_OWNER_NONE)
			continue;

		/* Canonicalize every orphan or interrupted allocator state. */
		if (fs_storage_owner_valid(qstate))
			owner = qstate;
		else if (fs_qmap_transition_owner(
				 qstate, FS_QMAP_ALLOCATING_FLAG, &owner) < 0)
			(void)fs_qmap_transition_owner(
				qstate, FS_QMAP_FREEING_FLAG, &owner);
		if (allocated && owner != FS_OWNER_NONE) {
			freeing = fs_qmap_transition(FS_QMAP_FREEING_FLAG,
						     owner);
			if (freeing == FS_OWNER_NONE ||
			    (qstate != freeing &&
			     fs_qmap_write_forward(dev, block, freeing) < 0))
				return -1;
			qstate = freeing;
		}
		if (allocated && fs_bitmap_write_forward(dev, block, 0) < 0)
			return -1;
		if (qstate != FS_OWNER_NONE &&
		    fs_qmap_write_forward(dev, block, FS_OWNER_NONE) < 0)
			return -1;
	}
	return 0;
}

static int fs_storage_import_account(
	struct resource_account_handle account,
	enum resource_charge_class charge_class, uint blocks, uint inodes)
{
	struct resource_request requests[2];
	uint count = 0;

	if (blocks != 0) {
		requests[count].kind = RESOURCE_FS_BLOCK;
		requests[count++].amount = blocks;
	}
	if (inodes != 0) {
		requests[count].kind = RESOURCE_FS_INODE;
		requests[count++].amount = inodes;
	}
	if (count == 0)
		return 0;
	return resource_import_usage(account, charge_class, requests, count);
}

static int fs_storage_accounts_sync(uint public_blocks,
				    uint public_inodes,
				    uint reserved_blocks,
				    uint reserved_inodes)
{
	struct resource_account_limits system_limits;
	struct resource_account_limits public_limits;

	if (!fs_storage_accounts_ready) {
		memset(&system_limits, 0, sizeof(system_limits));
		memset(&public_limits, 0, sizeof(public_limits));
		system_limits.class_limit[RESOURCE_CHARGE_RESERVED]
					 [RESOURCE_FS_BLOCK] =
			sb.nblocks;
		system_limits.class_limit[RESOURCE_CHARGE_RESERVED]
					 [RESOURCE_FS_INODE] =
			sb.ninodes - 1;
		system_limits.class_limit[RESOURCE_CHARGE_RESERVED]
					 [RESOURCE_BUFFER_CACHE] =
			IO_CACHE_SYSTEM_CAP;
		system_limits.class_limit[RESOURCE_CHARGE_RESERVED]
					 [RESOURCE_PHYSICAL_PAGE] =
			FS_TRANSIENT_PAGE_SYSTEM_CAP;
		public_limits.class_limit[RESOURCE_CHARGE_ORDINARY]
					 [RESOURCE_FS_BLOCK] =
			fs_storage.block_domain_limit;
		public_limits.class_limit[RESOURCE_CHARGE_ORDINARY]
					 [RESOURCE_FS_INODE] =
			fs_storage.inode_domain_limit;
		public_limits.class_limit[RESOURCE_CHARGE_ORDINARY]
					 [RESOURCE_BUFFER_CACHE] =
			IO_CACHE_PUBLIC_CAP;
		public_limits.class_limit[RESOURCE_CHARGE_ORDINARY]
					 [RESOURCE_PHYSICAL_PAGE] =
			FS_TRANSIENT_PAGE_PUBLIC_CAP;
		if (resource_policy_configure(
			    RESOURCE_FS_BLOCK, sb.nblocks,
			    fs_storage.block_domain_limit, sb.nblocks) < 0 ||
		    resource_policy_configure(
			    RESOURCE_FS_INODE, sb.ninodes - 1,
			    fs_storage.inode_domain_limit,
			    sb.ninodes - 1) < 0 ||
		    resource_account_create(
			    RESOURCE_ACCOUNT_STORAGE, FS_OWNER_SYSTEM,
			    RESOURCE_CHARGE_GRANT(
				    RESOURCE_CHARGE_RESERVED),
			    &system_limits, &fs_system_account) < 0 ||
		    resource_account_create(
			    RESOURCE_ACCOUNT_STORAGE,
			    fs_storage.public_principal_id,
			    RESOURCE_CHARGE_GRANT(
				    RESOURCE_CHARGE_ORDINARY),
			    &public_limits, &fs_public_account) < 0 ||
		    resource_account_member_acquire(fs_system_account) < 0 ||
		    resource_account_member_acquire(fs_public_account) < 0 ||
		    fs_storage_import_account(
			    fs_system_account, RESOURCE_CHARGE_RESERVED,
			    reserved_blocks, reserved_inodes) < 0 ||
		    fs_storage_import_account(
			    fs_public_account, RESOURCE_CHARGE_ORDINARY,
			    public_blocks, public_inodes) < 0 ||
		    bio_principal_bind(FS_OWNER_SYSTEM,
				       fs_system_account) < 0 ||
		    bio_principal_bind(fs_storage.public_principal_id,
				       fs_public_account) < 0)
			return -1;
		fs_storage_accounts_ready = 1;
		return 0;
	}
	struct resource_request system_usage[2] = {
		{
			.kind = RESOURCE_FS_BLOCK,
			.amount = reserved_blocks,
		},
		{
			.kind = RESOURCE_FS_INODE,
			.amount = reserved_inodes,
		},
	};
	struct resource_request public_usage[2] = {
		{
			.kind = RESOURCE_FS_BLOCK,
			.amount = public_blocks,
		},
		{
			.kind = RESOURCE_FS_INODE,
			.amount = public_inodes,
		},
	};

	/*
	 * Mount scans are authoritative.  Recovery and boot-lease reaping may
	 * change persistent ownership without going through the live allocator,
	 * so replace both counters as one bounded controller transaction instead
	 * of asserting against a stale first-pass import.
	 */
	if (resource_reconcile_usage(
		    fs_system_account, RESOURCE_CHARGE_RESERVED,
		    system_usage, 2) < 0 ||
	    resource_reconcile_usage(
		    fs_public_account, RESOURCE_CHARGE_ORDINARY,
		    public_usage, 2) < 0)
		return -1;
	return 0;
}

static int fs_storage_rebuild(int dev, int enforce_policy)
{
	uint max_scope_id = VFS_SCOPE_FIRST_DYNAMIC - 1;
	uint total_inodes = sb.ninodes - 1;
	uint public_blocks = 0;
	uint public_inodes = 0;
	uint reserved_blocks = 0;
	uint reserved_inodes = 0;
	struct buf *bitmap = 0;
	uint bitmap_block = ~0U;

	memset(&fs_storage, 0, sizeof(fs_storage));
	fs_storage.public_principal_id = sb.public_principal_id;
	fs_storage.block_alloc_cursor = sb.datastart;
	fs_storage.inode_alloc_cursor = 1;
	for (uint block = sb.datastart; block < sb.size; block++) {
		uint current = BBLOCK(block, sb);
		uint bit = block % BPB;
		uint owner;

		if (current != bitmap_block) {
			if (bitmap)
				brelse(bitmap);
			if (fs_read_block(dev, current, &bitmap) < 0)
				return -1;
			bitmap_block = current;
		}
		if (fs_qmap_read(dev, block, &owner) < 0) {
			brelse(bitmap);
			return -1;
		}
		if ((bitmap->data[bit / 8] & (1 << (bit % 8))) == 0) {
			if (owner != FS_OWNER_NONE) {
				brelse(bitmap);
				return -1;
			}
			fs_storage.free_blocks++;
			continue;
		}
		if (!fs_storage_owner_valid(owner)) {
			brelse(bitmap);
			return -1;
		}
		if (FS_OWNER_IS_SCOPE(owner)) {
			uint scope_id = FS_OWNER_SCOPE_ID(owner);

			if (scope_id < VFS_SCOPE_FIRST_DYNAMIC) {
				brelse(bitmap);
				return -1;
			}
			if (scope_id > max_scope_id)
				max_scope_id = scope_id;
			reserved_blocks++;
		} else if (owner == fs_storage.public_principal_id) {
			if (public_blocks == (uint)-1) {
				brelse(bitmap);
				return -1;
			}
			public_blocks++;
		} else if (owner != FS_OWNER_SYSTEM) {
			brelse(bitmap);
			return -1;
		} else {
			reserved_blocks++;
		}
	}
	if (bitmap)
		brelse(bitmap);

	for (uint inum = 1; inum < sb.ninodes; inum++) {
		struct buf *bp;
		struct dinode *dip;

		if (fs_read_block(dev, IBLOCK(inum, sb), &bp) < 0)
			return -1;
		dip = (struct dinode *)bp->data + inum % IPB;

		if (dip->type == 0) {
			if (dip->fs_owner_domain != FS_OWNER_NONE ||
			    dip->fs_owner_version != 0) {
				brelse(bp);
				return -1;
			}
			fs_storage.free_inodes++;
		} else {
			uint owner = dip->fs_owner_domain;

			if (!fs_storage_owner_valid(owner) ||
			    dip->fs_owner_version != FS_OWNER_VERSION) {
				brelse(bp);
				return -1;
			}
			if (FS_OWNER_IS_SCOPE(owner)) {
				uint scope_id = FS_OWNER_SCOPE_ID(owner);

				if (scope_id < VFS_SCOPE_FIRST_DYNAMIC) {
					brelse(bp);
					return -1;
				}
				if (scope_id > max_scope_id)
					max_scope_id = scope_id;
				reserved_inodes++;
			} else if (owner == fs_storage.public_principal_id) {
				if (public_inodes == (uint)-1) {
					brelse(bp);
					return -1;
				}
				public_inodes++;
			} else if (owner != FS_OWNER_SYSTEM) {
				brelse(bp);
				return -1;
			} else {
				reserved_inodes++;
			}
			if (dip->vfs_version == VFS_LABEL_VERSION &&
			    dip->vfs_policy == VFS_POLICY_WORKFLOW &&
			    dip->vfs_scope_id >= VFS_SCOPE_FIRST_DYNAMIC) {
				if (!FS_OWNER_IS_SCOPE(dip->fs_owner_domain) ||
				    FS_OWNER_SCOPE_ID(dip->fs_owner_domain) !=
					    dip->vfs_scope_id) {
					brelse(bp);
					return -1;
				}
				if (dip->vfs_scope_id > max_scope_id)
					max_scope_id = dip->vfs_scope_id;
			}
		}
		brelse(bp);
	}

	fs_storage.system_block_reserve = sb.system_block_reserve;
	fs_storage.system_inode_reserve = sb.system_inode_reserve;
	fs_storage.workflow_block_guarantee =
		sb.workflow_block_guarantee;
	fs_storage.workflow_inode_guarantee =
		sb.workflow_inode_guarantee;
	if (enforce_policy && !fs_policy_contract_runtime_funded(
			      fs_storage.free_blocks, fs_storage.free_inodes,
			      fs_storage.workflow_block_guarantee,
			      fs_storage.workflow_inode_guarantee))
		return -1;
	fs_storage.workflow_block_reserve =
		fs_storage.workflow_block_guarantee * VFS_SCOPE_MAX_ACTIVE;
	fs_storage.workflow_inode_reserve =
		fs_storage.workflow_inode_guarantee * VFS_SCOPE_MAX_ACTIVE;
	fs_storage.system_block_reserve_remaining = fs_policy_system_remaining(
		fs_storage.free_blocks, fs_storage.workflow_block_guarantee,
		fs_storage.system_block_reserve);
	fs_storage.system_inode_reserve_remaining = fs_policy_system_remaining(
		fs_storage.free_inodes, fs_storage.workflow_inode_guarantee,
		fs_storage.system_inode_reserve);

	uint public_block_capacity =
		sb.nblocks - fs_storage.system_block_reserve -
		fs_storage.workflow_block_reserve;
	uint public_inode_capacity =
		total_inodes - fs_storage.system_inode_reserve -
		fs_storage.workflow_inode_reserve;
	fs_storage.block_domain_limit = FS_DOMAIN_BLOCK_LIMIT ?
		FS_DOMAIN_BLOCK_LIMIT :
		fs_div_round_up(public_block_capacity, 4);
	fs_storage.inode_domain_limit = FS_DOMAIN_INODE_LIMIT ?
		FS_DOMAIN_INODE_LIMIT :
		fs_div_round_up(public_inode_capacity, 4);
	/*
	 * A configured tenant ceiling is an upper bound, not a promise that can
	 * exceed this filesystem's ordinary allocation pool.  Tiny images and
	 * deliberately large "unlimited" test profiles must therefore converge
	 * on the same effective limit used by the global controller.
	 */
	if (fs_storage.block_domain_limit > public_block_capacity)
		fs_storage.block_domain_limit = public_block_capacity;
	if (fs_storage.inode_domain_limit > public_inode_capacity)
		fs_storage.inode_domain_limit = public_inode_capacity;
	if (fs_storage.block_domain_limit == 0 && public_block_capacity != 0)
		fs_storage.block_domain_limit = 1;
	if (fs_storage.inode_domain_limit == 0 && public_inode_capacity != 0)
		fs_storage.inode_domain_limit = 1;
	if (public_blocks > fs_storage.block_domain_limit ||
	    public_inodes > fs_storage.inode_domain_limit)
		return -1;
	fs_storage.workflow_block_domain_limit =
		FS_WORKFLOW_DOMAIN_BLOCK_LIMIT ?
			FS_WORKFLOW_DOMAIN_BLOCK_LIMIT :
			fs_unreserved_capacity(
				fs_storage.free_blocks,
				fs_storage.system_block_reserve_remaining,
				(VFS_SCOPE_MAX_ACTIVE - 1) *
					fs_storage.workflow_block_guarantee);
	fs_storage.workflow_inode_domain_limit =
		FS_WORKFLOW_DOMAIN_INODE_LIMIT ?
			FS_WORKFLOW_DOMAIN_INODE_LIMIT :
			fs_unreserved_capacity(
				fs_storage.free_inodes,
				fs_storage.system_inode_reserve_remaining,
				(VFS_SCOPE_MAX_ACTIVE - 1) *
					fs_storage.workflow_inode_guarantee);
	if (fs_storage.workflow_block_domain_limit <
	    fs_storage.workflow_block_guarantee)
		fs_storage.workflow_block_domain_limit =
			fs_storage.workflow_block_guarantee;
	if (fs_storage.workflow_inode_domain_limit <
	    fs_storage.workflow_inode_guarantee)
		fs_storage.workflow_inode_domain_limit =
			fs_storage.workflow_inode_guarantee;
	proc_scope_set_id_floor(max_scope_id >= FS_OWNER_MAX_PERSISTENT_ID ?
				FS_OWNER_NONE : max_scope_id + 1);
	if (fs_storage_accounts_sync(public_blocks, public_inodes,
				     reserved_blocks, reserved_inodes) < 0)
		return -1;
	fs_storage.ready = 1;
	return 0;
}

int fs_storage_scope_account_create(
	uint scope_id, struct resource_account_handle *out)
{
	struct resource_account_limits limits;
	uint owner;
	int enabled;

	if (out == 0 || scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id > FS_OWNER_MAX_PERSISTENT_ID)
		return -1;
	*out = resource_account_none();
	enabled = intr_save();
	if (!fs_storage.ready) {
		intr_restore(enabled);
		return -1;
	}
	owner = FS_OWNER_SCOPE(scope_id);
	memset(&limits, 0, sizeof(limits));
	limits.class_limit[RESOURCE_CHARGE_RESERVED][RESOURCE_FS_BLOCK] =
		fs_storage.workflow_block_domain_limit;
	limits.class_limit[RESOURCE_CHARGE_RESERVED][RESOURCE_FS_INODE] =
		fs_storage.workflow_inode_domain_limit;
	limits.class_limit[RESOURCE_CHARGE_RESERVED][RESOURCE_BUFFER_CACHE] =
		IO_CACHE_WORKFLOW_CAP;
	limits.class_limit[RESOURCE_CHARGE_RESERVED][RESOURCE_PHYSICAL_PAGE] =
		FS_TRANSIENT_PAGE_WORKFLOW_CAP;
	if (resource_account_create(
		    RESOURCE_ACCOUNT_STORAGE, owner,
		    RESOURCE_CHARGE_GRANT(RESOURCE_CHARGE_RESERVED),
		    &limits, out) < 0 ||
	    resource_account_member_acquire(*out) < 0) {
		if (resource_account_handle_valid(*out))
			(void)resource_account_close(*out);
		*out = resource_account_none();
		intr_restore(enabled);
		return -1;
	}
	intr_restore(enabled);
	return 0;
}

int fs_storage_owner_account(uint owner,
			     struct resource_account_handle *out)
{
	if (out == 0 || !fs_storage_accounts_ready)
		return -1;
	if (owner == FS_OWNER_SYSTEM) {
		*out = fs_system_account;
		return resource_account_handle_valid(*out) ? 0 : -1;
	}
	if (owner == fs_storage.public_principal_id) {
		*out = fs_public_account;
		return resource_account_handle_valid(*out) ? 0 : -1;
	}
	if (FS_OWNER_IS_SCOPE(owner) &&
	    FS_OWNER_SCOPE_ID(owner) >= VFS_SCOPE_FIRST_DYNAMIC)
		return resource_account_find(
			RESOURCE_ACCOUNT_STORAGE, owner, out);
	return -1;
}

void fs_storage_scope_account_close(
	struct resource_account_handle account)
{
	struct resource_account_kind_snapshot snapshot;

	if (resource_account_kind_snapshot(
		    account, RESOURCE_FS_BLOCK, &snapshot) < 0 ||
	    snapshot.account_kind != RESOURCE_ACCOUNT_STORAGE ||
	    snapshot.external_id < FS_OWNER_SCOPE_FLAG ||
	    snapshot.external_id > (uint64)~0U ||
	    fs_block_candidate_drain((uint)snapshot.external_id) < 0)
		panic("workflow storage candidate close");
	if (resource_account_close(account) < 0 ||
	    resource_account_member_release(account, 0) < 0)
		panic("workflow storage account close");
}

int fs_storage_scope_admissible(void)
{
	uint required_blocks;
	uint required_inodes;
	int admissible = 0;
	int enabled = intr_save();

	if (!fs_storage.ready)
		goto out;
	required_blocks = fs_storage.system_block_reserve_remaining +
		vfs_scope_storage_guarantee(VFS_SCOPE_NONE, 0,
					    fs_storage.workflow_block_guarantee);
	required_inodes = fs_storage.system_inode_reserve_remaining +
		vfs_scope_storage_guarantee(VFS_SCOPE_NONE, 1,
					    fs_storage.workflow_inode_guarantee);
	admissible = fs_storage.free_blocks >= required_blocks &&
		     fs_storage.free_inodes >= required_inodes;
out:
	intr_restore(enabled);
	return admissible;
}

static int fs_storage_charge_from_vfs(const struct vfs_cred *cred,
				      struct fs_storage_charge *charge)
{
	if (charge == 0 || cred == 0)
		return -1;
	if (cred->kernel && cred->scope_id == VFS_SCOPE_NONE &&
	    cred->storage_principal_id == FS_OWNER_SYSTEM) {
		charge->owner = FS_OWNER_SYSTEM;
		charge->level = FS_CHARGE_SYSTEM;
		return 0;
	}
	if (cred->kernel ||
	    cred->storage_principal_id > FS_OWNER_MAX_PERSISTENT_ID)
		return -1;
	if (cred->scope_id >= VFS_SCOPE_FIRST_DYNAMIC) {
		if (cred->scope_id > FS_OWNER_MAX_PERSISTENT_ID ||
		    cred->storage_principal_id != cred->scope_id ||
		    !vfs_scope_active(cred->scope_id))
			return -1;
		charge->owner = FS_OWNER_SCOPE(cred->scope_id);
		charge->level = FS_CHARGE_WORKFLOW;
	} else {
		if (cred->scope_id != VFS_SCOPE_NONE ||
		    cred->storage_principal_id != fs_storage.public_principal_id)
			return -1;
		charge->owner = fs_storage.public_principal_id;
		charge->level = FS_CHARGE_PUBLIC;
	}
	return 0;
}

static int fs_storage_reserve_many(const struct fs_storage_charge *charge,
				   int inode, uint amount)
{
	uint *free_count;
	uint reserve;
	uint guarantee;
	uint reserve_spent = 0;
	uint *system_remaining;
	uint scope_id = VFS_SCOPE_NONE;
	int enabled;
	struct resource_account_handle account;
	struct resource_request request = {
		.kind = inode ? RESOURCE_FS_INODE : RESOURCE_FS_BLOCK,
		.amount = amount,
	};
	struct resource_reservation reservation;
	enum resource_charge_class charge_class;

	if (!fs_storage.ready || charge == 0 || amount == 0 ||
	    charge->level > FS_CHARGE_SYSTEM ||
	    charge->owner < FS_OWNER_SYSTEM)
		return -1;
	if ((charge->level == FS_CHARGE_WORKFLOW &&
	     (!FS_OWNER_IS_SCOPE(charge->owner) ||
	      FS_OWNER_SCOPE_ID(charge->owner) < VFS_SCOPE_FIRST_DYNAMIC)) ||
	    (charge->level == FS_CHARGE_PUBLIC &&
	     charge->owner != fs_storage.public_principal_id) ||
	    (charge->level == FS_CHARGE_SYSTEM &&
	     charge->owner != FS_OWNER_SYSTEM))
		return -1;
	free_count = inode ? &fs_storage.free_inodes : &fs_storage.free_blocks;
	system_remaining = inode ?
		&fs_storage.system_inode_reserve_remaining :
		&fs_storage.system_block_reserve_remaining;
	guarantee = inode ? fs_storage.workflow_inode_guarantee :
			      fs_storage.workflow_block_guarantee;
	if (charge->level == FS_CHARGE_WORKFLOW)
		scope_id = FS_OWNER_SCOPE_ID(charge->owner);
	enabled = intr_save();
	if (charge->level != FS_CHARGE_SYSTEM) {
		reserve = *system_remaining;
		reserve += vfs_scope_storage_guarantee(scope_id, inode,
						       guarantee);
	} else {
		// SYSTEM consumes a fungible reserve credit only after shared
		// capacity is gone. Lower tiers then stop reserving that spent
		// credit while every workflow guarantee remains intact.
		reserve = vfs_scope_storage_guarantee(VFS_SCOPE_NONE, inode,
						      guarantee);
	}

	if (amount > *free_count || *free_count - amount < reserve) {
		intr_restore(enabled);
		return -1;
	}
	if (fs_storage_owner_account(charge->owner, &account) < 0) {
		intr_restore(enabled);
		return -1;
	}
	charge_class = charge->level == FS_CHARGE_PUBLIC ?
		RESOURCE_CHARGE_ORDINARY : RESOURCE_CHARGE_RESERVED;
	if (resource_reserve_many(account, charge_class, &request, 1,
				  &reservation) < 0) {
		intr_restore(enabled);
		return -1;
	}
	if (resource_reservation_commit(&reservation) < 0)
		panic("filesystem resource commit");
	if (charge->level == FS_CHARGE_SYSTEM) {
		uint shared = *free_count > reserve + *system_remaining ?
			*free_count - reserve - *system_remaining : 0;

		reserve_spent = amount > shared ? amount - shared : 0;
		if (reserve_spent > *system_remaining)
			panic("system storage reserve invariant");
		*system_remaining -= reserve_spent;
	}
	*free_count -= amount;
	intr_restore(enabled);
	return 0;
}

static int fs_storage_reserve(const struct fs_storage_charge *charge,
			      int inode)
{
	return fs_storage_reserve_many(charge, inode, 1);
}

static void fs_storage_release_many_accounted(
	uint owner, struct resource_account_handle exact_account,
	int inode, uint amount)
{
	uint *free_count = inode ? &fs_storage.free_inodes :
					  &fs_storage.free_blocks;
	uint *system_remaining = inode ?
		&fs_storage.system_inode_reserve_remaining :
		&fs_storage.system_block_reserve_remaining;
	uint system_reserve = inode ? fs_storage.system_inode_reserve :
				      fs_storage.system_block_reserve;
	uint total = inode ? sb.ninodes - 1 : sb.nblocks;
	int enabled;
	struct resource_account_handle account;
	struct resource_request request = {
		.kind = inode ? RESOURCE_FS_INODE : RESOURCE_FS_BLOCK,
		.amount = amount,
	};
	enum resource_charge_class charge_class;

	if (owner < FS_OWNER_SYSTEM || amount == 0)
		panic("storage release invariant");
	// Another I/O may have closed the filesystem while this free was in
	// flight.  Persistent recovery will rebuild the counters; do not turn a
	// fail-closed transition into a kernel panic.
	if (!fs_storage.ready)
		return;
	enabled = intr_save();
	if (*free_count > total || amount > total - *free_count)
		panic("storage free count invariant");
	*free_count += amount;
	if (owner == FS_OWNER_SYSTEM && *system_remaining < system_reserve) {
		uint available = system_reserve - *system_remaining;

		*system_remaining += MIN(amount, available);
	}
	if (resource_account_handle_valid(exact_account)) {
		if (!resource_account_matches(exact_account,
					      RESOURCE_ACCOUNT_STORAGE,
					      owner))
			panic("storage release account identity");
		account = exact_account;
		charge_class = owner == fs_storage.public_principal_id ?
			RESOURCE_CHARGE_ORDINARY : RESOURCE_CHARGE_RESERVED;
	} else if (FS_OWNER_IS_SCOPE(owner)) {
		if (FS_OWNER_SCOPE_ID(owner) < VFS_SCOPE_FIRST_DYNAMIC)
			panic("workflow storage release invariant");
		/*
		 * Previous-boot workflow objects are imported into the reserved
		 * cleanup account. A live generation has its own exact principal.
		 */
		if (fs_storage_owner_account(owner, &account) < 0)
			account = fs_system_account;
		charge_class = RESOURCE_CHARGE_RESERVED;
	} else if (owner == fs_storage.public_principal_id) {
		account = fs_public_account;
		charge_class = RESOURCE_CHARGE_ORDINARY;
	} else if (owner != FS_OWNER_SYSTEM) {
		panic("storage principal release invariant");
	} else {
		account = fs_system_account;
		charge_class = RESOURCE_CHARGE_RESERVED;
	}
	if (resource_release_many(account, charge_class, &request, 1) < 0)
		panic("storage resource release invariant");
	intr_restore(enabled);
}

static void fs_storage_release_many(uint owner, int inode, uint amount)
{
	fs_storage_release_many_accounted(owner, resource_account_none(),
					   inode, amount);
}

static void fs_storage_release(uint owner, int inode)
{
	fs_storage_release_many(owner, inode, 1);
}

#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
void fs_allocator_test_storage_snapshot(
	struct fsalloc_test_snapshot *snapshot)
{
	int enabled;

	if (snapshot == 0)
		return;
	enabled = intr_save();
	snapshot->free_blocks = fs_storage.free_blocks;
	snapshot->free_inodes = fs_storage.free_inodes;
	snapshot->account_blocks = (uint)resource_account_class_usage(
		fs_public_account, RESOURCE_CHARGE_ORDINARY,
		RESOURCE_FS_BLOCK);
	snapshot->account_inodes = (uint)resource_account_class_usage(
		fs_public_account, RESOURCE_CHARGE_ORDINARY,
		RESOURCE_FS_INODE);
	intr_restore(enabled);
}
#endif

static int fs_claim_gate_lock(void)
{
	struct thread *self = curr_thread();
	int enabled;

	if (self == 0)
		return -1;
	enabled = intr_save();
	for (;;) {
		if (fs_claim_owner == 0) {
			fs_claim_owner = self;
			intr_restore(enabled);
			return 0;
		}
		if (fs_claim_owner == self)
			panic("storage claim gate recursion");
		if (wait_queue_sleep_irq(&fs_claim_waiters) != WAIT_QUEUE_OK) {
			intr_restore(enabled);
			return -1;
		}
	}
}

static void fs_claim_gate_unlock(void)
{
	struct thread *self = curr_thread();
	int enabled = intr_save();

	if (self == 0 || fs_claim_owner != self)
		panic("storage claim gate owner");
	fs_claim_owner = 0;
	wait_queue_wake_all(&fs_claim_waiters);
	intr_restore(enabled);
}

// Claiming already allocated storage changes only its billing principal.  It
// must not consume free-space or SYSTEM reserve credits a second time.
static int fs_storage_claim_public_existing(uint blocks, uint inodes)
{
	int enabled;
	struct resource_request requests[2];
	uint count = 0;
	int result;

	if (!fs_storage.ready)
		return -1;
	if (blocks != 0) {
		requests[count].kind = RESOURCE_FS_BLOCK;
		requests[count++].amount = blocks;
	}
	if (inodes != 0) {
		requests[count].kind = RESOURCE_FS_INODE;
		requests[count++].amount = inodes;
	}
	if (count == 0)
		return 0;
	enabled = intr_save();
	result = resource_transfer_usage(
		fs_system_account, RESOURCE_CHARGE_RESERVED,
		fs_public_account, RESOURCE_CHARGE_ORDINARY,
		requests, count);
	intr_restore(enabled);
	return result;
}

static struct bio_checkpoint_result fs_claim_checkpoint(int transfer)
{
	struct bio_checkpoint_result status;

	// Once qmap-first publication starts, rollback is neither crash-safe nor
	// quota-safe. The cleanup checkpoint defers thread exit until the bounded
	// forward commit reaches the inode publication point.
	if (transfer) {
		if (bio_request_settle_quiescent_cleanup() < 0)
			panic("storage claim commit checkpoint");
		return bio_checkpoint_make(BIO_CHECKPOINT_READY);
	}
	status = bio_request_checkpoint_quiescent();
	return status;
}

// Return the number of SYSTEM-owned blocks that still need conversion.  The
// indirect map block is storage too and is charged together with its entries.
// Entries are sorted and processed one qmap block at a time, so each physical
// metadata block is read/written once per pass. Every checkpoint runs after
// brelse(); the sleepable claim gate deliberately retains the unique quota
// reservation across the forward-only transfer pass.
static int fs_claim_inode_blocks(int dev, const uint addrs[NDIRECT + 1],
				 int transfer, int *public_seen,
				 uint *system_count)
{
	uint count = 0;
	uint block_count = 0;
	struct buf *bp;
	uint *owners;

	for (uint i = 0; i < NDIRECT; i++) {
		if (addrs[i] != 0) {
			if (addrs[i] < sb.datastart || addrs[i] >= sb.size)
				return -1;
			fs_claim_blocks[block_count++] = addrs[i];
		}
	}
	if (addrs[NDIRECT] != 0) {
		if (addrs[NDIRECT] < sb.datastart ||
		    addrs[NDIRECT] >= sb.size)
			return -1;
		fs_claim_blocks[block_count++] = addrs[NDIRECT];
		if (fs_read_block(dev, addrs[NDIRECT], &bp) < 0)
			return -1;
		for (uint i = 0; i < NINDIRECT; i++) {
			uint block = ((uint *)bp->data)[i];

			if (block != 0) {
				if (block < sb.datastart || block >= sb.size) {
					brelse(bp);
					return -1;
				}
				fs_claim_blocks[block_count++] = block;
			}
		}
		brelse(bp);
		struct bio_checkpoint_result checkpoint =
			fs_claim_checkpoint(transfer);
		if (bio_checkpoint_should_stop(checkpoint))
			return checkpoint.state == BIO_CHECKPOINT_DEFERRED ?
				VIRTIO_DISK_ERR_BUSY : -1;
	}
	if (block_count > MAXFILE + 1)
		return -1;

	// Allocation is normally sequential, making insertion sort linear in the
	// common case while keeping the fixed workspace and implementation small.
	for (uint i = 1; i < block_count; i++) {
		uint block = fs_claim_blocks[i];
		uint j = i;

		while (j != 0 && fs_claim_blocks[j - 1] > block) {
			fs_claim_blocks[j] = fs_claim_blocks[j - 1];
			j--;
		}
		fs_claim_blocks[j] = block;
	}
	for (uint i = 0; i < block_count; i++) {
		if (fs_claim_blocks[i] < sb.datastart ||
		    fs_claim_blocks[i] >= sb.size)
			return -1;
		if (i != 0 && fs_claim_blocks[i] == fs_claim_blocks[i - 1])
			return -1;
	}

	for (uint base = 0; base < block_count;) {
		uint qblock = QBLOCK(fs_claim_blocks[base], sb);
		uint end = base + 1;
		int dirty = 0;

		while (end < block_count &&
		       QBLOCK(fs_claim_blocks[end], sb) == qblock)
			end++;
		if (fs_read_block(dev, qblock, &bp) < 0)
			return -1;
		owners = (uint *)bp->data;
		for (uint i = base; i < end; i++) {
			uint block = fs_claim_blocks[i];
			uint owner = owners[block % QPB];

			if (owner == FS_OWNER_PUBLIC) {
				if (public_seen)
					*public_seen = 1;
				continue;
			}
			if (owner != FS_OWNER_SYSTEM) {
				brelse(bp);
				return -1;
			}
			count++;
			if (transfer) {
				owners[block % QPB] = FS_OWNER_PUBLIC;
				dirty = 1;
				if (public_seen)
					*public_seen = 1;
			}
		}
		if (dirty && fs_write_metadata_block(bp) < 0) {
			brelse(bp);
			return -1;
		}
		brelse(bp);
		if (dirty && fs_durable_barrier_forward() < 0)
			return -1;
		struct bio_checkpoint_result checkpoint =
			fs_claim_checkpoint(transfer);
		if (bio_checkpoint_should_stop(checkpoint))
			return checkpoint.state == BIO_CHECKPOINT_DEFERRED ?
				VIRTIO_DISK_ERR_BUSY : -1;
		base = end;
	}
	*system_count = count;
	return 0;
}

static int fs_dinode_is_mutable_public(const struct dinode *dip, uint inum)
{
	return dip->type == T_FILE && dip->vfs_magic == VFS_LABEL_MAGIC &&
	       dip->vfs_version == VFS_LABEL_VERSION &&
	       dip->vfs_flags == VFS_LABEL_F_PUBLIC &&
	       dip->vfs_scope_id == VFS_SCOPE_NONE &&
	       dip->vfs_policy == VFS_POLICY_PUBLIC &&
	       dip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE &&
	       dip->vfs_policy_generation == VFS_POLICY_GENERATION &&
	       dip->vfs_incarnation != 0 &&
	       (dip->exec_flags & EXEC_FLAG_IMMUTABLE) == 0 &&
	       dip->vfs_checksum == vfs_label_checksum(
		       inum, dip->vfs_magic, dip->vfs_version, dip->vfs_flags,
		       dip->vfs_scope_id, dip->vfs_policy,
		       dip->vfs_exec_profile, dip->vfs_policy_generation,
		       dip->vfs_incarnation, dip->fs_owner_domain,
		       dip->fs_owner_version);
}

// A crash during a claim can leave PUBLIC qmap entries below a still-SYSTEM
// inode.  qmap-first ordering makes that state unambiguous and forward-only.
static int fs_recover_public_claims(int dev)
{
	for (uint inum = 1; inum < sb.ninodes; inum++) {
		struct buf *bp;
		struct dinode *dip;
		uint system_blocks;
		uint converted_blocks;
		int public_seen = 0;

		if (fs_read_block(dev, IBLOCK(inum, sb), &bp) < 0)
			return -1;
		dip = (struct dinode *)bp->data + inum % IPB;
		if (dip->type != T_FILE ||
		    (dip->fs_owner_domain != FS_OWNER_SYSTEM &&
		     dip->fs_owner_domain != FS_OWNER_PUBLIC)) {
			brelse(bp);
			continue;
		}
		if (fs_claim_inode_blocks(dev, dip->addrs, 0, &public_seen,
					  &system_blocks) < 0) {
			brelse(bp);
			return -1;
		}
		if (dip->fs_owner_domain == FS_OWNER_PUBLIC) {
			if (system_blocks != 0) {
				brelse(bp);
				return -1;
			}
			brelse(bp);
			continue;
		}
		if (!public_seen) {
			brelse(bp);
			continue;
		}
		if (dip->fs_owner_version != FS_OWNER_VERSION ||
		    !fs_dinode_is_mutable_public(dip, inum)) {
			brelse(bp);
			return -1;
		}
		if (fs_claim_inode_blocks(dev, dip->addrs, 1, 0,
					  &converted_blocks) < 0) {
			brelse(bp);
			return -1;
		}
		if (converted_blocks != system_blocks) {
			brelse(bp);
			return -1;
		}
		dip->fs_owner_domain = FS_OWNER_PUBLIC;
		dip->vfs_checksum = vfs_label_checksum(
			inum, dip->vfs_magic, dip->vfs_version, dip->vfs_flags,
			dip->vfs_scope_id, dip->vfs_policy,
			dip->vfs_exec_profile, dip->vfs_policy_generation,
			dip->vfs_incarnation, dip->fs_owner_domain,
			dip->fs_owner_version);
		if (fs_write_inode_block(bp) < 0) {
			brelse(bp);
			return -1;
		}
		brelse(bp);
	}
	return 0;
}

static int fs_storage_charge_from_owner(uint owner,
					struct fs_storage_charge *charge)
{
	if (charge == 0)
		return -1;
	if (owner == FS_OWNER_SYSTEM) {
		charge->owner = owner;
		charge->level = FS_CHARGE_SYSTEM;
		return 0;
	}
	if (owner == FS_OWNER_PUBLIC) {
		charge->owner = owner;
		charge->level = FS_CHARGE_PUBLIC;
		return 0;
	}
	if (FS_OWNER_IS_SCOPE(owner) &&
	    FS_OWNER_SCOPE_ID(owner) >= VFS_SCOPE_FIRST_DYNAMIC) {
		charge->owner = owner;
		charge->level = FS_CHARGE_WORKFLOW;
		return 0;
	}
	return -1;
}

// Mutable PUBLIC files installed by mkfs begin as SYSTEM-sponsored objects.
// Their first user mutation atomically adopts the whole persistent object so
// overwriting existing blocks cannot bypass the stable PUBLIC quota.
static int fs_claim_sponsored_public_inode(struct inode *ip,
					   const struct vfs_cred *cred)
{
	struct fs_storage_charge caller;
	uint missing_blocks;
	uint converted_blocks;
	int result = -1;

	if (ip == 0 || cred == 0)
		return -1;
	if (cred->kernel || ip->type != T_FILE ||
	    ip->vfs_policy != VFS_POLICY_PUBLIC)
		return 0;
	if (fs_storage_charge_from_vfs(cred, &caller) < 0 ||
	    caller.level != FS_CHARGE_PUBLIC)
		return -1;
	if (ip->fs_owner_domain == FS_OWNER_PUBLIC)
		return 0;
	if (ip->fs_owner_domain != FS_OWNER_SYSTEM ||
	    !vfs_inode_label_valid(ip) || !exec_policy_inode_mutable(ip))
		return -1;
	if (fs_claim_gate_lock() < 0)
		return -1;
	if (ip->fs_owner_domain == FS_OWNER_PUBLIC) {
		result = 0;
		goto out;
	}
	if (ip->fs_owner_domain != FS_OWNER_SYSTEM ||
	    !vfs_inode_label_valid(ip) || !exec_policy_inode_mutable(ip))
		goto out;
	if (fs_claim_inode_blocks(ip->dev, ip->addrs, 0, 0,
				  &missing_blocks) < 0)
		goto out;
	if (fs_storage_claim_public_existing(missing_blocks, 1) < 0)
		goto out;
	if (fs_claim_inode_blocks(ip->dev, ip->addrs, 1, 0,
				  &converted_blocks) < 0)
		goto out;
	if (converted_blocks != missing_blocks)
		panic("PUBLIC claim changed beneath scanner");
	ip->fs_owner_domain = FS_OWNER_PUBLIC;
	ip->vfs_checksum = vfs_label_checksum(
		ip->inum, ip->vfs_magic, ip->vfs_version, ip->vfs_flags,
		ip->vfs_scope_id, ip->vfs_policy, ip->vfs_exec_profile,
		ip->vfs_policy_generation, ip->vfs_incarnation,
		ip->fs_owner_domain, ip->fs_owner_version);
	if (iupdate(ip) < 0)
		goto out;
	if (fs_durable_barrier_forward() < 0)
		goto out;
	if (bio_request_settle_quiescent_cleanup() < 0)
		panic("storage claim inode checkpoint");
	result = 0;
out:
	fs_claim_gate_unlock();
	return result;
}

static int fs_dinode_has_scope_owner(int dev, uint inum)
{
	struct buf *bp;
	struct dinode *dip;
	int owned;
	int result;

	if (inum == 0 || inum >= sb.ninodes)
		return 0;
	for (;;) {
		result = fs_read_block(dev, IBLOCK(inum, sb), &bp);
		if (result != VIRTIO_DISK_ERR_BUSY)
			break;
		if (fs_forward_checkpoint() < 0)
			return -1;
	}
	if (result < 0)
		return result;
	dip = (struct dinode *)bp->data + inum % IPB;
	owned = dip->type != 0 && dip->fs_owner_version == FS_OWNER_VERSION &&
		FS_OWNER_IS_SCOPE(dip->fs_owner_domain) &&
		FS_OWNER_SCOPE_ID(dip->fs_owner_domain) >=
			VFS_SCOPE_FIRST_DYNAMIC;
	brelse(bp);
	return owned;
}

static int fs_reap_scope_inode_forward(int dev, uint inum, int *changed)
{
	for (;;) {
		struct buf *bp;
		struct dinode *dip;
		int result;

		result = fs_read_block(dev, IBLOCK(inum, sb), &bp);
		if (result == VIRTIO_DISK_ERR_BUSY) {
			if (fs_forward_checkpoint() < 0)
				return -1;
			continue;
		}
		if (result < 0)
			return result;
		dip = (struct dinode *)bp->data + inum % IPB;
		if (dip->type == 0 || !fs_storage_owner_valid(
					 dip->fs_owner_domain) ||
		    !FS_OWNER_IS_SCOPE(dip->fs_owner_domain)) {
			brelse(bp);
			return 0;
		}
		fs_scrub_retire_dinode(dip, inum);
		result = fs_write_inode_block(bp);
		brelse(bp);
		if (result == VIRTIO_DISK_ERR_BUSY) {
			if (fs_forward_checkpoint() < 0)
				return -1;
			continue;
		}
		if (result < 0)
			return result;
		if (changed)
			*changed = 1;
		return 0;
	}
}

// Dynamic workflow namespaces are boot leases. No persistent recovery token
// exists yet, so a reboot revokes every old lease before a new workflow can
// be admitted. The three passes are idempotent across power loss: names are
// detached first, dinodes are then retired, and tagged orphan blocks last.
static int fs_reap_boot_workflow_objects(int dev)
{
	struct fs_storage_charge system_charge = {
		.owner = FS_OWNER_SYSTEM,
		.level = FS_CHARGE_SYSTEM,
	};
	struct vfs_cred kernel_cred;
	struct inode *root;
	struct dirent de;
	int names_changed = 0;
	int inodes_changed = 0;
	int root_status;

	vfs_cred_kernel(&kernel_cred);
	root = root_dir_status(&root_status);
	if (root == 0 || root_status != FS_LOOKUP_FOUND) {
		if (root)
			iput(root);
		return root_status < 0 ? root_status :
					 fs_io_fail(FS_FAILURE_OPERATION);
	}
	for (uint64 off = 0; off < root->size; off += sizeof(de)) {
		int owned;
		int io_result;

		for (;;) {
			io_result = readi(root, &kernel_cred, 0,
					  (uint64)&de, off, sizeof(de));
			if (io_result == sizeof(de))
				break;
			if (io_result != VIRTIO_DISK_ERR_BUSY ||
			    fs_forward_checkpoint() < 0) {
				iput(root);
				return fs_io_fail(FS_FAILURE_TRANSIENT_READ);
			}
		}
		if (io_result != sizeof(de)) {
			iput(root);
			return fs_io_fail(FS_FAILURE_TRANSIENT_READ);
		}
		if (de.inum == 0)
			continue;
		owned = fs_dinode_has_scope_owner(dev, de.inum);
		if (owned < 0) {
			iput(root);
			return -1;
		}
		if (!owned)
			continue;
		memset(&de, 0, sizeof(de));
		if (fs_namespace_gate_lock() < 0) {
			iput(root);
			return -1;
		}
		for (;;) {
			io_result = writei_charged(
				root, &kernel_cred, &system_charge, 0,
				(uint64)&de, off, sizeof(de),
				FS_EPOCH_NAMESPACE_DETACH);
			if (io_result == sizeof(de))
				break;
			if (io_result != VIRTIO_DISK_ERR_BUSY ||
			    fs_forward_checkpoint() < 0) {
				fs_dentry_index_invalidate_directory(root);
				fs_namespace_gate_unlock();
				iput(root);
				return io_result < 0 ? io_result : -1;
			}
		}
		fs_dentry_index_invalidate_directory(root);
		fs_namespace_gate_unlock();
		names_changed = 1;
	}
	iput(root);
	if (names_changed && fs_durable_barrier_forward() < 0)
		return -1;

	for (uint inum = 1; inum < sb.ninodes; inum++) {
		if (fs_reap_scope_inode_forward(
			    dev, inum, &inodes_changed) < 0)
			return -1;
	}
	if (inodes_changed && fs_durable_barrier_forward() < 0)
		return -1;

	for (uint block = sb.datastart; block < sb.size; block++) {
		uint owner;
		uint freeing;
		int allocated;

		if (fs_qmap_read(dev, block, &owner) < 0)
			return -1;
		if (!fs_storage_owner_valid(owner) || !FS_OWNER_IS_SCOPE(owner))
			continue;
		freeing = fs_qmap_transition(FS_QMAP_FREEING_FLAG, owner);
		if (freeing == FS_OWNER_NONE ||
		    fs_qmap_write_forward(dev, block, freeing) < 0)
			return -1;
		if (fs_scrub_block_allocated(dev, block, &allocated) < 0)
			return -1;
		if (allocated && fs_bitmap_write_forward(dev, block, 0) < 0)
			return -1;
		if (fs_qmap_write_forward(dev, block, FS_OWNER_NONE) < 0)
			return -1;
	}
	return 0;
}

// Init fs
void fsinit()
{
	int dev = ROOTDEV;

	fs_epoch_init();
	memset(fs_block_magazines, 0, sizeof(fs_block_magazines));
	memset(fs_block_candidate_reserved, 0,
	       sizeof(fs_block_candidate_reserved));
	memset(fs_deferred_reclaims, 0, sizeof(fs_deferred_reclaims));
	memset(fs_deferred_reclaim_plan, 0,
	       sizeof(fs_deferred_reclaim_plan));
	fs_deferred_reclaim_count = 0;
	fs_deferred_reclaim_cursor = 0;
	memset(fs_dentry_index, 0, sizeof(fs_dentry_index));
	memset(fs_directory_indexes, 0, sizeof(fs_directory_indexes));
	fs_dentry_index_used = 0;
	fs_dentry_index_tombstones = 0;
	fs_directory_index_cursor = 0;
	fs_dentry_index_generation = 1;
	fs_io_health = FS_IO_HEALTHY;
	fs_claim_owner = 0;
	fs_allocator_owner = 0;
	fs_namespace_owner = 0;
	fs_namespace_owner_generation = 0;
	fs_dentry_owner = 0;
	fs_dentry_owner_generation = 0;
	memset(inode_mapping_guards, 0, sizeof(inode_mapping_guards));
	wait_queue_init(&fs_claim_waiters, WAIT_REASON_FS_CLAIM);
	wait_queue_init(&fs_allocator_waiters, WAIT_REASON_FS_CLAIM);
	wait_queue_init(&fs_namespace_waiters, WAIT_REASON_FS_CLAIM);
	wait_queue_init(&fs_dentry_waiters, WAIT_REASON_FS_CLAIM);
	wait_queue_init(&inode_mapping_waiters, WAIT_REASON_FS_CLAIM);
	if (readsb(dev, &sb) < 0) {
		(void)fs_io_fail(FS_FAILURE_MOUNT_UNAVAILABLE);
		return;
	}
	if (!fs_layout_valid()) {
		(void)fs_io_fail(FS_FAILURE_MOUNT_UNAVAILABLE);
		return;
	}
	if (fs_mount_scrub(dev) < 0 || fs_recover_public_claims(dev) < 0 ||
	    fs_storage_rebuild(dev, 0) < 0 ||
	    fs_reap_boot_workflow_objects(dev) < 0 ||
	    fs_storage_rebuild(dev, 1) < 0)
		(void)fs_io_fail(FS_FAILURE_MOUNT_UNAVAILABLE);
	if (fs_io_health == FS_IO_HEALTHY) {
		struct inode *root;
		int status;
		int index_result = -1;

		root = root_dir_status(&status);
		if (root != 0 && status == FS_LOOKUP_FOUND &&
		    fs_dentry_gate_lock() == 0) {
			index_result = fs_directory_index_rebuild(root);
			fs_dentry_gate_unlock();
		}
		if (root == 0 || status != FS_LOOKUP_FOUND || index_result < 0)
			(void)fs_io_fail(FS_FAILURE_MOUNT_UNAVAILABLE);
		if (root != 0)
			iput(root);
	}
}

// Zero a block.
static int bzero(int dev, int bno)
{
	struct bio_overwrite_receipt overwrite = BIO_OVERWRITE_RECEIPT_INIT;
	struct buf *bp = 0;
	int result;

	result = bprepare_overwrite(dev, bno, &overwrite);
	if (result == BIO_OVERWRITE_FALLBACK) {
		result = fs_read_block(dev, bno, &bp);
	} else if (result == VIRTIO_DISK_OK) {
		bp = overwrite.buf;
	} else {
		bcancel_overwrite(&overwrite);
		return fs_io_fail(result == VIRTIO_DISK_ERR_BUSY ?
				      FS_FAILURE_SCHEDULING_UNAVAILABLE :
				      FS_FAILURE_TRANSIENT_READ);
	}
	if (result < 0)
		return result;
	result = bclaim(bp);
	if (result < 0) {
		if (overwrite.active)
			bcancel_overwrite(&overwrite);
		else
			brelse(bp);
		return result;
	}
	memset(bp->data, 0, BSIZE);
	if (overwrite.active) {
		result = bpublish_overwrite(&overwrite, BSIZE, &bp);
		if (result == VIRTIO_DISK_ERR_BUSY) {
			bcancel_overwrite(&overwrite);
			result = fs_read_block(dev, bno, &bp);
			if (result < 0)
				return result;
			result = bclaim(bp);
			if (result < 0) {
				brelse(bp);
				return result;
			}
			memset(bp->data, 0, BSIZE);
		} else if (result < 0) {
			bcancel_overwrite(&overwrite);
			return fs_io_fail(FS_FAILURE_TRANSIENT_READ);
		}
	}
	result = fs_write_data_block(bp);
	if (result < 0) {
		brelse(bp);
		return result;
	}
	brelse(bp);
	return 0;
}

static int fs_allocator_gate_lock(int cleanup)
{
	struct thread *self = curr_thread();
	int enabled;

	if (self == 0)
		return -1;
	enabled = intr_save();
	for (;;) {
		if (fs_allocator_owner == 0) {
			fs_allocator_owner = self;
			intr_restore(enabled);
			return 0;
		}
		if (fs_allocator_owner == self)
			panic("filesystem allocator gate recursion");
		if (cleanup) {
			(void)wait_queue_sleep_irq_uninterruptible(
				&fs_allocator_waiters);
			continue;
		}
		if (wait_queue_sleep_irq(&fs_allocator_waiters) !=
		    WAIT_QUEUE_OK) {
			intr_restore(enabled);
			return -1;
		}
	}
}

static void fs_allocator_gate_unlock(void)
{
	struct thread *self = curr_thread();
	int enabled = intr_save();

	if (self == 0 || fs_allocator_owner != self)
		panic("filesystem allocator gate owner");
	fs_allocator_owner = 0;
	wait_queue_wake_all(&fs_allocator_waiters);
	intr_restore(enabled);
}

static int fs_qmap_write_forward(int dev, uint block, uint state)
{
	int result;

	for (;;) {
		result = fs_qmap_write(dev, block, state);
		if (result != VIRTIO_DISK_ERR_BUSY)
			break;
		if (fs_forward_checkpoint() < 0)
			return -1;
	}
	if (result < 0)
		return result;
	return fs_durable_barrier_forward();
}

static int fs_bitmap_write_forward(int dev, uint block, int allocated)
{
	int result;

	for (;;) {
		result = fs_bitmap_write(dev, block, allocated);
		if (result != VIRTIO_DISK_ERR_BUSY)
			break;
		if (fs_forward_checkpoint() < 0)
			return -1;
	}
	if (result < 0)
		return result;
	return fs_durable_barrier_forward();
}

// Blocks.

// Allocate a zeroed disk block through the recoverable qmap state machine.
static uint balloc_one(uint dev, const struct fs_storage_charge *charge,
		       int *error)
{
	uint b, bi, block = 0, first, limit, range_start, range_end;
	uint cursor;
	uint intent;
	uint qstate;
	struct bio_checkpoint_result checkpoint;
	int m, pass;
	int reserved = 0;
	int result = -1;
	struct buf *bp;

	if (error)
		*error = -1;
	if (charge == 0 || fs_epoch_preflight(3) < 0 ||
	    fs_allocator_gate_lock(0) < 0)
		return 0;
	if (fs_storage_reserve(charge, 0) < 0)
		goto out;
	reserved = 1;
	cursor = fs_storage.block_alloc_cursor;
	if (cursor < sb.datastart || cursor >= sb.size)
		cursor = sb.datastart;
	for (pass = 0; pass < 2 && block == 0; pass++) {
		range_start = pass == 0 ? cursor : sb.datastart;
		range_end = pass == 0 ? sb.size : cursor;
		if (range_start >= range_end)
			continue;
		for (b = range_start - range_start % BPB;
		     b < range_end && block == 0; b += BPB) {
			first = range_start > b ? range_start - b : 0;
			if (b + first < sb.datastart)
				first = sb.datastart - b;
			limit = MIN(BPB, range_end - b);
			result = fs_read_block(dev, BBLOCK(b, sb), &bp);
			if (result < 0)
				goto out;
			for (bi = first; bi < limit; bi++) {
				m = 1 << (bi % 8);
				if ((bp->data[bi / 8] & m) == 0) {
					if (fs_block_candidate_is_reserved(b + bi) &&
					    fs_block_candidate_reclaim(b + bi) < 0)
						panic("filesystem candidate index");
					block = b + bi;
					break;
				}
			}
			brelse(bp);
			fs_storage.block_alloc_cursor = b + limit;
			if (fs_storage.block_alloc_cursor >= sb.size)
				fs_storage.block_alloc_cursor = sb.datastart;
			if (block != 0)
				break;
			checkpoint = bio_request_checkpoint();
			if (bio_checkpoint_should_stop(checkpoint)) {
				result = checkpoint.state == BIO_CHECKPOINT_DEFERRED ?
					VIRTIO_DISK_ERR_BUSY : -1;
				goto out;
			}
		}
	}
	if (block == 0)
		goto out;
	result = fs_qmap_read(dev, block, &qstate);
	if (result < 0)
		goto out;
	if (qstate != FS_OWNER_NONE) {
		result = fs_io_fail(FS_FAILURE_METADATA_WRITE_INDETERMINATE);
		goto out;
	}

	/* Zero is durable before any allocation metadata can become reachable. */
	result = bzero(dev, block);
	if (result < 0)
		goto out;
	result = fs_durable_barrier();
	if (result < 0)
		goto out;

	intent = fs_qmap_transition(FS_QMAP_ALLOCATING_FLAG, charge->owner);
	if (intent == FS_OWNER_NONE) {
		result = fs_io_fail(FS_FAILURE_OPERATION);
		goto out;
	}
	/* Before intent publication a pre-submit BUSY is fully abortable. */
	result = fs_allocator_fault_before(
		FSALLOC_OP_ALLOC, FSALLOC_PHASE_INTENT, 0);
	if (result < 0)
		goto out;
	result = fs_qmap_write(dev, block, intent);
	if (result < 0)
		goto out;
#ifdef FS_ALLOCATOR_DELETE_BARRIER_MUTANT
	/* Negative acceptance profile: the volatile overlay must expose this. */
	result = 0;
#else
	result = fs_durable_barrier_forward();
#endif
	if (result < 0)
		goto out;
	FS_ALLOCATOR_FAULT_AFTER(FSALLOC_OP_ALLOC, FSALLOC_PHASE_INTENT);
	result = fs_allocator_fault_before(
		FSALLOC_OP_ALLOC, FSALLOC_PHASE_BITMAP, 1);
	if (result < 0)
		goto out;
	result = fs_bitmap_write_forward(dev, block, 1);
	if (result < 0)
		goto out;
	FS_ALLOCATOR_FAULT_AFTER(FSALLOC_OP_ALLOC, FSALLOC_PHASE_BITMAP);
	result = fs_allocator_fault_before(
		FSALLOC_OP_ALLOC, FSALLOC_PHASE_OWNER, 1);
	if (result < 0)
		goto out;
	result = fs_qmap_write_forward(dev, block, charge->owner);
	if (result < 0)
		goto out;
	FS_ALLOCATOR_FAULT_AFTER(FSALLOC_OP_ALLOC, FSALLOC_PHASE_OWNER);

	reserved = 0;
	fs_storage.block_alloc_cursor = block + 1;
	if (fs_storage.block_alloc_cursor >= sb.size)
		fs_storage.block_alloc_cursor = sb.datastart;
	if (error)
		*error = 0;
	fs_allocator_gate_unlock();
	return block;

out:
	if (reserved && fs_storage.ready)
		fs_storage_release(charge->owner, 0);
	if (error)
		*error = result;
	fs_allocator_gate_unlock();
	return 0;
}

static struct fs_block_magazine *
fs_block_magazine_find(uint owner, int create)
{
	struct fs_block_magazine *free_slot = 0;

	for (uint i = 0; i < FS_BLOCK_MAGAZINE_SLOTS; i++) {
		struct fs_block_magazine *magazine = &fs_block_magazines[i];

		if (magazine->owner == owner)
			return magazine;
		if (magazine->owner == FS_OWNER_NONE && free_slot == 0)
			free_slot = magazine;
	}
	if (!create || free_slot == 0)
		return 0;
	free_slot->owner = owner;
	return free_slot;
}

static int fs_block_candidate_is_reserved(uint block)
{
	if (block >= FSSIZE)
		return 1;
	return (fs_block_candidate_reserved[block / 8] &
		(1U << (block % 8))) != 0;
}

static void fs_block_candidate_set(uint block)
{
	if (block < sb.datastart || block >= sb.size || block >= FSSIZE ||
	    fs_block_candidate_is_reserved(block))
		panic("filesystem block candidate reserve");
	fs_block_candidate_reserved[block / 8] |= 1U << (block % 8);
}

static void fs_block_candidate_clear(uint block)
{
	if (block < sb.datastart || block >= sb.size || block >= FSSIZE ||
	    !fs_block_candidate_is_reserved(block))
		panic("filesystem block candidate release");
	fs_block_candidate_reserved[block / 8] &= ~(1U << (block % 8));
}

static int fs_block_candidate_reclaim(uint block)
{
	if (!fs_block_candidate_is_reserved(block))
		return 0;
	for (uint i = 0; i < FS_BLOCK_MAGAZINE_SLOTS; i++) {
		struct fs_block_magazine *magazine = &fs_block_magazines[i];

		for (uint j = 0; j < magazine->count; j++) {
			if (magazine->blocks[j] != block)
				continue;
			magazine->blocks[j] =
				magazine->blocks[--magazine->count];
			fs_block_candidate_clear(block);
			return 0;
		}
	}
	return -1;
}

static int fs_block_candidate_transfer(uint owner)
{
	struct fs_block_magazine *target =
		fs_block_magazine_find(owner, 0);

	if (target == 0 || target->count != 0)
		return -1;
	for (uint i = 0; i < FS_BLOCK_MAGAZINE_SLOTS; i++) {
		struct fs_block_magazine *donor = &fs_block_magazines[i];

		if (donor == target || donor->count == 0)
			continue;
		target->blocks[target->count++] =
			donor->blocks[--donor->count];
		return 0;
	}
	return -1;
}

static int fs_block_candidate_refill(int dev, uint owner)
{
	struct fs_block_magazine *magazine;
	struct bio_checkpoint_result checkpoint;
	struct buf *bp;
	uint cursor;
	uint range_end;
	uint range_start;
	int pass;
	int result = -1;

	magazine = fs_block_magazine_find(owner, 1);
	if (magazine == 0 || magazine->count != 0)
		return -1;
	cursor = fs_storage.block_alloc_cursor;
	if (cursor < sb.datastart || cursor >= sb.size)
		cursor = sb.datastart;
	for (pass = 0; pass < 2 && magazine->count == 0; pass++) {
		range_start = pass == 0 ? cursor : sb.datastart;
		range_end = pass == 0 ? sb.size : cursor;
		for (uint base = range_start - range_start % BPB;
		     base < range_end &&
		     magazine->count < FS_BLOCK_MAGAZINE_CAP; base += BPB) {
			uint first = range_start > base ? range_start - base : 0;
			uint limit = MIN(BPB, range_end - base);

			if (base + first < sb.datastart)
				first = sb.datastart - base;
			result = fs_read_block(dev, BBLOCK(base, sb), &bp);
			if (result < 0)
				goto out;
			for (uint bit = first;
			     bit < limit &&
			     magazine->count < FS_BLOCK_MAGAZINE_CAP; bit++) {
				uint block = base + bit;

				if ((bp->data[bit / 8] &
				     (1U << (bit % 8))) != 0 ||
				    fs_block_candidate_is_reserved(block))
					continue;
				fs_block_candidate_set(block);
				magazine->blocks[magazine->count++] = block;
			}
			brelse(bp);
			fs_storage.block_alloc_cursor = base + limit;
			if (fs_storage.block_alloc_cursor >= sb.size)
				fs_storage.block_alloc_cursor = sb.datastart;
			checkpoint = bio_request_checkpoint();
			if (bio_checkpoint_should_stop(checkpoint)) {
				result = checkpoint.state ==
					BIO_CHECKPOINT_DEFERRED ?
						VIRTIO_DISK_ERR_BUSY : -1;
				goto out;
			}
		}
	}
out:
	if (magazine->count != 0)
		return 0;
	if (result != VIRTIO_DISK_ERR_BUSY &&
	    fs_block_candidate_transfer(owner) == 0)
		return 0;
	magazine->owner = FS_OWNER_NONE;
	return result;
}

static uint fs_block_candidate_take(int dev, uint owner, int *result)
{
	struct fs_block_magazine *magazine =
		fs_block_magazine_find(owner, 0);
	int refill_result;

	if (magazine == 0 || magazine->count == 0) {
		refill_result = fs_block_candidate_refill(dev, owner);
		if (refill_result >= 0)
			goto ready;
		if (result != 0)
			*result = refill_result;
		return 0;
	}
ready:
	magazine = fs_block_magazine_find(owner, 0);
	if (magazine == 0 || magazine->count == 0)
		panic("filesystem candidate cache empty");
	if (result != 0)
		*result = 0;
	return magazine->blocks[--magazine->count];
}

static uint balloc_epoch(int dev, const struct fs_storage_charge *charge,
			 int *error)
{
	uint block = 0;
	uint qstate = FS_OWNER_NONE;
	int allocated = 0;
	int epoch_staged = 0;
	int mapping_staged = 0;
	int quota_reserved = 0;
	int result = -1;

	if (error != 0)
		*error = -1;
	if (charge == 0 || fs_epoch_preflight(3) < 0 ||
	    fs_allocator_gate_lock(0) < 0)
		return 0;
	block = fs_block_candidate_take(dev, charge->owner, &result);
	if (block == 0)
		goto out;
	result = fs_storage_reserve(charge, 0);
	if (result < 0)
		goto out;
	quota_reserved = 1;
	result = fs_scrub_block_allocated(dev, block, &allocated);
	if (result < 0)
		goto out;
	result = fs_qmap_read(dev, block, &qstate);
	if (result < 0)
		goto out;
	if (allocated || qstate != FS_OWNER_NONE) {
		result = fs_io_fail(FS_FAILURE_METADATA_WRITE_INDETERMINATE);
		goto out;
	}
	result = bzero(dev, block);
	if (result < 0)
		goto out;
	epoch_staged = 1;
	result = fs_qmap_write(dev, block, charge->owner);
	if (result < 0)
		goto out;
	mapping_staged = 1;
	result = fs_bitmap_write(dev, block, 1);
	if (result < 0) {
		result = fs_io_fail(FS_FAILURE_METADATA_WRITE_INDETERMINATE);
		goto out;
	}
	quota_reserved = 0;
	fs_block_candidate_clear(block);
	if (error != 0)
		*error = 0;
	fs_allocator_gate_unlock();
	return block;

out:
	if (epoch_staged) {
		int commit_result = fs_epoch_commit();

		if (commit_result < 0 || mapping_staged) {
			/*
			 * A published allocation-map image owns its quota even when
			 * the remaining map update fails.  Recovery will reclaim the
			 * unreachable block; refunding here would let another caller
			 * spend capacity whose durable state is still ambiguous.
			 */
			quota_reserved = 0;
			result = fs_io_fail(
				FS_FAILURE_METADATA_WRITE_INDETERMINATE);
		}
	}
	if (block != 0 && fs_block_candidate_is_reserved(block))
		fs_block_candidate_clear(block);
	if (quota_reserved && fs_storage.ready)
		fs_storage_release(charge->owner, 0);
	if (error != 0)
		*error = result;
	fs_allocator_gate_unlock();
	return 0;
}

static uint balloc(uint dev, const struct fs_storage_charge *charge, int *error)
{
#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
	return balloc_one(dev, charge, error);
#else
	if (!fs_epoch_runtime_enabled() || fs_epoch_bypass_active())
		return balloc_one(dev, charge, error);
	return balloc_epoch(dev, charge, error);
#endif
}

// Free is idempotent and retains its owner in qmap until refund is safe.
static int bfree(int dev, uint block)
{
	uint freeing;
	uint owner = FS_OWNER_NONE;
	uint qstate;
	int allocated;
	int attempts = 0;
	int bypass_entered = 0;
	int result = -1;

	if (block < sb.datastart || block >= sb.size)
		return fs_io_fail(FS_FAILURE_OPERATION);
	if (fs_epoch_destructive_begin(&bypass_entered) < 0)
		return -1;
	if (fs_allocator_gate_lock(1) < 0)
		goto out_bypass;

	for (;;) {
		result = fs_scrub_block_allocated(dev, block, &allocated);
		if (result >= 0)
			break;
		if (!fs_storage.ready)
			goto out;
		if (++attempts >= 3) {
			result = fs_io_fail(FS_FAILURE_MOUNT_UNAVAILABLE);
			goto out;
		}
		if (fs_forward_checkpoint() < 0)
			goto out;
	}
	attempts = 0;
	for (;;) {
		result = fs_qmap_read(dev, block, &qstate);
		if (result >= 0)
			break;
		if (!fs_storage.ready)
			goto out;
		if (++attempts >= 3) {
			result = fs_io_fail(FS_FAILURE_MOUNT_UNAVAILABLE);
			goto out;
		}
		if (fs_forward_checkpoint() < 0)
			goto out;
	}
	if (!allocated && qstate == FS_OWNER_NONE) {
		result = 0;
		goto out;
	}
	if (fs_storage_owner_valid(qstate))
		owner = qstate;
	else if (fs_qmap_transition_owner(
			 qstate, FS_QMAP_ALLOCATING_FLAG, &owner) < 0 &&
		 fs_qmap_transition_owner(
			 qstate, FS_QMAP_FREEING_FLAG, &owner) < 0) {
		result = fs_io_fail(FS_FAILURE_METADATA_WRITE_INDETERMINATE);
		goto out;
	}
	freeing = fs_qmap_transition(FS_QMAP_FREEING_FLAG, owner);
	if (freeing == FS_OWNER_NONE) {
		result = fs_io_fail(FS_FAILURE_METADATA_WRITE_INDETERMINATE);
		goto out;
	}
	if (qstate != freeing) {
		result = fs_allocator_fault_before(
			FSALLOC_OP_FREE, FSALLOC_PHASE_INTENT, 1);
		if (result < 0)
			goto out;
		result = fs_qmap_write_forward(dev, block, freeing);
		if (result < 0)
			goto out;
		FS_ALLOCATOR_FAULT_AFTER(FSALLOC_OP_FREE,
					 FSALLOC_PHASE_INTENT);
	}
	if (allocated) {
		result = fs_allocator_fault_before(
			FSALLOC_OP_FREE, FSALLOC_PHASE_BITMAP, 1);
		if (result < 0)
			goto out;
		result = fs_bitmap_write_forward(dev, block, 0);
		if (result < 0)
			goto out;
		FS_ALLOCATOR_FAULT_AFTER(FSALLOC_OP_FREE,
					 FSALLOC_PHASE_BITMAP);
	}
	result = fs_allocator_fault_before(
		FSALLOC_OP_FREE, FSALLOC_PHASE_OWNER, 1);
	if (result < 0)
		goto out;
	result = fs_qmap_write_forward(dev, block, FS_OWNER_NONE);
	if (result < 0)
		goto out;
	FS_ALLOCATOR_FAULT_AFTER(FSALLOC_OP_FREE, FSALLOC_PHASE_OWNER);
	result = fs_allocator_fault_before(
		FSALLOC_OP_FREE, FSALLOC_PHASE_REFUND, 1);
	if (result < 0)
		goto out;
	fs_storage_release(owner, 0);
	FS_ALLOCATOR_FAULT_AFTER(FSALLOC_OP_FREE, FSALLOC_PHASE_REFUND);
	result = 0;
out:
	fs_allocator_gate_unlock();
out_bypass:
	fs_epoch_destructive_end(bypass_entered);
	return result;
}

#ifndef FS_ALLOCATOR_FAULT_TEST_PROFILE

_Static_assert(FS_DEFERRED_RECLAIM_UNIQUE_BUFFERS + 2U <=
		       BIO_CACHE_CLEANUP_CAP,
	       "deferred reclaim must leave cleanup cache working slots");
_Static_assert(FS_DEFERRED_RECLAIM_SYSTEM_RESERVE <
		       FS_DEFERRED_RECLAIM_CAP,
	       "deferred reclaim system reserve must be usable");

static uint
fs_deferred_reclaim_owner_count(uint sponsor_owner)
{
	uint count = 0;

	for (uint slot = 0; slot < FS_DEFERRED_RECLAIM_CAP; slot++) {
		struct fs_deferred_reclaim_entry *entry =
			&fs_deferred_reclaims[slot];

		if (entry->reserved && entry->sponsor_owner == sponsor_owner)
			count++;
	}
	return count;
}

static int
fs_deferred_reclaim_capacity_available(uint sponsor_owner)
{
	if (fs_deferred_reclaim_count >= FS_DEFERRED_RECLAIM_CAP)
		return 0;
	if (sponsor_owner != FS_OWNER_SYSTEM &&
	    fs_deferred_reclaim_count >=
		    FS_DEFERRED_RECLAIM_CAP -
			    FS_DEFERRED_RECLAIM_SYSTEM_RESERVE)
		return 0;
	return fs_deferred_reclaim_owner_count(sponsor_owner) <
	       FS_DEFERRED_RECLAIM_OWNER_CAP;
}

static int
fs_deferred_reclaim_reserve(uint sponsor_owner,
			    struct inode_reclaim *reclaim)
{
	uint sponsor_class;
	uint slot;

	if (reclaim == 0 || reclaim->deferred_reserved ||
	    sponsor_owner == FS_OWNER_NONE || !fs_epoch_request_held())
		return -1;
	while (!fs_deferred_reclaim_capacity_available(sponsor_owner)) {
		if (fs_epoch_dirty() && fs_epoch_commit() < 0)
			return -1;
		if (fs_deferred_reclaim_maintain_owner(
			    FS_OWNER_NONE, 0) <= 0)
			return -1;
	}
	if (bio_deferred_owner_retain(sponsor_owner, &sponsor_class) < 0 &&
	    bio_deferred_owner_retain_cleanup(sponsor_owner,
					      &sponsor_class) < 0)
		return -1;
	for (slot = 0; slot < FS_DEFERRED_RECLAIM_CAP; slot++)
		if (!fs_deferred_reclaims[slot].reserved)
			break;
	if (slot == FS_DEFERRED_RECLAIM_CAP)
		panic("deferred reclaim free slot");
	fs_deferred_reclaim_count++;
	memset(&fs_deferred_reclaims[slot], 0,
	       sizeof(fs_deferred_reclaims[slot]));
	fs_deferred_reclaims[slot].reserved = 1;
	fs_deferred_reclaims[slot].sponsor_owner = sponsor_owner;
	fs_deferred_reclaims[slot].sponsor_class = sponsor_class;
	reclaim->deferred_slot = slot;
	reclaim->deferred_reserved = 1;
	return 0;
}

static void
fs_deferred_reclaim_cancel(struct inode_reclaim *reclaim)
{
	struct fs_deferred_reclaim_entry *entry;
	uint sponsor_owner;

	if (reclaim == 0 || !reclaim->deferred_reserved)
		return;
	if (fs_deferred_reclaim_count == 0 ||
	    reclaim->deferred_slot >= FS_DEFERRED_RECLAIM_CAP)
		panic("deferred reclaim cancel underflow");
	entry = &fs_deferred_reclaims[reclaim->deferred_slot];
	if (!entry->reserved || entry->published)
		panic("deferred reclaim published cancel");
	sponsor_owner = entry->sponsor_owner;
	memset(entry, 0, sizeof(*entry));
	fs_deferred_reclaim_count--;
	reclaim->deferred_reserved = 0;
	reclaim->deferred_slot = 0;
	bio_deferred_owner_release(sponsor_owner);
}

static int
fs_deferred_reclaim_publish(struct inode_reclaim *reclaim)
{
	struct fs_deferred_reclaim_entry *entry;
	uint64 generation;

	if (reclaim == 0 || !reclaim->deferred_reserved ||
	    reclaim->deferred_slot >= FS_DEFERRED_RECLAIM_CAP)
		return -1;
	entry = &fs_deferred_reclaims[reclaim->deferred_slot];
	if (!entry->reserved || entry->published ||
	    fs_epoch_generation_fence(entry->sponsor_owner,
				      &generation) < 0)
		return -1;
	entry->reclaim = *reclaim;
	entry->reclaim.deferred_reserved = 0;
	entry->reclaim.deferred_slot = 0;
	entry->fence_generation = generation;
	entry->published = 1;
	memset(reclaim, 0, sizeof(*reclaim));
	agent_background_request();
	return 0;
}

static int
fs_deferred_reclaim_blocks_done(const struct inode_reclaim *reclaim)
{
	if (reclaim->mode == INODE_RECLAIM_DIRECT)
		return reclaim->direct_cursor >= NDIRECT &&
		       reclaim->indirect == 0;
	if (reclaim->mode == INODE_RECLAIM_LIST)
		return reclaim->block_cursor >= reclaim->block_count &&
		       reclaim->indirect == 0;
	return reclaim->mode == INODE_RECLAIM_NONE;
}

static int
fs_deferred_reclaim_next(struct inode_reclaim *reclaim,
			 struct fs_deferred_reclaim_action *action)
{
	memset(action, 0, sizeof(*action));
	if (reclaim->mode == INODE_RECLAIM_DIRECT) {
		if (reclaim->direct_cursor < NDIRECT) {
			uint cursor = reclaim->direct_cursor;

			while (cursor < NDIRECT &&
			       reclaim->direct[cursor] == 0)
				cursor++;
			action->block = cursor < NDIRECT ?
				reclaim->direct[cursor] : 0;
			action->advance = 1;
			action->cursor = cursor < NDIRECT ? cursor + 1 :
							      NDIRECT;
			return 1;
		}
		if (reclaim->indirect != 0 &&
		    reclaim->indirect_cursor < NINDIRECT) {
			struct buf *bp;

			if (fs_read_block(reclaim->dev, reclaim->indirect,
					  &bp) < 0)
				return -1;
			uint cursor = reclaim->indirect_cursor;
			uint *blocks = (uint *)bp->data;

			while (cursor < NINDIRECT && blocks[cursor] == 0)
				cursor++;
			action->block = cursor < NINDIRECT ? blocks[cursor] : 0;
			brelse(bp);
			action->advance = 2;
			action->cursor = cursor < NINDIRECT ? cursor + 1 :
								NINDIRECT;
			return 1;
		}
		if (reclaim->indirect != 0) {
			action->block = reclaim->indirect;
			action->advance = 3;
			return 1;
		}
	} else if (reclaim->mode == INODE_RECLAIM_LIST) {
		if (reclaim->block_cursor < reclaim->block_count) {
			uint cursor = reclaim->block_cursor;

			while (cursor < reclaim->block_count &&
			       reclaim->block_list[cursor] == 0)
				cursor++;
			action->block = cursor < reclaim->block_count ?
				reclaim->block_list[cursor] : 0;
			action->advance = 4;
			action->cursor = cursor < reclaim->block_count ?
					 cursor + 1 : reclaim->block_count;
			return 1;
		}
		if (reclaim->indirect != 0) {
			action->block = reclaim->indirect;
			action->advance = 3;
			return 1;
		}
	} else if (reclaim->mode != INODE_RECLAIM_NONE) {
		panic("deferred reclaim mode");
	}
	if (reclaim->release_inode) {
		action->inode = reclaim->inode;
		action->advance = 5;
		return 1;
	}
	return 0;
}

static void
fs_deferred_reclaim_advance(
	struct inode_reclaim *reclaim,
	const struct fs_deferred_reclaim_action *action)
{
	if (action->advance == 1) {
		if (action->cursor <= reclaim->direct_cursor ||
		    action->cursor > NDIRECT)
			panic("deferred direct reclaim cursor");
		reclaim->direct_cursor = action->cursor;
	} else if (action->advance == 2) {
		if (action->cursor <= reclaim->indirect_cursor ||
		    action->cursor > NINDIRECT)
			panic("deferred indirect reclaim cursor");
		reclaim->indirect_cursor = action->cursor;
	} else if (action->advance == 3)
		reclaim->indirect = 0;
	else if (action->advance == 4) {
		if (action->cursor <= reclaim->block_cursor ||
		    action->cursor > reclaim->block_count)
			panic("deferred list reclaim cursor");
		reclaim->block_cursor = action->cursor;
	} else if (action->advance == 5)
		reclaim->release_inode = 0;
	else
		panic("deferred reclaim advance");
}

static int
fs_deferred_reclaim_unique_add(uint blocks[], uint *count, uint block)
{
	for (uint i = 0; i < *count; i++)
		if (blocks[i] == block)
			return 0;
	if (*count >= FS_DEFERRED_RECLAIM_UNIQUE_BUFFERS)
		return -1;
	blocks[(*count)++] = block;
	return 0;
}

static int
fs_deferred_reclaim_action_fits(
	const struct fs_deferred_reclaim_action *action,
	uint unique[], uint *unique_count)
{
	uint trial[FS_DEFERRED_RECLAIM_UNIQUE_BUFFERS];
	uint count = *unique_count;

	memmove(trial, unique, sizeof(trial));
	if (action->inode != 0) {
		if (action->inode >= sb.ninodes ||
		    fs_deferred_reclaim_unique_add(
			    trial, &count, IBLOCK(action->inode, sb)) < 0)
			return 0;
	} else if (action->block != 0) {
		if (action->block < sb.datastart || action->block >= sb.size ||
		    fs_deferred_reclaim_unique_add(
			    trial, &count, BBLOCK(action->block, sb)) < 0 ||
		    fs_deferred_reclaim_unique_add(
			    trial, &count, QBLOCK(action->block, sb)) < 0)
			return 0;
	}
	memmove(unique, trial, sizeof(trial));
	*unique_count = count;
	return 1;
}

static int
fs_deferred_reclaim_stage_block(struct inode_reclaim *reclaim, uint block)
{
	uint qstate;
	uint owner = FS_OWNER_NONE;
	int allocated;
	int result;

	if (block == 0)
		return 0;
	if (block < sb.datastart || block >= sb.size ||
	    fs_epoch_preflight(2) < 0 ||
	    fs_scrub_block_allocated(reclaim->dev, block, &allocated) < 0 ||
	    fs_qmap_read(reclaim->dev, block, &qstate) < 0)
		return -1;
	if (qstate != FS_OWNER_NONE) {
		if (fs_storage_owner_valid(qstate))
			owner = qstate;
		else if (fs_qmap_transition_owner(
				 qstate, FS_QMAP_ALLOCATING_FLAG, &owner) < 0 &&
			 fs_qmap_transition_owner(
				 qstate, FS_QMAP_FREEING_FLAG, &owner) < 0)
			return fs_io_fail(
				FS_FAILURE_METADATA_WRITE_INDETERMINATE);
		if (owner != reclaim->storage_owner)
			return fs_io_fail(
				FS_FAILURE_METADATA_WRITE_INDETERMINATE);
		result = fs_qmap_write(reclaim->dev, block, FS_OWNER_NONE);
		if (result < 0)
			return result;
	}
	if (allocated) {
		result = fs_bitmap_write(reclaim->dev, block, 0);
		if (result < 0)
			return result;
	}
	return 0;
}

static int
fs_deferred_reclaim_stage_inode(struct inode_reclaim *reclaim)
{
	struct buf *bp;
	struct dinode *dip;
	int result;

	if (!reclaim->release_inode || reclaim->inode == 0 ||
	    reclaim->inode >= sb.ninodes || fs_epoch_preflight(1) < 0)
		return -1;
	result = fs_read_block(reclaim->dev,
			       IBLOCK(reclaim->inode, sb), &bp);
	if (result < 0)
		return result;
	dip = (struct dinode *)bp->data + reclaim->inode % IPB;
	if (dip->vfs_incarnation != reclaim->incarnation) {
		brelse(bp);
		return fs_io_fail(FS_FAILURE_METADATA_WRITE_INDETERMINATE);
	}
	if (dip->type == 0 && dip->fs_owner_domain == FS_OWNER_NONE &&
	    dip->fs_owner_version == 0) {
		brelse(bp);
		return 0;
	}
	if (dip->type != T_FILE || dip->size != 0 ||
	    dip->fs_owner_domain != reclaim->storage_owner) {
		brelse(bp);
		return fs_io_fail(FS_FAILURE_METADATA_WRITE_INDETERMINATE);
	}
	for (uint i = 0; i < NDIRECT + 1; i++) {
		if (dip->addrs[i] != 0) {
			brelse(bp);
			return fs_io_fail(
				FS_FAILURE_METADATA_WRITE_INDETERMINATE);
		}
	}
	fs_scrub_retire_dinode(dip, reclaim->inode);
	result = fs_write_inode_block(bp);
	brelse(bp);
	return result;
}

static int
fs_deferred_reclaim_entry_complete(
	const struct fs_deferred_reclaim_entry *entry)
{
	return entry->published &&
	       fs_deferred_reclaim_blocks_done(&entry->reclaim) &&
	       !entry->reclaim.release_inode;
}

static void
fs_deferred_reclaim_pop_complete(void)
{
	for (uint slot = 0; slot < FS_DEFERRED_RECLAIM_CAP; slot++) {
		struct fs_deferred_reclaim_entry *entry =
			&fs_deferred_reclaims[slot];
		uint sponsor_owner;

		if (!entry->reserved ||
		    !fs_deferred_reclaim_entry_complete(entry))
			continue;
		if (fs_deferred_reclaim_count == 0)
			panic("deferred reclaim pop underflow");
		sponsor_owner = entry->sponsor_owner;
		itruncate_reclaim_finish(&entry->reclaim);
		memset(entry, 0, sizeof(*entry));
		fs_deferred_reclaim_count--;
		bio_deferred_owner_release(sponsor_owner);
	}
}

int
fs_deferred_reclaim_pending(void)
{
	return fs_deferred_reclaim_count != 0;
}

int
fs_deferred_reclaim_owner_pending(uint owner)
{
	return owner != FS_OWNER_NONE &&
	       fs_deferred_reclaim_owner_count(owner) != 0;
}

static int
fs_deferred_reclaim_maintain_owner(uint owner_filter, int filtered)
{
	uint unique_count = 0;
	uint plan_count = 0;
	uint sponsor_owner;
	uint sponsor_class;
	uint selected = FS_DEFERRED_RECLAIM_CAP;
	int result = -1;

	if (!fs_epoch_request_held())
		return -1;
	fs_deferred_reclaim_pop_complete();
	if (fs_deferred_reclaim_count == 0)
		return 0;
	for (uint scanned = 0; scanned < FS_DEFERRED_RECLAIM_CAP;
	     scanned++) {
		uint slot = (fs_deferred_reclaim_cursor + scanned) %
			    FS_DEFERRED_RECLAIM_CAP;
		struct fs_deferred_reclaim_entry *entry =
			&fs_deferred_reclaims[slot];

		if (!entry->reserved || !entry->published ||
		    (filtered && entry->sponsor_owner != owner_filter) ||
		    !fs_epoch_generation_committed(entry->fence_generation))
			continue;
		selected = slot;
		break;
	}
	if (selected == FS_DEFERRED_RECLAIM_CAP) {
		agent_background_request();
		return 0;
	}
	fs_deferred_reclaim_cursor =
		(selected + 1) % FS_DEFERRED_RECLAIM_CAP;
	sponsor_owner = fs_deferred_reclaims[selected].sponsor_owner;
	sponsor_class = fs_deferred_reclaims[selected].sponsor_class;
	if (fs_epoch_dirty() && fs_epoch_commit() < 0)
		return -1;
	if (bio_deferred_sponsor_begin(sponsor_owner, sponsor_class, 0) < 0)
		return -1;
	memset(fs_deferred_reclaim_unique, 0,
	       sizeof(fs_deferred_reclaim_unique));

	for (uint scanned = 0;
	     scanned < FS_DEFERRED_RECLAIM_CAP &&
	     plan_count < FS_DEFERRED_RECLAIM_BATCH_UNITS; scanned++) {
		uint slot = (selected + scanned) % FS_DEFERRED_RECLAIM_CAP;
		struct fs_deferred_reclaim_entry *entry =
			&fs_deferred_reclaims[slot];

		if (!entry->reserved || !entry->published ||
		    entry->sponsor_owner != sponsor_owner ||
		    !fs_epoch_generation_committed(entry->fence_generation))
			continue;
		fs_deferred_reclaim_shadow = entry->reclaim;
		for (;;) {
			struct fs_deferred_reclaim_action action;
			int next = fs_deferred_reclaim_next(
				&fs_deferred_reclaim_shadow, &action);

			if (next < 0)
				goto out;
			if (next == 0)
				break;
			if (!fs_deferred_reclaim_action_fits(
				    &action, fs_deferred_reclaim_unique,
				    &unique_count)) {
				if (plan_count != 0)
					goto planned;
				result = fs_io_fail(
					FS_FAILURE_METADATA_WRITE_INDETERMINATE);
				goto out;
			}
			for (uint i = 0; i < plan_count; i++)
				if (action.block != 0 &&
				    fs_deferred_reclaim_plan[i].block ==
					    action.block) {
					result = fs_io_fail(
						FS_FAILURE_METADATA_WRITE_INDETERMINATE);
					goto out;
				}
			action.slot = slot;
			fs_deferred_reclaim_plan[plan_count++] = action;
			fs_deferred_reclaim_advance(
				&fs_deferred_reclaim_shadow, &action);
			if (plan_count == FS_DEFERRED_RECLAIM_BATCH_UNITS)
				goto planned;
		}
	}
planned:
	if (plan_count == 0)
		goto out;
	if (fs_allocator_gate_lock(1) < 0)
		goto out;
	for (uint i = 0; i < plan_count; i++) {
		struct fs_deferred_reclaim_action *action =
			&fs_deferred_reclaim_plan[i];
		struct inode_reclaim *reclaim =
			&fs_deferred_reclaims[action->slot].reclaim;

		result = action->inode != 0 ?
			fs_deferred_reclaim_stage_inode(reclaim) :
			fs_deferred_reclaim_stage_block(reclaim,
							 action->block);
		if (result < 0)
			goto out_unlock;
	}
	result = fs_epoch_commit();
	if (result < 0)
		goto out_unlock;
	for (uint i = 0; i < plan_count; i++) {
		struct fs_deferred_reclaim_action *action =
			&fs_deferred_reclaim_plan[i];
		struct inode_reclaim *reclaim =
			&fs_deferred_reclaims[action->slot].reclaim;

		fs_deferred_reclaim_advance(reclaim, action);
		if (action->block != 0)
			fs_storage_release_many_accounted(
				reclaim->storage_owner,
				reclaim->storage_account, 0, 1);
		else if (action->inode != 0)
			fs_storage_release_many_accounted(
				reclaim->storage_owner,
				reclaim->storage_account, 1, 1);
	}
	result = 1;
out_unlock:
	fs_allocator_gate_unlock();
out:
	bio_deferred_sponsor_end();
	if (result > 0)
		fs_deferred_reclaim_pop_complete();
	if (fs_deferred_reclaim_count != 0)
		agent_background_request();
	return result;
}

int
fs_deferred_reclaim_maintain(void)
{
	return fs_deferred_reclaim_maintain_owner(FS_OWNER_NONE, 0) < 0 ?
		-1 : 0;
}

static int
fs_deferred_reclaim_drain_owner(uint owner)
{
	if (owner == FS_OWNER_NONE || !fs_epoch_request_held())
		return -1;
	while (fs_deferred_reclaim_owner_count(owner) != 0) {
		int result;

		if (fs_epoch_dirty() && fs_epoch_commit() < 0)
			return -1;
		result = fs_deferred_reclaim_maintain_owner(owner, 1);
		if (result <= 0)
			return -1;
	}
	return 0;
}

int
fs_deferred_reclaim_drain_current(void)
{
	return fs_deferred_reclaim_drain_owner(bio_current_owner());
}

int
fs_deferred_reclaim_drain_all(void)
{
	if (!fs_epoch_request_held() || bio_current_owner() != FS_OWNER_SYSTEM)
		return -1;
	while (fs_deferred_reclaim_count != 0) {
		int result;

		if (fs_epoch_dirty() && fs_epoch_commit() < 0)
			return -1;
		result = fs_deferred_reclaim_maintain_owner(FS_OWNER_NONE, 0);
		if (result <= 0)
			return -1;
	}
	return 0;
}

#else

int fs_deferred_reclaim_pending(void)
{
	return 0;
}

int fs_deferred_reclaim_owner_pending(uint owner)
{
	(void)owner;
	return 0;
}

int fs_deferred_reclaim_maintain(void)
{
	return 0;
}

int fs_deferred_reclaim_drain_current(void)
{
	return 0;
}

int fs_deferred_reclaim_drain_all(void)
{
	return 0;
}

#endif

static int fs_block_candidate_drain(uint owner)
{
	struct fs_block_magazine *magazine;

	if (fs_allocator_gate_lock(1) < 0)
		return -1;
	magazine = fs_block_magazine_find(owner, 0);
	if (magazine != 0) {
		while (magazine->count != 0) {
			uint block = magazine->blocks[--magazine->count];

			fs_block_candidate_clear(block);
		}
		magazine->owner = FS_OWNER_NONE;
	}
	fs_allocator_gate_unlock();
	return 0;
}

// The inode table is capacity-sized, so key lookup must not scan every slot.
#define FS_ICACHE_HASH_BUCKETS 256U
#define FS_ICACHE_INDEX_NONE (-1)
_Static_assert((FS_ICACHE_HASH_BUCKETS & (FS_ICACHE_HASH_BUCKETS - 1)) == 0,
	       "inode-cache hash size must be a power of two");

struct {
	struct inode inode[FS_ICACHE_SIZE];
	int next[FS_ICACHE_SIZE];
	int hash_head[FS_ICACHE_HASH_BUCKETS];
	int free_head;
	int initialized;
} itable;

static struct inode *iget(uint dev, uint inum);

static uint inode_cache_bucket(uint dev, uint inum)
{
	return ((dev * 16777619U) ^ inum) & (FS_ICACHE_HASH_BUCKETS - 1);
}

static int inode_cache_index(struct inode *ip)
{
	if (ip < &itable.inode[0] || ip >= &itable.inode[FS_ICACHE_SIZE])
		panic("inode cache pointer");
	return (int)(ip - &itable.inode[0]);
}

static void
inode_mapping_token(void **token, uint64 *generation)
{
	struct thread *self = curr_thread();

	if (self != 0 && self->identity_generation != 0) {
		*token = self;
		*generation = self->identity_generation;
	} else {
		*token = &inode_mapping_boot_token;
		*generation = 0;
	}
}

static int
inode_mapping_writer_owned_locked(struct inode_mapping_guard *guard,
				   void *token, uint64 generation)
{
	return guard->writer == token &&
	       guard->writer_generation == generation;
}

static int
inode_mapping_read_lock(struct inode *ip)
{
	struct inode_mapping_guard *guard;
	void *token;
	uint64 generation;
	int queued = 0;
	int enabled;

	if (ip == 0)
		return -1;
	guard = &inode_mapping_guards[inode_cache_index(ip)];
	inode_mapping_token(&token, &generation);
	enabled = intr_save();
	for (;;) {
		if (guard->writer == 0 && guard->writer_waiters == 0) {
			if (guard->readers == (uint)-1)
				panic("inode mapping reader overflow");
			guard->readers++;
			if (queued) {
				if (guard->reader_waiters == 0)
					panic("inode mapping reader waiter underflow");
				guard->reader_waiters--;
			}
			intr_restore(enabled);
			return 0;
		}
		if (inode_mapping_writer_owned_locked(guard, token, generation))
			panic("inode mapping read recursion");
		if (!queued) {
			if (guard->reader_waiters == (uint)-1)
				panic("inode mapping reader waiter overflow");
			guard->reader_waiters++;
			queued = 1;
		}
		if (token == &inode_mapping_boot_token ||
		    wait_queue_sleep_irq(&inode_mapping_waiters) !=
			    WAIT_QUEUE_OK) {
			if (guard->reader_waiters == 0)
				panic("inode mapping reader waiter underflow");
			guard->reader_waiters--;
			intr_restore(enabled);
			return -1;
		}
	}
}

static void
inode_mapping_read_unlock(struct inode *ip)
{
	struct inode_mapping_guard *guard;
	int enabled;

	if (ip == 0)
		panic("inode mapping read unlock");
	guard = &inode_mapping_guards[inode_cache_index(ip)];
	enabled = intr_save();
	if (guard->readers == 0)
		panic("inode mapping reader underflow");
	guard->readers--;
	if (guard->readers == 0 && guard->writer_waiters != 0)
		wait_queue_wake_all(&inode_mapping_waiters);
	intr_restore(enabled);
}

static int
inode_mapping_write_lock(struct inode *ip, int cleanup)
{
	struct inode_mapping_guard *guard;
	void *token;
	uint64 generation;
	int queued = 0;
	int enabled;

	if (ip == 0)
		return -1;
	guard = &inode_mapping_guards[inode_cache_index(ip)];
	inode_mapping_token(&token, &generation);
	enabled = intr_save();
	for (;;) {
		if (inode_mapping_writer_owned_locked(guard, token, generation)) {
			if (guard->writer_depth == (uint)-1)
				panic("inode mapping writer overflow");
			guard->writer_depth++;
			intr_restore(enabled);
			return 0;
		}
		if (guard->writer == 0 && guard->readers == 0) {
			guard->writer = token;
			guard->writer_generation = generation;
			guard->writer_depth = 1;
			if (queued) {
				if (guard->writer_waiters == 0)
					panic("inode mapping waiter underflow");
				guard->writer_waiters--;
			}
			intr_restore(enabled);
			return 0;
		}
		if (!queued) {
			if (guard->writer_waiters == (uint)-1)
				panic("inode mapping waiter overflow");
			guard->writer_waiters++;
			queued = 1;
		}
		if (token == &inode_mapping_boot_token) {
			guard->writer_waiters--;
			intr_restore(enabled);
			if (cleanup)
				panic("boot inode mapping contention");
			return -1;
		}
		if (cleanup) {
			(void)wait_queue_sleep_irq_uninterruptible(
				&inode_mapping_waiters);
			continue;
		}
		if (wait_queue_sleep_irq(&inode_mapping_waiters) !=
		    WAIT_QUEUE_OK) {
			if (guard->writer_waiters == 0)
				panic("inode mapping waiter underflow");
			guard->writer_waiters--;
			wait_queue_wake_all(&inode_mapping_waiters);
			intr_restore(enabled);
			return -1;
		}
	}
}

static void
inode_mapping_write_unlock(struct inode *ip)
{
	struct inode_mapping_guard *guard;
	void *token;
	uint64 generation;
	int enabled;

	if (ip == 0)
		panic("inode mapping write unlock");
	guard = &inode_mapping_guards[inode_cache_index(ip)];
	inode_mapping_token(&token, &generation);
	enabled = intr_save();
	if (!inode_mapping_writer_owned_locked(guard, token, generation))
		panic("inode mapping writer owner");
	if (guard->writer_depth == 0)
		panic("inode mapping writer underflow");
	guard->writer_depth--;
	if (guard->writer_depth != 0) {
		intr_restore(enabled);
		return;
	}
	guard->writer = 0;
	guard->writer_generation = 0;
	if (guard->writer_waiters != 0 || guard->reader_waiters != 0)
		wait_queue_wake_all(&inode_mapping_waiters);
	intr_restore(enabled);
}

static void
inode_mapping_require(struct inode *ip, int write)
{
	struct inode_mapping_guard *guard;
	void *token;
	uint64 generation;
	int enabled;
	int held;

	guard = &inode_mapping_guards[inode_cache_index(ip)];
	inode_mapping_token(&token, &generation);
	enabled = intr_save();
	held = inode_mapping_writer_owned_locked(guard, token, generation) ||
	       (!write && guard->readers != 0);
	intr_restore(enabled);
	if (!held) {
		if (write)
			panic("inode mapping write guard");
		panic("inode mapping read guard");
	}
}

static void inode_cache_init_once(void)
{
	if (itable.initialized)
		return;
	for (uint bucket = 0; bucket < FS_ICACHE_HASH_BUCKETS; bucket++)
		itable.hash_head[bucket] = FS_ICACHE_INDEX_NONE;
	itable.free_head = FS_ICACHE_INDEX_NONE;
	for (int index = FS_ICACHE_SIZE - 1; index >= 0; index--) {
		itable.next[index] = itable.free_head;
		itable.free_head = index;
	}
	itable.initialized = 1;
}

static void inode_cache_drop_ref(struct inode *ip)
{
	uint bucket;
	uint scanned = 0;
	int index, *link;

	if (ip == 0 || ip->ref <= 0)
		panic("inode reference underflow");
	ip->ref--;
	if (ip->ref != 0)
		return;
	index = inode_cache_index(ip);
	if (inode_mapping_guards[index].readers != 0 ||
	    inode_mapping_guards[index].reader_waiters != 0 ||
	    inode_mapping_guards[index].writer != 0 ||
	    inode_mapping_guards[index].writer_depth != 0 ||
	    inode_mapping_guards[index].writer_waiters != 0)
		panic("inode cache mapping guard live");
	bucket = inode_cache_bucket(ip->dev, ip->inum);
	link = &itable.hash_head[bucket];
	while (*link != FS_ICACHE_INDEX_NONE && *link != index) {
		if (scanned++ >= FS_ICACHE_SIZE)
			panic("inode cache hash cycle");
		link = &itable.next[*link];
	}
	if (*link != index)
		panic("inode cache unlink");
	*link = itable.next[index];
	itable.next[index] = itable.free_head;
	itable.free_head = index;
}

// Allocate an inode on device dev.
// Mark it as allocated by  giving it type `type`.
// Returns an allocated and referenced inode.
struct inode *ialloc(uint dev, short type,
		     const struct fs_storage_charge *charge, int *error)
{
	int inum, pass;
	uint range_start, range_end, start_cursor;
	uint intent;
	uint incarnation;
	struct buf *bp;
	struct dinode *dip;
	struct inode *ip = 0;
	int reserved = 0;
	int result = -1;

	if (error)
		*error = -1;
	if (charge == 0)
		return 0;
	if (fs_epoch_preflight(1) < 0)
		return 0;
	if (fs_allocator_gate_lock(0) < 0)
		return 0;
	intent = fs_qmap_transition(FS_QMAP_ALLOCATING_FLAG, charge->owner);
	if (intent == FS_OWNER_NONE)
		goto out;
	if (fs_storage_reserve(charge, 1) < 0)
		goto out;
	reserved = 1;
	start_cursor = fs_storage.inode_alloc_cursor;
	if (start_cursor < 1 || start_cursor >= sb.ninodes)
		start_cursor = 1;
	for (pass = 0; pass < 2; pass++) {
		range_start = pass == 0 ? start_cursor : 1;
		range_end = pass == 0 ? sb.ninodes : start_cursor;
		if (range_start >= range_end)
			continue;
		for (inum = range_start; inum < (int)range_end; inum++) {
			result = fs_read_block(dev, IBLOCK(inum, sb), &bp);
			if (result < 0)
				goto out;
			dip = (struct dinode *)bp->data + inum % IPB;
			if (dip->type == 0) { // a free inode
				ip = iget(dev, inum);
				if (ip == 0) {
					brelse(bp);
					goto out;
				}
				incarnation = dip->vfs_incarnation + 1;
				if (incarnation == 0)
					incarnation = 1;
				memset(dip, 0, sizeof(*dip));
				dip->type = type;
				dip->vfs_magic = VFS_LABEL_MAGIC;
				dip->vfs_version = VFS_LABEL_VERSION;
				dip->vfs_flags = VFS_LABEL_F_FREE;
				dip->vfs_policy = VFS_POLICY_FREE;
				dip->vfs_policy_generation = VFS_POLICY_GENERATION;
				dip->vfs_incarnation = incarnation;
				dip->fs_owner_domain = intent;
				dip->fs_owner_version = FS_OWNER_VERSION;
				dip->vfs_checksum = vfs_label_checksum(
					inum, dip->vfs_magic, dip->vfs_version,
					dip->vfs_flags, dip->vfs_scope_id,
					dip->vfs_policy, dip->vfs_exec_profile,
					dip->vfs_policy_generation,
					dip->vfs_incarnation,
					dip->fs_owner_domain,
					dip->fs_owner_version);
				result = fs_allocator_fault_before(
					FSALLOC_OP_IALLOC,
					FSALLOC_PHASE_INTENT, 0);
				if (result < 0) {
					brelse(bp);
					inode_cache_drop_ref(ip);
					ip = 0;
					goto out;
				}
				result = fs_write_inode_block(bp);
				if (result < 0) {
					brelse(bp);
					inode_cache_drop_ref(ip);
					ip = 0;
					goto out;
				}
				brelse(bp);
				result = fs_durable_barrier_forward();
				if (result < 0) {
					inode_cache_drop_ref(ip);
					ip = 0;
					goto out;
				}
				FS_ALLOCATOR_FAULT_AFTER(
					FSALLOC_OP_IALLOC,
					FSALLOC_PHASE_INTENT);
				fs_storage.inode_alloc_cursor = inum + 1;
				if (fs_storage.inode_alloc_cursor >= sb.ninodes)
					fs_storage.inode_alloc_cursor = 1;
				reserved = 0;
				if (error)
					*error = 0;
				fs_allocator_gate_unlock();
				return ip;
			}
			brelse(bp);
			if (inum % IPB == IPB - 1 || inum + 1 == (int)range_end) {
				struct bio_checkpoint_result checkpoint;

				fs_storage.inode_alloc_cursor = inum + 1;
				if (fs_storage.inode_alloc_cursor >= sb.ninodes)
					fs_storage.inode_alloc_cursor = 1;
				checkpoint = bio_request_checkpoint();
				if (bio_checkpoint_should_stop(checkpoint)) {
					result = checkpoint.state ==
							 BIO_CHECKPOINT_DEFERRED ?
						VIRTIO_DISK_ERR_BUSY : -1;
					goto out;
				}
			}
		}
	}
	result = -1;
out:
	if (reserved && fs_storage.ready)
		fs_storage_release(charge->owner, 1);
	if (error)
		*error = result;
	fs_allocator_gate_unlock();
	return 0;
}

// Copy a modified in-memory inode to disk.
// Must be called after every change to an on-disk inode field
// that lives on disk.
int iupdate(struct inode *ip)
{
	struct buf *bp;
	struct dinode *dip;
	int result;

	if (ip == 0)
		return fs_io_fail(FS_FAILURE_OPERATION);
	if (fs_io_health != FS_IO_HEALTHY)
		return -1;
	if (fs_epoch_preflight(1) < 0)
		return -1;
	result = fs_read_block(ip->dev, IBLOCK(ip->inum, sb), &bp);
	if (result < 0)
		return result;
	dip = (struct dinode *)bp->data + ip->inum % IPB;
	dip->type = ip->type;
	dip->agent_meta_slot = ip->agent_meta_slot;
	dip->agent_meta_flags = ip->agent_meta_flags;
	dip->agent_meta_version = ip->agent_meta_version;
	dip->size = ip->size;
	dip->exec_flags = ip->exec_flags;
	dip->exec_generation = ip->exec_generation;
	dip->exec_role_mask = ip->exec_role_mask;
	dip->exec_layout_version = ip->exec_layout_version;
	dip->exec_rw_offset = ip->exec_rw_offset;
	dip->vfs_magic = ip->vfs_magic;
	dip->vfs_version = ip->vfs_version;
	dip->vfs_flags = ip->vfs_flags;
	dip->vfs_scope_id = ip->vfs_scope_id;
	dip->vfs_policy = ip->vfs_policy;
	dip->vfs_exec_profile = ip->vfs_exec_profile;
	dip->vfs_policy_generation = ip->vfs_policy_generation;
	dip->vfs_incarnation = ip->vfs_incarnation;
	dip->fs_owner_domain = ip->fs_owner_domain;
	dip->fs_owner_version = ip->fs_owner_version;
	dip->vfs_checksum = ip->vfs_checksum;
	// LAB4: you may need to update link count here
	memmove(dip->addrs, ip->addrs, sizeof(ip->addrs));
	result = fs_write_inode_block(bp);
	brelse(bp);
	return result;
}

// Find the inode with number inum on device dev
// and return the in-memory copy. Does not read
// it from disk.
static struct inode *iget(uint dev, uint inum)
{
	uint bucket;
	uint scanned = 0;
	int index;
	struct inode *ip;

	inode_cache_init_once();
	bucket = inode_cache_bucket(dev, inum);
	for (index = itable.hash_head[bucket];
	     index != FS_ICACHE_INDEX_NONE; index = itable.next[index]) {
		if (scanned++ >= FS_ICACHE_SIZE)
			panic("inode cache hash cycle");
		ip = &itable.inode[index];
		if (ip->ref <= 0)
			panic("free inode in hash");
		if (ip->dev == dev && ip->inum == inum) {
			ip->ref++;
			return ip;
		}
	}

	index = itable.free_head;
	if (index == FS_ICACHE_INDEX_NONE)
		return 0;
	itable.free_head = itable.next[index];
	ip = &itable.inode[index];
	if (inode_mapping_guards[index].readers != 0 ||
	    inode_mapping_guards[index].reader_waiters != 0 ||
	    inode_mapping_guards[index].writer != 0 ||
	    inode_mapping_guards[index].writer_depth != 0 ||
	    inode_mapping_guards[index].writer_waiters != 0)
		panic("inode cache reused with mapping guard");
	memset(ip, 0, sizeof(*ip));
	ip->dev = dev;
	ip->inum = inum;
	ip->ref = 1;
	itable.next[index] = itable.hash_head[bucket];
	itable.hash_head[bucket] = index;
	return ip;
}

struct inode *inode_get(uint dev, uint inum)
{
	if (inum == 0 || inum >= sb.ninodes)
		return 0;
	return iget(dev, inum);
}

// Increment reference count for ip.
// Returns ip to enable ip = idup(ip1) idiom.
struct inode *idup(struct inode *ip)
{
	ip->ref++;
	return ip;
}

// Reads the inode from disk if necessary.
int ivalid(struct inode *ip)
{
	struct buf *bp;
	struct dinode *dip;
	int result;

	if (ip == 0)
		return -1;
	if (ip->valid == 0) {
		if (inode_mapping_write_lock(ip, 0) < 0)
			return -1;
		/* Another loader may have published the image while we waited. */
		if (ip->valid != 0) {
			inode_mapping_write_unlock(ip);
			return 0;
		}
		result = fs_read_block(ip->dev, IBLOCK(ip->inum, sb), &bp);
		if (result < 0) {
			uint dev = ip->dev;
			uint inum = ip->inum;
			int ref = ip->ref;
			int removed = ip->removed;

			memset(ip, 0, sizeof(*ip));
			ip->dev = dev;
			ip->inum = inum;
			ip->ref = ref;
			ip->removed = removed;
			inode_mapping_write_unlock(ip);
			return result;
		}
		dip = (struct dinode *)bp->data + ip->inum % IPB;
		ip->type = dip->type;
		ip->agent_meta_slot = dip->agent_meta_slot;
		ip->agent_meta_flags = dip->agent_meta_flags;
		ip->agent_meta_version = dip->agent_meta_version;
		ip->size = dip->size;
		ip->exec_flags = dip->exec_flags;
		ip->exec_generation = dip->exec_generation;
		ip->exec_role_mask = dip->exec_role_mask;
		ip->exec_layout_version = dip->exec_layout_version;
		ip->exec_rw_offset = dip->exec_rw_offset;
		ip->vfs_magic = dip->vfs_magic;
		ip->vfs_version = dip->vfs_version;
		ip->vfs_flags = dip->vfs_flags;
		ip->vfs_scope_id = dip->vfs_scope_id;
		ip->vfs_policy = dip->vfs_policy;
		ip->vfs_exec_profile = dip->vfs_exec_profile;
		ip->vfs_policy_generation = dip->vfs_policy_generation;
		ip->vfs_incarnation = dip->vfs_incarnation;
		ip->fs_owner_domain = dip->fs_owner_domain;
		ip->fs_owner_version = dip->fs_owner_version;
		ip->vfs_checksum = dip->vfs_checksum;
		// LAB4: You may need to get lint count here
		memmove(ip->addrs, dip->addrs, sizeof(ip->addrs));
		brelse(bp);
		ip->valid = 1;
		inode_mapping_write_unlock(ip);
	}
	return 0;
}

// Publish a removed inode as free before releasing its detached block token.
// The token can be drained synchronously by iput() or incrementally by a
// background reclaimer without keeping an inode or buffer pinned.
int inode_remove_detach(struct inode *ip, struct inode_reclaim *reclaim)
{
#ifndef FS_ALLOCATOR_FAULT_TEST_PROFILE
	uint sponsor_owner;
	int result;

	if (ip == 0 || reclaim == 0)
		return 0;
	if (inode_mapping_write_lock(ip, 1) < 0)
		return -1;
	if (ip->ref != 1 || !ip->valid || !ip->removed) {
		inode_mapping_write_unlock(ip);
		return 0;
	}
	memset(reclaim, 0, sizeof(*reclaim));
	reclaim->dev = ip->dev;
	reclaim->storage_owner = ip->fs_owner_domain;
	reclaim->storage_account = resource_account_none();
	if (!fs_storage_owner_valid(reclaim->storage_owner) ||
	    fs_storage_owner_account(reclaim->storage_owner,
				     &reclaim->storage_account) < 0) {
		ip->removed = 0;
		inode_mapping_write_unlock(ip);
		inode_cache_drop_ref(ip);
		return -1;
	}
	sponsor_owner = bio_current_owner();
	if (fs_deferred_reclaim_reserve(sponsor_owner, reclaim) < 0) {
		ip->removed = 0;
		inode_mapping_write_unlock(ip);
		inode_cache_drop_ref(ip);
		return -1;
	}
	if (ip->size != 0) {
		result = itruncate_detach_all(ip, reclaim);
		if (result < 0)
			goto deferred_fail;
	} else {
		/* Stage an explicit inode image so the reclaim fence cannot refer
		 * to an older, unrelated committed generation. */
		reclaim->mode = INODE_RECLAIM_NONE;
		result = iupdate(ip);
		if (result < 0)
			goto deferred_fail;
	}
	reclaim->inode = ip->inum;
	reclaim->incarnation = ip->vfs_incarnation;
	reclaim->release_inode = 1;
	agent_file_version_reclaim(ip);
	if (fs_deferred_reclaim_publish(reclaim) < 0)
		panic("inode reclaim publish");
	ip->valid = 0;
	ip->removed = 0;
	inode_mapping_write_unlock(ip);
	inode_cache_drop_ref(ip);
	return 1;

deferred_fail:
	fs_deferred_reclaim_cancel(reclaim);
	if (reclaim->mode == INODE_RECLAIM_LIST &&
	    reclaim->block_list != 0)
		(void)kfree_account_page((char *)reclaim->block_list,
					 reclaim->page_account,
					 reclaim->page_charge_class);
	memset(reclaim, 0, sizeof(*reclaim));
	ip->removed = 0;
	inode_mapping_write_unlock(ip);
	inode_cache_drop_ref(ip);
	return -1;
#else
	struct vfs_cred kernel_cred;
	struct inode allocated_inode;
	uint storage_owner;
	uint freeing;
	int bypass_entered = 0;
	int transition_status;

	if (ip == 0 || reclaim == 0)
		return 0;
	if (inode_mapping_write_lock(ip, 1) < 0)
		return -1;
	if (ip->ref != 1 || !ip->valid || !ip->removed) {
		inode_mapping_write_unlock(ip);
		return 0;
	}
	if (fs_epoch_destructive_begin(&bypass_entered) < 0) {
		inode_mapping_write_unlock(ip);
		return -1;
	}
	storage_owner = ip->fs_owner_domain;
	freeing = fs_qmap_transition(FS_QMAP_FREEING_FLAG, storage_owner);
	if (freeing == FS_OWNER_NONE) {
		ip->removed = 0;
		inode_mapping_write_unlock(ip);
		inode_cache_drop_ref(ip);
		fs_epoch_destructive_end(bypass_entered);
		return -1;
	}
	vfs_cred_kernel(&kernel_cred);
	if (itruncate_detach(ip, &kernel_cred, 0, reclaim) < 0) {
		ip->removed = 0;
		inode_mapping_write_unlock(ip);
		inode_cache_drop_ref(ip);
		fs_epoch_destructive_end(bypass_entered);
		return -1;
	}
	if (fs_allocator_gate_lock(1) < 0) {
		(void)fs_io_fail(FS_FAILURE_METADATA_WRITE_INDETERMINATE);
		if (reclaim->mode == INODE_RECLAIM_LIST &&
		    reclaim->block_list != 0)
			(void)kfree_account_page((char *)reclaim->block_list,
						 reclaim->page_account,
						 reclaim->page_charge_class);
		memset(reclaim, 0, sizeof(*reclaim));
		ip->removed = 0;
		inode_mapping_write_unlock(ip);
		inode_cache_drop_ref(ip);
		fs_epoch_destructive_end(bypass_entered);
		return -1;
	}
	memmove(&allocated_inode, ip, sizeof(allocated_inode));
	transition_status = fs_allocator_fault_before(
		FSALLOC_OP_IFREE, FSALLOC_PHASE_INTENT, 1);
	if (transition_status < 0)
		goto publish_fail;
	ip->fs_owner_domain = freeing;
	ip->vfs_checksum = vfs_label_checksum(
		ip->inum, ip->vfs_magic, ip->vfs_version, ip->vfs_flags,
		ip->vfs_scope_id, ip->vfs_policy, ip->vfs_exec_profile,
		ip->vfs_policy_generation, ip->vfs_incarnation,
		ip->fs_owner_domain, ip->fs_owner_version);
	transition_status = iupdate(ip);
	if (transition_status < 0 || fs_durable_barrier_forward() < 0)
		goto publish_fail;
	FS_ALLOCATOR_FAULT_AFTER(FSALLOC_OP_IFREE, FSALLOC_PHASE_INTENT);
	transition_status = fs_allocator_fault_before(
		FSALLOC_OP_IFREE, FSALLOC_PHASE_OWNER, 1);
	if (transition_status < 0)
		goto publish_fail;
	agent_file_version_reclaim(ip);
	ip->type = 0;
	ip->agent_meta_slot = 0;
	ip->agent_meta_flags = 0;
	ip->agent_meta_version = 0;
	ip->exec_flags = 0;
	ip->exec_generation = 0;
	ip->exec_role_mask = 0;
	ip->exec_layout_version = 0;
	ip->exec_rw_offset = 0;
	vfs_inode_mark_free(ip);
	ip->fs_owner_domain = freeing;
	ip->fs_owner_version = FS_OWNER_VERSION;
	ip->vfs_checksum = vfs_label_checksum(
		ip->inum, ip->vfs_magic, ip->vfs_version, ip->vfs_flags,
		ip->vfs_scope_id, ip->vfs_policy, ip->vfs_exec_profile,
		ip->vfs_policy_generation, ip->vfs_incarnation,
		ip->fs_owner_domain, ip->fs_owner_version);
	transition_status = iupdate(ip);
	if (transition_status < 0 || fs_durable_barrier_forward() < 0)
		goto publish_fail;
	FS_ALLOCATOR_FAULT_AFTER(FSALLOC_OP_IFREE, FSALLOC_PHASE_OWNER);
	transition_status = fs_allocator_fault_before(
		FSALLOC_OP_IFREE, FSALLOC_PHASE_REFUND, 1);
	if (transition_status < 0)
		goto publish_fail;
	ip->fs_owner_domain = FS_OWNER_NONE;
	ip->fs_owner_version = 0;
	ip->vfs_checksum = vfs_label_checksum(
		ip->inum, ip->vfs_magic, ip->vfs_version, ip->vfs_flags,
		ip->vfs_scope_id, ip->vfs_policy, ip->vfs_exec_profile,
		ip->vfs_policy_generation, ip->vfs_incarnation,
		ip->fs_owner_domain, ip->fs_owner_version);
	transition_status = iupdate(ip);
	if (transition_status < 0 || fs_durable_barrier_forward() < 0)
		goto publish_fail;
	fs_storage_release(storage_owner, 1);
	FS_ALLOCATOR_FAULT_AFTER(FSALLOC_OP_IFREE, FSALLOC_PHASE_REFUND);
	fs_allocator_gate_unlock();
	ip->valid = 0;
	ip->removed = 0;
	inode_mapping_write_unlock(ip);
	inode_cache_drop_ref(ip);
	fs_epoch_destructive_end(bypass_entered);
	return 1;

publish_fail:
	fs_allocator_gate_unlock();
	memmove(ip, &allocated_inode, sizeof(*ip));
	if (reclaim->mode == INODE_RECLAIM_LIST &&
	    reclaim->block_list != 0)
		(void)kfree_account_page((char *)reclaim->block_list,
					 reclaim->page_account,
					 reclaim->page_charge_class);
	memset(reclaim, 0, sizeof(*reclaim));
	ip->removed = 0;
	inode_mapping_write_unlock(ip);
	inode_cache_drop_ref(ip);
	fs_epoch_destructive_end(bypass_entered);
	return -1;
#endif
}

// Drop a reference to an in-memory inode. Removed objects first publish a
// detached, free inode and then reclaim the private block token.
int iput_drop_only(struct inode *ip)
{
	int enabled;

	if (ip == 0)
		return -1;
	enabled = intr_save();
	if (ip->ref == 1 && ip->valid && ip->removed) {
		intr_restore(enabled);
		return 0;
	}
	inode_cache_drop_ref(ip);
	intr_restore(enabled);
	return 1;
}

void iput(struct inode *ip)
{
	if (ip->ref == 1 && ip->valid && ip->removed) {
		struct inode_reclaim reclaim;
		int detached = inode_remove_detach(ip, &reclaim);
		int reclaimed;

		if (detached > 0) {
			reclaimed = itruncate_reclaim(&reclaim);
			(void)reclaimed;
		}
		return;
	}
	inode_cache_drop_ref(ip);
}

static int fs_put_removed_checked(struct inode *) __attribute__((noinline));

static int fs_put_removed_checked(struct inode *ip)
{
	struct inode_reclaim reclaim;
	int detached;

	if (ip->ref != 1 || !ip->valid || !ip->removed) {
		iput(ip);
		return -1;
	}
	detached = inode_remove_detach(ip, &reclaim);
	if (detached <= 0)
		return -1;
	return itruncate_reclaim(&reclaim);
}

static int fs_create_failure_status(int result)
{
	return fs_io_health == FS_IO_INDETERMINATE ?
		FS_LOOKUP_INDETERMINATE : (result < 0 ? result : FS_LOOKUP_ERROR);
}

void iabort(struct inode *ip)
{
	if (ip == 0)
		return;
	ip->removed = 1;
	iput(ip);
}

// Inode content
//
// The content (data) associated with each inode is stored
// in blocks on the disk. The first NDIRECT block numbers
// are listed in ip->addrs[].  The next NINDIRECT blocks are
// listed in block ip->addrs[NDIRECT].

// Return the disk block address of the nth block in inode ip.
// When alloc is zero, a missing mapping is reported without changing the file.
static uint bmap_error(int *error, int result)
{
	if (error != 0)
		*error = result;
	return 0;
}

static uint bmap(struct inode *ip, uint bn, int alloc,
		 const struct fs_storage_charge *charge, int *error)
{
	uint addr, candidate, indirect, *a;
	struct buf *bp;
	int indirect_allocated = 0;
	int result;

	if (error != 0)
		*error = 0;
	inode_mapping_require(ip, alloc != 0);

	if (bn < NDIRECT) {
		addr = ip->addrs[bn];
		if (addr == 0 && alloc) {
			candidate = balloc(ip->dev, charge, &result);
			if (candidate == 0)
				return bmap_error(error, result);
			if (ip->addrs[bn] == 0) {
				ip->addrs[bn] = candidate;
				addr = candidate;
			} else {
				addr = ip->addrs[bn];
				result = bfree(ip->dev, candidate);
				if (result < 0)
					return bmap_error(error, result);
			}
		}
		return addr;
	}
	bn -= NDIRECT;

	if (bn < NINDIRECT) {
		indirect = ip->addrs[NDIRECT];
		if (indirect == 0) {
			if (!alloc)
				return 0;
			candidate = balloc(ip->dev, charge, &result);
			if (candidate == 0)
				return bmap_error(error, result);
			if (ip->addrs[NDIRECT] == 0) {
				ip->addrs[NDIRECT] = candidate;
				indirect = candidate;
				indirect_allocated = 1;
			} else {
				indirect = ip->addrs[NDIRECT];
				result = bfree(ip->dev, candidate);
				if (result < 0)
					return bmap_error(error, result);
			}
		}
		result = fs_read_block(ip->dev, indirect, &bp);
		if (result < 0) {
			if (indirect_allocated) {
				int cleanup;

				ip->addrs[NDIRECT] = 0;
				cleanup = bfree(ip->dev, indirect);
				if (cleanup < 0)
					result = cleanup;
			}
			return bmap_error(error, result);
		}
		a = (uint *)bp->data;
		addr = a[bn];
		brelse(bp);
		if (addr == 0 && alloc) {
			candidate = balloc(ip->dev, charge, &result);
			if (candidate == 0 && indirect_allocated) {
				ip->addrs[NDIRECT] = 0;
				if (bfree(ip->dev, indirect) < 0)
					result = -1;
				return bmap_error(error, result);
			}
			if (candidate != 0) {
				/*
				 * Allocation may cross a budget checkpoint. Reacquire the
				 * indirect block and publish only after revalidating the
				 * entry; a concurrent winner keeps its mapping and this
				 * candidate is returned to the same quota domain.
				 */
				result = fs_read_block(ip->dev, indirect, &bp);
				if (result < 0) {
					int cleanup = bfree(ip->dev, candidate);

					if (cleanup < 0)
						result = cleanup;
					return bmap_error(error, result);
				}
				a = (uint *)bp->data;
				if (a[bn] == 0) {
					a[bn] = candidate;
					result = fs_write_metadata_block(bp);
					if (result < 0) {
						brelse(bp);
						if (bfree(ip->dev, candidate) < 0)
							result = -1;
						return bmap_error(error, result);
					}
					brelse(bp);
					result = fs_durable_barrier_forward();
					if (result < 0)
						return bmap_error(error, result);
					addr = candidate;
					candidate = 0;
				} else {
					addr = a[bn];
					brelse(bp);
				}
				if (candidate != 0) {
					result = bfree(ip->dev, candidate);
					if (result < 0)
						return bmap_error(error, result);
				}
			}
		}
		if (addr == 0 && indirect_allocated) {
			int empty = 1;

			result = fs_read_block(ip->dev, indirect, &bp);
			if (result < 0)
				return bmap_error(error, result);
			a = (uint *)bp->data;
			for (uint i = 0; i < NINDIRECT; i++)
				if (a[i] != 0) {
					empty = 0;
					break;
				}
			if (empty && ip->addrs[NDIRECT] == indirect)
				ip->addrs[NDIRECT] = 0;
			else
				empty = 0;
			brelse(bp);
			if (empty) {
				result = bfree(ip->dev, indirect);
				if (result < 0)
					return bmap_error(error, result);
			}
		}
		return addr;
	}

	return 0;
}

static uint bmap_read_batch(struct inode *ip, uint bn, uint count,
			    uint *out, int *error)
{
	struct buf *bp;
	uint *indirect;
	uint mapped = 0;
	uint index;
	int result;

	if (out == 0 || error == 0 || count == 0)
		return 0;
	inode_mapping_require(ip, 0);
	*error = 0;
	while (mapped < count && bn + mapped < NDIRECT) {
		uint addr = ip->addrs[bn + mapped];

		if (addr == 0)
			return mapped;
		out[mapped++] = addr;
	}
	if (mapped == count)
		return mapped;
	index = bn + mapped;
	if (index < NDIRECT)
		return mapped;
	index -= NDIRECT;
	if (index >= NINDIRECT || ip->addrs[NDIRECT] == 0)
		return mapped;
	result = fs_read_block(ip->dev, ip->addrs[NDIRECT], &bp);
	if (result < 0) {
		*error = result;
		return mapped;
	}
	indirect = (uint *)bp->data;
	while (mapped < count && index < NINDIRECT) {
		uint addr = indirect[index++];

		if (addr == 0)
			break;
		out[mapped++] = addr;
	}
	brelse(bp);
	return mapped;
}

// Undo a block allocated for a write that could not copy any data into it.
static int bmap_discard(struct inode *ip, uint bn)
{
	struct buf *bp;
	uint addr;
	uint indirect;
	uint *a;

	inode_mapping_require(ip, 1);

	if (bn < NDIRECT) {
		addr = ip->addrs[bn];
		if (addr != 0) {
			ip->addrs[bn] = 0;
			if (bfree(ip->dev, addr) < 0)
				return -1;
		}
		return 0;
	}
	bn -= NDIRECT;
	if (bn >= NINDIRECT || ip->addrs[NDIRECT] == 0)
		return 0;
	indirect = ip->addrs[NDIRECT];
	if (fs_read_block(ip->dev, indirect, &bp) < 0)
		return -1;
	a = (uint *)bp->data;
	addr = a[bn];
	if (addr == 0) {
		brelse(bp);
		return 0;
	}
	a[bn] = 0;
	if (fs_write_metadata_block(bp) < 0) {
		brelse(bp);
		return -1;
	}
	brelse(bp);
	if (fs_durable_barrier_forward() < 0)
		return -1;
	if (bfree(ip->dev, addr) < 0)
		return -1;
	return 0;
}

static int truncate_free_block(int dev, uint block)
{
	if (block == 0)
		return 0;
	if (bfree(dev, block) < 0)
		return -1;
	(void)kernel_work_checkpoint_cleanup(KERNEL_WORK_OPERATION_UNITS);
	return 0;
}

static int itruncate_detach_all(struct inode *ip,
				struct inode_reclaim *reclaim)
{
	uint old_addrs[NDIRECT + 1];
	uint old_size = ip->size;
	uint deferred_slot = reclaim->deferred_slot;
	uint deferred_reserved = reclaim->deferred_reserved;
	uint storage_owner = reclaim->storage_owner;
	struct resource_account_handle storage_account =
		reclaim->storage_account;

	inode_mapping_require(ip, 1);

	memmove(old_addrs, ip->addrs, sizeof(old_addrs));
	reclaim->mode = INODE_RECLAIM_DIRECT;
	reclaim->dev = ip->dev;
	memmove(reclaim->direct, ip->addrs, sizeof(reclaim->direct));
	reclaim->indirect = ip->addrs[NDIRECT];
	memset(ip->addrs, 0, sizeof(ip->addrs));
	ip->size = 0;
	if (iupdate(ip) < 0) {
		memmove(ip->addrs, old_addrs, sizeof(old_addrs));
		ip->size = old_size;
		memset(reclaim, 0, sizeof(*reclaim));
		reclaim->deferred_slot = deferred_slot;
		reclaim->deferred_reserved = deferred_reserved;
		reclaim->storage_owner = storage_owner;
		reclaim->storage_account = storage_account;
		return -1;
	}
	if (fs_durable_barrier_forward() < 0)
		return -1;
	return 0;
}

static int itruncate_detach_partial(struct inode *ip, uint size,
				    struct inode_reclaim *reclaim)
{
	uint *blocks;
	struct resource_account_handle page_account;
	enum resource_charge_class page_charge_class;
	uint count = 0;
	uint first_discard = (size + BSIZE - 1) / BSIZE;
	uint old_addrs[NDIRECT + 1];
	uint old_size = ip->size;
	struct buf *bp;
	uint *entries;
	uint indirect_block = 0;
	uint first_indirect = 0;
	int clear_indirect_tail = 0;
	int result;

	inode_mapping_require(ip, 1);

	_Static_assert(MAXFILE * sizeof(uint) <= PGSIZE,
		       "truncate reclaim list must fit in one page");
	page_charge_class = ip->fs_owner_domain ==
				    fs_storage.public_principal_id ?
				    RESOURCE_CHARGE_ORDINARY :
				    RESOURCE_CHARGE_RESERVED;
	if (fs_storage_owner_account(ip->fs_owner_domain, &page_account) < 0) {
		if (!FS_OWNER_IS_SCOPE(ip->fs_owner_domain) ||
		    fs_storage_owner_account(FS_OWNER_SYSTEM, &page_account) < 0)
			return -1;
		page_charge_class = RESOURCE_CHARGE_RESERVED;
	}
	blocks = (uint *)kalloc_account_page(page_account, page_charge_class);
	if (blocks == 0)
		return -1;
	reclaim->mode = INODE_RECLAIM_LIST;
	reclaim->dev = ip->dev;
	reclaim->block_list = blocks;
	reclaim->page_account = page_account;
	reclaim->page_charge_class = page_charge_class;
	memmove(old_addrs, ip->addrs, sizeof(old_addrs));

	for (uint bn = first_discard; bn < NDIRECT; bn++) {
		if (ip->addrs[bn] != 0)
			blocks[count++] = ip->addrs[bn];
		ip->addrs[bn] = 0;
	}
	if (ip->addrs[NDIRECT] != 0) {
		if (fs_read_block(ip->dev, ip->addrs[NDIRECT], &bp) < 0)
			goto fail;
		entries = (uint *)bp->data;
		if (first_discard <= NDIRECT) {
			reclaim->indirect = ip->addrs[NDIRECT];
			ip->addrs[NDIRECT] = 0;
			for (uint i = 0; i < NINDIRECT; i++)
				if (entries[i] != 0)
					blocks[count++] = entries[i];
		} else {
			first_indirect = first_discard - NDIRECT;
			indirect_block = ip->addrs[NDIRECT];
			for (uint i = first_indirect; i < NINDIRECT; i++) {
				if (entries[i] != 0)
					blocks[count++] = entries[i];
			}
			clear_indirect_tail = 1;
		}
		brelse(bp);
	}

	/*
	 * Publish the shorter EOF before clearing retained indirect entries.
	 * A reset can therefore leave harmless mappings beyond EOF, never a hole
	 * inside the durable file size.
	 */
	ip->size = size;
	result = iupdate(ip);
	if (result < 0)
		goto fail;
	result = fs_durable_barrier_forward();
	if (result < 0)
		goto fail;
	if (clear_indirect_tail) {
		for (;;) {
			result = fs_read_block(ip->dev, indirect_block, &bp);
			if (result == VIRTIO_DISK_ERR_BUSY) {
				if (fs_forward_checkpoint() < 0)
					goto fail_published;
				continue;
			}
			if (result < 0)
				goto fail_published;
			entries = (uint *)bp->data;
			for (uint i = first_indirect; i < NINDIRECT; i++)
				entries[i] = 0;
			result = fs_write_metadata_block(bp);
			brelse(bp);
			if (result != VIRTIO_DISK_ERR_BUSY)
				break;
			if (fs_forward_checkpoint() < 0)
				goto fail_published;
		}
		if (result < 0)
			goto fail_published;
		if (fs_durable_barrier_forward() < 0)
			goto fail_published;
	}
	reclaim->block_count = count;
	return 0;

fail_published:
	/* The durable shorter EOF remains valid even if tail cleanup failed. */
	(void)kfree_account_page((char *)reclaim->block_list,
				 reclaim->page_account,
				 reclaim->page_charge_class);
	memset(reclaim, 0, sizeof(*reclaim));
	return fs_io_fail(FS_FAILURE_OPERATION);

fail:
	memmove(ip->addrs, old_addrs, sizeof(old_addrs));
	ip->size = old_size;
	(void)kfree_account_page((char *)reclaim->block_list,
				 reclaim->page_account,
				 reclaim->page_charge_class);
	memset(reclaim, 0, sizeof(*reclaim));
	return fs_io_fail(FS_FAILURE_OPERATION);
}

// Atomically remove discarded mappings. The caller owns reclaim after success
// and must release it even if process teardown is requested.
int itruncate_detach(struct inode *ip, const struct vfs_cred *cred, uint size,
			 struct inode_reclaim *reclaim)
{
	int bypass_entered = 0;
	int result = -1;

	if (ip == 0 || reclaim == 0)
		return -1;
	memset(reclaim, 0, sizeof(*reclaim));
	if (inode_mapping_write_lock(ip, 0) < 0)
		return -1;
	if (!vfs_inode_authorize(ip, cred, VFS_OP_TRUNCATE) ||
	    !exec_policy_inode_mutable(ip))
		goto out;
	if (size > ip->size)
		goto out;
	if (size == ip->size) {
		result = 0;
		goto out;
	}
	if (fs_claim_sponsored_public_inode(ip, cred) < 0)
		goto out;
#ifndef FS_ALLOCATOR_FAULT_TEST_PROFILE
	if (size == 0) {
		reclaim->dev = ip->dev;
		reclaim->storage_owner = ip->fs_owner_domain;
		if (fs_storage_owner_account(reclaim->storage_owner,
					     &reclaim->storage_account) < 0 ||
		    fs_deferred_reclaim_reserve(bio_current_owner(),
						 reclaim) < 0)
			goto out;
		result = itruncate_detach_all(ip, reclaim);
		if (result < 0) {
			fs_deferred_reclaim_cancel(reclaim);
			goto out;
		}
		if (fs_deferred_reclaim_publish(reclaim) < 0)
			panic("truncate reclaim publish");
		result = 0;
		goto out;
	}
#endif
	if (fs_epoch_destructive_begin(&bypass_entered) < 0)
		goto out;
	result = size == 0 ? itruncate_detach_all(ip, reclaim) :
			     itruncate_detach_partial(ip, size, reclaim);
	fs_epoch_destructive_end(bypass_entered);
	out:
	inode_mapping_write_unlock(ip);
	return result;
}

static void itruncate_reclaim_finish(struct inode_reclaim *reclaim)
{
	if (reclaim->mode == INODE_RECLAIM_LIST && reclaim->block_list != 0)
		(void)kfree_account_page((char *)reclaim->block_list,
					 reclaim->page_account,
					 reclaim->page_charge_class);
	memset(reclaim, 0, sizeof(*reclaim));
}

// Drain at most max_units detached block-map entries. No inode or buffer is
// retained between calls, so a scheduler-owned cleanup job can yield on I/O
// debt and resume without replaying a destructive operation.
int itruncate_reclaim_step(struct inode_reclaim *reclaim, uint max_units)
{
	uint units = 0;

	if (reclaim == 0)
		return -1;
	if (reclaim->mode == INODE_RECLAIM_NONE)
		return 1;
	if (reclaim->mode != INODE_RECLAIM_DIRECT &&
	    reclaim->mode != INODE_RECLAIM_LIST)
		panic("invalid inode reclaim mode");
	if (max_units == 0)
		max_units = 1;
	while (units < max_units) {
		uint block = 0;
		int have_unit = 0;
		int advance = 0;

		if (reclaim->mode == INODE_RECLAIM_DIRECT) {
			if (reclaim->direct_cursor < NDIRECT) {
				block = reclaim->direct[reclaim->direct_cursor];
				have_unit = 1;
				advance = 1;
			} else if (reclaim->indirect != 0 &&
				   reclaim->indirect_cursor < NINDIRECT) {
				struct buf *bp;

				if (fs_read_block(reclaim->dev, reclaim->indirect,
						  &bp) < 0)
					return -1;
				block = ((uint *)bp->data)[reclaim->indirect_cursor];
				brelse(bp);
				have_unit = 1;
				advance = 2;
			} else if (reclaim->indirect != 0) {
				block = reclaim->indirect;
				have_unit = 1;
				advance = 3;
			}
		} else {
			if (reclaim->block_cursor < reclaim->block_count) {
				block = reclaim->block_list[reclaim->block_cursor];
				have_unit = 1;
				advance = 4;
			} else if (reclaim->indirect != 0) {
				block = reclaim->indirect;
				have_unit = 1;
				advance = 3;
			}
		}
		if (!have_unit) {
			itruncate_reclaim_finish(reclaim);
			return 1;
		}
		if (truncate_free_block(reclaim->dev, block) < 0)
			return -1;
		if (advance == 1)
			reclaim->direct_cursor++;
		else if (advance == 2)
			reclaim->indirect_cursor++;
		else if (advance == 3)
			reclaim->indirect = 0;
		else if (advance == 4)
			reclaim->block_cursor++;
		units++;
		if (bio_checkpoint_should_stop(bio_request_checkpoint_cleanup()))
			return 0;
	}
	return 0;
}

int itruncate_reclaim(struct inode_reclaim *reclaim)
{
	while (reclaim != 0 && reclaim->mode != INODE_RECLAIM_NONE) {
		int result = itruncate_reclaim_step(reclaim, 1);

		if (result < 0) {
			itruncate_reclaim_finish(reclaim);
			return -1;
		}
	}
	return 0;
}

// Shrink an inode and release every whole block beyond the new end.
int itruncate(struct inode *ip, const struct vfs_cred *cred, uint size)
{
	struct inode_reclaim reclaim;

	if (itruncate_detach(ip, cred, size, &reclaim) < 0)
		return -1;
	return itruncate_reclaim(&reclaim);
}

// Truncate inode (discard contents).
int itrunc(struct inode *ip, const struct vfs_cred *cred)
{
	return itruncate(ip, cred, 0);
}

// Read data from inode.
// If user_dst==1, then dst is a user virtual address;
// otherwise, dst is a kernel address.
static int readi_atomic(struct inode *ip, const struct vfs_cred *cred,
			const struct open_file_io_token *lease,
			int user_dst, uint64 dst, uint off, uint n,
			int device_read)
{
	uint blocknos[FS_READ_BATCH_MAX];
	struct buf *buffers[FS_READ_BATCH_MAX];
	struct vm_copy_segment segments[FS_READ_BATCH_MAX];
	struct bio_checkpoint_result checkpoint;
	uint tot = 0;
	uint m;
	uint batch_bytes;
	uint batch_limit;
	uint batch_count;
	uint mapped;
	uint copied;
	uint span;
	int map_result;
	int mapping_failed;
	int failed = 0;
	int failure_result = -1;

	inode_mapping_require(ip, 0);

	if (fs_io_health != FS_IO_HEALTHY ||
	    (lease != 0 ?
	     !open_file_io_token_validate(lease, ip, VFS_OP_READ) :
	     !vfs_inode_authorize(ip, cred, VFS_OP_READ)))
		return -1;
	if (off > ip->size || off + n < off)
		return 0;
	if (off + n > ip->size)
		n = ip->size - off;

	batch_limit = device_read ? 1U : FS_READ_BATCH_MAX;
	while (tot < n) {
		span = off % BSIZE + n - tot;
		batch_count = span / BSIZE;
		if (span % BSIZE != 0)
			batch_count++;
		batch_count = MIN(batch_count, batch_limit);
		map_result = 0;
		mapped = bmap_read_batch(ip, off / BSIZE, batch_count,
					 blocknos, &map_result);
		mapping_failed = mapped != batch_count;
		batch_count = mapped;
		if (mapping_failed && map_result >= 0)
			map_result = -1;
		if (batch_count == 0) {
			failure_result = map_result;
			failed = 1;
			break;
		}
		if (device_read) {
			failure_result = fs_read_device_block(
				ip->dev, blocknos[0], &buffers[0]);
		} else if (batch_count == 1) {
			failure_result = fs_read_block(
				ip->dev, blocknos[0], &buffers[0]);
		} else {
			failure_result = fs_read_blocks_batch(ip->dev, blocknos, buffers,
						      batch_count);
			if (failure_result < 0) {
				batch_count = 1;
				mapping_failed = 0;
				failure_result = fs_read_block(ip->dev, blocknos[0],
							       &buffers[0]);
			}
		}
		if (failure_result < 0) {
			failed = 1;
			break;
		}

		batch_bytes = 0;
		for (copied = 0; copied < batch_count; copied++) {
			uint block_offset = copied == 0 ? off % BSIZE : 0;

			m = MIN(n - tot - batch_bytes, BSIZE - block_offset);
			segments[copied].source =
				(char *)buffers[copied]->data + block_offset;
			segments[copied].length = m;
			batch_bytes += m;
		}
		if ((batch_count == 1 &&
		     either_copyout(user_dst, dst, (char *)segments[0].source,
				    segments[0].length) < 0) ||
		    (batch_count > 1 &&
		     either_copyoutv(user_dst, dst, segments, batch_count) < 0)) {
			failure_result = -1;
			failed = 1;
		}
		copied = 0;
		while (copied < batch_count) {
			if (buffers[copied] != 0)
				brelse(buffers[copied]);
			copied++;
		}
		if (failed)
			break;
		tot += batch_bytes;
		off += batch_bytes;
		dst += batch_bytes;
		checkpoint = bio_request_checkpoint();
		if (bio_checkpoint_should_stop(checkpoint)) {
			failed = 1;
			break;
		}
		if (mapping_failed) {
			failure_result = map_result;
			failed = 1;
			break;
		}
	}
	if (failed && tot == 0)
		return failure_result;
	return tot;
}

static int readi_with_auth(struct inode *ip, const struct vfs_cred *cred,
			   const struct open_file_io_token *lease,
			   int user_dst, uint64 dst, uint off, uint n)
{
	int result;

	if (inode_mapping_read_lock(ip) < 0)
		return -1;
	result = readi_atomic(ip, cred, lease, user_dst, dst, off, n, 0);
	inode_mapping_read_unlock(ip);
	return result;
}

int readi(struct inode *ip, const struct vfs_cred *cred, int user_dst,
	  uint64 dst, uint off, uint n)
{
	return readi_with_auth(ip, cred, 0, user_dst, dst, off, n);
}

int readi_lease(struct inode *ip, const struct vfs_cred *cred,
		const struct open_file_io_token *lease, int user_dst,
		uint64 dst, uint off, uint n)
{
	if (lease == 0)
		return -1;
	return readi_with_auth(ip, cred, lease, user_dst, dst, off, n);
}

int readi_device(struct inode *ip, const struct vfs_cred *cred, int user_dst,
		 uint64 dst, uint off, uint n)
{
	int result;

	if (inode_mapping_read_lock(ip) < 0)
		return -1;
	result = readi_atomic(ip, cred, 0, user_dst, dst, off, n, 1);
	inode_mapping_read_unlock(ip);
	return result;
}

// Write data to inode.
// Caller must hold ip->lock.
// If user_src==1, then src is a user virtual address;
// otherwise, src is a kernel address.
// Returns the number of bytes successfully written.
// If the return value is less than the requested n,
// there was an error of some kind.
static int writei_charged_locked(struct inode *ip,
				 const struct vfs_cred *cred,
				 const struct fs_storage_charge *charge,
				 const struct open_file_io_token *lease,
				 int user_src, uint64 src, uint off, uint n,
				 enum fs_epoch_phase namespace_phase)
{
	struct fs_storage_charge object_charge;
	const struct fs_storage_charge *allocation_charge = charge;
	uint tot, m, addr, bn;
	struct buf *bp;
	int allocated;
	struct bio_checkpoint_result checkpoint;
	int failed = 0;
	int inode_changed = 0;
	int failure_result = -1;

	inode_mapping_require(ip, 1);

	if (fs_io_health != FS_IO_HEALTHY ||
	    (lease != 0 ?
	     !open_file_io_token_validate(lease, ip, VFS_OP_WRITE) :
	     !vfs_inode_authorize(ip, cred, VFS_OP_WRITE)))
		return -1;
	if (n != 0 && !exec_policy_inode_mutable(ip))
		return -1;
	if (off > ip->size || off + n < off)
		return -1;
	if (off + n > MAXFILE * BSIZE)
		return -1;
	if (n != 0 && ip->type == T_FILE) {
		if (fs_claim_sponsored_public_inode(ip, cred) < 0 ||
		    fs_storage_charge_from_owner(ip->fs_owner_domain,
						 &object_charge) < 0)
			return -1;
		allocation_charge = &object_charge;
	}

	for (tot = 0; tot < n; tot += m, off += m, src += m) {
		struct bio_overwrite_receipt overwrite =
			BIO_OVERWRITE_RECEIPT_INIT;
		int full_overwrite;

		m = MIN(n - tot, BSIZE - off % BSIZE);
		full_overwrite = ip->type != T_DIR && (off % BSIZE) == 0 &&
				 m == BSIZE;
		if ((ip->type == T_DIR ?
		     fs_epoch_preflight_phase(2, namespace_phase) :
		     fs_epoch_preflight(2)) < 0) {
			failure_result = -1;
			failed = 1;
			break;
		}
		bn = off / BSIZE;
		addr = bmap(ip, bn, 0, 0, &failure_result);
		allocated = 0;
		if (addr == 0) {
			addr = bmap(ip, bn, 1, allocation_charge,
				    &failure_result);
			if (addr == 0) {
				if (failure_result >= 0)
					failure_result = -1;
				failed = 1;
				break;
			}
			allocated = 1;
		}
		bp = 0;
		if (full_overwrite) {
			failure_result = bprepare_overwrite(ip->dev, addr,
							      &overwrite);
			if (failure_result == BIO_OVERWRITE_FALLBACK)
				failure_result = fs_read_block(ip->dev, addr, &bp);
			else if (failure_result == VIRTIO_DISK_OK)
				bp = overwrite.buf;
			else {
				bcancel_overwrite(&overwrite);
				failure_result = fs_io_fail(
					failure_result == VIRTIO_DISK_ERR_BUSY ?
					FS_FAILURE_SCHEDULING_UNAVAILABLE :
					FS_FAILURE_TRANSIENT_READ);
			}
		} else {
			failure_result = fs_read_block(ip->dev, addr, &bp);
		}
		if (failure_result < 0) {
			if (allocated)
				(void)bmap_discard(ip, bn);
			failed = 1;
			break;
		}
		if (either_copyin(user_src, src,
				  (char *)bp->data + (off % BSIZE), m) == -1) {
			if (overwrite.active)
				bcancel_overwrite(&overwrite);
			else
				brelse(bp);
			if (allocated)
				(void)bmap_discard(ip, bn);
			failure_result = -1;
			failed = 1;
			break;
		}
		if (overwrite.active) {
			failure_result = bpublish_overwrite(&overwrite, m, &bp);
			if (failure_result == VIRTIO_DISK_ERR_BUSY) {
				bcancel_overwrite(&overwrite);
				failure_result = fs_read_block(ip->dev, addr, &bp);
				if (failure_result >= 0 &&
				    either_copyin(user_src, src,
						  (char *)bp->data, m) == -1) {
					brelse(bp);
					bp = 0;
					failure_result = -1;
				}
			} else if (failure_result < 0) {
				bcancel_overwrite(&overwrite);
				failure_result =
					fs_io_fail(FS_FAILURE_TRANSIENT_READ);
			}
			if (failure_result < 0) {
				if (allocated)
					(void)bmap_discard(ip, bn);
				failed = 1;
				break;
			}
		}
		failure_result = ip->type == T_DIR ?
			fs_write_namespace_block(bp, namespace_phase) :
			fs_write_data_block(bp);
		if (failure_result < 0) {
			brelse(bp);
			if (allocated)
				(void)bmap_discard(ip, bn);
			failed = 1;
			break;
		}
		brelse(bp);
		if (allocated)
			inode_changed = 1;
		checkpoint = bio_request_checkpoint();
		if (bio_checkpoint_should_stop(checkpoint)) {
			tot += m;
			off += m;
			failed = 1;
			break;
		}
	}
	if (off > ip->size) {
		ip->size = off;
		inode_changed = 1;
	}

	// Existing-block overwrites do not change inode metadata. Avoid turning
	// every small data write into an unrelated inode-table write.
	if (inode_changed && iupdate(ip) < 0)
		return -1;

	if (failed && tot == 0)
		return failure_result;
	return tot;
}

static int writei_charged(struct inode *ip, const struct vfs_cred *cred,
			  const struct fs_storage_charge *charge,
			  int user_src, uint64 src, uint off, uint n,
			  enum fs_epoch_phase namespace_phase)
{
	int result;

	if (inode_mapping_write_lock(ip, 0) < 0)
		return -1;
	result = writei_charged_locked(ip, cred, charge, 0, user_src, src, off,
				       n, namespace_phase);
	inode_mapping_write_unlock(ip);
	return result;
}

static int writei_with_auth(struct inode *ip, const struct vfs_cred *cred,
			    const struct open_file_io_token *lease,
			    int user_src, uint64 src, uint off, uint n)
{
	struct fs_storage_charge charge;
	int namespace_locked = 0;
	int result;

	if (fs_storage_charge_from_vfs(cred, &charge) < 0)
		return -1;
	if (ip != 0 && ip->type == T_DIR) {
		if (fs_namespace_gate_lock() < 0)
			return -1;
		namespace_locked = 1;
	}
	if (inode_mapping_write_lock(ip, 0) < 0) {
		if (namespace_locked)
			fs_namespace_gate_unlock();
		return -1;
	}
	bio_fs_atomic_enter();
	result = writei_charged_locked(ip, cred, &charge, lease, user_src, src, off,
				       n, FS_EPOCH_NAMESPACE_ATTACH);
	bio_fs_atomic_leave();
	inode_mapping_write_unlock(ip);
	if (namespace_locked) {
		if (n != 0)
			fs_dentry_index_invalidate_directory(ip);
		fs_namespace_gate_unlock();
	}
	return result;
}

int writei(struct inode *ip, const struct vfs_cred *cred, int user_src,
	   uint64 src, uint off, uint n)
{
	return writei_with_auth(ip, cred, 0, user_src, src, off, n);
}

int writei_lease(struct inode *ip, const struct vfs_cred *cred,
		 const struct open_file_io_token *lease, int user_src,
		 uint64 src, uint off, uint n)
{
	if (lease == 0)
		return -1;
	return writei_with_auth(ip, cred, lease, user_src, src, off, n);
}

/*
 * Grow an inode into a fully mapped, zero-filled extent without depending on
 * later data writes to allocate filesystem metadata.  Publishing each newly
 * mapped block with the corresponding size makes an interrupted preparation
 * resumable on the next boot instead of leaving a sparse-looking inode.
 */
int
fs_preallocate_inode(struct inode *ip, const struct vfs_cred *cred, uint size)
{
	struct fs_storage_charge charge;
	uint blocks;
	uint start_block;
	int result = -1;

	if (ip == 0 || cred == 0 || size > MAXFILE * BSIZE ||
	    !vfs_inode_authorize(ip, cred, VFS_OP_WRITE) ||
	    !exec_policy_inode_mutable(ip) ||
	    fs_storage_charge_from_vfs(cred, &charge) < 0)
		return -1;
	if (inode_mapping_write_lock(ip, 0) < 0)
		return -1;
	blocks = (size + BSIZE - 1) / BSIZE;
	start_block = MIN(blocks, (ip->size + BSIZE - 1) / BSIZE);
	for (uint bn = start_block; bn < blocks; bn++) {
		uint addr;
		uint published_size = MIN(size, (bn + 1) * BSIZE);
		struct bio_checkpoint_result checkpoint;
		int inode_changed = 0;
		int map_result;

		bio_fs_atomic_enter();
		addr = bmap(ip, bn, 0, 0, &map_result);
		if (addr == 0) {
			if (map_result < 0) {
				result = map_result;
				bio_fs_atomic_leave();
				goto out;
			}
			addr = bmap(ip, bn, 1, &charge, &map_result);
			if (addr == 0) {
				result = map_result < 0 ? map_result :
					 fs_io_fail(FS_FAILURE_OPERATION);
				bio_fs_atomic_leave();
				goto out;
			}
			inode_changed = 1;
		}
		if (ip->size < published_size) {
			ip->size = published_size;
			inode_changed = 1;
		}
		if (inode_changed && (result = iupdate(ip)) < 0) {
			bio_fs_atomic_leave();
			goto out;
		}
		bio_fs_atomic_leave();
		checkpoint = bio_request_checkpoint();
		if (bio_checkpoint_should_stop(checkpoint)) {
			result = checkpoint.state == BIO_CHECKPOINT_DEFERRED ?
				VIRTIO_DISK_ERR_BUSY : -1;
			goto out;
		}
	}
	if (ip->size < size) {
		bio_fs_atomic_enter();
		ip->size = size;
		result = iupdate(ip);
		if (result < 0) {
			bio_fs_atomic_leave();
			goto out;
		}
		bio_fs_atomic_leave();
	}
	result = 0;
out:
	inode_mapping_write_unlock(ip);
	return result;
}

int fs_dirent_canonicalize(const char *input, char out[DIRSIZ + 1])
{
	uint i;

	if (input == 0 || out == 0 || input[0] == 0)
		return -1;
	memset(out, 0, DIRSIZ + 1);
	for (i = 0; i < DIRSIZ && input[i] != 0; i++)
		out[i] = input[i];
	return 0;
}

#define DIR_SCAN_BATCH_ENTRIES 8U

struct dir_scan_candidate {
	uint offset;
	struct dirent entry;
};

struct dir_scan_batch {
	uint count;
	uint scanned;
	uint next_offset;
	struct dir_scan_candidate candidates[DIR_SCAN_BATCH_ENTRIES];
};

static struct dir_scan_batch fs_dentry_rebuild_batch;

_Static_assert(BSIZE % sizeof(struct dirent) == 0,
	       "directory entries must not cross filesystem blocks");
_Static_assert(sizeof(struct dir_scan_batch) <= 256,
	       "directory scan batch must stay stack bounded");

/*
 * Copy only the interesting entries from one directory block.  Target inode
 * validation happens after the buffer is released, so a cache holder is never
 * carried across an operation that may issue another disk request.
 */
static int dir_scan_fill(struct inode *dp, const char *key, uint start,
			 int *first_empty, struct dir_scan_batch *batch)
{
	struct buf *bp = 0;
	struct dirent de;
	uint addr;
	uint block_end;
	uint block_start;
	uint off;
	int map_result = -1;
	int result = -1;

	if (dp == 0 || batch == 0 ||
	    start % sizeof(struct dirent) != 0)
		return -1;
	if (inode_mapping_read_lock(dp) < 0)
		return -1;
	if (start >= dp->size || dp->size % sizeof(struct dirent) != 0)
		goto unlock;
	memset(batch, 0, sizeof(*batch));
	batch->next_offset = start;
	block_start = start - start % BSIZE;
	block_end = MIN(block_start + BSIZE, dp->size);

	addr = bmap(dp, block_start / BSIZE, 0, 0, &map_result);
	if (addr == 0) {
		result = map_result < 0 ? map_result : -1;
		goto out;
	}
	result = fs_read_block(dp->dev, addr, &bp);
	if (result < 0)
		goto out;
	for (off = start; off < block_end; off += sizeof(de)) {
		memmove(&de, bp->data + off - block_start, sizeof(de));
		batch->scanned++;
		batch->next_offset = off + sizeof(de);
		if (de.inum == 0) {
			if (first_empty != 0 && *first_empty < 0)
				*first_empty = (int)off;
			continue;
		}
		if (key != 0 && strncmp(key, de.name, DIRSIZ) != 0)
			continue;
		batch->candidates[batch->count].offset = off;
		batch->candidates[batch->count].entry = de;
		batch->count++;
		if (batch->count == DIR_SCAN_BATCH_ENTRIES)
			break;
	}
	kernel_performance_directory_probe(batch->scanned);
	result = 0;
out:
	if (bp != 0)
		brelse(bp);
	inode_mapping_read_unlock(dp);
	return result;
unlock:
	inode_mapping_read_unlock(dp);
	return result;
}

static int dir_scan_checkpoint(uint scanned)
{
	struct bio_checkpoint_result checkpoint = bio_request_checkpoint();

	if (bio_checkpoint_should_stop(checkpoint))
		return checkpoint.state == BIO_CHECKPOINT_DEFERRED ?
			FS_LOOKUP_BUSY : FS_LOOKUP_ERROR;
	return kernel_work_checkpoint(scanned) < 0 ? FS_LOOKUP_ERROR : 0;
}

static int
fs_dentry_gate_owned(void)
{
	struct thread *self = curr_thread();
	void *token;
	uint64 generation;

	if (self != 0 && self->identity_generation != 0) {
		token = self;
		generation = self->identity_generation;
	} else {
		token = &fs_dentry_boot_token;
		generation = 0;
	}
	return fs_dentry_owner == token &&
	       fs_dentry_owner_generation == generation;
}

static int
fs_dentry_gate_lock(void)
{
	struct thread *self = curr_thread();
	void *token;
	uint64 generation;
	int enabled;

	if (self != 0 && self->identity_generation != 0) {
		token = self;
		generation = self->identity_generation;
	} else {
		token = &fs_dentry_boot_token;
		generation = 0;
	}
	enabled = intr_save();
	for (;;) {
		if (fs_dentry_owner == 0) {
			fs_dentry_owner = token;
			fs_dentry_owner_generation = generation;
			intr_restore(enabled);
			return 0;
		}
		if (fs_dentry_gate_owned())
			panic("dentry gate recursion");
		if (token == &fs_dentry_boot_token) {
			intr_restore(enabled);
			return -1;
		}
		if (wait_queue_sleep_irq(&fs_dentry_waiters) != WAIT_QUEUE_OK) {
			intr_restore(enabled);
			return -1;
		}
	}
}

static void
fs_dentry_gate_unlock(void)
{
	int enabled = intr_save();

	if (!fs_dentry_gate_owned())
		panic("dentry gate owner");
	fs_dentry_owner = 0;
	fs_dentry_owner_generation = 0;
	wait_queue_wake_one(&fs_dentry_waiters);
	intr_restore(enabled);
}

static void
fs_dentry_gate_lock_uninterruptible(void)
{
	struct thread *self = curr_thread();
	void *token;
	uint64 generation;
	int enabled;

	if (self != 0 && self->identity_generation != 0) {
		token = self;
		generation = self->identity_generation;
	} else {
		token = &fs_dentry_boot_token;
		generation = 0;
	}
	enabled = intr_save();
	for (;;) {
		if (fs_dentry_owner == 0) {
			fs_dentry_owner = token;
			fs_dentry_owner_generation = generation;
			intr_restore(enabled);
			return;
		}
		if (fs_dentry_gate_owned())
			panic("dentry gate recursion");
		if (token == &fs_dentry_boot_token)
			panic("boot dentry gate contention");
		(void)wait_queue_sleep_irq_uninterruptible(&fs_dentry_waiters);
	}
}

static void
fs_dentry_gate_require(void)
{
	if (!fs_dentry_gate_owned())
		panic("dentry index outside gate");
}

static int
fs_namespace_gate_owned(void)
{
	struct thread *self = curr_thread();
	void *token;
	uint64 generation;

	if (self != 0 && self->identity_generation != 0) {
		token = self;
		generation = self->identity_generation;
	} else {
		token = &fs_dentry_boot_token;
		generation = 0;
	}
	return fs_namespace_owner == token &&
	       fs_namespace_owner_generation == generation;
}

static int
fs_namespace_gate_lock(void)
{
	struct thread *self = curr_thread();
	void *token;
	uint64 generation;
	int enabled;

	if (self != 0 && self->identity_generation != 0) {
		token = self;
		generation = self->identity_generation;
	} else {
		token = &fs_dentry_boot_token;
		generation = 0;
	}
	enabled = intr_save();
	for (;;) {
		if (fs_namespace_owner == 0) {
			fs_namespace_owner = token;
			fs_namespace_owner_generation = generation;
			intr_restore(enabled);
			return 0;
		}
		if (fs_namespace_gate_owned())
			panic("namespace gate recursion");
		if (token == &fs_dentry_boot_token) {
			intr_restore(enabled);
			return -1;
		}
		if (wait_queue_sleep_irq(&fs_namespace_waiters) !=
		    WAIT_QUEUE_OK) {
			intr_restore(enabled);
			return -1;
		}
	}
}

static void
fs_namespace_gate_unlock(void)
{
	int enabled = intr_save();

	if (!fs_namespace_gate_owned())
		panic("namespace gate owner");
	fs_namespace_owner = 0;
	fs_namespace_owner_generation = 0;
	wait_queue_wake_one(&fs_namespace_waiters);
	intr_restore(enabled);
}

static uint
fs_dentry_hash(struct inode *dp, const char key[DIRSIZ + 1])
{
	uint hash = 2166136261U;
	uint identity[3] = { dp->dev, dp->inum, dp->vfs_incarnation };

	for (uint i = 0; i < 3; i++) {
		uint value = identity[i];

		for (uint byte = 0; byte < sizeof(value); byte++) {
			hash ^= value & 0xffU;
			hash *= 16777619U;
			value >>= 8;
		}
	}
	for (uint i = 0; i < DIRSIZ; i++) {
		hash ^= (uchar)key[i];
		hash *= 16777619U;
	}
	return hash ? hash : 1;
}

static void
fs_dentry_index_bump(void)
{
	fs_dentry_gate_require();
	fs_dentry_index_generation++;
	if (fs_dentry_index_generation == 0)
		fs_dentry_index_generation = 1;
}

static void
fs_dentry_index_reset_all(void)
{
	fs_dentry_gate_require();
	fs_dentry_index_bump();
	memset(fs_dentry_index, 0, sizeof(fs_dentry_index));
	fs_dentry_index_used = 0;
	fs_dentry_index_tombstones = 0;
	for (uint i = 0; i < FS_DIRECTORY_INDEX_CAP; i++) {
		struct fs_directory_index_state *state =
			&fs_directory_indexes[i];

		if (!state->used)
			continue;
		state->complete = 0;
		state->overflow = 0;
		state->entries = 0;
		state->first_free_entry = 0;
		memset(state->occupied, 0, sizeof(state->occupied));
	}
}

static void
fs_directory_index_discard(struct fs_directory_index_state *state)
{
	fs_dentry_gate_require();
	if (state == 0 || !state->used)
		return;
	fs_dentry_index_bump();
	for (uint slot = 0; slot < FS_DENTRY_INDEX_CAP; slot++) {
		struct fs_dentry_index_entry *entry = &fs_dentry_index[slot];

		if (entry->state != FS_DENTRY_INDEX_USED ||
		    entry->dev != state->dev ||
		    entry->dir_inum != state->inum ||
		    entry->dir_incarnation != state->incarnation)
			continue;
		entry->state = FS_DENTRY_INDEX_TOMBSTONE;
		if (fs_dentry_index_used == 0)
			panic("dentry index used underflow");
		fs_dentry_index_used--;
		fs_dentry_index_tombstones++;
	}
	state->complete = 0;
	state->overflow = 0;
	state->entries = 0;
	state->first_free_entry = 0;
	memset(state->occupied, 0, sizeof(state->occupied));
	if (fs_dentry_index_tombstones > FS_DENTRY_INDEX_CAP / 2)
		fs_dentry_index_reset_all();
}

static struct fs_directory_index_state *
fs_directory_index_find(struct inode *dp, int create)
{
	struct fs_directory_index_state *free_state = 0;

	fs_dentry_gate_require();
	if (dp == 0 || dp->dev > 65535U || dp->inum > 65535U)
		return 0;
	for (uint i = 0; i < FS_DIRECTORY_INDEX_CAP; i++) {
		struct fs_directory_index_state *state =
			&fs_directory_indexes[i];

		if (!state->used) {
			if (free_state == 0)
				free_state = state;
			continue;
		}
		if (state->dev != dp->dev || state->inum != dp->inum)
			continue;
		if (state->incarnation == dp->vfs_incarnation &&
		    state->size == dp->size)
			return state;
		fs_directory_index_discard(state);
		memset(state, 0, sizeof(*state));
		free_state = state;
		break;
	}
	if (!create)
		return 0;
	if (free_state == 0) {
		free_state = &fs_directory_indexes[fs_directory_index_cursor];
		fs_directory_index_cursor =
			(fs_directory_index_cursor + 1) %
			FS_DIRECTORY_INDEX_CAP;
		fs_directory_index_discard(free_state);
		memset(free_state, 0, sizeof(*free_state));
	}
	free_state->used = 1;
	free_state->dev = dp->dev;
	free_state->inum = dp->inum;
	free_state->incarnation = dp->vfs_incarnation;
	free_state->size = dp->size;
	return free_state;
}

static int
fs_dentry_index_probe(struct inode *dp, uint hash, uint offset,
		      uint *slot_out, int *found_out)
{
	uint first_tombstone = FS_DENTRY_INDEX_CAP;

	fs_dentry_gate_require();
	if (slot_out == 0 || found_out == 0)
		return -1;
	for (uint scanned = 0; scanned < FS_DENTRY_INDEX_CAP; scanned++) {
		uint slot = (hash + scanned) & (FS_DENTRY_INDEX_CAP - 1);
		struct fs_dentry_index_entry *entry = &fs_dentry_index[slot];

		if (entry->state == FS_DENTRY_INDEX_EMPTY) {
			*slot_out = first_tombstone < FS_DENTRY_INDEX_CAP ?
				first_tombstone : slot;
			*found_out = 0;
			return 0;
		}
		if (entry->state == FS_DENTRY_INDEX_TOMBSTONE) {
			if (first_tombstone == FS_DENTRY_INDEX_CAP)
				first_tombstone = slot;
			continue;
		}
		if (entry->hash == hash && entry->dev == dp->dev &&
		    entry->dir_inum == dp->inum &&
		    entry->dir_incarnation == dp->vfs_incarnation &&
		    entry->offset == offset) {
			*slot_out = slot;
			*found_out = 1;
			return 0;
		}
	}
	if (first_tombstone < FS_DENTRY_INDEX_CAP) {
		*slot_out = first_tombstone;
		*found_out = 0;
		return 0;
	}
	return -1;
}

static int
fs_dentry_index_insert(struct inode *dp, const char key[DIRSIZ + 1],
		       uint offset, uint target_inum,
		       uint target_incarnation, int replace)
{
	struct fs_dentry_index_entry *entry;
	uint hash;
	uint slot;
	int found;

	fs_dentry_gate_require();
	hash = fs_dentry_hash(dp, key);
	if (target_inum == 0 || target_inum > 65535U ||
	    fs_dentry_index_probe(dp, hash, offset, &slot, &found) < 0)
		return -1;
	entry = &fs_dentry_index[slot];
	if (found && !replace)
		return -2;
	if (!found) {
		if (entry->state == FS_DENTRY_INDEX_TOMBSTONE) {
			if (fs_dentry_index_tombstones == 0)
				panic("dentry tombstone underflow");
			fs_dentry_index_tombstones--;
		}
		fs_dentry_index_used++;
	}
	memset(entry, 0, sizeof(*entry));
	entry->hash = hash;
	entry->offset = offset;
	entry->dir_incarnation = dp->vfs_incarnation;
	entry->target_incarnation = target_incarnation;
	entry->dev = (ushort)dp->dev;
	entry->dir_inum = (ushort)dp->inum;
	entry->target_inum = (ushort)target_inum;
	entry->state = FS_DENTRY_INDEX_USED;
	fs_dentry_index_bump();
	return 0;
}

static void
fs_directory_index_occupied_set(struct fs_directory_index_state *state,
				uint offset, int occupied)
{
	uint entry = offset / sizeof(struct dirent);
	uint limit;

	fs_dentry_gate_require();
	if (state == 0 || entry >= FS_DIRECTORY_INDEX_MAX_ENTRIES ||
	    offset % sizeof(struct dirent) != 0)
		panic("directory occupancy index");
	limit = state->size / sizeof(struct dirent);
	if (entry >= limit)
		panic("directory occupancy range");
	if (occupied) {
		state->occupied[entry / 8] |= 1U << (entry % 8);
		if (entry == state->first_free_entry)
			while (state->first_free_entry < limit &&
			       (state->occupied[state->first_free_entry / 8] &
				(1U << (state->first_free_entry % 8))) != 0)
				state->first_free_entry++;
	} else {
		state->occupied[entry / 8] &= ~(1U << (entry % 8));
		if (entry < state->first_free_entry)
			state->first_free_entry = entry;
	}
}

static int
fs_directory_index_first_empty(struct fs_directory_index_state *state)
{
	uint entry;
	uint entries;

	fs_dentry_gate_require();
	if (state == 0 || !state->complete)
		return -1;
	entries = state->size / sizeof(struct dirent);
	entry = state->first_free_entry;
	if (entry >= entries ||
	    (state->occupied[entry / 8] & (1U << (entry % 8))) != 0)
		return -1;
	return (int)(entry * sizeof(struct dirent));
}

static int
fs_directory_index_rebuild(struct inode *dp)
{
	struct fs_directory_index_state *state;
	struct dir_scan_batch *batch = &fs_dentry_rebuild_batch;
	uint incarnation;
	uint size;
	uint off = 0;
	int result = -1;

	fs_dentry_gate_require();
	if (dp == 0 || dp->type != T_DIR ||
	    dp->size > MAXFILE * BSIZE ||
	    dp->size % sizeof(struct dirent) != 0)
		return -1;
	state = fs_directory_index_find(dp, 1);
	if (state == 0)
		return -1;
	fs_directory_index_discard(state);
	state->dev = dp->dev;
	state->inum = dp->inum;
	incarnation = dp->vfs_incarnation;
	size = dp->size;
	state->incarnation = incarnation;
	state->size = size;
	state->first_free_entry = 0;
	if (size / sizeof(struct dirent) >
	    FS_DIRECTORY_INDEX_MAX_ENTRIES) {
		state->overflow = 1;
		state->complete = 1;
		return 0;
	}
	while (off < dp->size) {
		if (dir_scan_fill(dp, 0, off, 0, batch) < 0)
			goto fail;
		off = batch->next_offset;
		for (uint i = 0; i < batch->count; i++) {
			struct dir_scan_candidate *candidate =
				&batch->candidates[i];
			struct inode *target;
			char key[DIRSIZ + 1];

			if (candidate->entry.inum == 0 ||
			    candidate->entry.inum >= sb.ninodes ||
			    candidate->entry.name[0] == 0)
				goto fail;
			memset(key, 0, sizeof(key));
			memmove(key, candidate->entry.name, DIRSIZ);
			target = inode_get(dp->dev, candidate->entry.inum);
			if (target == 0)
				goto fail;
			result = ivalid(target);
			if (result < 0 || !vfs_inode_label_valid(target)) {
				iput(target);
				goto fail;
			}
			if (!state->overflow)
				result = fs_dentry_index_insert(
					dp, key, candidate->offset,
					candidate->entry.inum,
					target->vfs_incarnation, 0);
			else
				result = 0;
			iput(target);
			if (result == -2)
				goto fail;
			if (result < 0)
				state->overflow = 1;
			fs_directory_index_occupied_set(
				state, candidate->offset, 1);
			state->entries++;
		}
		result = dir_scan_checkpoint(batch->scanned);
		if (result < 0)
			goto fail;
	}
	if (dp->vfs_incarnation != incarnation || dp->size != size ||
	    state->dev != dp->dev || state->inum != dp->inum)
		goto fail;
	state->complete = 1;
	return 0;

fail:
	fs_directory_index_discard(state);
	return result < 0 ? result : -1;
}

static int
fs_directory_index_prepare(struct inode *dp,
			   struct fs_directory_index_state **out)
{
	struct fs_directory_index_state *state;
	int result;

	fs_dentry_gate_require();
	if (out == 0)
		return -1;
	*out = 0;
	state = fs_directory_index_find(dp, 0);
	if (state == 0 || !state->complete) {
		result = fs_directory_index_rebuild(dp);
		if (result < 0)
			return result;
		state = fs_directory_index_find(dp, 0);
	}
	if (state == 0 || !state->complete)
		return -1;
	*out = state;
	return state->overflow ? 0 : 1;
}

static void
fs_directory_index_invalidate(struct inode *dp)
{
	fs_dentry_gate_require();
	struct fs_directory_index_state *state =
		fs_directory_index_find(dp, 0);

	if (state != 0)
		fs_directory_index_discard(state);
}

#define FS_DENTRY_LOCATE_FALLBACK 0
#define FS_DENTRY_LOCATE_HIT 1
#define FS_DENTRY_LOCATE_ABSENT 2

static int
fs_dentry_index_snapshot_begin(struct inode *dp, uint64 *generation)
{
	struct fs_directory_index_state *state;
	int result;

	fs_dentry_gate_require();
	if (generation == 0)
		return -1;
	result = fs_directory_index_prepare(dp, &state);
	if (result <= 0)
		return FS_DENTRY_LOCATE_FALLBACK;
	*generation = fs_dentry_index_generation;
	return 1;
}

static int
fs_dentry_index_snapshot_next(struct inode *dp, uint hash,
			      uint64 generation, uint *cursor,
			      struct fs_dentry_index_entry *out)
{
	fs_dentry_gate_require();
	if (cursor == 0 || out == 0 ||
	    generation != fs_dentry_index_generation)
		return -1;
	while (*cursor < FS_DENTRY_INDEX_CAP) {
		uint scanned = (*cursor)++;
		uint slot = (hash + scanned) & (FS_DENTRY_INDEX_CAP - 1);
		struct fs_dentry_index_entry *entry = &fs_dentry_index[slot];

		if (entry->state == FS_DENTRY_INDEX_EMPTY)
			return 0;
		if (entry->state != FS_DENTRY_INDEX_USED ||
		    entry->hash != hash || entry->dev != dp->dev ||
		    entry->dir_inum != dp->inum ||
		    entry->dir_incarnation != dp->vfs_incarnation)
			continue;
		*out = *entry;
		return 1;
	}
	return 0;
}

static int
fs_dentry_entry_equal(const struct fs_dentry_index_entry *left,
		      const struct fs_dentry_index_entry *right)
{
	return left->hash == right->hash && left->offset == right->offset &&
	       left->dir_incarnation == right->dir_incarnation &&
	       left->target_incarnation == right->target_incarnation &&
	       left->dev == right->dev && left->dir_inum == right->dir_inum &&
	       left->target_inum == right->target_inum &&
	       left->state == right->state;
}

static void
fs_dentry_index_invalidate_snapshot(
	struct inode *dp, const struct fs_dentry_index_entry *snapshot,
	uint64 generation)
{
	uint slot;
	int found;

	if (snapshot == 0 || fs_dentry_gate_lock() < 0)
		return;
	if (generation == fs_dentry_index_generation &&
	    fs_dentry_index_probe(dp, snapshot->hash, snapshot->offset,
				   &slot, &found) == 0 && found &&
	    fs_dentry_entry_equal(&fs_dentry_index[slot], snapshot))
		fs_directory_index_invalidate(dp);
	fs_dentry_gate_unlock();
}

static int
fs_dentry_index_snapshot_stable(uint64 generation)
{
	int stable;

	if (fs_dentry_gate_lock() < 0)
		return 0;
	stable = generation == fs_dentry_index_generation;
	fs_dentry_gate_unlock();
	return stable;
}

static struct inode *
fs_dentry_index_validate(struct inode *dp, const char key[DIRSIZ + 1],
			 const struct fs_dentry_index_entry *entry,
			 int *name_match, int *stale, int *status)
{
	struct dirent de;
	struct inode *target;
	struct vfs_cred kernel_cred;
	char actual[DIRSIZ + 1];
	int result;

	if (name_match)
		*name_match = 0;
	if (stale)
		*stale = 0;
	if (entry == 0 || entry->offset >= dp->size ||
	    entry->offset % sizeof(de) != 0 || entry->dev != dp->dev ||
	    entry->dir_inum != dp->inum ||
	    entry->dir_incarnation != dp->vfs_incarnation) {
		if (stale)
			*stale = 1;
		if (status)
			*status = FS_LOOKUP_ERROR;
		return 0;
	}
	vfs_cred_kernel(&kernel_cred);
	result = readi(dp, &kernel_cred, 0, (uint64)&de,
		       entry->offset, sizeof(de));
	if (result != sizeof(de)) {
		if (status)
			*status = result < 0 ? result : FS_LOOKUP_ERROR;
		return 0;
	}
	kernel_performance_directory_probe(1);
	memset(actual, 0, sizeof(actual));
	memmove(actual, de.name, DIRSIZ);
	if (de.inum != entry->target_inum ||
	    fs_dentry_hash(dp, actual) != entry->hash) {
		if (stale)
			*stale = 1;
		if (status)
			*status = FS_LOOKUP_ERROR;
		return 0;
	}
	if (strncmp(actual, key, DIRSIZ) != 0) {
		if (status)
			*status = FS_LOOKUP_ABSENT;
		return 0;
	}
	if (name_match)
		*name_match = 1;
	target = inode_get(dp->dev, de.inum);
	if (target == 0) {
		if (status)
			*status = FS_LOOKUP_ERROR;
		return 0;
	}
	result = ivalid(target);
	if (result < 0 || !vfs_inode_label_valid(target) ||
	    target->vfs_incarnation != entry->target_incarnation) {
		iput(target);
		if (stale)
			*stale = 1;
		if (status)
			*status = result < 0 ? result : FS_LOOKUP_ERROR;
		return 0;
	}
	if (status)
		*status = FS_LOOKUP_FOUND;
	return target;
}

static int
fs_dentry_index_create_conflict(struct inode *dp,
				const char key[DIRSIZ + 1],
				uint target_policy, uint target_scope_id,
				int *empty_off)
{
	struct fs_dentry_index_entry snapshot;
	struct fs_directory_index_state *state;
	uint64 generation = 0;
	uint cursor = 0;
	uint hash = fs_dentry_hash(dp, key);
	int result;

	if (empty_off == 0 || fs_dentry_gate_lock() < 0)
		return FS_DENTRY_LOCATE_FALLBACK;
	result = fs_dentry_index_snapshot_begin(dp, &generation);
	fs_dentry_gate_unlock();
	if (result <= 0)
		return FS_DENTRY_LOCATE_FALLBACK;
	for (;;) {
		struct inode *target;
		int name_match;
		int stale;
		int validation_status = FS_LOOKUP_ERROR;
		int matches;

		if (fs_dentry_gate_lock() < 0)
			return FS_DENTRY_LOCATE_FALLBACK;
		result = fs_dentry_index_snapshot_next(
			dp, hash, generation, &cursor, &snapshot);
		fs_dentry_gate_unlock();
		if (result < 0)
			return FS_DENTRY_LOCATE_FALLBACK;
		if (result == 0)
			break;
		target = fs_dentry_index_validate(
			dp, key, &snapshot, &name_match, &stale,
			&validation_status);
		if (target == 0) {
			if (stale)
				fs_dentry_index_invalidate_snapshot(
					dp, &snapshot, generation);
			if (!name_match && !stale &&
			    validation_status == FS_LOOKUP_ABSENT)
				continue;
			return FS_DENTRY_LOCATE_FALLBACK;
		}
		if (!fs_dentry_index_snapshot_stable(generation)) {
			iput(target);
			return FS_DENTRY_LOCATE_FALLBACK;
		}
		matches = target->vfs_policy == target_policy &&
			  (target_policy != VFS_POLICY_WORKFLOW ||
			   target->vfs_scope_id == target_scope_id);
		if (target->vfs_policy == VFS_POLICY_WORKFLOW &&
		    target->vfs_scope_id == VFS_SCOPE_SYSTEM &&
		    ((target_policy == VFS_POLICY_WORKFLOW &&
		      target_scope_id >= VFS_SCOPE_FIRST_DYNAMIC) ||
		     (target_policy == VFS_POLICY_PUBLIC &&
		      target->vfs_exec_profile == VFS_EXEC_PROFILE_NONE)))
			matches = 1;
		iput(target);
		if (matches)
			return FS_DENTRY_LOCATE_HIT;
	}
	if (fs_dentry_gate_lock() < 0)
		return FS_DENTRY_LOCATE_FALLBACK;
	state = generation == fs_dentry_index_generation ?
		fs_directory_index_find(dp, 0) : 0;
	if (state == 0 || !state->complete || state->overflow) {
		fs_dentry_gate_unlock();
		return FS_DENTRY_LOCATE_FALLBACK;
	}
	*empty_off = fs_directory_index_first_empty(state);
	fs_dentry_gate_unlock();
	return FS_DENTRY_LOCATE_ABSENT;
}

static void
fs_dentry_index_publish_link(struct inode *dp,
			     const char key[DIRSIZ + 1], uint offset,
			     uint target_inum, uint target_incarnation)
{
	struct fs_directory_index_state *state;
	uint ordinal = offset / sizeof(struct dirent);

	fs_dentry_gate_lock_uninterruptible();
	state = fs_directory_index_find(dp, 0);
	if (state != 0 && state->complete && !state->overflow &&
	    ordinal < FS_DIRECTORY_INDEX_MAX_ENTRIES &&
	    (state->occupied[ordinal / 8] & (1U << (ordinal % 8))) == 0 &&
	    state->entries < FS_DIRECTORY_INDEX_MAX_ENTRIES) {
		fs_directory_index_occupied_set(state, offset, 1);
		state->entries++;
		if (fs_dentry_index_insert(dp, key, offset, target_inum,
					   target_incarnation, 0) < 0)
			fs_directory_index_invalidate(dp);
	} else if (state != 0) {
		fs_directory_index_invalidate(dp);
	}
	fs_dentry_gate_unlock();
}

static void
fs_dentry_index_publish_unlink(struct inode *dp,
			       const char key[DIRSIZ + 1], uint offset,
			       uint target_inum, uint target_incarnation)
{
	struct fs_directory_index_state *state;
	struct fs_dentry_index_entry *entry;
	uint hash = fs_dentry_hash(dp, key);
	uint slot;
	uint ordinal = offset / sizeof(struct dirent);
	int found;

	fs_dentry_gate_lock_uninterruptible();
	state = fs_directory_index_find(dp, 0);
	if (state == 0) {
		fs_dentry_gate_unlock();
		return;
	}
	if (!state->complete || state->overflow ||
	    fs_dentry_index_probe(dp, hash, offset, &slot, &found) < 0 ||
	    !found || ordinal >= FS_DIRECTORY_INDEX_MAX_ENTRIES ||
	    (state->occupied[ordinal / 8] & (1U << (ordinal % 8))) == 0) {
		fs_directory_index_invalidate(dp);
		fs_dentry_gate_unlock();
		return;
	}
	entry = &fs_dentry_index[slot];
	if (entry->target_inum != target_inum ||
	    entry->target_incarnation != target_incarnation ||
	    state->entries == 0) {
		fs_directory_index_invalidate(dp);
		fs_dentry_gate_unlock();
		return;
	}
	entry->state = FS_DENTRY_INDEX_TOMBSTONE;
	if (fs_dentry_index_used == 0)
		panic("dentry unlink used underflow");
	fs_dentry_index_used--;
	fs_dentry_index_tombstones++;
	state->entries--;
	fs_directory_index_occupied_set(state, offset, 0);
	fs_dentry_index_bump();
	if (fs_dentry_index_tombstones > FS_DENTRY_INDEX_CAP / 2)
		fs_dentry_index_reset_all();
	fs_dentry_gate_unlock();
}

static void
fs_dentry_index_invalidate_directory(struct inode *dp)
{
	fs_dentry_gate_lock_uninterruptible();
	fs_directory_index_invalidate(dp);
	fs_dentry_gate_unlock();
}

/*
 * Prebuilt images historically use a fixed DIRSIZ dirent as their lookup key.
 * Canonicalize every operation to that on-disk key. Target inode policy and
 * scope are still checked after the name match, so aliases cannot widen access.
 */
// Look for a directory entry in a directory.
// If found, set *poff to byte offset of entry.
struct inode *dirlookup(struct inode *dp, char *name, uint *poff,
			uint policy, uint scope_id, int *status)
{
	uint off = 0, found_off = 0;
	struct dirent de;
	struct dir_scan_batch batch;
	struct inode *found = 0;
	struct inode *target;
	struct vfs_cred kernel_cred;
	char key[DIRSIZ + 1];
	struct fs_dentry_index_entry indexed;
	uint64 index_generation = 0;
	uint index_cursor = 0;
	uint index_hash;
	int name_match;
	int stale;
	int result;

	if (status)
		*status = FS_LOOKUP_ERROR;
	if (fs_dirent_canonicalize(name, key) < 0 || dp == 0 ||
	    dp->type != T_DIR || policy == 0)
		return 0;
	vfs_cred_kernel(&kernel_cred);
	if (!vfs_inode_authorize(dp, &kernel_cred, VFS_OP_READ))
		return 0;
	index_hash = fs_dentry_hash(dp, key);
	if (fs_dentry_gate_lock() < 0)
		return 0;
	result = fs_dentry_index_snapshot_begin(dp, &index_generation);
	fs_dentry_gate_unlock();
	if (result > 0) {
		for (;;) {
			int validation_status = FS_LOOKUP_ERROR;

			if (fs_dentry_gate_lock() < 0)
				goto authoritative;
			result = fs_dentry_index_snapshot_next(
				dp, index_hash, index_generation,
				&index_cursor, &indexed);
			fs_dentry_gate_unlock();
			if (result < 0)
				goto authoritative;
			if (result == 0) {
				if (found) {
					if (poff)
						*poff = found_off;
					if (status)
						*status = FS_LOOKUP_FOUND;
				} else if (status) {
					*status = FS_LOOKUP_ABSENT;
				}
				return found;
			}
			target = fs_dentry_index_validate(
				dp, key, &indexed, &name_match, &stale,
				&validation_status);
			if (target == 0) {
				if (stale) {
					fs_dentry_index_invalidate_snapshot(
						dp, &indexed, index_generation);
					goto authoritative;
				}
				if (!name_match &&
				    validation_status == FS_LOOKUP_ABSENT)
					continue;
				result = validation_status;
				goto fail;
			}
			if (!fs_dentry_index_snapshot_stable(index_generation)) {
				iput(target);
				goto authoritative;
			}
			if (target->vfs_policy != policy ||
			    (target->vfs_policy == VFS_POLICY_WORKFLOW &&
			     target->vfs_scope_id != scope_id)) {
				iput(target);
				continue;
			}
			if (found) {
				iput(target);
				result = FS_LOOKUP_ERROR;
				goto fail;
			}
			found = target;
			found_off = indexed.offset;
		}
	}

authoritative:
	if (found) {
		iput(found);
		found = 0;
	}
	off = 0;
	while (off < dp->size) {
		if (!vfs_inode_authorize(dp, &kernel_cred, VFS_OP_READ)) {
			result = FS_LOOKUP_ERROR;
			goto fail;
		}
		result = dir_scan_fill(dp, key, off, 0, &batch);
		if (result < 0)
			goto fail;
		off = batch.next_offset;
		for (uint i = 0; i < batch.count; i++) {
			de = batch.candidates[i].entry;
			if (strncmp(key, de.name, DIRSIZ) == 0) {
				target = inode_get(dp->dev, de.inum);
				if (target == 0) {
					result = FS_LOOKUP_ERROR;
					goto fail;
				}
				result = ivalid(target);
				if (result < 0) {
					iput(target);
					goto fail;
				}
				if (!vfs_inode_label_valid(target)) {
					iput(target);
					result = FS_LOOKUP_ERROR;
					goto fail;
				}
				if (policy != 0 &&
				    target->vfs_policy != policy) {
					iput(target);
					continue;
				}
				if (target->vfs_policy == VFS_POLICY_WORKFLOW &&
				    target->vfs_scope_id != scope_id) {
					iput(target);
					continue;
				}
				if (found) {
					iput(target);
					result = FS_LOOKUP_ERROR;
					goto fail;
				}
				found = target;
				found_off = batch.candidates[i].offset;
			}
		}
		result = dir_scan_checkpoint(batch.scanned);
		if (result < 0)
			goto fail;
	}
	if (found) {
		if (poff)
			*poff = found_off;
		if (status)
			*status = FS_LOOKUP_FOUND;
	} else if (status) {
		*status = FS_LOOKUP_ABSENT;
	}
	return found;

fail:
	if (status)
		*status = result;
	if (found)
		iput(found);
	return 0;
}

//Show the filenames of all files in the directory
int dirls(struct inode *dp, const struct vfs_cred *cred)
{
	uint count = 0;
	uint off = 0;
	struct dirent de;
	struct dir_scan_batch batch;
	struct inode *target;
	char name[DIRSIZ + 1];
	int result;

	if (dp == 0 || dp->type != T_DIR)
		return -1;
	if (!vfs_inode_authorize(dp, cred, VFS_OP_READ))
		return -1;

	while (off < dp->size) {
		if (!vfs_inode_authorize(dp, cred, VFS_OP_READ))
			return -1;
		result = dir_scan_fill(dp, 0, off, 0, &batch);
		if (result < 0)
			return result;
		off = batch.next_offset;
		for (uint i = 0; i < batch.count; i++) {
			de = batch.candidates[i].entry;
			target = inode_get(dp->dev, de.inum);
			if (target == 0)
				return -1;
			result = ivalid(target);
			if (result < 0) {
				iput(target);
				return result;
			}
			if (!vfs_inode_label_valid(target)) {
				iput(target);
				return -1;
			}
			if (!vfs_inode_authorize(target, cred, VFS_OP_LOOKUP)) {
				iput(target);
				continue;
			}
			iput(target);
			memmove(name, de.name, DIRSIZ);
			name[DIRSIZ] = 0;
			printf("%s\n", name);
			count++;
		}
		result = dir_scan_checkpoint(batch.scanned);
		if (result < 0)
			return result;
	}
	return count;
}

// Write a new directory entry (name, inum) into the directory dp.
int dirlink(struct inode *dp, char *name, uint inum,
	    const struct vfs_cred *cred)
{
	uint off = 0;
	int empty_off = -1;
	struct dirent de;
	struct dir_scan_batch batch;
	struct inode *ip;
	struct inode *target;
	struct vfs_cred kernel_cred;
	struct fs_storage_charge charge = {
		.owner = FS_OWNER_SYSTEM,
		.level = FS_CHARGE_SYSTEM,
	};
	uint target_policy;
	uint target_scope_id;
	uint target_incarnation;
	char key[DIRSIZ + 1];
	int lookup_status;
	int index_status;
	int result;
	if (fs_dirent_canonicalize(name, key) < 0 || dp == 0 ||
	    dp->type != T_DIR)
		return -1;
	if (!vfs_inode_authorize(dp, cred, VFS_OP_CREATE))
		return -1;
	target = inode_get(dp->dev, inum);
	if (target == 0)
		return -1;
	result = ivalid(target);
	if (result < 0 || !vfs_inode_label_valid(target)) {
		iput(target);
		return result < 0 ? result : -1;
	}
	target_policy = target->vfs_policy;
	target_scope_id = target->vfs_scope_id;
	target_incarnation = target->vfs_incarnation;
	if ((target_policy == VFS_POLICY_WORKFLOW &&
	     target_scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
	     (cred == 0 || cred->scope_id != target_scope_id)) ||
	    (target_policy == VFS_POLICY_PUBLIC &&
	     (cred == 0 || cred->scope_id != VFS_SCOPE_NONE || cred->kernel)) ||
	    (target_policy == VFS_POLICY_KERNEL_PRIVATE &&
	     (cred == 0 || !cred->kernel ||
	      cred->scope_id != VFS_SCOPE_NONE))) {
		iput(target);
		return -1;
	}
	iput(target);
	vfs_cred_kernel(&kernel_cred);
	if (fs_namespace_gate_lock() < 0)
		return -1;
	index_status = fs_dentry_index_create_conflict(
		dp, key, target_policy, target_scope_id, &empty_off);
	if (index_status == FS_DENTRY_LOCATE_HIT) {
		result = -1;
		goto out_namespace;
	}
	if (index_status == FS_DENTRY_LOCATE_ABSENT) {
		if (empty_off < 0) {
			result = -1;
			goto out_namespace;
		}
		goto write_entry;
	}
	// Validate namespace uniqueness, immutable SYSTEM-name reservation, and
	// free-slot selection with one bounded block walker.  Inode validation is
	// deliberately outside the buffer lifetime.
	while (off < dp->size) {
		if (!vfs_inode_authorize(dp, cred, VFS_OP_CREATE) ||
		    !vfs_inode_authorize(dp, &kernel_cred, VFS_OP_READ)) {
			result = -1;
			goto out_namespace;
		}
		result = dir_scan_fill(dp, key, off, &empty_off, &batch);
		if (result < 0)
			goto out_namespace;
		off = batch.next_offset;
		for (uint i = 0; i < batch.count; i++) {
			de = batch.candidates[i].entry;
			if (strncmp(key, de.name, DIRSIZ) != 0)
				continue;
			ip = inode_get(dp->dev, de.inum);
			if (ip == 0) {
				result = -1;
				goto out_namespace;
			}
			result = ivalid(ip);
			if (result < 0) {
				iput(ip);
				goto out_namespace;
			}
			if (!vfs_inode_label_valid(ip)) {
				iput(ip);
				result = -1;
				goto out_namespace;
			}
			lookup_status = ip->vfs_policy == target_policy &&
				(target_policy != VFS_POLICY_WORKFLOW ||
				 ip->vfs_scope_id == target_scope_id);
			if (ip->vfs_policy == VFS_POLICY_WORKFLOW &&
			    ip->vfs_scope_id == VFS_SCOPE_SYSTEM &&
			    ((target_policy == VFS_POLICY_WORKFLOW &&
			      target_scope_id >= VFS_SCOPE_FIRST_DYNAMIC) ||
			     (target_policy == VFS_POLICY_PUBLIC &&
			      ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE)))
				lookup_status = 1;
			iput(ip);
			if (lookup_status) {
				result = -1;
				goto out_namespace;
			}
		}
		result = dir_scan_checkpoint(batch.scanned);
		if (result < 0)
			goto out_namespace;
	}
	if (empty_off < 0) {
		result = -1;
		goto out_namespace;
	}
write_entry:
	memmove(de.name, key, DIRSIZ);
	de.inum = inum;
	result = writei_charged(dp, &kernel_cred, &charge, 0,
				(uint64)&de, (uint)empty_off, sizeof(de),
				FS_EPOCH_NAMESPACE_ATTACH);
	if (result != sizeof(de)) {
		fs_dentry_index_invalidate_directory(dp);
		result = fs_io_health == FS_IO_INDETERMINATE ?
			FS_LOOKUP_INDETERMINATE : (result < 0 ? result : -1);
		goto out_namespace;
	}
	fs_dentry_index_publish_link(dp, key, (uint)empty_off, inum,
				     target_incarnation);
	result = fs_durable_barrier_forward();
	if (result < 0)
		result = FS_LOOKUP_INDETERMINATE;
	else
		result = 0;
out_namespace:
	fs_namespace_gate_unlock();
	return result;
}

int dirunlink(struct inode *dp, char *name, uint offset, uint expected_inum,
	      uint expected_incarnation, const struct vfs_cred *cred,
	      uint policy)
{
	struct dirent de;
	struct inode *target;
	struct vfs_cred kernel_cred;
	char key[DIRSIZ + 1];
	struct fs_storage_charge charge = {
		.owner = FS_OWNER_SYSTEM,
		.level = FS_CHARGE_SYSTEM,
	};
	int result;

	if (fs_dirent_canonicalize(name, key) < 0 || dp == 0 ||
	    dp->type != T_DIR)
		return -1;
	if (!vfs_inode_authorize(dp, cred, VFS_OP_DELETE))
		return -1;
	if (offset >= dp->size || offset % sizeof(de) != 0)
		return -1;
	if (fs_namespace_gate_lock() < 0)
		return -1;
	vfs_cred_kernel(&kernel_cred);
	result = readi(dp, &kernel_cred, 0, (uint64)&de, offset,
		       sizeof(de));
	if (result != sizeof(de)) {
		result = result < 0 ? result : -1;
		goto out_namespace;
	}
	if (de.inum != expected_inum ||
	    strncmp(key, de.name, DIRSIZ) != 0) {
		result = -1;
		goto out_namespace;
	}
	target = inode_get(dp->dev, de.inum);
	if (target == 0) {
		result = -1;
		goto out_namespace;
	}
	result = ivalid(target);
	if (result < 0) {
		iput(target);
		goto out_namespace;
	}
	if (!vfs_inode_label_valid(target) ||
	    target->vfs_policy != policy ||
	    target->vfs_incarnation != expected_incarnation ||
	    !vfs_inode_authorize(target, cred, VFS_OP_DELETE) ||
	    !exec_policy_inode_mutable(target)) {
		iput(target);
		result = -1;
		goto out_namespace;
	}
	iput(target);
	memset(&de, 0, sizeof(de));
	result = writei_charged(dp, &kernel_cred, &charge, 0,
				(uint64)&de, offset, sizeof(de),
				FS_EPOCH_NAMESPACE_DETACH);
	if (result != sizeof(de)) {
		fs_dentry_index_invalidate_directory(dp);
		result = result < 0 ? result : -1;
		goto out_namespace;
	}
	fs_dentry_index_publish_unlink(dp, key, offset, expected_inum,
				       expected_incarnation);
	result = fs_durable_barrier_forward();
out_namespace:
	fs_namespace_gate_unlock();
	return result;
}

int fs_rollback_created_workflow(char *path, uint expected_dev,
				 uint expected_inum, uint expected_incarnation,
				 uint scope_id)
{
	struct inode *dp, *ip;
	struct vfs_cred cred;
	char key[DIRSIZ + 1];
	uint offset;
	int status;

	if (fs_dirent_canonicalize(path, key) < 0 || expected_dev == 0 ||
	    expected_inum == 0 ||
	    expected_incarnation == 0 ||
	    scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG || !vfs_scope_retained(scope_id))
		return -1;
	vfs_cred_kernel(&cred);
	dp = root_dir_status(&status);
	if (dp == 0 || status != FS_LOOKUP_FOUND || dp->dev != expected_dev)
		goto fail_parent;
	ip = dirlookup(dp, key, &offset, VFS_POLICY_WORKFLOW, scope_id,
		       &status);
	if (ip == 0 || status != FS_LOOKUP_FOUND || ip->dev != expected_dev ||
	    ip->inum != expected_inum ||
	    ip->vfs_incarnation != expected_incarnation || ip->type != T_FILE ||
	    ip->agent_meta_slot != 0 || ip->agent_meta_flags != 0 ||
	    ip->agent_meta_version != 0 || !agent_edit_unlink_allowed(ip))
		goto fail_target;
	if (dirunlink(dp, key, offset, expected_inum, expected_incarnation,
		      &cred, VFS_POLICY_WORKFLOW) < 0)
		goto fail_target;
	agent_edit_note_delete(ip);
	ip->removed = 1;
	status = fs_put_removed_checked(ip);
	iput(dp);
	return status;

fail_target:
	if (ip)
		iput(ip);
fail_parent:
	if (dp)
		iput(dp);
	return -1;
}

#define FS_SCOPE_RECLAIM_DIR 1U
#define FS_SCOPE_RECLAIM_INODE 2U
#define FS_SCOPE_RECLAIM_SCAN_STEP (4U * BSIZE / sizeof(struct dirent))
#define FS_SCOPE_RECLAIM_LOOKUP_STEP 64U
#define FS_SCOPE_RECLAIM_MUTATION_STEP 16U

struct fs_scope_reclaim_cursor {
	int used;
	uint scope_id;
	uint phase;
	uint64 dir_offset;
	uint inode_cursor;
	uint inode_count;
	uint inodes[NINODE];
	int reclaimed;
	int failed;
	struct inode_reclaim blocks;
};

static struct fs_scope_reclaim_cursor
	fs_scope_reclaim_cursors[VFS_SCOPE_MAX_RETIRING];

static struct fs_scope_reclaim_cursor *
fs_scope_reclaim_cursor_get(uint scope_id)
{
	struct fs_scope_reclaim_cursor *free_cursor = 0;

	for (uint i = 0; i < VFS_SCOPE_MAX_RETIRING; i++) {
		struct fs_scope_reclaim_cursor *cursor =
			&fs_scope_reclaim_cursors[i];

		if (cursor->used && cursor->scope_id == scope_id)
			return cursor;
		if (!cursor->used && free_cursor == 0)
			free_cursor = cursor;
	}
	if (free_cursor == 0)
		return 0;
	memset(free_cursor, 0, sizeof(*free_cursor));
	free_cursor->used = 1;
	free_cursor->scope_id = scope_id;
	free_cursor->phase = FS_SCOPE_RECLAIM_DIR;
	return free_cursor;
}

// Namespace detach and inode/block reclamation are separate, resumable
// phases. A committed directory clear can therefore never hide an orphan
// from the second phase, and no VFS object is held across a budget checkpoint.
int fs_reclaim_scope_files(uint scope_id)
{
	struct fs_storage_charge system_charge = {
		.owner = FS_OWNER_SYSTEM,
		.level = FS_CHARGE_SYSTEM,
	};
	struct fs_scope_reclaim_cursor *cursor;
	struct vfs_cred kernel_cred;
	uint scans = 0;
	uint lookups = 0;
	uint mutations = 0;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    !vfs_scope_retiring(scope_id))
		return -1;
	cursor = fs_scope_reclaim_cursor_get(scope_id);
	if (cursor == 0)
		return -1;
	vfs_cred_kernel(&kernel_cred);
	for (;;) {
		if (cursor->phase == FS_SCOPE_RECLAIM_DIR) {
			int root_status;
			struct inode *dp;
			struct inode *target = 0;
			struct dirent de;
			uint64 off = cursor->dir_offset;

			/* Empty preallocated directory slots are cheap to inspect and
			 * must not consume the destructive-work budget. Keep independent
			 * scan, inode lookup, and mutation bounds so sparse namespaces
			 * reclaim promptly without turning one checkpoint into an
			 * unbounded syscall. */
			if (scans >= FS_SCOPE_RECLAIM_SCAN_STEP)
				return FS_RECLAIM_PENDING;

			dp = root_dir_status(&root_status);
			if (dp == 0 || root_status != FS_LOOKUP_FOUND) {
				if (dp)
					iput(dp);
				return root_status < 0 ? root_status : -1;
			}
			if (off >= dp->size) {
				iput(dp);
				cursor->phase = FS_SCOPE_RECLAIM_INODE;
				continue;
			}
			if (readi(dp, &kernel_cred, 0, (uint64)&de, off,
				  sizeof(de)) != sizeof(de)) {
				iput(dp);
				return -1;
			}
			scans++;
			if (de.inum != 0) {
				if (lookups >= FS_SCOPE_RECLAIM_LOOKUP_STEP) {
					iput(dp);
					return FS_RECLAIM_PENDING;
				}
				lookups++;
				target = inode_get(dp->dev, de.inum);
				if (target == 0) {
					cursor->failed = 1;
				} else {
					if (ivalid(target) < 0) {
						iput(target);
						iput(dp);
						return -1;
					}
					if (vfs_inode_label_valid(target) &&
					    target->vfs_policy ==
						    VFS_POLICY_WORKFLOW &&
					    target->vfs_scope_id == scope_id) {
						if (mutations >=
						    FS_SCOPE_RECLAIM_MUTATION_STEP) {
							iput(target);
							iput(dp);
							return FS_RECLAIM_PENDING;
						}
						mutations++;
						if (target->type != T_FILE ||
						    !exec_policy_inode_mutable(target)) {
							cursor->failed = 1;
						} else {
							struct dirent empty;

							memset(&empty, 0, sizeof(empty));
							if (fs_namespace_gate_lock() < 0) {
								iput(target);
								iput(dp);
								return -1;
							}
							if (writei_charged(
								    dp, &kernel_cred,
								    &system_charge, 0,
								    (uint64)&empty, off,
								    sizeof(empty),
								    FS_EPOCH_NAMESPACE_DETACH) !=
							    sizeof(empty)) {
								fs_dentry_index_invalidate_directory(dp);
								fs_namespace_gate_unlock();
								iput(target);
								iput(dp);
								return -1;
							}
							fs_dentry_index_invalidate_directory(dp);
							fs_namespace_gate_unlock();
							if (cursor->inode_count >= NINODE) {
								iput(target);
								iput(dp);
								return -1;
							}
							cursor->inodes[cursor->inode_count++] =
								target->inum;
						}
					}
					iput(target);
				}
			}
			cursor->dir_offset += sizeof(de);
			iput(dp);
		} else if (cursor->phase == FS_SCOPE_RECLAIM_INODE) {
			struct inode *target;

			if (mutations >= FS_SCOPE_RECLAIM_MUTATION_STEP)
				return FS_RECLAIM_PENDING;
			mutations++;

			if (cursor->blocks.mode != INODE_RECLAIM_NONE) {
				int done = itruncate_reclaim_step(&cursor->blocks, 1);

				if (done < 0) {
					cursor->failed = 1;
					return -1;
				}
				if (!done)
					return FS_RECLAIM_PENDING;
				cursor->inode_cursor++;
				cursor->reclaimed++;
				continue;
			}
			if (cursor->inode_cursor >= cursor->inode_count) {
				int result = cursor->failed ? -1 : cursor->reclaimed;

				memset(cursor, 0, sizeof(*cursor));
				return result;
			}
			target = inode_get(ROOTDEV,
					   cursor->inodes[cursor->inode_cursor]);
			if (target == 0) {
				cursor->failed = 1;
				cursor->inode_cursor++;
				continue;
			}
			if (ivalid(target) < 0) {
				iput(target);
				return -1;
			}
			if (target->type == 0) {
				iput(target);
				cursor->inode_cursor++;
				continue;
			}
			if (target->fs_owner_domain != FS_OWNER_SCOPE(scope_id) ||
			    target->type != T_FILE ||
			    !vfs_inode_label_valid(target) ||
			    target->vfs_policy != VFS_POLICY_WORKFLOW ||
			    target->vfs_scope_id != scope_id ||
			    !exec_policy_inode_mutable(target)) {
				iput(target);
				cursor->failed = 1;
				cursor->inode_cursor++;
				continue;
			}
			target->removed = 1;
			if (target->ref == 1) {
				int detached = inode_remove_detach(
					target, &cursor->blocks);

				if (detached < 0) {
					cursor->failed = 1;
					cursor->inode_cursor++;
				} else if (detached == 0) {
					iput(target);
					cursor->inode_cursor++;
				} else if (cursor->blocks.mode ==
					   INODE_RECLAIM_NONE) {
					cursor->inode_cursor++;
					cursor->reclaimed++;
				}
			} else {
				iput(target);
				cursor->inode_cursor++;
				cursor->reclaimed++;
			}
		} else {
			panic("invalid scope reclaim phase");
		}
		if (bio_checkpoint_should_stop(bio_request_checkpoint()))
			return FS_RECLAIM_PENDING;
	}
}

//Return the inode of the root directory
struct inode *root_dir_status(int *status)
{
	struct inode *r = iget(ROOTDEV, ROOTINO);
	int result;

	if (status)
		*status = FS_LOOKUP_ERROR;
	if (r == 0)
		return 0;
	result = ivalid(r);
	if (result < 0) {
		if (status)
			*status = result;
		iput(r);
		return 0;
	}
	if (r->type != T_DIR) {
		iput(r);
		return 0;
	}
	if (status)
		*status = FS_LOOKUP_FOUND;
	return r;
}

struct inode *fs_create(char *path, short type, int *created,
			const struct vfs_cred *cred, uint policy, int *status)
{
	struct inode *dp;
	struct inode *ip;
	struct fs_storage_charge charge;
	uint intent_owner;
	char key[DIRSIZ + 1];
	int lookup_status;
	int root_status;
	int result;

	if (created)
		*created = 0;
	if (status)
		*status = FS_LOOKUP_ERROR;
	if (fs_dirent_canonicalize(path, key) < 0)
		return 0;
	dp = root_dir_status(&root_status);
	if (dp == 0 || root_status != FS_LOOKUP_FOUND) {
		root_status = fs_create_failure_status(root_status);
		if (status)
			*status = root_status;
		if (created && root_status == FS_LOOKUP_INDETERMINATE)
			*created = FS_CREATE_INDETERMINATE;
		if (dp)
			iput(dp);
		return 0;
	}
	if (!vfs_inode_authorize(dp, cred, VFS_OP_CREATE) ||
	    !vfs_create_request_authorize(cred, policy, 0, 0, 0)) {
		iput(dp);
		return 0;
	}
	ip = dirlookup(dp, key, 0, policy,
		       policy == VFS_POLICY_WORKFLOW ? cred->scope_id :
						 VFS_SCOPE_NONE,
		       &lookup_status);
	if (ip != 0) {
		iput(dp);
		result = ivalid(ip);
		if (result < 0) {
			result = fs_create_failure_status(result);
			if (status)
				*status = result;
			if (created && result == FS_LOOKUP_INDETERMINATE)
				*created = FS_CREATE_INDETERMINATE;
			iput(ip);
			return 0;
		}
		if (type == T_FILE && ip->type == T_FILE &&
		    vfs_inode_create_matches(ip, cred, policy)) {
			if (status)
				*status = FS_LOOKUP_FOUND;
			return ip;
		}
		iput(ip);
		return 0;
	}
	if (lookup_status != FS_LOOKUP_ABSENT) {
		lookup_status = fs_create_failure_status(lookup_status);
		if (status)
			*status = lookup_status;
		if (created && lookup_status == FS_LOOKUP_INDETERMINATE)
			*created = FS_CREATE_INDETERMINATE;
		iput(dp);
		return 0;
	}
	if (fs_storage_charge_from_vfs(cred, &charge) < 0) {
		result = fs_create_failure_status(-1);
		if (status)
			*status = result;
		if (created && result == FS_LOOKUP_INDETERMINATE)
			*created = FS_CREATE_INDETERMINATE;
		iput(dp);
		return 0;
	}
	ip = ialloc(dp->dev, type, &charge, &lookup_status);
	if (ip == 0) {
		lookup_status = fs_create_failure_status(lookup_status);
		if (status)
			*status = lookup_status;
		if (created && lookup_status == FS_LOOKUP_INDETERMINATE)
			*created = FS_CREATE_INDETERMINATE;
		iput(dp);
		return 0;
	}
	result = ivalid(ip);
	if (result < 0)
		goto fail_allocated;
	if (ip->type != type ||
	    fs_qmap_transition_owner(ip->fs_owner_domain,
				      FS_QMAP_ALLOCATING_FLAG,
				      &intent_owner) < 0 ||
	    intent_owner != charge.owner) {
		result = -1;
		goto fail_allocated;
	}
	ip->fs_owner_domain = charge.owner;
	result = vfs_inode_init_label(ip, cred, policy);
	if (result < 0) {
		result = fs_io_fail(FS_FAILURE_METADATA_WRITE_INDETERMINATE);
		goto fail_allocated;
	}
	lookup_status = fs_allocator_fault_before(
		FSALLOC_OP_IALLOC, FSALLOC_PHASE_OWNER, 0);
	if (lookup_status < 0) {
		result = lookup_status;
		goto fail_allocated;
	}
	result = iupdate(ip);
	if (result < 0)
		goto fail_allocated;
	result = fs_durable_barrier_forward();
	if (result < 0)
		goto fail_allocated;
	FS_ALLOCATOR_FAULT_AFTER(FSALLOC_OP_IALLOC, FSALLOC_PHASE_OWNER);
	result = dirlink(dp, key, ip->inum, cred);
	if (result < 0)
		goto fail_allocated;
	iput(dp);
	if (created)
		*created = 1;
	if (status)
		*status = FS_LOOKUP_FOUND;
	return ip;

fail_allocated:
	result = fs_create_failure_status(result);
	if (result == FS_LOOKUP_INDETERMINATE)
		iput(ip);
	else {
		ip->removed = 1;
		if (fs_put_removed_checked(ip) < 0)
			result = FS_LOOKUP_INDETERMINATE;
	}
	if (created && result == FS_LOOKUP_INDETERMINATE)
		*created = FS_CREATE_INDETERMINATE;
	if (status)
		*status = result;
	iput(dp);
	return 0;
}

// Find the corresponding inode in one security namespace.
struct inode *namei_scope_status(char *path, uint policy, uint scope_id,
				 int *status)
{
	int skip = 0;
	struct inode *ip;
	// if(path[0] == '.' && path[1] == '/')
	//     skip = 2;
	// if (path[0] == '/') {
	//     skip = 1;
	// }
	if (status)
		*status = FS_LOOKUP_ERROR;
	int root_status;
	struct inode *dp = root_dir_status(&root_status);
	if (dp == 0 || root_status != FS_LOOKUP_FOUND) {
		if (status)
			*status = root_status;
		if (dp)
			iput(dp);
		return 0;
	}
	ip = dirlookup(dp, path + skip, 0, policy, scope_id, status);
	iput(dp);
	return ip;
}
