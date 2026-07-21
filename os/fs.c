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
	uint public_principal_id;
	uint public_blocks;
	uint public_inodes;
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
static struct wait_queue fs_claim_waiters;
static struct thread *fs_claim_owner;

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

static void fs_storage_rebuild(int dev, int enforce_policy)
{
	uint max_scope_id = VFS_SCOPE_FIRST_DYNAMIC - 1;
	uint total_inodes = sb.ninodes - 1;
	struct buf *bitmap = 0;
	uint bitmap_block = ~0U;

	memset(&fs_storage, 0, sizeof(fs_storage));
	fs_storage.public_principal_id = sb.public_principal_id;
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
		} else if (owner == fs_storage.public_principal_id) {
			if (fs_storage.public_blocks == (uint)-1)
				panic("public block accounting overflow");
			fs_storage.public_blocks++;
		} else if (owner != FS_OWNER_SYSTEM) {
			panic("allocated block has invalid public owner");
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
			} else if (owner == fs_storage.public_principal_id) {
				if (fs_storage.public_inodes == (uint)-1)
					panic("public inode accounting overflow");
				fs_storage.public_inodes++;
			} else if (owner != FS_OWNER_SYSTEM) {
				panic("inode has invalid public owner");
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

	uint public_blocks = sb.nblocks - fs_storage.system_block_reserve -
			     fs_storage.workflow_block_reserve;
	uint public_inodes = total_inodes - fs_storage.system_inode_reserve -
			     fs_storage.workflow_inode_reserve;
	fs_storage.block_domain_limit = FS_DOMAIN_BLOCK_LIMIT ?
		FS_DOMAIN_BLOCK_LIMIT : fs_div_round_up(public_blocks, 4);
	fs_storage.inode_domain_limit = FS_DOMAIN_INODE_LIMIT ?
		FS_DOMAIN_INODE_LIMIT : fs_div_round_up(public_inodes, 4);
	if (fs_storage.block_domain_limit == 0)
		fs_storage.block_domain_limit = 1;
	if (fs_storage.inode_domain_limit == 0)
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
	fs_storage.ready = 1;
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
	uint limit;
	uint *public_used;
	uint *system_remaining;
	uint scope_id = VFS_SCOPE_NONE;
	int enabled;

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
	if (charge->level == FS_CHARGE_WORKFLOW)
		limit = inode ? fs_storage.workflow_inode_domain_limit :
				fs_storage.workflow_block_domain_limit;
	else
		limit = inode ? fs_storage.inode_domain_limit :
				fs_storage.block_domain_limit;
	public_used = inode ? &fs_storage.public_inodes :
			      &fs_storage.public_blocks;
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
	if (charge->level == FS_CHARGE_WORKFLOW) {
		if (vfs_scope_storage_reserve(scope_id, inode, limit) < 0) {
			intr_restore(enabled);
			return -1;
		}
	} else if (charge->level == FS_CHARGE_PUBLIC) {
		if (*public_used >= limit) {
			intr_restore(enabled);
			return -1;
		}
		(*public_used)++;
	}
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
	uint *public_used = inode ? &fs_storage.public_inodes :
					  &fs_storage.public_blocks;
	int enabled;

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
		// A previous boot's workflow ledger is intentionally absent while
		// its persisted objects are reaped. Live scopes still release their
		// transient usage here; the on-disk owner remains the source of truth.
		vfs_scope_storage_release(FS_OWNER_SCOPE_ID(owner), inode);
	} else if (owner == fs_storage.public_principal_id) {
		if (*public_used == 0)
			panic("public storage release invariant");
		(*public_used)--;
	} else if (owner != FS_OWNER_SYSTEM) {
		panic("storage principal release invariant");
	}
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

	if (!fs_storage.ready)
		return -1;
	enabled = intr_save();
	if (fs_storage.public_blocks > fs_storage.block_domain_limit ||
	    fs_storage.public_inodes > fs_storage.inode_domain_limit ||
	    blocks > fs_storage.block_domain_limit - fs_storage.public_blocks ||
	    inodes > fs_storage.inode_domain_limit - fs_storage.public_inodes) {
		intr_restore(enabled);
		return -1;
	}
	fs_storage.public_blocks += blocks;
	fs_storage.public_inodes += inodes;
	intr_restore(enabled);
	return 0;
}

static uint fs_claim_block_owner(int dev, uint block, int transfer,
				 int *public_seen)
{
	uint owner;

	if (block < sb.datastart || block >= sb.size)
		panic("storage claim block range");
	owner = fs_qmap_read(dev, block);
	if (owner == FS_OWNER_PUBLIC) {
		if (public_seen)
			*public_seen = 1;
		return 0;
	}
	if (owner != FS_OWNER_SYSTEM)
		panic("storage claim block owner");
	if (transfer) {
		fs_qmap_write(dev, block, FS_OWNER_PUBLIC);
		if (public_seen)
			*public_seen = 1;
	}
	return 1;
}

// Return the number of SYSTEM-owned blocks that still need conversion.  The
// indirect map block is storage too and is charged together with its entries.
static uint fs_claim_inode_blocks(int dev, const uint addrs[NDIRECT + 1],
				  int transfer, int *public_seen)
{
	uint count = 0;
	struct buf *bp;
	uint *entries;

	for (uint i = 0; i < NDIRECT; i++)
		if (addrs[i] != 0)
			count += fs_claim_block_owner(dev, addrs[i], transfer,
						      public_seen);
	if (addrs[NDIRECT] == 0)
		return count;
	count += fs_claim_block_owner(dev, addrs[NDIRECT], transfer,
				      public_seen);
	bp = bread(dev, addrs[NDIRECT]);
	entries = (uint *)bp->data;
	for (uint i = 0; i < NINDIRECT; i++)
		if (entries[i] != 0)
			count += fs_claim_block_owner(dev, entries[i], transfer,
						      public_seen);
	brelse(bp);
	return count;
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
		int public_seen = 0;

		if (dip->type != T_FILE ||
		    (dip->fs_owner_domain != FS_OWNER_SYSTEM &&
		     dip->fs_owner_domain != FS_OWNER_PUBLIC)) {
			brelse(bp);
			continue;
		}
		system_blocks = fs_claim_inode_blocks(dev, dip->addrs, 0,
						      &public_seen);
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
		if (fs_claim_inode_blocks(dev, dip->addrs, 1, 0) != system_blocks)
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
	missing_blocks = fs_claim_inode_blocks(ip->dev, ip->addrs, 0, 0);
	if (fs_storage_claim_public_existing(missing_blocks, 1) < 0)
		goto out;
	if (fs_claim_inode_blocks(ip->dev, ip->addrs, 1, 0) != missing_blocks)
		panic("PUBLIC claim changed beneath scanner");
	ip->fs_owner_domain = FS_OWNER_PUBLIC;
	ip->vfs_checksum = vfs_label_checksum(
		ip->inum, ip->vfs_magic, ip->vfs_version, ip->vfs_flags,
		ip->vfs_scope_id, ip->vfs_policy, ip->vfs_exec_profile,
		ip->vfs_policy_generation, ip->vfs_incarnation,
		ip->fs_owner_domain, ip->fs_owner_version);
	iupdate(ip);
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
	memset(bp->data, 0, BSIZE);
	bwrite(bp);
	brelse(bp);
}

// Blocks.

// Allocate a zeroed disk block.
static uint balloc(uint dev, const struct fs_storage_charge *charge)
{
	int b, bi, m;
	struct buf *bp;

	if (fs_storage_reserve(charge, 0) < 0)
		return 0;
	bp = 0;
	for (b = sb.datastart - sb.datastart % BPB;
	     b < sb.size; b += BPB) {
		bp = bread(dev, BBLOCK(b, sb));
		for (bi = 0; bi < BPB && b + bi < sb.size; bi++) {
			if (b + bi < sb.datastart)
				continue;
			m = 1 << (bi % 8);
			if ((bp->data[bi / 8] & m) == 0) { // Is block free?
				fs_qmap_write(dev, b + bi, charge->owner);
				bp->data[bi / 8] |= m; // Mark block in use.
				bwrite(bp);
				brelse(bp);
				bzero(dev, b + bi);
				return b + bi;
			}
		}
		brelse(bp);
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

// Drop a reference to an in-memory inode.
// If that was the last reference, the inode table entry can
// be recycled.
// If that was the last reference and the inode has no links
// to it, free the inode (and its content) on disk.
// All calls to iput() must be inside a transaction in
// case it has to free the inode.
void iput(struct inode *ip)
{
	if (ip->ref == 1 && ip->valid && ip->removed) {
		struct vfs_cred kernel_cred;
		uint storage_owner = ip->fs_owner_domain;

		vfs_cred_kernel(&kernel_cred);
		if (itrunc(ip, &kernel_cred) < 0) {
			ip->removed = 0;
			ip->ref--;
			return;
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
	uint addr, *a;
	struct buf *bp;
	int indirect_allocated = 0;

	if (bn < NDIRECT) {
		if ((addr = ip->addrs[bn]) == 0 && alloc)
			ip->addrs[bn] = addr = balloc(ip->dev, charge);
		return addr;
	}
	bn -= NDIRECT;

	if (bn < NINDIRECT) {
		if ((addr = ip->addrs[NDIRECT]) == 0) {
			if (!alloc)
				return 0;
			ip->addrs[NDIRECT] = addr = balloc(ip->dev, charge);
			if (addr == 0)
				return 0;
			indirect_allocated = 1;
		}
		bp = bread(ip->dev, addr);
		a = (uint *)bp->data;
		if ((addr = a[bn]) == 0 && alloc) {
			a[bn] = addr = balloc(ip->dev, charge);
			if (addr != 0)
				bwrite(bp);
		}
		brelse(bp);
		if (addr == 0 && indirect_allocated) {
			bfree(ip->dev, ip->addrs[NDIRECT]);
			ip->addrs[NDIRECT] = 0;
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

#define TRUNCATE_INDIRECT_BATCH 16U

// Once the inode no longer points at this indirect block, its entries can be
// reclaimed in small batches without carrying a buffer across a safe point.
static void truncate_free_indirect(int dev, uint indirect)
{
	uint blocks[TRUNCATE_INDIRECT_BATCH];
	struct buf *bp;
	uint count;
	uint *entries;

	if (indirect == 0)
		return;
	for (uint base = 0; base < NINDIRECT; base += count) {
		count = MIN(TRUNCATE_INDIRECT_BATCH, NINDIRECT - base);
		bp = bread(dev, indirect);
		entries = (uint *)bp->data;
		memmove(blocks, entries + base, count * sizeof(*blocks));
		brelse(bp);
		for (uint i = 0; i < count; i++)
			truncate_free_block(dev, blocks[i]);
		(void)kernel_work_checkpoint_cleanup(
			KERNEL_WORK_OPERATION_UNITS);
	}
	truncate_free_block(dev, indirect);
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

void itruncate_reclaim(struct inode_reclaim *reclaim)
{
	if (reclaim == 0 || reclaim->mode == INODE_RECLAIM_NONE)
		return;
	if (reclaim->mode == INODE_RECLAIM_DIRECT) {
		for (uint i = 0; i < NDIRECT; i++)
			truncate_free_block(reclaim->dev, reclaim->direct[i]);
		truncate_free_indirect(reclaim->dev, reclaim->indirect);
	} else if (reclaim->mode == INODE_RECLAIM_LIST) {
		for (uint i = 0; i < reclaim->block_count; i++)
			truncate_free_block(reclaim->dev,
					    reclaim->block_list[i]);
		truncate_free_block(reclaim->dev, reclaim->indirect);
		kfree((char *)reclaim->block_list);
	} else {
		panic("invalid inode reclaim mode");
	}
	memset(reclaim, 0, sizeof(*reclaim));
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
int readi(struct inode *ip, const struct vfs_cred *cred, int user_dst,
	  uint64 dst, uint off, uint n)
{
	uint tot, m, addr;
	struct buf *bp;
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
	}
	if (failed && tot == 0)
		return -1;
	return tot;
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
	int failed = 0;

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
	}

	if (off > ip->size)
		ip->size = off;

	// write the i-node back to disk even if the size didn't change
	// because the loop above might have called bmap() and added a new
	// block to ip->addrs[].
	iupdate(ip);

	if (failed && tot == 0)
		return -1;
	return tot;
}

int writei(struct inode *ip, const struct vfs_cred *cred, int user_src,
	   uint64 src, uint off, uint n)
{
	struct fs_storage_charge charge;

	if (fs_storage_charge_from_vfs(cred, &charge) < 0)
		return -1;
	return writei_charged(ip, cred, &charge, user_src, src, off, n);
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
	// Names of immutable SYSTEM images are reserved across every workflow
	// namespace so a mutable artifact cannot shadow a trusted executable.
	if (target_policy == VFS_POLICY_WORKFLOW &&
	    target_scope_id >= VFS_SCOPE_FIRST_DYNAMIC) {
		ip = dirlookup(dp, name, 0, VFS_POLICY_WORKFLOW,
			       VFS_SCOPE_SYSTEM, &lookup_status);
		if (ip != 0) {
			iput(ip);
			return -1;
		}
		if (lookup_status != FS_LOOKUP_ABSENT)
			return -1;
	}
	// Check that name is not present.
	ip = dirlookup(dp, name, 0, target_policy, target_scope_id,
		       &lookup_status);
	if (ip != 0) {
		iput(ip);
		return -1;
	}
	if (lookup_status != FS_LOOKUP_ABSENT)
		return -1;

	// Look for an empty dirent.
	for (off = 0; off < dp->size; off += sizeof(de)) {
		if (readi(dp, &kernel_cred, 0, (uint64)&de, off,
			  sizeof(de)) != sizeof(de))
			return -1;
		if (de.inum == 0)
			break;
	}
	if (off >= dp->size)
		return -1;
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

// Remove every mutable object owned by a retired workflow namespace.  Scope
// identifiers are never reused, so clearing the directory entries makes the
// namespace teardown final even if an inode remains pinned briefly in memory.
int fs_reclaim_scope_files(uint scope_id)
{
	struct fs_storage_charge system_charge = {
		.owner = FS_OWNER_SYSTEM,
		.level = FS_CHARGE_SYSTEM,
	};
	struct vfs_cred kernel_cred;
	struct inode *dp;
	struct inode *target;
	struct dirent de;
	uint64 off;
	int reclaimed = 0;
	int failed = 0;

	if (scope_id < VFS_SCOPE_FIRST_DYNAMIC ||
	    !vfs_scope_retiring(scope_id))
		return -1;
	dp = root_dir();
	if (dp == 0)
		return -1;
	vfs_cred_kernel(&kernel_cred);
	for (off = 0; off < dp->size; off += sizeof(de)) {
		if (readi(dp, &kernel_cred, 0, (uint64)&de, off,
			  sizeof(de)) != sizeof(de)) {
			failed = 1;
			break;
		}
		if (de.inum == 0)
			continue;
		target = inode_get(dp->dev, de.inum);
		if (target == 0) {
			failed = 1;
			continue;
		}
		ivalid(target);
		if (!vfs_inode_label_valid(target) ||
		    target->vfs_policy != VFS_POLICY_WORKFLOW ||
		    target->vfs_scope_id != scope_id) {
			iput(target);
			continue;
		}
		if (target->type != T_FILE ||
		    !exec_policy_inode_mutable(target)) {
			iput(target);
			failed = 1;
			continue;
		}
		memset(&de, 0, sizeof(de));
		if (writei_charged(dp, &kernel_cred, &system_charge, 0,
				   (uint64)&de, off, sizeof(de)) != sizeof(de)) {
			iput(target);
			failed = 1;
			continue;
		}
		target->removed = 1;
		iput(target);
		reclaimed++;
	}
	iput(dp);
	return failed ? -1 : reclaimed;
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
