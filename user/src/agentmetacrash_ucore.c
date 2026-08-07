#include <agent.h>
#include <agent_metadata_test_abi.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define WORKFLOW_FILE "metafile"
#define BASELINE_STATUS "baseline"
#define UPDATED_STATUS "updated"
#define REPLICATION_POLL_LIMIT 1600
#define REPLICATION_STABLE_POLLS 3
#define REPLICATION_POLL_MSEC 10

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agentmetacrash_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static void make_workflow_file(void)
{
	char byte = 'm';
	int fd = open(WORKFLOW_FILE, O_CREATE | O_RDWR | O_TRUNC);

	check(fd >= 0, "create workflow file");
	check(write(fd, &byte, 1) == 1, "write workflow file");
	check(close(fd) == 0, "close workflow file");
}

static void fill_metadata(struct agent_file_meta *meta, const char *status,
			  uint64 update_mask)
{
	memset(meta, 0, sizeof(*meta));
	meta->fid = 1;
	strcpy(meta->physical_name, WORKFLOW_FILE);
	strcpy(meta->logical_path, "/recovery/object");
	strcpy(meta->project, "recovery");
	strcpy(meta->workflow, "powercut");
	strcpy(meta->run_id, "BOOT");
	strcpy(meta->stage, "persist");
	strcpy(meta->kind, "artifact");
	strcpy(meta->status, status);
	meta->flags = AGENT_FILE_META_F_PERSIST;
	meta->update_mask = update_mask;
}

static void wait_for_baseline_replication(void)
{
	struct agent_info info;
	int stable = 0;

	for (int attempt = 0; attempt < REPLICATION_POLL_LIMIT; attempt++) {
		memset(&info, 0, sizeof(info));
		check(agent_info(&info) == AGENT_STATUS_OK,
		      "read metadata writeback state");
		if (info.metadata_writeback_dirty != 0 &&
		    info.metadata_writeback_dirty ==
			    info.metadata_writeback_durable &&
		    info.metadata_writeback_pending == 0)
			stable++;
		else
			stable = 0;
		/* durable == dirty 后的稳定空闲区间包含镜像提交。 */
		if (stable >= REPLICATION_STABLE_POLLS) {
			printf("agentmetacrash_ucore: baseline_dirty=%p baseline_durable=%p pending=%d\n",
			       info.metadata_writeback_dirty,
			       info.metadata_writeback_durable,
			       (int)info.metadata_writeback_pending);
			printf("agentmetacrash_ucore: baseline_ready=1 replicated=1\n");
			return;
		}
		check(sleep(REPLICATION_POLL_MSEC) == 0,
		      "wait for baseline mirror");
	}
	printf("agentmetacrash_ucore: baseline_timeout dirty=%p durable=%p pending=%d commits=%d\n",
	       info.metadata_writeback_dirty,
	       info.metadata_writeback_durable,
	       (int)info.metadata_writeback_pending,
	       (int)info.metadata_writeback_commits);
	check(0, "baseline mirror deadline");
}

static void run_workflow(void)
{
	struct agent_file_meta meta;
	struct agent_metadata_test_arm arm;
	int status;

	make_workflow_file();
	fill_metadata(&meta, BASELINE_STATUS, 0);
	check(agent_file_meta_set(&meta) == AGENT_STATUS_OK,
	      "commit baseline metadata");
	wait_for_baseline_replication();
	memset(&arm, 0, sizeof(arm));
	arm.version = AGENT_METADATA_TEST_ABI_VERSION;
	check(agent_metadata_test_arm_next(&arm) == 0,
	      "arm explicit metadata transaction");
	check(arm.version == AGENT_METADATA_TEST_ABI_VERSION &&
	      arm.flags == AGENT_METADATA_TEST_F_ARMED && arm.scope_id != 0 &&
	      arm.lifecycle_id != 0 && arm.lifecycle_generation != 0 &&
	      arm.baseline_generation != 0 &&
	      arm.target_generation == arm.baseline_generation + 1 &&
	      arm.arm_token != 0,
	      "validate explicit metadata target");

	/* 测试内核钩子仅中断回执对应的代次。 */
	fill_metadata(&meta, UPDATED_STATUS, AGENT_FILE_META_UPDATE_STATUS);
	status = agent_file_meta_set(&meta);
	printf("agentmetacrash_ucore: unexpected_update_return=%d\n", status);
	check(0, "power-cut checkpoint was not reached");
}

int main(void)
{
	int pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	int status = 0;

	check(pid >= 0, "create metadata workflow");
	if (pid == 0)
		run_workflow();
	check(waitpid(pid, &status) == pid && status == 0,
	      "wait metadata workflow");
	check(0, "crash workflow returned to parent");
	return 1;
}
