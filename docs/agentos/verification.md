# 验证与性能评估

本文档说明 AgentOS-uCore 专项测试如何运行、每个测试覆盖哪些能力、性能数据如何解读。逐项流程见 [testing-details.md](testing-details.md)，原始输出样例见 [test-record.md](test-record.md)。

## 验证组织方式

AgentOS-uCore 的验证分四层：

| 层次 | 入口 | 作用 |
| --- | --- | --- |
| 构建检查 | `make agentos-user`、`make agentos-build`、`make kernel-stack-check` | 确认内核、用户态 ABI 和文件系统镜像能从当前源码构建；每次生成 `build/kernel` 前都会自动执行内核栈预算检查。 |
| AgentOS 专项测试 | `make agentos-test` 或 `bash scripts/run-agent-tests.sh` | 在 QEMU 中逐项运行 Agent 功能、权限和用户输入检查。 |
| 资源安全复测 | `make fs-enospc-test`、`make proc-reap-test`、`make syscall-fairness-test` | 在增强目标和普通 uCore 对照目标上验证文件系统耗尽、持久 PUBLIC 配额跨域退出/重启、进程回收、活进程配额及 syscall 内核工作预算。 |
| 双目标与聚合验证 | `make dual-platform-run`、`make full-verify` | 运行双目标科研平台负载，并串联宿主机、AgentOS 和进程回收检查。 |

`agentos-test` 只关注根目录 AgentOS-uCore 目标；`fs-enospc-test`、`proc-reap-test` 和 `syscall-fairness-test` 同时覆盖根目录增强目标与 `baseline_ucore/` 普通目标。双目标验证详情见 [../verification.md](../verification.md)。

## 验证环境

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Linux / WSL2 Ubuntu |
| 工具链 | `riscv64-linux-gnu-gcc`、`riscv64-linux-gnu-ld`、`riscv64-linux-gnu-objdump` |
| 虚拟机 | `qemu-system-riscv64` |
| 构建工具 | `make`、`bash`、`python3` |
| 默认模型路径 | 专项测试默认使用模板 LLM Relay，不访问云端模型 |

依赖检查入口：

```bash
make doctor
```

Windows 侧检查入口：

```powershell
.\scripts\check-windows-prereqs.ps1
```

## 构建命令

```bash
make agentos-user TOOLPREFIX=riscv64-linux-gnu-
make agentos-build TOOLPREFIX=riscv64-linux-gnu-
```

只需要构建用户态测试程序时：

```bash
make agentos-user TOOLPREFIX=riscv64-linux-gnu-
```

## 专项测试入口

推荐直接运行：

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

等价脚本入口：

```bash
bash scripts/run-agent-tests.sh
```

脚本会按顺序启动 QEMU，并运行以下测试程序：

下表同时区分既有运行证据和本次新增回归契约。2026-07-21 的输出支持既有 Agent/route/queue 机制；可信 workflow scope、私有 cause/span owner 和审计 authority 分区是其后新增，必须以本轮 QEMU 日志为准，不能从程序已编译或标记字符串存在推断通过。

| 测试程序 | 覆盖重点 | 通过标记 |
| --- | --- | --- |
| `agentfinal_ucore` | Agent 创建、Context 映射、批量工具调用、Context Path、snapshot、rollback、用户 cache、timeline、provenance、Run Ledger。 | `agentfinal_ucore: parent passed` |
| `agentfs_ucore` | 真实 inode 绑定、metadata 双 bank、属性查询、索引查询、查询缓存、内容摘要、预取提示、文件删除清理。 | `agentfs_ucore: parent passed` |
| `agentscan_ucore` | 根目录自动扫描、自动 metadata 写入、文件创建和删除后的 metadata 更新。 | `agentscan_ucore: parent passed` |
| `agentloop_ucore` | FIFO、stable source=4、directed=8、external=12、KERNEL origin 预留容量、消费后配额归还、慢 watcher 广播隔离、watch/unwatch、timeout、heartbeat、wait cancel、事件因果。 | `message_source_limit=4`、`ipc_class_limit=8`、`external_limit=12`、`system_event_reserved=4`、`external_reject_reclaim=1`、`broadcast_slow_watcher_isolated=1`、`parent passed` |
| `agentsched_ucore` | 角色权重、受权调度配置、事件优先、调度原因记录、公平性观测。 | `agentsched_ucore: parent passed` |
| `agentconflict_ucore` | 文件编辑租约、非持有者写入拒绝、版本提交检查、普通进程拒绝。 | `agentconflict_ucore: parent passed` |
| `agentllm_ucore` | 显式 `MESSAGE` / `LLM_DONE` route 下的结构化请求、Relay 模板响应、LLM capability、完成事件、Context/timeline。 | `agentllm_ucore: parent passed` |
| `agentbench_ucore` | 批量工具、Context、文件查询、预取、timeout/heartbeat，以及显式 route 下的 wait/wake 计时。 | `agentbench_ucore: parent passed` |
| `labbench_ucore` | 综合场景中的性能入口，包装运行 `agentbench_ucore`。 | `labbench_ucore: parent passed` |
| `labdemo_ucore` | orchestrator 建立 sentinel -> investigator -> recovery 路由后的恢复场景、文件查询、预取交接、消息、权限、audit/timeline/provenance。 | `labdemo_ucore: parent passed` |
| `agentsecurity_ucore` | 既有权限/route/controller 负向检查；新增用户非零 cause/span 拒绝、可信跨 Agent source attribution、low/high audit authority 隔离。 | `trusted_span_authority=1`、`trusted_cause_attribution=1`、`audit_authority_partition=1`、`parent passed`；本轮通过 |
| `agenttrust_ucore` | 可执行映像 W^X、密封映像不可变、bootstrap 授权范围、Agent 角色与可信映像绑定。 | `agenttrust_ucore: parent passed` |
| `agentvfs_ucore` | 工作流文件能力、公共/工作流命名空间隔离、继承描述符重新鉴权、精确能力委派和失败事务原子性。 | `agentvfs_ucore: parent passed` |
| `agentscope_ucore` | syscall 541 factory、542 一次性 pipe fd 委派、动态 scope、同名对象/action/lease/audit/IPC 隔离、scope-local metadata reload、并发 metadata 事务、持久微写合并、volatile 写回分流、满表 scan-pressure 限流、跨 scope 查询时限、最终持久化一致性、配额保证和 retirement 回收。 | `cross_scope_isolation=1`、`scope_reload_isolation=1`、`ipc_scope_isolation=1`、`metadata_transactions=1`、`metadata_write_coalescing=1 writes=<at-least-128> commits=<bounded>`、`metadata_cross_scope_progress=1 queries=32 latency_ms=<at-most-5000>`、`metadata_final_consistency=1`、`metadata_volatile_no_writeback=1 writes=32`、`metadata_scan_pressure_bounded=1`、`scope_capacity_reservation=1`、`transactional_fd_delegation=1`、`lifecycle_reclamation=1`、`parent passed`；完整 15/15 QEMU 回归通过 |
| `usersafety_ucore` | syscall 指针、字符串、`exec` 参数、线程入口、等待队列、管道、文件和信号量输入范围。 | `usersafety_ucore: parent passed` |

原始输出不在本文档重复展开，统一保存在 [test-record.md](test-record.md)。每个测试的流程和断言解释见 [testing-details.md](testing-details.md)。

本次 scope 回归核对的固定机制参数如下：PUBLIC=0、SYSTEM=1、动态 workflow>=3，数值 2 保留为安装级 PUBLIC 存储 principal，最多 4 个 active/retiring scope；进程普通槽 96、受控保留槽 32、每 scope 保留 8；metadata/dependency/action/edit/span-prefetch 每 scope 分别 112/16/8/8/8。审计物理 512、每 scope 128，low/high 各 64，low principal 上限 16、high active principal 上限 8；high 满时只能自滚或回收 inactive principal，active principal 互不淘汰。存储策略按完成镜像空闲量核算并把 PUBLIC principal/G/S 持久化到 superblock，挂载从 qmap/dinode 重建 PUBLIC 用量；每 scope 硬下限 320 inode/512 block，SYSTEM 硬下限 8 inode/512 block。当前平台镜像实际核算为每 scope 342/1195、SYSTEM 64/512，构建日志必须与内核使用同一版本化契约。

账本验证不得假设当前可见窗口 sequence 连续：系统 sequence 跨 scope 单调，low/high/principal 分区独立滚动。测试仅对无 gap 的相邻记录核验直接 `prev_hash`，并用 `dropped_records=total_records-visible_records` 解释窗口外记录。非活跃 principal 的旧 high 证据是可观测但有界的历史窗口。

## 资源安全与内核栈入口

文件系统耗尽复测使用极小 SFS 镜像分别触发 inode、inode cache 和数据块耗尽，要求分配失败被返回给调用者、内核继续运行且释放后资源可复用；AgentOS 配额场景还执行 640 次 PUBLIC 短命 inode 循环，验证版本 sidecar 最终回收后 workflow 编辑版本与内容摘要缓存仍可用。两个目标随后各在同一磁盘镜像上连续启动三次 `fspquota_ucore`，先在打开文件 unlink 后强制断电并验证挂载物理回收，再验证 PUBLIC 用量跨完整进程域退出与重挂载保持、删除后才退款：

```bash
make fs-enospc-test TOOLPREFIX=riscv64-linux-gnu-
```

进程回收复测覆盖子进程先退出、父进程先退出、阻塞 syscall 撤销、有父僵尸隔离、资源域配额、系统保留槽及配额归还：

```bash
make proc-reap-test TOOLPREFIX=riscv64-linux-gnu-
```

syscall 公平性复测完全在 Guest 内用 pipe、进程和线程建立因果关系，宿主机只采集输出。它依次验证单次 64 KiB 控制台 `write()` 的 peer 进展、普通 inode 大写入的内核重调度计数、合法短写与 observer 进展，以及 `O_TRUNC` open 的内核重调度计数和原子 EOF 可见性：

```bash
make syscall-fairness-test TOOLPREFIX=riscv64-linux-gnu-
```

三个阶段都要求 `BEGIN < PEER < END`；inode 与 truncate 阶段都用只读的 last-syscall 重调度计数证明前一个 syscall 内部跨过调度边界，observer 分别证明 peer 进展和已提交 EOF 可见。最外层父进程等待 worker 完整退出，runner 要求 QEMU 正常关机；这验证退出完整性而不夸大为退出清理公平性。增强目标和 baseline 使用同一测试契约。

内核栈使用检查由根目录和 `baseline_ucore/` 的 `build/kernel` 规则自动执行，在链接前根据编译器 callgraph 与栈帧数据核算用户陷入、嵌套中断和安全余量。也可单独执行：

```bash
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
make -C baseline_ucore kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`make full-verify` 当前会串联 `run-agent-tests.sh`、`run-proc-reap-tests.sh` 和 `run-syscall-fairness-tests.sh`，但不会串联 `run-fs-enospc-tests.sh`；发布前必须额外执行 `make fs-enospc-test`。聚合流程中的内核构建仍会自动执行内核栈检查。

## 覆盖关系

| 赛题任务 | 对应测试 |
| --- | --- |
| 任务一：Agent 进程与地址空间 | `agentfinal_ucore`、`agentscope_ucore`、`agentsecurity_ucore`、`agenttrust_ucore`、`usersafety_ucore` |
| 任务二：结构化工具调用 | `agentfinal_ucore`、`agentbench_ucore`、`agentsecurity_ucore` |
| 任务三：Context Path | `agentfinal_ucore`、`agentscope_ucore`、`agentsecurity_ucore`、`agentscan_ucore`、`labdemo_ucore` |
| 任务四：文件属性查询 | `agentfs_ucore`、`agentscope_ucore`、`agentscan_ucore`、`agentbench_ucore`、`agentconflict_ucore`、`agentvfs_ucore` |
| 任务五：Agent Loop | `agentloop_ucore`、`agentscope_ucore`、`agentsched_ucore`、`agentbench_ucore`、`labdemo_ucore` |
| 任务六：综合场景 | `labdemo_ucore`、`labbench_ucore`、`make dual-platform-run` |
| 安全与稳健性复测 | `agentscope_ucore`、`agentsecurity_ucore`、`agenttrust_ucore`、`agentvfs_ucore`、`usersafety_ucore`、`make fs-enospc-test`、`make proc-reap-test`、`make syscall-fairness-test`、`make kernel-stack-check` |

## 性能数据说明

性能数据分两类。

第一类是 QEMU 内专项微基准，由 `agentbench_ucore` 输出：

| 指标 | 含义 |
| --- | --- |
| `scalar_min/avg/max` | 多轮单次工具调用的 tick 观测。 |
| `batch_min/avg/max` | 多轮批量工具调用的 tick 观测。 |
| `scan_records` | 文件查询扫描路径触达的记录数。 |
| `index_records` | 文件查询索引路径检查的候选记录数。 |
| `file_digest_cache hits/misses` | 内容摘要缓存命中和未命中次数。 |
| `prefetch_records` | metadata 预取提示可见记录数。 |
| `event_wait_wake` | 事件等待/唤醒路径的 tick 观测。 |

第二类是双目标对照实验，由 `make dual-platform-run` 后的 `results/latest/` 生成：

| 文件 | 内容 |
| --- | --- |
| `summary.csv` | 双目标总体状态、状态文件数量和关键对照项。 |
| `runner-sweep.csv` | plain 与 AgentOS 在多个场景下的 tick 对照。 |
| `experiments/raw/*.csv` | 文件查询、Context/timeline、事件等待、并发写入、LLM Relay、失败恢复等实验原始数据。 |
| `experiments/experiment-stats.csv` | 每组实验的 min、avg、max、P50、P95。 |
| `charts/*.svg` | 从 CSV 生成的图表。 |

QEMU tick 会受到宿主机调度、终端输出和文件系统缓存影响，因此报告使用同一环境下的相对差异和结构化计数。文件查询看扫描数与候选数，Context/timeline 看重建步骤与 snapshot/query 成本，事件实验看轮询次数与 wait/wake 次数，并发实验看覆盖风险与租约拒绝结果。

## 基础兼容抽测

AgentOS-uCore 保留代表性 uCore 基础 syscall 抽测。CHAPTER=3 下的 `ch3_trace` 应输出：

```text
Test trace OK!
```

普通进程消息接口由 `agentsecurity_ucore` 中的 `mail_basic=1` 覆盖。该抽测说明 AgentOS 扩展没有破坏代表性基础 syscall 路径。

## 结果产物

专项测试通过后，QEMU 日志保留在对应脚本输出目录。双目标运行通过后，结果主要位于：

```text
/tmp/agentos-dual-platform/
results/latest/
```

`/tmp/agentos-dual-platform/` 保存 QEMU 日志、镜像提取状态文件和页面渲染结果。`results/latest/` 保存汇总 CSV、SVG 图表、Markdown 摘要和 HTML 导览页。`results/latest/` 是本机运行产物，默认不提交。

## 失败定位

| 现象 | 优先查看 |
| --- | --- |
| QEMU 长时间无输出 | `/tmp/agentos-dual-platform/seeded-action-state/*/ucore-run.log` |
| 构建失败 | `make agentos-user` 或 `make agentos-build` 的编译输出 |
| 某个专项测试失败 | [testing-details.md](testing-details.md) 中对应测试流程 |
| 双目标状态不一致 | [../verification.md](../verification.md) 的双目标验证章节 |
| 页面或图表缺失 | `host_tools/test_*.py` 和 `results/latest/` |

## 当前验证状态

本文仍不把当前 `make full-verify` 记录为全绿。各项独立专项不能与聚合入口状态混为一谈：2026-07-22 的 `scripts/run-agent-tests.sh` 15/15 已通过；稳定 PUBLIC principal、挂载孤儿清扫与账本重建、以及三启动 `fspquota_ucore` 也已在 AgentOS 与 baseline 的同一 raw 镜像 crash/seed/verify 三轮中动态通过。当前构建期内核栈预算为增强目标 `14432/16384`、baseline `8336/16384`；进程回收与基础 syscall 公平性沿用此前双目标通过记录，last-syscall 终审契约仍按其独立状态复测。详细命令、关键输出和覆盖边界见 [test-record.md](test-record.md)。

## 当前范围说明

| 方向 | 当前范围 |
| --- | --- |
| 文件扫描深度 | 自动扫描 uCore 根目录短文件名，文件对象 metadata 支持用户态显式写入和根目录自动发现。 |
| syscall 公平性覆盖 | 基础 QEMU 轮已动态验证单次 64 KiB 控制台写内部的同级进程进展；inode 首次写的 last-syscall 重调度/短写、截断 observer 和 worker 退出完整性属于待标准工具链复测的终审契约。pipe、exec/fork 分页、VM snapshot 屏障、退出清理和 Agent batch 当前按源码安全点契约检查；固定上界目录扫描与仅可信 Agent 可达的 metadata raw I/O 尚无独立公平性压力用例。 |
| Agent 调度 | 验证角色权重、受权调度配置、事件优先、deadline、heartbeat、wait cancel 和虚拟运行量。 |
| LLM Gateway | 内核提供结构化请求、响应事件、Context 和审计记录；云端访问由用户态或宿主机 Relay 完成。 |
| 页面和图表 | 内核输出 `agentos:event`、timeline、audit 和 provenance，宿主机工具负责转成页面和图表。 |
| 性能数据 | 当前采用同一 QEMU 环境下的 tick、扫描数、候选数、轮询数、拒绝数和重建步骤等相对指标。 |
