struct agent_info {
  int is_agent;
  int agent_id;
  uint64 context_base;
  uint64 context_size;
  int agent_type;
  int heartbeat_interval;
  int resource_quota;
  int loop_state;
  uint64 agent_call_count;
  uint64 context_path_count;
  uint64 context_path_capacity;
  uint64 context_path_head;
  uint64 context_path_oldest;
  uint64 context_path_latest;
  uint64 context_path_dropped;
  uint64 context_path_rollback_count;
  uint64 latest_response_offset;
  uint64 records_offset;
};

#define AGENT_CALL_VERSION 1

#define AGENT_TYPE_NONE  0
#define AGENT_TYPE_AGENT 1

#define AGENT_LOOP_NONE    0
#define AGENT_LOOP_IDLE    1
#define AGENT_LOOP_RUNNING 2

#define AGENT_TOOL_ECHO              1
#define AGENT_TOOL_PID_INFO          2
#define AGENT_TOOL_CTX_STAT          3
#define AGENT_TOOL_QUERY_PROCESS     4
#define AGENT_TOOL_GET_SYSTEM_STATUS 5
#define AGENT_TOOL_READ_CONTEXT      6
#define AGENT_TOOL_QUERY_FILE        7
#define AGENT_TOOL_SEND_MESSAGE      8
#define AGENT_TOOL_READ_MESSAGE      9
#define AGENT_TOOL_COUNT             9

#define AGENT_STATUS_OK           0
#define AGENT_STATUS_BAD_REQUEST -1
#define AGENT_STATUS_UNKNOWN_TOOL -2
#define AGENT_STATUS_NOT_AGENT   -3
#define AGENT_STATUS_BAD_PARAM   -4
#define AGENT_STATUS_NOT_FOUND   -5
#define AGENT_STATUS_NO_SPACE    -6

#define AGENT_PARAM_NONE   0
#define AGENT_PARAM_UINT64 1
#define AGENT_PARAM_STRING 2

#define AGENT_TOOL_NAME_SIZE 32
#define AGENT_PARAM_KEY_SIZE 16
#define AGENT_PAYLOAD_SIZE   64
#define AGENT_RESULT_SIZE    96
#define AGENT_TOOL_PARAMS_SIZE 64
#define AGENT_TOOL_DESC_SIZE   96
#define AGENT_OP_PAYLOAD_SIZE  AGENT_PAYLOAD_SIZE
#define AGENT_FAST_RESULT_SIZE AGENT_PAYLOAD_SIZE
#define AGENT_CONTEXT_TEXT_SIZE 16
#define AGENT_BATCH_MAX        64

#define AGENT_CONTEXT_MAGIC 0x4147435458543031ULL
#define AGENT_CONTEXT_VERSION 1
#define AGENT_CONTEXT_MAX_RECORDS 128

#define AGENT_DEFAULT_HEARTBEAT_INTERVAL 0
#define AGENT_DEFAULT_RESOURCE_QUOTA AGENT_CONTEXT_MAX_RECORDS

struct agent_request {
  int version;
  int tool_id;
  uint64 request_id;
  uint64 arg0;
  uint64 arg1;
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
  uint64 request_id;
  uint64 sequence;
  uint64 value0;
  uint64 value1;
  uint64 value2;
  char tool_name[AGENT_TOOL_NAME_SIZE];
  char result[AGENT_RESULT_SIZE];
};

struct agent_tool_desc {
  int tool_id;
  char name[AGENT_TOOL_NAME_SIZE];
  char params[AGENT_TOOL_PARAMS_SIZE];
  char description[AGENT_TOOL_DESC_SIZE];
};

struct agent_op {
  int version;
  int tool_id;
  uint64 request_id;
  uint64 arg0;
  uint64 arg1;
  uint64 flags;
  char payload[AGENT_OP_PAYLOAD_SIZE];
};

struct agent_result {
  int version;
  int status;
  int tool_id;
  uint64 request_id;
  uint64 sequence;
  uint64 value0;
  uint64 value1;
  uint64 value2;
  char result[AGENT_FAST_RESULT_SIZE];
};

struct agent_context_header {
  uint64 magic;
  uint64 version;
  uint64 capacity;
  uint64 count;
  uint64 head;
  uint64 total_calls;
  uint64 oldest_sequence;
  uint64 latest_sequence;
  uint64 dropped_records;
  uint64 rollback_count;
  uint64 latest_response_offset;
  uint64 records_offset;
};

struct agent_context_record {
  uint64 sequence;
  uint64 request_id;
  uint64 arg0;
  uint64 value0;
  uint64 value1;
  uint64 value2;
  uint64 tick;
  int tool_id;
  int status;
  char payload[AGENT_CONTEXT_TEXT_SIZE];
  char result[AGENT_CONTEXT_TEXT_SIZE];
};

#define AGENT_CONTEXT_HEADER_OFFSET 0
#define AGENT_CONTEXT_LATEST_RESPONSE_OFFSET \
  (sizeof(struct agent_context_header))
#define AGENT_CONTEXT_RECORDS_OFFSET 4096
