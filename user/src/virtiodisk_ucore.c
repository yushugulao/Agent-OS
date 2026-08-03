#include <stdio.h>
#include <stdlib.h>
#include <syscall.h>
#include <unistd.h>
#include <virtio_test_abi.h>

#define TEST_BLOCK 1
#define PRESSURE_THREADS 4
#define FULL_RING_THREADS 4

static volatile int fast_result = 99;
static volatile int fast_done;
static volatile int pressure_go;
static volatile int pressure_result[PRESSURE_THREADS];
static volatile int full_ring_done[FULL_RING_THREADS];
static volatile int full_ring_result[FULL_RING_THREADS];

static void check(int condition, const char *message)
{
	if (condition)
		return;
	printf("virtiodisk_ucore: FAIL %s\n", message);
	exit(1);
}

static int virtio_test(int command, unsigned long a0, unsigned long a1,
		       unsigned long a2, unsigned long a3, unsigned long a4)
{
	return syscall(SYS_virtio_disk_test, command, a0, a1, a2, a3, a4);
}

static void configure(unsigned flags, unsigned delay, unsigned status,
		      unsigned timeout, unsigned after)
{
	check(virtio_test(VIRTIO_TEST_CONFIGURE, flags, delay, status,
			  timeout, after) == 0,
	      "configure fault injection");
}

static struct virtio_test_stats stats(void)
{
	struct virtio_test_stats value;
	check(virtio_test(VIRTIO_TEST_STATS, (unsigned long)&value, 0, 0, 0,
			  0) == 0,
	      "read driver stats");
	check(value.version == VIRTIO_TEST_ABI_VERSION &&
		      value.size == sizeof(value),
	      "stats ABI");
	return value;
}

static void trace_request(const char *test_case,
			  const struct virtio_test_stats *value,
			  unsigned expected_type)
{
	check(value->last_request_id != 0 &&
	      value->last_request_type == expected_type &&
	      value->last_complete_tick >= value->last_submit_tick,
	      "driver request trace identity");
	printf("virtiodisk_ucore: request case=%s id=%p type=%d submit=%p complete=%p result=%d\n",
	       test_case, (uint64)value->last_request_id,
	       (int)value->last_request_type,
	       value->last_submit_tick, value->last_complete_tick,
	       value->last_result);
}

static int read_probe(void)
{
	return virtio_test(VIRTIO_TEST_READ, TEST_BLOCK, 0, 0, 0, 0);
}

static void delayed_peer(void *arg)
{
	(void)arg;
	sleep(2);
	fast_result = read_probe();
	fast_done = 1;
	exit(0);
}

static void pressure_reader(void *arg)
{
	int slot = (int)(long)arg;
	while (!pressure_go)
		sched_yield();
	pressure_result[slot] = read_probe();
	exit(0);
}

static void full_ring_worker(void *arg)
{
	int slot = (int)(long)arg;

	full_ring_result[slot] = slot == 2 ?
		virtio_test(VIRTIO_TEST_FLUSH, 0, 0, 0, 0, 0) : read_probe();
	full_ring_done[slot] = 1;
	exit(0);
}

static struct virtio_test_stats wait_for_stats(unsigned long long inflight,
					       int need_descriptor_wait)
{
	struct virtio_test_stats value;

	for (int attempt = 0; attempt < 2000; attempt++) {
		value = stats();
		if (value.inflight >= inflight &&
		    (!need_descriptor_wait || value.descriptor_waits != 0))
			return value;
		sched_yield();
	}
	check(0, "wait for driver concurrency state");
	return stats();
}

static void test_lost_interrupt(void)
{
	struct virtio_test_stats value;
	configure(VIRTIO_TEST_DROP_COMPLETION, 0, 0, 50, 0);
	check(read_probe() == VIRTIO_TEST_OK,
	      "timer recovers dropped completion interrupt");
	value = stats();
	check(value.timer_recoveries == 1 && value.resets == 0 &&
		      value.completions == 1,
	      "lost interrupt recovery counters");
	trace_request("lost-irq", &value, VIRTIO_TEST_TYPE_READ);
	puts("virtiodisk_ucore: lost-irq passed");
}

static void test_delayed_progress(void)
{
	struct virtio_test_stats value;
	int tid;
	fast_result = 99;
	fast_done = 0;
	tid = thread_create(delayed_peer, 0);
	check(tid > 0, "create delayed peer");
	configure(VIRTIO_TEST_DELAY_COMPLETION, 20, 0, 80, 0);
	check(read_probe() == VIRTIO_TEST_OK, "delayed request completes");
	check(waittid(tid) == 0 && fast_done &&
		      fast_result == VIRTIO_TEST_OK,
	      "other thread progresses during delayed I/O");
	value = stats();
	check(value.delayed_completions == 1 && value.max_inflight >= 2,
	      "delayed request overlaps peer request");
	trace_request("delayed-progress", &value, VIRTIO_TEST_TYPE_READ);
	puts("virtiodisk_ucore: delayed-progress passed");
}

static void test_descriptor_pressure(void)
{
	struct virtio_test_stats value;
	int tids[PRESSURE_THREADS];
	pressure_go = 0;
	configure(VIRTIO_TEST_DELAY_COMPLETION | VIRTIO_TEST_REPEAT,
		  20, 0, 100, 0);
	for (int i = 0; i < PRESSURE_THREADS; i++) {
		pressure_result[i] = 99;
		tids[i] = thread_create(pressure_reader, (void *)(long)i);
		check(tids[i] > 0, "create descriptor pressure reader");
	}
	pressure_go = 1;
	for (int i = 0; i < PRESSURE_THREADS; i++) {
		check(waittid(tids[i]) == 0, "join descriptor pressure reader");
		check(pressure_result[i] == VIRTIO_TEST_OK,
		      "descriptor pressure read succeeds");
	}
	value = stats();
	check(value.descriptor_waits > 0 && value.max_inflight >= 2 &&
		      value.delayed_completions == PRESSURE_THREADS,
	      "descriptor wait queue absorbs pressure");
	trace_request("descriptor-pressure", &value, VIRTIO_TEST_TYPE_READ);
	puts("virtiodisk_ucore: descriptor-pressure passed");
}

static void test_full_ring_reclaim(void)
{
	struct virtio_test_stats value;
	int64 short_pair_deadline;
	int tids[FULL_RING_THREADS];

	configure(VIRTIO_TEST_FULL_RING_RECLAIM, 20, 0, 200, 0);
	for (int i = 0; i < FULL_RING_THREADS; i++) {
		full_ring_done[i] = 0;
		full_ring_result[i] = 99;
		tids[i] = -1;
	}
	/* read3 + read3 + flush2 consumes the complete eight-entry ring. */
	for (int i = 0; i < 3; i++) {
		tids[i] = thread_create(full_ring_worker, (void *)(long)i);
		check(tids[i] > 0, "create full-ring owner");
		(void)wait_for_stats(i + 1, 0);
	}
	tids[3] = thread_create(full_ring_worker, (void *)(long)3);
	check(tids[3] > 0, "create full-ring descriptor waiter");
	(void)wait_for_stats(3, 1);
	short_pair_deadline = get_mtime() + 2000;
	for (;;) {
		value = stats();
		if ((full_ring_done[0] && full_ring_done[3]) ||
		    full_ring_done[1] || full_ring_done[2] ||
		    value.completions > 2 || get_mtime() >= short_pair_deadline)
			break;
		sched_yield();
	}
	value = stats();
	check(full_ring_done[0] && full_ring_done[3] &&
	      !full_ring_done[1] && !full_ring_done[2],
	      "head reclamation wakes waiter before long peers");
	check(value.completions == 2 && value.descriptor_reclaims >= 2,
	      "short owner and descriptor waiter publish ordered receipts");
	for (int i = 0; i < FULL_RING_THREADS; i++) {
		check(waittid(tids[i]) == 0, "join full-ring request");
		check(full_ring_result[i] == VIRTIO_TEST_OK,
		      "full-ring request succeeds");
	}
	value = stats();
	check(value.descriptor_waits > 0 && value.max_inflight >= 3 &&
	      value.delayed_completions == 3 && value.resets == 0 &&
	      value.descriptor_reclaims >= FULL_RING_THREADS,
	      "descriptor ownership is reclaimed only after result consumption");
	puts("virtiodisk_ucore: full-ring-reclaim passed");
}

static void test_status_errors(void)
{
	struct virtio_test_stats value;
	configure(VIRTIO_TEST_FORCE_STATUS, 0, VIRTIO_TEST_STATUS_IOERR,
		  50, 0);
	check(read_probe() == VIRTIO_TEST_IOERR, "IOERR reaches caller");
	value = stats();
	check(value.io_errors == 1, "IOERR accounted once");

	configure(VIRTIO_TEST_FORCE_STATUS, 0,
		  VIRTIO_TEST_STATUS_UNSUPPORTED, 50, 0);
	check(read_probe() == VIRTIO_TEST_UNSUPPORTED,
	      "UNSUPPORTED reaches caller");
	value = stats();
	check(value.unsupported_errors == 1,
	      "UNSUPPORTED accounted once");
	trace_request("status-errors", &value, VIRTIO_TEST_TYPE_READ);
	puts("virtiodisk_ucore: status-errors passed");
}

static void test_range_rejection(void)
{
	struct virtio_test_stats value;
	unsigned long args[5];
	int result;

	for (int slot = 0; slot < 5; slot++) {
		for (int i = 0; i < 5; i++)
			args[i] = i == slot;
		check(virtio_test(VIRTIO_TEST_READ_RANGE, args[0], args[1],
				  args[2], args[3], args[4]) == -1,
		      "range test command rejects every unexpected argument");
	}
	configure(0, 0, 0, 50, 0);
	result = virtio_test(VIRTIO_TEST_READ_RANGE, 0, 0, 0, 0, 0);
	check(result == VIRTIO_TEST_REJECTED_RANGE,
	      "out-of-range request has a distinct result");
	value = stats();
	check(value.rejected_requests == 1 && value.range_rejections == 1 &&
		      value.submits == 0 && value.completions == 0 &&
		      value.io_errors == 0 && value.inflight == 0 &&
		      value.last_request_id != 0 &&
		      value.last_request_type == VIRTIO_TEST_TYPE_READ &&
		      value.last_complete_tick == value.last_submit_tick &&
		      value.last_result == VIRTIO_TEST_REJECTED_RANGE,
	      "range preflight records rejection without device IOERR");
	printf("virtiodisk_ucore: range-rejection id=%p rejected=%p submits=%p result=%d\n",
	       (uint64)value.last_request_id, value.range_rejections,
	       value.submits, value.last_result);
	puts("virtiodisk_ucore: range-rejection passed");
}

static void test_flush_accounting(void)
{
	struct io_policy_info before, after;
	struct virtio_test_stats value;

	configure(0, 0, 0, 50, 0);
	check(io_policy_info(&before) == 0, "read pre-FLUSH I/O accounting");
	check(virtio_test(VIRTIO_TEST_FLUSH, 0, 0, 0, 0, 0) ==
		      VIRTIO_TEST_OK,
	      "supported FLUSH reaches device");
	check(io_policy_info(&after) == 0, "read post-FLUSH I/O accounting");
	check(after.physical_flushes > before.physical_flushes &&
		      after.failed_transfers == before.failed_transfers,
	      "successful FLUSH is accounted separately");
	value = stats();
	check(value.submits == 1 && value.completions == 1 &&
		      value.last_result == VIRTIO_TEST_OK,
	      "successful FLUSH completion is traced");
	trace_request("flush-accounting", &value, VIRTIO_TEST_TYPE_FLUSH);
	puts("virtiodisk_ucore: flush-accounting passed");
}

static void test_flush_disabled(void)
{
	struct io_policy_info before, after;
	struct virtio_test_stats value;

	configure(VIRTIO_TEST_DISABLE_FLUSH, 0, 0, 50, 0);
	check(io_policy_info(&before) == 0,
	      "read pre-failed-FLUSH I/O accounting");
	check(virtio_test(VIRTIO_TEST_FLUSH, 0, 0, 0, 0, 0) ==
		      VIRTIO_TEST_UNSUPPORTED,
	      "disabled FLUSH fails closed");
	check(io_policy_info(&after) == 0,
	      "read post-failed-FLUSH I/O accounting");
	check(after.physical_flushes == before.physical_flushes &&
		      after.failed_transfers == before.failed_transfers,
	      "unsupported FLUSH stays outside physical I/O accounting");
	value = stats();
	check(value.unsupported_errors == 1 && value.rejected_requests == 1 &&
		      value.submits == 0,
	      "unsupported durability is not submitted");
	trace_request("flush-disabled", &value, VIRTIO_TEST_TYPE_FLUSH);
	puts("virtiodisk_ucore: flush-disabled passed");
}

static void test_timeout_reset(void)
{
	struct virtio_test_stats value;
	int tids[PRESSURE_THREADS];
	int timeouts = 0;
	int completed = 0;
	pressure_go = 0;
	configure(VIRTIO_TEST_STALL_COMPLETION | VIRTIO_TEST_REPEAT,
		  0, 0, 10, 0);
	for (int i = 0; i < PRESSURE_THREADS; i++) {
		pressure_result[i] = 99;
		tids[i] = thread_create(pressure_reader, (void *)(long)i);
		check(tids[i] > 0, "create reset waiter");
	}
	pressure_go = 1;
	for (int i = 0; i < PRESSURE_THREADS; i++) {
		check(waittid(tids[i]) == 0, "join reset waiter");
		timeouts += pressure_result[i] == VIRTIO_TEST_TIMEOUT;
		completed += pressure_result[i] == VIRTIO_TEST_OK;
	}
	check(timeouts >= 2 && completed >= 1,
	      "reset wakes active waiters and resumes descriptor waiters");
	check(read_probe() == VIRTIO_TEST_OK,
	      "reinitialized queue accepts a later request");
	value = stats();
	check(value.resets == 1 && value.timeout_results == timeouts &&
		      value.reset_recoveries == 1 && value.reset_offline == 0 &&
		      value.offline_errors == 0 && value.inflight == 0 &&
		      value.descriptor_waits > 0,
	      "timeout reset settles every request");
	trace_request("timeout-reset", &value, VIRTIO_TEST_TYPE_READ);
	puts("virtiodisk_ucore: timeout-reset passed");
}

static void test_used_ring_validation(void)
{
	struct virtio_test_stats value;
	int64 recovery_deadline;

	configure(VIRTIO_TEST_FORGE_USED_INDEX, 0, 0, 50, 0);
	check(read_probe() == VIRTIO_TEST_IOERR,
	      "forged used index fails the active request");
	value = stats();
	check(value.resets == 1 && value.used_budget_resets == 1 &&
	      value.reset_recoveries == 1 && value.max_used_batch <= 8,
	      "forged used index is rejected before an unbounded scan");
	trace_request("forged-index", &value, VIRTIO_TEST_TYPE_READ);
	puts("virtiodisk_ucore: forged-used-index passed");

	configure(VIRTIO_TEST_DUPLICATE_USED, 0, 0, 50, 0);
	check(read_probe() == VIRTIO_TEST_OK,
	      "first completion remains attributable before duplicate reset");
	recovery_deadline = get_mtime() + 1000;
	do {
		value = stats();
		if (value.reset_recoveries == 1)
			break;
		sched_yield();
	} while (get_mtime() < recovery_deadline);
	check(value.resets == 1 && value.invalid_used_entries == 1 &&
	      value.duplicate_used_injections == 1 &&
	      value.reset_recoveries == 1 && value.max_used_batch <= 8,
	      "duplicate used entry is rejected and resets the queue");
	trace_request("duplicate-used", &value, VIRTIO_TEST_TYPE_READ);
	puts("virtiodisk_ucore: duplicate-used passed");
}

static void test_stuck_reset(void)
{
	struct virtio_test_stats value;

	configure(VIRTIO_TEST_STALL_COMPLETION | VIRTIO_TEST_STUCK_RESET,
		  0, 0, 10, 0);
	check(read_probe() == VIRTIO_TEST_TIMEOUT,
	      "stuck reset releases caller at reset deadline");
	value = stats();
	check(value.resets == 1 && value.reset_recoveries == 0 &&
		      value.reset_offline == 1 && value.timeout_results == 1 &&
		      value.inflight == 0,
	      "stuck reset quarantines DMA and settles request");
	trace_request("stuck-reset", &value, VIRTIO_TEST_TYPE_READ);
	check(read_probe() == VIRTIO_TEST_OFFLINE,
	      "offline error reaches caller after stuck reset");
	puts("virtiodisk_ucore: stuck-reset passed");
}

int main(void)
{
	test_lost_interrupt();
	test_delayed_progress();
	test_descriptor_pressure();
	test_full_ring_reclaim();
	test_status_errors();
	test_range_rejection();
	test_flush_accounting();
	test_flush_disabled();
	test_timeout_reset();
	test_used_ring_validation();
	test_stuck_reset();
	puts("virtiodisk_ucore: parent passed");
	return 0;
}
