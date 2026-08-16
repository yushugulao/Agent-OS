#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syscall.h>
#include <unistd.h>

#define ARTIFACT_HANDLE 0x6d756c7469000001ULL

static struct agent_context_header multi_header;
static struct agent_context_record multi_records[AGENT_CONTEXT_MAX_RECORDS];
static const unsigned char artifact_digest[32] = {
	0xd1, 0x9f, 0x9b, 0x28, 0xe1, 0x85, 0xdd, 0xdb,
	0xa7, 0xcb, 0x52, 0x3d, 0xd3, 0x5c, 0x79, 0x3b,
	0x8f, 0x99, 0x43, 0xb9, 0x63, 0xd7, 0xd8, 0x6b,
	0xf6, 0x37, 0x02, 0xf4, 0xb4, 0xc7, 0x38, 0x75,
};

static void check(int condition, const char *message)
{
	if (!condition) {
		printf("agentmulti_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static int bytes_equal(const unsigned char *left,
		       const unsigned char *right, unsigned int length)
{
	unsigned char difference = 0;

	for (unsigned int i = 0; i < length; i++)
		difference |= left[i] ^ right[i];
	return difference == 0;
}

static uint64 push_context(uint64 request, const char *payload)
{
	struct agent_context_record record;

	memset(&record, 0, sizeof(record));
	record.request_id = request;
	record.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	record.status = AGENT_STATUS_OK;
	strcpy(record.payload, payload);
	strcpy(record.result, "settled");
	check(context_push(&record) == AGENT_STATUS_OK, "Context push");
	check(context_snapshot(&multi_header, multi_records,
			       AGENT_CONTEXT_MAX_RECORDS) >= 0 &&
		      multi_header.visible_head_sequence != 0,
	      "Context active path snapshot");
	return multi_header.visible_head_sequence;
}

static void check_runtime_subset(
	const struct agent_runtime_config_result *self)
{
	struct agent_runtime_config config;
	struct agent_runtime_config_result result;
	struct agent_runtime_config_result child_result;
	int status = -1;
	int pid;

	memset(&config, 0, sizeof(config));
	config.version = AGENT_RUNTIME_CONFIG_VERSION;
	config.size = sizeof(config);
	config.operation = AGENT_RUNTIME_CONTROL_SPAWN;
	config.capabilities = AGENT_CAP_CONTENT_READ | AGENT_CAP_TASK_ACCEPT;
	config.allowed_tools = 0;
	config.resource_budget = 4;
	config.artifact_count_limit = 4;
	config.artifact_bytes_limit = 4096;
	config.artifact_read_limit = 8192;
	config.summary_high_watermark = 8;
	memset(&result, 0, sizeof(result));
	pid = agent_runtime_control(&config, &result);
	if (pid == 0) {
		memset(&config, 0, sizeof(config));
		config.version = AGENT_RUNTIME_CONFIG_VERSION;
		config.size = sizeof(config);
		config.operation = AGENT_RUNTIME_CONTROL_QUERY_SELF;
		memset(&child_result, 0, sizeof(child_result));
		if (agent_runtime_control(&config, &child_result) !=
			    AGENT_STATUS_OK ||
		    child_result.capabilities !=
			    (AGENT_CAP_CONTENT_READ | AGENT_CAP_TASK_ACCEPT) ||
		    child_result.allowed_tools != 0 ||
		    child_result.control_id == 0 ||
		    child_result.control_id == self->control_id)
			exit(2);
		exit(0);
	}
	check(pid > 0 && result.status == AGENT_STATUS_OK && result.pid == pid,
	      "runtime child accepted");
	check(waitpid(pid, &status) == pid && status == 0,
	      "runtime child exit");
	check(result.capabilities == config.capabilities &&
		      result.allowed_tools == 0 && result.control_id != 0 &&
		      result.control_id != self->control_id,
	      "runtime exact authority subset");
	printf("agentmulti_ucore: runtime_subset=1 generic_role_config=1\n");
}

static void check_artifact(uint64 context_sequence)
{
	static const char body[] = "context artifact body";
	struct agent_context_artifact_control control;
	struct agent_context_artifact_result result;
	int seal_status;
	int fd;

	fd = open("multiart", O_CREATE | O_RDWR | O_TRUNC);
	check(fd >= 0 && write(fd, body, sizeof(body) - 1) == sizeof(body) - 1,
	      "Artifact body file");
	memset(&control, 0, sizeof(control));
	control.version = AGENT_CONTEXT_ARTIFACT_VERSION;
	control.size = sizeof(control);
	control.operation = AGENT_CONTEXT_ARTIFACT_SEAL;
	control.flags = AGENT_CONTEXT_ARTIFACT_F_UTF8 |
			AGENT_CONTEXT_ARTIFACT_F_SHAREABLE;
	control.handle = ARTIFACT_HANDLE;
	control.source_fd = fd;
	control.kind = AGENT_CONTEXT_ARTIFACT_SUBTASK;
	control.length = sizeof(body) - 1;
	control.source_context_sequence = context_sequence;
	control.task_id = 1;
	memcpy(control.content_sha256, artifact_digest,
		sizeof(control.content_sha256));
	for (int attempt = 0; attempt < 32; attempt++) {
		seal_status = agent_context_artifact(&control, &result);
		if (seal_status != AGENT_STATUS_RETRY)
			break;
		sched_yield();
	}
	if (seal_status != AGENT_STATUS_OK)
		printf("agentmulti_ucore: artifact_seal_status=%d result=%d\n",
		       seal_status, result.status);
	check(seal_status == AGENT_STATUS_OK &&
		      result.state == AGENT_CONTEXT_ARTIFACT_STATE_SEALED &&
		      result.length == sizeof(body) - 1,
	      "Artifact seal");
	control.operation = AGENT_CONTEXT_ARTIFACT_BIND;
	check(agent_context_artifact(&control, &result) == AGENT_STATUS_OK &&
		      result.references == 1,
	      "Artifact Context bind");
	control.operation = AGENT_CONTEXT_ARTIFACT_SHARE;
	check(agent_context_artifact(&control, &result) == AGENT_STATUS_OK &&
		      (result.flags & AGENT_CONTEXT_ARTIFACT_F_SHARED) != 0,
	      "Artifact share");
	control.operation = AGENT_CONTEXT_ARTIFACT_QUERY;
	check(agent_context_artifact(&control, &result) == AGENT_STATUS_OK &&
		      bytes_equal(result.content_sha256, artifact_digest,
			  sizeof(artifact_digest)),
	      "Artifact sealed query");
	close(fd);
	unlink("multiart");
	printf("agentmulti_ucore: artifact_seal=1 bind=1 share=1 sha256=1\n");
}

static void record_signature(
	const struct agent_runtime_config_result *self,
	const struct agent_workflow_lifecycle_info *lifecycle,
	uint64 object_id, unsigned char marker, uint64 request,
	struct agent_context_prefetch_result *result)
{
	struct agent_context_prefetch_control control;
	uint64 sequence = push_context(request, "workspace-read");

	memset(&control, 0, sizeof(control));
	control.version = AGENT_CONTEXT_PREFETCH_VERSION;
	control.size = sizeof(control);
	control.operation = AGENT_CONTEXT_PREFETCH_RECORD;
	control.flags = AGENT_CONTEXT_PREFETCH_F_READ_ONLY |
			AGENT_CONTEXT_PREFETCH_F_HOST;
	control.operation_type = 1;
	control.tool_id = AGENT_TOOL_READ_WORKSPACE_FILE;
	control.result_status = AGENT_STATUS_OK;
	control.length = 64;
	control.context_sequence = sequence;
	control.cause_sequence = multi_header.current_cause_sequence;
	control.tick = request;
	control.workflow_lifecycle_id = lifecycle->key.id;
	control.workflow_lifecycle_generation = lifecycle->key.generation;
	control.branch_generation = multi_header.branch_generation;
	control.agent_id = self->agent_id;
	control.agent_control_id = self->control_id;
	control.workspace_object_id = object_id;
	memset(control.workspace_revision_sha256, marker,
	       sizeof(control.workspace_revision_sha256));
	memset(control.query_fingerprint, marker,
	       sizeof(control.query_fingerprint));
	check(agent_context_prefetch(&control, result) == AGENT_STATUS_OK,
	      "prefetch training record");
}

static void check_prefetch(
	const struct agent_runtime_config_result *self,
	const struct agent_workflow_lifecycle_info *lifecycle)
{
	struct agent_context_prefetch_control control;
	struct agent_context_prefetch_result result;
	struct agent_event event;

	memset(&control, 0, sizeof(control));
	control.version = AGENT_CONTEXT_PREFETCH_VERSION;
	control.size = sizeof(control);
	control.operation = AGENT_CONTEXT_PREFETCH_CONFIGURE;
	control.policy = AGENT_CONTEXT_PREFETCH_POLICY_TRANSITION;
	control.min_observations = 2;
	control.confidence_threshold_ppm = 750000;
	control.max_prefetch_bytes = 1024;
	control.max_inflight = 1;
	check(agent_context_prefetch(&control, &result) == AGENT_STATUS_OK,
	      "prefetch configure");
	record_signature(self, lifecycle, 101, 0xa1, 10, &result);
	record_signature(self, lifecycle, 202, 0xb2, 11, &result);
	record_signature(self, lifecycle, 101, 0xa1, 12, &result);
	record_signature(self, lifecycle, 202, 0xb2, 13, &result);
	record_signature(self, lifecycle, 101, 0xa1, 14, &result);
	check(result.predicted == 1 && result.observations == 2 &&
		      result.confidence_ppm == AGENT_CONTEXT_PREFETCH_CONFIDENCE_SCALE &&
		      result.target_workspace_object_id == 202,
	      "prefetch A to B prediction");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 4) == AGENT_STATUS_OK &&
		      event.type == AGENT_EVENT_PREFETCH_HINT,
	      "Host prefetch hint event");
	record_signature(self, lifecycle, 202, 0xb2, 15, &result);
	memset(&control, 0, sizeof(control));
	control.version = AGENT_CONTEXT_PREFETCH_VERSION;
	control.size = sizeof(control);
	control.operation = AGENT_CONTEXT_PREFETCH_STATUS;
	check(agent_context_prefetch(&control, &result) == AGENT_STATUS_OK &&
		      result.hits == 1 && result.misses == 0,
	      "prefetch hit accounting");
	printf("agentmulti_ucore: prefetch observations=2 predicted=1 "
	       "hint=1 hit=1\n");
}

static void run_agent(void)
{
	struct agent_runtime_config query;
	struct agent_runtime_config_result self;
	struct agent_workflow_lifecycle_info lifecycle;
	uint64 sequence;

	memset(&query, 0, sizeof(query));
	query.version = AGENT_RUNTIME_CONFIG_VERSION;
	query.size = sizeof(query);
	query.operation = AGENT_RUNTIME_CONTROL_QUERY_SELF;
	memset(&self, 0, sizeof(self));
	check(agent_runtime_control(&query, &self) == AGENT_STATUS_OK &&
		      self.agent_id != 0 && self.control_id != 0,
	      "runtime self query");
	memset(&lifecycle, 0, sizeof(lifecycle));
	check(agent_workflow_lifecycle_info(&lifecycle, 0) ==
		      AGENT_STATUS_OK && lifecycle.charged == 1,
	      "workflow lifecycle");
	check_runtime_subset(&self);
	sequence = push_context(1, "artifact-source");
	check_artifact(sequence);
	check_prefetch(&self, &lifecycle);
	printf("agentmulti_ucore: passed\n");
	exit(0);
}

int main(void)
{
	int pid;
	int status = -1;

	printf("agentmulti_ucore: generic multi-Agent primitives test\n");
	pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create workflow Agent");
	if (pid == 0)
		run_agent();
	check(waitpid(pid, &status) == pid && status == 0,
	      "wait workflow Agent");
	printf("agentmulti_ucore: parent passed\n");
	return 0;
}
