#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static struct agent_file_query scan_query;
static struct agent_file_query_result scan_result;

static int query_status_physical(const char *status, const char *name);

static void check(int ok, const char *msg)
{
	if (!ok) {
		printf("agentscan_ucore: check failed: %s\n", msg);
		exit(1);
	}
}

static void wait_for_scan(uint64 min_runs)
{
	struct agent_info info;

	for (int i = 0; i < 1000; i++) {
		check(agent_info(&info) == 0, "agent_info");
		if (info.file_scan_runs >= min_runs &&
		    info.file_scan_entries > 0 &&
		    query_status_physical("present", "usershell") >= 1)
			return;
		sleep(10);
	}
	check(0, "background scan did not finish");
}

static int query_physical(const char *name)
{
	memset(&scan_query, 0, sizeof(scan_query));
	scan_query.flags = AGENT_FILE_QUERY_SCAN;
	scan_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(scan_query.physical_name, name);
	return agent_file_query(&scan_query, &scan_result);
}

static int query_status_physical(const char *status, const char *name)
{
	memset(&scan_query, 0, sizeof(scan_query));
	scan_query.flags = AGENT_FILE_QUERY_USE_INDEX;
	scan_query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
	strcpy(scan_query.status, status);
	strcpy(scan_query.physical_name, name);
	return agent_file_query(&scan_query, &scan_result);
}

static void make_file(const char *name)
{
	int fd;
	char body[] = "auto-scan-body";

	fd = open(name, O_CREATE | O_RDWR);
	check(fd >= 0, "create file");
	check(write(fd, body, strlen(body)) == (ssize_t)strlen(body),
	      "write file");
	check(close(fd) == 0, "close file");
}

static void run_agent(void)
{
	struct agent_info before;
	struct agent_info after;
	const char *auto_name = "autoscan_ok";

	check(agent_file_meta_init() == 0, "meta init");
	check(agent_info(&before) == 0, "before info");
	wait_for_scan(before.file_scan_runs + 1);
	check(agent_info(&after) == 0, "after info");
	check(after.file_scan_runs >= before.file_scan_runs + 1,
	      "scan run count");
	check(after.file_scan_entries > before.file_scan_entries,
	      "scan entries");
	check(query_status_physical("present", "usershell") >= 1,
	      "usershell indexed");
	check(scan_result.used_index == 1, "usershell uses status index");
	check(scan_result.hits[0].dev != 0 && scan_result.hits[0].inum != 0,
	      "usershell inode");
	printf("agentscan_ucore: background_scan usershell=1 runs=%d entries=%d added=%d\n",
	       (int)after.file_scan_runs, (int)after.file_scan_entries,
	       (int)after.file_scan_added);

	make_file(auto_name);
	check(query_physical(auto_name) >= 1, "auto file query");
	check(scan_result.hits[0].size >= 14, "auto file size");
	check(scan_result.hits[0].dev != 0 && scan_result.hits[0].inum != 0,
	      "auto file inode");
	printf("agentscan_ucore: auto_file_create=1 size=%d generation=%d\n",
	       (int)scan_result.hits[0].size,
	       (int)scan_result.hits[0].fs_generation);

	check(unlink(auto_name) == 0, "unlink auto file");
	sched_yield();
	check(query_physical(auto_name) == 0, "auto file deleted");
	printf("agentscan_ucore: auto_file_delete=1\n");

	check(agent_info(&after) == 0, "final info");
	check(after.file_scan_generation > 0, "scan generation");
	printf("agentscan_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agentscan_ucore: background file scan test\n");
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create orchestrator");
	if (pid == 0)
		run_agent();
	check(waitpid(pid, &status) == pid, "wait child");
	check(status == 0, "child status");
	printf("agentscan_ucore: parent passed\n");
	return 0;
}
