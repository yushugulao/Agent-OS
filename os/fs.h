#ifndef __FS_H__
#define __FS_H__

#include "types.h"
// On-disk file system format.
// Both the kernel and user programs use this header file.

#define NFILE 100 // open files per system
#define NINODE 512 // maximum number of active i-nodes
#ifndef FS_ICACHE_SIZE
#define FS_ICACHE_SIZE NINODE
#endif
#define NDEV 10 // maximum major device number
#define ROOTDEV 1 // device number of file system root disk
#define MAXOPBLOCKS 10 // max # of blocks any FS op writes
#define NBUF (MAXOPBLOCKS * 3) // size of disk block cache
#ifndef FSSIZE
#define FSSIZE 16384 // size of file system in blocks
#endif
#define MAXPATH 128 // maximum file path name

#define FS_LOOKUP_ERROR  (-1)
#define FS_LOOKUP_ABSENT 0
#define FS_LOOKUP_FOUND  1

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

#define FSMAGIC 0x10203042

#define NDIRECT 12
#define NINDIRECT (BSIZE / sizeof(uint))
#define MAXFILE (NDIRECT + NINDIRECT)

// File type
#define T_DIR 1 // Directory
#define T_FILE 2 // File

#define EXEC_FLAG_TRUSTED   0x1U
#define EXEC_FLAG_IMMUTABLE 0x2U
#define EXEC_FLAG_BOOTSTRAP 0x4U
#define EXEC_FLAG_DOMAIN_SAFE 0x8U
#define EXEC_FLAG_KNOWN \
	(EXEC_FLAG_TRUSTED | EXEC_FLAG_IMMUTABLE | EXEC_FLAG_BOOTSTRAP | \
	 EXEC_FLAG_DOMAIN_SAFE)
#define EXEC_ROLE_BIT(role) (1U << (role))
#define EXEC_LAYOUT_VERSION 1U

#define VFS_LABEL_MAGIC 0x56465331U
#define VFS_LABEL_VERSION 1U
#define VFS_LABEL_F_PUBLIC         0x1U
#define VFS_LABEL_F_PROTECTED      0x2U
#define VFS_LABEL_F_KERNEL_PRIVATE 0x4U
#define VFS_LABEL_F_ROOT           0x8U
#define VFS_LABEL_F_FREE           0x10U
#define VFS_LABEL_F_KNOWN \
	(VFS_LABEL_F_PUBLIC | VFS_LABEL_F_PROTECTED | \
	 VFS_LABEL_F_KERNEL_PRIVATE | VFS_LABEL_F_ROOT | VFS_LABEL_F_FREE)
#define VFS_DOMAIN_PUBLIC 0U
#define VFS_DOMAIN_WORKFLOW 1U
#define VFS_POLICY_PUBLIC 1U
#define VFS_POLICY_WORKFLOW 2U
#define VFS_POLICY_KERNEL_PRIVATE 3U
#define VFS_POLICY_ROOT 4U
#define VFS_POLICY_FREE 5U
#define VFS_POLICY_GENERATION 1U
#define VFS_EXEC_PROFILE_NONE 0U
#define VFS_EXEC_PROFILE_WORKFLOW 1U
#define VFS_EXEC_PROFILE_CONTENT_READ 2U
#define VFS_EXEC_PROFILE_ARTIFACT_WRITE 3U

// On-disk inode structure
struct dinode {
	short type; // File type
	short agent_meta_slot; // Agent metadata slot plus one; zero means none
	short agent_meta_flags;
	short agent_meta_version;
	uint size; // Size of file (bytes)
	uint addrs[NDIRECT + 1]; // Data block addresses
	uint exec_flags;
	uint exec_generation;
	uint exec_role_mask;
	uint exec_layout_version;
	uint exec_rw_offset;
	uint vfs_magic;
	uint vfs_version;
	uint vfs_flags;
	uint vfs_domain;
	uint vfs_policy;
	uint vfs_exec_profile;
	uint vfs_policy_generation;
	uint vfs_incarnation;
	uint vfs_reserved0;
	uint vfs_reserved1;
	uint vfs_checksum;
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

// file.h
struct inode;
struct vfs_cred;

void fsinit();
int dirlink(struct inode *, char *, uint, const struct vfs_cred *);
int dirunlink(struct inode *, char *, uint, uint, uint,
	      const struct vfs_cred *, uint);
struct inode *dirlookup(struct inode *, char *, uint *, uint, int *);
struct inode *fs_create(char *, short, int *, const struct vfs_cred *, uint);
struct inode *ialloc(uint, short);
struct inode *inode_get(uint, uint);
void iabort(struct inode *);
struct inode *idup(struct inode *);
void iinit();
void ivalid(struct inode *);
void iput(struct inode *);
void iunlock(struct inode *);
void iunlockput(struct inode *);
void iupdate(struct inode *);
struct inode *namei_policy(char *, uint);
struct inode *namei_policy_status(char *, uint, int *);
struct inode *root_dir();
int readi(struct inode *, const struct vfs_cred *, int, uint64, uint, uint);
int writei(struct inode *, const struct vfs_cred *, int, uint64, uint, uint);
int itrunc(struct inode *, const struct vfs_cred *);
int dirls(struct inode *, const struct vfs_cred *);
#endif //!__FS_H__
