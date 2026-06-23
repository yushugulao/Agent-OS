// SPDX-License-Identifier: Apache-2.0

#include "kernel/types.h"
#include "kernel/stat.h"
#include "kernel/agent.h"
#include "user/user.h"

#define NOINLINE __attribute__((noinline))

#define FILE_BENCH_OPS 32768
#define TOOL_BENCH_OPS 8192
#define WAIT_BENCH_OPS 512
#define SNAPSHOT_ROUNDS 512
#define LAB_EVENT_BURST 8

static struct agent_op *batch_ops;
static struct agent_result *batch_results;
static struct agent_context_record *records;
static struct agent_file_query bench_query;
static struct agent_file_query scan_query;
static struct agent_file_query index_query;
static struct agent_file_query empty_query;
static struct agent_file_query trunc_query;
static struct agent_file_query report_query;
static struct agent_file_query_result *bench_query_result;
static struct agent_file_query_result *scan_query_result;
static struct agent_file_query_result *index_query_result;
static struct agent_file_query_result *empty_query_result;
static struct agent_file_query_result *trunc_query_result;
static struct agent_file_query_result *report_query_result;

static void
check(int condition, const char *message)
{
  if (!condition) {
    printf("labbench: check failed: %s\n", message);
    exit(1);
  }
}

static void
alloc_buffers(void)
{
  batch_ops = malloc(sizeof(struct agent_op) * AGENT_BATCH_MAX);
  batch_results = malloc(sizeof(struct agent_result) * AGENT_BATCH_MAX);
  records = malloc(sizeof(struct agent_context_record) *
                   AGENT_CONTEXT_MAX_RECORDS);
  bench_query_result = malloc(sizeof(struct agent_file_query_result));
  scan_query_result = malloc(sizeof(struct agent_file_query_result));
  index_query_result = malloc(sizeof(struct agent_file_query_result));
  empty_query_result = malloc(sizeof(struct agent_file_query_result));
  trunc_query_result = malloc(sizeof(struct agent_file_query_result));
  report_query_result = malloc(sizeof(struct agent_file_query_result));
  check(batch_ops != 0 && batch_results != 0 && records != 0 &&
            bench_query_result != 0 && scan_query_result != 0 &&
            index_query_result != 0 && empty_query_result != 0 &&
            trunc_query_result != 0 && report_query_result != 0,
        "allocate buffers");
}

static int
contains(const char *text, const char *needle)
{
  int i;
  int j;

  if (needle[0] == 0)
    return 1;
  for (i = 0; text[i]; i++) {
    for (j = 0; needle[j] && text[i + j] == needle[j]; j++)
      ;
    if (needle[j] == 0)
      return 1;
  }
  return 0;
}

static void
print_perf(const char *name, int ops, int ticks, int base_ops, int base_ticks)
{
  uint64 safe_ticks = ticks > 0 ? ticks : 1;
  uint64 safe_base_ticks = base_ticks > 0 ? base_ticks : 1;
  uint64 ops_per_tick = ops / safe_ticks;
  uint64 speedup_x100 = ((uint64)ops * safe_base_ticks * 100) /
                        (safe_ticks * (uint64)base_ops);

  check(speedup_x100 > 0, "perf speedup overflow");
  printf("labbench: %s ops=%d ticks=%d ops_per_tick=%ld speedup_x100=%ld\n",
         name, ops, ticks, ops_per_tick, speedup_x100);
  printf("agentos:event type=BENCH case=%s ops=%d ticks=%d ops_per_tick=%ld speedup_x100=%ld\n",
         name, ops, ticks, ops_per_tick, speedup_x100);
}

static void
fill_file_query(struct agent_file_query *q, uint64 flags)
{
  memset(q, 0, sizeof(*q));
  q->flags = flags;
  q->max_hits = AGENT_FILE_QUERY_MAX_HITS;
  strcpy(q->project, "lab-gene-x");
  strcpy(q->workflow, "nightly-regression");
  strcpy(q->run_id, "RUN-042");
  strcpy(q->stage, "align");
}

static int
bench_file_query(uint64 flags, int ops)
{
  int i;
  int start;
  int end;

  fill_file_query(&bench_query, flags);
  start = uptime();
  for (i = 0; i < ops; i++) {
    check(agent_file_query(&bench_query, bench_query_result) >= 1,
          "file query");
    check(bench_query_result->total_hits >= 1, "file query hits");
  }
  end = uptime();
  return end - start;
}

static void NOINLINE
validate_file_queries(void)
{
  struct agent_file_query fid_query;
  struct agent_file_query_result *fid_result;

  fid_result = malloc(sizeof(struct agent_file_query_result));
  check(fid_result != 0, "fid result alloc");

  fill_file_query(&scan_query, AGENT_FILE_QUERY_SCAN);
  fill_file_query(&index_query, AGENT_FILE_QUERY_USE_INDEX);
  check(agent_file_query(&scan_query, scan_query_result) >= 1,
        "scan semantic query");
  check(agent_file_query(&index_query, index_query_result) >= 1,
        "index semantic query");
  check(scan_query_result->total_hits == index_query_result->total_hits,
        "scan/index total_hits");
  check(scan_query_result->returned == index_query_result->returned,
        "scan/index returned");
  check(index_query_result->used_index == 1, "index used");
  check(scan_query_result->used_index == 0, "scan not indexed");
  check(index_query_result->scanned_records < scan_query_result->scanned_records,
        "index scans fewer records");

  memset(&empty_query, 0, sizeof(empty_query));
  empty_query.flags = AGENT_FILE_QUERY_USE_INDEX;
  empty_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
  check(agent_file_query(&empty_query, empty_query_result) ==
            AGENT_STATUS_BAD_PARAM,
        "empty query rejected");
  strcpy(empty_query.status, "failed");
  check(agent_file_query(&empty_query, empty_query_result) == 0,
        "empty query syscall");
  check(empty_query_result->total_hits == 0 && empty_query_result->returned == 0,
        "empty query result");

  memset(&trunc_query, 0, sizeof(trunc_query));
  trunc_query.flags = AGENT_FILE_QUERY_USE_INDEX;
  trunc_query.max_hits = 1;
  strcpy(trunc_query.kind, "report");
  check(agent_file_query(&trunc_query, trunc_query_result) == 1,
        "trunc query syscall");
  check(trunc_query_result->total_hits > trunc_query_result->returned,
        "trunc total greater");
  check(trunc_query_result->truncated == 1, "trunc flag");

  memset(&report_query, 0, sizeof(report_query));
  report_query.flags = AGENT_FILE_QUERY_USE_INDEX;
  report_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
  strcpy(report_query.run_id, "RUN-042");
  strcpy(report_query.kind, "report");
  check(agent_file_query(&report_query, report_query_result) >= 1,
        "report query syscall");
  check(report_query_result->used_index == 1, "report query index");
  check(report_query_result->scanned_records < scan_query_result->scanned_records,
        "report query selective");

  memset(&fid_query, 0, sizeof(fid_query));
  fid_query.flags = AGENT_FILE_QUERY_USE_INDEX;
  fid_query.fid = 4;
  fid_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
  check(agent_file_query(&fid_query, fid_result) == 1, "fid query syscall");
  check(fid_result->total_hits == 1 &&
            strcmp(fid_result->hits[0].physical_name,
                   "lab_RUN042_align_err") == 0,
        "fid query returns full metadata");

  printf("labbench: file_semantics scan_hits=%d index_hits=%d scan_scanned=%d index_scanned=%d report_scanned=%d empty=%d truncated=%d fid=1\n",
         scan_query_result->total_hits, index_query_result->total_hits,
         scan_query_result->scanned_records, index_query_result->scanned_records,
         report_query_result->scanned_records, empty_query_result->total_hits,
         trunc_query_result->truncated);
}

static void NOINLINE
validate_metadata_semantics(void)
{
  struct agent_file_meta meta;
  struct agent_op op;
  struct agent_result res;
  struct agent_file_query query;
  struct agent_file_query_result *result;
  uint64 scoped_deps = AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE;
  uint64 custom_deps = AGENT_DEP_ALIGN | AGENT_DEP_REPORT;

  result = malloc(sizeof(struct agent_file_query_result));
  check(result != 0, "metadata result alloc");

  memset(&meta, 0, sizeof(meta));
  meta.fid = 4;
  meta.dependency_mask = scoped_deps;
  check(agent_file_meta_set(&meta) == 0, "dependency mask update");

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_DEPENDENCY_QUERY;
  op.request_id = 910001;
  strcpy(op.payload, "align");
  check(agent_run(&op, &res, 1, 0) == 1, "dependency query run");
  check(res.status == AGENT_STATUS_OK, "dependency query status");
  check(res.value0 == scoped_deps, "dependency query uses metadata");
  check(strcmp(res.result, "report+archive") == 0,
        "dependency query text scoped");

  memset(&meta, 0, sizeof(meta));
  strcpy(meta.physical_name, "lab_history_report");
  strcpy(meta.status, "history");
  check(agent_file_meta_set(&meta) == 0, "history marker set");

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_RERUN_STAGE;
  op.request_id = 910002;
  strcpy(op.payload, "align");
  check(agent_run(&op, &res, 1, 0) == 1, "mask rerun run");
  check(res.status == AGENT_STATUS_OK, "mask rerun status");
  check(res.value0 == scoped_deps && res.value2 == 2,
        "mask rerun counts scoped artifacts");

  memset(&query, 0, sizeof(query));
  query.flags = AGENT_FILE_QUERY_USE_INDEX;
  query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
  strcpy(query.run_id, "RUN-042");
  strcpy(query.stage, "analyze");
  strcpy(query.status, "pending");
  check(agent_file_query(&query, result) >= 1, "analyze still pending");
  check(result->total_hits >= 1, "mask rerun skipped analyze");

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_RERUN_STAGE;
  op.request_id = 910002;
  strcpy(op.payload, "report");
  check(agent_run(&op, &res, 1, 0) == 1,
        "same corr different stage rerun");
  check(res.status == AGENT_STATUS_OK, "same corr scoped by stage");

  memset(&query, 0, sizeof(query));
  query.flags = AGENT_FILE_QUERY_USE_INDEX;
  query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
  strcpy(query.run_id, "RUN-041");
  strcpy(query.stage, "report");
  strcpy(query.status, "history");
  check(agent_file_query(&query, result) >= 1, "history query");
  check(result->total_hits >= 1, "history status preserved");

  memset(&meta, 0, sizeof(meta));
  strcpy(meta.physical_name, "lab_RUN042_report_incomplete");
  strcpy(meta.status, "incomplete");
  check(agent_file_meta_set(&meta) == 0, "reset report incomplete");

  memset(&meta, 0, sizeof(meta));
  strcpy(meta.physical_name, "lab_RUN999_recovery_report");
  strcpy(meta.logical_path, "/lab/projects/lab-gene-x/runs/RUN-999/recovery.md");
  strcpy(meta.project, "lab-gene-x");
  strcpy(meta.workflow, "nightly-regression");
  strcpy(meta.run_id, "RUN-999");
  strcpy(meta.stage, "report");
  strcpy(meta.kind, "report");
  strcpy(meta.status, "failed");
  strcpy(meta.summary, "other run recovery report");
  check(agent_file_meta_set(&meta) == 0, "other run report insert");

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_WRITE_REPORT;
  op.request_id = 910003;
  strcpy(op.payload, "run_id=RUN-042;stage=report;summary=RUN042 report");
  check(agent_run(&op, &res, 1, 0) == 1, "write report run");
  check(res.status == AGENT_STATUS_OK && res.value0 == 910003,
        "write report status");

  memset(&query, 0, sizeof(query));
  query.flags = AGENT_FILE_QUERY_USE_INDEX;
  query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
  strcpy(query.physical_name, "lab_RUN042_recovery_report");
  strcpy(query.status, "ok");
  check(agent_file_query(&query, result) >= 1, "recovery report query");
  check(result->total_hits == 1, "single recovery report updated");

  memset(&query, 0, sizeof(query));
  query.flags = AGENT_FILE_QUERY_USE_INDEX;
  query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
  strcpy(query.physical_name, "lab_RUN042_report_incomplete");
  strcpy(query.status, "incomplete");
  check(agent_file_query(&query, result) >= 1,
        "incomplete report preserved");
  check(result->total_hits == 1, "write report did not update all reports");

  memset(&query, 0, sizeof(query));
  query.flags = AGENT_FILE_QUERY_USE_INDEX;
  query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
  strcpy(query.physical_name, "lab_RUN999_recovery_report");
  strcpy(query.status, "failed");
  check(agent_file_query(&query, result) >= 1, "other run report query");
  check(result->total_hits == 1, "write report scoped by run");

  memset(&meta, 0, sizeof(meta));
  meta.fid = 4;
  meta.dependency_mask = custom_deps;
  check(agent_file_meta_set(&meta) == 0, "custom dependency mask update");

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_DEPENDENCY_QUERY;
  op.request_id = 910004;
  strcpy(op.payload, "align");
  check(agent_run(&op, &res, 1, 0) == 1, "custom dependency query run");
  check(res.status == AGENT_STATUS_OK, "custom dependency query status");
  check(res.value0 == custom_deps &&
            strcmp(res.result, "align+report") == 0,
        "custom dependency text");

  memset(&meta, 0, sizeof(meta));
  meta.fid = 4;
  meta.update_mask = AGENT_FILE_META_UPDATE_DEPS;
  meta.dependency_mask = 0;
  check(agent_file_meta_set(&meta) == 0, "clear dependency mask");

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_DEPENDENCY_QUERY;
  op.request_id = 910005;
  strcpy(op.payload, "align");
  check(agent_run(&op, &res, 1, 0) == 1, "cleared dependency query run");
  check(res.status == AGENT_STATUS_OK, "cleared dependency query status");
  check(res.value0 == 0 && strcmp(res.result, "none") == 0,
        "cleared dependency text");

  memset(&meta, 0, sizeof(meta));
  strcpy(meta.physical_name, "lab_RUN042_new_qc_report");
  strcpy(meta.logical_path, "/lab/projects/lab-gene-x/runs/RUN-042/qc.md");
  strcpy(meta.project, "lab-gene-x");
  strcpy(meta.workflow, "nightly-regression");
  strcpy(meta.run_id, "RUN-042");
  strcpy(meta.stage, "report");
  strcpy(meta.kind, "report");
  strcpy(meta.status, "ok");
  strcpy(meta.summary, "inserted qc artifact");
  check(agent_file_meta_set(&meta) == 0, "insert new artifact");

  memset(&query, 0, sizeof(query));
  query.flags = AGENT_FILE_QUERY_USE_INDEX;
  query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
  strcpy(query.physical_name, "lab_RUN042_new_qc_report");
  check(agent_file_query(&query, result) >= 1, "inserted artifact query");
  check(result->total_hits == 1, "inserted artifact visible");

  memset(&meta, 0, sizeof(meta));
  strcpy(meta.physical_name, "lab_RUN042_new_qc_report");
  meta.update_mask = AGENT_FILE_META_DELETE;
  check(agent_file_meta_set(&meta) == 0, "delete inserted artifact");
  check(agent_file_query(&query, result) == 0, "deleted artifact query");
  check(result->total_hits == 0, "deleted artifact hidden");

  printf("labbench: metadata_dependency=1 scoped_rerun=1 scoped_report=1 history_preserved=1 single_report=1 mask_text=1 dep_clear=1 insert=1 delete=1\n");
}

static void
fill_echo_ops(uint64 base)
{
  int i;

  memset(batch_ops, 0, sizeof(struct agent_op) * AGENT_BATCH_MAX);
  for (i = 0; i < AGENT_BATCH_MAX; i++) {
    batch_ops[i].version = AGENT_CALL_VERSION;
    batch_ops[i].tool_id = AGENT_TOOL_ECHO;
    batch_ops[i].request_id = base + i;
    batch_ops[i].arg0 = i;
    batch_ops[i].arg1 = i + 1;
    strcpy(batch_ops[i].payload, "labbench");
  }
}

static int NOINLINE
bench_scalar_tool(int ops)
{
  struct agent_op op;
  struct agent_result res;
  int i;
  int start;
  int end;

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_ECHO;
  strcpy(op.payload, "labbench");
  start = uptime();
  for (i = 0; i < ops; i++) {
    op.request_id = i + 1;
    check(agent_run(&op, &res, 1, 0) == 1, "scalar run");
    check(res.status == AGENT_STATUS_OK, "scalar status");
  }
  end = uptime();
  return end - start;
}

static int NOINLINE
bench_batch_tool(int ops)
{
  int i;
  int rounds = ops / AGENT_BATCH_MAX;
  int start;
  int end;

  start = uptime();
  for (i = 0; i < rounds; i++) {
    fill_echo_ops(100000 + i * AGENT_BATCH_MAX);
    check(agent_run(batch_ops, batch_results, AGENT_BATCH_MAX, 0) ==
              AGENT_BATCH_MAX,
          "batch run");
    check(batch_results[AGENT_BATCH_MAX - 1].status == AGENT_STATUS_OK,
          "batch status");
  }
  end = uptime();
  return end - start;
}

static int NOINLINE
bench_context_query(int rounds)
{
  struct agent_context_record r;
  int i;
  int start;
  int end;

  start = uptime();
  for (i = 0; i < rounds; i++)
    check(context_query(0, &r, 1) == 1, "context_query");
  end = uptime();
  return end - start;
}

static int NOINLINE
bench_context_snapshot(int rounds, int *total_records)
{
  struct agent_context_header header;
  int i;
  int n;
  int start;
  int end;

  *total_records = 0;
  start = uptime();
  for (i = 0; i < rounds; i++) {
    n = context_snapshot(&header, records, AGENT_CONTEXT_MAX_RECORDS);
    check(n >= 1, "context_snapshot");
    *total_records += n;
  }
  end = uptime();
  return end - start;
}

static int NOINLINE
bench_capability(int ops)
{
  struct agent_op op;
  struct agent_result res;
  int i;
  int start;
  int end;

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_CAPABILITY_CHECK;
  op.arg0 = AGENT_ROLE_RECOVERY;
  strcpy(op.payload, "rerun_stage");
  start = uptime();
  for (i = 0; i < ops; i++) {
    op.request_id = 300000 + i;
    check(agent_run(&op, &res, 1, 0) == 1, "capability run");
    check(res.status == AGENT_STATUS_OK, "capability status");
  }
  end = uptime();
  return end - start;
}

static int NOINLINE
bench_duplicate(void)
{
  struct agent_op op;
  struct agent_result res;
  int start;
  int end;

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_RERUN_STAGE;
  op.request_id = 999001;
  op.arg0 = AGENT_ROLE_RECOVERY;
  strcpy(op.payload, "report");
  start = uptime();
  check(agent_run(&op, &res, 1, 0) == 1, "duplicate first");
  check(res.status == AGENT_STATUS_OK, "duplicate first status");
  check(agent_run(&op, &res, 1, 0) == 1, "duplicate second");
  check(res.status == AGENT_STATUS_DUPLICATE, "duplicate second status");
  end = uptime();
  printf("labbench: duplicate_reject attempts=2 executed=1 rejected=1 ticks=%d\n",
         end - start);
  printf("agentos:event type=BENCH case=duplicate_reject attempts=2 executed=1 rejected=1 ticks=%d\n",
         end - start);
  return end - start;
}

static void NOINLINE
run_permission_agent(void)
{
  struct agent_op op;
  struct agent_result res;
  struct agent_event event;
  struct agent_file_meta meta;
  struct agent_info info;

  check(agent_set_role(AGENT_ROLE_ORCHESTRATOR) == AGENT_STATUS_DENIED,
        "sentinel self escalation denied");
  check(agent_info(&info) == 0, "sentinel info after escalation");
  check(info.agent_role == AGENT_ROLE_SENTINEL,
        "sentinel role preserved after escalation");

  memset(&event, 0, sizeof(event));
  event.type = AGENT_EVENT_MESSAGE;
  strcpy(event.payload, "denied");
  check(agent_wake(9999, &event) == AGENT_STATUS_DENIED,
        "sentinel wake denied");

  memset(&meta, 0, sizeof(meta));
  meta.fid = 4;
  strcpy(meta.status, "failed");
  check(agent_file_meta_init() == AGENT_STATUS_DENIED,
        "sentinel meta init denied");
  check(agent_file_meta_set(&meta) == AGENT_STATUS_DENIED,
        "sentinel meta set denied");

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_RERUN_STAGE;
  op.request_id = 700001;
  strcpy(op.payload, "align");
  check(agent_run(&op, &res, 1, 0) == 1, "sentinel rerun denied run");
  check(res.status == AGENT_STATUS_DENIED, "sentinel rerun denied");

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_WRITE_REPORT;
  op.request_id = 700002;
  strcpy(op.payload, "report");
  check(agent_run(&op, &res, 1, 0) == 1, "sentinel report denied run");
  check(res.status == AGENT_STATUS_DENIED, "sentinel report denied");

  printf("labbench: permission_denied self_escalation=1 wake=1 meta=1 rerun=1 report=1\n");
  exit(0);
}

static void NOINLINE
bench_permission_denied(void)
{
  int pid;
  int status;

  pid = agent_create();
  check(pid >= 0, "permission agent create");
  if (pid == 0)
    run_permission_agent();
  wait(&status);
  check(status == 0, "permission agent status");
}

static void NOINLINE
run_core_bench(void)
{
  struct agent_event event;
  struct agent_info info;
  struct agent_op bad_flags_op;
  struct agent_result bad_flags_res;
  int scan_ticks;
  int index_ticks;
  int busy_ticks;
  int scalar_ticks;
  int batch_ticks;
  int query_ticks;
  int snapshot_ticks;
  int snapshot_records;
  int capability_ticks;

  check(agent_set_role(AGENT_ROLE_ORCHESTRATOR) == 0,
        "core role orchestrator");
  check(agent_file_meta_init() == 0, "meta init");
  memset(&bad_flags_op, 0, sizeof(bad_flags_op));
  memset(&bad_flags_res, 0, sizeof(bad_flags_res));
  bad_flags_op.version = AGENT_CALL_VERSION;
  bad_flags_op.tool_id = AGENT_TOOL_ECHO;
  strcpy(bad_flags_op.payload, "bad-flags");
  check(agent_run(&bad_flags_op, &bad_flags_res, 1, 1) ==
            AGENT_STATUS_BAD_PARAM,
        "agent_run bad flags rejected");
  validate_file_queries();
  validate_metadata_semantics();
  check(agent_watch(AGENT_EVENT_MESSAGE, "never") == 0, "timeout watch");
  check(agent_wait(&event, 1) == AGENT_STATUS_TIMEOUT, "wait timeout");
  check(event.status == AGENT_STATUS_TIMEOUT, "timeout event status");
  check(agent_watch(AGENT_EVENT_TIMER, "heartbeat") == 0, "timer watch");
  check(agent_heartbeat(2) == 0, "heartbeat");
  check(agent_wait(&event, 20) == AGENT_STATUS_OK, "heartbeat timer event");
  check(event.type == AGENT_EVENT_TIMER, "heartbeat timer type");
  check(agent_info(&info) == 0, "heartbeat info");
  check(info.heartbeat_interval == 2, "heartbeat interval info");
  check(info.last_heartbeat_tick > 0, "heartbeat tick info");
  check(agent_heartbeat_stop() == 0, "heartbeat stop");
  check(agent_info(&info) == 0, "heartbeat stop info");
  check(info.heartbeat_interval == 0, "heartbeat stopped interval");
  check(agent_unwatch(AGENT_EVENT_TIMER, "heartbeat") == 0,
        "timer unwatch");
  check(agent_unwatch(AGENT_EVENT_TIMER, "heartbeat") ==
            AGENT_STATUS_NOT_FOUND,
        "timer unwatch missing");
  printf("labbench: loop_timeout=1 heartbeat_timer=1 heartbeat_stop=1 unwatch=1 heartbeat_interval=%d last_heartbeat=%ld\n",
         info.heartbeat_interval, info.last_heartbeat_tick);
  scan_ticks = bench_file_query(AGENT_FILE_QUERY_SCAN, FILE_BENCH_OPS);
  index_ticks = bench_file_query(AGENT_FILE_QUERY_USE_INDEX, FILE_BENCH_OPS);
  busy_ticks = bench_file_query(AGENT_FILE_QUERY_SCAN, WAIT_BENCH_OPS);
  scalar_ticks = bench_scalar_tool(TOOL_BENCH_OPS);
  batch_ticks = bench_batch_tool(TOOL_BENCH_OPS);
  query_ticks = bench_context_query(SNAPSHOT_ROUNDS);
  snapshot_ticks = bench_context_snapshot(SNAPSHOT_ROUNDS, &snapshot_records);
  capability_ticks = bench_capability(FILE_BENCH_OPS);
  bench_duplicate();

  printf("labbench: case ops ticks ops_per_tick speedup_x100\n");
  print_perf("file_scan_query", FILE_BENCH_OPS, scan_ticks, FILE_BENCH_OPS,
             scan_ticks);
  print_perf("file_index_query", FILE_BENCH_OPS, index_ticks, FILE_BENCH_OPS,
             scan_ticks);
  print_perf("busy_poll_query", WAIT_BENCH_OPS, busy_ticks, WAIT_BENCH_OPS,
             busy_ticks);
  print_perf("scalar_tool_call", TOOL_BENCH_OPS, scalar_ticks, TOOL_BENCH_OPS,
             scalar_ticks);
  print_perf("batch_agent_run", TOOL_BENCH_OPS, batch_ticks, TOOL_BENCH_OPS,
             scalar_ticks);
  print_perf("context_query", SNAPSHOT_ROUNDS, query_ticks, SNAPSHOT_ROUNDS,
             query_ticks);
  print_perf("context_snapshot", snapshot_records, snapshot_ticks,
             SNAPSHOT_ROUNDS, query_ticks);
  print_perf("capability_check", FILE_BENCH_OPS, capability_ticks,
             FILE_BENCH_OPS, capability_ticks);
}

static void NOINLINE
run_wait_agent(int ack_fd, int ready_fd)
{
  struct agent_event event;
  struct agent_context_header header;
  int i;
  int n;
  char ch = 'R';

  check(agent_watch(AGENT_EVENT_MESSAGE, "bench") == 0, "wait watch");
  check(write(ready_fd, &ch, 1) == 1, "wait ready");
  for (i = 0; i < WAIT_BENCH_OPS; i++) {
    check(agent_wait(&event, 500) == AGENT_STATUS_OK, "wait event");
    check(event.type == AGENT_EVENT_MESSAGE, "wait event type");
    ch = 'A';
    check(write(ack_fd, &ch, 1) == 1, "wait ack");
  }
  n = context_snapshot(&header, records, AGENT_CONTEXT_MAX_RECORDS);
  check(n == AGENT_CONTEXT_MAX_RECORDS, "wait context full");
  check(header.latest_sequence >= WAIT_BENCH_OPS, "wait context latest");
  printf("labbench: event_context_records=%d latest=%ld\n", n,
         header.latest_sequence);
  exit(0);
}

static void NOINLINE
run_bystander_agent(int ack_fd, int ready_fd)
{
  struct agent_event event;
  char ch = 'B';

  check(agent_watch(AGENT_EVENT_MESSAGE, "other") == 0, "bystander watch");
  check(write(ready_fd, &ch, 1) == 1, "bystander ready");
  check(agent_wait(&event, 10) == AGENT_STATUS_TIMEOUT,
        "bystander timeout");
  ch = 'T';
  check(write(ack_fd, &ch, 1) == 1, "bystander ack");
  exit(0);
}

static void NOINLINE
run_waker_agent(int target_pid, int ack_fd, int ticks_fd)
{
  struct agent_event event;
  int i;
  int start;
  int end;
  char ch;
  int ticks;

  check(agent_set_role(AGENT_ROLE_ORCHESTRATOR) == 0,
        "waker role orchestrator");
  memset(&event, 0, sizeof(event));
  event.type = AGENT_EVENT_MESSAGE;
  strcpy(event.payload, "bench");
  start = uptime();
  for (i = 0; i < WAIT_BENCH_OPS; i++) {
    event.corr_id = i + 1;
    check(agent_wake(target_pid, &event) == 0, "agent wake");
    check(read(ack_fd, &ch, 1) == 1, "ack read");
  }
  end = uptime();
  ticks = end - start;
  check(write(ticks_fd, &ticks, sizeof(ticks)) == sizeof(ticks),
        "waker ticks write");
  exit(0);
}

static int NOINLINE
bench_event_wait_wake(void)
{
  int ack[2];
  int byack[2];
  int ready[2];
  int ticks_pipe[2];
  int pid;
  int bystander_pid;
  int waker_pid;
  int wait_ticks;
  int status;
  char ch;

  check(pipe(ack) == 0, "ack pipe");
  check(pipe(byack) == 0, "bystander ack pipe");
  check(pipe(ready) == 0, "ready pipe");
  check(pipe(ticks_pipe) == 0, "ticks pipe");
  pid = agent_create();
  check(pid >= 0, "wait agent create");
  if (pid == 0) {
    close(ack[0]);
    close(byack[0]);
    close(byack[1]);
    close(ready[0]);
    run_wait_agent(ack[1], ready[1]);
  }
  bystander_pid = agent_create();
  check(bystander_pid >= 0, "bystander agent create");
  if (bystander_pid == 0) {
    close(ack[0]);
    close(ack[1]);
    close(byack[0]);
    close(ready[0]);
    run_bystander_agent(byack[1], ready[1]);
  }
  close(ack[1]);
  close(byack[1]);
  close(ready[1]);
  check(read(ready[0], &ch, 1) == 1, "ready read");
  check(read(ready[0], &ch, 1) == 1, "bystander ready read");
  close(ready[0]);
  waker_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
  check(waker_pid >= 0, "waker agent create");
  if (waker_pid == 0) {
    close(byack[0]);
    close(byack[1]);
    close(ready[0]);
    close(ready[1]);
    close(ticks_pipe[0]);
    run_waker_agent(pid, ack[0], ticks_pipe[1]);
  }
  close(ticks_pipe[1]);
  close(ack[0]);
  check(read(ticks_pipe[0], &wait_ticks, sizeof(wait_ticks)) ==
            sizeof(wait_ticks),
        "ticks read");
  close(ticks_pipe[0]);
  check(read(byack[0], &ch, 1) == 1, "bystander timeout ack");
  check(ch == 'T', "bystander timeout marker");
  wait(&status);
  check(status == 0, "wait agent status");
  wait(&status);
  check(status == 0, "bystander status");
  wait(&status);
  check(status == 0, "waker status");
  close(byack[0]);
  printf("labbench: non_target_timeout=1\n");
  return wait_ticks;
}

static void NOINLINE
run_fifo_agent(int ready_fd, int go_fd)
{
  struct agent_event event;
  struct agent_info info;
  int i;
  char ch = 'Q';

  check(agent_watch(AGENT_EVENT_MESSAGE, "burst") == 0, "fifo watch");
  check(write(ready_fd, &ch, 1) == 1, "fifo ready");
  check(read(go_fd, &ch, 1) == 1, "fifo go");
  for (i = 0; i < LAB_EVENT_BURST; i++) {
    check(agent_wait(&event, 20) == AGENT_STATUS_OK, "fifo wait");
    check(event.corr_id == (uint64)(i + 1), "fifo order");
  }
  check(agent_info(&info) == 0, "fifo info");
  check(info.event_count == LAB_EVENT_BURST, "fifo event count");
  check(info.event_dropped == 1, "fifo dropped");
  check(agent_wait(&event, 5) == AGENT_STATUS_TIMEOUT, "fifo drained");
  printf("labbench: event_fifo queued=%d dropped=%ld ordered=1\n",
         LAB_EVENT_BURST, info.event_dropped);
  exit(0);
}

static void NOINLINE
run_fifo_sender(int target_pid, int done_fd)
{
  struct agent_event event;
  int i;
  char ch = 'F';

  check(agent_set_role(AGENT_ROLE_ORCHESTRATOR) == 0,
        "fifo sender role orchestrator");
  memset(&event, 0, sizeof(event));
  event.type = AGENT_EVENT_MESSAGE;
  strcpy(event.payload, "burst");
  for (i = 0; i < LAB_EVENT_BURST; i++) {
    event.corr_id = i + 1;
    check(agent_wake(target_pid, &event) == 0, "fifo wake");
  }
  event.corr_id = LAB_EVENT_BURST + 1;
  check(agent_wake(target_pid, &event) == AGENT_STATUS_NO_SPACE,
        "fifo overflow status");
  check(write(done_fd, &ch, 1) == 1, "fifo sender done");
  exit(0);
}

static void NOINLINE
bench_event_fifo(void)
{
  int ready[2];
  int go[2];
  int done[2];
  int pid;
  int sender_pid;
  int status;
  char ch;

  check(pipe(ready) == 0, "fifo ready pipe");
  check(pipe(go) == 0, "fifo go pipe");
  check(pipe(done) == 0, "fifo done pipe");
  pid = agent_create();
  check(pid >= 0, "fifo agent create");
  if (pid == 0) {
    close(ready[0]);
    close(go[1]);
    close(done[0]);
    close(done[1]);
    run_fifo_agent(ready[1], go[0]);
  }
  close(ready[1]);
  close(go[0]);
  check(read(ready[0], &ch, 1) == 1, "fifo ready read");
  close(ready[0]);
  sender_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
  check(sender_pid >= 0, "fifo sender create");
  if (sender_pid == 0) {
    close(go[1]);
    close(done[0]);
    run_fifo_sender(pid, done[1]);
  }
  close(done[1]);
  check(read(done[0], &ch, 1) == 1, "fifo sender done read");
  close(done[0]);
  wait(&status);
  check(status == 0, "fifo sender status");
  ch = 'G';
  check(write(go[1], &ch, 1) == 1, "fifo go write");
  close(go[1]);
  wait(&status);
  check(status == 0, "fifo agent status");
}

static void NOINLINE
run_message_full_target(int ready_fd, int go_fd)
{
  struct agent_event event;
  struct agent_info info;
  struct agent_op op;
  struct agent_result res;
  int i;
  char ch = 'M';

  check(agent_watch(AGENT_EVENT_MESSAGE, "msgfull") == 0,
        "message full watch");
  check(write(ready_fd, &ch, 1) == 1, "message full ready");
  check(read(go_fd, &ch, 1) == 1, "message full go");
  for (i = 0; i < LAB_EVENT_BURST; i++)
    check(agent_wait(&event, 20) == AGENT_STATUS_OK, "message full wait");
  check(agent_info(&info) == 0, "message full info");
  check(info.event_dropped == 1, "message full dropped");
  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_READ_MESSAGE;
  op.request_id = 810001;
  check(agent_run(&op, &res, 1, 0) == 1, "message full read mailbox");
  check(res.status == AGENT_STATUS_OK && res.value0 == 1,
        "message full mailbox valid");
  check(strcmp(res.result, "msgfull-8") == 0,
        "message full mailbox rollback preserved");
  printf("labbench: send_message_overflow queued=%d dropped=%ld rollback=1\n",
         LAB_EVENT_BURST, info.event_dropped);
  exit(0);
}

static void NOINLINE
run_message_full_sender(int target_pid, int done_fd)
{
  struct agent_op op;
  struct agent_result res;
  int i;
  char ch = 'm';

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_SEND_MESSAGE;
  op.arg0 = target_pid;
  for (i = 0; i < LAB_EVENT_BURST; i++) {
    op.request_id = 820000 + i;
    strcpy(op.payload, "msgfull-");
    op.payload[8] = '1' + i;
    op.payload[9] = 0;
    check(agent_run(&op, &res, 1, 0) == 1, "message full send");
    check(res.status == AGENT_STATUS_OK, "message full send status");
  }
  op.request_id = 820999;
  strcpy(op.payload, "msgfull-overflow");
  check(agent_run(&op, &res, 1, 0) == 1, "message overflow send");
  check(res.status == AGENT_STATUS_NO_SPACE, "message overflow no_space");
  check(write(done_fd, &ch, 1) == 1, "message sender done");
  exit(0);
}

static void NOINLINE
bench_send_message_overflow(void)
{
  int ready[2];
  int go[2];
  int done[2];
  int target_pid;
  int sender_pid;
  int status;
  char ch;

  check(pipe(ready) == 0, "message ready pipe");
  check(pipe(go) == 0, "message go pipe");
  check(pipe(done) == 0, "message done pipe");
  target_pid = agent_create();
  check(target_pid >= 0, "message target create");
  if (target_pid == 0) {
    close(ready[0]);
    close(go[1]);
    close(done[0]);
    close(done[1]);
    run_message_full_target(ready[1], go[0]);
  }
  close(ready[1]);
  close(go[0]);
  check(read(ready[0], &ch, 1) == 1, "message target ready");
  close(ready[0]);
  sender_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
  check(sender_pid >= 0, "message sender create");
  if (sender_pid == 0) {
    close(go[1]);
    close(done[0]);
    run_message_full_sender(target_pid, done[1]);
  }
  close(done[1]);
  check(read(done[0], &ch, 1) == 1, "message sender done read");
  close(done[0]);
  wait(&status);
  check(status == 0, "message sender status");
  ch = 'G';
  check(write(go[1], &ch, 1) == 1, "message go write");
  close(go[1]);
  wait(&status);
  check(status == 0, "message target status");
}

static void NOINLINE
run_file_status_partial_target(int ready_fd)
{
  struct agent_event event;
  struct agent_file_query query;
  struct agent_file_query_result *result;
  char ch = 'P';

  result = malloc(sizeof(struct agent_file_query_result));
  check(result != 0, "partial result alloc");

  check(agent_watch(AGENT_EVENT_FILE_STATUS, "stage=align") == 0,
        "partial watch");
  check(write(ready_fd, &ch, 1) == 1, "partial ready");
  check(agent_wait(&event, 20) == AGENT_STATUS_OK, "partial wait");
  check(event.type == AGENT_EVENT_FILE_STATUS, "partial event type");
  check(contains(event.payload, "fid=4"), "partial payload fid");
  check(contains(event.payload, "stage=align"), "partial payload stage");
  check(contains(event.payload, "run_id=RUN-042"), "partial payload run");
  memset(&query, 0, sizeof(query));
  query.flags = AGENT_FILE_QUERY_USE_INDEX;
  query.fid = 4;
  query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
  check(agent_file_query(&query, result) == 1, "partial fid query");
  check(strcmp(result->hits[0].physical_name, "lab_RUN042_align_err") == 0,
        "partial fid full name");
  printf("labbench: file_status_partial_payload fid=4 stage=align run_id=RUN-042 full_lookup=1\n");
  exit(0);
}

static void NOINLINE
run_file_status_partial_sender(int done_fd)
{
  struct agent_file_meta meta;
  char ch = 'p';

  check(agent_set_role(AGENT_ROLE_ORCHESTRATOR) == 0,
        "partial sender role");
  memset(&meta, 0, sizeof(meta));
  meta.fid = 4;
  strcpy(meta.status, "failed");
  check(agent_file_meta_set(&meta) == 0, "partial meta set");
  check(write(done_fd, &ch, 1) == 1, "partial sender done");
  exit(0);
}

static void NOINLINE
bench_file_status_partial_payload(void)
{
  int ready[2];
  int done[2];
  int target_pid;
  int sender_pid;
  int status;
  char ch;

  check(pipe(ready) == 0, "partial ready pipe");
  check(pipe(done) == 0, "partial done pipe");
  target_pid = agent_create();
  check(target_pid >= 0, "partial target create");
  if (target_pid == 0) {
    close(ready[0]);
    close(done[0]);
    close(done[1]);
    run_file_status_partial_target(ready[1]);
  }
  close(ready[1]);
  check(read(ready[0], &ch, 1) == 1, "partial target ready");
  close(ready[0]);
  sender_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
  check(sender_pid >= 0, "partial sender create");
  if (sender_pid == 0) {
    close(done[0]);
    run_file_status_partial_sender(done[1]);
  }
  close(done[1]);
  check(read(done[0], &ch, 1) == 1, "partial sender done read");
  close(done[0]);
  wait(&status);
  check(status == 0, "partial sender status");
  wait(&status);
  check(status == 0, "partial target status");
}

static void NOINLINE
run_file_status_full_target(int ready_fd, int go_fd)
{
  struct agent_event event;
  struct agent_info info;
  int i;
  char ch = 'F';

  check(agent_watch(AGENT_EVENT_FILE_STATUS, "status=failed") == 0,
        "file full watch");
  check(write(ready_fd, &ch, 1) == 1, "file full ready");
  check(read(go_fd, &ch, 1) == 1, "file full go");
  for (i = 0; i < LAB_EVENT_BURST; i++)
    check(agent_wait(&event, 20) == AGENT_STATUS_OK, "file full wait");
  check(agent_info(&info) == 0, "file full info");
  check(info.event_dropped == 1, "file full dropped");
  printf("labbench: file_status_overflow queued=%d dropped=%ld no_space=1\n",
         LAB_EVENT_BURST, info.event_dropped);
  exit(0);
}

static void
fill_failed_meta(struct agent_file_meta *meta, int fid)
{
  memset(meta, 0, sizeof(*meta));
  meta->fid = fid;
  strcpy(meta->physical_name, "lab_overflow_artifact");
  meta->physical_name[21] = '0' + (fid % 10);
  meta->physical_name[22] = 0;
  strcpy(meta->project, "lab-gene-x");
  strcpy(meta->workflow, "nightly-regression");
  strcpy(meta->run_id, "RUN-042");
  strcpy(meta->stage, "overflow");
  strcpy(meta->kind, "log");
  strcpy(meta->status, "failed");
  strcpy(meta->summary, "overflow event test");
  meta->dependency_mask = AGENT_DEP_REPORT;
}

static void NOINLINE
run_file_status_full_sender(int done_fd)
{
  struct agent_file_meta meta;
  int i;
  int rc;
  char ch = 'f';

  check(agent_set_role(AGENT_ROLE_ORCHESTRATOR) == 0,
        "file full sender role");
  for (i = 0; i < LAB_EVENT_BURST; i++) {
    fill_failed_meta(&meta, 80 + i);
    rc = agent_file_meta_set(&meta);
    check(rc == 0, "file full meta set");
  }
  fill_failed_meta(&meta, 80 + LAB_EVENT_BURST);
  rc = agent_file_meta_set(&meta);
  check(rc == AGENT_STATUS_NO_SPACE, "file full no_space");
  check(write(done_fd, &ch, 1) == 1, "file sender done");
  exit(0);
}

static void NOINLINE
bench_file_status_overflow(void)
{
  int ready[2];
  int go[2];
  int done[2];
  int target_pid;
  int sender_pid;
  int status;
  char ch;

  check(pipe(ready) == 0, "file ready pipe");
  check(pipe(go) == 0, "file go pipe");
  check(pipe(done) == 0, "file done pipe");
  target_pid = agent_create();
  check(target_pid >= 0, "file target create");
  if (target_pid == 0) {
    close(ready[0]);
    close(go[1]);
    close(done[0]);
    close(done[1]);
    run_file_status_full_target(ready[1], go[0]);
  }
  close(ready[1]);
  close(go[0]);
  check(read(ready[0], &ch, 1) == 1, "file target ready");
  close(ready[0]);
  sender_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
  check(sender_pid >= 0, "file sender create");
  if (sender_pid == 0) {
    close(go[1]);
    close(done[0]);
    run_file_status_full_sender(done[1]);
  }
  close(done[1]);
  check(read(done[0], &ch, 1) == 1, "file sender done read");
  close(done[0]);
  wait(&status);
  check(status == 0, "file sender status");
  ch = 'G';
  check(write(go[1], &ch, 1) == 1, "file go write");
  close(go[1]);
  wait(&status);
  check(status == 0, "file target status");
}

int
main(int argc, char *argv[])
{
  int pid;
  int status;
  int wait_ticks;
  struct agent_event event;
  struct agent_file_meta meta;

  printf("labbench: Agent-OS task4/task5 benchmark\n");
  alloc_buffers();
  memset(&event, 0, sizeof(event));
  memset(&meta, 0, sizeof(meta));
  check(agent_file_meta_init() == -1, "parent meta init rejected");
  check(agent_file_meta_set(&meta) == -1, "parent meta set rejected");
  check(agent_wake(0, &event) == -1, "parent wake rejected");
  check(agent_create_role(AGENT_ROLE_RECOVERY) < 0,
        "parent recovery create rejected");
  pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
  check(pid >= 0, "core bench agent");
  if (pid == 0) {
    run_core_bench();
    exit(0);
  }
  wait(&status);
  check(status == 0, "core bench status");

  bench_permission_denied();
  wait_ticks = bench_event_wait_wake();
  print_perf("event_wait_wake", WAIT_BENCH_OPS, wait_ticks, WAIT_BENCH_OPS,
             wait_ticks);
  bench_event_fifo();
  bench_send_message_overflow();
  bench_file_status_partial_payload();
  bench_file_status_overflow();
  printf("labbench: passed\n");
  exit(0);
}
