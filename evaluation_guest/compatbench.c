#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "compatbench_seed.h"

/* 基线 uCore 和 AgentOS-uCore 原样编译本文件；负载不使用目标专属 API，
 * 保证两侧测量工作相同。 */
#define BENCH_SCHEMA 2
#define BENCH_ROUNDS 3
#define METRIC_COUNT 5
#define BENCH_CACHE_STATE "warm_guest_paths"
#define BENCH_SCHEDULE "challenge_rotated_v1"
#define FORK_WAIT_OPS 32
#define FORK_EXEC_WAIT_OPS 12
#define PIPE_ROUNDTRIP_OPS 1024
#define FILE_CHUNK_BYTES 512
#define FILE_CHUNKS_PER_DIRECTION 256
#define FILE_TOTAL_BYTES \
	(FILE_CHUNK_BYTES * FILE_CHUNKS_PER_DIRECTION * 2)
#define PIPELINE_INPUT_SHARDS 8
#define PIPELINE_RECORDS_PER_SHARD 64
#define PIPELINE_RECORDS \
	(PIPELINE_INPUT_SHARDS * PIPELINE_RECORDS_PER_SHARD)
#define PIPELINE_GROUPS 8
#define PIPELINE_RECORD_BYTES 16
#define PIPELINE_SOURCE_BYTES (PIPELINE_RECORDS * PIPELINE_RECORD_BYTES)
#define PIPELINE_ARTIFACT_BYTES 64
#define PIPELINE_ARTIFACT_MAGIC 0x52504150U
#define PIPELINE_ARTIFACT_SCHEMA 1U

static const char *const metric_names[] = {
	"fork_wait",
	"fork_exec_wait",
	"pipe_roundtrip",
	"seq_file_io",
	"research_artifact_pipeline",
};

struct pipeline_record {
	unsigned int record_id;
	unsigned int group;
	unsigned int measurement;
	unsigned int proof;
};

struct pipeline_artifact {
	unsigned int magic;
	unsigned int schema;
	unsigned int source_records;
	unsigned int source_bytes;
	unsigned int transformed_records;
	unsigned int group_count;
	unsigned int group_totals[PIPELINE_GROUPS];
	unsigned int source_checksum;
	unsigned int result_checksum;
};

static void fail(const char *message)
{
	printf("compatbench: check failed: %s\n", message);
	exit(1);
}

static void require(int condition, const char *message)
{
	if (!condition)
		fail(message);
}

static void hex_fixed(unsigned long long value, char *text, int digits)
{
	static const char alphabet[] = "0123456789abcdef";

	for (int i = digits - 1; i >= 0; i--) {
		text[i] = alphabet[value & 0xfULL];
		value >>= 4;
	}
	text[digits] = 0;
}

static unsigned int fnv_byte(unsigned int hash, unsigned int byte)
{
	return (hash ^ (byte & 0xffU)) * 16777619U;
}

static unsigned int fnv_u32(unsigned int hash, unsigned int value)
{
	for (int shift = 0; shift < 32; shift += 8)
		hash = fnv_byte(hash, value >> shift);
	return hash;
}

static unsigned int receipt_begin(void)
{
	unsigned int hash = 2166136261U;

	hash = fnv_u32(hash, (unsigned int)COMPATBENCH_CHALLENGE);
	hash = fnv_u32(hash,
		       (unsigned int)(COMPATBENCH_CHALLENGE >> 32));
	return hash;
}

static unsigned int receipt_add(unsigned int hash, int metric, int round,
				unsigned int operations, unsigned int elapsed_ms,
				unsigned int checksum)
{
	hash = fnv_u32(hash, (unsigned int)metric);
	hash = fnv_u32(hash, (unsigned int)round);
	hash = fnv_u32(hash, operations);
	hash = fnv_u32(hash, elapsed_ms);
	return fnv_u32(hash, checksum);
}

static int scheduled_metric(int round, int position)
{
	unsigned int start = ((unsigned int)COMPATBENCH_CHALLENGE +
		(unsigned int)(round - 1) * 2U) % METRIC_COUNT;
	int forward = (int)(((COMPATBENCH_CHALLENGE >> 8) ^
		(unsigned long long)round) & 1ULL);
	unsigned int offset = forward ? (unsigned int)position :
		(METRIC_COUNT - (unsigned int)position) % METRIC_COUNT;

	return (int)((start + offset) % METRIC_COUNT);
}

static int read_full(int fd, void *buffer, int length)
{
	char *bytes = buffer;
	int done = 0;

	while (done < length) {
		int count = read(fd, bytes + done, (size_t)(length - done));

		if (count <= 0)
			return -1;
		done += count;
	}
	return 0;
}

static int write_full(int fd, const void *buffer, int length)
{
	const char *bytes = buffer;
	int done = 0;

	while (done < length) {
		int count = write(fd, bytes + done, (size_t)(length - done));

		if (count <= 0)
			return -1;
		done += count;
	}
	return 0;
}

static int bytes_equal(const char *left, const char *right, int length)
{
	for (int i = 0; i < length; i++)
		if (left[i] != right[i])
			return 0;
	return 1;
}

static unsigned int bench_fork_wait(void)
{
	unsigned int checksum = 2166136261U;

	for (int i = 0; i < FORK_WAIT_OPS; i++) {
		int status = -1;
		int pid = fork();

		require(pid >= 0, "fork/wait fork");
		if (pid == 0)
			exit(0);
		require(waitpid(pid, &status) == pid, "fork/wait waitpid");
		require(status == 0, "fork/wait child status");
		checksum = fnv_u32(checksum, (unsigned int)i);
	}
	return checksum;
}

static unsigned int bench_fork_exec_wait(const char *challenge)
{
	unsigned int checksum = 2166136261U;
	char *worker_argv[] = {
		"compatbench",
		"--exec-worker",
		(char *)challenge,
		0,
	};

	for (int i = 0; i < FORK_EXEC_WAIT_OPS; i++) {
		int status = -1;
		int pid = fork();

		require(pid >= 0, "fork/exec/wait fork");
		if (pid == 0) {
			exec("compatbench", worker_argv);
			exit(91);
		}
		require(waitpid(pid, &status) == pid, "fork/exec/wait waitpid");
		require(status == 0, "fork/exec/wait child status");
		checksum = fnv_u32(checksum, (unsigned int)(i + 0x100));
	}
	return checksum;
}

static unsigned int bench_pipe_roundtrip(unsigned int *elapsed_ms)
{
	int request[2];
	int response[2];
	unsigned int checksum = 2166136261U;
	unsigned int word;
	int status = -1;

	require(pipe(request) == 0, "pipe request create");
	require(pipe(response) == 0, "pipe response create");
	int pid = fork();
	require(pid >= 0, "pipe fork");
	if (pid == 0) {
		close(request[1]);
		close(response[0]);
		word = 0x72656164U;
		if (write_full(response[1], &word, sizeof(word)) < 0)
			exit(94);
		for (int i = 0; i < PIPE_ROUNDTRIP_OPS; i++) {
			if (read_full(request[0], &word, sizeof(word)) < 0)
				exit(92);
			word ^= 0xa5a55a5aU;
			if (write_full(response[1], &word, sizeof(word)) < 0)
				exit(93);
		}
		close(request[0]);
		close(response[1]);
		exit(0);
	}

	close(request[0]);
	close(response[1]);
	require(read_full(response[0], &word, sizeof(word)) == 0,
		"pipe child ready");
	require(word == 0x72656164U, "pipe ready value");
	long long start = get_mtime();
	for (int i = 0; i < PIPE_ROUNDTRIP_OPS; i++) {
		unsigned int expected;

		word = (unsigned int)COMPATBENCH_CHALLENGE ^ (unsigned int)i;
		require(write_full(request[1], &word, sizeof(word)) == 0,
			"pipe request write");
		require(read_full(response[0], &word, sizeof(word)) == 0,
			"pipe response read");
		expected = ((unsigned int)COMPATBENCH_CHALLENGE ^
			    (unsigned int)i) ^ 0xa5a55a5aU;
		require(word == expected, "pipe response value");
		checksum = fnv_u32(checksum, word);
	}
	long long end = get_mtime();
	close(request[1]);
	close(response[0]);
	require(waitpid(pid, &status) == pid, "pipe waitpid");
	require(status == 0, "pipe child status");
	require(start >= 0 && end >= start, "pipe measurement window");
	*elapsed_ms = (unsigned int)(end - start);
	return checksum;
}

static unsigned char file_byte(unsigned int offset)
{
	unsigned long long challenge = COMPATBENCH_CHALLENGE;
	unsigned int lane = offset & 7U;

	return (unsigned char)(((challenge >> (lane * 8U)) & 0xffU) ^
			       (offset * 131U) ^ (offset >> 7));
}

static unsigned int bench_seq_file_io(unsigned int *elapsed_ms)
{
	char buffer[FILE_CHUNK_BYTES];
	char expected[FILE_CHUNK_BYTES];
	unsigned int checksum = 2166136261U;
	const char *path = "cbseqio";

	for (unsigned int i = 0; i < FILE_CHUNK_BYTES; i++)
		expected[i] = (char)file_byte(i);
	(void)unlink(path);
	int fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);
	require(fd >= 0, "sequential file create");
	long long start = get_mtime();
	for (unsigned int chunk = 0; chunk < FILE_CHUNKS_PER_DIRECTION;
	     chunk++) {
		require(write_full(fd, expected, FILE_CHUNK_BYTES) == 0,
			"sequential file write");
		checksum = fnv_u32(checksum,
				   (unsigned int)(unsigned char)expected[chunk]);
	}
	require(close(fd) == 0, "sequential file write close");

	fd = open(path, O_RDONLY);
	require(fd >= 0, "sequential file open");
	for (unsigned int chunk = 0; chunk < FILE_CHUNKS_PER_DIRECTION;
	     chunk++) {
		require(read_full(fd, buffer, FILE_CHUNK_BYTES) == 0,
			"sequential file read");
		require(bytes_equal(buffer, expected, FILE_CHUNK_BYTES),
			"sequential file content");
		checksum = fnv_u32(checksum,
				   (unsigned int)(unsigned char)buffer[FILE_CHUNK_BYTES - 1]);
	}
	require(close(fd) == 0, "sequential file read close");
	long long end = get_mtime();
	require(unlink(path) == 0, "sequential file unlink");
	require(start >= 0 && end >= start, "file measurement window");
	*elapsed_ms = (unsigned int)(end - start);
	return checksum;
}

static struct pipeline_record pipeline_record_at(unsigned int index)
{
	struct pipeline_record record;
	unsigned long long challenge = COMPATBENCH_CHALLENGE;
	unsigned int lane = (unsigned int)
		((challenge >> ((index & 7U) * 8U)) & 0xffU);
	unsigned int checksum = 2166136261U;

	record.record_id = index;
	record.group = (index + (unsigned int)challenge) &
		(PIPELINE_GROUPS - 1U);
	record.measurement = (lane * 257U + index * 17U +
		(index / PIPELINE_RECORDS_PER_SHARD) * 31U) & 0xffffU;
	checksum = fnv_u32(checksum, (unsigned int)challenge);
	checksum = fnv_u32(checksum, (unsigned int)(challenge >> 32));
	checksum = fnv_u32(checksum, record.record_id);
	checksum = fnv_u32(checksum, record.group);
	record.proof = fnv_u32(checksum, record.measurement);
	return record;
}

static void pipeline_shard_path(unsigned int shard, char path[5])
{
	path[0] = 'c';
	path[1] = 'b';
	path[2] = 'p';
	path[3] = (char)('0' + shard);
	path[4] = 0;
}

static unsigned int pipeline_artifact_checksum(
	const struct pipeline_artifact *artifact)
{
	unsigned int checksum = 2166136261U;

	checksum = fnv_u32(checksum, artifact->magic);
	checksum = fnv_u32(checksum, artifact->schema);
	checksum = fnv_u32(checksum, artifact->source_records);
	checksum = fnv_u32(checksum, artifact->source_bytes);
	checksum = fnv_u32(checksum, artifact->transformed_records);
	checksum = fnv_u32(checksum, artifact->group_count);
	for (unsigned int group = 0; group < PIPELINE_GROUPS; group++)
		checksum = fnv_u32(checksum, artifact->group_totals[group]);
	return fnv_u32(checksum, artifact->source_checksum);
}

static unsigned int bench_research_artifact_pipeline(unsigned int *elapsed_ms)
{
	struct pipeline_record records[PIPELINE_RECORDS_PER_SHARD];
	struct pipeline_artifact artifact;
	struct pipeline_artifact observed;
	unsigned int source_checksum = 2166136261U;
	char path[5];
	char trailing;
	const char *artifact_path = "cbpart";

	require(sizeof(struct pipeline_record) == PIPELINE_RECORD_BYTES,
		"pipeline record layout");
	require(sizeof(struct pipeline_artifact) == PIPELINE_ARTIFACT_BYTES,
		"pipeline artifact layout");
	for (unsigned int shard = 0; shard < PIPELINE_INPUT_SHARDS; shard++) {
		pipeline_shard_path(shard, path);
		(void)unlink(path);
	}
	(void)unlink(artifact_path);

	long long start = get_mtime();
	for (unsigned int shard = 0; shard < PIPELINE_INPUT_SHARDS; shard++) {
		for (unsigned int slot = 0; slot < PIPELINE_RECORDS_PER_SHARD;
		     slot++)
			records[slot] = pipeline_record_at(
				shard * PIPELINE_RECORDS_PER_SHARD + slot);
		pipeline_shard_path(shard, path);
		int fd = open(path, O_CREATE | O_WRONLY | O_TRUNC);
		require(fd >= 0, "pipeline shard create");
		require(write_full(fd, records, sizeof(records)) == 0,
			"pipeline shard write");
		require(close(fd) == 0, "pipeline shard write close");
	}

	memset(&artifact, 0, sizeof(artifact));
	artifact.magic = PIPELINE_ARTIFACT_MAGIC;
	artifact.schema = PIPELINE_ARTIFACT_SCHEMA;
	artifact.source_records = PIPELINE_RECORDS;
	artifact.source_bytes = PIPELINE_SOURCE_BYTES;
	artifact.transformed_records = PIPELINE_RECORDS;
	artifact.group_count = PIPELINE_GROUPS;
	for (unsigned int shard = 0; shard < PIPELINE_INPUT_SHARDS; shard++) {
		pipeline_shard_path(shard, path);
		int fd = open(path, O_RDONLY);
		require(fd >= 0, "pipeline shard open");
		require(read_full(fd, records, sizeof(records)) == 0,
			"pipeline shard read");
		require(read(fd, &trailing, 1) == 0, "pipeline shard length");
		require(close(fd) == 0, "pipeline shard read close");
		for (unsigned int slot = 0; slot < PIPELINE_RECORDS_PER_SHARD;
		     slot++) {
			unsigned int index =
				shard * PIPELINE_RECORDS_PER_SHARD + slot;
			struct pipeline_record expected = pipeline_record_at(index);
			unsigned int transformed;

			require(bytes_equal((const char *)&records[slot],
					    (const char *)&expected,
					    sizeof(expected)),
				"pipeline source record");
			source_checksum = fnv_u32(source_checksum,
						 records[slot].record_id);
			source_checksum = fnv_u32(source_checksum,
						 records[slot].group);
			source_checksum = fnv_u32(source_checksum,
						 records[slot].measurement);
			source_checksum = fnv_u32(source_checksum,
						 records[slot].proof);
			transformed = (records[slot].measurement ^
				       records[slot].proof) & 0xffffU;
			artifact.group_totals[records[slot].group] += transformed;
		}
	}
	artifact.source_checksum = source_checksum;
	artifact.result_checksum = pipeline_artifact_checksum(&artifact);

	int fd = open(artifact_path, O_CREATE | O_WRONLY | O_TRUNC);
	require(fd >= 0, "pipeline artifact create");
	require(write_full(fd, &artifact, sizeof(artifact)) == 0,
		"pipeline artifact write");
	require(close(fd) == 0, "pipeline artifact write close");
	fd = open(artifact_path, O_RDONLY);
	require(fd >= 0, "pipeline artifact open");
	require(read_full(fd, &observed, sizeof(observed)) == 0,
		"pipeline artifact read");
	require(read(fd, &trailing, 1) == 0, "pipeline artifact length");
	require(close(fd) == 0, "pipeline artifact read close");
	require(bytes_equal((const char *)&artifact, (const char *)&observed,
			    sizeof(artifact)), "pipeline artifact content");
	long long end = get_mtime();

	for (unsigned int shard = 0; shard < PIPELINE_INPUT_SHARDS; shard++) {
		pipeline_shard_path(shard, path);
		require(unlink(path) == 0, "pipeline shard unlink");
	}
	require(unlink(artifact_path) == 0, "pipeline artifact unlink");
	require(start >= 0 && end >= start, "pipeline measurement window");
	*elapsed_ms = (unsigned int)(end - start);
	return artifact.result_checksum;
}

static void emit_sample(const char *challenge, int metric, int round,
			unsigned int operations, unsigned int elapsed_ms,
			unsigned int checksum)
{
	char checksum_text[9];

	hex_fixed(checksum, checksum_text, 8);
	printf("compatbench: sample schema=%d challenge=%s metric=%s "
	       "round=%d ops=%d elapsed_ms=%d checksum=%s\n",
	       BENCH_SCHEMA, challenge, metric_names[metric], round,
	       (int)operations, (int)elapsed_ms, checksum_text);
}

static unsigned int timed_metric(const char *challenge, int metric, int round,
				 unsigned int receipt)
{
	unsigned int operations;
	unsigned int checksum;
	unsigned int elapsed = 0;
	long long start;
	long long end;

	switch (metric) {
	case 0:
		operations = FORK_WAIT_OPS;
		start = get_mtime();
		checksum = bench_fork_wait();
		end = get_mtime();
		require(start >= 0 && end >= start,
			"fork/wait measurement window");
		elapsed = (unsigned int)(end - start);
		break;
	case 1:
		operations = FORK_EXEC_WAIT_OPS;
		start = get_mtime();
		checksum = bench_fork_exec_wait(challenge);
		end = get_mtime();
		require(start >= 0 && end >= start,
			"exec measurement window");
		elapsed = (unsigned int)(end - start);
		break;
	case 2:
		operations = PIPE_ROUNDTRIP_OPS;
		checksum = bench_pipe_roundtrip(&elapsed);
		break;
	case 3:
		operations = FILE_TOTAL_BYTES;
		checksum = bench_seq_file_io(&elapsed);
		break;
	case 4:
		operations = PIPELINE_RECORDS;
		checksum = bench_research_artifact_pipeline(&elapsed);
		break;
	default:
		fail("unknown metric");
		return receipt;
	}

	require(elapsed > 0, "nonzero millisecond measurement");
	emit_sample(challenge, metric, round, operations, elapsed, checksum);
	return receipt_add(receipt, metric, round, operations, elapsed, checksum);
}

int main(int argc, char **argv)
{
	char challenge[17];

	hex_fixed(COMPATBENCH_CHALLENGE, challenge, 16);
	if (argc == 3 && strcmp(argv[1], "--exec-worker") == 0)
		return strcmp(argv[2], challenge) == 0 ? 0 : 90;
	require(argc == 1, "unexpected arguments");
	require(COMPATBENCH_CHALLENGE != 0, "nonzero challenge");
	require(sizeof(metric_names) / sizeof(metric_names[0]) == METRIC_COUNT,
		"metric table size");

	printf("compatbench: begin schema=%d challenge=%s "
	       "clock=gettimeofday_ms rounds=%d source=canonical-v2 "
	       "cache=%s schedule=%s\n",
	       BENCH_SCHEMA, challenge, BENCH_ROUNDS, BENCH_CACHE_STATE,
	       BENCH_SCHEDULE);

	/* 两侧各做一次相同的非计时预热，排除首次分配影响。 */
	(void)bench_fork_wait();
	(void)bench_fork_exec_wait(challenge);
	unsigned int warmup_elapsed;
	(void)bench_pipe_roundtrip(&warmup_elapsed);
	(void)bench_seq_file_io(&warmup_elapsed);
	(void)bench_research_artifact_pipeline(&warmup_elapsed);

	unsigned int receipt = receipt_begin();
	for (int round = 1; round <= BENCH_ROUNDS; round++)
		for (int position = 0; position < METRIC_COUNT; position++) {
			int metric = scheduled_metric(round, position);

			receipt = timed_metric(challenge, metric, round, receipt);
		}

	char receipt_text[9];
	hex_fixed(receipt, receipt_text, 8);
	printf("compatbench: done schema=%d challenge=%s samples=%d receipt=%s\n",
	       BENCH_SCHEMA, challenge, BENCH_ROUNDS * METRIC_COUNT,
	       receipt_text);
	puts("compatbench: passed");
	return 0;
}
