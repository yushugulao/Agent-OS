<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 测试记录

本文只记录可复现的测试输出。评审结论、测试覆盖情况和性能数据归纳见 [verification.md](verification.md)。

## 任务四、任务五和综合场景验证记录

| 项目 | 内容 |
| --- | --- |
| 分支 | `Wang` |
| 环境 | WSL2 Ubuntu 26.04 |
| 构建命令 | `make user/_labdemo user/_labbench fs.img`、`make kernel/kernel` |
| 运行命令 | `make qemu` 后连续执行两次 `labdemo`，再执行 `labbench` |
| 结果 | 通过 |

`labdemo` 关键输出摘录：

```text
$ labdemo
labdemo: Agent-OS lab recovery demo
labdemo: init project=lab-gene-x run=RUN-042
labdemo: sentinel state=WAITING
labdemo: inject failure stage=align reason=OOM
labdemo: sentinel event type=FILE_STATUS payload=fid=4;status=failed;stage=align;run_id=RUN-042;truncated=0
labdemo: query_file project=lab-gene-x run=RUN-042 status=failed hits=1 scanned=1 used_index=1 first=lab_RUN042_align_err
labdemo: unauthorized rerun by sentinel status=DENIED
labdemo: investigator reason="memory limit exceeded at align stage"
labdemo: affected stages=align+analyze+report+archive
labdemo: unaffected stages=prepare
labdemo: recovery check capability rerun_stage=allow
labdemo: rerun stage=align status=OK corr_id=RUN-042-align-rerun-1
labdemo: duplicate corr_id=RUN-042-align-rerun-1 status=DUPLICATE
labdemo: rerun stage=report status=OK corr_id=RUN-042-report-rerun-1
labdemo: report metadata updated artifact=lab_RUN042_recovery_report
labdemo: final report_query hits=1 used_index=1 scanned=9
labdemo: final status=RECOVERED
agentos:event type=FINAL status=RECOVERED
labdemo: passed
```

同一 QEMU 会话中第二次运行 `labdemo` 也输出 `labdemo: passed`。

`labbench` 关键输出：

```text
$ labbench
labbench: Agent-OS task4/task5 benchmark
labbench: file_semantics scan_hits=2 index_hits=2 scan_scanned=112 index_scanned=2 report_scanned=9 empty=0 truncated=1 fid=1
labbench: metadata_dependency=1 scoped_rerun=1 scoped_report=1 history_preserved=1 single_report=1 mask_text=1 dep_clear=1 insert=1 delete=1
labbench: loop_timeout=1 heartbeat_timer=1 heartbeat_stop=1 unwatch=1 heartbeat_interval=0 last_heartbeat=42
labbench: duplicate_reject attempts=2 executed=1 rejected=1 ticks=0
labbench: case ops ticks ops_per_tick speedup_x100
labbench: file_scan_query ops=32768 ticks=36 ops_per_tick=910 speedup_x100=100
labbench: file_index_query ops=32768 ticks=22 ops_per_tick=1489 speedup_x100=163
labbench: busy_poll_query ops=512 ticks=0 ops_per_tick=512 speedup_x100=100
labbench: scalar_tool_call ops=8192 ticks=3 ops_per_tick=2730 speedup_x100=100
labbench: batch_agent_run ops=8192 ticks=0 ops_per_tick=8192 speedup_x100=300
labbench: context_query ops=512 ticks=0 ops_per_tick=512 speedup_x100=100
labbench: context_snapshot ops=65536 ticks=1 ops_per_tick=65536 speedup_x100=12800
labbench: capability_check ops=32768 ticks=9 ops_per_tick=3640 speedup_x100=100
labbench: permission_denied self_escalation=1 wake=1 meta=1 rerun=1 report=1
labbench: event_context_records=128 latest=513
labbench: non_target_timeout=1
labbench: event_wait_wake ops=512 ticks=2 ops_per_tick=256 speedup_x100=100
labbench: event_fifo queued=8 dropped=1 ordered=1
labbench: send_message_overflow queued=8 dropped=1 rollback=1
labbench: file_status_partial_payload fid=4 stage=align run_id=RUN-042 full_lookup=1
labbench: file_status_overflow queued=8 dropped=1 no_space=1
labbench: passed
```

验证点：

| 验证项 | 结果 |
| --- | --- |
| Sentinel 注册 watch 后等待文件失败事件 | `labdemo: sentinel state=WAITING` |
| 文件状态变化唤醒目标 Agent | `sentinel event type=FILE_STATUS` |
| 属性查询定位失败工件 | `status=failed hits=1 first=lab_RUN042_align_err` |
| 查询使用索引路径 | `used_index=1 scanned=1` |
| 失败摘要来自故障工件 | `investigator reason="memory limit exceeded at align stage"` |
| 依赖查询支持最小恢复 | `affected stages=align+analyze+report+archive`、`unaffected stages=prepare` |
| 权限拒绝可复现 | `unauthorized rerun by sentinel status=DENIED` |
| 幂等拒绝可复现 | `duplicate ... status=DUPLICATE` |
| 恢复和报告元数据更新完成 | `final status=RECOVERED` |
| 多索引候选选择有效 | `scan_scanned=112 index_scanned=2 report_scanned=9` |
| 文件 scan/index 有性能对比 | `file_index_query speedup_x100=163` |
| dependency/rerun 关键行为可复现 | `scoped_report=1 single_report=1 mask_text=1 dep_clear=1 insert=1 delete=1` |
| 权限自升权被拒绝 | `permission_denied self_escalation=1` |
| event wait/wake 压测通过 | `event_wait_wake ops=512`、`event_context_records=128`、`non_target_timeout=1`、`heartbeat_stop=1`、`unwatch=1`、`event_fifo queued=8 dropped=1 ordered=1`、`send_message_overflow`、`file_status_partial_payload ... full_lookup=1`、`file_status_overflow`、`labbench: passed` |

说明：`labdemo` 内部使用进程共享打印锁，普通日志和 `agentos:event` 行按整行输出；同一 QEMU 会话连续两次运行均以 `labdemo: passed` 和 `agentos:event type=FINAL status=RECOVERED` 为通过标志。

## 任务一至三验证记录

| 项目 | 内容 |
| --- | --- |
| 分支 | `Wang` |
| 环境 | WSL2 Ubuntu 26.04 |
| 构建命令 | `make fs.img`、`make kernel/kernel`、`make user/_agentcall user/_contexttest user/_agentstress user/_agentexec user/_agentfinal user/_agentbench` |
| 运行命令 | `make qemu` 后依次执行 `agentfinal`、`agentcall`、`contexttest`、`agentstress`、`agentbench`、`agentexec` |
| 结果 | 通过 |

关键输出：

```text
$ agentfinal
agentfinal: context size=16384 capacity=128
agentfinal: batch first_seq=1 last_seq=64
agentfinal: short_text_history=1 payload=final result=final
agentfinal: snapshot count=64 latest=64
agentfinal: direct_dirty_before_snapshot=1
agentfinal: tamper_protected=1
agentfinal: fifo oldest=65 latest=192 dropped=64
agentfinal: direct_context_match=1
agentfinal: passed
$ agentcall
tool read_context: status=0 seq=8 count=8 head=8 calls=8
agent bad_output: no_side_effect calls=17 context=17
agent lazy_output: legacy=1 batch=1
history: count=128 head=14 total=142 capacity=128
agentcall: strict validation passed
$ contexttest
contexttest: fifo oldest=3 latest=130 dropped=2
contexttest: short_text_history=1 payload=manual-in result=manual-out
contexttest: rollback_not_found=-5
contexttest: rollback latest=10 branch_latest=131
contexttest: passed
$ agentstress
agentstress: exec_failure_preserved=1
agentstress: create_exit=12
agentstress: sbrk_boundary_steps=255
agentstress: normal_context_fault=status -1
agentstress: parent_over_context_rejected=1
agentstress: passed
$ agentbench
agentbench: scalar_run ops=65536 ticks=18 ops_per_tick=3640 speedup_x100=100
agentbench: batch_run ops=65536 ticks=2 ops_per_tick=32768 speedup_x100=900
agentbench: direct_context ops=1000000 ticks=0 ops_per_tick=1000000 speedup_x100=27465
agentbench: context_query ops=2048 ticks=1 ops_per_tick=2048 speedup_x100=56
agentbench: context_snapshot ops=262144 ticks=3 ops_per_tick=87381 speedup_x100=4266
agentbench: passed
$ agentexec
agentexec: wrapper status=0
```

验证点：

| 验证项 | 结果 |
| --- | --- |
| Agent Context 当前布局 | `agentfinal: context size=16384 capacity=128` |
| 批量工具调用 sequence 连续 | `agentfinal: batch first_seq=1 last_seq=64` |
| Context Path 保存短文本 payload/result 摘要 | `agentfinal: short_text_history=1`、`contexttest: short_text_history=1` |
| 直接 Context 读是镜像，snapshot 刷新可信历史 | `agentfinal: direct_dirty_before_snapshot=1`、`agentfinal: tamper_protected=1` |
| FIFO 淘汰元信息正确 | `agentfinal: fifo oldest=65 latest=192 dropped=64` |
| `read_context` 返回本次追加后的 post-state | `count=8 head=8 calls=8` 与 `seq=8` 对齐 |
| 合法 lazy 输出页可通过 writable-prefault 使用 | `agent lazy_output: legacy=1 batch=1` |
| 父进程堆越过 Agent Context 后创建 Agent 被拒绝 | `agentstress: parent_over_context_rejected=1` |
| 性能测试输出吞吐对比 | `agentbench: passed` |
| 任务一至三复测仍通过 | `agentfinal`、`agentcall`、`contexttest`、`agentstress`、`agentbench`、`agentexec` 均通过 |
