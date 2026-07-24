# 验证与性能评估

本文档说明 AgentOS-uCore 专项测试如何运行、每个测试覆盖哪些能力、性能数据如何解读。逐项流程见 [testing-details.md](testing-details.md)，原始输出样例见 [test-record.md](test-record.md)。

## 验证组织方式

AgentOS-uCore 的验证分五层：

| 层次 | 入口 | 作用 |
| --- | --- | --- |
| 构建检查 | `make agentos-user`、`make agentos-build`、`make kernel-stack-check` | 确认内核、用户态 ABI 和文件系统镜像能从当前源码构建。 |
| 增长与边界门 | `make ci-check` | 固定 profile 下检查源码、镜像、运行段、PCB、栈深/容量、完整 Agent 状态、十二个 Agent 模块预算、符号所有权和依赖方向；不启动 QEMU。 |
| AgentOS 专项测试 | `make agentos-test` 或 `bash scripts/run-agent-tests.sh` | 在 QEMU 中逐项运行 Agent 功能、权限和用户输入检查。 |
| 资源安全复测 | `make fs-enospc-test`、`make proc-reap-test`、`make thread-resource-test`、`make file-resource-test`、`make syscall-fairness-test` | 验证文件系统耗尽、持久 PUBLIC 配额、统一资源账户/teardown、调度公平和 syscall 工作预算。 |
| 双目标与聚合验证 | `make dual-platform-run`、`make full-verify` | 运行双目标负载；`full-verify` 先执行 `ci-check`，再串联 Agent、进程、syscall、file、thread 和 ENOSPC 机制专项。 |

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
make ci-check
```

`make ci-check` 使用 `ci/kernel-budgets.json` 的 canonical toolchain/profile。静态指标包括内核 LOC、stripped ELF/raw、text/data/BSS/total、`struct proc`、Context detail sidecar 与完整 21 页 Agent 状态的单实例/全局/ordinary/reserved/account 容量、线程调用图栈深、64 KiB boot stack 的链接跨度与启动调用图、32 MiB 虚拟栈容量、8 MiB 受信/保留物理栈池，以及 facade/core/context/identity/ipc/lifecycle/metadata/metadata_objects/metadata_store/observe/resource_controller/workflow_lifecycle 十二个模块。其受控符号 integration graph 还精确登记 `bio/file/fs/loader/main/proc/syscall/trap/vfs_security` 九个 bridge；SCC 上限 3 是 checker 内硬约束。该图不包含只通过普通 uCore 符号形成的依赖，不能解释为完整 uCore 调用图。阈值以 JSON 为准。

完整 16 case 的 duration 只累计各 QEMU case 的 monotonic 运行时间，不包含编译。当前 `calibrated_full_suite` 配置来自固定 runner 上 `bounded-runner-final-01/02/03` 三轮 16/16，时间为 `261.343281873s`、`237.948978492s`、`255.370930671s`，中位基线为 `255.370930671s`，上限为 `268.14s`；相对中位数约 5% headroom，足以覆盖最大样本，并比旧门更紧。GitLab job 同时用 `resource_group` 串行并绑定 `agentos-qemu-calibrated` tag；更换硬件、虚拟化层或 QEMU 后必须先恢复 provisional 状态并重新采样。

宿主 runner 以字节流读取并在进程退出前后全量 drain，不依赖文本行边界；包括 panic 在内的预定义 failure 模式按大小写不敏感方式检查，marker 后的剩余输出也不会跳过。监控循环每轮最多读取一个 64 KiB 块，随后重新检查 case timeout 和 marker grace，持续 stdout 洪泛不能饿死 deadline。每 case 最多接受 16 MiB 总输出，未终止记录最多保留 64 KiB，诊断行最多保留 4 KiB；总量/记录越界 fail closed，诊断副本截断。case deadline 优先于 checkpoint 成功，并在 scanner feed 和 runner notice 后重新核对，迟到 marker 不能通过。普通 case 必须自然 `rc=0`；stdout/stderr pipe 先到 EOF 时仍在原 case deadline 和 marker grace 内等待真实退出状态，不能因 EOF 触发信号，也不能借 EOF 延长宽限期。stop 阶段同样等待退出状态与 EOF 两者发布。marker grace 只用于终止已经失败或挂起的 case，其 `SIGTERM` 结果或升级 `SIGKILL` 都仍失败。仅两个持久化 checkpoint 阶段显式选择 checkpoint mode 后，可在预期 marker 后接受单次 runner `SIGTERM`；这验证 checkpoint 前状态的重挂载恢复，不等同硬掉电注入。`SIGKILL`、超时、非零退出和后置 panic 都失败。预期 guest fault 也必须先由显式 marker 逐次 arm，再精确消费一条 `bad addr`；未 arm 或 arm 后未发生都会 fail closed。预算 checker 31 项、通用 runner 24 项和生产 profile validator 5 项自测分别覆盖策略放宽、输出/退出边界与 shell 入口 profile 选择。

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

下表记录当前回归契约。generation-safe lifecycle、统一 resource controller/teardown、lazy physical stack、Context sidecar 和模块拆分后的当前工作树已在固定 runner 连续完成三轮 16/16；裸 marker 仍只表示对应脚本断言，不能从程序已编译或标记字符串存在推断通过。

| 测试程序 | 覆盖重点 | 通过标记 |
| --- | --- | --- |
| `agentfinal_ucore` | Agent 创建、21 页状态原子计费、批量工具调用、Context commit lane、snapshot、rollback、用户 cache、timeline、provenance、Run Ledger。 | `context_commit_lane=1 sequence=1..3 hash=1`、`agentfinal_ucore: parent passed` |
| `agentfs_ucore` | 真实 inode 绑定、metadata 双 bank、属性查询、索引查询、查询缓存、内容摘要、有界去重预取、文件删除清理、字段驱动批量 action 状态维护、依赖 generation 稳定性、metadata 工作预算和交接端点生命周期。 | `metadata_action_bounded=1 field_driven=1 batched=1 preemptions=5`、`prefetch_hints=1 bounded=1 count=2 preemptions=8`、`handoff_target_exit=1 endpoint_reuse=1 preemptions=6 ... clean=1`、`agentfs_ucore: parent passed` |
| `agentscan_ucore` | 根目录自动扫描、自动 metadata 写入、文件创建和删除后的 metadata 更新。 | `agentscan_ucore: parent passed` |
| `agentloop_ucore` | FIFO、stable source=4、directed=8、external=12、KERNEL origin 预留容量、消费后配额归还、慢 watcher 广播隔离、watch/unwatch、timeout、heartbeat、wait cancel、事件因果。 | `message_source_limit=4`、`ipc_class_limit=8`、`external_limit=12`、`system_event_reserved=4`、`external_reject_reclaim=1`、`broadcast_slow_watcher_isolated=1`、`parent passed` |
| `agentsched_ucore` | 角色权重、受权调度配置、事件优先、调度原因记录和资源域内 Agent/FIFO 公平性观测。 | 当前三轮完整套件均输出 `agentsched_ucore: parent passed` |
| `agentconflict_ucore` | 文件编辑租约、非持有者写入拒绝、版本提交检查、普通进程拒绝。 | `agentconflict_ucore: parent passed` |
| `agentllm_ucore` | 显式 `MESSAGE` / `LLM_DONE` route 下的结构化请求、Relay 模板响应、LLM capability、完成事件、Context/timeline。 | `agentllm_ucore: parent passed` |
| `agentbench_ucore` | 批量工具、Context、文件查询、预取、timeout/heartbeat，以及显式 route 下的 wait/wake 计时。 | `agentbench_ucore: parent passed` |
| `labbench_ucore` | 综合场景中的性能入口，包装运行 `agentbench_ucore`。 | `labbench_ucore: parent passed` |
| `labdemo_ucore` | orchestrator 建立 sentinel -> investigator -> recovery 路由后的恢复场景、文件查询、预取交接、消息、权限、audit/timeline/provenance。 | `labdemo_ucore: parent passed` |
| `agentsecurity_ucore` | 既有权限/route/controller 负向检查；新增用户非零 cause/span 拒绝、可信跨 Agent source attribution、low/high audit authority 隔离。 | 当前三轮完整套件均输出 `trusted_span_authority=1`、`trusted_cause_attribution=1`、`audit_authority_partition=1` 和 `parent passed` |
| `agenttrust_ucore` | 可执行映像 W^X、密封映像不可变、bootstrap 授权范围、Agent 角色与可信映像绑定。 | `agenttrust_ucore: parent passed` |
| `agentvfs_ucore` | 工作流文件能力、公共/工作流命名空间隔离、跨 scope inode 描述符撤销、worker pipe 单跳委派和失败事务原子性。 | `cross_scope_fd_revoked=1`、`worker_pipe_delegation=1`、`parent passed` |
| `agentscope_ucore` | syscall 541/542/545、generation-safe lifecycle、跨域对象隔离、PUBLIC 降权后代谱系撤销、metadata/观测预算和 retirement。 | 当前专项约 `93.7s`，实际输出 `scope_close_authority=1`、`scope_controller_exit_revoke=1 public_lineage=1`、`scope_forced_cleanup=1`、`scope_replacement_admitted=1` 和 `parent passed` |
| `iobudget_ucore` | syscall 544 ABI v3 sized-copy、稳定 PUBLIC/workflow owner、NORMAL/CONTROL class、owner/shared/device lease 上界、线程退出 lease 回收、scheduler 内核态中断交付、fault teardown 清理归因/debt 结算、完成归因、PUBLIC cache/速率压力、workflow cache floor 与压力下写入进展。 | 当前三轮完整套件均通过八项具名机制 marker 与 `parent passed`；ABI sized-copy 是无单独 marker 的第九类断言 |
| `usersafety_ucore` | syscall 指针、字符串、`exec` 参数、线程入口、等待队列、管道、文件和信号量输入范围。 | `usersafety_ucore: parent passed` |

原始输出不在本文档重复展开，统一保存在 [test-record.md](test-record.md)。每个测试的流程和断言解释见 [testing-details.md](testing-details.md)。

scope 回归核对：PUBLIC=0、SYSTEM=1、动态 workflow>=3，数值 2 是安装级 PUBLIC 存储 principal。权威 lifecycle ledger 固定 8 槽，key 为 `(id,generation)`，ACTIVE+CLOSING 最多 4 个；槽彻底退休后才以更高 generation 复用。`vfs_scope_refs[NPROC]` 只是 VFS 引用/清理记录。进程、线程、file object、block/inode、cache 和 Agent 状态页统一映射到 generation-safe EXEC/STORAGE account；每个 Agent 的 9 sidecar + 6 mirror + 6 shadow 以一次 21 页 `RESOURCE_AGENT_STATE_PAGE` 请求原子计费。`resource_domain_id` 只做 CPU 调度分区。其余 metadata/audit 与存储容量契约仍按对应 policy 文件核对。

Workflow 根把不复用的 `agent_control_id` 绑定到当次 `(lifecycle id,generation)`。根离开或 factory 关闭时先进入 CLOSING，再按完整 key 撤销；Agent/VFS 凭据已清零的 PUBLIC child/grandchild 仍必须终止。exec prepare/commit/abort 在同一发布边界复核 lifecycle。成员进入统一 teardown，自行清理 FD、inode、sidecar、VM 和 resource/I/O 账目；最后成员释放引用后才进入 RETIRING。当前专项已经实际取得 `public_lineage=1` 和 `parent passed`。

块 I/O policy ABI v3 的设备根 burst/refill 为 560/280，PUBLIC NORMAL 为 32/16；每个 active workflow 的 NORMAL/CONTROL/BACKGROUND 为 24/12、48/24、8/4，每个 retiring workflow 只保留 BACKGROUND 8/4；SYSTEM SYSTEM/BACKGROUND 为 96/48、16/8，共享前台 slice 为 32/16。普通流量必须取得设备根信用并等待 device debt；SYSTEM owner、CONTROL 和 SYSTEM class 可在根信用耗尽时带 debt 前进，因此静态 560/280 envelope 是配置约束，不是保护流量的运行时硬总上限。shared fast path 在没有 admission waiter 时可直接借信用，排队 grant 才按 owner/class cursor 轮转。cache 的 SYSTEM/PUBLIC/active workflow floor/cap 为 40/96、24/48、36/64，`NBUF=256`；当前轮转退役清理 job 临时使用 3/8，cap 是稳态驻留边界而非瞬时硬上限。

buffer cache 以 exclusive holder、递归深度和私有等待队列串行化同块访问；持有 buffer 时 I/O/CPU checkpoint 均不能睡眠或 yield。复合文件系统原语另有 FS atomic depth；只有释放全部 buffer、且调用者已提交对象状态的 quiescent checkpoint 才可等待。loader 与 metadata exact-read 从正数短读前缀继续。PUBLIC 赞助对象接管使用固定工作区收集/排序块，按 qmap block 分组，并在唯一 claim gate 下完成 qmap-first、inode-last 前向提交。metadata COW 先验证新 primary 再更新旧 mirror；同步管理请求使用 FIFO ticket 接纳并建立不可替换 job，失败条件检查到 condition queue 入队保持关中断原子，不把 syscall 返回描述成 primary 已完成验证的持久化屏障。

scheduler 每轮在 idle context 安装 kernel trap 向量并短暂开启中断，再进入后台维护和线程选择。该机制为所有调度轮提供 timer/device 中断交付边界，防止唯一 runnable 线程在内核 pipe 条件路径反复 `yield()`、长期不返回用户态时锁死 I/O debt 与后台 token refill；`scheduler_interrupt_progress=1` 对此作动态回归。线程选择本身再分两级：外层 active-domain FIFO 严格轮转，内层才执行普通 FIFO 或 Agent 软评分；Agent/score burst 按域维护。

正常退出、主线程 fault、workflow revoke 和构造回滚共用 `LIVE -> REQUESTED -> QUIESCING -> DETACHED -> RECLAIMING -> SETTLING -> HANDOFF -> PUBLISHED -> RECYCLED`。进程的 Agent 侧清理只通过 phase-aware、幂等的 `agent_proc_teardown()` 推进：它负责撤销控制权、释放 Context/身份，并在 SETTLING 验证 Agent 私有状态与 Agent 页账为空。REQUESTED 后禁止发布新对象；唯一 teardown owner 继续由外层阶段结算通用 resource account 与 I/O lease/debt、清除 terminal 凭据和 lifecycle，scheduler 切回 idle stack 后才释放最后物理栈页。旧 `fault_exit_cleanup=1` 是历史问题证据，不能替代重构后状态机的当前回归。

账本验证不得假设当前可见窗口 sequence 连续：系统 sequence 跨 scope 单调，low/high/principal 分区独立滚动。测试仅对无 gap 的相邻记录核验直接 `prev_hash`，并用 `dropped_records=total_records-visible_records` 解释窗口外记录。非活跃 principal 的旧 high 证据是可观测但有界的历史窗口。

## 资源安全与内核栈入口

文件系统耗尽复测使用极小 SFS 镜像分别触发 inode、inode cache 和数据块耗尽，要求分配失败被返回给调用者、内核继续运行且释放后资源可复用；AgentOS 配额场景还执行 640 次 PUBLIC 短命 inode 循环，验证版本 sidecar 最终回收后 workflow 编辑版本与内容摘要缓存仍可用。两个目标随后各在同一磁盘镜像上连续启动三次 `fspquota_ucore`，先在打开文件 unlink 后命中显式 checkpoint 并由 runner 以单次 SIGTERM 结束 QEMU，验证重挂载物理回收，再验证 PUBLIC 用量跨完整进程域退出与重挂载保持、删除后才退款；该测试不宣称模拟硬掉电：

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

根目录 AgentOS 仍使用 16 KiB stack + 4 KiB guard/canary，但只预建虚拟槽，物理页在线程 admission 时映射，在 scheduler handoff 后释放。32 MiB 是全部虚拟槽容量，8 MiB 是受信/保留线程物理池。`baseline_ucore/` 当前仍是固定物理栈实现，二者共享栈深/guard 行为目标而非 lazy mapping 实现。构建期调用图检查可单独执行：

```bash
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
make -C baseline_ucore kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`make full-verify` 当前先执行 `make ci-check`，随后串联 `run-agent-tests.sh`、`run-proc-reap-tests.sh`、`run-syscall-fairness-tests.sh`、`run-file-resource-tests.sh`、`run-thread-resource-tests.sh` 和 `run-fs-enospc-tests.sh`。GitLab 还用独立串行 job 运行五组机制回归。是否通过必须以本次命令日志为准。

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

本文仍不把当前 `make full-verify` 记录为全绿。当前独立回归不能与未运行的聚合入口状态混为一谈：

- 13824/16384、371.5s 和 126.1s 均是当前 lifecycle/resource/teardown/lazy-stack/module 重构之前的历史快照；
- `sizeof(struct proc)` 的当前静态探针为 `28808` B；版本化 JSON 保留 `28776` B 的冻结 baseline 和 `30215` B 的 max，当前 actual 只需低于 max，不能据此向上移动 ratchet。完整 Agent 状态按 21 页/`86016` B 原子计费，全局/ordinary/reserved/ordinary-domain/reserved-domain 六项预算为 `11010048/8257536/2752512/5505024/688128` B，9 页 detail sidecar 仍另以每活跃 Agent 36 KiB、全局 4.5 MiB 观察；
- 源码、镜像、运行段和栈的最终值不在本文抄录可能漂移的工作树快照，以本次 `make ci-check` 输出和版本化 JSON 为准；checker 当前含 31 项预算、24 项通用 runner 和 5 项生产 profile validator 自测；
- duration budget 已由 bounded/flood-safe runner 在固定 runner 三轮 16/16 校准为 `255.370930671s` 基线、`268.14s` 上限；
- 当前 `agentscope_ucore` 已取得 `public_lineage=1`，proc、syscall、file、thread、ENOSPC 专项也已通过；
- `make full-verify` 尚未在当前工作树完成，不能据旧专项外推聚合全绿；

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
