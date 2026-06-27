# 测试记录

测试分支：`uCore`

测试环境：

- WSL2 Ubuntu；
- QEMU riscv64；
- `riscv64-linux-gnu-` 工具链。

## 构建

执行：

```bash
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent
make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore
```

结果：通过。

## 完整脚本

执行：

```bash
bash scripts/run-agent-tests.sh
```

结果：通过。脚本依次运行 `agentfinal_ucore`、`agentfs_ucore`、`agentscan_ucore`、`agentloop_ucore`、`agentsched_ucore`、`agentbench_ucore`、`labbench_ucore`、`labdemo_ucore`、`agentsecurity_ucore`，均找到对应 `parent passed` 标记，且日志中没有 `check failed`、`panic` 或 `unknown syscall`。

## agentfinal_ucore 样例输出

```text
agentfinal_ucore: Agent-OS on uCore final verification
agentfinal_ucore: context size=24576 capacity=128
agentfinal_ucore: batch first_seq=1 last_seq=64
agentfinal_ucore: short_text_history=1 payload=ucore-final result=ucore-final
agentfinal_ucore: context_detail=1 sequence=8
agentfinal_ucore: tamper_protected=1
agentfinal_ucore: causal_context=1 first_cause=0 next_cause=1 span=1 edges=63
agentfinal_ucore: context_integrity=1 first_hash=... latest_hash=...
agentfinal_ucore: user_cache_preserved=1 offset=21504 size=3072
agentfinal_ucore: record_flags system=1 manual=1 truncated=0
agentfinal_ucore: legacy_name_protocol=1
agentfinal_ucore: fifo oldest=66 latest=193 dropped=65
agentfinal_ucore: file_query hits=2 scanned=2 used_index=1
agentfinal_ucore: prefetch_hints=1 count=... first_stage=analyze
agentfinal_ucore: span_prefetch=1 count=... first_stage=analyze
agentfinal_ucore: event_wait=1 payload=self wake
agentfinal_ucore: runtime_trace=1 records=... context=1 sched=1 wait=1
agentfinal_ucore: span_trace=1 records=... context=1 event=1
agentfinal_ucore: unified_timeline=1 records=... context=1 sched=1 audit=1 prefetch=1
agentfinal_ucore: timeline_query=1 audit=213 recent=281 cursor=177
agentfinal_ucore: timeline_wait=1 timeout=-7 source_gate=1 event_gate=1 wake=1 query=1 read=1 sleeps=1 wakeups=1
agentfinal_ucore: provenance_graph=1 edges=126 context=1 audit=1
agentfinal_ucore: run_ledger=1 records=... hash=... context=... event=... sched=... prefetch=...
agentfinal_ucore: passed
agentfinal_ucore: parent passed
```

结论：Agent 创建、Context 映射、batch 调用、短文本历史、完整 detail 查询、运行轨迹查询、当前 span 短记录查询、统一 timeline、timeline 过滤查询、timeline 游标增量读取、Run Ledger 摘要、cause/span 因果链、篡改保护、用户自管 cache、名称协议、手动/系统记录区分、FIFO 淘汰、文件索引、本地预取提示、span 预取提示和事件等待均通过。

## agentfs_ucore 样例输出

```text
agentfs_ucore: Agent FS metadata test
agentfs_ucore: default_inode dev=1 inum=14 scanned=2
agentfs_ucore: prefetch_hints=1 count=3 first_stage=analyze source_seq=1
agentfs_ucore: custom_inode dev=1 inum=20 size=7
agentfs_ucore: content_digest=1 size=7 bytes=7 hash=52642947 preview=agentfs
agentfs_ucore: digest_cache=1 hits=1 misses=1
agentfs_ucore: digest_cache_invalidated=1 misses=1
agentfs_ucore: digest_timeline=1 tool=20 preview=agentfs2
agentfs_ucore: .agentmeta_reload=1
agentfs_ucore: query_cache=1 reason=68
agentfs_ucore: bulk_index scan=118 index=6 hits=1
agentfs_ucore: query_plan scan_plan=0 index_plan=1 reason=4 bucket=15 candidates=6
agentfs_ucore: scan_index_consistent=1
agentfs_ucore: truncated_query total=100 returned=3 truncated=1
agentfs_ucore: clear_status=1 cache_invalidated=1
agentfs_ucore: delete_clears_metadata=1
agentfs_ucore: missing_selector_not_found=1
agentfs_ucore: passed
agentfs_ucore: parent passed
```

结论：文件元数据可绑定真实根目录文件 inode，查询结果包含 `dev`、`inum`、`size`；默认 align 查询后会产生后续阶段 metadata 预取提示；自定义 metadata 可从私有 `.agentmeta` 重新加载；真实文件内容摘要可以被缓存，改写文件后旧 digest 缓存不会返回过期内容；内容摘要工具调用会进入统一 timeline，页面可按 `tool_id=20` 读取 size、bytes、hash 和 preview；接近 128 条记录时 scan/index 的 `scanned_records` 差异可见；query plan 能说明索引路径按 status 索引选择 bucket 15 并检查 6 条候选记录；重复查询会命中同一 `fs_generation` 下的结果缓存，属性更新后旧缓存不会返回过期结果；属性清空、文件删除同步和不存在 selector 返回 `NOT_FOUND` 均通过。

## agentscan_ucore 样例输出

```text
agentscan_ucore: background file scan test
agentscan_ucore: background_scan usershell=1 runs=1 entries=64 added=10
agentscan_ucore: auto_file_create=1 size=14 generation=19
agentscan_ucore: auto_file_delete=1
agentscan_ucore: passed
agentscan_ucore: parent passed
```

结论：调度器空隙分批扫描 uCore 根目录可发现已有真实文件；普通文件 syscall 创建的新文件能自动进入 Agent 元数据表和索引；删除真实文件后自动元数据会被清理。

## agentloop_ucore 样例输出

```text
agentloop_ucore: Agent event queue test
agentloop_ucore: fifo=1
agentloop_ucore: event_causality=1
agentloop_ucore: overflow_dropped=1
agentloop_ucore: unwatch=1
agentloop_ucore: timeout_sleep_no_poll=1
agentloop_ucore: timer_unwatch=1
agentloop_ucore: heartbeat_wake_stop=1
agentloop_ucore: wait_cancel=1
agentloop_ucore: passed
agentloop_ucore: parent passed
```

结论：16 槽 FIFO 事件队列顺序正确，事件带有 cause/span，队列满时拒绝新事件且不覆盖旧事件；`agent_unwatch()`、有限 timeout 睡眠、wait cancel、TIMER unwatch、heartbeat 唤醒和停止均通过。

## agentsched_ucore 样例输出

```text
agentsched_ucore: adaptive Agent scheduler test
agentsched_ucore: role_weights sentinel=70 investigator=90 recovery=120 orchestrator=110
agentsched_ucore: configurable_policy=1 weight=150 priority=20 budget=3
agentsched_ucore: event_priority=1 dispatch=6 event_dispatch=1
agentsched_ucore: reason_trace=1 records=6 reason=131 score=1655
agentsched_ucore: fairness=1 dispatch=18 preemptions=13 vruntime=162
agentsched_ucore: passed
agentsched_ucore: parent passed
```

结论：不同 Agent 角色拥有不同调度权重；orchestrator 可配置目标 Agent 的 weight、priority 和 budget，非授权调用会被拒绝；有待消费事件的 Agent 会被调度器记录为事件相关调度；最近调度记录可通过 `agent_sched_snapshot()` 查询，并包含事件队列、角色权重、配置优先级、分数、事件数量等原因字段；反复让出处理器后，调度次数、让出处理器次数和虚拟运行量计数均会增长。

## agentbench_ucore 样例输出

```text
agentbench_ucore: Agent-OS on uCore benchmark
agentbench_ucore: timeout_heartbeat=1
agentbench_ucore: repeated_ticks scalar_min=17 scalar_avg=17 scalar_max=18 batch_min=4 batch_avg=5 batch_max=6
agentbench_ucore: file_query_records scan_records=118 index_records=6
agentbench_ucore: file_query_plan scan_plan=0 index_plan=1 index_reason=68 index_candidates=6
agentbench_ucore: file_query_cache hit=1 reason=68
agentbench_ucore: file_digest bytes=37888 ticks=11 preview=agentbench-digest-content-block-0001 agentbench-digest-content-
agentbench_ucore: file_digest_cache hits=63 misses=1
agentbench_ucore: prefetch_records total=192 first_stage=analyze
agentbench_ucore: timeline_records snapshot=8192 query=48 cursor=6568
agentbench_ucore: provenance_records snapshot=2048
agentbench_ucore: timeline_wait_ready records=659 ticks=1
agentbench_ucore: timeline_read_ready records=8192 ticks=651
agentbench_ucore: case ops ticks ops_per_tick speedup_x100
agentbench_ucore: scalar_agent_run ops=256 ticks=17 ops_per_tick=15 speedup_x100=100
agentbench_ucore: batch_agent_run ops=256 ticks=5 ops_per_tick=51 speedup_x100=340
agentbench_ucore: direct_context ops=5000 ticks=1 ops_per_tick=5000 speedup_x100=33203
agentbench_ucore: context_query ops=16 ticks=1 ops_per_tick=16 speedup_x100=100
agentbench_ucore: context_snapshot ops=2048 ticks=5 ops_per_tick=409 speedup_x100=2560
agentbench_ucore: file_scan_query ops=64 ticks=18 ops_per_tick=3 speedup_x100=100
agentbench_ucore: file_index_query ops=64 ticks=7 ops_per_tick=9 speedup_x100=257
agentbench_ucore: file_digest_read ops=37888 ticks=11 ops_per_tick=3444 speedup_x100=100
agentbench_ucore: file_prefetch_snapshot ops=192 ticks=2 ops_per_tick=96 speedup_x100=300
agentbench_ucore: timeline_snapshot ops=8192 ticks=695 ops_per_tick=11 speedup_x100=51200
agentbench_ucore: timeline_query_prefetch ops=48 ticks=1 ops_per_tick=48 speedup_x100=407
agentbench_ucore: timeline_query_cursor ops=6568 ticks=712 ops_per_tick=9 speedup_x100=78
agentbench_ucore: provenance_snapshot ops=2048 ticks=7 ops_per_tick=292 speedup_x100=12800
agentbench_ucore: timeline_wait_ready ops=659 ticks=1 ops_per_tick=659 speedup_x100=100
agentbench_ucore: timeline_read_ready ops=8192 ticks=651 ops_per_tick=12 speedup_x100=100
agentbench_ucore: busy_poll_query ops=128 ticks=10 ops_per_tick=12 speedup_x100=100
agentbench_ucore: event_wait_wake ops=8 ticks=5 ops_per_tick=1 speedup_x100=100
agentbench_ucore: busy_poll_vs_wait busy_ops=128 busy_ticks=10 wait_ops=8 wait_ticks=5
agentbench_ucore: passed
agentbench_ucore: parent passed
labbench_ucore: parent passed
```

结论：batch、direct context 和 snapshot 路径均稳定完成；文件查询输出了 scan/index 候选记录数差异，并给出 query plan 解释索引选择原因；预取提示 snapshot 能在文件查询之后直接返回后续阶段 metadata 提示。tick 数值会随运行环境波动。

## labdemo_ucore 样例输出

```text
labdemo_ucore: Agent-OS laboratory recovery demo
labdemo_ucore: created role=orchestrator pid=2 context=0x0000003ffffe9000
agentos:event type=AGENT_CREATED tick=... role=orchestrator pid=2 context=0x0000003ffffe9000
agentos:event type=RUN_OBJECT tick=... project=lab-gene-x workflow=nightly-regression run_id=RUN-042 desired_state=RECOVERED policy=minimal_rerun
labdemo_ucore: created role=investigator pid=4 context=0x0000003ffffe9000
agentos:event type=AGENT_CREATED tick=... role=investigator pid=4 context=0x0000003ffffe9000
labdemo_ucore: created role=sentinel pid=5 context=0x0000003ffffe9000
agentos:event type=AGENT_CREATED tick=... role=sentinel pid=5 context=0x0000003ffffe9000
agentos:event type=WATCH_REGISTERED tick=... role=sentinel event=FILE_STATUS filter=status=failed
labdemo_ucore: created role=recovery pid=3 context=0x0000003ffffe9000
agentos:event type=AGENT_CREATED tick=... role=recovery pid=3 context=0x0000003ffffe9000
agentos:event type=INCIDENT_CREATED tick=... id=INC-RUN-042-ALIGN-OOM project=lab-gene-x workflow=nightly-regression run_id=RUN-042 stage=align reason=memory_limit
labdemo_ucore: sentinel event payload=status=failed;stage=align;run_id=RUN-042;project=lab-gene-x
agentos:event type=TOOL_CALL tick=... role=sentinel tool=query_file project=lab-gene-x run_id=RUN-042 status=failed hits=1 used_index=1 seq=4
labdemo_ucore: sentinel prefetch_hint stage=analyze source_seq=4 plan=2 candidates=1
agentos:event type=PREFETCH_HINT tick=... role=sentinel project=lab-gene-x run_id=RUN-042 source_stage=align next_stage=analyze source_seq=4 candidates=1 reason=15
agentos:event type=AUDIT tick=... role=sentinel action=rerun_stage result=DENIED reason=capability corr_id=RUN-042-align-rerun-1 seq=5
labdemo_ucore: investigator handoff_prefetch stage=analyze source_seq=4 reason=31
labdemo_ucore: investigator span_prefetch stage=analyze count=... source_pid=... target_pid=...
labdemo_ucore: investigator span_trace records=... context=1 event=1 prefetch=1
agentos:event type=MESSAGE tick=... from=sentinel to=investigator status=OK corr_id=MSG-RUN-042-S-I prefetch_handoff=analyze seq=6
labdemo_ucore: investigator reason=align output is ready before injected failure
labdemo_ucore: investigator digest bytes=27 preview=align memory_limit evidence seq=4
agentos:event type=TOOL_CALL tick=... role=investigator tool=read_file_digest stage=align status=OK bytes=27 seq=4
labdemo_ucore: affected stages=align+analyze+report+archive
labdemo_ucore: investigator prefetch_summary stage=analyze result=analysis waits for align
agentos:event type=PREFETCH_USED tick=... role=investigator stage=analyze summary=analysis waits for align seq=6
agentos:event type=LLM_CALL tick=... mode=template task=explain_root_cause llm_request_id=LLM-RUN-042-RCA-1 project=lab-gene-x run_id=RUN-042 refs=3,4,5,6 status=OK
agentos:event type=LLM_RESULT tick=... mode=template llm_request_id=LLM-RUN-042-RCA-1 llm_status=OK llm_explanation=memory_limit referenced_sequences=3,4,5,6 confidence=medium
agentos:event type=PLAN_CREATED tick=... role=investigator plan=PLAN-RUN-042-RECOVER-1 project=lab-gene-x run_id=RUN-042 actions=align,analyze,report skip=prepare prefetch=analyze refs=3,4,5,6
agentos:event type=CONTEXT_SNAPSHOT tick=... role=investigator records=6 latest=6
agentos:event type=MESSAGE tick=... from=investigator to=recovery status=OK corr_id=MSG-RUN-042-I-R plan=PLAN-RUN-042-RECOVER-1 seq=7
agentos:event type=ACTION tick=... role=recovery stage=align status=OK corr_id=RUN-042-align-rerun-1 plan=PLAN-RUN-042-RECOVER-1 seq=4 duplicate=0
agentos:event type=AUDIT tick=... role=recovery action=rerun_align result=DUPLICATE corr_id=RUN-042-align-rerun-1 plan=PLAN-RUN-042-RECOVER-1 seq=5
agentos:event type=REPORT tick=... role=recovery project=lab-gene-x run_id=RUN-042 file=RUN-042-recovery.md status=OK corr_id=RUN-042-report-write-1 plan=PLAN-RUN-042-RECOVER-1 seq=6 llm_enhanced=0
labdemo_ucore: final report_query hits=2 used_index=1 scanned=7
agentos:event type=FINAL tick=... project=lab-gene-x run_id=RUN-042 status=RECOVERED plan=PLAN-RUN-042-RECOVER-1
labdemo_ucore: global_audit=1 records=... agents=3 context=1 event=1 sched=1 prefetch=1
labdemo_ucore: audit_query=1 kind=... span=... event=2 prefetch=... start=...
labdemo_ucore: unified_timeline records=... context=1 event=1 sched=1 prefetch=1 digest=1
labdemo_ucore: timeline_query prefetch=3 cursor=... digest=1
labdemo_ucore: provenance_graph edges=... message=1 prefetch=1 digest=1
labdemo_ucore: passed
labdemo_ucore: parent passed
```

结论：多 Agent 场景通过，能够展示监控、诊断、恢复和审计过程；sentinel 能读取文件查询产生的预取提示，message 入队时内核把该提示交接给 investigator，investigator 能把带 `HANDOFF` 原因位的提示转化为 analyze 摘要读取，并能读取真实 align 日志的 digest 内容证据；LLM 和计划事件引用 summary、digest、dependency、prefetch 四条 sequence；orchestrator 能从全局审计短记录中看到 sentinel、investigator、recovery 的 Context、事件、调度和预取交接摘要，并能按 kind、span、目标事件、预取 source/target 和起始 sequence 过滤查询，也能通过统一 timeline 精确拉取 prefetch handoff、digest 内容证据或按上一条已读记录继续读取；provenance graph 进一步确认 sentinel 到 investigator 的 message、prefetch 触发关系和 investigator 的 digest 证据边。

## agentsecurity_ucore 样例输出

```text
agentsecurity_ucore: Agent permission test
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

结论：普通进程 mail 最小路径可用；普通进程不能直接投递事件、取消 Agent 等待、修改 Agent 文件元数据、访问私有 `.agentmeta`、读取全局审计、读取当前 span 短记录、读取统一 timeline、查询 timeline、过滤全局审计或配置调度；pid 1 的普通直接子进程可创建 orchestrator，保证 usershell 手动测试路径可用；初始化前索引查询不会阻塞；legacy `tool_id` 和 `tool_name` 不一致会失败；legacy 参数 key/type 错误会返回 `BAD_PARAM`；syscall-only 工具不能通过 batch 执行；sentinel 不能通过用户态传入 recovery role 伪造恢复权限，也不能读取或过滤全局审计、不能配置调度；recovery 的恢复能力来自内核真实 role/capability，重复 corr_id 被识别为 duplicate，且定向恢复和报告写入不会误修改其他 run。

## ch3_trace 基础兼容抽测

执行：

```bash
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=3
timeout 60s make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=ch3_trace CHAPTER=3
```

样例输出：

```text
string from task trace test
Test trace OK!
```

结论：`SYS_trace=410` 已接入 syscall 分发表，`TRACE_READ`、`TRACE_WRITE` 和 `TRACE_SYSCALL` 可被基础用户程序验证。

## 代码与材料检查

已检查：

- `git diff --check` 通过；
- 仓库内容未包含敏感 token 字符串；
- 仓库内容没有旧版内核关键字；
- 仓库内容没有旧版目录或旧测试入口残留。
