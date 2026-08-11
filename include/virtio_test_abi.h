#ifndef VIRTIO_TEST_ABI_H
#define VIRTIO_TEST_ABI_H

#define VIRTIO_TEST_ABI_VERSION 4U

enum virtio_test_command {
	VIRTIO_TEST_CONFIGURE = 1,
	VIRTIO_TEST_READ = 2,
	VIRTIO_TEST_FLUSH = 3,
	VIRTIO_TEST_STATS = 4,
	VIRTIO_TEST_READ_RANGE = 5,
};

enum virtio_test_fault {
	VIRTIO_TEST_DROP_COMPLETION = 0x01U,
	VIRTIO_TEST_DELAY_COMPLETION = 0x02U,
	VIRTIO_TEST_FORCE_STATUS = 0x04U,
	VIRTIO_TEST_DISABLE_FLUSH = 0x08U,
	VIRTIO_TEST_STALL_COMPLETION = 0x10U,
	VIRTIO_TEST_REPEAT = 0x20U,
	VIRTIO_TEST_STUCK_RESET = 0x40U,
	VIRTIO_TEST_FORGE_USED_INDEX = 0x80U,
	VIRTIO_TEST_DUPLICATE_USED = 0x100U,
	VIRTIO_TEST_FULL_RING_RECLAIM = 0x200U,
};

enum virtio_test_result {
	VIRTIO_TEST_OK = 0,
	VIRTIO_TEST_IOERR = -1,
	VIRTIO_TEST_UNSUPPORTED = -2,
	VIRTIO_TEST_TIMEOUT = -3,
	VIRTIO_TEST_OFFLINE = -4,
	VIRTIO_TEST_BUSY = -5,
	VIRTIO_TEST_REJECTED_RANGE = -6,
};

#define VIRTIO_TEST_STATUS_IOERR 1U
#define VIRTIO_TEST_STATUS_UNSUPPORTED 2U
#define VIRTIO_TEST_TYPE_READ 0U
#define VIRTIO_TEST_TYPE_WRITE 1U
#define VIRTIO_TEST_TYPE_FLUSH 4U

struct virtio_test_stats {
	unsigned int version;
	unsigned int size;
	unsigned long long submits;
	unsigned long long completions;
	unsigned long long descriptor_waits;
	unsigned long long timer_recoveries;
	unsigned long long delayed_completions;
	unsigned long long resets;
	unsigned long long timeout_results;
	unsigned long long io_errors;
	unsigned long long unsupported_errors;
	unsigned long long offline_errors;
	unsigned long long rejected_requests;
	unsigned long long range_rejections;
	unsigned long long inflight;
	unsigned long long max_inflight;
	unsigned long long reset_recoveries;
	unsigned long long reset_offline;
	unsigned long long invalid_used_entries;
	unsigned long long used_budget_resets;
	unsigned long long duplicate_used_injections;
	unsigned long long descriptor_reclaims;
	unsigned long long max_used_batch;
	unsigned long long last_request_id;
	unsigned long long last_submit_tick;
	unsigned long long last_complete_tick;
	unsigned int last_request_type;
	int last_result;
};

#endif
