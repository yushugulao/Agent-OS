#ifndef __FS_H__
#define __FS_H__

#include "types.h"
#include "resource_controller.h"

#if defined(__GNUC__)
#define FS_MUST_CHECK __attribute__((warn_unused_result))
#else
#define FS_MUST_CHECK
#endif
// On-disk file system format.
// Both the kernel and user programs use this header file.

#define NINODE 2048 // maximum number of active i-nodes
#ifndef FS_ICACHE_SIZE
#define FS_ICACHE_SIZE NINODE
#endif
#define NDEV 10 // maximum major device number
#define ROOTDEV 1 // device number of file system root disk
#define MAXOPBLOCKS 10 // max # of blocks any FS op writes
#define NBUF 256 // size of the partitioned disk block cache
#ifndef FSSIZE
#define FSSIZE 16384 // size of file system in blocks
#endif
#include "../fs_storage_policy.h"
#define MAXPATH 128 // maximum file path name

#define FS_LOOKUP_ERROR  (-1)
#define FS_LOOKUP_ABSENT 0
#define FS_LOOKUP_FOUND  1
/* Preserved device scheduling failure; callers must not treat it as absent. */
#define FS_LOOKUP_BUSY   (-5)
/* Namespace publication may have completed; callers must fail closed. */
#define FS_LOOKUP_INDETERMINATE (-7)
#define FS_CREATE_INDETERMINATE FS_LOOKUP_INDETERMINATE

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
	uint qmapstart; // Block number of first storage-owner map block
	uint datastart; // Block number of first allocatable data block
	uint storage_policy_version;
	uint storage_scope_slots;
	uint workflow_block_guarantee;
	uint workflow_inode_guarantee;
	uint system_block_reserve;
	uint system_inode_reserve;
	uint public_principal_id;
	uint storage_policy_checksum;
};

_Static_assert(sizeof(struct superblock) == 64,
	       "on-disk superblock format must remain 64 bytes");

#define FSMAGIC 0x10203047

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
#define VFS_LABEL_VERSION 3U
#define VFS_LABEL_F_PUBLIC         0x1U
#define VFS_LABEL_F_PROTECTED      0x2U
#define VFS_LABEL_F_KERNEL_PRIVATE 0x4U
#define VFS_LABEL_F_ROOT           0x8U
#define VFS_LABEL_F_FREE           0x10U
#define VFS_LABEL_F_KNOWN \
	(VFS_LABEL_F_PUBLIC | VFS_LABEL_F_PROTECTED | \
	 VFS_LABEL_F_KERNEL_PRIVATE | VFS_LABEL_F_ROOT | VFS_LABEL_F_FREE)
#define VFS_SCOPE_NONE          0U
#define VFS_SCOPE_SYSTEM        1U
#define VFS_SCOPE_FIRST_DYNAMIC FS_WORKFLOW_SCOPE_FIRST_ID
#define VFS_POLICY_PUBLIC 1U
#define VFS_POLICY_WORKFLOW 2U
#define VFS_POLICY_KERNEL_PRIVATE 3U
#define VFS_POLICY_ROOT 4U
#define VFS_POLICY_FREE 5U
#define VFS_POLICY_GENERATION 2U
#define VFS_EXEC_PROFILE_NONE 0U
#define VFS_EXEC_PROFILE_WORKFLOW 1U
#define VFS_EXEC_PROFILE_CONTENT_READ 2U
#define VFS_EXEC_PROFILE_ARTIFACT_WRITE 3U

#define FS_OWNER_VERSION 3U
#define FS_OWNER_NONE 0U
#define FS_OWNER_SYSTEM 1U
#define FS_OWNER_PUBLIC FS_PUBLIC_PRINCIPAL_ID
#define FS_OWNER_SCOPE_FLAG 0x80000000U
#define FS_OWNER_ID_MASK (FS_OWNER_SCOPE_FLAG - 1U)
#define FS_OWNER_SCOPE(id) (FS_OWNER_SCOPE_FLAG | (id))
#define FS_OWNER_IS_SCOPE(owner) (((owner) & FS_OWNER_SCOPE_FLAG) != 0)
#define FS_OWNER_SCOPE_ID(owner) ((owner) & FS_OWNER_ID_MASK)
/*
 * The qmap is also the block allocator's recovery log.  Stable workflow
 * owners use the 10 prefix; 01 and 11 retain the 30-bit owner payload while
 * an allocation or free is being committed.  Stable owner values are kept
 * unchanged, so existing images remain readable.
 */
#define FS_QMAP_STATE_MASK 0xc0000000U
#define FS_QMAP_OWNER_PAYLOAD_MASK 0x3fffffffU
#define FS_QMAP_ALLOCATING_FLAG 0x40000000U
#define FS_QMAP_FREEING_FLAG 0xc0000000U
#define FS_OWNER_MAX_PERSISTENT_ID FS_QMAP_OWNER_PAYLOAD_MASK
// Trusted mkfs images may sponsor immutable PUBLIC objects as SYSTEM;
// runtime PUBLIC allocations always use FS_OWNER_PUBLIC.
#define FS_OWNER_IS_PUBLIC_OBJECT(owner) \
	((owner) == FS_OWNER_SYSTEM || (owner) == FS_OWNER_PUBLIC)

_Static_assert(FS_OWNER_PUBLIC != FS_OWNER_SYSTEM &&
	       FS_OWNER_PUBLIC < FS_OWNER_SCOPE_FLAG &&
	       VFS_SCOPE_FIRST_DYNAMIC > FS_OWNER_PUBLIC &&
	       VFS_SCOPE_FIRST_DYNAMIC < FS_OWNER_SCOPE_FLAG,
	       "storage principal and workflow scope ranges must not overlap");

#define FS_CHARGE_PUBLIC 0U
#define FS_CHARGE_WORKFLOW 1U
#define FS_CHARGE_SYSTEM 2U

#ifndef FS_DOMAIN_BLOCK_LIMIT
#define FS_DOMAIN_BLOCK_LIMIT 0U
#endif
#ifndef FS_DOMAIN_INODE_LIMIT
#define FS_DOMAIN_INODE_LIMIT 0U
#endif
#ifndef FS_WORKFLOW_DOMAIN_BLOCK_LIMIT
#define FS_WORKFLOW_DOMAIN_BLOCK_LIMIT 0U
#endif
#ifndef FS_WORKFLOW_DOMAIN_INODE_LIMIT
#define FS_WORKFLOW_DOMAIN_INODE_LIMIT 0U
#endif
struct fs_storage_charge {
	uint owner;
	uint level;
};

// On-disk inode structure
struct dinode {
	short type; // File type
	short agent_meta_slot; // slot plus one; zero=unknown, -1=capacity deferred
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
	uint vfs_scope_id;
	uint vfs_policy;
	uint vfs_exec_profile;
	uint vfs_policy_generation;
	uint vfs_incarnation;
	uint fs_owner_domain;
	uint fs_owner_version;
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

// Storage-owner entries per block and owner-map block for disk block b.
#define QPB (BSIZE / sizeof(uint))
#define QBLOCK(b, sb) ((b) / QPB + sb.qmapstart)

// Directory is a file containing a sequence of dirent structures.
#define DIRSIZ 14

struct dirent {
	ushort inum;
	char name[DIRSIZ];
};

// file.h
struct inode;
struct open_file_io_token;
struct vfs_cred;

// A truncate first publishes an inode without the discarded mappings, then
// carries this private token while the old blocks are reclaimed safely.
#define INODE_RECLAIM_NONE 0U
#define INODE_RECLAIM_DIRECT 1U
#define INODE_RECLAIM_LIST 2U
#define FS_RECLAIM_PENDING (-2)
struct inode_reclaim {
	uint mode;
	int dev;
	uint storage_owner;
	struct resource_account_handle storage_account;
	uint direct[NDIRECT];
	uint indirect;
	uint *block_list;
	uint block_count;
	uint direct_cursor;
	uint indirect_cursor;
	uint block_cursor;
	struct resource_account_handle page_account;
	enum resource_charge_class page_charge_class;
	uint inode;
	uint incarnation;
	uint release_inode;
	uint deferred_slot;
	uint deferred_reserved;
};

void fsinit();
int fs_storage_scope_admissible(void);
int fs_storage_scope_account_create(
	uint, struct resource_account_handle *);
int fs_storage_owner_account(uint, struct resource_account_handle *);
void fs_storage_scope_account_close(struct resource_account_handle);
int fs_dirent_canonicalize(const char *, char [DIRSIZ + 1]);
int dirlink(struct inode *, char *, uint, const struct vfs_cred *);
int dirunlink(struct inode *, char *, uint, uint, uint,
	      const struct vfs_cred *, uint);
int fs_rollback_created_workflow(char *, uint, uint, uint, uint);
int fs_reclaim_scope_files(uint);
struct inode *dirlookup(struct inode *, char *, uint *, uint, uint, int *);
struct inode *fs_create(char *, short, int *, const struct vfs_cred *, uint,
			int *);
struct inode *ialloc(uint, short, const struct fs_storage_charge *, int *);
struct inode *inode_get(uint, uint);
int ivalid(struct inode *) FS_MUST_CHECK;
void iput(struct inode *);
int iput_drop_only(struct inode *);
int inode_remove_detach(struct inode *, struct inode_reclaim *);
int iupdate(struct inode *) FS_MUST_CHECK;
struct inode *namei_scope_status(char *, uint, uint, int *);
struct inode *root_dir_status(int *);
int readi(struct inode *, const struct vfs_cred *, int, uint64, uint, uint);
int readi_lease(struct inode *, const struct vfs_cred *,
		const struct open_file_io_token *, int, uint64, uint, uint);
int readi_device(struct inode *, const struct vfs_cred *, int, uint64, uint,
		 uint);
int writei(struct inode *, const struct vfs_cred *, int, uint64, uint, uint);
int writei_lease(struct inode *, const struct vfs_cred *,
		 const struct open_file_io_token *, int, uint64, uint, uint);
int fs_preallocate_inode(struct inode *, const struct vfs_cred *, uint);
int itruncate_detach(struct inode *, const struct vfs_cred *, uint,
			 struct inode_reclaim *);
int itruncate_reclaim(struct inode_reclaim *) FS_MUST_CHECK;
int itruncate_reclaim_step(struct inode_reclaim *, uint);
int fs_deferred_reclaim_maintain(void);
void fs_deferred_reclaim_tick(uint64 now);
/* A caller may synchronously settle only its admitted owner. */
int fs_deferred_reclaim_drain_current(void);
#endif //!__FS_H__
