# 赛题要求追踪

本表把赛题任务映射到当前生产实现、主要验证和诚实边界。源码位置是评审导航，不替代冻结提交与动态证据。

## 1. 总表

| 任务 | 赛题能力 | 当前实现 | 主要源码 | 验证入口 | 边界 |
| --- | --- | --- | --- | --- | --- |
| 1 | Agent 进程与专用地址空间 | 可信映像/role/capability、7 页 Context 区、workflow id+generation、member+closing+gates | `os/agent_core.c`、`os/proc.c`、`os/workflow_lifecycle.c`、`os/vfs_security.c` | Agent create/scope/security Guest tests；fence syscall-cut test | 单 Hart；workflow/slot 有界；无 crash workflow recovery |
| 2 | 面向 Agent 的系统调用/工具 | name/id 工具目录、typed KV、24-node immutable execution contract、Phase Lease、V2/V3/batch、16-slot Task SQ/CQ | `os/agent_core.c`、`os/agent_execution_contract.c`、`os/agent_task_channel.c`、`os/resource_controller.c` | execution-contract/task-channel checker；tool/contract Guest tests；UAPI check | 内核不运行 LLM；合同最多 24 节点/48 attempts；Task Channel 按需 4 页，当前 provider 同步且无 payload backend |
| 3 | Context Path | 内核可信 Context、只读 mirror、cause/span/branch、六标签 provenance、critical denial evidence | Context/observe/provenance/evidence 模块 | Context/rollback/security Guest tests；execution-contract/Evidence Ring model | user cache 不可信；标签保守传播且不判断文本安全；历史有界 |
| 4 | 文件属性查询 | 显式 volatile metadata、status/stage/kind 索引、inode incarnation、typed live query/resync | metadata catalog/query/object、`os/agent_live_query_events.c` | live-query checker/model；file-query Guest benchmark | 不 autoscan；不持久化 catalog；重启需重新登记 |
| 5 | Agent Loop | 有界 event queue、watch/wait、heartbeat、route、workflow EEVDF、可表达 `PENDING` 的 Task completion core、Fence-Sealed Evidence Ring | `os/agent_ipc.c`、`os/workflow_scheduler.c`、Task/observe/evidence 模块 | scheduler model；evidence/fence/live-query model；loop/sched/IPC Guest tests | EEVDF 总 cap4=bootstrap+最多 3 fresh；16 为四波逻辑样本；当前 Task provider 同步；异常回退 legacy scheduler |
| 6 | 综合 Agent 应用 | plain/AgentOS 同科研合同；MCP 2026-07-28/A2A v1 的 deterministic 用户态映射 | `user/src/rp_*`、`baseline_ucore/`、`host_tools/mcp_a2a_gateway.py`、`host_tools/agent_task_transport.py` | seeded/dual paired；gateway/in-memory transport tests；formal verification | gateway 尚无内核 SQ/CQ binary adapter；JSON/HTTP/OAuth/JWS 在用户态；单项归因需消融；数字只来自 bundle |

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
| 执行合同 | 每 lifecycle generation 一份 24-node frozen DAG；tool/schema/dependency/artifact/deadline/retry/cancel/envelope 确定验证 |
| 合同调用 | V3 保留 V2 prefix，绑定 contract/node/attempt、32-byte fingerprint、source Context 与 evidence ticket |
| 完成缓存 | 每个 accepted node/attempt 使用一个稳定槽；每份合同合计最多 48 槽；同 attempt 合法重试返回原终态 |
| 工具资源阶段 | exec/storage envelope 从现有 U 原子锁定；claim publish/refund 与 terminal settle 不超卖 U/P/F |
| Task core | single-issuer、16-slot SQ/CQ、2 mapped + 2 private page、copy-before-validate、sticky resync；callback 协议可返回 `PENDING` |
| 当前 provider | 内建 provider 同步完成，只接受 null input/output artifact `NONE`；没有动态 provider registration UAPI |
| 类型化资源 | slot/type/owned-borrowed/generation handle 与 8 槽私有表；`RESOURCE_IMPORT` 固定 `DENIED`，当前无 payload import/result resource backend |
| 兼容 | scalar V2 与 `agent_run()` batch 保留；Task Channel 按需建立 |

## 4. 任务三细化

| 要求 | 实现/断言 |
| --- | --- |
| 记录调用轨迹 | sequence、request、tool/status、payload/result、tick |
| 因果关系 | cause/span/branch/control id 由内核归因；跨 Agent 继承受 route/scope 限制 |
| 查询/快照 | syscall 和只读 mirror；publish sequence 防止 torn read |
| rollback | 只改变 Context active path，不倒转已发生的外部文件/IPC 事实 |
| workflow evidence | Context 规范事件进入 Evidence Ring，fence 才生成 challenge root |
| provenance vocabulary | 固定六标签：kernel fact、trusted control、Agent-derived、untrusted file/tool、cross-Agent |
| 数据流传播 | 文件查询/工具结果/IPC 保守 OR 标签；Task core 对任何 live resource 保留同一规则，但当前 bridge 不能 import/create resource；rollback/clear 恢复对应 Context 状态 |
| effect gate | lifecycle + frozen edge/schema + capability + manifest labels/effects 同时通过才允许外部副作用 |
| critical denial | 计划外/不可信来源调用在副作用前 `DENIED`，记录 source/tool/reason/lifecycle/ticket |

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
| 感知调度 | workflow EEVDF 以 lag eligibility + latency/event virtual deadline 选择，按 workflow service cycles 记账 |
| 线程放大防护 | 同 workflow 多线程聚合为一个公平实体；睡眠 lag decay；单实体 fast path；测量以 1 个 fresh 4-thread workflow 对 2 个 fresh single-thread workflow 加 bootstrap peer |
| 调度回退/容量 | 总计最多 4 个 active workflow：1 个 `BOOT_SEALED` bootstrap participant 加最多 3 个 fresh workflow；异常回退旧 RR/Agent heuristic 并计数 |
| 1/4/16 评价 | 1 为单实体 fast path；4 为 bootstrap+3 fresh 的满容量 fairness；16 为四波复用同一 bootstrap 加 12 个 fresh lifecycle 的逻辑样本，不是 16-way 或每波 4 fresh；唤醒直方图只含 fresh-agent 样本 |
| Task completion | core 可保留 `PENDING`，target exactly-one terminal CQE，Context sequence + Evidence ticket 可关联；当前 provider 同步，cancel 不另发 CQE |
| hard deadline | timer 只标记到期，在首个 schedulable safe point 终止；不承诺 wall-clock 上界 |
| 观测 | ordinary Context 单次 canonical ring；critical 兼容 ledger 投影 |
| 可验证 cut | challenge-bound evidence root + exact credit digest + volatile metadata generation |

audit/timeline/provenance/ledger 兼容 API 仍支持；observe crash recovery 不支持，syscall 固定 `BAD_PARAM`。

## 7. 创新增量与外部思想

| 项目机制 | 公开概念来源 | 本项目特定增量 |
| --- | --- | --- |
| Workflow Credit Domain | Linux cpuacct/percpu_counter/rstat 的批量本地计数 | workflow exec/storage 双账户、U/P/F hard admission、context switch trim、fence exact U/digest |
| Fence-Sealed Evidence Ring | Linux BPF ring buffer 的 ordered reserve/commit/discard | workflow generation、ordinary/critical 分区、Agent 因果事件、gap、challenge fence、compat projection |
| Agent Live-Query FS | Haiku BFS 属性、选择性索引和 live query | explicit volatile Agent metadata、Context event、scope generation、typed predicate、resync ACK、fence drain |
| Execution Contract/Phase Lease | AgentCgroup tool-call 峰值测量；Murakkab 声明式 workflow/SLO 分离 | 24-node immutable kernel contract、deterministic effect gate、existing-U phase lock、nonce claim、terminal settle |
| Workflow EEVDF | Linux EEVDF lag/virtual deadline/sleep decay | workflow 而非线程的公平实体、Agent latency/deadline 输入、bootstrap+最多 3 fresh 的总 cap4、安全 fallback 和 V3 metrics |
| Context Provenance | CaMeL control/data separation；IPIGuard planned TDG | 六个固定来源标签、manifest effect mask、full-generation gate、critical pre-effect denial evidence |
| Typed Task Channel | io_uring SQ/CQ；WIT/WASI 0.3 ownership/future/stream | 16-slot/4-page single issuer、copy-before-validate、sticky resync、one terminal CQE、8-slot generation handle core；当前同步 null/NONE provider |
| MCP/A2A gateway | MCP 2026-07-28 Tasks；A2A v1 Task/Context/Artifact/stream/cancel | remote task identity 到 lifecycle/contract/channel/request 的 transport-neutral 用户态绑定；当前仅 deterministic in-memory adapter |

以上为 clean-room、概念级参考。没有复制/vendoring Linux、Haiku、相关论文原型或协议 SDK 的源码、数据、二进制或磁盘格式；Task Channel 不实现完整 io_uring/Wasm ABI，gateway 也不把远程协议栈放入内核，详见 [task6-execution-contract.md](task6-execution-contract.md) 与 [../../NOTICE](../../NOTICE)。

## 8. 验证矩阵

| 合同 | Host/static | cross build | QEMU/paired |
| --- | --- | --- | --- |
| UAPI layout | `check-agent-uapi-layout.py` | kernel/user 同头编译 | syscall 行为 |
| Credit U/P/F | `test-workflow-credit-domain.py` | budget/module check | quota/teardown Guest |
| Evidence Ring | `test-agent-evidence-ring.py` | kernel link/stack | Context/audit/fence Guest |
| Live Query | checker + `test-agent-live-query-fs.py` | metadata/IPC modules | query/watch/event Guest |
| Workflow fence | checker + mutation + syscall-cut | ABI/link | controller/retry/receipt Guest |
| Execution Contract/Phase | `test-agent-execution-contract.py` | V3 ABI/link/stack | normal DAG、deadline/retry/cancel、prompt-injection denial Guest |
| Workflow EEVDF | `test_workflow_scheduler_model.py` | scheduler module/link | 抽象 model 验证算法；Guest 1-way fast path、bootstrap+3 fresh 的 4-way、公平/延迟、4 波共 16 个逻辑样本；直方图仅 fresh-agent |
| Task Channel | `test-agent-task-channel.py` | SQ/CQ ABI/map/reclaim | batch/scalar V3/SQ-CQ 以不同线格式各执行 16 次空 `ECHO` 并验证同一语义 fingerprint；scalar 固定三个 typed params；调用点 syscall 1/16/2 与 ABI/复制记账 3584/12288/4096、Context pre-effect service-start tick 间隔、`agent_info` 边界 elapsed、retained-terminal cancel；full/resync 功能恢复 |
| MCP/A2A gateway | transport + gateway unit tests | 用户态 syntax/import | protocol fixture/in-memory Task lifecycle replay；不覆盖内核 SQ/CQ adapter |
| 综合任务 | Host contract selftests | 双目标 build | seeded/dual paired run |

## 9. 发布证据边界

- checker 通过不等于动态功能发布；
- Guest `passed` 行不等于 Host 可复验 receipt；
- Dashboard 不等于原始证据；
- fence receipt 不等于磁盘持久证据；
- paired end-to-end 差异不自动证明某个内核机制贡献；
- 16-workflow 数据是四波复用同一 bootstrap 加 12 个 fresh lifecycle 的 16 个逻辑样本，不等于 16 个并发或独立 EEVDF lifecycle，也不得写成每波 4 个 fresh；
- one terminal CQE 不等于远程工具副作用的分布式 exactly-once；
- Task core 能表达 `PENDING` 不等于当前 provider 异步；当前仅同步 null-input/output-`NONE` provider，且无动态 registration UAPI；
- typed handle/8 槽表不等于 payload backend；当前 `RESOURCE_IMPORT` fail closed，也不发布 result resource；
- hard deadline 在首个 schedulable safe point 终止，不提供 wall-clock 延迟上界；
- Task Guest 的三条路径各固定 16 次空 `ECHO`，但线格式不同：scalar V3 显式携带 `payload=""`、`arg0=0`、`arg1=0` 三个 typed params；调用点 syscall 为 1/16/2，描述符 ABI/已知复制记账为 3584/12288/4096 字节，另列 scalar dispatch header 与 SQ/CQ control 字节；这些都不是内核路径 counter、实测总复制量或全部内存流量；
- p50/p99 来自工具效果前写入 Context record 的 service-start tick 间隔，sequence elapsed 是两个 `agent_info` 边界 tick；它们不表示 CPU service、raw cycles 或 wall clock，公共 CQE `completion_tick` 保持执行后完成语义；
- cancel latency 只覆盖 retained-terminal 幂等 cancel，不代表当前同步 provider 支持真正 running/pending cancel；CQ-full/sticky-resync 只作功能恢复验证；
- provenance denial 证明结构边界生效，不证明模型没有受到 prompt injection；
- gateway/transport unit test 只覆盖 deterministic 用户态 in-memory 映射，不等于已有内核 binary adapter、JSON/HTTP/OAuth/JWS 或完整远程互操作；
- 只有 `evidence/releases/INDEX.md` 指向、manifest/checksum/semantic replay 都成立的 bundle 才是正式发布结果。
