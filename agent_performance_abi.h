#ifndef AGENT_PERFORMANCE_ABI_H
#define AGENT_PERFORMANCE_ABI_H

#define AGENT_PERFORMANCE_SNAPSHOT_VERSION 3U

/* 计数器覆盖整个内核；调用者用差值计算区间值。 */
#define AGENT_PERFORMANCE_COUNTER_SCOPE_GLOBAL 1U

struct agent_performance_snapshot {
	unsigned int version;
	unsigned int struct_size;
	unsigned int counter_scope;
	unsigned int reserved;
	/* 来自原始 cycle counter 的严格有序令牌，不是时间单位。 */
	unsigned long long sample_tick;
	unsigned long long observer_lifecycle_id;
	unsigned long long observer_lifecycle_generation;
	unsigned long long fs_epoch_commits;
	unsigned long long fs_epoch_buffers_staged;
	unsigned long long block_physical_writes;
	unsigned long long block_durable_flushes;
	unsigned long long fs_epoch_deduplicated_stages;
	unsigned long long cow_pages_shared;
	unsigned long long cow_pages_copied;
	unsigned long long cow_fault_promotions;
	unsigned long long exec_cache_hits;
	unsigned long long exec_cache_misses;
	unsigned long long exec_cache_shared_pages;
	unsigned long long exec_cache_evictions;
	unsigned long long observer_workload_syscalls;
	unsigned long long directory_block_probes;
	unsigned long long directory_entries_examined;
	unsigned long long virtio_notifications;
	unsigned long long virtio_submitted_requests;
	unsigned long long virtio_write_batch_calls;
	unsigned long long virtio_batched_write_requests;
	unsigned long long virtio_indirect_write_batch_calls;
	unsigned long long virtio_read_batch_calls;
	unsigned long long virtio_batched_read_requests;
	unsigned long long block_physical_reads;
	unsigned long long overwrite_prereads_skipped;
	unsigned long long file_auth_full;
	unsigned long long file_auth_lease_hits;
	unsigned long long file_auth_revalidations;
};

_Static_assert(sizeof(unsigned int) == 4,
	       "Agent performance ABI requires 32-bit unsigned int");
_Static_assert(sizeof(unsigned long long) == 8,
	       "Agent performance ABI requires 64-bit unsigned long long");
_Static_assert(sizeof(struct agent_performance_snapshot) == 256,
	       "Agent performance snapshot ABI layout");
_Static_assert(__builtin_offsetof(struct agent_performance_snapshot,
				  sample_tick) == 16,
	       "Agent performance sample tick offset");
_Static_assert(__builtin_offsetof(struct agent_performance_snapshot,
				  observer_lifecycle_id) == 24,
	       "Agent performance observer lifecycle offset");
_Static_assert(__builtin_offsetof(struct agent_performance_snapshot,
				  fs_epoch_commits) == 40,
	       "Agent performance epoch counter offset");
_Static_assert(__builtin_offsetof(struct agent_performance_snapshot,
				  exec_cache_evictions) == 128,
	       "Agent performance exec counter offset");
_Static_assert(__builtin_offsetof(struct agent_performance_snapshot,
				  observer_workload_syscalls) == 136,
	       "Agent performance observer syscall offset");
_Static_assert(__builtin_offsetof(struct agent_performance_snapshot,
				  virtio_indirect_write_batch_calls) == 192,
	       "Agent performance indirect write counter offset");
_Static_assert(__builtin_offsetof(struct agent_performance_snapshot,
				  virtio_read_batch_calls) == 200,
	       "Agent performance read batch counter offset");
_Static_assert(__builtin_offsetof(struct agent_performance_snapshot,
				  virtio_batched_read_requests) == 208,
	       "Agent performance batched read counter offset");
_Static_assert(__builtin_offsetof(struct agent_performance_snapshot,
				  block_physical_reads) == 216,
	       "Agent performance physical read counter offset");
_Static_assert(__builtin_offsetof(struct agent_performance_snapshot,
				  overwrite_prereads_skipped) == 224,
	       "Agent performance overwrite counter offset");
_Static_assert(__builtin_offsetof(struct agent_performance_snapshot,
				  file_auth_full) == 232,
	       "Agent performance authorization counter offset");
_Static_assert(__builtin_offsetof(struct agent_performance_snapshot,
				  file_auth_revalidations) == 248,
	       "Agent performance final counter offset");

#endif
