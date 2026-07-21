# 任务三：上下文路径管理

本文是 [design.md](design.md) 的任务三细节附录，重点说明 Context Path 的布局、写入、查询、回滚、淘汰和可信性设计。

## 目标

任务三要求系统维护 Agent 多轮工具调用上下文，使 Agent 能看到自己历史调用路径，并能在路径过长时自动淘汰旧记录。

AgentOS-uCore 当前实现的是“128 条固定容量短文本摘要路径 + 最近 128 条完整详情 + 用户自管 cache + cause/span 因果字段 + prev/record hash 完整性链”。摘要 record 用于快速呈现和高频查询，`context_detail(sequence, out)` 用于查看内核 PCB 中保存的完整 `agent_op`、`agent_result` 和记录 flags。用户自管 cache 位于 Context 尾部，用于 Agent 自己保存短期策略状态，不进入内核可信历史。

## 上下文布局：Context

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

## 权威历史：shadow

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
7. cache 内容仍然保留，表示 snapshot 只刷新内核管理区，不覆盖用户自管 cache。

对应检查项为 `tamper_protected` 和 `user_cache_preserved`，原始输出见 [test-record.md](test-record.md)。

## 因果链记录

Context v6 会把工具调用、手动记录和事件消费串成轻量因果链，并为每条记录维护完整性链。公开 cause/span 之外，内核 PCB sidecar 保存 source pid/control id 和 span owner，公开整数本身不构成可信身份：

1. 第一条自动工具记录的 `cause_sequence` 为 0，表示 root。
2. 后续自动工具记录默认指向上一条 Context sequence。
3. 同一 Agent 连续调用使用同一个 `span_id`。
4. `context_push(record)` 必须令 `cause_sequence=0` 且 `span_id=0`；非零值返回 `AGENT_STATUS_BAD_PARAM`，内核再把手动记录接入当前可信链。
5. 工具触发的事件只有在同一 active workflow scope 的对象/路由授权通过后，才携带由内核认证的 source cause/span/owner。
6. 目标 Agent 成功 `agent_wait()` 后继承公开 span 和私有 owner/source，后续工具调用继续这条可信链路。
7. 第一条记录的 `prev_hash` 为 0。
8. 后续记录的 `prev_hash` 等于上一条记录的 `record_hash`。
9. header 中的 `latest_record_hash` 等于最新记录的 `record_hash`。

这使 Context Path 不只保存“发生了什么”，还保存“为什么接着发生”，并能验证当前可见路径的相邻顺序由内核维护。跨 Agent 事件中的 `cause_sequence` 是 source 进程本地序号；provenance 通过内核私有 source pid/control sidecar 解释它，不会把它误连到 target 恰好相同的本地序号。该链只在同一 workflow scope 内传播，用于运行追踪，不是磁盘持久化日志。

`agentfinal_ucore` 会检查首条记录是 root，第二条记录指向第一条记录，span 连续，header 中的当前 cause/span 与最新状态一致；同时检查每条记录的 prev/hash 链接关系。对应检查项为 `causal_context` 和 `context_integrity`，原始输出见 [test-record.md](test-record.md)。

## 写入路径

每次工具调用完成后，内核执行：

1. 给本次调用分配 sequence。
2. 把 `struct agent_result` 写入 latest 区。
3. 构造 `struct agent_context_record`。
4. 写入当前 head 指向的 record 槽。
5. 更新 count、head、oldest、latest、dropped。
6. 同步用户镜像。

手动 `context_push()` 使用同一个 sequence 流，保证手动记录和工具调用记录按同一顺序排列；用户只提交内容，cause/span/owner 由内核决定。

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
| `agent_audit_snapshot(records, max)` | orchestrator 查询当前 workflow scope 的 Context、事件、调度和预取交接短记录 |
| `agent_audit_query(filter, records, max)` | 先按 scope/owner 裁剪，再按 kind、span、目标事件、source/target 或 sequence 过滤 |
| `agent_ledger_snapshot(summary)` | orchestrator 查询当前 scope 稀疏可见窗口、dropped、分类计数和逻辑链尾 hash |
| `agent_file_prefetch_span_snapshot(hints, max)` | 当前 Agent 按自己的 span 查询跨 Agent metadata 预取提示 |
| `context_rollback(sequence)` | 回滚到仍可见 sequence |
| `context_clear()` | 清空记录和元信息 |

`context_snapshot()` 是测试和示例的主路径，因为它一次 syscall 就能拿到 header 和多条记录。`context_detail()` 用于在需要完整审计证据时补充摘要 record 中没有保存的完整参数和结果。

`agent_trace_snapshot()` 面向运行查看和问题排查。它不改变 Context Path，只读取当前 Agent 的 Context 摘要和最近调度原因，按 tick 合并为最多 144 条短记录。`agentfinal_ucore` 会检查结果中同时存在 Context、调度和事件等待记录。

`agent_span_trace_snapshot()` 面向当前可信 span。它读取共享物理表，但只返回调用者 scope、`current_span_id` 和私有 span owner 全部匹配的记录；调用者不能传入任意 span id。这样参与 Agent 可以看到本 workflow 链路中的 Context、事件和预取交接，同时不能借公开 span 数字读取另一 scope/owner。

`agent_timeline_snapshot()` 面向平台导出。它不改变 Context Path，也不新增新的权威历史，而是把当前 Agent 可见的 Context、调度、审计和预取提示转换成 `struct agent_timeline_record`。普通 Agent 只能看到自身数据以及同 scope 当前可信 span 的审计；orchestrator 可以额外看到本 workflow scope 的审计窗口。

`agent_timeline_query()` 用同一套权限规则，但允许在内核中按 source mask、起始 tick、span、kind、pid、tool、event、status、flags 和 after-cursor 过滤。`start_tick` 适合粗粒度读取某个 tick 之后的记录；after-cursor 使用上一条已读记录的 `tick/source/sequence`，只返回严格排在它之后的记录。过滤发生在 scope/owner 裁剪后的集合内，不能用 filter 读取其他 workflow。

`agent_timeline_wait()` 复用同一套 filter。当前没有匹配记录时，Agent 进入睡眠；Context、审计、调度或预取提示写入后，内核递增 observe epoch，把新事实转换成统一 `agent_timeline_record`，再用等待者保存的完整 filter 判断是否唤醒。接口返回正数表示已有匹配记录，调用者随后用同一个 filter 调 `agent_timeline_query()` 读取；返回 `AGENT_STATUS_TIMEOUT` 表示有限等待到期。`agentfinal_ucore` 会验证 source=Context 的未来记录等待能 timeout，也会验证 heartbeat audit 不会误增加 Context-only 等待的 timeline wake 计数，MESSAGE 条件不会被 TIMER audit 唤醒，并验证 TIMER audit 写入能唤醒 source=Audit 且 event=TIMER 的等待。

`agent_timeline_read()` 是 wait+query 的合并接口。它复用同一个 filter 和同一套权限裁剪；已有匹配记录时直接复制，没有匹配记录时睡眠等待，醒来后在同一次 syscall 中复制记录。该接口避免页面或 Agent worker 先 `agent_timeline_wait()` 再 `agent_timeline_query()` 的两次陷入，也避免 wait 返回与 query 执行之间出现新的记录导致读数不一致。

`agent_provenance_snapshot()` 面向因果图导出。它输出 `source_sequence -> target_sequence`，但边身份不只依赖序号：本地 Context 使用当前进程，跨 Agent event/cause 使用私有 source pid/control，span 边还要求 scope 和 private owner。可见审计形成 Context 到 Audit 的边，文件查询形成 Context 到 Prefetch 的边。这样 source 本地 sequence 与 target 本地 sequence 碰撞时不会产生伪边。

`agent_audit_snapshot()` 面向本 workflow orchestrator。物理审计表共 512 槽，按最多 4 个 scope 各保证 128；每 scope low/high 各64。Context/event/sched/prefetch/manual 遥测固定进入 low，low 每 principal 最多16；只有内核确认的特权状态效果进入 high，high 为每个 active principal 保证8条。high 满时只自滚当前 principal 或回收已退出/inactive principal 的记录，绝不淘汰另一 active principal；非活跃历史允许有界滚动并由 `dropped_records` 反映。

每 scope 的 `prev_hash/record_hash/ledger_hash` 构成逻辑链，系统 `sequence` 则跨 scope 单调。因此当前可见记录可因其他 scope 写入和分区滚动而跳号，且某条记录的前驱可能已在窗口外。`agent_ledger_snapshot()` 返回 `total_records`、`visible_records` 和 `dropped_records=total-visible`；只对实际连续的可见记录检查直接 hash 邻接，合法 gap 不等同于损坏。`agent_audit_query()` 只能在 scope/owner 裁剪后的集合中继续过滤。

本组接口的检查点如下。原始输出统一见 [test-record.md](test-record.md)。

| 接口 | 检查点 |
| --- | --- |
| `agent_trace_snapshot()` | 当前 Agent 的 Context、调度和等待记录能够进入同一组短记录。 |
| `agent_span_trace_snapshot()` | 自唤醒或跨 Agent 消息之后，当前 span 能同时看到 Context 与事件记录。 |
| `agent_timeline_snapshot()` / `agent_timeline_query()` | timeline 同时包含 Context、调度、审计和预取提示，且支持来源、tick 和游标过滤。 |
| `agent_timeline_wait()` / `agent_timeline_read()` | 没有匹配记录时睡眠；source 或 event 不匹配时不误唤醒；匹配后可在同一次 syscall 中复制记录。 |
| `agent_provenance_snapshot()` | Context 边、audit 边和 prefetch 边可以按因果关系导出。 |
| `agent_audit_snapshot()` / `agent_audit_query()` | orchestrator 能看到本 workflow 的摘要，并且 filter 不能扩大到其他 scope/owner。 |
| `agent_ledger_snapshot()` | scope-local 总量、稀疏 sequence 窗口、dropped、分类计数和逻辑链尾可由一个摘要读取。 |

span 同时用于文件预取提示。文件查询产生的 metadata 提示进入当前 Agent 本地 ring；可信 span 提示还进入物理 32 槽、每 scope 保留8条的共享表。目标 Agent 只在同 scope route 成功并消费 message 后继承私有 owner，才能查询该链提示；公开 span id 不允许跨 workflow 读取。

## 环形淘汰：FIFO

当前容量固定为 128 条。超过容量时，系统覆盖最旧记录，并增加 `dropped_records`。

`agentfinal_ucore` 写入 192 条记录后检查：

| 字段 | 期望 |
| --- | ---: |
| `count` | 128 |
| `oldest_sequence` | 66 |
| `latest_sequence` | 193 |
| `dropped_records` | 65 |

对应原始输出见 [test-record.md](test-record.md)。

## 性能路径

Context Path 有三种读取方式：

| 方式 | 适用场景 |
| --- | --- |
| 直接读 user mirror | 高频读取 latest 状态或可信 Agent 自身状态查看 |
| `context_query()` | 查询少量历史记录 |
| `context_snapshot()` | 批量读取当前可见历史，推荐示例使用 |
| `agent_trace_snapshot()` | 导出 Context 与调度原因的合并短视图 |
| `agent_span_trace_snapshot()` | 导出当前 span 的 Context、事件、调度和预取交接短记录 |
| `agent_timeline_snapshot()` | 用同一结构导出当前 Agent 可见的 Context、调度、审计和预取提示 |
| `agent_timeline_query()` | 减少按来源、tick、span 或上一条已读记录查看时的无关记录复制 |
| `agent_timeline_wait()` | 让页面或 Agent worker 在没有新匹配记录时睡眠，并按完整 timeline filter 减少无关唤醒 |
| `agent_timeline_read()` | 把等待和复制合并到一次 syscall，减少事件驱动刷新热路径开销 |
| `agent_provenance_snapshot()` | 直接导出因果边，减少从日志或短摘要中猜测触发关系 |
| `agent_audit_snapshot()` | 呈现当前 workflow 多 Agent 的 scope-local 短记录，仅本 scope orchestrator 可读 |
| `agent_audit_query()` | 在当前 scope 可见窗口内过滤，不能扩大 scope/owner |
| `agent_ledger_snapshot()` | 读取本 scope 稀疏窗口、dropped 和逻辑链尾，仅 orchestrator 可读 |

`agentbench_ucore` 对比 direct、query 和 snapshot 三条读取路径，并在 [test-record.md](test-record.md) 中保留具体 tick 样例。本文档只说明结论：直接读 mirror 适合高频 latest 状态读取，`context_query()` 适合少量历史，`context_snapshot()` 适合批量读取当前可见路径。

## 当前限制

| 限制项 | 说明 |
| --- | --- |
| 历史容量 | 固定 128 条 |
| 文本长度 | payload/result 各保存 16 字节摘要 |
| 完整详情容量 | 最近 128 条可通过 `context_detail()` 查询；更早详情会随环形记录淘汰 |
| 用户自管 cache | 不进入 Context Path，不被 snapshot 覆盖，内核不把它作为可信历史 |
| 持久化 | Context Path 当前随进程生命周期存在，不持久化到磁盘 |

## 验证证据

原始输出见 [test-record.md](test-record.md)，测试步骤见 [testing-details.md](testing-details.md)。任务三重点检查以下内容：

| 检查项 | 含义 |
| --- | --- |
| 短文本历史 | payload/result 摘要进入最近 128 条 Context 记录。 |
| 完整详情 | 最近 128 条 detail 可通过 `context_detail()` 查询。 |
| 手动记录与自动记录 | 系统记录、手动 push 和截断标志可区分。 |
| cause/span | 自动记录和同 scope 事件继承可信 cause/span；手动 push 非零 cause/span 被拒绝，私有 sidecar 保证来源归属。 |
| 完整性摘要 | Context 记录维护 `prev_hash` 和 `record_hash`。 |
| Scope 账本 | 本 workflow 多 Agent 场景能读取稀疏窗口、dropped、分类计数和 scope-local 链尾 hash。 |
| 用户态篡改保护 | mirror 被用户写脏后，snapshot 会恢复内核 shadow 权威状态。 |
| 用户自管 cache | snapshot 不覆盖 Agent 自己维护的 cache 区。 |
| FIFO 淘汰 | 写满后维护 `oldest_sequence`、`latest_sequence` 和 `dropped_records`。 |
| span 视图 | 当前 span 能看到自身 Context、事件和预取交接短记录。 |
