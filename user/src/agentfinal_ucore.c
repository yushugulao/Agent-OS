#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#ifdef WAIT_ATOMIC_TEST_PROFILE
#include <wait_atomic_test_abi.h>
#endif

#define FINAL_PROVENANCE_MAX 256
#define FINAL_QUERY_CACHE_MAGIC 0x51434143U

struct final_query_cache_entry {
	uint magic;
	uint version;
	uint bytes;
	uint reserved;
	uint64 fs_generation;
	uint64 query_ticks;
	uint64 plan_reason;
	int total_hits;
	int returned;
	int plan;
	int used_index;
	struct agent_file_hit first_hit;
};

static struct agent_op ops[AGENT_BATCH_MAX];
static struct agent_result results[AGENT_BATCH_MAX];
static struct agent_context_record records[AGENT_CONTEXT_MAX_RECORDS];
/* 各类快照不会并发使用，共享缓冲区可避免验收程序挤占普通文件容量。 */
static union {
	struct agent_trace_record trace[AGENT_TRACE_MAX_RECORDS];
	struct agent_audit_record audit[AGENT_AUDIT_MAX_RECORDS];
	struct agent_timeline_record timeline[AGENT_TIMELINE_MAX_RECORDS];
	struct agent_provenance_edge provenance[FINAL_PROVENANCE_MAX];
} final_observe_scratch;
_Static_assert(sizeof(final_observe_scratch) >= sizeof(records),
	       "observation scratch must hold a Context archive copy");
#define TRACE_RECORDS final_observe_scratch.trace
#define SPAN_RECORDS final_observe_scratch.audit
#define TIMELINE_RECORDS final_observe_scratch.timeline
#define PROVENANCE_EDGES final_observe_scratch.provenance
static struct agent_timeline_filter timeline_filter;
static struct agent_ledger_summary final_ledger;
static struct agent_info final_info;
static struct agent_context_header final_header;
static struct agent_context_detail final_detail;
static struct agent_context_record final_manual;
static struct agent_file_query final_query;
static struct agent_file_query_result final_query_result;
static struct agent_event final_event;
static struct agent_request final_req;
static struct agent_response final_resp;
static volatile int context_lane_slow_ready;
static volatile int context_lane_slow_done;
static struct agent_result context_lane_slow_result;

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentfinal_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static int text_contains(const char *text, const char *needle)
{
	int n = strlen(needle);

	if (n == 0)
		return 1;
	for (int i = 0; text[i]; i++)
		if (strncmp(text + i, needle, n) == 0)
			return 1;
	return 0;
}

#ifdef AGENT_CONTEXT_SYNC_TEST_PROFILE
static unsigned char sync_mirror_before[AGENT_CONTEXT_SIZE];
static struct agent_context_record
	sync_records_before[AGENT_CONTEXT_MAX_RECORDS];

static int bytes_equal(const void *left, const void *right, int n)
{
	const unsigned char *a = left;
	const unsigned char *b = right;

	for (int i = 0; i < n; i++)
		if (a[i] != b[i])
			return 0;
	return 1;
}
#endif

#ifdef WAIT_ATOMIC_TEST_PROFILE
static volatile int wait_atomic_sibling_ready;
static volatile int wait_deadline_status[2];
static int wait_deadline_timeout[2];
#define WAIT_HANDOFF_MAX 15
static volatile int wait_handoff_done[WAIT_HANDOFF_MAX];
static volatile int wait_handoff_status[WAIT_HANDOFF_MAX];
static volatile uint64 wait_handoff_corr[WAIT_HANDOFF_MAX];
static struct agent_info wait_handoff_before;
static struct agent_info wait_handoff_after_first;
static struct agent_info wait_handoff_after;
static struct agent_info wait_handoff_poll;
static struct agent_event wait_handoff_event;
static int wait_handoff_tids[WAIT_HANDOFF_MAX];
static volatile int timeline_handoff_done;
static volatile int timeline_handoff_status;
static struct agent_timeline_filter timeline_handoff_filter;

static void wait_deadline_worker(void *arg)
{
	struct agent_event event;
	int slot = (int)(long)arg;

	memset(&event, 0, sizeof(event));
	wait_deadline_status[slot] =
		agent_wait(&event, wait_deadline_timeout[slot]);
	exit(0);
}

static void wait_handoff_worker(void *arg)
{
	struct agent_event event;
	int slot = (int)(long)arg;

	memset(&event, 0, sizeof(event));
	wait_handoff_status[slot] = agent_wait(&event, -1);
	wait_handoff_corr[slot] = event.corr_id;
	wait_handoff_done[slot] = 1;
	exit(0);
}

static void timeline_handoff_worker(void *arg)
{
	(void)arg;
	timeline_handoff_status =
		agent_timeline_wait(&timeline_handoff_filter, -1);
	timeline_handoff_done = 1;
	exit(0);
}

static int wait_handoff_done_count(int count)
{
	int done = 0;

	for (int i = 0; i < count; i++)
		done += wait_handoff_done[i] != 0;
	return done;
}

static void wait_handoff_until(int count, int expected)
{
	for (int retry = 0; retry < 200000; retry++) {
		if (wait_handoff_done_count(count) == expected)
			return;
		sched_yield();
	}
	check(0, "event handoff progress");
}

static void wait_handoff_until_sleeping(uint64 target)
{
	for (int retry = 0; retry < 200000; retry++) {
		check(agent_info(&wait_handoff_poll) == 0,
		      "event handoff sleep info");
		if (wait_handoff_poll.wait_sleep_count >= target)
			return;
		sched_yield();
	}
	check(0, "event handoff waiter publication");
}

static void wait_handoff_until_mixed_sleeping(
	uint64 event_target, uint64 timeline_target)
{
	for (int retry = 0; retry < 200000; retry++) {
		check(agent_info(&wait_handoff_poll) == 0,
		      "mixed handoff sleep info");
		if (wait_handoff_poll.wait_sleep_count >= event_target &&
		    wait_handoff_poll.timeline_wait_sleep_count >= timeline_target)
			return;
		sched_yield();
	}
	check(0, "mixed handoff waiter publication");
}

static void wait_handoff_until_tick(uint64 target)
{
	for (int retry = 0; retry < 200000; retry++) {
		check(agent_info(&wait_handoff_poll) == 0,
		      "mixed handoff tick info");
		if (wait_handoff_poll.current_tick >= target)
			return;
		sched_yield();
	}
	check(0, "mixed handoff future tick");
}

static __attribute__((noinline)) void
check_event_wake_handoff_phase(int count)
{
	check(count > 0 && count <= WAIT_HANDOFF_MAX, "event handoff count");
	check(agent_info(&wait_handoff_before) == 0,
	      "event handoff info before");
	for (int i = 0; i < count; i++) {
		wait_handoff_done[i] = 0;
		wait_handoff_status[i] = -99;
		wait_handoff_corr[i] = 0;
		wait_handoff_tids[i] =
			thread_create(wait_handoff_worker, (void *)(long)i);
		check(wait_handoff_tids[i] > 0, "event handoff waiter create");
	}
	wait_handoff_until_sleeping(wait_handoff_before.wait_sleep_count + count);

	for (int i = 0; i < count; i++) {
		memset(&wait_handoff_event, 0, sizeof(wait_handoff_event));
		wait_handoff_event.type = AGENT_EVENT_MESSAGE;
		wait_handoff_event.corr_id = 8100 + count * 16 + i;
		strcpy(wait_handoff_event.payload, "wait-handoff");
		check(agent_wake(getpid(), &wait_handoff_event) == 0,
		      "event handoff publish");
		wait_handoff_until(count, i + 1);
		if (i == 0) {
			check(agent_info(&wait_handoff_after_first) == 0,
			      "event handoff first info");
			check(wait_handoff_after_first.wait_wakeup_count ==
				      wait_handoff_before.wait_wakeup_count + 1,
			      "event wake-one accounting");
		}
	}
	for (int i = 0; i < count; i++) {
		check(waittid(wait_handoff_tids[i]) == 0,
		      "event handoff waiter join");
		check(wait_handoff_status[i] == AGENT_STATUS_OK,
		      "event handoff waiter status");
	}
	check(agent_info(&wait_handoff_after) == 0, "event handoff info after");
	check(wait_handoff_after.wait_wakeup_count ==
		      wait_handoff_before.wait_wakeup_count + count,
	      "event handoff exact wake accounting");
}

static __attribute__((noinline)) void
check_event_timeline_waiter_isolation(void)
{
	struct agent_info before;
	struct agent_info after;
	int event_tid;
	int timeline_tid;

	check(agent_info(&before) == 0, "mixed handoff info before");
	check(before.event_queue_count == 0, "mixed handoff queue before");
	memset(&timeline_handoff_filter, 0, sizeof(timeline_handoff_filter));
	timeline_handoff_filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK |
		AGENT_TIMELINE_FILTER_START_TICK |
		AGENT_TIMELINE_FILTER_KIND |
		AGENT_TIMELINE_FILTER_TARGET_PID |
		AGENT_TIMELINE_FILTER_EVENT_TYPE;
	timeline_handoff_filter.source_mask = AGENT_TIMELINE_SOURCE_MASK_AUDIT;
	timeline_handoff_filter.start_tick = before.current_tick + 1;
	timeline_handoff_filter.kind = AGENT_AUDIT_KIND_EVENT_ENQUEUE;
	timeline_handoff_filter.target_pid = getpid();
	timeline_handoff_filter.event_type = AGENT_EVENT_MESSAGE;
	timeline_handoff_done = 0;
	timeline_handoff_status = -99;
	wait_handoff_done[0] = 0;
	wait_handoff_status[0] = -99;
	wait_handoff_corr[0] = 0;
	timeline_tid = thread_create(timeline_handoff_worker, 0);
	check(timeline_tid > 0, "timeline handoff waiter create");
	event_tid = thread_create(wait_handoff_worker, 0);
	check(event_tid > 0, "mixed event waiter create");
	wait_handoff_until_mixed_sleeping(before.wait_sleep_count + 1,
					 before.timeline_wait_sleep_count + 1);
	wait_handoff_until_tick(timeline_handoff_filter.start_tick);

	memset(&wait_handoff_event, 0, sizeof(wait_handoff_event));
	wait_handoff_event.type = AGENT_EVENT_MESSAGE;
	wait_handoff_event.corr_id = 8991;
	strcpy(wait_handoff_event.payload, "wait-handoff-timeline");
	check(agent_wake(getpid(), &wait_handoff_event) == 0,
	      "mixed handoff publish");
	wait_handoff_until(1, 1);
	for (int retry = 0; retry < 200000 && !timeline_handoff_done; retry++)
		sched_yield();
	check(timeline_handoff_done, "timeline handoff completion");
	check(waittid(event_tid) == 0, "mixed event waiter join");
	check(waittid(timeline_tid) == 0, "timeline handoff waiter join");
	check(wait_handoff_status[0] == AGENT_STATUS_OK,
	      "mixed event waiter status");
	check(wait_handoff_corr[0] == 8991, "mixed event waiter identity");
	check(timeline_handoff_status > 0, "timeline handoff waiter status");
	check(agent_info(&after) == 0, "mixed handoff info after");
	check(after.wait_wakeup_count == before.wait_wakeup_count + 1,
	      "mixed event wake accounting");
	check(after.timeline_wait_wakeup_count ==
		      before.timeline_wait_wakeup_count + 1,
	      "mixed timeline wake accounting");
	check(after.event_queue_count == 0, "mixed handoff queue after");
	printf("agentfinal_ucore: event_baton_identity timeline_waiter=1 event_waiter=1 event_wakeups=1\n");
}

static __attribute__((noinline)) void check_event_wake_handoff(void)
{
	static const int counts[] = {1, 4, 8, WAIT_HANDOFF_MAX};

	check(agent_watch(AGENT_EVENT_MESSAGE, "wait-handoff") == 0,
	      "watch event handoff");
	for (uint i = 0; i < sizeof(counts) / sizeof(counts[0]); i++)
		check_event_wake_handoff_phase(counts[i]);
	check_event_timeline_waiter_isolation();
	check(agent_unwatch(AGENT_EVENT_MESSAGE, "wait-handoff") == 1,
	      "unwatch event handoff");
	printf("agentfinal_ucore: event_wake_handoff waiters=1,4,8,15 wakeups=28 herd=0\n");
}

static void wait_deadline_observe(uint phase,
				  struct wait_atomic_deadline_snapshot *snapshot)
{
	int status;

	for (;;) {
		memset(snapshot, 0, sizeof(*snapshot));
		status = wait_atomic_test_deadline_observe(phase, snapshot);
		if (status == 0)
			return;
		check(status == WAIT_ATOMIC_TEST_RETRY,
		      "deadline profile observation");
		sched_yield();
	}
}

static void check_thread_wait_deadlines(void)
{
	struct wait_atomic_deadline_snapshot snapshot;
	struct wait_atomic_test_receipt receipt;
	struct agent_event event;
	int tids[2];

	wait_deadline_timeout[0] = 30;
	wait_deadline_timeout[1] = -1;
	wait_deadline_status[0] = wait_deadline_status[1] = -99;
	tids[0] = thread_create(wait_deadline_worker, (void *)0);
	tids[1] = thread_create(wait_deadline_worker, (void *)1);
	check(tids[0] > 0 && tids[1] > 0 && tids[0] != tids[1],
	      "finite and infinite wait threads");
	wait_deadline_observe(WAIT_ATOMIC_TEST_DEADLINE_FINITE_INFINITE,
			      &snapshot);
	check(snapshot.finite_threads == 1 &&
	      snapshot.infinite_threads == 1 && snapshot.keyed_threads == 2,
	      "finite and infinite wait publication");
	check(waittid(tids[0]) == 0 &&
	      wait_deadline_status[0] == AGENT_STATUS_TIMEOUT,
	      "finite waiter timeout");
	wait_deadline_observe(WAIT_ATOMIC_TEST_DEADLINE_INFINITE_ONLY,
			      &snapshot);
	check(snapshot.waiting_threads == 1 &&
	      snapshot.infinite_threads == 1 &&
	      snapshot.loop_state == AGENT_LOOP_WAITING,
	      "infinite waiter preserved");
	check(agent_watch(AGENT_EVENT_MESSAGE, "thread-deadline-release") == 0,
	      "watch infinite waiter release");
	memset(&event, 0, sizeof(event));
	event.type = AGENT_EVENT_MESSAGE;
	event.corr_id = 8011;
	strcpy(event.payload, "thread-deadline-release");
	check(agent_wake(getpid(), &event) == 0, "release infinite waiter");
	check(waittid(tids[1]) == 0 &&
	      wait_deadline_status[1] == AGENT_STATUS_OK,
	      "infinite waiter event");
	check(agent_unwatch(AGENT_EVENT_MESSAGE,
			    "thread-deadline-release") == 1,
	      "unwatch infinite waiter release");
	wait_deadline_observe(WAIT_ATOMIC_TEST_DEADLINE_IDLE_FIRST, &snapshot);

	wait_deadline_timeout[0] = 30;
	wait_deadline_timeout[1] = 120;
	wait_deadline_status[0] = wait_deadline_status[1] = -99;
	tids[0] = thread_create(wait_deadline_worker, (void *)0);
	tids[1] = thread_create(wait_deadline_worker, (void *)1);
	check(tids[0] > 0 && tids[1] > 0 && tids[0] != tids[1],
	      "distinct deadline wait threads");
	wait_deadline_observe(WAIT_ATOMIC_TEST_DEADLINE_DISTINCT, &snapshot);
	check(snapshot.finite_threads == 2 &&
	      snapshot.reused_threads == 2 &&
	      snapshot.earliest_deadline < snapshot.latest_deadline &&
	      snapshot.earliest_tid != snapshot.latest_tid,
	      "distinct deadline publication");
	check(waittid(tids[0]) == 0 &&
	      wait_deadline_status[0] == AGENT_STATUS_TIMEOUT,
	      "short waiter timeout");
	wait_deadline_observe(WAIT_ATOMIC_TEST_DEADLINE_LONG_ONLY, &snapshot);
	check(snapshot.waiting_threads == 1 &&
	      snapshot.earliest_deadline == snapshot.latest_deadline &&
	      snapshot.loop_state == AGENT_LOOP_WAITING,
	      "long waiter preserved");
	check(waittid(tids[1]) == 0 &&
	      wait_deadline_status[1] == AGENT_STATUS_TIMEOUT,
	      "long waiter timeout");
	wait_deadline_observe(WAIT_ATOMIC_TEST_DEADLINE_COMPLETE, &snapshot);
	memset(&receipt, 0, sizeof(receipt));
	check(wait_atomic_test_query(WAIT_ATOMIC_TEST_THREAD_DEADLINE,
				     getpid(), &receipt) == 0,
	      "query thread deadline receipt");
	check(receipt.operation == WAIT_ATOMIC_TEST_THREAD_DEADLINE &&
	      receipt.target_pid == getpid() && receipt.sequence != 0 &&
	      receipt.flags == WAIT_ATOMIC_TEST_DEADLINE_FLAGS,
	      "thread deadline receipt");
	printf("agentfinal_ucore: thread_wait_deadlines finite_infinite=1 distinct_deadlines=1 keyed_timer=1 loop_aggregate=1 slot_reuse=1\n");
}

static void wait_atomic_teardown_sibling(void *unused)
{
	(void)unused;
	wait_atomic_sibling_ready = 1;
	for (;;)
		sched_yield();
}

static void check_wait_publication_atomicity(void)
{
	struct agent_info before;
	struct agent_info after;
	struct agent_event event;
	struct wait_atomic_test_receipt receipt;
	int pid;
	int status = 0;
	int tid;

	check(agent_info(&before) == 0, "wait injection info before");
	check(before.event_queue_count == 0, "wait injection empty event queue");
	check(wait_atomic_test_arm(WAIT_ATOMIC_TEST_AGENT_WAIT) == 0,
	      "arm agent wait injection");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, -1) == AGENT_STATUS_OK,
	      "consume injected wait event");
	check(event.type == AGENT_EVENT_TIMER &&
	      strcmp(event.payload, "wait=atomic-injected") == 0,
	      "injected wait event identity");
	check(agent_info(&after) == 0, "wait injection info after");
	check(after.event_queue_count == 0 &&
	      after.wait_sleep_count == before.wait_sleep_count &&
	      after.wait_wakeup_count == before.wait_wakeup_count,
	      "injected event consumed before sleep");
	memset(&receipt, 0, sizeof(receipt));
	check(wait_atomic_test_query(WAIT_ATOMIC_TEST_AGENT_WAIT, getpid(),
				     &receipt) == 0,
	      "query agent wait injection");
	check(receipt.version == WAIT_ATOMIC_TEST_ABI_VERSION &&
	      receipt.size == sizeof(receipt) &&
	      receipt.operation == WAIT_ATOMIC_TEST_AGENT_WAIT &&
	      receipt.target_pid == getpid() && receipt.sequence != 0 &&
	      receipt.flags == WAIT_ATOMIC_TEST_AGENT_FLAGS,
	      "agent wait injection receipt");
	pid = fork();
	check(pid >= 0, "fork teardown injection child");
	if (pid == 0) {
		wait_atomic_sibling_ready = 0;
		tid = thread_create(wait_atomic_teardown_sibling, 0);
		if (tid <= 0)
			exit(91);
		while (!wait_atomic_sibling_ready)
			sched_yield();
		if (wait_atomic_test_arm(WAIT_ATOMIC_TEST_TEARDOWN) < 0)
			exit(92);
		exit(73);
	}
	check(waitpid(pid, &status) == pid, "wait teardown injection child");
	check(status == 73, "teardown injection child status");
	memset(&receipt, 0, sizeof(receipt));
	check(wait_atomic_test_query(WAIT_ATOMIC_TEST_TEARDOWN, pid,
				     &receipt) == 0,
	      "query teardown injection");
	check(receipt.version == WAIT_ATOMIC_TEST_ABI_VERSION &&
	      receipt.size == sizeof(receipt) &&
	      receipt.operation == WAIT_ATOMIC_TEST_TEARDOWN &&
	      receipt.target_pid == pid && receipt.sequence != 0 &&
	      receipt.flags == WAIT_ATOMIC_TEST_TEARDOWN_FLAGS,
	      "teardown injection receipt");
	printf("agentfinal_ucore: wait_publication_atomic=1 event_wake_none=1 event_no_sleep=1 sibling_wake_none=1 teardown_completed=1\n");
}
#endif

static int file_hit_equal(const struct agent_file_hit *left,
			  const struct agent_file_hit *right)
{
	return left->fid == right->fid &&
	       strcmp(left->physical_name, right->physical_name) == 0 &&
	       strcmp(left->logical_path, right->logical_path) == 0 &&
	       strcmp(left->stage, right->stage) == 0 &&
	       strcmp(left->kind, right->kind) == 0 &&
	       strcmp(left->status, right->status) == 0 &&
	       strcmp(left->summary, right->summary) == 0 &&
	       left->dependency_mask == right->dependency_mask &&
	       left->dev == right->dev && left->inum == right->inum &&
	       left->incarnation == right->incarnation &&
	       left->size == right->size &&
	       left->fs_generation == right->fs_generation;
}

static void check_user_query_cache(
	struct agent_info *info, struct agent_context_header *header,
	const struct agent_file_query_result *result)
{
	struct final_query_cache_entry *cache;
	int n;

	check(header->version == AGENT_CONTEXT_VERSION, "query cache version");
	check(header->eviction_policy == AGENT_CONTEXT_EVICT_FIFO,
	      "query cache FIFO policy");
	check(header->user_cache_size >= sizeof(*cache),
	      "structured query cache size");
	check((result->plan_reason & AGENT_FILE_QUERY_REASON_CACHE_HIT) == 0,
	      "kernel query result cache disabled");
	cache = (struct final_query_cache_entry *)(info->context_base +
					      header->user_cache_offset);
	memset(cache, 0, sizeof(*cache));
	cache->magic = FINAL_QUERY_CACHE_MAGIC;
	cache->version = AGENT_CONTEXT_VERSION;
	cache->bytes = sizeof(*cache);
	cache->fs_generation = result->fs_generation;
	cache->query_ticks = result->query_ticks;
	cache->plan_reason = result->plan_reason;
	cache->total_hits = result->total_hits;
	cache->returned = result->returned;
	cache->plan = result->plan;
	cache->used_index = result->used_index;
	if (result->returned > 0)
		cache->first_hit = result->hits[0];
	n = context_snapshot(header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n > 0, "snapshot after structured query cache");
	check(cache->magic == FINAL_QUERY_CACHE_MAGIC &&
	      cache->version == AGENT_CONTEXT_VERSION &&
	      cache->bytes == sizeof(*cache),
	      "user query cache header preserved");
	check(cache->fs_generation == result->fs_generation &&
		      cache->query_ticks == result->query_ticks &&
		      cache->plan_reason == result->plan_reason &&
		      cache->total_hits == result->total_hits &&
		      cache->returned == result->returned &&
		      cache->plan == result->plan &&
		      cache->used_index == result->used_index &&
		      (result->returned == 0 ||
		       file_hit_equal(&cache->first_hit, &result->hits[0])),
	      "user query cache fields preserved");
	printf("agentfinal_ucore: user_cache_preserved=1 offset=%d size=%d\n",
	       (int)header->user_cache_offset, (int)header->user_cache_size);
	printf("agentfinal_ucore: context_query_cache=1 user_managed=1 kernel_cache_hit=0\n");
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
	meta.flags = 0;
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

static void make_echo(struct agent_op *op, uint64 id, const char *text)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = AGENT_TOOL_ECHO;
	op->request_id = id;
	op->arg0 = id;
	op->arg1 = id + 1;
	strcpy(op->payload, text);
}

static void check_context_mapping_isolation(void)
{
	volatile uint64 *trusted_page;
	int pid;
	int status = 0;

	pid = agent_create_role(AGENT_ROLE_SENTINEL);
	check(pid >= 0, "create low privilege Context writer");
	if (pid == 0) {
		if (agent_info(&final_info) != 0 || !final_info.is_agent ||
		    final_info.agent_role != AGENT_ROLE_SENTINEL ||
		    final_info.context_base != AGENT_CONTEXT_BASE)
			exit(81);
		/* 直接写可信页；正确的只读 PTE 必须把子进程以 -2 终止。 */
		trusted_page = (volatile uint64 *)final_info.context_base;
		printf("agentfinal_ucore: context_ro_store_fault_armed=1\n");
		*trusted_page = 0;
		exit(82);
	}
	check(waitpid(pid, &status) == pid, "wait Context writer fault");
	check(status == -2, "trusted Context page rejects user store");

	pid = fork();
	check(pid >= 0, "fork PUBLIC Context probe");
	if (pid == 0) {
		if (agent_info(&final_info) != 0 || final_info.is_agent ||
		    final_info.context_base != 0 || final_info.context_size != 0)
			exit(83);
		/* PUBLIC fork 即使知道固定地址，也不能读取父 Agent 的映射。 */
		trusted_page = (volatile uint64 *)AGENT_CONTEXT_BASE;
		printf("agentfinal_ucore: context_public_unmapped_fault_armed=1\n");
		(void)*trusted_page;
		exit(84);
	}
	check(waitpid(pid, &status) == pid, "wait PUBLIC Context probe");
	check(status == -2, "PUBLIC fork has no Context mapping");
	printf("agentfinal_ucore: context_ro_mapping=1 low_agent_fault=-2 public_unmapped_fault=-2\n");
}

static void context_lane_slow_worker(void *unused)
{
	struct agent_op op;
	struct agent_result result;

	(void)unused;
	make_echo(&op, 7201, "context-lane-ready");
	if (agent_run(&op, &result, 1, 0) != 1 ||
	    result.status != AGENT_STATUS_OK)
		exit(71);
	__sync_synchronize();
	context_lane_slow_ready = 1;
	__sync_synchronize();
	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = AGENT_TOOL_RERUN_STAGE;
	op.request_id = 7202;
	strcpy(op.payload, "align");
	if (agent_run(&op, &result, 1, 0) != 1 ||
	    result.status != AGENT_STATUS_OK)
		exit(72);
	context_lane_slow_result = result;
	__sync_synchronize();
	context_lane_slow_done = 1;
	__sync_synchronize();
	exit(0);
}

static void check_context_commit_lane(void)
{
	int tid;
	int status;
	int n;
	int main_tid = gettid();
	int slow_tid = 0;
	int fast_tid = 0;
	int slow_found = 0;
	int fast_found = 0;

	status = agent_file_meta_init();
	printf("agentfinal_ucore: context_lane_meta_init=%d\n", status);
	check(status == 0, "context lane meta init");
	seed_demo_metadata();
	check(context_clear() == 0, "context lane clear");
	context_lane_slow_ready = 0;
	context_lane_slow_done = 0;
	memset(&context_lane_slow_result, 0,
	       sizeof(context_lane_slow_result));
	tid = thread_create(context_lane_slow_worker, 0);
	check(tid >= 0, "context lane slow thread");
	while (!context_lane_slow_ready)
		sched_yield();
	for (;;) {
		check(agent_info(&final_info) == 0, "context lane progress");
		if (final_info.agent_call_count >= 2)
			break;
		check(!context_lane_slow_done, "context lane overlap");
		sched_yield();
	}
	/* 用提交序列验证串行化，不依赖两个线程恰好形成某个调度窗口。 */
	make_echo(&ops[0], 7203, "context-lane-fast");
	check(agent_run(&ops[0], &results[0], 1, 0) == 1,
	      "context lane fast run");
	status = waittid(tid);
	check(status == 0, "context lane slow join");
	check(context_lane_slow_done, "context lane slow completed");
	check(context_lane_slow_result.sequence == 2,
	      "context lane slow sequence");
	check(results[0].sequence == 3, "context lane fast sequence");
	n = context_snapshot(&final_header, records,
			     AGENT_CONTEXT_MAX_RECORDS);
	check(n == 3, "context lane snapshot count");
	check(final_header.oldest_sequence == 1,
	      "context lane oldest");
	check(final_header.latest_sequence == 3,
	      "context lane latest");
	for (int i = 0; i < n; i++) {
		check(records[i].sequence == (uint64)i + 1,
		      "context lane monotonic sequence");
		check(records[i].record_hash != 0,
		      "context lane record hash");
		if (i > 0)
			check(records[i].prev_hash ==
				      records[i - 1].record_hash,
			      "context lane hash chain");
	}
	check(final_header.latest_record_hash ==
		      records[n - 1].record_hash,
	      "context lane header hash");
	memset(&timeline_filter, 0, sizeof(timeline_filter));
	timeline_filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK;
	timeline_filter.source_mask = AGENT_TIMELINE_SOURCE_MASK_CONTEXT;
	n = agent_timeline_query(&timeline_filter, TIMELINE_RECORDS,
				 AGENT_TIMELINE_MAX_RECORDS);
	for (int i = 0; i < n; i++) {
		if (TIMELINE_RECORDS[i].sequence == 2) {
			slow_tid = TIMELINE_RECORDS[i].tid;
			slow_found = 1;
		}
		if (TIMELINE_RECORDS[i].sequence == 3) {
			fast_tid = TIMELINE_RECORDS[i].tid;
			fast_found = 1;
		}
	}
	check(slow_found && fast_found && slow_tid == tid &&
	      fast_tid == main_tid &&
	      slow_tid != fast_tid,
	      "context historical thread identity");
	printf("agentfinal_ucore: context_commit_lane=1 sequence=1..3 hash=1\n");
}

static void check_context_rollback_identity(void)
{
	struct agent_context_header tampered_header;
	struct agent_context_record *direct_records;
	struct agent_context_record *tampered_records;
	uint64 old_branch, new_branch, source_hash;
	uint64 abandoned_sequence, abandoned_hash, abandoned_branch;
	int n, edges, found = 0;

	check(context_clear() == 0, "rollback identity clear");
	check(context_rollback(999999) == AGENT_STATUS_NOT_FOUND,
	      "rollback nonexistent sequence rejected");
	for (int i = 0; i < 3; i++)
		make_echo(&ops[i], 7300 + i, "branch-old");
	check(agent_run(ops, results, 3, 0) == 3, "rollback identity seed");
	n = context_snapshot(&final_header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == 3, "rollback identity seed snapshot");
	old_branch = records[0].branch_generation;
	source_hash = records[0].record_hash;
	abandoned_sequence = records[2].sequence;
	abandoned_hash = records[2].record_hash;
	abandoned_branch = records[2].branch_generation;
	check(context_rollback(records[0].sequence) == 0,
	      "rollback identity rollback");
	check(context_snapshot(&final_header, records,
			       AGENT_CONTEXT_MAX_RECORDS) == 1,
	      "rollback publishes active path only");
	new_branch = final_header.branch_generation;
	check(new_branch != old_branch && final_header.visible_head_sequence == 1 &&
		      final_header.latest_sequence == 3 && final_header.count == 3 &&
		      final_header.active_path_count == 1 &&
		      final_header.active_path_oldest_sequence == 1 &&
		      records[0].sequence == 1 &&
		      records[0].path_parent_sequence == 0,
	      "rollback creates branch without truncation");
	check(context_detail(abandoned_sequence, &final_detail) == 0 &&
		      final_detail.sequence == abandoned_sequence &&
		      final_detail.op.request_id == 7302,
	      "rollback archive remains queryable by identity");
	direct_records = (struct agent_context_record *)(
		final_info.context_base + final_header.records_offset);
	check(direct_records[(abandoned_sequence - 1) % final_header.capacity]
			      .record_hash == abandoned_hash &&
		      direct_records[(abandoned_sequence - 1) %
				     final_header.capacity]
			      .branch_generation == abandoned_branch,
	      "rollback direct Context preserves archive");
	check(context_direct_active_query(final_info.context_base, 0, records,
					 AGENT_CONTEXT_MAX_RECORDS) == 1 &&
		      records[0].sequence == 1,
	      "rollback direct Context active path");
	tampered_records = (struct agent_context_record *)&final_observe_scratch;
	memcpy(tampered_records, direct_records, sizeof(records));
	tampered_records[0].path_parent_sequence = tampered_records[0].sequence;
	check(context_mirror_active_query(&final_header, tampered_records, 0,
					 records,
					 AGENT_CONTEXT_MAX_RECORDS) == -1,
	      "direct active path rejects a cycle");
	check(context_snapshot(&final_header, records,
			       AGENT_CONTEXT_MAX_RECORDS) == 1 &&
		      direct_records[0].path_parent_sequence == 0,
	      "read-only Context preserves active path");
	memcpy(tampered_records, direct_records, sizeof(records));
	tampered_records[0].payload[0] ^= 1;
	check(context_mirror_active_query(&final_header, tampered_records, 0,
					 records,
					 AGENT_CONTEXT_MAX_RECORDS) == -1,
	      "direct active path rejects content hash tamper");
	tampered_header = final_header;
	tampered_header.latest_record_hash ^= 1;
	check(context_mirror_active_query(&tampered_header, direct_records, 0,
					 records,
					 AGENT_CONTEXT_MAX_RECORDS) == -1,
	      "direct active path rejects head hash tamper");
	make_echo(&ops[0], 7304, "branch-new");
	check(agent_run(&ops[0], &results[0], 1, 0) == 1 &&
		      results[0].sequence == 4,
	      "rollback does not reuse sequence");
	n = context_snapshot(&final_header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == 2 && final_header.count == 4 &&
		      final_header.active_path_count == 2 &&
		      records[0].sequence == 1 && records[1].sequence == 4 &&
		      records[1].cause_sequence == 1 &&
		      records[1].path_parent_sequence == 1 &&
		      records[1].branch_generation == new_branch &&
		      records[1].prev_hash == source_hash,
	      "rollback branch ancestry");
	edges = agent_provenance_snapshot(PROVENANCE_EDGES,
					 FINAL_PROVENANCE_MAX);
	for (int i = 0; i < edges; i++)
		if (PROVENANCE_EDGES[i].source_sequence == 1 &&
		    PROVENANCE_EDGES[i].target_sequence == 4 &&
		    PROVENANCE_EDGES[i].source_branch_generation == old_branch &&
		    PROVENANCE_EDGES[i].target_branch_generation == new_branch &&
		    PROVENANCE_EDGES[i].source_record_hash == source_hash &&
		    PROVENANCE_EDGES[i].target_record_hash == records[1].record_hash)
			found = 1;
	check(found, "rollback provenance immutable identity");
	check(context_clear() == 0, "incremental summary clear");
	for (int round = 0; round < 2; round++) {
		for (int i = 0; i < AGENT_BATCH_MAX; i++)
			make_echo(&ops[i], 7400 + round * AGENT_BATCH_MAX + i,
				  "summary-fill");
		check(agent_run(ops, results, AGENT_BATCH_MAX, 0) ==
			      AGENT_BATCH_MAX,
		      "incremental summary fill");
	}
	check(context_snapshot(&final_header, records,
			       AGENT_CONTEXT_MAX_RECORDS) ==
		      AGENT_CONTEXT_MAX_RECORDS,
	      "incremental summary full snapshot");
	source_hash = records[1].record_hash;
	old_branch = final_header.branch_generation;
	check(context_rollback(2) == 0,
	      "incremental summary rollback");
	check(context_snapshot(&final_header, records,
			       AGENT_CONTEXT_MAX_RECORDS) == 2 &&
		      final_header.count == AGENT_CONTEXT_MAX_RECORDS &&
		      final_header.latest_sequence == AGENT_CONTEXT_MAX_RECORDS &&
		      final_header.rollback_count == 1 &&
		      final_header.active_path_count == 2 &&
		      final_header.active_path_oldest_sequence == 1 &&
		      final_header.visible_head_sequence == 2 &&
		      final_header.latest_record_hash == source_hash &&
		      final_header.branch_generation > old_branch,
	      "incremental summary rollback receipt");
	new_branch = final_header.branch_generation;
	make_echo(&ops[0], 7529, "summary-evict-a");
	check(agent_run(&ops[0], &results[0], 1, 0) == 1 &&
		      results[0].sequence == 129,
	      "incremental summary first eviction");
	check(context_snapshot(&final_header, records,
			       AGENT_CONTEXT_MAX_RECORDS) == 2 &&
		      final_header.oldest_sequence == 2 &&
		      final_header.active_path_count == 2 &&
		      final_header.active_path_oldest_sequence == 2 &&
		      records[0].sequence == 2 && records[1].sequence == 129 &&
		      records[1].prev_hash == source_hash &&
		      final_header.branch_generation == new_branch,
	      "incremental summary captured successor");
	make_echo(&ops[0], 7530, "summary-evict-b");
	check(agent_run(&ops[0], &results[0], 1, 0) == 1 &&
		      results[0].sequence == 130,
	      "incremental summary second eviction");
	check(context_snapshot(&final_header, records,
			       AGENT_CONTEXT_MAX_RECORDS) == 2 &&
		      final_header.oldest_sequence == 3 &&
		      final_header.rollback_count == 1 &&
		      final_header.active_path_count == 2 &&
		      final_header.active_path_oldest_sequence == 129 &&
		      final_header.visible_head_sequence == 130 &&
		      records[0].sequence == 129 && records[1].sequence == 130 &&
		      records[1].path_parent_sequence == 129 &&
		      records[1].prev_hash == records[0].record_hash &&
		      final_header.latest_record_hash == records[1].record_hash &&
		      final_header.branch_generation == new_branch,
	      "incremental summary second successor");
	printf("agentfinal_ucore: context_summary branch=%d rollback_count=%d active_count=%d active_oldest=%d head=%d\n",
	       (int)final_header.branch_generation,
	       (int)final_header.rollback_count,
	       (int)final_header.active_path_count,
	       (int)final_header.active_path_oldest_sequence,
	       (int)final_header.visible_head_sequence);
	printf("agentfinal_ucore: context_rollback_branch=1 sequence_reuse=0 provenance_bound=1\n");
	printf("agentfinal_ucore: context_active_path=1 archive_retained=1 direct_query=1 fifo_suffix=1\n");
}

#ifdef AGENT_CONTEXT_SYNC_TEST_PROFILE
static void arm_context_sync_failure(struct agent_info *info,
				     struct agent_context_header *header)
{
	volatile uint64 *marker;

	check(header->user_cache_size >= sizeof(*marker),
	      "context sync test cache slot");
	marker = (volatile uint64 *)(info->context_base +
				     header->user_cache_offset);
	*marker = ~AGENT_CONTEXT_MAGIC;
}

static void check_context_sync_unchanged(
	const struct agent_context_header *before_header,
	const struct agent_context_record *before_records, int before_count,
	const struct agent_result *before_latest,
	const struct agent_context_detail *before_detail)
{
	struct agent_context_detail after_detail;
	struct agent_result *direct_latest =
		(struct agent_result *)(final_info.context_base +
					final_info.latest_response_offset);
	int n;

	check(bytes_equal((void *)final_info.context_base, sync_mirror_before,
			  before_header->user_cache_offset),
	      "failed context publish preserves direct Context");
	check(context_detail(before_records[0].sequence, &after_detail) == 0 &&
	      bytes_equal(&after_detail, before_detail, sizeof(after_detail)),
	      "failed context publish preserves overwritten detail");
	n = context_snapshot(&final_header, records, AGENT_CONTEXT_MAX_RECORDS);

	check(n == before_count &&
	      bytes_equal(&final_header, before_header, sizeof(final_header)),
	      "failed context publish preserves header");
	check(bytes_equal(records, before_records,
			  before_count * sizeof(records[0])),
	      "failed context publish preserves records");
	check(bytes_equal(direct_latest, before_latest, sizeof(*before_latest)),
	      "failed context publish preserves latest");
}

static void check_context_sync_failure_atomicity(void)
{
	struct agent_context_header before_header;
	struct agent_context_detail before_detail;
	struct agent_result before_latest;
	struct agent_result *direct_latest;
	int n;

	check(context_clear() == 0, "context sync test clear");
	for (int seeded = 0; seeded < AGENT_CONTEXT_MAX_RECORDS;
	     seeded += AGENT_BATCH_MAX) {
		int count = AGENT_CONTEXT_MAX_RECORDS - seeded;

		if (count > AGENT_BATCH_MAX)
			count = AGENT_BATCH_MAX;
		for (int i = 0; i < count; i++)
			make_echo(&ops[i], 7350 + seeded + i, "sync-atomic");
		check(agent_run(ops, results, count, 0) == count,
		      "context sync test seed");
	}
	n = context_snapshot(&final_header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_CONTEXT_MAX_RECORDS && final_header.oldest_sequence == 1 &&
		      final_header.latest_sequence == AGENT_CONTEXT_MAX_RECORDS,
	      "context sync test full fifo snapshot");
	before_header = final_header;
	memcpy(sync_records_before, records, sizeof(sync_records_before));
	memcpy(sync_mirror_before, (void *)final_info.context_base,
	       before_header.user_cache_offset);
	direct_latest = (struct agent_result *)(final_info.context_base +
					       final_info.latest_response_offset);
	before_latest = *direct_latest;
	check(context_detail(sync_records_before[0].sequence, &before_detail) == 0,
	      "context sync test overwrite detail");

	arm_context_sync_failure(&final_info, &final_header);
	make_echo(&ops[0], 7350 + AGENT_CONTEXT_MAX_RECORDS,
		  "sync-fail-append");
	check(agent_run(&ops[0], &results[0], 1, 0) == -1,
	      "append sync failure surfaced");
	check_context_sync_unchanged(&before_header, sync_records_before, n,
				     &before_latest, &before_detail);

	arm_context_sync_failure(&final_info, &final_header);
	check(context_rollback(sync_records_before[0].sequence) ==
		      AGENT_STATUS_NO_SPACE,
	      "rollback sync failure surfaced");
	check_context_sync_unchanged(&before_header, sync_records_before, n,
				     &before_latest, &before_detail);

	arm_context_sync_failure(&final_info, &final_header);
	check(context_clear() == AGENT_STATUS_NO_SPACE,
	      "clear sync failure surfaced");
	check_context_sync_unchanged(&before_header, sync_records_before, n,
				     &before_latest, &before_detail);

	make_echo(&ops[0], 7351 + AGENT_CONTEXT_MAX_RECORDS, "sync-recovered");
	check(agent_run(&ops[0], &results[0], 1, 0) == 1 &&
	      results[0].sequence == before_header.total_calls + 1,
	      "context publish recovers after sync failure");
	n = context_snapshot(&final_header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_CONTEXT_MAX_RECORDS &&
	      final_header.oldest_sequence ==
		      before_header.oldest_sequence + 1 &&
	      final_header.latest_sequence == before_header.latest_sequence + 1 &&
	      final_header.dropped_records == before_header.dropped_records + 1,
	      "context publish recovery commits fifo state");
	check(context_detail(sync_records_before[0].sequence, &before_detail) ==
		      AGENT_STATUS_NOT_FOUND &&
	      context_detail(final_header.latest_sequence, &before_detail) == 0,
	      "context publish recovery replaces oldest detail");
	printf("agentfinal_ucore: context_sync_atomic=1 append=1 rollback=1 clear=1 recovery=1\n");
}
#endif

static void check_legacy_name_protocol(void)
{
	struct agent_request *req = &final_req;
	struct agent_response *resp = &final_resp;

	memset(req, 0, sizeof(*req));
	memset(resp, 0, sizeof(*resp));
	req->version = AGENT_CALL_VERSION;
	req->request_id = 7101;
	strcpy(req->tool_name, "echo");
	strcpy(req->payload_key, "payload");
	req->payload_type = AGENT_PARAM_STRING;
	strcpy(req->arg0_key, "arg0");
	req->arg0_type = AGENT_PARAM_UINT64;
	strcpy(req->arg1_key, "arg1");
	req->arg1_type = AGENT_PARAM_UINT64;
	req->arg0 = 21;
	req->arg1 = 22;
	strcpy(req->payload, "legacy-name");
	check(agent_call(req, resp) == 0, "legacy name echo");
	check(resp->status == AGENT_STATUS_OK, "legacy echo status");
	check(strcmp(resp->result, "legacy-name") == 0, "legacy echo text");

	memset(req, 0, sizeof(*req));
	memset(resp, 0, sizeof(*resp));
	req->version = AGENT_CALL_VERSION;
	req->request_id = 7102;
	strcpy(req->tool_name, "pid_info");
	check(agent_call(req, resp) == 0, "legacy name pid");
	check(resp->status == AGENT_STATUS_OK, "legacy pid status");
	check(resp->value2 == 1, "legacy pid agent");

	memset(req, 0, sizeof(*req));
	memset(resp, 0, sizeof(*resp));
	req->version = AGENT_CALL_VERSION;
	req->request_id = 7103;
	strcpy(req->tool_name, "query_file");
	strcpy(req->payload_key, "path");
	req->payload_type = AGENT_PARAM_STRING;
	strcpy(req->payload, "r42align");
	check(agent_call(req, resp) == 0, "legacy name query_file");
	check(resp->status == AGENT_STATUS_OK, "legacy query_file status");
	check(resp->value1 != 0, "legacy query_file inum");

	memset(req, 0, sizeof(*req));
	memset(resp, 0, sizeof(*resp));
	req->version = AGENT_CALL_VERSION;
	req->request_id = 7104;
	strcpy(req->tool_name, "read_file_digest");
	strcpy(req->payload_key, "selector");
	req->payload_type = AGENT_PARAM_STRING;
	strcpy(req->payload, "r42align");
	check(agent_call(req, resp) == 0, "legacy name digest");
	check(resp->status == AGENT_STATUS_OK, "legacy digest status");
	check(resp->value0 >= resp->value1, "legacy digest size");

	memset(req, 0, sizeof(*req));
	memset(resp, 0, sizeof(*resp));
	req->version = AGENT_CALL_VERSION;
	req->request_id = 7105;
	strcpy(req->tool_name, "dependency_update");
	strcpy(req->payload_key, "selector");
	req->payload_type = AGENT_PARAM_STRING;
	strcpy(req->payload,
	       "source=report;target=align;namespace=lab-gene-x;run_id=RUN-042");
	check(agent_call(req, resp) == 0, "legacy dependency update");
	check(resp->status == AGENT_STATUS_OK, "legacy dependency status");
	check(strcmp(resp->result, "dependency_updated") == 0,
	      "legacy dependency text");

	memset(req, 0, sizeof(*req));
	memset(resp, 0, sizeof(*resp));
	req->version = AGENT_CALL_VERSION;
	req->request_id = 7106;
	strcpy(req->tool_name, "dependency_query");
	strcpy(req->payload_key, "label");
	req->payload_type = AGENT_PARAM_STRING;
	strcpy(req->payload,
	       "label=report;namespace=lab-gene-x;run_id=RUN-042");
	check(agent_call(req, resp) == 0, "legacy dependency query");
	check(resp->status == AGENT_STATUS_OK, "legacy dependency query status");
	check(text_contains(resp->result, "align"),
	      "legacy dependency query result");
	printf("agentfinal_ucore: legacy_name_protocol=1\n");
}

static void make_generic_op(struct agent_op *op, int tool_id, uint64 id,
			    uint64 arg0, const char *payload)
{
	memset(op, 0, sizeof(*op));
	op->version = AGENT_CALL_VERSION;
	op->tool_id = tool_id;
	op->request_id = id;
	op->arg0 = arg0;
	if (payload)
		strcpy(op->payload, payload);
}

static int run_metadata_op(struct agent_op *op, struct agent_result *res,
			   const char *msg)
{
	for (int retries = 0; retries < 8 * AGENT_FILE_META_MAX; retries++) {
		check(agent_run(op, res, 1, 0) == 1, msg);
		if (res->status != AGENT_STATUS_RETRY)
			return retries;
		sched_yield();
	}
	check(0, "metadata operation retry bound");
	return -1;
}

static void check_generic_action_and_llm(void)
{
	static struct agent_op op;
	static struct agent_result res;
	static struct agent_event event;
	int action_retries;
	int artifact_retries;

	make_generic_op(&op, AGENT_TOOL_ACTION_COMMIT, 7201, 0,
			"label=align;run_id=RUN-042;namespace=lab-gene-x");
	action_retries = run_metadata_op(&op, &res, "generic action run");
	check(res.status == AGENT_STATUS_OK, "generic action status");
	check(strcmp(res.result, "action_committed") == 0,
	      "generic action text");

	make_generic_op(&op, AGENT_TOOL_ARTIFACT_UPDATE, 7202, 0,
			"label=report;run_id=RUN-042;namespace=lab-gene-x");
	artifact_retries = run_metadata_op(&op, &res, "generic artifact run");
	printf("agentfinal_ucore: artifact status=%d result=%s token=%d job=%d retries=%d\n",
	       res.status, res.result, (int)res.value0, (int)res.value1,
	       artifact_retries);
	check(res.status == AGENT_STATUS_OK, "generic artifact status");
	check(strcmp(res.result, "artifact_updated") == 0,
	      "generic artifact text");
	printf("agentfinal_ucore: generic_action_abi=1 action_retries=%d artifact_retries=%d\n",
	       action_retries, artifact_retries);

	check(agent_watch(AGENT_EVENT_LLM_DONE, "template") == 0,
	      "watch llm done");
	make_generic_op(&op, AGENT_TOOL_LLM_REQUEST, 7203, 0,
			"template prompt summary");
	check(agent_run(&op, &res, 1, 0) == 1, "llm request run");
	check(res.status == AGENT_STATUS_OK, "llm request status");
	check(strcmp(res.result, "llm_request") == 0, "llm request text");
	make_generic_op(&op, AGENT_TOOL_LLM_RESPONSE, 7204, getpid(),
			"template response summary");
	check(agent_run(&op, &res, 1, 0) == 1, "llm response run");
	check(res.status == AGENT_STATUS_OK, "llm response status");
	check(res.value2 == 1, "llm response delivered");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 20) == AGENT_STATUS_OK, "wait llm response");
	check(event.type == AGENT_EVENT_LLM_DONE, "llm event type");
	check(strcmp(event.payload, "template response summary") == 0,
	      "llm event payload");
	printf("agentfinal_ucore: llm_template_relay=1\n");
}

static void check_runtime_trace(void)
{
	int n;
	int has_context = 0;
	int has_sched = 0;
	int has_wait = 0;
	uint64 last_tick = 0;

	n = agent_trace_snapshot(TRACE_RECORDS, AGENT_TRACE_MAX_RECORDS);
	check(n > 0, "runtime trace count");
	check(n <= AGENT_TRACE_MAX_RECORDS, "runtime trace cap");
	for (int i = 0; i < n; i++) {
		check(TRACE_RECORDS[i].tick >= last_tick,
		      "runtime trace order");
		last_tick = TRACE_RECORDS[i].tick;
		if (TRACE_RECORDS[i].kind == AGENT_TRACE_KIND_CONTEXT) {
			has_context = 1;
			check(TRACE_RECORDS[i].sequence != 0,
			      "trace context sequence");
			if (TRACE_RECORDS[i].tool_id == AGENT_TOOL_AGENT_WAIT &&
			    TRACE_RECORDS[i].value0 == AGENT_EVENT_MESSAGE)
				has_wait = 1;
		}
		if (TRACE_RECORDS[i].kind == AGENT_TRACE_KIND_SCHED) {
			has_sched = 1;
			check((TRACE_RECORDS[i].flags &
			       AGENT_SCHED_REASON_ROLE_WEIGHT) != 0,
			      "trace sched reason");
		}
	}
	check(has_context, "trace has context");
	check(has_sched, "trace has sched");
	check(has_wait, "trace has wait");
	printf("agentfinal_ucore: runtime_trace=1 records=%d context=%d sched=%d wait=%d\n",
	       n, has_context, has_sched, has_wait);
}

static void check_span_trace(struct agent_info *info)
{
	int total;
	int n;
	int has_context = 0;
	int has_event = 0;
	uint64 last_sequence = 0;

	check(agent_info(info) == 0, "span trace info");
	check(info->current_span_id != 0, "span trace current span");
	total = agent_span_trace_snapshot(0, 0);
	check(total > 0, "span trace total");
	n = agent_span_trace_snapshot(SPAN_RECORDS, AGENT_AUDIT_MAX_RECORDS);
	check(n > 0, "span trace records");
	check(n <= total, "span trace count");
	for (int i = 0; i < n; i++) {
		check(SPAN_RECORDS[i].span_id == info->current_span_id,
		      "span trace id");
		check(SPAN_RECORDS[i].sequence >= last_sequence,
		      "span trace order");
		last_sequence = SPAN_RECORDS[i].sequence;
		if (SPAN_RECORDS[i].kind == AGENT_AUDIT_KIND_CONTEXT)
			has_context = 1;
		if (SPAN_RECORDS[i].kind == AGENT_AUDIT_KIND_EVENT_ENQUEUE ||
		    SPAN_RECORDS[i].kind == AGENT_AUDIT_KIND_EVENT_CONSUME)
			has_event = 1;
	}
	check(has_context, "span trace context");
	check(has_event, "span trace event");
	printf("agentfinal_ucore: span_trace=1 records=%d context=%d event=%d\n",
	       n, has_context, has_event);
}

static void check_unified_timeline(void)
{
	int total;
	int n;
	int has_context = 0;
	int has_sched = 0;
	int has_audit = 0;
	int has_event_audit = 0;
	int audit_records = 0;
	int recent_records = 0;
	int cursor_records = 0;
	uint64 last_tick = 0;
	uint64 mid_tick;
	uint64 cursor_tick;
	uint64 cursor_sequence;
	int cursor_source;

	total = agent_timeline_snapshot(0, 0);
	check(total > 0, "timeline total");
	n = agent_timeline_snapshot(TIMELINE_RECORDS,
				    AGENT_TIMELINE_MAX_RECORDS);
	check(n > 0, "timeline records");
	check(n <= AGENT_TIMELINE_MAX_RECORDS, "timeline count");
	for (int i = 0; i < n; i++) {
		check(TIMELINE_RECORDS[i].tick >= last_tick,
		      "timeline order");
		last_tick = TIMELINE_RECORDS[i].tick;
		if (TIMELINE_RECORDS[i].source ==
		    AGENT_TIMELINE_SOURCE_CONTEXT)
			has_context = 1;
		if (TIMELINE_RECORDS[i].source == AGENT_TIMELINE_SOURCE_SCHED)
			has_sched = 1;
		if (TIMELINE_RECORDS[i].source == AGENT_TIMELINE_SOURCE_AUDIT) {
			has_audit = 1;
			if (TIMELINE_RECORDS[i].kind ==
				    AGENT_AUDIT_KIND_EVENT_ENQUEUE ||
			    TIMELINE_RECORDS[i].kind ==
				    AGENT_AUDIT_KIND_EVENT_CONSUME)
				has_event_audit = 1;
		}
		check(TIMELINE_RECORDS[i].source ==
			      AGENT_TIMELINE_SOURCE_CONTEXT ||
		      TIMELINE_RECORDS[i].source ==
			      AGENT_TIMELINE_SOURCE_SCHED ||
		      TIMELINE_RECORDS[i].source ==
			      AGENT_TIMELINE_SOURCE_AUDIT,
		      "timeline supported source");
	}
	check(has_context, "timeline context");
	check(has_sched, "timeline sched");
	check(has_audit, "timeline audit");
	check(has_event_audit, "timeline event audit");
	mid_tick = TIMELINE_RECORDS[n / 2].tick;
	cursor_tick = TIMELINE_RECORDS[n / 2].tick;
	cursor_source = TIMELINE_RECORDS[n / 2].source;
	cursor_sequence = TIMELINE_RECORDS[n / 2].sequence;

	memset(&timeline_filter, 0, sizeof(timeline_filter));
	timeline_filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK;
	timeline_filter.source_mask = AGENT_TIMELINE_SOURCE_MASK_AUDIT;
	audit_records = agent_timeline_query(&timeline_filter, TIMELINE_RECORDS,
					     AGENT_TIMELINE_MAX_RECORDS);
	check(audit_records > 0, "timeline query audit");
	for (int i = 0; i < audit_records; i++)
		check(TIMELINE_RECORDS[i].source == AGENT_TIMELINE_SOURCE_AUDIT,
		      "timeline query audit source");

	memset(&timeline_filter, 0, sizeof(timeline_filter));
	timeline_filter.flags = AGENT_TIMELINE_FILTER_START_TICK;
	timeline_filter.start_tick = mid_tick;
	recent_records = agent_timeline_query(&timeline_filter,
					      TIMELINE_RECORDS,
					      AGENT_TIMELINE_MAX_RECORDS);
	check(recent_records > 0, "timeline query recent");
	for (int i = 0; i < recent_records; i++)
		check(TIMELINE_RECORDS[i].tick >= mid_tick,
		      "timeline query recent tick");

	memset(&timeline_filter, 0, sizeof(timeline_filter));
	timeline_filter.flags = AGENT_TIMELINE_FILTER_AFTER_CURSOR;
	timeline_filter.after_tick = cursor_tick;
	timeline_filter.after_source = cursor_source;
	timeline_filter.after_sequence = cursor_sequence;
	cursor_records = agent_timeline_query(&timeline_filter,
					      TIMELINE_RECORDS,
					      AGENT_TIMELINE_MAX_RECORDS);
	check(cursor_records > 0, "timeline query cursor");
	for (int i = 0; i < cursor_records; i++)
		check(timeline_after_cursor(&TIMELINE_RECORDS[i], cursor_tick,
					    cursor_source, cursor_sequence),
		      "timeline query cursor order");

	printf("agentfinal_ucore: unified_timeline=1 records=%d context=%d sched=%d audit=%d event=%d\n",
	       n, has_context, has_sched, has_audit, has_event_audit);
	printf("agentfinal_ucore: timeline_query=1 audit=%d recent=%d cursor=%d\n",
	       audit_records, recent_records, cursor_records);
}

static void check_provenance_graph(void)
{
	int total;
	int n;
	int has_context = 0;
	int has_audit = 0;

	total = agent_provenance_snapshot(0, 0);
	check(total >= AGENT_BATCH_MAX - 1, "provenance total");
	n = agent_provenance_snapshot(PROVENANCE_EDGES, FINAL_PROVENANCE_MAX);
	check(n > 0, "provenance records");
	check(n <= total, "provenance count");
	for (int i = 0; i < n; i++) {
		struct agent_provenance_edge *edge = &PROVENANCE_EDGES[i];

		check(edge->source_sequence != 0,
		      "provenance source sequence");
		check(edge->target_sequence != 0,
		      "provenance target sequence");
		if (edge->kind == AGENT_PROVENANCE_EDGE_CONTEXT &&
		    edge->source_type == AGENT_PROVENANCE_NODE_CONTEXT &&
		    edge->target_type == AGENT_PROVENANCE_NODE_CONTEXT &&
		    edge->source_pid == getpid() &&
		    edge->target_pid == getpid() &&
		    edge->source_sequence == 1 &&
		    edge->target_sequence == 2)
			has_context = 1;
		if (edge->kind == AGENT_PROVENANCE_EDGE_AUDIT &&
		    edge->source_type == AGENT_PROVENANCE_NODE_CONTEXT &&
		    edge->target_type == AGENT_PROVENANCE_NODE_AUDIT &&
		    edge->source_pid == getpid())
			has_audit = 1;
	}
	check(has_context, "provenance context edge");
	check(has_audit, "provenance audit edge");
	printf("agentfinal_ucore: provenance_graph=1 edges=%d context=%d audit=%d\n",
	       n, has_context, has_audit);
}

static void check_run_ledger(void)
{
	int n;
	int projection_boundaries = 0;

	n = agent_audit_snapshot(SPAN_RECORDS, AGENT_AUDIT_MAX_RECORDS);
	check(n > 0, "ledger audit records");
	check(n <= AGENT_AUDIT_MAX_RECORDS, "ledger audit cap");
	memset(&final_ledger, 0, sizeof(final_ledger));
	check(agent_ledger_snapshot(&final_ledger) == 0, "ledger snapshot");
	check(final_ledger.version == AGENT_LEDGER_VERSION, "ledger version");
	check(final_ledger.visible_records > 0, "ledger visible");
	check(n <= (int)final_ledger.visible_records, "ledger visible audit");
	check(final_ledger.total_records >= final_ledger.visible_records,
	      "ledger total");
	check(final_ledger.latest_sequence >= final_ledger.oldest_sequence,
	      "ledger sequence range");
	check(final_ledger.ledger_hash != 0, "ledger hash");
	check(final_ledger.context_records > 0, "ledger context");
	check(final_ledger.event_records > 0, "ledger event");
	check(final_ledger.sched_records > 0, "ledger sched");
	check(final_ledger.other_records <= final_ledger.total_records,
	      "ledger other records");
	check(final_ledger.context_records + final_ledger.event_records +
		      final_ledger.sched_records +
		      final_ledger.other_records ==
		      final_ledger.total_records,
	      "ledger kind accounting");
	check(final_ledger.timeline_total >= final_ledger.total_records,
	      "ledger timeline total");
	check(SPAN_RECORDS[0].sequence >= final_ledger.oldest_sequence,
	      "ledger oldest window");
	check(SPAN_RECORDS[n - 1].sequence <= final_ledger.latest_sequence,
	      "ledger latest window");
	if (n == (int)final_ledger.visible_records)
		check(SPAN_RECORDS[0].sequence == final_ledger.oldest_sequence,
		      "ledger oldest");
	for (int i = 0; i < n; i++) {
		check(SPAN_RECORDS[i].record_hash != 0, "ledger record hash");
		if (i > 0) {
			check(SPAN_RECORDS[i].sequence >
				      SPAN_RECORDS[i - 1].sequence,
			      "ledger projection order");
			if (SPAN_RECORDS[i].prev_hash !=
			    SPAN_RECORDS[i - 1].record_hash)
				projection_boundaries++;
		}
	}
	check(final_ledger.dropped_records ==
		      final_ledger.total_records - final_ledger.visible_records,
	      "ledger bounded projection accounting");
	printf("agentfinal_ucore: run_ledger=1 records=%d projection_boundaries=%d dropped=%d other=%d digest_tag=%d context=%d event=%d sched=%d\n",
	       n, projection_boundaries, (int)final_ledger.dropped_records,
	       (int)final_ledger.other_records,
	       (int)final_ledger.ledger_hash,
	       (int)final_ledger.context_records,
	       (int)final_ledger.event_records,
	       (int)final_ledger.sched_records);
}

static void set_timeline_wait_future_filter(uint64 source_mask,
					    int event_type)
{
	check(agent_info(&final_info) == 0, "timeline wait current tick");
	memset(&timeline_filter, 0, sizeof(timeline_filter));
	timeline_filter.flags = AGENT_TIMELINE_FILTER_SOURCE_MASK |
				AGENT_TIMELINE_FILTER_START_TICK;
	timeline_filter.source_mask = source_mask;
	timeline_filter.start_tick = final_info.current_tick + 1;
	if (event_type != 0) {
		timeline_filter.flags |= AGENT_TIMELINE_FILTER_EVENT_TYPE;
		timeline_filter.event_type = event_type;
	}
}

static void check_timeline_wait(void)
{
	int waited;
	int queried;
	int consumed;
	int read_records;
	int timeout_status;
	int gated_status;
	int event_gated_status;
	int source_gate;
	int event_gate;
	uint64 wait_count_before;
	uint64 wait_sleep_before;
	uint64 wait_wakeup_before;
	uint64 wait_sleep_after;
	uint64 wait_wakeup_after;
	uint64 gate_wakeup_before;
	uint64 gate_wakeup_after;
	uint64 event_wakeup_before;
	uint64 event_wakeup_after;
	uint64 read_sleep_before;
	uint64 read_wakeup_before;
	uint64 read_sleep_after;
	uint64 read_wakeup_after;

	set_timeline_wait_future_filter(AGENT_TIMELINE_SOURCE_MASK_CONTEXT,
					0);
	waited = agent_timeline_wait(&timeline_filter, 1);
	check(waited == AGENT_STATUS_TIMEOUT, "timeline wait timeout");
	timeout_status = waited;

	check(agent_watch(AGENT_EVENT_TIMER, "heartbeat") == 0,
	      "timeline wait timer watch");
	check(agent_heartbeat(1) == 0, "timeline wait heartbeat");
	set_timeline_wait_future_filter(AGENT_TIMELINE_SOURCE_MASK_CONTEXT,
					0);
	check(agent_info(&final_info) == 0,
	      "timeline wait gate before");
	gate_wakeup_before = final_info.timeline_wait_wakeup_count;
	gated_status = agent_timeline_wait(&timeline_filter, 6);
	check(gated_status == AGENT_STATUS_TIMEOUT,
	      "timeline wait source gate timeout");
	check(agent_info(&final_info) == 0,
	      "timeline wait gate after");
	gate_wakeup_after = final_info.timeline_wait_wakeup_count;
	source_gate = gate_wakeup_after == gate_wakeup_before;
	check(source_gate, "timeline wait source gate");

	set_timeline_wait_future_filter(AGENT_TIMELINE_SOURCE_MASK_AUDIT,
					AGENT_EVENT_MESSAGE);
	check(agent_info(&final_info) == 0,
	      "timeline wait event gate before");
	event_wakeup_before = final_info.timeline_wait_wakeup_count;
	event_gated_status = agent_timeline_wait(&timeline_filter, 6);
	check(event_gated_status == AGENT_STATUS_TIMEOUT,
	      "timeline wait event gate timeout");
	check(agent_info(&final_info) == 0,
	      "timeline wait event gate after");
	event_wakeup_after = final_info.timeline_wait_wakeup_count;
	event_gate = event_wakeup_after == event_wakeup_before;
	check(event_gate, "timeline wait event gate");

	check(agent_heartbeat_stop() == 0, "timeline wait gate heartbeat stop");
	for (;;) {
		consumed = agent_wait(&final_event, 0);
		if (consumed != AGENT_STATUS_OK)
			break;
	}
	check(consumed == AGENT_STATUS_TIMEOUT, "timeline wait gate drain");

	check(agent_heartbeat(8) == 0, "timeline wait heartbeat restart");
	set_timeline_wait_future_filter(AGENT_TIMELINE_SOURCE_MASK_AUDIT,
					AGENT_EVENT_TIMER);
	check(agent_info(&final_info) == 0,
	      "timeline wait info before");
	wait_count_before = final_info.timeline_wait_count;
	wait_sleep_before = final_info.timeline_wait_sleep_count;
	wait_wakeup_before = final_info.timeline_wait_wakeup_count;
	waited = agent_timeline_wait(&timeline_filter, 50);
	check(waited > 0, "timeline wait wake");
	queried = agent_timeline_query(&timeline_filter, TIMELINE_RECORDS,
				       AGENT_TIMELINE_MAX_RECORDS);
	check(queried == waited, "timeline wait query count");
	check(queried > 0, "timeline wait query records");
	check(agent_info(&final_info) == 0,
	      "timeline wait info after");
	wait_sleep_after = final_info.timeline_wait_sleep_count;
	wait_wakeup_after = final_info.timeline_wait_wakeup_count;
	check(final_info.timeline_wait_count >= wait_count_before + 1,
	      "timeline wait count");
	check(wait_sleep_after > wait_sleep_before,
	      "timeline wait sleep count");
	check(wait_wakeup_after > wait_wakeup_before,
	      "timeline wait wakeup count");
	check(agent_heartbeat_stop() == 0,
	      "timeline wait heartbeat stop before read");
	for (;;) {
		consumed = agent_wait(&final_event, 0);
		if (consumed != AGENT_STATUS_OK)
			break;
	}
	check(consumed == AGENT_STATUS_TIMEOUT,
	      "timeline wait drain before read");

	check(agent_heartbeat(8) == 0, "timeline read heartbeat restart");
	set_timeline_wait_future_filter(AGENT_TIMELINE_SOURCE_MASK_AUDIT,
					AGENT_EVENT_TIMER);
	check(agent_info(&final_info) == 0,
	      "timeline read info before");
	read_sleep_before = final_info.timeline_wait_sleep_count;
	read_wakeup_before = final_info.timeline_wait_wakeup_count;
	read_records = agent_timeline_read(&timeline_filter, TIMELINE_RECORDS,
					   AGENT_TIMELINE_MAX_RECORDS, 50);
	check(read_records > 0, "timeline read wake");
	for (int i = 0; i < read_records; i++) {
		check(TIMELINE_RECORDS[i].source ==
			      AGENT_TIMELINE_SOURCE_AUDIT,
		      "timeline read source");
		check(TIMELINE_RECORDS[i].event_type == AGENT_EVENT_TIMER,
		      "timeline read event type");
	}
	check(agent_info(&final_info) == 0,
	      "timeline read info after");
	read_sleep_after = final_info.timeline_wait_sleep_count;
	read_wakeup_after = final_info.timeline_wait_wakeup_count;
	check(read_sleep_after > read_sleep_before,
	      "timeline read sleep count");
	check(read_wakeup_after > read_wakeup_before,
	      "timeline read wakeup count");

	check(agent_heartbeat_stop() == 0, "timeline wait heartbeat stop");
	for (;;) {
		consumed = agent_wait(&final_event, 0);
		if (consumed != AGENT_STATUS_OK)
			break;
	}
	check(consumed == AGENT_STATUS_TIMEOUT, "timeline wait drain timer");
	check(agent_unwatch(AGENT_EVENT_TIMER, "heartbeat") >= 0,
	      "timeline wait unwatch");
	printf("agentfinal_ucore: timeline_wait=1 timeout=%d source_gate=%d event_gate=%d wake=%d query=%d read=%d sleeps=%d wakeups=%d\n",
	       timeout_status, source_gate, event_gate, waited, queried,
	       read_records,
	       (int)(wait_sleep_after - wait_sleep_before),
	       (int)(wait_wakeup_after - wait_wakeup_before));
}

static void run_agent_child(void)
{
	struct agent_context_header *direct_header;
	struct agent_result *latest;
	struct agent_info *info = &final_info;
	struct agent_context_header *header = &final_header;
	struct agent_context_detail *detail = &final_detail;
	struct agent_context_record *manual = &final_manual;
	struct agent_file_query *q = &final_query;
	struct agent_file_query_result *qr = &final_query_result;
	struct agent_event *event = &final_event;
	int wake_rc;
	int n;

	check(agent_info(info) == 0, "agent_info");
	check(info->is_agent == 1, "is agent");
	check(info->agent_role == AGENT_ROLE_ORCHESTRATOR, "orchestrator role");
	check((info->capability_mask & AGENT_CAP_META_WRITE) != 0,
	      "meta write cap");
	check((info->capability_mask & AGENT_CAP_ORCHESTRATE) != 0,
	      "orchestrate cap");
	check(info->context_base == AGENT_CONTEXT_BASE, "context base");
	check(info->context_size == AGENT_CONTEXT_SIZE, "context size");
	direct_header = (struct agent_context_header *)info->context_base;
	latest = (struct agent_result *)(info->context_base +
					 info->latest_response_offset);
	check(direct_header->magic == AGENT_CONTEXT_MAGIC, "context magic");
	printf("agentfinal_ucore: context size=%d capacity=%d\n",
	       (int)info->context_size, (int)direct_header->capacity);

	check_context_mapping_isolation();
	check_context_commit_lane();
	check_context_rollback_identity();
#ifdef AGENT_CONTEXT_SYNC_TEST_PROFILE
	check_context_sync_failure_atomicity();
#endif
	check(context_clear() == 0, "context clear");
	for (int i = 0; i < AGENT_BATCH_MAX; i++)
		make_echo(&ops[i], i + 1, i == 7 ? "ucore-final" : "final");
	check(agent_run(ops, results, AGENT_BATCH_MAX, 0) == AGENT_BATCH_MAX,
	      "agent_run batch");
	check(results[0].sequence == 1, "first sequence");
	check(results[AGENT_BATCH_MAX - 1].sequence == AGENT_BATCH_MAX,
	      "last sequence");
	check(latest->sequence == AGENT_BATCH_MAX, "latest direct");
	printf("agentfinal_ucore: batch first_seq=%d last_seq=%d\n",
	       (int)results[0].sequence,
	       (int)results[AGENT_BATCH_MAX - 1].sequence);

	n = context_snapshot(header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_BATCH_MAX, "snapshot count");
	check(header->latest_sequence == AGENT_BATCH_MAX, "snapshot latest");
	check(header->version == AGENT_CONTEXT_VERSION, "context version");
	check(records[0].cause_sequence == 0, "first cause");
	check(records[0].span_id != 0, "first span");
	check(records[1].cause_sequence == records[0].sequence,
	      "next cause");
	check(records[1].span_id == records[0].span_id, "span continuity");
	check(header->current_cause_sequence == AGENT_BATCH_MAX,
	      "current cause");
	check(header->current_span_id == records[0].span_id, "header span");
	check(header->provenance_edges >= AGENT_BATCH_MAX - 1,
	      "provenance edges");
	printf("agentfinal_ucore: causal_context=1 first_cause=%d next_cause=%d span=%d edges=%d\n",
	       (int)records[0].cause_sequence,
	       (int)records[1].cause_sequence, (int)records[0].span_id,
	       (int)header->provenance_edges);
	check(records[0].prev_hash == 0, "first prev hash");
	check(records[0].record_hash != 0, "first record hash");
	for (int i = 1; i < n; i++) {
		check(records[i].prev_hash == records[i - 1].record_hash,
		      "context chain link");
		check(records[i].record_hash != 0, "context record hash");
	}
	check(header->latest_record_hash == records[n - 1].record_hash,
	      "header latest hash");
	printf("agentfinal_ucore: context_integrity=1 first_hash=%d latest_hash=%d\n",
	       (int)records[0].record_hash,
	       (int)header->latest_record_hash);
	check_provenance_graph();
	check(strcmp(records[7].payload, "ucore-final") == 0,
	      "short payload");
	check(strcmp(records[7].result, "ucore-final") == 0, "short result");
	printf("agentfinal_ucore: short_text_history=1 payload=%s result=%s\n",
	       records[7].payload, records[7].result);
	check(context_detail(records[7].sequence, detail) == 0,
	      "context detail");
	check((detail->flags & AGENT_CONTEXT_RECORD_F_SYSTEM) != 0,
	      "detail system flag");
	check(strcmp(detail->op.payload, "ucore-final") == 0,
	      "detail payload");
	printf("agentfinal_ucore: context_detail=1 sequence=%d\n",
	       (int)detail->sequence);

	records[0].sequence = 9999;
	n = context_snapshot(header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_BATCH_MAX, "snapshot after tamper");
	check(records[0].sequence == 1, "kernel Context protects snapshot");
	check(((struct agent_context_record *)(info->context_base +
					       info->records_offset))[0]
		      .sequence == 1,
	      "direct Context remains authoritative");
	printf("agentfinal_ucore: tamper_protected=1\n");

	memset(manual, 0, sizeof(*manual));
	manual->tool_id = AGENT_TOOL_CONTEXT_PUSH;
	manual->request_id = 6501;
	manual->status = AGENT_STATUS_OK;
	strcpy(manual->payload, "manual-audit");
	strcpy(manual->result, "manual-ok");
	check(context_push(manual) == 0, "manual context push");
	n = context_snapshot(header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_BATCH_MAX + 1, "manual snapshot count");
	check((records[n - 1].flags & AGENT_CONTEXT_RECORD_F_MANUAL) != 0,
	      "manual flag");
	check(context_detail(records[n - 1].sequence, detail) == 0,
	      "manual detail");
	check((detail->flags & AGENT_CONTEXT_RECORD_F_MANUAL) != 0,
	      "manual detail flag");
	printf("agentfinal_ucore: record_flags system=1 manual=1 truncated=%d\n",
	       (int)((records[7].flags & AGENT_CONTEXT_RECORD_F_TRUNCATED) !=
		     0));

	for (int round = 0; round < 2; round++) {
		for (int i = 0; i < AGENT_BATCH_MAX; i++)
			make_echo(&ops[i], 1000 + round * AGENT_BATCH_MAX + i,
				  "wrap");
		check(agent_run(ops, results, AGENT_BATCH_MAX, 0) ==
			      AGENT_BATCH_MAX,
		      "wrap batch");
	}
	n = context_snapshot(header, records, AGENT_CONTEXT_MAX_RECORDS);
	check(n == AGENT_CONTEXT_MAX_RECORDS, "fifo count");
	check(header->oldest_sequence == 66, "fifo oldest");
	check(header->latest_sequence == 193, "fifo latest");
	check(header->dropped_records == 65, "fifo dropped");
	check(header->active_path_count == AGENT_CONTEXT_MAX_RECORDS &&
		      header->active_path_oldest_sequence == 66 &&
		      records[0].sequence == 66 &&
		      records[0].path_parent_sequence == 65,
	      "FIFO active path converges to retained suffix");
	check(header->eviction_policy == AGENT_CONTEXT_EVICT_FIFO,
	      "FIFO policy published");
	{
		uint64 branch = header->branch_generation;
		uint64 visible_head = header->visible_head_sequence;

		check(context_rollback(header->oldest_sequence - 1) ==
			      AGENT_STATUS_NOT_FOUND,
		      "rollback evicted sequence rejected");
		check(context_snapshot(header, records,
				       AGENT_CONTEXT_MAX_RECORDS) ==
			      AGENT_CONTEXT_MAX_RECORDS,
		      "snapshot after rejected rollback");
		check(header->branch_generation == branch &&
		      header->visible_head_sequence == visible_head,
		      "rejected rollback leaves branch unchanged");
	}
	printf("agentfinal_ucore: fifo oldest=%d latest=%d dropped=%d policy=1\n",
	       (int)header->oldest_sequence, (int)header->latest_sequence,
	       (int)header->dropped_records);
	printf("agentfinal_ucore: context_rollback_negative nonexistent=1 evicted=1\n");

	check(agent_file_meta_init() == 0, "meta init");
	seed_demo_metadata();
	memset(q, 0, sizeof(*q));
	q->flags = AGENT_FILE_QUERY_USE_INDEX;
	q->max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(q->project, "lab-gene-x");
	strcpy(q->run_id, "RUN-042");
	strcpy(q->stage, "align");
	check(agent_file_query(q, qr) >= 1, "file query");
	check(qr->used_index == 1, "file query index");
	check(qr->plan == AGENT_FILE_QUERY_PLAN_STAGE_INDEX,
	      "file query plan");
	check((qr->plan_reason & AGENT_FILE_QUERY_REASON_STAGE_INDEX) != 0,
	      "file query reason");
	check(qr->candidate_records > 0 &&
	      qr->candidate_records <= qr->scanned_records,
	      "file query candidates");
	check(qr->returned >= 1 && strcmp(qr->hits[0].stage, "align") == 0,
	      "file query source stage");
	check((qr->hits[0].dependency_mask &
	       (agent_dependency_label_bit("analyze") |
		agent_dependency_label_bit("report"))) ==
		      (agent_dependency_label_bit("analyze") |
		       agent_dependency_label_bit("report")),
	      "file query dependency mask");
	check_user_query_cache(info, header, qr);
	printf("agentfinal_ucore: file_query hits=%d scanned=%d used_index=%d\n",
	       qr->total_hits, qr->scanned_records, qr->used_index);
	memset(q, 0, sizeof(*q));
	memset(qr, 0, sizeof(*qr));
	q->flags = AGENT_FILE_QUERY_USE_INDEX;
	q->max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(q->project, "lab-gene-x");
	strcpy(q->run_id, "RUN-042");
	strcpy(q->stage, "analyze");
	check(agent_file_query(q, qr) >= 1, "dependency target query");
	check(qr->plan == AGENT_FILE_QUERY_PLAN_STAGE_INDEX &&
	      (qr->plan_reason & AGENT_FILE_QUERY_REASON_STAGE_INDEX) != 0,
	      "dependency target index evidence");
	check(qr->returned >= 1 &&
	      strcmp(qr->hits[0].stage, "analyze") == 0 &&
	      (qr->hits[0].dependency_mask &
	       agent_dependency_label_bit("report")) != 0,
	      "dependency target metadata");
	printf("agentfinal_ucore: metadata_dependency_query=1 source=align target=analyze transitive=report stage_index=1\n");
	check_generic_action_and_llm();
	check_legacy_name_protocol();

	check(agent_watch(AGENT_EVENT_MESSAGE, "self") == 0, "watch");
	memset(event, 0, sizeof(*event));
	event->type = AGENT_EVENT_MESSAGE;
	event->corr_id = 7001;
	strcpy(event->payload, "self wake");
	wake_rc = agent_wake(info->is_agent ? getpid() : 0, event);
	check(wake_rc == 0, "wake self");
	check(agent_wait(event, 20) == AGENT_STATUS_OK, "wait self");
	check(event->corr_id == 7001, "wait corr");
	printf("agentfinal_ucore: event_wait=1 payload=%s\n", event->payload);
	check_runtime_trace();
	check_span_trace(info);
	check_unified_timeline();
	check_timeline_wait();
	check_run_ledger();
#ifdef WAIT_ATOMIC_TEST_PROFILE
	check_wait_publication_atomicity();
	check_thread_wait_deadlines();
	check_event_wake_handoff();
#endif

	printf("agentfinal_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agentfinal_ucore: Agent-OS on uCore final verification\n");
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "agent_create_role orchestrator");
	if (pid == 0)
		run_agent_child();
	check(waitpid(pid, &status) == pid, "wait child");
	check(status == 0, "child status");
	printf("agentfinal_ucore: parent passed\n");
	return 0;
}
