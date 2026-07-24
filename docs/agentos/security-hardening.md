# 安全加固与资源韧性设计

本文记录 AgentOS-uCore 在系统调用输入防护、同步、文件系统、调度、可信执行、文件访问和进程生命周期上的安全加固。目标不是为已知测试增加特判，而是建立可复用的内核机制，使普通进程或低权限 Agent 的错误和恶意输入不能停止内核、伪造权限或耗尽全局资源。进程槽与线程槽受进程资源域约束，CPU 先按 active 资源域轮转；文件系统块和 inode 受稳定存储 principal 或 workflow scope 配额约束。已覆盖路径上的资源不足通过可恢复错误与完整回滚保持系统继续运行。

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
4. **资源按所有权回收。** 阻塞 syscall 的临时引用、线程资源、进程执行槽和父进程等待凭据分别管理，退出时按依赖顺序释放。
5. **配额绑定与资源同寿命的身份。** 活进程及其线程按进程资源域累计，持久存储按稳定 principal 累计；fork、线程创建、进程退出或系统重启都不能重置对应账本，系统关键工作保留独立份额。
6. **策略保持可配置，硬性约束不可绕过。** orchestrator 可以调整 Agent 域内调度权重等策略，但不能关闭资源域轮转、普通进程的有限等待保证、可信映像校验或 VFS 能力检查。
7. **高频局部变化不能同步放大全局维护。** 数据路径只发布可立即查询的局部状态，昂贵的全局 checkpoint 由分域待办、固定合并窗口和后台频率上界统一调度。
8. **设备服务和缓存也服从稳定 owner。** 块 I/O 以持久主体/workflow 而不是 PID 归因，前台、控制和后台预算分离；cache floor、cap 和 retirement 使用同一 owner 生命周期，系统关键工作保留独立份额。
9. **观测查询也必须计费。** 计数查询和不复制结果的查询不能成为绕过调度预算的旁路；所有被检查的来源记录都按固定批次预付内核工作预算，可扩展集合通过有序索引或单遍归并读取。
10. **安全域必须有可信终止权。** workflow 的关闭权绑定创建时的唯一内核 controller，而不是角色、PID 或父子关系；撤销先使授权失效，再让成员沿正常 teardown 释放资源，不能从外部粗暴释放线程持有对象。

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
| `4f1bfaa` | `MESSAGE_SEND` 被解释为全局裸 PID 通道，低权限 Agent 可向 Recovery/Orchestrator 注入消息并耗尽关键事件队列 | stable control id 定向路由；接收方或受权控制者 grant/revoke；external/direct/attributed/source 三层配额，为显式内核 origin 保留至少 4 个容量名额；慢订阅者隔离和退出回收 | `agentsecurity_ucore` 已验证未授权拒绝、grant/revoke、target LLM_DONE consent、MESSAGE 位图隔离和 `ROUTE_MAX+2` 短命 source 槽回收；`agentloop_ucore` 已验证 source=4、directed=8、external=12、第 13 条 external 拒绝、4 条 KERNEL TIMER 保留容量、消费后重接纳和慢 watcher 隔离。attributed=8、同一来源混合跨类及路由幂等/部分撤销仍缺独立输出 |
| `16d11aa` | Agent capability、对象表和 IPC 仍可被解释为所有 workflow 共享的全局权限 | syscall 541 由可信 factory 创建动态 scope；所有敏感对象使用 capability + active scope + stable owner；syscall 542 只一次性委派 pipe；最多 4 scope 并保留独立进程/存储/对象份额 | `agentscope_ucore` 的同名对象、动作、租约、IPC、audit、配额、fd 委派、事务竞争和回收断言已在完整 Agent 回归中通过 |
| `16d11aa`（审计） | 用户可伪造 span/cause，低权限遥测或委派 span 可挤掉关键审计效果 | private span owner 与 cause control sidecar；`context_push` 拒绝非零 cause/span；审计按 scope low/high 分区，只有内核确认的特权状态效果进入 high | `agentsecurity_ucore` 的 forged context、trusted cause attribution、audit authority partition 回归已通过 |
| `7ebe45e` | 长时间运行的 syscall 在内核态关闭中断期间越过用户态时间片，PUBLIC 进程可持续独占 CPU | 每次 dispatch 建立不可由 syscall 重置的周期 deadline 和工作额度；统一 begin/end、timer pending、resumed 检测和显式安全点约束长路径；fork 用 VM snapshot 屏障稳定源页表且逐页计费；inode 调度后短返回，truncate 先 detach 再以不可取消 cleanup checkpoint 回收；FD reservation 与文件槽快照保证让出期间的生命周期 | 本次线程改动后双目标 `run-syscall-fairness-tests.sh` 已通过控制台、inode 写、`O_TRUNC` last-syscall 计数、observer 和退出完整性契约 |
| `89d412d` | PUBLIC 配额错误绑定短命进程资源域，完整退出后可换域或重启绕过累计上限；覆盖 SYSTEM 预装可变文件还可绕过新分配计费 | 独立 `storage_principal_id` 凭据；当前无 uid/tenant ABI，因此所有普通进程绑定安装级 PUBLIC principal 2；挂载清扫不可达 inode/块、从 qmap/dinode 重建账本并拒绝旧格式；PUBLIC 首次修改 SYSTEM 赞助对象前，以 qmap-first 顺序整体接管 inode 和已有块 | 双目标 `fspquota_ucore` 在同一镜像连续启动三次，覆盖打开后 unlink 强制断电、14 块赞助对象接管、完整进程域退出、重启恢复、删除退款和新域再次受限 |
| `e67d1c0` | 全局文件对象表没有资源域边界；阻塞 syscall 可在关闭 FD 后继续固定临时引用并绕过每进程 FD 上限 | 唯一 filepool 槽按创建者资源域和 ordinary/reserved admission 类别计费；普通分配同时受每域上限和全局水位约束，内核受控工作保留独立容量；最后引用关闭才退款 | `make file-resource-test` 已以 64/48/16/16 配置在 AgentOS 和 baseline 双目标通过 |
| `1464c37` | 低权限 Agent 的微小文件写入同步放大全局 metadata 持久化并阻断其他 workflow | inode sidecar 即时发布、scope-local dirty/durable 代数、固定非滑动合并窗口、分块 COW bank 状态机和 scope-local 缓存失效共同解耦数据路径与 bank 提交；scanner 保留独立自适应 cooldown | `agentscope_ucore` 已验证至少 128 次单字节写只形成有界批次、另一 scope 完成 32 次查询，并在强制重载后保持最终一致 |
| `831823e` | 块设备队列和全局 buffer cache 没有持久主体/workflow 公平边界；内核态 yield loop 可长期屏蔽 timer/device 中断，fault 退出还可在 `freethread()` 前后丢失清理 I/O 账本 | 稳定 owner/class 的 lease/token/debt、排队 shared grant 轮转、普通流量设备根限速、SYSTEM/CONTROL 带债前进、每轮 scheduler idle kerneltrap 中断窗口、terminal cleanup I/O/kernel-work 上下文、按 sponsor 设置的 cache floor/cap 与 exclusive holder；FS atomic/quiescent checkpoint、grouped qmap claim、FIFO metadata submit lane 把跨预算生命周期纳入统一机制 | 最终修复后的独立 `iobudget_ucore` 输出八项具名机制标记和 `parent passed`，`elapsed=2.4s`；完整 Agent 16/16 的 `359.4s` 是 pipe 委派历史轮；`make fs-enospc-test` 的既有 75.1s 历史轮通过，设备错误、短 I/O、metadata COW 和 grouped claim 中点掉电仍缺动态注入 |
| `859ffe4` | 线程未计入资源域，PUBLIC 进程可用 thread bomb 扩大 CPU 竞争份额并耗尽线程槽 | admission 原子预扣主线程；额外线程按不可变资源域和 ordinary/reserved 类别计费，受域上限、普通全局水位和系统保留量约束；每类域上限必须严格小于对应全局水位；创建失败与退出统一退款；调度器先严格轮转 active 域，再执行域内 FIFO/Agent 软评分 | `make thread-resource-test` 以 19/12/6/6/4 tiny policy 验证 12 项边界并通过；完整 Agent 16/16、默认构建、单独 `agentsched_ucore`、双目标进程回收/syscall 公平性/filepool 脚本也通过，`full-verify` 尚未运行 |
| `c85b47a` | Recovery 可在 metadata 全局事务门内触发依赖派生图超线性重建 | 依赖表只保存 scope-local 显式边，兼容 mask 在既有单遍扫描内按需解析；`ACTION_COMMIT` / `RERUN_STAGE` 使用一次选集和一次原子提交；跨 Agent 预取只携带稳定端点并在预算让出后重校验 | `agentfs_ucore` 的 `metadata_action_bounded=1 field_driven=1 batched=1`、handoff 生命周期和 kernel-work preemption 标记 |
| `a24f68c` | 跨安全主体创建会自动传播 pipe 控制端点，低权限 Agent 还能继续转委派 | 一次性票据绑定发起线程的下一次主体创建，在不可让出区固定精确 file 对象并消费；子进程只得到端点、不继承票据，失败和 exec 撤销票据，未知继承类别默认拒绝 | `agentscope_ucore` 与 `agentvfs_ucore` 覆盖单跳 pipe 委派和跨 scope 隔离；exec 撤销目前由实现审计覆盖 |
| `a33092b` | audit/span/timeline/provenance 的计数和过滤路径反复扫描全局 512 槽，低权限 Agent 可在一次 syscall 内制造超线性 CPU 工作并绕过域级公平 | 每 workflow audit scope 维护 sequence 与 `(tick, sequence)` 两个 128 槽有序索引，淘汰统一执行 unlink/publish；ledger 窗口摘要直接读取索引状态；四类观测查询按单遍或四路归并读取，并在扫描前分量子预付、让出后重计和补足 kernel-work 预算 | 完整 Agent 16/16 通过；`agentscope_ucore` 验证索引顺序、低权限查询调度证据和跨 scope 进展，压力循环 12 次、查询内让出 64 次，另一 scope 的 32 次查询在 3ms 内完成 |
| 本次修复 | workflow 没有可信强制撤销，低权限成员可在根 Orchestrator 退出后继续持有 capability、进程槽、FD 和 scope 生命周期身份 | 生命周期账本绑定唯一根 `agent_control_id`；显式关闭或根离开将 ACTIVE 原子转为 CLOSING，统一撤销授权、拒绝发布并向 active/pending 成员提交协作退出，最后成员正常清理后才进入有界 RETIRING | `agentscope_ucore` 验证低权限/子 Orchestrator 拒绝、64 位 scope 校验、根自关、factory 关闭、阻塞成员清理、根自然退出、9 轮回收与 replacement admission |

## 4. 用户输入与内核对象检查

系统调用层只把寄存器参数视为数值或用户虚拟地址。字符串、数组、结构体和输出缓冲区必须先通过 VM copy 接口访问，内核不能直接解引用用户地址。长度计算先检查上限和溢出，变长参数在分配文件、页、进程或写入磁盘之前完成验证。

文件描述符、inode、pipe 和同步对象通过稳定引用跨越可能睡眠的路径。失败路径按获取顺序的逆序释放引用，避免一次非法 syscall 消耗全局文件表或物理页。用户页在 syscall 执行期间失效时，copy 接口返回失败，不把用户缺页升级为内核异常。

相关实现集中在：

- `os/syscall.c`、`os/vm.c`：参数复制和地址范围检查；
- `os/file.c`、`os/pipe.c`：文件、pipe 临时引用生命周期；
- `os/loader.c`：`exec` 参数、映像布局和地址空间替换；
- `user/src/usersafety_ucore.c`：坏地址、超长参数、定向等待和失败事务复测。

## 5. 等待、退出与进程/线程资源域

### 5.1 定向等待和协作退出

`os/wait.c` 为 mutex、semaphore、condvar、进程等待、Agent event 和 timeline 等睡眠对象维护明确队列，只唤醒与状态变化相关的线程。线程取消从同一队列摘除节点，避免节点同时出现在运行队列和等待队列。pipe 当前仍以让出处理器的方式等待，但会检查进程退出请求并沿正常 syscall 清理路径释放临时引用。

多线程进程退出时，发起退出的线程先把进程标记为退出中，取消其他线程的可中断等待并等待它们离开 syscall。只有不再存在会访问进程共享状态的线程后，内核才释放地址空间、文件表和同步对象。这使阻塞 `read()` 等路径持有的临时引用能够沿正常清理路径归还。

### 5.2 执行槽与等待凭据分离

子进程退出结果保存在父进程私有的 `child_record` 中。父进程未调用 `wait()` 时，记录仍保留退出 pid 和状态，但已退出进程的执行槽、内核栈、页表和文件引用可以先释放。每个父进程的待取结果有固定容量，恶意父进程只能耗尽自己的记录配额。

### 5.3 活进程资源域与系统保留槽

非管理员的普通 fork 后代继承不可变的 `resource_domain_id`，一个域的 live 进程数最多为 `PROC_RESOURCE_DOMAIN_LIMIT=64`。128 个进程槽中，普通 admission 合计最多使用 `PROC_ORDINARY_SLOTS=96`；`PROC_RESERVED_SLOTS=32` 仅供内核受控的 boot、workflow、Agent 和 worker admission。最多 4 个 workflow 各保证 8 个保留槽，单个 scope 不能消耗其他 scope 的 admission 份额。创建新隔离域或消耗保留槽需要内核持有的 domain admin/factory 状态，用户态参数和普通 Agent capability 都不能自行获得。

这套机制同时满足三个目标：单个长存活 fork bomb 有上限；父进程退出或后代重新挂接不能重置配额；普通域达到上限后，受权 Agent 和 worker admission 仍有独立保留槽。

### 5.4 线程资源域与域级公平调度

线程槽和进程槽使用同一个不可伪造的 `resource_domain_id`，但维护独立计数。进程 admission 在发布进程前原子预扣主线程槽，避免“进程已接纳、主线程却无法建立”的半初始化状态；之后每个 `thread_create()` 都按线程上保存的 ordinary/reserved admission 类别扣账。普通线程同时受 `THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT` 和 `THREAD_RESOURCE_ORDINARY_LIMIT` 约束，受控线程使用独立的域上限及 `THREAD_RESOURCE_RESERVED_LIMIT`，所有类别还受物理 `THREAD_RESOURCE_POOL_SIZE` 总量约束。策略头在编译期要求普通和保留域上限都严格小于对应全局水位，错误配置不能让单域吃完整个类别。默认值集中在 `thread_resource_policy.h`，专项构建可缩小参数而不改变机制。

扣账、线程状态发布和失败回滚处于同一关中断生命周期协议。用户栈或页表映射失败、线程启动失败和正常/异常线程退出都通过统一退款路径清除线程上的不可变归属；进程资源域只有在 live 进程、ordinary/reserved 线程和文件槽全部归零后才可清除或复用。主线程直到 terminal teardown 离开可运行/阻塞状态、且不再需要内核栈时才退款，避免提前释放名额后仍占用真实线程资源。

运行队列采用两级结构。外层 active-domain FIFO 保证每个有可运行线程的资源域只出现一次，并在每次 dispatch 后严格轮转；内层队列只保存本域线程。没有 Agent 时按域内 FIFO 取队；存在 Agent 时角色、事件、deadline、priority、budget 和虚拟运行量仍参与软评分，但 `scheduler_agent_burst` 与 score burst 都是域内状态，只能决定该域本轮选哪个线程，不能让线程多的域跨过其他 active 域。这个边界同时限制 thread bomb 的线程槽占用和 CPU 份额，不按 PID、程序名或 syscall 特判。

### 5.5 全局文件对象表配额与系统保留

全局 filepool 以“唯一 `struct file` 槽”计费，而不是以进程 FD 或引用次数计费。`filealloc(owner)` 在发布新槽前把创建者的 `resource_domain_id` 和不可变 ordinary/reserved admission 类别写入槽；普通分配必须同时满足本域上限和全局 ordinary 水位，只有内核已授予 `resource_slot_reserved` 的 boot、workflow、Agent 或 worker admission 才能使用保留区。默认配置下，两个目标的 filepool 都有 2048 槽：主目标 ordinary 水位为 1536、系统保留 512、普通域上限 1024、单个受控域上限 128；baseline ordinary 水位为 1792、系统保留 256、普通域上限 1024、受控域上限 256。容量公式集中在共享的 `file_resource_policy.h`，测试构建可以缩小数值而不改变分配机制。

fork 继承和阻塞 syscall 的 `filedup()` 只共享已有 file 对象，不产生第二份配额；关闭原 FD 也不会让仍被 syscall 固定的对象从账本消失。只有最后一次 `fileclose()` 把引用降为零时，内核才原子清空 filepool 槽并向原创建域退款，再在临界区外完成可能让出的 inode 或 pipe 清理。资源域也只有在 live 进程数、ordinary/reserved 线程数和文件槽数都归零后才可复用，避免跨域继承资源仍存活时发生 domain id 复用和错账。

分配、引用和最终退款在同一关中断临界协议内完成；pipe 创建先预留两个进程 FD，再分配两个受配额约束的 file 对象，任一阶段失败都按逆序释放并退款。因此失败的双端 pipe、fork 回滚、进程退出和阻塞 syscall 展开都经过同一生命周期接口，不依赖 syscall、PID 或文件类型特判。该通用机制在 AgentOS 与 `baseline_ucore/` 保持一致。

### 5.6 文件系统存储主体与分级保留量

进程表中的 `resource_domain_id` 是短命、可复用的执行资源槽，只用于累计存活进程，不能作为持久磁盘身份。文件系统计费改用独立的 `storage_principal_id`：当前 uCore 没有 uid、登录会话或租户 ABI，因此所有普通进程都绑定安装级匿名 PUBLIC principal `2`，无论它们属于哪个进程资源域，退出、重新 fork/exec 或重启都不会换一个配额身份。workflow 仍按内核签发的 scope 计费，但动态 scope 从 `3` 开始；`0`、`1` 分别保留给 VFS PUBLIC/SYSTEM 语义，`2` 只作为稳定 PUBLIC 存储主体，不是可创建的 workflow scope。以后接入用户或租户系统时，应由可信身份层签发另一稳定 principal，并继续与进程资源域、PID 和映像名称解耦。

磁盘格式在 bitmap 后保存逐块 owner map，并在 inode 中保存存储 owner 和格式版本。数据块、间接索引块和目录扩容块按实际分配主体写入 owner；`truncate`、`unlink`、失败写回滚和 inode 回收再按持久 owner 精确退款。由 mkfs 或可信维护路径以 SYSTEM 计费但允许 PUBLIC 修改的文件，在第一次 PUBLIC 写入或截断前必须整体接管。内核在固定 `MAXFILE + 1` 工作区收集直接块、间接索引块及其数据块，排序后按 `QBLOCK` 分组，使每个 qmap block 在一轮中只读写一次；可睡眠 claim gate 串行化同类操作，但它是 wake-all 重检，不宣称 FIFO。预检可中断且每次 checkpoint 前都已释放 buffer；随后一次预留全部 PUBLIC 配额，按 qmap-first、inode-last 进入不可回滚的前向提交，cleanup checkpoint 即使收到退出请求也继续完成。挂载会识别 SYSTEM inode 下已有部分 PUBLIC qmap 的中间状态，并沿相同方向完成接管。这样覆盖已有块同样不能绕过配额，也不会把半接管对象误记为 SYSTEM。VFS 凭据同时携带对象授权所需的 scope/capability 和计费所需的 storage principal，两者职责分离。

分配水位分为三层：PUBLIC principal 必须同时留下所有 admitted/future workflow 和 SYSTEM 剩余量；某个 workflow 只能在自己的 scope 配额内使用共享 workflow 水位，并必须留下其他 scope 尚未消费的保证；内核维护路径和受信任 SYSTEM 可以消耗自己的系统信用，但仍须兑现所有 admitted/future workflow 的最低保证。容量算法由 `fs_storage_policy.h` 在 mkfs 和内核间共享：以完成镜像后的真实空闲量为输入，workflow 总保证最多使用扣除 SYSTEM 后余量的四分之三，并设置每 scope 320 inode/512 block、SYSTEM 8 inode/512 block 的显式硬下限。计算出的 policy version、scope 数、PUBLIC principal、G/S 和 checksum 持久化在 superblock；内核重启固定使用 G，只从 `free-4G` 恢复尚未消耗的 SYSTEM 信用，避免把合法消耗的 S 再预留一次。当前平台镜像核算结果为每 scope 342 inode/1195 block、SYSTEM 64 inode/512 block。PUBLIC 和每个 WORKFLOW scope 的块/inode 还分别累计到稳定 owner 上限。

挂载时会校验 superblock 容量契约及 `inode -> bitmap -> owner map -> data` 的完整布局。内核先从扁平根目录建立 inode 与数据块可达集合：无目录引用的已分配 inode、以及没有任何可达 inode 引用的 bitmap 块会在计费前清扫；悬空目录项、重复块、越界块或引用空闲块则视为损坏并拒绝启动。这样在 `balloc` 尚未挂接映射、truncate 已分离映射但尚未回收、或打开文件 unlink 后突然掉电的窗口中，不会形成永久 PUBLIC 配额泄漏。随后分别扫描 qmap 与 dinode，从持久 owner 重建 PUBLIC 已用 block/inode 数和 workflow scope 下界；账本不依赖任何进程仍然存活。第一次核算只服务于恢复，先回收没有持久恢复令牌的旧 workflow boot lease；第二次核算才要求空闲量至少覆盖持久化的 `4G`，避免合法的旧 scope 残留在回收前把系统误判为不可启动。新 workflow admission 与分配使用同一关中断临界区检查实际剩余保证。mkfs 在安装完全部可信程序后要求初始空闲量同时覆盖 `S+4G`，不能兑现时拒绝出镜像；主机 mkfs 每次生成镜像前都按当次 `FS_*` 参数重编，避免配置与内核漂移。此次同步提升容量策略、owner 和 superblock 契约版本；旧镜像没有稳定 PUBLIC principal，内核会明确拒绝而不是以零账本继续挂载。已分配块缺少 owner、已分配 inode owner/version 无效或 checksum 错误同样拒绝启动。当前教学文件系统没有日志，挂载清扫只恢复资源可达性和配额不变量，不宣称文件内容更新具备完整事务原子性。

### 5.7 Agent 文件版本 sidecar 生命周期

编辑版本和内容版本不再分别从两个“先到先得”的全局池分配。内核按 `inum` 直接索引覆盖全部磁盘 inode 的统一 sidecar，并同时校验 `dev + inum + incarnation + storage owner + VFS policy`。一个存活 inode 只能占用自己的槽，PUBLIC 文件不能通过反复创建新 incarnation 占走其他 inode 的版本位置；PUBLIC、WORKFLOW 和 SYSTEM 可获得多少版本状态，因而由同一套稳定存储主体 inode 配额与分级保留水位决定，不再维护一套容易漂移的平行配额。

删除目录项不是文件生命期终点，因为仍可能有打开的描述符继续访问 inode。实际清理挂在 `iput()` 的最终回收分支：只有链接已删除、最后引用释放且 `itrunc()` 成功后，才在清除 inode 身份之前原子移除该 incarnation 的编辑版本、内容版本、活动租约和 digest cache。新 incarnation 首次触达同一槽时还会执行防御性旧状态清理；仅持有旧 `dev + inum + incarnation` 的提交或租约过期路径只能查找，不能重建已经死亡的版本状态。

### 5.8 块 I/O 速率预算与 buffer cache 保留

块设备服务使用与持久存储一致的 owner：安装级 PUBLIC、SYSTEM，以及每个内核签发的 active 或正在清理的 workflow。syscall 在进入可能触盘的实现前捕获 owner 和 class；PUBLIC 使用 `NORMAL`，workflow 的 Orchestrator/Recovery 使用 `CONTROL`，其他 workflow 使用 `NORMAL`。内核 metadata、scanner 和 scope reclaim 显式建立 SYSTEM 或触发 workflow 的 `BACKGROUND` job。新 syscall 默认进入 I/O admission，只有已经审计为不可能触盘的 syscall 才在统一 allowlist 中跳过；嵌套文件调用复用最外层 request，不能靠内部 helper 重取信用。

每个 owner/class 有受保护的 burst/refill bucket。PUBLIC NORMAL 为 32/16；每个 active workflow 的 NORMAL/CONTROL/BACKGROUND 为 24/12、48/24、8/4；每个 retiring workflow 只保留 BACKGROUND 8/4；SYSTEM SYSTEM/BACKGROUND 为 96/48、16/8；前台 shared 为 32/16。请求先租 owner/shared 信用，并尽量租设备根信用；只有真实 VirtIO 1 KiB 传输完成时才提交。首个完成提交已有 lease，后续完成继续消费 token，超额分别形成 owner debt 和 device debt。没有触盘的请求退款，线程退出和等待取消清理未提交 lease。admission 与 debt 使用分离的对象私有队列；同一 bucket 只把排队信用交给 FIFO 队首。存在排队者时，shared grant 再按 owner/class cursor 轮转；没有任何 admission waiter 的 fast path 可直接借 shared，因此实现和测试均不声称“所有 shared grant 都经过 round-robin”。`BACKGROUND` 不能借 shared。

设备根 bucket 的 burst/refill 为 560/280，但它不是所有流量的硬聚合上限。PUBLIC、workflow `NORMAL` 和其他非保护流量必须取得根信用，并在 device debt 清零前等待；SYSTEM owner、`CONTROL` 和 `SYSTEM` class 在根信用耗尽时仍可使用自己的 owner/class 保留预算前进，每个完成仍增加 device debt，后续 refill 先偿债。编译期断言只证明已配置的 PUBLIC、SYSTEM、shared、4 个 active workflow 全部 class 和最多 8 个 retiring workflow `BACKGROUND` burst/refill 落在 560/280 静态 envelope 内，不把保护流量的带债进展描述成运行时硬总上限。

只有 token/debt 账本还不够：如果唯一 runnable 线程反复在内核态 pipe 条件路径 `yield()`，旧 scheduler 可能一直在关中断状态重新调度同一线程，使负责 refill 和设备完成的 pending timer/device interrupt 没有交付机会。现在每轮选择线程前都把执行身份切到 idle context，安装 kernel trap 向量并短暂打开中断，随后再关中断进入后台维护和原有调度选择。这是所有调度轮次共享的机制边界，不依赖 PID、文件名或 syscall 特判。

由主线程触发的正常 `exit()`、用户 page fault 或非法指令共用进程级 terminal teardown；非主 sibling 无论正常退出还是 fault 都只经 `thread_exit_current()` 回收自身。进程级 teardown 等 sibling 从阻塞点展开后，由主线程建立 cleanup kernel-work 与 I/O request；该模式忽略进程退出取消，文件描述符关闭、未链接 inode 回收和其他释放 I/O 继续按原 owner/class 归因。`bio_request_end_current_cleanup()` 提交残余 lease 并结算 owner/class debt；PUBLIC/NORMAL 还等待 device debt，SYSTEM/CONTROL 的受保护 device debt 留在全局设备根账本中由 refill 偿还。之后才结束 cleanup、释放 teardown thread 并调用 `vfs_proc_reset()` quiesce 最后 workflow 成员。`fault_exit_cleanup=1` 动态验证 PUBLIC 主线程 page fault 后物理写增长、未归因传输不变且 lease/两级 debt 清零。

buffer cache 为每个 buffer 记录稳定 sponsor。256 个 buffer 中，SYSTEM、PUBLIC、每个 active workflow 的 floor/cap 分别为 40/96、24/48、36/64。cap 是稳态驻留边界而非瞬时硬占用上限：必要的 transient buffer 可暂时越界，但在最后引用释放时立即失效。替换只会使用 invalid/dead owner、调用者自身或高于自身 floor 的 donor；跨 sponsor 命中不会给原 sponsor 刷新 LRU，新分配数据块由 `bclaim()` 转到实际 owner，而共享文件系统 metadata 不会因读者访问被偷换 sponsor。scope quiesce 后不再保留 36/64 active 分区；仅当轮转 reaper 正在执行该 owner 的清理 job 时提供 3/8 临时 floor/cap。后台 job 继续使用触发它的 SYSTEM/workflow owner，不把多个 workflow 混入一个全局 background cache 分区。这里的“隔离”是缓存容量和服务公平，不是每 owner 复制一份数据，也不是保密边界。

同一 `dev + blockno` 始终复用一个 buffer，并以 exclusive `holder + hold_depth + holder_waiters` 串行化。进程持有任一 buffer 时，I/O checkpoint 只能 deferred，CPU 工作 checkpoint 也不能 yield。`readi()` / `writei()` 还以 `bio_fs_atomic_enter/leave()` 标记复合文件系统原语；普通 checkpoint 在原子段内延后，只有调用者已释放全部 buffer 且自行保证 inode/目录状态已提交时，才可使用 quiescent checkpoint 睡眠。内核动态验证“不持 buffer”，而“状态已提交”是调用者契约。qmap claim、truncate 和退役清理进入不可回滚阶段后使用 cleanup 变体，退出请求不能中断其有界前向提交。文件读写由此可在块边界返回正数短 I/O；loader 和 metadata exact-read helper 在原子段外偿还预算并从已提交 offset 续读。

workflow 生命周期账本区分 ACTIVE、CLOSING 和 RETIRING。CLOSING 已撤销用户授权并拒绝新对象/成员，但在现有成员完成自身 terminal cleanup 前仍作为完整 I/O/cache owner 驻留，不能提前降成只具备后台份额的 retiring owner。最后成员释放 scope 引用后才进入 RETIRING；此时不再接纳用户对象或用户 I/O，但稳定 owner 保留 `BACKGROUND` bucket 来完成清理。VFS 生命周期身份账本有 `NPROC` 条记录，只有 `used == 0` 的记录可复用；ACTIVE+CLOSING admission 最多 4 个，计入 admission 的 ACTIVE/CLOSING 与 RETIRING 合计硬上限为 8，并最多占用 8 个 FS reclaim cursor。reaper 在 `NPROC` 身份账本范围轮转选择 retiring scope，避免固定槽位饥饿。设备静态 envelope 更保守地计入 4 active + 8 retiring 的 BACKGROUND 保证。namespace、inode 和 detached block 全部处理后，`bio_scope_retire()` 才把 I/O owner 标记为 retiring、定向唤醒残余等待者；owner 状态直到 active request、waiter、lease 和 debt 全部归零才释放，空闲缓存同时失效。这样新 scope 不会覆盖旧账本或遗失其 I/O owner。scope 文件清理采用 namespace detach、inode detach、逐块 reclaim 三段可恢复协议，每个 step 都不跨预算边界持有 inode 或 buffer；普通 truncate 和最终 inode 回收复用同一个 `inode_reclaim` 描述符。因此限流不会把已经从命名空间移除的对象变成永久泄漏，也不依赖某个 PID 或 syscall 特判。

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

external=12 的 admission 上限为显式 `KERNEL` origin 保留至少 4 个容量名额。heartbeat TIMER 等内核直接产生的事件必须明确传入该 origin，才能越过 external 边界；带 Agent source 的文件状态、作业完成和拒绝通知即使事件类型属于系统通知，也仍按 attributed external 计费，不能冒充内核来源侵占保留量。所有 origin 最终仍受 16 槽总容量约束。`LLM_DONE` 虽由 `LLM_RELAY` 专用工具产生，但它是定向 IPC，既需要 route，也计入 directed/external/source 三组配额。

广播系统事件逐 watcher 独立尝试。某个慢订阅者队列已满、watch 不匹配或已经退出时，内核继续投递后续目标；文件 metadata 等权威状态一旦提交，不会因为一个通知接收者资源不足而错误返回 `NO_SPACE`。这种 best-effort 通知语义把控制状态提交与观察者背压分离，避免单个低权限 Agent 阻断全局工作流。

当前路由授权表是 scope-local 内核私有执行状态，尚无 snapshot/query ABI，grant/revoke 也不追加 Context 或 audit。现有审计只能观察授权之后同 scope 的事件入队和消费，不能完整重建路由策略变更历史。若后续需要运维审计，应新增只读快照和受权的 route-change audit record，而不是把用户日志当作权威控制面记录。

### 6.4 Workflow scope factory 与对象所有权

scope 编号由内核定义：0 是 PUBLIC，1 是只读可信 SYSTEM，3 及以上是动态 workflow；数值 2 保留给稳定 PUBLIC 存储 principal，不是 workflow scope。只有非 Agent、具有 resource-domain admin 且仍运行可信 bootstrap 映像的 factory 可以调用 syscall 541 `agent_workflow_create(role)` 建立新 scope。普通 role grant 只允许在当前 scope 内调用 `agent_create_role()`，即使 orchestrator 有全部业务 capability，也不能用它铸造新对象域和新配额。

同 scope Agent/worker 继承 scope，但不是同一个安全主体；workflow 或可信 bootstrap 动态 scope 的降权普通 fork 也会建立新凭据。pipe 因而不再按环境状态自动传播：syscall 542 `agent_scope_delegate_fd(fd)` 只接受 pipe，并把一次性票据绑定到调用线程的下一次安全主体创建。内核在不可让出的临界区同时 `filedup()` 固定精确对象、只清除该线程票据，再开始可能让出的 VM 复制；其他线程的 spawn 不能抢走票据，并发关闭和 fd 复用不能把票据换绑到新对象。成功子进程只获得端点、不获得票据，继续交接必须重新授权；参数、权限、映像、资源等任何创建失败以及 `exec` 都撤销调用线程票据。file 对象使用显式继承类别：stdio 可继承、inode 仅在 scope 不变时继承并逐操作重鉴权、pipe 必须委派、未知类型默认拒绝。普通 PUBLIC 父子仍是同一安全主体，resource-domain admin 的记账域变化不参与 Agent/VFS 授权，因此保留 POSIX pipe 继承；普通文件不能通过委派接口传递。

敏感对象身份按类型组合 scope 和 stable owner：文件/metadata/租约以 `scope + dev + inum + incarnation` 为基础，action/dependency/cache 先按 scope 分区，IPC/wait control 使用同 scope stable control id，span/audit/prefetch 使用 scope + 公开 span + 私有 span owner/cause principal。capability 只在当前 scope active 且对象 owner 精确匹配时生效；SYSTEM 仅在显式只读路径可见。

新 workflow 根在发布为 runnable 前取得不复用的 `agent_control_id`，并把它精确写入 scope 生命周期账本。只有仍持有该根标记且 control id 一致的进程，或非 Agent、具备 resource-domain admin 且仍运行可信 bootstrap 映像的 factory，才能调用 syscall 545 `agent_workflow_close(scope_id)`。后创建的 Orchestrator、低权限 Agent、同 PID/PCB 槽或单独的 `ORCHESTRATE` capability 都不获得关闭权；scope 参数先按完整 64 位动态范围校验，不能通过截断高位构造别名。

显式关闭与根进程正常退出、fault 退出及凭据清除走同一个幂等 controller-departure 路径。关闭采用连续的两阶段关中断协议：第一阶段先把 ACTIVE 原子转换为 CLOSING，形成不可逆的授权与发布屏障，`vfs_scope_active()` 随即为假，现有 Agent/VFS capability 的可见值归零，scope acquire、storage reserve 和 pending exec 激活均拒绝；调用路径在两阶段之间不主动让出，第二阶段在新的关中断区扫描 active 与 pending scope 凭据。扫描只在首次请求时写 `exit_requested`，不覆盖已开始 teardown 的 `exit_owner_tid`；仅唤醒可中断 sleep，成员在 syscall/fault/exit 边界自行关闭 FD、释放 inode、结算 I/O lease/debt 和线程资源。spawn 最终发布也在自己的关中断区复核 `vfs_scope_active()`；在当前单 hart 模型中，该复核与 CLOSING 发布通过关中断序列化边界互斥，因此关闭后不会漏出新的 runnable 成员。

最后成员释放引用后才进入 RETIRING 并撤销 active cache floor。不可复用的生命周期账本继续保留该身份，轮转 reaper 使用 BACKGROUND 预算清理 metadata、dependency、action history、edit lease/version、query/digest cache、audit、span prefetch、IPC 和普通文件，再释放 scope 与 I/O owner。boot scope 持久文件保留，其 scope 作为 inactive storage owner 留存，避免旧工件被新 scope 重新解释。

### 6.5 审计分区和可信因果

物理审计表 512 槽按最多 4 个 workflow 各保证 128 条，每 scope 分成 low 64 和 high 64。Context/event/sched/prefetch/manual 等遥测始终 low，low 每 stable principal 最多16条。只有 syscall/tool 成功后由内核确认的特权状态效果进入 high；high 依据每 scope 8 个保留进程份额为每 active principal 保留8条。high 满时只滚动当前 principal 自己的记录或回收已退出/inactive principal 的最旧记录，绝不牺牲另一 active principal 的 protected evidence。inactive 历史仍是有界窗口，回收量通过 `dropped_records` 可见。

公开 `span_id` 和 `cause_sequence` 是呈现字段，不是权限票据。内核为每个 Context/event 保存私有 span owner、source control 和 source pid；`context_push()` 要求用户输入的 cause/span 都为0，再由内核连接当前链。每 scope 独立维护 ledger hash，但 sequence 在系统中单调递增，因此当前窗口允许因跨 scope 写入、low/high 滚动和 principal 滚动而稀疏。`dropped_records=total-visible` 解释窗口外前驱，不能要求所有相邻可见 sequence 都直接 hash 相连。

每个 scope 同时维护按 `sequence` 和按 `(tick, sequence)` 排列的两个 128 槽索引。新记录发布和旧槽淘汰都通过统一 unlink/publish 路径更新两份索引，ledger 的 `visible/oldest/latest` 因而可在 O(1) 时间得到，无需重扫物理 512 槽。audit/span/provenance 沿 sequence 索引单遍扫描，timeline 对 Context、sched、audit 和 prefetch 四个有序来源做四路归并，不再为每条输出重新选择全表最小项。需要扫描的计数查询按候选扫描上界计费；每 16 条换算一批预算，单次 checkpoint 不超过一个工作量子，并在每次让出后重计来源、补足增长差额。所有预算让出都发生在读取开始前，因此 timeline wait 不会在“扫描未命中”和“登记等待者”之间新增丢唤醒窗口。实现不增加公共查询 ABI，也不向低权限调用者暴露其他 scope 的观测负载。

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

可信 metadata 在用户态发布前完成加载尝试和可信判定：`main()` 在 `fsinit()`、`timer_init()` 后立即运行 `agent_storage_init()`，随后才启动运行时 I/O policy 并加载首个用户程序。单个 bank 损坏时选择另一份可验证副本并标记恢复；不存在有效 bank 或选择失败时系统继续启动，但 metadata load/persist/init API 进入 fail-closed，不能以空表继续授权。scope retirement 则保留一条不依赖损坏 bank 内容的 VFS 清理路径：依赖、动作、缓存、审计、租约等可见内存状态和真实 VFS-labelled 文件仍按 scope 回收，完成后释放生命周期身份。当前没有动态注入启动 bank 损坏，不能把正常加载回归外推为该故障路径的运行验证。

metadata 内存表、索引、inode sidecar 和持久化由同一可睡眠事务门保护。进程请求采用单调 ticket FIFO：无排队者时可直接取得门；一旦领取 ticket 就不可中断地等到自己的 turn，若期间收到退出请求，也必须先取得并立即传递事务门，避免遗弃 ticket 阻塞全局。最外层释放只唤醒 FIFO 队首。真实 VFS callback 仍不能插队；scheduler 则可在门恰好空闲时取得一个硬有界维护轮次，且该保留轮次解锁时不会重复唤醒已由前任唤醒的 serving ticket，因此既不破坏 ticket 与睡眠队列的对应，也不会被持续进程流量永久饿死。进程态查询、索引、显式依赖、action 与预取等可扩展扫描按 128 records 向 `kernel_work_checkpoint_cleanup()` 计费；scheduler 只执行每轮最多 16 个目录项或一个持久化状态机步骤。依赖表只保存每 scope 有配额上限的显式用户边；文件 `dependency_mask` 是兼容的规范输入，由查询、action 与预取在既有固定表遍历中按需解析。结构变化只推进依赖代数，纯状态 action 不触碰该代数，从机制上删除了 Recovery 可触发的门内超线性派生重建。`ACTION_COMMIT` / `RERUN_STAGE` 先生成固定选集，再一次更新每个槽、一次维护 status 索引和一次登记写回；查询预取使用精确 hit slot、一次文件表扫描、目标位图去重和 8 条发布上限。跨 Agent handoff 进一步把不受信任生命周期的 PCB 指针替换为稳定端点句柄，所有可能触发 checkpoint 的扫描只用局部副本，固定表扫描先计费、端点再校验、最后无调度提交，消除了依赖重建放大、按目标重复放大和槽复用污染三类路径。

可能替换物理 COW job 的同步 set/delete/init/reload 仍另进入单调 ticket 的 FIFO submit lane。条件失败检查、事务门释放和 submit queue 入队期间保持中断关闭，消除 unlock-to-sleep 丢唤醒窗口；reload wait 使用同一协议。submit ticket 同样不能在退出时放弃。持久化跨预算等待时 immutable job 保持同一 `job_id`，后来提交者不能替换。VFS callback 与 scheduler 只做非阻塞尝试，不能在持有底层文件系统状态时形成反向等待。

低权限 workflow Agent 的普通文件 create/write/truncate/delete 也不再同步调用完整 metadata bank 持久化。write/truncate 把已提交的 inode size、更新时间和文件代数先写入按 incarnation 绑定的 sidecar，查询立即覆盖旧主表；create/delete 先完成内存记录变化。只有带 `PERSIST` 的记录才增加该 workflow scope 的 dirty generation，volatile 文件的微写只改变内存/sidecar，不触发不包含该对象的空 bank checkpoint。首个脏变化开启固定一秒的非滑动窗口，后续微小变化只累计 coalesced 计数，不刷新 deadline。scheduler 在事务门空闲时每轮推进一个 checkpoint state；dirty scope 轮转成为该 job 的稳定 sponsor，实际物理传输受它的硬 `BACKGROUND` burst/refill 限制。提交成功或失败后只设置固定合并窗口，不再按 checkpoint 执行耗时延长休整；设备退化产生的占用和重试速率分别由 I/O debt 与固定 not-before deadline 约束。到期写回每轮先于扫描获得独立机会。提交只确认快照期间未继续变化的 scope，失败或新写入都保留待办。正常 sidecar 发布即使遇到 metadata 锁竞争也不会升级为全目录扫描，只有绑定缺失等无法局部表达的状态才进入协调扫描。查询缓存 generation 同样按 scope 隔离，SYSTEM 对象变化才跨域失效。显式持久 metadata 管理、首次 bank 安装、reload 和 scope retirement 同步进入 FIFO submit lane 并建立不可替换的持久化任务；这保证有序接纳，不把 syscall 返回描述为 primary 已完成回读验证的持久化屏障。显式 set 对已有路径只做无副作用的 inode 探测，参数冲突或失败不会因预协调而提前改写 metadata。

协调扫描本身也有独立的服务边界。`pending` 是一位合并状态，首次启用可立即扫描，后续请求既不能把 cooldown 提前，也不能持续后移首次到期时间；active 期间任意数量的绑定失败最多排队一轮。后台在争用 metadata 事务前先检查 not-before deadline，每 tick 最多处理 16 个目录项。完整扫描或加载/短读失败后都按 `max(20 tick, 4 * 本轮耗时)` 休整。因此某 scope 填满 metadata 配额后，即使低权限 Agent 持续微写经查询确认未绑定的超额文件，也不能把全根扫描变成无间隔全局事务风暴。

全局文件对象表进一步由进程资源域配额和普通/保留水位约束；`make file-resource-test` 已用 64/48/16/16 缩小配置在双目标验证隐藏临时引用、失败回滚、保留区进展和最终退款。每线程内核栈使用 16 KiB 栈槽和 4 KiB 未映射 guard，并在低端保留 canary；构建时由 `make kernel-stack-check` 拒绝超过预算或无法确定上界的调用链。

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

# 构建期内核栈预算
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
make -C baseline_ucore kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`scripts/run-agent-tests.sh` 当前包含 16 个程序，并继续以 `agentscope_ucore`、`agentsecurity_ucore`、`agenttrust_ucore`、`agentvfs_ucore`、`iobudget_ucore` 和 `usersafety_ucore` 覆盖安全机制。Workflow 强制撤销实现快照通过完整 16/16，墙钟约 `371.5s`，其中 `agentscope_ucore` 约 `127.9s`；内核栈预算为 `13824 < 16384`。子代理审查后补强了阻塞成员 EOF 防伪、超过生命周期上限的 9 轮回收、factory 关闭时低权限成员持有资源，以及非规范 64 位 scope id 拒绝；这些最终修改又以 `CASE_TIMEOUT=260s AGENT_TEST_CASE=agentscope_ucore bash scripts/run-agent-tests.sh` 专项通过，耗时约 `126.1s`。该轮输出 `scope_close_authority=1`、`scope_controller_exit_revoke=1`、`scope_forced_cleanup=1`、`scope_replacement_admitted=1` 和 `parent passed`。此前观测查询、tiny policy 线程资源、双目标进程回收、syscall 公平性和 filepool 资源脚本继续按历史轮保留；审查后未重跑完整 16 项，`full-verify` 尚未运行。

这些专项入口检查的是机制约束。`make dual-platform-run` 继续验证科研平台功能等价和 AgentOS 专属证据，`make full-verify` 串联宿主机、双目标、Agent、进程生命周期、线程资源域、syscall 公平性和全局文件对象表配额检查；ENOSPC 与内核栈预算仍保留为可单独复现的专项入口。

## 10. 维护要求

- 修改 syscall、VM、文件、同步、进程退出或调度路径时，必须运行对应安全专项测试。
- 新增 Agent role、capability、可信程序或 workflow 文件类型时，必须同步更新执行清单、VFS profile、负向测试和本文档。
- 新增安全域成员发布或凭据转换路径时，必须接入 ACTIVE/CLOSING 发布检查；新增阻塞点必须响应进程级退出请求，并由当前线程沿正常清理路径释放临时资源。
- 通用安全机制若同步到 `baseline_ucore/`，必须保持两侧语义一致，并在双目标文档中说明它不属于 AgentOS 实验变量。
- 新的可恢复资源不足路径必须返回错误并回滚，不能把普通用户可触发的条件写成 `panic()`。
- 调整 `io_policy.h` 的 budget 或 cache floor/cap 时，必须同时保持 4 active + 8 retiring BACKGROUND 的保守静态 envelope、普通流量设备根 bucket 与 `NBUF` 静态断言成立，并复测 PUBLIC 压力、多 workflow、SYSTEM/workflow BACKGROUND、retiring 3/8、shared 排队轮转和保护流量带债进展。
- 文档不得把共享加固 baseline 描述为未修改的上游 uCore，也不得把通过结构扫描解释为完整运行验证。
