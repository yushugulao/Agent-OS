#ifndef AGENT_TOOL_ABI_H
#define AGENT_TOOL_ABI_H

/* 共享且架构稳定的 Agent 工具 ABI；内核和用户库共同包含，本文件不放实现声明。 */
#define AGENT_CALL_VERSION_V1 1U
#define AGENT_CALL_VERSION_V2 2U
#define AGENT_CALL_VERSION AGENT_CALL_VERSION_V1

#define AGENT_TOOL_ECHO              1
#define AGENT_TOOL_PID_INFO          2
#define AGENT_TOOL_CTX_STAT          3
#define AGENT_TOOL_QUERY_PROCESS     4
#define AGENT_TOOL_GET_SYSTEM_STATUS 5
#define AGENT_TOOL_READ_CONTEXT      6
#define AGENT_TOOL_QUERY_FILE        7
#define AGENT_TOOL_SEND_MESSAGE      8
#define AGENT_TOOL_READ_MESSAGE      9
#define AGENT_TOOL_FILE_META_INIT    10
#define AGENT_TOOL_READ_FILE_SUMMARY 11
#define AGENT_TOOL_DEPENDENCY_QUERY  12
#define AGENT_TOOL_CAPABILITY_CHECK  13
#define AGENT_TOOL_RERUN_STAGE       14
#define AGENT_TOOL_WRITE_REPORT      15
#define AGENT_TOOL_AGENT_WATCH       16
#define AGENT_TOOL_AGENT_WAIT        17
#define AGENT_TOOL_AGENT_HEARTBEAT   18
#define AGENT_TOOL_CONTEXT_PUSH      19
#define AGENT_TOOL_READ_FILE_DIGEST  20
#define AGENT_TOOL_ACTION_COMMIT     21
#define AGENT_TOOL_ARTIFACT_UPDATE   22
#define AGENT_TOOL_LLM_REQUEST       23
#define AGENT_TOOL_LLM_RESPONSE      24
#define AGENT_TOOL_DEPENDENCY_UPDATE 25
#define AGENT_TOOL_DELEGATE_TASK     26
#define AGENT_TOOL_COUNT             26

#define AGENT_TOOL_F_CALLABLE     1ULL
#define AGENT_TOOL_F_SYSCALL_ONLY 2ULL

#define AGENT_STATUS_OK            0
#define AGENT_STATUS_BAD_REQUEST  -1
#define AGENT_STATUS_UNKNOWN_TOOL -2
#define AGENT_STATUS_NOT_AGENT    -3
#define AGENT_STATUS_BAD_PARAM    -4
#define AGENT_STATUS_NOT_FOUND    -5
#define AGENT_STATUS_NO_SPACE     -6
#define AGENT_STATUS_TIMEOUT      -7
#define AGENT_STATUS_DENIED       -8
#define AGENT_STATUS_DUPLICATE    -9
#define AGENT_STATUS_CANCELLED   -10
#define AGENT_STATUS_CONFLICT    -11
#define AGENT_STATUS_STALE       -12
#define AGENT_STATUS_BAD_VERSION -13
#define AGENT_STATUS_BAD_SIZE    -14
#define AGENT_STATUS_BAD_TYPE    -15
#define AGENT_STATUS_UNKNOWN_PARAM -16
#define AGENT_STATUS_RETRY       -17
#define AGENT_STATUS_IO_ERROR    -18
#define AGENT_STATUS_DURABILITY  -19
#define AGENT_STATUS_INDETERMINATE -20

#define AGENT_PARAM_NONE   0U
#define AGENT_PARAM_UINT64 1U
#define AGENT_PARAM_STRING 2U
#define AGENT_PARAM_VERSION 1U

#define AGENT_TOOL_NAME_SIZE    32U
#define AGENT_PARAM_KEY_SIZE    16U
#define AGENT_PAYLOAD_SIZE      64U
#define AGENT_RESULT_SIZE       96U
#define AGENT_TOOL_PARAMS_SIZE  64U
#define AGENT_TOOL_DESC_SIZE    96U
#define AGENT_OP_PAYLOAD_SIZE   AGENT_PAYLOAD_SIZE
#define AGENT_FAST_RESULT_SIZE  AGENT_PAYLOAD_SIZE
#define AGENT_BATCH_MAX         64U
#define AGENT_TOOL_PARAM_MAX     8U
#define AGENT_PARAM_STRING_SIZE 64U

/* 规范 wire key；字段尺寸包含结尾 NUL，无法编码的 key 必须在构建期拒绝。 */
#define AGENT_PARAM_KEY_REGISTRY(X) \
	X(PAYLOAD, "payload") \
	X(ARG0, "arg0") \
	X(ARG1, "arg1") \
	X(TYPE, "type") \
	X(PATH, "path") \
	X(TARGET_PID, "target_pid") \
	X(MESSAGE, "message") \
	X(SELECTOR, "selector") \
	X(LABEL, "label") \
	X(ROLE, "role") \
	X(ACTION, "action") \
	X(STAGE, "stage") \
	X(EVENT_TYPE, "event_type") \
	X(FILTER, "filter") \
	X(TIMEOUT, "timeout") \
	X(INTERVAL, "interval") \
	X(RECORD, "record") \
	X(PROMPT_SUMMARY, "prompt_summary") \
	X(REPLY_SUMMARY, "reply_summary")

#define AGENT_PARAM_KEY_ASSERT(symbol, literal) \
	_Static_assert(sizeof(literal) <= AGENT_PARAM_KEY_SIZE, \
		       "Agent parameter key exceeds its wire field");
AGENT_PARAM_KEY_REGISTRY(AGENT_PARAM_KEY_ASSERT)
#undef AGENT_PARAM_KEY_ASSERT

/* 两个目标都把 heartbeat_interval 存入有符号 PCB 字段。 */
#define AGENT_HEARTBEAT_MAX_TICKS 0x7fffffffULL

struct agent_op {
	int version;
	int tool_id;
	unsigned long long request_id;
	unsigned long long arg0;
	unsigned long long arg1;
	unsigned long long flags;
	char payload[AGENT_OP_PAYLOAD_SIZE];
};

struct agent_result {
	int version;
	int status;
	int tool_id;
	unsigned long long request_id;
	unsigned long long sequence;
	unsigned long long value0;
	unsigned long long value1;
	unsigned long long value2;
	char result[AGENT_FAST_RESULT_SIZE];
};

/* 版本 1 与原始 ABI 保持逐字节兼容。 */
struct agent_request {
	int version;
	int tool_id;
	unsigned long long request_id;
	unsigned long long arg0;
	unsigned long long arg1;
	int arg0_type;
	int arg1_type;
	int payload_type;
	char tool_name[AGENT_TOOL_NAME_SIZE];
	char arg0_key[AGENT_PARAM_KEY_SIZE];
	char arg1_key[AGENT_PARAM_KEY_SIZE];
	char payload_key[AGENT_PARAM_KEY_SIZE];
	char payload[AGENT_PAYLOAD_SIZE];
};

struct agent_response {
	int version;
	int status;
	int tool_id;
	unsigned long long request_id;
	unsigned long long sequence;
	unsigned long long value0;
	unsigned long long value1;
	unsigned long long value2;
	char tool_name[AGENT_TOOL_NAME_SIZE];
	char result[AGENT_RESULT_SIZE];
};

struct agent_tool_desc {
	int tool_id;
	unsigned long long flags;
	char name[AGENT_TOOL_NAME_SIZE];
	char params[AGENT_TOOL_PARAMS_SIZE];
	char description[AGENT_TOOL_DESC_SIZE];
};

/* 版本 2 携带有界变长类型化参数数组；每条记录独立定长和定版，后续 ABI 可追加后缀。 */
union agent_param_value_v2 {
	unsigned long long uint64_value;
	char string_value[AGENT_PARAM_STRING_SIZE];
};

struct agent_param_v2 {
	unsigned int version;
	unsigned int size;
	unsigned int type;
	unsigned int value_size;
	char key[AGENT_PARAM_KEY_SIZE];
	union agent_param_value_v2 value;
};

struct agent_request_v2 {
	unsigned int version;
	unsigned int size;
	int tool_id;
	unsigned int param_count;
	unsigned long long request_id;
	unsigned long long flags;
	unsigned long long params;
	char tool_name[AGENT_TOOL_NAME_SIZE];
};

struct agent_response_v2 {
	unsigned int version;
	unsigned int size;
	int status;
	int tool_id;
	unsigned long long request_id;
	unsigned long long sequence;
	unsigned long long value0;
	unsigned long long value1;
	unsigned long long value2;
	char tool_name[AGENT_TOOL_NAME_SIZE];
	char result[AGENT_RESULT_SIZE];
};

struct agent_tool_desc_v2 {
	unsigned int version;
	unsigned int size;
	int tool_id;
	unsigned int param_count;
	unsigned long long flags;
	char name[AGENT_TOOL_NAME_SIZE];
	char params[AGENT_TOOL_PARAMS_SIZE];
	char description[AGENT_TOOL_DESC_SIZE];
};

_Static_assert(sizeof(unsigned int) == 4,
	       "Agent tool ABI requires 32-bit unsigned int");
_Static_assert(sizeof(unsigned long long) == 8,
	       "Agent tool ABI requires 64-bit unsigned long long");
_Static_assert(sizeof(struct agent_request) == 192,
	       "Agent tool request v1 ABI layout");
_Static_assert(sizeof(struct agent_response) == 184,
	       "Agent tool response v1 ABI layout");
_Static_assert(sizeof(struct agent_tool_desc) == 208,
	       "Agent tool descriptor v1 ABI layout");
_Static_assert(sizeof(struct agent_param_v2) == 96,
	       "Agent tool parameter v2 ABI layout");
_Static_assert(__builtin_offsetof(struct agent_param_v2, value) == 32,
	       "Agent tool parameter value ABI offset");
_Static_assert(sizeof(struct agent_request_v2) == 72,
	       "Agent tool request v2 ABI layout");
_Static_assert(__builtin_offsetof(struct agent_request_v2, params) == 32,
	       "Agent tool request parameter pointer ABI offset");
_Static_assert(sizeof(struct agent_response_v2) == 184,
	       "Agent tool response v2 ABI layout");
_Static_assert(sizeof(struct agent_tool_desc_v2) == 216,
	       "Agent tool descriptor v2 ABI layout");

#endif
