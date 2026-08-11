#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <agenteval_seed.h>

#define EVAL_SCHEMA 1
#define EVAL_PAIRS 15
#define FIGURE_HIT_COUNT 1
#define EVAL_LOADS 3
#define EVAL_MAX_LOAD 96
#define EVAL_FILE_QUERIES 16
#define EVAL_PATH_LOADS 4
#define EVAL_PATH_MAX_QUERIES 8
#define EVAL_UNION_LOADS 5
#define EVAL_FILE_RECORD_MAGIC 0x4147464958545552ULL
#define EVAL_FILE_RECORD_SCHEMA 1
#define EVAL_CONTEXT_SELECTOR 0x4147415396722031ULL
#define FUNCTIONAL_TASK3_ROUNDS 6
#define FUNCTIONAL_TOOL_CATALOG_SCHEMA 1
#define TASK4_FUNCTIONAL_FID_BASE 10000
#define TASK4_FUNCTIONAL_FID_STRIDE 4
#define TASK5_DELAY_TICKS 8
#define TASK5_TICK_MSEC 10
#define TASK5_MAX_WAIT_LOOPS 3
#define TASK5_RECEIPT_VALUES 28
#define REVISIT_IDENTITIES 4
#define REVISIT_VISITS 5
#define REVISIT_CONCURRENCY_LEVELS 3
#define REVISIT_ROUNDS 16
#define REVISIT_COMMAND_MAGIC 0x4149524551563031ULL
#define REVISIT_REPLY_MAGIC 0x4149524552503031ULL
#define REVISIT_COMMAND_VISIT 1
#define REVISIT_COMMAND_QUERY 2
#define REVISIT_COMMAND_STOP 3

_Static_assert(TASK4_FUNCTIONAL_FID_BASE > 2000 + EVAL_MAX_LOAD,
	       "Task4 functional fids overlap performance fixtures");

#define FNV_OFFSET 1469598103934665603ULL
#define FNV_PRIME 1099511628211ULL

static const int eval_loads[EVAL_LOADS] = { 24, 64, 96 };
static const int eval_path_loads[EVAL_PATH_LOADS] = { 8, 24, 48, 96 };
static const int eval_path_operations[EVAL_PATH_LOADS] = { 8, 6, 4, 1 };
static const int eval_union_loads[EVAL_UNION_LOADS] = { 8, 24, 48, 64, 96 };

static struct agent_file_meta file_meta;
static struct agent_file_query file_query;
static struct agent_file_query_result file_result;
static struct agent_info eval_info;
static int ambient_file_records;
static int expected_visible_file_records;

static struct agent_context_header functional_context_header;
static struct agent_context_record
	functional_context_records[AGENT_CONTEXT_MAX_RECORDS];
static struct agent_op
	functional_context_ops[FUNCTIONAL_TASK3_ROUNDS + 1];
static struct agent_result
	functional_context_tool_results[FUNCTIONAL_TASK3_ROUNDS + 1];
static struct agent_tool_desc_v2 functional_tools[AGENT_TOOL_COUNT];
static int functional_tool_count;
static struct agent_param_v2 functional_params[AGENT_TOOL_PARAM_MAX];
static struct agent_request_v2 functional_request;
static struct agent_response_v2 functional_response;
static struct agent_info functional_info_before;
static struct agent_info functional_info_after;
static struct agent_event functional_event;
static volatile int task5_wait_status;
static int functional_compat_sentinel_pid;
static int functional_compat_sentinel_status;

struct file_observation {
	int syscall_result;
	int total_hits;
	int returned;
	int truncated;
	int scanned_records;
	int used_index;
	int plan;
	int candidate_records;
	int index_rebuild_records;
	uint64 plan_reason;
	uint64 fs_generation;
	uint64 path_record_hash;
	uint64 path_bytes_read;
	int path_failures;
	struct agent_file_hit hit;
};

struct eval_file_record {
	uint64 magic;
	uint64 schema;
	uint64 challenge;
	uint64 dependency_mask;
	uint64 record_hash;
	int fid;
	char physical_name[AGENT_FILE_NAME_SIZE];
	char logical_path[AGENT_FILE_LOGICAL_SIZE];
	char project[AGENT_FILE_PROJECT_SIZE];
	char workflow[AGENT_FILE_WORKFLOW_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
	char stage[AGENT_FILE_FIELD_SIZE];
	char kind[AGENT_FILE_FIELD_SIZE];
	char status[AGENT_FILE_FIELD_SIZE];
	char summary[AGENT_FILE_SUMMARY_SIZE];
};

struct measurement {
	uint64 duration_us;
	uint64 work_units;
	uint64 records_examined;
	uint64 result_items;
	uint64 index_rebuild_records;
	uint64 result_cache_hits;
	uint64 result_fingerprint;
};

struct revisit_command {
	uint64 magic;
	uint64 request_id;
	int kind;
	int identity;
	int visit;
	int reserved;
};

struct revisit_reply {
	uint64 magic;
	uint64 request_id;
	uint64 lifecycle_generation;
	uint64 started_us;
	uint64 completed_us;
	int kind;
	int identity;
	int agent_id;
	uint lifecycle_id;
	int correct;
	int contamination;
	int return_visit;
	int fallback;
};

struct revisit_worker {
	int pid;
	int command_fd;
	int reply_fd;
	int agent_id;
	uint lifecycle_id;
	uint64 lifecycle_generation;
};

struct revisit_perf_sample {
	uint64 request_id;
	uint64 submitted_us;
	uint64 started_us;
	uint64 completed_us;
	uint64 received_us;
	uint64 wait_us;
	uint64 service_us;
	uint64 turnaround_us;
	uint64 result_fingerprint;
	int concurrency;
	int round;
	int slot;
	int identity;
	int correct;
	int contamination;
	int fallback;
	int isolation_ok;
};

static struct revisit_worker revisit_workers[REVISIT_IDENTITIES];
static struct revisit_reply revisit_visits[REVISIT_VISITS];
static struct revisit_perf_sample
	revisit_perf_samples[REVISIT_IDENTITIES * REVISIT_ROUNDS];
static struct revisit_reply
	revisit_perf_replies[REVISIT_IDENTITIES * REVISIT_ROUNDS];
static uint64
	revisit_perf_sent_us[REVISIT_IDENTITIES * REVISIT_ROUNDS];
static uint64
	revisit_perf_received_us[REVISIT_IDENTITIES * REVISIT_ROUNDS];
static uint64
	revisit_sorted_wait_us[REVISIT_IDENTITIES * REVISIT_ROUNDS];
static uint64
	revisit_sorted_service_us[REVISIT_IDENTITIES * REVISIT_ROUNDS];
static uint64
	revisit_sorted_turnaround_us[REVISIT_IDENTITIES * REVISIT_ROUNDS];
static uint64 revisit_visit_fingerprints[REVISIT_VISITS];
static const int revisit_sequence[REVISIT_VISITS] = { 0, 1, 2, 3, 0 };
static const int revisit_concurrency_levels[REVISIT_CONCURRENCY_LEVELS] = {
	1, 2, 4
};

/* 测量与功能阶段串行，复用暂存区。 */
static union evaluation_capture {
	struct {
		struct file_observation scan[EVAL_FILE_QUERIES];
		struct file_observation index[EVAL_FILE_QUERIES];
		struct agent_file_query queries[EVAL_FILE_QUERIES];
		int targets[EVAL_FILE_QUERIES];
	} file;
	struct {
		struct agent_op ops[EVAL_MAX_LOAD];
		struct agent_result scalar_results[EVAL_MAX_LOAD];
		struct agent_result batch_results[EVAL_MAX_LOAD];
	} tool;
	struct {
		struct agent_context_record syscall_records[EVAL_MAX_LOAD];
		struct agent_context_record direct_records[EVAL_MAX_LOAD];
		int syscall_query_results[EVAL_MAX_LOAD];
		int direct_query_results[EVAL_MAX_LOAD];
	} context;
	struct {
		struct agent_op digest_op;
		struct agent_result digest_result;
		char challenge[17];
		char names[3][AGENT_FILE_NAME_SIZE];
		char summaries[3][AGENT_FILE_SUMMARY_SIZE];
		char bodies[3][AGENT_FAST_RESULT_SIZE];
		char needle[AGENT_FILE_SUMMARY_SIZE];
		char retired_name[AGENT_FILE_NAME_SIZE];
		uint64 values[56];
	} task4;
	uint64 task5_values[TASK5_RECEIPT_VALUES];
} capture;

#define scan_file_observations (capture.file.scan)
#define index_file_observations (capture.file.index)
#define path_file_observations (capture.file.scan)
#define prepared_file_queries (capture.file.queries)
#define prepared_file_targets (capture.file.targets)
#define tool_ops (capture.tool.ops)
#define scalar_tool_results (capture.tool.scalar_results)
#define batch_tool_results (capture.tool.batch_results)
#define tool_results (capture.tool.scalar_results)
#define syscall_context_results (capture.context.syscall_records)
#define direct_context_results (capture.context.direct_records)
#define syscall_context_query_results (capture.context.syscall_query_results)
#define direct_context_query_results (capture.context.direct_query_results)
#define context_results (capture.context.syscall_records)

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agenteval_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static uint64 hash_bytes(uint64 hash, const void *address, int length)
{
	const unsigned char *bytes = address;

	for (int i = 0; i < length; i++) {
		hash ^= bytes[i];
		hash *= FNV_PRIME;
	}
	return hash;
}

static int bytes_equal(const void *left, const void *right, int length)
{
	const unsigned char *a = left;
	const unsigned char *b = right;

	for (int i = 0; i < length; i++) {
		if (a[i] != b[i])
			return 0;
	}
	return 1;
}

static uint64 hash_u64(uint64 hash, uint64 value)
{
	for (int i = 0; i < 8; i++) {
		hash ^= (unsigned char)(value & 0xff);
		hash *= FNV_PRIME;
		value >>= 8;
	}
	return hash;
}

static void format_hex16(uint64 value, char text[17]);
static uint64 semantic_token(const char *domain, int load, int pair,
			     int item);

static uint64 functional_values_semantic(const char *domain,
					 const uint64 *values, int count)
{
	uint64 hash = FNV_OFFSET;

	hash = hash_bytes(hash, domain, strlen(domain));
	hash = hash_u64(hash, AGENTEVAL_CHALLENGE);
	for (int i = 0; i < count; i++)
		hash = hash_u64(hash, values[i]);
	return hash;
}

static uint64 functional_receipt(const char *task, const uint64 *values,
				 int count, uint64 semantic)
{
	uint64 hash = FNV_OFFSET;

	hash = hash_bytes(hash, "agentos-functional-receipt-v1",
			  strlen("agentos-functional-receipt-v1"));
	hash = hash_u64(hash, AGENTEVAL_CHALLENGE);
	hash = hash_bytes(hash, task, strlen(task));
	for (int i = 0; i < count; i++)
		hash = hash_u64(hash, values[i]);
	hash = hash_u64(hash, semantic);
	return hash;
}

static void print_functional_values(const uint64 *values, int count)
{
	for (int i = 0; i < count; i++)
		printf("%s%lld", i == 0 ? "" : ",",
		       (long long)values[i]);
}

static void print_launcher_receipt(const uint64 *values, int count,
				   uint64 semantic)
{
	char challenge_text[17];
	char semantic_text[17];
	char receipt_text[17];
	uint64 receipt = functional_receipt("launcher", values, count,
					    semantic);

	format_hex16(AGENTEVAL_CHALLENGE, challenge_text);
	format_hex16(semantic, semantic_text);
	format_hex16(receipt, receipt_text);
	printf("agenteval_ucore: launcher schema=1 challenge=%s values=",
	       challenge_text);
	print_functional_values(values, count);
	printf(" semantic=%s receipt=%s status=ready\n", semantic_text,
	       receipt_text);
}

static void print_functional_receipt(const char *task,
				     const uint64 *values, int count,
				     uint64 semantic)
{
	char challenge_text[17];
	char semantic_text[17];
	char receipt_text[17];
	uint64 receipt = functional_receipt(task, values, count, semantic);

	format_hex16(AGENTEVAL_CHALLENGE, challenge_text);
	format_hex16(semantic, semantic_text);
	format_hex16(receipt, receipt_text);
	printf("agenteval_ucore: functional schema=1 task=%s challenge=%s values=",
	       task, challenge_text);
	print_functional_values(values, count);
	printf(" semantic=%s receipt=%s status=passed\n", semantic_text,
	       receipt_text);
}

static uint64 semantic_token(const char *domain, int load, int pair,
			     int item)
{
	uint64 hash = FNV_OFFSET;

	hash = hash_bytes(hash, domain, strlen(domain));
	hash = hash_u64(hash, AGENTEVAL_CHALLENGE);
	hash = hash_u64(hash, (uint64)load);
	hash = hash_u64(hash, (uint64)pair);
	hash = hash_u64(hash, (uint64)item);
	return hash | (1ULL << 63);
}

static int file_target_gcd(int left, int right)
{
	while (right != 0) {
		int remainder = left % right;

		left = right;
		right = remainder;
	}
	return left;
}

static int file_target_step(int load)
{
	uint64 mixed = AGENTEVAL_CHALLENGE ^
			 (AGENTEVAL_CHALLENGE >> 32);
	int step = (int)(((mixed >> 8) | 1ULL) % (uint64)load);

	if (step == 0)
		step = 1;
	while (file_target_gcd(step, load) != 1) {
		step += 2;
		if (step >= load)
			step = 1;
	}
	return step;
}

static uint64 result_fingerprint_begin(const char *experiment, int load,
				       int pair)
{
	uint64 hash = FNV_OFFSET;

	hash = hash_bytes(hash, "agentos-result-v1",
			  strlen("agentos-result-v1"));
	hash = hash_u64(hash, AGENTEVAL_CHALLENGE);
	hash = hash_bytes(hash, experiment, strlen(experiment));
	hash = hash_u64(hash, (uint64)load);
	hash = hash_u64(hash, (uint64)pair);
	return hash;
}

static uint64 now_us(void)
{
	TimeVal now;

	check(sys_get_time(&now, 0) == 0, "clock syscall");
	return now.sec * 1000000ULL + now.usec;
}

static uint64 elapsed_us(uint64 start, uint64 end)
{
	check(end >= start, "monotonic clock");
	return end - start;
}

static void revisit_write_exact(int fd, const void *buffer, int size)
{
	const char *bytes = buffer;
	int written = 0;

	while (written < size) {
		int count = write(fd, bytes + written, size - written);

		check(count > 0, "revisit pipe write");
		written += count;
	}
}

static void revisit_read_exact(int fd, void *buffer, int size)
{
	char *bytes = buffer;
	int received = 0;

	while (received < size) {
		int count = read(fd, bytes + received, size - received);

		check(count > 0, "revisit pipe read");
		received += count;
	}
}

static uint64 revisit_context_token(int identity)
{
	uint64 hash = FNV_OFFSET;

	hash = hash_bytes(hash, "aios-revisit-context-v1",
			  strlen("aios-revisit-context-v1"));
	hash = hash_u64(hash, AGENTEVAL_CHALLENGE);
	hash = hash_u64(hash, (uint64)(uint)identity);
	return hash | (1ULL << 63);
}

static uint64 revisit_observation_fingerprint(
	int visit, int identity, const struct revisit_reply *reply,
	int correct, int contamination, int return_visit, int fallback)
{
	uint64 hash = FNV_OFFSET;

	hash = hash_bytes(hash, "aios-revisit-observation-v1",
			  strlen("aios-revisit-observation-v1"));
	hash = hash_u64(hash, AGENTEVAL_CHALLENGE);
	hash = hash_u64(hash, (uint64)(uint)visit);
	hash = hash_u64(hash, (uint64)(uint)identity);
	hash = hash_u64(hash, reply->request_id);
	hash = hash_u64(hash, (uint64)(uint)reply->agent_id);
	hash = hash_u64(hash, (uint64)reply->lifecycle_id);
	hash = hash_u64(hash, reply->lifecycle_generation);
	hash = hash_u64(hash, (uint64)(uint)correct);
	hash = hash_u64(hash, (uint64)(uint)contamination);
	hash = hash_u64(hash, (uint64)(uint)return_visit);
	hash = hash_u64(hash, (uint64)(uint)fallback);
	return hash;
}

static uint64 revisit_sample_fingerprint(
	const struct revisit_perf_sample *sample)
{
	uint64 hash = FNV_OFFSET;

	hash = hash_bytes(hash, "agentos-qos-sample-v2",
			  strlen("agentos-qos-sample-v2"));
	hash = hash_u64(hash, AGENTEVAL_CHALLENGE);
	hash = hash_u64(hash, (uint64)(uint)sample->concurrency);
	hash = hash_u64(hash, (uint64)(uint)sample->round);
	hash = hash_u64(hash, (uint64)(uint)sample->slot);
	hash = hash_u64(hash, (uint64)(uint)sample->identity);
	hash = hash_u64(hash, sample->request_id);
	hash = hash_u64(hash, (uint64)(uint)sample->correct);
	hash = hash_u64(hash, (uint64)(uint)sample->contamination);
	hash = hash_u64(hash, (uint64)(uint)sample->fallback);
	hash = hash_u64(hash, (uint64)(uint)sample->isolation_ok);
	hash = hash_u64(hash, sample->submitted_us);
	hash = hash_u64(hash, sample->started_us);
	hash = hash_u64(hash, sample->completed_us);
	hash = hash_u64(hash, sample->received_us);
	hash = hash_u64(hash, sample->wait_us);
	hash = hash_u64(hash, sample->service_us);
	hash = hash_u64(hash, sample->turnaround_us);
	return hash;
}

static int revisit_worker_unique(int identity)
{
	const struct revisit_worker *worker = &revisit_workers[identity];

	for (int other = 0; other < REVISIT_IDENTITIES; other++) {
		const struct revisit_worker *candidate =
			&revisit_workers[other];

		if (other == identity)
			continue;
		if (candidate->agent_id == worker->agent_id ||
		    (candidate->lifecycle_id == worker->lifecycle_id &&
		     candidate->lifecycle_generation ==
			     worker->lifecycle_generation))
			return 0;
	}
	return 1;
}

static int revisit_context_record_identity(
	const struct agent_context_record *record)
{
	for (int identity = 0; identity < REVISIT_IDENTITIES; identity++) {
		uint64 expected = revisit_context_token(identity);

		if (record->request_id == expected &&
		    record->arg0 == (uint64)(uint)identity &&
		    record->value0 == expected &&
		    record->tool_id == AGENT_TOOL_CONTEXT_PUSH &&
		    record->status == AGENT_STATUS_OK &&
		    strncmp(record->payload, "identity-", 9) == 0 &&
		    record->payload[9] == 'A' + identity &&
		    strncmp(record->result, "context-", 8) == 0 &&
		    record->result[8] == 'A' + identity)
			return identity;
	}
	return -1;
}

static void revisit_context_observe(int identity, int returning,
				    struct revisit_reply *reply)
{
	int count;
	int own = 0;
	int contamination = 0;

	memset(&functional_context_header, 0,
	       sizeof(functional_context_header));
	memset(functional_context_records, 0,
	       sizeof(functional_context_records));
	count = context_snapshot(&functional_context_header,
				 functional_context_records,
				 AGENT_CONTEXT_MAX_RECORDS);
	if (count >= 0) {
		for (int i = 0; i < count; i++) {
			const struct agent_context_record *record =
				&functional_context_records[i];
			int observed_identity =
				revisit_context_record_identity(record);

			if (observed_identity == identity)
				own++;
			else if (observed_identity >= 0)
				contamination++;
		}
	}
	reply->correct = count >= 0 && own == 1 && contamination == 0;
	reply->contamination = contamination;
	reply->return_visit = returning && reply->correct;
	reply->fallback = !reply->correct;
}

static void revisit_seed_context(int identity)
{
	struct agent_context_record record;
	uint64 token = revisit_context_token(identity);

	memset(&record, 0, sizeof(record));
	record.request_id = token;
	record.arg0 = (uint64)(uint)identity;
	record.value0 = token;
	record.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	record.status = AGENT_STATUS_OK;
	strcpy(record.payload, "identity-A");
	strcpy(record.result, "context-A");
	record.payload[9] = 'A' + identity;
	record.result[8] = 'A' + identity;
	if (context_push(&record) != AGENT_STATUS_OK) {
		return;
	}
}

static void run_revisit_worker(int command_fd, int reply_fd, int identity)
{
	struct agent_workflow_lifecycle_info lifecycle;
	struct agent_info info;
	struct revisit_command command;
	struct revisit_reply reply;

	memset(&info, 0, sizeof(info));
	memset(&lifecycle, 0, sizeof(lifecycle));
	check(agent_info(&info) == AGENT_STATUS_OK && info.is_agent &&
		      info.agent_role == AGENT_ROLE_ORCHESTRATOR,
	      "revisit workflow Agent identity");
	check(agent_workflow_lifecycle_info(&lifecycle, 0) ==
		      AGENT_STATUS_OK &&
		      lifecycle.version ==
			      AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION &&
		      lifecycle.struct_size == sizeof(lifecycle) &&
		      lifecycle.charged == 1 && lifecycle.key.id != 0 &&
		      lifecycle.key.generation != 0,
	      "revisit workflow lifecycle identity");
	memset(&reply, 0, sizeof(reply));
	reply.magic = REVISIT_REPLY_MAGIC;
	reply.kind = 0;
	reply.identity = identity;
	reply.agent_id = info.agent_id;
	reply.lifecycle_id = lifecycle.key.id;
	reply.lifecycle_generation = lifecycle.key.generation;
	reply.started_us = now_us();
	reply.completed_us = reply.started_us;
	revisit_write_exact(reply_fd, &reply, sizeof(reply));

	for (;;) {
		revisit_read_exact(command_fd, &command, sizeof(command));
		check(command.magic == REVISIT_COMMAND_MAGIC &&
			      command.identity == identity,
		      "revisit command identity");
		if (command.kind == REVISIT_COMMAND_STOP)
			exit(0);
		check(command.kind == REVISIT_COMMAND_VISIT ||
			      command.kind == REVISIT_COMMAND_QUERY,
		      "revisit command kind");
		memset(&reply, 0, sizeof(reply));
		reply.magic = REVISIT_REPLY_MAGIC;
		reply.request_id = command.request_id;
		reply.kind = command.kind;
		reply.identity = identity;
		reply.agent_id = info.agent_id;
		reply.lifecycle_id = lifecycle.key.id;
		reply.lifecycle_generation = lifecycle.key.generation;
		reply.started_us = now_us();
		if (command.kind == REVISIT_COMMAND_VISIT &&
		    command.visit == 1)
			revisit_seed_context(identity);
		revisit_context_observe(
			identity,
			command.kind == REVISIT_COMMAND_VISIT &&
				command.visit > 1,
			&reply);
		reply.completed_us = now_us();
		revisit_write_exact(reply_fd, &reply, sizeof(reply));
	}
}

static void revisit_start_workers(void)
{
	for (int identity = 0; identity < REVISIT_IDENTITIES; identity++) {
		int command_pipe[2];
		int reply_pipe[2];
		struct revisit_reply ready;
		int pid;

		check(pipe(command_pipe) == 0 && pipe(reply_pipe) == 0,
		      "create revisit workflow pipes");
		check(agent_scope_delegate_fd(command_pipe[0]) ==
			      AGENT_STATUS_OK &&
			      agent_scope_delegate_fd(reply_pipe[1]) ==
				      AGENT_STATUS_OK,
		      "delegate revisit workflow pipes");
		pid = identity == 0 ?
			      agent_create_role(AGENT_ROLE_ORCHESTRATOR) :
			      agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
		check(pid >= 0, "create revisit workflow");
		if (pid == 0)
			run_revisit_worker(command_pipe[0], reply_pipe[1],
					   identity);
		check(close(command_pipe[0]) == 0 && close(reply_pipe[1]) == 0,
		      "close revisit child pipe endpoints");
		revisit_workers[identity].pid = pid;
		revisit_workers[identity].command_fd = command_pipe[1];
		revisit_workers[identity].reply_fd = reply_pipe[0];
		revisit_read_exact(reply_pipe[0], &ready, sizeof(ready));
		check(ready.magic == REVISIT_REPLY_MAGIC && ready.kind == 0 &&
			      ready.identity == identity && ready.agent_id > 0 &&
			      ready.lifecycle_id != 0 &&
			      ready.lifecycle_generation != 0,
		      "receive revisit workflow identity");
		revisit_workers[identity].agent_id = ready.agent_id;
		revisit_workers[identity].lifecycle_id = ready.lifecycle_id;
		revisit_workers[identity].lifecycle_generation =
			ready.lifecycle_generation;
	}
}

static void revisit_request(int identity, int kind, int visit,
			    uint64 request_id, struct revisit_reply *reply)
{
	struct revisit_command command;
	const struct revisit_worker *worker = &revisit_workers[identity];

	memset(&command, 0, sizeof(command));
	command.magic = REVISIT_COMMAND_MAGIC;
	command.request_id = request_id;
	command.kind = kind;
	command.identity = identity;
	command.visit = visit;
	revisit_write_exact(worker->command_fd, &command, sizeof(command));
	revisit_read_exact(worker->reply_fd, reply, sizeof(*reply));
	check(reply->magic == REVISIT_REPLY_MAGIC &&
		      reply->request_id == request_id && reply->kind == kind &&
		      reply->identity == identity &&
		      reply->agent_id == worker->agent_id &&
		      reply->lifecycle_id == worker->lifecycle_id &&
		      reply->lifecycle_generation ==
			      worker->lifecycle_generation,
	      "receive revisit workflow reply");
}

static void print_revisit_observation(int visit, int identity,
				      const struct revisit_reply *reply,
				      int correct, int contamination,
				      int return_visit, int fallback,
				      uint64 fingerprint)
{
	char request_text[17];
	char result_text[17];

	format_hex16(reply->request_id, request_text);
	format_hex16(fingerprint, result_text);
	printf("agenteval_ucore: revisit schema=1 visit=%d identity=%c request_id=%s agent_id=%d lifecycle_id=%u lifecycle_generation=%llu correct=%d contamination=%d return_visit=%d fallback=%d result_fingerprint=%s status=observed\n",
	       visit, 'A' + identity, request_text, reply->agent_id,
	       reply->lifecycle_id,
	       (unsigned long long)reply->lifecycle_generation, correct,
	       contamination, return_visit, fallback, result_text);
}

static void run_revisit_visits(void)
{
	uint64 summary_hash = FNV_OFFSET;
	int correct = 0;
	int contamination = 0;
	int return_visit = 0;
	int fallback = 0;

	for (int visit = 0; visit < REVISIT_VISITS; visit++) {
		int identity = revisit_sequence[visit];
		int ordinal = visit == REVISIT_VISITS - 1 ? 2 : 1;
		int unique;
		int observed_correct;
		int observed_return;
		int observed_fallback;
		uint64 request_id = semantic_token(
			"aios-revisit-visit-v1", visit + 1, identity,
			ordinal);

		revisit_request(identity, REVISIT_COMMAND_VISIT, ordinal,
				request_id, &revisit_visits[visit]);
		unique = revisit_worker_unique(identity);
		observed_correct = revisit_visits[visit].correct && unique;
		observed_return = revisit_visits[visit].return_visit && unique;
		observed_fallback = revisit_visits[visit].fallback || !unique;
		revisit_visit_fingerprints[visit] =
			revisit_observation_fingerprint(
				visit + 1, identity, &revisit_visits[visit],
				observed_correct,
				revisit_visits[visit].contamination,
				observed_return, observed_fallback);
		correct += observed_correct;
		contamination += revisit_visits[visit].contamination;
		return_visit += observed_return;
		fallback += observed_fallback;
		print_revisit_observation(
			visit + 1, identity, &revisit_visits[visit],
			observed_correct, revisit_visits[visit].contamination,
			observed_return, observed_fallback,
			revisit_visit_fingerprints[visit]);
	}

	summary_hash = hash_bytes(summary_hash, "aios-revisit-summary-v1",
				  strlen("aios-revisit-summary-v1"));
	summary_hash = hash_u64(summary_hash, AGENTEVAL_CHALLENGE);
	summary_hash = hash_u64(summary_hash, REVISIT_VISITS);
	summary_hash = hash_u64(summary_hash, (uint64)(uint)correct);
	summary_hash = hash_u64(summary_hash, (uint64)(uint)contamination);
	summary_hash = hash_u64(summary_hash, (uint64)(uint)return_visit);
	summary_hash = hash_u64(summary_hash, (uint64)(uint)fallback);
	for (int visit = 0; visit < REVISIT_VISITS; visit++)
		summary_hash = hash_u64(summary_hash,
					revisit_visit_fingerprints[visit]);
	{
		char result_text[17];

		format_hex16(summary_hash, result_text);
		printf("agenteval_ucore: revisit_summary schema=1 visits=%d correct=%d contamination=%d return_visit=%d fallback=%d result_fingerprint=%s status=measured\n",
		       REVISIT_VISITS, correct, contamination, return_visit,
		       fallback, result_text);
	}
}

static uint64 revisit_nearest_rank(const uint64 *values, int count,
				   int percentile)
{
	int rank = (percentile * count + 99) / 100;

	check(count > 0 && rank > 0 && rank <= count,
	      "revisit percentile rank");
	return values[rank - 1];
}

static void revisit_sort_durations(uint64 *values, int count)
{
	for (int i = 1; i < count; i++) {
		uint64 value = values[i];
		int position = i;

		while (position > 0 && values[position - 1] > value) {
			values[position] = values[position - 1];
			position--;
		}
		values[position] = value;
	}
}

static void run_revisit_concurrency(int concurrency)
{
	uint64 start_us;
	uint64 end_us;
	uint64 duration_us;
	uint64 wait_sum_us = 0;
	uint64 service_sum_us = 0;
	uint64 turnaround_sum_us = 0;
	uint64 throughput_milli_rps;
	uint64 goodput_milli_rps;
	uint64 avg_milli_us;
	uint64 p50_us;
	uint64 p90_us;
	uint64 p99_us;
	uint64 wait_avg_milli_us;
	uint64 wait_p50_us;
	uint64 wait_p90_us;
	uint64 wait_p99_us;
	uint64 service_avg_milli_us;
	uint64 service_p50_us;
	uint64 service_p90_us;
	uint64 service_p99_us;
	uint64 fairness_jain_ppm;
	uint64 max_min_fairness_ppm;
	uint64 workload_digest = FNV_OFFSET;
	uint64 summary_hash = FNV_OFFSET;
	int requests = REVISIT_ROUNDS * concurrency;
	int identity_isolated[REVISIT_IDENTITIES] = { 0 };
	int isolated = 0;
	int correct = 0;
	int contamination = 0;
	int fallback = 0;

	check(concurrency == 1 || concurrency == 2 || concurrency == 4,
	      "revisit concurrency level");
	start_us = now_us();
	for (int round = 0; round < REVISIT_ROUNDS; round++) {
		for (int slot = 0; slot < concurrency; slot++) {
			int index = round * concurrency + slot;
			int identity = index % REVISIT_IDENTITIES;
			struct revisit_command command;

			memset(&command, 0, sizeof(command));
			command.magic = REVISIT_COMMAND_MAGIC;
			command.request_id = semantic_token(
				"agentos-qos-request-v2", concurrency,
				round, slot * REVISIT_IDENTITIES + identity);
			command.kind = REVISIT_COMMAND_QUERY;
			command.identity = identity;
			revisit_perf_sent_us[index] = now_us();
			revisit_write_exact(
				revisit_workers[identity].command_fd, &command,
				sizeof(command));
		}
		for (int slot = 0; slot < concurrency; slot++) {
			int index = round * concurrency + slot;
			int identity = index % REVISIT_IDENTITIES;

			revisit_read_exact(
				revisit_workers[identity].reply_fd,
				&revisit_perf_replies[index],
				sizeof(revisit_perf_replies[index]));
			revisit_perf_received_us[index] = now_us();
		}
	}
	end_us = now_us();
	duration_us = elapsed_us(start_us, end_us);
	check(duration_us > 0, "revisit concurrency measurable duration");

	for (int round = 0; round < REVISIT_ROUNDS; round++) {
		for (int slot = 0; slot < concurrency; slot++) {
			int index = round * concurrency + slot;
			int identity = index % REVISIT_IDENTITIES;
			const struct revisit_reply *reply =
				&revisit_perf_replies[index];
			struct revisit_perf_sample *sample =
				&revisit_perf_samples[index];
			uint64 expected_request = semantic_token(
				"agentos-qos-request-v2", concurrency,
				round, slot * REVISIT_IDENTITIES + identity);
			int unique = revisit_worker_unique(identity);

			check(reply->magic == REVISIT_REPLY_MAGIC &&
				      reply->request_id == expected_request &&
				      reply->kind == REVISIT_COMMAND_QUERY &&
				      reply->identity == identity &&
				      reply->agent_id ==
					      revisit_workers[identity].agent_id &&
				      reply->lifecycle_id ==
					      revisit_workers[identity].lifecycle_id &&
				      reply->lifecycle_generation ==
					      revisit_workers[identity]
						      .lifecycle_generation &&
				      reply->started_us >=
					      revisit_perf_sent_us[index] &&
				      reply->completed_us >= reply->started_us &&
				      reply->completed_us <=
					      revisit_perf_received_us[index],
			      "revisit concurrency reply");
			sample->request_id = expected_request;
			sample->submitted_us = revisit_perf_sent_us[index];
			sample->started_us = reply->started_us;
			sample->completed_us = reply->completed_us;
			sample->received_us = revisit_perf_received_us[index];
			sample->wait_us = elapsed_us(sample->submitted_us,
						     sample->started_us);
			sample->service_us = elapsed_us(sample->started_us,
							sample->completed_us);
			sample->turnaround_us = elapsed_us(
				revisit_perf_sent_us[index],
				reply->completed_us);
			check(sample->turnaround_us ==
				      sample->wait_us + sample->service_us,
			      "revisit QoS decomposition");
			sample->concurrency = concurrency;
			sample->round = round;
			sample->slot = slot;
			sample->identity = identity;
			sample->correct = reply->correct && unique;
			sample->contamination = reply->contamination;
			sample->fallback = reply->fallback || !unique;
			sample->isolation_ok = sample->correct &&
					       sample->contamination == 0 &&
					       !sample->fallback;
			sample->result_fingerprint =
				revisit_sample_fingerprint(sample);
			revisit_sorted_wait_us[index] = sample->wait_us;
			revisit_sorted_service_us[index] = sample->service_us;
			revisit_sorted_turnaround_us[index] =
				sample->turnaround_us;
			wait_sum_us += sample->wait_us;
			service_sum_us += sample->service_us;
			turnaround_sum_us += sample->turnaround_us;
			isolated += sample->isolation_ok;
			identity_isolated[identity] += sample->isolation_ok;
			correct += sample->correct;
			contamination += sample->contamination;
			fallback += sample->fallback;
		}
	}
	revisit_sort_durations(revisit_sorted_wait_us, requests);
	revisit_sort_durations(revisit_sorted_service_us, requests);
	revisit_sort_durations(revisit_sorted_turnaround_us, requests);
	throughput_milli_rps = (uint64)requests * 1000000000ULL /
				 duration_us;
	goodput_milli_rps = (uint64)(uint)isolated * 1000000000ULL /
				duration_us;
	avg_milli_us = turnaround_sum_us * 1000ULL / (uint64)requests;
	p50_us = revisit_nearest_rank(revisit_sorted_turnaround_us, requests, 50);
	p90_us = revisit_nearest_rank(revisit_sorted_turnaround_us, requests, 90);
	p99_us = revisit_nearest_rank(revisit_sorted_turnaround_us, requests, 99);
	wait_avg_milli_us = wait_sum_us * 1000ULL / (uint64)requests;
	wait_p50_us = revisit_nearest_rank(revisit_sorted_wait_us, requests, 50);
	wait_p90_us = revisit_nearest_rank(revisit_sorted_wait_us, requests, 90);
	wait_p99_us = revisit_nearest_rank(revisit_sorted_wait_us, requests, 99);
	service_avg_milli_us = service_sum_us * 1000ULL / (uint64)requests;
	service_p50_us = revisit_nearest_rank(revisit_sorted_service_us,
					       requests, 50);
	service_p90_us = revisit_nearest_rank(revisit_sorted_service_us,
					       requests, 90);
	service_p99_us = revisit_nearest_rank(revisit_sorted_service_us,
					       requests, 99);
	{
		uint64 squares = 0;
		int minimum = identity_isolated[0];
		int maximum = identity_isolated[0];

		for (int identity = 0; identity < REVISIT_IDENTITIES; identity++) {
			uint64 value = (uint64)(uint)identity_isolated[identity];

			squares += value * value;
			if (identity_isolated[identity] < minimum)
				minimum = identity_isolated[identity];
			if (identity_isolated[identity] > maximum)
				maximum = identity_isolated[identity];
		}
		fairness_jain_ppm = squares == 0 ? 0 :
			(uint64)(uint)isolated * (uint64)(uint)isolated *
			1000000ULL / (REVISIT_IDENTITIES * squares);
		max_min_fairness_ppm = maximum == 0 ? 0 :
			(uint64)(uint)minimum * 1000000ULL / (uint64)(uint)maximum;
	}
	workload_digest = hash_bytes(workload_digest,
				     "agentos-qos-workload-v2",
				     strlen("agentos-qos-workload-v2"));
	workload_digest = hash_u64(workload_digest, AGENTEVAL_CHALLENGE);
	workload_digest = hash_u64(workload_digest, (uint64)(uint)concurrency);
	for (int index = 0; index < requests; index++) {
		const struct revisit_perf_sample *sample =
			&revisit_perf_samples[index];

		workload_digest = hash_u64(workload_digest,
					   (uint64)(uint)sample->round);
		workload_digest = hash_u64(workload_digest,
					   (uint64)(uint)sample->slot);
		workload_digest = hash_u64(workload_digest,
					   (uint64)(uint)sample->identity);
		workload_digest = hash_u64(workload_digest, sample->request_id);
	}

	for (int index = 0; index < requests; index++) {
		const struct revisit_perf_sample *sample =
			&revisit_perf_samples[index];
		char request_text[17];
		char result_text[17];

		format_hex16(sample->request_id, request_text);
		format_hex16(sample->result_fingerprint, result_text);
		printf("agenteval_ucore: concurrency_sample schema=2 concurrency=%d round=%d slot=%d identity=%c request_id=%s submitted_us=%llu started_us=%llu completed_us=%llu received_us=%llu wait_us=%llu service_us=%llu turnaround_us=%llu correct=%d contamination=%d fallback=%d isolation_ok=%d result_fingerprint=%s status=measured\n",
		       sample->concurrency, sample->round, sample->slot,
		       'A' + sample->identity, request_text,
		       (unsigned long long)sample->submitted_us,
		       (unsigned long long)sample->started_us,
		       (unsigned long long)sample->completed_us,
		       (unsigned long long)sample->received_us,
		       (unsigned long long)sample->wait_us,
		       (unsigned long long)sample->service_us,
		       (unsigned long long)sample->turnaround_us,
		       sample->correct, sample->contamination, sample->fallback,
		       sample->isolation_ok, result_text);
	}

	summary_hash = hash_bytes(summary_hash, "agentos-qos-summary-v2",
				  strlen("agentos-qos-summary-v2"));
	summary_hash = hash_u64(summary_hash, AGENTEVAL_CHALLENGE);
	summary_hash = hash_u64(summary_hash, (uint64)(uint)concurrency);
	summary_hash = hash_u64(summary_hash, REVISIT_ROUNDS);
	summary_hash = hash_u64(summary_hash, (uint64)(uint)requests);
	summary_hash = hash_u64(summary_hash, (uint64)(uint)requests);
	summary_hash = hash_u64(summary_hash, start_us);
	summary_hash = hash_u64(summary_hash, end_us);
	summary_hash = hash_u64(summary_hash, duration_us);
	summary_hash = hash_u64(summary_hash, throughput_milli_rps);
	summary_hash = hash_u64(summary_hash, goodput_milli_rps);
	summary_hash = hash_u64(summary_hash, avg_milli_us);
	summary_hash = hash_u64(summary_hash, p50_us);
	summary_hash = hash_u64(summary_hash, p90_us);
	summary_hash = hash_u64(summary_hash, p99_us);
	summary_hash = hash_u64(summary_hash, wait_avg_milli_us);
	summary_hash = hash_u64(summary_hash, wait_p50_us);
	summary_hash = hash_u64(summary_hash, wait_p90_us);
	summary_hash = hash_u64(summary_hash, wait_p99_us);
	summary_hash = hash_u64(summary_hash, service_avg_milli_us);
	summary_hash = hash_u64(summary_hash, service_p50_us);
	summary_hash = hash_u64(summary_hash, service_p90_us);
	summary_hash = hash_u64(summary_hash, service_p99_us);
	summary_hash = hash_u64(summary_hash, fairness_jain_ppm);
	summary_hash = hash_u64(summary_hash, max_min_fairness_ppm);
	summary_hash = hash_u64(summary_hash, (uint64)(uint)isolated);
	summary_hash = hash_u64(summary_hash, (uint64)(uint)correct);
	summary_hash = hash_u64(summary_hash, (uint64)(uint)contamination);
	summary_hash = hash_u64(summary_hash, (uint64)(uint)fallback);
	summary_hash = hash_u64(summary_hash, workload_digest);
	for (int index = 0; index < requests; index++)
		summary_hash = hash_u64(
			summary_hash,
			revisit_perf_samples[index].result_fingerprint);
	{
		char workload_text[17];
		char result_text[17];

		format_hex16(workload_digest, workload_text);
		format_hex16(summary_hash, result_text);
		printf("agenteval_ucore: concurrency schema=2 concurrency=%d rounds=%d requests=%d completed=%d start_us=%llu end_us=%llu duration_us=%llu throughput_milli_rps=%llu goodput_milli_rps=%llu avg_milli_us=%llu p50_us=%llu p90_us=%llu p99_us=%llu wait_avg_milli_us=%llu wait_p50_us=%llu wait_p90_us=%llu wait_p99_us=%llu service_avg_milli_us=%llu service_p50_us=%llu service_p90_us=%llu service_p99_us=%llu fairness_jain_ppm=%llu max_min_fairness_ppm=%llu isolated=%d correct=%d contamination=%d fallback=%d workload_digest=%s result_fingerprint=%s status=measured\n",
		       concurrency, REVISIT_ROUNDS, requests, requests,
		       (unsigned long long)start_us,
		       (unsigned long long)end_us,
		       (unsigned long long)duration_us,
		       (unsigned long long)throughput_milli_rps,
		       (unsigned long long)goodput_milli_rps,
		       (unsigned long long)avg_milli_us,
		       (unsigned long long)p50_us,
		       (unsigned long long)p90_us,
		       (unsigned long long)p99_us,
		       (unsigned long long)wait_avg_milli_us,
		       (unsigned long long)wait_p50_us,
		       (unsigned long long)wait_p90_us,
		       (unsigned long long)wait_p99_us,
		       (unsigned long long)service_avg_milli_us,
		       (unsigned long long)service_p50_us,
		       (unsigned long long)service_p90_us,
		       (unsigned long long)service_p99_us,
		       (unsigned long long)fairness_jain_ppm,
		       (unsigned long long)max_min_fairness_ppm, isolated, correct,
		       contamination, fallback, workload_text, result_text);
	}
}

static void revisit_stop_workers(void)
{
	for (int identity = 0; identity < REVISIT_IDENTITIES; identity++) {
		struct revisit_command command;
		int status = -1;

		memset(&command, 0, sizeof(command));
		command.magic = REVISIT_COMMAND_MAGIC;
		command.kind = REVISIT_COMMAND_STOP;
		command.identity = identity;
		revisit_write_exact(revisit_workers[identity].command_fd,
				    &command, sizeof(command));
		check(close(revisit_workers[identity].command_fd) == 0 &&
			      close(revisit_workers[identity].reply_fd) == 0,
		      "close revisit parent pipe endpoints");
		check(waitpid(revisit_workers[identity].pid, &status) ==
			      revisit_workers[identity].pid &&
			      status == 0,
		      "wait revisit workflow");
	}
}

static void run_revisit_evaluation(void)
{
	revisit_start_workers();
	run_revisit_visits();
	for (int index = 0; index < REVISIT_CONCURRENCY_LEVELS; index++)
		run_revisit_concurrency(revisit_concurrency_levels[index]);
	revisit_stop_workers();
}

static int pair_runs_ab(int pair)
{
	return (pair & 1) == (AGENTEVAL_CHALLENGE & 1);
}

static uint64 workload_fingerprint(const char *experiment, int load,
				   int pair, int operations, uint64 selector)
{
	uint64 hash = FNV_OFFSET;

	hash = hash_u64(hash, AGENTEVAL_CHALLENGE);
	hash = hash_bytes(hash, experiment, strlen(experiment));
	hash = hash_u64(hash, (uint64)load);
	hash = hash_u64(hash, (uint64)pair);
	hash = hash_u64(hash, (uint64)operations);
	hash = hash_u64(hash, selector);
	return hash;
}

static void format_hex16(uint64 value, char text[17])
{
	static const char digits[] = "0123456789abcdef";

	for (int i = 15; i >= 0; i--) {
		text[i] = digits[value & 0xf];
		value >>= 4;
	}
	text[16] = 0;
}

static void print_sample(const char *experiment, int load, int pair,
			 const char *variant, const char *order,
			 const char *cache, int operations, int dataset_size,
			 uint64 workload_hash,
			 const struct measurement *measurement)
{
	char workload_text[17];
	char result_text[17];

	format_hex16(workload_hash, workload_text);
	format_hex16(measurement->result_fingerprint, result_text);
	printf("agenteval_ucore: sample schema=2 experiment=%s load=%d pair=%d variant=%s order=%s cache=%s operations=%d dataset_size=%d work_units=%llu records_examined=%llu result_items=%llu duration_us=%llu index_rebuild_records=%llu result_cache_hits=%llu workload_fingerprint=%s result_fingerprint=%s status=measured\n",
	       experiment, load, pair, variant, order, cache, operations,
	       dataset_size,
	       (unsigned long long)measurement->work_units,
	       (unsigned long long)measurement->records_examined,
	       (unsigned long long)measurement->result_items,
	       (unsigned long long)measurement->duration_us,
	       (unsigned long long)measurement->index_rebuild_records,
	       (unsigned long long)measurement->result_cache_hits,
	       workload_text, result_text);
}

static void make_code(char *out, char prefix, int number)
{
	out[0] = prefix;
	out[1] = '0' + (number / 100) % 10;
	out[2] = '0' + (number / 10) % 10;
	out[3] = '0' + number % 10;
	out[4] = 0;
}

static uint64 eval_file_record_hash(const struct eval_file_record *record)
{
	uint64 hash = FNV_OFFSET;

	hash = hash_u64(hash, record->magic);
	hash = hash_u64(hash, record->schema);
	hash = hash_u64(hash, record->challenge);
	hash = hash_u64(hash, (uint64)(uint)record->fid);
	hash = hash_bytes(hash, record->physical_name,
			  sizeof(record->physical_name));
	hash = hash_bytes(hash, record->logical_path,
			  sizeof(record->logical_path));
	hash = hash_bytes(hash, record->project, sizeof(record->project));
	hash = hash_bytes(hash, record->workflow, sizeof(record->workflow));
	hash = hash_bytes(hash, record->run_id, sizeof(record->run_id));
	hash = hash_bytes(hash, record->stage, sizeof(record->stage));
	hash = hash_bytes(hash, record->kind, sizeof(record->kind));
	hash = hash_bytes(hash, record->status, sizeof(record->status));
	hash = hash_bytes(hash, record->summary, sizeof(record->summary));
	hash = hash_u64(hash, record->dependency_mask);
	return hash;
}

static void eval_file_record_build(struct eval_file_record *record, int index)
{
	memset(record, 0, sizeof(*record));
	record->magic = EVAL_FILE_RECORD_MAGIC;
	record->schema = EVAL_FILE_RECORD_SCHEMA;
	record->challenge = AGENTEVAL_CHALLENGE;
	record->fid = 2000 + index;
	make_code(record->physical_name, 'e', index);
	strcpy(record->logical_path, record->physical_name);
	strcpy(record->project, "eval");
	strcpy(record->workflow, "comparison");
	strcpy(record->run_id, "RUN-EVAL");
	strcpy(record->stage, "query");
	strcpy(record->kind, "artifact");
	make_code(record->status, 'q', index);
	strcpy(record->summary, "measured evaluation fixture");
	record->dependency_mask = agent_dependency_label_bit("ready");
	record->record_hash = eval_file_record_hash(record);
}

static int eval_file_record_valid(const struct eval_file_record *record,
				  int expected_index)
{
	char name[8];
	char status[8];

	make_code(name, 'e', expected_index);
	make_code(status, 'q', expected_index);
	return record->magic == EVAL_FILE_RECORD_MAGIC &&
	       record->schema == EVAL_FILE_RECORD_SCHEMA &&
	       record->challenge == AGENTEVAL_CHALLENGE &&
	       record->fid == 2000 + expected_index &&
	       strcmp(record->physical_name, name) == 0 &&
	       strcmp(record->logical_path, name) == 0 &&
	       strcmp(record->project, "eval") == 0 &&
	       strcmp(record->workflow, "comparison") == 0 &&
	       strcmp(record->run_id, "RUN-EVAL") == 0 &&
	       strcmp(record->stage, "query") == 0 &&
	       strcmp(record->kind, "artifact") == 0 &&
	       strcmp(record->status, status) == 0 &&
	       strcmp(record->summary, "measured evaluation fixture") == 0 &&
	       record->dependency_mask == agent_dependency_label_bit("ready") &&
	       record->record_hash == eval_file_record_hash(record);
}

static int eval_file_field_matches(const char *selector, const char *value)
{
	return selector[0] == 0 || strcmp(selector, value) == 0;
}

static int eval_file_record_matches_query(
	const struct eval_file_record *record,
	const struct agent_file_query *query)
{
	return eval_file_field_matches(query->physical_name,
				       record->physical_name) &&
	       eval_file_field_matches(query->logical_path,
				       record->logical_path) &&
	       eval_file_field_matches(query->project, record->project) &&
	       eval_file_field_matches(query->workflow, record->workflow) &&
	       eval_file_field_matches(query->run_id, record->run_id) &&
	       eval_file_field_matches(query->stage, record->stage) &&
	       eval_file_field_matches(query->kind, record->kind) &&
	       eval_file_field_matches(query->status, record->status) &&
	       query->summary_contains[0] == 0;
}

static void eval_file_record_project(const struct eval_file_record *record,
				     const Stat *status,
				     struct agent_file_hit *hit)
{
	memset(hit, 0, sizeof(*hit));
	hit->fid = record->fid;
	strcpy(hit->physical_name, record->physical_name);
	strcpy(hit->logical_path, record->logical_path);
	strcpy(hit->stage, record->stage);
	strcpy(hit->kind, record->kind);
	strcpy(hit->status, record->status);
	strcpy(hit->summary, record->summary);
	hit->dependency_mask = record->dependency_mask;
	hit->dev = status->dev;
	hit->inum = status->ino;
	hit->size = sizeof(*record);
}

static void seed_file_metadata(int first, int limit)
{
	struct eval_file_record record;

	for (int i = first; i < limit; i++) {
		int fd;

		eval_file_record_build(&record, i);
		fd = open(record.physical_name, O_CREATE | O_RDWR | O_TRUNC);
		check(fd >= 0, "create evaluation file");
		check(write(fd, &record, sizeof(record)) == (ssize_t)sizeof(record),
		      "write evaluation file");
		check(close(fd) == 0, "close evaluation file");
		memset(&file_meta, 0, sizeof(file_meta));
		file_meta.fid = record.fid;
		strcpy(file_meta.physical_name, record.physical_name);
		strcpy(file_meta.logical_path, record.logical_path);
		strcpy(file_meta.project, record.project);
		strcpy(file_meta.workflow, record.workflow);
		strcpy(file_meta.run_id, record.run_id);
		strcpy(file_meta.stage, record.stage);
		strcpy(file_meta.kind, record.kind);
		strcpy(file_meta.status, record.status);
		strcpy(file_meta.summary, record.summary);
		file_meta.dependency_mask = record.dependency_mask;
		check(agent_file_meta_set(&file_meta) == 0,
		      "seed evaluation metadata");
	}
}

static int census_visible_file_records(void)
{
	char challenge[17];
	int returned;

	memset(&file_query, 0, sizeof(file_query));
	file_query.flags = AGENT_FILE_QUERY_SCAN;
	file_query.max_hits = 1;
	strcpy(file_query.physical_name, "census-");
	format_hex16(AGENTEVAL_CHALLENGE, file_query.physical_name + 7);
	returned = agent_file_query(&file_query, &file_result);
	check(returned == 0 && file_result.total_hits == 0 &&
		      file_result.returned == 0 && !file_result.truncated,
	      "metadata census selector remains absent");
	check(file_result.used_index == 0 &&
		      file_result.plan == AGENT_FILE_QUERY_PLAN_SCAN &&
		      (file_result.plan_reason &
		       AGENT_FILE_QUERY_REASON_FORCED_SCAN) != 0,
	      "metadata census uses forced scan");
	check(file_result.scanned_records >= 0 &&
		      file_result.scanned_records <= AGENT_FILE_META_MAX &&
		      file_result.candidate_records == file_result.scanned_records &&
		      file_result.index_rebuild_records == 0,
	      "metadata census reports visible live work");
	format_hex16(AGENTEVAL_CHALLENGE, challenge);
	check(strcmp(file_query.physical_name + 7, challenge) == 0,
	      "metadata census binds boot challenge");
	return file_result.candidate_records;
}

static void prepare_file_query(struct agent_file_query *query,
			       int target_meta)
{
	char status[8];

	make_code(status, 'q', target_meta);
	memset(query, 0, sizeof(*query));
	query->max_hits = 1;
	strcpy(query->project, "eval");
	strcpy(query->workflow, "comparison");
	strcpy(query->run_id, "RUN-EVAL");
	strcpy(query->stage, "query");
	strcpy(query->status, status);
}

static void prepare_file_query_workload(int load, int pair, int operations)
{
	uint64 mixed = AGENTEVAL_CHALLENGE ^
			 (AGENTEVAL_CHALLENGE >> 32);
	int start = (int)(mixed % (uint64)load);
	int step = file_target_step(load);
	int first = pair == 0 ? start :
		    (start + (pair - 1) * step) % load;

	check(operations > 0 && operations <= EVAL_FILE_QUERIES &&
		      operations <= load,
	      "file query operation count");
	for (int i = 0; i < operations; i++) {
		prepared_file_targets[i] = (first + i * step) % load;
		prepare_file_query(&prepared_file_queries[i],
				   prepared_file_targets[i]);
		for (int prior = 0; prior < i; prior++)
			check(prepared_file_targets[prior] !=
				      prepared_file_targets[i],
			      "file query targets are unique");
	}
}

static void set_file_query_flags(int operations, uint64 flags)
{
	for (int i = 0; i < operations; i++)
		prepared_file_queries[i].flags = flags;
}

static uint64 file_workload_fingerprint(const char *experiment, int load,
					int pair, int operations)
{
	uint64 selector = FNV_OFFSET;
	char name[8];

	selector = hash_bytes(selector, "agentos-file-manifest-v1",
			      strlen("agentos-file-manifest-v1"));
	selector = hash_u64(selector, AGENTEVAL_CHALLENGE);
	selector = hash_u64(selector, (uint64)(uint)load);
	for (int i = 0; i < load; i++) {
		make_code(name, 'e', i);
		selector = hash_bytes(selector, name, strlen(name));
	}
	for (int i = 0; i < operations; i++)
		selector = hash_u64(selector,
				    (uint64)(uint)prepared_file_targets[i]);
	return workload_fingerprint(experiment, load, pair, operations,
				    selector);
}

static void capture_file_observation(struct file_observation *observation,
				     int syscall_result)
{
	memset(observation, 0, sizeof(*observation));
	observation->syscall_result = syscall_result;
	observation->total_hits = file_result.total_hits;
	observation->returned = file_result.returned;
	observation->truncated = file_result.truncated;
	observation->scanned_records = file_result.scanned_records;
	observation->used_index = file_result.used_index;
	observation->plan = file_result.plan;
	observation->candidate_records = file_result.candidate_records;
	observation->index_rebuild_records = file_result.index_rebuild_records;
	observation->plan_reason = file_result.plan_reason;
	observation->fs_generation = file_result.fs_generation;
	if (file_result.returned > 0)
		observation->hit = file_result.hits[0];
}

static uint64 hash_file_semantics(uint64 hash,
				  const struct file_observation *observation)
{
	const struct agent_file_hit *hit = &observation->hit;

	hash = hash_u64(hash, (uint64)(uint)observation->syscall_result);
	hash = hash_u64(hash, (uint64)(uint)observation->total_hits);
	hash = hash_u64(hash, (uint64)(uint)observation->returned);
	hash = hash_u64(hash, (uint64)(uint)observation->truncated);
	hash = hash_u64(hash, (uint64)(uint)hit->fid);
	hash = hash_bytes(hash, hit->physical_name, strlen(hit->physical_name));
	hash = hash_bytes(hash, hit->logical_path, strlen(hit->logical_path));
	hash = hash_bytes(hash, hit->stage, strlen(hit->stage));
	hash = hash_bytes(hash, hit->kind, strlen(hit->kind));
	hash = hash_bytes(hash, hit->status, strlen(hit->status));
	hash = hash_bytes(hash, hit->summary, strlen(hit->summary));
	hash = hash_u64(hash, hit->dependency_mask);
	return hash;
}

static void time_file_contest_variant(int load, int operations, int path_walk,
				      struct file_observation *observations,
				      struct measurement *measurement)
{
	uint64 start;

	memset(measurement, 0, sizeof(*measurement));
	memset(observations, 0,
	       (uint)operations * sizeof(observations[0]));
	set_file_query_flags(operations,
		path_walk ? 0 : AGENT_FILE_QUERY_USE_INDEX);
	start = now_us();
	for (int operation = 0; operation < operations; operation++) {
		struct file_observation *observation = &observations[operation];

		if (path_walk) {
			for (int item = 0; item < load; item++) {
				struct eval_file_record record;
				Stat status;
				char name[8];
				ssize_t bytes = -1;
				int stat_result = -1;
				int close_result = -1;
				int fd;

				memset(&record, 0, sizeof(record));
				memset(&status, 0, sizeof(status));
				make_code(name, 'e', item);
				observation->scanned_records++;
				fd = open(name, O_RDONLY);
				if (fd >= 0) {
					bytes = read(fd, &record, sizeof(record));
					stat_result = fstat(fd, &status);
					close_result = close(fd);
				}
				if (bytes > 0)
					observation->path_bytes_read +=
						(uint64)bytes;
				if (fd < 0 || bytes != (ssize_t)sizeof(record) ||
				    stat_result != 0 || close_result != 0 ||
				    !eval_file_record_valid(&record, item)) {
					observation->path_failures++;
				} else {
					observation->candidate_records++;
					if (eval_file_record_matches_query(
						    &record,
						    &prepared_file_queries[operation])) {
						observation->total_hits++;
						if (observation->returned == 0) {
							eval_file_record_project(
								&record, &status,
								&observation->hit);
							observation->path_record_hash =
								record.record_hash;
							observation->returned = 1;
						} else {
							observation->truncated = 1;
						}
					}
				}
			}
			observation->syscall_result = observation->returned;
		} else {
			int result = agent_file_query(
				&prepared_file_queries[operation], &file_result);

			capture_file_observation(observation, result);
		}
	}
	measurement->duration_us = elapsed_us(start, now_us());
}

static void time_file_table_variant(int operations, int use_index,
				    struct file_observation *observations,
				    struct measurement *measurement)
{
	uint64 start;

	memset(measurement, 0, sizeof(*measurement));
	memset(observations, 0,
	       (uint)operations * sizeof(observations[0]));
	set_file_query_flags(operations, use_index ?
		AGENT_FILE_QUERY_USE_INDEX : AGENT_FILE_QUERY_SCAN);
	start = now_us();
	for (int operation = 0; operation < operations; operation++) {
		int result = agent_file_query(&prepared_file_queries[operation],
					      &file_result);

		capture_file_observation(&observations[operation], result);
	}
	measurement->duration_us = elapsed_us(start, now_us());
}

static int file_hit_matches_record(const struct agent_file_hit *hit,
				   const struct eval_file_record *record)
{
	return hit->fid == record->fid &&
	       strcmp(hit->physical_name, record->physical_name) == 0 &&
	       strcmp(hit->logical_path, record->logical_path) == 0 &&
	       strcmp(hit->stage, record->stage) == 0 &&
	       strcmp(hit->kind, record->kind) == 0 &&
	       strcmp(hit->status, record->status) == 0 &&
	       strcmp(hit->summary, record->summary) == 0 &&
	       hit->dependency_mask == record->dependency_mask;
}

static void validate_index_physical_identity(
	struct file_observation *observation,
	const struct agent_file_query *query, int expected_index)
{
	struct eval_file_record record;
	Stat status;
	int fd = open(observation->hit.physical_name, O_RDONLY);

	memset(&record, 0, sizeof(record));
	memset(&status, 0, sizeof(status));
	check(fd >= 0, "open indexed physical file");
	check(read(fd, &record, sizeof(record)) == (ssize_t)sizeof(record),
	      "read indexed physical file");
	check(fstat(fd, &status) == 0, "stat indexed physical file");
	check(close(fd) == 0, "close indexed physical file");
	check(eval_file_record_valid(&record, expected_index) &&
		      eval_file_record_matches_query(&record, query) &&
		      file_hit_matches_record(&observation->hit, &record),
	      "indexed hit matches challenge record");
	check(observation->hit.dev == status.dev &&
		      observation->hit.inum == status.ino &&
		      observation->hit.size == sizeof(record),
	      "indexed hit matches physical identity");
	observation->path_record_hash = record.record_hash;
	observation->path_bytes_read = sizeof(record);
}

static void validate_agent_file_observation(
	const struct file_observation *observation, int target_meta, int use_index)
{
	struct eval_file_record expected;

	eval_file_record_build(&expected, target_meta);
	check(observation->syscall_result == 1 &&
		      observation->total_hits == 1 &&
		      observation->returned == 1 && !observation->truncated &&
		      file_hit_matches_record(&observation->hit, &expected),
	      "file query semantic result");
	check(observation->hit.dev != 0 && observation->hit.inum != 0 &&
		      observation->hit.incarnation != 0 &&
		      observation->hit.size == sizeof(struct eval_file_record),
	      "file query is bound to a real inode");
	if (use_index) {
		check(observation->used_index == 1 &&
			      observation->plan ==
				      AGENT_FILE_QUERY_PLAN_STATUS_INDEX &&
			      (observation->plan_reason &
			       AGENT_FILE_QUERY_REASON_STATUS_INDEX) != 0,
		      "ready status index plan");
		check((observation->plan_reason &
		       AGENT_FILE_QUERY_REASON_CACHE_HIT) == 0 &&
			      observation->index_rebuild_records == 0,
		      "warm index excludes cache and rebuild");
	} else {
		check(observation->used_index == 0 &&
			      observation->plan == AGENT_FILE_QUERY_PLAN_SCAN &&
			      (observation->plan_reason &
			       AGENT_FILE_QUERY_REASON_FORCED_SCAN) != 0,
		      "forced metadata table scan plan");
	}
	check(observation->candidate_records > 0 &&
		      observation->scanned_records > 0 &&
		      observation->candidate_records <=
			      observation->scanned_records,
	      "file candidates are bounded by charged work");
}

static void finalize_agent_file_variant(
	const char *experiment, int load, int pair, int operations, int use_index,
	int validate_physical, struct file_observation *observations,
	struct measurement *measurement)
{
	measurement->work_units = 0;
	measurement->records_examined = 0;
	measurement->result_items = 0;
	measurement->index_rebuild_records = 0;
	measurement->result_cache_hits = 0;
	measurement->result_fingerprint = result_fingerprint_begin(
		experiment, load, pair);
	for (int i = 0; i < operations; i++) {
		struct file_observation *observation = &observations[i];

		validate_agent_file_observation(
			observation, prepared_file_targets[i], use_index);
		if (!use_index)
			check(observation->candidate_records ==
				      expected_visible_file_records,
			      "metadata scan visible census remains stable");
		if (validate_physical)
			validate_index_physical_identity(
				observation, &prepared_file_queries[i],
				prepared_file_targets[i]);
		measurement->work_units += observation->scanned_records;
		measurement->records_examined += observation->candidate_records;
		measurement->result_items += observation->total_hits;
		measurement->index_rebuild_records +=
			(uint64)(uint)observation->index_rebuild_records;
		if ((observation->plan_reason &
		     AGENT_FILE_QUERY_REASON_CACHE_HIT) != 0)
			measurement->result_cache_hits++;
		measurement->result_fingerprint = hash_file_semantics(
			measurement->result_fingerprint, observation);
	}
	check(measurement->records_examined > 0 &&
		      measurement->records_examined <= measurement->work_units,
	      "file candidate accounting is bounded by charged work");
	check(measurement->index_rebuild_records == 0 &&
		      measurement->result_cache_hits == 0,
	      "timed file query excludes rebuild and result cache");
	if (use_index) {
		check(measurement->work_units >= (uint64)(uint)operations &&
			      measurement->records_examined >=
				      (uint64)(uint)operations,
		      "indexed query reports real candidate work");
	} else {
		check(measurement->work_units ==
			      (uint64)(uint)expected_visible_file_records *
				      (uint64)(uint)operations &&
			      measurement->records_examined ==
				      measurement->work_units,
		      "metadata scan reports all visible live work");
	}
}

static void finalize_path_file_variant(
	const char *experiment, int load, int pair, int operations,
	const struct file_observation *observations,
	struct measurement *measurement)
{
	measurement->work_units = 0;
	measurement->records_examined = 0;
	measurement->result_items = 0;
	measurement->index_rebuild_records = 0;
	measurement->result_cache_hits = 0;
	measurement->result_fingerprint = result_fingerprint_begin(
		experiment, load, pair);
	for (int i = 0; i < operations; i++) {
		const struct file_observation *observation = &observations[i];

		check(observation->path_failures == 0 &&
			      observation->scanned_records == load &&
			      observation->candidate_records == load &&
			      observation->path_bytes_read ==
				      (uint64)(uint)load *
				      sizeof(struct eval_file_record),
		      "path walk checks every fixture file");
		check(observation->syscall_result == 1 &&
			      observation->total_hits == 1 &&
			      observation->returned == 1 &&
			      !observation->truncated &&
			      observation->hit.fid ==
				      2000 + prepared_file_targets[i] &&
			      observation->hit.dev != 0 &&
			      observation->hit.inum != 0 &&
			      observation->path_record_hash != 0,
		      "path walk returns one physical result");
		measurement->work_units += observation->scanned_records;
		measurement->records_examined += observation->candidate_records;
		measurement->result_items += observation->total_hits;
		measurement->result_fingerprint = hash_file_semantics(
			measurement->result_fingerprint, observation);
	}
	check(measurement->work_units ==
		      (uint64)(uint)load * (uint64)(uint)operations &&
		      measurement->records_examined == measurement->work_units &&
		      measurement->result_items == (uint64)(uint)operations,
	      "path walk accounting uses actual N files");
}

static void check_path_index_equivalence(
	const struct file_observation *path,
	const struct file_observation *index, int operations,
	const struct measurement *path_measurement,
	const struct measurement *index_measurement)
{
	check(path_measurement->result_fingerprint ==
		      index_measurement->result_fingerprint,
	      "path and index semantic fingerprints agree");
	for (int i = 0; i < operations; i++)
		check(path[i].hit.dev == index[i].hit.dev &&
			      path[i].hit.inum == index[i].hit.inum &&
			      path[i].hit.size == index[i].hit.size &&
			      path[i].path_record_hash ==
				      index[i].path_record_hash,
		      "path and index physical identities agree");
}

static void rebuild_file_index_diagnostic(const char *experiment, int load)
{
	struct file_observation observation;
	const char *cache;
	char workload_text[17];
	char result_text[17];
	uint64 workload;
	uint64 result_fingerprint;
	uint64 start;
	uint64 duration;
	uint64 work_units;
	uint64 result_cache_hits;
	int result;

	prepare_file_query_workload(load, 0, 1);
	set_file_query_flags(1, AGENT_FILE_QUERY_USE_INDEX);
	start = now_us();
	result = agent_file_query(&prepared_file_queries[0], &file_result);
	duration = elapsed_us(start, now_us());
	capture_file_observation(&observation, result);
	check(observation.syscall_result == 1 && observation.total_hits == 1 &&
		      observation.returned == 1 &&
		      observation.hit.fid ==
			      2000 + prepared_file_targets[0],
	      "cold index semantic result");
	check(observation.used_index == 1 &&
		      observation.plan == AGENT_FILE_QUERY_PLAN_STATUS_INDEX,
	      "cold status index plan");
	result_cache_hits =
		(observation.plan_reason & AGENT_FILE_QUERY_REASON_CACHE_HIT) != 0;
	check(result_cache_hits == 0,
	      "index readiness probe excludes query cache");
	cache = observation.index_rebuild_records > 0 ? "cold-rebuild" :
							       "ready";
	work_units = observation.index_rebuild_records > 0 ?
			     (uint64)(uint)observation.index_rebuild_records :
			     (uint64)(uint)observation.scanned_records;
	workload = file_workload_fingerprint(experiment, load, 0, 1);
	result_fingerprint = result_fingerprint_begin(experiment, load, 0);
	result_fingerprint = hash_file_semantics(result_fingerprint,
						 &observation);
	format_hex16(workload, workload_text);
	format_hex16(result_fingerprint, result_text);
	printf("agenteval_ucore: diagnostic schema=2 experiment=%s load=%d cache=%s operations=1 dataset_size=%d work_units=%llu result_items=%d duration_us=%llu index_rebuild_records=%d result_cache_hits=%llu workload_fingerprint=%s result_fingerprint=%s status=measured\n",
	       experiment, load, cache, load, (unsigned long long)work_units,
	       observation.total_hits, (unsigned long long)duration,
	       observation.index_rebuild_records,
	       (unsigned long long)result_cache_hits, workload_text, result_text);
}

static void run_file_query_path_index(int load, int operations)
{
	const char *experiment = "file_query_path_index";
	struct measurement path;
	struct measurement index;

	rebuild_file_index_diagnostic(experiment, load);
	prepare_file_query_workload(load, 0, 1);
	time_file_contest_variant(load, 1, 1,
				  path_file_observations, &path);
	time_file_contest_variant(load, 1, 0,
				  index_file_observations, &index);
	finalize_path_file_variant(experiment, load, 0, 1,
				   path_file_observations, &path);
	finalize_agent_file_variant(experiment, load, 0, 1, 1, 1,
				    index_file_observations, &index);
	check_path_index_equivalence(path_file_observations,
				     index_file_observations, 1,
				     &path, &index);

	for (int pair = 1; pair <= EVAL_PAIRS; pair++) {
		const char *order = pair_runs_ab(pair) ? "AB" : "BA";
		uint64 workload;

		prepare_file_query_workload(load, pair, operations);
		workload = file_workload_fingerprint(
			experiment, load, pair, operations);
		if (pair_runs_ab(pair)) {
			time_file_contest_variant(load, operations, 1,
					  path_file_observations, &path);
			time_file_contest_variant(load, operations, 0,
					  index_file_observations, &index);
		} else {
			time_file_contest_variant(load, operations, 0,
					  index_file_observations, &index);
			time_file_contest_variant(load, operations, 1,
					  path_file_observations, &path);
		}
		finalize_path_file_variant(experiment, load, pair, operations,
					   path_file_observations, &path);
		finalize_agent_file_variant(experiment, load, pair, operations,
					    1, 1, index_file_observations,
					    &index);
		check_path_index_equivalence(path_file_observations,
					     index_file_observations, operations,
					     &path, &index);
		if (pair_runs_ab(pair)) {
			print_sample(experiment, load, pair, "path_walk", order,
				     "warm-paths", operations, load, workload,
				     &path);
			print_sample(experiment, load, pair, "index", order,
				     "ready-index", operations, load, workload,
				     &index);
		} else {
			print_sample(experiment, load, pair, "index", order,
				     "ready-index", operations, load, workload,
				     &index);
			print_sample(experiment, load, pair, "path_walk", order,
				     "warm-paths", operations, load, workload,
				     &path);
		}
	}
}

static void run_file_query_table_ablation(int load)
{
	const char *experiment = "file_query_table_ablation";
	struct measurement scan;
	struct measurement index;

	rebuild_file_index_diagnostic(experiment, load);
	prepare_file_query_workload(load, 0, FIGURE_HIT_COUNT);
	time_file_table_variant(FIGURE_HIT_COUNT, 0,
				scan_file_observations, &scan);
	time_file_table_variant(FIGURE_HIT_COUNT, 1,
				index_file_observations, &index);
	finalize_agent_file_variant(experiment, load, 0, FIGURE_HIT_COUNT,
				    0, 0, scan_file_observations, &scan);
	finalize_agent_file_variant(experiment, load, 0, FIGURE_HIT_COUNT,
				    1, 0, index_file_observations, &index);
	check(scan.result_fingerprint == index.result_fingerprint,
	      "metadata table warmup equivalence");

	for (int pair = 1; pair <= EVAL_PAIRS; pair++) {
		const char *order = pair_runs_ab(pair) ? "AB" : "BA";
		uint64 workload;

		prepare_file_query_workload(load, pair, FIGURE_HIT_COUNT);
		workload = file_workload_fingerprint(
			experiment, load, pair, FIGURE_HIT_COUNT);
		if (pair_runs_ab(pair)) {
			time_file_table_variant(FIGURE_HIT_COUNT, 0,
						scan_file_observations, &scan);
			time_file_table_variant(FIGURE_HIT_COUNT, 1,
						index_file_observations, &index);
		} else {
			time_file_table_variant(FIGURE_HIT_COUNT, 1,
						index_file_observations, &index);
			time_file_table_variant(FIGURE_HIT_COUNT, 0,
						scan_file_observations, &scan);
		}
		finalize_agent_file_variant(
			experiment, load, pair, FIGURE_HIT_COUNT, 0, 0,
			scan_file_observations, &scan);
		finalize_agent_file_variant(
			experiment, load, pair, FIGURE_HIT_COUNT, 1, 0,
			index_file_observations, &index);
		check(scan.result_fingerprint == index.result_fingerprint,
		      "metadata table pair equivalence");
		if (pair_runs_ab(pair)) {
			print_sample(experiment, load, pair, "scan", order,
				     "forced-scan", FIGURE_HIT_COUNT, load,
				     workload, &scan);
			print_sample(experiment, load, pair, "index", order,
				     "ready-index", FIGURE_HIT_COUNT, load,
				     workload, &index);
		} else {
			print_sample(experiment, load, pair, "index", order,
				     "ready-index", FIGURE_HIT_COUNT, load,
				     workload, &index);
			print_sample(experiment, load, pair, "scan", order,
				     "forced-scan", FIGURE_HIT_COUNT, load,
				     workload, &scan);
		}
	}
}

static int path_operations_for_load(int load)
{
	for (int i = 0; i < EVAL_PATH_LOADS; i++)
		if (eval_path_loads[i] == load)
			return eval_path_operations[i];
	return 0;
}

static int table_experiment_has_load(int load)
{
	for (int i = 0; i < EVAL_LOADS; i++)
		if (eval_loads[i] == load)
			return 1;
	return 0;
}

static void run_file_query_experiment(void)
{
	int seeded = 0;

	ambient_file_records = census_visible_file_records();
	check(ambient_file_records <= AGENT_FILE_META_MAX - EVAL_MAX_LOAD,
	      "metadata census leaves fixture capacity");

	for (int i = 0; i < EVAL_UNION_LOADS; i++) {
		int load = eval_union_loads[i];
		int path_operations;
		int before_seed = census_visible_file_records();
		int after_seed;

		check(before_seed == ambient_file_records + seeded,
		      "metadata census stable before fixture seed");
		seed_file_metadata(seeded, load);
		after_seed = census_visible_file_records();
		check(after_seed - before_seed == load - seeded &&
			      after_seed == ambient_file_records + load,
		      "metadata census observes exact fixture seed delta");
		expected_visible_file_records = after_seed;
		seeded = load;
		path_operations = path_operations_for_load(load);
		if (path_operations > 0)
			run_file_query_path_index(load, path_operations);
		if (table_experiment_has_load(load))
			run_file_query_table_ablation(load);
	}
}

static void prepare_tool_workload(int load, int pair)
{
	memset(scalar_tool_results, 0,
	       (uint)load * sizeof(scalar_tool_results[0]));
	memset(batch_tool_results, 0,
	       (uint)load * sizeof(batch_tool_results[0]));
	for (int i = 0; i < load; i++) {
		memset(&tool_ops[i], 0, sizeof(tool_ops[i]));
		tool_ops[i].version = AGENT_CALL_VERSION;
		tool_ops[i].tool_id = AGENT_TOOL_ECHO;
		tool_ops[i].request_id = semantic_token(
			"tool-request-v1", load, pair, i);
		tool_ops[i].arg0 = AGENTEVAL_CHALLENGE ^
				       ((uint64)(uint)pair << 32) ^ (uint64)i;
		tool_ops[i].arg1 = ((uint64)(uint)load << 32) |
				       (uint64)(uint)pair;
		strcpy(tool_ops[i].payload, "agenteval");
	}
}

static uint64 hash_tool_results(int load, int pair,
				const struct agent_result *results)
{
	uint64 hash = result_fingerprint_begin("tool_batch", load, pair);
	uint64 first_sequence = results[0].sequence;

	check(first_sequence != 0, "echo result sequence starts");

	for (int i = 0; i < load; i++) {
		const struct agent_result *result = &results[i];
		uint64 relative_sequence;

		check(result->version == AGENT_CALL_VERSION &&
			      result->status == AGENT_STATUS_OK &&
			      result->tool_id == AGENT_TOOL_ECHO,
		      "echo result status");
		check(result->request_id == tool_ops[i].request_id &&
			      result->value0 ==
				      (uint64)strlen(tool_ops[i].payload) &&
			      result->value1 == tool_ops[i].arg0 &&
			      result->value2 == tool_ops[i].arg1 &&
			      strcmp(result->result, tool_ops[i].payload) == 0,
			      "echo semantic result");
		check(result->sequence >= first_sequence,
		      "echo result sequence does not regress");
		relative_sequence = result->sequence - first_sequence;
		check(relative_sequence == (uint64)i,
		      "echo result sequence is contiguous");
		hash = hash_u64(hash, result->request_id);
		hash = hash_u64(hash, tool_ops[i].arg0);
		hash = hash_u64(hash, tool_ops[i].arg1);
		hash = hash_u64(hash, result->value0);
		hash = hash_u64(hash, result->value1);
		hash = hash_u64(hash, result->value2);
		hash = hash_bytes(hash, result->result, strlen(result->result));
	}
	return hash;
}

static void time_tool_variant(int load, int batch,
			      struct agent_result *results,
			      struct measurement *measurement)
{
	uint64 start;
	int completed = 0;

	memset(measurement, 0, sizeof(*measurement));
	start = now_us();
	while (completed < load) {
		int count = batch ? load - completed : 1;
		int result;

		if (count > (int)AGENT_BATCH_MAX)
			count = AGENT_BATCH_MAX;
		result = agent_run(&tool_ops[completed],
				   &results[completed], count, 0);
		if (result <= 0 || result > count)
			break;
		completed += result;
	}
	measurement->duration_us = elapsed_us(start, now_us());
	measurement->work_units = (uint64)(uint)completed;
}

static void finalize_tool_variant(int load, int pair,
				  const struct agent_result *results,
				  struct measurement *measurement)
{
	check(measurement->work_units == (uint64)load, "agent_run progress");
	measurement->records_examined = 0;
	measurement->result_items = measurement->work_units;
	measurement->index_rebuild_records = 0;
	measurement->result_cache_hits = 0;
	measurement->result_fingerprint = hash_tool_results(load, pair, results);
}

static void run_tool_batch_experiment(void)
{
	for (int load_index = 0; load_index < EVAL_LOADS; load_index++) {
		int load = eval_loads[load_index];
		struct measurement scalar;
		struct measurement batch;

		prepare_tool_workload(load, 0);
		time_tool_variant(load, 0, scalar_tool_results, &scalar);
		time_tool_variant(load, 1, batch_tool_results, &batch);
		finalize_tool_variant(load, 0, scalar_tool_results, &scalar);
		finalize_tool_variant(load, 0, batch_tool_results, &batch);
		check(scalar.result_fingerprint == batch.result_fingerprint,
		      "tool warmup equivalence");

		for (int pair = 1; pair <= EVAL_PAIRS; pair++) {
			const char *order = pair_runs_ab(pair) ? "AB" : "BA";
			uint64 workload;

			prepare_tool_workload(load, pair);
			workload = workload_fingerprint("tool_batch", load,
						   pair, load,
						   AGENT_TOOL_ECHO);
			if (pair_runs_ab(pair)) {
				time_tool_variant(load, 0, scalar_tool_results,
						  &scalar);
				time_tool_variant(load, 1, batch_tool_results,
						  &batch);
			} else {
				time_tool_variant(load, 1, batch_tool_results,
						  &batch);
				time_tool_variant(load, 0, scalar_tool_results,
						  &scalar);
			}
			finalize_tool_variant(load, pair, scalar_tool_results,
					      &scalar);
			finalize_tool_variant(load, pair, batch_tool_results,
					      &batch);
			check(scalar.result_fingerprint == batch.result_fingerprint,
			      "tool pair equivalence");
			check(scalar.work_units == batch.work_units &&
				      scalar.work_units == (uint64)load,
			      "tool pair completed work");
			if (pair_runs_ab(pair)) {
				print_sample("tool_batch", load, pair, "scalar",
					     order, "warm", load, 0,
					     workload, &scalar);
				print_sample("tool_batch", load, pair, "batch",
					     order, "warm", load, 0,
					     workload, &batch);
			} else {
				print_sample("tool_batch", load, pair, "batch",
					     order, "warm", load, 0,
					     workload, &batch);
				print_sample("tool_batch", load, pair, "scalar",
					     order, "warm", load, 0,
					     workload, &scalar);
			}
		}
	}
}

static uint64 context_fixture_arg0(int load, int pair)
{
	return AGENTEVAL_CHALLENGE ^ ((uint64)(uint)load << 32) ^
	       (uint64)(uint)pair;
}

static uint64 context_fixture_arg1(int load, int pair)
{
	return ((uint64)(uint)pair << 32) | (uint64)(uint)load;
}

static uint64 prepare_context_fixture(int load, int pair)
{
	struct agent_op op;
	struct agent_result result;
	struct agent_context_header header;

	memset(&op, 0, sizeof(op));
	memset(&result, 0, sizeof(result));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = AGENT_TOOL_ECHO;
	op.request_id = semantic_token("context-request-v1", load, pair, 0);
	op.arg0 = context_fixture_arg0(load, pair);
	op.arg1 = context_fixture_arg1(load, pair);
	strcpy(op.payload, "context-eval");
	check(agent_run(&op, &result, 1, 0) == 1,
	      "create challenge-bound context fixture");
	check(result.status == AGENT_STATUS_OK &&
		      result.request_id == op.request_id &&
		      result.value0 == strlen(op.payload) &&
		      result.value1 == op.arg0 && result.value2 == op.arg1 &&
		      strcmp(result.result, op.payload) == 0,
	      "context fixture semantic result");
	check(context_direct_header_snapshot(eval_info.context_base, &header) == 0 &&
		      result.sequence != 0 &&
		      header.latest_sequence == result.sequence,
	      "context fixture is the visible latest record");
	return result.sequence;
}

static uint64 hash_context_results(
	int load, int pair, uint64 target_sequence,
	const struct agent_context_record *results)
{
	uint64 hash = result_fingerprint_begin("context_access", load, pair);
	uint64 request_id = semantic_token("context-request-v1", load, pair, 0);
	uint64 arg0 = context_fixture_arg0(load, pair);
	uint64 arg1 = context_fixture_arg1(load, pair);

	for (int i = 0; i < load; i++) {
		const struct agent_context_record *record = &results[i];

		check(record->sequence == target_sequence &&
			      record->record_hash != 0,
		      "context semantic result");
		check(record->request_id == request_id &&
			      record->arg0 == arg0 &&
			      record->value0 == strlen("context-eval") &&
			      record->value1 == arg0 && record->value2 == arg1 &&
			      record->tool_id == AGENT_TOOL_ECHO &&
			      record->status == AGENT_STATUS_OK &&
			      strcmp(record->payload, "context-eval") == 0 &&
			      strcmp(record->result, "context-eval") == 0,
		      "context fixture remains challenge-bound");
		hash = hash_u64(hash, record->request_id);
		hash = hash_u64(hash, record->arg0);
		hash = hash_u64(hash, record->value0);
		hash = hash_u64(hash, record->value1);
		hash = hash_u64(hash, record->value2);
		hash = hash_bytes(hash, record->payload, strlen(record->payload));
		hash = hash_bytes(hash, record->result, strlen(record->result));
	}
	return hash;
}

static void prepare_context_capture(int load)
{
	memset(syscall_context_results, 0,
	       (uint)load * sizeof(syscall_context_results[0]));
	memset(direct_context_results, 0,
	       (uint)load * sizeof(direct_context_results[0]));
	memset(syscall_context_query_results, 0,
	       (uint)load * sizeof(syscall_context_query_results[0]));
	memset(direct_context_query_results, 0,
	       (uint)load * sizeof(direct_context_query_results[0]));
}

static void time_context_variant(
	int load, int direct, uint64 target_sequence,
	struct agent_context_record *results, int *query_results,
	struct measurement *measurement)
{
	uint64 start;

	memset(measurement, 0, sizeof(*measurement));
	start = now_us();
	for (int i = 0; i < load; i++) {
		if (direct) {
			query_results[i] = context_direct_active_query(
				eval_info.context_base, target_sequence,
				&results[i], 1);
		} else {
			query_results[i] = context_query(target_sequence,
							 &results[i], 1);
		}
	}
	measurement->duration_us = elapsed_us(start, now_us());
}

static void finalize_context_variant(
	int load, int pair, uint64 target_sequence,
	const struct agent_context_record *results, const int *query_results,
	struct measurement *measurement)
{
	measurement->work_units = 0;
	measurement->records_examined = 0;
	for (int i = 0; i < load; i++) {
		check(query_results[i] == 1, "context query result");
		measurement->work_units += (uint64)(uint)query_results[i];
	}
	measurement->result_items = measurement->work_units;
	measurement->index_rebuild_records = 0;
	measurement->result_cache_hits = 0;
	measurement->result_fingerprint = hash_context_results(
		load, pair, target_sequence, results);
}

static void validate_context_mirror(uint64 target_sequence)
{
	memset(context_results, 0, sizeof(context_results));
	check(context_query(target_sequence, &context_results[0], 1) == 1,
	      "validated context syscall query");
	check(context_direct_active_query(eval_info.context_base, target_sequence,
					  &context_results[1], 1) == 1,
	      "validated mapped context query");
	check(bytes_equal(&context_results[0], &context_results[1],
			  sizeof(context_results[0])),
	      "mapped context matches kernel query");
}

static void run_context_access_experiment(void)
{
	check(agent_info(&eval_info) == 0, "context agent info");
	check(eval_info.context_base != 0 &&
		      eval_info.context_size == AGENT_CONTEXT_SIZE,
	      "mapped context available");

	for (int load_index = 0; load_index < EVAL_LOADS; load_index++) {
		volatile struct agent_context_header *header =
			(volatile struct agent_context_header *)eval_info.context_base;
		int load = eval_loads[load_index];
		uint64 target_sequence;
		struct measurement syscall_query;
		struct measurement direct;

		check(header->magic == AGENT_CONTEXT_MAGIC &&
			      header->version == AGENT_CONTEXT_VERSION &&
			      header->latest_sequence != 0,
			      "context header contract");
		target_sequence = prepare_context_fixture(load, 0);
		validate_context_mirror(target_sequence);
		prepare_context_capture(load);
		time_context_variant(load, 0, target_sequence,
				     syscall_context_results,
				     syscall_context_query_results,
				     &syscall_query);
		time_context_variant(load, 1, target_sequence,
				     direct_context_results,
				     direct_context_query_results, &direct);
		finalize_context_variant(load, 0, target_sequence,
					 syscall_context_results,
					 syscall_context_query_results,
					 &syscall_query);
		finalize_context_variant(load, 0, target_sequence,
					 direct_context_results,
					 direct_context_query_results, &direct);
		check(syscall_query.result_fingerprint == direct.result_fingerprint,
		      "context warmup equivalence");
		validate_context_mirror(target_sequence);

		for (int pair = 1; pair <= EVAL_PAIRS; pair++) {
			const char *order = pair_runs_ab(pair) ? "AB" : "BA";
			uint64 workload = workload_fingerprint(
				"context_access", load, pair, load,
				EVAL_CONTEXT_SELECTOR);

			target_sequence = prepare_context_fixture(load, pair);
			validate_context_mirror(target_sequence);
			prepare_context_capture(load);
			if (pair_runs_ab(pair)) {
				time_context_variant(
					load, 0, target_sequence,
					syscall_context_results,
					syscall_context_query_results,
					&syscall_query);
				time_context_variant(
					load, 1, target_sequence,
					direct_context_results,
					direct_context_query_results, &direct);
			} else {
				time_context_variant(
					load, 1, target_sequence,
					direct_context_results,
					direct_context_query_results, &direct);
				time_context_variant(
					load, 0, target_sequence,
					syscall_context_results,
					syscall_context_query_results,
					&syscall_query);
			}
			finalize_context_variant(
				load, pair, target_sequence,
				syscall_context_results,
				syscall_context_query_results, &syscall_query);
			finalize_context_variant(
				load, pair, target_sequence, direct_context_results,
				direct_context_query_results, &direct);
			check(syscall_query.result_fingerprint ==
				      direct.result_fingerprint,
			      "context pair equivalence");
			check(syscall_query.work_units == direct.work_units &&
				      syscall_query.work_units == (uint64)load,
			      "context pair completed work");
			validate_context_mirror(target_sequence);
			if (pair_runs_ab(pair)) {
				print_sample("context_access", load, pair,
					     "syscall", order, "warm", load, 0,
					     workload, &syscall_query);
				print_sample("context_access", load, pair,
					     "direct", order, "warm", load, 0,
					     workload, &direct);
			} else {
				print_sample("context_access", load, pair,
					     "direct", order, "warm", load, 0,
					     workload, &direct);
				print_sample("context_access", load, pair,
					     "syscall", order, "warm", load, 0,
					     workload, &syscall_query);
			}
		}
	}
}

static void run_functional_task1(void)
{
	volatile struct agent_context_header *header;
	volatile uint64 *cache;
	uint64 values[19];
	uint64 direct_token;
	uint64 semantic;

	check(agent_info(&eval_info) == 0, "task1 agent info");
	check(eval_info.is_agent == 1 &&
		      eval_info.agent_role == AGENT_ROLE_ORCHESTRATOR,
	      "task1 trusted Agent identity");
	check(eval_info.context_base != 0 &&
		      eval_info.context_size == AGENT_CONTEXT_SIZE,
	      "task1 context mapping");
	header = (volatile struct agent_context_header *)eval_info.context_base;
	check(header->magic == AGENT_CONTEXT_MAGIC &&
		      header->version == AGENT_CONTEXT_VERSION &&
		      header->capacity == AGENT_CONTEXT_MAX_RECORDS,
	      "task1 direct context header read");
	check(header->user_cache_size >= sizeof(*cache) &&
		      header->user_cache_offset + sizeof(*cache) <=
			      eval_info.context_size,
	      "task1 writable context cache");
	cache = (volatile uint64 *)(eval_info.context_base +
				   header->user_cache_offset);
	direct_token = AGENTEVAL_CHALLENGE ^ (uint64)(uint)getpid() ^
		       eval_info.context_base;
	*cache = direct_token;
	check(*cache == direct_token, "task1 direct context cache write");

	values[0] = (uint64)(uint)getpid();
	values[1] = (uint64)(uint)getppid();
	values[2] = (uint64)(uint)eval_info.is_agent;
	values[3] = (uint64)(uint)eval_info.agent_role;
	values[4] = (uint64)(uint)eval_info.agent_id;
	values[5] = eval_info.context_base;
	values[6] = eval_info.context_size;
	values[7] = header->magic;
	values[8] = header->version;
	values[9] = header->capacity;
	values[10] = (uint64)(uint)eval_info.resource_quota;
	values[11] = (uint64)(uint)eval_info.loop_state;
	values[12] = header->user_cache_offset;
	values[13] = header->user_cache_size;
	values[14] = direct_token;
	values[15] = (uint64)(uint)functional_compat_sentinel_pid;
	values[16] = (uint64)(long long)functional_compat_sentinel_status;
	values[17] = AGENT_ROLE_SENTINEL;
	values[18] = 1;
	semantic = functional_values_semantic("task1-semantic-v1", values,
					      19);
	print_functional_receipt("task1", values, 19, semantic);
}

static void functional_param_uint(uint index, const char *key, uint64 value)
{
	check(index < AGENT_TOOL_PARAM_MAX, "task2 uint parameter index");
	memset(&functional_params[index], 0,
	       sizeof(functional_params[index]));
	functional_params[index].version = AGENT_PARAM_VERSION;
	functional_params[index].size = sizeof(functional_params[index]);
	functional_params[index].type = AGENT_PARAM_UINT64;
	functional_params[index].value_size =
		sizeof(functional_params[index].value.uint64_value);
	strcpy(functional_params[index].key, key);
	functional_params[index].value.uint64_value = value;
}

static void functional_param_string(uint index, const char *key,
				    const char *value)
{
	check(index < AGENT_TOOL_PARAM_MAX, "task2 string parameter index");
	check(strlen(value) < AGENT_PARAM_STRING_SIZE,
	      "task2 string parameter size");
	memset(&functional_params[index], 0,
	       sizeof(functional_params[index]));
	functional_params[index].version = AGENT_PARAM_VERSION;
	functional_params[index].size = sizeof(functional_params[index]);
	functional_params[index].type = AGENT_PARAM_STRING;
	functional_params[index].value_size = strlen(value) + 1;
	strcpy(functional_params[index].key, key);
	strcpy(functional_params[index].value.string_value, value);
}

static void functional_request_init(int tool_id, const char *name,
				    uint param_count, uint64 request_id)
{
	memset(&functional_request, 0, sizeof(functional_request));
	memset(&functional_response, 0, sizeof(functional_response));
	functional_request.version = AGENT_CALL_VERSION_V2;
	functional_request.size = sizeof(functional_request);
	functional_request.tool_id = tool_id;
	functional_request.param_count = param_count;
	functional_request.request_id = request_id;
	functional_request.params = param_count ? (uint64)functional_params : 0;
	if (name != 0)
		strcpy(functional_request.tool_name, name);
}

static void functional_tool_call(const char *message)
{
	check(tool_call(&functional_request, &functional_response) == 0,
	      message);
	check(functional_response.version == AGENT_CALL_VERSION_V2 &&
		      functional_response.size == sizeof(functional_response) &&
		      functional_response.request_id ==
			      functional_request.request_id,
	      "task2 V2 response envelope");
}

struct functional_required_tool_schema {
	int tool_id;
	uint param_count;
	uint64 flags;
	const char *name;
	const char *params;
};

static const struct functional_required_tool_schema functional_required_tools[] = {
	{ AGENT_TOOL_ECHO, 3, AGENT_TOOL_F_CALLABLE, "echo",
	  "payload:string,arg0:uint64,arg1:uint64" },
	{ AGENT_TOOL_QUERY_PROCESS, 1, AGENT_TOOL_F_CALLABLE, "query_process",
	  "type?:uint64" },
	{ AGENT_TOOL_CAPABILITY_CHECK, 2, AGENT_TOOL_F_CALLABLE,
	  "capability_check", "role:uint64,action:string" },
};

static int functional_bounded_text_length(const char *text, int capacity)
{
	for (int i = 0; i < capacity; i++)
		if (text[i] == 0)
			return i;
	return -1;
}

static int functional_schema_param_count(const char *schema)
{
	int length = functional_bounded_text_length(
		schema, AGENT_TOOL_PARAMS_SIZE);
	int count = 1;
	int colon = 0;
	int segment = 0;

	if (length < 0 || length == 0)
		return -1;
	if (strcmp(schema, "none") == 0)
		return 0;
	for (int i = 0; i <= length; i++) {
		char character = schema[i];

		if (character == ':') {
			if (colon || segment == 0)
				return -1;
			colon = 1;
		} else if (character == ',' || character == 0) {
			if (!colon || segment == 0)
				return -1;
			colon = 0;
			segment = 0;
			if (character == ',')
				count++;
		} else {
			segment++;
		}
	}
	return count <= (int)AGENT_TOOL_PARAM_MAX ? count : -1;
}

static const struct agent_tool_desc_v2 *functional_tool_desc(int tool_id)
{
	for (int i = 0; i < functional_tool_count; i++)
		if (functional_tools[i].tool_id == tool_id)
			return &functional_tools[i];
	return 0;
}

static uint64 functional_core_schema_hash(uint64 *required_mask)
{
	uint64 hash = FNV_OFFSET;
	uint64 mask = 0;

	hash = hash_bytes(hash, "task2-tool-schema-v1",
			  strlen("task2-tool-schema-v1"));
	for (uint i = 0;
	     i < sizeof(functional_required_tools) /
			 sizeof(functional_required_tools[0]);
	     i++) {
		const struct functional_required_tool_schema *required =
			&functional_required_tools[i];
		const struct agent_tool_desc_v2 *desc =
			functional_tool_desc(required->tool_id);

		check(desc != 0 && desc->param_count == required->param_count &&
			      desc->flags == required->flags &&
			      strcmp(desc->name, required->name) == 0 &&
			      strcmp(desc->params, required->params) == 0,
		      "task2 required core schema");
		mask |= 1ULL << i;
		hash = hash_u64(hash, (uint64)(uint)desc->tool_id);
		hash = hash_u64(hash, (uint64)desc->param_count);
		hash = hash_u64(hash, desc->flags);
		hash = hash_bytes(hash, desc->name, strlen(desc->name));
		hash = hash_bytes(hash, desc->params, strlen(desc->params));
	}
	*required_mask = mask;
	return hash;
}

static uint64 functional_catalog_load(int *callable_count,
				      uint64 *required_mask,
				      uint64 *core_schema_hash)
{
	uint64 hash = FNV_OFFSET;
	int capacity = sizeof(functional_tools) / sizeof(functional_tools[0]);

	memset(functional_tools, 0, sizeof(functional_tools));
	functional_tool_count = tool_list(functional_tools, 0);
	check(functional_tool_count > 0 && functional_tool_count <= capacity,
	      "task2 bounded catalog count");
	check(tool_list(functional_tools, capacity) == functional_tool_count,
	      "task2 complete versioned catalog");
	*callable_count = 0;
	hash = hash_bytes(hash, "task2-tool-catalog-v1",
			  strlen("task2-tool-catalog-v1"));
	hash = hash_u64(hash, AGENTEVAL_CHALLENGE);
	hash = hash_u64(hash, FUNCTIONAL_TOOL_CATALOG_SCHEMA);
	hash = hash_u64(hash, AGENT_CALL_VERSION_V2);
	hash = hash_u64(hash, (uint64)(uint)functional_tool_count);
	for (int i = 0; i < functional_tool_count; i++) {
		const struct agent_tool_desc_v2 *desc = &functional_tools[i];
		int name_length = functional_bounded_text_length(
			desc->name, sizeof(desc->name));
		int params_length = functional_bounded_text_length(
			desc->params, sizeof(desc->params));
		int description_length = functional_bounded_text_length(
			desc->description, sizeof(desc->description));

		check(desc->version == AGENT_CALL_VERSION_V2 &&
			      desc->size == sizeof(*desc) && desc->tool_id > 0 &&
			      desc->param_count <= AGENT_TOOL_PARAM_MAX &&
			      (desc->flags == AGENT_TOOL_F_CALLABLE ||
			       desc->flags == AGENT_TOOL_F_SYSCALL_ONLY) &&
			      name_length > 0 && params_length > 0 &&
			      description_length > 0 &&
			      functional_schema_param_count(desc->params) ==
				      (int)desc->param_count,
		      "task2 catalog descriptor schema");
		for (int other = 0; other < i; other++)
			check(functional_tools[other].tool_id != desc->tool_id &&
				      strcmp(functional_tools[other].name,
					     desc->name) != 0,
			      "task2 unique catalog identity");
		if ((desc->flags & AGENT_TOOL_F_CALLABLE) != 0)
			(*callable_count)++;
		hash = hash_u64(hash, (uint64)(uint)desc->tool_id);
		hash = hash_u64(hash, desc->version);
		hash = hash_u64(hash, desc->size);
		hash = hash_u64(hash, desc->param_count);
		hash = hash_u64(hash, desc->flags);
		hash = hash_bytes(hash, desc->name, name_length);
		hash = hash_bytes(hash, desc->params, params_length);
		printf("agenteval_ucore: catalog schema=%d challenge=",
		       FUNCTIONAL_TOOL_CATALOG_SCHEMA);
		{
			char challenge_text[17];

			format_hex16(AGENTEVAL_CHALLENGE, challenge_text);
			printf("%s", challenge_text);
		}
		printf(" index=%d total=%d abi=%u tool_id=%d flags=%llu param_count=%u name=%s params=%s status=listed\n",
		       i, functional_tool_count, AGENT_CALL_VERSION_V2,
		       desc->tool_id, (unsigned long long)desc->flags,
		       desc->param_count, desc->name, desc->params);
	}
	*core_schema_hash = functional_core_schema_hash(required_mask);
	return hash;
}

static uint64 functional_response_text_hash(void)
{
	return hash_bytes(FNV_OFFSET, functional_response.result,
			  strlen(functional_response.result));
}

static void run_functional_task2(void)
{
	static const char echo_payload[] = "eval-v2";
	uint64 values[33];
	uint64 echo_arg0 = AGENTEVAL_CHALLENGE ^ (uint64)(uint)getpid();
	uint64 echo_arg1 = AGENTEVAL_CHALLENGE ^ 0xa5a5a5a5a5a5a5a5ULL;
	uint64 catalog_hash;
	uint64 core_schema_hash;
	uint64 echo_payload_hash;
	uint64 required_mask;
	uint64 semantic;
	int callable_count = 0;

	catalog_hash = functional_catalog_load(&callable_count, &required_mask,
					       &core_schema_hash);
	echo_payload_hash = hash_bytes(FNV_OFFSET, echo_payload,
				       strlen(echo_payload));

	functional_param_string(0, "payload", echo_payload);
	functional_param_uint(1, "arg0", echo_arg0);
	functional_param_uint(2, "arg1", echo_arg1);
	functional_request_init(
		AGENT_TOOL_ECHO, "echo", 3,
		semantic_token("task2-call-v1", 2, 0, 0));
	functional_tool_call("task2 echo V2 call");
	check(functional_response.status == AGENT_STATUS_OK &&
		      functional_response.tool_id == AGENT_TOOL_ECHO &&
		      functional_response.value0 == strlen(echo_payload) &&
		      functional_response.value1 == echo_arg0 &&
		      functional_response.value2 == echo_arg1 &&
		      strcmp(functional_response.result, echo_payload) == 0,
	      "task2 echo semantics");
	values[8] = functional_response.sequence;
	values[9] = functional_response.value0;
	values[10] = functional_response.value1;
	values[11] = functional_response.value2;
	values[12] = echo_payload_hash;

	functional_param_uint(0, "type", AGENT_TYPE_AGENT);
	functional_request_init(
		AGENT_TOOL_QUERY_PROCESS, "query_process", 1,
		semantic_token("task2-call-v1", 2, 0, 1));
	functional_tool_call("task2 query_process V2 call");
	check(functional_response.status == AGENT_STATUS_OK &&
		      functional_response.tool_id == AGENT_TOOL_QUERY_PROCESS &&
		      functional_response.value0 >= 1 &&
		      functional_response.value1 >= 1 &&
		      functional_response.value2 >= 1 &&
		      functional_response.value2 <= functional_response.value0 &&
		      strcmp(functional_response.result, "query_process") == 0,
	      "task2 query_process semantics");
	values[13] = functional_response.sequence;
	values[14] = functional_response.value0;
	values[15] = functional_response.value1;
	values[16] = functional_response.value2;

	functional_param_uint(0, "role", AGENT_ROLE_ORCHESTRATOR);
	functional_param_string(1, "action", "orchestrate");
	functional_request_init(
		AGENT_TOOL_CAPABILITY_CHECK, "capability_check", 2,
		semantic_token("task2-call-v1", 2, 0, 2));
	functional_tool_call("task2 capability_check V2 call");
	check(functional_response.status == AGENT_STATUS_OK &&
		      functional_response.tool_id ==
			      AGENT_TOOL_CAPABILITY_CHECK &&
		      functional_response.value0 == 1 &&
		      functional_response.value1 == AGENT_ROLE_ORCHESTRATOR &&
		      (functional_response.value2 & AGENT_CAP_ORCHESTRATE) != 0 &&
		      strcmp(functional_response.result, "allow") == 0,
	      "task2 capability semantics");
	values[17] = functional_response.sequence;
	values[18] = functional_response.value0;
	values[19] = functional_response.value1;
	values[20] = functional_response.value2;

	functional_request_init(
		0, "eval_missing_tool", 0,
		semantic_token("task2-call-v1", 2, 0, 3));
	functional_tool_call("task2 unknown tool V2 call");
	check(functional_response.status == AGENT_STATUS_UNKNOWN_TOOL &&
		      functional_response.sequence == 0 &&
		      strcmp(functional_response.result, "unknown_tool") == 0,
	      "task2 unknown tool rejected");
	values[21] = functional_response.sequence;
	values[22] = (uint64)(long long)functional_response.status;
	values[23] = functional_response_text_hash();

	functional_request_init(
		AGENT_TOOL_ECHO, "pid_info", 0,
		semantic_token("task2-call-v1", 2, 0, 4));
	functional_tool_call("task2 mismatched tool V2 call");
	check(functional_response.status == AGENT_STATUS_BAD_REQUEST &&
		      functional_response.sequence == 0 &&
		      strcmp(functional_response.result, "tool_mismatch") == 0,
	      "task2 mismatched tool rejected");
	values[24] = functional_response.sequence;
	values[25] = (uint64)(long long)functional_response.status;
	values[26] = functional_response_text_hash();

	functional_param_uint(0, "arg0", echo_arg0);
	functional_param_uint(1, "arg0", echo_arg1);
	functional_param_string(2, "payload", echo_payload);
	functional_request_init(
		AGENT_TOOL_ECHO, "echo", 3,
		semantic_token("task2-call-v1", 2, 0, 5));
	functional_tool_call("task2 duplicate parameter V2 call");
	check(functional_response.status == AGENT_STATUS_DUPLICATE &&
		      functional_response.sequence == 0 &&
		      strcmp(functional_response.result, "duplicate_param") == 0,
	      "task2 duplicate parameter rejected");
	values[27] = functional_response.sequence;
	values[28] = (uint64)(long long)functional_response.status;
	values[29] = functional_response_text_hash();

	functional_param_string(0, "arg0", "wrong");
	functional_param_uint(1, "arg1", echo_arg1);
	functional_param_string(2, "payload", echo_payload);
	functional_request_init(
		AGENT_TOOL_ECHO, "echo", 3,
		semantic_token("task2-call-v1", 2, 0, 6));
	functional_tool_call("task2 wrong type V2 call");
	check(functional_response.status == AGENT_STATUS_BAD_TYPE &&
		      functional_response.sequence == 0 &&
		      strcmp(functional_response.result, "bad_param_type") == 0,
	      "task2 wrong type rejected");
	values[30] = functional_response.sequence;
	values[31] = (uint64)(long long)functional_response.status;
	values[32] = functional_response_text_hash();

	values[0] = FUNCTIONAL_TOOL_CATALOG_SCHEMA;
	values[1] = AGENT_CALL_VERSION_V2;
	values[2] = (uint64)(uint)functional_tool_count;
	values[3] = (uint64)(uint)callable_count;
	values[4] = sizeof(functional_required_tools) /
		    sizeof(functional_required_tools[0]);
	values[5] = required_mask;
	values[6] = catalog_hash;
	values[7] = core_schema_hash;
	semantic = functional_values_semantic("task2-semantic-v1", values,
					      33);
	print_functional_receipt("task2", values, 33, semantic);
}

static void prepare_functional_task3_tool(struct agent_op *op, int index)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = AGENT_TOOL_ECHO;
	op->request_id = semantic_token("task3-tool-v1",
					FUNCTIONAL_TASK3_ROUNDS, 0, index);
	op->arg0 = AGENTEVAL_CHALLENGE ^ (uint64)(uint)index;
	op->arg1 = ((uint64)FUNCTIONAL_TASK3_ROUNDS << 32) |
		   (uint64)(uint)index;
	strcpy(op->payload, "ctx-tool");
}

static void check_functional_task3_result(const struct agent_op *op,
					  const struct agent_result *result,
					  uint64 sequence)
{
	check(result->version == AGENT_CALL_VERSION &&
		      result->status == AGENT_STATUS_OK &&
		      result->tool_id == AGENT_TOOL_ECHO &&
		      result->request_id == op->request_id &&
		      result->sequence == sequence &&
		      result->value0 == strlen(op->payload) &&
		      result->value1 == op->arg0 &&
		      result->value2 == op->arg1 &&
		      strcmp(result->result, op->payload) == 0,
	      "task3 production tool result");
}

static void check_functional_task3_record(
	const struct agent_context_record *record, const struct agent_op *op,
	uint64 sequence, uint64 parent_sequence, uint64 branch_generation)
{
	check(record->sequence == sequence &&
		      record->request_id == op->request_id &&
		      record->branch_generation == branch_generation &&
		      record->path_parent_sequence == parent_sequence &&
		      record->arg0 == op->arg0 &&
		      record->value0 == strlen(op->payload) &&
		      record->value1 == op->arg0 &&
		      record->value2 == op->arg1 &&
		      (record->flags & ~AGENT_CONTEXT_PROVENANCE_MASK) ==
			      AGENT_CONTEXT_RECORD_F_SYSTEM &&
		      (AGENT_CONTEXT_PROVENANCE_DECODE(record->flags) &
		       AGENT_PROVENANCE_AGENT_DERIVED) != 0 &&
		      record->record_hash != 0 &&
		      record->tool_id == AGENT_TOOL_ECHO &&
		      record->status == AGENT_STATUS_OK &&
		      strcmp(record->payload, op->payload) == 0 &&
		      strcmp(record->result, op->payload) == 0,
	      "task3 production Context record");
}

static uint64 functional_task3_record_hash(
	uint64 hash, const struct agent_context_record *record)
{
	hash = hash_u64(hash, record->sequence);
	hash = hash_u64(hash, record->request_id);
	hash = hash_u64(hash, record->path_parent_sequence);
	hash = hash_u64(hash, record->arg0);
	hash = hash_u64(hash, record->value0);
	hash = hash_u64(hash, record->value1);
	hash = hash_u64(hash, record->value2);
	hash = hash_u64(hash, record->flags & ~AGENT_CONTEXT_PROVENANCE_MASK);
	hash = hash_u64(hash, (uint64)(uint)record->tool_id);
	hash = hash_u64(hash, (uint64)(uint)record->status);
	hash = hash_bytes(hash, record->payload, strlen(record->payload));
	hash = hash_bytes(hash, record->result, strlen(record->result));
	return hash;
}

static void run_functional_task3(void)
{
	uint64 values[22];
	uint64 first_sequence;
	uint64 last_sequence;
	uint64 rollback_sequence;
	uint64 old_branch;
	uint64 new_branch;
	uint64 branch_sequence;
	uint64 tool_semantic;
	uint64 capacity;
	uint64 semantic;
	int direct_count;
	int query_count;
	int active_after_rollback;
	int active_after_branch;
	int post_query_count;
	int post_direct_count;
	int clear_count;
	int fifo_count;

	check(context_clear() == AGENT_STATUS_OK, "task3 initial clear");
	for (int i = 0; i < FUNCTIONAL_TASK3_ROUNDS; i++) {
		prepare_functional_task3_tool(&functional_context_ops[i], i);
		memset(&functional_context_tool_results[i], 0,
		       sizeof(functional_context_tool_results[i]));
		check(agent_run(&functional_context_ops[i],
				&functional_context_tool_results[i], 1, 0) == 1,
		      "task3 consecutive production tool call");
		check_functional_task3_result(&functional_context_ops[i],
					      &functional_context_tool_results[i],
					      (uint64)i + 1);
	}
	check(context_snapshot(&functional_context_header,
			       functional_context_records,
			       AGENT_CONTEXT_MAX_RECORDS) ==
		      FUNCTIONAL_TASK3_ROUNDS,
	      "task3 six-round snapshot");
	first_sequence = functional_context_records[0].sequence;
	last_sequence = functional_context_records[FUNCTIONAL_TASK3_ROUNDS - 1].sequence;
	rollback_sequence = functional_context_records[2].sequence;
	check(first_sequence == 1 && last_sequence == FUNCTIONAL_TASK3_ROUNDS &&
		      rollback_sequence == 3,
	      "task3 clear resets visible sequence");
	old_branch = functional_context_records[0].branch_generation;
	tool_semantic = hash_bytes(FNV_OFFSET, "task3-tool-path-v1",
				   strlen("task3-tool-path-v1"));
	tool_semantic = hash_u64(tool_semantic, AGENTEVAL_CHALLENGE);
	for (int i = 0; i < FUNCTIONAL_TASK3_ROUNDS; i++) {
		check_functional_task3_record(
			&functional_context_records[i], &functional_context_ops[i],
			(uint64)i + 1, i == 0 ? 0 : (uint64)i,
			old_branch);
		tool_semantic = functional_task3_record_hash(
			tool_semantic, &functional_context_records[i]);
	}
	query_count = context_query(first_sequence, functional_context_records,
				    AGENT_CONTEXT_MAX_RECORDS);
	check(query_count == FUNCTIONAL_TASK3_ROUNDS,
	      "task3 syscall query count");
	direct_count = context_direct_active_query(
		eval_info.context_base, first_sequence, context_results,
		EVAL_MAX_LOAD);
	check(direct_count == FUNCTIONAL_TASK3_ROUNDS,
	      "task3 direct query count");
	for (int i = 0; i < FUNCTIONAL_TASK3_ROUNDS; i++)
		check(bytes_equal(&functional_context_records[i],
				  &context_results[i],
				  sizeof(functional_context_records[i])),
		      "task3 syscall and direct query agree");

	check(context_rollback(rollback_sequence) == AGENT_STATUS_OK,
	      "task3 rollback");
	active_after_rollback = context_snapshot(
		&functional_context_header, functional_context_records,
		AGENT_CONTEXT_MAX_RECORDS);
	check(active_after_rollback == 3 &&
		      functional_context_header.visible_head_sequence ==
			      rollback_sequence,
	      "task3 rollback active path");
	new_branch = functional_context_header.branch_generation;
	check(new_branch != old_branch, "task3 rollback branch generation");
	prepare_functional_task3_tool(
		&functional_context_ops[FUNCTIONAL_TASK3_ROUNDS],
				      FUNCTIONAL_TASK3_ROUNDS);
	memset(&functional_context_tool_results[FUNCTIONAL_TASK3_ROUNDS], 0,
	       sizeof(functional_context_tool_results[FUNCTIONAL_TASK3_ROUNDS]));
	check(agent_run(&functional_context_ops[FUNCTIONAL_TASK3_ROUNDS],
			&functional_context_tool_results[FUNCTIONAL_TASK3_ROUNDS],
			1, 0) == 1,
	      "task3 post-rollback production tool call");
	branch_sequence = last_sequence + 1;
	check_functional_task3_result(
		&functional_context_ops[FUNCTIONAL_TASK3_ROUNDS],
		&functional_context_tool_results[FUNCTIONAL_TASK3_ROUNDS],
				      branch_sequence);
	active_after_branch = context_snapshot(
		&functional_context_header, functional_context_records,
		AGENT_CONTEXT_MAX_RECORDS);
	check(active_after_branch == 4 &&
		      functional_context_header.visible_head_sequence ==
			      branch_sequence &&
		      functional_context_header.branch_generation == new_branch,
	      "task3 post-rollback active branch");
	check_functional_task3_record(&functional_context_records[3],
				      &functional_context_ops[FUNCTIONAL_TASK3_ROUNDS],
				      branch_sequence, rollback_sequence,
				      new_branch);
	tool_semantic = functional_task3_record_hash(
		tool_semantic, &functional_context_records[3]);
	post_query_count = context_query(first_sequence,
					 functional_context_records,
					 AGENT_CONTEXT_MAX_RECORDS);
	check(post_query_count == active_after_branch,
	      "task3 post-rollback syscall query");
	post_direct_count = context_direct_active_query(
		eval_info.context_base, first_sequence, context_results,
		EVAL_MAX_LOAD);
	check(post_direct_count == active_after_branch,
	      "task3 post-rollback direct query");
	for (int i = 0; i < active_after_branch; i++)
		check(bytes_equal(&functional_context_records[i],
				  &context_results[i],
				  sizeof(functional_context_records[i])),
		      "task3 post-rollback query agreement");

	check(context_clear() == AGENT_STATUS_OK, "task3 FIFO clear");
	clear_count = context_snapshot(&functional_context_header,
				       functional_context_records,
				       AGENT_CONTEXT_MAX_RECORDS);
	check(clear_count == 0, "task3 clear visible count");
	capacity = functional_context_header.capacity;
	check(capacity == AGENT_CONTEXT_MAX_RECORDS,
	      "task3 published FIFO capacity");
	for (uint64 i = 0; i < capacity + 5; i++) {
		struct agent_context_record *record =
			&functional_context_records[0];

		memset(record, 0, sizeof(*record));
		record->request_id = semantic_token(
			"functional-fifo-v1", (int)(capacity + 5), 0,
			(int)i);
		record->arg0 = AGENTEVAL_CHALLENGE ^ i;
		record->value0 = i;
		record->tool_id = AGENT_TOOL_CONTEXT_PUSH;
		record->status = AGENT_STATUS_OK;
		strcpy(record->payload, "fifo");
		strcpy(record->result, "ok");
		check(context_push(record) == AGENT_STATUS_OK,
		      "task3 FIFO append remains non-OOM");
	}
	fifo_count = context_snapshot(&functional_context_header,
				      functional_context_records,
				      AGENT_CONTEXT_MAX_RECORDS);
	check(fifo_count == (int)capacity &&
		      functional_context_header.dropped_records == 5 &&
		      functional_context_header.oldest_sequence == 6 &&
		      functional_context_header.latest_sequence == capacity + 5 &&
		      functional_context_header.eviction_policy ==
			      AGENT_CONTEXT_EVICT_FIFO,
	      "task3 bounded FIFO eviction");

	values[0] = FUNCTIONAL_TASK3_ROUNDS;
	values[1] = (uint64)(uint)query_count;
	values[2] = (uint64)(uint)direct_count;
	values[3] = first_sequence;
	values[4] = last_sequence;
	values[5] = tool_semantic;
	values[6] = rollback_sequence;
	values[7] = (uint64)(uint)active_after_rollback;
	values[8] = old_branch;
	values[9] = new_branch;
	values[10] = branch_sequence;
	values[11] = rollback_sequence;
	values[12] = (uint64)(uint)post_query_count;
	values[13] = (uint64)(uint)post_direct_count;
	values[14] = (uint64)(uint)clear_count;
	values[15] = capacity;
	values[16] = (uint64)(uint)fifo_count;
	values[17] = functional_context_header.dropped_records;
	values[18] = functional_context_header.oldest_sequence;
	values[19] = functional_context_header.latest_sequence;
	values[20] = functional_context_header.eviction_policy;
	values[21] = (uint64)(uint)active_after_branch;
	semantic = functional_values_semantic("task3-semantic-v2", values,
					      22);
	print_functional_receipt("task3", values, 22, semantic);
}

static int task4_fixture_code(void)
{
	uint64 mixed = AGENTEVAL_CHALLENGE ^ (AGENTEVAL_CHALLENGE >> 32);

	return (int)(mixed % 1000);
}

static void task4_fixture_name(char *name, char label, int code)
{
	strcpy(name, "t4a000");
	name[2] = label;
	name[3] = '0' + (code / 100) % 10;
	name[4] = '0' + (code / 10) % 10;
	name[5] = '0' + code % 10;
}

static void task4_fixture_text(char *text, const char *prefix,
			       const char *challenge)
{
	strcpy(text, prefix);
	strcpy(text + strlen(text), challenge);
}

static void task4_create_file(const char *name, const char *body)
{
	int fd = open(name, O_CREATE | O_RDWR | O_TRUNC);

	check(fd >= 0, "task4 create real file");
	check(write(fd, body, strlen(body)) == (ssize_t)strlen(body),
	      "task4 write challenge content");
	check(close(fd) == 0, "task4 close real file");
}

static void task4_set_metadata(int fid, const char *name, const char *run_id,
			       const char *kind, const char *summary)
{
	memset(&file_meta, 0, sizeof(file_meta));
	file_meta.fid = fid;
	strcpy(file_meta.physical_name, name);
	strcpy(file_meta.logical_path, name);
	strcpy(file_meta.project, "eval4");
	strcpy(file_meta.workflow, "query-proof");
	strcpy(file_meta.run_id, run_id);
	strcpy(file_meta.stage, "memory");
	strcpy(file_meta.kind, kind);
	strcpy(file_meta.status, "ready");
	strcpy(file_meta.summary, summary);
	file_meta.dependency_mask = agent_dependency_label_bit("ready");
	check(agent_file_meta_set(&file_meta) == AGENT_STATUS_OK,
	      "task4 set inode attributes");
}

static void task4_prepare_query(const char *run_id,
				const char *summary_contains)
{
	memset(&file_query, 0, sizeof(file_query));
	file_query.flags = AGENT_FILE_QUERY_SCAN;
	file_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(file_query.project, "eval4");
	strcpy(file_query.workflow, "query-proof");
	strcpy(file_query.run_id, run_id);
	strcpy(file_query.stage, "memory");
	strcpy(file_query.kind, "artifact");
	strcpy(file_query.status, "ready");
	if (summary_contains)
		strcpy(file_query.summary_contains, summary_contains);
}

static int task4_hit_matches(const struct agent_file_hit *hit, int fid,
			     const char *name, const char *summary,
			     const char *body)
{
	return hit->fid == fid &&
	       strcmp(hit->physical_name, name) == 0 &&
	       strcmp(hit->logical_path, name) == 0 &&
	       strcmp(hit->stage, "memory") == 0 &&
	       strcmp(hit->kind, "artifact") == 0 &&
	       strcmp(hit->status, "ready") == 0 &&
	       strcmp(hit->summary, summary) == 0 &&
	       hit->dependency_mask == agent_dependency_label_bit("ready") &&
	       hit->dev != 0 && hit->inum != 0 && hit->incarnation != 0 &&
	       hit->size == strlen(body) && hit->fs_generation != 0;
}

static uint64 task4_query_semantic(const char *domain,
				   const struct agent_file_query *query,
				   const struct agent_file_query_result *result)
{
	uint64 hash = FNV_OFFSET;
	const char *fields[] = {
		query->physical_name, query->logical_path, query->project,
		query->workflow, query->run_id, query->stage, query->kind,
		query->status, query->summary_contains,
	};

	hash = hash_bytes(hash, domain, strlen(domain));
	hash = hash_u64(hash, AGENTEVAL_CHALLENGE);
	hash = hash_u64(hash, query->flags);
	hash = hash_u64(hash, (uint64)(uint)query->max_hits);
	for (uint i = 0; i < sizeof(fields) / sizeof(fields[0]); i++)
		hash = hash_bytes(hash, fields[i], strlen(fields[i]));
	hash = hash_u64(hash, (uint64)(uint)result->total_hits);
	hash = hash_u64(hash, (uint64)(uint)result->returned);
	hash = hash_u64(hash, (uint64)(uint)result->truncated);
	hash = hash_u64(hash, (uint64)(uint)result->used_index);
	hash = hash_u64(hash, (uint64)(uint)result->plan);
	hash = hash_u64(hash, result->fs_generation);
	for (int i = 0; i < result->returned; i++) {
		const struct agent_file_hit *hit = &result->hits[i];
		const char *hit_fields[] = {
			hit->physical_name, hit->logical_path, hit->stage,
			hit->kind, hit->status, hit->summary,
		};

		hash = hash_u64(hash, (uint64)(uint)hit->fid);
		for (uint field = 0;
		     field < sizeof(hit_fields) / sizeof(hit_fields[0]); field++)
			hash = hash_bytes(hash, hit_fields[field],
					  strlen(hit_fields[field]));
		hash = hash_u64(hash, hit->dependency_mask);
		hash = hash_u64(hash, hit->dev);
		hash = hash_u64(hash, hit->inum);
		hash = hash_u64(hash, hit->incarnation);
		hash = hash_u64(hash, hit->size);
		hash = hash_u64(hash, hit->fs_generation);
	}
	return hash;
}

static int task4_delete_metadata(int fid, const char *name, uint64 dev,
				 uint64 inum, uint64 incarnation)
{
	memset(&file_meta, 0, sizeof(file_meta));
	file_meta.fid = fid;
	strcpy(file_meta.physical_name, name);
	file_meta.dev = dev;
	file_meta.inum = inum;
	file_meta.incarnation = incarnation;
	file_meta.flags = AGENT_FILE_META_F_DELETE;
	return agent_file_meta_set(&file_meta);
}

static void run_functional_task4(void)
{
	struct agent_op *digest_op = &capture.task4.digest_op;
	struct agent_result *digest_result = &capture.task4.digest_result;
	char *challenge = capture.task4.challenge;
	char (*names)[AGENT_FILE_NAME_SIZE] = capture.task4.names;
	char (*summaries)[AGENT_FILE_SUMMARY_SIZE] = capture.task4.summaries;
	char (*bodies)[AGENT_FAST_RESULT_SIZE] = capture.task4.bodies;
	char *needle = capture.task4.needle;
	char *retired_name = capture.task4.retired_name;
	uint64 *values = capture.task4.values;
	uint64 semantic;
	int code = task4_fixture_code();
	int fid_base = TASK4_FUNCTIONAL_FID_BASE +
		       code * TASK4_FUNCTIONAL_FID_STRIDE;
	int query_status;

	format_hex16(AGENTEVAL_CHALLENGE, challenge);
	/* 复用三个测量后槽位，但保持身份独立。 */
	for (int i = 0; i < 3; i++) {
		make_code(retired_name, 'e', EVAL_MAX_LOAD - 3 + i);
		check(task4_delete_metadata(
			      2000 + EVAL_MAX_LOAD - 3 + i, retired_name,
			      0, 0, 0) == AGENT_STATUS_OK,
		      "task4 retire measured metadata fixture");
		check(unlink(retired_name) == 0,
		      "task4 retire measured file fixture");
	}
	for (int i = 0; i < 3; i++) {
		task4_fixture_name(names[i], 'a' + i, code);
		task4_fixture_text(
			summaries[i], i == 0 ? "memory needle " :
			(i == 1 ? "memory peer " : "memory decoy "),
			challenge);
		task4_fixture_text(
			bodies[i], i == 0 ? "task4-content-a-" :
			(i == 1 ? "task4-content-b-" : "task4-content-c-"),
			challenge);
		task4_create_file(names[i], bodies[i]);
		task4_set_metadata(fid_base + i, names[i], challenge + 1,
				   i < 2 ? "artifact" : "report",
				   summaries[i]);
	}

	/* 属性查询对六个精确字段取逻辑与。 */
	task4_prepare_query(challenge + 1, 0);
	query_status = agent_file_query(&file_query, &file_result);
	check(query_status == 2 && file_result.total_hits == 2 &&
		      file_result.returned == 2 && !file_result.truncated &&
		      !file_result.used_index &&
		      file_result.plan == AGENT_FILE_QUERY_PLAN_SCAN,
	      "task4 multi-condition AND query");
	check(task4_hit_matches(&file_result.hits[0], fid_base, names[0],
				 summaries[0], bodies[0]) &&
		      task4_hit_matches(&file_result.hits[1], fid_base + 1,
				 names[1], summaries[1], bodies[1]) &&
		      (file_result.hits[0].dev != file_result.hits[1].dev ||
		       file_result.hits[0].inum != file_result.hits[1].inum ||
		       file_result.hits[0].incarnation !=
			       file_result.hits[1].incarnation),
	      "task4 ordered unique structured inode hits");
	values[0] = (uint64)(uint)code;
	values[1] = (uint64)(uint)fid_base;
	values[2] = (uint64)(uint)(fid_base + 1);
	values[3] = (uint64)(uint)(fid_base + 2);
	values[4] = (uint64)(uint)file_result.total_hits;
	values[5] = (uint64)(uint)file_result.returned;
	values[6] = (uint64)(uint)file_result.truncated;
	values[7] = (uint64)(uint)file_result.used_index;
	values[8] = (uint64)(uint)file_result.plan;
	values[9] = (uint64)(uint)file_result.hits[0].fid;
	values[10] = (uint64)(uint)file_result.hits[1].fid;
	values[11] = file_result.hits[0].dev;
	values[12] = file_result.hits[0].inum;
	values[13] = file_result.hits[0].incarnation;
	values[14] = file_result.hits[0].size;
	values[15] = file_result.hits[0].fs_generation;
	values[16] = file_result.hits[1].dev;
	values[17] = file_result.hits[1].inum;
	values[18] = file_result.hits[1].incarnation;
	values[19] = file_result.hits[1].size;
	values[20] = file_result.hits[1].fs_generation;
	values[21] = file_result.fs_generation;
	values[22] = task4_query_semantic(
		"task4-attributes-v2", &file_query, &file_result);

	/* 内容摘要查询为模糊匹配，单独验证该功能。 */
	task4_fixture_text(needle, "needle ", challenge);
	task4_prepare_query(challenge + 1, needle);
	query_status = agent_file_query(&file_query, &file_result);
	check(query_status == 1 && file_result.total_hits == 1 &&
		      file_result.returned == 1 && !file_result.truncated &&
		      !file_result.used_index &&
		      file_result.plan == AGENT_FILE_QUERY_PLAN_SCAN &&
		      task4_hit_matches(&file_result.hits[0], fid_base, names[0],
					summaries[0], bodies[0]) &&
		      file_result.hits[0].dev == values[11] &&
		      file_result.hits[0].inum == values[12] &&
		      file_result.hits[0].incarnation == values[13],
	      "task4 fuzzy summary query");
	values[23] = (uint64)(uint)file_result.total_hits;
	values[24] = (uint64)(uint)file_result.returned;
	values[25] = (uint64)(uint)file_result.truncated;
	values[26] = (uint64)(uint)file_result.used_index;
	values[27] = (uint64)(uint)file_result.plan;
	values[28] = (uint64)(uint)file_result.hits[0].fid;
	values[29] = file_result.hits[0].dev;
	values[30] = file_result.hits[0].inum;
	values[31] = file_result.hits[0].incarnation;
	values[32] = file_result.hits[0].size;
	values[33] = file_result.hits[0].fs_generation;
	values[34] = file_result.fs_generation;
	values[35] = task4_query_semantic(
		"task4-summary-v2", &file_query, &file_result);

	/* 正式摘要工具把关键字命中绑定到真实文件内容。 */
	memset(digest_op, 0, sizeof(*digest_op));
	memset(digest_result, 0, sizeof(*digest_result));
	digest_op->version = AGENT_CALL_VERSION;
	digest_op->tool_id = AGENT_TOOL_READ_FILE_DIGEST;
	digest_op->request_id = semantic_token("task4-digest-v2", code, 0, 0);
	strcpy(digest_op->payload, names[0]);
	check(agent_run(digest_op, digest_result, 1, 0) == 1 &&
		      digest_result->request_id == digest_op->request_id &&
		      digest_result->sequence != 0 &&
		      digest_result->status == AGENT_STATUS_OK &&
		      digest_result->tool_id == AGENT_TOOL_READ_FILE_DIGEST &&
		      digest_result->value0 == strlen(bodies[0]) &&
		      digest_result->value1 == strlen(bodies[0]) &&
		      digest_result->value2 ==
			      hash_bytes(FNV_OFFSET, bodies[0], strlen(bodies[0])) &&
		      strcmp(digest_result->result, bodies[0]) == 0,
	      "task4 content digest and preview");
	values[36] = digest_op->request_id;
	values[37] = digest_result->request_id;
	values[38] = digest_result->sequence;
	values[39] = (uint64)(long long)digest_result->status;
	values[40] = (uint64)(uint)digest_result->tool_id;
	values[41] = digest_result->value0;
	values[42] = digest_result->value1;
	values[43] = digest_result->value2;
	values[44] = hash_bytes(FNV_OFFSET, digest_result->result,
				strlen(digest_result->result));

	values[45] = (uint64)(long long)task4_delete_metadata(
		fid_base, names[0], values[11], values[12], values[13]);
	check((long long)values[45] == AGENT_STATUS_OK,
	      "task4 delete first attribute record");
	task4_prepare_query(challenge + 1, 0);
	query_status = agent_file_query(&file_query, &file_result);
	check(query_status == 1 && file_result.total_hits == 1 &&
		      file_result.returned == 1 && !file_result.truncated &&
		      task4_hit_matches(&file_result.hits[0], fid_base + 1,
					names[1], summaries[1], bodies[1]) &&
		      file_result.hits[0].dev == values[16] &&
		      file_result.hits[0].inum == values[17] &&
		      file_result.hits[0].incarnation == values[18] &&
		      file_result.fs_generation > values[34],
	      "task4 delete removes only selected attributes");
	values[46] = (uint64)(uint)file_result.total_hits;
	values[47] = (uint64)(uint)file_result.returned;
	values[48] = (uint64)(uint)file_result.hits[0].fid;
	values[49] = file_result.fs_generation;
	values[50] = task4_query_semantic(
		"task4-delete-one-v2", &file_query, &file_result);

	values[51] = (uint64)(long long)task4_delete_metadata(
		fid_base + 1, names[1], values[16], values[17], values[18]);
	check((long long)values[51] == AGENT_STATUS_OK,
	      "task4 delete second attribute record");
	task4_prepare_query(challenge + 1, 0);
	query_status = agent_file_query(&file_query, &file_result);
	check(query_status == 0 && file_result.total_hits == 0 &&
		      file_result.returned == 0 && !file_result.truncated &&
		      file_result.fs_generation > values[49],
	      "task4 deleted attributes no longer queryable");
	values[52] = (uint64)(uint)file_result.total_hits;
	values[53] = (uint64)(uint)file_result.returned;
	values[54] = file_result.fs_generation;
	values[55] = task4_query_semantic(
		"task4-delete-all-v2", &file_query, &file_result);

	check(task4_delete_metadata(fid_base + 2, names[2], 0, 0, 0) ==
		      AGENT_STATUS_OK,
	      "task4 remove distractor attributes");
	for (int i = 0; i < 3; i++)
		check(unlink(names[i]) == 0, "task4 remove fixture file");
	semantic = functional_values_semantic("task4-semantic-v2", values, 56);
	print_functional_receipt("task4", values, 56, semantic);
}

static void run_functional_sentinel(int gate_fd, uint64 corr_id)
{
	struct agent_info sentinel_info;
	uint64 wake_tick;
	char gate;
	int parent_pid = getppid();

	check(agent_info(&sentinel_info) == AGENT_STATUS_OK &&
		      sentinel_info.is_agent == 1 &&
		      sentinel_info.agent_role == AGENT_ROLE_SENTINEL,
	      "task5 agent_create Sentinel identity");
	check(read(gate_fd, &gate, 1) == 1 && gate == 'G',
	      "task5 Sentinel gate");
	close(gate_fd);
	check(agent_info(&sentinel_info) == AGENT_STATUS_OK,
	      "task5 Sentinel delay start");
	wake_tick = sentinel_info.current_tick + TASK5_DELAY_TICKS;
	sleep(TASK5_DELAY_TICKS * TASK5_TICK_MSEC);
	do {
		check(agent_info(&sentinel_info) == AGENT_STATUS_OK,
		      "task5 Sentinel delay clock");
		if (sentinel_info.current_tick < wake_tick)
			sched_yield();
	} while (sentinel_info.current_tick < wake_tick);
	memset(&functional_event, 0, sizeof(functional_event));
	functional_event.type = AGENT_EVENT_MESSAGE;
	functional_event.corr_id = corr_id;
	strcpy(functional_event.payload, "eval-functional");
	check(agent_wake(parent_pid, &functional_event) == AGENT_STATUS_OK,
	      "task5 Sentinel delayed wake");
	exit(0);
}

static void run_functional_waiter(void *arg)
{
	(void)arg;
	memset(&functional_event, 0, sizeof(functional_event));
	task5_wait_status = agent_wait(&functional_event, 50);
	exit(0);
}

static void run_functional_task5(void)
{
	uint64 *values = capture.task5_values;
	struct agent_info waiter_info;
	uint64 corr_id = semantic_token("task5-event-v1", 2, 0, 0);
	uint64 heartbeat_sleep_before;
	uint64 heartbeat_wake_before;
	uint64 message_sleep_before;
	uint64 message_event_id;
	uint64 message_event_tick;
	uint64 message_sleep_after;
	uint64 message_wake_after;
	uint64 semantic;
	int message_source;
	int message_target;
	int gate[2];
	int helper_pid;
	int helper_status = -1;
	int waiter_tid;
	int timeout_status;
	char start = 'G';

	/* 任务 3 可能残留已预留的 CONTEXT_LIMIT 事件，先隔离本次等待。 */
	for (int pending = 0; pending < AGENT_EVENT_QUEUE_CAP; pending++) {
		check(agent_info(&eval_info) == AGENT_STATUS_OK,
		      "task5 preflight queue info");
		if (eval_info.event_queue_count == 0)
			break;
		memset(&functional_event, 0, sizeof(functional_event));
		check(agent_wait(&functional_event, 1) == AGENT_STATUS_OK,
		      "task5 drain pre-existing event");
	}
	check(agent_info(&eval_info) == AGENT_STATUS_OK &&
		      eval_info.event_queue_count == 0,
	      "task5 isolated starting queue");
	check(pipe(gate) == 0, "task5 Sentinel gate pipe");
	check(agent_scope_delegate_fd(gate[0]) == AGENT_STATUS_OK,
	      "task5 delegate Sentinel gate");
	helper_pid = agent_create();
	check(helper_pid >= 0, "task5 create Sentinel helper");
	if (helper_pid == 0)
		run_functional_sentinel(gate[0], corr_id);
	close(gate[0]);
	check(agent_watch(AGENT_EVENT_MESSAGE, "eval-functional") ==
		      AGENT_STATUS_OK,
	      "task5 watch delayed message");
	check(agent_route_config(helper_pid, getpid(),
				 AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "task5 trusted Sentinel route");
	check(agent_info(&functional_info_before) == AGENT_STATUS_OK,
	      "task5 info before message wait");
	message_sleep_before = functional_info_before.wait_sleep_count;
	task5_wait_status = -99;
	waiter_tid = thread_create(run_functional_waiter, 0);
	check(waiter_tid > 0, "task5 create message waiter");
	for (int attempt = 0; attempt < 64; attempt++) {
		check(agent_info(&waiter_info) == AGENT_STATUS_OK,
		      "task5 observe message waiter");
		if (waiter_info.wait_sleep_count > message_sleep_before)
			break;
		sched_yield();
	}
	check(waiter_info.wait_sleep_count > message_sleep_before,
	      "task5 message waiter published");
	check(write(gate[1], &start, 1) == 1,
	      "task5 release Sentinel helper");
	close(gate[1]);
	check(waittid(waiter_tid) == 0 &&
		      task5_wait_status == AGENT_STATUS_OK,
	      "task5 delayed message wait");
	check(agent_info(&functional_info_after) == AGENT_STATUS_OK,
	      "task5 info after message wait");
	check(functional_event.type == AGENT_EVENT_MESSAGE &&
		      functional_event.source_pid == helper_pid &&
		      functional_event.target_pid == getpid() &&
		      functional_event.corr_id == corr_id &&
		      strcmp(functional_event.payload, "eval-functional") == 0,
	      "task5 delayed message identity");
	check(functional_info_after.current_tick >=
		      functional_info_before.current_tick + TASK5_DELAY_TICKS,
	      "task5 delayed wait advances wall ticks");
	check(functional_info_after.wait_loop_count >
		      functional_info_before.wait_loop_count &&
		      functional_info_after.wait_loop_count -
			      functional_info_before.wait_loop_count <=
			      TASK5_MAX_WAIT_LOOPS,
	      "task5 delayed wait has bounded predicate checks");
	values[19] = functional_info_before.current_tick;
	values[20] = functional_info_after.current_tick;
	values[21] = functional_info_before.sched_dispatch_count;
	values[22] = functional_info_after.sched_dispatch_count;
	values[23] = functional_info_before.sched_vruntime;
	values[24] = functional_info_after.sched_vruntime;
	values[25] = functional_info_before.wait_loop_count;
	values[26] = functional_info_after.wait_loop_count;
	values[27] = functional_info_before.sched_weight;
	message_source = functional_event.source_pid;
	message_target = functional_event.target_pid;
	message_event_id = functional_event.event_id;
	message_event_tick = functional_event.tick;
	message_sleep_after = functional_info_after.wait_sleep_count;
	message_wake_after = functional_info_after.wait_wakeup_count;
	check(waitpid(helper_pid, &helper_status) == helper_pid &&
		      helper_status == 0,
	      "task5 stable Sentinel waitpid");
	check(agent_unwatch(AGENT_EVENT_MESSAGE, "eval-functional") == 1,
	      "task5 remove message watch");

	check(agent_info(&functional_info_after) == AGENT_STATUS_OK,
	      "task5 info before heartbeats");
	heartbeat_sleep_before = functional_info_after.wait_sleep_count;
	heartbeat_wake_before = functional_info_after.wait_wakeup_count;
	check(agent_heartbeat_set(2) == AGENT_STATUS_OK,
	      "task5 heartbeat interval two");
	memset(&functional_event, 0, sizeof(functional_event));
	check(agent_wait(&functional_event, 50) == AGENT_STATUS_OK &&
		      functional_event.type == AGENT_EVENT_TIMER &&
		      strcmp(functional_event.payload, "timer=heartbeat") == 0,
	      "task5 first heartbeat");
	values[11] = functional_event.tick;
	check(agent_heartbeat_set(1) == AGENT_STATUS_OK,
	      "task5 dynamic heartbeat interval one");
	memset(&functional_event, 0, sizeof(functional_event));
	check(agent_wait(&functional_event, 50) == AGENT_STATUS_OK &&
		      functional_event.type == AGENT_EVENT_TIMER &&
		      strcmp(functional_event.payload, "timer=heartbeat") == 0,
	      "task5 second heartbeat");
	values[12] = functional_event.tick;
	check(values[12] > values[11], "task5 heartbeat tick progress");
	check(agent_info(&functional_info_after) == AGENT_STATUS_OK,
	      "task5 info after heartbeats");
	check(functional_info_after.wait_sleep_count >
		      heartbeat_sleep_before &&
		      functional_info_after.wait_wakeup_count >
			      heartbeat_wake_before,
	      "task5 heartbeat sleep and wake counters");
	check(agent_heartbeat_stop() == AGENT_STATUS_OK,
	      "task5 heartbeat stop");
	for (int pending = 0; pending < AGENT_EVENT_QUEUE_CAP; pending++) {
		check(agent_info(&eval_info) == AGENT_STATUS_OK,
		      "task5 stopped queue info");
		if (eval_info.event_queue_count == 0)
			break;
		memset(&functional_event, 0, sizeof(functional_event));
		check(agent_wait(&functional_event, 1) == AGENT_STATUS_OK &&
			      functional_event.type == AGENT_EVENT_TIMER,
		      "task5 drain stopped heartbeat");
	}
	check(agent_info(&eval_info) == AGENT_STATUS_OK &&
		      eval_info.event_queue_count == 0,
	      "task5 stopped queue drained");
	memset(&functional_event, 0, sizeof(functional_event));
	timeout_status = agent_wait(&functional_event, 3);
	check(timeout_status == AGENT_STATUS_TIMEOUT &&
		      functional_event.status == AGENT_STATUS_TIMEOUT,
	      "task5 stopped heartbeat timeout");

	values[0] = (uint64)(uint)getpid();
	values[1] = (uint64)(uint)helper_pid;
	values[2] = (uint64)(uint)message_source;
	values[3] = (uint64)(uint)message_target;
	values[4] = corr_id;
	values[5] = message_event_id;
	values[6] = message_event_tick;
	values[7] = functional_info_before.wait_sleep_count;
	values[8] = message_sleep_after;
	values[9] = functional_info_before.wait_wakeup_count;
	values[10] = message_wake_after;
	values[13] = functional_info_after.wait_sleep_count -
		     heartbeat_sleep_before;
	values[14] = functional_info_after.wait_wakeup_count -
		     heartbeat_wake_before;
	values[15] = (uint64)(long long)timeout_status;
	values[16] = (uint64)(long long)helper_status;
	values[17] = 2;
	values[18] = AGENT_ROLE_SENTINEL;
	semantic = functional_values_semantic("task5-semantic-v2", values,
					      TASK5_RECEIPT_VALUES);
	print_functional_receipt("task5", values, TASK5_RECEIPT_VALUES,
				 semantic);
}

static void run_evaluation(void)
{
	check(agent_info(&eval_info) == 0, "evaluation agent info");
	check(eval_info.is_agent &&
		      eval_info.agent_role == AGENT_ROLE_ORCHESTRATOR,
	      "evaluation runs as orchestrator");
	check((eval_info.capability_mask &
	       (AGENT_CAP_META_READ | AGENT_CAP_META_WRITE |
		AGENT_CAP_ORCHESTRATE)) ==
		      (AGENT_CAP_META_READ | AGENT_CAP_META_WRITE |
		       AGENT_CAP_ORCHESTRATE),
	      "evaluation capabilities");
	check(context_clear() == 0, "clear evaluation context");
	check(agent_file_meta_init() == 0, "initialize evaluation metadata");

	run_file_query_experiment();
	run_tool_batch_experiment();
	run_context_access_experiment();
	/* 功能验收不计入任何计时区间。 */
	run_functional_task1();
	run_functional_task2();
	run_functional_task3();
	run_functional_task4();
	run_functional_task5();
	printf("agenteval_ucore: worker passed\n");
	exit(0);
}

static void run_compat_sentinel_probe(void)
{
	struct agent_info sentinel_info;

	check(agent_info(&sentinel_info) == AGENT_STATUS_OK &&
		      sentinel_info.is_agent == 1 &&
		      sentinel_info.agent_role == AGENT_ROLE_SENTINEL,
	      "task1 agent_create Sentinel identity");
	exit(0);
}

int main(void)
{
	char challenge_text[17];
	uint64 launcher_values[5];
	uint64 launcher_semantic;
	int pid;
	int status = 0;

	check(AGENTEVAL_CHALLENGE != 0, "nonzero evaluation challenge");
	format_hex16(AGENTEVAL_CHALLENGE, challenge_text);
	printf("agenteval_ucore: challenge=%s\n", challenge_text);
	printf("agenteval_ucore: measured AgentOS evaluation\n");
	check(agent_info(&eval_info) == 0, "query evaluation launcher");
	if (eval_info.is_agent) {
		check(eval_info.agent_role == AGENT_ROLE_ORCHESTRATOR,
		      "launcher orchestrator role");
		run_evaluation();
	}
	check(eval_info.agent_role == 0 && eval_info.context_base == 0 &&
		      eval_info.context_size == 0,
	      "launcher remains an ordinary process");
	launcher_values[0] = (uint64)(uint)getpid();
	launcher_values[1] = (uint64)(uint)eval_info.is_agent;
	launcher_values[2] = (uint64)(uint)eval_info.agent_role;
	launcher_values[3] = eval_info.context_base;
	launcher_values[4] = eval_info.context_size;
	launcher_semantic = functional_values_semantic(
		"task1-launcher-semantic-v1", launcher_values, 5);
	print_launcher_receipt(launcher_values, 5, launcher_semantic);
	functional_compat_sentinel_pid = agent_create();
	check(functional_compat_sentinel_pid >= 0,
	      "task1 compatibility Sentinel create");
	if (functional_compat_sentinel_pid == 0)
		run_compat_sentinel_probe();
	check(waitpid(functional_compat_sentinel_pid,
		      &functional_compat_sentinel_status) ==
		      functional_compat_sentinel_pid &&
		      functional_compat_sentinel_status == 0,
	      "task1 compatibility Sentinel identity status");
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create evaluation orchestrator");
	if (pid == 0)
		run_evaluation();
	check(waitpid(pid, &status) == pid, "wait evaluation orchestrator");
	check(status == 0, "evaluation orchestrator status");
	/* 主计时结束后再运行补充隔离负载。 */
	run_revisit_evaluation();
	printf("agenteval_ucore: parent passed\n");
	return 0;
}
