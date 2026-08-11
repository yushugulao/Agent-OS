# 任务二：结构化工具调用

## 场景与约束

Agent 需要发现工具、提交参数并根据结构化状态继续多轮工作。用户地址、工具名称、参数类型和返回缓冲区都来自不可信用户态；内核必须在执行副作用前完成复制、schema 校验、授权和资源准入。

## 方案

AgentOS 维护一份 name/id 唯一的工具目录，并提供四类入口：

| 接口 | 适用场景 | 关键合同 |
| --- | --- | --- |
| `agent_call()` / `agent_tool_list()` | 题面兼容 V1 | 固定布局，逐次检查 capability 与 scope |
| `tool_call()` / `tool_list()` | 探索式 V2 | sized typed-KV；值类型为 `uint64` 或有界 string |
| `tool_call_v3()` | 预声明高保证流程 | 保留 V2 前缀，绑定合同、节点、attempt、schema 和 predecessor Context |
| `agent_run()` | 紧凑顺序批处理 | 一次 syscall 最多 64 个 `agent_op`，按数组顺序同步执行 |

V2 参数按注册 key 匹配，再压平到共享执行器的 `arg0`、`arg1` 和 64 字节 payload。未知、重复、缺失、错类型、未终止字符串以及 id/name 冲突都会返回确定错误。用户地址或 copy 失败由 syscall 返回 `-1`；成功解析后的业务状态写入 response。

### 执行路径

```text
copy request and descriptors
        -> check version and exact size
        -> resolve tool id/name and schema
        -> check role, capability and VFS scope
        -> check resources and optional V3 contract
        -> execute owner implementation
        -> commit Context and Evidence
        -> publish typed status/result
```

`agent_run()` 共用工具 owner 和 Context commit lane。batch 中不接收会睡眠的 wait/wake 操作，因此一次等待不会钉住整组请求。V1/V2 适合逐轮选择工具；启用 ENFORCE 的 V3 才校验冻结 DAG、attempt、deadline、输入 fingerprint 和 predecessor。

## 关键实现

| 职责 | 源码 |
| --- | --- |
| 25 项工具目录与 schema | [agent_tool_abi.h](../../agent_tool_abi.h)、[os/agent_tool_protocol.c](../../os/agent_tool_protocol.c) |
| syscall、授权与 owner dispatch | [os/agent_core.c](../../os/agent_core.c) |
| Context 提交 | [os/agent_context.c](../../os/agent_context.c) |
| ENFORCE V3 | [agent_execution_contract_abi.h](../../agent_execution_contract_abi.h)、[os/agent_execution_contract.c](../../os/agent_execution_contract.c) |
| SQ/CQ 路径 | [agent_task_channel_abi.h](../../agent_task_channel_abi.h)、[os/agent_task_channel.c](../../os/agent_task_channel.c) |
| 用户接口 | [user/include/agent.h](../../user/include/agent.h) |

## 验证与量化

| 证据 | 覆盖范围 |
| --- | --- |
| `agenttoolabi_ucore` | V1/V2 目录、参数负向矩阵和缓冲区边界 |
| `agentsecurity_ucore` | 伪造 role、id/name 冲突和越权调用 |
| `agentfinal_ucore` | 64 项顺序 batch、Context sequence 和代表性工具 |
| `agentcontract_ucore` | V3 合同、依赖、deadline、retry/cancel 和 provenance 拒绝 |
| `agenttask_ucore` | batch、scalar V3 与 SQ/CQ 的 16-op 等价 ECHO 序列 |

2026-08-11 一次性活动保留了每条 transport 路径 32 个 16-op sequence。batch、scalar V3、SQ/CQ 的序列耗时中位数分别为 `561 us`、`2051 us` 和 `1620.5 us`。三条路径使用不同 wire 和复制范围，结论只比较等价空 ECHO 的整段 transport。原始数据和统计口径见 [高级性能图](advanced-performance-figures.md) 与 [validation.json](../../one_shot_metrics/data/20260811/validation.json)。

```bash
AGENT_TEST_CASE=agenttoolabi_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
```

## 当前边界

- 工具目录当前为 25 项；`agent_run()` 的上限为 64 项。
- compact batch 同步顺序执行，不提供并行或 non-blocking 语义。
- V1/V2 继续执行逐调用授权，不携带冻结 DAG、attempt 或 predecessor 保证。
- 单份 ENFORCE 合同最多 24 个节点、48 个 accepted attempt 终态槽。
- Task Channel provider 当前同步接收 null input 并返回 artifact `NONE`，业务 payload backend 尚未开放。
- 状态码可表达副作用发布后的不确定结果；调用者需要查询状态，内核不会把它改写为成功。
