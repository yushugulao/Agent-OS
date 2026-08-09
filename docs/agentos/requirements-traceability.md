# 赛题要求追踪

本表把赛题任务映射到当前生产实现、主要验证和诚实边界。源码位置是评审导航，不替代冻结提交与动态证据。

## 1. 总表

| 任务 | 赛题能力 | 当前实现 | 主要源码 | 验证入口 | 边界 |
| --- | --- | --- | --- | --- | --- |
| 1 | Agent 进程与专用地址空间 | 可信映像/role/capability、7 页 Context 区、workflow id+generation、member+closing+gates | `os/agent_core.c`、`os/proc.c`、`os/workflow_lifecycle.c`、`os/vfs_security.c` | Agent create/scope/security Guest tests；fence syscall-cut test | 单 Hart；workflow/slot 有界；无 crash workflow recovery |
| 2 | 面向 Agent 的系统调用/工具 | name/id 工具目录、typed KV、参数/能力验证、`agent_run()` batch | `os/agent_core.c`、`os/agent_metadata_actions.c`、`agent_tool_abi.h` | tool/agent final/bench Guest tests；UAPI check | 内核不运行 LLM；fence 是 count=0 的独立控制路径 |
| 3 | Context Path | 内核可信 Context、只读 mirror、query/snapshot/detail/rollback、cause/span/branch | Context 模块、`os/agent_observe.c` | Context/rollback/security Guest tests；Evidence Ring model | user cache 不可信；历史有界 |
| 4 | 文件属性查询 | 显式 volatile metadata、status/stage/kind 索引、inode incarnation、typed live query/resync | metadata catalog/query/object、`os/agent_live_query_events.c` | live-query checker/model；file-query Guest benchmark | 不 autoscan；不持久化 catalog；重启需重新登记 |
| 5 | Agent Loop | 有界 event queue、watch/wait、heartbeat、route、调度、Fence-Sealed Evidence Ring | `os/agent_ipc.c`、observe/timeline、evidence ring、scheduler | evidence/fence/live-query model；loop/sched/IPC Guest tests | seal 是内存 partial coverage，不是 disk durable |
| 6 | 综合 Agent 应用 | plain/AgentOS 运行同一科研工作流合同，Host 提取状态和 paired measurement | `user/src/rp_*`、`baseline_ucore/`、`host_tools/` | seeded action state、dual target、formal verification | 单项机制归因需消融；发布数字只来自 bundle |

## 2. 任务一细化

| 要求 | 实现/断言 |
| --- | --- |
| 区分普通进程与 Agent | `is_agent` 只由受控 create/exec 发布，普通 exec 不取得身份 |
| Agent 角色和权限 | role 映射 capability；父权限、映像 profile 和 scope 继续衰减 |
| 专用 Context | 固定 ABI 地址，6 页可信区 + 1 页 user cache |
| fork/exec/exit | operation/departure gate 包围身份和资源转移；失败撤销 |
| workflow 生命周期 | immutable `id+generation`、members、closing、controller、fence gate |
| 资源隔离 | exec/storage account；U/P/F hard admission；fence exact U |

不再把“多阶段 retirement”作为加分能力。当前更小的状态机以最后 member 和 gate quiescence 决定回收。

## 3. 任务二细化

| 要求 | 实现/断言 |
| --- | --- |
| 结构化调用 | v1 兼容请求和 v2 typed KV；版本、size、type、required key 检查 |
| 工具发现 | 内核 tool list，name/id 映射唯一 |
| 批处理 | `agent_run()` 对数组逐项执行并返回稳定 status |
| 权限 | 每个工具检查 role/capability/scope，不信任用户传入 actor 字段 |
| fence | `agent_run(count=0, AGENT_RUN_F_FENCE)`，controller-only |

## 4. 任务三细化

| 要求 | 实现/断言 |
| --- | --- |
| 记录调用轨迹 | sequence、request、tool/status、payload/result、tick |
| 因果关系 | cause/span/branch/control id 由内核归因；跨 Agent 继承受 route/scope 限制 |
| 查询/快照 | syscall 和只读 mirror；publish sequence 防止 torn read |
| rollback | 只改变 Context active path，不倒转已发生的外部文件/IPC 事实 |
| workflow evidence | Context 规范事件进入 Evidence Ring，fence 才生成 challenge root |

## 5. 任务四细化

| 要求 | 当前证据 |
| --- | --- |
| 文件属性 | 显式 `agent_file_meta_set()`，绑定真实 incarnation |
| 属性查询 | scan/index 计划、候选/扫描工作量、最多 8 hits |
| 实时变化 | typed query 的 `ENTER/UPDATE/LEAVE` 事件进入 Agent Queue |
| 有界丢失处理 | generation `RESYNC_REQUIRED` + snapshot/query + ACK |
| 安全隔离 | scope/lifecycle/control id/incarnation 全部重验 |

明确不实现：普通目录 autoscan、persistent metadata flag、catalog journal/bank/recovery。`PERSIST/AUTOSCAN` 保留为 legacy 常量并返回 `BAD_PARAM`。

## 6. 任务五细化

| 要求 | 当前证据 |
| --- | --- |
| watch/wait | legacy watch + typed live watch，thread-generation wait |
| event queue | 总容量、kernel reserve、IPC/source limit，有界失败 |
| heartbeat | timer 事件，set/stop，不在内核运行 Agent 业务 |
| Agent IPC | 同 active workflow 的定向 route 和 capability |
| 感知调度 | weight/priority/event/deadline/heartbeat/budget/vruntime |
| 观测 | ordinary Context 单次 canonical ring；critical 兼容 ledger 投影 |
| 可验证 cut | challenge-bound evidence root + exact credit digest + volatile metadata generation |

audit/timeline/provenance/ledger 兼容 API 仍支持；observe crash recovery 不支持，syscall 固定 `BAD_PARAM`。

## 7. 创新增量与外部思想

| 项目机制 | 公开概念来源 | 本项目特定增量 |
| --- | --- | --- |
| Workflow Credit Domain | Linux cpuacct/percpu_counter/rstat 的批量本地计数 | workflow exec/storage 双账户、U/P/F hard admission、context switch trim、fence exact U/digest |
| Fence-Sealed Evidence Ring | Linux BPF ring buffer 的 ordered reserve/commit/discard | workflow generation、ordinary/critical 分区、Agent 因果事件、gap、challenge fence、compat projection |
| Agent Live-Query FS | Haiku BFS 属性、选择性索引和 live query | explicit volatile Agent metadata、Context event、scope generation、typed predicate、resync ACK、fence drain |

以上为 clean-room、概念级参考。没有复制/vendoring Linux、Haiku 或 AIOS 源码、数据、二进制或磁盘格式，详见 [../../NOTICE](../../NOTICE)。

## 8. 验证矩阵

| 合同 | Host/static | cross build | QEMU/paired |
| --- | --- | --- | --- |
| UAPI layout | `check-agent-uapi-layout.py` | kernel/user 同头编译 | syscall 行为 |
| Credit U/P/F | `test-workflow-credit-domain.py` | budget/module check | quota/teardown Guest |
| Evidence Ring | `test-agent-evidence-ring.py` | kernel link/stack | Context/audit/fence Guest |
| Live Query | checker + `test-agent-live-query-fs.py` | metadata/IPC modules | query/watch/event Guest |
| Workflow fence | checker + mutation + syscall-cut | ABI/link | controller/retry/receipt Guest |
| 综合任务 | Host contract selftests | 双目标 build | seeded/dual paired run |

## 9. 发布证据边界

- checker 通过不等于动态功能发布；
- Guest `passed` 行不等于 Host 可复验 receipt；
- Dashboard 不等于原始证据；
- fence receipt 不等于磁盘持久证据；
- paired end-to-end 差异不自动证明某个内核机制贡献；
- 只有 `evidence/releases/INDEX.md` 指向、manifest/checksum/semantic replay 都成立的 bundle 才是正式发布结果。
