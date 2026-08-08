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

## 3. 风险与机制总览

| 风险 | 当前机制 | 验证合同 |
| --- | --- | --- |
| 坏用户指针、非法长度或资源不足破坏内核状态 | 统一使用 `copyin()`、`copyout()`、`copyinstr()` 和长度上限；副作用前完成校验，失败路径回滚未发布对象 | `usersafety_ucore`、`fsenospc_ucore` |
| 全局唤醒破坏无关等待者 | mutex、semaphore、child、Agent event、timeline 等对象各自维护定向等待队列 | `usersafety_ucore`、`procreap_ucore`、blocking semantics case |
| 深调用链或长 syscall 越过公平边界 | 内核栈 guard 与构建期调用图预算；timer 发布 `need_resched`、工作额度和可提交安全点 | `make kernel-stack-check`、`make syscall-fairness-test` |
| 普通进程伪造 Agent 身份、角色或系统事件 | 可信映像 grant、role/capability、active scope 和对象 owner 联合授权；公共 wake 只能投递普通消息 | `agentsecurity_ucore`、`agenttrust_ucore` |
| 文件描述符或路径绕过 workflow 隔离 | inode 安全标签、`dev + inum + incarnation` 身份、逐操作 VFS 检查和一次性 pipe 委派 | `agentvfs_ucore`、`agentscope_ucore` |
| workflow 根退出后成员或降权后代继续持权 | 不可变 `(id,generation)` lifecycle、唯一 controller、ACTIVE/CLOSING/RETIRING 状态和统一 teardown | `agentscope_ucore`、`make workflow-teardown-race-test` |
| 进程、线程、file object 或 Agent 状态耗尽全局保留量 | generation-safe EXEC account、ordinary/reserved 水位、原子向量预留与退出结算 | proc-reap、thread/file/physical resource runner |
| PUBLIC 存储或 I/O 压力侵占 workflow/SYSTEM | 稳定 STORAGE principal、block/inode 配额、owner/class I/O lease、cache floor/cap 和 debt settlement | fs quota、`iobudget_ucore`、VirtIO/allocator fault runner |
| 高频文件变化放大全局 metadata 工作 | incarnation sidecar、scope-local dirty generation、固定合并窗口、分块 COW bank 和有界 scanner | `agentfs_ucore`、`agentscope_ucore`、metadata recovery runner |
| IPC、事件或 telemetry 被单一来源耗尽 | stable control-id route、分类/来源配额、内核 origin 保留、scope-local audit 分区 | `agentloop_ucore`、`agentsecurity_ucore` |
| 观测查询形成无预算全表重扫 | scope-local 有序索引、单遍或多路归并、候选预付 kernel-work | `agentscope_ucore` 与 observe recovery runner |
| 构建日志、静态 marker 或页面状态冒充 Guest 结果 | clean/build/guest 分阶段判定；Guest 日志完整匹配；release bundle 绑定源码、工具与原始材料 | Host runner tests、`make full-verify` |

表中只列当前机制和验证入口，不表示动态通过。Agent case 以 `ci/kernel-budgets.json` 为准，正式结果只从 [正式证据索引](../../evidence/releases/INDEX.md) 指向的 bundle 读取。

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

活子进程只用子 PCB 的 `parent` 指针表示父子关系，父进程以 `live_child_count` 为其未来退出结果预留容量。退出时 pid 和状态进入父进程私有的紧凑 `child_exit` FIFO，发布不会因临时分配或全局表压力失败；`wait()` 常数时间取队首，`waitpid()` 只做固定上限的队内删除。父进程未等待时，退出结果仍保留，但子进程的执行槽、内核栈、页表和文件引用可以先释放。完成环与活子计数共享固定私有配额，恶意父进程只能耗尽自己的配额。

### 5.3 通用资源账户与系统保留

`os/resource_controller.[ch]` 是通用资源计费唯一事实源。账户句柄为 `{slot,generation}`，种类分为 EXEC 与 STORAGE，状态为 FREE/ACTIVE/CLOSING/DRAINING；资源种类覆盖 PROCESS、THREAD、FILE_OBJECT、FS_BLOCK、FS_INODE、BUFFER_CACHE 和 AGENT_STATE_PAGE。每项 charge 还区分 ordinary/reserved，账户上限、普通全局水位、系统保留和总容量在同一次 admission 中检查。成员、durable usage 或 pending reservation 尚未归零时，CLOSING/DRAINING 账户不能复用；I/O 速率状态由 BIO owner 生命周期单独管理。

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

编辑版本和内容版本不再分别从两个“先到先得”的全局池分配。统一版本 sidecar 改为 512 槽稀疏哈希表，只为实际进入 Agent metadata 路径的文件分配状态；查找键包含 `dev + inum + incarnation + scope + lifecycle generation`，槽位由空闲链表回收。它不再按全部磁盘 inode 预留数组，也不会让旧 workflow generation 命中新 workflow 的同号 inode。

删除目录项不是文件生命期终点，因为仍可能有打开的描述符继续访问 inode。实际清理挂在 `iput()` 的最终回收分支：只有链接已删除、最后引用释放，且 `inode_remove_detach()` 与 `itruncate_reclaim()` 完成后，才在清除 inode 身份之前从哈希桶摘除该 incarnation 的版本、活动租约和 digest cache，再把槽归还空闲链。新 incarnation 首次触达同一物理 inode 时还会撤销旧临时状态；仅持有旧身份的提交或租约过期路径只能查找，不能重建已经死亡的版本状态。容量仍由 metadata catalog 上限和稳定存储主体的 inode 配额共同约束。

### 5.8 块 I/O 速率预算与 buffer cache 保留

块设备服务使用与持久存储一致的稳定 owner：安装级 PUBLIC、SYSTEM，以及每个内核签发的 active 或正在清理的 workflow。BIO 自己持有 owner/class、shared 和 device bucket；两级 reservation 在同一关中断窗口扣款，commit/cancel 各恰好一次，`last_refill_tick` 在访问时惰性补充信用。通用 `resource_account` 不再为所有账户携带 I/O lane 或全局 lease 表。PUBLIC 使用 `NORMAL`，workflow 的 Orchestrator/Recovery 使用 `CONTROL`，其他 workflow 使用 `NORMAL`；metadata、scanner 和 scope reclaim 显式建立 SYSTEM 或触发 workflow 的 `BACKGROUND` job。

每个活跃 owner/class 有受保护的 burst/refill bucket，另有 shared 与 device 根。无竞争传输依次消费 reserved、shared；异域 owner 活动、排队、retire/quiesce 或已有 debt 时停止借用，`BACKGROUND` 不借 shared。一次 admission 同时预留来源 bucket 和 device bucket，提交/取消各恰好一次；shared 永不带债，有界原子请求只在请求上界内形成 owner/device debt。bucket 通过 `last_refill_tick` 惰性补充，debt bitmap 只推进实际有债务的 lane，空闲系统不再周期扫描所有通用账户。只有真实 `disk_submit` 发布物理计费；volatile overlay 命中不计，写回块和最终 FLUSH 逐次计费。具体 burst、refill 与保护上界以 `io_policy.h` 为准，文档不复制易漂移的数字。

设备根 bucket 的 burst/refill 为 560/280。编译期分别验证所有 reserved lane 的最坏总和 528/264 和 shared gate 各自不超过设备 envelope；二者不能相加，因为 shared 每次同时扣根。reserved 根透支由真实 account token 背书并由后续 refill 先偿还。8 槽 lifecycle ledger 的静态 cleanup 预算不表示运行时可同时 admission 8 个 workflow；冻结期 ACTIVE/CLOSING/RETIRING 合计最多 4 个。

只有 token/debt 账本还不够：如果唯一 runnable 线程反复在内核态 pipe 条件路径 `yield()`，旧 scheduler 可能一直在关中断状态重新调度同一线程，使负责 refill 和设备完成的 pending timer/device interrupt 没有交付机会。现在每轮选择线程前都把执行身份切到 idle context，安装 kernel trap 向量并短暂打开中断，随后再关中断进入后台维护和原有调度选择。这是所有调度轮次共享的机制边界，不依赖 PID、文件名或 syscall 特判。

正常 `exit()`、主线程 page fault/非法指令、workflow revoke 和未发布构造回滚共用 5.1 的 teardown 状态机；非主 sibling 仍只经 `thread_exit_current()` 回收自身。SETTLING 阶段建立 cleanup kernel-work 与 I/O request，使 fileclose、未链接 inode 回收等真实 I/O 继续按原 STORAGE account/class 归因；随后提交残余 lease 并结算 owner/class debt。HANDOFF 阶段调用 `vfs_proc_terminal_clear()` 清除凭据，再由 `vfs_proc_lifecycle_release()` 释放不可变 lifecycle 引用；scheduler 已切到 idle stack 后才释放最后物理栈页。`proc-reap`、`agentscope_ucore` 和 `iobudget_ucore` 定义了该机制的动态合同；当前发布状态只由对应 bundle 判定。`fault_exit_cleanup=1` 只覆盖 fault 分支，不能单独代表状态机所有内部阶段。

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

所有执行直接投递的数据面入口复用同一交付函数：`agent_wake()`、`send_message`、非零 target `llm_request` 和 `llm_response` 均先核对 stable route，再检查 watch、队列预算并入队；`llm_request(target=0)` 只记录摘要。未授权、目标不存在、watch 不匹配或资源不足都不会留下旁路副作用。`LLM_RELAY` 仍负责“谁可产生 LLM 结果”，route 负责“结果可发给谁”，两层授权缺一不可。

目标事件队列仍是 16 槽 FIFO，但每个槽用内核私有 accounting flags 编码 origin/resource class，并保存 stable source control id。资源边界分三层：所有带 Agent 来源的 external 事件合计最多 12 条；directed IPC（`MESSAGE` / `LLM_DONE`）和 attributed notification（`FILE_STATUS` / `JOB_DONE` / `POLICY_DENIED` / `CONTEXT_LIMIT` / `DASHBOARD_EXPORT`）各自最多 8 条；同一个 stable source 跨两类合计最多 4 条。用户可见 `source_pid` 不参与配额身份判断，攻击者不能换事件类型或利用 PID 复用刷新额度。

external=12 的 admission 上限为显式 `KERNEL` origin 保留至少 4 个容量名额。heartbeat TIMER 由内核专用的 `INTRINSIC_COALESCED` delivery policy 产生，越过 external 和 watch 过滤，并用私有槽标志限制为同类型最多一条 pending；用户 syscall 不能选择 origin 或 delivery policy，也不能用相同 payload 冒充该路径。带 Agent source 的文件状态、作业完成和拒绝通知即使事件类型属于系统通知，也仍按 attributed external 计费，不能侵占保留量。所有 origin 最终仍受 16 槽总容量约束。`LLM_DONE` 虽由 `LLM_RELAY` 专用工具产生，但它是定向 IPC，既需要 route，也计入 directed/external/source 三组配额。

`agent_wait()` 采用 reserve-copyout-commit 交付协议，而不是在找到事件时立即出队。内核以 event-id cookie 原子保留精确队首事件或 cancel token，释放短临界区后完成用户 copyout 和 Context attribution，再重新核对 cookie 后提交消费、配额退款及下一 waiter handoff。copyout、lane 或归因失败会 abort 保留并定向唤醒等待者，事件/cancel 继续可见；同一槽同时只能有一个消费者。静态与 mutation 合同覆盖该状态机；“reserve 后用户页失效 + sibling waiter/cancel/teardown”四路 Guest 组合仍是显式验收边界，只有 release bundle 实际包含对应 profile 日志时才能称为动态覆盖。

历史 `mailread/mailwrite` 只保留 syscall 编号和用户态包装，内核恒定返回 `-1`，不解析 PID、不访问用户内存，也不分配队列。这样旧二进制得到稳定失败，而不能绕过 scope、route、watch 与 Agent 事件配额。

删除 legacy 两页 sidecar、端点代际和独立资源结算后，消息只由 `agent_ipc` 的可信事件队列承载。需要跨安全域协调的测试和控制路径使用显式一次性委派 pipe，不借裸 PID mail。

广播系统事件逐 watcher 独立尝试。某个慢订阅者的 external admission 已满、总队列已满、watch 不匹配或已经退出时，内核继续投递后续目标；文件 metadata 等权威状态一旦提交，不会因为一个通知接收者资源不足而错误返回 `NO_SPACE`。这种 best-effort 通知语义把控制状态提交与观察者背压分离，避免单个低权限 Agent 阻断全局工作流。

当前路由授权表是 scope-local 内核私有执行状态，尚无 snapshot/query ABI，grant/revoke 也不追加 Context 或 audit。现有审计只能观察授权之后同 scope 的事件入队和消费，不能完整重建路由策略变更历史。若后续需要运维审计，应新增只读快照和受权的 route-change audit record，而不是把用户日志当作权威控制面记录。

### 6.4 Workflow scope factory 与对象所有权

scope 编号由内核定义：0 是 PUBLIC，1 是只读可信 SYSTEM，3 及以上是动态 workflow；数值 2 保留给稳定 PUBLIC 存储 principal，不是 workflow scope。只有非 Agent、具有 resource-domain admin 且仍运行可信 bootstrap 映像的 factory 可以调用 syscall 541 `agent_workflow_create(role)` 建立新 scope。普通 role grant 只允许在当前 scope 内调用 `agent_create_role()`，即使 orchestrator 有全部业务 capability，也不能用它铸造新对象域和新配额。

同 scope Agent/worker 继承 VFS scope，但不是同一个安全主体；workflow 或可信 bootstrap 动态 scope 的降权普通 fork 会清除 Agent/VFS 凭据，却仍通过不可变 lifecycle key 留在原 workflow 的终止谱系。pipe 不按环境状态自动传播：syscall 542 `agent_scope_delegate_fd(fd)` 只接受 pipe，并把一次性票据绑定到调用线程的下一次安全主体创建。成功子进程只获得端点、不获得票据；失败与 exec 都撤销票据。普通 PUBLIC 父子只有在本来就不属于 workflow lifecycle 时才保持普通 POSIX 继承语义。

敏感对象身份按类型组合 scope 和 stable owner：文件/metadata/租约以 `scope + dev + inum + incarnation` 为基础，action/dependency/cache 先按 scope 分区，IPC/wait control 使用同 scope stable control id，span/audit 使用 scope + 公开 span + 私有 span owner/cause principal。capability 只在当前 scope active 且对象 owner 精确匹配时生效；SYSTEM 仅在显式只读路径可见。

新 workflow 根在发布为 runnable 前取得不复用的 `agent_control_id`，并绑定当次 admission 的 `(workflow_lifecycle_id,generation)`。只有仍持有该根标记且 control id/lifecycle key 一致的进程，或仍运行可信 bootstrap 映像的 factory，才能调用 syscall 545。后创建的 Orchestrator、低权限 Agent、同 PID/PCB 槽或单独的 `ORCHESTRATE` capability 都不获得关闭权。control id 不复用；lifecycle 槽可回收但 generation 必须增长，两者不要混为一个身份。

syscall 546 只允许进程取得自己的 lifecycle/runtime 快照或比较自己的 expected key。它没有 PID、scope 或任意 ledger 查询参数，并采用版本化 sized-prefix copyout；坏指针、非法 flags/key 在写输出前失败。合法 `STALE/NOT_FOUND` 可以同时返回 self snapshot，便于测试识别重用而不扩大权限。`(id,generation)` 仍只是不可变身份/比较值，不能替代根 control id、factory authority、capability 或对象 owner。

显式关闭与根进程正常退出、fault 退出及 terminal credential clear 走同一个幂等 controller-departure 路径。ACTIVE 原子转换为 CLOSING 后形成不可逆的授权与发布屏障；scope acquire、storage reserve、spawn 和 pending exec commit 均拒绝。成员扫描匹配不可变 lifecycle key，而不是可清除的当前凭据，故 PUBLIC 降权子孙不能逃逸。扫描调用统一 teardown request，不覆盖已经取得 `teardown_owner_tid` 的进程；成员在自身上下文关闭 FD、释放 inode/VM/sidecar，并结算 resource account、BIO 在途请求与 debt。exec 的 `prepare/commit/abort` 在发布边界再次核对 lifecycle 状态。

最后成员释放引用后才进入 RETIRING 并撤销 active cache floor。ledger 在该 generation 的退休工作完成前保留 key；轮转 reaper 使用 BACKGROUND 预算清理 metadata、dependency、action history、edit lease/version、query/digest cache、audit、IPC 和普通文件，再释放 STORAGE account 与槽。下一 workflow 即使复用相同 slot/id，也具有更高 generation，不能重新解释旧状态。

### 6.5 审计分区和可信因果

物理审计表 512 槽按最多 4 个 workflow 各保证 128 条，每 scope 分成 low 64 和 high 64。Context/event/sched/manual 等遥测始终 low；每个 active stable principal 在 low 中保证 8 条，其他份额空闲时可借用到 16 条。low 满且新主体到达时先回收已离开主体，再只回收其他主体高于 8 条的借用溢出，并继续沿既有因果 victim 规则选择记录；任何 active 主体的 8 条保证不会被借用者挤走。causal victim 的 scratch 同样覆盖完整 16 条 burst，因此第 9 到 16 条中的重复 span/kind 也会参与冗余判断，不能因旧 8 槽辅助上限而隐藏。只有 syscall/tool 成功后由内核确认的特权状态效果进入 high；high 依据每 scope 8 个保留进程份额为每 active principal 保留 8 条。high 满时只滚动当前 principal 自己的记录或回收已退出/inactive principal 的最旧记录，绝不牺牲另一 active principal 的 protected evidence。inactive 历史仍是有界窗口，回收量通过 `dropped_records` 可见。

公开 `span_id` 和 `cause_sequence` 是呈现字段，不是权限票据。内核为每个 Context/event 保存私有 span owner、source control 和 source pid；`context_push()` 要求用户输入的 cause/span 都为0，再由内核连接当前链。每 scope 独立维护 ledger hash，但 sequence 在系统中单调递增，因此当前窗口允许因跨 scope 写入、low/high 滚动和 principal 滚动而稀疏。`dropped_records=total-visible` 解释窗口外前驱，不能要求所有相邻可见 sequence 都直接 hash 相连。

持久观测格式已经升级到 checkpoint v8。durable section 提供四个普通 workflow 槽和一个 Recovery 保留槽，普通容量与 `WORKFLOW_LIFECYCLE_MAX_ACTIVE` 对齐。每个 scope 的 6 个记录槽固定由 latest tail 4 条和最多 2 个 causal diversity anchor 组成，anchor 按 identity class、kind、stable principal 与可信 span 选择，再与 tail 按 sequence 排序；这是一条显式稀疏链，不是“最新连续后缀”。磁盘 entry sidecar 保存 `identity_class`、`link_flags`、`principal`、`span_owner` 和 `receipt_id`，`PREV_RETAINED` 仅在相邻保留项确为直接 hash 前驱时置位。scope 的 `admission_drops` 记录 sequence/hash 分配前的准入拒绝；成功建链但未被持久窗口选中的数量另由计数关系推导，公共 `dropped_records` 仍聚合两类缺失。

恢复先验证完整 v8 checkpoint 的 header、保留零值、scope/entry、sidecar 组合、全局 sequence/receipt 唯一性、链间隙、链尾和全部 lease 高水位，再为每个空 live scope 预检槽位，并在同一关中断窗口发布索引、计数与 receipt；中途失败回滚本轮插入，已有 live 证据不被 reload 覆盖。durable store 以 active generation replication fence 区分“primary 已发布”和“双 bank 已复制”：绑定覆写目标时在 `INVALIDATE` 前撤销，repair 与 fail-closed 同样清零，boot 只在双 bank 一致时恢复，mirror `COMMIT` 验证后才发布新 generation。live sidecar 已淘汰的 `target == 0` receipt 在精确 entry 扫描前和 generation 二次确认后各检查一次该 fence，禁止 primary-only 记录误报 `DURABLE`。REAP 的授权和擦除仍使用两阶段状态机，但其控制写通过通用 durable `URGENT` flag 和 store `expedite` 把到期时间提前，retry 也复用同一 notify 路径。普通 receipt 继续以 flags=0 使用既有 serial fence 和合并窗口，不能借 REAP 之名把低权限观测写放大成全局紧急 I/O。

每个 scope 同时维护按 `sequence` 和按 `(tick, sequence)` 排列的两个 128 槽索引。新记录发布和旧槽淘汰都通过统一 unlink/publish 路径更新两份索引，ledger 的 `visible/oldest/latest` 因而可在 O(1) 时间得到，无需重扫物理 512 槽。audit/span/provenance 沿 sequence 索引单遍扫描，timeline 对 Context、sched 和 audit 三个有序来源做归并，不再为每条输出重新选择全表最小项。需要扫描的计数查询按候选扫描上界计费；每 16 条换算一批预算，单次 checkpoint 不超过一个工作量子，并在每次让出后重计来源、补足增长差额。等待匹配的查询只在最后一次预算让出完成且来源上界已经被覆盖后采样 `scan_epoch`；后续未命中扫描到 waiter 发布之间，再以关中断的 epoch 重检和 keyed 入队闭合，因此预算公平点不会制造丢失唤醒。

SCHED 记录的 ring 归 timeline owner，而不是调度 core。core 只构造采样；观测 facade 先检查线程级 suppression，再按“ring commit -> epoch advance/定向 wake -> audit publish”的顺序发布。这样被抑制的内部持久化工作不会留下可查询 SCHED 记录或推进其 epoch，也不会出现 ring 已可见而等待者仍依赖旧 epoch 的状态。当前 checkpoint 格式的动态等级须由最终提交绑定的 release bundle 决定。实现不增加公共查询 ABI，也不向低权限调用者暴露其他 scope 的观测负载。

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

类级公平只能约束两次调度之间选谁，不能约束一次 syscall 在内核中运行多久。`os/kernel_work.c` 采用与 Linux `need_resched` 相同的发布思路：timer 到期只设置当前线程的待调度位，不在中断、VirtIO 或持有临时对象的路径直接切换；可提交安全点读取这个位和 512 个工作单位，再通过正常 `yield()` 让出。当前每单位按 64 字节工作量折算，单次 syscall 的硬边界为 32 KiB；操作类安全点另以固定单位覆盖不按字节计数的循环。每次 dispatch 清空待调度位并重置工作额度，不再在每个 checkpoint 读取 cycle 或维护重复的周期 deadline。线程在 syscall 内被重新调度会留下 resumed 标记，下一 checkpoint 明确返回“已经发生调度”，避免恢复后继续无界推进。没有待调度、恢复或额度耗尽时，syscall 尾部走常数时间快路径；测试只读取上一调用的重调度次数，不再维护独立回执协议。

syscall 入口还把“可能触盘”和“需要文件系统 epoch”收敛为一个只计算一次的 policy 位图。普通短 syscall 直接取得零策略；`read`、`write` 只固定一次文件对象并按 inode 类型补充策略，`openat` 只在 create/truncate 时补充 epoch。准入、失败回滚和结算共用事务上下文中的同一 policy，删除了两套大 switch 和重复 FD 分类；未知 syscall 仍按保守策略进入慢路径。

安全点只放在可提交边界：控制台按已输出的 64 字节、pipe 按已发布的传输块、exec 和 fork 按已完整处理的页面、`agent_run()` 按已完成的 operation、普通 FD_INODE 读写按单个块边界计费。fork 在开始逐页复制前建立进程级 VM snapshot 屏障，调度器暂缓同进程非 owner 线程，同时继续调度其他进程；失败回滚也使用不可取消 cleanup checkpoint。inode 路径在更新共享文件 offset、一次性内容版本和 Agent size 发布 sidecar 后才检查；sidecar 按 inode incarnation 非阻塞发布，查询和 metadata bank 快照都会覆盖事务表中的旧 size，持久化完成时再用 sequence 判断是否仍有并发更新。这样 metadata 事务忙或写线程退出也不会把已提交 size 隐藏在旧记录后。一旦 checkpoint 报告发生过调度，inode 路径就以合法短读或短写返回已提交前缀，让用户态重新发起操作，而不是跨调度继续使用旧 offset。新增长循环必须声明工作单位和原子提交边界并接入同一 checkpoint，不能自行判断 PID、角色或 syscall 编号。

`O_TRUNC`、unlink 后的最终 `iput()` 和最后一次 `close()` 还涉及可跨调度的资源生命周期。truncate 先从 inode 原子 detach 被丢弃的直接/间接映射并提交新 size，再从私有 reclaim 描述中分批释放块；cleanup checkpoint 即使进程已经收到退出请求也继续完成这批无主资源的回收。`fileclose()` 在最后引用消失时先把类型、inode、pipe 和资源域计费信息复制到本地快照，原子发布空闲全局文件槽并退款，之后不再访问可能已被复用的槽。`fileopen()` 则先占用不可读写、不可继承的进程 FD reservation，待 `O_TRUNC` 回收和文件初始化全部完成才安装真实文件，失败时统一释放 reservation。这些规则在 AgentOS 与 baseline 通用路径保持一致。

当前专项验证覆盖上述可扩展数据、装载和回收路径，但不宣称已经穷尽任意 syscall。目录 scanner 仍有固定的每轮条目上限；metadata raw I/O 和 scope 文件清理已经改为可恢复 background step，并同时服从内核工作 checkpoint 与块 I/O debt checkpoint。后续新增可扩展循环仍必须声明原子提交边界，不能把分域 I/O admission 当作替代 CPU 安全点的机制。

文件系统对 inode、inode cache 和数据块耗尽返回失败，回滚未提交状态并准确报告短写；稳定存储 principal 配额和分级水位进一步保证 PUBLIC 压力不能触及 workflow/system 保留量。Agent 文件版本 sidecar 只为进入 Agent metadata 路径的存活身份分配，并随 inode 最终回收或 workflow retirement 归还稀疏槽；短命文件不能留下旧 generation 的版本状态。`fsquota_ucore` 验证同一运行中的 PUBLIC 压力、释放复用及 workflow/system 保留量；双目标 `fspquota_ucore` 的既有 crash/seed/verify 轮验证持久计费与 qmap-first 恢复，但本次 grouped claim 尚未做中途掉电注入。

metadata 后端采用 generation + payload hash 的双 bank 提交：目标 bank 先发布无效 header，再只写变化的 payload segment；变化段逐段读回比较、整体摘要一致后才发布 header，header 回读一致后才切换 active generation。新的 primary 完整验证后，状态机才允许用同一不可变快照覆盖旧 bank 作为 mirror；因此 primary 验证前保留旧代，mirror 阶段失败则仍保留已验证的新 primary，不宣称任意故障下两个历史代都完整。逻辑缩短复用已有高水位块，不同步 truncate。payload hash 只作为一致性摘要，不是抵抗恶意磁盘篡改的密码学认证。

可信 metadata 在用户态发布前完成加载尝试和可信判定：mkfs 通过内核、构建器和 Host probe 共用的纯磁盘 ABI，预装两个字节一致、完整预分配且标记为 `KERNEL_PRIVATE/SYSTEM` 的 v8 generation-1 canonical 空 bank；`main()` 在 `fsinit()`、`timer_init()` 后立即运行 `agent_storage_init()`，随后才启动运行时 I/O policy 并加载首个用户程序。运行时不从“没有 authority”推断首次启动；稳定状态没有任何有效 v7/v8 bank，包括双 `ABSENT`、双 `UNCOMMITTED`、双损坏或其混合，均 fail closed。v5 及更旧版本不再由内核解码，直接按损坏关闭；本仓库不提供原地转换，升级时必须重建镜像。只要存在一份完整有效 bank，另一副本仍可按既有恢复协议修复。后台读取使用绑定 authority cookie、bank、inode identity/size 和首部字段的可恢复 cursor；每轮只推进 SYSTEM `BACKGROUND` I/O 预算内的前缀，最终重读首部并验证 payload。已经确定的 terminal bank 分类会缓存，候选 bank 仍须再次完整确认；确认时 generation/hash/migration、inode 或首部不一致都按确定损坏关闭，不会用无界 `INTERRUPTED` 重试掩盖竞态。双 bank 暂时 `BUSY/EIO/INTERRUPTED` 时 Agent admission 和持久身份租约保持 fail closed，但 timer 只在指数退避 deadline 到期时发布一次后台工作。真实 I/O 失败增加退避计数，已经取得前缀或 catalog prepare 进展的 `PROGRESS` 只在下一 tick 续跑，不增加失败次数。静态 seed 的信任边界是受控 mkfs 与普通进程不可访问的 raw disk/KERNEL_PRIVATE 路径，不是密码学签名；旧镜像必须重建。

这里的 checksum、header/payload hash 和 inode 标签校验只用于格式、一致性和意外损坏检测，都不是 MAC，也不证明镜像供应链可信。metadata genesis 的 authority 来自受控 mkfs 把 canonical bank 直接写入 raw image 并赋予普通进程不可访问的 `KERNEL_PRIVATE/SYSTEM` 标签；若构建器或 raw image 本身不可信，这些摘要不能提供攻击者篡改防护。

持久发布边界也不以一次块写完成为准。文件系统的前向状态转换在声明持久后必须通过 `fs_durable_barrier()`，由块层向已协商 `VIRTIO_BLK_F_FLUSH` 的设备提交 flush；设备不支持 flush、flush 失败或发布结果无法确定时，路径返回不确定状态或使相应 authority fail closed，不能把 volatile cache 当成已提交。动态 powercut profile 只能在这些 device-flush/durable-barrier 语义上，用认证 supervisor 对稳定 QEMU leader 发出一次 `SIGKILL` 并验证后续重启恢复。它不刷新或模拟物理控制器缓存，不等价于整机突然断电，也不覆盖永久介质故障。

验证后的原始 bank image 在一次候选 epoch 内保持只读；catalog prepare 只修改非 active bank shadow 中的私有 plan，按固定记录步长续跑，并绑定 candidate epoch、catalog generation、参数、指针和内容 hash。plan 完成并取得 catalog mutation fence 后才恢复单调 identifier floor；此后在 fence owner 内执行不再分配的投影，再安装 active shadow 和 generation。若已有 foreign fence，apply 在改写前返回 `INTERRUPTED` 以便重试，不能笼统称为“prepare 后绝不失败”。零身份的普通持久记录进入 derived quarantine，自动扫描或 SYSTEM 记录进入 pending reconciliation；普通查询看不到两类记录，scanner 使用专用只读视图单次协调，避免在加载门内执行 `records * directory` 查找。前台 scoped reload 使用可恢复候选，游标同时绑定 store authority、scope 和不可变 workflow lifecycle key；BIO 公平退让只保留同一绑定下的已验证前缀，绑定变化或非进展错误立即丢弃。用户库对 `RETRY` 做有限次 `sched_yield()` 后重启，既不要求大 bank 在一次 syscall 内完成，也不把 raw buffer 或未确认 catalog plan 暴露为 authority。重探不会创建 bank 或安装空表；同一启动内取得已验证快照、完成投影和必要副本修复后才开放 Agent 创建和查询。确认损坏仍永久 fail closed。scope retirement 则保留一条不依赖损坏 bank 内容的 VFS 清理路径：依赖、动作、缓存、审计、租约等可见内存状态和真实 VFS-labelled 文件仍按 scope 回收，完成后释放生命周期身份。

metadata 内存表、索引、inode sidecar 和持久化由同一可睡眠事务门保护。进程请求采用单调 ticket FIFO：无排队者时可直接取得门；一旦领取 ticket 就不可中断地等到自己的 turn，若期间收到退出请求，也必须先取得并立即传递事务门，避免遗弃 ticket 阻塞全局。最外层释放只唤醒 FIFO 队首。真实 VFS callback 仍不能插队；scheduler 则可在门恰好空闲时取得一个硬有界维护轮次，且该保留轮次解锁时不会重复唤醒已由前任唤醒的 serving ticket，因此既不破坏 ticket 与睡眠队列的对应，也不会被持续进程流量永久饿死。进程态查询、索引、显式依赖和 action 等可扩展扫描按 128 records 向 `kernel_work_checkpoint_cleanup()` 计费；scheduler 只执行每轮最多 16 个目录项或一个持久化状态机步骤。依赖表只保存每 scope 有配额上限的显式用户边；文件 `dependency_mask` 是兼容的规范输入，由查询和 action 在既有固定表遍历中按需解析。结构变化只推进依赖代数，纯状态 action 不触碰该代数，从机制上删除了 Recovery 可触发的门内超线性派生重建。`ACTION_COMMIT` / `RERUN_STAGE` 先生成固定选集，再一次更新每个槽、一次维护 status 索引和一次登记写回。

每个进程的 Context 写路径另有可睡眠、FIFO、可重入的 commit lane。sequence 接纳、工具执行、Context header/record 发布、Context syscall、IPC 状态记录、文件查询和 wait 归因都在同一 lane 中按序提交；若操作还需要 metadata，唯一锁序是 `lane -> metadata`，最终离开 lane 时断言没有遗留 metadata transaction。`agent_call_count` 统计已接纳并预留的调用序号，因此在途慢调用可使它暂时领先；`latest_sequence` 只推进到已经完整写入 Context 的提交水位。该机制解决并发调用的顺序/哈希一致性，而不是按工具 ID 加特判。

可能替换物理 COW job 的同步 set/delete/init/reload 仍另进入单调 ticket 的 FIFO submit lane。密封快照期间 catalog 可继续合并修改；同 owner 提交者只能有界协助旧 job，待其释放通道后再捕获本次目标，跨 owner、未密封或待重启任务不能接管。条件失败检查、事务门释放和 submit queue 入队期间保持中断关闭，消除 unlock-to-sleep 丢唤醒窗口；reload wait 使用同一协议。submit ticket 同样不能在退出时放弃。同步 catalog mutation 还取得绑定 `agent_metadata_txn_token()` 的 owner fence，并跨持久化 checkpoint 释放/重取 metadata gate 保持；所有权威写入口对 foreign writer 返回冲突或重试。undo token 绑定 fence token、slot、诊断 generation 和完整 post-record，恢复前重新检查 exact post-state、中央容量与唯一键。fence 只串行化写者，读取仍可观察到持久化期间的暂态 post-state，因此它不是 opacity 事务；越过不可逆 COW 边界后返回 `INDETERMINATE`，不能伪装成已经回滚，rollback 不变量失败则运行时 fail closed。持久化跨预算等待时 immutable job 保持同一 `job_id`，后来提交者不能替换。VFS callback 与 scheduler 只做非阻塞尝试，不能在持有底层文件系统状态时形成反向等待。

metadata set 允许自动创建真实 workflow 文件时，创建来源不是一个布尔猜测，而是 `existing`、`created`、`FS_CREATE_INDETERMINATE` 三态。只有 `created` 才把精确 receipt `(path, scope, dev, inum, incarnation)` 记入 catalog undo；回滚通过 `fs_rollback_created_workflow()` 重新查找并逐项核对同一目录项、scope、设备、inode 号和 incarnation，同时要求对象尚未绑定 metadata 且允许 unlink，绝不按文件名删除后来替换的对象。目录项发布或 durable barrier 之后无法确认结果时，`FS_CREATE_INDETERMINATE` 必须一路转换为 `AGENT_STATUS_INDETERMINATE` 并使 metadata runtime fail closed；它不能按“未创建”处理，也不能用普通 undo 伪造成功回滚。

低权限 workflow Agent 的普通文件 create/write/truncate/delete 也不再同步调用完整 metadata bank 持久化。所有 `agent_meta_slot/flags/version` 变化统一通过 `agent_file_state_set_index()` 校验、`iupdate()` 并在失败时恢复旧值；write/sync/truncate/delete 统一通过 `agent_fs_apply_inode_event()`，create 只在 VFS 成功发布后进入目录协调。write/truncate 把已提交的 inode size、更新时间和文件代数先写入按 incarnation 绑定的 sidecar，查询立即覆盖旧主表；create/delete 先完成内存记录变化。只有带 `PERSIST` 的记录才增加该 workflow scope 的 dirty generation，volatile 文件的微写只改变内存/sidecar，不触发不包含该对象的空 bank checkpoint。首个脏变化开启固定一秒的非滑动窗口，后续微小变化只累计 coalesced 计数，不刷新 deadline。scheduler 在事务门空闲时每轮推进一个 checkpoint state；dirty scope 轮转成为该 job 的稳定 sponsor，实际物理传输受它的硬 `BACKGROUND` burst/refill 限制。提交成功或失败后只设置固定合并窗口，不再按 checkpoint 执行耗时延长休整；设备退化产生的占用和重试速率分别由 I/O debt 与固定 not-before deadline 约束。到期写回每轮先于扫描获得独立机会。提交只确认快照期间未继续变化的 scope，失败或新写入都保留待办。正常 sidecar 发布即使遇到 metadata 锁竞争也不会升级为全目录扫描，只有绑定缺失等无法局部表达的状态才进入协调扫描。metadata 可见 generation 按 scope 隔离，SYSTEM 对象变化才影响其他可见域；文件查询不保存全局内核结果缓存，而是每次按当前 generation 执行 scan/index。显式持久 metadata 管理、reload 和 scope retirement 同步进入 FIFO submit lane 并建立不可替换的持久化任务；这保证有序接纳，不把 syscall 返回描述为 primary 已完成回读验证的持久化屏障。显式 set 对已有路径只做无副作用的 inode 探测，参数冲突或失败不会因预协调而提前改写 metadata。

协调扫描本身也有独立的服务边界。`pending` 是 resume、idle、普通 full restart 与 urgent full restart 的有界多级状态，首次启用可立即扫描，后续普通请求既不能把 cooldown 提前，也不能持续后移首次到期时间；active 期间任意数量的绑定失败最多排队一轮。catalog 或 AUTOSCAN 容量耗尽时，普通 VFS create 仍由独立 STORAGE 配额决定并保留 workflow 标签；无法物化的 inode 把 `agent_meta_slot=-1` 和 sidecar 版本持久化为 capacity deferred。write/truncate/delete 看到该状态且 scope 仍饱和时不会重复排队扫描。只有扫描已经标记该 scope 饱和且 catalog slot 确实清除后，scoped capacity-release 才允许一次 urgent 补扫；metadata gate busy 的 delete 没有释放容量，只排队普通协调扫描。若释放恰逢 active scan 后续读错，urgent full restart 的优先级高于 cursor resume，不能漏掉旧 offset 前的 deferred inode。后台在争用 metadata 事务前先检查 not-before deadline，每 tick 最多处理 16 个目录项。完整扫描或加载/短读失败后都按 `max(20 tick, 4 * 本轮耗时)` 休整。因此某 scope 填满 metadata 配额后，即使低权限 Agent 持续微写经查询确认未绑定的超额文件，也不能把全根扫描变成无间隔全局事务风暴。冷启动仍先淘汰 lifecycle 已失效的旧动态 scope，但 v7 快照只按稳定的 112 条硬分区等磁盘合同判定，不能用后来收紧的 96 条 AUTOSCAN 新增长度把同版本历史判坏。97 至 112 条旧 AUTOSCAN 完整加载且不静默删除；运行期以 old/new class delta 只准其保持或减少，精确回滚走独立硬边界复核。

scanner 的绑定回退使用 catalog 的单一 resolver，而不是另一套全表 name scan。selector 同时携带从 `DIRSIZ + 1` 有界缓冲区取得的 physical/logical path 和完整 `dev + inum + incarnation`；不同 key 落到不同槽、或 identity 只命中另一条路径时，scanner 不作猜测并安排重试。路径仍命中但 incarnation 已变化表示同名新对象：旧记录先被撤销，新对象取得新 FID。VFS 的 lookup、create、link、unlink 和创建回滚共用唯一 `fs_dirent_canonicalize()`：非空输入只取磁盘可表示的前 `DIRSIZ` 字节并补 NUL。因此历史长名和同一 14 字节前缀继续是同一 legacy dirent alias，但命中后仍必须通过目标 inode 的 policy、scope 和逐操作授权，alias 不扩大权限。create hook 只把这个真实 canonical key 交给 metadata resolver，不再将 raw 长名写入物理/逻辑索引而使 reload 或 rollback fail closed。

全局文件对象表进一步由 EXEC resource account 和 ordinary/reserved 水位约束。打开文件项使用紧凑标签和对象 union；普通 PCB 只保留一个 IPC/观测冷状态指针。Context detail/attribution 使用 9 页 sidecar，冷状态使用 1 页，另有 6 页用户只读可信视图和 1 页用户 cache。17 页作为一次 `RESOURCE_AGENT_STATE_PAGE` 请求原子预留、提交和退款，共 `69632` B（68 KiB）；冷状态与其余 Agent 状态一样只在 Agent admission 时分配，普通进程不承担。线程切换上下文与 syscall/BIO 冷态复用已有 supervisor-only trapframe 页，最终 PCB、线程池和文件池体积由内核预算约束。

当前六项总状态基线为每进程/全局池/ordinary 池/reserved 池/ordinary 域/reserved 域 `69632/8912896/6684672/2228224/4456448/557056` B；对应 CI 上限为 `73114/9358541/7018906/2339636/4679271/584909` B。9 页 Context sidecar 仍以独立指标观察，但不代表第二份运行时 reserve。逻辑 admission 也不是总内存 OOM 下的硬页保留，数值以 `ci/kernel-budgets.json` 为唯一门禁来源。

每线程内核栈仍有 16 KiB 虚拟槽、4 KiB 未映射 guard 与 canary；物理页按 live thread 分配，32 MiB 只表示全部虚拟槽容量，8 MiB 才是受信/保留线程的物理池。启动/调度使用独立的 64 KiB `boot_stack`。`make kernel-stack-check` 拒绝越界线程/启动调用链，`make local-check` 还核对 boot stack 链接跨度、栈虚拟容量、物理保留池、sidecar 动态容量和 `struct proc` 体积。

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
make local-check
```

`scripts/run-agent-tests.sh` 运行 `ci/kernel-budgets.json` 中 `agent_test_suite.expected_cases` 登记的完整有序清单；`workflow_teardown_race_ucore` 是独立机制专项，不计入该清单。时长门的 profile、fingerprint、baseline、limit 和 samples 也由同一版本化配置绑定，不能跨源码提交复用。发布的动态结果和指标只由 `evidence/releases/INDEX.md` 指向的 C→E bundle 判定；当前索引没有 release 记录，`remote_ci.status=not-attached` 也不表示远端执行成功。

通用 QEMU runner 二进制全量 drain，并在 marker 后继续大小写不敏感地检查包括 panic 在内的预定义 failure 模式；输出洪泛、迟到 marker、普通 case 信号退出、非零退出和后置 panic 都失败。显式 checkpoint profile 只接受完整 marker 后 runner 发出的单次 `SIGTERM`；显式 powercut profile 只接受认证 supervisor 对稳定 QEMU leader 发出的单次 `SIGKILL`，并要求随机 nonce、PID/starttime、镜像退出码及完整后代回收证明一致。该 powercut profile 是突然 VM 终止后的重启路径，不会清空宿主页缓存，也不能表述为整机物理断电。预算 checker、runner 与生产 profile validator 的 fail-closed 自测集合以源码为准，不在文档固化容易变化的数量；任一具体发布是否完成 clean `full-verify`，仍须查该发布的本地 bundle manifest，不能由工作树状态或本文叙述推断。

这些专项入口检查的是机制约束。`make dual-platform-run` 验证科研平台功能等价和 AgentOS 专属证据；profile v7 的 `make full-verify` 串联 target structure、`local-check`、Host 合同、传统接口动态门、版本化 Agent 套件、双目标和独立资源/恢复/故障专项，并把精确验证的 ch3 Guest 转录及 allocator raw-image/flush 证据作为 canonical archive 交付。未校准的时长策略会在首个 Agent QEMU 前 fail closed。内核栈与各机制测试仍保留独立入口，便于定位失败。

## 10. 维护要求

- 修改 syscall、VM、文件、同步、进程退出或调度路径时，必须运行对应安全专项测试。
- 新增 Agent role、capability、可信程序或 workflow 文件类型时，必须同步更新执行清单、VFS profile、负向测试和本文档。
- 新增安全域成员发布或凭据转换路径时，必须接入 ACTIVE/CLOSING 发布检查；新增阻塞点必须响应进程级退出请求，并由当前线程沿正常清理路径释放临时资源。
- 新增凭据降级、fork 或 exec 路径时，必须传播不可变 lifecycle key；只有 terminal teardown 可以 `leave`。槽复用必须递增 generation，禁止从 scope/PID/角色反推 lifecycle。
- 新增资源种类或配额时，必须接入 `resource_controller` 的 account、reservation 和 teardown settlement；不得恢复平行的 per-domain 私有计数。`resource_domain_id` 只用于调度。
- 新增进程级退出原因时，必须进入现有 teardown 状态机；REQUESTED 后不得发布新对象，scheduler handoff 前不得释放当前内核栈。
- `agent.c` 必须保持薄 facade；状态和实现归属版本化 owner 集合。metadata 的 transaction/file-state/catalog/query/scan/directory/objects/actions/store（含 format/I/O）只能通过命名空间化窄接口连接，directory bridge 不得持有可写全局状态或形成反向依赖。
- 通用安全机制若同步到 `baseline_ucore/`，必须保持两侧行为契约一致；当前 resource controller、workflow lifecycle、统一 teardown 和 lazy stack 尚不是 baseline 的共享实现。
- 新的可恢复资源不足路径必须返回错误并回滚，不能把普通用户可触发的条件写成 `panic()`。
- 调整 `io_policy.h` 的 budget 或 cache floor/cap 时，必须保持 owner 保留总和和 shared gate 分别不超过 device envelope、shared 不带债、有界请求的 owner/device debt 上界、protected aggregate envelope 与 `NBUF` 静态断言；同时复测 PUBLIC 压力、多 workflow、SYSTEM/workflow BACKGROUND、retiring cleanup、shared 排队轮转和 `disk_submit` 物理计费。
- 修改内核或 Agent 模块边界时必须更新并运行 `make local-check`。源码、镜像、text/data/BSS/total、PCB、Agent Context sidecar 与完整 Agent 状态容量、线程与 64 KiB boot stack 调用图、32 MiB 虚拟容量、8 MiB 物理保留池、owner/bridge 注册集合、模块阈值和 Agent case 清单以 `ci/kernel-budgets.json` 为准；受控符号用户必须登记，SCC 硬上限不能仅改 JSON 放宽。metadata 拆分单元、IPC 及 contract headers 必须同时纳入聚合 source/text/BSS 预算，禁止靠跨文件迁移绕过 no-growth 约束。完整套件耗时只在受管且已校准的本地环境中作为硬门，独立 teardown race 另行验证。
- 文档不得把共享加固 baseline 描述为未修改的上游 uCore，也不得把通过结构扫描解释为完整运行验证。
