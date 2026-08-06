# 任务二：结构化工具调用

任务二提供内核工具表、严格参数协议和批量执行路径。稳定结构定义以
`user/include/agent.h` 和 [api.md](api.md) 为准；本文不再复制工具数量、字段
偏移或模块清单。

## 接口分层

| 接口 | 定位 |
| --- | --- |
| `agent_run()` | 以 `tool_id` 批量执行非阻塞操作的热路径 |
| `tool_call()` / `tool_list()` | sized typed-KV V2 正式协议 |
| `agent_call()` / `agent_tool_list()` | 保持原题面布局的 V1 兼容协议 |

三条入口共享同一工具描述和 typed rule 表。系统调用层只复制和校验用户输入，
工具查找、授权、Context 提交及 metadata 操作分别由其 owner 模块处理；历史
`agent.c` 只保留薄兼容 facade。

## 协议不变量

- V2 请求、参数和描述符必须携带受支持的 version 与精确 size。
- 参数按注册 key 匹配；未知、重复、缺失、错类型和越界字符串全部拒绝。
- 同时给出工具 id 与名称时必须指向同一工具。
- 用户地址或 copy 失败由 syscall 返回 `-1`；可解析请求的业务结果写入
  `response.status`。
- V1 布局保持 byte-compatible，但不能绕过 V2 共用的工具规则与授权。
- 协议扩展发布新版本或明确的 sized-prefix 规则，不能在原版本下重解释字段。

`agent_run()` 在一次陷入中完成多个已校验操作，按顺序返回逐项结果，并通过同一
Context commit lane 发布 sequence、结果和因果记录。可能阻塞的 wait/wake 保持
独立 syscall，避免一个等待项钉住整个 batch。工具执行仍在 syscall 公平预算和
metadata 事务预算内，批量接口不是绕过调度或资源控制的后门。

## 错误语义

状态码区分无效请求、未知工具、权限不足、容量不足、超时、可重试、明确 I/O
失败、持久性失败与发布边界后的不确定结果。调用者可以据此选择修正参数、退避、
查询提交状态或终止 workflow，而不依赖内核日志文本。

## 验证

- `agenttoolabi_ucore` 对照 V1/V2 工具表并覆盖参数负向矩阵和缓冲区边界。
- `agentfinal_ucore` 验证批量顺序、Context 提交和代表性工具调用。
- `agentbench_ucore` 比较 scalar 与 batch 的真实 syscall 数和耗时。
- `agentsecurity_ucore` 验证伪造角色、工具 id/name 冲突和能力越界均失败。

正式运行入口见 [verification.md](verification.md)。
