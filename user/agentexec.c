// SPDX-License-Identifier: Apache-2.0

#include "kernel/types.h"
#include "kernel/stat.h"
#include "kernel/agent.h"
#include "user/user.h"

static void
run_agent_exec_check(void)
{
  struct agent_info info;
  struct agent_context_header *header;
  struct agent_request req;
  struct agent_response resp;

  if (agent_info(&info) < 0) {
    printf("agentexec: agent_info failed\n");
    exit(1);
  }

  if (info.is_agent != 1 || info.agent_type != AGENT_TYPE_AGENT ||
      info.resource_quota != AGENT_DEFAULT_RESOURCE_QUOTA ||
      info.loop_state != AGENT_LOOP_IDLE) {
    printf("agentexec: metadata mismatch is_agent=%d type=%d quota=%d loop=%d\n",
           info.is_agent, info.agent_type, info.resource_quota,
           info.loop_state);
    exit(1);
  }

  header = (struct agent_context_header *)info.context_base;
  if (header->magic != AGENT_CONTEXT_MAGIC ||
      header->capacity != AGENT_CONTEXT_MAX_RECORDS) {
    printf("agentexec: context header mismatch magic=%ld capacity=%ld\n",
           header->magic, header->capacity);
    exit(1);
  }

  memset(&req, 0, sizeof(req));
  req.version = AGENT_CALL_VERSION;
  req.tool_id = AGENT_TOOL_PID_INFO;
  req.request_id = 1;
  strcpy(req.tool_name, "pid_info");

  memset(&resp, 0, sizeof(resp));
  if (tool_call(&req, &resp) < 0 || resp.status != AGENT_STATUS_OK) {
    printf("agentexec: tool_call failed status=%d\n", resp.status);
    exit(1);
  }

  printf("agentexec: pid=%d agent_id=%d ctx_magic=%ld tool=%s seq=%ld\n",
         getpid(), info.agent_id, header->magic, resp.tool_name,
         resp.sequence);
}

int
main(int argc, char *argv[])
{
  struct agent_info info;
  int pid;
  int status;
  char *child_argv[] = {"agentexec", 0};

  if (agent_info(&info) == 0 && info.is_agent == 0) {
    pid = agent_create();
    if (pid < 0) {
      printf("agentexec: wrapper agent_create failed\n");
      exit(1);
    }
    if (pid == 0) {
      exec("agentexec", child_argv);
      printf("agentexec: wrapper child exec failed\n");
      exit(1);
    }
    wait(&status);
    printf("agentexec: wrapper status=%d\n", status);
    if (status != 0)
      exit(1);
    exit(0);
  }

  run_agent_exec_check();
  exit(0);
}
