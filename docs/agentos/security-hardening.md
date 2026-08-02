# 安全加固与资源韧性设计

本文记录 AgentOS-uCore 在系统调用输入防护、同步、文件系统、调度、可信执行、文件访问和进程生命周期上的安全加固。目标不是为已知测试增加特判，而是建立可复用的内核机制，使普通进程或低权限 Agent 的错误和恶意输入不能停止内核、伪造权限或耗尽全局资源。进程、线程、文件对象和 Agent 状态页计入 generation-safe EXEC account，块、inode、cache 和 I/O 计入稳定主体对应的 STORAGE account；CPU 则独立按 active `resource_domain_id` 轮转。已覆盖路径上的资源不足通过可恢复错误与完整回滚保持系统继续运行。

## 1. 与赛题要求的关系

赛题要求把调度、资源配额和安全控制等核心机制放在内核，同时保持普通进程与 Agent 进程共存，并要求系统稳定、无内核 panic 或资源泄漏。本批加固因此属于 AgentOS-uCore 的内核工程基础和安全隔离创新，不改变工具调用、Context Path、文件查询和 Agent Loop 的用户态策略职责。

仓库中两个目标的职责如下：

| 目标 | 职责 | 安全机制范围 |
| --- | --- | --- |
| 根目录 AgentOS-uCore | 最终系统和赛题主目标 | 通用 uCore 安全加固 + Agent 身份、可信执行、能力模型、VFS 安全域和公平调度 |
| `baseline_ucore/` | 实验对照组，不是 AgentOS 系统本体 | 与主目标共享不依赖 AgentOS 的通用安全加固，但不包含 Agent syscall、Agent Context、Agent 文件 metadata 或 Agent 事件队列 |

因此 `baseline_ucore/` 应描述为“共享基础安全加固、不含 AgentOS 扩展的 uCore 对照目标”，不能再称为未修改的上游 uCore。共享加固使双目标在相同的安全和生命周期基底上比较，实验变量集中在 AgentOS 专属机制。

## 2. 设计原则

1. **预期失败返回错误。** 坏用户指针、非法长度、资源不足、权限不足和队列取消属于可恢复错误，不得进入 `panic()`。
2. **对象唤醒必须定向。** 等待者只挂入所等待对象的队列，释放或事件发生时只唤醒相关线程。
3. **权限来自内核绑定。** PID、父子关系、程序名、用户态自报 role/scope/span 都不能直接产生权限；授权必须同时绑定可信映像、内核角色/capability、active workflow scope 和精确对象 owner。
4. **资源按所有权回收。** 阻塞 syscall 临时引用、进程/线程、文件、存储、缓存和 I/O lease 统一绑定 generation-safe resource account，退出只经单一 teardown 状态机按依赖顺序释放。
5. **计费身份与调度分区分离。** EXEC/STORAGE account 决定配额与退款，`resource_domain_id` 只决定 CPU 外层公平轮转；持久存储按稳定 principal 累计，fork、降权、退出或重启都不能重置对应账本。
6. **策略保持可配置，硬性约束不可绕过。** orchestrator 可以调整 Agent 域内调度权重等策略，但不能关闭资源域轮转、普通进程的有限等待保证、可信映像校验或 VFS 能力检查。
7. **高频局部变化不能同步放大全局维护。** 数据路径只发布可立即查询的局部状态，昂贵的全局 checkpoint 由分域待办、固定合并窗口和后台频率上界统一调度。
8. **设备服务和缓存也服从稳定 owner。** 块 I/O 以持久主体/workflow 而不是 PID 归因，前台、控制和后台预算分离；cache floor、cap 和 retirement 使用同一 owner 生命周期，系统关键工作保留独立份额。
9. **观测查询也必须计费。** 计数查询和不复制结果的查询不能成为绕过调度预算的旁路；所有被检查的来源记录都按固定批次预付内核工作预算，可扩展集合通过有序索引或单遍归并读取。
10. **安全域必须有可信终止权。** workflow 的关闭权绑定创建时的唯一内核 controller，而不是角色、PID 或父子关系；撤销先使授权失效，再让成员沿正常 teardown 释放资源，不能从外部粗暴释放线程持有对象。
11. **安全身份必须抗复用。** workflow 和资源账户都使用不可变 `id/slot + generation` 句柄；槽可以回收，旧 generation 不能别名到新对象。
12. **增长必须有预算。** 模块所有权、源码与镜像体积、运行段、PCB、栈深/容量和完整套件耗时都由版本化 CI 门限约束；尚未运行的门禁不得写成通过。

## 3. 修复与机制总览

| 提交 | 风险 | 机制性修复 | 主要验证 |
| --- | --- | --- | --- |
| `599846b` | syscall 可直接解引用或越界使用用户输入 | 统一使用 `copyin()`、`copyout()`、`copyinstr()` 和长度上限；副作用前完成参数、数组、字符串和输出区校验 | `usersafety_ucore` |
| `0c9aa61` | 全局唤醒破坏无关等待队列 | 引入对象私有等待队列和定向唤醒，等待节点只由所属队列管理 | `usersafety_ucore`、`procreap_ucore` |
| `39b63e7` | inode、block、文件表耗尽触发 panic | 分配路径返回失败并回滚未提交状态；短写准确返回已经提交的前缀长度 | `fsenospc_ucore` 验证 inode/cache/block；`usersafety_ucore` 验证文件/描述符分配失败清理；全局 filepool 的域配额与压力验证见后续 `e67d1c0` |
| `caa30b4` | 深调用链或异常嵌套越过内核栈 | 每线程固定栈槽和 guard，构建时分析栈帧与调用图并强制预算 | `make kernel-stack-check` |
| `96ec4b9` | Agent 评分调度可永久饿死普通任务 | `AGENT_SCHED_MAX_AGENT_BURST` 强制类级公平上限，并保留 FIFO 逃生选择 | `agentsched_ucore` |
| `a08357a` / `672ffc0` | Agent 伪造内核系统事件 | 公共 `agent_wake()` 只投递普通消息；文件、定时器和 LLM 完成事件只能由专用内核路径产生 | `agentsecurity_ucore` |
| `1144924` | 孤儿僵尸无人回收并占满进程表 | 退出和再托管路径回收无人等待的退出对象，保持正常 `wait()` 语义 | `procreap_ucore` |
| `c36e7ab` | 普通进程自行创建全权限 Agent | 创建 grant 与业务 capability 分离，只有可信 bootstrap 和获授权 orchestrator 可委派角色 | `agentsecurity_ucore` |
| `acb8cd6` | 角色与可执行代码没有可信绑定 | 构建期清单写入不可变 inode 安全元数据，loader 把映像身份、角色上限、bootstrap 资格和 RX/RW+NX 布局绑定到进程 | `agenttrust_ucore` |
| `2d5b994` | 普通文件 syscall 绕过 Agent capability | inode 安全标签、`dev + inum + incarnation` 身份、进程 VFS 凭据和操作级检查共同保护 workflow 域；同 scope 继承 fd 在实际操作时重新校验，跨 scope fd 直接撤销 | `agentvfs_ucore` 动态验证跨 scope 撤销；同 scope 逐操作重校验由实现审计和既有文件能力用例共同覆盖 |
| `93d89ae` | 进程退出遗弃阻塞 syscall 中其他线程的临时资源 | 退出逐线程定向取消等待并等待同进程线程退出阻塞路径，再销毁共享地址空间、文件和同步对象 | `procreap_ucore` |
| `6362075` | 有父僵尸长期占用执行槽 | 将父进程可见的退出状态保存为独立 child record，执行槽可先释放，父进程仍可取得 `wait()` 结果 | `procreap_ucore` |
| `807b1e4` | 长存活 fork bomb 耗尽统一进程池 | 后代绑定不可变进程资源域，普通域有累计 live 配额，bootstrap/Agent worker 使用受控保留槽 | `procreap_ucore`、`procreap_agent_ucore` |
| `7d87f76` | PUBLIC 进程持久耗尽块和 inode，使工作流与内核元数据进入 ENOSPC | 逐块 owner map、inode owner、存储主体配额及 PUBLIC/WORKFLOW/SYSTEM 分级保留水位共同约束分配 | `fsquota_ucore` 两组配额/保留场景，`fsenospc_ucore` 双目标复测 |
| `ab246d4` | PUBLIC 进程用短命文件耗尽 Agent 全局版本表并阻断工作流编辑与摘要缓存 | 每个磁盘 inode 固定拥有一个版本 sidecar 槽；最终 `iput()` 回收同一生命期的版本、租约和摘要缓存，槽的可用性继承稳定存储主体的 inode 配额与分级保留量 | `fsquota_ucore` 跨越旧 512 槽上限的 640 次创建/删除循环，并验证 workflow 版本和内容缓存仍可用 |
| `0d9e50e` | `MESSAGE_SEND` 被错误扩大为全局等待取消权，低权限 Agent 可持续打断 Orchestrator 或 Recovery | 独立 `WAIT_CANCEL` capability、内核私有且不复用的 control id、同 active scope 和直接 controller 关系共同授权；消息路由与等待控制互不隐含 | `agentsecurity_ucore` 验证全部低权限角色取消父 orchestrator 均被拒绝，controller A 退出后 controller B 的新 control id 不继承旧取消权；control id 不复用机制保证 PID/PCB 槽复用不会扩权；`agentloop_ucore: wait_cancel=1` |
| `4f1bfaa` | `MESSAGE_SEND` 被解释为全局裸 PID 通道，低权限 Agent 可向 Recovery/Orchestrator 注入消息并耗尽关键事件队列 | stable control id 定向路由；接收方或受权控制者 grant/revoke；external/direct/attributed/source 三层配额，为显式内核 origin 保留至少 4 个容量名额；heartbeat intrinsic 单条合并；慢订阅者隔离和退出回收 | `agentsecurity_ucore` 已验证未授权拒绝、grant/revoke、target LLM_DONE consent、MESSAGE 位图隔离和 `ROUTE_MAX+2` 短命 source 槽回收；`agentloop_ucore` 验证 source=4、directed=8、external=12、第 13 条 external 拒绝、一条 heartbeat 使用保留容量且多周期不累积、消费后重接纳和 external 饱和 watcher 隔离。attributed=8、同一来源混合跨类及路由幂等/部分撤销仍缺独立输出 |
| `16d11aa` | Agent capability、对象表和 IPC 仍可被解释为所有 workflow 共享的全局权限 | syscall 541 由可信 factory 创建动态 scope；所有敏感对象使用 capability + active scope + stable owner；syscall 542 只一次性委派 pipe；最多 4 scope 并保留独立进程/存储/对象份额 | `agentscope_ucore` 的同名对象、动作、租约、IPC、audit、配额、fd 委派、事务竞争和回收断言已在完整 Agent 回归中通过 |
| `16d11aa`（审计） | 用户可伪造 span/cause，低权限遥测或委派 span 可挤掉关键审计效果 | private span owner 与 cause control sidecar；`context_push` 拒绝非零 cause/span；审计按 scope low/high 分区，只有内核确认的特权状态效果进入 high | `agentsecurity_ucore` 的 forged context、trusted cause attribution、audit authority partition 回归已通过 |
| `7ebe45e` | 长时间运行的 syscall 在内核态关闭中断期间越过用户态时间片，PUBLIC 进程可持续独占 CPU | 每次 dispatch 建立不可由 syscall 重置的周期 deadline 和工作额度；统一 begin/end、timer pending、resumed 检测和显式安全点约束长路径；fork 用 VM snapshot 屏障稳定源页表且逐页计费；inode 调度后短返回，truncate 先 detach 再以不可取消 cleanup checkpoint 回收；FD reservation 与文件槽快照保证让出期间的生命周期 | 该提交对应的历史双目标 `run-syscall-fairness-tests.sh` 覆盖控制台、inode 写、`O_TRUNC` last-syscall 计数、observer 和退出完整性；统一账户/lazy stack 后需重跑 |
| `89d412d` | PUBLIC 配额错误绑定短命进程资源域，完整退出后可换域或重启绕过累计上限；覆盖 SYSTEM 预装可变文件还可绕过新分配计费 | 独立 `storage_principal_id` 凭据；当前无 uid/tenant ABI，因此所有普通进程绑定安装级 PUBLIC principal 2；挂载清扫不可达 inode/块、从 qmap/dinode 重建账本并拒绝旧格式；PUBLIC 首次修改 SYSTEM 赞助对象前，以 qmap-first 顺序整体接管 inode 和已有块 | 双目标 `fspquota_ucore` 在同一镜像连续启动三次，覆盖打开后 unlink 的显式 SIGTERM checkpoint、14 块赞助对象接管、完整进程域退出、重启恢复、删除退款和新域再次受限；不宣称硬掉电注入 |
| `e67d1c0` | 全局文件对象表没有资源域边界；阻塞 syscall 可在关闭 FD 后继续固定临时引用并绕过每进程 FD 上限 | 唯一 filepool 槽按创建者资源域和 ordinary/reserved admission 类别计费；普通分配同时受每域上限和全局水位约束，内核受控工作保留独立容量；最后引用关闭才退款 | `make file-resource-test` 已以 64/48/16/16 配置在 AgentOS 和 baseline 双目标通过 |
| `1464c37` | 低权限 Agent 的微小文件写入同步放大全局 metadata 持久化并阻断其他 workflow | inode sidecar 即时发布、scope-local dirty/durable 代数、固定非滑动合并窗口、分块 COW bank 状态机和 scope-local 缓存失效共同解耦数据路径与 bank 提交；scanner 保留独立自适应 cooldown | `agentscope_ucore` 已验证至少 128 次单字节写只形成有界批次、另一 scope 完成 32 次查询，并在强制重载后保持最终一致 |
| `831823e` | 块设备队列和全局 buffer cache 没有持久主体/workflow 公平边界；内核态 yield loop 可长期屏蔽 timer/device 中断，fault 退出还可在 `freethread()` 前后丢失清理 I/O 账本 | 稳定 owner/class 的 lease/token/debt、排队 shared grant 轮转、普通流量设备根限速、SYSTEM/CONTROL 带债前进、每轮 scheduler idle kerneltrap 中断窗口、terminal cleanup I/O/kernel-work 上下文、按 sponsor 设置的 cache floor/cap 与 exclusive holder；FS atomic/quiescent checkpoint、grouped qmap claim、FIFO metadata submit lane 把跨预算生命周期纳入统一机制 | 最终修复后的独立 `iobudget_ucore` 输出八项具名机制标记和 `parent passed`，`elapsed=2.4s`；完整 Agent 16/16 的 `359.4s` 是 pipe 委派历史轮；`make fs-enospc-test` 的既有 75.1s 历史轮通过，设备错误、短 I/O、metadata COW 和 grouped claim 中点掉电仍缺动态注入 |
| `859ffe4` | 线程未计入资源域，PUBLIC 进程可用 thread bomb 扩大 CPU 竞争份额并耗尽线程槽 | admission 原子预扣主线程；额外线程按不可变资源域和 ordinary/reserved 类别计费，受域上限、普通全局水位和系统保留量约束；每类域上限必须严格小于对应全局水位；创建失败与退出统一退款；调度器先严格轮转 active 域，再执行域内 FIFO/Agent 软评分 | 该提交当时以 19/12/6/6/4 tiny policy 验证 12 项边界并通过；完整 Agent 16/16、默认构建、单独 `agentsched_ucore`、双目标进程回收/syscall 公平性/filepool 脚本也通过，当时尚未运行 `full-verify` |
| `c85b47a` | Recovery 可在 metadata 全局事务门内触发依赖派生图超线性重建 | 依赖表只保存 scope-local 显式边，兼容 mask 在既有单遍扫描内按需解析；`ACTION_COMMIT` / `RERUN_STAGE` 使用一次选集和一次原子提交；跨 Agent 预取只携带稳定端点并在预算让出后重校验 | `agentfs_ucore` 的 `metadata_action_bounded=1 field_driven=1 batched=1`、handoff 生命周期和 kernel-work preemption 标记 |
| `a24f68c` | 跨安全主体创建会自动传播 pipe 控制端点，低权限 Agent 还能继续转委派 | 一次性票据绑定发起线程的下一次主体创建，在不可让出区固定精确 file 对象并消费；子进程只得到端点、不继承票据，失败和 exec 撤销票据，未知继承类别默认拒绝 | `agentscope_ucore` 与 `agentvfs_ucore` 覆盖单跳 pipe 委派和跨 scope 隔离；exec 撤销目前由实现审计覆盖 |
| `a33092b` | audit/span/timeline/provenance 的计数和过滤路径反复扫描全局 512 槽，低权限 Agent 可在一次 syscall 内制造超线性 CPU 工作并绕过域级公平 | 每 workflow audit scope 维护 sequence 与 `(tick, sequence)` 两个 128 槽有序索引，淘汰统一执行 unlink/publish；ledger 窗口摘要直接读取索引状态；四类观测查询按单遍或四路归并读取，并在扫描前分量子预付、让出后重计和补足 kernel-work 预算 | 该提交对应的完整 Agent 16/16 通过；`agentscope_ucore` 验证索引顺序、低权限查询调度证据和跨 scope 进展，压力循环 12 次、查询内让出 64 次，另一 scope 的 32 次查询在 3ms 内完成 |
| 本次修复 | workflow 没有可信强制撤销，低权限成员可在根 Orchestrator 退出后继续持有 capability、进程槽、FD 和 scope 生命周期身份 | 生命周期账本绑定唯一根 `agent_control_id`；显式关闭或根离开将 ACTIVE 原子转为 CLOSING，统一撤销授权、拒绝发布并向 active/pending 成员提交协作退出，最后成员正常清理后才进入有界 RETIRING | `agentscope_ucore` 验证低权限/子 Orchestrator 拒绝、64 位 scope 校验、根自关、factory 关闭、阻塞成员清理、根自然退出、9 轮回收与 replacement admission |
| 当前架构收敛 | 降权为 PUBLIC 的后代可逃出按凭据扫描的 workflow 撤销，且可复用槽没有可信版本 | 独立 `workflow_lifecycle` ledger 为谱系签发不可变 `(id,generation)`；DROP、fork、exec 只转换凭据，不释放 lifecycle；撤销按完整 key 扫描，槽仅在彻底回收后以更高 generation 复用 | 历史 `agentscope_ucore` 专项约 `93.7s`，曾输出 `scope_controller_exit_revoke=1 public_lineage=1` 与 `parent passed`；后续发布是否保持该结果由对应 release bundle 判定 |
| 当前架构收敛 | 多套进程/线程/file/storage/I/O 账本重复、半发布和退出顺序难维护 | `resource_controller` 统一 EXEC/STORAGE account、ordinary/reserved、向量 reservation、usage reconcile 与 rate lease；单一 teardown 统一 exit/fault/revoke/rollback；lazy physical stack 与 Context sidecar 降低常驻体积 | 2026-07-25 对应版本的完整 Agent 套件连续三轮 16/16，proc-reap/syscall-fairness/file-resource/thread-resource/fs-enospc 专项全部通过；当前套件已扩展为 18 case，静态阈值以 `ci/kernel-budgets.json` 为准 |
| `75d0dfd` | teardown 跨资源竞争缺少组合证据，测试无法在不读取 PCB 的情况下确认精确 lifecycle generation | 新增 syscall 546 的版本化 self-only snapshot/compare ABI；`workflow_teardown_race_ucore` 在同一用例组合 factory close、自然退出、PUBLIC 谱系、Context/metadata waiter、阻塞 file 引用、I/O debt/cache、inode/account 回收和 generation 重用 | checkpoint 的 clean `make full-verify` 中独立连续三轮通过；八类有序 marker 与 `parent passed` 均由 profile validator 核对，不计入 16-case Agent 套件 |
| `0099c38` / `192f09e` / `ef0451c` | Reader action runner 把构建路径中的 `panic` 当成 Guest panic，QEMU 尚未启动便错误终止 | clean/build/guest 阶段化执行；构建只看退出码；guest 启动后才对规范化完整日志行匹配 panic/fault/check-failed；失败阶段写入 summary | `test_plain_ucore_action_runner.py` 同时验证 `build/riscv64/ch6b_panic` 不失败和规范 Guest `[PANIC ...]` 必须失败；checkpoint Reader E2E 通过 |
| 当前 metadata 拆分 | 单体对象实现难维护，且新增文件可能成为迁移代码或 BSS 以绕过单模块预算的手段 | metadata 拆为 transaction、file state、catalog、query、scan、directory、objects、actions、prefetch、store；catalog 集中持有 selector resolver，scanner 不再复制一遍全表路径查找；dependency/action/status undo 由 actions 持有，预取选择/handoff/snapshot 由 prefetch 持有；版本化 aggregate budget 同时限制 source、loaded text 和 BSS | `-Os` owner 由 Makefile 和预算清单逐项锁定，不以某两个文件作为例外白名单；本轮继续对 file state、prefetch 和持久化路径做行为保持减法。最终 source/text/BSS、栈和镜像指标只采用代码提交 C 的 canonical WARN 构建，当前工作树数字不作为发布结果 |

## 4. 用户输入与内核对象检查

系统调用层只把寄存器参数视为数值或用户虚拟地址。字符串、数组、结构体和输出缓冲区必须先通过 VM copy 接口访问，内核不能直接解引用用户地址。长度计算先检查上限和溢出，变长参数在分配文件、页、进程或写入磁盘之前完成验证。

文件描述符、inode、pipe 和同步对象通过稳定引用跨越可能睡眠的路径。失败路径按获取顺序的逆序释放引用，避免一次非法 syscall 消耗全局文件表或物理页。用户页在 syscall 执行期间失效时，copy 接口返回失败，不把用户缺页升级为内核异常。

相关实现集中在：

- `os/syscall.c`、`os/vm.c`：参数复制和地址范围检查；
- `os/file.c`、`os/pipe.c`：文件、pipe 临时引用生命周期；
- `os/loader.c`：`exec` 参数、映像布局和地址空间替换；
- `user/src/usersafety_ucore.c`：坏地址、超长参数、定向等待和失败事务复测。

## 5. 等待、退出、资源账户与调度域

### 5.1 定向等待和协作退出

`os/wait.c` 为 mutex、semaphore、condvar、进程等待、Agent event 和 timeline 等睡眠对象维护明确队列，只唤醒与状态变化相关的线程。线程取消从同一队列摘除节点，避免节点同时出现在运行队列和等待队列。pipe 当前仍以让出处理器的方式等待，但会检查进程退出请求并沿正常 syscall 清理路径释放临时引用。对象等待队列只解决“何时重新运行”；Agent event 的“由谁消费、copyout 失败后是否仍可见”另由 6.3 的 reserve-cookie-commit/abort 协议保证，二者不能互相替代。

所有进程级终止原因使用同一正向状态机：`LIVE -> REQUESTED -> QUIESCING -> DETACHED -> RECLAIMING -> SETTLING -> HANDOFF -> PUBLISHED -> RECYCLED`。正常 `exit()`、主线程 fault、workflow revoke 和未发布构造回滚只选择入口，不各自发明释放顺序。Agent 侧进程析构只公开 phase-aware、幂等的 `agent_proc_teardown()`：QUIESCING 撤销控制权，RECLAIMING 释放 Context 状态并清除身份，SETTLING 断言 Agent 私有状态为空；调用方不能再手工组合原始 Context/metadata 清理入口。状态机只有一个 `teardown_owner_tid`，第一次退出码生效；进入 REQUESTED 后不能发布新进程所有对象。QUIESCING/DETACHED 让 sibling 从可中断等待展开并分离 child/FD，RECLAIMING 释放文件、Context 状态和 VM，SETTLING 结算 cleanup I/O 与 kernel-work，HANDOFF 清除凭据并释放 workflow lifecycle；scheduler 在 idle stack 上完成 PUBLISHED/RECYCLED，避免当前线程释放自己仍在使用的内核栈。

### 5.2 执行槽与等待凭据分离

子进程退出结果保存在父进程私有的 `child_record` 中。父进程未调用 `wait()` 时，记录仍保留退出 pid 和状态，但已退出进程的执行槽、内核栈、页表和文件引用可以先释放。每个父进程的待取结果有固定容量，恶意父进程只能耗尽自己的记录配额。

### 5.3 通用资源账户与系统保留

`os/resource_controller.[ch]` 是资源计费唯一事实源。账户句柄为 `{slot,generation}`，种类分为 EXEC 与 STORAGE，状态为 FREE/ACTIVE/CLOSING/DRAINING；资源种类覆盖 PROCESS、THREAD、FILE_OBJECT、FS_BLOCK、FS_INODE、BUFFER_CACHE 和 AGENT_STATE_PAGE。每项 charge 还区分 ordinary/reserved，账户上限、普通全局水位、系统保留和总容量在同一次 admission 中检查。成员、durable usage、pending reservation 或 rate lease/debt 尚未归零时，CLOSING/DRAINING 账户不能复用。

`resource_reserve_many()` 把复合资源视为一个向量：进程与主线程、pipe 两端等要么全部预留并提交，要么完整取消，避免半接纳对象。挂载恢复使用 `resource_reconcile_usage()` 将 qmap/dinode 事实写回 STORAGE account；对象所有权变化使用 transfer，不能通过退出或句柄槽复用刷新账本。普通 fork 后代继承 EXEC account，一个账户的进程上限和全局 ordinary 水位限制长存活 fork bomb；可信 boot/workflow admission 只能通过内核授予 reserved class。

### 5.4 线程资源域与域级公平调度

线程和进程都保存同一个 EXEC `resource_account`，但 PROCESS/THREAD 是独立 resource kind。进程 admission 以一个向量原子预留 process + t0；之后每个 `thread_create()` 只增加 THREAD usage。创建用户栈、trapframe 或内核栈失败时取消 reservation，只有线程真正不可运行后才退款。ordinary/reserved 上限仍由策略配置映射到控制器，错误配置不能让单账户吃完整个类别。

`resource_domain_id` 不再表示计费身份，它只是 scheduler partition index。运行队列外层 active-domain FIFO 保证每个有可运行线程的调度域每轮只出现一次，内层才执行 FIFO 或 Agent 软评分。因此一个 EXEC account 的线程数、角色权重和事件积压都不能放大跨域 CPU 份额；计费账户生命周期和调度分区策略也不再相互绑死。

每线程 16 KiB 内核栈虚拟槽、4 KiB guard 和 canary 保持不变，但物理页在 admission 时由 `kernel_stack_acquire()` 按需映射，并在 scheduler 已切到 idle stack 后由 `kernel_stack_release_inactive()` 释放。全部槽的 32 MiB 是虚拟容量；启动时只为受信/保留线程维护 8 MiB 物理栈保留池，普通 live stack 来自通用分配器。当前是单 hart 本地 TLB fence；未来 SMP 必须补远端 shootdown。

### 5.5 全局文件对象表配额与系统保留

全局 filepool 以“唯一 `struct file` 槽”计费，而不是以进程 FD 或引用次数计费。`struct file` 保存创建者 EXEC account handle 与 ordinary/reserved class；`filealloc()` 在发布槽前预留 FILE_OBJECT，最后引用关闭才退款。阻塞 syscall 固定已有对象只增加引用，不新增计费，也不能靠关闭原 FD 逃离账户上限。

fork 继承和阻塞 syscall 的 `filedup()` 只共享已有 file object，不产生第二份配额。最后一次 `fileclose()` 先复制本地清理快照，原子清空槽并向原 resource account 退款，再在临界区外完成可能让出的 inode 或 pipe 清理。generation handle 防止账户槽复用把迟到退款记到新主体。

pipe 创建先预留两个进程 FD，再用一个资源向量预留两个 FILE_OBJECT；任一阶段失败都取消同一个 reservation 并释放 FD。该通用控制器目前只在根目录 AgentOS 目标实现；`baseline_ucore/` 仍保留旧计数路径。两侧专项可验证相同的“隐藏引用仍计费、失败完整回滚、保留区可进展”行为契约，但不能据此声称实现共享。

### 5.6 文件系统存储主体与分级保留量

进程表中的 `resource_domain_id` 只是短命、可复用的 CPU 调度分区索引，不能作为资源计费或持久磁盘身份。进程/线程/file object 计入 EXEC resource account；文件系统则用独立的 `storage_principal_id` 找到 STORAGE account。当前 uCore 没有 uid、登录会话或租户 ABI，因此所有普通进程都绑定安装级匿名 PUBLIC principal `2`，退出、重新 fork/exec 或重启都不会换一个配额身份。workflow 仍按内核签发的 scope 计费，但动态 scope 从 `3` 开始；`0`、`1` 分别保留给 VFS PUBLIC/SYSTEM 语义，`2` 只作为稳定 PUBLIC 存储主体，不是可创建的 workflow scope。

磁盘格式在 bitmap 后保存逐块 owner map，并在 inode 中保存存储 owner 和格式版本。数据块、间接索引块和目录扩容块按实际分配主体写入 owner；`truncate`、`unlink`、失败写回滚和 inode 回收再按持久 owner 精确退款。由 mkfs 或可信维护路径以 SYSTEM 计费但允许 PUBLIC 修改的文件，在第一次 PUBLIC 写入或截断前必须整体接管。内核在固定 `MAXFILE + 1` 工作区收集直接块、间接索引块及其数据块，排序后按 `QBLOCK` 分组，使每个 qmap block 在一轮中只读写一次；可睡眠 claim gate 串行化同类操作，但它是 wake-all 重检，不宣称 FIFO。预检可中断且每次 checkpoint 前都已释放 buffer；随后一次预留全部 PUBLIC 配额，按 qmap-first、inode-last 进入不可回滚的前向提交，cleanup checkpoint 即使收到退出请求也继续完成。挂载会识别 SYSTEM inode 下已有部分 PUBLIC qmap 的中间状态，并沿相同方向完成接管。这样覆盖已有块同样不能绕过配额，也不会把半接管对象误记为 SYSTEM。VFS 凭据同时携带对象授权所需的 scope/capability 和计费所需的 storage principal，两者职责分离。

分配水位分为三层：PUBLIC principal 必须同时留下所有 admitted/future workflow 和 SYSTEM 剩余量；某个 workflow 只能在自己的 scope 配额内使用共享 workflow 水位，并必须留下其他 scope 尚未消费的保证；内核维护路径和受信任 SYSTEM 可以消耗自己的系统信用，但仍须兑现所有 admitted/future workflow 的最低保证。容量算法由 `fs_storage_policy.h` 在 mkfs 和内核间共享：以完成镜像后的真实空闲量为输入，workflow 总保证最多使用扣除 SYSTEM 后余量的四分之三，并设置每 scope 320 inode/512 block、SYSTEM 8 inode/512 block 的显式硬下限。计算出的 policy version、scope 数、PUBLIC principal、G/S 和 checksum 持久化在 superblock；内核重启固定使用 G，只从 `free-4G` 恢复尚未消耗的 SYSTEM 信用，避免把合法消耗的 S 再预留一次。当前平台镜像核算结果为每 scope 342 inode/1195 block、SYSTEM 64 inode/512 block；workflow inode 账户直接采用该 STORAGE domain limit。metadata catalog 仍是每 scope 112 条的独立有界索引，其中 AUTOSCAN 最多 96 条并为显式 metadata 保留 16 条；它不再充当 inode backing lease。PUBLIC 和每个 WORKFLOW scope 的块/inode 仍分别累计到稳定 owner 上限。

挂载时会校验 superblock 容量契约及 `inode -> bitmap -> owner map -> data` 的完整布局。内核先从扁平根目录建立 inode 与数据块可达集合：无目录引用的已分配 inode、以及没有任何可达 inode 引用的 bitmap 块会在计费前清扫；悬空目录项、重复块、越界块或引用空闲块则视为损坏并拒绝启动。该恢复算法的机制目标是处理 `balloc` 尚未挂接映射、truncate 已分离映射但尚未回收、或打开文件 unlink 后突然终止的中间状态，避免永久 PUBLIC 配额泄漏；现有动态证据只对 open-unlink 在完整 marker 后执行受控 `SIGTERM` checkpoint，不能外推为任意窗口或整机物理断电试验。随后分别扫描 qmap 与 dinode，从持久 owner 重建 PUBLIC 已用 block/inode 数和 workflow scope 下界；账本不依赖任何进程仍然存活。第一次核算只服务于恢复，先回收没有持久恢复令牌的旧 workflow boot lease；第二次核算才要求空闲量至少覆盖持久化的 `4G`，避免合法的旧 scope 残留在回收前把系统误判为不可启动。新 workflow admission 与分配使用同一关中断临界区检查实际剩余保证。mkfs 在安装完全部可信程序后要求初始空闲量同时覆盖 `S+4G`，不能兑现时拒绝出镜像；主机 mkfs 每次生成镜像前都按当次 `FS_*` 参数重编，避免配置与内核漂移。此次同步提升容量策略、owner 和 superblock 契约版本；旧镜像没有稳定 PUBLIC principal，内核会明确拒绝而不是以零账本继续挂载。已分配块缺少 owner、已分配 inode owner/version 无效或 checksum 错误同样拒绝启动。当前教学文件系统没有日志，挂载清扫只恢复资源可达性和配额不变量，不宣称文件内容更新具备完整事务原子性。

### 5.7 Agent 文件版本 sidecar 生命周期

编辑版本和内容版本不再分别从两个“先到先得”的全局池分配。内核按 `inum` 直接索引覆盖全部磁盘 inode 的统一 sidecar，并同时校验 `dev + inum + incarnation + storage owner + VFS policy`。一个存活 inode 只能占用自己的槽，PUBLIC 文件不能通过反复创建新 incarnation 占走其他 inode 的版本位置；PUBLIC、WORKFLOW 和 SYSTEM 可获得多少版本状态，因而由同一套稳定存储主体 inode 配额与分级保留水位决定，不再维护一套容易漂移的平行配额。

删除目录项不是文件生命期终点，因为仍可能有打开的描述符继续访问 inode。实际清理挂在 `iput()` 的最终回收分支：只有链接已删除、最后引用释放且 `itrunc()` 成功后，才在清除 inode 身份之前原子移除该 incarnation 的编辑版本、内容版本、活动租约和 digest cache。新 incarnation 首次触达同一槽时还会执行防御性旧状态清理；仅持有旧 `dev + inum + incarnation` 的提交或租约过期路径只能查找，不能重建已经死亡的版本状态。

### 5.8 块 I/O 速率预算与 buffer cache 保留

块设备服务使用与持久存储一致的 STORAGE account：安装级 PUBLIC、SYSTEM，以及每个内核签发的 active 或正在清理的 workflow。BIO 的 owner/class bucket、设备根 bucket、lease 与 debt 由资源控制器的 rate lane/global pool/bundle lease 承载；syscall 在进入可能触盘的实现前捕获账户与 class。PUBLIC 使用 `NORMAL`，workflow 的 Orchestrator/Recovery 使用 `CONTROL`，其他 workflow 使用 `NORMAL`；metadata、scanner 和 scope reclaim 显式建立 SYSTEM 或触发 workflow 的 `BACKGROUND` job。

每个 owner/class 有受保护的 burst/refill bucket。PUBLIC NORMAL 为 32/16；每个 active workflow 的 NORMAL/CONTROL/BACKGROUND 为 24/12、48/24、8/4；每个 retiring workflow 只保留 BACKGROUND 8/4；SYSTEM SYSTEM/BACKGROUND 为 96/48、16/8；前台 shared 为 32/16。请求先租 owner/shared 信用，并尽量租设备根信用；只有真实 VirtIO 1 KiB 传输完成时才提交。首个完成提交已有 lease，后续完成继续消费 token，超额分别形成 owner debt 和 device debt。没有触盘的请求退款，线程退出和等待取消清理未提交 lease。admission 与 debt 使用分离的对象私有队列；同一 bucket 只把排队信用交给 FIFO 队首。存在排队者时，shared grant 再按 owner/class cursor 轮转；没有任何 admission waiter 的 fast path 可直接借 shared，因此实现和测试均不声称“所有 shared grant 都经过 round-robin”。`BACKGROUND` 不能借 shared。

设备根 bucket 的 burst/refill 为 560/280，但它不是所有流量的硬聚合上限。PUBLIC、workflow `NORMAL` 和其他非保护流量必须取得根信用，并在 device debt 清零前等待；SYSTEM owner、`CONTROL` 和 `SYSTEM` class 在根信用耗尽时仍可使用自己的 owner/class 保留预算前进，每个完成仍增加 device debt，后续 refill 先偿债。编译期断言保守地为 8 槽 lifecycle ledger 的全部 cleanup bucket 核对 PUBLIC、SYSTEM、shared 与 workflow class 的 burst/refill；这是静态数组 envelope，不表示运行时可同时 admission 8 个 workflow。冻结期 ACTIVE/CLOSING/RETIRING 合计最多 4 个，保护流量的带债进展也不构成运行时硬总上限。

只有 token/debt 账本还不够：如果唯一 runnable 线程反复在内核态 pipe 条件路径 `yield()`，旧 scheduler 可能一直在关中断状态重新调度同一线程，使负责 refill 和设备完成的 pending timer/device interrupt 没有交付机会。现在每轮选择线程前都把执行身份切到 idle context，安装 kernel trap 向量并短暂打开中断，随后再关中断进入后台维护和原有调度选择。这是所有调度轮次共享的机制边界，不依赖 PID、文件名或 syscall 特判。

正常 `exit()`、主线程 page fault/非法指令、workflow revoke 和未发布构造回滚共用 5.1 的 teardown 状态机；非主 sibling 仍只经 `thread_exit_current()` 回收自身。SETTLING 阶段建立 cleanup kernel-work 与 I/O request，使 fileclose、未链接 inode 回收等真实 I/O 继续按原 STORAGE account/class 归因；随后提交残余 lease 并结算 owner/class debt。HANDOFF 阶段调用 `vfs_proc_terminal_clear()` 清除凭据，再由 `vfs_proc_lifecycle_release()` 释放不可变 lifecycle 引用；scheduler 已切到 idle stack 后才释放最后物理栈页。`proc-reap`、`agentscope_ucore` 和 `iobudget_ucore` 定义了该机制的具名动态合同；历史 16-case 结果不能替代后续发布绑定的 18-case bundle。`fault_exit_cleanup=1` 仍只是 fault 分支的具名证据，不能单独代表状态机所有内部阶段。

buffer cache 为每个 buffer 记录稳定 sponsor。256 个 buffer 中，SYSTEM、PUBLIC、每个 active workflow 的 floor/cap 分别为 40/96、24/48、36/64。cap 是稳态驻留边界而非瞬时硬占用上限：必要的 transient buffer 可暂时越界，但在最后引用释放时立即失效。替换只会使用 invalid/dead owner、调用者自身或高于自身 floor 的 donor；跨 sponsor 命中不会给原 sponsor 刷新 LRU，新分配数据块由 `bclaim()` 转到实际 owner，而共享文件系统 metadata 不会因读者访问被偷换 sponsor。scope quiesce 后不再保留 36/64 active 分区；仅当轮转 reaper 正在执行该 owner 的清理 job 时提供 3/8 临时 floor/cap。后台 job 继续使用触发它的 SYSTEM/workflow owner，不把多个 workflow 混入一个全局 background cache 分区。这里的“隔离”是缓存容量和服务公平，不是每 owner 复制一份数据，也不是保密边界。

同一 `dev + blockno` 始终复用一个 buffer，并以 exclusive `holder + hold_depth + holder_waiters` 串行化。进程持有任一 buffer 时，I/O checkpoint 只能 deferred，CPU 工作 checkpoint 也不能 yield。`readi()` / `writei()` 还以 `bio_fs_atomic_enter/leave()` 标记复合文件系统原语；普通 checkpoint 在原子段内延后，只有调用者已释放全部 buffer 且自行保证 inode/目录状态已提交时，才可使用 quiescent checkpoint 睡眠。内核动态验证“不持 buffer”，而“状态已提交”是调用者契约。qmap claim、truncate 和退役清理进入不可回滚阶段后使用 cleanup 变体，退出请求不能中断其有界前向提交。文件读写由此可在块边界返回正数短 I/O；loader 和 metadata exact-read helper 在原子段外偿还预算并从已提交 offset 续读。

权威 workflow lifecycle ledger 位于 `workflow_lifecycle.[ch]`，固定 8 槽，key 为不可变 `(id,generation)`，状态为 FREE/ACTIVE/CLOSING/RETIRING。冻结期 ACTIVE+CLOSING+RETIRING 合计最多 4 个；槽只有在最后成员离开、catalog retirement 与 I/O/cache 清理完成后才回到 FREE，并在下次 admission 取得更高 generation。generation 耗尽时拒绝复用，旧 key 永远不能别名到新 workflow。`vfs_scope_refs[NPROC]` 只是 VFS 引用/清理记录，不是生命周期身份账本。

CLOSING 已撤销用户授权并拒绝新对象/成员，但在成员完成自身 teardown 前仍作为完整 STORAGE/I/O/cache owner 驻留。最后成员释放 lifecycle 引用后才进入 RETIRING，只保留 `BACKGROUND` 预算来完成 namespace、inode 和 detached block 清理。owner/account 状态直到 request、waiter、lease、debt 和 durable usage 全部归零才释放。scope 文件清理使用 namespace detach、inode detach、逐块 reclaim 的有界前向协议，不依赖 PID 或 syscall 特判。

## 6. 可信 Agent 权限链

Agent 权限由以下链路共同决定：

```text
构建期执行清单
  -> 文件系统镜像中的不可变 inode 安全元数据
  -> loader 校验并绑定进程映像身份
  -> bootstrap / role grant
  -> 内核 agent_role 与 capability mask
  -> Agent syscall、工具和 VFS 操作授权
```

`user/include/exec_policy_manifest.h` 是构建期策略的单一来源，`nfs/fs.c` 在生成镜像时消费清单并写入 inode，`os/loader.c` 和 `os/exec_policy.c` 在执行时验证绑定。这里的可信根是构建期清单、不可变 inode 元数据及其身份和版本绑定，不是密码学签名。受控 flat image 的代码区映射为 RX，数据、bss、用户栈和 Agent Context 映射为 RW+NX，清单中的 `exec_rw_offset` 同时约束镜像生成与装载。`EXEC_MANIFEST_F_BOOTSTRAP` 只允许内核加载的可信初始程序建立根 Agent；普通 fork 不复制 bootstrap grant，运行中的普通 exec 会按新映像上限收缩文件权限并清空 bootstrap grant，不会重新获得启动授权。orchestrator 的委派范围也受当前映像 role mask 限制，不能创建清单未授权的角色。

进程映像安装不是用“是否为当前进程”临时猜测入口。`proc_install_user_image()` 必须显式接收 `PROC_IMAGE_INSTALL_BOOTSTRAP` 或 `PROC_IMAGE_INSTALL_LIVE_EXEC`，并在关中断发布区先调用 `proc_image_install_state_valid_locked()`：bootstrap 的 `threads[0]` 必须是非当前 `T_UNUSED/tid=-1/identity_generation=0` 槽，所有 sibling 也必须保持同一未使用身份；live exec 必须由当前 `RUNNING/tid=0` 且 generation 非零的主线程发起，sibling 只能已退出或未使用；两种模式都要求线程归属正确且不在运行队列。只有该状态、不处于 teardown、同步对象和 VFS transition 全部通过验证后，内核才提交不可逆 credential 变化，随后以进程级 `agent_process_image_install_locked()` 清除所有线程的 IPC wait/deadline/loop 暂态，再重置同步状态并交换 VM。这样 bootstrap 构造和 live exec 共享一个可审查的提交机制，却不会把尚未启动的 PCB 当成正在执行的进程。

### 6.1 注册新的 Agent 或 worker 程序

新增程序时必须显式完成以下步骤，不能依赖 PID、父 PID 或运行时文件名特判：

1. 在 `user/src/` 添加程序，并确认它进入 `user/Makefile` 对应的 `AGENT_TESTS`、平台列表或自定义 `CHAPTER`。
2. 程序需要作为 bootstrap 或保留 Agent 角色时，在 `EXEC_POLICY_ENTRIES` 中增加一行，分别声明源程序、镜像别名、信任 flags、允许创建的 role mask、launcher 使用的 launch role 和 VFS profile。`launch_role` 只供用户态 launcher 选择启动方式，不是内核授权来源。
3. 只有需要作为 `INIT_PROC` 建立根 Agent 的程序才使用 `EXEC_MANIFEST_F_BOOT_SEALED`。需要由 Agent 角色执行的映像使用 `EXEC_MANIFEST_F_SEALED` 和最小 role mask；`agent_create_role()` 创建时先受当前可信映像的 role mask 约束，子进程后续 `exec` 时再校验目标映像是否允许其角色。
4. 非 Agent workflow worker 不要求 `TRUSTED` role-image 标志。mkfs 为布局有效的程序自动生成 immutable、domain-safe worker 别名和 workflow VFS profile；`agent_worker_create()` 再把请求能力精确绑定到该别名 inode。按最小权限请求 `CONTENT_READ` / `ARTIFACT_WRITE` 的非空子集，VFS profile 只是能力上限。
5. 重新生成用户程序和 `nfs/fs.img`。Agent 角色映像由 bootstrap/orchestrator 按清单启动；普通 worker 由 orchestrator 对 mkfs 生成的 worker 别名调用 `agent_worker_create()`，不能用普通 `fork()` 代替委派。
6. 运行 `agenttrust_ucore`、`agentsecurity_ucore` 和 `agentvfs_ucore`，确认可信启动成功、未授权派生失败、映像不可修改且文件域权限未扩大。

清单中的源名只在构建镜像和选择别名时使用；运行时权限来自已经写入 inode 并由 loader 校验的安全元数据，而不是对进程名做白名单判断。把相同程序字节复制到普通文件只会得到新的 public inode，不会复制可信、不可变或角色许可属性。

### 6.2 等待取消控制关系

`MESSAGE_SEND` 是 Agent 间数据面能力，不能作为停止另一个 Agent 等待的控制凭据。内核为等待取消定义独立的 `AGENT_CAP_WAIT_CANCEL`；角色策略当前只把该能力授予 orchestrator，但 syscall 只检查 capability，不对角色名称做特判。

能力只回答“能否执行取消操作”，对象关系继续回答“能取消谁”。每个 Agent 创建时取得一个内核私有、单调且不回绕复用的 64 位 `agent_control_id`，目标同时记录直接创建者的 `agent_controller_id`。取消仅在两者匹配时允许，因此低权限子 Agent 不能向上取消 orchestrator，具备取消能力的兄弟或后来接管同一 PCB 槽的进程也不能横向取得旧控制权。普通 fork 不复制这些字段，Agent exec 保留原控制关系，退出回收会清零字段；创建者退出后旧 controller id 失效，未来若需要接管必须新增显式授权转移，而不能从 reparent、PID 或资源域自动推断。

目标查找、控制关系检查、重复令牌检查、令牌写入和等待队列唤醒在同一个关中断临界区内完成。用户字符串复制在临界区外完成；正在退出的目标按不存在处理。这既避免半写令牌和检查后复用，也不在临界区内执行可能睡眠的用户内存访问。

### 6.3 可信消息路由与事件资源隔离

`MESSAGE_SEND` 只证明主体能够发起数据面操作，不再授予“向任意 PID 发送”的全局对象权限。跨 Agent `MESSAGE` 和 `LLM_DONE` 默认拒绝，必须先通过 `agent_route_config(source_pid, target_pid, event_mask, operation)` 建立定向路由。调用者、source 和 target 必须属于同一 active workflow scope；target 凭 `WATCH` 自主接受也不能越过此边界。非 target 调用者还必须具备 `ROUTE_MANAGE`，且 source/target 都是调用者自身或其直接创建的 Agent。共同父进程、角色、capability、PID 或 resource domain 都不是跨 scope 通信权。

路由表保存在 target PCB 中，每项使用 source 的内核私有、单调且不复用的 64 位 `agent_control_id`，并单独记录允许的 `MESSAGE` / `LLM_DONE` 位图。PID 只在 syscall 当次用于定位对象；source/target 解析、存活状态、控制关系、route grant/revoke 和投递鉴权都在关中断临界区中完成。source 退出时内核遍历删除所有对应入站项，target 退出时清空自己的路由表。grant/revoke 幂等，revoke 只影响之后的入队，已通过授权并进入 FIFO 的事件不会被追溯删除。由此 PCB 槽或 PID 复用、reparent 和角色变化都不能复活旧通道。

所有执行直接投递的数据面入口复用同一交付函数：`agent_wake()`、`send_message`、非零 target `llm_request` 和 `llm_response` 均先核对 stable route，再检查 watch、队列预算并入队；`llm_request(target=0)` 只记录摘要。兼容 mailbox 只在成功入队后于同一短临界区更新。metadata 预取交接改用 `slot + pid + control_id + scope` 稳定端点句柄：预算化 prepare 不保留 PCB 指针，发布前重新解析全部身份并在固定上界原子段写本地 ring、span bus 和审计；目标退出或槽复用时交接被安全丢弃。未授权、目标不存在、watch 不匹配或资源不足都不会留下旁路副作用。`LLM_RELAY` 仍负责“谁可产生 LLM 结果”，route 负责“结果可发给谁”，两层授权缺一不可。

目标事件队列仍是 16 槽 FIFO，但每个槽用内核私有 accounting flags 编码 origin/resource class，并保存 stable source control id。资源边界分三层：所有带 Agent 来源的 external 事件合计最多 12 条；directed IPC（`MESSAGE` / `LLM_DONE`）和 attributed notification（`FILE_STATUS` / `JOB_DONE` / `POLICY_DENIED` / `CONTEXT_LIMIT` / `DASHBOARD_EXPORT`）各自最多 8 条；同一个 stable source 跨两类合计最多 4 条。用户可见 `source_pid` 不参与配额身份判断，攻击者不能换事件类型或利用 PID 复用刷新额度。

external=12 的 admission 上限为显式 `KERNEL` origin 保留至少 4 个容量名额。heartbeat TIMER 由内核专用的 `INTRINSIC_COALESCED` delivery policy 产生，越过 external 和 watch 过滤，并用私有槽标志限制为同类型最多一条 pending；用户 syscall 不能选择 origin 或 delivery policy，也不能用相同 payload 冒充该路径。带 Agent source 的文件状态、作业完成和拒绝通知即使事件类型属于系统通知，也仍按 attributed external 计费，不能侵占保留量。所有 origin 最终仍受 16 槽总容量约束。`LLM_DONE` 虽由 `LLM_RELAY` 专用工具产生，但它是定向 IPC，既需要 route，也计入 directed/external/source 三组配额。

`agent_wait()` 采用 reserve-copyout-commit 交付协议，而不是在找到事件时立即出队。内核以 event-id cookie 原子保留精确队首事件或 cancel token，释放短临界区后完成用户 copyout 和 Context attribution，再重新核对 cookie 后提交消费、配额退款及下一 waiter handoff。copyout、lane 或归因失败会 abort 保留并定向唤醒等待者，事件/cancel 继续可见；同一槽同时只能有一个消费者。静态与 mutation 合同覆盖该状态机；“reserve 后用户页失效 + sibling waiter/cancel/teardown”四路 Guest 组合仍是显式验收边界，只有 release bundle 实际包含对应 profile 日志时才能称为动态覆盖。

历史 `mailread/mailwrite` 只保留为 PUBLIC 兼容通道，不是 Agent 路由旁路。Agent 端点始终拒绝。无 workflow controller 的普通 PUBLIC 也不再构成全局通道：两端必须属于同一个 ACTIVE、slot+generation 完全一致的 EXEC account，并持有非零端点代数；携带 workflow lifecycle 的降权 PUBLIC 还必须同时命中同一 ACTIVE `(id,generation)`、同一动态 scope 和同一个非零 OPEN controller control id。缺失、陈旧或跨账户、lifecycle、scope 的 lineage 均拒绝。每个新进程和每次 PUBLIC exec 都轮换 endpoint generation，旧 PID、旧 PCB 槽或 exec 前队列不能授权新端点。

邮箱存储由 `agent_ipc` 独占为目标进程按需分配的两页 sidecar：一页 16x256 payload、一页元数据，使用目标 EXEC account 和 admission class 计入 `RESOURCE_PHYSICAL_PAGE`，因此发送者不能把内存账目转嫁给另一个主体。空队列读取不分配；首次合法发送以同一关中断临界区完成授权、两页分配、失败回滚、发布和入队；队列排空后保留到 teardown，最终在 EXEC account 释放前幂等退还两页。`mailread` 使用 reserve/copyout/commit 两阶段事务，用户页校验或 `copyout` 失败只撤销保留，不提前移动队首。需要跨安全域协调的测试和控制路径使用显式一次性委派 pipe，不再借裸 PID mail。

广播系统事件逐 watcher 独立尝试。某个慢订阅者的 external admission 已满、总队列已满、watch 不匹配或已经退出时，内核继续投递后续目标；文件 metadata 等权威状态一旦提交，不会因为一个通知接收者资源不足而错误返回 `NO_SPACE`。这种 best-effort 通知语义把控制状态提交与观察者背压分离，避免单个低权限 Agent 阻断全局工作流。

当前路由授权表是 scope-local 内核私有执行状态，尚无 snapshot/query ABI，grant/revoke 也不追加 Context 或 audit。现有审计只能观察授权之后同 scope 的事件入队和消费，不能完整重建路由策略变更历史。若后续需要运维审计，应新增只读快照和受权的 route-change audit record，而不是把用户日志当作权威控制面记录。

### 6.4 Workflow scope factory 与对象所有权

scope 编号由内核定义：0 是 PUBLIC，1 是只读可信 SYSTEM，3 及以上是动态 workflow；数值 2 保留给稳定 PUBLIC 存储 principal，不是 workflow scope。只有非 Agent、具有 resource-domain admin 且仍运行可信 bootstrap 映像的 factory 可以调用 syscall 541 `agent_workflow_create(role)` 建立新 scope。普通 role grant 只允许在当前 scope 内调用 `agent_create_role()`，即使 orchestrator 有全部业务 capability，也不能用它铸造新对象域和新配额。

同 scope Agent/worker 继承 VFS scope，但不是同一个安全主体；workflow 或可信 bootstrap 动态 scope 的降权普通 fork 会清除 Agent/VFS 凭据，却仍通过不可变 lifecycle key 留在原 workflow 的终止谱系。pipe 不按环境状态自动传播：syscall 542 `agent_scope_delegate_fd(fd)` 只接受 pipe，并把一次性票据绑定到调用线程的下一次安全主体创建。成功子进程只获得端点、不获得票据；失败与 exec 都撤销票据。普通 PUBLIC 父子只有在本来就不属于 workflow lifecycle 时才保持普通 POSIX 继承语义。

敏感对象身份按类型组合 scope 和 stable owner：文件/metadata/租约以 `scope + dev + inum + incarnation` 为基础，action/dependency/cache 先按 scope 分区，IPC/wait control 使用同 scope stable control id，span/audit/prefetch 使用 scope + 公开 span + 私有 span owner/cause principal。capability 只在当前 scope active 且对象 owner 精确匹配时生效；SYSTEM 仅在显式只读路径可见。

新 workflow 根在发布为 runnable 前取得不复用的 `agent_control_id`，并绑定当次 admission 的 `(workflow_lifecycle_id,generation)`。只有仍持有该根标记且 control id/lifecycle key 一致的进程，或仍运行可信 bootstrap 映像的 factory，才能调用 syscall 545。后创建的 Orchestrator、低权限 Agent、同 PID/PCB 槽或单独的 `ORCHESTRATE` capability 都不获得关闭权。control id 不复用；lifecycle 槽可回收但 generation 必须增长，两者不要混为一个身份。

syscall 546 只允许进程取得自己的 lifecycle/runtime 快照或比较自己的 expected key。它没有 PID、scope 或任意 ledger 查询参数，并采用版本化 sized-prefix copyout；坏指针、非法 flags/key 在写输出前失败。合法 `STALE/NOT_FOUND` 可以同时返回 self snapshot，便于测试识别重用而不扩大权限。`(id,generation)` 仍只是不可变身份/比较值，不能替代根 control id、factory authority、capability 或对象 owner。

显式关闭与根进程正常退出、fault 退出及 terminal credential clear 走同一个幂等 controller-departure 路径。ACTIVE 原子转换为 CLOSING 后形成不可逆的授权与发布屏障；scope acquire、storage reserve、spawn 和 pending exec commit 均拒绝。成员扫描匹配不可变 lifecycle key，而不是可清除的当前凭据，故 PUBLIC 降权子孙不能逃逸。扫描调用统一 teardown request，不覆盖已经取得 `teardown_owner_tid` 的进程；成员在自身上下文关闭 FD、释放 inode/VM/sidecar、结算 resource account 与 I/O lease/debt。exec 的 `prepare/commit/abort` 在发布边界再次核对 lifecycle 状态。

最后成员释放引用后才进入 RETIRING 并撤销 active cache floor。ledger 在该 generation 的退休工作完成前保留 key；轮转 reaper 使用 BACKGROUND 预算清理 metadata、dependency、action history、edit lease/version、query/digest cache、audit、span prefetch、IPC 和普通文件，再释放 STORAGE account 与槽。下一 workflow 即使复用相同 slot/id，也具有更高 generation，不能重新解释旧状态。

### 6.5 审计分区和可信因果

物理审计表 512 槽按最多 4 个 workflow 各保证 128 条，每 scope 分成 low 64 和 high 64。Context/event/sched/prefetch/manual 等遥测始终 low；每个 active stable principal 在 low 中保证 8 条，其他份额空闲时可借用到 16 条。low 满且新主体到达时先回收已离开主体，再只回收其他主体高于 8 条的借用溢出，并继续沿既有因果 victim 规则选择记录；任何 active 主体的 8 条保证不会被借用者挤走。causal victim 的 scratch 同样覆盖完整 16 条 burst，因此第 9 到 16 条中的重复 span/kind 也会参与冗余判断，不能因旧 8 槽辅助上限而隐藏。只有 syscall/tool 成功后由内核确认的特权状态效果进入 high；high 依据每 scope 8 个保留进程份额为每 active principal 保留 8 条。high 满时只滚动当前 principal 自己的记录或回收已退出/inactive principal 的最旧记录，绝不牺牲另一 active principal 的 protected evidence。inactive 历史仍是有界窗口，回收量通过 `dropped_records` 可见。

公开 `span_id` 和 `cause_sequence` 是呈现字段，不是权限票据。内核为每个 Context/event 保存私有 span owner、source control 和 source pid；`context_push()` 要求用户输入的 cause/span 都为0，再由内核连接当前链。每 scope 独立维护 ledger hash，但 sequence 在系统中单调递增，因此当前窗口允许因跨 scope 写入、low/high 滚动和 principal 滚动而稀疏。`dropped_records=total-visible` 解释窗口外前驱，不能要求所有相邻可见 sequence 都直接 hash 相连。

持久观测格式已经升级到 checkpoint v7。每个 scope 的 8 个槽固定由 latest tail 4 条和最多 4 个 causal diversity anchor 组成，anchor 按 identity class、kind、stable principal 与可信 span 选择，再与 tail 按 sequence 排序；这是一条显式稀疏链，不是“最新连续后缀”。磁盘 entry sidecar 保存 `identity_class`、`link_flags`、`principal`、`span_owner` 和 `receipt_id`，`PREV_RETAINED` 仅在相邻保留项确为直接 hash 前驱时置位。scope 的 `admission_drops` 记录 sequence/hash 分配前的准入拒绝；成功建链但未被持久窗口选中的数量另由计数关系推导，公共 `dropped_records` 仍聚合两类缺失。

恢复先验证完整 v7 checkpoint 的 header、保留零值、scope/entry、sidecar 组合、全局 sequence/receipt 唯一性、链间隙、链尾和全部 lease 高水位，再为每个空 live scope 预检槽位，并在同一关中断窗口发布索引、计数与 receipt；中途失败回滚本轮插入，已有 live 证据不被 reload 覆盖。durable store 以 active generation replication fence 区分“primary 已发布”和“双 bank 已复制”：绑定覆写目标时在 `INVALIDATE` 前撤销，repair 与 fail-closed 同样清零，boot 只在双 bank 一致时恢复，mirror `COMMIT` 验证后才发布新 generation。live sidecar 已淘汰的 `target == 0` receipt 在精确 entry 扫描前和 generation 二次确认后各检查一次该 fence，禁止 primary-only 记录误报 `DURABLE`。REAP 的授权和擦除仍使用两阶段状态机，但其控制写通过通用 durable `URGENT` flag 和 store `expedite` 把到期时间提前，retry 也复用同一 notify 路径。普通 receipt 继续以 flags=0 使用既有 serial fence 和合并窗口，不能借 REAP 之名把低权限观测写放大成全局紧急 I/O。

每个 scope 同时维护按 `sequence` 和按 `(tick, sequence)` 排列的两个 128 槽索引。新记录发布和旧槽淘汰都通过统一 unlink/publish 路径更新两份索引，ledger 的 `visible/oldest/latest` 因而可在 O(1) 时间得到，无需重扫物理 512 槽。audit/span/provenance 沿 sequence 索引单遍扫描，timeline 对 Context、sched、audit 和 prefetch 四个有序来源做四路归并，不再为每条输出重新选择全表最小项。需要扫描的计数查询按候选扫描上界计费；每 16 条换算一批预算，单次 checkpoint 不超过一个工作量子，并在每次让出后重计来源、补足增长差额。等待匹配的查询只在最后一次预算让出完成且来源上界已经被覆盖后采样 `scan_epoch`；后续未命中扫描到 waiter 发布之间，再以关中断的 epoch 重检和 keyed 入队闭合，因此预算公平点不会制造丢失唤醒。

SCHED 记录的 ring 归 timeline owner，而不是调度 core。core 只构造采样；观测 facade 先检查线程级 suppression，再按“ring commit -> epoch advance/定向 wake -> audit publish”的顺序发布。这样被抑制的内部持久化工作不会留下可查询 SCHED 记录或推进其 epoch，也不会出现 ring 已可见而等待者仍依赖旧 epoch 的状态。Observe v51 曾完成定向本地运行，但它早于 checkpoint v7，只能作为历史回归线索；v7 的动态等级仍须由最终提交绑定的 release bundle 决定。实现不增加公共查询 ABI，也不向低权限调用者暴露其他 scope 的观测负载。

## 7. 文件安全域

VFS 使用 PUBLIC=0、SYSTEM=1 和多个动态 workflow>=3，而不是一个所有 Agent 共享的 workflow 域；数值 2 保留为稳定 PUBLIC 存储 principal。进程凭据由可执行映像 profile、Agent role capability、kernel-issued scope 和受控 exec 委派共同得到。文件资源以 `scope + dev + inum + incarnation` 标识；同名文件和复用 inode 均不会继承另一 scope/生命期的 metadata、租约或缓存。`open/read/write/truncate/unlink` 等真实数据路径同时检查 inode scope 和当前有效能力，因此普通 syscall、预先打开的 fd 或相同 capability 不能跨 workflow。

主要规则如下：

| 主体 | PUBLIC | SYSTEM | 动态 workflow |
| --- | --- | --- | --- |
| 普通 public 进程 | 保留普通文件数据访问 | 仅允许显式执行查找，不获得对象数据权限 | 数据操作拒绝 |
| workflow Agent/worker | 与 public 数据隔离 | 只读执行/共享策略按明确操作开放 | 只访问与自身 scope 精确相等的对象，并继续检查 read/write capability |
| 内核维护路径 | 只执行明确代办操作，分配仍按原主体计费 | 可维护可信映像和私有 metadata | 可在 retirement 中按目标 scope 回收 |

`exec` 是数据隔离的显式例外：内核可查找布局有效的 SYSTEM 映像。普通进程仅执行该映像不会得到 workflow scope；worker 必须匹配 `agent_worker_create()` 预先绑定的 inode和父 scope，Agent 还必须通过可信 role-image 校验。pipe 只能由创建该安全主体时消费的一次性 fd 票据携带，不由 scope 继承或 exec 隐式扩大。

inode 标签带布局版本和一致性校验值。该校验用于发现格式或状态不一致，不是 MAC 或密码学防篡改。创建路径生成标签，装载和访问路径校验标签；未知或损坏标签按拒绝处理。可信可执行映像同时设置 immutable 标志，普通 `write`、`O_TRUNC` 和 `unlink` 不能改变整个映像文件。

## 8. 调度与资源耗尽硬限制

调度器首先按 active `resource_domain_id` 做不可配置的严格轮转，一个域无论有一个还是许多可运行线程，每轮都只取得一次外层选择机会。进入选中域后，Agent 评分仍可使用角色权重、priority、budget、事件、deadline、heartbeat 和虚拟运行量表达策略；`AGENT_SCHED_MAX_AGENT_BURST` 及 FIFO escape 只限制该域内的软评分选择。评分策略无法覆盖外层域公平边界，也不能借 thread bomb 增加跨域 CPU 份额。

类级公平只能约束两次调度之间选谁，不能约束一次 syscall 在内核中运行多久。`os/kernel_work.c` 因此为每个线程维护内核工作域：调度器在每次 dispatch 时重置一个 tick 的周期 deadline 和 1024 单位工作额度，syscall 的统一 begin/end 只维护嵌套深度，不刷新预算。timer 在内核态到期只设置 pending，不在中断、virtio 或持有临时对象的临界路径直接切换；下一安全点按 pending、deadline 或工作额度调用正常 `yield()`。线程在未退出 syscall 时重新 dispatch 会留下 resumed 标记，下一 checkpoint 把“已经发生调度”明确返回给调用者，避免调用者在同一次恢复后继续无界推进。syscall 返回前也执行统一检查，因此连续短 syscall 不能靠重复陷入刷新时间片。

安全点只放在可提交边界：控制台按已输出的 64 字节、pipe 按已发布的传输块、exec 和 fork 按已完整处理的页面、`agent_run()` 按已完成的 operation、普通 FD_INODE 读写按单个块边界计费。fork 在开始逐页复制前建立进程级 VM snapshot 屏障，调度器暂缓同进程非 owner 线程，同时继续调度其他进程；失败回滚也使用不可取消 cleanup checkpoint。inode 路径在更新共享文件 offset、一次性内容版本和 Agent size 发布 sidecar 后才检查；sidecar 按 inode incarnation 非阻塞发布，查询和 metadata bank 快照都会覆盖事务表中的旧 size，持久化完成时再用 sequence 判断是否仍有并发更新。这样 metadata 事务忙或写线程退出也不会把已提交 size 隐藏在旧记录后。一旦 checkpoint 报告发生过调度，inode 路径就以合法短读或短写返回已提交前缀，让用户态重新发起操作，而不是跨调度继续使用旧 offset。新增长循环必须声明工作单位和原子提交边界并接入同一 checkpoint，不能自行判断 PID、角色或 syscall 编号。

`O_TRUNC`、unlink 后的最终 `iput()` 和最后一次 `close()` 还涉及可跨调度的资源生命周期。truncate 先从 inode 原子 detach 被丢弃的直接/间接映射并提交新 size，再从私有 reclaim 描述中分批释放块；cleanup checkpoint 即使进程已经收到退出请求也继续完成这批无主资源的回收。`fileclose()` 在最后引用消失时先把类型、inode、pipe 和资源域计费信息复制到本地快照，原子发布空闲全局文件槽并退款，之后不再访问可能已被复用的槽。`fileopen()` 则先占用不可读写、不可继承的进程 FD reservation，待 `O_TRUNC` 回收和文件初始化全部完成才安装真实文件，失败时统一释放 reservation。这些规则在 AgentOS 与 baseline 通用路径保持一致。

当前专项验证覆盖上述可扩展数据、装载和回收路径，但不宣称已经穷尽任意 syscall。目录 scanner 仍有固定的每轮条目上限；metadata raw I/O 和 scope 文件清理已经改为可恢复 background step，并同时服从内核工作 checkpoint 与块 I/O debt checkpoint。后续新增可扩展循环仍必须声明原子提交边界，不能把分域 I/O admission 当作替代 CPU 安全点的机制。

文件系统对 inode、inode cache 和数据块耗尽返回失败，回滚未提交状态并准确报告短写；稳定存储 principal 配额和分级水位进一步保证 PUBLIC 压力不能触及 workflow/system 保留量。Agent 文件版本 sidecar 与 inode 槽及其最终回收绑定，因此短命文件也不能绕开存储主体边界耗尽独立的内核版本池。`fsquota_ucore` 验证同一运行中的 PUBLIC 压力、释放复用及 workflow/system 保留量；双目标 `fspquota_ucore` 的既有 crash/seed/verify 轮验证持久计费与 qmap-first 恢复，但本次 grouped claim 尚未做中途掉电注入。

metadata 后端采用 generation + payload hash 的双 bank 提交：目标 bank 先发布无效 header，再只写变化的 payload segment；变化段逐段读回比较、整体摘要一致后才发布 header，header 回读一致后才切换 active generation。新的 primary 完整验证后，状态机才允许用同一不可变快照覆盖旧 bank 作为 mirror；因此 primary 验证前保留旧代，mirror 阶段失败则仍保留已验证的新 primary，不宣称任意故障下两个历史代都完整。逻辑缩短复用已有高水位块，不同步 truncate。payload hash 只作为一致性摘要，不是抵抗恶意磁盘篡改的密码学认证。

可信 metadata 在用户态发布前完成加载尝试和可信判定：mkfs 通过内核、构建器和 Host probe 共用的纯磁盘 ABI，预装两个字节一致、完整预分配且标记为 `KERNEL_PRIVATE/SYSTEM` 的 v7 generation-1 canonical 空 bank；`main()` 在 `fsinit()`、`timer_init()` 后立即运行 `agent_storage_init()`，随后才启动运行时 I/O policy 并加载首个用户程序。运行时不从“没有 authority”推断首次启动；稳定状态没有任何有效 v5/v7 bank，包括双 `ABSENT`、双 `UNCOMMITTED`、双损坏或其混合，均 fail closed。只要存在一份完整有效 bank，另一副本仍可按既有恢复协议修复。后台读取使用绑定 authority cookie、bank、inode identity/size 和首部字段的可恢复 cursor；每轮只推进 SYSTEM `BACKGROUND` I/O 预算内的前缀，最终重读首部并验证 payload。已经确定的 terminal bank 分类会缓存，候选 bank 仍须再次完整确认；确认时 generation/hash/migration、inode 或首部不一致都按确定损坏关闭，不会用无界 `INTERRUPTED` 重试掩盖竞态。双 bank 暂时 `BUSY/EIO/INTERRUPTED` 时 Agent admission 和持久身份租约保持 fail closed，但 timer 只在指数退避 deadline 到期时发布一次后台工作。真实 I/O 失败增加退避计数，已经取得前缀或 catalog prepare 进展的 `PROGRESS` 只在下一 tick 续跑，不增加失败次数。静态 seed 的信任边界是受控 mkfs 与普通进程不可访问的 raw disk/KERNEL_PRIVATE 路径，不是密码学签名；旧的双空 bank 镜像必须重建或离线迁移。

这里的 checksum、header/payload hash 和 inode 标签校验只用于格式、一致性和意外损坏检测，都不是 MAC，也不证明镜像供应链可信。metadata genesis 的 authority 来自受控 mkfs 把 canonical bank 直接写入 raw image 并赋予普通进程不可访问的 `KERNEL_PRIVATE/SYSTEM` 标签；若构建器或 raw image 本身不可信，这些摘要不能提供攻击者篡改防护。

持久发布边界也不以一次块写完成为准。文件系统的前向状态转换在声明持久后必须通过 `fs_durable_barrier()`，由块层向已协商 `VIRTIO_BLK_F_FLUSH` 的设备提交 flush；设备不支持 flush、flush 失败或发布结果无法确定时，路径返回不确定状态或使相应 authority fail closed，不能把 volatile cache 当成已提交。动态 powercut profile 只能在这些 device-flush/durable-barrier 语义上，用认证 supervisor 对稳定 QEMU leader 发出一次 `SIGKILL` 并验证后续重启恢复。它不刷新或模拟物理控制器缓存，不等价于整机突然断电，也不覆盖永久介质故障。

验证后的原始 bank image 在一次候选 epoch 内保持只读；catalog prepare 只修改非 active bank shadow 中的私有 plan，按固定记录步长续跑，并绑定 candidate epoch、catalog generation、参数、指针和内容 hash。plan 完成并取得 catalog mutation fence 后才恢复单调 identifier floor；此后在 fence owner 内执行不再分配的投影，再安装 active shadow 和 generation。若已有 foreign fence，apply 在改写前返回 `INTERRUPTED` 以便重试，不能笼统称为“prepare 后绝不失败”。零身份的普通持久记录进入 derived quarantine，自动扫描或 SYSTEM 记录进入 pending reconciliation；普通查询看不到两类记录，scanner 使用专用只读视图单次协调，避免在加载门内执行 `records * directory` 查找。前台 `agent_file_meta_init()` 使用非可恢复候选：块读取在释放 FS atomic/buffer 后等待 I/O debt，scope prepare 在固定 1024 项上界内单次完成，因此大 bank 也必须在一次 syscall 内成功或返回真实错误，不跨 syscall 留下 raw buffer/confirmed cache。重探不会创建 bank 或安装空表；同一启动内取得已验证快照、完成投影和必要副本修复后才开放 Agent 创建和查询。确认损坏仍永久 fail closed。scope retirement 则保留一条不依赖损坏 bank 内容的 VFS 清理路径：依赖、动作、缓存、审计、租约等可见内存状态和真实 VFS-labelled 文件仍按 scope 回收，完成后释放生命周期身份。

metadata 内存表、索引、inode sidecar 和持久化由同一可睡眠事务门保护。进程请求采用单调 ticket FIFO：无排队者时可直接取得门；一旦领取 ticket 就不可中断地等到自己的 turn，若期间收到退出请求，也必须先取得并立即传递事务门，避免遗弃 ticket 阻塞全局。最外层释放只唤醒 FIFO 队首。真实 VFS callback 仍不能插队；scheduler 则可在门恰好空闲时取得一个硬有界维护轮次，且该保留轮次解锁时不会重复唤醒已由前任唤醒的 serving ticket，因此既不破坏 ticket 与睡眠队列的对应，也不会被持续进程流量永久饿死。进程态查询、索引、显式依赖、action 与预取等可扩展扫描按 128 records 向 `kernel_work_checkpoint_cleanup()` 计费；scheduler 只执行每轮最多 16 个目录项或一个持久化状态机步骤。依赖表只保存每 scope 有配额上限的显式用户边；文件 `dependency_mask` 是兼容的规范输入，由查询、action 与预取在既有固定表遍历中按需解析。结构变化只推进依赖代数，纯状态 action 不触碰该代数，从机制上删除了 Recovery 可触发的门内超线性派生重建。`ACTION_COMMIT` / `RERUN_STAGE` 先生成固定选集，再一次更新每个槽、一次维护 status 索引和一次登记写回；查询预取使用精确 hit slot、一次文件表扫描、目标位图去重和 8 条发布上限。跨 Agent handoff 进一步把不受信任生命周期的 PCB 指针替换为稳定端点句柄，所有可能触发 checkpoint 的扫描只用局部副本，固定表扫描先计费、端点再校验、最后无调度提交，消除了依赖重建放大、按目标重复放大和槽复用污染三类路径。

每个进程的 Context 写路径另有可睡眠、FIFO、可重入的 commit lane。sequence 接纳、工具执行、Context header/record 发布、Context syscall、IPC 状态记录、文件查询和 wait 归因都在同一 lane 中按序提交；若操作还需要 metadata，唯一锁序是 `lane -> metadata`，最终离开 lane 时断言没有遗留 metadata transaction。`agent_call_count` 统计已接纳并预留的调用序号，因此在途慢调用可使它暂时领先；`latest_sequence` 只推进到已经完整写入 Context 的提交水位。该机制解决并发调用的顺序/哈希一致性，而不是按工具 ID 加特判。

可能替换物理 COW job 的同步 set/delete/init/reload 仍另进入单调 ticket 的 FIFO submit lane。条件失败检查、事务门释放和 submit queue 入队期间保持中断关闭，消除 unlock-to-sleep 丢唤醒窗口；reload wait 使用同一协议。submit ticket 同样不能在退出时放弃。同步 catalog mutation 还取得绑定 `agent_metadata_txn_token()` 的 owner fence，并跨持久化 checkpoint 释放/重取 metadata gate 保持；所有权威写入口对 foreign writer 返回冲突或重试。undo token 绑定 fence token、slot、诊断 generation 和完整 post-record，恢复前重新检查 exact post-state、中央容量与唯一键。fence 只串行化写者，读取仍可观察到持久化期间的暂态 post-state，因此它不是 opacity 事务；越过不可逆 COW 边界后返回 `INDETERMINATE`，不能伪装成已经回滚，rollback 不变量失败则运行时 fail closed。持久化跨预算等待时 immutable job 保持同一 `job_id`，后来提交者不能替换。VFS callback 与 scheduler 只做非阻塞尝试，不能在持有底层文件系统状态时形成反向等待。

metadata set 允许自动创建真实 workflow 文件时，创建来源不是一个布尔猜测，而是 `existing`、`created`、`FS_CREATE_INDETERMINATE` 三态。只有 `created` 才把精确 receipt `(path, scope, dev, inum, incarnation)` 记入 catalog undo；回滚通过 `fs_rollback_created_workflow()` 重新查找并逐项核对同一目录项、scope、设备、inode 号和 incarnation，同时要求对象尚未绑定 metadata 且允许 unlink，绝不按文件名删除后来替换的对象。目录项发布或 durable barrier 之后无法确认结果时，`FS_CREATE_INDETERMINATE` 必须一路转换为 `AGENT_STATUS_INDETERMINATE` 并使 metadata runtime fail closed；它不能按“未创建”处理，也不能用普通 undo 伪造成功回滚。

低权限 workflow Agent 的普通文件 create/write/truncate/delete 也不再同步调用完整 metadata bank 持久化。所有 `agent_meta_slot/flags/version` 变化统一通过 `agent_file_state_set_index()` 校验、`iupdate()` 并在失败时恢复旧值；write/sync/truncate/delete 统一通过 `agent_fs_apply_inode_event()`，create 只在 VFS 成功发布后进入目录协调。write/truncate 把已提交的 inode size、更新时间和文件代数先写入按 incarnation 绑定的 sidecar，查询立即覆盖旧主表；create/delete 先完成内存记录变化。只有带 `PERSIST` 的记录才增加该 workflow scope 的 dirty generation，volatile 文件的微写只改变内存/sidecar，不触发不包含该对象的空 bank checkpoint。首个脏变化开启固定一秒的非滑动窗口，后续微小变化只累计 coalesced 计数，不刷新 deadline。scheduler 在事务门空闲时每轮推进一个 checkpoint state；dirty scope 轮转成为该 job 的稳定 sponsor，实际物理传输受它的硬 `BACKGROUND` burst/refill 限制。提交成功或失败后只设置固定合并窗口，不再按 checkpoint 执行耗时延长休整；设备退化产生的占用和重试速率分别由 I/O debt 与固定 not-before deadline 约束。到期写回每轮先于扫描获得独立机会。提交只确认快照期间未继续变化的 scope，失败或新写入都保留待办。正常 sidecar 发布即使遇到 metadata 锁竞争也不会升级为全目录扫描，只有绑定缺失等无法局部表达的状态才进入协调扫描。metadata 可见 generation 按 scope 隔离，SYSTEM 对象变化才影响其他可见域；文件查询不保存全局内核结果缓存，而是每次按当前 generation 执行 scan/index。显式持久 metadata 管理、reload 和 scope retirement 同步进入 FIFO submit lane 并建立不可替换的持久化任务；这保证有序接纳，不把 syscall 返回描述为 primary 已完成回读验证的持久化屏障。显式 set 对已有路径只做无副作用的 inode 探测，参数冲突或失败不会因预协调而提前改写 metadata。

协调扫描本身也有独立的服务边界。`pending` 是 resume、idle、普通 full restart 与 urgent full restart 的有界多级状态，首次启用可立即扫描，后续普通请求既不能把 cooldown 提前，也不能持续后移首次到期时间；active 期间任意数量的绑定失败最多排队一轮。catalog 或 AUTOSCAN 容量耗尽时，普通 VFS create 仍由独立 STORAGE 配额决定并保留 workflow 标签；无法物化的 inode 把 `agent_meta_slot=-1` 和 sidecar 版本持久化为 capacity deferred。write/truncate/delete 看到该状态且 scope 仍饱和时不会重复排队扫描。只有扫描已经标记该 scope 饱和且 catalog slot 确实清除后，scoped capacity-release 才允许一次 urgent 补扫；metadata gate busy 的 delete 没有释放容量，只排队普通协调扫描。若释放恰逢 active scan 后续读错，urgent full restart 的优先级高于 cursor resume，不能漏掉旧 offset 前的 deferred inode。后台在争用 metadata 事务前先检查 not-before deadline，每 tick 最多处理 16 个目录项。完整扫描或加载/短读失败后都按 `max(20 tick, 4 * 本轮耗时)` 休整。因此某 scope 填满 metadata 配额后，即使低权限 Agent 持续微写经查询确认未绑定的超额文件，也不能把全根扫描变成无间隔全局事务风暴。冷启动仍先淘汰 lifecycle 已失效的旧动态 scope，但 v7 快照只按稳定的 112 条硬分区等磁盘合同判定，不能用后来收紧的 96 条 AUTOSCAN 新增长度把同版本历史判坏。97 至 112 条旧 AUTOSCAN 完整加载且不静默删除；运行期以 old/new class delta 只准其保持或减少，精确回滚走独立硬边界复核。

scanner 的绑定回退使用 catalog 的单一 resolver，而不是另一套全表 name scan。selector 同时携带从 `DIRSIZ + 1` 有界缓冲区取得的 physical/logical path 和完整 `dev + inum + incarnation`；不同 key 落到不同槽、或 identity 只命中另一条路径时，scanner 不作猜测并安排重试。路径仍命中但 incarnation 已变化表示同名新对象：旧记录先被撤销，新对象取得新 FID。VFS 的 lookup、create、link、unlink 和创建回滚共用唯一 `fs_dirent_canonicalize()`：非空输入只取磁盘可表示的前 `DIRSIZ` 字节并补 NUL。因此历史长名和同一 14 字节前缀继续是同一 legacy dirent alias，但命中后仍必须通过目标 inode 的 policy、scope 和逐操作授权，alias 不扩大权限。create hook 只把这个真实 canonical key 交给 metadata resolver，不再将 raw 长名写入物理/逻辑索引而使 reload 或 rollback fail closed。

全局文件对象表进一步由 EXEC resource account 和 ordinary/reserved 水位约束。一个活跃 Agent 的 9 页 detail/attribution sidecar、6 页用户 mirror 和 6 页可信 shadow 作为一次 21 页 `RESOURCE_AGENT_STATE_PAGE` 请求原子预留、提交和退款，共 84 KiB；六项总状态预算为每进程/全局/ordinary 池/reserved 池/ordinary 域/reserved 域 `86016/11010048/8257536/2752512/5505024/688128` B。CI 另以 sidecar-only 指标观察 9 页细节结构，其全局 1152 页、ordinary/reserved 864/288 页以及单 account 576/72 页仍有效，但不再代表独立的运行时 reserve。idle 普通进程不承担这些物理页；逻辑 admission 不是总内存 OOM 下的硬页保留。

每线程内核栈仍有 16 KiB 虚拟槽、4 KiB 未映射 guard 与 canary；物理页按 live thread 分配，32 MiB 只表示全部虚拟槽容量，8 MiB 才是受信/保留线程的物理池。启动/调度使用独立的 64 KiB `boot_stack`。`make kernel-stack-check` 拒绝越界线程/启动调用链，`make ci-check` 还核对 boot stack 链接跨度、栈虚拟容量、物理保留池、sidecar 动态容量和 `struct proc` 体积。

## 9. 验证入口

```bash
# Agent 权限、可信映像、VFS 域、调度和 syscall 输入防护
bash scripts/run-agent-tests.sh

# 双目标 ENOSPC、持久 PUBLIC principal 重启复测，以及 AgentOS 分级保留量
make fs-enospc-test TOOLPREFIX=riscv64-linux-gnu-

# 两个目标的退出、僵尸、阻塞 syscall 和资源域；主目标另测 Agent 保留槽
make proc-reap-test TOOLPREFIX=riscv64-linux-gnu-

# 主目标的线程域配额、普通/保留水位、退出退款和跨域公平
make thread-resource-test TOOLPREFIX=riscv64-linux-gnu-

# 两个目标的全局文件对象表资源域配额、普通水位和系统保留
make file-resource-test TOOLPREFIX=riscv64-linux-gnu-

# 两个目标的 syscall 内核工作预算和安全点公平性
make syscall-fairness-test TOOLPREFIX=riscv64-linux-gnu-

# 主目标的撤销、退出、阻塞资源、metadata/I/O 结算和 lifecycle 重用组合竞态
make workflow-teardown-race-test TOOLPREFIX=riscv64-linux-gnu-

# 构建期内核栈预算
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
make -C baseline_ucore kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-

# 固定 profile 下的增长、PCB、栈容量和 Agent 模块所有权预算
make ci-check
```

`scripts/run-agent-tests.sh` 当前包含 18 个程序；`workflow_teardown_race_ucore` 是独立机制专项，不计入这 18 项。371.5s、126.1s 和 `13824 < 16384` 是 generation-safe lifecycle、资源控制器、统一 teardown、lazy stack 和后续模块拆分之前的历史结果。2026-07-25 的 bounded/flood-safe runner 在固定 runner 连续三轮 16/16，总 case monotonic 时间为 `261.343281873s`、`237.948978492s`、`255.370930671s`；中位 `255.370930671s` 低于当时的 `268.14s` gate。2026-07-26，checkpoint `75d0dfde716453af90d7310c6a1521968fcf7167` 又在干净环境完成过一次 `make full-verify`，墙钟 `19:45.97`，其中 Reader E2E 和独立 teardown race 三轮通过。这些均是带日期和提交号的历史事实，不自动继承给后续代码。

18-case 时长门的校准状态由版本化 `ci/kernel-budgets.json` 判定。当前受管源码指纹已在冻结提交 `04c1e6652324` 的 clean detached worktree 上完成三轮，原始 timing、确定性压缩 Guest/runner 日志、环境、逐执行 attestation 和逐文件哈希保存在 `evidence/calibrations/04c1e6652324/`；中位基线为 `283.0201263s`，上限为 `297.172s`。配置状态为 `calibrated_full_suite`，受管输入变化会立即使其失效。该包严格是未签名本地 E3 校准证据，不等于发布验收、GitLab CI、远程 Runner 或 E4。某次发布的完整动态通过项和最终指标只由 `evidence/releases/INDEX.md` 选中的 release bundle 判定；bundle manifest 必须绑定被测代码提交 C、命令、原始/规范化日志和校验结果。INDEX 未指向完整且验证通过的 bundle 时，本文列出的其他 marker 只能解释验收合同或历史记录，不能作为当前 HEAD 已完成 E3 的证据；远程 Runner 是否执行同理由 bundle 的 remote attestation 字段决定。

通用 QEMU runner 二进制全量 drain，并在 marker 后继续大小写不敏感地检查包括 panic 在内的预定义 failure 模式；输出洪泛、迟到 marker、普通 case 信号退出、非零退出和后置 panic 都失败。显式 checkpoint profile 只接受完整 marker 后 runner 发出的单次 `SIGTERM`；显式 powercut profile 只接受认证 supervisor 对稳定 QEMU leader 发出的单次 `SIGKILL`，并要求随机 nonce、PID/starttime、镜像退出码及完整后代回收证明一致。该 powercut profile 是突然 VM 终止后的重启路径，不会清空宿主页缓存，也不能表述为整机物理断电。Reader seeded-action runner 使用另一条阶段契约：clean/build 只看退出码，guest 启动后才按完整日志行识别故障，文件名含 `panic` 不触发失败。预算 checker、runner 与生产 profile validator 的 fail-closed 自测集合以源码为准，不在文档固化容易变化的数量；任一具体发布是否完成 clean `full-verify` 或远程普通/QEMU Runner，仍须查该发布的 bundle manifest，不能由工作树状态或本文叙述推断。

这些专项入口检查的是机制约束。`make dual-platform-run` 继续验证科研平台功能等价和 AgentOS 专属证据；profile v5 的 `make full-verify` 串联 target structure、`ci-check`、宿主机/Reader、18-case Agent、双目标、进程生命周期、线程资源域、syscall 公平性、全局文件对象表、物理页资源、metadata 恢复、观测恢复、VirtIO fault、workflow teardown race、ENOSPC 和 filesystem allocator fault 专项，并把 allocator raw-image/flush 证据作为 canonical archive 交付。未校准的 18-case 时长策略会在首个 Agent QEMU 前 fail closed。内核栈与各机制测试仍保留独立入口，便于定位失败。

## 10. 维护要求

- 修改 syscall、VM、文件、同步、进程退出或调度路径时，必须运行对应安全专项测试。
- 新增 Agent role、capability、可信程序或 workflow 文件类型时，必须同步更新执行清单、VFS profile、负向测试和本文档。
- 新增安全域成员发布或凭据转换路径时，必须接入 ACTIVE/CLOSING 发布检查；新增阻塞点必须响应进程级退出请求，并由当前线程沿正常清理路径释放临时资源。
- 新增凭据降级、fork 或 exec 路径时，必须传播不可变 lifecycle key；只有 terminal teardown 可以 `leave`。槽复用必须递增 generation，禁止从 scope/PID/角色反推 lifecycle。
- 新增资源种类或配额时，必须接入 `resource_controller` 的 account、reservation 和 teardown settlement；不得恢复平行的 per-domain 私有计数。`resource_domain_id` 只用于调度。
- 新增进程级退出原因时，必须进入现有 teardown 状态机；REQUESTED 后不得发布新对象，scheduler handoff 前不得释放当前内核栈。
- `agent.c` 必须保持薄 facade；状态和实现归属版本化 owner 集合。metadata 的 transaction/file-state/catalog/query/scan/directory/objects/actions/prefetch/store（含 format/I/O）只能通过命名空间化窄接口连接，directory bridge 不得持有可写全局状态或形成反向依赖。
- 通用安全机制若同步到 `baseline_ucore/`，必须保持两侧行为契约一致；当前 resource controller、workflow lifecycle、统一 teardown 和 lazy stack 尚不是 baseline 的共享实现。
- 新的可恢复资源不足路径必须返回错误并回滚，不能把普通用户可触发的条件写成 `panic()`。
- 调整 `io_policy.h` 的 budget 或 cache floor/cap 时，必须同时保持 4 active + 8 retiring BACKGROUND 的保守静态 envelope、普通流量设备根 bucket 与 `NBUF` 静态断言成立，并复测 PUBLIC 压力、多 workflow、SYSTEM/workflow BACKGROUND、retiring 3/8、shared 排队轮转和保护流量带债进展。
- 修改内核或 Agent 模块边界时必须更新并运行 `make ci-check`。源码、镜像、text/data/BSS/total、PCB、legacy mail 两页 sidecar、Agent Context sidecar 与完整 Agent 状态容量、线程与 64 KiB boot stack 调用图、32 MiB 虚拟容量、8 MiB 物理保留池、owner/bridge 注册集合和模块阈值以 `ci/kernel-budgets.json` 为准；受控符号用户必须登记，SCC 硬上限不能仅改 JSON 放宽。metadata 拆分单元、IPC 及 contract headers 必须同时纳入聚合 source/text/BSS 预算，禁止靠跨文件迁移绕过 no-growth 约束。完整 18-case 套件耗时只在固定校准 runner 上作为硬门，独立 teardown race 另行验证。
- 文档不得把共享加固 baseline 描述为未修改的上游 uCore，也不得把通过结构扫描解释为完整运行验证。
