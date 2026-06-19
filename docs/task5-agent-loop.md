# 任务五：Agent Loop 内核运行机制

本文是 [design.md](design.md) 的任务五细节附录，重点说明 uCore 分支当前实现的 watch、wait、wake、heartbeat 和事件投递机制。

## 目标

任务五希望操作系统支持 Agent 长期运行机制，使 Agent 不只是主动调用工具，还能等待内核事件、被文件状态变化唤醒、按心跳维护状态，并参与多 Agent 协作。

当前 uCore 分支实现的是可验证的 Agent Loop 内核机制：

- Agent 最多可注册 8 条 watch；
- Agent 可删除指定 watch，或一次清空全部 watch；
- 每个 Agent 有 16 槽 FIFO 事件队列；
- Agent 可等待事件，有限 timeout 会进入睡眠并由 tick 唤醒，事件入队会唤醒目标 Agent；
- 其他 Agent 或内核工具可唤醒目标 Agent；
- 文件元数据状态变化可触发事件；
- Agent 可设置 heartbeat，注册 TIMER watch 后可收到 heartbeat 事件；删除 TIMER watch 后不会消费 TIMER 事件；
- Agent 可通过 `agent_heartbeat_stop()` 停止 heartbeat；
- 等待、唤醒和事件消费会写入 Context Path；
- 综合场景用三个 Agent 串联事件流程。

当前还不是完整平台级 Agent 调度器，不包含优先级、取消等待和复杂调度策略。

## Loop 状态

`struct agent_info` 中的 `loop_state` 表示 Agent 当前状态：

| 状态 | 含义 |
| --- | --- |
| `AGENT_LOOP_NONE` | 普通进程或未初始化 |
| `AGENT_LOOP_IDLE` | Agent 已创建但未等待 |
| `AGENT_LOOP_RUNNING` | Agent 正在执行工具或演示逻辑 |
| `AGENT_LOOP_WAITING` | Agent 正在等待事件 |

`agent_watch()` 会让 Agent 进入可监听状态，`agent_wait()` 会让 Agent 等待匹配事件。

## 事件类型

当前事件类型：

| 类型 | 含义 | 触发来源 |
| --- | --- | --- |
| `AGENT_EVENT_FILE_STATUS` | 文件状态变化 | `agent_file_meta_set()` |
| `AGENT_EVENT_MESSAGE` | Agent 消息 | `agent_wake()` 或消息工具 |
| `AGENT_EVENT_TIMER` | 定时或心跳 | `agent_tick()` |
| `AGENT_EVENT_JOB_DONE` | 作业完成 | 预留 |
| `AGENT_EVENT_POLICY_DENIED` | 策略拒绝 | 预留或工具结果 |
| `AGENT_EVENT_CONTEXT_LIMIT` | Context 限制事件 | 预留 |
| `AGENT_EVENT_LLM_DONE` | LLM Gateway 返回解释或摘要 | 当前预留 |
| `AGENT_EVENT_DASHBOARD_EXPORT` | 可视化导出完成 | 当前预留 |

事件结构 `struct agent_event` 包含 type、source_pid、target_pid、status、event_id、tick、corr_id 和 payload。

## Watch

接口：

```c
int agent_watch(int event_type, const char *filter);
int agent_unwatch(int event_type, const char *filter);
```

语义：

1. 当前进程必须是 Agent。
2. `event_type` 可以是具体事件类型，也可以是 `AGENT_EVENT_NONE` 表示不过滤类型。
3. `filter` 是简单短文本包含匹配。
4. 注册成功后，后续事件 payload 包含 filter 时可唤醒该 Agent。
5. 相同 `event_type + filter` 的 watch 会被替换。
6. `agent_unwatch(AGENT_EVENT_NONE, "")` 会清空当前 Agent 的全部 watch。

`labdemo_ucore` 中 sentinel 注册：

```text
agentos:event type=WATCH_REGISTERED tick=... role=sentinel event=FILE_STATUS filter=status=failed
```

## Wait

接口：

```c
int agent_wait(struct agent_event *event, int timeout_ticks);
```

语义：

1. 当前进程必须是 Agent。
2. 若已有匹配事件，立即复制到用户态并返回 `AGENT_STATUS_OK`。
3. 若没有事件，进入等待状态。
4. 超过 `timeout_ticks` 后返回 `AGENT_STATUS_TIMEOUT`。
5. 成功消费事件后追加 Context record。

当前有限 timeout 也会进入 `SLEEPING` 状态，由事件入队、heartbeat 到期或 deadline 到期唤醒，不通过反复 `yield()` 消耗 CPU。`agent_info.wait_loop_count` 用于观察等待检查次数，`agentloop_ucore` 会验证有限 timeout 的循环次数保持在很小范围。

`agentbench_ucore` 先验证无事件等待会返回 timeout，并检查 `timeout_count` 增加；随后输出 busy polling 查询和 wait/wake 的计时观测，便于用户看到轮询路径与事件路径的成本都可测：

```text
agentbench_ucore: timeout_heartbeat=1
agentbench_ucore: busy_poll_query ops=128 ticks=5 ops_per_tick=25 speedup_x100=100
agentbench_ucore: event_wait_wake ops=8 ticks=3 ops_per_tick=2 speedup_x100=100
agentbench_ucore: busy_poll_vs_wait busy_ops=128 busy_ticks=5 wait_ops=8 wait_ticks=3
```

这里的 `speedup_x100=100` 是单项自身的计时基线。`busy_poll_vs_wait` 用于展示两个路径的观测数据，不设置固定 tick 阈值。

## Wake

接口：

```c
int agent_wake(int pid, struct agent_event *event);
```

语义：

1. 查找目标 pid。
2. 检查目标是否为 Agent。
3. 检查目标 watch 是否匹配事件类型和 payload。
4. 写入目标 FIFO 事件队列。
5. 唤醒目标进程。

队列满时返回 `AGENT_STATUS_NO_SPACE`，不会覆盖旧事件。`send_message` 工具在目标队列满时也返回 `AGENT_STATUS_NO_SPACE`，并避免留下不可感知的消息副作用。

`agentfinal_ucore` 用自唤醒验证最小路径：

```text
agentfinal_ucore: event_wait=1 payload=self wake
```

`labdemo_ucore` 用跨 Agent 唤醒验证场景路径：

```text
agentos:event type=MESSAGE from=sentinel to=investigator status=OK
```

## Heartbeat

接口：

```c
int agent_heartbeat(int interval_ticks);
int agent_heartbeat_stop(void);
```

语义：

1. 当前进程必须是 Agent。
2. `agent_heartbeat(interval)` 设置心跳间隔。
3. `agent_heartbeat_stop()` 是用户态便利 wrapper，内部调用 `agent_heartbeat(0)`，停止后不再投递 heartbeat 事件。
4. 注册 `AGENT_EVENT_TIMER` watch 后，heartbeat 到期会投递 `timer=heartbeat` 事件。
5. 后续 `agent_info()` 可观察 last heartbeat tick。
6. 删除 TIMER watch 后，heartbeat 到期不会投递可消费 TIMER 事件。

当前 `agentbench_ucore` 会调用 `agent_heartbeat()`，随后用 `agent_info()` 检查 `heartbeat_interval` 和 `last_heartbeat_tick`。`agentloop_ucore` 进一步验证 heartbeat 可以唤醒等待中的 Agent、删除 TIMER watch 后不再消费 TIMER 事件、停止后不会继续产生 heartbeat 事件。

## 文件状态事件

任务四和任务五的结合点是 `agent_file_meta_set()`：

1. 具备 `AGENT_CAP_META_WRITE` 的 Agent 更新文件元数据；
2. 如果状态字段发生变化，内核构造 `AGENT_EVENT_FILE_STATUS`；
3. 内核查找匹配 watch 的 Agent；
4. 把事件投递给目标 Agent；
5. 目标 Agent 从 `agent_wait()` 返回。

`labdemo_ucore` 中：

```text
agentos:event type=INCIDENT_CREATED tick=... id=INC-RUN-042-ALIGN-OOM project=lab-gene-x workflow=nightly-regression run_id=RUN-042 stage=align reason=memory_limit
labdemo_ucore: sentinel event payload=status=failed;stage=align;run_id=RUN-042;project=lab-gene-x
```

说明文件状态变化成功唤醒 sentinel。

## 消息事件

`AGENT_TOOL_SEND_MESSAGE` 和 `agent_wake()` 都可以向目标 Agent 发送消息事件。消息事件用于多 Agent 协作。`agent_wake()` 是 Agent-only syscall，调用者必须具备 `AGENT_CAP_MESSAGE_SEND` 或 `AGENT_CAP_ORCHESTRATE`；普通进程直接调用会返回 `-1`。

`labdemo_ucore` 中两段消息：

| 来源 | 目标 | 目的 |
| --- | --- | --- |
| sentinel | investigator | 发现失败后请求调查 |
| investigator | recovery | 完成分析后请求恢复 |

## Context Path 记录

Agent Loop 行为会写入 Context Path：

- watch 注册；
- wait 成功；
- heartbeat 设置；
- message 工具调用；
- query 和 recovery 工具调用。

这使得演示不仅能看到最终结果，也能回放 Agent 做出判断的过程。

`labdemo_ucore` 中 investigator 输出：

```text
agentos:event type=CONTEXT_SNAPSHOT tick=... role=investigator records=4 latest=...
```

说明 investigator 的推理和工具调用历史可以通过 snapshot 查看。

## 当前限制

| 限制项 | 说明 |
| --- | --- |
| 调度策略 | 当前没有实现优先级、抢占策略或长期任务队列 |
| 事件容量 | 当前每 Agent 固定 16 槽 FIFO，队列满时拒绝新事件 |
| 取消机制 | 当前没有取消等待或取消任务接口 |
| 多核复杂竞态 | 当前按 uCore 当前运行环境和测试路径验证，后续可扩展更强锁设计 |
| LLM 驱动 | 当前由用户测试程序驱动，不接真实 LLM |

## 验证证据

`agentfinal_ucore`：

```text
agentfinal_ucore: event_wait=1 payload=self wake
```

`agentbench_ucore`：

```text
agentbench_ucore: timeout_heartbeat=1
agentbench_ucore: event_wait_wake ops=8 ticks=3 ops_per_tick=2 speedup_x100=100
```

`agentloop_ucore`：

```text
agentloop_ucore: fifo=1
agentloop_ucore: overflow_dropped=1
agentloop_ucore: unwatch=1
agentloop_ucore: timeout_sleep_no_poll=1
agentloop_ucore: timer_unwatch=1
agentloop_ucore: heartbeat_wake_stop=1
agentloop_ucore: passed
```

`labdemo_ucore`：

```text
agentos:event type=WATCH_REGISTERED tick=... role=sentinel event=FILE_STATUS filter=status=failed
agentos:event type=LLM_CALL tick=... mode=template task=explain_root_cause llm_request_id=LLM-RUN-042-RCA-1 project=lab-gene-x run_id=RUN-042 refs=... status=OK
agentos:event type=LLM_RESULT tick=... mode=template llm_request_id=LLM-RUN-042-RCA-1 llm_status=OK llm_explanation=memory_limit referenced_sequences=... confidence=medium
agentos:event type=PLAN_CREATED tick=... role=investigator plan=PLAN-RUN-042-RECOVER-1 project=lab-gene-x run_id=RUN-042 actions=align,report skip=prepare refs=...
labdemo_ucore: sentinel event payload=status=failed;stage=align;run_id=RUN-042;project=lab-gene-x
agentos:event type=MESSAGE tick=... from=sentinel to=investigator status=OK corr_id=MSG-RUN-042-S-I seq=...
agentos:event type=FINAL tick=... project=lab-gene-x run_id=RUN-042 status=RECOVERED plan=PLAN-RUN-042-RECOVER-1
labdemo_ucore: passed
```

## 后续增强

后续可以把任务五推进为更完整的 Agent Loop：

- 事件优先级；
- wait 取消；
- 长期任务队列；
- 更细粒度的角色/capability 策略；
- 内核调度器感知 Agent 状态；
- 与 LLM Gateway 联动，让 LLM 决定下一步工具调用。
