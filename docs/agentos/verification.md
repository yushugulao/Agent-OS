# 验证与性能评估

本文档说明 AgentOS-uCore 专项测试如何运行、每个测试覆盖哪些能力、性能数据如何解读。发布结果只从 `evidence/releases/INDEX.md` 指向的冻结 bundle 读取，不在说明文档中复制历史日志。

发布状态绑定 `evidence/releases/INDEX.md` 与对应 release bundle 的 `manifest.json`。代码提交 C 先冻结，采集器在干净 C 上执行唯一一次 `make full-verify`，证据提交 E 作为 C 的直接子提交，只新增 bundle 并追加索引。包内 `source_commit` 指向 C，`SELF` 解析为 E。本地完整验收和 C→E bundle 是唯一正式交付链。GitLab 只托管源码与证据，不配置 Runner；`remote_ci.status` 固定为 `not-attached`。下文历史记录不能外推到未被 bundle 绑定的代码。

## 验证组织方式

AgentOS-uCore 的验证分五层：

| 层次 | 入口 | 作用 |
| --- | --- | --- |
| 构建检查 | `make agentos-user`、`make agentos-build`、`make kernel-stack-check` | 确认内核、用户态 ABI 和文件系统镜像能从当前源码构建。 |
| 增长与边界门 | `make local-check` | 固定 profile 下检查源码、镜像、运行段、PCB、栈深/容量、完整 Agent 状态、版本化 owner/bridge 集合及 metadata 聚合 source/text/BSS；不启动 QEMU。 |
| AgentOS 专项测试 | `make agentos-test` | 在隔离的 QEMU lane 中并行运行 Agent 功能、权限和用户输入检查。 |
| 资源与持久化复测 | `make fs-enospc-test`、`make fs-allocator-fault-test`、`make proc-reap-test`、`make thread-resource-test`、`make file-resource-test`、`make physical-resource-test`、`make metadata-recovery-test`、`make observe-recovery-test`、`make virtio-disk-test`、`make syscall-fairness-test`、`make workflow-teardown-race-test` | 动态验证存储/物理页资源边界、统一 teardown、metadata/观测重启恢复、VirtIO 故障矩阵、文件系统分配事务一致性和跨资源竞态。 |
| 双目标与聚合验证 | `make dual-platform-run`、`make full-verify` | 运行双目标负载；profile v7 串联 Host 合同、传统接口动态门、版本化 Agent 套件、双目标及独立机制专项。 |

`agentos-test` 和 `thread-resource-test` 只关注根目录 AgentOS-uCore 目标；`fs-enospc-test`、`proc-reap-test`、`file-resource-test` 和 `syscall-fairness-test` 同时覆盖根目录增强目标与 `baseline_ucore/` 普通目标。双目标验证详情见 [../verification.md](../verification.md)。

双目标的 AgentOS launcher 由共享 `RP_AGENTOS_ROLE_PROGRAMS` 清单区分真实 `agent_create_role()` 角色进程和 `agent_worker_create()` 非 Agent worker，不再从父进程的预期分支反推子进程身份。每个子进程在 `exec` 后由通用启动钩调用 `agent_info()`，通过显式委派的 pipe 回传 `is_agent`、role、filesystem domain 和 capability mask；Guest ledger 与 Host 检查器都会拒绝 launcher/回传身份不一致，Host mutation test 另会改写两类字段确认 fail closed。发布 bundle 必须保存本次双目标 Guest 记录，并动态产生 `identity_source=child_after_exec`、`agentos_agent_launches` 和 `agentos_worker_launches`；静态编译、Host 自测或旧轮次不能代替这些记录。

## 验证环境

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Linux / WSL2 Ubuntu |
| 工具链 | `riscv64-linux-gnu-gcc`、`riscv64-linux-gnu-ld`、`riscv64-linux-gnu-objdump` |
| 虚拟机 | `qemu-system-riscv64` |
| 构建工具 | `make`、`bash`、`python3` |

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
make local-check
```

`make local-check` 使用 `ci/kernel-budgets.json` 的 canonical toolchain/profile。静态指标包括内核 LOC、stripped ELF/raw、text/data/BSS/total、`struct proc`、Context detail sidecar 与完整 17 页 Agent 状态的单实例/全局/ordinary/reserved/account 容量、线程调用图栈深、64 KiB boot stack 的链接跨度与启动调用图、32 MiB 虚拟栈容量和 8 MiB 受信/保留物理栈池。owner 模块、integration bridge、允许依赖和 SCC 边界都由版本化注册集合给出，不在文档复制固定数量。metadata transaction/file-state/catalog/query/scan/directory/objects/actions/store（含 format/I/O）、IPC 及 contract headers 还共同受 `metadata_control_plane` 聚合 source/text/BSS 预算；source 只保留固定接口开销，loaded text 与 BSS 维持 no-growth，防止跨文件迁移。受控 integration graph 不包含只通过普通 uCore 符号形成的依赖，不能解释为完整 uCore 调用图。

同一 Host 门还执行 metadata canonical genesis raw-image 合同、catalog capacity、catalog rollback fence 模型/变异、metadata boot reprobe 和 Agent wait 原子交付接线检查。catalog、STORAGE policy、sidecar、COW 与恢复边界由源码和版本化 policy/checker 共同定义；文档不复制易变的 mutation 数或 case 数。`WAIT_ATOMIC_TEST_PROFILE` 与 Context 同步故障 profile 使用独立构建和 timing file，不计入普通 Agent suite 校准。

Agent suite duration 只累计版本化 case 清单中各 QEMU case 的 monotonic 运行时间，不包含编译。当前配置为 `provisional_requires_full_suite`；最终提交必须在记录的本地环境完成三轮测量，才能建立新的时长门。历史校准留在 Git 历史中，不随当前参赛树交付，也不能作为当前性能结论。

通用 QEMU runner 以字节流读取并在进程退出前后全量 drain，不依赖文本行边界；包括 panic 在内的预定义 failure 模式按大小写不敏感方式检查，marker 后的剩余输出也不会跳过。控制台仍可转发原始字节，落盘的 `.guest.log` 则把 CRLF、孤立 CR 统一成 LF；exact-line marker、SHA256、CSV 行号和 release manifest 一律绑定这份 canonical LF transcript。监控循环每轮最多读取一个 64 KiB 块，随后重新检查 case timeout 和 marker grace，持续 stdout 洪泛不能饿死 deadline。每 case 最多接受 16 MiB 总输出，未终止记录最多保留 64 KiB，诊断行最多保留 4 KiB；总量/记录越界 fail closed，诊断副本截断。case deadline 优先于完成判断，并在 scanner feed 和 runner notice 后重新核对，迟到 marker 不能通过。普通 case 必须自然 `rc=0`；stdout/stderr pipe 先到 EOF 时仍在原 deadline 内等待真实退出状态。checkpoint profile 只接受完整 marker 后 runner 发出的单次 `SIGTERM`。powercut profile 另由专用 supervisor 隔离被测树，以随机 nonce 和稳定 PID/starttime 认证控制请求；它只在向 QEMU leader 发送 `SIGKILL`、回收跨 `setsid()` 的全部后代并取得一致镜像退出码后提交完成证明。supervisor/leader 被 workload 提前杀死、控制通道 EOF、残留后代、超时、非零退出或后置 panic 均失败。该 profile 建模突然 VM 终止，`SIGKILL` 不清空宿主页缓存，不能等同于整机物理断电。预算 checker、通用 runner 和生产 profile validator 的 fail-closed 自测集合以当前源码为准，不固化易过时数量。

科研平台 `exact-field-v1` receipt 以 128 B 分块流式读取完整文件，只接受唯一完整的目标 `key=value`；跨块长 key、目标和长无关字段合法，空 key/value、CR、NUL、重复目标及前后缀伪匹配 fail closed。完整 bytes/hash/line count 与字段断言一并进入 receipt，ASan/UBSan Host probe 接入 `local-check`。

Plain reference registry 按目标登记唯一 source owner，先剥除注释再严格校验真实调用及完整文件/记录 envelope；missing、unknown、duplicate、cross-owner prepublication 和 impersonation 都 fail closed。seeded program observation 还绑定 seeded profile、QEMU 日志和 `rp_orch_timing` 中 orchestrator/launcher/program 的顺序、数量、字节、哈希与名称摘要。AgentOS `rp_agentos_mainflow` 只给出 11 个唯一、完整、有序的未验证 telemetry stage；任何 Guest `runtime_verified` 记录都 fail closed。Host 从与提取清单一致的单层非链接目录读取 telemetry 和 11 个规范来源，逐 source 复验唯一 claim、预期成功状态、阶段字段及完整 bytes/hash。`host-platform-alignment.json` 保存逐来源明细，最终 bundle 保存对应原始文件并在离线验签时重算。

每侧 complete-state ZIP 只含 `extract-summary.json` 和其中精确列出的纯 Guest `rp_*` 普通文件，并显式拒绝 `rp_host_run_result`。Plain/AgentOS Host run receipt 分别作为 `dual-plain-host-run-result.state` 和 `dual-agentos-host-run-result.state` 独立保存，并以 `sha256-inventory-v1` 绑定 Guest 清单和内容；归档验证器和比较器都重算该摘要，同数量内容替换不能继续使用旧 receipt。离线验证安全解包，以 `min_common_files=240`、两份 receipt、seeded summary 与两份 Guest 日志重放 `compare_state()`，要求摘要逐字段一致，并核对 Mainflow、program ledger 和 backend 原始字节。普通 `dual-platform-run` 不打包 complete-state ZIP；只有最终采集启用的 `full-verify` evidence mode 才发布它们。当前候选的实际执行和发布边界只在 [正式证据索引](../../evidence/releases/INDEX.md) 记录。

只需要构建用户态测试程序时：

```bash
make agentos-user TOOLPREFIX=riscv64-linux-gnu-
```

## 专项测试入口

推荐直接运行：

```bash
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

校准、`local-e3` 正式证据采集和单 case 调试使用串行脚本入口：

```bash
bash scripts/run-agent-tests.sh
```

串行脚本按 `ci/kernel-budgets.json` 中 `agent_test_suite.expected_cases` 的顺序启动 QEMU。普通 Make 入口将同一套 case 分配给 `scripts/resource-jobs.py` 选定的并行 lane，并按规范顺序合并 Guest 日志和计时清单；并行结果不套用串行 suite 的 wall-time 阈值。case 成员、顺序和时长 profile 只以版本化配置为准。每项完整 marker 和负向条件由 runner validator 维护，本文不复制程序表或 marker 清单。

进入普通套件前，脚本以独立镜像运行 Context-sync/WAIT_ATOMIC `agentfinal_ucore` profile。它有独立 timing file，不计入 Agent suite 校准。裸 marker 只是测试合同的一部分，只有完整退出条件、canonical Guest 日志和同一冻结源码的 bundle 共同通过，才能支持发布结论。

`workflow_teardown_race_ucore` 是独立机制专项，不在 Agent case 清单中。`make workflow-teardown-race-test` 组合核对 lifecycle ABI、关闭与自然退出、PUBLIC lineage、Context/metadata waiter、阻塞 file 引用、I/O debt/cache、inode/account 回收和 generation 重用；结果仍以当前 release bundle 为准。

scope 回归核对：PUBLIC=0、SYSTEM=1、动态 workflow>=3，数值 2 是安装级 PUBLIC 存储 principal。权威 lifecycle ledger 固定 8 槽，key 为 `(id,generation)`；冻结期 ACTIVE+CLOSING+RETIRING 合计最多 4 个，目录彻底退休后才释放准入槽，身份槽随后才能以更高 generation 复用。`vfs_scope_refs[NPROC]` 只是 VFS 引用/清理记录。进程、线程、file object、block/inode、cache 和 Agent 状态页统一映射到 generation-safe EXEC/STORAGE account；每个 Agent 的 9 页 detail、2 页冷状态与 7 页 Context 映射以一次 18 页 `RESOURCE_AGENT_STATE_PAGE` 请求原子计费。`resource_domain_id` 只做 CPU 调度分区。其余 metadata/audit 与存储容量契约仍按对应 policy 文件核对。

Workflow 根把不复用的 `agent_control_id` 绑定到当次 `(lifecycle id,generation)`。根离开或 factory 关闭时先进入 CLOSING，再按完整 key 撤销；Agent/VFS 凭据已清零的 PUBLIC child/grandchild 仍必须终止。exec prepare/commit/abort 在同一发布边界复核 lifecycle。成员进入统一 teardown，自行清理 FD、inode、sidecar、VM 和 resource/I/O 账目；最后成员释放引用后才进入 RETIRING。`public_lineage=1` 和最终通过标记属于测试合同，发布结论以 C 对应 bundle 为准。

块 I/O policy ABI 使用稳定 owner/class、shared/device bucket 和 cache floor/cap。无竞争前台传输依次消费 reserved、shared；异域活动或排队时恢复保留份额，shared 永不带债。两级 reservation 原子预留并在真实 `disk_submit` 处结算，有界请求的 owner/device debt 在 checkpoint、退出/撤销 settlement 或 refill 中清偿。信用按 `last_refill_tick` 惰性补充，debt bitmap 只推进有债务的 lane；volatile overlay 命中不计物理传输。具体参数与静态 envelope 以 `io_policy.h` 为准。

buffer cache 以 exclusive holder、递归深度和私有等待队列串行化同块访问；持有 buffer 时 I/O/CPU checkpoint 均不能睡眠或 yield。复合文件系统原语另有 FS atomic depth；只有释放全部 buffer、且调用者已提交对象状态的 quiescent checkpoint 才可等待。loader 与 metadata exact-read 从正数短读前缀继续。PUBLIC 赞助对象接管使用固定工作区收集/排序块，按 qmap block 分组，并在唯一 claim gate 下完成 qmap-first、inode-last 前向提交。metadata COW 先验证新 primary 再更新旧 mirror；同步请求以 FIFO ticket 绑定不可替换 job，前台每批最多推进 8 个物理步骤并一次结算公平预算。续体未到期时 FIFO 队首在内核等待 timer 唤醒，不用用户态热重试；syscall 返回也不等同于双副本屏障完成。

scheduler 每轮在 idle context 安装 kernel trap 向量并短暂开启中断，再进入后台维护和线程选择。该机制为所有调度轮提供 timer/device 中断交付边界，防止唯一 runnable 线程在内核 pipe 条件路径反复 `yield()`、长期不返回用户态时锁死 I/O debt 与后台 token refill；`scheduler_interrupt_progress=1` 对此作动态回归。线程选择本身再分两级：外层 active-domain FIFO 严格轮转，内层才执行普通 FIFO 或 Agent 软评分；Agent/score burst 按域维护。

正常退出、主线程 fault、workflow revoke 和构造回滚共用 `LIVE -> REQUESTED -> QUIESCING -> DETACHED -> RECLAIMING -> SETTLING -> HANDOFF -> PUBLISHED -> RECYCLED`。进程的 Agent 侧清理只通过 phase-aware、幂等的 `agent_proc_teardown()` 推进：它负责撤销控制权、释放 Context/身份，并在 SETTLING 验证 Agent 私有状态与 Agent 页账为空。REQUESTED 后禁止发布新对象；唯一 teardown owner 继续由外层阶段结算通用 resource account 与 BIO owner 的在途请求/debt、清除 terminal 凭据和 lifecycle，scheduler 切回 idle stack 后才释放最后物理栈页。

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

syscall 公平性复测完全在 Guest 内用 pipe、进程和线程建立因果关系，宿主机只采集输出。它依次验证单次 64 KiB 控制台 `write()` 的 peer 进展、普通 inode 大写入的内核重调度计数、合法短写与 observer 进展，以及 `O_TRUNC` 的原子 EOF 发布和延迟回收：

```bash
make syscall-fairness-test TOOLPREFIX=riscv64-linux-gnu-
```

三个阶段都要求 `BEGIN < PEER < END`。inode 阶段用只读的 last-syscall 重调度计数证明前一个 syscall 内部跨过调度边界；truncate 阶段输出实测重调度次数，但不把它硬编码为零或非零，因为实现允许在常数时间 EOF detach 后按设备时序分批偿还回收工作。observer 只接受已提交 EOF。最外层父进程等待 worker 完整退出，runner 要求 QEMU 正常关机；这验证退出完整性而不夸大为退出清理公平性。增强目标和 baseline 使用同一测试契约。

物理页、metadata、观测证据与 VirtIO 的故障专项均由真实 Guest marker 判定，不以源码字符串扫描代替：

```bash
make physical-resource-test TOOLPREFIX=riscv64-linux-gnu-
make metadata-recovery-test TOOLPREFIX=riscv64-linux-gnu-
make observe-recovery-test TOOLPREFIX=riscv64-linux-gnu-
make virtio-disk-test TOOLPREFIX=riscv64-linux-gnu-
make fs-allocator-fault-test TOOLPREFIX=riscv64-linux-gnu-
```

`physical-resource` 在 tiny policy 下动态覆盖普通域隔离、系统保留、reserved promise 生命周期/公平、teardown 退款；仅 policy 非法组合与生产对象不得包含 test hook 两项使用编译期负向门。`metadata-recovery` 的每个新镜像先由 mkfs 安装并由 Host parser 验证 canonical 双 bank genesis，再对 primary/mirror 各八个 COW phase 动态确认 baseline、显式 arm 下一代事务并受控中止。powercut runner 只在完整 phase marker 后强制中止；case 随后还必须由 host 日志验证器确认 quiet baseline、armed/bound/fire/phase 的唯一有序链，以及一致的 scope、generation、token、job、bank 与 phase。normal kernel 启动前，host raw parser 要求至少一份完整有效 bank、无同代异 hash，并按 payload/header 发布边界只接受完整 baseline 或 updated。恢复后两 bank 必须相同且旧 scope 已清理。启动重探分别对全部 bank 和较新 bank 注入三轮 `BUSY/EIO/INTERRUPTED`，要求 fail-closed、动态 `retries=N`、连续 deferred attempt 与同启动恢复；超过 background burst 的 32-record bank 还对 `ABSENT/UNCOMMITTED/CORRUPT` peer 分别复测可恢复 cursor、terminal cache、selected confirm、catalog plan 和最终副本一致性，并在 seed Guest 内要求前台 scoped reload 经有限重启完成。单次暂态 header-flush EIO 继续要求显式不确定结果和副本修复。`observe-recovery` 先用 host probe 直接编译生产 durable owner，动态覆盖 SYSTEM sink=0、commit 后重新通知失败再成功、lifecycle 槽之外的系统保留 dirty 位，以及 section serial 耗尽时保留最后 pending 状态并拒绝新分配；容量状态机 probe 直接包含生产 `agent_observe_capacity.c`，动态覆盖四个普通槽和一个 Recovery 保留槽、sticky 槽类别、同 workflow admission/abort、跨 scope 隔离、serial/target/source-generation token 绑定、DONE admission race、REAP response 丢失后的同 token 重发、STATUS delivery failure 保留、scope/generation cookie 与单次消费、授权 reload 推进，以及同身份恢复幂等与冲突身份 fail closed。对应 production validator mutation 必须能拒绝弱 token、恢复 token 缺 generation、类别升级、admission 清除 DONE、reload 停滞、冲突恢复、scope/generation cookie 绕过、copyout 前消费和已擦除 scope lookup 先于 token 重发。第一启动在 audit/span/event/control/agent 和一个空闲 lifecycle 槽完成分配后的首个 kernel marker 立即 `SIGKILL`，且不允许先发生后续 audit/checkpoint。下一启动从同一镜像分配 successor，Host 必须逐字段证明五组 ID 严格增大、同一 lifecycle 槽 generation 严格增大；之后再完成 checkpoint、reap 双副本确认和最终擦除三阶段。receipt 回归要求初始 `PENDING` 明确不是证据，伪 id 为 `STALE`，当前 lifecycle 的 exact entry 经 active durable section 重读后才为 `DURABLE`，在持久前挤出 checkpoint 窗口的 entry 即使 scope target 已复制也必须为 `FAILED`；普通进程、Recovery 与 teardown 后旧 lifecycle 都不能查询成功。最后一阶段使用只存在于测试 profile 的注入点耗尽 event ID，动态断言 IPC 返回 `NO_SPACE`、队列长度不变且生产对象不含该 hook；timeline profile 通过真实 publish owner 连续推进两次 epoch，独立 exact marker 必须证明第一次触发 final retry、第二次在到期边界被有界 timeout 截止；并发 profile 还建立不同 filter/deadline 的两个 waiter，要求一次 Context 记录只定向唤醒匹配线程、另一线程独立超时且退出后 sidecar 全清。`virtio-disk` 动态核对 lost IRQ、delayed progress、descriptor pressure、设备 status error、flush-disabled、timeout reset 和 stuck reset 的有序请求 identity/tick/result。

checkpoint runner 的单次 `SIGTERM` 只发生在完整 marker 后，用来建立受控重启边界；metadata 与 observation powercut runner 则使用同一个认证 supervisor 首次发送 `SIGKILL`。metadata case 在恢复前保存 raw-bank 解析结果；observation case 在 boot2 启动前先由独立 Host verifier 解析原始 uCore 镜像中的双 bank、durable arena 和 observation section，复算所有格式与哈希层，并把 scope、lifecycle generation、agent 和 receipt 身份精确绑定到 boot1 marker，恢复后再比较 cut/successor 身份 marker。它们证明进程级突然中止边界，不声称复现整机物理断电。profile v7 保存每项 runner stdout 和逐次 canonical LF Guest 日志的合并产物，缺 Guest 日志即失败；allocator fault 还必须保存通过同一 verifier 复验的 `fs-allocator-evidence.tar`。

filesystem allocator fault profile 由每个 case 的实际 ELF 与 flat binary 配对构造镜像，mkfs 会复算 RX/RW 分段和 W^X 边界；缺失、错位或内容不一致会在 Guest 启动前 fail closed。三阶段 raw-image verifier 只允许目标 inode/dirent/bitmap/qmap、阶段 receipt 和固定映射的 metadata COW 数据区变化；metadata bank 的 inode、间接表和 owner map 仍必须逐字节稳定。被允许推进的双 bank 还会逐阶段复算 header、durable arena、record 和 payload hash，要求至少一份有效副本、同代不得分叉、双有效代数相同或相邻且跨阶段不得回退，避免把合法观测日志推进误报为 allocator 越界，也避免用宽泛掩码隐藏私有 journal 损坏。

根目录 AgentOS 仍使用 16 KiB stack + 4 KiB guard/canary，但只预建虚拟槽，物理页在线程 admission 时映射，在 scheduler handoff 后释放。32 MiB 是全部虚拟槽容量，8 MiB 是受信/保留线程物理池。`baseline_ucore/` 当前仍是固定物理栈实现，二者共享栈深/guard 行为目标而非 lazy mapping 实现。构建期调用图检查可单独执行：

```bash
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
make -C baseline_ucore kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`make full-verify` 的 `local-e3` Agent suite 先执行 `agent-test-policy` 并沿用串行时长门；`profile=none` 才使用资源自适应 Agent lane，并只检查 timing inventory。独立资源回归使用自适应 lane，双目标测量和文件系统 epoch 保持独占。每项 runner stdout、canonical Guest 日志和 allocator archive 都进入同一 C 对应的 release bundle。

## 覆盖关系

| 赛题任务 | 对应测试 |
| --- | --- |
| 任务一：Agent 进程与地址空间 | `agentfinal_ucore`、`agentscope_ucore`、`agentsecurity_ucore`、`agenttrust_ucore`、`usersafety_ucore` |
| 任务二：结构化工具调用 | `agentfinal_ucore`、`agentbench_ucore`、`agentsecurity_ucore` |
| 任务三：Context Path | `agentfinal_ucore`、`agentscope_ucore`、`agentsecurity_ucore`、`agentscan_ucore`、`labdemo_ucore` |
| 任务四：文件属性查询 | `agentfs_ucore`、`agentscope_ucore`、`agentscan_ucore`、`agentbench_ucore`、`agentconflict_ucore`、`agentvfs_ucore` |
| 任务五：Agent Loop | `agentloop_ucore`、`agentscope_ucore`、`agentsched_ucore`、`threadresource_ucore`、`agentbench_ucore`、`labdemo_ucore` |
| 任务六：综合场景 | `labdemo_ucore`、`make dual-platform-run` |
| 安全与稳健性复测 | `agentscope_ucore`、`agentsecurity_ucore`、`agenttrust_ucore`、`agentvfs_ucore`、`usersafety_ucore`、`blocking_semantics_ucore`、`make physical-resource-test`、`make metadata-recovery-test`、`make observe-recovery-test`、`make virtio-disk-test`、其余资源专项与 `make kernel-stack-check` |

## 性能数据说明

功能 marker、运行诊断和性能原始数据必须分开解释。当前唯一进入 provenance-bound 原始实验链路的是 `agentbench_ucore` 的文件查询 benchmark：

| 指标 | 含义 |
| --- | --- |
| `traversal` | 强制遍历 metadata catalog 的实际 operations、触达记录和未经补值的 Guest 微秒差值。 |
| `cold_index` | 包含索引重建成本的一次冷索引查询及重建记录数。 |
| `warm_index` | 索引已就绪后的多次查询；每次仍真实遍历候选链，不使用内核查询结果缓存。 |
| provenance 字段 | 来源 Guest 日志 SHA256、marker SHA256、行号、命令、commit 和 run id。 |

`agentbench_ucore` 还会输出 scalar/batch、digest cache、indexed dependency query 和 event wait/wake 等诊断 telemetry。这些值用于本轮调试和功能回归，但在获得同等级的来源绑定与重复测量前，不列为独立原始实验。

普通双目标运行在新建的私有随机 `$DUAL_LOG_DIR` 中生成以下 provenance-bound 原件，并以 `measurement-set.json` 标记完整 generation：

| 文件 | 内容 |
| --- | --- |
| `dual-targeted-agentbench-guest.log` | 定向运行 `agentbench_ucore` 的原始 Guest 输出。 |
| `measured-experiments.json` | 绑定 Guest 日志、命令、commit、run id 和逐行 marker 的 manifest。 |
| `file-query-benchmark.csv` | 当前唯一的 provenance-bound Guest 原始实验数据。 |

QEMU tick 会受到宿主机调度、终端输出和文件系统缓存影响，因此文件查询报告同时保留 operations 和实际触达记录数，并明确区分冷索引重建与热索引查询。Context/timeline、事件等待、并发写入、LLM Relay 和恢复流程目前只有动态功能证据，不宣称拥有独立 raw CSV 或性能曲线。

## 基础兼容抽测

AgentOS-uCore 保留代表性 uCore 基础 syscall 抽测。CHAPTER=3 下的 `ch3_trace` 应输出：

```text
Test trace OK!
```

可单独执行：

```bash
make ch3-trace-test TOOLPREFIX=riscv64-linux-gnu-
```

该入口用自适应 worker 构建用户程序和内核，但只启动一个使用私有磁盘副本的 QEMU。runner 除了要求唯一的完整完成行，还要求此前真实出现基础写路径输出；Guest 中的 syscall 计数、用户地址读取和写回断言全部完成后才会打印完成行。`make full-verify` 在 Agent suite 前执行同一入口，因此兼容性不再只靠源码或文档判断。开发日志写入 `build/ch3-trace/guest.log`；profile v7 将其规范化原始转录作为 `ch3-trace-guest.log` 独立交付并离线复验。远端不配置 Runner，也不把缺失的远端流水线当作本地动态执行的替代品。

普通进程消息接口由 `agentsecurity_ucore` 中的 `mail_basic=1` 覆盖。该抽测说明 AgentOS 扩展没有破坏代表性基础 syscall 路径。

## 结果产物

专项测试通过后，QEMU 日志保留在对应脚本输出目录。双目标运行通过后，结果主要位于：

```text
$DUAL_LOG_DIR/
evidence/releases/<bundle>/
```

`$DUAL_LOG_DIR` 保存本次调试 run 的 QEMU 日志、纯 Guest 状态、Host receipt、状态对照和文件查询测量；普通运行不生成 complete-state ZIP 或 HTML/SVG 预览。正式采集不复用该目录，而由 `full-verify` 在自己的私有 staging 目录重新运行，再发布与 clean、已提交 HEAD 绑定的 `evidence/releases/<bundle>/`。私有目录的 `measurement-set.json` 在移交前完成原子性验证；进入正式包后由 verification summary、逐文件 hash 和语义重放接管完成性。其中 `logs/raw/dual-{plain,agentos}-complete-state.zip` 保存完整 Guest 状态，`logs/raw/dual-{plain,agentos}-host-run-result.state` 保存独立 Host receipt，`logs/raw/dual-{targeted-agentbench-guest.log,measured-experiments.json,file-query-benchmark.csv}` 保存双目标原始测量，`metrics/file-query-benchmark.{csv,json}` 必须能回溯到 `logs/raw/agent-suite-guest.log`。

## 失败定位

| 现象 | 优先查看 |
| --- | --- |
| QEMU 长时间无输出 | `$DUAL_LOG_DIR/seeded-action-state/*/ucore-run.log` |
| 构建失败 | `make agentos-user` 或 `make agentos-build` 的编译输出 |
| 某个专项测试失败 | [要求追踪表](requirements-traceability.md)、对应 runner validator 与 Guest 日志 |
| 双目标状态不一致 | [../verification.md](../verification.md) 的双目标验证章节 |
| 文件查询测量缺失 | `$DUAL_LOG_DIR/{dual-targeted-agentbench-guest.log,measured-experiments.json,file-query-benchmark.csv,measurement-set.json}` |

## 发布判定

- Agent case、时长 profile 和内核增长阈值以 `ci/kernel-budgets.json` 为准；实验与竞赛 claim 以 `ci/evaluation-suite.json` 为准。
- 源码、镜像、运行段、`struct proc`、栈和 metadata 聚合数值必须来自同一冻结源码的 `make local-check` 日志与 bundle metrics，不能由文档常量或其他提交的结果代替。
- profile v7 聚合传统接口动态门、Agent suite、双目标与独立资源/恢复/故障 runner。动态是否通过只由 [正式证据索引](../../evidence/releases/INDEX.md) 指向的已验证 bundle 判定。
- 当前索引没有 release 记录，因此最终动态结果和性能数据均未发布；`remote_ci.status=not-attached` 不表示远端执行成功。

## 当前范围说明

| 方向 | 当前范围 |
| --- | --- |
| 文件扫描深度 | 自动扫描 uCore 根目录短文件名，文件对象 metadata 支持用户态显式写入和根目录自动发现。 |
| syscall 与 I/O 公平性覆盖 | 合同覆盖控制台、inode 写/截断、scheduler 中断交付、fault teardown 归因，以及 VirtIO 丢中断、延迟完成、描述符压力、status error、flush-disabled 与 timeout/stuck reset。metadata recovery 覆盖双 bank COW phase、raw-bank 校验、单副本降级和暂态 I/O 错误。动态完成状态只从发布 bundle 读取；永久设备故障、双 bank 同时损坏后的在线修复、更多 owner/class 组合和整机物理断电不在当前证据边界内。 |
| Agent 调度 | 验证 active resource domain 外层轮转、域内角色权重、受权调度配置、事件优先、deadline、heartbeat、wait cancel、虚拟运行量和 thread bomb 下的 victim 进展。 |
| Workflow 撤销 | `agentscope_ucore` 与独立 teardown race 的合同覆盖根/factory 关闭、阻塞低权限成员、PUBLIC lineage、Context/metadata waiter、阻塞 fdget、I/O debt/cache、inode/file/account 和 generation 重用。close 与 spawn/pending exec 的精确发布瞬间及多线程 controller 仍未专项注入。 |
| LLM Gateway | 内核提供结构化请求、响应事件、Context 和审计记录；Guest `rp_llm_relay` 使用确定性模板，云端模型接入不属于竞赛交付。 |
| 页面和图表 | 内核输出 `agentos:event`、timeline、audit 和 provenance，宿主机工具负责转成页面和图表。 |
| 性能数据 | 发布性能结论只接受 provenance-bound 的强制遍历/冷索引/热索引文件查询 benchmark；普通 tick、扫描数、候选数、轮询数、拒绝数和重建步骤只作当次诊断 telemetry。 |
