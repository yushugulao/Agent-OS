#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define RECOVERY_ATTEMPTS 256

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agentmetatransient_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static void run_agent(void)
{
	static struct agent_file_query query;
	static struct agent_file_query_result result;
	int found;

	memset(&query, 0, sizeof(query));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = 1;
	strcpy(query.physical_name, "metadata-reprobe-probe");
	memset(&result, 0, sizeof(result));
	found = agent_file_query(&query, &result);
	check(found == 0 && result.returned == 0,
	      "same-boot metadata recovery");
	printf("agentmetatransient_ucore: query_succeeded=1\n");
	exit(0);
}

int main(void)
{
	int unavailable_seen = 0;
	int pid = AGENT_STATUS_RETRY;
	int status = 0;

	for (int attempt = 0; attempt < RECOVERY_ATTEMPTS; attempt++) {
		pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
		if (pid >= 0)
			break;
		check(pid == AGENT_STATUS_RETRY || pid == AGENT_STATUS_IO_ERROR,
		      "fail-closed Agent admission status");
		unavailable_seen = 1;
		printf("agentmetatransient_ucore: admission_retry=%d status=%d\n",
		       attempt + 1, pid);
		check(sleep(1) == 0, "wait for bounded boot reprobe");
	}
	check(unavailable_seen, "transient Agent admission closure observed");
	check(pid >= 0, "create workflow");
	if (pid == 0) {
		printf("agentmetatransient_ucore: create_succeeded=1\n");
		run_agent();
	}
	check(waitpid(pid, &status) == pid && status == 0, "wait workflow");
	printf("agentmetatransient_ucore: unavailable_seen=1 recovered=1\n");
	printf("agentmetatransient_ucore: parent passed\n");
	return 0;
}
