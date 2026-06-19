# Agent-OS API 与 ABI

本文档描述用户态程序与 Agent-OS 内核扩展之间的稳定接口约定。结构体和常量定义以内核态 `os/agent.h` 和用户态 `user/include/agent.h` 为准。

## 系统调用

### Agent-OS syscall

Agent-OS 在 uCore syscall 编号空间中使用 500 至 517：

| syscall | 编号 | 用户态原型 | 说明 |
| --- | ---: | --- | --- |
| `agent_create` | 500 | `int agent_create(void)` | 创建 Agent 子进程 |
| `agent_info` | 501 | `int agent_info(struct agent_info *)` | 查询当前进程 Agent 元信息 |
| `agent_run` | 502 | `int agent_run(struct agent_op *, struct agent_result *, int, uint64)` | 高性能批量工具调用入口 |
| `agent_call` | 503 | `int agent_call(struct agent_request *, struct agent_response *)` | legacy 结构化工具调用 |
| `agent_tool_list` | 504 | `int agent_tool_list(struct agent_tool_desc *, int)` | 查询工具列表 |
| `context_push` | 505 | `int context_push(struct agent_context_record *)` | 手动追加 Context Path 节点 |
| `context_query` | 506 | `int context_query(uint64, struct agent_context_record *, int)` | 按 sequence 查询可见 Context Path |
| `context_snapshot` | 507 | `int context_snapshot(struct agent_context_header *, struct agent_context_record *, int)` | 批量返回 header 和可见 Context Path |
| `context_rollback` | 508 | `int context_rollback(uint64)` | 回滚到仍可见的历史节点 |
| `context_clear` | 509 | `int context_clear(void)` | 清空当前 Agent Context Path |
| `agent_watch` | 510 | `int agent_watch(int, const char *)` | 注册 Agent Loop 事件类型和短文本过滤器 |
| `agent_wait` | 511 | `int agent_wait(struct agent_event *, int)` | 等待事件或 timeout，成功消费事件后写入 Context Path |
| `agent_heartbeat` | 512 | `int agent_heartbeat(int)` | 设置心跳间隔并更新最后心跳 tick |
| `agent_wake` | 513 | `int agent_wake(int, struct agent_event *)` | 向目标 Agent 投递结构化事件 |
| `agent_file_meta_init` | 514 | `int agent_file_meta_init(void)` | 初始化任务四演示文件元数据表 |
| `agent_file_meta_set` | 515 | `int agent_file_meta_set(struct agent_file_meta *)` | 插入或合并更新文件元数据，状态变化可触发事件 |
| `agent_file_query` | 516 | `int agent_file_query(struct agent_file_query *, struct agent_file_query_result *)` | Agent 文件属性查询，成功后写入 Context Path |
| `agent_create_role` | 517 | `int agent_create_role(int role)` | 按真实内核角色创建 Agent 子进程 |

`agent_run` 和 `context_snapshot` 是最终成品主路径。`agent_call` 保留为 legacy 兼容入口。

### uCore 基础兼容 syscall

| syscall | 编号 | 用户态原型 | 说明 |
| --- | ---: | --- | --- |
| `mailread` | 401 | `int mailread(void *buf, int len)` | 非阻塞读取当前进程普通 mail 队列；无消息返回 0 |
| `mailwrite` | 402 | `int mailwrite(int pid, void *buf, int len)` | 向目标普通进程 mail 队列写入最多 256 字节 |
| `trace` | 410 | `int trace(enum trace_request req, unsigned long id, uint8 data)` | 支持 `TRACE_READ`、`TRACE_WRITE` 和 syscall 计数查询 |

这些接口用于保留代表性基础 uCore 用户测试能力。Agent-OS 的最终验收主路径仍是 `CHAPTER=agent` 下的四个专项程序。

`mailread` / `mailwrite` 使用每进程 16 槽普通消息队列，每条最多 256 字节。`mailread` 无消息时返回 0，成功时返回读取字节数；`mailwrite` 成功时返回写入字节数。目标不存在、长度非法、队列满或用户指针错误返回 `-1`。

`trace` 的 `TRACE_READ` / `TRACE_WRITE` 只做 1 字节用户地址读写检查。`TRACE_SYSCALL` 返回对应 syscall ID 的累计进入次数，查询 `SYS_trace` 时本次 `trace` 调用也计入。当前只承诺 Agent Context 4 页不可执行；普通用户程序其他页仍按当前 uCore 装载方式映射，不宣称全局 W^X。

## Agent Context ABI

| 项目 | 值 |
| --- | --- |
| 起始地址 | `AGENT_CONTEXT_BASE` |
| 用户态计算 | `AGENT_TRAPFRAME - (16 + AGENT_CONTEXT_PAGES) * AGENT_PAGE_SIZE` |
| 大小 | `AGENT_CONTEXT_SIZE = 4 * 4096` |
| 当前大小 | 16384 字节 |
| 记录容量 | `AGENT_CONTEXT_MAX_RECORDS = 128` |
| Context 版本 | `AGENT_CONTEXT_VERSION = 2` |
| 权限 | Agent Context 用户镜像页可读写、不可执行；内核 shadow 副本不可被用户态访问 |

说明：上述权限只描述 Agent Context 特殊页。当前 uCore 分支仍使用 flat binary loader，普通用户程序正文、数据和 bss 所在页沿用基底 loader 的 RWX 映射；本项目没有在本轮把普通用户程序装载流程重构为完整 W^X。

布局：

| 偏移 | 内容 |
| --- | --- |
| `0` | `struct agent_context_header` |
| `sizeof(struct agent_context_header)` | `struct agent_result` |
| `AGENT_CONTEXT_RECORDS_OFFSET = 4096` | `struct agent_context_record[128]` |

内核在 `struct proc` 中同时保存 4 个用户镜像页和 4 个内核私有 shadow 页。header、latest result 和 record 的权威数据先写入 shadow 页，再同步到用户镜像页。用户态直接写镜像页不会改变 `context_query()` 或 `context_snapshot()` 返回的权威历史。

直接读 Context 镜像是可信 Agent 自身的高速缓存路径；如果需要可信历史，应使用 `context_snapshot()` 刷新并读取 shadow 权威数据。

## Agent 信息结构

`struct agent_info` 用于 `agent_info()`，关键字段如下：

| 字段 | 说明 |
| --- | --- |
| `is_agent` | 当前进程是否为 Agent |
| `agent_id` | 当前 Agent ID |
| `agent_role` | 当前 Agent 的真实内核角色；普通进程为 0 |
| `context_base` / `context_size` | Agent Context 用户虚拟地址和大小 |
| `agent_type` | Agent 类型，当前支持普通进程和 Agent 进程 |
| `heartbeat_interval` | 心跳间隔 |
| `resource_quota` | 当前 Context Path 记录配额 |
| `loop_state` | Agent Loop 状态 |
| `agent_call_count` | 工具调用总数 |
| `context_path_count` | 当前可见历史记录数 |
| `context_path_capacity` | 历史记录容量 |
| `context_path_head` | 下一次写入槽位 |
| `context_path_oldest` | 最早可见 sequence |
| `context_path_latest` | 最新 sequence |
| `context_path_dropped` | 被 FIFO 淘汰的记录数 |
| `context_path_rollback_count` | 成功回滚次数 |
| `latest_response_offset` | latest result 在 Agent Context 中的偏移 |
| `records_offset` | record 区在 Agent Context 中的偏移 |
| `event_count` / `event_dropped` | 事件统计 |
| `wait_count` / `timeout_count` | 等待与超时统计 |
| `last_heartbeat_tick` | 最近心跳 tick |
| `capability_mask` | 当前 Agent 能力位 |

## 角色与 capability

`agent_create()` 保留兼容语义，默认创建最低权限 `AGENT_ROLE_SENTINEL`。`agent_create_role(role)` 用于创建指定角色 Agent：

| 调用者 | 允许行为 |
| --- | --- |
| pid 1 的普通 init | 只允许创建 `AGENT_ROLE_ORCHESTRATOR`，用于启动演示控制面 |
| pid 1 的直接普通子进程 | 只允许创建 `AGENT_ROLE_ORCHESTRATOR`，用于支持 `usershell` 手动运行测试程序 |
| 具备 `AGENT_CAP_ORCHESTRATE` 的 Agent | 允许创建任意合法角色 |
| 其他普通进程 | 返回 `-1`，不创建 Agent |
| 不具备 orchestrate 能力的 Agent | 返回 `AGENT_STATUS_DENIED` |

当前角色能力如下：

| 角色 | capability |
| --- | --- |
| `AGENT_ROLE_SENTINEL` | `META_READ`、`PROCESS_READ`、`MESSAGE_SEND`、`WATCH`、`AUDIT_WRITE` |
| `AGENT_ROLE_INVESTIGATOR` | `META_READ`、`CONTENT_READ`、`MESSAGE_SEND`、`WATCH`、`AUDIT_WRITE` |
| `AGENT_ROLE_RECOVERY` | `META_READ`、`CONTENT_READ`、`MESSAGE_SEND`、`WATCH`、`RECOVER_STAGE`、`REPORT_WRITE`、`AUDIT_WRITE` |
| `AGENT_ROLE_ORCHESTRATOR` | 全部能力，包括 `META_WRITE` 和 `ORCHESTRATE` |

敏感授权只使用内核 `struct proc` 中的 `agent_role` 和 `agent_capability_mask`。`agent_op.arg0` 中传入的 role 只保留为 legacy/demo 参数，不参与 `capability_check`、`rerun_stage`、`write_report` 等敏感工具授权。

Agent-only 直接 syscall 的权限要求：

| syscall | 普通进程 | Agent capability 要求 |
| --- | --- | --- |
| `agent_wake` | 返回 `-1` | `MESSAGE_SEND` 或 `ORCHESTRATE` |
| `agent_file_meta_init` | 返回 `-1` | `META_WRITE` |
| `agent_file_meta_set` | 返回 `-1` | `META_WRITE` |
| `agent_file_query` | 返回 `-1` | `META_READ` |

## 高性能请求结构

`struct agent_op` 是最终热路径 ABI：

| 字段 | 说明 |
| --- | --- |
| `version` | 必须为 `AGENT_CALL_VERSION` |
| `tool_id` | 工具 ID，固定走 ID 分发 |
| `request_id` | 用户态请求 ID |
| `arg0` / `arg1` | 两个数值参数槽 |
| `flags` | 预留标志位 |
| `payload` | 64 字节短文本参数 |

`struct agent_result` 是对应结果：

| 字段 | 说明 |
| --- | --- |
| `version` / `status` / `tool_id` | 版本、状态和工具 ID |
| `request_id` / `sequence` | 请求 ID 和内核分配的 Agent 调用序号 |
| `value0` / `value1` / `value2` | 工具返回的三个数值槽 |
| `result` | 64 字节短文本结果 |

`agent_run()` 一次最多执行 `AGENT_BATCH_MAX = 64` 个 op。单个 op 的工具错误写入对应 result；用户指针错误、count 非法或非 Agent 调用返回 `-1`。

## Legacy 请求结构

`struct agent_request` / `struct agent_response` 保留，用于历史程序和语义兼容；最终性能测试不使用该热路径。`struct agent_request` 的关键字段：

| 字段 | 说明 |
| --- | --- |
| `version` | 必须为 `AGENT_CALL_VERSION` |
| `tool_id` | 工具 ID |
| `tool_name` | 工具名称 |
| `request_id` | 用户态请求 ID，内核原样带回 |
| `arg0_key` / `arg1_key` | 参数键名 |
| `arg0_type` / `arg1_type` | 参数类型 |
| `arg0` / `arg1` | 数值参数 |
| `payload_key` | 字符串参数键名 |
| `payload_type` | 字符串参数类型 |
| `payload` | 短字符串 payload |

当 `tool_id` 和 `tool_name` 同时提供时，内核先用 ID 定位工具，再校验名称是否匹配。不匹配时 legacy `agent_call()` 返回 `AGENT_STATUS_BAD_REQUEST`，结果文本为 `tool_mismatch`，不会执行工具。

## 错误码

| 错误码 | 值 | 说明 |
| --- | ---: | --- |
| `AGENT_STATUS_OK` | 0 | 成功 |
| `AGENT_STATUS_BAD_REQUEST` | -1 | 版本错误或请求结构不一致 |
| `AGENT_STATUS_UNKNOWN_TOOL` | -2 | 工具不存在 |
| `AGENT_STATUS_NOT_AGENT` | -3 | 普通进程调用 Agent-only 接口 |
| `AGENT_STATUS_BAD_PARAM` | -4 | 参数键、类型或必要参数错误 |
| `AGENT_STATUS_NOT_FOUND` | -5 | 文件、Agent 或历史节点不存在 |
| `AGENT_STATUS_NO_SPACE` | -6 | Context 空间、事件槽或布局不可用 |
| `AGENT_STATUS_TIMEOUT` | -7 | `agent_wait()` 等待超时 |
| `AGENT_STATUS_DENIED` | -8 | capability 或角色权限拒绝 |
| `AGENT_STATUS_DUPLICATE` | -9 | 重复恢复动作被识别 |

## 内核工具表

| ID | 名称 | 参数 | 返回 |
| ---: | --- | --- | --- |
| 1 | `echo` | `payload:string,arg0:uint64,arg1:uint64` | payload 长度、两个数值参数、payload 文本 |
| 2 | `pid_info` | `none` | 当前 pid、Agent ID、Agent 身份 |
| 3 | `ctx_stat` | `none` | Agent Context 起始地址、大小和当前调用次数 |
| 4 | `query_process` | `type:uint64` | 进程数量、Agent 数量和可运行进程数量 |
| 5 | `get_system_status` | `none` | 进程数量、Agent 数量和系统 tick |
| 6 | `read_context` | `none` | 本次调用追加后的 Context Path 记录数、head 和总调用次数 |
| 7 | `query_file` | `path:string` 或 `key=value` 属性过滤串 | 兼容路径查询；属性查询返回 hits、scanned、used_index/truncated 和首个命中文件 |
| 8 | `send_message` | `target_pid:uint64,message:string` | 向目标 Agent 发送短消息 |
| 9 | `read_message` | `none` | 读取当前 Agent 消息 |
| 10 | `file_meta_init` | `none` | 初始化任务四演示文件元数据表 |
| 11 | `read_file_summary` | `selector:string` | 按物理名、逻辑路径或 stage 返回摘要 |
| 12 | `dependency_query` | `stage:string` | 返回某阶段影响范围 |
| 13 | `capability_check` | `legacy_role:uint64,action:string` | 按当前进程真实 capability 检查动作；返回真实 role 和 capability mask |
| 14 | `rerun_stage` | `legacy_role:uint64,stage:string` | 只有具备 `RECOVER_STAGE` 的 Agent 可执行幂等恢复动作 |
| 15 | `write_report` | `legacy_role:uint64,payload:string` | 只有具备 `REPORT_WRITE` 的 Agent 可写恢复报告工件状态；支持 `stage=report;run_id=...;project=...` selector |
| 16 | `agent_watch` | `event_type:uint64,filter:string` | 注册 Agent Loop watch |
| 17 | `agent_wait` | `timeout:uint64` | 工具表可发现项；结构化事件返回使用 syscall |
| 18 | `agent_heartbeat` | `interval:uint64` | 设置心跳间隔 |

## 任务四文件查询 ABI

`struct agent_file_meta` 表示一条实验工件元数据，字段包括：

- `physical_name`
- `logical_path`
- `project`
- `workflow`
- `run_id`
- `stage`
- `kind`
- `status`
- `summary`
- `dependency_mask`
- `updated_tick`

`struct agent_file_query` 以空字符串表示“不限制该字段”。`flags` 支持：

| flag | 含义 |
| --- | --- |
| `AGENT_FILE_QUERY_USE_INDEX` | 在可用索引中选择候选路径 |
| `AGENT_FILE_QUERY_SCAN` | 强制扫描全部元数据记录 |

`struct agent_file_query_result` 返回：

| 字段 | 含义 |
| --- | --- |
| `total_hits` | 总命中数 |
| `returned` | 实际复制到 `hits[]` 的条数 |
| `used_index` | 本次是否使用索引路径 |
| `truncated` | 命中数超过 `hits[]` 容量时为 1 |
| `scanned_records` | 本次检查了多少条候选记录 |
| `query_ticks` | 查询内部 tick 差值 |

## 任务五 Agent Loop ABI

`struct agent_event` 是事件等待和唤醒结构：

| 字段 | 含义 |
| --- | --- |
| `type` | 事件类型，如 `AGENT_EVENT_FILE_STATUS` 或 `AGENT_EVENT_MESSAGE` |
| `source_pid` / `target_pid` | 事件来源和目标 |
| `status` | 等待结果状态 |
| `event_id` | 内核分配的事件 ID |
| `tick` | 投递 tick |
| `corr_id` | 可选相关 ID，用于恢复动作、消息或测试 |
| `payload` | 64 字节短文本事件摘要 |

当前事件类型包括：

| 类型 | 含义 |
| --- | --- |
| `AGENT_EVENT_FILE_STATUS` | 文件状态变化 |
| `AGENT_EVENT_MESSAGE` | Agent 消息 |
| `AGENT_EVENT_TIMER` | 定时/心跳事件 |
| `AGENT_EVENT_JOB_DONE` | 作业完成 |
| `AGENT_EVENT_POLICY_DENIED` | 策略拒绝 |
| `AGENT_EVENT_CONTEXT_LIMIT` | Context 限制事件 |

## Context Path 接口

| 接口 | 行为 |
| --- | --- |
| `context_push(record)` | 追加手动节点，内核分配新的 sequence |
| `context_query(start_sequence, out, max)` | 从 `start_sequence` 起按时间顺序复制仍可见记录；`start_sequence=0` 表示从最早可见记录开始 |
| `context_snapshot(header, records, max)` | 一次返回 header 和按时间顺序排列的可见 records |
| `context_rollback(sequence)` | 回滚到仍可见 sequence；不存在时返回 `AGENT_STATUS_NOT_FOUND` |
| `context_clear()` | 清空记录、计数和 latest response |

`struct agent_context_record` 保存工具 ID、状态码、sequence、request_id、数值槽、tick，以及 16 字节 payload/result 短文本摘要；工具名称可通过 `agent_tool_list()` 按 `tool_id` 解释。它不是完整 raw 请求/响应日志，不保存全部参数键名、参数类型或完整长文本。超过 128 条记录时，Context Path 按 FIFO 覆盖旧记录，并更新 `oldest_sequence`、`latest_sequence` 和 `dropped_records`。
