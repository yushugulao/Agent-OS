# 任务二：Agent 与内核结构化交互

本文是 [design.md](design.md) 的任务二细节附录，重点展开结构化工具调用、工具表、错误语义和 Agent Context 写入路径。系统总体接口分工和 ABI 汇总见 [api.md](api.md)。

任务二的目标是在 Agent 进程机制基础上，提供 Agent 进程与内核之间的结构化工具调用接口。AgentOS-uCore 当前热路径使用 `agent_op` / `agent_result` 和 `agent_run()` 批量 ABI，一次 syscall 最多执行 64 个工具 op；`agent_request` / `agent_response` 作为赛题“工具名称 + 参数键值列表”的正式名称协议入口。

## 接口

| 接口 | 说明 |
| --- | --- |
| `agent_run(struct agent_op *, struct agent_result *, int, uint64)` | 最终高性能批量工具调用入口 |
| `agent_call(struct agent_request *, struct agent_response *)` | 名称协议结构化工具调用入口 |
| `agent_tool_list(struct agent_tool_desc *, int)` | Agent 工具列表查询入口 |

系统调用层只负责分发，Agent 相关逻辑集中在 `os/agent.c`。这样工具表、工具执行、Context 写入、文件属性查询、Agent Loop 和消息读写位于同一个模块，便于统一维护。

## 协议

最终热路径结构定义在 `os/agent.h` 和 `user/include/agent.h`：

| 结构 | 关键字段 |
| --- | --- |
| `struct agent_op` | `version`、`tool_id`、`request_id`、`arg0`、`arg1`、`flags`、`payload` |
| `struct agent_result` | `version`、`status`、`tool_id`、`request_id`、`sequence`、`value0`、`value1`、`value2`、`result` |

名称协议请求和响应结构：

| 结构 | 关键字段 |
| --- | --- |
| `struct agent_request` | `version`、`tool_id`、`tool_name`、`request_id`、`arg0_key`、`arg0_type`、`arg0`、`arg1_key`、`arg1_type`、`arg1`、`payload_key`、`payload_type`、`payload` |
| `struct agent_response` | `version`、`status`、`tool_id`、`tool_name`、`request_id`、`sequence`、`value0`、`value1`、`value2`、`result` |
| `struct agent_tool_desc` | `tool_id`、`flags`、`name`、`params`、`description` |

`agent_run()` 只走 `tool_id`，避免热路径字符串扫描。`agent_call()` 既可以通过 `tool_id` 选择工具，也可以通过 `tool_name` 选择工具；只传 `tool_name` 时，内核按工具表名称解析，并校验参数键和类型。

## 批量执行

`agent_run()` 的执行流程：

1. 检查当前进程是否为 Agent。
2. 检查 `count` 是否在 `1..AGENT_BATCH_MAX`。
3. 从用户态复制 `agent_op` 数组。
4. 逐条检查 version 和 tool_id。
5. 执行工具。
6. 为每条工具调用分配 sequence。
7. 写入对应 `agent_result`。
8. 将结果追加到 Context Path。
9. 同步 shadow Context 到用户镜像。

批量执行的性能收益来自：

- 减少 syscall 次数；
- 减少重复检查；
- 用 tool_id 直接定位工具；
- 批量写出结果。

## 内核工具

当前实现 25 个工具，任务二基础工具和任务四、五扩展工具共用同一套工具表：

| 工具 | `tool_id` | 输入 | 输出 |
| --- | ---: | --- | --- |
| `echo` | 1 | `payload`、`arg0`、`arg1` | 返回 payload 长度、两个数值参数和 payload 文本 |
| `pid_info` | 2 | 无 | 返回当前 pid、Agent ID 和 Agent 身份 |
| `ctx_stat` | 3 | 无 | 返回 Agent Context 起始地址、大小和当前调用次数 |
| `query_process` | 4 | 可选类型 | 返回进程数量、Agent 数量和可运行进程数量 |
| `get_system_status` | 5 | 无 | 返回进程数量、Agent 数量和系统 tick |
| `read_context` | 6 | 无 | 返回本次调用追加后的 Context Path 记录数、head 和总调用次数 |
| `query_file` | 7 | 路径或属性条件串 | 返回文件查询结果 |
| `send_message` | 8 | `target_pid`、message | 沿显式 `MESSAGE` route 向目标 Agent 发送短消息 |
| `read_message` | 9 | 无 | 读取当前 Agent 消息 |
| `file_meta_init` | 10 | 无 | 重新加载任务四文件对象元数据表 |
| `read_file_summary` | 11 | selector | 返回文件摘要 |
| `dependency_query` | 12 | label | 返回对象标签影响范围 |
| `capability_check` | 13 | legacy role、action | 按当前进程真实 capability 检查动作，并返回真实 role/capability |
| `rerun_stage` | 14 | legacy role、stage | 旧示例兼容；内部调用通用动作提交路径，记录和重复请求判断归入 `action_commit` |
| `write_report` | 15 | legacy role、payload | 旧示例兼容；内部调用通用工件更新路径，记录和重复请求判断归入 `artifact_update` |
| `agent_watch` | 16 | event_type、filter | 注册事件 watch |
| `agent_wait` | 17 | timeout | syscall-only 工具表可发现项；`agent_run()` 调用返回 `AGENT_STATUS_BAD_PARAM` |
| `agent_heartbeat` | 18 | interval | 设置或停止心跳，`interval=0` 表示停止 |
| `context_push` | 19 | record | 手动 Context 节点使用的内部工具 ID |
| `read_file_digest` | 20 | selector | 读取真实文件短预览和 FNV-1a 内容指纹；绑定 metadata 的真实文件可复用 digest cache |
| `action_commit` | 21 | selector | 按通用对象 selector 幂等提交 Agent 动作 |
| `artifact_update` | 22 | selector | 按通用对象 selector 更新工件、报告、记忆或结果对象状态 |
| `llm_request` | 23 | target_pid、prompt_summary | 记录请求摘要；target 非零时沿 `MESSAGE` route 投递，target 为零时只记录 |
| `llm_response` | 24 | target_pid、response_summary | 由具备 `LLM_RELAY` 的 Agent 沿显式 `LLM_DONE` route 投递结果事件 |
| `dependency_update` | 25 | selector | 注册或更新通用对象依赖关系 |

`MESSAGE_SEND` 和 `LLM_RELAY` 只决定调用者能否发起对应操作，不授予任意目标范围。跨 Agent 的 `send_message`、非零 target `llm_request` 和 `llm_response` 必须先解析 source/target 的不可复用 stable control id，确认两端属于同一 active workflow scope，再分别命中 target 入站表中的 `MESSAGE` 或 `LLM_DONE` route；target consent 也不能越过 scope。自投递隐式允许。`llm_request(target_pid=0, ...)` 只记录摘要，不执行投递。`agentsecurity_ucore` 已覆盖 `send_message` / 非零 target `llm_request` 的未授权拒绝、`MESSAGE` grant/revoke、target 自主接受 `LLM_DONE`，并验证 LLM-only route 拒绝 `MESSAGE`；`agentllm_ucore` 提供 `LLM_DONE` route 的端到端正向回归。尚未由具备 `LLM_RELAY` 的 source 专项验证无 `LLM_DONE` 位时的响应拒绝。

## 错误处理

当前覆盖的错误状态：

| 状态 | 说明 |
| --- | --- |
| `AGENT_STATUS_NOT_AGENT` | 普通进程调用 Agent 工具入口 |
| `AGENT_STATUS_BAD_REQUEST` | 请求版本错误，或请求结构不一致 |
| `AGENT_STATUS_UNKNOWN_TOOL` | 工具不存在 |
| `AGENT_STATUS_BAD_PARAM` | 参数或必要字段不符合工具要求 |
| `AGENT_STATUS_NOT_FOUND` | 查询文件或目标 Agent 不存在 |
| `AGENT_STATUS_NO_SPACE` | Agent Context、IPC route 表、事件 source/class/external/总量配额或同步路径不可用 |
| `AGENT_STATUS_DENIED` | 权限检查拒绝 |
| `AGENT_STATUS_DUPLICATE` | 重复幂等动作被识别 |
| `AGENT_STATUS_CANCELLED` | Agent 等待被受权 Agent 取消 |

最终功能验收程序 `agentfinal_ucore` 会覆盖批量工具调用、sequence 连续性、Context 写入、Context Snapshot、通用 `action_commit/artifact_update` 和基础 template LLM 调用，并用 name-only `agent_call()` 验证 `echo`、`query_file`、`pid_info`、`read_file_digest`、`dependency_update` 和 `dependency_query`。`agentllm_ucore` 专门覆盖请求 Agent 与 Relay Agent 之间的 LLM 请求、模板响应、事件唤醒、Context 和 timeline 记录。`labdemo_ucore` 覆盖 denied 和 duplicate 两类业务错误。`agentsecurity_ucore` 专门覆盖用户态伪造 role 仍被内核真实 capability 拒绝的负向路径，并覆盖 `agent_call()` 中 `tool_id` 和 `tool_name` 不一致时返回 `AGENT_STATUS_BAD_REQUEST` / `tool_mismatch`。

## 上下文写入：Agent Context

每次 Agent 调用结束后，内核会把最新 `struct agent_result` 写入 Agent Context，同时追加一条 `struct agent_context_record` 到 Context Path 环形记录区。legacy `struct agent_response` 只作为 `agent_call()` 的返回结构，不直接写入 Context latest 区。

写入路径：

1. 工具执行得到 `agent_result`。
2. 内核分配新的 sequence。
3. record 写入当前 cause/span；首条记录 cause 为 0，后续记录默认指向上一条 sequence。
4. latest result 写入 shadow。
5. record 写入 shadow record 区。
6. header 元信息更新，包括当前 cause/span 和 provenance edge 计数。
7. 用户镜像同步。

工具触发的事件也会携带 cause/span。目标 Agent 消费事件后继承 span，后续工具调用继续同一链路。这让结构化工具调用不仅能返回结果，还能为多 Agent 协作提供前后关系。

## 与任务四、五的关系

任务四文件查询和任务五 Agent Loop 都复用任务二工具调用机制：

- 文件属性查询可以作为 `AGENT_TOOL_QUERY_FILE` 执行；
- 文件摘要和对象标签依赖查询作为工具执行；
- 权限检查、通用动作提交、通用工件更新和 LLM 请求/响应作为工具执行；
- watch、heartbeat 和 `context_push` 可以通过工具表发现；
- `agent_wait` 只允许通过 `agent_wait()` syscall 执行，避免在批量热路径中阻塞整个 batch；
- wait/wake 使用独立 syscall，因为 wait 可能阻塞，不适合作为 batch 热路径。

## 验证证据

原始串口输出统一保存在 [test-record.md](test-record.md)，逐项测试步骤见 [testing-details.md](testing-details.md)。本任务文档只保留任务二相关检查点：

| 程序 | 检查点 |
| --- | --- |
| `agentfinal_ucore` | 批量工具调用 sequence 连续；短 payload/result 写入 Context；通用 action ABI、LLM 模板 relay 和按名称调用协议均可用。 |
| `agentllm_ucore` | requester/relay/response 路径可跑通，LLM 请求以结构化工具调用进入 Context、timeline 和 audit。 |
| `agentbench_ucore` | scalar 与 batch 两条路径均输出多轮 tick 统计，batch 路径体现减少 syscall 次数后的吞吐优势。 |
| `labdemo_ucore` | 通用 action、事件和 audit 能在科研示例负载中组合使用，权限拒绝和重复请求都有结构化记录。 |
| `agentsecurity_ucore` | 用户态伪造 role 不生效；旧工具名/ID 不一致会失败；错误参数键或类型按结构化错误返回。 |
