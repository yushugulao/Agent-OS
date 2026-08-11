#ifndef WAIT_ATOMIC_TEST_ABI_H
#define WAIT_ATOMIC_TEST_ABI_H

#define WAIT_ATOMIC_TEST_ABI_VERSION 1U

#define WAIT_ATOMIC_TEST_COMMAND_ARM   1U
#define WAIT_ATOMIC_TEST_COMMAND_QUERY 2U
#define WAIT_ATOMIC_TEST_COMMAND_DEADLINE_OBSERVE 3U

#define WAIT_ATOMIC_TEST_AGENT_WAIT 1U
#define WAIT_ATOMIC_TEST_TEARDOWN   2U
#define WAIT_ATOMIC_TEST_THREAD_DEADLINE 3U

#define WAIT_ATOMIC_TEST_DEADLINE_FINITE_INFINITE 1U
#define WAIT_ATOMIC_TEST_DEADLINE_INFINITE_ONLY   2U
#define WAIT_ATOMIC_TEST_DEADLINE_IDLE_FIRST      3U
#define WAIT_ATOMIC_TEST_DEADLINE_DISTINCT        4U
#define WAIT_ATOMIC_TEST_DEADLINE_LONG_ONLY       5U
#define WAIT_ATOMIC_TEST_DEADLINE_COMPLETE        6U

#define WAIT_ATOMIC_TEST_RETRY 1

#define WAIT_ATOMIC_TEST_F_HOOK_FIRED       (1ULL << 0)
#define WAIT_ATOMIC_TEST_F_WAITER_EMPTY     (1ULL << 1)
#define WAIT_ATOMIC_TEST_F_EVENT_PUBLISHED  (1ULL << 2)
#define WAIT_ATOMIC_TEST_F_SIBLING_OBSERVED (1ULL << 3)
#define WAIT_ATOMIC_TEST_F_SIBLING_EXITED   (1ULL << 4)
#define WAIT_ATOMIC_TEST_F_COMPLETE         (1ULL << 5)
#define WAIT_ATOMIC_TEST_F_FINITE_INFINITE  (1ULL << 6)
#define WAIT_ATOMIC_TEST_F_DISTINCT_KEYS    (1ULL << 7)
#define WAIT_ATOMIC_TEST_F_INFINITE_KEPT    (1ULL << 8)
#define WAIT_ATOMIC_TEST_F_LONG_KEPT        (1ULL << 9)
#define WAIT_ATOMIC_TEST_F_TIMEOUTS         (1ULL << 10)
#define WAIT_ATOMIC_TEST_F_LOOP_AGGREGATE   (1ULL << 11)
#define WAIT_ATOMIC_TEST_F_RUNTIME_CLEARED  (1ULL << 12)
#define WAIT_ATOMIC_TEST_F_SLOT_REUSED      (1ULL << 13)

#define WAIT_ATOMIC_TEST_AGENT_FLAGS \
	(WAIT_ATOMIC_TEST_F_HOOK_FIRED | \
	 WAIT_ATOMIC_TEST_F_WAITER_EMPTY | \
	 WAIT_ATOMIC_TEST_F_EVENT_PUBLISHED | \
	 WAIT_ATOMIC_TEST_F_COMPLETE)

#define WAIT_ATOMIC_TEST_TEARDOWN_FLAGS \
	(WAIT_ATOMIC_TEST_F_HOOK_FIRED | \
	 WAIT_ATOMIC_TEST_F_WAITER_EMPTY | \
	 WAIT_ATOMIC_TEST_F_SIBLING_OBSERVED | \
	 WAIT_ATOMIC_TEST_F_SIBLING_EXITED | \
	 WAIT_ATOMIC_TEST_F_COMPLETE)

#define WAIT_ATOMIC_TEST_DEADLINE_FLAGS \
	(WAIT_ATOMIC_TEST_F_FINITE_INFINITE | \
	 WAIT_ATOMIC_TEST_F_DISTINCT_KEYS | \
	 WAIT_ATOMIC_TEST_F_INFINITE_KEPT | \
	 WAIT_ATOMIC_TEST_F_LONG_KEPT | \
	 WAIT_ATOMIC_TEST_F_TIMEOUTS | \
	 WAIT_ATOMIC_TEST_F_LOOP_AGGREGATE | \
	 WAIT_ATOMIC_TEST_F_RUNTIME_CLEARED | \
	 WAIT_ATOMIC_TEST_F_SLOT_REUSED | \
	 WAIT_ATOMIC_TEST_F_COMPLETE)

struct wait_atomic_test_receipt {
	unsigned int version;
	unsigned int size;
	unsigned int operation;
	int target_pid;
	unsigned long long sequence;
	unsigned long long flags;
};

struct wait_atomic_deadline_snapshot {
	unsigned int version;
	unsigned int size;
	unsigned int phase;
	int target_pid;
	int loop_state;
	unsigned int waiting_threads;
	unsigned int finite_threads;
	unsigned int infinite_threads;
	unsigned int keyed_threads;
	unsigned int reused_threads;
	int earliest_tid;
	int latest_tid;
	unsigned long long earliest_deadline;
	unsigned long long latest_deadline;
	unsigned long long timeout_count;
};

#endif
