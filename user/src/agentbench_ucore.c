#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TOOL_OPS 256
#define SNAPSHOT_ROUNDS 16
#define DIRECT_READS 5000
#define FILE_OPS 64
#define WAIT_OPS 8

static struct agent_op ops[AGENT_BATCH_MAX];
static struct agent_result results[AGENT_BATCH_MAX];
static struct agent_context_record records[AGENT_CONTEXT_MAX_RECORDS];
static struct agent_file_meta bench_meta;
static struct agent_file_query bench_file_query_arg;
static struct agent_file_query_result bench_file_query_result;

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentbench_ucore: check failed: %s\n", msg);
		exit(1);
	}
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

static void print_perf(const char *name, int ops_count, int ticks,
		       int base_ops, int base_ticks)
{
	int speed = (ops_count * base_ticks * 100) / (ticks * base_ops);
	printf("agentbench_ucore: %s ops=%d ticks=%d ops_per_tick=%d speedup_x100=%d\n",
	       name, ops_count, ticks, ops_count / ticks, speed);
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
		bench_meta.dependency_mask = AGENT_DEP_PREPARE;
		check(agent_file_meta_set(&bench_meta) == 0, "bench meta set");
	}
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

static int bench_file_query(uint64 flags)
{
	int start;

	memset(&bench_file_query_arg, 0, sizeof(bench_file_query_arg));
	bench_file_query_arg.flags = flags;
	bench_file_query_arg.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(bench_file_query_arg.project, "bench");
	strcpy(bench_file_query_arg.workflow, "metadata");
	strcpy(bench_file_query_arg.run_id, "RUN-BENCH");
	strcpy(bench_file_query_arg.stage, "bulk");
	strcpy(bench_file_query_arg.status, "b042");
	start = now_ms();
	for (int i = 0; i < FILE_OPS; i++) {
		check(agent_file_query(&bench_file_query_arg,
				       &bench_file_query_result) >= 1,
		      "file query");
		check(bench_file_query_result.total_hits >= 1, "file hits");
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
	struct agent_event event;
	struct agent_info before;
	struct agent_info after_timeout;
	struct agent_info after_heartbeat;
	uint64 old_heartbeat;

	check(agent_info(&before) == 0, "info before timeout");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 1) == AGENT_STATUS_TIMEOUT, "wait timeout");
	check(event.status == AGENT_STATUS_TIMEOUT, "timeout event status");
	check(strcmp(event.payload, "timeout") == 0, "timeout payload");
	check(agent_info(&after_timeout) == 0, "info after timeout");
	check(after_timeout.timeout_count == before.timeout_count + 1,
	      "timeout count");
	old_heartbeat = after_timeout.last_heartbeat_tick;
	check(agent_heartbeat(7) == 0, "heartbeat set");
	check(agent_info(&after_heartbeat) == 0, "info after heartbeat");
	check(after_heartbeat.heartbeat_interval == 7,
	      "heartbeat interval");
	check(after_heartbeat.last_heartbeat_tick >= old_heartbeat,
	      "heartbeat tick");
	printf("agentbench_ucore: timeout_heartbeat=1\n");
}

static void run_agent_bench(void)
{
	struct agent_info info;
	int scalar;
	int batch;
	int direct;
	int query;
	int snapshot;
	int snapshot_records;
	int scan;
	int index;
	int waitwake;

	check(agent_info(&info) == 0, "info");
	check(info.agent_role == AGENT_ROLE_ORCHESTRATOR, "orchestrator role");
	check((info.capability_mask & AGENT_CAP_META_WRITE) != 0,
	      "meta write cap");
	check((info.capability_mask & AGENT_CAP_ORCHESTRATE) != 0,
	      "orchestrate cap");
	check(context_clear() == 0, "clear");
	check(agent_file_meta_init() == 0, "meta init");
	seed_file_bench_metadata();
	check_timeout_and_heartbeat();
	scalar = bench_scalar();
	batch = bench_batch();
	direct = bench_direct(&info);
	query = bench_query();
	snapshot = bench_snapshot(&snapshot_records);
	scan = bench_file_query(AGENT_FILE_QUERY_SCAN);
	index = bench_file_query(AGENT_FILE_QUERY_USE_INDEX);
	waitwake = bench_wait_wake();

	printf("agentbench_ucore: case ops ticks ops_per_tick speedup_x100\n");
	print_perf("scalar_agent_run", TOOL_OPS, scalar, TOOL_OPS, scalar);
	print_perf("batch_agent_run", TOOL_OPS, batch, TOOL_OPS, scalar);
	print_perf("direct_context", DIRECT_READS, direct, TOOL_OPS, scalar);
	print_perf("context_query", SNAPSHOT_ROUNDS, query, SNAPSHOT_ROUNDS,
		   query);
	print_perf("context_snapshot", snapshot_records, snapshot,
		   SNAPSHOT_ROUNDS, query);
	print_perf("file_scan_query", FILE_OPS, scan, FILE_OPS, scan);
	print_perf("file_index_query", FILE_OPS, index, FILE_OPS, scan);
	print_perf("event_wait_wake", WAIT_OPS, waitwake, WAIT_OPS, waitwake);
	printf("agentbench_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agentbench_ucore: Agent-OS on uCore benchmark\n");
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create bench agent");
	if (pid == 0)
		run_agent_bench();
	check(waitpid(pid, &status) == pid, "wait bench");
	check(status == 0, "bench status");
	printf("agentbench_ucore: parent passed\n");
	return 0;
}
