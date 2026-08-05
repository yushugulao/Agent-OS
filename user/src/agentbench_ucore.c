#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TOOL_OPS 256
#define SNAPSHOT_ROUNDS 16
#define DIRECT_READS 5000
#define FILE_OPS 64
#define WAIT_OPS 8
#define BUSY_POLL_OPS 128
#define PREFETCH_ROUNDS 64
#define BENCH_PROVENANCE_MAX 128
#define FILE_QUERY_MEASUREMENT_SCHEMA 2

static struct agent_op ops[AGENT_BATCH_MAX];
static struct agent_result results[AGENT_BATCH_MAX];
static struct agent_op digest_op;
static struct agent_result digest_result;
static struct agent_context_record records[AGENT_CONTEXT_MAX_RECORDS];
static struct agent_file_meta bench_meta;
static struct agent_file_query bench_file_query_arg;

/* Serialized benchmark phases can share their large syscall buffers. */
union bench_transient_scratch {
	struct agent_info info;
	struct agent_event event;
	struct agent_file_query_result file_query_result;
};

static union bench_transient_scratch bench_scratch;
static struct agent_file_prefetch_hint
	bench_prefetch_hints[AGENT_FILE_PREFETCH_MAX_HINTS];
static struct agent_timeline_record
	bench_timeline_records[AGENT_TIMELINE_MAX_RECORDS];
static struct agent_timeline_filter bench_timeline_filter;
static struct agent_provenance_edge
	bench_provenance_edges[BENCH_PROVENANCE_MAX];

struct file_query_measurement_receipt {
	int schema;
	int load;
	int traversal_ops;
	int traversal_records;
	int traversal_duration_us;
	int traversal_plan;
	int cold_index_ops;
	int cold_index_records;
	int cold_index_duration_us;
	int cold_rebuild_records;
	int warm_index_ops;
	int warm_index_records;
	int warm_index_duration_us;
	int warm_index_plan;
	int warm_index_candidates;
	uint64 warm_index_reason;
	int warm_index_cache_hit;
};

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentbench_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static void set_demo_meta(int fid, const char *physical, const char *stage,
			  const char *kind, const char *status,
			  const char *summary, uint64 deps)
{
	struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = fid;
	strcpy(meta.physical_name, physical);
	strcpy(meta.logical_path, physical);
	strcpy(meta.project, "lab-gene-x");
	strcpy(meta.workflow, "nightly-regression");
	strcpy(meta.run_id, "RUN-042");
	strcpy(meta.stage, stage);
	strcpy(meta.kind, kind);
	strcpy(meta.status, status);
	strcpy(meta.summary, summary);
	meta.dependency_mask = deps;
	meta.flags = AGENT_FILE_META_F_PERSIST;
	check(agent_file_meta_set(&meta) == 0, "demo meta set");
}

static void seed_demo_metadata(void)
{
	set_demo_meta(1, "r42align", "align", "artifact", "ok",
		      "align output is ready before injected failure",
		      agent_dependency_label_bit("analyze") |
			      agent_dependency_label_bit("report"));
	set_demo_meta(2, "r42anlz", "analyze", "status", "pending",
		      "analysis waits for align",
		      agent_dependency_label_bit("report"));
	set_demo_meta(3, "r42report", "report", "report", "pending",
		      "report waits for analyze", 0);
}

static int timeline_after_cursor(struct agent_timeline_record *record,
				 uint64 tick, int source, uint64 sequence)
{
	if (record->tick != tick)
		return record->tick > tick;
	if (record->source != source)
		return record->source > source;
	return record->sequence > sequence;
}

static int now_ms(void)
{
	return (int)get_mtime();
}

static int elapsed(int start, int end)
{
	int d = end - start;
	return d <= 0 ? 1 : d;
}

static uint64 now_us(void)
{
	TimeVal now;

	check(sys_get_time(&now, 0) == 0, "file benchmark clock");
	return now.sec * 1000000ULL + now.usec;
}

static int raw_elapsed_us(uint64 start, uint64 end)
{
	uint64 delta;

	check(end >= start, "file benchmark monotonic clock");
	delta = end - start;
	check(delta <= INT_MAX, "file benchmark duration range");
	return (int)delta;
}

static void print_perf(const char *name, int ops_count, int ticks,
		       int base_ops, int base_ticks)
{
	int speed = (ops_count * base_ticks * 100) / (ticks * base_ops);
	printf("agentbench_ucore: %s ops=%d ticks=%d ops_per_tick=%d speedup_x100=%d\n",
	       name, ops_count, ticks, ops_count / ticks, speed);
}

static void tick_stats(int *values, int n, int *min, int *avg, int *max)
{
	int sum = 0;

	*min = values[0];
	*max = values[0];
	for (int i = 0; i < n; i++) {
		if (values[i] < *min)
			*min = values[i];
		if (values[i] > *max)
			*max = values[i];
		sum += values[i];
	}
	*avg = sum / n;
	if (*avg <= 0)
		*avg = 1;
}

static void fill_echo_batch(uint64 base)
{
	for (int i = 0; i < AGENT_BATCH_MAX; i++) {
		memset(&ops[i], 0, sizeof(ops[i]));
		ops[i].version = AGENT_CALL_VERSION;
		ops[i].tool_id = AGENT_TOOL_ECHO;
		ops[i].request_id = base + i;
		ops[i].arg0 = i;
		ops[i].arg1 = i + 1;
		strcpy(ops[i].payload, "bench");
	}
}

static void make_code(char *out, char prefix, int n)
{
	out[0] = prefix;
	out[1] = '0' + (n / 100) % 10;
	out[2] = '0' + (n / 10) % 10;
	out[3] = '0' + n % 10;
	out[4] = 0;
}

static void seed_file_bench_metadata(void)
{
	char name[8];
	char status[8];

	for (int i = 0; i < 100; i++) {
		make_code(name, 'p', i);
		make_code(status, 'b', i);
		memset(&bench_meta, 0, sizeof(bench_meta));
		bench_meta.fid = 500 + i;
		strcpy(bench_meta.physical_name, name);
		strcpy(bench_meta.logical_path, name);
		strcpy(bench_meta.project, "bench");
		strcpy(bench_meta.workflow, "metadata");
		strcpy(bench_meta.run_id, "RUN-BENCH");
		strcpy(bench_meta.stage, "bulk");
		strcpy(bench_meta.kind, "artifact");
		strcpy(bench_meta.status, status);
		strcpy(bench_meta.summary, "benchmark metadata");
		bench_meta.dependency_mask = agent_dependency_label_bit("ready");
		check(agent_file_meta_set(&bench_meta) == 0, "bench meta set");
	}
}

static void seed_digest_file(void)
{
	int fd;
	char chunk[] = "agentbench-digest-content-block-0001\n";

	fd = open("bdigest", O_CREATE | O_RDWR | O_TRUNC);
	check(fd >= 0, "digest file open");
	for (int i = 0; i < 16; i++)
		check(write(fd, chunk, strlen(chunk)) ==
			      (ssize_t)strlen(chunk),
		      "digest file write");
	check(close(fd) == 0, "digest file close");
	memset(&bench_meta, 0, sizeof(bench_meta));
	bench_meta.fid = 900;
	strcpy(bench_meta.physical_name, "bdigest");
	strcpy(bench_meta.logical_path, "bdigest");
	strcpy(bench_meta.project, "bench");
	strcpy(bench_meta.workflow, "metadata");
	strcpy(bench_meta.run_id, "RUN-BENCH");
	strcpy(bench_meta.stage, "digest");
	strcpy(bench_meta.kind, "artifact");
	strcpy(bench_meta.status, "ok");
	strcpy(bench_meta.summary, "digest benchmark file");
	bench_meta.dependency_mask = agent_dependency_label_bit("ready");
	check(agent_file_meta_set(&bench_meta) == 0, "digest meta set");
}

static int bench_scalar(void)
{
	struct agent_op op;
	struct agent_result res;
	int start;

	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = AGENT_TOOL_ECHO;
	strcpy(op.payload, "scalar");
	start = now_ms();
	for (int i = 0; i < TOOL_OPS; i++) {
		op.request_id = i + 1;
		check(agent_run(&op, &res, 1, 0) == 1, "scalar run");
		check(res.status == AGENT_STATUS_OK, "scalar status");
	}
	return elapsed(start, now_ms());
}

static int bench_batch(void)
{
	int rounds = TOOL_OPS / AGENT_BATCH_MAX;
	int start = now_ms();

	for (int i = 0; i < rounds; i++) {
		fill_echo_batch(100000 + i * AGENT_BATCH_MAX);
		check(agent_run(ops, results, AGENT_BATCH_MAX, 0) ==
			      AGENT_BATCH_MAX,
		      "batch run");
	}
	return elapsed(start, now_ms());
}

static int bench_direct(struct agent_info *info)
{
	volatile struct agent_context_header *h;
	volatile uint64 sum = 0;
	int start;

	h = (struct agent_context_header *)info->context_base;
	start = now_ms();
	for (int i = 0; i < DIRECT_READS; i++)
		sum += h->latest_sequence;
	check(sum > 0, "direct sum");
	return elapsed(start, now_ms());
}

static int bench_query(void)
{
	int start = now_ms();

	for (int i = 0; i < SNAPSHOT_ROUNDS; i++)
		check(context_query(0, records, 1) >= 1, "context query");
	return elapsed(start, now_ms());
}

static int bench_snapshot(int *total)
{
	struct agent_context_header header;
	int n;
	int start = now_ms();

	*total = 0;
	for (int i = 0; i < SNAPSHOT_ROUNDS; i++) {
		n = context_snapshot(&header, records,
				     AGENT_CONTEXT_MAX_RECORDS);
		check(n >= 1, "context snapshot");
		*total += n;
	}
	return elapsed(start, now_ms());
}

static int bench_file_query_traversal_us(uint64 flags)
{
	uint64 start;

	memset(&bench_file_query_arg, 0, sizeof(bench_file_query_arg));
	bench_file_query_arg.flags = flags;
	bench_file_query_arg.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(bench_file_query_arg.project, "bench");
	strcpy(bench_file_query_arg.workflow, "metadata");
	strcpy(bench_file_query_arg.run_id, "RUN-BENCH");
	strcpy(bench_file_query_arg.stage, "bulk");
	strcpy(bench_file_query_arg.status, "b042");
	start = now_us();
	for (int i = 0; i < FILE_OPS; i++) {
		check(agent_file_query(&bench_file_query_arg,
				       &bench_scratch.file_query_result) >= 1,
		      "file query");
		check(bench_scratch.file_query_result.total_hits >= 1,
		      "file hits");
	}
	return raw_elapsed_us(start, now_us());
}

static void prepare_file_query(uint64 flags, int max_hits)
{
	memset(&bench_file_query_arg, 0, sizeof(bench_file_query_arg));
	bench_file_query_arg.flags = flags;
	bench_file_query_arg.max_hits = max_hits;
	strcpy(bench_file_query_arg.project, "bench");
	strcpy(bench_file_query_arg.workflow, "metadata");
	strcpy(bench_file_query_arg.run_id, "RUN-BENCH");
	strcpy(bench_file_query_arg.stage, "bulk");
	strcpy(bench_file_query_arg.status, "b042");
}

static int bench_file_query_cold_us(uint64 flags, int max_hits,
				    struct agent_file_query_result *result)
{
	uint64 start;

	prepare_file_query(flags, max_hits);
	start = now_us();
	check(agent_file_query(&bench_file_query_arg, result) >= 1,
	      "file query once");
	check(result->total_hits >= 1, "file query once hits");
	return raw_elapsed_us(start, now_us());
}

static int bench_file_query_warm_us(int operations,
				    struct agent_file_query_result *result)
{
	uint64 start;

	prepare_file_query(AGENT_FILE_QUERY_USE_INDEX,
			   AGENT_FILE_QUERY_MAX_HITS);
	start = now_us();
	for (int i = 0; i < operations; i++) {
		check(agent_file_query(&bench_file_query_arg, result) >= 1,
		      "warm index query");
		check(result->total_hits >= 1, "warm index hits");
		check((result->plan_reason &
		       AGENT_FILE_QUERY_REASON_CACHE_HIT) == 0,
		      "warm index must execute instead of using kernel cache");
	}
	return raw_elapsed_us(start, now_us());
}

static struct file_query_measurement_receipt
measure_file_query_paths(void)
{
	struct file_query_measurement_receipt receipt;

	memset(&receipt, 0, sizeof(receipt));
	receipt.schema = FILE_QUERY_MEASUREMENT_SCHEMA;
	receipt.traversal_ops = FILE_OPS;
	receipt.traversal_duration_us =
		bench_file_query_traversal_us(AGENT_FILE_QUERY_SCAN);
	receipt.load =
		bench_scratch.file_query_result.candidate_records;
	receipt.traversal_records =
		bench_scratch.file_query_result.scanned_records;
	receipt.traversal_plan =
		bench_scratch.file_query_result.plan;

	memset(&bench_scratch.file_query_result, 0,
	       sizeof(bench_scratch.file_query_result));
	receipt.cold_index_ops = 1;
	receipt.cold_index_duration_us =
		bench_file_query_cold_us(
			AGENT_FILE_QUERY_USE_INDEX,
			AGENT_FILE_QUERY_MAX_HITS,
			&bench_scratch.file_query_result);
	receipt.cold_index_records =
		bench_scratch.file_query_result.scanned_records;
	receipt.cold_rebuild_records =
		bench_scratch.file_query_result.index_rebuild_records;
	check((bench_scratch.file_query_result.plan_reason &
	       AGENT_FILE_QUERY_REASON_CACHE_HIT) == 0,
	      "cold index must include real index work");
	check(receipt.cold_rebuild_records == 0,
	      "incremental index avoids cold rebuild work");

	memset(&bench_scratch.file_query_result, 0,
	       sizeof(bench_scratch.file_query_result));
	receipt.warm_index_ops = FILE_OPS;
	receipt.warm_index_duration_us =
		bench_file_query_warm_us(FILE_OPS,
					 &bench_scratch.file_query_result);
	receipt.warm_index_records =
		bench_scratch.file_query_result.scanned_records;
	receipt.warm_index_plan =
		bench_scratch.file_query_result.plan;
	receipt.warm_index_candidates =
		bench_scratch.file_query_result.candidate_records;
	receipt.warm_index_reason =
		bench_scratch.file_query_result.plan_reason;
	receipt.warm_index_cache_hit =
		(receipt.warm_index_reason &
		 AGENT_FILE_QUERY_REASON_CACHE_HIT) != 0;

	check(receipt.traversal_plan ==
			      AGENT_FILE_QUERY_PLAN_SCAN,
	      "scan plan");
	check(receipt.warm_index_plan ==
			      AGENT_FILE_QUERY_PLAN_STATUS_INDEX,
	      "index plan");
	check((receipt.warm_index_reason &
	       AGENT_FILE_QUERY_REASON_STATUS_INDEX) != 0,
	      "index reason");
	check(!receipt.warm_index_cache_hit,
	      "kernel query cache disabled");
	check(receipt.load > 0 &&
	      receipt.load <= receipt.traversal_records,
	      "scan load is bounded by traversal work");
	check(receipt.warm_index_candidates > 0 &&
	      receipt.warm_index_candidates <= receipt.warm_index_records,
	      "index candidates are bounded by bucket work");
	check(receipt.cold_index_records == receipt.warm_index_records,
	      "cold and warm index candidates");
	check(bench_scratch.file_query_result.index_rebuild_records == 0,
	      "warm index excludes rebuild work");
	return receipt;
}

static int bench_file_digest(int *total_bytes)
{
	int start;

	memset(&digest_op, 0, sizeof(digest_op));
	digest_op.version = AGENT_CALL_VERSION;
	digest_op.tool_id = AGENT_TOOL_READ_FILE_DIGEST;
	strcpy(digest_op.payload, "bdigest");
	*total_bytes = 0;
	start = now_ms();
	for (int i = 0; i < FILE_OPS; i++) {
		digest_op.request_id = 92000 + i;
		check(agent_run(&digest_op, &digest_result, 1, 0) == 1,
		      "file digest run");
		check(digest_result.status == AGENT_STATUS_OK,
		      "file digest status");
		check(digest_result.value1 > 0, "file digest bytes");
		*total_bytes += digest_result.value1;
	}
	return elapsed(start, now_ms());
}

static void seed_prefetch_history(void)
{
	memset(&bench_file_query_arg, 0, sizeof(bench_file_query_arg));
	bench_file_query_arg.flags = AGENT_FILE_QUERY_USE_INDEX;
	bench_file_query_arg.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(bench_file_query_arg.project, "lab-gene-x");
	strcpy(bench_file_query_arg.workflow, "nightly-regression");
	strcpy(bench_file_query_arg.run_id, "RUN-042");
	strcpy(bench_file_query_arg.stage, "align");
	check(agent_file_query(&bench_file_query_arg,
			       &bench_scratch.file_query_result) >= 1,
	      "prefetch seed query");
	check(agent_file_prefetch_snapshot(bench_prefetch_hints,
					   AGENT_FILE_PREFETCH_MAX_HINTS) >= 1,
	      "prefetch seed snapshot");
}

static int bench_prefetch_snapshot(int *total)
{
	int n;
	int start = now_ms();

	*total = 0;
	for (int i = 0; i < PREFETCH_ROUNDS; i++) {
		n = agent_file_prefetch_snapshot(
			bench_prefetch_hints, AGENT_FILE_PREFETCH_MAX_HINTS);
		check(n >= 1, "prefetch snapshot");
		*total += n;
	}
	return elapsed(start, now_ms());
}

static int bench_timeline_snapshot(int *total)
{
	int n;
	int start = now_ms();

	*total = 0;
	for (int i = 0; i < SNAPSHOT_ROUNDS; i++) {
		n = agent_timeline_snapshot(bench_timeline_records,
					    AGENT_TIMELINE_MAX_RECORDS);
		check(n >= 1, "timeline snapshot");
		*total += n;
	}
	return elapsed(start, now_ms());
}

static int bench_timeline_query(int *total)
{
	int n;
	int start;

	memset(&bench_timeline_filter, 0, sizeof(bench_timeline_filter));
	bench_timeline_filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK;
	bench_timeline_filter.source_mask = AGENT_TIMELINE_SOURCE_MASK_PREFETCH;
	start = now_ms();
	*total = 0;
	for (int i = 0; i < SNAPSHOT_ROUNDS; i++) {
		n = agent_timeline_query(&bench_timeline_filter,
					 bench_timeline_records,
					 AGENT_TIMELINE_MAX_RECORDS);
		check(n >= 1, "timeline query");
		for (int j = 0; j < n; j++)
			check(bench_timeline_records[j].source ==
				      AGENT_TIMELINE_SOURCE_PREFETCH,
			      "timeline query source");
		*total += n;
	}
	return elapsed(start, now_ms());
}

static int bench_timeline_cursor_query(int *total)
{
	uint64 cursor_tick;
	uint64 cursor_sequence;
	int cursor_source;
	int n;
	int start;

	n = agent_timeline_snapshot(bench_timeline_records,
				    AGENT_TIMELINE_MAX_RECORDS);
	check(n > 1, "timeline cursor seed");
	cursor_tick = bench_timeline_records[n / 2].tick;
	cursor_source = bench_timeline_records[n / 2].source;
	cursor_sequence = bench_timeline_records[n / 2].sequence;
	memset(&bench_timeline_filter, 0, sizeof(bench_timeline_filter));
	bench_timeline_filter.flags = AGENT_TIMELINE_FILTER_AFTER_CURSOR;
	bench_timeline_filter.after_tick = cursor_tick;
	bench_timeline_filter.after_source = cursor_source;
	bench_timeline_filter.after_sequence = cursor_sequence;
	start = now_ms();
	*total = 0;
	for (int i = 0; i < SNAPSHOT_ROUNDS; i++) {
		n = agent_timeline_query(&bench_timeline_filter,
					 bench_timeline_records,
					 AGENT_TIMELINE_MAX_RECORDS);
		check(n >= 1, "timeline cursor query");
		for (int j = 0; j < n; j++)
			check(timeline_after_cursor(&bench_timeline_records[j],
						    cursor_tick, cursor_source,
						    cursor_sequence),
			      "timeline cursor order");
		*total += n;
	}
	return elapsed(start, now_ms());
}

static int bench_provenance_snapshot(int *total)
{
	int n;
	int start = now_ms();

	*total = 0;
	for (int i = 0; i < SNAPSHOT_ROUNDS; i++) {
		n = agent_provenance_snapshot(bench_provenance_edges,
					      BENCH_PROVENANCE_MAX);
		check(n >= 1, "provenance snapshot");
		*total += n;
	}
	return elapsed(start, now_ms());
}

static int bench_timeline_wait_ready(int *total)
{
	int start;

	start = now_ms();
	*total = agent_timeline_wait(0, 0);
	check(*total > 0, "timeline wait ready");
	return elapsed(start, now_ms());
}

static int bench_timeline_read_ready(int *total)
{
	int n;
	int start;

	start = now_ms();
	*total = 0;
	for (int i = 0; i < SNAPSHOT_ROUNDS; i++) {
		n = agent_timeline_read(0, bench_timeline_records,
					AGENT_TIMELINE_MAX_RECORDS, 0);
		check(n > 0, "timeline read ready");
		*total += n;
	}
	return elapsed(start, now_ms());
}

static int bench_busy_poll_query(void)
{
	int start;

	memset(&bench_file_query_arg, 0, sizeof(bench_file_query_arg));
	bench_file_query_arg.flags = AGENT_FILE_QUERY_USE_INDEX;
	bench_file_query_arg.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(bench_file_query_arg.project, "bench");
	strcpy(bench_file_query_arg.workflow, "metadata");
	strcpy(bench_file_query_arg.run_id, "RUN-BENCH");
	strcpy(bench_file_query_arg.status, "not-ready");
	start = now_ms();
	for (int i = 0; i < BUSY_POLL_OPS; i++) {
		check(agent_file_query(&bench_file_query_arg,
				       &bench_scratch.file_query_result) == 0,
		      "busy poll no hit");
	}
	return elapsed(start, now_ms());
}

static void run_waiter(int ready_fd, int ack_fd)
{
	struct agent_event event;
	char ch = 'r';

	check(agent_watch(AGENT_EVENT_MESSAGE, "bench") == 0, "watch");
	check(write(ready_fd, &ch, 1) == 1, "ready");
	for (int i = 0; i < WAIT_OPS; i++) {
		check(agent_wait(&event, 200) == AGENT_STATUS_OK, "wait");
		ch = 'a';
		check(write(ack_fd, &ch, 1) == 1, "ack");
	}
	exit(0);
}

static int bench_wait_wake(void)
{
	int ready[2];
	int ack[2];
	int pid;
	int status = 0;
	char ch;
	struct agent_event event;
	int start;

	check(pipe(ready) == 0, "ready pipe");
	check(pipe(ack) == 0, "ack pipe");
	check(agent_scope_delegate_fd(ready[1]) == AGENT_STATUS_OK &&
		      agent_scope_delegate_fd(ack[1]) == AGENT_STATUS_OK,
	      "delegate waiter pipe endpoints");
	pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(pid >= 0, "create waiter");
	if (pid == 0) {
		close(ready[0]);
		close(ack[0]);
		run_waiter(ready[1], ack[1]);
	}
	close(ready[1]);
	close(ack[1]);
	check(read(ready[0], &ch, 1) == 1, "read ready");
	check(agent_route_config(getpid(), pid, AGENT_IPC_EVENT_MESSAGE,
				 AGENT_IPC_ROUTE_GRANT) == AGENT_STATUS_OK,
	      "grant wake route");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	strcpy(event.payload, "bench");
	start = now_ms();
	for (int i = 0; i < WAIT_OPS; i++) {
		event.corr_id = i + 1;
		check(agent_wake(pid, &event) == 0, "wake");
		check(read(ack[0], &ch, 1) == 1, "read ack");
	}
	check(waitpid(pid, &status) == pid, "wait waiter");
	check(status == 0, "waiter status");
	return elapsed(start, now_ms());
}

static void check_timeout_and_heartbeat(void)
{
	uint64 timeout_count;
	uint64 old_heartbeat;

	check(agent_info(&bench_scratch.info) == 0, "info before timeout");
	timeout_count = bench_scratch.info.timeout_count;
	memset(&bench_scratch.event, 0, sizeof(bench_scratch.event));
	check(agent_wait(&bench_scratch.event, 1) == AGENT_STATUS_TIMEOUT,
	      "wait timeout");
	check(bench_scratch.event.status == AGENT_STATUS_TIMEOUT,
	      "timeout event status");
	check(strcmp(bench_scratch.event.payload, "timeout") == 0,
	      "timeout payload");
	check(agent_info(&bench_scratch.info) == 0, "info after timeout");
	check(bench_scratch.info.timeout_count == timeout_count + 1,
	      "timeout count");
	old_heartbeat = bench_scratch.info.last_heartbeat_tick;
	check(agent_heartbeat(7) == 0, "heartbeat set");
	check(agent_info(&bench_scratch.info) == 0,
	      "info after heartbeat");
	check(bench_scratch.info.heartbeat_interval == 7,
	      "heartbeat interval");
	check(bench_scratch.info.last_heartbeat_tick >= old_heartbeat,
	      "heartbeat tick");
	check(agent_heartbeat_stop() == 0, "heartbeat stop");
	printf("agentbench_ucore: timeout_heartbeat=1\n");
}

static void run_agent_bench(void)
{
	int scalar;
	int batch;
	int direct;
	int query;
	int snapshot;
	int snapshot_records;
	int digest;
	int digest_bytes;
	int digest_cache_hits;
	int digest_cache_misses;
	int prefetch;
	int prefetch_records;
	int timeline;
	int timeline_query;
	int timeline_cursor;
	int timeline_records;
	int timeline_query_records;
	int timeline_cursor_records;
	int provenance;
	int provenance_records;
	int timeline_wait_ready;
	int timeline_wait_records;
	int timeline_read_ready;
	int timeline_read_records;
	int waitwake;
	int busypoll;
	int scalar_runs[3];
	int batch_runs[3];
	int scalar_min;
	int scalar_max;
	int batch_min;
	int batch_max;
	uint64 digest_hits_before;
	uint64 digest_misses_before;

	check(agent_info(&bench_scratch.info) == 0, "info");
	check(bench_scratch.info.agent_role == AGENT_ROLE_ORCHESTRATOR,
	      "orchestrator role");
	check((bench_scratch.info.capability_mask & AGENT_CAP_META_WRITE) != 0,
	      "meta write cap");
	check((bench_scratch.info.capability_mask & AGENT_CAP_ORCHESTRATE) != 0,
	      "orchestrate cap");
	check(context_clear() == 0, "clear");
	check(agent_file_meta_init() == 0, "meta init");
	seed_demo_metadata();
	seed_file_bench_metadata();
	seed_digest_file();
	check_timeout_and_heartbeat();
	for (int i = 0; i < 3; i++) {
		scalar_runs[i] = bench_scalar();
		batch_runs[i] = bench_batch();
	}
	tick_stats(scalar_runs, 3, &scalar_min, &scalar, &scalar_max);
	tick_stats(batch_runs, 3, &batch_min, &batch, &batch_max);
	direct = bench_direct(&bench_scratch.info);
	query = bench_query();
	snapshot = bench_snapshot(&snapshot_records);
	const struct file_query_measurement_receipt file_query_receipt =
		measure_file_query_paths();
	seed_prefetch_history();
	check(agent_info(&bench_scratch.info) == 0, "digest info before");
	digest_hits_before = bench_scratch.info.file_digest_cache_hits;
	digest_misses_before = bench_scratch.info.file_digest_cache_misses;
	digest = bench_file_digest(&digest_bytes);
	check(agent_info(&bench_scratch.info) == 0, "digest info after");
	digest_cache_hits =
		(int)(bench_scratch.info.file_digest_cache_hits -
		      digest_hits_before);
	digest_cache_misses =
		(int)(bench_scratch.info.file_digest_cache_misses -
		      digest_misses_before);
	check(digest_cache_hits >= FILE_OPS - 1, "digest cache hits");
	check(digest_cache_misses >= 1, "digest cache miss");
	prefetch = bench_prefetch_snapshot(&prefetch_records);
	timeline = bench_timeline_snapshot(&timeline_records);
	timeline_query = bench_timeline_query(&timeline_query_records);
	timeline_cursor = bench_timeline_cursor_query(&timeline_cursor_records);
	provenance = bench_provenance_snapshot(&provenance_records);
	timeline_wait_ready = bench_timeline_wait_ready(&timeline_wait_records);
	timeline_read_ready = bench_timeline_read_ready(&timeline_read_records);
	busypoll = bench_busy_poll_query();
	waitwake = bench_wait_wake();

	printf("agentbench_ucore: repeated_ticks scalar_min=%d scalar_avg=%d scalar_max=%d batch_min=%d batch_avg=%d batch_max=%d\n",
	       scalar_min, scalar, scalar_max, batch_min, batch, batch_max);
	printf("agentbench_ucore: file_query_records scan_records=%d index_records=%d\n",
	       file_query_receipt.traversal_records,
	       file_query_receipt.warm_index_records);
	printf("agentbench_ucore: file_query_plan scan_plan=%d index_plan=%d index_reason=%d index_candidates=%d\n",
	       file_query_receipt.traversal_plan,
	       file_query_receipt.warm_index_plan,
	       (int)file_query_receipt.warm_index_reason,
	       file_query_receipt.warm_index_candidates);
	printf("agentbench_ucore: file_query_execution kernel_cache_hit=%d reason=%d\n",
	       file_query_receipt.warm_index_cache_hit,
	       (int)file_query_receipt.warm_index_reason);
	printf("agentbench_ucore: file_query_benchmark schema=2 unit=us load=%d traversal_ops=%d traversal_records=%d traversal_duration_us=%d cold_index_ops=%d cold_index_records=%d cold_index_duration_us=%d cold_rebuild_records=%d cold_rebuild_included=1 warm_index_ops=%d warm_index_records=%d warm_index_duration_us=%d status=measured\n",
	       file_query_receipt.load,
	       file_query_receipt.traversal_ops,
	       file_query_receipt.traversal_records,
	       file_query_receipt.traversal_duration_us,
	       file_query_receipt.cold_index_ops,
	       file_query_receipt.cold_index_records,
	       file_query_receipt.cold_index_duration_us,
	       file_query_receipt.cold_rebuild_records,
	       file_query_receipt.warm_index_ops,
	       file_query_receipt.warm_index_records,
	       file_query_receipt.warm_index_duration_us);
	printf("agentbench_ucore: file_digest bytes=%d ticks=%d preview=%s\n",
	       digest_bytes, digest, digest_result.result);
	printf("agentbench_ucore: file_digest_cache hits=%d misses=%d\n",
	       digest_cache_hits, digest_cache_misses);
	printf("agentbench_ucore: prefetch_records total=%d first_stage=%s\n",
	       prefetch_records, bench_prefetch_hints[0].hit.stage);
	printf("agentbench_ucore: timeline_records snapshot=%d query=%d cursor=%d\n",
	       timeline_records, timeline_query_records,
	       timeline_cursor_records);
	printf("agentbench_ucore: provenance_records snapshot=%d\n",
	       provenance_records);
	printf("agentbench_ucore: timeline_wait_ready records=%d ticks=%d\n",
	       timeline_wait_records, timeline_wait_ready);
	printf("agentbench_ucore: timeline_read_ready records=%d ticks=%d\n",
	       timeline_read_records, timeline_read_ready);
	printf("agentbench_ucore: case ops ticks ops_per_tick speedup_x100\n");
	print_perf("scalar_agent_run", TOOL_OPS, scalar, TOOL_OPS, scalar);
	print_perf("batch_agent_run", TOOL_OPS, batch, TOOL_OPS, scalar);
	print_perf("direct_context", DIRECT_READS, direct, TOOL_OPS, scalar);
	print_perf("context_query", SNAPSHOT_ROUNDS, query, SNAPSHOT_ROUNDS,
		   query);
	print_perf("context_snapshot", snapshot_records, snapshot,
		   SNAPSHOT_ROUNDS, query);
	print_perf("file_digest_read", digest_bytes, digest, digest_bytes,
		   digest);
	print_perf("file_prefetch_snapshot", prefetch_records, prefetch,
		   PREFETCH_ROUNDS, prefetch);
	print_perf("timeline_snapshot", timeline_records, timeline,
		   SNAPSHOT_ROUNDS, timeline);
	print_perf("timeline_query_prefetch", timeline_query_records,
		   timeline_query, timeline_records, timeline);
	print_perf("timeline_query_cursor", timeline_cursor_records,
		   timeline_cursor, timeline_records, timeline);
	print_perf("provenance_snapshot", provenance_records, provenance,
		   SNAPSHOT_ROUNDS, provenance);
	print_perf("timeline_wait_ready", timeline_wait_records,
		   timeline_wait_ready, timeline_wait_records,
		   timeline_wait_ready);
	print_perf("timeline_read_ready", timeline_read_records,
		   timeline_read_ready, timeline_read_records,
		   timeline_read_ready);
	print_perf("busy_poll_query", BUSY_POLL_OPS, busypoll,
		   BUSY_POLL_OPS, busypoll);
	print_perf("event_wait_wake", WAIT_OPS, waitwake, WAIT_OPS, waitwake);
	printf("agentbench_ucore: busy_poll_vs_wait busy_ops=%d busy_ticks=%d wait_ops=%d wait_ticks=%d\n",
	       BUSY_POLL_OPS, busypoll, WAIT_OPS, waitwake);
	printf("agentbench_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agentbench_ucore: Agent-OS on uCore benchmark\n");
	check(agent_info(&bench_scratch.info) == 0,
	      "query bench launcher");
	if (bench_scratch.info.is_agent) {
		check(bench_scratch.info.agent_role == AGENT_ROLE_ORCHESTRATOR,
		      "bench launcher is orchestrator");
		run_agent_bench();
	}
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create bench agent");
	if (pid == 0)
		run_agent_bench();
	check(waitpid(pid, &status) == pid, "wait bench");
	check(status == 0, "bench status");
	printf("agentbench_ucore: parent passed\n");
	return 0;
}
