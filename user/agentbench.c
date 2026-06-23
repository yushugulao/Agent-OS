// SPDX-License-Identifier: Apache-2.0

#include "kernel/types.h"
#include "kernel/stat.h"
#include "kernel/riscv.h"
#include "kernel/memlayout.h"
#include "kernel/agent.h"
#include "user/user.h"

#define BENCH_OPS 65536
#define SNAPSHOT_ROUNDS 2048
#define DIRECT_READS 1000000

static struct agent_op batch_ops[AGENT_BATCH_MAX];
static struct agent_result batch_results[AGENT_BATCH_MAX];
static struct agent_context_record snapshot_records[AGENT_CONTEXT_MAX_RECORDS];

static void
check(int condition, const char *message)
{
  if (!condition) {
    printf("agentbench: check failed: %s\n", message);
    exit(1);
  }
}

static void
fill_echo_ops(uint64 base_request)
{
  int i;

  memset(batch_ops, 0, sizeof(batch_ops));
  for (i = 0; i < AGENT_BATCH_MAX; i++) {
    batch_ops[i].version = AGENT_CALL_VERSION;
    batch_ops[i].tool_id = AGENT_TOOL_ECHO;
    batch_ops[i].request_id = base_request + i;
    batch_ops[i].arg0 = base_request + i;
    batch_ops[i].arg1 = base_request + i + 1;
    strcpy(batch_ops[i].payload, "bench");
  }
}

static int
bench_scalar_run(int ops)
{
  struct agent_op op;
  struct agent_result res;
  int start;
  int end;
  int i;

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_ECHO;
  strcpy(op.payload, "bench");

  start = uptime();
  for (i = 0; i < ops; i++) {
    op.request_id = i + 1;
    op.arg0 = i;
    op.arg1 = i + 1;
    check(agent_run(&op, &res, 1, 0) == 1, "scalar agent_run");
    check(res.status == AGENT_STATUS_OK, "scalar status");
  }
  end = uptime();
  return end - start;
}

static int
bench_batch_run(int ops)
{
  int start;
  int end;
  int i;
  int rounds;
  uint64 expect;

  rounds = ops / AGENT_BATCH_MAX;
  start = uptime();
  for (i = 0; i < rounds; i++) {
    fill_echo_ops(100000 + i * AGENT_BATCH_MAX);
    check(agent_run(batch_ops, batch_results, AGENT_BATCH_MAX, 0) ==
              AGENT_BATCH_MAX,
          "batch agent_run");
    expect = batch_results[0].sequence;
    check(batch_results[AGENT_BATCH_MAX - 1].sequence ==
              expect + AGENT_BATCH_MAX - 1,
          "batch sequence");
  }
  end = uptime();
  return end - start;
}

static int
bench_direct_reads(struct agent_info *info, int reads)
{
  volatile struct agent_context_header *header;
  volatile uint64 sink = 0;
  int start;
  int end;
  int i;

  header = (struct agent_context_header *)info->context_base;
  start = uptime();
  for (i = 0; i < reads; i++) {
    sink += header->total_calls;
    sink += header->latest_sequence;
    sink += header->dropped_records;
  }
  end = uptime();
  if (sink == 0)
    printf("agentbench: sink=%ld\n", sink);
  return end - start;
}

static int
bench_context_query(int rounds)
{
  struct agent_context_record record;
  int start;
  int end;
  int i;

  start = uptime();
  for (i = 0; i < rounds; i++)
    check(context_query(0, &record, 1) == 1, "context_query");
  end = uptime();
  return end - start;
}

static int
bench_context_snapshot(int rounds, int *records)
{
  struct agent_context_header header;
  int start;
  int end;
  int i;
  int n;

  *records = 0;
  start = uptime();
  for (i = 0; i < rounds; i++) {
    n = context_snapshot(&header, snapshot_records, AGENT_CONTEXT_MAX_RECORDS);
    check(n == AGENT_CONTEXT_MAX_RECORDS, "context_snapshot count");
    *records += n;
  }
  end = uptime();
  return end - start;
}

static void
print_perf(char *name, int ops, int ticks, int base_ops, int base_ticks)
{
  uint64 ops_per_tick;
  uint64 speedup_x100;
  uint64 calc_ticks;
  uint64 calc_base_ticks;

  calc_ticks = ticks > 0 ? ticks : 1;
  calc_base_ticks = base_ticks > 0 ? base_ticks : 1;
  ops_per_tick = ops / calc_ticks;
  speedup_x100 = ((uint64)ops * calc_base_ticks * 100) /
                 (calc_ticks * (uint64)base_ops);
  check(speedup_x100 > 0, "perf speedup overflow");
  printf("agentbench: %s ops=%d ticks=%d ops_per_tick=%ld speedup_x100=%ld\n",
         name, ops, ticks, ops_per_tick, speedup_x100);
}

static void
run_agent(void)
{
  struct agent_info info;
  int scalar_ticks;
  int batch_ticks;
  int direct_ticks;
  int query_ticks;
  int snapshot_ticks;
  int snapshot_records_count;

  check(context_clear() == 0, "clear");
  scalar_ticks = bench_scalar_run(BENCH_OPS);
  batch_ticks = bench_batch_run(BENCH_OPS);
  check(agent_info(&info) == 0, "agent_info");
  direct_ticks = bench_direct_reads(&info, DIRECT_READS);
  query_ticks = bench_context_query(SNAPSHOT_ROUNDS);
  snapshot_ticks = bench_context_snapshot(SNAPSHOT_ROUNDS,
                                          &snapshot_records_count);

  printf("agentbench: case ops ticks ops_per_tick speedup_x100\n");
  print_perf("scalar_run", BENCH_OPS, scalar_ticks, BENCH_OPS, scalar_ticks);
  print_perf("batch_run", BENCH_OPS, batch_ticks, BENCH_OPS, scalar_ticks);
  print_perf("direct_context", DIRECT_READS, direct_ticks, BENCH_OPS,
             scalar_ticks);
  print_perf("context_query", SNAPSHOT_ROUNDS, query_ticks, BENCH_OPS,
             scalar_ticks);
  print_perf("context_snapshot", snapshot_records_count, snapshot_ticks,
             SNAPSHOT_ROUNDS, query_ticks);
  printf("agentbench: latest_sequence=%ld dropped=%ld capacity=%ld\n",
         info.context_path_latest, info.context_path_dropped,
         info.context_path_capacity);
  printf("agentbench: passed\n");
}

int
main(int argc, char *argv[])
{
  int pid;
  int status;

  pid = agent_create();
  check(pid >= 0, "agent_create");
  if (pid == 0) {
    run_agent();
    exit(0);
  }

  wait(&status);
  check(status == 0, "agent child status");
  exit(0);
}
