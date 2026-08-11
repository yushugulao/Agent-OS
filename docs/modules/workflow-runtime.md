# Workflow 运行时

AgentOS 运行时把事件等待、可信 IPC、模型请求、资源账户、workflow EEVDF 和一致性切片组织在同一个 lifecycle 中。Agent 在没有任务时进入内核等待，由文件变化、协作消息、heartbeat、deadline 或模型完成事件唤醒；所有终态继续写入 Context 和 workflow evidence。

## 文档索引

- [一、运行时主线](#一运行时主线)
- [二、事件队列与原子等待](#二事件队列与原子等待)
- [三、可信 IPC 与模型请求](#三可信-ipc-与模型请求)
- [四、Workflow Credit Domain](#四workflow-credit-domain)
- [五、Workflow EEVDF](#五workflow-eevdf)
- [六、执行记录与 Workflow fence](#六执行记录与-workflow-fence)
- [七、Nexus 多 Agent 运行时](#七nexus-多-agent-运行时)
- [八、实现位置与测试结果](#八实现位置与测试结果)

## 一、运行时主线

多 Agent workflow 的运行并非连续占用 CPU。模型调用、文件生成和协作者处理都会形成等待间隙；事件到来后，系统还要确认接收者仍属于原 workflow，防止过期响应进入新一轮运行。与此同时，一个 workflow 可以包含多个线程，单纯按线程调度会让线程数更多的 workflow 获得额外 CPU 服务。

基于这些约束，我们把运行时拆成五条相互衔接的内核路径：

1. event queue 保存文件、消息、timer、policy denial 和模型完成；
2. `agent_wait()` 以线程 generation 为等待 key，原子完成谓词复查和睡眠；
3. IPC route 与 LLM pending table 绑定 lifecycle、control id 和 correlation；
4. Credit Domain 约束内核对象准入，workflow EEVDF 按 workflow 分配 CPU；
5. evidence ring 记录终态，fence 取得 metadata、credit 和执行记录的一致性切片。

典型 Agent Loop 如下：

```text
安装 typed watch / IPC route / execution contract
  -> agent_wait(timeout)
  -> 读取 FILE_QUERY / MESSAGE / TIMER / POLICY_DENIED / LLM_DONE
  -> 提交结构化工具请求
  -> 内核提交 result + Context + evidence ticket
  -> 回到等待状态
```

## 二、事件队列与原子等待

### 2.1 Event 数据结构

每条事件使用固定布局，定义在 [`os/agent.h`](../../os/agent.h)，公共声明同步到 [`user/include/agent.h`](../../user/include/agent.h)：

```c
struct agent_event {
    int type;
    int source_pid;
    int target_pid;
    int status;
    uint64 event_id;
    uint64 tick;
    uint64 corr_id;
    uint64 cause_sequence;
    uint64 span_id;
    char payload[64];
};
```

事件类型覆盖文件状态、跨 Agent 消息、timer、job/model 完成、policy denial、Context limit、cancel 和 typed file query。`cause_sequence` 与 `span_id` 把事件消费接回 Context；`corr_id` 用于消息和模型请求匹配。

### 2.2 容量与来源隔离

每个 Agent 的 event queue 固定为 16 槽。内核按来源维护独立计数：

| 限制 | 当前值 | 作用 |
| --- | ---: | --- |
| 总队列容量 | 16 | 所有事件的硬上限 |
| kernel reserve | 4 | 保留给内核生成的 timer、cancel 等事件 |
| external limit | 12 | 限制外部事件占用 |
| class reserve | 4 | 防止 IPC 或 attributed event 占满全部 external 槽 |
| 单 source limit | 4 | 限制同一发送者的积压 |

heartbeat 等 intrinsic timer 采用 coalesced delivery；队列中已有同类事件时不重复入队。外部 IPC、来源归因事件和内核事件分别经过 [`agent_ipc_origin_policy`](../../os/agent_ipc.c) 校验。容量检查、event id 分配和尾指针发布都在同一个关中断区域完成。

### 2.3 Lost wakeup 的处理

`agent_wait()` 的关键要求是关闭“检查队列为空后，事件在真正睡眠前到达”的窗口。实现位于 [`os/agent_ipc.c`](../../os/agent_ipc.c)，核心顺序为：

```text
关中断
  -> 复查 cancel / queue head
  -> 有事件：reserve 队首并恢复运行态
  -> 无事件：发布 WAITING、deadline 和 wait key
  -> wait_queue_sleep_key_irq(identity_generation)
  -> 被唤醒后仍在同一锁合同内重新检查
```

wait key 使用线程的 `identity_generation`。timer tick 只唤醒 generation、wait channel、wait reason 和 deadline 全部匹配的线程；`exec`、线程槽位复用和 teardown 不会把旧 wakeup 交给新线程。

队列采用 event baton 控制接收者。事件到达后 [`wait_queue_wake_one_thread()`](../../os/wait.c) 选择一个真实等待线程并授予 baton；该线程 reserve 队首，copyout 和 Context 提交成功后消费事件。copyout 失败会撤销 reservation 并传递 baton，事件仍可重试。测试中的 1、4、8、15 个 waiter 共完成 28 次 wakeup，记录 `herd=0`。

## 三、可信 IPC 与模型请求

### 3.1 IPC route

跨 Agent `MESSAGE` 先检查 source 与 target 都是 live Agent，再比较 workflow scope/lifecycle。target 保存最多 16 条 route，每条 route 以 source `control_id` 为键并携带 event mask。普通发送者需要 `MESSAGE_SEND` capability；配置他人 route 需要 `ROUTE_MANAGE` 且 controller 必须控制 source 与 target。

发送成功后，内核队列项保存 source PID、control id、cause sequence、span 和来源标签；公开 `agent_event` 只返回事件字段，control id 与来源归因保存在 kernel sidecar。消费事件时，内核在 payload copyout 前把 `CROSS_AGENT_DATA` 等标签合并到 target 当前 provenance，随后追加一条 causal Context record。

常用接口如下：

```c
int agent_route_config(int source_pid, int target_pid,
                       uint64 event_mask, int operation);
int agent_wake(int pid, struct agent_event *event);
int agent_wait(struct agent_event *event, int timeout_ticks);
int agent_wait_cancel(int pid, const char *reason);
```

### 3.2 LLM request/response

结构化工具 `LLM_REQUEST` 和 `LLM_RESPONSE` 复用事件通道，但增加 pending table。每个 pending 项保存 requester/relay 的 PID 与 control id、完整 lifecycle key、非零 correlation 和 deadline。请求路径要求同一 requester lifecycle 内的 correlation 严格递增；同一 correlation 仍处于 active pending 时返回 `DUPLICATE`，已经完成或超时的 correlation 再次使用以及倒退 id 返回 `CONFLICT`。

成功响应必须同时匹配 requester、relay、lifecycle 与 correlation，并且只消费一次。kernel-tick TTL 为 120 秒；到期记录进入 terminal history，迟到响应得到 `TIMEOUT` 或 `STALE`。每个 requester 最多保留 16 个 pending 请求，全局表容量为 `NPROC`。实现见 [`os/agent_core.c`](../../os/agent_core.c)。

### 3.3 Heartbeat

`agent_heartbeat_set(interval)` 从当前 tick 重新计时，interval 为 0 时停止。timer 到期后在队列中产生 coalesced `TIMER` 事件；已经入队的事件不会因 stop 消失。`agent_heartbeat_stop()` 可以重复调用。heartbeat 与 cancel 的队列事件在成功消费时走 wait reservation 与 Context 提交路径；本地等待超时直接构造 `TIMEOUT` 返回，不占用队列 reservation，也不追加 Context record。

## 四、Workflow Credit Domain

Agent workflow 会同时创建进程、线程、文件对象、inode、buffer 和物理页。仅在分配失败后清理无法提供确定的 workflow 准入，因此 resource controller 为每个 workflow 建立 execution/storage 两个账户，并用三态 credit 记录对象所有权：

```text
free --reserve--> pending --publish--> used
  ^                    |
  +---- failure -------+
  ^
  +------ release <---- used

held = free + pending + used
```

`free` 是账户保留的空闲预充 credit，`pending` 属于尚未发布的分配，`used` 属于 live object。account limit、resource class limit 和 global capacity 都约束 `held`。创建失败时 pending 退回 free；对象发布后 pending 转为 used；销毁时 used 释放到 free，并可在压力下归还全局池。

资源快照覆盖 8 类对象：process、thread、file object、filesystem block、filesystem inode、buffer cache、Agent state page 和 physical page。共享 ABI 见 [`include/agent_resource_abi.h`](../../include/agent_resource_abi.h)，账户与 policy 实现在 [`os/resource_controller.c`](../../os/resource_controller.c)。

工具执行还使用 Resource Phase Credit Lease。V3 execution contract 给出阶段资源包络，内核先 `begin` 锁定额度，副作用前 `activate`，工具终态用 `settle` 结算真实使用；线程或进程异常退出时 `abort` 回收 lease。这个过程与 lifecycle operation gate 配合：workflow close 标记 closing 并请求成员退出，active phase 在成员完成操作时结算，finalizer 随后回收 lifecycle。

## 五、Workflow EEVDF

### 5.1 调度目标

uCore 原有调度器按可运行线程选择任务。一个 workflow 若创建更多线程，就可能在外层轮转中获得更多服务。AgentOS 为每个 workflow resource domain 建立一个 EEVDF entity，同一 workflow 的所有成员共享 `service_cycles`、`vruntime` 和 `virtual_deadline`；选中 workflow 后，再由 uCore 原有 per-Agent/FIFO 策略选择具体线程。

实体的核心字段定义在 [`os/workflow_scheduler.c`](../../os/workflow_scheduler.c)：

| 字段 | 含义 |
| --- | --- |
| `lifecycle + account + domain_id` | 调度实体的完整身份 |
| `vruntime` | 已结算的等权虚拟服务量 |
| `remaining_cycles` | 当前 service request 剩余量 |
| `virtual_deadline` | `vruntime + remaining_cycles` |
| `runnable_threads` | 当前 workflow 的可运行成员数 |
| `latency_class` | urgent / interactive / normal / batch |
| `sleep_start_tick` / `wake_tick` | 睡眠衰减与 wakeup latency 统计 |

### 5.2 选择算法

四类 latency class 对应 1、2、4、8 tick 的 service request，wall deadline 只能缩短 request。调度器以当前全局 `vtime` 判断 eligibility，再从 eligible entity 中选择 virtual deadline 最早者：

```text
request_cycles = request_ticks * cycles_per_tick
virtual_deadline = vruntime + remaining_cycles
eligible = (vruntime <= vtime)
selected = arg min(virtual_deadline) among eligible workflows
```

所有 workflow 使用固定 weight 1024，实际运行的 cycle 数直接加到该 workflow 的 `vruntime`。睡眠每经过 16 tick 进行一次衰减，最多 8 次右移，使旧 vruntime 向当前 vtime 靠近；该过程恢复交互 workflow 的响应性，却不会增加已结算 service。

[`fetch_task()`](../../os/proc.c) 从 run-queue cache 收集每个 active workflow 的候选项。只有一个 workflow 时保留 legacy O(1) 外层 dispatch；多个 workflow 时调用 `workflow_scheduler_select()`。候选身份、runnable cache 或 entity map 异常时回到原有 outer round-robin，并增加 fallback 计数。workflow 进入 runnable 时记录 ready tick，dispatch 时计算等待，切回 scheduler 后以 cycle counter 结算 service。

### 5.3 实测结果

一次性性能活动在 6 次独立 QEMU 启动中保存了 504 条 exact wake probe，其中 425 条为 0 tick、79 条为 1 tick。所有 workflow 从进入 runnable 到获得 dispatch 的等待均为 0 至 1 tick。依据同一 boot、同一并发场景下各 workflow 的原始 `service_cycles` 计算 Jain 指数，中位数如下：

| 并发 workflow | Boot 数 | Jain fairness 中位数 |
| ---: | ---: | ---: |
| 1 | 6 | 1.000000 |
| 2 | 6 | 0.999985 |
| 3 | 6 | 0.999993 |
| 4 | 6 | 0.999985 |

逐 probe 数据位于 [`one_shot_metrics/data/20260811/tables/eevdf_wakeups.csv`](../../one_shot_metrics/data/20260811/tables/eevdf_wakeups.csv)，逐 workflow service 位于 [`eevdf_samples.csv`](../../one_shot_metrics/data/20260811/tables/eevdf_samples.csv)，ECDF 与公平性图见[性能结果](../performance.md#7-workflow-eevdf)。

## 六、执行记录与 Workflow fence

### 6.1 Evidence ring

Context terminal、event、audit 和 timeline 记录最终投影到 workflow evidence ring。每个 lifecycle 按需使用 4 页，容量分为 48 个 ordinary 槽和 16 个 critical 槽。critical 分区保留给 security denial 等关键记录，不会被普通 success telemetry 占满。

producer 使用 `reserve -> fill -> commit/discard` 两阶段接口取得单调 ticket。工具执行在副作用前同时预留 ordinary 与 critical 槽，终态确定后提交其中一个；未提交 ticket 形成 gap，并进入 seal 摘要。核心结构和接口见 [`os/agent_evidence_ring.h`](../../os/agent_evidence_ring.h) 与 [`os/agent_evidence_ring.c`](../../os/agent_evidence_ring.c)。

### 6.2 Fence 顺序

controller 调用 `agent_workflow_fence()` 时，内核执行以下步骤：

1. 校验 Orchestrator capability 与 controller control id；提供 request/receipt 时再校验版本和非零 request id，二者都为 null 时执行匿名 fire-and-forget fence；
2. 对非零 request id 查询 retry cache；相同 request id 与 challenge 直接返回已提交 receipt；
3. 取得 lifecycle fence gate，要求 active operation 和 departure 都为 0；
4. 取得 metadata quiescence generation，排空 deferred filesystem reclaim 并提交 fs epoch；
5. 对 execution/storage account 取得精确 credit snapshot；
6. 用 challenge、metadata generation、credit epoch 和 credit digest 封存 evidence segment；
7. 缓存 320 字节 receipt，提交 fence sequence 并释放 gate。

核心顺序可在 [`agent_workflow_fence_execute()`](../../os/agent_workflow_fence.c) 中看到：

```c
agent_metadata_quiescence_fence_snapshot_current(&metadata_generation);
fs_deferred_reclaim_drain_current();
fs_epoch_commit();
workflow_credit_domain_fence(key, exec, storage, &credit);
agent_evidence_seal(key, fence_sequence, challenge,
                    metadata_generation, credit.epoch,
                    credit_digest, &evidence);
```

receipt 包含 lifecycle key、request/fence sequence、metadata generation、credit epoch、8 类资源使用量、credit digest、challenge、previous root 和本次 evidence root。当前 v1 固定标记 `PARTIAL_COVERAGE`、`CREDIT_EXACT`、`EVIDENCE_SEALED` 和 `METADATA_VOLATILE`；receipt 是 SHA-256 一致性切片，结构中没有公钥签名字段。ABI 定义见 [`include/agent_workflow_fence_abi.h`](../../include/agent_workflow_fence_abi.h)。

对带 request id 的 fence，同一 id 携带不同 challenge 返回 `CONFLICT`，更旧 id 返回 `STALE`；新 receipt 在上一个 receipt copyout 成功前返回 `RETRY`。匿名 fire-and-forget fence 不进入该重放协议。fence 失败会撤销 gate，不推进 fence sequence。

## 七、Nexus 多 Agent 运行时

<p align="center">
  <img src="../figures/architecture/nexus_runtime_flow.jpg" alt="AgentOS Nexus 多 Agent 运行流程" width="960">
</p>

[查看原生 DrawIO 源文件](../figures/architecture/nexus_runtime_flow.drawio)

`agentnexus_ucore` 展示了上述内核模块在一个完整产品流程中的组合。Coordinator、System、Research 和 Analyst 使用独立 PID、身份和 Context，但共享同一个 lifecycle、scope 和 workflow resource domain。

| 角色 | 主要任务 | 协作对象 |
| --- | --- | --- |
| Coordinator | 动态规划、委派任务、读取 specialist artifact、控制发布 | System、Research、Analyst |
| System | 读取进程、Context 和内核状态，形成系统快照 | Coordinator、Analyst |
| Research | 查询 workflow 文件并核对本地材料 | Coordinator、Analyst |
| Analyst | 读取 System/Research artifact，形成综合报告 | Coordinator |

Coordinator 通过内核 `MESSAGE` route 发送 typed task。System 与 Research worker 用 `PROGRESS` 返回阶段 metrics，Coordinator 收到后将结果 materialize 为 workflow-scoped artifact，再把 handle 交给后续角色；Analyst 的 terminal result 可以直接返回 artifact handle。读取权限由 artifact permission mask 控制；发布报告是单独的结构化副作用，需要匹配用户审批所绑定的规范化调用参数。外部模型仍由 Host relay 处理，Guest 只保存 turn、correlation、角色、工具目录和 artifact 状态。

Guest 主程序位于 [`user/src/agentnexus_ucore.c`](../../user/src/agentnexus_ucore.c)，共享产品协议见 [`user/include/agent_nexus_protocol.h`](../../user/include/agent_nexus_protocol.h)，Host 连接沿用 [`host_tools/agentos_relayd.py`](../../host_tools/agentos_relayd.py)。运行方法见[运行指南](../usage.md#4-nexus-多-agent-workflow)。

## 八、实现位置与测试结果

| 模块 | 主要源码 |
| --- | --- |
| Event queue、watch/wait、route | [`os/agent_ipc.c`](../../os/agent_ipc.c)、[`os/wait.c`](../../os/wait.c) |
| LLM pending 与 Agent loop | [`os/agent_core.c`](../../os/agent_core.c)、[`os/agent_background.c`](../../os/agent_background.c) |
| Credit Domain 与 phase lease | [`os/workflow_credit_domain.c`](../../os/workflow_credit_domain.c)、[`os/resource_controller.c`](../../os/resource_controller.c) |
| Workflow EEVDF | [`os/workflow_scheduler.c`](../../os/workflow_scheduler.c)、[`os/proc.c`](../../os/proc.c) |
| Evidence 与 fence | [`os/agent_evidence_ring.c`](../../os/agent_evidence_ring.c)、[`os/agent_workflow_fence.c`](../../os/agent_workflow_fence.c) |
| Timeline | [`os/agent_observe_timeline.c`](../../os/agent_observe_timeline.c) |

运行时测试覆盖等待原子性、慢 watcher 隔离、heartbeat、route、Credit Domain、evidence ticket、fence retry、EEVDF 与 Nexus replay：

```bash
python3 -B scripts/test-wait-atomic-wiring.py
python3 -B scripts/test-agent-live-loop.py
python3 -B scripts/test-workflow-credit-domain.py
python3 -B scripts/test-agent-evidence-ring.py
python3 -B scripts/test-workflow-fence.py

AGENT_TEST_CASE=agentloop_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentsched_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agent_eevdf_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-

make agentos-nexus-check
make agentos-nexus-replay TOOLPREFIX=riscv64-linux-gnu-
```

Guest 日志验证器要求 `broadcast_slow_watcher_isolated=1`、heartbeat dynamic/coalesced/stop、28 次无 herd event handoff、EEVDF topology 与 wake bucket 等标记。冻结性能数据通过 [`one_shot_metrics/validate.py`](../../one_shot_metrics/validate.py) 校验，当前 chart-readiness 检查覆盖 504 条 exact wake probe 和 6 个公平性 boot。

Workflow 运行时建立在[身份、生命周期与 Context](identity-context.md)之上，并消费[工具执行](tool-execution.md)与 [Live Query](live-query.md)产生的终态和事件。四组模块合在一起，构成从受控创建、等待唤醒到一致性切片和回收的完整 AgentOS 产品路径。
