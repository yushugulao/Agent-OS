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

static int nextagentid = 1;
static uint64 next_event_id = 1;

static struct agent_tool_desc agent_tools[] = {
	{ AGENT_TOOL_ECHO, "echo", "payload:string,arg0:uint64,arg1:uint64",
	  "return payload and numeric parameters" },
	{ AGENT_TOOL_PID_INFO, "pid_info", "none",
	  "return pid, agent id, and agent flag" },
	{ AGENT_TOOL_CTX_STAT, "ctx_stat", "none",
	  "return Agent Context base, size, and call count" },
	{ AGENT_TOOL_QUERY_PROCESS, "query_process", "type:uint64",
	  "count processes and Agent processes" },
	{ AGENT_TOOL_GET_SYSTEM_STATUS, "get_system_status", "none",
	  "return process count, agent count, and uptime tick" },
	{ AGENT_TOOL_READ_CONTEXT, "read_context", "none",
	  "return post-state Context Path counters" },
	{ AGENT_TOOL_QUERY_FILE, "query_file", "path|string-filter",
	  "query file inode metadata or Agent file metadata" },
	{ AGENT_TOOL_SEND_MESSAGE, "send_message",
	  "target_pid:uint64,message:string", "send a short Agent message" },
	{ AGENT_TOOL_READ_MESSAGE, "read_message", "none",
	  "read current Agent mailbox" },
	{ AGENT_TOOL_FILE_META_INIT, "file_meta_init", "none",
	  "install demo file metadata and rebuild indexes" },
	{ AGENT_TOOL_READ_FILE_SUMMARY, "read_file_summary", "selector:string",
	  "read one indexed file summary" },
	{ AGENT_TOOL_DEPENDENCY_QUERY, "dependency_query", "stage:string",
	  "return affected workflow stages" },
	{ AGENT_TOOL_CAPABILITY_CHECK, "capability_check",
	  "role:uint64,action:string", "check role capability" },
	{ AGENT_TOOL_RERUN_STAGE, "rerun_stage", "role:uint64,stage:string",
	  "perform an idempotent recovery rerun" },
	{ AGENT_TOOL_WRITE_REPORT, "write_report", "role:uint64,payload:string",
	  "write the recovery report artifact" },
	{ AGENT_TOOL_AGENT_WATCH, "agent_watch",
	  "event_type:uint64,filter:string", "register an Agent Loop watch" },
	{ AGENT_TOOL_AGENT_WAIT, "agent_wait", "timeout:uint64",
	  "wait for a watched event" },
	{ AGENT_TOOL_AGENT_HEARTBEAT, "agent_heartbeat", "interval:uint64",
	  "set heartbeat interval" },
};

static struct agent_file_meta agent_files[AGENT_FILE_META_MAX];
static int agent_file_status_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_stage_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_kind_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_status_next[AGENT_FILE_META_MAX];
static int agent_file_stage_next[AGENT_FILE_META_MAX];
static int agent_file_kind_next[AGENT_FILE_META_MAX];
static uint64 agent_action_history[AGENT_ACTION_HISTORY_MAX];
static int agent_action_history_count;

static void agent_file_reset_indexes(void);
static void agent_query_from_payload(struct agent_file_query *q, char *payload);

struct agent_proc_snapshot {
	int used;
	int agents;
	int runnable;
};

void agentinit(void)
{
	nextagentid = 1;
	next_event_id = 1;
	agent_action_history_count = 0;
	memset(agent_files, 0, sizeof(agent_files));
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

static int agent_context_layout_ok(void)
{
	if (AGENT_CONTEXT_LATEST_RESPONSE_OFFSET !=
	    sizeof(struct agent_context_header))
		return 0;
	if (AGENT_CONTEXT_RECORDS_OFFSET != PAGE_SIZE)
		return 0;
	if (AGENT_CONTEXT_RECORDS_OFFSET +
		    AGENT_CONTEXT_MAX_RECORDS *
			    sizeof(struct agent_context_record) >
	    AGENT_CONTEXT_SIZE)
		return 0;
	return 1;
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

void agent_clear_metadata(struct proc *p)
{
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
	p->agent_watch_valid = 0;
	p->agent_watch_event_type = AGENT_EVENT_NONE;
	memset(p->agent_watch_filter, 0, sizeof(p->agent_watch_filter));
	p->agent_event_valid = 0;
	p->agent_event_type = AGENT_EVENT_NONE;
	p->agent_event_source_pid = 0;
	p->agent_event_id = 0;
	p->agent_event_tick = 0;
	p->agent_event_corr_id = 0;
	memset(p->agent_event_payload, 0, sizeof(p->agent_event_payload));
	p->agent_event_count = 0;
	p->agent_event_dropped = 0;
	p->agent_wait_count = 0;
	p->agent_timeout_count = 0;
	p->agent_last_heartbeat_tick = 0;
	p->agent_capability_mask = 0;
}

static int agent_role_valid(int role)
{
	return role >= AGENT_ROLE_SENTINEL && role <= AGENT_ROLE_ORCHESTRATOR;
}

static uint64 agent_all_capabilities(void)
{
	return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
	       AGENT_CAP_PROCESS_READ | AGENT_CAP_MESSAGE_SEND |
	       AGENT_CAP_WATCH | AGENT_CAP_RECOVER_STAGE |
	       AGENT_CAP_REPORT_WRITE | AGENT_CAP_AUDIT_WRITE |
	       AGENT_CAP_META_WRITE | AGENT_CAP_ORCHESTRATE;
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
		       AGENT_CAP_RECOVER_STAGE | AGENT_CAP_REPORT_WRITE |
		       AGENT_CAP_AUDIT_WRITE;
	case AGENT_ROLE_ORCHESTRATOR:
		return agent_all_capabilities();
	default:
		return 0;
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

static uint64 agent_cap_for_action(char *action)
{
	if (strncmp(action, "rerun_stage", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_RECOVER_STAGE;
	if (strncmp(action, "write_report", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_REPORT_WRITE;
	if (strncmp(action, "query", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "query_file", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "read_meta", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_META_READ;
	if (strncmp(action, "read_file_summary", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "read_content", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_CONTENT_READ;
	if (strncmp(action, "send_message", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_MESSAGE_SEND;
	if (strncmp(action, "watch", AGENT_OP_PAYLOAD_SIZE) == 0)
		return AGENT_CAP_WATCH;
	if (strncmp(action, "meta_write", AGENT_OP_PAYLOAD_SIZE) == 0 ||
	    strncmp(action, "file_meta_write", AGENT_OP_PAYLOAD_SIZE) == 0)
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

void agent_free_proc_context(struct proc *p)
{
	if (p->agent_ctx_base) {
		uvmunmap(p->pagetable, p->agent_ctx_base, AGENT_CONTEXT_PAGES,
			 1);
	}
	agent_free_shadow(p->agent_shadow_kva);
	agent_clear_metadata(p);
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
	for (int i = 0; i < AGENT_CONTEXT_PAGES; i++) {
		if (p->agent_shadow_kva[i] == 0 || p->agent_ctx_kva[i] == 0)
			return -1;
		memmove((void *)p->agent_ctx_kva[i],
			(void *)p->agent_shadow_kva[i], PAGE_SIZE);
	}
	return 0;
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
	info->wait_count = p->agent_wait_count;
	info->timeout_count = p->agent_timeout_count;
	info->last_heartbeat_tick = p->agent_last_heartbeat_tick;
	info->capability_mask = p->agent_capability_mask;
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

static int agent_append_context(struct proc *p, struct agent_op *op,
				struct agent_result *latest, uint64 tick)
{
	uint64 slot;
	struct agent_context_record record;

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
	record.arg0 = op->arg0;
	record.value0 = latest->value0;
	record.value1 = latest->value1;
	record.value2 = latest->value2;
	record.tick = tick;
	record.tool_id = latest->tool_id;
	record.status = latest->status;
	safestrcpy(record.payload, op->payload, sizeof(record.payload));
	safestrcpy(record.result, latest->result, sizeof(record.result));
	if (agent_write_record(p, slot, &record) < 0)
		return -1;
	return agent_write_latest(p, latest);
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
}

static void agent_file_fill_meta(struct agent_file_meta *m, int fid,
				 char *physical, char *logical, char *project,
				 char *workflow, char *run_id, char *stage,
				 char *kind, char *status, char *summary,
				 uint64 dependency_mask)
{
	memset(m, 0, sizeof(*m));
	m->used = 1;
	m->fid = fid;
	safestrcpy(m->physical_name, physical, sizeof(m->physical_name));
	safestrcpy(m->logical_path, logical, sizeof(m->logical_path));
	safestrcpy(m->project, project, sizeof(m->project));
	safestrcpy(m->workflow, workflow, sizeof(m->workflow));
	safestrcpy(m->run_id, run_id, sizeof(m->run_id));
	safestrcpy(m->stage, stage, sizeof(m->stage));
	safestrcpy(m->kind, kind, sizeof(m->kind));
	safestrcpy(m->status, status, sizeof(m->status));
	safestrcpy(m->summary, summary, sizeof(m->summary));
	m->dependency_mask = dependency_mask;
	m->updated_tick = agent_ticks();
}

static void agent_file_install_default(void)
{
	memset(agent_files, 0, sizeof(agent_files));
	agent_file_fill_meta(&agent_files[0], 1, "lab_RUN042_prepare_ok",
			     "/lab/projects/lab-gene-x/runs/RUN-042/prepare.ok",
			     "lab-gene-x", "nightly-regression", "RUN-042",
			     "prepare", "status", "ok",
			     "prepare output is reusable", AGENT_DEP_PREPARE);
	agent_file_fill_meta(&agent_files[1], 2, "lab_RUN042_align_sam",
			     "/lab/projects/lab-gene-x/runs/RUN-042/align.sam",
			     "lab-gene-x", "nightly-regression", "RUN-042",
			     "align", "artifact", "ok",
			     "align output is ready before injected failure",
			     AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE |
				     AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE);
	agent_file_fill_meta(&agent_files[2], 3, "lab_RUN042_align_err",
			     "/lab/projects/lab-gene-x/runs/RUN-042/align.err",
			     "lab-gene-x", "nightly-regression", "RUN-042",
			     "align", "log", "ok",
			     "no failure recorded yet",
			     AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE |
				     AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE);
	agent_file_fill_meta(&agent_files[3], 4,
			     "lab_RUN042_analyze_skipped",
			     "/lab/projects/lab-gene-x/runs/RUN-042/analyze.skipped",
			     "lab-gene-x", "nightly-regression", "RUN-042",
			     "analyze", "status", "pending",
			     "analysis waits for align",
			     AGENT_DEP_ANALYZE | AGENT_DEP_REPORT |
				     AGENT_DEP_ARCHIVE);
	agent_file_fill_meta(&agent_files[4], 5,
			     "lab_RUN042_report_incomplete",
			     "/lab/projects/lab-gene-x/runs/RUN-042/report.incomplete",
			     "lab-gene-x", "nightly-regression", "RUN-042",
			     "report", "report", "pending",
			     "report waits for analyze",
			     AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE);
	agent_file_fill_meta(&agent_files[5], 6, "lab_RUN042_deps",
			     "/lab/projects/lab-gene-x/runs/RUN-042/deps.txt",
			     "lab-gene-x", "nightly-regression", "RUN-042",
			     "workflow", "deps", "ok",
			     "prepare->align->analyze->report->archive",
			     AGENT_DEP_PREPARE | AGENT_DEP_ALIGN |
				     AGENT_DEP_ANALYZE | AGENT_DEP_REPORT |
				     AGENT_DEP_ARCHIVE);
	agent_file_fill_meta(&agent_files[6], 7,
			     "lab_RUN042_recovery_report",
			     "/lab/projects/lab-gene-x/reports/RUN-042-recovery.md",
			     "lab-gene-x", "nightly-regression", "RUN-042",
			     "report", "report", "pending",
			     "recovery report not written yet",
			     AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE);
	agent_file_rebuild_indexes();
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
}

static int agent_file_query_internal(struct agent_file_query *q,
				     struct agent_file_query_result *r)
{
	int cursor = -1;
	int *next = 0;
	int use_index = 0;
	int max_hits;
	uint64 start;

	memset(r, 0, sizeof(*r));
	max_hits = q->max_hits;
	if (max_hits <= 0 || max_hits > AGENT_FILE_QUERY_MAX_HITS)
		max_hits = AGENT_FILE_QUERY_MAX_HITS;
	start = agent_ticks();
	if ((q->flags & AGENT_FILE_QUERY_USE_INDEX) &&
	    !(q->flags & AGENT_FILE_QUERY_SCAN)) {
		if (q->status[0]) {
			cursor = agent_file_status_head[agent_bucket(q->status)];
			next = agent_file_status_next;
			use_index = 1;
		} else if (q->stage[0]) {
			cursor = agent_file_stage_head[agent_bucket(q->stage)];
			next = agent_file_stage_next;
			use_index = 1;
		} else if (q->kind[0]) {
			cursor = agent_file_kind_head[agent_bucket(q->kind)];
			next = agent_file_kind_next;
			use_index = 1;
		}
	}
	if (use_index) {
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
	r->query_ticks = agent_ticks() - start;
	return r->returned;
}

static int agent_file_find(char *selector)
{
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

static int agent_dependency_for_stage(char *stage, uint64 *mask)
{
	if (strncmp(stage, "prepare", AGENT_FILE_FIELD_SIZE) == 0) {
		*mask = AGENT_DEP_PREPARE | AGENT_DEP_ALIGN |
			AGENT_DEP_ANALYZE | AGENT_DEP_REPORT |
			AGENT_DEP_ARCHIVE;
		return 0;
	}
	if (strncmp(stage, "align", AGENT_FILE_FIELD_SIZE) == 0) {
		*mask = AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE |
			AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE;
		return 0;
	}
	if (strncmp(stage, "analyze", AGENT_FILE_FIELD_SIZE) == 0) {
		*mask = AGENT_DEP_ANALYZE | AGENT_DEP_REPORT |
			AGENT_DEP_ARCHIVE;
		return 0;
	}
	if (strncmp(stage, "report", AGENT_FILE_FIELD_SIZE) == 0) {
		*mask = AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE;
		return 0;
	}
	if (strncmp(stage, "archive", AGENT_FILE_FIELD_SIZE) == 0) {
		*mask = AGENT_DEP_ARCHIVE;
		return 0;
	}
	return -1;
}

static void agent_stage_text(uint64 mask, char *out, int n)
{
	if (mask == (AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE | AGENT_DEP_REPORT |
		     AGENT_DEP_ARCHIVE))
		safestrcpy(out, "align+analyze+report+archive", n);
	else if (mask == (AGENT_DEP_ANALYZE | AGENT_DEP_REPORT |
			  AGENT_DEP_ARCHIVE))
		safestrcpy(out, "analyze+report+archive", n);
	else if (mask == (AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE))
		safestrcpy(out, "report+archive", n);
	else if (mask == AGENT_DEP_ARCHIVE)
		safestrcpy(out, "archive", n);
	else
		safestrcpy(out, "prepare+align+analyze+report+archive", n);
}

static int agent_action_seen(uint64 corr_id)
{
	if (corr_id == 0)
		return 0;
	for (int i = 0; i < agent_action_history_count; i++)
		if (agent_action_history[i] == corr_id)
			return 1;
	return 0;
}

static void agent_action_remember(uint64 corr_id)
{
	if (corr_id == 0)
		return;
	if (agent_action_history_count < AGENT_ACTION_HISTORY_MAX)
		agent_action_history[agent_action_history_count++] = corr_id;
	else {
		memmove(agent_action_history, agent_action_history + 1,
			sizeof(agent_action_history[0]) *
				(AGENT_ACTION_HISTORY_MAX - 1));
		agent_action_history[AGENT_ACTION_HISTORY_MAX - 1] = corr_id;
	}
}

static void agent_wake_threads(struct proc *p)
{
	for (int i = 0; i < NTHREAD; i++) {
		if (p->threads[i].state == SLEEPING) {
			p->threads[i].state = RUNNABLE;
			add_task(&p->threads[i]);
		}
	}
}

static int agent_filter_matches(struct proc *target, int type, char *payload)
{
	if (!target->agent_watch_valid)
		return 0;
	if (target->agent_watch_event_type != AGENT_EVENT_NONE &&
	    target->agent_watch_event_type != type)
		return 0;
	if (target->agent_watch_filter[0] &&
	    !agent_contains(payload, target->agent_watch_filter))
		return 0;
	return 1;
}

static int agent_queue_event(struct proc *target, int source_pid, int type,
			     uint64 corr_id, char *payload)
{
	if (!target->is_agent || !agent_filter_matches(target, type, payload))
		return 0;
	if (target->agent_event_valid)
		target->agent_event_dropped++;
	target->agent_event_valid = 1;
	target->agent_event_type = type;
	target->agent_event_source_pid = source_pid;
	target->agent_event_id = next_event_id++;
	target->agent_event_tick = agent_ticks();
	target->agent_event_corr_id = corr_id;
	safestrcpy(target->agent_event_payload, payload,
		   sizeof(target->agent_event_payload));
	target->agent_event_count++;
	agent_wake_threads(target);
	return 1;
}

static int agent_deliver_pid(int pid, int source_pid, int type, uint64 corr_id,
			     char *payload)
{
	for (struct proc *p = pool; p < &pool[NPROC]; p++)
		if (p->state != P_UNUSED && p->pid == pid)
			return agent_queue_event(p, source_pid, type, corr_id,
						 payload);
	return 0;
}

static int agent_deliver_watchers(int source_pid, int type, uint64 corr_id,
				  char *payload)
{
	int delivered = 0;

	for (struct proc *p = pool; p < &pool[NPROC]; p++)
		if (p->state != P_UNUSED)
			delivered += agent_queue_event(p, source_pid, type,
						       corr_id, payload);
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
	if (agent_append_context(p, &op, &latest, agent_ticks()) < 0)
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

static void agent_parse_selector(char *payload, char *stage, int stage_n,
				 char *project, int project_n, char *run_id,
				 int run_id_n)
{
	struct agent_file_query query;

	memset(stage, 0, stage_n);
	memset(project, 0, project_n);
	memset(run_id, 0, run_id_n);
	if (agent_contains(payload, "=") || agent_contains(payload, ":")) {
		agent_query_from_payload(&query, payload);
		safestrcpy(stage, query.stage, stage_n);
		safestrcpy(project, query.project, project_n);
		safestrcpy(run_id, query.run_id, run_id_n);
	} else {
		safestrcpy(stage, payload, stage_n);
	}
}

static void agent_file_update_status_select(char *stage, char *project,
					    char *run_id, char *status,
					    char *summary)
{
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
		}
	}
	agent_file_rebuild_indexes();
}

static void agent_file_update_status(char *stage, char *status, char *summary)
{
	agent_file_update_status_select(stage, "", "", status, summary);
}

static void agent_query_from_payload(struct agent_file_query *q, char *payload)
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
			while (payload[i] && payload[i] != ';' &&
			       payload[i] != ',')
				i++;
			continue;
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
		else if (strncmp(key, "logical", sizeof(key)) == 0)
			safestrcpy(q->logical_path, val,
				   sizeof(q->logical_path));
		else if (strncmp(key, "project", sizeof(key)) == 0)
			safestrcpy(q->project, val, sizeof(q->project));
		else if (strncmp(key, "workflow", sizeof(key)) == 0)
			safestrcpy(q->workflow, val, sizeof(q->workflow));
		else if (strncmp(key, "run", sizeof(key)) == 0 ||
			 strncmp(key, "run_id", sizeof(key)) == 0)
			safestrcpy(q->run_id, val, sizeof(q->run_id));
		else if (strncmp(key, "stage", sizeof(key)) == 0)
			safestrcpy(q->stage, val, sizeof(q->stage));
		else if (strncmp(key, "kind", sizeof(key)) == 0)
			safestrcpy(q->kind, val, sizeof(q->kind));
		else if (strncmp(key, "status", sizeof(key)) == 0)
			safestrcpy(q->status, val, sizeof(q->status));
		else if (strncmp(key, "summary", sizeof(key)) == 0)
			safestrcpy(q->summary_contains, val,
				   sizeof(q->summary_contains));
	}
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
	char event_payload[AGENT_EVENT_PAYLOAD_SIZE];
	char selector_stage[AGENT_FILE_FIELD_SIZE];
	char selector_project[AGENT_FILE_PROJECT_SIZE];
	char selector_run_id[AGENT_FILE_FIELD_SIZE];

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
			agent_query_from_payload(&query, op->payload);
			agent_file_query_internal(&query, &query_result);
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
				target->agent_mailbox_valid = 1;
				target->agent_mailbox_from = p->pid;
				safestrcpy(target->agent_mailbox, op->payload,
					   sizeof(target->agent_mailbox));
				agent_deliver_pid((int)op->arg0, p->pid,
						  AGENT_EVENT_MESSAGE,
						  op->request_id,
						  op->payload);
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
		agent_file_install_default();
		agent_action_history_count = 0;
		res->value0 = AGENT_FILE_META_MAX;
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
	case AGENT_TOOL_DEPENDENCY_QUERY:
		if (!agent_has_cap(p, AGENT_CAP_META_READ)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (agent_dependency_for_stage(op->payload, &deps) < 0) {
			res->status = AGENT_STATUS_NOT_FOUND;
			agent_result_text(res, "stage_not_found");
			break;
		}
		res->value0 = deps;
		res->value1 = (deps & AGENT_DEP_PREPARE ? 1 : 0) +
			      (deps & AGENT_DEP_ALIGN ? 1 : 0) +
			      (deps & AGENT_DEP_ANALYZE ? 1 : 0) +
			      (deps & AGENT_DEP_REPORT ? 1 : 0) +
			      (deps & AGENT_DEP_ARCHIVE ? 1 : 0);
		agent_stage_text(deps, res->result, sizeof(res->result));
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
		if (!agent_has_cap(p, AGENT_CAP_RECOVER_STAGE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			agent_deliver_watchers(p->pid, AGENT_EVENT_POLICY_DENIED,
					       op->request_id,
					       "action=rerun_stage");
			break;
		}
		if (agent_action_seen(op->request_id)) {
			res->status = AGENT_STATUS_DUPLICATE;
			agent_result_text(res, "duplicate");
			break;
		}
		agent_parse_selector(op->payload, selector_stage,
				     sizeof(selector_stage), selector_project,
				     sizeof(selector_project), selector_run_id,
				     sizeof(selector_run_id));
		if (agent_dependency_for_stage(selector_stage, &deps) < 0) {
			res->status = AGENT_STATUS_NOT_FOUND;
			agent_result_text(res, "stage_not_found");
			break;
		}
		agent_file_update_status_select(selector_stage, selector_project,
						selector_run_id, "ok",
						"rerun completed");
		if (strncmp(selector_stage, "align", sizeof(selector_stage)) == 0) {
			agent_file_update_status_select("analyze",
							selector_project,
							selector_run_id, "ok",
							"rerun after align");
			agent_file_update_status_select("report",
							selector_project,
							selector_run_id, "ok",
							"report regenerated");
		}
		agent_action_remember(op->request_id);
		res->value0 = deps;
		res->value1 = op->request_id;
		agent_result_text(res, "rerun_ok");
		memset(event_payload, 0, sizeof(event_payload));
		agent_text_append(event_payload, sizeof(event_payload),
				  "status=ok;stage=");
		agent_text_append(event_payload, sizeof(event_payload),
				  selector_stage);
		if (selector_run_id[0]) {
			agent_text_append(event_payload, sizeof(event_payload),
					  ";run_id=");
			agent_text_append(event_payload, sizeof(event_payload),
					  selector_run_id);
		}
		agent_text_append(event_payload, sizeof(event_payload),
				  ";action=rerun");
		delivered = agent_deliver_watchers(p->pid, AGENT_EVENT_JOB_DONE,
						   op->request_id,
						   event_payload);
		res->value2 = delivered;
		break;
	case AGENT_TOOL_WRITE_REPORT:
		if (!agent_has_cap(p, AGENT_CAP_REPORT_WRITE)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (agent_contains(op->payload, "=") ||
		    agent_contains(op->payload, ":")) {
			agent_parse_selector(op->payload, selector_stage,
					     sizeof(selector_stage),
					     selector_project,
					     sizeof(selector_project),
					     selector_run_id,
					     sizeof(selector_run_id));
			if (!selector_stage[0])
				safestrcpy(selector_stage, "report",
					   sizeof(selector_stage));
			agent_file_update_status_select(selector_stage,
							selector_project,
							selector_run_id, "ok",
							"recovery report written");
		} else {
			agent_file_update_status("report", "ok",
						 "recovery report written");
		}
		res->value0 = op->request_id;
		agent_result_text(res, "report_written");
		break;
	case AGENT_TOOL_AGENT_WATCH:
		if (!agent_has_cap(p, AGENT_CAP_WATCH)) {
			res->status = AGENT_STATUS_DENIED;
			agent_result_text(res, "denied");
			break;
		}
		if (op->arg0 > AGENT_EVENT_CONTEXT_LIMIT) {
			res->status = AGENT_STATUS_BAD_PARAM;
			agent_result_text(res, "bad_event_type");
			break;
		}
		p->agent_watch_valid = 1;
		p->agent_watch_event_type = op->arg0;
		safestrcpy(p->agent_watch_filter, op->payload,
			   sizeof(p->agent_watch_filter));
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
	op->payload[AGENT_OP_PAYLOAD_SIZE - 1] = 0;
	agent_result_init(res, op);
	p->agent_call_count++;
	res->sequence = p->agent_call_count;
	agent_execute_op(p, op, res);
	return agent_append_context(p, op, res, tick);
}

static int agent_user_range_mapped(struct proc *p, uint64 addr, uint64 len)
{
	uint64 end;

	if (len == 0)
		return 1;
	end = addr + len;
	if (end < addr)
		return 0;
	for (uint64 a = PGROUNDDOWN(addr); a < end; a += PAGE_SIZE)
		if (walkaddr(p->pagetable, a) == 0)
			return 0;
	return 1;
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
	for (int i = 0; i < count; i++)
		if (copyin(p->pagetable, (char *)&op,
			   opsaddr + i * sizeof(struct agent_op),
			   sizeof(op)) < 0)
			return -1;
	if (!agent_user_range_mapped(
		    p, resultsaddr,
		    (uint64)count * sizeof(struct agent_result)))
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
	if (agent_append_context(p, &op, &latest, agent_ticks()) < 0)
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
	    event_type > AGENT_EVENT_CONTEXT_LIMIT)
		return AGENT_STATUS_BAD_PARAM;
	memset(filter, 0, sizeof(filter));
	if (filteraddr != 0 &&
	    copyinstr(p->pagetable, filter, filteraddr, sizeof(filter)) < 0)
		return -1;
	p->agent_watch_valid = 1;
	p->agent_watch_event_type = event_type;
	safestrcpy(p->agent_watch_filter, filter, sizeof(p->agent_watch_filter));
	p->loop_state = AGENT_LOOP_IDLE;
	agent_append_system_context(p, AGENT_TOOL_AGENT_WATCH, 0, event_type,
				    filter, "watch", AGENT_STATUS_OK,
				    event_type, 0, 0);
	return 0;
}

int sys_agent_wait(uint64 eventaddr, int timeout_ticks)
{
	struct proc *p = curr_proc();
	struct thread *t = curr_thread();
	struct agent_event event;
	uint64 start = agent_ticks();
	uint64 now;
	int status;

	if (!p->is_agent)
		return -1;
	if (eventaddr && !agent_user_range_mapped(p, eventaddr, sizeof(event)))
		return -1;
	memset(&event, 0, sizeof(event));
	p->agent_wait_count++;
	for (;;) {
		if (p->agent_event_valid) {
			event.type = p->agent_event_type;
			event.source_pid = p->agent_event_source_pid;
			event.target_pid = p->pid;
			event.status = AGENT_STATUS_OK;
			event.event_id = p->agent_event_id;
			event.tick = p->agent_event_tick;
			event.corr_id = p->agent_event_corr_id;
			safestrcpy(event.payload, p->agent_event_payload,
				   sizeof(event.payload));
			p->agent_event_valid = 0;
			p->loop_state = AGENT_LOOP_RUNNING;
			status = AGENT_STATUS_OK;
			break;
		}
		now = agent_ticks();
		if (timeout_ticks >= 0 && now - start >= (uint64)timeout_ticks) {
			p->agent_timeout_count++;
			p->loop_state = AGENT_LOOP_IDLE;
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
			yield();
			continue;
		}
		t->state = SLEEPING;
		sched();
	}
	if (eventaddr &&
	    copyout(p->pagetable, eventaddr, (char *)&event,
		    sizeof(event)) < 0)
		return -1;
	if (status == AGENT_STATUS_OK)
		agent_append_system_context(p, AGENT_TOOL_AGENT_WAIT,
					    event.event_id, event.type,
					    event.payload, "event",
					    AGENT_STATUS_OK, event.type,
					    event.source_pid, event.corr_id);
	p->loop_state = AGENT_LOOP_IDLE;
	return status;
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
	if (!agent_deliver_pid(pid, p->pid, event.type, event.corr_id,
			       payload))
		return AGENT_STATUS_NOT_FOUND;
	return 0;
}

int sys_agent_file_meta_init(void)
{
	struct proc *p = curr_proc();

	if (!p->is_agent)
		return -1;
	if (!agent_has_cap(p, AGENT_CAP_META_WRITE))
		return AGENT_STATUS_DENIED;
	agent_action_history_count = 0;
	agent_file_install_default();
	return 0;
}

int sys_agent_file_meta_set(uint64 metaaddr)
{
	struct proc *p = curr_proc();
	struct agent_file_meta meta;
	int slot = -1;
	int status_changed = 0;
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
	if (meta.physical_name[0])
		safestrcpy(agent_files[slot].physical_name, meta.physical_name,
			   sizeof(agent_files[slot].physical_name));
	if (meta.logical_path[0])
		safestrcpy(agent_files[slot].logical_path, meta.logical_path,
			   sizeof(agent_files[slot].logical_path));
	if (meta.project[0])
		safestrcpy(agent_files[slot].project, meta.project,
			   sizeof(agent_files[slot].project));
	if (meta.workflow[0])
		safestrcpy(agent_files[slot].workflow, meta.workflow,
			   sizeof(agent_files[slot].workflow));
	if (meta.run_id[0])
		safestrcpy(agent_files[slot].run_id, meta.run_id,
			   sizeof(agent_files[slot].run_id));
	if (meta.stage[0])
		safestrcpy(agent_files[slot].stage, meta.stage,
			   sizeof(agent_files[slot].stage));
	if (meta.kind[0])
		safestrcpy(agent_files[slot].kind, meta.kind,
			   sizeof(agent_files[slot].kind));
	if (meta.status[0]) {
		if (strncmp(agent_files[slot].status, meta.status,
			    sizeof(meta.status)) != 0)
			status_changed = 1;
		safestrcpy(agent_files[slot].status, meta.status,
			   sizeof(agent_files[slot].status));
	}
	if (meta.summary[0])
		safestrcpy(agent_files[slot].summary, meta.summary,
			   sizeof(agent_files[slot].summary));
	if (meta.dependency_mask)
		agent_files[slot].dependency_mask = meta.dependency_mask;
	agent_files[slot].updated_tick = agent_ticks();
	agent_file_rebuild_indexes();
	agent_file_event_payload(&agent_files[slot], event_payload,
				 sizeof(event_payload));
	if (status_changed && meta.status[0])
		agent_deliver_watchers(p->pid, AGENT_EVENT_FILE_STATUS,
				       meta.fid, event_payload);
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
	if (!agent_user_range_mapped(p, resultaddr, sizeof(result)))
		return -1;
	returned = agent_file_query_internal(&query, &result);
	if (copyout(p->pagetable, resultaddr, (char *)&result,
		    sizeof(result)) < 0)
		return -1;
	agent_append_system_context(
		p, AGENT_TOOL_QUERY_FILE, 0, query.flags,
		query.status[0] ? query.status : query.stage,
		result.returned ? result.hits[0].physical_name : "empty",
		AGENT_STATUS_OK, result.total_hits, result.scanned_records,
		result.used_index);
	return returned;
}

int sys_agent_call(uint64 reqaddr, uint64 respaddr)
{
	struct proc *p = curr_proc();
	struct agent_request req;
	struct agent_response resp;
	struct agent_op op;
	struct agent_result res;
	struct agent_tool_desc *tool;

	if (copyin(p->pagetable, (char *)&req, reqaddr, sizeof(req)) < 0)
		return -1;
	if (!agent_user_range_mapped(p, respaddr, sizeof(resp)))
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

	for (struct proc *p = pool; p < &pool[NPROC]; p++) {
		if (p->state == P_UNUSED || !p->is_agent)
			continue;
		if (p->loop_state == AGENT_LOOP_WAITING)
			agent_wake_threads(p);
		if (p->heartbeat_interval > 0 &&
		    now - p->agent_last_heartbeat_tick >=
			    (uint64)p->heartbeat_interval) {
			p->agent_last_heartbeat_tick = now;
			safestrcpy(payload, "timer=heartbeat", sizeof(payload));
			agent_queue_event(p, 0, AGENT_EVENT_TIMER, now, payload);
		}
	}
}
