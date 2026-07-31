#ifndef AGENT_RESOURCE_ABI_H
#define AGENT_RESOURCE_ABI_H

#define AGENT_RESOURCE_SNAPSHOT_VERSION 1U

#define AGENT_RESOURCE_PROCESS 0U
#define AGENT_RESOURCE_THREAD 1U
#define AGENT_RESOURCE_FILE_OBJECT 2U
#define AGENT_RESOURCE_FS_BLOCK 3U
#define AGENT_RESOURCE_FS_INODE 4U
#define AGENT_RESOURCE_BUFFER_CACHE 5U
#define AGENT_RESOURCE_AGENT_STATE_PAGE 6U
#define AGENT_RESOURCE_PHYSICAL_PAGE 7U
#define AGENT_RESOURCE_KIND_COUNT 8U
#define AGENT_RESOURCE_KIND_MASK_ALL ((1U << AGENT_RESOURCE_KIND_COUNT) - 1U)

struct agent_resource_kind_snapshot {
	unsigned long long capacity;
	unsigned long long used;
	unsigned long long pending;
	unsigned long long ordinary_used;
	unsigned long long ordinary_pending;
	unsigned long long reserved_used;
	unsigned long long reserved_pending;
};

struct agent_resource_snapshot {
	unsigned int version;
	unsigned int struct_size;
	/* Configured global kind counters only; not account or rate coverage. */
	unsigned int measured_mask;
	unsigned int kind_count;
	unsigned long long ordinary_free_pages;
	unsigned long long reserved_free_pages;
	unsigned long long stack_reserved_free_pages;
	struct agent_resource_kind_snapshot kinds[AGENT_RESOURCE_KIND_COUNT];
};

_Static_assert(sizeof(unsigned int) == 4,
	       "Agent resource ABI requires 32-bit unsigned int");
_Static_assert(sizeof(unsigned long long) == 8,
	       "Agent resource ABI requires 64-bit unsigned long long");
_Static_assert(sizeof(struct agent_resource_kind_snapshot) == 56,
	       "Agent resource kind snapshot ABI layout");
_Static_assert(__builtin_offsetof(struct agent_resource_snapshot, kinds) == 40,
	       "Agent resource snapshot array ABI offset");
_Static_assert(sizeof(struct agent_resource_snapshot) == 488,
	       "Agent resource snapshot ABI layout");

#endif
