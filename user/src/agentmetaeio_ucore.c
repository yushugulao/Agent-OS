#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agentmetaeio_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static void run_agent(void)
{
	struct agent_file_meta meta;
	int fd = open("metaeio", O_CREATE | O_RDWR | O_TRUNC);
	int status;

	check(fd >= 0, "create file");
	check(close(fd) == 0, "close file");
	memset(&meta, 0, sizeof(meta));
	meta.fid = 1;
	strcpy(meta.physical_name, "metaeio");
	strcpy(meta.logical_path, "/recovery/eio");
	strcpy(meta.stage, "commit");
	strcpy(meta.status, "pending");
	meta.flags = AGENT_FILE_META_F_PERSIST;
	status = agent_file_meta_set(&meta);
	check(status == AGENT_STATUS_INDETERMINATE,
	      "published EIO is indeterminate");
	strcpy(meta.status, "retry");
	meta.update_mask = AGENT_FILE_META_UPDATE_STATUS;
	for (int attempt = 0; attempt < 64; attempt++) {
		status = agent_file_meta_set(&meta);
		if (status == AGENT_STATUS_OK)
			break;
		check(status == AGENT_STATUS_RETRY ||
		      status == AGENT_STATUS_IO_ERROR,
		      "repair exposes only retryable status");
		check(sleep(1) == 0, "wait for protected repair");
	}
	check(status == AGENT_STATUS_OK,
	      "single-device EIO repairs from verified peer");
	printf("agentmetaeio_ucore: transient_eio_repaired=1\n");
	exit(0);
}

int main(void)
{
	int pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	int status = 0;

	check(pid >= 0, "create workflow");
	if (pid == 0)
		run_agent();
	check(waitpid(pid, &status) == pid && status == 0, "wait workflow");
	printf("agentmetaeio_ucore: parent passed\n");
	return 0;
}
