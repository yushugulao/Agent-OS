#ifndef VIRTIO_H
#define VIRTIO_H

#include "bio.h"
#ifdef VIRTIO_DISK_FAULT_INJECTION
#include "../virtio_test_abi.h"
#endif

//
// virtio device definitions.
// for both the mmio interface, and virtio descriptors.
// only tested with qemu.
// this is the "legacy" virtio interface.
//
// the virtio spec:
// https://docs.oasis-open.org/virtio/virtio/v1.1/virtio-v1.1.pdf
//

// virtio mmio control registers, mapped starting at 0x10001000.
// from qemu virtio_mmio.h
#define VIRTIO_MMIO_MAGIC_VALUE 0x000 // 0x74726976
#define VIRTIO_MMIO_VERSION 0x004 // version; 1 is legacy
#define VIRTIO_MMIO_DEVICE_ID 0x008 // device type; 1 is net, 2 is disk
#define VIRTIO_MMIO_VENDOR_ID 0x00c // 0x554d4551
#define VIRTIO_MMIO_DEVICE_FEATURES 0x010
#define VIRTIO_MMIO_DRIVER_FEATURES 0x020
#define VIRTIO_MMIO_GUEST_PAGE_SIZE 0x028 // page size for PFN, write-only
#define VIRTIO_MMIO_QUEUE_SEL 0x030 // select queue, write-only
#define VIRTIO_MMIO_QUEUE_NUM_MAX 0x034 // max size of current queue, read-only
#define VIRTIO_MMIO_QUEUE_NUM 0x038 // size of current queue, write-only
#define VIRTIO_MMIO_QUEUE_ALIGN 0x03c // used ring alignment, write-only
#define VIRTIO_MMIO_QUEUE_PFN                                                  \
	0x040 // physical page number for queue, read/write
#define VIRTIO_MMIO_QUEUE_READY 0x044 // ready bit
#define VIRTIO_MMIO_QUEUE_NOTIFY 0x050 // write-only
#define VIRTIO_MMIO_INTERRUPT_STATUS 0x060 // read-only
#define VIRTIO_MMIO_INTERRUPT_ACK 0x064 // write-only
#define VIRTIO_MMIO_STATUS 0x070 // read/write
#define VIRTIO_MMIO_CONFIG 0x100

// status register bits, from qemu virtio_config.h
#define VIRTIO_CONFIG_S_ACKNOWLEDGE 1
#define VIRTIO_CONFIG_S_DRIVER 2
#define VIRTIO_CONFIG_S_DRIVER_OK 4
#define VIRTIO_CONFIG_S_FEATURES_OK 8
#define VIRTIO_CONFIG_S_DEVICE_NEEDS_RESET 64

// device feature bits
#define VIRTIO_BLK_F_RO 5 /* Disk is read-only */
#define VIRTIO_BLK_F_SCSI 7 /* Supports scsi command passthru */
#define VIRTIO_BLK_F_FLUSH 9 /* Supports an explicit cache flush */
#define VIRTIO_BLK_F_CONFIG_WCE 11 /* Writeback mode available in config */
#define VIRTIO_BLK_F_MQ 12 /* support more than one vq */
#define VIRTIO_F_ANY_LAYOUT 27
#define VIRTIO_RING_F_INDIRECT_DESC 28
#define VIRTIO_RING_F_EVENT_IDX 29

// this many virtio descriptors.
// must be a power of two.
#define NUM 8

// a single descriptor, from the spec.
struct virtq_desc {
	uint64 addr;
	uint32 len;
	uint16 flags;
	uint16 next;
};
#define VRING_DESC_F_NEXT 1 // chained with another descriptor
#define VRING_DESC_F_WRITE 2 // device writes (vs read)
#define VRING_DESC_F_INDIRECT 4 // descriptor points at an indirect table

// the (entire) avail ring, from the spec.
struct virtq_avail {
	uint16 flags; // always zero
	uint16 idx; // driver will write ring[idx] next
	uint16 ring[NUM]; // descriptor numbers of chain heads
	uint16 unused;
};

// one entry in the "used" ring, with which the
// device tells the driver about completed requests.
struct virtq_used_elem {
	uint32 id; // index of start of completed descriptor chain
	uint32 len;
};

struct virtq_used {
	uint16 flags; // always zero
	uint16 idx; // device increments when it adds a ring[] entry
	struct virtq_used_elem ring[NUM];
};

// these are specific to virtio block devices, e.g. disks,
// described in Section 5.2 of the spec.

#define VIRTIO_BLK_T_IN 0 // read the disk
#define VIRTIO_BLK_T_OUT 1 // write the disk
#define VIRTIO_BLK_T_FLUSH 4 // commit volatile write cache

enum {
	VIRTIO_BLK_S_OK = 0, VIRTIO_BLK_S_IOERR = 1,
	VIRTIO_BLK_S_UNSUPP = 2,
	VIRTIO_DISK_OK = 0, VIRTIO_DISK_ERR_IO = -1,
	VIRTIO_DISK_ERR_UNSUPPORTED = -2, VIRTIO_DISK_ERR_TIMEOUT = -3,
	VIRTIO_DISK_ERR_OFFLINE = -4, VIRTIO_DISK_ERR_BUSY = -5,
	VIRTIO_DISK_ERR_RANGE = -6,
	VIRTIO_DISK_DURABILITY_NONE = 0, VIRTIO_DISK_DURABILITY_FLUSH = 1,
};

/* Requests that make no progress are isolated after five seconds. */
#define VIRTIO_DISK_REQUEST_TIMEOUT_TICKS 500U
/* Controller reset is bounded independently from a request deadline. */
#define VIRTIO_DISK_RESET_TIMEOUT_TICKS 100U

/* Indirect descriptors let every queue entry carry one block request. */
#define VIRTIO_DISK_WRITE_BATCH_MAX NUM
#define VIRTIO_DISK_READ_BATCH_MAX NUM
#define VIRTIO_DISK_DIRECT_WRITE_BATCH_MAX 2U

// the format of the first descriptor in a disk request.
// to be followed by two more descriptors containing
// the block, and a one-byte status.
struct virtio_blk_req {
	uint32 type; // VIRTIO_BLK_T_IN or ..._OUT
	uint32 reserved;
	uint64 sector;
};

void virtio_disk_init();
void virtio_disk_runtime_start(void);
int virtio_disk_rw(struct buf *, int);
int virtio_disk_read_batch(struct buf **, uint);
int virtio_disk_write_batch(struct buf **, uint);
int virtio_disk_durability_capability(void);
int virtio_disk_durability_barrier(void);
void virtio_disk_intr();
void virtio_disk_tick(void);

#ifdef DURABILITY_POWERCUT_TEST_PROFILE
#define VIRTIO_DURABILITY_TEST_ABI_VERSION 2U
#define VIRTIO_DURABILITY_OVERLAY_CAPACITY 640U
struct virtio_durability_test_stats {
	uint version;
	uint size;
	uint capacity;
	uint pending_blocks;
	uint last_flush_pending_before;
	uint last_flush_pending_after;
	uint64 epoch;
	uint64 cached_writes;
	uint64 overlay_reads;
	uint64 raw_writes;
	uint64 last_acknowledged_sequence;
	uint64 flush_attempts;
	uint64 successful_flushes;
	uint64 failed_flushes;
	uint64 capacity_failures;
};
/* Stable, fail-closed snapshot; callers must not already own the overlay gate. */
void virtio_disk_durability_test_stats(
	struct virtio_durability_test_stats *);
#endif

#ifdef VIRTIO_DISK_FAULT_INJECTION
#define VIRTIO_DISK_TEST_DROP_COMPLETION VIRTIO_TEST_DROP_COMPLETION
#define VIRTIO_DISK_TEST_DELAY_COMPLETION VIRTIO_TEST_DELAY_COMPLETION
#define VIRTIO_DISK_TEST_FORCE_STATUS VIRTIO_TEST_FORCE_STATUS
#define VIRTIO_DISK_TEST_DISABLE_FLUSH VIRTIO_TEST_DISABLE_FLUSH
#define VIRTIO_DISK_TEST_STALL_COMPLETION VIRTIO_TEST_STALL_COMPLETION
#define VIRTIO_DISK_TEST_REPEAT VIRTIO_TEST_REPEAT
#define VIRTIO_DISK_TEST_STUCK_RESET VIRTIO_TEST_STUCK_RESET
#define VIRTIO_DISK_TEST_FORGE_USED_INDEX VIRTIO_TEST_FORGE_USED_INDEX
#define VIRTIO_DISK_TEST_DUPLICATE_USED VIRTIO_TEST_DUPLICATE_USED
#define VIRTIO_DISK_TEST_FULL_RING_RECLAIM VIRTIO_TEST_FULL_RING_RECLAIM

/* Kernel-only fault injection; production builds expose no user control. */
int virtio_disk_test_configure(uint flags, uint delay_ticks,
			       int status, uint timeout_ticks,
			       uint after_requests);
int virtio_disk_test_read(uint blockno);
int virtio_disk_test_read_range(void);
int virtio_disk_test_flush(void);
void virtio_disk_test_stats(struct virtio_test_stats *stats);
#endif

#ifdef VIRTIO_DISK_TEST_PROFILE
struct proc;
void virtio_disk_test_bind_boot_init(struct proc *, const char *);
int virtio_disk_test_authorized(const struct proc *);
#endif

#endif // VIRTIO_H
