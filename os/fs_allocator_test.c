#include "defs.h"
#include "bio.h"
#include "fs_allocator_test.h"
#include "proc.h"
#include "riscv.h"
#include "virtio.h"

#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE
#ifndef FS_ALLOCATOR_TEST_INIT_NAME
#error "FS allocator test profile requires a sealed init name"
#endif
#ifndef DURABILITY_POWERCUT_TEST_PROFILE
#error "FS allocator fault evidence requires the volatile durability profile"
#endif
_Static_assert(FSALLOC_DURABILITY_OVERLAY_CAPACITY ==
	       VIRTIO_DURABILITY_OVERLAY_CAPACITY,
	       "allocator evidence ABI must match the volatile overlay");
_Static_assert(FSALLOC_DURABILITY_BACKEND_ABI_VERSION ==
	       VIRTIO_DURABILITY_TEST_ABI_VERSION,
	       "allocator evidence ABI version must match the volatile overlay");

struct fs_allocator_fault_state {
	uint operation;
	uint phase;
	uint action;
	uint hook_hits;
	uint64 raw_writes_before;
	uint64 flushes_before;
	uint64 physical_writes_before;
	uint64 physical_flushes_before;
	int armed;
	int receipt_baseline_valid;
};

static struct fs_allocator_fault_state fs_allocator_fault;
static struct proc *fs_allocator_test_controller;

static const char *fs_allocator_test_operation_name(uint operation)
{
	switch (operation) {
	case FSALLOC_OP_ALLOC:
		return "alloc";
	case FSALLOC_OP_FREE:
		return "free";
	case FSALLOC_OP_IALLOC:
		return "ialloc";
	case FSALLOC_OP_IFREE:
		return "ifree";
	default:
		return "invalid";
	}
}

static const char *fs_allocator_test_phase_name(uint phase)
{
	switch (phase) {
	case FSALLOC_PHASE_INTENT:
		return "intent";
	case FSALLOC_PHASE_BITMAP:
		return "bitmap";
	case FSALLOC_PHASE_OWNER:
		return "owner";
	case FSALLOC_PHASE_REFUND:
		return "refund";
	default:
		return "invalid";
	}
}

void fs_allocator_test_bind_boot_init(struct proc *p, const char *name)
{
	uint expected_len = strlen(FS_ALLOCATOR_TEST_INIT_NAME);
	int enabled;

	if (p == 0 || name == 0 || p->parent != 0 ||
	    strlen(name) != expected_len ||
	    strncmp(name, FS_ALLOCATOR_TEST_INIT_NAME, expected_len) != 0)
		return;
	enabled = intr_save();
	if (fs_allocator_test_controller == 0)
		fs_allocator_test_controller = p;
	intr_restore(enabled);
}

int fs_allocator_test_authorized(const struct proc *p)
{
	int enabled = intr_save();
	int authorized = p != 0 && p == fs_allocator_test_controller;

	intr_restore(enabled);
	return authorized;
}

int fs_allocator_test_arm(uint operation, uint phase, uint action, uint oneshot)
{
	struct virtio_durability_test_stats durability;
	struct bio_physical_stats physical;
	int enabled;

	if (operation < FSALLOC_OP_ALLOC || operation > FSALLOC_OP_IFREE ||
	    phase < FSALLOC_PHASE_INTENT || phase > FSALLOC_PHASE_REFUND ||
	    action < FSALLOC_ACTION_BUSY || action > FSALLOC_ACTION_CRASH ||
	    oneshot != 1)
		return -1;
	virtio_disk_durability_test_stats(&durability);
	if (durability.version != VIRTIO_DURABILITY_TEST_ABI_VERSION ||
	    durability.size != sizeof(durability) ||
	    bio_physical_snapshot(&physical) < 0 ||
	    physical.version != BIO_PHYSICAL_STATS_VERSION ||
	    physical.size != sizeof(physical))
		return -1;
	enabled = intr_save();
	if (fs_allocator_fault.armed) {
		intr_restore(enabled);
		return -1;
	}
	fs_allocator_fault.operation = operation;
	fs_allocator_fault.phase = phase;
	fs_allocator_fault.action = action;
	fs_allocator_fault.raw_writes_before = durability.raw_writes;
	fs_allocator_fault.flushes_before = durability.successful_flushes;
	fs_allocator_fault.physical_writes_before = physical.writes;
	fs_allocator_fault.physical_flushes_before = physical.flushes;
	fs_allocator_fault.receipt_baseline_valid = 1;
	fs_allocator_fault.armed = 1;
	intr_restore(enabled);
	return 0;
}

void fs_allocator_test_disarm(void)
{
	int enabled = intr_save();

	fs_allocator_fault.armed = 0;
	intr_restore(enabled);
}

static int fs_allocator_test_claim(uint operation, uint phase, uint action)
{
	int claimed = 0;
	int enabled = intr_save();

	if (fs_allocator_fault.armed &&
	    fs_allocator_fault.operation == operation &&
	    fs_allocator_fault.phase == phase &&
	    fs_allocator_fault.action == action) {
		fs_allocator_fault.armed = 0;
		fs_allocator_fault.hook_hits++;
		claimed = 1;
	}
	intr_restore(enabled);
	return claimed;
}

int fs_allocator_test_before(uint operation, uint phase)
{
	if (fs_allocator_test_claim(operation, phase, FSALLOC_ACTION_BUSY))
		return VIRTIO_DISK_ERR_BUSY;
	if (fs_allocator_test_claim(operation, phase, FSALLOC_ACTION_EIO))
		return VIRTIO_DISK_ERR_IO;
	return 0;
}

void fs_allocator_test_after(uint operation, uint phase)
{
	struct virtio_durability_test_stats durability;
	struct bio_physical_stats physical;
	const char *operation_name;
	const char *phase_name;

	if (!fs_allocator_test_claim(operation, phase, FSALLOC_ACTION_CRASH))
		return;
	virtio_disk_durability_test_stats(&durability);
	memset(&physical, 0, sizeof(physical));
	(void)bio_physical_snapshot(&physical);
	operation_name = fs_allocator_test_operation_name(operation);
	phase_name = fs_allocator_test_phase_name(phase);
	if (durability.version != VIRTIO_DURABILITY_TEST_ABI_VERSION ||
	    durability.size != sizeof(durability) ||
	    durability.capacity != VIRTIO_DURABILITY_OVERLAY_CAPACITY ||
	    durability.successful_flushes == 0 ||
	    durability.successful_flushes > durability.flush_attempts ||
	    durability.pending_blocks != 0 ||
	    durability.last_flush_pending_before == 0 ||
	    durability.last_flush_pending_after != 0 ||
	    durability.last_acknowledged_sequence == 0 ||
	    durability.last_acknowledged_sequence != durability.cached_writes ||
	    durability.raw_writes > durability.last_acknowledged_sequence ||
	    durability.failed_flushes != 0 ||
	    durability.capacity_failures != 0 ||
	    physical.version != BIO_PHYSICAL_STATS_VERSION ||
	    physical.size != sizeof(physical) ||
	    !fs_allocator_fault.receipt_baseline_valid ||
	    durability.raw_writes < fs_allocator_fault.raw_writes_before ||
	    durability.successful_flushes < fs_allocator_fault.flushes_before ||
	    physical.writes < fs_allocator_fault.physical_writes_before ||
	    physical.flushes < fs_allocator_fault.physical_flushes_before ||
	    durability.raw_writes - fs_allocator_fault.raw_writes_before !=
		physical.writes - fs_allocator_fault.physical_writes_before ||
	    durability.successful_flushes - fs_allocator_fault.flushes_before !=
		physical.flushes - fs_allocator_fault.physical_flushes_before) {
#ifdef FS_ALLOCATOR_DELETE_BARRIER_MUTANT
		printf("fsalloc-cache: mutation=delete-flush "
		       "target=allocator-phase-barrier durable_epoch=%llu "
		       "pending_at_powercut=%d discarded_on_powercut=%d "
		       "powercut=1\n",
		       (unsigned long long)durability.epoch,
		       (int)durability.pending_blocks,
		       (int)durability.pending_blocks);
#endif
		printf("fsallocfault_kernel: durability_receipt_failed=1\n");
		intr_off();
		for (;;)
			asm volatile("wfi");
	}
	printf("fsalloc-cache: receipt_id=%s-%s-crash:fault:flush "
	       "backend_instance_id=%s-%s-crash:fault "
	       "abi_version=%u capacity_bytes=%u "
	       "durable_epoch=%llu raw_write_count=%llu "
	       "cached_write_count=%llu "
	       "flush_command_count=%llu acknowledged_flush_count=%llu "
	       "last_acknowledged_sequence=%llu "
	       "pending_before=%d "
	       "pending_after=%d pending_at_stage_end=%d "
	       "powercut_after_receipt=1\n",
	       operation_name, phase_name, operation_name, phase_name,
	       VIRTIO_DURABILITY_TEST_ABI_VERSION,
	       durability.capacity * BSIZE,
	       (unsigned long long)durability.epoch,
	       (unsigned long long)durability.raw_writes,
	       (unsigned long long)durability.cached_writes,
	       (unsigned long long)durability.flush_attempts,
	       (unsigned long long)durability.successful_flushes,
	       (unsigned long long)durability.last_acknowledged_sequence,
	       (int)durability.last_flush_pending_before,
	       (int)durability.last_flush_pending_after,
	       (int)durability.pending_blocks);
	printf("fsallocfault_kernel: case=%s phase=%s crash_checkpoint=1\n",
	       operation_name, phase_name);
	intr_off();
	for (;;)
		asm volatile("wfi");
}

void fs_allocator_test_snapshot(struct fsalloc_test_snapshot *snapshot)
{
	struct virtio_durability_test_stats durability;
	struct bio_physical_stats physical;
	int enabled;

	if (snapshot == 0)
		return;
	memset(snapshot, 0, sizeof(*snapshot));
	snapshot->version = FSALLOC_TEST_ABI_VERSION;
	snapshot->size = sizeof(*snapshot);
	enabled = intr_save();
	snapshot->hook_hits = fs_allocator_fault.hook_hits;
	snapshot->armed = fs_allocator_fault.armed;
	intr_restore(enabled);
	virtio_disk_durability_test_stats(&durability);
	snapshot->durability_profile =
		durability.version == VIRTIO_DURABILITY_TEST_ABI_VERSION &&
		durability.size == sizeof(durability);
	snapshot->durability_capacity = durability.capacity;
	snapshot->durability_pending_blocks = durability.pending_blocks;
	snapshot->durability_epoch = durability.epoch;
	snapshot->durability_cached_writes = durability.cached_writes;
	snapshot->durability_overlay_reads = durability.overlay_reads;
	snapshot->durability_raw_writes = durability.raw_writes;
	snapshot->durability_last_acknowledged_sequence =
		durability.last_acknowledged_sequence;
	snapshot->durability_flush_attempts = durability.flush_attempts;
	snapshot->durability_successful_flushes = durability.successful_flushes;
	snapshot->durability_failed_flushes = durability.failed_flushes;
	snapshot->durability_capacity_failures = durability.capacity_failures;
	if (bio_physical_snapshot(&physical) == 0 &&
	    physical.version == BIO_PHYSICAL_STATS_VERSION &&
	    physical.size == sizeof(physical)) {
		snapshot->physical_reads = physical.reads;
		snapshot->physical_writes = physical.writes;
		snapshot->physical_flushes = physical.flushes;
		snapshot->physical_failed_transfers = physical.failed_transfers;
		snapshot->physical_completion_sequence =
			physical.completion_sequence;
	}
	fs_allocator_test_storage_snapshot(snapshot);
}
#endif
