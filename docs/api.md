# Agent-OS API 与 ABI

本文档描述用户态程序与 Agent-OS 内核扩展之间的稳定边界。结构体和常量定义以 `kernel/agent.h` 为准，用户态声明以 `user/user.h` 为准。

## 系统调用

| syscall | 编号 | 用户态原型 | 说明 |
| --- | ---: | --- | --- |
| `agent_fork` | 22 | `int agent_fork(void)` | fork 子进程并标记为 Agent |
| `agent_info` | 23 | `int agent_info(struct agent_info *)` | 查询当前进程 Agent 元信息 |
| `agent_call` | 24 | `int agent_call(struct agent_request *, struct agent_response *)` | Agent 结构化工具调用 |
| `agent_tool_list` | 25 | `int agent_tool_list(struct agent_tool_desc *, int)` | 查询工具列表 |
| `tool_call` | 26 | `int tool_call(struct agent_request *, struct agent_response *)` | 赛题命名兼容入口，等价于 `agent_call` |
| `tool_list` | 27 | `int tool_list(struct agent_tool_desc *, int)` | 赛题命名兼容入口，等价于 `agent_tool_list` |
| `agent_create` | 28 | `int agent_create(void)` | 赛题命名兼容入口，当前 fork 风格创建 Agent |
| `context_push` | 29 | `int context_push(struct agent_context_record *)` | 手动追加 Context Path 节点 |
| `context_query` | 30 | `int context_query(uint64, struct agent_context_record *, int)` | 按 sequence 查询可见 Context Path |
| `context_rollback` | 31 | `int context_rollback(uint64)` | 回滚到仍可见的历史节点 |
| `context_clear` | 32 | `int context_clear(void)` | 清空当前 Agent Context Path |
| `agent_run` | 33 | `int agent_run(struct agent_op *, struct agent_result *, int, uint64)` | 高性能批量工具调用入口 |
| `context_snapshot` | 34 | `int context_snapshot(struct agent_context_header *, struct agent_context_record *, int)` | 批量返回 header 和可见 Context Path |

`agent_call`、`tool_call`、`context_query` 保留为 legacy 兼容入口。最终成品验收使用 `agent_run` 和 `context_snapshot`。

## Agent Context ABI

| 项目 | 值 |
| --- | --- |
| 起始地址 | `AGENT_CONTEXT_BASE = TRAPFRAME - AGENT_CONTEXT_SIZE` |
| 大小 | `AGENT_CONTEXT_SIZE = 4 * PGSIZE` |
| 当前大小 | 16384 字节 |
| 记录容量 | `AGENT_CONTEXT_MAX_RECORDS = 128` |
| 版本 | `AGENT_CONTEXT_VERSION = 1` |
| 权限 | 用户态镜像可读写，不可执行；内核 shadow 副本不可被用户态访问 |

布局：

| 偏移 | 内容 |
| --- | --- |
| `0` | `struct agent_context_header` |
| `sizeof(struct agent_context_header)` | `struct agent_result` |
| `AGENT_CONTEXT_RECORDS_OFFSET = 4096` | `struct agent_context_record[128]` |

内核在 `struct proc` 中同时保存 4 个用户镜像页地址和 4 个内核私有 shadow 页地址。header、latest result 和 record 的权威数据先写入 shadow 页，再同步到用户镜像页；用户态直接写镜像页不会改变 `context_query()` 或 `context_snapshot()` 返回的权威历史。直接读 Context 镜像是可信 Agent 自身的高速缓存路径；如果需要可信历史，应使用 `context_snapshot()` 刷新并读取 shadow 权威数据。

## 高性能请求结构

`struct agent_op` 是最终热路径 ABI：

| 字段 | 说明 |
| --- | --- |
| `version` | 必须为 `AGENT_CALL_VERSION` |
| `tool_id` | 工具 ID，固定走 O(1) 表索引 |
| `request_id` | 用户态请求 ID |
| `arg0`/`arg1` | 两个数值参数槽 |
| `flags` | 预留标志位 |
| `payload` | 64 字节短文本参数，与 legacy payload 对齐 |

`struct agent_result` 是对应结果：

| 字段 | 说明 |
| --- | --- |
| `version`/`status`/`tool_id` | 版本、状态和工具 ID |
| `request_id`/`sequence` | 请求 ID 和内核分配的 Agent 调用序号 |
| `value0`/`value1`/`value2` | 工具返回的三个数值槽 |
| `result` | 64 字节短文本结果 |

`agent_run()` 一次最多执行 `AGENT_BATCH_MAX = 64` 个 op。单个 op 的工具错误写入对应 result；用户指针错误、count 非法或非 Agent 调用返回 `-1`。内核执行 batch 前会先检查所有 op 可读、所有 result slot 可写；合法 lazy sbrk 输出页会在预检阶段 prefault，非法或只读输出页失败且不会执行工具、追加 Context 或产生 mailbox 副作用。

## Legacy 请求结构

`struct agent_request` / `struct agent_response` 仍保留，用于历史程序和语义兼容；最终性能测试不再使用该热路径。`struct agent_request` 的关键字段：

| 字段 | 说明 |
| --- | --- |
| `version` | 必须为 `AGENT_CALL_VERSION` |
| `tool_id` | 工具 ID；非 0 时可走 O(1) 索引 |
| `tool_name` | 工具名称；用于兼容按名称调用 |
| `request_id` | 用户态请求 ID，内核原样带回 |
| `arg0_key`/`arg1_key` | 参数键名 |
| `arg0_type`/`arg1_type` | 参数类型 |
| `arg0`/`arg1` | 数值参数 |
| `payload_key` | 字符串参数键名 |
| `payload_type` | 字符串参数类型 |
| `payload` | 短字符串 payload |

当 `tool_id` 和 `tool_name` 同时提供时，内核先用 ID 定位工具，再校验名称是否匹配。

legacy `tool_call()` 会按工具表校验参数键名和类型。键名或类型错误返回 `AGENT_STATUS_BAD_PARAM`，并写入可读结果文本，例如 `bad_payload_key`、`bad_payload_type`、`bad_arg0_key`、`bad_arg0_type` 或 `unexpected_param`。内核执行工具前会先检查 `struct agent_response *` 可写；合法 lazy sbrk response 页会被 prefault，坏输出指针返回 `-1`，且不递增 sequence、不写 Context、不执行工具。

## Legacy 响应结构

`struct agent_response` 的关键字段：

| 字段 | 说明 |
| --- | --- |
| `version` | 响应版本 |
| `status` | 状态码 |
| `tool_id`/`tool_name` | 实际执行或解析到的工具 |
| `request_id` | 对应请求 ID |
| `sequence` | 当前 Agent 的调用序号 |
| `value0`/`value1`/`value2` | 工具返回的数值槽 |
| `result` | 工具返回的短文本结果 |

## 错误码

| 错误码 | 值 | 说明 |
| --- | ---: | --- |
| `AGENT_STATUS_OK` | 0 | 成功 |
| `AGENT_STATUS_BAD_REQUEST` | -1 | 版本错误或请求结构不一致 |
| `AGENT_STATUS_UNKNOWN_TOOL` | -2 | 工具不存在 |
| `AGENT_STATUS_NOT_AGENT` | -3 | 普通进程调用 Agent-only 接口 |
| `AGENT_STATUS_BAD_PARAM` | -4 | 参数键、类型或必要参数错误 |
| `AGENT_STATUS_NOT_FOUND` | -5 | 文件、Agent 或历史节点不存在 |
| `AGENT_STATUS_NO_SPACE` | -6 | Context 空间或布局不可用 |

## 内核工具表

| ID | 名称 | 参数 | 返回 |
| ---: | --- | --- | --- |
| 1 | `echo` | `payload:string,arg0:uint64,arg1:uint64` | payload 长度、两个数值参数、payload 文本 |
| 2 | `pid_info` | `none` | 当前 pid、Agent ID、Agent 身份 |
| 3 | `ctx_stat` | `none` | Context base、size、调用次数 |
| 4 | `query_process` | `type:uint64` | 进程数量、Agent 数量、runnable 数量 |
| 5 | `get_system_status` | `none` | 进程数量、Agent 数量、ticks |
| 6 | `read_context` | `none` | 本次调用写入后的 Context Path count、head、total calls |
| 7 | `query_file` | `path:string` | inode 类型、inode 号、文件大小 |
| 8 | `send_message` | `target_pid:uint64,message:string` | 目标 pid、发送者 pid、消息长度 |
| 9 | `read_message` | `none` | mailbox 是否有效、发送者 pid、消息长度和文本 |

## Context Path 接口

| 接口 | 行为 |
| --- | --- |
| `context_push(record)` | 追加手动节点，内核分配新的 sequence |
| `context_query(start_sequence, out, max)` | 从 `start_sequence` 起按时间顺序复制仍可见记录；`start_sequence=0` 表示从最早可见记录开始 |
| `context_snapshot(header, records, max)` | 一次返回 header 和按时间顺序排列的可见 records |
| `context_rollback(sequence)` | 回滚到仍可见 sequence；不存在时返回 `AGENT_STATUS_NOT_FOUND` |
| `context_clear()` | 清空记录、计数和 latest response |

`struct agent_context_record` 保存工具 ID、状态码、sequence、request_id、数值槽、tick，以及 16 字节 payload/result 短文本摘要；工具名称可通过 `agent_tool_list()` / `tool_list()` 按 `tool_id` 解释。它不是完整 raw 请求/响应日志，不保存全部参数键名、参数类型、`arg1` 或完整长文本。超过 128 条记录时，Context Path 按 FIFO 覆盖旧记录，并更新 `oldest_sequence`、`latest_sequence` 和 `dropped_records`。`context_query()` 和 `context_snapshot()` 都从内核 shadow 页读取权威记录，并在 snapshot 前刷新用户镜像，避免用户态篡改镜像后污染内核返回值。Context 布局或记录区不可用时，Context syscall 返回 `AGENT_STATUS_NO_SPACE`；legacy `tool_call()` 可返回结构化 `NO_SPACE` 响应。
