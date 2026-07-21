#ifndef __FS_H__
#define __FS_H__

#include "types.h"
// On-disk file system format.
// Both the kernel and user programs use this header file.

#define NFILE 100 // open files per system
#ifndef NINODE
#define NINODE 512 // maximum number of file-system i-nodes in the image
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
	uint qmapstart; // Block number of first block-owner map block
	uint datastart; // Block number of first data block
	uint public_principal; // Stable owner for all ordinary processes
};

#define FSMAGIC 0x10203046

_Static_assert(sizeof(struct superblock) == 36,
	       "on-disk superblock format must remain 36 bytes");

#define NDIRECT 12
#define NINDIRECT (BSIZE / sizeof(uint))
#define MAXFILE (NDIRECT + NINDIRECT)

// File type
#define T_DIR 1 // Directory
#define T_FILE 2 // File

// LAB4: Keep it the same as dinode in os/fs.h after you change it
// On-disk inode structure
struct dinode {
	short type; // File type
	ushort fs_owner_version;
	uint fs_owner_domain;
	uint size; // Size of file (bytes)
	uint addrs[NDIRECT + 1]; // Data block addresses
};

#define FS_OWNER_VERSION 2U
#define FS_OWNER_FREE    0U
#define FS_OWNER_SYSTEM  1U
#define FS_OWNER_PUBLIC  2U

_Static_assert(FS_OWNER_PUBLIC != FS_OWNER_SYSTEM,
	       "PUBLIC and SYSTEM storage principals must be distinct");

_Static_assert(sizeof(struct dinode) == 64,
	       "on-disk inode format must remain 64 bytes");

// Inodes per block.
#define IPB (BSIZE / sizeof(struct dinode))

// Block containing inode i
#define IBLOCK(i, sb) ((i) / IPB + sb.inodestart)

// Bitmap bits per block
#define BPB (BSIZE * 8)

// Block of free map containing bit for block b
#define BBLOCK(b, sb) ((b) / BPB + sb.bmapstart)

// Block owners per owner-map block.
#define QPB (BSIZE / sizeof(uint))

// Block of owner map containing the owner for block b.
#define QBLOCK(b, sb) ((b) / QPB + sb.qmapstart)

// Directory is a file containing a sequence of dirent structures.
#define DIRSIZ 14

struct dirent {
	ushort inum;
	char name[DIRSIZ];
};

#endif //!__FS_H__
