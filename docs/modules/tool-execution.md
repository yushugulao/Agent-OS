# 结构化工具与执行合同

Agent 的一次工具调用会跨过用户指针、工具目录、workflow 权限、文件对象和资源账户。若各条传输路径分别解释这些状态，同一个动作会因调用方式不同而得到不同结果。AgentOS 将 Scalar、Compact Batch 与 Task SQ/CQ 汇入同一套 owner 实现，并在工具产生副作用前完成合同、来源和资源检查。

## 文档索引

- [工具目录与 Typed ABI](#工具目录与-typed-abi)
- [一次调用的内核路径](#一次调用的内核路径)
- [Execution Contract](#execution-contract)
- [来源、资源与提交顺序](#来源资源与提交顺序)
- [三种传输路径](#三种传输路径)
- [Task SQ/CQ 协议](#task-sqcq-协议)
- [测试与结果](#测试与结果)
- [实现索引](#实现索引)

## 工具目录与 Typed ABI

内核目录固定登记 25 项工具。每项同时描述 name/id、参数 schema、required capability、可接受的输入来源标签、输出标签和 side-effect mask。目录定义位于 [`os/agent_tool_protocol.c`](../../os/agent_tool_protocol.c)，ABI 常量位于 [`include/agent_tool_abi.h`](../../include/agent_tool_abi.h)。

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

这份注册表在启动时检查工具编号连续性、名称唯一性、参数 target 不重叠以及来源和副作用位是否合法。schema digest 以 `agentos.tool.manifest.v1` 为域，将 tool id、flags、capability、来源策略、side effect、名称和 schema 一起送入 SHA-256。合同节点因而可以绑定一份确定的工具语义。

AgentOS 保留三代工具调用接口：

| 接口 | Request | 主要用途 |
| --- | --- | --- |
| V1 `agent_call()` | 192 字节固定布局 | 兼容已有 Guest 程序 |
| V2 `tool_call()` | 72 字节头部与 typed-KV 数组 | 运行时选择工具与参数 |
| V3 `tool_call_v3()` | V2 前缀加合同绑定 | 执行冻结 DAG 节点 |

V2 一次最多接收 8 个 `agent_param_v2`。内核逐项 copyin，并检查参数版本、96 字节布局、key 终止符、类型、value size、重复 key 和必要参数。`tool_id` 与 `tool_name` 同时填写时必须命中同一目录项。以下调用形态与 [`user/src/agenttoolabi_ucore.c`](../../user/src/agenttoolabi_ucore.c) 的测试夹具一致：

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

/* params[] 分别填写 key/type/value_size/value。 */
tool_call(&req, &resp);
```

## 一次调用的内核路径

用户库、系统调用分派和工具 owner 之间没有旁路。以 V3 为例，[`user/lib/syscall.c`](../../user/lib/syscall.c) 的封装只传递两块 ABI 缓冲区：

```c
int tool_call_v3(struct agent_request_v3 *req,
                 struct agent_response_v3 *resp)
{
    return syscall(SYS_tool_call, req, resp);
}
```

[`os/syscall.c`](../../os/syscall.c) 将 `SYS_tool_call` 交给 `sys_tool_call()`；该函数先 copyin 8 字节 `version + size` 头，再选择 V2 或 V3 解码器。通过校验的 request 被压缩为内核私有 `agent_op`，随后进入 `agent_execute_one()`。完整路径如下：

| 阶段 | 主要符号 | 处理内容 |
| --- | --- | --- |
| Guest 封装 | `tool_call()`、`tool_call_v3()` | 进入 `SYS_tool_call` |
| Syscall dispatch | `syscall()`、`sys_tool_call()` | copyin 版本头，选择 V2/V3 |
| 协议解码 | `agent_tool_protocol_resolve()`、`agent_tool_protocol_decode_v2()` | 解析 name/id，校验 typed schema，生成私有 `agent_op` |
| 合同准入 | `agent_execution_contract_admit()` | 检查 lifecycle、node、attempt、前驱、deadline 和输入指纹 |
| 来源与额度 | `agent_provenance_authorize_tool()`、`resource_phase_lease_begin()` | 核对来源标签并锁定 phase credit |
| Effect gate | `agent_execution_contract_effect_begin()` | 决定进入副作用、取消或复用终态 |
| Owner 执行 | `agent_execute_op()`、`agent_metadata_execute_tool()` | 执行进程、IPC、metadata 或文件 owner 逻辑 |
| 终态提交 | `agent_provenance_commit_tool_output()`、`agent_execution_append_terminal()` | 结算额度，发布 Context、Evidence 与合同终态 |

`agent_execute_op()` 只接收私有副本。metadata 工具先进入 `agent_metadata_tool_enter()`，由 [`os/agent_metadata_objects.c`](../../os/agent_metadata_objects.c) 处理；其余工具在 [`os/agent_core.c`](../../os/agent_core.c) 中按 tool id 分派。Scalar、Batch 与 Task bridge 最终都调用这条 owner 路径。

V2 在 typed-KV 解码完成后才进入 lifecycle operation gate。V3 先核对完整 request 布局和 lifecycle binding，再复用 V2 decoder 生成 `agent_op`，随后进入 operation gate 与 `agent_execute_one()`；合同准入、来源授权、phase lease 和 effect gate 都发生在 owner 执行之前。ENFORCE lifecycle 中，V3 的非法 tool/schema/binding 会形成结构化安全终态；安全终态槽位不足时返回 `NO_SPACE`，其它暂时无法预留终态记录的情况返回 `RETRY`。

## Execution Contract

一个 active lifecycle 最多冻结一份合同。合同按拓扑顺序保存 24 个节点，全部 accepted attempt 共用 48 个终态槽，每个节点最多 4 次 attempt。节点状态在 `BLOCKED -> READY -> RUNNING -> SUCCEEDED/FAILED/CANCELLED` 之间推进。

| 约束 | `agent_execution_contract_node` 字段 |
| --- | --- |
| 工具语义 | `tool_id`、32 字节 `schema_digest`、`required_capabilities`、`side_effect_mask` |
| 拓扑依赖 | `node_id`、`predecessor_mask` |
| 输入来源 | `accepted_input_labels`、input/output artifact type |
| 输出传播 | `output_add_labels` |
| 控制策略 | `deadline_tick`、`max_attempts`、`retry_policy`、`cancel_policy` |
| 资源包络 | `exec_envelope[]`、`storage_envelope[]`、`charge_class` |

V3 request 继续绑定 contract key、node、attempt、source node、source Context sequence、producer identity、schema digest 和 canonical input fingerprint。根节点可以使用 inline input；跨 Agent 前驱和资源输入还要携带 kernel-issued producer identity。关键结构定义在 [`include/agent_execution_contract_abi.h`](../../include/agent_execution_contract_abi.h)。

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

`ENFORCE` 生效后，合同内节点必须通过 V3 或带等价 binding 的 Task Channel 提交。受约束的直接副作用 syscall 也经过 `agent_execution_contract_gate_direct_syscall()`。完成缓存记录 accepted attempt 的终态；合法重放返回同一结果，并在 V3 response 中设置 `AGENT_RESPONSE_V3_F_CACHED`。

## 来源、资源与提交顺序

工具目录给每项操作附带 provenance manifest。文件内容、工具输出与跨 Agent 消息分别引入 `UNTRUSTED_FILE_DATA`、`UNTRUSTED_TOOL_OUTPUT` 和 `CROSS_AGENT_DATA`；授权判断使用当前 Context 标签、合同声明和目录策略共同计算输入与输出标签。固定输入字节由 SHA-256 形成 fingerprint，来源标签不能代替 capability 或 VFS scope。

合同节点在执行前从 workflow 资源账户建立 Tool Phase Credit Lease。额度从已计费账户中暂时锁定，owner 执行结束后统一 deactivate 和 settle；准入失败不会留下 reservation，已经发布的对象沿唯一析构路径归还 credit。

`agent_execute_one()` 的提交次序直接体现在 [`os/agent_core.c`](../../os/agent_core.c) 中：

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

最后一步在 Context commit lane 中写入 sequence、工具状态和结果，同时为受约束调用生成 Evidence ticket 并推进合同节点终态。取消在 effect gate 前可产生 `CANCELLED`；owner 已开始副作用后，系统按照实际执行结果完成结算。

Nexus artifact 字节由用户态运行库单独发布。[`user/include/agent_nexus.h`](../../user/include/agent_nexus.h) 将 `AGENT_NEXUS_ARTIFACT_PUBLISH_IS_ATOMIC` 定义为 `0`；[`user/lib/agent_nexus.c`](../../user/lib/agent_nexus.c) 使用 workflow edit lease 串行化 publisher，先写入 zero-magic header 与 payload 并 `fsync`，再写最终 header 并再次 `fsync`。uCore 没有 rename/link 原子提交，崩溃时可能留下不可读的 publish-once tombstone；artifact read 会重新核对 magic、manifest digest、payload digest 和 lifecycle。该文件发布不与内核工具的 Context terminal 合并为一个原子事务。

## 三种传输路径

| 路径 | 提交方式 | 适合负载 | 终态位置 |
| --- | --- | --- | --- |
| Compact Batch | `agent_run()` 一次提交至多 64 个 `agent_op` | 已知的短顺序操作 | `agent_result[]` |
| Scalar V3 | 每个节点一次 `tool_call_v3()` | 动态分支、逐节点决策 | `agent_response_v3` |
| Task SQ/CQ | 16 槽共享 SQ/CQ，`enter()` 批量推进 | 高频提交、取消与 deadline | 只读 `agent_task_cqe` |

三条路径使用相同的 ECHO 语义指纹和 Context owner。一次 sequence 包含 16 个操作的测试中，Batch、Scalar V3 和 SQ/CQ 的中位时延分别为 `561.0 us`、`2,051.0 us` 和 `1,620.5 us`。逐样本分布见[性能测试](../performance.md#6-agent-task-传输路径)。

## Task SQ/CQ 协议

Task Channel 在 `setup` 时建立 single-issuer 通道；该调用必须来自进程主线程，后续 `enter` 与 `resource` 绑定同一 issuer tid 和 identity generation。SQ 和 CQ 各映射一页，内核另持 request private 与 resource private 两页。SQ 对 issuer 可写，CQ 在用户页表中只读；内核消费 SQE 前复制完整 128 字节描述符，此后不再读取用户槽位。

每条 SQE 携带以下复用保护：

- `request_id`：通道生命周期内严格递增；
- `ring_generation`：绑定当前通道协议代；
- `slot_generation = 1 + floor(sq_position / 16)`：识别环槽复用；
- `contract.lifecycle + contract.generation`：绑定 workflow 与合同；
- typed handle generation：识别资源槽 ABA。

Task Channel 的 `STALE` 与 sticky resync 处理不同：

| 状态 | 触发示例 | 通道状态 | 调用方动作 |
| --- | --- | --- | --- |
| `AGENT_TASK_CHANNEL_STALE` | `enter.generation` 不是当前 generation | 不自动设置 `RING_F_RESYNC` | 读取返回的当前 generation 和水位，重建本次控制请求 |
| `AGENT_TASK_CHANNEL_STALE` | issuer identity/lifecycle 已变化 | 不自动设置 `RING_F_RESYNC`；结果除 ABI version/size/status 外为零 | 重新建立有效 owner/lifecycle |
| `AGENT_TASK_CHANNEL_STALE` | cancel 目标已被 CQ ack 或不存在 | 不自动设置 `RING_F_RESYNC`；cancel id 已消费，返回当前 channel generation 与水位 | 结束对已消失目标的控制 |
| `AGENT_TASK_CHANNEL_RESYNC_REQUIRED` | request id 倒退、SQ/CQ 水位非法、SQE ring/slot generation 错误、重复 request 槽或非法 link | 设置 sticky `RING_F_RESYNC`，generation 前进，SQ 重置到零 | 丢弃未接受 SQE，采用返回的新 generation，以 `ENTER_F_RESYNC + sq_tail=0` 显式恢复 |

[`user/src/agenttask_ucore.c`](../../user/src/agenttask_ucore.c) 用重复 request id 制造协议故障，并验证两次 enter 的恢复过程：第一次返回 `RESYNC_REQUIRED` 和新 generation，第二次以零 tail 清除 sticky flag。普通 `STALE` 不增加 `protocol_faults` 或 `resync_count`。

每个 accepted target 最终只发布一个 CQE。cancel command 使用自己的递增 request id，在 `link_request_id` 中引用目标，不生成第二个 cancel CQE；取消策略同步拒绝时，该 cancel id 已被消费，`enter` 返回 `DENIED`，目标继续运行。CQ full 保留尚未发布的终态并施加 backpressure；hard deadline 在 timer 路径标记，到达可调度检查点后生成带 `AGENT_TASK_CQE_F_DEADLINE` 的 terminal CQE。

Task resource ABI 定义 import/release/query 和 16 字节 generation handle。当前同步 bridge 在 [`os/agent_task_bridge.c`](../../os/agent_task_bridge.c) 中接受 typed null input，输出 artifact type 为 `NONE`；非空资源 import 的 `result.status` 为 `AGENT_TASK_CHANNEL_DENIED`。该行为由 Task vertical test 固定。

## 测试与结果

| 用例 | 覆盖内容 | 成功标记 |
| --- | --- | --- |
| [`agenttoolabi_ucore.c`](../../user/src/agenttoolabi_ucore.c) | V1/V2 目录一致性、参数重排、未知/重复 key、类型和长度负向矩阵 | `strict_negative_matrix=1` |
| [`agentcontract_ucore.c`](../../user/src/agentcontract_ucore.c) | 24 节点 DAG、schema、capability、前驱来源、deadline、retry、phase credit | `replay=1 retry=1 deadline=1 phase_zero_leak=1` |
| [`agenttask_ucore.c`](../../user/src/agenttask_ucore.c) | SQ/CQ 权限、backpressure、sticky resync、retained-terminal 幂等 cancel、deadline | `submit=1 cq_ack=1 monotonic=1 resync=1` |

```bash
make agent-uapi-check
AGENT_TEST_CASE=agenttoolabi_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

## 实现索引

| 职责 | 源码 |
| --- | --- |
| UAPI 与用户态封装 | [`include/agent_tool_abi.h`](../../include/agent_tool_abi.h)、[`user/include/agent.h`](../../user/include/agent.h)、[`user/lib/syscall.c`](../../user/lib/syscall.c) |
| Nexus artifact 发布 | [`user/include/agent_nexus.h`](../../user/include/agent_nexus.h)、[`user/lib/agent_nexus.c`](../../user/lib/agent_nexus.c) |
| 工具目录与 typed schema | [`os/agent_tool_protocol.c`](../../os/agent_tool_protocol.c) |
| Owner dispatch 与 Context commit | [`os/agent_core.c`](../../os/agent_core.c)、[`os/agent_context.c`](../../os/agent_context.c) |
| Execution Contract | [`include/agent_execution_contract_abi.h`](../../include/agent_execution_contract_abi.h)、[`os/agent_execution_contract.c`](../../os/agent_execution_contract.c) |
| Provenance 与资源 | [`os/agent_provenance.c`](../../os/agent_provenance.c)、[`os/agent_resource.c`](../../os/agent_resource.c) |
| Task Channel 与 bridge | [`include/agent_task_channel_abi.h`](../../include/agent_task_channel_abi.h)、[`os/agent_task_channel.c`](../../os/agent_task_channel.c)、[`os/agent_task_bridge.c`](../../os/agent_task_bridge.c) |

公开结构和状态码见 [API](../api.md)，完整检查链见[安全机制](../security.md)。
