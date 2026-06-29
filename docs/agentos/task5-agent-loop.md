# 任务五：Agent Loop 内核运行机制

本文是 [design.md](design.md) 的任务五细节附录，重点说明 AgentOS-uCore 当前实现的 watch、wait、wake、wait cancel、heartbeat、事件投递、Agent 感知调度、全局审计机制和 Run Ledger 摘要。

## 目标

任务五希望操作系统支持 Agent 长期运行机制，使 Agent 不只是主动调用工具，还能等待内核事件、被文件状态变化唤醒、按心跳维护状态，并参与多 Agent 协作。

AgentOS-uCore 当前实现的是可验证的 Agent Loop 内核机制：

- Agent 最多可注册 8 条 watch；
- Agent 可删除指定 watch，或一次清空全部 watch；
- 每个 Agent 有 16 槽 FIFO 事件队列；
- Agent 可等待事件，有限 timeout 会进入睡眠并由 tick 唤醒，事件入队会唤醒目标 Agent；
- 其他 Agent 或内核工具可唤醒目标 Agent；
- 具备消息发送或编排能力的 Agent 可取消目标 Agent 的等待；
- 文件元数据状态变化可触发事件；
- Agent 可设置 heartbeat，注册 TIMER watch 后可收到 heartbeat 事件；删除 TIMER watch 后不会消费 TIMER 事件；
- Agent 可通过 `agent_heartbeat_stop()` 停止 heartbeat；
- 等待、唤醒和事件消费会写入 Context Path，并继承 cause/span 因果字段；
- 调度器按 Agent 角色、orchestrator 配置的调度参数、事件队列、deadline、heartbeat 到期、等待时长和虚拟运行量选择可运行任务，并记录最近 16 次调度原因；
- 参与 Agent 可按当前 span 查询 Context、事件、调度和预取交接短记录；
- 内核维护最近 512 条全局审计短记录，orchestrator 可查询并过滤多 Agent 的 Context、事件、调度和预取交接摘要；
- 全局审计短记录维护 prev/record hash 链，orchestrator 可读取 Run Ledger 摘要；
- 综合场景用三个 Agent 串联事件流程。

当前已经提供 orchestrator 受权配置接口，可调整目标 Agent 的 policy、weight、priority 和 budget；尚未实现复杂策略语言或宿主机策略文件下发。

## 循环状态：Loop

`struct agent_info` 中的 `loop_state` 表示 Agent 当前状态：

| 状态 | 含义 |
| --- | --- |
| `AGENT_LOOP_NONE` | 普通进程或未初始化 |
| `AGENT_LOOP_IDLE` | Agent 已创建但未等待 |
| `AGENT_LOOP_RUNNING` | Agent 正在执行工具或示例逻辑 |
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
| `AGENT_EVENT_CANCELLED` | 等待取消 | `agent_wait_cancel()` |

事件结构 `struct agent_event` 包含 type、source_pid、target_pid、status、event_id、tick、corr_id、cause_sequence、span_id 和 payload。`cause_sequence` 指向触发事件的前序 Context sequence，`span_id` 表示事件所属因果链。

## 观察项：Watch

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

`labdemo_ucore` 中 sentinel 会注册文件状态 watch。对应检查项为 `WATCH_REGISTERED`，原始输出见 [test-record.md](test-record.md)。

## 等待：Wait

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
6. 如果事件携带 span，当前 Agent 会继承该 span；后续工具调用会把事件消费作为 cause 继续记录。
7. 若目标 Agent 有未消费的 wait cancel 令牌，则立即返回 `AGENT_STATUS_CANCELLED`，并把取消事件写入用户态输出结构。

当前有限 timeout 也会进入 `SLEEPING` 状态，由事件入队、heartbeat 到期或 deadline 到期唤醒，不通过反复 `yield()` 消耗 CPU。`agent_info.wait_loop_count` 用于观察等待检查次数，`agentloop_ucore` 会验证有限 timeout 的循环次数保持在很小范围。

`agentbench_ucore` 先验证无事件等待会返回 timeout，并检查 `timeout_count` 增加；随后输出 busy polling 查询和 wait/wake 的计时观测，便于用户看到轮询路径与事件路径的成本都可测。具体 tick 样例统一保存在 [test-record.md](test-record.md)。

这里的 `speedup_x100=100` 是单项自身的计时基线。`busy_poll_vs_wait` 用于呈现两个路径的观测数据，不设置固定 tick 阈值。

## 唤醒：Wake

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

`agentfinal_ucore` 用自唤醒验证最小路径，检查事件能够入队、等待能够返回，并且相关记录进入 Run Ledger。`labdemo_ucore` 用跨 Agent 消息验证场景路径，检查 sentinel 到 investigator 的消息事件能够被内核投递和消费。原始输出统一见 [test-record.md](test-record.md)。

## 取消等待：Wait Cancel

接口：

```c
int agent_wait_cancel(int pid, const char *reason);
```

语义：

1. 当前进程必须是 Agent。
2. 调用者必须具备 `AGENT_CAP_MESSAGE_SEND` 或 `AGENT_CAP_ORCHESTRATE`。
3. 内核查找目标 pid，目标必须是 Agent。
4. 内核在目标 PCB 中写入一次性取消令牌，保存 source pid、event id、reason、cause sequence 和 span。
5. 如果目标已经睡眠在 `agent_wait()` 中，内核立即唤醒目标。
6. 如果取消先到达，目标下一次调用 `agent_wait()` 会立即返回。
7. 目标 `agent_wait()` 返回 `AGENT_STATUS_CANCELLED`，输出事件类型为 `AGENT_EVENT_CANCELLED`。
8. 取消事件也会追加到目标 Context Path，result 文本为 `cancelled`。

返回语义：

| 场景 | 返回 |
| --- | --- |
| 普通进程调用 | `-1` |
| Agent 缺少能力 | `AGENT_STATUS_DENIED` |
| 目标不存在或目标不是 Agent | `AGENT_STATUS_NOT_FOUND` |
| 目标已有未消费取消令牌 | `AGENT_STATUS_DUPLICATE` |
| 写入取消令牌成功 | 0 |

取消令牌不进入普通事件队列，也不受 watch/filter 限制。这样它不会被满队列阻挡，也不会被业务 filter 误拦截。`agentloop_ucore` 创建一个 sentinel 等待者，由 orchestrator 调用 `agent_wait_cancel()`，验证取消事件、reason、cause/span、`wait_cancel_count` 和 Context 追加均正确：

```text
agentloop_ucore: wait_cancel=1
```

## 心跳：Heartbeat

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

## 感知调度：Agent

uCore 原有调度器从可运行队列中取任务。AgentOS-uCore 当前实现保留普通 FIFO 取队路径，并增加一个 Agent 任务提示位：当可运行队列中没有 Agent 时，调度器继续使用原有 `fetch_task()`；当 Agent 进入可运行队列后，调度器改用 `fetch_best_task()`，短时取出一批可运行任务，用 `agent_sched_better()` 比较任务优先级，选出最适合运行的任务后把其余任务放回队列。若本次扫描发现队列中已经没有 Agent，提示位会被清除，后续普通进程负载回到原 FIFO 路径。

Agent 任务的调度分值因素包括：

| 因素 | 作用 |
| --- | --- |
| 角色权重 | recovery 120、orchestrator 110、investigator 90、sentinel 70 |
| 受权配置 | orchestrator 可调整目标 Agent 的 weight、priority 和 budget |
| 事件队列 | 有待消费事件的 Agent 获得明显加分，事件越多加分越高 |
| 等待状态 | 正在等待事件的 Agent 获得加分 |
| deadline | timeout deadline 越近，越容易被调度 |
| heartbeat | heartbeat 到期附近获得加分，便于及时处理 TIMER 事件 |
| 等待时长 | 进入可运行队列越久，分数越高，避免长期饥饿 |
| 虚拟运行量 | 已经运行较多的 Agent 被扣分，提升公平性 |
| 运行预算 | 单个 Agent 超过默认预算后被扣分 |

普通进程仍可运行。Agent 调度不是把普通进程完全压制，而是在存在可运行 Agent 时优先处理更紧急、更有权限或已经等待较久的 Agent；普通支持程序没有 Agent 身份时，不需要反复经过 Agent 调度分值计算路径。

`struct agent_info` 暴露调度观测字段：`sched_weight`、`sched_priority`、`sched_budget`、`sched_dispatch_count`、`sched_event_dispatch_count`、`sched_deadline_dispatch_count`、`sched_vruntime`、`sched_ready_tick`、`sched_last_dispatch_tick`、`sched_preemptions` 和 `sched_budget_used`。`agentsched_ucore` 用这些字段验证角色权重、受权调度配置、事件优先和公平性计数。

调度原因记录接口：

```c
int agent_sched_snapshot(struct agent_sched_record *records, int max);
int agent_sched_config(struct agent_sched_config *config);
int agent_trace_snapshot(struct agent_trace_record *records, int max);
int agent_span_trace_snapshot(struct agent_audit_record *records, int max);
int agent_timeline_snapshot(struct agent_timeline_record *records, int max);
int agent_timeline_query(struct agent_timeline_filter *filter,
			 struct agent_timeline_record *records, int max);
int agent_timeline_wait(struct agent_timeline_filter *filter, int timeout_ticks);
int agent_timeline_read(struct agent_timeline_filter *filter,
			struct agent_timeline_record *records, int max,
			int timeout_ticks);
int agent_provenance_snapshot(struct agent_provenance_edge *edges, int max);
int agent_audit_snapshot(struct agent_audit_record *records, int max);
int agent_audit_query(struct agent_audit_filter *filter,
                      struct agent_audit_record *records, int max);
int agent_ledger_snapshot(struct agent_ledger_summary *summary);
```

语义：

1. 当前进程必须是 Agent。
2. 内核为每个 Agent 保留最近 16 次被调度时的 `agent_sched_record`。
3. `max=0` 时返回当前可见记录数，不复制记录。
4. `max>0` 时按时间顺序复制最近记录。
5. 普通进程调用返回 `-1`。

`agent_sched_config()` 只允许 orchestrator 调用，使用 `struct agent_sched_config.update_mask` 指定更新 policy、weight、priority 或 budget。weight 合法范围为 10 到 200，priority 合法范围为 -100 到 100，budget 合法范围为 1 到 64。`agentsched_ucore` 会创建 sentinel Agent，将其配置为 `weight=150 priority=20 budget=3`，并检查该 Agent 后续调度记录中带有 `AGENT_SCHED_REASON_PRIORITY`。

`struct agent_sched_record` 包含调度 tick、dispatch count、score、reason flags、事件队列长度、ready age、deadline delta、heartbeat 是否到期、虚拟运行量、预算使用量、pid/tid、role、loop_state、weight 和 priority。reason flags 包含：

| flag | 含义 |
| --- | --- |
| `AGENT_SCHED_REASON_ROLE_WEIGHT` | 角色权重参与基础分 |
| `AGENT_SCHED_REASON_EVENT_QUEUE` | 有待消费事件 |
| `AGENT_SCHED_REASON_WAITING` | Agent 处于等待状态 |
| `AGENT_SCHED_REASON_DEADLINE_NEAR` | timeout deadline 接近 |
| `AGENT_SCHED_REASON_DEADLINE_NOW` | timeout deadline 已到或马上到 |
| `AGENT_SCHED_REASON_HEARTBEAT_DUE` | heartbeat 到期 |
| `AGENT_SCHED_REASON_BUDGET_USED` | 当前调度预算已用满 |
| `AGENT_SCHED_REASON_VRUNTIME` | 虚拟运行量产生扣分 |
| `AGENT_SCHED_REASON_READY_AGE` | 进入可运行队列后的等待时间参与调度分值计算 |
| `AGENT_SCHED_REASON_PRIORITY` | 配置的 priority 参与调度分值计算 |

`agentsched_ucore` 在自唤醒消息尚未消费时让出处理器，随后检查最近调度记录必须包含 `AGENT_SCHED_REASON_EVENT_QUEUE` 和 `AGENT_SCHED_REASON_ROLE_WEIGHT`，并输出：

```text
agentsched_ucore: reason_trace=1 records=... reason=... score=...
```

`agent_trace_snapshot()` 会复用这些调度记录，并把它们和 Context 摘要按 tick 放进同一组短记录中。它用于回答“事件等待、工具调用和调度原因在一个 Agent 内如何相互衔接”。`agentfinal_ucore` 会验证该接口至少包含一条 Context 记录、一条调度记录和一条 `agent_wait()` 事件消费记录。

`agent_span_trace_snapshot()` 是参与 Agent 的当前 span 观测接口。它读取同一组全局短记录，只返回当前 Agent 的 `current_span_id` 对应记录；普通进程返回 `-1`，缺少 `AUDIT_WRITE` 的 Agent 返回 `AGENT_STATUS_DENIED`。`labdemo_ucore` 中 investigator 消费 sentinel 消息后调用该接口，检查当前 span 中已经有 Context、事件和预取交接记录。

`agent_timeline_snapshot()` 是统一导出接口。它把当前 Agent 可见的 Context 摘要、调度原因、审计短记录和本地预取提示转换为 `agent_timeline_record`，并按 tick 输出。普通 Agent 只能看到自身数据和当前 span 的系统短记录；orchestrator 可以看到全局审计记录。Context 审计记录保留工具结果数值槽，内容摘要工具的 size、bytes、hash 和短 preview 可以进入统一 timeline。`agent_timeline_query()` 在同一可见集合上按 source、起始 tick、span、kind、pid、tool、event、status、flags 和 after-cursor 过滤，用于只读取某个 Agent、某个事件类型、某次预取交接、某个工具产生的内容证据，或按上一条已读记录继续读取后续片段。`agent_timeline_wait()` 使用同一套 filter；没有匹配记录时 Agent 睡眠，直到 Context、审计、调度或预取提示写入后被唤醒，返回正数后再用同一 filter 查询。`agent_timeline_read()` 使用同一套 filter，但会在同一次 syscall 中完成等待和记录复制。等待中的 filter 会保存在 PCB 中，内核把每次新写入转换为统一 timeline record，并直接按完整 filter 判断是否唤醒，避免只等待 Context 的 Agent 被纯 Audit 记录增加 timeline wake 计数，也避免只等待 MESSAGE 的 Agent 被 TIMER 记录唤醒。`agentfinal_ucore` 会检查输出中同时存在 Context、调度、审计和预取提示来源，检查 source/tick/cursor 过滤，并验证 timeline wait 的 timeout、source 不匹配不唤醒、event 不匹配不唤醒、heartbeat TIMER audit 唤醒和 wait-and-read 记录复制；`labdemo_ucore` 会检查多 Agent 场景下的统一 timeline 同时包含 Context、事件、调度、预取交接和 digest 内容证据，并用 query 精确读取 prefetch handoff、digest 证据和 cursor 后续片段。原始输出见 [test-record.md](test-record.md)。

`agent_provenance_snapshot()` 是因果图接口。它使用与 timeline 相同的权限视图，但输出的是 Context、Audit 和 Prefetch 节点之间的因果边。`labdemo_ucore` 用它验证 sentinel 到 investigator 的 message 边、prefetch handoff 边和 investigator 读取真实内容摘要的 digest 边。

`agent_audit_snapshot()` 是面向 orchestrator 的系统级观测接口。内核把 Context 追加、事件入队、事件消费、Agent 调度 dispatch 和预取提示交接写入固定 512 条全局 ring。每条记录包含 `prev_hash` 和 `record_hash`，形成内核维护的运行事实链。普通进程调用返回 `-1`，非 orchestrator Agent 调用返回 `AGENT_STATUS_DENIED`。`agent_audit_query()` 使用 `struct agent_audit_filter` 的 flags 过滤同一组短记录，可按 kind、span、pid/source/target、role、tool、event、status 和起始 sequence 查询。`agent_ledger_snapshot()` 返回这组全局短记录的 sequence 范围、记录总量、已淘汰数、分类计数、观测 epoch 和链尾 hash，适合状态页面先读取摘要，再按需读取明细。`labdemo_ucore` 在 sentinel、investigator、recovery 退出后调用这些接口，确认三个业务 Agent 都出现在全局短记录中，并且记录中同时包含 Context、事件、调度和预取交接摘要。

## 文件状态事件

任务四和任务五的结合点是 `agent_file_meta_set()`：

1. 具备 `AGENT_CAP_META_WRITE` 的 Agent 更新文件元数据；
2. 如果状态字段发生变化，内核构造 `AGENT_EVENT_FILE_STATUS`；
3. 内核查找匹配 watch 的 Agent；
4. 把事件投递给目标 Agent；
5. 目标 Agent 从 `agent_wait()` 返回。

`labdemo_ucore` 会在用户态写入科研示例数据后触发文件状态变化，sentinel 通过 watch 收到事件。原始输出见 [test-record.md](test-record.md)。

## 消息事件

`AGENT_TOOL_SEND_MESSAGE` 和 `agent_wake()` 都可以向目标 Agent 发送消息事件。消息事件用于多 Agent 协作。`agent_wake()` 是 Agent-only syscall，调用者必须具备 `AGENT_CAP_MESSAGE_SEND` 或 `AGENT_CAP_ORCHESTRATE`；普通进程直接调用会返回 `-1`。

`labdemo_ucore` 中两段消息：

| 来源 | 目标 | 目的 |
| --- | --- | --- |
| sentinel | investigator | 发现失败后请求调查 |
| investigator | recovery | 完成分析后请求恢复 |

message 入队时，内核还会把发送者当前可见的 metadata 预取提示复制到接收者的提示 ring。提示包含同一 span 时，还会写入全局 span 预取提示总线。接收者消费消息后继承 span，可以通过 `agent_file_prefetch_snapshot()` 查看交接到自己名下的提示，也可以通过 `agent_file_prefetch_span_snapshot()` 查看同一因果链中的提示来源和接收者。

## 上下文路径记录：Context Path

Agent Loop 行为会写入 Context Path：

- watch 注册；
- wait 成功；
- wait cancel；
- heartbeat 设置；
- message 工具调用；
- query 和 recovery 工具调用。

这使得示例不仅能看到最终结果，也能回放 Agent 做出判断的过程。

Context v6 还会把事件和后续工具调用连起来，并继续维护 Context 完整性链：

1. 事件入队时，内核写入 `cause_sequence` 和 `span_id`。
2. `agent_wait()` 成功消费事件后，当前 Agent 继承事件 span。
3. 后续 `query_file`、`send_message`、`action_commit` 等工具调用会继续使用这个 span。
4. 每次追加 Context 记录时，内核同时写入 prev_hash 和 record_hash，header 暴露最新链尾 hash。

因此跨 Agent 协作可以从 Context 和事件结构中追踪：sentinel 收到文件状态事件后查询文件，随后发送消息；investigator 消费消息后继续分析；recovery 消费消息后执行恢复。`agentloop_ucore` 会检查投递和消费的事件都带有非零 cause/span。`labdemo_ucore` 还会检查 investigator 能通过 snapshot 查看推理和工具调用历史，也能在当前 span 中看到上游事件和预取交接摘要。综合示例结束前，orchestrator 会查询全局审计短记录，确认三个业务 Agent、Context、事件、调度和预取交接都进入同一组系统级记录。原始输出统一见 [test-record.md](test-record.md)。

这说明多 Agent 场景不只保留单个 Agent 自己的 Context，还能在内核中形成一组可查询、可过滤并带摘要 hash 的系统级短记录，用于说明“哪个 Agent 收到事件、哪个 Agent 进行了工具调用、调度器何时运行了相关 Agent”。

## 当前限制

| 限制项 | 说明 |
| --- | --- |
| 调度策略 | 当前支持 orchestrator 配置 weight、priority 和 budget，尚未实现复杂策略语言 |
| 事件容量 | 当前每 Agent 固定 16 槽 FIFO，队列满时拒绝新事件 |
| 取消范围 | 当前支持取消正在等待或下一次等待的 Agent；尚未实现通用任务取消 |
| 多核复杂场景 | 当前按 uCore 当前运行环境和测试路径验证锁保护和队列状态 |
| LLM 驱动 | 当前由用户测试程序驱动，不接真实 LLM |

## 验证证据

原始输出统一保存在 [test-record.md](test-record.md)，逐项测试步骤见 [testing-details.md](testing-details.md)。任务五重点检查以下内容：

| 程序 | 检查项 |
| --- | --- |
| `agentfinal_ucore` | 自唤醒事件可被 watch/wait 消费，相关 Context、事件、调度和预取统计进入 Run Ledger。 |
| `agentbench_ucore` | timeout 会更新 heartbeat/timeout 统计；busy polling 与 wait/wake 都有可复查的 tick 观测。 |
| `agentloop_ucore` | FIFO 顺序、cause/span、队列满拒绝、unwatch、睡眠 timeout、TIMER unwatch、heartbeat stop 和 wait cancel 均可用。 |
| `agentsched_ucore` | 角色权重、orchestrator 调度配置、事件优先、原因记录和公平性计数均写入调度记录。 |
| `labdemo_ucore` | 文件状态事件唤醒 sentinel，message 触发 investigator，预取提示随 span 交接，digest、LLM 调用、恢复计划、全局审计、timeline 和 provenance 都进入同一条示例链路。 |
