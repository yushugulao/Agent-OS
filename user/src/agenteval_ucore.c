#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <agenteval_seed.h>

#define EVAL_SCHEMA 1
#define EVAL_PAIRS 7
#define EVAL_LOADS 3
#define EVAL_MAX_LOAD 96
#define EVAL_FILE_QUERIES 16
#define EVAL_CONTEXT_SELECTOR 0x4147415396722031ULL
#define FUNCTIONAL_TASK3_ROUNDS 6

#define FNV_OFFSET 1469598103934665603ULL
#define FNV_PRIME 1099511628211ULL

static const int eval_loads[EVAL_LOADS] = { 24, 64, 96 };

static struct agent_file_meta file_meta;
static struct agent_file_query file_query;
static struct agent_file_query_result file_result;
static struct agent_info eval_info;

struct functional_file_binding {
	int valid;
	int load;
	int pair;
	int target_meta;
	int target_fid;
	uint64 scan_work;
	uint64 index_work;
	uint64 result_items;
	uint64 result_fingerprint;
	uint64 inum;
	uint64 incarnation;
	uint64 fs_generation;
	int used_index;
	int plan;
};

static struct functional_file_binding functional_file;
static struct agent_context_header functional_context_header;
static struct agent_context_record
	functional_context_records[AGENT_CONTEXT_MAX_RECORDS];
static struct agent_tool_desc_v2 functional_tools[AGENT_TOOL_COUNT];
static struct agent_param_v2 functional_params[AGENT_TOOL_PARAM_MAX];
static struct agent_request_v2 functional_request;
static struct agent_response_v2 functional_response;
static struct agent_info functional_info_before;
static struct agent_info functional_info_after;
static struct agent_event functional_event;

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
	struct agent_file_hit hit;
};

struct measurement {
	uint64 duration_us;
	uint64 work_units;
	uint64 records_examined;
	uint64 result_items;
	uint64 result_fingerprint;
};

/* The three experiments are serialized, so their capture buffers can overlap. */
static union evaluation_capture {
	struct file_observation file[EVAL_MAX_LOAD];
	struct {
		struct agent_op ops[EVAL_MAX_LOAD];
		struct agent_result results[EVAL_MAX_LOAD];
	} tool;
	struct {
		struct agent_context_record records[EVAL_MAX_LOAD];
		int query_results[EVAL_MAX_LOAD];
	} context;
} capture;

#define file_observations (capture.file)
#define tool_ops (capture.tool.ops)
#define tool_results (capture.tool.results)
#define context_results (capture.context.records)
#define context_query_results (capture.context.query_results)

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

static int file_target_meta(int load, int pair)
{
	uint64 mixed = AGENTEVAL_CHALLENGE ^
			 (AGENTEVAL_CHALLENGE >> 32);

	mixed ^= (uint64)pair * 0x9e3779b97f4a7c15ULL;
	return (int)(mixed % (uint64)load);
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
	printf("agenteval_ucore: sample schema=1 experiment=%s load=%d pair=%d variant=%s order=%s cache=%s operations=%d dataset_size=%d work_units=%llu records_examined=%llu result_items=%llu duration_us=%llu workload_fingerprint=%s result_fingerprint=%s status=measured\n",
	       experiment, load, pair, variant, order, cache, operations,
	       dataset_size,
	       (unsigned long long)measurement->work_units,
	       (unsigned long long)measurement->records_examined,
	       (unsigned long long)measurement->result_items,
	       (unsigned long long)measurement->duration_us,
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

static void seed_file_metadata(int first, int limit)
{
	static const char body[] = "agentos-evaluation-file";
	char name[8];
	char status[8];

	for (int i = first; i < limit; i++) {
		int fd;

		make_code(name, 'e', i);
		make_code(status, 'q', i);
		fd = open(name, O_CREATE | O_RDWR | O_TRUNC);
		check(fd >= 0, "create evaluation file");
		check(write(fd, body, strlen(body)) == (ssize_t)strlen(body),
		      "write evaluation file");
		check(close(fd) == 0, "close evaluation file");
		memset(&file_meta, 0, sizeof(file_meta));
		file_meta.fid = 2000 + i;
		strcpy(file_meta.physical_name, name);
		strcpy(file_meta.logical_path, name);
		strcpy(file_meta.project, "eval");
		strcpy(file_meta.workflow, "comparison");
		strcpy(file_meta.run_id, "RUN-EVAL");
		strcpy(file_meta.stage, "query");
		strcpy(file_meta.kind, "artifact");
		strcpy(file_meta.status, status);
		strcpy(file_meta.summary, "measured evaluation fixture");
		file_meta.dependency_mask = agent_dependency_label_bit("ready");
		check(agent_file_meta_set(&file_meta) == 0,
		      "seed evaluation metadata");
	}
}

static void prepare_file_query(uint64 flags, int target_meta)
{
	char status[8];

	make_code(status, 'q', target_meta);
	memset(&file_query, 0, sizeof(file_query));
	file_query.flags = flags;
	file_query.max_hits = 1;
	strcpy(file_query.project, "eval");
	strcpy(file_query.workflow, "comparison");
	strcpy(file_query.run_id, "RUN-EVAL");
	strcpy(file_query.stage, "query");
	strcpy(file_query.status, status);
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

static struct measurement measure_file_variant(int dataset_size, int pair,
				       int operations, int target_meta,
				       int use_index)
{
	struct measurement measurement;
	uint64 start;

	prepare_file_query(use_index ? AGENT_FILE_QUERY_USE_INDEX :
					 AGENT_FILE_QUERY_SCAN,
			   target_meta);
	start = now_us();
	for (int i = 0; i < operations; i++) {
		int result = agent_file_query(&file_query, &file_result);

		capture_file_observation(&file_observations[i], result);
	}
	measurement.duration_us = elapsed_us(start, now_us());
	measurement.work_units = 0;
	measurement.records_examined = 0;
	measurement.result_items = 0;
	measurement.result_fingerprint = result_fingerprint_begin(
		"file_query", dataset_size, pair);
	for (int i = 0; i < operations; i++) {
		const struct file_observation *observation =
			&file_observations[i];

		check(observation->syscall_result == 1,
		      "file query returns one hit");
		check(observation->total_hits == 1 &&
			      observation->returned == 1 &&
			      observation->hit.fid == 2000 + target_meta,
		      "file query semantic result");
		check(observation->hit.dev != 0 &&
			      observation->hit.inum != 0 &&
			      observation->hit.incarnation != 0 &&
			      observation->hit.size ==
				      strlen("agentos-evaluation-file"),
		      "file query is bound to a real inode");
		if (use_index) {
			check(observation->used_index == 1 &&
				      observation->plan ==
					      AGENT_FILE_QUERY_PLAN_STATUS_INDEX,
			      "ready status index plan");
			check((observation->plan_reason &
			       AGENT_FILE_QUERY_REASON_STATUS_INDEX) != 0,
			      "ready status index reason");
			check((observation->plan_reason &
			       AGENT_FILE_QUERY_REASON_CACHE_HIT) == 0 &&
				      observation->index_rebuild_records == 0,
			      "warm index excludes cache and rebuild");
		} else {
			check(observation->used_index == 0 &&
				      observation->plan ==
					      AGENT_FILE_QUERY_PLAN_SCAN,
			      "forced scan plan");
			check((observation->plan_reason &
			       AGENT_FILE_QUERY_REASON_FORCED_SCAN) != 0,
			      "forced scan reason");
		}
		measurement.work_units += observation->scanned_records;
		measurement.records_examined += observation->candidate_records;
		measurement.result_items += observation->total_hits;
		measurement.result_fingerprint = hash_file_semantics(
			measurement.result_fingerprint, observation);
	}
	check(measurement.work_units == measurement.records_examined,
	      "file work accounting agrees with candidates");
	return measurement;
}

static void rebuild_file_index_diagnostic(int load)
{
	const char *cache;
	uint64 start;
	uint64 duration;
	uint64 work_units;
	int result;

	prepare_file_query(AGENT_FILE_QUERY_USE_INDEX,
			   file_target_meta(load, 0));
	start = now_us();
	result = agent_file_query(&file_query, &file_result);
	duration = elapsed_us(start, now_us());
	check(result == 1 && file_result.total_hits == 1,
	      "cold index semantic result");
	check(file_result.used_index == 1 &&
		      file_result.plan == AGENT_FILE_QUERY_PLAN_STATUS_INDEX,
	      "cold status index plan");
	check((file_result.plan_reason & AGENT_FILE_QUERY_REASON_CACHE_HIT) == 0,
	      "index readiness probe excludes query cache");
	cache = file_result.index_rebuild_records > 0 ? "cold-rebuild" :
							     "ready";
	work_units = file_result.index_rebuild_records > 0 ?
			     (uint64)file_result.index_rebuild_records :
			     (uint64)file_result.returned;
	printf("agenteval_ucore: diagnostic schema=1 experiment=file_query load=%d cache=%s operations=1 work_units=%d duration_us=%llu index_rebuild_records=%d status=measured\n",
	       load, cache, (int)work_units, (unsigned long long)duration,
	       file_result.index_rebuild_records);
}

static void run_file_query_experiment(void)
{
	int seeded = 0;

	for (int load_index = 0; load_index < EVAL_LOADS; load_index++) {
		int load = eval_loads[load_index];
		int warm_target = file_target_meta(load, 0);
		struct measurement scan;
		struct measurement index;

		seed_file_metadata(seeded, load);
		seeded = load;
		rebuild_file_index_diagnostic(load);

		scan = measure_file_variant(load, 0, EVAL_FILE_QUERIES,
					 warm_target, 0);
		index = measure_file_variant(load, 0, EVAL_FILE_QUERIES,
					  warm_target, 1);
		check(scan.result_fingerprint == index.result_fingerprint,
		      "file warmup equivalence");
		check(scan.work_units >=
			      (uint64)load * EVAL_FILE_QUERIES &&
			      index.work_units == EVAL_FILE_QUERIES,
		      "file warmup measured traversal");

		for (int pair = 1; pair <= EVAL_PAIRS; pair++) {
			const char *order = pair_runs_ab(pair) ? "AB" : "BA";
			int target_meta = file_target_meta(load, pair);
			uint64 workload = workload_fingerprint(
				"file_query", load, pair, EVAL_FILE_QUERIES,
				(uint64)target_meta);

			if (pair_runs_ab(pair)) {
				scan = measure_file_variant(
					load, pair, EVAL_FILE_QUERIES,
					target_meta, 0);
				index = measure_file_variant(
					load, pair, EVAL_FILE_QUERIES,
					target_meta, 1);
			} else {
				index = measure_file_variant(
					load, pair, EVAL_FILE_QUERIES,
					target_meta, 1);
				scan = measure_file_variant(
					load, pair, EVAL_FILE_QUERIES,
					target_meta, 0);
			}
			check(scan.result_fingerprint == index.result_fingerprint,
			      "file pair equivalence");
			check(scan.work_units >=
				      (uint64)load * EVAL_FILE_QUERIES &&
				      index.work_units == EVAL_FILE_QUERIES,
			      "file pair measured traversal");
			if (load == eval_loads[EVAL_LOADS - 1] &&
			    pair == EVAL_PAIRS) {
				const struct file_observation *indexed;
				int query_result;

				/*
				 * This untimed query makes the functional inode receipt
				 * independent of the preregistered AB/BA print order.
				 */
				prepare_file_query(AGENT_FILE_QUERY_USE_INDEX,
						   target_meta);
				query_result = agent_file_query(&file_query,
							&file_result);
				capture_file_observation(&file_observations[0],
							 query_result);
				indexed = &file_observations[0];
				check(indexed->syscall_result == 1 &&
					      indexed->total_hits == 1 &&
					      indexed->returned == 1 &&
					      indexed->hit.fid == 2000 + target_meta &&
					      indexed->hit.inum != 0 &&
					      indexed->hit.incarnation != 0,
				      "functional indexed inode query");
				check(indexed->used_index == 1 &&
					      indexed->plan ==
						      AGENT_FILE_QUERY_PLAN_STATUS_INDEX &&
					      (indexed->plan_reason &
					       AGENT_FILE_QUERY_REASON_STATUS_INDEX) != 0 &&
					      (indexed->plan_reason &
					       AGENT_FILE_QUERY_REASON_CACHE_HIT) == 0 &&
					      indexed->index_rebuild_records == 0,
				      "functional ready index query");

				functional_file.valid = 1;
				functional_file.load = load;
				functional_file.pair = pair;
				functional_file.target_meta = target_meta;
				functional_file.target_fid = indexed->hit.fid;
				functional_file.scan_work = scan.work_units;
				functional_file.index_work = index.work_units;
				functional_file.result_items = scan.result_items;
				functional_file.result_fingerprint =
					scan.result_fingerprint;
				functional_file.inum = indexed->hit.inum;
				functional_file.incarnation =
					indexed->hit.incarnation;
				functional_file.fs_generation =
					indexed->fs_generation;
				functional_file.used_index = indexed->used_index;
				functional_file.plan = indexed->plan;
			}
			if (pair_runs_ab(pair)) {
				print_sample("file_query", load, pair, "scan",
					     order, "forced-scan",
					     EVAL_FILE_QUERIES, load, workload,
					     &scan);
				print_sample("file_query", load, pair, "index",
					     order, "ready-index",
					     EVAL_FILE_QUERIES, load, workload,
					     &index);
			} else {
				print_sample("file_query", load, pair, "index",
					     order, "ready-index",
					     EVAL_FILE_QUERIES, load, workload,
					     &index);
				print_sample("file_query", load, pair, "scan",
					     order, "forced-scan",
					     EVAL_FILE_QUERIES, load, workload,
					     &scan);
			}
		}
	}
}

static void prepare_tool_workload(int load, int pair)
{
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

static uint64 hash_tool_results(int load, int pair)
{
	uint64 hash = result_fingerprint_begin("tool_batch", load, pair);
	uint64 first_sequence = tool_results[0].sequence;

	check(first_sequence != 0, "echo result sequence starts");

	for (int i = 0; i < load; i++) {
		const struct agent_result *result = &tool_results[i];
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

static struct measurement measure_tool_variant(int load, int pair, int batch)
{
	struct measurement measurement;
	uint64 start;
	int completed = 0;

	memset(tool_results, 0, sizeof(tool_results));
	start = now_us();
	while (completed < load) {
		int count = batch ? load - completed : 1;
		int result;

		if (count > (int)AGENT_BATCH_MAX)
			count = AGENT_BATCH_MAX;
		result = agent_run(&tool_ops[completed],
				   &tool_results[completed], count, 0);
		check(result > 0 && result <= count, "agent_run progress");
		completed += result;
	}
	measurement.duration_us = elapsed_us(start, now_us());
	measurement.work_units = completed;
	measurement.records_examined = 0;
	measurement.result_items = completed;
	measurement.result_fingerprint = hash_tool_results(load, pair);
	return measurement;
}

static void run_tool_batch_experiment(void)
{
	for (int load_index = 0; load_index < EVAL_LOADS; load_index++) {
		int load = eval_loads[load_index];
		struct measurement scalar;
		struct measurement batch;

		prepare_tool_workload(load, 0);
		scalar = measure_tool_variant(load, 0, 0);
		batch = measure_tool_variant(load, 0, 1);
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
				scalar = measure_tool_variant(load, pair, 0);
				batch = measure_tool_variant(load, pair, 1);
			} else {
				batch = measure_tool_variant(load, pair, 1);
				scalar = measure_tool_variant(load, pair, 0);
			}
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

static void copy_context_volatile(
	struct agent_context_record *destination,
	const volatile struct agent_context_record *source)
{
	destination->sequence = source->sequence;
	destination->request_id = source->request_id;
	destination->cause_sequence = source->cause_sequence;
	destination->span_id = source->span_id;
	destination->branch_generation = source->branch_generation;
	destination->path_parent_sequence = source->path_parent_sequence;
	destination->arg0 = source->arg0;
	destination->value0 = source->value0;
	destination->value1 = source->value1;
	destination->value2 = source->value2;
	destination->tick = source->tick;
	destination->flags = source->flags;
	destination->prev_hash = source->prev_hash;
	destination->record_hash = source->record_hash;
	destination->tool_id = source->tool_id;
	destination->status = source->status;
	for (int i = 0; i < AGENT_CONTEXT_TEXT_SIZE; i++) {
		destination->payload[i] = source->payload[i];
		destination->result[i] = source->result[i];
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
	volatile struct agent_context_header *header =
		(volatile struct agent_context_header *)eval_info.context_base;

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
	check(result.sequence != 0 && header->latest_sequence == result.sequence,
	      "context fixture is the visible latest record");
	return result.sequence;
}

static uint64 hash_context_results(int load, int pair,
				   uint64 target_sequence)
{
	uint64 hash = result_fingerprint_begin("context_access", load, pair);
	uint64 request_id = semantic_token("context-request-v1", load, pair, 0);
	uint64 arg0 = context_fixture_arg0(load, pair);
	uint64 arg1 = context_fixture_arg1(load, pair);

	for (int i = 0; i < load; i++) {
		const struct agent_context_record *record = &context_results[i];

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

static struct measurement measure_context_variant(int load, int pair,
					  int direct, uint64 target_sequence)
{
	struct measurement measurement;
	volatile struct agent_context_header *header =
		(volatile struct agent_context_header *)eval_info.context_base;
	const volatile struct agent_context_record *records =
		(const volatile struct agent_context_record *)(
			eval_info.context_base + header->records_offset);
	uint64 slot = (target_sequence - 1) % header->capacity;
	uint64 start;

	memset(context_results, 0, sizeof(context_results));
	memset(context_query_results, 0, sizeof(context_query_results));
	start = now_us();
	for (int i = 0; i < load; i++) {
		if (direct) {
			copy_context_volatile(&context_results[i], &records[slot]);
			context_query_results[i] = 1;
		} else {
			context_query_results[i] = context_query(
				target_sequence, &context_results[i], 1);
		}
	}
	measurement.duration_us = elapsed_us(start, now_us());
	measurement.work_units = 0;
	measurement.records_examined = 0;
	for (int i = 0; i < load; i++) {
		check(context_query_results[i] == 1, "context query result");
		measurement.work_units += context_query_results[i];
	}
	measurement.result_items = measurement.work_units;
	measurement.result_fingerprint =
		hash_context_results(load, pair, target_sequence);
	return measurement;
}

static void validate_context_mirror(uint64 target_sequence)
{
	const struct agent_context_header *header =
		(const struct agent_context_header *)eval_info.context_base;
	const struct agent_context_record *records =
		(const struct agent_context_record *)(
			eval_info.context_base + header->records_offset);

	memset(context_results, 0, sizeof(context_results));
	check(context_query(target_sequence, &context_results[0], 1) == 1,
	      "validated context syscall query");
	check(context_mirror_active_query(header, records, target_sequence,
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
		syscall_query = measure_context_variant(load, 0, 0,
							target_sequence);
		direct = measure_context_variant(load, 0, 1, target_sequence);
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
			if (pair_runs_ab(pair)) {
				syscall_query = measure_context_variant(
					load, pair, 0, target_sequence);
				direct = measure_context_variant(
					load, pair, 1, target_sequence);
			} else {
				direct = measure_context_variant(
					load, pair, 1, target_sequence);
				syscall_query = measure_context_variant(
					load, pair, 0, target_sequence);
			}
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
	uint64 values[15];
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
	semantic = functional_values_semantic("task1-semantic-v1", values,
					      15);
	print_functional_receipt("task1", values, 15, semantic);
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

static const struct agent_tool_desc_v2 *functional_tool_desc(int tool_id)
{
	for (int i = 0; i < AGENT_TOOL_COUNT; i++)
		if (functional_tools[i].tool_id == tool_id)
			return &functional_tools[i];
	return 0;
}

static uint64 functional_selected_schema_hash(void)
{
	static const int selected[] = {
		AGENT_TOOL_ECHO,
		AGENT_TOOL_QUERY_PROCESS,
		AGENT_TOOL_CAPABILITY_CHECK,
	};
	uint64 hash = FNV_OFFSET;

	hash = hash_bytes(hash, "task2-tool-schema-v1",
			  strlen("task2-tool-schema-v1"));
	for (uint i = 0; i < sizeof(selected) / sizeof(selected[0]); i++) {
		const struct agent_tool_desc_v2 *desc =
			functional_tool_desc(selected[i]);

		check(desc != 0 && desc->version == AGENT_CALL_VERSION_V2 &&
			      desc->size == sizeof(*desc),
		      "task2 selected tool schema");
		hash = hash_u64(hash, (uint64)(uint)desc->tool_id);
		hash = hash_u64(hash, (uint64)desc->param_count);
		hash = hash_u64(hash, desc->flags);
		hash = hash_bytes(hash, desc->name, strlen(desc->name));
		hash = hash_bytes(hash, desc->params, strlen(desc->params));
	}
	return hash;
}

static void run_functional_task2(void)
{
	static const char echo_payload[] = "eval-v2";
	uint64 values[20];
	uint64 echo_arg0 = AGENTEVAL_CHALLENGE ^ (uint64)(uint)getpid();
	uint64 echo_arg1 = AGENTEVAL_CHALLENGE ^ 0xa5a5a5a5a5a5a5a5ULL;
	uint64 schema_hash;
	uint64 echo_payload_hash;
	uint64 semantic;
	int callable_count = 0;
	int tool_count;

	memset(functional_tools, 0, sizeof(functional_tools));
	tool_count = tool_list(functional_tools, AGENT_TOOL_COUNT);
	check(tool_count == AGENT_TOOL_COUNT, "task2 complete tool list");
	for (int i = 0; i < tool_count; i++) {
		check(functional_tools[i].version == AGENT_CALL_VERSION_V2 &&
			      functional_tools[i].size ==
				      sizeof(functional_tools[i]),
		      "task2 tool list envelope");
		if ((functional_tools[i].flags & AGENT_TOOL_F_CALLABLE) != 0)
			callable_count++;
	}
	schema_hash = functional_selected_schema_hash();
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
	values[3] = functional_response.sequence;
	values[4] = functional_response.value0;
	values[5] = functional_response.value1;
	values[6] = functional_response.value2;
	values[7] = echo_payload_hash;

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
	values[8] = functional_response.sequence;
	values[9] = functional_response.value0;
	values[10] = functional_response.value1;
	values[11] = functional_response.value2;

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
	values[12] = functional_response.sequence;
	values[13] = functional_response.value0;
	values[14] = functional_response.value1;
	values[15] = functional_response.value2;

	functional_request_init(
		AGENT_TOOL_COUNT + 7, "echo", 0,
		semantic_token("task2-call-v1", 2, 0, 3));
	functional_tool_call("task2 unknown tool V2 call");
	check(functional_response.status == AGENT_STATUS_UNKNOWN_TOOL &&
		      functional_response.sequence == 0,
	      "task2 unknown tool rejected");
	values[16] = functional_response.sequence;
	values[17] = (uint64)(long long)functional_response.status;

	functional_param_string(0, "arg0", "wrong");
	functional_param_uint(1, "arg1", echo_arg1);
	functional_param_string(2, "payload", echo_payload);
	functional_request_init(
		AGENT_TOOL_ECHO, "echo", 3,
		semantic_token("task2-call-v1", 2, 0, 4));
	functional_tool_call("task2 wrong type V2 call");
	check(functional_response.status == AGENT_STATUS_BAD_TYPE &&
		      functional_response.sequence == 0,
	      "task2 wrong type rejected");
	values[18] = functional_response.sequence;
	values[19] = (uint64)(long long)functional_response.status;

	values[0] = (uint64)(uint)tool_count;
	values[1] = (uint64)(uint)callable_count;
	values[2] = schema_hash;
	semantic = functional_values_semantic("task2-semantic-v1", values,
					      20);
	print_functional_receipt("task2", values, 20, semantic);
}

static void fill_functional_context_record(struct agent_context_record *record,
					   int index)
{
	memset(record, 0, sizeof(*record));
	record->request_id = semantic_token("functional-context-v1",
					    FUNCTIONAL_TASK3_ROUNDS, 0,
					    index);
	record->arg0 = AGENTEVAL_CHALLENGE ^ (uint64)(uint)index;
	record->value0 = (uint64)(uint)index;
	record->tool_id = AGENT_TOOL_CONTEXT_PUSH;
	record->status = AGENT_STATUS_OK;
	strcpy(record->payload, "ctx-path");
	strcpy(record->result, "ctx-ok");
}

static uint64 functional_task3_semantic(void)
{
	struct agent_context_record *record = &functional_context_records[0];
	uint64 hash = FNV_OFFSET;

	hash = hash_bytes(hash, "task3-context-v1",
			  strlen("task3-context-v1"));
	hash = hash_u64(hash, AGENTEVAL_CHALLENGE);
	for (int i = 0; i < FUNCTIONAL_TASK3_ROUNDS; i++) {
		fill_functional_context_record(record, i);
		hash = hash_u64(hash, record->request_id);
		hash = hash_u64(hash, record->arg0);
		hash = hash_u64(hash, record->value0);
		hash = hash_u64(hash, (uint64)(uint)record->tool_id);
		hash = hash_u64(hash, (uint64)(uint)record->status);
		hash = hash_bytes(hash, record->payload,
				  strlen(record->payload));
		hash = hash_bytes(hash, record->result,
				  strlen(record->result));
	}
	return hash;
}

static void run_functional_task3(void)
{
	const struct agent_context_record *mirror;
	uint64 values[15];
	uint64 first_sequence;
	uint64 rollback_sequence;
	uint64 old_branch;
	uint64 new_branch;
	uint64 capacity;
	uint64 semantic;
	int direct_count;
	int query_count;
	int active_after_rollback;
	int clear_count;
	int fifo_count;

	check(context_clear() == AGENT_STATUS_OK, "task3 initial clear");
	for (int i = 0; i < FUNCTIONAL_TASK3_ROUNDS; i++) {
		fill_functional_context_record(&functional_context_records[0], i);
		check(context_push(&functional_context_records[0]) ==
			      AGENT_STATUS_OK,
		      "task3 challenge context push");
	}
	check(context_snapshot(&functional_context_header,
			       functional_context_records,
			       AGENT_CONTEXT_MAX_RECORDS) ==
		      FUNCTIONAL_TASK3_ROUNDS,
	      "task3 six-round snapshot");
	first_sequence = functional_context_records[0].sequence;
	rollback_sequence = functional_context_records[2].sequence;
	check(first_sequence == 1 && rollback_sequence == 3,
	      "task3 clear resets visible sequence");
	old_branch = functional_context_records[0].branch_generation;
	query_count = context_query(first_sequence, functional_context_records,
				    AGENT_CONTEXT_MAX_RECORDS);
	check(query_count == FUNCTIONAL_TASK3_ROUNDS,
	      "task3 syscall query count");
	mirror = (const struct agent_context_record *)(
		eval_info.context_base + functional_context_header.records_offset);
	direct_count = context_mirror_active_query(
		&functional_context_header, mirror, first_sequence,
		context_results, EVAL_MAX_LOAD);
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
	values[4] = rollback_sequence;
	values[5] = (uint64)(uint)active_after_rollback;
	values[6] = old_branch;
	values[7] = new_branch;
	values[8] = (uint64)(uint)clear_count;
	values[9] = capacity;
	values[10] = (uint64)(uint)fifo_count;
	values[11] = functional_context_header.dropped_records;
	values[12] = functional_context_header.oldest_sequence;
	values[13] = functional_context_header.latest_sequence;
	values[14] = functional_context_header.eviction_policy;
	semantic = functional_task3_semantic();
	print_functional_receipt("task3", values, 15, semantic);
}

static void run_functional_task4(void)
{
	uint64 values[13];
	uint64 semantic;

	check(functional_file.valid &&
		      functional_file.load == EVAL_MAX_LOAD &&
		      functional_file.pair == EVAL_PAIRS &&
		      functional_file.target_fid ==
			      2000 + functional_file.target_meta &&
		      functional_file.scan_work >=
			      (uint64)functional_file.load * EVAL_FILE_QUERIES &&
		      functional_file.index_work == EVAL_FILE_QUERIES &&
		      functional_file.result_items == EVAL_FILE_QUERIES &&
		      functional_file.inum != 0 &&
		      functional_file.incarnation != 0 &&
		      functional_file.fs_generation != 0,
	      "task4 saved real file-query binding");
	values[0] = (uint64)(uint)functional_file.load;
	values[1] = (uint64)(uint)functional_file.pair;
	values[2] = (uint64)(uint)functional_file.target_meta;
	values[3] = (uint64)(uint)functional_file.target_fid;
	values[4] = functional_file.scan_work;
	values[5] = functional_file.index_work;
	values[6] = functional_file.result_items;
	values[7] = functional_file.result_fingerprint;
	values[8] = functional_file.inum;
	values[9] = functional_file.incarnation;
	values[10] = functional_file.fs_generation;
	values[11] = (uint64)(uint)functional_file.used_index;
	values[12] = (uint64)(uint)functional_file.plan;
	semantic = functional_values_semantic("task4-semantic-v1", values,
					      13);
	print_functional_receipt("task4", values, 13, semantic);
}

static void run_functional_sentinel(int gate_fd, uint64 corr_id)
{
	char gate;
	int parent_pid = getppid();

	check(read(gate_fd, &gate, 1) == 1 && gate == 'G',
	      "task5 Sentinel gate");
	close(gate_fd);
	sleep(3);
	memset(&functional_event, 0, sizeof(functional_event));
	functional_event.type = AGENT_EVENT_MESSAGE;
	functional_event.corr_id = corr_id;
	strcpy(functional_event.payload, "eval-functional");
	check(agent_wake(parent_pid, &functional_event) == AGENT_STATUS_OK,
	      "task5 Sentinel delayed wake");
	exit(0);
}

static void run_functional_task5(void)
{
	uint64 values[18];
	uint64 corr_id = semantic_token("task5-event-v1", 2, 0, 0);
	uint64 heartbeat_sleep_before;
	uint64 heartbeat_wake_before;
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
	int timeout_status;
	char start = 'G';

	/* Task3 can leave a reserved CONTEXT_LIMIT event; isolate this wait. */
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
	helper_pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(helper_pid >= 0, "task5 create Sentinel helper");
	if (helper_pid == 0) {
		close(gate[1]);
		run_functional_sentinel(gate[0], corr_id);
	}
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
	check(write(gate[1], &start, 1) == 1,
	      "task5 release Sentinel helper");
	close(gate[1]);
	memset(&functional_event, 0, sizeof(functional_event));
	check(agent_wait(&functional_event, 50) == AGENT_STATUS_OK,
	      "task5 delayed message wait");
	check(agent_info(&functional_info_after) == AGENT_STATUS_OK,
	      "task5 info after message wait");
	check(functional_event.type == AGENT_EVENT_MESSAGE &&
		      functional_event.source_pid == helper_pid &&
		      functional_event.target_pid == getpid() &&
		      functional_event.corr_id == corr_id &&
		      strcmp(functional_event.payload, "eval-functional") == 0,
	      "task5 delayed message identity");
	check(functional_info_after.wait_sleep_count >
		      functional_info_before.wait_sleep_count &&
		      functional_info_after.wait_wakeup_count >
			      functional_info_before.wait_wakeup_count,
	      "task5 real sleep and wake counters");
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
	semantic = functional_values_semantic("task5-semantic-v1", values,
					      18);
	print_functional_receipt("task5", values, 18, semantic);
}

static void wait_for_file_scan(uint64 minimum_runs)
{
	for (int attempt = 0; attempt < 1000; attempt++) {
		check(agent_info(&eval_info) == 0, "file scan agent info");
		if (eval_info.file_scan_runs >= minimum_runs &&
		    eval_info.file_scan_pending == 0)
			return;
		sleep(10);
	}
	check(0, "background file scan did not quiesce");
}

static void run_evaluation(void)
{
	uint64 scan_runs;

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
	scan_runs = eval_info.file_scan_runs;
	check(agent_file_meta_init() == 0, "initialize evaluation metadata");
	wait_for_file_scan(scan_runs + 1);

	run_file_query_experiment();
	run_tool_batch_experiment();
	run_context_access_experiment();
	/* Functional acceptance is deliberately outside every timed interval. */
	run_functional_task1();
	run_functional_task2();
	run_functional_task3();
	run_functional_task4();
	run_functional_task5();
	printf("agenteval_ucore: worker passed\n");
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
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create evaluation orchestrator");
	if (pid == 0)
		run_evaluation();
	check(waitpid(pid, &status) == pid, "wait evaluation orchestrator");
	check(status == 0, "evaluation orchestrator status");
	printf("agenteval_ucore: parent passed\n");
	return 0;
}
