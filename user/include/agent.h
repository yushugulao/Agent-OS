#ifndef USER_AGENT_H
#define USER_AGENT_H

#include <stddef.h>

#define AGENT_CALL_VERSION 1
#define AGENT_TYPE_NONE  0
#define AGENT_TYPE_AGENT 1

#define AGENT_LOOP_NONE    0
#define AGENT_LOOP_IDLE    1
#define AGENT_LOOP_RUNNING 2
#define AGENT_LOOP_WAITING 3

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
#define AGENT_TOOL_COUNT             18

#define AGENT_STATUS_OK           0
#define AGENT_STATUS_BAD_REQUEST -1
#define AGENT_STATUS_UNKNOWN_TOOL -2
#define AGENT_STATUS_NOT_AGENT   -3
#define AGENT_STATUS_BAD_PARAM   -4
#define AGENT_STATUS_NOT_FOUND   -5
#define AGENT_STATUS_NO_SPACE    -6
#define AGENT_STATUS_TIMEOUT     -7
#define AGENT_STATUS_DENIED      -8
#define AGENT_STATUS_DUPLICATE   -9

#define AGENT_PAYLOAD_SIZE      64
#define AGENT_RESULT_SIZE       96
#define AGENT_TOOL_NAME_SIZE    32
#define AGENT_PARAM_KEY_SIZE    16
#define AGENT_TOOL_PARAMS_SIZE  64
#define AGENT_TOOL_DESC_SIZE    96
#define AGENT_OP_PAYLOAD_SIZE   AGENT_PAYLOAD_SIZE
#define AGENT_FAST_RESULT_SIZE  AGENT_PAYLOAD_SIZE
#define AGENT_CONTEXT_TEXT_SIZE 16
#define AGENT_BATCH_MAX         64

#define AGENT_PAGE_SIZE 4096
#define AGENT_CONTEXT_PAGES 4
#define AGENT_CONTEXT_SIZE (AGENT_CONTEXT_PAGES * AGENT_PAGE_SIZE)
#define AGENT_CONTEXT_MAGIC 0x4147435458543031ULL
#define AGENT_CONTEXT_VERSION 2
#define AGENT_CONTEXT_MAX_RECORDS 128
#define AGENT_USER_TOP (1L << (9 + 9 + 9 + 12 - 1))
#define AGENT_TRAMPOLINE (AGENT_USER_TOP - AGENT_PAGE_SIZE)
#define AGENT_TRAPFRAME (AGENT_TRAMPOLINE - AGENT_PAGE_SIZE)
#define AGENT_CONTEXT_BASE (AGENT_TRAPFRAME - (16 + AGENT_CONTEXT_PAGES) * AGENT_PAGE_SIZE)
#define AGENT_CONTEXT_LATEST_RESPONSE_OFFSET (sizeof(struct agent_context_header))
#define AGENT_CONTEXT_RECORDS_OFFSET AGENT_PAGE_SIZE

#define AGENT_FILE_META_MAX       128
#define AGENT_FILE_QUERY_MAX_HITS 8
#define AGENT_FILE_NAME_SIZE      32
#define AGENT_FILE_LOGICAL_SIZE   80
#define AGENT_FILE_PROJECT_SIZE   16
#define AGENT_FILE_WORKFLOW_SIZE  24
#define AGENT_FILE_FIELD_SIZE     16
#define AGENT_FILE_SUMMARY_SIZE   96
#define AGENT_FILE_QUERY_USE_INDEX 1
#define AGENT_FILE_QUERY_SCAN      2

#define AGENT_EVENT_PAYLOAD_SIZE 64
#define AGENT_EVENT_NONE          0
#define AGENT_EVENT_FILE_STATUS   1
#define AGENT_EVENT_MESSAGE       2
#define AGENT_EVENT_TIMER         3
#define AGENT_EVENT_JOB_DONE      4
#define AGENT_EVENT_POLICY_DENIED 5
#define AGENT_EVENT_CONTEXT_LIMIT 6

#define AGENT_ROLE_SENTINEL      1
#define AGENT_ROLE_INVESTIGATOR  2
#define AGENT_ROLE_RECOVERY      3
#define AGENT_ROLE_ORCHESTRATOR  4

#define AGENT_DEP_PREPARE (1ULL << 0)
#define AGENT_DEP_ALIGN   (1ULL << 1)
#define AGENT_DEP_ANALYZE (1ULL << 2)
#define AGENT_DEP_REPORT  (1ULL << 3)
#define AGENT_DEP_ARCHIVE (1ULL << 4)

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
	uint64 event_count;
	uint64 event_dropped;
	uint64 wait_count;
	uint64 timeout_count;
	uint64 last_heartbeat_tick;
	uint64 capability_mask;
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

struct agent_event {
	int type;
	int source_pid;
	int target_pid;
	int status;
	uint64 event_id;
	uint64 tick;
	uint64 corr_id;
	char payload[AGENT_EVENT_PAYLOAD_SIZE];
};

struct agent_file_meta {
	int used;
	int fid;
	char physical_name[AGENT_FILE_NAME_SIZE];
	char logical_path[AGENT_FILE_LOGICAL_SIZE];
	char project[AGENT_FILE_PROJECT_SIZE];
	char workflow[AGENT_FILE_WORKFLOW_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
	char stage[AGENT_FILE_FIELD_SIZE];
	char kind[AGENT_FILE_FIELD_SIZE];
	char status[AGENT_FILE_FIELD_SIZE];
	char summary[AGENT_FILE_SUMMARY_SIZE];
	uint64 dependency_mask;
	uint64 updated_tick;
};

struct agent_file_hit {
	int fid;
	char physical_name[AGENT_FILE_NAME_SIZE];
	char logical_path[AGENT_FILE_LOGICAL_SIZE];
	char stage[AGENT_FILE_FIELD_SIZE];
	char kind[AGENT_FILE_FIELD_SIZE];
	char status[AGENT_FILE_FIELD_SIZE];
	char summary[AGENT_FILE_SUMMARY_SIZE];
	uint64 dependency_mask;
};

struct agent_file_query {
	uint64 flags;
	int max_hits;
	char physical_name[AGENT_FILE_NAME_SIZE];
	char logical_path[AGENT_FILE_LOGICAL_SIZE];
	char project[AGENT_FILE_PROJECT_SIZE];
	char workflow[AGENT_FILE_WORKFLOW_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
	char stage[AGENT_FILE_FIELD_SIZE];
	char kind[AGENT_FILE_FIELD_SIZE];
	char status[AGENT_FILE_FIELD_SIZE];
	char summary_contains[AGENT_FILE_SUMMARY_SIZE];
};

struct agent_file_query_result {
	int total_hits;
	int returned;
	int scanned_records;
	int used_index;
	int truncated;
	uint64 query_ticks;
	struct agent_file_hit hits[AGENT_FILE_QUERY_MAX_HITS];
};

int agent_create(void);
int agent_info(struct agent_info *info);
int agent_run(struct agent_op *ops, struct agent_result *results, int count, uint64 flags);
int agent_call(struct agent_request *req, struct agent_response *resp);
int agent_tool_list(struct agent_tool_desc *out, int max);
int context_push(struct agent_context_record *record);
int context_query(uint64 start_sequence, struct agent_context_record *out, int max);
int context_snapshot(struct agent_context_header *header, struct agent_context_record *records, int max);
int context_rollback(uint64 sequence);
int context_clear(void);
int agent_watch(int event_type, const char *filter);
int agent_wait(struct agent_event *event, int timeout_ticks);
int agent_heartbeat(int interval_ticks);
int agent_wake(int pid, struct agent_event *event);
int agent_file_meta_init(void);
int agent_file_meta_set(struct agent_file_meta *meta);
int agent_file_query(struct agent_file_query *query, struct agent_file_query_result *result);

#endif
