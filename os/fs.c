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
#include "kernel_work.h"
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
_Static_assert(VFS_SCOPE_MAX_ACTIVE * AGENT_FILE_SCOPE_LIMIT ==
		       AGENT_FILE_ORDINARY_LIMIT,
	       "metadata inode limits must preserve catalog partitions");
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

static int writei_charged(struct inode *, const struct vfs_cred *,
			  const struct fs_storage_charge *, int, uint64, uint,
			  uint);
static int fs_qmap_write_forward(int dev, uint block, uint state);
static int fs_bitmap_write_forward(int dev, uint block, int allocated);

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

/* Allocation maps, directories, indirect maps and dinodes are FS metadata. */
static int fs_write_metadata_block(struct buf *bp)
{
	int result;

	if (fs_io_health != FS_IO_HEALTHY)
		return -1;
	result = bwrite(bp);

	return result < 0 ? fs_io_fail(fs_write_failure(result)) : 0;
}

/* File payload failures are contained by the owning object/protocol. */
static int fs_write_data_block(struct buf *bp)
{
	int result;

	if (fs_io_health != FS_IO_HEALTHY)
		return -1;
	result = bwrite(bp);

	if (result >= 0)
		return 0;
	return fs_io_fail(result == VIRTIO_DISK_ERR_BUSY ?
			      FS_FAILURE_SCHEDULING_UNAVAILABLE :
			      FS_FAILURE_OPERATION);
}

/* A completed metadata write is not a power-loss ordering point by itself. */
static int fs_durable_barrier(void)
{
	int result;

	if (fs_io_health != FS_IO_HEALTHY)
		return -1;
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
		result = fs_write_metadata_block(bp);
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
		fs_storage.workflow_inode_domain_limit < AGENT_FILE_SCOPE_LIMIT ?
			fs_storage.workflow_inode_domain_limit :
			AGENT_FILE_SCOPE_LIMIT;
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

static int fs_storage_reserve(const struct fs_storage_charge *charge,
			      int inode)
{
	uint *free_count;
	uint reserve;
	uint guarantee;
	uint *system_remaining;
	uint scope_id = VFS_SCOPE_NONE;
	int enabled;
	struct resource_account_handle account;
	struct resource_request request = {
		.kind = inode ? RESOURCE_FS_INODE : RESOURCE_FS_BLOCK,
		.amount = 1,
	};
	struct resource_reservation reservation;
	enum resource_charge_class charge_class;

	if (!fs_storage.ready || charge == 0 ||
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

	if (*free_count <= reserve) {
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
	if (charge->level == FS_CHARGE_SYSTEM &&
	    *free_count <= reserve + *system_remaining) {
		if (*system_remaining == 0)
			panic("system storage reserve invariant");
		(*system_remaining)--;
	}
	(*free_count)--;
	intr_restore(enabled);
	return 0;
}

static void fs_storage_release(uint owner, int inode)
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
		.amount = 1,
	};
	enum resource_charge_class charge_class;

	if (owner < FS_OWNER_SYSTEM)
		panic("storage release invariant");
	// Another I/O may have closed the filesystem while this free was in
	// flight.  Persistent recovery will rebuild the counters; do not turn a
	// fail-closed transition into a kernel panic.
	if (!fs_storage.ready)
		return;
	enabled = intr_save();
	if (*free_count >= total)
		panic("storage free count invariant");
	(*free_count)++;
	if (owner == FS_OWNER_SYSTEM && *system_remaining < system_reserve)
		(*system_remaining)++;
	if (FS_OWNER_IS_SCOPE(owner)) {
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
		if (fs_write_metadata_block(bp) < 0) {
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
		result = fs_write_metadata_block(bp);
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
		for (;;) {
			io_result = writei_charged(
				root, &kernel_cred, &system_charge, 0,
				(uint64)&de, off, sizeof(de));
			if (io_result == sizeof(de))
				break;
			if (io_result != VIRTIO_DISK_ERR_BUSY ||
			    fs_forward_checkpoint() < 0) {
				iput(root);
				return io_result < 0 ? io_result : -1;
			}
		}
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

	fs_io_health = FS_IO_HEALTHY;
	fs_claim_owner = 0;
	fs_allocator_owner = 0;
	wait_queue_init(&fs_claim_waiters, WAIT_REASON_FS_CLAIM);
	wait_queue_init(&fs_allocator_waiters, WAIT_REASON_FS_CLAIM);
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
}

// Zero a block.
static int bzero(int dev, int bno)
{
	struct buf *bp;
	int result;

	result = fs_read_block(dev, bno, &bp);
	if (result < 0)
		return result;
	result = bclaim(bp);
	if (result < 0) {
		brelse(bp);
		return result;
	}
	memset(bp->data, 0, BSIZE);
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
static uint balloc(uint dev, const struct fs_storage_charge *charge, int *error)
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
	if (charge == 0 || fs_allocator_gate_lock(0) < 0)
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

// Free is idempotent and retains its owner in qmap until refund is safe.
static int bfree(int dev, uint block)
{
	uint freeing;
	uint owner = FS_OWNER_NONE;
	uint qstate;
	int allocated;
	int attempts = 0;
	int result;

	if (block < sb.datastart || block >= sb.size)
		return fs_io_fail(FS_FAILURE_OPERATION);
	if (fs_allocator_gate_lock(1) < 0)
		return -1;

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
	return result;
}

//The inode table in memory
struct {
	struct inode inode[FS_ICACHE_SIZE];
} itable;

static struct inode *iget(uint dev, uint inum);

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
					ip->ref--;
					ip = 0;
					goto out;
				}
				result = fs_write_metadata_block(bp);
				if (result < 0) {
					brelse(bp);
					ip->ref--;
					ip = 0;
					goto out;
				}
				brelse(bp);
				result = fs_durable_barrier_forward();
				if (result < 0) {
					ip->ref--;
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
	result = fs_write_metadata_block(bp);
	brelse(bp);
	return result;
}

// Find the inode with number inum on device dev
// and return the in-memory copy. Does not read
// it from disk.
static struct inode *iget(uint dev, uint inum)
{
	struct inode *ip, *empty;
	// Is the inode already in the table?
	empty = 0;
	for (ip = &itable.inode[0]; ip < &itable.inode[FS_ICACHE_SIZE]; ip++) {
		if (ip->ref > 0 && ip->dev == dev && ip->inum == inum) {
			ip->ref++;
			return ip;
		}
		if (empty == 0 && ip->ref == 0) // Remember empty slot.
			empty = ip;
	}

	// Recycle an inode entry.
	if (empty == 0)
		return 0;

	ip = empty;
	ip->dev = dev;
	ip->inum = inum;
	ip->ref = 1;
	ip->valid = 0;
	ip->removed = 0;
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
	}
	return 0;
}

// Publish a removed inode as free before releasing its detached block token.
// The token can be drained synchronously by iput() or incrementally by a
// background reclaimer without keeping an inode or buffer pinned.
int inode_remove_detach(struct inode *ip, struct inode_reclaim *reclaim)
{
	struct vfs_cred kernel_cred;
	struct inode allocated_inode;
	uint storage_owner;
	uint freeing;
	int transition_status;

	if (ip == 0 || reclaim == 0 || ip->ref != 1 || !ip->valid ||
	    !ip->removed)
		return 0;
	storage_owner = ip->fs_owner_domain;
	freeing = fs_qmap_transition(FS_QMAP_FREEING_FLAG, storage_owner);
	if (freeing == FS_OWNER_NONE) {
		ip->removed = 0;
		ip->ref--;
		return -1;
	}
	vfs_cred_kernel(&kernel_cred);
	if (itruncate_detach(ip, &kernel_cred, 0, reclaim) < 0) {
		ip->removed = 0;
		ip->ref--;
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
		ip->ref--;
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
	ip->ref--;
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
	ip->ref--;
	return -1;
}

// Drop a reference to an in-memory inode. Removed objects first publish a
// detached, free inode and then reclaim the private block token.
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
	ip->ref--;
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

// Undo a block allocated for a write that could not copy any data into it.
static int bmap_discard(struct inode *ip, uint bn)
{
	struct buf *bp;
	uint addr;
	uint indirect;
	uint *a;

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
	if (reclaim == 0)
		return -1;
	memset(reclaim, 0, sizeof(*reclaim));
	if (!vfs_inode_authorize(ip, cred, VFS_OP_TRUNCATE) ||
	    !exec_policy_inode_mutable(ip))
		return -1;
	if (size > ip->size)
		return -1;
	if (size == ip->size)
		return 0;
	if (fs_claim_sponsored_public_inode(ip, cred) < 0)
		return -1;
	if (size == 0)
		return itruncate_detach_all(ip, reclaim);
	return itruncate_detach_partial(ip, size, reclaim);
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
			int user_dst, uint64 dst, uint off, uint n,
			int device_read)
{
	uint tot, m, addr;
	struct buf *bp;
	struct bio_checkpoint_result checkpoint;
	int failed = 0;
	int failure_result = -1;

	if (fs_io_health != FS_IO_HEALTHY ||
	    !vfs_inode_authorize(ip, cred, VFS_OP_READ))
		return -1;
	if (off > ip->size || off + n < off)
		return 0;
	if (off + n > ip->size)
		n = ip->size - off;

	for (tot = 0; tot < n; tot += m, off += m, dst += m) {
		addr = bmap(ip, off / BSIZE, 0, 0, &failure_result);
		if (addr == 0) {
			if (failure_result >= 0)
				failure_result = -1;
			failed = 1;
			break;
		}
		failure_result = device_read ?
			fs_read_device_block(ip->dev, addr, &bp) :
			fs_read_block(ip->dev, addr, &bp);
		if (failure_result < 0) {
			failed = 1;
			break;
		}
		m = MIN(n - tot, BSIZE - off % BSIZE);
		if (either_copyout(user_dst, dst,
				   (char *)bp->data + (off % BSIZE), m) == -1) {
			brelse(bp);
			failure_result = -1;
			failed = 1;
			break;
		}
		brelse(bp);
		checkpoint = bio_request_checkpoint();
		if (bio_checkpoint_should_stop(checkpoint)) {
			tot += m;
			failed = 1;
			break;
		}
	}
	if (failed && tot == 0)
		return failure_result;
	return tot;
}

int readi(struct inode *ip, const struct vfs_cred *cred, int user_dst,
	  uint64 dst, uint off, uint n)
{
	int result;

	bio_fs_atomic_enter();
	result = readi_atomic(ip, cred, user_dst, dst, off, n, 0);
	bio_fs_atomic_leave();
	return result;
}

int readi_device(struct inode *ip, const struct vfs_cred *cred, int user_dst,
		 uint64 dst, uint off, uint n)
{
	int result;

	bio_fs_atomic_enter();
	result = readi_atomic(ip, cred, user_dst, dst, off, n, 1);
	bio_fs_atomic_leave();
	return result;
}

// Write data to inode.
// Caller must hold ip->lock.
// If user_src==1, then src is a user virtual address;
// otherwise, src is a kernel address.
// Returns the number of bytes successfully written.
// If the return value is less than the requested n,
// there was an error of some kind.
static int writei_charged(struct inode *ip, const struct vfs_cred *cred,
			  const struct fs_storage_charge *charge,
			  int user_src, uint64 src, uint off, uint n)
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

	if (fs_io_health != FS_IO_HEALTHY ||
	    !vfs_inode_authorize(ip, cred, VFS_OP_WRITE))
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
		failure_result = fs_read_block(ip->dev, addr, &bp);
		if (failure_result < 0) {
			if (allocated)
				(void)bmap_discard(ip, bn);
			failed = 1;
			break;
		}
		m = MIN(n - tot, BSIZE - off % BSIZE);
		if (either_copyin(user_src, src,
				  (char *)bp->data + (off % BSIZE), m) == -1) {
			brelse(bp);
			if (allocated)
				(void)bmap_discard(ip, bn);
			failure_result = -1;
			failed = 1;
			break;
		}
		failure_result = ip->type == T_DIR ?
			fs_write_metadata_block(bp) : fs_write_data_block(bp);
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

int writei(struct inode *ip, const struct vfs_cred *cred, int user_src,
	   uint64 src, uint off, uint n)
{
	struct fs_storage_charge charge;
	int result;

	if (fs_storage_charge_from_vfs(cred, &charge) < 0)
		return -1;
	bio_fs_atomic_enter();
	result = writei_charged(ip, cred, &charge, user_src, src, off, n);
	bio_fs_atomic_leave();
	return result;
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
	return result;
}

// Look for a directory entry in a directory.
// If found, set *poff to byte offset of entry.
struct inode *dirlookup(struct inode *dp, char *name, uint *poff,
			uint policy, uint scope_id, int *status)
{
	uint off, found_off = 0;
	struct dirent de;
	struct inode *found = 0;
	struct inode *target;
	struct vfs_cred kernel_cred;
	int result;

	if (status)
		*status = FS_LOOKUP_ERROR;
	if (dp == 0 || dp->type != T_DIR || policy == 0)
		return 0;
	vfs_cred_kernel(&kernel_cred);

	for (off = 0; off < dp->size; off += sizeof(de)) {
		result = readi(dp, &kernel_cred, 0, (uint64)&de, off,
			       sizeof(de));
		if (result != sizeof(de)) {
			if (status && result < 0)
				*status = result;
			if (found)
				iput(found);
			return 0;
		}
		if (de.inum == 0)
			continue;
		if (strncmp(name, de.name, DIRSIZ) == 0) {
			target = inode_get(dp->dev, de.inum);
			if (target == 0) {
				if (found)
					iput(found);
				return 0;
			}
			result = ivalid(target);
			if (result < 0) {
				if (status)
					*status = result;
				iput(target);
				if (found)
					iput(found);
				return 0;
			}
			if (!vfs_inode_label_valid(target)) {
				iput(target);
				if (found)
					iput(found);
				return 0;
			}
			if (policy != 0 && target->vfs_policy != policy) {
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
				iput(found);
				return 0;
			}
			found = target;
			found_off = off;
		}
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
}

//Show the filenames of all files in the directory
int dirls(struct inode *dp, const struct vfs_cred *cred)
{
	uint64 off, count;
	struct dirent de;
	struct inode *target;
	char name[DIRSIZ + 1];

	if (dp == 0 || dp->type != T_DIR)
		return -1;
	if (!vfs_inode_authorize(dp, cred, VFS_OP_READ))
		return -1;

	count = 0;
	for (off = 0; off < dp->size; off += sizeof(de)) {
		if (readi(dp, cred, 0, (uint64)&de, off, sizeof(de)) !=
		    sizeof(de))
			return -1;
		if (de.inum == 0)
			continue;
		target = inode_get(dp->dev, de.inum);
		if (target == 0)
			return -1;
		if (ivalid(target) < 0) {
			iput(target);
			return -1;
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
	return count;
}

// Write a new directory entry (name, inum) into the directory dp.
int dirlink(struct inode *dp, char *name, uint inum,
	    const struct vfs_cred *cred)
{
	int off;
	int empty_off = -1;
	struct dirent de;
	struct inode *ip;
	struct inode *target;
	struct vfs_cred kernel_cred;
	struct fs_storage_charge charge = {
		.owner = FS_OWNER_SYSTEM,
		.level = FS_CHARGE_SYSTEM,
	};
	uint target_policy;
	uint target_scope_id;
	int lookup_status;
	int result;
	if (dp == 0 || dp->type != T_DIR)
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
	// Validate namespace uniqueness, immutable SYSTEM-name reservation, and
	// free-slot selection in one pass.  The fixed root table is intentionally
	// large, so multiplying full scans would defeat block-I/O fairness.
	for (off = 0; off < dp->size; off += sizeof(de)) {
		result = readi(dp, &kernel_cred, 0, (uint64)&de, off,
			       sizeof(de));
		if (result != sizeof(de))
			return result < 0 ? result : -1;
		if (de.inum == 0) {
			if (empty_off < 0)
				empty_off = off;
			continue;
		}
		if (strncmp(name, de.name, DIRSIZ) != 0)
			continue;
		ip = inode_get(dp->dev, de.inum);
		if (ip == 0)
			return -1;
		result = ivalid(ip);
		if (result < 0) {
			iput(ip);
			return result;
		}
		if (!vfs_inode_label_valid(ip)) {
			iput(ip);
			return -1;
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
		if (lookup_status)
			return -1;
	}
	if (empty_off < 0)
		return -1;
	off = empty_off;
	strncpy(de.name, name, DIRSIZ);
	de.inum = inum;
	result = writei_charged(dp, &kernel_cred, &charge, 0,
				(uint64)&de, off, sizeof(de));
	if (result != sizeof(de))
		return fs_io_health == FS_IO_INDETERMINATE ?
			FS_LOOKUP_INDETERMINATE : (result < 0 ? result : -1);
	result = fs_durable_barrier_forward();
	if (result < 0)
		return FS_LOOKUP_INDETERMINATE;
	return 0;
}

int dirunlink(struct inode *dp, char *name, uint offset, uint expected_inum,
	      uint expected_incarnation, const struct vfs_cred *cred,
	      uint policy)
{
	struct dirent de;
	struct inode *target;
	struct vfs_cred kernel_cred;
	struct fs_storage_charge charge = {
		.owner = FS_OWNER_SYSTEM,
		.level = FS_CHARGE_SYSTEM,
	};
	int result;

	if (dp == 0 || dp->type != T_DIR)
		return -1;
	if (!vfs_inode_authorize(dp, cred, VFS_OP_DELETE))
		return -1;
	if (offset >= dp->size || offset % sizeof(de) != 0)
		return -1;
	vfs_cred_kernel(&kernel_cred);
	result = readi(dp, &kernel_cred, 0, (uint64)&de, offset,
		       sizeof(de));
	if (result != sizeof(de))
		return result < 0 ? result : -1;
	if (de.inum != expected_inum ||
	    strncmp(name, de.name, DIRSIZ) != 0)
		return -1;
	target = inode_get(dp->dev, de.inum);
	if (target == 0)
		return -1;
	result = ivalid(target);
	if (result < 0) {
		iput(target);
		return result;
	}
	if (!vfs_inode_label_valid(target) ||
	    target->vfs_policy != policy ||
	    target->vfs_incarnation != expected_incarnation ||
	    !vfs_inode_authorize(target, cred, VFS_OP_DELETE) ||
	    !exec_policy_inode_mutable(target)) {
		iput(target);
		return -1;
	}
	iput(target);
	memset(&de, 0, sizeof(de));
	result = writei_charged(dp, &kernel_cred, &charge, 0,
				(uint64)&de, offset, sizeof(de));
	if (result != sizeof(de))
		return result < 0 ? result : -1;
	result = fs_durable_barrier_forward();
	if (result < 0)
		return result;
	return 0;
}

int fs_rollback_created_workflow(char *path, uint expected_dev,
				 uint expected_inum, uint expected_incarnation,
				 uint scope_id)
{
	struct inode *dp, *ip;
	struct vfs_cred cred;
	uint offset;
	int status;

	if (path == 0 || path[0] == 0 || strlen(path) > DIRSIZ ||
	    expected_dev == 0 || expected_inum == 0 ||
	    expected_incarnation == 0 ||
	    scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG || !vfs_scope_retained(scope_id))
		return -1;
	vfs_cred_kernel(&cred);
	dp = root_dir_status(&status);
	if (dp == 0 || status != FS_LOOKUP_FOUND || dp->dev != expected_dev)
		goto fail_parent;
	ip = dirlookup(dp, path, &offset, VFS_POLICY_WORKFLOW, scope_id,
		       &status);
	if (ip == 0 || status != FS_LOOKUP_FOUND || ip->dev != expected_dev ||
	    ip->inum != expected_inum ||
	    ip->vfs_incarnation != expected_incarnation || ip->type != T_FILE ||
	    ip->agent_meta_slot != 0 || ip->agent_meta_flags != 0 ||
	    ip->agent_meta_version != 0 || !agent_edit_unlink_allowed(ip))
		goto fail_target;
	if (dirunlink(dp, path, offset, expected_inum, expected_incarnation,
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
							if (writei_charged(
								    dp, &kernel_cred,
								    &system_charge, 0,
								    (uint64)&empty, off,
								    sizeof(empty)) !=
							    sizeof(empty)) {
								iput(target);
								iput(dp);
								return -1;
							}
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
	int lookup_status;
	int root_status;
	int result;

	if (created)
		*created = 0;
	if (status)
		*status = FS_LOOKUP_ERROR;
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
	if (!vfs_inode_authorize(dp, cred, VFS_OP_CREATE)) {
		iput(dp);
		return 0;
	}
	ip = dirlookup(dp, path, 0, policy,
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
	lookup_status = fs_allocator_fault_before(
		FSALLOC_OP_IALLOC, FSALLOC_PHASE_OWNER, 0);
	if (lookup_status < 0) {
		result = lookup_status;
		goto fail_allocated;
	}
	result = vfs_inode_init_label(ip, cred, policy);
	if (result < 0)
		goto fail_allocated;
	result = iupdate(ip);
	if (result < 0)
		goto fail_allocated;
	result = fs_durable_barrier_forward();
	if (result < 0)
		goto fail_allocated;
	FS_ALLOCATOR_FAULT_AFTER(FSALLOC_OP_IALLOC, FSALLOC_PHASE_OWNER);
	result = dirlink(dp, path, ip->inum, cred);
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
