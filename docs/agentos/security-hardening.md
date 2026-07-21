# 安全加固与资源韧性设计

本文记录 AgentOS-uCore 在系统调用输入防护、同步、文件系统、调度、可信执行、文件访问和进程生命周期上的安全加固。目标不是为已知测试增加特判，而是建立可复用的内核机制，使普通进程或低权限 Agent 的错误和恶意输入不能停止内核、伪造权限或耗尽全局资源。进程槽、文件系统块和 inode 均受资源域上限约束；已覆盖路径上的资源不足通过可恢复错误与完整回滚保持系统继续运行。

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
5. **配额按资源域累计。** fork 后代不能通过改变父子关系或长期存活绕过限制，系统关键工作保留独立槽位。
6. **策略保持可配置，硬性约束不可绕过。** orchestrator 可以调整 Agent 调度权重等策略，但不能关闭普通进程的有限等待保证、可信映像校验或 VFS 能力检查。

## 3. 修复与机制总览

| 提交 | 风险 | 机制性修复 | 主要验证 |
| --- | --- | --- | --- |
| `599846b` | syscall 可直接解引用或越界使用用户输入 | 统一使用 `copyin()`、`copyout()`、`copyinstr()` 和长度上限；副作用前完成参数、数组、字符串和输出区校验 | `usersafety_ucore` |
| `0c9aa61` | 全局唤醒破坏无关等待队列 | 引入对象私有等待队列和定向唤醒，等待节点只由所属队列管理 | `usersafety_ucore`、`procreap_ucore` |
| `39b63e7` | inode、block、文件表耗尽触发 panic | 分配路径返回失败并回滚未提交状态；短写准确返回已经提交的前缀长度 | `fsenospc_ucore` 验证 inode/cache/block；`usersafety_ucore` 验证文件/描述符分配失败清理，全局文件池耗尽尚无独立压力用例 |
| `caa30b4` | 深调用链或异常嵌套越过内核栈 | 每线程固定栈槽和 guard，构建时分析栈帧与调用图并强制预算 | `make kernel-stack-check` |
| `96ec4b9` | Agent 评分调度可永久饿死普通任务 | `AGENT_SCHED_MAX_AGENT_BURST` 强制类级公平上限，并保留 FIFO 逃生选择 | `agentsched_ucore` |
| `a08357a` / `672ffc0` | Agent 伪造内核系统事件 | 公共 `agent_wake()` 只投递普通消息；文件、定时器和 LLM 完成事件只能由专用内核路径产生 | `agentsecurity_ucore` |
| `1144924` | 孤儿僵尸无人回收并占满进程表 | 退出和再托管路径回收无人等待的退出对象，保持正常 `wait()` 语义 | `procreap_ucore` |
| `c36e7ab` | 普通进程自行创建全权限 Agent | 创建 grant 与业务 capability 分离，只有可信 bootstrap 和获授权 orchestrator 可委派角色 | `agentsecurity_ucore` |
| `acb8cd6` | 角色与可执行代码没有可信绑定 | 构建期清单写入不可变 inode 安全元数据，loader 把映像身份、角色上限、bootstrap 资格和 RX/RW+NX 布局绑定到进程 | `agenttrust_ucore` |
| `2d5b994` | 普通文件 syscall 绕过 Agent capability | inode 安全标签、`dev + inum + incarnation` 身份、进程 VFS 凭据和操作级检查共同保护 workflow 域；继承 fd 在实际操作时重新校验 | `agentvfs_ucore` |
| `93d89ae` | 进程退出遗弃阻塞 syscall 中其他线程的临时资源 | 退出逐线程定向取消等待并等待同进程线程退出阻塞路径，再销毁共享地址空间、文件和同步对象 | `procreap_ucore` |
| `6362075` | 有父僵尸长期占用执行槽 | 将父进程可见的退出状态保存为独立 child record，执行槽可先释放，父进程仍可取得 `wait()` 结果 | `procreap_ucore` |
| `807b1e4` | 长存活 fork bomb 耗尽统一进程池 | 后代绑定不可变进程资源域，普通域有累计 live 配额，bootstrap/Agent worker 使用受控保留槽 | `procreap_ucore`、`procreap_agent_ucore` |
| `7d87f76` | PUBLIC 进程持久耗尽块和 inode，使工作流与内核元数据进入 ENOSPC | 稳定存储域 cookie、逐块 owner map、inode owner、域配额及 PUBLIC/WORKFLOW/SYSTEM 分级保留水位共同约束分配 | `fsquota_ucore` 两组配额/保留场景，`fsenospc_ucore` 双目标复测 |
| `ab246d4` | PUBLIC 进程用短命文件耗尽 Agent 全局版本表并阻断工作流编辑与摘要缓存 | 每个磁盘 inode 固定拥有一个版本 sidecar 槽；最终 `iput()` 回收同一生命期的版本、租约和摘要缓存，槽的可用性继承 inode 域配额与分级保留量 | `fsquota_ucore` 跨越旧 512 槽上限的 640 次创建/删除循环，并验证 workflow 版本和内容缓存仍可用 |
| 当前变更 | `MESSAGE_SEND` 被解释为全局裸 PID 通道，低权限 Agent 可向 Recovery/Orchestrator 注入消息并耗尽关键事件队列 | stable control id 定向路由；接收方或受权控制者 grant/revoke；external/direct/attributed/source 三层配额，为显式内核 origin 保留至少 4 个容量名额；慢订阅者隔离和退出回收 | `agentsecurity_ucore` 已验证未授权拒绝、grant/revoke、target LLM_DONE consent、MESSAGE 位图隔离和 `ROUTE_MAX+2` 短命 source 槽回收；`agentloop_ucore` 已验证 source=4、directed=8、external=12、第 13 条 external 拒绝、4 条 KERNEL TIMER 保留容量、消费后重接纳和慢 watcher 隔离。attributed=8、同一来源混合跨类及路由幂等/部分撤销仍缺独立输出 |
| 本次 scope 变更 | Agent capability、对象表和 IPC 仍可被解释为所有 workflow 共享的全局权限 | syscall 541 由可信 factory 创建动态 scope；所有敏感对象使用 capability + active scope + stable owner；syscall 542 只一次性委派 pipe；最多 4 scope 并保留独立进程/存储/对象份额 | `agentscope_ucore` 的同名对象、动作、租约、IPC、audit、配额、fd 委派、事务竞争和回收断言已在完整 Agent 回归中通过 |
| 本次审计变更 | 用户可伪造 span/cause，低权限遥测或委派 span 可挤掉关键审计效果 | private span owner 与 cause control sidecar；`context_push` 拒绝非零 cause/span；审计按 scope low/high 分区，只有内核确认的特权状态效果进入 high | `agentsecurity_ucore` 的 forged context、trusted cause attribution、audit authority partition 回归已通过 |

## 4. 用户输入与内核对象检查

系统调用层只把寄存器参数视为数值或用户虚拟地址。字符串、数组、结构体和输出缓冲区必须先通过 VM copy 接口访问，内核不能直接解引用用户地址。长度计算先检查上限和溢出，变长参数在分配文件、页、进程或写入磁盘之前完成验证。

文件描述符、inode、pipe 和同步对象通过稳定引用跨越可能睡眠的路径。失败路径按获取顺序的逆序释放引用，避免一次非法 syscall 消耗全局文件表或物理页。用户页在 syscall 执行期间失效时，copy 接口返回失败，不把用户缺页升级为内核异常。

相关实现集中在：

- `os/syscall.c`、`os/vm.c`：参数复制和地址范围检查；
- `os/file.c`、`os/pipe.c`：文件、pipe 临时引用生命周期；
- `os/loader.c`：`exec` 参数、映像布局和地址空间替换；
- `user/src/usersafety_ucore.c`：坏地址、超长参数、定向等待和失败事务复测。

## 5. 等待、退出与进程资源域

### 5.1 定向等待和协作退出

`os/wait.c` 为 mutex、semaphore、condvar、进程等待、Agent event 和 timeline 等睡眠对象维护明确队列，只唤醒与状态变化相关的线程。线程取消从同一队列摘除节点，避免节点同时出现在运行队列和等待队列。pipe 当前仍以让出处理器的方式等待，但会检查进程退出请求并沿正常 syscall 清理路径释放临时引用。

多线程进程退出时，发起退出的线程先把进程标记为退出中，取消其他线程的可中断等待并等待它们离开 syscall。只有不再存在会访问进程共享状态的线程后，内核才释放地址空间、文件表和同步对象。这使阻塞 `read()` 等路径持有的临时引用能够沿正常清理路径归还。

### 5.2 执行槽与等待凭据分离

子进程退出结果保存在父进程私有的 `child_record` 中。父进程未调用 `wait()` 时，记录仍保留退出 pid 和状态，但已退出进程的执行槽、内核栈、页表和文件引用可以先释放。每个父进程的待取结果有固定容量，恶意父进程只能耗尽自己的记录配额。

### 5.3 活进程资源域与系统保留槽

非管理员的普通 fork 后代继承不可变的 `resource_domain_id`，一个域的 live 进程数最多为 `PROC_RESOURCE_DOMAIN_LIMIT=64`。128 个进程槽中，普通 admission 合计最多使用 `PROC_ORDINARY_SLOTS=96`；`PROC_RESERVED_SLOTS=32` 仅供内核受控的 boot、workflow、Agent 和 worker admission。最多 4 个 workflow 各保证 8 个保留槽，单个 scope 不能消耗其他 scope 的 admission 份额。创建新隔离域或消耗保留槽需要内核持有的 domain admin/factory 状态，用户态参数和普通 Agent capability 都不能自行获得。

这套机制同时满足三个目标：单个长存活 fork bomb 有上限；父进程退出或后代重新挂接不能重置配额；普通域达到上限后，受权 Agent 和 worker admission 仍有独立保留槽。

### 5.4 文件系统存储域与分级保留量

进程表中的 `resource_domain_id` 是可复用槽位，不能直接作为持久磁盘身份。内核因此为每个新进程资源域分配单调递增的 `storage_domain_id` cookie：同域 fork 后代继承 cookie，新域取得新 cookie，`exec` 不改变 cookie。文件系统启动时扫描已分配 inode 和块 owner map，取持久 cookie 最大值作为新 cookie 下界；达到 32 位上界时拒绝创建新域，不回绕到旧身份。

新磁盘格式在 bitmap 后增加逐块 owner map，并在 inode 中保存创建域 cookie 和格式版本。数据块、间接索引块和目录扩容块按实际分配主体写入 owner；`truncate`、`unlink`、失败写回滚和 inode 回收再按持久 owner 精确退款。授权凭据与分配凭据是两个对象：例如根目录更新仍以 kernel cred 完成目录授权，但块配额使用发起创建操作的原始主体，普通进程不能借内核代办路径消耗系统保留量。

分配水位分为三层：PUBLIC 必须同时留下所有 admitted/future workflow 和 SYSTEM 剩余量；某个 workflow 只能在自己的 scope 配额内使用共享 workflow 水位，并必须留下其他 scope 尚未消费的保证；内核维护路径和受信任 SYSTEM 可以消耗自己的系统信用，但仍须兑现所有 admitted/future workflow 的最低保证。容量算法由 `fs_storage_policy.h` 在 mkfs 和内核间共享：以完成镜像后的真实空闲量为输入，workflow 总保证最多使用扣除 SYSTEM 后余量的四分之三，并设置每 scope 320 inode/512 block、SYSTEM 8 inode/512 block 的显式硬下限。计算出的 version/slots/G/S/checksum 持久化在 superblock；内核重启固定使用 G，只从 `free-4G` 恢复尚未消耗的 SYSTEM 信用，避免把合法消耗的 S 再预留一次。当前平台镜像核算结果为每 scope 342 inode/1195 block、SYSTEM 64 inode/512 block。PUBLIC 和每个 WORKFLOW scope 的块/inode 还分别累计到稳定 owner 上限。

挂载时会校验 superblock 容量契约及 `inode -> bitmap -> owner map -> data` 的完整布局，并从 bitmap、inode 和 owner map 重建空闲计数及 cookie 下界。第一次核算只服务于恢复，先回收没有持久恢复令牌的旧 workflow boot lease；第二次核算才要求空闲量至少覆盖持久化的 `4G`，避免合法的旧 scope 残留在回收前把系统误判为不可启动。新 workflow admission 与分配使用同一关中断临界区检查实际剩余保证。mkfs 在安装完全部可信程序后要求初始空闲量同时覆盖 `S+4G`，不能兑现时拒绝出镜像；主机 mkfs 每次生成镜像前都按当次 `FS_*` 参数重编，避免配置与内核漂移。已分配块缺少 owner、已分配 inode owner/version 无效、容量 checksum 错误或新旧格式混用仍会拒绝启动。当前教学文件系统没有日志，因此这里只保证分配器写入顺序和可识别的不一致状态，不宣称突然掉电时具备完整事务原子性。

### 5.5 Agent 文件版本 sidecar 生命周期

编辑版本和内容版本不再分别从两个“先到先得”的全局池分配。内核按 `inum` 直接索引覆盖全部磁盘 inode 的统一 sidecar，并同时校验 `dev + inum + incarnation + storage owner + VFS policy`。一个存活 inode 只能占用自己的槽，PUBLIC 文件不能通过反复创建新 incarnation 占走其他 inode 的版本位置；PUBLIC、WORKFLOW 和 SYSTEM 可获得多少版本状态，因而由同一套 inode 域配额与分级保留水位决定，不再维护一套容易漂移的平行配额。

删除目录项不是文件生命期终点，因为仍可能有打开的描述符继续访问 inode。实际清理挂在 `iput()` 的最终回收分支：只有链接已删除、最后引用释放且 `itrunc()` 成功后，才在清除 inode 身份之前原子移除该 incarnation 的编辑版本、内容版本、活动租约和 digest cache。新 incarnation 首次触达同一槽时还会执行防御性旧状态清理；仅持有旧 `dev + inum + incarnation` 的提交或租约过期路径只能查找，不能重建已经死亡的版本状态。

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

所有执行直接投递的数据面入口复用同一交付函数：`agent_wake()`、`send_message`、非零 target `llm_request` 和 `llm_response` 均先核对 stable route，再检查 watch、队列预算并入队；`llm_request(target=0)` 只记录摘要。兼容 mailbox 和 metadata 预取交接只在成功入队后更新；未授权、目标不存在、watch 不匹配或资源不足都不会留下旁路副作用。`LLM_RELAY` 仍负责“谁可产生 LLM 结果”，route 负责“结果可发给谁”，两层授权缺一不可。

目标事件队列仍是 16 槽 FIFO，但每个槽用内核私有 accounting flags 编码 origin/resource class，并保存 stable source control id。资源边界分三层：所有带 Agent 来源的 external 事件合计最多 12 条；directed IPC（`MESSAGE` / `LLM_DONE`）和 attributed notification（`FILE_STATUS` / `JOB_DONE` / `POLICY_DENIED` / `CONTEXT_LIMIT` / `DASHBOARD_EXPORT`）各自最多 8 条；同一个 stable source 跨两类合计最多 4 条。用户可见 `source_pid` 不参与配额身份判断，攻击者不能换事件类型或利用 PID 复用刷新额度。

external=12 的 admission 上限为显式 `KERNEL` origin 保留至少 4 个容量名额。heartbeat TIMER 等内核直接产生的事件必须明确传入该 origin，才能越过 external 边界；带 Agent source 的文件状态、作业完成和拒绝通知即使事件类型属于系统通知，也仍按 attributed external 计费，不能冒充内核来源侵占保留量。所有 origin 最终仍受 16 槽总容量约束。`LLM_DONE` 虽由 `LLM_RELAY` 专用工具产生，但它是定向 IPC，既需要 route，也计入 directed/external/source 三组配额。

广播系统事件逐 watcher 独立尝试。某个慢订阅者队列已满、watch 不匹配或已经退出时，内核继续投递后续目标；文件 metadata 等权威状态一旦提交，不会因为一个通知接收者资源不足而错误返回 `NO_SPACE`。这种 best-effort 通知语义把控制状态提交与观察者背压分离，避免单个低权限 Agent 阻断全局工作流。

当前路由授权表是 scope-local 内核私有执行状态，尚无 snapshot/query ABI，grant/revoke 也不追加 Context 或 audit。现有审计只能观察授权之后同 scope 的事件入队和消费，不能完整重建路由策略变更历史。若后续需要运维审计，应新增只读快照和受权的 route-change audit record，而不是把用户日志当作权威控制面记录。

### 6.4 Workflow scope factory 与对象所有权

scope 编号由内核定义：0 是 PUBLIC，1 是只读可信 SYSTEM，2 及以上是动态 workflow。只有非 Agent、具有 resource-domain admin 且仍运行可信 bootstrap 映像的 factory 可以调用 syscall 541 `agent_workflow_create(role)` 建立新 scope。普通 role grant 只允许在当前 scope 内调用 `agent_create_role()`，即使 orchestrator 有全部业务 capability，也不能用它铸造新对象域和新配额。

同 scope Agent/worker 继承 scope；普通 fork 丢弃 workflow 凭据。跨 scope 创建默认仅继承 stdio。syscall 542 `agent_scope_delegate_fd(fd)` 只接受 pipe，并为下一次边界尝试签发一次性票据；票据在任何可能失败的资源分配前消费，不能因失败留给后续子进程，也不能用来传递普通文件。

敏感对象身份按类型组合 scope 和 stable owner：文件/metadata/租约以 `scope + dev + inum + incarnation` 为基础，action/dependency/cache 先按 scope 分区，IPC/wait control 使用同 scope stable control id，span/audit/prefetch 使用 scope + 公开 span + 私有 span owner/cause principal。capability 只在当前 scope active 且对象 owner 精确匹配时生效；SYSTEM 仅在显式只读路径可见。

scope 最后成员退出后先进入 retiring，禁止新对象/存储 admission。内核随后清理 metadata、dependency、action history、edit lease/version、query/digest cache、audit、span prefetch 和 IPC 状态，再释放 scope 槽。普通 scope 文件被回收；boot scope 持久文件保留，其 scope 作为 inactive storage owner 留存，避免旧工件被新 scope 重新解释。

### 6.5 审计分区和可信因果

物理审计表 512 槽按最多 4 个 workflow 各保证 128 条，每 scope 分成 low 64 和 high 64。Context/event/sched/prefetch/manual 等遥测始终 low，low 每 stable principal 最多16条。只有 syscall/tool 成功后由内核确认的特权状态效果进入 high；high 依据每 scope 8 个保留进程份额为每 active principal 保留8条。high 满时只滚动当前 principal 自己的记录或回收已退出/inactive principal 的最旧记录，绝不牺牲另一 active principal 的 protected evidence。inactive 历史仍是有界窗口，回收量通过 `dropped_records` 可见。

公开 `span_id` 和 `cause_sequence` 是呈现字段，不是权限票据。内核为每个 Context/event 保存私有 span owner、source control 和 source pid；`context_push()` 要求用户输入的 cause/span 都为0，再由内核连接当前链。每 scope 独立维护 ledger hash，但 sequence 在系统中单调递增，因此当前窗口允许因跨 scope 写入、low/high 滚动和 principal 滚动而稀疏。`dropped_records=total-visible` 解释窗口外前驱，不能要求所有相邻可见 sequence 都直接 hash 相连。

## 7. 文件安全域

VFS 使用 PUBLIC=0、SYSTEM=1 和多个动态 workflow>=2，而不是一个所有 Agent 共享的 workflow 域。进程凭据由可执行映像 profile、Agent role capability、kernel-issued scope 和受控 exec 委派共同得到。文件资源以 `scope + dev + inum + incarnation` 标识；同名文件和复用 inode 均不会继承另一 scope/生命期的 metadata、租约或缓存。`open/read/write/truncate/unlink` 等真实数据路径同时检查 inode scope 和当前有效能力，因此普通 syscall、预先打开的 fd 或相同 capability 不能跨 workflow。

主要规则如下：

| 主体 | PUBLIC | SYSTEM | 动态 workflow |
| --- | --- | --- | --- |
| 普通 public 进程 | 保留普通文件数据访问 | 仅允许显式执行查找，不获得对象数据权限 | 数据操作拒绝 |
| workflow Agent/worker | 与 public 数据隔离 | 只读执行/共享策略按明确操作开放 | 只访问与自身 scope 精确相等的对象，并继续检查 read/write capability |
| 内核维护路径 | 只执行明确代办操作，分配仍按原主体计费 | 可维护可信映像和私有 metadata | 可在 retirement 中按目标 scope 回收 |

`exec` 是数据隔离的显式例外：内核可查找布局有效的 SYSTEM 映像。普通进程仅执行该映像不会得到 workflow scope；worker 必须匹配 `agent_worker_create()` 预先绑定的 inode和父 scope，Agent 还必须通过可信 role-image 校验。跨 scope 的 pipe 则必须使用一次性 fd 委派，不由 exec 隐式携带。

inode 标签带布局版本和一致性校验值。该校验用于发现格式或状态不一致，不是 MAC 或密码学防篡改。创建路径生成标签，装载和访问路径校验标签；未知或损坏标签按拒绝处理。可信可执行映像同时设置 immutable 标志，普通 `write`、`O_TRUNC` 和 `unlink` 不能改变整个映像文件。

## 8. 调度与资源耗尽硬限制

Agent 评分仍可使用角色权重、priority、budget、事件、deadline、heartbeat 和虚拟运行量表达策略，但调度器额外维护不可配置的类级公平上限：连续选择 Agent 达到 `AGENT_SCHED_MAX_AGENT_BURST` 后，只要普通任务可运行，就必须选择普通 FIFO 任务。评分策略无法覆盖这条上限。

文件系统对 inode、inode cache 和数据块耗尽返回失败，回滚未提交状态并准确报告短写；存储域配额和分级水位进一步保证 PUBLIC 压力不能触及 workflow/system 保留量。Agent 文件版本 sidecar 与 inode 槽及其最终回收绑定，因此短命文件也不能绕开存储域边界耗尽独立的内核版本池。专项压力用例验证同域计数跨子进程退出仍有效、释放后可复用，以及 PUBLIC 触及全局水位后 workflow 文件、版本状态、内容摘要缓存与内核 metadata bank 仍可用。metadata 后端采用 generation + payload hash 的双 bank 提交：inactive bank 完整写入并回读后才成为新一代，ENOSPC 不覆盖上一代，显式 set/delete 失败还会回滚内存记录与 inode sidecar。metadata 内存表、索引、inode sidecar 和持久化由同一可睡眠事务门保护；VFS callback 与 scheduler 只做非阻塞尝试，失败后通过后台全量扫描协调，不能在持有底层文件系统状态时形成反向等待。事务 owner 在同一关中断临界区清空后广播其专属等待队列；所有 waiter 都重新检查条件并竞争，不把所有权绑定给单个被唤醒线程，因此任一 waiter 退出也不能把其余 syscall 永久遗弃。PUBLIC inode 在竞争事务门前被过滤，普通进程不能用无关文件 I/O 制造 metadata 锁竞争或扫描风暴。全局文件池分配失败也沿错误路径向上传播，但当前只验证了进程 fd 槽不足时的引用清理，尚缺耗尽整个 `FILEPOOLSIZE` 的独立压力用例。每线程内核栈使用 16 KiB 栈槽和 4 KiB 未映射 guard，并在低端保留 canary；构建时由 `make kernel-stack-check` 拒绝超过预算或无法确定上界的调用链。

## 9. 验证入口

```bash
# Agent 权限、可信映像、VFS 域、调度和 syscall 输入防护
bash scripts/run-agent-tests.sh

# 双目标 ENOSPC 复测，以及 AgentOS 存储域配额和系统保留量
make fs-enospc-test TOOLPREFIX=riscv64-linux-gnu-

# 两个目标的退出、僵尸、阻塞 syscall 和资源域；主目标另测 Agent 保留槽
make proc-reap-test TOOLPREFIX=riscv64-linux-gnu-

# 构建期内核栈预算
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
make -C baseline_ucore kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`scripts/run-agent-tests.sh` 当前包含 `agentscope_ucore`、`agentsecurity_ucore`、`agenttrust_ucore`、`agentvfs_ucore` 和 `usersafety_ucore` 等专项程序。本轮 15 项 Agent 回归全部通过；`agentscope_ucore` 产生了 `cross_scope_isolation`、`ipc_scope_isolation`、`same_scope_collaboration`、`metadata_transactions`、`scope_storage_quota`、`action_scope_isolation`、`audit_event_scope_isolation`、`lease_scope_isolation`、`scope_capacity_reservation`、`transactional_fd_delegation`、`lifecycle_reclamation` 和 `parent passed`。宿主提取器测试也通过 scope 选择歧义、容量契约损坏、guest 路径穿越拒绝和旧 scope 输出清理用例；进程回收与 ENOSPC 专项均独立通过。

这些专项入口检查的是机制约束。`make dual-platform-run` 继续验证科研平台功能等价和 AgentOS 专属证据，`make full-verify` 串联宿主机、双目标、Agent 和进程生命周期检查；ENOSPC 与内核栈预算仍保留为可单独复现的专项入口。

## 10. 维护要求

- 修改 syscall、VM、文件、同步、进程退出或调度路径时，必须运行对应安全专项测试。
- 新增 Agent role、capability、可信程序或 workflow 文件类型时，必须同步更新执行清单、VFS profile、负向测试和本文档。
- 通用安全机制若同步到 `baseline_ucore/`，必须保持两侧语义一致，并在双目标文档中说明它不属于 AgentOS 实验变量。
- 新的可恢复资源不足路径必须返回错误并回滚，不能把普通用户可触发的条件写成 `panic()`。
- 文档不得把共享加固 baseline 描述为未修改的上游 uCore，也不得把通过结构扫描解释为完整运行验证。
