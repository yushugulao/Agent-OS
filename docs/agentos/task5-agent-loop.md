# 任务五：Agent Loop 内核运行机制

本文是 [design.md](design.md) 的任务五细节附录，重点说明 watch、wait、wake、wait cancel、heartbeat、同 workflow IPC、Agent 感知调度、scope-partitioned audit 和 Run Ledger。

## 目标

任务五希望操作系统支持 Agent 长期运行机制，使 Agent 不只是主动调用工具，还能等待内核事件、被文件状态变化唤醒、按心跳维护状态，并参与多 Agent 协作。

AgentOS-uCore 当前实现的是可验证的 Agent Loop 内核机制：

- Agent 最多可注册 8 条 watch；
- Agent 可删除指定 watch，或一次清空全部 watch；
- 每个 Agent 有 16 槽 FIFO 事件队列；可归因外部事件合计最多 12 条，directed IPC 与 attributed notification 各自最多 8 条，同一 stable source 跨两类最多 4 条，为显式内核 origin 保留至少 4 个容量名额；
- Agent 可等待事件，有限 timeout 会进入睡眠并由 tick 唤醒，事件入队会唤醒目标 Agent；
- 跨 Agent 的 `MESSAGE` / `LLM_DONE` 只有 source/target 属于同一 active workflow scope，且接收方或受权控制者建立定向路由后才能入队；
- 具备独立 `WAIT_CANCEL` 能力且持有直接控制关系的 Agent 可取消目标 Agent 的等待；消息路由不授予等待取消权；
- 文件元数据状态变化可触发事件；
- Agent 可通过独立 set/stop syscall 动态调整或幂等停止 heartbeat，旧 512 ABI 继续兼容；
- heartbeat 是不受 watch/unwatch 抑制的内生 SYSTEM TIMER，能直接唤醒等待 Agent，且同一 Agent 最多保留一条未消费 heartbeat；
- 等待、唤醒和事件消费会写入 Context Path；公开 cause/span 的 source control 与 span owner 由内核私有 sidecar 认证；
- 调度器先严格轮转 active 进程资源域，再按选中域内的 FIFO 或 Agent 软评分选择线程，并记录最近 16 次 Agent 调度原因；
- 调度分值只表达域内软优先级；连续选择 Agent 或连续按分值选择达到 `AGENT_SCHED_MAX_AGENT_BURST=8` 后，本域强制回到普通线程或 FIFO 队首；线程数、角色、事件积压和 orchestrator 配置都不能越过外层资源域轮转；
- 参与 Agent 可按当前 span 查询 Context、事件、调度和预取交接短记录；
- 内核物理审计表 512 槽按最多 4 个 workflow 各保证 128，orchestrator 只能查询自己的 scope；
- 每 scope low/high 各64，low principal 上限16、high active principal 上限8，并维护独立逻辑 hash 链和稀疏窗口；
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
| `AGENT_EVENT_MESSAGE` | Agent 消息 | 用户态 `agent_wake()` 或消息工具 |
| `AGENT_EVENT_TIMER` | 定时或心跳 | heartbeat 到期由 `agent_tick()` 或 `agent_wait()` 检查后入队；有限 wait timeout 直接返回 TIMER/TIMEOUT，不入队 |
| `AGENT_EVENT_JOB_DONE` | 作业完成 | `agent_object_state_update()` 的 attributed 广播 |
| `AGENT_EVENT_POLICY_DENIED` | 策略拒绝 | 预留或工具结果 |
| `AGENT_EVENT_CONTEXT_LIMIT` | Context 限制事件 | 预留 |
| `AGENT_EVENT_LLM_DONE` | LLM Relay 返回解释或摘要 | 受权 `llm_response` 工具路径 |
| `AGENT_EVENT_DASHBOARD_EXPORT` | 可视化导出完成 | 当前预留 |
| `AGENT_EVENT_CANCELLED` | 等待取消 | `agent_wait_cancel()` |

事件结构 `struct agent_event` 包含 type、source_pid、target_pid、status、event_id、tick、corr_id、cause_sequence、span_id 和 payload。公开 `cause_sequence` 是 source 本地 Context sequence，`span_id` 是显示用链 ID；可信解释还要求同 scope、私有 source control 和 span owner，用户可见 PID/span 不是授权票据。

事件队列仍保持单一 FIFO 顺序，但资源核算显式区分 origin 和事件类别：

- `DIRECTED`：经定向路由投递的 `MESSAGE` / `LLM_DONE`，计入 IPC、external 和 stable source 三组计数；
- `ATTRIBUTED`：由某个 Agent 操作触发的 `FILE_STATUS` / `JOB_DONE` / `POLICY_DENIED` / `CONTEXT_LIMIT` / `DASHBOARD_EXPORT`，计入 attributed、external 和同一 stable source 计数；
- `KERNEL`：内核直接产生且不归因到 Agent 的系统事件，当前包括 heartbeat TIMER；不计入 external，只受 16 槽总容量约束。heartbeat 额外使用 intrinsic/coalesced policy，绕过 watch 过滤并限制为一条待处理记录。

`AGENT_EVENT_EXTERNAL_LIMIT=12` 为 `KERNEL` origin 保留至少 4 个总容量名额；`AGENT_EVENT_IPC_LIMIT=8` 和 `AGENT_EVENT_ATTRIBUTED_LIMIT=8` 又让 directed 与 attributed 两类互相保留至少 4 个 external 名额；`AGENT_EVENT_SOURCE_LIMIT=4` 按 stable source control id 跨两类合计，防止同一主体换事件类型绕过配额。用户可见 `source_pid` 只用于观察，不参与身份核算。`LLM_DONE` 虽然只能由受权专用工具产生，但资源类别仍是 directed IPC，必须同时命中 `LLM_DONE` 路由。

面向多个 watcher 的系统事件使用逐订阅者独立投递：一个目标未匹配、退出或队列已满时，内核继续检查后续目标，不让慢订阅者阻塞其他 Agent。文件元数据的权威状态一旦提交，事件广播只是通知，不会因某个 watcher 的队列资源不足而把已提交操作错误报告为失败。

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

事件或 cancel 被选中时不会立即从队列消失。内核在关中断窗口内 reserve 精确 FIFO 队首或 cancel token，用 event id cookie 标记单一消费者；释放短临界区后进入 Context commit lane，完成用户 copyout、可信 source/span 归因及 Context/audit 记录。finish 再核对 slot/head/cookie：成功才 commit 出队、退还 external/IPC/attributed 配额并 handoff 下一 waiter；lane 或 copyout 失败则 abort reservation、保留原事件或 cancel，并定向唤醒等待者。这样坏用户页和 sibling 并发不能在结果交付前提前消费队首。

`WAIT_ATOMIC_TEST_PROFILE` 不是生产 ABI，而是原子边界的动态注入 profile。runner 要求 `agentfinal_ucore: thread_wait_deadlines finite_infinite=1 distinct_deadlines=1 keyed_timer=1 loop_aggregate=1 slot_reuse=1`，以及 `agentfinal_ucore: wait_publication_atomic=1 event_wake_none=1 event_no_sleep=1 sibling_wake_none=1 teardown_completed=1`。前者覆盖 sibling 独立 deadline、generation key 和线程槽复用，后者覆盖事件在最终谓词重检前到达时不误睡，以及 teardown 撤销等待 sibling；reserve/cookie/commit/abort 顺序另由静态与 mutation 合同约束。当前 profile 尚未把“reserve 后用户页失效、sibling waiter、cancel、teardown”四项同时组合，不能把该组合写成已取得 Guest 证据。

`agentbench_ucore` 先验证无事件等待会返回 timeout，并检查 `timeout_count` 增加；随后输出 busy polling 查询和 wait/wake 的计时观测，便于用户看到轮询路径与事件路径的成本都可测。具体 tick 样例统一保存在 [test-record.md](test-record.md)。

这里的 `speedup_x100=100` 是单项自身的计时基线。`busy_poll_vs_wait` 用于呈现两个路径的观测数据，不设置固定 tick 阈值。

### 定向等待队列

Agent event、timeline、子进程退出和线程退出分别拥有独立 `struct wait_queue`；mutex、semaphore 和 condvar 也各自持有等待队列。线程睡眠时记录所属队列和等待原因，`wait_queue_wake_one()`、`wait_queue_wake_all()` 只操作对应队列，不再通过全局扫描唤醒无关线程。进程协作退出时，`wait_queue_interrupt()` 会先从原队列摘除目标线程再使其返回错误，避免留下悬挂节点或破坏其他同步对象的队列。

这项机制同时保证 Agent Loop 的两个性质：事件入队只唤醒等待该 Agent event 的线程；timeline 新记录只在保存的完整 filter 匹配时唤醒 timeline waiter。`usersafety_ucore` 用互斥量和无关进程压力验证定向唤醒，`procreap_ucore` 验证阻塞线程退出时的队列取消，`agentfinal_ucore` 验证 source/event filter 不匹配时不会发生 timeline 唤醒。

## 可信 IPC 路由

接口：

```c
int agent_route_config(int source_pid, int target_pid, uint64 event_mask,
                       int operation);
```

跨 Agent 数据面采用默认拒绝的定向路由。调用者、source、target 必须属于同一 active workflow scope；随后每个目标最多保存 16 条以 stable `agent_control_id` 为键的来源路由。当前位图只包含 `MESSAGE` 和 `LLM_DONE`。自投递隐式允许，不占路由槽。

路由可由两类主体配置：

1. target 自己以 `WATCH` 能力显式接受同 scope 的 live source；自主 consent 也不能跨 scope。
2. 具备 `ROUTE_MANAGE` 的控制者为自己或直接创建的同 scope Agent 配置。

grant/revoke 支持按事件位图增量更新并保持幂等。revoke 只阻止后续事件入队，已经通过授权并进入 FIFO 的事件仍可消费。PID 只用于当次定位；内核在同一关中断临界区内解析两个 PID、核对 stable control id、控制关系并更新路由。source 退出会从所有目标删除对应来源项，target 退出会清空自身路由表，因此 PID 或 PCB 槽复用不会继承旧授权。

`agent_wake()`、`send_message`、`llm_request` 和 `llm_response` 都经过“解析存活对象 -> same-scope -> route -> watch -> quota -> 入队”。只有成功入队后才更新 mailbox 和同 scope metadata 预取交接。

## 唤醒：Wake

接口：

```c
int agent_wake(int pid, struct agent_event *event);
```

语义：

1. 校验调用者是具备 `MESSAGE_SEND` 或 `ORCHESTRATE` 的 Agent。
2. 用户态接口唯一接受 `AGENT_EVENT_MESSAGE`；即使调用者是 orchestrator，也不能提交 FILE_STATUS、TIMER、LLM_DONE 等系统事件。
3. 在同一临界区内解析 source/target，强制同一 active workflow scope，再检查自投递或 `MESSAGE` 路由。
4. 检查目标 watch 是否匹配消息类型和 payload。
5. 检查同一 stable source、directed IPC、external 和事件总量配额，成功后写入目标 FIFO 并唤醒目标进程。

这里的 capability 只决定能否发起消息；scope 决定目标集合，route 决定同 scope 中的具体边。跨 scope、未建 route 或保留事件伪造均返回拒绝。已有 source/direct/external/total 队列配额继续作为同 scope 路由通过后的资源边界。

`agentfinal_ucore` 用自唤醒验证最小路径，检查事件能够入队、等待能够返回，并且相关记录进入 Run Ledger。`labdemo_ucore` 用跨 Agent 消息验证场景路径，检查 sentinel 到 investigator 的消息事件能够被内核投递和消费。原始输出统一见 [test-record.md](test-record.md)。

## 取消等待：Wait Cancel

接口：

```c
int agent_wait_cancel(int pid, const char *reason);
```

语义：

1. 当前进程必须是 Agent。
2. 调用者必须具备独立的 `AGENT_CAP_WAIT_CANCEL`；`MESSAGE_SEND` 只允许发送普通消息，不再隐含控制权。
3. 内核查找目标 pid，目标必须是仍存活的 Agent，并且创建时绑定的 controller id 必须等于调用者的 control id。
4. control id 是内核私有、单调且不复用的 64 位对象身份，不使用 role、PID、父指针或 PCB 槽地址推断授权。
5. 内核在目标 PCB 中写入一次性取消令牌，保存 source pid、event id、reason、cause sequence 和 span。
6. 如果目标已经睡眠在 `agent_wait()` 中，内核立即唤醒目标。
7. 如果取消先到达，目标下一次调用 `agent_wait()` 会立即返回。
8. 目标 `agent_wait()` 返回 `AGENT_STATUS_CANCELLED`，输出事件类型为 `AGENT_EVENT_CANCELLED`，并把取消事件追加到 Context Path。

返回语义：

| 场景 | 返回 |
| --- | --- |
| 普通进程调用 | `-1` |
| Agent 缺少能力 | `AGENT_STATUS_DENIED` |
| 目标不由调用者直接控制 | `AGENT_STATUS_DENIED` |
| 目标不存在或目标不是 Agent | `AGENT_STATUS_NOT_FOUND` |
| 目标已有未消费取消令牌 | `AGENT_STATUS_DUPLICATE` |
| 写入取消令牌成功 | 0 |

取消令牌不进入普通事件队列，也不受 watch/filter 或 IPC 路由限制，因此授权边界必须独立于消息能力。`agentloop_ucore` 验证 orchestrator 能取消自己直接创建的 sentinel；`agentsecurity_ucore` 验证所有低权限角色虽保留 `MESSAGE_SEND` 却没有取消能力，并验证旧 controller 退出后，新 orchestrator 的新 control id 既不能取消遗留目标，也不能继承旧消息路由。测试不要求实际发生 PCB/PID 复用；内核不复用 control id 的机制保证未来槽复用也不扩权。历史测试输出只证明等待取消边界；新的路由和队列隔离标记必须以本次专项测试实际输出为准，不能从旧的 `message_send_preserved=1` 推断。

```text
agentloop_ucore: wait_cancel=1
agentsecurity_ucore: wait_cancel_capability_split=1
agentsecurity_ucore: wait_cancel_scope=1
```

## 心跳：Heartbeat

接口：

```c
int sys_agent_heartbeat_set(uint64 interval_ticks);
int sys_agent_heartbeat_stop(void);
int agent_heartbeat_set(uint64 interval_ticks);
int agent_heartbeat(int interval_ticks);
int agent_heartbeat_stop(void);
```

语义：

1. 当前进程必须是 Agent。
2. syscall 552 的 `sys_agent_heartbeat_set(interval)` 设置心跳间隔，重设时从当前 tick 重新计时；syscall 553 的 `sys_agent_heartbeat_stop()` 幂等停止后续生成。`agent_heartbeat_set()` 和 `agent_heartbeat_stop()` 是便利 wrapper。
3. syscall 512 的 `agent_heartbeat(int)` 保留为兼容入口，正值设置周期，0 停止。
4. 所有 syscall 和工具路径共用 `AGENT_HEARTBEAT_MAX_TICKS=0x7fffffffULL` 校验；超界、负值的 64 位表示和 `UINT64_MAX` 返回 `AGENT_STATUS_BAD_PARAM`，原状态不变。
5. heartbeat 到期会投递 payload 为 `timer=heartbeat` 的 SYSTEM `AGENT_EVENT_TIMER` 并唤醒 Agent；它不依赖 TIMER watch，删除或清空 watch 也不能抑制。
6. 同一 Agent 最多保留一条未消费 heartbeat；后续到期与该记录合并。stop 不删除已入队记录，调用者在断言停止后无新唤醒前应先消费旧记录。
7. `agent_info()` 可观察 heartbeat interval 和 last heartbeat tick。

`agentbench_ucore` 会设置 heartbeat、用 `agent_info()` 检查 interval/tick 后立即停止，避免干扰后续基准。`agentloop_ucore` 动态覆盖无 watch 唤醒、频率调整、单条 pending coalesce、消费旧记录后的 stop、最大值与越界拒绝、工具路径边界，以及旧 512 ABI，并输出精确机制标记 `heartbeat_intrinsic=1 dynamic=1 coalesced=1 stop=1 bounds=1 legacy=1`。

## 感知调度：资源域与 Agent

AgentOS-uCore 的运行队列采用两级结构。`scheduler_active_domains` 是 active `resource_domain_id` 的 FIFO，每个有可运行线程的域最多出现一次；调度器每轮弹出一个域、只从该域选择一个线程，再把仍有 runnable 线程的域放回队尾。每域的线程保存在独立 `scheduler_domain_tasks[]` 中，因此一个 PUBLIC 进程创建更多线程不会在外层队列取得更多节点，也不能按线程数放大跨域 CPU 份额。

进入选中域后，如果本域没有 runnable Agent，直接按域内 FIFO 取队；存在 Agent 时，调度器在本域短时扫描候选，用 `agent_sched_better()` 比较软优先级，选出一个线程后把其余候选放回同一域队列。VM snapshot 暂停的 sibling 也只在原域内保留，不会迁移到其他域或破坏外层轮转。

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

上述因素全部属于软评分，只能决定选中资源域内的候选优先级，不能授权 Agent 无限连续运行。每个资源域独立维护两条不可配置的公平上限：

1. 当队列中同时存在普通任务时，连续选择 Agent 达到 `AGENT_SCHED_MAX_AGENT_BURST=8` 次后，本次必须选择扫描到的首个普通任务；普通任务运行后重新计算 Agent burst。
2. 无论被选任务是否为 Agent，连续 8 次按分值绕过 FIFO 后，本次必须选择 FIFO 队首；完成这次 FIFO escape 后重新计算 score burst。

两条检查位于角色权重、priority、budget、事件、deadline、heartbeat、ready age 和 vruntime 的比较之外。`agent_sched_config()` 只能修改软评分参数，不能修改常量 8，也不能清除或绕过本域 burst 计数。因此即使高权限 Agent 持续制造消息、堆积事件并把 weight/priority 配到最大，同域普通线程仍在有限 dispatch 内获得进展；其他 active 资源域更由外层轮转在每个域轮次取得一次选择机会。

普通支持程序没有 Agent 身份时不经过 Agent 分值计算，只走本域 FIFO。外层域轮转始终存在，不因当前是否有 Agent 而切换成全局线程 FIFO。

`struct agent_info` 暴露调度观测字段：`sched_weight`、`sched_priority`、`sched_budget`、`sched_dispatch_count`、`sched_event_dispatch_count`、`sched_deadline_dispatch_count`、`sched_vruntime`、`sched_ready_tick`、`sched_last_dispatch_tick`、`sched_preemptions` 和 `sched_budget_used`。`agentsched_ucore` 用这些字段验证角色权重、受权调度配置、事件优先和公平性计数，并让一个具有未消费消息的 Agent 连续让出 CPU，确认普通进程先写出结果，输出 `normal_progress=1 max_agent_burst=8`。`procreap_agent_ucore` 还在高分 Agent 持续可运行时验证退出回收线程仍能得到有界调度。

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

`agent_span_trace_snapshot()` 是参与 Agent 的当前可信 span 观测接口。它同时匹配调用者 scope、公开 `current_span_id` 与内核私有 span owner；公开 span 相同也不能读取其他 scope/owner。

`agent_timeline_snapshot/query/wait/read()` 先按 scope 和 private owner 得到可见集合，再做 source/tick/span/pid/tool/event/status/flags/cursor 过滤与等待。普通 Agent 只看自身和同 scope 当前可信 span，orchestrator 只扩大到本 workflow 审计，不存在全系统审计权限。

`agent_provenance_snapshot()` 使用同一 scope/owner 可见规则。跨 Agent cause 的 source sequence 由私有 source pid/control sidecar 解释，避免误连目标本地同号 Context。

`agent_audit_snapshot()` 面向本 scope orchestrator。物理 512 槽按 4 scope 各保证128；每 scope low/high 各64。遥测总是 low，low principal 上限16；内核确认的特权状态效果才是 high，high 每 active principal 保证8。high 满时只自滚当前 principal 或回收 inactive principal，active principal 互不淘汰；非活跃历史允许有界滚动并进入 `dropped_records`。每 scope 独立维护逻辑 hash 链，系统 sequence 可因其他 scope 和分区滚动而稀疏；只对没有 gap 的相邻可见记录检查直接 hash 邻接。

上述 audit/span/timeline/provenance 查询不以 `max=0` 或过滤结果为空为由跳过计费。scope-local 双有序索引消除全局重扫，单遍 scan 或 timeline 四路归并按每 16 个候选预付 kernel-work；让出后重新读取来源边界并补足增长差额，因此低权限 Agent 不能用计数查询在一个 syscall 内绕过资源域调度。

## 文件状态事件

任务四和任务五的结合点是 `agent_file_meta_set()`：

1. 具备 `AGENT_CAP_META_WRITE` 的 Agent 更新文件元数据；
2. 如果状态字段发生变化，内核构造 `AGENT_EVENT_FILE_STATUS`；
3. 内核查找匹配 watch 的 Agent；
4. 逐目标独立投递；某个 watcher 队列已满时继续检查后续 watcher；
5. 目标 Agent 从 `agent_wait()` 返回。

文件 metadata 是先提交的权威状态，广播只是 best-effort 通知。单个慢 watcher 的队列资源不足不会回滚状态，也不会让 `agent_file_meta_set()` 在提交后错误返回 `AGENT_STATUS_NO_SPACE`。`labdemo_ucore` 会在用户态写入科研示例数据后触发文件状态变化，sentinel 通过 watch 收到事件。原始输出见 [test-record.md](test-record.md)。

## 消息事件

`AGENT_TOOL_SEND_MESSAGE` 和 `agent_wake()` 都可以向目标 Agent 发送 MESSAGE 事件。消息事件用于多 Agent 协作。`agent_wake()` 是 Agent-only syscall，调用者必须具备 `AGENT_CAP_MESSAGE_SEND` 或 `AGENT_CAP_ORCHESTRATE`；普通进程直接调用会返回 `-1`。MESSAGE 是该用户态 syscall 唯一允许的事件类型：即使调用者是 Agent 或 orchestrator，也不能通过它把用户提供的类型伪装成 `FILE_STATUS`、`TIMER`、`LLM_DONE` 等系统事件。

能力检查之后还必须 source/target 同 active workflow scope并命中 route；target consent、相同角色/controller/resource domain 都不能越过 scope。只有成功入队才执行 mailbox 和同 scope prefetch handoff。

`labdemo_ucore` 中两段消息：

| 来源 | 目标 | 目的 |
| --- | --- | --- |
| sentinel | investigator | 发现失败后请求调查 |
| investigator | recovery | 完成分析后请求恢复 |

orchestrator 在三个工作 Agent 报告 ready、触发初始失败事件前，显式 grant sentinel -> investigator 和 investigator -> recovery 的 `MESSAGE` 路由。路由表达的是工作流拓扑，而不是对某个角色或 PID 范围的全局授权。

message 入队时，内核只在同 scope 内交接 metadata 提示。可信 span 提示进入物理32槽、每scope8条的分区表；接收者继承 private span owner 后才能查询。

## 上下文路径记录：Context Path

Agent Loop 行为会写入 Context Path：

- watch 注册；
- wait 成功；
- wait cancel；
- heartbeat 设置；
- message 工具调用；
- query 和 recovery 工具调用。

这使得示例不仅能看到最终结果，也能回放 Agent 做出判断的过程。

Context v8 还会把事件和后续工具调用连起来，并继续维护 Context 完整性链：

1. 事件入队时，内核写公开 cause/span，同时私存 source control/pid 和 span owner。
2. `agent_wait()` 只消费本 scope 已认证事件，并继承公开 span 与私有 owner/source。
3. 后续 `query_file`、`send_message`、`action_commit` 等工具调用会继续使用这个 span。
4. 每次追加 Context 记录时，内核同时写入 prev_hash 和 record_hash，header 暴露最新链尾 hash。

因此同 workflow 协作可追踪，但公开 cause/span 不可自行铸造。`context_push()` 的非零 cause/span 被拒绝；`agentscope_ucore` 覆盖跨 scope IPC 拒绝，`agentsecurity_ucore` 覆盖伪造 Context 与可信来源归因。新增标记以本轮实际 QEMU 输出为准。

这说明多 Agent 场景不只保留单个 Agent 自己的 Context，还能在内核中形成一组可查询、可过滤并带摘要 hash 的系统级短记录，用于说明“哪个 Agent 收到事件、哪个 Agent 进行了工具调用、调度器何时运行了相关 Agent”。

## 当前限制

| 限制项 | 说明 |
| --- | --- |
| 调度策略 | 当前支持 orchestrator 配置 weight、priority 和 budget，尚未实现复杂策略语言；这些参数都是域内软策略，外层 active-domain 轮转和固定为 8 的域内 Agent/FIFO 公平上限不可配置 |
| 事件容量 | 当前每 Agent 固定 16 槽 FIFO；external 合计 12、directed IPC 8、attributed notification 8、同一 stable source 跨两类 4，为显式 `KERNEL` origin 保留至少 4 个容量名额，所有事件仍受总容量 16 |
| IPC 路由 | 每个 target 最多16条同 scope stable-source route；不支持跨 scope，即使 target consent 也拒绝 |
| 路由可观测性 | 没有 route snapshot/query；scope audit 只能看到成功入队/消费，不能还原全部授权变更 |
| 审计窗口 | 每 scope 128（low/high各64）；low principal16，high active principal8；inactive high 历史允许有界滚动并由 dropped 说明 |
| 线程历史身份 | timeline/trace/sched 当前只保存采样时的 raw tid，不保存 `thread.identity_generation`；`tid=0` 是合法主线程，也可能表示规范化来源没有独立线程信息，因此 tid 过滤不具备 incarnation-safe 归因语义 |
| 取消范围 | 当前支持取消正在等待或下一次等待的 Agent；尚未实现通用任务取消 |
| 多核复杂场景 | 当前按 uCore 当前运行环境和测试路径验证锁保护和队列状态 |
| LLM 驱动 | 当前由用户测试程序驱动，不接真实 LLM |

## 验证证据

原始输出统一保存在 [test-record.md](test-record.md)，逐项测试步骤见 [testing-details.md](testing-details.md)。任务五重点检查以下内容：

| 程序 | 检查项 |
| --- | --- |
| `agentfinal_ucore` | 自唤醒事件可被 watch/wait 消费，相关 Context、事件、调度和预取统计进入 Run Ledger；`WAIT_ATOMIC_TEST_PROFILE` 另要求 `thread_wait_deadlines ... slot_reuse=1` 与 `wait_publication_atomic=1 ... teardown_completed=1` 两条完整 marker。 |
| `agentbench_ucore` | timeout 会更新 heartbeat/timeout 统计；busy polling 与 wait/wake 都有可复查的 tick 观测。 |
| `agentloop_ucore` | FIFO、cause/span、unwatch、睡眠 timeout、wait cancel 和严格 heartbeat 语义均有动态断言；精确 heartbeat 标记覆盖无 watch 内生唤醒、动态频率、单条 coalesce、stop、边界和旧 ABI。`message_source_limit=4`、`ipc_class_limit=8`、`external_limit=12`、`system_event_reserved=4`、`heartbeat_reserve_coalesced=1`、`external_reject_reclaim=1` 和 `broadcast_slow_watcher_isolated=1` 验证外部 admission、一个 heartbeat 可进入保留容量、消费后重新接纳，以及 external 已饱和的慢 watcher 不阻断后续 watcher。attributed=8 与 stable source 混合跨类仍缺独立边界输出。 |
| `agentsched_ucore` | 角色权重、orchestrator 调度配置、事件优先、原因记录和公平性计数均写入调度记录；普通进程在持续可运行高分 Agent 下先取得进展，并输出 `normal_progress=1 max_agent_burst=8`。 |
| `threadresource_ucore` | 以 19/12/6/6/4 tiny policy 验证普通/保留域上限与复用、容量拒绝计数稳定、线程/进程退出退款、普通/保留全局水位与复用、系统保留进展和 active-domain 公平轮转；输出 12 项机制标记及 `parent passed`。 |
| `agentsecurity_ucore` | 用户态 `agent_wake()` 只允许 MESSAGE；普通进程、保留系统事件和非法类型均被拒绝。`route_source_enforced=1`、`route_target_isolated=1`、`ipc_route_authorization=1`、`message_route_lifecycle=1`、`target_route_consent=1` 和 `route_slot_reclaimed=1` 验证未授权拒绝、控制者 grant/revoke、新 control id 隔离、target 自主接受 LLM_DONE、LLM-only route 拒绝 MESSAGE，以及超过 16 个短命 source 后路由槽可回收。 |
| `agentscope_ucore` | 除 IPC/audit/协作和观测预算标记外，最终专项还输出 `scope_close_authority=1`、`scope_controller_exit_revoke=1`、`scope_forced_cleanup=1`、`scope_replacement_admitted=1` 和 `parent passed`，验证根离开会撤销阻塞 Agent 的 event wait 并完整回收 scope。 |
| `usersafety_ucore` | 无关睡眠者不会被同步对象的定向唤醒破坏，syscall 返回后内核继续存活。 |
| `procreap_ucore` / `procreap_agent_ucore` | 阻塞线程退出会从等待队列安全取消；高分 Agent 持续可运行时，退出清理和普通任务仍得到有界调度。 |
| `labdemo_ucore` | orchestrator 在同一 workflow 建立路由后，scope audit、timeline 和 provenance 保持同一条示例链路。 |
