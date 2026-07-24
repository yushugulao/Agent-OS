#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TARGET_FILE "scopeobj"
#define VOLATILE_FILE "scopevolatile"
#define META_SCAN_PRESSURE_FILE "qa119"
#define COMMON_FID  6101
#define VOLATILE_FID 6102
#define COMMON_ACTION_REQUEST 6201
#define SCOPE_LIFECYCLE_ROUNDS 132
#define META_RACE_WRITERS 3
#define META_RACE_FILES 4
#define META_WRITE_FLOOD 128
#define META_VOLATILE_WRITE_FLOOD 32
#define META_WRITE_READY 16
#define META_CROSS_QUERY_ROUNDS 32
#define META_CROSS_QUERY_MAX_MS 5000
#define META_SCAN_MIN_REST_TICKS 20
#define META_WRITEBACK_WAIT_ROUNDS 800
#define OBSERVE_CROSS_QUERY_ROUNDS 32
#define OBSERVE_CROSS_QUERY_MAX_MS 15000

struct scope_command {
	int operation;
	uint64 arg0;
	uint64 arg1;
};

struct scope_reply {
	int ok;
	uint scope_id;
	uint64 value0;
	uint64 value1;
};

struct observe_pressure_result {
	uint64 iterations;
	uint64 preemptions;
	uint64 context_records;
	uint64 span_records;
	uint64 timeline_records;
};

static struct agent_audit_record scope_audit_records[AGENT_AUDIT_MAX_RECORDS];
static struct agent_file_query_result scope_query_result;
static struct agent_file_query_result meta_race_result;
static struct agent_file_edit_state scope_lease_state;
static struct agent_info scope_writeback_before;
static struct agent_info scope_writeback_after;
static struct agent_info volatile_writeback_before;
static struct agent_info volatile_writeback_after;
static struct agent_context_record observe_context_scratch;
static struct agent_timeline_filter observe_filter_scratch;
static struct agent_audit_record
	observe_span_scratch[AGENT_AUDIT_MAX_RECORDS];
static struct agent_timeline_record
	observe_timeline_scratch[AGENT_AUDIT_MAX_RECORDS];
static struct observe_pressure_result observe_result_scratch;
static struct scope_reply observe_start_reply;
static struct scope_reply observe_progress_reply;
static struct scope_reply observe_join_reply;
static struct agent_file_hit scope_before_reload;
static volatile int metadata_writer_stop;
static volatile int observe_query_stop;
static int observe_ready_pipe[2] = {-1, -1};
static int observe_stop_pipe[2] = {-1, -1};
static int observe_result_pipe[2] = {-1, -1};
static int observe_child_pid = -1;
static int observe_child_status;
static char observe_ready_signal;
static char observe_stop_signal;

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agentscope_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static void write_exact(int fd, const void *buffer, size_t size,
			const char *message)
{
	const char *cursor = buffer;

	while (size > 0) {
		ssize_t written = write(fd, cursor, size);

		check(written > 0, message);
		cursor += written;
		size -= written;
	}
}

static void read_exact(int fd, void *buffer, size_t size,
		       const char *message)
{
	char *cursor = buffer;

	while (size > 0) {
		ssize_t received = read(fd, cursor, size);

		check(received > 0, message);
		cursor += received;
		size -= received;
	}
}

static uint current_scope(void)
{
	struct agent_info info;

	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0, "agent info");
	check(info.is_agent == 1, "workflow root is agent");
	check(info.agent_role == AGENT_ROLE_ORCHESTRATOR,
	      "workflow root role");
	check(info.filesystem_domain >= 3, "trusted dynamic scope");
	return (uint)info.filesystem_domain;
}

static void send_reply(int fd, uint scope_id, uint64 value0, uint64 value1)
{
	struct scope_reply reply;

	reply.ok = 1;
	reply.scope_id = scope_id;
	reply.value0 = value0;
	reply.value1 = value1;
	write_exact(fd, &reply, sizeof(reply), "send scope reply");
}

static __attribute__((noinline)) void
create_scoped_object(const char *contents, const char *summary)
{
	struct agent_file_meta meta;
	int fd;

	fd = open(TARGET_FILE, O_CREATE | O_WRONLY | O_TRUNC);
	check(fd >= 0, "create scoped file");
	check(write(fd, contents, strlen(contents)) ==
		      (ssize_t)strlen(contents),
	      "write scoped file");
	check(close(fd) == 0, "close scoped file");

	memset(&meta, 0, sizeof(meta));
	meta.fid = COMMON_FID;
	strcpy(meta.physical_name, TARGET_FILE);
	strcpy(meta.logical_path, "workflow/shared");
	strcpy(meta.project, "scope-project");
	strcpy(meta.workflow, "scope-test");
	strcpy(meta.run_id, "same-run");
	strcpy(meta.stage, "shared");
	strcpy(meta.kind, "artifact");
	strcpy(meta.status, "ready");
	strcpy(meta.summary, summary);
	check(agent_file_meta_set(&meta) == 0, "set scoped metadata");
}

static void query_scoped_object(const char *summary, const char *status)
{
	struct agent_file_query query;

	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.project, "scope-project");
	strcpy(query.workflow, "scope-test");
	strcpy(query.run_id, "same-run");
	strcpy(query.stage, "shared");
	strcpy(query.kind, "artifact");
	strcpy(query.status, status);
	memset(&scope_query_result, 0, sizeof(scope_query_result));
	check(agent_file_query(&query, &scope_query_result) == 1,
	      "scoped query count");
	check(scope_query_result.total_hits == 1 &&
	      scope_query_result.returned == 1,
	      "scoped query has one object");
	check(scope_query_result.hits[0].fid == COMMON_FID,
	      "scoped query fid");
	check(strcmp(scope_query_result.hits[0].physical_name, TARGET_FILE) == 0,
	      "scoped query physical name");
	check(strcmp(scope_query_result.hits[0].summary, summary) == 0,
	      "scoped query own summary");
}

static void wait_metadata_quiet(struct agent_info *snapshot)
{
	struct agent_info info;
	int stable = 0;

	for (int i = 0; i < META_WRITEBACK_WAIT_ROUNDS; i++) {
		memset(&info, 0, sizeof(info));
		check(agent_info(&info) == 0, "read metadata writeback state");
		if (!info.metadata_writeback_pending)
			stable++;
		else
			stable = 0;
		if (stable >= 3) {
			if (snapshot)
				*snapshot = info;
			return;
		}
		sleep(10);
	}
	check(0, "metadata writeback did not settle");
}

static void metadata_stop_listener(void *arg)
{
	char signal;
	int fd = (int)(long)arg;

	read_exact(fd, &signal, sizeof(signal), "metadata writer stop signal");
	__sync_synchronize();
	metadata_writer_stop = 1;
	exit(0);
}

static __attribute__((noinline)) void metadata_micro_writer(int ready_fd,
						     int stop_fd)
{
	char ready = 1;
	int stop_tid;
	int writes = 0;

	metadata_writer_stop = 0;
	stop_tid = thread_create(metadata_stop_listener, (void *)(long)stop_fd);
	check(stop_tid > 0, "create metadata writer stop listener");
	while (!metadata_writer_stop || writes < META_WRITE_FLOOD) {
		char value = 'a' + writes % 26;
		int fd = open(TARGET_FILE, O_WRONLY);

		check(fd >= 0, "open metadata write flood target");
		check(write(fd, &value, 1) == 1,
		      "write one-byte metadata update");
		check(close(fd) == 0, "close metadata write flood target");
		fd = open(META_SCAN_PRESSURE_FILE, O_WRONLY);
		check(fd >= 0, "open untracked scan pressure target");
		check(write(fd, &value, 1) == 1,
		      "write one-byte untracked scan pressure update");
		check(close(fd) == 0, "close untracked scan pressure target");
		writes++;
		if (writes == META_WRITE_READY)
			write_exact(ready_fd, &ready, sizeof(ready),
				    "metadata writer ready");
		if ((writes & 7) == 0)
			check(sched_yield() == 0, "yield metadata writer");
	}
	check(waittid(stop_tid) >= 0, "join metadata writer stop listener");
	check(close(ready_fd) == 0 && close(stop_fd) == 0,
	      "close metadata writer controls");
	exit(0);
}

static int metadata_cross_scope_queries(const char *summary)
{
	int completed = 0;

	for (int i = 0; i < META_CROSS_QUERY_ROUNDS; i++) {
		query_scoped_object(summary, "ready");
		completed++;
		check(sched_yield() == 0, "yield during cross-scope query probe");
	}
	return completed;
}

static void observe_query_stop_listener(void *arg)
{
	char signal;
	int fd = (int)(long)arg;

	read_exact(fd, &signal, sizeof(signal), "observation query stop signal");
	__sync_synchronize();
	observe_query_stop = 1;
	exit(0);
}

static void fill_observe_context(void)
{
	for (uint64 i = 0; i < AGENT_CONTEXT_MAX_RECORDS; i++) {
		memset(&observe_context_scratch, 0,
		       sizeof(observe_context_scratch));
		observe_context_scratch.tool_id = AGENT_TOOL_CONTEXT_PUSH;
		observe_context_scratch.request_id = 88000 + i;
		observe_context_scratch.status = AGENT_STATUS_OK;
		strcpy(observe_context_scratch.payload, "observe-pressure");
		strcpy(observe_context_scratch.result, "recorded");
		check(context_push(&observe_context_scratch) == AGENT_STATUS_OK,
		      "fill observation context");
	}
}

static uint64 require_observe_query_preemption(const char *message)
{
	long preemptions;

	preemptions = kernel_work_last_preemptions();
	check(preemptions > 0, message);
	return (uint64)preemptions;
}

static uint64 check_observe_ordered_indexes(void)
{
	uint64 preemptions = 0;
	uint64 span_id;
	int count;
	int copied;

	count = agent_span_trace_snapshot(0, 0);
	check(count > 0 && count <= AGENT_AUDIT_MAX_RECORDS,
	      "count bounded span trace");
	preemptions += require_observe_query_preemption(
		"span count query reaches fairness checkpoint");
	memset(observe_span_scratch, 0, sizeof(observe_span_scratch));
	copied = agent_span_trace_snapshot(observe_span_scratch,
					  AGENT_AUDIT_MAX_RECORDS);
	check(copied > 0 && copied <= AGENT_AUDIT_MAX_RECORDS,
	      "copy bounded span trace");
	preemptions += require_observe_query_preemption(
		"span copy query reaches fairness checkpoint");
	span_id = observe_span_scratch[0].span_id;
	check(span_id != 0, "span trace has kernel-issued span");
	for (int i = 0; i < copied; i++) {
		check(observe_span_scratch[i].span_id == span_id,
		      "span trace excludes foreign span");
		if (i > 0)
			check(observe_span_scratch[i].sequence >
				      observe_span_scratch[i - 1].sequence,
			      "span trace sequence is monotonic");
	}
	observe_result_scratch.span_records = copied;

	count = agent_timeline_query(&observe_filter_scratch, 0, 0);
	check(count > 0 && count <= AGENT_AUDIT_MAX_RECORDS,
	      "count bounded audit timeline");
	preemptions += require_observe_query_preemption(
		"timeline count query reaches fairness checkpoint");
	memset(observe_timeline_scratch, 0,
	       sizeof(observe_timeline_scratch));
	copied = agent_timeline_query(&observe_filter_scratch,
				      observe_timeline_scratch,
				      AGENT_AUDIT_MAX_RECORDS);
	check(copied > 0 && copied <= AGENT_AUDIT_MAX_RECORDS,
	      "copy bounded audit timeline");
	preemptions += require_observe_query_preemption(
		"timeline copy query reaches fairness checkpoint");
	for (int i = 0; i < copied; i++) {
		check(observe_timeline_scratch[i].source ==
			      AGENT_TIMELINE_SOURCE_AUDIT,
		      "audit-only timeline excludes other sources");
		check(observe_timeline_scratch[i].span_id == span_id,
		      "audit timeline excludes foreign span");
		if (i > 0) {
			check(observe_timeline_scratch[i].tick >
				      observe_timeline_scratch[i - 1].tick ||
				      (observe_timeline_scratch[i].tick ==
					       observe_timeline_scratch[i - 1]
						       .tick &&
				       observe_timeline_scratch[i].sequence >
					       observe_timeline_scratch[i - 1]
						       .sequence),
			      "audit timeline order is monotonic");
		}
		for (int j = 0; j < i; j++)
			check(observe_timeline_scratch[i].sequence !=
				      observe_timeline_scratch[j].sequence,
			      "audit timeline has no duplicate sequence");
	}
	observe_result_scratch.timeline_records = copied;
	return preemptions;
}

static __attribute__((noinline)) void
observe_query_pressure(int ready_fd, int stop_fd, int result_fd)
{
	char ready = 1;
	int stop_tid;

	memset(&observe_result_scratch, 0, sizeof(observe_result_scratch));
	fill_observe_context();
	observe_result_scratch.context_records = AGENT_CONTEXT_MAX_RECORDS;
	observe_query_stop = 0;
	stop_tid = thread_create(observe_query_stop_listener,
				 (void *)(long)stop_fd);
	check(stop_tid > 0, "create observation query stop listener");
	memset(&observe_filter_scratch, 0, sizeof(observe_filter_scratch));
	observe_filter_scratch.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK;
	observe_filter_scratch.source_mask =
		AGENT_TIMELINE_SOURCE_MASK_AUDIT;
	observe_result_scratch.preemptions +=
		check_observe_ordered_indexes();
	while (!observe_query_stop || observe_result_scratch.iterations == 0) {
		check(agent_span_trace_snapshot(0, 0) >= 0,
		      "count span trace under pressure");
		observe_result_scratch.preemptions +=
			require_observe_query_preemption(
				"span pressure query reaches fairness checkpoint");

		check(agent_timeline_query(&observe_filter_scratch, 0, 0) >= 0,
		      "count audit timeline under pressure");
		observe_result_scratch.preemptions +=
			require_observe_query_preemption(
				"timeline pressure query reaches fairness checkpoint");

		check(agent_provenance_snapshot(0, 0) >= 0,
		      "count provenance under pressure");
		observe_result_scratch.preemptions +=
			require_observe_query_preemption(
				"provenance pressure query reaches fairness checkpoint");
		observe_result_scratch.iterations++;
		if (observe_result_scratch.iterations == 1) {
			check(observe_result_scratch.preemptions > 0,
			      "observation query pressure reaches fairness checkpoints");
			write_exact(ready_fd, &ready, sizeof(ready),
				    "observation query pressure ready");
		}
	}
	check(observe_result_scratch.preemptions > 0,
	      "observation query pressure reaches fairness checkpoints");
	check(waittid(stop_tid) >= 0,
	      "join observation query stop listener");
	write_exact(result_fd, &observe_result_scratch,
		    sizeof(observe_result_scratch),
		    "send observation query pressure result");
	check(close(ready_fd) == 0 && close(stop_fd) == 0 &&
		      close(result_fd) == 0,
	      "close observation query pressure controls");
	exit(0);
}

static int observe_cross_scope_queries(void)
{
	uint64 preemptions = 0;
	int completed = 0;

	for (int i = 0; i < OBSERVE_CROSS_QUERY_ROUNDS; i++) {
		long last_preemptions;

		check(agent_audit_query(0, 0, 0) >= 0,
		      "cross-scope observation query");
		last_preemptions = kernel_work_last_preemptions();
		check(last_preemptions >= 0,
		      "read cross-scope audit query preemptions");
		preemptions += (uint64)last_preemptions;
		completed++;
	}
	check(preemptions > 0,
	      "cross-scope audit queries reach fairness checkpoints");
	return completed;
}

static __attribute__((noinline)) void create_volatile_object(void)
{
	struct agent_file_meta meta;

	memset(&meta, 0, sizeof(meta));
	meta.fid = VOLATILE_FID;
	strcpy(meta.physical_name, VOLATILE_FILE);
	strcpy(meta.logical_path, "workflow/volatile");
	strcpy(meta.project, "scope-project");
	strcpy(meta.workflow, "scope-test");
	strcpy(meta.run_id, "volatile-run");
	strcpy(meta.stage, "volatile");
	strcpy(meta.kind, "artifact");
	strcpy(meta.status, "memory-only");
	strcpy(meta.summary, "scope-B-volatile");
	check(agent_file_meta_set(&meta) == 0,
	      "create non-persistent scoped metadata");
}

static __attribute__((noinline)) void query_volatile_object(void)
{
	struct agent_file_query query;
	struct agent_file_query_result result;

	memset(&query, 0, sizeof(query));
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.physical_name, VOLATILE_FILE);
	memset(&result, 0, sizeof(result));
	check(agent_file_query(&query, &result) == 1,
	      "volatile scoped query count");
	check(result.total_hits == 1 && result.returned == 1 &&
	      result.hits[0].fid == VOLATILE_FID,
	      "volatile scoped metadata retained");
}

static __attribute__((noinline)) void volatile_micro_writer(void)
{
	for (int i = 0; i < META_VOLATILE_WRITE_FLOOD; i++) {
		char value = 'a' + i % 26;
		int fd = open(VOLATILE_FILE, O_WRONLY);

		check(fd >= 0, "open volatile metadata target");
		check(write(fd, &value, 1) == 1,
		      "write one-byte volatile metadata update");
		check(close(fd) == 0, "close volatile metadata target");
	}
	exit(0);
}

static __attribute__((noinline)) void check_volatile_writeback_isolation(void)
{
	int child_status = -1;
	int pid;

	wait_metadata_quiet(&volatile_writeback_before);
	pid = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(pid >= 0, "create volatile metadata writer");
	if (pid == 0)
		volatile_micro_writer();
	check(waitpid(pid, &child_status) == pid,
	      "wait volatile metadata writer");
	check(child_status == 0, "volatile metadata writer status");
	wait_metadata_quiet(&volatile_writeback_after);
	check(volatile_writeback_after.metadata_writeback_requests ==
		      volatile_writeback_before.metadata_writeback_requests &&
	      volatile_writeback_after.metadata_writeback_commits ==
		      volatile_writeback_before.metadata_writeback_commits,
	      "volatile writes do not enter persistent writeback");
	query_volatile_object();
}

static __attribute__((noinline)) void check_scan_pressure_untracked(void)
{
	struct agent_file_query query;
	int fd;

	fd = open(META_SCAN_PRESSURE_FILE, O_RDONLY);
	check(fd >= 0, "open scan pressure target");
	check(close(fd) == 0, "close scan pressure target");
	memset(&query, 0, sizeof(query));
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.physical_name, META_SCAN_PRESSURE_FILE);
	memset(&scope_query_result, 0, sizeof(scope_query_result));
	check(agent_file_query(&query, &scope_query_result) == 0 &&
	      scope_query_result.total_hits == 0,
	      "metadata quota leaves scan pressure target untracked");
}

static __attribute__((noinline)) void query_scoped_object_missing(void)
{
	struct agent_file_query query;

	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.project, "scope-project");
	strcpy(query.workflow, "scope-test");
	strcpy(query.run_id, "same-run");
	strcpy(query.stage, "shared");
	strcpy(query.kind, "artifact");
	strcpy(query.status, "ready");
	memset(&scope_query_result, 0, sizeof(scope_query_result));
	check(agent_file_query(&query, &scope_query_result) == 0,
	      "foreign metadata is not visible");
	check(scope_query_result.total_hits == 0 &&
	      scope_query_result.returned == 0,
	      "foreign query result is empty");
}

static void read_scoped_object(const char *contents)
{
	char buffer[16];
	ssize_t received;
	int fd;

	memset(buffer, 0, sizeof(buffer));
	fd = open(TARGET_FILE, O_RDONLY);
	check(fd >= 0, "open own scoped file");
	received = read(fd, buffer, sizeof(buffer) - 1);
	check(received == (ssize_t)strlen(contents), "read own scoped file");
	check(close(fd) == 0, "close own scoped file");
	buffer[received] = 0;
	check(strcmp(buffer, contents) == 0, "own scoped file contents");
}

static __attribute__((noinline)) void same_scope_probe(uint expected_scope)
{
	check(current_scope() == expected_scope, "child inherited scope");
	check(agent_workflow_create(AGENT_ROLE_ORCHESTRATOR) ==
		      AGENT_STATUS_DENIED,
	      "workflow factory authority is not inherited");
	read_scoped_object("alpha");
	query_scoped_object("scope-A", "ready");
	exit(0);
}

static void metadata_race_name(char *name, char group, int index)
{
	name[0] = 'm';
	name[1] = 'r';
	name[2] = group;
	name[3] = '0' + index;
	name[4] = 0;
}

static __attribute__((noinline)) void
metadata_race_writer(char group, int start_fd)
{
	struct agent_info info;
	struct agent_file_meta meta;
	char name[8];
	char run_id[] = "race-a";
	char token;

	read_exact(start_fd, &token, 1, "wait metadata race start");
	run_id[5] = group;
	for (int i = 0; i < META_RACE_FILES; i++) {
		int fd;

		metadata_race_name(name, group, i);
		fd = open(name, O_CREATE | O_WRONLY | O_TRUNC);
		check(fd >= 0, "create metadata race file");
		check(write(fd, &group, 1) == 1, "write metadata race file");
		check(close(fd) == 0, "close metadata race file");
		memset(&meta, 0, sizeof(meta));
		meta.fid = 7000 + (group - 'a') * 16 + i;
		strcpy(meta.physical_name, name);
		strcpy(meta.logical_path, name);
		strcpy(meta.project, "txn-race");
		strcpy(meta.workflow, "metadata-transaction");
		strcpy(meta.run_id, run_id);
		strcpy(meta.stage, "parallel");
		strcpy(meta.kind, "artifact");
		strcpy(meta.status, "committed");
		strcpy(meta.summary, "serialized metadata writer");
		meta.flags = AGENT_FILE_META_F_PERSIST;
		check(agent_file_meta_set(&meta) == 0,
		      "commit concurrent metadata");
	}
	memset(&info, 0, sizeof(info));
	check(agent_info(&info) == 0, "read metadata contention count");
	exit(info.metadata_txn_wait_count > 0 ? 1 : 0);
}

static __attribute__((noinline)) void
metadata_race_query(const char *run_id)
{
	struct agent_file_query query;

	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(query.project, "txn-race");
	strcpy(query.workflow, "metadata-transaction");
	strcpy(query.run_id, run_id);
	strcpy(query.stage, "parallel");
	strcpy(query.status, "committed");
	memset(&meta_race_result, 0, sizeof(meta_race_result));
	check(agent_file_query(&query, &meta_race_result) == META_RACE_FILES,
	      "concurrent metadata query count");
	check(meta_race_result.total_hits == META_RACE_FILES &&
		      meta_race_result.returned == META_RACE_FILES,
	      "concurrent metadata transaction retained every record");
}

static __attribute__((noinline)) void check_metadata_transactions(void)
{
	struct agent_file_meta meta;
	char name[8];
	char start[META_RACE_WRITERS] = { 'a', 'b', 'c' };
	int start_pipe[2];
	int children[META_RACE_WRITERS];
	int contentions = 0;
	int status;

	check(pipe(start_pipe) == 0, "create metadata race pipe");
	for (int i = 0; i < META_RACE_WRITERS; i++) {
		check(agent_scope_delegate_fd(start_pipe[0]) == AGENT_STATUS_OK,
		      "delegate metadata race gate");
		children[i] = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
		check(children[i] >= 0, "create metadata race writer");
		if (children[i] == 0)
			metadata_race_writer('a' + i, start_pipe[0]);
	}
	write_exact(start_pipe[1], start, sizeof(start),
		    "release metadata race writers");
	for (int i = 0; i < META_RACE_WRITERS; i++) {
		status = -1;
		check(waitpid(children[i], &status) == children[i],
		      "wait metadata race writer");
		check(status == 0 || status == 1,
		      "metadata race writer status");
		contentions += status;
	}
	// Coalesced metadata updates may finish without yielding on this
	// single-core kernel, so contention is telemetry rather than a timing
	// assertion. The reload/query checks below are the correctness contract.
	printf("agentscope_ucore: metadata_txn_contentions=%d\n", contentions);
	check(close(start_pipe[0]) == 0 && close(start_pipe[1]) == 0,
	      "close metadata race pipe");
	check(agent_file_meta_init() == 0,
	      "reload concurrent metadata transactions");
	metadata_race_query("race-a");
	metadata_race_query("race-b");
	metadata_race_query("race-c");
	for (int group = 0; group < META_RACE_WRITERS; group++)
		for (int i = 0; i < META_RACE_FILES; i++) {
			metadata_race_name(name, 'a' + group, i);
			memset(&meta, 0, sizeof(meta));
			meta.fid = 7000 + group * 16 + i;
			meta.flags = AGENT_FILE_META_F_DELETE;
			check(agent_file_meta_set(&meta) == 0,
			      "delete metadata race record");
			check(unlink(name) == 0, "delete metadata race file");
		}
}

static __attribute__((noinline)) int create_quota_files(char group, int max)
{
	char name[] = "qs000";
	int created = 0;

	name[1] = group;
	for (int i = 0; i < max; i++) {
		int fd;

		name[2] = '0' + (i / 100) % 10;
		name[3] = '0' + (i / 10) % 10;
		name[4] = '0' + i % 10;
		fd = open(name, O_CREATE | O_WRONLY | O_TRUNC);
		if (fd < 0)
			break;
		check(close(fd) == 0, "close quota object");
		created++;
	}
	return created;
}

static __attribute__((noinline)) void check_scope_storage_quota(void)
{
	int first;
	int second;
	int pid;

	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create first quota writer");
	if (pid == 0)
		exit(create_quota_files('a', 120));
	check(waitpid(pid, &first) == pid, "wait first quota writer");
	check(first == 120, "first writer consumes part of scope quota");

	pid = fork();
	check(pid >= 0, "create public quota writer");
	if (pid == 0)
		exit(create_quota_files('b', 70));
	check(waitpid(pid, &second) == pid, "wait public quota writer");
	check(second == 70,
	      "public principal is independent of workflow resource domain");
}

static __attribute__((noinline)) void
run_scoped_action(int receive_own_event)
{
	struct agent_event event;
	struct agent_op op;
	struct agent_result result;

	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = AGENT_TOOL_ACTION_COMMIT;
	op.request_id = COMMON_ACTION_REQUEST;
	strcpy(op.payload,
	       "label=shared;run_id=same-run;namespace=scope-project");
	memset(&result, 0, sizeof(result));
	check(agent_run(&op, &result, 1, 0) == 1,
	      "first scoped action call");
	check(result.status == AGENT_STATUS_OK, "first scoped action succeeds");
	memset(&result, 0, sizeof(result));
	check(agent_run(&op, &result, 1, 0) == 1,
	      "duplicate scoped action call");
	check(result.status == AGENT_STATUS_DUPLICATE,
	      "second scoped action is duplicate");
	query_scoped_object("action completed", "ok");
	if (!receive_own_event)
		return;
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 0) == AGENT_STATUS_OK,
	      "own workflow action event delivered");
	check(event.type == AGENT_EVENT_JOB_DONE,
	      "own workflow action event type");
	check(event.source_pid == getpid() &&
		      event.corr_id == COMMON_ACTION_REQUEST,
	      "own workflow action event identity");
	check(strcmp(event.payload,
		     "state=ok;label=shared;run_id=same-run;action=action_commit") ==
		      0,
	      "own workflow action event payload");
	check(agent_unwatch(AGENT_EVENT_JOB_DONE, "action=action_commit") == 1,
	      "remove scoped action watcher");
}

static __attribute__((noinline)) void
check_foreign_audit_hidden(int foreign_pid)
{
	struct agent_audit_filter filter;
	int count;

	check(foreign_pid > 0, "foreign audit pid");
	memset(scope_audit_records, 0, sizeof(scope_audit_records));
	count = agent_audit_snapshot(scope_audit_records,
				     AGENT_AUDIT_MAX_RECORDS);
	check(count > 0, "own scope audit snapshot");
	for (int i = 0; i < count; i++)
		check(scope_audit_records[i].pid != foreign_pid,
		      "foreign audit absent from snapshot");

	memset(&filter, 0, sizeof(filter));
	filter.flags = AGENT_AUDIT_FILTER_PID |
		       AGENT_AUDIT_FILTER_TOOL_ID;
	filter.pid = foreign_pid;
	filter.tool_id = AGENT_TOOL_ACTION_COMMIT;
	memset(scope_audit_records, 0, sizeof(scope_audit_records));
	check(agent_audit_query(&filter, scope_audit_records,
				AGENT_AUDIT_MAX_RECORDS) == 0,
	      "foreign action audit query is empty");
}

static __attribute__((noinline)) void
check_foreign_lease(uint64 lease_id, uint64 base_version)
{
	struct agent_file_edit_state state;

	check(lease_id != 0, "foreign lease id");
	memset(&state, 0, sizeof(state));
	check(agent_file_edit_commit(lease_id, base_version, &state) ==
		      AGENT_STATUS_NOT_FOUND,
	      "foreign lease commit hidden");
	check(agent_file_edit_abort(lease_id) == AGENT_STATUS_NOT_FOUND,
	      "foreign lease abort hidden");
	memset(&state, 0, sizeof(state));
	check(agent_file_edit_begin(TARGET_FILE, 0, 200, &state) == 0,
	      "same-name local lease succeeds");
	check(state.lease_id != 0 && state.lease_id != lease_id,
	      "local lease identity isolated");
	check(agent_file_edit_abort(state.lease_id) == 0,
	      "abort local isolated lease");
}

static void pipe_redelegation_probe(int reply_fd, int expected)
{
	char byte = 0;

	check(write(reply_fd, &byte, 0) == expected,
	      expected == 0 ? "explicit pipe redelegation works" :
			      "ambient pipe propagation denied");
	exit(0);
}

static void pipe_data_probe(int fd, int expected)
{
	char byte = 'D';

	check(write(fd, &byte, 1) == expected,
	      expected == 1 ? "delegated pipe transfers data" :
			      "undelegated pipe rejects data");
	exit(0);
}

struct competing_pipe_args {
	int protected_fd;
	int delegated_fd;
};

static void forked_child_stack_probe(void *arg)
{
	exit((int)(long)arg);
}

static void competing_pipe_spawn(void *arg)
{
	struct competing_pipe_args *args = arg;
	char byte = 'T';
	int join_status;
	int status = -1;
	int child_tid;
	int pid;

	if (agent_scope_delegate_fd(args->delegated_fd) != AGENT_STATUS_OK)
		exit(1);
	pid = agent_create_role(AGENT_ROLE_ARTIFACT);
	if (pid < 0)
		exit(2);
	if (pid == 0) {
		child_tid = thread_create(forked_child_stack_probe, 0);
		if (child_tid < 0)
			exit(3);
		while ((join_status = waittid(child_tid)) == -2)
			sched_yield();
		if (join_status != 0)
			exit(4);
		if (write(args->protected_fd, &byte, 0) != -1 ||
		    write(args->delegated_fd, &byte, 1) != 1)
			exit(5);
		exit(0);
	}
	if (waitpid(pid, &status) != pid || status != 0)
		exit(6);
	exit(0);
}

static void check_pipe_redelegation(int reply_fd)
{
	struct competing_pipe_args competing_args;
	int competing_pipe[2];
	int data_pipe[2];
	int failed_spawn_pipe[2];
	int replacement[2];
	int stale[2];
	int stale_read_fd;
	int stale_write_fd;
	int status = -1;
	int tid;
	int pid;

	pid = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(pid >= 0, "create undelegated pipe probe");
	if (pid == 0)
		pipe_redelegation_probe(reply_fd, -1);
	check(waitpid(pid, &status) == pid, "wait undelegated pipe probe");
	check(status == 0, "undelegated pipe probe status");

	check(pipe(competing_pipe) == 0,
	      "create cross-thread delegation pipe");
	competing_args.protected_fd = reply_fd;
	competing_args.delegated_fd = competing_pipe[1];
	check(agent_scope_delegate_fd(reply_fd) == AGENT_STATUS_OK,
	      "authorize explicit pipe redelegation");
	tid = thread_create(competing_pipe_spawn, &competing_args);
	check(tid > 0, "create competing delegation thread");
	check(waittid(tid) == 0, "thread cannot steal delegation ticket");
	{
		char byte = 0;

		read_exact(competing_pipe[0], &byte, 1,
			   "receive competing thread data");
		check(byte == 'T', "competing thread receives only own ticket");
	}
	pid = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(pid >= 0, "create delegated pipe probe");
	if (pid == 0) {
		char byte = 0;

		check(write(reply_fd, &byte, 0) == 0,
		      "issuing thread keeps own delegation ticket");
		check(write(competing_pipe[1], &byte, 0) == -1,
		      "issuing thread cannot consume sibling ticket");
		exit(0);
	}
	status = -1;
	check(waitpid(pid, &status) == pid, "wait delegated pipe probe");
	check(status == 0, "delegated pipe probe status");

	pid = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(pid >= 0, "create consumed-ticket pipe probe");
	if (pid == 0)
		pipe_redelegation_probe(reply_fd, -1);
	status = -1;
	check(waitpid(pid, &status) == pid, "wait consumed-ticket pipe probe");
	check(status == 0, "consumed-ticket pipe probe status");
	check(close(competing_pipe[0]) == 0 &&
		      close(competing_pipe[1]) == 0,
	      "close cross-thread delegation pipe");

	check(pipe(data_pipe) == 0, "create delegated data pipe");
	check(agent_scope_delegate_fd(data_pipe[1]) == AGENT_STATUS_OK,
	      "authorize delegated data pipe");
	pid = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(pid >= 0, "create delegated data probe");
	if (pid == 0)
		pipe_data_probe(data_pipe[1], 1);
	status = -1;
	check(waitpid(pid, &status) == pid, "wait delegated data probe");
	check(status == 0, "delegated data probe status");
	{
		char byte = 0;

		read_exact(data_pipe[0], &byte, 1,
			   "receive delegated pipe data");
		check(byte == 'D', "delegated pipe data value");
	}
	pid = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(pid >= 0, "create consumed data-ticket probe");
	if (pid == 0)
		pipe_data_probe(data_pipe[1], -1);
	status = -1;
	check(waitpid(pid, &status) == pid,
	      "wait consumed data-ticket probe");
	check(status == 0, "consumed data-ticket probe status");
	check(close(data_pipe[0]) == 0 && close(data_pipe[1]) == 0,
	      "close delegated data pipe");

	check(pipe(failed_spawn_pipe) == 0,
	      "create failed-spawn delegation pipe");
	check(agent_scope_delegate_fd(failed_spawn_pipe[1]) ==
		      AGENT_STATUS_OK,
	      "authorize failed-spawn pipe");
	check(agent_create_role(99) < 0, "reject invalid delegated spawn");
	pid = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(pid >= 0, "create failed-spawn ticket probe");
	if (pid == 0)
		pipe_data_probe(failed_spawn_pipe[1], -1);
	status = -1;
	check(waitpid(pid, &status) == pid,
	      "wait failed-spawn ticket probe");
	check(status == 0, "failed-spawn ticket probe status");
	check(close(failed_spawn_pipe[0]) == 0 &&
		      close(failed_spawn_pipe[1]) == 0,
	      "close failed-spawn delegation pipe");

	check(pipe(stale) == 0, "create stale-ticket pipe");
	stale_read_fd = stale[0];
	stale_write_fd = stale[1];
	check(agent_scope_delegate_fd(stale_write_fd) == AGENT_STATUS_OK,
	      "authorize stale-ticket pipe");
	check(close(stale_read_fd) == 0 && close(stale_write_fd) == 0,
	      "close stale-ticket pipe");
	check(pipe(replacement) == 0, "reuse stale-ticket fd slots");
	check(replacement[0] == stale_read_fd &&
		      replacement[1] == stale_write_fd,
	      "pipe fd slots reused deterministically");
	pid = agent_create_role(AGENT_ROLE_ARTIFACT);
	check(pid >= 0, "create stale-ticket reuse probe");
	if (pid == 0)
		pipe_data_probe(replacement[1], -1);
	status = -1;
	check(waitpid(pid, &status) == pid, "wait stale-ticket reuse probe");
	check(status == 0, "stale-ticket reuse probe status");
	check(close(replacement[0]) == 0 && close(replacement[1]) == 0,
	      "close replacement pipe");
}

static void run_scope_root(char identity, int command_fd, int reply_fd,
			   int peer_pid)
{
	const char *contents = identity == 'A' ? "alpha" : "bravo";
	const char *summary = identity == 'A' ? "scope-A" : "scope-B";
	int writeback_ready[2] = {-1, -1};
	int writeback_stop[2] = {-1, -1};
	int writeback_child = -1;
	uint scope_id = current_scope();

	check(agent_workflow_create(AGENT_ROLE_ORCHESTRATOR) ==
		      AGENT_STATUS_DENIED,
	      "workflow root cannot mint another workflow");
	memset(&scope_lease_state, 0, sizeof(scope_lease_state));
	memset(&scope_writeback_before, 0, sizeof(scope_writeback_before));
	send_reply(reply_fd, scope_id, 0, 0);
	for (;;) {
		struct scope_command command;
		uint64 value0 = 0;
		uint64 value1 = 0;

		memset(&command, 0, sizeof(command));
		read_exact(command_fd, &command, sizeof(command),
			   "receive scope command");
		if (command.operation == 'N') {
			int fd = open(TARGET_FILE, O_RDONLY);

			check(fd < 0, "foreign file is not visible");
			query_scoped_object_missing();
		} else if (command.operation == 'C') {
			create_scoped_object(contents, summary);
			query_scoped_object(summary, "ready");
		} else if (command.operation == 'V') {
			read_scoped_object(contents);
			query_scoped_object(summary, "ready");
		} else if (command.operation == 'D') {
			check(identity == 'B', "volatile metadata owner");
			create_volatile_object();
			query_volatile_object();
		} else if (command.operation == 'M') {
			check(identity == 'A', "scope-local reload owner");
			check(agent_file_meta_init() == 0,
			      "reload only caller metadata scope");
		} else if (command.operation == 'E') {
			check(identity == 'B', "volatile metadata verifier");
			query_volatile_object();
		} else if (command.operation == 'Z') {
			check(identity == 'B', "volatile writeback verifier");
			check_volatile_writeback_isolation();
			value0 = META_VOLATILE_WRITE_FLOOD;
		} else if (command.operation == 'S') {
			int status = 0;
			int pid;

			check(identity == 'A', "same-scope command owner");
			pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
			check(pid >= 0, "create same-scope child");
			if (pid == 0)
				same_scope_probe(scope_id);
			check(waitpid(pid, &status) == pid, "wait same-scope child");
			check(status == 0, "same-scope child status");
		} else if (command.operation == 'T') {
			check(identity == 'A', "storage quota command owner");
			check_scope_storage_quota();
		} else if (command.operation == 'Y') {
			check(identity == 'A', "metadata transaction owner");
			check_metadata_transactions();
		} else if (command.operation == 'F') {
			char ready;

			check(identity == 'A' && writeback_child < 0,
			      "metadata write flood owner");
			wait_metadata_quiet(&scope_writeback_before);
			check_scan_pressure_untracked();
			check(pipe(writeback_ready) == 0 && pipe(writeback_stop) == 0,
			      "create metadata writer controls");
			check(agent_scope_delegate_fd(writeback_ready[1]) ==
				      AGENT_STATUS_OK &&
				      agent_scope_delegate_fd(writeback_stop[0]) ==
					      AGENT_STATUS_OK,
			      "delegate metadata writer controls");
			writeback_child = agent_create_role(AGENT_ROLE_ARTIFACT);
			check(writeback_child >= 0,
			      "create low-privilege metadata writer");
			if (writeback_child == 0) {
				check(close(writeback_ready[0]) < 0 &&
					      close(writeback_stop[1]) < 0,
				      "unused metadata controls not inherited");
				metadata_micro_writer(writeback_ready[1],
						      writeback_stop[0]);
			}
			check(close(writeback_ready[1]) == 0 &&
				      close(writeback_stop[0]) == 0,
			      "close parent metadata controls");
			read_exact(writeback_ready[0], &ready, sizeof(ready),
				   "metadata writer live barrier");
			check(close(writeback_ready[0]) == 0,
			      "close metadata writer ready control");
			writeback_ready[0] = writeback_ready[1] = -1;
			value0 = writeback_child;
		} else if (command.operation == 'P') {
			int64 started;
			int64 elapsed;

			check(identity == 'B', "cross-scope metadata query owner");
			started = get_mtime();
			value0 = metadata_cross_scope_queries(summary);
			elapsed = get_mtime() - started;
			check(elapsed >= 0 && elapsed <= META_CROSS_QUERY_MAX_MS,
			      "cross-scope metadata query latency bound");
			value1 = elapsed;
		} else if (command.operation == 'J') {
			char stop = 1;
			uint64 requests;
			uint64 coalesced;
			uint64 commits;
			uint64 scan_runs;
			uint64 elapsed_ticks;
			int child_status = -1;

			check(identity == 'A' && writeback_child > 0,
			      "metadata write flood join owner");
			write_exact(writeback_stop[1], &stop, sizeof(stop),
				    "stop metadata writer");
			check(close(writeback_stop[1]) == 0,
			      "close metadata writer stop control");
			writeback_stop[0] = writeback_stop[1] = -1;
			check(waitpid(writeback_child, &child_status) == writeback_child,
			      "wait low-privilege metadata writer");
			check(child_status == 0,
			      "low-privilege metadata writer status");
			writeback_child = -1;
			wait_metadata_quiet(&scope_writeback_after);
			requests = scope_writeback_after.metadata_writeback_requests -
				   scope_writeback_before.metadata_writeback_requests;
			coalesced = scope_writeback_after.metadata_writeback_coalesced -
				    scope_writeback_before.metadata_writeback_coalesced;
			commits = scope_writeback_after.metadata_writeback_commits -
				  scope_writeback_before.metadata_writeback_commits;
			scan_runs = scope_writeback_after.file_scan_runs -
				    scope_writeback_before.file_scan_runs;
			elapsed_ticks = scope_writeback_after.current_tick -
					scope_writeback_before.current_tick;
			check(requests >= META_WRITE_FLOOD,
			      "every micro-write enters scoped writeback accounting");
			check(commits > 0 && commits * 8 <= requests,
			      "micro-writes are bounded into writeback batches");
			check(coalesced + commits == requests,
			      "writeback accounting classifies every request");
			check(scope_writeback_after.metadata_writeback_dirty ==
				      scope_writeback_after.metadata_writeback_durable &&
			      !scope_writeback_after.metadata_writeback_pending,
			      "writeback reaches a durable generation");
			check(scan_runs <= elapsed_ticks / META_SCAN_MIN_REST_TICKS + 2,
			      "scan pressure obeys global cooldown");
			query_scoped_object(summary, "ready");
			scope_before_reload = scope_query_result.hits[0];
			check(agent_file_meta_init() == 0,
			      "reload coalesced metadata checkpoint");
			query_scoped_object(summary, "ready");
			check(scope_query_result.hits[0].size ==
				      scope_before_reload.size &&
			      scope_query_result.hits[0].fs_generation >=
				      scope_before_reload.fs_generation,
			      "coalesced metadata survives forced reload");
			value0 = requests;
			value1 = commits;
		} else if (command.operation == 'O') {
			check(identity == 'A' && observe_child_pid < 0,
			      "observation query pressure owner");
			check(pipe(observe_ready_pipe) == 0 &&
				      pipe(observe_stop_pipe) == 0 &&
				      pipe(observe_result_pipe) == 0,
			      "create observation query controls");
			check(agent_scope_delegate_fd(observe_ready_pipe[1]) ==
				      AGENT_STATUS_OK &&
				      agent_scope_delegate_fd(observe_stop_pipe[0]) ==
					      AGENT_STATUS_OK &&
				      agent_scope_delegate_fd(observe_result_pipe[1]) ==
					      AGENT_STATUS_OK,
			      "delegate observation query controls");
			observe_child_pid =
				agent_create_role(AGENT_ROLE_SENTINEL);
			check(observe_child_pid >= 0,
			      "create low-privilege observation query pressure");
			if (observe_child_pid == 0) {
				check(close(observe_ready_pipe[0]) < 0 &&
					      close(observe_stop_pipe[1]) < 0 &&
					      close(observe_result_pipe[0]) < 0,
				      "unused observation controls not inherited");
				observe_query_pressure(observe_ready_pipe[1],
						       observe_stop_pipe[0],
						       observe_result_pipe[1]);
			}
			check(close(observe_ready_pipe[1]) == 0 &&
				      close(observe_stop_pipe[0]) == 0 &&
				      close(observe_result_pipe[1]) == 0,
			      "close parent observation controls");
			read_exact(observe_ready_pipe[0],
				   &observe_ready_signal,
				   sizeof(observe_ready_signal),
				   "observation query live barrier");
			check(close(observe_ready_pipe[0]) == 0,
			      "close observation query ready control");
			observe_ready_pipe[0] = observe_ready_pipe[1] = -1;
			check(agent_audit_query(0, 0, 0) >= 0,
			      "orchestrator audit count query");
			value1 = require_observe_query_preemption(
				"orchestrator audit query reaches fairness checkpoint");
			value0 = observe_child_pid;
		} else if (command.operation == 'G') {
			int64 started;
			int64 elapsed;

			check(identity == 'B',
			      "cross-scope observation query owner");
			started = get_mtime();
			value0 = observe_cross_scope_queries();
			elapsed = get_mtime() - started;
			check(elapsed >= 0 &&
				      elapsed <= OBSERVE_CROSS_QUERY_MAX_MS,
			      "cross-scope observation query latency bound");
			value1 = elapsed;
		} else if (command.operation == 'K') {
			check(identity == 'A' && observe_child_pid > 0,
			      "observation query pressure join owner");
			observe_stop_signal = 1;
			write_exact(observe_stop_pipe[1],
				    &observe_stop_signal,
				    sizeof(observe_stop_signal),
				    "stop observation query pressure");
			check(close(observe_stop_pipe[1]) == 0,
			      "close observation query stop control");
			observe_stop_pipe[0] = observe_stop_pipe[1] = -1;
			memset(&observe_result_scratch, 0,
			       sizeof(observe_result_scratch));
			read_exact(observe_result_pipe[0],
				   &observe_result_scratch,
				   sizeof(observe_result_scratch),
				   "receive observation query pressure result");
			check(close(observe_result_pipe[0]) == 0,
			      "close observation query result control");
			observe_result_pipe[0] = observe_result_pipe[1] = -1;
			observe_child_status = -1;
			check(waitpid(observe_child_pid,
				      &observe_child_status) ==
				      observe_child_pid,
			      "wait low-privilege observation query pressure");
			check(observe_child_status == 0,
			      "low-privilege observation query status");
			check(observe_result_scratch.iterations > 0 &&
				      observe_result_scratch.preemptions > 0 &&
				      observe_result_scratch.context_records ==
					      AGENT_CONTEXT_MAX_RECORDS &&
				      observe_result_scratch.span_records > 0 &&
				      observe_result_scratch.timeline_records > 0,
			      "observation query pressure result");
			observe_child_pid = -1;
			value0 = observe_result_scratch.iterations;
			value1 = observe_result_scratch.preemptions;
		} else if (command.operation == 'W') {
			check(identity == 'B', "scoped watcher owner");
			check(agent_watch(AGENT_EVENT_JOB_DONE,
					  "action=action_commit") == 0,
			      "install scoped action watcher");
		} else if (command.operation == 'A') {
			run_scoped_action(identity == 'B');
		} else if (command.operation == 'U') {
			struct agent_event event;

			check(identity == 'B', "audit isolation owner");
			memset(&event, 0, sizeof(event));
			check(agent_wait(&event, 0) == AGENT_STATUS_TIMEOUT,
			      "foreign workflow event not delivered");
			check_foreign_audit_hidden(peer_pid);
		} else if (command.operation == 'I') {
			struct agent_event event;

			check(identity == 'B' && peer_pid > 0,
			      "cross-scope IPC probe owner");
			check(agent_route_config(peer_pid, getpid(),
					 AGENT_IPC_EVENT_MESSAGE,
					 AGENT_IPC_ROUTE_GRANT) ==
				      AGENT_STATUS_DENIED,
			      "target consent cannot cross workflow scope");
			memset(&event, 0, sizeof(event));
			event.type = AGENT_EVENT_MESSAGE;
			event.corr_id = 6301;
			strcpy(event.payload, "cross-scope-message");
			check(agent_wake(peer_pid, &event) == AGENT_STATUS_DENIED,
			      "message delivery cannot cross workflow scope");
		} else if (command.operation == 'L') {
			check(identity == 'A', "lease owner scope");
			memset(&scope_lease_state, 0, sizeof(scope_lease_state));
			check(agent_file_edit_begin(TARGET_FILE, 0, 200,
						    &scope_lease_state) == 0,
			      "begin scoped lease");
			value0 = scope_lease_state.lease_id;
			value1 = scope_lease_state.base_version;
		} else if (command.operation == 'X') {
			check(identity == 'B', "foreign lease probe scope");
			check_foreign_lease(command.arg0, command.arg1);
		} else if (command.operation == 'R') {
			check(identity == 'A' && scope_lease_state.lease_id != 0,
			      "release scoped lease owner");
			check(agent_file_edit_abort(scope_lease_state.lease_id) == 0,
			      "release scoped lease");
			memset(&scope_lease_state, 0, sizeof(scope_lease_state));
		} else if (command.operation == 'H') {
			check(identity == 'A', "pipe redelegation probe owner");
			check_pipe_redelegation(reply_fd);
		} else if (command.operation == 'Q') {
			send_reply(reply_fd, scope_id, 0, 0);
			exit(0);
		} else {
			check(0, "unknown scope command");
		}
		send_reply(reply_fd, scope_id, value0, value1);
	}
}

static struct scope_reply receive_reply(int fd, const char *message)
{
	struct scope_reply reply;

	read_exact(fd, &reply, sizeof(reply), message);
	check(reply.ok == 1 && reply.scope_id >= 3, message);
	return reply;
}

static struct scope_reply run_command(int command_fd, int reply_fd,
				      int operation, uint64 arg0, uint64 arg1,
				      uint expected_scope, const char *message)
{
	struct scope_command command;
	struct scope_reply reply;

	memset(&command, 0, sizeof(command));
	command.operation = operation;
	command.arg0 = arg0;
	command.arg1 = arg1;
	write_exact(command_fd, &command, sizeof(command), message);
	reply = receive_reply(reply_fd, message);
	check(reply.scope_id == expected_scope, message);
	return reply;
}

static __attribute__((noinline)) void scope_lifecycle_child(void)
{
	int fd = open("scopegc", O_CREATE | O_WRONLY | O_TRUNC);

	check(fd >= 0, "create lifecycle object");
	check(write(fd, "x", 1) == 1, "write lifecycle object");
	check(close(fd) == 0, "close lifecycle object");
	exit(0);
}

static int create_workflow_after_reap(void)
{
	for (int i = 0; i < 2000; i++) {
		int pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);

		if (pid >= 0)
			return pid;
		sleep(1);
	}
	return -1;
}

static void check_scope_lifecycle(void)
{
	for (int i = 0; i < SCOPE_LIFECYCLE_ROUNDS; i++) {
		int status = 0;
		int pid = create_workflow_after_reap();

		check(pid >= 0, "allocate recycled workflow scope");
		if (pid == 0)
			scope_lifecycle_child();
		check(waitpid(pid, &status) == pid,
		      "wait recycled workflow scope");
		check(status == 0, "recycled workflow scope status");
	}
}

static void check_scope_capacity_reservation(void)
{
	int children[3];
	int delegated_pipe[2];
	int replacement;
	int status = 0;

	for (int i = 0; i < 3; i++) {
		children[i] = create_workflow_after_reap();
		check(children[i] >= 0, "admit reserved workflow partition");
		if (children[i] == 0) {
			sleep(5000);
			exit(0);
		}
	}
	check(pipe(delegated_pipe) == 0, "create transactional delegation pipe");
	check(agent_scope_delegate_fd(delegated_pipe[0]) == AGENT_STATUS_OK,
	      "authorize one boundary delegation attempt");
	check(agent_workflow_create(AGENT_ROLE_ORCHESTRATOR) < 0,
	      "reject workflow without a full object-table partition");
	check(waitpid(children[0], &status) == children[0],
	      "wait first capacity workflow");
	check(status == 0, "first capacity workflow status");
	replacement = create_workflow_after_reap();
	check(replacement >= 0, "reuse released workflow partition");
	if (replacement == 0) {
		check(close(delegated_pipe[0]) < 0,
		      "failed admission consumes delegation ticket");
		exit(0);
	}
	status = -1;
	check(waitpid(replacement, &status) == replacement,
	      "wait replacement workflow");
	check(status == 0, "replacement workflow status");
	for (int i = 1; i < 3; i++) {
		status = -1;
		check(waitpid(children[i], &status) == children[i],
		      "wait capacity workflow");
		check(status == 0, "capacity workflow status");
	}
	check(close(delegated_pipe[0]) == 0 &&
	      close(delegated_pipe[1]) == 0,
	      "close transactional delegation pipe");
}

int main(void)
{
	struct scope_reply ready_a;
	struct scope_reply ready_b;
	struct scope_reply lease_a;
	struct scope_reply writeback_a;
	struct scope_reply progress_b;
	int a_command[2];
	int a_reply[2];
	int b_command[2];
	int b_reply[2];
	int pid_a;
	int pid_b;
	int status = 0;
	int64 observe_progress_started;
	int64 observe_progress_elapsed;

	printf("agentscope_ucore: workflow scope isolation test\n");
	check(pipe(a_command) == 0 && pipe(a_reply) == 0,
	      "create scope A pipes");
	check(pipe(b_command) == 0 && pipe(b_reply) == 0,
	      "create scope B pipes");

	check(agent_scope_delegate_fd(a_command[0]) == AGENT_STATUS_OK &&
		      agent_scope_delegate_fd(a_reply[1]) == AGENT_STATUS_OK,
	      "delegate scope A pipe endpoints");
	pid_a = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	check(pid_a >= 0, "create fresh scope A");
	if (pid_a == 0) {
		check(close(b_command[0]) < 0,
		      "undelegated scope B pipe is not inherited");
		run_scope_root('A', a_command[0], a_reply[1], -1);
	}
	check(agent_scope_delegate_fd(b_command[0]) == AGENT_STATUS_OK &&
		      agent_scope_delegate_fd(b_reply[1]) == AGENT_STATUS_OK,
	      "delegate scope B pipe endpoints");
	pid_b = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	check(pid_b >= 0, "create fresh scope B");
	if (pid_b == 0) {
		check(close(a_command[0]) < 0,
		      "undelegated scope A pipe is not inherited");
		run_scope_root('B', b_command[0], b_reply[1], pid_a);
	}

	ready_a = receive_reply(a_reply[0], "scope A ready");
	ready_b = receive_reply(b_reply[0], "scope B ready");
	check(ready_a.scope_id != ready_b.scope_id,
	      "fresh workflows have distinct scopes");

	run_command(a_command[1], a_reply[0], 'C', 0, 0, ready_a.scope_id,
		    "scope A creates object");
	run_command(b_command[1], b_reply[0], 'N', 0, 0, ready_b.scope_id,
		    "scope B cannot see scope A");
	run_command(b_command[1], b_reply[0], 'C', 0, 0, ready_b.scope_id,
		    "scope B creates same-name object");
	run_command(a_command[1], a_reply[0], 'V', 0, 0, ready_a.scope_id,
		    "scope A retains own object");
	run_command(b_command[1], b_reply[0], 'V', 0, 0, ready_b.scope_id,
		    "scope B retains own object");
	run_command(b_command[1], b_reply[0], 'D', 0, 0, ready_b.scope_id,
		    "scope B creates volatile metadata");
	run_command(a_command[1], a_reply[0], 'M', 0, 0, ready_a.scope_id,
		    "scope A reloads own metadata");
	run_command(b_command[1], b_reply[0], 'E', 0, 0, ready_b.scope_id,
		    "scope A reload preserves scope B volatile metadata");
	run_command(b_command[1], b_reply[0], 'Z', 0, 0, ready_b.scope_id,
		    "volatile writes bypass persistent writeback");
	run_command(b_command[1], b_reply[0], 'I', 0, 0, ready_b.scope_id,
		    "cross-scope IPC isolation");
	run_command(a_command[1], a_reply[0], 'S', 0, 0, ready_a.scope_id,
		    "same-scope child collaboration");
	run_command(a_command[1], a_reply[0], 'H', 0, 0, ready_a.scope_id,
		    "pipe delegation remains single hop");
	run_command(a_command[1], a_reply[0], 'Y', 0, 0, ready_a.scope_id,
		    "serialize concurrent metadata transactions");
	run_command(a_command[1], a_reply[0], 'T', 0, 0, ready_a.scope_id,
		    "same-scope aggregate storage quota");
	run_command(a_command[1], a_reply[0], 'F', 0, 0, ready_a.scope_id,
		    "start low-privilege metadata write flood");
	progress_b = run_command(b_command[1], b_reply[0], 'P', 0, 0,
				 ready_b.scope_id,
				 "scope B progresses during metadata flood");
	check(progress_b.value0 == META_CROSS_QUERY_ROUNDS &&
	      progress_b.value1 <= META_CROSS_QUERY_MAX_MS,
	      "scope B metadata queries progress during scope A writes");
	writeback_a = run_command(a_command[1], a_reply[0], 'J', 0, 0,
				  ready_a.scope_id,
				  "join coalesced metadata writeback");
	printf("agentscope_ucore: cross_scope_isolation=1\n");
	printf("agentscope_ucore: ipc_scope_isolation=1\n");
	printf("agentscope_ucore: same_scope_collaboration=1\n");
	printf("agentscope_ucore: pipe_redelegation_isolation=1\n");
	printf("agentscope_ucore: metadata_transactions=1\n");
	printf("agentscope_ucore: scope_storage_quota=1\n");
	printf("agentscope_ucore: scope_reload_isolation=1\n");
	printf("agentscope_ucore: metadata_write_coalescing=1 writes=%d commits=%d\n",
	       (int)writeback_a.value0, (int)writeback_a.value1);
	printf("agentscope_ucore: metadata_cross_scope_progress=1 queries=%d latency_ms=%d\n",
	       (int)progress_b.value0, (int)progress_b.value1);
	printf("agentscope_ucore: metadata_final_consistency=1\n");
	printf("agentscope_ucore: metadata_volatile_no_writeback=1 writes=%d\n",
	       META_VOLATILE_WRITE_FLOOD);
	printf("agentscope_ucore: metadata_scan_pressure_bounded=1\n");

	observe_start_reply = run_command(a_command[1], a_reply[0], 'O', 0, 0,
					  ready_a.scope_id,
					  "start bounded observation query pressure");
	check(observe_start_reply.value0 > 0 && observe_start_reply.value1 > 0,
	      "scope A observation query pressure starts");
	observe_progress_started = get_mtime();
	observe_progress_reply = run_command(
		b_command[1], b_reply[0], 'G', 0, 0, ready_b.scope_id,
		"scope B progresses during observation queries");
	observe_progress_elapsed = get_mtime() - observe_progress_started;
	check(observe_progress_reply.value0 == OBSERVE_CROSS_QUERY_ROUNDS &&
		      observe_progress_elapsed >= 0 &&
		      observe_progress_elapsed <=
			      OBSERVE_CROSS_QUERY_MAX_MS,
	      "scope B observation queries progress during scope A pressure");
	observe_join_reply = run_command(a_command[1], a_reply[0], 'K', 0, 0,
					 ready_a.scope_id,
					 "join bounded observation query pressure");
	check(observe_join_reply.value0 > 0 && observe_join_reply.value1 > 0,
	      "bounded observation query pressure completes");
	printf("agentscope_ucore: observe_query_bounded=1 context=%d loops=%d preemptions=%d\n",
	       AGENT_CONTEXT_MAX_RECORDS, (int)observe_join_reply.value0,
	       (int)observe_join_reply.value1);
	printf("agentscope_ucore: observe_index_ordered=1\n");
	printf("agentscope_ucore: observe_cross_scope_progress=1 queries=%d latency_ms=%d\n",
	       (int)observe_progress_reply.value0,
	       (int)observe_progress_elapsed);

	run_command(b_command[1], b_reply[0], 'W', 0, 0, ready_b.scope_id,
		    "scope B installs watcher");
	run_command(a_command[1], a_reply[0], 'A', 0, 0, ready_a.scope_id,
		    "scope A action history");
	run_command(b_command[1], b_reply[0], 'U', 0, 0, ready_b.scope_id,
		    "scope B audit and event isolation");
	run_command(b_command[1], b_reply[0], 'A', 0, 0, ready_b.scope_id,
		    "scope B independent action history");
	printf("agentscope_ucore: action_scope_isolation=1\n");
	printf("agentscope_ucore: audit_event_scope_isolation=1\n");

	lease_a = run_command(a_command[1], a_reply[0], 'L', 0, 0,
			      ready_a.scope_id, "scope A begins lease");
	check(lease_a.value0 != 0, "scope A lease reply");
	run_command(b_command[1], b_reply[0], 'X', lease_a.value0,
		    lease_a.value1, ready_b.scope_id,
		    "scope B cannot use scope A lease");
	run_command(a_command[1], a_reply[0], 'R', 0, 0, ready_a.scope_id,
		    "scope A releases lease");
	printf("agentscope_ucore: lease_scope_isolation=1\n");

	run_command(a_command[1], a_reply[0], 'Q', 0, 0, ready_a.scope_id,
		    "stop scope A");
	run_command(b_command[1], b_reply[0], 'Q', 0, 0, ready_b.scope_id,
		    "stop scope B");
	check(waitpid(pid_a, &status) == pid_a, "wait scope A");
	check(status == 0, "scope A status");
	check(waitpid(pid_b, &status) == pid_b, "wait scope B");
	check(status == 0, "scope B status");
	check_scope_capacity_reservation();
	printf("agentscope_ucore: scope_capacity_reservation=1\n");
	printf("agentscope_ucore: transactional_fd_delegation=1\n");
	check_scope_lifecycle();
	printf("agentscope_ucore: lifecycle_reclamation=1\n");
	printf("agentscope_ucore: parent passed\n");
	return 0;
}
