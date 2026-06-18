#include "kernel/types.h"
#include "kernel/stat.h"
#include "kernel/memlayout.h"
#include "kernel/agent.h"
#include "user/user.h"

static struct agent_context_record query_out[AGENT_CONTEXT_MAX_RECORDS];

static void
check(int condition, const char *message)
{
  if (!condition) {
    printf("contexttest: check failed: %s\n", message);
    exit(1);
  }
}

static void
fill_record(struct agent_context_record *record, uint64 request_id,
            uint64 arg0)
{
  memset(record, 0, sizeof(*record));
  record->request_id = request_id;
  record->tool_id = 0;
  record->status = AGENT_STATUS_OK;
  record->arg0 = arg0;
  record->value0 = request_id;
  record->value1 = arg0;
  strcpy(record->payload, "manual-in");
  strcpy(record->result, "manual-out");
}

static void
run_agent(void)
{
  struct agent_context_record record;
  struct agent_context_header header;
  struct agent_info info;
  char sample_payload[AGENT_CONTEXT_TEXT_SIZE];
  char sample_result[AGENT_CONTEXT_TEXT_SIZE];
  int i;
  int n;

  check(agent_info(&info) == 0, "agent_info initial");
  check(info.is_agent == 1, "is agent");
  check(context_clear() == 0, "initial clear");
  check(context_rollback(1) == AGENT_STATUS_NOT_FOUND,
        "rollback empty not found");

  for (i = 1; i <= AGENT_CONTEXT_MAX_RECORDS + 2; i++) {
    fill_record(&record, i, i);
    check(context_push(&record) == 0, "context_push");
  }

  check(agent_info(&info) == 0, "agent_info after push");
  check(info.context_path_count == AGENT_CONTEXT_MAX_RECORDS, "push count");
  check(info.context_path_oldest == 3, "oldest after FIFO");
  check(info.context_path_latest == AGENT_CONTEXT_MAX_RECORDS + 2,
        "latest after FIFO");
  check(info.context_path_dropped == 2, "dropped after FIFO");

  memset(query_out, 0, sizeof(query_out));
  n = context_query(0, query_out, AGENT_CONTEXT_MAX_RECORDS);
  check(n == AGENT_CONTEXT_MAX_RECORDS, "query full count");
  check(query_out[0].sequence == 3, "query oldest sequence");
  check(query_out[n - 1].sequence == AGENT_CONTEXT_MAX_RECORDS + 2,
        "query latest sequence");
  check(strcmp(query_out[0].payload, "manual-in") == 0, "query payload");
  check(strcmp(query_out[0].result, "manual-out") == 0, "query result");

  memset(&header, 0, sizeof(header));
  memset(query_out, 0, sizeof(query_out));
  n = context_snapshot(&header, query_out, AGENT_CONTEXT_MAX_RECORDS);
  check(n == AGENT_CONTEXT_MAX_RECORDS, "snapshot full count");
  check(header.latest_sequence == AGENT_CONTEXT_MAX_RECORDS + 2,
        "snapshot latest sequence");
  check(strcmp(query_out[0].payload, "manual-in") == 0,
        "snapshot payload");
  check(strcmp(query_out[0].result, "manual-out") == 0,
        "snapshot result");
  strcpy(sample_payload, query_out[0].payload);
  strcpy(sample_result, query_out[0].result);

  check(context_rollback(1) == AGENT_STATUS_NOT_FOUND,
        "rollback evicted not found");
  check(context_rollback(AGENT_CONTEXT_MAX_RECORDS + 3) ==
          AGENT_STATUS_NOT_FOUND,
        "rollback future not found");
  check(context_rollback(10) == 0, "rollback");
  check(agent_info(&info) == 0, "agent_info after rollback");
  check(info.context_path_latest == 10, "latest after rollback");
  check(info.context_path_count == 8, "count after rollback");
  check(info.context_path_rollback_count == 1, "rollback count");

  memset(query_out, 0, sizeof(query_out));
  n = context_query(0, query_out, AGENT_CONTEXT_MAX_RECORDS);
  check(n == 8, "query count after rollback");
  check(query_out[0].sequence == 3, "rollback oldest sequence");
  check(query_out[n - 1].sequence == 10, "rollback latest sequence");

  fill_record(&record, 19, 19);
  check(context_push(&record) == 0, "push after rollback");
  check(agent_info(&info) == 0, "agent_info after branch push");
  check(info.context_path_latest == 11, "latest after branch push");
  check(info.context_path_count == 9, "count after branch push");

  memset(query_out, 0, sizeof(query_out));
  n = context_query(0, query_out, AGENT_CONTEXT_MAX_RECORDS);
  check(n == 9, "query count after branch push");
  check(query_out[n - 1].sequence == 11, "branch latest sequence");

  check(context_clear() == 0, "clear");
  check(agent_info(&info) == 0, "agent_info after clear");
  check(info.context_path_count == 0, "clear count");
  check(info.context_path_latest == 0, "clear latest");
  n = context_query(0, query_out, AGENT_CONTEXT_MAX_RECORDS);
  check(n == 0, "query after clear");

  printf("contexttest: fifo oldest=3 latest=%d dropped=2\n",
         AGENT_CONTEXT_MAX_RECORDS + 2);
  printf("contexttest: short_text_history=1 payload=%s result=%s\n",
         sample_payload, sample_result);
  printf("contexttest: rollback_not_found=%d\n", AGENT_STATUS_NOT_FOUND);
  printf("contexttest: rollback latest=10 branch_latest=11\n");
  printf("contexttest: passed\n");
}

int
main(int argc, char *argv[])
{
  struct agent_context_record record;
  int pid;
  int status;

  fill_record(&record, 1, 1);
  check(context_push(&record) < 0, "non-agent context_push rejected");

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
