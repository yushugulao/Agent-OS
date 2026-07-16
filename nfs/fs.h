#ifndef __FS_H__
#define __FS_H__

#include "types.h"
// On-disk file system format.
// Both the kernel and user programs use this header file.

#define NFILE 100 // open files per system
#ifndef NINODE
#define NINODE 512 // maximum number of active i-nodes
#endif
#define NDEV 10 // maximum major device number
#define ROOTDEV 1 // device number of file system root disk
#define MAXOPBLOCKS 10 // max # of blocks any FS op writes
#define NBUF (MAXOPBLOCKS * 3) // size of disk block cache
#ifndef FSSIZE
#define FSSIZE 8192 // size of file system in blocks
#endif
#define MAXPATH 128 // maximum file path name

#define ROOTINO 1 // root i-number
#define BSIZE 1024 // block size

// Disk layout:
// [ boot block | super block | inode blocks | free bit map | data blocks]
//
// mkfs computes the super block and builds an initial file system. The
// super block describes the disk layout:
struct superblock {
	uint magic; // Must be FSMAGIC
	uint size; // Size of file system image (blocks)
	uint nblocks; // Number of data blocks
	uint ninodes; // Number of inodes.
	uint inodestart; // Block number of first inode block
	uint bmapstart; // Block number of first free map block
};

#define FSMAGIC 0x10203041

#define NDIRECT 12
#define NINDIRECT (BSIZE / sizeof(uint))
#define MAXFILE (NDIRECT + NINDIRECT)

// File type
#define T_DIR 1 // Directory
#define T_FILE 2 // File

#define EXEC_FLAG_TRUSTED   0x1U
#define EXEC_FLAG_IMMUTABLE 0x2U
#define EXEC_FLAG_BOOTSTRAP 0x4U
#define EXEC_FLAG_KNOWN \
	(EXEC_FLAG_TRUSTED | EXEC_FLAG_IMMUTABLE | EXEC_FLAG_BOOTSTRAP)
#define EXEC_ROLE_BIT(role) (1U << (role))
#define EXEC_LAYOUT_VERSION 1U

// LAB4: Keep it the same as dinode in os/fs.h after you change it
// On-disk inode structure
struct dinode {
	short type; // File type
	short agent_meta_slot;
	short agent_meta_flags;
	short agent_meta_version;
	uint size; // Size of file (bytes)
	uint addrs[NDIRECT + 1]; // Data block addresses
	uint exec_flags;
	uint exec_generation;
	uint exec_role_mask;
	uint exec_layout_version;
	uint exec_rw_offset;
	uint exec_reserved[11];
};

_Static_assert(sizeof(struct dinode) == 128,
	       "on-disk inode format must remain 128 bytes");

// Inodes per block.
#define IPB (BSIZE / sizeof(struct dinode))

// Block containing inode i
#define IBLOCK(i, sb) ((i) / IPB + sb.inodestart)

// Bitmap bits per block
#define BPB (BSIZE * 8)

// Block of free map containing bit for block b
#define BBLOCK(b, sb) ((b) / BPB + sb.bmapstart)

// Directory is a file containing a sequence of dirent structures.
#define DIRSIZ 14

struct dirent {
	ushort inum;
	char name[DIRSIZ];
};

#endif //!__FS_H__
