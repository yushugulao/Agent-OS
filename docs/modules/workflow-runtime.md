# 工作流运行时

AgentOS 把事件等待、可信 IPC、模型请求、资源账户、工作流 EEVDF 和阶段记录放在同一个生命周期中。智能体暂时没有任务时进入内核等待。文件变化、协作消息、心跳、截止时间或模型响应到达后，内核将其唤醒。工具与事件的最终状态继续写入上下文和工作流执行记录。

## 文档索引

- [一、运行过程](#一运行过程)
- [二、事件队列与无丢失唤醒](#二事件队列与无丢失唤醒)
- [三、可信 IPC 与模型请求](#三可信-ipc-与模型请求)
- [四、工作流资源账户](#四工作流资源账户)
- [五、工作流 EEVDF](#五工作流-eevdf)
- [六、执行记录与阶段快照](#六执行记录与阶段快照)
- [七、Nexus 多智能体运行时](#七nexus-多智能体运行时)
- [八、源码位置与测试](#八源码位置与测试)

## 一、运行过程

多智能体工作流不会一直占用 CPU。模型调用、文件生成和协作者执行任务时，其他进程常常处于等待状态。事件到来后，内核需要确认接收者仍属于原来的工作流，不能把迟到响应交给槽位复用后的新进程。一个工作流还可能创建多个线程。若调度器只按线程轮转，线程越多的工作流就会获得更多 CPU 时间。

运行时为此提供五组内核功能：

1. 事件队列保存文件变化、消息、定时器、策略拒绝和模型完成事件。
2. `agent_wait()` 以线程代次作为等待键，在同一次关中断期间复查队列并进入睡眠。
3. IPC 消息路由和模型请求表保存生命周期、`control_id` 与关联编号。
4. 工作流资源账户限制内核对象的创建，工作流 EEVDF 在不同工作流之间分配 CPU 时间。
5. 执行记录环保存最终状态，阶段快照汇总文件元数据、资源用量和执行记录。

一轮常见的智能体循环如下：

```text
安装文件订阅、IPC 消息路由和执行约定
  -> agent_wait(timeout)
  -> 处理 FILE_QUERY、MESSAGE、TIMER、POLICY_DENIED 或 LLM_DONE
  -> 提交结构化工具请求
  -> 内核提交结果、上下文和执行记录
  -> 再次等待
```

## 二、事件队列与无丢失唤醒

### 2.1 事件结构

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

事件类型包括文件状态、跨智能体消息、定时器、任务或模型完成、策略拒绝、上下文容量告警、取消和带类型信息的文件查询。`cause_sequence` 与 `span_id` 把事件处理接回上下文，`corr_id` 用来匹配消息和模型请求。

### 2.2 队列容量与来源隔离

每个智能体的事件队列固定为 16 槽。内核按来源分别计数：

| 限制 | 当前值 | 作用 |
| --- | ---: | --- |
| 队列总容量 | 16 | 限制全部事件数 |
| 内核保留槽 | 4 | 留给定时器、取消等内核事件 |
| 外部事件上限 | 12 | 防止外部事件占用内核保留槽 |
| 类别保留槽 | 4 | 防止 IPC 或带来源信息的事件挤满全部外部槽 |
| 单一来源上限 | 4 | 限制同一个发送者的积压 |

心跳等内核定时事件会合并投递：队列中已有同类事件时，不再重复加入。外部 IPC、带来源信息的事件和内核事件分别通过 [`agent_ipc_origin_policy`](../../os/agent_ipc.c) 检查。容量判断、事件编号分配和队尾发布都在同一次关中断期间完成。

### 2.3 等待与唤醒

`agent_wait()` 必须处理一个很短却很关键的时序：线程确认队列为空之后、真正睡眠之前，事件可能已经到达。实现位于 [`os/agent_ipc.c`](../../os/agent_ipc.c)，执行顺序如下：

```text
关中断
  -> 复查取消状态和队首
  -> 有事件：预留队首并恢复运行
  -> 无事件：登记 WAITING、截止时间和等待键
  -> wait_queue_sleep_key_irq(identity_generation)
  -> 唤醒后在同一套锁规则下重新检查
```

等待键使用线程的 `identity_generation`。时钟中断只唤醒身份代次、等待通道、等待原因和截止时间全部匹配的线程。`exec`、线程槽位复用和退出清理不会把旧唤醒交给新线程。

队列通过事件接力标记确定接收者。事件到达后，[`wait_queue_wake_one_thread()`](../../os/wait.c) 只挑选一个真正等待的线程，并为其设置接力标记。该线程预留队首，成功复制到用户态并写入上下文后才消费事件。复制失败时，内核撤销预留并把接力标记交给其他等待者。1、4、8、15 个等待线程的测试共完成 28 次唤醒，均记录 `herd=0`。

## 三、可信 IPC 与模型请求

### 3.1 IPC 消息路由

跨智能体 `MESSAGE` 先确认发送方和接收方都仍是有效智能体，再比较文件访问范围和完整生命周期。每个接收方最多保存 16 条消息路由。每条路由以发送方 `control_id` 为键，并记录允许的事件类型。普通发送者需要 `MESSAGE_SEND` 能力位。替他人配置路由需要 `ROUTE_MANAGE`，而且控制进程必须同时控制发送方与接收方。

发送成功后，内核队列项会保存发送方 PID、`control_id`、起因序号、调用跨度和来源标记。公开的 `agent_event` 只返回事件字段，控制编号和来源信息留在内核附加区。接收方处理事件时，内核会先把 `CROSS_AGENT_DATA` 等标记合并到当前来源状态，再追加一条带因果关系的上下文记录。

常用接口如下：

```c
int agent_route_config(int source_pid, int target_pid,
                       uint64 event_mask, int operation);
int agent_wake(int pid, struct agent_event *event);
int agent_wait(struct agent_event *event, int timeout_ticks);
int agent_wait_cancel(int pid, const char *reason);
```

### 3.2 模型请求与响应

结构化工具 `LLM_REQUEST` 和 `LLM_RESPONSE` 共用事件通道，但会额外登记到待完成请求表。每条记录保存请求进程和中继进程的 PID、`control_id`、完整生命周期键、非零关联编号和截止时间。同一请求进程在一个生命周期内提交的关联编号必须递增。仍在处理的编号重复出现时返回 `DUPLICATE`。已经完成或超时的编号再次出现，以及编号倒退时，返回 `CONFLICT`。

响应只有在请求进程、中继进程、生命周期和关联编号全部匹配时才会生效，而且只能消费一次。内核时钟规定的有效期为 120 秒。到期记录会进入最终历史表，迟到响应返回 `TIMEOUT` 或 `STALE`。每个请求进程最多保留 16 条待完成记录，全局表容量为 `NPROC`。实现见 [`os/agent_core.c`](../../os/agent_core.c)。

### 3.3 心跳

`agent_heartbeat_set(interval)` 从当前时钟滴答重新计时，`interval` 为 0 时停止心跳。到期后，内核在队列中加入合并后的 `TIMER` 事件。停止心跳不会删除已经入队的事件。`agent_heartbeat_stop()` 可以重复调用。心跳和取消事件成功取出后，会按照事件预留和上下文提交的正常路径处理。本地等待超时则直接返回 `TIMEOUT`，不占用队列槽位，也不写上下文记录。

## 四、工作流资源账户

一个工作流会同时创建进程、线程、文件对象、inode、缓冲区和物理页。如果只在分配失败后再清理，内核无法提前判断整个工作流是否还能继续创建对象。资源控制器因此为每个工作流建立执行账户和存储账户，并把额度分为三种状态：

```text
空闲 --预留--> 待发布 --发布--> 已使用
  ^                 |
  +----创建失败-----+
  ^
  +------释放 <----- 已使用

持有额度 = 空闲 + 待发布 + 已使用
```

空闲额度已经留给该账户，但尚未使用。待发布额度属于正在创建、尚未对外可见的对象，已使用额度属于已经发布的对象。账户总限额、资源类别限额和全局容量共同约束持有额度。创建失败时，待发布额度退回空闲。对象发布后，待发布转为已使用。对象销毁后，额度回到空闲，并可在系统压力较大时归还全局池。

资源快照统计 8 类对象：进程、线程、文件对象、文件系统块、文件系统 inode、缓冲区、智能体状态页和物理页。共用 ABI 见 [`include/agent_resource_abi.h`](../../include/agent_resource_abi.h)，账户与策略实现在 [`os/resource_controller.c`](../../os/resource_controller.c)。

每次工具执行都有一份独立的资源记录。V3 执行约定给出本次操作的资源上限，内核先调用 `begin` 锁定额度，操作生效前调用 `activate`，工具结束时调用 `settle` 结算实际用量。线程或进程异常退出时调用 `abort` 退回预留额度。关闭工作流时，生命周期先标记为正在关闭并要求成员退出。已经开始的操作完成结算后，异步收尾程序再回收生命周期。

## 五、工作流 EEVDF

### 5.1 调度目标

uCore 原有调度器直接从可运行线程中选择任务。若一个工作流创建更多线程，它可能在外层轮转时获得更多 CPU 时间。AgentOS 为每个工作流建立一个 EEVDF 调度对象。同一工作流的所有成员共享 `service_cycles`、`vruntime` 和 `virtual_deadline`。选中工作流后，再由 uCore 原有的单智能体或 FIFO 策略选择具体线程。

主要字段定义在 [`os/workflow_scheduler.c`](../../os/workflow_scheduler.c)：

| 字段 | 含义 |
| --- | --- |
| `lifecycle + account + domain_id` | 调度对象的完整身份 |
| `vruntime` | 按等权规则结算的虚拟运行时间 |
| `remaining_cycles` | 本次 CPU 时间申请的剩余周期数 |
| `virtual_deadline` | `vruntime + remaining_cycles` |
| `runnable_threads` | 当前可运行的工作流成员数 |
| `latency_class` | `urgent`、`interactive`、`normal`、`batch` 四档延迟等级 |
| `sleep_start_tick`、`wake_tick` | 睡眠修正和唤醒等待统计 |

### 5.2 选择方法

四档延迟等级分别申请 1、2、4、8 个时钟滴答，实际截止时间只能缩短申请量。调度器用全局 `vtime` 判断工作流当前能否运行，再选择虚拟截止时间最早者：

```text
request_cycles = request_ticks * cycles_per_tick
virtual_deadline = vruntime + remaining_cycles
eligible = (vruntime <= vtime)
selected = arg min(virtual_deadline) among eligible workflows
```

所有工作流的权重固定为 1024，实际运行的周期数直接计入该工作流的 `vruntime`。工作流每睡眠 16 个时钟滴答执行一次修正，最多右移 8 次，让较旧的 `vruntime` 向当前 `vtime` 靠近。这样，刚被唤醒的交互工作流可以及时恢复运行，但已经结算的 CPU 时间不会增加。

[`fetch_task()`](../../os/proc.c) 从运行队列缓存中收集各工作流的候选线程。只有一个工作流时，系统保留原来的 O(1) 外层选择。多个工作流同时运行时，调用 `workflow_scheduler_select()`。候选身份、可运行缓存或对象映射异常时，调度器退回原有外层轮转，并增加备用路径计数。工作流进入可运行状态时记录时钟滴答，真正获得 CPU 时计算等待时间，切回调度器后用周期计数器结算服务量。

### 5.3 实测结果

6 次独立 QEMU 启动共保存 504 条准确唤醒记录，其中 425 条为 0 个时钟滴答，79 条为 1 个时钟滴答。所有工作流从进入可运行状态到真正获得 CPU，等待均为 0 至 1 个时钟滴答。按同一次启动、同一并发场景中各工作流的 `service_cycles` 计算 Jain 公平性指数，中位数如下：

| 并发工作流数 | 启动次数 | Jain 公平性指数中位数 |
| ---: | ---: | ---: |
| 1 | 6 | 1.000000 |
| 2 | 6 | 0.999985 |
| 3 | 6 | 0.999993 |
| 4 | 6 | 0.999985 |

逐次唤醒数据位于 [`one_shot_metrics/data/20260811/tables/eevdf_wakeups.csv`](../../one_shot_metrics/data/20260811/tables/eevdf_wakeups.csv)，各工作流 CPU 周期数据位于 [`eevdf_samples.csv`](../../one_shot_metrics/data/20260811/tables/eevdf_samples.csv)，累计分布和公平性图见[性能测试](../performance.md#7-工作流-eevdf-调度)。

## 六、执行记录与阶段快照

### 6.1 执行记录环

上下文的最终状态、事件、审计记录和时间线都会写入工作流执行记录环。每个生命周期按需分配 4 页，其中 48 槽保存普通记录，16 槽保存关键记录。安全策略拒绝等重要信息写入关键分区，不会被普通成功日志占满。

写入者使用 `reserve -> fill -> commit/discard` 两阶段接口取得递增票号。工具生效前会同时在普通分区和关键分区预留槽位，最终状态确定后只提交其中一个。没有提交的票号形成缺口，并记入封存摘要。结构与接口见 [`os/agent_evidence_ring.h`](../../os/agent_evidence_ring.h) 和 [`os/agent_evidence_ring.c`](../../os/agent_evidence_ring.c)。

### 6.2 生成阶段快照

控制进程调用 `agent_workflow_fence()` 时，内核依次执行：

1. 检查 `Orchestrator` 能力和控制进程的 `control_id`。若同时传入请求和回执，再检查版本和非零请求编号。两个指针都为空时，只生成快照，不返回回执。
2. 对非零请求编号查询重试缓存。请求编号和 `challenge` 都相同便直接返回已提交回执。
3. 暂停接纳新的普通操作和退出操作，并确认两项计数都为 0。
4. 在文件元数据不再变动后取得其代次，排空延迟回收的文件系统对象，并提交文件系统时点。
5. 读取执行账户和存储账户的准确资源快照。
6. 用 `challenge`、文件元数据代次、资源时点和资源摘要封存执行记录段。
7. 缓存 320 字节回执，递增阶段快照序号，并恢复接纳新操作。

核心顺序位于 [`agent_workflow_fence_execute()`](../../os/agent_workflow_fence.c)：

```c
agent_metadata_quiescence_fence_snapshot_current(&metadata_generation);
fs_deferred_reclaim_drain_current();
fs_epoch_commit();
workflow_credit_domain_fence(key, exec, storage, &credit);
agent_evidence_seal(key, fence_sequence, challenge,
                    metadata_generation, credit.epoch,
                    credit_digest, &evidence);
```

回执包含生命周期键、请求序号、阶段快照序号、文件元数据代次、资源时点、8 类资源用量、资源摘要、`challenge`、前一段根哈希和本段执行记录根哈希。当前 v1 固定带有 `PARTIAL_COVERAGE`、`CREDIT_EXACT`、`EVIDENCE_SEALED` 和 `METADATA_VOLATILE` 标记。回执使用 SHA-256 串联阶段记录，结构中没有公钥签名字段。ABI 定义见 [`include/agent_workflow_fence_abi.h`](../../include/agent_workflow_fence_abi.h)。

对于带请求编号的调用，同一编号配不同 `challenge` 返回 `CONFLICT`，更旧编号返回 `STALE`。上一次生成的回执尚未成功复制到用户态时，后续请求返回 `RETRY`。不要求回执的调用不进入这套重放检查。生成失败时，内核恢复接纳新操作，也不会递增阶段快照序号。

## 七、Nexus 多智能体运行时

<p align="center">
  <img src="../figures/architecture/nexus_runtime_flow.jpg" alt="Nexus 多智能体协作流程" width="960">
</p>

**图 1　Nexus 多智能体协作流程**

原生图源见 [`nexus_runtime_flow.drawio`](../figures/architecture/nexus_runtime_flow.drawio)。

`agentnexus_ucore` 在同一工作流中创建四个独立进程。它们各有自己的 PID、智能体身份和上下文，但共用生命周期、文件访问范围和工作流资源账户。

| 角色 | 主要任务 | 协作对象 |
| --- | --- | --- |
| 协调智能体 | 制订计划、分派任务、读取各角色结果、发起报告发布 | 系统观察、资料检索、分析 |
| 系统观察智能体 | 读取进程、上下文和内核状态，形成系统状态结果 | 协调、分析 |
| 资料检索智能体 | 查询工作流文件并核对本地材料，形成资料结果 | 协调、分析 |
| 分析智能体 | 读取系统状态与资料结果，生成综合报告 | 协调 |

协调智能体通过内核 `MESSAGE` 发送带类型信息的任务。系统观察和资料检索智能体用 `PROGRESS` 消息返回阶段数据，协调智能体收到后把数据写成工作流内结果文件，再把文件句柄交给下一角色。分析智能体通过 `TASK_RESULT` 返回报告句柄。协调智能体随后发起需要审批的发布请求。内核确认发布参数与用户批准的内容一致后，才执行最终发布。结果文件的读取权限由权限掩码控制。外部模型仍由宿主机中继连接，客户机只保存轮次、关联编号、角色、工具目录和结果文件状态。

客户机主程序位于 [`user/src/agentnexus_ucore.c`](../../user/src/agentnexus_ucore.c)，共用协议见 [`user/include/agent_nexus_protocol.h`](../../user/include/agent_nexus_protocol.h)，宿主机连接沿用 [`host_tools/agentos_relayd.py`](../../host_tools/agentos_relayd.py)。运行方法见[运行指南](../usage.md#4-使用多智能体工作流)。

## 八、源码位置与测试

| 模块 | 主要源码 |
| --- | --- |
| 事件队列、文件订阅、等待和消息路由 | [`os/agent_ipc.c`](../../os/agent_ipc.c)、[`os/wait.c`](../../os/wait.c) |
| 模型待完成请求与智能体循环 | [`os/agent_core.c`](../../os/agent_core.c)、[`os/agent_background.c`](../../os/agent_background.c) |
| 工作流资源账户与工具执行资源记录 | [`os/workflow_credit_domain.c`](../../os/workflow_credit_domain.c)、[`os/resource_controller.c`](../../os/resource_controller.c) |
| 工作流 EEVDF | [`os/workflow_scheduler.c`](../../os/workflow_scheduler.c)、[`os/proc.c`](../../os/proc.c) |
| 执行记录与阶段快照 | [`os/agent_evidence_ring.c`](../../os/agent_evidence_ring.c)、[`os/agent_workflow_fence.c`](../../os/agent_workflow_fence.c) |
| 运行时间线 | [`os/agent_observe_timeline.c`](../../os/agent_observe_timeline.c) |

运行时测试覆盖无丢失唤醒、慢订阅者隔离、心跳、消息路由、资源账户、执行记录票号、阶段快照重试、EEVDF 和 Nexus 回放：

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

客户机日志校验器检查 `broadcast_slow_watcher_isolated=1`、心跳动态调整、合并与停止、28 次没有惊群的事件交接、EEVDF 拓扑和唤醒分组等标记。固定性能数据由 [`one_shot_metrics/validate.py`](../../one_shot_metrics/validate.py) 校验。绘图数据检查覆盖 504 次准确唤醒和 6 次公平性测试启动。

运行时使用[身份、生命周期与上下文](identity-context.md)提供的身份键，并处理[工具执行](tool-execution.md)和[文件实时查询](live-query.md)产生的最终状态与事件。
