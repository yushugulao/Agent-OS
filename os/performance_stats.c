#include "defs.h"
#include "performance_stats.h"

static struct kernel_performance_stats performance_stats;

void kernel_performance_directory_probe(uint entries)
{
	int enabled = intr_save();

	performance_stats.directory_block_probes++;
	performance_stats.directory_entries_examined += entries;
	intr_restore(enabled);
}

void kernel_performance_overwrite_preread_skipped(uint blocks)
{
	int enabled = intr_save();

	performance_stats.overwrite_prereads_skipped += blocks;
	intr_restore(enabled);
}

void kernel_performance_virtio_notify(
	uint requests, enum kernel_performance_virtio_submission submission,
	int indirect)
{
	int enabled = intr_save();

	performance_stats.virtio_notifications++;
	performance_stats.virtio_submitted_requests += requests;
	if (submission == KERNEL_PERFORMANCE_VIRTIO_WRITE_BATCH) {
		performance_stats.virtio_write_batch_calls++;
		performance_stats.virtio_batched_write_requests += requests;
		if (indirect)
			performance_stats.virtio_indirect_write_batch_calls++;
	} else if (submission == KERNEL_PERFORMANCE_VIRTIO_READ_BATCH) {
		performance_stats.virtio_read_batch_calls++;
		performance_stats.virtio_batched_read_requests += requests;
	}
	intr_restore(enabled);
}

void kernel_performance_stats_snapshot(struct kernel_performance_stats *out)
{
	int enabled;

	if (out == 0)
		return;
	enabled = intr_save();
	memmove(out, &performance_stats, sizeof(*out));
	intr_restore(enabled);
}
