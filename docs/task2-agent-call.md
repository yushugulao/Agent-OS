<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 任务二：Agent 与内核结构化交互

本文是 [design.md](design.md) 的任务二细节附录，重点展开结构化工具调用、工具表、错误语义和 Agent Context 写入路径。系统总体接口和职责划分、ABI 汇总见 [api.md](api.md)。

任务二的目标是在 Agent 进程机制基础上，提供 Agent 进程与内核之间的结构化工具调用接口。最终热路径使用 `agent_op` / `agent_result` 和 `agent_run()` 批量 ABI，一次 syscall 最多执行 64 个工具 op；legacy `agent_request` / `agent_response` 仍保留作语义兼容。

## 接口

| 接口 | 说明 |
| --- | --- |
| `agent_call(struct agent_request *, struct agent_response *)` | Agent 专用结构化工具调用入口 |
| `tool_call(struct agent_request *, struct agent_response *)` | 与赛题 `sys_tool_call` 对应的别名入口 |
| `agent_tool_list(struct agent_tool_desc *, int)` | Agent 工具列表查询入口 |
| `tool_list(struct agent_tool_desc *, int)` | 与赛题 `sys_tool_list` 对应的别名入口 |
| `agent_run(struct agent_op *, struct agent_result *, int, uint64)` | 最终高性能批量工具调用入口 |

系统调用层只负责读取参数并转发，Agent 相关逻辑集中在 `kernel/agent.c`。这样工具表、工具执行、Context 写入、文件属性查询、Agent Loop 和 mailbox 读写位于同一个模块，避免 `sysproc.c` 膨胀。

## 协议

最终热路径结构定义在 `kernel/agent.h`：

| 结构 | 关键字段 |
| --- | --- |
| `struct agent_op` | `version`、`tool_id`、`request_id`、`arg0`、`arg1`、`flags`、`payload` |
| `struct agent_result` | `version`、`status`、`tool_id`、`request_id`、`sequence`、`value0`、`value1`、`value2`、`result` |

legacy 请求和响应结构仍定义在 `kernel/agent.h`：

| 结构 | 关键字段 |
| --- | --- |
| `struct agent_request` | `version`、`tool_id`、`tool_name`、`request_id`、`arg0_key`、`arg0_type`、`arg0`、`arg1_key`、`arg1_type`、`arg1`、`payload_key`、`payload_type`、`payload` |
| `struct agent_response` | `version`、`status`、`tool_id`、`tool_name`、`request_id`、`sequence`、`value0`、`value1`、`value2`、`result` |
| `struct agent_tool_desc` | `tool_id`、`name`、`params`、`description` |

`agent_run()` 只走 `tool_id`，避免热路径字符串扫描。legacy 请求既可以通过 `tool_id` 选择工具，也可以通过 `tool_name` 选择工具。若两者同时提供但不匹配，内核返回 `AGENT_STATUS_BAD_REQUEST`。

工具 ID 查询使用顺序 ID 直接索引，工具名查询保留字符串扫描以满足赛题“工具名称”要求。请求同时包含 ID 和名称时，内核优先用 ID 定位工具，再校验名称，避免重复扫描工具表。legacy `tool_call()` 还会按工具表校验参数键名和参数类型，例如 `query_file` 必须使用 `payload_key="path"` 和字符串 payload，`send_message` 必须使用 `target_pid:uint64` 和 `message:string`。

## 内核工具

当前实现 18 个工具，任务二基础工具和任务四、五扩展工具共用同一套工具表：

| 工具 | `tool_id` | 输入 | 输出 |
| --- | --- | --- | --- |
| `echo` | `AGENT_TOOL_ECHO` | `payload:string`、`arg0:uint64`、`arg1:uint64` | 返回 payload 长度、两个数值参数和 payload 文本 |
| `pid_info` | `AGENT_TOOL_PID_INFO` | 无 | 返回当前 pid、Agent ID 和 Agent 身份 |
| `ctx_stat` | `AGENT_TOOL_CTX_STAT` | 无 | 返回 Agent Context 起始地址、大小和当前调用次数 |
| `query_process` | `AGENT_TOOL_QUERY_PROCESS` | 可选 `type:uint64` | 返回进程数量、Agent 数量和可运行进程数量 |
| `get_system_status` | `AGENT_TOOL_GET_SYSTEM_STATUS` | 无 | 返回进程数量、Agent 数量和系统 tick |
| `read_context` | `AGENT_TOOL_READ_CONTEXT` | 无 | 返回本次调用追加后的 Context Path 记录数、head 和总调用次数 |
| `query_file` | `AGENT_TOOL_QUERY_FILE` | `path:string`、`fid:uint64` 或 `key=value` filters | 兼容路径查询；属性查询返回命中数、扫描数、索引使用情况和首个命中文件 |
| `send_message` | `AGENT_TOOL_SEND_MESSAGE` | `target_pid:uint64`、`message:string` | 向目标 Agent 邮箱写入消息 |
| `read_message` | `AGENT_TOOL_READ_MESSAGE` | 无 | 读取当前 Agent 邮箱消息 |
| `file_meta_init` | `AGENT_TOOL_FILE_META_INIT` | 无 | 初始化任务四文件元数据表 |
| `read_file_summary` | `AGENT_TOOL_READ_FILE_SUMMARY` | `selector:string` | 返回文件摘要 |
| `dependency_query` | `AGENT_TOOL_DEPENDENCY_QUERY` | `stage:string` | 返回阶段影响范围 |
| `capability_check` | `AGENT_TOOL_CAPABILITY_CHECK` | `action:string` | 检查当前 Agent PCB 中的 capability |
| `rerun_stage` | `AGENT_TOOL_RERUN_STAGE` | `stage:string` | 受控恢复动作 |
| `write_report` | `AGENT_TOOL_WRITE_REPORT` | `payload:string` | 只更新恢复报告工件的内存元数据状态 |
| `agent_watch` | `AGENT_TOOL_AGENT_WATCH` | `event_type:uint64`、`filter:string` | 注册事件 watch |
| `agent_wait` | `AGENT_TOOL_AGENT_WAIT` | `timeout:uint64` | 工具表可发现项；结构化事件返回使用 syscall |
| `agent_heartbeat` | `AGENT_TOOL_AGENT_HEARTBEAT` | `interval:uint64` | 设置心跳 |

## 错误处理

当前覆盖的错误状态：

| 状态 | 说明 |
| --- | --- |
| `AGENT_STATUS_NOT_AGENT` | 普通进程调用 Agent 工具入口 |
| `AGENT_STATUS_BAD_REQUEST` | 请求版本错误，或工具 ID 与工具名称不匹配 |
| `AGENT_STATUS_UNKNOWN_TOOL` | 工具不存在 |
| `AGENT_STATUS_BAD_PARAM` | 参数键、参数类型或必要参数不符合工具要求 |
| `AGENT_STATUS_NOT_FOUND` | 查询文件或目标 Agent 不存在 |
| `AGENT_STATUS_NO_SPACE` | Agent Context 布局、shadow 记录区或同步路径不可用 |

最终功能验收程序 `agentfinal` 会覆盖批量工具调用、sequence 连续性、Context 写入和 Context Snapshot。legacy 错误路径仍通过状态码表达，但不再作为最终性能成品的主验收入口。

坏输出指针采用执行前预检语义。`agent_run()` 会先确认全部 result slot 可写；legacy `tool_call()` 会先确认 `struct agent_response *` 可写。预检会对合法 lazy `sbrk` 输出页调用内核缺页处理进行 prefault，因此未提前触碰但位于合法堆范围内的输出页可以成功使用。只读页、越界页或无法映射的坏输出指针返回 `-1`，不执行工具、不递增 sequence、不追加 Context，也不会产生 mailbox 写入等副作用。

`read_context` 工具返回同一时间点的 post-state：本次工具调用先获得 sequence，再把这次调用计入即将写入的 Context Path 统计，因此返回的 count、head 和 total calls 与调用追加后的状态一致。legacy `tool_call()` 如果工具已成功执行但 Context 布局不可用，会返回结构化 `AGENT_STATUS_NO_SPACE` 响应，而不是把普通坏用户指针和内部空间错误混在一起。

`query_file` 现在同时保留两种语义：payload 是普通路径时走 xv6 inode 查询兼容路径；payload 是 `key=value` 条件串时走任务四文件属性查询引擎，返回 hits、scanned、used_index/truncated 和首个命中文件。属性查询支持 `fid=...` 精确回查；空查询、未知 key、空 value 或坏格式片段返回 `AGENT_STATUS_BAD_PARAM`。

## Agent Context 写入

每次 Agent 调用结束后，内核会把最新 `struct agent_result` 写入 Agent Context，同时追加一条 `struct agent_context_record` 到 Context Path 环形记录区。legacy `struct agent_response` 只作为 `tool_call()` 的返回结构，不直接写入 Context latest 区。这样 Agent 可以通过 syscall 读取本次响应，也可以直接从 Agent Context 读取最近响应和多轮调用历史。

当前写入路径不再对自身 Agent Context 使用 `copyout`。内核通过 `agent_shadow_kva[4]` 写入权威 Context 页，再同步到 `agent_ctx_kva[4]` 用户镜像页；批量 op 结束后同步 header，record 和 latest result 在每个 op 完成时同步。`send_message` 和 `read_message` 均在访问 mailbox 时持有目标进程锁，避免多核 QEMU 下的读写竞态。
