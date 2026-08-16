#include <agent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/*
 * Persistent native authority for the generic Host Harness.  The Host owns
 * model/provider work; this Guest owns Agent identities and every delegated
 * Task transition.  The text protocol contains only bounded scalar metadata.
 */

#define HARNESS_MAX_AGENTS 8U
#define HARNESS_MAX_TASKS AGENT_TASK_CHANNEL_CAPACITY
#define HARNESS_LINE_BYTES 640U
#define HARNESS_CHARGE_RESERVED 1U
#define HARNESS_TASK_EFFECTS \
	(AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA | \
	 AGENT_SIDE_EFFECT_IPC | AGENT_SIDE_EFFECT_PROCESS | \
	 AGENT_SIDE_EFFECT_PERMISSION | AGENT_SIDE_EFFECT_ARTIFACT)

struct harness_sq_page {
	struct agent_task_ring_header header;
	struct agent_task_sqe entries[AGENT_TASK_CHANNEL_CAPACITY];
};

struct harness_cq_page {
	struct agent_task_ring_header header;
	struct agent_task_cqe entries[AGENT_TASK_CHANNEL_CAPACITY];
};

struct harness_channel {
	volatile struct harness_sq_page *sq;
	volatile const struct harness_cq_page *cq;
	uint64 generation;
	uint64 sq_tail;
	uint64 cq_head;
};

struct harness_worker_command {
	uint64 task_id;
	int terminal_status;
	uint result_artifact_handle;
};

struct harness_worker_event {
	uint operation;
	uint host_agent_id;
	uint64 task_id;
	int status;
	int terminal_status;
	uint terminal_generation;
};

#define HARNESS_WORKER_CLAIMED 1U
#define HARNESS_WORKER_FAILED 3U

struct harness_worker {
	int active;
	uint host_agent_id;
	int pid;
	uint agent_id;
	uint64 control_id;
	int command_fd;
	int event_fd;
};

struct harness_task {
	int active;
	uint host_agent_id;
	uint node_id;
	uint64 task_id;
	struct agent_task_resource_handle resource;
	struct agent_task_sqe sqe;
};

static struct harness_worker workers[HARNESS_MAX_AGENTS];
static struct harness_task tasks[HARNESS_MAX_TASKS];
static struct harness_channel channel;
static struct agent_workflow_lifecycle_key lifecycle;
static struct agent_execution_contract_node harness_nodes[HARNESS_MAX_TASKS];
static struct agent_execution_contract_key harness_contract;
static uint harness_contract_ready;
static uint harness_nodes_used;
static uint64 next_request_id = 700000ULL;

static void fail(const char *message)
{
	printf("AGENT_HARNESS ERROR %s\n", message);
	exit(1);
}

static int read_exact(int fd, void *buffer, uint size)
{
	unsigned char *out = buffer;
	uint offset = 0;

	while (offset < size) {
		ssize_t count = read(fd, out + offset, size - offset);

		if (count <= 0)
			return -1;
		offset += (uint)count;
	}
	return 0;
}

static int write_exact(int fd, const void *buffer, uint size)
{
	const unsigned char *input = buffer;
	uint offset = 0;

	while (offset < size) {
		ssize_t count = write(fd, input + offset, size - offset);

		if (count <= 0)
			return -1;
		offset += (uint)count;
	}
	return 0;
}

static int read_line(char *line, uint capacity)
{
	uint length = 0;

	while (length + 1U < capacity) {
		char value;

		if (read(0, &value, 1) != 1)
			return -1;
		if (value == '\r')
			continue;
		if (value == '\n') {
			line[length] = 0;
			return (int)length;
		}
		line[length++] = value;
	}
	return -1;
}

static char *next_token(char **cursor)
{
	char *token;

	while (**cursor == ' ')
		(*cursor)++;
	if (**cursor == 0)
		return 0;
	token = *cursor;
	while (**cursor != 0 && **cursor != ' ')
		(*cursor)++;
	if (**cursor != 0)
		*(*cursor)++ = 0;
	return token;
}

static int parse_u64(const char *text, uint64 *value)
{
	uint64 result = 0;
	int base = 10;

	if (text == 0 || *text == 0)
		return -1;
	if (text[0] == '0' && text[1] == 'x') {
		base = 16;
		text += 2;
	}
	if (*text == 0)
		return -1;
	for (; *text != 0; text++) {
		uint digit;

		if (*text >= '0' && *text <= '9')
			digit = (uint)(*text - '0');
		else if (base == 16 && *text >= 'a' && *text <= 'f')
			digit = (uint)(*text - 'a' + 10);
		else if (base == 16 && *text >= 'A' && *text <= 'F')
			digit = (uint)(*text - 'A' + 10);
		else
			return -1;
		if (digit >= (uint)base || result > (~0ULL - digit) / (uint)base)
			return -1;
		result = result * (uint)base + digit;
	}
	*value = result;
	return 0;
}

static int parse_i32(const char *text, int *value)
{
	uint64 magnitude;
	int negative = 0;

	if (text != 0 && *text == '-') {
		negative = 1;
		text++;
	}
	if (parse_u64(text, &magnitude) < 0 || magnitude > 0x7fffffffULL)
		return -1;
	*value = negative ? -(int)magnitude : (int)magnitude;
	return 0;
}

static int parse_digest(const char *text, unsigned char digest[32])
{
	if (text == 0 || strlen(text) != 64U)
		return -1;
	for (uint index = 0; index < 32U; index++) {
		uint value = 0;

		for (uint nibble = 0; nibble < 2U; nibble++) {
			char current = text[index * 2U + nibble];
			uint digit;

			if (current >= '0' && current <= '9')
				digit = (uint)(current - '0');
			else if (current >= 'a' && current <= 'f')
				digit = (uint)(current - 'a' + 10);
			else if (current >= 'A' && current <= 'F')
				digit = (uint)(current - 'A' + 10);
			else
				return -1;
			value = value * 16U + digit;
		}
		digest[index] = (unsigned char)value;
	}
	return 0;
}

static void volatile_write(void *destination, const void *source, uint size)
{
	volatile unsigned char *out = destination;
	const unsigned char *input = source;

	for (uint index = 0; index < size; index++)
		out[index] = input[index];
	__sync_synchronize();
}

static void volatile_read(void *destination, const void *source, uint size)
{
	unsigned char *out = destination;
	const volatile unsigned char *input = source;

	__sync_synchronize();
	for (uint index = 0; index < size; index++)
		out[index] = input[index];
	__sync_synchronize();
}

static void enter_channel(uint flags, uint max_submit, uint min_complete,
			  struct agent_task_channel_enter_result *result)
{
	struct agent_task_channel_enter request;

	memset(&request, 0, sizeof(request));
	memset(result, 0, sizeof(*result));
	request.version = AGENT_TASK_CHANNEL_VERSION;
	request.size = sizeof(request);
	request.flags = flags;
	request.max_submit = max_submit;
	request.generation = channel.generation;
	request.sq_tail = channel.sq_tail;
	request.cq_head = channel.cq_head;
	request.min_complete = min_complete;
	if (agent_task_channel_enter(&request, result) != 0)
		fail("task_channel_enter");
}

static void setup_channel(void)
{
	struct agent_task_channel_setup request;
	struct agent_task_channel_setup_result result;

	memset(&request, 0, sizeof(request));
	memset(&result, 0, sizeof(result));
	request.version = AGENT_TASK_CHANNEL_VERSION;
	request.size = sizeof(request);
	request.flags = AGENT_TASK_CHANNEL_SETUP_F_SINGLE_ISSUER;
	request.lifecycle = lifecycle;
	if (agent_task_channel_setup(&request, &result) != 0 ||
	    result.status != AGENT_TASK_CHANNEL_OK || result.generation == 0)
		fail("task_channel_setup");
	channel.generation = result.generation;
	channel.sq = (volatile struct harness_sq_page *)(unsigned long)result.sq_base;
	channel.cq = (volatile const struct harness_cq_page *)(unsigned long)result.cq_base;
}

static void prime_context(void)
{
	struct agent_op operation;
	struct agent_result result;

	memset(&operation, 0, sizeof(operation));
	memset(&result, 0, sizeof(result));
	operation.version = AGENT_OP_VERSION;
	operation.tool_id = AGENT_TOOL_ECHO;
	operation.request_id = ++next_request_id;
	if (agent_run(&operation, &result, 1, 0) != 1 ||
	    result.status != AGENT_STATUS_OK || result.sequence == 0)
		fail("context_prime");
}

static void ensure_contract(void)
{
	struct agent_execution_contract_control control;
	struct agent_execution_contract_result result;

	if (harness_contract_ready)
		return;
	for (uint index = 0; index < HARNESS_MAX_TASKS; index++) {
		struct agent_execution_contract_node *node = &harness_nodes[index];

		memset(node, 0, sizeof(*node));
		node->version = AGENT_EXECUTION_CONTRACT_NODE_VERSION;
		node->size = sizeof(*node);
		node->node_id = index;
		node->tool_id = AGENT_TOOL_DELEGATE_TASK;
		node->required_capabilities = AGENT_CAP_ORCHESTRATE;
		node->accepted_input_labels = AGENT_PROVENANCE_ALL;
		node->output_add_labels = AGENT_PROVENANCE_AGENT_DERIVED;
		node->side_effect_mask = HARNESS_TASK_EFFECTS;
		node->input_artifact_type = AGENT_ARTIFACT_TASK;
		node->output_artifact_type = AGENT_ARTIFACT_NONE;
		node->max_attempts = 1;
		node->cancel_policy = AGENT_EXECUTION_CANCEL_ALLOW;
		node->charge_class = HARNESS_CHARGE_RESERVED;
	}

	memset(&control, 0, sizeof(control));
	memset(&result, 0, sizeof(result));
	control.version = AGENT_EXECUTION_CONTRACT_VERSION;
	control.size = sizeof(control);
	control.operation = AGENT_EXECUTION_CONTRACT_CREATE;
	control.flags = AGENT_EXECUTION_CONTRACT_F_ENFORCE;
	control.key.lifecycle = lifecycle;
	control.request_id = ++next_request_id;
	control.nodes = (uint64)harness_nodes;
	control.node_count = HARNESS_MAX_TASKS;
	control.node_size = sizeof(harness_nodes[0]);
	if (agent_execution_contract(&control, &result) != 0 ||
	    result.status != AGENT_STATUS_OK || result.key.generation == 0) {
		printf("AGENT_HARNESS CONTRACT status=%d nodes=%u state=%u\n",
		       result.status, result.node_count, result.state);
		fail("contract_create");
	}
	harness_contract = result.key;

	memset(&control, 0, sizeof(control));
	memset(&result, 0, sizeof(result));
	control.version = AGENT_EXECUTION_CONTRACT_VERSION;
	control.size = sizeof(control);
	control.operation = AGENT_EXECUTION_CONTRACT_QUERY;
	control.key = harness_contract;
	control.request_id = ++next_request_id;
	control.nodes = (uint64)harness_nodes;
	control.node_count = HARNESS_MAX_TASKS;
	control.node_size = sizeof(harness_nodes[0]);
	if (agent_execution_contract(&control, &result) != 0 ||
	    result.status != AGENT_STATUS_OK ||
	    result.node_count != HARNESS_MAX_TASKS)
		fail("contract_query");
	harness_contract_ready = 1;
}

static void retire_contract(const struct agent_execution_contract_key *key)
{
	struct agent_execution_contract_control control;
	struct agent_execution_contract_result result;

	for (uint attempt = 0; attempt < 256U; attempt++) {
		memset(&control, 0, sizeof(control));
		memset(&result, 0, sizeof(result));
		control.version = AGENT_EXECUTION_CONTRACT_VERSION;
		control.size = sizeof(control);
		control.operation = AGENT_EXECUTION_CONTRACT_RETIRE;
		control.key = *key;
		control.request_id = ++next_request_id;
		if (agent_execution_contract(&control, &result) == 0 &&
		    result.status == AGENT_STATUS_OK &&
		    result.state == AGENT_EXECUTION_CONTRACT_RECLAIMED)
			return;
		if (result.status != AGENT_STATUS_RETRY)
			break;
		(void)sched_yield();
	}
	fail("contract_retire");
}

static void resource_call(uint operation,
	struct agent_task_resource_handle handle, uint type, uint flags,
	uint64 source, uint64 length,
	struct agent_task_channel_resource_result *result)
{
	struct agent_task_channel_resource request;

	memset(&request, 0, sizeof(request));
	memset(result, 0, sizeof(*result));
	request.version = AGENT_TASK_CHANNEL_VERSION;
	request.size = sizeof(request);
	request.operation = operation;
	request.handle = handle;
	request.resource_type = type;
	request.resource_flags = flags;
	request.source_handle = source;
	request.length = length;
	request.channel_generation = channel.generation;
	if (agent_task_channel_resource(&request, result) != 0)
		fail("task_resource");
}

static void task_path(char path[16], uint64 task_id)
{
	static const char digits[] = "0123456789abcdef";

	memcpy(path, "hnt", 3);
	for (uint index = 0; index < 8U; index++)
		path[3U + index] = digits[(task_id >> ((7U - index) * 4U)) & 15U];
	path[11] = 0;
}

static struct agent_task_resource_handle import_descriptor(
	uint64 task_id, const struct agent_task_delegate_descriptor *descriptor)
{
	struct agent_task_channel_resource_result result;
	char path[16];
	int fd;

	task_path(path, task_id);
	(void)unlink(path);
	fd = open(path, O_CREATE | O_TRUNC | O_WRONLY);
	if (fd < 0 || write(fd, descriptor, sizeof(*descriptor)) != sizeof(*descriptor) ||
	    close(fd) != 0)
		fail("descriptor_write");
	fd = open(path, O_RDONLY);
	if (fd < 0)
		fail("descriptor_open");
	resource_call(AGENT_TASK_RESOURCE_IMPORT,
		(struct agent_task_resource_handle){ 0 }, AGENT_ARTIFACT_TASK,
		AGENT_TASK_HANDLE_F_OWNED, (uint64)(uint)fd, sizeof(*descriptor),
		&result);
	(void)close(fd);
	(void)unlink(path);
	if (result.status != AGENT_TASK_CHANNEL_OK || result.handle.slot == 0) {
		printf("AGENT_HARNESS RESOURCE status=%d state=%d slot=%d generation=%d length=%d\n",
		       result.status, result.state, result.handle.slot,
		       result.handle.generation, result.length);
		fail("descriptor_import");
	}
	return result.handle;
}

static struct harness_worker *find_worker(uint host_agent_id)
{
	for (uint index = 0; index < HARNESS_MAX_AGENTS; index++)
		if (workers[index].active && workers[index].host_agent_id == host_agent_id)
			return &workers[index];
	return 0;
}

static struct harness_task *find_task(uint64 task_id)
{
	for (uint index = 0; index < HARNESS_MAX_TASKS; index++)
		if (tasks[index].active && tasks[index].task_id == task_id)
			return &tasks[index];
	return 0;
}

static struct harness_task *allocate_task(void)
{
	if (harness_nodes_used >= HARNESS_MAX_TASKS)
		return 0;
	for (uint index = 0; index < HARNESS_MAX_TASKS; index++)
		if (!tasks[index].active) {
			memset(&tasks[index], 0, sizeof(tasks[index]));
			tasks[index].active = 1;
			tasks[index].node_id = harness_nodes_used++;
			return &tasks[index];
		}
	return 0;
}

static void provider_loop(uint host_agent_id, int command_fd, int event_fd)
{
	struct agent_task_delegate_claim request;
	struct agent_task_delegate_claim_result claim;
	struct harness_worker_command command;
	struct harness_worker_event event;

	for (;;) {
		memset(&request, 0, sizeof(request));
		request.version = AGENT_TASK_DELEGATE_VERSION;
		request.size = sizeof(request);
		request.flags = AGENT_TASK_DELEGATE_CLAIM_F_WAIT;
		request.lifecycle = lifecycle;
		memset(&claim, 0, sizeof(claim));
		if (agent_task_delegate_claim(&request, &claim) != 0) {
			printf("AGENT_HARNESS PROVIDER_CLAIM host=%u syscall=-1 result=%d\n",
			       host_agent_id, claim.status);
			exit(2);
		}
		if (claim.status == AGENT_TASK_CHANNEL_STALE ||
		    claim.status == AGENT_TASK_CHANNEL_RETRY)
			continue;
		if (claim.status != AGENT_TASK_CHANNEL_OK ||
		    claim.state != AGENT_TASK_DELEGATE_STATE_CLAIMED) {
			printf("AGENT_HARNESS PROVIDER_CLAIM host=%u syscall=0 result=%d state=%u\n",
			       host_agent_id, claim.status, claim.state);
			exit(3);
		}
		memset(&event, 0, sizeof(event));
		event.operation = HARNESS_WORKER_CLAIMED;
		event.host_agent_id = host_agent_id;
		event.task_id = claim.descriptor.task_id;
		event.status = claim.status;
		if (write_exact(event_fd, &event, sizeof(event)) < 0 ||
		    read_exact(command_fd, &command, sizeof(command)) < 0 ||
		    command.task_id != claim.descriptor.task_id)
			exit(4);
		{
			struct agent_task_delegate_complete complete;
			struct agent_task_delegate_complete_result result;

			memset(&complete, 0, sizeof(complete));
			complete.version = AGENT_TASK_DELEGATE_VERSION;
			complete.size = sizeof(complete);
			complete.lifecycle = claim.lifecycle;
			complete.owner_pid = claim.owner_pid;
			complete.terminal_status = command.terminal_status;
			complete.owner_control_id = claim.owner_control_id;
			complete.channel_generation = claim.channel_generation;
			complete.request_id = claim.request_id;
			complete.slot_generation = claim.slot_generation;
			complete.task_id = claim.descriptor.task_id;
			complete.correlation_id = claim.descriptor.correlation_id;
			for (uint attempt = 0; attempt < 8U; attempt++) {
				memset(&result, 0, sizeof(result));
				if (agent_task_delegate_complete(&complete, &result) != 0) {
					printf("AGENT_HARNESS PROVIDER_CALL syscall=-1 result=%d attempt=%u\n",
					       result.status, attempt);
					event.operation = HARNESS_WORKER_FAILED;
					event.status = -5;
					event.terminal_status = result.status;
					(void)write_exact(event_fd, &event, sizeof(event));
					exit(5);
				}
				if (result.status == AGENT_TASK_CHANNEL_OK)
					break;
				if (result.status != AGENT_TASK_CHANNEL_RETRY ||
				    result.terminal_generation == 0) {
					printf("AGENT_HARNESS PROVIDER_CALL syscall=0 result=%d terminal=%d generation=%u attempt=%u\n",
					       result.status, result.terminal_status,
					       result.terminal_generation, attempt);
					event.operation = HARNESS_WORKER_FAILED;
					event.status = -6;
					event.terminal_status = result.status;
					event.terminal_generation = result.terminal_generation;
					(void)write_exact(event_fd, &event, sizeof(event));
					exit(6);
				}
				complete.flags = AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL;
				complete.ack_terminal_status = result.terminal_status;
				complete.terminal_generation = result.terminal_generation;
			}
			if (result.status != AGENT_TASK_CHANNEL_OK) {
				event.operation = HARNESS_WORKER_FAILED;
				event.status = -7;
				event.terminal_status = result.status;
				event.terminal_generation = result.terminal_generation;
				(void)write_exact(event_fd, &event, sizeof(event));
				exit(7);
			}
		}
	}
}

static void spawn_worker(char *arguments)
{
	struct agent_runtime_config config;
	struct agent_runtime_config_result result;
	struct harness_worker *worker = 0;
	char *cursor = arguments;
	char *token;
	uint64 host_agent, capabilities, tools, resource, count, bytes, reads, summary;
	int command_pipe[2];
	int event_pipe[2];
	int pid;

	if ((token = next_token(&cursor)) == 0 || parse_u64(token, &host_agent) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &capabilities) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &tools) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &resource) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &count) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &bytes) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &reads) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &summary) < 0 ||
	    next_token(&cursor) != 0 || host_agent == 0 ||
	    find_worker((uint)host_agent) != 0)
		fail("spawn_frame");
	for (uint index = 0; index < HARNESS_MAX_AGENTS; index++)
		if (!workers[index].active) {
			worker = &workers[index];
			break;
		}
	if (worker == 0 || pipe(command_pipe) < 0 || pipe(event_pipe) < 0)
		fail("spawn_capacity");
	if (agent_scope_delegate_fd(command_pipe[0]) != AGENT_STATUS_OK ||
	    agent_scope_delegate_fd(event_pipe[1]) != AGENT_STATUS_OK)
		fail("spawn_pipe_delegate");
	memset(&config, 0, sizeof(config));
	config.version = AGENT_RUNTIME_CONFIG_VERSION;
	config.size = sizeof(config);
	config.operation = AGENT_RUNTIME_CONTROL_SPAWN;
	/* The fixed provider loop needs pipe transport in addition to the
	 * user-configured task authority. It never exposes these transport-only
	 * capabilities to the Host Agent policy. */
	config.capabilities = capabilities | AGENT_CAP_TASK_ACCEPT |
			      AGENT_CAP_CONTENT_READ | AGENT_CAP_MESSAGE_SEND;
	config.allowed_tools = tools;
	config.resource_budget = (uint)resource;
	config.artifact_count_limit = (uint)count;
	config.artifact_bytes_limit = bytes;
	config.artifact_read_limit = reads;
	config.summary_high_watermark = (uint)summary;
	memset(&result, 0, sizeof(result));
	pid = agent_runtime_control(&config, &result);
	if (pid == 0) {
		(void)close(command_pipe[1]);
		(void)close(event_pipe[0]);
		provider_loop((uint)host_agent, command_pipe[0], event_pipe[1]);
		exit(0);
	}
	if (pid < 0 || result.status != AGENT_STATUS_OK || result.control_id == 0)
		fail("spawn_runtime");
	(void)close(command_pipe[0]);
	(void)close(event_pipe[1]);
	if (agent_route_config(getpid(), pid, AGENT_IPC_ROUTE_TASK,
			       AGENT_IPC_ROUTE_GRANT) != AGENT_STATUS_OK)
		fail("spawn_route");
	memset(worker, 0, sizeof(*worker));
	worker->active = 1;
	worker->host_agent_id = (uint)host_agent;
	worker->pid = pid;
	worker->agent_id = result.agent_id;
	worker->control_id = result.control_id;
	worker->command_fd = command_pipe[1];
	worker->event_fd = event_pipe[0];
	printf("AGENT_HARNESS SPAWN %u %d %u %lu\n", worker->host_agent_id,
	       worker->pid, worker->agent_id, worker->control_id);
}

static void submit_task(char *arguments)
{
	struct agent_task_delegate_descriptor descriptor;
	struct agent_task_channel_enter_result entered;
	struct harness_worker_event event;
	struct harness_worker *worker;
	struct harness_task *task;
	char *cursor = arguments;
	char *token;
	uint64 task_id, parent_task, parent_agent, target_agent;
	uint64 objective, input, required, tools, resource, reads, deadline, result_kind;
	char *revision = 0;

	if ((token = next_token(&cursor)) == 0 || parse_u64(token, &task_id) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &parent_task) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &parent_agent) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &target_agent) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &objective) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &input) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &required) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &tools) < 0 ||
	    (revision = next_token(&cursor)) == 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &resource) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &reads) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &deadline) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &result_kind) < 0 ||
	    next_token(&cursor) != 0 || task_id == 0 || find_task(task_id) != 0)
		fail("delegate_frame");
	worker = find_worker((uint)target_agent);
	task = allocate_task();
	if (worker == 0 || task == 0)
		fail("delegate_target");
	memset(&descriptor, 0, sizeof(descriptor));
	descriptor.version = AGENT_TASK_DELEGATE_DESCRIPTOR_VERSION;
	descriptor.size = sizeof(descriptor);
	descriptor.target_pid = worker->pid;
	descriptor.target_agent_id = worker->agent_id;
	descriptor.task_type = (uint)result_kind;
	descriptor.target_control_id = worker->control_id;
	descriptor.task_id = task_id;
	descriptor.correlation_id = ++next_request_id;
	descriptor.parent_task_id = parent_task;
	descriptor.capsule_handle = (uint)objective;
	descriptor.input_artifact_handle = (uint)input;
	/* The Host Context Artifact Store validates the sealed result before it
	 * asks the native provider to complete. The Guest descriptor therefore
	 * carries the expected Host result kind as task_type and does not claim a
	 * second, file-backed Guest Artifact handle. */
	descriptor.expected_result_type = AGENT_ARTIFACT_NONE;
	descriptor.required_capabilities = required;
	descriptor.allowed_tools = tools;
	if (parse_digest(revision, descriptor.workspace_revision_sha256) < 0)
		fail("delegate_revision");
	descriptor.resource_budget = (uint)resource;
	descriptor.read_budget = (uint)reads;
	descriptor.deadline_tick = deadline;
	task->host_agent_id = (uint)target_agent;
	task->task_id = task_id;
	task->resource = import_descriptor(task_id, &descriptor);
	ensure_contract();
	memset(&task->sqe, 0, sizeof(task->sqe));
	task->sqe.version = AGENT_TASK_CHANNEL_ENTRY_VERSION;
	task->sqe.size = sizeof(task->sqe);
	task->sqe.opcode = AGENT_TASK_CHANNEL_OP_SUBMIT;
	task->sqe.flags = AGENT_TASK_SQE_F_HARD_DEADLINE;
	task->sqe.request_id = ++next_request_id;
	task->sqe.ring_generation = channel.generation;
	task->sqe.slot_generation =
		channel.sq_tail / AGENT_TASK_CHANNEL_CAPACITY + 1ULL;
	task->sqe.contract = harness_contract;
	task->sqe.node_id = task->node_id;
	task->sqe.attempt_id = 1;
	task->sqe.tool_id = AGENT_TOOL_DELEGATE_TASK;
	task->sqe.deadline_tick = deadline;
	task->sqe.input = task->resource;
	task->sqe.input.flags = AGENT_TASK_HANDLE_F_BORROWED;
	memcpy(task->sqe.schema_digest,
	       harness_nodes[task->node_id].schema_digest,
	       sizeof(task->sqe.schema_digest));
	volatile_write((void *)&channel.sq->entries[
		channel.sq_tail % AGENT_TASK_CHANNEL_CAPACITY],
		&task->sqe, sizeof(task->sqe));
	channel.sq_tail++;
	enter_channel(0, 1, 0, &entered);
	if (entered.status != AGENT_TASK_CHANNEL_OK || entered.submitted != 1)
		fail("delegate_submit");
	if (entered.cq_tail > channel.cq_head) {
		struct agent_task_cqe early;

		volatile_read(&early, (const void *)&channel.cq->entries[
			channel.cq_head % AGENT_TASK_CHANNEL_CAPACITY], sizeof(early));
		printf("AGENT_HARNESS EARLY task=%lu status=%d reason=%u request=%lu\n",
		       task_id, early.status, early.decision_reason, early.request_id);
		fail("delegate_early_terminal");
	}
	if (read_exact(worker->event_fd, &event, sizeof(event)) < 0 ||
	    event.operation != HARNESS_WORKER_CLAIMED || event.task_id != task_id)
		fail("delegate_claim");
	printf("AGENT_HARNESS CLAIM %lu %u %u\n", task_id,
	       (uint)parent_agent, (uint)target_agent);
}

static void complete_task(char *arguments)
{
	struct agent_task_channel_enter_result entered;
	struct agent_task_channel_resource_result released;
	struct harness_worker_command command;
	struct agent_task_cqe cqe;
	struct harness_task *task;
	struct harness_worker *worker;
	char *cursor = arguments;
	char *token;
	uint64 task_id, result_handle;
	int status = AGENT_STATUS_BAD_REQUEST;

	if ((token = next_token(&cursor)) == 0 || parse_u64(token, &task_id) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_i32(token, &status) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &result_handle) < 0 ||
	    next_token(&cursor) != 0)
		fail("complete_frame");
	task = find_task(task_id);
	worker = task == 0 ? 0 : find_worker(task->host_agent_id);
	if (task == 0 || worker == 0)
		fail("complete_task");
	memset(&command, 0, sizeof(command));
	command.task_id = task_id;
	command.terminal_status = status;
	command.result_artifact_handle = (uint)result_handle;
	if (write_exact(worker->command_fd, &command, sizeof(command)) < 0) {
		printf("AGENT_HARNESS PROVIDER_PIPE phase=write pid=%d\n",
		       worker->pid);
		fail("complete_provider");
	}
	for (uint attempt = 0; attempt < 2048U; attempt++) {
		enter_channel(0, 0, 0, &entered);
		if (entered.status != AGENT_TASK_CHANNEL_OK)
			fail("complete_poll");
		if (entered.cq_tail == channel.cq_head + 1ULL)
			break;
		(void)sched_yield();
	}
	if (entered.cq_tail != channel.cq_head + 1ULL)
		fail("complete_timeout");
	volatile_read(&cqe, (const void *)&channel.cq->entries[
		channel.cq_head % AGENT_TASK_CHANNEL_CAPACITY], sizeof(cqe));
	if (cqe.request_id != task->sqe.request_id ||
	    (cqe.status != command.terminal_status &&
	     cqe.status != AGENT_STATUS_TIMEOUT &&
	     cqe.status != AGENT_STATUS_CANCELLED))
		fail("complete_cqe");
	channel.cq_head++;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0, &entered);
	if (entered.status != AGENT_TASK_CHANNEL_OK)
		fail("complete_ack");
	resource_call(AGENT_TASK_RESOURCE_RELEASE, task->resource, 0, 0, 0, 0,
		      &released);
	if (released.status != AGENT_TASK_CHANNEL_OK)
		fail("complete_release");
	printf("AGENT_HARNESS COMPLETE %lu %d %u %lu\n", task_id,
	       cqe.status, cqe.flags, cqe.context_sequence);
	memset(task, 0, sizeof(*task));
}

static void run_service(void)
{
	struct agent_runtime_config query;
	struct agent_runtime_config_result self;
	struct agent_workflow_lifecycle_info info;
	char line[HARNESS_LINE_BYTES];

	memset(&query, 0, sizeof(query));
	memset(&self, 0, sizeof(self));
	query.version = AGENT_RUNTIME_CONFIG_VERSION;
	query.size = sizeof(query);
	query.operation = AGENT_RUNTIME_CONTROL_QUERY_SELF;
	if (agent_runtime_control(&query, &self) != AGENT_STATUS_OK)
		fail("runtime_self");
	memset(&info, 0, sizeof(info));
	if (agent_workflow_lifecycle_info(&info, 0) != AGENT_STATUS_OK || !info.charged)
		fail("lifecycle");
	lifecycle = info.key;
	prime_context();
	setup_channel();
	printf("AGENT_HARNESS READY %lu %lu %u %lu\n", lifecycle.id,
	       lifecycle.generation, self.agent_id, self.control_id);
	for (;;) {
		int length = read_line(line, sizeof(line));
		char *arguments;

		if (length < 0)
			fail("serial_read");
		arguments = line;
		while (*arguments != 0 && *arguments != ' ')
			arguments++;
		if (*arguments == ' ')
			*arguments++ = 0;
		else
			arguments = line + length;
		if (strcmp(line, "SPAWN") == 0)
			spawn_worker(arguments);
		else if (strcmp(line, "DELEGATE") == 0)
			submit_task(arguments);
		else if (strcmp(line, "COMPLETE") == 0)
			complete_task(arguments);
		else if (strcmp(line, "TICK") == 0) {
			struct agent_info current;

			memset(&current, 0, sizeof(current));
			if (agent_info(&current) != 0)
				fail("tick_query");
			printf("AGENT_HARNESS TICK %lu\n", current.current_tick);
		}
		else if (strcmp(line, "CLOSE") == 0) {
			for (uint index = 0; index < HARNESS_MAX_TASKS; index++)
				if (tasks[index].active)
					fail("close_tasks_active");
			if (harness_contract_ready)
				retire_contract(&harness_contract);
			printf("AGENT_HARNESS CLOSED %lu %lu\n", lifecycle.id,
			       lifecycle.generation);
			exit(0);
		} else
			fail("unknown_command");
	}
}

int main(void)
{
	int pid;
	int status = -1;

	printf("agentharness_ucore: native Task Channel authority\n");
	pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);
	if (pid < 0)
		fail("workflow_start");
	if (pid == 0)
		run_service();
	if (waitpid(pid, &status) != pid || status != 0)
		fail("workflow_wait");
	printf("agentharness_ucore: parent passed\n");
	return 0;
}
