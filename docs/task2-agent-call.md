# 任务二：Agent 与内核结构化交互

本文是 [design.md](design.md) 的任务二细节附录，重点展开结构化工具调用、工具表、错误语义和 Agent Context 写入路径。系统总体接口分工和 ABI 汇总见 [api.md](api.md)。

任务二的目标是在 Agent 进程机制基础上，提供 Agent 进程与内核之间的结构化工具调用接口。uCore 分支的最终热路径使用 `agent_op` / `agent_result` 和 `agent_run()` 批量 ABI，一次 syscall 最多执行 64 个工具 op；legacy `agent_request` / `agent_response` 仍保留作语义兼容。

## 接口

| 接口 | 说明 |
| --- | --- |
| `agent_run(struct agent_op *, struct agent_result *, int, uint64)` | 最终高性能批量工具调用入口 |
| `agent_call(struct agent_request *, struct agent_response *)` | legacy 结构化工具调用入口 |
| `agent_tool_list(struct agent_tool_desc *, int)` | Agent 工具列表查询入口 |

系统调用层只负责分发，Agent 相关逻辑集中在 `os/agent.c`。这样工具表、工具执行、Context 写入、文件属性查询、Agent Loop 和消息读写位于同一个模块，便于统一维护。

## 协议

最终热路径结构定义在 `os/agent.h` 和 `user/include/agent.h`：

| 结构 | 关键字段 |
| --- | --- |
| `struct agent_op` | `version`、`tool_id`、`request_id`、`arg0`、`arg1`、`flags`、`payload` |
| `struct agent_result` | `version`、`status`、`tool_id`、`request_id`、`sequence`、`value0`、`value1`、`value2`、`result` |

legacy 请求和响应结构：

| 结构 | 关键字段 |
| --- | --- |
| `struct agent_request` | `version`、`tool_id`、`tool_name`、`request_id`、`arg0_key`、`arg0_type`、`arg0`、`arg1_key`、`arg1_type`、`arg1`、`payload_key`、`payload_type`、`payload` |
| `struct agent_response` | `version`、`status`、`tool_id`、`tool_name`、`request_id`、`sequence`、`value0`、`value1`、`value2`、`result` |
| `struct agent_tool_desc` | `tool_id`、`name`、`params`、`description` |

`agent_run()` 只走 `tool_id`，避免热路径字符串扫描。legacy 请求既可以通过 `tool_id` 选择工具，也可以通过 `tool_name` 选择工具。

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

当前实现 18 个工具，任务二基础工具和任务四、五扩展工具共用同一套工具表：

| 工具 | `tool_id` | 输入 | 输出 |
| --- | ---: | --- | --- |
| `echo` | 1 | `payload`、`arg0`、`arg1` | 返回 payload 长度、两个数值参数和 payload 文本 |
| `pid_info` | 2 | 无 | 返回当前 pid、Agent ID 和 Agent 身份 |
| `ctx_stat` | 3 | 无 | 返回 Agent Context 起始地址、大小和当前调用次数 |
| `query_process` | 4 | 可选类型 | 返回进程数量、Agent 数量和可运行进程数量 |
| `get_system_status` | 5 | 无 | 返回进程数量、Agent 数量和系统 tick |
| `read_context` | 6 | 无 | 返回本次调用追加后的 Context Path 记录数、head 和总调用次数 |
| `query_file` | 7 | 路径或属性条件串 | 返回文件查询结果 |
| `send_message` | 8 | `target_pid`、message | 向目标 Agent 发送短消息 |
| `read_message` | 9 | 无 | 读取当前 Agent 消息 |
| `file_meta_init` | 10 | 无 | 初始化任务四文件元数据表 |
| `read_file_summary` | 11 | selector | 返回文件摘要 |
| `dependency_query` | 12 | stage | 返回阶段影响范围 |
| `capability_check` | 13 | legacy role、action | 按当前进程真实 capability 检查动作，并返回真实 role/capability |
| `rerun_stage` | 14 | legacy role、stage | 只有具备 `RECOVER_STAGE` 的 Agent 可执行受控恢复动作 |
| `write_report` | 15 | legacy role、payload | 只有具备 `REPORT_WRITE` 的 Agent 可写报告状态；支持 `stage=report;run_id=...;project=...` selector |
| `agent_watch` | 16 | event_type、filter | 注册事件 watch |
| `agent_wait` | 17 | timeout | 工具表可发现项；实际等待用 syscall |
| `agent_heartbeat` | 18 | interval | 设置心跳 |

## 错误处理

当前覆盖的错误状态：

| 状态 | 说明 |
| --- | --- |
| `AGENT_STATUS_NOT_AGENT` | 普通进程调用 Agent 工具入口 |
| `AGENT_STATUS_BAD_REQUEST` | 请求版本错误，或请求结构不一致 |
| `AGENT_STATUS_UNKNOWN_TOOL` | 工具不存在 |
| `AGENT_STATUS_BAD_PARAM` | 参数或必要字段不符合工具要求 |
| `AGENT_STATUS_NOT_FOUND` | 查询文件或目标 Agent 不存在 |
| `AGENT_STATUS_NO_SPACE` | Agent Context、事件槽或同步路径不可用 |
| `AGENT_STATUS_DENIED` | 权限检查拒绝 |
| `AGENT_STATUS_DUPLICATE` | 重复恢复动作被识别 |

最终功能验收程序 `agentfinal_ucore` 会覆盖批量工具调用、sequence 连续性、Context 写入和 Context Snapshot。`labdemo_ucore` 覆盖 denied 和 duplicate 两类业务错误。`agentsecurity_ucore` 专门覆盖用户态伪造 role 仍被内核真实 capability 拒绝的负向路径，并覆盖 legacy `agent_call()` 中 `tool_id` 和 `tool_name` 不一致时返回 `AGENT_STATUS_BAD_REQUEST` / `tool_mismatch`。

## Agent Context 写入

每次 Agent 调用结束后，内核会把最新 `struct agent_result` 写入 Agent Context，同时追加一条 `struct agent_context_record` 到 Context Path 环形记录区。legacy `struct agent_response` 只作为 `agent_call()` 的返回结构，不直接写入 Context latest 区。

写入路径：

1. 工具执行得到 `agent_result`。
2. 内核分配新的 sequence。
3. latest result 写入 shadow。
4. record 写入 shadow record 区。
5. header 元信息更新。
6. 用户镜像同步。

## 与任务四、五的关系

任务四文件查询和任务五 Agent Loop 都复用任务二工具调用机制：

- 文件属性查询可以作为 `AGENT_TOOL_QUERY_FILE` 执行；
- 文件摘要和依赖查询作为工具执行；
- 权限检查和恢复动作作为工具执行；
- watch 和 heartbeat 可以通过工具表发现；
- wait/wake 使用独立 syscall，因为 wait 可能阻塞，不适合作为 batch 热路径。

## 验证证据

`agentfinal_ucore`：

```text
agentfinal_ucore: batch first_seq=1 last_seq=64
agentfinal_ucore: short_text_history=1 payload=ucore-final result=ucore-final
agentfinal_ucore: passed
```

`agentbench_ucore`：

```text
agentbench_ucore: scalar_agent_run ops=256 ticks=4 ops_per_tick=64 speedup_x100=100
agentbench_ucore: batch_agent_run ops=256 ticks=2 ops_per_tick=128 speedup_x100=200
```

`labdemo_ucore`：

```text
agentos:event type=AUDIT role=sentinel action=rerun_stage result=DENIED
agentos:event type=AUDIT role=recovery action=rerun_align result=DUPLICATE
```

`agentsecurity_ucore`：

```text
agentsecurity_ucore: sentinel spoof_denied=1
agentsecurity_ucore: recovery rerun_ok=1 duplicate=1
agentsecurity_ucore: legacy_tool_mismatch=1
```
