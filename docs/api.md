# AgentOS 系统调用与 ABI

AgentOS 通过 uCore 系统调用向 Guest 提供身份、workflow、工具、Context、文件查询、事件、调度、资源和 Task Channel。公开声明集中在 [`user/include/agent.h`](../user/include/agent.h)，版本化结构位于 `include/agent_*_abi.h`，用户态封装位于 [`user/lib/syscall.c`](../user/lib/syscall.c)。

## ABI 约定

- 版本化结构体填写 `version`，并按结构定义填写 `size` 或 `struct_size`（若存在），保留字段置零；
- 内核重新 copyin 指针、数组和字符串，固定字符串必须在字段范围内以 NUL 结束；
- workflow 对象使用完整 `{id, generation}`；
- Task 对象同时检查 channel、ring 和 slot generation；
- Task Channel command 的 request id 严格递增；workflow fence 允许同一 id 的精确重放，工具 request id 按接口承担相关性标识；
- syscall 返回入口处理结果，response、CQE 和 fence receipt 继续提供结构化状态。

工具状态定义在 [`agent_tool_abi.h`](../include/agent_tool_abi.h)，主要包括 `OK`、`BAD_REQUEST`、`NO_SPACE`、`TIMEOUT`、`DENIED`、`DUPLICATE`、`CANCELLED`、`CONFLICT`、`STALE` 和 `RETRY`。

ABI checker 使用 RISC-V64 probe、静态断言和冻结清单验证结构大小、字段 offset、枚举值与 syscall 号：

```bash
make agent-uapi-check
```

## 系统调用分组

系统调用号定义在 [`os/syscall_ids.h`](../os/syscall_ids.h)。

| 分组 | Syscall | 用户态封装 |
| --- | --- | --- |
| 身份 | 500、501、517、561 | `agent_create()`、`agent_info()`、`agent_create_role()`、`agent_launch_info()` |
| Workflow | 539、541、542、545、546 | `agent_worker_create()`、`agent_workflow_create()`、`agent_scope_delegate_fd()`、`agent_workflow_close()`、`agent_workflow_lifecycle_info()` |
| 工具 | 502、503、504、547、548 | `agent_run()`、`agent_call()`、`agent_tool_list()`、`tool_call()`、`tool_call_v3()`、`tool_list()` |
| Context | 505-509、519 | `context_push/query/snapshot/detail/rollback/clear()` |
| 文件状态 | 514-516、535-538 | `agent_file_meta_*()`、`agent_file_query()`、`agent_file_edit_*()` |
| 事件与 IPC | 510-513、518、520、540、552、553 | `agent_watch()`、`agent_live_watch/unwatch()`、`agent_wait()`、`agent_wait_cancel()`、`agent_heartbeat_set/stop()`、`agent_wake()`、`agent_route_config()` |
| 观测与调度 | 521-525、528-534、557、559、560 | `agent_sched_*()`、`agent_trace_snapshot()`、`agent_audit_*()`、`agent_span_trace_snapshot()`、`agent_timeline_*()`、`agent_provenance_snapshot()`、`agent_ledger_snapshot()`、`agent_resource_snapshot()`、`agent_performance_snapshot()` |
| 执行合同 | 562 | `agent_execution_contract()` |
| Task Channel | 563-565 | `agent_task_channel_setup/enter/resource()` |

`tool_call_v3()` 与 V2 共用 `SYS_tool_call`，内核根据 request version 选择布局。`agent_workflow_fence()` 复用 `SYS_agent_run`，要求 `count == 0` 且 `flags == AGENT_RUN_F_FENCE`。

## 身份与 Workflow

```c
int agent_workflow_create(int role);
int agent_worker_create(const char *image, uint64 capabilities);
int agent_info(struct agent_info *info);
int agent_workflow_lifecycle_info(
    struct agent_workflow_lifecycle_info *info,
    const struct agent_workflow_lifecycle_key *expected);
int agent_scope_delegate_fd(int fd);
int agent_workflow_close(uint64 scope_id);
```

`agent_workflow_create()` 创建 controller 与 lifecycle root，返回 controller pid。`agent_workflow_lifecycle_info()` 返回 lifecycle key、Context lane、metadata transaction、resource account identity 和 workflow EEVDF 快照。`agent_worker_create()` 在当前 lifecycle 内安装 worker 身份，内核验证映像、role、capability 与 scope。

文件描述符通过 `agent_scope_delegate_fd()` 逐个进入 workflow scope。close 使 lifecycle 转入 closing，成员、active operation 和资源账户完成结算后回收槽位。

## 工具调用

V1 使用固定长度结构：

```c
int agent_call(struct agent_request *req,
               struct agent_response *resp);
int agent_tool_list(struct agent_tool_desc *out, int max);
```

V2 使用 typed 参数数组：

```c
int tool_list(struct agent_tool_desc_v2 *out, int max);
int tool_call(struct agent_request_v2 *req,
              struct agent_response_v2 *resp);
```

单次 V2 request 最多包含 8 个 `agent_param_v2`。每个参数独立声明 key、type 与 value size，内核核对 schema、重复 key、未知 key、字符串长度和 `tool_id/tool_name` 一致性。

V3 绑定执行合同：

```c
int tool_call_v3(struct agent_request_v3 *req,
                 struct agent_response_v3 *resp);
```

request 增加 contract key、node id、attempt、schema digest、input fingerprint、source Context sequence 和 artifact 类型。response 使用 `AGENT_RESPONSE_V3_F_CACHED` 表示已完成终态。

## Execution Contract

```c
int agent_execution_contract(
    const struct agent_execution_contract_control *control,
    struct agent_execution_contract_result *result);
```

control 支持 `CREATE`、`QUERY` 和 `RETIRE`。每个 lifecycle 持有一份 immutable contract，最多 24 个节点与 48 个 accepted attempt 终态槽；单节点最多 4 次 attempt。节点按拓扑顺序提交，`predecessor_mask` 只能引用已有前驱。

`AGENT_EXECUTION_CONTRACT_F_ENFORCE` 启用后，V3 调用和受约束的直接系统调用匹配当前合同。节点状态包括 `BLOCKED`、`READY`、`RUNNING`、`SUCCEEDED`、`FAILED` 与 `CANCELLED`。完整结构见 [`agent_execution_contract_abi.h`](../include/agent_execution_contract_abi.h)。

## Batch 与 Workflow fence

```c
int agent_run(struct agent_op *ops,
              struct agent_result *results,
              int count, uint64 flags);

int agent_workflow_fence(
    const struct agent_workflow_fence_request *request,
    struct agent_workflow_fence_receipt *receipt);
```

compact batch 一次提交最多 64 项，每项返回独立结果。fence request 为 56 字节，包含 32 字节 challenge 与 request id；receipt 固定为 320 字节，记录 lifecycle key、fence sequence、metadata generation、credit epoch、执行记录范围、八类资源用量和摘要。flags 标记 partial coverage、精确 credit、记录 seal 和 volatile metadata。布局见 [`agent_workflow_fence_abi.h`](../include/agent_workflow_fence_abi.h)。

## Context Path

```c
int context_push(struct agent_context_record *record);
int context_query(uint64 start_sequence,
                  struct agent_context_record *out, int max);
int context_snapshot(struct agent_context_header *header,
                     struct agent_context_record *records, int max);
int context_detail(uint64 sequence,
                   struct agent_context_detail *detail);
int context_rollback(uint64 sequence);
int context_clear(void);
```

Context 区固定为 7 页，record 容量为 128。内核页包含 header、latest response 和 record window，最后一页为用户 cache。直接读取 helper 使用 publish sequence 检查快照一致性。rollback 移动活动 branch head；目标 sequence 不在保留窗口时返回 `NOT_FOUND`，path summary 或 generation 一致性失败时返回 `STALE`。

## 文件状态

```c
int agent_file_meta_init(void);
int agent_file_meta_set(struct agent_file_meta *meta);
int agent_file_query(struct agent_file_query *query,
                     struct agent_file_query_result *result);
int agent_file_edit_begin(const char *path, uint64 flags, int ttl_ticks,
                          struct agent_file_edit_state *state);
int agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
                           struct agent_file_edit_state *state);
int agent_file_edit_abort(uint64 lease_id);
```

metadata 通过 `update_mask` 指定修改字段，`AGENT_FILE_META_F_DELETE` 删除记录。catalog 容量为 512，单次 query 最多返回 8 个 hit。`USE_INDEX` 允许选择 status、stage 或 kind 索引，`SCAN` 强制 traversal。result 返回 plan、checked count、query ticks、plan reason 和 `fs_generation`。

编辑租约使用 lease id、版本与 TTL。commit 的 `expected_version` 用于检测并发修改。

## Typed watch 与 IPC

```c
int agent_live_watch(struct agent_file_live_watch *watch);
int agent_live_unwatch(struct agent_file_live_watch *watch);
int agent_wait(struct agent_event *event, int timeout_ticks);
int agent_wait_cancel(int pid, const char *reason);
int agent_wake(int pid, struct agent_event *event);
int agent_route_config(int source_pid, int target_pid,
                       uint64 event_mask, int operation);
```

typed watch 保存完整 query 谓词，返回 watch id 与 generation。集合变化产生 `ENTER`、`UPDATE`、`LEAVE`；`RESYNC_REQUIRED` 要求重新查询并携带 `ACK_RESYNC` 更新基线。

事件队列容量为 16，并为内核事件与来源类别保留槽位。跨 Agent `MESSAGE` 与 `LLM_DONE` 需要显式 route、相同 active workflow 和匹配身份。`agent_wake()` 只接受允许由用户态注入的事件类型。

## 调度、资源与 Timeline

```c
int agent_sched_config(struct agent_sched_config *config);
int agent_sched_snapshot(struct agent_sched_record *records, int max);
int agent_resource_snapshot(struct agent_resource_snapshot *snapshot);
int agent_performance_snapshot(struct agent_performance_snapshot *snapshot);
int agent_timeline_query(struct agent_timeline_filter *filter,
                         struct agent_timeline_record *records, int max);
int agent_timeline_read(struct agent_timeline_filter *filter,
                        struct agent_timeline_record *records, int max,
                        int timeout_ticks);
```

`agent_sched_config()` 配置 workflow 内的 per-Agent weight、priority、budget 和 policy，`agent_sched_snapshot()` 读取调用 Agent 自身的 dispatch、ready age、vruntime、budget 与 reason trace。外层 workflow EEVDF 使用固定等权服务，其 latency class、request、vruntime、virtual deadline、sleep decay 和 service cycles 通过 `agent_workflow_lifecycle_info()` 读取。resource snapshot 返回全局资源种类计数与 free-page 池状态，performance snapshot 返回全局内核工作量计数及 observer lifecycle 字段。timeline filter 可按 source、tick、span、pid、role、tool、event、status 和 cursor 组合查询。

## Task Channel

```c
int agent_task_channel_setup(
    const struct agent_task_channel_setup *request,
    struct agent_task_channel_setup_result *result);
int agent_task_channel_enter(
    const struct agent_task_channel_enter *request,
    struct agent_task_channel_enter_result *result);
int agent_task_channel_resource(
    const struct agent_task_channel_resource *request,
    struct agent_task_channel_resource_result *result);
```

channel 使用 single issuer，SQ/CQ 容量各 16，SQE、CQE 和 ring header 均为 128 字节。`setup` 建立映射，`enter` 提交并收割完成项，`resource` 处理 typed handle 的 import、release 和 query。

SQE 支持 submit、cancel、link 和 hard deadline。CQE flags 描述 cancelled、deadline、denied 与 link failed。16 字节 resource handle 由 slot、type、flags 和 generation 组成。完整布局见 [`agent_task_channel_abi.h`](../include/agent_task_channel_abi.h)。

## 状态处理

| 状态 | 调用方操作 |
| --- | --- |
| `BAD_REQUEST` / `BAD_*` | 修正 version、size、flags、参数或结构布局 |
| `DENIED` | 检查 role、capability、scope、来源和合同节点 |
| `NO_SPACE` | 释放对象、消费 CQ 或等待资源结算 |
| `STALE` / `CONFLICT` / `NOT_FOUND` | 重新读取 lifecycle、generation 或版本 |
| `RESYNC_REQUIRED` | 完成全量查询并确认新基线 |
| `RETRY` | 等待状态推进，并按具体接口的重放规则重试；fence 可精确重放，Task 新 command 使用更大的 request id |
| `TIMEOUT` / `CANCELLED` | 读取 response 或 CQE 的 terminal 状态 |

模块关系见[产品架构](architecture.md)，检查顺序见[安全机制](security.md)。
