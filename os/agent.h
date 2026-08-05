#ifndef AGENT_H
#define AGENT_H

#include "const.h"
#include "riscv.h"
#include "types.h"
#include "../agent_lifecycle_abi.h"
#include "../agent_performance_abi.h"
#include "../agent_resource_abi.h"
#include "../agent_tool_abi.h"

#define AGENT_TYPE_NONE  0
#define AGENT_TYPE_AGENT 1

#define AGENT_LOOP_NONE    0
#define AGENT_LOOP_IDLE    1
#define AGENT_LOOP_RUNNING 2
#define AGENT_LOOP_WAITING 3

#define AGENT_THREAD_RUNTIME_ACTIVATE 1
#define AGENT_THREAD_RUNTIME_RELEASE  2

#define AGENT_CONTEXT_TEXT_SIZE 16

#define AGENT_CONTEXT_MAGIC       0x4147435458543031ULL
#define AGENT_CONTEXT_VERSION     8
#define AGENT_CONTEXT_PAGES       6
#define AGENT_CONTEXT_SIDECAR_PAGE_COUNT 9U
#define AGENT_STATE_PAGE_COUNT \
	(AGENT_CONTEXT_SIDECAR_PAGE_COUNT + 2U * AGENT_CONTEXT_PAGES)
#define AGENT_CONTEXT_SIZE        (AGENT_CONTEXT_PAGES * PAGE_SIZE)
#define AGENT_CONTEXT_MAX_RECORDS 128
#define AGENT_CONTEXT_HEADER_OFFSET 0
#define AGENT_CONTEXT_LATEST_RESPONSE_OFFSET \
	(sizeof(struct agent_context_header))
#define AGENT_CONTEXT_RECORDS_OFFSET PAGE_SIZE
#define AGENT_CONTEXT_BASE \
	(TRAPFRAME - (16 + AGENT_CONTEXT_PAGES) * PAGE_SIZE)

#define AGENT_CONTEXT_RECORD_F_SYSTEM    1
#define AGENT_CONTEXT_RECORD_F_MANUAL    2
#define AGENT_CONTEXT_RECORD_F_TRUNCATED 4
#define AGENT_CONTEXT_EVICT_FIFO 1

#define AGENT_EVENT_QUEUE_CAP           16
#define AGENT_EVENT_KERNEL_RESERVE       4
#define AGENT_EVENT_CLASS_RESERVE        4
#define AGENT_EVENT_EXTERNAL_LIMIT \
	(AGENT_EVENT_QUEUE_CAP - AGENT_EVENT_KERNEL_RESERVE)
#define AGENT_EVENT_IPC_LIMIT \
	(AGENT_EVENT_EXTERNAL_LIMIT - AGENT_EVENT_CLASS_RESERVE)
#define AGENT_EVENT_ATTRIBUTED_LIMIT \
	(AGENT_EVENT_EXTERNAL_LIMIT - AGENT_EVENT_CLASS_RESERVE)
#define AGENT_EVENT_SOURCE_LIMIT         4
#define AGENT_IPC_ROUTE_MAX              16
#define AGENT_WATCH_MAX                   8

#define AGENT_SCHED_POLICY_ADAPTIVE 1
#define AGENT_SCHED_DEFAULT_BUDGET  8
#define AGENT_SCHED_MAX_AGENT_BURST 8
#define AGENT_SCHED_TRACE_CAP       16
#define AGENT_SCHED_WEIGHT_MIN      10
#define AGENT_SCHED_WEIGHT_MAX      200
#define AGENT_SCHED_PRIORITY_MIN    -100
#define AGENT_SCHED_PRIORITY_MAX    100
#define AGENT_SCHED_BUDGET_MIN      1
#define AGENT_SCHED_BUDGET_MAX      64

#define AGENT_SCHED_CONFIG_POLICY   (1ULL << 0)
#define AGENT_SCHED_CONFIG_WEIGHT   (1ULL << 1)
#define AGENT_SCHED_CONFIG_PRIORITY (1ULL << 2)
#define AGENT_SCHED_CONFIG_BUDGET   (1ULL << 3)

#define AGENT_SCHED_REASON_ROLE_WEIGHT   (1ULL << 0)
#define AGENT_SCHED_REASON_EVENT_QUEUE   (1ULL << 1)
#define AGENT_SCHED_REASON_WAITING       (1ULL << 2)
#define AGENT_SCHED_REASON_DEADLINE_NEAR (1ULL << 3)
#define AGENT_SCHED_REASON_DEADLINE_NOW  (1ULL << 4)
#define AGENT_SCHED_REASON_HEARTBEAT_DUE (1ULL << 5)
#define AGENT_SCHED_REASON_BUDGET_USED   (1ULL << 6)
#define AGENT_SCHED_REASON_VRUNTIME      (1ULL << 7)
#define AGENT_SCHED_REASON_READY_AGE     (1ULL << 8)
#define AGENT_SCHED_REASON_PRIORITY      (1ULL << 9)

#define AGENT_TRACE_KIND_CONTEXT 1
#define AGENT_TRACE_KIND_SCHED   2
#define AGENT_TRACE_MAX_RECORDS \
	(AGENT_CONTEXT_MAX_RECORDS + AGENT_SCHED_TRACE_CAP)

#define AGENT_AUDIT_KIND_CONTEXT       1
#define AGENT_AUDIT_KIND_EVENT_ENQUEUE 2
#define AGENT_AUDIT_KIND_EVENT_CONSUME 3
#define AGENT_AUDIT_KIND_SCHED         4
#define AGENT_AUDIT_KIND_PREFETCH      5
#define AGENT_AUDIT_MAX_RECORDS        512
#define AGENT_AUDIT_TEXT_SIZE          32
#define AGENT_LEDGER_VERSION           2

#define AGENT_TIMELINE_SOURCE_CONTEXT  1
#define AGENT_TIMELINE_SOURCE_SCHED    2
#define AGENT_TIMELINE_SOURCE_AUDIT    3
#define AGENT_TIMELINE_SOURCE_PREFETCH 4
#define AGENT_TIMELINE_MAX_RECORDS     512

#define AGENT_TIMELINE_SOURCE_MASK_CONTEXT \
	(1ULL << AGENT_TIMELINE_SOURCE_CONTEXT)
#define AGENT_TIMELINE_SOURCE_MASK_SCHED \
	(1ULL << AGENT_TIMELINE_SOURCE_SCHED)
#define AGENT_TIMELINE_SOURCE_MASK_AUDIT \
	(1ULL << AGENT_TIMELINE_SOURCE_AUDIT)
#define AGENT_TIMELINE_SOURCE_MASK_PREFETCH \
	(1ULL << AGENT_TIMELINE_SOURCE_PREFETCH)
#define AGENT_TIMELINE_SOURCE_MASK_ALL \
	(AGENT_TIMELINE_SOURCE_MASK_CONTEXT | \
	 AGENT_TIMELINE_SOURCE_MASK_SCHED | \
	 AGENT_TIMELINE_SOURCE_MASK_AUDIT | \
	 AGENT_TIMELINE_SOURCE_MASK_PREFETCH)

#define AGENT_TIMELINE_FILTER_SOURCE_MASK (1ULL << 0)
#define AGENT_TIMELINE_FILTER_START_TICK  (1ULL << 1)
#define AGENT_TIMELINE_FILTER_SPAN_ID     (1ULL << 2)
#define AGENT_TIMELINE_FILTER_KIND        (1ULL << 3)
#define AGENT_TIMELINE_FILTER_PID         (1ULL << 4)
#define AGENT_TIMELINE_FILTER_SOURCE_PID  (1ULL << 5)
#define AGENT_TIMELINE_FILTER_TARGET_PID  (1ULL << 6)
#define AGENT_TIMELINE_FILTER_ROLE        (1ULL << 7)
#define AGENT_TIMELINE_FILTER_TOOL_ID     (1ULL << 8)
#define AGENT_TIMELINE_FILTER_EVENT_TYPE  (1ULL << 9)
#define AGENT_TIMELINE_FILTER_STATUS      (1ULL << 10)
#define AGENT_TIMELINE_FILTER_FLAGS_ALL   (1ULL << 11)
#define AGENT_TIMELINE_FILTER_AFTER_CURSOR (1ULL << 12)
#define AGENT_TIMELINE_FILTER_ALL_FLAGS \
	(AGENT_TIMELINE_FILTER_SOURCE_MASK | \
	 AGENT_TIMELINE_FILTER_START_TICK | \
	 AGENT_TIMELINE_FILTER_SPAN_ID | \
	 AGENT_TIMELINE_FILTER_KIND | \
	 AGENT_TIMELINE_FILTER_PID | \
	 AGENT_TIMELINE_FILTER_SOURCE_PID | \
	 AGENT_TIMELINE_FILTER_TARGET_PID | \
	 AGENT_TIMELINE_FILTER_ROLE | \
	 AGENT_TIMELINE_FILTER_TOOL_ID | \
	 AGENT_TIMELINE_FILTER_EVENT_TYPE | \
	 AGENT_TIMELINE_FILTER_STATUS | \
	 AGENT_TIMELINE_FILTER_FLAGS_ALL | \
	 AGENT_TIMELINE_FILTER_AFTER_CURSOR)

#define AGENT_AUDIT_FILTER_START_SEQUENCE (1ULL << 0)
#define AGENT_AUDIT_FILTER_SPAN_ID        (1ULL << 1)
#define AGENT_AUDIT_FILTER_KIND           (1ULL << 2)
#define AGENT_AUDIT_FILTER_PID            (1ULL << 3)
#define AGENT_AUDIT_FILTER_SOURCE_PID     (1ULL << 4)
#define AGENT_AUDIT_FILTER_TARGET_PID     (1ULL << 5)
#define AGENT_AUDIT_FILTER_ROLE           (1ULL << 6)
#define AGENT_AUDIT_FILTER_TOOL_ID        (1ULL << 7)
#define AGENT_AUDIT_FILTER_EVENT_TYPE     (1ULL << 8)
#define AGENT_AUDIT_FILTER_STATUS         (1ULL << 9)

#define AGENT_FILE_META_F_DELETE  1
#define AGENT_FILE_META_F_PERSIST 2
#define AGENT_FILE_META_F_AUTOSCAN 4

#define AGENT_FILE_META_UPDATE_PHYSICAL   (1ULL << 0)
#define AGENT_FILE_META_UPDATE_LOGICAL    (1ULL << 1)
#define AGENT_FILE_META_UPDATE_PROJECT    (1ULL << 2)
#define AGENT_FILE_META_UPDATE_WORKFLOW   (1ULL << 3)
#define AGENT_FILE_META_UPDATE_RUN_ID     (1ULL << 4)
#define AGENT_FILE_META_UPDATE_STAGE      (1ULL << 5)
#define AGENT_FILE_META_UPDATE_KIND       (1ULL << 6)
#define AGENT_FILE_META_UPDATE_STATUS     (1ULL << 7)
#define AGENT_FILE_META_UPDATE_SUMMARY    (1ULL << 8)
#define AGENT_FILE_META_UPDATE_DEPENDENCY (1ULL << 9)
#define AGENT_FILE_META_UPDATE_ALL        0x3ffULL

#define AGENT_FILE_META_MAX       512
#define AGENT_FILE_SYSTEM_LIMIT   64
#define AGENT_FILE_SCOPE_LIMIT    112
#define AGENT_FILE_ORDINARY_LIMIT \
	(AGENT_FILE_META_MAX - AGENT_FILE_SYSTEM_LIMIT)
#define AGENT_FILE_STATUS_BATCH_LIMIT 112
#define AGENT_FILE_QUERY_MAX_HITS 8
#define AGENT_FILE_NAME_SIZE      32
#define AGENT_FILE_LOGICAL_SIZE   80
#define AGENT_FILE_PROJECT_SIZE   16
#define AGENT_FILE_WORKFLOW_SIZE  24
#define AGENT_FILE_FIELD_SIZE     16
#define AGENT_FILE_SUMMARY_SIZE   96

#define AGENT_FILE_QUERY_USE_INDEX 1
#define AGENT_FILE_QUERY_SCAN      2

#define AGENT_FILE_EDIT_F_BREAK_EXPIRED      (1ULL << 0)
#define AGENT_FILE_EDIT_F_ORCHESTRATOR_BREAK (1ULL << 1)
#define AGENT_FILE_EDIT_DEFAULT_TTL          200
#define AGENT_FILE_EDIT_MAX_TTL              2000

#define AGENT_FILE_QUERY_PLAN_SCAN         0
#define AGENT_FILE_QUERY_PLAN_STATUS_INDEX 1
#define AGENT_FILE_QUERY_PLAN_STAGE_INDEX  2
#define AGENT_FILE_QUERY_PLAN_KIND_INDEX   3

#define AGENT_FILE_QUERY_REASON_FORCED_SCAN  (1ULL << 0)
#define AGENT_FILE_QUERY_REASON_INDEX_OFF    (1ULL << 1)
#define AGENT_FILE_QUERY_REASON_STATUS_INDEX (1ULL << 2)
#define AGENT_FILE_QUERY_REASON_STAGE_INDEX  (1ULL << 3)
#define AGENT_FILE_QUERY_REASON_KIND_INDEX   (1ULL << 4)
#define AGENT_FILE_QUERY_REASON_NO_INDEX_KEY (1ULL << 5)
#define AGENT_FILE_QUERY_REASON_CACHE_HIT    (1ULL << 6)

#define AGENT_FILE_DIGEST_MAX_BYTES 4096
#define AGENT_FILE_DIGEST_CHUNK     64

#define AGENT_FILE_PREFETCH_MAX_HINTS 8
#define AGENT_FILE_PREFETCH_REASON_DEPENDENCY  (1ULL << 0)
#define AGENT_FILE_PREFETCH_REASON_SAME_RUN    (1ULL << 1)
#define AGENT_FILE_PREFETCH_REASON_PENDING     (1ULL << 2)
#define AGENT_FILE_PREFETCH_REASON_STAGE_INDEX (1ULL << 3)
#define AGENT_FILE_PREFETCH_REASON_HANDOFF     (1ULL << 4)
#define AGENT_FILE_PREFETCH_REASON_SPAN_BUS    (1ULL << 5)
#define AGENT_FILE_PREFETCH_SPAN_MAX 32

#define AGENT_DEPENDENCY_F_USER (1ULL << 0)

#define AGENT_PROVENANCE_NODE_CONTEXT  1
#define AGENT_PROVENANCE_NODE_AUDIT    2
#define AGENT_PROVENANCE_NODE_PREFETCH 3

#define AGENT_PROVENANCE_EDGE_CONTEXT  1
#define AGENT_PROVENANCE_EDGE_AUDIT    2
#define AGENT_PROVENANCE_EDGE_PREFETCH 3
#define AGENT_PROVENANCE_MAX_EDGES \
	(AGENT_CONTEXT_MAX_RECORDS + AGENT_AUDIT_MAX_RECORDS + \
	 AGENT_FILE_PREFETCH_MAX_HINTS)

#define AGENT_EVENT_PAYLOAD_SIZE 64
#define AGENT_WATCH_FILTER_SIZE  64

#define AGENT_EVENT_NONE          0
#define AGENT_EVENT_FILE_STATUS   1
#define AGENT_EVENT_MESSAGE       2
#define AGENT_EVENT_TIMER         3
#define AGENT_EVENT_JOB_DONE      4
#define AGENT_EVENT_POLICY_DENIED 5
#define AGENT_EVENT_CONTEXT_LIMIT 6
#define AGENT_EVENT_LLM_DONE      7
#define AGENT_EVENT_DASHBOARD_EXPORT 8
#define AGENT_EVENT_CANCELLED     9
#define AGENT_EVENT_MAX           AGENT_EVENT_CANCELLED

#define AGENT_EVENT_MASK(type) (1ULL << (type))
#define AGENT_IPC_EVENT_MESSAGE  AGENT_EVENT_MASK(AGENT_EVENT_MESSAGE)
#define AGENT_IPC_EVENT_LLM_DONE AGENT_EVENT_MASK(AGENT_EVENT_LLM_DONE)
#define AGENT_IPC_EVENT_MASK \
	(AGENT_IPC_EVENT_MESSAGE | AGENT_IPC_EVENT_LLM_DONE)

#define AGENT_IPC_ROUTE_REVOKE 0
#define AGENT_IPC_ROUTE_GRANT  1

#define AGENT_ROLE_SENTINEL      1
#define AGENT_ROLE_INVESTIGATOR  2
#define AGENT_ROLE_RECOVERY      3
#define AGENT_ROLE_ORCHESTRATOR  4
#define AGENT_ROLE_ARTIFACT      5

#define AGENT_ROLE_GRANT_BIT(role) (1ULL << ((role) - 1))
#define AGENT_ROLE_GRANT_ALL \
	(AGENT_ROLE_GRANT_BIT(AGENT_ROLE_SENTINEL) | \
	 AGENT_ROLE_GRANT_BIT(AGENT_ROLE_INVESTIGATOR) | \
	 AGENT_ROLE_GRANT_BIT(AGENT_ROLE_RECOVERY) | \
	 AGENT_ROLE_GRANT_BIT(AGENT_ROLE_ORCHESTRATOR) | \
	 AGENT_ROLE_GRANT_BIT(AGENT_ROLE_ARTIFACT))

#define AGENT_CAP_META_READ     (1ULL << 0)
#define AGENT_CAP_CONTENT_READ  (1ULL << 1)
#define AGENT_CAP_PROCESS_READ  (1ULL << 2)
#define AGENT_CAP_MESSAGE_SEND  (1ULL << 3)
#define AGENT_CAP_WATCH         (1ULL << 4)
#define AGENT_CAP_ACTION_WRITE  (1ULL << 5)
#define AGENT_CAP_ARTIFACT_WRITE (1ULL << 6)
#define AGENT_CAP_AUDIT_WRITE   (1ULL << 7)
#define AGENT_CAP_META_WRITE    (1ULL << 8)
#define AGENT_CAP_ORCHESTRATE   (1ULL << 9)
#define AGENT_CAP_LLM_RELAY     (1ULL << 10)
#define AGENT_CAP_WAIT_CANCEL   (1ULL << 11)
#define AGENT_CAP_ROUTE_MANAGE  (1ULL << 12)
#define AGENT_CAP_RECOVER_STAGE AGENT_CAP_ACTION_WRITE
#define AGENT_CAP_REPORT_WRITE  AGENT_CAP_ARTIFACT_WRITE
#define AGENT_CAP_DEPENDENCY_UPDATE AGENT_CAP_META_WRITE

#define AGENT_DEP_SLOT(n) (1ULL << ((n) & 63))

struct agent_info {
	int is_agent;
	int agent_id;
	int agent_role;
	uint64 context_base;
	uint64 context_size;
	int agent_type;
	int heartbeat_interval;
	int resource_quota;
	int loop_state;
	uint64 agent_call_count;
	uint64 metadata_txn_wait_count;
	uint64 metadata_writeback_dirty;
	uint64 metadata_writeback_durable;
	uint64 metadata_writeback_requests;
	uint64 metadata_writeback_coalesced;
	uint64 metadata_writeback_commits;
	uint64 metadata_writeback_pending;
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
	uint64 event_queue_count;
	uint64 watch_count;
	uint64 wait_count;
	uint64 wait_loop_count;
	uint64 wait_sleep_count;
	uint64 wait_wakeup_count;
	uint64 wait_cancel_count;
	uint64 timeout_count;
	uint64 last_heartbeat_tick;
	uint64 current_tick;
	uint64 capability_mask;
	uint64 file_scan_runs;
	uint64 file_scan_entries;
	uint64 file_scan_added;
	uint64 file_scan_updated;
	uint64 file_scan_removed;
	uint64 file_scan_generation;
	uint64 file_scan_pending;
	uint64 file_digest_cache_hits;
	uint64 file_digest_cache_misses;
	int sched_policy;
	int sched_weight;
	int sched_priority;
	uint64 sched_budget;
	uint64 sched_dispatch_count;
	uint64 sched_event_dispatch_count;
	uint64 sched_deadline_dispatch_count;
	uint64 sched_vruntime;
	uint64 sched_ready_tick;
	uint64 sched_last_dispatch_tick;
	uint64 sched_preemptions;
	uint64 sched_budget_used;
	uint64 sched_last_score;
	uint64 sched_last_reason;
	uint64 sched_trace_count;
	uint64 current_span_id;
	uint64 current_cause_sequence;
	uint64 provenance_edges;
	uint64 observe_epoch;
	uint64 timeline_wait_count;
	uint64 timeline_wait_sleep_count;
	uint64 timeline_wait_wakeup_count;
	uint64 timeline_wait_timeout_count;
	uint64 filesystem_domain;
	uint64 filesystem_capability_mask;
	uint64 legacy_mailbox_allocated;
	uint64 legacy_mailbox_pages;
	uint64 legacy_mailbox_queue_count;
	uint64 file_scan_deferred;
	uint64 file_scan_failures;
	uint64 metadata_journal_txns;
	uint64 metadata_journal_blocks;
	uint64 metadata_compactions;
	uint64 metadata_full_cow_blocks;
};

struct agent_sched_record {
	uint64 tick;
	uint64 dispatch_count;
	uint64 score;
	uint64 reason_flags;
	uint64 event_queue_count;
	uint64 ready_age;
	uint64 deadline_delta;
	uint64 heartbeat_due;
	uint64 vruntime;
	uint64 budget_used;
	int pid;
	int tid;
	int role;
	int loop_state;
	int weight;
	int priority;
};

struct agent_sched_config {
	uint64 update_mask;
	int target_pid;
	int policy;
	int weight;
	int priority;
	uint64 budget;
};

struct agent_trace_record {
	uint64 tick;
	uint64 sequence;
	uint64 cause_sequence;
	uint64 span_id;
	uint64 value0;
	uint64 value1;
	uint64 value2;
	uint64 flags;
	int kind;
	int tool_id;
	int status;
	int role;
	int loop_state;
	int pid;
	int tid;
	char text[AGENT_CONTEXT_TEXT_SIZE];
};

struct agent_audit_record {
	uint64 sequence;
	uint64 tick;
	uint64 cause_sequence;
	uint64 span_id;
	uint64 workflow_lifecycle_generation;
	uint64 branch_generation;
	uint64 cause_branch_generation;
	uint64 actor_control_id;
	uint64 cause_control_id;
	uint64 cause_record_hash;
	uint64 prev_hash;
	uint64 record_hash;
	uint64 value0;
	uint64 value1;
	uint64 value2;
	uint64 flags;
	int kind;
	uint workflow_lifecycle_id;
	int pid;
	int tid;
	int source_pid;
	int target_pid;
	int agent_id;
	int role;
	int loop_state;
	int tool_id;
	int event_type;
	int status;
	char text[AGENT_AUDIT_TEXT_SIZE];
};

#include "../agent_observe_abi.h"

struct agent_ledger_summary {
	int version;
	int reserved;
	uint64 oldest_sequence;
	uint64 latest_sequence;
	uint64 visible_records;
	uint64 total_records;
	uint64 dropped_records;
	uint64 ledger_hash;
	uint64 context_records;
	uint64 event_records;
	uint64 sched_records;
	uint64 prefetch_records;
	uint64 timeline_total;
	uint64 observe_epoch;
};

struct agent_audit_filter {
	uint64 flags;
	uint64 start_sequence;
	uint64 span_id;
	int kind;
	int pid;
	int source_pid;
	int target_pid;
	int role;
	int tool_id;
	int event_type;
	int status;
};

struct agent_timeline_record {
	uint64 tick;
	uint64 sequence;
	uint64 cause_sequence;
	uint64 span_id;
	uint64 workflow_lifecycle_generation;
	uint64 branch_generation;
	uint64 cause_branch_generation;
	uint64 actor_control_id;
	uint64 cause_control_id;
	uint64 cause_record_hash;
	uint64 value0;
	uint64 value1;
	uint64 value2;
	uint64 flags;
	int source;
	int kind;
	uint workflow_lifecycle_id;
	int pid;
	int tid;
	int source_pid;
	int target_pid;
	int role;
	int loop_state;
	int tool_id;
	int event_type;
	int status;
	char text[AGENT_AUDIT_TEXT_SIZE];
};

struct agent_timeline_filter {
	uint64 flags;
	uint64 source_mask;
	uint64 start_tick;
	uint64 span_id;
	uint64 require_flags;
	uint64 after_tick;
	uint64 after_sequence;
	int kind;
	int pid;
	int source_pid;
	int target_pid;
	int role;
	int tool_id;
	int event_type;
	int status;
	int after_source;
};

struct agent_provenance_edge {
	uint64 span_id;
	uint64 source_sequence;
	uint64 target_sequence;
	uint64 tick;
	uint64 workflow_lifecycle_generation;
	uint64 source_branch_generation;
	uint64 target_branch_generation;
	uint64 source_control_id;
	uint64 target_control_id;
	uint64 source_record_hash;
	uint64 target_record_hash;
	uint64 flags;
	uint64 value0;
	uint64 value1;
	uint64 value2;
	int kind;
	uint workflow_lifecycle_id;
	int source_type;
	int target_type;
	int source_pid;
	int target_pid;
	int role;
	int loop_state;
	int tid;
	int tool_id;
	int event_type;
	int status;
	char text[AGENT_AUDIT_TEXT_SIZE];
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
	uint64 user_cache_offset;
	uint64 user_cache_size;
	uint64 current_span_id;
	uint64 current_cause_sequence;
	uint64 latest_record_hash;
	uint64 provenance_edges;
	uint64 workflow_lifecycle_generation;
	uint64 branch_generation;
	uint64 visible_head_sequence;
	uint64 active_path_count;
	uint64 active_path_oldest_sequence;
	uint workflow_lifecycle_id;
	uint eviction_policy;
};

struct agent_context_record {
	uint64 sequence;
	uint64 request_id;
	uint64 cause_sequence;
	uint64 span_id;
	uint64 branch_generation;
	uint64 path_parent_sequence;
	uint64 arg0;
	uint64 value0;
	uint64 value1;
	uint64 value2;
	uint64 tick;
	uint64 flags;
	uint64 prev_hash;
	uint64 record_hash;
	int tool_id;
	int status;
	char payload[AGENT_CONTEXT_TEXT_SIZE];
	char result[AGENT_CONTEXT_TEXT_SIZE];
};

struct agent_context_detail {
	uint64 sequence;
	uint64 flags;
	struct agent_op op;
	struct agent_result result;
};

struct agent_event {
	int type;
	int source_pid;
	int target_pid;
	int status;
	uint64 event_id;
	uint64 tick;
	uint64 corr_id;
	uint64 cause_sequence;
	uint64 span_id;
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
	uint64 flags;
	uint64 dev;
	uint64 inum;
	uint64 incarnation;
	uint64 size;
	uint64 fs_generation;
	uint64 update_mask;
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
	uint64 dev;
	uint64 inum;
	uint64 incarnation;
	uint64 size;
	uint64 fs_generation;
};

struct agent_file_prefetch_hint {
	uint64 sequence;
	uint64 source_sequence;
	uint64 span_id;
	uint64 workflow_lifecycle_generation;
	uint64 branch_generation;
	uint64 actor_control_id;
	uint64 cause_branch_generation;
	uint64 cause_control_id;
	uint64 cause_record_hash;
	uint64 reason;
	uint64 score;
	uint64 tick;
	uint64 fs_generation;
	int fid;
	uint workflow_lifecycle_id;
	int actor_tid;
	int actor_role;
	int actor_loop_state;
	int source_fid;
	int source_pid;
	int target_pid;
	int plan;
	int candidate_records;
	int total_hits;
	struct agent_file_hit hit;
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
	int plan;
	int index_bucket;
	int candidate_records;
	/* Slots actually visited while rebuilding an invalid index. */
	int index_rebuild_records;
	int reserved;
	uint64 query_ticks;
	uint64 plan_reason;
	uint64 fs_generation;
	struct agent_file_hit hits[AGENT_FILE_QUERY_MAX_HITS];
};

struct agent_file_edit_state {
	int active;
	int owner_pid;
	int owner_agent_id;
	int owner_role;
	int dirty;
	uint64 lease_id;
	uint64 dev;
	uint64 inum;
	uint64 incarnation;
	uint64 base_version;
	uint64 current_version;
	uint64 deadline_tick;
	uint64 conflict_count;
	char path[AGENT_FILE_LOGICAL_SIZE];
};

struct proc;
struct workflow_lifecycle_key;
struct inode;
struct thread;

void agentinit(void);
void agent_storage_init(void);
void agent_proc_prepare(struct proc *p);
void agent_proc_teardown(struct proc *p);
void agent_thread_runtime_transition(struct thread *t, int transition);
void agent_process_image_install_locked(struct proc *p);
void agent_observe_thread_reset(struct thread *t);
int agent_exec_public_identity_commit(struct proc *p);
int agent_ipc_legacy_mailbox_empty(const struct proc *p);
void agent_scope_controller_departing(struct proc *p);
void agent_authority_bootstrap(struct proc *p);
void agent_authority_on_exec(struct proc *p);
int agent_authority_check(struct proc *p, int role);
int agent_alias_exec_context(struct proc *p, pagetable_t pagetable);
void agent_unmap_exec_context(struct proc *p, pagetable_t pagetable);
int agent_make_role(struct proc *p, int role);
int agent_create_proc(void);
int agent_create_role_proc(int role);
int agent_workflow_create_proc(int role);
void agent_tick(void);
void agent_background_request(void);
void agent_background_maintain(void);
void agent_background_checkpoint(void);
int agent_metadata_durability_fence_current(void);
int agent_metadata_quiescence_fence_current(void);
void agent_file_request_scan(void);
int agent_scope_reclaim_begin(
	uint scope_id, struct workflow_lifecycle_key, uint64 *metadata_target);
int agent_scope_reclaim_metadata_done(
	uint scope_id, struct workflow_lifecycle_key, uint64 metadata_target);
void agent_file_version_reclaim(struct inode *ip);
int agent_edit_write_allowed(struct inode *ip);
int agent_edit_write_lease_allowed(struct inode *, uint64 *, uint64 *);
int agent_edit_write_lease_snapshot(struct inode *, uint64 *, uint64 *);
int agent_edit_truncate_allowed(struct inode *ip);
int agent_edit_unlink_allowed(struct inode *ip);
void agent_edit_note_write(struct inode *ip);
void agent_edit_note_truncate(struct inode *ip);
void agent_edit_note_delete(struct inode *ip);
int agent_file_is_meta_store_name(char *path);
void agent_sched_on_enqueue(struct thread *t);
void agent_sched_on_dispatch(struct thread *t);
void agent_sched_on_yield(struct thread *t);
int agent_sched_better(struct thread *a, struct thread *b);

int sys_agent_create(void);
int sys_agent_create_role(int role);
int sys_agent_workflow_create(int role);
int sys_agent_workflow_close(uint64 scope_id);
int sys_agent_workflow_lifecycle_info(uint64 addr, uint64 user_size,
				      uint64 flags, uint64 expected_id,
				      uint64 expected_generation);
int sys_agent_resource_snapshot(uint64 addr, uint64 user_size);
int sys_agent_performance_snapshot(uint64 addr, uint64 user_size);
int sys_agent_scope_delegate_fd(int fd);
int sys_agent_info(uint64 addr);
int sys_agent_sched_snapshot(uint64 recordsaddr, int max);
int sys_agent_sched_config(uint64 configaddr);
int sys_agent_trace_snapshot(uint64 recordsaddr, int max);
int sys_agent_audit_snapshot(uint64 recordsaddr, int max);
int sys_agent_audit_query(uint64 filteraddr, uint64 recordsaddr, int max);
int sys_agent_audit_receipt(uint64 requestaddr);
int sys_agent_observe_recovery(uint64 requestaddr, uint64 recordsaddr);
int sys_agent_span_trace_snapshot(uint64 recordsaddr, int max);
int sys_agent_timeline_snapshot(uint64 recordsaddr, int max);
int sys_agent_timeline_query(uint64 filteraddr, uint64 recordsaddr, int max);
int sys_agent_timeline_wait(uint64 filteraddr, int timeout_ticks);
int sys_agent_timeline_read(uint64 filteraddr, uint64 recordsaddr, int max,
			    int timeout_ticks);
int sys_agent_provenance_snapshot(uint64 edgesaddr, int max);
int sys_agent_ledger_snapshot(uint64 summaryaddr);
int sys_agent_run(uint64 opsaddr, uint64 resultsaddr, int count, uint64 flags);
int sys_agent_call(uint64 reqaddr, uint64 respaddr);
int sys_agent_tool_list(uint64 addr, int max);
int sys_tool_call(uint64 reqaddr, uint64 respaddr);
int sys_tool_list(uint64 addr, int max, uint desc_size, uint version);
int sys_context_push(uint64 recordaddr);
int sys_context_query(uint64 start_sequence, uint64 outaddr, int max);
int sys_context_snapshot(uint64 headeraddr, uint64 recordsaddr, int max);
int sys_context_detail(uint64 sequence, uint64 detailaddr);
int sys_context_rollback(uint64 sequence);
int sys_context_clear(void);
int sys_agent_watch(int event_type, uint64 filteraddr);
int sys_agent_unwatch(int event_type, uint64 filteraddr);
int sys_agent_wait(uint64 eventaddr, int timeout_ticks);
int sys_agent_wait_cancel(int pid, uint64 reasonaddr);
int sys_agent_heartbeat(uint64 interval_ticks);
int sys_agent_heartbeat_set(uint64 interval_ticks);
int sys_agent_heartbeat_stop(void);
#ifdef AGENT_METADATA_CRASH_PHASE
int sys_agent_metadata_test(uint command, uint64 armaddr, uint64 user_size);
#endif
int sys_agent_wake(int pid, uint64 eventaddr);
int sys_agent_file_meta_init(void);
int sys_agent_file_meta_set(uint64 metaaddr);
int sys_agent_file_query(uint64 queryaddr, uint64 resultaddr);
int sys_agent_file_edit_begin(uint64 pathaddr, uint64 flags, int ttl_ticks,
			      uint64 stateaddr);
int sys_agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
			       uint64 stateaddr);
int sys_agent_file_edit_abort(uint64 lease_id);
int sys_agent_file_edit_state(uint64 pathaddr, uint64 stateaddr);
int sys_agent_worker_create(uint64 pathaddr, uint64 requested_caps);
int sys_agent_route_config(int source_pid, int target_pid, uint64 event_mask,
			   int operation);
int sys_agent_file_prefetch_snapshot(uint64 hintsaddr, int max);
int sys_agent_file_prefetch_span_snapshot(uint64 hintsaddr, int max);

#endif
