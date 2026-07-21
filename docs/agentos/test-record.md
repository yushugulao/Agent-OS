# 测试记录

测试目标：根目录 AgentOS-uCore 增强目标

本次最终复测日期：2026-07-21

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

独立专项记录：脚本依次运行 `agentfinal_ucore`、`agentfs_ucore`、`agentscan_ucore`、`agentloop_ucore`、`agentsched_ucore`、`agentconflict_ucore`、`agentllm_ucore`、`agentbench_ucore`、`labbench_ucore`、`labdemo_ucore`、`agentsecurity_ucore`、`agenttrust_ucore`、`agentvfs_ucore`、`usersafety_ucore`。专项运行要求每个程序在超时前输出 `parent passed`，且日志中不存在 `check failed`、`panic`、`unknown syscall`、`bad addr`、`IllegalInstruction` 或 `child_failed`。2026-07-21 完整脚本通过；其后增强的 `agentloop_ucore` 和 `agentsecurity_ucore` 边界断言也分别完成 QEMU 复测并通过。该记录不等于 `make full-verify` 全绿，聚合入口的已知状态见文末。

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
| `agentfs_ucore` | `.agentmeta_reload=1`、`bulk_index scan=118 index=6`、`digest_cache_invalidated=1` | 真实文件元数据、索引查询和内容摘要缓存可用 |
| `agentscan_ucore` | `background_scan usershell=1`、`auto_file_create=1`、`auto_file_delete=1` | 根目录真实文件能被扫描并同步到 Agent metadata |
| `agentloop_ucore` | `message_source_limit=4`、`ipc_class_limit=8`、`external_limit=12`、`system_event_reserved=4`、`external_reject_reclaim=1`、`broadcast_slow_watcher_isolated=1` | FIFO、睡眠等待、heartbeat、directed 单来源=4、directed 类=8、external=12、第 13 条 external 拒绝、4 条 KERNEL TIMER 保留容量、消费后重接纳和慢 watcher 隔离均通过 |
| `agentsched_ucore` | `role_weights ...`、`event_priority=1`、`reason_trace=1` | Agent 感知调度和调度原因记录可用 |
| `agentconflict_ucore` | `conflict_denied=1`、`direct_write_denied=1`、`stale_commit=1` | 文件编辑冲突由内核真实文件路径阻止 |
| `agentllm_ucore` | `relay_timeline=1`、`requester_done=1` | LLM Relay 事件、唤醒和 timeline 摘要可用 |
| `agentbench_ucore` | `batch_agent_run`、`file_index_query`、`timeline_query_prefetch` | 性能主路径和文件索引/Timeline 查询可观测 |
| `labdemo_ucore` | `type=INCIDENT_CREATED`、`prefetch_handoff=analyze`、`provenance_graph edges=...` | 设定的模拟流程 多 Agent 恢复场景可复现 |
| `agentsecurity_ucore` | `route_source_enforced=1`、`route_target_isolated=1`、`ipc_route_authorization=1`、`message_route_lifecycle=1`、`target_route_consent=1`、`route_slot_reclaimed=1` | 系统事件防伪、未授权注入拒绝、grant/revoke、target LLM_DONE consent、MESSAGE 位图隔离、stable control id 生命周期和 source 退出槽回收均通过 |
| `agenttrust_ucore` | `wx_image=1`、`immutable_image=1`、`role_image_binding=1` | W^X、可信映像不可变和 Agent 角色映像绑定可验证 |
| `agentvfs_ucore` | `inherited_fd_revalidated=1`、`protected_paths=1` | 普通 VFS 路径不能绕过文件能力，继承描述符会按当前凭据重新鉴权 |
| `usersafety_ucore` | `live after pointer bounds`、`live after directed wakeup`、`parent passed` | syscall 输入检查、定向唤醒和失败事务回滚可验证 |
| `fsenospc_ucore` | `inode exhaustion survived`、`block exhaustion survived` | inode、inode cache 与数据块耗尽返回失败而非触发内核 panic |
| `procreap_ucore` / `procreap_agent_ucore` | `live-domain-limit=1`、`reserved-agent-slot=1` | 进程回收、资源域配额与系统保留槽可验证 |
| 内核栈预算 | `kernel stack budget`、`kernel stack user path` | 构建期 callgraph/栈帧预算检查随内核构建执行 |

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
agentfs_ucore: demo_inode dev=1 inum=14 scanned=2
agentfs_ucore: prefetch_hints=1 count=3 first_stage=analyze source_seq=1
agentfs_ucore: scoped_dependency=1 设定的模拟流程=align+analyze+report runalt=align+archive
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
agentfs_ucore: dependency_update=1 result=align+review generation=...
agentfs_ucore: clear_status=1 cache_invalidated=1
agentfs_ucore: delete_clears_metadata=1
agentfs_ucore: missing_selector_not_found=1
agentfs_ucore: passed
agentfs_ucore: parent passed
```

结论：文件元数据可绑定真实根目录文件；查询结果携带 `dev`、`inum`、`incarnation`、`size`，其中安全绑定、缓存与编辑版本以 `dev + inum + incarnation` 区分 inode 的不同生命周期，样例日志输出可见的 inode 和大小字段。用户态写入的 align 元数据查询后会产生后续 label metadata 预取提示；用户态也可通过 `dependency_update` 显式注册通用对象依赖，再用 `dependency_query` 读取；自定义 metadata 可从私有 `.agentmeta` 重新加载；真实文件内容摘要可以被缓存，改写文件后旧 digest 缓存不会返回过期内容；内容摘要工具调用会进入统一 timeline，页面可按 `tool_id=20` 读取 size、bytes、hash 和 preview；接近 128 条记录时 scan/index 的 `scanned_records` 差异可见；query plan 能说明索引路径按 status 索引选择 bucket 15 并检查 6 条候选记录；重复查询会命中同一 `fs_generation` 下的结果缓存，属性更新后旧缓存不会返回过期结果；属性清空、文件删除同步和不存在 selector 返回 `NOT_FOUND` 均通过。

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
agentvfs_ucore: inherited_fd_revalidated=1
agentvfs_ucore: protected_paths=1
agentvfs_ucore: parent passed
```

结论：公共文件与工作流受保护文件可以同名存在且互不覆盖；普通进程和无文件能力的 Agent 不能借 `open/read/write/unlink` 绕过授权，investigator 只有读权限。普通 `fork()` 会降权，继承的已打开描述符也会在使用时按当前凭据重新鉴权。普通进程可以执行布局有效的 worker 映像，但不会因此提权；orchestrator 的 worker 委派受 immutable/domain-safe 映像属性、VFS profile 能力上限和精确 inode 绑定共同约束。失败的创建/截断意图在文件系统发生变更前返回。文件安全身份以 `dev + inum + incarnation` 区分 inode 生命周期。

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

两类通用目标均需出现前三组程序标记；AgentOS 配额场景还需出现版本生命周期、工作流保留和清理标记：

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
[fs-enospc] generic targets and Agent quota cases passed
```

结论：磁盘 inode、内存 inode cache 和数据块耗尽均通过正常错误返回或短写报告，未转化为全内核 panic；释放资源后可以重新分配。PUBLIC 域完成超过旧版本表容量的 640 次短命 inode 循环后，workflow 仍能取得编辑版本并命中内容摘要缓存，说明版本 sidecar 随最终 inode 生命周期回收，且不能跨 inode 槽侵占 Agent 保留资源。此测试单独覆盖 AgentOS-uCore 与 `baseline_ucore/` 的通用 ENOSPC，并额外覆盖 AgentOS 资源域，但当前没有被 `make full-verify` 串联。

## 专项输出：进程回收与配额

执行：

```bash
make proc-reap-test TOOLPREFIX=riscv64-linux-gnu-
```

关键输出：

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

结论：该检查不是只在上述独立命令运行。根目录和 `baseline_ucore/` 的 `build/kernel` 都会在链接前执行同一脚本；预算超限、未建模递归/间接调用或超大单帧会直接使构建失败。运行时不可映射 guard page 与 canary 提供第二层防护。

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

当前不记录 `make full-verify` 全绿。最近一次运行在宿主机 Reader E2E 阶段中止：`host_tools/test_plain_ucore_reader_e2e.py` 断言 API Catalog 页面应包含 `Reader GET Routes`，而实际页面缺少该文本。由于脚本使用 `set -eu`，该失败发生后不会继续运行双目标 QEMU、AgentOS 专项和进程回收阶段。

因此，上述安全专项应按各自独立脚本和通过标记记录；`fs-enospc-test` 还必须始终单独执行，因为 `run-full-verification.sh` 当前没有调用它。后续修复 Reader 页面或测试契约后，应重新运行完整入口，再更新本节状态。
