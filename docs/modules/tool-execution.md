# 结构化工具调用与执行约定

一次工具调用要经过用户指针、工具目录、工作流权限、文件对象和资源账户。若批量调用、单次调用和任务通道各自解释这些信息，同一个操作可能得到不同结果。AgentOS 让三种调用方式共用同一套内核实现，并在工具实际修改系统状态前，依次检查执行约定、数据来源和资源额度。

## 文档索引

- [工具目录与带类型信息的参数](#工具目录与带类型信息的参数)
- [一次调用经过哪些内核代码](#一次调用经过哪些内核代码)
- [执行约定](#执行约定)
- [来源、资源和结果写入顺序](#来源资源和结果写入顺序)
- [三种调用方式](#三种调用方式)
- [任务通道协议](#任务通道协议)
- [测试结果](#测试结果)
- [实现索引](#实现索引)

<a id="工具目录与-typed-abi"></a>
## 工具目录与带类型信息的参数

内核目录固定登记 25 项工具。每项记录工具名称和编号、参数格式、所需能力、允许的数据来源、输出来源标记以及可能产生的副作用。目录定义位于 [`os/agent_tool_protocol.c`](../../os/agent_tool_protocol.c)，ABI 常量位于 [`include/agent_tool_abi.h`](../../include/agent_tool_abi.h)。

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

系统启动时会自检这张表，包括工具编号是否连续、名称是否重复、参数写入位置是否重叠，以及来源标记和副作用位是否合法。随后，内核以 `agentos.tool.manifest.v1` 作为摘要域，把工具编号、标志、能力、来源规则、副作用、名称和参数格式一并送入 SHA-256，生成 `schema_digest`。执行约定通过这份摘要确认工具含义没有发生变化。

AgentOS 保留三代工具接口：

| 接口 | 请求布局 | 用途 |
| --- | --- | --- |
| V1 `agent_call()` | 192 字节固定结构 | 兼容已有用户程序 |
| V2 `tool_call()` | 72 字节请求头，后接带类型信息的键值参数 | 在运行时选择工具和参数 |
| V3 `tool_call_v3()` | V2 前缀，后接执行约定信息 | 执行已经登记的依赖节点 |

V2 一次最多接收 8 个 `agent_param_v2`。内核逐项复制，并核对参数版本、96 字节布局、键名结尾、类型、值的长度、重复键和必填参数。若同时填写 `tool_id` 与 `tool_name`，两者必须指向同一个目录项。下面的写法与 [`user/src/agenttoolabi_ucore.c`](../../user/src/agenttoolabi_ucore.c) 中的测试一致：

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

用户库到工具实现之间没有另一条快捷路径。以 V3 为例，[`user/lib/syscall.c`](../../user/lib/syscall.c) 只把请求和响应两块 ABI 缓冲区传给内核：

```c
int tool_call_v3(struct agent_request_v3 *req,
                 struct agent_response_v3 *resp)
{
    return syscall(SYS_tool_call, req, resp);
}
```

[`os/syscall.c`](../../os/syscall.c) 把 `SYS_tool_call` 交给 `sys_tool_call()`。该函数先复制 8 字节的 `version` 和 `size`，再选择 V2 或 V3 解码程序。请求通过检查后，会被整理成内核私有的 `agent_op`，最后进入 `agent_execute_one()`。

| 步骤 | 主要函数 | 所做工作 |
| --- | --- | --- |
| 用户态封装 | `tool_call()`、`tool_call_v3()` | 发起 `SYS_tool_call` |
| 系统调用分派 | `syscall()`、`sys_tool_call()` | 复制版本头，选择 V2 或 V3 |
| 参数解码 | `agent_tool_protocol_resolve()`、`agent_tool_protocol_decode_v2()` | 解析名称和编号，检查带类型信息的参数，生成私有 `agent_op` |
| 执行约定检查 | `agent_execution_contract_admit()` | 核对生命周期、节点、尝试次数、前驱、截止时间和输入指纹 |
| 来源与额度检查 | `agent_provenance_authorize_tool()`、`resource_phase_lease_begin()` | 核对来源标记，预留阶段额度 |
| 副作用检查 | `agent_execution_contract_effect_begin()` | 决定开始执行、取消，或直接取用已有结果 |
| 工具执行 | `agent_execute_op()`、`agent_metadata_execute_tool()` | 执行进程通信、元数据或文件操作 |
| 写回结果 | `agent_provenance_commit_tool_output()`、`agent_execution_append_terminal()` | 结算额度，写入上下文、执行记录票号和节点结果 |

`agent_execute_op()` 只读取内核私有副本。元数据工具先进入 `agent_metadata_tool_enter()`，再由 [`os/agent_metadata_objects.c`](../../os/agent_metadata_objects.c) 处理；其余工具由 [`os/agent_core.c`](../../os/agent_core.c) 按编号分派。单次调用、批量调用和任务通道的转换层最终都会进入这条路径。

V2 完成带类型信息的参数解码后，才检查生命周期是否允许新操作。V3 的顺序略有不同：先核对完整请求布局和生命周期绑定，再解析工具与参数范围，调用同一套 V2 参数解码程序，然后检查生命周期并进入 `agent_execute_one()`。执行约定、数据来源、阶段额度和副作用均在工具真正执行之前核对。启用 `ENFORCE` 的工作流中，V3 的工具、参数格式或绑定不合法时，内核会写下一条结构化拒绝记录。记录槽位不足时返回 `NO_SPACE`；槽位暂时不能预留时返回 `RETRY`。

<a id="execution-contract"></a>
## 执行约定

一个活动生命周期最多登记一份执行约定。约定按依赖顺序保存 24 个节点，每个节点最多尝试 4 次，所有已经接受的尝试共用 48 个结果槽。节点先从 `BLOCKED` 进入 `READY` 和 `RUNNING`，执行后记为 `SUCCEEDED`、`FAILED` 或 `CANCELLED`。

| 约束内容 | `agent_execution_contract_node` 字段 |
| --- | --- |
| 工具含义 | `tool_id`、32 字节 `schema_digest`、`required_capabilities`、`side_effect_mask` |
| 节点依赖 | `node_id`、`predecessor_mask` |
| 输入来源 | `accepted_input_labels`、输入和输出结果类型 |
| 输出标记 | `output_add_labels` |
| 执行规则 | `deadline_tick`、`max_attempts`、`retry_policy`、`cancel_policy` |
| 资源上限 | `exec_envelope[]`、`storage_envelope[]`、`charge_class` |

V3 请求还要绑定约定键、节点号、尝试次数、来源节点、来源上下文序号、生产者身份、参数格式摘要和规范化输入指纹。根节点可以直接携带输入；若输入来自其他智能体或前一节点的结果，请求还要带上内核签发的生产者身份。相关结构定义在 [`include/agent_execution_contract_abi.h`](../../include/agent_execution_contract_abi.h)。

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

启用 `ENFORCE` 后，约定中的节点必须通过 V3 请求，或通过带有同等绑定信息的任务通道提交。直接发起的副作用系统调用也要经过 `agent_execution_contract_gate_direct_syscall()`。已经接受的尝试会保存执行结果；合法重放会返回相同结果，并在 V3 响应中设置 `AGENT_RESPONSE_V3_F_CACHED`。

<a id="来源资源与提交顺序"></a>
## 来源、资源和结果写入顺序

工具目录为每项操作登记来源规则。文件内容、外部工具结果和其他智能体消息，分别加入 `UNTRUSTED_FILE_DATA`、`UNTRUSTED_TOOL_OUTPUT` 和 `CROSS_AGENT_DATA`。内核根据当前上下文标记、执行约定和目录规则计算输入输出的来源标记。SHA-256 用于固定输入字节的指纹；来源标记不能代替能力检查，也不能代替文件访问范围检查。

节点开始执行前，会从工作流资源账户中预留一份阶段额度。工具结束后，内核统一取消占用并结算。未通过检查的请求不会留下预留额度；已经创建的对象则在唯一的析构路径中归还额度。

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

最后一步在上下文写入区记录执行序号、工具状态和结果。受执行约定约束的调用还会得到执行记录票号，并更新对应节点。若取消请求在副作用检查之前到达，节点可以写为 `CANCELLED`；工具已经开始修改系统状态后，则按实际执行结果结算。

Nexus 结果文件由用户态运行库单独发布。[`user/include/agent_nexus.h`](../../user/include/agent_nexus.h) 将 `AGENT_NEXUS_ARTIFACT_PUBLISH_IS_ATOMIC` 定义为 `0`。[`user/lib/agent_nexus.c`](../../user/lib/agent_nexus.c) 先取得工作流编辑租约，再写入魔数为零的文件头和数据并执行 `fsync`，最后写入正式文件头并再次执行 `fsync`。uCore 没有可供这里使用的 `rename` 或 `link` 原子替换，因此崩溃可能留下无法读取的占位文件。读取时会重新核对魔数、清单摘要、数据摘要和生命周期。这个文件写入过程与内核工具的上下文记录并不构成同一个原子事务。

## 三种调用方式

| 调用方式 | 提交方法 | 适合的任务 | 结果位置 |
| --- | --- | --- | --- |
| 批量调用 | `agent_run()` 一次提交至多 64 个 `agent_op` | 顺序已经确定的短操作 | `agent_result[]` |
| V3 单次调用 | 每个节点调用一次 `tool_call_v3()` | 运行时产生分支、逐节点决定 | `agent_response_v3` |
| 任务通道 | 16 槽提交队列和完成队列，由 `enter()` 批量推进 | 高频提交、取消和截止时间 | 只读的 `agent_task_cqe` |

三种方式对 `ECHO` 参数的解释相同，也由同一模块写入上下文。在每轮包含 16 个操作的测试中，批量调用、V3 单次调用和任务通道的中位时延分别为 561.0 微秒、2,051.0 微秒和 1,620.5 微秒。逐次结果见[性能测试](../performance.md#6-智能体任务传输)。

<a id="task-sqcq-协议"></a>
## 任务通道协议

`setup` 为任务通道指定唯一提交者，这个调用必须来自进程主线程。后续的 `enter` 和 `resource` 都绑定同一个提交线程及其身份代次号。提交队列和完成队列各映射一页，内核另有一页保存请求私有数据、一页保存资源私有数据。提交队列可由提交者写入，完成队列在用户页表中只读。内核处理 SQE 前会复制完整的 128 字节描述符，此后不再读取用户槽位。

每条 SQE 用以下字段防止复用旧对象：

- `request_id`：在一个通道生命周期内严格递增；
- `ring_generation`：必须绑定当前通道版本；
- `slot_generation = 1 + floor(sq_position / 16)`：用于识别环槽是否已经复用；
- `contract.lifecycle` 和 `contract.generation`：绑定工作流及执行约定；
- 带类型句柄中的 `generation`：用于识别资源槽是否已经复用。

`STALE` 只说明请求引用了过期对象；`RESYNC_REQUIRED` 才表示通道协议已经失去同步。两者的处理方法如下：

| 状态 | 触发情况 | 通道变化 | 调用方处理 |
| --- | --- | --- | --- |
| `AGENT_TASK_CHANNEL_STALE` | `enter.generation` 不是当前通道版本 | 不设置 `RING_F_RESYNC` | 读取返回的通道版本和水位，重新构造这次控制请求 |
| `AGENT_TASK_CHANNEL_STALE` | 提交者身份或工作流生命周期已经变化 | 不设置 `RING_F_RESYNC`；除 ABI 版本、大小和状态外，其余结果为零 | 重新建立任务通道，并使用有效的提交者和工作流 |
| `AGENT_TASK_CHANNEL_STALE` | 取消目标已经被 CQ 确认读取，或目标不存在 | 不设置 `RING_F_RESYNC`；取消命令编号已经用掉，并返回当前通道版本和水位 | 结束对这个目标的控制 |
| `AGENT_TASK_CHANNEL_RESYNC_REQUIRED` | 请求号倒退、队列水位非法、SQE 通道版本或环槽代次号错误、请求槽重复、关联关系非法 | 设置持续的 `RING_F_RESYNC`，通道版本前进，SQ 回到零 | 丢弃尚未接受的 SQE，采用返回的新版本，以 `ENTER_F_RESYNC` 和 `sq_tail=0` 明确恢复 |

[`user/src/agenttask_ucore.c`](../../user/src/agenttask_ucore.c) 用重复请求号制造一次协议错误，并检查两次 `enter` 的结果。第一次应返回 `RESYNC_REQUIRED` 和新的通道版本；第二次使用零队尾清除持续标记。普通 `STALE` 不增加 `protocol_faults` 或 `resync_count`。

每个已经接受的目标任务只产生一条 CQE。取消命令使用自己递增的 `request_id`，并以 `link_request_id` 指向目标，不会另行产生一条取消 CQE。若取消策略当场拒绝命令，取消命令的编号仍会记为已使用；`enter` 返回 `DENIED`，原目标继续运行。完成队列已满时，内核保留尚未写出的结果，等用户读取 CQ 后继续发布。强制截止时间由定时器路径标记，任务到达可调度检查点后，内核生成带 `AGENT_TASK_CQE_F_DEADLINE` 的 CQE。

任务资源 ABI 提供导入、释放和查询操作，并使用 16 字节的带代次号句柄。当前同步转换层位于 [`os/agent_task_bridge.c`](../../os/agent_task_bridge.c)，它接受带类型信息的空输入，输出结果类型为 `NONE`。导入非空资源时，`result.status` 为 `AGENT_TASK_CHANNEL_DENIED`。任务通道的完整路径测试固定了这一行为。

<a id="测试与结果"></a>
## 测试结果

| 用例 | 检查内容 | 成功标记 |
| --- | --- | --- |
| [`agenttoolabi_ucore.c`](../../user/src/agenttoolabi_ucore.c) | V1 与 V2 目录一致性、参数换序、未知键、重复键、类型和长度错误 | `strict_negative_matrix=1` |
| [`agentcontract_ucore.c`](../../user/src/agentcontract_ucore.c) | 24 节点依赖图、参数格式、能力、前驱来源、截止时间、重试和阶段额度 | `replay=1 retry=1 deadline=1 phase_zero_leak=1` |
| [`agenttask_ucore.c`](../../user/src/agenttask_ucore.c) | 队列页权限、队列已满、重新同步、已完成任务的幂等取消和截止时间 | `submit=1 cq_ack=1 monotonic=1 resync=1` |

```bash
make agent-uapi-check
AGENT_TEST_CASE=agenttoolabi_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

## 实现索引

| 代码职责 | 源码 |
| --- | --- |
| UAPI 与用户态封装 | [`include/agent_tool_abi.h`](../../include/agent_tool_abi.h)、[`user/include/agent.h`](../../user/include/agent.h)、[`user/lib/syscall.c`](../../user/lib/syscall.c) |
| Nexus 结果文件发布 | [`user/include/agent_nexus.h`](../../user/include/agent_nexus.h)、[`user/lib/agent_nexus.c`](../../user/lib/agent_nexus.c) |
| 工具目录与参数格式 | [`os/agent_tool_protocol.c`](../../os/agent_tool_protocol.c) |
| 工具分派与上下文写入 | [`os/agent_core.c`](../../os/agent_core.c)、[`os/agent_context.c`](../../os/agent_context.c) |
| 执行约定 | [`include/agent_execution_contract_abi.h`](../../include/agent_execution_contract_abi.h)、[`os/agent_execution_contract.c`](../../os/agent_execution_contract.c) |
| 数据来源与资源计费 | [`os/agent_provenance.c`](../../os/agent_provenance.c)、[`os/agent_resource.c`](../../os/agent_resource.c) |
| 任务通道与转换层 | [`include/agent_task_channel_abi.h`](../../include/agent_task_channel_abi.h)、[`os/agent_task_channel.c`](../../os/agent_task_channel.c)、[`os/agent_task_bridge.c`](../../os/agent_task_bridge.c) |

公开结构和状态码见 [API](../api.md)，各项检查的先后顺序见[安全机制](../security.md)。
