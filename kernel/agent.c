// SPDX-License-Identifier: Apache-2.0

#include "types.h"
#include "param.h"
#include "fs.h"
#include "memlayout.h"
#include "riscv.h"
#include "spinlock.h"
#include "sleeplock.h"
#include "file.h"
#include "proc.h"
#include "defs.h"
#include "stat.h"
#include "agent.h"

extern struct proc proc[NPROC];

static int nextagentid = 1;
static struct spinlock agentid_lock;
static struct spinlock agent_event_lock;
static struct spinlock agent_file_lock;
static struct spinlock agent_action_lock;

#define AGENT_FILE_INDEX_BUCKETS 16
#define AGENT_ACTION_HISTORY_MAX 32
#define AGENT_FILE_DEFAULT_USED 112
#define AGENT_DEMO_PROJECT "lab-gene-x"
#define AGENT_DEMO_WORKFLOW "nightly-regression"
#define AGENT_DEMO_RUN "RUN-042"

static struct agent_tool_desc agent_tools[] = {
  {AGENT_TOOL_ECHO, "echo", "payload:string,arg0:uint64,arg1:uint64",
   "return the payload and numeric parameters"},
  {AGENT_TOOL_PID_INFO, "pid_info", "none",
   "return the current pid, agent id, and agent flag"},
  {AGENT_TOOL_CTX_STAT, "ctx_stat", "none",
   "return Agent Context base, size, and call count"},
  {AGENT_TOOL_QUERY_PROCESS, "query_process", "type:uint64",
   "count processes, optionally filtering agent processes"},
  {AGENT_TOOL_GET_SYSTEM_STATUS, "get_system_status", "none",
   "return used process count, agent count, and uptime ticks"},
  {AGENT_TOOL_READ_CONTEXT, "read_context", "none",
   "return Context Path count, head slot, and total calls"},
  {AGENT_TOOL_QUERY_FILE, "query_file", "path:string|fid:uint64|filters",
   "query a path, fid, or Agent file metadata filters"},
  {AGENT_TOOL_SEND_MESSAGE, "send_message", "target_pid:uint64,message:string",
   "store a short message in another Agent process mailbox"},
  {AGENT_TOOL_READ_MESSAGE, "read_message", "none",
   "read the current Agent process mailbox"},
  {AGENT_TOOL_FILE_META_INIT, "file_meta_init", "none",
   "initialize Agent-OS lab artifact metadata"},
  {AGENT_TOOL_READ_FILE_SUMMARY, "read_file_summary", "selector:string",
   "return a short artifact summary by logical path or stage"},
  {AGENT_TOOL_DEPENDENCY_QUERY, "dependency_query", "stage:string",
   "return affected workflow stages for a changed stage"},
  {AGENT_TOOL_CAPABILITY_CHECK, "capability_check",
   "action:string", "check current Agent capability for side effects"},
  {AGENT_TOOL_RERUN_STAGE, "rerun_stage", "stage:string",
   "perform an idempotent controlled rerun for an affected stage"},
  {AGENT_TOOL_WRITE_REPORT, "write_report", "payload:string",
   "mark the recovery report artifact in Agent metadata"},
  {AGENT_TOOL_AGENT_WATCH, "agent_watch", "event_type:uint64,filter:string",
   "register an Agent Loop watch filter"},
  {AGENT_TOOL_AGENT_WAIT, "agent_wait", "timeout:uint64",
   "syscall-only wait; agent_run returns use_agent_wait_syscall"},
  {AGENT_TOOL_AGENT_HEARTBEAT, "agent_heartbeat", "interval:uint64",
   "set heartbeat interval and update last heartbeat tick"},
};

static struct agent_file_meta agent_files[AGENT_FILE_META_MAX];
static int agent_file_status_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_stage_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_kind_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_run_head[AGENT_FILE_INDEX_BUCKETS];
static int agent_file_status_next[AGENT_FILE_META_MAX];
static int agent_file_stage_next[AGENT_FILE_META_MAX];
static int agent_file_kind_next[AGENT_FILE_META_MAX];
static int agent_file_run_next[AGENT_FILE_META_MAX];

static uint64 agent_next_event_id = 1;
struct agent_action_key {
  uint64 corr_id;
  char project[AGENT_FILE_PROJECT_SIZE];
  char workflow[AGENT_FILE_WORKFLOW_SIZE];
  char run_id[AGENT_FILE_FIELD_SIZE];
  char stage[AGENT_FILE_FIELD_SIZE];
  char action[AGENT_FILE_FIELD_SIZE];
};

struct agent_scope {
  char project[AGENT_FILE_PROJECT_SIZE];
  char workflow[AGENT_FILE_WORKFLOW_SIZE];
  char run_id[AGENT_FILE_FIELD_SIZE];
  char stage[AGENT_FILE_FIELD_SIZE];
  char summary[AGENT_FILE_SUMMARY_SIZE];
};

static struct agent_action_key agent_action_history[AGENT_ACTION_HISTORY_MAX];
static int agent_action_history_count;

struct agent_context_prefix {
  struct agent_context_header header;
  struct agent_result latest;
};

struct agent_proc_snapshot {
  int used;
  int agents;
  int runnable;
};

static uint64 agent_ticks(void);
int agent_file_meta_init(void);
int agent_file_meta_set(uint64);
int agent_file_query(uint64, uint64);
int agent_watch(int, uint64);
int agent_wait(uint64, int);
int agent_heartbeat(int);
int agent_set_role(int);

void
agentinit(void)
{
  initlock(&agentid_lock, "nextagentid");
  initlock(&agent_event_lock, "agent_event");
  initlock(&agent_file_lock, "agent_file");
  initlock(&agent_action_lock, "agent_action");
}

static int
agent_alloc_id(void)
{
  int agent_id;

  acquire(&agentid_lock);
  agent_id = nextagentid;
  nextagentid = nextagentid + 1;
  release(&agentid_lock);

  return agent_id;
}

static uint64
agent_caps_for_role(int role)
{
  switch (role) {
  case AGENT_ROLE_SENTINEL:
  case AGENT_ROLE_INVESTIGATOR:
    return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
           AGENT_CAP_PROCESS_READ | AGENT_CAP_MESSAGE_SEND |
           AGENT_CAP_WATCH;
  case AGENT_ROLE_RECOVERY:
    return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
           AGENT_CAP_PROCESS_READ | AGENT_CAP_MESSAGE_SEND |
           AGENT_CAP_WATCH | AGENT_CAP_RECOVER_STAGE |
           AGENT_CAP_REPORT_WRITE;
  case AGENT_ROLE_ORCHESTRATOR:
    return AGENT_CAP_META_READ | AGENT_CAP_CONTENT_READ |
           AGENT_CAP_PROCESS_READ | AGENT_CAP_MESSAGE_SEND |
           AGENT_CAP_WATCH | AGENT_CAP_RECOVER_STAGE |
           AGENT_CAP_REPORT_WRITE | AGENT_CAP_AUDIT_WRITE |
           AGENT_CAP_EVENT_WAKE | AGENT_CAP_META_WRITE;
  default:
    return 0;
  }
}

static int
agent_has_cap(struct proc *p, uint64 cap)
{
  return p->is_agent && (p->agent_capability_mask & cap) == cap;
}

void
agent_free_shadow_context(uint64 *shadow_kva)
{
  int i;

  for (i = 0; i < AGENT_CONTEXT_PAGES; i++) {
    if (shadow_kva[i]) {
      kfree((void *)shadow_kva[i]);
      shadow_kva[i] = 0;
    }
  }
}

void
agent_clear_metadata(struct proc *p)
{
  int i;

  agent_free_shadow_context(p->agent_shadow_kva);

  p->is_agent = 0;
  p->agent_type = AGENT_TYPE_NONE;
  p->agent_role = 0;
  p->agent_id = 0;
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
  for (i = 0; i < AGENT_CONTEXT_PAGES; i++) {
    p->agent_ctx_kva[i] = 0;
    p->agent_shadow_kva[i] = 0;
  }
  p->agent_mailbox_valid = 0;
  p->agent_mailbox_from = 0;
  memset(p->agent_mailbox, 0, sizeof(p->agent_mailbox));
  acquire(&agent_event_lock);
  p->agent_watch_valid = 0;
  p->agent_watch_event_type = AGENT_EVENT_NONE;
  memset(p->agent_watch_filter, 0, sizeof(p->agent_watch_filter));
  p->agent_event_head = 0;
  p->agent_event_tail = 0;
  p->agent_event_queued = 0;
  memset(p->agent_event_type, 0, sizeof(p->agent_event_type));
  memset(p->agent_event_source_pid, 0, sizeof(p->agent_event_source_pid));
  memset(p->agent_event_id, 0, sizeof(p->agent_event_id));
  memset(p->agent_event_tick, 0, sizeof(p->agent_event_tick));
  memset(p->agent_event_corr_id, 0, sizeof(p->agent_event_corr_id));
  memset(p->agent_event_payload, 0, sizeof(p->agent_event_payload));
  p->agent_event_count = 0;
  p->agent_event_dropped = 0;
  p->agent_wait_count = 0;
  p->agent_timeout_count = 0;
  p->agent_wait_deadline = 0;
  p->agent_last_heartbeat_tick = 0;
  release(&agent_event_lock);
  p->agent_capability_mask = 0;
}

static int
agent_context_layout_ok(void)
{
  if (AGENT_CONTEXT_LATEST_RESPONSE_OFFSET !=
      sizeof(struct agent_context_header))
    return 0;
  if (AGENT_CONTEXT_RECORDS_OFFSET != PGSIZE)
    return 0;
  if (AGENT_CONTEXT_RECORDS_OFFSET + AGENT_CONTEXT_MAX_RECORDS *
                                         sizeof(struct agent_context_record) >
      AGENT_CONTEXT_SIZE)
    return 0;
  return 1;
}

int
agent_prepare_context(pagetable_t pagetable, uint64 *ctx_kva,
                      uint64 *shadow_kva)
{
  char *mem;
  char *shadow;
  uint64 va;
  int mapped = 0;
  int i;

  for (i = 0; i < AGENT_CONTEXT_PAGES; i++) {
    ctx_kva[i] = 0;
    shadow_kva[i] = 0;
  }

  for (va = AGENT_CONTEXT_BASE; va < AGENT_CONTEXT_BASE + AGENT_CONTEXT_SIZE;
       va += PGSIZE) {
    mem = kalloc();
    if (mem == 0) {
      goto bad;
    }
    shadow = kalloc();
    if (shadow == 0) {
      kfree(mem);
      goto bad;
    }
    memset(mem, 0, PGSIZE);
    memset(shadow, 0, PGSIZE);
    if (mappages(pagetable, va, PGSIZE, (uint64)mem,
                 PTE_R | PTE_W | PTE_U) != 0) {
      kfree(mem);
      kfree(shadow);
      goto bad;
    }
    ctx_kva[mapped] = (uint64)mem;
    shadow_kva[mapped] = (uint64)shadow;
    mapped++;
  }

  return 0;

bad:
  if (mapped > 0) {
    uvmunmap(pagetable, AGENT_CONTEXT_BASE, mapped, 1);
  }
  agent_free_shadow_context(shadow_kva);
  for (i = 0; i < AGENT_CONTEXT_PAGES; i++)
    ctx_kva[i] = 0;
  return -1;
}

void
agent_discard_prepared_context(pagetable_t pagetable, uint64 *shadow_kva)
{
  (void)pagetable;
  agent_free_shadow_context(shadow_kva);
}

void
agent_install_context(struct proc *p, uint64 *ctx_kva, uint64 *shadow_kva)
{
  int i;

  for (i = 0; i < AGENT_CONTEXT_PAGES; i++) {
    p->agent_ctx_kva[i] = ctx_kva[i];
    p->agent_shadow_kva[i] = shadow_kva[i];
    ctx_kva[i] = 0;
    shadow_kva[i] = 0;
  }
}

int
agent_map_context(struct proc *p, pagetable_t pagetable)
{
  uint64 ctx_kva[AGENT_CONTEXT_PAGES];
  uint64 shadow_kva[AGENT_CONTEXT_PAGES];

  if (agent_prepare_context(pagetable, ctx_kva, shadow_kva) < 0)
    return -1;
  agent_install_context(p, ctx_kva, shadow_kva);
  return 0;
}

static char *
agent_context_array_ptr(uint64 *kva, uint64 offset, uint64 len)
{
  uint64 page;
  uint64 page_offset;

  if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
    return 0;
  page = offset / PGSIZE;
  page_offset = offset % PGSIZE;
  if (page >= AGENT_CONTEXT_PAGES || page_offset + len > PGSIZE)
    return 0;
  if (kva[page] == 0)
    return 0;
  return (char *)(kva[page] + page_offset);
}

static int
agent_context_array_read(uint64 *kva, uint64 offset, char *dst, uint64 len)
{
  uint64 page;
  uint64 page_offset;
  uint64 n;

  if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
    return -1;
  while (len > 0) {
    page = offset / PGSIZE;
    page_offset = offset % PGSIZE;
    if (page >= AGENT_CONTEXT_PAGES || kva[page] == 0)
      return -1;
    n = PGSIZE - page_offset;
    if (n > len)
      n = len;
    memmove(dst, (char *)(kva[page] + page_offset), n);
    dst += n;
    offset += n;
    len -= n;
  }
  return 0;
}

static int
agent_context_array_write(uint64 *kva, uint64 offset, char *src, uint64 len)
{
  uint64 page;
  uint64 page_offset;
  uint64 n;

  if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
    return -1;
  while (len > 0) {
    page = offset / PGSIZE;
    page_offset = offset % PGSIZE;
    if (page >= AGENT_CONTEXT_PAGES || kva[page] == 0)
      return -1;
    n = PGSIZE - page_offset;
    if (n > len)
      n = len;
    memmove((char *)(kva[page] + page_offset), src, n);
    src += n;
    offset += n;
    len -= n;
  }
  return 0;
}

static char *
agent_context_ptr(struct proc *p, uint64 offset, uint64 len)
{
  return agent_context_array_ptr(p->agent_shadow_kva, offset, len);
}

static char *
agent_mirror_context_ptr(struct proc *p, uint64 offset, uint64 len)
{
  return agent_context_array_ptr(p->agent_ctx_kva, offset, len);
}

static int
agent_sync_context_range(struct proc *p, uint64 offset, uint64 len)
{
  char *src;
  char *dst;
  uint64 page_offset;
  uint64 n;

  if (offset + len < offset || offset + len > AGENT_CONTEXT_SIZE)
    return -1;
  while (len > 0) {
    page_offset = offset % PGSIZE;
    n = PGSIZE - page_offset;
    if (n > len)
      n = len;
    src = agent_context_ptr(p, offset, n);
    dst = agent_mirror_context_ptr(p, offset, n);
    if (src == 0 || dst == 0)
      return -1;
    memmove(dst, src, n);
    offset += n;
    len -= n;
  }
  return 0;
}

static int
agent_sync_context_all(struct proc *p)
{
  int i;

  for (i = 0; i < AGENT_CONTEXT_PAGES; i++) {
    if (p->agent_shadow_kva[i] == 0 || p->agent_ctx_kva[i] == 0)
      return -1;
    memmove((void *)p->agent_ctx_kva[i], (void *)p->agent_shadow_kva[i],
            PGSIZE);
  }
  return 0;
}

static struct agent_context_header *
agent_context_header_ptr(struct proc *p)
{
  return (struct agent_context_header *)agent_context_ptr(
      p, AGENT_CONTEXT_HEADER_OFFSET, sizeof(struct agent_context_header));
}

static struct agent_result *
agent_latest_result_ptr(struct proc *p)
{
  return (struct agent_result *)agent_context_ptr(
      p, AGENT_CONTEXT_LATEST_RESPONSE_OFFSET, sizeof(struct agent_result));
}

static uint64
agent_context_record_offset(struct proc *p, uint64 slot)
{
  return p->records_offset + slot * sizeof(struct agent_context_record);
}

static int
agent_read_context_record(struct proc *p, uint64 slot,
                          struct agent_context_record *record)
{
  if (slot >= p->context_path_capacity)
    return -1;
  return agent_context_array_read(
      p->agent_shadow_kva, agent_context_record_offset(p, slot),
      (char *)record, sizeof(*record));
}

static int
agent_write_context_record(struct proc *p, uint64 slot,
                           struct agent_context_record *record)
{
  uint64 offset;

  if (slot >= p->context_path_capacity)
    return -1;
  offset = agent_context_record_offset(p, slot);
  if (agent_context_array_write(p->agent_shadow_kva, offset, (char *)record,
                                sizeof(*record)) < 0)
    return -1;
  return agent_sync_context_range(p, offset, sizeof(*record));
}

static void
agent_fill_header(struct proc *p, struct agent_context_header *header)
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

static int
agent_write_header(struct proc *p)
{
  struct agent_context_header *header;

  header = agent_context_header_ptr(p);
  if (header == 0)
    return -1;
  agent_fill_header(p, header);
  return agent_sync_context_range(p, AGENT_CONTEXT_HEADER_OFFSET,
                                  sizeof(*header));
}

static int
agent_write_latest_result(struct proc *p, struct agent_result *latest)
{
  struct agent_result *dst;

  dst = agent_latest_result_ptr(p);
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

int
agent_init_context(struct proc *p, pagetable_t pagetable)
{
  int i;

  (void)pagetable;

  if (!agent_context_layout_ok())
    return -1;

  for (i = 0; i < AGENT_CONTEXT_PAGES; i++) {
    if (p->agent_shadow_kva[i] == 0 || p->agent_ctx_kva[i] == 0)
      return -1;
    memset((void *)p->agent_shadow_kva[i], 0, PGSIZE);
    memset((void *)p->agent_ctx_kva[i], 0, PGSIZE);
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
  if (agent_write_latest_result(p, 0) < 0)
    return -1;

  return 0;
}

int
agent_make(struct proc *p, pagetable_t pagetable, int role)
{
  uint64 caps;

  if (p->sz > AGENT_CONTEXT_BASE)
    return -1;
  caps = agent_caps_for_role(role);
  if (caps == 0)
    return -1;
  if (agent_map_context(p, pagetable) < 0)
    return -1;

  p->is_agent = 1;
  p->agent_type = AGENT_TYPE_AGENT;
  p->agent_role = role;
  p->agent_id = agent_alloc_id();
  p->agent_ctx_base = AGENT_CONTEXT_BASE;
  p->agent_ctx_size = AGENT_CONTEXT_SIZE;
  p->heartbeat_interval = AGENT_DEFAULT_HEARTBEAT_INTERVAL;
  p->resource_quota = AGENT_DEFAULT_RESOURCE_QUOTA;
  p->loop_state = AGENT_LOOP_IDLE;
  p->agent_capability_mask = caps;
  p->agent_last_heartbeat_tick = agent_ticks();

  if (agent_init_context(p, pagetable) < 0) {
    agent_free_shadow_context(p->agent_shadow_kva);
    return -1;
  }

  return 0;
}

int
agent_info_copyout(uint64 addr)
{
  struct proc *p = myproc();
  struct agent_info info;

  info.is_agent = p->is_agent;
  info.agent_id = p->agent_id;
  info.context_base = p->agent_ctx_base;
  info.context_size = p->agent_ctx_size;
  info.agent_type = p->agent_type;
  info.agent_role = p->agent_role;
  info.heartbeat_interval = p->heartbeat_interval;
  info.resource_quota = p->resource_quota;
  info.loop_state = p->loop_state;
  info.agent_call_count = p->agent_call_count;
  info.context_path_count = p->context_path_count;
  info.context_path_capacity = p->context_path_capacity;
  info.context_path_head = p->context_path_head;
  info.context_path_oldest = p->context_path_oldest;
  info.context_path_latest = p->context_path_latest;
  info.context_path_dropped = p->context_path_dropped;
  info.context_path_rollback_count = p->context_path_rollback_count;
  info.latest_response_offset = p->latest_response_offset;
  info.records_offset = p->records_offset;
  info.event_count = p->agent_event_count;
  info.event_dropped = p->agent_event_dropped;
  info.wait_count = p->agent_wait_count;
  info.timeout_count = p->agent_timeout_count;
  info.last_heartbeat_tick = p->agent_last_heartbeat_tick;
  info.capability_mask = p->agent_capability_mask;

  if (copyout(p->pagetable, addr, (char *)&info, sizeof(info)) < 0)
    return -1;

  return 0;
}

static int
agent_text_empty(char *s)
{
  return s[0] == 0;
}

static int
agent_key_is(char *key, char *expected)
{
  return strncmp(key, expected, AGENT_PARAM_KEY_SIZE) == 0;
}

static int
agent_has_arg0(struct agent_request *req)
{
  return !agent_text_empty(req->arg0_key) ||
         req->arg0_type != AGENT_PARAM_NONE;
}

static int
agent_has_arg1(struct agent_request *req)
{
  return !agent_text_empty(req->arg1_key) ||
         req->arg1_type != AGENT_PARAM_NONE;
}

static int
agent_has_payload(struct agent_request *req)
{
  return !agent_text_empty(req->payload_key) ||
         req->payload_type != AGENT_PARAM_NONE || !agent_text_empty(req->payload);
}

static void
agent_bad_param(struct agent_response *resp, char *text)
{
  resp->status = AGENT_STATUS_BAD_PARAM;
  safestrcpy(resp->result, text, sizeof(resp->result));
}

static int
agent_check_arg0(struct agent_request *req, struct agent_response *resp,
                 char *key)
{
  if (!agent_key_is(req->arg0_key, key)) {
    agent_bad_param(resp, "bad_arg0_key");
    return -1;
  }
  if (req->arg0_type != AGENT_PARAM_UINT64) {
    agent_bad_param(resp, "bad_arg0_type");
    return -1;
  }
  return 0;
}

static int
agent_check_arg1(struct agent_request *req, struct agent_response *resp,
                 char *key)
{
  if (!agent_key_is(req->arg1_key, key)) {
    agent_bad_param(resp, "bad_arg1_key");
    return -1;
  }
  if (req->arg1_type != AGENT_PARAM_UINT64) {
    agent_bad_param(resp, "bad_arg1_type");
    return -1;
  }
  return 0;
}

static int
agent_check_payload(struct agent_request *req, struct agent_response *resp,
                    char *key)
{
  if (!agent_key_is(req->payload_key, key)) {
    agent_bad_param(resp, "bad_payload_key");
    return -1;
  }
  if (req->payload_type != AGENT_PARAM_STRING) {
    agent_bad_param(resp, "bad_payload_type");
    return -1;
  }
  return 0;
}

static int
agent_check_no_args(struct agent_request *req, struct agent_response *resp)
{
  if (agent_has_arg0(req) || agent_has_arg1(req) || agent_has_payload(req)) {
    agent_bad_param(resp, "unexpected_param");
    return -1;
  }
  return 0;
}

static struct agent_tool_desc *
agent_tool_by_id(int tool_id)
{
  if (tool_id <= 0 || tool_id > AGENT_TOOL_COUNT)
    return 0;
  if (agent_tools[tool_id - 1].tool_id != tool_id)
    return 0;
  return &agent_tools[tool_id - 1];
}

static struct agent_tool_desc *
agent_tool_by_name(char *name)
{
  int i;

  for (i = 0; i < AGENT_TOOL_COUNT; i++)
    if (strncmp(agent_tools[i].name, name, AGENT_TOOL_NAME_SIZE) == 0)
      return &agent_tools[i];
  return 0;
}

static struct agent_tool_desc *
agent_resolve_tool(struct agent_request *req, struct agent_response *resp)
{
  struct agent_tool_desc *by_id = 0;
  struct agent_tool_desc *by_name = 0;

  if (req->tool_id != 0)
    by_id = agent_tool_by_id(req->tool_id);
  if (!agent_text_empty(req->tool_name)) {
    if (by_id) {
      if (strncmp(by_id->name, req->tool_name, AGENT_TOOL_NAME_SIZE) != 0) {
        resp->status = AGENT_STATUS_BAD_REQUEST;
        safestrcpy(resp->result, "tool_id_name_mismatch",
                   sizeof(resp->result));
        return 0;
      }
      return by_id;
    }
    by_name = agent_tool_by_name(req->tool_name);
  }

  if (by_name)
    return by_name;
  if (by_id)
    return by_id;

  resp->status = AGENT_STATUS_UNKNOWN_TOOL;
  safestrcpy(resp->result, "unknown_tool", sizeof(resp->result));
  return 0;
}

static int
agent_validate_legacy_params(struct agent_request *req,
                             struct agent_response *resp, int tool_id)
{
  switch (tool_id) {
  case AGENT_TOOL_ECHO:
    if (agent_check_payload(req, resp, "payload") < 0 ||
        agent_check_arg0(req, resp, "arg0") < 0 ||
        agent_check_arg1(req, resp, "arg1") < 0)
      return -1;
    return 0;
  case AGENT_TOOL_PID_INFO:
  case AGENT_TOOL_CTX_STAT:
  case AGENT_TOOL_GET_SYSTEM_STATUS:
  case AGENT_TOOL_READ_CONTEXT:
  case AGENT_TOOL_READ_MESSAGE:
    return agent_check_no_args(req, resp);
  case AGENT_TOOL_QUERY_PROCESS:
    if (agent_has_payload(req) || agent_has_arg1(req)) {
      agent_bad_param(resp, "unexpected_param");
      return -1;
    }
    if (agent_has_arg0(req))
      return agent_check_arg0(req, resp, "type");
    return 0;
  case AGENT_TOOL_QUERY_FILE:
    if (agent_has_arg0(req) || agent_has_arg1(req)) {
      agent_bad_param(resp, "unexpected_param");
      return -1;
    }
    return agent_check_payload(req, resp, "path");
  case AGENT_TOOL_SEND_MESSAGE:
    if (agent_has_arg1(req)) {
      agent_bad_param(resp, "unexpected_param");
      return -1;
    }
    if (agent_check_arg0(req, resp, "target_pid") < 0 ||
        agent_check_payload(req, resp, "message") < 0)
      return -1;
    return 0;
  default:
    return 0;
  }
}

static int
agent_user_range_writable(struct proc *p, uint64 addr, uint64 len)
{
  uint64 end;
  uint64 a;
  pte_t *pte;

  if (len == 0)
    return 1;
  end = addr + len;
  if (end < addr)
    return 0;
  for (a = PGROUNDDOWN(addr); a < end; a += PGSIZE) {
    if (a >= MAXVA)
      return 0;
    pte = walk(p->pagetable, a, 0);
    if (pte == 0 || (*pte & PTE_V) == 0) {
      if (vmfault(p->pagetable, a, 0) == 0)
        return 0;
      pte = walk(p->pagetable, a, 0);
    }
    if (pte == 0)
      return 0;
    if ((*pte & PTE_V) == 0 || (*pte & PTE_U) == 0 ||
        (*pte & PTE_W) == 0)
      return 0;
  }
  return 1;
}

static void
agent_init_response(struct agent_response *resp, struct agent_request *req,
                    struct agent_tool_desc *tool)
{
  memset(resp, 0, sizeof(*resp));
  resp->version = AGENT_CALL_VERSION;
  resp->status = AGENT_STATUS_OK;
  resp->tool_id = tool ? tool->tool_id : req->tool_id;
  resp->request_id = req->request_id;
  if (tool)
    safestrcpy(resp->tool_name, tool->name, sizeof(resp->tool_name));
  else
    safestrcpy(resp->tool_name, req->tool_name, sizeof(resp->tool_name));
}

static uint64
agent_ticks(void)
{
  uint xticks;

  acquire(&tickslock);
  xticks = ticks;
  release(&tickslock);
  return xticks;
}

static int
agent_append_context(struct proc *p, struct agent_op *op,
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
    p->context_path_oldest = latest->sequence - p->context_path_capacity + 1;
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

  if (agent_write_context_record(p, slot, &record) < 0)
    return -1;

  if (agent_write_latest_result(p, latest) < 0)
    return -1;

  return 0;
}

static void
agent_collect_proc_snapshot(int filter_agents,
                            struct agent_proc_snapshot *snapshot)
{
  struct proc *pp;

  memset(snapshot, 0, sizeof(*snapshot));
  for (pp = proc; pp < &proc[NPROC]; pp++) {
    acquire(&pp->lock);
    if (pp->state != UNUSED) {
      if (!filter_agents || pp->is_agent) {
        snapshot->used++;
        if (pp->is_agent)
          snapshot->agents++;
        if (pp->state == RUNNABLE || pp->state == RUNNING)
          snapshot->runnable++;
      }
    }
    release(&pp->lock);
  }
}

static void
agent_result_text(struct agent_result *res, char *text)
{
  safestrcpy(res->result, text, sizeof(res->result));
}

static void
agent_result_no_space(struct agent_result *res)
{
  res->status = AGENT_STATUS_NO_SPACE;
  agent_result_text(res, "no_space");
}

static void
agent_result_init(struct agent_result *res, struct agent_op *op)
{
  memset(res, 0, sizeof(*res));
  res->version = AGENT_CALL_VERSION;
  res->status = AGENT_STATUS_OK;
  res->tool_id = op->tool_id;
  res->request_id = op->request_id;
}

static int
agent_contains(char *text, char *needle)
{
  int i;
  int j;

  if (needle[0] == 0)
    return 1;
  for (i = 0; text[i]; i++) {
    for (j = 0; needle[j] && text[i + j] == needle[j]; j++)
      ;
    if (needle[j] == 0)
      return 1;
  }
  return 0;
}

static uint
agent_hash(char *s)
{
  uint h = 2166136261U;

  while (*s) {
    h ^= (uchar)*s;
    h *= 16777619U;
    s++;
  }
  return h;
}

static int
agent_bucket(char *s)
{
  return agent_hash(s) % AGENT_FILE_INDEX_BUCKETS;
}

static void
agent_file_clear_indexes_locked(void)
{
  int i;

  for (i = 0; i < AGENT_FILE_INDEX_BUCKETS; i++) {
    agent_file_status_head[i] = -1;
    agent_file_stage_head[i] = -1;
    agent_file_kind_head[i] = -1;
    agent_file_run_head[i] = -1;
  }
  for (i = 0; i < AGENT_FILE_META_MAX; i++) {
    agent_file_status_next[i] = -1;
    agent_file_stage_next[i] = -1;
    agent_file_kind_next[i] = -1;
    agent_file_run_next[i] = -1;
  }
}

static void
agent_file_rebuild_indexes_locked(void)
{
  int i;
  int b;

  agent_file_clear_indexes_locked();
  for (i = 0; i < AGENT_FILE_META_MAX; i++) {
    if (!agent_files[i].used)
      continue;
    b = agent_bucket(agent_files[i].status);
    agent_file_status_next[i] = agent_file_status_head[b];
    agent_file_status_head[b] = i;
    b = agent_bucket(agent_files[i].stage);
    agent_file_stage_next[i] = agent_file_stage_head[b];
    agent_file_stage_head[b] = i;
    b = agent_bucket(agent_files[i].kind);
    agent_file_kind_next[i] = agent_file_kind_head[b];
    agent_file_kind_head[b] = i;
    b = agent_bucket(agent_files[i].run_id);
    agent_file_run_next[i] = agent_file_run_head[b];
    agent_file_run_head[b] = i;
  }
}

static void
agent_file_fill_meta(struct agent_file_meta *m, int fid, char *physical,
                     char *logical, char *project, char *workflow,
                     char *run_id, char *stage, char *kind, char *status,
                     char *summary, uint64 deps)
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
  m->dependency_mask = deps;
  m->updated_tick = agent_ticks();
}

static void
agent_file_install_default_locked(void)
{
  int i;

  memset(agent_files, 0, sizeof(agent_files));
  agent_file_fill_meta(
      &agent_files[0], 1, "lab_RUN042_prepare_log",
      "/lab/projects/lab-gene-x/runs/RUN-042/prepare.log", "lab-gene-x",
      "nightly-regression", "RUN-042", "prepare", "log", "ok",
      "prepare stage completed", AGENT_DEP_PREPARE);
  agent_file_fill_meta(
      &agent_files[1], 2, "lab_RUN042_prepare_ok",
      "/lab/projects/lab-gene-x/runs/RUN-042/prepare.ok", "lab-gene-x",
      "nightly-regression", "RUN-042", "prepare", "status", "ok",
      "prepare output is reusable", AGENT_DEP_PREPARE);
  agent_file_fill_meta(
      &agent_files[2], 3, "lab_RUN042_align_log",
      "/lab/projects/lab-gene-x/runs/RUN-042/align.log", "lab-gene-x",
      "nightly-regression", "RUN-042", "align", "log", "ok",
      "align stage waiting for nightly result",
      AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE | AGENT_DEP_REPORT |
          AGENT_DEP_ARCHIVE);
  agent_file_fill_meta(
      &agent_files[3], 4, "lab_RUN042_align_err",
      "/lab/projects/lab-gene-x/runs/RUN-042/align.err", "lab-gene-x",
      "nightly-regression", "RUN-042", "align", "log", "ok",
      "no failure recorded yet",
      AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE | AGENT_DEP_REPORT |
          AGENT_DEP_ARCHIVE);
  agent_file_fill_meta(
      &agent_files[4], 5, "lab_RUN042_analyze_skipped",
      "/lab/projects/lab-gene-x/runs/RUN-042/analyze.skipped",
      "lab-gene-x", "nightly-regression", "RUN-042", "analyze",
      "status", "pending", "analysis waits for align",
      AGENT_DEP_ANALYZE | AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE);
  agent_file_fill_meta(
      &agent_files[5], 6, "lab_RUN042_report_incomplete",
      "/lab/projects/lab-gene-x/runs/RUN-042/report.incomplete",
      "lab-gene-x", "nightly-regression", "RUN-042", "report", "report",
      "incomplete", "report waits for align and analyze",
      AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE);
  agent_file_fill_meta(
      &agent_files[6], 7, "lab_RUN042_deps",
      "/lab/projects/lab-gene-x/runs/RUN-042/deps.txt", "lab-gene-x",
      "nightly-regression", "RUN-042", "workflow", "deps", "ok",
      "prepare->align->analyze->report->archive",
      AGENT_DEP_PREPARE | AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE |
          AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE);
  agent_file_fill_meta(
      &agent_files[7], 8, "lab_RUN042_archive_missing",
      "/lab/projects/lab-gene-x/runs/RUN-042/archive.missing",
      "lab-gene-x", "nightly-regression", "RUN-042", "archive", "status",
      "pending", "archive waits for report", AGENT_DEP_ARCHIVE);
  agent_file_fill_meta(
      &agent_files[8], 9, "lab_RUN042_recovery_report",
      "/lab/projects/lab-gene-x/reports/RUN-042-recovery.md",
      "lab-gene-x", "nightly-regression", "RUN-042", "report", "report",
      "pending", "recovery report not written yet",
      AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE);
  for (i = 9; i < AGENT_FILE_DEFAULT_USED; i++) {
    if ((i % 5) == 0) {
      agent_file_fill_meta(&agent_files[i], i + 1, "lab_history_prepare",
                           "/lab/history/prepare.log", "lab-gene-x",
                           "nightly-regression", "RUN-041", "prepare",
                           "log", "ok", "historical prepare artifact",
                           AGENT_DEP_PREPARE);
    } else if ((i % 5) == 1) {
      agent_file_fill_meta(&agent_files[i], i + 1, "lab_history_report",
                           "/lab/history/report.md", "lab-gene-x",
                           "nightly-regression", "RUN-041", "report",
                           "report", "ok", "historical report artifact",
                           AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE);
    } else if ((i % 5) == 2) {
      agent_file_fill_meta(&agent_files[i], i + 1, "lab_history_archive",
                           "/lab/history/archive.ok", "lab-gene-x",
                           "nightly-regression", "RUN-041", "archive",
                           "status", "ok", "historical archive artifact",
                           AGENT_DEP_ARCHIVE);
    } else if ((i % 5) == 3) {
      agent_file_fill_meta(&agent_files[i], i + 1, "lab_other_project_log",
                           "/lab/projects/other/runs/RUN-011/log", "other",
                           "nightly-regression", "RUN-011", "prepare",
                           "log", "ok", "other project artifact",
                           AGENT_DEP_PREPARE);
    } else {
      agent_file_fill_meta(&agent_files[i], i + 1, "lab_history_deps",
                           "/lab/history/deps.txt", "lab-gene-x",
                           "weekly-regression", "RUN-040", "workflow",
                           "deps", "ok", "historical dependency artifact",
                           AGENT_DEP_PREPARE | AGENT_DEP_ALIGN |
                               AGENT_DEP_ANALYZE | AGENT_DEP_REPORT |
                               AGENT_DEP_ARCHIVE);
    }
  }
  agent_file_rebuild_indexes_locked();
}

static void
agent_file_make_hit(struct agent_file_hit *hit, struct agent_file_meta *meta)
{
  memset(hit, 0, sizeof(*hit));
  hit->fid = meta->fid;
  safestrcpy(hit->physical_name, meta->physical_name,
             sizeof(hit->physical_name));
  safestrcpy(hit->logical_path, meta->logical_path, sizeof(hit->logical_path));
  safestrcpy(hit->stage, meta->stage, sizeof(hit->stage));
  safestrcpy(hit->kind, meta->kind, sizeof(hit->kind));
  safestrcpy(hit->status, meta->status, sizeof(hit->status));
  safestrcpy(hit->summary, meta->summary, sizeof(hit->summary));
  hit->dependency_mask = meta->dependency_mask;
}

static int
agent_field_match(char *want, char *have)
{
  return want[0] == 0 || strncmp(want, have, AGENT_FILE_LOGICAL_SIZE) == 0;
}

static int
agent_file_matches(struct agent_file_query *q, struct agent_file_meta *m)
{
  if (!m->used)
    return 0;
  if (q->fid > 0 && q->fid != m->fid)
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

static int
agent_file_chain_len(int cursor, int *next)
{
  int len = 0;

  while (cursor >= 0) {
    len++;
    cursor = next[cursor];
  }
  return len;
}

static void
agent_file_consider_index(char *field, int *head, int *next, int *cursor_out,
                          int **next_out, int *best_len)
{
  int cursor;
  int len;

  if (field[0] == 0)
    return;
  cursor = head[agent_bucket(field)];
  len = agent_file_chain_len(cursor, next);
  if (len < *best_len) {
    *best_len = len;
    *cursor_out = cursor;
    *next_out = next;
  }
}

static int
agent_file_query_locked(struct agent_file_query *q,
                        struct agent_file_query_result *r)
{
  int cursor;
  int *next;
  int i;
  int use_index = 0;
  int max_hits;
  int best_len;
  uint64 start;

  memset(r, 0, sizeof(*r));
  max_hits = q->max_hits;
  if (max_hits <= 0 || max_hits > AGENT_FILE_QUERY_MAX_HITS)
    max_hits = AGENT_FILE_QUERY_MAX_HITS;

  start = agent_ticks();
  cursor = -1;
  next = 0;
  best_len = AGENT_FILE_META_MAX + 1;
  if ((q->flags & AGENT_FILE_QUERY_USE_INDEX) &&
      !(q->flags & AGENT_FILE_QUERY_SCAN)) {
    agent_file_consider_index(q->status, agent_file_status_head,
                              agent_file_status_next, &cursor, &next,
                              &best_len);
    agent_file_consider_index(q->run_id, agent_file_run_head,
                              agent_file_run_next, &cursor, &next,
                              &best_len);
    agent_file_consider_index(q->stage, agent_file_stage_head,
                              agent_file_stage_next, &cursor, &next,
                              &best_len);
    agent_file_consider_index(q->kind, agent_file_kind_head,
                              agent_file_kind_next, &cursor, &next,
                              &best_len);
    use_index = next != 0;
  }

  if (use_index) {
    for (i = cursor; i >= 0; i = next[i]) {
      r->scanned_records++;
      if (agent_file_matches(q, &agent_files[i])) {
        r->total_hits++;
        if (r->returned < max_hits) {
          agent_file_make_hit(&r->hits[r->returned], &agent_files[i]);
          r->returned++;
        } else {
          r->truncated = 1;
        }
      }
    }
  } else {
    for (i = 0; i < AGENT_FILE_META_MAX; i++) {
      if (!agent_files[i].used)
        continue;
      r->scanned_records++;
      if (agent_file_matches(q, &agent_files[i])) {
        r->total_hits++;
        if (r->returned < max_hits) {
          agent_file_make_hit(&r->hits[r->returned], &agent_files[i]);
          r->returned++;
        } else {
          r->truncated = 1;
        }
      }
    }
  }

  r->used_index = use_index;
  r->query_ticks = agent_ticks() - start;
  return r->returned;
}

static int
agent_file_find_locked(char *selector)
{
  int i;

  for (i = 0; i < AGENT_FILE_META_MAX; i++) {
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

static int
agent_query_has_filter(struct agent_file_query *q)
{
  return q->fid > 0 || q->physical_name[0] || q->logical_path[0] ||
         q->project[0] || q->workflow[0] || q->run_id[0] || q->stage[0] ||
         q->kind[0] || q->status[0] || q->summary_contains[0];
}

static int
agent_query_from_payload(struct agent_file_query *q, char *payload)
{
  char key[AGENT_FILE_FIELD_SIZE];
  char val[AGENT_FILE_LOGICAL_SIZE];
  int i = 0;
  int k;
  int v;
  int filters = 0;

  memset(q, 0, sizeof(*q));
  q->flags = AGENT_FILE_QUERY_USE_INDEX;
  q->max_hits = AGENT_FILE_QUERY_MAX_HITS;

  while (payload[i]) {
    while (payload[i] == ' ' || payload[i] == ';' || payload[i] == ',')
      i++;
    if (payload[i] == 0)
      break;
    k = 0;
    memset(key, 0, sizeof(key));
    while (payload[i] && payload[i] != '=' && payload[i] != ':' &&
           payload[i] != ';' && payload[i] != ',' &&
           k < (int)sizeof(key) - 1)
      key[k++] = payload[i++];
    if (k == 0)
      return -1;
    if (payload[i] && payload[i] != '=' && payload[i] != ':' &&
        payload[i] != ';' && payload[i] != ',')
      return -1;
    if (payload[i] != '=' && payload[i] != ':') {
      return -1;
    }
    i++;
    v = 0;
    memset(val, 0, sizeof(val));
    while (payload[i] && payload[i] != ';' && payload[i] != ',' &&
           v < (int)sizeof(val) - 1)
      val[v++] = payload[i++];
    if (v == 0)
      return -1;
    if (payload[i] && payload[i] != ';' && payload[i] != ',')
      return -1;

    if (strncmp(key, "fid", sizeof(key)) == 0) {
      q->fid = 0;
      for (v = 0; val[v]; v++) {
        if (val[v] < '0' || val[v] > '9')
          return -1;
        q->fid = q->fid * 10 + val[v] - '0';
      }
      if (q->fid <= 0)
        return -1;
      filters++;
    } else if (strncmp(key, "physical", sizeof(key)) == 0 ||
        strncmp(key, "physical_name", sizeof(key)) == 0) {
      safestrcpy(q->physical_name, val, sizeof(q->physical_name));
      filters++;
    } else if (strncmp(key, "logical", sizeof(key)) == 0 ||
               strncmp(key, "logical_path", sizeof(key)) == 0 ||
               strncmp(key, "path", sizeof(key)) == 0) {
      safestrcpy(q->logical_path, val, sizeof(q->logical_path));
      filters++;
    } else if (strncmp(key, "project", sizeof(key)) == 0) {
      safestrcpy(q->project, val, sizeof(q->project));
      filters++;
    } else if (strncmp(key, "workflow", sizeof(key)) == 0) {
      safestrcpy(q->workflow, val, sizeof(q->workflow));
      filters++;
    } else if (strncmp(key, "run", sizeof(key)) == 0 ||
               strncmp(key, "run_id", sizeof(key)) == 0) {
      safestrcpy(q->run_id, val, sizeof(q->run_id));
      filters++;
    } else if (strncmp(key, "stage", sizeof(key)) == 0) {
      safestrcpy(q->stage, val, sizeof(q->stage));
      filters++;
    } else if (strncmp(key, "kind", sizeof(key)) == 0) {
      safestrcpy(q->kind, val, sizeof(q->kind));
      filters++;
    } else if (strncmp(key, "status", sizeof(key)) == 0) {
      safestrcpy(q->status, val, sizeof(q->status));
      filters++;
    } else if (strncmp(key, "summary", sizeof(key)) == 0) {
      safestrcpy(q->summary_contains, val, sizeof(q->summary_contains));
      filters++;
    } else {
      return -1;
    }
  }
  return filters > 0 ? 0 : -1;
}

static void
agent_text_append(char *out, char *suffix, int n)
{
  int len;

  if (n <= 0)
    return;
  len = strlen(out);
  if (len < n - 1)
    safestrcpy(out + len, suffix, n - len);
}

static void
agent_stage_append(uint64 mask, uint64 bit, char *name, char *out, int n)
{
  if ((mask & bit) == 0)
    return;
  if (out[0])
    agent_text_append(out, "+", n);
  agent_text_append(out, name, n);
}

static void
agent_stage_affected_text(uint64 mask, char *out, int n)
{
  if (n <= 0)
    return;
  out[0] = 0;
  agent_stage_append(mask, AGENT_DEP_PREPARE, "prepare", out, n);
  agent_stage_append(mask, AGENT_DEP_ALIGN, "align", out, n);
  agent_stage_append(mask, AGENT_DEP_ANALYZE, "analyze", out, n);
  agent_stage_append(mask, AGENT_DEP_REPORT, "report", out, n);
  agent_stage_append(mask, AGENT_DEP_ARCHIVE, "archive", out, n);
  if (out[0] == 0)
    safestrcpy(out, "none", n);
}

static void
agent_uint_text(uint64 v, char *out, int n)
{
  char tmp[24];
  int i = 0;
  int j = 0;

  if (n <= 0)
    return;
  if (v == 0) {
    out[0] = '0';
    if (n > 1)
      out[1] = 0;
    return;
  }
  while (v > 0 && i < (int)sizeof(tmp)) {
    tmp[i++] = '0' + v % 10;
    v /= 10;
  }
  while (i > 0 && j < n - 1)
    out[j++] = tmp[--i];
  out[j] = 0;
}

static int
agent_action_allowed(struct proc *p, char *action)
{
  if (strncmp(action, "rerun_stage", AGENT_OP_PAYLOAD_SIZE) == 0) {
    return agent_has_cap(p, AGENT_CAP_RECOVER_STAGE);
  }
  if (strncmp(action, "write_report", AGENT_OP_PAYLOAD_SIZE) == 0) {
    return agent_has_cap(p, AGENT_CAP_REPORT_WRITE);
  }
  if (strncmp(action, "query", AGENT_OP_PAYLOAD_SIZE) == 0) {
    return agent_has_cap(p, AGENT_CAP_META_READ);
  }
  if (strncmp(action, "meta_write", AGENT_OP_PAYLOAD_SIZE) == 0) {
    return agent_has_cap(p, AGENT_CAP_META_WRITE);
  }
  if (strncmp(action, "event_wake", AGENT_OP_PAYLOAD_SIZE) == 0) {
    return agent_has_cap(p, AGENT_CAP_EVENT_WAKE);
  }
  return 0;
}

static void
agent_scope_default(struct agent_scope *scope, char *stage)
{
  memset(scope, 0, sizeof(*scope));
  safestrcpy(scope->project, AGENT_DEMO_PROJECT, sizeof(scope->project));
  safestrcpy(scope->workflow, AGENT_DEMO_WORKFLOW, sizeof(scope->workflow));
  safestrcpy(scope->run_id, AGENT_DEMO_RUN, sizeof(scope->run_id));
  if (stage)
    safestrcpy(scope->stage, stage, sizeof(scope->stage));
}

static int
agent_scope_from_payload(char *payload, char *default_stage,
                         int plain_payload_is_stage,
                         struct agent_scope *scope)
{
  char key[AGENT_FILE_FIELD_SIZE];
  char val[AGENT_FILE_SUMMARY_SIZE];
  int i = 0;
  int k;
  int v;

  agent_scope_default(scope, default_stage);
  if (agent_text_empty(payload)) {
    return scope->stage[0] ? 0 : -1;
  }
  if (!agent_contains(payload, "=") && !agent_contains(payload, ":")) {
    if (plain_payload_is_stage)
      safestrcpy(scope->stage, payload, sizeof(scope->stage));
    else
      safestrcpy(scope->summary, payload, sizeof(scope->summary));
    return scope->stage[0] ? 0 : -1;
  }

  while (payload[i]) {
    while (payload[i] == ' ' || payload[i] == ';' || payload[i] == ',')
      i++;
    if (payload[i] == 0)
      break;
    k = 0;
    memset(key, 0, sizeof(key));
    while (payload[i] && payload[i] != '=' && payload[i] != ':' &&
           payload[i] != ';' && payload[i] != ',' &&
           k < (int)sizeof(key) - 1)
      key[k++] = payload[i++];
    if (k == 0 || (payload[i] != '=' && payload[i] != ':'))
      return -1;
    i++;
    v = 0;
    memset(val, 0, sizeof(val));
    while (payload[i] && payload[i] != ';' && payload[i] != ',' &&
           v < (int)sizeof(val) - 1)
      val[v++] = payload[i++];
    if (v == 0)
      return -1;

    if (strncmp(key, "project", sizeof(key)) == 0)
      safestrcpy(scope->project, val, sizeof(scope->project));
    else if (strncmp(key, "workflow", sizeof(key)) == 0)
      safestrcpy(scope->workflow, val, sizeof(scope->workflow));
    else if (strncmp(key, "run", sizeof(key)) == 0 ||
             strncmp(key, "run_id", sizeof(key)) == 0)
      safestrcpy(scope->run_id, val, sizeof(scope->run_id));
    else if (strncmp(key, "stage", sizeof(key)) == 0)
      safestrcpy(scope->stage, val, sizeof(scope->stage));
    else if (strncmp(key, "summary", sizeof(key)) == 0 ||
             strncmp(key, "report", sizeof(key)) == 0 ||
             strncmp(key, "payload", sizeof(key)) == 0)
      safestrcpy(scope->summary, val, sizeof(scope->summary));
    else
      return -1;
  }

  return scope->stage[0] ? 0 : -1;
}

static void
agent_action_make_key(struct agent_action_key *key, uint64 corr_id,
                      struct agent_scope *scope, char *action)
{
  memset(key, 0, sizeof(*key));
  key->corr_id = corr_id;
  safestrcpy(key->project, scope->project, sizeof(key->project));
  safestrcpy(key->workflow, scope->workflow, sizeof(key->workflow));
  safestrcpy(key->run_id, scope->run_id, sizeof(key->run_id));
  safestrcpy(key->stage, scope->stage, sizeof(key->stage));
  safestrcpy(key->action, action, sizeof(key->action));
}

static int
agent_action_key_equal(struct agent_action_key *a, struct agent_action_key *b)
{
  return a->corr_id == b->corr_id &&
         strncmp(a->project, b->project, sizeof(a->project)) == 0 &&
         strncmp(a->workflow, b->workflow, sizeof(a->workflow)) == 0 &&
         strncmp(a->run_id, b->run_id, sizeof(a->run_id)) == 0 &&
         strncmp(a->stage, b->stage, sizeof(a->stage)) == 0 &&
         strncmp(a->action, b->action, sizeof(a->action)) == 0;
}

static void
agent_action_reset(void)
{
  acquire(&agent_action_lock);
  memset(agent_action_history, 0, sizeof(agent_action_history));
  agent_action_history_count = 0;
  release(&agent_action_lock);
}

static int
agent_action_seen(uint64 corr_id, struct agent_scope *scope, char *action)
{
  int i;
  struct agent_action_key key;
  int seen = 0;

  if (corr_id == 0)
    return 0;
  agent_action_make_key(&key, corr_id, scope, action);
  acquire(&agent_action_lock);
  for (i = 0; i < agent_action_history_count; i++) {
    if (agent_action_key_equal(&agent_action_history[i], &key)) {
      seen = 1;
      break;
    }
  }
  release(&agent_action_lock);
  return seen;
}

static void
agent_action_remember(uint64 corr_id, struct agent_scope *scope, char *action)
{
  struct agent_action_key key;

  if (corr_id == 0)
    return;
  agent_action_make_key(&key, corr_id, scope, action);
  acquire(&agent_action_lock);
  if (agent_action_history_count < AGENT_ACTION_HISTORY_MAX) {
    agent_action_history[agent_action_history_count++] = key;
  } else {
    memmove(agent_action_history, agent_action_history + 1,
            sizeof(agent_action_history[0]) * (AGENT_ACTION_HISTORY_MAX - 1));
    agent_action_history[AGENT_ACTION_HISTORY_MAX - 1] = key;
  }
  release(&agent_action_lock);
}

static int
agent_filter_matches(struct proc *target, int type, char *payload)
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

static int
agent_queue_event_locked(struct proc *target, int source_pid, int type,
                         uint64 corr_id, char *payload)
{
  int slot;

  if (!target->is_agent || !agent_filter_matches(target, type, payload))
    return 0;
  if (target->agent_event_queued >= AGENT_EVENT_QUEUE_MAX) {
    target->agent_event_dropped++;
    return -1;
  }
  slot = target->agent_event_tail;
  target->agent_event_type[slot] = type;
  target->agent_event_source_pid[slot] = source_pid;
  target->agent_event_id[slot] = agent_next_event_id++;
  target->agent_event_tick[slot] = agent_ticks();
  target->agent_event_corr_id[slot] = corr_id;
  safestrcpy(target->agent_event_payload[slot], payload,
             sizeof(target->agent_event_payload[slot]));
  target->agent_event_tail =
      (target->agent_event_tail + 1) % AGENT_EVENT_QUEUE_MAX;
  target->agent_event_queued++;
  target->agent_event_count++;
  wakeup(target);
  return 1;
}

static int
agent_deliver_event_pid(int pid, int source_pid, int type, uint64 corr_id,
                        char *payload)
{
  struct proc *target;
  int delivered = 0;

  acquire(&agent_event_lock);
  for (target = proc; target < &proc[NPROC]; target++) {
    if (target->state != UNUSED && target->pid == pid) {
      delivered =
          agent_queue_event_locked(target, source_pid, type, corr_id, payload);
      break;
    }
  }
  release(&agent_event_lock);
  return delivered;
}

static int
agent_deliver_event_watchers(int source_pid, int type, uint64 corr_id,
                             char *payload)
{
  struct proc *target;
  int delivered = 0;
  int queued;
  int full = 0;

  acquire(&agent_event_lock);
  for (target = proc; target < &proc[NPROC]; target++) {
    if (target->state != UNUSED) {
      queued =
          agent_queue_event_locked(target, source_pid, type, corr_id, payload);
      if (queued > 0)
        delivered += queued;
      else if (queued < 0)
        full = 1;
    }
  }
  release(&agent_event_lock);
  if (full)
    return -1;
  return delivered;
}

static int
agent_append_system_context(struct proc *p, int tool_id, uint64 request_id,
                            uint64 arg0, char *payload, char *result,
                            int status, uint64 value0, uint64 value1,
                            uint64 value2)
{
  struct agent_op op;
  struct agent_result latest;

  memset(&op, 0, sizeof(op));
  op.version = AGENT_CALL_VERSION;
  op.tool_id = tool_id;
  op.request_id = request_id;
  op.arg0 = arg0;
  safestrcpy(op.payload, payload, sizeof(op.payload));
  memset(&latest, 0, sizeof(latest));
  latest.version = AGENT_CALL_VERSION;
  latest.tool_id = tool_id;
  latest.request_id = request_id;
  latest.status = status;
  latest.value0 = value0;
  latest.value1 = value1;
  latest.value2 = value2;
  p->agent_call_count++;
  latest.sequence = p->agent_call_count;
  agent_result_text(&latest, result);
  if (agent_append_context(p, &op, &latest, agent_ticks()) < 0)
    return AGENT_STATUS_NO_SPACE;
  if (agent_write_header(p) < 0)
    return AGENT_STATUS_NO_SPACE;
  return 0;
}

static int
agent_file_scope_match(struct agent_file_meta *meta, struct agent_scope *scope)
{
  return strncmp(meta->project, scope->project, sizeof(meta->project)) == 0 &&
         strncmp(meta->workflow, scope->workflow,
                 sizeof(meta->workflow)) == 0 &&
         strncmp(meta->run_id, scope->run_id, sizeof(meta->run_id)) == 0;
}

static int
agent_file_is_recovery_report(struct agent_file_meta *meta)
{
  return strncmp(meta->physical_name, "lab_RUN042_recovery_report",
                 sizeof(meta->physical_name)) == 0;
}

static int
agent_file_update_slot_locked(int slot, char *status, char *summary)
{
  if (slot < 0 || slot >= AGENT_FILE_META_MAX || !agent_files[slot].used)
    return 0;
  safestrcpy(agent_files[slot].status, status,
             sizeof(agent_files[slot].status));
  if (summary && summary[0])
    safestrcpy(agent_files[slot].summary, summary,
               sizeof(agent_files[slot].summary));
  agent_files[slot].updated_tick = agent_ticks();
  return 1;
}

static int
agent_file_update_stage_scope_locked(struct agent_scope *scope, char *stage,
                                     char *status, char *summary)
{
  int i;
  int updated = 0;

  for (i = 0; i < AGENT_FILE_META_MAX; i++) {
    if (!agent_files[i].used)
      continue;
    if (agent_file_scope_match(&agent_files[i], scope) &&
        strncmp(agent_files[i].stage, stage, sizeof(agent_files[i].stage)) ==
            0 &&
        !agent_file_is_recovery_report(&agent_files[i]))
      updated += agent_file_update_slot_locked(i, status, summary);
  }
  agent_file_rebuild_indexes_locked();
  return updated;
}

static int
agent_file_update_recovery_report_scope_locked(struct agent_scope *scope,
                                               char *status, char *summary)
{
  int i;
  int updated = 0;

  for (i = 0; i < AGENT_FILE_META_MAX; i++) {
    if (!agent_files[i].used)
      continue;
    if (agent_file_scope_match(&agent_files[i], scope) &&
        agent_file_is_recovery_report(&agent_files[i]))
      updated += agent_file_update_slot_locked(i, status, summary);
  }
  agent_file_rebuild_indexes_locked();
  return updated;
}

static int
agent_file_update_mask_scope_locked(struct agent_scope *scope, uint64 mask,
                                    char *status, char *summary)
{
  int updated = 0;

  if (mask & AGENT_DEP_PREPARE)
    updated +=
        agent_file_update_stage_scope_locked(scope, "prepare", status, summary);
  if (mask & AGENT_DEP_ALIGN)
    updated +=
        agent_file_update_stage_scope_locked(scope, "align", status, summary);
  if (mask & AGENT_DEP_ANALYZE)
    updated +=
        agent_file_update_stage_scope_locked(scope, "analyze", status, summary);
  if (mask & AGENT_DEP_REPORT)
    updated +=
        agent_file_update_stage_scope_locked(scope, "report", status, summary);
  if (mask & AGENT_DEP_ARCHIVE)
    updated +=
        agent_file_update_stage_scope_locked(scope, "archive", status, summary);
  return updated;
}

static void
agent_file_event_payload(struct agent_file_meta *meta, char *out, int n)
{
  char fidbuf[16];

  safestrcpy(out, "fid=", n);
  agent_uint_text(meta->fid, fidbuf, sizeof(fidbuf));
  agent_text_append(out, fidbuf, n);
  agent_text_append(out, ";status=", n);
  agent_text_append(out, meta->status, n);
  agent_text_append(out, ";stage=", n);
  agent_text_append(out, meta->stage, n);
  agent_text_append(out, ";run_id=", n);
  agent_text_append(out, meta->run_id, n);
  agent_text_append(out, ";truncated=0", n);
}

static int
agent_file_default_dependency_for_stage(char *stage, uint64 *mask)
{
  if (strncmp(stage, "prepare", AGENT_FILE_FIELD_SIZE) == 0) {
    *mask = AGENT_DEP_PREPARE | AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE |
            AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE;
    return 0;
  }
  if (strncmp(stage, "align", AGENT_FILE_FIELD_SIZE) == 0) {
    *mask = AGENT_DEP_ALIGN | AGENT_DEP_ANALYZE | AGENT_DEP_REPORT |
            AGENT_DEP_ARCHIVE;
    return 0;
  }
  if (strncmp(stage, "analyze", AGENT_FILE_FIELD_SIZE) == 0) {
    *mask = AGENT_DEP_ANALYZE | AGENT_DEP_REPORT | AGENT_DEP_ARCHIVE;
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

static int
agent_file_dependency_for_scope(struct agent_scope *scope, uint64 *mask)
{
  int i;
  int found = -1;
  uint64 latest_tick = 0;

  acquire(&agent_file_lock);
  for (i = 0; i < AGENT_FILE_META_MAX; i++) {
    if (!agent_files[i].used)
      continue;
    if (agent_file_scope_match(&agent_files[i], scope) &&
        strncmp(agent_files[i].stage, scope->stage,
                sizeof(agent_files[i].stage)) == 0) {
      if (found < 0 || agent_files[i].updated_tick >= latest_tick) {
        found = i;
        latest_tick = agent_files[i].updated_tick;
      }
    }
  }
  if (found >= 0) {
    *mask = agent_files[found].dependency_mask;
    release(&agent_file_lock);
    return 0;
  }
  release(&agent_file_lock);

  if (strncmp(scope->project, AGENT_DEMO_PROJECT, sizeof(scope->project)) == 0 &&
      strncmp(scope->workflow, AGENT_DEMO_WORKFLOW,
              sizeof(scope->workflow)) == 0 &&
      strncmp(scope->run_id, AGENT_DEMO_RUN, sizeof(scope->run_id)) == 0)
    return agent_file_default_dependency_for_stage(scope->stage, mask);
  return -1;
}

static void
agent_execute_op(struct proc *p, struct agent_op *op, struct agent_result *res)
{
  struct agent_proc_snapshot snapshot;
  struct inode *ip;
  struct proc *target;
  struct agent_file_query query;
  struct agent_file_query_result *query_result;
  struct agent_scope scope;
  uint64 deps;
  int found;
  int delivered;
  int updated;
  int rc;
  int old_mailbox_valid;
  int old_mailbox_from;
  char old_mailbox[64];
  char event_payload[AGENT_EVENT_PAYLOAD_SIZE];

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
    agent_collect_proc_snapshot(op->arg0 == AGENT_TYPE_AGENT, &snapshot);
    res->value0 = snapshot.used;
    res->value1 = snapshot.agents;
    res->value2 = snapshot.runnable;
    agent_result_text(res, "query_process");
    break;
  case AGENT_TOOL_GET_SYSTEM_STATUS:
    agent_collect_proc_snapshot(0, &snapshot);
    res->value0 = snapshot.used;
    res->value1 = snapshot.agents;
    res->value2 = agent_ticks();
    agent_result_text(res, "system_status");
    break;
  case AGENT_TOOL_READ_CONTEXT:
    if (p->context_path_capacity) {
      if (p->context_path_count < p->context_path_capacity)
        res->value0 = p->context_path_count + 1;
      else
        res->value0 = p->context_path_capacity;
      res->value1 = (p->context_path_head + 1) % p->context_path_capacity;
    }
    res->value2 = p->agent_call_count;
    agent_result_text(res, "read_context");
    break;
  case AGENT_TOOL_QUERY_FILE:
    if (!agent_has_cap(p, AGENT_CAP_META_READ) &&
        !agent_has_cap(p, AGENT_CAP_CONTENT_READ)) {
      res->status = AGENT_STATUS_DENIED;
      agent_result_text(res, "denied");
      break;
    }
    if (agent_text_empty(op->payload)) {
      res->status = AGENT_STATUS_BAD_PARAM;
      agent_result_text(res, "path_required");
      break;
    }
    if (agent_contains(op->payload, "=") || agent_contains(op->payload, ":")) {
      if (agent_query_from_payload(&query, op->payload) < 0) {
        res->status = AGENT_STATUS_BAD_PARAM;
        agent_result_text(res, "bad_query");
        break;
      }
      query_result = (struct agent_file_query_result *)kalloc();
      if (query_result == 0) {
        res->status = AGENT_STATUS_NO_SPACE;
        agent_result_text(res, "no_space");
        break;
      }
      acquire(&agent_file_lock);
      agent_file_query_locked(&query, query_result);
      release(&agent_file_lock);
      res->value0 = query_result->total_hits;
      res->value1 = query_result->scanned_records;
      res->value2 = (uint64)query_result->used_index |
                    ((uint64)query_result->truncated << 1);
      if (query_result->returned > 0)
        agent_result_text(res, query_result->hits[0].physical_name);
      else
        agent_result_text(res, "empty");
      kfree((void *)query_result);
      break;
    }
    begin_op();
    if ((ip = namei(op->payload)) == 0) {
      end_op();
      res->status = AGENT_STATUS_NOT_FOUND;
      agent_result_text(res, "file_not_found");
      break;
    }
    ilock(ip);
    res->value0 = ip->type;
    res->value1 = ip->inum;
    res->value2 = ip->size;
    iunlockput(ip);
    end_op();
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
    for (target = proc; target < &proc[NPROC]; target++) {
      acquire(&target->lock);
      if (target->state != UNUSED && target->pid == (int)op->arg0 &&
          target->is_agent) {
        old_mailbox_valid = target->agent_mailbox_valid;
        old_mailbox_from = target->agent_mailbox_from;
        safestrcpy(old_mailbox, target->agent_mailbox, sizeof(old_mailbox));
        target->agent_mailbox_valid = 1;
        target->agent_mailbox_from = p->pid;
        safestrcpy(target->agent_mailbox, op->payload,
                   sizeof(target->agent_mailbox));
        release(&target->lock);
        delivered = agent_deliver_event_pid((int)op->arg0, p->pid,
                                            AGENT_EVENT_MESSAGE,
                                            op->request_id, op->payload);
        if (delivered < 0) {
          acquire(&target->lock);
          if (target->state != UNUSED && target->pid == (int)op->arg0 &&
              target->agent_mailbox_from == p->pid) {
            target->agent_mailbox_valid = old_mailbox_valid;
            target->agent_mailbox_from = old_mailbox_from;
            safestrcpy(target->agent_mailbox, old_mailbox,
                       sizeof(target->agent_mailbox));
          }
          release(&target->lock);
          res->status = AGENT_STATUS_NO_SPACE;
          agent_result_text(res, "event_queue_full");
          return;
        }
        res->value0 = op->arg0;
        res->value1 = p->pid;
        res->value2 = strlen(op->payload);
        agent_result_text(res, "send_message");
        return;
      }
      release(&target->lock);
    }
    res->status = AGENT_STATUS_NOT_FOUND;
    agent_result_text(res, "target_missing");
    break;
  case AGENT_TOOL_READ_MESSAGE:
    acquire(&p->lock);
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
    release(&p->lock);
    break;
  case AGENT_TOOL_FILE_META_INIT:
    rc = agent_file_meta_init();
    if (rc < 0) {
      res->status = rc;
      agent_result_text(res, rc == AGENT_STATUS_DENIED ? "denied" :
                        "not_agent");
      break;
    }
    res->value0 = AGENT_FILE_DEFAULT_USED;
    agent_result_text(res, "file_meta_init");
    break;
  case AGENT_TOOL_READ_FILE_SUMMARY:
    if (!agent_has_cap(p, AGENT_CAP_META_READ)) {
      res->status = AGENT_STATUS_DENIED;
      agent_result_text(res, "denied");
      break;
    }
    if (agent_text_empty(op->payload)) {
      res->status = AGENT_STATUS_BAD_PARAM;
      agent_result_text(res, "selector_required");
      break;
    }
    acquire(&agent_file_lock);
    found = agent_file_find_locked(op->payload);
    if (found >= 0) {
      res->value0 = agent_files[found].fid;
      res->value1 = agent_files[found].dependency_mask;
      res->value2 = agent_files[found].updated_tick;
      agent_result_text(res, agent_files[found].summary);
    }
    release(&agent_file_lock);
    if (found < 0) {
      res->status = AGENT_STATUS_NOT_FOUND;
      agent_result_text(res, "summary_not_found");
    }
    break;
  case AGENT_TOOL_DEPENDENCY_QUERY:
    if (!agent_has_cap(p, AGENT_CAP_META_READ)) {
      res->status = AGENT_STATUS_DENIED;
      agent_result_text(res, "denied");
      break;
    }
    if (agent_scope_from_payload(op->payload, 0, 1, &scope) < 0) {
      res->status = AGENT_STATUS_BAD_PARAM;
      agent_result_text(res, "bad_selector");
      break;
    }
    if (agent_file_dependency_for_scope(&scope, &deps) < 0) {
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
    agent_stage_affected_text(deps, res->result, sizeof(res->result));
    break;
  case AGENT_TOOL_CAPABILITY_CHECK:
    if (!agent_action_allowed(p, op->payload)) {
      res->status = AGENT_STATUS_DENIED;
      res->value0 = 0;
      res->value1 = p->agent_role;
      res->value2 = p->agent_capability_mask;
      agent_result_text(res, "denied");
    } else {
      res->value0 = 1;
      res->value1 = p->agent_role;
      res->value2 = p->agent_capability_mask;
      agent_result_text(res, "allow");
    }
    break;
  case AGENT_TOOL_RERUN_STAGE:
    if (!agent_action_allowed(p, "rerun_stage")) {
      res->status = AGENT_STATUS_DENIED;
      agent_result_text(res, "denied");
      agent_deliver_event_watchers(p->pid, AGENT_EVENT_POLICY_DENIED,
                                   op->request_id, "action=rerun_stage");
      break;
    }
    if (agent_scope_from_payload(op->payload, 0, 1, &scope) < 0) {
      res->status = AGENT_STATUS_BAD_PARAM;
      agent_result_text(res, "bad_selector");
      break;
    }
    if (agent_action_seen(op->request_id, &scope, "rerun_stage")) {
      res->status = AGENT_STATUS_DUPLICATE;
      agent_result_text(res, "duplicate");
      break;
    }
    if (agent_file_dependency_for_scope(&scope, &deps) < 0) {
      res->status = AGENT_STATUS_NOT_FOUND;
      agent_result_text(res, "stage_not_found");
      break;
    }
    acquire(&agent_file_lock);
    updated = agent_file_update_mask_scope_locked(&scope, deps, "ok",
                                                  "rerun by dependency mask");
    release(&agent_file_lock);
    if (updated == 0 && deps != 0) {
      res->status = AGENT_STATUS_NOT_FOUND;
      agent_result_text(res, "stage_not_found");
      break;
    }
    agent_action_remember(op->request_id, &scope, "rerun_stage");
    res->value0 = deps;
    res->value1 = op->request_id;
    res->value2 = updated;
    agent_result_text(res, "rerun_ok");
    safestrcpy(event_payload, "status=ok;action=rerun;stage=",
               sizeof(event_payload));
    agent_text_append(event_payload, scope.stage, sizeof(event_payload));
    agent_text_append(event_payload, ";affected=", sizeof(event_payload));
    agent_stage_affected_text(deps, res->result, sizeof(res->result));
    agent_text_append(event_payload, res->result, sizeof(event_payload));
    agent_result_text(res, "rerun_ok");
    delivered = agent_deliver_event_watchers(p->pid, AGENT_EVENT_JOB_DONE,
                                             op->request_id, event_payload);
    if (delivered < 0) {
      res->status = AGENT_STATUS_NO_SPACE;
      agent_result_text(res, "event_queue_full");
      break;
    }
    (void)delivered;
    break;
  case AGENT_TOOL_WRITE_REPORT:
    if (!agent_action_allowed(p, "write_report")) {
      res->status = AGENT_STATUS_DENIED;
      agent_result_text(res, "denied");
      break;
    }
    if (agent_scope_from_payload(op->payload, "report", 0, &scope) < 0) {
      res->status = AGENT_STATUS_BAD_PARAM;
      agent_result_text(res, "bad_selector");
      break;
    }
    acquire(&agent_file_lock);
    updated = agent_file_update_recovery_report_scope_locked(
        &scope, "ok", scope.summary[0] ? scope.summary :
                         "recovery report metadata ready");
    release(&agent_file_lock);
    if (updated == 0) {
      res->status = AGENT_STATUS_NOT_FOUND;
      agent_result_text(res, "report_not_found");
      break;
    }
    res->value0 = op->request_id;
    agent_result_text(res, "report_meta_updated");
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
    acquire(&agent_event_lock);
    p->agent_watch_valid = 1;
    p->agent_watch_event_type = op->arg0;
    safestrcpy(p->agent_watch_filter, op->payload,
               sizeof(p->agent_watch_filter));
    release(&agent_event_lock);
    res->value0 = op->arg0;
    agent_result_text(res, "watch");
    break;
  case AGENT_TOOL_AGENT_HEARTBEAT:
    if ((int)op->arg0 < 0) {
      res->status = AGENT_STATUS_BAD_PARAM;
      agent_result_text(res, "bad_interval");
      break;
    }
    acquire(&agent_event_lock);
    p->heartbeat_interval = op->arg0;
    p->agent_last_heartbeat_tick = agent_ticks();
    res->value0 = p->heartbeat_interval;
    res->value1 = p->agent_last_heartbeat_tick;
    release(&agent_event_lock);
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

static int
agent_execute_one(struct proc *p, struct agent_op *op, struct agent_result *res,
                  uint64 tick)
{
  op->payload[AGENT_OP_PAYLOAD_SIZE - 1] = 0;
  agent_result_init(res, op);
  p->agent_call_count++;
  res->sequence = p->agent_call_count;
  agent_execute_op(p, op, res);
  return agent_append_context(p, op, res, tick);
}

int
agent_tool_call(uint64 reqaddr, uint64 respaddr)
{
  struct proc *p = myproc();
  struct agent_request req;
  struct agent_response resp;
  struct agent_result fast;
  struct agent_op op;
  struct agent_tool_desc *tool;
  uint64 tick;

  if (copyin(p->pagetable, (char *)&req, reqaddr, sizeof(req)) < 0)
    return -1;

  req.tool_name[AGENT_TOOL_NAME_SIZE - 1] = 0;
  req.arg0_key[AGENT_PARAM_KEY_SIZE - 1] = 0;
  req.arg1_key[AGENT_PARAM_KEY_SIZE - 1] = 0;
  req.payload_key[AGENT_PARAM_KEY_SIZE - 1] = 0;
  req.payload[AGENT_PAYLOAD_SIZE - 1] = 0;
  if (!agent_user_range_writable(p, respaddr, sizeof(resp)))
    return -1;

  memset(&resp, 0, sizeof(resp));
  resp.version = AGENT_CALL_VERSION;
  resp.request_id = req.request_id;
  if (p->is_agent == 0) {
    resp.status = AGENT_STATUS_NOT_AGENT;
    safestrcpy(resp.result, "not_agent", sizeof(resp.result));
  } else {
    memset(&op, 0, sizeof(op));
    op.version = req.version;
    op.tool_id = req.tool_id;
    op.request_id = req.request_id;
    op.arg0 = req.arg0;
    op.arg1 = req.arg1;
    safestrcpy(op.payload, req.payload, sizeof(op.payload));

    if (req.version == AGENT_CALL_VERSION) {
      agent_init_response(&resp, &req, 0);
      tool = agent_resolve_tool(&req, &resp);
      if (tool && resp.status == AGENT_STATUS_OK) {
        op.tool_id = tool->tool_id;
        safestrcpy(req.tool_name, tool->name, sizeof(req.tool_name));
        agent_validate_legacy_params(&req, &resp, tool->tool_id);
      }
    }

    p->loop_state = AGENT_LOOP_RUNNING;

    if (resp.status != AGENT_STATUS_OK) {
      agent_result_init(&fast, &op);
      p->agent_call_count++;
      fast.sequence = p->agent_call_count;
      fast.status = resp.status;
      agent_result_text(&fast, resp.result);
      tick = agent_ticks();
      if (agent_append_context(p, &op, &fast, tick) < 0 ||
          agent_write_header(p) < 0) {
        agent_result_no_space(&fast);
      }
    } else {
      tick = agent_ticks();
      if (agent_execute_one(p, &op, &fast, tick) < 0 ||
          agent_write_header(p) < 0) {
        agent_result_no_space(&fast);
      }
    }

    resp.version = AGENT_CALL_VERSION;
    resp.status = fast.status;
    resp.tool_id = fast.tool_id;
    resp.request_id = fast.request_id;
    resp.sequence = fast.sequence;
    resp.value0 = fast.value0;
    resp.value1 = fast.value1;
    resp.value2 = fast.value2;
    tool = agent_tool_by_id(fast.tool_id);
    if (tool)
      safestrcpy(resp.tool_name, tool->name, sizeof(resp.tool_name));
    else
      safestrcpy(resp.tool_name, req.tool_name, sizeof(resp.tool_name));
    safestrcpy(resp.result, fast.result, sizeof(resp.result));
    p->loop_state = AGENT_LOOP_IDLE;
  }

  if (copyout(p->pagetable, respaddr, (char *)&resp, sizeof(resp)) < 0)
    return -1;

  return 0;
}

int
agent_run(uint64 opsaddr, uint64 resultsaddr, int count, uint64 flags)
{
  struct proc *p = myproc();
  struct agent_op op;
  struct agent_result res;
  uint64 tick;
  int i;

  if (p->is_agent == 0)
    return -1;
  if (flags != 0)
    return AGENT_STATUS_BAD_PARAM;
  if (count < 0 || count > AGENT_BATCH_MAX)
    return -1;
  if (count == 0)
    return 0;

  for (i = 0; i < count; i++) {
    if (copyin(p->pagetable, (char *)&op,
               opsaddr + i * sizeof(struct agent_op), sizeof(op)) < 0)
      return -1;
  }
  if (!agent_user_range_writable(
          p, resultsaddr, (uint64)count * sizeof(struct agent_result)))
    return -1;

  tick = agent_ticks();
  p->loop_state = AGENT_LOOP_RUNNING;
  for (i = 0; i < count; i++) {
    if (copyin(p->pagetable, (char *)&op,
               opsaddr + i * sizeof(struct agent_op), sizeof(op)) < 0) {
      p->loop_state = AGENT_LOOP_IDLE;
      return -1;
    }
    if (agent_execute_one(p, &op, &res, tick) < 0) {
      p->loop_state = AGENT_LOOP_IDLE;
      return -1;
    }
    if (copyout(p->pagetable, resultsaddr + i * sizeof(struct agent_result),
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

int
agent_tool_list(uint64 addr, int max)
{
  int n;
  struct proc *p = myproc();

  if (max < 0)
    return -1;

  n = max;
  if (n > AGENT_TOOL_COUNT)
    n = AGENT_TOOL_COUNT;

  if (n > 0) {
    if (copyout(p->pagetable, addr, (char *)agent_tools,
                n * sizeof(struct agent_tool_desc)) < 0)
      return -1;
  }

  return AGENT_TOOL_COUNT;
}

int
agent_context_push(uint64 recordaddr)
{
  struct proc *p = myproc();
  struct agent_context_record record;
  struct agent_op op;
  struct agent_result latest;
  uint64 tick;

  if (p->is_agent == 0)
    return -1;
  if (copyin(p->pagetable, (char *)&record, recordaddr, sizeof(record)) < 0)
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
  tick = agent_ticks();
  memset(&latest, 0, sizeof(latest));
  latest.version = AGENT_CALL_VERSION;
  latest.status = record.status;
  latest.tool_id = record.tool_id;
  latest.request_id = record.request_id;
  latest.sequence = p->agent_call_count;
  latest.value0 = record.value0;
  latest.value1 = record.value1;
  latest.value2 = record.value2;
  if (record.result[0])
    agent_result_text(&latest, record.result);
  else
    agent_result_text(&latest, "manual");

  if (agent_append_context(p, &op, &latest, tick) < 0)
    return AGENT_STATUS_NO_SPACE;
  if (agent_write_header(p) < 0)
    return AGENT_STATUS_NO_SPACE;
  return 0;
}

int
agent_context_query(uint64 start_sequence, uint64 outaddr, int max)
{
  struct proc *p = myproc();
  struct agent_context_record record;
  uint64 slot;
  uint64 first_slot;
  uint64 i;
  int copied = 0;

  if (p->is_agent == 0)
    return -1;
  if (max < 0)
    return -1;
  if (max == 0 || p->context_path_count == 0)
    return 0;

  first_slot = (p->context_path_head + p->context_path_capacity -
                p->context_path_count) % p->context_path_capacity;
  for (i = 0; i < p->context_path_count && copied < max; i++) {
    slot = (first_slot + i) % p->context_path_capacity;
    if (agent_read_context_record(p, slot, &record) < 0)
      return AGENT_STATUS_NO_SPACE;
    if (record.sequence > 0 &&
        (start_sequence == 0 || record.sequence >= start_sequence)) {
      if (copyout(p->pagetable,
                  outaddr + copied * sizeof(struct agent_context_record),
                  (char *)&record, sizeof(record)) < 0)
        return -1;
      copied++;
    }
  }

  return copied;
}

int
agent_context_snapshot(uint64 headeraddr, uint64 recordsaddr, int max)
{
  struct proc *p = myproc();
  struct agent_context_header *header;

  if (p->is_agent == 0)
    return -1;
  if (max < 0)
    return -1;
  if (agent_write_header(p) < 0 || agent_sync_context_all(p) < 0)
    return AGENT_STATUS_NO_SPACE;
  header = agent_context_header_ptr(p);
  if (header == 0)
    return AGENT_STATUS_NO_SPACE;
  if (headeraddr != 0) {
    if (copyout(p->pagetable, headeraddr, (char *)header, sizeof(*header)) < 0)
      return -1;
  }
  if (max == 0 || recordsaddr == 0)
    return 0;
  return agent_context_query(0, recordsaddr, max);
}

int
agent_context_rollback(uint64 sequence)
{
  struct proc *p = myproc();
  struct agent_context_record record;
  struct agent_result latest;
  uint64 slot;
  uint64 first_slot;
  uint64 new_count;

  if (p->is_agent == 0)
    return -1;
  if (p->context_path_count == 0 || sequence < p->context_path_oldest ||
      sequence > p->context_path_latest)
    return AGENT_STATUS_NOT_FOUND;

  slot = (sequence - 1) % p->context_path_capacity;
  if (agent_read_context_record(p, slot, &record) < 0)
    return AGENT_STATUS_NO_SPACE;
  if (record.sequence != sequence)
    return AGENT_STATUS_NOT_FOUND;

  first_slot = (p->context_path_head + p->context_path_capacity -
                p->context_path_count) % p->context_path_capacity;
  new_count = ((slot + p->context_path_capacity - first_slot) %
               p->context_path_capacity) + 1;
  p->context_path_latest = sequence;
  p->context_path_count = new_count;
  p->context_path_head = (slot + 1) % p->context_path_capacity;
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

  if (agent_write_latest_result(p, &latest) < 0)
    return AGENT_STATUS_NO_SPACE;
  if (agent_write_header(p) < 0)
    return AGENT_STATUS_NO_SPACE;
  return 0;
}

int
agent_context_clear(void)
{
  struct proc *p = myproc();
  uint64 i;

  if (p->is_agent == 0)
    return -1;

  p->agent_call_count = 0;
  p->context_path_count = 0;
  p->context_path_head = 0;
  p->context_path_oldest = 0;
  p->context_path_latest = 0;
  p->context_path_dropped = 0;
  p->context_path_rollback_count = 0;

  for (i = 1; i < AGENT_CONTEXT_PAGES; i++)
    if (p->agent_shadow_kva[i])
      memset((void *)p->agent_shadow_kva[i], 0, PGSIZE);

  if (agent_write_latest_result(p, 0) < 0)
    return AGENT_STATUS_NO_SPACE;
  if (agent_write_header(p) < 0)
    return AGENT_STATUS_NO_SPACE;
  if (agent_sync_context_all(p) < 0)
    return AGENT_STATUS_NO_SPACE;
  return 0;
}

int
agent_watch(int event_type, uint64 filteraddr)
{
  struct proc *p = myproc();
  char filter[AGENT_WATCH_FILTER_SIZE];

  if (p->is_agent == 0)
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

  acquire(&agent_event_lock);
  p->agent_watch_valid = 1;
  p->agent_watch_event_type = event_type;
  safestrcpy(p->agent_watch_filter, filter, sizeof(p->agent_watch_filter));
  p->loop_state = AGENT_LOOP_IDLE;
  release(&agent_event_lock);

  agent_append_system_context(p, AGENT_TOOL_AGENT_WATCH, 0, event_type, filter,
                              "watch", AGENT_STATUS_OK, event_type, 0, 0);
  return 0;
}

int
agent_unwatch(int event_type, uint64 filteraddr)
{
  struct proc *p = myproc();
  char filter[AGENT_WATCH_FILTER_SIZE];
  int clear_all;
  int match;

  if (p->is_agent == 0)
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

  clear_all = event_type == AGENT_EVENT_NONE && filter[0] == 0;
  acquire(&agent_event_lock);
  match = clear_all ||
          (p->agent_watch_valid &&
           p->agent_watch_event_type == event_type &&
           strncmp(p->agent_watch_filter, filter,
                   sizeof(p->agent_watch_filter)) == 0);
  if (match) {
    p->agent_watch_valid = 0;
    p->agent_watch_event_type = AGENT_EVENT_NONE;
    memset(p->agent_watch_filter, 0, sizeof(p->agent_watch_filter));
    p->loop_state = AGENT_LOOP_IDLE;
  }
  release(&agent_event_lock);

  if (!match)
    return AGENT_STATUS_NOT_FOUND;
  agent_append_system_context(p, AGENT_TOOL_AGENT_WATCH, 0, event_type,
                              "unwatch", "unwatch", AGENT_STATUS_OK,
                              event_type, 0, 0);
  return 0;
}

int
agent_wait(uint64 eventaddr, int timeout_ticks)
{
  struct proc *p = myproc();
  struct agent_event event;
  uint64 start;
  uint64 now;
  int status;
  int slot;

  if (p->is_agent == 0)
    return -1;
  if (eventaddr != 0 &&
      !agent_user_range_writable(p, eventaddr, sizeof(event)))
    return -1;

  memset(&event, 0, sizeof(event));
  start = agent_ticks();
  acquire(&agent_event_lock);
  p->agent_wait_count++;
  p->agent_wait_deadline = timeout_ticks >= 0 ? start + timeout_ticks : 0;
  for (;;) {
    if (p->agent_event_queued > 0) {
      slot = p->agent_event_head;
      event.type = p->agent_event_type[slot];
      event.source_pid = p->agent_event_source_pid[slot];
      event.target_pid = p->pid;
      event.status = AGENT_STATUS_OK;
      event.event_id = p->agent_event_id[slot];
      event.tick = p->agent_event_tick[slot];
      event.corr_id = p->agent_event_corr_id[slot];
      safestrcpy(event.payload, p->agent_event_payload[slot],
                 sizeof(event.payload));
      p->agent_event_type[slot] = AGENT_EVENT_NONE;
      p->agent_event_source_pid[slot] = 0;
      p->agent_event_id[slot] = 0;
      p->agent_event_tick[slot] = 0;
      p->agent_event_corr_id[slot] = 0;
      memset(p->agent_event_payload[slot], 0,
             sizeof(p->agent_event_payload[slot]));
      p->agent_event_head =
          (p->agent_event_head + 1) % AGENT_EVENT_QUEUE_MAX;
      p->agent_event_queued--;
      p->agent_wait_deadline = 0;
      p->loop_state = AGENT_LOOP_RUNNING;
      status = AGENT_STATUS_OK;
      break;
    }
    now = agent_ticks();
    if (timeout_ticks >= 0 && now - start >= (uint64)timeout_ticks) {
      p->agent_timeout_count++;
      p->agent_wait_deadline = 0;
      p->loop_state = AGENT_LOOP_IDLE;
      event.type = AGENT_EVENT_TIMER;
      event.source_pid = 0;
      event.target_pid = p->pid;
      event.status = AGENT_STATUS_TIMEOUT;
      event.event_id = 0;
      event.tick = now;
      event.corr_id = 0;
      safestrcpy(event.payload, "timeout", sizeof(event.payload));
      status = AGENT_STATUS_TIMEOUT;
      break;
    }
    if (killed(p)) {
      p->agent_wait_deadline = 0;
      p->loop_state = AGENT_LOOP_IDLE;
      release(&agent_event_lock);
      return -1;
    }
    p->loop_state = AGENT_LOOP_WAITING;
    sleep(p, &agent_event_lock);
  }
  release(&agent_event_lock);

  if (eventaddr != 0 &&
      copyout(p->pagetable, eventaddr, (char *)&event, sizeof(event)) < 0)
    return -1;
  if (status == AGENT_STATUS_OK) {
    agent_append_system_context(p, AGENT_TOOL_AGENT_WAIT, event.event_id,
                                event.type, event.payload, "event",
                                AGENT_STATUS_OK, event.type,
                                event.source_pid, event.corr_id);
    acquire(&agent_event_lock);
    if (p->loop_state == AGENT_LOOP_RUNNING)
      p->loop_state = AGENT_LOOP_IDLE;
    release(&agent_event_lock);
  }
  return status;
}

int
agent_heartbeat(int interval_ticks)
{
  struct proc *p = myproc();

  if (p->is_agent == 0)
    return -1;
  if (interval_ticks < 0)
    return AGENT_STATUS_BAD_PARAM;
  acquire(&agent_event_lock);
  p->heartbeat_interval = interval_ticks;
  p->agent_last_heartbeat_tick = agent_ticks();
  release(&agent_event_lock);
  agent_append_system_context(p, AGENT_TOOL_AGENT_HEARTBEAT, 0,
                              interval_ticks, "heartbeat", "heartbeat",
                              AGENT_STATUS_OK, interval_ticks,
                              p->agent_last_heartbeat_tick, 0);
  return 0;
}

int
agent_heartbeat_stop(void)
{
  return agent_heartbeat(0);
}

int
agent_set_role(int role)
{
  struct proc *p = myproc();
  uint64 caps;

  if (p->is_agent == 0)
    return -1;
  caps = agent_caps_for_role(role);
  if (caps == 0)
    return AGENT_STATUS_BAD_PARAM;
  if (role != p->agent_role) {
    agent_append_system_context(p, AGENT_TOOL_CAPABILITY_CHECK, 0, role,
                                "set_role", "role_denied",
                                AGENT_STATUS_DENIED, p->agent_role,
                                p->agent_capability_mask, 0);
    return AGENT_STATUS_DENIED;
  }
  p->agent_role = role;
  p->agent_capability_mask = caps;
  agent_append_system_context(p, AGENT_TOOL_CAPABILITY_CHECK, 0, role,
                              "set_role", "role_set", AGENT_STATUS_OK,
                              role, caps, 0);
  return 0;
}

int
agent_wake(int pid, uint64 eventaddr)
{
  struct proc *p = myproc();
  struct agent_event event;
  char payload[AGENT_EVENT_PAYLOAD_SIZE];
  int delivered;

  if (p->is_agent == 0)
    return -1;
  if (!agent_has_cap(p, AGENT_CAP_EVENT_WAKE))
    return AGENT_STATUS_DENIED;
  if (eventaddr == 0)
    return AGENT_STATUS_BAD_PARAM;
  if (copyin(p->pagetable, (char *)&event, eventaddr, sizeof(event)) < 0)
    return -1;
  event.payload[sizeof(event.payload) - 1] = 0;
  safestrcpy(payload, event.payload, sizeof(payload));
  delivered =
      agent_deliver_event_pid(pid, p->pid, event.type, event.corr_id, payload);
  if (delivered < 0)
    return AGENT_STATUS_NO_SPACE;
  if (delivered == 0)
    return AGENT_STATUS_NOT_FOUND;
  return 0;
}

int
agent_file_meta_init(void)
{
  struct proc *p = myproc();

  if (p->is_agent == 0)
    return -1;
  if (!agent_has_cap(p, AGENT_CAP_META_WRITE))
    return AGENT_STATUS_DENIED;
  agent_action_reset();
  acquire(&agent_file_lock);
  agent_file_install_default_locked();
  release(&agent_file_lock);
  return 0;
}

int
agent_file_meta_set(uint64 metaaddr)
{
  struct proc *p = myproc();
  struct agent_file_meta meta;
  int i;
  int slot = -1;
  int status_changed = 0;
  int delivered;
  char event_payload[AGENT_EVENT_PAYLOAD_SIZE];

  if (p->is_agent == 0)
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

  acquire(&agent_file_lock);
  for (i = 0; i < AGENT_FILE_META_MAX; i++) {
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
  if (meta.update_mask & AGENT_FILE_META_DELETE) {
    if (meta.fid <= 0 && meta.physical_name[0] == 0 &&
        meta.logical_path[0] == 0) {
      release(&agent_file_lock);
      return AGENT_STATUS_BAD_PARAM;
    }
    if (slot < 0) {
      release(&agent_file_lock);
      return AGENT_STATUS_NOT_FOUND;
    }
    memset(&agent_files[slot], 0, sizeof(agent_files[slot]));
    agent_file_rebuild_indexes_locked();
    release(&agent_file_lock);
    return 0;
  }
  if (slot < 0) {
    for (i = 0; i < AGENT_FILE_META_MAX; i++) {
      if (!agent_files[i].used) {
        slot = i;
        break;
      }
    }
  }
  if (slot < 0) {
    release(&agent_file_lock);
    return AGENT_STATUS_NO_SPACE;
  }
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
                sizeof(agent_files[slot].status)) != 0)
      status_changed = 1;
    safestrcpy(agent_files[slot].status, meta.status,
               sizeof(agent_files[slot].status));
  }
  if (meta.summary[0])
    safestrcpy(agent_files[slot].summary, meta.summary,
               sizeof(agent_files[slot].summary));
  if ((meta.update_mask & AGENT_FILE_META_UPDATE_DEPS) ||
      meta.dependency_mask)
    agent_files[slot].dependency_mask = meta.dependency_mask;
  agent_files[slot].updated_tick = agent_ticks();
  agent_file_rebuild_indexes_locked();
  agent_file_event_payload(&agent_files[slot], event_payload,
                           sizeof(event_payload));
  release(&agent_file_lock);

  if (status_changed && meta.status[0]) {
    delivered = agent_deliver_event_watchers(p->pid, AGENT_EVENT_FILE_STATUS,
                                             meta.fid, event_payload);
    if (delivered < 0)
      return AGENT_STATUS_NO_SPACE;
  }
  return 0;
}

int
agent_file_query(uint64 queryaddr, uint64 resultaddr)
{
  struct proc *p = myproc();
  struct agent_file_query query;
  struct agent_file_query_result *result;
  int returned;

  if (p->is_agent == 0)
    return -1;
  if (!agent_has_cap(p, AGENT_CAP_META_READ))
    return AGENT_STATUS_DENIED;
  if (copyin(p->pagetable, (char *)&query, queryaddr, sizeof(query)) < 0)
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
  if (!agent_query_has_filter(&query))
    return AGENT_STATUS_BAD_PARAM;
  if (!agent_user_range_writable(p, resultaddr,
                                 sizeof(struct agent_file_query_result)))
    return -1;

  result = (struct agent_file_query_result *)kalloc();
  if (result == 0)
    return AGENT_STATUS_NO_SPACE;
  acquire(&agent_file_lock);
  agent_file_query_locked(&query, result);
  release(&agent_file_lock);
  if (copyout(p->pagetable, resultaddr, (char *)result, sizeof(*result)) < 0) {
    kfree((void *)result);
    return -1;
  }
  agent_append_system_context(p, AGENT_TOOL_QUERY_FILE, 0, query.flags,
                              query.status[0] ? query.status : query.stage,
                              result->returned ?
                                  result->hits[0].physical_name :
                                  "empty",
                              AGENT_STATUS_OK, result->total_hits,
                              result->scanned_records, result->used_index);
  returned = result->returned;
  kfree((void *)result);
  return returned;
}

void
agent_tick(void)
{
  struct proc *p;
  uint64 now;
  char payload[AGENT_EVENT_PAYLOAD_SIZE];

  now = agent_ticks();
  acquire(&agent_event_lock);
  for (p = proc; p < &proc[NPROC]; p++) {
    if (p->state == UNUSED || !p->is_agent)
      continue;
    if (p->loop_state == AGENT_LOOP_WAITING &&
        p->agent_wait_deadline > 0 && now >= p->agent_wait_deadline)
      wakeup(p);
    if (p->heartbeat_interval > 0 &&
        now - p->agent_last_heartbeat_tick >=
            (uint64)p->heartbeat_interval) {
      p->agent_last_heartbeat_tick = now;
      safestrcpy(payload, "timer=heartbeat", sizeof(payload));
      agent_queue_event_locked(p, 0, AGENT_EVENT_TIMER, now, payload);
    }
  }
  release(&agent_event_lock);
}
