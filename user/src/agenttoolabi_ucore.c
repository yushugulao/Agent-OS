#include <agent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syscall.h>
#include <unistd.h>

static struct agent_param_v2 params[AGENT_TOOL_PARAM_MAX];
static struct agent_request_v2 request;
static struct {
	uint64 before;
	struct agent_response_v2 value;
	uint64 after;
} response_buffer;
static struct agent_tool_desc_v2 tools_v2[AGENT_TOOL_COUNT];
static struct agent_tool_desc tools_v1[AGENT_TOOL_COUNT];

#define USER_BUFFER_SENTINEL 0xd1a65afe8badf00dULL

struct expected_tool_schema {
	int tool_id;
	uint flags;
	uint param_count;
	const char *name;
	const char *params;
};

static const struct expected_tool_schema expected_tools[AGENT_TOOL_COUNT] = {
	{ AGENT_TOOL_ECHO, AGENT_TOOL_F_CALLABLE, 3, "echo", "payload:string,arg0:uint64,arg1:uint64" },
	{ AGENT_TOOL_PID_INFO, AGENT_TOOL_F_CALLABLE, 0, "pid_info", "none" },
	{ AGENT_TOOL_CTX_STAT, AGENT_TOOL_F_DEPRECATED, 0, "ctx_stat", "none" },
	{ AGENT_TOOL_QUERY_PROCESS, AGENT_TOOL_F_CALLABLE, 1, "query_process", "type?:uint64" },
	{ AGENT_TOOL_GET_SYSTEM_STATUS, AGENT_TOOL_F_CALLABLE, 0, "get_system_status", "none" },
	{ AGENT_TOOL_CONTEXT_STATUS, AGENT_TOOL_F_CALLABLE, 0, "context_status", "none" },
	{ AGENT_TOOL_QUERY_FILE, AGENT_TOOL_F_CALLABLE, 1, "query_file", "path:string" },
	{ AGENT_TOOL_SEND_MESSAGE, AGENT_TOOL_F_CALLABLE, 2, "send_message", "target_pid:uint64,message:string" },
	{ AGENT_TOOL_READ_MESSAGE, AGENT_TOOL_F_CALLABLE, 0, "read_message", "none" },
	{ AGENT_TOOL_METADATA_INIT, AGENT_TOOL_F_CALLABLE, 0, "metadata_init", "none" },
	{ AGENT_TOOL_READ_FILE_SUMMARY, AGENT_TOOL_F_CALLABLE, 1, "read_file_summary", "selector:string" },
	{ AGENT_TOOL_DEPENDENCY_QUERY, AGENT_TOOL_F_CALLABLE, 1, "dependency_query", "label:string" },
	{ AGENT_TOOL_CAPABILITY_CHECK, AGENT_TOOL_F_CALLABLE, 2, "capability_check", "role:uint64,action:string" },
	{ AGENT_TOOL_RERUN_STAGE, AGENT_TOOL_F_DEPRECATED, 2, "rerun_stage", "role?:uint64,stage:string" },
	{ AGENT_TOOL_WRITE_REPORT, AGENT_TOOL_F_DEPRECATED, 2, "write_report", "role?:uint64,payload:string" },
	{ AGENT_TOOL_AGENT_WATCH, AGENT_TOOL_F_CALLABLE, 2, "agent_watch", "event_type:uint64,filter:string" },
	{ AGENT_TOOL_AGENT_WAIT, AGENT_TOOL_F_SYSCALL_ONLY, 1, "agent_wait", "timeout:uint64" },
	{ AGENT_TOOL_HEARTBEAT_CONFIGURE, AGENT_TOOL_F_CALLABLE, 1, "heartbeat_configure", "interval:uint64" },
	{ AGENT_TOOL_CONTEXT_PUSH, AGENT_TOOL_F_SYSCALL_ONLY, 1, "context_push", "record:string" },
	{ AGENT_TOOL_READ_FILE_DIGEST, AGENT_TOOL_F_CALLABLE, 1, "read_file_digest", "selector:string" },
	{ AGENT_TOOL_ACTION_COMMIT, AGENT_TOOL_F_CALLABLE, 2, "action_commit", "role?:uint64,selector:string" },
	{ AGENT_TOOL_ARTIFACT_UPDATE, AGENT_TOOL_F_CALLABLE, 2, "artifact_update", "role?:uint64,selector:string" },
	{ AGENT_TOOL_LLM_REQUEST, AGENT_TOOL_F_CALLABLE, 2, "llm_request", "target_pid:uint64,prompt_summary:string" },
	{ AGENT_TOOL_LLM_RESPONSE, AGENT_TOOL_F_CALLABLE, 2, "llm_response", "target_pid:uint64,reply_summary:string" },
	{ AGENT_TOOL_DEPENDENCY_UPDATE, AGENT_TOOL_F_CALLABLE, 1, "dependency_update", "selector:string" },
	{ AGENT_TOOL_DELEGATE_TASK, AGENT_TOOL_F_SYSCALL_ONLY, 0, "delegate_task", "none" },
	{ AGENT_TOOL_APPLY_PATCH, AGENT_TOOL_F_BROKERED, 3, "apply_patch", "artifact_handle:uint64,expected_rev:uint64,path:string" },
	{ AGENT_TOOL_WRITE_FILE, AGENT_TOOL_F_BROKERED, 3, "write_file", "artifact_handle:uint64,expected_rev:uint64,path:string" },
	{ AGENT_TOOL_SEARCH_FILES, AGENT_TOOL_F_BROKERED, 0, "search_files", "none" },
	{ AGENT_TOOL_READ_WORKSPACE_FILE, AGENT_TOOL_F_BROKERED, 0, "read_workspace_file", "none" },
	{ AGENT_TOOL_INSPECT_SYSTEM, AGENT_TOOL_F_BROKERED, 0, "inspect_system", "none" },
	{ AGENT_TOOL_BUILD_UCORE_PROGRAM, AGENT_TOOL_F_BROKERED, 0, "build_ucore_program", "none" },
	{ AGENT_TOOL_RUN_UCORE_PROGRAM, AGENT_TOOL_F_BROKERED, 0, "run_ucore_program", "none" },
};

static void check(int ok, const char *message)
{
	if (!ok) {
		printf("agenttoolabi_ucore: check failed: %s\n", message);
		exit(1);
	}
}

static void response_buffer_init(void)
{
	memset(&response_buffer, 0, sizeof(response_buffer));
	response_buffer.before = USER_BUFFER_SENTINEL;
	response_buffer.after = USER_BUFFER_SENTINEL;
}

static void response_buffer_check(void)
{
	check(response_buffer.before == USER_BUFFER_SENTINEL &&
	      response_buffer.after == USER_BUFFER_SENTINEL,
	      "v2 response buffer sentinel");
}

static void param_uint(uint index, const char *key, uint64 value)
{
	memset(&params[index], 0, sizeof(params[index]));
	params[index].version = AGENT_PARAM_VERSION;
	params[index].size = sizeof(params[index]);
	params[index].type = AGENT_PARAM_UINT64;
	params[index].value_size = sizeof(params[index].value.uint64_value);
	strcpy(params[index].key, key);
	params[index].value.uint64_value = value;
}

static void param_string(uint index, const char *key, const char *value)
{
	memset(&params[index], 0, sizeof(params[index]));
	params[index].version = AGENT_PARAM_VERSION;
	params[index].size = sizeof(params[index]);
	params[index].type = AGENT_PARAM_STRING;
	params[index].value_size = strlen(value) + 1;
	strcpy(params[index].key, key);
	strcpy(params[index].value.string_value, value);
}

static void request_init(int tool_id, const char *name, uint count)
{
	memset(&request, 0, sizeof(request));
	response_buffer_init();
	request.version = AGENT_CALL_VERSION_V2;
	request.size = sizeof(request);
	request.tool_id = tool_id;
	request.param_count = count;
	request.request_id = 9200 + count;
	request.params = count ? (uint64)params : 0;
	if (name)
		strcpy(request.tool_name, name);
}

static void expect_status(int status, const char *message)
{
	check(sys_tool_call(&request, &response_buffer.value) == 0, message);
	response_buffer_check();
	check(response_buffer.value.version == AGENT_CALL_VERSION_V2,
	      "response version");
	check(response_buffer.value.size == sizeof(response_buffer.value),
	      "response size");
	check(response_buffer.value.status == status, message);
}

static void check_lists(void)
{
	int count;

	memset(tools_v1, 0, sizeof(tools_v1));
	count = agent_tool_list(tools_v1, AGENT_TOOL_COUNT);
	check(count == AGENT_TOOL_COUNT, "legacy tool count");
	memset(tools_v2, 0, sizeof(tools_v2));
	count = sys_tool_list(tools_v2, AGENT_TOOL_COUNT);
	check(count == AGENT_TOOL_COUNT, "v2 tool count");
	for (uint i = 0; i < AGENT_TOOL_COUNT; i++) {
		const struct expected_tool_schema *expected = &expected_tools[i];

		check(tools_v1[i].tool_id == expected->tool_id &&
		      tools_v1[i].flags == expected->flags &&
		      strcmp(tools_v1[i].name, expected->name) == 0 &&
		      strcmp(tools_v1[i].params, expected->params) == 0,
		      "legacy full schema table");
		check(tools_v2[i].version == AGENT_CALL_VERSION_V2 &&
		      tools_v2[i].size == sizeof(tools_v2[i]) &&
		      tools_v2[i].tool_id == expected->tool_id &&
		      tools_v2[i].param_count == expected->param_count &&
		      tools_v2[i].flags == expected->flags &&
		      strcmp(tools_v2[i].name, expected->name) == 0 &&
		      strcmp(tools_v2[i].params, expected->params) == 0,
		      "v2 full schema table");
	}
	check(strcmp(tools_v2[AGENT_TOOL_HEARTBEAT_CONFIGURE - 1].description,
		     "set or stop the current Agent heartbeat") == 0,
	      "heartbeat configure descriptor");
	check(tool_list(tools_v2, 0) == AGENT_TOOL_COUNT,
	      "tool list compatibility alias");
	check(syscall(SYS_tool_list, tools_v2, 1,
		      sizeof(struct agent_tool_desc_v2) - 1,
		      AGENT_CALL_VERSION_V2) == AGENT_STATUS_BAD_SIZE,
	      "tool list descriptor size rejected");
	check(syscall(SYS_tool_list, tools_v2, 1,
		      sizeof(struct agent_tool_desc_v2),
		      AGENT_CALL_VERSION_V1) == AGENT_STATUS_BAD_VERSION,
	      "tool list version rejected");
	check(syscall(SYS_tool_list, tools_v2, -1,
		      sizeof(struct agent_tool_desc_v2),
		      AGENT_CALL_VERSION_V2) == AGENT_STATUS_BAD_PARAM,
	      "tool list negative count rejected");
	printf("agenttoolabi_ucore: tool_list_v1_v2=1 count=%d\n", count);
	printf("agenttoolabi_ucore: tool_list_contract=1\n");
	printf("agenttoolabi_ucore: optional_schema=1 heartbeat_unified=1\n");
	printf("agenttoolabi_ucore: schema_generated=1 validated=%d\n",
	       AGENT_TOOL_COUNT);
}

static void check_v1_compatibility(void)
{
	struct agent_request legacy;
	struct agent_response legacy_response;

	check(agent_watch(AGENT_EVENT_LLM_DONE, "") == 0,
	      "watch self llm responses");
	memset(&legacy, 0, sizeof(legacy));
	memset(&legacy_response, 0, sizeof(legacy_response));
	legacy.version = AGENT_CALL_VERSION_V1;
	legacy.tool_id = AGENT_TOOL_ECHO;
	legacy.request_id = 9101;
	legacy.arg0 = 11;
	legacy.arg1 = 12;
	legacy.arg0_type = AGENT_PARAM_UINT64;
	legacy.arg1_type = AGENT_PARAM_UINT64;
	legacy.payload_type = AGENT_PARAM_STRING;
	strcpy(legacy.arg0_key, "arg0");
	strcpy(legacy.arg1_key, "arg1");
	strcpy(legacy.payload_key, "payload");
	strcpy(legacy.payload, "v1-compatible");
	check(agent_call(&legacy, &legacy_response) == 0,
	      "legacy call return");
	check(legacy_response.status == AGENT_STATUS_OK &&
	      strcmp(legacy_response.result, "v1-compatible") == 0,
	      "legacy call result");
	legacy.tool_id = AGENT_TOOL_COUNT + 1;
	check(agent_call(&legacy, &legacy_response) == 0 &&
	      legacy_response.status == AGENT_STATUS_UNKNOWN_TOOL,
	      "legacy invalid id not masked by name");
	memset(&legacy, 0, sizeof(legacy));
	legacy.version = AGENT_CALL_VERSION_V1;
	legacy.tool_id = AGENT_TOOL_PID_INFO;
	legacy.arg0 = 7;
	legacy.arg0_type = AGENT_PARAM_UINT64;
	strcpy(legacy.arg0_key, "noise");
	check(agent_call(&legacy, &legacy_response) == 0 &&
	      legacy_response.status == AGENT_STATUS_BAD_PARAM,
	      "legacy unexpected parameter rejected");
	memset(&legacy, 0, sizeof(legacy));
	legacy.version = AGENT_CALL_VERSION_V1;
	legacy.tool_id = AGENT_TOOL_PID_INFO;
	legacy.arg0 = 7;
	check(agent_call(&legacy, &legacy_response) == 0 &&
	      legacy_response.status == AGENT_STATUS_BAD_PARAM &&
	      strcmp(legacy_response.result, "untyped_param_value") == 0,
	      "legacy untyped value rejected");
	printf("agenttoolabi_ucore: v1_compatible=1\n");
}

static void check_v2_success(void)
{
	param_uint(0, "arg1", 22);
	param_string(1, "payload", "v2-typed");
	param_uint(2, "arg0", 21);
	request_init(0, "echo", 3);
	check(tool_call(&request, &response_buffer.value) == 0, "v2 alias call");
	response_buffer_check();
	check(response_buffer.value.status == AGENT_STATUS_OK &&
	      response_buffer.value.tool_id == AGENT_TOOL_ECHO &&
	      response_buffer.value.value0 == 8 &&
	      response_buffer.value.value1 == 21 &&
	      response_buffer.value.value2 == 22 &&
	      strcmp(response_buffer.value.result, "v2-typed") == 0,
	      "v2 reordered parameters");
	request_init(AGENT_TOOL_PID_INFO, 0, 0);
	expect_status(AGENT_STATUS_OK, "v2 id-only call");
	printf("agenttoolabi_ucore: v2_typed_reordered=1\n");
}

static void check_key_capacity_and_llm_response(void)
{
	struct agent_request legacy;
	struct agent_event event;
	struct {
		uint64 before;
		struct agent_response value;
		uint64 after;
	} legacy_response;

	memset(&legacy, 0, sizeof(legacy));
	memset(&legacy_response, 0, sizeof(legacy_response));
	legacy_response.before = USER_BUFFER_SENTINEL;
	legacy_response.after = USER_BUFFER_SENTINEL;
	check(agent_watch(AGENT_EVENT_MESSAGE, "request") == 0,
	      "watch llm requests");
	legacy.version = AGENT_CALL_VERSION_V1;
	legacy.tool_id = AGENT_TOOL_LLM_REQUEST;
	legacy.request_id = 9191;
	legacy.arg0 = getpid();
	legacy.arg0_type = AGENT_PARAM_UINT64;
	legacy.payload_type = AGENT_PARAM_STRING;
	strcpy(legacy.arg0_key, "target_pid");
	strcpy(legacy.payload_key, "prompt_summary");
	strcpy(legacy.payload, "v1-request");
	check(agent_call(&legacy, &legacy_response.value) == 0 &&
	      legacy_response.value.status == AGENT_STATUS_OK &&
	      legacy_response.value.value2 == 1,
	      "legacy llm request key");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 20) == AGENT_STATUS_OK &&
	      event.type == AGENT_EVENT_MESSAGE && event.corr_id == 9191,
	      "consume legacy llm request");
	memset(&legacy, 0, sizeof(legacy));
	legacy.version = AGENT_CALL_VERSION_V1;
	legacy.tool_id = AGENT_TOOL_LLM_RESPONSE;
	legacy.request_id = 9191;
	legacy.arg0 = getpid();
	legacy.arg0_type = AGENT_PARAM_UINT64;
	legacy.payload_type = AGENT_PARAM_STRING;
	strcpy(legacy.arg0_key, "target_pid");
	strcpy(legacy.payload_key, "reply_summary");
	strcpy(legacy.payload, "v1-reply");
	check(agent_call(&legacy, &legacy_response.value) == 0 &&
	      legacy_response.value.status == AGENT_STATUS_OK &&
	      strcmp(legacy_response.value.result, "llm_response") == 0,
	      "legacy llm response key");
	check(legacy_response.before == USER_BUFFER_SENTINEL &&
	      legacy_response.after == USER_BUFFER_SENTINEL,
	      "v1 response buffer sentinel");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 20) == AGENT_STATUS_OK &&
	      event.type == AGENT_EVENT_LLM_DONE && event.corr_id == 9191,
	      "consume legacy llm response");

	param_uint(0, "target_pid", getpid());
	param_string(1, "prompt_summary", "v2-request");
	request_init(AGENT_TOOL_LLM_REQUEST, "llm_request", 2);
	expect_status(AGENT_STATUS_OK, "v2 llm request key");
	check(response_buffer.value.value2 == 1, "v2 llm request delivered");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 20) == AGENT_STATUS_OK &&
	      event.type == AGENT_EVENT_MESSAGE && event.corr_id == 9202,
	      "consume v2 llm request");
	param_uint(0, "target_pid", getpid());
	param_string(1, "reply_summary", "v2-reply");
	request_init(AGENT_TOOL_LLM_RESPONSE, "llm_response", 2);
	expect_status(AGENT_STATUS_OK, "v2 llm response key");
	check(strcmp(response_buffer.value.result, "llm_response") == 0,
	      "v2 llm response result");
	memset(&event, 0, sizeof(event));
	check(agent_wait(&event, 20) == AGENT_STATUS_OK &&
	      event.type == AGENT_EVENT_LLM_DONE && event.corr_id == 9202,
	      "consume v2 llm response");
	check(agent_unwatch(AGENT_EVENT_LLM_DONE, "") == 1,
	      "unwatch self llm responses");

	param_uint(0, "123456789012345", 0x1122334455667788ULL);
	request_init(AGENT_TOOL_QUERY_PROCESS, "query_process", 1);
	expect_status(AGENT_STATUS_UNKNOWN_PARAM,
		      "15-character parameter key is encodable");
	check(params[0].value.uint64_value == 0x1122334455667788ULL,
	      "maximum key does not overwrite value");

	param_uint(0, "type", 1);
	memset(params[0].key, 'x', sizeof(params[0].key));
	request_init(AGENT_TOOL_QUERY_PROCESS, "query_process", 1);
	expect_status(AGENT_STATUS_BAD_SIZE,
		      "16-byte unterminated v2 key rejected");

	memset(&legacy, 0, sizeof(legacy));
	memset(&legacy_response.value, 0, sizeof(legacy_response.value));
	legacy.version = AGENT_CALL_VERSION_V1;
	legacy.tool_id = AGENT_TOOL_QUERY_PROCESS;
	legacy.arg0 = 1;
	legacy.arg0_type = AGENT_PARAM_UINT64;
	memset(legacy.arg0_key, 'x', sizeof(legacy.arg0_key));
	check(agent_call(&legacy, &legacy_response.value) == 0 &&
	      legacy_response.value.status == AGENT_STATUS_BAD_SIZE,
	      "16-byte unterminated v1 key rejected");
	check(legacy_response.before == USER_BUFFER_SENTINEL &&
	      legacy_response.after == USER_BUFFER_SENTINEL,
	      "v1 rejection response buffer sentinel");
	printf("agenttoolabi_ucore: key_capacity=1 llm_response_v1_v2=1 buffer_sentinel=1\n");
}

static void check_v2_rejections(void)
{
	request_init(AGENT_TOOL_COUNT + 7, "echo", 0);
	expect_status(AGENT_STATUS_UNKNOWN_TOOL, "invalid id not masked by name");
	request_init(AGENT_TOOL_ECHO, "pid_info", 0);
	expect_status(AGENT_STATUS_BAD_REQUEST, "tool mismatch");

	param_string(0, "unknown", "x");
	request_init(AGENT_TOOL_ECHO, "echo", 1);
	expect_status(AGENT_STATUS_UNKNOWN_PARAM, "unknown parameter");
	param_uint(0, "arg0", 1);
	param_uint(1, "arg0", 2);
	param_string(2, "payload", "duplicate");
	request_init(AGENT_TOOL_ECHO, "echo", 3);
	expect_status(AGENT_STATUS_DUPLICATE, "duplicate parameter");
	param_string(0, "arg0", "wrong");
	param_uint(1, "arg1", 2);
	param_string(2, "payload", "wrong-type");
	request_init(AGENT_TOOL_ECHO, "echo", 3);
	expect_status(AGENT_STATUS_BAD_TYPE, "wrong parameter type");
	param_uint(0, "arg0", 1);
	param_uint(1, "arg1", 2);
	request_init(AGENT_TOOL_ECHO, "echo", 2);
	expect_status(AGENT_STATUS_BAD_PARAM, "missing required parameter");

	param_uint(0, "arg0", 1);
	params[0].size--;
	request_init(AGENT_TOOL_QUERY_PROCESS, "query_process", 1);
	expect_status(AGENT_STATUS_BAD_SIZE, "bad parameter record size");
	param_uint(0, "type", 1);
	params[0].version++;
	request_init(AGENT_TOOL_QUERY_PROCESS, "query_process", 1);
	expect_status(AGENT_STATUS_BAD_VERSION, "bad parameter record version");
	param_string(0, "path", "abc");
	params[0].value_size--;
	request_init(AGENT_TOOL_QUERY_FILE, "query_file", 1);
	expect_status(AGENT_STATUS_BAD_SIZE, "bad string length");

	request_init(AGENT_TOOL_PID_INFO, "pid_info", 0);
	request.params = (uint64)params;
	expect_status(AGENT_STATUS_BAD_PARAM, "zero count pointer contract");
	request_init(AGENT_TOOL_QUERY_PROCESS, "query_process", 1);
	request.params = 0;
	expect_status(AGENT_STATUS_BAD_PARAM, "nonzero count pointer contract");
	request_init(AGENT_TOOL_PID_INFO, "pid_info", AGENT_TOOL_PARAM_MAX + 1);
	expect_status(AGENT_STATUS_BAD_SIZE, "parameter count bound");
	request_init(0, 0, 0);
	memset(request.tool_name, 'x', sizeof(request.tool_name));
	expect_status(AGENT_STATUS_BAD_SIZE, "unterminated tool name");
	request_init(AGENT_TOOL_PID_INFO, "pid_info", 0);
	request.flags = 1;
	expect_status(AGENT_STATUS_BAD_PARAM, "unsupported request flags");
	request_init(AGENT_TOOL_AGENT_WAIT, "agent_wait", 0);
	expect_status(AGENT_STATUS_BAD_PARAM, "syscall-only tool rejected");
	request_init(AGENT_TOOL_RERUN_STAGE, "rerun_stage", 0);
	expect_status(AGENT_STATUS_DEPRECATED, "rerun_stage deprecated");
	request_init(AGENT_TOOL_WRITE_REPORT, "write_report", 0);
	expect_status(AGENT_STATUS_DEPRECATED, "write_report deprecated");
	request_init(AGENT_TOOL_CTX_STAT, "ctx_stat", 0);
	expect_status(AGENT_STATUS_DEPRECATED, "ctx_stat deprecated");
	request_init(AGENT_TOOL_APPLY_PATCH, "apply_patch", 0);
	expect_status(AGENT_STATUS_BROKER_REQUIRED,
		      "apply_patch requires workspace broker");
	request_init(AGENT_TOOL_WRITE_FILE, "write_file", 0);
	expect_status(AGENT_STATUS_BROKER_REQUIRED,
		      "write_file requires workspace broker");
	param_uint(0, "interval", AGENT_HEARTBEAT_MAX_TICKS + 1ULL);
	request_init(AGENT_TOOL_HEARTBEAT_CONFIGURE, "heartbeat_configure", 1);
	expect_status(AGENT_STATUS_BAD_PARAM,
		      "tool execution heartbeat limit rejected");
	check(strcmp(response_buffer.value.result, "bad_heartbeat_interval") == 0,
	      "tool execution error text preserved");
	request_init(AGENT_TOOL_PID_INFO, "pid_info", 0);
	request.size--;
	expect_status(AGENT_STATUS_BAD_SIZE, "bad request size");
	request_init(AGENT_TOOL_PID_INFO, "pid_info", 0);
	request.version = 0;
	expect_status(AGENT_STATUS_BAD_VERSION, "bad request version");
	printf("agenttoolabi_ucore: strict_negative_matrix=1\n");
}

int main(void)
{
	int pid;
	int status = 0;

	printf("agenttoolabi_ucore: versioned tool ABI test\n");
	check_lists();
	pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);
	check(pid >= 0, "create test Agent");
	if (pid == 0) {
		check_v1_compatibility();
		check_v2_success();
		check_key_capacity_and_llm_response();
		check_v2_rejections();
		exit(0);
	}
	check(waitpid(pid, &status) == pid && status == 0, "wait test Agent");
	printf("agenttoolabi_ucore: parent passed\n");
	return 0;
}
