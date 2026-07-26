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
#include "bio.h"
#include "defs.h"
#include "exec_policy.h"
#include "file.h"
#include "kernel_work.h"
#include "proc.h"
#include "riscv.h"
#include "types.h"
#include "vfs_security.h"
// there should be one superblock per disk device, but we run with
// only one device
struct superblock sb;

struct fs_storage_state {
	uint free_blocks;
	uint free_inodes;
	uint block_alloc_cursor;
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
static int fs_storage_accounts_ready;
static struct wait_queue fs_claim_waiters;
static struct thread *fs_claim_owner;
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

// Read the super block.
static void readsb(int dev, struct superblock *sb)
{
	struct buf *bp;
	bp = bread(dev, 1);
	memmove(sb, bp->data, sizeof(*sb));
	brelse(bp);
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

static uint fs_qmap_read(int dev, uint block)
{
	struct buf *bp;
	uint owner;

	bp = bread(dev, QBLOCK(block, sb));
	owner = ((uint *)bp->data)[block % QPB];
	brelse(bp);
	return owner;
}

static void fs_qmap_write(int dev, uint block, uint owner)
{
	struct buf *bp;

	bp = bread(dev, QBLOCK(block, sb));
	((uint *)bp->data)[block % QPB] = owner;
	bwrite(bp);
	brelse(bp);
}

static int fs_scrub_bit_test(const uchar *map, uint bit)
{
	return (map[bit / 8] & (1U << (bit % 8))) != 0;
}

static void fs_scrub_bit_set(uchar *map, uint bit)
{
	map[bit / 8] |= 1U << (bit % 8);
}

static int fs_scrub_block_allocated(int dev, uint block)
{
	struct buf *bp;
	uint bit;
	int allocated;

	if (block < sb.datastart || block >= sb.size)
		panic("filesystem block reference out of range");
	bp = bread(dev, BBLOCK(block, sb));
	bit = block % BPB;
	allocated = (bp->data[bit / 8] & (1U << (bit % 8))) != 0;
	brelse(bp);
	return allocated;
}

static void fs_scrub_mark_block(int dev, uint block)
{
	if (block < sb.datastart || block >= sb.size)
		panic("filesystem block reference out of range");
	if (fs_scrub_bit_test(fs_scrub_reachable_blocks, block))
		panic("duplicate filesystem block reference");
	if (!fs_scrub_block_allocated(dev, block))
		panic("inode references free filesystem block");
	fs_scrub_bit_set(fs_scrub_reachable_blocks, block);
}

// Mark every block retained by an inode, including allocations just beyond
// EOF left by an interrupted append.  Such mappings remain reclaimable by a
// later truncate/unlink and must not be handed to another inode.
static void fs_scrub_mark_inode_blocks(int dev, const struct dinode *dip)
{
	struct buf *bp;
	uint *entries;
	uint needed;

	if (dip->size > MAXFILE * BSIZE)
		panic("inode size exceeds filesystem limit");
	needed = fs_div_round_up(dip->size, BSIZE);
	for (uint i = 0; i < NDIRECT; i++) {
		if (i < needed && dip->addrs[i] == 0)
			panic("reachable inode has missing data block");
		if (dip->addrs[i] != 0)
			fs_scrub_mark_block(dev, dip->addrs[i]);
	}
	if (dip->addrs[NDIRECT] == 0) {
		if (needed > NDIRECT)
			panic("reachable inode has missing indirect block");
		return;
	}
	fs_scrub_mark_block(dev, dip->addrs[NDIRECT]);
	bp = bread(dev, dip->addrs[NDIRECT]);
	entries = (uint *)bp->data;
	for (uint i = 0; i < NINDIRECT; i++) {
		if (i < needed - MIN(needed, NDIRECT) && entries[i] == 0)
			panic("reachable inode has missing data block");
		if (entries[i] != 0)
			fs_scrub_mark_block(dev, entries[i]);
	}
	brelse(bp);
}

static void fs_scrub_read_dinode(int dev, uint inum, struct dinode *out)
{
	struct buf *bp;
	struct dinode *dip;

	bp = bread(dev, IBLOCK(inum, sb));
	dip = (struct dinode *)bp->data + inum % IPB;
	memmove(out, dip, sizeof(*out));
	brelse(bp);
}

static uint fs_scrub_inode_block(int dev, const struct dinode *dip, uint bn)
{
	struct buf *bp;
	uint block;

	if (bn < NDIRECT)
		return dip->addrs[bn];
	bn -= NDIRECT;
	if (bn >= NINDIRECT || dip->addrs[NDIRECT] == 0)
		return 0;
	bp = bread(dev, dip->addrs[NDIRECT]);
	block = ((uint *)bp->data)[bn];
	brelse(bp);
	return block;
}

static void fs_scrub_mark_root_entries(int dev, const struct dinode *root)
{
	uint blocks;
	uint entries_seen = 0;

	if (root->size == 0 || root->size % sizeof(struct dirent) != 0 ||
	    root->size > sb.ninodes * sizeof(struct dirent))
		panic("invalid flat root directory size");
	blocks = fs_div_round_up(root->size, BSIZE);
	for (uint bn = 0; bn < blocks; bn++) {
		struct buf *bp;
		uint block = fs_scrub_inode_block(dev, root, bn);
		uint bytes = MIN(root->size - bn * BSIZE, BSIZE);

		if (block == 0)
			panic("root directory has missing block");
		bp = bread(dev, block);
		for (uint off = 0; off < bytes; off += sizeof(struct dirent)) {
			struct dirent de;
			struct dinode child;

			memmove(&de, bp->data + off, sizeof(de));
			entries_seen++;
			if (de.inum == 0)
				continue;
			if (de.inum >= sb.ninodes)
				panic("directory entry inode out of range");
			if (fs_scrub_bit_test(fs_scrub_reachable_inodes,
					      de.inum))
				panic("duplicate directory inode reference");
			fs_scrub_read_dinode(dev, de.inum, &child);
			if (child.type == 0)
				panic("directory entry references free inode");
			if (child.type != T_FILE)
				panic("nested inode in flat filesystem");
			fs_scrub_bit_set(fs_scrub_reachable_inodes, de.inum);
			fs_scrub_mark_inode_blocks(dev, &child);
		}
		brelse(bp);
	}
	if (entries_seen != root->size / sizeof(struct dirent))
		panic("root directory scan invariant");
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

// The filesystem has one flat, fixed-size root namespace.  Reconstructing
// reachability from that root makes interrupted unlink/allocation cleanup
// independent of process lifetime and keeps persistent quota usage bounded.
static void fs_mount_scrub(int dev)
{
	struct dinode root;

	memset(fs_scrub_reachable_blocks, 0,
	       sizeof(fs_scrub_reachable_blocks));
	memset(fs_scrub_reachable_inodes, 0,
	       sizeof(fs_scrub_reachable_inodes));
	fs_scrub_read_dinode(dev, ROOTINO, &root);
	if (root.type != T_DIR)
		panic("missing filesystem root inode");
	fs_scrub_bit_set(fs_scrub_reachable_inodes, ROOTINO);
	fs_scrub_mark_inode_blocks(dev, &root);
	fs_scrub_mark_root_entries(dev, &root);

	// Publish every orphan inode as a valid FREE object before releasing its
	// blocks.  A reset during either pass is therefore safe to retry.
	for (uint inum = 1; inum < sb.ninodes; inum++) {
		struct buf *bp;
		struct dinode *dip;

		if (fs_scrub_bit_test(fs_scrub_reachable_inodes, inum))
			continue;
		bp = bread(dev, IBLOCK(inum, sb));
		dip = (struct dinode *)bp->data + inum % IPB;
		if (dip->type != 0) {
			fs_scrub_retire_dinode(dip, inum);
			bwrite(bp);
		}
		brelse(bp);
	}

	for (uint block = sb.datastart; block < sb.size;) {
		struct buf *bp = bread(dev, BBLOCK(block, sb));
		uint bitmap_block = BBLOCK(block, sb);
		int dirty = 0;

		for (; block < sb.size && BBLOCK(block, sb) == bitmap_block;
		     block++) {
			uint bit = block % BPB;
			int allocated =
				(bp->data[bit / 8] & (1U << (bit % 8))) != 0;
			int reachable = fs_scrub_bit_test(
				fs_scrub_reachable_blocks, block);

			if (reachable && !allocated)
				panic("reachable filesystem block is free");
			if (!reachable && allocated) {
				// Owner first is retry-safe: this block remains
				// unreachable if power fails before the bitmap write.
				fs_qmap_write(dev, block, FS_OWNER_NONE);
				bp->data[bit / 8] &= ~(1U << (bit % 8));
				dirty = 1;
			}
		}
		if (dirty)
			bwrite(bp);
		brelse(bp);
	}
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

static void fs_storage_accounts_sync(uint public_blocks,
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
		public_limits.class_limit[RESOURCE_CHARGE_ORDINARY]
					 [RESOURCE_FS_BLOCK] =
			fs_storage.block_domain_limit;
		public_limits.class_limit[RESOURCE_CHARGE_ORDINARY]
					 [RESOURCE_FS_INODE] =
			fs_storage.inode_domain_limit;
		public_limits.class_limit[RESOURCE_CHARGE_ORDINARY]
					 [RESOURCE_BUFFER_CACHE] =
			IO_CACHE_PUBLIC_CAP;
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
			panic("filesystem resource accounts");
		fs_storage_accounts_ready = 1;
		return;
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
		panic("filesystem resource rebuild mismatch");
}

static void fs_storage_rebuild(int dev, int enforce_policy)
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
	for (uint block = sb.datastart; block < sb.size; block++) {
		uint current = BBLOCK(block, sb);
		uint bit = block % BPB;
		uint owner;

		if (current != bitmap_block) {
			if (bitmap)
				brelse(bitmap);
			bitmap = bread(dev, current);
			bitmap_block = current;
		}
		if ((bitmap->data[bit / 8] & (1 << (bit % 8))) == 0) {
			fs_storage.free_blocks++;
			continue;
		}
		owner = fs_qmap_read(dev, block);
		if (owner < FS_OWNER_SYSTEM)
			panic("allocated block has no storage owner");
		if (FS_OWNER_IS_SCOPE(owner)) {
			uint scope_id = FS_OWNER_SCOPE_ID(owner);

			if (scope_id < VFS_SCOPE_FIRST_DYNAMIC)
				panic("allocated block has invalid workflow owner");
			if (scope_id > max_scope_id)
				max_scope_id = scope_id;
			reserved_blocks++;
		} else if (owner == fs_storage.public_principal_id) {
			if (public_blocks == (uint)-1)
				panic("public block accounting overflow");
			public_blocks++;
		} else if (owner != FS_OWNER_SYSTEM) {
			panic("allocated block has invalid public owner");
		} else {
			reserved_blocks++;
		}
	}
	if (bitmap)
		brelse(bitmap);

	for (uint inum = 1; inum < sb.ninodes; inum++) {
		struct buf *bp = bread(dev, IBLOCK(inum, sb));
		struct dinode *dip = (struct dinode *)bp->data + inum % IPB;

		if (dip->type == 0) {
			if (dip->fs_owner_domain != FS_OWNER_NONE ||
			    dip->fs_owner_version != 0)
				panic("free inode has storage owner");
			fs_storage.free_inodes++;
		} else {
			uint owner = dip->fs_owner_domain;

			if (owner < FS_OWNER_SYSTEM ||
			    dip->fs_owner_version != FS_OWNER_VERSION)
				panic("allocated inode has invalid storage owner");
			if (FS_OWNER_IS_SCOPE(owner)) {
				uint scope_id = FS_OWNER_SCOPE_ID(owner);

				if (scope_id < VFS_SCOPE_FIRST_DYNAMIC)
					panic("inode has invalid workflow owner");
				if (scope_id > max_scope_id)
					max_scope_id = scope_id;
				reserved_inodes++;
			} else if (owner == fs_storage.public_principal_id) {
				if (public_inodes == (uint)-1)
					panic("public inode accounting overflow");
				public_inodes++;
			} else if (owner != FS_OWNER_SYSTEM) {
				panic("inode has invalid public owner");
			} else {
				reserved_inodes++;
			}
			if (dip->vfs_version == VFS_LABEL_VERSION &&
			    dip->vfs_policy == VFS_POLICY_WORKFLOW &&
			    dip->vfs_scope_id >= VFS_SCOPE_FIRST_DYNAMIC) {
				if (!FS_OWNER_IS_SCOPE(dip->fs_owner_domain) ||
				    FS_OWNER_SCOPE_ID(dip->fs_owner_domain) !=
					    dip->vfs_scope_id)
					panic("workflow inode owner mismatch");
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
		panic("filesystem cannot fund workflow guarantees");
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
	proc_scope_set_id_floor(max_scope_id >= FS_OWNER_ID_MASK ?
				FS_OWNER_NONE : max_scope_id + 1);
	fs_storage_accounts_sync(public_blocks, public_inodes,
				 reserved_blocks, reserved_inodes);
	fs_storage.ready = 1;
}

int fs_storage_scope_account_create(
	uint scope_id, struct resource_account_handle *out)
{
	struct resource_account_limits limits;
	uint owner;
	int enabled;

	if (out == 0 || scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    scope_id >= FS_OWNER_SCOPE_FLAG)
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
	if (cred->kernel || cred->storage_principal_id >= FS_OWNER_SCOPE_FLAG)
		return -1;
	if (cred->scope_id >= VFS_SCOPE_FIRST_DYNAMIC) {
		if (cred->scope_id >= FS_OWNER_SCOPE_FLAG ||
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

	if (!fs_storage.ready || owner < FS_OWNER_SYSTEM)
		panic("storage release invariant");
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
		if (wait_queue_sleep(&fs_claim_waiters) != WAIT_QUEUE_OK) {
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

static int fs_claim_checkpoint(int transfer)
{
	int status = transfer ?
		bio_request_checkpoint_quiescent_cleanup() :
		bio_request_checkpoint_quiescent();

	// Once qmap-first publication starts, rollback is neither crash-safe nor
	// quota-safe. The cleanup checkpoint defers thread exit until the bounded
	// forward commit reaches the inode publication point.
	if (transfer && status < 0)
		panic("storage claim commit checkpoint");
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

	for (uint i = 0; i < NDIRECT; i++)
		if (addrs[i] != 0)
			fs_claim_blocks[block_count++] = addrs[i];
	if (addrs[NDIRECT] != 0) {
		fs_claim_blocks[block_count++] = addrs[NDIRECT];
		bp = bread(dev, addrs[NDIRECT]);
		for (uint i = 0; i < NINDIRECT; i++) {
			uint block = ((uint *)bp->data)[i];

			if (block != 0)
				fs_claim_blocks[block_count++] = block;
		}
		brelse(bp);
		if (fs_claim_checkpoint(transfer) < 0)
			return -1;
	}
	if (block_count > MAXFILE + 1)
		panic("storage claim block list overflow");

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
			panic("storage claim block range");
		if (i != 0 && fs_claim_blocks[i] == fs_claim_blocks[i - 1])
			panic("duplicate storage claim block");
	}

	for (uint base = 0; base < block_count;) {
		uint qblock = QBLOCK(fs_claim_blocks[base], sb);
		uint end = base + 1;
		int dirty = 0;

		while (end < block_count &&
		       QBLOCK(fs_claim_blocks[end], sb) == qblock)
			end++;
		bp = bread(dev, qblock);
		owners = (uint *)bp->data;
		for (uint i = base; i < end; i++) {
			uint block = fs_claim_blocks[i];
			uint owner = owners[block % QPB];

			if (owner == FS_OWNER_PUBLIC) {
				if (public_seen)
					*public_seen = 1;
				continue;
			}
			if (owner != FS_OWNER_SYSTEM)
				panic("storage claim block owner");
			count++;
			if (transfer) {
				owners[block % QPB] = FS_OWNER_PUBLIC;
				dirty = 1;
				if (public_seen)
					*public_seen = 1;
			}
		}
		if (dirty)
			bwrite(bp);
		brelse(bp);
		if (fs_claim_checkpoint(transfer) < 0)
			return -1;
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
static void fs_recover_public_claims(int dev)
{
	for (uint inum = 1; inum < sb.ninodes; inum++) {
		struct buf *bp = bread(dev, IBLOCK(inum, sb));
		struct dinode *dip = (struct dinode *)bp->data + inum % IPB;
		uint system_blocks;
		uint converted_blocks;
		int public_seen = 0;

		if (dip->type != T_FILE ||
		    (dip->fs_owner_domain != FS_OWNER_SYSTEM &&
		     dip->fs_owner_domain != FS_OWNER_PUBLIC)) {
			brelse(bp);
			continue;
		}
		if (fs_claim_inode_blocks(dev, dip->addrs, 0, &public_seen,
					  &system_blocks) < 0)
			panic("PUBLIC claim recovery preflight");
		if (dip->fs_owner_domain == FS_OWNER_PUBLIC) {
			if (system_blocks != 0)
				panic("committed PUBLIC inode has SYSTEM blocks");
			brelse(bp);
			continue;
		}
		if (!public_seen) {
			brelse(bp);
			continue;
		}
		if (dip->fs_owner_version != FS_OWNER_VERSION ||
		    !fs_dinode_is_mutable_public(dip, inum))
			panic("invalid interrupted PUBLIC claim");
		if (fs_claim_inode_blocks(dev, dip->addrs, 1, 0,
					  &converted_blocks) < 0 ||
		    converted_blocks != system_blocks)
			panic("PUBLIC claim recovery changed beneath scanner");
		dip->fs_owner_domain = FS_OWNER_PUBLIC;
		dip->vfs_checksum = vfs_label_checksum(
			inum, dip->vfs_magic, dip->vfs_version, dip->vfs_flags,
			dip->vfs_scope_id, dip->vfs_policy,
			dip->vfs_exec_profile, dip->vfs_policy_generation,
			dip->vfs_incarnation, dip->fs_owner_domain,
			dip->fs_owner_version);
		bwrite(bp);
		brelse(bp);
	}
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
				  &converted_blocks) < 0 ||
	    converted_blocks != missing_blocks)
		panic("PUBLIC claim changed beneath scanner");
	ip->fs_owner_domain = FS_OWNER_PUBLIC;
	ip->vfs_checksum = vfs_label_checksum(
		ip->inum, ip->vfs_magic, ip->vfs_version, ip->vfs_flags,
		ip->vfs_scope_id, ip->vfs_policy, ip->vfs_exec_profile,
		ip->vfs_policy_generation, ip->vfs_incarnation,
		ip->fs_owner_domain, ip->fs_owner_version);
	iupdate(ip);
	if (bio_request_checkpoint_quiescent_cleanup() < 0)
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

	if (inum == 0 || inum >= sb.ninodes)
		return 0;
	bp = bread(dev, IBLOCK(inum, sb));
	dip = (struct dinode *)bp->data + inum % IPB;
	owned = dip->type != 0 && dip->fs_owner_version == FS_OWNER_VERSION &&
		FS_OWNER_IS_SCOPE(dip->fs_owner_domain) &&
		FS_OWNER_SCOPE_ID(dip->fs_owner_domain) >=
			VFS_SCOPE_FIRST_DYNAMIC;
	brelse(bp);
	return owned;
}

// Dynamic workflow namespaces are boot leases. No persistent recovery token
// exists yet, so a reboot revokes every old lease before a new workflow can
// be admitted. The three passes are idempotent across power loss: names are
// detached first, dinodes are then retired, and tagged orphan blocks last.
static void fs_reap_boot_workflow_objects(int dev)
{
	struct fs_storage_charge system_charge = {
		.owner = FS_OWNER_SYSTEM,
		.level = FS_CHARGE_SYSTEM,
	};
	struct vfs_cred kernel_cred;
	struct inode *root;
	struct dirent de;

	vfs_cred_kernel(&kernel_cred);
	root = root_dir();
	if (root == 0)
		panic("missing root during workflow reap");
	for (uint64 off = 0; off < root->size; off += sizeof(de)) {
		if (readi(root, &kernel_cred, 0, (uint64)&de, off,
			  sizeof(de)) != sizeof(de))
			panic("workflow directory reap read");
		if (de.inum == 0 ||
		    !fs_dinode_has_scope_owner(dev, de.inum))
			continue;
		memset(&de, 0, sizeof(de));
		if (writei_charged(root, &kernel_cred, &system_charge, 0,
				   (uint64)&de, off, sizeof(de)) != sizeof(de))
			panic("workflow directory reap write");
	}
	iput(root);

	for (uint inum = 1; inum < sb.ninodes; inum++) {
		struct buf *bp = bread(dev, IBLOCK(inum, sb));
		struct dinode *dip = (struct dinode *)bp->data + inum % IPB;
		uint incarnation;

		if (dip->type == 0 ||
		    !FS_OWNER_IS_SCOPE(dip->fs_owner_domain)) {
			brelse(bp);
			continue;
		}
		incarnation = dip->vfs_incarnation;
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
			dip->vfs_scope_id, dip->vfs_policy,
			dip->vfs_exec_profile, dip->vfs_policy_generation,
			dip->vfs_incarnation, dip->fs_owner_domain,
			dip->fs_owner_version);
		bwrite(bp);
		brelse(bp);
	}

	for (uint block = sb.datastart; block < sb.size; block++) {
		struct buf *bp;
		uint owner = fs_qmap_read(dev, block);
		uint bit;

		if (!FS_OWNER_IS_SCOPE(owner))
			continue;
		bp = bread(dev, BBLOCK(block, sb));
		bit = block % BPB;
		if (bp->data[bit / 8] & (1 << (bit % 8))) {
			bp->data[bit / 8] &= ~(1 << (bit % 8));
			bwrite(bp);
		}
		brelse(bp);
		fs_qmap_write(dev, block, FS_OWNER_NONE);
	}
}

// Init fs
void fsinit()
{
	int dev = ROOTDEV;
	readsb(dev, &sb);
	if (!fs_layout_valid()) {
		panic("invalid file system");
	}
	fs_claim_owner = 0;
	wait_queue_init(&fs_claim_waiters, WAIT_REASON_FS_CLAIM);
	fs_mount_scrub(dev);
	fs_recover_public_claims(dev);
	fs_storage_rebuild(dev, 0);
	fs_reap_boot_workflow_objects(dev);
	fs_storage_rebuild(dev, 1);
}

// Zero a block.
static void bzero(int dev, int bno)
{
	struct buf *bp;
	bp = bread(dev, bno);
	bclaim(bp);
	memset(bp->data, 0, BSIZE);
	bwrite(bp);
	brelse(bp);
}

// Blocks.

// Allocate a zeroed disk block.
static uint balloc(uint dev, const struct fs_storage_charge *charge)
{
	uint b, bi, block, first, limit, range_start, range_end;
	uint cursor;
	int checkpoint, m, pass;
	struct buf *bp;

	if (fs_storage_reserve(charge, 0) < 0)
		return 0;
	cursor = fs_storage.block_alloc_cursor;
	if (cursor < sb.datastart || cursor >= sb.size)
		cursor = sb.datastart;
	for (pass = 0; pass < 2; pass++) {
		range_start = pass == 0 ? cursor : sb.datastart;
		range_end = pass == 0 ? sb.size : cursor;
		if (range_start >= range_end)
			continue;
		for (b = range_start - range_start % BPB;
		     b < range_end; b += BPB) {
			first = range_start > b ? range_start - b : 0;
			if (b + first < sb.datastart)
				first = sb.datastart - b;
			limit = MIN(BPB, range_end - b);
			bp = bread(dev, BBLOCK(b, sb));
			for (bi = first; bi < limit; bi++) {
				block = b + bi;
				m = 1 << (bi % 8);
				if ((bp->data[bi / 8] & m) == 0) { // Is block free?
					fs_qmap_write(dev, block, charge->owner);
					bp->data[bi / 8] |= m; // Mark block in use.
					bwrite(bp);
					brelse(bp);
					fs_storage.block_alloc_cursor = block + 1;
					if (fs_storage.block_alloc_cursor >= sb.size)
						fs_storage.block_alloc_cursor = sb.datastart;
					bzero(dev, block);
					return block;
				}
			}
			brelse(bp);
			fs_storage.block_alloc_cursor = b + limit;
			if (fs_storage.block_alloc_cursor >= sb.size)
				fs_storage.block_alloc_cursor = sb.datastart;
			checkpoint = bio_request_checkpoint();
			if (checkpoint == BIO_CHECKPOINT_INTERRUPTED) {
				fs_storage_release(charge->owner, 0);
				return 0;
			}
			/*
			 * DEFERRED means this filesystem atomic unit cannot sleep yet.
			 * The scan owns a reservation and is guaranteed a free block, so
			 * continue from the rotating cursor and settle debt at the outer
			 * syscall boundary. Treating DEFERRED as cancellation livelocks
			 * allocations that cross a bitmap block.
			 */
		}
	}
	fs_storage_release(charge->owner, 0);
	return 0;
}

// Free a disk block.
static void bfree(int dev, uint b)
{
	struct buf *bp;
	int bi, m;
	uint owner;

	if (b < sb.datastart || b >= sb.size)
		panic("freeing non-data block");

	bp = bread(dev, BBLOCK(b, sb));
	bi = b % BPB;
	m = 1 << (bi % 8);
	if ((bp->data[bi / 8] & m) == 0)
		panic("freeing free block");
	bp->data[bi / 8] &= ~m;
	bwrite(bp);
	brelse(bp);
	owner = fs_qmap_read(dev, b);
	if (owner < FS_OWNER_SYSTEM)
		panic("freeing unowned block");
	fs_qmap_write(dev, b, FS_OWNER_NONE);
	fs_storage_release(owner, 0);
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
		     const struct fs_storage_charge *charge)
{
	int inum;
	uint incarnation;
	struct buf *bp;
	struct dinode *dip;
	struct inode *ip;

	if (charge == 0 || fs_storage_reserve(charge, 1) < 0)
		return 0;
	for (inum = 1; inum < sb.ninodes; inum++) {
		bp = bread(dev, IBLOCK(inum, sb));
		dip = (struct dinode *)bp->data + inum % IPB;
		if (dip->type == 0) { // a free inode
			ip = iget(dev, inum);
			if (ip == 0) {
				brelse(bp);
				fs_storage_release(charge->owner, 1);
				return 0;
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
			dip->fs_owner_domain = charge->owner;
			dip->fs_owner_version = FS_OWNER_VERSION;
			dip->vfs_checksum = vfs_label_checksum(
				inum, dip->vfs_magic, dip->vfs_version,
			dip->vfs_flags, dip->vfs_scope_id,
				dip->vfs_policy, dip->vfs_exec_profile,
				dip->vfs_policy_generation,
				dip->vfs_incarnation, dip->fs_owner_domain,
				dip->fs_owner_version);
			bwrite(bp);
			brelse(bp);
			return ip;
		}
		brelse(bp);
		if (inum % IPB == IPB - 1 &&
		    bio_request_checkpoint() < 0) {
			fs_storage_release(charge->owner, 1);
			return 0;
		}
	}
	fs_storage_release(charge->owner, 1);
	return 0;
}

// Copy a modified in-memory inode to disk.
// Must be called after every change to an on-disk inode field
// that lives on disk.
void iupdate(struct inode *ip)
{
	struct buf *bp;
	struct dinode *dip;

	bp = bread(ip->dev, IBLOCK(ip->inum, sb));
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
	bwrite(bp);
	brelse(bp);
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
void ivalid(struct inode *ip)
{
	struct buf *bp;
	struct dinode *dip;
	if (ip->valid == 0) {
		bp = bread(ip->dev, IBLOCK(ip->inum, sb));
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
}

// Publish a removed inode as free before releasing its detached block token.
// The token can be drained synchronously by iput() or incrementally by a
// background reclaimer without keeping an inode or buffer pinned.
int inode_remove_detach(struct inode *ip, struct inode_reclaim *reclaim)
{
	struct vfs_cred kernel_cred;
	uint storage_owner;

	if (ip == 0 || reclaim == 0 || ip->ref != 1 || !ip->valid ||
	    !ip->removed)
		return 0;
	storage_owner = ip->fs_owner_domain;
	vfs_cred_kernel(&kernel_cred);
	if (itruncate_detach(ip, &kernel_cred, 0, reclaim) < 0) {
		ip->removed = 0;
		ip->ref--;
		return -1;
	}
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
	iupdate(ip);
	fs_storage_release(storage_owner, 1);
	ip->valid = 0;
	ip->removed = 0;
	ip->ref--;
	return 1;
}

// Drop a reference to an in-memory inode. Removed objects first publish a
// detached, free inode and then reclaim the private block token.
void iput(struct inode *ip)
{
	if (ip->ref == 1 && ip->valid && ip->removed) {
		struct inode_reclaim reclaim;
		int detached = inode_remove_detach(ip, &reclaim);

		if (detached > 0)
			itruncate_reclaim(&reclaim);
		return;
	}
	ip->ref--;
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
static uint bmap(struct inode *ip, uint bn, int alloc,
		 const struct fs_storage_charge *charge)
{
	uint addr, candidate, indirect, *a;
	struct buf *bp;
	int indirect_allocated = 0;

	if (bn < NDIRECT) {
		addr = ip->addrs[bn];
		if (addr == 0 && alloc) {
			candidate = balloc(ip->dev, charge);
			if (candidate == 0)
				return 0;
			if (ip->addrs[bn] == 0) {
				ip->addrs[bn] = candidate;
				addr = candidate;
			} else {
				addr = ip->addrs[bn];
				bfree(ip->dev, candidate);
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
			candidate = balloc(ip->dev, charge);
			if (candidate == 0)
				return 0;
			if (ip->addrs[NDIRECT] == 0) {
				ip->addrs[NDIRECT] = candidate;
				indirect = candidate;
				indirect_allocated = 1;
			} else {
				indirect = ip->addrs[NDIRECT];
				bfree(ip->dev, candidate);
			}
		}
		bp = bread(ip->dev, indirect);
		a = (uint *)bp->data;
		addr = a[bn];
		brelse(bp);
		if (addr == 0 && alloc) {
			candidate = balloc(ip->dev, charge);
			if (candidate != 0) {
				/*
				 * Allocation may cross a budget checkpoint. Reacquire the
				 * indirect block and publish only after revalidating the
				 * entry; a concurrent winner keeps its mapping and this
				 * candidate is returned to the same quota domain.
				 */
				bp = bread(ip->dev, indirect);
				a = (uint *)bp->data;
				if (a[bn] == 0) {
					a[bn] = candidate;
					bwrite(bp);
					addr = candidate;
					candidate = 0;
				} else {
					addr = a[bn];
				}
				brelse(bp);
				if (candidate != 0)
					bfree(ip->dev, candidate);
			}
		}
		if (addr == 0 && indirect_allocated) {
			int empty = 1;

			bp = bread(ip->dev, indirect);
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
			if (empty)
				bfree(ip->dev, indirect);
		}
		return addr;
	}

	return 0;
}

// Undo a block allocated for a write that could not copy any data into it.
static void bmap_discard(struct inode *ip, uint bn)
{
	struct buf *bp;
	uint addr;
	uint indirect;
	uint *a;
	int empty = 1;

	if (bn < NDIRECT) {
		addr = ip->addrs[bn];
		if (addr != 0) {
			ip->addrs[bn] = 0;
			bfree(ip->dev, addr);
		}
		return;
	}
	bn -= NDIRECT;
	if (bn >= NINDIRECT || ip->addrs[NDIRECT] == 0)
		return;
	indirect = ip->addrs[NDIRECT];
	bp = bread(ip->dev, indirect);
	a = (uint *)bp->data;
	addr = a[bn];
	if (addr == 0) {
		brelse(bp);
		return;
	}
	a[bn] = 0;
	for (int i = 0; i < NINDIRECT; i++)
		if (a[i] != 0) {
			empty = 0;
			break;
		}
	bwrite(bp);
	brelse(bp);
	bfree(ip->dev, addr);
	if (empty) {
		ip->addrs[NDIRECT] = 0;
		bfree(ip->dev, indirect);
	}
}

static void truncate_free_block(int dev, uint block)
{
	if (block == 0)
		return;
	bfree(dev, block);
	(void)kernel_work_checkpoint_cleanup(KERNEL_WORK_OPERATION_UNITS);
}

static int itruncate_detach_all(struct inode *ip,
				struct inode_reclaim *reclaim)
{
	reclaim->mode = INODE_RECLAIM_DIRECT;
	reclaim->dev = ip->dev;
	memmove(reclaim->direct, ip->addrs, sizeof(reclaim->direct));
	reclaim->indirect = ip->addrs[NDIRECT];
	memset(ip->addrs, 0, sizeof(ip->addrs));
	ip->size = 0;
	iupdate(ip);
	return 0;
}

static int itruncate_detach_partial(struct inode *ip, uint size,
				    struct inode_reclaim *reclaim)
{
	uint *blocks;
	uint count = 0;
	uint first_discard = (size + BSIZE - 1) / BSIZE;
	struct buf *bp;
	uint *entries;

	_Static_assert(MAXFILE * sizeof(uint) <= PGSIZE,
		       "truncate reclaim list must fit in one page");
	blocks = (uint *)kalloc();
	if (blocks == 0)
		return -1;
	reclaim->mode = INODE_RECLAIM_LIST;
	reclaim->dev = ip->dev;
	reclaim->block_list = blocks;

	for (uint bn = first_discard; bn < NDIRECT; bn++) {
		if (ip->addrs[bn] != 0)
			blocks[count++] = ip->addrs[bn];
		ip->addrs[bn] = 0;
	}
	if (ip->addrs[NDIRECT] != 0) {
		bp = bread(ip->dev, ip->addrs[NDIRECT]);
		entries = (uint *)bp->data;
		if (first_discard <= NDIRECT) {
			reclaim->indirect = ip->addrs[NDIRECT];
			ip->addrs[NDIRECT] = 0;
			for (uint i = 0; i < NINDIRECT; i++)
				if (entries[i] != 0)
					blocks[count++] = entries[i];
		} else {
			uint first_indirect = first_discard - NDIRECT;

			for (uint i = first_indirect; i < NINDIRECT; i++) {
				if (entries[i] != 0)
					blocks[count++] = entries[i];
				entries[i] = 0;
			}
			bwrite(bp);
		}
		brelse(bp);
	}

	// All discarded mappings become unreachable before the first yield.
	ip->size = size;
	iupdate(ip);
	reclaim->block_count = count;
	return 0;
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
		kfree((char *)reclaim->block_list);
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

		if (reclaim->mode == INODE_RECLAIM_DIRECT) {
			if (reclaim->direct_cursor < NDIRECT) {
				block = reclaim->direct[reclaim->direct_cursor++];
				have_unit = 1;
			} else if (reclaim->indirect != 0 &&
				   reclaim->indirect_cursor < NINDIRECT) {
				struct buf *bp = bread(reclaim->dev,
						       reclaim->indirect);

				block = ((uint *)bp->data)[reclaim->indirect_cursor++];
				brelse(bp);
				have_unit = 1;
			} else if (reclaim->indirect != 0) {
				block = reclaim->indirect;
				reclaim->indirect = 0;
				have_unit = 1;
			}
		} else {
			if (reclaim->block_cursor < reclaim->block_count) {
				block = reclaim->block_list[reclaim->block_cursor++];
				have_unit = 1;
			} else if (reclaim->indirect != 0) {
				block = reclaim->indirect;
				reclaim->indirect = 0;
				have_unit = 1;
			}
		}
		if (!have_unit) {
			itruncate_reclaim_finish(reclaim);
			return 1;
		}
		truncate_free_block(reclaim->dev, block);
		units++;
		if (bio_request_checkpoint_cleanup() < 0)
			return 0;
	}
	return 0;
}

void itruncate_reclaim(struct inode_reclaim *reclaim)
{
	while (reclaim != 0 && reclaim->mode != INODE_RECLAIM_NONE)
		(void)itruncate_reclaim_step(reclaim, 1);
}

// Shrink an inode and release every whole block beyond the new end.
int itruncate(struct inode *ip, const struct vfs_cred *cred, uint size)
{
	struct inode_reclaim reclaim;

	if (itruncate_detach(ip, cred, size, &reclaim) < 0)
		return -1;
	itruncate_reclaim(&reclaim);
	return 0;
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
			int user_dst, uint64 dst, uint off, uint n)
{
	uint tot, m, addr;
	struct buf *bp;
	int checkpoint;
	int failed = 0;

	if (!vfs_inode_authorize(ip, cred, VFS_OP_READ))
		return -1;
	if (off > ip->size || off + n < off)
		return 0;
	if (off + n > ip->size)
		n = ip->size - off;

	for (tot = 0; tot < n; tot += m, off += m, dst += m) {
		addr = bmap(ip, off / BSIZE, 0, 0);
		if (addr == 0) {
			failed = 1;
			break;
		}
		bp = bread(ip->dev, addr);
		m = MIN(n - tot, BSIZE - off % BSIZE);
		if (either_copyout(user_dst, dst,
				   (char *)bp->data + (off % BSIZE), m) == -1) {
			brelse(bp);
			failed = 1;
			break;
		}
		brelse(bp);
		checkpoint = bio_request_checkpoint();
		if (checkpoint < 0) {
			tot += m;
			failed = 1;
			break;
		}
	}
	if (failed && tot == 0)
		return -1;
	return tot;
}

int readi(struct inode *ip, const struct vfs_cred *cred, int user_dst,
	  uint64 dst, uint off, uint n)
{
	int result;

	bio_fs_atomic_enter();
	result = readi_atomic(ip, cred, user_dst, dst, off, n);
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
	int checkpoint;
	int failed = 0;
	int inode_changed = 0;

	if (!vfs_inode_authorize(ip, cred, VFS_OP_WRITE))
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
		addr = bmap(ip, bn, 0, 0);
		allocated = 0;
		if (addr == 0) {
			addr = bmap(ip, bn, 1, allocation_charge);
			if (addr == 0) {
				failed = 1;
				break;
			}
			allocated = 1;
		}
		bp = bread(ip->dev, addr);
		m = MIN(n - tot, BSIZE - off % BSIZE);
		if (either_copyin(user_src, src,
				  (char *)bp->data + (off % BSIZE), m) == -1) {
			brelse(bp);
			if (allocated)
				bmap_discard(ip, bn);
			failed = 1;
			break;
		}
		bwrite(bp);
		brelse(bp);
		if (allocated)
			inode_changed = 1;
		checkpoint = bio_request_checkpoint();
		if (checkpoint < 0) {
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
	if (inode_changed)
		iupdate(ip);

	if (failed && tot == 0)
		return -1;
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

	if (status)
		*status = FS_LOOKUP_ERROR;
	if (dp == 0 || dp->type != T_DIR || policy == 0)
		return 0;
	vfs_cred_kernel(&kernel_cred);

	for (off = 0; off < dp->size; off += sizeof(de)) {
		if (readi(dp, &kernel_cred, 0, (uint64)&de, off,
			  sizeof(de)) != sizeof(de)) {
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
			ivalid(target);
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
		ivalid(target);
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
	if (dp == 0 || dp->type != T_DIR)
		return -1;
	if (!vfs_inode_authorize(dp, cred, VFS_OP_CREATE))
		return -1;
	target = inode_get(dp->dev, inum);
	if (target == 0 || !vfs_inode_label_valid(target)) {
		if (target)
			iput(target);
		return -1;
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
		if (readi(dp, &kernel_cred, 0, (uint64)&de, off,
			  sizeof(de)) != sizeof(de))
			return -1;
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
		ivalid(ip);
		if (!vfs_inode_label_valid(ip)) {
			iput(ip);
			return -1;
		}
		lookup_status = ip->vfs_policy == target_policy &&
			(target_policy != VFS_POLICY_WORKFLOW ||
			 ip->vfs_scope_id == target_scope_id);
		if (target_policy == VFS_POLICY_WORKFLOW &&
		    target_scope_id >= VFS_SCOPE_FIRST_DYNAMIC &&
		    ip->vfs_policy == VFS_POLICY_WORKFLOW &&
		    ip->vfs_scope_id == VFS_SCOPE_SYSTEM)
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
	if (writei_charged(dp, &kernel_cred, &charge, 0, (uint64)&de,
			   off, sizeof(de)) != sizeof(de))
		return -1;
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

	if (dp == 0 || dp->type != T_DIR)
		return -1;
	if (!vfs_inode_authorize(dp, cred, VFS_OP_DELETE))
		return -1;
	if (offset >= dp->size || offset % sizeof(de) != 0)
		return -1;
	vfs_cred_kernel(&kernel_cred);
	if (readi(dp, &kernel_cred, 0, (uint64)&de, offset,
		  sizeof(de)) != sizeof(de) || de.inum != expected_inum ||
	    strncmp(name, de.name, DIRSIZ) != 0)
		return -1;
	target = inode_get(dp->dev, de.inum);
	if (target == 0)
		return -1;
	ivalid(target);
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
	if (writei_charged(dp, &kernel_cred, &charge, 0, (uint64)&de,
			   offset, sizeof(de)) != sizeof(de))
		return -1;
	return 0;
}

#define FS_SCOPE_RECLAIM_DIR 1U
#define FS_SCOPE_RECLAIM_INODE 2U
#define FS_SCOPE_RECLAIM_STEP 16U

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
	uint units = 0;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    !vfs_scope_retiring(scope_id))
		return -1;
	cursor = fs_scope_reclaim_cursor_get(scope_id);
	if (cursor == 0)
		return -1;
	vfs_cred_kernel(&kernel_cred);
	while (units++ < FS_SCOPE_RECLAIM_STEP) {
		if (cursor->phase == FS_SCOPE_RECLAIM_DIR) {
			struct inode *dp = root_dir();
			struct inode *target = 0;
			struct dirent de;
			uint64 off = cursor->dir_offset;

			if (dp == 0)
				return -1;
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
			if (de.inum != 0) {
				target = inode_get(dp->dev, de.inum);
				if (target == 0) {
					cursor->failed = 1;
				} else {
					ivalid(target);
					if (vfs_inode_label_valid(target) &&
					    target->vfs_policy ==
						    VFS_POLICY_WORKFLOW &&
					    target->vfs_scope_id == scope_id) {
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
			ivalid(target);
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
		if (bio_request_checkpoint() < 0)
			return FS_RECLAIM_PENDING;
	}
	return FS_RECLAIM_PENDING;
}

//Return the inode of the root directory
struct inode *root_dir()
{
	struct inode *r = iget(ROOTDEV, ROOTINO);
	if (r == 0)
		return 0;
	ivalid(r);
	if (r->type != T_DIR) {
		iput(r);
		return 0;
	}
	return r;
}

struct inode *fs_create(char *path, short type, int *created,
			const struct vfs_cred *cred, uint policy)
{
	struct inode *dp;
	struct inode *ip;
	struct fs_storage_charge charge;
	int lookup_status;

	if (created)
		*created = 0;
	dp = root_dir();
	if (dp == 0)
		return 0;
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
		ivalid(ip);
		if (type == T_FILE && ip->type == T_FILE &&
		    vfs_inode_create_matches(ip, cred, policy))
			return ip;
		iput(ip);
		return 0;
	}
	if (lookup_status != FS_LOOKUP_ABSENT) {
		iput(dp);
		return 0;
	}
	if (fs_storage_charge_from_vfs(cred, &charge) < 0) {
		iput(dp);
		return 0;
	}
	ip = ialloc(dp->dev, type, &charge);
	if (ip == 0) {
		iput(dp);
		return 0;
	}
	ivalid(ip);
	if (ip->type != type || vfs_inode_init_label(ip, cred, policy) < 0) {
		iabort(ip);
		iput(dp);
		return 0;
	}
	iupdate(ip);
	if (dirlink(dp, path, ip->inum, cred) < 0) {
		iabort(ip);
		iput(dp);
		return 0;
	}
	iput(dp);
	if (created)
		*created = 1;
	return ip;
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
	struct inode *dp = root_dir();
	if (dp == 0)
		return 0;
	ip = dirlookup(dp, path + skip, 0, policy, scope_id, status);
	iput(dp);
	return ip;
}

struct inode *namei_scope(char *path, uint policy, uint scope_id)
{
	return namei_scope_status(path, policy, scope_id, 0);
}
