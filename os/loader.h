#ifndef LOADER_H
#define LOADER_H

#include "const.h"
#include "file.h"
#include "proc.h"
#include "types.h"
#include "../user_stack_policy.h"

/*
 * This class is derived while the executable inode is still locked and its
 * manifest and VFS profile have both been validated.  It is evidence about
 * the image, not authority by itself; exec credential preparation still
 * intersects it with the caller's role and inheritable capabilities.
 */
enum user_image_agent_class {
	USER_IMAGE_AGENT_FORBIDDEN = 0,
	USER_IMAGE_AGENT_TRUSTED,
};

#define USER_IMAGE_RX_CACHE_STATS_VERSION 1ULL

struct user_image_rx_cache_stats {
	uint64 version;
	uint64 size;
	uint64 exec_cache_hits;
	uint64 exec_cache_misses;
	uint64 exec_cache_shared_pages;
	uint64 exec_cache_evictions;
};

struct user_image {
	pagetable_t pagetable;
	uint64 max_page;
	uint64 ustack_base;
	uint64 heap_base;
	uint64 heap_break;
	uint64 entry;
	uint64 shared_base;
	uint64 shared_pages;
	uint exec_dev;
	uint exec_inum;
	uint exec_flags;
	uint exec_generation;
	uint exec_role_mask;
	uint exec_layout_version;
	uint exec_rw_offset;
	uint vfs_exec_profile;
	uint vfs_exec_incarnation;
	enum user_image_agent_class agent_class;
};

int load_init_app();
int user_image_build(struct inode *, uint64,
		     struct resource_account_handle,
		     enum resource_charge_class, struct user_image *);
void user_image_discard(struct user_image *);
void user_image_rx_cache_stats_snapshot(
	struct user_image_rx_cache_stats *);

#define BASE_ADDRESS (0x1000)
#define USTACK_SIZE (USER_STACK_SIZE_BYTES)
#define TRAP_PAGE_SIZE (PAGE_SIZE)

_Static_assert(USTACK_SIZE == PAGE_SIZE,
	       "the user stack contract must remain exactly one page");

#define USER_IMAGE_LIMIT (AGENT_CONTEXT_BASE)

/* Keep brk results representable by the existing signed scalar ABI. */
#define USER_HEAP_LIMIT_RAW (USER_IMAGE_LIMIT - PAGE_SIZE)
#define USER_HEAP_LIMIT \
	(USER_HEAP_LIMIT_RAW < 0x7ffff000ULL ? \
	 USER_HEAP_LIMIT_RAW : 0x7ffff000ULL)

#endif // LOADER_H
