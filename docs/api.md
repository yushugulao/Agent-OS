# AgentOS 系统调用与 ABI

AgentOS 通过 uCore 系统调用向 Guest 提供身份、workflow、结构化工具、Context、Live Query、事件、调度、资源和 Task Channel。公开声明集中在 [`user/include/agent.h`](../user/include/agent.h)，内核与用户态共用的冻结结构位于 `include/agent_*_abi.h`，用户态封装位于 [`user/lib/syscall.c`](../user/lib/syscall.c)。

## 文档索引

- [调用链与返回值](#调用链与返回值)
- [ABI 约定](#abi-约定)
- [系统调用索引](#系统调用索引)
- [身份与 Workflow](#身份与-workflow)
- [工具调用与执行合同](#工具调用与执行合同)
- [Batch 与 Workflow fence](#batch-与-workflow-fence)
- [Context Path](#context-path)
- [Live Query 与文件编辑](#live-query-与文件编辑)
- [事件、调度与观测](#事件调度与观测)
- [Task Channel](#task-channel)
- [状态处理](#状态处理)

## 调用链与返回值

用户库封装不解释内核对象，只把 ABI 缓冲区交给 RISC-V `ecall`。[`os/syscall.c`](../os/syscall.c) 根据 syscall id 分派到模块 owner，owner 完成 copyin、授权、执行和 copyout。

```c
/* user/lib/syscall.c */
int tool_call_v3(struct agent_request_v3 *req,
                 struct agent_response_v3 *resp)
{
    return syscall(SYS_tool_call, req, resp);
}

/* os/syscall.c */
case SYS_tool_call:
    ret = sys_tool_call(trapframe->a0, trapframe->a1);
    break;
```

`sys_tool_call()` 先 copyin request 的 `version + size`，再分派 V2/V3 解码器。通过 schema 校验的输入被复制为内核私有 `agent_op`，调用方随后修改原数组不会改变本次执行。

V2 的顺序为 request header/identity、tool id/name、syscall-only 属性、参数指针范围、typed-KV 解码、lifecycle operation gate、`agent_execute_one()`。V3 先 copyin 完整 200 字节 request 并核对 lifecycle binding，再完成 tool resolve、参数范围与同一 V2 decoder，随后进入 operation gate；Execution Contract、provenance、phase credit 和 effect gate 都在 `agent_execute_one()` 中执行。ENFORCE lifecycle 的 V3 准入拒绝写入结构化 `DENIED/STALE` 结果；安全终态槽位不足时返回 `NO_SPACE`，其它暂时无法提交终态记录的情况返回 `RETRY`。

不同接口的返回通道需要分别读取：

| 接口类型 | syscall 返回值 | 结构化结果 |
| --- | --- | --- |
| `tool_call()` / `tool_call_v3()` | ABI copyin/copyout 是否成功 | `response.status`、`sequence`、result 与 V3 decision 字段 |
| `agent_execution_contract()` | control/result 传输是否成功 | `result.status`、合同 state 与 generation |
| `agent_task_channel_*()` | request/result 传输是否成功 | `result.status`、ring flags 与权威水位 |
| `agent_file_query()` | 返回 hit 数或负值 | `agent_file_query_result` 的 plan、generation 与 hits |
| Context/观测 snapshot | 返回记录数或接口状态 | 调用方提供的 header、record 或 snapshot |

因此，`ecall` 成功只表示内核已经处理并写回 response；工具被拒绝、超时或冲突仍由 response 中的稳定状态表达。

## ABI 约定

1. 版本化结构先填写 `version`，再按定义填写 `size` 或 `struct_size`；所有 reserved 字段置零。
2. 指针、数组和字符串由内核重新 copyin。固定字符串必须在字段容量内以 NUL 结束。
3. Workflow 对象使用完整 `{id, generation}`；只保存数值 id 无法识别槽位复用。
4. Task 对象同时携带 channel/ring generation、slot generation 和 typed handle generation；SQE 的 `ring_generation` 必须等于 authoritative channel generation。
5. Task command 的 request id 严格递增。Workflow fence 允许同一 request id 的精确重放；普通工具 request id 按对应接口承担相关性或 replay identity。
6. 读取 Context 映射时，以 publication sequence 检查一致性；rollback 和 query 继续核对 branch 与 generation。

工具状态定义在 [`include/agent_tool_abi.h`](../include/agent_tool_abi.h)，包括 `OK`、`BAD_REQUEST`、`UNKNOWN_TOOL`、`NO_SPACE`、`TIMEOUT`、`DENIED`、`DUPLICATE`、`CANCELLED`、`CONFLICT`、`STALE`、`RETRY`、`DURABILITY` 与 `INDETERMINATE`。Task Channel 另有独立的协议状态，工具执行结果仍使用 `AGENT_STATUS_*` 写入 CQE。

ABI checker 使用 RISC-V64 probe、`_Static_assert` 与冻结清单核对结构大小、字段 offset、枚举值，以及纳入清单的 syscall 号：

```bash
make agent-uapi-check
```

冻结清单位于 [`ci/agent-uapi-layout.json`](../ci/agent-uapi-layout.json)，探针位于 [`scripts/probes/agent-uapi-layout.c`](../scripts/probes/agent-uapi-layout.c)。

## 系统调用索引

系统调用号定义在 [`os/syscall_ids.h`](../os/syscall_ids.h)，用户态镜像位于 [`user/lib/syscall_ids.h`](../user/lib/syscall_ids.h)。

| 分组 | Syscall | 用户态接口 |
| --- | --- | --- |
| 身份 | 500、501、517、561 | `agent_create()`、`agent_info()`、`agent_create_role()`、`agent_launch_info()` |
| Workflow | 539、541、542、545、546 | `agent_worker_create()`、`agent_workflow_create()`、`agent_scope_delegate_fd()`、`agent_workflow_close()`、`agent_workflow_lifecycle_info()` |
| 工具 | 502、503、504、547、548 | `agent_run()`、`agent_call()`、`agent_tool_list()`、`tool_call()`、`tool_call_v3()`、`tool_list()` |
| Context | 505-509、519 | `context_push/query/snapshot/detail/rollback/clear()` |
| 文件状态 | 514-516、535-538 | `agent_file_meta_*()`、`agent_file_query()`、`agent_file_edit_*()` |
| 事件与 IPC | 510-513、518、520、540、552、553 | `agent_watch()`、`agent_live_watch/unwatch()`、`agent_wait()`、`agent_wait_cancel()`、`agent_wake()`、`agent_route_config()`、heartbeat |
| 调度与观测 | 521-525、528-534、557、559、560 | sched、trace、audit、timeline、provenance、ledger、resource、performance |
| 执行合同 | 562 | `agent_execution_contract()` |
| Task Channel | 563-565 | `agent_task_channel_setup/enter/resource()` |

`tool_call_v3()` 与 V2 共用 `SYS_tool_call`，内核依据 request version 选择布局。`agent_workflow_fence()` 复用 `SYS_agent_run`，其调用满足 `count == 0` 且 `flags == AGENT_RUN_F_FENCE`。

## 身份与 Workflow

```c
int agent_create(void);
int agent_create_role(int role);
int agent_workflow_create(int role);
int agent_worker_create(const char *image, uint64 capabilities);
int agent_scope_delegate_fd(int fd);
int agent_workflow_close(uint64 scope_id);
int agent_info(struct agent_info *info);
int agent_workflow_lifecycle_info(
    struct agent_workflow_lifecycle_info *info,
    const struct agent_workflow_lifecycle_key *expected);
```

`agent_workflow_create()` 创建 controller、lifecycle root、VFS scope 和资源账户，返回 controller pid。同一 workflow 中具备 `AGENT_CAP_ORCHESTRATE` 的 Agent 可通过 `agent_worker_create()` 创建带 pending image identity 的 worker。请求 capability 必须非零，只能取 `AGENT_CAP_CONTENT_READ | AGENT_CAP_ARTIFACT_WRITE` 的子集，并同时属于调用者已有集合；子进程随后执行 `exec()`，内核再核对 capability ceiling、workflow scope 与 executable identity。

`agent_workflow_lifecycle_info()` 返回完整 lifecycle key、Context lane、metadata transaction、resource account identity 和 workflow EEVDF 快照。调用方可传入 expected key 进行 compare-and-read，避免跨 generation 读取。

`agent_scope_delegate_fd()` 为当前线程设置一次性 FD 委派 ticket；它只接受标为 `FD_INHERIT_DELEGATE` 的描述符（当前为 pipe）。下一次受控主体创建会消费该 snapshot，在子进程 `filedup` 该描述符，父进程继续持有原 FD。普通 inode 与 stdio 返回 `BAD_PARAM`。`agent_workflow_close()` 使 lifecycle 转入 closing；成员、active operation、Task Channel、执行资源账户和后台 delta 结算后，generation slot 才能回收。标记 `preserve_on_retire` 的持久输出 scope 会保留 registry、文件与 storage charge，storage account 可在 `DRAINING` 状态跨过 lifecycle 回收继续存在。

## 工具调用与执行合同

### V1 与 V2

```c
int agent_call(struct agent_request *req,
               struct agent_response *resp);
int agent_tool_list(struct agent_tool_desc *out, int max);

int tool_call(struct agent_request_v2 *req,
              struct agent_response_v2 *resp);
int tool_list(struct agent_tool_desc_v2 *out, int max);
```

V1 使用固定 request；V2 通过 typed-KV 数组表达参数。单次 V2 request 最多包含 8 个 `agent_param_v2`，每项独立声明 key、type 与 value size。工具目录定义 25 个 name/id 唯一的工具以及 required capability、来源策略和 side-effect mask，详见[结构化工具与执行合同](modules/tool-execution.md#工具目录与-typed-abi)。

### V3 与合同控制

```c
int tool_call_v3(struct agent_request_v3 *req,
                 struct agent_response_v3 *resp);

int agent_execution_contract(
    const struct agent_execution_contract_control *control,
    struct agent_execution_contract_result *result);
```

Contract control 支持 `CREATE`、`QUERY` 和 `RETIRE`。每个 lifecycle 最多冻结 24 个拓扑有序节点，单节点最多 4 个 attempt，合同整体保留 48 个 accepted attempt 终态。`predecessor_mask` 只能引用编号更小的节点。

V3 request 保留完整 V2 前缀，并追加 contract key、node id、attempt、schema digest、input fingerprint、source Context sequence、source node、producer control id/pid 和 artifact 类型。response 增加 decision reason、Evidence ticket、output provenance、artifact type 与 completion flags；`AGENT_RESPONSE_V3_F_CACHED` 表示命中完成缓存。

## Batch 与 Workflow fence

```c
int agent_run(struct agent_op *ops,
              struct agent_result *results,
              int count, uint64 flags);

int agent_workflow_fence(
    const struct agent_workflow_fence_request *request,
    struct agent_workflow_fence_receipt *receipt);
```

Compact Batch 一次提交最多 64 项，按数组顺序执行，每项返回独立 `agent_result`。Batch 与 Scalar 共享 `agent_execute_one()` 和 Context commit lane。

Fence request 为 56 字节，包含 flags、32 字节 challenge 和 request id；receipt 固定为 320 字节，记录 lifecycle key、fence sequence、metadata generation、credit epoch、执行记录范围、八类资源用量、`previous_root` 及摘要。处理中返回 `RETRY`，同一 request 的精确重放返回同一 receipt。结构定义见 [`include/agent_workflow_fence_abi.h`](../include/agent_workflow_fence_abi.h)。

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

每个 Agent 映射 7 页 Context：6 个内核只读页保存 header、latest response 与最多 128 条 record，第 7 页供 Guest cache 使用。record 包含 sequence、request id、cause/span、branch parent、tool/status、payload/result 与 hash 链；`context_detail()` 返回规范化 `agent_op` 与 `agent_result`，不保存完整的 V2 参数、V3 binding 或 Task SQE。

直接读取 helper 先后检查 publication sequence，避免读取并发发布中的半个快照。`context_rollback()` 移动 active branch head，不改写历史 sequence 和 record hash；目标离开保留窗口时返回 `NOT_FOUND`，path summary 或 generation 不一致时返回 `STALE`。

## Live Query 与文件编辑

```c
int agent_file_meta_init(void);
int agent_file_meta_set(struct agent_file_meta *meta);
int agent_file_query(struct agent_file_query *query,
                     struct agent_file_query_result *result);

int agent_live_watch(struct agent_file_live_watch *watch);
int agent_live_unwatch(struct agent_file_live_watch *watch);

int agent_file_edit_begin(const char *path, uint64 flags, int ttl_ticks,
                          struct agent_file_edit_state *state);
int agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
                           struct agent_file_edit_state *state);
int agent_file_edit_abort(uint64 lease_id);
int agent_file_edit_state(const char *path,
                          struct agent_file_edit_state *state);
```

Catalog 只接收显式登记的 metadata，容量为 512；普通 upsert 使用零 flags，`AGENT_FILE_META_F_DELETE` 删除登记，`PERSIST`、`AUTOSCAN` 和未知位会被拒绝。查询最多返回 8 个 hit；`USE_INDEX` 允许 planner 选择 status、stage 或 kind 索引，`SCAN` 强制 traversal。result 返回命中/截断、plan/reason、扫描量、候选量、query ticks 和 `fs_generation`。

每个文件对象使用 `{dev, inum, incarnation}`。inode 复用时 incarnation 递增，旧 metadata、digest、edit lease 和 deferred unlink 无法命中新对象。Typed watch 保存完整 query，集合变化发布 `ENTER`、`UPDATE` 或 `LEAVE`。出现增量缺口后，Agent 保留旧 watch，先安装同一 query 的替代 watch，再取得未截断的有界 snapshot，最后在旧 watch 上 ACK 并 unwatch。单次 query 最多返回 8 个 hit；`truncated != 0` 时不能建立完整基线，也不能确认该 resync。完整过程见 [Live Query](modules/live-query.md)。

编辑租约保存 owner、scope、inode identity、lease id、base version 和 TTL。commit 的 `expected_version` 检测并发修改。

## 事件、调度与观测

```c
int agent_wait(struct agent_event *event, int timeout_ticks);
int agent_wait_cancel(int pid, const char *reason);
int agent_wake(int pid, struct agent_event *event);
int agent_route_config(int source_pid, int target_pid,
                       uint64 event_mask, int operation);

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

事件队列容量为 16，并为内核事件和来源类别保留槽位。跨 Agent `MESSAGE` 与 `LLM_DONE` 需要显式 route、相同 active workflow 和匹配 identity；`agent_wake()` 只接受允许由用户态注入的事件类型。

`agent_sched_config()` 配置 workflow 内 per-Agent weight、priority、budget 和 policy；`agent_sched_snapshot()` 返回调用 Agent 的 dispatch、ready age、vruntime、budget 与 reason trace。外层 workflow EEVDF 的 request、vruntime、virtual deadline、sleep decay 与 service cycles 由 lifecycle info 返回。Timeline filter 可按 source、tick、span、pid、role、tool、event、status 和 cursor 组合查询。

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

Channel 使用 single issuer，SQ/CQ 容量均为 16，ring header、SQE 和 CQE 都是 128 字节。`setup` 必须由进程主线程调用并建立映射；后续 `enter` 与 `resource` 绑定同一 issuer tid 和 identity generation。`enter` 提交 SQ tail、确认 CQ head 并推进完成；`resource` 提供 typed handle 的 import/release/query 控制面。当前同步 bridge 处理 null input/output，非空资源 import 的 `result.status` 为 `AGENT_TASK_CHANNEL_DENIED`。

SQE 支持 submit、cancel、link 和 hard deadline。每个 accepted target 只产生一个 terminal CQE；cancel command 拥有独立 request id，并通过 `link_request_id` 引用目标。取消策略同步拒绝时，cancel id 已被消费，`enter` 返回 `DENIED`，不产生 cancel CQE，目标继续运行。CQE flags 表达 target 的 cancelled、deadline、denied 与 link failed 终态。

Task Channel 有两类不同恢复语义：

| 结果 | 含义 | 恢复动作 |
| --- | --- | --- |
| `AGENT_TASK_CHANNEL_STALE` | control generation 不匹配 | 读取结果中的当前 generation 与水位后重建请求；不自动进入 resync |
| `AGENT_TASK_CHANNEL_STALE` | issuer identity/lifecycle 已失效 | 结果除 ABI version/size/status 外为零；重新建立有效 owner/lifecycle |
| `AGENT_TASK_CHANNEL_STALE` | cancel 目标已被 CQ ack 或不存在 | cancel id 已消费，结果返回当前 channel generation 与水位；结束对已消失目标的控制 |
| `AGENT_TASK_CHANNEL_RESYNC_REQUIRED` | SQE、水位、request id、ring/slot generation 或 link 违反协议 | 采用返回的新 generation，丢弃未接受 SQE，以 `ENTER_F_RESYNC` 和 `sq_tail=0` 清除 sticky resync |

布局和状态常量见 [`include/agent_task_channel_abi.h`](../include/agent_task_channel_abi.h)，完整协议见[结构化工具与执行合同](modules/tool-execution.md#task-sqcq-协议)。

## 状态处理

| 状态 | 调用方处理 |
| --- | --- |
| `BAD_VERSION` / `BAD_SIZE` / `BAD_PARAM` | 修正版本、布局、flags、reserved 字段和 typed 参数 |
| `UNKNOWN_TOOL` / `UNKNOWN_PARAM` | 重新读取工具目录并修正 name/id、key 或参数集合 |
| `DUPLICATE` | 按具体接口读取既有 pending/terminal 状态；不要重复创建同一请求 |
| `DENIED` | 检查 role、capability、scope、来源标签和合同节点 |
| `NO_SPACE` | 消费 CQ、释放对象或等待资源结算后再提交 |
| `STALE` / `CONFLICT` / `NOT_FOUND` | 重新读取 lifecycle、generation、对象 identity 或 edit version |
| `RESYNC_REQUIRED` | Live Query 以 replacement watch 和未截断 snapshot 重建基线；Task ring 执行显式 resync |
| `RETRY` | 等待 owner 状态推进；按接口规则重放，Task 新 command 使用更大的 request id |
| 工具/Task 的 `TIMEOUT` / `CANCELLED` | 读取 response/CQE 的 terminal status、decision reason 与 Context sequence |
| `DURABILITY` / `INDETERMINATE` | 读取最终对象状态和记录后决定补偿或停止重放 |

`agent_wait_cancel()` 的语义独立于工具/Task target：它向目标当前或下一次 `agent_wait()` 安装 `AGENT_EVENT_CANCELLED` 事件，目标从 `agent_event` 读取取消或超时结果后自行推进状态；`DUPLICATE` 表示已有 pending cancel。

模块关系见[产品架构](architecture.md)，授权与提交次序见[安全机制](security.md)。
