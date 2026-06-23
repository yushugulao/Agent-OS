<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 任务五：Agent Loop 内核运行机制

本文说明当前任务五实现。目标是让 Agent 能注册关注条件、进入等待状态，并由内核事件唤醒，而不是在用户态持续轮询。

## 设计目标

当前阶段实现以下能力：

- `agent_watch(event_type, filter)` 注册事件类型和短文本过滤器。
- `agent_unwatch(event_type, filter)` 删除匹配 watch，或清空全部 watch。
- `agent_wait(event, timeout_ticks)` 进入内核等待，事件到达或超时后返回。
- `agent_heartbeat(interval_ticks)` 设置心跳间隔，并通过 `agent_info()` 可观察最后心跳 tick。
- `agent_heartbeat_stop()` 停止 heartbeat timer 事件。
- `agent_wake(pid, event)` 提供受控手动唤醒接口，只有具备 `AGENT_CAP_EVENT_WAKE` 的 Agent 可调用。
- 文件状态变化可触发 `AGENT_EVENT_FILE_STATUS`。
- `send_message` 写 mailbox 时触发 `AGENT_EVENT_MESSAGE`。
- 被 Agent 消费的事件写入 Context Path。

## Agent 元数据

`struct proc` 中新增任务五相关字段：

- `agent_watch_valid`
- `agent_watch_event_type`
- `agent_watch_filter`
- `agent_event_head`
- `agent_event_tail`
- `agent_event_queued`
- `agent_event_type[8]`
- `agent_event_source_pid[8]`
- `agent_event_id[8]`
- `agent_event_tick[8]`
- `agent_event_corr_id[8]`
- `agent_event_payload[8]`
- `agent_event_count`
- `agent_event_dropped`
- `agent_wait_count`
- `agent_timeout_count`
- `agent_last_heartbeat_tick`
- `agent_role`
- `agent_capability_mask`

`struct agent_info` 暴露统计字段，便于用户态和评审程序观察。

## 事件类型

公开事件类型位于 `kernel/agent.h`：

| 事件 | 用途 |
| --- | --- |
| `AGENT_EVENT_FILE_STATUS` | 文件元数据状态变化，例如 `align` 失败 |
| `AGENT_EVENT_MESSAGE` | mailbox 收到 Agent 间消息 |
| `AGENT_EVENT_TIMER` | 心跳或超时相关事件 |
| `AGENT_EVENT_JOB_DONE` | 恢复动作完成 |
| `AGENT_EVENT_POLICY_DENIED` | 权限拒绝 |
| `AGENT_EVENT_CONTEXT_LIMIT` | 后续用于 Context 容量预警 |

## 等待和唤醒机制

实现位置：[kernel/agent.c](../kernel/agent.c)。

事件状态由 `agent_event_lock` 保护。每个 Agent 当前使用 8 槽 FIFO 事件队列：

- 如果队列未满，事件写入 tail 并 `wakeup(target)`。
- `agent_wait()` 从 head 按 FIFO 顺序取出事件。
- 如果队列已满，新事件不会覆盖旧事件，目标 Agent 的 `agent_event_dropped` 增加。`agent_wake()`、`send_message` 和文件状态事件投递路径都会让调用方观察到 `AGENT_STATUS_NO_SPACE`。watcher 广播采用 partial delivery 语义：某些 watcher 可能已经收到事件，某些 watcher 因队列满未收到；文件状态路径中元数据更新已经完成，调用方可重新查询元数据确认状态。
- `agent_wait()` 在没有事件时设置 `loop_state=AGENT_LOOP_WAITING` 并 `sleep(p, &agent_event_lock)`。
- 时钟中断调用 `agent_tick()` 检查 timeout 和 heartbeat；只有 timeout 到期或 heartbeat 事件需要投递时才唤醒等待 Agent，不再每个 tick 唤醒所有等待者。
- 事件投递路径也直接唤醒目标 Agent。

这种设计避免用户态忙轮询，同时保持 xv6 锁顺序简单。

## 文件状态事件

`agent_file_meta_set()` 更新文件元数据时，如果 `status` 发生变化，会向匹配 watch 的 Agent 投递 `AGENT_EVENT_FILE_STATUS`。事件 payload 只承诺短摘要字段，例如 `fid/status/stage/run_id/truncated`；完整物理名、逻辑路径和摘要通过 `agent_file_query(fid=...)` 回查。即使调用方只传 `fid/status`，内核也会先合并已有元数据，再用完整记录判断是否命中按 stage 或 run_id 注册的 watcher。该接口只允许具备元数据写 capability 的 Agent 调用，`labdemo` 中由 Orchestrator 负责初始化元数据和注入故障。

`labdemo` 中 Sentinel 注册：

```c
agent_watch(AGENT_EVENT_FILE_STATUS, "status=failed");
```

Orchestrator Agent 注入：

```text
stage=align status=failed summary="memory limit exceeded at align stage"
```

Sentinel 随后从 `agent_wait()` 返回并执行 `query_file`。

## mailbox 事件

`send_message` 工具在写目标 Agent mailbox 后调用事件投递：

```text
AGENT_EVENT_MESSAGE payload=<message>
```

因此 Investigator 和 Recovery 不需要轮询 `read_message`，可以先注册：

```c
agent_watch(AGENT_EVENT_MESSAGE, "investigate");
agent_watch(AGENT_EVENT_MESSAGE, "recover");
```

再通过 `agent_wait()` 等待对应消息。

如果目标 Agent 的消息事件队列已满，`send_message` 返回 `AGENT_STATUS_NO_SPACE`，并回滚本次 mailbox 写入，保留上一条成功消息。

## Context Path 记录

`agent_watch()`、`agent_heartbeat()`、`agent_set_role()` 和成功消费事件的 `agent_wait()` 都会追加 Context Path 记录。这样最终报告可以引用：

- 注册了什么 watch；
- 何时收到事件；
- 事件 payload 是什么；
- 后续工具调用依据哪次事件。

## 验证证据

`labdemo` 关键输出：

```text
labdemo: sentinel state=WAITING
agentos:event type=AGENT_STATE role=sentinel state=WAITING
labdemo: inject failure stage=align reason=OOM
labdemo: sentinel event type=FILE_STATUS payload=fid=4;status=failed;stage=align;run_id=RUN-042;truncated=0
agentos:event type=AGENT_STATE role=sentinel state=RUNNING payload=fid=4;status=failed;stage=align;run_id=RUN-042;truncated=0
labdemo: send_message sentinel->investigator status=OK
labdemo: send_message investigator->recovery status=OK
labdemo: final status=RECOVERED
labdemo: passed
```

`labbench` 关键输出：

```text
labbench: loop_timeout=1 heartbeat_timer=1 heartbeat_stop=1 unwatch=1 heartbeat_interval=0 last_heartbeat=42
labbench: permission_denied self_escalation=1 wake=1 meta=1 rerun=1 report=1
labbench: busy_poll_query ops=512 ticks=0 ops_per_tick=512 speedup_x100=100
labbench: event_context_records=128 latest=513
labbench: non_target_timeout=1
labbench: event_wait_wake ops=512 ticks=2 ops_per_tick=256 speedup_x100=100
labbench: event_fifo queued=8 dropped=1 ordered=1
labbench: send_message_overflow queued=8 dropped=1 rollback=1
labbench: file_status_partial_payload fid=4 stage=align run_id=RUN-042 full_lookup=1
labbench: file_status_overflow queued=8 dropped=1 no_space=1
labbench: passed
```

说明：

- `busy_poll_query` 是反复查询文件状态的轮询基线，不作为 event wait/wake 的严格速度对照。
- `event_wait_wake` 是 Orchestrator Agent 向等待 Agent 投递 512 次消息事件，等待 Agent 每次由 `agent_wait()` 返回并 ack。
- `loop_timeout=1` 验证无事件时 timeout 返回明确状态；`heartbeat_timer=1` 验证当前 watch 接收 TIMER 且 filter 匹配时，heartbeat timer 事件可以进入事件队列；`heartbeat_stop=1` 验证停止后不再投递 heartbeat 事件；`unwatch=1` 验证删除 watch 后不再接收匹配事件。
- `permission_denied` 验证普通进程不能直接创建 Recovery/Investigator 等高权限工作 Agent，低权限 Agent 也不能通过 `agent_set_role(ORCHESTRATOR)` 自升权，不能 wake、重置/修改元数据、执行恢复动作或更新报告元数据。
- `non_target_timeout=1` 验证非目标 Agent 不会被误唤醒。
- `event_context_records=128` 验证事件消费会写入 Context Path，超过容量后仍保持 128 条可见历史。
- `event_fifo queued=8 dropped=1 ordered=1` 验证突发事件按 FIFO 顺序消费，队列满时不会覆盖旧事件，并通过 dropped 计数暴露溢出。
- `file_status_partial_payload` 验证 partial metadata update 仍按合并后的真实 stage/run_id 触发 watcher，并能通过 `fid=4` 回查完整记录。
- `send_message_overflow` 和 `file_status_overflow` 验证 mailbox 与文件状态事件路径也能把满队列反馈给调用方。
- xv6 tick 粒度较粗，性能数据只作为趋势样例；任务五正确性以 wait/wake 能完成 512 次、无误唤醒、无 panic、`labbench: passed` 为准。

## 当前实现限制

- 当前事件队列是 8 槽 FIFO，适合演示和压力验证；最终成品可继续扩展队列容量和事件优先级。
- 当前 timeout 由 tick 唤醒等待者后检查，不追求高精度定时器。
- 当前 heartbeat timer 事件受 watch/filter 限制；只有当前 watch 接收 `AGENT_EVENT_TIMER` 且 filter 匹配时，timer 事件才会入队；`agent_heartbeat_stop()` 后不会继续投递 heartbeat timer 事件。
- 当前 capability mask 已进入 `agent_info`。默认 `agent_create()` 创建 Sentinel；普通进程只能通过 `agent_create_role()` 创建 Sentinel，或在没有存活 Orchestrator 时引导一个 Orchestrator；Recovery、Investigator 等工作 Agent 必须由 Orchestrator 创建。`agent_set_role()` 只能确认当前角色，不能自升权。最终成品可升级为更细的 capability manager。
