#include <fcntl.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "traditional_perf_seed.h"

#if TRADPERF_TARGET_AGENTOS == 1
#include <agent.h>
#endif

#define TRADPERF_SCHEMA 1U
#define FNV64_OFFSET 1469598103934665603ULL
#define FNV64_PRIME 1099511628211ULL

#define CACHE_BYTES 4096U
#define CACHE_READ_OPS 256U
#define CACHE_BATCH_FDS 8U
#define OPEN_CLOSE_OPS 256U
#define TINY_WRITE_BATCHES 8U
#define TINY_WRITES_PER_BATCH 16U
#define TINY_WRITE_BYTES 16U
#define TINY_TOTAL_BYTES \
	(TINY_WRITE_BATCHES * TINY_WRITES_PER_BATCH * TINY_WRITE_BYTES)
#define FORK_WAIT_OPS 32U
#define WARM_EXEC_OPS 16U

#if TRADPERF_TARGET_AGENTOS == 1
#define TRADPERF_TARGET_NAME "agentos"
#define TRADPERF_BARRIER_KIND "fsync"
#elif TRADPERF_TARGET_AGENTOS == 0
#define TRADPERF_TARGET_NAME "baseline"
#define TRADPERF_BARRIER_KIND "sync_write_completion"
#else
#error "TRADPERF_TARGET_AGENTOS must be zero or one"
#endif

struct clock_sample {
	uint64 us;
	uint64 ticks;
};

struct duration {
	uint64 us;
	uint64 ticks;
};

struct counters {
	uint64 open_calls;
	uint64 close_calls;
	uint64 read_calls;
	uint64 write_calls;
	uint64 bytes_read;
	uint64 bytes_written;
	uint64 fork_calls;
	uint64 exec_calls;
	uint64 wait_calls;
	uint64 durability_barriers;
};

struct result {
	const char *workload;
	const char *barrier_kind;
	uint64 operations;
	uint64 outcome_hash;
	struct duration elapsed;
	struct counters calls;
	uint64 file_auth_full;
	uint64 file_auth_lease_hits;
	uint64 file_auth_revalidations;
};

struct file_auth_sample {
	uint64 full;
	uint64 hits;
	uint64 revalidations;
};

struct line_builder {
	char data[768];
	uint length;
};

static unsigned char cache_seed[CACHE_BYTES];
static unsigned char cache_warm[CACHE_BYTES];
static unsigned char cache_reads[CACHE_BATCH_FDS][CACHE_BYTES];
static unsigned char tiny_expected[TINY_TOTAL_BYTES];
static unsigned char tiny_observed[TINY_TOTAL_BYTES];
static int wait_statuses[FORK_WAIT_OPS];
static int exec_statuses[WARM_EXEC_OPS];

static int write_all(int fd, const void *data, uint length)
{
	const char *bytes = data;
	uint done = 0;

	while (done < length) {
		int written = write(fd, bytes + done, length - done);

		if (written <= 0 || (uint)written > length - done)
			return -1;
		done += (uint)written;
	}
	return 0;
}

static void require(int condition, const char *step);

static struct file_auth_sample file_auth_now(void)
{
	struct file_auth_sample sample;

	memset(&sample, 0, sizeof(sample));
#if TRADPERF_TARGET_AGENTOS == 1
	struct agent_performance_snapshot snapshot;

	memset(&snapshot, 0, sizeof(snapshot));
	require(agent_performance_snapshot(&snapshot) == AGENT_STATUS_OK,
		"file-auth-snapshot");
	require(snapshot.version == AGENT_PERFORMANCE_SNAPSHOT_VERSION &&
		snapshot.struct_size == sizeof(snapshot), "file-auth-schema");
	sample.full = snapshot.file_auth_full;
	sample.hits = snapshot.file_auth_lease_hits;
	sample.revalidations = snapshot.file_auth_revalidations;
#endif
	return sample;
}

static void file_auth_delta(struct result *result,
			    const struct file_auth_sample *before)
{
	struct file_auth_sample after = file_auth_now();

	require(after.full >= before->full && after.hits >= before->hits &&
		after.revalidations >= before->revalidations,
		"file-auth-monotonic");
	result->file_auth_full = after.full - before->full;
	result->file_auth_lease_hits = after.hits - before->hits;
	result->file_auth_revalidations =
		after.revalidations - before->revalidations;
}

static void emit_failure(const char *step)
{
	static const char prefix[] = "tradperf: failed step=";
	static const char newline[] = "\n";

	(void)write_all(1, prefix, sizeof(prefix) - 1U);
	(void)write_all(1, step, (uint)strlen(step));
	(void)write_all(1, newline, 1);
	exit(1);
}

static void require(int condition, const char *step)
{
	if (!condition)
		emit_failure(step);
}

static void line_text(struct line_builder *line, const char *text)
{
	uint length = (uint)strlen(text);

	require(length <= sizeof(line->data) - line->length,
		"protocol-line-capacity");
	memcpy(line->data + line->length, text, length);
	line->length += length;
}

static void line_u64(struct line_builder *line, uint64 value)
{
	char digits[20];
	uint used = 0;

	do {
		digits[used++] = (char)('0' + value % 10ULL);
		value /= 10ULL;
	} while (value != 0);
	require(used <= sizeof(line->data) - line->length,
		"protocol-number-capacity");
	while (used != 0)
		line->data[line->length++] = digits[--used];
}

static void line_field(struct line_builder *line, const char *name,
		       uint64 value)
{
	line_text(line, " ");
	line_text(line, name);
	line_text(line, "=");
	line_u64(line, value);
}

static void line_string_field(struct line_builder *line, const char *name,
			      const char *value)
{
	line_text(line, " ");
	line_text(line, name);
	line_text(line, "=");
	line_text(line, value);
}

static void line_emit(struct line_builder *line)
{
	require(line->length < sizeof(line->data), "protocol-newline-capacity");
	line->data[line->length++] = '\n';
	require(write_all(1, line->data, line->length) == 0,
		"protocol-write");
}

static uint64 hash_byte(uint64 hash, unsigned int byte)
{
	return (hash ^ (byte & 0xffU)) * FNV64_PRIME;
}

static uint64 hash_bytes(uint64 hash, const void *data, uint length)
{
	const unsigned char *bytes = data;

	for (uint i = 0; i < length; i++)
		hash = hash_byte(hash, bytes[i]);
	return hash;
}

static int bytes_equal(const void *left, const void *right, uint length)
{
	const unsigned char *a = left;
	const unsigned char *b = right;

	for (uint i = 0; i < length; i++)
		if (a[i] != b[i])
			return 0;
	return 1;
}

static uint64 hash_text(uint64 hash, const char *text)
{
	return hash_bytes(hash, text, (uint)strlen(text));
}

static uint64 hash_u64(uint64 hash, uint64 value)
{
	for (uint shift = 0; shift < 64U; shift += 8U)
		hash = hash_byte(hash, (unsigned int)(value >> shift));
	return hash;
}

static uint64 outcome_begin(const char *workload)
{
	uint64 hash = FNV64_OFFSET;

	hash = hash_text(hash, "agentos-tradperf-v1|");
	hash = hash_u64(hash, TRADPERF_RUN_NONCE);
	hash = hash_text(hash, "|");
	hash = hash_text(hash, workload);
	return hash_text(hash, "|");
}

static struct clock_sample clock_now(void)
{
	TimeVal now;
	struct clock_sample sample;

	require(sys_get_time(&now, 0) == 0, "clock");
	sample.us = now.sec * 1000000ULL + now.usec;
	sample.ticks = now.sec * 1000ULL + now.usec / 1000ULL;
	return sample;
}

static void duration_add(struct duration *elapsed,
			 const struct clock_sample *start,
			 const struct clock_sample *end)
{
	require(end->us >= start->us && end->ticks >= start->ticks,
		"monotonic-clock");
	elapsed->us += end->us - start->us;
	elapsed->ticks = elapsed->us / 1000ULL;
}

static unsigned char cache_byte(uint offset)
{
	uint lane = offset & 7U;
	uint64 challenge_byte = TRADPERF_RUN_NONCE >> (lane * 8U);
	uint mixed = (uint)challenge_byte ^ (TRADPERF_SAMPLE_ID * 29U) ^
		(offset * 131U) ^ (offset >> 3U);

	return (unsigned char)mixed;
}

static unsigned char tiny_byte(uint offset)
{
	uint lane = offset & 7U;
	uint64 challenge_byte = TRADPERF_RUN_NONCE >> (lane * 8U);
	uint mixed = (uint)challenge_byte ^ (TRADPERF_SAMPLE_ID * 43U) ^
		(offset * 73U) ^ (offset >> 4U);

	return (unsigned char)mixed;
}

static void create_file(const char *path, const void *data, uint length)
{
	(void)unlink(path);
	int fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);

	require(fd >= 0, "fixture-open");
	require(write_all(fd, data, length) == 0, "fixture-write");
#if TRADPERF_TARGET_AGENTOS == 1
	require(fsync(fd) == 0, "fixture-fsync");
#endif
	require(close(fd) == 0, "fixture-close");
}

static void read_file_exact(const char *path, void *data, uint length)
{
	int fd = open(path, O_RDONLY);
	char trailing;

	require(fd >= 0, "verify-open");
	require(read(fd, data, length) == (int)length, "verify-read");
	require(read(fd, &trailing, 1) == 0, "verify-length");
	require(close(fd) == 0, "verify-close");
}

static struct result run_cache_read(void)
{
	static const char path[] = "tpcache";
	struct result result;
	struct file_auth_sample auth;
	int fds[CACHE_BATCH_FDS];

	memset(&result, 0, sizeof(result));
	result.workload = "cache_read_4k";
	result.barrier_kind = "none";
	result.operations = CACHE_READ_OPS;
	result.outcome_hash = outcome_begin(result.workload);
	for (uint i = 0; i < CACHE_BYTES; i++)
		cache_seed[i] = cache_byte(i);
	create_file(path, cache_seed, sizeof(cache_seed));
	read_file_exact(path, cache_warm, sizeof(cache_warm));
	require(bytes_equal(cache_warm, cache_seed, sizeof(cache_warm)),
		"cache-warm-content");
	auth = file_auth_now();

	/*
	 * uCore has no seek syscall. Each batch pre-opens independent offsets;
	 * only the eight read calls are timed, and the 32 real windows are added.
	 */
	for (uint batch = 0; batch < CACHE_READ_OPS / CACHE_BATCH_FDS;
	     batch++) {
		for (uint lane = 0; lane < CACHE_BATCH_FDS; lane++) {
			fds[lane] = open(path, O_RDONLY);
			require(fds[lane] >= 0, "cache-batch-open");
		}
		struct clock_sample start = clock_now();
		for (uint lane = 0; lane < CACHE_BATCH_FDS; lane++) {
			int count = read(fds[lane], cache_reads[lane], CACHE_BYTES);

			require(count == (int)CACHE_BYTES, "cache-batch-read");
			result.calls.read_calls++;
			result.calls.bytes_read += (uint)count;
		}
		struct clock_sample end = clock_now();
		duration_add(&result.elapsed, &start, &end);
		for (uint lane = 0; lane < CACHE_BATCH_FDS; lane++) {
			result.outcome_hash = hash_bytes(result.outcome_hash,
				cache_reads[lane], CACHE_BYTES);
			require(close(fds[lane]) == 0, "cache-batch-close");
		}
	}
	file_auth_delta(&result, &auth);
	require(unlink(path) == 0, "cache-cleanup");
	return result;
}

static struct result run_open_close(void)
{
	static const char path[] = "tpopen";
	static const unsigned char content[] = { 0x74, 0x70, 0x01, 0x00 };
	struct result result;
	struct file_auth_sample auth;
	struct clock_sample start;
	struct clock_sample end;

	memset(&result, 0, sizeof(result));
	result.workload = "open_close";
	result.barrier_kind = "none";
	result.operations = OPEN_CLOSE_OPS;
	result.outcome_hash = outcome_begin(result.workload);
	create_file(path, content, sizeof(content));
	auth = file_auth_now();
	start = clock_now();
	for (uint i = 0; i < OPEN_CLOSE_OPS; i++) {
		int fd = open(path, O_RDONLY);

		require(fd >= 0, "open-close-open");
		result.calls.open_calls++;
		require(close(fd) == 0, "open-close-close");
		result.calls.close_calls++;
	}
	end = clock_now();
	duration_add(&result.elapsed, &start, &end);
	file_auth_delta(&result, &auth);
	for (uint i = 0; i < OPEN_CLOSE_OPS; i++)
		result.outcome_hash = hash_u64(result.outcome_hash, i + 1U);
	require(unlink(path) == 0, "open-close-cleanup");
	return result;
}

static struct result run_tiny_write(void)
{
	static const char path[] = "tpwrite";
	struct result result;
	struct file_auth_sample auth;
	struct clock_sample start;
	struct clock_sample end;
	uint offset = 0;

	memset(&result, 0, sizeof(result));
	result.workload = "tiny_write_fsync";
	result.barrier_kind = TRADPERF_BARRIER_KIND;
	result.operations = TINY_WRITE_BATCHES * TINY_WRITES_PER_BATCH;
	result.outcome_hash = outcome_begin(result.workload);
	for (uint i = 0; i < sizeof(tiny_expected); i++)
		tiny_expected[i] = tiny_byte(i);
	(void)unlink(path);
	int fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);
	require(fd >= 0, "tiny-open");
	auth = file_auth_now();
	start = clock_now();
	for (uint batch = 0; batch < TINY_WRITE_BATCHES; batch++) {
		for (uint write_index = 0;
		     write_index < TINY_WRITES_PER_BATCH; write_index++) {
			int count = write(fd, tiny_expected + offset,
					  TINY_WRITE_BYTES);

			require(count == (int)TINY_WRITE_BYTES, "tiny-write");
			result.calls.write_calls++;
			result.calls.bytes_written += (uint)count;
			offset += (uint)count;
#if TRADPERF_TARGET_AGENTOS == 0
			/* Baseline completes a synchronous block write per call. */
			result.calls.durability_barriers++;
#endif
		}
#if TRADPERF_TARGET_AGENTOS == 1
		require(fsync(fd) == 0, "tiny-fsync");
		result.calls.durability_barriers++;
#else
		/* Every baseline write returns only after its synchronous bwrite. */
		__asm__ volatile("" ::: "memory");
#endif
	}
	end = clock_now();
	duration_add(&result.elapsed, &start, &end);
	file_auth_delta(&result, &auth);
	/* Close and byte-for-byte readback prove the result outside the write window. */
	require(close(fd) == 0, "tiny-close");
	read_file_exact(path, tiny_observed, sizeof(tiny_observed));
	require(bytes_equal(tiny_observed, tiny_expected, sizeof(tiny_observed)),
		"tiny-readback-content");
	result.outcome_hash = hash_bytes(result.outcome_hash, tiny_observed,
					 sizeof(tiny_observed));
	result.outcome_hash = hash_u64(result.outcome_hash, TINY_WRITE_BATCHES);
	require(unlink(path) == 0, "tiny-cleanup");
	return result;
}

static int fork_exit_status(uint iteration)
{
	uint lane = iteration & 7U;
	uint value = (uint)(TRADPERF_RUN_NONCE >> (lane * 8U));

	value ^= TRADPERF_SAMPLE_ID * 3U;
	value ^= iteration;
	return (int)(value & 0x3fU);
}

static struct result run_fork_wait(void)
{
	struct result result;
	struct file_auth_sample auth;
	struct clock_sample start;
	struct clock_sample end;

	memset(&result, 0, sizeof(result));
	result.workload = "fork_wait";
	result.barrier_kind = "none";
	result.operations = FORK_WAIT_OPS;
	result.outcome_hash = outcome_begin(result.workload);
	auth = file_auth_now();
	start = clock_now();
	for (uint i = 0; i < FORK_WAIT_OPS; i++) {
		int pid = fork();

		require(pid >= 0, "fork-wait-fork");
		if (pid == 0)
			exit(fork_exit_status(i));
		result.calls.fork_calls++;
		wait_statuses[i] = -1;
		require(waitpid(pid, &wait_statuses[i]) == pid, "fork-wait-wait");
		result.calls.wait_calls++;
		require(wait_statuses[i] == fork_exit_status(i),
			"fork-wait-status");
	}
	end = clock_now();
	duration_add(&result.elapsed, &start, &end);
	file_auth_delta(&result, &auth);
	for (uint i = 0; i < FORK_WAIT_OPS; i++)
		result.outcome_hash = hash_u64(result.outcome_hash,
					       (uint)(unsigned int)wait_statuses[i]);
	return result;
}

static void fixed_hex16(uint64 value, char text[17])
{
	static const char digits[] = "0123456789abcdef";

	for (int index = 15; index >= 0; index--) {
		text[index] = digits[value & 0xfULL];
		value >>= 4;
	}
	text[16] = 0;
}

static void decimal_u32(uint value, char text[11])
{
	char reverse[10];
	uint used = 0;

	do {
		reverse[used++] = (char)('0' + value % 10U);
		value /= 10U;
	} while (value != 0);
	for (uint i = 0; i < used; i++)
		text[i] = reverse[used - i - 1U];
	text[used] = 0;
}

static int exec_probe_status(uint iteration)
{
	uint lane = iteration & 7U;
	uint value = (uint)(TRADPERF_RUN_NONCE >> (lane * 8U));

	value ^= TRADPERF_SAMPLE_ID * 7U;
	value ^= iteration * 17U;
	return (int)(value & 0x3fU);
}

static int launch_exec_probe(uint iteration)
{
	char nonce[17];
	char sample[11];
	char order_slot[11];
	char iteration_text[11];
	char *argv[] = {
		"tradexec", nonce, sample, order_slot, iteration_text, 0,
	};
	int status = -1;
	int pid;

	fixed_hex16(TRADPERF_RUN_NONCE, nonce);
	decimal_u32(TRADPERF_SAMPLE_ID, sample);
	decimal_u32(TRADPERF_ORDER_SLOT, order_slot);
	decimal_u32(iteration, iteration_text);
	pid = fork();
	require(pid >= 0, "warm-exec-fork");
	if (pid == 0) {
		exec("tradexec", argv);
		exit(127);
	}
	require(waitpid(pid, &status) == pid, "warm-exec-wait");
	require(status == exec_probe_status(iteration), "warm-exec-status");
	return status;
}

static struct result run_warm_exec(void)
{
	struct result result;
	struct file_auth_sample auth;
	struct clock_sample start;
	struct clock_sample end;

	memset(&result, 0, sizeof(result));
	result.workload = "warm_exec";
	result.barrier_kind = "none";
	result.operations = WARM_EXEC_OPS;
	result.outcome_hash = outcome_begin(result.workload);
	(void)launch_exec_probe(255U);
	auth = file_auth_now();
	start = clock_now();
	for (uint i = 0; i < WARM_EXEC_OPS; i++) {
		exec_statuses[i] = launch_exec_probe(i);
		result.calls.fork_calls++;
		result.calls.exec_calls++;
		result.calls.wait_calls++;
	}
	end = clock_now();
	duration_add(&result.elapsed, &start, &end);
	file_auth_delta(&result, &auth);
	for (uint i = 0; i < WARM_EXEC_OPS; i++)
		result.outcome_hash = hash_u64(result.outcome_hash,
					       (uint)(unsigned int)exec_statuses[i]);
	return result;
}

static void emit_common_prefix(struct line_builder *line)
{
	line_text(line, "agentos:tradperf schema=1 nonce=");
	line_u64(line, TRADPERF_RUN_NONCE);
	line_field(line, "sample", TRADPERF_SAMPLE_ID);
	line_string_field(line, "target", TRADPERF_TARGET_NAME);
}

static void emit_begin(void)
{
	struct line_builder line;

	memset(&line, 0, sizeof(line));
	emit_common_prefix(&line);
	line_field(&line, "order_slot", TRADPERF_ORDER_SLOT);
	line_string_field(&line, "phase", "begin");
	line_string_field(&line, "tick_unit", "ms");
	line_emit(&line);
}

static void emit_result(const struct result *result)
{
	struct line_builder line;

	memset(&line, 0, sizeof(line));
	emit_common_prefix(&line);
	line_string_field(&line, "workload", result->workload);
	line_field(&line, "duration_us", result->elapsed.us);
	line_field(&line, "duration_ticks", result->elapsed.ticks);
	line_field(&line, "ops", result->operations);
	line_field(&line, "outcome_hash", result->outcome_hash);
	line_field(&line, "open_calls", result->calls.open_calls);
	line_field(&line, "close_calls", result->calls.close_calls);
	line_field(&line, "read_calls", result->calls.read_calls);
	line_field(&line, "write_calls", result->calls.write_calls);
	line_field(&line, "bytes_read", result->calls.bytes_read);
	line_field(&line, "bytes_written", result->calls.bytes_written);
	line_field(&line, "fork_calls", result->calls.fork_calls);
	line_field(&line, "exec_calls", result->calls.exec_calls);
	line_field(&line, "wait_calls", result->calls.wait_calls);
	line_field(&line, "durability_barriers",
		   result->calls.durability_barriers);
	line_field(&line, "file_auth_full", result->file_auth_full);
	line_field(&line, "file_auth_lease_hits", result->file_auth_lease_hits);
	line_field(&line, "file_auth_revalidations",
		   result->file_auth_revalidations);
	line_string_field(&line, "barrier_kind", result->barrier_kind);
	line_emit(&line);
}

static void emit_end(uint64 aggregate_hash)
{
	struct line_builder line;

	memset(&line, 0, sizeof(line));
	emit_common_prefix(&line);
	line_string_field(&line, "phase", "end");
	line_field(&line, "aggregate_hash", aggregate_hash);
	line_emit(&line);
}

int main(void)
{
	struct result results[5];
	uint64 aggregate_hash;

	require(TRADPERF_RUN_NONCE != 0, "nonzero-nonce");
	emit_begin();
	results[0] = run_cache_read();
	results[1] = run_open_close();
	results[2] = run_tiny_write();
	results[3] = run_fork_wait();
	results[4] = run_warm_exec();
	for (uint i = 0; i < sizeof(results) / sizeof(results[0]); i++)
		emit_result(&results[i]);

	aggregate_hash = outcome_begin("aggregate");
	for (uint i = 0; i < sizeof(results) / sizeof(results[0]); i++)
		aggregate_hash = hash_u64(aggregate_hash,
					  results[i].outcome_hash);
	emit_end(aggregate_hash);
	require(write_all(1, "tradperf: complete\n", 19U) == 0,
		"completion-write");
	return 0;
}
