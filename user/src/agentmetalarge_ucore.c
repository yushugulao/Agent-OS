#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define LARGE_RECORDS 32
#define REPLICATION_POLLS 1600

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agentmetalarge_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static void seed_large_bank(void)
{
	char name[8] = "lg00";
	char byte = 'L';
	struct agent_info info;
	int stable = 0;

	for (int i = 0; i < LARGE_RECORDS; i++) {
		name[2] = '0' + (i / 10);
		name[3] = '0' + (i % 10);
		int fd = open(name, O_CREATE | O_RDWR | O_TRUNC);

		check(fd >= 0, "create large-bank file");
		check(write(fd, &byte, 1) == 1, "write large-bank file");
		check(close(fd) == 0, "close large-bank file");
	}
	for (int attempt = 0; attempt < REPLICATION_POLLS; attempt++) {
		memset(&info, 0, sizeof(info));
		check(agent_info(&info) == AGENT_STATUS_OK,
		      "read metadata replication state");
		if (info.metadata_writeback_dirty != 0 &&
		    info.metadata_writeback_dirty ==
			    info.metadata_writeback_durable &&
		    info.metadata_writeback_pending == 0)
			stable++;
		else
			stable = 0;
		if (stable >= 3) {
			check(agent_file_meta_init() == AGENT_STATUS_OK,
			      "restartable reload of over-burst metadata bank");
			printf("agentmetalarge_ucore: runtime_reload_completed=1\n");
			printf("agentmetalarge_ucore: seed_ready=1 records=%d\n",
			       LARGE_RECORDS);
			for (;;)
				check(sleep(1000) == 0, "hold seeded workflow");
		}
		check(sleep(10) == 0, "wait metadata replication");
	}
	check(0, "large-bank replication deadline");
}

int main(void)
{
	int pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	int status = 0;

	check(pid >= 0, "create large-bank workflow");
	if (pid == 0)
		seed_large_bank();
	check(waitpid(pid, &status) == pid && status == 0,
	      "wait large-bank workflow");
	return 1;
}
