#ifndef PHYSICAL_PAGE_TEST_ABI_H
#define PHYSICAL_PAGE_TEST_ABI_H

#define PHYSICAL_PAGE_TEST_ABI_VERSION 2U
#define PHYSICAL_PAGE_TEST_SNAPSHOT 1U
#define PHYSICAL_PAGE_TEST_PROMISE_LIFECYCLE 2U

#define PHYSICAL_PAGE_TEST_RUN_COMPLETE 2U
#define PHYSICAL_PAGE_TEST_RECEIPT_CAP 30U
#define PHYSICAL_PAGE_TEST_POOL_AFFINE (1U << 1)
#define PHYSICAL_PAGE_TEST_ACCOUNT_ACTIVE 1U
#define PHYSICAL_PAGE_TEST_ACCOUNT_CLOSING 2U
#define PHYSICAL_PAGE_TEST_ACCOUNT_DRAINING 3U

enum physical_page_test_step {
	PHYSICAL_PAGE_STEP_KIND_ATTRIBUTES = 1,
	PHYSICAL_PAGE_STEP_SOURCE_CREATE,
	PHYSICAL_PAGE_STEP_TARGET_CREATE,
	PHYSICAL_PAGE_STEP_TRANSFER_RESERVE,
	PHYSICAL_PAGE_STEP_TRANSFER_COMMIT,
	PHYSICAL_PAGE_STEP_TRANSFER,
	PHYSICAL_PAGE_STEP_IMPORT,
	PHYSICAL_PAGE_STEP_RECONCILE,
	PHYSICAL_PAGE_STEP_SOURCE_USAGE,
	PHYSICAL_PAGE_STEP_TARGET_USAGE,
	PHYSICAL_PAGE_STEP_PROMISE_INITIAL,
	PHYSICAL_PAGE_STEP_FILL_CREATE,
	PHYSICAL_PAGE_STEP_MEMBER_ACQUIRE,
	PHYSICAL_PAGE_STEP_FIRST_RESERVE,
	PHYSICAL_PAGE_STEP_FIRST_COMMIT,
	PHYSICAL_PAGE_STEP_PENDING_RESERVE,
	PHYSICAL_PAGE_STEP_PROMISE_FULL,
	PHYSICAL_PAGE_STEP_EXTRA_ACTIVE,
	PHYSICAL_PAGE_STEP_CLOSE,
	PHYSICAL_PAGE_STEP_EXTRA_CLOSING,
	PHYSICAL_PAGE_STEP_MEMBER_RELEASE,
	PHYSICAL_PAGE_STEP_EXTRA_DRAINING,
	PHYSICAL_PAGE_STEP_PENDING_CANCEL,
	PHYSICAL_PAGE_STEP_EXTRA_AFTER_CANCEL,
	PHYSICAL_PAGE_STEP_USAGE_RELEASE,
	PHYSICAL_PAGE_STEP_PROMISE_REFUNDED,
	PHYSICAL_PAGE_STEP_REPLACEMENT_CREATE,
	PHYSICAL_PAGE_STEP_PROMISE_REPLACEMENT,
	PHYSICAL_PAGE_STEP_REPLACEMENT_CLOSE,
	PHYSICAL_PAGE_STEP_PROMISE_FINAL,
};

struct physical_page_test_receipt {
	unsigned long long step;
	long long result;
	unsigned long long value0;
	unsigned long long value1;
};

struct physical_page_test_header {
	unsigned long long version;
	unsigned long long size;
	unsigned long long command;
};

struct physical_page_account_snapshot {
	struct physical_page_test_header header;
	unsigned long long account_slot;
	unsigned long long account_generation;
	unsigned long long account_state;
	unsigned long long charge_grants;
	unsigned long long ordinary_usage;
	unsigned long long ordinary_pending;
	unsigned long long ordinary_limit;
	unsigned long long reserved_usage;
	unsigned long long reserved_pending;
	unsigned long long reserved_limit;
	unsigned long long reserve_free;
	unsigned long long reserve_total;
	unsigned long long reserved;
};

struct physical_page_lifecycle_report {
	struct physical_page_test_header header;
	unsigned long long run_state;
	unsigned long long receipt_count;
	struct physical_page_test_receipt receipts[PHYSICAL_PAGE_TEST_RECEIPT_CAP];
};

#endif
