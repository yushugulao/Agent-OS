# Agent-OS 内核结构化交互接口与工具调用协议

Agent 通过工具调用协议向内核提交工具编号、typed 参数和版本化请求，内核以状态码和结构化结果回应。一次调用还要经过用户指针、Tool Registry、工作流能力位、文件对象和 Workflow Credit Domain。若 Batch、单次调用和 Task Channel 各自解释这些信息，同一个操作可能得到不同结果。AgentOS 让这些调用方式共用同一套内核执行路径，并在工具实际修改系统状态前，依次检查 Execution Contract、provenance 和 Phase Lease。

## 文档索引

- [Tool Registry 与 typed schema](#tool-registry-与-typed-schema)
- [一次调用经过哪些内核代码](#一次调用经过哪些内核代码)
- [Execution Contract](#execution-contract)
- [Provenance、资源与 commit 顺序](#provenance资源与-commit-顺序)
- [三种调用方式](#三种调用方式)
- [Task Channel 协议](#task-channel-协议)
- [测试结果](#测试结果)
- [实现索引](#实现索引)

<a id="工具目录与-typed-abi"></a>
## Tool Registry 与 typed schema

内核 Tool Registry 固定登记 25 项工具。每项记录工具名称/编号、参数 schema、所需能力位、允许的输入 label、输出 provenance label 和副作用掩码。Registry 定义位于 [`os/agent_tool_protocol.c`](../../os/agent_tool_protocol.c)，ABI 常量位于 [`include/agent_tool_abi.h`](../../include/agent_tool_abi.h)。

```c
#define AGENT_TOOL_REGISTRY(X) \
    X(AGENT_TOOL_ECHO, AGENT_TOOL_F_CALLABLE, "echo", ...) \
    X(AGENT_TOOL_QUERY_FILE, AGENT_TOOL_F_CALLABLE, "query_file", ...) \
    X(AGENT_TOOL_SEND_MESSAGE, AGENT_TOOL_F_CALLABLE, "send_message", ...) \
    X(AGENT_TOOL_ACTION_COMMIT, AGENT_TOOL_F_CALLABLE, "action_commit", ...)

static const struct param_rule rules[] = {
    R(PAYLOAD, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1),
    R(TARGET_PID, AGENT_PARAM_UINT64, PARAM_ARG0, 1),
    R(MESSAGE, AGENT_PARAM_STRING, PARAM_PAYLOAD, 1),
};
```

系统启动时会自检这张表，包括工具编号是否连续、名称是否重复、参数写入位置是否重叠，以及 provenance 和副作用位是否合法。随后，内核以 `agentos.tool.manifest.v1` 作为摘要域，对工具编号、标志、能力位、provenance 规则、副作用、名称和 schema 计算内容摘要，生成 `schema_digest`。Execution Contract 通过这份摘要确认工具语义没有发生变化。

AgentOS 保留三代工具接口：

| 接口 | 请求布局 | 用途 |
| --- | --- | --- |
| V1 `agent_call()` | 192 字节固定结构 | 兼容已有用户程序 |
| V2 `tool_call()` | 72 字节请求头，后接带类型信息的键值参数 | 在运行时选择工具和参数 |
| V3 `tool_call_v3()` | V2 前缀，后接 Execution Contract 绑定信息 | 执行已经登记的依赖节点 |

V2 一次最多接收 8 个 `agent_param_v2`。内核逐项复制，并核对参数版本、96 字节布局、键名结尾、类型、值长度、重复键和必填字段。若同时填写 `tool_id` 与 `tool_name`，两者必须指向同一个 Registry 条目。下面的写法与 [`user/src/agenttoolabi_ucore.c`](../../user/src/agenttoolabi_ucore.c) 中的测试一致：

```c
struct agent_param_v2 params[2] = {0};
struct agent_request_v2 req = {0};
struct agent_response_v2 resp = {0};

req.version = AGENT_CALL_VERSION_V2;
req.size = sizeof(req);
req.tool_id = AGENT_TOOL_ECHO;
req.param_count = 2;
req.request_id = 1001;
req.params = (uint64)params;
strcpy(req.tool_name, "echo");

/* params[] 分别填写 key、type、value_size 和 value。 */
tool_call(&req, &resp);
```

<a id="一次调用的内核路径"></a>
## 一次调用经过哪些内核代码

Guest 运行库到工具实现之间没有另一条快捷路径。以 V3 为例，[`user/lib/syscall.c`](../../user/lib/syscall.c) 只把请求和响应两块 ABI 缓冲区传给内核：

```c
int tool_call_v3(struct agent_request_v3 *req,
                 struct agent_response_v3 *resp)
{
    return syscall(SYS_tool_call, req, resp);
}
```

[`os/syscall.c`](../../os/syscall.c) 把 `SYS_tool_call` 交给 `sys_tool_call()`。该函数先复制 8 字节 `version`/`size`，再选择 V2 或 V3 解码器。请求通过检查后会被整理成内核私有的 `agent_op`，最后进入 `agent_execute_one()`。

| 步骤 | 主要函数 | 所做工作 |
| --- | --- | --- |
| Guest 封装 | `tool_call()`、`tool_call_v3()` | 发起 `SYS_tool_call` |
| 系统调用分派 | `syscall()`、`sys_tool_call()` | 复制版本头，选择 V2 或 V3 |
| schema 解码 | `agent_tool_protocol_resolve()`、`agent_tool_protocol_decode_v2()` | 解析名称/编号，检查带类型信息的参数，生成内核私有的 `agent_op` |
| Contract 准入 | `agent_execution_contract_admit()` | 核对生命周期、节点、尝试次数、前驱、截止时间和输入指纹 |
| provenance/资源 | `agent_provenance_authorize_tool()`、`resource_phase_lease_begin()` | 核对 provenance label，reserve Phase Lease |
| 副作用检查 | `agent_execution_contract_effect_begin()` | 决定执行、取消或返回缓存结果 |
| 工具执行 | `agent_execute_op()`、`agent_metadata_execute_tool()` | 执行 IPC、元数据或文件操作 |
| terminal commit | `agent_provenance_commit_tool_output()`、`agent_execution_append_terminal()` | 结算资源，commit Context、工作流记录票号和节点结果 |

`agent_execute_op()` 只读取内核私有副本。元数据工具先进入 `agent_metadata_tool_enter()`，再由 [`os/agent_metadata_objects.c`](../../os/agent_metadata_objects.c) 处理；其余工具由 [`os/agent_core.c`](../../os/agent_core.c) 按编号分派。单次调用、批处理和 Task Bridge 最终都会进入这条路径。

V2 完成参数 schema 解码后，才检查生命周期是否允许新操作。V3 的顺序略有不同：先核对完整请求布局和生命周期绑定，再解析工具与参数范围，调用同一套 V2 解码器，然后登记普通操作并进入 `agent_execute_one()`。Execution Contract、provenance、Phase Lease 和副作用状态均在工具真正执行之前核对。启用 `ENFORCE` 的工作流中，V3 的工具、schema 或绑定不合法时，内核会写下一条结构化拒绝记录。工作流记录槽位不足时返回 `NO_SPACE`；槽位暂时不能 reserve 时返回 `RETRY`。

## Execution Contract

一个活动生命周期最多登记一份 Execution Contract。Contract 按依赖顺序保存 24 个节点，每个节点最多尝试 4 次，所有已接受的尝试共用 48 个结果槽位。节点先从 `BLOCKED` 进入 `READY` 和 `RUNNING`，执行后进入 `SUCCEEDED`、`FAILED` 或 `CANCELLED` terminal state。

| 约束内容 | `agent_execution_contract_node` 字段 |
| --- | --- |
| 工具语义 | `tool_id`、32 字节 `schema_digest`、`required_capabilities`、`side_effect_mask` |
| DAG 依赖 | `node_id`、`predecessor_mask` |
| 输入 provenance | `accepted_input_labels`、输入/输出 artifact 类型 |
| 输出 provenance | `output_add_labels` |
| 执行策略 | `deadline_tick`、`max_attempts`、`retry_policy`、`cancel_policy` |
| 资源上限 | `exec_envelope[]`、`storage_envelope[]`、`charge_class` |

V3 请求还要绑定 Execution Contract 键、节点号、尝试编号、来源节点、来源 Context 序号、生产者身份、schema digest 和规范化输入指纹。根节点可以直接携带输入；若输入来自其他 Agent 或前驱结果，请求还要带上内核签发的生产者身份。相关结构定义在 [`include/agent_execution_contract_abi.h`](../../include/agent_execution_contract_abi.h)。

```c
struct agent_execution_contract_control control = {0};

control.version = AGENT_EXECUTION_CONTRACT_VERSION;
control.size = sizeof(control);
control.operation = AGENT_EXECUTION_CONTRACT_CREATE;
control.flags = AGENT_EXECUTION_CONTRACT_F_ENFORCE;
control.nodes = (uint64)nodes;
control.node_count = node_count;
control.node_size = sizeof(nodes[0]);
agent_execution_contract(&control, &result);
```

启用 `ENFORCE` 后，Execution Contract 节点必须通过 V3 请求，或通过带有同等绑定信息的 Task Channel 提交。直接发起的副作用系统调用也要经过 `agent_execution_contract_gate_direct_syscall()`。已接受的尝试会保存 terminal state；合法 Replay 返回同一结果，并在 V3 响应中设置 `AGENT_RESPONSE_V3_F_CACHED`。

<a id="来源资源与提交顺序"></a>
## Provenance、资源与 commit 顺序

Tool Registry 为每项操作登记 provenance 规则。文件内容、外部工具输出和跨 Agent 消息分别加入 `UNTRUSTED_FILE_DATA`、`UNTRUSTED_TOOL_OUTPUT` 和 `CROSS_AGENT_DATA`。内核根据当前 Context、Execution Contract 和 Registry 规则计算输入/输出 provenance label。内容指纹用于固定输入字节；provenance 不能代替能力位检查，也不能代替 VFS 文件访问范围检查。

节点开始执行前，会从 Workflow Credit Domain 中 reserve 一份 Phase Lease。工具结束后，内核统一停用并结算。未通过检查的请求不会留下 `pending` 额度；已经创建的对象则在唯一析构路径中归还额度。

[`os/agent_core.c`](../../os/agent_core.c) 中的代码直接给出了写入顺序：

```c
admission = agent_execution_contract_admit(...);
status = agent_provenance_authorize_tool(...);
resource_phase_lease_begin(...);
effect_admission = agent_execution_contract_effect_begin(&claim);

agent_execute_op(p, op, res);
resource_phase_lease_settle(&phase_lease, phase_generation);
agent_provenance_commit_tool_output(p, &provenance_decision, res->status);
agent_execution_append_terminal(p, op, res, tick, status, ...);
```

最后一步在 Context commit lane 记录序号、工具状态和结果。受 Execution Contract 约束的调用还会得到工作流记录票号，并更新对应节点。若取消请求在副作用检查之前到达，节点可以进入 `CANCELLED`；工具已经开始修改系统状态后，则按实际 terminal state 结算。

Nexus 的 artifact 由 Guest Runtime 组织，再通过 `agent_file_publish()` 把 header 与 payload 一次交给内核。VFS 在未命名 inode 中写完全部字节并完成第一阶段 checkpoint，随后只做一次正式目录接入，再以 attach-only checkpoint 固定目录项。这样，正式路径要么不存在，要么能够读到完整内容；同名发布不会覆盖先到的文件。对于 `DUPLICATE` 和 `INDETERMINATE`，Nexus 回读正式路径，只有 header、payload 和 EOF 都与本次请求严格一致时才按幂等成功处理。Context、metadata 与 Workflow Fence 继续用 artifact handle、lifecycle generation 和 terminal record 对齐。

## 三种调用方式

| 调用方式 | 提交方法 | 适合的任务 | 结果位置 |
| --- | --- | --- | --- |
| 批处理 | `agent_run()` 一次提交至多 64 个 `agent_op` | 顺序已经确定的短操作 | `agent_result[]` |
| V3 单次调用 | 每个节点调用一次 `tool_call_v3()` | 运行时产生分支、逐节点决定 | `agent_response_v3` |
| Task Channel | 16 槽 SQ/CQ，由 `enter()` 批量推进 | 高频提交、取消和截止时间 | 只读 `agent_task_cqe` |

三种方式对 `ECHO` 参数的解释相同，也由同一模块 commit Context。在每轮包含 16 个操作的测试中，批处理、V3 单次调用和 Task Channel 的中位延迟分别为 561.0 微秒、2,051.0 微秒和 1,620.5 微秒。逐次结果见[性能测试](../performance.md#6-agent-task-传输)。

<a id="task-sqcq-协议"></a>
## Task Channel 协议

`setup` 为 Task Channel 指定唯一提交者，这个调用必须来自进程主线程。后续 `enter` 和 `resource` 都绑定同一个提交线程及其身份 generation。SQ 和 CQ 各映射一页，内核另有一页保存请求私有数据、一页保存资源私有数据。SQ 由提交者写入，CQ 在用户页表中只读。内核处理 SQE 前会复制完整的 128 字节描述符，此后不再读取用户槽位。

每条 SQE 用以下字段防止复用旧对象：

- `request_id`：在一个 Task Channel 生命周期内严格递增；
- `ring_generation`：必须绑定当前通道 generation；
- `slot_generation = 1 + floor(sq_position / 16)`：用于识别环槽是否已经复用；
- `contract.lifecycle` 和 `contract.generation`：绑定工作流与 Execution Contract；
- 类型化句柄中的 `generation`：用于识别资源槽位是否已经复用。

`STALE` 只说明请求引用了过期对象；`RESYNC_REQUIRED` 才表示 Task Channel 协议已经失去同步。两者的处理方法如下：

| 状态 | 触发情况 | 通道状态 | 调用方处理 |
| --- | --- | --- | --- |
| `AGENT_TASK_CHANNEL_STALE` | `enter.generation` 不是当前通道 generation | 不设置 `RING_F_RESYNC` | 读取返回的 generation 和水位，重新构造这次控制请求 |
| `AGENT_TASK_CHANNEL_STALE` | 提交者身份或工作流生命周期已经变化 | 不设置 `RING_F_RESYNC`；除 ABI 版本、大小和状态外，其余结果为零 | 重建 Task Channel，并使用有效的提交者和工作流 |
| `AGENT_TASK_CHANNEL_STALE` | 取消目标已经被 CQ 确认读取，或目标不存在 | 不设置 `RING_F_RESYNC`；取消命令编号已经用掉，并返回当前 generation 和水位 | 结束对这个目标的控制 |
| `AGENT_TASK_CHANNEL_RESYNC_REQUIRED` | 请求号倒退、队列水位非法、SQE 通道 generation 或 `slot_generation` 错误、请求槽重复、关联关系非法 | 设置持续的 `RING_F_RESYNC`，通道 generation 前进，SQ 回到零 | 丢弃尚未接受的 SQE，采用返回的新 generation，以 `ENTER_F_RESYNC` 和 `sq_tail=0` 明确重新同步 |

[`user/src/agenttask_ucore.c`](../../user/src/agenttask_ucore.c) 用重复请求号制造一次协议错误，并检查两次 `enter` 的结果。第一次应返回 `RESYNC_REQUIRED` 和新的通道 generation；第二次使用零队尾清除持续重新同步标记。普通 `STALE` 不增加 `protocol_faults` 或 `resync_count`。

每个已经接受的目标任务只产生一条 CQE。取消命令使用自己递增的 `request_id`，并以 `link_request_id` 指向目标，不会另行产生一条取消 CQE。若取消策略当场拒绝命令，取消命令的编号仍会记为已使用；`enter` 返回 `DENIED`，原目标继续运行。CQ 已满时，内核保留尚未写出的结果，等用户确认读取后继续发布。强制截止时间由定时器路径标记，任务到达可调度检查点后，内核生成带 `AGENT_TASK_CQE_F_DEADLINE` 的 CQE。

任务资源 ABI 提供导入、释放和查询操作，并使用 16 字节的类型化句柄，其中包含槽位 generation。`IMPORT` 仅从当前 Agent 可读的普通文件描述符导入准确的 1–63 字节 OWNED UTF-8，并绑定该 Agent 已经存在且经过校验的最新 Context，不会借导入操作新建 Context。Task Bridge 从 offset 0 读取并额外探测 EOF，不改变文件的共享 offset；通过 NUL 与 UTF-8 检查后，内核保存不可变快照、内容指纹、producer、Context sequence 和 `UNTRUSTED_FILE_DATA` provenance，不保留文件对象指针。

SQE 可以把同一句柄作为 BORROWED 输入。当前 ECHO Task Bridge 会把内核快照复制到工具 payload：BORROWED 任务完成后资源继续处于 `LIVE`，OWNED 任务完成后则自动消费。显式 `RELEASE` 或槽位再次使用后，旧 generation 查询返回 `STALE`；成功 IMPORT 的最终 copyout 失败时，通道会回滚尚未暴露的槽位。

<a id="测试与结果"></a>
## 测试结果

| 用例 | 检查内容 | 成功标记 |
| --- | --- | --- |
| [`agenttoolabi_ucore.c`](../../user/src/agenttoolabi_ucore.c) | V1 与 V2 目录一致性、参数换序、未知键、重复键、类型和长度错误 | `strict_negative_matrix=1` |
| [`agentcontract_ucore.c`](../../user/src/agentcontract_ucore.c) | 24 节点依赖图、schema、能力位、前驱 provenance、截止时间、重试和 Phase Lease | `replay=1 retry=1 deadline=1 phase_zero_leak=1` |
| [`agenttask_ucore.c`](../../user/src/agenttask_ucore.c) | 队列页权限、SQ/CQ 满载、重新同步、幂等取消、截止时间、UTF-8 快照、OWNED/BORROWED 生命周期和 fd transaction pin | `resource_utf8_snapshot=1 borrowed_live=1 owned_consumed=1 release_stale=1 generation_aba=1` |
| [`agentpublish_ucore.c`](../../user/src/agentpublish_ucore.c) | 完整 header/payload/EOF、同名竞争、不覆盖、Nexus 幂等回读、非法请求零副作用和资源回收 | `same_scope_race=1 ok=1 duplicate=1 no_overwrite=1` |

```bash
make agent-uapi-check
AGENT_TEST_CASE=agenttoolabi_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentpublish_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

`agenttoolabi_ucore` 的正向调用覆盖 Registry 中的结构化请求，错误矩阵则逐项改变参数顺序、键名、类型与长度；内核只接受符合 schema 的输入，并返回可区分的失败状态。`agentcontract_ucore` 把同一工具放入带前驱、deadline、retry 和资源上限的执行图中，结果仍能按节点生成唯一 terminal record，资源在结束后回到零。两组结果说明工具调用协议不仅能被解析，还能把错误处理、执行约束和结果记录统一到同一条内核路径。

`agenttask_ucore` 的资源检查标志全部通过。测试从当前文件访问范围准备输入，确认 ECHO 使用导入时的 UTF-8 快照而不是再次读取 fd；BORROWED 完成后仍可查询，OWNED 完成后自动消费，显式释放与槽位复用前的旧 generation 都返回 `STALE`。独立的 `resource_unlinked_close_race=1 transaction_pin=1 launched_concurrently=1` 标志还确认 sibling close 与导入并发启动并完成；descriptor transaction 检查负责核对确定的 pin、读取与结算顺序。这说明 Task resource 的内容、所有权和 generation 在完整 SQ/CQ 路径中保持一致。

Agent Task 性能测试使用 16 个等价 `ECHO` 操作。Batch 中位耗时为 `561.0 微秒`，Scalar V3 为 `2,051.0 微秒`，`SQ/CQ` 为 `1,620.5 微秒`。这组短同步调用中，Batch 通过一次 syscall 合并提交，适合追求最低批量延迟；`SQ/CQ` 没有在该负载上胜过 Batch，它的作用是保留长期队列、backpressure、cancel 和唯一 terminal CQE，不能把两者按单个延迟指标简单替代。

`agentpublish_ucore` 用两个同 scope 进程同时发布同名文件，结果恰好是一个 `OK`、一个 `DUPLICATE`，胜出文件保持完整且后续提交不能覆盖。32 字节 header 与 96 字节 payload 后紧接 EOF；错误的 pointer、path、size、version 和保留字段都没有留下正式文件名。Nexus 对相同字节能够通过正式路径回读收敛，内容不同则拒绝；删除测试文件后 inode 与 block 计数回到基线。这些结果说明结果文件的正式名字只指向完整内容，并发和失败请求也没有留下额外资源。

## 实现索引

| 代码职责 | 源码 |
| --- | --- |
| UAPI 与用户态封装 | [`include/agent_tool_abi.h`](../../include/agent_tool_abi.h)、[`user/include/agent.h`](../../user/include/agent.h)、[`user/lib/syscall.c`](../../user/lib/syscall.c) |
| Nexus artifact 发布 | [`include/agent_file_publish_abi.h`](../../include/agent_file_publish_abi.h)、[`os/agent_file_state.c`](../../os/agent_file_state.c)、[`os/fs.c`](../../os/fs.c)、[`user/include/agent_nexus.h`](../../user/include/agent_nexus.h)、[`user/lib/agent_nexus.c`](../../user/lib/agent_nexus.c) |
| Tool Registry 与 schema | [`os/agent_tool_protocol.c`](../../os/agent_tool_protocol.c) |
| 工具分派与 Context commit | [`os/agent_core.c`](../../os/agent_core.c)、[`os/agent_context.c`](../../os/agent_context.c) |
| Execution Contract | [`include/agent_execution_contract_abi.h`](../../include/agent_execution_contract_abi.h)、[`os/agent_execution_contract.c`](../../os/agent_execution_contract.c) |
| Provenance 与资源计费 | [`os/agent_provenance.c`](../../os/agent_provenance.c)、[`os/agent_resource.c`](../../os/agent_resource.c) |
| Task Channel 与 Task Bridge | [`include/agent_task_channel_abi.h`](../../include/agent_task_channel_abi.h)、[`os/agent_task_channel.c`](../../os/agent_task_channel.c)、[`os/agent_task_bridge.c`](../../os/agent_task_bridge.c) |

公开结构和状态码见 [API](../api.md)，各项检查的先后顺序见[安全机制](../security.md)。
