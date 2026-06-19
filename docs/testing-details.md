# 测试内容详细说明

本文档解释当前 uCore 分支最终测试程序的内部步骤、覆盖范围和预期输出。测试入口和运行命令见 [verification.md](verification.md)。

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
11. 子进程检查第 8 条记录的 payload/result 短文本为 `ucore-final`。
12. 子进程调用 `context_detail()`，检查完整 `agent_op`、完整 `agent_result` 和 `SYSTEM` flag。
13. 子进程手动篡改用户态 Context 镜像中的第一条记录 sequence。
14. 子进程再次调用 `context_snapshot()`。
15. 子进程检查 snapshot 返回的第一条记录仍为原始 sequence，并检查用户镜像被刷新。
16. 子进程向 `header.user_cache_offset` 写入 `cache-ok`，再次调用 `context_snapshot()` 后检查 cache 内容仍保留。
17. 子进程调用 `context_push()` 追加手动记录，检查 `MANUAL` flag 和 detail ring。
18. 子进程继续批量写入 128 条记录，使总记录达到 193 条。
19. 子进程再次 snapshot，检查 FIFO 淘汰：
   - count 为 128；
   - oldest 为 66；
   - latest 为 193；
   - dropped 为 65。
20. 子进程调用 `agent_file_meta_init()` 初始化文件元数据。
21. 子进程按 `project=lab-gene-x`、`run_id=RUN-042`、`stage=align` 查询文件。
22. 子进程检查查询命中，且 `used_index == 1`。
23. 子进程使用只提供 `tool_name` 的 `agent_call()` 依次验证 `echo`、`query_file`、`pid_info`。
24. 子进程注册 message watch。
25. 子进程用 `agent_wake()` 向自己投递事件。
26. 子进程调用 `agent_wait()`，检查成功收到 `self wake`。
27. 子进程输出 `agentfinal_ucore: passed` 并退出。
28. 父进程等待子进程退出，检查退出状态为 0，输出 `agentfinal_ucore: parent passed`。

### 1.2 关键输出

```text
agentfinal_ucore: context size=20480 capacity=128
agentfinal_ucore: batch first_seq=1 last_seq=64
agentfinal_ucore: short_text_history=1 payload=ucore-final result=ucore-final
agentfinal_ucore: context_detail=1 sequence=8
agentfinal_ucore: tamper_protected=1
agentfinal_ucore: user_cache_preserved=1 offset=17408 size=3072
agentfinal_ucore: record_flags system=1 manual=1 truncated=0
agentfinal_ucore: legacy_name_protocol=1
agentfinal_ucore: fifo oldest=66 latest=193 dropped=65
agentfinal_ucore: file_query hits=2 scanned=2 used_index=1
agentfinal_ucore: event_wait=1 payload=self wake
agentfinal_ucore: passed
agentfinal_ucore: parent passed
```

### 1.3 覆盖结论

| 覆盖点 | 证明方式 |
| --- | --- |
| Agent 创建 | pid 1 父进程创建 orchestrator Agent 子进程 |
| Agent PCB 字段 | `agent_info()` 返回 Agent 状态、真实 role、capability 和 Context 信息 |
| Agent Context 映射 | 直接读取 header 和 latest result |
| 批量工具调用 | 一次 `agent_run()` 执行 64 个 echo op |
| Context Path 写入 | snapshot 返回 64 条记录 |
| 短文本历史 | payload/result 短摘要可查询 |
| 完整详情 | `context_detail()` 返回完整 op/result 和 flags |
| shadow 防篡改 | 用户镜像被写坏后，snapshot 仍返回权威历史 |
| 用户自管 cache | snapshot 不覆盖 `user_cache_offset` 之后的 cache 内容 |
| 名称协议 | name-only `agent_call()` 可调用 echo、query_file、pid_info |
| 手动记录 | `context_push()` 记录 `MANUAL` flag |
| FIFO 淘汰 | 193 条记录后只保留 128 条，oldest/latest/dropped 正确 |
| 文件索引 | `agent_file_query()` 使用索引路径 |
| Agent 事件 | watch/wake/wait 自唤醒成功 |
| 特权 Agent 能力 | orchestrator 能初始化文件元数据并向自身投递事件 |

## 2. `agentfs_ucore`

`agentfs_ucore` 是任务四文件系统能力测试，重点证明 Agent 文件元数据不只是内存演示表，而是能绑定真实根目录文件 inode，并写入和重新加载私有 `.agentmeta` 元数据文件。

### 2.1 测试流程

1. 父进程创建 orchestrator Agent 子进程。
2. 子进程调用 `agent_file_meta_init()`，加载或创建默认真实演示文件。
3. 子进程查询默认 `RUN-042` 文件，检查返回项包含真实 `dev`、`inum` 和 `size`。
4. 子进程创建自定义真实文件，写入内容。
5. 子进程用 `agent_file_meta_set()` 将自定义逻辑属性绑定到该真实文件。
6. 子进程查询自定义文件，检查 `dev + inum` 和文件大小与真实文件一致。
7. 子进程写入接近 128 条真实文件元数据，制造足够的数据量。
8. 子进程分别运行扫描查询和索引查询，检查索引路径的 `scanned_records` 明显更少。
9. 子进程清空某条记录的 status，确认属性更新生效。
10. 子进程删除绑定文件，确认关联元数据随文件删除被清理。
11. 子进程再次调用 `agent_file_meta_init()`，确认自定义元数据来自 `.agentmeta` 重新加载，没有被默认表覆盖。
12. 子进程调用 `write_report` 指向不存在的 selector，确认返回 `AGENT_STATUS_NOT_FOUND`。

### 2.2 关键输出

```text
agentfs_ucore: default_inode dev=1 inum=11 scanned=2
agentfs_ucore: custom_inode dev=1 inum=17 size=7
agentfs_ucore: bulk_index scan=108 index=6 hits=1
agentfs_ucore: scan_index_consistent=1
agentfs_ucore: truncated_query total=100 returned=3 truncated=1
agentfs_ucore: .agentmeta_reload=1
agentfs_ucore: clear_status=1
agentfs_ucore: delete_clears_metadata=1
agentfs_ucore: missing_selector_not_found=1
agentfs_ucore: passed
agentfs_ucore: parent passed
```

### 2.3 覆盖结论

| 覆盖点 | 证明方式 |
| --- | --- |
| 真实 inode 绑定 | 查询结果返回 `dev`、`inum`、`size` |
| `.agentmeta` 可写入和重新加载 | 自定义元数据重新初始化后仍存在 |
| scan/index 差异 | 接近 128 条记录下输出 `bulk_index scan=108 index=6` |
| 属性删除 | 清空 status 后查询行为符合预期 |
| 文件删除同步 | 删除真实文件后关联元数据被清理 |
| 未命中 selector | `write_report` 对不存在目标返回 `AGENT_STATUS_NOT_FOUND` |

## 3. `agentloop_ucore`

`agentloop_ucore` 是任务五事件运行机制测试，重点证明 FIFO 事件队列、watch/unwatch、有限 timeout 睡眠、TIMER unwatch 和 heartbeat stop 都可运行。

### 3.1 测试流程

1. 父进程创建 orchestrator Agent 子进程。
2. 子进程注册 message watch。
3. 子进程连续投递多个事件，调用 `agent_wait()` 检查 FIFO 顺序。
4. 子进程填满 16 槽事件队列，再尝试投递第 17 个事件，确认返回 `AGENT_STATUS_NO_SPACE` 且旧事件没有被覆盖。
5. 子进程删除 watch，再投递相同事件，确认不会唤醒。
6. 子进程重新注册 watch，调用有限 timeout wait，确认线程进入睡眠并由 timeout 唤醒，且 `wait_loop_count` 增量很小。
7. 子进程注册 TIMER watch，启动 heartbeat，确认 heartbeat 事件可唤醒等待。
8. 子进程删除 TIMER watch 后再次启动 heartbeat，确认不会消费 TIMER 事件。
9. 子进程调用 `agent_heartbeat_stop()`，确认停止后不再产生 heartbeat 事件。

### 3.2 关键输出

```text
agentloop_ucore: fifo=1
agentloop_ucore: overflow_dropped=1
agentloop_ucore: unwatch=1
agentloop_ucore: timeout_sleep_no_poll=1
agentloop_ucore: timer_unwatch=1
agentloop_ucore: heartbeat_wake_stop=1
agentloop_ucore: passed
agentloop_ucore: parent passed
```

### 3.3 覆盖结论

| 覆盖点 | 证明方式 |
| --- | --- |
| FIFO 顺序 | 多事件按投递顺序被消费 |
| 队列满语义 | 满队列返回 `AGENT_STATUS_NO_SPACE`，旧事件不被覆盖 |
| watch 删除 | `agent_unwatch()` 后相同事件不再匹配 |
| timeout 睡眠 | 有限 timeout wait 返回 `AGENT_STATUS_TIMEOUT`，并用 `wait_loop_count` 证明没有反复轮询 |
| heartbeat 唤醒 | 注册 TIMER watch 后可收到 heartbeat 事件 |
| TIMER watch 删除 | 删除 TIMER watch 后 heartbeat 不再唤醒等待 |
| heartbeat 停止 | stop 后不再继续产生 heartbeat 事件 |

## 4. `agentbench_ucore`

`agentbench_ucore` 是性能和吞吐测试。它不使用固定耗时阈值，而是输出可对比的 tick 统计。

benchmark 主进程通过 `agent_create_role(AGENT_ROLE_ORCHESTRATOR)` 创建 orchestrator Agent，文件元数据初始化、文件查询、事件投递等需要权限的操作都在 orchestrator 内执行。wait/wake 子测试中的 waiter Agent 使用最低权限 sentinel role。

### 4.1 测试项目

| 项目 | 操作数 | 测试内容 |
| --- | ---: | --- |
| `scalar_agent_run` | 256 | 每次 syscall 执行 1 个 echo op |
| `batch_agent_run` | 256 | 每次 syscall 执行 64 个 echo op |
| `direct_context` | 5000 | 用户态直接读取 Context header 的 latest sequence |
| `context_query` | 16 | 每次 syscall 查询 1 条 Context record |
| `context_snapshot` | 2048 | 多次 snapshot，每次最多返回 128 条记录，按返回记录数计数 |
| `file_scan_query` | 64 | 强制扫描文件元数据表 |
| `file_index_query` | 64 | 使用文件元数据索引路径 |
| `timeout_heartbeat` | 1 | 验证无事件等待返回 timeout，且 heartbeat 字段可通过 `agent_info()` 观察 |
| `busy_poll_query` | 128 | 模拟用户态持续查询无事件条件，作为轮询路径计时观测 |
| `event_wait_wake` | 8 | 父进程多次唤醒等待中的 Agent 子进程，并输出计时观测 |

### 4.2 输出字段

| 字段 | 含义 |
| --- | --- |
| `ops` | 执行的逻辑操作数量 |
| `ticks` | 消耗的内核 tick 数；最小按 1 处理，避免除 0 |
| `ops_per_tick` | 每 tick 完成的操作数 |
| `speedup_x100` | 相对基线放大 100 倍后的速度比 |

### 4.3 当前样例输出

```text
agentbench_ucore: timeout_heartbeat=1
agentbench_ucore: repeated_ticks scalar_min=5 scalar_avg=5 scalar_max=6 batch_min=3 batch_avg=3 batch_max=4
agentbench_ucore: file_query_records scan_records=107 index_records=6
agentbench_ucore: case ops ticks ops_per_tick speedup_x100
agentbench_ucore: scalar_agent_run ops=256 ticks=5 ops_per_tick=51 speedup_x100=100
agentbench_ucore: batch_agent_run ops=256 ticks=3 ops_per_tick=85 speedup_x100=166
agentbench_ucore: direct_context ops=5000 ticks=1 ops_per_tick=5000 speedup_x100=9765
agentbench_ucore: context_query ops=16 ticks=1 ops_per_tick=16 speedup_x100=100
agentbench_ucore: context_snapshot ops=2048 ticks=3 ops_per_tick=682 speedup_x100=4266
agentbench_ucore: file_scan_query ops=64 ticks=5 ops_per_tick=12 speedup_x100=100
agentbench_ucore: file_index_query ops=64 ticks=2 ops_per_tick=32 speedup_x100=300
agentbench_ucore: busy_poll_query ops=128 ticks=5 ops_per_tick=25 speedup_x100=100
agentbench_ucore: event_wait_wake ops=8 ticks=3 ops_per_tick=2 speedup_x100=100
agentbench_ucore: busy_poll_vs_wait busy_ops=128 busy_ticks=5 wait_ops=8 wait_ticks=3
agentbench_ucore: passed
agentbench_ucore: parent passed
labbench_ucore: parent passed
```

### 4.4 性能解释

| 对比 | 设计含义 |
| --- | --- |
| `batch_agent_run` vs `scalar_agent_run` | 批量 syscall 减少陷入内核次数 |
| `direct_context` vs syscall 查询 | 用户态镜像适合高频读最新状态 |
| `context_snapshot` vs `context_query` | 批量历史查询减少多次 syscall 和多次遍历 |
| `file_index_query` vs `file_scan_query` | 文件元数据索引减少候选记录检查，`file_query_records` 直接输出候选记录数差异 |
| `timeout_heartbeat` | Agent Loop 的超时和心跳字段有直接断言，不只依赖场景日志 |
| `busy_poll_query` / `event_wait_wake` | Agent Loop 不只是功能演示，也能输出轮询路径和等待唤醒路径的计时观测 |

tick 数值随环境波动，阅读性能数据时应结合多轮 min/avg/max、候选记录数和设计解释看相对趋势。

## 5. `labdemo_ucore`

`labdemo_ucore` 是面向答辩的最终场景测试。它把底层能力串成一个可解释的多 Agent 工作流。

### 5.1 场景设定

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

### 5.2 流程

1. 普通 init 调用 `agent_create_role(AGENT_ROLE_ORCHESTRATOR)` 创建 orchestrator。
2. orchestrator 调用 `agent_file_meta_init()` 安装演示文件元数据。
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
13. sentinel 通过消息唤醒 investigator。
14. investigator 查询 align 文件摘要，得到故障原因。
15. investigator 查询 dependency，得到影响阶段。
16. investigator 输出模板 `LLM_CALL` / `LLM_RESULT` 事件和 `PLAN_CREATED` 事件，预留最终 LLM Gateway 和 Planner/Auditor 拆分入口。
17. investigator 调用 `context_snapshot()` 展示自身审计历史。
18. investigator 通过消息唤醒 recovery。
19. recovery 通过 capability 检查。
20. recovery 执行 `rerun_stage align`。
21. recovery 再次执行同一动作，内核返回 duplicate。
22. recovery 写报告并查询 report 文件。
23. recovery 输出带 `corr_id` 和 plan id 的 `AUDIT`、`ACTION`、`REPORT` 和 `FINAL` 事件。
24. orchestrator 等待三个角色 Agent 退出，输出 `labdemo_ucore: passed`。
25. 普通 init 等待 orchestrator 退出，输出 `labdemo_ucore: parent passed`。

### 5.3 关键输出

```text
agentos:event type=RUN_OBJECT tick=... project=lab-gene-x workflow=nightly-regression run_id=RUN-042 desired_state=RECOVERED policy=minimal_rerun
agentos:event type=WATCH_REGISTERED tick=... role=sentinel event=FILE_STATUS filter=status=failed
agentos:event type=INCIDENT_CREATED tick=... id=INC-RUN-042-ALIGN-OOM project=lab-gene-x workflow=nightly-regression run_id=RUN-042 stage=align reason=memory_limit
labdemo_ucore: sentinel event payload=status=failed;stage=align;run_id=RUN-042;project=lab-gene-x
agentos:event type=TOOL_CALL tick=... role=sentinel tool=query_file project=lab-gene-x run_id=RUN-042 status=failed hits=1 used_index=1 seq=...
agentos:event type=AUDIT tick=... role=sentinel action=rerun_stage result=DENIED reason=capability corr_id=RUN-042-align-rerun-1 seq=...
agentos:event type=MESSAGE tick=... from=sentinel to=investigator status=OK corr_id=MSG-RUN-042-S-I seq=...
labdemo_ucore: investigator reason=align output is ready before injected failure
labdemo_ucore: affected stages=align+analyze+report+archive
agentos:event type=LLM_CALL tick=... mode=template task=explain_root_cause llm_request_id=LLM-RUN-042-RCA-1 project=lab-gene-x run_id=RUN-042 refs=... status=OK
agentos:event type=LLM_RESULT tick=... mode=template llm_request_id=LLM-RUN-042-RCA-1 llm_status=OK llm_explanation=memory_limit referenced_sequences=... confidence=medium
agentos:event type=PLAN_CREATED tick=... role=investigator plan=PLAN-RUN-042-RECOVER-1 project=lab-gene-x run_id=RUN-042 actions=align,report skip=prepare refs=...
agentos:event type=CONTEXT_SNAPSHOT tick=... role=investigator records=4 latest=...
agentos:event type=MESSAGE tick=... from=investigator to=recovery status=OK corr_id=MSG-RUN-042-I-R plan=PLAN-RUN-042-RECOVER-1 seq=...
agentos:event type=ACTION tick=... role=recovery stage=align status=OK corr_id=RUN-042-align-rerun-1 plan=PLAN-RUN-042-RECOVER-1 seq=... duplicate=0
agentos:event type=AUDIT tick=... role=recovery action=rerun_align result=DUPLICATE corr_id=RUN-042-align-rerun-1 plan=PLAN-RUN-042-RECOVER-1 seq=...
agentos:event type=REPORT tick=... role=recovery project=lab-gene-x run_id=RUN-042 file=RUN-042-recovery.md status=OK corr_id=RUN-042-report-write-1 plan=PLAN-RUN-042-RECOVER-1 seq=... llm_enhanced=0
agentos:event type=FINAL tick=... project=lab-gene-x run_id=RUN-042 status=RECOVERED plan=PLAN-RUN-042-RECOVER-1
labdemo_ucore: passed
```

### 5.4 覆盖结论

| 覆盖点 | 证明方式 |
| --- | --- |
| 多 Agent 并存 | 同时创建 sentinel、investigator、recovery |
| 控制面权限 | 普通 init 只启动 orchestrator；元数据初始化、失败注入和角色 Agent 创建都由 orchestrator 完成 |
| 文件状态事件 | orchestrator 注入 failed 状态后唤醒 sentinel |
| 文件属性查询 | sentinel 查询失败工件 |
| 依赖查询 | investigator 查询 align 的影响范围 |
| 权限控制 | sentinel 恢复动作被拒绝 |
| Agent 间通信 | sentinel 唤醒 investigator，investigator 唤醒 recovery |
| Context 审计 | investigator 输出 snapshot |
| 幂等恢复 | recovery 第二次 rerun 返回 duplicate |
| 最终状态 | 输出 `FINAL status=RECOVERED` |

## 6. `agentsecurity_ucore`

`agentsecurity_ucore` 是权限限制负向测试，专门覆盖审阅中指出的“普通进程能直接改全局元数据或伪造事件”“用户态自报 role 可绕过权限”的问题。

### 6.1 测试流程

1. 普通 init 验证 `mailread()` 无消息返回 0，`mailwrite()` 写入自己成功，随后 `mailread()` 能读回相同内容。
2. 普通 init 调用 `agent_wake()`，预期返回 `-1`。
3. 普通 init 调用 `agent_file_meta_init()`，预期返回 `-1`。
4. 普通 init 调用 `agent_file_meta_set()`，预期返回 `-1`。
5. 普通 init 直接 `open(".agentmeta")`、`open(".agentmeta", O_CREATE)`、`unlink(".agentmeta")`，预期均返回 `-1`。
6. 普通 init 创建一个普通子进程，子进程作为 usershell 等价路径创建 orchestrator Agent。
7. orchestrator 子 Agent 通过 `agent_info()` 检查真实 role 和 capability mask。
8. 普通 init 创建主 orchestrator Agent。
9. orchestrator 在未初始化元数据前执行带索引查询，预期返回 0 条命中且不会阻塞。
10. orchestrator 初始化文件元数据。
11. orchestrator 使用 legacy `agent_call()` 传入不一致的 `tool_id` 和 `tool_name`，预期返回 `AGENT_STATUS_BAD_REQUEST` 和 `tool_mismatch`。
12. orchestrator 分别把 `RUN-042` 和 `RUN-999` 的 align、report 阶段置为 failed。
13. orchestrator 创建 sentinel Agent。
14. sentinel 检查真实 role/capability mask。
15. sentinel 把 `agent_op.arg0` 伪造成 `AGENT_ROLE_RECOVERY` 后调用 `capability_check("rerun_stage")`，预期仍返回 `AGENT_STATUS_DENIED`。
16. sentinel 继续伪造 recovery 调用 `rerun_stage align`，预期返回 `AGENT_STATUS_DENIED`。
17. sentinel 继续伪造 recovery 调用 `write_report`，预期返回 `AGENT_STATUS_DENIED`。
18. sentinel 直接调用 `agent_file_meta_set()`，预期返回 `AGENT_STATUS_DENIED`。
19. sentinel 查询 `RUN-042` 和 `RUN-999` 状态仍为 failed，证明拒绝路径没有改变文件状态。
20. orchestrator 创建 recovery Agent。
21. recovery 检查真实 role/capability mask。
22. recovery 调用 `rerun_stage`，payload 使用 `stage=align;run_id=RUN-999;project=lab-gene-x` 定向选择目标 run；即使 `agent_op.arg0` 填成 sentinel，也按真实 recovery role 成功。
23. recovery 使用同一 corr_id 再次调用同一 selector，预期返回 `AGENT_STATUS_DUPLICATE`。
24. recovery 调用 `write_report`，payload 使用 `stage=report;run_id=RUN-999;project=lab-gene-x`，只写入目标 report。
25. orchestrator 查询 `RUN-999` 的 align 和 report 变为 ok，`RUN-042` 仍为 failed，证明恢复和报告写入没有跨 run 修改。
26. 测试输出 `agentsecurity_ucore: passed` 和 `agentsecurity_ucore: parent passed`。

### 6.2 关键输出

```text
agentsecurity_ucore: mail_basic=1
agentsecurity_ucore: plain_process_denied=1
agentsecurity_ucore: .agentmeta_protected=1
agentsecurity_ucore: role=orchestrator_child capability_checked=1
agentsecurity_ucore: plain_child_orchestrator=1
agentsecurity_ucore: role=orchestrator capability_checked=1
agentsecurity_ucore: preinit_index_query=1
agentsecurity_ucore: legacy_tool_mismatch=1
agentsecurity_ucore: legacy_param_validation=1 syscall_only=1
agentsecurity_ucore: role=sentinel capability_checked=1
agentsecurity_ucore: sentinel spoof_denied=1
agentsecurity_ucore: role=recovery capability_checked=1
agentsecurity_ucore: recovery rerun_ok=1 duplicate=1
agentsecurity_ucore: scoped_rerun=1
agentsecurity_ucore: scoped_report=1
agentsecurity_ucore: passed
agentsecurity_ucore: parent passed
```

### 6.3 覆盖结论

| 覆盖点 | 证明方式 |
| --- | --- |
| 普通进程不能直接投递事件 | `agent_wake()` 返回 `-1` |
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
| 重复恢复被识别 | 相同 corr_id 第二次 rerun 返回 duplicate |
| 多 run 恢复和报告写入不会误伤 | 只恢复和写入 selector 指定的 `RUN-999`，`RUN-042` 保持 failed |

## 7. 运行方式和复现建议

推荐用脚本运行完整测试：

```bash
bash scripts/run-agent-tests.sh
```

如果需要单独复现某一项：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfs_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentloop_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsecurity_ucore CHAPTER=agent
```

不要在同一工作树中并行启动多个 QEMU 测试，因为 `nfs/fs-copy.img` 会被多个进程同时访问，可能造成镜像锁冲突。
