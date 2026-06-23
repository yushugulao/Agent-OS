<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Agent-OS API 与 ABI

本文档描述用户态程序与 Agent-OS 内核扩展之间的稳定接口。结构体和常量定义以 `kernel/agent.h` 为准，用户态声明以 `user/user.h` 为准。

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
| `agent_watch` | 35 | `int agent_watch(int, const char *)` | 注册 Agent Loop 事件类型和短文本过滤器 |
| `agent_wait` | 36 | `int agent_wait(struct agent_event *, int)` | 等待事件或 timeout，成功消费事件后写入 Context Path |
| `agent_heartbeat` | 37 | `int agent_heartbeat(int)` | 设置心跳间隔并更新最后心跳 tick |
| `agent_wake` | 38 | `int agent_wake(int, struct agent_event *)` | 向目标 Agent 投递结构化事件 |
| `agent_file_meta_init` | 39 | `int agent_file_meta_init(void)` | 初始化任务四演示文件元数据表 |
| `agent_file_meta_set` | 40 | `int agent_file_meta_set(struct agent_file_meta *)` | 插入或合并更新文件元数据，状态变化可触发事件 |
| `agent_file_query` | 41 | `int agent_file_query(struct agent_file_query *, struct agent_file_query_result *)` | Agent-only 文件属性查询，成功后写入 Context Path |
| `agent_set_role` | 42 | `int agent_set_role(int)` | Agent 确认当前角色；不允许自升权 |
| `agent_create_role` | 43 | `int agent_create_role(int)` | 创建指定角色 Agent；普通进程只能创建 Sentinel，或在没有存活 Orchestrator 时引导一个 Orchestrator |
| `agent_unwatch` | 44 | `int agent_unwatch(int, const char *)` | 删除匹配 watch；`AGENT_EVENT_NONE` 加空 filter 清空全部 watch |
| `agent_heartbeat_stop` | 45 | `int agent_heartbeat_stop(void)` | 停止 heartbeat timer 事件 |

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

`agent_run()` 一次最多执行 `AGENT_BATCH_MAX = 64` 个 op，`flags` 当前必须为 0；非 0 时返回 `AGENT_STATUS_BAD_PARAM`，不执行任何工具。单个 op 的工具错误写入对应 result；用户指针错误、count 非法或非 Agent 调用返回 `-1`。内核执行 batch 前会先检查所有 op 可读、所有 result slot 可写；合法 lazy sbrk 输出页会在预检阶段 prefault，非法或只读输出页失败且不会执行工具、追加 Context 或产生 mailbox 副作用。

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
| `AGENT_STATUS_NO_SPACE` | -6 | Context 空间不可用，或目标事件队列已满 |
| `AGENT_STATUS_TIMEOUT` | -7 | `agent_wait()` 等待超时 |
| `AGENT_STATUS_DENIED` | -8 | capability 或角色权限拒绝 |
| `AGENT_STATUS_DUPLICATE` | -9 | 重复 `corr_id` 恢复动作被幂等表拒绝 |

## 内核工具表

| ID | 名称 | 参数 | 返回 |
| ---: | --- | --- | --- |
| 1 | `echo` | `payload:string,arg0:uint64,arg1:uint64` | payload 长度、两个数值参数、payload 文本 |
| 2 | `pid_info` | `none` | 当前 pid、Agent ID、Agent 身份 |
| 3 | `ctx_stat` | `none` | Context base、size、调用次数 |
| 4 | `query_process` | `type:uint64` | 进程数量、Agent 数量、runnable 数量 |
| 5 | `get_system_status` | `none` | 进程数量、Agent 数量、ticks |
| 6 | `read_context` | `none` | 本次调用写入后的 Context Path count、head、total calls |
| 7 | `query_file` | `path:string`、`fid:uint64` 或 `key=value` 属性过滤串 | 兼容路径查询；属性查询返回 hits、scanned、used_index/truncated 和首个命中文件 |
| 8 | `send_message` | `target_pid:uint64,message:string` | 目标 pid、发送者 pid、消息长度 |
| 9 | `read_message` | `none` | mailbox 是否有效、发送者 pid、消息长度和文本 |
| 10 | `file_meta_init` | `none` | 初始化任务四演示文件元数据表 |
| 11 | `read_file_summary` | `selector:string` | 按物理名、逻辑路径或 stage 返回摘要 |
| 12 | `dependency_query` | `stage:string` | 返回某阶段影响范围 |
| 13 | `capability_check` | `action:string` | 检查当前 Agent PCB 中的 capability 是否允许执行动作 |
| 14 | `rerun_stage` | `stage:string` | 幂等执行受控恢复动作 |
| 15 | `write_report` | `payload:string` | 更新恢复报告工件的内存元数据状态 |
| 16 | `agent_watch` | `event_type:uint64,filter:string` | 注册 Agent Loop watch |
| 17 | `agent_wait` | `timeout:uint64` | 工具表可发现项；该工具是 syscall-only，`agent_run()` 调用会返回 `AGENT_STATUS_BAD_PARAM` |
| 18 | `agent_heartbeat` | `interval:uint64` | 设置心跳间隔 |

## 任务四文件查询 ABI

`struct agent_file_meta` 表示一条科研平台工件元数据，字段包括 `fid`、`physical_name`、`logical_path`、`project`、`workflow`、`run_id`、`stage`、`kind`、`status`、`summary`、`dependency_mask`、`updated_tick` 和 `update_mask`。当前实现把这些记录保存在 Agent 子系统的内核内存元数据表中，不写入 xv6 inode，也不在重启后持久化。`dependency_mask` 非 0 时会被写入；如需把依赖掩码显式清零，调用方设置 `update_mask |= AGENT_FILE_META_UPDATE_DEPS` 且 `dependency_mask = 0`。如需删除记录，调用方设置 `update_mask |= AGENT_FILE_META_DELETE`，并提供 `fid`、`physical_name` 或 `logical_path` 中至少一个定位条件。

`struct agent_file_query` 以空字符串表示“不限制该字段”，`fid > 0` 表示按工件 ID 精确查询。`flags` 支持：

| flag | 含义 |
| --- | --- |
| `AGENT_FILE_QUERY_USE_INDEX` | 在 `status/run_id/stage/kind` 可用索引中选择候选最少的索引桶 |
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

`agent_create()` 和 `agent_fork()` 默认创建 Sentinel Agent。`agent_create_role(role)` 收紧为两类路径：普通进程只能创建 Sentinel，或在系统中没有存活 Orchestrator 时引导创建一个 Orchestrator；Recovery、Investigator 等工作 Agent 必须由 Orchestrator 创建。`agent_set_role(role)` 只允许 Agent 确认自己已经拥有的角色；如果 Sentinel 直接调用 `agent_set_role(AGENT_ROLE_ORCHESTRATOR)`，返回 `AGENT_STATUS_DENIED`，角色和 capability 不变。`capability_check`、`rerun_stage`、`write_report`、`agent_wake`、`agent_file_meta_init` 和 `agent_file_meta_set` 都以 PCB 中的 capability mask 为准，不信任工具请求里临时传入的 role 数字。

`agent_file_meta_init()`、`agent_file_meta_set()`、`agent_wake()` 是高权限 Agent syscall：普通进程调用返回 `-1`，Agent 但 capability 不足返回 `AGENT_STATUS_DENIED`。`agent_file_meta_init()` 初始化 112 条默认记录并预留 16 个空槽，同时清理演示动作历史，因此 `labdemo` 可在同一 QEMU 会话中重复运行。`agent_file_meta_set()` 如果状态变化触发事件但目标事件队列已满，会返回 `AGENT_STATUS_NO_SPACE`；元数据更新已经完成，部分 watcher 可能已经收到事件，调用方应重新查询元数据确认当前状态。文件状态事件 payload 只承诺短摘要字段，如 `fid/status/stage/run_id/truncated`；完整物理名、逻辑路径和摘要通过 `agent_file_query(fid=...)` 回查。

`dependency_query(stage)` 支持旧式 stage 文本，也支持 selector：`project=...;workflow=...;run_id=...;stage=...`。未提供 project/workflow/run_id 时默认使用演示运行 `lab-gene-x / nightly-regression / RUN-042`。该工具优先读取 selector 范围中同 stage 最近更新的 `dependency_mask`，包括显式清零后的 0 掩码；默认演示运行没有匹配元数据时，才使用内置示例 DAG 兜底。文本结果按 bit 拼接，例如 `align+report`、`report+archive` 或 `none`。

`rerun_stage(stage)` 同样支持 stage 文本或 selector。它使用上述 `dependency_mask` 作为恢复范围，按 bit 更新 selector 指定 project/workflow/run_id 内对应阶段的元数据状态；`report` 阶段恢复只更新普通报告工件，不更新 `lab_RUN042_recovery_report`。幂等键为 `{project, workflow, run_id, stage, action, request_id}`，同一 request_id 在不同运行或不同阶段上不会互相误拒绝。

`write_report` 支持 selector，默认目标仍为 `lab-gene-x / nightly-regression / RUN-042 / report`。它只把 selector 指定运行内的恢复报告工件标记为元数据状态 `ok`，并更新 summary；它不是 xv6 文件系统中的真实文件创建或写入。

## 任务五 Agent Loop ABI

`struct agent_event` 是事件等待和唤醒结构：

| 字段 | 含义 |
| --- | --- |
| `type` | 事件类型，如 `AGENT_EVENT_FILE_STATUS` 或 `AGENT_EVENT_MESSAGE` |
| `source_pid`/`target_pid` | 事件来源和目标 |
| `status` | 等待结果状态 |
| `event_id` | 内核分配的事件 ID |
| `tick` | 投递 tick |
| `corr_id` | 可选相关 ID，用于恢复动作、消息或测试 |
| `payload` | 64 字节短文本事件摘要 |

当前事件类型包括 `FILE_STATUS`、`MESSAGE`、`TIMER`、`JOB_DONE`、`POLICY_DENIED` 和 `CONTEXT_LIMIT`。`agent_wait()` 返回 `AGENT_STATUS_TIMEOUT` 时，输出事件的 `payload` 为 `timeout`。

`agent_unwatch(event_type, filter)` 删除匹配的 watch；当 `event_type=AGENT_EVENT_NONE` 且 filter 为空时，清空当前 Agent 的全部 watch。`agent_heartbeat_stop()` 等价于把 heartbeat 间隔设为 0，停止后不会再投递 heartbeat timer 事件。`agent_tick()` 只在 timeout 到期或 heartbeat 事件需要投递时唤醒等待 Agent，避免每个 tick 唤醒所有等待者。

## Context Path 接口

| 接口 | 行为 |
| --- | --- |
| `context_push(record)` | 追加手动节点，内核分配新的 sequence |
| `context_query(start_sequence, out, max)` | 从 `start_sequence` 起按时间顺序复制仍可见记录；`start_sequence=0` 表示从最早可见记录开始 |
| `context_snapshot(header, records, max)` | 一次返回 header 和按时间顺序排列的可见 records |
| `context_rollback(sequence)` | 裁剪到仍可见 sequence；不存在时返回 `AGENT_STATUS_NOT_FOUND` |
| `context_clear()` | 清空记录、计数和 latest response |

`struct agent_context_record` 保存工具 ID、状态码、sequence、request_id、数值槽、tick，以及 16 字节 payload/result 短文本摘要；工具名称可通过 `agent_tool_list()` / `tool_list()` 按 `tool_id` 解释。它不是完整 raw 请求/响应日志，不保存全部参数键名、参数类型、`arg1` 或完整长文本。超过 128 条记录时，Context Path 按 FIFO 覆盖旧记录，并更新 `oldest_sequence`、`latest_sequence` 和 `dropped_records`。`context_rollback()` 只裁剪当前仍可见的历史，不回退 `agent_call_count`；后续 `context_push()` 或工具调用会分配新的、更大的 sequence，所以查询结果允许出现不连续 sequence。`context_query()` 和 `context_snapshot()` 都从内核 shadow 页读取权威记录，并在 snapshot 前刷新用户镜像，避免用户态篡改镜像后污染内核返回值。Context 布局或记录区不可用时，Context syscall 返回 `AGENT_STATUS_NO_SPACE`；legacy `tool_call()` 可返回结构化 `NO_SPACE` 响应。
