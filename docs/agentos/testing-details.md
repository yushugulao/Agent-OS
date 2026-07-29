# 测试内容详细说明

本文档解释AgentOS-uCore 测试程序的内部步骤、覆盖范围和预期输出。测试入口和运行命令见 [verification.md](verification.md)。预期 marker 是验收合同，不等于已经运行；发布是否通过只从 `evidence/releases/INDEX.md` 指向的 C→E release bundle 及其 `manifest.json`、canonical LF Guest 日志和强类型原始产物读取。没有远端 Runner 只令 `remote_ci.status=not-attached`、阻止 E4，不阻止干净 C 的本地完整验证形成 E3。

## 1. `agentfinal_ucore`

`agentfinal_ucore` 是最终正确性测试，重点覆盖任务一、任务二、任务三，同时检查任务四文件索引和任务五事件自唤醒是否可用。

### 1.1 测试流程

独立构建同时启用 `AGENT_CONTEXT_SYNC_TEST_PROFILE` 与 `WAIT_ATOMIC_TEST_PROFILE`。Context 部分先填满 128 条 FIFO，保存整个 managed mirror 与 oldest detail，再分别注入 append、rollback、clear 的预检失败；每次都在任何 snapshot 修复前逐字节比较 mirror，并核对 header、records、latest、detail、branch/hash/counter 不变，最后验证一次正常 append 淘汰 oldest 并恢复提交。wait 部分动态覆盖有限/无限与不同 deadline 的 keyed timer、线程槽 generation 重用、事件 reserve 后无 waiter 的原子发布，以及最后 sibling teardown 的重检/发布窗口，要求 `agentfinal_ucore: thread_wait_deadlines finite_infinite=1 distinct_deadlines=1 keyed_timer=1 loop_aggregate=1 slot_reuse=1` 与 `agentfinal_ucore: wait_publication_atomic=1 event_wake_none=1 event_no_sleep=1 sibling_wake_none=1 teardown_completed=1`。该 prelude 使用独立镜像和临时 timing file，与下述生产配置 18-case 分开；它不是第 19 个 case，也不得计入 18-case calibration。是否实际通过由 C 对应 bundle 的 profile 日志决定，不能由 marker 字符串推断。

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
7. 子进程先建立三条旧分支记录，rollback 到第一条；snapshot/query 只能看到当前 active path，`context_detail()` 与物理 mirror 中的 archive 仍保留被放弃记录，新记录继续使用全局 sequence=4 并以独立 path parent 指回锚点。用户态 mirror helper 还要拒绝人为制造的环，随后由可信 snapshot 恢复镜像。
8. 子进程调用 `context_clear()`，保证后续 sequence 从干净状态开始。
9. 子进程构造 64 个 echo 操作，使用一次 `agent_run()` 批量执行。
10. 子进程检查：
   - batch 返回值等于 64；
   - 第一条 result 的 sequence 为 1；
   - 最后一条 result 的 sequence 为 64；
   - 直接读取 latest result 的 sequence 为 64。
11. 子进程调用 `context_snapshot()`，检查返回 64 条有序记录。
12. 子进程检查第一条记录是 root，第二条记录指向第一条记录，并检查 span 连续。
13. 子进程检查 header 中的当前 cause/span、provenance edge 计数和 latest record hash；单进程 Context Path 中每条记录的 `prev_hash` 必须指向上一条 Context 记录。
14. 子进程检查第 8 条记录的 payload/result 短文本为 `ucore-final`。
15. 子进程调用 `context_detail()`，检查完整 `agent_op`、完整 `agent_result` 和 `SYSTEM` flag。
16. 子进程手动篡改用户态 Context 镜像中的第一条记录 sequence。
17. 子进程再次调用 `context_snapshot()`。
18. 子进程检查 snapshot 返回的第一条记录仍为原始 sequence，并检查用户镜像被刷新。
19. 子进程向 `header.user_cache_offset` 写入结构化 query cache，再次调用 `context_snapshot()` 后检查 cache 内容仍保留。
20. 子进程以 cause=0、span=0 调用 `context_push()` 追加手动记录，检查 `MANUAL` flag 和 detail ring；可信 cause/span 由内核接入，用户非零自报值由安全测试拒绝。
21. 子进程继续批量写入 128 条记录，使总记录达到 193 条。
22. 子进程再次 snapshot，检查 FIFO 淘汰：
   - count 为 128；
   - oldest 为 66；
   - latest 为 193；
   - dropped 为 65。
   - active path count 为 128，active oldest 为 66，证明已淘汰祖先不会钉住或泄漏到当前视图。
23. 子进程调用 `agent_file_meta_init()` 初始化文件元数据。
24. 子进程按示例项目、设定的模拟流程和比对处理环节查询文件。
25. 子进程检查查询命中，且 `used_index == 1`。
26. 子进程调用 `agent_file_prefetch_snapshot()`，检查本次文件查询产生了对象标签依赖预取提示。
27. 子进程调用 `agent_file_prefetch_span_snapshot()`，检查同 scope/private-owner span 分区包含当前 Agent 提示，并带有 `SPAN_BUS`、source pid 和 target pid。
28. 子进程使用只提供 `tool_name` 的 `agent_call()` 依次验证 `echo`、`query_file`、`pid_info`、`read_file_digest`、`dependency_update` 和 `dependency_query`。
29. 子进程注册 message watch。
30. 子进程用 `agent_wake()` 向自己投递事件。
31. 子进程调用 `agent_wait()`，检查成功收到 `self wake`。
32. 子进程调用 `agent_trace_snapshot()`，检查返回记录中同时包含 Context 记录、调度原因记录和 `agent_wait()` 事件消费记录，并检查记录按 tick 排列。
33. 子进程调用 `agent_span_trace_snapshot()`，检查当前 span 的系统级短记录中包含 Context 和事件记录，并检查返回记录都属于当前 span。
34. 子进程调用 `agent_timeline_snapshot()`，检查统一 timeline 同时包含 Context、调度、审计和预取提示来源，并检查 tick 顺序。
35. 子进程调用 `agent_timeline_query()`，检查 source mask 只返回 audit 来源，start tick 只返回指定 tick 之后的记录，并检查 after-cursor 只返回上一条已读记录之后的记录。
36. 子进程调用 `agent_timeline_wait()`，先验证等待未来 Context 记录会 timeout；再注册 TIMER watch 和 heartbeat，验证纯 Audit 写入不会增加 Context-only 等待的 timeline wake 计数；随后验证 AUDIT+MESSAGE 条件不会被 TIMER audit 唤醒；最后验证 AUDIT+TIMER 条件会被内核新记录唤醒，用同一 filter 查询到记录，并用 `agent_timeline_read()` 在一次 syscall 内等待和取回记录。
37. 子进程调用 `agent_provenance_snapshot()`，检查 Context 因果边和 audit 因果边均可见。
38. 子进程调用 `agent_ledger_snapshot()` 读取当前 scope 摘要。物理 sequence 可因其他 scope 写入而跳号，low/high/principal 滚动也会产生窗口缺口；测试只对无 gap 的相邻可见记录检查直接 hash 邻接，并要求 gap 数量能由 `dropped_records` 覆盖，链尾等于 scope-local `ledger_hash`。
39. 子进程输出 `agentfinal_ucore: passed` 并退出。
40. 父进程等待子进程退出，检查退出状态为 0，输出 `agentfinal_ucore: parent passed`。

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
| rollback active path | query/snapshot 只返回当前路径；detail/archive 保留旧分支；direct helper 拒绝环；sequence 不复用 |
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
4. 子进程轮询第二个 probe，确认它最终由有界后台扫描纳入查询，再删除该 probe；随后用 14/15 字节边界名验证超长 `O_CREATE` 被拒绝且不留下截断 alias，并确认合法边界文件仍可写入 metadata、强制重载和查询。
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
15. 子进程重复执行同一个索引查询，确认每次都实际遍历候选链，且 `plan_reason` 不带 `AGENT_FILE_QUERY_REASON_CACHE_HIT`。
16. 子进程写入接近 128 条真实文件元数据，制造足够的数据量。
17. 子进程分别运行扫描查询和索引查询，检查索引路径的 `scanned_records` 明显更少。
18. 子进程检查查询计划：扫描路径必须返回 `AGENT_FILE_QUERY_PLAN_SCAN`，索引路径必须返回 `AGENT_FILE_QUERY_PLAN_STATUS_INDEX`，并带有 status 索引原因、索引桶和候选记录数。
19. 子进程调用 `dependency_query`，分别带入设定的模拟流程和备用模拟流程，检查同名 label 的依赖结果按 run_id 分开。
20. 子进程调用 `dependency_update` 注册一条 `source -> target` 通用对象依赖，再用 `dependency_query` 验证该依赖可见。
21. 子进程提交覆盖主对象和依赖对象的 `action_commit`，要求一次 syscall 发生 kernel-work 重调度、每个槽只更新一次、主对象与依赖对象摘要正确，并确认纯状态提交不改变 dependency generation。
22. 子进程执行默认 align 查询并读取预取提示，要求提示由对象标签依赖产生、使用 label 索引计划、只指向当前 run、target fid 不重复、数量不超过 8，且该查询的 last-syscall 重调度计数大于 0。
23. 子进程创建等待消息的 Recovery 目标和独立 churner；发送者在预取交接预算检查点让目标消费事件并退出，churner 随即创建 replacement 复用释放的进程槽。发送 syscall 恢复后才允许 replacement 检查状态，要求它没有收到旧目标的 hint 或 mailbox，证明交接提交按稳定端点重校验而非跨检查点保存 PCB 指针。
24. 子进程清空某条记录的 status，重新执行查询并确认没有返回旧结果。
25. 子进程删除绑定文件，确认关联元数据随文件删除被清理。
26. 子进程调用 `action_commit` 指向不存在的 selector，确认返回 `AGENT_STATUS_NOT_FOUND`。

### 2.2 输出阅读方式

本测试的输出重点是启动早期绑定、后台扫描、目录项名称兼容、selector 一致性、inode 生命周期 guard、私有 `.agentmeta` 重新加载、内容摘要、无内核结果 cache 的真实查询执行、scan/index 候选差异、显式依赖与兼容位图按需解析、字段驱动批量 action、有界预取和交接端点复用。阅读时关注 `partial_update_binding`、`preload_create_query`、`dirent_name_bound=14 legacy_alias=1 metadata_canonical=1`、`selector_consistency`、`stale_identity_guard`、`metadata_action_bounded=1 field_driven=1 batched=1`、`prefetch_hints=1 bounded=1`、`handoff_target_exit=1 endpoint_reuse=1`、`demo_inode`、`custom_inode`、`content_digest`、`digest_cache_invalidated`、`.agentmeta_reload`、`query_execution_isolated`、`bulk_index`、`query_plan`、`dependency_update`、`delete_clears_metadata` 和最终通过标记。完整样例输出见 [test-record.md](test-record.md)。

### 2.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| 启动早期真实对象协调 | 字段级更新经强制重载仍保留真实 inode identity；首次显式 metadata 操作前创建的另一文件最终由后台扫描纳入查询 |
| 目录项名称兼容 | 长名 `O_CREATE` 和同 14 字节前缀 reopen 统一命中 canonical dirent；连续写入可读回；create hook 的 metadata 物理键只保存真实 14 字节名，重载和删除不 fail closed |
| selector 一致性 | fid/path 指向不同记录时返回冲突；陈旧 inode identity 不能选择路径复用后的新对象 |
| 文件生命周期身份 | 查询结果携带 `dev`、`inum`、`incarnation`、`size`；安全绑定和版本状态以 `dev + inum + incarnation` 为键，测试断言并输出真实 inode 与大小 |
| metadata 双 bank 可写入和重新加载 | 自定义元数据重新初始化后仍存在 |
| 内容摘要缓存 | 两次读取同一真实文件输出 `digest_cache=1`，改写后输出 `digest_cache_invalidated=1` |
| 内容证据进入 timeline | 输出 `digest_timeline=1`，表示可按工具 id 查询 digest Context 记录 |
| scan/index 差异 | 接近 128 条记录下输出 `bulk_index scan=118 index=6` |
| 查询执行与用户缓存边界 | 重复索引查询输出 `query_execution_isolated=1 kernel_cache_hit=0`，证明内核每次真实执行；`agentfinal_ucore` 再把完整有界 `agent_file_query_result` 写入用户 Context cache 并逐字段回读，字段更新后输出 `stale_query_result=0` |
| 属性删除 | 清空 status 后查询行为符合预期 |
| 文件删除同步 | 删除真实文件后关联元数据被清理 |
| 未命中 selector | `action_commit` 对不存在目标返回 `AGENT_STATUS_NOT_FOUND` |
| 依赖查询 | `label/namespace/run_id` selector 只返回所选运行的对象依赖，不混入同名 label 的其他运行 |
| 依赖注册 | `dependency_update` 可由用户态注册通用对象依赖，后续 `dependency_query` 可按同一 selector 读取 |
| 批量 action | 主对象与依赖对象在一次有预算的状态提交中各更新一次，纯状态变化不推进 dependency generation |
| 预取提示 | 默认 align 查询后得到当前运行内 analyze/report 等后续 label metadata 提示；目标去重、总数不超过 8，且查询内部产生 kernel-work 重调度 |
| 交接端点生命周期 | 目标在交接检查点退出并由 replacement 复用槽位；replacement 的 hint ring 与 mailbox 保持为空 |

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

`agentloop_ucore` 是任务五事件运行机制测试，重点检查 FIFO 事件队列、单 stable source 配额、内核 TIMER 与外部事件共存、watch/unwatch、有限 timeout 睡眠、wait cancel，以及 intrinsic/coalesced heartbeat 的设置、动态调整、停止、边界和旧 ABI。

### 4.1 测试流程

1. 父进程创建 orchestrator Agent 子进程。
2. 子进程注册 message watch。
3. 子进程连续投递多个事件，调用 `agent_wait()` 检查 FIFO 顺序。
4. 子进程检查投递和消费的事件包含 cause/span。
5. 子进程从同一个 stable source 连续投递 4 条未消费 `MESSAGE`，第 5 条必须返回 `AGENT_STATUS_NO_SPACE`；消费首条后立即补投一条并要求成功，再按原顺序消费余下事件，确认 source=4 边界和逐槽归还。实现中的 source 计数跨 directed/attributed 共用，但该步骤没有混合两类。
6. 另创建两个 directed source 各发送 4 条消息，把 directed IPC 填到 8，并确认继续 directed 投递被拒绝；第三个 source 触发 4 条 attributed `POLICY_DENIED`，使 external 合计达到 12。第四个 source 再触发 attributed 通知时，目标队列必须保持 12；随后不注册 TIMER watch，启动 heartbeat，确认一条 SYSTEM TIMER 可进入保留容量，并在跨越多个周期后仍只占一条。停止并消费全部事件后，再分别投递 self directed 和第四个 source attributed 通知，确认两类都能重新接纳。
7. 创建早期 slow watcher，用两个 directed source 和一个 attributed source 将其 external admission 填到 12；只在填满后创建 later watcher，再触发 `POLICY_DENIED` 广播。早期目标拒绝该条 external 通知时扫描仍继续，later watcher 必须收到事件。该用例不调用 `agent_file_meta_set()`，因此 metadata 提交不受通知背压的语义仍由实现审查支持。
8. 子进程删除 message watch，再投递相同事件，确认不会唤醒。
9. 子进程重新注册 watch，调用有限 timeout wait，确认线程进入睡眠并由 timeout 唤醒，且 `wait_loop_count` 增量很小。
10. 子进程确认没有 TIMER watch，调用独立 set syscall，仍必须由 heartbeat SYSTEM TIMER 唤醒；随后先设为极慢周期、再动态改为 1 tick，确认新频率及时生效。
11. 子进程在不消费的情况下跨越多个周期，确认队列中只有一条 heartbeat；调用独立 stop 后先消费该旧记录，再确认有限 wait timeout，证明 stop 不再生成新事件且不会假装删除旧事件。
12. 子进程验证 `AGENT_HEARTBEAT_MAX_TICKS` 可接受，`MAX+1`、`UINT64_MAX`、旧 ABI 负数和工具路径越界均返回 `AGENT_STATUS_BAD_PARAM` 且状态保持停止；再用 512 号旧 ABI 设置、消费和停止，最后两次调用 553 stop 验证幂等。
13. 子进程以带 `WAIT_CANCEL` 的 orchestrator 身份创建 sentinel 等待者，作为其直接 controller 调用 `agent_wait_cancel()`，确认等待者返回 `AGENT_STATUS_CANCELLED`，事件类型为 `AGENT_EVENT_CANCELLED`，并带有 reason、cause/span 和 Context 记录。

### 4.2 输出阅读方式

本测试输出围绕事件顺序、事件因果、single directed source、IPC 类、external 总量、SYSTEM 保留容量、慢 watcher 隔离、watch 删除、有限 timeout 睡眠、严格 heartbeat 和 wait cancel 展开。当前 runner 要求精确标记 `agentloop_ucore: heartbeat_intrinsic=1 dynamic=1 coalesced=1 stop=1 bounds=1 legacy=1`。2026-07-21 的历史 QEMU 摘录曾记录 4 条 heartbeat 同时填满保留槽和 `timer_unwatch=1`；这属于已被新语义取代的旧实现证据，不能证明当前版本。当前测试改为验证第 13 条 external 被拒绝、一条 coalesced heartbeat 越过 external 边界、drain 后重新接纳，以及 external 饱和 watcher 的广播隔离；attributed=8、同一 stable source 混合跨类和 metadata 提交返回值仍没有独立断言。历史摘录及其适用范围见 [test-record.md](test-record.md)。

### 4.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| FIFO 顺序 | 多事件按投递顺序被消费 |
| 事件因果信息 | 投递和消费事件均携带 cause/span |
| 单 stable source 上限 | 同一 stable source 的 4 条 directed event 可入队，第 5 条返回 `AGENT_STATUS_NO_SPACE`，旧事件不被覆盖；消费 1 条后立即补投成功，证明 source 槽逐次归还。混合跨类 source 计数由实现保证，尚无独立输出 |
| directed 与 external 压力 | 两个 stable source 各 4 条消息触及 directed=8，继续 directed 投递被拒绝；第三个 source 的 4 条 attributed 通知让 external 达到 12，第四个 source 的第 13 条 external 不入队且队列保持 12 |
| 内核 origin 保留、coalesce 与重接纳 | external=12 后，一条 intrinsic SYSTEM heartbeat 越过 external admission，并在多个周期内保持单条 pending；全部消费后 self directed 与新的 attributed 通知均可再次入队。该重接纳证明相关 admission 可恢复，但没有单独把 attributed 计数从 8 的边界清空后再验证 |
| attributed 类边界 | 当前只填入 4 条 attributed 通知，没有单独触及 `ATTRIBUTED_LIMIT=8`，属于明确的剩余测试缺口 |
| 慢 watcher 隔离 | 较早分配的 watcher 先耗尽 external=12 admission；同一 `POLICY_DENIED` attributed 广播在该目标被拒绝后仍继续送达后续 watcher；metadata 提交路径未在该用例中动态覆盖 |
| watch 删除 | `agent_unwatch()` 后相同事件不再匹配 |
| timeout 睡眠 | 有限 timeout wait 返回 `AGENT_STATUS_TIMEOUT`，并用 `wait_loop_count` 检查是否避免反复轮询 |
| wait cancel | 受权 Agent 可取消目标 Agent 的等待；取消令牌与普通事件队列在实现上独立，但当前动态用例没有组合“队列已满 + cancel” |
| heartbeat 内生唤醒与调频 | 没有 TIMER watch 时仍收到 SYSTEM heartbeat；极慢周期动态改为 1 tick 后在短 timeout 内唤醒 |
| heartbeat coalesce 与停止 | 多周期不消费时只有一条 pending；stop 后先 drain 旧记录，再确认没有后续唤醒；stop 可重复调用 |
| heartbeat 边界与兼容 | 最大值成功，`MAX+1`、`UINT64_MAX`、旧 ABI 负数和工具路径越界均拒绝且不改状态；旧 512 ABI 正值/0 仍可用 |

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

`agentbench_ucore` 是机制负载与诊断回归。它输出 tick、操作数和候选记录等 telemetry，用于发现路径退化和解释 Guest 行为；这些未绑定的 tick 只属于当次启动，不能单独形成跨版本性能结论。发布时唯一可宣称的性能证据是 provenance-bound 的强制遍历/冷索引/热索引文件查询 benchmark。

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
| `ticks` | 当次 Guest 的诊断 tick；最小按 1 处理，避免除 0，不是发布性能证据 |
| `ops_per_tick` | 由当次诊断 tick 派生的路径观测值 |
| `speedup_x100` | 当次程序内部相对基线的诊断比值，不可跨运行或跨版本认证 |

### 8.3 输出阅读方式

本测试会输出多组 `ops/ticks/ops_per_tick/speedup_x100`，并额外输出 scan/index 候选记录数、查询计划、digest cache、prefetch 记录数、timeline 记录数和 busy-poll/wait 计时观测。它们用于定位机制与回归，不进入 runner measurement ABI，也不生成认证的 tick/speedup 图。完整样例输出见 [test-record.md](test-record.md)。

### 8.4 诊断解释

| 对比 | 设计含义 |
| --- | --- |
| `batch_agent_run` vs `scalar_agent_run` | 批量 syscall 减少陷入内核次数 |
| `direct_context` vs syscall 查询 | 用户态镜像适合高频读最新状态 |
| `context_snapshot` vs `context_query` | 批量历史查询减少多次 syscall 和多次遍历 |
| `file_index_query` vs `file_scan_query` | 文件元数据索引减少候选记录检查；`file_query_records` 输出候选记录数差异，`file_query_plan` 输出索引选择原因，`file_query_execution` 明确热索引仍为 `kernel_cache_hit=0` |
| `file_digest_read` | 受权 Agent 可读取真实文件短预览和内容指纹，诊断输出按处理字节数呈现，`file_digest_cache` 呈现重复读取时的缓存命中 |
| `file_prefetch_snapshot` | 文件查询之后可直接读取内核给出的后续 metadata 提示，避免下一轮重新从宽条件查询开始 |
| `provenance_snapshot` | 页面可直接获取因果边，减少从 timeline 文本和短记录中二次推断触发关系 |
| `timeline_wait_ready` | 当前已有记录时 wait 不睡眠，直接返回可读数量；真正睡眠唤醒由 `agentfinal_ucore` 断言 |
| `timeline_read_ready` | 当前已有记录时 read 不睡眠，直接复制可见 timeline 记录 |
| `timeout_heartbeat` | Agent Loop 的超时和心跳字段有直接断言，不只依赖场景日志 |
| `busy_poll_query` / `event_wait_wake` | Agent Loop 不只是功能示例，也能输出轮询路径和等待唤醒路径的计时观测 |

tick 数值随环境和启动状态波动，只能作为当次诊断 telemetry。跨版本性能结论必须读取独立文件查询 benchmark 的原始日志、source receipt、CSV 和 commit 绑定，不能用这些 tick 做 min/avg/max 或速度比外推。

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

`agentsecurity_ucore` 是权限限制负向测试。除既有 role、route、wait cancel 和系统事件边界外，本次增加：用户态不能通过 `context_push` 自报 span/cause；跨 Agent provenance 使用可信 source control；低权限遥测和借来的 span 不能进入或淘汰 protected/high 审计效果分区；legacy mail 的按需资源分配、controller lineage 和跨 scope 拒绝也成为强制日志契约。

### 10.1 测试流程

1. 内核加载的可信初始进程检查自身保持普通进程身份且业务 capability 为零；随后成功创建 orchestrator，证明内核私有 bootstrap 授权生效。
2. 可信初始进程属于无 Agent controller 的系统 lifecycle；其 legacy mail read/send 均拒绝且不分配 sidecar。随后验证事件、IPC、metadata、scope audit、timeline 和两个 metadata bank 受能力与对象边界保护。
3. 可信初始进程创建两个独立普通 EXEC account：跨账户裸 PID mail 必须拒绝且目标队列保持为空；其中一个普通子进程再次 `fork()`，同一 immutable account 内的 legacy mail 保持兼容。随后普通子进程检查两种 Agent 创建接口和全部合法角色都返回 `AGENT_STATUS_DENIED`。
4. 该普通子进程再执行 `exec()`，重复验证创建授权仍为零，证明授权不会经普通派生链传播。
5. 可信初始进程创建 controller A；A 创建并等待一个 sentinel 就绪，显式建立 A 到 sentinel 的消息路由后退出，使 sentinel 成为孤儿。随后创建 controller B，验证新 control id 即使将来发生 PID/PCB 槽复用，也不能取消该 sentinel 或继承 A 的旧消息路由；该用例本身不直接断言实际复用了同一 PID/PCB。
6. 可信初始进程创建正常 orchestrator；orchestrator 通过 `agent_info()` 检查真实 role 和 capability mask，并通过成功委派各角色验证创建权。
7. bootstrap 同时保持两个独立 workflow root 为 ACTIVE；workflow B 的 PUBLIC 子进程向 workflow A 的 PUBLIC 端点发送 legacy mail 必须失败，A 的队列保持为空，且测试核对两个动态 scope 确实不同。正常 orchestrator 普通 `fork()` 后，子进程验证 Agent 身份、role、capability 和 Context 均已清零，且没有创建授权。两个同 account/lifecycle/controller lineage 的 PUBLIC 子进程完成 legacy mail 正向收发；普通同账户发送端另验证空读零分配、首次发送两页、无效用户地址不消费队首、16 槽满队列、排空保留、退出后新端点无残留、旧 PID 拒绝和新 PID 单调前进。该发送端最后带着未读消息执行 PUBLIC `exec`，新映像必须看到空队列并可通过新 endpoint 正常自收发。
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

本测试输出围绕可信根授权、`fork/exec` 不继承创建权、低权限角色不可继续委派、stable control id IPC 路由、grant/revoke、target consent、退出回收、`.agentmeta` 保护、真实 role/capability、初始化前索引查询安全、legacy 参数校验、sentinel 角色与系统事件伪造失败、recovery 定向动作和多 run 工件更新展开。runner 逐行强制检查 `mail_lazy_empty`、`mail_queue_full`、`mail_read_failure_atomic`、`mail_endpoint_reuse_isolated`、`mail_exec_endpoint_rotated`、`mail_ordinary_domain_isolation`、`mail_active_workflow_isolation`、`mail_scoped_public` 和各跨域拒绝 marker；`physical-resource` 还要求真实账户观测得到 `legacy_mail_accounting=1 alloc_delta=2 exit_delta=0`。这些新 marker 本轮只完成定向编译和静态契约测试，尚未生成 QEMU 证据。2026-07-21 的既有 QEMU 运行已出现 `route_source_enforced=1`、`route_target_isolated=1`、`ipc_route_authorization=1`、`message_route_lifecycle=1`、`target_route_consent=1` 和 `route_slot_reclaimed=1`；旧 `message_send_preserved=1` 不再作为可信路由证据。完整运行摘录见 [test-record.md](test-record.md)。

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
| 慢订阅者广播隔离 | external admission 已饱和的 watcher 在前、later watcher 在后；`broadcast_slow_watcher_isolated=1` 确认前者拒绝时后者仍收到同一 attributed 广播 |
| Agent 不能用消息接口伪造系统事件 | sentinel 直接投递 `LLM_DONE` 返回 `AGENT_STATUS_DENIED` 且不入队；非法类型返回 `AGENT_STATUS_BAD_PARAM`；合法 `MESSAGE` 仍需先命中显式路由 |
| Whole-scope 审计读取、过滤、调度配置和依赖注册权限 | 普通进程返回 `-1`，sentinel 缺相应能力时返回拒绝 |
| 普通进程不能直接修改文件元数据 | `agent_file_meta_init()`、`agent_file_meta_set()` 返回 `-1` |
| 普通进程不能直接访问 metadata 双 bank | 对 `.agentmeta` 和 `.agentmeta1` 的 `open`、`open(O_CREATE)`、`unlink` 均返回 `-1` |
| legacy PUBLIC mail 保持兼容但不跨域 | 同一普通 EXEC account 的后代正常收发；两个独立普通 account 互发拒绝；同 account/ACTIVE lifecycle/非零 OPEN controller lineage 的两个降权 PUBLIC 正常收发；Agent、bootstrap 缺失 controller、两个同时 ACTIVE workflow、不同 lifecycle/scope 均拒绝且不分配 sidecar |
| legacy mail 资源按需、失败原子且可回收 | 空读保持 0 页；首次合法发送发布 2 页；无效用户地址不消费队首；16 槽满队列不扩容；旧 PID 拒绝且新端点队列为空；PUBLIC exec 清除旧队列并发布可用的新 generation；physical-resource 从 workflow account 实测分配增量 2 页、子进程退出后回到 fork 前用量 |
| Agent 创建权有明确可信根 | 仅内核加载的初始进程获得 bootstrap 角色授权；普通 `fork` 子进程不继承 |
| 普通派生链不可铸造 Agent | 普通子进程在 `exec` 前后调用两种创建接口均被拒绝 |
| 角色委派不能向低权限 Agent 扩散 | orchestrator 可显式创建角色；sentinel、investigator 和 recovery 调用所有创建形式均被拒绝 |
| Agent 进程槽复用不残留权限 | orchestrator 回收后派生普通子进程，身份、capability、Context 和创建权均为普通进程状态 |
| bootstrap 授权按映像生命周期撤销 | 可信初始进程执行普通 `exec` 后，所有 Agent 创建请求均被拒绝 |
| 初始化前索引查询安全 | 未调用 `agent_file_meta_init()` 前，索引查询返回 0 条命中且不阻塞 |
| legacy 工具名和工具 ID 不一致会失败 | `agent_call()` 传输返回 0，`response.status` 为 `AGENT_STATUS_BAD_REQUEST`，结果文本为 `tool_mismatch` |
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
3. 普通 `fork()` 子进程失去 Agent 身份、文件能力和动态 scope，父进程已打开的 workflow inode fd 因而直接撤销；同 scope inode fd 的逐操作重鉴权是内核机制，不把该跨 scope 撤销用例误记为动态覆盖。
4. orchestrator 只能向 mkfs 生成的 immutable、domain-safe worker 映像委派精确能力。空能力、未知位、超过映像 profile 上限、错误映像和跨映像执行都被拒绝或降权；普通进程直接执行该映像不会获得 workflow 权限。
5. 写能力不足时，带创建/截断意图的 `open()` 在改变文件系统之前失败，验证失败事务没有留下半创建或半截断状态。
6. 普通进程不能访问两个 metadata bank；元数据查询只定位工作流命名空间中的对象，不会误绑定同名公共文件。

### 12.2 通过标记与覆盖结论

关注辅助程序输出的 `failed_open_atomic=1`、`cross_image_attenuated=1`、`wrong_first_exec_attenuated=1`、`sealed_exec_no_elevation=1`，以及主程序的 `cross_scope_fd_revoked=1`、`worker_pipe_delegation=1`、`protected_paths=1` 和 `parent passed`。测试同时覆盖打开时授权、跨 scope 描述符撤销、worker pipe 单跳委派、映像绑定委派和命名空间隔离。

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

`fsquota_ucore` 的 PUBLIC 子进程先循环 640 次创建文件、在描述符仍打开时删除目录项、继续写入，再关闭最后引用。循环次数严格超过旧全局版本表的 512 槽容量，既覆盖“unlink 不是最终生命期”的语义，也迫使每个 incarnation 走最终 inode/sidecar 回收。随后测试继续施加块和 inode 压力，并由 workflow orchestrator 创建工件、提交编辑版本、连续读取两次内容摘要以确认 digest cache 命中。关键标记为 `public_version_churn=1 cycles=640`、`workflow_version_reserve=1` 和 `content_version_reserve=1`；它们与既有 `workflow_reserve=1`、`kernel_metadata_reserve=1` 一起证明 PUBLIC 域不能耗尽工作流的存储或版本状态。这里的 QEMU 用例证明分配水位和回收行为；四个 scope 的数值容量契约由共享策略单测、mkfs 负例、版本化 superblock 校验和 admission 检查共同证明，不把单个 `scope_storage_isolation` 标记夸大为全盘块压力证明。

`fspquota_ucore` 专门区分“短命进程资源域”和“持久存储主体”。第一次启动先持久化 phase 文件，再创建 1 block PUBLIC 文件、删除目录项但保持描述符打开；输出 `crash_orphan_ready=1` 后，runner 以显式 checkpoint mode 发送一次 `SIGTERM` 终止 QEMU，若需升级 `SIGKILL` 则该阶段失败。第二次挂载必须在计费前回收该不可达 inode/block，否则后续精确上限断言无法完成。为避免“只忽略计费但不释放物理状态”的假通过，runner 还保存 mkfs 基线并在 seed 后直接解析 raw image，要求 bitmap 分配与非 FREE qmap owner 都只比基线增加 4 个数据块，dinode 只增加 7 个。镜像还带入一个 SYSTEM 赞助、可变 PUBLIC 的 13 数据块文件；PUBLIC 子进程首次覆盖它时，内核必须把 inode、12 个直接块、间接索引块和第 13 个数据块整体接管，共计 14 block/1 inode。子进程再创建其余对象，恰好占满 18 block/8 inode 上限后完整退出；init 只在回收该域后输出 `sponsored_object_charged=1 blocks=14` 和 `durable_fixture=1 blocks=18 inodes=8 owner_exited=1`，runner 对 seed checkpoint 使用相同的单次 `SIGTERM` 合约并保留镜像。第三次以同一 kernel/镜像启动时，新 PUBLIC 域先读取旧内容，再验证额外 block 与 inode 都被拒绝；通过 `O_TRUNC` 释放一个 block、通过 `unlink` 释放一个 inode并分别复用，之后再次增长仍被拒绝。该域退出后，另一个新域仍被旧文件计费；最终清理持久 fixture 后才能重新分配。顺序标记为 `crash_orphan_ready=1`、`reboot_charge_persisted=1`、`deletion_reuse=1`、`relaunch_charge_persisted=1 launches=2`、`cleanup_reuse=1` 和 `parent passed`。AgentOS 与 baseline 使用相同用户程序和三阶段 runner，因此这个回归不依赖 Agent 角色或某个 PID 特判。

该测试对应的磁盘契约将 PUBLIC principal 写入 superblock，并提升策略/owner 格式版本。挂载时从 qmap 和 dinode 分别重建 block/inode 用量；缺少该字段的旧镜像会因版本或布局不匹配被明确拒绝，而不是以空账本继续运行。

profile v5 的 `make full-verify` 已串联该入口；也可用上述命令独立复现。是否实际通过仍以 C 对应 release bundle 中的该步原始日志为准。

## 15. 进程回收与配额复测

入口为 `make proc-reap-test` 或 `bash scripts/run-proc-reap-tests.sh`。脚本在增强目标运行普通生命周期测试与 adversarial Agent 测试，并在 `baseline_ucore/` 运行普通生命周期测试。

`procreap_ucore` 覆盖子进程先退出、父进程先退出、孤儿资源回收、阻塞 syscall 撤销、等待队列取消、无人 `wait()` 的父进程隔离、资源域活进程上限、谱系绕过拒绝、配额归还和同级资源域隔离。`procreap_agent_ucore` 进一步覆盖高压退出调度、普通域压力隔离、Agent 系统保留槽和恶意 Agent 行为。最终标记分别为两个程序的 `parent passed` 和脚本的 `[proc-reap] both targets passed`。

`make full-verify` 会串联本项，但如果此前的宿主机检查失败，流程会在到达该阶段前中止。

## 16. 内核栈预算检查

根目录与 `baseline_ucore/` 都执行调用图栈深检查，但物理映射策略不同。AgentOS 为全部线程保留 16 KiB 栈虚拟槽和 4 KiB guard/canary，只在线程 admission 时取得物理页，并在 scheduler 已切回 idle stack 后释放；32 MiB 是虚拟容量，8 MiB 是受信/保留线程物理池。baseline 当前仍使用固定物理栈。`scripts/check-kernel-stack-usage.py` 读取 callgraph 与栈帧数据，超出配置栈大小、未建模递归/间接调用或大帧都会阻止构建。

独立复查命令为：

```bash
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
make -C baseline_ucore kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

成功时输出 `kernel stack budget`、`kernel stack user path`、`kernel stack interrupt path` 和独立的 boot path 预算；失败时返回非零状态。`make ci-check` 还核对 `boot_stack..boot_stack_top` 的 64 KiB 链接跨度，并通过 probe 限制 32 MiB 虚拟容量和 8 MiB 物理保留池，防止把“虚拟槽稳定”误写成“全部物理页常驻”。当前单 hart 使用本地 TLB fence，未来 SMP 还需远端 shootdown 测试。

## 17. 运行方式和复现建议

推荐用脚本运行 AgentOS 专项测试：

```bash
bash scripts/run-agent-tests.sh
```

该脚本包含 `agenttrust_ucore`、`agentvfs_ucore`、`usersafety_ucore`、`agenttoolabi_ucore` 和 `blocking_semantics_ucore`，但不把 ENOSPC、进程回收或 workflow teardown race 计入普通 Agent case。每次全套或定向 `agentfinal_ucore` 前，脚本先以独立构建运行 Context-sync/WAIT_ATOMIC prelude；其临时 timing 与普通 18-case timing 分离。`make full-verify` 先要求 18-case 时长状态已经 calibrated；随后 profile v5 执行结构/`ci-check` 与 Host/Reader，运行该 prelude 和 18-case Agent 套件并用 canonical LF Guest 日志提取测量，再运行双目标 QEMU、proc/syscall/file/thread/physical、metadata/观测重启、VirtIO、独立 teardown race、ENOSPC 和 filesystem allocator fault 专项。实际关系见 [verification.md](verification.md)。

Reader E2E 的历史阻塞已经修复。根因不是内核：旧 `plain_ucore_action_runner.py` 在构建阶段扫描 `panic` 子串，把 `build/riscv64/ch6b_panic` 当成 Guest panic。当前执行器明确区分 clean/build/guest，clean 与 build 只看进程退出码；只有 QEMU guest 启动后才对去 ANSI 的完整日志行匹配 panic、trap、`check failed` 和 orchestrator failure。`test_plain_ucore_action_runner.py` 同时覆盖“构建文件名含 panic 仍通过”和“规范 Guest `[PANIC ...]` 必须失败”。`125.0s` Reader 定向和提交 `75d0dfd` 的 clean `full-verify` 都只是历史 checkpoint；当前候选的执行结果统一记录在 [test-record.md](test-record.md)，发布结果必须由 C 对应 bundle 中的 Reader manifest 和原始日志绑定。

科研平台 receipt 的 Host 合同使用 `exact-field-v1`。`rp_evidence_measure_file_field()` 以 128 B 分块流式读取整个证据文件，跨块匹配且只接受唯一、完整的目标 `key=value`；长无关字段、跨块长 key 和跨块目标字段都必须可解析。空 key、空 value、CR、NUL、重复目标、前缀或后缀伪匹配均 fail closed；bytes、hash 与 line count 覆盖完整文件而不是截断缓冲区。`scripts/test-rp-evidence-file-field.py` 以 ASan/UBSan Host probe 接入 `make ci-check`，但该 E1 合同不能代替当前 Reader E2E、Guest 或 `full-verify`。

参考产品还受 target-specific registry 约束：每个 destination 只有一个 source owner，文件必须形成完整 `demo_reference/demo_expected/reference_ready` envelope，记录以 `(destination,anchor)` 唯一；缺失、未知、重复、跨 owner 预发布或冒名均 fail closed。seeded program observation 同时绑定 seeded profile、QEMU 日志和 `rp_orch_timing` 的 orchestrator/launcher/program 顺序、数量、字节、哈希及名称摘要。AgentOS 主流程固定 11 个唯一、完整、有序的未验证 telemetry stage；Guest 出现任何 Mainflow `runtime_verified` 回执都会失败。Host 从安全状态清单独立复验每个规范来源中的唯一 claim、预期状态和对应 telemetry，并计算完整 bytes/hash；动态 writer、字符数组或函数指针不能借静态 producer 扫描获得验证身份。

双目标比较不会把 Host 回执伪装成 Guest 状态。每侧 complete-state ZIP 只含 `extract-summary.json` 和其中精确列出的纯 Guest `rp_*` 普通文件，`rp_host_run_result` 必须缺席。两份 Host run receipt 以独立 raw sidecar 保存，使用 `sha256-inventory-v1` 绑定排序文件清单、数量、长度和全部内容；比较器显式重算摘要，并用 seeded summary 与两份 canonical Guest 日志中的唯一 action marker 绑定 action 数。Reader 同样先验证原始 Guest receipt，再合成 Host LLM relay 的事务化差异 overlay，relay 不可修改 Guest 快照。Host overlay 与 action runner 共用逐组件路径校验，私有 `host-state` 拒绝 symlink、Windows junction 和链接祖先；旧 overlay 只删除已验证的平面普通文件，不递归跟随目录入口。离线验签安全解包后以 `min_common_files=240` 重放 `compare_state()`，要求重放摘要逐字段一致，并核对 Mainflow、program ledger 和 backend 原始字节。普通双目标运行只保留状态目录及 sidecar；complete-state ZIP 只由最终采集的 evidence mode 生成。

runtime tick 不接受“部分可用”。当前生产链没有可信的独立 runtime producer，`runner-sweep` 只输出 `unavailable/plain_runtime_cases_zero` 和零数据行；不可达的 measured collector、相关身份常量和两张推导图已作为死测试 ABI 删除。未来恢复测量必须使用新的、非 reference 的 runtime 源，并逐字段绑定 case、ticks、reason、日志与 commit/run。

不要在同一工作树中并行启动多个 QEMU 测试，因为 `nfs/fs-copy.img` 会被多个进程同时访问，可能造成镜像锁冲突。

## 18. `agentscope_ucore`

`agentscope_ucore` 是本次可信 workflow scope 的机制回归。它不靠固定 PID、文件名白名单或角色特判，而是同时建立两个由 syscall 541 创建的动态 scope，并故意在两域使用相同文件名、fid、namespace、run 和 action selector。

测试流程：

1. 可信 bootstrap factory 对命令/回复 pipe 的指定端点调用 syscall 542，再分别创建 scope A/B；未委派 pipe 和跨 scope inode fd 不跨边界，一次性票据在主体创建尝试后消失。
2. 两个根 Agent 的 `filesystem_domain` 都不小于 3 且彼此不同；数值 2 保留为稳定 PUBLIC 存储 principal。同 scope `agent_create_role()` 子 Agent 继承 scope，但不会自动继承 pipe。测试先证明 bootstrap 控制端点不能进入 Artifact，再让两个线程分别签发不同端点并交错创建子主体，要求每个子主体只看到创建线程自己的票据；由非主线程发起 fork 的子主体还要成功创建并回收一个新线程，验证线程栈 VA 与 tid 解耦且不会覆盖复制地址空间中的既有栈；随后验证单字节数据交付、单次消费、失败创建撤销、关闭后同槽 fd 复用不继承旧票据。scope 内 orchestrator 仍不能再调用 workflow factory。`exec` 清票由统一映像安装/凭据重置路径的实现审计确认，本轮未设置单独动态断言。
3. A/B 创建同名文件和相同 metadata/fid，各自只能查询、打开和读取自己的对象。
4. B 创建不带 `PERSIST` 的内存态 metadata，A 强制重载自己的 bank 记录后，B 的记录仍可查询，证明 `file_meta_init` 不能跨 scope 清表。
5. target consent、route grant 和 MESSAGE 投递跨 scope 均拒绝；同 scope stable route 仍可协作。
6. A 在同一 scope 内同时启动三个 Orchestrator；目标 bank 经 invalidate、分块 payload 写入、header publish 和回读验证后，owner 只在安全边界协作让出 CPU，使其他 writer 有机会到达事务门。三者各自持续创建并提交 `PERSIST` metadata，结束后强制从双 bank 重载，再按 run 查询必须完整得到三组共 12 条记录。`metadata_txn_contentions` 只报告该次单核时序实际发生的等待次数，允许为 0，不作为正确性门槛；并发提交、完整重载和查询结果才是断言。进程 waiter 领取单调 ticket，最外层释放只 wake-one；scheduler 的有界保留轮次不会重复唤醒已经 runnable 的 serving ticket。
7. B 创建不带 `PERSIST` 的真实 volatile 对象，再由低权限 Artifact 连续执行 32 次单字节写入；前后 scope-local request/commit 计数必须完全不变，强制重载后 volatile 对象仍保持本域内存状态，证明它不会被错误写回或被 reload 清除。
8. 配额阶段在其他测试文件创建前运行。A/B 各创建 97 个普通 workflow 文件：前 96 个进入 AUTOSCAN 物化视图，第 97 个仍由 STORAGE 账户接纳但保持未索引；显式 syscall 携带 `AGENT_FILE_META_F_AUTOSCAN` 也不能越过 96 条边界。随后每域 16 个非 AUTOSCAN 显式 metadata 均成功，第 17 个显式请求返回 `NO_SPACE`。catalog 已满后再创建的普通 VFS 文件仍成功、不可由 peer scope 打开且不出现在 metadata 查询中。降权 PUBLIC 子进程另行创建并删除 70 个对象，清理后 scope 能再次创建文件。ACTIVE/CLOSING/RETIRING 合计最多 4 个 workflow，RETIRING 在 catalog 回收完成前继续占用其准入槽。workflow STORAGE policy 的 inode 硬下限为 320，当前镜像保证约 342；catalog 每 scope 112 由 AUTOSCAN 96 与显式保留 16 组成，两套容量不再互相钳制。sidecar 更新必须统一经过 `agent_file_state_set_index()`，目录 write/sync/truncate/delete 事件统一经过 `agent_fs_apply_inode_event()`；create 只有在 VFS 发布成功后才协调 metadata。
9. 配额对象清理后，低权限 Artifact 才对一个已绑定 `PERSIST` 对象连续微写至少 128 次；第 16 次后通过 guest pipe 发出存活屏障，B 在写入仍进行时完成 32 次本域 metadata 查询并主动让出 CPU，guest `get_mtime()` 要求总延迟不超过 5 秒。测试读取 scope-local telemetry，要求持久变化全部进入记账、合并请求加成功批次等于总请求、完整 bank checkpoint 不超过请求数的八分之一。具体写入/批次数受调度时序影响，不把某次观测值固化为契约；这里不再把未绑定满表写入或全局 scan 次数当成通过证据。
10. A 在写回完成后要求 `dirty_generation == durable_generation` 且 pending 清零，再强制从双 bank 重载并比较 size 和文件代数，证明异步合并没有丢失最终状态。
11. 相同 action selector 在两个 scope 各自拥有幂等历史；audit/event/query 只返回本 scope，公开 PID/span 不扩大范围。
12. A 的编辑 lease/version 不能由 B 查询、提交或终止。
13. 同 scope 多进程共享 workflow 存储计费并受同一 scope limit；从该 workflow 明确降级出的普通子进程改用安装级 PUBLIC principal，不能继续借用短命进程资源域作为磁盘身份。其他 admitted/future scope 的数值保证另由共享 policy 单测、mkfs 初始 `S+4G` 契约、挂载 `4G` 复核和原子 admission 检查覆盖。
14. 同时占用4个 workflow admission 后第5个创建失败；释放一个后可创建替代 scope。最后成员退出触发 retirement，回收 metadata/action/lease/audit/prefetch/IPC 等表后槽可复用。
15. `agentvfs_ucore` 对 `agent_worker_create()` 独立验证无票据拒绝、一次显式授权成功和消费后再次拒绝，防止 worker/pending-exec 分支偏离统一安全主体机制。
16. A 创建低权限 Sentinel，填满 Context 后持续执行 span、audit-only timeline 和 provenance 查询。每轮检查返回记录保持 sequence/timeline 顺序，且全部满足当前 scope 与可信 span 的可见性边界；查询后通过 `kernel_work_last_preemptions()` 确认可扩展扫描实际进入既有调度预算。
17. Sentinel 保持压力期间，父进程经 scope B 的命令/回复通道驱动本域查询并等待完整回复，再停止并回收 scope A 压力子进程。该父侧端到端流程同时证明另一 workflow 能持续前进、控制通道没有跨 scope 混淆，且压力结束后资源可正常回收。
18. 新 workflow 根检查低权限 Sentinel 和后创建 Orchestrator 的关闭请求均被拒绝，再关闭自身 scope；父侧要求根以 `AGENT_STATUS_CANCELLED` 结束。可信 factory 另按 scope id 关闭一个由低权限 Sentinel 持有 lifetime pipe 的 workflow，并要求成员全部退出、pipe 到达 EOF。非规范 `(1ULL << 32) | scope` 必须返回 `AGENT_STATUS_BAD_PARAM`。
19. 根 controller 自然退出时，内核按不可变 lifecycle key 撤销谱系。测试创建已降权为 PUBLIC 的 child 与 grandchild；两者分别验证 `is_agent==0`、filesystem domain/capability 清零，并独立发送 `P`/`G` 就绪标记。随后二者仍必须与低权限阻塞成员一起退出，证明撤销不依赖当前 Agent/VFS 凭据。
20. 自动 controller-exit 撤销连续执行 9 轮，跨越 8 槽 lifecycle ledger；随后必须成功创建 replacement workflow。槽允许回收，但新 admission 必须取得更高 generation，旧 `(id,generation)` 不能覆盖新 workflow。

预期回归标记包括：

```text
agentscope_ucore: cross_scope_isolation=1
agentscope_ucore: ipc_scope_isolation=1
agentscope_ucore: same_scope_collaboration=1
agentscope_ucore: pipe_redelegation_isolation=1
agentscope_ucore: metadata_transactions=1
agentscope_ucore: scope_storage_isolation=1 catalog_limit=112 autoscan_limit=96 explicit_reserve=16 workflow_created=97 peer_created=97 public_created=70 overflow_unindexed=1 autoscan_flag_no_space=1 explicit_no_space=1 reusable=1
agentscope_ucore: scope_reload_isolation=1
agentscope_ucore: metadata_write_coalescing=1 writes=<at-least-128> commits=<bounded>
agentscope_ucore: metadata_cross_scope_progress=1 queries=32 latency_ms=<at-most-5000>
agentscope_ucore: metadata_final_consistency=1
agentscope_ucore: metadata_volatile_reload_isolation=1 writes=32
agentscope_ucore: observe_query_bounded=1 ...
agentscope_ucore: observe_index_ordered=1
agentscope_ucore: observe_cross_scope_progress=1 ...
agentscope_ucore: action_scope_isolation=1
agentscope_ucore: audit_event_scope_isolation=1
agentscope_ucore: lease_scope_isolation=1
agentscope_ucore: scope_capacity_reservation=1
agentscope_ucore: transactional_fd_delegation=1
agentscope_ucore: scope_close_authority=1
agentscope_ucore: scope_controller_exit_revoke=1 public_lineage=1
agentscope_ucore: scope_forced_cleanup=1
agentscope_ucore: scope_replacement_admitted=1
agentscope_ucore: lifecycle_reclamation=1
agentscope_ucore: parent passed
```

338.4s、371.5s、128.1s、127.9s 和 126.1s 都是 generation-safe lifecycle 与 PUBLIC lineage 用例之前的历史记录。权威 lifecycle ledger 现在固定 8 槽并使用 `(id,generation)`；`vfs_scope_refs[NPROC]` 只保存清理引用。后续历史专项约 `93.7s`，曾实际取得上述 `public_lineage=1` 和 `parent passed`；任何后续代码的通过状态只从其 release bundle 读取。

## 19. syscall 内核工作预算复测

入口为 `make syscall-fairness-test` 或 `bash scripts/run-syscall-fairness-tests.sh`。脚本为根目录 AgentOS-uCore 和 `baseline_ucore/` 分别构建独立临时镜像，运行同一个 `syscallfair_ucore`，不复用可被其他测试修改的 `fs-copy.img`。测试的同步与因果关系全部由 Guest 内的 pipe、进程和线程建立；宿主运行器把 QEMU stdin 设为 `/dev/null`，只采集原始输出，不注入字符或参与唤醒。

控制台阶段先让同级进程阻塞在 Guest pipe gate。writer 释放 gate 后，以一次 `write(stdout, ..., 64 KiB)` 输出同时含 BEGIN/END 的数据；peer 读到 gate 后主动 `sched_yield()`，再输出进展标记。脚本要求：

```text
SYSCALLFAIR_CONSOLE_BEGIN < SYSCALLFAIR_CONSOLE_PEER < SYSCALLFAIR_CONSOLE_END
```

因为 BEGIN 和 END 位于同一个 64 KiB write buffer，这个顺序证明控制台 syscall 返回前发生过安全点调度。

inode 阶段创建共享地址空间的 observer 线程。observer 反复从独立 fd 读取仍为空的目标文件，writer 首次返回必须满足 `0 < n < 64 KiB`，并立即通过只读的 `kernel_work_last_preemptions()` 要求前一个 syscall 至少经历一次内核态重调度；随后等待 observer 读到已提交数据、输出 `SYSCALLFAIR_INODE_SHORT` 并循环完成剩余数据。脚本要求 `INODE_BEGIN < INODE_PEER < INODE_SHORT < INODE_END`。重调度计数提供 write 内部的因果证据，observer 独立证明 peer 取得进展；二者动态验证真实 inode 数据和共享 file offset 的已提交前缀，而不把 write 返回后的用户态 timer 抢占误算成 syscall 内调度。AgentOS 专属 size 发布 sidecar、query overlay 与持久化 sequence 由源码审查覆盖，本双目标用例不把普通 inode read 夸大为 metadata query 证据。

截断阶段先填充一个 64 KiB 文件，再在同一进程创建 observer 线程。主线程输出 TRUNC_BEGIN 后调用 `open(path, O_WRONLY | O_TRUNC)`，并用 last-syscall 重调度计数要求该 open 在内核态跨过调度边界；observer 连续两轮打开文件并读到 EOF，两轮之间主动让出，随后输出 TRUNC_PEER。脚本要求 `TRUNC_BEGIN < TRUNC_PEER < TRUNC_END`。计数提供 open 内部的因果证据，observer 独立证明原子 detach 后的 EOF 对其他线程可见；二者不依赖 open 返回后到共享标志写入前是否发生用户态 timer 抢占。

三个阶段的每个标记都必须只出现一次。最外层父进程只在 fairness worker 被 `waitpid()` 完整回收后输出 `syscallfair_ucore: parent passed`；宿主 runner 随后要求 QEMU 在 5 秒内正常关机，日志不得包含 panic、非法地址或未知 syscall。这一部分验证退出完整性，不单独宣称证明退出清理内部的公平边界。两个目标都满足后输出 `[syscall-fairness] both targets passed`。统一 resource account、teardown 和 lazy stack 后的双目标专项有历史通过记录，覆盖控制台、普通 inode I/O 和截断回收；发布复跑由 C 对应 bundle 绑定。源码检查覆盖 pipe、exec/fork 分页、VM snapshot 屏障和 Agent batch 安全点；它不等于穷尽任意 syscall 路径。目录 scanner 仍采用固定每轮上限，metadata raw I/O 由独立 COW/I/O budget 专项覆盖。

## 20. 全局文件对象表资源配额复测

入口为 `make file-resource-test` 或 `bash scripts/run-file-resource-tests.sh`。两个目标运行同一个行为契约，但实现不同：AgentOS 的唯一 file object 计入 generation-safe EXEC account，pipe 两端使用向量 reservation；baseline 保留旧 per-domain 计数。tiny 配置让 Guest 精确触发隐藏引用、ordinary 水位与 reserved 进展。重构后的双目标专项有历史通过记录；发布复跑由 C 对应 bundle 绑定。

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

这些场景验证的计费单位是唯一 filepool 槽：fork 和 syscall pin 增加引用但不重复扣账，最终引用关闭才退款。AgentOS 还必须验证迟到退款携带原 resource account generation，不能记到复用后的新账户；同一 Guest 契约不等于两目标共享实现。

## 21. 线程资源域与域级公平调度复测

入口为 `make thread-resource-test` 或 `bash scripts/run-thread-resource-tests.sh`。脚本只构建 AgentOS。THREAD usage 现在计入 EXEC resource account，`resource_domain_id` 只用于外层 CPU 调度分区；process+t0 使用向量 admission。线程还必须在资源预留后取得按需物理内核栈，任一映射失败都取消预留。19/12/6/6/4 tiny policy 仍用于精确触发账户/全局 ordinary/reserved 边界，不改变机制。

测试依次执行：

1. 普通子域占满 6 个线程槽，连续 8 次额外 `thread_create()` 都必须因容量拒绝；回收一个线程后可立即创建 replacement。`capacity_reject_stable` 只证明这类容量拒绝不污染计数，不声称注入了用户栈或 trapframe 映射故障。
2. 受控根域占满 4 个保留线程槽，额外创建被域上限拒绝；回收后 replacement 成功，分别验证保留域上限和退款复用。
3. 子进程创建多个永不主动退出的 sibling 后直接进程退出，不调用 `waittid()`；terminal teardown 撤销 sibling 并退款，随后同域可重新占满线程上限。
4. 两个普通 holder 加第三普通探针共同占满普通全局水位 12。第三域只有预扣主线程、远未达到域上限，其 `thread_create()` 仍被全局 ordinary 水位拒绝；新的普通进程 admission 同样拒绝。普通压力下，根保留域和新 workflow 保留域继续运行；两个保留域都各自未满、物理池仍留 1 槽时，额外保留线程被全局 reserved 水位 6 拒绝。释放保留/普通线程后，两类容量都可复用。
5. 攻击域占满主线程加 5 个 worker 的 6 个槽，其中 5 个 worker 不断 `sched_yield()`；独立 victim 域完成 512 次让出。每个攻击 worker 必须实际运行，但其 yield-loop 总计数不得超过固定 `bound=576`，即只允许 64 轮启停余量，证明线程数量没有放大外层域 CPU 份额。

runner 要求以下标记按顺序且各出现一次，日志中不得出现 `check failed`、`panic`、`unknown syscall`、`bad addr` 或 `IllegalInstruction`；`parent passed` 后 QEMU 还必须正常结束：

```text
threadresource_ucore: domain_limit=1
threadresource_ucore: capacity_reject_stable=1
threadresource_ucore: reserved_domain_limit=1
threadresource_ucore: reserved_domain_reuse=1
threadresource_ucore: exit_reuse=1
threadresource_ucore: ordinary_waterline=1
threadresource_ucore: global_thread_limit=1
threadresource_ucore: reserved_global_limit=1
threadresource_ucore: reserved_progress=1
threadresource_ucore: reserved_global_reuse=1
threadresource_ucore: global_reuse=1
threadresource_ucore: domain_fairness=1 hog=... victim=512 bound=576
threadresource_ucore: parent passed
[thread-resource] passed pool=19 ordinary=12 reserved=6 domain=6/4
[thread-resource] all checks passed
```

321s、359.4s、13680 和 13856 是旧线程计数/固定物理栈路径的历史结果。统一 resource account 与 lazy physical stack 版本已经通过本专项、`ci-check` 和三轮完整 Agent 套件；线程专项还在同一镜像连续 50/50 轮通过。

## 22. `iobudget_ucore`

`iobudget_ucore` 是块设备 I/O 与 buffer cache 分域机制的回归，随 `scripts/run-agent-tests.sh` 运行。它不通过固定 PID、文件名白名单或固定等待时长推断公平性，而是用 syscall 544 读取 `io_policy_info()` ABI v4 的持久 owner、class、owner/device lease/debt、物理完成和 cache 分区计数，再用 Guest pipe 建立原始 workflow lineage 压力进程与独立 workflow 的先后关系。测试还构造唯一 runnable 线程在内核 pipe 条件路径反复 `yield()` 的场景，要求 scheduler 的 idle kerneltrap 窗口仍能交付 timer/device 中断；另让持有已 unlink 文件的 lineage 子进程触发 page fault，验证 terminal teardown 的清理 I/O 仍归因并结清 debt。用户 wrapper 自动传入当前 `sizeof(struct io_policy_info)`；测试同时调用较小旧结构前缀，要求 `version/struct_size` 可读且尾部哨兵不被覆盖。内核的 sized-copy 规则由 `(addr, user_size)` 分发、8 字节最小头和 `min(user_size, current_size)` copyout 实现。

测试流程：

1. 在创建压力进程前先检查完整 ABI 和较短前缀 sized-copy，覆盖 `version`、内核 `struct_size`、最小 8 字节头和调用者尾部不被越界写。
2. 创建一个线程，使其阻塞在 I/O admission 后请求进程退出；等待线程展开，再比较 lease 计数，确认未完成 request 的 owner/shared/device lease 已退款。
3. 用一对 pipe 让子进程阻塞在内核读条件路径，父进程创建超过 workflow NORMAL burst 的文件并等待 I/O refill。该场景只有反复在内核态 `yield()` 的 waiter 可运行；操作必须完成并回收 waiter，证明 scheduler 每轮短中断窗口能交付 timer/device interrupt。
4. lineage 子进程创建并写入文件、保持已 unlink 文件描述符后报告 I/O 状态，再触发用户 page fault。父进程确认退出码为 `-2`，随后由同一 lineage 的观察子进程读取状态：owner 与 `NORMAL` class 不变、清理物理写增长、`unreserved_transfers` 不变，且 lease、owner debt、device debt 均为 0。
5. 测试主体创建独立 Orchestrator workflow，并只委派探针所需 pipe 端点。workflow 创建、预热并保持一个私有热块，读取 `io_policy_info()` 确认它取得不同于原 lineage 的持久 scope owner，并保存初始 resident/floor/cap。
6. 原 lineage 的独立子进程创建循环读写文件和同时越过 workflow NORMAL 初始信用包络及 `IO_CACHE_WORKFLOW_CAP` 的冷工作集。它校验物理传输与 rate decision 的单调、覆盖和无溢出关系，要求 refill 或 throttle 前进并观察到 cache eviction，证明压力实际到达速率和自身 cache cap。
7. 压力进程确认 owner 为不可变的原 lineage owner、class 为 `NORMAL`、profile 为 workflow NORMAL；owner/shared/device 的 token 与 lease 分别受 burst 约束，压力阶段结束时 lease 和 debt 全部结清且 completion sequence 前进。
8. 外部压力存活期间，父进程命令 workflow 探测此前预热的块。workflow 先在 probe read 前快照压力后的 owner 驻留量，再读取并校验内容，要求读取前仍保留 `min(initial_resident, cache_floor)` 且不超过 `cache_cap`；这样探针重新装入块也不能修饰被测状态。`physical_reads` 和 `cache_hits` 是 owner 聚合计数，可能包含异步 metadata 校验，因此不再被误作单次读取凭据。
9. 同一 Orchestrator 再成功完成真实文件写入，并要求 owner 聚合 `physical_writes` 增长且 `io_class == CONTROL`，证明 lineage 压力下 workflow 控制路径仍有进展。syscall 成功是操作完成依据；聚合计数只用于确认该 owner 确实产生物理写，不充当单次操作 receipt。
10. 停止 lineage 压力并等待两个子进程完整退出。原 lineage 的 `unreserved_transfers` 不得增长，证明用户物理传输没有丢失 syscall/background 归因。

通过标记：

```text
iobudget_ucore: thread_exit_lease_cleanup=1
iobudget_ucore: scheduler_interrupt_progress=1
iobudget_ucore: fault_exit_cleanup=1
iobudget_ucore: lineage_rate_accounting=1 immutable_owner=1
iobudget_ucore: nested_io_attribution=1
iobudget_ucore: cache_scope_isolation=1
iobudget_ucore: workflow_bounded_progress=1
iobudget_ucore: control_reserve_progress=1
iobudget_ucore: parent passed
```

ABI sized-copy 是没有单独 marker 的实质断言。上面的 marker 只能说明本次 Guest 运行完成；最终交付状态必须由与候选提交绑定的原始日志和证据包判定，历史耗时或旧套件结果不能替代当前完整验收。

PUBLIC 32/16、每 active workflow 24/12 + 48/24 + 8/4、每 retiring workflow `BACKGROUND` 8/4、SYSTEM 96/48 + 16/8、shared 32/16 的配置总和由编译期断言保守按 4 active + 8 retiring 放入 560/280 静态 envelope。运行时设备根对普通流量执行 token/lease/debt 限速；SYSTEM owner、`CONTROL` 和 `SYSTEM` class 在根信用耗尽时仍可凭 owner/class 保留预算带 device debt 前进，所以根 bucket 不是保护流量的硬总上限。shared fast path 在没有 admission waiter 时可直接借用，只有排队授权再按 owner/class cursor 轮转；当前测试没有断言 `shared_grants` 或排队轮转。

cache 的 SYSTEM/PUBLIC/active workflow floor/cap 为 40/96、24/48、36/64，当前退役清理 job 临时使用 3/8；cap 是稳态驻留边界，transient buffer 可暂时越界并在最后释放时失效。同块用 exclusive holder 串行化；持有 buffer 时 I/O/CPU checkpoint 均不睡眠或 yield，复合文件系统原语使用 FS atomic depth，只有释放全部 buffer 后才能在 quiescent checkpoint 偿债。这些 holder/atomic 条件由源码不变量和相关回归共同覆盖，`iobudget_ucore` 不逐项输出 marker。

`iobudget_ucore` 自身的动态压力仍只覆盖一个原始 workflow lineage 和一个独立 Orchestrator `CONTROL` owner；冷工作集能证明压力 owner 到达自身 cap/eviction，但不保证本次运行一定选择 workflow block 作为跨 owner donor，因此它验证的是公开的 owner resident floor，而不是某个具体热块的逐块保留 receipt。Recovery、SYSTEM/workflow `BACKGROUND`、多 workflow 同时压力、retiring 3/8、跨 owner LRU/transient 和主动 device-debt 注入仍不能从该场景外推。独立 `virtio-disk` runner 补充丢中断、延迟完成、描述符压力、设备状态错误、flush 禁用、timeout reset 和 stuck reset；`metadata-recovery` 的发布契约要求 primary/mirror 各八个 COW phase，并以 45 次 Guest 启动复测同盘重启、双目标 `BUSY/EIO/INTERRUPTED` 同启动重探、单次暂态 header-flush EIO，以及超过 background burst 的 32-record bank 在三类 terminal peer 下的 cursor/plan 恢复。Host mutation、构建和接线只构成 E1；45 次动态结果是否存在只能由候选提交对应 bundle 的原始产物判定。它们仍不等于永久设备故障或整机物理断电，且未覆盖启动时双 bank 同时损坏或 grouped qmap claim 中点掉电。

## 23. 物理页、持久状态与 VirtIO 故障复测

五组独立入口由 profile v5 串联，也可单独执行：

```bash
make physical-resource-test TOOLPREFIX=riscv64-linux-gnu-
make metadata-recovery-test TOOLPREFIX=riscv64-linux-gnu-
make observe-recovery-test TOOLPREFIX=riscv64-linux-gnu-
make virtio-disk-test TOOLPREFIX=riscv64-linux-gnu-
make fs-allocator-fault-test TOOLPREFIX=riscv64-linux-gnu-
```

`physical-resource` 同时检查共享 policy 的非法配置和测试钩子隔离，并在真实 Guest 中验证预留承诺生命周期、普通/保留域公平、退款、域隔离、系统保留及 teardown 归还。legacy mail 子用例在 fresh workflow account 内先取得 fork 后基线，再让 PUBLIC 子进程自投递一条消息，要求账户物理页用量精确增加 2；保持 sidecar 存活完成观测后退出子进程，账户用量必须精确回到 fork 前值。编译期负向门只约束配置，不能替代 `legacy_mail_accounting=1 alloc_delta=2 exit_delta=0`、`physicalresource_ucore: parent passed` 等动态 marker。

`metadata-recovery` 现计划执行 45 次 Guest 启动。primary 与 mirror 两段各覆盖八个 COW phase：同一次 crash boot 先动态确认 baseline 已完成双 bank 复制，再通过 test-profile-only syscall 显式取得下一代 COW 事务的 scope、generation 和单调唯一 arm token，随后发起第二次更新；不再用全局提交次数猜测目标事务。独立日志验证器严格要求 quiet baseline、`target_armed`、`target_bound`、`target_fire` 和 phase marker 唯一且有序，三段目标身份一致、job 不变、目标代恰为 baseline 加一，并核对 bank/phase。认证 supervisor 只在完整 phase marker 后向稳定 QEMU leader 发送首次且唯一的 `SIGKILL`，并要求跨 session 后代全部回收。恢复 Guest 启动前，host 先直接解析磁盘镜像；格式参数来自 `os/agent_metadata_disk.h` 的 RISC-V 编译 probe 与版本化 JSON 契约，不在 runner 复制结构偏移。primary 的 payload 发布前不得出现完整新值，header 写入后允许旧值或新值；mirror 中断必须保留已验证的新 primary；任意阶段均要求至少一份完整有效 bank、禁止同 generation 异 hash。正常恢复后两 bank 必须完全一致且旧 lifecycle 记录已清理。

启动读取 profile 覆盖 `BUSY/EIO/INTERRUPTED`，分别对全部 bank 和磁盘上较新的 bank 注入三次故障。首次 boot fault 只建立 pending，随后每个真实失败恰好对应一个连续 deferred attempt；`retries=N`、逐 bank remaining 倒计数、退避 deadline 和拒绝/恢复/创建/查询顺序均由完整日志行验证。另生成含 32 条记录、超过 16 个块 background burst 的真实 bank：Guest 先在同一 syscall 完成前台 live reload，再由 host 突然中止；host 分别把 peer 改为 `ABSENT`、`UNCOMMITTED`、`CORRUPT`，剩余大 bank 在 SYSTEM 预算下必须输出严格递增的读/prepare progress，最终同启动恢复且双副本一致。该组同时防止 terminal peer 反复驱逐 cursor、候选确认被跳过、prepare plan 重拷贝和 progress 被错误计入退避失败。bank selection 和 header-flush 用例继续验证较新 authority 不回滚，以及单次暂态 header-flush EIO 的显式不确定结果和副本修复。构建路径含 `panic` 不是 Guest 故障，只有完整 Guest panic 行才失败；production 对象不得引用 profile owner 或包含 marker。该用例不覆盖永久设备故障；power-cut profile 是突然中止 VM 的故障模型，`SIGKILL` 不清空宿主页缓存，因此仍不得表述为整机物理断电试验。Host mutation、生产/profile 交叉编译和 runner 接线属于 E1；45 次 Guest 是否完成只能由 C 对应 bundle 判定。

`observe-recovery` 先在第一启动中分配 audit/span/event/control/agent 五类身份和一个空闲 lifecycle 槽 generation；内核在分配后的第一条 marker 处输出数值，认证 runner 立即 `SIGKILL`，不会等待 syscall copyout、后续 audit 或 checkpoint。第二启动复用同一镜像并输出 successor，Host 要求五类数值全部严格增大、lifecycle 槽相同且 generation 严格增大。随后再以同一镜像完成三阶段 checkpoint/Recovery/reap 验证，覆盖 audit/span/timeline/provenance 的可解析性与隔离。receipt 子用例先证明 `PENDING` 不是证据和伪 id 为 `STALE`，再由 bounded `WAIT` 取得 exact active record 的 `DURABLE`；负例在持久前写入超过 checkpoint retention 的同 scope 记录，目标复制后原 entry 缺失必须为 `FAILED`。live receipt 已淘汰而 `target == 0` 时，测试合同还要求精确 entry 扫描前与 active generation 二次确认后都通过 replication fence；primary 已发布但 mirror 尚未 `COMMIT` 的 generation 不得返回 `DURABLE`。普通进程、Recovery 与旧 lifecycle 也分别被拒绝。生产对象的 size/nm 检查确保这些注入点不泄漏。

checkpoint v7 不再保存“最新连续后缀”。每个非空 scope 固定选择最新 tail 4 条，再从更早的可见窗口选择最多 4 个 causal diversity anchor；候选按 identity class、kind、stable principal 与可信 span 多样性评分，同分取较新的 sequence，最终 8 条按 sequence 重排。磁盘 entry sidecar 显式携带 `identity_class`、`link_flags`、`principal`、`span_owner` 和 `receipt_id`；`PREV_RETAINED` 与 `LATEST_TAIL` 分别表达直接保留前驱和固定 tail，不能用相邻数组位置臆造连续链。scope 级 `admission_drops` 记录取号/建链前的准入拒绝，成功入链但未被 retention 选中的 `hashed_omitted` 由计数关系推导；允许全部尝试都被拒绝的合法 drop-only scope。恢复必须先验证完整 v7 image，包括零保留字节、scope flags、sidecar 组合、全局 sequence/receipt 唯一性、lease 高水位、gap 与 `ledger_hash` 链尾，再在关中断窗口预检空槽并原子发布；插入失败要清除本轮槽位、索引和计数，live scope 已有新证据时不得由 reload 覆盖。

容量 mutation 使用 low 64 槽模型验证每个 active stable principal 的 8 条保证份额、空闲容量下到 16 条的借用突发，以及新主体到达时只回收已离开主体或其他主体高于 8 条的溢出。causal victim 的 scratch 必须按完整 burst 16 定长；构造重复 causal bucket 只落在第 9 到 16 条的变异，仍须识别冗余并保留真正唯一的 span/kind anchor。四槽镜像仍建模为三个普通槽和一个 Recovery successor 槽：冷启动四槽全满时普通 admission 必须关闭，只有成功 bootstrap 绑定的 Recovery 能替换带 successor 授权且已经 sealed 的保留槽；active、closing、retiring、普通未授权证据和 `REAP_AUTHORIZED` 中间态均不能被替换。REAP 先复制授权标志，随后由 SYSTEM 擦除；授权/擦除控制写通过通用 durable `URGENT` 标志调用 store provider 的 `expedite`，失败后的 retry 和 provider 安装继续走同一 notify 路径。普通 receipt 使用 flags=0，保留既有 serial fence 和写回合并窗口，不能为了加速 REAP 把所有观测写都升级成同步紧急提交。active replication mutation 另要求覆写 target 在 `INVALIDATE` 前撤销 fence、boot repair 不恢复 fence、repair/fail-closed 清零，并且只有验证后的 mirror `COMMIT` 能发布新 generation。上述 layout、Host parser 和 mutation 合同属于 focused E1，不替代 checkpoint v7 的 QEMU 多启动 E2。`virtio-disk` 仍逐项注入丢中断、延迟进展、描述符压力、设备状态错误、flush 禁用、超时 reset 和 reset 卡死，并要求各 profile marker 与最终 `parent passed`。

本轮 v7 之前工作树曾先通过 catalog capacity 14/14、metadata boot reprobe 47/47 和 catalog rollback fence 15/15 三组 Host 合同；随后本地 `observe-recovery` v51 完成 `boot0-cut`、`boot1`、`boot2`、`boot3` 四阶段及三次同镜像重启。每阶段的 catalog 协调均出现 `workflow_create_status=2 attempts=1` 后紧跟 `workflow_create_status=0 attempts=1`，即 PENDING 经核验转为 OK；boot3 还输出 `timeline_wait_epoch_recheck=1 injection=2 retries=1 bounded_timeout=1`、`timeline_wait_threads=1 filters=2 deadlines=2 targeted=1 timeout=1 cleanup=1`、`boot3_erased=1 generation_isolated=1 stable_identity=1` 和 `parent passed`，runner 最终输出 `[observe-recovery] power-cut lease and three-boot durable evidence lifecycle passed`。这只是 v51 的定向本地 QEMU 历史回归，不能证明 v7 已完成多启动 E2，更不能冒充 clean-HEAD release bundle、远程 CI 或完整 `full-verify`。

当前源码的 Host 静态/模型合同要求 catalog 总量 512、SYSTEM 64、ordinary 448、每 workflow 固定 112，并进一步约束 live AUTOSCAN 净增长 96、显式保留 16、独立 STORAGE inode domain limit、持久 deferred sidecar、饱和扫描抑制，以及槽位释放后的重建、写回和 reload。冷启动必须先按不可变 lifecycle key 淘汰旧动态 scope，再按稳定 v7 硬边界装载；模型要求 112 条旧 AUTOSCAN 完整加载、113 条损坏，加载后新增 AUTOSCAN 拒绝、count-neutral edit 与降额转换允许，降至 95 后才重新准入，失败持久事务恢复精确 pre-state。合同拒绝把当前软策略写入快照损坏判定或增加部分迁移状态。sidecar setter 与目录事件入口必须保持唯一，create 只能在 VFS 成功后协调；合同继续检查 ACTIVE/CLOSING/RETIRING 的 4 槽准入、lifecycle key 的 prepare/apply 重验及明确错误传播。实际 mutation 数以候选上的 checker 输出为准，只授予 E1。当前 AgentScope fresh-image 合同不变：A/B 各自创建 97 个普通文件、前 96 个物化、16 个显式 metadata 准入、第 17 个显式请求 `NO_SPACE`，并验证满 catalog 后普通 VFS create 与跨 scope 隔离仍成立；删除一个已索引文件后，第 97 个 deferred 文件必须重建、写回，并在强制 reload 后保持可查询。它不改写上段 v7 之前的历史记录；当前候选的 Reader、AgentScope、聚合门和 release 状态只在 [test-record.md](test-record.md) 集中记录。

这些多启动 runner 通过 `EVIDENCE_GUEST_LOG_FILE` 逐次追加 Guest 输出；控制台可以转发原始字节，但落盘 `.guest.log` 会把 CRLF 和孤立 CR 统一为 LF，作为 exact-line、SHA256、CSV 行号和 manifest 共同引用的 canonical transcript。full-verify 和 GitLab wrapper 都保存带 `runner-stdout`、`runner-guest-logs` 分段的组合日志。仅有 runner 汇总行而缺少非空 canonical Guest 日志时必须失败。

## 24. Workflow 强制撤销复测

最终专项复测命令：

```bash
CASE_TIMEOUT=260s AGENT_TEST_CASE=agentscope_ucore \
  bash scripts/run-agent-tests.sh
```

机制断言分为四组：

1. **关闭权。** 低权限 Sentinel 和后创建的 Orchestrator 不能关闭 scope；绑定根可以关闭自身，可信 bootstrap factory 可以按稳定 scope id 关闭目标；高位非零但截断后看似合法的 64 位参数被拒绝。
2. **先撤权、后清理。** ACTIVE -> CLOSING 立即使 Agent/VFS 授权失效并拒绝新 join、pending exec commit 和存储预留。关闭按完整 `(lifecycle id,generation)` 请求统一 teardown，不从其他线程栈外释放资源。
3. **成员与临时资源。** factory 关闭和根自然退出都包含一个阻塞在 `agent_wait()` 且持有 pipe 的低权限 Sentinel。父侧要求 lifetime pipe 真正 EOF；成员若从 wait 意外返回，会先写 poison byte 使测试失败。
4. **有界生命周期。** 根自然退出触发的撤销重复 9 轮，跨越 8 槽 ledger；replacement workflow 必须取得新 generation 并正常回收。
5. **PUBLIC 谱系。** workflow 根创建 PUBLIC child 和 grandchild；二者分别验证 Agent/VFS 凭据清零并发送 `P`/`G` 就绪标记，随后仍随原 lifecycle 被撤销。

最终输出：

```text
agentscope_ucore: scope_close_authority=1
agentscope_ucore: scope_controller_exit_revoke=1 public_lineage=1
agentscope_ucore: scope_forced_cleanup=1
agentscope_ucore: scope_replacement_admitted=1
agentscope_ucore: lifecycle_reclamation=1
agentscope_ucore: parent passed
```

验证时间线必须区分：371.5s、127.9s 和 126.1s 是 generation-safe lifecycle、PUBLIC 谱系、统一 teardown 和资源控制器之前的历史结果。后续 `agentscope_ucore` 专项约 `93.7s` 并取得 `public_lineage=1`；`75d0dfd` checkpoint 又完成旧 profile 的 clean 聚合验收，`14a9450` 完成具名定向复测。后续代码的聚合状态只由 release bundle 绑定。close 与 exec/spawn 精确竞态和多线程 controller 仍是独立缺口。

## 25. `workflow_teardown_race_ucore`

这是独立的组合竞态专项，不属于 `scripts/run-agent-tests.sh` 的 18 case。默认入口至少连续运行三轮：

```bash
make workflow-teardown-race-test TOOLPREFIX=riscv64-linux-gnu-
```

测试用 syscall 546 的 self-only lifecycle snapshot/compare ABI 观察当前进程自己的 immutable key、Context commit lane 与 metadata transaction gate；它不读裸 PCB，也不能把 key 当作关闭权限。完整 ABI 为 version 1/48 字节，raw syscall 接受至少 8 字节的 sized-prefix。坏 flags/key/地址在 copyout 前失败，合法 stale/not-found 则可以同时返回 self snapshot。

每轮组合覆盖：

1. factory 关闭和根自然退出两种入口都进入相同 phased teardown，并保留最终竞态快照；
2. PUBLIC 降权后代、Context lane owner/waiter 与 metadata gate owner/waiter 同时存在时仍被撤销并展开；
3. 阻塞 syscall 关闭用户 FD 后仍持有的临时 file 引用按 account 计费，连续生命周期超过全局保留边界后仍全部退款；
4. teardown 期间的 I/O debt、cache sponsor、inode 和 file object 被结算，另一 scope 保持进展；
5. factory-close 与 natural-exit 的 lifecycle id 都只有在退休完成后才能以更高 generation 重用，旧 key 返回 stale，新 account 为零债务且 inode 可复用。

profile validator 要求有序出现 ABI、两类退出、I/O、阻塞引用容量、generation 重用、fresh account 和 `parent passed` marker，并交叉核对 runner 注入的 domain/global file capacity。提交 `75d0dfd` 的一次 clean `full-verify` 中，该 teardown-race 专项按配置连续运行三轮并通过；metadata 目录拆分提交 `14a9450` 后又完成三轮定向复测。该用例仍没有精确注入 close 与 spawn/pending exec 的发布瞬间或多线程 controller。

## 26. 内核增长与模块边界预算

静态门入口：

```bash
make ci-check
```

该命令执行 budget checker 自测、Agent 模块边界脚本、canonical kernel/probe 构建，以及 kernel 与 agent-modules 两组检查。`ci/kernel-budgets.json` 是唯一阈值事实源，覆盖：

- 内核源码物理行数；
- stripped ELF、raw binary、text、data、BSS 和运行总量；
- `sizeof(struct proc)`；
- 9 页 Context detail sidecar 与完整 21 页 Agent 状态的单实例、全局、ordinary/reserved 池和单 account 容量；
- 线程调用图栈深、64 KiB boot stack 的链接跨度与启动调用图、32 MiB 虚拟栈容量和 8 MiB 受信/保留物理栈池；
- `ci/kernel-budgets.json` 版本化登记的 owner/bridge 集合的逐模块 LOC、导出符号和依赖方向，以及 core 不得重新定义已迁移权威状态；
- metadata transaction/file-state/catalog/query/scan/directory/objects/actions/prefetch/store（含 format/I/O）、IPC 和 contract headers 的 `metadata_control_plane` 聚合 source/text/BSS 预算；source 仅允许固定接口开销，loaded text/BSS no-growth；
- 受控符号 integration graph 的 SCC 硬上限。该图只追踪受控 Agent 符号，不是完整 uCore 调用图。

阶段性工作树的旧 `ci-check` 结果只作历史比较，不能据此声明当前候选、最终 clean-HEAD evidence 或远程 CI 通过。当前候选的唯一数值记录见 [test-record.md](test-record.md)。

预算采用 downward ratchet：体积下降后应收紧 baseline/max，不能保留足以让旧大数组或 monolith 回归的宽松上限。Context detail 与 legacy mail 迁出 PCB 后曾降至 25640 B；之后预冻结静态 probe 的 `sizeof(struct proc)=25936`、JSON `25936/27233` B 也只作 H-17 历史比较，不是发布 C 的最终指标。每个活跃 Agent以一次 `RESOURCE_AGENT_STATE_PAGE` 请求原子计费 21 页：9 页 detail/attribution sidecar、6 页用户 mirror、6 页可信 shadow，共 84 KiB；Context v8 只重排既有页内布局。legacy mail 的两页 sidecar 只在首次合法发送时按目标 EXEC account 另行分配。idle 普通进程不分配这些页；它们都从通用 `kalloc` 取得，不是全局 OOM 下的硬保留。最终源码、镜像、`struct proc`、栈和 metadata aggregate 数值以 C 的 canonical budget log、版本化 JSON 和 bundle metrics 为准。

完整 Agent 时间预算与静态门分开。它统计当前 18 个 QEMU case 的 monotonic 运行时间总和，不含编译；targeted `AGENT_TEST_CASE` 不能满足全套时间门。旧 16-case、`31d4ddf53695` 及 `814021ab9dac` 的三轮 18-case 都只作对应源码的历史校准，不能向新候选传播 baseline、limit、samples 或 fingerprint；GitLab job 继续用 `resource_group` 串行化重新校准的 QEMU。当前策略和实际门禁结果见 `ci/kernel-budgets.json` 与 [test-record.md](test-record.md)。

QEMU runner 以二进制方式读取并全量 drain 子进程输出，大小写不敏感检测包括 panic 在内的预定义 failure 模式，marker 后仍继续扫描；持久 `.guest.log` 使用 canonical LF。监控循环有界并持续重查 case/marker deadline，输出洪泛、迟到 marker、普通 case 信号退出、非零退出和后置 panic 都不能成功。显式 checkpoint profile 只接受 runner 在完整 marker 后发送的单次 `SIGTERM`；显式 powercut profile 只接受认证 supervisor 向稳定 QEMU leader 发送的单次 `SIGKILL`，且随机 nonce、PID/starttime、镜像退出码、控制通道和完整后代回收证明必须一致。workload 自行杀死 leader/supervisor 或留下跨 `setsid()` 后代均失败。预期 fault、checkpoint 与 powercut 只能由各自显式 profile 启用。通用 runner/profile validator 的自测集合以源码为准；duration checker 只接受恰好包含全部 18 个预期 Agent case 且顺序一致的 timing file。Context-sync/WAIT_ATOMIC prelude 有独立 timing file，不得混入这 18 行。Reader action runner另按 clean/build/guest 分阶段：构建只看退出码，guest 才按完整日志行识别故障。`agentfinal_ucore` 的普通套件和 prelude 都要求 `context_commit_lane=1 sequence=1..3 hash=1`。
