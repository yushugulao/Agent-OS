# 任务三：上下文路径管理

本文是 [design.md](design.md) 的任务三细节附录，重点说明 Context Path 的布局、写入、查询、回滚、淘汰和可信性设计。

## 目标

任务三要求系统维护 Agent 多轮工具调用上下文，使 Agent 能看到自己历史调用路径，并能在路径过长时自动淘汰旧记录。

uCore 分支实现的是“128 条固定容量短文本摘要路径 + 最近 128 条完整详情 + 用户自管 cache + cause/span 因果字段 + prev/record hash 完整性链”。摘要 record 用于快速展示和高频查询，`context_detail(sequence, out)` 用于查看内核 PCB 中保存的完整 `agent_op`、`agent_result` 和记录 flags。用户自管 cache 位于 Context 尾部，用于 Agent 自己保存临时策略状态，不进入内核可信历史。

## Context 布局

Agent Context 共 6 页：

| 区域 | 偏移 | 内容 |
| --- | ---: | --- |
| header | 0 | `struct agent_context_header` |
| latest | `sizeof(struct agent_context_header)` | `struct agent_result` |
| records | 4096 | `struct agent_context_record[128]` |
| user cache | `header.user_cache_offset` | Agent 自管缓存区，当前测试输出为 offset 21504、size 3072 |

`struct agent_context_header` 关键字段：

| 字段 | 说明 |
| --- | --- |
| `magic` | Context magic |
| `version` | Context layout 版本 |
| `capacity` | 最大记录数，当前为 128 |
| `count` | 当前有效记录数 |
| `head` | 下一次写入槽位 |
| `total_calls` | 总工具调用数 |
| `oldest_sequence` | 当前最早可见 sequence |
| `latest_sequence` | 当前最新 sequence |
| `dropped_records` | 因 FIFO 淘汰的记录数 |
| `rollback_count` | 成功 rollback 次数 |
| `current_span_id` | 当前 Agent 正在延续的因果链 span |
| `current_cause_sequence` | 下一条自动记录默认指向的前序 sequence |
| `latest_record_hash` | 当前 Context 完整性链的链尾 hash |
| `provenance_edges` | 当前 Agent 已记录的非 root 因果关系数量 |
| `latest_response_offset` | latest result 偏移 |
| `records_offset` | record 区偏移 |
| `user_cache_offset` | 用户自管 cache 起点 |
| `user_cache_size` | 用户自管 cache 大小 |

`struct agent_context_record` 关键字段：

| 字段 | 说明 |
| --- | --- |
| `sequence` | 内核分配的递增序号 |
| `request_id` | 用户请求 ID |
| `cause_sequence` | 触发当前记录的前序 sequence；0 表示根节点 |
| `span_id` | 当前因果链 ID |
| `arg0` | 第一个数值参数摘要 |
| `value0/value1/value2` | 工具结果数值槽 |
| `tick` | 写入时 tick |
| `tool_id` | 工具 ID |
| `status` | 工具结果状态 |
| `flags` | `SYSTEM`、`MANUAL`、`TRUNCATED` 等记录标志 |
| `prev_hash` | 本条记录追加前的 Context 链尾 hash |
| `record_hash` | 本条记录由内核计算的完整性 hash |
| `payload` | 16 字节 payload 摘要 |
| `result` | 16 字节 result 摘要 |

最近 128 条完整详情保存在内核 PCB 的 detail ring 中，不放在用户 Context 页内。`struct agent_context_detail` 保存：

| 字段 | 说明 |
| --- | --- |
| `sequence` | 与摘要 record 相同的 sequence |
| `flags` | 记录来源和截断信息 |
| `op` | 完整 `struct agent_op` |
| `result` | 完整 `struct agent_result` |

## shadow 权威历史

Context 使用双份数据：

| 副本 | 用途 |
| --- | --- |
| kernel shadow | 内核权威历史，用户态无法直接修改 |
| user mirror | 用户态高速读取镜像 |

所有写入先进入 kernel shadow，再同步到 user mirror。`context_query()` 和 `context_snapshot()` 都读取 shadow。用户态即使直接写坏镜像，也不能伪造内核返回的历史。

`agentfinal_ucore` 的篡改和 cache 测试：

1. 用户态把 user mirror 中第一条记录 sequence 改成 9999。
2. 调用 `context_snapshot()`。
3. snapshot 返回原始 sequence。
4. snapshot 同步 user mirror，使直接读也恢复为原始内容；
5. 用户态向 `user_cache_offset` 写入 `cache-ok`；
6. 再次调用 `context_snapshot()`；
7. cache 内容仍然保留，证明 snapshot 只刷新内核管理区，不覆盖用户自管 cache。

对应输出：

```text
agentfinal_ucore: tamper_protected=1
agentfinal_ucore: user_cache_preserved=1 offset=21504 size=3072
```

## 因果链记录

Context v6 会把工具调用、手动记录和事件消费串成轻量因果链，并为每条记录维护完整性链：

1. 第一条自动工具记录的 `cause_sequence` 为 0，表示 root。
2. 后续自动工具记录默认指向上一条 Context sequence。
3. 同一 Agent 连续调用使用同一个 `span_id`。
4. `context_push(record)` 可以显式传入 cause/span，把手动记录接到指定链路。
5. 工具触发的事件会携带 source Agent 的 cause/span。
6. 目标 Agent 成功 `agent_wait()` 后继承事件 span，后续工具调用继续这条链路。
7. 第一条记录的 `prev_hash` 为 0。
8. 后续记录的 `prev_hash` 等于上一条记录的 `record_hash`。
9. header 中的 `latest_record_hash` 等于最新记录的 `record_hash`。

这使 Context Path 不只保存“发生了什么”，还保存“为什么接着发生”，并能验证当前可见路径的相邻顺序由内核维护。跨 Agent 事件中的 `cause_sequence` 需要结合事件来源 pid 和 `span_id` 解释；它用于当前系统内的运行追踪，不是磁盘持久化日志。

`agentfinal_ucore` 会检查首条记录是 root，第二条记录指向第一条记录，span 连续，header 中的当前 cause/span 与最新状态一致；同时检查每条记录的 prev/hash 链接关系，并输出：

```text
agentfinal_ucore: causal_context=1 first_cause=0 next_cause=1 span=1 edges=63
agentfinal_ucore: context_integrity=1 first_hash=... latest_hash=...
```

## 写入路径

每次工具调用完成后，内核执行：

1. 给本次调用分配 sequence。
2. 把 `struct agent_result` 写入 latest 区。
3. 构造 `struct agent_context_record`。
4. 写入当前 head 指向的 record 槽。
5. 更新 count、head、oldest、latest、dropped。
6. 同步用户镜像。

手动 `context_push()` 使用同一个 sequence 流，保证手动记录和工具调用记录按同一顺序排列。

## 查询接口

| 接口 | 说明 |
| --- | --- |
| `context_query(start_sequence, out, max)` | 从指定 sequence 开始返回可见记录；`start_sequence=0` 表示从最早可见记录开始 |
| `context_snapshot(header, records, max)` | 一次返回 header 和有序 records，是推荐读取方式 |
| `context_detail(sequence, out)` | 查询仍在内核 detail ring 中的最近 128 条完整请求/响应 |
| `agent_trace_snapshot(records, max)` | 把 Context 摘要和调度原因合并为当前 Agent 的运行轨迹短记录 |
| `agent_span_trace_snapshot(records, max)` | 当前 Agent 查询自己所在 span 的系统级短记录 |
| `agent_timeline_snapshot(records, max)` | 把当前 Agent 可见的 Context、调度、审计和预取提示导出为统一 timeline |
| `agent_timeline_query(filter, records, max)` | 在统一 timeline 上执行内核侧过滤查询，支持按上一条记录游标继续读取 |
| `agent_timeline_wait(filter, timeout_ticks)` | 等待当前可见 timeline 出现匹配记录；命中后再用同一 filter 查询 |
| `agent_timeline_read(filter, records, max, timeout_ticks)` | 等待匹配 timeline 记录，并在同一次 syscall 中复制记录 |
| `agent_provenance_snapshot(edges, max)` | 把当前 Agent 可见的 Context、审计和预取提示导出为因果边 |
| `agent_audit_snapshot(records, max)` | orchestrator 查询多 Agent 场景中的全局 Context、事件、调度和预取交接短记录 |
| `agent_audit_query(filter, records, max)` | orchestrator 按 kind、span、目标事件、预取 source/target 或起始 sequence 过滤全局短记录 |
| `agent_ledger_snapshot(summary)` | orchestrator 查询全局短记录的 sequence 范围、分类计数和链尾 hash |
| `agent_file_prefetch_span_snapshot(hints, max)` | 当前 Agent 按自己的 span 查询跨 Agent metadata 预取提示 |
| `context_rollback(sequence)` | 回滚到仍可见 sequence |
| `context_clear()` | 清空记录和元信息 |

`context_snapshot()` 是最终测试和演示的主路径，因为它一次 syscall 就能拿到 header 和多条记录。`context_detail()` 用于在需要完整审计证据时补充摘要 record 中没有保存的完整参数和结果。

`agent_trace_snapshot()` 面向演示和调试。它不改变 Context Path，只读取当前 Agent 的 Context 摘要和最近调度原因，按 tick 合并为最多 144 条短记录。`agentfinal_ucore` 会检查结果中同时存在 Context、调度和事件等待记录：

```text
agentfinal_ucore: runtime_trace=1 records=... context=1 sched=1 wait=1
```

`agent_span_trace_snapshot()` 面向当前 span。它读取全局短记录 ring，但只返回当前 Agent 的 `current_span_id` 对应记录；调用者不能传入任意 span id。这样 investigator 或 recovery 可以在协作过程中直接看到当前 span 里的 Context、事件和预取交接摘要，而不需要等待 orchestrator 汇总。`agentfinal_ucore` 会检查自唤醒事件之后当前 span 同时包含 Context 和事件记录：

```text
agentfinal_ucore: span_trace=1 records=... context=1 event=1
```

`agent_timeline_snapshot()` 面向最终展示和平台导出。它不改变 Context Path，也不新增新的权威历史，而是把当前 Agent 可见的 Context、调度、审计和预取提示转换成 `struct agent_timeline_record`。普通 Agent 只能看到自身 Context、调度、预取提示以及当前 span 的审计短记录；orchestrator 可以额外看到全局审计短记录。`agentfinal_ucore` 会检查统一 timeline 同时包含四类来源：

```text
agentfinal_ucore: unified_timeline=1 records=... context=1 sched=1 audit=1 prefetch=1
agentfinal_ucore: timeline_query=1 audit=213 recent=281 cursor=177
agentfinal_ucore: timeline_wait=1 timeout=-7 source_gate=1 event_gate=1 wake=1 query=1 read=1 sleeps=1 wakeups=1
```

`agent_timeline_query()` 用同一套权限规则，但允许在内核中按 source mask、起始 tick、span、kind、pid、tool、event、status、flags 和 after-cursor 过滤。`start_tick` 适合粗粒度读取某个 tick 之后的记录；after-cursor 使用上一条已读记录的 `tick/source/sequence`，只返回严格排在它之后的记录，适合最终页面增量刷新。过滤发生在当前 Agent 已可见的集合内，不能让普通 Agent 读取全局审计。

`agent_timeline_wait()` 复用同一套 filter。当前没有匹配记录时，Agent 进入睡眠；Context、审计、调度或预取提示写入后，内核递增 observe epoch，把新事实转换成统一 `agent_timeline_record`，再用等待者保存的完整 filter 判断是否唤醒。接口返回正数表示已有匹配记录，调用者随后用同一个 filter 调 `agent_timeline_query()` 读取；返回 `AGENT_STATUS_TIMEOUT` 表示有限等待到期。`agentfinal_ucore` 会验证 source=Context 的未来记录等待能 timeout，也会验证 heartbeat audit 不会误增加 Context-only 等待的 timeline wake 计数，MESSAGE 条件不会被 TIMER audit 唤醒，并验证 TIMER audit 写入能唤醒 source=Audit 且 event=TIMER 的等待。

`agent_timeline_read()` 是 wait+query 的合并接口。它复用同一个 filter 和同一套权限裁剪；已有匹配记录时直接复制，没有匹配记录时睡眠等待，醒来后在同一次 syscall 中复制记录。该接口避免页面或 Agent worker 先 `agent_timeline_wait()` 再 `agent_timeline_query()` 的两次陷入，也避免 wait 返回与 query 执行之间出现新的记录导致读数不一致。

`agent_provenance_snapshot()` 面向因果图展示。它不按时间排序，而是输出 `source_sequence -> target_sequence` 形式的因果边。当前 Agent 自己的 Context Path 会形成 Context 到 Context 的边；可见审计记录会形成 Context 到 Audit 的边；文件查询生成的预取提示会形成 Context 到 Prefetch 的边。该接口用于最终页面解释“哪个工具调用、事件或提示导致了后续动作”，和 timeline 的时间视图互补。`agentfinal_ucore` 会检查 Context 边和 audit 边：

```text
agentfinal_ucore: provenance_graph=1 edges=126 context=1 audit=1
```

`agent_audit_snapshot()` 面向综合演示中的 orchestrator。它不替代单个 Agent 的 `context_snapshot()` 和 `context_detail()`，而是把多个 Agent 产生的 Context 追加、事件入队、事件消费、调度 dispatch 和预取提示交接摘要放入全局短记录，便于讲清楚多 Agent 协作过程。每条全局短记录都保存 `prev_hash` 和 `record_hash`，`agent_ledger_snapshot()` 返回当前可见 sequence 范围、累计写入数、已淘汰数、分类计数和链尾 hash。`agent_audit_query()` 在同一组全局短记录上执行过滤查询，可按 kind、span、目标进程、事件类型和起始 sequence 等条件取证。`labdemo_ucore` 会检查全局记录中同时出现 sentinel、investigator、recovery：

```text
labdemo_ucore: global_audit=1 records=... agents=3 context=1 event=1 sched=1 prefetch=1
labdemo_ucore: audit_query=1 kind=... span=... event=2 prefetch=... start=...
```

span 同时用于文件预取提示。文件查询产生的 metadata 预取提示会进入当前 Agent 本地 ring；如果提示带有非零 span，内核还会写入全局 span 提示总线。目标 Agent 消费 message 事件并继承 span 后，可以调用 `agent_file_prefetch_span_snapshot()` 查询这条因果链中的提示，看到 source pid、target pid 和目标工件。这让跨 Agent 协作不需要只依赖消息文本或串口日志来拼接上游提示来源。

## FIFO 淘汰

当前容量固定为 128 条。超过容量时，系统覆盖最旧记录，并增加 `dropped_records`。

`agentfinal_ucore` 写入 192 条记录后检查：

| 字段 | 期望 |
| --- | ---: |
| `count` | 128 |
| `oldest_sequence` | 66 |
| `latest_sequence` | 193 |
| `dropped_records` | 65 |

对应输出：

```text
agentfinal_ucore: fifo oldest=66 latest=193 dropped=65
```

## 性能路径

Context Path 有三种读取方式：

| 方式 | 适用场景 |
| --- | --- |
| 直接读 user mirror | 高频读取 latest 状态或可信 Agent 自身调试 |
| `context_query()` | 查询少量历史记录 |
| `context_snapshot()` | 批量读取当前可见历史，推荐展示和演示使用 |
| `agent_trace_snapshot()` | 展示 Context 与调度原因的合并短视图 |
| `agent_span_trace_snapshot()` | 展示当前 span 的 Context、事件、调度和预取交接短记录 |
| `agent_timeline_snapshot()` | 用同一结构展示当前 Agent 可见的 Context、调度、审计和预取提示 |
| `agent_timeline_query()` | 减少最终页面按来源、tick、span 或上一条已读记录查看时的无关记录复制 |
| `agent_timeline_wait()` | 让页面或 Agent worker 在没有新匹配记录时睡眠，并按完整 timeline filter 减少无关唤醒 |
| `agent_timeline_read()` | 把等待和复制合并到一次 syscall，减少事件驱动刷新热路径开销 |
| `agent_provenance_snapshot()` | 直接导出因果边，减少最终页面从日志或短摘要中猜测触发关系 |
| `agent_audit_snapshot()` | 展示多 Agent 场景的全局短记录，包含 Context、事件、调度和预取交接摘要，仅 orchestrator 可读 |
| `agent_audit_query()` | 过滤多 Agent 场景的全局短记录，仅 orchestrator 可读 |
| `agent_ledger_snapshot()` | 读取全局短记录摘要和链尾 hash，仅 orchestrator 可读 |

`agentbench_ucore` 对比了 direct、query 和 snapshot：

```text
agentbench_ucore: direct_context ops=5000 ticks=1 ops_per_tick=5000 speedup_x100=31250
agentbench_ucore: context_query ops=16 ticks=1 ops_per_tick=16 speedup_x100=100
agentbench_ucore: context_snapshot ops=2048 ticks=6 ops_per_tick=341 speedup_x100=2133
```

## 当前限制

| 限制项 | 说明 |
| --- | --- |
| 历史容量 | 固定 128 条 |
| 文本长度 | payload/result 各保存 16 字节摘要 |
| 完整详情容量 | 最近 128 条可通过 `context_detail()` 查询；更早详情会随环形记录淘汰 |
| 用户自管 cache | 不进入 Context Path，不被 snapshot 覆盖，内核不把它作为可信历史 |
| 持久化 | Context Path 当前随进程生命周期存在，不持久化到磁盘 |

## 验证证据

```text
agentfinal_ucore: short_text_history=1 payload=ucore-final result=ucore-final
agentfinal_ucore: context_detail=1 sequence=8
agentfinal_ucore: record_flags system=1 manual=1 truncated=0
agentfinal_ucore: causal_context=1 first_cause=0 next_cause=1 span=1 edges=63
agentfinal_ucore: context_integrity=1 first_hash=... latest_hash=...
agentfinal_ucore: run_ledger=1 records=... hash=... context=... event=... sched=... prefetch=...
agentfinal_ucore: tamper_protected=1
agentfinal_ucore: user_cache_preserved=1 offset=21504 size=3072
agentfinal_ucore: fifo oldest=66 latest=193 dropped=65
agentfinal_ucore: span_trace=1 records=... context=1 event=1
agentfinal_ucore: passed
```
