// SPDX-License-Identifier: Apache-2.0

#include "kernel/types.h"
#include "kernel/stat.h"
#include "kernel/agent.h"
#include "user/user.h"
#include <stdarg.h>

void vprintf(int, const char *, va_list);

static int recovery_pid;
static int investigator_pid;
static int ready_fd = -1;
static int log_lock_fd[2] = {-1, -1};

static void
locked_printf(const char *fmt, ...)
{
  va_list ap;
  char token = 'L';
  int locked = 0;

  if (log_lock_fd[0] >= 0 && log_lock_fd[1] >= 0 &&
      read(log_lock_fd[0], &token, 1) == 1)
    locked = 1;
  va_start(ap, fmt);
  vprintf(1, fmt, ap);
  va_end(ap);
  if (locked)
    write(log_lock_fd[1], &token, 1);
}

#define printf(...) locked_printf(__VA_ARGS__)

static int
starts_with(const char *text, const char *prefix)
{
  while (*prefix) {
    if (*text != *prefix)
      return 0;
    text++;
    prefix++;
  }
  return 1;
}

static void
check(int condition, const char *message)
{
  if (!condition) {
    printf("labdemo: check failed: %s\n", message);
    exit(1);
  }
}

static void
event_created(const char *role, int pid, struct agent_info *info)
{
  printf("labdemo: created %s pid=%d context=%p\n", role, pid,
         (void *)info->context_base);
  printf("agentos:event type=AGENT_CREATED role=%s pid=%d context=%p\n", role,
         pid, (void *)info->context_base);
}

static void
run_one(struct agent_op *op, struct agent_result *res, int expected,
        const char *label)
{
  check(agent_run(op, res, 1, 0) == 1, label);
  check(res->status == expected, label);
}

static void
make_op(struct agent_op *op, int tool_id, uint64 request_id, uint64 arg0,
        const char *payload)
{
  memset(op, 0, sizeof(*op));
  op->version = AGENT_CALL_VERSION;
  op->tool_id = tool_id;
  op->request_id = request_id;
  op->arg0 = arg0;
  if (payload)
    strcpy(op->payload, payload);
}

static void
run_sentinel(void)
{
  struct agent_info info;
  struct agent_event event;
  struct agent_op op;
  struct agent_result res;

  check(agent_set_role(AGENT_ROLE_SENTINEL) == 0, "sentinel role");
  check(agent_info(&info) == 0, "sentinel info");
  event_created("sentinel", getpid(), &info);
  check(agent_heartbeat(5) == 0, "sentinel heartbeat");
  check(agent_watch(AGENT_EVENT_FILE_STATUS, "status=failed") == 0,
        "sentinel watch");
  printf("labdemo: sentinel watch status=failed\n");
  printf("agentos:event type=WATCH_REGISTERED role=sentinel filter=status=failed\n");
  printf("labdemo: sentinel state=WAITING\n");
  printf("agentos:event type=AGENT_STATE role=sentinel state=WAITING\n");
  if (ready_fd >= 0)
    check(write(ready_fd, "S", 1) == 1, "sentinel ready");

  check(agent_wait(&event, 200) == AGENT_STATUS_OK, "sentinel wait");
  check(event.type == AGENT_EVENT_FILE_STATUS, "sentinel event type");
  printf("labdemo: sentinel event type=FILE_STATUS payload=%s\n",
         event.payload);
  printf("agentos:event type=AGENT_STATE role=sentinel state=RUNNING payload=%s\n",
         event.payload);

  make_op(&op, AGENT_TOOL_QUERY_FILE, 1001, 0,
          "project=lab-gene-x;run_id=RUN-042;status=failed");
  run_one(&op, &res, AGENT_STATUS_OK, "sentinel query failed");
  check(res.value0 >= 1, "failed query hit");
  printf("labdemo: query_file project=lab-gene-x run=RUN-042 status=failed hits=%ld scanned=%ld used_index=%ld first=%s\n",
         res.value0, res.value1, res.value2 & 1, res.result);
  printf("agentos:event type=TOOL_CALL role=sentinel tool=query_file status=OK seq=%ld hits=%ld used_index=%ld\n",
         res.sequence, res.value0, res.value2 & 1);

  make_op(&op, AGENT_TOOL_CAPABILITY_CHECK, 1002, AGENT_ROLE_SENTINEL,
          "rerun_stage");
  run_one(&op, &res, AGENT_STATUS_DENIED, "sentinel denied");
  printf("labdemo: unauthorized rerun by sentinel status=DENIED\n");
  printf("agentos:event type=AUDIT role=sentinel action=rerun_stage result=DENIED seq=%ld\n",
         res.sequence);

  make_op(&op, AGENT_TOOL_SEND_MESSAGE, 1003, investigator_pid,
          "investigate RUN-042 align");
  run_one(&op, &res, AGENT_STATUS_OK, "sentinel send investigator");
  printf("labdemo: send_message sentinel->investigator status=OK\n");
  printf("agentos:event type=MESSAGE from=sentinel to=investigator status=OK seq=%ld\n",
         res.sequence);
  exit(0);
}

static void
run_investigator(void)
{
  struct agent_info info;
  struct agent_event event;
  struct agent_op op;
  struct agent_result res;
  struct agent_context_header header;
  struct agent_context_record records[8];
  int n;

  check(agent_set_role(AGENT_ROLE_INVESTIGATOR) == 0, "investigator role");
  check(agent_info(&info) == 0, "investigator info");
  event_created("investigator", getpid(), &info);
  check(agent_watch(AGENT_EVENT_MESSAGE, "investigate") == 0,
        "investigator watch");
  printf("agentos:event type=WATCH_REGISTERED role=investigator filter=investigate\n");
  if (ready_fd >= 0)
    check(write(ready_fd, "I", 1) == 1, "investigator ready");
  check(agent_wait(&event, 200) == AGENT_STATUS_OK, "investigator wait");
  check(event.type == AGENT_EVENT_MESSAGE, "investigator message event");
  pause(1);

  make_op(&op, AGENT_TOOL_READ_FILE_SUMMARY, 2001, 0,
          "lab_RUN042_align_err");
  run_one(&op, &res, AGENT_STATUS_OK, "summary align err");
  check(starts_with(res.result, "memory"), "failure summary");
  printf("labdemo: investigator reason=\"%s\"\n", res.result);
  printf("agentos:event type=TOOL_CALL role=investigator tool=read_file_summary status=OK seq=%ld artifact=lab_RUN042_align_err summary=%s\n",
         res.sequence, res.result);

  make_op(&op, AGENT_TOOL_DEPENDENCY_QUERY, 2002, 0, "align");
  run_one(&op, &res, AGENT_STATUS_OK, "dependency align");
  printf("labdemo: affected stages=%s\n", res.result);
  printf("labdemo: unaffected stages=prepare\n");
  printf("agentos:event type=TOOL_CALL role=investigator tool=dependency_query status=OK seq=%ld impact=%s\n",
         res.sequence, res.result);

  n = context_snapshot(&header, records, 8);
  check(n >= 1, "investigator snapshot");
  printf("labdemo: investigator context_snapshot records=%d latest=%ld\n", n,
         header.latest_sequence);
  printf("agentos:event type=CONTEXT_SNAPSHOT role=investigator records=%d latest=%ld\n",
         n, header.latest_sequence);

  make_op(&op, AGENT_TOOL_SEND_MESSAGE, 2003, recovery_pid,
          "recover RUN-042 align");
  run_one(&op, &res, AGENT_STATUS_OK, "investigator send recovery");
  printf("labdemo: send_message investigator->recovery status=OK\n");
  printf("agentos:event type=MESSAGE from=investigator to=recovery status=OK seq=%ld\n",
         res.sequence);
  exit(0);
}

static void
run_recovery(void)
{
  struct agent_info info;
  struct agent_event event;
  struct agent_op op;
  struct agent_result res;
  struct agent_file_query query;
  struct agent_file_query_result result;

  check(agent_set_role(AGENT_ROLE_RECOVERY) == 0, "recovery role");
  check(agent_info(&info) == 0, "recovery info");
  event_created("recovery", getpid(), &info);
  check(agent_watch(AGENT_EVENT_MESSAGE, "recover") == 0, "recovery watch");
  printf("agentos:event type=WATCH_REGISTERED role=recovery filter=recover\n");
  if (ready_fd >= 0)
    check(write(ready_fd, "R", 1) == 1, "recovery ready");
  check(agent_wait(&event, 200) == AGENT_STATUS_OK, "recovery wait");
  pause(1);

  make_op(&op, AGENT_TOOL_CAPABILITY_CHECK, 3001, AGENT_ROLE_RECOVERY,
          "rerun_stage");
  run_one(&op, &res, AGENT_STATUS_OK, "recovery capability");
  printf("labdemo: recovery check capability rerun_stage=allow\n");
  printf("agentos:event type=AUDIT role=recovery action=rerun_stage result=ALLOW seq=%ld\n",
         res.sequence);

  make_op(&op, AGENT_TOOL_RERUN_STAGE, 4201, AGENT_ROLE_RECOVERY, "align");
  run_one(&op, &res, AGENT_STATUS_OK, "rerun align");
  printf("labdemo: rerun stage=align status=OK corr_id=RUN-042-align-rerun-1\n");
  printf("agentos:event type=ACTION role=recovery stage=align status=OK corr_id=RUN-042-align-rerun-1 seq=%ld\n",
         res.sequence);

  make_op(&op, AGENT_TOOL_RERUN_STAGE, 4201, AGENT_ROLE_RECOVERY, "align");
  run_one(&op, &res, AGENT_STATUS_DUPLICATE, "duplicate align");
  printf("labdemo: duplicate corr_id=RUN-042-align-rerun-1 status=DUPLICATE\n");
  printf("agentos:event type=AUDIT role=recovery action=rerun_align result=DUPLICATE seq=%ld\n",
         res.sequence);

  make_op(&op, AGENT_TOOL_RERUN_STAGE, 4202, AGENT_ROLE_RECOVERY, "report");
  run_one(&op, &res, AGENT_STATUS_OK, "rerun report");
  printf("labdemo: rerun stage=report status=OK corr_id=RUN-042-report-rerun-1\n");
  printf("labdemo: skip stage=prepare reason=unaffected\n");

  make_op(&op, AGENT_TOOL_WRITE_REPORT, 4203, AGENT_ROLE_RECOVERY,
          "RUN-042 recovery report");
  run_one(&op, &res, AGENT_STATUS_OK, "write report");
  printf("labdemo: report metadata updated artifact=lab_RUN042_recovery_report\n");
  printf("agentos:event type=REPORT role=recovery artifact=lab_RUN042_recovery_report status=OK seq=%ld llm_status=template refs=1,2\n",
         res.sequence);

  memset(&query, 0, sizeof(query));
  query.flags = AGENT_FILE_QUERY_USE_INDEX;
  query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
  strcpy(query.run_id, "RUN-042");
  strcpy(query.status, "ok");
  strcpy(query.kind, "report");
  strcpy(query.physical_name, "lab_RUN042_recovery_report");
  check(agent_file_query(&query, &result) >= 1, "final report query");
  check(result.total_hits == 1, "single recovery report");
  printf("labdemo: final report_query hits=%d used_index=%d scanned=%d\n",
         result.total_hits, result.used_index, result.scanned_records);
  printf("labdemo: final status=RECOVERED\n");
  printf("agentos:event type=FINAL status=RECOVERED\n");
  exit(0);
}

static void
inject_failure(void)
{
  struct agent_file_meta meta;

  memset(&meta, 0, sizeof(meta));
  meta.fid = 4;
  strcpy(meta.physical_name, "lab_RUN042_align_err");
  strcpy(meta.stage, "align");
  strcpy(meta.status, "failed");
  strcpy(meta.summary, "memory limit exceeded at align stage");
  meta.dependency_mask = AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE |
                         AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE;
  check(agent_file_meta_set(&meta) == 0, "inject failure");
  printf("labdemo: inject failure stage=align reason=OOM\n");
  printf("agentos:event type=INCIDENT_CREATED id=INC-RUN-042-ALIGN-OOM stage=align reason=memory_limit\n");
}

static void
run_orchestrator(void)
{
  int sentinel_pid;
  int ready_pipe[2];
  int status;
  int ok = 0;
  char ch = 'O';

  check(agent_set_role(AGENT_ROLE_ORCHESTRATOR) == 0, "orchestrator role");
  check(agent_file_meta_init() == 0, "file meta init");
  printf("agentos:event type=LAB_INIT project=lab-gene-x workflow=nightly-regression run_id=RUN-042\n");
  check(pipe(ready_pipe) == 0, "ready pipe");
  ready_fd = ready_pipe[1];

  recovery_pid = agent_create_role(AGENT_ROLE_RECOVERY);
  check(recovery_pid >= 0, "create recovery");
  if (recovery_pid == 0) {
    close(ready_pipe[0]);
    run_recovery();
  }
  check(read(ready_pipe[0], &ch, 1) == 1, "recovery ready");

  investigator_pid = agent_create_role(AGENT_ROLE_INVESTIGATOR);
  check(investigator_pid >= 0, "create investigator");
  if (investigator_pid == 0) {
    close(ready_pipe[0]);
    run_investigator();
  }
  check(read(ready_pipe[0], &ch, 1) == 1, "investigator ready");

  sentinel_pid = agent_create_role(AGENT_ROLE_SENTINEL);
  check(sentinel_pid >= 0, "create sentinel");
  if (sentinel_pid == 0) {
    close(ready_pipe[0]);
    run_sentinel();
  }
  check(read(ready_pipe[0], &ch, 1) == 1, "sentinel ready");

  close(ready_pipe[1]);
  close(ready_pipe[0]);
  inject_failure();

  while (wait(&status) > 0) {
    check(status == 0, "child status");
    ok++;
  }
  check(ok == 3, "three worker agents exited");
  exit(0);
}

int
main(int argc, char *argv[])
{
  int orchestrator_pid;
  int status;
  int ok = 0;

  check(pipe(log_lock_fd) == 0, "log lock pipe");
  check(write(log_lock_fd[1], "L", 1) == 1, "log lock token");
  printf("labdemo: Agent-OS lab recovery demo\n");
  printf("labdemo: init project=lab-gene-x run=RUN-042\n");
  orchestrator_pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
  check(orchestrator_pid >= 0, "create orchestrator");
  if (orchestrator_pid == 0) {
    run_orchestrator();
  }

  while (wait(&status) > 0) {
    check(status == 0, "child status");
    ok++;
  }
  check(ok == 1, "orchestrator exited");
  printf("labdemo: passed\n");
  exit(0);
}
