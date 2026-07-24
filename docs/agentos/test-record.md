# 测试记录

测试目标：根目录 AgentOS-uCore 增强目标

本次最终复测日期：2026-07-24

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

## AgentOS 专项脚本

执行：

```bash
bash scripts/run-agent-tests.sh
```

独立专项记录：2026-07-22 的脚本依次运行 15 个 Agent 程序并全部通过；随后增加 `iobudget_ucore`。2026-07-24 完成 pipe 安全主体委派机制后的历史轮以 `359.4s` 完成 16/16；本次观测查询修复后，`CASE_TIMEOUT=240s scripts/run-agent-tests.sh` 再次完成 16/16，整条命令墙钟约 `338.4s`。专项运行要求每个程序在超时前输出 `parent passed`，且日志中不存在未被用例明确声明为预期故障的 `check failed`、`panic`、`unknown syscall`、`bad addr`、`IllegalInstruction` 或 `child_failed`。本轮 16/16 仍不等于尚未运行的 `make full-verify` 全绿。

## 输出提取方式

测试程序的输出按三类信息写入文档：

| 信息类型 | 输出形态 | 文档用途 |
| --- | --- | --- |
| 通过标记 | `passed`、`parent passed` | 判断测试是否完成 |
| 状态事实 | `key=value`，例如 `used_index=1`、`stale_commit=1`、`tamper_protected=1` | 提取成完成情况和功能结论 |
| 计时观测 | `ops ticks ops_per_tick speedup_x100`、`scan_records`、`index_records` | 提取成性能表和对比说明 |

这种提取方式避免只呈现大段 QEMU 日志。下面每个样例都保留最能说明功能的输出行，并在结论中说明这些输出对应的内核能力。

## 结论摘要

| 测试程序 | 关键输出 | 对应内容 |
| --- | --- | --- |
| `agentfinal_ucore` | `batch first_seq=1 last_seq=64`、`tamper_protected=1`、`run_ledger=1` | Agent 创建、批量工具调用、Context 可信历史和全局运行账本可用 |
| `agentfs_ucore` | `.agentmeta_reload=1`、`bulk_index scan=149 index=6`、`metadata_action_bounded=1 field_driven=1 batched=1 preemptions=5`、`prefetch_hints=1 bounded=1 count=2 preemptions=8`、`handoff_target_exit=1 endpoint_reuse=1 preemptions=6 ... clean=1` | 真实文件元数据、显式依赖与兼容位图按需解析、索引查询、字段驱动批量状态维护、有界去重预取、metadata 内核工作预算和稳定交接端点可用 |
| `agentscan_ucore` | `background_scan usershell=1`、`auto_file_create=1`、`auto_file_delete=1` | 根目录真实文件能被扫描并同步到 Agent metadata |
| `agentloop_ucore` | `message_source_limit=4`、`ipc_class_limit=8`、`external_limit=12`、`system_event_reserved=4`、`external_reject_reclaim=1`、`broadcast_slow_watcher_isolated=1` | FIFO、睡眠等待、heartbeat、directed 单来源=4、directed 类=8、external=12、第 13 条 external 拒绝、4 条 KERNEL TIMER 保留容量、消费后重接纳和慢 watcher 隔离均通过 |
| `agentsched_ucore` | `role_weights ...`、`event_priority=1`、`reason_trace=1` | Agent 感知调度和调度原因记录可用 |
| `agentconflict_ucore` | `conflict_denied=1`、`direct_write_denied=1`、`stale_commit=1` | 文件编辑冲突由内核真实文件路径阻止 |
| `agentllm_ucore` | `relay_timeline=1`、`requester_done=1` | LLM Relay 事件、唤醒和 timeline 摘要可用 |
| `agentbench_ucore` | `batch_agent_run`、`file_index_query`、`timeline_query_prefetch` | 性能主路径和文件索引/Timeline 查询可观测 |
| `labdemo_ucore` | `type=INCIDENT_CREATED`、`prefetch_handoff=analyze`、`provenance_graph edges=...` | 设定的模拟流程 多 Agent 恢复场景可复现 |
| `agentsecurity_ucore` | `route_source_enforced=1`、`route_target_isolated=1`、`ipc_route_authorization=1`、`message_route_lifecycle=1`、`target_route_consent=1`、`route_slot_reclaimed=1` | 系统事件防伪、未授权注入拒绝、grant/revoke、target LLM_DONE consent、MESSAGE 位图隔离、stable control id 生命周期和 source 退出槽回收均通过 |
| `agentscope_ucore` | `observe_query_bounded=1 ... preemptions=64`、`observe_index_ordered=1`、`observe_cross_scope_progress=1 queries=32 latency_ms=3`、`pipe_redelegation_isolation=1`、`transactional_fd_delegation=1` | audit/span/timeline/provenance 查询进入内核工作预算，有序索引保持结果顺序与 scope/span 隔离，压力下另一 workflow 仍有界前进；pipe 只凭创建线程的一次性票据交接 |
| `agenttrust_ucore` | `wx_image=1`、`immutable_image=1`、`role_image_binding=1` | W^X、可信映像不可变和 Agent 角色映像绑定可验证 |
| `agentvfs_ucore` | `cross_scope_fd_revoked=1`、`worker_pipe_delegation=1`、`protected_paths=1` | 普通 VFS 路径不能绕过文件能力；降权 fork 撤销跨 scope inode fd，worker pipe 只接受单跳显式委派 |
| `iobudget_ucore` | `thread_exit_lease_cleanup=1`、`scheduler_interrupt_progress=1`、`fault_exit_cleanup=1`、`public_budget_shared=1`、`nested_io_attribution=1`、`cache_scope_isolation=1`、`workflow_bounded_progress=1`、`control_reserve_progress=1`、`parent passed` | 最终 teardown 修复后独立轮 `elapsed=2.4s`；ABI sized-copy 另由无单独 marker 的断言覆盖 |
| `usersafety_ucore` | `live after pointer bounds`、`live after directed wakeup`、`parent passed` | syscall 输入检查、定向唤醒和失败事务回滚可验证 |
| `fsenospc_ucore` | `inode exhaustion survived`、`block exhaustion survived` | inode、inode cache 与数据块耗尽返回失败而非触发内核 panic |
| `fspquota_ucore` | `crash_orphan_ready=1`、`reboot_charge_persisted=1`、`relaunch_charge_persisted=1`、`cleanup_reuse=1` | 双目标同镜像三次启动已通过，验证掉电孤儿回收及 PUBLIC 计费跨完整进程域退出与重启保持 |
| `procreap_ucore` / `procreap_agent_ucore` | `live-domain-limit=1`、`reserved-agent-slot=1` | 进程回收、资源域配额与系统保留槽可验证 |
| `syscallfair_ucore` | `[syscall-fairness] both targets passed`、console/inode/trunc 顺序、last-syscall 重调度与 `parent passed` | 本次线程改动后双目标脚本已通过 |
| `threadresource_ucore` | `domain_limit`、`capacity_reject_stable`、`reserved_domain_limit`、`reserved_domain_reuse`、`exit_reuse`、`ordinary_waterline`、`global_thread_limit`、`reserved_global_limit`、`reserved_progress`、`reserved_global_reuse`、`global_reuse`、`domain_fairness`、`parent passed` | 本次改动后 19/12/6/6/4 tiny policy 专项通过 |
| 内核栈预算 | `kernel stack budget: user=7456 interrupt=2272 margin=4096 required=13824 limit=16384` | 当前观测查询修复后的 AgentOS 构建期 callgraph/栈帧预算检查通过 |

## 本次可信 IPC 变更验证状态

2026-07-21 的 QEMU 复测确认 stable control id 定向路由，并动态覆盖 external 合计 12、directed IPC 8、directed 单 stable source 4，以及为显式内核 origin 保留 4 个容量名额。实现还把 attributed notification 上限设为 8，并让 stable source=4 跨 directed/attributed 统一核算；这两个边界尚缺专项动态断言：

- 未 grant 时，低权限 Agent 经 `agent_wake`、`send_message` 和 `llm_request` 向 Recovery/Orchestrator 投递均被拒绝，对应 `route_source_enforced=1` 和 `route_target_isolated=1`；
- orchestrator 合法 grant 后可投递，revoke 后再次拒绝，对应 `ipc_route_authorization=1`；新 controller 的新 control id 不能使用旧 source route，对应 `message_route_lifecycle=1`。该生命周期用例不直接断言实际 PID/PCB 复用；
- target 使用 `WATCH` 自主接受 `LLM_DONE`，LLM-only route 仍拒绝 `MESSAGE`，对应 `target_route_consent=1`；同一 target 顺序接入 `ROUTE_MAX+2` 个短命 source，每轮 route 和实际 MESSAGE 都成功，证明 source 退出后路由槽可回收，对应 `route_slot_reclaimed=1`；
- 同一 stable source 的第 5 条未消费 directed event 返回 `AGENT_STATUS_NO_SPACE`，消费 1 条后可立即补投；两个 source 填满 directed=8，第三个 source 用 4 条 attributed 让 external 达到 12，第四个 source 的第 13 条 external 不入队，随后 4 条 KERNEL TIMER 将总队列填到 16；全部消费后 directed 和 attributed 均可重新接纳，对应 `message_source_limit=4`、`ipc_class_limit=8`、`external_limit=12`、`system_event_reserved=4` 和 `external_reject_reclaim=1`；
- 一个已满 watcher 不阻断后续 watcher 收到 attributed 广播，对应 `broadcast_slow_watcher_isolated=1`；
- 完整 `run-agent-tests`、`run-proc-reap-tests` 的 AgentOS/基线目标及 `agentos-platform-run` 均通过，`agentllm_ucore`、`agentbench_ucore`、`rp_agent_collab` 和 `labdemo_ucore` 在显式建路由后保持通过。

仍保留的非阻断测试缺口是：attributed 单类尚未填到 8；没有让同一 stable source 混合 directed/attributed 触及 4；满 watcher 场景未动态调用 `agent_file_meta_set()` 核对已提交 metadata 的返回值；满普通队列未与 wait cancel 组合。路由侧尚未专项覆盖重复 grant/revoke 幂等、组合位图的部分 revoke、同时占满 16 条 route 后第 17 条返回 `NO_SPACE`、target 退出清表和撤销前已入队事件保留。旧的 `message_send_preserved=1` 只属于等待取消拆分时的历史行为，不再作为可信路由证据。

## 样例输出：agentfinal_ucore

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

## 样例输出：agentfs_ucore

```text
agentfs_ucore: Agent FS metadata test
agentfs_ucore: scoped_dependency=1 run042=align+analyze+report runalt=align+archive
agentfs_ucore: dependency_update=1 result=align+review generation=26
agentfs_ucore: metadata_action_bounded=1 field_driven=1 batched=1 preemptions=5
agentfs_ucore: demo_inode dev=1 inum=44 scanned=3
agentfs_ucore: prefetch_hints=1 bounded=1 count=2 preemptions=8 first_stage=analyze source_seq=20
agentfs_ucore: handoff_target_exit_send=0 preemptions=6
agentfs_ucore: handoff_target_exit=1 endpoint_reuse=1 preemptions=6 replacement=5 clean=1
agentfs_ucore: custom_inode dev=1 inum=55 size=7
agentfs_ucore: content_digest=1 size=7 bytes=7 hash=52642947 preview=agentfs
agentfs_ucore: digest_cache=1 hits=1 misses=1
agentfs_ucore: digest_cache_invalidated=1 misses=1
agentfs_ucore: digest_timeline=1 tool=20 preview=agentfs2
agentfs_ucore: .agentmeta_reload=1
agentfs_ucore: query_cache=1 reason=68
agentfs_ucore: bulk_index scan=149 index=6 hits=1
agentfs_ucore: query_plan scan_plan=0 index_plan=1 reason=4 bucket=15 candidates=6
agentfs_ucore: scan_index_consistent=1
agentfs_ucore: truncated_query total=100 returned=3 truncated=1
agentfs_ucore: clear_status=1 cache_invalidated=1
agentfs_ucore: delete_clears_metadata=1
agentfs_ucore: missing_selector_not_found=1
agentfs_ucore: passed
agentfs_ucore: parent passed
```

结论：文件元数据可绑定真实根目录文件；查询结果携带 `dev`、`inum`、`incarnation`、`size`，其中安全绑定、缓存与编辑版本以 `dev + inum + incarnation` 区分 inode 的不同生命周期。用户态可注册并按 run 查询通用对象依赖；自定义 metadata 可从私有 `.agentmeta` 重新加载；真实文件内容摘要缓存、timeline、scan/index 差异、查询缓存失效、属性清空、删除同步和未命中 selector 均通过。action 回归确认一次批量提交产生 4 次 syscall 内核重调度、每个目标槽只更新一次且纯状态更新不改变 dependency generation；预取回归确认一次查询最多产生 8 条唯一提示，本轮实际为 2 条，target fid 不重复且查询内部产生 8 次内核重调度。交接竞态让原目标在 7 次重调度期间退出并由 replacement 复用进程槽，replacement 的 hint ring 与 mailbox 均保持为空。2026-07-24 独立 `agentfs_ucore` 通过，`elapsed=86.5s`。

## 样例输出：agentscan_ucore

```text
agentscan_ucore: background file scan test
agentscan_ucore: background_scan usershell=1 runs=1 entries=64 added=10
agentscan_ucore: auto_file_create=1 size=14 generation=19
agentscan_ucore: auto_file_delete=1
agentscan_ucore: passed
agentscan_ucore: parent passed
```

结论：调度器空隙分批扫描 uCore 根目录可发现已有真实文件；普通文件 syscall 创建的新文件能自动进入 Agent 元数据表和索引；删除真实文件后自动元数据会被清理。

## 样例输出：agentloop_ucore

```text
agentloop_ucore: Agent event queue test
agentloop_ucore: fifo=1
agentloop_ucore: event_causality=1
agentloop_ucore: overflow_dropped=1
agentloop_ucore: message_source_limit=4
agentloop_ucore: unwatch=1
agentloop_ucore: ipc_class_limit=8
agentloop_ucore: external_limit=12
agentloop_ucore: system_event_reserved=4
agentloop_ucore: external_reject_reclaim=1
agentloop_ucore: broadcast_slow_watcher_isolated=1
agentloop_ucore: timeout_sleep_no_poll=1
agentloop_ucore: timer_unwatch=1
agentloop_ucore: heartbeat_wake_stop=1
agentloop_ucore: wait_cancel=1
agentloop_ucore: passed
agentloop_ucore: parent passed
```

结论：16 槽 FIFO 顺序、cause/span、`agent_unwatch()`、有限 timeout 睡眠、wait cancel、TIMER unwatch、heartbeat 唤醒和停止均通过。新增配额断言确认单来源 4 条、directed 8 条、external 12 条边界；单来源消费 1 条后可立即补投，第 13 条 external 不入队，4 条 KERNEL TIMER 可填满保留容量，全部消费后 directed 与 attributed 可重新接纳，满 watcher 也不阻断后续订阅者。`overflow_dropped=1` 现在由单来源第 5 条 MESSAGE 触发，不再代表总队列已经填满。

## 样例输出：agentsched_ucore

```text
agentsched_ucore: adaptive Agent scheduler test
agentsched_ucore: normal_progress=1 max_agent_burst=8
agentsched_ucore: role_weights sentinel=70 investigator=90 recovery=120 artifact=100 orchestrator=110
agentsched_ucore: configurable_policy=1 weight=150 priority=20 budget=3
agentsched_ucore: event_priority=1 dispatch=6 event_dispatch=1
agentsched_ucore: reason_trace=1 records=6 reason=131 score=1655
agentsched_ucore: fairness=1 dispatch=18 preemptions=13 vruntime=162
agentsched_ucore: passed
agentsched_ucore: parent passed
```

结论：不同 Agent 角色拥有不同调度权重；连续 Agent 调度达到 8 次硬上限后普通任务获得进展。orchestrator 可配置目标 Agent 的 weight、priority 和 budget，非授权调用会被拒绝；有待消费事件的 Agent 会被调度器记录为事件相关调度；最近调度记录可通过 `agent_sched_snapshot()` 查询，并包含事件队列、角色权重、配置优先级、分数、事件数量等原因字段；反复让出处理器后，调度次数、让出处理器次数和虚拟运行量计数均会增长。

## 样例输出：agentconflict_ucore

```text
agentconflict_ucore: Agent file edit conflict test
agentconflict_ucore: plain_process_denied=1
agentconflict_ucore: conflict_denied=1 direct_write_denied=1 owner=2 conflicts=4
agentconflict_ucore: owner_commit=1 version=3
agentconflict_ucore: stale_commit=1 versioned_commit=1 base=3 current=4
agentconflict_ucore: passed
agentconflict_ucore: parent passed
```

结论：普通进程不能申请文件编辑租约；两个 Agent 同时编辑同一真实文件时，第二个 Agent 会得到 `AGENT_STATUS_CONFLICT` 和持有者信息；未持有租约的真实 `write`、`O_TRUNC`、`unlink` 都会失败；持有者可以写入并提交；错误期望版本返回 `AGENT_STATUS_STALE`，正确版本提交会推进文件版本。

## 样例输出：agentllm_ucore

```text
agentllm_ucore: Agent LLM relay test
agentllm_ucore: relay_timeline=1
agentllm_ucore: requester_done=1
agentllm_ucore: template_relay=1
agentllm_ucore: passed
agentllm_ucore: parent passed
```

结论：请求 Agent 通过 `llm_request` 发送 prompt 摘要，Relay Agent 具备 `LLM_RELAY` capability 并通过 `llm_response` 返回模板结果；请求方由 `AGENT_EVENT_LLM_DONE` 唤醒，Context 和 timeline 能记录这次请求、等待和响应事实。真实云端调用仍由用户态或宿主机 relay 完成。

## 样例输出：agentbench_ucore

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

结论：batch、direct context 和 snapshot 路径均稳定完成；文件查询输出了 scan/index 候选记录数差异，并给出 query plan 解释索引选择原因；预取提示 snapshot 能在文件查询之后直接返回后续 label metadata 提示。tick 数值会随运行环境波动。

## 样例输出：labdemo_ucore

```text
labdemo_ucore: Agent-OS laboratory recovery demo
labdemo_ucore: created role=orchestrator pid=2 context=0x0000003ffffe9000
agentos:event type=AGENT_CREATED tick=... role=orchestrator pid=2 context=0x0000003ffffe9000
agentos:event type=RUN_OBJECT tick=... project=lab-gene-x workflow=nightly-regression run_id=设定的模拟流程 desired_state=RECOVERED policy=minimal_rerun
labdemo_ucore: created role=investigator pid=4 context=0x0000003ffffe9000
agentos:event type=AGENT_CREATED tick=... role=investigator pid=4 context=0x0000003ffffe9000
labdemo_ucore: created role=sentinel pid=5 context=0x0000003ffffe9000
agentos:event type=AGENT_CREATED tick=... role=sentinel pid=5 context=0x0000003ffffe9000
agentos:event type=WATCH_REGISTERED tick=... role=sentinel event=FILE_STATUS filter=status=failed
labdemo_ucore: created role=recovery pid=3 context=0x0000003ffffe9000
agentos:event type=AGENT_CREATED tick=... role=recovery pid=3 context=0x0000003ffffe9000
agentos:event type=INCIDENT_CREATED tick=... id=INC-设定的模拟流程-ALIGN-OOM project=lab-gene-x workflow=nightly-regression run_id=设定的模拟流程 stage=align reason=memory_limit
labdemo_ucore: sentinel event payload=status=failed;stage=align;run_id=设定的模拟流程;project=lab-gene-x
agentos:event type=TOOL_CALL tick=... role=sentinel tool=query_file project=lab-gene-x run_id=设定的模拟流程 status=failed hits=1 used_index=1 seq=4
labdemo_ucore: sentinel prefetch_hint stage=analyze source_seq=4 plan=2 candidates=1
agentos:event type=PREFETCH_HINT tick=... role=sentinel project=lab-gene-x run_id=设定的模拟流程 source_stage=align next_stage=analyze source_seq=4 candidates=1 reason=15
agentos:event type=AUDIT tick=... role=sentinel action=action_commit result=DENIED reason=capability corr_id=设定的模拟流程-align-rerun-1 seq=5
labdemo_ucore: investigator handoff_prefetch stage=analyze source_seq=4 reason=31
labdemo_ucore: investigator span_prefetch stage=analyze count=... source_pid=... target_pid=...
labdemo_ucore: investigator span_trace records=... context=1 event=1 prefetch=1
agentos:event type=MESSAGE tick=... from=sentinel to=investigator status=OK corr_id=MSG-设定的模拟流程-S-I prefetch_handoff=analyze seq=6
labdemo_ucore: investigator reason=align output is ready before injected failure
labdemo_ucore: investigator digest bytes=27 preview=align memory_limit evidence seq=4
agentos:event type=TOOL_CALL tick=... role=investigator tool=read_file_digest stage=align status=OK bytes=27 seq=4
labdemo_ucore: affected labels=align+analyze+report+archive
labdemo_ucore: investigator prefetch_summary stage=analyze result=analysis waits for align
agentos:event type=PREFETCH_USED tick=... role=investigator stage=analyze summary=analysis waits for align seq=6
agentos:event type=LLM_CALL tick=... mode=template task=explain_root_cause llm_request_id=LLM-设定的模拟流程-RCA-1 project=lab-gene-x run_id=设定的模拟流程 refs=3,4,5,6 status=OK
agentos:event type=LLM_RESULT tick=... mode=template llm_request_id=LLM-设定的模拟流程-RCA-1 llm_status=OK llm_explanation=memory_limit referenced_sequences=3,4,5,6 confidence=medium
agentos:event type=PLAN_CREATED tick=... role=investigator plan=PLAN-设定的模拟流程-RECOVER-1 project=lab-gene-x run_id=设定的模拟流程 actions=align,analyze,report skip=prepare prefetch=analyze refs=3,4,5,6
agentos:event type=CONTEXT_SNAPSHOT tick=... role=investigator records=6 latest=6
agentos:event type=MESSAGE tick=... from=investigator to=recovery status=OK corr_id=MSG-设定的模拟流程-I-R plan=PLAN-设定的模拟流程-RECOVER-1 seq=7
agentos:event type=ACTION tick=... role=recovery label=align status=OK corr_id=设定的模拟流程-align-rerun-1 plan=PLAN-设定的模拟流程-RECOVER-1 seq=4 duplicate=0
agentos:event type=AUDIT tick=... role=recovery action=commit_align result=DUPLICATE corr_id=设定的模拟流程-align-rerun-1 plan=PLAN-设定的模拟流程-RECOVER-1 seq=5
agentos:event type=ARTIFACT tick=... role=recovery project=lab-gene-x run_id=设定的模拟流程 file=设定的模拟流程-recovery.md status=OK corr_id=设定的模拟流程-report-write-1 plan=PLAN-设定的模拟流程-RECOVER-1 seq=6 llm_enhanced=0
labdemo_ucore: final report_query hits=2 used_index=1 scanned=7
agentos:event type=FINAL tick=... project=lab-gene-x run_id=设定的模拟流程 status=RECOVERED plan=PLAN-设定的模拟流程-RECOVER-1
labdemo_ucore: global_audit=1 records=... agents=3 context=1 event=1 sched=1 prefetch=1
labdemo_ucore: audit_query=1 kind=... span=... event=2 prefetch=... start=...
labdemo_ucore: unified_timeline records=... context=1 event=1 sched=1 prefetch=1 digest=1
labdemo_ucore: timeline_query prefetch=3 cursor=... digest=1
labdemo_ucore: provenance_graph edges=... message=1 prefetch=1 digest=1
labdemo_ucore: passed
labdemo_ucore: parent passed
```

结论：多 Agent 场景通过，能够呈现监控、诊断、恢复和审计过程；sentinel 能读取文件查询产生的预取提示，message 入队时内核把该提示交接给 investigator，investigator 能把带 `HANDOFF` 原因位的提示转化为 analyze 摘要读取，并能读取真实 align 日志的 digest 内容证据；LLM 和计划事件引用 summary、digest、dependency、prefetch 四条 sequence；orchestrator 能从全局审计短记录中看到 sentinel、investigator、recovery 的 Context、事件、调度和预取交接摘要，并能按 kind、span、目标事件、预取 source/target 和起始 sequence 过滤查询，也能通过统一 timeline 精确拉取 prefetch handoff、digest 内容证据或按上一条已读记录继续读取；provenance graph 进一步确认 sentinel 到 investigator 的 message、prefetch 触发关系和 investigator 的 digest 证据边。

## 样例输出：agentsecurity_ucore

```text
agentsecurity_ucore: Agent permission test
agentsecurity_ucore: bootstrap_plain_identity=1
agentsecurity_ucore: mail_basic=1
agentsecurity_ucore: plain_process_denied=1
agentsecurity_ucore: .agentmeta_protected=1
agentsecurity_ucore: untrusted_exec_role_creation_denied=1
agentsecurity_ucore: plain_child_role_creation_denied=1
agentsecurity_ucore: role=retired-controller capability_checked=1
agentsecurity_ucore: role=cancel-victim capability_checked=1
agentsecurity_ucore: role=replacement-controller capability_checked=1
agentsecurity_ucore: wait_cancel_scope=1
agentsecurity_ucore: message_route_lifecycle=1
agentsecurity_ucore: wait_cancel_controller_lifecycle=1
agentsecurity_ucore: role=orchestrator capability_checked=1
agentsecurity_ucore: bootstrap_orchestrator_create=1
agentsecurity_ucore: orchestrator_plain_fork_denied=1
agentsecurity_ucore: route_target_isolated=1
agentsecurity_ucore: route_source_enforced=1
agentsecurity_ucore: ipc_route_authorization=1
agentsecurity_ucore: target_route_consent=1
agentsecurity_ucore: route_slot_reclaimed=1
agentsecurity_ucore: preinit_index_query=1
agentsecurity_ucore: legacy_tool_mismatch=1
agentsecurity_ucore: legacy_param_validation=1 syscall_only=1
agentsecurity_ucore: role=sentinel capability_checked=1
agentsecurity_ucore: role=sentinel delegation_denied=1
agentsecurity_ucore: role=sentinel wait_cancel_denied=1
agentsecurity_ucore: wake_event_authorization=1
agentsecurity_ucore: sentinel spoof_denied=1
agentsecurity_ucore: role=investigator capability_checked=1
agentsecurity_ucore: role=investigator delegation_denied=1
agentsecurity_ucore: role=investigator wait_cancel_denied=1
agentsecurity_ucore: role=artifact capability_checked=1
agentsecurity_ucore: role=artifact delegation_denied=1
agentsecurity_ucore: role=artifact wait_cancel_denied=1
agentsecurity_ucore: artifact_action_denied=1
agentsecurity_ucore: role=recovery capability_checked=1
agentsecurity_ucore: role=recovery delegation_denied=1
agentsecurity_ucore: role=recovery wait_cancel_denied=1
agentsecurity_ucore: recovery action_ok=1 duplicate=1
agentsecurity_ucore: wait_cancel_capability_split=1
agentsecurity_ucore: scoped_action=1
agentsecurity_ucore: scoped_artifact=1
agentsecurity_ucore: passed
agentsecurity_ucore: reaped_agent_slot_cleared=1
agentsecurity_ucore: bootstrap_exec_grant_revoked=1
agentsecurity_ucore: parent passed
```

结论：内核加载的可信初始进程是唯一 bootstrap 创建授权根；授权留在内核 PCB 中并与业务 capability 分离，不扩展未版本化的 `agent_info` ABI。普通 `fork`、普通子进程 `exec`、orchestrator 的普通 `fork` 以及可信根自身 `exec` 均不会传播或保留创建权，已回收 Agent 进程槽再次用于普通进程时也不残留身份、能力或 Context；只有 orchestrator 能委派角色，低权限角色无法继续创建任何 Agent。等待取消与消息能力分离，stable control id 防止新 controller 继承旧授权。可信 IPC 专项进一步确认低权限 Agent 不能向任意 PID 注入，控制者可 grant/revoke，target 可自主接受 LLM_DONE 且 LLM-only route 不放行 MESSAGE；同一 target 经 `ROUTE_MAX+2` 个短命 source 时，每轮授权与实际投递都成功，证明 source 退出回收路由槽。

## 样例输出：agenttrust_ucore

```text
agenttrust_ucore: executable trust test
agenttrust_ucore: wx_image=1
agenttrust_ucore: immutable_image=1
agenttrust_ucore: bootstrap_role_boundary=1
agenttrust_ucore: trusted_agent_exec=1
agenttrust_ucore: role_image_binding=1
agenttrust_ucore: parent passed
```

结论：数据页不可执行、代码页不可写；策略清单标记的可信映像不能被写入、截断、覆盖创建或删除。bootstrap 授权不能任意创建其他角色，orchestrator 角色只在执行与其绑定的可信映像时保留；错误角色映像和普通复制出的未可信映像不能继承该身份。该机制的可信根是构建期策略清单与密封 inode 元数据，不应表述为密码学签名或运行时哈希认证。

## 样例输出：agentvfs_ucore

```text
agentvfs_ucore: filesystem capability test
agentvfs_probe: sealed_exec_no_elevation=1
agentvfs_probe: cross_image_attenuated=1
agentvfs_probe: failed_open_atomic=1
agentvfs_probe: wrong_first_exec_attenuated=1
agentvfs_ucore: cross_scope_fd_revoked=1
agentvfs_ucore: protected_paths=1
agentvfs_ucore: parent passed
```

结论：公共文件与工作流受保护文件可以同名存在且互不覆盖；普通进程和无文件能力的 Agent 不能借 `open/read/write/unlink` 绕过授权，investigator 只有读权限。普通 `fork()` 会降权并撤销跨 scope 的已打开 inode 描述符；同 scope inode fd 才保留逐操作重鉴权语义。普通进程可以执行布局有效的 worker 映像，但不会因此提权；orchestrator 的 worker 委派受 immutable/domain-safe 映像属性、VFS profile 能力上限和精确 inode 绑定共同约束。失败的创建/截断意图在文件系统发生变更前返回。文件安全身份以 `dev + inum + incarnation` 区分 inode 生命周期。

## 样例输出：usersafety_ucore

```text
usersafety_ucore: syscall boundary verification
usersafety_ucore: live after pointer bounds
usersafety_ucore: live after string bounds
usersafety_ucore: live after exec argv bounds
usersafety_ucore: live after thread boundaries
usersafety_ucore: live after directed wakeup
usersafety_ucore: live after pipe buffers
usersafety_ucore: live after wait copyout
usersafety_ucore: live after time copyout
usersafety_ucore: live after fd directions
usersafety_ucore: live after file rollback
usersafety_ucore: live after semaphore inputs
usersafety_ucore: live after failed exec transaction
usersafety_ucore: exec child passed
usersafety_ucore: live after successful exec transaction
usersafety_ucore: parent passed
```

结论：非法指针、跨页缓冲区、未终止字符串、过量 `exec` 参数和非法线程入口均沿统一用户地址校验路径失败；定向唤醒不会被无关子进程退出干扰。管道临时引用、`wait`/时间 copyout、文件描述符方向、fd 槽不足时打开既有文件或创建 pipe 的引用回滚，以及信号量计数范围均有负向覆盖；失败的 `exec` 不破坏旧地址空间，成功 `exec` 完成事务切换。

## 专项输出：文件系统 ENOSPC

执行：

```bash
make fs-enospc-test TOOLPREFIX=riscv64-linux-gnu-
```

两类通用目标均需出现前三组程序标记；AgentOS 配额场景还需出现版本生命周期、工作流保留和清理标记。新增持久主体场景在两个目标各使用同一镜像连续启动三次，并要求按顺序出现以下标记：

```text
fsenospc_ucore: inode exhaustion survived
fsenospc_ucore: inode cache exhaustion survived
fsenospc_ucore: block exhaustion survived
fsenospc_ucore: parent passed
fsquota_ucore: public_version_churn=1 cycles=640
fsquota_ucore: public_domain_limited=1 blocks=15 inodes=8
fsquota_ucore: post_exit_accounting=1
fsquota_ucore: workflow_reserve=1
fsquota_ucore: workflow_version_reserve=1
fsquota_ucore: content_version_reserve=1
fsquota_ucore: kernel_metadata_reserve=1
fsquota_ucore: pressure_cleanup=1
fspquota_ucore: crash_orphan_ready=1
fspquota_ucore: sponsored_object_charged=1 blocks=14
fspquota_ucore: durable_fixture=1 blocks=18 inodes=8 owner_exited=1
fspquota_ucore: reboot_charge_persisted=1
fspquota_ucore: deletion_reuse=1
fspquota_ucore: relaunch_charge_persisted=1 launches=2
fspquota_ucore: cleanup_reuse=1
fspquota_ucore: parent passed
[fs-enospc] generic, persistent principal, and Agent quota cases passed
```

本次冻结源码的 `make fs-enospc-test` 全流程通过，墙钟 `75.1s`；quota/domain、持久 PUBLIC principal、孤儿回收、重启计费与删除复用场景均完成。磁盘 inode、内存 inode cache 和数据块耗尽均通过正常错误返回或短写报告，未转化为全内核 panic；释放资源后可以重新分配。PUBLIC 域完成超过旧版本表容量的 640 次短命 inode 循环后，workflow 仍能取得编辑版本并命中内容摘要缓存，说明版本 sidecar 随最终 inode 生命周期回收，且不能跨 inode 槽侵占 Agent 保留资源。

本次稳定 principal 回归已在 AgentOS 与 baseline 上分别完成 crash/seed/verify 三轮。第一轮在 PUBLIC 文件已 unlink 但描述符仍打开时强制结束 QEMU，第二轮挂载必须回收该不可达 inode/block；runner 在 seed 后解析 raw bitmap/dinode/qmap，确认物理分配相对 mkfs 基线仅增加 4 block/7 inode，且非 FREE owner 同样只增加 4 个，排除只从账本忽略孤儿的假通过。镜像还预装一个由 SYSTEM 赞助的 13 数据块可变文件；PUBLIC 首次覆盖前必须把 inode、数据块和间接索引块整体接管，共计 `14 block/1 inode`，不能借预装对象绕过配额。第二轮随后让占满 `18 block/8 inode` 上限的 PUBLIC 进程资源域完整退出，第三轮在同一磁盘镜像上验证 qmap/dinode 重建、新域仍受旧文件计费、删除退款及清理复用；两个目标均依次取得 `reboot_charge_persisted`、`deletion_reuse`、`relaunch_charge_persisted`、`cleanup_reuse` 和 `parent passed`，runner 最终报告通过。该入口仍没有被 `make full-verify` 串联，发布验收时应继续单独执行。

## 专项输出：进程回收与配额

执行：

```bash
make proc-reap-test TOOLPREFIX=riscv64-linux-gnu-
```

2026-07-21 在可用 RISC-V/QEMU 环境中的基础机制输出：

```text
procreap_ucore: process lifecycle verification
procreap_ucore: wait-queue cancellation passed
procreap_ucore: unreaped-parent-isolated=1
procreap_ucore: live-domain-limit=1
procreap_ucore: lineage-bypass-denied=1
procreap_ucore: live-quota-returned=1
procreap_ucore: peer-domain-isolated=1
procreap_ucore: parent passed
procreap_agent_ucore: bounded teardown scheduling
procreap_agent_ucore: child-pressure-isolated=1
procreap_agent_ucore: reserved-agent-slot=1
procreap_agent_ucore: adversarial-agent=1
procreap_agent_ucore: parent passed
[proc-reap] both targets passed
```

结论：普通生命周期测试在增强目标和对照目标运行，adversarial Agent 测试在增强目标运行。覆盖阻塞 syscall 撤销、等待队列定向取消、拒绝 `wait()` 的父进程隔离、活进程资源域配额、谱系绕过拒绝、配额归还、同级域隔离、Agent 系统保留槽和高压退出调度。`make full-verify` 会调用该脚本，但只有聚合流程实际执行到该阶段才能据此记为通过。

## 专项输出：内核栈预算

独立命令：

```bash
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
make -C baseline_ucore kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

成功输出包含三类记录：

```text
kernel stack budget: user=... interrupt=... margin=... required=... limit=...
kernel stack user path: ...
kernel stack interrupt path: kernelvec -> ...
```

结论：该检查不是只在上述独立命令运行。根目录和 `baseline_ucore/` 的 `build/kernel` 都会在链接前执行同一脚本；预算超限、未建模递归/间接调用或超大单帧会直接使构建失败。当前 pipe 安全主体委派改动后的增强目标结果为 `user=7488`、`interrupt=2272`、`margin=4096`、`required=13856 < limit=16384`。运行时不可映射 guard page 与 canary 提供第二层防护。

## 基础兼容抽测：ch3_trace

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
- 仓库内容未包含敏感凭据字符串；
- 仓库内容没有旧版内核关键字；
- 仓库内容没有旧版目录或旧测试入口残留。

## 当前聚合验证状态

当前不据独立专项结果宣称 `make full-verify` 全绿。上述安全机制只按各自脚本和通过标记记录；`run-full-verification.sh` 已串联线程资源专项，但本次改动后未执行该聚合入口。`fs-enospc-test` 还必须始终单独执行，因为聚合脚本当前没有调用它。

## 2026-07-21 workflow scope 安全回归

本轮在冻结快照上分别运行 `make agentos-test`、`make fs-enospc-test`、`make proc-reap-test`、宿主提取器与存储策略测试、`make agentos-platform-build`、平台 QEMU 挂载抽测，以及增强目标和对照目标的 `kernel-stack-check`。没有把这些独立通过项误记为 `make full-verify` 全绿。

关键结果：

- Agent 专项 15/15 通过；`agentsecurity_ucore` 输出 `trusted_span_authority=1`、`trusted_cause_attribution=1`、`audit_authority_partition=1` 和 `parent passed`。
- `agentscope_ucore` 输出 scope、IPC、metadata reload、三 writer 事务竞争、存储配额、action/audit/lease 隔离、容量保留、一次性 fd 委派、生命周期回收全部标记及 `parent passed`。
- ENOSPC 的容量契约、普通耗尽、当时的运行期存储配额和系统保留量用例全部通过；该轮尚未包含后来新增的稳定 PUBLIC principal 同镜像重启回归。进程回收的 baseline、Agent 和 adversarial 用例全部通过。
- 宿主提取器的路径穿越拒绝、旧输出清理、scope 选择隔离和存储策略 C 单测通过；相关 Python 文件通过语法检查。
- AgentOS 平台构建通过，实际存储契约为 block `G=1195/S=512`、inode `G=342/S=64`；QEMU 成功挂载并启动 `rp_agentos_orch`，有界超时前无 panic 或非法文件系统。
- 增强目标内核栈预算 `14144 < 16384`，对照目标 `8144 < 16384`；最终 `git diff --check` 通过且无残留 QEMU/构建进程。

原始日志保存在仓库同级工作目录的 `scope-169-*.log` 文件中。

## 2026-07-22 syscall 公平性回归

执行：

```bash
make syscall-fairness-test TOOLPREFIX=riscv64-linux-gnu-
```

关键输出：

```text
[syscall-fairness] agent passed console=2997/5046/68536 inode=68561/68611/68635 trunc=68658/68683/68707
[syscall-fairness] baseline passed console=2997/5046/68536 inode=68561/68611/68635 trunc=68658/68683/68707
[syscall-fairness] both targets passed
```

测试没有依赖宿主输入注入。历史轮日志证明两个目标都在一次 64 KiB 控制台 `write()` 返回前调度 Guest pipe gate 后的同级进程；随后终审把 inode 和 truncate 契约改为 last-syscall 重调度计数加独立 observer，用计数证明 64 KiB `write()` 与 `O_TRUNC` open 内部跨过调度边界，并用 observer 证明 peer 读到已提交数据和原子 EOF 可见。最外层父进程等待测试 worker 完整退出后才输出 `parent passed`，宿主 runner 要求 QEMU 正常关机。本次线程资源域改动后已重新执行 `make syscall-fairness-test` 并通过双目标终审契约。

基础机制轮次还独立通过 `make agentos-test` 15/15、当时的 `make fs-enospc-test` 和 `make proc-reap-test`。7 月 22 日审查后又补入 fork 逐页计费与 VM snapshot 屏障、Agent size 非阻塞发布 sidecar、baseline exec epoch、last-syscall 重调度观测、强化的 inode/ENOSPC/退出断言及结构检查；随后新增稳定 PUBLIC principal、挂载孤儿清扫与账本重建及三启动 `fspquota_ucore`。当时版本的 Agent 15/15 与双目标同镜像 crash/seed/verify QEMU 已通过，不能由旧结果替代的物理孤儿回收和持久计费证据现已补齐；构建期内核栈预算为增强目标 `14432 < 16384`、对照目标 `8336 < 16384`。块 I/O policy、分块 metadata COW、resumable scope reclaim、scheduler 中断窗口和 terminal teardown 随后补齐；线程资源域改动前的冻结源码在这些修改后以 `337.1s` 完成 Agent 专项 16/16，并保留下述独立机制证据。

## 2026-07-22 metadata 合并写回回归

执行：

```bash
TOOLPREFIX=riscv64-linux-gnu- CASE_TIMEOUT=240s bash scripts/run-agent-tests.sh
```

当时工作区从 clean user/kernel 构建开始完成整套 Agent QEMU 回归，15/15 通过，最终输出 `[agent-tests] all Agent-OS uCore checks passed`。构建期内核栈预算同时通过，本轮增强目标为 `required=14128 < limit=16384`。

写回与跨域进展的实际输出：

```text
agentscope_ucore: metadata_write_coalescing=1 writes=131 commits=4
agentscope_ucore: metadata_cross_scope_progress=1 queries=32 latency_ms=3626
agentscope_ucore: metadata_final_consistency=1
agentscope_ucore: metadata_volatile_no_writeback=1 writes=32
agentscope_ucore: metadata_scan_pressure_bounded=1
agentscope_ucore: parent passed
```

`writes=131`、`commits=4` 和 `latency_ms=3626` 是该轮调度时序下的观测值，不是固定常量。测试契约要求每次变化进入 scope-local 记账，`commits > 0`、`commits * 8 <= requests`、`coalesced + commits == requests`，安静后 `dirty == durable` 且 pending 清零。随后强制重载 bank，size 和文件代数保持一致。另一 workflow 在 writer 存活屏障与显式停止之间完成 32 次查询，证明数据路径不再逐次同步执行完整 metadata checkpoint。volatile 微写前后的 request/commit 计数完全不变，满表未绑定对象的扫描轮次满足 cooldown 上界。当前实现仍复用 `agent_file_writeback_rest_deadline()`，但它只返回固定合并窗口；checkpoint 的物理占用改由稳定 sponsor 的硬 `BACKGROUND` I/O budget 限制，不再按 checkpoint 自身耗时做四倍休整。scanner 的 `max(20 tick, 4 * 扫描耗时)` 自适应 cooldown 保持不变。该历史 QEMU 轮没有覆盖新的分块 COW/budget 组合。

显式 metadata 事务边界的新增输出：

```text
agentfs_ucore: partial_update_binding=1
agentfs_ucore: preload_create_query=1
agentfs_ucore: selector_consistency=1
agentfs_ucore: stale_identity_guard=1
agentfs_ucore: parent passed
```

`partial_update_binding` 覆盖启动早期只更新 STATUS 的请求仍绑定调用者指定的真实 `dev + inum + incarnation`，并在重载后保持身份；`preload_create_query` 覆盖首次显式 metadata 操作之前创建的文件最终由有界后台扫描纳入查询。后两个标记分别覆盖 fid 与 path 指向不同对象时返回冲突，以及旧 inode identity 不能修改或删除路径复用后的新对象；失败请求不会提前协调、误删或持久化其他记录。

## 2026-07-23 块 I/O 与 buffer cache 分域回归

当前源码已把 `iobudget_ucore` 加入 `scripts/run-agent-tests.sh`。测试通过 syscall 544 读取 I/O policy ABI v3，先验证 `version`、当前 `struct_size`、较短旧前缀和尾部哨兵不被覆盖，再要求 PUBLIC 冷工作集实际触发 throttle、wait 和 eviction；同时验证 owner/shared/device lease 不超过各自 burst、线程退出归还未提交 lease、唯一 runnable 内核 pipe waiter 下 timer/device 中断仍可推进、page fault 退出时未链接文件回收 I/O 仍归因且结清两级 debt、PUBLIC 物理传输不存在未归因增长，以及独立 workflow 的热块仍命中 cache、Orchestrator 使用 `CONTROL` class 并完成物理写入。用户 wrapper 自动把当前结构大小作为第二参数；内核至少接收固定的 8 字节 `version + struct_size` 头，并只 copyout 用户声明大小与当前大小中的较小值，所以旧用户库可以继续读取其已知前缀。

最终 teardown 修复后的独立 QEMU 观察到：

```text
iobudget_ucore: thread_exit_lease_cleanup=1
iobudget_ucore: scheduler_interrupt_progress=1
iobudget_ucore: fault_exit_cleanup=1
iobudget_ucore: public_budget_shared=1
iobudget_ucore: nested_io_attribution=1
iobudget_ucore: cache_scope_isolation=1
iobudget_ucore: workflow_bounded_progress=1
iobudget_ucore: control_reserve_progress=1
iobudget_ucore: parent passed
```

最终修复后的独立运行 `elapsed=2.4s`。ABI sized-copy、线程退出 lease、scheduler 中断交付、fault 退出清理、PUBLIC budget/shared 上界、完成归因、cache 服务隔离、workflow 进展和 CONTROL class 共九类实质断言；日志中是八个具名机制 marker 加 `parent passed`。2026-07-24 的 pipe 安全主体委派改动后，完整 Agent 脚本以墙钟约 `359.4s` 完成 16/16。ABI v3 的设备 burst/refill 为 560/280：普通流量必须取得根信用，SYSTEM/CONTROL 可在根信用耗尽时带 device debt 前进，因此根 bucket 不是保护流量的硬总上限。静态 envelope 只约束配置总和。cache 的 SYSTEM/PUBLIC/active workflow floor/cap 为 40/96、24/48、36/64，退役清理 job 临时为 3/8。

当前完整轮的 `agentscope_ucore` 观察到 `metadata_txn_contentions=3`、`metadata_cross_scope_progress=1 queries=32 latency_ms=684`、metadata transaction/COW、微写合并、最终一致性、容量、`lifecycle_reclamation=1` 和 `parent passed`，`elapsed=139.9s`。`NPROC` 身份账本只复用 `used == 0` 的记录，active 最多 4 个且 active + retiring 不超过 8；全部 active 退出积压时最多 8 个退役任务占用最多 8 个 FS reclaim cursor，并由 reaper 在 `NPROC` 身份账本范围轮转选择清理。

当前动态 I/O 用例只覆盖一个 PUBLIC 与一个 workflow Orchestrator `CONTROL` owner；没有断言 `shared_grants` 或排队轮转，也未覆盖 Recovery、SYSTEM/workflow `BACKGROUND`、多 workflow 同时压力、retiring 3/8、跨 owner LRU/transient、主动 device-debt 注入，以及启动 bank 损坏、VirtIO 设备错误/短 I/O/metadata COW 中途掉电。线程资源域改动前的冻结源码曾以 `75.1s` 通过 `make fs-enospc-test` 的 quota/domain/persistent principal/orphan/reboot 全流程，但其中没有专门在 grouped qmap claim 中点断电。以上历史专项仍不等于本次改动后的 `make full-verify` 全绿。

## 2026-07-24 观测查询预算与索引回归

本次把 audit 物理记录表从查询入口降为存储后端：每个 workflow scope 维护 sequence 与 `(tick, sequence)` 两个 128 槽有序索引，覆盖记录统一先 unlink 再 publish；ledger 窗口摘要直接读取索引状态。audit、span 和 provenance 沿 sequence 索引单遍扫描，timeline 对四个有序来源做线性归并。计数、过滤和复制路径在扫描前按每 16 条候选记录换算 `kernel_work` 预算，单次 checkpoint 不超过一个工作量子；让出后重新统计来源并补足增长差额。预算安全点不落在 timeline wait 的未命中扫描与等待者登记之间，公共 ABI 也没有增加内部扫描量字段。

`agentscope_ucore` 由低权限 Sentinel 填满 128 条 Context 后反复执行 span、audit-only timeline 和 provenance 计数/复制查询。测试逐轮要求查询产生内核重调度证据，并验证 span 记录 sequence 单调、timeline 按 `(tick, sequence)` 排序、记录不重复且没有越过 scope/span 可见边界。压力持续期间，父进程从发送命令前开始计时，驱动另一 scope 完成 32 次查询和完整回复，避免只测到子进程启动后的局部窗口。

```text
agentscope_ucore: observe_query_bounded=1 context=128 loops=12 preemptions=64
agentscope_ucore: observe_index_ordered=1
agentscope_ucore: observe_cross_scope_progress=1 queries=32 latency_ms=3
agentscope_ucore: parent passed
```

验证命令及结果：

```text
AGENT_TEST_CASE=agentscope_ucore CASE_TIMEOUT=240s scripts/run-agent-tests.sh
[agent-tests] agentscope_ucore: elapsed=139.0s
[agent-tests] agentscope_ucore passed

CASE_TIMEOUT=240s scripts/run-agent-tests.sh
[agent-tests] agentscope_ucore: elapsed=128.1s
[agent-tests] all Agent-OS uCore checks passed
elapsed=338.4s
```

构建期内核栈预算为 `user=7456`、`interrupt=2272`、`margin=4096`、`required=13824 < limit=16384`。代码、安全和测试子代理完成最终复核，未发现阻塞提交的问题。本轮没有运行 `make full-verify`。

## 2026-07-24 pipe 安全主体委派回归

本次把 pipe 从同 scope 环境继承改为显式的单跳对象 capability。`agent_create_role()`、`agent_worker_create()`、`agent_workflow_create()` 以及 workflow/可信 bootstrap 动态 scope 的降权普通 fork 都是安全主体边界；只有创建线程在 syscall 542 中签发一次性票据的端点才进入子主体。票据移入 `struct thread`，两个线程的授权集不会因 spawn 先后交叉；内核在 VM 复制可能让出之前原子固定精确 file 对象并消费发起线程票据，关闭旧 fd、复用相同槽位、失败创建或 exec 都不能把授权留给后续主体。file 对象增加显式继承类别，stdio、逐操作重鉴权 inode、显式委派 pipe 分开处理，未知类型默认拒绝。普通 PUBLIC init 的 resource-domain admin 只影响资源记账，父子仍属同一安全主体并保留 POSIX pipe 继承。现有测试和场景中确需协作的端点均改为逐次显式授权。

`agentscope_ucore` 的动态断言覆盖：未重新授权时 Artifact 看不到 bootstrap 回复 pipe；两个线程分别签不同端点并交错 spawn 时各子主体只见本线程授权；由非主线程发起 fork 的子主体还能创建并回收新线程，验证子线程栈槽不依赖 tid 推导且不会覆盖复制地址空间中的既有栈；显式端点可真实传输一个字节且只用一次；无效创建、关闭 fd 后同槽复用都不会遗留票据。`exec` 清票由统一映像安装/凭据重置路径的实现审计确认，本轮没有为它设置单独动态断言。`agentvfs_ucore` 另覆盖 worker 的无票据、单次授权和消费后拒绝。下列标记是本轮验收门槛：

```text
agentscope_ucore: pipe_redelegation_isolation=1
agentscope_ucore: transactional_fd_delegation=1
agentscope_ucore: parent passed
```

验证命令及结果：

```text
AGENT_TEST_CASE=agentscope_ucore CASE_TIMEOUT=300s bash scripts/run-agent-tests.sh
[agent-tests] agentscope_ucore: elapsed=137.6s
[agent-tests] agentscope_ucore passed

CASE_TIMEOUT=300s bash scripts/run-agent-tests.sh
[agent-tests] all Agent-OS uCore checks passed
elapsed=359.4s

bash scripts/run-proc-reap-tests.sh
[proc-reap] both targets passed

bash scripts/run-file-resource-tests.sh
[file-resource] both targets passed

bash scripts/run-thread-resource-tests.sh
[thread-resource] all checks passed
```

构建期内核栈预算为 `user=7488`、`interrupt=2272`、`margin=4096`、`required=13856 < limit=16384`。本轮没有运行 `make full-verify`。

## 2026-07-23 线程资源域与域级公平调度回归

本次机制把线程槽纳入不可变进程资源域，并把运行队列改成 active-domain FIFO 与域内线程队列两级结构。进程 admission 原子预扣 t0；额外线程按 ordinary/reserved 类别计费，创建失败与退出沿统一路径退款。外层每个 active 域只有一个队列节点，Agent 评分和 burst 只在选中域内生效。

执行：

```bash
make thread-resource-test TOOLPREFIX=riscv64-linux-gnu-
```

专项构建使用 `pool=19 ordinary=12 reserved=6 domain=6/4`。策略头通过静态断言要求普通域上限严格小于普通全局水位、保留域上限严格小于保留全局水位，保证单域测试不能退化为命中全局边界。关键输出：

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

runner 要求 12 项机制标记按序且唯一。普通全局场景让第三个 ordinary 域仍低于单域上限时，由全局普通水位拒绝继续创建；保留全局场景跨两个 reserved 域，二者均低于单域上限且物理池仍有 1 个空槽时，由全局保留水位拒绝继续创建。公平性场景要求 victim 完成 512 次让出，固定计入启动和停止各 32 轮余量，因此攻击域 yield-loop 总计数上界为 576。`capacity_reject_stable` 只证明容量拒绝不会污染线程计数，不代表注入或覆盖了映射失败。runner 还要求 QEMU 在 `parent passed` 后正常结束且日志无 panic/fault；该专项已通过。

同一代码改动后还实际通过默认 AgentOS 构建、单独 `agentsched_ucore`、`run-proc-reap-tests.sh` 双目标（含 adversarial Agent）、`run-syscall-fairness-tests.sh` 双目标和 `run-file-resource-tests.sh` 双目标。AgentOS 构建期栈预算为：

```text
kernel stack budget: user=7328 interrupt=2256 margin=4096 required=13680 limit=16384
```

同一代码改动后还以 `CASE_TIMEOUT=300s bash scripts/run-agent-tests.sh` 完成 16/16，墙钟约 `321s`。本次没有运行 `make full-verify`，因此不把专项与 Agent 聚合脚本结果扩写为全仓验证全绿。
