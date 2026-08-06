# 场景运行脚本

本文档用于按固定顺序复现 AgentOS-uCore 的主要能力。推荐顺序是：先说明系统目标，再运行正确性测试，再运行性能测试，最后运行多 Agent 综合场景。

## 1. 开场说明

本项目在 uCore 内核上实现 Agent-OS，把 Agent 进程身份、结构化工具调用、上下文历史、文件元数据索引和 Agent 事件运行机制放入内核支持层。

完整专项脚本运行 `ci/kernel-budgets.json` 登记的 Agent case 清单。当前配置为 `provisional_requires_full_suite`，不能复用旧 fingerprint、基线或上限；最终提交完成三轮重校准后才恢复本地时长门。`workflow_teardown_race_ucore` 及 physical、metadata/observation recovery、VirtIO 故障 runner 单独记账；预算 checker、通用 runner 和生产 profile validator 的自测集合以当前源码为准：

```bash
agentfinal_ucore
agentfs_ucore
agentscan_ucore
agentloop_ucore
agentsched_ucore
agentconflict_ucore
agentllm_ucore
agentbench_ucore
labbench_ucore
labdemo_ucore
agentsecurity_ucore
agenttoolabi_ucore
agentscope_ucore
agenttrust_ucore
agentvfs_ucore
iobudget_ucore
usersafety_ucore
blocking_semantics_ucore
```

各程序分工：

| 程序 | 作用 |
| --- | --- |
| `agentfinal_ucore` | 覆盖任务一至三核心功能，同时检查文件索引和事件自唤醒 |
| `agentfs_ucore` | 检查真实 inode 绑定、私有 `.agentmeta` 重载、字段驱动 action、依赖按需解析、metadata 工作预算和稳定 handoff 端点 |
| `agentscan_ucore` | 检查任务四的根目录自动扫描、真实文件元数据建立和索引维护 |
| `agentloop_ucore` | 检查任务五的 FIFO 事件队列、stable source 上限、SYSTEM TIMER 共存、unwatch、有限 timeout 睡眠、wait cancel，以及 heartbeat 的内生唤醒、调频、合并、停止、边界和旧 ABI |
| `agentsched_ucore` | 检查任务五的 Agent 感知调度、受权配置、事件状态、调度原因和公平性计数 |
| `agentconflict_ucore` | 检查真实文件编辑租约、版本提交和非持有者写入拒绝 |
| `agentllm_ucore` | 检查结构化 LLM 请求、Relay 响应、完成事件和 timeline 记录 |
| `agentbench_ucore` | 给出批量调用、Context 直接读、snapshot、文件索引候选记录数的性能证据，并验证 timeout/heartbeat、busy polling 与 wait/wake 计时 |
| `labbench_ucore` | 综合场景中的性能入口，当前包装运行 `agentbench_ucore` |
| `labdemo_ucore` | 呈现一个由 orchestrator 控制的多 Agent 实验恢复场景 |
| `agentsecurity_ucore` | 呈现普通进程和低权限 Agent 无法越权，并验证普通 mail 与多 run 精确恢复 |
| `agentscope_ucore` | 检查动态 workflow scope、跨域对象/IPC 隔离、事务竞争、微写合并、观测双索引与预算化查询、跨域进展、配额、fd 委派，以及可信关闭权、根退出强制撤销、阻塞成员清理和生命周期回收 |
| `agenttrust_ucore` | 检查代码 RX、数据 RW+NX、可信映像不可变及 Agent 角色与可执行 inode 绑定 |
| `agentvfs_ucore` | 检查 public/workflow 文件隔离、非 Agent worker 能力衰减、跨 scope fd 撤销及 pipe 单跳委派 |
| `iobudget_ucore` | 检查稳定 PUBLIC/workflow owner、真实提交归因、请求内物理传输批量结算、线程退出 lease 回收、唯一 runnable 内核 pipe waiter 下的 scheduler 中断交付、fault 退出清理的归因/debt 结算、buffer cache floor/cap 和 CONTROL 保留预算下的有界进展；ABI v6 定向结果只作阶段性回归，当前发布结果以冻结提交原始日志为准 |
| `usersafety_ucore` | 检查用户指针范围、exec 参数、pipe/file 失败回滚和定向等待队列 |

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
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentconflict_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentllm_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsecurity_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentscope_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agenttrust_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentvfs_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=usersafety_ucore CHAPTER=agent
```

## 3. 正确性示例

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
```

这一段不需要逐行朗读全部测试输出。建议把串口输出按以下观察点讲清楚，完整原始输出放在 [正式证据索引](../../evidence/releases/INDEX.md) 中查询。

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
| `demo_inode` | 用户态示例元数据已经绑定真实根目录文件的 `dev/inum/incarnation`，inode 号复用不会继承旧状态 |
| `custom_inode` | 用户态创建的新文件也能绑定 Agent 元数据 |
| `bulk_index` | 接近 128 条记录时，索引路径检查的候选记录少于扫描路径 |
| `query_plan` | 内核说明本次索引路径按 status 选择 bucket，并检查了多少候选记录 |
| `prefetch_hints` | 内核根据历史查询和对象标签依赖给出后续 metadata 提示 |
| `handoff_target_exit` | 目标在交接检查点退出并复用槽位后，replacement 没有收到旧端点的 hint 或 mailbox |
| `.agentmeta_reload` | 再次初始化时从私有 `.agentmeta` 重新加载自定义元数据 |
| `partial_update_binding` | 启动早期的字段级更新仍绑定调用者指定的真实 inode，重载后身份不丢失 |
| `selector_consistency` | fid/path 等非空 selector 若命中不同对象则拒绝修改或删除 |
| `clear_status` | 属性清空能够生效 |
| `delete_clears_metadata` | 删除真实文件会立即清理内存查询状态，持久副本进入分域合并写回 |
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
| `overflow_dropped=1` | 当前由同一 stable source 的第 5 条消息触发拒绝，不覆盖旧事件；不再表示 16 槽已经填满 |
| `message_source_limit=4` | 同一 stable source 的 directed MESSAGE 达到 4 条上限；消费 1 条后可立即补投，证明来源计数逐槽归还。跨 directed/attributed 共用仍没有混合事件输出 |
| `ipc_class_limit=8` | 两个 stable source 各发送 4 条 directed MESSAGE，触及 IPC 类边界 |
| `external_limit=12` | 再加入 4 条 attributed 通知，使 external 数量达到 12 |
| `system_event_reserved=4` | external admission 固定为 12，因此总容量中至少 4 个名额保留给 KERNEL/SYSTEM origin |
| `heartbeat_reserve_coalesced=1` | external=12 后一条 heartbeat TIMER 可进入保留容量，跨多个周期仍只保留一条 pending |
| `external_reject_reclaim=1` | 第 13 条 external 不入队；消费全部事件后，directed 与 attributed 事件均可再次接纳 |
| `broadcast_slow_watcher_isolated=1` | 较早 watcher 的 external admission 已饱和时，后续 watcher 仍收到同一 attributed 广播 |
| `unwatch=1` | watch 可删除 |
| `timeout_sleep_no_poll=1` | 有限 timeout 等待进入睡眠，不通过循环消耗 CPU |
| `heartbeat_intrinsic=1 dynamic=1 coalesced=1 stop=1 bounds=1 legacy=1` | 无 TIMER watch 仍唤醒；调频立即生效；最多一条 pending；drain 后 stop 无新事件；边界严格；512 ABI 兼容 |
| `wait_cancel=1` | 具备独立取消能力的 controller 能取消自己直接创建的 Agent，目标返回取消事件 |

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
| `normal_progress` | 选中资源域内连续 Agent dispatch 达到不可配置的 burst 上限后必须运行本域普通任务；外层 active-domain FIFO 另保证跨域有界进展 |
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
| `file_query_execution` | 呈现重复文件属性查询仍实际执行索引候选遍历，并明确 `kernel_cache_hit=0` |
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

1. 内核首次加载清单中带 bootstrap 标志且允许 orchestrator 的可信 init；启动 grant 只在这次初始装载建立。
2. orchestrator 初始化文件元数据并创建三个业务 Agent。
3. 三个 Agent 报告 ready 后，orchestrator 显式 grant sentinel -> investigator 和 investigator -> recovery 的 `MESSAGE` 路由，再开始故障流程。
4. sentinel 监听 `status=failed`。
5. orchestrator 注入 align 阶段失败。
6. sentinel 收到事件并查询文件索引。
7. sentinel 读取内核给出的 metadata 预取提示。
8. sentinel 尝试恢复但权限不足，被内核拒绝。
9. sentinel 发送普通 investigate 消息，内核在 message 入队时把 sentinel 的预取提示交接给 investigator。
10. investigator 查询 align 摘要和依赖，确认影响范围。
11. investigator 从自己的预取提示 snapshot 中读取带 `HANDOFF` 原因位的 analyze 提示，并从当前 span 的全局提示总线确认 source/target pid。
12. investigator 查询当前 span 的系统级短记录，确认 Context、事件和预取交接摘要已经进入内核记录。
13. investigator 输出模板 LLM 解释事件和恢复计划事件，预留 LLM Gateway 和 Planner/Auditor 拆分入口。
14. investigator 输出 Context Snapshot，呈现决策过程中的可审计记录。
15. investigator 沿已授权路由唤醒 recovery。
16. recovery 通过权限检查并执行恢复。
17. recovery 重复执行同一恢复动作，内核识别为 duplicate。
18. recovery 写报告状态并输出带 corr_id 的 report 事件。
19. 最终查询报告文件，系统输出 recovered。
20. orchestrator 查询全局审计短记录，确认三个业务 Agent 的 Context、事件、调度和预取交接摘要都可见。
21. orchestrator 按 kind、span、文件状态事件、预取 source/target 和起始 sequence 过滤查询全局短记录。

### 9.3 关键观察点

综合场景的原始事件日志较长，完整样例放在 [正式证据索引](../../evidence/releases/INDEX.md)。现场讲解时建议按事件链路观察，而不是逐行读串口：

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
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentscope_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agenttrust_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentvfs_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=usersafety_ucore CHAPTER=agent
```

`agentsecurity_ucore` 覆盖普通进程 mail 最小路径；普通进程不能直接投递事件、取消 Agent 等待或修改 Agent 文件元数据；usershell 等价的普通 `fork/exec` 路径不能创建任何 Agent；低权限 Agent 不能继续委派。普通 exec 会撤销 bootstrap grant，之后执行同名或清单中的 bootstrap 映像也不会恢复启动授权。初始化前索引查询不会卡住；legacy 工具 ID/名称不一致会失败；sentinel 不能通过伪造 `AGENT_ROLE_RECOVERY` 获得动作权限；recovery 只会更新 selector 指定的 run。

`agentscope_ucore` 同时建立多个由可信 factory 签发的 workflow scope，验证 capability 只有在 active scope 和精确 owner 同时命中时才生效；同名文件、metadata、action、audit、lease 和 IPC 不能跨域。配额阶段让 scope A/B 各创建 97 个普通文件：前 96 个进入 AUTOSCAN 物化视图，第 97 个仍由独立 STORAGE policy 接纳但保持未索引；显式 syscall 携带 AUTOSCAN 标志也不能越过 96 条边界，每域随后还能建立 16 个非 AUTOSCAN 显式 metadata，第 17 个显式请求才返回 `NO_SPACE`。catalog 满后新增普通 VFS 文件仍可创建且不能被 peer scope 打开，PUBLIC 主体另行创建并删除 70 个对象，清理后存储与 catalog 槽均可复用。ACTIVE/CLOSING/RETIRING 最多合计 4 个 scope，RETIRING 在 catalog 回收完成前保持原准入槽。每 scope STORAGE inode 硬下限为 320，当前镜像保证约 342；catalog 每 scope 112 由 AUTOSCAN 96 与显式保留 16 组成，不再充当 inode 上限。随后低权限 Artifact 才在 guest pipe 存活屏障后持续微写一个已绑定 `PERSIST` 对象，另一 scope 必须在 5 秒内完成 32 次查询；测试同时检查写回批次数、dirty/durable 最终一致和强制重载后的 size/generation。另一个 Artifact 对 volatile 文件执行 32 次微写，request/commit 计数不得增长且 reload 不能清除内存态对象。事务门、存储/进程保留量、单跳 pipe fd 委派及 scope retirement 均有实际 QEMU 回归入口；当前候选是否通过仍以 release bundle 为准。

同一程序还验证 workflow 的可信终止协议。低权限 Sentinel 和后创建的 Orchestrator 调用 `agent_workflow_close()` 必须返回 `AGENT_STATUS_DENIED`，带高位别名的 64 位 scope id 必须返回 `AGENT_STATUS_BAD_PARAM`；只有创建时绑定的根 controller 或仍运行可信 bootstrap 的 factory 可以发起关闭。显式关闭和根自然退出都会先把 scope 置为 CLOSING、撤销授权，再让一个阻塞在 `agent_wait()` 且持有 pipe 的低权限成员沿正常退出路径释放端点。测试在 pipe EOF 前设置返回后 poison 写入，避免把“等待意外返回”误判为成功清理；自动根退出重复 9 轮，超过 `VFS_SCOPE_LIFECYCLE_CAP=8` 后仍能接纳 replacement workflow。

关键输出为：

```text
agentscope_ucore: pipe_redelegation_isolation=1
agentscope_ucore: scope_storage_isolation=1 catalog_limit=112 autoscan_limit=96 explicit_reserve=16 workflow_created=97 peer_created=97 public_created=70 overflow_unindexed=1 autoscan_flag_no_space=1 explicit_no_space=1 reusable=1
agentscope_ucore: metadata_volatile_reload_isolation=1 writes=32
agentscope_ucore: scope_close_authority=1
agentscope_ucore: scope_controller_exit_revoke=1
agentscope_ucore: scope_forced_cleanup=1
agentscope_ucore: scope_replacement_admitted=1
agentscope_ucore: parent passed
```

`agenttrust_ucore` 检查构建期清单写入 inode 的可信策略：程序代码页为 RX，数据页为 RW+NX，可信映像拒绝写入、截断和删除；只有允许当前 Agent 角色的可信 inode 可以 exec，复制相同程序字节得到的普通文件不会继承信任。

`agentvfs_ucore` 检查普通文件路径不能绕过 Agent capability：public 进程无法读取、修改或删除 workflow 工件，workflow Agent 也不能把 public 文件冒充受保护工件；orchestrator 可通过 syscall 539 `agent_worker_create()` 创建非 Agent worker，但请求能力同时受父凭据和目标映像 profile 限制。错误 exec 不安装委派，降权普通 fork 撤销跨 scope inode fd，worker pipe 只通过创建线程的一次性票据进入子主体。

`usersafety_ucore` 检查坏指针、跨页和整数溢出范围不会破坏内核状态，失败的 wait copyout 不会提前回收子进程，pipe/file 分配失败会回滚，并且不相关的子进程退出不会错误唤醒 mutex 等待者。

当前版本已经具备任务一至三的增强实现，完成任务四基于 `dev + inum + incarnation` 的真实文件元数据服务、public/workflow VFS 隔离、索引查询和根目录自动扫描，完成任务五的有界事件队列、等待/唤醒/取消机制、Agent 感知调度、普通进程强制公平上限、受权调度配置、调度原因记录、当前 span 短记录、统一 timeline、timeline 过滤查询、timeline 游标增量读取、全局审计短记录和过滤查询。观测查询使用 scope-local 双有序索引、单遍扫描或四路归并，并按候选数预付内核工作预算；计数查询不再成为无预算旁路。系统同时提供任务六综合示例。多级目录递归扫描、云端访问和页面大屏属于用户态或宿主机工具的扩展范围，不写入当前内核职责。
