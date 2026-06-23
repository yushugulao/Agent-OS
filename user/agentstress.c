// SPDX-License-Identifier: Apache-2.0

#include "kernel/types.h"
#include "kernel/param.h"
#include "kernel/stat.h"
#include "kernel/riscv.h"
#include "kernel/memlayout.h"
#include "kernel/agent.h"
#include "user/user.h"

#define BIG_STEP (1 << 30)

static void
check(int condition, const char *message)
{
  if (!condition) {
    printf("agentstress: check failed: %s\n", message);
    exit(1);
  }
}

static void
test_agent_exec_failure_preserves_context(void)
{
  struct agent_info info;
  struct agent_op op;
  struct agent_result result;
  struct agent_context_header header;
  struct agent_context_record record;
  char *badargv[MAXARG + 2];
  int pid;
  int status;
  int i;

  pid = agent_create();
  check(pid >= 0, "agent_create exec failure");
  if (pid == 0) {
    for (i = 0; i < MAXARG + 1; i++)
      badargv[i] = "x";
    badargv[MAXARG + 1] = 0;

    check(exec("agentexec", badargv) < 0, "oversized exec must fail");
    check(agent_info(&info) == 0, "agent_info after failed exec");
    check(info.is_agent == 1, "is agent after failed exec");
    check(info.context_base == AGENT_CONTEXT_BASE,
          "context base after failed exec");

    memset(&op, 0, sizeof(op));
    op.version = AGENT_CALL_VERSION;
    op.tool_id = AGENT_TOOL_ECHO;
    op.request_id = 1;
    strcpy(op.payload, "after-fail");
    check(agent_run(&op, &result, 1, 0) == 1, "agent_run after failed exec");
    check(result.status == AGENT_STATUS_OK, "status after failed exec");
    check(context_snapshot(&header, &record, 1) == 1,
          "snapshot after failed exec");
    check(header.latest_sequence == result.sequence,
          "snapshot latest after failed exec");
    check(record.sequence == result.sequence, "record after failed exec");
    printf("agentstress: exec_failure_preserved=1\n");
    exit(0);
  }

  wait(&status);
  check(status == 0, "exec failure child status");
}

static void
test_agent_exec_cycles(void)
{
  int i;
  int pid;
  int status;
  char *argv[] = {"agentexec", 0};

  for (i = 0; i < 4; i++) {
    pid = agent_create();
    check(pid >= 0, "agent_create exec");
    if (pid == 0) {
      exec("agentexec", argv);
      printf("agentstress: exec agentexec failed\n");
      exit(1);
    }
    wait(&status);
    check(status == 0, "agentexec status");
  }
  printf("agentstress: exec_cycles=4\n");
}

static void
test_many_create_exit(void)
{
  struct agent_info info;
  int i;
  int pid;
  int status;

  for (i = 0; i < 12; i++) {
    pid = agent_create();
    check(pid >= 0, "agent_create loop");
    if (pid == 0) {
      check(agent_info(&info) == 0, "agent_info");
      check(info.is_agent == 1, "is agent");
      check(info.context_base == AGENT_CONTEXT_BASE, "context base");
      exit(0);
    }
    wait(&status);
    check(status == 0, "create exit status");
  }
  printf("agentstress: create_exit=12\n");
}

static void
test_sbrk_boundary(void)
{
  int pid;
  int status;

  pid = agent_create();
  check(pid >= 0, "agent_create sbrk");
  if (pid == 0) {
    char *p;
    int steps = 0;

    p = sbrk(4096);
    check(p != SBRK_ERROR, "small eager sbrk");

    for (;;) {
      p = sbrklazy(BIG_STEP);
      if (p == SBRK_ERROR)
        break;
      steps++;
      if (steps > 512) {
        printf("agentstress: lazy sbrk did not hit limit\n");
        exit(1);
      }
    }

    check(steps > 0, "lazy sbrk progressed");
    printf("agentstress: sbrk_boundary_steps=%d\n", steps);
    exit(0);
  }

  wait(&status);
  check(status == 0, "sbrk child status");
}

static void
test_normal_context_fault(void)
{
  int pid;
  int status;

  pid = fork();
  check(pid >= 0, "normal fork");
  if (pid == 0) {
    volatile char *ctx = (char *)AGENT_CONTEXT_BASE;
    ctx[0] = 'X';
    exit(1);
  }

  wait(&status);
  check(status == -1, "normal context fault");
  printf("agentstress: normal_context_fault=status -1\n");
}

static void
grow_normal_above_agent_context(void)
{
  uint64 target;
  uint64 cur;
  uint64 remain;
  int step;

  target = AGENT_CONTEXT_BASE + PGSIZE;
  while ((uint64)sbrk(0) <= target) {
    cur = (uint64)sbrk(0);
    remain = target - cur + 1;
    if (remain > BIG_STEP)
      step = BIG_STEP;
    else
      step = (int)remain;
    if (step <= 0)
      step = 1;
    check(sbrklazy(step) != SBRK_ERROR, "normal lazy grow over context");
  }
}

static void
test_parent_over_context_rejected(void)
{
  int pid;
  int status;

  pid = fork();
  check(pid >= 0, "parent over context fork");
  if (pid == 0) {
    volatile char *ctx;

    grow_normal_above_agent_context();
    check(agent_create() < 0, "lazy over-context agent_create rejected");

    ctx = (volatile char *)AGENT_CONTEXT_BASE;
    ctx[0] = 'N';
    check(agent_create() < 0, "mapped over-context agent_create rejected");
    printf("agentstress: parent_over_context_rejected=1\n");
    exit(0);
  }

  wait(&status);
  check(status == 0, "parent over context status");
}

int
main(int argc, char *argv[])
{
  test_agent_exec_cycles();
  test_agent_exec_failure_preserves_context();
  test_many_create_exit();
  test_sbrk_boundary();
  test_normal_context_fault();
  test_parent_over_context_rejected();
  printf("agentstress: passed\n");
  exit(0);
}
