#include <fcntl.h>
#include <io_policy.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define FSEPOCH_CASE_DIRTY 1
#define FSEPOCH_CASE_INFLIGHT 2
#define FSEPOCH_CASE_DURABLE 3

#ifndef FSEPOCH_CASE
#define FSEPOCH_CASE FSEPOCH_CASE_DURABLE
#endif

#if FSEPOCH_CASE < FSEPOCH_CASE_DIRTY || FSEPOCH_CASE > FSEPOCH_CASE_DURABLE
#error "invalid filesystem epoch regression case"
#endif

#define BLOCK_SIZE 1024U
#define BATCH_BLOCKS 8U
#define BATCH_BYTES (BATCH_BLOCKS * BLOCK_SIZE)
#define CREATED_BYTES BLOCK_SIZE
#define MIN_COMMIT_WRITES (BATCH_BLOCKS + 3U)
#define MAX_COMMIT_WRITES (BATCH_BLOCKS + 8U)
#define BOOT_IMAGE "workflow_teardown_race_ucore"
#define PUBLIC_IMAGE "wf_public"

static const char batch_path[] = "fsepoch_batch";
static const char created_path[] = "fsepoch_new";
static const char state_path[] = "fsepoch_state";
static unsigned char batch_old[BATCH_BYTES];
static unsigned char batch_new[BATCH_BYTES];
static unsigned char created_new[CREATED_BYTES];
static unsigned char scratch[BATCH_BYTES];

static const char *case_name(void)
{
	if (FSEPOCH_CASE == FSEPOCH_CASE_DIRTY)
		return "dirty";
	if (FSEPOCH_CASE == FSEPOCH_CASE_INFLIGHT)
		return "inflight";
	return "durable";
}

static void fail(const char *message)
{
	printf("fsepoch_ucore: FAIL case=%s reason=%s\n", case_name(),
	       message);
	exit(1);
}

static void hang(void)
{
	for (;;)
		asm volatile("" ::: "memory");
}

static void fill_pattern(unsigned char *buffer, unsigned size,
			 unsigned seed)
{
	for (unsigned i = 0; i < size; i++)
		buffer[i] = (unsigned char)(seed + i * 37U +
					    (i / BLOCK_SIZE) * 17U);
}

static int bytes_equal(const unsigned char *left,
		       const unsigned char *right, unsigned size)
{
	for (unsigned i = 0; i < size; i++)
		if (left[i] != right[i])
			return 0;
	return 1;
}

static void write_exact(int fd, const unsigned char *buffer, unsigned size,
			const char *message)
{
	unsigned written = 0;

	while (written != size) {
		int result = write(fd, buffer + written, size - written);

		if (result <= 0 || (unsigned)result > size - written)
			fail(message);
		written += (unsigned)result;
	}
}

static void read_exact_file(const char *path, unsigned char *buffer,
			    unsigned size, const char *message)
{
	unsigned read_bytes = 0;
	unsigned char extra;
	int fd = open(path, O_RDONLY);

	if (fd < 0)
		fail(message);
	while (read_bytes != size) {
		int result = read(fd, buffer + read_bytes, size - read_bytes);

		if (result <= 0 || (unsigned)result > size - read_bytes)
			fail(message);
		read_bytes += (unsigned)result;
	}
	if (read(fd, &extra, 1) != 0 || close(fd) < 0)
		fail(message);
}

static void expect_file(const char *path, const unsigned char *expected,
			unsigned size, const char *message)
{
	read_exact_file(path, scratch, size, message);
	if (!bytes_equal(scratch, expected, size))
		fail(message);
}

static void expect_absent(const char *path, const char *message)
{
	int fd = open(path, O_RDONLY);

	if (fd >= 0) {
		(void)close(fd);
		fail(message);
	}
}

static int read_stage(void)
{
	unsigned char stage;
	int fd = open(state_path, O_RDONLY);

	if (fd < 0)
		return 0;
	if (read(fd, &stage, 1) != 1 || close(fd) < 0)
		fail("read-stage");
	return stage;
}

static void write_stage(unsigned char stage, int create)
{
	int flags = O_WRONLY;
	int fd;

	if (create)
		flags |= O_CREATE;
	fd = open(state_path, flags);
	if (fd < 0 || write(fd, &stage, 1) != 1 || fsync(fd) < 0 ||
	    close(fd) < 0)
		fail("write-stage");
}

static struct io_policy_info io_snapshot(void)
{
	struct io_policy_info value;

	if (io_policy_info(&value) < 0 || value.version != IO_POLICY_VERSION ||
	    value.struct_size != sizeof(value))
		fail("io-snapshot");
	return value;
}

static void prepare_baseline(void)
{
	int fd = open(batch_path, O_CREATE | O_WRONLY | O_TRUNC);

	if (fd < 0)
		fail("prepare-open");
	write_exact(fd, batch_old, sizeof(batch_old), "prepare-write");
	if (fsync(fd) < 0 || close(fd) < 0)
		fail("prepare-fsync");
	expect_absent(created_path, "prepare-created-present");
	write_stage('P', 1);
	if (sync() < 0)
		fail("prepare-sync");
	printf("fsepoch_ucore: prepared case=%s blocks=%u\n", case_name(),
	       BATCH_BLOCKS);
	hang();
}

static void warm_batch_cache(void)
{
	expect_file(batch_path, batch_old, sizeof(batch_old), "warm-baseline");
}

static void stage_group(int *batch_fd, int *created_fd)
{
	*batch_fd = open(batch_path, O_WRONLY);
	if (*batch_fd < 0)
		fail("batch-open");
	write_exact(*batch_fd, batch_new, sizeof(batch_new), "batch-write");
	*created_fd = open(created_path, O_CREATE | O_WRONLY);
	if (*created_fd < 0)
		fail("create-open");
	write_exact(*created_fd, created_new, sizeof(created_new),
		    "create-write");
}

static void verify_commit_receipt(const char *kind,
				  const struct io_policy_info *before,
				  const struct io_policy_info *after)
{
	unsigned long long writes = after->physical_writes -
		before->physical_writes;
	unsigned long long flushes = after->physical_flushes -
		before->physical_flushes;
	unsigned long long failures = after->failed_transfers -
		before->failed_transfers;

	/* PREPARE、INODE 和 NAMESPACE 各有一道顺序屏障。 */
	if (after->physical_writes < before->physical_writes ||
	    after->physical_flushes < before->physical_flushes ||
	    after->failed_transfers < before->failed_transfers ||
	    writes < MIN_COMMIT_WRITES || writes > MAX_COMMIT_WRITES ||
	    flushes != 3U || failures != 0)
		fail("ordered-group-receipt");
	printf("fsepoch_ucore: %s case=%s payload_blocks=%u writes=%llu "
	       "flushes=%llu failed=%llu\n", kind, case_name(),
	       BATCH_BLOCKS + 1U, writes, flushes, failures);
}

static void retry_and_commit(void)
{
	struct io_policy_info before = io_snapshot();
	struct io_policy_info after;
	int batch_fd;
	int created_fd;

	stage_group(&batch_fd, &created_fd);
	printf("fsepoch_ucore: retry_fsync_enter case=%s\n", case_name());
	if (fsync(created_fd) < 0)
		fail("retry-fsync");
	after = io_snapshot();
	verify_commit_receipt("retry_receipt", &before, &after);
	if (close(created_fd) < 0 || close(batch_fd) < 0)
		fail("retry-close");
	expect_file(batch_path, batch_new, sizeof(batch_new),
		    "retry-batch-content");
	expect_file(created_path, created_new, sizeof(created_new),
		    "retry-created-content");
}

static void run_fault_boot(void)
{
	struct io_policy_info before;
	struct io_policy_info after;
	int batch_fd;
	int created_fd;

	warm_batch_cache();
	expect_absent(created_path, "fault-created-present");
	write_stage('R', 0);
	before = io_snapshot();
	stage_group(&batch_fd, &created_fd);
	if (FSEPOCH_CASE == FSEPOCH_CASE_DIRTY) {
		puts("fsepoch_ucore: powercut_window case=dirty point=before_fsync");
		hang();
	}
	if (FSEPOCH_CASE == FSEPOCH_CASE_INFLIGHT)
		puts("fsepoch_ucore: powercut_window case=inflight point=fsync_enter");
	else
		puts("fsepoch_ucore: commit_fsync_enter case=durable");
	if (fsync(created_fd) < 0)
		fail("fault-fsync");
	after = io_snapshot();
	verify_commit_receipt("commit_receipt", &before, &after);
	if (FSEPOCH_CASE == FSEPOCH_CASE_INFLIGHT) {
		puts("fsepoch_ucore: fsync_returned case=inflight");
		hang();
	}
	puts("fsepoch_ucore: powercut_window case=durable point=after_fsync");
	hang();
}

static void verify_inflight_prefix(void)
{
	unsigned old_blocks = 0;
	unsigned new_blocks = 0;

	read_exact_file(batch_path, scratch, sizeof(scratch),
			"inflight-read");
	for (unsigned block = 0; block < BATCH_BLOCKS; block++) {
		unsigned offset = block * BLOCK_SIZE;

		if (bytes_equal(scratch + offset, batch_old + offset,
				BLOCK_SIZE))
			old_blocks++;
		else if (bytes_equal(scratch + offset, batch_new + offset,
				     BLOCK_SIZE))
			new_blocks++;
		else
			fail("inflight-torn-block");
	}
	if (old_blocks == 0 || new_blocks == 0)
		fail("inflight-window-not-partial");
	printf("fsepoch_ucore: inflight_recovery old_blocks=%u new_blocks=%u\n",
	       old_blocks, new_blocks);
}

static void run_recovery_boot(void)
{
	if (FSEPOCH_CASE == FSEPOCH_CASE_DIRTY) {
		expect_file(batch_path, batch_old, sizeof(batch_old),
			    "dirty-old-content");
		expect_absent(created_path, "dirty-created-visible");
		retry_and_commit();
	} else if (FSEPOCH_CASE == FSEPOCH_CASE_INFLIGHT) {
		verify_inflight_prefix();
		expect_absent(created_path, "inflight-created-visible");
		retry_and_commit();
	} else {
		struct io_policy_info before;
		struct io_policy_info after;
		int fd;

		expect_file(batch_path, batch_new, sizeof(batch_new),
			    "durable-batch-content");
		expect_file(created_path, created_new, sizeof(created_new),
			    "durable-created-content");
		fd = open(batch_path, O_RDONLY);
		if (fd < 0)
			fail("durable-noop-open");
		before = io_snapshot();
		puts("fsepoch_ucore: durable_noop_fsync_enter case=durable");
		if (fsync(fd) < 0)
			fail("durable-noop-fsync");
		after = io_snapshot();
		if (after.physical_writes != before.physical_writes ||
		    after.physical_flushes != before.physical_flushes ||
		    close(fd) < 0)
			fail("durable-noop-io");
		puts("fsepoch_ucore: durable_recovery noop_fsync_io=0");
	}
	write_stage('D', 0);
	printf("fsepoch_ucore: retry_durable_checkpoint case=%s\n",
	       case_name());
	hang();
}

static void run_final_boot(void)
{
	expect_file(batch_path, batch_new, sizeof(batch_new),
		    "final-batch-content");
	expect_file(created_path, created_new, sizeof(created_new),
		    "final-created-content");
	printf("fsepoch_ucore: parent passed case=%s blocks=%u\n", case_name(),
	       BATCH_BLOCKS + 1U);
}

int main(int argc, char **argv)
{
	int stage;
	char *public_argv[] = { PUBLIC_IMAGE, 0 };

	if (argc < 1 || argv == 0 || argv[0] == 0)
		fail("missing-argv0");
	if (strcmp(argv[0], BOOT_IMAGE) == 0) {
		if (exec(PUBLIC_IMAGE, public_argv) < 0)
			fail("public-exec");
		fail("public-exec-returned");
	}
	if (strcmp(argv[0], PUBLIC_IMAGE) != 0)
		fail("unexpected-image");

	fill_pattern(batch_old, sizeof(batch_old), 0x31U);
	fill_pattern(batch_new, sizeof(batch_new), 0xa7U);
	fill_pattern(created_new, sizeof(created_new), 0x5cU);
	stage = read_stage();
	if (stage == 0)
		prepare_baseline();
	if (stage == 'P')
		run_fault_boot();
	if (stage == 'R')
		run_recovery_boot();
	if (stage == 'D') {
		run_final_boot();
		return 0;
	}
	fail("invalid-stage");
	return 1;
}
