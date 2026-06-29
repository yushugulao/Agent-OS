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
12. 子进程检查 header 中的当前 cause/span、provenance edge 计数和 latest record hash；同时检查每条记录的 `prev_hash` 都指向上一条记录的 `record_hash`。
13. 子进程检查第 8 条记录的 payload/result 短文本为 `ucore-final`。
14. 子进程调用 `context_detail()`，检查完整 `agent_op`、完整 `agent_result` 和 `SYSTEM` flag。
15. 子进程手动篡改用户态 Context 镜像中的第一条记录 sequence。
16. 子进程再次调用 `context_snapshot()`。
17. 子进程检查 snapshot 返回的第一条记录仍为原始 sequence，并检查用户镜像被刷新。
18. 子进程向 `header.user_cache_offset` 写入 `cache-ok`，再次调用 `context_snapshot()` 后检查 cache 内容仍保留。
19. 子进程调用 `context_push()` 追加手动记录，检查 `MANUAL` flag 和 detail ring。
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
26. 子进程调用 `agent_file_prefetch_span_snapshot()`，检查同一 span 的全局提示中包含当前 Agent 产生的提示，并带有 `SPAN_BUS` 原因位、source pid 和 target pid。
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
37. 子进程调用 `agent_ledger_snapshot()`，读取全局短记录摘要，检查可见记录数、sequence 范围、链尾 hash、Context/event/sched/prefetch 分类计数，并与 `agent_audit_snapshot()` 返回的明细首尾 sequence 和相邻 hash 关系对应。
38. 子进程输出 `agentfinal_ucore: passed` 并退出。
39. 父进程等待子进程退出，检查退出状态为 0，输出 `agentfinal_ucore: parent passed`。

### 1.2 输出阅读方式

本测试的串口输出主要分为三类：Context 与工具调用检查、文件查询与事件检查、timeline/provenance/ledger 检查。阅读时关注 `context size`、`batch first_seq/last_seq`、`tamper_protected`、`fifo`、`file_query`、`event_wait`、`timeline_query`、`provenance_graph`、`run_ledger` 和最终通过标记即可。完整样例输出见 [test-record.md](test-record.md)。

### 1.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| Agent 创建 | pid 1 父进程创建 orchestrator Agent 子进程 |
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
| Run Ledger 摘要 | `agent_ledger_snapshot()` 返回全局短记录的 sequence 范围、分类计数和链尾 hash，明细 hash 链能逐条验证 |
| 文件索引 | `agent_file_query()` 使用索引路径 |
| 预取提示 | `agent_file_prefetch_snapshot()` 返回由文件查询历史和对象标签依赖生成的 metadata 提示 |
| span 预取提示 | `agent_file_prefetch_span_snapshot()` 返回当前 span 中带 source/target pid 的全局 metadata 提示 |
| Agent 事件 | watch/wake/wait 自唤醒成功 |
| 特权 Agent 能力 | orchestrator 能初始化文件元数据并向自身投递事件 |

## 2. `agentfs_ucore`

`agentfs_ucore` 是任务四文件系统能力测试，重点检查 Agent 文件元数据是否绑定真实根目录文件 inode，并写入和重新加载私有 `.agentmeta` 元数据文件。

### 2.1 测试流程

1. 父进程创建 orchestrator Agent 子进程。
2. 子进程调用 `agent_file_meta_init()`，加载 `.agentmeta` 或创建空元数据表。
3. 子进程由用户态写入设定的模拟流程示例元数据，并查询该文件对象，检查返回项包含真实 `dev`、`inum` 和 `size`。
4. 子进程创建自定义真实文件，写入内容。
5. 子进程用 `agent_file_meta_set()` 将自定义逻辑属性绑定到该真实文件。
6. 子进程查询自定义文件，检查 `dev + inum` 和文件大小与真实文件一致。
7. 子进程调用 `read_file_digest`，分别用物理文件名和属性 selector 定位同一文件，检查 size、bytes、hash 和 preview。
8. 子进程通过 `agent_info()` 读取 digest cache 计数，确认第二次读取同一真实文件时命中缓存。
9. 子进程改写同一真实文件内容，再次读取 digest，确认 hash/preview 更新且 digest cache 出现新的 miss。
10. 子进程用 `agent_timeline_query()` 按 `source=CONTEXT` 和 `tool_id=READ_FILE_DIGEST` 查询，确认统一 timeline 中保留 size、bytes、hash 和 preview。
11. 子进程再次调用 `agent_file_meta_init()`，确认自定义元数据来自 `.agentmeta` 重新加载，没有被空表覆盖。
12. 子进程重复执行同一个非强制扫描查询，确认 `plan_reason` 带有 `AGENT_FILE_QUERY_REASON_CACHE_HIT`。
13. 子进程写入接近 128 条真实文件元数据，制造足够的数据量。
14. 子进程分别运行扫描查询和索引查询，检查索引路径的 `scanned_records` 明显更少。
15. 子进程检查查询计划：扫描路径必须返回 `AGENT_FILE_QUERY_PLAN_SCAN`，索引路径必须返回 `AGENT_FILE_QUERY_PLAN_STATUS_INDEX`，并带有 status 索引原因、索引桶和候选记录数。
16. 子进程调用 `dependency_query`，分别带入设定的模拟流程和备用模拟流程，检查同名 label 的依赖结果按 run_id 分开。
17. 子进程调用 `dependency_update` 注册一条 `source -> target` 通用对象依赖，再用 `dependency_query` 验证该依赖可见。
18. 子进程读取预取提示，检查提示由对象标签依赖产生、使用 label 索引计划，并指向当前 run 内的 analyze/report 等后续 label。
19. 子进程清空某条记录的 status，确认属性更新生效，并确认旧 generation 查询缓存没有返回过期命中。
20. 子进程删除绑定文件，确认关联元数据随文件删除被清理。
21. 子进程调用 `action_commit` 指向不存在的 selector，确认返回 `AGENT_STATUS_NOT_FOUND`。

### 2.2 输出阅读方式

本测试的输出重点是 inode 绑定、私有 `.agentmeta` 重新加载、内容摘要、查询缓存、scan/index 候选差异、依赖注册和 selector 未命中。阅读时关注 `demo_inode`、`custom_inode`、`content_digest`、`digest_cache_invalidated`、`.agentmeta_reload`、`bulk_index`、`query_plan`、`dependency_update`、`delete_clears_metadata` 和最终通过标记。完整样例输出见 [test-record.md](test-record.md)。

### 2.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| 真实 inode 绑定 | 查询结果返回 `dev`、`inum`、`size` |
| `.agentmeta` 可写入和重新加载 | 自定义元数据重新初始化后仍存在 |
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

`agentloop_ucore` 是任务五事件运行机制测试，重点检查 FIFO 事件队列、watch/unwatch、有限 timeout 睡眠、wait cancel、TIMER unwatch 和 heartbeat stop 是否都可运行。

### 4.1 测试流程

1. 父进程创建 orchestrator Agent 子进程。
2. 子进程注册 message watch。
3. 子进程连续投递多个事件，调用 `agent_wait()` 检查 FIFO 顺序。
4. 子进程检查投递和消费的事件包含 cause/span。
5. 子进程填满 16 槽事件队列，再尝试投递第 17 个事件，确认返回 `AGENT_STATUS_NO_SPACE` 且旧事件没有被覆盖。
6. 子进程删除 watch，再投递相同事件，确认不会唤醒。
7. 子进程重新注册 watch，调用有限 timeout wait，确认线程进入睡眠并由 timeout 唤醒，且 `wait_loop_count` 增量很小。
8. 子进程注册 TIMER watch，启动 heartbeat，确认 heartbeat 事件可唤醒等待。
9. 子进程删除 TIMER watch 后再次启动 heartbeat，确认不会消费 TIMER 事件。
10. 子进程调用 `agent_heartbeat_stop()`，确认停止后不再产生 heartbeat 事件。
11. 子进程创建 sentinel 等待者，调用 `agent_wait_cancel()` 设置一次性取消令牌，确认等待者返回 `AGENT_STATUS_CANCELLED`，事件类型为 `AGENT_EVENT_CANCELLED`，并带有 reason、cause/span 和 Context 记录。

### 4.2 输出阅读方式

本测试输出围绕事件顺序、事件因果、队列满处理、watch 删除、有限 timeout 睡眠、TIMER watch、heartbeat stop 和 wait cancel 展开。完整样例输出见 [test-record.md](test-record.md)。

### 4.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| FIFO 顺序 | 多事件按投递顺序被消费 |
| 事件因果信息 | 投递和消费事件均携带 cause/span |
| 队列满语义 | 满队列返回 `AGENT_STATUS_NO_SPACE`，旧事件不被覆盖 |
| watch 删除 | `agent_unwatch()` 后相同事件不再匹配 |
| timeout 睡眠 | 有限 timeout wait 返回 `AGENT_STATUS_TIMEOUT`，并用 `wait_loop_count` 检查是否避免反复轮询 |
| wait cancel | 受权 Agent 可取消目标 Agent 的等待，普通事件队列满也不会阻挡取消令牌 |
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

1. 普通 init 调用 `agent_create_role(AGENT_ROLE_ORCHESTRATOR)` 创建 orchestrator。
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
31. orchestrator 调用 `agent_audit_snapshot()`，确认最近全局审计短记录中同时出现 sentinel、investigator、recovery，且包含 Context、事件、调度和预取交接记录。
32. orchestrator 调用 `agent_audit_query()`，按 Context kind、span、sentinel 文件状态事件、预取 source/target 和最新 sequence 过滤查询全局短记录。
33. orchestrator 调用 `agent_timeline_snapshot()`，检查统一 timeline 中包含 Context、事件、调度和预取交接摘要。
34. orchestrator 调用 `agent_timeline_query()`，按 audit source、prefetch kind、source pid、target pid 和 handoff flags 精确读取 sentinel 到 investigator 的预取交接记录，按 tool id 精确读取 digest 内容证据，并用 after-cursor 检查同一 timeline 可按上一条记录继续读取。
35. orchestrator 调用 `agent_provenance_snapshot()`，确认 message、prefetch 和 digest 内容证据都进入因果图。
36. orchestrator 输出 `labdemo_ucore: passed`。
37. 普通 init 等待 orchestrator 退出，输出 `labdemo_ucore: parent passed`。

### 9.3 输出阅读方式

综合场景输出按事件链阅读：运行对象建立、监听注册、故障事件、文件查询、权限拒绝、预取提示、Agent 间消息、内容摘要读取、模板 LLM 请求、恢复计划、恢复动作、幂等拒绝、工件更新、最终 recovered 状态、全局审计、统一 timeline 和 provenance。完整样例输出见 [test-record.md](test-record.md)。

### 9.4 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| 多 Agent 并存 | 同时创建 sentinel、investigator、recovery |
| 控制面权限 | 普通 init 只启动 orchestrator；元数据初始化、失败注入和角色 Agent 创建都由 orchestrator 完成 |
| 文件状态事件 | orchestrator 注入 failed 状态后唤醒 sentinel |
| 文件属性查询 | sentinel 查询失败工件 |
| 依赖查询 | investigator 查询 align 的影响范围 |
| 权限控制 | sentinel 恢复动作被拒绝 |
| Agent 间通信 | sentinel 唤醒 investigator，investigator 唤醒 recovery |
| span 预取查询 | investigator 按当前 span 查询全局提示，确认提示来源和接收者 |
| 当前 span 短记录 | investigator 按当前 span 查询 Context、事件和预取交接短记录 |
| Context 审计 | investigator 输出 snapshot |
| 全局审计 | orchestrator 查询最近全局短记录，确认多 Agent 的 Context、事件、调度和预取交接摘要都可见 |
| 审计过滤查询 | orchestrator 按 kind、span、目标事件和起始 sequence 过滤全局短记录 |
| 统一 timeline | orchestrator 用一个接口读取 Context、事件、调度和预取交接摘要 |
| timeline 过滤查询 | orchestrator 用一个接口按条件读取 prefetch handoff 记录，并按上一条已读记录继续读取 timeline |
| 因果边查询 | orchestrator 用一个接口确认 sentinel 到 investigator 的 message 和 prefetch 触发关系 |
| 幂等恢复 | recovery 第二次 rerun 返回 duplicate |
| 最终状态 | 输出 `FINAL status=RECOVERED` |

## 10. `agentsecurity_ucore`

`agentsecurity_ucore` 是权限限制负向测试，专门覆盖检查中指出的“普通进程能直接改全局元数据、伪造事件或取消等待”“用户态自报 role 可绕过权限”的问题，并检查普通进程不能读取全局、当前 span、统一 timeline 或 timeline query/read 短记录，sentinel 不能读取或过滤全局审计短记录。

### 10.1 测试流程

1. 普通 init 验证 `mailread()` 无消息返回 0，`mailwrite()` 写入自己成功，随后 `mailread()` 能读回相同内容。
2. 普通 init 调用 `agent_wake()`，预期返回 `-1`。
3. 普通 init 调用 `agent_file_meta_init()`，预期返回 `-1`。
4. 普通 init 调用 `agent_file_meta_set()`，预期返回 `-1`。
5. 普通 init 调用 `agent_audit_snapshot()`、`agent_audit_query()`、`agent_span_trace_snapshot()`、`agent_timeline_snapshot()`、`agent_timeline_query()` 和 `agent_timeline_wait()`，预期返回 `-1`。
6. 普通 init 直接 `open(".agentmeta")`、`open(".agentmeta", O_CREATE)`、`unlink(".agentmeta")`，预期均返回 `-1`。
7. 普通 init 创建一个普通子进程，子进程作为 usershell 等价路径创建 orchestrator Agent。
8. orchestrator 子 Agent 通过 `agent_info()` 检查真实 role 和 capability mask。
9. 普通 init 创建主 orchestrator Agent。
10. orchestrator 在未初始化元数据前执行带索引查询，预期返回 0 条命中且不会阻塞。
11. orchestrator 初始化文件元数据。
12. orchestrator 使用 legacy `agent_call()` 传入不一致的 `tool_id` 和 `tool_name`，预期返回 `AGENT_STATUS_BAD_REQUEST` 和 `tool_mismatch`。
13. orchestrator 分别把设定的模拟流程和另一个模拟流程的比对处理、报告生成环节置为 failed。
14. orchestrator 创建 sentinel Agent。
15. sentinel 检查真实 role/capability mask。
16. sentinel 把 `agent_op.arg0` 伪造成 `AGENT_ROLE_RECOVERY` 后调用 `capability_check("action_commit")`，预期仍返回 `AGENT_STATUS_DENIED`。
17. sentinel 继续伪造 recovery 调用 `action_commit align`，预期返回 `AGENT_STATUS_DENIED`。
18. sentinel 继续伪造 recovery 调用 `artifact_update report`，预期返回 `AGENT_STATUS_DENIED`。
19. sentinel 调用 `dependency_update` 注册对象依赖，预期返回 `AGENT_STATUS_DENIED`。
20. sentinel 调用 `read_file_digest` 读取真实文件内容证据，预期返回 `AGENT_STATUS_DENIED`，说明 metadata read 不等于 content read。
21. sentinel 调用 `agent_audit_snapshot()` 和 `agent_audit_query()`，预期返回 `AGENT_STATUS_DENIED`。
22. sentinel 直接调用 `agent_file_meta_set()`，预期返回 `AGENT_STATUS_DENIED`。
23. sentinel 查询设定的模拟流程和另一个模拟流程状态仍为 failed，说明拒绝路径没有改变文件状态。
24. orchestrator 创建 recovery Agent。
25. recovery 检查真实 role/capability mask。
26. recovery 调用 `action_commit`，payload 使用 label、run_id 和 namespace 定向选择另一个模拟流程；即使 `agent_op.arg0` 填成 sentinel，也按真实 recovery role 成功。
27. recovery 使用同一 corr_id 再次调用同一 selector，预期返回 `AGENT_STATUS_DUPLICATE`。
28. recovery 调用 `artifact_update`，payload 使用 label、run_id 和 namespace，只写入另一个模拟流程的目标报告。
29. orchestrator 查询另一个模拟流程的比对处理和报告生成环节变为 ok，设定的模拟流程仍为 failed，说明动作和工件更新没有跨 run 修改。
30. 测试输出 `agentsecurity_ucore: passed` 和 `agentsecurity_ucore: parent passed`。

### 10.2 输出阅读方式

本测试输出围绕普通进程拒绝、`.agentmeta` 保护、真实 role/capability、初始化前索引查询安全、legacy 参数校验、sentinel 伪造失败、recovery 定向动作和多 run 工件更新展开。完整样例输出见 [test-record.md](test-record.md)。

### 10.3 覆盖结论

| 覆盖点 | 检查方式 |
| --- | --- |
| 普通进程不能直接投递事件、取消等待或配置调度 | `agent_wake()`、`agent_wait_cancel()`、`agent_sched_config()` 返回 `-1` |
| 全局审计读取、过滤、调度配置和依赖注册权限 | 普通进程调用返回 `-1`，sentinel 调用返回 `AGENT_STATUS_DENIED` |
| 普通进程不能直接修改文件元数据 | `agent_file_meta_init()`、`agent_file_meta_set()` 返回 `-1` |
| 普通进程不能直接访问 `.agentmeta` | `open`、`open(O_CREATE)`、`unlink` 均返回 `-1` |
| 普通进程 mail 基础路径可用 | `mailwrite()` 写入，`mailread()` 读回同一内容 |
| usershell 手动运行路径可用 | pid 1 的普通直接子进程可创建 orchestrator |
| 初始化前索引查询安全 | 未调用 `agent_file_meta_init()` 前，索引查询返回 0 条命中且不阻塞 |
| legacy 工具名和工具 ID 不一致会失败 | `agent_call()` 返回 `AGENT_STATUS_BAD_REQUEST` 和 `tool_mismatch` |
| legacy 参数键和类型校验 | 错误参数返回 `AGENT_STATUS_BAD_PARAM`，syscall-only 工具不能走 batch |
| 用户态 role 参数不可信 | sentinel 伪造 recovery 仍被拒绝 |
| 文件状态拒绝路径无副作用 | sentinel 伪造 rerun 后 align 仍为 failed |
| recovery 权限来自真实 PCB 字段 | recovery 即使传入 sentinel role，也能按真实权限恢复 |
| 重复动作被识别 | 相同 corr_id 第二次 action 返回 duplicate |
| 多 run 动作和工件更新不会误伤 | 只更新 selector 指定的另一个模拟流程，设定的模拟流程保持 failed |

## 11. 运行方式和复现建议

推荐用脚本运行完整测试：

```bash
bash scripts/run-agent-tests.sh
```

如果需要单独复现某一项，运行命令见 [scenario-script.md](scenario-script.md)；完整验证入口见 [verification.md](verification.md)。

不要在同一工作树中并行启动多个 QEMU 测试，因为 `nfs/fs-copy.img` 会被多个进程同时访问，可能造成镜像锁冲突。
