# AgentOS 用户接口与 ABI

AgentOS 的公开接口由仓库根目录的 ABI 头文件和 [`user/include/agent.h`](../../user/include/agent.h) 定义。本文说明调用顺序、稳定字段和错误处理，不复制完整结构体；实现与文档冲突时，以公开头文件、静态断言和对应 QEMU 测试为准。

## 基本约定

- 所有版本化结构体都要填写 `version` 和 `size`，保留字段必须为零。
- 指针、字符串和数组在进入内核后重新复制并检查；固定字符串必须在字段范围内以 NUL 结束。
- workflow 对象使用完整 `id + generation`。PID、slot、inode 或 handle 单独出现不能证明身份。
- request id 在各自 lifecycle 或 channel 内单调递增。重复、过期和冲突分别返回结构化状态。
- 状态码以 [`agent_tool_abi.h`](../../agent_tool_abi.h) 为准：`OK` 为 0，错误包含 `BAD_REQUEST`、`NO_SPACE`、`TIMEOUT`、`DENIED`、`DUPLICATE`、`CANCELLED`、`CONFLICT`、`STALE`、`RETRY` 等。
- syscall 成功只说明请求被内核接受；业务状态仍应读取 response、CQE 或 receipt 中的 `status`。

## 典型调用顺序

```text
agent_workflow_create
  -> agent_info / agent_workflow_lifecycle_info
  -> 可选：agent_execution_contract(CREATE)
  -> tool_call(V2) / tool_call_v3 / agent_run
  -> Context、metadata、watch、event 与 IPC
  -> agent_workflow_fence
  -> agent_workflow_close
```

创建、调用和关闭都应在同一 lifecycle generation 内完成。收到 `STALE` 或 `CONFLICT` 后，调用方先重新读取 lifecycle 和合同状态，再决定是否重建请求；不要原样无限重试。

## 身份与 lifecycle

| 接口 | 作用 | 关键边界 |
| --- | --- | --- |
| `agent_create()` | 创建默认角色 Agent | 只走受控 Agent 创建路径 |
| `agent_create_role(role)` | 创建指定角色 Agent | role 必须在公开枚举内 |
| `agent_workflow_create(role)` | 建立 workflow root | 返回当前 scope；后续对象绑定 generation |
| `agent_worker_create(image, caps)` | 在当前 workflow 创建 worker | 映像、capability 和 scope 均由内核验证 |
| `agent_info(info)` | 读取调用者 Agent 状态 | self-only，不提供任意 PID 身份冒充 |
| `agent_workflow_lifecycle_info(info, expected)` | 查询当前 lifecycle | `expected` 可用于 generation 复核 |
| `agent_workflow_close(scope_id)` | 进入关闭流程 | 活动成员和操作未归零时不会提前回收 |

`agent_launch_info()` 保留受控启动信息，`agent_scope_delegate_fd()` 只委派经过检查的文件描述符。普通 `fork/exec` 不会自动提升为可信 Agent。

## 工具目录与调用

工具编号、状态码、参数类型和结构体布局定义在 [`agent_tool_abi.h`](../../agent_tool_abi.h)。当前目录有 25 项工具。

### V1

```c
int agent_call(struct agent_request *req,
               struct agent_response *resp);
```

V1 是固定定长兼容接口。新动态调用优先使用 V2；已有 Guest 可继续使用 V1，但不得绕过相同的身份、capability 和 scope 检查。

### V2

```c
int tool_list(struct agent_tool_desc_v2 *out, int max);
int tool_call(struct agent_request_v2 *req,
              struct agent_response_v2 *resp);
```

V2 request 指向最多 8 个 `agent_param_v2`。每个参数独立声明 key、type、value size；内核把参数集合与工具 schema 对齐，拒绝未知 key、重复 key、错误类型和越界字符串。`tool_id` 与 `tool_name` 同时提供时必须指向同一目录项。

V2 适合模型或 policy 根据上一轮真实结果动态决定下一次调用。它不携带冻结 DAG 的 predecessor、attempt、deadline 和输入 fingerprint。

### V3

```c
int tool_call_v3(struct agent_request_v3 *req,
                 struct agent_response_v3 *resp);
```

V3 保留完整 V2 request 前缀，并增加 execution contract key、node、attempt、schema digest、input fingerprint、source Context 和 artifact 类型。启用 `AGENT_EXECUTION_CONTRACT_F_ENFORCE` 后，调用必须命中当前 frozen contract。

合同控制接口为：

```c
int agent_execution_contract(
    const struct agent_execution_contract_control *control,
    struct agent_execution_contract_result *result);
```

支持 `CREATE`、`QUERY`、`RETIRE`。每个 lifecycle 最多一份 immutable contract，最多 24 个节点；节点按拓扑顺序提交。完整布局见 [`agent_execution_contract_abi.h`](../../agent_execution_contract_abi.h)。

### Compact batch

```c
int agent_run(struct agent_op *ops,
              struct agent_result *results,
              int count, uint64 flags);
```

batch 最多提交 64 项。它用于短小同步操作，降低 syscall 和复制次数；每项仍返回独立状态。`AGENT_RUN_F_FENCE` 与普通 batch 不能混用。

## Context Path

公开入口包括：

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

Context 固定映射 7 页，容量为 128 条记录。6 个内核页包含 header、latest response 和 record window，第 7 页为用户 cache。读取方用 publish sequence 检查一致性；不要修改内核页或把 cache 当作可信记录。

记录携带 cause、span、branch、tool、status 和 provenance。rollback 只移动逻辑 branch head，不修改已经发布的历史。目标 sequence 已淘汰时返回 `STALE` 或 `NOT_FOUND`。

## 文件 metadata 与查询

```c
int agent_file_meta_init(void);
int agent_file_meta_set(struct agent_file_meta *meta);
int agent_file_query(struct agent_file_query *query,
                     struct agent_file_query_result *result);
```

metadata 支持 physical/logical path、project、workflow、run、stage、kind、status、summary 和 dependency。`update_mask` 明确指定本次修改字段；`AGENT_FILE_META_F_DELETE` 删除记录。旧的 persist/autoscan 位保留数值兼容，但当前实现拒绝它们。

query 最多返回 8 个 hit，并报告实际 plan、检查记录数、query ticks、plan reason 和 `fs_generation`。`USE_INDEX` 允许 `status/stage/kind` 索引，`SCAN` 强制遍历；两条路径使用相同谓词和结果结构。

## Typed live watch

```c
int agent_live_watch(struct agent_file_live_watch *watch);
int agent_live_unwatch(struct agent_file_live_watch *watch);
int agent_wait(struct agent_event *event, int timeout_ticks);
```

watch 保存完整 `agent_file_query` 谓词并返回 `watch_id` 和 generation。集合变化产生 `ENTER`、`UPDATE`、`LEAVE`。出现丢失、队列压力或 generation 变化时设置 `RESYNC_REQUIRED`；调用方重新执行完整 query 后，以 `ACK_RESYNC` 更新基线。

`agent_watch(event_type, filter)` 是旧字符串兼容入口。新增文件查询功能应使用 typed watch，不依赖 substring 解释。

## 事件、IPC 与 heartbeat

事件队列容量为 16，并为内核事件和来源类别保留槽位。主要接口为：

```c
int agent_route_config(int source_pid, int target_pid,
                       uint64 event_mask, int operation);
int agent_wait(struct agent_event *event, int timeout_ticks);
int agent_wait_cancel(int pid, const char *reason);
int agent_wake(int pid, struct agent_event *event);
int agent_heartbeat_set(uint64 interval_ticks);
int agent_heartbeat_stop(void);
```

跨 Agent `MESSAGE` 与 `LLM_DONE` 需要显式 route、相同 active workflow 和匹配身份。file-query、policy-denied 等内核来源事件不能通过 `agent_wake()` 伪造。

LLM request/response 工具只提供 Guest 内的 correlation 与事件交付，不解析 prompt、provider JSON、HTTPS 或串口 frame。完整模型循环属于 `agentlive_ucore` 和 Host relay。

## 调度与观测

| 接口 | 返回内容 |
| --- | --- |
| `agent_sched_config()` | weight、priority、budget 和 policy 配置 |
| `agent_sched_snapshot()` | workflow dispatch、vruntime、budget 与原因位 |
| `agent_trace_snapshot()` | Context 与调度兼容视图 |
| `agent_timeline_query/read/wait()` | 带 cursor 和过滤条件的合并时间线 |
| `agent_audit_query()` | 兼容审计记录 |
| `agent_provenance_snapshot()` | Context/audit 来源边 |
| `agent_resource_snapshot()` | workflow 资源账户 |
| `agent_performance_snapshot()` | 实现定义的工作量计数 |

timeline、audit 和 observer 都是有界视图。没有读到某条低层事件不能证明它从未发生。

## Workflow fence

```c
int agent_workflow_fence(
    const struct agent_workflow_fence_request *request,
    struct agent_workflow_fence_receipt *receipt);
```

request 绑定 32 字节 challenge 和单调 request id。成功 receipt 固定为 320 字节，包含 lifecycle key、fence sequence、metadata generation、credit epoch、evidence 范围、资源用量、credit digest、previous root 和 evidence root。

metadata/live-query、credit 或 evidence 尚未达到同一 cut 时返回 `RETRY`。调用方应等待进展后重试，不应接受 partial receipt 代替完整封存。布局见 [`agent_workflow_fence_abi.h`](../../agent_workflow_fence_abi.h)。

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

每个 channel 为 single issuer，SQ/CQ 容量各 16。SQE、CQE 和 ring header 都是 128 字节；SQ 用户可写，CQ 用户只读。一个 accepted target 只产生一个 terminal CQE。共享水位、generation 或 slot generation 异常会设置 sticky `RESYNC`，issuer 必须显式重建可见 header。

typed handle 固定为 `{slot, type, flags, generation}`，共 16 字节。当前 provider 同步处理 null input，并返回 artifact `NONE`；`RESOURCE_IMPORT` 返回 `DENIED`。因此这些结构已经冻结 ABI，但当前没有业务 payload/result backend。完整合同见 [`agent_task_channel_abi.h`](../../agent_task_channel_abi.h)。

## Nexus 产品 ABI

Nexus `N1` TASK 是 Guest 用户态固定 wire envelope，通过内核 `MESSAGE` 发送。它与 Task Channel SQ/CQ 无关。Nexus artifact 由 Guest store 管理，包含 actor、manifest、payload digest、manifest digest、lifecycle generation 和权限位。

对应结构见 [`user/include/agent_nexus_protocol.h`](../../user/include/agent_nexus_protocol.h)。该协议服务仓库内 Nexus 产品，不是内核公共 TASK 类型，也不是 MCP/A2A 网络协议。

## 验证

```bash
make agent-uapi-check
make agent-module-check
AGENT_TEST_CASE=agenttoolabi_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

静态检查验证尺寸、offset、syscall 号和模块边界；QEMU 场景验证真实 copyin/copyout、错误码、generation、重放、取消和回收。测试层次与结果解释见[验证说明](verification.md)。
