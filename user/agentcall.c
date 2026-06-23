// SPDX-License-Identifier: Apache-2.0

#include "kernel/types.h"
#include "kernel/stat.h"
#include "kernel/memlayout.h"
#include "kernel/riscv.h"
#include "kernel/agent.h"
#include "user/user.h"

static struct agent_tool_desc tool_descs[AGENT_TOOL_COUNT];

static void
check(int condition, const char *message)
{
  if (!condition) {
    printf("agentcall: check failed: %s\n", message);
    exit(1);
  }
}

static void
prepare_request(struct agent_request *req, int tool_id, uint64 request_id,
                const char *tool_name, const char *payload_key,
                const char *payload, const char *arg0_key, uint64 arg0,
                const char *arg1_key, uint64 arg1)
{
  memset(req, 0, sizeof(*req));
  req->version = AGENT_CALL_VERSION;
  req->tool_id = tool_id;
  req->request_id = request_id;
  if (tool_name && tool_name[0])
    strcpy(req->tool_name, tool_name);
  if (payload_key && payload_key[0]) {
    strcpy(req->payload_key, payload_key);
    req->payload_type = AGENT_PARAM_STRING;
  }
  if (payload)
    strcpy(req->payload, payload);
  if (arg0_key && arg0_key[0]) {
    strcpy(req->arg0_key, arg0_key);
    req->arg0_type = AGENT_PARAM_UINT64;
    req->arg0 = arg0;
  }
  if (arg1_key && arg1_key[0]) {
    strcpy(req->arg1_key, arg1_key);
    req->arg1_type = AGENT_PARAM_UINT64;
    req->arg1 = arg1;
  }
}

static void
run_tool(struct agent_request *req, struct agent_response *resp, int status)
{
  memset(resp, 0, sizeof(*resp));
  check(tool_call(req, resp) == 0, "tool_call syscall");
  check(resp->status == status, "tool_call status");
}

static void *
lazy_output_page(void)
{
  uint64 cur;
  int pad;
  char *p;

  cur = (uint64)sbrk(0);
  pad = PGSIZE - (cur % PGSIZE);
  if (pad != PGSIZE)
    check(sbrklazy(pad) != SBRK_ERROR, "lazy output align");
  p = sbrklazy(PGSIZE);
  check(p != SBRK_ERROR, "lazy output page");
  return p;
}

static void
test_agent_bad_output_no_side_effect(int target_pid)
{
  struct agent_info before;
  struct agent_info after;
  struct agent_request req;

  check(agent_info(&before) == 0, "bad output before info");
  prepare_request(&req, AGENT_TOOL_SEND_MESSAGE, 9000, "send_message",
                  "message", "bad-output-message", "target_pid", target_pid,
                  0, 0);
  check(tool_call(&req, (struct agent_response *)0) < 0,
        "agent bad output rejected");
  check(agent_info(&after) == 0, "bad output after info");
  check(after.agent_call_count == before.agent_call_count,
        "bad output call count unchanged");
  check(after.context_path_count == before.context_path_count,
        "bad output context count unchanged");
  printf("agent bad_output: no_side_effect calls=%ld context=%ld\n",
         after.agent_call_count, after.context_path_count);
}

static void
test_agent_lazy_outputs(void)
{
  struct agent_request req;
  struct agent_response *lazy_resp;
  struct agent_op op;
  struct agent_result *lazy_result;

  lazy_resp = (struct agent_response *)lazy_output_page();
  prepare_request(&req, AGENT_TOOL_ECHO, 9100, "echo", "payload",
                  "lazy-legacy", "arg0", 1, "arg1", 2);
  check(tool_call(&req, lazy_resp) == 0, "lazy legacy tool_call");
  check(lazy_resp->status == AGENT_STATUS_OK, "lazy legacy status");
  check(strcmp(lazy_resp->result, "lazy-legacy") == 0,
        "lazy legacy result");

  lazy_result = (struct agent_result *)lazy_output_page();
  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = AGENT_TOOL_ECHO;
  op.request_id = 9101;
  strcpy(op.payload, "lazy-run");
  check(agent_run(&op, lazy_result, 1, 0) == 1, "lazy agent_run");
  check(lazy_result->status == AGENT_STATUS_OK, "lazy run status");
  check(strcmp(lazy_result->result, "lazy-run") == 0, "lazy run result");
  printf("agent lazy_output: legacy=1 batch=1\n");
}

static void
print_tool_list(void)
{
  int total;
  int i;

  memset(tool_descs, 0, sizeof(tool_descs));
  total = tool_list(tool_descs, AGENT_TOOL_COUNT);
  check(total == AGENT_TOOL_COUNT, "tool_list total");
  printf("tool_list: total=%d\n", total);
  for (i = 0; i < total; i++) {
    printf("tool_list[%d]: id=%d name=%s params=%s\n", i,
           tool_descs[i].tool_id, tool_descs[i].name, tool_descs[i].params);
  }
}

static void
test_parent_errors(void)
{
  struct agent_request req;
  struct agent_response resp;
  int rc;

  prepare_request(&req, AGENT_TOOL_ECHO, 100, "echo", "payload",
                  "parent-call", "arg0", 1, "arg1", 2);
  run_tool(&req, &resp, AGENT_STATUS_NOT_AGENT);
  printf("parent non_agent: status=%d result=%s\n", resp.status, resp.result);

  rc = tool_call(&req, (struct agent_response *)0);
  check(rc < 0, "bad response pointer rejected");
  printf("bad_pointer: rc=%d\n", rc);
}

static void
test_normal_context_access(void)
{
  int pid;
  int status;

  pid = fork();
  check(pid >= 0, "normal context fork");
  if (pid == 0) {
    volatile char *ctx = (char *)AGENT_CONTEXT_BASE;
    ctx[0] = 'X';
    exit(7);
  }

  wait(&status);
  printf("normal_context_access: status=%d\n", status);
  check(status == -1, "normal process cannot write Agent Context");
}

static void
test_agent_exec(void)
{
  int pid;
  int status;
  char *argv[] = {"agentexec", 0};

  pid = agent_fork();
  check(pid >= 0, "agent exec fork");
  if (pid == 0) {
    exec("agentexec", argv);
    printf("agentcall: exec agentexec failed\n");
    exit(1);
  }

  wait(&status);
  printf("agentexec status=%d\n", status);
  check(status == 0, "agent exec status");
}

static void
test_repeated_agent_create(void)
{
  struct agent_info info;
  int i;
  int pid;
  int status;

  for (i = 0; i < 3; i++) {
    pid = agent_create();
    check(pid >= 0, "agent_create");
    if (pid == 0) {
      check(agent_info(&info) == 0, "created agent_info");
      check(info.is_agent == 1, "created is agent");
      check(info.agent_type == AGENT_TYPE_AGENT, "created agent type");
      check(info.context_base == AGENT_CONTEXT_BASE, "created context base");
      check(info.context_path_capacity == AGENT_CONTEXT_MAX_RECORDS,
            "created context capacity");
      exit(0);
    }

    wait(&status);
    check(status == 0, "created agent exit status");
  }

  printf("agent_create alias: created=3 status=0\n");
}

static void
test_context_history(struct agent_info *info)
{
  struct agent_context_header *header;
  struct agent_context_record *records;
  uint64 i;

  header = (struct agent_context_header *)info->context_base;
  records = (struct agent_context_record *)(info->context_base +
                                            header->records_offset);

  check(header->magic == AGENT_CONTEXT_MAGIC, "context magic");
  check(header->count >= 8, "context has multi-round history");
  check(header->capacity == AGENT_CONTEXT_MAX_RECORDS, "context capacity");

  printf("history: count=%ld head=%ld total=%ld capacity=%ld\n", header->count,
         header->head, header->total_calls, header->capacity);
  for (i = 0; i < header->count && i < 5; i++) {
    printf("history[%ld]: seq=%ld tool_id=%d status=%d value0=%ld\n", i,
           records[i].sequence, records[i].tool_id, records[i].status,
           records[i].value0);
  }
}

static void
test_context_wrap(struct agent_info *info)
{
  struct agent_context_header *header;
  struct agent_context_record *records;
  uint64 latest_slot;

  header = (struct agent_context_header *)info->context_base;
  records = (struct agent_context_record *)(info->context_base +
                                            header->records_offset);

  check(header->magic == AGENT_CONTEXT_MAGIC, "wrap context magic");
  check(header->capacity == AGENT_CONTEXT_MAX_RECORDS, "wrap capacity");
  check(header->count == AGENT_CONTEXT_MAX_RECORDS, "wrap count");
  check(header->total_calls > AGENT_CONTEXT_MAX_RECORDS, "wrap total calls");
  check(header->head == header->total_calls % header->capacity, "wrap head");

  latest_slot = (header->head + header->capacity - 1) % header->capacity;
  check(records[latest_slot].sequence == header->total_calls,
        "wrap latest sequence");

  printf("history_wrap: count=%ld head=%ld total=%ld latest_slot=%ld latest_seq=%ld\n",
         header->count, header->head, header->total_calls, latest_slot,
         records[latest_slot].sequence);
}

static void
run_sender(int target_pid)
{
  struct agent_info info;
  struct agent_request req;
  struct agent_response resp;
  int i;

  check(agent_info(&info) == 0, "sender agent_info");
  printf("sender: pid=%d agent_id=%d type=%d quota=%d loop=%d ctx=%p\n",
         getpid(), info.agent_id, info.agent_type, info.resource_quota,
         info.loop_state, (void *)info.context_base);

  prepare_request(&req, AGENT_TOOL_ECHO, 1, "echo", "payload", "scan:sched",
                  "arg0", 7, "arg1", 11);
  run_tool(&req, &resp, AGENT_STATUS_OK);
  printf("tool echo: status=%d seq=%ld len=%ld arg0=%ld arg1=%ld result=%s\n",
         resp.status, resp.sequence, resp.value0, resp.value1, resp.value2,
         resp.result);

  prepare_request(&req, 0, 2, "pid_info", 0, "", 0, 0, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_OK);
  printf("tool pid_info: status=%d seq=%ld pid=%ld agent_id=%ld is_agent=%ld\n",
         resp.status, resp.sequence, resp.value0, resp.value1, resp.value2);

  prepare_request(&req, AGENT_TOOL_CTX_STAT, 3, "", 0, "", 0, 0, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_OK);
  printf("tool ctx_stat: status=%d seq=%ld base=%p size=%ld calls=%ld\n",
         resp.status, resp.sequence, (void *)resp.value0, resp.value1,
         resp.value2);

  prepare_request(&req, AGENT_TOOL_QUERY_PROCESS, 4, "query_process", 0, "",
                  "type", AGENT_TYPE_AGENT, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_OK);
  printf("tool query_process: status=%d seq=%ld used=%ld agents=%ld runnable=%ld\n",
         resp.status, resp.sequence, resp.value0, resp.value1, resp.value2);

  prepare_request(&req, AGENT_TOOL_GET_SYSTEM_STATUS, 5, "get_system_status",
                  0, "", 0, 0, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_OK);
  printf("tool get_system_status: status=%d seq=%ld used=%ld agents=%ld ticks=%ld\n",
         resp.status, resp.sequence, resp.value0, resp.value1, resp.value2);

  prepare_request(&req, AGENT_TOOL_QUERY_FILE, 6, "query_file", "path",
                  "README", 0, 0, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_OK);
  printf("tool query_file: status=%d seq=%ld type=%ld ino=%ld size=%ld\n",
         resp.status, resp.sequence, resp.value0, resp.value1, resp.value2);

  prepare_request(&req, AGENT_TOOL_SEND_MESSAGE, 7, "send_message", "message",
                  "hello-agent", "target_pid", target_pid, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_OK);
  printf("tool send_message: status=%d seq=%ld target=%ld from=%ld len=%ld\n",
         resp.status, resp.sequence, resp.value0, resp.value1, resp.value2);

  prepare_request(&req, AGENT_TOOL_READ_CONTEXT, 8, "read_context", 0, "", 0,
                  0, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_OK);
  check(resp.value0 == resp.sequence, "read_context post count");
  check(resp.value1 == resp.sequence % AGENT_CONTEXT_MAX_RECORDS,
        "read_context post head");
  check(resp.value2 == resp.sequence, "read_context post calls");
  printf("tool read_context: status=%d seq=%ld count=%ld head=%ld calls=%ld\n",
         resp.status, resp.sequence, resp.value0, resp.value1, resp.value2);

  prepare_request(&req, 0, 9, "missing_tool", 0, "", 0, 0, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_UNKNOWN_TOOL);
  printf("error unknown_tool: status=%d seq=%ld result=%s\n", resp.status,
         resp.sequence, resp.result);

  prepare_request(&req, AGENT_TOOL_ECHO, 10, "echo", "payload", "bad-version",
                  "arg0", 1, "arg1", 2);
  req.version = 99;
  run_tool(&req, &resp, AGENT_STATUS_BAD_REQUEST);
  printf("error bad_version: status=%d seq=%ld result=%s\n", resp.status,
         resp.sequence, resp.result);

  prepare_request(&req, AGENT_TOOL_QUERY_FILE, 11, "query_file", 0, "", 0, 0,
                  0, 0);
  run_tool(&req, &resp, AGENT_STATUS_BAD_PARAM);
  printf("error bad_param: status=%d seq=%ld result=%s\n", resp.status,
         resp.sequence, resp.result);

  prepare_request(&req, AGENT_TOOL_QUERY_FILE, 12, "query_file", "wrong",
                  "README", 0, 0, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_BAD_PARAM);
  check(strcmp(resp.result, "bad_payload_key") == 0,
        "bad payload key result");
  printf("error bad_payload_key: status=%d seq=%ld result=%s\n", resp.status,
         resp.sequence, resp.result);

  prepare_request(&req, AGENT_TOOL_QUERY_FILE, 13, "query_file", "path",
                  "README", 0, 0, 0, 0);
  req.payload_type = AGENT_PARAM_UINT64;
  run_tool(&req, &resp, AGENT_STATUS_BAD_PARAM);
  check(strcmp(resp.result, "bad_payload_type") == 0,
        "bad payload type result");
  printf("error bad_payload_type: status=%d seq=%ld result=%s\n",
         resp.status, resp.sequence, resp.result);

  prepare_request(&req, AGENT_TOOL_QUERY_FILE, 131, "query_file", "path",
                  "unknown_key=value", 0, 0, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_BAD_PARAM);
  check(strcmp(resp.result, "bad_query") == 0, "bad query key result");
  printf("error bad_query_key: status=%d seq=%ld result=%s\n", resp.status,
         resp.sequence, resp.result);

  prepare_request(&req, AGENT_TOOL_QUERY_FILE, 132, "query_file", "path",
                  "project=", 0, 0, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_BAD_PARAM);
  check(strcmp(resp.result, "bad_query") == 0, "bad query value result");
  printf("error bad_query_value: status=%d seq=%ld result=%s\n",
         resp.status, resp.sequence, resp.result);

  prepare_request(&req, 0, 14, "pid_info", 0, "", "arg0", 1, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_BAD_PARAM);
  check(strcmp(resp.result, "unexpected_param") == 0,
        "unexpected param result");
  printf("error unexpected_param: status=%d seq=%ld result=%s\n",
         resp.status, resp.sequence, resp.result);

  prepare_request(&req, AGENT_TOOL_ECHO, 15, "echo", "payload",
                  "0123456789abcdef0123456789abcdef0123456789abc", "arg0",
                  123, "arg1", 456);
  run_tool(&req, &resp, AGENT_STATUS_OK);
  check(resp.value0 == strlen(req.payload), "long payload length");
  check(strcmp(resp.result, req.payload) == 0, "long payload not truncated");
  printf("legacy long_payload: status=%d seq=%ld len=%ld result=%s\n",
         resp.status, resp.sequence, resp.value0, resp.result);

  test_agent_bad_output_no_side_effect(target_pid);
  test_agent_lazy_outputs();

  for (i = 16; i <= AGENT_CONTEXT_MAX_RECORDS + 10; i++) {
    prepare_request(&req, AGENT_TOOL_ECHO, i, "echo", "payload", "wrap",
                    "arg0", i, "arg1", i + 100);
    run_tool(&req, &resp, AGENT_STATUS_OK);
  }

  check(agent_info(&info) == 0, "sender final agent_info");
  test_context_history(&info);
  test_context_wrap(&info);
}

static void
run_receiver(int done_fd, int ready_fd)
{
  struct agent_info info;
  struct agent_request req;
  struct agent_response resp;
  char ch;

  check(agent_info(&info) == 0, "receiver agent_info");
  printf("receiver: pid=%d agent_id=%d ctx=%p capacity=%ld\n", getpid(),
         info.agent_id, (void *)info.context_base, info.context_path_capacity);

  ch = 'R';
  check(write(ready_fd, &ch, 1) == 1, "receiver ready");
  close(ready_fd);

  check(read(done_fd, &ch, 1) == 1, "receiver wait");

  prepare_request(&req, AGENT_TOOL_READ_MESSAGE, 1, "read_message", 0, "", 0,
                  0, 0, 0);
  run_tool(&req, &resp, AGENT_STATUS_OK);
  printf("receiver message: status=%d seq=%ld valid=%ld from=%ld result=%s\n",
         resp.status, resp.sequence, resp.value0, resp.value1, resp.result);
  check(resp.value0 == 1, "receiver mailbox valid");
  check(strcmp(resp.result, "hello-agent") == 0, "receiver message content");
}

int
main(int argc, char *argv[])
{
  int ready[2];
  int done[2];
  int receiver_pid;
  int sender_pid;
  int status;
  char ch;

  print_tool_list();
  test_parent_errors();
  test_normal_context_access();
  test_agent_exec();
  test_repeated_agent_create();

  check(pipe(ready) == 0, "ready pipe");
  check(pipe(done) == 0, "done pipe");

  receiver_pid = agent_fork();
  check(receiver_pid >= 0, "receiver agent_fork");
  if (receiver_pid == 0) {
    close(ready[0]);
    close(done[1]);
    run_receiver(done[0], ready[1]);
    exit(0);
  }

  close(ready[1]);
  close(done[0]);
  check(read(ready[0], &ch, 1) == 1, "parent wait receiver");

  sender_pid = agent_fork();
  check(sender_pid >= 0, "sender agent_fork");
  if (sender_pid == 0) {
    run_sender(receiver_pid);
    exit(0);
  }

  wait(&status);
  printf("parent: sender %d exited with status %d\n", sender_pid, status);
  check(status == 0, "sender status");

  ch = 'D';
  check(write(done[1], &ch, 1) == 1, "release receiver");

  wait(&status);
  printf("parent: receiver %d exited with status %d\n", receiver_pid, status);
  check(status == 0, "receiver status");

  printf("agentcall: strict validation passed\n");
  exit(0);
}
