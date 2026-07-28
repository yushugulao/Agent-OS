/*
 * Filesystem allocator fault/reboot acceptance client.
 *
 * The runner builds this source explicitly for the profile-only kernel ABI.
 * Production kernels do not contain the syscall handler or fault owner.
 */
#include <fcntl.h>
#include <fs_allocator_test_abi.h>
#include <stdio.h>
#include <stdlib.h>
#include <syscall.h>
#include <unistd.h>

#ifndef FSALLOC_FAULT_OP
#define FSALLOC_FAULT_OP FSALLOC_OP_ALLOC
#endif
#ifndef FSALLOC_FAULT_PHASE
#define FSALLOC_FAULT_PHASE FSALLOC_PHASE_INTENT
#endif
#ifndef FSALLOC_FAULT_ACTION
#define FSALLOC_FAULT_ACTION FSALLOC_ACTION_BUSY
#endif

static const char *const operation_names[] = {
	"invalid", "alloc", "free", "ialloc", "ifree",
};
static const char *const phase_names[] = {
	"invalid", "intent", "bitmap", "owner", "refund",
};
static const char *const action_names[] = {
	"invalid", "busy", "eio", "crash",
};

static void fail(const char *message)
{
	printf("fsallocfault_ucore: FAIL %s\n", message);
	exit(1);
}

static int test_call(unsigned command, unsigned long arg0,
		     unsigned long arg1, unsigned long arg2,
		     unsigned long arg3)
{
	return syscall(SYS_fs_allocator_fault_test, command, arg0, arg1, arg2,
		       arg3, 0);
}

static void snapshot(struct fsalloc_test_snapshot *value)
{
	if (test_call(FSALLOC_TEST_SNAPSHOT, (unsigned long)value,
		      sizeof(*value), 0, 0) < 0)
		fail("profile snapshot ABI unavailable");
	if (value->version != FSALLOC_TEST_ABI_VERSION ||
	    value->size != sizeof(*value))
		fail("profile snapshot ABI mismatch");
	if (value->durability_profile != 1 ||
	    value->durability_capacity != FSALLOC_DURABILITY_OVERLAY_CAPACITY ||
	    value->durability_capacity_failures != 0)
		fail("volatile durability profile unavailable");
}

static void verify_flush_receipt(
	const char *stage, const struct fsalloc_test_snapshot *before,
	const struct fsalloc_test_snapshot *after, int evidence_marker)
{
	unsigned long long raw_delta =
		after->durability_raw_writes - before->durability_raw_writes;

	if (after->durability_capacity != before->durability_capacity ||
	    after->durability_epoch != before->durability_epoch + 1 ||
	    after->durability_flush_attempts !=
		    before->durability_flush_attempts + 1 ||
	    after->durability_successful_flushes !=
		    before->durability_successful_flushes + 1 ||
	    after->durability_failed_flushes !=
		    before->durability_failed_flushes ||
	    after->durability_capacity_failures !=
		    before->durability_capacity_failures ||
	    after->durability_pending_blocks != 0 ||
	    after->durability_last_acknowledged_sequence <
		    before->durability_last_acknowledged_sequence ||
	    (before->durability_pending_blocks != 0 &&
	     after->durability_last_acknowledged_sequence ==
		     before->durability_last_acknowledged_sequence) ||
	    after->durability_last_acknowledged_sequence !=
		    after->durability_cached_writes ||
	    after->durability_raw_writes >
		    after->durability_last_acknowledged_sequence ||
	    raw_delta < before->durability_pending_blocks)
		fail("volatile durability flush receipt");
	printf("fsallocfault_ucore: flush_receipt stage=%s abi=%u "
	       "capacity=%u epoch_before=%llu epoch_after=%llu "
	       "pending_before=%u pending_after=%u raw_writes_delta=%llu "
	       "real_flush_delta=%llu failed_flush_delta=%llu "
	       "capacity_failures=%llu\n",
	       stage, FSALLOC_DURABILITY_BACKEND_ABI_VERSION,
	       after->durability_capacity, before->durability_epoch,
	       after->durability_epoch, before->durability_pending_blocks,
	       after->durability_pending_blocks, raw_delta,
	       after->durability_successful_flushes -
		       before->durability_successful_flushes,
	       after->durability_failed_flushes -
		       before->durability_failed_flushes,
	       after->durability_capacity_failures);
	if (evidence_marker) {
		printf("fsalloc-cache: receipt_id=%s-%s-%s:%s:flush "
		       "backend_instance_id=%s-%s-%s:%s "
		       "abi_version=%u capacity_bytes=%u "
		       "durable_epoch=%llu raw_write_count=%llu "
		       "cached_write_count=%llu "
		       "flush_command_count=%llu acknowledged_flush_count=%llu "
		       "last_acknowledged_sequence=%llu "
		       "pending_before=%u pending_after=%u "
		       "pending_at_stage_end=%u powercut_after_receipt=0\n",
		       operation_names[FSALLOC_FAULT_OP],
		       phase_names[FSALLOC_FAULT_PHASE],
		       action_names[FSALLOC_FAULT_ACTION], stage,
		       operation_names[FSALLOC_FAULT_OP],
		       phase_names[FSALLOC_FAULT_PHASE],
		       action_names[FSALLOC_FAULT_ACTION], stage,
		       FSALLOC_DURABILITY_BACKEND_ABI_VERSION,
		       after->durability_capacity * 1024U,
		       after->durability_epoch,
		       after->durability_raw_writes,
		       after->durability_cached_writes,
		       after->durability_flush_attempts,
		       after->durability_successful_flushes,
		       after->durability_last_acknowledged_sequence,
		       before->durability_pending_blocks,
		       after->durability_pending_blocks,
		       after->durability_pending_blocks);
	}
}

static void flush_with_receipt(const char *stage,
			       struct fsalloc_test_snapshot *after)
{
	struct fsalloc_test_snapshot before;

	snapshot(&before);
	if (test_call(FSALLOC_TEST_FLUSH, 0, 0, 0, 0) < 0)
		fail("explicit durability flush");
	snapshot(after);
	verify_flush_receipt(stage, &before, after, 1);
}

static int exercise_operation(void)
{
	char block[1024];
	int fd;

	for (unsigned i = 0; i < sizeof(block); i++)
		block[i] = (char)(i * 17U + 3U);
	switch (FSALLOC_FAULT_OP) {
	case FSALLOC_OP_ALLOC:
		fd = open("fsalloc_block", O_CREATE | O_WRONLY | O_TRUNC);
		if (fd < 0)
			return -1;
		if (write(fd, block, sizeof(block)) != sizeof(block)) {
			(void)close(fd);
			return -1;
		}
		return close(fd);
	case FSALLOC_OP_FREE:
		return unlink("fsalloc_free");
	case FSALLOC_OP_IALLOC:
		fd = open("fsalloc_inode", O_CREATE | O_WRONLY | O_TRUNC);
		if (fd < 0)
			return -1;
		return close(fd);
	case FSALLOC_OP_IFREE:
		return unlink("fsalloc_ifree");
	default:
		return -1;
	}
}

static void verify_runtime_settlement(
	int result, const struct fsalloc_test_snapshot *before,
	const struct fsalloc_test_snapshot *after)
{
	int block_delta = (int)after->free_blocks - (int)before->free_blocks;
	int inode_delta = (int)after->free_inodes - (int)before->free_inodes;
	int account_block_delta =
		(int)after->account_blocks - (int)before->account_blocks;
	int account_inode_delta =
		(int)after->account_inodes - (int)before->account_inodes;
	int expected_result_ok;
	int expected_block = 0;
	int expected_inode = 0;
	int expected_account_block = 0;
	int expected_account_inode = 0;

	if (FSALLOC_FAULT_ACTION == FSALLOC_ACTION_BUSY) {
		expected_result_ok =
			FSALLOC_FAULT_OP == FSALLOC_OP_ALLOC &&
			FSALLOC_FAULT_PHASE != FSALLOC_PHASE_INTENT ?
				result == 0 :
			FSALLOC_FAULT_OP == FSALLOC_OP_IALLOC ? result < 0 :
			FSALLOC_FAULT_OP == FSALLOC_OP_ALLOC ? result < 0 :
			result == 0;
		if (FSALLOC_FAULT_OP == FSALLOC_OP_ALLOC) {
			expected_inode = -1;
			expected_account_inode = 1;
			if (FSALLOC_FAULT_PHASE != FSALLOC_PHASE_INTENT) {
				expected_block = -1;
				expected_account_block = 1;
			}
		} else if (FSALLOC_FAULT_OP == FSALLOC_OP_FREE) {
			expected_block = 1;
			expected_inode = 1;
			expected_account_block = -1;
			expected_account_inode = -1;
		} else if (FSALLOC_FAULT_OP == FSALLOC_OP_IFREE) {
			expected_inode = 1;
			expected_account_inode = -1;
		}
	} else {
		expected_result_ok =
			FSALLOC_FAULT_OP == FSALLOC_OP_ALLOC ||
			FSALLOC_FAULT_OP == FSALLOC_OP_IALLOC ? result < 0 :
			result == 0;
		if (FSALLOC_FAULT_OP == FSALLOC_OP_ALLOC) {
			expected_block = -1;
			expected_inode = -1;
			expected_account_block = 1;
			expected_account_inode = 1;
		} else if (FSALLOC_FAULT_OP == FSALLOC_OP_FREE) {
			expected_inode = 1;
			expected_account_inode = -1;
		} else if (FSALLOC_FAULT_OP == FSALLOC_OP_IALLOC) {
			expected_inode = -1;
			expected_account_inode = 1;
		}
	}
	if (!expected_result_ok || block_delta != expected_block ||
	    inode_delta != expected_inode ||
	    account_block_delta != expected_account_block ||
	    account_inode_delta != expected_account_inode)
		fail("runtime allocator settlement");
}

static void prepare_free_fixtures(void)
{
	char block[1024];
	int fd;

	if (FSALLOC_FAULT_OP == FSALLOC_OP_FREE) {
		for (unsigned i = 0; i < sizeof(block); i++)
			block[i] = (char)(i ^ 0x5aU);
		fd = open("fsalloc_free", O_CREATE | O_WRONLY | O_TRUNC);
		if (fd < 0 || write(fd, block, sizeof(block)) != sizeof(block) ||
		    close(fd) < 0)
			fail("prepare block-free fixture");
	} else if (FSALLOC_FAULT_OP == FSALLOC_OP_IFREE) {
		fd = open("fsalloc_ifree", O_CREATE | O_WRONLY | O_TRUNC);
		if (fd < 0 || close(fd) < 0)
			fail("prepare inode-free fixture");
	}
}

static int crash_boot_stage(void)
{
	char stage = 0;
	int fd = open("fsalloc_state", O_RDONLY);

	if (fd < 0)
		return 0;
	if (read(fd, &stage, 1) != 1 || close(fd) < 0)
		fail("read crash boot stage");
	return stage;
}

static void write_crash_boot_stage(char stage, int create)
{
	int flags = O_WRONLY;
	int fd;

	if (create)
		flags |= O_CREATE | O_TRUNC;
	fd = open("fsalloc_state", flags);
	if (fd < 0 || write(fd, &stage, 1) != 1 || close(fd) < 0)
		fail("write crash boot stage");
}

int main(void)
{
	struct fsalloc_test_snapshot before;
	struct fsalloc_test_snapshot after;
	struct fsalloc_test_snapshot flush_before;
	struct fsalloc_test_snapshot flush_after;
	const char *operation;
	const char *phase;
	const char *action;
	int result;
	int crash_stage = 0;

	if (FSALLOC_FAULT_OP < FSALLOC_OP_ALLOC ||
	    FSALLOC_FAULT_OP > FSALLOC_OP_IFREE ||
	    FSALLOC_FAULT_PHASE < FSALLOC_PHASE_INTENT ||
	    FSALLOC_FAULT_PHASE > FSALLOC_PHASE_REFUND ||
	    FSALLOC_FAULT_ACTION < FSALLOC_ACTION_BUSY ||
	    FSALLOC_FAULT_ACTION > FSALLOC_ACTION_CRASH)
		fail("invalid compile-time case");
	operation = operation_names[FSALLOC_FAULT_OP];
	phase = phase_names[FSALLOC_FAULT_PHASE];
	action = action_names[FSALLOC_FAULT_ACTION];

	crash_stage = crash_boot_stage();
	if (crash_stage == 'F') {
		flush_with_receipt("reboot", &after);
		printf("fsallocfault_ucore: case=%s phase=%s action=%s "
		       "reboot_ready=1 free_blocks=%d free_inodes=%d "
		       "account_blocks=%d account_inodes=%d\n",
		       operation, phase, action, (int)after.free_blocks,
		       (int)after.free_inodes, (int)after.account_blocks,
		       (int)after.account_inodes);
		printf("fsallocfault_ucore: case=%s phase=%s action=%s "
		       "reboot_ready=1\n", operation, phase, action);
		return 0;
	}
	if (crash_stage == 0) {
		prepare_free_fixtures();
		write_crash_boot_stage('P', 1);
		flush_with_receipt("prepare", &after);
		printf("fsallocfault_ucore: case=%s phase=%s action=%s "
		       "prepared=1\n", operation, phase, action);
		for (;;)
			sleep(100);
	}
	if (crash_stage != 'P')
		fail("invalid fault boot stage");
	/* Same inode/block and size: only payload data changes before fault. */
	write_crash_boot_stage('F', 0);
	snapshot(&before);
	if (test_call(FSALLOC_TEST_ARM, FSALLOC_FAULT_OP,
		      FSALLOC_FAULT_PHASE, FSALLOC_FAULT_ACTION, 1) < 0)
		fail("arm one-shot allocator fault");
	snapshot(&after);
	verify_flush_receipt("fault-baseline", &before, &after, 0);
	before = after;
	printf("fsallocfault_ucore: case=%s phase=%s action=%s armed=1\n",
	       operation, phase, action);
	result = exercise_operation();

	/* A CRASH hook prints its kernel checkpoint and never returns. */
	if (FSALLOC_FAULT_ACTION == FSALLOC_ACTION_CRASH)
		fail("crash checkpoint returned");
	snapshot(&after);
	if (after.hook_hits != before.hook_hits + 1 || after.armed != 0)
		fail("one-shot allocator fault receipt");
	verify_runtime_settlement(result, &before, &after);
	flush_before = after;
	if (test_call(FSALLOC_TEST_FLUSH, 0, 0, 0, 0) < 0)
		fail("fault result durability flush");
	snapshot(&flush_after);
	verify_flush_receipt("fault", &flush_before, &flush_after, 1);
	if (flush_after.free_blocks != after.free_blocks ||
	    flush_after.free_inodes != after.free_inodes ||
	    flush_after.account_blocks != after.account_blocks ||
	    flush_after.account_inodes != after.account_inodes ||
	    flush_after.hook_hits != after.hook_hits ||
	    flush_after.armed != after.armed)
		fail("durability flush changed allocator accounting");
	if (test_call(FSALLOC_TEST_DISARM, 0, 0, 0, 0) < 0)
		fail("disarm allocator fault");
	printf("fsallocfault_ucore: case=%s phase=%s action=%s result=%d "
	       "free_blocks_delta=%d free_inodes_delta=%d "
	       "account_blocks_delta=%d account_inodes_delta=%d hits=%d\n",
	       operation, phase, action, result,
	       (int)after.free_blocks - (int)before.free_blocks,
	       (int)after.free_inodes - (int)before.free_inodes,
	       (int)after.account_blocks - (int)before.account_blocks,
	       (int)after.account_inodes - (int)before.account_inodes,
	       (int)after.hook_hits - (int)before.hook_hits);
	puts("fsallocfault_ucore: runtime_verified=1");
	return 0;
}
