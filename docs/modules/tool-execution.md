# 结构化工具与执行合同

AgentOS 把工具发现、参数校验、授权、执行和结果发布放入同一条内核调用链。Agent 可以按任务形态使用逐次 typed 调用、冻结执行合同、紧凑批处理或 Task SQ/CQ。

## 工具目录

内核维护 25 项 name/id 唯一的工具目录。每个目录项声明参数 key、type、最大长度、必要性、required capability 和 side-effect mask。请求进入工具 owner 前，内核检查未知 key、重复参数、缺失参数、类型、字符串长度与终止符以及 `tool_id/tool_name` 一致性。

| 接口 | 数据结构 | 使用方式 |
| --- | --- | --- |
| V1 `agent_call()` | 固定长度 request/response | 兼容已有 Guest 程序 |
| V2 `tool_call()` | sized typed-KV | 根据上一轮结果动态选择工具 |
| V3 `tool_call_v3()` | V2 前缀与合同字段 | 执行冻结 DAG 中的节点 |
| `agent_run()` | 最多 64 项 op/result | 同步提交短小顺序操作 |
| Task Channel | 16 槽 SQ/CQ | 通过共享队列提交和收割任务 |

V1、V2 每次调用都会重新执行身份与授权检查。V2 单次 request 最多包含 8 个参数，参数类型包括 `uint64` 和有界 string。

## 请求处理链

```text
复制 request 与 descriptor
    -> 校验 version、size 和 flags
    -> 检查 Agent 身份与 lifecycle
    -> 解析 tool id/name 并校验参数 schema
    -> 进入 lifecycle operation gate
    -> 执行 contract admission 与 required capability 匹配
    -> 完成 provenance 授权并申请 phase credit
    -> 进入 effect gate，由工具 owner 继续检查 VFS scope
    -> 提交 Context 与执行记录
    -> 发布 typed status/result
```

检查完成后，执行器只使用内核私有 request 副本。所有路径共用工具 owner 与 Context commit lane，因而 scalar、batch 和 SQ/CQ 对同一操作产生一致的工具结果与记录顺序。

## ENFORCE V3

每个 lifecycle 可以冻结一份最多 24 个节点的有向无环合同。节点按拓扑顺序编号，predecessor 只能指向已有节点。

| 维度 | 合同字段 |
| --- | --- |
| 工具 | tool id、32 字节 schema digest、required capability、side-effect mask |
| 依赖 | predecessor mask、source node、source Context sequence |
| 数据 | input/output artifact type、来源标签、32 字节 input fingerprint |
| 资源 | exec/storage envelope、charge class |
| 控制 | deadline、最大 attempt、retry mask、cancel policy |

V3 请求绑定 contract key、node、attempt、deadline、source Context 和 input fingerprint。内核依次检查 lifecycle、节点、前驱、schema、capability、来源、资源与 deadline。工具开始前预留执行记录槽并建立 Tool Phase Credit Lease，完成后结算资源、提交 Context，再发布节点终态。

合同节点状态包括 `BLOCKED`、`READY`、`RUNNING`、`SUCCEEDED`、`FAILED` 和 `CANCELLED`。完成缓存保存 accepted attempt 的终态，合法重试直接返回同一结果并设置 cached 标志。

```c
int agent_execution_contract(
    const struct agent_execution_contract_control *control,
    struct agent_execution_contract_result *result);

int tool_call_v3(struct agent_request_v3 *req,
                 struct agent_response_v3 *resp);
```

## Tool Phase Credit Lease

Phase Lease 从 workflow 已计入资源中锁定本次工具需要的短期额度。claim 使用 nonce，失败路径 refund，成功路径按实际对象发布与析构结算。Lease 与执行合同使用相同的 workflow、node 和 attempt 身份，资源记账、工具终态和 Context 发布共享一个提交顺序。

## Compact batch

`agent_run()` 在一次 syscall 中同步执行最多 64 项 `agent_op`：

```c
int agent_run(struct agent_op *ops,
              struct agent_result *results,
              int count, uint64 flags);
```

batch 内操作按数组顺序提交，每项返回独立状态。wait/wake 类睡眠操作使用独立通道，避免等待请求占住整组 batch。16-op ECHO 负载中，compact batch 的中位 sequence 延迟为 `561 us`，结果见[性能测试](../performance.md)。

## Task SQ/CQ

每个 Agent 可以建立 single-issuer Task Channel，共使用 4 页：

| 页面 | 用户权限 | 内容 |
| --- | --- | --- |
| SQ | read/write | 16 个 128 字节 SQE 与 ring header |
| CQ | read-only | 16 个 128 字节 CQE 与 ring header |
| request private | 内核私有 | 权威水位、request 状态、issuer 与 deadline |
| resource private | 内核私有 | handle generation、owner、digest 与来源 |

内核消费 SQE 前复制完整描述符。request id 严格递增；channel generation 不匹配时返回 `STALE`，SQE 的 ring/slot generation 不匹配或出现协议故障时进入 sticky resync，issuer 通过显式 `RESYNC` 重建可见 header。

每个 accepted target 只发布一个 terminal CQE。cancel 使用独立 request id 引用目标；effect gate 前可以将目标转为 cancelled，effect 开始后按实际执行结果结算。hard deadline 在 timer 中标记，并在下一个 schedulable safe point 完成终态发布。

typed resource handle 固定为 16 字节，每个 Agent 的私有表包含 8 个槽位。当前内核 bridge 同步处理 `null` input，完成结果的 artifact 类型为 `NONE`。

## 实现位置

| 职责 | 源码 |
| --- | --- |
| 工具目录与 schema | [`agent_tool_abi.h`](../../agent_tool_abi.h)、[`os/agent_tool_protocol.c`](../../os/agent_tool_protocol.c) |
| syscall 与 owner dispatch | [`os/agent_core.c`](../../os/agent_core.c) |
| 执行合同 | [`agent_execution_contract_abi.h`](../../agent_execution_contract_abi.h)、[`os/agent_execution_contract.c`](../../os/agent_execution_contract.c) |
| 来源检查 | [`os/agent_provenance.c`](../../os/agent_provenance.c) |
| Task Channel | [`agent_task_channel_abi.h`](../../agent_task_channel_abi.h)、[`os/agent_task_channel.c`](../../os/agent_task_channel.c) |
| 用户态封装 | [`user/include/agent.h`](../../user/include/agent.h)、[`user/lib/syscall.c`](../../user/lib/syscall.c) |

## 测试入口

`agenttoolabi_ucore` 覆盖 V1/V2 工具目录和 typed-KV 负向矩阵；`agentcontract_ucore` 覆盖依赖、deadline、retry、cancel、来源与资源；`agenttask_ucore` 覆盖 SQ/CQ 所有权、resync、backpressure、terminal CQE 和 typed handle。

```bash
AGENT_TEST_CASE=agenttoolabi_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

公开结构、状态码和字段布局见 [API](../api.md)，副作用检查链见[安全机制](../security.md)。
