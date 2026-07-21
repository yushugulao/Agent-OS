// File system implementation.  Five layers:
//   + Blocks: allocator for raw disk blocks.
//   + Log: crash recovery for multi-step updates.
//   + Files: inode allocator, reading, writing, metadata.
//   + Directories: inode with special contents (list of other inodes!)
//   + Names: paths like /usr/rtm/xv6/fs.c for convenient naming.
//
// This file contains the low-level file system manipulation
// routines.  The (higher-level) system call implementations
// are in sysfile.c.

#include "fs.h"
#include "bio.h"
#include "defs.h"
#include "file.h"
#include "kernel_work.h"
#include "proc.h"
#include "riscv.h"
#include "types.h"
// there should be one superblock per disk device, but we run with
// only one device
struct superblock sb;

#ifndef FS_PUBLIC_BLOCK_WATERMARK
#define FS_PUBLIC_BLOCK_WATERMARK 0U
#endif
#ifndef FS_PUBLIC_INODE_WATERMARK
#define FS_PUBLIC_INODE_WATERMARK 0U
#endif
#ifndef FS_DOMAIN_BLOCK_LIMIT
#define FS_DOMAIN_BLOCK_LIMIT 0U
#endif
#ifndef FS_DOMAIN_INODE_LIMIT
#define FS_DOMAIN_INODE_LIMIT 0U
#endif

#define FS_PUBLIC_RESERVE_DIVISOR 16U
#define FS_DOMAIN_LIMIT_DIVISOR   4U

// Mount-time reachability state lives in BSS rather than on the small kernel
// stack.  The bounds are part of the filesystem format accepted by this
// kernel; the current 8192-block/512-inode image is comfortably below them.
#define FS_SCRUB_MAX_BLOCKS 65536U
#define FS_SCRUB_MAX_INODES 65536U
#define FS_SCRUB_BITMAP_BYTES(limit) (((limit) + 7U) / 8U)

static uint fs_free_blocks;
static uint fs_free_inodes;
static uint fs_public_blocks;
static uint fs_public_inodes;
static uint fs_public_block_watermark;
static uint fs_public_inode_watermark;
static uint fs_public_block_limit;
static uint fs_public_inode_limit;
static int fs_resource_ready;
static struct wait_queue fs_claim_waiters;
static struct thread *fs_claim_owner;
static uchar fs_scrub_all_blocks[FS_SCRUB_BITMAP_BYTES(FS_SCRUB_MAX_BLOCKS)];
static uchar
	fs_scrub_reachable_blocks[FS_SCRUB_BITMAP_BYTES(FS_SCRUB_MAX_BLOCKS)];
static uchar
	fs_scrub_reachable_inodes[FS_SCRUB_BITMAP_BYTES(FS_SCRUB_MAX_INODES)];

// Read the super block.
static void readsb(int dev, struct superblock *sb)
{
	struct buf *bp;
	bp = bread(dev, 1);
	memmove(sb, bp->data, sizeof(*sb));
	brelse(bp);
}

static int fs_owner_valid(uint owner)
{
	return owner == FS_OWNER_SYSTEM || owner == FS_OWNER_PUBLIC;
}

static uint qmap_get(int dev, uint block)
{
	struct buf *bp = bread(dev, QBLOCK(block, sb));
	uint owner = ((uint *)bp->data)[block % QPB];

	brelse(bp);
	return owner;
}

static void qmap_set(int dev, uint block, uint owner)
{
	struct buf *bp = bread(dev, QBLOCK(block, sb));

	((uint *)bp->data)[block % QPB] = owner;
	bwrite(bp);
	brelse(bp);
}

static int bitmap_used(int dev, uint block)
{
	struct buf *bp = bread(dev, BBLOCK(block, sb));
	int used = (bp->data[(block % BPB) / 8] >> (block % 8)) & 1;

	brelse(bp);
	return used;
}

static uint fs_div_round_up(uint value, uint divisor)
{
	return value / divisor + (value % divisor != 0);
}

static uint fs_runtime_value(uint configured, uint base, uint divisor)
{
	uint value;

	if (configured != 0)
		value = configured;
	else
		value = base / divisor;
	if (value == 0 && base != 0)
		value = 1;
	if (value > base)
		value = base;
	return value;
}

static void fs_validate_layout(void)
{
	uint inode_blocks;
	uint bitmap_blocks;
	uint qmap_blocks;
	uint inode_end;
	uint bitmap_end;
	uint qmap_end;

	if (sb.size < 2 || sb.ninodes < 2 || sb.inodestart != 2 ||
	    sb.public_principal != FS_OWNER_PUBLIC)
		panic("invalid file system layout");
	inode_blocks = fs_div_round_up(sb.ninodes, IPB);
	bitmap_blocks = fs_div_round_up(sb.size, BPB);
	qmap_blocks = fs_div_round_up(sb.size, QPB);
	if (inode_blocks > sb.size - sb.inodestart)
		panic("invalid file system layout");
	inode_end = sb.inodestart + inode_blocks;
	if (bitmap_blocks > sb.size - inode_end)
		panic("invalid file system layout");
	bitmap_end = inode_end + bitmap_blocks;
	if (qmap_blocks > sb.size - bitmap_end)
		panic("invalid file system layout");
	qmap_end = bitmap_end + qmap_blocks;
	if (sb.bmapstart != inode_end || sb.qmapstart != bitmap_end ||
	    sb.datastart != qmap_end || sb.datastart >= sb.size ||
	    sb.nblocks != sb.size - sb.datastart)
		panic("invalid file system layout");
}

static int fs_scrub_bit_test(const uchar *bits, uint index)
{
	return (bits[index / 8] >> (index % 8)) & 1;
}

static void fs_scrub_bit_set(uchar *bits, uint index)
{
	bits[index / 8] |= 1U << (index % 8);
}

static void fs_scrub_read_dinode(int dev, uint inum, struct dinode *out)
{
	struct buf *bp = bread(dev, IBLOCK(inum, sb));
	struct dinode *dip = (struct dinode *)bp->data + inum % IPB;

	*out = *dip;
	brelse(bp);
}

static void fs_scrub_validate_dinode(const struct dinode *dip)
{
	if ((dip->type != T_DIR && dip->type != T_FILE) ||
	    dip->fs_owner_version != FS_OWNER_VERSION ||
	    !fs_owner_valid(dip->fs_owner_domain) ||
	    dip->size > MAXFILE * BSIZE)
		panic("invalid allocated inode");
}

static void fs_scrub_mark_allocated_block(int dev, uint block)
{
	uint owner;

	if (block < sb.datastart || block >= sb.size)
		panic("inode block out of range");
	if (fs_scrub_bit_test(fs_scrub_all_blocks, block))
		panic("duplicate inode block");
	if (!bitmap_used(dev, block))
		panic("inode references free block");
	owner = qmap_get(dev, block);
	if (!fs_owner_valid(owner))
		panic("invalid referenced block owner");
	fs_scrub_bit_set(fs_scrub_all_blocks, block);
}

static void fs_scrub_scan_inode_blocks(int dev, const struct dinode *dip)
{
	struct buf *bp;
	uint *entries;
	uint data_blocks = fs_div_round_up(dip->size, BSIZE);

	for (uint i = 0; i < NDIRECT; i++) {
		if (i < data_blocks && dip->addrs[i] == 0)
			panic("inode data hole");
		if (dip->addrs[i] != 0)
			fs_scrub_mark_allocated_block(dev, dip->addrs[i]);
	}
	if (data_blocks > NDIRECT && dip->addrs[NDIRECT] == 0)
		panic("inode data hole");
	if (dip->addrs[NDIRECT] == 0)
		return;
	fs_scrub_mark_allocated_block(dev, dip->addrs[NDIRECT]);
	bp = bread(dev, dip->addrs[NDIRECT]);
	entries = (uint *)bp->data;
	for (uint i = 0; i < NINDIRECT; i++) {
		if (i + NDIRECT < data_blocks && entries[i] == 0) {
			brelse(bp);
			panic("inode data hole");
		}
		if (entries[i] != 0)
			fs_scrub_mark_allocated_block(dev, entries[i]);
	}
	brelse(bp);
}

static uint fs_scrub_inode_data_block(int dev, const struct dinode *dip,
				      uint logical_block)
{
	struct buf *bp;
	uint block;

	if (logical_block < NDIRECT)
		return dip->addrs[logical_block];
	logical_block -= NDIRECT;
	if (logical_block >= NINDIRECT || dip->addrs[NDIRECT] == 0)
		return 0;
	bp = bread(dev, dip->addrs[NDIRECT]);
	block = ((uint *)bp->data)[logical_block];
	brelse(bp);
	return block;
}

static void fs_scrub_find_reachable_inodes(int dev)
{
	struct dinode root;
	uint offset;

	fs_scrub_read_dinode(dev, ROOTINO, &root);
	if (root.type != T_DIR || root.size % sizeof(struct dirent) != 0)
		panic("invalid root directory");
	fs_scrub_bit_set(fs_scrub_reachable_inodes, ROOTINO);
	for (offset = 0; offset < root.size;) {
		struct buf *bp;
		uint block = fs_scrub_inode_data_block(dev, &root,
						       offset / BSIZE);
		uint block_bytes = root.size - offset;

		if (block == 0)
			panic("root directory hole");
		if (block_bytes > BSIZE)
			block_bytes = BSIZE;
		bp = bread(dev, block);
		for (uint local = 0; local < block_bytes;
		     local += sizeof(struct dirent)) {
			struct dirent de;
			struct dinode target;

			memmove(&de, bp->data + local, sizeof(de));
			if (de.inum == 0)
				continue;
			if (de.inum >= sb.ninodes) {
				brelse(bp);
				panic("dangling root directory entry");
			}
			fs_scrub_read_dinode(dev, de.inum, &target);
			if (target.type == 0) {
				brelse(bp);
				panic("dangling root directory entry");
			}
			if (target.type != T_FILE ||
			    fs_scrub_bit_test(fs_scrub_reachable_inodes,
					      de.inum)) {
				brelse(bp);
				panic("non-flat root directory");
			}
			fs_scrub_bit_set(fs_scrub_reachable_inodes, de.inum);
		}
		brelse(bp);
		offset += block_bytes;
	}
}

static void fs_scrub_mark_reachable_blocks(int dev)
{
	for (uint inum = 1; inum < sb.ninodes; inum++) {
		struct dinode dip;
		struct buf *bp;
		uint *entries;

		if (!fs_scrub_bit_test(fs_scrub_reachable_inodes, inum))
			continue;
		fs_scrub_read_dinode(dev, inum, &dip);
		for (uint i = 0; i < NDIRECT; i++)
			if (dip.addrs[i] != 0)
				fs_scrub_bit_set(fs_scrub_reachable_blocks,
						 dip.addrs[i]);
		if (dip.addrs[NDIRECT] == 0)
			continue;
		fs_scrub_bit_set(fs_scrub_reachable_blocks,
				 dip.addrs[NDIRECT]);
		bp = bread(dev, dip.addrs[NDIRECT]);
		entries = (uint *)bp->data;
		for (uint i = 0; i < NINDIRECT; i++)
			if (entries[i] != 0)
				fs_scrub_bit_set(fs_scrub_reachable_blocks,
						 entries[i]);
		brelse(bp);
	}
}

static void fs_scrub_clear_orphan_inodes(int dev)
{
	for (uint inum = 1; inum < sb.ninodes; inum++) {
		struct buf *bp = bread(dev, IBLOCK(inum, sb));
		struct dinode *dip = (struct dinode *)bp->data + inum % IPB;

		if (dip->type != 0 &&
		    !fs_scrub_bit_test(fs_scrub_reachable_inodes, inum)) {
			memset(dip, 0, sizeof(*dip));
			bwrite(bp);
		}
		brelse(bp);
	}
}

static void fs_scrub_clear_block_bitmap(int dev, uint block)
{
	struct buf *bp = bread(dev, BBLOCK(block, sb));
	uint bit = block % BPB;

	if ((bp->data[bit / 8] & (1U << (bit % 8))) == 0) {
		brelse(bp);
		panic("scrub clearing free block");
	}
	bp->data[bit / 8] &= ~(1U << (bit % 8));
	bwrite(bp);
	brelse(bp);
}

static void fs_scrub_clear_unreachable_blocks(int dev)
{
	for (uint block = sb.datastart; block < sb.size; block++) {
		int used = bitmap_used(dev, block);
		uint owner = qmap_get(dev, block);

		if (fs_scrub_bit_test(fs_scrub_reachable_blocks, block)) {
			if (!used || !fs_owner_valid(owner))
				panic("invalid reachable block");
			continue;
		}
		if (used)
			fs_scrub_clear_block_bitmap(dev, block);
		if (owner != FS_OWNER_FREE)
			qmap_set(dev, block, FS_OWNER_FREE);
	}
}

// This filesystem has a single flat root directory, so on-disk reachability
// can be reconstructed completely at mount.  Clearing orphan inodes before
// their blocks makes an interrupted scrub restartable: the next mount sees
// only unreferenced allocated blocks and finishes reclaiming them.
static void fs_mount_scrub(int dev)
{
	if (sb.size > FS_SCRUB_MAX_BLOCKS ||
	    sb.ninodes > FS_SCRUB_MAX_INODES)
		panic("file system exceeds scrub capacity");
	memset(fs_scrub_all_blocks, 0, sizeof(fs_scrub_all_blocks));
	memset(fs_scrub_reachable_blocks, 0,
	       sizeof(fs_scrub_reachable_blocks));
	memset(fs_scrub_reachable_inodes, 0,
	       sizeof(fs_scrub_reachable_inodes));

	// Validate every allocated inode first, including eventual orphans, so
	// corruption cannot be hidden by reclamation.
	for (uint inum = 1; inum < sb.ninodes; inum++) {
		struct dinode dip;

		fs_scrub_read_dinode(dev, inum, &dip);
		if (dip.type == 0)
			continue;
		fs_scrub_validate_dinode(&dip);
		fs_scrub_scan_inode_blocks(dev, &dip);
	}
	fs_scrub_find_reachable_inodes(dev);
	fs_scrub_mark_reachable_blocks(dev);
	fs_scrub_clear_orphan_inodes(dev);
	fs_scrub_clear_unreachable_blocks(dev);
}

static void fs_resource_rebuild(int dev)
{
	uint inode_capacity = sb.ninodes - 1;
	uint public_block_capacity;
	uint public_inode_capacity;

	fs_free_blocks = 0;
	fs_free_inodes = 0;
	fs_public_blocks = 0;
	fs_public_inodes = 0;
	for (uint block = 0; block < sb.size; block++) {
		int used = bitmap_used(dev, block);
		uint owner = qmap_get(dev, block);

		if (block < sb.datastart) {
			if (!used || owner != FS_OWNER_SYSTEM)
				panic("invalid metadata block owner");
			continue;
		}
		if (!used) {
			fs_free_blocks++;
			if (owner != FS_OWNER_FREE)
				qmap_set(dev, block, FS_OWNER_FREE);
			continue;
		}
		if (!fs_owner_valid(owner))
			panic("invalid data block owner");
		if (owner == FS_OWNER_PUBLIC)
			fs_public_blocks++;
	}
	for (uint inum = 1; inum < sb.ninodes; inum++) {
		struct buf *bp = bread(dev, IBLOCK(inum, sb));
		struct dinode *dip = (struct dinode *)bp->data + inum % IPB;

		if (dip->type == 0) {
			if (dip->fs_owner_version != 0 ||
			    dip->fs_owner_domain != FS_OWNER_FREE) {
				brelse(bp);
				panic("invalid free inode owner");
			}
			fs_free_inodes++;
		} else {
			if (dip->fs_owner_version != FS_OWNER_VERSION ||
			    !fs_owner_valid(dip->fs_owner_domain)) {
				brelse(bp);
				panic("invalid inode owner");
			}
			if (dip->fs_owner_domain == FS_OWNER_PUBLIC)
				fs_public_inodes++;
		}
		brelse(bp);
	}

	fs_public_block_watermark = fs_runtime_value(
		FS_PUBLIC_BLOCK_WATERMARK, sb.nblocks,
		FS_PUBLIC_RESERVE_DIVISOR);
	fs_public_inode_watermark = fs_runtime_value(
		FS_PUBLIC_INODE_WATERMARK, inode_capacity,
		FS_PUBLIC_RESERVE_DIVISOR);
	public_block_capacity = sb.nblocks - fs_public_block_watermark;
	public_inode_capacity = inode_capacity - fs_public_inode_watermark;
	fs_public_block_limit = fs_runtime_value(
		FS_DOMAIN_BLOCK_LIMIT, public_block_capacity,
		FS_DOMAIN_LIMIT_DIVISOR);
	fs_public_inode_limit = fs_runtime_value(
		FS_DOMAIN_INODE_LIMIT, public_inode_capacity,
		FS_DOMAIN_LIMIT_DIVISOR);
	if (fs_public_block_limit == 0)
		fs_public_block_limit = 1;
	if (fs_public_inode_limit == 0)
		fs_public_inode_limit = 1;
	// Existing persistent objects remain charged even with no live process.
	// A mount whose durable PUBLIC usage is already over a newly lowered limit
	// stays read-only for new PUBLIC allocations until objects are released.
	fs_resource_ready = 1;
}

static int fs_resource_reserve(uint owner, uint blocks, uint inodes)
{
	int enabled;
	int allowed = 0;

	if (!fs_resource_ready || !fs_owner_valid(owner))
		return -1;
	enabled = intr_save();
	if (owner == FS_OWNER_PUBLIC &&
	    (fs_public_blocks > fs_public_block_limit ||
	     fs_public_inodes > fs_public_inode_limit ||
	     blocks > fs_public_block_limit - fs_public_blocks ||
	     inodes > fs_public_inode_limit - fs_public_inodes))
		goto out;
	if (blocks <= fs_free_blocks && inodes <= fs_free_inodes &&
	    (owner == FS_OWNER_SYSTEM ||
	     ((blocks == 0 ||
	       fs_free_blocks - blocks >= fs_public_block_watermark) &&
	      (inodes == 0 ||
	       fs_free_inodes - inodes >= fs_public_inode_watermark)))) {
		fs_free_blocks -= blocks;
		fs_free_inodes -= inodes;
		if (owner == FS_OWNER_PUBLIC) {
			fs_public_blocks += blocks;
			fs_public_inodes += inodes;
		}
		allowed = 1;
	}
out:
	intr_restore(enabled);
	return allowed ? 0 : -1;
}

static void fs_resource_release(uint owner, uint blocks, uint inodes)
{
	int enabled = intr_save();

	if (!fs_resource_ready || !fs_owner_valid(owner) ||
	    blocks > sb.nblocks - fs_free_blocks ||
	    inodes > (sb.ninodes - 1) - fs_free_inodes)
		panic("file system resource count invariant");
	if (owner == FS_OWNER_PUBLIC &&
	    (blocks > fs_public_blocks || inodes > fs_public_inodes))
		panic("public storage count invariant");
	fs_free_blocks += blocks;
	fs_free_inodes += inodes;
	if (owner == FS_OWNER_PUBLIC) {
		fs_public_blocks -= blocks;
		fs_public_inodes -= inodes;
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

static int fs_resource_claim_public_existing(uint blocks, uint inodes)
{
	int enabled;

	if (!fs_resource_ready)
		return -1;
	enabled = intr_save();
	if (fs_public_blocks > fs_public_block_limit ||
	    fs_public_inodes > fs_public_inode_limit ||
	    blocks > fs_public_block_limit - fs_public_blocks ||
	    inodes > fs_public_inode_limit - fs_public_inodes) {
		intr_restore(enabled);
		return -1;
	}
	fs_public_blocks += blocks;
	fs_public_inodes += inodes;
	intr_restore(enabled);
	return 0;
}

static uint fs_claim_block_owner(int dev, uint block, int transfer,
				 int *public_seen)
{
	uint owner;

	if (block < sb.datastart || block >= sb.size)
		panic("storage claim block range");
	owner = qmap_get(dev, block);
	if (owner == FS_OWNER_PUBLIC) {
		if (public_seen)
			*public_seen = 1;
		return 0;
	}
	if (owner != FS_OWNER_SYSTEM)
		panic("storage claim block owner");
	if (transfer) {
		qmap_set(dev, block, FS_OWNER_PUBLIC);
		if (public_seen)
			*public_seen = 1;
	}
	return 1;
}

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

// Complete any qmap-first ownership transfer interrupted by a hard reboot.
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
		if (dip->fs_owner_version != FS_OWNER_VERSION)
			panic("invalid interrupted PUBLIC claim");
		if (fs_claim_inode_blocks(dev, dip->addrs, 1, 0) != system_blocks)
			panic("PUBLIC claim recovery changed beneath scanner");
		dip->fs_owner_domain = FS_OWNER_PUBLIC;
		bwrite(bp);
		brelse(bp);
	}
}

static int fs_claim_sponsored_inode(struct inode *ip, uint mutation_owner)
{
	uint missing_blocks;
	int result = -1;

	if (ip == 0 || ip->type != T_FILE ||
	    mutation_owner != FS_OWNER_PUBLIC)
		return 0;
	if (ip->fs_owner_domain == FS_OWNER_PUBLIC)
		return 0;
	if (ip->fs_owner_domain != FS_OWNER_SYSTEM ||
	    ip->fs_owner_version != FS_OWNER_VERSION)
		return -1;
	if (fs_claim_gate_lock() < 0)
		return -1;
	if (ip->fs_owner_domain == FS_OWNER_PUBLIC) {
		result = 0;
		goto out;
	}
	if (ip->fs_owner_domain != FS_OWNER_SYSTEM ||
	    ip->fs_owner_version != FS_OWNER_VERSION)
		goto out;
	missing_blocks = fs_claim_inode_blocks(ip->dev, ip->addrs, 0, 0);
	if (fs_resource_claim_public_existing(missing_blocks, 1) < 0)
		goto out;
	if (fs_claim_inode_blocks(ip->dev, ip->addrs, 1, 0) != missing_blocks)
		panic("PUBLIC claim changed beneath scanner");
	ip->fs_owner_domain = FS_OWNER_PUBLIC;
	iupdate(ip);
	result = 0;
out:
	fs_claim_gate_unlock();
	return result;
}

// Init fs
void fsinit()
{
	int dev = ROOTDEV;
	readsb(dev, &sb);
	if (sb.magic != FSMAGIC) {
		panic("invalid file system");
	}
	fs_validate_layout();
	fs_claim_owner = 0;
	wait_queue_init(&fs_claim_waiters, WAIT_REASON_FS_CLAIM);
	fs_mount_scrub(dev);
	fs_recover_public_claims(dev);
	fs_resource_rebuild(dev);
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
static uint balloc(uint dev, uint owner)
{
	uint base, bi;
	int m;
	struct buf *bp;

	if (fs_resource_reserve(owner, 1, 0) < 0)
		return 0;
	bp = 0;
	base = sb.datastart - sb.datastart % BPB;
	for (; base < sb.size; base += BPB) {
		uint first = base < sb.datastart ? sb.datastart - base : 0;

		bp = bread(dev, BBLOCK(base, sb));
		for (bi = first; bi < BPB && base + bi < sb.size; bi++) {
			uint block = base + bi;

			m = 1 << (bi % 8);
			if ((bp->data[bi / 8] & m) == 0) {
				if (qmap_get(dev, block) != FS_OWNER_FREE) {
					brelse(bp);
					panic("free block has owner");
				}
				qmap_set(dev, block, owner);
				bp->data[bi / 8] |= m;
				bwrite(bp);
				brelse(bp);
				bzero(dev, block);
				return block;
			}
		}
		brelse(bp);
	}
	fs_resource_release(owner, 1, 0);
	return 0;
}

// Free a disk block.
static void bfree(int dev, uint b)
{
	struct buf *bp;
	int bi, m;
	uint owner;

	if (b < sb.datastart || b >= sb.size)
		panic("freeing metadata block");
	owner = qmap_get(dev, b);
	if (!fs_owner_valid(owner))
		panic("freeing unowned block");
	bp = bread(dev, BBLOCK(b, sb));
	bi = b % BPB;
	m = 1 << (bi % 8);
	if ((bp->data[bi / 8] & m) == 0)
		panic("freeing free block");
	bp->data[bi / 8] &= ~m;
	bwrite(bp);
	brelse(bp);
	qmap_set(dev, b, FS_OWNER_FREE);
	fs_resource_release(owner, 1, 0);
}

//The inode table in memory
struct {
	struct inode inode[FS_ICACHE_SIZE];
} itable;

static struct inode *iget(uint dev, uint inum);

// Allocate an inode on device dev.
// Mark it as allocated by  giving it type `type`.
// Returns an allocated and referenced inode.
struct inode *ialloc(uint dev, short type, uint owner)
{
	int inum;
	struct buf *bp;
	struct dinode *dip;
	struct inode *ip;

	if (fs_resource_reserve(owner, 0, 1) < 0)
		return 0;
	for (inum = 1; inum < sb.ninodes; inum++) {
		bp = bread(dev, IBLOCK(inum, sb));
		dip = (struct dinode *)bp->data + inum % IPB;
		if (dip->type == 0) { // a free inode
			ip = iget(dev, inum);
			if (ip == 0) {
				brelse(bp);
				fs_resource_release(owner, 0, 1);
				return 0;
			}
			memset(dip, 0, sizeof(*dip));
			dip->type = type;
			dip->fs_owner_version = FS_OWNER_VERSION;
			dip->fs_owner_domain = owner;
			bwrite(bp);
			brelse(bp);
			return ip;
		}
		brelse(bp);
	}
	fs_resource_release(owner, 0, 1);
	return 0;
}

// Copy a modified in-memory inode to disk.
// Must be called after every change to an ip->xxx field
// that lives on disk.
void iupdate(struct inode *ip)
{
	struct buf *bp;
	struct dinode *dip;

	bp = bread(ip->dev, IBLOCK(ip->inum, sb));
	dip = (struct dinode *)bp->data + ip->inum % IPB;
	dip->type = ip->type;
	dip->fs_owner_version = ip->fs_owner_version;
	dip->fs_owner_domain = ip->fs_owner_domain;
	dip->size = ip->size;
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
	ip->content_epoch = 1;
	return ip;
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
		ip->fs_owner_version = dip->fs_owner_version;
		ip->fs_owner_domain = dip->fs_owner_domain;
		ip->size = dip->size;
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
		uint owner = ip->fs_owner_domain;

		if (ip->fs_owner_version != FS_OWNER_VERSION ||
		    !fs_owner_valid(owner))
			panic("invalid removed inode owner");
		if (itrunc(ip, owner) < 0)
			panic("removed inode truncate failed");
		ip->type = 0;
		ip->fs_owner_version = 0;
		ip->fs_owner_domain = FS_OWNER_FREE;
		iupdate(ip);
		ip->valid = 0;
		ip->removed = 0;
		fs_resource_release(owner, 0, 1);
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
static uint bmap(struct inode *ip, uint bn, int alloc, uint owner)
{
	uint addr, *a;
	struct buf *bp;
	int indirect_allocated = 0;

	if (bn < NDIRECT) {
		if ((addr = ip->addrs[bn]) == 0 && alloc)
			ip->addrs[bn] = addr = balloc(ip->dev, owner);
		return addr;
	}
	bn -= NDIRECT;

	if (bn < NINDIRECT) {
		if ((addr = ip->addrs[NDIRECT]) == 0) {
			if (!alloc)
				return 0;
			ip->addrs[NDIRECT] = addr = balloc(ip->dev, owner);
			if (addr == 0)
				return 0;
			indirect_allocated = 1;
		}
		bp = bread(ip->dev, addr);
		a = (uint *)bp->data;
		if ((addr = a[bn]) == 0 && alloc) {
			a[bn] = addr = balloc(ip->dev, owner);
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

// Truncate inode (discard contents).
int itrunc(struct inode *ip, uint mutation_owner)
{
	enum { ITRUNC_BATCH = 16 };
	uint detached_direct[NDIRECT];
	uint detached_indirect;
	uint detached_batch[ITRUNC_BATCH];
	int i, j, count;
	struct buf *bp;
	uint *a;

	if (fs_claim_sponsored_inode(ip, mutation_owner) < 0)
		return -1;

	// Make truncation logically complete before the first safe point. New I/O
	// can only observe the empty mapping while the detached blocks are reclaimed.
	memmove(detached_direct, ip->addrs, sizeof(detached_direct));
	detached_indirect = ip->addrs[NDIRECT];
	memset(ip->addrs, 0, sizeof(ip->addrs));
	ip->size = 0;
	iupdate(ip);
	ip->content_epoch++;
	if (ip->content_epoch == 0)
		ip->content_epoch = 1;

	for (i = 0; i < NDIRECT; i++) {
		if (detached_direct[i] == 0)
			continue;
		bfree(ip->dev, detached_direct[i]);
		(void)kernel_work_checkpoint_cleanup(KERNEL_WORK_OPERATION_UNITS);
	}

	if (detached_indirect != 0) {
		for (i = 0; i < NINDIRECT; i += ITRUNC_BATCH) {
			count = MIN(ITRUNC_BATCH, NINDIRECT - i);
			bp = bread(ip->dev, detached_indirect);
			a = (uint *)bp->data;
			memmove(detached_batch, a + i,
				count * sizeof(detached_batch[0]));
			brelse(bp);
			for (j = 0; j < count; j++) {
				if (detached_batch[j] == 0)
					continue;
				bfree(ip->dev, detached_batch[j]);
				(void)kernel_work_checkpoint_cleanup(
					KERNEL_WORK_OPERATION_UNITS);
			}
			(void)kernel_work_checkpoint_cleanup(
				KERNEL_WORK_OPERATION_UNITS);
		}
		bfree(ip->dev, detached_indirect);
		(void)kernel_work_checkpoint_cleanup(KERNEL_WORK_OPERATION_UNITS);
	}
	return 0;
}

// Read data from inode.
// If user_dst==1, then dst is a user virtual address;
// otherwise, dst is a kernel address.
int readi(struct inode *ip, int user_dst, uint64 dst, uint off, uint n)
{
	uint tot, m, addr;
	struct buf *bp;
	int failed = 0;

	if (off > ip->size || off + n < off)
		return 0;
	if (off + n > ip->size)
		n = ip->size - off;

	for (tot = 0; tot < n; tot += m, off += m, dst += m) {
		addr = bmap(ip, off / BSIZE, 0, FS_OWNER_SYSTEM);
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
int writei(struct inode *ip, int user_src, uint64 src, uint off, uint n,
	   uint owner)
{
	uint allocation_owner = owner;
	uint tot, m, addr, bn;
	struct buf *bp;
	int allocated;
	int failed = 0;

	if (off > ip->size || off + n < off)
		return -1;
	if (off + n > MAXFILE * BSIZE)
		return -1;
	if (n != 0 && !fs_owner_valid(owner))
		return -1;
	if (n != 0 && ip->type == T_FILE) {
		if (fs_claim_sponsored_inode(ip, owner) < 0)
			return -1;
		allocation_owner = ip->fs_owner_domain;
	}

	for (tot = 0; tot < n; tot += m, off += m, src += m) {
		bn = off / BSIZE;
		addr = bmap(ip, bn, 0, allocation_owner);
		allocated = 0;
		if (addr == 0) {
			addr = bmap(ip, bn, 1, allocation_owner);
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
	if (tot != 0) {
		ip->content_epoch++;
		if (ip->content_epoch == 0)
			ip->content_epoch = 1;
	}

	if (failed && tot == 0)
		return -1;
	return tot;
}

// Look for a directory entry in a directory.
// If found, set *poff to byte offset of entry.
struct inode *dirlookup(struct inode *dp, char *name, uint *poff)
{
	uint off, inum;
	struct dirent de;

	if (dp == 0 || dp->type != T_DIR)
		return 0;

	for (off = 0; off < dp->size; off += sizeof(de)) {
		if (readi(dp, 0, (uint64)&de, off, sizeof(de)) != sizeof(de))
			return 0;
		if (de.inum == 0)
			continue;
		if (strncmp(name, de.name, DIRSIZ) == 0) {
			// entry matches path element
			if (poff)
				*poff = off;
			inum = de.inum;
			return iget(dp->dev, inum);
		}
	}

	return 0;
}

//Show the filenames of all files in the directory
int dirls(struct inode *dp)
{
	uint64 off, count;
	struct dirent de;
	char name[DIRSIZ + 1];

	if (dp == 0 || dp->type != T_DIR)
		return -1;

	count = 0;
	for (off = 0; off < dp->size; off += sizeof(de)) {
		if (readi(dp, 0, (uint64)&de, off, sizeof(de)) != sizeof(de))
			return -1;
		if (de.inum == 0)
			continue;
		memmove(name, de.name, DIRSIZ);
		name[DIRSIZ] = 0;
		printf("%s\n", name);
		count++;
	}
	return count;
}

// Write a new directory entry (name, inum) into the directory dp.
int dirlink(struct inode *dp, char *name, uint inum, uint owner)
{
	int off;
	struct dirent de;
	struct inode *ip;
	if (dp == 0 || dp->type != T_DIR)
		return -1;
	// Check that name is not present.
	if ((ip = dirlookup(dp, name, 0)) != 0) {
		iput(ip);
		return -1;
	}

	// Look for an empty dirent.
	for (off = 0; off < dp->size; off += sizeof(de)) {
		if (readi(dp, 0, (uint64)&de, off, sizeof(de)) != sizeof(de))
			return -1;
		if (de.inum == 0)
			break;
	}
	strncpy(de.name, name, DIRSIZ);
	de.inum = inum;
	if (writei(dp, 0, (uint64)&de, off, sizeof(de), owner) != sizeof(de))
		return -1;
	return 0;
}

int dirunlink(struct inode *dp, char *name, uint *inum, uint owner)
{
	uint off;
	struct dirent de;

	if (dp == 0 || dp->type != T_DIR)
		return -1;
	for (off = 0; off < dp->size; off += sizeof(de)) {
		if (readi(dp, 0, (uint64)&de, off, sizeof(de)) != sizeof(de))
			return -1;
		if (de.inum == 0)
			continue;
		if (strncmp(name, de.name, DIRSIZ) == 0) {
			if (inum)
				*inum = de.inum;
			memset(&de, 0, sizeof(de));
			if (writei(dp, 0, (uint64)&de, off, sizeof(de), owner) !=
			    sizeof(de))
				return -1;
			return 0;
		}
	}
	return -1;
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

struct inode *fs_create(char *path, short type, int *created, uint owner)
{
	struct inode *dp;
	struct inode *ip;

	if (created)
		*created = 0;
	dp = root_dir();
	if (dp == 0)
		return 0;
	if ((ip = dirlookup(dp, path, 0)) != 0) {
		iput(dp);
		ivalid(ip);
		if (type == T_FILE && ip->type == T_FILE)
			return ip;
		iput(ip);
		return 0;
	}
	ip = ialloc(dp->dev, type, owner);
	if (ip == 0) {
		iput(dp);
		return 0;
	}
	ivalid(ip);
	if (ip->type != type || dirlink(dp, path, ip->inum, owner) < 0) {
		iabort(ip);
		iput(dp);
		return 0;
	}
	iput(dp);
	if (created)
		*created = 1;
	return ip;
}

//Find the corresponding inode according to the path
struct inode *namei(char *path)
{
	int skip = 0;
	struct inode *ip;
	// if(path[0] == '.' && path[1] == '/')
	//     skip = 2;
	// if (path[0] == '/') {
	//     skip = 1;
	// }
	struct inode *dp = root_dir();
	if (dp == 0)
		return 0;
	ip = dirlookup(dp, path + skip, 0);
	iput(dp);
	return ip;
}
