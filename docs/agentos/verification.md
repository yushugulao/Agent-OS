# 验证与性能评估

本文档说明 AgentOS-uCore 专项测试如何运行、每个测试覆盖哪些能力、性能数据如何解读。逐项流程见 [testing-details.md](testing-details.md)，原始输出样例见 [test-record.md](test-record.md)。

## 验证组织方式

AgentOS-uCore 的验证分四层：

| 层次 | 入口 | 作用 |
| --- | --- | --- |
| 构建检查 | `make agentos-user`、`make agentos-build`、`make kernel-stack-check` | 确认内核、用户态 ABI 和文件系统镜像能从当前源码构建；每次生成 `build/kernel` 前都会自动执行内核栈预算检查。 |
| AgentOS 专项测试 | `make agentos-test` 或 `bash scripts/run-agent-tests.sh` | 在 QEMU 中逐项运行 Agent 功能、权限和用户输入检查。 |
| 资源安全复测 | `make fs-enospc-test`、`make proc-reap-test`、`make thread-resource-test`、`make file-resource-test`、`make syscall-fairness-test` | 验证文件系统耗尽、持久 PUBLIC 配额、进程回收、进程/线程/filepool 资源域、线程域级调度公平及 syscall 内核工作预算。线程专项当前只构建 AgentOS 主目标，其余标明双目标的脚本同时运行 baseline。 |
| 双目标与聚合验证 | `make dual-platform-run`、`make full-verify` | 运行双目标科研平台负载，并串联宿主机、AgentOS、进程回收、syscall 公平性、filepool 和线程资源检查。 |

`agentos-test` 和 `thread-resource-test` 只关注根目录 AgentOS-uCore 目标；`fs-enospc-test`、`proc-reap-test`、`file-resource-test` 和 `syscall-fairness-test` 同时覆盖根目录增强目标与 `baseline_ucore/` 普通目标。双目标验证详情见 [../verification.md](../verification.md)。

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

下表同时区分既有运行证据和当前回归契约。早期输出支持既有 Agent/route/queue 机制；可信 workflow scope、私有 cause/span owner、审计 authority 分区和 workflow 强制撤销是后续增强，必须以对应 QEMU 日志为准，不能从程序已编译或标记字符串存在推断通过。

| 测试程序 | 覆盖重点 | 通过标记 |
| --- | --- | --- |
| `agentfinal_ucore` | Agent 创建、Context 映射、批量工具调用、Context Path、snapshot、rollback、用户 cache、timeline、provenance、Run Ledger。 | `agentfinal_ucore: parent passed` |
| `agentfs_ucore` | 真实 inode 绑定、metadata 双 bank、属性查询、索引查询、查询缓存、内容摘要、有界去重预取、文件删除清理、字段驱动批量 action 状态维护、依赖 generation 稳定性、metadata 工作预算和交接端点生命周期。 | `metadata_action_bounded=1 field_driven=1 batched=1 preemptions=5`、`prefetch_hints=1 bounded=1 count=2 preemptions=8`、`handoff_target_exit=1 endpoint_reuse=1 preemptions=6 ... clean=1`、`agentfs_ucore: parent passed` |
| `agentscan_ucore` | 根目录自动扫描、自动 metadata 写入、文件创建和删除后的 metadata 更新。 | `agentscan_ucore: parent passed` |
| `agentloop_ucore` | FIFO、stable source=4、directed=8、external=12、KERNEL origin 预留容量、消费后配额归还、慢 watcher 广播隔离、watch/unwatch、timeout、heartbeat、wait cancel、事件因果。 | `message_source_limit=4`、`ipc_class_limit=8`、`external_limit=12`、`system_event_reserved=4`、`external_reject_reclaim=1`、`broadcast_slow_watcher_isolated=1`、`parent passed` |
| `agentsched_ucore` | 角色权重、受权调度配置、事件优先、调度原因记录和资源域内 Agent/FIFO 公平性观测。 | 本次线程改动后单独运行输出 `agentsched_ucore: parent passed` |
| `agentconflict_ucore` | 文件编辑租约、非持有者写入拒绝、版本提交检查、普通进程拒绝。 | `agentconflict_ucore: parent passed` |
| `agentllm_ucore` | 显式 `MESSAGE` / `LLM_DONE` route 下的结构化请求、Relay 模板响应、LLM capability、完成事件、Context/timeline。 | `agentllm_ucore: parent passed` |
| `agentbench_ucore` | 批量工具、Context、文件查询、预取、timeout/heartbeat，以及显式 route 下的 wait/wake 计时。 | `agentbench_ucore: parent passed` |
| `labbench_ucore` | 综合场景中的性能入口，包装运行 `agentbench_ucore`。 | `labbench_ucore: parent passed` |
| `labdemo_ucore` | orchestrator 建立 sentinel -> investigator -> recovery 路由后的恢复场景、文件查询、预取交接、消息、权限、audit/timeline/provenance。 | `labdemo_ucore: parent passed` |
| `agentsecurity_ucore` | 既有权限/route/controller 负向检查；新增用户非零 cause/span 拒绝、可信跨 Agent source attribution、low/high audit authority 隔离。 | `trusted_span_authority=1`、`trusted_cause_attribution=1`、`audit_authority_partition=1`、`parent passed`；本轮通过 |
| `agenttrust_ucore` | 可执行映像 W^X、密封映像不可变、bootstrap 授权范围、Agent 角色与可信映像绑定。 | `agenttrust_ucore: parent passed` |
| `agentvfs_ucore` | 工作流文件能力、公共/工作流命名空间隔离、跨 scope inode 描述符撤销、worker pipe 单跳委派和失败事务原子性。 | `cross_scope_fd_revoked=1`、`worker_pipe_delegation=1`、`parent passed` |
| `agentscope_ucore` | syscall 541 factory、542 单跳 pipe 委派、545 可信关闭、动态 scope 和跨域对象隔离、metadata 事务/微写、观测预算、配额，以及根退出/factory 关闭触发的 CLOSING、阻塞成员协作退出和 bounded retirement。 | 回归要求输出 `scope_close_authority=1`、`scope_controller_exit_revoke=1`、`scope_forced_cleanup=1`、`scope_replacement_admitted=1`，以及既有 pipe/observe/lifecycle 标记和 `parent passed` |
| `iobudget_ucore` | syscall 544 ABI v3 sized-copy、稳定 PUBLIC/workflow owner、NORMAL/CONTROL class、owner/shared/device lease 上界、线程退出 lease 回收、scheduler 内核态中断交付、fault teardown 清理归因/debt 结算、完成归因、PUBLIC cache/速率压力、workflow cache floor 与压力下写入进展。 | 最终 teardown 修复后的独立轮输出八项具名机制 marker 与 `parent passed`，`elapsed=2.4s`；ABI sized-copy 是无单独 marker 的第九类断言 |
| `usersafety_ucore` | syscall 指针、字符串、`exec` 参数、线程入口、等待队列、管道、文件和信号量输入范围。 | `usersafety_ucore: parent passed` |

原始输出不在本文档重复展开，统一保存在 [test-record.md](test-record.md)。每个测试的流程和断言解释见 [testing-details.md](testing-details.md)。

本次 scope 回归核对的固定机制参数如下：PUBLIC=0、SYSTEM=1、动态 workflow>=3，数值 2 保留为安装级 PUBLIC 存储 principal；ACTIVE+CLOSING admission 最多 4 个，`VFS_SCOPE_LIFECYCLE_CAP=8` 约束计入 admission 的 ACTIVE/CLOSING 与 RETIRING 身份。VFS 生命周期 ledger 有 `NPROC` 条身份记录，只复用 `used == 0` 的记录；FS reclaim cursor 最多 8 个，reaper 在 `NPROC` 账本范围轮转选择，防止固定槽位饥饿和退役状态覆盖。进程普通槽 96、受控保留槽 32、每 admitted scope 保留 8；metadata/dependency/action/edit/span-prefetch 每 scope 分别 112/16/8/8/8。审计物理 512、每 scope 128，low/high 各 64，low principal 上限 16、high active principal 上限 8；high 满时只能自滚或回收 inactive principal，active principal 互不淘汰。存储策略按完成镜像空闲量核算并把 PUBLIC principal/G/S 持久化到 superblock，挂载从 qmap/dinode 重建 PUBLIC 用量；每 scope 硬下限 320 inode/512 block，SYSTEM 硬下限 8 inode/512 block。当前平台镜像实际核算为每 scope 342/1195、SYSTEM 64/512，构建日志必须与内核使用同一版本化契约。

Workflow 根在发布前把不复用的 `agent_control_id` 绑定为唯一生命周期 controller；关闭权不会传播给低权限成员或后创建的 Orchestrator。根离开或可信 bootstrap factory 调用 syscall 545 时，ACTIVE 原子进入 CLOSING，`vfs_scope_active()` 随即为假，新 acquire、pending exec 发布和存储预留失败；active/pending 成员收到协作退出请求。CLOSING 仍保留完整 I/O/cache owner，直到成员在自身线程上下文中完成 FD、inode、lease/debt 和地址空间清理；之后才进入 RETIRING。最终专项使用真正 pipe EOF、9 轮连续撤销和 replacement admission 验证这条资源生命周期。

块 I/O policy ABI v3 的设备根 burst/refill 为 560/280，PUBLIC NORMAL 为 32/16；每个 active workflow 的 NORMAL/CONTROL/BACKGROUND 为 24/12、48/24、8/4，每个 retiring workflow 只保留 BACKGROUND 8/4；SYSTEM SYSTEM/BACKGROUND 为 96/48、16/8，共享前台 slice 为 32/16。普通流量必须取得设备根信用并等待 device debt；SYSTEM owner、CONTROL 和 SYSTEM class 可在根信用耗尽时带 debt 前进，因此静态 560/280 envelope 是配置约束，不是保护流量的运行时硬总上限。shared fast path 在没有 admission waiter 时可直接借信用，排队 grant 才按 owner/class cursor 轮转。cache 的 SYSTEM/PUBLIC/active workflow floor/cap 为 40/96、24/48、36/64，`NBUF=256`；当前轮转退役清理 job 临时使用 3/8，cap 是稳态驻留边界而非瞬时硬上限。

buffer cache 以 exclusive holder、递归深度和私有等待队列串行化同块访问；持有 buffer 时 I/O/CPU checkpoint 均不能睡眠或 yield。复合文件系统原语另有 FS atomic depth；只有释放全部 buffer、且调用者已提交对象状态的 quiescent checkpoint 才可等待。loader 与 metadata exact-read 从正数短读前缀继续。PUBLIC 赞助对象接管使用固定工作区收集/排序块，按 qmap block 分组，并在唯一 claim gate 下完成 qmap-first、inode-last 前向提交。metadata COW 先验证新 primary 再更新旧 mirror；同步管理请求使用 FIFO ticket 接纳并建立不可替换 job，失败条件检查到 condition queue 入队保持关中断原子，不把 syscall 返回描述成 primary 已完成验证的持久化屏障。

scheduler 每轮在 idle context 安装 kernel trap 向量并短暂开启中断，再进入后台维护和线程选择。该机制为所有调度轮提供 timer/device 中断交付边界，防止唯一 runnable 线程在内核 pipe 条件路径反复 `yield()`、长期不返回用户态时锁死 I/O debt 与后台 token refill；`scheduler_interrupt_progress=1` 对此作动态回归。线程选择本身再分两级：外层 active-domain FIFO 严格轮转，内层才执行普通 FIFO 或 Agent 软评分；Agent/score burst 按域维护。

由主线程触发的正常退出、用户 fault 或非法指令共用不可中断的进程级 terminal cleanup I/O/kernel-work 上下文；非主 sibling 无论正常退出还是 fault 都只退出自身线程。文件关闭和 inode 回收产生的物理传输继续按原 owner/class 记账；剩余 lease 与 owner/class debt 在释放 teardown thread 前结算，PUBLIC/NORMAL 还等待 device debt，SYSTEM/CONTROL 的受保护 device debt 留在全局设备根账本中由 refill 偿还。`fault_exit_cleanup=1` 覆盖 PUBLIC 主线程 page fault、未链接文件清理、物理写归因和两级 debt 清零。可信 metadata bank 则在 `timer_init()` 后、`bio_policy_start()` 与用户进程发布前完成加载尝试和可信判定；单副本损坏从另一有效 bank 恢复，无可验证有效 bank 时 metadata API fail closed，但系统继续启动且 scope 的 VFS-labelled 清理仍可退休。当前没有启动 bank 损坏动态注入。

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

线程资源专项使用 19/12/6/6/4 tiny policy 精确触发线程物理池、普通/保留全局水位和普通/保留域上限，验证主线程预扣、额外线程扣账、容量拒绝计数稳定、退出退款、系统保留进展、跨域复用及 active-domain 公平轮转。策略还在编译期要求每类域上限严格小于对应全局水位，防止错误配置允许单域耗尽整个类别：

```bash
make thread-resource-test TOOLPREFIX=riscv64-linux-gnu-
```

通过标记依次为 `domain_limit`、`capacity_reject_stable`、`reserved_domain_limit`、`reserved_domain_reuse`、`exit_reuse`、`ordinary_waterline`、`global_thread_limit`、`reserved_global_limit`、`reserved_progress`、`reserved_global_reuse`、`global_reuse`、`domain_fairness`、`parent passed` 和 `[thread-resource] all checks passed`。`global_thread_limit` 在第三普通域仍有域内余量时触发普通全局水位；`reserved_global_limit` 在两个保留域都未满、物理池还留一槽时触发保留全局水位。公平阶段让攻击域的 5 个 worker 全部实际运行，同时要求独立 victim 完成 512 次让出且攻击域 yield-loop 总计数不超过固定 `bound=576`。`capacity_reject_stable` 只证明容量拒绝不污染计数，不代表动态注入映射失败。

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

`make full-verify` 当前会串联 `run-agent-tests.sh`、`run-proc-reap-tests.sh`、`run-syscall-fairness-tests.sh`、`run-file-resource-tests.sh` 和 `run-thread-resource-tests.sh`，但不会串联 `run-fs-enospc-tests.sh`；发布前必须额外执行 `make fs-enospc-test`。聚合流程中的内核构建仍会自动执行内核栈检查。

## 覆盖关系

| 赛题任务 | 对应测试 |
| --- | --- |
| 任务一：Agent 进程与地址空间 | `agentfinal_ucore`、`agentscope_ucore`、`agentsecurity_ucore`、`agenttrust_ucore`、`usersafety_ucore` |
| 任务二：结构化工具调用 | `agentfinal_ucore`、`agentbench_ucore`、`agentsecurity_ucore` |
| 任务三：Context Path | `agentfinal_ucore`、`agentscope_ucore`、`agentsecurity_ucore`、`agentscan_ucore`、`labdemo_ucore` |
| 任务四：文件属性查询 | `agentfs_ucore`、`agentscope_ucore`、`agentscan_ucore`、`agentbench_ucore`、`agentconflict_ucore`、`agentvfs_ucore` |
| 任务五：Agent Loop | `agentloop_ucore`、`agentscope_ucore`、`agentsched_ucore`、`threadresource_ucore`、`agentbench_ucore`、`labdemo_ucore` |
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

本文仍不把当前 `make full-verify` 记录为全绿。各项专项不能与该聚合入口状态混为一谈。当前 workflow 强制撤销修复的证据为：

- 默认 AgentOS 构建通过，自动内核栈预算为 `required=13824 < 16384`；
- 实现快照的完整 16 项 Agent 脚本通过，墙钟约 `371.5s`；其中 `agentscope_ucore` 约 `127.9s`；
- 子代理审查补强 EOF 防伪、9 轮回收、低权限资源持有和 64 位参数校验后，最终 `agentscope_ucore` 专项约 `126.1s` 通过，输出四项 `scope_close_*`/cleanup/replacement 标记及 `parent passed`；
- 审查后未重跑完整 16 项；此前观测查询、19/12/6/6/4 tiny policy 线程资源、单独 `agentsched_ucore`、双目标进程回收、syscall 公平性和 filepool 脚本的通过结果继续按历史轮保留；
- `make full-verify` 尚未运行，不能据这些专项外推聚合全绿；

详细命令、关键输出和覆盖边界见 [test-record.md](test-record.md)。

## 当前范围说明

| 方向 | 当前范围 |
| --- | --- |
| 文件扫描深度 | 自动扫描 uCore 根目录短文件名，文件对象 metadata 支持用户态显式写入和根目录自动发现。 |
| syscall 与 I/O 公平性覆盖 | CPU 终审轮已动态覆盖控制台、inode 写和截断；CPU checkpoint 与 I/O debt checkpoint 是互补机制。`iobudget_ucore` 还动态覆盖唯一 runnable 内核 pipe waiter 下的 scheduler 中断交付、fault teardown 的 attributed cleanup/debt settlement，以及一个 PUBLIC 和一个 workflow Orchestrator CONTROL owner；它没有断言 shared 排队轮转，也未覆盖 Recovery、SYSTEM/workflow BACKGROUND、多 workflow 同压、retiring 3/8、跨 owner LRU/transient 或主动 device-debt 注入。启动 bank 损坏、VirtIO 设备错误/短 I/O、metadata COW 掉电及 grouped qmap claim 中点掉电仍缺动态证据。 |
| Agent 调度 | 验证 active resource domain 外层轮转、域内角色权重、受权调度配置、事件优先、deadline、heartbeat、wait cancel、虚拟运行量和 thread bomb 下的 victim 进展。 |
| Workflow 撤销 | 动态验证根自关、factory 关闭、根自然退出、阻塞低权限成员清理和 9 轮回收；尚未精确注入 close 与 spawn/pending exec 竞态、多线程 controller、纯 CPU 成员或关闭期间主动 I/O debt/inode 压力。 |
| LLM Gateway | 内核提供结构化请求、响应事件、Context 和审计记录；云端访问由用户态或宿主机 Relay 完成。 |
| 页面和图表 | 内核输出 `agentos:event`、timeline、audit 和 provenance，宿主机工具负责转成页面和图表。 |
| 性能数据 | 当前采用同一 QEMU 环境下的 tick、扫描数、候选数、轮询数、拒绝数和重建步骤等相对指标。 |
