// SPDX-License-Identifier: Apache-2.0

#include "kernel/types.h"
#include "kernel/stat.h"
#include "kernel/agent.h"
#include "user/user.h"

int
main(int argc, char *argv[])
{
  struct agent_info info;
  int pid;
  int status;

  if (agent_info(&info) < 0) {
    printf("agentdemo: agent_info failed for parent\n");
    exit(1);
  }

  printf("parent: pid=%d is_agent=%d agent_id=%d\n", getpid(), info.is_agent,
         info.agent_id);

  pid = agent_fork();
  if (pid < 0) {
    printf("agentdemo: agent_fork failed\n");
    exit(1);
  }

  if (pid == 0) {
    char *ctx;

    if (agent_info(&info) < 0) {
      printf("agentdemo: agent_info failed for child\n");
      exit(1);
    }

    printf("child: pid=%d is_agent=%d agent_id=%d ctx=%p size=%ld\n",
           getpid(), info.is_agent, info.agent_id, (void *)info.context_base,
           info.context_size);

    ctx = (char *)info.context_base;
    ctx[0] = 'A';
    ctx[1] = 'G';
    ctx[2] = 'T';
    ctx[3] = 0;

    printf("child: context_write=%s\n", ctx);
    exit(0);
  }

  wait(&status);
  printf("parent: agent child %d exited with status %d\n", pid, status);
  exit(0);
}
