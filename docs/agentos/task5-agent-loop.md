# 任务五：事件驱动 Agent Loop

## 场景与约束

长驻 Agent 需要在文件变化、消息、heartbeat 或 deadline 到来时继续工作，并在空闲期让出 CPU。事件、IPC 和调度状态来自多个并发来源，必须绑定真实发送者和 workflow generation；关键拒绝还要进入可核验的证据链。

内核提供 loop substrate，Guest 用户态 policy 或 model 决定下一步工具调用。

## 方案

```text
install watch
      -> agent_wait(timeout)
      -> consume FILE_QUERY / MESSAGE / TIMER / POLICY_DENIED
      -> Guest chooses a structured call
      -> kernel validates and executes
      -> Context record and Evidence event
```

### 事件、watch 与 wait

公开 event queue 容量为 16。内核为控制事件保留槽位，并分别限制普通 IPC 和单一来源，防止外部生产者耗尽全部队列。队列满时返回容量或重试状态；live query 进入 generation-based resync。

`agent_watch()` 订阅传统 event/filter，`agent_live_watch()` 保存 typed file query。token 绑定 target、control id、scope 和 lifecycle generation。`agent_wait()` 使用 thread-generation wait key，并在同一锁合同下完成入队前后重查，避免 lost wakeup。exec、exit 和 proc reset 会清理订阅和等待状态。

### IPC 与 LLM correlation

MESSAGE 投递要求 source/target 属于同一 active lifecycle，route event mask 已授权，source capability 有效，target generation/control id 仍匹配。`agent_wake()` 不能伪造 `FILE_QUERY`、`POLICY_DENIED`、`TIMER` 或 `LLM_DONE`。

`LLM_REQUEST/LLM_RESPONSE` 只提供相关性受控的 Guest RPC。pending 绑定 requester、relay、完整 lifecycle、非零 correlation 和 deadline。correlation 对同一 requester 严格递增，成功投递后才推进；匹配响应消费一次。kernel-tick TTL 为 120 秒，保留期内可区分 `STALE` 与 `TIMEOUT`。

### Heartbeat 与 workflow EEVDF

heartbeat 通过 timer 安排事件。set 从当前 tick 重新计时，stop 幂等，已经入队的事件仍可被消费。

workflow EEVDF 把整个 workflow 当作公平实体。调度器维护 vruntime、lag、request size 和 virtual deadline，从 eligible 集合选择最早 deadline，并按实际 service cycles 记账。同一 workflow 增加线程不会获得额外公平份额。单实体走 fast path，身份或容量异常时回退原调度器并记录 fallback。

### Evidence 与 fence

工具和 Context 发布后，内核把 sequence、cause/span/branch、actor、tool/status 和 record hash 写入 Evidence Ring。每 workflow 按需使用 4 页，包含 48 个 ordinary 槽和 16 个 critical 槽。producer 执行 `reserve -> fill -> commit/discard`，ticket gap 也进入 seal。

controller 发起 fence 时先阻止新 operation/departure，再取得 metadata、filesystem epoch 和精确 U credit cut，最后以 32 字节 challenge 封存 ordered evidence。成功 receipt 为 320 字节，绑定 ticket 范围、event/gap 数、metadata generation 和 credit digest。同 request id/challenge 重试返回相同 receipt。

## 关键实现

| 职责 | 源码 |
| --- | --- |
| Event queue、watch/wait 与 IPC | [os/agent_ipc.c](../../os/agent_ipc.c)、[os/agent_core.c](../../os/agent_core.c) |
| Typed file event | [os/agent_live_query_events.c](../../os/agent_live_query_events.c) |
| Heartbeat 与 timer | [os/agent_background.c](../../os/agent_background.c)、[os/timer.c](../../os/timer.c) |
| Workflow EEVDF | [os/workflow_scheduler.c](../../os/workflow_scheduler.c) |
| Evidence Ring | [os/agent_evidence_ring.c](../../os/agent_evidence_ring.c) |
| Workflow fence | [os/agent_workflow_fence.c](../../os/agent_workflow_fence.c) |
| 兼容 audit/timeline 投影 | [os/agent_observe.c](../../os/agent_observe.c)、[os/agent_observe_timeline.c](../../os/agent_observe_timeline.c) |

## 验证与量化

```bash
python -B scripts/test-agent-evidence-ring.py
python -B scripts/test-workflow-fence.py
AGENT_TEST_CASE=agentloop_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentsched_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agent_eevdf_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
```

`agentloop_ucore` 验证无事件休眠、wakeup、heartbeat、route 和跨域拒绝。`agentsched_ucore` 覆盖多 Agent 进展与回退。`agent_eevdf_ucore` 记录 workflow service、Jain fairness 和 wakeup probe。

一次性活动保留 504 条 exact wake probe，其中 425 条为 0 tick、79 条为 1 tick；调度 tick 为 10 ms。基于 raw `service_cycles` 重算的 Jain 中位数在并发 1/2/3/4 下分别为 `1.000000/0.999985/0.999993/0.999985`。原始数据见 [高级性能图](advanced-performance-figures.md)。

## 当前边界

- Guest 内核为单 Hart；公平性描述的是 workflow service 分配。
- EEVDF 同时最多跟踪 4 个 active workflow。测量拓扑为 bootstrap 加最多 3 个 fresh workflow。
- 16 档由四波逻辑样本组成，四波复用 bootstrap 并累计 12 个 fresh lifecycle。
- heartbeat 只生成事件，不在内核执行 Agent 业务。
- LLM correlation 不包含 HTTPS、模型语义或远程 exactly-once。
- Evidence、audit 和 fence receipt 覆盖当前启动周期内的有界内存窗口。
