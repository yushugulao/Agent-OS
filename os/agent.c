#include "agent.h"
#include "defs.h"
#include "file.h"
#include "fs.h"
#include "loader.h"
#include "timer.h"
#include "trap.h"

extern struct proc pool[NPROC];

#define AGENT_FILE_INDEX_BUCKETS 16
#define AGENT_ACTION_HISTORY_MAX 32
#define AGENT_FILE_QUERY_CACHE_MAX 8
#define AGENT_FILE_DIGEST_CACHE_MAX 8
#define AGENT_FILE_EDIT_MAX 32
#define AGENT_FILE_VERSION_MAX 512
#define AGENT_DEPENDENCY_MAX 64
#define AGENT_META_STORE_NAME ".agentmeta"
#define AGENT_META_STORE_MAGIC 0x41474d4554413034ULL
#define AGENT_INODE_META_VERSION 1
#define AGENT_FS_SCAN_INTERVAL 20
#define AGENT_FS_SCAN_STEP 16

static int nextagentid = 1;
static uint64 next_event_id = 1;
static uint64 agent_audit_next_sequence = 1;
static uint64 agent_audit_head;
static uint64 agent_audit_count;
static uint64 agent_audit_ledger_hash;
static uint64 agent_audit_kind_counts[AGENT_AUDIT_KIND_PREFETCH + 1];
static struct agent_audit_record agent_audit_records[AGENT_AUDIT_MAX_RECORDS];
static uint64 agent_span_prefetch_next_sequence = 1;
static uint64 agent_span_prefetch_head;
static uint64 agent_span_prefetch_count;
static struct agent_file_prefetch_hint
	agent_span_prefetch_hints[AGENT_FILE_PREFETCH_SPAN_MAX];
static uint64 agent_observe_epoch = 1;
static int agent_timeline_waiting_agents;

struct agent_file_query_cache_entry {
	int valid;
	uint64 fs_generation;
	struct agent_file_query key;
	struct agent_file_query_result result;
};

static struct agent_file_query_cache_entry
	agent_file_query_cache[AGENT_FILE_QUERY_CACHE_MAX];
static int agent_file_query_cache_head;

struct agent_file_digest_cache_entry {
	int valid;
	uint64 dev;
	uint64 inum;
	uint64 size;
	uint64 content_generation;
	uint64 bytes;
	uint64 hash;
	char preview[AGENT_FAST_RESULT_SIZE];
};

static struct agent_file_digest_cache_entry
	agent_file_digest_cache[AGENT_FILE_DIGEST_CACHE_MAX];
static int agent_file_digest_cache_head;
static uint64 agent_file_digest_cache_hits;
static uint64 agent_file_digest_cache_misses;

static long agent_sched_score_at(struct thread *t, uint64 now,
				 struct agent_sched_record *record);
static void agent_observe_record(struct agent_timeline_record *record);
static void agent_audit_context(struct proc *p,
				struct agent_context_record *record);
static void agent_audit_event(int kind, struct proc *actor,
			      struct agent_event *event);
static void agent_audit_sched(struct proc *p,
			      struct agent_sched_record *record);
static void agent_text_append(char *dst, int n, char *src);
static void agent_timeline_from_context(struct proc *p,
					struct agent_context_record *record,
					struct agent_timeline_record *timeline);
static void agent_timeline_from_sched(struct agent_sched_record *record,
				      struct agent_timeline_record *timeline);
static void agent_timeline_from_audit(struct agent_audit_record *record,
				      struct agent_timeline_record *timeline);
static void agent_timeline_from_prefetch(struct proc *p,
					 struct agent_file_prefetch_hint *hint,
					 struct agent_timeline_record *timeline);

static struct agent_tool_desc agent_tools[] = {
	{ AGENT_TOOL_ECHO, AGENT_TOOL_F_CALLABLE, "echo", "payload:string,arg0:uint64,arg1:uint64",
	  "return payload and numeric parameters" },
	{ AGENT_TOOL_PID_INFO, AGENT_TOOL_F_CALLABLE, "pid_info", "none",
	  "return pid, agent id, and agent flag" },
	{ AGENT_TOOL_CTX_STAT, AGENT_TOOL_F_CALLABLE, "ctx_stat", "none",
	  "return Agent Context base, size, and call count" },
	{ AGENT_TOOL_QUERY_PROCESS, AGENT_TOOL_F_CALLABLE, "query_process", "type:uint64",
	  "count processes and Agent processes" },
	{ AGENT_TOOL_GET_SYSTEM_STATUS, AGENT_TOOL_F_CALLABLE, "get_system_status", "none",
	  "return process count, agent count, and uptime tick" },
	{ AGENT_TOOL_READ_CONTEXT, AGENT_TOOL_F_CALLABLE, "read_context", "none",
	  "return post-state Context Path counters" },
	{ AGENT_TOOL_QUERY_FILE, AGENT_TOOL_F_CALLABLE, "query_file", "path|string-filter",
	  "query file inode metadata or Agent file metadata" },
	{ AGENT_TOOL_SEND_MESSAGE, AGENT_TOOL_F_CALLABLE,
	  "send_message",
	  "target_pid:uint64,message:string", "send a short Agent message" },
	{ AGENT_TOOL_READ_MESSAGE, AGENT_TOOL_F_CALLABLE, "read_message", "none",
	  "read current Agent mailbox" },
	{ AGENT_TOOL_FILE_META_INIT, AGENT_TOOL_F_CALLABLE, "file_meta_init", "none",
	  "reload file object metadata and rebuild indexes" },
	{ AGENT_TOOL_READ_FILE_SUMMARY, AGENT_TOOL_F_CALLABLE, "read_file_summary", "selector:string",
	  "read one indexed file summary" },
	{ AGENT_TOOL_DEPENDENCY_QUERY, AGENT_TOOL_F_CALLABLE, "dependency_query", "label:string",
	  "return registered dependent object labels" },
	{ AGENT_TOOL_CAPABILITY_CHECK, AGENT_TOOL_F_CALLABLE,
	  "capability_check",
	  "role:uint64,action:string", "check role capability" },
	{ AGENT_TOOL_RERUN_STAGE, AGENT_TOOL_F_CALLABLE, "rerun_stage", "role:uint64,stage:string",
	  "legacy action alias for a scoped state update" },
	{ AGENT_TOOL_WRITE_REPORT, AGENT_TOOL_F_CALLABLE, "write_report", "role:uint64,payload:string",
	  "legacy artifact alias for a scoped state update" },
	{ AGENT_TOOL_AGENT_WATCH, AGENT_TOOL_F_CALLABLE,
	  "agent_watch",
	  "event_type:uint64,filter:string", "register an Agent Loop watch" },
	{ AGENT_TOOL_AGENT_WAIT, AGENT_TOOL_F_SYSCALL_ONLY, "agent_wait", "timeout:uint64",
	  "wait for a watched event" },
	{ AGENT_TOOL_AGENT_HEARTBEAT, AGENT_TOOL_F_CALLABLE, "agent_heartbeat", "interval:uint64",
	  "set heartbeat interval" },
	{ AGENT_TOOL_CONTEXT_PUSH, AGENT_TOOL_F_SYSCALL_ONLY, "context_push",
	  "record", "manual Context Path append" },
	{ AGENT_TOOL_READ_FILE_DIGEST, AGENT_TOOL_F_CALLABLE,
	  "read_file_digest", "selector:string",
	  "read a real file preview and content digest" },
	{ AGENT_TOOL_ACTION_COMMIT, AGENT_TOOL_F_CALLABLE,
	  "action_commit", "selector:string",
	  "commit a generic Agent action against object metadata" },
	{ AGENT_TOOL_ARTIFACT_UPDATE, AGENT_TOOL_F_CALLABLE,
	  "artifact_update", "selector:string",
	  "update a generic Agent artifact state" },
	{ AGENT_TOOL_LLM_REQUEST, AGENT_TOOL_F_CALLABLE,
	  "llm_request", "target_pid:uint64,prompt_summary:string",
	  "record and route a structured LLM request" },
	{ AGENT_TOOL_LLM_RESPONSE, AGENT_TOOL_F_CALLABLE,
	  "llm_response", "target_pid:uint64,response_summary:string",
	  "return a structured LLM relay response" },
	{ AGENT_TOOL_DEPENDENCY_UPDATE, AGENT_TOOL_F_CALLABLE,
	  "dependency_update", "selector:string",
	  "register or update a generic object dependency" },
};

static struct agent_file_meta agent_files[AGENT_FILE_META_MAX];
static int agent_file_status_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_stage_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_kind_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_status_next[AGENT_FILE_META_MAX];
static int agent_file_stage_next[AGENT_FILE_META_MAX];
static int agent_file_kind_next[AGENT_FILE_META_MAX];
struct agent_action_history_entry {
	int tool_id;
	uint64 request_id;
	char project[AGENT_FILE_PROJECT_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
	char stage[AGENT_FILE_FIELD_SIZE];
};

struct agent_meta_store {
	uint64 magic;
	uint64 version;
	uint64 count;
	struct agent_file_meta records[AGENT_FILE_META_MAX];
};

struct agent_file_version_entry {
	int used;
	uint64 dev;
	uint64 inum;
	uint64 version;
};

struct agent_file_content_version_entry {
	int used;
	uint64 dev;
	uint64 inum;
	uint64 version;
};

struct agent_file_edit_entry {
	int active;
	int dirty;
	int owner_pid;
	int owner_agent_id;
	int owner_role;
	uint64 lease_id;
	uint64 dev;
	uint64 inum;
	uint64 base_version;
	uint64 deadline_tick;
	uint64 conflict_count;
	char path[AGENT_FILE_LOGICAL_SIZE];
};

struct agent_dependency_entry {
	int used;
	uint64 flags;
	char namespace[AGENT_FILE_PROJECT_SIZE];
	char run_id[AGENT_FILE_FIELD_SIZE];
	char source[AGENT_FILE_FIELD_SIZE];
	char target[AGENT_FILE_FIELD_SIZE];
	char relation[AGENT_FILE_FIELD_SIZE];
	char summary[AGENT_FILE_SUMMARY_SIZE];
};

static struct agent_action_history_entry
	agent_action_history[AGENT_ACTION_HISTORY_MAX];
static struct agent_meta_store agent_meta_store_buf;
static struct agent_file_version_entry
	agent_file_versions[AGENT_FILE_VERSION_MAX];
static struct agent_file_content_version_entry
	agent_file_content_versions[AGENT_FILE_VERSION_MAX];
static struct agent_file_edit_entry agent_file_edits[AGENT_FILE_EDIT_MAX];
static struct agent_dependency_entry agent_dependencies[AGENT_DEPENDENCY_MAX];
static int agent_action_history_count;
static int agent_file_loaded;
static int agent_meta_store_busy;
static uint64 agent_file_generation;
static uint64 agent_file_content_generation;
static int agent_file_scan_enabled;
static int agent_file_scan_pending;
static int agent_file_scan_active;
static uint64 agent_file_scan_offset;
static int agent_file_scan_seen[AGENT_FILE_META_MAX];
static uint64 agent_file_scan_next_tick;
static uint64 agent_file_scan_last_step_tick;
static uint64 agent_file_scan_runs;
static uint64 agent_file_scan_entries;
static uint64 agent_file_scan_added;
static uint64 agent_file_scan_updated;
static uint64 agent_file_scan_removed;
static uint64 agent_dependency_generation;
static volatile int agent_file_edit_guard;
static uint64 agent_file_edit_next_lease;

static void agent_file_reset_indexes(void);
static void agent_file_rebuild_indexes(void);
static int agent_file_bind_slot(int slot);
static int agent_file_persist(void);
static int agent_query_from_payload(struct agent_file_query *q, char *payload);
static void agent_file_enable_scan(void);
static void agent_dependency_rebuild_records(void);
static int agent_dependency_for_label(char *label, char *namespace,
				      char *run_id, uint64 *mask);
static uint64 agent_label_bit(char *label);

struct agent_proc_snapshot {
	int used;
	int agents;
	int runnable;
};

void agentinit(void)
{
	nextagentid = 1;
	next_event_id = 1;
	agent_audit_next_sequence = 1;
	agent_audit_head = 0;
	agent_audit_count = 0;
	agent_audit_ledger_hash = 0;
	memset(agent_audit_kind_counts, 0, sizeof(agent_audit_kind_counts));
	memset(agent_audit_records, 0, sizeof(agent_audit_records));
	agent_span_prefetch_next_sequence = 1;
	agent_span_prefetch_head = 0;
	agent_span_prefetch_count = 0;
	agent_observe_epoch = 1;
	agent_timeline_waiting_agents = 0;
	memset(agent_span_prefetch_hints, 0,
	       sizeof(agent_span_prefetch_hints));
	memset(agent_file_query_cache, 0, sizeof(agent_file_query_cache));
	agent_file_query_cache_head = 0;
	memset(agent_file_digest_cache, 0, sizeof(agent_file_digest_cache));
	agent_file_digest_cache_head = 0;
	agent_file_digest_cache_hits = 0;
	agent_file_digest_cache_misses = 0;
	agent_action_history_count = 0;
	memset(agent_action_history, 0, sizeof(agent_action_history));
	memset(agent_file_versions, 0, sizeof(agent_file_versions));
	memset(agent_file_content_versions, 0,
	       sizeof(agent_file_content_versions));
	memset(agent_file_edits, 0, sizeof(agent_file_edits));
	agent_file_loaded = 0;
	agent_meta_store_busy = 0;
	agent_file_generation = 0;
	agent_file_content_generation = 0;
	agent_file_scan_enabled = 0;
	agent_file_scan_pending = 0;
	agent_file_scan_active = 0;
	agent_file_scan_offset = 0;
	memset(agent_file_scan_seen, 0, sizeof(agent_file_scan_seen));
	agent_file_scan_next_tick = 0;
	agent_file_scan_last_step_tick = 0;
	agent_file_scan_runs = 0;
	agent_file_scan_entries = 0;
	agent_file_scan_added = 0;
	agent_file_scan_updated = 0;
	agent_file_scan_removed = 0;
	agent_dependency_generation = 0;
	agent_file_edit_guard = 0;
	agent_file_edit_next_lease = 1;
	memset(agent_files, 0, sizeof(agent_files));
	memset(agent_dependencies, 0, sizeof(agent_dependencies));
	agent_file_reset_indexes();
}

static uint64 agent_ticks(void)
{
	return get_cycle() / (CPU_FREQ / TICKS_PER_SEC);
}

static int agent_alloc_id(void)
{
	return nextagentid++;
}

static int agent_text_empty(char *s)
{
	return s == 0 || s[0] == 0;
}

static int agent_contains(char *haystack, char *needle)
{
	int hlen;
	int nlen;
	int i;

	if (needle == 0 || needle[0] == 0)
		return 1;
	if (haystack == 0)
		return 0;
	hlen = strlen(haystack);
	nlen = strlen(needle);
	if (nlen > hlen)
		return 0;
	for (i = 0; i <= hlen - nlen; i++)
		if (strncmp(haystack + i, needle, nlen) == 0)
			return 1;
	return 0;
}

static uint64 agent_hash_mix(uint64 h, uint64 v)
{
	for (int i = 0; i < 8; i++) {
		h ^= (uchar)(v & 0xff);
		h *= 1099511628211ULL;
		v >>= 8;
	}
	return h;
}

static uint64 agent_hash_bytes(uint64 h, char *buf, int n)
{
	for (int i = 0; i < n; i++) {
		h ^= (uchar)buf[i];
		h *= 1099511628211ULL;
	}
	return h;
}

static uint64 agent_context_record_hash(struct agent_context_record *record)
{
	uint64 h = 1469598103934665603ULL;

	h = agent_hash_mix(h, record->prev_hash);
	h = agent_hash_mix(h, record->sequence);
	h = agent_hash_mix(h, record->request_id);
	h = agent_hash_mix(h, record->cause_sequence);
	h = agent_hash_mix(h, record->span_id);
	h = agent_hash_mix(h, record->arg0);
	h = agent_hash_mix(h, record->value0);
	h = agent_hash_mix(h, record->value1);
	h = agent_hash_mix(h, record->value2);
	h = agent_hash_mix(h, record->tick);
	h = agent_hash_mix(h, record->flags);
	h = agent_hash_mix(h, (uint64)(uint)record->tool_id);
	h = agent_hash_mix(h, (uint64)(uint)record->status);
	h = agent_hash_bytes(h, record->payload, sizeof(record->payload));
	h = agent_hash_bytes(h, record->result, sizeof(record->result));
	return h ? h : 1;
}

static uint64 agent_audit_record_hash(struct agent_audit_record *record)
{
	uint64 h = 1469598103934665603ULL;

	h = agent_hash_mix(h, record->prev_hash);
	h = agent_hash_mix(h, record->sequence);
	h = agent_hash_mix(h, record->tick);
	h = agent_hash_mix(h, record->cause_sequence);
	h = agent_hash_mix(h, record->span_id);
	h = agent_hash_mix(h, record->value0);
	h = agent_hash_mix(h, record->value1);
	h = agent_hash_mix(h, record->value2);
	h = agent_hash_mix(h, record->flags);
	h = agent_hash_mix(h, (uint64)(uint)record->kind);
	h = agent_hash_mix(h, (uint64)(uint)record->pid);
	h = agent_hash_mix(h, (uint64)(uint)record->source_pid);
	h = agent_hash_mix(h, (uint64)(uint)record->target_pid);
	h = agent_hash_mix(h, (uint64)(uint)record->agent_id);
	h = agent_hash_mix(h, (uint64)(uint)record->role);
	h = agent_hash_mix(h, (uint64)(uint)record->loop_state);
	h = agent_hash_mix(h, (uint64)(uint)record->tool_id);
	h = agent_hash_mix(h, (uint64)(uint)record->event_type);
	h = agent_hash_mix(h, (uint64)(uint)record->status);
	h = agent_hash_bytes(h, record->text, sizeof(record->text));
	return h ? h : 1;
}

static int agent_context_layout_ok(void)
{
	uint64 cache_offset;

	if (AGENT_CONTEXT_LATEST_RESPONSE_OFFSET !=
	    sizeof(struct agent_context_header))
		return 0;
	if (AGENT_CONTEXT_RECORDS_OFFSET != PAGE_SIZE)
		return 0;
	cache_offset = AGENT_CONTEXT_RECORDS_OFFSET +
		       AGENT_CONTEXT_MAX_RECORDS *
			       sizeof(struct agent_context_record);
	if (cache_offset > AGENT_CONTEXT_SIZE)
		return 0;
	if (cache_offset == AGENT_CONTEXT_SIZE)
		return 0;
	return 1;
}

static uint64 agent_context_user_cache_offset(void)
{
	return AGENT_CONTEXT_RECORDS_OFFSET +
	       AGENT_CONTEXT_MAX_RECORDS *
		       sizeof(struct agent_context_record);
}

static uint64 agent_context_user_cache_size(void)
{
	uint64 offset = agent_context_user_cache_offset();

	if (offset >= AGENT_CONTEXT_SIZE)
		return 0;
	return AGENT_CONTEXT_SIZE - offset;
}

static void agent_free_shadow(uint64 *kva)
{
	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++) {
		if (kva[i]) {
			kfree((void *)kva[i]);
			kva[i] = 0;
		}
	}
}

static void agent_timeline_waiting_set(struct proc *p, int waiting)
{
	if (p == 0)
		return;
	waiting = waiting ? 1 : 0;
	if (p->agent_timeline_waiting == waiting)
		return;
	if (waiting)
		agent_timeline_waiting_agents++;
	else if (agent_timeline_waiting_agents > 0)
		agent_timeline_waiting_agents--;
	p->agent_timeline_waiting = waiting;
}

void agent_clear_metadata(struct proc *p)
{
	agent_timeline_waiting_set(p, 0);
	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++) {
		p->agent_ctx_kva[i] = 0;
		p->agent_shadow_kva[i] = 0;
	}
	p->is_agent = 0;
	p->agent_type = AGENT_TYPE_NONE;
	p->agent_id = 0;
	p->agent_role = 0;
	p->agent_ctx_base = 0;
	p->agent_ctx_size = 0;
	p->agent_call_count = 0;
	p->heartbeat_interval = 0;
	p->resource_quota = 0;
	p->loop_state = AGENT_LOOP_NONE;
	p->context_path_count = 0;
	p->context_path_capacity = 0;
	p->context_path_head = 0;
	p->context_path_oldest = 0;
	p->context_path_latest = 0;
	p->context_path_dropped = 0;
	p->context_path_rollback_count = 0;
	p->latest_response_offset = 0;
	p->records_offset = 0;
	p->agent_mailbox_valid = 0;
	p->agent_mailbox_from = 0;
	memset(p->agent_mailbox, 0, sizeof(p->agent_mailbox));
	p->agent_watch_count = 0;
	memset(p->agent_watch_valid, 0, sizeof(p->agent_watch_valid));
	memset(p->agent_watch_event_type, 0, sizeof(p->agent_watch_event_type));
	memset(p->agent_watch_filter, 0, sizeof(p->agent_watch_filter));
	memset(p->agent_events, 0, sizeof(p->agent_events));
	p->agent_event_head = 0;
	p->agent_event_tail = 0;
	p->agent_event_count_queued = 0;
	p->agent_event_count = 0;
	p->agent_event_dropped = 0;
	p->agent_wait_count = 0;
	p->agent_wait_loop_count = 0;
	p->agent_wait_sleep_count = 0;
	p->agent_wait_wakeup_count = 0;
	p->agent_wait_cancel_count = 0;
	p->agent_timeout_count = 0;
	p->agent_wait_deadline_valid = 0;
	p->agent_wait_deadline = 0;
	p->agent_wait_cancel_pending = 0;
	p->agent_wait_cancel_source_pid = 0;
	p->agent_wait_cancel_event_id = 0;
	p->agent_wait_cancel_corr_id = 0;
	p->agent_wait_cancel_tick = 0;
	p->agent_wait_cancel_cause_sequence = 0;
	p->agent_wait_cancel_span_id = 0;
	memset(p->agent_wait_cancel_reason, 0,
	       sizeof(p->agent_wait_cancel_reason));
	p->agent_last_heartbeat_tick = 0;
	p->agent_capability_mask = 0;
	p->agent_detail_count = 0;
	p->agent_detail_head = 0;
	memset(p->agent_details, 0, sizeof(p->agent_details));
	p->agent_prefetch_sequence = 0;
	p->agent_prefetch_count = 0;
	p->agent_prefetch_head = 0;
	memset(p->agent_prefetch_hints, 0, sizeof(p->agent_prefetch_hints));
	p->agent_sched_policy = 0;
	p->agent_sched_weight = 0;
	p->agent_sched_priority = 0;
	p->agent_sched_ready_tick = 0;
	p->agent_sched_last_dispatch_tick = 0;
	p->agent_sched_dispatch_count = 0;
	p->agent_sched_event_dispatch_count = 0;
	p->agent_sched_deadline_dispatch_count = 0;
	p->agent_sched_vruntime = 0;
	p->agent_sched_preemptions = 0;
	p->agent_sched_budget = 0;
	p->agent_sched_budget_used = 0;
	p->agent_sched_last_score = 0;
	p->agent_sched_last_reason = 0;
	p->agent_sched_trace_count = 0;
	p->agent_sched_trace_head = 0;
	memset(p->agent_sched_records, 0, sizeof(p->agent_sched_records));
	p->agent_current_span_id = 0;
	p->agent_current_cause_sequence = 0;
	p->agent_context_chain_hash = 0;
	p->agent_provenance_edges = 0;
	p->agent_observe_epoch = 0;
	p->agent_timeline_wait_count = 0;
	p->agent_timeline_wait_sleep_count = 0;
	p->agent_timeline_wait_wakeup_count = 0;
	p->agent_timeline_wait_timeout_count = 0;
	p->agent_timeline_wait_deadline_valid = 0;
	p->agent_timeline_wait_deadline = 0;
	memset(&p->agent_timeline_wait_filter, 0,
	       sizeof(p->agent_timeline_wait_filter));
}

static int agent_role_valid(int role)
{
	return role >= AGENT_ROLE_SENTINEL && role <= AGENT_ROLE_ORCHESTRATOR;
}

static uint64 agent_all_capabilities(void)
{
	return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
	       AGENT_CAP_PROCESS_READ | AGENT_CAP_MESSAGE_SEND |
	       AGENT_CAP_WATCH | AGENT_CAP_ACTION_WRITE |
	       AGENT_CAP_ARTIFACT_WRITE | AGENT_CAP_AUDIT_WRITE |
	       AGENT_CAP_META_WRITE | AGENT_CAP_ORCHESTRATE |
	       AGENT_CAP_LLM_RELAY;
}

static uint64 agent_role_capability_mask(int role)
{
	switch (role) {
	case AGENT_ROLE_SENTINEL:
		return AGENT_CAP_META_READ | AGENT_CAP_PROCESS_READ |
		       AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		       AGENT_CAP_AUDIT_WRITE;
	case AGENT_ROLE_INVESTIGATOR:
		return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		       AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		       AGENT_CAP_AUDIT_WRITE;
	case AGENT_ROLE_RECOVERY:
		return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
		       AGENT_CAP_MESSAGE_SEND | AGENT_CAP_WATCH |
		       AGENT_CAP_ACTION_WRITE | AGENT_CAP_ARTIFACT_WRITE |
		       AGENT_CAP_AUDIT_WRITE;
	case AGENT_ROLE_ORCHESTRATOR:
		return agent_all_capabilities();
	default:
		return 0;
	}
}

static int agent_role_sched_weight(int role)
{
	switch (role) {
	case AGENT_ROLE_RECOVERY:
		return 120;
	case AGENT_ROLE_ORCHESTRATOR:
		return 110;
	case AGENT_ROLE_INVESTIGATOR:
		return 90;
	case AGENT_ROLE_SENTINEL:
		return 70;
	default:
		return 50;
	}
}

static int agent_has_cap(struct proc *p, uint64 cap)
{
	return p->is_agent && (p->agent_capability_mask & cap) == cap;
}

static int agent_has_any_cap(struct proc *p, uint64 caps)
{
	return p->is_agent && (p->agent_capability_mask & caps) != 0;
}

static void agent_audit_emit(int kind, uint64 tick, struct proc *actor,
			     int source_pid, int target_pid, int event_type,
			     int tool_id, int status, uint64 cause_sequence,
			     uint64 span_id, uint64 value0, uint64 value1,
			     uint64 value2, uint64 flags, char *text)
{
	struct agent_audit_record *record;
	struct agent_timeline_record timeline;
	uint64 slot;

	slot = agent_audit_head % AGENT_AUDIT_MAX_RECORDS;
	record = &agent_audit_records[slot];
	memset(record, 0, sizeof(*record));
	record->sequence = agent_audit_next_sequence++;
	record->tick = tick;
	record->kind = kind;
	record->source_pid = source_pid;
	record->target_pid = target_pid;
	record->event_type = event_type;
	record->tool_id = tool_id;
	record->status = status;
	record->cause_sequence = cause_sequence;
	record->span_id = span_id;
	record->value0 = value0;
	record->value1 = value1;
	record->value2 = value2;
	record->flags = flags;
	if (actor) {
		record->pid = actor->pid;
		record->agent_id = actor->agent_id;
		record->role = actor->agent_role;
		record->loop_state = actor->loop_state;
	}
	safestrcpy(record->text, text ? text : "", sizeof(record->text));
	record->prev_hash = agent_audit_ledger_hash;
	record->record_hash = agent_audit_record_hash(record);
	agent_audit_ledger_hash = record->record_hash;
	if (kind >= 0 && kind <= AGENT_AUDIT_KIND_PREFETCH)
		agent_audit_kind_counts[kind]++;
	agent_audit_head = (agent_audit_head + 1) % AGENT_AUDIT_MAX_RECORDS;
	agent_audit_count++;
	agent_timeline_from_audit(record, &timeline);
	agent_observe_record(&timeline);
}

static void agent_audit_context(struct proc *p,
				struct agent_context_record *record)
{
	if (p == 0 || record == 0)
		return;
	agent_audit_emit(AGENT_AUDIT_KIND_CONTEXT, record->tick, p, p->pid,
			 p->pid, 0, record->tool_id, record->status,
			 record->cause_sequence, record->span_id,
			 record->value0, record->value1, record->value2,
			 record->flags,
			 record->result[0] ? record->result : record->payload);
}

static void agent_audit_event(int kind, struct proc *actor,
			      struct agent_event *event)
{
	if (event == 0)
		return;
	agent_audit_emit(kind, event->tick, actor, event->source_pid,
			 event->target_pid, event->type, 0, event->status,
			 event->cause_sequence, event->span_id,
			 event->event_id, event->corr_id, event->target_pid,
			 0, event->payload);
}

static void agent_audit_sched(struct proc *p, struct agent_sched_record *record)
{
	if (p == 0 || record == 0)
		return;
	agent_audit_emit(AGENT_AUDIT_KIND_SCHED, record->tick, p, p->pid,
			 p->pid, 0, 0, AGENT_STATUS_OK, 0, 0,
			 record->dispatch_count, record->score,
			 record->event_queue_count, record->reason_flags,
			 "sched");
}

static void agent_audit_prefetch_handoff(struct proc *source,
					 struct proc *target,
					 struct agent_file_prefetch_hint *hint,
					 struct agent_file_meta *target_meta,
					 uint64 reason)
{
	if (source == 0 || target == 0 || hint == 0 || target_meta == 0)
		return;
	agent_audit_emit(AGENT_AUDIT_KIND_PREFETCH, agent_ticks(), target,
			 source->pid, target->pid, AGENT_EVENT_MESSAGE,
			 AGENT_TOOL_QUERY_FILE, AGENT_STATUS_OK,
			 hint->source_sequence, source->agent_current_span_id,
			 hint->source_sequence, hint->source_fid, hint->fid,
			 reason, target_meta->stage);
}

static uint64 agent_cap_for_action(char *action)
{
	if (strncmp(action, "action_commit", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "state_update", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "rerun_stage", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_ACTION_WRITE;
	if (strncmp(action, "artifact_update", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "record_result", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "write_report", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_ARTIFACT_WRITE;
	if (strncmp(action, "llm_response", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "llm_relay", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_LLM_RELAY;
	if (strncmp(action, "llm_request", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_MESSAGE_SEND;
	if (strncmp(action, "query", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "query_file", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "read_meta", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_META_READ;
	if (strncmp(action, "read_file_summary", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "read_file_digest", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "read_content", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_CONTENT_READ;
	if (strncmp(action, "send_message", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_MESSAGE_SEND;
	if (strncmp(action, "watch", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_WATCH;
	if (strncmp(action, "meta_write", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "file_meta_write", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "dependency_update", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_META_WRITE;
	if (strncmp(action, "orchestrate", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_ORCHESTRATE;
	return 0;
}

static int agent_action_allowed(struct proc *p, char *action)
{
	uint64 cap = agent_cap_for_action(action);

	return cap != 0 && agent_has_cap(p, cap);
}

static int agent_plain_can_create_orchestrator(struct proc *p)
{
	if (p->pid == 1)
		return 1;
	return p->parent && p->parent->pid == 1 && !p->parent->is_agent;
}

void agent_sched_on_enqueue(struct thread *t)
{
	struct proc *p;

	if (t == 0 || t->process == 0)
		return;
	p = t->process;
	if (!p->is_agent)
		return;
	p->agent_sched_ready_tick = agent_ticks();
	if (p->agent_sched_policy == 0)
		p->agent_sched_policy = AGENT_SCHED_POLICY_ADAPTIVE;
	if (p->agent_sched_weight <= 0)
		p->agent_sched_weight = agent_role_sched_weight(p->agent_role);
	if (p->agent_sched_budget == 0)
		p->agent_sched_budget = AGENT_SCHED_DEFAULT_BUDGET;
}

void agent_sched_on_yield(struct thread *t)
{
	struct proc *p;

	if (t == 0 || t->process == 0)
		return;
	p = t->process;
	if (!p->is_agent)
		return;
	p->agent_sched_preemptions++;
}

static int agent_sched_should_trace(struct proc *p,
				    struct agent_sched_record *record)
{
	uint64 important;

	if (p == 0 || record == 0)
		return 0;
	if (record->dispatch_count <= 4)
		return 1;
	important = AGENT_SCHED_REASON_EVENT_QUEUE |
		    AGENT_SCHED_REASON_DEADLINE_NEAR |
		    AGENT_SCHED_REASON_DEADLINE_NOW |
		    AGENT_SCHED_REASON_HEARTBEAT_DUE |
		    AGENT_SCHED_REASON_PRIORITY;
	if ((record->reason_flags & important) != 0)
		return 1;
	if ((record->dispatch_count & 0xf) == 0)
		return 1;
	return 0;
}

void agent_sched_on_dispatch(struct thread *t)
{
	struct proc *p;
	uint64 now;
	uint64 weight;
	uint64 cost;
	struct agent_sched_record record;
	struct agent_timeline_record timeline;
	uint64 trace_slot;
	long score;
	int trace;

	if (t == 0 || t->process == 0)
		return;
	p = t->process;
	if (!p->is_agent)
		return;
	now = agent_ticks();
	score = agent_sched_score_at(t, now, &record);
	weight = p->agent_sched_weight > 0 ? p->agent_sched_weight : 50;
	cost = 1000 / weight;
	if (cost == 0)
		cost = 1;
	p->agent_sched_dispatch_count++;
	if (p->agent_event_count_queued > 0)
		p->agent_sched_event_dispatch_count++;
	if (p->agent_wait_deadline_valid && p->agent_wait_deadline <= now + 2)
		p->agent_sched_deadline_dispatch_count++;
	p->agent_sched_last_dispatch_tick = now;
	p->agent_sched_vruntime += cost;
	p->agent_sched_budget_used++;
	if (p->agent_sched_budget &&
	    p->agent_sched_budget_used >= p->agent_sched_budget)
		p->agent_sched_budget_used = 0;
	record.dispatch_count = p->agent_sched_dispatch_count;
	p->agent_sched_last_score = score > 0 ? (uint64)score : 0;
	p->agent_sched_last_reason = record.reason_flags;
	record.score = p->agent_sched_last_score;
	trace = agent_sched_should_trace(p, &record);
	if (trace) {
		trace_slot = p->agent_sched_trace_head % AGENT_SCHED_TRACE_CAP;
		memmove(&p->agent_sched_records[trace_slot], &record,
			sizeof(record));
		p->agent_sched_trace_head =
			(p->agent_sched_trace_head + 1) % AGENT_SCHED_TRACE_CAP;
		p->agent_sched_trace_count++;
		agent_timeline_from_sched(&record, &timeline);
		agent_observe_record(&timeline);
		agent_audit_sched(p, &record);
	}
}

static long agent_sched_score_at(struct thread *t, uint64 now,
				 struct agent_sched_record *record)
{
	struct proc *p;
	uint64 age;
	uint64 heartbeat_due = 0;
	long score;
	long penalty;
	uint64 reason = 0;

	if (t == 0 || t->state != RUNNABLE || t->process == 0)
		return -1000000;
	p = t->process;
	if (!p->is_agent) {
		return 200 + (long)((now > p->agent_sched_ready_tick) ?
					    MIN(now - p->agent_sched_ready_tick,
						(uint64)80) :
					    0);
	}
	age = now > p->agent_sched_ready_tick ?
		      now - p->agent_sched_ready_tick :
		      0;
	if (age > 200)
		age = 200;
	score = 300 + p->agent_sched_weight * 4 + (long)age;
	reason |= AGENT_SCHED_REASON_ROLE_WEIGHT;
	if (p->agent_sched_priority != 0) {
		score += p->agent_sched_priority * 8;
		reason |= AGENT_SCHED_REASON_PRIORITY;
	}
	if (age > 0)
		reason |= AGENT_SCHED_REASON_READY_AGE;
	if (p->agent_event_count_queued > 0) {
		score += 900 + p->agent_event_count_queued * 20;
		reason |= AGENT_SCHED_REASON_EVENT_QUEUE;
	}
	if (p->loop_state == AGENT_LOOP_WAITING) {
		score += 180;
		reason |= AGENT_SCHED_REASON_WAITING;
	}
	if (p->agent_wait_deadline_valid) {
		if (p->agent_wait_deadline <= now + 2) {
			score += 700;
			reason |= AGENT_SCHED_REASON_DEADLINE_NOW;
		} else if (p->agent_wait_deadline <= now + 8) {
			score += 250;
			reason |= AGENT_SCHED_REASON_DEADLINE_NEAR;
		}
	}
	if (p->heartbeat_interval > 0 &&
	    now - p->agent_last_heartbeat_tick >=
		    (uint64)p->heartbeat_interval) {
		score += 260;
		heartbeat_due = 1;
		reason |= AGENT_SCHED_REASON_HEARTBEAT_DUE;
	}
	if (p->agent_sched_budget &&
	    p->agent_sched_budget_used >= p->agent_sched_budget) {
		score -= 160;
		reason |= AGENT_SCHED_REASON_BUDGET_USED;
	}
	penalty = p->agent_sched_vruntime > 4000 ?
			  500 :
			  (long)(p->agent_sched_vruntime / 8);
	if (penalty > 0)
		reason |= AGENT_SCHED_REASON_VRUNTIME;
	score -= penalty;
	if (record) {
		memset(record, 0, sizeof(*record));
		record->tick = now;
		record->score = score > 0 ? (uint64)score : 0;
		record->reason_flags = reason;
		record->event_queue_count = p->agent_event_count_queued;
		record->ready_age = age;
		if (p->agent_wait_deadline_valid)
			record->deadline_delta =
				p->agent_wait_deadline > now ?
					p->agent_wait_deadline - now :
					0;
		record->heartbeat_due = heartbeat_due;
		record->vruntime = p->agent_sched_vruntime;
		record->budget_used = p->agent_sched_budget_used;
		record->pid = p->pid;
		record->tid = t->tid;
		record->role = p->agent_role;
		record->loop_state = p->loop_state;
		record->weight = p->agent_sched_weight;
		record->priority = p->agent_sched_priority;
	}
	return score;
}

static long agent_sched_score(struct thread *t)
{
	return agent_sched_score_at(t, agent_ticks(), 0);
}

int agent_sched_better(struct thread *a, struct thread *b)
{
	struct proc *pa;
	struct proc *pb;
	long sa = agent_sched_score(a);
	long sb = agent_sched_score(b);

	if (sa != sb)
		return sa > sb;
	if (a == 0)
		return 0;
	if (b == 0)
		return 1;
	pa = a->process;
	pb = b->process;
	if (pa && pb && pa->is_agent && pb->is_agent &&
	    pa->agent_sched_vruntime != pb->agent_sched_vruntime)
		return pa->agent_sched_vruntime < pb->agent_sched_vruntime;
	if (pa && pb && pa->agent_sched_ready_tick != pb->agent_sched_ready_tick)
		return pa->agent_sched_ready_tick < pb->agent_sched_ready_tick;
	return task_to_id(a) < task_to_id(b);
}

void agent_free_proc_context(struct proc *p)
{
	if (p->agent_ctx_base) {
		uvmunmap(p->pagetable, p->agent_ctx_base, AGENT_CONTEXT_PAGES,
			 1);
	}
	agent_free_shadow(p->agent_shadow_kva);
	agent_clear_metadata(p);
}

int agent_alias_exec_context(struct proc *p, pagetable_t pagetable)
{
	int mapped = 0;

	if (p == 0 || pagetable == 0)
		return -1;
	if (!p->is_agent)
		return 0;
	if (p->agent_ctx_base == 0 ||
	    p->agent_ctx_base > MAXVA - AGENT_CONTEXT_SIZE)
		return -1;
	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++) {
		if (p->agent_ctx_kva[i] == 0 ||
		    mappages(pagetable, p->agent_ctx_base + i * PAGE_SIZE,
			     PAGE_SIZE, p->agent_ctx_kva[i],
			     PTE_U | PTE_R | PTE_W) < 0)
			goto fail;
		mapped++;
	}
	return 0;

fail:
	if (mapped != 0)
		uvmunmap(pagetable, p->agent_ctx_base, mapped, 0);
	return -1;
}

void agent_unmap_exec_context(struct proc *p, pagetable_t pagetable)
{
	if (p != 0 && pagetable != 0 && p->is_agent && p->agent_ctx_base != 0)
		uvmunmap(pagetable, p->agent_ctx_base, AGENT_CONTEXT_PAGES, 0);
}

static char *agent_context_array_ptr(uint64 *kva, uint64 offset, uint64 len)
{
	uint64 page;
	uint64 page_offset;

	if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
		return 0;
	page = offset / PAGE_SIZE;
	page_offset = offset % PAGE_SIZE;
	if (page >= AGENT_CONTEXT_PAGES || page_offset + len > PAGE_SIZE)
		return 0;
	if (kva[page] == 0)
		return 0;
	return (char *)(kva[page] + page_offset);
}

static int agent_context_array_read(uint64 *kva, uint64 offset, char *dst,
				    uint64 len)
{
	uint64 page;
	uint64 page_offset;
	uint64 n;

	if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
		return -1;
	while (len > 0) {
		page = offset / PAGE_SIZE;
		page_offset = offset % PAGE_SIZE;
		if (page >= AGENT_CONTEXT_PAGES || kva[page] == 0)
			return -1;
		n = PAGE_SIZE - page_offset;
		if (n > len)
			n = len;
		memmove(dst, (char *)(kva[page] + page_offset), n);
		dst += n;
		offset += n;
		len -= n;
	}
	return 0;
}

static int agent_context_array_write(uint64 *kva, uint64 offset, char *src,
				     uint64 len)
{
	uint64 page;
	uint64 page_offset;
	uint64 n;

	if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
		return -1;
	while (len > 0) {
		page = offset / PAGE_SIZE;
		page_offset = offset % PAGE_SIZE;
		if (page >= AGENT_CONTEXT_PAGES || kva[page] == 0)
			return -1;
		n = PAGE_SIZE - page_offset;
		if (n > len)
			n = len;
		memmove((char *)(kva[page] + page_offset), src, n);
		src += n;
		offset += n;
		len -= n;
	}
	return 0;
}

static int agent_sync_context_range(struct proc *p, uint64 offset, uint64 len)
{
	char buf[128];
	uint64 n;

	if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
		return -1;
	while (len > 0) {
		n = len > sizeof(buf) ? sizeof(buf) : len;
		if (agent_context_array_read(p->agent_shadow_kva, offset, buf,
					     n) < 0)
			return -1;
		if (agent_context_array_write(p->agent_ctx_kva, offset, buf,
					      n) < 0)
			return -1;
		offset += n;
		len -= n;
	}
	return 0;
}

static int agent_sync_context_all(struct proc *p)
{
	return agent_sync_context_range(p, 0,
					agent_context_user_cache_offset());
}

static struct agent_context_header *agent_header_ptr(struct proc *p)
{
	return (struct agent_context_header *)agent_context_array_ptr(
		p->agent_shadow_kva, AGENT_CONTEXT_HEADER_OFFSET,
		sizeof(struct agent_context_header));
}

static struct agent_result *agent_latest_ptr(struct proc *p)
{
	return (struct agent_result *)agent_context_array_ptr(
		p->agent_shadow_kva, AGENT_CONTEXT_LATEST_RESPONSE_OFFSET,
		sizeof(struct agent_result));
}

static uint64 agent_record_offset(struct proc *p, uint64 slot)
{
	return p->records_offset + slot * sizeof(struct agent_context_record);
}

static int agent_read_record(struct proc *p, uint64 slot,
			     struct agent_context_record *record)
{
	if (slot >= p->context_path_capacity)
		return -1;
	return agent_context_array_read(p->agent_shadow_kva,
					agent_record_offset(p, slot),
					(char *)record, sizeof(*record));
}

static int agent_write_record(struct proc *p, uint64 slot,
			      struct agent_context_record *record)
{
	uint64 offset;

	if (slot >= p->context_path_capacity)
		return -1;
	offset = agent_record_offset(p, slot);
	if (agent_context_array_write(p->agent_shadow_kva, offset,
				      (char *)record, sizeof(*record)) < 0)
		return -1;
	return agent_sync_context_range(p, offset, sizeof(*record));
}

static void agent_fill_header(struct proc *p,
			      struct agent_context_header *header)
{
	memset(header, 0, sizeof(*header));
	header->magic = AGENT_CONTEXT_MAGIC;
	header->version = AGENT_CONTEXT_VERSION;
	header->capacity = p->context_path_capacity;
	header->count = p->context_path_count;
	header->head = p->context_path_head;
	header->total_calls = p->agent_call_count;
	header->oldest_sequence = p->context_path_oldest;
	header->latest_sequence = p->context_path_latest;
	header->dropped_records = p->context_path_dropped;
	header->rollback_count = p->context_path_rollback_count;
	header->latest_response_offset = p->latest_response_offset;
	header->records_offset = p->records_offset;
	header->user_cache_offset = agent_context_user_cache_offset();
	header->user_cache_size = agent_context_user_cache_size();
	header->current_span_id = p->agent_current_span_id;
	header->current_cause_sequence = p->agent_current_cause_sequence;
	header->latest_record_hash = p->agent_context_chain_hash;
	header->provenance_edges = p->agent_provenance_edges;
}

static int agent_write_header(struct proc *p)
{
	struct agent_context_header *header = agent_header_ptr(p);

	if (header == 0)
		return -1;
	agent_fill_header(p, header);
	return agent_sync_context_range(p, AGENT_CONTEXT_HEADER_OFFSET,
					sizeof(*header));
}

static int agent_write_latest(struct proc *p, struct agent_result *latest)
{
	struct agent_result *dst = agent_latest_ptr(p);

	if (dst == 0)
		return -1;
	if (latest)
		memmove(dst, latest, sizeof(*dst));
	else {
		memset(dst, 0, sizeof(*dst));
		dst->version = AGENT_CALL_VERSION;
	}
	return agent_sync_context_range(p, AGENT_CONTEXT_LATEST_RESPONSE_OFFSET,
					sizeof(*dst));
}

static int agent_init_context(struct proc *p)
{
	if (!agent_context_layout_ok())
		return -1;
	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++) {
		if (p->agent_shadow_kva[i] == 0 || p->agent_ctx_kva[i] == 0)
			return -1;
		memset((void *)p->agent_shadow_kva[i], 0, PAGE_SIZE);
		memset((void *)p->agent_ctx_kva[i], 0, PAGE_SIZE);
	}
	p->agent_call_count = 0;
	p->context_path_count = 0;
	p->context_path_capacity = AGENT_CONTEXT_MAX_RECORDS;
	p->context_path_head = 0;
	p->context_path_oldest = 0;
	p->context_path_latest = 0;
	p->context_path_dropped = 0;
	p->context_path_rollback_count = 0;
	p->latest_response_offset = AGENT_CONTEXT_LATEST_RESPONSE_OFFSET;
	p->records_offset = AGENT_CONTEXT_RECORDS_OFFSET;
	p->agent_current_span_id = ((uint64)p->agent_id << 32) | 1;
	p->agent_current_cause_sequence = 0;
	p->agent_context_chain_hash = 0;
	p->agent_provenance_edges = 0;
	if (agent_write_header(p) < 0)
		return -1;
	return agent_write_latest(p, 0);
}

int agent_map_context(struct proc *p)
{
	char *mem;
	char *shadow;
	uint64 va;
	int mapped = 0;

	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++) {
		p->agent_ctx_kva[i] = 0;
		p->agent_shadow_kva[i] = 0;
	}
	for (va = AGENT_CONTEXT_BASE;
	     va < AGENT_CONTEXT_BASE + AGENT_CONTEXT_SIZE;
	     va += PAGE_SIZE) {
		mem = kalloc();
		if (mem == 0)
			goto bad;
		shadow = kalloc();
		if (shadow == 0) {
			kfree(mem);
			goto bad;
		}
		memset(mem, 0, PAGE_SIZE);
		memset(shadow, 0, PAGE_SIZE);
		if (mappages(p->pagetable, va, PAGE_SIZE, (uint64)mem,
			     PTE_R | PTE_W | PTE_U) != 0) {
			kfree(mem);
			kfree(shadow);
			goto bad;
		}
		p->agent_ctx_kva[mapped] = (uint64)mem;
		p->agent_shadow_kva[mapped] = (uint64)shadow;
		mapped++;
	}
	return 0;

bad:
	if (mapped > 0)
		uvmunmap(p->pagetable, AGENT_CONTEXT_BASE, mapped, 1);
	agent_free_shadow(p->agent_shadow_kva);
	return -1;
}

int agent_make_role(struct proc *p, int role)
{
	if (!agent_role_valid(role))
		return -1;
	if (p->max_page * PAGE_SIZE > AGENT_CONTEXT_BASE)
		return -1;
	if (agent_map_context(p) < 0)
		return -1;
	p->is_agent = 1;
	p->agent_type = AGENT_TYPE_AGENT;
	p->agent_id = agent_alloc_id();
	p->agent_role = role;
	p->agent_ctx_base = AGENT_CONTEXT_BASE;
	p->agent_ctx_size = AGENT_CONTEXT_SIZE;
	p->heartbeat_interval = 0;
	p->resource_quota = AGENT_CONTEXT_MAX_RECORDS;
	p->loop_state = AGENT_LOOP_IDLE;
	p->agent_capability_mask = agent_role_capability_mask(role);
	p->agent_last_heartbeat_tick = agent_ticks();
	p->agent_sched_policy = AGENT_SCHED_POLICY_ADAPTIVE;
	p->agent_sched_weight = agent_role_sched_weight(role);
	p->agent_sched_priority = 0;
	p->agent_sched_ready_tick = agent_ticks();
	p->agent_sched_last_dispatch_tick = 0;
	p->agent_sched_dispatch_count = 0;
	p->agent_sched_event_dispatch_count = 0;
	p->agent_sched_deadline_dispatch_count = 0;
	p->agent_sched_vruntime = 0;
	p->agent_sched_preemptions = 0;
	p->agent_sched_budget = AGENT_SCHED_DEFAULT_BUDGET;
	p->agent_sched_budget_used = 0;
	p->agent_sched_last_score = 0;
	p->agent_sched_last_reason = 0;
	p->agent_sched_trace_count = 0;
	p->agent_sched_trace_head = 0;
	memset(p->agent_sched_records, 0, sizeof(p->agent_sched_records));
	p->agent_observe_epoch = agent_observe_epoch;
	p->agent_timeline_wait_count = 0;
	p->agent_timeline_wait_sleep_count = 0;
	p->agent_timeline_wait_wakeup_count = 0;
	p->agent_timeline_wait_timeout_count = 0;
	agent_timeline_waiting_set(p, 0);
	p->agent_timeline_wait_deadline_valid = 0;
	p->agent_timeline_wait_deadline = 0;
	memset(&p->agent_timeline_wait_filter, 0,
	       sizeof(p->agent_timeline_wait_filter));
	if (agent_init_context(p) < 0) {
		agent_free_proc_context(p);
		return -1;
	}
	return 0;
}

int agent_make(struct proc *p)
{
	return agent_make_role(p, AGENT_ROLE_SENTINEL);
}

static void agent_info_fill(struct proc *p, struct agent_info *info)
{
	memset(info, 0, sizeof(*info));
	info->is_agent = p->is_agent;
	info->agent_id = p->agent_id;
	info->agent_role = p->agent_role;
	info->context_base = p->agent_ctx_base;
	info->context_size = p->agent_ctx_size;
	info->agent_type = p->agent_type;
	info->heartbeat_interval = p->heartbeat_interval;
	info->resource_quota = p->resource_quota;
	info->loop_state = p->loop_state;
	info->agent_call_count = p->agent_call_count;
	info->context_path_count = p->context_path_count;
	info->context_path_capacity = p->context_path_capacity;
	info->context_path_head = p->context_path_head;
	info->context_path_oldest = p->context_path_oldest;
	info->context_path_latest = p->context_path_latest;
	info->context_path_dropped = p->context_path_dropped;
	info->context_path_rollback_count = p->context_path_rollback_count;
	info->latest_response_offset = p->latest_response_offset;
	info->records_offset = p->records_offset;
	info->event_count = p->agent_event_count;
	info->event_dropped = p->agent_event_dropped;
	info->event_queue_count = p->agent_event_count_queued;
	info->watch_count = p->agent_watch_count;
	info->wait_count = p->agent_wait_count;
	info->wait_loop_count = p->agent_wait_loop_count;
	info->wait_sleep_count = p->agent_wait_sleep_count;
	info->wait_wakeup_count = p->agent_wait_wakeup_count;
	info->wait_cancel_count = p->agent_wait_cancel_count;
	info->timeout_count = p->agent_timeout_count;
	info->last_heartbeat_tick = p->agent_last_heartbeat_tick;
	info->current_tick = agent_ticks();
	info->capability_mask = p->agent_capability_mask;
	info->file_scan_runs = agent_file_scan_runs;
	info->file_scan_entries = agent_file_scan_entries;
	info->file_scan_added = agent_file_scan_added;
	info->file_scan_updated = agent_file_scan_updated;
	info->file_scan_removed = agent_file_scan_removed;
	info->file_scan_generation = agent_file_generation;
	info->file_scan_pending = agent_file_scan_pending ||
				  agent_file_scan_active;
	info->file_digest_cache_hits = agent_file_digest_cache_hits;
	info->file_digest_cache_misses = agent_file_digest_cache_misses;
	info->sched_policy = p->agent_sched_policy;
	info->sched_weight = p->agent_sched_weight;
	info->sched_priority = p->agent_sched_priority;
	info->sched_budget = p->agent_sched_budget;
	info->sched_dispatch_count = p->agent_sched_dispatch_count;
	info->sched_event_dispatch_count = p->agent_sched_event_dispatch_count;
	info->sched_deadline_dispatch_count =
		p->agent_sched_deadline_dispatch_count;
	info->sched_vruntime = p->agent_sched_vruntime;
	info->sched_ready_tick = p->agent_sched_ready_tick;
	info->sched_last_dispatch_tick = p->agent_sched_last_dispatch_tick;
	info->sched_preemptions = p->agent_sched_preemptions;
	info->sched_budget_used = p->agent_sched_budget_used;
	info->sched_last_score = p->agent_sched_last_score;
	info->sched_last_reason = p->agent_sched_last_reason;
	info->sched_trace_count = p->agent_sched_trace_count;
	info->current_span_id = p->agent_current_span_id;
	info->current_cause_sequence = p->agent_current_cause_sequence;
	info->provenance_edges = p->agent_provenance_edges;
	info->observe_epoch = agent_observe_epoch;
	info->timeline_wait_count = p->agent_timeline_wait_count;
	info->timeline_wait_sleep_count = p->agent_timeline_wait_sleep_count;
	info->timeline_wait_wakeup_count = p->agent_timeline_wait_wakeup_count;
	info->timeline_wait_timeout_count = p->agent_timeline_wait_timeout_count;
}

static struct agent_tool_desc *agent_tool_by_id(int tool_id)
{
	if (tool_id <= 0 || tool_id > AGENT_TOOL_COUNT)
		return 0;
	if (agent_tools[tool_id - 1].tool_id != tool_id)
		return 0;
	return &agent_tools[tool_id - 1];
}

static struct agent_tool_desc *agent_tool_by_name(char *name)
{
	for (int i = 0; i < AGENT_TOOL_COUNT; i++)
		if (strncmp(agent_tools[i].name, name,
			    AGENT_TOOL_NAME_SIZE) == 0)
			return &agent_tools[i];
	return 0;
}

static void agent_result_text(struct agent_result *res, char *text)
{
	safestrcpy(res->result, text, sizeof(res->result));
}

static void agent_result_init(struct agent_result *res, struct agent_op *op)
{
	memset(res, 0, sizeof(*res));
	res->version = AGENT_CALL_VERSION;
	res->status = AGENT_STATUS_OK;
	res->tool_id = op->tool_id;
	res->request_id = op->request_id;
}

static int agent_append_context_flags(struct proc *p, struct agent_op *op,
				      struct agent_result *latest, uint64 tick,
				      uint64 flags)
{
	uint64 slot;
	struct agent_context_record record;
	struct agent_timeline_record timeline;

	if (p->agent_ctx_base == 0 || p->context_path_capacity == 0 ||
	    !agent_context_layout_ok())
		return -1;
	slot = p->context_path_head % p->context_path_capacity;
	if (p->context_path_count < p->context_path_capacity) {
		if (p->context_path_count == 0)
			p->context_path_oldest = latest->sequence;
		p->context_path_count++;
	} else {
		p->context_path_dropped++;
		p->context_path_oldest =
			latest->sequence - p->context_path_capacity + 1;
	}
	p->context_path_latest = latest->sequence;
	p->context_path_head = (slot + 1) % p->context_path_capacity;

	memset(&record, 0, sizeof(record));
	record.sequence = latest->sequence;
	record.request_id = latest->request_id;
	record.cause_sequence = p->agent_current_cause_sequence;
	record.span_id = p->agent_current_span_id ?
				 p->agent_current_span_id :
				 (((uint64)p->agent_id << 32) | 1);
	record.arg0 = op->arg0;
	record.value0 = latest->value0;
	record.value1 = latest->value1;
	record.value2 = latest->value2;
	record.tick = tick;
	record.flags = flags;
	if (strlen(op->payload) >= sizeof(record.payload) ||
	    strlen(latest->result) >= sizeof(record.result))
		record.flags |= AGENT_CONTEXT_RECORD_F_TRUNCATED;
	record.tool_id = latest->tool_id;
	record.status = latest->status;
	safestrcpy(record.payload, op->payload, sizeof(record.payload));
	safestrcpy(record.result, latest->result, sizeof(record.result));
	record.prev_hash = p->agent_context_chain_hash;
	record.record_hash = agent_context_record_hash(&record);
	memset(&p->agent_details[slot], 0, sizeof(p->agent_details[slot]));
	p->agent_details[slot].sequence = latest->sequence;
	p->agent_details[slot].flags = record.flags;
	memmove(&p->agent_details[slot].op, op, sizeof(*op));
	memmove(&p->agent_details[slot].result, latest, sizeof(*latest));
	p->agent_detail_head = p->context_path_head;
	p->agent_detail_count = p->context_path_count;
	if (agent_write_record(p, slot, &record) < 0)
		return -1;
	p->agent_context_chain_hash = record.record_hash;
	if (record.cause_sequence)
		p->agent_provenance_edges++;
	p->agent_current_cause_sequence = latest->sequence;
	p->agent_current_span_id = record.span_id;
	if (agent_write_latest(p, latest) < 0)
		return -1;
	agent_timeline_from_context(p, &record, &timeline);
	agent_observe_record(&timeline);
	agent_audit_context(p, &record);
	return 0;
}

static int agent_append_context(struct proc *p, struct agent_op *op,
				struct agent_result *latest, uint64 tick)
{
	return agent_append_context_flags(p, op, latest, tick,
					  AGENT_CONTEXT_RECORD_F_SYSTEM);
}

static void agent_collect_proc_snapshot(int filter_agents,
					struct agent_proc_snapshot *snapshot)
{
	struct proc *pp;

	memset(snapshot, 0, sizeof(*snapshot));
	for (pp = pool; pp < &pool[NPROC]; pp++) {
		if (pp->state != P_UNUSED) {
			if (!filter_agents || pp->is_agent) {
				snapshot->used++;
				if (pp->is_agent)
					snapshot->agents++;
				for (int i = 0; i < NTHREAD; i++) {
					if (pp->threads[i].state == RUNNABLE ||
					    pp->threads[i].state == RUNNING) {
						snapshot->runnable++;
						break;
					}
				}
			}
		}
	}
}

static uint64 agent_bucket(char *s)
{
	uint64 h = 1469598103934665603ULL;

	while (*s) {
		h ^= (uchar)*s++;
		h *= 1099511628211ULL;
	}
	return h % AGENT_FILE_INDEX_BUCKETS;
}

static void agent_slot_name(int slot, char *out, int n)
{
	if (n < 6)
		return;
	memset(out, 0, n);
	out[0] = 'a';
	out[1] = 'f';
	out[2] = '0' + (slot / 100) % 10;
	out[3] = '0' + (slot / 10) % 10;
	out[4] = '0' + slot % 10;
}

int agent_file_is_meta_store_name(char *path)
{
	return path && strncmp(path, AGENT_META_STORE_NAME, DIRSIZ) == 0;
}

static void agent_edit_lock(void)
{
	while (__sync_lock_test_and_set(&agent_file_edit_guard, 1) != 0)
		;
	__sync_synchronize();
}

static void agent_edit_unlock(void)
{
	__sync_synchronize();
	__sync_lock_release(&agent_file_edit_guard);
}

static int agent_file_content_version_slot(uint64 dev, uint64 inum,
					   int create)
{
	int free_slot = -1;

	for (int i = 0; i < AGENT_FILE_VERSION_MAX; i++) {
		if (agent_file_content_versions[i].used &&
		    agent_file_content_versions[i].dev == dev &&
		    agent_file_content_versions[i].inum == inum)
			return i;
		if (!agent_file_content_versions[i].used && free_slot < 0)
			free_slot = i;
	}
	if (!create || free_slot < 0)
		return -1;
	memset(&agent_file_content_versions[free_slot], 0,
	       sizeof(agent_file_content_versions[free_slot]));
	agent_file_content_versions[free_slot].used = 1;
	agent_file_content_versions[free_slot].dev = dev;
	agent_file_content_versions[free_slot].inum = inum;
	return free_slot;
}

static int agent_file_content_version(struct inode *ip, uint64 *version,
				      int create)
{
	int slot;

	if (ip == 0 || version == 0)
		return -1;
	slot = agent_file_content_version_slot(ip->dev, ip->inum, create);
	if (slot < 0)
		return -1;
	*version = agent_file_content_versions[slot].version;
	return 0;
}

static void agent_file_content_bump(struct inode *ip)
{
	int slot;

	if (ip == 0)
		return;
	slot = agent_file_content_version_slot(ip->dev, ip->inum, 1);
	if (slot < 0)
		return;
	agent_file_content_generation++;
	if (agent_file_content_generation == 0)
		agent_file_content_generation = 1;
	agent_file_content_versions[slot].version =
		agent_file_content_generation;
}

static int agent_edit_version_slot_locked(uint64 dev, uint64 inum,
					  int create)
{
	int free_slot = -1;

	for (int i = 0; i < AGENT_FILE_VERSION_MAX; i++) {
		if (agent_file_versions[i].used &&
		    agent_file_versions[i].dev == dev &&
		    agent_file_versions[i].inum == inum)
			return i;
		if (!agent_file_versions[i].used && free_slot < 0)
			free_slot = i;
	}
	if (!create || free_slot < 0)
		return -1;
	memset(&agent_file_versions[free_slot], 0,
	       sizeof(agent_file_versions[free_slot]));
	agent_file_versions[free_slot].used = 1;
	agent_file_versions[free_slot].dev = dev;
	agent_file_versions[free_slot].inum = inum;
	return free_slot;
}

static uint64 agent_edit_version_locked(uint64 dev, uint64 inum, int create,
					int *ok)
{
	int slot = agent_edit_version_slot_locked(dev, inum, create);

	if (slot < 0) {
		if (ok)
			*ok = 0;
		return 0;
	}
	if (ok)
		*ok = 1;
	return agent_file_versions[slot].version;
}

static int agent_edit_set_version_locked(uint64 dev, uint64 inum,
					 uint64 version)
{
	int slot = agent_edit_version_slot_locked(dev, inum, 1);

	if (slot < 0)
		return -1;
	agent_file_versions[slot].version = version;
	return 0;
}

static void agent_edit_bump_version_locked(uint64 dev, uint64 inum)
{
	int ok;
	uint64 version = agent_edit_version_locked(dev, inum, 1, &ok);

	if (ok)
		agent_edit_set_version_locked(dev, inum, version + 1);
}

static struct agent_file_edit_entry *
agent_edit_find_locked(uint64 dev, uint64 inum)
{
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_file_edits[i].active &&
		    agent_file_edits[i].dev == dev &&
		    agent_file_edits[i].inum == inum)
			return &agent_file_edits[i];
	return 0;
}

static struct agent_file_edit_entry *agent_edit_find_lease_locked(uint64 lease)
{
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_file_edits[i].active &&
		    agent_file_edits[i].lease_id == lease)
			return &agent_file_edits[i];
	return 0;
}

static struct agent_file_edit_entry *agent_edit_free_locked(void)
{
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (!agent_file_edits[i].active)
			return &agent_file_edits[i];
	return 0;
}

static int agent_edit_expired(struct agent_file_edit_entry *e, uint64 now)
{
	return e->active && e->deadline_tick != 0 && now >= e->deadline_tick;
}

static void agent_edit_release_locked(struct agent_file_edit_entry *e,
				      int publish_dirty)
{
	if (e == 0 || !e->active)
		return;
	if (publish_dirty && e->dirty)
		agent_edit_set_version_locked(e->dev, e->inum,
					      e->base_version + 1);
	memset(e, 0, sizeof(*e));
}

static void agent_edit_cleanup_expired_locked(uint64 now)
{
	for (int i = 0; i < AGENT_FILE_EDIT_MAX; i++)
		if (agent_edit_expired(&agent_file_edits[i], now))
			agent_edit_release_locked(&agent_file_edits[i], 1);
}

static int agent_edit_owner(struct agent_file_edit_entry *e, struct proc *p)
{
	return e && p && p->is_agent && e->owner_pid == p->pid &&
	       e->owner_agent_id == p->agent_id;
}

static int agent_edit_can_manage(struct proc *p)
{
	return agent_has_any_cap(p, AGENT_CAP_CONTENT_READ |
				    AGENT_CAP_ARTIFACT_WRITE |
				    AGENT_CAP_META_WRITE |
				    AGENT_CAP_ORCHESTRATE);
}

static void agent_edit_fill_state_locked(struct agent_file_edit_state *state,
					 struct agent_file_edit_entry *e,
					 uint64 dev, uint64 inum, char *path)
{
	int ok;

	memset(state, 0, sizeof(*state));
	state->dev = dev;
	state->inum = inum;
	state->current_version =
		agent_edit_version_locked(dev, inum, 0, &ok);
	if (path)
		safestrcpy(state->path, path, sizeof(state->path));
	if (e == 0 || !e->active)
		return;
	state->active = 1;
	state->owner_pid = e->owner_pid;
	state->owner_agent_id = e->owner_agent_id;
	state->owner_role = e->owner_role;
	state->dirty = e->dirty;
	state->lease_id = e->lease_id;
	state->base_version = e->base_version;
	state->deadline_tick = e->deadline_tick;
	state->conflict_count = e->conflict_count;
	if (e->path[0])
		safestrcpy(state->path, e->path, sizeof(state->path));
}

static void agent_edit_audit(struct proc *p, int status, char *text,
			     struct agent_file_edit_state *state)
{
	if (p == 0 || !p->is_agent || state == 0)
		return;
	agent_audit_emit(AGENT_AUDIT_KIND_CONTEXT, agent_ticks(), p, p->pid,
			 p->pid, 0, AGENT_TOOL_QUERY_FILE, status, 0,
			 p->agent_current_span_id, state->lease_id,
			 state->current_version, state->owner_pid,
			 state->dev, text);
}

static void agent_file_normalize_physical(int slot, struct agent_file_meta *m)
{
	char generated[DIRSIZ];

	if (m->physical_name[0] == 0 ||
	    strlen(m->physical_name) >= DIRSIZ ||
	    agent_file_is_meta_store_name(m->physical_name)) {
		agent_slot_name(slot, generated, sizeof(generated));
		safestrcpy(m->physical_name, generated,
			   sizeof(m->physical_name));
	}
}

static struct inode *agent_fs_lookup_or_create(char *name, int create)
{
	struct inode *ip;

	if ((ip = namei(name)) != 0) {
		ivalid(ip);
		if (ip->type == T_FILE)
			return ip;
		iput(ip);
		return 0;
	}
	if (!create)
		return 0;
	return fs_create(name, T_FILE, 0);
}

static int agent_file_load(void)
{
	struct inode *ip;
	struct agent_meta_store *store = &agent_meta_store_buf;
	int n;
	int used = 0;

	if (agent_file_loaded) {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			if (agent_files[i].used)
				used++;
		return used;
	}
	memset(agent_files, 0, sizeof(agent_files));
	ip = agent_fs_lookup_or_create(AGENT_META_STORE_NAME, 0);
	if (ip) {
		memset(store, 0, sizeof(*store));
		n = readi(ip, 0, (uint64)store, 0, sizeof(*store));
		if (n == (int)sizeof(*store) &&
		    store->magic == AGENT_META_STORE_MAGIC &&
		    store->version == AGENT_INODE_META_VERSION) {
			memmove(agent_files, store->records,
				sizeof(agent_files));
			for (int i = 0; i < AGENT_FILE_META_MAX; i++)
				if (agent_files[i].used) {
					used++;
					agent_file_bind_slot(i);
				}
		}
		iput(ip);
	}
	agent_file_loaded = 1;
	agent_file_rebuild_indexes();
	return used;
}

static int agent_file_persist(void)
{
	struct inode *ip;
	struct agent_meta_store *store = &agent_meta_store_buf;
	int n;

	if (agent_meta_store_busy)
		return 0;
	agent_meta_store_busy = 1;
	ip = agent_fs_lookup_or_create(AGENT_META_STORE_NAME, 1);
	if (ip == 0) {
		agent_meta_store_busy = 0;
		return -1;
	}
	memset(store, 0, sizeof(*store));
	store->magic = AGENT_META_STORE_MAGIC;
	store->version = AGENT_INODE_META_VERSION;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		if (agent_files[i].used)
			store->count++;
	memmove(store->records, agent_files, sizeof(agent_files));
	n = writei(ip, 0, (uint64)store, 0, sizeof(*store));
	iput(ip);
	agent_meta_store_busy = 0;
	return n == (int)sizeof(*store) ? 0 : -1;
}

static int agent_file_bind_slot(int slot)
{
	struct agent_file_meta *m;
	struct inode *ip;

	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return -1;
	m = &agent_files[slot];
	if (!m->used)
		return -1;
	agent_file_normalize_physical(slot, m);
	ip = agent_fs_lookup_or_create(m->physical_name, 1);
	if (ip == 0)
		return -1;
	ip->agent_meta_slot = slot + 1;
	ip->agent_meta_flags = m->flags & AGENT_FILE_META_F_PERSIST;
	ip->agent_meta_version = AGENT_INODE_META_VERSION;
	iupdate(ip);
	m->dev = ip->dev;
	m->inum = ip->inum;
	m->size = ip->size;
	m->fs_generation = ++agent_file_generation;
	iput(ip);
	return 0;
}

static void agent_file_clear_slot(int slot)
{
	struct inode *ip;

	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return;
	if (agent_files[slot].used && agent_files[slot].physical_name[0]) {
		ip = namei(agent_files[slot].physical_name);
		if (ip) {
			ivalid(ip);
			if (ip->agent_meta_slot == slot + 1) {
				ip->agent_meta_slot = 0;
				ip->agent_meta_flags = 0;
				ip->agent_meta_version = 0;
				iupdate(ip);
			}
			iput(ip);
		}
	}
	memset(&agent_files[slot], 0, sizeof(agent_files[slot]));
}

static int agent_file_find_slot_by_physical(char *path)
{
	if (path == 0 || path[0] == 0)
		return -1;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used)
			continue;
		if (strncmp(agent_files[i].physical_name, path,
			    sizeof(agent_files[i].physical_name)) == 0)
			return i;
	}
	return -1;
}

static int agent_file_alloc_slot(void)
{
	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		if (!agent_files[i].used)
			return i;
	return -1;
}

static void agent_file_infer_kind(char *name, char *out, int n)
{
	if (agent_contains(name, "md") || agent_contains(name, "txt"))
		safestrcpy(out, "document", n);
	else if (agent_contains(name, "log") || agent_contains(name, "err"))
		safestrcpy(out, "log", n);
	else if (agent_contains(name, "status") || agent_contains(name, "ok"))
		safestrcpy(out, "status", n);
	else if (agent_contains(name, "data") || agent_contains(name, "csv"))
		safestrcpy(out, "dataset", n);
	else
		safestrcpy(out, "file", n);
}

static void agent_file_infer_status(char *name, char *out, int n)
{
	if (agent_contains(name, "fail") || agent_contains(name, "err"))
		safestrcpy(out, "failed", n);
	else if (agent_contains(name, "ok") || agent_contains(name, "pass"))
		safestrcpy(out, "ok", n);
	else
		safestrcpy(out, "present", n);
}

static int agent_file_scan_bind_inode(struct inode *ip, char *path)
{
	struct agent_file_meta *m;
	int slot;
	int added = 0;
	int changed = 0;

	if (ip == 0 || path == 0 || path[0] == 0 ||
	    agent_file_is_meta_store_name(path))
		return 0;
	ivalid(ip);
	if (ip->type != T_FILE)
		return 0;
	slot = ip->agent_meta_slot - 1;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX ||
	    !agent_files[slot].used)
		slot = agent_file_find_slot_by_physical(path);
	if (slot < 0)
		slot = agent_file_alloc_slot();
	if (slot < 0)
		return 0;
	m = &agent_files[slot];
	if (!m->used) {
		memset(m, 0, sizeof(*m));
		m->used = 1;
		m->fid = slot + 1;
		m->flags = AGENT_FILE_META_F_PERSIST |
			   AGENT_FILE_META_F_AUTOSCAN;
		added = 1;
	}
	if (m->physical_name[0] == 0 ||
	    (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		if (strncmp(m->physical_name, path,
			    sizeof(m->physical_name)) != 0) {
			safestrcpy(m->physical_name, path,
				   sizeof(m->physical_name));
			changed = 1;
		}
	}
	if (m->logical_path[0] == 0 ||
	    (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		if (strncmp(m->logical_path, path, sizeof(m->logical_path)) !=
		    0) {
			safestrcpy(m->logical_path, path,
				   sizeof(m->logical_path));
			changed = 1;
		}
	}
	if (m->project[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(m->project, "root", sizeof(m->project));
		changed = 1;
	}
	if (m->workflow[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(m->workflow, "background-scan",
			   sizeof(m->workflow));
		changed = 1;
	}
	if (m->run_id[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(m->run_id, "ROOT", sizeof(m->run_id));
		changed = 1;
	}
	if (m->stage[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(m->stage, "scan", sizeof(m->stage));
		changed = 1;
	}
	if (m->kind[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		agent_file_infer_kind(path, m->kind, sizeof(m->kind));
		changed = 1;
	}
	if (m->status[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		agent_file_infer_status(path, m->status, sizeof(m->status));
		changed = 1;
	}
	if (m->summary[0] == 0 && (m->flags & AGENT_FILE_META_F_AUTOSCAN)) {
		safestrcpy(m->summary, "auto scanned root file",
			   sizeof(m->summary));
		changed = 1;
	}
	if (m->dev != ip->dev || m->inum != ip->inum || m->size != ip->size) {
		m->dev = ip->dev;
		m->inum = ip->inum;
		m->size = ip->size;
		changed = 1;
	}
	if (ip->agent_meta_slot != slot + 1 ||
	    ip->agent_meta_version != AGENT_INODE_META_VERSION) {
		ip->agent_meta_slot = slot + 1;
		ip->agent_meta_flags = m->flags & AGENT_FILE_META_F_PERSIST;
		ip->agent_meta_version = AGENT_INODE_META_VERSION;
		iupdate(ip);
		changed = 1;
	}
	if (changed || added) {
		m->updated_tick = agent_ticks();
		m->fs_generation = ++agent_file_generation;
		if (added)
			agent_file_scan_added++;
		else
			agent_file_scan_updated++;
	}
	agent_file_scan_seen[slot] = 1;
	return changed || added;
}

void agent_file_request_scan(void)
{
	if (!agent_file_scan_enabled)
		return;
	agent_file_scan_pending = 1;
}

static void agent_file_enable_scan(void)
{
	agent_file_scan_enabled = 1;
	agent_file_scan_pending = 1;
	agent_file_scan_next_tick = agent_ticks();
}

void agent_background_maintain(void)
{
	struct inode *root;
	struct inode *ip;
	struct dirent de;
	char name[DIRSIZ + 1];
	uint64 now;
	uint64 off;
	int steps = 0;
	int changed = 0;

	if (!agent_file_scan_enabled || agent_meta_store_busy)
		return;
	now = agent_ticks();
	if (!agent_file_scan_active) {
		if (!agent_file_scan_pending)
			return;
		agent_file_load();
		agent_file_scan_pending = 0;
		agent_file_scan_active = 1;
		agent_file_scan_offset = 0;
		memset(agent_file_scan_seen, 0, sizeof(agent_file_scan_seen));
		agent_file_scan_runs++;
	} else if (agent_file_scan_last_step_tick == now) {
		return;
	}
	agent_file_scan_last_step_tick = now;
	root = root_dir();
	if (root == 0)
		return;
	for (off = agent_file_scan_offset;
	     off < root->size && steps < AGENT_FS_SCAN_STEP;
	     off += sizeof(de), steps++) {
		if (readi(root, 0, (uint64)&de, off, sizeof(de)) !=
		    sizeof(de))
			break;
		agent_file_scan_entries++;
		if (de.inum == 0)
			continue;
		memset(name, 0, sizeof(name));
		memmove(name, de.name, DIRSIZ);
		name[DIRSIZ] = 0;
		if (name[0] == 0 || agent_file_is_meta_store_name(name))
			continue;
		ip = dirlookup(root, name, 0);
		if (ip == 0)
			continue;
		ivalid(ip);
		changed += agent_file_scan_bind_inode(ip, name);
		iput(ip);
	}
	agent_file_scan_offset = off;
	if (agent_file_scan_offset >= root->size) {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			if (!agent_files[i].used)
				continue;
			if ((agent_files[i].flags & AGENT_FILE_META_F_AUTOSCAN) &&
			    !agent_file_scan_seen[i]) {
				agent_file_clear_slot(i);
				agent_file_scan_removed++;
				changed++;
			}
		}
		if (changed) {
			agent_file_rebuild_indexes();
			agent_file_persist();
		} else {
			agent_file_rebuild_indexes();
		}
		agent_file_scan_active = 0;
		agent_file_scan_next_tick =
			agent_ticks() + AGENT_FS_SCAN_INTERVAL;
	}
	iput(root);
}

void agent_fs_note_create(struct inode *ip, char *path)
{
	int slot = -1;

	if (agent_meta_store_busy || ip == 0 || path == 0 ||
	    agent_file_is_meta_store_name(path))
		return;
	agent_file_content_bump(ip);
	agent_file_load();
	ivalid(ip);
	if (ip->type != T_FILE || ip->agent_meta_slot > 0)
		return;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		if (!agent_files[i].used) {
			slot = i;
			break;
		}
	if (slot < 0)
		return;
	memset(&agent_files[slot], 0, sizeof(agent_files[slot]));
	agent_files[slot].used = 1;
	agent_files[slot].fid = slot + 1;
	agent_files[slot].flags = AGENT_FILE_META_F_PERSIST |
				  AGENT_FILE_META_F_AUTOSCAN;
	safestrcpy(agent_files[slot].physical_name, path,
		   sizeof(agent_files[slot].physical_name));
	safestrcpy(agent_files[slot].logical_path, path,
		   sizeof(agent_files[slot].logical_path));
	safestrcpy(agent_files[slot].kind, "file",
		   sizeof(agent_files[slot].kind));
	safestrcpy(agent_files[slot].status, "created",
		   sizeof(agent_files[slot].status));
	safestrcpy(agent_files[slot].summary, "created by fileopen",
		   sizeof(agent_files[slot].summary));
	agent_files[slot].updated_tick = agent_ticks();
	if (agent_file_scan_active)
		agent_file_scan_seen[slot] = 1;
	agent_file_bind_slot(slot);
	agent_file_rebuild_indexes();
	agent_file_persist();
	agent_file_request_scan();
}

static void agent_fs_update_inode_meta(struct inode *ip, char *summary)
{
	int slot;

	if (agent_meta_store_busy || ip == 0)
		return;
	ivalid(ip);
	slot = ip->agent_meta_slot - 1;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return;
	agent_file_load();
	if (!agent_files[slot].used)
		return;
	agent_files[slot].size = ip->size;
	agent_files[slot].updated_tick = agent_ticks();
	agent_files[slot].fs_generation = ++agent_file_generation;
	if (summary && summary[0])
		safestrcpy(agent_files[slot].summary, summary,
			   sizeof(agent_files[slot].summary));
	agent_file_rebuild_indexes();
	agent_file_persist();
	agent_file_request_scan();
}

void agent_fs_note_write(struct inode *ip)
{
	agent_file_content_bump(ip);
	agent_fs_update_inode_meta(ip, "file content updated");
}

void agent_fs_note_truncate(struct inode *ip)
{
	agent_file_content_bump(ip);
	agent_fs_update_inode_meta(ip, "file truncated");
}

void agent_fs_note_delete(struct inode *ip)
{
	int slot;

	if (agent_meta_store_busy || ip == 0)
		return;
	agent_file_content_bump(ip);
	ivalid(ip);
	slot = ip->agent_meta_slot - 1;
	if (slot < 0 || slot >= AGENT_FILE_META_MAX)
		return;
	agent_file_load();
	agent_file_clear_slot(slot);
	ip->agent_meta_slot = 0;
	ip->agent_meta_flags = 0;
	ip->agent_meta_version = 0;
	iupdate(ip);
	agent_file_rebuild_indexes();
	agent_file_persist();
	agent_file_request_scan();
}

static int agent_edit_modify_allowed(struct inode *ip, char *action)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;
	struct agent_file_edit_state state;
	uint64 now;
	int allowed = 1;

	if (ip == 0)
		return 0;
	agent_edit_lock();
	now = agent_ticks();
	agent_edit_cleanup_expired_locked(now);
	edit = agent_edit_find_locked(ip->dev, ip->inum);
	if (edit && !agent_edit_owner(edit, p)) {
		edit->conflict_count++;
		agent_edit_fill_state_locked(&state, edit, ip->dev, ip->inum,
					     0);
		allowed = 0;
	}
	agent_edit_unlock();
	if (!allowed)
		agent_edit_audit(p, AGENT_STATUS_CONFLICT, action, &state);
	return allowed;
}

int agent_edit_write_allowed(struct inode *ip)
{
	return agent_edit_modify_allowed(ip, "edit_write_conflict");
}

int agent_edit_truncate_allowed(struct inode *ip)
{
	return agent_edit_modify_allowed(ip, "edit_trunc_conflict");
}

int agent_edit_unlink_allowed(struct inode *ip)
{
	return agent_edit_modify_allowed(ip, "edit_unlink_conflict");
}

static void agent_edit_note_modify(struct inode *ip)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;

	if (ip == 0)
		return;
	agent_edit_lock();
	agent_edit_cleanup_expired_locked(agent_ticks());
	edit = agent_edit_find_locked(ip->dev, ip->inum);
	if (edit && agent_edit_owner(edit, p))
		edit->dirty = 1;
	else if (edit == 0)
		agent_edit_bump_version_locked(ip->dev, ip->inum);
	agent_edit_unlock();
}

void agent_edit_note_write(struct inode *ip)
{
	agent_edit_note_modify(ip);
}

void agent_edit_note_truncate(struct inode *ip)
{
	agent_edit_note_modify(ip);
}

void agent_edit_note_delete(struct inode *ip)
{
	struct agent_file_edit_entry *edit;

	if (ip == 0)
		return;
	agent_edit_lock();
	edit = agent_edit_find_locked(ip->dev, ip->inum);
	if (edit)
		agent_edit_release_locked(edit, 1);
	else
		agent_edit_bump_version_locked(ip->dev, ip->inum);
	agent_edit_unlock();
}

static void agent_file_reset_indexes(void)
{
	for (int i = 0; i < AGENT_FILE_INDEX_BUCKETS; i++) {
		agent_file_status_head[i] = -1;
		agent_file_stage_head[i] = -1;
		agent_file_kind_head[i] = -1;
	}
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		agent_file_status_next[i] = -1;
		agent_file_stage_next[i] = -1;
		agent_file_kind_next[i] = -1;
	}
}

static void agent_file_rebuild_indexes(void)
{
	uint64 b;

	agent_file_reset_indexes();
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used)
			continue;
		if (agent_files[i].status[0]) {
			b = agent_bucket(agent_files[i].status);
			agent_file_status_next[i] = agent_file_status_head[b];
			agent_file_status_head[b] = i;
		}
		if (agent_files[i].stage[0]) {
			b = agent_bucket(agent_files[i].stage);
			agent_file_stage_next[i] = agent_file_stage_head[b];
			agent_file_stage_head[b] = i;
		}
		if (agent_files[i].kind[0]) {
			b = agent_bucket(agent_files[i].kind);
			agent_file_kind_next[i] = agent_file_kind_head[b];
			agent_file_kind_head[b] = i;
		}
	}
	agent_dependency_rebuild_records();
}

static void agent_file_install_empty_store(void)
{
	memset(agent_files, 0, sizeof(agent_files));
	agent_file_loaded = 1;
	agent_file_rebuild_indexes();
	agent_file_persist();
	agent_file_enable_scan();
}

static int agent_field_match(char *want, char *have)
{
	return want[0] == 0 ||
	       strncmp(want, have, AGENT_FILE_LOGICAL_SIZE) == 0;
}

static int agent_file_matches(struct agent_file_query *q,
			      struct agent_file_meta *m)
{
	if (!m->used)
		return 0;
	if (!agent_field_match(q->physical_name, m->physical_name))
		return 0;
	if (!agent_field_match(q->logical_path, m->logical_path))
		return 0;
	if (!agent_field_match(q->project, m->project))
		return 0;
	if (!agent_field_match(q->workflow, m->workflow))
		return 0;
	if (!agent_field_match(q->run_id, m->run_id))
		return 0;
	if (!agent_field_match(q->stage, m->stage))
		return 0;
	if (!agent_field_match(q->kind, m->kind))
		return 0;
	if (!agent_field_match(q->status, m->status))
		return 0;
	if (q->summary_contains[0] &&
	    !agent_contains(m->summary, q->summary_contains))
		return 0;
	return 1;
}

static int agent_file_query_has_filter(struct agent_file_query *q)
{
	return q->physical_name[0] || q->logical_path[0] || q->project[0] ||
	       q->workflow[0] || q->run_id[0] || q->stage[0] ||
	       q->kind[0] || q->status[0] || q->summary_contains[0];
}

static int agent_file_query_cacheable(struct agent_file_query *q)
{
	return (q->flags & AGENT_FILE_QUERY_SCAN) == 0 &&
	       !agent_file_scan_active;
}

static int agent_file_query_key_equal(struct agent_file_query *a,
				      struct agent_file_query *b)
{
	return a->flags == b->flags && a->max_hits == b->max_hits &&
	       strncmp(a->physical_name, b->physical_name,
		       sizeof(a->physical_name)) == 0 &&
	       strncmp(a->logical_path, b->logical_path,
		       sizeof(a->logical_path)) == 0 &&
	       strncmp(a->project, b->project, sizeof(a->project)) == 0 &&
	       strncmp(a->workflow, b->workflow, sizeof(a->workflow)) == 0 &&
	       strncmp(a->run_id, b->run_id, sizeof(a->run_id)) == 0 &&
	       strncmp(a->stage, b->stage, sizeof(a->stage)) == 0 &&
	       strncmp(a->kind, b->kind, sizeof(a->kind)) == 0 &&
	       strncmp(a->status, b->status, sizeof(a->status)) == 0 &&
	       strncmp(a->summary_contains, b->summary_contains,
		       sizeof(a->summary_contains)) == 0;
}

static int agent_file_query_cache_lookup(struct agent_file_query *key,
					 struct agent_file_query_result *r)
{
	struct agent_file_query_cache_entry *e;

	for (int i = 0; i < AGENT_FILE_QUERY_CACHE_MAX; i++) {
		e = &agent_file_query_cache[i];
		if (!e->valid)
			continue;
		if (e->fs_generation != agent_file_generation)
			continue;
		if (!agent_file_query_key_equal(&e->key, key))
			continue;
		memmove(r, &e->result, sizeof(*r));
		r->plan_reason |= AGENT_FILE_QUERY_REASON_CACHE_HIT;
		r->query_ticks = 0;
		return 1;
	}
	return 0;
}

static void agent_file_query_cache_store(struct agent_file_query *key,
					 struct agent_file_query_result *r)
{
	struct agent_file_query_cache_entry *e;

	if (r->total_hits <= 0 || agent_file_scan_active)
		return;
	e = &agent_file_query_cache[agent_file_query_cache_head %
				    AGENT_FILE_QUERY_CACHE_MAX];
	agent_file_query_cache_head =
		(agent_file_query_cache_head + 1) %
		AGENT_FILE_QUERY_CACHE_MAX;
	memset(e, 0, sizeof(*e));
	e->valid = 1;
	e->fs_generation = agent_file_generation;
	memmove(&e->key, key, sizeof(e->key));
	memmove(&e->result, r, sizeof(e->result));
}

static void agent_file_make_hit(struct agent_file_hit *hit,
				struct agent_file_meta *m)
{
	memset(hit, 0, sizeof(*hit));
	hit->fid = m->fid;
	safestrcpy(hit->physical_name, m->physical_name,
		   sizeof(hit->physical_name));
	safestrcpy(hit->logical_path, m->logical_path,
		   sizeof(hit->logical_path));
	safestrcpy(hit->stage, m->stage, sizeof(hit->stage));
	safestrcpy(hit->kind, m->kind, sizeof(hit->kind));
	safestrcpy(hit->status, m->status, sizeof(hit->status));
	safestrcpy(hit->summary, m->summary, sizeof(hit->summary));
	hit->dependency_mask = m->dependency_mask;
	hit->dev = m->dev;
	hit->inum = m->inum;
	hit->size = m->size;
	hit->fs_generation = m->fs_generation;
}

static int agent_file_query_internal(struct agent_file_query *q,
				     struct agent_file_query_result *r)
{
	int cursor = -1;
	int *next = 0;
	int use_index = 0;
	int bucket = -1;
	int max_hits;
	uint64 start;
	uint64 reason = 0;
	struct agent_file_query key;

	memset(r, 0, sizeof(*r));
	r->plan = AGENT_FILE_QUERY_PLAN_SCAN;
	r->index_bucket = -1;
	agent_file_load();
	max_hits = q->max_hits;
	if (max_hits <= 0 || max_hits > AGENT_FILE_QUERY_MAX_HITS)
		max_hits = AGENT_FILE_QUERY_MAX_HITS;
	memmove(&key, q, sizeof(key));
	key.max_hits = max_hits;
	if (agent_file_query_cacheable(&key) &&
	    agent_file_query_cache_lookup(&key, r))
		return r->returned;
	start = agent_ticks();
	if (q->flags & AGENT_FILE_QUERY_SCAN) {
		reason |= AGENT_FILE_QUERY_REASON_FORCED_SCAN;
	} else if (q->flags & AGENT_FILE_QUERY_USE_INDEX) {
		if (q->status[0]) {
			bucket = agent_bucket(q->status);
			cursor = agent_file_status_head[bucket];
			next = agent_file_status_next;
			use_index = 1;
			r->plan = AGENT_FILE_QUERY_PLAN_STATUS_INDEX;
			reason |= AGENT_FILE_QUERY_REASON_STATUS_INDEX;
		} else if (q->stage[0]) {
			bucket = agent_bucket(q->stage);
			cursor = agent_file_stage_head[bucket];
			next = agent_file_stage_next;
			use_index = 1;
			r->plan = AGENT_FILE_QUERY_PLAN_STAGE_INDEX;
			reason |= AGENT_FILE_QUERY_REASON_STAGE_INDEX;
		} else if (q->kind[0]) {
			bucket = agent_bucket(q->kind);
			cursor = agent_file_kind_head[bucket];
			next = agent_file_kind_next;
			use_index = 1;
			r->plan = AGENT_FILE_QUERY_PLAN_KIND_INDEX;
			reason |= AGENT_FILE_QUERY_REASON_KIND_INDEX;
		} else {
			reason |= AGENT_FILE_QUERY_REASON_NO_INDEX_KEY;
		}
	} else {
		reason |= AGENT_FILE_QUERY_REASON_INDEX_OFF;
	}
	if (use_index) {
		r->index_bucket = bucket;
		for (int i = cursor; i >= 0; i = next[i]) {
			r->scanned_records++;
			if (agent_file_matches(q, &agent_files[i])) {
				r->total_hits++;
				if (r->returned < max_hits)
					agent_file_make_hit(
						&r->hits[r->returned++],
						&agent_files[i]);
				else
					r->truncated = 1;
			}
		}
	} else {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			if (!agent_files[i].used)
				continue;
			r->scanned_records++;
			if (agent_file_matches(q, &agent_files[i])) {
				r->total_hits++;
				if (r->returned < max_hits)
					agent_file_make_hit(
						&r->hits[r->returned++],
						&agent_files[i]);
				else
					r->truncated = 1;
			}
		}
	}
	r->used_index = use_index;
	r->candidate_records = r->scanned_records;
	r->query_ticks = agent_ticks() - start;
	r->plan_reason = reason;
	r->fs_generation = agent_file_generation;
	if (agent_file_query_cacheable(&key))
		agent_file_query_cache_store(&key, r);
	return r->returned;
}

static uint64 agent_label_bit(char *label)
{
	uint64 hash = 1469598103934665603ULL;
	int bit;

	if (label == 0 || label[0] == 0)
		return 0;
	for (int i = 0; label[i] && i < AGENT_FILE_FIELD_SIZE; i++) {
		hash ^= (unsigned char)label[i];
		hash *= 1099511628211ULL;
	}
	bit = hash % 60;
	return 1ULL << bit;
}

static int agent_dependency_same_scope(struct agent_file_meta *source,
				       struct agent_file_meta *target)
{
	if (source == 0 || target == 0)
		return 0;
	if (source->project[0] && target->project[0] &&
	    strncmp(source->project, target->project,
		    sizeof(source->project)) != 0)
		return 0;
	if (source->run_id[0] && target->run_id[0] &&
	    strncmp(source->run_id, target->run_id,
		    sizeof(source->run_id)) != 0)
		return 0;
	return 1;
}

static void agent_dependency_record_set(struct agent_dependency_entry *dep,
					struct agent_file_meta *source,
					struct agent_file_meta *target)
{
	memset(dep, 0, sizeof(*dep));
	dep->used = 1;
	dep->flags = 0;
	safestrcpy(dep->namespace, source->project, sizeof(dep->namespace));
	safestrcpy(dep->run_id, source->run_id, sizeof(dep->run_id));
	safestrcpy(dep->source, source->stage, sizeof(dep->source));
	safestrcpy(dep->target, target->stage, sizeof(dep->target));
	safestrcpy(dep->relation, "depends_on", sizeof(dep->relation));
	safestrcpy(dep->summary, target->summary, sizeof(dep->summary));
}

static int agent_dependency_record_exists(struct agent_file_meta *source,
					  struct agent_file_meta *target)
{
	for (int i = 0; i < AGENT_DEPENDENCY_MAX; i++) {
		if (!agent_dependencies[i].used)
			continue;
		if (strncmp(agent_dependencies[i].namespace, source->project,
			    sizeof(source->project)) == 0 &&
		    strncmp(agent_dependencies[i].run_id, source->run_id,
			    sizeof(source->run_id)) == 0 &&
		    strncmp(agent_dependencies[i].source, source->stage,
			    sizeof(source->stage)) == 0 &&
		    strncmp(agent_dependencies[i].target, target->stage,
			    sizeof(target->stage)) == 0)
			return 1;
	}
	return 0;
}

static int agent_key_is(char *key, char *want)
{
	return strncmp(key, want, AGENT_FILE_FIELD_SIZE) == 0;
}

static int agent_dependency_update_from_payload(char *payload,
						struct agent_result *res)
{
	struct agent_dependency_entry dep;
	char key[AGENT_FILE_FIELD_SIZE];
	char val[AGENT_FILE_SUMMARY_SIZE];
	int free_slot = -1;
	int slot = -1;
	int i = 0;
	int k;
	int v;

	memset(&dep, 0, sizeof(dep));
	dep.used = 1;
	dep.flags = AGENT_DEPENDENCY_F_USER;
	safestrcpy(dep.relation, "depends_on", sizeof(dep.relation));

	while (payload[i]) {
		while (payload[i] == ' ' || payload[i] == ';' ||
		       payload[i] == ',')
			i++;
		if (!payload[i])
			break;
		k = 0;
		memset(key, 0, sizeof(key));
		while (payload[i] && payload[i] != '=' &&
		       payload[i] != ':' && payload[i] != ';' &&
		       payload[i] != ',' && k < (int)sizeof(key) - 1)
			key[k++] = payload[i++];
		if (payload[i] != '=' && payload[i] != ':')
			return AGENT_STATUS_BAD_PARAM;
		i++;
		v = 0;
		memset(val, 0, sizeof(val));
		while (payload[i] && payload[i] != ';' &&
		       payload[i] != ',' && v < (int)sizeof(val) - 1)
			val[v++] = payload[i++];
		if (agent_key_is(key, "source") || agent_key_is(key, "from") ||
		    agent_key_is(key, "label"))
			safestrcpy(dep.source, val, sizeof(dep.source));
		else if (agent_key_is(key, "target") || agent_key_is(key, "to"))
			safestrcpy(dep.target, val, sizeof(dep.target));
		else if (agent_key_is(key, "namespace") ||
			 agent_key_is(key, "project"))
			safestrcpy(dep.namespace, val, sizeof(dep.namespace));
		else if (agent_key_is(key, "run_id") || agent_key_is(key, "run"))
			safestrcpy(dep.run_id, val, sizeof(dep.run_id));
		else if (agent_key_is(key, "relation"))
			safestrcpy(dep.relation, val, sizeof(dep.relation));
		else if (agent_key_is(key, "summary"))
			safestrcpy(dep.summary, val, sizeof(dep.summary));
		else
			return AGENT_STATUS_BAD_PARAM;
	}

	if (!dep.source[0] || !dep.target[0])
		return AGENT_STATUS_BAD_PARAM;
	if (!dep.summary[0])
		safestrcpy(dep.summary, dep.target, sizeof(dep.summary));

	for (int d = 0; d < AGENT_DEPENDENCY_MAX; d++) {
		if (!agent_dependencies[d].used) {
			if (free_slot < 0)
				free_slot = d;
			continue;
		}
		if (strncmp(agent_dependencies[d].namespace, dep.namespace,
			    sizeof(dep.namespace)) == 0 &&
		    strncmp(agent_dependencies[d].run_id, dep.run_id,
			    sizeof(dep.run_id)) == 0 &&
		    strncmp(agent_dependencies[d].source, dep.source,
			    sizeof(dep.source)) == 0 &&
		    strncmp(agent_dependencies[d].target, dep.target,
			    sizeof(dep.target)) == 0) {
			slot = d;
			break;
		}
	}
	if (slot < 0)
		slot = free_slot;
	if (slot < 0)
		return AGENT_STATUS_NO_SPACE;

	memmove(&agent_dependencies[slot], &dep, sizeof(dep));
	agent_dependency_generation++;
	res->value0 = agent_dependency_generation;
	res->value1 = agent_label_bit(dep.source);
	res->value2 = agent_label_bit(dep.target);
	agent_result_text(res, "dependency_updated");
	return AGENT_STATUS_OK;
}

static void agent_dependency_rebuild_records(void)
{
	uint64 bit;
	int out = 0;

	for (int i = 0; i < AGENT_DEPENDENCY_MAX; i++) {
		struct agent_dependency_entry keep;

		if (agent_dependencies[i].used &&
		    (agent_dependencies[i].flags & AGENT_DEPENDENCY_F_USER)) {
			memmove(&keep, &agent_dependencies[i], sizeof(keep));
			if (i != out) {
				memmove(&agent_dependencies[out], &keep,
					sizeof(keep));
				memset(&agent_dependencies[i], 0,
				       sizeof(agent_dependencies[i]));
			}
			out++;
		} else {
			memset(&agent_dependencies[i], 0,
			       sizeof(agent_dependencies[i]));
		}
	}
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used || agent_files[i].stage[0] == 0 ||
		    agent_files[i].dependency_mask == 0)
			continue;
		for (int j = 0; j < AGENT_FILE_META_MAX; j++) {
			if (i == j || !agent_files[j].used ||
			    agent_files[j].stage[0] == 0)
				continue;
			if (!agent_dependency_same_scope(&agent_files[i],
							 &agent_files[j]))
				continue;
			bit = agent_label_bit(agent_files[j].stage);
			if ((agent_files[i].dependency_mask & bit) == 0)
				continue;
			if (agent_dependency_record_exists(&agent_files[i],
							   &agent_files[j]))
				continue;
			if (out >= AGENT_DEPENDENCY_MAX)
				break;
			agent_dependency_record_set(&agent_dependencies[out++],
						    &agent_files[i],
						    &agent_files[j]);
		}
	}
	agent_dependency_generation++;
}

static int agent_mask_count(uint64 mask)
{
	int count = 0;

	while (mask) {
		count += mask & 1;
		mask >>= 1;
	}
	return count;
}

static int agent_file_find_fid(int fid)
{
	for (int i = 0; i < AGENT_FILE_META_MAX; i++)
		if (agent_files[i].used && agent_files[i].fid == fid)
			return i;
	return -1;
}

static int agent_file_prefetch_count_stage(struct agent_file_meta *source,
					   char *stage)
{
	int bucket;
	int count = 0;

	if (!stage[0])
		return 0;
	bucket = agent_bucket(stage);
	for (int i = agent_file_stage_head[bucket]; i >= 0;
	     i = agent_file_stage_next[i]) {
		if (!agent_files[i].used)
			continue;
		if (strncmp(agent_files[i].stage, stage,
			    sizeof(agent_files[i].stage)) != 0)
			continue;
		if (source->project[0] &&
		    strncmp(agent_files[i].project, source->project,
			    sizeof(agent_files[i].project)) != 0)
			continue;
		if (source->workflow[0] &&
		    strncmp(agent_files[i].workflow, source->workflow,
			    sizeof(agent_files[i].workflow)) != 0)
			continue;
		if (source->run_id[0] &&
		    strncmp(agent_files[i].run_id, source->run_id,
			    sizeof(agent_files[i].run_id)) != 0)
			continue;
		count++;
	}
	return count;
}

static void agent_file_prefetch_bus_store(struct agent_file_prefetch_hint *hint)
{
	struct agent_file_prefetch_hint copy;
	int slot;
	int visible;
	int start;

	if (hint == 0 || hint->span_id == 0 || hint->fid == 0)
		return;
	visible = agent_span_prefetch_count;
	if (visible > AGENT_FILE_PREFETCH_SPAN_MAX)
		visible = AGENT_FILE_PREFETCH_SPAN_MAX;
	start = (agent_span_prefetch_head + AGENT_FILE_PREFETCH_SPAN_MAX -
		 visible) %
		AGENT_FILE_PREFETCH_SPAN_MAX;
	for (int i = 0; i < visible; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_SPAN_MAX;
		if (agent_span_prefetch_hints[slot].span_id ==
			    hint->span_id &&
		    agent_span_prefetch_hints[slot].fid == hint->fid &&
		    agent_span_prefetch_hints[slot].source_fid ==
			    hint->source_fid &&
		    agent_span_prefetch_hints[slot].source_pid ==
			    hint->source_pid &&
		    agent_span_prefetch_hints[slot].target_pid ==
			    hint->target_pid)
			goto fill;
	}
	slot = agent_span_prefetch_head % AGENT_FILE_PREFETCH_SPAN_MAX;
	agent_span_prefetch_head =
		(agent_span_prefetch_head + 1) % AGENT_FILE_PREFETCH_SPAN_MAX;
	if (agent_span_prefetch_count < AGENT_FILE_PREFETCH_SPAN_MAX)
		agent_span_prefetch_count++;

fill:
	memmove(&copy, hint, sizeof(copy));
	copy.sequence = agent_span_prefetch_next_sequence++;
	copy.reason |= AGENT_FILE_PREFETCH_REASON_SPAN_BUS;
	memmove(&agent_span_prefetch_hints[slot], &copy, sizeof(copy));
}

static void agent_file_prefetch_store(struct proc *p,
				      struct agent_file_meta *source,
				      struct agent_file_meta *target,
				      uint64 source_sequence, uint64 reason,
				      int source_pid, uint64 span_id)
{
	struct agent_file_prefetch_hint *hint;
	struct agent_timeline_record timeline;
	int slot;
	int visible;
	int candidates;

	if (!p || !p->is_agent || !target->used)
		return;
	candidates = agent_file_prefetch_count_stage(source, target->stage);
	for (int i = 0; i < p->agent_prefetch_count; i++) {
		slot = (p->agent_prefetch_head +
			AGENT_FILE_PREFETCH_MAX_HINTS -
			p->agent_prefetch_count + i) %
		       AGENT_FILE_PREFETCH_MAX_HINTS;
		if (p->agent_prefetch_hints[slot].fid == target->fid)
			goto fill;
	}
	slot = p->agent_prefetch_head % AGENT_FILE_PREFETCH_MAX_HINTS;
	p->agent_prefetch_head =
		(p->agent_prefetch_head + 1) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	if (p->agent_prefetch_count < AGENT_FILE_PREFETCH_MAX_HINTS)
		p->agent_prefetch_count++;

fill:
	hint = &p->agent_prefetch_hints[slot];
	memset(hint, 0, sizeof(*hint));
	hint->sequence = ++p->agent_prefetch_sequence;
	hint->source_sequence = source_sequence;
	hint->span_id = span_id ? span_id : p->agent_current_span_id;
	hint->reason = reason;
	hint->tick = agent_ticks();
	hint->fs_generation = agent_file_generation;
	hint->fid = target->fid;
	hint->source_fid = source->fid;
	hint->source_pid = source_pid ? source_pid : p->pid;
	hint->target_pid = p->pid;
	hint->plan = AGENT_FILE_QUERY_PLAN_STAGE_INDEX;
	hint->candidate_records = candidates;
	hint->total_hits = candidates;
	hint->score = 1000 + candidates;
	if (strncmp(target->status, "pending", AGENT_FILE_FIELD_SIZE) == 0) {
		hint->reason |= AGENT_FILE_PREFETCH_REASON_PENDING;
		hint->score += 100;
	}
	if (target->stage[0]) {
		hint->reason |= AGENT_FILE_PREFETCH_REASON_STAGE_INDEX;
		hint->score += 50;
	}
	visible = p->agent_prefetch_count;
	if (visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	hint->score += visible;
	agent_file_make_hit(&hint->hit, target);
	agent_file_prefetch_bus_store(hint);
	if ((reason & AGENT_FILE_PREFETCH_REASON_HANDOFF) == 0) {
		agent_audit_emit(AGENT_AUDIT_KIND_PREFETCH, hint->tick, p,
				 hint->source_pid, hint->target_pid,
				 AGENT_EVENT_NONE, AGENT_TOOL_QUERY_FILE,
				 AGENT_STATUS_OK, hint->source_sequence,
				 hint->span_id, hint->source_sequence,
				 hint->source_fid, hint->fid, hint->reason,
				 target->stage);
	}
	agent_timeline_from_prefetch(p, hint, &timeline);
	agent_observe_record(&timeline);
}

static void agent_file_prefetch_update(struct proc *p,
				       struct agent_file_query *q,
				       struct agent_file_query_result *r,
				       uint64 source_sequence)
{
	struct agent_file_meta *source;
	struct agent_file_meta *target;
	uint64 deps;
	uint64 target_bit;
	uint64 reason;
	int source_slot;
	int emitted;

	if (!p || !p->is_agent || !r || r->returned <= 0)
		return;
	for (int h = 0; h < r->returned; h++) {
		source_slot = agent_file_find_fid(r->hits[h].fid);
		if (source_slot < 0)
			continue;
		source = &agent_files[source_slot];
		emitted = 0;
		for (int d = 0; d < AGENT_DEPENDENCY_MAX; d++) {
			if (!agent_dependencies[d].used)
				continue;
			if (strncmp(agent_dependencies[d].source, source->stage,
				    sizeof(agent_dependencies[d].source)) != 0)
				continue;
			if (agent_dependencies[d].namespace[0] &&
			    strncmp(agent_dependencies[d].namespace,
				    source->project,
				    sizeof(agent_dependencies[d].namespace)) != 0)
				continue;
			if (agent_dependencies[d].run_id[0] &&
			    strncmp(agent_dependencies[d].run_id,
				    source->run_id,
				    sizeof(agent_dependencies[d].run_id)) != 0)
				continue;
			for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
				if (!agent_files[i].used || i == source_slot)
					continue;
				target = &agent_files[i];
				if (strncmp(target->stage,
					    agent_dependencies[d].target,
					    sizeof(target->stage)) != 0)
					continue;
				if (agent_dependencies[d].namespace[0] &&
				    strncmp(target->project,
					    agent_dependencies[d].namespace,
					    sizeof(target->project)) != 0)
					continue;
				if (agent_dependencies[d].run_id[0] &&
				    strncmp(target->run_id,
					    agent_dependencies[d].run_id,
					    sizeof(target->run_id)) != 0)
					continue;
				if (source->workflow[0] &&
				    strncmp(target->workflow, source->workflow,
					    sizeof(target->workflow)) != 0)
					continue;
				reason = AGENT_FILE_PREFETCH_REASON_DEPENDENCY |
					 AGENT_FILE_PREFETCH_REASON_SAME_RUN;
				if ((q->flags & AGENT_FILE_QUERY_USE_INDEX) ||
				    r->used_index)
					reason |=
						AGENT_FILE_PREFETCH_REASON_STAGE_INDEX;
				agent_file_prefetch_store(
					p, source, target, source_sequence,
					reason, p->pid, p->agent_current_span_id);
				emitted = 1;
			}
		}
		if (emitted)
			continue;
		deps = source->dependency_mask;
		if (deps == 0)
			continue;
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			if (!agent_files[i].used || i == source_slot)
				continue;
			target = &agent_files[i];
			target_bit = agent_label_bit(target->stage);
			if (target_bit == 0)
				continue;
			if ((deps & target_bit) == 0)
				continue;
			if (source->project[0] &&
			    strncmp(source->project, target->project,
				    sizeof(source->project)) != 0)
				continue;
			if (source->workflow[0] &&
			    strncmp(source->workflow, target->workflow,
				    sizeof(source->workflow)) != 0)
				continue;
			if (source->run_id[0] &&
			    strncmp(source->run_id, target->run_id,
				    sizeof(source->run_id)) != 0)
				continue;
			reason = AGENT_FILE_PREFETCH_REASON_DEPENDENCY |
				 AGENT_FILE_PREFETCH_REASON_SAME_RUN;
			if ((q->flags & AGENT_FILE_QUERY_USE_INDEX) ||
			    r->used_index)
				reason |=
					AGENT_FILE_PREFETCH_REASON_STAGE_INDEX;
			agent_file_prefetch_store(p, source, target,
						 source_sequence, reason,
						 p->pid,
						 p->agent_current_span_id);
		}
	}
}

static int agent_file_prefetch_handoff(struct proc *target,
				       struct proc *source)
{
	struct agent_file_prefetch_hint hint;
	struct agent_file_meta *source_meta;
	struct agent_file_meta *target_meta;
	uint64 reason;
	int visible;
	int start;
	int slot;
	int source_slot;
	int target_slot;
	int copied = 0;

	if (!target || !source || target == source || !target->is_agent ||
	    !source->is_agent)
		return 0;
	visible = source->agent_prefetch_count;
	if (visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	start = (source->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
		 visible) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	for (int i = 0; i < visible; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_MAX_HINTS;
		memmove(&hint, &source->agent_prefetch_hints[slot],
			sizeof(hint));
		source_slot = agent_file_find_fid(hint.source_fid);
		target_slot = agent_file_find_fid(hint.fid);
		if (source_slot < 0 || target_slot < 0)
			continue;
		source_meta = &agent_files[source_slot];
		target_meta = &agent_files[target_slot];
		reason = hint.reason | AGENT_FILE_PREFETCH_REASON_HANDOFF;
		agent_file_prefetch_store(target, source_meta, target_meta,
					  hint.source_sequence, reason,
					  hint.source_pid ? hint.source_pid :
							    source->pid,
					  hint.span_id ? hint.span_id :
							source->agent_current_span_id);
		agent_audit_prefetch_handoff(source, target, &hint,
					     target_meta, reason);
		copied++;
	}
	return copied;
}

static int agent_file_find(char *selector)
{
	agent_file_load();
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used)
			continue;
		if (strncmp(selector, agent_files[i].physical_name,
			    sizeof(agent_files[i].physical_name)) == 0 ||
		    strncmp(selector, agent_files[i].logical_path,
			    sizeof(agent_files[i].logical_path)) == 0 ||
		    strncmp(selector, agent_files[i].stage,
			    sizeof(agent_files[i].stage)) == 0)
			return i;
	}
	return -1;
}

static int agent_file_digest_select(char *selector, char *physical, int n)
{
	struct agent_file_query query;
	int found;

	if (agent_text_empty(selector))
		return AGENT_STATUS_BAD_PARAM;
	memset(physical, 0, n);
	if (agent_contains(selector, "=") || agent_contains(selector, ":")) {
		if (agent_query_from_payload(&query, selector) < 0)
			return AGENT_STATUS_BAD_PARAM;
		if (!agent_file_query_has_filter(&query))
			return AGENT_STATUS_BAD_PARAM;
		agent_file_load();
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			if (!agent_files[i].used)
				continue;
			if (agent_file_matches(&query, &agent_files[i])) {
				safestrcpy(physical,
					   agent_files[i].physical_name, n);
				return 0;
			}
		}
		return AGENT_STATUS_NOT_FOUND;
	}
	found = agent_file_find(selector);
	if (found >= 0) {
		safestrcpy(physical, agent_files[found].physical_name, n);
		return 0;
	}
	safestrcpy(physical, selector, n);
	return 0;
}

static void agent_file_digest_preview(char *preview, int *pos, char c)
{
	if (*pos >= AGENT_FAST_RESULT_SIZE - 1)
		return;
	if (c == '\n' || c == '\r' || c == '\t')
		c = ' ';
	if (c < 32 || c > 126)
		c = '.';
	preview[*pos] = c;
	(*pos)++;
	preview[*pos] = 0;
}

static int agent_file_digest_cacheable(struct inode *ip)
{
	uint64 version;

	if (ip == 0)
		return 0;
	return ip->agent_meta_slot > 0 &&
	       ip->agent_meta_version == AGENT_INODE_META_VERSION &&
	       agent_file_content_version(ip, &version, 1) == 0;
}

static uint64 agent_file_digest_content_generation(struct inode *ip)
{
	uint64 version;

	if (ip == 0)
		return 0;
	if (agent_file_content_version(ip, &version, 1) < 0)
		return (uint64)-1;
	return version;
}

static int agent_file_digest_cache_lookup(struct inode *ip,
					  struct agent_result *res)
{
	struct agent_file_digest_cache_entry *e;
	uint64 content_generation;

	content_generation = agent_file_digest_content_generation(ip);
	for (int i = 0; i < AGENT_FILE_DIGEST_CACHE_MAX; i++) {
		e = &agent_file_digest_cache[i];
		if (!e->valid)
			continue;
		if (e->dev != ip->dev || e->inum != ip->inum)
			continue;
		if (e->size != ip->size)
			continue;
		if (e->content_generation != content_generation)
			continue;
		res->value0 = e->size;
		res->value1 = e->bytes;
		res->value2 = e->hash;
		agent_result_text(res, e->preview[0] ? e->preview :
						"empty_file");
		agent_file_digest_cache_hits++;
		return 1;
	}
	agent_file_digest_cache_misses++;
	return 0;
}

static void agent_file_digest_cache_store(struct inode *ip, uint64 bytes,
					  uint64 hash, char *preview)
{
	struct agent_file_digest_cache_entry *e;

	if (!agent_file_digest_cacheable(ip))
		return;
	e = &agent_file_digest_cache[agent_file_digest_cache_head %
				     AGENT_FILE_DIGEST_CACHE_MAX];
	agent_file_digest_cache_head =
		(agent_file_digest_cache_head + 1) %
		AGENT_FILE_DIGEST_CACHE_MAX;
	memset(e, 0, sizeof(*e));
	e->valid = 1;
	e->dev = ip->dev;
	e->inum = ip->inum;
	e->size = ip->size;
	e->content_generation = agent_file_digest_content_generation(ip);
	e->bytes = bytes;
	e->hash = hash;
	safestrcpy(e->preview, preview[0] ? preview : "empty_file",
		   sizeof(e->preview));
}

static void agent_file_digest_read(char *selector, struct agent_result *res)
{
	char physical[AGENT_FILE_NAME_SIZE];
	char preview[AGENT_FAST_RESULT_SIZE];
	char buf[AGENT_FILE_DIGEST_CHUNK];
	struct inode *ip;
	uint64 hash = 1469598103934665603ULL;
	uint64 limit;
	uint64 total = 0;
	uint off = 0;
	int pos = 0;
	int rc;
	int cacheable;

	rc = agent_file_digest_select(selector, physical, sizeof(physical));
	if (rc < 0) {
		res->status = rc;
		agent_result_text(res, rc == AGENT_STATUS_NOT_FOUND ?
					   "digest_not_found" :
					   "bad_selector");
		return;
	}
	if (strncmp(physical, AGENT_META_STORE_NAME,
		    sizeof(AGENT_META_STORE_NAME)) == 0) {
		res->status = AGENT_STATUS_DENIED;
		agent_result_text(res, "denied");
		return;
	}
	if ((ip = namei(physical)) == 0) {
		res->status = AGENT_STATUS_NOT_FOUND;
		agent_result_text(res, "digest_not_found");
		return;
	}
	ivalid(ip);
	if (ip->type != T_FILE) {
		iput(ip);
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "not_file");
		return;
	}
	cacheable = agent_file_digest_cacheable(ip);
	if (cacheable && agent_file_digest_cache_lookup(ip, res)) {
		iput(ip);
		return;
	}
	memset(preview, 0, sizeof(preview));
	limit = ip->size < AGENT_FILE_DIGEST_MAX_BYTES ?
			ip->size :
			AGENT_FILE_DIGEST_MAX_BYTES;
	while (total < limit) {
		uint want = MIN((uint)(limit - total),
				(uint)sizeof(buf));
		int got = readi(ip, 0, (uint64)buf, off, want);
		if (got < 0) {
			iput(ip);
			res->status = AGENT_STATUS_BAD_REQUEST;
			agent_result_text(res, "digest_read_error");
			return;
		}
		if (got == 0)
			break;
		for (int i = 0; i < got; i++) {
			hash ^= (unsigned char)buf[i];
			hash *= 1099511628211ULL;
			agent_file_digest_preview(preview, &pos, buf[i]);
		}
		total += got;
		off += got;
	}
	res->value0 = ip->size;
	res->value1 = total;
	res->value2 = hash;
	if (cacheable)
		agent_file_digest_cache_store(ip, total, hash, preview);
	iput(ip);
	agent_result_text(res, preview[0] ? preview : "empty_file");
}

static int agent_dependency_for_label(char *label, char *namespace,
				      char *run_id, uint64 *mask)
{
	uint64 found = 0;

	for (int i = 0; i < AGENT_DEPENDENCY_MAX; i++) {
		if (!agent_dependencies[i].used)
			continue;
		if (strncmp(agent_dependencies[i].source, label,
			    sizeof(agent_dependencies[i].source)) != 0)
			continue;
		if (namespace && namespace[0] &&
		    strncmp(agent_dependencies[i].namespace, namespace,
			    sizeof(agent_dependencies[i].namespace)) != 0)
			continue;
		if (run_id && run_id[0] &&
		    strncmp(agent_dependencies[i].run_id, run_id,
			    sizeof(agent_dependencies[i].run_id)) != 0)
			continue;
		if (agent_dependencies[i].target[0]) {
			found |= agent_label_bit(agent_dependencies[i].source);
			found |= agent_label_bit(agent_dependencies[i].target);
		}
	}
	if (found) {
		*mask = found;
		return 0;
	}
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used || agent_files[i].dependency_mask == 0)
			continue;
		if (namespace && namespace[0] &&
		    strncmp(agent_files[i].project, namespace,
			    sizeof(agent_files[i].project)) != 0)
			continue;
		if (run_id && run_id[0] &&
		    strncmp(agent_files[i].run_id, run_id,
			    sizeof(agent_files[i].run_id)) != 0)
			continue;
		if (strncmp(agent_files[i].stage, label,
			    sizeof(agent_files[i].stage)) == 0 ||
		    strncmp(agent_files[i].physical_name, label,
			    sizeof(agent_files[i].physical_name)) == 0 ||
		    strncmp(agent_files[i].logical_path, label,
			    sizeof(agent_files[i].logical_path)) == 0)
			found |= agent_files[i].dependency_mask;
	}
	if (found) {
		*mask = found;
		return 0;
	}
	return -1;
}

static void agent_stage_text(uint64 mask, char *out, int n)
{
	int first = 1;
	uint64 bit;
	uint64 emitted = 0;

	memset(out, 0, n);
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used || agent_files[i].stage[0] == 0)
			continue;
		bit = agent_label_bit(agent_files[i].stage);
		if ((mask & bit) == 0)
			continue;
		if ((emitted & bit) != 0)
			continue;
		emitted |= bit;
		if (!first)
			agent_text_append(out, n, "+");
		agent_text_append(out, n, agent_files[i].stage);
		first = 0;
	}
	if (!out[0])
		safestrcpy(out, "none", n);
}

static int agent_action_seen(int tool_id, char *project, char *run_id,
			     char *stage, uint64 request_id)
{
	struct agent_action_history_entry *e;

	if (request_id == 0)
		return 0;
	for (int i = 0; i < agent_action_history_count; i++) {
		e = &agent_action_history[i];
		if (e->tool_id == tool_id && e->request_id == request_id &&
		    strncmp(e->project, project, sizeof(e->project)) == 0 &&
		    strncmp(e->run_id, run_id, sizeof(e->run_id)) == 0 &&
		    strncmp(e->stage, stage, sizeof(e->stage)) == 0)
			return 1;
	}
	return 0;
}

static void agent_action_remember(int tool_id, char *project, char *run_id,
				  char *stage, uint64 request_id)
{
	struct agent_action_history_entry *e;

	if (request_id == 0)
		return;
	if (agent_action_history_count >= AGENT_ACTION_HISTORY_MAX) {
		memmove(agent_action_history, agent_action_history + 1,
			sizeof(agent_action_history[0]) *
				(AGENT_ACTION_HISTORY_MAX - 1));
		agent_action_history_count = AGENT_ACTION_HISTORY_MAX - 1;
	}
	e = &agent_action_history[agent_action_history_count++];
	memset(e, 0, sizeof(*e));
	e->tool_id = tool_id;
	e->request_id = request_id;
	safestrcpy(e->project, project, sizeof(e->project));
	safestrcpy(e->run_id, run_id, sizeof(e->run_id));
	safestrcpy(e->stage, stage, sizeof(e->stage));
}

static void agent_wake_event_waiters(struct proc *p)
{
	wait_queue_wake_all(&p->agent_event_waiters);
	p->agent_wait_wakeup_count++;
}

static void agent_wake_timeline_waiters(struct proc *p)
{
	wait_queue_wake_all(&p->agent_timeline_waiters);
}

static int agent_filter_matches(struct proc *target, int type, char *payload)
{
	for (int i = 0; i < AGENT_WATCH_MAX; i++) {
		if (!target->agent_watch_valid[i])
			continue;
		if (target->agent_watch_event_type[i] != AGENT_EVENT_NONE &&
		    target->agent_watch_event_type[i] != type)
			continue;
		if (target->agent_watch_filter[i][0] &&
		    !agent_contains(payload, target->agent_watch_filter[i]))
			continue;
		return 1;
	}
	return 0;
}

static int agent_watch_set(struct proc *p, int event_type, char *filter)
{
	int free_slot = -1;

	for (int i = 0; i < AGENT_WATCH_MAX; i++) {
		if (p->agent_watch_valid[i] &&
		    p->agent_watch_event_type[i] == event_type &&
		    strncmp(p->agent_watch_filter[i], filter,
			    AGENT_WATCH_FILTER_SIZE) == 0)
			return 0;
		if (!p->agent_watch_valid[i] && free_slot < 0)
			free_slot = i;
	}
	if (free_slot < 0)
		return AGENT_STATUS_NO_SPACE;
	p->agent_watch_valid[free_slot] = 1;
	p->agent_watch_event_type[free_slot] = event_type;
	safestrcpy(p->agent_watch_filter[free_slot], filter,
		   sizeof(p->agent_watch_filter[free_slot]));
	p->agent_watch_count++;
	return 0;
}

static int agent_watch_clear(struct proc *p, int event_type, char *filter)
{
	int removed = 0;
	int clear_all = event_type == AGENT_EVENT_NONE && filter[0] == 0;

	for (int i = 0; i < AGENT_WATCH_MAX; i++) {
		if (!p->agent_watch_valid[i])
			continue;
		if (!clear_all) {
			if (event_type != AGENT_EVENT_NONE &&
			    p->agent_watch_event_type[i] != event_type)
				continue;
			if (filter[0] &&
			    strncmp(p->agent_watch_filter[i], filter,
				    AGENT_WATCH_FILTER_SIZE) != 0)
				continue;
		}
		p->agent_watch_valid[i] = 0;
		p->agent_watch_event_type[i] = AGENT_EVENT_NONE;
		memset(p->agent_watch_filter[i], 0,
		       sizeof(p->agent_watch_filter[i]));
		removed++;
	}
	p->agent_watch_count -= removed;
	if (p->agent_watch_count < 0)
		p->agent_watch_count = 0;
	return removed;
}

static int agent_event_dequeue(struct proc *p, struct agent_event *event)
{
	if (p->agent_event_count_queued <= 0)
		return 0;
	memmove(event, &p->agent_events[p->agent_event_head], sizeof(*event));
	memset(&p->agent_events[p->agent_event_head], 0, sizeof(*event));
	p->agent_event_head =
		(p->agent_event_head + 1) % AGENT_EVENT_QUEUE_CAP;
	p->agent_event_count_queued--;
	return 1;
}

static int agent_queue_event(struct proc *target, int source_pid, int type,
			     uint64 corr_id, uint64 cause_sequence,
			     uint64 span_id, char *payload)
{
	struct agent_event *event;

	if (!target->is_agent)
		return 0;
	if (!agent_filter_matches(target, type, payload))
		return 0;
	if (target->agent_event_count_queued >= AGENT_EVENT_QUEUE_CAP) {
		target->agent_event_dropped++;
		return -1;
	}
	event = &target->agent_events[target->agent_event_tail];
	memset(event, 0, sizeof(*event));
	event->type = type;
	event->source_pid = source_pid;
	event->target_pid = target->pid;
	event->status = AGENT_STATUS_OK;
	event->event_id = next_event_id++;
	event->tick = agent_ticks();
	event->corr_id = corr_id;
	event->cause_sequence = cause_sequence;
	event->span_id = span_id;
	safestrcpy(event->payload, payload, sizeof(event->payload));
	target->agent_event_tail =
		(target->agent_event_tail + 1) % AGENT_EVENT_QUEUE_CAP;
	target->agent_event_count_queued++;
	target->agent_event_count++;
	agent_audit_event(AGENT_AUDIT_KIND_EVENT_ENQUEUE, target, event);
	agent_wake_event_waiters(target);
	return 1;
}

static int agent_deliver_pid(int pid, struct proc *source, int source_pid,
			     int type, uint64 corr_id, uint64 cause_sequence,
			     uint64 span_id, char *payload)
{
	int delivered;

	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (p->state == P_UNUSED || p->pid != pid)
			continue;
		delivered = agent_queue_event(p, source_pid, type, corr_id,
					      cause_sequence, span_id,
					      payload);
		if (delivered > 0 && type == AGENT_EVENT_MESSAGE)
			agent_file_prefetch_handoff(p, source);
		return delivered;
	}
	return 0;
}

static int agent_wait_take_cancel(struct proc *p, struct agent_event *event)
{
	if (!p->agent_wait_cancel_pending)
		return 0;
	memset(event, 0, sizeof(*event));
	event->type = AGENT_EVENT_CANCELLED;
	event->source_pid = p->agent_wait_cancel_source_pid;
	event->target_pid = p->pid;
	event->status = AGENT_STATUS_CANCELLED;
	event->event_id = p->agent_wait_cancel_event_id;
	event->tick = p->agent_wait_cancel_tick;
	event->corr_id = p->agent_wait_cancel_corr_id;
	event->cause_sequence = p->agent_wait_cancel_cause_sequence;
	event->span_id = p->agent_wait_cancel_span_id;
	safestrcpy(event->payload, p->agent_wait_cancel_reason,
		   sizeof(event->payload));
	p->agent_wait_cancel_pending = 0;
	p->agent_wait_deadline_valid = 0;
	p->agent_wait_deadline = 0;
	p->loop_state = AGENT_LOOP_RUNNING;
	return 1;
}

static int agent_deliver_watchers(int source_pid, int type, uint64 corr_id,
				  uint64 cause_sequence, uint64 span_id,
				  char *payload)
{
	int delivered = 0;
	int rc;

	for (struct proc *p = pool; p < &pool[NPROC]; p++)
		if (p->state != P_UNUSED) {
			rc = agent_queue_event(p, source_pid, type, corr_id,
					       cause_sequence, span_id,
					       payload);
			if (rc < 0)
				return rc;
			delivered += rc;
		}
	return delivered;
}

static int agent_append_system_context(struct proc *p, int tool_id,
				       uint64 request_id, uint64 arg0,
				       char *payload, char *result, int status,
				       uint64 value0, uint64 value1,
				       uint64 value2)
{
	struct agent_op op;
	struct agent_result latest;

	if (!p->is_agent)
		return -1;
	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = tool_id;
	op.request_id = request_id;
	op.arg0 = arg0;
	safestrcpy(op.payload, payload, sizeof(op.payload));
	memset(&latest, 0, sizeof(latest));
	latest.version = AGENT_CALL_VERSION;
	latest.status = status;
	latest.tool_id = tool_id;
	latest.request_id = request_id;
	p->agent_call_count++;
	latest.sequence = p->agent_call_count;
	latest.value0 = value0;
	latest.value1 = value1;
	latest.value2 = value2;
	agent_result_text(&latest, result);
	if (agent_append_context_flags(p, &op, &latest, agent_ticks(),
				       AGENT_CONTEXT_RECORD_F_SYSTEM) < 0)
		return AGENT_STATUS_NO_SPACE;
	if (agent_write_header(p) < 0)
		return AGENT_STATUS_NO_SPACE;
	return 0;
}

static void agent_text_append(char *dst, int n, char *src)
{
	int len;

	if (n <= 0 || src == 0)
		return;
	len = strlen(dst);
	if (len >= n - 1)
		return;
	safestrcpy(dst + len, src, n - len);
}

static void agent_file_event_payload(struct agent_file_meta *meta, char *out,
				     int n)
{
	memset(out, 0, n);
	if (meta->status[0]) {
		agent_text_append(out, n, "status=");
		agent_text_append(out, n, meta->status);
	}
	if (meta->stage[0]) {
		agent_text_append(out, n, ";stage=");
		agent_text_append(out, n, meta->stage);
	}
	if (meta->run_id[0]) {
		agent_text_append(out, n, ";run_id=");
		agent_text_append(out, n, meta->run_id);
	}
	if (meta->project[0]) {
		agent_text_append(out, n, ";project=");
		agent_text_append(out, n, meta->project);
	}
	if (!out[0])
		safestrcpy(out, "status=changed", n);
}

static int agent_parse_selector(char *payload, char *stage, int stage_n,
				char *project, int project_n, char *run_id,
				int run_id_n)
{
	struct agent_file_query query;

	memset(stage, 0, stage_n);
	memset(project, 0, project_n);
	memset(run_id, 0, run_id_n);
	if (agent_contains(payload, "=") || agent_contains(payload, ":")) {
		if (agent_query_from_payload(&query, payload) < 0)
			return -1;
		safestrcpy(stage, query.stage, stage_n);
		safestrcpy(project, query.project, project_n);
		safestrcpy(run_id, query.run_id, run_id_n);
	} else {
		safestrcpy(stage, payload, stage_n);
	}
	return 0;
}

static int agent_file_update_status_select(char *stage, char *project,
					   char *run_id, char *status,
					   char *summary)
{
	int updated = 0;

	agent_file_load();
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used)
			continue;
		if (strncmp(agent_files[i].stage, stage,
			    sizeof(agent_files[i].stage)) == 0 &&
		    (!project[0] ||
		     strncmp(agent_files[i].project, project,
			     sizeof(agent_files[i].project)) == 0) &&
		    (!run_id[0] ||
		     strncmp(agent_files[i].run_id, run_id,
			     sizeof(agent_files[i].run_id)) == 0)) {
			safestrcpy(agent_files[i].status, status,
				   sizeof(agent_files[i].status));
			if (summary && summary[0])
				safestrcpy(agent_files[i].summary, summary,
					   sizeof(agent_files[i].summary));
			agent_files[i].updated_tick = agent_ticks();
			agent_files[i].fs_generation = ++agent_file_generation;
			agent_file_bind_slot(i);
			updated++;
		}
	}
	agent_file_rebuild_indexes();
	if (updated)
		agent_file_persist();
	return updated;
}

static int agent_query_from_payload(struct agent_file_query *q, char *payload)
{
	char key[AGENT_FILE_FIELD_SIZE];
	char val[AGENT_FILE_LOGICAL_SIZE];
	int i = 0;
	int k;
	int v;

	memset(q, 0, sizeof(*q));
	q->flags = AGENT_FILE_QUERY_USE_INDEX;
	q->max_hits = AGENT_FILE_QUERY_MAX_HITS;
	while (payload[i]) {
		while (payload[i] == ' ' || payload[i] == ';' ||
		       payload[i] == ',')
			i++;
		k = 0;
		memset(key, 0, sizeof(key));
		while (payload[i] && payload[i] != '=' &&
		       payload[i] != ':' && payload[i] != ';' &&
		       payload[i] != ',' && k < (int)sizeof(key) - 1)
			key[k++] = payload[i++];
		if (payload[i] != '=' && payload[i] != ':') {
			return -1;
		}
		i++;
		v = 0;
		memset(val, 0, sizeof(val));
		while (payload[i] && payload[i] != ';' &&
		       payload[i] != ',' && v < (int)sizeof(val) - 1)
			val[v++] = payload[i++];
		if (strncmp(key, "path", sizeof(key)) == 0 ||
		    strncmp(key, "physical", sizeof(key)) == 0)
			safestrcpy(q->physical_name, val,
				   sizeof(q->physical_name));
		else if (strncmp(key, "logical", sizeof(key)) == 0 ||
			 strncmp(key, "object", sizeof(key)) == 0 ||
			 strncmp(key, "object_id", sizeof(key)) == 0)
			safestrcpy(q->logical_path, val,
				   sizeof(q->logical_path));
		else if (strncmp(key, "project", sizeof(key)) == 0 ||
			 strncmp(key, "namespace", sizeof(key)) == 0)
			safestrcpy(q->project, val, sizeof(q->project));
		else if (strncmp(key, "workflow", sizeof(key)) == 0)
			safestrcpy(q->workflow, val, sizeof(q->workflow));
		else if (strncmp(key, "run", sizeof(key)) == 0 ||
			 strncmp(key, "run_id", sizeof(key)) == 0)
			safestrcpy(q->run_id, val, sizeof(q->run_id));
		else if (strncmp(key, "stage", sizeof(key)) == 0 ||
			 strncmp(key, "label", sizeof(key)) == 0)
			safestrcpy(q->stage, val, sizeof(q->stage));
		else if (strncmp(key, "kind", sizeof(key)) == 0 ||
			 strncmp(key, "type", sizeof(key)) == 0)
			safestrcpy(q->kind, val, sizeof(q->kind));
		else if (strncmp(key, "status", sizeof(key)) == 0 ||
			 strncmp(key, "state", sizeof(key)) == 0)
			safestrcpy(q->status, val, sizeof(q->status));
		else if (strncmp(key, "summary", sizeof(key)) == 0)
			safestrcpy(q->summary_contains, val,
				   sizeof(q->summary_contains));
		else
			return -1;
	}
	return 0;
}

static void agent_object_state_update(struct proc *p, struct agent_op *op,
				      struct agent_result *res,
				      char *ok_text, char *event_action,
				      char *summary, int propagate_deps,
				      int require_selector,
				      int history_tool_id)
{
	uint64 deps = 0;
	uint64 target_bit;
	int action_tool_id;
	int updated;
	int delivered;
	char event_payload[AGENT_EVENT_PAYLOAD_SIZE];
	char selector_label[AGENT_FILE_FIELD_SIZE];
	char selector_project[AGENT_FILE_PROJECT_SIZE];
	char selector_run_id[AGENT_FILE_FIELD_SIZE];

	action_tool_id = history_tool_id ? history_tool_id : op->tool_id;
	if (require_selector && !agent_contains(op->payload, "=") &&
	    !agent_contains(op->payload, ":")) {
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "selector_required");
		return;
	}
	if (agent_parse_selector(op->payload, selector_label,
				 sizeof(selector_label), selector_project,
				 sizeof(selector_project), selector_run_id,
				 sizeof(selector_run_id)) < 0) {
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "bad_selector");
		return;
	}
	if (!selector_label[0]) {
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "label_required");
		return;
	}
	if (propagate_deps &&
	    agent_dependency_for_label(selector_label, selector_project,
				       selector_run_id, &deps) < 0)
		deps = 0;
	if (agent_action_seen(action_tool_id, selector_project, selector_run_id,
			      selector_label, op->request_id)) {
		res->status = AGENT_STATUS_DUPLICATE;
		agent_result_text(res, "duplicate");
		return;
	}
	updated = agent_file_update_status_select(
		selector_label, selector_project, selector_run_id, "ok",
		summary);
	if (updated == 0) {
		res->status = AGENT_STATUS_NOT_FOUND;
		agent_result_text(res, "target_not_found");
		return;
	}
	if (propagate_deps) {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
			if (!agent_files[i].used)
				continue;
			target_bit = agent_label_bit(agent_files[i].stage);
			if ((deps & target_bit) == 0)
				continue;
			if (selector_project[0] &&
			    strncmp(agent_files[i].project, selector_project,
				    sizeof(agent_files[i].project)) != 0)
				continue;
			if (selector_run_id[0] &&
			    strncmp(agent_files[i].run_id, selector_run_id,
				    sizeof(agent_files[i].run_id)) != 0)
				continue;
			agent_file_update_status_select(agent_files[i].stage,
							selector_project,
							selector_run_id, "ok",
							"dependency refreshed");
		}
	}
	agent_action_remember(action_tool_id, selector_project, selector_run_id,
			      selector_label, op->request_id);
	res->value0 = deps;
	res->value1 = op->request_id;
	agent_result_text(res, ok_text);
	memset(event_payload, 0, sizeof(event_payload));
	agent_text_append(event_payload, sizeof(event_payload),
			  "state=ok;label=");
	agent_text_append(event_payload, sizeof(event_payload), selector_label);
	if (selector_run_id[0]) {
		agent_text_append(event_payload, sizeof(event_payload),
				  ";run_id=");
		agent_text_append(event_payload, sizeof(event_payload),
				  selector_run_id);
	}
	if (event_action && event_action[0]) {
		agent_text_append(event_payload, sizeof(event_payload),
				  ";action=");
		agent_text_append(event_payload, sizeof(event_payload),
				  event_action);
	}
	delivered = agent_deliver_watchers(p->pid, AGENT_EVENT_JOB_DONE,
					   op->request_id,
					   p->agent_call_count + 1,
					   p->agent_current_span_id,
					   event_payload);
	res->value2 = delivered;
}

static void agent_execute_op(struct proc *p, struct agent_op *op,
			     struct agent_result *res)
{
	struct agent_proc_snapshot snapshot;
	struct inode *ip;
	struct agent_file_query query;
	struct agent_file_query_result query_result;
	uint64 deps;
	int found;
	int delivered;
	char dependency_label[AGENT_FILE_FIELD_SIZE];
	char dependency_project[AGENT_FILE_PROJECT_SIZE];
	char dependency_run_id[AGENT_FILE_FIELD_SIZE];

	if (op->version != AGENT_CALL_VERSION) {
		res->status = AGENT_STATUS_BAD_REQUEST;
		agent_result_text(res, "bad_request");
		return;
	}

	switch (op->tool_id) {
	case AGENT_TOOL_ECHO:
		res->value0 = strlen(op->payload);
		res->value1 = op->arg0;
		res->value2 = op->arg1;
		agent_result_text(res, op->payload);
		break;
	case AGENT_TOOL_PID_INFO:
		res->value0 = p->pid;
		res->value1 = p->agent_id;
		res->value2 = p->is_agent;
		agent_result_text(res, "pid_info");
		break;
	case AGENT_TOOL_CTX_STAT:
		res->value0 = p->agent_ctx_base;
		res->value1 = p->agent_ctx_size;
		res->value2 = p->agent_call_count;
		agent_result_text(res, "ctx_stat");
		break;
	case AGENT_TOOL_QUERY_PROCESS:
		if (!agent_has_cap(p, AGENT_CAP_PROCESS_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_collect_proc_snapshot(op->arg0 == AGENT_TYPE_AGENT,
					    &snapshot);
		res->value0 = snapshot.used;
		res->value1 = snapshot.agents;
		res->value2 = snapshot.runnable;
		agent_result_text(res, "query_process");
		break;
	case AGENT_TOOL_GET_SYSTEM_STATUS:
		if (!agent_has_cap(p, AGENT_CAP_PROCESS_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_collect_proc_snapshot(0, &snapshot);
		res->value0 = snapshot.used;
		res->value1 = snapshot.agents;
		res->value2 = agent_ticks();
		agent_result_text(res, "system_status");
		break;
	case AGENT_TOOL_READ_CONTEXT:
		res->value0 = p->context_path_count < p->context_path_capacity ?
				      p->context_path_count + 1 :
				      p->context_path_capacity;
		res->value1 = p->context_path_capacity ?
				      (p->context_path_head + 1) %
					      p->context_path_capacity :
				      0;
		res->value2 = p->agent_call_count;
		agent_result_text(res, "read_context");
		break;
	case AGENT_TOOL_QUERY_FILE:
		if (!agent_has_cap(p, AGENT_CAP_META_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (agent_text_empty(op->payload)) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "path_required");
			break;
		}
		if (agent_contains(op->payload, "=") ||
		    agent_contains(op->payload, ":")) {
			if (agent_query_from_payload(&query, op->payload) < 0) {
				res->status = AGENT_STATUS_BAD_PARAM;
				agent_result_text(res, "bad_selector");
				break;
			}
			if (!agent_file_query_has_filter(&query)) {
				res->status = AGENT_STATUS_BAD_PARAM;
				agent_result_text(res, "empty_selector");
				break;
			}
			agent_file_query_internal(&query, &query_result);
			agent_file_prefetch_update(p, &query, &query_result,
						   p->agent_call_count);
			res->value0 = query_result.total_hits;
			res->value1 = query_result.scanned_records;
			res->value2 = (uint64)query_result.used_index |
				      ((uint64)query_result.truncated << 1);
			if (query_result.returned > 0)
				agent_result_text(
					res,
					query_result.hits[0].physical_name);
			else
				agent_result_text(res, "empty");
			break;
		}
		if ((ip = namei(op->payload)) == 0) {
			res->status = AGENT_STATUS_NOT_FOUND;
			agent_result_text(res, "file_not_found");
			break;
		}
		ivalid(ip);
		res->value0 = ip->type;
		res->value1 = ip->inum;
		res->value2 = ip->size;
		iput(ip);
		agent_result_text(res, "query_file");
		break;
	case AGENT_TOOL_SEND_MESSAGE:
		if (!agent_has_cap(p, AGENT_CAP_MESSAGE_SEND)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (op->arg0 == 0 || agent_text_empty(op->payload)) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "message_required");
			break;
		}
		for (struct proc *target = pool; target < &pool[NPROC];
		     target++) {
			if (target->state != P_UNUSED &&
			    target->pid == (int)op->arg0 && target->is_agent) {
				delivered = agent_deliver_pid(
					(int)op->arg0, p, p->pid,
					AGENT_EVENT_MESSAGE, op->request_id,
					p->agent_call_count + 1,
					p->agent_current_span_id,
					op->payload);
				if (delivered < 0) {
					res->status = AGENT_STATUS_NO_SPACE;
					agent_result_text(res, "event_queue_full");
					return;
				}
				target->agent_mailbox_valid = 1;
				target->agent_mailbox_from = p->pid;
				safestrcpy(target->agent_mailbox, op->payload,
					   sizeof(target->agent_mailbox));
				res->value0 = op->arg0;
				res->value1 = p->pid;
				res->value2 = strlen(op->payload);
				agent_result_text(res, "send_message");
				return;
			}
		}
		res->status = AGENT_STATUS_NOT_FOUND;
		agent_result_text(res, "target_missing");
		break;
	case AGENT_TOOL_READ_MESSAGE:
		if (p->agent_mailbox_valid) {
			res->value0 = 1;
			res->value1 = p->agent_mailbox_from;
			res->value2 = strlen(p->agent_mailbox);
			agent_result_text(res, p->agent_mailbox);
			p->agent_mailbox_valid = 0;
		} else {
			res->value0 = 0;
			agent_result_text(res, "no_message");
		}
		break;
	case AGENT_TOOL_FILE_META_INIT:
		if (!agent_has_cap(p, AGENT_CAP_META_WRITE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_action_history_count = 0;
		memset(agent_action_history, 0, sizeof(agent_action_history));
		agent_file_loaded = 0;
		res->value0 = agent_file_load();
		if (res->value0 == 0) {
			agent_file_install_empty_store();
			res->value0 = 0;
		}
		agent_file_enable_scan();
		agent_result_text(res, "file_meta_init");
		break;
	case AGENT_TOOL_READ_FILE_SUMMARY:
		if (!agent_has_cap(p, AGENT_CAP_CONTENT_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		found = agent_file_find(op->payload);
		if (found < 0) {
			res->status = AGENT_STATUS_NOT_FOUND;
			agent_result_text(res, "summary_not_found");
		} else {
			res->value0 = agent_files[found].fid;
			res->value1 = agent_files[found].dependency_mask;
			res->value2 = agent_files[found].updated_tick;
			agent_result_text(res, agent_files[found].summary);
		}
		break;
	case AGENT_TOOL_READ_FILE_DIGEST:
		if (!agent_has_cap(p, AGENT_CAP_CONTENT_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_file_digest_read(op->payload, res);
		break;
	case AGENT_TOOL_DEPENDENCY_QUERY:
		if (!agent_has_cap(p, AGENT_CAP_META_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		memset(dependency_label, 0, sizeof(dependency_label));
		memset(dependency_project, 0, sizeof(dependency_project));
		memset(dependency_run_id, 0, sizeof(dependency_run_id));
		if (agent_contains(op->payload, "=") ||
		    agent_contains(op->payload, ":")) {
			if (agent_parse_selector(op->payload, dependency_label,
						 sizeof(dependency_label),
						 dependency_project,
						 sizeof(dependency_project),
						 dependency_run_id,
						 sizeof(dependency_run_id)) < 0) {
				res->status = AGENT_STATUS_BAD_PARAM;
				agent_result_text(res, "bad_selector");
				break;
			}
		} else {
			safestrcpy(dependency_label, op->payload,
				   sizeof(dependency_label));
		}
		if (agent_dependency_for_label(dependency_label,
					       dependency_project,
					       dependency_run_id, &deps) < 0) {
			res->status = AGENT_STATUS_NOT_FOUND;
			agent_result_text(res, "dependency_not_found");
			break;
		}
		res->value0 = deps;
		res->value1 = agent_mask_count(deps);
		res->value2 = agent_dependency_generation;
		agent_stage_text(deps, res->result, sizeof(res->result));
		break;
	case AGENT_TOOL_DEPENDENCY_UPDATE:
		if (!agent_has_cap(p, AGENT_CAP_DEPENDENCY_UPDATE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (agent_text_empty(op->payload)) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "selector_required");
			break;
		}
		res->status = agent_dependency_update_from_payload(op->payload,
								   res);
		if (res->status == AGENT_STATUS_BAD_PARAM)
			agent_result_text(res, "bad_selector");
		else if (res->status == AGENT_STATUS_NO_SPACE)
			agent_result_text(res, "dependency_full");
		break;
	case AGENT_TOOL_CAPABILITY_CHECK:
		res->value1 = p->agent_role;
		res->value2 = p->agent_capability_mask;
		if (agent_action_allowed(p, op->payload)) {
			res->value0 = 1;
			agent_result_text(res, "allow");
		} else {
			res->value0 = 0;
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
		}
		break;
	case AGENT_TOOL_RERUN_STAGE:
		if (!agent_has_cap(p, AGENT_CAP_ACTION_WRITE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			agent_deliver_watchers(p->pid, AGENT_EVENT_POLICY_DENIED,
					       op->request_id,
					       p->agent_call_count + 1,
					       p->agent_current_span_id,
					       "action=action_commit;compat=rerun_stage");
			break;
		}
		agent_object_state_update(p, op, res, "action_committed",
					  "action_commit", "action completed",
					  1, 0, AGENT_TOOL_ACTION_COMMIT);
		break;
	case AGENT_TOOL_WRITE_REPORT:
		if (!agent_has_cap(p, AGENT_CAP_ARTIFACT_WRITE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_object_state_update(p, op, res, "artifact_updated",
					  "artifact_update",
					  "artifact updated", 0, 1,
					  AGENT_TOOL_ARTIFACT_UPDATE);
		break;
	case AGENT_TOOL_ACTION_COMMIT:
		if (!agent_has_cap(p, AGENT_CAP_ACTION_WRITE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_object_state_update(p, op, res, "action_committed",
					  "action_commit",
					  "action completed", 1, 1, 0);
		break;
	case AGENT_TOOL_ARTIFACT_UPDATE:
		if (!agent_has_cap(p, AGENT_CAP_ARTIFACT_WRITE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		agent_object_state_update(p, op, res, "artifact_updated",
					  "artifact_update",
					  "artifact updated", 0, 1, 0);
		break;
	case AGENT_TOOL_LLM_REQUEST:
		if (!agent_has_cap(p, AGENT_CAP_MESSAGE_SEND)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (agent_text_empty(op->payload)) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "prompt_required");
			break;
		}
		delivered = 0;
		if (op->arg0) {
			delivered = agent_deliver_pid((int)op->arg0, p, p->pid,
						      AGENT_EVENT_MESSAGE,
						      op->request_id,
						      p->agent_call_count + 1,
						      p->agent_current_span_id,
						      op->payload);
			if (delivered < 0) {
				res->status = AGENT_STATUS_NO_SPACE;
				agent_result_text(res, "event_queue_full");
				break;
			}
		}
		res->value0 = op->request_id;
		res->value1 = op->arg0;
		res->value2 = delivered;
		agent_result_text(res, "llm_request");
		break;
	case AGENT_TOOL_LLM_RESPONSE:
		if (!agent_has_cap(p, AGENT_CAP_LLM_RELAY)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (op->arg0 == 0 || agent_text_empty(op->payload)) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "response_required");
			break;
		}
		delivered = agent_deliver_pid((int)op->arg0, p, p->pid,
					      AGENT_EVENT_LLM_DONE,
					      op->request_id,
					      p->agent_call_count + 1,
					      p->agent_current_span_id,
					      op->payload);
		if (delivered < 0) {
			res->status = AGENT_STATUS_NO_SPACE;
			agent_result_text(res, "event_queue_full");
			break;
		}
		res->value0 = op->request_id;
		res->value1 = op->arg0;
		res->value2 = delivered;
		agent_result_text(res, "llm_response");
		break;
	case AGENT_TOOL_AGENT_WATCH:
		if (!agent_has_cap(p, AGENT_CAP_WATCH)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (op->arg0 > AGENT_EVENT_MAX) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "bad_event_type");
			break;
		}
		if (agent_watch_set(p, op->arg0, op->payload) < 0) {
			res->status = AGENT_STATUS_NO_SPACE;
			agent_result_text(res, "watch_full");
			break;
		}
		res->value0 = op->arg0;
		agent_result_text(res, "watch");
		break;
	case AGENT_TOOL_AGENT_HEARTBEAT:
		p->heartbeat_interval = op->arg0;
		p->agent_last_heartbeat_tick = agent_ticks();
		res->value0 = p->heartbeat_interval;
		res->value1 = p->agent_last_heartbeat_tick;
		agent_result_text(res, "heartbeat");
		break;
	case AGENT_TOOL_AGENT_WAIT:
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "use_agent_wait_syscall");
		break;
	default:
		res->status = AGENT_STATUS_UNKNOWN_TOOL;
		agent_result_text(res, "unknown_tool");
		break;
	}
}

static int agent_execute_one(struct proc *p, struct agent_op *op,
			     struct agent_result *res, uint64 tick)
{
	struct agent_tool_desc *tool;

	op->payload[AGENT_OP_PAYLOAD_SIZE - 1] = 0;
	agent_result_init(res, op);
	tool = agent_tool_by_id(op->tool_id);
	if (tool && (tool->flags & AGENT_TOOL_F_SYSCALL_ONLY)) {
		res->status = AGENT_STATUS_BAD_PARAM;
		agent_result_text(res, "use_agent_wait_syscall");
		p->agent_call_count++;
		res->sequence = p->agent_call_count;
		return agent_append_context(p, op, res, tick);
	}
	p->agent_call_count++;
	res->sequence = p->agent_call_count;
	agent_execute_op(p, op, res);
	return agent_append_context(p, op, res, tick);
}

static int agent_req_has_arg0(struct agent_request *req)
{
	return req->arg0_key[0] || req->arg0_type != AGENT_PARAM_NONE;
}

static int agent_req_has_arg1(struct agent_request *req)
{
	return req->arg1_key[0] || req->arg1_type != AGENT_PARAM_NONE;
}

static int agent_req_has_payload(struct agent_request *req)
{
	return req->payload_key[0] || req->payload_type != AGENT_PARAM_NONE ||
	       req->payload[0];
}

static int agent_req_uint_arg(char *key, int type, char *want, char *err,
			      int err_n)
{
	if (strncmp(key, want, AGENT_PARAM_KEY_SIZE) != 0) {
		safestrcpy(err, "bad_arg_key", err_n);
		return -1;
	}
	if (type != AGENT_PARAM_UINT64) {
		safestrcpy(err, "bad_arg_type", err_n);
		return -1;
	}
	return 0;
}

static int agent_req_string_payload(struct agent_request *req, char *want,
				    char *err, int err_n)
{
	if (strncmp(req->payload_key, want, AGENT_PARAM_KEY_SIZE) != 0) {
		safestrcpy(err, "bad_payload_key", err_n);
		return -1;
	}
	if (req->payload_type != AGENT_PARAM_STRING) {
		safestrcpy(err, "bad_payload_type", err_n);
		return -1;
	}
	return 0;
}

static int agent_req_no_params(struct agent_request *req, char *err, int err_n)
{
	if (agent_req_has_arg0(req) || agent_req_has_arg1(req) ||
	    agent_req_has_payload(req)) {
		safestrcpy(err, "unexpected_param", err_n);
		return -1;
	}
	return 0;
}

static int agent_validate_legacy_request(struct agent_request *req,
					 int tool_id, char *err, int err_n)
{
	switch (tool_id) {
	case AGENT_TOOL_ECHO:
		if (agent_req_string_payload(req, "payload", err, err_n) < 0)
			return -1;
		if (agent_req_uint_arg(req->arg0_key, req->arg0_type, "arg0",
				       err, err_n) < 0)
			return -1;
		if (agent_req_uint_arg(req->arg1_key, req->arg1_type, "arg1",
				       err, err_n) < 0)
			return -1;
		return 0;
	case AGENT_TOOL_QUERY_PROCESS:
		if (agent_req_has_arg0(req) &&
		    agent_req_uint_arg(req->arg0_key, req->arg0_type, "type",
				       err, err_n) < 0)
			return -1;
		if (agent_req_has_arg1(req) || agent_req_has_payload(req)) {
			safestrcpy(err, "unexpected_param", err_n);
			return -1;
		}
		return 0;
	case AGENT_TOOL_QUERY_FILE:
		if (agent_req_string_payload(req, "path", err, err_n) < 0)
			return -1;
		if (agent_req_has_arg0(req) || agent_req_has_arg1(req)) {
			safestrcpy(err, "unexpected_param", err_n);
			return -1;
		}
		return 0;
	case AGENT_TOOL_SEND_MESSAGE:
		if (agent_req_uint_arg(req->arg0_key, req->arg0_type,
				       "target_pid", err, err_n) < 0)
			return -1;
		if (agent_req_string_payload(req, "message", err, err_n) < 0)
			return -1;
		if (agent_req_has_arg1(req)) {
			safestrcpy(err, "unexpected_param", err_n);
			return -1;
		}
		return 0;
	case AGENT_TOOL_READ_FILE_SUMMARY:
		return agent_req_string_payload(req, "selector", err, err_n);
	case AGENT_TOOL_READ_FILE_DIGEST:
		return agent_req_string_payload(req, "selector", err, err_n);
	case AGENT_TOOL_DEPENDENCY_QUERY:
		return agent_req_string_payload(req, "label", err, err_n);
	case AGENT_TOOL_CAPABILITY_CHECK:
		if (agent_req_uint_arg(req->arg0_key, req->arg0_type, "role",
				       err, err_n) < 0)
			return -1;
		return agent_req_string_payload(req, "action", err, err_n);
	case AGENT_TOOL_ACTION_COMMIT:
	case AGENT_TOOL_ARTIFACT_UPDATE:
		if (agent_req_has_arg0(req) &&
		    agent_req_uint_arg(req->arg0_key, req->arg0_type, "role",
				       err, err_n) < 0)
			return -1;
		return agent_req_string_payload(req, "selector", err, err_n);
	case AGENT_TOOL_LLM_REQUEST:
		if (agent_req_uint_arg(req->arg0_key, req->arg0_type,
				       "target_pid", err, err_n) < 0)
			return -1;
		return agent_req_string_payload(req, "prompt_summary", err,
						err_n);
	case AGENT_TOOL_LLM_RESPONSE:
		if (agent_req_uint_arg(req->arg0_key, req->arg0_type,
				       "target_pid", err, err_n) < 0)
			return -1;
		return agent_req_string_payload(req, "response_summary", err,
						err_n);
	case AGENT_TOOL_DEPENDENCY_UPDATE:
		return agent_req_string_payload(req, "selector", err, err_n);
	case AGENT_TOOL_RERUN_STAGE:
		if (agent_req_has_arg0(req) &&
		    agent_req_uint_arg(req->arg0_key, req->arg0_type, "role",
				       err, err_n) < 0)
			return -1;
		return agent_req_string_payload(req, "stage", err, err_n);
	case AGENT_TOOL_WRITE_REPORT:
		if (agent_req_has_arg0(req) &&
		    agent_req_uint_arg(req->arg0_key, req->arg0_type, "role",
				       err, err_n) < 0)
			return -1;
		return agent_req_string_payload(req, "payload", err, err_n);
	case AGENT_TOOL_AGENT_WATCH:
		if (agent_req_uint_arg(req->arg0_key, req->arg0_type,
				       "event_type", err, err_n) < 0)
			return -1;
		return agent_req_string_payload(req, "filter", err, err_n);
	case AGENT_TOOL_AGENT_HEARTBEAT:
		if (agent_req_uint_arg(req->arg0_key, req->arg0_type,
				       "interval", err, err_n) < 0)
			return -1;
		if (agent_req_has_arg1(req) || agent_req_has_payload(req)) {
			safestrcpy(err, "unexpected_param", err_n);
			return -1;
		}
		return 0;
	case AGENT_TOOL_PID_INFO:
	case AGENT_TOOL_CTX_STAT:
	case AGENT_TOOL_GET_SYSTEM_STATUS:
	case AGENT_TOOL_READ_CONTEXT:
	case AGENT_TOOL_READ_MESSAGE:
	case AGENT_TOOL_FILE_META_INIT:
		return agent_req_no_params(req, err, err_n);
	default:
		if (tool_id > 0 && tool_id <= AGENT_TOOL_COUNT &&
		    (agent_tools[tool_id - 1].flags & AGENT_TOOL_F_SYSCALL_ONLY)) {
			safestrcpy(err, "syscall_only", err_n);
			return -1;
		}
		safestrcpy(err, "bad_legacy_tool", err_n);
		return -1;
	}
}

int sys_agent_create(void)
{
	return agent_create_proc();
}

int sys_agent_create_role(int role)
{
	struct proc *p = curr_proc();

	if (!agent_role_valid(role))
		return AGENT_STATUS_BAD_PARAM;
	if (p->is_agent) {
		if (!agent_has_cap(p, AGENT_CAP_ORCHESTRATE))
			return AGENT_STATUS_DENIED;
	} else if (role != AGENT_ROLE_ORCHESTRATOR ||
		   !agent_plain_can_create_orchestrator(p)) {
		return -1;
	}
	return agent_create_role_proc(role);
}

int sys_agent_info(uint64 addr)
{
	struct proc *p = curr_proc();
	struct agent_info info;

	agent_info_fill(p, &info);
	return copyout(p->pagetable, addr, (char *)&info, sizeof(info));
}

static void agent_trace_from_context(struct proc *p,
				     struct agent_context_record *record,
				     struct agent_trace_record *trace)
{
	struct thread *t = curr_thread();

	memset(trace, 0, sizeof(*trace));
	trace->kind = AGENT_TRACE_KIND_CONTEXT;
	trace->tick = record->tick;
	trace->sequence = record->sequence;
	trace->cause_sequence = record->cause_sequence;
	trace->span_id = record->span_id;
	trace->value0 = record->value0;
	trace->value1 = record->value1;
	trace->value2 = record->value2;
	trace->flags = record->flags;
	trace->tool_id = record->tool_id;
	trace->status = record->status;
	trace->role = p->agent_role;
	trace->loop_state = p->loop_state;
	trace->pid = p->pid;
	trace->tid = t ? t->tid : 0;
	safestrcpy(trace->text,
		   record->result[0] ? record->result : record->payload,
		   sizeof(trace->text));
}

static void agent_trace_from_sched(struct agent_sched_record *record,
				   struct agent_trace_record *trace)
{
	memset(trace, 0, sizeof(*trace));
	trace->kind = AGENT_TRACE_KIND_SCHED;
	trace->tick = record->tick;
	trace->sequence = record->dispatch_count;
	trace->value0 = record->score;
	trace->value1 = record->event_queue_count;
	trace->value2 = record->vruntime;
	trace->flags = record->reason_flags;
	trace->role = record->role;
	trace->loop_state = record->loop_state;
	trace->pid = record->pid;
	trace->tid = record->tid;
	safestrcpy(trace->text, "sched", sizeof(trace->text));
}

int sys_agent_sched_snapshot(uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_sched_record record;
	uint64 visible;
	uint64 start;
	uint64 slot;
	int n;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	visible = p->agent_sched_trace_count;
	if (visible > AGENT_SCHED_TRACE_CAP)
		visible = AGENT_SCHED_TRACE_CAP;
	if (max == 0)
		return visible;
	if (recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	n = max < (int)visible ? max : (int)visible;
	start = p->agent_sched_trace_count > AGENT_SCHED_TRACE_CAP ?
			p->agent_sched_trace_head :
			0;
	for (int i = 0; i < n; i++) {
		slot = (start + i) % AGENT_SCHED_TRACE_CAP;
		memmove(&record, &p->agent_sched_records[slot],
			sizeof(record));
		if (copyout(p->pagetable,
			    recordsaddr +
				    i * sizeof(struct agent_sched_record),
			    (char *)&record, sizeof(record)) < 0)
			return -1;
	}
	return n;
}

int sys_agent_sched_config(uint64 configaddr)
{
	struct proc *p = curr_proc();
	struct proc *target;
	struct agent_sched_config config;
	uint64 valid_mask = AGENT_SCHED_CONFIG_POLICY |
			    AGENT_SCHED_CONFIG_WEIGHT |
			    AGENT_SCHED_CONFIG_PRIORITY |
			    AGENT_SCHED_CONFIG_BUDGET;

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (configaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	if (copyin(p->pagetable, (char *)&config, configaddr,
		   sizeof(config)) < 0)
		return -1;
	if (config.target_pid <= 0 || config.update_mask == 0 ||
	    (config.update_mask & ~valid_mask) != 0)
		return AGENT_STATUS_BAD_PARAM;
	if ((config.update_mask & AGENT_SCHED_CONFIG_POLICY) &&
	    config.policy != AGENT_SCHED_POLICY_ADAPTIVE)
		return AGENT_STATUS_BAD_PARAM;
	if ((config.update_mask & AGENT_SCHED_CONFIG_WEIGHT) &&
	    (config.weight < AGENT_SCHED_WEIGHT_MIN ||
	     config.weight > AGENT_SCHED_WEIGHT_MAX))
		return AGENT_STATUS_BAD_PARAM;
	if ((config.update_mask & AGENT_SCHED_CONFIG_PRIORITY) &&
	    (config.priority < AGENT_SCHED_PRIORITY_MIN ||
	     config.priority > AGENT_SCHED_PRIORITY_MAX))
		return AGENT_STATUS_BAD_PARAM;
	if ((config.update_mask & AGENT_SCHED_CONFIG_BUDGET) &&
	    (config.budget < AGENT_SCHED_BUDGET_MIN ||
	     config.budget > AGENT_SCHED_BUDGET_MAX))
		return AGENT_STATUS_BAD_PARAM;
	for (target = pool; target < &pool[NPROC]; target++) {
		if (target->state == P_UNUSED ||
		    target->pid != config.target_pid)
			continue;
		if (!target->is_agent)
			return AGENT_STATUS_NOT_FOUND;
		if (config.update_mask & AGENT_SCHED_CONFIG_POLICY)
			target->agent_sched_policy = config.policy;
		if (config.update_mask & AGENT_SCHED_CONFIG_WEIGHT)
			target->agent_sched_weight = config.weight;
		if (config.update_mask & AGENT_SCHED_CONFIG_PRIORITY)
			target->agent_sched_priority = config.priority;
		if (config.update_mask & AGENT_SCHED_CONFIG_BUDGET) {
			target->agent_sched_budget = config.budget;
			if (target->agent_sched_budget_used >= config.budget)
				target->agent_sched_budget_used = 0;
		}
		target->agent_sched_ready_tick = agent_ticks();
		return 0;
	}
	return AGENT_STATUS_NOT_FOUND;
}

int sys_agent_trace_snapshot(uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_context_record context_record;
	struct agent_sched_record sched_record;
	struct agent_trace_record trace;
	uint64 context_visible;
	uint64 sched_visible;
	uint64 sched_start;
	uint64 ci = 0;
	uint64 si = 0;
	uint64 total;
	uint64 seq;
	uint64 slot;
	int limit;
	int copied = 0;
	int have_context;
	int have_sched;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	context_visible = p->context_path_count;
	if (context_visible > p->context_path_capacity)
		context_visible = p->context_path_capacity;
	sched_visible = p->agent_sched_trace_count;
	if (sched_visible > AGENT_SCHED_TRACE_CAP)
		sched_visible = AGENT_SCHED_TRACE_CAP;
	total = context_visible + sched_visible;
	if (total > AGENT_TRACE_MAX_RECORDS)
		total = AGENT_TRACE_MAX_RECORDS;
	if (max == 0)
		return total;
	if (recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	limit = max < (int)total ? max : (int)total;
	sched_start = p->agent_sched_trace_count > AGENT_SCHED_TRACE_CAP ?
			      p->agent_sched_trace_head :
			      0;
	while (copied < limit &&
	       (ci < context_visible || si < sched_visible)) {
		have_context = 0;
		have_sched = 0;
		if (ci < context_visible && p->context_path_capacity > 0) {
			seq = p->context_path_oldest + ci;
			slot = (seq - 1) % p->context_path_capacity;
			if (agent_read_record(p, slot, &context_record) < 0)
				return AGENT_STATUS_NO_SPACE;
			if (context_record.sequence == seq)
				have_context = 1;
			else {
				ci++;
				continue;
			}
		}
		if (si < sched_visible) {
			slot = (sched_start + si) % AGENT_SCHED_TRACE_CAP;
			memmove(&sched_record, &p->agent_sched_records[slot],
				sizeof(sched_record));
			have_sched = 1;
		}
		if (have_context &&
		    (!have_sched || context_record.tick <= sched_record.tick)) {
			agent_trace_from_context(p, &context_record, &trace);
			ci++;
		} else if (have_sched) {
			agent_trace_from_sched(&sched_record, &trace);
			si++;
		} else {
			break;
		}
		if (copyout(p->pagetable,
			    recordsaddr +
				    copied * sizeof(struct agent_trace_record),
			    (char *)&trace, sizeof(trace)) < 0)
			return -1;
		copied++;
	}
	return copied;
}

int sys_agent_audit_snapshot(uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_audit_record record;
	uint64 visible;
	uint64 start;
	uint64 slot;
	int limit;

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	visible = agent_audit_count;
	if (visible > AGENT_AUDIT_MAX_RECORDS)
		visible = AGENT_AUDIT_MAX_RECORDS;
	if (max == 0)
		return visible;
	if (recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	limit = max < (int)visible ? max : (int)visible;
	start = agent_audit_count > AGENT_AUDIT_MAX_RECORDS ?
			agent_audit_head :
			0;
	for (int i = 0; i < limit; i++) {
		slot = (start + i) % AGENT_AUDIT_MAX_RECORDS;
		memmove(&record, &agent_audit_records[slot], sizeof(record));
		if (copyout(p->pagetable,
			    recordsaddr +
				    i * sizeof(struct agent_audit_record),
			    (char *)&record, sizeof(record)) < 0)
			return -1;
	}
	return limit;
}

int sys_agent_ledger_snapshot(uint64 summaryaddr)
{
	struct proc *p = curr_proc();
	struct agent_ledger_summary summary;
	uint64 visible;

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (summaryaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	memset(&summary, 0, sizeof(summary));
	visible = agent_audit_count;
	if (visible > AGENT_AUDIT_MAX_RECORDS)
		visible = AGENT_AUDIT_MAX_RECORDS;
	summary.version = AGENT_LEDGER_VERSION;
	summary.visible_records = visible;
	summary.total_records = agent_audit_count;
	summary.dropped_records =
		agent_audit_count > AGENT_AUDIT_MAX_RECORDS ?
			agent_audit_count - AGENT_AUDIT_MAX_RECORDS :
			0;
	if (visible > 0) {
		summary.oldest_sequence = agent_audit_next_sequence - visible;
		summary.latest_sequence = agent_audit_next_sequence - 1;
	}
	summary.ledger_hash = agent_audit_ledger_hash;
	summary.context_records = agent_audit_kind_counts[AGENT_AUDIT_KIND_CONTEXT];
	summary.event_records =
		agent_audit_kind_counts[AGENT_AUDIT_KIND_EVENT_ENQUEUE] +
		agent_audit_kind_counts[AGENT_AUDIT_KIND_EVENT_CONSUME];
	summary.sched_records = agent_audit_kind_counts[AGENT_AUDIT_KIND_SCHED];
	summary.prefetch_records =
		agent_audit_kind_counts[AGENT_AUDIT_KIND_PREFETCH];
	summary.timeline_total = agent_audit_count + agent_span_prefetch_count;
	summary.observe_epoch = agent_observe_epoch;
	return copyout(p->pagetable, summaryaddr, (char *)&summary,
		       sizeof(summary));
}

static int agent_audit_match(struct agent_audit_record *record,
			     struct agent_audit_filter *filter)
{
	uint64 flags = filter->flags;

	if ((flags & AGENT_AUDIT_FILTER_START_SEQUENCE) &&
	    record->sequence < filter->start_sequence)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_SPAN_ID) &&
	    record->span_id != filter->span_id)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_KIND) && record->kind != filter->kind)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_PID) && record->pid != filter->pid)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_SOURCE_PID) &&
	    record->source_pid != filter->source_pid)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_TARGET_PID) &&
	    record->target_pid != filter->target_pid)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_ROLE) && record->role != filter->role)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_TOOL_ID) &&
	    record->tool_id != filter->tool_id)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_EVENT_TYPE) &&
	    record->event_type != filter->event_type)
		return 0;
	if ((flags & AGENT_AUDIT_FILTER_STATUS) &&
	    record->status != filter->status)
		return 0;
	return 1;
}

int sys_agent_audit_query(uint64 filteraddr, uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_audit_filter filter;
	struct agent_audit_record record;
	uint64 visible;
	uint64 start;
	uint64 slot;
	int matched = 0;
	int copied = 0;

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	memset(&filter, 0, sizeof(filter));
	if (filteraddr &&
	    copyin(p->pagetable, (char *)&filter, filteraddr,
		   sizeof(filter)) < 0)
		return -1;
	if (max > 0 && recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	visible = agent_audit_count;
	if (visible > AGENT_AUDIT_MAX_RECORDS)
		visible = AGENT_AUDIT_MAX_RECORDS;
	start = agent_audit_count > AGENT_AUDIT_MAX_RECORDS ?
			agent_audit_head :
			0;
	for (int i = 0; i < (int)visible; i++) {
		slot = (start + i) % AGENT_AUDIT_MAX_RECORDS;
		memmove(&record, &agent_audit_records[slot], sizeof(record));
		if (!agent_audit_match(&record, &filter))
			continue;
		matched++;
		if (max == 0 || copied >= max)
			continue;
		if (copyout(p->pagetable,
			    recordsaddr +
				    copied * sizeof(struct agent_audit_record),
			    (char *)&record, sizeof(record)) < 0)
			return -1;
		copied++;
	}
	if (max == 0)
		return matched;
	return copied;
}

int sys_agent_span_trace_snapshot(uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_audit_record record;
	uint64 visible;
	uint64 start;
	uint64 slot;
	uint64 span_id;
	int matched = 0;
	int copied = 0;

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_AUDIT_WRITE))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (max > 0 && recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	span_id = p->agent_current_span_id;
	if (span_id == 0)
		return 0;
	visible = agent_audit_count;
	if (visible > AGENT_AUDIT_MAX_RECORDS)
		visible = AGENT_AUDIT_MAX_RECORDS;
	start = agent_audit_count > AGENT_AUDIT_MAX_RECORDS ?
			agent_audit_head :
			0;
	for (int i = 0; i < (int)visible; i++) {
		slot = (start + i) % AGENT_AUDIT_MAX_RECORDS;
		memmove(&record, &agent_audit_records[slot], sizeof(record));
		if (record.span_id != span_id)
			continue;
		matched++;
		if (max == 0 || copied >= max)
			continue;
		if (copyout(p->pagetable,
			    recordsaddr +
				    copied * sizeof(struct agent_audit_record),
			    (char *)&record, sizeof(record)) < 0)
			return -1;
		copied++;
	}
	if (max == 0)
		return matched;
	return copied;
}

static int agent_provenance_emit(struct proc *p, uint64 edgesaddr, int max,
				 int *matched, int *copied,
				 struct agent_provenance_edge *edge)
{
	(*matched)++;
	if (max == 0 || *copied >= max)
		return 0;
	if (copyout(p->pagetable,
		    edgesaddr + *copied * sizeof(struct agent_provenance_edge),
		    (char *)edge, sizeof(*edge)) < 0)
		return -1;
	(*copied)++;
	return 0;
}

static void agent_provenance_from_context(struct proc *p,
					  struct agent_context_record *record,
					  struct agent_provenance_edge *edge)
{
	struct thread *t = curr_thread();

	memset(edge, 0, sizeof(*edge));
	edge->kind = AGENT_PROVENANCE_EDGE_CONTEXT;
	edge->source_type = AGENT_PROVENANCE_NODE_CONTEXT;
	edge->target_type = AGENT_PROVENANCE_NODE_CONTEXT;
	edge->source_pid = p->pid;
	edge->target_pid = p->pid;
	edge->source_sequence = record->cause_sequence;
	edge->target_sequence = record->sequence;
	edge->span_id = record->span_id;
	edge->tick = record->tick;
	edge->flags = record->flags;
	edge->value0 = record->value0;
	edge->value1 = record->value1;
	edge->value2 = t ? t->tid : 0;
	edge->role = p->agent_role;
	edge->tool_id = record->tool_id;
	edge->status = record->status;
	safestrcpy(edge->text,
		   record->result[0] ? record->result : record->payload,
		   sizeof(edge->text));
}

static void agent_provenance_from_audit(struct agent_audit_record *record,
					struct agent_provenance_edge *edge)
{
	memset(edge, 0, sizeof(*edge));
	edge->kind = AGENT_PROVENANCE_EDGE_AUDIT;
	edge->source_type = AGENT_PROVENANCE_NODE_CONTEXT;
	edge->target_type = AGENT_PROVENANCE_NODE_AUDIT;
	edge->source_pid = record->source_pid ? record->source_pid :
						 record->pid;
	edge->target_pid = record->target_pid ? record->target_pid :
						 record->pid;
	edge->source_sequence = record->cause_sequence;
	edge->target_sequence = record->sequence;
	edge->span_id = record->span_id;
	edge->tick = record->tick;
	edge->flags = record->flags;
	edge->value0 = record->value0;
	edge->value1 = record->value1;
	edge->value2 = record->value2;
	edge->role = record->role;
	edge->tool_id = record->tool_id;
	edge->event_type = record->event_type;
	edge->status = record->status;
	safestrcpy(edge->text, record->text, sizeof(edge->text));
}

static void agent_provenance_from_prefetch(
	struct proc *p, struct agent_file_prefetch_hint *hint,
	struct agent_provenance_edge *edge)
{
	memset(edge, 0, sizeof(*edge));
	edge->kind = AGENT_PROVENANCE_EDGE_PREFETCH;
	edge->source_type = AGENT_PROVENANCE_NODE_CONTEXT;
	edge->target_type = AGENT_PROVENANCE_NODE_PREFETCH;
	edge->source_pid = hint->source_pid ? hint->source_pid : p->pid;
	edge->target_pid = hint->target_pid ? hint->target_pid : p->pid;
	edge->source_sequence = hint->source_sequence;
	edge->target_sequence = hint->sequence;
	edge->span_id = hint->span_id;
	edge->tick = hint->tick;
	edge->flags = hint->reason;
	edge->value0 = hint->source_fid;
	edge->value1 = hint->fid;
	edge->value2 = hint->candidate_records;
	edge->role = p->agent_role;
	edge->tool_id = AGENT_TOOL_QUERY_FILE;
	edge->status = AGENT_STATUS_OK;
	safestrcpy(edge->text,
		   hint->hit.stage[0] ? hint->hit.stage :
					hint->hit.physical_name,
		   sizeof(edge->text));
}

int sys_agent_provenance_snapshot(uint64 edgesaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_context_record context_record;
	struct agent_audit_record audit_record;
	struct agent_file_prefetch_hint hint;
	struct agent_provenance_edge edge;
	uint64 context_visible;
	uint64 audit_visible;
	uint64 prefetch_visible;
	uint64 seq;
	uint64 slot;
	uint64 start;
	uint64 span_id;
	int audit_global;
	int audit_allowed;
	int matched = 0;
	int copied = 0;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (max > 0 && edgesaddr == 0)
		return AGENT_STATUS_BAD_PARAM;

	context_visible = p->context_path_count;
	if (context_visible > p->context_path_capacity)
		context_visible = p->context_path_capacity;
	for (int i = 0; i < (int)context_visible; i++) {
		seq = p->context_path_oldest + i;
		slot = (seq - 1) % p->context_path_capacity;
		if (agent_read_record(p, slot, &context_record) < 0)
			return AGENT_STATUS_NO_SPACE;
		if (context_record.sequence != seq ||
		    context_record.cause_sequence == 0)
			continue;
		agent_provenance_from_context(p, &context_record, &edge);
		if (agent_provenance_emit(p, edgesaddr, max, &matched,
					  &copied, &edge) < 0)
			return -1;
	}

	audit_global = agent_has_cap(p, AGENT_CAP_ORCHESTRATE);
	audit_allowed = audit_global || agent_has_cap(p, AGENT_CAP_AUDIT_WRITE);
	span_id = p->agent_current_span_id;
	audit_visible = audit_allowed ? agent_audit_count : 0;
	if (audit_visible > AGENT_AUDIT_MAX_RECORDS)
		audit_visible = AGENT_AUDIT_MAX_RECORDS;
	start = agent_audit_count > AGENT_AUDIT_MAX_RECORDS ?
			agent_audit_head :
			0;
	for (int i = 0; i < (int)audit_visible; i++) {
		slot = (start + i) % AGENT_AUDIT_MAX_RECORDS;
		memmove(&audit_record, &agent_audit_records[slot],
			sizeof(audit_record));
		if (!audit_global && audit_record.span_id != span_id)
			continue;
		if (audit_record.cause_sequence == 0)
			continue;
		agent_provenance_from_audit(&audit_record, &edge);
		if (agent_provenance_emit(p, edgesaddr, max, &matched,
					  &copied, &edge) < 0)
			return -1;
	}

	prefetch_visible = p->agent_prefetch_count;
	if (prefetch_visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		prefetch_visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	start = (p->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
		 prefetch_visible) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	for (int i = 0; i < (int)prefetch_visible; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_MAX_HINTS;
		memmove(&hint, &p->agent_prefetch_hints[slot], sizeof(hint));
		if (hint.source_sequence == 0)
			continue;
		agent_provenance_from_prefetch(p, &hint, &edge);
		if (agent_provenance_emit(p, edgesaddr, max, &matched,
					  &copied, &edge) < 0)
			return -1;
	}

	if (max == 0)
		return matched;
	return copied;
}

static void agent_timeline_from_context(struct proc *p,
					struct agent_context_record *record,
					struct agent_timeline_record *timeline)
{
	struct thread *t = curr_thread();

	memset(timeline, 0, sizeof(*timeline));
	timeline->source = AGENT_TIMELINE_SOURCE_CONTEXT;
	timeline->kind = AGENT_TRACE_KIND_CONTEXT;
	timeline->tick = record->tick;
	timeline->sequence = record->sequence;
	timeline->cause_sequence = record->cause_sequence;
	timeline->span_id = record->span_id;
	timeline->value0 = record->value0;
	timeline->value1 = record->value1;
	timeline->value2 = record->value2;
	timeline->flags = record->flags;
	timeline->pid = p->pid;
	timeline->source_pid = p->pid;
	timeline->target_pid = p->pid;
	timeline->role = p->agent_role;
	timeline->loop_state = p->loop_state;
	timeline->tool_id = record->tool_id;
	timeline->status = record->status;
	timeline->tid = t ? t->tid : 0;
	safestrcpy(timeline->text,
		   record->result[0] ? record->result : record->payload,
		   sizeof(timeline->text));
}

static void agent_timeline_from_sched(struct agent_sched_record *record,
				      struct agent_timeline_record *timeline)
{
	memset(timeline, 0, sizeof(*timeline));
	timeline->source = AGENT_TIMELINE_SOURCE_SCHED;
	timeline->kind = AGENT_TRACE_KIND_SCHED;
	timeline->tick = record->tick;
	timeline->sequence = record->dispatch_count;
	timeline->value0 = record->score;
	timeline->value1 = record->event_queue_count;
	timeline->value2 = record->vruntime;
	timeline->flags = record->reason_flags;
	timeline->pid = record->pid;
	timeline->tid = record->tid;
	timeline->source_pid = record->pid;
	timeline->target_pid = record->pid;
	timeline->role = record->role;
	timeline->loop_state = record->loop_state;
	timeline->status = AGENT_STATUS_OK;
	safestrcpy(timeline->text, "sched", sizeof(timeline->text));
}

static void agent_timeline_from_audit(struct agent_audit_record *record,
				      struct agent_timeline_record *timeline)
{
	memset(timeline, 0, sizeof(*timeline));
	timeline->source = AGENT_TIMELINE_SOURCE_AUDIT;
	timeline->kind = record->kind;
	timeline->tick = record->tick;
	timeline->sequence = record->sequence;
	timeline->cause_sequence = record->cause_sequence;
	timeline->span_id = record->span_id;
	timeline->value0 = record->value0;
	timeline->value1 = record->value1;
	timeline->value2 = record->value2;
	timeline->flags = record->flags;
	timeline->pid = record->pid;
	timeline->source_pid = record->source_pid;
	timeline->target_pid = record->target_pid;
	timeline->role = record->role;
	timeline->loop_state = record->loop_state;
	timeline->tool_id = record->tool_id;
	timeline->event_type = record->event_type;
	timeline->status = record->status;
	safestrcpy(timeline->text, record->text, sizeof(timeline->text));
}

static void agent_timeline_from_prefetch(struct proc *p,
					 struct agent_file_prefetch_hint *hint,
					 struct agent_timeline_record *timeline)
{
	memset(timeline, 0, sizeof(*timeline));
	timeline->source = AGENT_TIMELINE_SOURCE_PREFETCH;
	timeline->kind = hint->plan;
	timeline->tick = hint->tick;
	timeline->sequence = hint->sequence;
	timeline->cause_sequence = hint->source_sequence;
	timeline->span_id = hint->span_id;
	timeline->value0 = hint->fid;
	timeline->value1 = hint->source_fid;
	timeline->value2 = hint->candidate_records;
	timeline->flags = hint->reason;
	timeline->pid = p->pid;
	timeline->source_pid = hint->source_pid;
	timeline->target_pid = hint->target_pid;
	timeline->role = p->agent_role;
	timeline->loop_state = p->loop_state;
	timeline->tool_id = AGENT_TOOL_QUERY_FILE;
	timeline->status = AGENT_STATUS_OK;
	safestrcpy(timeline->text,
		   hint->hit.stage[0] ? hint->hit.stage : hint->hit.physical_name,
		   sizeof(timeline->text));
}

static int agent_timeline_load_context(struct proc *p, uint64 *cursor,
				       uint64 visible,
				       struct agent_timeline_record *timeline)
{
	struct agent_context_record record;
	uint64 seq;
	uint64 slot;

	while (*cursor < visible && p->context_path_capacity > 0) {
		seq = p->context_path_oldest + *cursor;
		(*cursor)++;
		slot = (seq - 1) % p->context_path_capacity;
		if (agent_read_record(p, slot, &record) < 0)
			return -1;
		if (record.sequence != seq)
			continue;
		agent_timeline_from_context(p, &record, timeline);
		return 1;
	}
	return 0;
}

static int agent_timeline_load_sched(struct proc *p, uint64 *cursor,
				     uint64 visible, uint64 start,
				     struct agent_timeline_record *timeline)
{
	struct agent_sched_record record;
	uint64 slot;

	if (*cursor >= visible)
		return 0;
	slot = (start + *cursor) % AGENT_SCHED_TRACE_CAP;
	(*cursor)++;
	memmove(&record, &p->agent_sched_records[slot], sizeof(record));
	agent_timeline_from_sched(&record, timeline);
	return 1;
}

static int agent_timeline_load_audit(char *used, uint64 visible,
				     uint64 start, int global,
				     uint64 span_id,
				     struct agent_timeline_record *timeline)
{
	struct agent_audit_record record;
	struct agent_audit_record best_record;
	uint64 slot;
	uint64 best_tick = (uint64)-1;
	uint64 best_sequence = (uint64)-1;
	int best_index = -1;

	for (int i = 0; i < (int)visible; i++) {
		if (used[i])
			continue;
		slot = (start + i) % AGENT_AUDIT_MAX_RECORDS;
		memmove(&record, &agent_audit_records[slot], sizeof(record));
		if (!global && record.span_id != span_id)
			continue;
		if (best_index < 0 || record.tick < best_tick ||
		    (record.tick == best_tick &&
		     record.sequence < best_sequence)) {
			best_index = i;
			best_tick = record.tick;
			best_sequence = record.sequence;
			memmove(&best_record, &record, sizeof(best_record));
		}
	}
	if (best_index >= 0) {
		used[best_index] = 1;
		agent_timeline_from_audit(&best_record, timeline);
		return 1;
	}
	return 0;
}

static int agent_timeline_load_prefetch(struct proc *p, uint64 *cursor,
					uint64 visible, uint64 start,
					struct agent_timeline_record *timeline)
{
	struct agent_file_prefetch_hint hint;
	uint64 slot;

	if (*cursor >= visible)
		return 0;
	slot = (start + *cursor) % AGENT_FILE_PREFETCH_MAX_HINTS;
	(*cursor)++;
	memmove(&hint, &p->agent_prefetch_hints[slot], sizeof(hint));
	agent_timeline_from_prefetch(p, &hint, timeline);
	return 1;
}

static int agent_timeline_audit_visible(int global, uint64 span_id)
{
	struct agent_audit_record record;
	uint64 visible;
	uint64 start;
	uint64 slot;
	int matched = 0;

	visible = agent_audit_count;
	if (visible > AGENT_AUDIT_MAX_RECORDS)
		visible = AGENT_AUDIT_MAX_RECORDS;
	if (global)
		return (int)visible;
	if (span_id == 0)
		return 0;
	start = agent_audit_count > AGENT_AUDIT_MAX_RECORDS ?
			agent_audit_head :
			0;
	for (int i = 0; i < (int)visible; i++) {
		slot = (start + i) % AGENT_AUDIT_MAX_RECORDS;
		memmove(&record, &agent_audit_records[slot], sizeof(record));
		if (record.span_id == span_id)
			matched++;
	}
	return matched;
}

static int agent_timeline_source_enabled(struct agent_timeline_filter *filter,
					 int source)
{
	if (filter == 0 || (filter->flags & AGENT_TIMELINE_FILTER_SOURCE_MASK) == 0)
		return 1;
	if (source <= 0 || source >= 64)
		return 0;
	return (filter->source_mask & (1ULL << source)) != 0;
}

static int agent_timeline_after_cursor(struct agent_timeline_filter *filter,
				       struct agent_timeline_record *record)
{
	if (record->tick > filter->after_tick)
		return 1;
	if (record->tick < filter->after_tick)
		return 0;
	if (record->source > filter->after_source)
		return 1;
	if (record->source < filter->after_source)
		return 0;
	return record->sequence > filter->after_sequence;
}

static int agent_timeline_match(struct agent_timeline_filter *filter,
				struct agent_timeline_record *record)
{
	if (filter == 0 || filter->flags == 0)
		return 1;
	if (!agent_timeline_source_enabled(filter, record->source))
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_START_TICK) &&
	    record->tick < filter->start_tick)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_AFTER_CURSOR) &&
	    !agent_timeline_after_cursor(filter, record))
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_SPAN_ID) &&
	    record->span_id != filter->span_id)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_KIND) &&
	    record->kind != filter->kind)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_PID) &&
	    record->pid != filter->pid)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_SOURCE_PID) &&
	    record->source_pid != filter->source_pid)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_TARGET_PID) &&
	    record->target_pid != filter->target_pid)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_ROLE) &&
	    record->role != filter->role)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_TOOL_ID) &&
	    record->tool_id != filter->tool_id)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_EVENT_TYPE) &&
	    record->event_type != filter->event_type)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_STATUS) &&
	    record->status != filter->status)
		return 0;
	if ((filter->flags & AGENT_TIMELINE_FILTER_FLAGS_ALL) &&
	    (record->flags & filter->require_flags) != filter->require_flags)
		return 0;
	return 1;
}

static int agent_observe_record_visible(struct proc *p,
					struct agent_timeline_record *record)
{
	if (p == 0 || record == 0 || !p->is_agent)
		return 0;
	if (record->source == AGENT_TIMELINE_SOURCE_AUDIT) {
		if (agent_has_cap(p, AGENT_CAP_ORCHESTRATE))
			return 1;
		return agent_has_cap(p, AGENT_CAP_AUDIT_WRITE) &&
		       record->span_id != 0 &&
		       record->span_id == p->agent_current_span_id;
	}
	if (record->source == AGENT_TIMELINE_SOURCE_CONTEXT ||
	    record->source == AGENT_TIMELINE_SOURCE_SCHED)
		return record->pid == p->pid;
	if (record->source == AGENT_TIMELINE_SOURCE_PREFETCH)
		return agent_has_cap(p, AGENT_CAP_META_READ) &&
		       record->target_pid == p->pid;
	return 0;
}

static void agent_observe_record(struct agent_timeline_record *record)
{
	if (record == 0)
		return;
	agent_observe_epoch++;
	if (agent_timeline_waiting_agents <= 0)
		return;
	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (p->state == P_UNUSED || !p->is_agent ||
		    !p->agent_timeline_waiting)
			continue;
		if (!agent_observe_record_visible(p, record))
			continue;
		if (!agent_timeline_match(&p->agent_timeline_wait_filter,
					  record))
			continue;
		p->agent_observe_epoch = agent_observe_epoch;
		p->agent_timeline_wait_wakeup_count++;
		agent_wake_timeline_waiters(p);
	}
}

static int agent_timeline_export(struct proc *p,
				 struct agent_timeline_filter *filter,
				 uint64 recordsaddr, int max)
{
	struct agent_timeline_record context_timeline;
	struct agent_timeline_record sched_timeline;
	struct agent_timeline_record audit_timeline;
	struct agent_timeline_record prefetch_timeline;
	struct agent_timeline_record *selected;
	uint64 context_visible;
	uint64 sched_visible;
	uint64 audit_visible;
	uint64 audit_ring_visible;
	uint64 prefetch_visible;
	uint64 sched_start;
	uint64 audit_start;
	uint64 prefetch_start;
	uint64 ci = 0;
	uint64 si = 0;
	uint64 pi = 0;
	uint64 best_tick;
	uint64 span_id;
	char audit_used[AGENT_AUDIT_MAX_RECORDS];
	int audit_global;
	int audit_allowed;
	int have_context;
	int have_sched;
	int have_audit;
	int have_prefetch;
	int copied = 0;
	int matched = 0;
	int total;
	int pick;

	context_visible = agent_timeline_source_enabled(
				  filter, AGENT_TIMELINE_SOURCE_CONTEXT) ?
				  p->context_path_count :
				  0;
	if (context_visible > p->context_path_capacity)
		context_visible = p->context_path_capacity;
	sched_visible = agent_timeline_source_enabled(
				filter, AGENT_TIMELINE_SOURCE_SCHED) ?
				p->agent_sched_trace_count :
				0;
	if (sched_visible > AGENT_SCHED_TRACE_CAP)
		sched_visible = AGENT_SCHED_TRACE_CAP;
	prefetch_visible = agent_timeline_source_enabled(
				   filter, AGENT_TIMELINE_SOURCE_PREFETCH) &&
				   agent_has_cap(p, AGENT_CAP_META_READ) ?
				   p->agent_prefetch_count :
				   0;
	if (prefetch_visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		prefetch_visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	audit_global = agent_has_cap(p, AGENT_CAP_ORCHESTRATE);
	audit_allowed = agent_timeline_source_enabled(
				filter, AGENT_TIMELINE_SOURCE_AUDIT) &&
				(audit_global ||
				 agent_has_cap(p, AGENT_CAP_AUDIT_WRITE));
	span_id = p->agent_current_span_id;
	audit_visible = audit_allowed ?
				agent_timeline_audit_visible(audit_global, span_id) :
				0;
	audit_ring_visible = audit_allowed ? agent_audit_count : 0;
	if (audit_ring_visible > AGENT_AUDIT_MAX_RECORDS)
		audit_ring_visible = AGENT_AUDIT_MAX_RECORDS;
	total = (int)(context_visible + sched_visible + prefetch_visible +
		      audit_visible);
	if (max == 0 && (filter == 0 || filter->flags == 0))
		return total;

	sched_start = p->agent_sched_trace_count > AGENT_SCHED_TRACE_CAP ?
			      p->agent_sched_trace_head :
			      0;
	audit_start = agent_audit_count > AGENT_AUDIT_MAX_RECORDS ?
			      agent_audit_head :
			      0;
	memset(audit_used, 0, sizeof(audit_used));
	prefetch_start =
		(p->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
		 prefetch_visible) %
		AGENT_FILE_PREFETCH_MAX_HINTS;

	have_context = agent_timeline_load_context(
		p, &ci, context_visible, &context_timeline);
	if (have_context < 0)
		return AGENT_STATUS_NO_SPACE;
	have_sched = agent_timeline_load_sched(p, &si, sched_visible,
					       sched_start, &sched_timeline);
	have_audit = audit_allowed ?
			     agent_timeline_load_audit(audit_used,
						       audit_ring_visible,
						       audit_start, audit_global,
						       span_id, &audit_timeline) :
			     0;
	have_prefetch = agent_timeline_load_prefetch(
		p, &pi, prefetch_visible, prefetch_start, &prefetch_timeline);

	while ((max == 0 || copied < max) &&
	       (have_context || have_sched || have_audit || have_prefetch)) {
		best_tick = (uint64)-1;
		pick = 0;
		selected = 0;
		if (have_context && context_timeline.tick <= best_tick) {
			best_tick = context_timeline.tick;
			selected = &context_timeline;
			pick = AGENT_TIMELINE_SOURCE_CONTEXT;
		}
		if (have_sched && sched_timeline.tick < best_tick) {
			best_tick = sched_timeline.tick;
			selected = &sched_timeline;
			pick = AGENT_TIMELINE_SOURCE_SCHED;
		}
		if (have_audit && audit_timeline.tick < best_tick) {
			best_tick = audit_timeline.tick;
			selected = &audit_timeline;
			pick = AGENT_TIMELINE_SOURCE_AUDIT;
		}
		if (have_prefetch && prefetch_timeline.tick < best_tick) {
			selected = &prefetch_timeline;
			pick = AGENT_TIMELINE_SOURCE_PREFETCH;
		}
		if (selected == 0)
			break;
		if (agent_timeline_match(filter, selected)) {
			matched++;
			if (max > 0) {
				if (copyout(p->pagetable,
					    recordsaddr +
						    copied *
							    sizeof(struct agent_timeline_record),
					    (char *)selected,
					    sizeof(*selected)) < 0)
					return -1;
				copied++;
			}
		}
		if (pick == AGENT_TIMELINE_SOURCE_CONTEXT) {
			have_context = agent_timeline_load_context(
				p, &ci, context_visible, &context_timeline);
			if (have_context < 0)
				return AGENT_STATUS_NO_SPACE;
		} else if (pick == AGENT_TIMELINE_SOURCE_SCHED) {
			have_sched = agent_timeline_load_sched(
				p, &si, sched_visible, sched_start,
				&sched_timeline);
		} else if (pick == AGENT_TIMELINE_SOURCE_AUDIT) {
			have_audit = agent_timeline_load_audit(
				audit_used, audit_ring_visible,
				audit_start, audit_global, span_id,
				&audit_timeline);
		} else if (pick == AGENT_TIMELINE_SOURCE_PREFETCH) {
			have_prefetch = agent_timeline_load_prefetch(
				p, &pi, prefetch_visible, prefetch_start,
				&prefetch_timeline);
		}
	}
	return max == 0 ? matched : copied;
}

int sys_agent_timeline_snapshot(uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (max > 0 && recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	return agent_timeline_export(p, 0, recordsaddr, max);
}

int sys_agent_timeline_query(uint64 filteraddr, uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_timeline_filter filter;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	if (max > 0 && recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	memset(&filter, 0, sizeof(filter));
	if (filteraddr != 0 &&
	    copyin(p->pagetable, (char *)&filter, filteraddr,
		   sizeof(filter)) < 0)
		return -1;
	if ((filter.flags & ~AGENT_TIMELINE_FILTER_ALL_FLAGS) != 0)
		return AGENT_STATUS_BAD_PARAM;
	return agent_timeline_export(p, &filter, recordsaddr, max);
}

static int agent_timeline_wait_for_match(struct proc *p,
					 struct agent_timeline_filter *filter,
					 int timeout_ticks)
{
	uint64 start;
	uint64 now;
	int matched;

	start = agent_ticks();
	p->agent_timeline_wait_count++;
	for (;;) {
		matched = agent_timeline_export(p, filter, 0, 0);
		if (matched < 0)
			return matched;
		if (matched > 0) {
			agent_timeline_waiting_set(p, 0);
			p->agent_timeline_wait_deadline_valid = 0;
			memset(&p->agent_timeline_wait_filter, 0,
			       sizeof(p->agent_timeline_wait_filter));
			p->agent_observe_epoch = agent_observe_epoch;
			p->loop_state = AGENT_LOOP_IDLE;
			return matched;
		}
		now = agent_ticks();
		if (timeout_ticks >= 0 &&
		    now - start >= (uint64)timeout_ticks) {
			agent_timeline_waiting_set(p, 0);
			p->agent_timeline_wait_deadline_valid = 0;
			memset(&p->agent_timeline_wait_filter, 0,
			       sizeof(p->agent_timeline_wait_filter));
			p->agent_timeline_wait_timeout_count++;
			p->loop_state = AGENT_LOOP_IDLE;
			return AGENT_STATUS_TIMEOUT;
		}
		p->loop_state = AGENT_LOOP_WAITING;
		agent_timeline_waiting_set(p, 1);
		memmove(&p->agent_timeline_wait_filter, filter,
			sizeof(p->agent_timeline_wait_filter));
		p->agent_observe_epoch = agent_observe_epoch;
		if (timeout_ticks >= 0) {
			p->agent_timeline_wait_deadline_valid = 1;
			p->agent_timeline_wait_deadline = start + timeout_ticks;
		} else {
			p->agent_timeline_wait_deadline_valid = 0;
			p->agent_timeline_wait_deadline = 0;
		}
		p->agent_timeline_wait_sleep_count++;
		if (wait_queue_sleep(&p->agent_timeline_waiters) < 0) {
			agent_timeline_waiting_set(p, 0);
			p->agent_timeline_wait_deadline_valid = 0;
			p->loop_state = AGENT_LOOP_IDLE;
			return -1;
		}
	}
}

static int agent_timeline_copy_filter(struct proc *p, uint64 filteraddr,
				      struct agent_timeline_filter *filter)
{
	memset(filter, 0, sizeof(*filter));
	if (filteraddr != 0 &&
	    copyin(p->pagetable, (char *)filter, filteraddr,
		   sizeof(*filter)) < 0)
		return -1;
	if ((filter->flags & ~AGENT_TIMELINE_FILTER_ALL_FLAGS) != 0)
		return AGENT_STATUS_BAD_PARAM;
	return 0;
}

int sys_agent_timeline_wait(uint64 filteraddr, int timeout_ticks)
{
	struct proc *p = curr_proc();
	struct agent_timeline_filter filter;
	int rc;

	if (!p->is_agent)
		return -1;
	if (timeout_ticks < -1)
		return AGENT_STATUS_BAD_PARAM;
	rc = agent_timeline_copy_filter(p, filteraddr, &filter);
	if (rc < 0)
		return rc;
	return agent_timeline_wait_for_match(p, &filter, timeout_ticks);
}

int sys_agent_timeline_read(uint64 filteraddr, uint64 recordsaddr, int max,
			    int timeout_ticks)
{
	struct proc *p = curr_proc();
	struct agent_timeline_filter filter;
	uint64 bytes;
	int matched;
	int rc;

	if (!p->is_agent)
		return -1;
	if (max < 0 || max > AGENT_TIMELINE_MAX_RECORDS)
		return AGENT_STATUS_BAD_PARAM;
	if (timeout_ticks < -1)
		return AGENT_STATUS_BAD_PARAM;
	if (max > 0 && recordsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	bytes = (uint64)max * sizeof(struct agent_timeline_record);
	if (max > 0 &&
	    user_range_check(p->pagetable, recordsaddr, bytes, PTE_W) < 0)
		return -1;
	rc = agent_timeline_copy_filter(p, filteraddr, &filter);
	if (rc < 0)
		return rc;
	matched = agent_timeline_wait_for_match(p, &filter, timeout_ticks);
	if (matched <= 0 || max == 0)
		return matched;
	return agent_timeline_export(p, &filter, recordsaddr, max);
}

int sys_agent_run(uint64 opsaddr, uint64 resultsaddr, int count, uint64 flags)
{
	struct proc *p = curr_proc();
	struct agent_op op;
	struct agent_result res;
	uint64 tick;

	(void)flags;
	if (!p->is_agent)
		return -1;
	if (count < 0 || count > AGENT_BATCH_MAX)
		return -1;
	if (count == 0)
		return 0;
	if (user_range_check(p->pagetable, opsaddr,
			     (uint64)count * sizeof(struct agent_op), PTE_R) < 0)
		return -1;
	if (user_range_check(p->pagetable, resultsaddr,
			     (uint64)count * sizeof(struct agent_result),
			     PTE_W) < 0)
		return -1;
	tick = agent_ticks();
	p->loop_state = AGENT_LOOP_RUNNING;
	for (int i = 0; i < count; i++) {
		if (copyin(p->pagetable, (char *)&op,
			   opsaddr + i * sizeof(struct agent_op),
			   sizeof(op)) < 0) {
			p->loop_state = AGENT_LOOP_IDLE;
			return -1;
		}
		if (agent_execute_one(p, &op, &res, tick) < 0 ||
		    copyout(p->pagetable,
			    resultsaddr + i * sizeof(struct agent_result),
			    (char *)&res, sizeof(res)) < 0) {
			p->loop_state = AGENT_LOOP_IDLE;
			return -1;
		}
	}
	p->loop_state = AGENT_LOOP_IDLE;
	if (agent_write_header(p) < 0)
		return -1;
	return count;
}

int sys_agent_tool_list(uint64 addr, int max)
{
	struct proc *p = curr_proc();
	int n = max;

	if (max < 0)
		return -1;
	if (n > AGENT_TOOL_COUNT)
		n = AGENT_TOOL_COUNT;
	if (n > 0 &&
	    copyout(p->pagetable, addr, (char *)agent_tools,
		    n * sizeof(struct agent_tool_desc)) < 0)
		return -1;
	return AGENT_TOOL_COUNT;
}

int sys_context_push(uint64 recordaddr)
{
	struct proc *p = curr_proc();
	struct agent_context_record record;
	struct agent_op op;
	struct agent_result latest;

	if (!p->is_agent)
		return -1;
	if (copyin(p->pagetable, (char *)&record, recordaddr,
		   sizeof(record)) < 0)
		return -1;
	record.payload[sizeof(record.payload) - 1] = 0;
	record.result[sizeof(record.result) - 1] = 0;
	memset(&op, 0, sizeof(op));
	op.version = AGENT_CALL_VERSION;
	op.tool_id = record.tool_id;
	op.request_id = record.request_id;
	op.arg0 = record.arg0;
	safestrcpy(op.payload, record.payload, sizeof(op.payload));
	if (record.span_id)
		p->agent_current_span_id = record.span_id;
	if (record.cause_sequence)
		p->agent_current_cause_sequence = record.cause_sequence;
	p->agent_call_count++;
	memset(&latest, 0, sizeof(latest));
	latest.version = AGENT_CALL_VERSION;
	latest.status = record.status;
	latest.tool_id = record.tool_id;
	latest.request_id = record.request_id;
	latest.sequence = p->agent_call_count;
	latest.value0 = record.value0;
	latest.value1 = record.value1;
	latest.value2 = record.value2;
	agent_result_text(&latest, record.result[0] ? record.result : "manual");
	if (agent_append_context_flags(p, &op, &latest, agent_ticks(),
				       AGENT_CONTEXT_RECORD_F_MANUAL) < 0)
		return AGENT_STATUS_NO_SPACE;
	if (agent_write_header(p) < 0)
		return AGENT_STATUS_NO_SPACE;
	return 0;
}

int sys_context_query(uint64 start_sequence, uint64 outaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_context_record record;
	uint64 seq;
	uint64 slot;
	int copied = 0;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return -1;
	if (max == 0 || p->context_path_count == 0)
		return 0;
	seq = (start_sequence == 0 ||
	       start_sequence < p->context_path_oldest) ?
		      p->context_path_oldest :
		      start_sequence;
	if (seq > p->context_path_latest)
		return 0;
	while (seq <= p->context_path_latest && copied < max) {
		slot = (seq - 1) % p->context_path_capacity;
		if (agent_read_record(p, slot, &record) < 0)
			return AGENT_STATUS_NO_SPACE;
		if (record.sequence == seq) {
			if (copyout(p->pagetable,
				    outaddr +
					    copied *
						    sizeof(struct agent_context_record),
				    (char *)&record, sizeof(record)) < 0)
				return -1;
			copied++;
		}
		seq++;
	}
	return copied;
}

int sys_context_snapshot(uint64 headeraddr, uint64 recordsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_context_header *header;

	if (!p->is_agent)
		return -1;
	if (max < 0)
		return -1;
	if (agent_write_header(p) < 0 || agent_sync_context_all(p) < 0)
		return AGENT_STATUS_NO_SPACE;
	header = agent_header_ptr(p);
	if (header == 0)
		return AGENT_STATUS_NO_SPACE;
	if (headeraddr &&
	    copyout(p->pagetable, headeraddr, (char *)header,
		    sizeof(*header)) < 0)
		return -1;
	if (max == 0 || recordsaddr == 0)
		return 0;
	return sys_context_query(0, recordsaddr, max);
}

int sys_context_detail(uint64 sequence, uint64 detailaddr)
{
	struct proc *p = curr_proc();
	struct agent_context_detail detail;
	uint64 slot;

	if (!p->is_agent)
		return -1;
	if (detailaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	if (p->context_path_count == 0 ||
	    sequence < p->context_path_oldest ||
	    sequence > p->context_path_latest)
		return AGENT_STATUS_NOT_FOUND;
	slot = (sequence - 1) % p->context_path_capacity;
	if (p->agent_details[slot].sequence != sequence)
		return AGENT_STATUS_NOT_FOUND;
	memmove(&detail, &p->agent_details[slot], sizeof(detail));
	if (copyout(p->pagetable, detailaddr, (char *)&detail,
		    sizeof(detail)) < 0)
		return -1;
	return 0;
}

int sys_context_rollback(uint64 sequence)
{
	struct proc *p = curr_proc();
	struct agent_context_record record;
	struct agent_result latest;
	uint64 slot;

	if (!p->is_agent)
		return -1;
	if (p->context_path_count == 0 ||
	    sequence < p->context_path_oldest ||
	    sequence > p->context_path_latest)
		return AGENT_STATUS_NOT_FOUND;
	slot = (sequence - 1) % p->context_path_capacity;
	if (agent_read_record(p, slot, &record) < 0)
		return AGENT_STATUS_NO_SPACE;
	if (record.sequence != sequence)
		return AGENT_STATUS_NOT_FOUND;
	p->agent_call_count = sequence;
	p->context_path_latest = sequence;
	p->context_path_count = sequence - p->context_path_oldest + 1;
	p->context_path_head = sequence % p->context_path_capacity;
	p->context_path_rollback_count++;
	memset(&latest, 0, sizeof(latest));
	latest.version = AGENT_CALL_VERSION;
	latest.status = record.status;
	latest.tool_id = record.tool_id;
	latest.request_id = record.request_id;
	latest.sequence = record.sequence;
	latest.value0 = record.value0;
	latest.value1 = record.value1;
	latest.value2 = record.value2;
	agent_result_text(&latest, "rollback");
	p->agent_current_cause_sequence = record.sequence;
	p->agent_context_chain_hash = record.record_hash;
	if (record.span_id)
		p->agent_current_span_id = record.span_id;
	if (agent_write_latest(p, &latest) < 0 || agent_write_header(p) < 0)
		return AGENT_STATUS_NO_SPACE;
	return 0;
}

int sys_context_clear(void)
{
	struct proc *p = curr_proc();

	if (!p->is_agent)
		return -1;
	p->agent_call_count = 0;
	p->context_path_count = 0;
	p->context_path_head = 0;
	p->context_path_oldest = 0;
	p->context_path_latest = 0;
	p->context_path_dropped = 0;
	p->context_path_rollback_count = 0;
	p->agent_detail_count = 0;
	p->agent_detail_head = 0;
	p->agent_current_span_id = ((uint64)p->agent_id << 32) | 1;
	p->agent_current_cause_sequence = 0;
	p->agent_context_chain_hash = 0;
	p->agent_provenance_edges = 0;
	memset(p->agent_details, 0, sizeof(p->agent_details));
	for (int i = 1; i < AGENT_CONTEXT_PAGES; i++)
		if (p->agent_shadow_kva[i])
			memset((void *)p->agent_shadow_kva[i], 0, PAGE_SIZE);
	if (agent_write_latest(p, 0) < 0 || agent_write_header(p) < 0 ||
	    agent_sync_context_all(p) < 0)
		return AGENT_STATUS_NO_SPACE;
	return 0;
}

int sys_agent_watch(int event_type, uint64 filteraddr)
{
	struct proc *p = curr_proc();
	char filter[AGENT_WATCH_FILTER_SIZE];

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_WATCH))
		return AGENT_STATUS_DENIED;
	if (event_type < AGENT_EVENT_NONE ||
	    event_type > AGENT_EVENT_MAX)
		return AGENT_STATUS_BAD_PARAM;
	memset(filter, 0, sizeof(filter));
	if (filteraddr != 0 &&
	    copyinstr(p->pagetable, filter, filteraddr, sizeof(filter)) < 0)
		return -1;
	if (agent_watch_set(p, event_type, filter) < 0)
		return AGENT_STATUS_NO_SPACE;
	p->loop_state = AGENT_LOOP_IDLE;
	agent_append_system_context(p, AGENT_TOOL_AGENT_WATCH, 0, event_type,
				    filter, "watch", AGENT_STATUS_OK,
				    event_type, 0, 0);
	return 0;
}

int sys_agent_unwatch(int event_type, uint64 filteraddr)
{
	struct proc *p = curr_proc();
	char filter[AGENT_WATCH_FILTER_SIZE];
	int removed;

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_WATCH))
		return AGENT_STATUS_DENIED;
	if (event_type < AGENT_EVENT_NONE ||
	    event_type > AGENT_EVENT_MAX)
		return AGENT_STATUS_BAD_PARAM;
	memset(filter, 0, sizeof(filter));
	if (filteraddr != 0 &&
	    copyinstr(p->pagetable, filter, filteraddr, sizeof(filter)) < 0)
		return -1;
	removed = agent_watch_clear(p, event_type, filter);
	agent_append_system_context(p, AGENT_TOOL_AGENT_WATCH, 0, event_type,
				    filter, "unwatch", AGENT_STATUS_OK,
				    removed, 0, 0);
	return removed;
}

int sys_agent_wait(uint64 eventaddr, int timeout_ticks)
{
	struct proc *p = curr_proc();
	struct agent_event event;
	uint64 start = agent_ticks();
	uint64 now;
	int status;

	if (!p->is_agent)
		return -1;
	if (eventaddr &&
	    user_range_check(p->pagetable, eventaddr, sizeof(event), PTE_W) < 0)
		return -1;
	memset(&event, 0, sizeof(event));
	p->agent_wait_count++;
	for (;;) {
		p->agent_wait_loop_count++;
		if (agent_wait_take_cancel(p, &event)) {
			status = AGENT_STATUS_CANCELLED;
			break;
		}
		if (agent_event_dequeue(p, &event)) {
			p->loop_state = AGENT_LOOP_RUNNING;
			p->agent_wait_deadline_valid = 0;
			status = AGENT_STATUS_OK;
			break;
		}
		now = agent_ticks();
		if (p->heartbeat_interval > 0 &&
		    now - p->agent_last_heartbeat_tick >=
			    (uint64)p->heartbeat_interval) {
			p->agent_last_heartbeat_tick = now;
			agent_queue_event(p, 0, AGENT_EVENT_TIMER, now,
					  p->context_path_latest,
					  p->agent_current_span_id,
					  "timer=heartbeat");
			continue;
		}
		if (timeout_ticks >= 0 && now - start >= (uint64)timeout_ticks) {
			p->agent_timeout_count++;
			p->loop_state = AGENT_LOOP_IDLE;
			p->agent_wait_deadline_valid = 0;
			event.type = AGENT_EVENT_TIMER;
			event.target_pid = p->pid;
			event.status = AGENT_STATUS_TIMEOUT;
			event.tick = now;
			safestrcpy(event.payload, "timeout",
				   sizeof(event.payload));
			status = AGENT_STATUS_TIMEOUT;
			break;
		}
		p->loop_state = AGENT_LOOP_WAITING;
		if (timeout_ticks >= 0) {
			p->agent_wait_deadline_valid = 1;
			p->agent_wait_deadline = start + timeout_ticks;
		} else {
			p->agent_wait_deadline_valid = 0;
			p->agent_wait_deadline = 0;
		}
		p->agent_wait_sleep_count++;
		if (wait_queue_sleep(&p->agent_event_waiters) < 0) {
			p->agent_wait_deadline_valid = 0;
			status = -1;
			break;
		}
	}
	if (eventaddr &&
	    copyout(p->pagetable, eventaddr, (char *)&event,
		    sizeof(event)) < 0)
		return -1;
	if (status == AGENT_STATUS_OK || status == AGENT_STATUS_CANCELLED) {
		if (event.span_id)
			p->agent_current_span_id = event.span_id;
		p->agent_current_cause_sequence = event.cause_sequence;
		agent_audit_event(AGENT_AUDIT_KIND_EVENT_CONSUME, p, &event);
		agent_append_system_context(p, AGENT_TOOL_AGENT_WAIT,
					    event.event_id, event.type,
					    event.payload,
					    status == AGENT_STATUS_CANCELLED ?
						    "cancelled" :
						    "event",
					    status, event.type,
					    event.source_pid, event.corr_id);
	}
	p->loop_state = AGENT_LOOP_IDLE;
	return status;
}

int sys_agent_wait_cancel(int pid, uint64 reasonaddr)
{
	struct proc *p = curr_proc();
	struct proc *target;
	char reason[AGENT_EVENT_PAYLOAD_SIZE];
	uint64 event_id;

	if (!p->is_agent)
		return -1;
	if (!agent_has_any_cap(p, AGENT_CAP_MESSAGE_SEND | AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	memset(reason, 0, sizeof(reason));
	if (reasonaddr != 0 &&
	    copyinstr(p->pagetable, reason, reasonaddr, sizeof(reason)) < 0)
		return -1;
	if (reason[0] == 0)
		safestrcpy(reason, "cancel", sizeof(reason));
	for (target = pool; target < &pool[NPROC]; target++) {
		if (target->state == P_UNUSED || target->pid != pid)
			continue;
		if (!target->is_agent)
			return AGENT_STATUS_NOT_FOUND;
		if (target->agent_wait_cancel_pending)
			return AGENT_STATUS_DUPLICATE;
		event_id = next_event_id++;
		target->agent_wait_cancel_pending = 1;
		target->agent_wait_cancel_source_pid = p->pid;
		target->agent_wait_cancel_event_id = event_id;
		target->agent_wait_cancel_corr_id = event_id;
		target->agent_wait_cancel_tick = agent_ticks();
		target->agent_wait_cancel_cause_sequence =
			p->context_path_latest;
		target->agent_wait_cancel_span_id = p->agent_current_span_id;
		safestrcpy(target->agent_wait_cancel_reason, reason,
			   sizeof(target->agent_wait_cancel_reason));
		target->agent_wait_cancel_count++;
		target->agent_wait_deadline_valid = 0;
		agent_wake_event_waiters(target);
		return 0;
	}
	return AGENT_STATUS_NOT_FOUND;
}

int sys_agent_heartbeat(int interval_ticks)
{
	struct proc *p = curr_proc();

	if (!p->is_agent)
		return -1;
	if (interval_ticks < 0)
		return AGENT_STATUS_BAD_PARAM;
	p->heartbeat_interval = interval_ticks;
	p->agent_last_heartbeat_tick = agent_ticks();
	agent_append_system_context(p, AGENT_TOOL_AGENT_HEARTBEAT, 0,
				    interval_ticks, "heartbeat", "heartbeat",
				    AGENT_STATUS_OK, interval_ticks,
				    p->agent_last_heartbeat_tick, 0);
	return 0;
}

int sys_agent_wake(int pid, uint64 eventaddr)
{
	struct proc *p = curr_proc();
	struct agent_event event;
	char payload[AGENT_EVENT_PAYLOAD_SIZE];
	int delivered;

	if (!p->is_agent)
		return -1;
	if (!agent_has_any_cap(p, AGENT_CAP_MESSAGE_SEND | AGENT_CAP_ORCHESTRATE))
		return AGENT_STATUS_DENIED;
	if (eventaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	if (copyin(p->pagetable, (char *)&event, eventaddr, sizeof(event)) < 0)
		return -1;
	event.payload[sizeof(event.payload) - 1] = 0;
	safestrcpy(payload, event.payload, sizeof(payload));
	delivered = agent_deliver_pid(pid, p, p->pid, event.type, event.corr_id,
				      p->context_path_latest,
				      p->agent_current_span_id, payload);
	if (delivered < 0)
		return AGENT_STATUS_NO_SPACE;
	if (!delivered)
		return AGENT_STATUS_NOT_FOUND;
	return 0;
}

static int agent_file_edit_lookup_path(struct proc *p, uint64 pathaddr,
				       char *path, struct inode **out)
{
	struct inode *ip;

	if (copyinstr(p->pagetable, path, pathaddr, MAXPATH) < 0)
		return -1;
	path[MAXPATH - 1] = 0;
	if (path[0] == 0 || agent_file_is_meta_store_name(path))
		return AGENT_STATUS_BAD_PARAM;
	ip = namei(path);
	if (ip == 0)
		return AGENT_STATUS_NOT_FOUND;
	ivalid(ip);
	if (ip->type != T_FILE) {
		iput(ip);
		return AGENT_STATUS_BAD_PARAM;
	}
	*out = ip;
	return 0;
}

static int agent_file_edit_copy_state(struct proc *p, uint64 stateaddr,
				      struct agent_file_edit_state *state)
{
	if (stateaddr == 0)
		return 0;
	if (user_range_check(p->pagetable, stateaddr, sizeof(*state), PTE_W) < 0)
		return -1;
	return copyout(p->pagetable, stateaddr, (char *)state,
		       sizeof(*state));
}

int sys_agent_file_edit_begin(uint64 pathaddr, uint64 flags, int ttl_ticks,
			      uint64 stateaddr)
{
	struct proc *p = curr_proc();
	struct inode *ip;
	struct agent_file_edit_entry *edit;
	struct agent_file_edit_entry *slot;
	struct agent_file_edit_state state;
	char path[MAXPATH];
	uint64 now;
	uint64 version;
	int ok;
	int rc;
	int ttl;

	if (!p->is_agent)
		return -1;
	if (!agent_edit_can_manage(p))
		return AGENT_STATUS_DENIED;
	if (stateaddr &&
	    user_range_check(p->pagetable, stateaddr, sizeof(state), PTE_W) < 0)
		return -1;
	rc = agent_file_edit_lookup_path(p, pathaddr, path, &ip);
	if (rc < 0)
		return rc;

	now = agent_ticks();
	ttl = ttl_ticks <= 0 ? AGENT_FILE_EDIT_DEFAULT_TTL : ttl_ticks;
	if (ttl > AGENT_FILE_EDIT_MAX_TTL)
		ttl = AGENT_FILE_EDIT_MAX_TTL;

	agent_edit_lock();
	agent_edit_cleanup_expired_locked(now);
	version = agent_edit_version_locked(ip->dev, ip->inum, 1, &ok);
	if (!ok) {
		agent_edit_unlock();
		iput(ip);
		return AGENT_STATUS_NO_SPACE;
	}
	edit = agent_edit_find_locked(ip->dev, ip->inum);
	if (edit) {
		if ((flags & AGENT_FILE_EDIT_F_ORCHESTRATOR_BREAK) &&
		    agent_has_cap(p, AGENT_CAP_ORCHESTRATE)) {
			agent_edit_release_locked(edit, 1);
			version = agent_edit_version_locked(ip->dev, ip->inum,
							   1, &ok);
			if (!ok) {
				agent_edit_unlock();
				iput(ip);
				return AGENT_STATUS_NO_SPACE;
			}
		} else {
			edit->conflict_count++;
			agent_edit_fill_state_locked(&state, edit, ip->dev,
						     ip->inum, path);
			agent_edit_unlock();
			iput(ip);
			agent_edit_audit(p, AGENT_STATUS_CONFLICT,
					 "edit_begin_conflict", &state);
			if (agent_file_edit_copy_state(p, stateaddr, &state) <
			    0)
				return -1;
			return AGENT_STATUS_CONFLICT;
		}
	}
	slot = agent_edit_free_locked();
	if (slot == 0) {
		agent_edit_unlock();
		iput(ip);
		return AGENT_STATUS_NO_SPACE;
	}
	memset(slot, 0, sizeof(*slot));
	slot->active = 1;
	slot->owner_pid = p->pid;
	slot->owner_agent_id = p->agent_id;
	slot->owner_role = p->agent_role;
	slot->lease_id = agent_file_edit_next_lease++;
	if (agent_file_edit_next_lease == 0)
		agent_file_edit_next_lease = 1;
	slot->dev = ip->dev;
	slot->inum = ip->inum;
	slot->base_version = version;
	slot->deadline_tick = now + ttl;
	safestrcpy(slot->path, path, sizeof(slot->path));
	agent_edit_fill_state_locked(&state, slot, ip->dev, ip->inum, path);
	agent_edit_unlock();
	iput(ip);
	agent_edit_audit(p, AGENT_STATUS_OK, "edit_begin", &state);
	return agent_file_edit_copy_state(p, stateaddr, &state);
}

int sys_agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
			       uint64 stateaddr)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;
	struct agent_file_edit_state state;
	char path[AGENT_FILE_LOGICAL_SIZE];
	uint64 dev;
	uint64 inum;
	uint64 base;
	uint64 now;
	uint64 current;
	uint64 new_version;
	int ok;
	int dirty;
	int rc;

	if (!p->is_agent)
		return -1;
	if (stateaddr &&
	    user_range_check(p->pagetable, stateaddr, sizeof(state), PTE_W) < 0)
		return -1;
	now = agent_ticks();
	agent_edit_lock();
	agent_edit_cleanup_expired_locked(now);
	edit = agent_edit_find_lease_locked(lease_id);
	if (edit == 0) {
		agent_edit_unlock();
		return AGENT_STATUS_NOT_FOUND;
	}
	if (!agent_edit_owner(edit, p) &&
	    !agent_has_cap(p, AGENT_CAP_ORCHESTRATE)) {
		agent_edit_fill_state_locked(&state, edit, edit->dev,
					     edit->inum, edit->path);
		agent_edit_unlock();
		agent_edit_audit(p, AGENT_STATUS_DENIED,
				 "edit_commit_denied", &state);
		if (agent_file_edit_copy_state(p, stateaddr, &state) < 0)
			return -1;
		return AGENT_STATUS_DENIED;
	}
	current = agent_edit_version_locked(edit->dev, edit->inum, 0, &ok);
	if (!ok || current != edit->base_version ||
	    expected_version != edit->base_version) {
		agent_edit_fill_state_locked(&state, edit, edit->dev,
					     edit->inum, edit->path);
		agent_edit_unlock();
		agent_edit_audit(p, AGENT_STATUS_STALE,
				 "edit_commit_stale", &state);
		if (agent_file_edit_copy_state(p, stateaddr, &state) < 0)
			return -1;
		return AGENT_STATUS_STALE;
	}
	dev = edit->dev;
	inum = edit->inum;
	base = edit->base_version;
	dirty = edit->dirty;
	safestrcpy(path, edit->path, sizeof(path));
	new_version = dirty ? base + 1 : base;
	rc = agent_edit_set_version_locked(dev, inum, new_version);
	memset(edit, 0, sizeof(*edit));
	memset(&state, 0, sizeof(state));
	state.active = 0;
	state.lease_id = lease_id;
	state.dev = dev;
	state.inum = inum;
	state.base_version = base;
	state.current_version = new_version;
	state.dirty = dirty;
	safestrcpy(state.path, path, sizeof(state.path));
	agent_edit_unlock();
	if (rc < 0)
		return AGENT_STATUS_NO_SPACE;
	agent_edit_audit(p, AGENT_STATUS_OK, "edit_commit", &state);
	return agent_file_edit_copy_state(p, stateaddr, &state);
}

int sys_agent_file_edit_abort(uint64 lease_id)
{
	struct proc *p = curr_proc();
	struct agent_file_edit_entry *edit;
	struct agent_file_edit_state state;

	if (!p->is_agent)
		return -1;
	agent_edit_lock();
	agent_edit_cleanup_expired_locked(agent_ticks());
	edit = agent_edit_find_lease_locked(lease_id);
	if (edit == 0) {
		agent_edit_unlock();
		return AGENT_STATUS_NOT_FOUND;
	}
	if (!agent_edit_owner(edit, p) &&
	    !agent_has_cap(p, AGENT_CAP_ORCHESTRATE)) {
		agent_edit_fill_state_locked(&state, edit, edit->dev,
					     edit->inum, edit->path);
		agent_edit_unlock();
		agent_edit_audit(p, AGENT_STATUS_DENIED,
				 "edit_abort_denied", &state);
		return AGENT_STATUS_DENIED;
	}
	agent_edit_fill_state_locked(&state, edit, edit->dev, edit->inum,
				     edit->path);
	agent_edit_release_locked(edit, 1);
	agent_edit_unlock();
	agent_edit_audit(p, AGENT_STATUS_OK, "edit_abort", &state);
	return 0;
}

int sys_agent_file_edit_state(uint64 pathaddr, uint64 stateaddr)
{
	struct proc *p = curr_proc();
	struct inode *ip;
	struct agent_file_edit_entry *edit;
	struct agent_file_edit_state state;
	char path[MAXPATH];
	int ok;
	int rc;

	if (!p->is_agent)
		return -1;
	if (stateaddr == 0 ||
	    user_range_check(p->pagetable, stateaddr, sizeof(state), PTE_W) < 0)
		return -1;
	rc = agent_file_edit_lookup_path(p, pathaddr, path, &ip);
	if (rc < 0)
		return rc;
	agent_edit_lock();
	agent_edit_cleanup_expired_locked(agent_ticks());
	agent_edit_version_locked(ip->dev, ip->inum, 1, &ok);
	if (!ok) {
		agent_edit_unlock();
		iput(ip);
		return AGENT_STATUS_NO_SPACE;
	}
	edit = agent_edit_find_locked(ip->dev, ip->inum);
	agent_edit_fill_state_locked(&state, edit, ip->dev, ip->inum, path);
	agent_edit_unlock();
	iput(ip);
	return agent_file_edit_copy_state(p, stateaddr, &state);
}

int sys_agent_file_meta_init(void)
{
	struct proc *p = curr_proc();

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_META_WRITE))
		return AGENT_STATUS_DENIED;
	agent_action_history_count = 0;
	memset(agent_action_history, 0, sizeof(agent_action_history));
	agent_file_loaded = 0;
	if (agent_file_load() <= 0)
		agent_file_install_empty_store();
	agent_file_enable_scan();
	return 0;
}

int sys_agent_file_meta_set(uint64 metaaddr)
{
	struct proc *p = curr_proc();
	struct agent_file_meta meta;
	int slot = -1;
	int status_changed = 0;
	uint64 mask;
	char event_payload[AGENT_EVENT_PAYLOAD_SIZE];

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_META_WRITE))
		return AGENT_STATUS_DENIED;
	if (copyin(p->pagetable, (char *)&meta, metaaddr, sizeof(meta)) < 0)
		return -1;
	meta.physical_name[sizeof(meta.physical_name) - 1] = 0;
	meta.logical_path[sizeof(meta.logical_path) - 1] = 0;
	meta.project[sizeof(meta.project) - 1] = 0;
	meta.workflow[sizeof(meta.workflow) - 1] = 0;
	meta.run_id[sizeof(meta.run_id) - 1] = 0;
	meta.stage[sizeof(meta.stage) - 1] = 0;
	meta.kind[sizeof(meta.kind) - 1] = 0;
	meta.status[sizeof(meta.status) - 1] = 0;
	meta.summary[sizeof(meta.summary) - 1] = 0;
	agent_file_load();
	mask = meta.update_mask;
	for (int i = 0; i < AGENT_FILE_META_MAX; i++) {
		if (!agent_files[i].used)
			continue;
		if ((meta.fid > 0 && agent_files[i].fid == meta.fid) ||
		    (meta.physical_name[0] &&
		     strncmp(agent_files[i].physical_name, meta.physical_name,
			     sizeof(meta.physical_name)) == 0) ||
		    (meta.logical_path[0] &&
		     strncmp(agent_files[i].logical_path, meta.logical_path,
			     sizeof(meta.logical_path)) == 0)) {
			slot = i;
			break;
		}
	}
	if (meta.flags & AGENT_FILE_META_F_DELETE) {
		if (slot < 0)
			return AGENT_STATUS_NOT_FOUND;
		agent_file_clear_slot(slot);
		agent_file_rebuild_indexes();
		agent_file_persist();
		agent_file_request_scan();
		return 0;
	}
	if (slot < 0) {
		for (int i = 0; i < AGENT_FILE_META_MAX; i++)
			if (!agent_files[i].used) {
				slot = i;
				break;
			}
	}
	if (slot < 0)
		return AGENT_STATUS_NO_SPACE;
	if (!agent_files[slot].used) {
		memset(&agent_files[slot], 0, sizeof(agent_files[slot]));
		agent_files[slot].used = 1;
		agent_files[slot].fid = meta.fid ? meta.fid : slot + 1;
	}
	if ((mask & AGENT_FILE_META_UPDATE_PHYSICAL) ||
	    (!mask && meta.physical_name[0]))
		safestrcpy(agent_files[slot].physical_name, meta.physical_name,
			   sizeof(agent_files[slot].physical_name));
	if ((mask & AGENT_FILE_META_UPDATE_LOGICAL) ||
	    (!mask && meta.logical_path[0]))
		safestrcpy(agent_files[slot].logical_path, meta.logical_path,
			   sizeof(agent_files[slot].logical_path));
	if ((mask & AGENT_FILE_META_UPDATE_PROJECT) ||
	    (!mask && meta.project[0]))
		safestrcpy(agent_files[slot].project, meta.project,
			   sizeof(agent_files[slot].project));
	if ((mask & AGENT_FILE_META_UPDATE_WORKFLOW) ||
	    (!mask && meta.workflow[0]))
		safestrcpy(agent_files[slot].workflow, meta.workflow,
			   sizeof(agent_files[slot].workflow));
	if ((mask & AGENT_FILE_META_UPDATE_RUN_ID) ||
	    (!mask && meta.run_id[0]))
		safestrcpy(agent_files[slot].run_id, meta.run_id,
			   sizeof(agent_files[slot].run_id));
	if ((mask & AGENT_FILE_META_UPDATE_STAGE) ||
	    (!mask && meta.stage[0]))
		safestrcpy(agent_files[slot].stage, meta.stage,
			   sizeof(agent_files[slot].stage));
	if ((mask & AGENT_FILE_META_UPDATE_KIND) ||
	    (!mask && meta.kind[0]))
		safestrcpy(agent_files[slot].kind, meta.kind,
			   sizeof(agent_files[slot].kind));
	if ((mask & AGENT_FILE_META_UPDATE_STATUS) ||
	    (!mask && meta.status[0])) {
		if (strncmp(agent_files[slot].status, meta.status,
			    sizeof(meta.status)) != 0)
			status_changed = 1;
		safestrcpy(agent_files[slot].status, meta.status,
			   sizeof(agent_files[slot].status));
	}
	if ((mask & AGENT_FILE_META_UPDATE_SUMMARY) ||
	    (!mask && meta.summary[0]))
		safestrcpy(agent_files[slot].summary, meta.summary,
			   sizeof(agent_files[slot].summary));
	if ((mask & AGENT_FILE_META_UPDATE_DEPENDENCY) ||
	    (!mask && meta.dependency_mask))
		agent_files[slot].dependency_mask = meta.dependency_mask;
	agent_files[slot].flags &= ~AGENT_FILE_META_F_AUTOSCAN;
	if (meta.flags & AGENT_FILE_META_F_AUTOSCAN)
		agent_files[slot].flags |= AGENT_FILE_META_F_AUTOSCAN;
	if (meta.flags & AGENT_FILE_META_F_PERSIST)
		agent_files[slot].flags |= AGENT_FILE_META_F_PERSIST;
	agent_files[slot].updated_tick = agent_ticks();
	agent_files[slot].fs_generation = ++agent_file_generation;
	if (agent_file_bind_slot(slot) < 0)
		return AGENT_STATUS_NOT_FOUND;
	agent_file_rebuild_indexes();
	if (agent_files[slot].flags & AGENT_FILE_META_F_PERSIST)
		agent_file_persist();
	agent_file_event_payload(&agent_files[slot], event_payload,
				 sizeof(event_payload));
	if (status_changed && meta.status[0] &&
	    agent_deliver_watchers(p->pid, AGENT_EVENT_FILE_STATUS, meta.fid,
				   p->context_path_latest,
				   p->agent_current_span_id,
				   event_payload) < 0)
		return AGENT_STATUS_NO_SPACE;
	agent_file_request_scan();
	return 0;
}

int sys_agent_file_query(uint64 queryaddr, uint64 resultaddr)
{
	struct proc *p = curr_proc();
	struct agent_file_query query;
	struct agent_file_query_result result;
	int returned;

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_META_READ))
		return AGENT_STATUS_DENIED;
	if (copyin(p->pagetable, (char *)&query, queryaddr,
		   sizeof(query)) < 0)
		return -1;
	query.physical_name[sizeof(query.physical_name) - 1] = 0;
	query.logical_path[sizeof(query.logical_path) - 1] = 0;
	query.project[sizeof(query.project) - 1] = 0;
	query.workflow[sizeof(query.workflow) - 1] = 0;
	query.run_id[sizeof(query.run_id) - 1] = 0;
	query.stage[sizeof(query.stage) - 1] = 0;
	query.kind[sizeof(query.kind) - 1] = 0;
	query.status[sizeof(query.status) - 1] = 0;
	query.summary_contains[sizeof(query.summary_contains) - 1] = 0;
	if (user_range_check(p->pagetable, resultaddr, sizeof(result), PTE_W) < 0)
		return -1;
	if (!agent_file_query_has_filter(&query))
		return AGENT_STATUS_BAD_PARAM;
	returned = agent_file_query_internal(&query, &result);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0)
		return -1;
	if (agent_append_system_context(
		    p, AGENT_TOOL_QUERY_FILE, 0, query.flags,
		    query.status[0] ? query.status : query.stage,
		    result.returned ? result.hits[0].physical_name : "empty",
		    AGENT_STATUS_OK, result.total_hits, result.scanned_records,
		    result.used_index) == 0)
		agent_file_prefetch_update(p, &query, &result,
					   p->agent_call_count);
	return returned;
}

int sys_agent_file_prefetch_snapshot(uint64 hintsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_file_prefetch_hint hint;
	int visible;
	int n;
	int start;
	int slot;

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_META_READ))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	visible = p->agent_prefetch_count;
	if (visible > AGENT_FILE_PREFETCH_MAX_HINTS)
		visible = AGENT_FILE_PREFETCH_MAX_HINTS;
	if (max == 0)
		return visible;
	if (hintsaddr == 0)
		return AGENT_STATUS_BAD_PARAM;
	n = visible < max ? visible : max;
	start = (p->agent_prefetch_head + AGENT_FILE_PREFETCH_MAX_HINTS -
		 visible) %
		AGENT_FILE_PREFETCH_MAX_HINTS;
	for (int i = 0; i < n; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_MAX_HINTS;
		memmove(&hint, &p->agent_prefetch_hints[slot], sizeof(hint));
		if (copyout(p->pagetable,
			    hintsaddr +
				    i * sizeof(struct agent_file_prefetch_hint),
			    (char *)&hint, sizeof(hint)) < 0)
			return -1;
	}
	return n;
}

int sys_agent_file_prefetch_span_snapshot(uint64 hintsaddr, int max)
{
	struct proc *p = curr_proc();
	struct agent_file_prefetch_hint hint;
	uint64 span_id;
	int visible;
	int start;
	int slot;
	int matched = 0;
	int copied = 0;

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_META_READ))
		return AGENT_STATUS_DENIED;
	if (max < 0)
		return AGENT_STATUS_BAD_PARAM;
	span_id = p->agent_current_span_id;
	if (span_id == 0)
		return 0;
	visible = agent_span_prefetch_count;
	if (visible > AGENT_FILE_PREFETCH_SPAN_MAX)
		visible = AGENT_FILE_PREFETCH_SPAN_MAX;
	start = (agent_span_prefetch_head + AGENT_FILE_PREFETCH_SPAN_MAX -
		 visible) %
		AGENT_FILE_PREFETCH_SPAN_MAX;
	for (int i = 0; i < visible; i++) {
		slot = (start + i) % AGENT_FILE_PREFETCH_SPAN_MAX;
		memmove(&hint, &agent_span_prefetch_hints[slot],
			sizeof(hint));
		if (hint.span_id != span_id)
			continue;
		matched++;
		if (max == 0 || copied >= max)
			continue;
		if (hintsaddr == 0)
			return AGENT_STATUS_BAD_PARAM;
		if (copyout(p->pagetable,
			    hintsaddr +
				    copied *
					    sizeof(struct agent_file_prefetch_hint),
			    (char *)&hint, sizeof(hint)) < 0)
			return -1;
		copied++;
	}
	if (max == 0)
		return matched;
	return copied;
}

int sys_agent_call(uint64 reqaddr, uint64 respaddr)
{
	struct proc *p = curr_proc();
	struct agent_request req;
	struct agent_response resp;
	struct agent_op op;
	struct agent_result res;
	struct agent_tool_desc *tool;
	char validate_error[AGENT_RESULT_SIZE];

	if (copyin(p->pagetable, (char *)&req, reqaddr, sizeof(req)) < 0)
		return -1;
	if (user_range_check(p->pagetable, respaddr, sizeof(resp), PTE_W) < 0)
		return -1;
	memset(&resp, 0, sizeof(resp));
	resp.version = AGENT_CALL_VERSION;
	resp.request_id = req.request_id;
	if (!p->is_agent) {
		resp.status = AGENT_STATUS_NOT_AGENT;
		safestrcpy(resp.result, "not_agent", sizeof(resp.result));
		return copyout(p->pagetable, respaddr, (char *)&resp,
			       sizeof(resp));
	}
	req.tool_name[AGENT_TOOL_NAME_SIZE - 1] = 0;
	req.payload[AGENT_PAYLOAD_SIZE - 1] = 0;
	tool = req.tool_id ? agent_tool_by_id(req.tool_id) : 0;
	if (!tool && req.tool_name[0])
		tool = agent_tool_by_name(req.tool_name);
	if (!tool) {
		resp.status = AGENT_STATUS_UNKNOWN_TOOL;
		safestrcpy(resp.result, "unknown_tool", sizeof(resp.result));
		return copyout(p->pagetable, respaddr, (char *)&resp,
			       sizeof(resp));
	}
	if (req.tool_id && req.tool_name[0] &&
	    strncmp(req.tool_name, tool->name, AGENT_TOOL_NAME_SIZE) != 0) {
		resp.status = AGENT_STATUS_BAD_REQUEST;
		resp.tool_id = req.tool_id;
		safestrcpy(resp.tool_name, tool->name, sizeof(resp.tool_name));
		safestrcpy(resp.result, "tool_mismatch", sizeof(resp.result));
		return copyout(p->pagetable, respaddr, (char *)&resp,
			       sizeof(resp));
	}
	memset(validate_error, 0, sizeof(validate_error));
	if (agent_validate_legacy_request(&req, tool->tool_id, validate_error,
					  sizeof(validate_error)) < 0) {
		resp.status = AGENT_STATUS_BAD_PARAM;
		resp.tool_id = tool->tool_id;
		safestrcpy(resp.tool_name, tool->name, sizeof(resp.tool_name));
		safestrcpy(resp.result, validate_error, sizeof(resp.result));
		return copyout(p->pagetable, respaddr, (char *)&resp,
			       sizeof(resp));
	}
	memset(&op, 0, sizeof(op));
	op.version = req.version;
	op.tool_id = tool->tool_id;
	op.request_id = req.request_id;
	op.arg0 = req.arg0;
	op.arg1 = req.arg1;
	safestrcpy(op.payload, req.payload, sizeof(op.payload));
	if (agent_execute_one(p, &op, &res, agent_ticks()) < 0 ||
	    agent_write_header(p) < 0)
		res.status = AGENT_STATUS_NO_SPACE;
	resp.status = res.status;
	resp.tool_id = res.tool_id;
	resp.request_id = res.request_id;
	resp.sequence = res.sequence;
	resp.value0 = res.value0;
	resp.value1 = res.value1;
	resp.value2 = res.value2;
	safestrcpy(resp.tool_name, tool->name, sizeof(resp.tool_name));
	safestrcpy(resp.result, res.result, sizeof(resp.result));
	return copyout(p->pagetable, respaddr, (char *)&resp, sizeof(resp));
}

void agent_tick(void)
{
	uint64 now = agent_ticks();
	char payload[AGENT_EVENT_PAYLOAD_SIZE];

	if (agent_file_scan_enabled && !agent_file_scan_active &&
	    now >= agent_file_scan_next_tick)
		agent_file_scan_pending = 1;
	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (p->state == P_UNUSED || !p->is_agent)
			continue;
		if (p->agent_wait_deadline_valid &&
		    now >= p->agent_wait_deadline) {
			p->agent_wait_deadline_valid = 0;
			agent_wake_event_waiters(p);
		}
		if (p->agent_timeline_wait_deadline_valid &&
		    now >= p->agent_timeline_wait_deadline) {
			p->agent_timeline_wait_deadline_valid = 0;
			agent_wake_timeline_waiters(p);
		}
		if (p->heartbeat_interval > 0 &&
		    now - p->agent_last_heartbeat_tick >=
			    (uint64)p->heartbeat_interval) {
			p->agent_last_heartbeat_tick = now;
			safestrcpy(payload, "timer=heartbeat", sizeof(payload));
			agent_queue_event(p, 0, AGENT_EVENT_TIMER, now,
					  p->context_path_latest,
					  p->agent_current_span_id, payload);
		}
	}
}
