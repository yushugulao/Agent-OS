# 验证与性能评估

本文档给出 uCore 分支最终成品的可复现验证入口。逐项测试说明见 [testing-details.md](testing-details.md)，测试输出摘要保存在 [test-record.md](test-record.md)。

## 验证环境

| 项目 | 内容 |
| --- | --- |
| 分支 | `uCore` |
| 开发环境 | WSL2 Ubuntu 26.04 |
| 通用要求 | Linux、RISC-V GCC/binutils、QEMU riscv64、make、git |
| 已验证工具链 | `riscv64-linux-gnu-` |
| 构建命令 | `make clean`、`make user nfs/fs.img`、`make build` |
| 运行命令 | `make run` 或 `scripts/run-agent-tests.sh` |

## 构建命令

```bash
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent
make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore
```

其中：

- `CHAPTER=agent` 只构建本项目最终验证所需用户程序；
- `INIT_PROC=agentfinal_ucore` 用于把指定测试程序编译为内核启动后的第一个用户程序；
- `LOG=error` 可减少 QEMU 输出噪声；
- `SBI` 默认使用 OpenSBI，避免部分环境下 bootloader 输出不稳定。

## 最终验收命令

推荐直接运行：

```bash
bash scripts/run-agent-tests.sh
```

脚本会顺序执行：

1. `make -C user clean`
2. `make clean`
3. `make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent`
4. `make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore`
5. `make run ... INIT_PROC=agentfinal_ucore`
6. `make run ... INIT_PROC=agentfs_ucore`
7. `make run ... INIT_PROC=agentscan_ucore`
8. `make run ... INIT_PROC=agentloop_ucore`
9. `make run ... INIT_PROC=agentsched_ucore`
10. `make run ... INIT_PROC=agentbench_ucore`
11. `make run ... INIT_PROC=labbench_ucore`
12. `make run ... INIT_PROC=labdemo_ucore`
13. `make run ... INIT_PROC=agentsecurity_ucore`

也可以手动分别运行：

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

通过标准：

| 标准 | 期望 |
| --- | --- |
| 构建 | `make user nfs/fs.img` 和 `make build` 成功 |
| 运行 | QEMU 正常启动并执行指定 init 程序 |
| 稳定性 | 无 kernel panic |
| 任务一至三功能 | `agentfinal_ucore: passed` |
| 任务四文件系统能力 | `agentfs_ucore: parent passed` |
| 任务四自动扫描 | `agentscan_ucore: parent passed` |
| 任务五 Agent Loop | `agentloop_ucore: parent passed` |
| 任务五 Agent 调度 | `agentsched_ucore: parent passed` |
| 任务一至五性能 | `agentbench_ucore: parent passed` |
| 演示规划性能入口 | `labbench_ucore: parent passed` |
| 综合场景 | `labdemo_ucore: parent passed` |
| 权限限制 | `agentsecurity_ucore: parent passed` |

## 测试覆盖表

| 测试程序 | 覆盖范围 | 关键通过输出 |
| --- | --- | --- |
| `agentfinal_ucore` | Agent 创建、6 页 Context、批量工具调用、短文本历史、`context_detail()`、完整性链、运行轨迹、当前 span 短记录、统一 timeline、timeline 过滤查询、timeline 游标增量读取、Run Ledger 摘要、cause/span 因果链、用户自管 cache、名称协议、直接读 Context 镜像、Context Snapshot、FIFO 淘汰、篡改保护、文件索引、预取提示、span 预取提示查询、自唤醒事件 | `agentfinal_ucore: passed` |
| `agentfs_ucore` | 真实文件 inode 绑定、字段清空、文件删除清理、`.agentmeta` 重新加载、接近 128 条文件元数据下的 scan/index 差异和一致性、query plan、generation-aware 查询缓存、预取提示、结果截断标志、不存在 selector 返回 NOT_FOUND | `agentfs_ucore: parent passed` |
| `agentscan_ucore` | 调度器空隙分批扫描根目录、真实文件自动建元数据、索引查询、文件删除后自动清理元数据 | `agentscan_ucore: parent passed` |
| `agentloop_ucore` | FIFO 事件顺序、事件 cause/span、队列满丢弃、多 watch、unwatch、有限 timeout 睡眠、wait cancel、TIMER unwatch、heartbeat wake/stop | `agentloop_ucore: parent passed` |
| `agentsched_ucore` | 角色权重、受权调度配置、事件优先、调度原因记录、调度次数、让出处理器次数和虚拟运行量公平性计数 | `agentsched_ucore: parent passed` |
| `agentbench_ucore` | scalar vs batch、direct Context、context_query vs context_snapshot、timeline snapshot/query/cursor、文件扫描 vs 索引候选记录数、查询缓存、预取提示 snapshot、timeout/heartbeat 断言、busy polling 和 event wait/wake 计时观测 | `agentbench_ucore: parent passed` |
| `labbench_ucore` | 初步演示规划中的性能入口，包装运行 `agentbench_ucore`，后续可升级为 `labbench --full` | `labbench_ucore: parent passed` |
| `labdemo_ucore` | orchestrator 控制的多 Agent 综合场景、文件属性查询、预取提示消费、span 预取提示查询、当前 span 短记录、统一 timeline、timeline 过滤查询、timeline 游标增量读取、依赖查询、事件等待、消息唤醒、权限拒绝、幂等恢复、报告查询、结构化 `agentos:event`、全局审计短记录和过滤查询 | `labdemo_ucore: parent passed` |
| `agentsecurity_ucore` | 初始化前索引查询、legacy tool mismatch、普通进程敏感调用拒绝、`.agentmeta` 普通访问保护、pid 1 直接子进程启动 orchestrator、sentinel 伪造 recovery 拒绝、多 run 定向恢复、重复 corr_id 拒绝、role/capability mask 检查、全局审计权限检查、全局审计过滤权限检查、Run Ledger 权限检查、timeline 权限检查、timeline query/read 权限检查、调度配置权限检查 | `agentsecurity_ucore: parent passed` |

综合演示主入口是 `labdemo_ucore`。`agentfinal_ucore` 和 `agentbench_ucore` 是任务一至三高性能底座和任务四/五演示能力的持续验证入口。

## 功能输出样例

```text
agentfinal_ucore: Agent-OS on uCore final verification
agentfinal_ucore: context size=24576 capacity=128
agentfinal_ucore: batch first_seq=1 last_seq=64
agentfinal_ucore: short_text_history=1 payload=ucore-final result=ucore-final
agentfinal_ucore: tamper_protected=1
agentfinal_ucore: context_detail=1 sequence=8
agentfinal_ucore: causal_context=1 first_cause=0 next_cause=1 span=1 edges=63
agentfinal_ucore: context_integrity=1 first_hash=... latest_hash=...
agentfinal_ucore: user_cache_preserved=1 offset=21504 size=3072
agentfinal_ucore: record_flags system=1 manual=1 truncated=0
agentfinal_ucore: legacy_name_protocol=1
agentfinal_ucore: fifo oldest=66 latest=193 dropped=65
agentfinal_ucore: file_query hits=2 scanned=2 used_index=1
agentfinal_ucore: prefetch_hints=1 count=3 first_stage=analyze
agentfinal_ucore: span_prefetch=1 count=... first_stage=...
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

文件系统测试输出摘要：

```text
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

自动扫描测试输出摘要：

```text
agentscan_ucore: background_scan usershell=1 runs=1 entries=64 added=10
agentscan_ucore: auto_file_create=1 size=14 generation=19
agentscan_ucore: auto_file_delete=1
agentscan_ucore: passed
agentscan_ucore: parent passed
```

Agent Loop 测试输出摘要：

```text
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

Agent 调度测试输出摘要：

```text
agentsched_ucore: role_weights sentinel=70 investigator=90 recovery=120 orchestrator=110
agentsched_ucore: configurable_policy=1 weight=150 priority=20 budget=3
agentsched_ucore: event_priority=1 dispatch=6 event_dispatch=1
agentsched_ucore: reason_trace=1 records=6 reason=131 score=1655
agentsched_ucore: fairness=1 dispatch=18 preemptions=13 vruntime=162
agentsched_ucore: passed
agentsched_ucore: parent passed
```

综合演示输出摘要：

```text
labdemo_ucore: Agent-OS laboratory recovery demo
agentos:event type=AGENT_CREATED tick=... role=orchestrator
agentos:event type=RUN_OBJECT tick=... project=lab-gene-x workflow=nightly-regression run_id=RUN-042 desired_state=RECOVERED
agentos:event type=WATCH_REGISTERED tick=... role=sentinel event=FILE_STATUS filter=status=failed
agentos:event type=INCIDENT_CREATED tick=... id=INC-RUN-042-ALIGN-OOM project=lab-gene-x run_id=RUN-042 stage=align reason=memory_limit
labdemo_ucore: sentinel event payload=status=failed;stage=align;run_id=RUN-042;project=lab-gene-x
agentos:event type=TOOL_CALL tick=... role=sentinel tool=query_file project=lab-gene-x run_id=RUN-042 status=failed hits=1 used_index=1
agentos:event type=PREFETCH_HINT tick=... role=sentinel project=lab-gene-x run_id=RUN-042 source_stage=align next_stage=analyze
labdemo_ucore: investigator handoff_prefetch stage=analyze source_seq=4 reason=31
labdemo_ucore: investigator span_prefetch stage=analyze count=... source_pid=... target_pid=...
labdemo_ucore: investigator span_trace records=... context=1 event=1 prefetch=1
agentos:event type=MESSAGE tick=... from=sentinel to=investigator status=OK corr_id=MSG-RUN-042-S-I prefetch_handoff=analyze
labdemo_ucore: investigator digest bytes=27 preview=align memory_limit evidence seq=...
agentos:event type=TOOL_CALL tick=... role=investigator tool=read_file_digest stage=align status=OK bytes=27 seq=...
agentos:event type=PREFETCH_USED tick=... role=investigator stage=analyze summary=analysis waits for align
agentos:event type=LLM_CALL tick=... mode=template task=explain_root_cause llm_request_id=LLM-RUN-042-RCA-1 refs=...,...,...,... status=OK
agentos:event type=PLAN_CREATED tick=... role=investigator plan=PLAN-RUN-042-RECOVER-1 actions=align,analyze,report skip=prepare prefetch=analyze refs=...,...,...,...
agentos:event type=AUDIT tick=... role=sentinel action=rerun_stage result=DENIED corr_id=RUN-042-align-rerun-1
agentos:event type=ACTION tick=... role=recovery stage=align status=OK corr_id=RUN-042-align-rerun-1
agentos:event type=REPORT tick=... role=recovery project=lab-gene-x run_id=RUN-042 file=RUN-042-recovery.md status=OK corr_id=RUN-042-report-write-1 plan=PLAN-RUN-042-RECOVER-1 seq=... llm_enhanced=0
agentos:event type=FINAL tick=... status=RECOVERED plan=PLAN-RUN-042-RECOVER-1
labdemo_ucore: global_audit=1 records=... agents=3 context=1 event=1 sched=1 prefetch=1
labdemo_ucore: audit_query=1 kind=... span=... event=2 prefetch=... start=...
labdemo_ucore: unified_timeline records=... context=1 event=1 sched=1 prefetch=1 digest=1
labdemo_ucore: timeline_query prefetch=3 cursor=... digest=1
labdemo_ucore: provenance_graph edges=... message=1 prefetch=1 digest=1
labdemo_ucore: passed
labdemo_ucore: parent passed
```

权限限制输出摘要：

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

## 性能数据样例

```text
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

说明：

- 上述性能数字是一次样例输出，QEMU tick、宿主机负载和日志等级都会影响绝对数值。
- `batch_agent_run` 与 `scalar_agent_run` 执行同样数量的 echo 工具操作，前者将 64 个 op 合并为一次 syscall。
- `direct_context` 直接读取用户态 Context 镜像，不进入内核。
- `context_snapshot` 一次返回最多 128 条可见记录，按返回记录数计算吞吐。
- 文件索引查询的提升幅度与数据分布有关；当前测试保证能观察到 scan/index 两条路径，并输出候选记录数差异。
- `file_digest_read` 读取真实文件短预览和内容指纹，按参与计算字节数计数；`file_digest_cache` 输出重复读取同一真实文件证据时的缓存命中和未命中。
- `file_prefetch_snapshot` 读取由文件查询历史和阶段依赖生成的 metadata 提示，提示本身不预读文件内容。
- `busy_poll_query` 和 `event_wait_wake` 同时输出，用于展示轮询路径与事件路径都可观测；不设置固定 tick 阈值。

## 基础兼容抽测

Agent-OS 最终验收主路径是 `CHAPTER=agent`。同时，当前实现补充了代表性的 uCore 基础 syscall：`trace`、`mailread`、`mailwrite`。基础兼容抽测使用 `CHAPTER=3` 中的 `ch3_trace`：

```bash
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=3
timeout 60s make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=ch3_trace CHAPTER=3
```

期望输出：

```text
string from task trace test
Test trace OK!
```

`agentsecurity_ucore` 中还验证了普通进程 mail 的最小路径：

```text
agentsecurity_ucore: mail_basic=1
```

## 覆盖到的赛题验收项

| 赛题验收项 | 证据 |
| --- | --- |
| Agent 进程能成功创建，PCB 扩展字段正确初始化 | `agentfinal_ucore` |
| Agent Context 区正确分配，Agent 可直接读取 | `agentfinal_ucore` 直接读取 header/latest/record |
| 用户态篡改 Context 镜像不影响内核权威历史 | `agentfinal_ucore: tamper_protected=1` |
| 用户自管 Context cache 不被 snapshot 覆盖 | `agentfinal_ucore: user_cache_preserved=1` |
| 普通进程和 Agent 进程共存 | `agent_create_role` 由 pid 1 普通 init 或 pid 1 直接普通子进程创建 orchestrator；普通进程不能直接执行敏感 Agent syscall |
| 用户态 Agent 能调用至少 3 个内核工具 | `agentfinal_ucore`、`labdemo_ucore` |
| 请求和响应为结构化格式 | `agent_op`、`agent_result`、`agent_request`、`agent_response` |
| 工具名称 + 参数键值列表协议可用 | `agentfinal_ucore: legacy_name_protocol=1` |
| 5 轮以上连续工具调用并维护路径 | `agentfinal_ucore` 连续 192 次 op |
| Context Path 保存 128 条短文本摘要路径 | `agentfinal_ucore: short_text_history=1` |
| Context Path 保存 cause/span 因果字段 | `agentfinal_ucore: causal_context=1` |
| Context Path 保存完整性链 | `agentfinal_ucore: context_integrity=1` |
| Context 和调度原因可合并为运行轨迹 | `agentfinal_ucore: runtime_trace=1 records=... context=1 sched=1 wait=1` |
| 当前 span 短记录可由参与 Agent 查询 | `agentfinal_ucore: span_trace=1 records=... context=1 event=1`、`labdemo_ucore: investigator span_trace ...` |
| 多 Agent Context、事件、调度和预取交接摘要可由 orchestrator 查询 | `labdemo_ucore: global_audit=1 records=... agents=3 context=1 event=1 sched=1 prefetch=1` |
| 多 Agent 全局短记录可由 orchestrator 过滤查询 | `labdemo_ucore: audit_query=1 kind=... span=... event=2 prefetch=... start=...` |
| 全局短记录可用摘要和 hash 链快速校验 | `agentfinal_ucore: run_ledger=1 records=... hash=... context=... event=... sched=... prefetch=...` |
| Context、调度、审计、预取提示和内容摘要证据可统一导出、过滤并等待 | `agentfinal_ucore: unified_timeline=1`、`agentfinal_ucore: timeline_query=1`、`agentfinal_ucore: timeline_wait=1 timeout=-7 source_gate=1 event_gate=1 wake=1 query=1 read=1 sleeps=1 wakeups=1`、`labdemo_ucore: timeline_query prefetch=3 cursor=... digest=1` |
| 可见因果关系可直接导出 | `agentfinal_ucore: provenance_graph=1 edges=126 context=1 audit=1`、`labdemo_ucore: provenance_graph edges=... message=1 prefetch=1 digest=1` |
| Agent 直接从 Context 高速读取路径数据 | `agentbench_ucore: direct_context` |
| 路径超长自动淘汰且不 OOM | `agentfinal_ucore` 验证 `oldest=66 latest=193 dropped=65` |
| 完整工具调用详情可查询 | `agentfinal_ucore: context_detail=1` |
| 文件属性查询、真实 inode 关联、私有 `.agentmeta`、索引路径、查询缓存、查询计划和预取提示 | `agentfinal_ucore`、`agentfinal_ucore: span_prefetch=1`、`agentfs_ucore: .agentmeta_reload=1`、`agentfs_ucore: query_cache=1 ...`、`agentfs_ucore: prefetch_hints=1`、`agentbench_ucore: file_query_cache hit=1 ...`、`agentbench_ucore: prefetch_records ...`、`labdemo_ucore: sentinel prefetch_hint ...`、`labdemo_ucore: investigator handoff_prefetch ...`、`labdemo_ucore: investigator span_prefetch ...`、`labdemo_ucore: audit_query=1 ... prefetch=...`、`agentos:event type=PREFETCH_USED ...` |
| 调度器空隙根目录扫描和自动索引维护 | `agentscan_ucore: background_scan usershell=1`、`agentscan_ucore: auto_file_create=1`、`agentscan_ucore: auto_file_delete=1` |
| 初始化前索引查询不会卡死 | `agentsecurity_ucore: preinit_index_query=1` |
| legacy `tool_id` / `tool_name` 不匹配会失败 | `agentsecurity_ucore: legacy_tool_mismatch=1` |
| 文件依赖查询和最小恢复 | `labdemo_ucore: affected stages=align+analyze+report+archive` |
| Agent watch/wait 和文件状态唤醒 | `agentloop_ucore`、`labdemo_ucore` |
| Agent wait timeout、heartbeat 字段更新和 heartbeat 事件停止 | `agentbench_ucore: timeout_heartbeat=1`、`agentloop_ucore: timeout_sleep_no_poll=1`、`agentloop_ucore: timer_unwatch=1`、`agentloop_ucore: heartbeat_wake_stop=1` |
| Agent wait cancel | `agentloop_ucore: wait_cancel=1`；普通进程调用 `agent_wait_cancel()` 在 `agentsecurity_ucore` 中被拒绝 |
| Agent 事件携带 cause/span 并在消费后延续 | `agentloop_ucore: event_causality=1` |
| Agent 感知调度策略 | `agentsched_ucore: role_weights ...`、`agentsched_ucore: configurable_policy=1`、`agentsched_ucore: event_priority=1`、`agentsched_ucore: reason_trace=1`、`agentsched_ucore: fairness=1` |
| 消息触发 Agent 事件 | `labdemo_ucore` 中 sentinel->investigator、investigator->recovery 消息 |
| 权限拒绝和幂等恢复 | `labdemo_ucore` 中 denied 和 duplicate 输出 |
| 权限不能由用户态 role 参数伪造 | `agentsecurity_ucore` 中 sentinel 伪造 recovery 仍返回 `AGENT_STATUS_DENIED` |
| 普通进程和非 orchestrator Agent 不能读取、过滤或摘要查询全局审计 | `agentsecurity_ucore` 中普通进程返回 `-1`，sentinel 返回 `AGENT_STATUS_DENIED` |
| 多 run 恢复和报告写入只修改目标 run | `agentsecurity_ucore: scoped_rerun=1`、`agentsecurity_ucore: scoped_report=1` |
| 代表性 uCore 基础 syscall 可用 | `ch3_trace` 输出 `Test trace OK!`；`agentsecurity_ucore: mail_basic=1` |

## 仍需补充的验证

| 方向 | 当前缺口 |
| --- | --- |
| 文件扫描深度 | 当前自动扫描 uCore 根目录短文件名，尚未扩展到多级目录递归 |
| 长期 Agent 调度 | 当前验证角色权重、受权调度配置、事件优先、deadline、heartbeat、wait cancel 和虚拟运行量，尚未提供复杂策略语言 |
| 最终成品 LLM Gateway | 当前只预留结构化事件和工具输出，未接真实云端 LLM |
| 最终成品可视化大屏 | 当前输出 `agentos:event`，但宿主机大屏尚未实现 |
| 性能可信度 | tick 粒度较粗，后续可补更细粒度计数 |
