# 任务五：Agent Loop、IPC 与 fence evidence

任务五把有界事件队列、watch/wait、heartbeat、可信 IPC、Agent 感知调度和 Context evidence 连接为一条内核驱动循环。当前证据热路径以 Fence-Sealed Evidence Ring 为 canonical 存储，同时保留 audit/timeline/provenance/ledger 兼容读取视图。

## 1. Agent Loop

典型循环为：

```text
install typed/legacy watch
        |
        v
agent_wait(timeout)
        |
        +--> FILE_QUERY: query current metadata / handle transition
        +--> MESSAGE or LLM_DONE: follow authorized route
        +--> TIMER: heartbeat or deadline work
        +--> POLICY_DENIED: inspect Context/audit compatibility view
        |
        v
structured tool call -> Context record -> Evidence Ring
```

事件不是用户态共享内存里的无鉴别消息。内核分配 event id/tick，绑定 source/target、scope、cause/span，并在消费时再次验证线程/process generation。

## 2. 有界事件队列

公开容量由 `AGENT_EVENT_QUEUE_CAP` 等 UAPI 常量定义。队列为内核事件、IPC 和各来源保留独立预算，避免单一外部来源占满全部槽。

- kernel reserve 保证 policy/timeout/live-query resync 等控制事件仍有空间；
- external/IPC/source limit 限制普通投递；
- 队列满不静默伪装成功；live query 转为 `RESYNC_REQUIRED`，普通 IPC 返回容量/重试状态；
- event id 与 lifecycle generation 防止旧事件被新进程消费。

## 3. watch 与 wait

### 3.1 watch

- `agent_watch()` 支持传统 event type + string filter；
- `agent_live_watch()` 固定使用 `AGENT_EVENT_FILE_QUERY` 和 typed query；
- typed watch 产生 `ENTER/UPDATE/LEAVE/RESYNC_REQUIRED`；
- watch token 绑定 target pointer、control id、scope 和 lifecycle generation；
- exec、exit、unwatch 和 proc reset 清除订阅。

### 3.2 wait

`agent_wait()` 使用线程级等待状态和 generation-safe wait key。入队前后在同一锁合同下重查，避免 lost wakeup。有限 timeout 使用独立 deadline，不被无关 churn 延长；cancel 只能由具备能力的调用者作用于当前 target generation。

timeline 的 wait/read API 使用独立 waiter 状态，发布路径只唤醒真正匹配 source/filter/cursor 的线程。

## 4. 可信 IPC

跨 Agent 的 MESSAGE/LLM delivery 需要：

- source 与 target 都属于同一 active workflow lifecycle；
- 定向 route 的 event mask 已授权；
- source capability 允许发起相应动作；
- target generation 与 control id 仍匹配；
- 队列预算允许。

`agent_wake()` 不能伪造 `FILE_QUERY`、`POLICY_DENIED`、`TIMER`、`LLM_DONE` 等内核/专用工具事件。cross-scope 即使 PID 可见也会拒绝。

## 5. heartbeat 与调度

heartbeat 在内核 timer 上安排事件，不执行 Agent 业务。set 从当前 tick 重新计时，stop 幂等且不删除已经入队的事件。

Agent 感知调度综合 role weight、priority、event queue、waiting、deadline、heartbeat、budget 和 vruntime。调度 trace 有界，稀疏采样进入兼容观测视图。当前内核为单 Hart；并行 QEMU lane 只提高 Host 测试吞吐。

## 6. Context 到 Evidence Ring

每次结构化 tool/Context 发布后，observe 层分配 audit sequence，并构造一条 canonical evidence event：

- Context sequence/request/tick；
- cause/span/branch/path parent；
- actor/cause control id 与 record hash；
- PID/TID/role/loop state；
- tool id、status、value 和短 payload/result。

分类规则明确：

- 普通成功、无授权效果：ordinary ring，只写一次 canonical event；
- 拒绝或授权效果：critical ring，并立即生成兼容 ledger 投影；
- ring 不可用：timeline 仍发布，并 fail closed 到 legacy protected ledger。

这不是“所有 audit/timeline/provenance 已删除”。兼容 API 仍从 ring 和 legacy 来源构造视图，event/sched 记录也可留在 ledger。

## 7. Ring 并发和容量

每 workflow 按需计费 4 页：48 ordinary + 16 critical。producer 使用 `reserve -> fill -> commit/discard`：

1. IRQ-off 短临界区分配单调 ticket 并把槽置 BUSY；
2. IRQ-on 填充 256 字节 event；
3. IRQ-off 校验 ticket/key 后 COMMIT；
4. 失败则 DISCARD，并把 ticket 计为 gap。

reader 只接受 COMMITTED 且 ticket 匹配的槽。若较早 ticket 仍 BUSY，则不发布更晚 ticket，保持严格顺序。ring 满时把当前 segment 滚入内部 root 后复用槽；critical 与 ordinary 分区避免成功 telemetry 驱逐关键拒绝。

## 8. workflow fence

只有 controller/orchestrator 能发起 fence。lifecycle fence gate 先阻止新 operation/departure，然后依次取得 metadata/live-query cut、filesystem epoch cut、精确 U credit snapshot，最后 seal evidence。

seal 绑定：

- 32 字节 challenge；
- fence sequence；
- previous public root；
- ticket first/last、event count、gap count；
- metadata generation；
- credit epoch/digest；
- ordered segment event/gap digest。

成功 receipt 为 320 字节，带 `PARTIAL_COVERAGE | CREDIT_EXACT | EVIDENCE_SEALED | METADATA_VOLATILE`。同 request id/challenge 重试返回相同 receipt，避免 copyout 失败后重复 seal。

## 9. audit、timeline、ledger 与 provenance

| API | 当前数据来源 | 限制 |
| --- | --- | --- |
| audit query/snapshot | Evidence Ring + critical/fallback/event/sched legacy ledger | 有界内存视图，不是磁盘日志 |
| timeline query/wait/read | Context/ring、sched、legacy audit 的有序投影 | 过滤/游标只对当前可见窗口成立 |
| provenance snapshot | Context cause/span/branch/control + legacy audit edge | 不把 ring Context 再复制成第二条 edge |
| ledger snapshot | 当前 scope 的聚合计数、窗口与 evidence digest tag | `ledger_hash` 不是 fence SHA-256 根 |
| audit receipt | 关键兼容投影对应 ring ticket | 肯定状态为 `FENCE_SEALED`，不是 disk durable |

`AGENT_AUDIT_DURABILITY_DURABLE` 是源码兼容别名。当前语义等同 `FENCE_SEALED`。

## 10. 已停产 recovery

observe recovery 请求结构和 syscall 编号仍保留，防止旧编号被新功能复用。但 dispatcher 固定返回 `AGENT_STATUS_BAD_PARAM`：

- 不存在 observation disk bank；
- 不提供 scope list/read/reap/status；
- 不从重启镜像恢复 audit/timeline/provenance；
- workflow fence receipt 不能跨 crash 重新枚举。

因此“fence-sealed”只能陈述当前运行期的可验证根，不得写成 durable/crash-recoverable。

## 11. 设计来源

Evidence Ring 受 Linux BPF ring buffer 的共享有序 ring、reserve/commit/discard 和通知策略启发。AgentOS 增加 workflow generation、ordinary/critical 分区、Agent 因果字段、gap 承诺和 challenge-bound fence。代码为 clean-room 实现，没有复制 BPF 源码、测试数据或二进制布局。

## 12. 验证入口

```bash
python -B scripts/test-agent-evidence-ring.py
python -B scripts/test-workflow-fence.py
python -B scripts/test-workflow-syscall-cut.py
python -B scripts/test-agent-live-query-fs.py
make agent-module-check TOOLPREFIX=riscv-none-elf-
```

Guest event、route、wait、timeline 和调度行为由 AgentOS 专项程序直接验证；调度数字由对应性能场景的实际输出读取。
