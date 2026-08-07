#include "agent.h"
#include "agent_internal.h"
#include "bio.h"
#include "defs.h"
#include "exec_policy.h"
#include "fs_epoch.h"
#include "kalloc.h"
#include "loader.h"
#include "open_file_io_lease.h"
#include "performance_stats.h"
#include "proc.h"
#include "resource_controller.h"
#include "syscall_ids.h"
#include "syscall.h"
#include "timer.h"
#include "vm.h"

_Static_assert((int)AGENT_RESOURCE_PROCESS == (int)RESOURCE_PROCESS,
	       "resource snapshot process kind drift");
_Static_assert((int)AGENT_RESOURCE_THREAD == (int)RESOURCE_THREAD,
	       "resource snapshot thread kind drift");
_Static_assert((int)AGENT_RESOURCE_FILE_OBJECT == (int)RESOURCE_FILE_OBJECT,
	       "resource snapshot file kind drift");
_Static_assert((int)AGENT_RESOURCE_FS_BLOCK == (int)RESOURCE_FS_BLOCK,
	       "resource snapshot block kind drift");
_Static_assert((int)AGENT_RESOURCE_FS_INODE == (int)RESOURCE_FS_INODE,
	       "resource snapshot inode kind drift");
_Static_assert((int)AGENT_RESOURCE_BUFFER_CACHE == (int)RESOURCE_BUFFER_CACHE,
	       "resource snapshot cache kind drift");
_Static_assert((int)AGENT_RESOURCE_AGENT_STATE_PAGE ==
	       (int)RESOURCE_AGENT_STATE_PAGE,
	       "resource snapshot Agent state kind drift");
_Static_assert((int)AGENT_RESOURCE_PHYSICAL_PAGE == (int)RESOURCE_PHYSICAL_PAGE,
	       "resource snapshot physical page kind drift");
_Static_assert((int)AGENT_RESOURCE_KIND_COUNT == (int)RESOURCE_KIND_COUNT,
	       "resource snapshot kind count drift");

static int agent_resource_snapshot_authorized(const struct proc *p)
{
	return p != 0 && !p->is_agent && p->resource_domain_admin &&
	       exec_policy_process_bootstrap(p);
}

static int agent_performance_snapshot_authorized(struct proc *p)
{
	/* 全局计数器只向签名引导主体开放。 */
	return p != 0 && p->resource_domain_admin &&
	       exec_policy_process_bootstrap(p);
}

static uint64 agent_performance_workload_syscalls(const struct proc *p)
{
	return (uint64)syscall_count_read(p, SYS_openat) +
	       syscall_count_read(p, SYS_read) +
	       syscall_count_read(p, SYS_write) +
	       syscall_count_read(p, SYS_close) +
	       syscall_count_read(p, SYS_unlinkat) +
	       syscall_count_read(p, SYS_fsync) +
	       syscall_count_read(p, SYS_agent_file_meta_set) +
	       syscall_count_read(p, SYS_agent_file_query);
}

int sys_agent_resource_snapshot(uint64 addr, uint64 user_size)
{
	struct resource_policy_snapshot policies[RESOURCE_KIND_COUNT];
	struct agent_resource_snapshot snapshot;
	struct proc *p = curr_proc();
	uint64 copy_size;
	uint measured;
	int enabled;

	if (!agent_resource_snapshot_authorized(p))
		return AGENT_STATUS_DENIED;
	if (user_size < 2 * sizeof(unsigned int))
		return AGENT_STATUS_BAD_PARAM;
	copy_size = MIN(user_size, sizeof(snapshot));
	if (user_range_check(p->pagetable, addr, copy_size, PTE_W) < 0)
		return -1;
	memset(&snapshot, 0, sizeof(snapshot));
	/* 单核且记账修改均在关中断区间内，可取得一致快照。 */
	enabled = intr_save();
	measured = resource_policy_snapshot_all(policies, RESOURCE_KIND_COUNT);
	snapshot.ordinary_free_pages = kalloc_free_pages();
	snapshot.reserved_free_pages = kalloc_physical_reserved_free_pages();
	snapshot.stack_reserved_free_pages = kalloc_stack_reserved_free_pages();
	intr_restore(enabled);
	snapshot.version = AGENT_RESOURCE_SNAPSHOT_VERSION;
	snapshot.struct_size = sizeof(snapshot);
	snapshot.measured_mask = measured;
	snapshot.kind_count = AGENT_RESOURCE_KIND_COUNT;
	for (uint kind = 0; kind < RESOURCE_KIND_COUNT; kind++) {
		struct agent_resource_kind_snapshot *out = &snapshot.kinds[kind];
		const struct resource_policy_snapshot *in = &policies[kind];

		out->capacity = in->capacity;
		out->used = in->used;
		out->pending = in->pending;
		out->ordinary_used = in->ordinary_used;
		out->ordinary_pending = in->ordinary_pending;
		out->reserved_used = in->reserved_used;
		out->reserved_pending = in->reserved_pending;
	}
	if (copyout(p->pagetable, addr, (char *)&snapshot, copy_size) < 0)
		return -1;
	return AGENT_STATUS_OK;
}

int sys_agent_performance_snapshot(uint64 addr, uint64 user_size)
{
	struct agent_performance_snapshot snapshot;
	struct bio_physical_stats io;
	struct fs_epoch_stats epoch;
	struct uvm_cow_stats cow;
	struct user_image_rx_cache_stats exec_cache;
	struct kernel_performance_stats kernel;
	struct open_file_io_lease_stats file_auth;
	struct proc *p = curr_proc();
	uint64 copy_size;

	if (!agent_performance_snapshot_authorized(p))
		return AGENT_STATUS_DENIED;
	if (user_size < 2 * sizeof(unsigned int))
		return AGENT_STATUS_BAD_PARAM;
	copy_size = MIN(user_size, sizeof(snapshot));
	if (user_range_check(p->pagetable, addr, copy_size, PTE_W) < 0)
		return -1;
	memset(&snapshot, 0, sizeof(snapshot));
	memset(&io, 0, sizeof(io));
	memset(&epoch, 0, sizeof(epoch));
	memset(&cow, 0, sizeof(cow));
	memset(&exec_cache, 0, sizeof(exec_cache));
	memset(&kernel, 0, sizeof(kernel));
	memset(&file_auth, 0, sizeof(file_auth));
	bio_physical_snapshot(&io);
	fs_epoch_stats_snapshot(&epoch);
	uvm_cow_stats_snapshot(&cow);
	user_image_rx_cache_stats_snapshot(&exec_cache);
	kernel_performance_stats_snapshot(&kernel);
	open_file_io_lease_stats_snapshot(&file_auth);
	snapshot.version = AGENT_PERFORMANCE_SNAPSHOT_VERSION;
	snapshot.struct_size = sizeof(snapshot);
	snapshot.counter_scope = AGENT_PERFORMANCE_COUNTER_SCOPE_GLOBAL;
	/* 仅作排序标记；短核心区间保留原始周期精度。 */
	snapshot.sample_tick = get_cycle();
	snapshot.observer_lifecycle_id = p->workflow_lifecycle_id;
	snapshot.observer_lifecycle_generation =
		p->workflow_lifecycle_generation;
	snapshot.fs_epoch_commits = epoch.successful_commits;
	snapshot.fs_epoch_buffers_staged = epoch.staged_buffers;
	snapshot.block_physical_writes = io.successful_writes;
	snapshot.block_physical_reads = io.reads;
	snapshot.block_durable_flushes = io.successful_flushes;
	snapshot.fs_epoch_deduplicated_stages =
		epoch.deduplicated_stages;
	snapshot.cow_pages_shared = cow.cow_shared_mappings;
	snapshot.cow_pages_copied = cow.cow_fault_copies;
	snapshot.cow_fault_promotions = cow.cow_fault_promotions;
	snapshot.exec_cache_hits = exec_cache.exec_cache_hits;
	snapshot.exec_cache_misses = exec_cache.exec_cache_misses;
	snapshot.exec_cache_shared_pages =
		exec_cache.exec_cache_shared_pages;
	snapshot.exec_cache_evictions = exec_cache.exec_cache_evictions;
	snapshot.observer_workload_syscalls =
		agent_performance_workload_syscalls(p);
	snapshot.directory_block_probes = kernel.directory_block_probes;
	snapshot.directory_entries_examined =
		kernel.directory_entries_examined;
	snapshot.virtio_notifications = kernel.virtio_notifications;
	snapshot.virtio_submitted_requests = kernel.virtio_submitted_requests;
	snapshot.virtio_write_batch_calls = kernel.virtio_write_batch_calls;
	snapshot.virtio_batched_write_requests =
		kernel.virtio_batched_write_requests;
	snapshot.virtio_indirect_write_batch_calls =
		kernel.virtio_indirect_write_batch_calls;
	snapshot.virtio_read_batch_calls = kernel.virtio_read_batch_calls;
	snapshot.virtio_batched_read_requests =
		kernel.virtio_batched_read_requests;
	snapshot.overwrite_prereads_skipped =
		kernel.overwrite_prereads_skipped;
	snapshot.file_auth_full = file_auth.full_auth;
	snapshot.file_auth_lease_hits = file_auth.lease_hit;
	snapshot.file_auth_revalidations = file_auth.revalidation;
	if (copyout(p->pagetable, addr, (char *)&snapshot, copy_size) < 0)
		return -1;
	return AGENT_STATUS_OK;
}
