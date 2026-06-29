# 场景运行脚本

本文档用于按固定顺序复现 AgentOS-uCore 的主要能力。推荐顺序是：先说明系统目标，再运行正确性测试，再运行性能测试，最后运行多 Agent 综合场景。

## 1. 开场说明

本项目在 uCore 内核上实现 Agent-OS，把 Agent 进程身份、结构化工具调用、上下文历史、文件元数据索引和 Agent 事件运行机制放入内核支持层。

当前示例分为九部分：

```bash
agentfinal_ucore
agentfs_ucore
agentscan_ucore
agentloop_ucore
agentsched_ucore
agentbench_ucore
labbench_ucore
labdemo_ucore
agentsecurity_ucore
```

各程序分工：

| 程序 | 作用 |
| --- | --- |
| `agentfinal_ucore` | 覆盖任务一至三核心功能，同时检查文件索引和事件自唤醒 |
| `agentfs_ucore` | 检查任务四的真实 inode 绑定、私有 `.agentmeta` 重新加载和索引查询 |
| `agentscan_ucore` | 检查任务四的根目录自动扫描、真实文件元数据建立和索引维护 |
| `agentloop_ucore` | 检查任务五的 FIFO 事件队列、unwatch、有限 timeout 睡眠、wait cancel、TIMER unwatch 和 heartbeat stop |
| `agentsched_ucore` | 检查任务五的 Agent 感知调度、受权配置、事件状态、调度原因和公平性计数 |
| `agentbench_ucore` | 给出批量调用、Context 直接读、snapshot、文件索引候选记录数的性能证据，并验证 timeout/heartbeat、busy polling 与 wait/wake 计时 |
| `labbench_ucore` | 综合场景中的性能入口，当前包装运行 `agentbench_ucore` |
| `labdemo_ucore` | 呈现一个由 orchestrator 控制的多 Agent 实验恢复场景 |
| `agentsecurity_ucore` | 呈现普通进程和低权限 Agent 无法越权，并验证普通 mail 与多 run 精确恢复 |

## 2. 环境和运行方式

示例前说明当前环境：

- WSL2 Ubuntu 26.04；
- QEMU riscv64；
- `riscv64-linux-gnu-` 工具链；
- 当前目标：AgentOS-uCore 增强目标。

推荐运行：

```bash
bash scripts/run-agent-tests.sh
```

如果需要分步示例，可以分别运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfs_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentscan_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentloop_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsched_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsecurity_ucore CHAPTER=agent
```

## 3. 正确性示例

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
```

这一段不需要逐行朗读全部测试输出。建议把串口输出按以下观察点讲清楚，完整原始输出放在 [test-record.md](test-record.md) 中查询。

| 观察点 | 讲解重点 |
| --- | --- |
| Context 容量 | 每个 Agent 有固定 Context 区，保存最近 128 条可见历史，并为 detail ring 和用户自管 cache 配置独立区域。 |
| 批量工具调用 | 一次 `agent_run` 可以执行一批工具操作，sequence 连续递增，减少多轮工具调用的系统调用次数。 |
| 短摘要与 detail | Context Path 同时保存短 payload/result 摘要和可按 sequence 查询的完整 detail，方便快速浏览和精确复核。 |
| mirror/cache 与 shadow | 用户态 mirror 适合低开销直接读，内核 shadow 保存可信历史；用户改写 mirror 不会改变 snapshot 结果。 |
| 因果链与完整性 | 记录中包含 cause、span、前序 hash 和当前 hash，多轮工具调用可以串成可追踪路径。 |
| FIFO 淘汰 | 写满后淘汰最旧记录，并同步维护 oldest、latest、dropped 等元信息。 |
| 文件查询与预取提示 | 文件对象查询走 metadata 索引路径，内核能根据查询历史和对象标签给出后续可能关注的 metadata 提示。 |
| 事件等待 | Agent 可 watch、wake、wait，自唤醒测试能说明事件路径可用。 |
| timeline 和 ledger | Context、事件、调度、审计、预取提示可以进入统一记录流，便于按来源、时间和 span 查询。 |

看到 `agentfinal_ucore: passed` 和 `agentfinal_ucore: parent passed` 即可进入下一项。

## 4. 文件系统能力示例

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfs_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `demo_inode` | 用户态示例元数据已经绑定真实根目录文件的 `dev/inum` |
| `custom_inode` | 用户态创建的新文件也能绑定 Agent 元数据 |
| `bulk_index` | 接近 128 条记录时，索引路径检查的候选记录少于扫描路径 |
| `query_plan` | 内核说明本次索引路径按 status 选择 bucket，并检查了多少候选记录 |
| `prefetch_hints` | 内核根据历史查询和对象标签依赖给出后续 metadata 提示 |
| `.agentmeta_reload` | 再次初始化时从私有 `.agentmeta` 重新加载自定义元数据 |
| `clear_status` | 属性清空能够生效 |
| `delete_clears_metadata` | 删除真实文件会同步清理 Agent 元数据 |
| `missing_selector_not_found` | 恢复/报告 selector 没有命中时返回明确失败 |

看到 `agentfs_ucore: passed` 和 `agentfs_ucore: parent passed` 即可进入下一项。

## 5. 自动扫描示例

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentscan_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `background_scan usershell=1` | 内核在调度器空隙扫描根目录，发现镜像中已有真实文件 |
| `auto_file_create=1` | 普通文件 syscall 创建的新文件会自动进入 Agent 文件元数据表 |
| `auto_file_delete=1` | 删除真实文件后，自动元数据会被下一轮扫描清理 |

看到 `agentscan_ucore: passed` 和 `agentscan_ucore: parent passed` 即可进入下一项。

## 6. Agent Loop 示例

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentloop_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `fifo=1` | 事件按照投递顺序被消费 |
| `event_causality=1` | 事件携带 cause/span，消费事件后的工具调用可以继续同一因果链 |
| `overflow_dropped=1` | 16 槽队列满时拒绝新事件，不覆盖旧事件 |
| `unwatch=1` | watch 可删除 |
| `timeout_sleep_no_poll=1` | 有限 timeout 等待进入睡眠，不通过循环消耗 CPU |
| `timer_unwatch=1` | TIMER watch 删除后，heartbeat 不再投递可消费 TIMER 事件 |
| `heartbeat_wake_stop=1` | heartbeat 能唤醒 Agent，停止后不再投递 |
| `wait_cancel=1` | 受权 Agent 能取消目标 Agent 的等待，目标返回取消事件 |

看到 `agentloop_ucore: passed` 和 `agentloop_ucore: parent passed` 即可进入下一项。

## 7. Agent 调度示例

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsched_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `role_weights` | 不同 Agent 角色有不同内核调度权重 |
| `configurable_policy` | orchestrator 可受权调整目标 Agent 的 weight、priority 和 budget |
| `event_priority` | 有待处理事件的 Agent 被调度器识别并记录 |
| `reason_trace` | `agent_sched_snapshot()` 能读出最近调度原因，输出包含事件队列、角色权重和调度分数 |
| `fairness` | 多次让出处理器后，调度次数、让出次数和虚拟运行量都会增长 |

看到调度配置、调度原因和公平性计数检查通过，并出现 `agentsched_ucore: passed` 与 `agentsched_ucore: parent passed` 即可进入下一项。

## 8. 性能示例

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `scalar_agent_run` | 单个工具调用基线 |
| `batch_agent_run` | 64 个 op 合并到一次 syscall，减少内核入口次数 |
| `direct_context` | 用户态直接读 Context 镜像，不需要 syscall |
| `context_query` | 逐条查询历史 |
| `context_snapshot` | 一次返回多条历史，适合批量读取 |
| `file_scan_query` | 文件元数据扫描路径 |
| `file_index_query` | 文件元数据索引路径 |
| `file_digest_read` | 真实文件短预览和内容指纹读取路径 |
| `file_digest_cache` | 呈现重复读取同一真实文件内容证据时的 digest cache 命中 |
| `file_query_records` | 直接呈现扫描路径和索引路径检查的候选记录数量 |
| `file_query_plan` | 直接呈现查询计划和索引选择原因 |
| `file_query_cache` | 呈现重复文件属性查询命中同一 `fs_generation` 下的内核结果缓存 |
| `prefetch_records` | 呈现预取提示 snapshot 返回的 metadata 提示数量 |
| `file_prefetch_snapshot` | 呈现读取预取提示的计时观测 |
| `timeout_heartbeat` | 无事件等待会 timeout，心跳字段可通过 `agent_info()` 观察 |
| `busy_poll_query` | 用户态轮询查询路径的计时观测 |
| `event_wait_wake` | Agent Loop 等待和唤醒计时观测 |

说明性能数字会随 QEMU 和宿主机负载波动。查看时应强调相对趋势和设计原因：减少 syscall 次数、减少重复查询、减少线性扫描。

看到 `agentbench_ucore: passed` 和 `agentbench_ucore: parent passed` 即可结束性能段落。

## 9. 场景示例

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
```

### 9.1 场景设定

一个实验流水线有多个阶段：

- prepare；
- align；
- analyze；
- report；
- archive。

系统中有一个 orchestrator 控制 Agent，以及三个业务 Agent：

| 角色 | 职责 |
| --- | --- |
| orchestrator | 初始化文件元数据、创建业务 Agent、注入失败事件 |
| sentinel | 发现失败事件 |
| investigator | 分析失败原因和影响范围 |
| recovery | 执行恢复动作 |

### 9.2 讲解流程

1. 普通 init 只创建 orchestrator。
2. orchestrator 初始化文件元数据并创建三个业务 Agent。
3. sentinel 监听 `status=failed`。
4. orchestrator 注入 align 阶段失败。
5. sentinel 收到事件并查询文件索引。
6. sentinel 读取内核给出的 metadata 预取提示。
7. sentinel 尝试恢复但权限不足，被内核拒绝。
8. sentinel 发送普通 investigate 消息，内核在 message 入队时把 sentinel 的预取提示交接给 investigator。
9. investigator 查询 align 摘要和依赖，确认影响范围。
10. investigator 从自己的预取提示 snapshot 中读取带 `HANDOFF` 原因位的 analyze 提示，并从当前 span 的全局提示总线确认 source/target pid。
11. investigator 查询当前 span 的系统级短记录，确认 Context、事件和预取交接摘要已经进入内核记录。
12. investigator 输出模板 LLM 解释事件和恢复计划事件，预留 LLM Gateway 和 Planner/Auditor 拆分入口。
13. investigator 输出 Context Snapshot，呈现决策过程中的可审计记录。
14. investigator 唤醒 recovery。
15. recovery 通过权限检查并执行恢复。
16. recovery 重复执行同一恢复动作，内核识别为 duplicate。
17. recovery 写报告状态并输出带 corr_id 的 report 事件。
18. 最终查询报告文件，系统输出 recovered。
19. orchestrator 查询全局审计短记录，确认三个业务 Agent 的 Context、事件、调度和预取交接摘要都可见。
20. orchestrator 按 kind、span、文件状态事件、预取 source/target 和起始 sequence 过滤查询全局短记录。

### 9.3 关键观察点

综合场景的原始事件日志较长，完整样例放在 [test-record.md](test-record.md)。现场讲解时建议按事件链路观察，而不是逐行读串口：

| 阶段 | 观察点 | 机制含义 |
| --- | --- | --- |
| 运行对象建立 | `RUN_OBJECT` | 用户态科研平台把一次运行注册成可追踪对象，后续事件都能归入同一 run。 |
| 监听注册 | `WATCH_REGISTERED` | sentinel 进入事件驱动工作方式，不需要反复扫描状态文件。 |
| 故障注入 | `INCIDENT_CREATED` | 文件状态变化被转换为 Agent 可消费事件。 |
| 文件查询 | `TOOL_CALL/query_file` | sentinel 使用内核文件 metadata 索引定位失败对象。 |
| 预取提示 | `PREFETCH_HINT` | 内核根据已查询对象和依赖关系提示下一步可能需要的 metadata。 |
| 权限拒绝 | `AUDIT/DENIED` | 低权限 Agent 即使知道故障，也不能直接执行恢复动作。 |
| 消息交接 | `MESSAGE`、`handoff_prefetch` | Agent 间消息由内核记录，同时把相关预取提示交给接收者。 |
| 摘要读取 | `read_file_digest`、`PREFETCH_USED` | investigator 按提示读取内容摘要，把普通文件证据纳入记录。 |
| LLM 请求 | `LLM_CALL` | 当前默认走模板 relay，结构化请求和响应摘要仍进入记录流。 |
| 恢复计划 | `PLAN_CREATED` | 用户态生成恢复计划，内核保存 plan、span、引用记录和权限事实。 |
| 上下文快照 | `CONTEXT_SNAPSHOT` | 多轮判断过程可以通过 Context Path 复核。 |
| 恢复动作 | `ACTION` | recovery 通过 capability 检查后提交通用动作。 |
| 幂等处理 | `DUPLICATE` | 重复 corr_id 不会重复执行恢复副作用。 |
| 工件更新 | `ARTIFACT` | 恢复报告状态通过通用 artifact metadata 更新。 |
| 完成状态 | `FINAL` | 运行状态回到 recovered。 |
| 全局记录 | `global_audit`、`unified_timeline`、`provenance_graph` | orchestrator 可读取多 Agent 协作的统一短记录、时间线和来源关系。 |

看到 `labdemo_ucore: passed` 和 `labdemo_ucore: parent passed` 即可结束综合场景。

## 10. 结尾总结

本项目在 uCore 上实现了 Agent 进程、工具调用、Context Path、文件元数据索引和 Agent Loop。综合场景把这些功能组合成一个完整的内核级 Agent 协作系统，并由 orchestrator 读取和过滤全局短记录说明多 Agent 协作过程，而不是只停留在分散 syscall 的层面。

补充安全验证可运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsecurity_ucore CHAPTER=agent
```

该程序覆盖普通进程 mail 最小路径；普通进程不能直接投递事件、取消 Agent 等待或修改 Agent 文件元数据；usershell 等价路径可以创建 orchestrator；初始化前索引查询不会卡住；legacy 工具 ID/名称不一致会失败；sentinel 也不能通过伪造 `AGENT_ROLE_RECOVERY` 获得动作权限；recovery 只会更新 selector 指定的 run。

当前版本已经具备任务一至三的增强实现，完成任务四的真实 inode 关联文件元数据服务、索引查询和根目录自动扫描，完成任务五的有界事件队列、等待/唤醒/取消机制、Agent 感知调度、受权调度配置、调度原因记录、当前 span 短记录、统一 timeline、timeline 过滤查询、timeline 游标增量读取、全局审计短记录和过滤查询，并提供任务六综合示例。多级目录递归扫描、云端访问和页面大屏属于用户态或宿主机工具的扩展范围，不写入当前内核职责。
