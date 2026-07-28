#ifndef FS_ALLOCATOR_TEST_ABI_H
#define FS_ALLOCATOR_TEST_ABI_H

#define FSALLOC_TEST_ABI_VERSION 3U
#define FSALLOC_DURABILITY_BACKEND_ABI_VERSION 2U
#define FSALLOC_DURABILITY_OVERLAY_CAPACITY 640U

enum fsalloc_test_command {
	FSALLOC_TEST_ARM = 1,
	FSALLOC_TEST_SNAPSHOT = 2,
	FSALLOC_TEST_DISARM = 3,
	FSALLOC_TEST_FLUSH = 4,
};

enum fsalloc_test_operation {
	FSALLOC_OP_ALLOC = 1,
	FSALLOC_OP_FREE = 2,
	FSALLOC_OP_IALLOC = 3,
	FSALLOC_OP_IFREE = 4,
};

enum fsalloc_test_phase {
	FSALLOC_PHASE_INTENT = 1,
	FSALLOC_PHASE_BITMAP = 2,
	FSALLOC_PHASE_OWNER = 3,
	FSALLOC_PHASE_REFUND = 4,
};

enum fsalloc_test_action {
	FSALLOC_ACTION_BUSY = 1,
	FSALLOC_ACTION_EIO = 2,
	FSALLOC_ACTION_CRASH = 3,
};

struct fsalloc_test_snapshot {
	unsigned version;
	unsigned size;
	unsigned free_blocks;
	unsigned free_inodes;
	unsigned account_blocks;
	unsigned account_inodes;
	unsigned hook_hits;
	unsigned armed;
	unsigned durability_profile;
	unsigned durability_capacity;
	unsigned durability_pending_blocks;
	unsigned durability_reserved;
	unsigned long long durability_epoch;
	unsigned long long durability_cached_writes;
	unsigned long long durability_overlay_reads;
	unsigned long long durability_raw_writes;
	unsigned long long durability_last_acknowledged_sequence;
	unsigned long long durability_flush_attempts;
	unsigned long long durability_successful_flushes;
	unsigned long long durability_failed_flushes;
	unsigned long long durability_capacity_failures;
};

#endif
