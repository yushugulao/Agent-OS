// SPDX-License-Identifier: Apache-2.0

#include "kernel/types.h"
#include "kernel/stat.h"
#include "kernel/riscv.h"
#include "kernel/memlayout.h"
#include "kernel/agent.h"
#include "user/user.h"

static struct agent_op ops[AGENT_BATCH_MAX];
static struct agent_result results[AGENT_BATCH_MAX];
static struct agent_context_record snapshot_records[AGENT_CONTEXT_MAX_RECORDS];

static void
check(int condition, const char *message)
{
  if (!condition) {
    printf("agentfinal: check failed: %s\n", message);
    exit(1);
  }
}

static void
fill_ops(uint64 base_request)
{
  int i;

  memset(ops, 0, sizeof(ops));
  for (i = 0; i < AGENT_BATCH_MAX; i++) {
    ops[i].version = AGENT_CALL_VERSION;
    ops[i].request_id = base_request + i;
    ops[i].arg0 = i;
    ops[i].arg1 = i + 1000;
    switch (i % 4) {
    case 0:
      ops[i].tool_id = AGENT_TOOL_ECHO;
      strcpy(ops[i].payload, "final");
      break;
    case 1:
      ops[i].tool_id = AGENT_TOOL_PID_INFO;
      break;
    case 2:
      ops[i].tool_id = AGENT_TOOL_CTX_STAT;
      break;
    default:
      ops[i].tool_id = AGENT_TOOL_READ_CONTEXT;
      break;
    }
  }
}

static void
check_batch(uint64 first_sequence)
{
  int i;

  for (i = 0; i < AGENT_BATCH_MAX; i++) {
    check(results[i].status == AGENT_STATUS_OK, "batch result status");
    check(results[i].sequence == first_sequence + i, "batch sequence");
  }
}

static void
check_tamper_protected(struct agent_info *info,
                       struct agent_context_header *header)
{
  struct agent_context_record *direct;
  uint64 slot;
  uint64 sequence;
  int tool_id;
  int status;
  int n;

  direct = (struct agent_context_record *)(info->context_base +
                                           header->records_offset);
  sequence = snapshot_records[0].sequence;
  tool_id = snapshot_records[0].tool_id;
  status = snapshot_records[0].status;
  slot = (sequence - 1) % header->capacity;

  direct[slot].sequence = 999999;
  direct[slot].tool_id = -1;
  direct[slot].status = -99;
  strcpy(direct[slot].payload, "dirty");
  strcpy(direct[slot].result, "dirty");
  check(direct[slot].sequence == 999999, "tamper direct dirty sequence");
  check(strcmp(direct[slot].payload, "dirty") == 0,
        "tamper direct dirty payload");
  printf("agentfinal: direct_dirty_before_snapshot=1\n");

  memset(header, 0, sizeof(*header));
  memset(snapshot_records, 0, sizeof(snapshot_records));
  n = context_snapshot(header, snapshot_records, AGENT_CONTEXT_MAX_RECORDS);
  check(n == AGENT_BATCH_MAX, "tamper snapshot count");
  check(snapshot_records[0].sequence == sequence, "tamper sequence");
  check(snapshot_records[0].tool_id == tool_id, "tamper tool");
  check(snapshot_records[0].status == status, "tamper status");
  check(strcmp(snapshot_records[0].payload, "final") == 0,
        "tamper payload");
  check(strcmp(snapshot_records[0].result, "final") == 0, "tamper result");
  check(direct[slot].sequence == sequence, "tamper direct sequence restore");
  check(direct[slot].tool_id == tool_id, "tamper direct tool restore");
  check(direct[slot].status == status, "tamper direct status restore");
  check(strcmp(direct[slot].payload, "final") == 0,
        "tamper direct payload restore");
  check(strcmp(direct[slot].result, "final") == 0,
        "tamper direct result restore");
  printf("agentfinal: tamper_protected=1\n");
}

static void
check_snapshot_matches_direct(struct agent_info *info,
                              struct agent_context_header *header, int n)
{
  struct agent_context_record *direct;
  uint64 slot;
  int i;

  direct = (struct agent_context_record *)(info->context_base +
                                           header->records_offset);
  for (i = 0; i < n; i++) {
    slot = (snapshot_records[i].sequence - 1) % header->capacity;
    check(direct[slot].sequence == snapshot_records[i].sequence,
          "snapshot direct sequence");
    check(direct[slot].tool_id == snapshot_records[i].tool_id,
          "snapshot direct tool");
    check(direct[slot].status == snapshot_records[i].status,
          "snapshot direct status");
    check(strcmp(direct[slot].payload, snapshot_records[i].payload) == 0,
          "snapshot direct payload");
    check(strcmp(direct[slot].result, snapshot_records[i].result) == 0,
          "snapshot direct result");
  }
}

static void
run_agent(void)
{
  struct agent_info info;
  struct agent_context_header *direct_header;
  struct agent_context_header header;
  struct agent_result *latest;
  int n;

  check(agent_info(&info) == 0, "agent_info");
  check(info.is_agent == 1, "is agent");
  check(info.context_size == AGENT_CONTEXT_SIZE, "context size");
  check(context_clear() == 0, "context_clear");

  direct_header = (struct agent_context_header *)info.context_base;
  latest = (struct agent_result *)(info.context_base +
                                   direct_header->latest_response_offset);
  check(direct_header->magic == AGENT_CONTEXT_MAGIC, "context magic");
  check(direct_header->capacity == AGENT_CONTEXT_MAX_RECORDS,
        "context capacity");
  printf("agentfinal: context size=%ld capacity=%ld\n", info.context_size,
         direct_header->capacity);

  fill_ops(1);
  check(agent_run(ops, results, AGENT_BATCH_MAX, 0) == AGENT_BATCH_MAX,
        "first batch");
  check_batch(1);
  check(latest->sequence == AGENT_BATCH_MAX, "latest after batch");
  printf("agentfinal: batch first_seq=1 last_seq=%ld\n",
         results[AGENT_BATCH_MAX - 1].sequence);

  memset(&header, 0, sizeof(header));
  memset(snapshot_records, 0, sizeof(snapshot_records));
  n = context_snapshot(&header, snapshot_records, AGENT_CONTEXT_MAX_RECORDS);
  check(n == AGENT_BATCH_MAX, "snapshot first count");
  check(header.latest_sequence == AGENT_BATCH_MAX, "snapshot first latest");
  check(snapshot_records[0].sequence == 1, "snapshot first oldest");
  check(snapshot_records[n - 1].sequence == AGENT_BATCH_MAX,
        "snapshot first latest record");
  check(strcmp(snapshot_records[0].payload, "final") == 0,
        "snapshot payload");
  check(strcmp(snapshot_records[0].result, "final") == 0, "snapshot result");
  printf("agentfinal: short_text_history=1 payload=%s result=%s\n",
         snapshot_records[0].payload, snapshot_records[0].result);
  check_snapshot_matches_direct(&info, &header, n);
  printf("agentfinal: snapshot count=%d latest=%ld\n", n,
         header.latest_sequence);
  check_tamper_protected(&info, &header);

  fill_ops(1000);
  check(agent_run(ops, results, AGENT_BATCH_MAX, 0) == AGENT_BATCH_MAX,
        "second batch");
  fill_ops(2000);
  check(agent_run(ops, results, AGENT_BATCH_MAX, 0) == AGENT_BATCH_MAX,
        "third batch");

  memset(&header, 0, sizeof(header));
  memset(snapshot_records, 0, sizeof(snapshot_records));
  n = context_snapshot(&header, snapshot_records, AGENT_CONTEXT_MAX_RECORDS);
  check(n == AGENT_CONTEXT_MAX_RECORDS, "fifo snapshot count");
  check(header.oldest_sequence == 65, "fifo oldest");
  check(header.latest_sequence == 192, "fifo latest");
  check(header.dropped_records == 64, "fifo dropped");
  check(snapshot_records[0].sequence == 65, "fifo first record");
  check(snapshot_records[n - 1].sequence == 192, "fifo last record");
  check_snapshot_matches_direct(&info, &header, n);
  printf("agentfinal: fifo oldest=%ld latest=%ld dropped=%ld\n",
         header.oldest_sequence, header.latest_sequence,
         header.dropped_records);
  printf("agentfinal: direct_context_match=1\n");
  printf("agentfinal: passed\n");
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
