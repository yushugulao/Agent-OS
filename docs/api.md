# AgentOS 系统调用与 ABI

AgentOS 在 uCore 上增加了一组面向 Agent（智能体）程序的系统调用。Agent 进程创建与地址空间、工具调用协议、上下文路径管理、Live Query、事件、调度、资源和 Task Channel 都通过版本化 ABI 进入内核。公开接口集中在 [`user/include/agent.h`](../user/include/agent.h)，内核与用户态共用的结构位于 `include/agent_*_abi.h`，用户态封装位于 [`user/lib/syscall.c`](../user/lib/syscall.c)。

## 文档索引

- [调用链与返回值](#调用链与返回值)
- [ABI 约定](#abi-约定)
- [系统调用索引](#系统调用索引)
- [身份与工作流](#身份与工作流)
- [Agent-OS 内核结构化交互接口](#agent-os-内核结构化交互接口)
- [批量调用与 Workflow Fence](#批量调用与-workflow-fence)
- [上下文路径管理](#上下文路径管理)
- [面向 Agent 查询优化的文件系统接口](#面向-agent-查询优化的文件系统接口)
- [Agent Loop、调度与运行记录](#agent-loop调度与运行记录)
- [Task Channel](#task-channel)
- [状态处理](#状态处理)

## 调用链与返回值

用户库只负责把 ABI 缓冲区交给 RISC-V `ecall`，缓冲区中的对象由内核读取和处理。[`os/syscall.c`](../os/syscall.c) 按系统调用号找到相应模块，再完成数据复制、权限检查、执行和结果回写。

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

`sys_tool_call()` 先从用户空间复制 `version` 和 `size`，再选择 V2 或 V3 解码程序。schema 检查通过后，请求会被转换为内核私有的 `agent_op`。此后即使调用方改动原参数数组，也不会影响已经进入内核的请求。

V2 的检查顺序固定为：请求头和身份、工具编号和名称、仅供系统调用使用的属性、参数指针范围、带类型信息的键值参数、生命周期操作检查，最后进入 `agent_execute_one()`。V3 会先复制完整的 200 字节请求并核对生命周期绑定，再解析工具、检查参数范围，并调用同一套 V2 schema 解码程序；随后完成生命周期操作检查并进入 `agent_execute_one()`。Execution Contract、provenance（来源追溯）、Phase Lease 和副作用检查都在 `agent_execute_one()` 内完成。工作流启用 `ENFORCE` 后，V3 请求若未通过检查，会得到结构化的 `DENIED` 或 `STALE` 结果。用于记录 terminal record 的安全槽位不足时返回 `NO_SPACE`；槽位暂时不能 commit 时返回 `RETRY`。

不同接口写回结果的位置并不相同，调用后应按下表读取：

| 接口类型 | 系统调用返回值 | 具体结果 |
| --- | --- | --- |
| `tool_call()`、`tool_call_v3()` | ABI 数据能否正常复制 | `response.status`、`sequence`、工具结果和 V3 的决定原因（`decision_reason`）等字段 |
| `agent_execution_contract()` | 请求和结果能否正常传输 | `result.status`、Execution Contract 状态和 generation |
| `agent_task_channel_*()` | 请求和结果能否正常传输 | `result.status`、ring 标志和内核记录的水位 |
| `agent_file_query()` | 命中条数或负值 | `agent_file_query_result` 中的查询方式、更新序号和命中项 |
| Context 或 Runtime 快照 | 记录条数或接口状态 | 调用方提供的表头、记录或快照 |

因此，`ecall` 返回成功只说明内核已经处理请求并写回响应。工具是否被拒绝、是否超时、是否发生冲突，还要查看响应结构中的状态。

## ABI 约定

1. 带版本号的结构先填写 `version`，再按定义填写 `size` 或 `struct_size`；所有保留字段都应置零。
2. 指针、数组和字符串会由内核重新复制。固定长度字符串必须在字段容量内以 NUL 结尾。
3. 工作流对象必须使用完整的 `{id, generation}`。只保存数值编号，无法识别槽位是否已经复用。
4. Task Channel 对象同时带有通道 generation、`slot_generation` 和类型化句柄 generation。SQE 中的 `ring_generation` 必须等于当前通道 generation。
5. 任务命令的 `request_id` 必须严格递增。Workflow Fence 支持相同 `request_id` 的 Replay；普通工具请求中的 `request_id` 则按各接口规定用于关联请求或识别重放。
6. 直接读取 Context 映射时，要用发布序号检查前后是否一致。回退和查询还要继续核对分支与 generation。

工具状态定义在 [`include/agent_tool_abi.h`](../include/agent_tool_abi.h)，包括 `OK`、`BAD_REQUEST`、`UNKNOWN_TOOL`、`NO_SPACE`、`TIMEOUT`、`DENIED`、`DUPLICATE`、`CANCELLED`、`CONFLICT`、`STALE`、`RETRY`、`DURABILITY` 和 `INDETERMINATE`。Task Channel 另有一组协议状态；工具执行结果仍以 `AGENT_STATUS_*` 写入 CQE。

ABI 检查程序使用 RISC-V64 探针、`_Static_assert` 和冻结清单，核对结构大小、字段偏移、枚举值以及清单中的系统调用号：

```bash
make agent-uapi-check
```

冻结清单位于 [`ci/agent-uapi-layout.json`](../ci/agent-uapi-layout.json)，探针位于 [`scripts/probes/agent-uapi-layout.c`](../scripts/probes/agent-uapi-layout.c)。当前检查覆盖 560 项结构大小、字段偏移、枚举值和系统调用号合约，其中包含系统调用 566 及 64 字节的结果文件发布请求。

## 系统调用索引

系统调用号定义在 [`os/syscall_ids.h`](../os/syscall_ids.h)，用户态对应表位于 [`user/lib/syscall_ids.h`](../user/lib/syscall_ids.h)。

| 分组 | 系统调用号 | 用户态接口 |
| --- | --- | --- |
| Identity | 500、501、517、561 | `agent_create()`、`agent_info()`、`agent_create_role()`、`agent_launch_info()` |
| Workflow | 539、541、542、545、546 | `agent_worker_create()`、`agent_workflow_create()`、`agent_scope_delegate_fd()`、`agent_workflow_close()`、`agent_workflow_lifecycle_info()` |
| 工具 | 502、503、504、547、548 | `agent_run()`、`agent_call()`、`agent_tool_list()`、`tool_call()`、`tool_call_v3()`、`tool_list()` |
| Context | 505 至 509、519 | `context_push/query/snapshot/detail/rollback/clear()` |
| 文件状态与结果发布 | 514 至 516、535 至 538、566 | `agent_file_meta_*()`、`agent_file_query()`、`agent_file_edit_*()`、`agent_file_publish()` |
| 事件与进程通信 | 510 至 513、518、520、540、552、553 | `agent_watch()`、`agent_live_watch/unwatch()`、`agent_wait()`、`agent_wait_cancel()`、`agent_wake()`、`agent_route_config()`、心跳接口 |
| 调度与运行记录 | 521 至 525、528 至 534、557、559、560 | 调度、跟踪、审计、时间线、来源、计费、资源和性能接口 |
| Execution Contract | 562 | `agent_execution_contract()` |
| Task Channel | 563 至 565 | `agent_task_channel_setup/enter/resource()` |

`tool_call_v3()` 与 V2 共用 `SYS_tool_call`，内核依据请求中的版本号选择布局。`agent_workflow_fence()` 复用 `SYS_agent_run`，调用时要求 `count == 0`，并设置 `flags == AGENT_RUN_F_FENCE`。

<a id="身份与-workflow"></a>
## 身份与工作流

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

`agent_workflow_create()` 创建工作流的管理 Agent、生命周期根、文件访问范围和资源账户，返回管理 Agent 的进程号。同一工作流内，拥有 `AGENT_CAP_ORCHESTRATE` 的 Agent 可以调用 `agent_worker_create()` 创建工作 Agent。申请的能力集合必须非零，只能来自 `AGENT_CAP_CONTENT_READ` 和 `AGENT_CAP_ARTIFACT_WRITE`，同时不能超出调用方已经拥有的能力。子进程执行 `exec()` 时，内核还会核对能力上限、所属工作流和可执行文件身份。

`agent_workflow_lifecycle_info()` 返回完整的生命周期键、Context 通道、元数据事务、资源账户身份和工作流 EEVDF 快照。调用方可以传入预期键，按“比较后读取”的方式避免误读其他 generation 的数据。

`agent_scope_delegate_fd()` 为当前线程登记一张一次性的文件描述符委派票据。它只接受带有 `FD_INHERIT_DELEGATE` 标记的描述符，目前仅支持管道。下一次创建受控进程时，内核会取走这张票据，在子进程中执行 `filedup`，父进程仍保留原描述符。普通 inode 和标准输入输出描述符返回 `BAD_PARAM`。

`agent_workflow_close()` 会把生命周期改为关闭中。成员进程、已经开始的操作、Task Channel、执行资源账户和后台增量全部处理完后，生命周期槽位才会回收。若 artifact 的文件访问范围带有 `preserve_on_retire`，生命周期槽位可以先回收，相关登记项、文件和存储计费仍会保留；对应的存储账户即使已经进入 `DRAINING` 或 `FREE`，这些数据也可继续存在，直至后续处理完成。

<a id="工具调用与执行约定"></a>
## Agent-OS 内核结构化交互接口

### V1 与 V2

```c
int agent_call(struct agent_request *req,
               struct agent_response *resp);
int agent_tool_list(struct agent_tool_desc *out, int max);

int tool_call(struct agent_request_v2 *req,
              struct agent_response_v2 *resp);
int tool_list(struct agent_tool_desc_v2 *out, int max);
```

这组接口实现工具调用协议：请求以工具名称/编号和 typed 键值参数表示，响应给出状态码、序号和结构化结果。V1 使用固定布局的请求。V2 用一组带类型信息的键值参数表示调用内容。一次 V2 请求最多包含 8 个 `agent_param_v2`，每项分别填写键名、类型和值的长度。Tool Registry 登记了 25 个名称和编号均不重复的工具，同时注明所需能力、允许的 provenance 和副作用类型。详见[Agent-OS 内核结构化交互接口与工具调用协议](modules/tool-execution.md#tool-registry-与-typed-schema)。

### V3 与 Execution Contract 管理

```c
int tool_call_v3(struct agent_request_v3 *req,
                 struct agent_response_v3 *resp);

int agent_execution_contract(
    const struct agent_execution_contract_control *control,
    struct agent_execution_contract_result *result);
```

Execution Contract 可以 `CREATE`、`QUERY` 或 `RETIRE`。每个生命周期最多登记 24 个按依赖顺序排列的节点，每个节点最多尝试 4 次，一份 Execution Contract 最多保留 48 条已经接受的执行结果。`predecessor_mask` 只能引用编号更小的节点。

V3 请求保留完整的 V2 前缀，后面增加 Execution Contract 键、节点号、尝试次数、schema digest、输入指纹、来源 Context 序号、来源节点、生产者的控制编号和进程号，以及 artifact 类型。响应增加决定原因、Evidence Ring 票号、输出 provenance、结果类型和完成标志。设置 `AGENT_RESPONSE_V3_F_CACHED` 表示本次调用直接取用了已经完成的结果。

<a id="batch-与-workflow-fence"></a>
## 批量调用与 Workflow Fence

```c
int agent_run(struct agent_op *ops,
              struct agent_result *results,
              int count, uint64 flags);

int agent_workflow_fence(
    const struct agent_workflow_fence_request *request,
    struct agent_workflow_fence_receipt *receipt);
```

`agent_run()` 一次最多提交 64 个操作，内核按数组顺序执行，每项分别写回 `agent_result`。批量调用和单次调用共用 `agent_execute_one()`，也共用 Context commit 顺序。

Workflow Fence 请求占 56 字节，包含标志、32 字节 `challenge` 和请求号；Workflow Fence 回执固定为 320 字节，记录生命周期键、屏障序号、元数据 generation、计费轮次、Evidence Ring 范围、八类资源用量、`previous_root` 和摘要。请求尚在处理时返回 `RETRY`；使用同一 `request_id` 进行 Replay 时返回同一份回执。结构定义见 [`include/agent_workflow_fence_abi.h`](../include/agent_workflow_fence_abi.h)。

<a id="context-path"></a>
## 上下文路径管理

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

每个 Agent 映射 7 页 Context。前 6 页由内核只读映射，保存表头、最近一次响应和最多 128 条路径记录；第 7 页留给 Guest 程序缓存查询结果等派生数据。记录中包含执行序号、请求号、原因和跨度、父分支、工具和状态、输入输出以及哈希链。`context_detail()` 只返回规范化后的 `agent_op` 和 `agent_result`，不会保存完整的 V2 参数、V3 绑定或任务 SQE。

直接读取映射时，辅助函数会在复制前后检查发布序号，避免拿到并发写入中的半份记录。`context_rollback()` 只移动当前分支的指针，不改写原有序号和记录哈希。目标已经离开保留窗口时返回 `NOT_FOUND`；路径摘要或 generation 不符时返回 `STALE`。

<a id="live-query-与文件编辑"></a>
## 面向 Agent 查询优化的文件系统接口

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
int agent_file_publish(const char *path, const void *header,
                       unsigned int header_size, const void *payload,
                       unsigned int payload_size);
```

Metadata Catalog 只记录经过 `agent_file_meta_set()` 显式登记的元数据，最多保存 512 项。普通登记要求 `flags` 为零，`AGENT_FILE_META_F_DELETE` 用于删除登记；`PERSIST`、`AUTOSCAN` 和未知标志都会被拒绝。一次查询最多返回 8 项。设置 `USE_INDEX` 时，内核可以按状态、阶段或类型使用索引；设置 `SCAN` 时则逐项扫描。返回结构给出完整命中数、是否截断、实际查询方式、扫描量、候选量、查询耗时和 `fs_generation`。

文件身份由 `{dev, inum, incarnation}` 三部分组成。inode 被重新分配时，`incarnation` 随之增加，因此旧元数据、摘要、编辑租约和待处理的删除记录都不会误认新文件。Typed Watch 保存完整查询条件，文件集合发生变化时发布 `ENTER`、`UPDATE` 或 `LEAVE`。

增量事件出现 generation 缺口后，Agent 先保留旧订阅，再用相同条件建立替代订阅。随后执行一次未被截断的查询，取得完整基线；最后在旧订阅上确认缺口并将其移除。一次查询只有 8 个返回位置，也没有分页游标。若 `truncated != 0`，这次查询不能用作完整基线，也不能确认本轮重新同步。具体步骤见 [Live Query](modules/live-query.md)。

编辑租约记录所有者、文件访问范围、inode 身份、租约号、基础版本和有效期。commit 时，`expected_version` 用来发现并发修改。

`agent_file_publish()` 对应系统调用 566。64 字节请求结构带有 `version`、`size`、正式路径、header/payload 指针及两段长度，保留字段和 `flags` 必须为零，两段内容合计不能超过 4,096 字节。正式路径只能是 1–14 字节的直接 basename。调用者必须是具备 artifact 写能力、处于活动文件访问范围内的 Agent。内核先复制完整字节并写入未命名 inode，数据和 inode checkpoint 完成后再接入正式文件名，并用第二次 attach-only checkpoint 固定目录项；同名文件已存在时返回 `DUPLICATE`，不会覆盖。若目录接入结果无法确定，则返回 `INDETERMINATE`。Nexus 只在正式路径与本次 header、payload 逐字节一致且紧接 EOF 时，把这两种状态收敛为幂等成功；内容不同便保留失败结果。

<a id="事件调度与观测"></a>
## Agent Loop、调度与运行记录

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

事件队列有 16 个位置，其中一部分预留给内核事件和特定来源。Agent 之间发送 `MESSAGE` 或 `LLM_DONE`，必须事先配置路由，并且双方处于同一活动工作流、身份匹配。`agent_wake()` 只允许注入 ABI 明确开放给用户态的事件。

`agent_sched_config()` 设置工作流内各 Agent 的权重、优先级、预算和调度策略。`agent_sched_snapshot()` 返回当前 Agent 的派发次数、就绪等待时间、虚拟运行时间、预算和调度原因。工作流一级的 EEVDF 快照由生命周期信息接口返回，其中包含 `request`、`vruntime`、`virtual_deadline`、`sleep_decay` 和 `service_cycles`。时间线可以按 provenance、tick、调用跨度、进程号、角色、工具、事件、状态和游标组合筛选。

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

每条 Task Channel 只允许一个提交者。SQ 和 CQ 各有 16 个位置，队列表头、SQE 和 CQE 均为 128 字节。`setup` 只能由进程主线程调用，调用后建立共享映射。后续的 `enter` 和 `resource` 都绑定同一个提交者线程及其身份 generation。`enter` 提交新的 SQ 队尾、确认已经读取的 CQ 队头，并推动内核处理任务；`resource` 用于导入、释放和查询类型化句柄。

`IMPORT` 接受当前进程打开的可读普通文件描述符，类型固定为 `AGENT_ARTIFACT_UTF8`，长度必须准确落在 1–63 字节，创建的句柄只能是 OWNED。调用前，当前 Agent 必须已有一条经过校验的最新 Context；导入只绑定它的 sequence，不新建记录。内核从 offset 0 读取并多探测一个字节确认 EOF，不改变共享文件 offset；内容经过 NUL 与 UTF-8 检查后，以不可变快照保存在 Task Channel 私有页中，同时记录 SHA-256、生产者 PID/`control_id` 和 `UNTRUSTED_FILE_DATA` provenance。导入成功后的最终 copyout 若失败，刚建立的资源会在用户态可见前回滚。

SQE 可以把已有 OWNED 句柄改作 BORROWED 别名提交。当前 ECHO Task Bridge 会把快照复制到工具 payload：BORROWED 任务完成后资源仍处于 `LIVE`，调用方随后显式 `RELEASE`；OWNED 任务完成后输入自动消费。释放后的句柄以及槽位复用前的旧 generation 都返回 `STALE`。

SQE 支持提交、取消、关联和强制截止时间。每个已经接受的目标任务只产生一条完成 CQE。取消命令使用自己的 `request_id`，并通过 `link_request_id` 指向目标。若取消策略当场拒绝该命令，取消命令的编号仍会被记为已使用；`enter` 返回 `DENIED`，不会产生取消 CQE，原目标继续执行。CQE 标志可以表示目标被取消、超过截止时间、被拒绝或关联失败。

`AGENT_TASK_CHANNEL_STALE` 和 `AGENT_TASK_CHANNEL_RESYNC_REQUIRED` 的处理方法不同：

| 结果 | 原因 | 处理方法 |
| --- | --- | --- |
| `AGENT_TASK_CHANNEL_STALE` | 控制请求中的通道 generation 不符 | 读取返回的当前 generation 和水位，重新构造请求；通道不会因此进入重新同步状态 |
| `AGENT_TASK_CHANNEL_STALE` | 提交者身份、所有者或工作流生命周期已经失效 | 返回结构中只有 ABI 版本、大小和状态有效，其余字段为零；重建 Task Channel，并使用有效的提交者和工作流 |
| `AGENT_TASK_CHANNEL_STALE` | 取消目标已经被确认读取，或该目标不存在 | 取消命令的编号已经用掉，结果带回当前 generation 和水位；不再控制这个已经消失的目标 |
| `AGENT_TASK_CHANNEL_RESYNC_REQUIRED` | SQE、水位、请求号、通道 generation、`slot_generation` 或关联关系违反协议 | 采用结果中的新 generation，丢弃尚未接受的 SQE，再以 `ENTER_F_RESYNC` 和 `sq_tail=0` 清除持续重新同步标记 |

布局和状态常量见 [`include/agent_task_channel_abi.h`](../include/agent_task_channel_abi.h)，完整协议见[结构化交互与工具调用协议](modules/tool-execution.md#task-sqcq-协议)。

## 状态处理

| 状态 | 调用方处理 |
| --- | --- |
| `BAD_VERSION`、`BAD_SIZE`、`BAD_PARAM` | 修正版本、布局、标志、保留字段和带类型信息的参数 |
| `UNKNOWN_TOOL`、`UNKNOWN_PARAM` | 重新读取 Tool Registry，修正名称、编号、键名或参数集合 |
| `DUPLICATE` | 按接口读取已经存在的处理中状态或最终结果，不要再次创建同一请求 |
| `DENIED` | 检查角色、能力、文件访问范围、provenance 和 Execution Contract 节点 |
| `NO_SPACE` | 读取并确认完成队列、释放对象，或等待资源结算后再提交 |
| `STALE`、`CONFLICT`、`NOT_FOUND` | 重新读取生命周期 generation、对象身份或文件编辑版本 |
| `RESYNC_REQUIRED` | Live Query 要建立替代订阅并用未截断查询重建基线；Task Channel 要执行明确的重新同步 |
| `RETRY` | 等待负责该对象的模块继续处理，再按接口规则重试；Task Channel 中的新命令要使用更大的请求号 |
| 工具或任务返回 `TIMEOUT`、`CANCELLED` | 从响应或 CQE 中读取执行结果、决定原因和 Context 序号 |
| `DURABILITY`、`INDETERMINATE` | 重新读取对象状态和 terminal record，再决定补偿操作或停止 Replay |

`agent_wait_cancel()` 取消的是目标当前或下一次 `agent_wait()`，它与工具调用和 Task Channel 中的目标取消并非同一套语义。内核会为目标安装一条 `AGENT_EVENT_CANCELLED` 事件，目标从 `agent_event` 读取取消或超时结果后自行处理；返回 `DUPLICATE` 表示已经有一条取消请求等待处理。

模块关系见[产品架构](architecture.md)，各项检查的先后顺序见[安全机制](security.md)。
