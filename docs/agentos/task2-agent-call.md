# 任务二：结构化工具调用

任务二提供内核工具目录、严格参数协议和批量执行路径。稳定结构定义以
`user/include/agent.h` 和 [api.md](api.md) 为准；本文不再复制工具数量、字段
偏移或模块清单。

## 接口分层

| 接口 | 定位与边界 |
| --- | --- |
| `agent_run()` | compact `agent_op` 数组；一次 syscall 最多提交 64 项，内核按数组顺序同步执行，不是并行或 non-blocking runtime |
| `tool_call()` / `tool_list()` | sized typed-KV V2；参数类型只有 `uint64` 与有界 string，解码后落到 `arg0/arg1/64-byte payload` |
| `tool_call_v3()` | 保留 V2 prefix；仅接受已冻结的 `AGENT_EXECUTION_CONTRACT_F_ENFORCE` contract，并绑定 node/attempt/schema/predecessor |
| `agent_call()` / `agent_tool_list()` | 保持原题面布局的 V1 兼容协议；不获得 V3 execution-contract 保证 |

这些入口共享工具目录、capability 检查和最终 owner 实现，但不共享同一种 wire
format：`agent_run()` 不是 typed-KV batch，V1/V2 也不携带 V3 contract 字段。
系统调用层复制并校验各自的输入形状，工具执行、Context 提交及 metadata 操作
仍由对应 owner 模块处理。

## 协议不变量

- V2 请求、参数和描述符必须携带受支持的 version 与精确 size。
- 参数按注册 key 匹配；未知、重复、缺失、错类型和越界字符串全部拒绝。
- 同时给出工具 id 与名称时必须指向同一工具。
- 用户地址或 copy 失败由 syscall 返回 `-1`；可解析请求的业务结果写入
  `response.status`。
- V1 布局保持 byte-compatible；它仍受工具存在性、角色/capability、scope 和资源准入检查，但不声明 V3 的 DAG/attempt/deadline/provenance envelope。
- 协议扩展发布新版本或明确的 sized-prefix 规则，不能在原版本下重解释字段。

V1/V2 在当前 lifecycle 没有启用 enforced contract 时走 legacy admission。V2
适合探索式 model-led loop：模型可在每一轮根据上轮结果选择下一个 typed call，
但内核只验证该次请求的 schema、capability、scope 与资源边界，不预先证明整条
计划。只有 V3 加 enforced immutable contract 才提供 node/attempt/dependency、
deadline、schema digest、input fingerprint 与 predecessor Context 的高保证包络。

`agent_run()` 在一次陷入中顺序完成多个 compact 操作，逐项返回结果，并通过同一
Context commit lane 发布 sequence、结果和因果记录。可能睡眠的 wait/wake 不在
batch 中，避免一个等待项钉住整个数组；这不等于 batch 本身异步或非阻塞。工具
执行仍在 syscall 公平预算和 metadata 事务预算内。

## 错误语义

状态码区分无效请求、未知工具、权限不足、容量不足、超时、可重试、明确 I/O
失败与发布边界后的不确定结果。调用者可以据此选择修正参数、退避、
查询提交状态或终止 workflow，而不依赖内核日志文本。

## 模型工具循环的分工

[Anthropic tool-use 文档](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)
把 client tool loop 描述为“模型产生结构化 tool request，应用执行并回送
tool result”。[Claude Code 的 agentic loop](https://code.claude.com/docs/en/how-claude-code-works)
也是模型选择工具、执行环境采取动作并把结果反馈给下一轮。AgentOS-uCore 只借鉴
这种控制分工：模型决策在 Guest 用户态，内核 RPC 验证并执行本地工具；这不表示
项目嵌入 Claude Code、Anthropic SDK 或任何特定模型运行时。

## 验证

- `agenttoolabi_ucore` 对照 V1/V2 工具表并覆盖参数负向矩阵和缓冲区边界。
- `agentfinal_ucore` 验证批量顺序、Context 提交和代表性工具调用。
- `agentbench_ucore` 比较 scalar 与 batch 的 Guest 调用点次数和实际 tick；调用点次数不是内核路径 syscall counter。
- `agenttask_ucore` 让 batch、scalar V3、SQ/CQ 以不同 wire 各执行 16 次空 `ECHO`；`1/16/2` 只表示 Guest 入口调用次数，不是内核路径 syscall counter。
- `agentlive_ucore` 发现 rich tool overlay，在默认 6 轮同串口 replay 中验证 V2 exploratory 调用、错误建议拒绝和真实 result 回灌；入口是 `make agent-live-demo`，Windows xPack 可追加 `TOOLPREFIX=riscv-none-elf-`。
- `agentsecurity_ucore` 验证伪造角色、工具 id/name 冲突和能力越界均失败。

正式运行入口见 [verification.md](verification.md)。
