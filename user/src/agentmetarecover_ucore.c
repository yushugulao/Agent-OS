#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define WORKFLOW_FILE "metafile"

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agentmetarecover_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static void run_read_only_probe(void)
{
	static struct agent_file_query query;
	static struct agent_file_query_result result;
	int found;

	memset(&query, 0, sizeof(query));
	memset(&result, 0, sizeof(result));
	query.flags = AGENT_FILE_QUERY_USE_INDEX;
	query.max_hits = 1;
	strcpy(query.physical_name, WORKFLOW_FILE);
	found = agent_file_query(&query, &result);
	check(found == 0 && result.returned == 0,
	      "stale workflow record is not exposed after recovery");
	printf("agentmetarecover_ucore: query_found=%d returned=%d\n",
	       found, result.returned);
	printf("agentmetarecover_ucore: readonly_recovery=1 metadata_available=1\n");
	exit(0);
}

int main(void)
{
	int pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	int status = 0;

	check(pid >= 0, "create read-only recovery workflow");
	if (pid == 0)
		run_read_only_probe();
	check(waitpid(pid, &status) == pid && status == 0,
	      "wait read-only recovery workflow");
	printf("agentmetarecover_ucore: parent passed\n");
	return 0;
}
