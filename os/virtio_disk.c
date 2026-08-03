// Legacy virtio-mmio block driver with schedulable completion waits.

#include "bio.h"
#include "defs.h"
#include "file.h"
#include "fs.h"
#include "plic.h"
#include "proc.h"
#include "riscv.h"
#include "timer.h"
#include "types.h"
#include "virtio.h"

#define R(r) ((volatile uint32 *)(VIRTIO0 + (r)))

#ifdef VIRTIO_DISK_FAULT_INJECTION
_Static_assert((int)VIRTIO_TEST_REJECTED_RANGE ==
	       (int)VIRTIO_DISK_ERR_RANGE,
	       "VirtIO range result must be stable across the test ABI");
#endif

enum virtio_disk_state {
	VIRTIO_DISK_OFFLINE = 0,
	VIRTIO_DISK_ONLINE,
	VIRTIO_DISK_QUIESCING,
	VIRTIO_DISK_RESETTING,
	VIRTIO_DISK_REINIT,
};

static struct disk {
	// Legacy queues require two contiguous, page-aligned DMA pages.
	char pages[2][2 * PGSIZE];
	struct virtq_desc *desc;
	struct virtq_avail *avail;
	struct virtq_used *used;
	char free[NUM];
	uint16 used_idx;
	struct {
		struct buf *b;
		uint owner;
		uint io_class;
		uchar active;
		uchar completed;
		uchar device_done;
		uchar descriptors_freed;
		uint type;
		uint bank;
		int result;
		uint64 deadline;
		uint64 release_tick;
		uint64 submit_tick;
		uint64 generation;
		uint64 request_id;
		struct wait_queue waiters;
#ifdef VIRTIO_DISK_FAULT_INJECTION
		uint test_flags;
		uchar test_status;
		uint test_delay_ticks;
#endif
	} info[NUM];

	struct virtio_blk_req ops[2][NUM];
	/* DMA never targets a caller-owned buffer. Reset quarantine stays static. */
	uchar bounce[2][NUM][BSIZE];
	uchar device_status[2][NUM];
	uint active_bank;
	struct wait_queue desc_waiters;
	uint32 features;
	uint64 capacity;
	uint64 tick;
	uint64 queue_generation;
	uint64 next_request_id;
	uint timeout_ticks;
	enum virtio_disk_state state;
	int runtime;
	int reset_result;
	uint64 reset_deadline;
	uint64 reset_cycle_deadline;
#ifdef VIRTIO_DISK_FAULT_INJECTION
	uint test_flags;
	uint test_delay;
	uint test_after;
	uint test_profile_submits;
	int test_claimed;
	int test_status;
	int test_stuck_reset;
	struct virtio_test_stats test_stats;
#endif
#ifdef VIRTIO_DISK_TEST_PROFILE
	struct proc *test_controller;
	struct resource_account_handle test_controller_account;
	uint64 test_controller_generation;
#endif
#ifdef DURABILITY_POWERCUT_TEST_PROFILE
	/*
	 * A test-only volatile write-back device.  No cached WRITE reaches the
	 * host image until a real device FLUSH commits the whole overlay epoch.
	 */
	struct {
		uint blockno;
		uint64 sequence;
		uchar valid;
		uchar data[BSIZE];
	} durability_overlay[VIRTIO_DURABILITY_OVERLAY_CAPACITY];
	struct buf durability_commit_buf;
	struct wait_queue durability_waiters;
	uint durability_count;
	int durability_busy;
	uint64 durability_sequence;
	struct virtio_durability_test_stats durability_stats;
#endif
} __attribute__((aligned(PGSIZE))) disk;

#ifdef DURABILITY_POWERCUT_TEST_PROFILE
_Static_assert(VIRTIO_DURABILITY_OVERLAY_CAPACITY >=
	       2U * (MAXFILE + 1U) + MAXOPBLOCKS + 64U,
	       "volatile overlay must hold two metadata banks and one FS op");
#endif

static void disk_queue_reset_memory(void)
{
	char *pages = disk.pages[disk.active_bank];

	memset(pages, 0, 2 * PGSIZE);
	memset(disk.device_status[disk.active_bank], 0xff,
	       sizeof(disk.device_status[disk.active_bank]));
	disk.desc = (struct virtq_desc *)pages;
	disk.avail = (struct virtq_avail *)(pages +
					    NUM * sizeof(struct virtq_desc));
	disk.used = (struct virtq_used *)(pages + PGSIZE);
	disk.used_idx = 0;
	for (int i = 0; i < NUM; i++)
		disk.free[i] = 1;
}

static int disk_device_start(int rotate_bank)
{
	uint32 status = 0;

	if (rotate_bank)
		disk.active_bank ^= 1U;

	if (*R(VIRTIO_MMIO_MAGIC_VALUE) != 0x74726976 ||
	    *R(VIRTIO_MMIO_VERSION) != 1 || *R(VIRTIO_MMIO_DEVICE_ID) != 2 ||
	    *R(VIRTIO_MMIO_VENDOR_ID) != 0x554d4551)
		return -1;
	*R(VIRTIO_MMIO_STATUS) = 0;
	__sync_synchronize();
	if (*R(VIRTIO_MMIO_STATUS) != 0)
		return -1;
	status |= VIRTIO_CONFIG_S_ACKNOWLEDGE;
	*R(VIRTIO_MMIO_STATUS) = status;

	status |= VIRTIO_CONFIG_S_DRIVER;
	*R(VIRTIO_MMIO_STATUS) = status;

	uint32 features = *R(VIRTIO_MMIO_DEVICE_FEATURES) &
		(1U << VIRTIO_BLK_F_FLUSH);
	*R(VIRTIO_MMIO_DRIVER_FEATURES) = features;
	disk.features = features;
	status |= VIRTIO_CONFIG_S_FEATURES_OK;
	*R(VIRTIO_MMIO_STATUS) = status;
	if ((*R(VIRTIO_MMIO_STATUS) & VIRTIO_CONFIG_S_FEATURES_OK) == 0)
		return -1;
	*R(VIRTIO_MMIO_GUEST_PAGE_SIZE) = PGSIZE;
	*R(VIRTIO_MMIO_QUEUE_SEL) = 0;
	uint32 max = *R(VIRTIO_MMIO_QUEUE_NUM_MAX);
	if (max == 0)
		return -1;
	if (max < NUM)
		return -1;
	if (*R(VIRTIO_MMIO_QUEUE_PFN) != 0)
		return -1;
	disk_queue_reset_memory();
	*R(VIRTIO_MMIO_QUEUE_NUM) = NUM;
	*R(VIRTIO_MMIO_QUEUE_ALIGN) = PGSIZE;
	*R(VIRTIO_MMIO_QUEUE_PFN) =
		((uint64)disk.pages[disk.active_bank]) >> PGSHIFT;
	status |= VIRTIO_CONFIG_S_DRIVER_OK;
	*R(VIRTIO_MMIO_STATUS) = status;
	if ((*R(VIRTIO_MMIO_STATUS) & VIRTIO_CONFIG_S_DRIVER_OK) == 0)
		return -1;
	disk.capacity = (uint64)*R(VIRTIO_MMIO_CONFIG) |
		((uint64)*R(VIRTIO_MMIO_CONFIG + 4) << 32);
	disk.queue_generation++;
	if (disk.queue_generation == 0)
		disk.queue_generation++;
	return 0;
}

void virtio_disk_init()
{
	wait_queue_init(&disk.desc_waiters, WAIT_REASON_VIRTIO_DESCRIPTOR);
	for (int i = 0; i < NUM; i++)
		wait_queue_init(&disk.info[i].waiters,
				WAIT_REASON_VIRTIO_COMPLETION);
#ifdef DURABILITY_POWERCUT_TEST_PROFILE
	wait_queue_init(&disk.durability_waiters,
			WAIT_REASON_VIRTIO_DESCRIPTOR);
	disk.durability_stats.version = VIRTIO_DURABILITY_TEST_ABI_VERSION;
	disk.durability_stats.size = sizeof(disk.durability_stats);
	disk.durability_stats.capacity = VIRTIO_DURABILITY_OVERLAY_CAPACITY;
#endif
	disk.timeout_ticks = VIRTIO_DISK_REQUEST_TIMEOUT_TICKS;
	disk.state = VIRTIO_DISK_OFFLINE;
	if (disk_device_start(0) < 0)
		panic("virtio disk initialization failed");
	disk.state = VIRTIO_DISK_ONLINE;
}
void virtio_disk_runtime_start(void)
{
	disk.runtime = 1;
}
static int alloc_desc()
{
	for (int i = 0; i < NUM; i++) {
		if (disk.free[i] && !disk.info[i].active) {
			disk.free[i] = 0;
			return i;
		}
	}
	return -1;
}
static void free_desc(int i)
{
	if (i >= NUM)
		panic("free_desc 1");
	if (disk.free[i])
		panic("free_desc 2");
	disk.desc[i].addr = 0;
	disk.desc[i].len = 0;
	disk.desc[i].flags = 0;
	disk.desc[i].next = 0;
	disk.free[i] = 1;
}
static void free_chain(int i)
{
	while (1) {
		int flag = disk.desc[i].flags;
		int nxt = disk.desc[i].next;
		free_desc(i);
		if (flag & VRING_DESC_F_NEXT)
			i = nxt;
		else
			break;
	}
}
static int alloc_desc_chain(int *idx, int count)
{
	for (int i = 0; i < count; i++) {
		idx[i] = alloc_desc();
		if (idx[i] < 0) {
			for (int j = 0; j < i; j++)
				free_desc(idx[j]);
			return -1;
		}
	}
	return 0;
}
static int disk_status_result(uchar status)
{
	if (status == VIRTIO_BLK_S_OK)
		return VIRTIO_DISK_OK;
	if (status == VIRTIO_BLK_S_UNSUPP)
		return VIRTIO_DISK_ERR_UNSUPPORTED;
	return VIRTIO_DISK_ERR_IO;
}

#ifdef VIRTIO_DISK_FAULT_INJECTION
static void disk_test_result(int result)
{
	if (result == VIRTIO_DISK_ERR_TIMEOUT)
		disk.test_stats.timeout_results++;
	else if (result == VIRTIO_DISK_ERR_IO)
		disk.test_stats.io_errors++;
	else if (result == VIRTIO_DISK_ERR_UNSUPPORTED)
		disk.test_stats.unsupported_errors++;
	else if (result == VIRTIO_DISK_ERR_OFFLINE)
		disk.test_stats.offline_errors++;
}
#endif

static void disk_complete(int id, int result)
{
	if (id < 0 || id >= NUM || !disk.info[id].active ||
	    disk.info[id].completed)
		return;
	if (disk.info[id].generation != disk.queue_generation)
		return;
	if (result == VIRTIO_DISK_OK &&
	    disk.info[id].type == VIRTIO_BLK_T_IN &&
	    disk.info[id].b != 0)
		memmove(disk.info[id].b->data,
			disk.bounce[disk.info[id].bank][id], BSIZE);
	disk.info[id].result = result;
	disk.info[id].completed = 1;
#ifdef VIRTIO_DISK_FAULT_INJECTION
	disk.test_stats.completions++;
	if (disk.test_stats.inflight)
		disk.test_stats.inflight--;
	disk_test_result(result);
	disk.test_stats.last_request_id = disk.info[id].request_id;
	disk.test_stats.last_request_type = disk.info[id].type;
	disk.test_stats.last_submit_tick = disk.info[id].submit_tick;
	disk.test_stats.last_complete_tick = disk.tick;
	disk.test_stats.last_result = result;
#endif
	if (disk.info[id].b != 0) {
		disk.info[id].b->disk = 0;
		disk.info[id].b->disk_result = result;
	}
	wait_queue_wake_all(&disk.info[id].waiters);
}

static void disk_complete_quarantined(int id, int result)
{
	if (id < 0 || id >= NUM || !disk.info[id].active ||
	    disk.info[id].completed)
		return;
	disk.info[id].result = result;
	disk.info[id].completed = 1;
#ifdef VIRTIO_DISK_FAULT_INJECTION
	disk.test_stats.completions++;
	if (disk.test_stats.inflight)
		disk.test_stats.inflight--;
	disk_test_result(result);
	disk.test_stats.last_request_id = disk.info[id].request_id;
	disk.test_stats.last_request_type = disk.info[id].type;
	disk.test_stats.last_submit_tick = disk.info[id].submit_tick;
	disk.test_stats.last_complete_tick = disk.tick;
	disk.test_stats.last_result = result;
#endif
	if (disk.info[id].b != 0) {
		disk.info[id].b->disk = 0;
		disk.info[id].b->disk_result = result;
	}
	/* The controller did not acknowledge reset: descriptors stay quarantined. */
	wait_queue_wake_all(&disk.info[id].waiters);
}

static void disk_complete_all(int result, int dma_fenced)
{
	for (int i = 0; i < NUM; i++) {
		if (!disk.info[i].active)
			continue;
		if (dma_fenced) {
			/* Queue reset, not the old descriptor chain, owns reclamation. */
			disk.info[i].descriptors_freed = 1;
			disk_complete(i, result);
		} else {
			disk_complete_quarantined(i, result);
		}
	}
}

/*
 * Completion publishes only the result. Descriptor ownership stays with the
 * submitting thread until it consumes that result, so a head cannot be
 * advertised to waiters while alloc_desc() still rejects it as active.
 */
static void disk_release_request(int id)
{
	int descriptors_available = 0;

	if (id < 0 || id >= NUM || !disk.info[id].active ||
	    !disk.info[id].completed)
		panic("virtio request release");
	if (!disk.info[id].descriptors_freed &&
	    disk.state == VIRTIO_DISK_ONLINE &&
	    disk.info[id].generation == disk.queue_generation) {
		free_chain(id);
		disk.info[id].descriptors_freed = 1;
		descriptors_available = 1;
	} else if (disk.info[id].descriptors_freed &&
		   disk.state == VIRTIO_DISK_ONLINE && disk.free[id]) {
		/* An acknowledged reset rebuilt the queue while this result waited. */
		descriptors_available = 1;
	}
	disk.info[id].active = 0;
	disk.info[id].b = 0;
#ifdef VIRTIO_DISK_FAULT_INJECTION
	disk.info[id].test_flags = 0;
	disk.info[id].test_delay_ticks = 0;
	if (descriptors_available)
		disk.test_stats.descriptor_reclaims++;
#endif
	if (descriptors_available)
		wait_queue_wake_all(&disk.desc_waiters);
}

static void disk_reset_begin(int result)
{
	if (disk.state != VIRTIO_DISK_ONLINE)
		return;
	disk.state = VIRTIO_DISK_QUIESCING;
	disk.reset_result = result;
	disk.reset_deadline = disk.tick + VIRTIO_DISK_RESET_TIMEOUT_TICKS;
	disk.reset_cycle_deadline = get_cycle() +
		(uint64)VIRTIO_DISK_RESET_TIMEOUT_TICKS *
		(CPU_FREQ / TICKS_PER_SEC);
#ifdef VIRTIO_DISK_FAULT_INJECTION
	disk.test_stuck_reset = 0;
	for (int i = 0; i < NUM; i++)
		if (disk.info[i].active &&
		    (disk.info[i].test_flags & VIRTIO_DISK_TEST_STUCK_RESET))
			disk.test_stuck_reset = 1;
	disk.test_flags = 0;
	disk.test_claimed = 0;
	disk.test_profile_submits = 0;
	disk.test_stats.resets++;
#endif
	wait_queue_wake_all(&disk.desc_waiters);
}

static void disk_reset_offline(void)
{
	if (disk.state != VIRTIO_DISK_RESETTING &&
	    disk.state != VIRTIO_DISK_QUIESCING)
		return;
	disk.state = VIRTIO_DISK_OFFLINE;
	disk_complete_all(disk.reset_result, 0);
#ifdef VIRTIO_DISK_FAULT_INJECTION
	disk.test_stats.reset_offline++;
#endif
	wait_queue_wake_all(&disk.desc_waiters);
}

static void disk_reset_step(void)
{
	if (disk.state == VIRTIO_DISK_QUIESCING) {
		*R(VIRTIO_MMIO_STATUS) = 0;
		__sync_synchronize();
		disk.state = VIRTIO_DISK_RESETTING;
	}
	if (disk.state != VIRTIO_DISK_RESETTING)
		return;
#ifdef VIRTIO_DISK_FAULT_INJECTION
	if (disk.test_stuck_reset) {
		if (disk.tick >= disk.reset_deadline)
			disk_reset_offline();
		return;
	}
#endif
	if (*R(VIRTIO_MMIO_STATUS) == 0) {
		/* A read-back reset acknowledgement is the DMA ownership fence. */
		__sync_synchronize();
		disk.state = VIRTIO_DISK_REINIT;
		disk_complete_all(disk.reset_result, 1);
		if (disk_device_start(1) < 0) {
			disk.state = VIRTIO_DISK_OFFLINE;
#ifdef VIRTIO_DISK_FAULT_INJECTION
			disk.test_stats.reset_offline++;
#endif
		} else {
			disk.state = VIRTIO_DISK_ONLINE;
#ifdef VIRTIO_DISK_FAULT_INJECTION
			disk.test_stats.reset_recoveries++;
#endif
		}
		wait_queue_wake_all(&disk.desc_waiters);
		return;
	}
	if (disk.tick >= disk.reset_deadline)
		disk_reset_offline();
}

static uint disk_outstanding_count(void)
{
	uint outstanding = 0;

	for (int id = 0; id < NUM; id++)
		if (disk.info[id].active &&
		    disk.info[id].generation == disk.queue_generation)
			outstanding++;
	return outstanding;
}

#ifdef VIRTIO_DISK_FAULT_INJECTION
static void disk_test_forge_used_index(void)
{
	uint16 pending = (uint16)(disk.used->idx - disk.used_idx);
	uint id;

	if (pending == 0)
		return;
	id = disk.used->ring[disk.used_idx % NUM].id;
	if (id >= NUM || !disk.info[id].active ||
	    !(disk.info[id].test_flags &
	      VIRTIO_DISK_TEST_FORGE_USED_INDEX))
		return;
	disk.info[id].test_flags &= ~VIRTIO_DISK_TEST_FORGE_USED_INDEX;
	/* Simulate a device attempting to make more progress than this queue owns. */
	disk.used->idx = (uint16)(disk.used_idx + NUM + 1);
	__sync_synchronize();
}

static int disk_test_inject_duplicate_used(int id, uint flags)
{
	uint16 next;

	if (!(flags & VIRTIO_DISK_TEST_DUPLICATE_USED))
		return 0;
	disk.info[id].test_flags &= ~VIRTIO_DISK_TEST_DUPLICATE_USED;
	next = disk.used->idx;
	disk.used->ring[next % NUM].id = id;
	__sync_synchronize();
	disk.used->idx = (uint16)(next + 1);
	__sync_synchronize();
	disk.test_stats.duplicate_used_injections++;
	return 1;
}
#endif

static void disk_process_used(int from_irq)
{
	uint16 device_idx;
	uint16 pending;
	uint outstanding;
	uint processed = 0;

	/* The argument is only consumed by compile-time fault injection. */
	(void)from_irq;
	if (disk.state != VIRTIO_DISK_ONLINE)
		return;
	__sync_synchronize();
#ifdef VIRTIO_DISK_FAULT_INJECTION
	disk_test_forge_used_index();
#endif
	device_idx = disk.used->idx;
	pending = (uint16)(device_idx - disk.used_idx);
	if (pending == 0)
		return;
	outstanding = disk_outstanding_count();
	if (pending > NUM || pending > outstanding) {
#ifdef VIRTIO_DISK_FAULT_INJECTION
		disk.test_stats.used_budget_resets++;
#endif
		disk_reset_begin(VIRTIO_DISK_ERR_IO);
		return;
	}
	while (disk.state == VIRTIO_DISK_ONLINE && processed < pending &&
	       processed < NUM) {
		int id;

		__sync_synchronize();
		id = disk.used->ring[disk.used_idx % NUM].id;
		if (id < 0 || id >= NUM || !disk.info[id].active ||
		    disk.info[id].generation != disk.queue_generation ||
		    disk.info[id].completed || disk.info[id].device_done) {
#ifdef VIRTIO_DISK_FAULT_INJECTION
			disk.test_stats.invalid_used_entries++;
#endif
			disk_reset_begin(VIRTIO_DISK_ERR_IO);
			return;
		}
#ifdef VIRTIO_DISK_FAULT_INJECTION
		uint flags = disk.info[id].test_flags;
		if ((flags & VIRTIO_DISK_TEST_STALL_COMPLETION) ||
		    (from_irq && (flags & VIRTIO_DISK_TEST_DROP_COMPLETION)))
			return;
		if (!from_irq && (flags & VIRTIO_DISK_TEST_DROP_COMPLETION))
			disk.test_stats.timer_recoveries++;
#endif
		disk.used_idx++;
		processed++;
		disk.info[id].device_done = 1;
#ifdef VIRTIO_DISK_FAULT_INJECTION
		if (flags & VIRTIO_DISK_TEST_FORCE_STATUS)
			disk.device_status[disk.info[id].bank][id] =
				disk.info[id].test_status;
		if (flags & VIRTIO_DISK_TEST_DELAY_COMPLETION) {
			disk.info[id].release_tick =
				disk.tick + disk.info[id].test_delay_ticks;
			disk.test_stats.delayed_completions++;
			if (disk_test_inject_duplicate_used(id, flags)) {
				if (pending == NUM) {
					disk.test_stats.used_budget_resets++;
					disk_reset_begin(VIRTIO_DISK_ERR_IO);
					return;
				}
				pending++;
			}
			continue;
		}
#endif
		disk_complete(id, disk_status_result(
			disk.device_status[disk.info[id].bank][id]));
#ifdef VIRTIO_DISK_FAULT_INJECTION
		if (disk_test_inject_duplicate_used(id, flags)) {
			if (pending == NUM) {
				disk.test_stats.used_budget_resets++;
				disk_reset_begin(VIRTIO_DISK_ERR_IO);
				return;
			}
			pending++;
		}
#endif
	}
#ifdef VIRTIO_DISK_FAULT_INJECTION
	if (processed > disk.test_stats.max_used_batch)
		disk.test_stats.max_used_batch = processed;
#endif
}

static int disk_can_sleep(void)
{
	struct thread *t = curr_thread();
	return t != 0 && t->state == RUNNING && t->tid >= 0;
}

#ifdef DURABILITY_POWERCUT_TEST_PROFILE
static int disk_durability_overlay_enter(void)
{
	int enabled = intr_save();

	while (disk.durability_busy) {
		if (!disk.runtime || !disk_can_sleep()) {
			intr_restore(enabled);
			return VIRTIO_DISK_ERR_BUSY;
		}
		if (wait_queue_sleep_irq_uninterruptible(
			    &disk.durability_waiters) != WAIT_QUEUE_OK) {
			intr_restore(enabled);
			return VIRTIO_DISK_ERR_BUSY;
		}
	}
	disk.durability_busy = 1;
	intr_restore(enabled);
	return VIRTIO_DISK_OK;
}

static void disk_durability_overlay_leave(void)
{
	int enabled = intr_save();

	disk.durability_busy = 0;
	wait_queue_wake_all(&disk.durability_waiters);
	intr_restore(enabled);
}

static int disk_durability_overlay_find(uint blockno)
{
	for (uint slot = 0; slot < VIRTIO_DURABILITY_OVERLAY_CAPACITY; slot++)
		if (disk.durability_overlay[slot].valid &&
		    disk.durability_overlay[slot].blockno == blockno)
			return (int)slot;
	return -1;
}

static int disk_durability_overlay_store(const struct buf *b)
{
	int slot = disk_durability_overlay_find(b->blockno);

	if (disk.durability_sequence == ~(uint64)0) {
		disk.durability_stats.capacity_failures++;
		return VIRTIO_DISK_ERR_IO;
	}
	if (slot < 0) {
		if (disk.durability_count ==
		    VIRTIO_DURABILITY_OVERLAY_CAPACITY) {
			disk.durability_stats.capacity_failures++;
			return VIRTIO_DISK_ERR_IO;
		}
		for (uint candidate = 0;
		     candidate < VIRTIO_DURABILITY_OVERLAY_CAPACITY;
		     candidate++)
			if (!disk.durability_overlay[candidate].valid) {
				slot = (int)candidate;
				break;
			}
		if (slot < 0) {
			disk.durability_stats.capacity_failures++;
			return VIRTIO_DISK_ERR_IO;
		}
		disk.durability_overlay[slot].valid = 1;
		disk.durability_overlay[slot].blockno = b->blockno;
		disk.durability_count++;
	}
	disk.durability_sequence++;
	disk.durability_overlay[slot].sequence = disk.durability_sequence;
	memmove(disk.durability_overlay[slot].data, b->data, BSIZE);
	disk.durability_stats.cached_writes++;
	disk.durability_stats.pending_blocks = disk.durability_count;
	return VIRTIO_DISK_OK;
}
#endif

static int disk_submit(struct buf *b, uint type, int test_direct,
		       uint64 sector)
{
	int count = type == VIRTIO_BLK_T_FLUSH ? 2 : 3;
	int idx[3], head = -1, submitted = 0;
	int status = VIRTIO_DISK_ERR_IO;
	uint owner = FS_OWNER_SYSTEM;
	uint io_class = IO_POLICY_CLASS_SYSTEM;
	int enabled;
	uint64 boot_deadline = get_cycle() +
		(uint64)VIRTIO_DISK_REQUEST_TIMEOUT_TICKS *
		(CPU_FREQ / TICKS_PER_SEC);
	uint64 request_id;

	(void)test_direct;
	bio_current_sponsor(&owner, &io_class);
	enabled = intr_save();
	disk.next_request_id++;
	if (disk.next_request_id == 0)
		disk.next_request_id++;
	request_id = disk.next_request_id;

	if (b != 0 && (sector >= disk.capacity ||
	    BSIZE / 512 > disk.capacity - sector)) {
		status = VIRTIO_DISK_ERR_RANGE;
		goto out;
	}
	if (disk.runtime && !disk_can_sleep()) {
		status = VIRTIO_DISK_ERR_BUSY;
		goto out;
	}
	for (;;) {
		while (disk.state != VIRTIO_DISK_ONLINE) {
			if (disk.state == VIRTIO_DISK_OFFLINE) {
				status = VIRTIO_DISK_ERR_OFFLINE;
				goto out;
			}
			if (!disk_can_sleep()) {
				disk_reset_step();
				if (get_cycle() >= disk.reset_cycle_deadline)
					disk_reset_offline();
				continue;
			}
			(void)wait_queue_sleep_irq_uninterruptible(
				&disk.desc_waiters);
		}
		if (alloc_desc_chain(idx, count) == 0)
			break;
		if (!disk_can_sleep()) {
			disk_process_used(0);
			continue;
		}
#ifdef VIRTIO_DISK_FAULT_INJECTION
		disk.test_stats.descriptor_waits++;
#endif
		(void)wait_queue_sleep_irq_uninterruptible(&disk.desc_waiters);
	}
	head = idx[0];
	int tail = idx[count - 1];
	struct virtio_blk_req *req = &disk.ops[disk.active_bank][head];
	req->type = type;
	req->reserved = 0;
	req->sector = sector;
	disk.desc[head].addr = (uint64)req;
	disk.desc[head].len = sizeof(*req);
	disk.desc[head].flags = VRING_DESC_F_NEXT;
	disk.desc[head].next = idx[1];
	if (b != 0) {
		if (type == VIRTIO_BLK_T_OUT)
			memmove(disk.bounce[disk.active_bank][head], b->data,
				BSIZE);
		disk.desc[idx[1]].addr =
			(uint64)disk.bounce[disk.active_bank][head];
		disk.desc[idx[1]].len = BSIZE;
		disk.desc[idx[1]].flags = (type == VIRTIO_BLK_T_IN ?
			VRING_DESC_F_WRITE : 0) | VRING_DESC_F_NEXT;
		disk.desc[idx[1]].next = tail;
		b->disk = 1;
	}
	disk.info[head].b = b;
	disk.device_status[disk.active_bank][head] = 0xff;
	disk.info[head].active = 1;
	disk.info[head].completed = 0;
	disk.info[head].device_done = 0;
	disk.info[head].descriptors_freed = 0;
	disk.info[head].type = type;
	disk.info[head].bank = disk.active_bank;
	disk.info[head].release_tick = 0;
	disk.info[head].submit_tick = disk.tick;
	disk.info[head].deadline = disk.tick + disk.timeout_ticks;
	disk.info[head].generation = disk.queue_generation;
	disk.info[head].request_id = request_id;
	disk.info[head].owner = owner;
	disk.info[head].io_class = io_class;
#ifdef VIRTIO_DISK_FAULT_INJECTION
	int inject_test_fault = 1;
#ifdef VIRTIO_DISK_TEST_PROFILE
	inject_test_fault = test_direct;
#else
	(void)test_direct;
#endif
	disk.info[head].test_flags = 0;
	disk.info[head].test_status = 0;
	disk.info[head].test_delay_ticks = 0;
	if ((disk.test_flags & VIRTIO_DISK_TEST_FULL_RING_RECLAIM) &&
	    inject_test_fault) {
		uint sequence = disk.test_profile_submits++;

		if (sequence < 3) {
			disk.info[head].test_flags =
				VIRTIO_DISK_TEST_DELAY_COMPLETION;
			disk.info[head].test_delay_ticks = sequence == 0 ?
				disk.test_delay : 4 * disk.test_delay;
		}
		if (sequence >= 2)
			disk.test_flags = 0;
	} else if (disk.test_flags && inject_test_fault &&
	    (!disk.test_claimed ||
	     (disk.test_flags & VIRTIO_DISK_TEST_REPEAT))) {
		if (disk.test_after)
			disk.test_after--;
		else {
			disk.info[head].test_flags = disk.test_flags;
			disk.info[head].test_status = disk.test_status;
			disk.info[head].test_delay_ticks = disk.test_delay;
			disk.test_claimed = 1;
			if (!(disk.test_flags & VIRTIO_DISK_TEST_REPEAT)) {
				disk.test_flags = 0;
				disk.test_claimed = 0;
			}
		}
	}
#endif
	disk.desc[tail].addr =
		(uint64)&disk.device_status[disk.active_bank][head];
	disk.desc[tail].len = 1;
	disk.desc[tail].flags = VRING_DESC_F_WRITE;
	disk.desc[tail].next = 0;
	disk.avail->ring[disk.avail->idx % NUM] = head;
	__sync_synchronize();
	disk.avail->idx++;
	__sync_synchronize();
	*R(VIRTIO_MMIO_QUEUE_NOTIFY) = 0;
	submitted = 1;
#ifdef VIRTIO_DISK_FAULT_INJECTION
	disk.test_stats.submits++;
	disk.test_stats.inflight++;
	if (disk.test_stats.inflight > disk.test_stats.max_inflight)
		disk.test_stats.max_inflight = disk.test_stats.inflight;
#endif
	while (!disk.info[head].completed) {
		if (disk_can_sleep()) {
			(void)wait_queue_sleep_irq_uninterruptible(
				&disk.info[head].waiters);
		} else {
			disk_process_used(0);
			if (get_cycle() >= boot_deadline &&
			    disk.state == VIRTIO_DISK_ONLINE)
				disk_reset_begin(VIRTIO_DISK_ERR_TIMEOUT);
			disk_reset_step();
			if ((disk.state == VIRTIO_DISK_RESETTING ||
			     disk.state == VIRTIO_DISK_QUIESCING) &&
			    get_cycle() >= disk.reset_cycle_deadline)
				disk_reset_offline();
		}
	}
	status = disk.info[head].result;
	disk_release_request(head);
out:
#ifdef VIRTIO_DISK_FAULT_INJECTION
	if (!submitted) {
		disk.test_stats.last_request_id = request_id;
		disk.test_stats.last_request_type = type;
		disk.test_stats.last_submit_tick = disk.tick;
		disk.test_stats.last_complete_tick = disk.tick;
		disk.test_stats.last_result = status;
		disk.test_stats.rejected_requests++;
		if (status == VIRTIO_DISK_ERR_RANGE)
			disk.test_stats.range_rejections++;
		disk_test_result(status);
	}
#endif
	intr_restore(enabled);
	if (submitted)
		bio_account_transfer(owner, io_class,
				     type == VIRTIO_BLK_T_IN ? BIO_TRANSFER_READ :
				     type == VIRTIO_BLK_T_OUT ? BIO_TRANSFER_WRITE :
							   BIO_TRANSFER_FLUSH,
				     status);
	return status;
}

int virtio_disk_rw(struct buf *b, int write)
{
	int result;
	int overlay_acquired = 0;

	if (b == 0)
		return VIRTIO_DISK_ERR_IO;
#ifdef DURABILITY_POWERCUT_TEST_PROFILE
	result = disk_durability_overlay_enter();
	if (result == VIRTIO_DISK_OK)
		overlay_acquired = 1;
	if (result == VIRTIO_DISK_OK &&
	    ((uint64)b->blockno * (BSIZE / 512) >= disk.capacity ||
	     BSIZE / 512 > disk.capacity -
			   (uint64)b->blockno * (BSIZE / 512)))
		result = VIRTIO_DISK_ERR_RANGE;
	if (result == VIRTIO_DISK_OK && write)
		result = disk_durability_overlay_store(b);
	else if (result == VIRTIO_DISK_OK) {
		int slot = disk_durability_overlay_find(b->blockno);

		if (slot >= 0) {
			memmove(b->data, disk.durability_overlay[slot].data,
				BSIZE);
			disk.durability_stats.overlay_reads++;
		} else {
			result = disk_submit(
				b, VIRTIO_BLK_T_IN, 0,
				(uint64)b->blockno * (BSIZE / 512));
		}
	}
	if (overlay_acquired)
		disk_durability_overlay_leave();
	return result;
#else
	(void)result;
	(void)overlay_acquired;
	return disk_submit(b, write ? VIRTIO_BLK_T_OUT : VIRTIO_BLK_T_IN, 0,
			   (uint64)b->blockno * (BSIZE / 512));
#endif
}

static int disk_durability_capability(int test_direct)
{
#ifdef VIRTIO_DISK_FAULT_INJECTION
	int inject_test_fault = 1;
#ifdef VIRTIO_DISK_TEST_PROFILE
	inject_test_fault = test_direct;
#else
	(void)test_direct;
#endif
	if ((disk.test_flags & VIRTIO_DISK_TEST_DISABLE_FLUSH) &&
	    inject_test_fault)
		return VIRTIO_DISK_DURABILITY_NONE;
#else
	(void)test_direct;
#endif
	return (disk.features & (1U << VIRTIO_BLK_F_FLUSH)) ?
		VIRTIO_DISK_DURABILITY_FLUSH : VIRTIO_DISK_DURABILITY_NONE;
}

int virtio_disk_durability_capability(void)
{
	return disk_durability_capability(0);
}

static int disk_durability_barrier(int test_direct)
{
	int capability = disk_durability_capability(test_direct);
	if (capability != VIRTIO_DISK_DURABILITY_FLUSH) {
		int enabled;

		enabled = intr_save();
		disk.next_request_id++;
		if (disk.next_request_id == 0)
			disk.next_request_id++;
#ifdef VIRTIO_DISK_FAULT_INJECTION
		disk.test_stats.last_request_id = disk.next_request_id;
		disk.test_stats.last_request_type = VIRTIO_BLK_T_FLUSH;
		disk.test_stats.last_submit_tick = disk.tick;
		disk.test_stats.last_complete_tick = disk.tick;
		disk.test_stats.last_result = VIRTIO_DISK_ERR_UNSUPPORTED;
		disk.test_stats.rejected_requests++;
		disk_test_result(VIRTIO_DISK_ERR_UNSUPPORTED);
#endif
		intr_restore(enabled);
		return VIRTIO_DISK_ERR_UNSUPPORTED;
	}
#ifdef DURABILITY_POWERCUT_TEST_PROFILE
	int result;
	uint remaining;
	uint64 after_sequence = 0;

	result = disk_durability_overlay_enter();
	if (result != VIRTIO_DISK_OK)
		return result;
	disk.durability_stats.flush_attempts++;
	remaining = disk.durability_count;
	disk.durability_stats.last_flush_pending_before = remaining;
	while (remaining != 0) {
		int selected = -1;
		uint64 selected_sequence = ~(uint64)0;

		for (uint slot = 0;
		     slot < VIRTIO_DURABILITY_OVERLAY_CAPACITY; slot++)
			if (disk.durability_overlay[slot].valid &&
			    disk.durability_overlay[slot].sequence > after_sequence &&
			    disk.durability_overlay[slot].sequence <
				    selected_sequence) {
				selected = (int)slot;
				selected_sequence =
					disk.durability_overlay[slot].sequence;
			}
		if (selected < 0) {
			result = VIRTIO_DISK_ERR_IO;
			break;
		}
		memset(&disk.durability_commit_buf, 0,
		       sizeof(disk.durability_commit_buf));
		disk.durability_commit_buf.blockno =
			disk.durability_overlay[selected].blockno;
		memmove(disk.durability_commit_buf.data,
			disk.durability_overlay[selected].data, BSIZE);
		result = disk_submit(
			&disk.durability_commit_buf, VIRTIO_BLK_T_OUT, 0,
			(uint64)disk.durability_commit_buf.blockno *
				(BSIZE / 512));
		if (result != VIRTIO_DISK_OK)
			break;
		disk.durability_stats.raw_writes++;
		after_sequence = selected_sequence;
		remaining--;
	}
	if (result == VIRTIO_DISK_OK)
		result = disk_submit(0, VIRTIO_BLK_T_FLUSH,
				     test_direct, 0);
	if (result == VIRTIO_DISK_OK) {
		for (uint slot = 0;
		     slot < VIRTIO_DURABILITY_OVERLAY_CAPACITY; slot++)
			disk.durability_overlay[slot].valid = 0;
		disk.durability_count = 0;
		disk.durability_stats.pending_blocks = 0;
		disk.durability_stats.last_flush_pending_after = 0;
		if (after_sequence != 0)
			disk.durability_stats.last_acknowledged_sequence =
				after_sequence;
		disk.durability_stats.epoch++;
		disk.durability_stats.successful_flushes++;
	} else {
		/* Keep every slot so a later flush can replay the full epoch. */
		disk.durability_stats.last_flush_pending_after =
			disk.durability_count;
		disk.durability_stats.failed_flushes++;
	}
	disk_durability_overlay_leave();
	return result;
#else
	return disk_submit(0, VIRTIO_BLK_T_FLUSH, test_direct, 0);
#endif
}

int virtio_disk_durability_barrier(void)
{
	return disk_durability_barrier(0);
}

void virtio_disk_tick(void)
{
	int enabled = intr_save();
	disk.tick++;
	if (disk.state == VIRTIO_DISK_ONLINE)
		disk_process_used(0); /* recovers a lost device interrupt */
#ifdef VIRTIO_DISK_FAULT_INJECTION
	for (int i = 0; i < NUM; i++)
		if (disk.info[i].active && disk.info[i].device_done &&
		    !disk.info[i].completed && disk.info[i].release_tick != 0 &&
		    disk.tick >= disk.info[i].release_tick)
			disk_complete(i, disk_status_result(
				disk.device_status[disk.info[i].bank][i]));
#endif
	for (int i = 0; disk.state == VIRTIO_DISK_ONLINE && i < NUM; i++) {
		if (!disk.info[i].active || disk.info[i].completed ||
		    disk.tick < disk.info[i].deadline)
			continue;
		if (disk.info[i].device_done)
			disk_complete(i, VIRTIO_DISK_ERR_TIMEOUT);
		else
			disk_reset_begin(VIRTIO_DISK_ERR_TIMEOUT);
	}
	disk_reset_step();
	intr_restore(enabled);
}

void virtio_disk_intr(void)
{
	uint32 cause = *R(VIRTIO_MMIO_INTERRUPT_STATUS) & 0x3;
	__sync_synchronize();
	if ((cause & 2) &&
	    (*R(VIRTIO_MMIO_STATUS) & VIRTIO_CONFIG_S_DEVICE_NEEDS_RESET))
		disk_reset_begin(VIRTIO_DISK_ERR_IO);
	else if (cause & 2)
		disk.capacity = (uint64)*R(VIRTIO_MMIO_CONFIG) |
			((uint64)*R(VIRTIO_MMIO_CONFIG + 4) << 32);
	if ((cause & 1) && disk.state == VIRTIO_DISK_ONLINE)
		disk_process_used(1);
	*R(VIRTIO_MMIO_INTERRUPT_ACK) = cause;
}

#ifdef VIRTIO_DISK_FAULT_INJECTION
int virtio_disk_test_configure(uint flags, uint delay_ticks, int status,
			       uint timeout_ticks, uint after_requests)
{
	int enabled = intr_save();
	for (int i = 0; i < NUM; i++)
		if (disk.info[i].active) {
			intr_restore(enabled);
			return -1;
		}
	if (disk.state != VIRTIO_DISK_ONLINE) {
		intr_restore(enabled);
		return -1;
	}
	memset(&disk.test_stats, 0, sizeof(disk.test_stats));
	disk.test_stats.version = VIRTIO_TEST_ABI_VERSION;
	disk.test_stats.size = sizeof(disk.test_stats);
	disk.test_flags = flags;
	disk.test_delay = delay_ticks;
	disk.test_after = after_requests;
	disk.test_profile_submits = 0;
	disk.test_claimed = 0;
	disk.test_status = status;
	disk.timeout_ticks = timeout_ticks ? timeout_ticks :
		VIRTIO_DISK_REQUEST_TIMEOUT_TICKS;
	intr_restore(enabled);
	return 0;
}

int virtio_disk_test_read(uint blockno)
{
	struct buf probe;
	memset(&probe, 0, sizeof(probe));
	probe.blockno = blockno;
	return disk_submit(&probe, VIRTIO_BLK_T_IN, 1,
			   (uint64)blockno * (BSIZE / 512));
}

int virtio_disk_test_read_range(void)
{
	struct buf probe;

	memset(&probe, 0, sizeof(probe));
	return disk_submit(&probe, VIRTIO_BLK_T_IN, 1, ~(uint64)0);
}

int virtio_disk_test_flush(void)
{
	return disk_durability_barrier(1);
}

void virtio_disk_test_stats(struct virtio_test_stats *stats)
{
	int enabled = intr_save();
	*stats = disk.test_stats;
	intr_restore(enabled);
}
#endif

#ifdef DURABILITY_POWERCUT_TEST_PROFILE
void virtio_disk_durability_test_stats(
	struct virtio_durability_test_stats *stats)
{
	int enabled;

	if (stats == 0)
		return;
	if (disk_durability_overlay_enter() != VIRTIO_DISK_OK) {
		memset(stats, 0, sizeof(*stats));
		return;
	}
	enabled = intr_save();
	*stats = disk.durability_stats;
	intr_restore(enabled);
	disk_durability_overlay_leave();
}
#endif

#ifdef VIRTIO_DISK_TEST_PROFILE
#ifndef VIRTIO_DISK_TEST_INIT_NAME
#error "VIRTIO_DISK_TEST_PROFILE requires a sealed init image name"
#endif
void virtio_disk_test_bind_boot_init(struct proc *p, const char *name)
{
	int enabled;
	uint expected_len = strlen(VIRTIO_DISK_TEST_INIT_NAME);

	if (p == 0 || name == 0 || p->parent != 0 ||
	    strlen(name) != expected_len ||
	    strncmp(name, VIRTIO_DISK_TEST_INIT_NAME, expected_len) != 0)
		return;
	enabled = intr_save();
	if (disk.test_controller == 0) {
		disk.test_controller = p;
		disk.test_controller_account = p->resource_account;
		disk.test_controller_generation =
			p->threads[0].identity_generation;
	}
	intr_restore(enabled);
}

int virtio_disk_test_authorized(const struct proc *p)
{
	int enabled = intr_save();
	int authorized = p != 0 && p == disk.test_controller &&
		p->parent == 0 && proc_teardown_live((struct proc *)p) &&
		resource_account_handle_valid(p->resource_account) &&
		resource_account_handle_equal(
			p->resource_account, disk.test_controller_account) &&
		p->threads[0].identity_generation != 0 &&
		p->threads[0].identity_generation ==
			disk.test_controller_generation;
	intr_restore(enabled);
	return authorized;
}
#endif
