# 接口与 ABI：Agent-OS

本文档描述用户态程序与 Agent-OS 内核扩展之间的稳定接口约定。结构体和常量定义以内核态 `os/agent.h` 和用户态 `user/include/agent.h` 为准。

## 系统调用

### 系统调用：Agent-OS

Agent-OS 在 uCore syscall 编号空间中使用 500 至 538：

| syscall | 编号 | 用户态原型 | 说明 |
| --- | ---: | --- | --- |
| `agent_create` | 500 | `int agent_create(void)` | 创建 Agent 子进程 |
| `agent_info` | 501 | `int agent_info(struct agent_info *)` | 查询当前进程 Agent 元信息 |
| `agent_run` | 502 | `int agent_run(struct agent_op *, struct agent_result *, int, uint64)` | 高性能批量工具调用入口 |
| `agent_call` | 503 | `int agent_call(struct agent_request *, struct agent_response *)` | 正式名称协议入口，使用工具名称和参数键值列表 |
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
| `agent_file_meta_init` | 514 | `int agent_file_meta_init(void)` | 重新加载文件对象元数据、重建索引并启用扫描 |
| `agent_file_meta_set` | 515 | `int agent_file_meta_set(struct agent_file_meta *)` | 插入或合并更新文件元数据，状态变化可触发事件 |
| `agent_file_query` | 516 | `int agent_file_query(struct agent_file_query *, struct agent_file_query_result *)` | Agent 文件属性查询，成功后写入 Context Path |
| `agent_create_role` | 517 | `int agent_create_role(int role)` | 按真实内核角色创建 Agent 子进程 |
| `agent_unwatch` | 518 | `int agent_unwatch(int, const char *)` | 删除匹配 watch；`AGENT_EVENT_NONE` 加空 filter 表示清空全部 watch |
| `context_detail` | 519 | `int context_detail(uint64, struct agent_context_detail *)` | 按 sequence 查询完整工具调用详情 |
| `agent_wait_cancel` | 520 | `int agent_wait_cancel(int pid, const char *reason)` | 给目标 Agent 设置一次性等待取消令牌，并唤醒目标 |
| `agent_sched_snapshot` | 521 | `int agent_sched_snapshot(struct agent_sched_record *, int)` | 查询当前 Agent 最近 16 次调度原因记录 |
| `agent_trace_snapshot` | 522 | `int agent_trace_snapshot(struct agent_trace_record *, int)` | 合并返回当前 Agent 的 Context 摘要和调度原因短记录 |
| `agent_audit_snapshot` | 523 | `int agent_audit_snapshot(struct agent_audit_record *, int)` | orchestrator 查询全局 Agent 审计短记录 |
| `agent_audit_query` | 524 | `int agent_audit_query(struct agent_audit_filter *, struct agent_audit_record *, int)` | orchestrator 按条件过滤查询全局 Agent 审计短记录 |
| `agent_sched_config` | 525 | `int agent_sched_config(struct agent_sched_config *)` | orchestrator 配置目标 Agent 的调度 policy、weight、priority 和 budget |
| `agent_file_prefetch_snapshot` | 526 | `int agent_file_prefetch_snapshot(struct agent_file_prefetch_hint *, int)` | 查询当前 Agent 的文件 metadata 预取提示 |
| `agent_file_prefetch_span_snapshot` | 527 | `int agent_file_prefetch_span_snapshot(struct agent_file_prefetch_hint *, int)` | 查询当前 span 的全局文件 metadata 预取提示 |
| `agent_span_trace_snapshot` | 528 | `int agent_span_trace_snapshot(struct agent_audit_record *, int)` | 当前 Agent 查询当前 span 的系统级短记录 |
| `agent_timeline_snapshot` | 529 | `int agent_timeline_snapshot(struct agent_timeline_record *, int)` | 统一导出当前 Agent 可见的 Context、调度、审计和预取提示短记录 |
| `agent_timeline_query` | 530 | `int agent_timeline_query(struct agent_timeline_filter *, struct agent_timeline_record *, int)` | 在统一 timeline 上执行内核侧过滤查询 |
| `agent_provenance_snapshot` | 531 | `int agent_provenance_snapshot(struct agent_provenance_edge *, int)` | 导出当前 Agent 可见的 Context、审计和预取因果边 |
| `agent_timeline_wait` | 532 | `int agent_timeline_wait(struct agent_timeline_filter *, int)` | 等待当前可见 timeline 出现匹配记录或 timeout |
| `agent_timeline_read` | 533 | `int agent_timeline_read(struct agent_timeline_filter *, struct agent_timeline_record *, int, int)` | 等待匹配 timeline 记录并在同一次 syscall 中复制记录 |
| `agent_ledger_snapshot` | 534 | `int agent_ledger_snapshot(struct agent_ledger_summary *)` | orchestrator 读取全局运行账本摘要和链尾 hash |
| `agent_file_edit_begin` | 535 | `int agent_file_edit_begin(const char *, uint64, int, struct agent_file_edit_state *)` | 为真实文件申请独占编辑租约 |
| `agent_file_edit_commit` | 536 | `int agent_file_edit_commit(uint64, uint64, struct agent_file_edit_state *)` | 按租约和期望版本提交编辑 |
| `agent_file_edit_abort` | 537 | `int agent_file_edit_abort(uint64)` | 放弃当前进程持有的编辑租约 |
| `agent_file_edit_state` | 538 | `int agent_file_edit_state(const char *, struct agent_file_edit_state *)` | 查询真实文件当前编辑租约和版本状态 |

`agent_run` 和 `context_snapshot` 是最终成品性能主路径。`agent_file_prefetch_snapshot` 用于读取当前 Agent 自己可见的 metadata 预取提示，`agent_file_prefetch_span_snapshot` 用于读取同一 span 下跨 Agent 汇总的 metadata 预取提示。`agent_trace_snapshot` 是单个 Agent 的演示和调试主路径，用于把工具调用历史与调度原因放进同一组短记录中。`agent_span_trace_snapshot` 读取当前 Agent 所在 span 的系统级短记录，使参与协作的 Agent 能解释本轮协作中的 Context、事件和预取交接来源。`agent_timeline_snapshot` 是统一导出入口，把当前 Agent 可见的 Context、调度、审计和预取提示转换成同一种 record，便于最终科研平台页面直接读取。`agent_timeline_query` 在同一组可见记录上执行 source、tick、span、pid、kind、tool、event、status、flags 和 after-cursor 过滤，减少最终页面重复拉取和用户态筛选，也支持页面拿上一条记录作为游标继续读取后续记录。`agent_timeline_wait` 复用同一 filter，在没有匹配记录时让 Agent 睡眠；新记录写入时内核把新记录规范化为 `agent_timeline_record`，并直接用等待者保存的完整 filter 判断是否唤醒。`agent_timeline_read` 在同一套规则上把等待和复制合并为一次 syscall，减少页面或 Agent worker 的 wait 后再 query 成本。`agent_file_edit_begin`、`agent_file_edit_commit`、`agent_file_edit_abort` 和 `agent_file_edit_state` 是真实文件编辑冲突控制接口；内核用真实 `dev + inum` 识别文件，并在 `write`、`O_TRUNC`、`unlink` 路径上检查租约持有者。`agent_provenance_snapshot` 导出同一可见范围内的因果边，用于最终页面绘制“哪个 Context、事件或预取提示触发了后续动作”。`agent_audit_snapshot` 和 `agent_audit_query` 是 orchestrator 的系统级观测入口，用于读取和过滤最近 512 条全局短记录。`agent_ledger_snapshot` 在同一组全局短记录上返回可见范围、总量、已淘汰数、分类计数和账本 hash，便于页面用一个摘要判断本轮运行事实是否仍处在同一条内核维护的记录链上。`agent_call` 是赛题“工具名称 + 参数键值列表”结构化协议的正式入口，也兼容已有演示程序。

### 基础兼容系统调用：uCore

| syscall | 编号 | 用户态原型 | 说明 |
| --- | ---: | --- | --- |
| `mailread` | 401 | `int mailread(void *buf, int len)` | 非阻塞读取当前进程普通 mail 队列；无消息返回 0 |
| `mailwrite` | 402 | `int mailwrite(int pid, void *buf, int len)` | 向目标普通进程 mail 队列写入最多 256 字节 |
| `trace` | 410 | `int trace(enum trace_request req, unsigned long id, uint8 data)` | 支持 `TRACE_READ`、`TRACE_WRITE` 和 syscall 计数查询 |

这些接口用于保留代表性基础 uCore 用户测试能力。Agent-OS 的最终验收主路径仍是 `CHAPTER=agent` 下的专项程序。

`mailread` / `mailwrite` 使用每进程 16 槽普通消息队列，每条最多 256 字节。`mailread` 无消息时返回 0，成功时返回读取字节数；`mailwrite` 成功时返回写入字节数。目标不存在、长度非法、队列满或用户指针错误返回 `-1`。

`trace` 的 `TRACE_READ` / `TRACE_WRITE` 只做 1 字节用户地址读写检查。`TRACE_SYSCALL` 返回对应 syscall ID 的累计进入次数，查询 `SYS_trace` 时本次 `trace` 调用也计入。当前只承诺 Agent Context 特殊页不可执行；普通用户程序其他页仍按当前 uCore 装载方式映射，不宣称全局 W^X。

## 上下文 ABI：Agent Context

| 项目 | 值 |
| --- | --- |
| 起始地址 | `AGENT_CONTEXT_BASE` |
| 用户态计算 | `AGENT_TRAPFRAME - (16 + AGENT_CONTEXT_PAGES) * AGENT_PAGE_SIZE` |
| 大小 | `AGENT_CONTEXT_SIZE = 6 * 4096` |
| 当前大小 | 24576 字节 |
| 记录容量 | `AGENT_CONTEXT_MAX_RECORDS = 128` |
| Context 版本 | `AGENT_CONTEXT_VERSION = 6` |
| 权限 | Agent Context 用户镜像页可读写、不可执行；内核 shadow 副本不可被用户态访问 |

说明：上述权限只描述 Agent Context 特殊页。当前 uCore 分支仍使用 flat binary loader，普通用户程序正文、数据和 bss 所在页沿用基底 loader 的 RWX 映射；本项目没有在本轮把普通用户程序装载流程重构为完整 W^X。

布局：

| 偏移 | 内容 |
| --- | --- |
| `0` | `struct agent_context_header` |
| `sizeof(struct agent_context_header)` | `struct agent_result` |
| `AGENT_CONTEXT_RECORDS_OFFSET = 4096` | `struct agent_context_record[128]` |
| `header.user_cache_offset` | 用户自管 cache 起点，当前测试输出为 21504 |
| `header.user_cache_size` | 用户自管 cache 大小，当前测试输出为 3072 |

内核在 `struct proc` 中同时保存 6 个用户镜像页和 6 个内核私有 shadow 页。header、latest result 和 record 的权威数据先写入 shadow 页，再同步到用户镜像页。用户态直接写镜像页不会改变 `context_query()` 或 `context_snapshot()` 返回的权威历史。

用户自管 cache 区位于 Context 尾部，不进入 shadow 权威历史，也不会被 `context_snapshot()` 刷新覆盖。它只用于 Agent 自己保存策略缓存或临时状态，不能作为内核可信历史。若需要可信历史，应使用 `context_snapshot()` 刷新并读取 shadow 权威数据。若需要完整请求和完整响应，应使用 `context_detail(sequence, out)`，不要把 16 字节短摘要 record 当作完整日志。

## 信息结构：Agent

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
| `current_span_id` | 当前 Agent 因果链 span；同一链路中的记录和事件共享该值 |
| `current_cause_sequence` | 当前记录或事件默认指向的前序 Context sequence |
| `provenance_edges` | 已写入的非 root 因果关系数量 |
| `observe_epoch` | 当前内核观测 epoch；Context、审计、调度和预取提示写入时递增 |
| `latest_response_offset` | latest result 在 Agent Context 中的偏移 |
| `records_offset` | record 区在 Agent Context 中的偏移 |
| `event_queue_count` / `event_count` / `event_dropped` | 当前队列长度、累计事件数和丢弃计数 |
| `watch_count` | 当前有效 watch 条件数 |
| `wait_count` / `wait_sleep_count` / `wait_wakeup_count` / `wait_cancel_count` / `timeout_count` | 等待、进入等待路径、被唤醒、等待取消和超时统计 |
| `wait_loop_count` | `agent_wait()` 的检查循环次数，用于检查有限 timeout 是否避免反复轮询 |
| `timeline_wait_count` / `timeline_wait_sleep_count` / `timeline_wait_wakeup_count` / `timeline_wait_timeout_count` | timeline 等待、睡眠、被观测事件唤醒和等待超时统计 |
| `last_heartbeat_tick` | 最近心跳 tick |
| `current_tick` | `agent_info()` 返回时的内核 Agent tick，供 timeline 等待建立未来记录过滤条件 |
| `capability_mask` | 当前 Agent 能力位 |
| `file_scan_runs` / `file_scan_entries` | 根目录自动扫描轮数和检查过的目录项数量 |
| `file_scan_added` / `file_scan_updated` / `file_scan_removed` | 自动扫描新增、更新和清理的元数据计数 |
| `file_scan_generation` / `file_scan_pending` | 文件元数据代数和是否存在待处理扫描请求 |
| `file_digest_cache_hits` / `file_digest_cache_misses` | 真实文件内容摘要缓存命中和未命中计数 |
| `sched_policy` / `sched_weight` / `sched_priority` / `sched_budget` | 当前 Agent 调度策略、角色权重、调度优先级和预算总额 |
| `sched_dispatch_count` / `sched_event_dispatch_count` / `sched_deadline_dispatch_count` | 被调度次数、因事件获得调度的次数和 deadline 相关调度次数 |
| `sched_vruntime` / `sched_ready_tick` / `sched_last_dispatch_tick` | 虚拟运行量、最近进入可运行队列 tick 和最近被调度 tick |
| `sched_preemptions` / `sched_budget_used` | 让出处理器次数和当前预算使用量 |
| `sched_last_score` / `sched_last_reason` / `sched_trace_count` | 最近一次调度分数、原因 flags 和累计原因记录数 |
| `current_span_id` / `current_cause_sequence` / `provenance_edges` | 当前 Agent 的因果链观测字段 |

事件队列容量为 `AGENT_EVENT_QUEUE_CAP = 16`。watch 数量上限为 `AGENT_WATCH_MAX = 8`。

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
| `AGENT_ROLE_RECOVERY` | `META_READ`、`CONTENT_READ`、`MESSAGE_SEND`、`WATCH`、`ACTION_WRITE`、`ARTIFACT_WRITE`、`AUDIT_WRITE` |
| `AGENT_ROLE_ORCHESTRATOR` | 全部能力，包括 `META_WRITE`、`ORCHESTRATE` 和 `LLM_RELAY` |

敏感授权只使用内核 `struct proc` 中的 `agent_role` 和 `agent_capability_mask`。`agent_op.arg0` 中传入的 role 只保留为 legacy/demo 参数，不参与 `capability_check`、`action_commit`、`artifact_update`、`llm_response` 等敏感工具授权。`rerun_stage` 和 `write_report` 仍可调用，但它们只是面向旧演示的兼容别名；运行记录、事件 action 和重复请求判断都归入 `action_commit` 或 `artifact_update`。

`RECOVER_STAGE` 和 `REPORT_WRITE` 在头文件中保留为旧程序兼容别名，分别等价于 `ACTION_WRITE` 和 `ARTIFACT_WRITE`。新代码和文档应优先使用通用能力名。

Agent-only 直接 syscall 的权限要求：

| syscall | 普通进程 | Agent capability 要求 |
| --- | --- | --- |
| `agent_wake` | 返回 `-1` | `MESSAGE_SEND` 或 `ORCHESTRATE` |
| `agent_wait_cancel` | 返回 `-1` | `MESSAGE_SEND` 或 `ORCHESTRATE` |
| `agent_file_meta_init` | 返回 `-1` | `META_WRITE` |
| `agent_file_meta_set` | 返回 `-1` | `META_WRITE` |
| `agent_file_query` | 返回 `-1` | `META_READ` |
| `agent_file_edit_begin` | 返回 `-1` | `CONTENT_READ`、`ARTIFACT_WRITE`、`META_WRITE` 或 `ORCHESTRATE` 之一 |
| `agent_file_edit_commit` | 返回 `-1` | 租约持有者；orchestrator 可释放卡住的租约 |
| `agent_file_edit_abort` | 返回 `-1` | 租约持有者；orchestrator 可释放卡住的租约 |
| `agent_file_edit_state` | 返回 `-1` | Agent 身份 |
| `agent_file_prefetch_snapshot` | 返回 `-1` | `META_READ` |
| `agent_file_prefetch_span_snapshot` | 返回 `-1` | `META_READ` |
| `agent_span_trace_snapshot` | 返回 `-1` | `AUDIT_WRITE` |
| `agent_timeline_snapshot` | 返回 `-1` | Agent 身份；审计记录按 role/capability 自动裁剪 |
| `agent_timeline_query` | 返回 `-1` | Agent 身份；只在当前 Agent 已可见的 timeline 记录上过滤 |
| `agent_timeline_wait` | 返回 `-1` | Agent 身份；只等待当前 Agent 已可见的 timeline 记录 |
| `agent_timeline_read` | 返回 `-1` | Agent 身份；只等待并复制当前 Agent 已可见的 timeline 记录 |
| `agent_provenance_snapshot` | 返回 `-1` | Agent 身份；审计边按 role/capability 自动裁剪 |
| `agent_audit_snapshot` | 返回 `-1` | `ORCHESTRATE` |
| `agent_audit_query` | 返回 `-1` | `ORCHESTRATE` |
| `agent_ledger_snapshot` | 返回 `-1` | `ORCHESTRATE` |
| `agent_sched_config` | 返回 `-1` | `ORCHESTRATE` |

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

## 名称协议请求结构

`struct agent_request` / `struct agent_response` 是赛题“工具名称 + 参数键值列表”的正式结构化协议入口；性能主路径仍使用更紧凑的 `agent_op` / `agent_result`。`struct agent_request` 的关键字段：

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

当 `tool_id` 和 `tool_name` 同时提供时，内核先用 ID 定位工具，再校验名称是否匹配。不匹配时 `agent_call()` 返回 `AGENT_STATUS_BAD_REQUEST`，结果文本为 `tool_mismatch`，不会执行工具。只提供 `tool_name` 时，内核按工具表名称解析工具，并继续校验参数键和类型。

## 错误码

| 错误码 | 值 | 说明 |
| --- | ---: | --- |
| `AGENT_STATUS_OK` | 0 | 成功 |
| `AGENT_STATUS_BAD_REQUEST` | -1 | 版本错误或请求结构不一致 |
| `AGENT_STATUS_UNKNOWN_TOOL` | -2 | 工具不存在 |
| `AGENT_STATUS_NOT_AGENT` | -3 | 普通进程调用 Agent-only 接口 |
| `AGENT_STATUS_BAD_PARAM` | -4 | 参数键、类型或必要参数错误 |
| `AGENT_STATUS_NOT_FOUND` | -5 | 文件、Agent 或历史节点不存在 |
| `AGENT_STATUS_NO_SPACE` | -6 | Context 空间、事件队列或布局不可用 |
| `AGENT_STATUS_TIMEOUT` | -7 | `agent_wait()` 等待超时 |
| `AGENT_STATUS_DENIED` | -8 | capability 或角色权限拒绝 |
| `AGENT_STATUS_DUPLICATE` | -9 | 重复幂等动作被识别 |
| `AGENT_STATUS_CANCELLED` | -10 | `agent_wait()` 被受权 Agent 取消 |
| `AGENT_STATUS_CONFLICT` | -11 | 文件编辑租约已被其他 Agent 持有 |
| `AGENT_STATUS_STALE` | -12 | 提交时给出的期望版本已经不是当前租约基准版本 |

## 内核工具表

| ID | 名称 | 参数 | 返回 |
| ---: | --- | --- | --- |
| 1 | `echo` | `payload:string,arg0:uint64,arg1:uint64` | payload 长度、两个数值参数、payload 文本 |
| 2 | `pid_info` | `none` | 当前 pid、Agent ID、Agent 身份 |
| 3 | `ctx_stat` | `none` | Agent Context 起始地址、大小和当前调用次数 |
| 4 | `query_process` | `type:uint64` | 进程数量、Agent 数量和可运行进程数量 |
| 5 | `get_system_status` | `none` | 进程数量、Agent 数量和系统 tick |
| 6 | `read_context` | `none` | 本次调用追加后的 Context Path 记录数、head 和总调用次数 |
| 7 | `query_file` | `path:string` 或 `key=value` 属性过滤串 | 兼容路径查询；属性查询返回 hits、scanned、used_index、query plan、truncated 和首个命中文件 |
| 8 | `send_message` | `target_pid:uint64,message:string` | 向目标 Agent 发送短消息 |
| 9 | `read_message` | `none` | 读取当前 Agent 消息 |
| 10 | `file_meta_init` | `none` | 重新加载 `.agentmeta`、重建索引并启用根目录扫描；后端为空时安装空元数据表 |
| 11 | `read_file_summary` | `selector:string` | 按物理名、逻辑路径或对象 label 返回摘要 |
| 12 | `dependency_query` | `label:string` 或 `label/namespace/run_id` selector | 返回用户态注册的对象依赖影响范围，结果中的 `value2` 是依赖记录代数 |
| 13 | `capability_check` | `legacy_role:uint64,action:string` | 按当前进程真实 capability 检查动作；返回真实 role 和 capability mask |
| 14 | `rerun_stage` | `legacy_role:uint64,stage:string` | demo compatibility；内部调用通用 `action_commit` 状态更新路径 |
| 15 | `write_report` | `legacy_role:uint64,payload:string` | demo compatibility；内部调用通用 `artifact_update` 状态更新路径 |
| 16 | `agent_watch` | `event_type:uint64,filter:string` | 注册 Agent Loop watch |
| 17 | `agent_wait` | `timeout:uint64` | syscall-only 可发现项；`agent_run()` 调用返回 `AGENT_STATUS_BAD_PARAM` |
| 18 | `agent_heartbeat` | `interval:uint64` | 设置心跳间隔；`interval=0` 停止心跳 |
| 19 | `context_push` | `record` | 手动 Context 节点使用的内部工具 ID |
| 20 | `read_file_digest` | `selector:string` | 读取真实文件的短预览、参与计算字节数和 FNV-1a 内容指纹 |
| 21 | `action_commit` | `selector:string` | 按通用对象 selector 幂等提交 Agent 动作，可根据依赖标签刷新后续对象 |
| 22 | `artifact_update` | `selector:string` | 按通用对象 selector 更新工件、报告、记忆或结果对象状态 |
| 23 | `llm_request` | `target_pid:uint64,prompt_summary:string` | 记录 LLM 请求摘要，可把请求事件投递给用户态 LLM Relay |
| 24 | `llm_response` | `target_pid:uint64,response_summary:string` | 由具备 `LLM_RELAY` 的 Agent 投递 LLM 结果事件，唤醒请求方 |
| 25 | `dependency_update` | `selector:string` | 由具备元数据写权限的 Agent 注册或更新通用对象依赖 |

工具描述中 `flags` 表示调用方式：

| flag | 含义 |
| --- | --- |
| `AGENT_TOOL_F_CALLABLE` | 可通过 `agent_run()` 执行 |
| `AGENT_TOOL_F_SYSCALL_ONLY` | 只作为工具表可发现项，必须通过对应 syscall 执行 |

## 任务四文件查询 ABI

`struct agent_file_meta` 表示一条 Agent 文件对象元数据。字段名保留早期科研 demo 兼容形式，但语义按通用对象模型使用：`project` 可作为 namespace，`logical_path` 可作为 object_id，`stage` 可作为 label，`kind` 可作为 type，`status` 可作为 state。字段包括：

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
- `flags`
- `dev`
- `inum`
- `size`
- `fs_generation`
- `update_mask`

文件元数据主键优先使用真实文件的 `dev + inum`。`physical_name` 必须能解析为 uCore 根目录中的真实短文件名，复杂逻辑路径保存在 `logical_path` 等 Agent 属性字段中。根目录私有文件 `.agentmeta` 保存固定格式元数据表，`agent_file_meta_init()` 会强制重新加载它；文件不存在、格式错误或没有有效记录时安装空元数据表。普通文件 syscall 不能直接 `open/create/unlink` `.agentmeta`，Agent 子系统内部 helper 负责读写该后端文件。

字符串 selector 支持两组字段名：兼容字段 `project/run_id/stage/kind/status`，以及通用字段 `namespace/object_id/label/type/state`。内核按这些字段执行同一套查询、状态更新、依赖查询和预取提示生成。科研平台中的 RUN-042 数据由用户态 orchestrator 写入；内核不会预置项目名、run id 或固定阶段顺序。

对象依赖关系不再由内核固定解释某几个阶段名称。用户态可以通过 `dependency_update` 显式注册 `source/target/namespace/run_id/relation/summary` 形式的通用依赖记录，也可以继续通过 `agent_file_meta_set()` 写入对象 label 和 `dependency_mask` 作为紧凑兼容输入。每条记录包含源对象 label、目标对象 label、关系、namespace、run_id 和摘要；`dependency_query` 可用 `label=...;namespace=...;run_id=...` 缩小查询范围，文件查询后的预取提示和 provenance 也优先读取这些通用记录。旧的 `dependency_mask` 仍保留为兼容格式。

`update_mask` 用于精确更新字段，也允许清空字段。例如只清空 status 时传入 `AGENT_FILE_META_UPDATE_STATUS` 并让 `status` 为空字符串。

`flags` 支持：

| flag | 含义 |
| --- | --- |
| `AGENT_FILE_META_F_DELETE` | 按 path/fid/dev+inum 删除元数据 |
| `AGENT_FILE_META_F_PERSIST` | 更新后写入 `.agentmeta` |
| `AGENT_FILE_META_F_AUTOSCAN` | 由根目录自动扫描维护的元数据 |

根目录自动扫描由 Agent 文件元数据服务启用。timer tick 和文件系统 hook 只标记扫描请求；调度器空隙调用 `agent_background_maintain()` 分批扫描 uCore 根目录。扫描发现的新真实文件会生成 `AUTOSCAN | PERSIST` 元数据，文件删除后对应自动元数据会被清理。当前扫描范围是 uCore 根目录短文件名，不承诺多级目录递归或全文内容索引。

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
| `plan` | 查询计划，0 为扫描，1/2/3 分别为 status/stage/kind 索引 |
| `index_bucket` | 命中的索引桶；扫描路径为 -1 |
| `candidate_records` | 本次候选记录数量，和 `scanned_records` 一起用于解释索引收益 |
| `query_ticks` | 查询内部 tick 差值 |
| `plan_reason` | 查询计划原因 flags，例如强制扫描、status 索引、stage 索引、kind 索引、查询缓存命中或没有可用索引键 |
| `fs_generation` | 查询时文件元数据服务的全局更新代数 |

每条 hit 还返回 `dev`、`inum`、`size` 和 `fs_generation`，用于说明查询结果来自真实文件绑定和当前元数据版本。

查询计划常量：

| 常量 | 含义 |
| --- | --- |
| `AGENT_FILE_QUERY_PLAN_SCAN` | 扫描全部可用元数据槽 |
| `AGENT_FILE_QUERY_PLAN_STATUS_INDEX` | 使用 status 索引链 |
| `AGENT_FILE_QUERY_PLAN_STAGE_INDEX` | 使用 stage 索引链 |
| `AGENT_FILE_QUERY_PLAN_KIND_INDEX` | 使用 kind 索引链 |

`plan_reason` 使用位标记说明为什么选择该计划：`FORCED_SCAN` 表示调用者强制扫描；`INDEX_OFF` 表示未请求索引；`STATUS_INDEX`、`STAGE_INDEX`、`KIND_INDEX` 表示对应索引参与计划；`NO_INDEX_KEY` 表示请求了索引但查询条件没有 status、stage 或 kind；`CACHE_HIT` 表示本次非强制扫描命中查询直接复用了同一 `fs_generation` 下的结果缓存。空结果和自动扫描进行中的查询不进入缓存。

### 文件编辑租约 ABI

文件编辑租约用于处理两个 Agent 同时希望修改同一真实文件的情况。内核用真实 `dev + inum` 识别文件，不依赖用户态传入的逻辑路径。普通进程不能申请租约；Agent 需要具备内容读取、工件写入、元数据写入或编排能力之一。租约存在时，真实 `write`、`O_TRUNC` 和 `unlink` 路径会检查当前进程是否是租约持有者；不是持有者时直接失败。

调用方式如下：

| 接口 | 语义 |
| --- | --- |
| `agent_file_edit_begin(path, flags, ttl_ticks, state)` | 为根目录真实文件申请独占编辑租约；成功返回 0，文件不存在返回 `AGENT_STATUS_NOT_FOUND`，已有持有者返回 `AGENT_STATUS_CONFLICT` |
| `agent_file_edit_commit(lease_id, expected_version, state)` | 提交租约；`expected_version` 必须等于 begin 返回的 `base_version`，否则返回 `AGENT_STATUS_STALE` |
| `agent_file_edit_abort(lease_id)` | 释放当前进程持有的租约；如果已经写入过文件，内核仍会推进文件版本 |
| `agent_file_edit_state(path, state)` | 查询某个真实文件当前是否有租约和当前版本 |

`flags` 当前支持：

| flag | 含义 |
| --- | --- |
| `AGENT_FILE_EDIT_F_BREAK_EXPIRED` | 保留标志；过期租约当前会在新操作到来时自动释放 |
| `AGENT_FILE_EDIT_F_ORCHESTRATOR_BREAK` | orchestrator 可主动释放已有租约并重新申请，用于演示控制面处理卡住的编辑者 |

`struct agent_file_edit_state` 返回：

| 字段 | 含义 |
| --- | --- |
| `active` | 是否存在有效租约 |
| `owner_pid` / `owner_agent_id` / `owner_role` | 当前持有者信息 |
| `dirty` | 持有者是否已经写入、截断或删除文件 |
| `lease_id` | 提交或放弃时使用的租约编号 |
| `dev` / `inum` | 真实文件身份 |
| `base_version` | begin 时看到的版本 |
| `current_version` | 当前版本；提交成功后若发生写入会加 1 |
| `deadline_tick` | 租约自动释放 tick |
| `conflict_count` | 本租约被其他进程拒绝的次数 |
| `path` | begin/state 调用时使用的短文件名 |

该机制采用“立即拒绝 + 有限租约”的方式处理资源访问冲突：没有等待队列，因此不会形成循环等待；持有者异常退出或长时间不提交时，后续操作会释放过期租约。它不是文件内容合并器，不会自动把两个 Agent 的修改合成一份新内容；它负责阻止无序覆盖，并用版本检查告诉调用者必须重新读取、重新生成或走恢复流程。

### 文件内容摘要工具

`read_file_digest(selector:string)` 是任务四的内容级工具。它要求调用者具备 `AGENT_CAP_CONTENT_READ`，普通 metadata 查询能力不足以读取文件内容。`selector` 可以是物理文件名、逻辑路径、stage，也可以是 `project=...;run_id=...;stage=...;status=...` 这类属性过滤串；属性过滤命中多条时读取第一条命中文件。`.agentmeta` 私有后端文件不会通过该工具暴露。

绑定 Agent metadata 的真实文件会进入 8 槽 digest cache。缓存 key 是真实文件 `dev + inum + size + content_generation`，缓存 value 是文件大小、参与计算字节数、FNV-1a 指纹和短预览。文件创建、写入、截断或删除后，内容版本变化，旧 digest cache 条目自然失效；单纯 metadata 更新不会让同一文件内容摘要缓存失效。未绑定 Agent metadata 的普通文件不缓存，避免内核无法感知同尺寸改写时返回过期摘要。缓存命中和未命中计数通过 `agent_info.file_digest_cache_hits`、`agent_info.file_digest_cache_misses` 暴露。

返回值使用 `struct agent_result`：

| 字段 | 含义 |
| --- | --- |
| `value0` | 真实文件大小 |
| `value1` | 参与本次指纹计算的字节数，最多 `AGENT_FILE_DIGEST_MAX_BYTES = 4096` |
| `value2` | FNV-1a 64 位内容指纹 |
| `result` | 文件开头的短预览，非可打印字符会被替换为 `.` |

该工具不是全文搜索接口，也不建立内容倒排索引。它的用途是让 Agent 在拿到 metadata 命中后，能用受权工具取得轻量内容证据，例如报告页面展示 preview、评审脚本校验 artifact 是否真的存在、或者对照未改动 uCore 中用户态读取文件的成本。

### 文件预取提示 ABI

文件查询成功写入 Context 后，内核会根据命中的 source 文件、同一 namespace/workflow/run 的对象标签依赖和当前索引信息，生成 metadata 预取提示。提示首先保存在当前 Agent 的 PCB 中，容量为 `AGENT_FILE_PREFETCH_MAX_HINTS = 8`。同时，带有 span 的提示会写入全局 span 预取提示总线，容量为 `AGENT_FILE_PREFETCH_SPAN_MAX = 32`。它只提示“后续可能需要哪些文件 metadata”，预取提示本身不读取文件内容，也不替代 `agent_file_query()`；需要内容级证据时使用 `read_file_digest` 工具读取短预览和内容指纹。

当 Agent 通过 message 事件唤醒另一个 Agent 时，内核会把发送者当前可见的 metadata 预取提示复制到接收者的预取提示 ring，并给复制后的提示增加 `AGENT_FILE_PREFETCH_REASON_HANDOFF`。这样接收者可以直接调用 `agent_file_prefetch_snapshot()` 得到上游 Agent 的下一步候选，不需要从消息文本中解析策略字段。复制后的提示也会写入 span 预取提示总线，保留 source pid、target pid 和 span id，便于同一因果链上的 Agent 用统一接口查询本轮协作中的候选工件。

`agent_file_prefetch_snapshot(hints, max)` 返回当前可见提示数量，普通进程调用返回 `-1`，无 `META_READ` 能力的 Agent 返回 `AGENT_STATUS_DENIED`。`max=0` 时只返回数量，不复制记录；`max>0` 时按产生顺序复制最多 `max` 条。

`agent_file_prefetch_span_snapshot(hints, max)` 返回当前 Agent 的 `current_span_id` 对应的全局提示数量或记录。它只返回同一 span 下的提示；当前 Agent 尚未进入任何 span 时返回 0。普通进程调用返回 `-1`，无 `META_READ` 能力的 Agent 返回 `AGENT_STATUS_DENIED`。`max=0` 时只返回匹配数量；`max>0` 时按全局提示顺序复制最多 `max` 条。

`struct agent_file_prefetch_hint` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `sequence` | 预取提示自身的递增序号 |
| `source_sequence` | 触发本提示的 Context sequence |
| `span_id` | 触发本提示的因果链 span |
| `reason` | 生成原因 flags |
| `score` | 内核给出的简单排序分数 |
| `tick` | 生成提示时的 tick |
| `fs_generation` | 生成提示时的文件元数据代数 |
| `fid` | 建议后续关注的目标元数据 ID |
| `source_fid` | 触发提示的源元数据 ID |
| `source_pid` | 触发提示的 Agent pid |
| `target_pid` | 当前应该消费提示的 Agent pid |
| `plan` | 建议使用的查询计划，当前为 stage 索引 |
| `candidate_records` | 目标 stage 在同一 run 下的候选记录数 |
| `total_hits` | 当前可用于提示的目标记录数量 |
| `hit` | 目标文件的 `agent_file_hit` 快照 |

`reason` 使用以下 flags：

| flag | 含义 |
| --- | --- |
| `AGENT_FILE_PREFETCH_REASON_DEPENDENCY` | 由对象标签依赖关系产生 |
| `AGENT_FILE_PREFETCH_REASON_SAME_RUN` | source 和 target 属于同一 project/workflow/run |
| `AGENT_FILE_PREFETCH_REASON_PENDING` | target 当前状态为 pending |
| `AGENT_FILE_PREFETCH_REASON_STAGE_INDEX` | target 可通过 stage 索引定位 |
| `AGENT_FILE_PREFETCH_REASON_HANDOFF` | 由另一个 Agent 的 message 事件交接而来 |
| `AGENT_FILE_PREFETCH_REASON_SPAN_BUS` | 已写入同一 span 的全局预取提示总线 |

## 任务五 Agent Loop ABI

Agent Loop 使用每 Agent 16 槽 FIFO 事件队列。队列满时返回 `AGENT_STATUS_NO_SPACE`，不会覆盖旧事件。每个 Agent 最多注册 8 条 watch。相同 `event_type + filter` 会替换原 watch，`agent_unwatch()` 可删除匹配 watch 或清空全部 watch。有限 timeout 的 `agent_wait()` 会进入睡眠，由事件入队、heartbeat 到期、deadline 到期或 wait cancel 令牌唤醒；`agent_info.wait_loop_count` 用于观察该路径没有反复轮询。

当可运行队列中存在 Agent 时，调度器会读取 Agent 状态选择可运行任务；纯普通进程负载仍走原 FIFO 取队路径。当前策略为内核自适应策略，并允许 orchestrator 配置目标 Agent 的 weight、priority 和 budget：角色权重、配置优先级、事件队列、等待状态、timeout deadline、heartbeat 到期、等待时长、虚拟运行量和预算使用量都会影响分数。`agentsched_ucore` 通过 `agent_info`、`agent_sched_config()` 和 `agent_sched_snapshot()` 验证角色权重、受权调度配置、事件优先、调度原因记录和公平性计数。

`agent_sched_snapshot(records, max)` 返回当前 Agent 最近最多 16 次被调度时的原因记录。`max=0` 时不复制记录，只返回当前可见记录数。普通进程调用返回 `-1`。

`agent_trace_snapshot(records, max)` 返回当前 Agent 的运行轨迹短记录。它会把 Context Path 中最近最多 128 条摘要记录和调度器中最近最多 16 条调度原因记录按 tick 合并，最多返回 `AGENT_TRACE_MAX_RECORDS = 144` 条。`max=0` 时不复制记录，只返回当前可见记录数。普通进程调用返回 `-1`。该接口只整理当前 Agent 自己已经拥有的 Context 和调度数据，不创建新的全局日志，也不改变事件队列。

`agent_span_trace_snapshot(records, max)` 返回当前 Agent 所在 span 的系统级短记录。它读取同一个全局短记录 ring，但只返回 `record.span_id == current_span_id` 的记录；当前 Agent 尚未进入 span 时返回 0。普通进程调用返回 `-1`；缺少 `AUDIT_WRITE` 能力的 Agent 返回 `AGENT_STATUS_DENIED`；`max=0` 返回匹配数量；`max>0` 时按全局 sequence 顺序复制最多 `max` 条。该接口不接受调用者传入 span id，避免把非 orchestrator Agent 扩大成任意全局审计查询者。

`agent_audit_snapshot(records, max)` 返回系统级 Agent 审计短记录。内核维护固定容量 `AGENT_AUDIT_MAX_RECORDS = 512` 的全局 ring，记录 Context 追加、事件入队、事件消费、Agent 调度 dispatch 和预取提示交接。每条记录还保存 `prev_hash` 和 `record_hash`：第一条记录的 `prev_hash=0`，后续记录的 `prev_hash` 等于上一条审计记录的 `record_hash`。普通进程调用返回 `-1`；不具备 `ORCHESTRATE` 的 Agent 返回 `AGENT_STATUS_DENIED`；`max=0` 返回当前可见记录数；`max>0` 时按全局 sequence 顺序复制最近记录。该接口是内存态观测能力，不写磁盘，也不替代完整 `context_detail()`。

`agent_audit_query(filter, records, max)` 在同一组全局短记录上执行过滤查询。`filter=NULL` 表示不过滤；`filter->flags` 决定哪些字段参与匹配，可按 `start_sequence`、`span_id`、`kind`、`pid`、`source_pid`、`target_pid`、`role`、`tool_id`、`event_type` 和 `status` 过滤。`max=0` 返回匹配数量，不复制记录；`max>0` 时复制最多 `max` 条匹配记录。权限和错误语义与 `agent_audit_snapshot()` 相同。

`agent_ledger_snapshot(summary)` 返回全局运行账本摘要。它不复制审计明细，只返回 `oldest_sequence`、`latest_sequence`、`visible_records`、`total_records`、`dropped_records`、`ledger_hash`、Context/event/sched/prefetch 分类计数、`timeline_total` 和 `observe_epoch`。其中 `ledger_hash` 等于最新审计记录的 `record_hash`。普通进程调用返回 `-1`，非 orchestrator Agent 返回 `AGENT_STATUS_DENIED`，空指针返回 `AGENT_STATUS_BAD_PARAM`。该接口适合最终页面或演示脚本快速确认“当前展示的全局短记录仍属于同一条内核维护的运行事实链”。

`agent_timeline_snapshot(records, max)` 返回统一 timeline 记录。该接口把当前 Agent 可见的四类短记录规范化为 `struct agent_timeline_record`：

- 当前 Agent 的 Context Path 摘要；
- 当前 Agent 最近 16 次调度原因；
- 当前 Agent 可见的审计记录：orchestrator 可见全局审计，其他具备 `AUDIT_WRITE` 的 Agent 只可见当前 span；
- 当前 Agent 自己的 metadata 预取提示。

普通进程调用返回 `-1`。`max=0` 返回当前可见记录总数，不复制记录；`max>0` 时按 tick 输出最多 `max` 条。全局审计 ring 内部按 sequence 维护，timeline 导出时会对可见审计记录按 tick 选择，避免多 Agent 并发记录导致页面时间线乱序。该接口不替代 `context_detail()`，也不保存完整 raw 请求/响应；它面向最终演示页面和研究平台运行详情。

`agent_timeline_query(filter, records, max)` 在同一组当前可见记录上执行内核侧过滤。`filter=NULL` 表示不过滤，语义等同于 `agent_timeline_snapshot()`。`max=0` 返回匹配数量，不复制记录；`max>0` 时按 tick 复制最多 `max` 条匹配记录。普通进程调用返回 `-1`。过滤只能缩小当前 Agent 已经有权看到的记录集合：普通 Agent 不能通过 filter 读取其他 span 的审计记录，非 orchestrator Agent 也不能把 source mask 设为 audit 后获得全局审计。

`AGENT_TIMELINE_FILTER_AFTER_CURSOR` 用于增量读取。调用者把已经处理过的最后一条 record 的 `tick`、`source` 和 `sequence` 填入 `after_tick`、`after_source` 和 `after_sequence`，内核只返回严格晚于该游标的记录。比较顺序与 timeline 导出顺序一致：先比较 `tick`，同一 tick 下按 `source` 顺序比较，再比较来源内部 `sequence`。它比只使用 `start_tick` 更适合页面刷新，因为同一个 tick 中可能存在多条不同来源记录。

`agent_timeline_wait(filter, timeout_ticks)` 使用同一套 timeline filter，但只返回匹配记录数，不复制记录。若当前已经有匹配记录，接口立即返回数量；若没有匹配记录，当前 Agent 保存 filter 并进入睡眠。Context、调度、审计或预取提示写入时，内核把新事实转换成 `agent_timeline_record`，并在内核里用等待者保存的完整 filter 判断是否匹配；例如只等待 Context 的 Agent 不会因为 Audit 记录写入而增加 timeline wait 唤醒计数，只等待 TIMER 事件的 Agent 也不会被 MESSAGE 审计记录唤醒。`timeout_ticks >= 0` 表示有限等待，超时返回 `AGENT_STATUS_TIMEOUT`；`timeout_ticks == -1` 表示无限等待。返回正数后，调用者用同一个 filter 调用 `agent_timeline_query()` 读取记录。普通进程调用返回 `-1`，非法 filter flags 返回 `AGENT_STATUS_BAD_PARAM`。该接口用于最终页面或 Agent worker 的事件驱动刷新，避免用户态循环调用 `agent_timeline_query()`。

`agent_timeline_read(filter, records, max, timeout_ticks)` 使用与 `agent_timeline_wait()` 相同的等待和唤醒规则，但在匹配记录出现后立即把最多 `max` 条记录复制到 `records`。如果当前已经有匹配记录，它不睡眠并直接复制；如果当前没有匹配记录，它先睡眠等待，醒来后在同一次 syscall 中复制记录。`max=0` 时只返回匹配数量，不复制记录；`max>AGENT_TIMELINE_MAX_RECORDS` 返回 `AGENT_STATUS_BAD_PARAM`；坏输出指针在睡眠前返回 `-1`。这个接口用于最终 Web UI 或 Agent worker 的热路径，避免 `agent_timeline_wait()` 返回后再调用 `agent_timeline_query()` 的第二次 syscall 和中间状态变化。

`struct agent_timeline_record` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `source` | 规范化来源：`CONTEXT`、`SCHED`、`AUDIT`、`PREFETCH` |
| `kind` | 来源内部类型；Context/Sched 使用 trace kind，Audit 使用 audit kind，Prefetch 使用 query plan |
| `tick` | 产生该条记录时的 tick 计数值 |
| `sequence` | 来源内部序号：Context sequence、dispatch count、audit sequence 或 prefetch sequence |
| `cause_sequence` / `span_id` | 因果字段 |
| `pid` / `tid` | 产生记录的进程和线程；无线程信息时为 0 |
| `source_pid` / `target_pid` | 事件或提示的来源与目标；单 Agent 记录中通常等于 `pid` |
| `role` / `loop_state` | 记录产生时的 Agent 角色和 Loop 状态 |
| `tool_id` / `event_type` / `status` | 工具、事件类型和状态码 |
| `value0` / `value1` / `value2` | 来源相关的数值槽，例如调度分数、事件 ID、source fid、target fid、候选记录数 |
| `flags` | Context flags、调度 reason flags、审计 flags 或预取 reason flags |
| `text` | 32 字节短摘要 |

`struct agent_timeline_filter` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `flags` | 使用 `AGENT_TIMELINE_FILTER_*` 位选择参与匹配的字段 |
| `source_mask` | 配合 `AGENT_TIMELINE_FILTER_SOURCE_MASK`，可选择 Context、Sched、Audit、Prefetch 来源 |
| `start_tick` | 配合 `AGENT_TIMELINE_FILTER_START_TICK`，只返回 `tick >= start_tick` 的记录 |
| `span_id` | 配合 `AGENT_TIMELINE_FILTER_SPAN_ID`，只返回指定 span 的记录 |
| `require_flags` | 配合 `AGENT_TIMELINE_FILTER_FLAGS_ALL`，要求记录 flags 至少包含这些位 |
| `after_tick` / `after_source` / `after_sequence` | 配合 `AGENT_TIMELINE_FILTER_AFTER_CURSOR`，只返回严格晚于该三元游标的记录 |
| `kind` | 配合 `AGENT_TIMELINE_FILTER_KIND`，匹配来源内部类型 |
| `pid` / `source_pid` / `target_pid` | 匹配产生记录的 Agent、事件来源 Agent 或事件目标 Agent |
| `role` | 匹配 Agent 角色 |
| `tool_id` | 匹配工具 ID |
| `event_type` | 匹配事件类型 |
| `status` | 匹配状态码 |

`agent_provenance_snapshot(edges, max)` 返回当前 Agent 可见的因果边。`max=0` 返回匹配到的边数量，不复制记录；`max>0` 时复制最多 `max` 条。普通进程调用返回 `-1`。该接口不扩大权限：每个 Agent 都能看到自己的 Context 因果边和本地预取提示边；审计边遵循与 timeline 相同的可见规则，orchestrator 可见全局审计边，其他具备审计能力的 Agent 只可见当前 span 内的审计边。

`struct agent_provenance_edge` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `kind` | 边来源：`CONTEXT`、`AUDIT` 或 `PREFETCH` |
| `source_type` / `target_type` | 源节点和目标节点类型：Context、Audit 或 Prefetch |
| `source_sequence` / `target_sequence` | 源节点和目标节点在对应来源中的 sequence |
| `span_id` | 该因果边所属 span |
| `tick` | 目标节点产生时的 tick |
| `source_pid` / `target_pid` | 源 Agent 和目标 Agent；单 Agent Context 边通常相同 |
| `role` | 目标记录产生时的 Agent 角色 |
| `tool_id` / `event_type` / `status` | 关联工具、事件类型和状态码 |
| `value0` / `value1` / `value2` | 来源相关数值，例如文件 fid、候选记录数或线程 id |
| `flags` | Context flags、audit flags 或 prefetch reason flags |
| `text` | 32 字节短摘要 |

`struct agent_sched_record` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `tick` | 记录产生时的 tick |
| `dispatch_count` | 本 Agent 第几次被调度 |
| `score` | 本次调度评分 |
| `reason_flags` | 参与本次评分的原因 flags |
| `event_queue_count` | 被调度前待消费事件数量 |
| `ready_age` | 进入可运行状态后的等待 tick |
| `deadline_delta` | timeout deadline 距当前 tick 的差值；无 deadline 时为 0 |
| `heartbeat_due` | heartbeat 是否到期 |
| `vruntime` | 调度前虚拟运行量 |
| `budget_used` | 调度前预算使用量 |
| `pid` / `tid` | 被调度的进程和线程 |
| `role` / `loop_state` / `weight` / `priority` | 被调度时的 Agent 角色、Loop 状态、角色权重和调度优先级 |

`agent_sched_config(config)` 由 orchestrator 调用，用于配置目标 Agent 的调度参数。普通进程调用返回 `-1`，非 orchestrator Agent 返回 `AGENT_STATUS_DENIED`。结构体字段：

| 字段 | 含义 |
| --- | --- |
| `update_mask` | 使用 `AGENT_SCHED_CONFIG_*` 位选择要更新的字段 |
| `target_pid` | 目标 Agent pid |
| `policy` | 当前支持 `AGENT_SCHED_POLICY_ADAPTIVE` |
| `weight` | 权重，合法范围 `AGENT_SCHED_WEIGHT_MIN..AGENT_SCHED_WEIGHT_MAX` |
| `priority` | 额外优先级，合法范围 `AGENT_SCHED_PRIORITY_MIN..AGENT_SCHED_PRIORITY_MAX` |
| `budget` | 调度预算，合法范围 `AGENT_SCHED_BUDGET_MIN..AGENT_SCHED_BUDGET_MAX` |

配置接口只更新 `update_mask` 指定的字段。参数越界返回 `AGENT_STATUS_BAD_PARAM`，目标不存在或目标不是 Agent 返回 `AGENT_STATUS_NOT_FOUND`。`agentsched_ucore` 会把一个 sentinel Agent 配置为 `weight=150 priority=20 budget=3`，并检查后续调度记录中出现 `AGENT_SCHED_REASON_PRIORITY`。

调度原因 flags：

| flag | 含义 |
| --- | --- |
| `AGENT_SCHED_REASON_ROLE_WEIGHT` | 角色权重参与基础分 |
| `AGENT_SCHED_REASON_EVENT_QUEUE` | 事件队列中有待消费事件 |
| `AGENT_SCHED_REASON_WAITING` | Agent 处于等待状态 |
| `AGENT_SCHED_REASON_DEADLINE_NEAR` | timeout deadline 接近 |
| `AGENT_SCHED_REASON_DEADLINE_NOW` | timeout deadline 已到或马上到 |
| `AGENT_SCHED_REASON_HEARTBEAT_DUE` | heartbeat 到期 |
| `AGENT_SCHED_REASON_BUDGET_USED` | 当前调度预算已用满并产生扣分 |
| `AGENT_SCHED_REASON_VRUNTIME` | 虚拟运行量产生扣分 |
| `AGENT_SCHED_REASON_READY_AGE` | 进入可运行队列后的等待时间参与评分 |
| `AGENT_SCHED_REASON_PRIORITY` | orchestrator 配置的 priority 参与评分 |

`struct agent_trace_record` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `kind` | 记录来源：`AGENT_TRACE_KIND_CONTEXT` 或 `AGENT_TRACE_KIND_SCHED` |
| `tick` | 记录产生时的 tick |
| `sequence` | Context 记录的 sequence，或调度记录的 dispatch count |
| `cause_sequence` / `span_id` | Context 因果字段；调度记录中为 0 |
| `value0` / `value1` / `value2` | Context 记录的数值槽；调度记录中分别为 score、event queue count、vruntime |
| `flags` | Context record flags，或调度 reason flags |
| `tool_id` / `status` | Context 工具 ID 和结果状态；调度记录中为 0 |
| `role` / `loop_state` | 记录产生时的 Agent 角色和 Loop 状态 |
| `pid` / `tid` | 对应进程和线程 |
| `text` | Context 短结果或短 payload；调度记录中为 `sched` |

`struct agent_audit_record` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `sequence` | 全局审计序号，单调递增 |
| `kind` | 记录来源：Context 追加、事件入队、事件消费、调度 dispatch 或预取提示交接 |
| `tick` | 记录产生时的 tick |
| `prev_hash` | 追加本条审计记录前的全局账本链尾 hash |
| `record_hash` | 由 prev_hash、sequence、tick、cause/span、角色、来源、状态、数值槽和短文本计算得到的本条记录 hash |
| `pid` / `source_pid` / `target_pid` | 产生记录的 Agent、事件来源和事件目标 |
| `agent_id` / `role` / `loop_state` | 记录产生时的 Agent 身份、角色和 Loop 状态 |
| `tool_id` / `event_type` / `status` | 工具 ID、事件类型和状态码 |
| `cause_sequence` / `span_id` | 对应 Context 或事件的因果字段 |
| `value0` / `value1` / `value2` | 按来源解释的数值槽；Context 记录保留工具结果数值槽，事件记录保留事件 ID/corr ID/目标 pid，调度记录保留分数和队列信息，预取交接记录中分别是 source sequence、source fid、target fid |
| `flags` | Context record flags、调度 reason flags 或预取 reason flags |
| `text` | 32 字节短摘要，例如工具结果、事件 payload、`sched` 或预取目标 stage |

当前 `kind` 取值如下：

| 取值 | 含义 |
| --- | --- |
| `AGENT_AUDIT_KIND_CONTEXT` | Context 追加 |
| `AGENT_AUDIT_KIND_EVENT_ENQUEUE` | 事件入队 |
| `AGENT_AUDIT_KIND_EVENT_CONSUME` | 事件消费 |
| `AGENT_AUDIT_KIND_SCHED` | Agent 调度 dispatch |
| `AGENT_AUDIT_KIND_PREFETCH` | message 事件触发的预取提示交接 |

`struct agent_audit_filter` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `flags` | 过滤开关，使用 `AGENT_AUDIT_FILTER_*` 位 |
| `start_sequence` | 设置 `START_SEQUENCE` 后，只返回 sequence 不小于该值的记录 |
| `span_id` | 设置 `SPAN_ID` 后，只返回同一 span 的记录 |
| `kind` | 设置 `KIND` 后，只返回指定来源类型 |
| `pid` / `source_pid` / `target_pid` | 设置对应 flag 后按产生记录的 Agent、事件来源或事件目标过滤 |
| `role` | 设置 `ROLE` 后按 Agent 角色过滤 |
| `tool_id` / `event_type` / `status` | 设置对应 flag 后按工具、事件类型或状态码过滤 |

`struct agent_ledger_summary` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `version` | 当前为 `AGENT_LEDGER_VERSION = 1` |
| `oldest_sequence` / `latest_sequence` | 当前可见审计记录的最早和最新 sequence |
| `visible_records` | 当前 ring 中可复制的审计记录数，最多 512 |
| `total_records` | 启动后累计写入的审计记录数 |
| `dropped_records` | 因 ring 容量被覆盖的旧记录数 |
| `ledger_hash` | 当前全局审计链尾 hash，等于最新可见审计记录的 `record_hash` |
| `context_records` / `event_records` / `sched_records` / `prefetch_records` | 按来源累计的记录数 |
| `timeline_total` | 可作为 timeline 候选来源的全局记录总量 |
| `observe_epoch` | 观测 epoch，Context、审计、调度或预取提示写入时递增 |

`struct agent_event` 是事件等待和唤醒结构：

| 字段 | 含义 |
| --- | --- |
| `type` | 事件类型，如 `AGENT_EVENT_FILE_STATUS` 或 `AGENT_EVENT_MESSAGE` |
| `source_pid` / `target_pid` | 事件来源和目标 |
| `status` | 等待结果状态 |
| `event_id` | 内核分配的事件 ID |
| `tick` | 投递 tick |
| `corr_id` | 可选相关 ID，用于动作、消息、LLM 请求或测试 |
| `cause_sequence` | 触发该事件的前序 Context sequence；跨 Agent 消息时结合 source pid 和 span 解释 |
| `span_id` | 事件所属因果链 ID，目标 Agent 消费事件后会继承该 span |
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
| `AGENT_EVENT_LLM_DONE` | 用户态 LLM Relay 返回解释或摘要；内核只负责事件、Context、timeline 和审计记录 |
| `AGENT_EVENT_DASHBOARD_EXPORT` | 可视化导出完成；当前作为最终成品预留事件 |
| `AGENT_EVENT_CANCELLED` | `agent_wait_cancel()` 产生的等待取消事件 |

`agent_heartbeat_stop()` 是用户态便利 wrapper，内部调用 `agent_heartbeat(0)`。heartbeat 到期产生 `AGENT_EVENT_TIMER` 时仍需匹配 TIMER watch；删除 TIMER watch 后，heartbeat 不会投递可消费 TIMER 事件。

`agent_wait_cancel(pid, reason)` 是 Agent-only 控制接口。调用者必须具备 `MESSAGE_SEND` 或 `ORCHESTRATE`。内核给目标 Agent 写入一次性取消令牌并唤醒目标；如果目标已经在 `agent_wait()` 中睡眠，会立即返回 `AGENT_STATUS_CANCELLED`；如果取消令牌先到达，目标下一次 `agent_wait()` 会立即返回。返回的事件类型为 `AGENT_EVENT_CANCELLED`，payload 保存短 reason，cause/span 继承调用者当前 Context 状态。普通进程调用返回 `-1`，目标不存在返回 `AGENT_STATUS_NOT_FOUND`，目标已有未消费取消令牌时返回 `AGENT_STATUS_DUPLICATE`。

## 上下文路径接口：Context Path

| 接口 | 行为 |
| --- | --- |
| `context_push(record)` | 追加手动节点，内核分配新的 sequence |
| `context_query(start_sequence, out, max)` | 从 `start_sequence` 起按时间顺序复制仍可见记录；`start_sequence=0` 表示从最早可见记录开始 |
| `context_snapshot(header, records, max)` | 一次返回 header 和按时间顺序排列的可见 records |
| `context_detail(sequence, out)` | 返回最近 128 条完整详情中指定 sequence 对应的 `agent_op`、`agent_result` 和 flags |
| `context_rollback(sequence)` | 回滚到仍可见 sequence；不存在时返回 `AGENT_STATUS_NOT_FOUND` |
| `context_clear()` | 清空记录、计数和 latest response |

`struct agent_context_record` 保存工具 ID、状态码、sequence、request_id、cause_sequence、span_id、数值槽、tick、flags、`prev_hash`、`record_hash`，以及 16 字节 payload/result 短文本摘要；工具名称可通过 `agent_tool_list()` 按 `tool_id` 解释。它不是完整 raw 请求/响应日志，不保存全部参数键名、参数类型或完整长文本。最近 128 条完整详情保存在内核 PCB 的 detail ring 中，通过 `context_detail()` 查询，不放在用户 Context 页内。超过 128 条记录时，Context Path 按 FIFO 覆盖旧记录，并更新 `oldest_sequence`、`latest_sequence` 和 `dropped_records`。

Context v6 增加轻量因果链字段和完整性链字段：

| 字段 | 含义 |
| --- | --- |
| `cause_sequence` | 当前记录由哪条前序 Context record 触发；0 表示本链路根节点 |
| `span_id` | 当前链路 ID，用于把工具调用、事件投递和事件消费串起来 |
| `prev_hash` | 追加本条记录前的 Context 链尾 hash；第一条记录为 0 |
| `record_hash` | 由 prev_hash、sequence、cause/span、工具 ID、状态、数值槽和短文本摘要计算得到的记录 hash |

`struct agent_context_header.latest_record_hash` 暴露当前 Context 链尾 hash。`context_rollback(sequence)` 会把链尾 hash 回滚到目标记录的 `record_hash`，`context_clear()` 会把链尾 hash 清零。这个字段用于表达当前可见路径的顺序关系由内核维护，不依赖用户态日志拼接。

内核写入自动工具记录时，会使用当前 Agent 的 `current_cause_sequence` 和 `current_span_id`；写入完成后，当前 cause 更新为新记录的 sequence。`context_push(record)` 可以显式给出 cause/span，用于把手动记录接入同一链路。跨 Agent 消息或文件事件会携带 source Agent 的 cause/span，目标 Agent 在 `agent_wait()` 消费事件后继承该 span，后续工具调用继续写入同一链路。该机制用于内核内可信审计和演示追踪，不等同于持久化密码学保证。

Context record flags：

| flag | 含义 |
| --- | --- |
| `AGENT_CONTEXT_RECORD_F_SYSTEM` | 内核自动工具或系统事件记录 |
| `AGENT_CONTEXT_RECORD_F_MANUAL` | `context_push()` 手动记录 |
| `AGENT_CONTEXT_RECORD_F_TRUNCATED` | payload 或 result 短摘要发生截断 |
