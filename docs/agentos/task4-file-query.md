# 任务四：面向 Agent 查询优化的文件系统扩展

本文是 [design.md](design.md) 的任务四细节附录，重点说明 AgentOS-uCore 当前实现的文件元数据表、带 incarnation 的真实 inode 关联、VFS public/workflow 安全域、私有 metadata 双 bank、属性查询、索引路径、内容摘要工具、通用依赖注册/查询和查询历史驱动的预取提示。

## 目标

任务四希望操作系统为 Agent 提供更适合智能体使用的文件查询能力，使 Agent 不只按路径打开文件，还能按 namespace、object_id、label、state、type、summary 等属性查询文件对象。科研工件是当前示例负载使用的一类文件对象，不是内核唯一支持的对象模型。

AgentOS-uCore 当前实现的是内核级文件元数据服务：

- 支持最多 512 条文件元数据，并为 SYSTEM 与最多四个 active workflow scope 划分独立上限；退役 scope 由独立生命周期账本和轮转 reaper 有界清理；
- 以真实文件的 `dev + inum + incarnation` 作为主要身份，避免 inode 号复用后继承旧状态；
- 要求 `physical_name` 能解析到 uCore 根目录中的真实文件名；
- 使用根目录私有 `.agentmeta` / `.agentmeta1` 双 bank 保存带 generation/hash 的版本化快照，并以可恢复分块 COW 状态机发布；
- 使用按 workflow scope 记账的固定窗口后台写回和稳定 sponsor `BACKGROUND` I/O 预算，把普通文件微小变化合并为有界 metadata checkpoint；
- 使用 FIFO ticket 事务门、统一 metadata 工作预算和按字段变化驱动的维护入口，为跨 workflow 请求提供有界接纳；
- 在普通 `open/read/write/truncate/unlink` 路径执行 public/workflow/kernel-private 数据访问策略，防止绕过 Agent 工具 capability；
- 支持扫描查询；
- 支持 state/label/type 索引查询，ABI 字段兼容保留为 status/stage/kind；
- 支持同一文件元数据代数下的查询结果缓存；
- 支持摘要查询；
- 支持受权 Agent 读取真实文件短预览和 FNV-1a 内容指纹；
- 支持同一文件元数据代数下的真实文件内容摘要缓存；
- 支持依赖关系注册和查询；
- 支持根据历史查询和对象标签依赖生成 metadata 预取提示；
- 支持按当前 span 查询跨 Agent 汇总的 metadata 预取提示；
- 文件状态变化可以触发 Agent 事件；
- 调度器空隙会按 tick 合并分批扫描 uCore 根目录，自动维护真实文件的元数据和索引；
- 查询结果会写入 Context Path。

当前实现聚焦 uCore 根目录短文件名，不把范围扩大成多级目录递归扫描或通用全文索引。

## 数据结构

`struct agent_file_meta` 表示一条文件元数据：

| 字段 | 说明 |
| --- | --- |
| `used` | 该槽位是否有效 |
| `fid` | 文件元数据 ID |
| `physical_name` | 物理文件名或内核示例名 |
| `logical_path` | Agent 可理解的逻辑路径 |
| `project` | namespace；科研示例中用作项目名 |
| `workflow` | 工作流名 |
| `run_id` | 实验运行 ID |
| `stage` | 对象 label；科研示例中用作阶段名 |
| `kind` | 对象 type；科研示例中用作工件类型 |
| `status` | 对象 state，如 ok、failed |
| `summary` | 文件摘要 |
| `dependency_mask` | 对象标签依赖位图 |
| `updated_tick` | 最近更新时间 |
| `flags` | 删除、持久化等元数据操作标志 |
| `dev` | 真实文件设备号 |
| `inum` | 真实文件 inode 号 |
| `incarnation` | inode 槽当前生命代数；同一 inum 删除后复用时会变化 |
| `size` | 真实文件大小 |
| `fs_generation` | 文件系统侧更新代数 |
| `update_mask` | 本次更新哪些字段 |

查询结构 `struct agent_file_query` 以空字符串表示“不限制该字段”。结果结构 `struct agent_file_query_result` 返回命中数、返回数、扫描数、是否使用索引、查询计划、计划原因、候选记录数、索引桶、是否截断、tick 和最多 8 条命中。

`struct agent_file_prefetch_hint` 表示一条预取提示。它保存触发提示的 Context sequence、span id、source pid、target pid、source fid、target fid、原因 flags、排序分数、文件元数据代数、候选记录数量和一份目标文件 hit 快照。每个 Agent 本地提示容量固定为 8 条，属于当前 Agent 的内核 PCB 状态；全局 span 预取提示总线容量固定为 32 条，用于同一因果链上的跨 Agent 查询。

内核启动时 `agentinit()` 会把 status、stage、kind 三类索引桶初始化为 `-1`。因此即使测试程序在调用 `agent_file_meta_init()` 前先执行带索引查询，也会返回 0 条命中，而不会沿着未初始化链表扫描。

## 文件节点关联和私有元数据文件

任务四不是只保存一张脱离文件系统的示例表。当前实现会把 Agent 元数据绑定到真实 uCore 根目录文件：

1. `agent_file_meta_init()` 会强制读取 `.agentmeta` 和 `.agentmeta1`，选择 generation 最高且完整校验通过的私有快照，但只替换调用者 workflow scope 的记录。
2. 其他 scope 的持久或内存态记录不会被调用者重载清除。已有 active bank 的重载失败会保留内存表并返回错误；尚无 active bank 时，内核把当前带 `PERSIST` 的记录提交为第一代，未加载时则从空表开始。
3. 快照只串行化有效记录；每条记录显式保存原 slot、scope、`physical_name`、`dev`、`inum`、`incarnation`、`size` 和 `fs_generation`，header 保存 generation、精确记录数和 payload hash。
4. `fileopen(O_CREATE)`、写入、截断、删除会通知 Agent 子系统刷新或删除关联元数据。普通 VFS callback 只发布内存状态，且仅对 `PERSIST` 记录推进 scope-local 脏代数；volatile 记录不进入 bank 写回。
5. `agent_file_meta_set()` 支持 `AGENT_FILE_META_F_DELETE` 删除属性，支持 `AGENT_FILE_META_F_PERSIST` 事务写入 metadata 双 bank；持久化失败时恢复修改前的内存记录和 inode sidecar。
6. 受权 Agent 显式写入 metadata 时会接管自动扫描槽位；除非显式带上 `AGENT_FILE_META_F_AUTOSCAN`，否则旧的自动扫描标志会被清除，后续扫描只刷新真实 inode 身份，不改写这些对象属性。

`.agentmeta` 和 `.agentmeta1` 是标记为 `KERNEL_PRIVATE` 的 Agent 子系统内部后端。普通文件 syscall 直接读取、创建、修改或删除任一 bank 都会返回 `-1`；Agent 子系统内部 helper 使用内核凭据读写。加载时按 header 声明的逻辑长度验证版本、generation、payload hash、slot 范围、scope 配额和 scope 内唯一性，物理文件可以保留更长的已分配高水位。提交先向目标 bank 写无效 header，再按固定 1 KiB segment 与已验证的内存 bank shadow 做字节比较，只写并逐段读回核对变化 payload；整体摘要一致后才发布有效 header，header 回读一致后才切换 active generation。新的 primary 完整验证后，状态机才允许用同一不可变快照更新旧 bank 作为 mirror。缩短快照不在 checkpoint 中同步 truncate，而是复用旧尾块；primary 验证前失败保留旧 active bank，mirror 阶段失败仍保留新 primary。payload hash 是一致性摘要，不是密码学认证。

启动时，可信 bank 不再等到首个 Agent 查询才懒加载。`main()` 在文件系统和 timer 初始化完成后立即调用 `agent_storage_init()`，只有加载/恢复结束后才启动运行时 I/O policy 并发布首个用户进程。单个 bank 损坏时会从另一份可验证副本恢复；只有不存在有效 bank 或选择失败时 metadata API 才 fail closed，不会用空表继续。但 scope retirement 仍可清理依赖、动作、缓存、审计、租约和真实 VFS-labelled 文件并释放 scope 身份。当前测试只覆盖正常 bank 加载/重载，没有动态注入启动 bank 损坏。

普通持久文件变化采用写回而不是直写：write/truncate 通过 inode incarnation sidecar 先发布最新 size、更新时间和文件代数，查询及 bank 快照都会覆盖内存主表中的旧值；create/delete 则先完成内存记录变化。带 `PERSIST` 的记录随后让对应 scope 的 `dirty_generation` 进入固定一秒的非滑动合并窗口；不进入快照的 volatile 记录只更新内存/sidecar，不制造空 checkpoint。后台维护在事务门空闲时每轮只推进 COW 状态机的一个步骤；dirty scope 轮转成为整个 job 的稳定 sponsor，物理传输使用该 owner 的硬 `BACKGROUND` burst/refill。checkpoint 成功或失败后的 not-before deadline 都是固定合并窗口，不再按 checkpoint 执行耗时延长；I/O debt 决定何时能推进下一步。到期写回先于扫描获得维护机会，持续 scan pending 不能使其永久饥饿。快照开始时捕获各 scope 的脏代数，提交后只推进期间没有新增变化的 `durable_generation`，所以并发写入不会被错误标成已持久化；失败则保留脏状态等待固定窗口和 budget。

全局 metadata transaction gate 对进程请求使用单调 ticket FIFO，并只唤醒队首。线程取得 ticket 后不可中断地等到自己的 turn；若进程已经请求退出，也会先接过并立即传递事务门，不能遗弃 ticket。scheduler 在门空闲时可取得硬有界维护轮次，以免后台写回和扫描被持续进程请求饿死；该轮次不会重复唤醒已由前任唤醒的 serving ticket，普通进程态 VFS callback 仍不能插队。进程态表扫描、索引维护、显式依赖和预取工作按 128 records 进入统一 kernel-work 安全点；scheduler 每轮最多扫描 16 个目录项，字段变化只发布线性索引。显式用户依赖保存在每 scope 最多 16 条的固定表中；兼容 `dependency_mask` 留在源文件记录，由依赖查询、action 和预取在已有线性扫描中按需解释，不再物化全局派生图。字段变化掩码分别维护 status/stage/kind 索引和依赖代数，纯状态变化不推进依赖 generation。通用 action 先用位图形成目标与依赖选集，再一次更新每个槽、一次重建 status 索引并合并持久写回，复杂度为固定次数的有界表扫描。

显式持久 metadata set/delete、首次空 bank、reload 和 scope retirement 还会同步进入单调 ticket 的 FIFO submit lane，并建立不可替换的 COW job；该语义保证有序接纳，不保证调用返回时 primary 已完成回读验证。条件不满足时，中断从失败检查一直保持关闭，直到事务门释放且线程已经进入 submit condition queue，避免 unlock-to-sleep 丢失唤醒。持久化在预算边界暂时释放事务门时，immutable job 保持同一 `job_id`，后来提交者不能替换。metadata exact-read 在文件系统原子段之外偿还 I/O 预算，并从正数短读前缀继续；临时 interruption 不会被记成 bank corruption。scanner 仍独立使用 `max(20 tick, 4 * 扫描耗时)` 自适应 cooldown，不能与 checkpoint 的固定窗口混为一谈。

这套实现让查询结果中的文件身份可以追溯到某一代真实 inode，同时保留 Agent 需要的 namespace、workflow、run、label、type、state 等高层属性。完整身份始终是 `dev + inum + incarnation`；删除文件后即使设备号和 inode 号被复用，新 incarnation 也会使旧 metadata、digest cache、版本和租约失效。为了兼容已有用户态测试，结构体字段名仍使用 project/stage/kind/status；字符串 selector 同时接受 `namespace/object_id/label/type/state` 等通用字段名。

## VFS 安全域与普通文件路径

Agent 工具 capability 只约束 `agent_file_query()`、`read_file_digest` 等 Agent 接口还不够；普通进程仍可能尝试用 `open/read/write/unlink` 直接访问工作流文件。因此每个 inode 都保存经过校验的 VFS label，每个进程都保存独立于 Agent 业务角色的 `filesystem_domain` 和 `filesystem_capability_mask`，普通文件路径按当前进程凭据逐次鉴权：

| inode 策略 | 规则 |
| --- | --- |
| `PUBLIC` | 数据操作只由 public 域普通进程访问，保留普通 uCore 文件命名空间 |
| `WORKFLOW` | 数据操作只由 workflow 域访问；读取要求 `CONTENT_READ`，创建、写入、截断和删除要求 `ARTIFACT_WRITE` |
| `KERNEL_PRIVATE` | 只允许内核凭据访问，用于 `.agentmeta` 等私有状态 |
| `ROOT` | 两个域都可查找、读取和执行目录入口；workflow 域修改根目录要求 `ARTIFACT_WRITE` |

文件的安全策略在创建时确定，后写入 Agent metadata 不会把攻击者预先创建的 public inode 重新标成 workflow 工件。可执行映像还带有 VFS profile，它是能力上限而不是权限来源。Agent 创建时，角色业务能力会继续衰减其 VFS effective capability；orchestrator 若需要启动非 Agent 文件 worker，必须调用 `agent_worker_create()`，显式请求 `CONTENT_READ` 或 `ARTIFACT_WRITE`，且请求不能超过父进程能力和目标映像 profile。

`exec` 不等同于读取文件内容。当前实现可以在调用者所在域没有匹配项时查找另一域的布局有效映像；普通进程直接执行 workflow 映像后仍是 public 域且没有文件能力。Agent 只有执行允许当前角色的可信映像才能保留身份；非 Agent worker 只有通过精确 pending 委派才能安装 workflow 凭据。

`agent_worker_create()` 的 pending 委派绑定到目标可执行 inode 的 `dev + inum + incarnation`。worker 映像只要求 immutable、domain-safe、有效 W^X 布局和非空 VFS profile，不要求 Agent `TRUSTED` role-image 标志。子进程只有执行创建时绑定的同一映像才取得 workflow 凭据；执行其他映像会清除委派。普通 `fork()` 不复制 workflow effective capability。文件描述符虽然按 uCore 进程语义复制，但每次 `read/write` 都会用当前进程凭据重新检查 inode，因此没有权限的子进程不能利用父进程预先打开的 fd 绕过策略。

## 根目录自动扫描

任务四的自动维护路径由 Agent 子系统和调度器配合完成：

1. `agent_file_meta_init()` 或 `file_meta_init` 工具启用扫描能力。
2. timer tick 只安排周期扫描；文件 hook 能直接绑定的对象只更新对应 scope 的内存记录或 inode sidecar，并按 `PERSIST` 属性登记合并写回。只有锁竞争且无法由 sidecar 表达、绑定丢失等需要协调的情况才标记 `file_scan_pending`。
3. 扫描请求采用非滑动 pending/deadline 状态机：首次启用立即到期，后续请求不能提前 cooldown，active 期间的重复请求只合并为一轮。调度器空隙调用 `agent_background_maintain()`，获取全局 metadata 事务前先检查 deadline，同一 tick 内最多推进一次、每次最多处理 16 个根目录项。
4. 扫描会跳过两个 metadata bank，只为真实根目录文件建立 `AGENT_FILE_META_F_AUTOSCAN | AGENT_FILE_META_F_PERSIST` 元数据。
5. 自动元数据使用真实 `dev + inum + incarnation + size` 作为身份，并填入 `project=root`、`workflow=background-scan`、`run_id=ROOT`、`stage=scan` 等默认属性。
6. 完整扫描结束后，已经不存在的自动元数据会被清理；手动写入的对象元数据不会被扫描流程误删。
7. 完整扫描成功、加载失败或目录短读后统一进入 `max(20 tick, 4 * 本轮耗时)` 休息期；即使 metadata 槽已满，未绑定文件的持续微写也不能让全根扫描完成即重启。元数据变化后重建 status、stage、kind 索引；后台按 scope 脏代数和固定写回窗口批量提交 metadata 双 bank，不由单次微小写入同步触发全局快照。

`agentscan_ucore` 专门验证这条路径：系统先发现镜像中已有的 `usershell`，再通过普通文件 syscall 创建 `autoscan_ok`，确认无需显式调用 `agent_file_meta_set()` 也能查询到该文件；删除该文件后，下一轮扫描会清理对应元数据。

## 用户态初始化数据

`agent_file_meta_init()` 只负责强制选择最新有效 metadata bank 中属于调用者 scope 的记录、重建索引和启用扫描；其他 scope 的内存态对象不会被替换。没有 active bank 时会提交当前可持久记录作为第一代；已有 active bank 但两个 bank 都无效或存储忙时返回错误而不清空当前表。科研示例需要的设定的模拟流程、示例项目名和对象依赖由用户态 orchestrator 调用 `agent_file_meta_set()` 写入。该模拟流程包含数据准备、比对处理、结果分析、报告生成和归档交付。

`labdemo_ucore` 中由 orchestrator Agent 写入科研平台示例数据，再把设定的模拟流程中的比对处理对象状态改为 failed，从而触发 sentinel Agent。普通进程不能直接初始化或修改任一 workflow scope 的元数据状态。

`agentsecurity_ucore` 还会在初始化前先执行一次 indexed query，确认未初始化索引不会卡住；随后同时构造设定的模拟流程和另一个模拟流程两个 failed run，用于验证通用 action/artifact 更新只修改 selector 指定的目标 run。

`agentfs_ucore` 会创建额外真实文件，绑定自定义元数据，并验证重新调用 `agent_file_meta_init()` 时自定义数据来自 `.agentmeta` 重新加载，而不是被空表覆盖。它还会验证字段清空、文件删除清理、selector 未命中、scan/index 返回语义一致、结果超过 `max_hits` 时设置 `truncated`，并生成接近 128 条真实文件元数据，让扫描路径和索引路径的 `scanned_records` 差异明显。重复执行同一个非强制扫描查询时，它会验证 `CACHE_HIT` 原因位；随后更新文件状态，确认旧代数缓存不会继续返回过期结果。

## 查询路径

当前支持两条查询路径：

| 路径 | 使用方式 | 说明 |
| --- | --- | --- |
| 扫描路径 | `AGENT_FILE_QUERY_SCAN` | 遍历全部 128 条元数据槽 |
| 索引路径 | `AGENT_FILE_QUERY_USE_INDEX` | 根据 state/label/type 索引链减少候选记录；ABI 字段名兼容 status/stage/kind |

查询结果会解释本次选择：

| 字段 | 含义 |
| --- | --- |
| `plan` | 0 表示扫描，1/2/3 分别表示 state/label/type 索引 |
| `plan_reason` | 位标记，说明是强制扫描、未请求索引、使用了某类索引、命中查询缓存或没有可用索引键 |
| `index_bucket` | 命中的索引桶；扫描路径为 -1 |
| `candidate_records` | 本次候选记录数量，用于和全量扫描规模对比 |
| `fs_generation` | 查询时当前 workflow scope（包含可见 SYSTEM 对象）的缓存代数 |

这使 Agent 可以直接知道“为什么这次按 status 索引查，只检查了 6 条候选记录”，而不是只能看到最终命中结果。

## 查询结果缓存

任务四在索引路径之上加入了一个 8 槽内核查询结果缓存。缓存 key 包含查询 flags、归一化后的 `max_hits` 以及 physical、logical、project、workflow、run、stage、kind、status、summary_contains 等查询字段。缓存 value 保存完整 `agent_file_query_result`。

缓存服务非 `AGENT_FILE_QUERY_SCAN` 的命中查询。强制扫描查询总是重新遍历元数据，用于测试、诊断和对照索引路径；空结果不进入缓存，避免把“文件暂时不存在”当成稳定事实。自动扫描进行中允许读取代数仍匹配的缓存，保证其他 workflow 的稳定结果可以继续服务，但不创建新缓存；扫描产生实际 metadata 变化时只推进对象所属 scope 的 generation。缓存条目记录生成时当前 workflow scope 的 `fs_generation`；本域文件变化只增加本域代数，SYSTEM 对象变化才增加所有已存在 scope 的代数。旧缓存条目因代数不一致被跳过，无需遍历清空缓存表，也不会让一个低权限 workflow 的写入冲掉其他 workflow 的查询缓存。

命中缓存时，结果仍保留原查询计划、候选记录数、命中信息和 `fs_generation`，并额外设置 `AGENT_FILE_QUERY_REASON_CACHE_HIT`。因此 `plan_reason=68` 表示同一次查询既来自 status 索引计划，又命中了查询结果缓存。缓存命中不会追加额外的扫描成本，`query_ticks` 置为 0。

这个设计面向 Agent 的常见访问模式：同一个页面、同一个 Agent worker 或同一轮多 Agent 协作，经常会重复读取“某 run 的 failed artifact”“某 label 的输出”“恢复后的 report”。内核用 generation-aware cache 避免重复走索引链，同时保留强制扫描路径用于检查结果一致性。

索引路径适合 Agent 常见查询，例如：

- 查询某个 run 的 failed 文件；
- 查询某个 label 的输出；
- 查询某类 report 文件；
- 查询恢复后状态为 ok 的报告。

## 文件内容摘要

metadata 查询说明“哪个文件符合条件”，内容摘要工具说明“这个真实文件的开头内容和内容指纹是什么”。`AGENT_TOOL_READ_FILE_DIGEST` 使用 `read_file_digest` 工具名，输入为 `selector:string`。selector 可以是物理文件名、逻辑路径、label，也可以是 `namespace=...;run_id=...;label=...;state=...` 或兼容的 `project=...;run_id=...;stage=...;status=...` 属性过滤串。属性过滤命中多条时读取第一条命中文件。

该工具要求调用者具备 `AGENT_CAP_CONTENT_READ`。sentinel 这类只具备 metadata 读权限的 Agent 会收到 `AGENT_STATUS_DENIED`；`.agentmeta` 私有后端文件不会通过该工具暴露。工具返回：

| 字段 | 含义 |
| --- | --- |
| `value0` | 真实文件大小 |
| `value1` | 参与 FNV-1a 指纹计算的字节数，最多 4096 |
| `value2` | FNV-1a 64 位内容指纹 |
| `result` | 文件开头短预览 |

这不是全文搜索和内容倒排索引。它提供的是轻量内容证据：Agent 在得到 metadata 命中后，可以进一步确认文件确实存在、文件内容和预期一致，并把该工具调用写入 Context Path、timeline 和性能输出。统一 timeline 中可以按 `tool_id=AGENT_TOOL_READ_FILE_DIGEST` 查询到该记录，`value0/value1/value2/text` 分别保留文件大小、参与计算字节数、内容指纹和短预览。

### 内容摘要缓存

`read_file_digest` 还维护 8 槽内核 digest cache。缓存 key 是真实 inode 身份和文件内容版本：

- `dev`
- `inum`
- `incarnation`
- `size`
- `content_generation`

缓存 value 保存文件大小、参与计算字节数、FNV-1a 指纹和短预览。重复读取同一个绑定了 Agent metadata 的真实文件时，第二次可以直接复用缓存结果。文件创建、写入、截断或删除会改变 incarnation 或内容版本，旧缓存条目会被跳过；单纯 metadata 更新不会让同一文件内容摘要缓存失效。

未绑定 Agent metadata 的普通文件不会进入 digest cache。这样做是为了避免普通文件同尺寸改写时，Agent 子系统缺少可靠内容版本信号而返回过期内容证据。缓存计数通过 `agent_info.file_digest_cache_hits` 和 `agent_info.file_digest_cache_misses` 暴露，供测试和性能材料直接引用。

## 查询历史驱动的预取提示

文件查询命中后，内核会把本次查询视为 Agent 当前探索路径的一部分，并根据源文件的对象标签依赖推导后续可能需要关注的文件 metadata。例如科研示例查询设定的模拟流程中的比对处理 label 后，如果用户态注册的依赖关系显示比对处理会影响结果分析、报告生成和归档交付，内核会在同一 namespace/workflow/run 中查找这些后续对象的元数据，并生成预取提示。

预取提示的生成条件：

1. 当前进程必须是 Agent；
2. 文件查询必须至少命中一条记录；
3. 命中的 source 文件有 `dependency_mask`；
4. target 文件与 source 位于同一 namespace/workflow/run；
5. target label 位于 source 的依赖集合中；
6. target label 可以通过 label 索引解释候选记录数量。

查询结果和查询缓存都保存命中项对应的精确 metadata slot。生成预取时，内核先按每 scope 16 条依赖配额收集选择器；label hash 只作预筛，最终仍精确比较 label、namespace、run 和 workflow。随后只扫描一次文件表，以槽位位图对所有 source 的目标全局去重；没有显式目标的 source 才使用兼容 `dependency_mask` 候选。整次查询最多发布 8 个唯一目标，dependency、文件表、提示总线和 handoff 的可扩展遍历都计入 metadata 工作预算。handoff 在事件入队时只捕获稳定端点句柄，预算化阶段只处理一个 hint 的局部副本；固定表扫描预算预付后，再用 `slot + pid + control_id + scope` 重新解析接收者并执行不可调度的有界提交。

每个 Agent 最多保留 8 条本地预取提示。相同 target fid 的提示会被更新；容量满后按 FIFO 方式替换较早提示。带有 span 的提示还会写入全局 span 预取提示总线，最多保留 32 条，并记录 source pid 和 target pid。提示使用 `AGENT_FILE_PREFETCH_REASON_DEPENDENCY`、`AGENT_FILE_PREFETCH_REASON_SAME_RUN`、`AGENT_FILE_PREFETCH_REASON_PENDING`、`AGENT_FILE_PREFETCH_REASON_STAGE_INDEX`、`AGENT_FILE_PREFETCH_REASON_HANDOFF` 和 `AGENT_FILE_PREFETCH_REASON_SPAN_BUS` 说明生成原因。其中 `HANDOFF` 表示提示来自另一个 Agent 的 message 事件交接，`SPAN_BUS` 表示该提示已进入同一 span 的全局提示总线。若接收 Agent 在预算检查点期间退出，端点重校验失败后不会向已回收或复用的 PCB、本地 ring、span bus、timeline 或 audit 发布任何交接副作用。

读取接口：

```c
int agent_file_prefetch_snapshot(struct agent_file_prefetch_hint *hints, int max);
int agent_file_prefetch_span_snapshot(struct agent_file_prefetch_hint *hints, int max);
```

`agent_file_prefetch_snapshot()` 查询当前 Agent 自己可见的提示。`agent_file_prefetch_span_snapshot()` 查询当前 Agent 的 `current_span_id` 对应的全局提示，只返回同一 span 下的记录；当前 Agent 尚未进入 span 时返回 0。两者在 `max=0` 时只返回当前提示数量；`max>0` 会按产生顺序复制提示。普通进程调用返回 `-1`；没有 `META_READ` 能力的 Agent 返回 `AGENT_STATUS_DENIED`。

这项能力不是完整文件内容预加载。它的作用是把“Agent 查到一个阶段后，后续大概率会继续查哪些相关工件”提前交给内核表达，减少下一轮 Agent 继续做宽泛扫描或重新拼接依赖关系的成本。Agent 之间通过 message 事件协作时，内核可以把发送者当前可见的提示复制给接收者，让接收者直接从自己的 snapshot 中读取上游提示，而不需要从消息文本中解析策略字段。
同一 span 的提示总线进一步减少了跨 Agent 协作时的状态拼接成本：接收者不仅能读取自己 PCB 中被交接来的提示，还能用 span 查询看到“这条因果链中是谁产生了提示、提示交给了谁、目标工件是什么”。这为后续宿主机科研 Agent 对比示例提供了更直观的内核级协作证据。

## 工具接口

任务四能力既可以通过 syscall 直接调用，也可以通过工具调用进入：

| 接口 | 说明 |
| --- | --- |
| `agent_file_meta_init()` | 初始化文件元数据表 |
| `agent_file_meta_set(meta)` | 插入或更新一条元数据 |
| `agent_file_query(query, result)` | 结构化查询 |
| `agent_file_prefetch_snapshot(hints, max)` | 读取当前 Agent 的 metadata 预取提示 |
| `agent_file_prefetch_span_snapshot(hints, max)` | 读取当前 span 的全局 metadata 预取提示 |
| `AGENT_TOOL_QUERY_FILE` | 工具方式查询文件 |
| `AGENT_TOOL_READ_FILE_SUMMARY` | 按 selector 读取摘要 |
| `AGENT_TOOL_READ_FILE_DIGEST` | 按 selector 读取真实文件短预览和内容指纹 |
| `AGENT_TOOL_DEPENDENCY_QUERY` | 查询某个对象 label 的影响范围 |
| `AGENT_TOOL_ACTION_COMMIT` | 按 selector 提交通用 Agent 动作 |
| `AGENT_TOOL_ARTIFACT_UPDATE` | 按 selector 更新通用工件或结果对象状态 |

`AGENT_TOOL_QUERY_FILE` 支持属性条件串，例如：

```text
project=<示例项目>;run_id=<设定的模拟流程>;status=failed
```

通用动作工具 `AGENT_TOOL_ACTION_COMMIT` 支持同样的 selector 风格，例如：

```text
label=align;run_id=<另一个模拟流程>;namespace=<示例项目>
```

通用工件更新工具 `AGENT_TOOL_ARTIFACT_UPDATE` 也支持 selector 风格，例如：

```text
label=report;run_id=<另一个模拟流程>;namespace=<示例项目>
```

内核会同时匹配 label、run_id 和 namespace。这样动作提交和工件更新不会因为同一个 label 上存在多个 run 而误修改其他文件元数据。`AGENT_TOOL_RERUN_STAGE` 和 `AGENT_TOOL_WRITE_REPORT` 仍保留为旧示例兼容工具，但它们内部调用同一套通用状态更新路径，并把事件 action 与重复请求判断归入 `action_commit` 或 `artifact_update`。

`query_file` 对空查询、未知 key 和坏格式片段返回 `AGENT_STATUS_BAD_PARAM`，不会静默忽略错误条件。

## 与 Context Path 的关系

文件查询成功后会追加 Context record。这样 Agent 的“看到什么文件、做出什么判断”可以在 Context Path 中回放。Context record 同时保存 cause/span：如果这次文件查询来自前一个事件或工具调用，它会指向对应的前序 sequence，并延续当前 span。

文件元数据写接口要求调用者是 Agent 且具备 `AGENT_CAP_META_WRITE`。当前只有 orchestrator 拥有该能力；sentinel、investigator 和 recovery 可按各自能力读取元数据或内容，但不能直接改写当前 workflow scope 的文件状态。

在 `labdemo_ucore` 中：

1. sentinel 查询失败文件；
2. sentinel 读取本次查询产生的 metadata 预取提示；
3. sentinel 发送普通 investigate 消息，内核在 message 入队时交接预取提示，并写入同一 span 的全局提示总线；
4. investigator 查询 align 摘要；
5. investigator 查询依赖；
6. investigator 从自己的 snapshot 读取带 `HANDOFF` 原因位的 analyze 提示，也从 span snapshot 验证 source/target pid，并据此读取 analyze 摘要；
7. recovery 查询报告；
8. 这些工具调用都会进入各自 Agent 的 Context Path。

## 与 Agent Loop 的关系

`agent_file_meta_set()` 更新文件状态时，会检查是否有 Agent 注册了匹配 watch。如果某个 Agent 监听 `status=failed` 或兼容的 state 条件，更新为 failed 的文件会产生 `AGENT_EVENT_FILE_STATUS`，并唤醒等待中的 Agent。事件会携带触发本次状态变化的 cause/span，目标 Agent 消费事件后可以把后续查询和动作提交接到同一因果链。

这正是 `labdemo_ucore` 的启动条件：

1. sentinel 注册 failed 状态监听；
2. orchestrator 把 align 阶段文件更新为 failed；
3. sentinel 从 `agent_wait()` 返回；
4. sentinel 查询失败文件并启动后续分析。

## 依赖关系查询

`dependency_update` 是通用依赖注册工具，payload 使用 `source/target/namespace/run_id/relation/summary` 这组字段。内核只保存显式注册的对象标签关系，不解释这些标签的业务含义。`dependency_mask` 仍作为紧凑兼容 ABI，表示某个对象 label 会影响哪些后续对象 label；每个 label 通过稳定 hash 映射到一个 bit。该位图保留在文件 metadata 中并按需解析，不复制成全局依赖记录。用户态写入元数据或调用 `dependency_update` 后，内核会按同一 namespace 和 run_id 查询依赖：

| 字段 | 说明 |
| --- | --- |
| `namespace` | 对象所属命名空间，兼容字段为 `project` |
| `run_id` | 本次运行或任务实例 |
| `source` | 产生影响的对象 label |
| `target` | 被影响的对象 label |
| `relation` | 当前为通用 `depends_on` |
| `summary` | 目标对象摘要 |

`dependency_query("label=align;namespace=<示例项目>;run_id=<设定的模拟流程>")` 返回：

```text
align+analyze+report+archive
```

查询可以带 namespace 和 run_id。同一个 namespace 下如果存在多个 run，内核只返回所选 run 的依赖影响范围；不带这些字段时则返回该 label 当前可见的合并结果。这个例子来自科研平台用户态初始化数据，不是内核固定规则。换成代码 Agent、运维 Agent 或写作 Agent 时，用户态可以用 `dependency_update` 写入 `parse -> compile`、`alert -> diagnose`、`outline -> draft` 之类的 label 关系，内核按同一套依赖记录查询和生成预取提示。

## 性能验证

`agentbench_ucore` 对比扫描路径和索引路径。tick 数值只作为观测，重点看 `scan_records` 与 `index_records` 的候选记录数差异：

原始输出见 [test-record.md](test-record.md)。这些观测用于确认当前系统同时具备：

- 可观测扫描路径；
- 可观测索引路径；
- 可输出 `used_index`、`scanned_records`、`plan`、`plan_reason`、`candidate_records` 和缓存命中原因；
- 可用受权工具读取真实文件短预览和内容指纹；
- 可用 `agent_info` 观察内容摘要缓存命中和未命中；
- 可输出由历史查询和对象标签依赖生成的 metadata 预取提示；
- 能用候选记录数和多轮 tick 观测解释索引价值。

## 综合场景中的证据

`labdemo_ucore` 中的文件查询证据说明：

- sentinel 能按属性查询失败文件；
- sentinel 能读取查询历史驱动的预取提示；
- investigator 能通过内核交接的预取提示读取摘要、真实日志 digest、依赖和后续 label 摘要；
- recovery 后能查询报告文件；
- 查询路径使用索引。

`agentsecurity_ucore` 说明索引初始化前查询安全，且 recovery 只更新 selector 指定的 run。`agentfs_ucore` 说明真实文件关联、预取提示、自定义 inode、内容摘要、digest cache、`.agentmeta` reload、查询缓存、scan/index 一致性、截断查询、字段清空、删除清理和 selector 未命中都可验证。`agentscan_ucore` 说明根目录后台扫描、普通文件创建后的自动元数据和文件删除后的清理都可运行。

`agentvfs_ucore` 进一步验证 public/workflow 路径隔离、普通进程不能读写或删除 workflow 工件、读写 worker 的映像 profile 上限、错误映像不能取得 pending 委派、普通 fork 不继承文件能力、继承 fd 会重新鉴权，以及 public 命名空间仍可由普通进程使用。

原始输出统一见 [test-record.md](test-record.md)，测试步骤见 [testing-details.md](testing-details.md)。

## 当前限制

| 限制项 | 说明 |
| --- | --- |
| 元数据来源 | 当前来自私有 metadata 双 bank、用户态 `agent_file_meta_set()` 和根目录自动扫描；首次无后端状态时内核只安装空表 |
| 文件系统扫描范围 | 当前自动扫描 uCore 根目录短文件名，不做多级目录递归 |
| 持久化索引 | 元数据表事务写入并重新加载 `.agentmeta` / `.agentmeta1`；内存索引启动后根据元数据重建 |
| 查询语法 | 当前支持结构体字段查询和简单 `key=value` 字符串 |
| 查询规模 | 当前最多 512 条元数据，单 workflow scope 最多 112 条，单次最多返回 8 条 hit |
| 内容摘要 | 当前读取最多 4096 字节计算指纹，返回短预览，不做全文索引 |
| 预取提示 | 当前只生成 metadata 提示，提示本身不预读文件内容，不保存到磁盘 |
| 文件安全域 | 当前实现 PUBLIC、SYSTEM、最多 4 个 active workflow，以及 active + retiring 合计不超过 8 的生命周期域；退役积压最多 8 个，身份槽只在 `used == 0` 后复用，尚未提供任意数量的用户命名域或用户可编程动态策略语言 |
| 故障验证 | 当前回归覆盖正常 I/O、ENOSPC 和重载一致性；没有注入启动 bank 损坏、VirtIO 短写/设备错误或 metadata COW 中途掉电，不据此声称完整崩溃原子性 |
