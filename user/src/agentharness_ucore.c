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

#define HARNESS_MAX_AGENTS 7U
#define HARNESS_MAX_TASKS AGENT_TASK_CHANNEL_CAPACITY
#define HARNESS_CONTRACT_NODES AGENT_EXECUTION_CONTRACT_MAX_NODES
#define HARNESS_LINE_BYTES 640U
#define HARNESS_CHARGE_RESERVED 1U
#define HARNESS_CATALOG_MAX AGENT_FILE_QUERY_MAX_HITS
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
	uint operation;
	uint reserved;
	uint64 task_id;
	int terminal_status;
	uint result_artifact_handle;
	uint artifact_handle;
	uint artifact_kind;
	uint artifact_flags;
	uint tool_id;
	uint chunk_length;
	uint64 artifact_length;
	uint64 artifact_offset;
	uint64 host_context_sequence;
	uint64 cause_sequence;
	unsigned char content_sha256[32];
	union {
		unsigned char chunk[240];
		struct {
			uint index;
			uint count;
			uint64 size;
			unsigned char revision[32];
			char stage[AGENT_FILE_FIELD_SIZE];
			char kind[AGENT_FILE_FIELD_SIZE];
			char summary[AGENT_FILE_SUMMARY_SIZE];
		} catalog_entry;
		struct {
			char stage[AGENT_FILE_FIELD_SIZE];
			char kind[AGENT_FILE_FIELD_SIZE];
			char status[AGENT_FILE_FIELD_SIZE];
			char summary[AGENT_FILE_SUMMARY_SIZE];
		} catalog_query;
		struct {
			struct agent_task_delegate_descriptor descriptor;
			struct agent_execution_contract_key contract;
			uint node_id;
			unsigned char schema_digest[AGENT_EXECUTION_DIGEST_SIZE];
		} submit;
	} payload;
};

struct harness_worker_event {
	uint operation;
	uint host_agent_id;
	uint64 task_id;
	int status;
	int terminal_status;
	uint terminal_generation;
	uint artifact_handle;
	uint artifact_kind;
	uint artifact_flags;
	uint producer_agent_id;
	uint64 artifact_length;
	uint64 context_sequence;
	uint64 producer_control_id;
	unsigned char content_sha256[32];
	uint catalog_reuse;
	uint catalog_records;
	uint catalog_candidates;
	uint catalog_used_index;
	uint catalog_mask;
	uint catalog_watch_events;
	uint64 catalog_fs_generation;
	uint64 catalog_dev[HARNESS_CATALOG_MAX];
	uint64 catalog_inum[HARNESS_CATALOG_MAX];
	uint64 catalog_incarnation[HARNESS_CATALOG_MAX];
	uint64 wait_sleep_count;
	uint64 wait_wakeup_count;
	uint64 last_heartbeat_tick;
	uint64 channel_generation;
	uint64 request_id;
	uint64 slot_generation;
};

#define HARNESS_WORKER_CLAIMED 1U
#define HARNESS_WORKER_ARTIFACT 2U
#define HARNESS_WORKER_FAILED 3U
#define HARNESS_WORKER_SUBMITTED 4U
#define HARNESS_WORKER_COMPLETED 5U
#define HARNESS_WORKER_CATALOG 6U

#define HARNESS_COMMAND_COMPLETE       1U
#define HARNESS_COMMAND_ARTIFACT_BEGIN 2U
#define HARNESS_COMMAND_ARTIFACT_CHUNK 3U
#define HARNESS_COMMAND_ARTIFACT_SEAL  4U
#define HARNESS_COMMAND_ARTIFACT_BIND  5U
#define HARNESS_COMMAND_SUBMIT_TASK    6U
#define HARNESS_COMMAND_COLLECT_TASK   7U
#define HARNESS_COMMAND_CATALOG_BEGIN  8U
#define HARNESS_COMMAND_CATALOG_ENTRY  9U
#define HARNESS_COMMAND_CATALOG_COMMIT 10U
#define HARNESS_COMMAND_CATALOG_QUERY  11U
#define HARNESS_COMMAND_CATALOG_STALE  12U

struct harness_worker {
	int active;
	uint host_agent_id;
	int pid;
	uint agent_id;
	uint64 control_id;
	uint64 wait_sleep_count;
	uint64 wait_wakeup_count;
	uint64 last_heartbeat_tick;
	int command_fd;
	int event_fd;
};

struct harness_task {
	int active;
	uint owner_host_agent_id;
	uint host_agent_id;
	uint tool_id;
	uint node_id;
	uint owner_managed;
	uint64 task_id;
	uint64 correlation_id;
	struct agent_task_resource_handle resource;
	struct agent_task_sqe sqe;
};

struct harness_catalog_entry {
	int received;
	uint64 size;
	unsigned char object_id[32];
	unsigned char revision[32];
	char stage[AGENT_FILE_FIELD_SIZE];
	char kind[AGENT_FILE_FIELD_SIZE];
	char summary[AGENT_FILE_SUMMARY_SIZE];
};

struct harness_catalog_state {
	int initialized;
	int building;
	int window_valid;
	uint host_agent_id;
	uint count;
	uint previous_count;
	uint received_mask;
	uint64 cursor;
	uint64 eof;
	uint reuse_count;
	uint watch_events;
	unsigned char generation[32];
	unsigned char page_digest[32];
	struct agent_file_live_watch watch;
	struct harness_catalog_entry entries[HARNESS_CATALOG_MAX];
};

static struct harness_worker workers[HARNESS_MAX_AGENTS];
static struct harness_task tasks[HARNESS_MAX_TASKS];
static struct harness_channel channel;
static struct agent_workflow_lifecycle_key lifecycle;
static struct agent_execution_contract_node
	harness_nodes[HARNESS_CONTRACT_NODES];
static struct agent_execution_contract_key harness_contract;
static unsigned char harness_node_consumed[HARNESS_CONTRACT_NODES];
static uint harness_contract_ready;
static uint64 harness_tasks_submitted;
static uint64 harness_tasks_completed;
static uint64 harness_worker_wait_sleep_count;
static uint64 harness_worker_wait_wakeup_count;
static uint64 harness_worker_last_heartbeat_tick;
static uint64 harness_task_wait_count;
static uint64 harness_artifact_count;
static uint64 harness_artifact_bytes;
static uint harness_catalog_state_code;
static uint harness_catalog_records;
static uint harness_catalog_candidates;
static uint harness_catalog_reuse_count;
static uint harness_catalog_watch_events;
static const uint harness_contract_tool_plan[HARNESS_CONTRACT_NODES] = {
	AGENT_TOOL_DELEGATE_TASK, AGENT_TOOL_DELEGATE_TASK,
	AGENT_TOOL_DELEGATE_TASK,
	AGENT_TOOL_SEARCH_FILES, AGENT_TOOL_SEARCH_FILES,
	AGENT_TOOL_SEARCH_FILES,
	AGENT_TOOL_READ_WORKSPACE_FILE, AGENT_TOOL_READ_WORKSPACE_FILE,
	AGENT_TOOL_READ_WORKSPACE_FILE,
	AGENT_TOOL_WRITE_FILE, AGENT_TOOL_WRITE_FILE,
	AGENT_TOOL_WRITE_FILE, AGENT_TOOL_WRITE_FILE,
	AGENT_TOOL_APPLY_PATCH, AGENT_TOOL_APPLY_PATCH,
	AGENT_TOOL_APPLY_PATCH,
	AGENT_TOOL_BUILD_UCORE_PROGRAM, AGENT_TOOL_BUILD_UCORE_PROGRAM,
	AGENT_TOOL_BUILD_UCORE_PROGRAM, AGENT_TOOL_BUILD_UCORE_PROGRAM,
	AGENT_TOOL_BUILD_UCORE_PROGRAM,
	AGENT_TOOL_RUN_UCORE_PROGRAM, AGENT_TOOL_RUN_UCORE_PROGRAM,
	AGENT_TOOL_RUN_UCORE_PROGRAM,
};
static uint64 next_request_id = 700000ULL;
static struct agent_info status_current;
static struct agent_context_header status_context;
static struct agent_workflow_lifecycle_info status_lifecycle;
static struct harness_catalog_state harness_catalog;
static struct agent_file_meta harness_catalog_meta[HARNESS_CATALOG_MAX + 1U];
static int harness_catalog_statuses[HARNESS_CATALOG_MAX + 1U];
static struct agent_file_query harness_catalog_query;
static struct agent_file_query_result harness_catalog_result;
static struct agent_event harness_catalog_event;
static struct agent_workflow_fence_request harness_fence_request;
static struct agent_workflow_fence_receipt harness_fence_receipt;
static struct agent_runtime_config harness_service_query;
static struct agent_runtime_config_result harness_service_self;
static struct agent_workflow_lifecycle_info harness_service_lifecycle;
static struct agent_runtime_config harness_spawn_config;
static struct agent_runtime_config_result harness_spawn_result;
static char harness_service_line[HARNESS_LINE_BYTES];
/* Each provider runs in its own forked address space.  Keeping the bounded
 * serial/Task scratch objects here avoids consuming most of the 4 KiB user
 * stack without sharing mutable state between providers. */
static struct agent_task_delegate_claim harness_provider_request;
static struct agent_task_delegate_claim_result harness_provider_claim;
static struct harness_worker_command harness_provider_command;
static struct harness_worker_event harness_provider_event;
static struct agent_info harness_provider_info;
static struct agent_task_delegate_complete harness_provider_terminal_query;
static struct agent_task_delegate_complete_result
	harness_provider_terminal_result;

static char *next_token(char **cursor);
static int parse_u64(const char *text, uint64 *value);
static int parse_hex_bytes(const char *text, unsigned char *output,
	uint capacity, uint *length);
static void print_digest(const unsigned char digest[32]);

static void fail(const char *message)
{
	printf("AGENT_HARNESS ERROR %s\n", message);
	exit(1);
}

static __attribute__((noinline)) void print_status(void)
{
	uint agents_active = 0;
	uint tasks_active = 0;
	uint64 sq_head;
	uint64 sq_tail;
	uint64 cq_head;
	uint64 cq_tail;

	memset(&status_current, 0, sizeof(status_current));
	memset(&status_context, 0, sizeof(status_context));
	memset(&status_lifecycle, 0, sizeof(status_lifecycle));
	if (agent_info(&status_current) != 0 ||
	    context_snapshot(&status_context, 0, 0) < 0 ||
	    agent_workflow_lifecycle_info(&status_lifecycle, &lifecycle) !=
	        AGENT_STATUS_OK ||
	    !status_lifecycle.charged ||
	    status_lifecycle.key.id != lifecycle.id ||
	    status_lifecycle.key.generation != lifecycle.generation)
		fail("status_query");
	for (uint index = 0; index < HARNESS_MAX_AGENTS; index++)
		if (workers[index].active)
			agents_active++;
	for (uint index = 0; index < HARNESS_MAX_TASKS; index++)
		if (tasks[index].active)
			tasks_active++;
	sq_head = channel.sq->header.head;
	sq_tail = channel.sq->header.tail;
	cq_head = channel.cq->header.head;
	cq_tail = channel.cq->header.tail;
	if (sq_tail < sq_head || cq_tail < cq_head)
		fail("status_ring");
	printf("AGENT_HARNESS STATUS version=2 tick=%lu lifecycle_id=%lu lifecycle_generation=%lu agents_active=%u tasks_active=%u tasks_pending=0 tasks_claimed=%u tasks_terminal=%lu sq_depth=%lu cq_depth=%lu submitted=%lu completed=%lu context_count=%lu context_latest=%lu context_dropped=%lu artifact_count=%lu artifact_bytes=%lu catalog_state=%u catalog_records=%u catalog_candidates=%u catalog_reuse=%u catalog_watch_events=%u loop_state=%d wait_count=%lu wait_sleep_count=%lu wait_wakeup_count=%lu task_wait_count=%lu last_heartbeat_tick=%lu scheduler_runnable=%u scheduler_vruntime=%lu scheduler_virtual_deadline=%lu scheduler_service_cycles=%lu resource_account_slot=%u resource_account_generation=%lu\n",
	       status_current.current_tick, lifecycle.id, lifecycle.generation,
	       agents_active, tasks_active, tasks_active,
	       harness_tasks_completed, sq_tail - sq_head, cq_tail - cq_head,
	       harness_tasks_submitted, harness_tasks_completed,
	       status_context.count, status_context.latest_sequence,
	       status_context.dropped_records, harness_artifact_count,
	       harness_artifact_bytes, harness_catalog_state_code,
	       harness_catalog_records, harness_catalog_candidates,
	       harness_catalog_reuse_count, harness_catalog_watch_events,
	       status_current.loop_state,
	       status_current.wait_count, harness_worker_wait_sleep_count,
	       harness_worker_wait_wakeup_count,
	       harness_task_wait_count,
	       harness_worker_last_heartbeat_tick,
	       status_lifecycle.scheduler_runnable,
	       status_lifecycle.scheduler_vruntime,
	       status_lifecycle.scheduler_virtual_deadline,
	       status_lifecycle.scheduler_service_cycles,
	       status_lifecycle.resource_account_slot,
	       status_lifecycle.resource_account_generation);
}

static __attribute__((noinline)) void seal_workflow(char *arguments)
{
	char *cursor = arguments;
	char *token;
	uint64 request_id;
	uint challenge_length = 0;
	int status = AGENT_STATUS_RETRY;

	memset(&harness_fence_request, 0, sizeof(harness_fence_request));
	memset(&harness_fence_receipt, 0, sizeof(harness_fence_receipt));
	if ((token = next_token(&cursor)) == 0 ||
	    parse_u64(token, &request_id) < 0 || request_id == 0 ||
	    (token = next_token(&cursor)) == 0 ||
	    parse_hex_bytes(token, harness_fence_request.challenge,
		AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE, &challenge_length) < 0 ||
	    challenge_length != AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE ||
	    next_token(&cursor) != 0)
		fail("fence_frame");
	harness_fence_request.version = AGENT_WORKFLOW_FENCE_VERSION;
	harness_fence_request.struct_size = sizeof(harness_fence_request);
	harness_fence_request.request_id = request_id;
	for (uint attempt = 0; attempt < 64U; attempt++) {
		memset(&harness_fence_receipt, 0, sizeof(harness_fence_receipt));
		status = agent_workflow_fence(&harness_fence_request,
			&harness_fence_receipt);
		if (status != AGENT_STATUS_RETRY)
			break;
		(void)sched_yield();
	}
	printf("AGENT_HARNESS FENCE %d %d %u %lu %lu %lu %lu %lu %lu %lu %lu %lu ",
	       status, harness_fence_receipt.status, harness_fence_receipt.flags,
	       harness_fence_receipt.key.id,
	       harness_fence_receipt.key.generation,
	       harness_fence_receipt.request_id,
	       harness_fence_receipt.fence_sequence,
	       harness_fence_receipt.metadata_generation,
	       harness_fence_receipt.credit_epoch,
	       harness_fence_receipt.evidence_first_sequence,
	       harness_fence_receipt.evidence_last_sequence,
	       harness_fence_receipt.evidence_event_count);
	print_digest(harness_fence_receipt.credit_digest);
	printf(" ");
	print_digest(harness_fence_receipt.evidence_root);
	printf("\n");
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
		ssize_t count;

		count = read(0, &value, 1);
		if (count < 0) {
			/* Kernel task/cancel events may interrupt the controller's
			 * console wait.  The synchronized Host frame remains pending. */
			(void)sched_yield();
			continue;
		}
		if (count == 0)
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

static int parse_hex_bytes(const char *text, unsigned char *output,
	uint capacity, uint *length)
{
	uint text_length;

	if (text == 0)
		return -1;
	text_length = strlen(text);
	if (text_length == 0 || (text_length & 1U) != 0 ||
	    text_length / 2U > capacity)
		return -1;
	for (uint index = 0; index < text_length / 2U; index++) {
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
		output[index] = (unsigned char)value;
	}
	*length = text_length / 2U;
	return 0;
}

static void print_digest(const unsigned char digest[32])
{
	static const char digits[] = "0123456789abcdef";

	for (uint index = 0; index < 32U; index++)
		printf("%c%c", digits[digest[index] >> 4],
		       digits[digest[index] & 15U]);
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

static void prime_worker_context(uint host_agent_id)
{
	struct agent_context_header header;
	struct agent_context_record record;
	int push_status;
	int snapshot_status;

	memset(&header, 0, sizeof(header));
	if (context_snapshot(&header, 0, 0) == 0 &&
	    header.visible_head_sequence != 0)
		return;
	memset(&record, 0, sizeof(record));
	record.request_id = ++next_request_id;
	record.arg0 = host_agent_id;
	record.tool_id = AGENT_TOOL_CONTEXT_PUSH;
	record.status = AGENT_STATUS_OK;
	memcpy(record.payload, "agent-loop", sizeof("agent-loop") - 1U);
	memcpy(record.result, "ready", sizeof("ready") - 1U);
	memset(&header, 0, sizeof(header));
	push_status = context_push(&record);
	snapshot_status = context_snapshot(&header, 0, 0);
	if (push_status != AGENT_STATUS_OK || snapshot_status != 0 ||
	    header.visible_head_sequence == 0)
		fail("worker_context_prime");
}

static int tool_security(uint tool_id, uint64 *capabilities,
	uint64 *accepted, uint64 *output, uint64 *effects)
{
	uint64 control = AGENT_PROVENANCE_KERNEL_FACT |
		AGENT_PROVENANCE_TRUSTED_USER_CONTROL |
		AGENT_PROVENANCE_AGENT_DERIVED;
	uint64 artifact = control |
		AGENT_PROVENANCE_UNTRUSTED_FILE_DATA |
		AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT;

	*accepted = artifact | AGENT_PROVENANCE_CROSS_AGENT_DATA;
	*output = AGENT_PROVENANCE_AGENT_DERIVED;
	*effects = AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA |
		AGENT_SIDE_EFFECT_IPC | AGENT_SIDE_EFFECT_ARTIFACT;
	if (tool_id == AGENT_TOOL_DELEGATE_TASK) {
		*capabilities = AGENT_CAP_ORCHESTRATE;
		*accepted = control |
			AGENT_PROVENANCE_UNTRUSTED_FILE_DATA |
			AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT |
			AGENT_PROVENANCE_CROSS_AGENT_DATA;
		*effects = HARNESS_TASK_EFFECTS;
		return 0;
	}
	if (tool_id == AGENT_TOOL_APPLY_PATCH ||
	    tool_id == AGENT_TOOL_WRITE_FILE) {
		*capabilities = AGENT_CAP_WORKSPACE_WRITE;
		*effects |= AGENT_SIDE_EFFECT_FILE;
		return 0;
	}
	if (tool_id == AGENT_TOOL_SEARCH_FILES ||
	    tool_id == AGENT_TOOL_READ_WORKSPACE_FILE) {
		*capabilities = AGENT_CAP_CONTENT_READ;
		*output = AGENT_PROVENANCE_AGENT_DERIVED |
			AGENT_PROVENANCE_UNTRUSTED_FILE_DATA;
		*effects |= AGENT_SIDE_EFFECT_WATCH;
		return 0;
	}
	if (tool_id == AGENT_TOOL_BUILD_UCORE_PROGRAM) {
		*capabilities = AGENT_CAP_CONTENT_READ |
			AGENT_CAP_WORKSPACE_WRITE;
		*output = AGENT_PROVENANCE_AGENT_DERIVED |
			AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT;
		*effects |= AGENT_SIDE_EFFECT_PROCESS;
		return 0;
	}
	if (tool_id == AGENT_TOOL_RUN_UCORE_PROGRAM) {
		*capabilities = AGENT_CAP_CONTENT_READ;
		*output = AGENT_PROVENANCE_AGENT_DERIVED |
			AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT;
		*effects |= AGENT_SIDE_EFFECT_PROCESS;
		return 0;
	}
	return -1;
}

static void ensure_contract(void)
{
	struct agent_execution_contract_control control;
	struct agent_execution_contract_result result;

	if (harness_contract_ready)
		return;
	memset(harness_nodes, 0, sizeof(harness_nodes));
	for (uint index = 0; index < HARNESS_CONTRACT_NODES; index++) {
		struct agent_execution_contract_node *node = &harness_nodes[index];
		uint64 capabilities = 0;
		uint64 accepted = 0;
		uint64 output = 0;
		uint64 effects = 0;

		if (tool_security(harness_contract_tool_plan[index], &capabilities, &accepted,
			&output, &effects) < 0)
			fail("contract_tool");
		node->version = AGENT_EXECUTION_CONTRACT_NODE_VERSION;
		node->size = sizeof(*node);
		node->node_id = index;
		node->tool_id = harness_contract_tool_plan[index];
		node->required_capabilities = capabilities;
		node->accepted_input_labels = accepted;
		node->output_add_labels = output;
		node->side_effect_mask = effects;
		node->input_artifact_type = AGENT_ARTIFACT_TASK;
		/* The Task descriptor names the eventual result Artifact.  The
		 * submission CQE itself remains untyped for every delegated provider. */
		node->output_artifact_type = AGENT_ARTIFACT_NONE;
		node->max_attempts = 1;
		node->cancel_policy = AGENT_EXECUTION_CANCEL_ALLOW;
		node->charge_class = HARNESS_CHARGE_RESERVED;
		if (harness_contract_tool_plan[index] != AGENT_TOOL_DELEGATE_TASK)
			node->exec_envelope[0] = 1;
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
	control.node_count = HARNESS_CONTRACT_NODES;
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
	control.node_count = HARNESS_CONTRACT_NODES;
	control.node_size = sizeof(harness_nodes[0]);
	if (agent_execution_contract(&control, &result) != 0 ||
	    result.status != AGENT_STATUS_OK ||
	    result.node_count != HARNESS_CONTRACT_NODES)
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

static struct harness_task *allocate_task(uint tool_id)
{
	uint node_id = HARNESS_CONTRACT_NODES;

	for (uint index = 0; index < HARNESS_CONTRACT_NODES; index++)
		if (!harness_node_consumed[index] &&
		    harness_contract_tool_plan[index] == tool_id) {
			node_id = index;
			break;
		}
	if (node_id == HARNESS_CONTRACT_NODES)
		return 0;
	for (uint index = 0; index < HARNESS_MAX_TASKS; index++)
		if (!tasks[index].active) {
			memset(&tasks[index], 0, sizeof(tasks[index]));
			tasks[index].active = 1;
			tasks[index].tool_id = tool_id;
			tasks[index].node_id = node_id;
			harness_node_consumed[node_id] = 1;
			return &tasks[index];
		}
	return 0;
}

static void artifact_path(char path[16], uint handle)
{
	static const char digits[] = "0123456789abcdef";

	memcpy(path, "hna", 3);
	for (uint index = 0; index < 8U; index++)
		path[3U + index] = digits[(handle >> ((7U - index) * 4U)) & 15U];
	path[11] = 0;
}

static void digest_text(const unsigned char digest[32], char text[65])
{
	static const char digits[] = "0123456789abcdef";

	for (uint index = 0; index < 32U; index++) {
		text[index * 2U] = digits[digest[index] >> 4];
		text[index * 2U + 1U] = digits[digest[index] & 15U];
	}
	text[64] = 0;
}

static int bytes_equal(const void *left, const void *right, uint length)
{
	const unsigned char *a = left;
	const unsigned char *b = right;

	for (uint index = 0; index < length; index++)
		if (a[index] != b[index])
			return 0;
	return 1;
}

static void catalog_name(char name[AGENT_FILE_NAME_SIZE], uint host_agent_id,
	int index)
{
	memset(name, 0, AGENT_FILE_NAME_SIZE);
	name[0] = 'h';
	name[1] = 'c';
	name[2] = (char)('0' + host_agent_id);
	if (index < 0) {
		name[3] = 'c';
		name[4] = 't';
		name[5] = 'l';
	} else {
		name[3] = (char)('0' + index);
	}
}

static int catalog_wait_watch(void)
{
	for (uint attempt = 0; attempt < 8U; attempt++) {
		memset(&harness_catalog_event, 0, sizeof(harness_catalog_event));
		if (agent_wait(&harness_catalog_event, 200) != AGENT_STATUS_OK)
			return -1;
		if (harness_catalog_event.type == AGENT_EVENT_FILE_QUERY) {
			harness_catalog.watch_events++;
			return 0;
		}
	}
	return -1;
}

static void catalog_meta_common(struct agent_file_meta *meta,
	uint host_agent_id, int index)
{
	char object_text[65];

	memset(meta, 0, sizeof(*meta));
	meta->fid = 6000 + (int)host_agent_id * 16 + index + 1;
	catalog_name(meta->physical_name, host_agent_id, index);
	strcpy(meta->project, "nexus");
	strcpy(meta->workflow, "workspace");
	if (index < 0) {
		strcpy(meta->logical_path, "host/manifest-control");
		strcpy(meta->stage, "control");
		strcpy(meta->kind, "manifest");
		strcpy(meta->summary, "versioned workspace manifest");
	} else {
		digest_text(harness_catalog.entries[index].object_id, object_text);
		memcpy(meta->logical_path, "host/", 5);
		memcpy(meta->logical_path + 5, object_text, 65);
		strcpy(meta->stage, harness_catalog.entries[index].stage);
		strcpy(meta->kind, harness_catalog.entries[index].kind);
		strcpy(meta->summary, harness_catalog.entries[index].summary);
		meta->size = harness_catalog.entries[index].size;
	}
	meta->update_mask = AGENT_FILE_META_UPDATE_ALL;
}

static int catalog_set_control(const char *state)
{
	struct agent_file_meta *meta = &harness_catalog_meta[0];
	int status;

	catalog_meta_common(meta, harness_catalog.host_agent_id, -1);
	strcpy(meta->run_id, "workspace");
	strcpy(meta->status, state);
	status = agent_file_meta_set(meta);
	if (status != AGENT_STATUS_OK)
		return -1;
	return harness_catalog.watch.watch_id == 0 ? 0 : catalog_wait_watch();
}

static int catalog_initialize(uint host_agent_id)
{
	struct agent_file_meta *control = &harness_catalog_meta[0];
	char name[AGENT_FILE_NAME_SIZE];
	int fd;

	if (harness_catalog.initialized)
		return harness_catalog.host_agent_id == host_agent_id ? 0 : -1;
	memset(&harness_catalog, 0, sizeof(harness_catalog));
	harness_catalog.host_agent_id = host_agent_id;
	if (agent_metadata_init() != AGENT_STATUS_OK)
		return -1;
	catalog_name(name, host_agent_id, -1);
	(void)unlink(name);
	fd = open(name, O_CREATE | O_TRUNC | O_WRONLY);
	if (fd < 0 || close(fd) != 0)
		return -1;
	for (uint index = 0; index < HARNESS_CATALOG_MAX; index++) {
		catalog_name(name, host_agent_id, (int)index);
		(void)unlink(name);
		fd = open(name, O_CREATE | O_TRUNC | O_WRONLY);
		if (fd < 0 || close(fd) != 0)
			return -1;
	}
	catalog_meta_common(control, host_agent_id, -1);
	strcpy(control->run_id, "workspace");
	strcpy(control->status, "stale");
	if (agent_file_meta_set(control) != AGENT_STATUS_OK)
		return -1;
	memset(&harness_catalog.watch, 0, sizeof(harness_catalog.watch));
	harness_catalog.watch.version = AGENT_FILE_LIVE_WATCH_VERSION;
	harness_catalog.watch.query.flags = AGENT_FILE_QUERY_USE_INDEX;
	harness_catalog.watch.query.max_hits = 1;
	strcpy(harness_catalog.watch.query.physical_name,
	       control->physical_name);
	strcpy(harness_catalog.watch.query.project, "nexus");
	strcpy(harness_catalog.watch.query.workflow, "workspace");
	strcpy(harness_catalog.watch.query.stage, "control");
	strcpy(harness_catalog.watch.query.kind, "manifest");
	if (agent_live_watch(&harness_catalog.watch) != AGENT_STATUS_OK ||
	    harness_catalog.watch.watch_id == 0)
		return -1;
	harness_catalog.initialized = 1;
	return 0;
}

static int catalog_mark_data_stale(uint count)
{
	uint used = 0;

	for (uint index = 0; index < count; index++) {
		struct agent_file_meta *meta = &harness_catalog_meta[used];

		memset(meta, 0, sizeof(*meta));
		meta->fid = 6000 + (int)harness_catalog.host_agent_id * 16 +
			(int)index + 1;
		catalog_name(meta->physical_name,
			harness_catalog.host_agent_id, (int)index);
		strcpy(meta->status, "stale");
		meta->update_mask = AGENT_FILE_META_UPDATE_STATUS;
		used++;
	}
	if (used == 0)
		return 0;
	memset(harness_catalog_statuses, 0, sizeof(harness_catalog_statuses));
	if (agent_file_meta_set_batch(harness_catalog_meta,
		    harness_catalog_statuses, (int)used,
		    AGENT_FILE_META_BATCH_F_NONE) != (int)used)
		return -1;
	for (uint index = 0; index < used; index++)
		if (harness_catalog_statuses[index] != AGENT_STATUS_OK)
			return -1;
	return 0;
}

static void catalog_event_base(struct harness_worker_event *event,
	uint host_agent_id, uint64 task_id, uint operation)
{
	memset(event, 0, sizeof(*event));
	event->operation = HARNESS_WORKER_CATALOG;
	event->host_agent_id = host_agent_id;
	event->task_id = task_id;
	event->artifact_kind = operation;
	event->catalog_records = harness_catalog.count + 1U;
	event->catalog_watch_events = harness_catalog.watch_events;
	memcpy(event->content_sha256, harness_catalog.generation, 32);
}

static void worker_catalog_command(uint host_agent_id,
	const struct harness_worker_command *command,
	struct harness_worker_event *event)
{
	uint operation = command->operation;

	catalog_event_base(event, host_agent_id, command->task_id, operation);
	if (catalog_initialize(host_agent_id) < 0) {
		event->status = AGENT_STATUS_IO_ERROR;
		return;
	}
	if (operation == HARNESS_COMMAND_CATALOG_BEGIN) {
		uint count = command->artifact_handle;
		int reuse = harness_catalog.window_valid &&
			harness_catalog.count == count &&
			harness_catalog.cursor == command->artifact_offset &&
			harness_catalog.eof == command->host_context_sequence &&
			bytes_equal(harness_catalog.generation,
			       command->content_sha256, 32) &&
			bytes_equal(harness_catalog.page_digest,
			       command->payload.chunk, 32);

		if (count == 0 || count > HARNESS_CATALOG_MAX) {
			event->status = AGENT_STATUS_BAD_PARAM;
			return;
		}
		if (reuse) {
			harness_catalog.reuse_count++;
			event->status = AGENT_STATUS_OK;
			event->catalog_reuse = 1;
			event->catalog_candidates = harness_catalog.count;
			return;
		}
		harness_catalog.previous_count = harness_catalog.count;
		harness_catalog.count = count;
		harness_catalog.cursor = command->artifact_offset;
		harness_catalog.eof = command->host_context_sequence;
		harness_catalog.received_mask = 0;
		harness_catalog.window_valid = 0;
		harness_catalog.building = 1;
		memset(harness_catalog.entries, 0,
		       sizeof(harness_catalog.entries));
		memcpy(harness_catalog.generation,
		       command->content_sha256, 32);
		memcpy(harness_catalog.page_digest,
		       command->payload.chunk, 32);
		if (catalog_set_control("building") < 0 ||
		    catalog_mark_data_stale(harness_catalog.previous_count) < 0) {
			(void)catalog_set_control("stale");
			harness_catalog.building = 0;
			event->status = AGENT_STATUS_IO_ERROR;
			return;
		}
		event->status = AGENT_STATUS_OK;
		event->catalog_records = count + 1U;
		return;
	}
	if (operation == HARNESS_COMMAND_CATALOG_ENTRY) {
		uint index = command->payload.catalog_entry.index;
		struct harness_catalog_entry *entry;

		if (!harness_catalog.building ||
		    command->payload.catalog_entry.count != harness_catalog.count ||
		    index >= harness_catalog.count ||
		    command->payload.catalog_entry.stage[0] == 0 ||
		    command->payload.catalog_entry.kind[0] == 0 ||
		    command->payload.catalog_entry.summary[0] == 0) {
			event->status = AGENT_STATUS_BAD_PARAM;
			return;
		}
		entry = &harness_catalog.entries[index];
		entry->received = 1;
		entry->size = command->payload.catalog_entry.size;
		memcpy(entry->object_id, command->content_sha256, 32);
		memcpy(entry->revision,
		       command->payload.catalog_entry.revision, 32);
		strcpy(entry->stage, command->payload.catalog_entry.stage);
		strcpy(entry->kind, command->payload.catalog_entry.kind);
		strcpy(entry->summary, command->payload.catalog_entry.summary);
		harness_catalog.received_mask |= 1U << index;
		event->status = AGENT_STATUS_OK;
		return;
	}
	if (operation == HARNESS_COMMAND_CATALOG_COMMIT) {
		uint expected = (1U << harness_catalog.count) - 1U;

		if (!harness_catalog.building ||
		    harness_catalog.received_mask != expected) {
			event->status = AGENT_STATUS_CONFLICT;
			return;
		}
		for (uint index = 0; index < harness_catalog.count; index++) {
			struct agent_file_meta *meta = &harness_catalog_meta[index];
			char generation_text[65];

			catalog_meta_common(meta, host_agent_id, (int)index);
			digest_text(harness_catalog.generation, generation_text);
			memcpy(meta->run_id, generation_text,
			       AGENT_FILE_FIELD_SIZE - 1U);
			meta->run_id[AGENT_FILE_FIELD_SIZE - 1U] = 0;
			strcpy(meta->status, "current");
		}
		memset(harness_catalog_statuses, 0,
		       sizeof(harness_catalog_statuses));
		if (agent_file_meta_set_batch(harness_catalog_meta,
			    harness_catalog_statuses,
			    (int)harness_catalog.count,
			    AGENT_FILE_META_BATCH_F_NONE) !=
		    (int)harness_catalog.count) {
			event->status = AGENT_STATUS_IO_ERROR;
			goto abort;
		}
		for (uint index = 0; index < harness_catalog.count; index++)
			if (harness_catalog_statuses[index] != AGENT_STATUS_OK) {
				event->status = harness_catalog_statuses[index];
				goto abort;
			}
		if (catalog_set_control("ready") < 0) {
			event->status = AGENT_STATUS_IO_ERROR;
			goto abort;
		}
		memset(&harness_catalog_query, 0,
		       sizeof(harness_catalog_query));
		harness_catalog_query.flags = AGENT_FILE_QUERY_USE_INDEX;
		harness_catalog_query.max_hits = HARNESS_CATALOG_MAX;
		strcpy(harness_catalog_query.project, "nexus");
		strcpy(harness_catalog_query.workflow, "workspace");
		strcpy(harness_catalog_query.status, "current");
		memset(&harness_catalog_result, 0,
		       sizeof(harness_catalog_result));
		if (agent_file_query(&harness_catalog_query,
			    &harness_catalog_result) !=
		    (int)harness_catalog.count ||
		    harness_catalog_result.returned != (int)harness_catalog.count ||
		    harness_catalog_result.truncated ||
		    !harness_catalog_result.used_index) {
			event->status = AGENT_STATUS_INDETERMINATE;
			goto abort;
		}
		harness_catalog.building = 0;
		harness_catalog.window_valid = 1;
		event->status = AGENT_STATUS_OK;
		event->catalog_records = harness_catalog.count + 1U;
		event->catalog_candidates = harness_catalog.count;
		event->catalog_used_index = 1;
		event->catalog_watch_events = harness_catalog.watch_events;
		event->catalog_fs_generation =
			harness_catalog_result.fs_generation;
		return;
abort:
		(void)catalog_mark_data_stale(harness_catalog.count);
		(void)catalog_set_control("stale");
		harness_catalog.building = 0;
		harness_catalog.window_valid = 0;
		return;
	}
	if (operation == HARNESS_COMMAND_CATALOG_QUERY) {
		if (!harness_catalog.window_valid || harness_catalog.building) {
			event->status = AGENT_STATUS_STALE;
			return;
		}
		memset(&harness_catalog_query, 0,
		       sizeof(harness_catalog_query));
		harness_catalog_query.flags = AGENT_FILE_QUERY_USE_INDEX;
		harness_catalog_query.max_hits = HARNESS_CATALOG_MAX;
		strcpy(harness_catalog_query.project, "nexus");
		strcpy(harness_catalog_query.workflow, "workspace");
		strcpy(harness_catalog_query.status,
		       command->payload.catalog_query.status[0] ?
		       command->payload.catalog_query.status : "current");
		strcpy(harness_catalog_query.stage,
		       command->payload.catalog_query.stage);
		strcpy(harness_catalog_query.kind,
		       command->payload.catalog_query.kind);
		strcpy(harness_catalog_query.summary_contains,
		       command->payload.catalog_query.summary);
		memset(&harness_catalog_result, 0,
		       sizeof(harness_catalog_result));
		if (agent_file_query(&harness_catalog_query,
			    &harness_catalog_result) < 0 ||
		    harness_catalog_result.returned !=
			harness_catalog_result.total_hits ||
		    harness_catalog_result.truncated ||
		    !harness_catalog_result.used_index) {
			event->status = AGENT_STATUS_INDETERMINATE;
			return;
		}
		for (int hit = 0; hit < harness_catalog_result.returned; hit++) {
			struct agent_file_hit *item =
				&harness_catalog_result.hits[hit];

			for (uint index = 0; index < harness_catalog.count; index++) {
				char name[AGENT_FILE_NAME_SIZE];

				catalog_name(name, host_agent_id, (int)index);
				if (!strcmp(name, item->physical_name)) {
					event->catalog_mask |= 1U << index;
					event->catalog_dev[index] = item->dev;
					event->catalog_inum[index] = item->inum;
					event->catalog_incarnation[index] =
						item->incarnation;
					break;
				}
			}
		}
		event->status = AGENT_STATUS_OK;
		event->catalog_records = harness_catalog.count + 1U;
		event->catalog_candidates =
			(uint)harness_catalog_result.returned;
		event->catalog_used_index =
			(uint)harness_catalog_result.used_index;
		event->catalog_watch_events = harness_catalog.watch_events;
		event->catalog_fs_generation =
			harness_catalog_result.fs_generation;
		return;
	}
	if (operation == HARNESS_COMMAND_CATALOG_STALE) {
		int failed = catalog_mark_data_stale(harness_catalog.count) < 0 ||
			catalog_set_control("stale") < 0;

		harness_catalog.window_valid = 0;
		harness_catalog.building = 0;
		event->status = failed ? AGENT_STATUS_IO_ERROR : AGENT_STATUS_OK;
		event->catalog_records = harness_catalog.count + 1U;
		event->catalog_watch_events = harness_catalog.watch_events;
		return;
	}
	event->status = AGENT_STATUS_BAD_PARAM;
}

static int worker_context_push(const struct harness_worker_command *command,
	const char *phase, int status, uint64 *sequence)
{
	struct agent_context_header header;
	struct agent_context_record record;
	int push_status;
	uint phase_length;

	*sequence = 0;
	memset(&record, 0, sizeof(record));
	record.request_id = ++next_request_id;
	/* Manual Context records cannot nominate a cause.  The validated Task CQE
	 * carries the causal sequence; keep it as structured record data and bind
	 * the Artifact to the sequence that this syscall actually publishes. */
	record.arg0 = command->artifact_handle;
	record.value0 = command->artifact_length;
	record.value1 = command->host_context_sequence;
	record.value2 = command->cause_sequence != 0 ? command->cause_sequence :
			command->task_id;
	record.tool_id = (int)command->tool_id;
	record.status = status;
	phase_length = strlen(phase);
	if (phase_length >= sizeof(record.payload))
		phase_length = sizeof(record.payload) - 1U;
	memcpy(record.payload, phase, phase_length);
	memcpy(record.result, "artifact", sizeof("artifact") - 1U);
	push_status = context_push(&record);
	if (push_status != AGENT_STATUS_OK)
		return push_status;
	memset(&header, 0, sizeof(header));
	if (context_snapshot(&header, 0, 0) != 0 ||
	    header.visible_head_sequence == 0)
		return AGENT_STATUS_IO_ERROR;
	*sequence = header.visible_head_sequence;
	return AGENT_STATUS_OK;
}

static int worker_context_head(uint64 *sequence)
{
	struct agent_context_header header;

	*sequence = 0;
	memset(&header, 0, sizeof(header));
	if (context_snapshot(&header, 0, 0) != 0 ||
	    header.visible_head_sequence == 0)
		return AGENT_STATUS_IO_ERROR;
	*sequence = header.visible_head_sequence;
	return AGENT_STATUS_OK;
}

static void worker_event_from_artifact(struct harness_worker_event *event,
	uint host_agent_id, const struct agent_context_artifact_result *result,
	uint64 context_sequence)
{
	memset(event, 0, sizeof(*event));
	event->operation = HARNESS_WORKER_ARTIFACT;
	event->host_agent_id = host_agent_id;
	event->status = result->status;
	event->artifact_handle = (uint)result->handle;
	event->artifact_kind = result->kind;
	event->artifact_flags = result->flags;
	event->producer_agent_id = result->producer_agent_id;
	event->artifact_length = result->length;
	event->context_sequence = context_sequence;
	event->producer_control_id = result->producer_control_id;
	memcpy(event->content_sha256, result->content_sha256,
	       sizeof(event->content_sha256));
}

static int worker_artifact_command(uint host_agent_id,
	const struct harness_worker_command *command, int *stage_fd,
	uint *stage_handle, uint64 *stage_length, uint64 *stage_received,
	struct harness_worker_event *event)
{
	struct agent_context_artifact_control control;
	struct agent_context_artifact_result result;
	char path[16];
	uint64 sequence;
	int fd;
	int status;

	memset(event, 0, sizeof(*event));
	event->operation = HARNESS_WORKER_ARTIFACT;
	event->host_agent_id = host_agent_id;
	event->task_id = command->task_id;
	if (command->operation == HARNESS_COMMAND_ARTIFACT_BEGIN) {
		if (*stage_fd >= 0 || command->artifact_handle == 0 ||
		    command->artifact_length == 0 ||
		    command->artifact_length > AGENT_CONTEXT_ARTIFACT_MAX_BYTES ||
		    command->artifact_kind == 0 ||
		    command->artifact_kind >= AGENT_CONTEXT_ARTIFACT_KIND_COUNT ||
		    (command->artifact_flags &
		     ~(AGENT_CONTEXT_ARTIFACT_F_UTF8 |
		       AGENT_CONTEXT_ARTIFACT_F_SHAREABLE |
		       AGENT_CONTEXT_ARTIFACT_F_EXTERNAL)) != 0) {
			event->status = AGENT_STATUS_BAD_PARAM;
			return 0;
		}
		artifact_path(path, command->artifact_handle);
		(void)unlink(path);
		fd = open(path, O_CREATE | O_TRUNC | O_WRONLY);
		if (fd < 0) {
			event->status = AGENT_STATUS_IO_ERROR;
			return 0;
		}
		*stage_fd = fd;
		*stage_handle = command->artifact_handle;
		*stage_length = command->artifact_length;
		*stage_received = 0;
		event->status = AGENT_STATUS_OK;
		return 0;
	}
	if (command->operation == HARNESS_COMMAND_ARTIFACT_CHUNK) {
		if (*stage_fd < 0 || command->artifact_handle != *stage_handle ||
		    command->chunk_length == 0 ||
		    command->chunk_length > sizeof(command->payload.chunk) ||
		    command->artifact_offset != *stage_received ||
		    *stage_received + command->chunk_length > *stage_length) {
			event->status = AGENT_STATUS_BAD_PARAM;
			return 0;
		}
		if (write(*stage_fd, command->payload.chunk, command->chunk_length) !=
		    (int)command->chunk_length) {
			event->status = AGENT_STATUS_IO_ERROR;
			return 0;
		}
		*stage_received += command->chunk_length;
		event->status = AGENT_STATUS_OK;
		event->artifact_length = *stage_received;
		return 0;
	}
	if (command->operation == HARNESS_COMMAND_ARTIFACT_SEAL) {
		if (*stage_fd < 0 || command->artifact_handle != *stage_handle ||
		    *stage_received != *stage_length || close(*stage_fd) != 0) {
			event->status = AGENT_STATUS_BAD_PARAM;
			return 0;
		}
		*stage_fd = -1;
		/* The provider claim already published the Context node that causes
		 * this result.  Reuse that active sequence when sealing the result;
		 * a manual Context push here would need permission effects unrelated
		 * to the brokered tool.  The owner publishes the merge node after the
		 * terminal CQE and binds the sealed Artifact to that node below. */
		status = worker_context_head(&sequence);
		artifact_path(path, command->artifact_handle);
		fd = open(path, O_RDONLY);
		if (status != AGENT_STATUS_OK || fd < 0) {
			if (fd >= 0)
				(void)close(fd);
			(void)unlink(path);
			event->status = status != AGENT_STATUS_OK ? status :
					AGENT_STATUS_IO_ERROR;
			return 0;
		}
		memset(&control, 0, sizeof(control));
		control.version = AGENT_CONTEXT_ARTIFACT_VERSION;
		control.size = sizeof(control);
		control.operation = AGENT_CONTEXT_ARTIFACT_SEAL;
		control.flags = command->artifact_flags;
		control.handle = command->artifact_handle;
		control.source_fd = fd;
		control.kind = command->artifact_kind;
		control.length = command->artifact_length;
		control.source_context_sequence = sequence;
		control.task_id = command->task_id;
		memcpy(control.content_sha256, command->content_sha256,
		       sizeof(control.content_sha256));
		for (uint attempt = 0; attempt < 32U; attempt++) {
			memset(&result, 0, sizeof(result));
			status = agent_context_artifact(&control, &result);
			if (status != AGENT_STATUS_RETRY)
				break;
			(void)sched_yield();
		}
		(void)close(fd);
		(void)unlink(path);
		if (status == AGENT_STATUS_OK) {
			control.operation = AGENT_CONTEXT_ARTIFACT_BIND;
			status = agent_context_artifact(&control, &result);
		}
		if (status == AGENT_STATUS_OK &&
		    (command->artifact_flags &
		     AGENT_CONTEXT_ARTIFACT_F_SHAREABLE) != 0) {
			control.operation = AGENT_CONTEXT_ARTIFACT_SHARE;
			status = agent_context_artifact(&control, &result);
		}
		if (status != AGENT_STATUS_OK) {
			memset(&result, 0, sizeof(result));
			result.status = status;
		}
		worker_event_from_artifact(event, host_agent_id, &result, sequence);
		event->task_id = command->task_id;
		return 0;
	}
	if (command->operation == HARNESS_COMMAND_ARTIFACT_BIND) {
		status = worker_context_push(command, "merge", AGENT_STATUS_OK,
					     &sequence);
		memset(&control, 0, sizeof(control));
		control.version = AGENT_CONTEXT_ARTIFACT_VERSION;
		control.size = sizeof(control);
		control.operation = AGENT_CONTEXT_ARTIFACT_BIND;
		control.handle = command->artifact_handle;
		control.source_context_sequence = sequence;
		memset(&result, 0, sizeof(result));
		if (status == AGENT_STATUS_OK)
			status = agent_context_artifact(&control, &result);
		if (status == AGENT_STATUS_OK) {
			control.operation = AGENT_CONTEXT_ARTIFACT_QUERY;
			status = agent_context_artifact(&control, &result);
		}
		if (status != AGENT_STATUS_OK) {
			memset(&result, 0, sizeof(result));
			result.status = status;
		}
		worker_event_from_artifact(event, host_agent_id, &result, sequence);
		event->task_id = command->task_id;
		return 0;
	}
	event->status = AGENT_STATUS_BAD_PARAM;
	return 0;
}

static void worker_submit_task(uint host_agent_id,
	const struct harness_worker_command *command,
	struct harness_worker_event *event)
{
	const struct agent_task_delegate_descriptor *descriptor =
		&command->payload.submit.descriptor;
	struct agent_task_channel_enter_result entered;
	struct harness_task *task = 0;

	memset(event, 0, sizeof(*event));
	event->operation = HARNESS_WORKER_SUBMITTED;
	event->host_agent_id = host_agent_id;
	event->task_id = descriptor->task_id;
	if (descriptor->task_id == 0 ||
	    descriptor->task_type != command->tool_id ||
	    command->payload.submit.node_id >= HARNESS_CONTRACT_NODES ||
	    find_task(descriptor->task_id) != 0) {
		event->status = AGENT_STATUS_BAD_PARAM;
		return;
	}
	for (uint index = 0; index < HARNESS_MAX_TASKS; index++)
		if (!tasks[index].active) {
			task = &tasks[index];
			break;
		}
	if (task == 0) {
		event->status = AGENT_STATUS_NO_SPACE;
		return;
	}
	memset(task, 0, sizeof(*task));
	task->active = 1;
	task->owner_host_agent_id = host_agent_id;
	task->host_agent_id = command->host_context_sequence;
	task->tool_id = command->tool_id;
	task->node_id = command->payload.submit.node_id;
	task->task_id = descriptor->task_id;
	task->resource = import_descriptor(task->task_id, descriptor);
	memset(&task->sqe, 0, sizeof(task->sqe));
	task->sqe.version = AGENT_TASK_CHANNEL_ENTRY_VERSION;
	task->sqe.size = sizeof(task->sqe);
	task->sqe.opcode = AGENT_TASK_CHANNEL_OP_SUBMIT;
	task->sqe.flags = AGENT_TASK_SQE_F_HARD_DEADLINE;
	task->sqe.request_id = ++next_request_id;
	task->sqe.ring_generation = channel.generation;
	task->sqe.slot_generation =
		channel.sq_tail / AGENT_TASK_CHANNEL_CAPACITY + 1ULL;
	task->sqe.contract = command->payload.submit.contract;
	task->sqe.node_id = task->node_id;
	task->sqe.attempt_id = 1;
	task->sqe.tool_id = AGENT_TOOL_DELEGATE_TASK;
	task->sqe.deadline_tick = descriptor->deadline_tick;
	task->sqe.input = task->resource;
	task->sqe.input.flags = AGENT_TASK_HANDLE_F_BORROWED;
	memcpy(task->sqe.schema_digest, command->payload.submit.schema_digest,
	       sizeof(task->sqe.schema_digest));
	volatile_write((void *)&channel.sq->entries[
		channel.sq_tail % AGENT_TASK_CHANNEL_CAPACITY],
		&task->sqe, sizeof(task->sqe));
	channel.sq_tail++;
	enter_channel(0, 1, 0, &entered);
	event->status = entered.status;
	event->channel_generation = task->sqe.ring_generation;
	event->request_id = task->sqe.request_id;
	event->slot_generation = task->sqe.slot_generation;
	if (entered.status != AGENT_TASK_CHANNEL_OK || entered.submitted != 1) {
		struct agent_task_channel_resource_result released;

		resource_call(AGENT_TASK_RESOURCE_RELEASE, task->resource,
			      0, 0, 0, 0, &released);
		memset(task, 0, sizeof(*task));
		return;
	}
	if (entered.cq_tail > channel.cq_head) {
		struct agent_task_cqe early;

		volatile_read(&early, (const void *)&channel.cq->entries[
			channel.cq_head % AGENT_TASK_CHANNEL_CAPACITY], sizeof(early));
		event->status = AGENT_STATUS_CONFLICT;
		event->terminal_status = early.status;
		event->terminal_generation = early.decision_reason;
	}
}

static void worker_collect_task(uint host_agent_id,
	const struct harness_worker_command *command,
	struct harness_worker_event *event)
{
	struct agent_task_channel_enter_result entered;
	struct agent_task_channel_resource_result released;
	struct agent_task_cqe cqe;
	struct harness_task *task = find_task(command->task_id);

	memset(event, 0, sizeof(*event));
	event->operation = HARNESS_WORKER_COMPLETED;
	event->host_agent_id = host_agent_id;
	event->task_id = command->task_id;
	if (task == 0) {
		event->status = AGENT_STATUS_NOT_FOUND;
		return;
	}
	for (uint attempt = 0; attempt < 2048U; attempt++) {
		enter_channel(0, 0, 0, &entered);
		if (entered.status != AGENT_TASK_CHANNEL_OK) {
			event->status = entered.status;
			return;
		}
		if (entered.cq_tail == channel.cq_head + 1ULL)
			break;
		(void)sched_yield();
	}
	if (entered.cq_tail != channel.cq_head + 1ULL) {
		event->status = AGENT_STATUS_TIMEOUT;
		return;
	}
	volatile_read(&cqe, (const void *)&channel.cq->entries[
		channel.cq_head % AGENT_TASK_CHANNEL_CAPACITY], sizeof(cqe));
	if (cqe.request_id != task->sqe.request_id) {
		event->status = AGENT_STATUS_CONFLICT;
		return;
	}
	channel.cq_head++;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0, &entered);
	if (entered.status != AGENT_TASK_CHANNEL_OK) {
		event->status = entered.status;
		return;
	}
	resource_call(AGENT_TASK_RESOURCE_RELEASE, task->resource, 0, 0, 0, 0,
		      &released);
	if (released.status != AGENT_TASK_CHANNEL_OK) {
		event->status = released.status;
		return;
	}
	event->status = AGENT_STATUS_OK;
	event->terminal_status = cqe.status;
	event->terminal_generation = cqe.flags;
	event->context_sequence = cqe.context_sequence;
	memset(task, 0, sizeof(*task));
}

static void provider_loop(uint host_agent_id, int command_fd, int event_fd,
	int channel_owner)
{
	int stage_fd = -1;
	uint stage_handle = 0;
	uint64 stage_length = 0;
	uint64 stage_received = 0;
	int heartbeat_configured = 0;

	prime_worker_context(host_agent_id);
	if (channel_owner)
		setup_channel();
	for (;;) {
		memset(&harness_provider_request, 0,
		       sizeof(harness_provider_request));
		harness_provider_request.version = AGENT_TASK_DELEGATE_VERSION;
		harness_provider_request.size = sizeof(harness_provider_request);
		harness_provider_request.flags = AGENT_TASK_DELEGATE_CLAIM_F_WAIT;
		harness_provider_request.lifecycle = lifecycle;
		memset(&harness_provider_claim, 0, sizeof(harness_provider_claim));
		if (agent_task_delegate_claim(&harness_provider_request,
			&harness_provider_claim) != 0) {
			printf("AGENT_HARNESS PROVIDER_CLAIM host=%u syscall=-1 result=%d\n",
			       host_agent_id, harness_provider_claim.status);
			exit(2);
		}
		if (harness_provider_claim.status == AGENT_TASK_CHANNEL_STALE ||
		    harness_provider_claim.status == AGENT_TASK_CHANNEL_RETRY)
			continue;
		if (harness_provider_claim.status != AGENT_TASK_CHANNEL_OK ||
		    harness_provider_claim.state != AGENT_TASK_DELEGATE_STATE_CLAIMED) {
			printf("AGENT_HARNESS PROVIDER_CLAIM host=%u syscall=0 result=%d state=%u\n",
			       host_agent_id, harness_provider_claim.status,
			       harness_provider_claim.state);
			exit(3);
		}
		/* Heartbeat mutates IPC state and therefore runs only while the
		 * provider holds the claimed tool's Execution Contract effect lease. */
		if (!heartbeat_configured) {
			int heartbeat_status = agent_heartbeat_configure(100);

			if (heartbeat_status != AGENT_STATUS_OK) {
				printf("AGENT_HARNESS PROVIDER_HEARTBEAT host=%u status=%d\n",
				       host_agent_id, heartbeat_status);
				exit(5);
			}
			heartbeat_configured = 1;
		}
		memset(&harness_provider_event, 0, sizeof(harness_provider_event));
		harness_provider_event.operation = HARNESS_WORKER_CLAIMED;
		harness_provider_event.host_agent_id = host_agent_id;
		harness_provider_event.task_id =
			harness_provider_claim.descriptor.task_id;
		harness_provider_event.status = harness_provider_claim.status;
		memset(&harness_provider_info, 0, sizeof(harness_provider_info));
		if (agent_info(&harness_provider_info) != 0)
			exit(5);
		harness_provider_event.wait_sleep_count =
			harness_provider_info.wait_sleep_count;
		harness_provider_event.wait_wakeup_count =
			harness_provider_info.wait_wakeup_count;
		harness_provider_event.last_heartbeat_tick =
			harness_provider_info.last_heartbeat_tick;
		if (write_exact(event_fd, &harness_provider_event,
				sizeof(harness_provider_event)) < 0)
			exit(4);
		for (;;) {
			if (read_exact(command_fd, &harness_provider_command,
				       sizeof(harness_provider_command)) < 0) {
				/* Task and owner notifications both interrupt the governed
				 * pipe wait.  Query this exact provider binding before treating
				 * the wakeup as a cancellation offer. */
				memset(&harness_provider_terminal_query, 0,
				       sizeof(harness_provider_terminal_query));
				harness_provider_terminal_query.version =
					AGENT_TASK_DELEGATE_VERSION;
				harness_provider_terminal_query.size =
					sizeof(harness_provider_terminal_query);
				harness_provider_terminal_query.flags =
					AGENT_TASK_DELEGATE_COMPLETE_F_QUERY_TERMINAL;
				harness_provider_terminal_query.lifecycle =
					harness_provider_claim.lifecycle;
				harness_provider_terminal_query.owner_pid =
					harness_provider_claim.owner_pid;
				harness_provider_terminal_query.owner_control_id =
					harness_provider_claim.owner_control_id;
				harness_provider_terminal_query.channel_generation =
					harness_provider_claim.channel_generation;
				harness_provider_terminal_query.request_id =
					harness_provider_claim.request_id;
				harness_provider_terminal_query.slot_generation =
					harness_provider_claim.slot_generation;
				harness_provider_terminal_query.task_id =
					harness_provider_claim.descriptor.task_id;
				harness_provider_terminal_query.correlation_id =
					harness_provider_claim.descriptor.correlation_id;
				memset(&harness_provider_terminal_result, 0,
				       sizeof(harness_provider_terminal_result));
				if (agent_task_delegate_complete(
					    &harness_provider_terminal_query,
					    &harness_provider_terminal_result) != 0 ||
				    (harness_provider_terminal_result.status !=
					     AGENT_TASK_CHANNEL_OK &&
				     harness_provider_terminal_result.status !=
					     AGENT_TASK_CHANNEL_RETRY)) {
					printf("AGENT_HARNESS PROVIDER_QUERY host=%u status=%d state=%u terminal=%d generation=%u\n",
					       host_agent_id,
					       harness_provider_terminal_result.status,
					       harness_provider_terminal_result.state,
					       harness_provider_terminal_result.terminal_status,
					       harness_provider_terminal_result.terminal_generation);
					exit(4);
				}
				if (harness_provider_terminal_result.status ==
					    AGENT_TASK_CHANNEL_OK ||
				    harness_provider_terminal_result.terminal_generation == 0) {
					(void)sched_yield();
					continue;
				}
				memset(&harness_provider_command, 0,
				       sizeof(harness_provider_command));
				harness_provider_command.operation =
					HARNESS_COMMAND_COMPLETE;
				harness_provider_command.task_id =
					harness_provider_claim.descriptor.task_id;
				harness_provider_command.terminal_status =
					AGENT_STATUS_BAD_REQUEST;
				break;
			}
			if (harness_provider_command.operation ==
			    HARNESS_COMMAND_COMPLETE) {
				if (harness_provider_command.task_id !=
				    harness_provider_claim.descriptor.task_id) {
					printf("AGENT_HARNESS PROVIDER_PIPE host=%u phase=complete_binding expected=%lu actual=%lu\n",
					       host_agent_id,
					       harness_provider_claim.descriptor.task_id,
					       harness_provider_command.task_id);
					exit(4);
				}
				break;
			}
			if (harness_provider_command.operation ==
			    HARNESS_COMMAND_SUBMIT_TASK) {
				worker_submit_task(host_agent_id,
					&harness_provider_command,
					&harness_provider_event);
				if (write_exact(event_fd, &harness_provider_event,
						sizeof(harness_provider_event)) < 0)
					exit(4);
				continue;
			}
			if (harness_provider_command.operation ==
			    HARNESS_COMMAND_COLLECT_TASK) {
				worker_collect_task(host_agent_id,
					&harness_provider_command,
					&harness_provider_event);
				if (write_exact(event_fd, &harness_provider_event,
						sizeof(harness_provider_event)) < 0)
					exit(4);
				continue;
			}
			if (harness_provider_command.operation >=
					HARNESS_COMMAND_CATALOG_BEGIN &&
			    harness_provider_command.operation <=
					HARNESS_COMMAND_CATALOG_STALE) {
				if (harness_provider_command.task_id !=
					harness_provider_claim.descriptor.task_id ||
				    (harness_provider_claim.descriptor.task_type !=
					AGENT_TOOL_SEARCH_FILES &&
				     harness_provider_claim.descriptor.task_type !=
					AGENT_TOOL_READ_WORKSPACE_FILE)) {
					memset(&harness_provider_event, 0,
					       sizeof(harness_provider_event));
					harness_provider_event.operation =
						HARNESS_WORKER_CATALOG;
					harness_provider_event.host_agent_id =
						host_agent_id;
					harness_provider_event.task_id =
						harness_provider_command.task_id;
					harness_provider_event.status =
						AGENT_STATUS_DENIED;
				} else {
					worker_catalog_command(host_agent_id,
						&harness_provider_command,
						&harness_provider_event);
				}
				if (write_exact(event_fd, &harness_provider_event,
						sizeof(harness_provider_event)) < 0)
					exit(4);
				continue;
			}
			worker_artifact_command(host_agent_id,
				&harness_provider_command, &stage_fd,
				&stage_handle, &stage_length, &stage_received,
				&harness_provider_event);
			if (write_exact(event_fd, &harness_provider_event,
					sizeof(harness_provider_event)) < 0) {
				printf("AGENT_HARNESS PROVIDER_PIPE host=%u phase=artifact_event task=%lu\n",
				       host_agent_id,
				       harness_provider_claim.descriptor.task_id);
				exit(4);
			}
		}
		{
			struct agent_task_delegate_complete complete;
			struct agent_task_delegate_complete_result result;

			memset(&complete, 0, sizeof(complete));
			complete.version = AGENT_TASK_DELEGATE_VERSION;
			complete.size = sizeof(complete);
			complete.lifecycle = harness_provider_claim.lifecycle;
			complete.owner_pid = harness_provider_claim.owner_pid;
			complete.terminal_status =
				harness_provider_command.terminal_status;
			complete.owner_control_id =
				harness_provider_claim.owner_control_id;
			complete.channel_generation =
				harness_provider_claim.channel_generation;
			complete.request_id = harness_provider_claim.request_id;
			complete.slot_generation =
				harness_provider_claim.slot_generation;
			complete.task_id =
				harness_provider_claim.descriptor.task_id;
			complete.correlation_id =
				harness_provider_claim.descriptor.correlation_id;
			for (uint attempt = 0; attempt < 8U; attempt++) {
				memset(&result, 0, sizeof(result));
				if (agent_task_delegate_complete(&complete, &result) != 0) {
					printf("AGENT_HARNESS PROVIDER_CALL syscall=-1 result=%d attempt=%u\n",
					       result.status, attempt);
					harness_provider_event.operation = HARNESS_WORKER_FAILED;
					harness_provider_event.status = -5;
					harness_provider_event.terminal_status = result.status;
					(void)write_exact(event_fd, &harness_provider_event,
						sizeof(harness_provider_event));
					exit(5);
				}
				if (result.status == AGENT_TASK_CHANNEL_OK)
					break;
				if (result.status != AGENT_TASK_CHANNEL_RETRY ||
				    result.terminal_generation == 0) {
					printf("AGENT_HARNESS PROVIDER_CALL syscall=0 result=%d terminal=%d generation=%u attempt=%u\n",
					       result.status, result.terminal_status,
					       result.terminal_generation, attempt);
					harness_provider_event.operation = HARNESS_WORKER_FAILED;
					harness_provider_event.status = -6;
					harness_provider_event.terminal_status = result.status;
					harness_provider_event.terminal_generation =
						result.terminal_generation;
					(void)write_exact(event_fd, &harness_provider_event,
						sizeof(harness_provider_event));
					exit(6);
				}
				complete.flags = AGENT_TASK_DELEGATE_COMPLETE_F_ACK_TERMINAL;
				complete.ack_terminal_status = result.terminal_status;
				complete.terminal_generation = result.terminal_generation;
			}
			if (result.status != AGENT_TASK_CHANNEL_OK) {
				harness_provider_event.operation = HARNESS_WORKER_FAILED;
				harness_provider_event.status = -7;
				harness_provider_event.terminal_status = result.status;
				harness_provider_event.terminal_generation =
					result.terminal_generation;
				(void)write_exact(event_fd, &harness_provider_event,
					sizeof(harness_provider_event));
				exit(7);
			}
			/*
			 * Completion ends the Contract effect lease.  A pipe write after this
			 * point is intentionally rejected by direct-syscall enforcement.  The
			 * owner CQE below is the authoritative acknowledgement; the provider
			 * immediately returns to the exempt native claim syscall.
			 */
		}
	}
}

static __attribute__((noinline)) void spawn_worker(char *arguments)
{
	struct harness_worker *worker = 0;
	char *cursor = arguments;
	char *token;
	uint64 host_agent, capabilities, tools, resource, count, bytes, reads, summary;
	uint64 channel_owner;
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
	    (token = next_token(&cursor)) == 0 ||
	    parse_u64(token, &channel_owner) < 0 || channel_owner > 1 ||
	    next_token(&cursor) != 0 || host_agent == 0 ||
	    find_worker((uint)host_agent) != 0)
		fail("spawn_frame");
	for (uint index = 0; index < HARNESS_MAX_AGENTS; index++)
		if (!workers[index].active) {
			worker = &workers[index];
			break;
		}
	if (worker == 0)
		fail("spawn_worker_capacity");
	if (pipe(command_pipe) < 0)
		fail("spawn_command_pipe");
	if (pipe(event_pipe) < 0) {
		(void)close(command_pipe[0]);
		(void)close(command_pipe[1]);
		fail("spawn_event_pipe");
	}
	if (agent_scope_delegate_fd(command_pipe[0]) != AGENT_STATUS_OK ||
	    agent_scope_delegate_fd(event_pipe[1]) != AGENT_STATUS_OK)
		fail("spawn_pipe_delegate");
	memset(&harness_spawn_config, 0, sizeof(harness_spawn_config));
	harness_spawn_config.version = AGENT_RUNTIME_CONFIG_VERSION;
	harness_spawn_config.size = sizeof(harness_spawn_config);
	harness_spawn_config.operation = AGENT_RUNTIME_CONTROL_SPAWN;
	/* The fixed provider loop needs pipe transport in addition to the
	 * user-configured task authority. It never exposes these transport-only
	 * capabilities to the Host Agent policy. */
	harness_spawn_config.capabilities = capabilities | AGENT_CAP_TASK_ACCEPT |
			      AGENT_CAP_CONTENT_READ | AGENT_CAP_MESSAGE_SEND |
			      AGENT_CAP_META_WRITE | AGENT_CAP_ARTIFACT_WRITE |
			      AGENT_CAP_ORCHESTRATE;
	harness_spawn_config.allowed_tools = tools;
	if ((capabilities & AGENT_CAP_ORCHESTRATE) != 0)
		harness_spawn_config.allowed_tools |=
			1ULL << (AGENT_TOOL_DELEGATE_TASK - 1U);
	harness_spawn_config.resource_budget = (uint)resource;
	harness_spawn_config.artifact_count_limit = (uint)count;
	harness_spawn_config.artifact_bytes_limit = bytes;
	harness_spawn_config.artifact_read_limit = reads;
	harness_spawn_config.summary_high_watermark = (uint)summary;
	memset(&harness_spawn_result, 0, sizeof(harness_spawn_result));
	pid = agent_runtime_control(&harness_spawn_config,
		&harness_spawn_result);
	if (pid == 0) {
		(void)close(command_pipe[1]);
		(void)close(event_pipe[0]);
		/*
		 * A newly spawned worker inherits the controller's endpoints for every
		 * older worker.  Keeping them would consume two descriptors per peer and
		 * would also keep otherwise closed pipes artificially alive.  The child
		 * only owns its current command reader and event writer.
		 */
		for (uint index = 0; index < HARNESS_MAX_AGENTS; index++) {
			if (!workers[index].active)
				continue;
			(void)close(workers[index].command_fd);
			(void)close(workers[index].event_fd);
		}
		memset(tasks, 0, sizeof(tasks));
		memset(&channel, 0, sizeof(channel));
		memset(harness_node_consumed, 0,
		       sizeof(harness_node_consumed));
		harness_contract_ready = 0;
		provider_loop((uint)host_agent, command_pipe[0], event_pipe[1],
			      (int)channel_owner);
		exit(0);
	}
	if (pid < 0 || harness_spawn_result.status != AGENT_STATUS_OK ||
	    harness_spawn_result.control_id == 0) {
		(void)close(command_pipe[0]);
		(void)close(command_pipe[1]);
		(void)close(event_pipe[0]);
		(void)close(event_pipe[1]);
		fail("spawn_runtime");
	}
	(void)close(command_pipe[0]);
	(void)close(event_pipe[1]);
	if (agent_route_config(getpid(), pid, AGENT_IPC_ROUTE_TASK,
			       AGENT_IPC_ROUTE_GRANT) != AGENT_STATUS_OK)
		fail("spawn_route");
	for (uint index = 0; index < HARNESS_MAX_AGENTS; index++) {
		struct harness_worker *peer = &workers[index];

		if (!peer->active)
			continue;
		if (agent_route_config(peer->pid, pid, AGENT_IPC_ROUTE_TASK,
				       AGENT_IPC_ROUTE_GRANT) != AGENT_STATUS_OK ||
		    agent_route_config(pid, peer->pid, AGENT_IPC_ROUTE_TASK,
				       AGENT_IPC_ROUTE_GRANT) != AGENT_STATUS_OK)
			fail("spawn_peer_route");
	}
	memset(worker, 0, sizeof(*worker));
	worker->active = 1;
	worker->host_agent_id = (uint)host_agent;
	worker->pid = pid;
	worker->agent_id = harness_spawn_result.agent_id;
	worker->control_id = harness_spawn_result.control_id;
	worker->command_fd = command_pipe[1];
	worker->event_fd = event_pipe[0];
	printf("AGENT_HARNESS SPAWN %u %d %u %lu\n", worker->host_agent_id,
	       worker->pid, worker->agent_id, worker->control_id);
}

static __attribute__((noinline)) void submit_task(char *arguments)
{
	struct agent_task_delegate_descriptor descriptor;
	struct agent_task_channel_enter_result entered;
	struct harness_worker_command command;
	struct harness_worker_event event;
	struct harness_worker *owner;
	struct harness_worker *worker;
	struct harness_task *task;
	char *cursor = arguments;
	char *token;
	uint64 task_id, correlation_id, parent_task, parent_agent, target_agent;
	uint64 objective, input, result_handle, result_kind;
	uint64 required, tools, resource, reads, deadline, tool_id;
	char *revision = 0;

	if ((token = next_token(&cursor)) == 0 || parse_u64(token, &task_id) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &correlation_id) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &parent_task) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &parent_agent) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &target_agent) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &objective) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &input) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &result_handle) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &result_kind) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &required) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &tools) < 0 ||
	    (revision = next_token(&cursor)) == 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &resource) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &reads) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &deadline) < 0 ||
	    (token = next_token(&cursor)) == 0 || parse_u64(token, &tool_id) < 0 ||
	    next_token(&cursor) != 0 || task_id == 0 || correlation_id == 0 ||
	    find_task(task_id) != 0)
		fail("delegate_frame");
	worker = find_worker((uint)target_agent);
	owner = parent_task == 0 ? 0 : find_worker((uint)parent_agent);
	task = allocate_task((uint)tool_id);
	if (worker == 0)
		fail("delegate_target");
	if (task == 0)
		fail("delegate_contract_capacity");
	if (parent_task != 0 && (owner == 0 || owner == worker))
		fail("delegate_parent");
	memset(&descriptor, 0, sizeof(descriptor));
	descriptor.version = AGENT_TASK_DELEGATE_DESCRIPTOR_VERSION;
	descriptor.size = sizeof(descriptor);
	descriptor.target_pid = worker->pid;
	descriptor.target_agent_id = worker->agent_id;
	descriptor.task_type = (uint)tool_id;
	descriptor.target_control_id = worker->control_id;
	descriptor.task_id = task_id;
	descriptor.correlation_id = correlation_id;
	descriptor.parent_task_id = parent_task;
	descriptor.capsule_handle = (uint)objective;
	descriptor.input_artifact_handle = (uint)input;
	descriptor.result_artifact_handle = (uint)result_handle;
	descriptor.expected_result_type = (uint)result_kind;
	descriptor.required_capabilities = required;
	descriptor.allowed_tools = tools;
	if (parse_digest(revision, descriptor.workspace_revision_sha256) < 0)
		fail("delegate_revision");
	descriptor.resource_budget = (uint)resource;
	descriptor.read_budget = (uint)reads;
	descriptor.deadline_tick = deadline;
	task->owner_host_agent_id = (uint)parent_agent;
	task->host_agent_id = (uint)target_agent;
	task->task_id = task_id;
	task->correlation_id = correlation_id;
	if (owner != 0) {
		ensure_contract();
		task->owner_managed = 1;
		memset(&command, 0, sizeof(command));
		command.operation = HARNESS_COMMAND_SUBMIT_TASK;
		command.task_id = task_id;
		command.tool_id = (uint)tool_id;
		command.host_context_sequence = target_agent;
		command.payload.submit.descriptor = descriptor;
		command.payload.submit.contract = harness_contract;
		command.payload.submit.node_id = task->node_id;
		memcpy(command.payload.submit.schema_digest,
		       harness_nodes[task->node_id].schema_digest,
		       sizeof(command.payload.submit.schema_digest));
		if (write_exact(owner->command_fd, &command, sizeof(command)) < 0 ||
		    read_exact(owner->event_fd, &event, sizeof(event)) < 0 ||
		    event.operation != HARNESS_WORKER_SUBMITTED ||
		    event.task_id != task_id || event.status != AGENT_TASK_CHANNEL_OK) {
			printf("AGENT_HARNESS OWNER_SUBMIT task=%lu status=%d terminal=%d reason=%u\n",
			       task_id, event.status, event.terminal_status,
			       event.terminal_generation);
			fail("delegate_owner_submit");
		}
		task->sqe.ring_generation = event.channel_generation;
		task->sqe.request_id = event.request_id;
		task->sqe.slot_generation = event.slot_generation;
		goto wait_claim;
	}
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
	if (entered.status != AGENT_TASK_CHANNEL_OK || entered.submitted != 1) {
		printf("AGENT_HARNESS SUBMIT status=%d submitted=%u tool=%u node=%u task=%lu\n",
		       entered.status, entered.submitted, task->tool_id,
		       task->node_id, task_id);
		fail("delegate_submit");
	}
	if (entered.cq_tail > channel.cq_head) {
		struct agent_task_cqe early;

		volatile_read(&early, (const void *)&channel.cq->entries[
			channel.cq_head % AGENT_TASK_CHANNEL_CAPACITY], sizeof(early));
		printf("AGENT_HARNESS EARLY task=%lu status=%d reason=%u request=%lu\n",
		       task_id, early.status, early.decision_reason, early.request_id);
		fail("delegate_early_terminal");
	}
wait_claim:
	if (read_exact(worker->event_fd, &event, sizeof(event)) < 0 ||
	    event.operation != HARNESS_WORKER_CLAIMED || event.task_id != task_id)
		fail("delegate_claim");
	if (event.wait_sleep_count >= worker->wait_sleep_count)
		harness_worker_wait_sleep_count +=
			event.wait_sleep_count - worker->wait_sleep_count;
	if (event.wait_wakeup_count >= worker->wait_wakeup_count)
		harness_worker_wait_wakeup_count +=
			event.wait_wakeup_count - worker->wait_wakeup_count;
	worker->wait_sleep_count = event.wait_sleep_count;
	worker->wait_wakeup_count = event.wait_wakeup_count;
	worker->last_heartbeat_tick = event.last_heartbeat_tick;
	if (event.last_heartbeat_tick > harness_worker_last_heartbeat_tick)
		harness_worker_last_heartbeat_tick = event.last_heartbeat_tick;
	harness_tasks_submitted++;
	harness_task_wait_count++;
	printf("AGENT_HARNESS CLAIM %lu %u %u\n", task_id,
	       (uint)parent_agent, (uint)target_agent);
}

static __attribute__((noinline)) void complete_task(char *arguments)
{
	struct agent_task_channel_enter_result entered;
	struct agent_task_channel_resource_result released;
	struct harness_worker_command command;
	struct harness_worker_event event;
	struct agent_task_cqe cqe;
	struct harness_task *task;
	struct harness_worker *owner;
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
	owner = task == 0 || !task->owner_managed ? 0 :
		find_worker(task->owner_host_agent_id);
	if (task == 0 || worker == 0 || (task->owner_managed && owner == 0))
		fail("complete_task");
	memset(&command, 0, sizeof(command));
	command.operation = HARNESS_COMMAND_COMPLETE;
	command.task_id = task_id;
	command.terminal_status = status;
	command.result_artifact_handle = (uint)result_handle;
	if (write_exact(worker->command_fd, &command, sizeof(command)) < 0) {
		printf("AGENT_HARNESS PROVIDER_PIPE phase=write pid=%d\n",
		       worker->pid);
		fail("complete_provider");
	}
	if (task->owner_managed) {
		memset(&command, 0, sizeof(command));
		command.operation = HARNESS_COMMAND_COLLECT_TASK;
		command.task_id = task_id;
		if (write_exact(owner->command_fd, &command, sizeof(command)) < 0 ||
		    read_exact(owner->event_fd, &event, sizeof(event)) < 0 ||
		    event.operation != HARNESS_WORKER_COMPLETED ||
		    event.task_id != task_id || event.status != AGENT_STATUS_OK)
			fail("complete_owner_collect");
		if (event.terminal_status != status &&
		    event.terminal_status != AGENT_STATUS_TIMEOUT &&
		    event.terminal_status != AGENT_STATUS_CANCELLED)
			fail("complete_owner_cqe");
		printf("AGENT_HARNESS COMPLETE %lu %d %u %lu\n", task_id,
		       event.terminal_status, event.terminal_generation,
		       event.context_sequence);
		harness_tasks_completed++;
		memset(task, 0, sizeof(*task));
		return;
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
	harness_tasks_completed++;
	memset(task, 0, sizeof(*task));
}

static __attribute__((noinline)) void request_task_cancel(char *arguments)
{
	struct agent_task_delegate_complete request;
	struct agent_task_delegate_complete_result result;
	struct harness_task *task;
	struct harness_worker *owner;
	char *cursor = arguments;
	char *token;
	uint64 task_id;

	if ((token = next_token(&cursor)) == 0 ||
	    parse_u64(token, &task_id) < 0 || task_id == 0 ||
	    next_token(&cursor) != 0)
		fail("cancel_frame");
	task = find_task(task_id);
	owner = task == 0 || !task->owner_managed ? 0 :
		find_worker(task->owner_host_agent_id);
	if (task == 0 || task->sqe.ring_generation == 0 ||
	    task->sqe.request_id == 0 || task->sqe.slot_generation == 0 ||
	    task->correlation_id == 0 || (task->owner_managed && owner == 0))
		fail("cancel_task");
	memset(&request, 0, sizeof(request));
	request.version = AGENT_TASK_DELEGATE_VERSION;
	request.size = sizeof(request);
	request.flags = AGENT_TASK_DELEGATE_COMPLETE_F_REQUEST_CANCEL;
	request.lifecycle = lifecycle;
	request.owner_pid = owner == 0 ? getpid() : owner->pid;
	request.terminal_status = AGENT_STATUS_CANCELLED;
	request.owner_control_id = owner == 0 ?
		harness_service_self.control_id : owner->control_id;
	request.channel_generation = task->sqe.ring_generation;
	request.request_id = task->sqe.request_id;
	request.slot_generation = task->sqe.slot_generation;
	request.task_id = task_id;
	request.correlation_id = task->correlation_id;
	memset(&result, 0, sizeof(result));
	if (agent_task_delegate_complete(&request, &result) != 0)
		fail("cancel_syscall");
	printf("AGENT_HARNESS CANCEL %lu %d %u %d %u\n", task_id,
	       result.status, result.state, result.terminal_status,
	       result.terminal_generation);
}

static __attribute__((noinline)) void collect_task_cancel(char *arguments)
{
	struct agent_task_channel_enter_result entered;
	struct agent_task_channel_resource_result released;
	struct harness_worker_command command;
	struct harness_worker_event event;
	struct agent_task_cqe cqe;
	struct harness_task *task;
	struct harness_worker *owner;
	char *cursor = arguments;
	char *token;
	uint64 task_id;

	if ((token = next_token(&cursor)) == 0 ||
	    parse_u64(token, &task_id) < 0 || task_id == 0 ||
	    next_token(&cursor) != 0)
		fail("cancel_collect_frame");
	task = find_task(task_id);
	owner = task == 0 || !task->owner_managed ? 0 :
		find_worker(task->owner_host_agent_id);
	if (task == 0 || (task->owner_managed && owner == 0))
		fail("cancel_collect_task");
	if (task->owner_managed) {
		memset(&command, 0, sizeof(command));
		command.operation = HARNESS_COMMAND_COLLECT_TASK;
		command.task_id = task_id;
		if (write_exact(owner->command_fd, &command, sizeof(command)) < 0 ||
		    read_exact(owner->event_fd, &event, sizeof(event)) < 0 ||
		    event.operation != HARNESS_WORKER_COMPLETED ||
		    event.task_id != task_id || event.status != AGENT_STATUS_OK ||
		    event.terminal_status != AGENT_STATUS_CANCELLED)
			fail("cancel_collect_owner");
		printf("AGENT_HARNESS CANCELLED %lu %d %u %lu\n", task_id,
		       event.terminal_status, event.terminal_generation,
		       event.context_sequence);
		harness_tasks_completed++;
		memset(task, 0, sizeof(*task));
		return;
	}
	for (uint attempt = 0; attempt < 2048U; attempt++) {
		enter_channel(0, 0, 0, &entered);
		if (entered.status != AGENT_TASK_CHANNEL_OK)
			fail("cancel_collect_poll");
		if (entered.cq_tail == channel.cq_head + 1ULL)
			break;
		(void)sched_yield();
	}
	if (entered.cq_tail != channel.cq_head + 1ULL)
		fail("cancel_collect_timeout");
	volatile_read(&cqe, (const void *)&channel.cq->entries[
		channel.cq_head % AGENT_TASK_CHANNEL_CAPACITY], sizeof(cqe));
	if (cqe.request_id != task->sqe.request_id ||
	    cqe.status != AGENT_STATUS_CANCELLED)
		fail("cancel_collect_cqe");
	channel.cq_head++;
	enter_channel(AGENT_TASK_CHANNEL_ENTER_F_DRAIN, 0, 0, &entered);
	if (entered.status != AGENT_TASK_CHANNEL_OK)
		fail("cancel_collect_ack");
	resource_call(AGENT_TASK_RESOURCE_RELEASE, task->resource, 0, 0, 0, 0,
		      &released);
	if (released.status != AGENT_TASK_CHANNEL_OK)
		fail("cancel_collect_release");
	printf("AGENT_HARNESS CANCELLED %lu %d %u %lu\n", task_id,
	       cqe.status, cqe.flags, cqe.context_sequence);
	harness_tasks_completed++;
	memset(task, 0, sizeof(*task));
}

static __attribute__((noinline)) void artifact_serial_command(uint operation,
	char *arguments)
{
	struct harness_worker_command command;
	struct harness_worker_event event;
	struct harness_worker *worker;
	char *cursor = arguments;
	char *token;
	uint64 host_agent, task_id, handle, kind, flags, tool_id;
	uint64 length, host_sequence, cause, offset;
	uint chunk_length = 0;

	memset(&command, 0, sizeof(command));
	command.operation = operation;
	if ((token = next_token(&cursor)) == 0 ||
	    parse_u64(token, &host_agent) < 0 ||
	    (token = next_token(&cursor)) == 0 ||
	    parse_u64(token, &task_id) < 0 ||
	    (token = next_token(&cursor)) == 0 ||
	    parse_u64(token, &handle) < 0 || host_agent == 0 ||
	    task_id == 0 || handle == 0)
		fail("artifact_frame");
	command.task_id = task_id;
	command.artifact_handle = (uint)handle;
	if (operation == HARNESS_COMMAND_ARTIFACT_CHUNK) {
		if ((token = next_token(&cursor)) == 0 ||
		    parse_u64(token, &offset) < 0 ||
		    (token = next_token(&cursor)) == 0 ||
		    parse_hex_bytes(token, command.payload.chunk,
			    sizeof(command.payload.chunk), &chunk_length) < 0 ||
		    next_token(&cursor) != 0)
			fail("artifact_chunk_frame");
		command.artifact_offset = offset;
		command.chunk_length = chunk_length;
	} else {
		if ((token = next_token(&cursor)) == 0 || parse_u64(token, &kind) < 0 ||
		    (token = next_token(&cursor)) == 0 || parse_u64(token, &flags) < 0 ||
		    (token = next_token(&cursor)) == 0 || parse_u64(token, &tool_id) < 0 ||
		    (token = next_token(&cursor)) == 0 || parse_u64(token, &length) < 0 ||
		    (token = next_token(&cursor)) == 0 || parse_u64(token, &host_sequence) < 0 ||
		    (token = next_token(&cursor)) == 0 || parse_u64(token, &cause) < 0 ||
		    (token = next_token(&cursor)) == 0 ||
		    parse_digest(token, command.content_sha256) < 0 ||
		    next_token(&cursor) != 0)
			fail("artifact_control_frame");
		command.artifact_kind = (uint)kind;
		command.artifact_flags = (uint)flags;
		command.tool_id = (uint)tool_id;
		command.artifact_length = length;
		command.host_context_sequence = host_sequence;
		command.cause_sequence = cause;
	}
	worker = find_worker((uint)host_agent);
	if (worker == 0 || write_exact(worker->command_fd, &command,
					 sizeof(command)) < 0 ||
	    read_exact(worker->event_fd, &event, sizeof(event)) < 0 ||
	    event.operation != HARNESS_WORKER_ARTIFACT ||
	    event.host_agent_id != (uint)host_agent ||
	    event.task_id != task_id)
		fail("artifact_worker");
	if (operation == HARNESS_COMMAND_ARTIFACT_SEAL &&
	    event.status == AGENT_STATUS_OK) {
		harness_artifact_count++;
		harness_artifact_bytes += event.artifact_length;
	}
	printf("AGENT_HARNESS ARTIFACT %u %u %lu %d %u %u %u %lu %lu %u %lu ",
	       operation, (uint)host_agent, task_id, event.status,
	       event.artifact_handle, event.artifact_kind, event.artifact_flags,
	       event.artifact_length, event.context_sequence,
	       event.producer_agent_id, event.producer_control_id);
	print_digest(event.content_sha256);
	printf("\n");
}

static __attribute__((noinline)) void catalog_serial_command(uint operation,
	char *arguments)
{
	struct harness_worker_command command;
	struct harness_worker_event event;
	struct harness_worker *worker;
	char *cursor = arguments;
	char *token;
	uint64 host_agent;
	uint64 task_id;
	uint summary_length = 0;

	memset(&command, 0, sizeof(command));
	command.operation = operation;
	if ((token = next_token(&cursor)) == 0 ||
	    parse_u64(token, &host_agent) < 0 ||
	    (token = next_token(&cursor)) == 0 ||
	    parse_u64(token, &task_id) < 0 || host_agent == 0 || task_id == 0)
		fail("catalog_frame");
	command.task_id = task_id;
	if (operation == HARNESS_COMMAND_CATALOG_BEGIN) {
		uint64 count;
		uint64 page_cursor;
		uint64 eof;

		if ((token = next_token(&cursor)) == 0 ||
		    parse_u64(token, &count) < 0 || count == 0 ||
		    count > HARNESS_CATALOG_MAX ||
		    (token = next_token(&cursor)) == 0 ||
		    parse_u64(token, &page_cursor) < 0 ||
		    (token = next_token(&cursor)) == 0 ||
		    parse_u64(token, &eof) < 0 || eof > 1 ||
		    (token = next_token(&cursor)) == 0 ||
		    parse_digest(token, command.content_sha256) < 0 ||
		    (token = next_token(&cursor)) == 0 ||
		    parse_digest(token, command.payload.chunk) < 0 ||
		    next_token(&cursor) != 0)
			fail("catalog_begin_frame");
		command.artifact_handle = (uint)count;
		command.artifact_offset = page_cursor;
		command.host_context_sequence = eof;
	} else if (operation == HARNESS_COMMAND_CATALOG_ENTRY) {
		uint64 index;
		uint64 count;
		uint64 size;
		char *stage = 0;
		char *kind = 0;

		if ((token = next_token(&cursor)) == 0 ||
		    parse_u64(token, &index) < 0 ||
		    (token = next_token(&cursor)) == 0 ||
		    parse_u64(token, &count) < 0 || count == 0 ||
		    count > HARNESS_CATALOG_MAX || index >= count ||
		    (token = next_token(&cursor)) == 0 ||
		    parse_u64(token, &size) < 0 ||
		    (token = next_token(&cursor)) == 0 ||
		    parse_digest(token, command.content_sha256) < 0 ||
		    (token = next_token(&cursor)) == 0 ||
		    parse_digest(token,
			command.payload.catalog_entry.revision) < 0 ||
		    (stage = next_token(&cursor)) == 0 ||
		    (kind = next_token(&cursor)) == 0 ||
		    strlen(stage) >= AGENT_FILE_FIELD_SIZE ||
		    strlen(kind) >= AGENT_FILE_FIELD_SIZE ||
		    (token = next_token(&cursor)) == 0 ||
		    parse_hex_bytes(token,
			(unsigned char *)command.payload.catalog_entry.summary,
			AGENT_FILE_SUMMARY_SIZE - 1U, &summary_length) < 0 ||
		    summary_length == 0 || next_token(&cursor) != 0)
			fail("catalog_entry_frame");
		command.payload.catalog_entry.index = (uint)index;
		command.payload.catalog_entry.count = (uint)count;
		command.payload.catalog_entry.size = size;
		strcpy(command.payload.catalog_entry.stage, stage);
		strcpy(command.payload.catalog_entry.kind, kind);
		command.payload.catalog_entry.summary[summary_length] = 0;
	} else if (operation == HARNESS_COMMAND_CATALOG_QUERY) {
		char *stage = next_token(&cursor);
		char *kind = next_token(&cursor);
		char *status = next_token(&cursor);

		if (stage == 0 || kind == 0 || status == 0 ||
		    strlen(stage) >= AGENT_FILE_FIELD_SIZE ||
		    strlen(kind) >= AGENT_FILE_FIELD_SIZE ||
		    strlen(status) >= AGENT_FILE_FIELD_SIZE ||
		    (token = next_token(&cursor)) == 0 ||
		    (strcmp(token, "-") != 0 &&
		     parse_hex_bytes(token,
			(unsigned char *)command.payload.catalog_query.summary,
			AGENT_FILE_SUMMARY_SIZE - 1U, &summary_length) < 0) ||
		    next_token(&cursor) != 0)
			fail("catalog_query_frame");
		if (strcmp(stage, "-") != 0)
			strcpy(command.payload.catalog_query.stage, stage);
		if (strcmp(kind, "-") != 0)
			strcpy(command.payload.catalog_query.kind, kind);
		if (strcmp(status, "-") != 0)
			strcpy(command.payload.catalog_query.status, status);
		if (summary_length != 0)
			command.payload.catalog_query.summary[summary_length] = 0;
	} else if ((operation != HARNESS_COMMAND_CATALOG_COMMIT &&
		    operation != HARNESS_COMMAND_CATALOG_STALE) ||
		   next_token(&cursor) != 0) {
		fail("catalog_control_frame");
	}
	worker = find_worker((uint)host_agent);
	if (worker == 0 || write_exact(worker->command_fd, &command,
					 sizeof(command)) < 0 ||
	    read_exact(worker->event_fd, &event, sizeof(event)) < 0 ||
	    event.operation != HARNESS_WORKER_CATALOG ||
	    event.host_agent_id != (uint)host_agent ||
		event.task_id != task_id)
		fail("catalog_worker");
	if (event.status == AGENT_STATUS_OK) {
		harness_catalog_records = event.catalog_records;
		harness_catalog_candidates = event.catalog_candidates;
		harness_catalog_watch_events = event.catalog_watch_events;
		if (event.catalog_reuse)
			harness_catalog_reuse_count++;
		if (operation == HARNESS_COMMAND_CATALOG_BEGIN)
			harness_catalog_state_code = event.catalog_reuse ? 2U : 1U;
		else if (operation == HARNESS_COMMAND_CATALOG_COMMIT ||
			 operation == HARNESS_COMMAND_CATALOG_QUERY)
			harness_catalog_state_code = 2U;
		else if (operation == HARNESS_COMMAND_CATALOG_STALE)
			harness_catalog_state_code = 0U;
	}
	printf("AGENT_HARNESS CATALOG %u %u %lu %d %u %u %u %u %u %u %lu ",
	       operation, (uint)host_agent, task_id, event.status,
	       event.catalog_reuse, event.catalog_records,
	       event.catalog_candidates, event.catalog_used_index,
	       event.catalog_mask, event.catalog_watch_events,
	       event.catalog_fs_generation);
	print_digest(event.content_sha256);
	for (uint index = 0; index < HARNESS_CATALOG_MAX; index++)
		printf(" %lu:%lu:%lu", event.catalog_dev[index],
		       event.catalog_inum[index],
		       event.catalog_incarnation[index]);
	printf("\n");
}

static void run_service(void)
{
	memset(&harness_service_query, 0, sizeof(harness_service_query));
	memset(&harness_service_self, 0, sizeof(harness_service_self));
	harness_service_query.version = AGENT_RUNTIME_CONFIG_VERSION;
	harness_service_query.size = sizeof(harness_service_query);
	harness_service_query.operation = AGENT_RUNTIME_CONTROL_QUERY_SELF;
	if (agent_runtime_control(&harness_service_query,
		&harness_service_self) != AGENT_STATUS_OK)
		fail("runtime_self");
	memset(&harness_service_lifecycle, 0,
	       sizeof(harness_service_lifecycle));
	if (agent_workflow_lifecycle_info(&harness_service_lifecycle, 0) !=
	    AGENT_STATUS_OK || !harness_service_lifecycle.charged)
		fail("lifecycle");
	lifecycle = harness_service_lifecycle.key;
	prime_context();
	setup_channel();
	printf("AGENT_HARNESS READY %lu %lu %u %lu\n", lifecycle.id,
	       lifecycle.generation, harness_service_self.agent_id,
	       harness_service_self.control_id);
	for (;;) {
		int length = read_line(harness_service_line,
				       sizeof(harness_service_line));
		char *arguments;

		if (length < 0)
			fail("serial_read");
		arguments = harness_service_line;
		while (*arguments != 0 && *arguments != ' ')
			arguments++;
		if (*arguments == ' ')
			*arguments++ = 0;
		else
			arguments = harness_service_line + length;
		if (strcmp(harness_service_line, "SPAWN") == 0)
			spawn_worker(arguments);
		else if (strcmp(harness_service_line, "DELEGATE") == 0)
			submit_task(arguments);
		else if (strcmp(harness_service_line, "COMPLETE") == 0)
			complete_task(arguments);
		else if (strcmp(harness_service_line, "CANCEL") == 0)
			request_task_cancel(arguments);
		else if (strcmp(harness_service_line, "COLLECT_CANCEL") == 0)
			collect_task_cancel(arguments);
		else if (strcmp(harness_service_line, "ARTIFACT_BEGIN") == 0)
			artifact_serial_command(HARNESS_COMMAND_ARTIFACT_BEGIN,
				arguments);
		else if (strcmp(harness_service_line, "ARTIFACT_CHUNK") == 0)
			artifact_serial_command(HARNESS_COMMAND_ARTIFACT_CHUNK,
				arguments);
		else if (strcmp(harness_service_line, "ARTIFACT_SEAL") == 0)
			artifact_serial_command(HARNESS_COMMAND_ARTIFACT_SEAL,
				arguments);
		else if (strcmp(harness_service_line, "ARTIFACT_BIND") == 0)
			artifact_serial_command(HARNESS_COMMAND_ARTIFACT_BIND,
				arguments);
		else if (strcmp(harness_service_line, "CATALOG_BEGIN") == 0)
			catalog_serial_command(HARNESS_COMMAND_CATALOG_BEGIN,
				arguments);
		else if (strcmp(harness_service_line, "CATALOG_ENTRY") == 0)
			catalog_serial_command(HARNESS_COMMAND_CATALOG_ENTRY,
				arguments);
		else if (strcmp(harness_service_line, "CATALOG_COMMIT") == 0)
			catalog_serial_command(HARNESS_COMMAND_CATALOG_COMMIT,
				arguments);
		else if (strcmp(harness_service_line, "CATALOG_QUERY") == 0)
			catalog_serial_command(HARNESS_COMMAND_CATALOG_QUERY,
				arguments);
		else if (strcmp(harness_service_line, "CATALOG_STALE") == 0)
			catalog_serial_command(HARNESS_COMMAND_CATALOG_STALE,
				arguments);
		else if (strcmp(harness_service_line, "TICK") == 0) {
			struct agent_info current;

			memset(&current, 0, sizeof(current));
			if (agent_info(&current) != 0)
				fail("tick_query");
			printf("AGENT_HARNESS TICK %lu\n", current.current_tick);
		}
		else if (strcmp(harness_service_line, "STATUS") == 0)
			print_status();
		else if (strcmp(harness_service_line, "FENCE") == 0)
			seal_workflow(arguments);
		else if (strcmp(harness_service_line, "CLOSE") == 0) {
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
