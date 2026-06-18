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
  {AGENT_TOOL_QUERY_FILE, "query_file", "path:string",
   "return basic inode information for a named file"},
  {AGENT_TOOL_SEND_MESSAGE, "send_message", "target_pid:uint64,message:string",
   "store a short message in another Agent process mailbox"},
  {AGENT_TOOL_READ_MESSAGE, "read_message", "none",
   "read the current Agent process mailbox"},
};

struct agent_context_prefix {
  struct agent_context_header header;
  struct agent_result latest;
};

struct agent_proc_snapshot {
  int used;
  int agents;
  int runnable;
};

void
agentinit(void)
{
  initlock(&agentid_lock, "nextagentid");
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
agent_make(struct proc *p, pagetable_t pagetable)
{
  if (p->sz > AGENT_CONTEXT_BASE)
    return -1;
  if (agent_map_context(p, pagetable) < 0)
    return -1;

  p->is_agent = 1;
  p->agent_type = AGENT_TYPE_AGENT;
  p->agent_id = agent_alloc_id();
  p->agent_ctx_base = AGENT_CONTEXT_BASE;
  p->agent_ctx_size = AGENT_CONTEXT_SIZE;
  p->heartbeat_interval = AGENT_DEFAULT_HEARTBEAT_INTERVAL;
  p->resource_quota = AGENT_DEFAULT_RESOURCE_QUOTA;
  p->loop_state = AGENT_LOOP_IDLE;

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

static void
agent_execute_op(struct proc *p, struct agent_op *op, struct agent_result *res)
{
  struct agent_proc_snapshot snapshot;
  struct inode *ip;
  struct proc *target;

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
    if (agent_text_empty(op->payload)) {
      res->status = AGENT_STATUS_BAD_PARAM;
      agent_result_text(res, "path_required");
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
    if (op->arg0 == 0 || agent_text_empty(op->payload)) {
      res->status = AGENT_STATUS_BAD_PARAM;
      agent_result_text(res, "message_required");
      break;
    }
    for (target = proc; target < &proc[NPROC]; target++) {
      acquire(&target->lock);
      if (target->state != UNUSED && target->pid == (int)op->arg0 &&
          target->is_agent) {
        target->agent_mailbox_valid = 1;
        target->agent_mailbox_from = p->pid;
        safestrcpy(target->agent_mailbox, op->payload,
                   sizeof(target->agent_mailbox));
        release(&target->lock);
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

  (void)flags;
  if (p->is_agent == 0)
    return -1;
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
  uint64 seq;
  uint64 slot;
  int copied = 0;

  if (p->is_agent == 0)
    return -1;
  if (max < 0)
    return -1;
  if (max == 0 || p->context_path_count == 0)
    return 0;

  if (start_sequence == 0 || start_sequence < p->context_path_oldest)
    seq = p->context_path_oldest;
  else
    seq = start_sequence;

  if (seq > p->context_path_latest)
    return 0;

  while (seq <= p->context_path_latest && copied < max) {
    slot = (seq - 1) % p->context_path_capacity;
    if (agent_read_context_record(p, slot, &record) < 0)
      return AGENT_STATUS_NO_SPACE;
    if (record.sequence == seq) {
      if (copyout(p->pagetable,
                  outaddr + copied * sizeof(struct agent_context_record),
                  (char *)&record, sizeof(record)) < 0)
        return -1;
      copied++;
    }
    seq++;
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
