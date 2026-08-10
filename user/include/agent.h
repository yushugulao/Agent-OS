#ifndef USER_AGENT_H
#define USER_AGENT_H

#include <stddef.h>
#include "../../agent_execution_contract_abi.h"
#include "../../agent_task_channel_abi.h"
#include "../../agent_lifecycle_abi.h"
#include "../../agent_performance_abi.h"
#include "../../agent_provenance_abi.h"
#include "../../agent_resource_abi.h"
#include "../../agent_tool_abi.h"
#include "../../agent_workflow_fence_abi.h"
#define AGENT_TYPE_NONE  0
#define AGENT_TYPE_AGENT 1

#define AGENT_LOOP_NONE    0
#define AGENT_LOOP_IDLE    1
#define AGENT_LOOP_RUNNING 2
#define AGENT_LOOP_WAITING 3

#define AGENT_CONTEXT_TEXT_SIZE 16

#define AGENT_PAGE_SIZE 4096
#define AGENT_CONTEXT_KERNEL_PAGES 6
#define AGENT_CONTEXT_PAGES 7
#define AGENT_CONTEXT_SIZE (AGENT_CONTEXT_PAGES * AGENT_PAGE_SIZE)
#define AGENT_CONTEXT_MAGIC 0x4147435458543031ULL
#define AGENT_CONTEXT_VERSION 9
#define AGENT_CONTEXT_MAX_RECORDS 128
#define AGENT_USER_TOP (1L << (9 + 9 + 9 + 12 - 1))
#define AGENT_TRAMPOLINE (AGENT_USER_TOP - AGENT_PAGE_SIZE)
#define AGENT_TRAPFRAME (AGENT_TRAMPOLINE - AGENT_PAGE_SIZE)
#define AGENT_CONTEXT_BASE (AGENT_TRAPFRAME - (16 + AGENT_CONTEXT_PAGES) * AGENT_PAGE_SIZE)
#define AGENT_CONTEXT_LATEST_RESPONSE_OFFSET (sizeof(struct agent_context_header))
#define AGENT_CONTEXT_RECORDS_OFFSET AGENT_PAGE_SIZE
#define AGENT_CONTEXT_PUBLISH_SEQUENCE_OFFSET \
	(AGENT_CONTEXT_RECORDS_OFFSET - sizeof(uint64))

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
#define AGENT_SCHED_TRACE_CAP        8
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
/* v8 磁盘检查点仍可恢复该停产类型，新代码不再生成。 */
#define AGENT_AUDIT_KIND_PREFETCH      5
#define AGENT_AUDIT_MAX_RECORDS        512
#define AGENT_AUDIT_TEXT_SIZE          32
#define AGENT_LEDGER_VERSION           3

#define AGENT_TIMELINE_SOURCE_CONTEXT  1
#define AGENT_TIMELINE_SOURCE_SCHED    2
#define AGENT_TIMELINE_SOURCE_AUDIT    3
#define AGENT_TIMELINE_MAX_RECORDS     512

#define AGENT_TIMELINE_SOURCE_MASK_CONTEXT \
	(1ULL << AGENT_TIMELINE_SOURCE_CONTEXT)
#define AGENT_TIMELINE_SOURCE_MASK_SCHED \
	(1ULL << AGENT_TIMELINE_SOURCE_SCHED)
#define AGENT_TIMELINE_SOURCE_MASK_AUDIT \
	(1ULL << AGENT_TIMELINE_SOURCE_AUDIT)
#define AGENT_TIMELINE_SOURCE_MASK_ALL \
	(AGENT_TIMELINE_SOURCE_MASK_CONTEXT | \
	 AGENT_TIMELINE_SOURCE_MASK_SCHED | \
	 AGENT_TIMELINE_SOURCE_MASK_AUDIT)

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
/*
 * Compatibility tombstones.  Their numeric values remain frozen, but
 * agent_file_meta_set() rejects both bits: live-query metadata is volatile
 * and enters the index only through an explicit metadata update.
 */
#define AGENT_FILE_META_F_PERSIST 2
#define AGENT_FILE_META_F_AUTOSCAN 4
#define AGENT_FILE_META_F_UNSUPPORTED_MASK \
	(AGENT_FILE_META_F_PERSIST | AGENT_FILE_META_F_AUTOSCAN)

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

#define AGENT_DEPENDENCY_F_USER (1ULL << 0)

#define AGENT_PROVENANCE_NODE_CONTEXT  1
#define AGENT_PROVENANCE_NODE_AUDIT    2

#define AGENT_PROVENANCE_EDGE_CONTEXT  1
#define AGENT_PROVENANCE_EDGE_AUDIT    2
#define AGENT_PROVENANCE_MAX_EDGES \
	(AGENT_CONTEXT_MAX_RECORDS + AGENT_AUDIT_MAX_RECORDS)

#define AGENT_EVENT_PAYLOAD_SIZE 64
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
#define AGENT_EVENT_FILE_QUERY   10
#define AGENT_EVENT_MAX           AGENT_EVENT_FILE_QUERY

#define AGENT_FILE_LIVE_WATCH_VERSION 1U
#define AGENT_FILE_LIVE_WATCH_F_RESYNC_REQUIRED (1U << 0)
#define AGENT_FILE_LIVE_WATCH_F_ACK_RESYNC      (1U << 1)

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

static inline uint64
agent_dependency_label_bit(const char *label)
{
	uint64 hash = 1469598103934665603ULL;
	int bit;

	if (!label || !label[0])
		return 0;
	for (int i = 0; label[i] && i < AGENT_FILE_FIELD_SIZE; i++) {
		hash ^= (unsigned char)label[i];
		hash *= 1099511628211ULL;
	}
	bit = hash % 60;
	return AGENT_DEP_SLOT(bit);
}

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
	/* 退役 ABI 字段，内核恒填零。 */
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

#include "../../agent_observe_abi.h"

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
	uint64 other_records;
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

struct agent_file_live_watch {
	unsigned int version;
	unsigned int flags;
	unsigned long long watch_id;
	unsigned long long initial_generation;
	unsigned long long catalog_generation;
	unsigned long long resync_generation;
	struct agent_file_query query;
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
	/* 重建无效索引时实际访问的槽位数。 */
	int index_rebuild_records;
	union {
		int reserved;
		unsigned int provenance_labels;
	};
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

int agent_create(void);
int agent_create_role(int role);
int agent_workflow_create(int role);
int agent_workflow_close(uint64 scope_id);
int agent_workflow_lifecycle_info(
	struct agent_workflow_lifecycle_info *info,
	const struct agent_workflow_lifecycle_key *expected);
int agent_execution_contract(
	const struct agent_execution_contract_control *control,
	struct agent_execution_contract_result *result);
int agent_task_channel_setup(
	const struct agent_task_channel_setup *request,
	struct agent_task_channel_setup_result *result);
int agent_task_channel_enter(
	const struct agent_task_channel_enter *request,
	struct agent_task_channel_enter_result *result);
int agent_task_channel_resource(
	const struct agent_task_channel_resource *request,
	struct agent_task_channel_resource_result *result);
int agent_resource_snapshot(struct agent_resource_snapshot *snapshot);
int agent_performance_snapshot(struct agent_performance_snapshot *snapshot);
int agent_scope_delegate_fd(int fd);
int agent_worker_create(const char *image, uint64 capabilities);
int agent_info(struct agent_info *info);
int agent_launch_info(struct agent_info *info);
int agent_sched_snapshot(struct agent_sched_record *records, int max);
int agent_sched_config(struct agent_sched_config *config);
int agent_trace_snapshot(struct agent_trace_record *records, int max);
int agent_audit_snapshot(struct agent_audit_record *records, int max);
int agent_audit_query(struct agent_audit_filter *filter,
		      struct agent_audit_record *records, int max);
int agent_audit_receipt(struct agent_audit_receipt_request *request);
/* Compatibility tombstone: retained for source ABI; always unsupported. */
int agent_observe_recovery(struct agent_observe_recovery_request *request,
			   void *records);
int agent_span_trace_snapshot(struct agent_audit_record *records, int max);
int agent_timeline_snapshot(struct agent_timeline_record *records, int max);
int agent_timeline_query(struct agent_timeline_filter *filter,
			 struct agent_timeline_record *records, int max);
int agent_timeline_wait(struct agent_timeline_filter *filter, int timeout_ticks);
int agent_timeline_read(struct agent_timeline_filter *filter,
			struct agent_timeline_record *records, int max,
			int timeout_ticks);
int agent_provenance_snapshot(struct agent_provenance_edge *edges, int max);
int agent_ledger_snapshot(struct agent_ledger_summary *summary);
int agent_run(struct agent_op *ops, struct agent_result *results, int count, uint64 flags);
int agent_workflow_fence(const struct agent_workflow_fence_request *request,
			 struct agent_workflow_fence_receipt *receipt);
int agent_call(struct agent_request *req, struct agent_response *resp);
int agent_tool_list(struct agent_tool_desc *out, int max);
int sys_tool_call(struct agent_request_v2 *req, struct agent_response_v2 *resp);
int tool_call_v3(struct agent_request_v3 *req, struct agent_response_v3 *resp);
int sys_tool_list(struct agent_tool_desc_v2 *out, int max);
int tool_call(struct agent_request_v2 *req, struct agent_response_v2 *resp);
int tool_list(struct agent_tool_desc_v2 *out, int max);
int context_push(struct agent_context_record *record);
int context_query(uint64 start_sequence, struct agent_context_record *out, int max);
int context_snapshot(struct agent_context_header *header, struct agent_context_record *records, int max);
int context_mirror_active_query(const struct agent_context_header *header,
				const struct agent_context_record *mirror_records,
				uint64 start_sequence,
				struct agent_context_record *out, int max);
int context_direct_active_query(uint64 context_base, uint64 start_sequence,
				struct agent_context_record *out, int max);
int context_direct_header_snapshot(uint64 context_base,
				   struct agent_context_header *header);
int context_detail(uint64 sequence, struct agent_context_detail *detail);
int context_rollback(uint64 sequence);
int context_clear(void);
int agent_watch(int event_type, const char *filter);
int agent_unwatch(int event_type, const char *filter);
int agent_live_watch(struct agent_file_live_watch *watch);
int agent_live_unwatch(struct agent_file_live_watch *watch);
int agent_wait(struct agent_event *event, int timeout_ticks);
int agent_wait_cancel(int pid, const char *reason);
int agent_heartbeat(int interval_ticks);
int sys_agent_heartbeat_set(uint64 interval_ticks);
int sys_agent_heartbeat_stop(void);
int agent_heartbeat_set(uint64 interval_ticks);
int agent_heartbeat_stop(void);
#ifdef WAIT_ATOMIC_TEST_PROFILE
int wait_atomic_test_arm(uint operation);
struct wait_atomic_test_receipt;
struct wait_atomic_deadline_snapshot;
int wait_atomic_test_query(uint operation, int target_pid,
			   struct wait_atomic_test_receipt *receipt);
int wait_atomic_test_deadline_observe(
	uint phase, struct wait_atomic_deadline_snapshot *snapshot);
#endif
int agent_wake(int pid, struct agent_event *event);
int agent_file_meta_init(void);
int agent_file_meta_set(struct agent_file_meta *meta);
int agent_file_query(struct agent_file_query *query, struct agent_file_query_result *result);
int agent_file_edit_begin(const char *path, uint64 flags, int ttl_ticks,
			  struct agent_file_edit_state *state);
int agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
			   struct agent_file_edit_state *state);
int agent_file_edit_abort(uint64 lease_id);
int agent_file_edit_state(const char *path,
			  struct agent_file_edit_state *state);
int agent_route_config(int source_pid, int target_pid, uint64 event_mask,
		       int operation);
#endif
