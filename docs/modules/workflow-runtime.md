# Workflow 运行时

AgentOS 运行时把事件等待、可信 IPC、资源账户、workflow 调度和执行记录组织在同一个 lifecycle 中。Agent 可以在没有工作时休眠，由文件变化、协作消息、heartbeat、deadline 或模型完成事件唤醒。

## 事件循环

一次典型 Agent Loop 按以下顺序推进：

```text
安装 typed watch 或 route
    -> agent_wait(timeout)
    -> 读取 FILE_QUERY / MESSAGE / TIMER / POLICY_DENIED
    -> 提交结构化工具请求
    -> 将结果写入 Context
    -> 返回等待状态
```

每个 Agent 的 event queue 包含 16 个槽位。内核为控制事件和来源类别保留容量，避免普通消息占满队列后阻塞 wakeup、denial 与 resync。队列达到限制时返回容量或重试状态。

`agent_wait()` 使用 thread-generation wait key，在同一锁合同内完成入队前后的条件重查，关闭“检查为空后、真正睡眠前”的 lost wakeup 窗口。`exec`、`exit` 和 proc reset 会同步清理订阅与等待状态。

```c
int agent_wait(struct agent_event *event, int timeout_ticks);
int agent_wait_cancel(int pid, const char *reason);
int agent_wake(int pid, struct agent_event *event);
int agent_route_config(int source_pid, int target_pid,
                       uint64 event_mask, int operation);
```

## IPC 与请求相关性

跨 Agent `MESSAGE` 发送时，内核检查 source 与 target 的 active lifecycle、route mask、source capability、target generation 和 control id。文件变化、策略拒绝、timer 等内核事件由对应模块发布。

模型请求使用 `LLM_REQUEST/LLM_RESPONSE` 管理 Guest RPC。pending 项保存 requester、relay、完整 lifecycle、非零 correlation 和 deadline。correlation 在同一 requester 内严格递增，匹配成功后只消费一次。kernel-tick TTL 为 120 秒，超时与迟到响应分别返回 `TIMEOUT` 和 `STALE`。

heartbeat 由 timer 安排。set 从当前 tick 重新计时，stop 可以重复调用；已经进入队列的 timer 事件保持可读。

## Workflow Credit Domain

资源账户用 free、reserved、charged 三态描述硬额度：

```text
reserve: F -> P
publish: P -> U
failure: P -> F
destroy: U -> F
held = F + P + U
```

account、resource class 和 global limit 同时约束 held。资源不足时创建请求返回 `NO_SPACE`。Tool Phase Credit Lease 从已计入的资源中锁定工具执行所需的短期额度，并在工具终态发布时完成结算。

资源快照覆盖 process、thread、file object、filesystem block、filesystem inode、buffer cache、Agent state page 和 physical page 八类对象。workflow close 根据成员、active operation、资源账户、Task Channel 和后台任务的状态推进回收。

## Workflow EEVDF

AgentOS 把 workflow 作为跨进程调度实体，外层实体使用固定等权服务。latency class 与 wall deadline 决定 service request，睡眠衰减把 vruntime 向全局 vtime 调整；调度器从 eligible 集合中选择 virtual deadline 最早的 workflow。同一 workflow 的成员共享 service-cycle 账户，实体内部继续使用 uCore 的线程选择与 per-Agent weight、priority、budget 策略。

调度状态在 enqueue、dequeue、dispatch、sleep 和 tick 时更新。单 workflow 使用 fast path；状态异常时回到 uCore 调度路径并记录 fallback。当前 RISC-V64 Guest 使用单 Hart，同时跟踪最多 4 个 active workflow。

```c
int agent_sched_config(struct agent_sched_config *config);
int agent_sched_snapshot(struct agent_sched_record *records, int max);
int agent_resource_snapshot(struct agent_resource_snapshot *snapshot);
int agent_performance_snapshot(struct agent_performance_snapshot *snapshot);
```

6 次独立启动共记录 504 次 exact wake probe，其中 425 次为 0 tick、79 次为 1 tick。并发度 1 至 4 的 Jain fairness 中位数为 `1.000000/0.999985/0.999993/0.999985`。分布与逐 workflow service 数据见[性能结果](../performance.md)。

## 执行记录与 Workflow fence

工具和 Context 发布完成后，内核把 sequence、cause/span/branch、actor、tool/status 和 record hash 写入有界 ring。每个 workflow 按需使用 4 页，包含 48 个 ordinary 槽和 16 个 critical 槽。producer 通过 `reserve -> fill -> commit/discard` 更新 ring，ticket gap 参与最终 seal。

controller 发起 workflow fence 后，内核阻止新 operation 与 departure，排空 metadata 更新，取得资源快照，再封存当前有序记录。成功 receipt 为 320 字节，包含调用者 challenge、lifecycle key、fence sequence、metadata generation、credit epoch、记录范围、资源摘要、previous root 和本次 root。receipt 的 flags 标记 partial coverage、精确 credit、记录 seal 和 volatile metadata；fence 完成后 lifecycle 可以继续运行。同一 request id 与 challenge 的重试返回原 receipt。

```c
int agent_workflow_fence(
    const struct agent_workflow_fence_request *request,
    struct agent_workflow_fence_receipt *receipt);
```

## 上层运行方式

`agentlive_ucore` 使用事件、Context 和 typed tool 运行长驻 Agent Loop。Host relay 处理串口 frame、TLS 和 provider JSON，Guest 保留 turn、correlation、工具目录和执行状态。

`agentnexus_ucore` 在同一 workflow 中组织 Coordinator、System、Research 和 Analyst。四个 Agent 使用独立 PID、身份和 Context，通过内核 `MESSAGE` 与 workflow-scoped artifact 传递阶段结果。Nexus 的应用流程与运行命令见[运行指南](../usage.md)。

## 实现位置

| 模块 | 源码 |
| --- | --- |
| Event queue、watch/wait 与 IPC | [`os/agent_ipc.c`](../../os/agent_ipc.c)、[`os/agent_core.c`](../../os/agent_core.c) |
| Heartbeat 与 timer | [`os/agent_background.c`](../../os/agent_background.c)、[`os/timer.c`](../../os/timer.c) |
| Workflow Credit Domain | [`os/workflow_credit_domain.c`](../../os/workflow_credit_domain.c)、[`os/resource_controller.c`](../../os/resource_controller.c) |
| Workflow EEVDF | [`os/workflow_scheduler.c`](../../os/workflow_scheduler.c) |
| 执行记录与 fence | [`os/agent_evidence_ring.c`](../../os/agent_evidence_ring.c)、[`os/agent_workflow_fence.c`](../../os/agent_workflow_fence.c) |
| timeline | [`os/agent_observe_timeline.c`](../../os/agent_observe_timeline.c) |

## 测试入口

`agentloop_ucore` 检查空队列休眠、wakeup、heartbeat、route 与跨域拒绝；`agentsched_ucore` 检查多 Agent 进展与 fallback；`agent_eevdf_ucore` 输出 workflow service 与 exact wake probe。

```bash
python3 -B scripts/test-workflow-credit-domain.py
python3 -B scripts/test-agent-evidence-ring.py
python3 -B scripts/test-workflow-fence.py
AGENT_TEST_CASE=agentloop_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agent_eevdf_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

资源、调度和 fence 结构见 [API](../api.md)，身份与 lifecycle 关系见[身份与 Context](identity-context.md)。
