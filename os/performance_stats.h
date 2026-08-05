#ifndef PERFORMANCE_STATS_H
#define PERFORMANCE_STATS_H

#include "types.h"

struct kernel_performance_stats {
	uint64 directory_block_probes;
	uint64 directory_entries_examined;
	uint64 virtio_notifications;
	uint64 virtio_submitted_requests;
	uint64 virtio_write_batch_calls;
	uint64 virtio_batched_write_requests;
	uint64 virtio_indirect_write_batch_calls;
	uint64 virtio_read_batch_calls;
	uint64 virtio_batched_read_requests;
	uint64 overwrite_prereads_skipped;
};

enum kernel_performance_virtio_submission {
	KERNEL_PERFORMANCE_VIRTIO_SINGLE = 0,
	KERNEL_PERFORMANCE_VIRTIO_WRITE_BATCH = 1,
	KERNEL_PERFORMANCE_VIRTIO_READ_BATCH = 2,
};

void kernel_performance_directory_probe(uint);
void kernel_performance_overwrite_preread_skipped(uint);
void kernel_performance_virtio_notify(
	uint, enum kernel_performance_virtio_submission, int);
void kernel_performance_stats_snapshot(struct kernel_performance_stats *);

#endif
