# 测试内容详细说明

本文档解释AgentOS-uCore 测试程序的内部步骤、覆盖范围和预期输出。测试入口和运行命令见 [verification.md](verification.md)。

## 1. `agentfinal_ucore`

`agentfinal_ucore` 是最终正确性测试，重点覆盖任务一、任务二、任务三，同时检查任务四文件索引和任务五事件自唤醒是否可用。

### 1.1 测试流程

1. 父进程打印 `Agent-OS on uCore final verification`。
2. 父进程调用 `agent_create_role(AGENT_ROLE_ORCHESTRATOR)` 创建 orchestrator Agent 子进程。
3. 子进程调用 `agent_info()`。
4. 子进程检查：
   - `info.is_agent == 1`；
   - `info.agent_role == AGENT_ROLE_ORCHESTRATOR`；
   - capability mask 包含 `AGENT_CAP_META_WRITE` 和 `AGENT_CAP_ORCHESTRATE`；
   - `info.context_base == AGENT_CONTEXT_BASE`；
   - `info.context_size == AGENT_CONTEXT_SIZE`。
5. 子进程把 `info.context_base` 转成 `struct agent_context_header *`，直接读取 Context header。
6. 子进程检查 header magic，并输出 Context 大小和容量。
7. 子进程调用 `context_clear()`，保证后续 sequence 从干净状态开始。
8. 子进程构造 64 个 echo 操作，使用一次 `agent_run()` 批量执行。
9. 子进程检查：
   - batch 返回值等于 64；
   - 第一条 result 的 sequence 为 1；
   - 最后一条 result 的 sequence 为 64；
   - 直接读取 latest result 的 sequence 为 64。
10. 子进程调用 `context_snapshot()`，检查返回 64 条有序记录。
11. 子进程检查第一条记录是 root，第二条记录指向第一条记录，并检查 span 连续。
12. 子进程检查 header 中的当前 cause/span、provenance edge 计数和 latest record hash；单进程 Context Path 中每条记录的 `prev_hash` 必须指向上一条 Context 记录。
13. 子进程检查第 8 条记录的 payload/result 短文本为 `ucore-final`。
14. 子进程调用 `context_detail()`，检查完整 `agent_op`、完整 `agent_result` 和 `SYSTEM` flag。
15. 子进程手动篡改用户态 Context 镜像中的第一条记录 sequence。
16. 子进程再次调用 `context_snapshot()`。
17. 子进程检查 snapshot 返回的第一条记录仍为原始 sequence，并检查用户镜像被刷新。
18. 子进程向 `header.user_cache_offset` 写入 `cache-ok`，再次调用 `context_snapshot()` 后检查 cache 内容仍保留。
19. 子进程以 cause=0、span=0 调用 `context_push()` 追加手动记录，检查 `MANUAL` flag 和 detail ring；可信 cause/span 由内核接入，用户非零自报值由安全测试拒绝。
20. 子进程继续批量写入 128 条记录，使总记录达到 193 条。
21. 子进程再次 snapshot，检查 FIFO 淘汰：
   - count 为 128；
   - oldest 为 66；
   - latest 为 193；
   - dropped 为 65。
22. 子进程调用 `agent_file_meta_init()` 初始化文件元数据。
23. 子进程按示例项目、设定的模拟流程和比对处理环节查询文件。
24. 子进程检查查询命中，且 `used_index == 1`。
25. 子进程调用 `agent_file_prefetch_snapshot()`，检查本次文件查询产生了对象标签依赖预取提示。
26. 子进程调用 `agent_file_prefetch_span_snapshot()`，检查同 scope/private-owner span 分区包含当前 Agent 提示，并带有 `SPAN_BUS`、source pid 和 target pid。
27. 子进程使用只提供 `tool_name` 的 `agent_call()` 依次验证 `echo`、`query_file`、`pid_info`、`read_file_digest`、`dependency_update` 和 `dependency_query`。
28. 子进程注册 message watch。
29. 子进程用 `agent_wake()` 向自己投递事件。
30. 子进程调用 `agent_wait()`，检查成功收到 `self wake`。
31. 子进程调用 `agent_trace_snapshot()`，检查返回记录中同时包含 Context 记录、调度原因记录和 `agent_wait()` 事件消费记录，并检查记录按 tick 排列。
32. 子进程调用 `agent_span_trace_snapshot()`，检查当前 span 的系统级短记录中包含 Context 和事件记录，并检查返回记录都属于当前 span。
33. 子进程调用 `agent_timeline_snapshot()`，检查统一 timeline 同时包含 Context、调度、审计和预取提示来源，并检查 tick 顺序。
34. 子进程调用 `agent_timeline_query()`，检查 source mask 只返回 audit 来源，start tick 只返回指定 tick 之后的记录，并检查 after-cursor 只返回上一条已读记录之后的记录。
35. 子进程调用 `agent_timeline_wait()`，先验证等待未来 Context 记录会 timeout；再注册 TIMER watch 和 heartbeat，验证纯 Audit 写入不会增加 Context-only 等待的 timeline wake 计数；随后验证 AUDIT+MESSAGE 条件不会被 TIMER audit 唤醒；最后验证 AUDIT+TIMER 条件会被内核新记录唤醒，用同一 filter 查询到记录，并用 `agent_timeline_read()` 在一次 syscall 内等待和取回记录。
36. 子进程调用 `agent_provenance_snapshot()`，检查 Context 因果边和 audit 因果边均可见。
37. 子进程调用 `agent_ledger_snapshot()` 读取当前 scope 摘要。物理 sequence 可因其他 scope 写入而跳号，low/high/principal 滚动也会产生窗口缺口；测试只对无 gap 的相邻可见记录检查直接 hash 邻接，并要求 gap 数量能由 `dropped_records` 覆盖，链尾等于 scope-local `ledger_hash`。
38. 子进程输出 `agentfinal_ucore: passed` 并退出。
39. 父进程等待子进程退出，检查退出状态为 0，输出 `agentfinal_ucore: parent passed`。

### 1.2 输出阅读方式

本测试的串口输出主要分为三类：Context 与工具调用检查、文件查询与事件检查、timeline/provenance/ledger 检查。阅读时关注 `context size`、`batch first_seq/last_seq`、`tamper_protected`、`fifo`、`file_query`、`event_wait`、`timeline_query`、`provenance_graph`、`run_ledger` 和最终通过标记即可。完整样例输出见 [test-record.md](test-record.md)。

### 1.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| Agent 创建 | 内核加载的可信初始进程凭创建授权生成 orchestrator Agent 子进程 |
| Agent PCB 字段 | `agent_info()` 返回 Agent 状态、真实 role、capability 和 Context 信息 |
| Agent Context 映射 | 直接读取 header 和 latest result |
| 批量工具调用 | 一次 `agent_run()` 执行 64 个 echo op |
| Context Path 写入 | snapshot 返回 64 条记录 |
| 短文本历史 | payload/result 短摘要可查询 |
| 完整详情 | `context_detail()` 返回完整 op/result 和 flags |
| 因果链 | Context record 和 header 中 cause/span 连续 |
| 完整性链 | 首条记录 `prev_hash=0`，后续记录 `prev_hash` 指向上一条 `record_hash`，header latest hash 指向最新记录 |
| shadow 防篡改 | 用户镜像被写坏后，snapshot 仍返回权威历史 |
| 用户自管 cache | snapshot 不覆盖 `user_cache_offset` 之后的 cache 内容 |
| 名称协议 | name-only `agent_call()` 可调用 echo、query_file、pid_info、read_file_digest、dependency_update 和 dependency_query |
| 手动记录 | `context_push()` 记录 `MANUAL` flag |
| FIFO 淘汰 | 193 条记录后只保留 128 条，oldest/latest/dropped 正确 |
| 运行轨迹 | `agent_trace_snapshot()` 合并 Context 摘要和调度原因，且包含事件等待记录 |
| 当前 span 短记录 | `agent_span_trace_snapshot()` 返回当前 span 的 Context 和事件记录 |
| 统一 timeline | `agent_timeline_snapshot()` 返回同一结构的 Context、调度、审计和预取提示记录 |
| timeline 过滤查询 | `agent_timeline_query()` 能按来源、tick 和上一条已读记录游标过滤当前可见记录 |
| timeline 等待唤醒 | `agent_timeline_wait()` 能在无匹配记录时 timeout，能按完整 filter 减少无关唤醒，也能被 heartbeat TIMER audit 新记录唤醒；`agent_timeline_read()` 能在同一次 syscall 内等待并复制记录 |
| 因果边导出 | `agent_provenance_snapshot()` 返回 Context、审计和预取提示之间的可见触发关系 |
| Run Ledger 摘要 | 返回当前 scope 稀疏窗口、dropped、分类计数和逻辑链尾；只在连续可见片段逐条验 hash |
| 文件索引 | `agent_file_query()` 使用索引路径 |
| 预取提示 | `agent_file_prefetch_snapshot()` 返回由文件查询历史和对象标签依赖生成的 metadata 提示 |
| span 预取提示 | `agent_file_prefetch_span_snapshot()` 返回当前 scope/private-owner span 的 metadata 提示 |
| Agent 事件 | watch/wake/wait 自唤醒成功 |
| 特权 Agent 能力 | orchestrator 能初始化文件元数据并向自身投递事件 |

## 2. `agentfs_ucore`

`agentfs_ucore` 是任务四文件系统能力测试，重点检查 Agent 文件元数据是否绑定真实根目录文件对象，并事务写入和重新加载私有 metadata 双 bank。内核用 `dev + inum + incarnation` 标识一次 inode 生命周期，避免 inode 号复用后把旧权限、版本或元数据错误绑定到新文件。

### 2.1 测试流程

1. 父进程创建 orchestrator Agent 子进程。
2. 子进程在首次显式 metadata 操作前创建两个真实 probe 文件，分别用于启动早期字段更新和后台协调扫描。
3. 子进程对第一个 probe 只更新 STATUS；内核按 physical path 恢复真实 inode 绑定，随后 `agent_file_meta_init()` 强制重载，`dev + inum + incarnation` 和状态仍保持一致。
4. 子进程轮询第二个 probe，确认它最终由有界后台扫描纳入查询，再删除该 probe。
5. 子进程由用户态写入设定的模拟流程示例元数据，并查询该文件对象。返回项携带 `dev`、`inum`、`incarnation` 和 `size`；测试输出并断言可见的 inode 与大小字段。
6. 子进程用 fid 和 physical path 指向两个不同记录，确认统一返回 `AGENT_STATUS_CONFLICT` 且两条记录均未受影响。
7. 子进程删除并重建同名文件，再用旧 `dev + inum + incarnation` 请求删除新对象，确认不可变 identity guard 拒绝陈旧 selector。
8. 子进程创建自定义真实文件，写入内容，并用 `agent_file_meta_set()` 绑定自定义逻辑属性。
9. 子进程查询自定义文件，检查返回的 inode 与文件大小和真实文件一致；内核中的绑定、缓存和编辑版本均以 `dev + inum + incarnation` 为身份键。
10. 子进程调用 `read_file_digest`，分别用物理文件名和属性 selector 定位同一文件，检查 size、bytes、hash 和 preview。
11. 子进程通过 `agent_info()` 读取 digest cache 计数，确认第二次读取同一真实文件时命中缓存。
12. 子进程改写同一真实文件内容，再次读取 digest，确认 hash/preview 更新且 digest cache 出现新的 miss。
13. 子进程用 `agent_timeline_query()` 按 `source=CONTEXT` 和 `tool_id=READ_FILE_DIGEST` 查询，确认统一 timeline 中保留 size、bytes、hash 和 preview。
14. 子进程再次调用 `agent_file_meta_init()`，确认自定义元数据来自双 bank 强制重新加载，没有被空表覆盖。
15. 子进程重复执行同一个非强制扫描查询，确认 `plan_reason` 带有 `AGENT_FILE_QUERY_REASON_CACHE_HIT`。
16. 子进程写入接近 128 条真实文件元数据，制造足够的数据量。
17. 子进程分别运行扫描查询和索引查询，检查索引路径的 `scanned_records` 明显更少。
18. 子进程检查查询计划：扫描路径必须返回 `AGENT_FILE_QUERY_PLAN_SCAN`，索引路径必须返回 `AGENT_FILE_QUERY_PLAN_STATUS_INDEX`，并带有 status 索引原因、索引桶和候选记录数。
19. 子进程调用 `dependency_query`，分别带入设定的模拟流程和备用模拟流程，检查同名 label 的依赖结果按 run_id 分开。
20. 子进程调用 `dependency_update` 注册一条 `source -> target` 通用对象依赖，再用 `dependency_query` 验证该依赖可见。
21. 子进程读取预取提示，检查提示由对象标签依赖产生、使用 label 索引计划，并指向当前 run 内的 analyze/report 等后续 label。
22. 子进程清空某条记录的 status，确认属性更新生效，并确认旧 generation 查询缓存没有返回过期命中。
23. 子进程删除绑定文件，确认关联元数据随文件删除被清理。
24. 子进程调用 `action_commit` 指向不存在的 selector，确认返回 `AGENT_STATUS_NOT_FOUND`。

### 2.2 输出阅读方式

本测试的输出重点是启动早期绑定、后台扫描、selector 一致性、inode 生命周期 guard、私有 `.agentmeta` 重新加载、内容摘要、查询缓存、scan/index 候选差异、依赖注册和 selector 未命中。阅读时关注 `partial_update_binding`、`preload_create_query`、`selector_consistency`、`stale_identity_guard`、`demo_inode`、`custom_inode`、`content_digest`、`digest_cache_invalidated`、`.agentmeta_reload`、`bulk_index`、`query_plan`、`dependency_update`、`delete_clears_metadata` 和最终通过标记。完整样例输出见 [test-record.md](test-record.md)。

### 2.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| 启动早期真实对象协调 | 字段级更新经强制重载仍保留真实 inode identity；首次显式 metadata 操作前创建的另一文件最终由后台扫描纳入查询 |
| selector 一致性 | fid/path 指向不同记录时返回冲突；陈旧 inode identity 不能选择路径复用后的新对象 |
| 文件生命周期身份 | 查询结果携带 `dev`、`inum`、`incarnation`、`size`；安全绑定和版本状态以 `dev + inum + incarnation` 为键，测试断言并输出真实 inode 与大小 |
| metadata 双 bank 可写入和重新加载 | 自定义元数据重新初始化后仍存在 |
| 内容摘要缓存 | 两次读取同一真实文件输出 `digest_cache=1`，改写后输出 `digest_cache_invalidated=1` |
| 内容证据进入 timeline | 输出 `digest_timeline=1`，表示可按工具 id 查询 digest Context 记录 |
| scan/index 差异 | 接近 128 条记录下输出 `bulk_index scan=118 index=6` |
| 查询缓存 | 重复非强制扫描查询输出 `query_cache=1`，字段更新后输出 `cache_invalidated=1` |
| 属性删除 | 清空 status 后查询行为符合预期 |
| 文件删除同步 | 删除真实文件后关联元数据被清理 |
| 未命中 selector | `action_commit` 对不存在目标返回 `AGENT_STATUS_NOT_FOUND` |
| 依赖查询 | `label/namespace/run_id` selector 只返回所选运行的对象依赖，不混入同名 label 的其他运行 |
| 依赖注册 | `dependency_update` 可由用户态注册通用对象依赖，后续 `dependency_query` 可按同一 selector 读取 |
| 预取提示 | 默认 align 查询后得到当前运行内 analyze/report 等后续 label metadata 提示 |

## 3. `agentscan_ucore`

`agentscan_ucore` 是任务四自动维护能力测试，重点检查真实根目录文件是否可以由内核自动发现并进入 Agent 文件元数据表，而不需要用户态逐个调用 `agent_file_meta_set()`。

### 3.1 测试流程

1. 父进程创建 orchestrator Agent 子进程。
2. 子进程调用 `agent_file_meta_init()`，启用文件元数据服务和自动扫描。
3. 子进程等待 `agent_info.file_scan_runs` 增加，并确认 `usershell` 已能通过 status 索引查询到。
4. 子进程按 `physical_name=usershell` 查询，确认镜像中已有真实文件被自动发现。
5. 子进程通过普通文件 syscall 创建 `autoscan_ok`，写入短内容并关闭文件。
6. 子进程等待下一轮扫描完成。
7. 子进程查询 `autoscan_ok`，确认返回真实文件大小和索引结果。
8. 子进程删除 `autoscan_ok`。
9. 子进程等待扫描清理元数据，再次查询确认该文件已经不可见。

### 3.2 输出阅读方式

本测试只需要确认三类事实：根目录已有文件可被后台扫描发现，新建普通文件可自动进入 Agent metadata，删除真实文件后自动 metadata 会被清理。完整样例输出见 [test-record.md](test-record.md)。

### 3.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| 根目录自动扫描 | 已有 `usershell` 无需手动写元数据即可被查询 |
| 自动建元数据 | 新建 `autoscan_ok` 后产生 `AUTOSCAN` 元数据 |
| 索引维护 | 自动文件可按属性走索引查询 |
| 删除清理 | 删除真实文件后自动元数据被清理 |
| 扫描可观测 | `agent_info` 暴露扫描轮数、目录项数量和新增计数 |

## 4. `agentloop_ucore`

`agentloop_ucore` 是任务五事件运行机制测试，重点检查 FIFO 事件队列、单 stable source 配额、内核 TIMER 与外部事件共存、watch/unwatch、有限 timeout 睡眠、wait cancel、TIMER unwatch 和 heartbeat stop 是否都可运行。

### 4.1 测试流程

1. 父进程创建 orchestrator Agent 子进程。
2. 子进程注册 message watch。
3. 子进程连续投递多个事件，调用 `agent_wait()` 检查 FIFO 顺序。
4. 子进程检查投递和消费的事件包含 cause/span。
5. 子进程从同一个 stable source 连续投递 4 条未消费 `MESSAGE`，第 5 条必须返回 `AGENT_STATUS_NO_SPACE`；消费首条后立即补投一条并要求成功，再按原顺序消费余下事件，确认 source=4 边界和逐槽归还。实现中的 source 计数跨 directed/attributed 共用，但该步骤没有混合两类。
6. 另创建两个 directed source 各发送 4 条消息，把 directed IPC 填到 8，并确认继续 directed 投递被拒绝；第三个 source 触发 4 条 attributed `POLICY_DENIED`，使 external 合计达到 12。第四个 source 再触发 attributed 通知时，目标队列必须保持 12；随后 4 条 heartbeat TIMER 以显式 `KERNEL` origin 将队列填到 16。消费全部事件后，再分别投递 self directed 和第四个 source attributed 通知，确认两类都能重新接纳。
7. 创建早期 full watcher，用 KERNEL heartbeat 把其队列填到 16；再创建 later watcher 和 attributed source，确认早期目标满队列不会终止扫描，later watcher 仍收到 `POLICY_DENIED` 广播。该用例不调用 `agent_file_meta_set()`，因此 metadata 提交不受通知背压的语义仍由实现审查支持。
8. 子进程删除 message watch，再投递相同事件，确认不会唤醒。
9. 子进程重新注册 watch，调用有限 timeout wait，确认线程进入睡眠并由 timeout 唤醒，且 `wait_loop_count` 增量很小。
10. 子进程注册 TIMER watch，启动 heartbeat，确认 heartbeat 事件可唤醒等待。
11. 子进程删除 TIMER watch 后再次启动 heartbeat，确认不会消费 TIMER 事件。
12. 子进程调用 `agent_heartbeat_stop()`，确认停止后不再产生 heartbeat 事件。
13. 子进程以带 `WAIT_CANCEL` 的 orchestrator 身份创建 sentinel 等待者，作为其直接 controller 调用 `agent_wait_cancel()`，确认等待者返回 `AGENT_STATUS_CANCELLED`，事件类型为 `AGENT_EVENT_CANCELLED`，并带有 reason、cause/span 和 Context 记录。

### 4.2 输出阅读方式

本测试输出围绕事件顺序、事件因果、single directed source、IPC 类、external 总量、内核 TIMER 保留容量、慢 watcher 隔离、watch 删除、有限 timeout 睡眠、TIMER watch、heartbeat stop 和 wait cancel 展开。2026-07-21 的 QEMU 运行已出现 `message_source_limit=4`、`ipc_class_limit=8`、`external_limit=12`、`system_event_reserved=4`、`external_reject_reclaim=1` 和 `broadcast_slow_watcher_isolated=1`。其中动态断言实际尝试第 13 条 external、把 4 条 KERNEL TIMER 同时填入保留容量，并在 drain 后重新接纳 directed 与 attributed；attributed=8、同一 stable source 混合跨类和 metadata 提交返回值仍没有独立断言。完整运行摘录见 [test-record.md](test-record.md)。

### 4.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| FIFO 顺序 | 多事件按投递顺序被消费 |
| 事件因果信息 | 投递和消费事件均携带 cause/span |
| 单 stable source 上限 | 同一 stable source 的 4 条 directed event 可入队，第 5 条返回 `AGENT_STATUS_NO_SPACE`，旧事件不被覆盖；消费 1 条后立即补投成功，证明 source 槽逐次归还。混合跨类 source 计数由实现保证，尚无独立输出 |
| directed 与 external 压力 | 两个 stable source 各 4 条消息触及 directed=8，继续 directed 投递被拒绝；第三个 source 的 4 条 attributed 通知让 external 达到 12，第四个 source 的第 13 条 external 不入队且队列保持 12 |
| 内核 origin 保留与重接纳 | external=12 后，4 条显式 `KERNEL` origin TIMER 将总队列填到 16；全部消费后 self directed 与新的 attributed 通知均可再次入队。该重接纳证明相关 admission 可恢复，但没有单独把 attributed 计数从 8 的边界清空后再验证 |
| attributed 类边界 | 当前只填入 4 条 attributed 通知，没有单独触及 `ATTRIBUTED_LIMIT=8`，属于明确的剩余测试缺口 |
| 慢 watcher 隔离 | 较早分配的 watcher 先把队列填满；同一 `POLICY_DENIED` attributed 广播仍继续送达后续 watcher；metadata 提交路径未在该用例中动态覆盖 |
| watch 删除 | `agent_unwatch()` 后相同事件不再匹配 |
| timeout 睡眠 | 有限 timeout wait 返回 `AGENT_STATUS_TIMEOUT`，并用 `wait_loop_count` 检查是否避免反复轮询 |
| wait cancel | 受权 Agent 可取消目标 Agent 的等待；取消令牌与普通事件队列在实现上独立，但当前动态用例没有组合“队列已满 + cancel” |
| heartbeat 唤醒 | 注册 TIMER watch 后可收到 heartbeat 事件 |
| TIMER watch 删除 | 删除 TIMER watch 后 heartbeat 不再唤醒等待 |
| heartbeat 停止 | stop 后不再继续产生 heartbeat 事件 |

## 5. `agentsched_ucore`

`agentsched_ucore` 是任务五调度策略测试，重点检查内核调度器是否已经感知 Agent 角色、orchestrator 配置参数和事件状态，并输出可检查的调度计数和调度原因记录。

### 5.1 测试流程

1. 父进程创建 orchestrator Agent 子进程。
2. 子进程分别创建 sentinel、investigator、recovery、orchestrator 角色 Agent，读取各自 `agent_info.sched_weight`。
3. 子进程确认四类角色权重分别为 70、90、120、110。
4. 子进程创建 sentinel Agent，orchestrator 调用 `agent_sched_config()` 把它配置为 `weight=150 priority=20 budget=3`。
5. sentinel 读取 `agent_info()`，确认配置生效；随后投递自唤醒 message，触发调度并检查最近调度记录包含 `AGENT_SCHED_REASON_PRIORITY`。
6. 子进程注册 message watch，并向自己投递事件。
7. 子进程让出处理器，使调度器重新选择可运行任务。
8. 子进程读取 `sched_dispatch_count` 和 `sched_event_dispatch_count`，确认事件路径被记录。
9. 子进程调用 `agent_sched_snapshot()`，确认最近一条调度记录包含 `AGENT_SCHED_REASON_EVENT_QUEUE` 和 `AGENT_SCHED_REASON_ROLE_WEIGHT`。
10. 子进程多次让出处理器，确认调度次数、让出处理器次数、虚拟运行量和调度原因记录数量增加。

### 5.2 输出阅读方式

本测试的输出重点是角色默认权重、受权调度配置、事件相关调度、调度原因记录和公平性计数。完整样例输出见 [test-record.md](test-record.md)。

### 5.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| 角色权重 | `agent_info.sched_weight` 返回不同角色权重 |
| 受权调度配置 | orchestrator 可配置目标 Agent 的 weight、priority 和 budget |
| 配置拒绝 | 非 orchestrator Agent 和普通进程不能调用 `agent_sched_config()` |
| 事件优先 | 待消费事件让 `sched_event_dispatch_count` 增加 |
| 调度原因记录 | `agent_sched_snapshot()` 返回最近调度记录，且 reason flags 包含事件队列和角色权重 |
| 调度计数 | `sched_dispatch_count` 随运行增加 |
| 公平性计数 | `sched_vruntime` 随运行增加 |
| 让出处理器计数 | `sched_preemptions` 随主动让出增加 |

## 6. `agentconflict_ucore`

`agentconflict_ucore` 是文件编辑冲突测试，重点检查冲突处理是否发生在内核真实文件操作路径，而不是用户态程序之间的约定。它覆盖同一真实文件被两个 Agent 同时编辑、未持有租约仍直接写文件、旧版本提交等情况。

### 6.1 测试流程

1. 父进程创建短文件名真实文件 `edtarget`，写入初始内容。
2. 父进程作为普通进程直接调用 `agent_file_edit_begin()`，检查返回 `-1`。
3. 父进程创建 Agent A，Agent A 调用 `agent_file_edit_begin("edtarget")`。
4. Agent A 检查返回的 `state.active == 1`，持有者 pid 是自己，并得到 `lease_id` 和 `base_version`。
5. Agent A 写入普通标记文件 `edready`，通知 Agent B 开始竞争同一文件。
6. 父进程创建 Agent B，Agent B 等待 `edready` 出现。
7. Agent B 调用 `agent_file_edit_begin("edtarget")`，检查返回 `AGENT_STATUS_CONFLICT`，并检查返回状态中包含 Agent A 的 owner pid。
8. Agent B 不持有租约，直接 `open("edtarget", O_WRONLY)` 后调用 `write()`，检查真实 `write` 返回失败。
9. Agent B 继续尝试 `open("edtarget", O_WRONLY | O_TRUNC)`，检查打开失败，因为截断会修改同一 inode。
10. Agent B 尝试 `unlink("edtarget")`，检查删除失败。
11. Agent B 调用 `agent_file_edit_state()`，确认租约仍 active，且 `conflict_count` 已增加。
12. Agent B 写入普通标记文件 `eddone` 后退出。
13. Agent A 等待 `eddone`，随后以租约持有者身份写入 `edtarget`。
14. Agent A 调用 `agent_file_edit_commit(lease_id, base_version)`，检查提交成功，返回版本等于 `base_version + 1`。
15. 父进程等待 Agent A 和 Agent B 都正常退出。
16. 父进程创建 Agent C。
17. Agent C 成功申请同一文件的新租约，但故意用 `base_version + 99` 提交，检查返回 `AGENT_STATUS_STALE`。
18. Agent C 放弃该租约，重新申请、写入并用正确 `base_version` 提交，检查版本推进。
19. 所有子进程退出后，父进程输出 `agentconflict_ucore: parent passed`。

### 6.2 输出阅读方式

本测试的输出重点是普通进程不能申请租约、第二个 Agent 申请同一文件被拒绝、非持有者直接写真实文件失败、持有者提交成功、旧版本提交被拒绝。完整样例输出见 [test-record.md](test-record.md)。

### 6.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| 普通进程不能申请编辑租约 | 父进程直接调用 `agent_file_edit_begin()` 返回 `-1` |
| 同一文件只能有一个编辑持有者 | Agent B 对同一文件申请租约返回 `AGENT_STATUS_CONFLICT` |
| 非持有者真实写入失败 | Agent B 的 `write()`、`O_TRUNC`、`unlink()` 均失败 |
| 持有者真实写入成功 | Agent A 持有租约后写入并提交成功 |
| 冲突可观测 | `agent_file_edit_state()` 返回 owner 和 conflict count |
| 版本检查生效 | 错误 `expected_version` 返回 `AGENT_STATUS_STALE` |
| 正确提交推进版本 | 正确 `expected_version` 提交后 `current_version = base_version + 1` |

## 7. `agentllm_ucore`

`agentllm_ucore` 是 LLM-friendly 路径测试。它不访问云端 API，也不保存 secret，只检查内核是否能把 LLM 请求当作结构化 Agent 工作流的一部分来记录、投递和唤醒。

### 7.1 测试流程

1. 父进程创建 orchestrator Agent。
2. orchestrator 作为本轮 template Relay，先注册 `AGENT_EVENT_MESSAGE` watch，过滤 `llm_request`。
3. orchestrator 创建 investigator Agent 作为请求方。
4. 请求方注册 `AGENT_EVENT_LLM_DONE` watch，过滤 `template_response`。
5. 请求方调用 `AGENT_TOOL_LLM_REQUEST`，把 prompt 摘要发给 Relay。
6. Relay 通过 `agent_wait()` 收到消息事件，检查 source pid、corr id 和 payload。
7. Relay 调用 `AGENT_TOOL_LLM_RESPONSE`，把模板结果投递给请求方。
8. 请求方从 `agent_wait()` 返回，检查事件类型为 `AGENT_EVENT_LLM_DONE`，source pid 是 Relay，payload 是模板结果摘要。
9. 请求方调用 `context_snapshot()`，确认本次 LLM 请求和等待记录进入 Context。
10. Relay 调用 `agent_timeline_snapshot()` 和 `agent_ledger_snapshot()`，确认响应动作进入统一观测记录和账本摘要。
11. 两个 Agent 正常退出，父进程输出 `agentllm_ucore: parent passed`。

### 7.2 输出阅读方式

本测试的输出重点是 Relay 记录进入 timeline、请求方收到 LLM 完成事件、模板 Relay 路径可运行。完整样例输出见 [test-record.md](test-record.md)。

### 7.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| LLM 请求结构化 | 请求方用 `AGENT_TOOL_LLM_REQUEST` 写入 prompt 摘要和 request id |
| Relay 权限 | Relay 由 orchestrator 承担，具备 `AGENT_CAP_LLM_RELAY` |
| Relay 事件投递 | Relay 通过 `AGENT_TOOL_LLM_RESPONSE` 投递 `AGENT_EVENT_LLM_DONE` |
| 请求方唤醒 | 请求方 `agent_wait()` 收到模板结果事件 |
| Context 记录 | 请求方 snapshot 中包含 LLM 请求和等待记录 |
| timeline 和账本 | Relay 可读取本轮响应记录和账本摘要 |
| 云端职责位置 | 测试只使用模板结果，真实云端调用留在用户态或宿主机 relay |

## 8. `agentbench_ucore`

`agentbench_ucore` 是性能和吞吐测试。它不使用固定耗时阈值，而是输出可对比的 tick 统计。

benchmark 主进程通过 `agent_create_role(AGENT_ROLE_ORCHESTRATOR)` 创建 orchestrator Agent，文件元数据初始化、文件查询、事件投递等需要权限的操作都在 orchestrator 内执行。wait/wake 子测试中的 waiter Agent 使用最低权限 sentinel role。

### 8.1 测试项目

| 项目 | 操作数 | 测试内容 |
| --- | ---: | --- |
| `scalar_agent_run` | 256 | 每次 syscall 执行 1 个 echo op |
| `batch_agent_run` | 256 | 每次 syscall 执行 64 个 echo op |
| `direct_context` | 5000 | 用户态直接读取 Context header 的 latest sequence |
| `context_query` | 16 | 每次 syscall 查询 1 条 Context record |
| `context_snapshot` | 2048 | 多次 snapshot，每次最多返回 128 条记录，按返回记录数计数 |
| `file_scan_query` | 64 | 强制扫描文件元数据表 |
| `file_index_query` | 64 | 使用文件元数据索引路径 |
| `file_digest_read` | 37888 | 读取真实文件短预览和内容指纹，按参与计算字节数计数 |
| `file_prefetch_snapshot` | 192 | 重复读取预取提示，按返回提示条数计数 |
| `timeline_snapshot` | 8192 | 重复读取统一 timeline，按返回记录数计数 |
| `timeline_query_prefetch` | 48 | 按来源和 flags 读取 prefetch handoff 记录 |
| `timeline_query_cursor` | 6568 | 使用上一条已读记录作为游标继续读取 timeline |
| `provenance_snapshot` | 2048 | 读取当前 Agent 可见因果边，按返回边数计数 |
| `timeline_wait_ready` | 659 | 当前已有匹配 timeline 记录时，wait 直接返回可读数量 |
| `timeout_heartbeat` | 1 | 验证无事件等待返回 timeout，且 heartbeat 字段可通过 `agent_info()` 观察 |
| `busy_poll_query` | 128 | 模拟用户态持续查询无事件条件，作为轮询路径计时观测 |
| `event_wait_wake` | 8 | 父进程多次唤醒等待中的 Agent 子进程，并输出计时观测 |

### 8.2 输出字段

| 字段 | 含义 |
| --- | --- |
| `ops` | 执行的逻辑操作数量 |
| `ticks` | 消耗的内核 tick 数；最小按 1 处理，避免除 0 |
| `ops_per_tick` | 每 tick 完成的操作数 |
| `speedup_x100` | 相对基线放大 100 倍后的速度比 |

### 8.3 输出阅读方式

本测试会输出多组 `ops/ticks/ops_per_tick/speedup_x100`，并额外输出 scan/index 候选记录数、查询计划、digest cache、prefetch 记录数、timeline 记录数和 busy-poll/wait 计时观测。完整样例输出见 [test-record.md](test-record.md)，图表化结果见双目标运行生成的 `results/latest/charts/`。

### 8.4 性能解释

| 对比 | 设计含义 |
| --- | --- |
| `batch_agent_run` vs `scalar_agent_run` | 批量 syscall 减少陷入内核次数 |
| `direct_context` vs syscall 查询 | 用户态镜像适合高频读最新状态 |
| `context_snapshot` vs `context_query` | 批量历史查询减少多次 syscall 和多次遍历 |
| `file_index_query` vs `file_scan_query` | 文件元数据索引减少候选记录检查，`file_query_records` 输出候选记录数差异，`file_query_plan` 输出索引选择原因，`file_query_cache` 输出重复查询缓存命中 |
| `file_digest_read` | 受权 Agent 可读取真实文件短预览和内容指纹，性能表按处理字节数呈现，`file_digest_cache` 呈现重复读取时的缓存命中 |
| `file_prefetch_snapshot` | 文件查询之后可直接读取内核给出的后续 metadata 提示，避免下一轮重新从宽条件查询开始 |
| `provenance_snapshot` | 页面可直接获取因果边，减少从 timeline 文本和短记录中二次推断触发关系 |
| `timeline_wait_ready` | 当前已有记录时 wait 不睡眠，直接返回可读数量；真正睡眠唤醒由 `agentfinal_ucore` 断言 |
| `timeline_read_ready` | 当前已有记录时 read 不睡眠，直接复制可见 timeline 记录 |
| `timeout_heartbeat` | Agent Loop 的超时和心跳字段有直接断言，不只依赖场景日志 |
| `busy_poll_query` / `event_wait_wake` | Agent Loop 不只是功能示例，也能输出轮询路径和等待唤醒路径的计时观测 |

tick 数值随环境波动，阅读性能数据时应结合多轮 min/avg/max、候选记录数和设计解释看相对趋势。

## 9. `labdemo_ucore`

`labdemo_ucore` 是面向综合场景的最终场景测试。它把底层能力串成一个可解释的多 Agent 工作流。

### 9.1 场景设定

实验流水线包含多个阶段：

- prepare
- align
- analyze
- report
- archive

系统中创建三个 Agent：

| 角色 | 职责 |
| --- | --- |
| sentinel | 监听失败事件，发现异常 |
| investigator | 查询失败原因和影响范围 |
| recovery | 执行受控恢复动作并验证结果 |

### 9.2 流程

1. 内核加载的可信 init 调用 `agent_create_role(AGENT_ROLE_ORCHESTRATOR)` 创建 orchestrator。
2. orchestrator 调用 `agent_file_meta_init()` 重新加载文件对象元数据并启用扫描。
3. orchestrator 创建 recovery、investigator、sentinel 三个角色 Agent。
4. 三个角色 Agent 分别输出自己的 role、pid 和 Context 地址。
5. sentinel 注册 `status=failed` 的文件状态监听。
6. investigator 注册 `investigate` 消息监听。
7. recovery 注册 `recover` 消息监听。
8. orchestrator 更新 align 阶段文件元数据，把状态改为 failed。
9. 内核投递 `AGENT_EVENT_FILE_STATUS`。
10. sentinel 调用 `agent_wait()` 收到失败事件。
11. sentinel 通过 `query_file` 找到失败工件。
12. sentinel 尝试执行恢复动作，`capability_check` 按真实 sentinel role 返回 denied。
13. sentinel 调用 `agent_file_prefetch_snapshot()`，检查失败文件查询产生了依赖驱动的 metadata 预取提示。
14. sentinel 发送普通 investigate 消息。
15. 内核在 message 入队时把 sentinel 当前可见的预取提示交接给 investigator，investigator 校验消息 payload 前缀后读取自己的预取提示 snapshot。
16. investigator 调用 `agent_file_prefetch_span_snapshot()`，确认同一 span 中存在带 `HANDOFF` 和 `SPAN_BUS` 原因位的提示，并且 source pid 指向上游 Agent、target pid 指向自己。
17. investigator 调用 `agent_span_trace_snapshot()`，检查当前 span 中包含 Context、事件和预取交接记录。
18. investigator 查询 align 文件摘要，得到故障原因。
19. investigator 调用 `read_file_digest` 读取真实 align 日志内容证据，检查 bytes、hash 和 preview。
20. investigator 查询 dependency，得到影响 label。
21. investigator 检查提示包含 `HANDOFF` 和 `DEPENDENCY` 原因位，并按提示读取 analyze 摘要，确认提示已经转化为实际工具调用。
22. investigator 输出模板 `LLM_CALL` / `LLM_RESULT` 事件和 `PLAN_CREATED` 事件，引用 summary、digest、dependency 和 prefetch 四条 sequence。
23. investigator 调用 `context_snapshot()` 呈现自身审计历史。
24. investigator 通过消息唤醒 recovery。
25. recovery 通过 capability 检查。
26. recovery 执行 `action_commit align`。
27. recovery 再次执行同一动作，内核返回 duplicate。
28. recovery 更新 report 工件状态并查询 report 文件。
29. recovery 输出带 `corr_id` 和 plan id 的 `AUDIT`、`ACTION`、`ARTIFACT` 和 `FINAL` 事件。
30. orchestrator 等待三个角色 Agent 退出。
31. orchestrator 调用 `agent_audit_snapshot()`，确认本 workflow 审计窗口中出现 sentinel、investigator、recovery 及多类记录。
32. orchestrator 调用 `agent_audit_query()`，在 scope 裁剪后的窗口内按 kind、span、事件、source/target 和 sequence 过滤。
33. orchestrator 调用 `agent_timeline_snapshot()`，检查统一 timeline 中包含 Context、事件、调度和预取交接摘要。
34. orchestrator 调用 `agent_timeline_query()`，按 audit source、prefetch kind、source pid、target pid 和 handoff flags 精确读取 sentinel 到 investigator 的预取交接记录，按 tool id 精确读取 digest 内容证据，并用 after-cursor 检查同一 timeline 可按上一条记录继续读取。
35. orchestrator 调用 `agent_provenance_snapshot()`，确认 message、prefetch 和 digest 内容证据都进入因果图。
36. orchestrator 输出 `labdemo_ucore: passed`。
37. 可信 init 等待 orchestrator 退出，输出 `labdemo_ucore: parent passed`。

### 9.3 输出阅读方式

综合场景输出按事件链阅读：运行对象建立、监听、故障、查询、权限、预取、同 workflow 消息、LLM、恢复、scope 审计、timeline 和 provenance。

### 9.4 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| 多 Agent 并存 | 同时创建 sentinel、investigator、recovery |
| 控制面权限 | 可信 init 只启动 orchestrator；元数据初始化、失败注入和角色 Agent 创建都由 orchestrator 完成 |
| 文件状态事件 | orchestrator 注入 failed 状态后唤醒 sentinel |
| 文件属性查询 | sentinel 查询失败工件 |
| 依赖查询 | investigator 查询 align 的影响范围 |
| 权限控制 | sentinel 恢复动作被拒绝 |
| Agent 间通信 | sentinel 唤醒 investigator，investigator 唤醒 recovery |
| span 预取查询 | investigator 按当前 scope/private-owner span 查询提示 |
| 当前 span 短记录 | investigator 按当前 span 查询 Context、事件和预取交接短记录 |
| Context 审计 | investigator 输出 snapshot |
| Scope 审计 | orchestrator 查询本 workflow 窗口，确认本域多 Agent 摘要可见 |
| 审计过滤查询 | filter 只缩小 scope/owner 裁剪后的记录集合 |
| 统一 timeline | orchestrator 用一个接口读取 Context、事件、调度和预取交接摘要 |
| timeline 过滤查询 | orchestrator 用一个接口按条件读取 prefetch handoff 记录，并按上一条已读记录继续读取 timeline |
| 因果边查询 | orchestrator 用一个接口确认 sentinel 到 investigator 的 message 和 prefetch 触发关系 |
| 幂等恢复 | recovery 第二次 rerun 返回 duplicate |
| 最终状态 | 输出 `FINAL status=RECOVERED` |

## 10. `agentsecurity_ucore`

`agentsecurity_ucore` 是权限限制负向测试。除既有 role、route、wait cancel 和系统事件边界外，本次增加：用户态不能通过 `context_push` 自报 span/cause；跨 Agent provenance 使用可信 source control；低权限遥测和借来的 span 不能进入或淘汰 protected/high 审计效果分区。

### 10.1 测试流程

1. 内核加载的可信初始进程检查自身保持普通进程身份且业务 capability 为零；随后成功创建 orchestrator，证明内核私有 bootstrap 授权生效。
2. 可信初始进程验证普通 mail 可用，并验证事件、IPC、metadata、scope audit、timeline 和两个 metadata bank 受能力与对象边界保护。
3. 可信初始进程普通 `fork()` 后，子进程检查两种 Agent 创建接口和全部合法角色都返回 `AGENT_STATUS_DENIED`。
4. 该普通子进程再执行 `exec()`，重复验证创建授权仍为零，证明授权不会经普通派生链传播。
5. 可信初始进程创建 controller A；A 创建并等待一个 sentinel 就绪，显式建立 A 到 sentinel 的消息路由后退出，使 sentinel 成为孤儿。随后创建 controller B，验证新 control id 即使将来发生 PID/PCB 槽复用，也不能取消该 sentinel 或继承 A 的旧消息路由；该用例本身不直接断言实际复用了同一 PID/PCB。
6. 可信初始进程创建正常 orchestrator；orchestrator 通过 `agent_info()` 检查真实 role 和 capability mask，并通过成功委派各角色验证创建权。
7. orchestrator 普通 `fork()` 后，子进程验证 Agent 身份、role、capability 和 Context 均已清零，且没有创建授权。
8. orchestrator 在未初始化元数据前执行带索引查询，预期返回 0 条命中且不会阻塞，随后初始化文件元数据。
9. orchestrator 使用 legacy `agent_call()` 验证工具名、工具 ID、参数键和参数类型校验。
10. orchestrator 分别把设定的模拟流程和另两个模拟流程的比对处理、报告生成环节置为 failed。
11. orchestrator 创建 sentinel、investigator 和 artifact；低权限 Agent 均检查真实 role/capability，验证保留 `MESSAGE_SEND`、缺少 `WAIT_CANCEL`，并确认创建接口、全部角色委派及对父 orchestrator 的取消均返回 `AGENT_STATUS_DENIED`。
12. sentinel 验证系统事件/role/span/cause 伪造、依赖/内容/whole-scope audit/调度/metadata 越权均被拒绝；未授 route 的直接投递也拒绝。
13. orchestrator 为受控 source/target 显式 grant `MESSAGE` 路由，合法消息成功；随后 revoke 后再次投递被拒绝。测试同时核对事件的 source pid、corr id 和 payload，证明 route 只改变投递授权，不放宽事件来源语义。
14. recovery target 以 `WATCH` 自主接受父 source 的 `LLM_DONE` 路由；父进程先验证该 LLM-only route 拒绝 `MESSAGE`，再通过 `llm_response` 成功投递 `LLM_DONE`。
15. 同一个 orchestrator target 顺序经历 `AGENT_IPC_ROUTE_MAX+2` 个短命 sentinel source；每轮 grant 后 source 都成功投递 MESSAGE，再退出并被回收，第 18 轮仍成功，证明 source 退出会释放路由槽。
16. orchestrator 创建 recovery；recovery 检查真实 role/capability，通过创建和等待取消拒绝验证没有委派或控制授权，并验证重复 corr_id、定向动作及工件更新行为。
17. orchestrator 确认拒绝路径和定向更新没有跨 run 修改，输出 `agentsecurity_ucore: passed`。
18. 可信初始进程等待 orchestrator 后再次派生普通子进程，验证复用已回收 Agent 槽时身份、能力和创建权均已清零；该子测试不观察 IPC route 表。
19. 可信初始进程执行自身 `exec()`，验证 bootstrap 创建授权被撤销，输出 `agentsecurity_ucore: parent passed`。
20. sentinel 构造非零 cause/span 的手动 Context，预期被拒绝且当前可信 span 不变；随后在已授权同 scope MESSAGE 中让 orchestrator 接收由内核认证的 source，provenance 必须指回 sentinel，输出 `trusted_span_authority=1` 和 `trusted_cause_attribution=1`。
21. orchestrator 完成一次由内核确认的特权 `ACTION_COMMIT`，随后 sentinel 制造大量 low 遥测；查询仍能看到 protected effect，同时 noisy principal 受 low=16 限制，输出 `audit_authority_partition=1`。high 每 active principal 保证8条，满表只自滚当前 principal 或回收 inactive principal，不能淘汰其他 active principal。

### 10.2 输出阅读方式

本测试输出围绕可信根授权、`fork/exec` 不继承创建权、低权限角色不可继续委派、stable control id IPC 路由、grant/revoke、target consent、退出回收、`.agentmeta` 保护、真实 role/capability、初始化前索引查询安全、legacy 参数校验、sentinel 角色与系统事件伪造失败、recovery 定向动作和多 run 工件更新展开。2026-07-21 的 QEMU 运行已出现 `route_source_enforced=1`、`route_target_isolated=1`、`ipc_route_authorization=1`、`message_route_lifecycle=1`、`target_route_consent=1` 和 `route_slot_reclaimed=1`；旧 `message_send_preserved=1` 不再作为可信路由证据。完整运行摘录见 [test-record.md](test-record.md)。

### 10.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| 普通进程不能直接投递事件、取消等待或配置调度 | `agent_wake()`、`agent_wait_cancel()`、`agent_sched_config()` 返回 `-1` |
| 普通进程不能配置 Agent IPC | `agent_route_config()` 返回 `-1`，不产生路由或队列副作用 |
| 消息能力不隐含等待控制权 | sentinel、investigator、artifact 和 recovery 均保留 `MESSAGE_SEND`，但缺少 `WAIT_CANCEL`，取消父 orchestrator 返回 `AGENT_STATUS_DENIED` |
| 等待取消绑定不可复用的控制对象 | controller A 退出后创建 controller B；B 的新 control id 取消 A 的遗留子 Agent 被拒绝。测试不直接断言实际 PID/PCB 复用 |
| 消息路由不随控制对象继承 | controller A 退出后，controller B 的新 control id 不能命中 A 的旧 route；独立的 churn 用例另行验证 source 退出释放路由槽 |
| `MESSAGE_SEND` 不等于全局目标权限 | 未 grant 时 `agent_wake`、`send_message` 和 `llm_request` 均返回 `AGENT_STATUS_DENIED`；显式 grant 后成功，revoke 后再次拒绝 |
| 路由管理有控制边界 | orchestrator 控制 source/target 的 grant/revoke 有专项断言；`target_route_consent=1` 验证 target 持有 `WATCH` 时可自主接受父 source 的 LLM_DONE，且 LLM-only route 不放行 MESSAGE |
| 路由槽回收 | `route_slot_reclaimed=1` 让同一 target 顺序经历 `ROUTE_MAX+2` 个短命 source，每轮 route 投递均成功，证明 source 退出后槽可供后续来源使用 |
| 慢订阅者广播隔离 | full watcher 在前、later watcher 在后；`broadcast_slow_watcher_isolated=1` 已确认后者收到同一 attributed 广播 |
| Agent 不能用消息接口伪造系统事件 | sentinel 直接投递 `LLM_DONE` 返回 `AGENT_STATUS_DENIED` 且不入队；非法类型返回 `AGENT_STATUS_BAD_PARAM`；合法 `MESSAGE` 仍需先命中显式路由 |
| Whole-scope 审计读取、过滤、调度配置和依赖注册权限 | 普通进程返回 `-1`，sentinel 缺相应能力时返回拒绝 |
| 普通进程不能直接修改文件元数据 | `agent_file_meta_init()`、`agent_file_meta_set()` 返回 `-1` |
| 普通进程不能直接访问 metadata 双 bank | 对 `.agentmeta` 和 `.agentmeta1` 的 `open`、`open(O_CREATE)`、`unlink` 均返回 `-1` |
| 普通进程 mail 基础路径可用 | `mailwrite()` 写入，`mailread()` 读回同一内容 |
| Agent 创建权有明确可信根 | 仅内核加载的初始进程获得 bootstrap 角色授权；普通 `fork` 子进程不继承 |
| 普通派生链不可铸造 Agent | 普通子进程在 `exec` 前后调用两种创建接口均被拒绝 |
| 角色委派不能向低权限 Agent 扩散 | orchestrator 可显式创建角色；sentinel、investigator 和 recovery 调用所有创建形式均被拒绝 |
| Agent 进程槽复用不残留权限 | orchestrator 回收后派生普通子进程，身份、capability、Context 和创建权均为普通进程状态 |
| bootstrap 授权按映像生命周期撤销 | 可信初始进程执行普通 `exec` 后，所有 Agent 创建请求均被拒绝 |
| 初始化前索引查询安全 | 未调用 `agent_file_meta_init()` 前，索引查询返回 0 条命中且不阻塞 |
| legacy 工具名和工具 ID 不一致会失败 | `agent_call()` 返回 `AGENT_STATUS_BAD_REQUEST` 和 `tool_mismatch` |
| legacy 参数键和类型校验 | 错误参数返回 `AGENT_STATUS_BAD_PARAM`，syscall-only 工具不能走 batch |
| 用户态 role 参数不可信 | sentinel 伪造 recovery 仍被拒绝 |
| 文件状态拒绝路径无副作用 | sentinel 伪造 rerun 后 align 仍为 failed |
| recovery 权限来自真实 PCB 字段 | recovery 即使传入 sentinel role，也能按真实权限恢复 |
| 重复动作被识别 | 相同 corr_id 第二次 action 返回 duplicate |
| 多 run 动作和工件更新不会误伤 | 只更新 selector 指定的另一个模拟流程，设定的模拟流程保持 failed |
| 用户不能伪造 trusted span/cause | `context_push()` 非零 cause/span 被拒绝，当前 span 不改变；跨 Agent provenance 使用内核私有 source control，输出 `trusted_span_authority=1 trusted_cause_attribution=1` |
| 审计 authority 分区 | telemetry 永远进入 low，成功特权状态效果才进入 high；low principal16、high active principal8，其他 active principal 不能互相淘汰，输出 `audit_authority_partition=1` |

## 11. `agenttrust_ucore`

`agenttrust_ucore` 验证 Agent 角色不能只依赖用户态自报名称，而必须绑定构建期执行策略清单中的可信映像身份。这里的可信根是内核使用的策略清单和密封 inode 元数据，不等同于密码学签名或运行时文件哈希。

### 11.1 测试流程

1. 验证数据页不可执行、代码页不可写，同时普通数据仍可写，检查用户映像 W^X。
2. 对可信 orchestrator 映像尝试写打开、读写打开、截断、覆盖创建和删除，均应失败；重新读取的内容保持不变。
3. bootstrap 进程尝试直接创建非授权 sentinel 角色，必须被拒绝。
4. 创建 orchestrator 后执行与该角色绑定的可信映像，执行成功且角色仍为 orchestrator。
5. 分别执行绑定到错误角色的密封映像和普通复制出的未可信映像，均不能以原 orchestrator 身份运行。

### 11.2 通过标记与覆盖结论

关注 `wx_image=1`、`immutable_image=1`、`bootstrap_role_boundary=1`、`trusted_agent_exec=1`、`role_image_binding=1` 和 `parent passed`。这些标记共同覆盖页权限、密封映像不可变、bootstrap 授权范围，以及角色到可信执行映像的精确绑定。

## 12. `agentvfs_ucore`

`agentvfs_ucore` 验证普通 `open/read/write/unlink` 路径与 Agent 文件能力使用同一套内核授权机制，防止绕过 `CONTENT_READ`、`ARTIFACT_WRITE` 等能力。受保护文件的身份和委派状态以 `dev + inum + incarnation` 为基础，而不是只相信路径字符串。

### 12.1 测试流程

1. 先后以“公共文件先创建”和“工作流文件先创建”两种顺序制造同名对象，验证公共命名空间与工作流命名空间互不覆盖。
2. sentinel 对受保护文件的读、写、截断和删除均被拒绝；investigator 只能读取，不能写入、截断、删除或取得编辑租约。
3. 普通 `fork()` 子进程失去 Agent 身份和文件能力；即使继承了父进程已打开的文件描述符，后续读写仍会按当前进程凭据重新鉴权并被拒绝。
4. orchestrator 只能向 mkfs 生成的 immutable、domain-safe worker 映像委派精确能力。空能力、未知位、超过映像 profile 上限、错误映像和跨映像执行都被拒绝或降权；普通进程直接执行该映像不会获得 workflow 权限。
5. 写能力不足时，带创建/截断意图的 `open()` 在改变文件系统之前失败，验证失败事务没有留下半创建或半截断状态。
6. 普通进程不能访问两个 metadata bank；元数据查询只定位工作流命名空间中的对象，不会误绑定同名公共文件。

### 12.2 通过标记与覆盖结论

关注辅助程序输出的 `failed_open_atomic=1`、`cross_image_attenuated=1`、`wrong_first_exec_attenuated=1`、`sealed_exec_no_elevation=1`，以及主程序的 `inherited_fd_revalidated=1`、`protected_paths=1` 和 `parent passed`。测试同时覆盖打开时授权、描述符使用时重新鉴权、映像绑定委派和命名空间隔离。

## 13. `usersafety_ucore`

`usersafety_ucore` 是 syscall 用户输入与事务复测，目标是让错误输入稳定返回失败，而不是访问无效范围、破坏内核状态或错误提交部分结果。

### 13.1 测试流程

1. 对指针加法溢出、跨页缓冲区、只读/不可访问用户地址和未终止字符串执行负向调用。
2. 检查 `exec` 参数数量超限和不可访问的 argv 指针，并拒绝非法线程入口；失败的 `exec` 保留原地址空间，成功的 `exec` 完成事务切换。
3. 检查无关子进程退出不会错误唤醒互斥等待者，唤醒只作用于目标等待队列。
4. 在并发关闭描述符时检查阻塞管道 syscall 持有的临时引用；检查失败读写不会错误推进管道状态。
5. 检查 `wait` 和时间 syscall 的 copyout 失败不提交状态，错误的 `wait` 输出地址不会提前回收子进程。
6. 检查文件描述符方向、fd 槽不足时打开既有文件和创建 pipe 的引用回滚，以及信号量负值、非法 ID 和计数溢出。

### 13.2 通过标记与覆盖结论

测试为每组输出 `live after ...`，包括 `pointer bounds`、`string bounds`、`exec argv bounds`、`thread boundaries`、`directed wakeup`、`pipe buffers`、`wait copyout`、`time copyout`、`fd directions`、`file rollback`、`semaphore inputs`、`failed exec transaction` 和 `successful exec transaction`。最终标记为 `usersafety_ucore: parent passed`。

## 14. 文件系统 ENOSPC 复测

入口为 `make fs-enospc-test` 或 `bash scripts/run-fs-enospc-tests.sh`。脚本先编译运行共享容量策略单测，核对当前平台精确 G/S、superblock 契约 checksum、稳定 PUBLIC principal、SYSTEM 信用耗尽后的重启条件和运行期不足拒绝；随后构造无法覆盖 `S+4G` 的镜像，要求 mkfs 以明确诊断拒绝。baseline mkfs 还分别构造空根目录和恰好 64 个目录项的块对齐根目录，直接核对 dinode size 与映射块一致，防止挂载清扫把构建期目录空洞放大为启动 panic。之后脚本构建极小文件系统镜像，在 AgentOS-uCore 与 `baseline_ucore/` 上分别运行 `fsenospc_ucore`，依次耗尽磁盘 inode、内存 inode cache 和数据块；AgentOS-uCore 还以“低主体上限”和“全局保留水位”两种配置运行 `fsquota_ucore`。最后，两个目标都运行 `fspquota_ucore` 的 crash/seed/verify 三启动持久主体回归。

测试要求完整失败返回 `-1`、部分写入返回短写长度、内核不 panic，并在释放资源后再次分配成功。通用目标的程序标记包括 `inode exhaustion survived`、`inode cache exhaustion survived`、`block exhaustion survived` 和 `parent passed`；所有通用、持久主体与 Agent 配额场景通过后，脚本输出 `[fs-enospc] generic, persistent principal, and Agent quota cases passed`。

`fsquota_ucore` 的 PUBLIC 子进程先循环 640 次创建文件、在描述符仍打开时删除目录项、继续写入，再关闭最后引用。循环次数严格超过旧全局版本表的 512 槽容量，既覆盖“unlink 不是最终生命期”的语义，也迫使每个 incarnation 走最终 inode/sidecar 回收。随后测试继续施加块和 inode 压力，并由 workflow orchestrator 创建工件、提交编辑版本、连续读取两次内容摘要以确认 digest cache 命中。关键标记为 `public_version_churn=1 cycles=640`、`workflow_version_reserve=1` 和 `content_version_reserve=1`；它们与既有 `workflow_reserve=1`、`kernel_metadata_reserve=1` 一起证明 PUBLIC 域不能耗尽工作流的存储或版本状态。这里的 QEMU 用例证明分配水位和回收行为；四个 scope 的数值容量契约由共享策略单测、mkfs 负例、版本化 superblock 校验和 admission 检查共同证明，不把单个 `scope_storage_quota` 标记夸大为全盘块压力证明。

`fspquota_ucore` 专门区分“短命进程资源域”和“持久存储主体”。第一次启动先持久化 phase 文件，再创建 1 block PUBLIC 文件、删除目录项但保持描述符打开；输出 `crash_orphan_ready=1` 后 runner 直接终止 QEMU。第二次挂载必须在计费前回收该不可达 inode/block，否则后续精确上限断言无法完成。为避免“只忽略计费但不释放物理状态”的假通过，runner 还保存 mkfs 基线并在 seed 后直接解析 raw image，要求 bitmap 分配与非 FREE qmap owner 都只比基线增加 4 个数据块，dinode 只增加 7 个。镜像还带入一个 SYSTEM 赞助、可变 PUBLIC 的 13 数据块文件；PUBLIC 子进程首次覆盖它时，内核必须把 inode、12 个直接块、间接索引块和第 13 个数据块整体接管，共计 14 block/1 inode。子进程再创建其余对象，恰好占满 18 block/8 inode 上限后完整退出；init 只在回收该域后输出 `sponsored_object_charged=1 blocks=14` 和 `durable_fixture=1 blocks=18 inodes=8 owner_exited=1`，runner 再次关闭 QEMU但保留镜像。第三次以同一 kernel/镜像启动时，新 PUBLIC 域先读取旧内容，再验证额外 block 与 inode 都被拒绝；通过 `O_TRUNC` 释放一个 block、通过 `unlink` 释放一个 inode并分别复用，之后再次增长仍被拒绝。该域退出后，另一个新域仍被旧文件计费；最终清理持久 fixture 后才能重新分配。顺序标记为 `crash_orphan_ready=1`、`reboot_charge_persisted=1`、`deletion_reuse=1`、`relaunch_charge_persisted=1 launches=2`、`cleanup_reuse=1` 和 `parent passed`。AgentOS 与 baseline 使用相同用户程序和三阶段 runner，因此这个回归不依赖 Agent 角色或某个 PID 特判。

该测试对应的磁盘契约将 PUBLIC principal 写入 superblock，并提升策略/owner 格式版本。挂载时从 qmap 和 dinode 分别重建 block/inode 用量；缺少该字段的旧镜像会因版本或布局不匹配被明确拒绝，而不是以空账本继续运行。

该入口没有被 `make full-verify` 串联，完整验收时必须单独执行。

## 15. 进程回收与配额复测

入口为 `make proc-reap-test` 或 `bash scripts/run-proc-reap-tests.sh`。脚本在增强目标运行普通生命周期测试与 adversarial Agent 测试，并在 `baseline_ucore/` 运行普通生命周期测试。

`procreap_ucore` 覆盖子进程先退出、父进程先退出、孤儿资源回收、阻塞 syscall 撤销、等待队列取消、无人 `wait()` 的父进程隔离、资源域活进程上限、谱系绕过拒绝、配额归还和同级资源域隔离。`procreap_agent_ucore` 进一步覆盖高压退出调度、普通域压力隔离、Agent 系统保留槽和恶意 Agent 行为。最终标记分别为两个程序的 `parent passed` 和脚本的 `[proc-reap] both targets passed`。

`make full-verify` 会串联本项，但如果此前的宿主机检查失败，流程会在到达该阶段前中止。

## 16. 内核栈预算检查

根目录与 `baseline_ucore/` 的每个 `build/kernel` 规则都会在链接前调用 `scripts/check-kernel-stack-usage.py`，因此栈检查随内核构建自动执行，而不只是一个可选测试。脚本读取编译器 callgraph 和函数栈帧数据，计算用户陷入路径、内核中断路径、入口帧与安全余量的总预算；超出配置栈大小、出现未建模递归/间接调用或单帧越过 guard 上限都会阻止构建。

独立复查命令为：

```bash
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
make -C baseline_ucore kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

成功时输出 `kernel stack budget`、`kernel stack user path` 和 `kernel stack interrupt path`；失败时输出 `kernel stack check failed` 并返回非零状态。运行时的每进程内核栈还使用不可映射 guard page 和 canary 检查，构建期预算与运行时防护共同覆盖栈溢出。

## 17. 运行方式和复现建议

推荐用脚本运行 AgentOS 专项测试：

```bash
bash scripts/run-agent-tests.sh
```

该脚本包含 `agenttrust_ucore`、`agentvfs_ucore` 和 `usersafety_ucore`，但不包含 ENOSPC 与进程回收脚本。资源安全入口和聚合验证的实际串联关系见 [verification.md](verification.md)。如果需要单独复现其他场景，运行命令见 [scenario-script.md](scenario-script.md)。

当前不能把 `make full-verify` 记为完整通过：最近一次运行在 `host_tools/test_plain_ucore_reader_e2e.py` 的 `Reader GET Routes` 页面断言处中止，尚未进入后续 QEMU 阶段。该 Reader E2E 问题应与上述内核专项结果分开记录。

不要在同一工作树中并行启动多个 QEMU 测试，因为 `nfs/fs-copy.img` 会被多个进程同时访问，可能造成镜像锁冲突。

## 18. `agentscope_ucore`

`agentscope_ucore` 是本次可信 workflow scope 的机制回归。它不靠固定 PID、文件名白名单或角色特判，而是同时建立两个由 syscall 541 创建的动态 scope，并故意在两域使用相同文件名、fid、namespace、run 和 action selector。

测试流程：

1. 可信 bootstrap factory 对命令/回复 pipe 的指定端点调用 syscall 542，再分别创建 scope A/B；未委派 pipe 和普通 fd 不跨边界，一次性票据在边界尝试后消失。
2. 两个根 Agent 的 `filesystem_domain` 都不小于 3 且彼此不同；数值 2 保留为稳定 PUBLIC 存储 principal。同 scope `agent_create_role()` 子 Agent 继承 scope，scope 内 orchestrator 不能再调用 workflow factory。
3. A/B 创建同名文件和相同 metadata/fid，各自只能查询、打开和读取自己的对象。
4. B 创建不带 `PERSIST` 的内存态 metadata，A 强制重载自己的 bank 记录后，B 的记录仍可查询，证明 `file_meta_init` 不能跨 scope 清表。
5. target consent、route grant 和 MESSAGE 投递跨 scope 均拒绝；同 scope stable route 仍可协作。
6. A 在同一 scope 内同时启动三个 Orchestrator；inactive bank 完整写入且 VFS 对象释放后，owner 在不释放事务 token 的前提下协作让出一次 CPU，使其他 writer 可达等待队列。三者各自持续创建并提交 `PERSIST` metadata，并通过每进程 `metadata_txn_wait_count` 要求至少两个 writer 实际睡眠等待事务门。结束后强制从双 bank 重载，再按 run 查询必须完整得到三组记录。事务释放采用专属队列广播，所有 waiter 都在锁内重新检查 owner，不依赖单个线程继续传递唤醒。
7. B 创建不带 `PERSIST` 的真实 volatile 对象，再由低权限 Artifact 连续执行 32 次单字节写入；前后 scope-local request/commit 计数必须完全不变，证明内存态记录不会制造不包含自身的空 bank checkpoint。
8. A 的存储配额阶段先创建 120 个文件并占满本 scope 的 112 个 metadata 槽。随后低权限 Artifact 同时微写一个已绑定持久对象和经查询确认未绑定的超额对象，至少完成 128 轮；第 16 轮后通过 guest pipe 发出存活屏障，直到主进程明确停止前持续施压。B 在该存活窗口内完成 32 次本域 metadata 查询并主动让出 CPU，guest `get_mtime()` 要求总延迟不超过 5 秒。
9. 测试读取 scope-local telemetry，要求持久变化全部进入记账、合并请求加成功批次等于总请求、完整 bank checkpoint 不超过请求数的八分之一；全局 scan runs 增量还必须满足 20 tick 非滑动 cooldown 的轮次上界。若攻击全程落在已有自适应 cooldown 内，零轮扫描也是合法结果。具体写入/批次数受调度时序影响，不把某次观测值固化为契约。
10. A 在写回完成后要求 `dirty_generation == durable_generation` 且 pending 清零，再强制从双 bank 重载并比较 size 和文件代数，证明异步合并没有丢失最终状态。
11. 相同 action selector 在两个 scope 各自拥有幂等历史；audit/event/query 只返回本 scope，公开 PID/span 不扩大范围。
12. A 的编辑 lease/version 不能由 B 查询、提交或终止。
13. 同 scope 多进程共享 workflow 存储计费并受同一 scope limit；从该 workflow 明确降级出的普通子进程改用安装级 PUBLIC principal，不能继续借用短命进程资源域作为磁盘身份。其他 admitted/future scope 的数值保证另由共享 policy 单测、mkfs 初始 `S+4G` 契约、挂载 `4G` 复核和原子 admission 检查覆盖。
14. 同时占用4个 workflow admission 后第5个创建失败；释放一个后可创建替代 scope。最后成员退出触发 retirement，回收 metadata/action/lease/audit/prefetch/IPC 等表后槽可复用。

预期回归标记包括：

```text
agentscope_ucore: cross_scope_isolation=1
agentscope_ucore: ipc_scope_isolation=1
agentscope_ucore: same_scope_collaboration=1
agentscope_ucore: metadata_transactions=1
agentscope_ucore: scope_storage_quota=1
agentscope_ucore: scope_reload_isolation=1
agentscope_ucore: metadata_write_coalescing=1 writes=<at-least-128> commits=<bounded>
agentscope_ucore: metadata_cross_scope_progress=1 queries=32 latency_ms=<at-most-5000>
agentscope_ucore: metadata_final_consistency=1
agentscope_ucore: metadata_volatile_no_writeback=1 writes=32
agentscope_ucore: metadata_scan_pressure_bounded=1
agentscope_ucore: action_scope_isolation=1
agentscope_ucore: audit_event_scope_isolation=1
agentscope_ucore: lease_scope_isolation=1
agentscope_ucore: scope_capacity_reservation=1
agentscope_ucore: transactional_fd_delegation=1
agentscope_ucore: lifecycle_reclamation=1
agentscope_ucore: parent passed
```

以上全部标记均已出现在 2026-07-22 从 clean user/kernel 构建开始的完整 15/15 Agent QEMU 回归中。动态断言证明两域隔离、同域协作、事务等待、微小写入的有界合并、volatile 分流、满表扫描限流、跨 scope 有界进展、强制重载后的最终一致性、配额边界、fd 委派与生命周期回收；四份 workflow 数值保证仍由共享策略单测、mkfs 容量负例和挂载/admission 契约共同证明，不从单个输出标记外推。

## 19. syscall 内核工作预算复测

入口为 `make syscall-fairness-test` 或 `bash scripts/run-syscall-fairness-tests.sh`。脚本为根目录 AgentOS-uCore 和 `baseline_ucore/` 分别构建独立临时镜像，运行同一个 `syscallfair_ucore`，不复用可被其他测试修改的 `fs-copy.img`。测试的同步与因果关系全部由 Guest 内的 pipe、进程和线程建立；宿主运行器把 QEMU stdin 设为 `/dev/null`，只采集原始输出，不注入字符或参与唤醒。

控制台阶段先让同级进程阻塞在 Guest pipe gate。writer 释放 gate 后，以一次 `write(stdout, ..., 64 KiB)` 输出同时含 BEGIN/END 的数据；peer 读到 gate 后主动 `sched_yield()`，再输出进展标记。脚本要求：

```text
SYSCALLFAIR_CONSOLE_BEGIN < SYSCALLFAIR_CONSOLE_PEER < SYSCALLFAIR_CONSOLE_END
```

因为 BEGIN 和 END 位于同一个 64 KiB write buffer，这个顺序证明控制台 syscall 返回前发生过安全点调度。

inode 阶段创建共享地址空间的 observer 线程。observer 反复从独立 fd 读取仍为空的目标文件，writer 首次返回必须满足 `0 < n < 64 KiB`，并立即通过只读的 `kernel_work_last_preemptions()` 要求前一个 syscall 至少经历一次内核态重调度；随后等待 observer 读到已提交数据、输出 `SYSCALLFAIR_INODE_SHORT` 并循环完成剩余数据。脚本要求 `INODE_BEGIN < INODE_PEER < INODE_SHORT < INODE_END`。重调度计数提供 write 内部的因果证据，observer 独立证明 peer 取得进展；二者动态验证真实 inode 数据和共享 file offset 的已提交前缀，而不把 write 返回后的用户态 timer 抢占误算成 syscall 内调度。AgentOS 专属 size 发布 sidecar、query overlay 与持久化 sequence 由源码审查覆盖，本双目标用例不把普通 inode read 夸大为 metadata query 证据。

截断阶段先填充一个 64 KiB 文件，再在同一进程创建 observer 线程。主线程输出 TRUNC_BEGIN 后调用 `open(path, O_WRONLY | O_TRUNC)`，并用 last-syscall 重调度计数要求该 open 在内核态跨过调度边界；observer 连续两轮打开文件并读到 EOF，两轮之间主动让出，随后输出 TRUNC_PEER。脚本要求 `TRUNC_BEGIN < TRUNC_PEER < TRUNC_END`。计数提供 open 内部的因果证据，observer 独立证明原子 detach 后的 EOF 对其他线程可见；二者不依赖 open 返回后到共享标志写入前是否发生用户态 timer 抢占。

三个阶段的每个标记都必须只出现一次。最外层父进程只在 fairness worker 被 `waitpid()` 完整回收后输出 `syscallfair_ucore: parent passed`；宿主 runner 随后要求 QEMU 在 5 秒内正常关机，日志不得包含 panic、非法地址或未知 syscall。这一部分验证退出完整性，不单独宣称证明退出清理内部的公平边界。两个目标都满足后输出 `[syscall-fairness] both targets passed`。终审复测完成后，这一契约将动态覆盖控制台、普通 inode I/O 和截断回收；当前实际动态证据只覆盖基础控制台轮。源码检查覆盖 pipe、exec/fork 分页、VM snapshot 屏障和 Agent batch 安全点；它不等于穷尽任意 syscall 路径。固定上界目录扫描和仅可信 Agent 可达的 metadata raw I/O 仍是残余覆盖。

## 20. 全局文件对象表资源配额复测

入口为 `make file-resource-test` 或 `bash scripts/run-file-resource-tests.sh`。脚本用 `CHAPTER=file_resource` 为根目录 AgentOS-uCore 和 `baseline_ucore/` 分别构建同一个 `fileresource_ucore`，并把 filepool、普通全局水位、普通域上限和受控域上限分别缩小为 64、48、16、16，使 Guest 可以精确填满边界。本次独立运行中，AgentOS、baseline 和汇总检查均输出 passed。该资源专项也已接入 `make full-verify`，但本次没有运行聚合 `full-verify`，不能把专项通过外推为聚合验证通过。

测试首先让线程阻塞在空 pipe 的 `read()`，随后关闭原读 FD。另一个同资源域进程仍有空闲 FD，但隐藏临时引用必须继续占用创建者域的 filepool 配额；达到域上限后的额外 `open()` 被拒绝。持有阻塞线程的进程退出后，协作撤销路径使引用降到零，随后完整容量可再次使用，分别输出 `blocking_pin_bounded=1` 和 `exit_reuse=1`。

第二阶段把同一 ordinary 域推进到 15/16 槽，连续十六次请求需要两个 file 对象的 pipe。每次第二端分配失败时，第一端及两个 FD reservation 都必须完整回滚；否则即使每轮只泄漏一个 reservation，也足以占满该进程剩余的 14 个 FD 槽。测试随后关闭一个单文件对象，把域用量降至 14/16，并要求一个完整 pipe 立即成功，从动态结果同时证明 file 对象计费与 FD reservation 没有泄漏；关闭 pipe 后再把域精确填至 16 槽并验证额外 `open()` 被拒绝。第三阶段让三个独立 ordinary 域各占 16 槽，合计填满 48 槽普通水位；第四个 ordinary 域被拒绝时，bootstrap/reserved 进程仍能够 `open()` 和创建 pipe。实际顺序标记为：

```text
fileresource_ucore: blocking_pin_bounded=1
fileresource_ucore: exit_reuse=1
fileresource_ucore: pipe_rollback=1
fileresource_ucore: domain_limit=1
fileresource_ucore: ordinary_waterline=1
fileresource_ucore: reserved_progress=1
fileresource_ucore: parent passed
```

这些场景验证的计费单位是唯一 filepool 槽：fork 和 syscall pin 增加引用但不重复扣账，最终引用关闭才退款。主目标和 baseline 运行同一 Guest 断言并均通过，避免只在 AgentOS 专属角色路径上得到结果。runner 最终输出 `[file-resource] both targets passed`。
