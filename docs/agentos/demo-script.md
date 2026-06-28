# 最终演示讲解稿

本文档用于现场讲解或录制演示视频。推荐演示顺序是：先讲系统目标，再跑正确性测试，再跑性能测试，最后跑多 Agent 综合场景。

## 1. 开场说明

本项目在 uCore 内核上实现 Agent-OS。它不是普通用户态脚本，而是内核级 Agent 进程、结构化工具调用、上下文历史、文件元数据索引和 Agent 事件运行机制。

当前演示分为九部分：

```bash
agentfinal_ucore
agentfs_ucore
agentscan_ucore
agentloop_ucore
agentsched_ucore
agentbench_ucore
labbench_ucore
labdemo_ucore
agentsecurity_ucore
```

三者分工：

| 程序 | 作用 |
| --- | --- |
| `agentfinal_ucore` | 覆盖任务一至三核心功能，同时检查文件索引和事件自唤醒 |
| `agentfs_ucore` | 检查任务四的真实 inode 绑定、私有 `.agentmeta` 重新加载和索引查询 |
| `agentscan_ucore` | 检查任务四的根目录自动扫描、真实文件元数据建立和索引维护 |
| `agentloop_ucore` | 检查任务五的 FIFO 事件队列、unwatch、有限 timeout 睡眠、wait cancel、TIMER unwatch 和 heartbeat stop |
| `agentsched_ucore` | 检查任务五的 Agent 感知调度、受权配置、事件状态、调度原因和公平性计数 |
| `agentbench_ucore` | 给出批量调用、Context 直接读、snapshot、文件索引候选记录数的性能证据，并验证 timeout/heartbeat、busy polling 与 wait/wake 计时 |
| `labbench_ucore` | 初步演示规划中的性能入口，当前包装运行 `agentbench_ucore`，后续可升级为 `labbench --full` |
| `labdemo_ucore` | 展示一个由 orchestrator 控制的多 Agent 实验恢复场景 |
| `agentsecurity_ucore` | 展示普通进程和低权限 Agent 无法越权，并验证普通 mail 与多 run 精确恢复 |

## 2. 环境和运行方式

演示前说明当前环境：

- WSL2 Ubuntu 26.04；
- QEMU riscv64；
- `riscv64-linux-gnu-` 工具链；
- 当前 Git 分支：`uCore`。

推荐运行：

```bash
bash scripts/run-agent-tests.sh
```

如果需要分步演示，可以分别运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfs_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentscan_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentloop_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsched_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsecurity_ucore CHAPTER=agent
```

## 3. 正确性演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
```

重点解释输出：

```text
agentfinal_ucore: context size=24576 capacity=128
```

说明每个 Agent 有 6 页 Context，最多保留 128 条可见历史，并为完整 detail ring 和用户自管 cache 预留空间。

```text
agentfinal_ucore: batch first_seq=1 last_seq=64
```

说明一次 syscall 执行 64 个工具调用，并保证 sequence 连续。

```text
agentfinal_ucore: short_text_history=1 payload=ucore-final result=ucore-final
```

说明 Context Path 保存短 payload/result 摘要，读者可以看到多轮调用的内容，不只是数字计数。

```text
agentfinal_ucore: context_detail=1 sequence=8
agentfinal_ucore: record_flags system=1 manual=1 truncated=0
agentfinal_ucore: provenance_graph=1 edges=126 context=1 audit=1
agentfinal_ucore: timeline_wait=1 timeout=-7 source_gate=1 event_gate=1 wake=1 query=1 read=1 sleeps=1 wakeups=1
```

说明短摘要之外还可以按 sequence 查询完整请求/响应；系统自动记录、手动追加记录和可见因果边能被区分；页面或 Agent worker 也可以等待新的匹配 timeline 记录，并且内核会按完整 filter 减少无关唤醒。

```text
agentfinal_ucore: tamper_protected=1
```

说明用户态直接修改 Context 镜像不能伪造内核权威历史。当前设计同时兼顾直接读性能和可信 snapshot。

```text
agentfinal_ucore: causal_context=1 first_cause=0 next_cause=1 span=1 edges=63
agentfinal_ucore: context_integrity=1 first_hash=... latest_hash=...
```

说明 Context v6 记录了 cause/span 因果字段和完整性链：第一条记录是 root，第二条记录指向第一条记录，同一批工具调用处于同一个 span；每条记录还保存前一条记录 hash 和自身 hash。

```text
agentfinal_ucore: user_cache_preserved=1 offset=21504 size=3072
agentfinal_ucore: legacy_name_protocol=1
```

说明 Context 尾部提供 Agent 自管 cache，snapshot 不会覆盖它；`agent_call()` 也能按工具名称和参数键值列表完成正式结构化调用。

```text
agentfinal_ucore: fifo oldest=66 latest=193 dropped=65
```

说明超过容量后按 FIFO 淘汰旧记录，并维护 oldest/latest/dropped 元信息。

```text
agentfinal_ucore: file_query hits=2 scanned=2 used_index=1
agentfinal_ucore: prefetch_hints=1 count=3 first_stage=analyze
agentfinal_ucore: span_prefetch=1 count=... first_stage=...
```

说明文件元数据查询已经走索引路径，并且内核根据本次 align 查询和对象标签依赖给出了后续可能关注的工件提示；同一 span 的全局提示总线也能查到对应提示。

```text
agentfinal_ucore: event_wait=1 payload=self wake
```

说明 Agent watch/wake/wait 机制可用。

```text
agentfinal_ucore: runtime_trace=1 records=... context=1 sched=1 wait=1
agentfinal_ucore: span_trace=1 records=... context=1 event=1
agentfinal_ucore: unified_timeline=1 records=... context=1 sched=1 audit=1 prefetch=1
agentfinal_ucore: timeline_query=1 audit=213 recent=281 cursor=177
agentfinal_ucore: run_ledger=1 records=... hash=... context=... event=... sched=... prefetch=...
```

说明内核能把当前 Agent 的 Context 摘要、调度原因和事件等待记录合并成运行轨迹，也能按当前 span 返回本轮协作的系统级短记录，并能对统一 timeline 做内核侧过滤和游标增量读取。Run Ledger 摘要还可以用一个小结构确认全局短记录的 sequence 范围、分类计数和链尾 hash。纯用户态系统通常只能从日志拼接这些信息，而这里由内核直接返回结构化短记录。

```text
agentfinal_ucore: unified_timeline=1 records=... context=1 sched=1 audit=1 prefetch=1
agentfinal_ucore: timeline_query=1 audit=213 recent=281 cursor=177
```

说明最终展示层可以用一个接口读取 Context、调度、审计和预取提示，也可以用 query 只拉取某个来源或某个 tick 之后的记录，不需要分别理解四套底层结构。

最后看到：

```text
agentfinal_ucore: passed
agentfinal_ucore: parent passed
```

## 4. 文件系统能力演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfs_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `demo_inode` | 用户态演示元数据已经绑定真实根目录文件的 `dev/inum` |
| `custom_inode` | 用户态创建的新文件也能绑定 Agent 元数据 |
| `bulk_index` | 接近 128 条记录时，索引路径检查的候选记录少于扫描路径 |
| `query_plan` | 内核说明本次索引路径按 status 选择 bucket，并检查了多少候选记录 |
| `prefetch_hints` | 内核根据历史查询和对象标签依赖给出后续 metadata 提示 |
| `.agentmeta_reload` | 再次初始化时从私有 `.agentmeta` 重新加载自定义元数据 |
| `clear_status` | 属性清空能够生效 |
| `delete_clears_metadata` | 删除真实文件会同步清理 Agent 元数据 |
| `missing_selector_not_found` | 恢复/报告 selector 没有命中时返回明确失败 |

最后看到：

```text
agentfs_ucore: passed
agentfs_ucore: parent passed
```

## 5. 自动扫描演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentscan_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `background_scan usershell=1` | 内核在调度器空隙扫描根目录，发现镜像中已有真实文件 |
| `auto_file_create=1` | 普通文件 syscall 创建的新文件会自动进入 Agent 文件元数据表 |
| `auto_file_delete=1` | 删除真实文件后，自动元数据会被下一轮扫描清理 |

最后看到：

```text
agentscan_ucore: passed
agentscan_ucore: parent passed
```

## 6. Agent Loop 演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentloop_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `fifo=1` | 事件按照投递顺序被消费 |
| `event_causality=1` | 事件携带 cause/span，消费事件后的工具调用可以继续同一因果链 |
| `overflow_dropped=1` | 16 槽队列满时拒绝新事件，不覆盖旧事件 |
| `unwatch=1` | watch 可删除 |
| `timeout_sleep_no_poll=1` | 有限 timeout 等待进入睡眠，不通过循环消耗 CPU |
| `timer_unwatch=1` | TIMER watch 删除后，heartbeat 不再投递可消费 TIMER 事件 |
| `heartbeat_wake_stop=1` | heartbeat 能唤醒 Agent，停止后不再投递 |
| `wait_cancel=1` | 受权 Agent 能取消目标 Agent 的等待，目标返回取消事件 |

最后看到：

```text
agentloop_ucore: passed
agentloop_ucore: parent passed
```

## 7. Agent 调度演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsched_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `role_weights` | 不同 Agent 角色有不同内核调度权重 |
| `configurable_policy` | orchestrator 可受权调整目标 Agent 的 weight、priority 和 budget |
| `event_priority` | 有待处理事件的 Agent 被调度器识别并记录 |
| `reason_trace` | `agent_sched_snapshot()` 能读出最近调度原因，评委可以看到事件队列、角色权重和调度分数 |
| `fairness` | 多次让出处理器后，调度次数、让出次数和虚拟运行量都会增长 |

最后看到：

```text
agentsched_ucore: configurable_policy=1 weight=150 priority=20 budget=3
agentsched_ucore: reason_trace=1 records=6 reason=131 score=1655
agentsched_ucore: passed
agentsched_ucore: parent passed
```

## 8. 性能演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `scalar_agent_run` | 单个工具调用基线 |
| `batch_agent_run` | 64 个 op 合并到一次 syscall，减少内核入口次数 |
| `direct_context` | 用户态直接读 Context 镜像，不需要 syscall |
| `context_query` | 逐条查询历史 |
| `context_snapshot` | 一次返回多条历史，适合批量读取 |
| `file_scan_query` | 文件元数据扫描路径 |
| `file_index_query` | 文件元数据索引路径 |
| `file_digest_read` | 真实文件短预览和内容指纹读取路径 |
| `file_digest_cache` | 展示重复读取同一真实文件内容证据时的 digest cache 命中 |
| `file_query_records` | 直接展示扫描路径和索引路径检查的候选记录数量 |
| `file_query_plan` | 直接展示查询计划和索引选择原因 |
| `file_query_cache` | 展示重复文件属性查询命中同一 `fs_generation` 下的内核结果缓存 |
| `prefetch_records` | 展示预取提示 snapshot 返回的 metadata 提示数量 |
| `file_prefetch_snapshot` | 展示读取预取提示的计时观测 |
| `timeout_heartbeat` | 无事件等待会 timeout，心跳字段可通过 `agent_info()` 观察 |
| `busy_poll_query` | 用户态轮询查询路径的计时观测 |
| `event_wait_wake` | Agent Loop 等待和唤醒计时观测 |

说明性能数字会随 QEMU 和宿主机负载波动。答辩时应强调相对趋势和设计原因：减少 syscall 次数、减少重复查询、减少线性扫描。

最后看到：

```text
agentbench_ucore: passed
agentbench_ucore: parent passed
```

## 9. 场景演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
```

### 9.1 场景设定

一个实验流水线有多个阶段：

- prepare；
- align；
- analyze；
- report；
- archive。

系统中有一个 orchestrator 控制 Agent，以及三个业务 Agent：

| 角色 | 职责 |
| --- | --- |
| orchestrator | 初始化文件元数据、创建业务 Agent、注入失败事件 |
| sentinel | 发现失败事件 |
| investigator | 分析失败原因和影响范围 |
| recovery | 执行恢复动作 |

### 9.2 讲解流程

1. 普通 init 只创建 orchestrator。
2. orchestrator 初始化文件元数据并创建三个业务 Agent。
3. sentinel 监听 `status=failed`。
4. orchestrator 注入 align 阶段失败。
5. sentinel 收到事件并查询文件索引。
6. sentinel 读取内核给出的 metadata 预取提示。
7. sentinel 尝试恢复但权限不足，被内核拒绝。
8. sentinel 发送普通 investigate 消息，内核在 message 入队时把 sentinel 的预取提示交接给 investigator。
9. investigator 查询 align 摘要和依赖，确认影响范围。
10. investigator 从自己的预取提示 snapshot 中读取带 `HANDOFF` 原因位的 analyze 提示，并从当前 span 的全局提示总线确认 source/target pid。
11. investigator 查询当前 span 的系统级短记录，确认 Context、事件和预取交接摘要已经进入内核记录。
12. investigator 输出模板 LLM 解释事件和恢复计划事件，预留最终 LLM Gateway 和 Planner/Auditor 拆分入口。
13. investigator 输出 Context Snapshot，展示决策过程中的可审计记录。
14. investigator 唤醒 recovery。
15. recovery 通过权限检查并执行恢复。
16. recovery 重复执行同一恢复动作，内核识别为 duplicate。
17. recovery 写报告状态并输出带 corr_id 的 report 事件。
18. 最终查询报告文件，系统输出 recovered。
19. orchestrator 查询全局审计短记录，确认三个业务 Agent 的 Context、事件、调度和预取交接摘要都可见。
20. orchestrator 按 kind、span、文件状态事件、预取 source/target 和起始 sequence 过滤查询全局短记录。

### 9.3 关键输出

```text
agentos:event type=RUN_OBJECT tick=... project=lab-gene-x workflow=nightly-regression run_id=RUN-042 desired_state=RECOVERED
agentos:event type=WATCH_REGISTERED tick=... role=sentinel event=FILE_STATUS filter=status=failed
agentos:event type=INCIDENT_CREATED tick=... id=INC-RUN-042-ALIGN-OOM project=lab-gene-x run_id=RUN-042 stage=align reason=memory_limit
labdemo_ucore: sentinel event payload=status=failed;stage=align;run_id=RUN-042;project=lab-gene-x
agentos:event type=TOOL_CALL tick=... role=sentinel tool=query_file project=lab-gene-x run_id=RUN-042 status=failed hits=1 used_index=1
agentos:event type=PREFETCH_HINT tick=... role=sentinel project=lab-gene-x run_id=RUN-042 source_stage=align next_stage=analyze source_seq=4 candidates=1 reason=15
agentos:event type=AUDIT tick=... role=sentinel action=action_commit result=DENIED reason=capability corr_id=RUN-042-align-rerun-1
labdemo_ucore: investigator handoff_prefetch stage=analyze source_seq=4 reason=31
labdemo_ucore: investigator span_prefetch stage=analyze count=... source_pid=... target_pid=...
labdemo_ucore: investigator span_trace records=... context=1 event=1 prefetch=1
agentos:event type=MESSAGE tick=... from=sentinel to=investigator status=OK corr_id=MSG-RUN-042-S-I prefetch_handoff=analyze
agentos:event type=PREFETCH_USED tick=... role=investigator stage=analyze summary=analysis waits for align seq=6
labdemo_ucore: investigator digest bytes=27 preview=align memory_limit evidence seq=4
agentos:event type=TOOL_CALL tick=... role=investigator tool=read_file_digest stage=align status=OK bytes=27 seq=4
agentos:event type=LLM_CALL tick=... mode=template task=explain_root_cause llm_request_id=LLM-RUN-042-RCA-1 refs=3,4,5,6 status=OK
agentos:event type=PLAN_CREATED tick=... role=investigator plan=PLAN-RUN-042-RECOVER-1 actions=align,analyze,report skip=prepare prefetch=analyze refs=3,4,5,6
agentos:event type=CONTEXT_SNAPSHOT tick=... role=investigator records=6 latest=6
agentos:event type=ACTION tick=... role=recovery label=align status=OK corr_id=RUN-042-align-rerun-1 plan=PLAN-RUN-042-RECOVER-1
agentos:event type=AUDIT tick=... role=recovery action=commit_align result=DUPLICATE corr_id=RUN-042-align-rerun-1 plan=PLAN-RUN-042-RECOVER-1
agentos:event type=ARTIFACT tick=... role=recovery file=RUN-042-recovery.md status=OK corr_id=RUN-042-report-write-1 plan=PLAN-RUN-042-RECOVER-1
agentos:event type=FINAL tick=... status=RECOVERED plan=PLAN-RUN-042-RECOVER-1
labdemo_ucore: global_audit=1 records=... agents=3 context=1 event=1 sched=1 prefetch=1
labdemo_ucore: audit_query=1 kind=... span=... event=2 prefetch=... start=...
labdemo_ucore: unified_timeline records=... context=1 event=1 sched=1 prefetch=1 digest=1
labdemo_ucore: timeline_query prefetch=3 cursor=... digest=1
labdemo_ucore: provenance_graph edges=... message=1 prefetch=1 digest=1
labdemo_ucore: passed
labdemo_ucore: parent passed
```

### 9.4 讲解重点

| 输出 | 说明 |
| --- | --- |
| `WATCH_REGISTERED` | Agent Loop 注册成功 |
| `INCIDENT_CREATED` | 文件状态变化触发事件 |
| `TOOL_CALL ... query_file` | Agent 使用文件元数据索引查询失败工件 |
| `PREFETCH_HINT` | 内核根据历史查询和对象标签依赖提示后续可能需要的 metadata |
| `handoff_prefetch` | message 入队时内核把发送者的提示复制到接收者，接收者从自己的 snapshot 读取 |
| `span_prefetch` | 接收者按当前 span 查询全局提示总线，看到提示来源和接收者 |
| `PREFETCH_USED` | investigator 把提示转化为实际摘要读取动作 |
| `LLM_CALL` | 当前使用模板模式预留最终 LLM Gateway 输入输出契约 |
| `PLAN_CREATED` | 恢复计划使用稳定 plan id，并把 align、analyze、report 放入用户态动作序列 |
| `DENIED` | 内核权限检查生效，sentinel 不能直接恢复 |
| `MESSAGE` | Agent 间通过内核事件通信 |
| `CONTEXT_SNAPSHOT` | investigator 的判断过程进入 Context Path |
| `ACTION ... corr_id=...` | recovery 执行带幂等 ID 的通用动作 |
| `DUPLICATE` | 幂等表拒绝重复动作 |
| `ARTIFACT` | recovery 更新报告工件状态，后续可接 LLM 报告润色 |
| `FINAL status=RECOVERED` | 场景完成 |
| `global_audit=1` | orchestrator 能读取内核全局短记录，表示多 Agent 的 Context、事件、调度和预取交接摘要被统一保存 |
| `audit_query=1` | orchestrator 能按条件过滤全局短记录，表示审计数据不需要只靠串口日志人工检索 |
| `run_ledger=1` | orchestrator 能读取全局短记录摘要、分类计数和链尾 hash |
| `unified_timeline` | orchestrator 能用同一种 record 读取 Context、事件、调度、预取交接和内容证据摘要，适合接最终演示页面 |
| `timeline_query` | orchestrator 能按 source、kind、source pid、target pid、tool id 和 flags 精确读取 prefetch handoff 与 digest 证据，也能按上一条已读记录继续读取 timeline |
| `timeline_wait` | Agent 可以睡眠等待新的匹配 timeline 记录，适合最终页面和 worker 做事件驱动刷新 |
| `provenance_graph` | orchestrator 能直接看到 sentinel 到 investigator 的 message、prefetch 触发关系和 investigator 的 digest 证据边 |

## 10. 结尾总结

本项目在 uCore 上实现了 Agent 进程、工具调用、Context Path、文件元数据索引和 Agent Loop。最终场景把这些功能组合成一个完整的内核级 Agent 协作系统，并由 orchestrator 读取和过滤全局短记录说明多 Agent 协作过程，而不是只停留在分散 syscall 的层面。

补充安全验证可运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsecurity_ucore CHAPTER=agent
```

该程序覆盖普通进程 mail 最小路径；普通进程不能直接投递事件、取消 Agent 等待或修改 Agent 文件元数据；usershell 等价路径可以创建 orchestrator；初始化前索引查询不会卡住；legacy 工具 ID/名称不一致会失败；sentinel 也不能通过伪造 `AGENT_ROLE_RECOVERY` 获得动作权限；recovery 只会更新 selector 指定的 run。

当前版本已经具备任务一至三的增强实现，完成任务四的真实 inode 关联文件元数据服务、索引查询和根目录自动扫描，完成任务五的有界事件队列、等待/唤醒/取消机制、Agent 感知调度、受权调度配置、调度原因记录、当前 span 短记录、统一 timeline、timeline 过滤查询、timeline 游标增量读取、全局审计短记录和过滤查询，并提供任务六综合演示。后续可以继续增强多级目录递归扫描、云端 LLM Gateway 和可视化大屏。
