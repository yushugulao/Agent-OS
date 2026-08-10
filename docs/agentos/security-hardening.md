# AgentOS 安全加固

本文记录当前生产构建的安全边界。核心原则是 generation-safe 身份、最小 capability、硬资源准入、有界队列、显式 resync 和 fail-closed workflow fence。本文不把内存 seal 描述为磁盘持久性。

## 1. 威胁模型

假设普通用户程序、低权限 Agent、同 workflow 的非 controller worker 和错误 Host 输入可能恶意或失效。需要阻止：

- 伪造 Agent role/capability/controller；
- 通过 PID、inode number、slot reuse 命中旧对象；
- 跨 scope 读取/修改文件或投递 IPC；
- 用普通事件淹没关键拒绝/控制事件；
- 通过批量 credit 超卖全局或账户硬额度；
- fence 与仍在运行/释放的 workflow 操作交叉；
- 把不完整 live-query 增量当作完整状态；
- 从不可信文件、工具结果或跨 Agent 消息诱导计划外的发消息、改文件、起进程或提权；
- 通过伪造 contract node、schema digest、predecessor Context、Task handle 或 ring generation 越权；
- 通过多建线程放大 workflow CPU 份额；
- 通过共享 SQ 页 TOCTOU、伪造 CQ ack、重复 cancel/complete 或 stale handle 产生双终态；
- 把兼容字段 `DURABLE/PERSIST/AUTOSCAN/recovery` 误解释为当前能力；
- 将用户态/Host 自报成功冒充内核或正式证据。

不在当前证明范围：恶意内核、敌对 hypervisor/Host、物理内存篡改、供应链攻击、密码学密钥保护、物理断电后的 metadata/evidence/task 恢复、完整磁盘机密性、自然语言 prompt injection 的语义识别、任意远程副作用的分布式 exactly-once。

## 2. 身份与 capability

Agent 身份只能由受控创建/exec 安装。可信映像注册给出 role/capability 上限，请求还受父身份、scope 和 VFS profile 衰减。`control_id` 由内核分配；用户不能写入 Context cause/span/control 字段制造授权关系。

所有长寿命引用至少绑定：

| 对象 | 不可省略的身份 |
| --- | --- |
| workflow | lifecycle id + generation + scope |
| controller | workflow key + control id |
| process/watch/wait | process slot/pointer + process/control generation |
| file | dev + inum + incarnation + scope/lifecycle |
| resource account | slot + generation + kind/external id |
| execution contract/node | lifecycle key + contract generation + node/attempt + schema/input digest |
| Task Channel | process/main-thread identity generation + lifecycle + channel/ring/slot generation |
| typed resource | channel generation + slot/type/owned-borrowed + resource generation + owner request |
| evidence | workflow key + ticket/fence sequence |

旧 generation 无法通过数值复用获得新 generation 的权限。

## 3. lifecycle cut

workflow 只有 `members + closing` 核心生命周期，加三类 gate：

- operation gate：普通操作进入前计数；closing/fence 后拒绝新进入；
- departure gate：exit/teardown 与 fence 互斥；
- fence gate：只由当前 controller 关闭，在已有 operation/departure 为零时取得 cut。

fence begin 不能在持 gate 时睡眠；发现非 quiescent 立即撤销并返回 `RETRY`。reclaim 要求 closing、members/operations/departures 为零且 fence gate 关闭。这样不需要多个 retirement phase，也不会在不同子系统各自猜测 workflow 是否结束。

### 3.1 冻结执行合同与 effect fence

每个 lifecycle generation 最多冻结一份 24 节点 execution contract。节点按拓扑顺序声明 tool/schema、predecessor、artifact、deadline、retry/cancel、capability、provenance/effect 和 exec/storage envelope。V3 调用必须引用完整 contract generation、node/attempt、32 字节输入 digest、source node 和该 predecessor 已提交的精确 Context sequence。

副作用前的 gate 顺序是 lifecycle、contract/dependency/deadline、capability/provenance/effect、Evidence reservation、Phase Lease、effect fence。取消只有在 effect fence 前能成为 `CANCELLED` winner；效果开始后 cancel too late，原调用仍负责唯一终态。每个 accepted node/attempt 都有一个 retry-stable completion 槽，每份合同合计最多 48 槽；相同 attempt 的合法重试只读原终态，不重新执行。enforcement contract 下，合同外工具或受保护 direct syscall 不会绕过同一边界。

### 3.2 Context Provenance

六个固定标签为 `KERNEL_FACT`、`TRUSTED_USER_CONTROL`、`AGENT_DERIVED`、`UNTRUSTED_FILE_DATA`、`UNTRUSTED_TOOL_OUTPUT` 和 `CROSS_AGENT_DATA`。标签保守 OR 传播；file/query、tool output、IPC/mailbox，以及 Task core 中任何 live resource，都必须携带来源，不能通过一次 Agent 变换洗掉 untrusted 位。当前 Task bridge 不能 import/create resource，因此该规则不是 payload backend 的交付声明。

tool manifest 声明 accepted/output labels、required capability 和完整 side-effect mask。任何外部效果必须同时通过完整 generation、冻结依赖边、capability 和数据流规则。不可信内容不能引入合同外高权限节点。拒绝在副作用前返回 `DENIED`，并把 source sequence、tool、reason、lifecycle 和 ticket 写入 critical Evidence Ring；关键 evidence 无法预留时调用 fail closed。

这是一项结构安全机制，不是 prompt-injection 文本分类。内核不判断某段文本是否恶意，也不调用 LLM guard；安全论证是“模型可以被影响，但结构化请求不能越过冻结边界”。

## 4. 资源硬额度

Workflow Credit Domain 的安全不变量：

```text
held(account,class,kind) = U + P + F
held <= account class limit
sum held <= global/class capacity
P belongs to a live reservation
U belongs to a published live object
F may be reclaimed or trimmed, never oversold
```

补充 F 前先完成完整向量验证；失败不修改任何 resource kind。压力回收只能 trim 空闲 F，不能偷取 U/P。reservation commit/cancel 严格把 P 移到 U/F。真实对象死亡后才 `U -> F`，避免先释放计数再释放对象的窗口。

Tool Phase Lease 只锁定账户已经持有的 U，不扩展 `held`。普通 release/transfer 必须扣除 locked/claimed 后再判断可用 U；active phase 不能退回普通 F/P admission。对象发布前的 claim 同时绑定 lease generation、nonce、account/class 和完整资源向量：失败精确 refund 到 locked U，成功发布由对象析构负责一次 `U -> F`，未消费 envelope 在 settle 时 `U -> F`。线程 exit/process teardown 会扫描并清理所属 lease；存在 outstanding claim 时不得跨越不安全阻塞或静默回收。

fence 单锁 trim exec/storage，并拒绝任何 P 非零快照。receipt 导出的 `resource_used[]` 是该 cut 的 U；credit digest 同时绑定 lifecycle、epoch 和账户 generation，不能把另一账户的数字拼入 receipt。

批量设计受 Linux percpu/rstat 思想启发，但硬限额校验和 fence cut 是 Agent workflow 特定实现。

## 5. Evidence Ring 安全

### 5.1 并发发布

slot 必须按 `FREE/DISCARDED -> BUSY -> COMMITTED` 转移。reservation 保存 key、slot 和 ticket；commit 时逐项重验。读者使用 acquire load，只读取 COMMITTED 且 ticket 匹配的完整 event，并隐藏较早 BUSY ticket 之后的记录。

fill 在 IRQ-on 完成，避免大结构复制扩大 IRQ-off 临界区。discard 进入 gap counter；系统不把失败写入伪装为完整序列。

### 5.2 关键证据隔离

授权效果或非 OK status 固定进入 16 槽 critical 区，普通成功进入 48 槽 ordinary 区。两区不竞争同一槽，使大量成功 telemetry 不能直接覆盖 critical 记录。critical 同时产生兼容 ledger 投影；ring 失败则 fail closed 到 legacy protected ledger。

### 5.3 seal

SHA-256 输入采用 domain separation，并绑定 previous root、challenge、fence sequence、ordered event/gap、metadata generation 和 credit digest。内部 rollover/retirement root 与对外 workflow fence root 使用不同标志；内部 seal 不能冒充 challenge receipt。

request id cache 防止 copyout 重试重复推进 root。同 id 不同 challenge 为 conflict，旧 id 为 stale，未完成 copyout 时新 id为 retry。

### 5.4 不提供的保证

ring 与 retained seal 都在内存。`EVIDENCE_SEALED` 不等于 fsync、disk durable、crash recoverable 或远程认证。receipt 明确设置 `PARTIAL_COVERAGE`，不能用它证明未进入 ring 的全部内核/Host 行为。

## 6. Live Query 安全

### 6.1 显式准入

只有 `META_WRITE` Agent 能登记 metadata。普通 VFS create 不自动提升为 workflow metadata，避免攻击者通过预创建文件获得高层属性。`PERSIST/AUTOSCAN` 当前被拒绝，阻止旧调用者意外依赖已停产语义。

### 6.2 scope 与 incarnation

query/watch transition 先验证 requester scope 对 owner scope 可见。VFS tombstone/content pending 保存完整 lifecycle、scope、dev/inum/incarnation；处理时重验，旧对象或跨 scope receipt 不能更新新 catalog entry。

### 6.3 typed watch

subscription 绑定 target、control id、workflow key 和 typed predicate。typed `FILE_QUERY` 不回退到字符串 filter。proc reuse/exec/exit 清除 token，旧 watch id 不能控制新 Agent。

### 6.4 resync

队列或 pending 容量不足时标记单调 resync generation，并投递 `RESYNC_REQUIRED`。旧 ACK 只清除不晚于自己的缺口；domain 表耗尽升级为 global resync，而不是静默丢失。fence 若仍有 pending/resync 返回 `RETRY`，避免 receipt 绑定不完整 metadata generation。

catalog 是 volatile 内存状态。没有恢复 bank 就没有“验证失败后回退旧 bank”的安全声明；重启后用户态必须重新登记。

## 7. VFS、exec 与 IPC

- PUBLIC、SYSTEM 和动态 workflow scope 在 lookup/open/read/write/truncate/unlink 执行访问检查。
- 文件 metadata 不能反向改变 inode 创建时的安全域。
- worker 委派绑定目标映像 incarnation 和 capability 上限；执行其他映像清除委派。
- 普通 fork 不扩大 capability，跨 scope fd 撤销，每次 I/O 重新检查当前凭据。
- MESSAGE/LLM 跨 Agent 投递必须命中同 lifecycle 定向 route。
- `agent_wake()` 不能伪造 file-query、timer、policy-denied 或 LLM completion。

### 7.1 Workflow EEVDF

调度 key 同时绑定 lifecycle、resource account generation 和 domain id。CPU service 按 workflow 而不是线程累计，因此 fork/thread fan-out 不增加公平实体。只有 lag eligible 的 workflow 参加 virtual-deadline 比较；睡眠 lag 只向全局 virtual time 衰减，不能通过短睡眠重置欠账。

调度选择采用 read-only plan 后原子 commit；验证失败不污染 `vruntime`/lag。实体表总计最多 4 个，其中 1 个槽由 `BOOT_SEALED` bootstrap participant 占用，fresh workflow 最多 3 个；单 workflow 走 fast path，普通进程和容量/身份异常回退既有 RR/Agent scheduler 并记录 fallback。4-way 安全/公平测量使用 bootstrap+3 fresh；线程放大测量使用 1 个 fresh 4-thread workflow、2 个 fresh single-thread workflow 和 bootstrap peer。16 档是四波复用同一 bootstrap 加 12 个 fresh lifecycle 的逻辑样本，不是 16 个并发或独立实体，也不是每波 4 个 fresh；唤醒直方图只聚合 fresh-agent 样本。

### 7.2 Task Channel

- setup 只接受当前 Agent main thread 作为 single issuer，并绑定 thread identity generation、process 和 lifecycle；
- SQ 为用户 read/write，CQ 为 read-only；request/resource 权威状态保存在两个不映射的私有页；
- 内核只从共享 entry 读取一次，完整复制 128 字节后再验证，后续不重读用户 SQE；
- ring/slot/channel/resource generation、严格单调 request id 和完整 contract binding 防止 ABA/replay；
- CQ ack 必须位于私有 `cq_head..cq_tail`，用户可见 header 不是授权来源；
- CQ full 只产生 backpressure；协议错乱进入 sticky resync，不能通过伪造水位跳过 completion；
- accepted target 只发布一个 terminal CQE，cancel command 不另发 CQE；late cancel 不覆盖已经开始效果的原 winner；
- timer IRQ 只标记 hard deadline 到期，重型 expire 在首个 schedulable safe point 争取唯一终态，不在 IRQ 中释放 page 或调用 provider，也不承诺 wall-clock 终止上界；
- reclaim 等待 callback/lifecycle pin，typed handle 用 slot/type/flags/generation 和 8 槽私有 owner/digest/provenance 表重验。

Task Channel 的 exactly-once 只约束终态 CQE 发布，不证明远程工具副作用具备分布式 exactly-once。typed handle 也不等于完整 Wasm/WASI runtime。

core callback 协议允许 provider 返回 `PENDING`，但当前 bridge 只有内建同步 provider，且没有动态 provider registration UAPI。该 provider 只接受 null input 与 output artifact `NONE`；`RESOURCE_IMPORT` 固定返回 `DENIED`，所以当前没有 payload import 或 result resource backend，CQE result handle 保持 null。

### 7.3 MCP/A2A gateway

当前 gateway 是 transport-neutral 的纯用户态映射，测试使用 deterministic in-memory transport；它尚未通过 binary adapter 连接真实内核 SQ/CQ。gateway 把 binding 层已经认证的 remote task id 绑定到 issuer/tenant/subject 以及 lifecycle/contract/channel/request generation，未知 issuer、tenant mismatch、protocol version mismatch 和越权 task lookup 均 fail closed。远程 JSON schema digest 与内核 manifest digest 分离，外部 schema 不能替代内核 tool authority。HTTP、OAuth、JWS、Agent Card 签名和网络重放防护由外部 binding 层负责，当前模块本身不实现这些协议，也不会把未验证 envelope 直接提交给 Task Channel。

## 8. 兼容观测接口

保留 audit/timeline/provenance/ledger 是为了现有用户态读取，不是第二套 durable store。聚合视图会去重被 Evidence Ring shadow 的 Context 投影。

`agent_audit_receipt()` 的肯定状态是 `FENCE_SEALED`。`AGENT_AUDIT_DURABILITY_DURABLE` 只是同值别名。observe recovery syscall 为 tombstone，固定 `BAD_PARAM`。任何文档、页面或测试不得把这些兼容名称重新解释为磁盘恢复。

## 9. 失败处理

| 失败 | 行为 |
| --- | --- |
| resource vector 不可准入 | 修改前拒绝 |
| Evidence Ring 暂不可用 | ordinary 记录进入 gap/fallback；关键记录保留受保护 ledger |
| fence 存在 operation/departure | `RETRY`，不推进 root |
| live-query pending/resync 未清空 | `RETRY`，不发布 metadata cut |
| fs epoch cut 失败 | `IO_ERROR`，撤销 fence gate |
| receipt copyout 失败 | 保留 retry-stable cache，不允许新请求覆盖 |
| old lifecycle/watch/request id | `STALE`/拒绝 |
| legacy PERSIST/AUTOSCAN/recovery | `BAD_PARAM` |
| contract/node/schema/predecessor 不匹配 | 副作用前 `DENIED/STALE/CONFLICT`，关键拒绝进入 Evidence |
| provenance/capability/effect 不满足 | 副作用前 `DENIED`；关键 Evidence 无空间则 fail closed |
| phase envelope/claim 不满足 | 不回退普通准入；refund/settle 或拒绝，不部分发布资源向量 |
| EEVDF 身份/状态异常 | 不提交计划，回退 legacy scheduler 并计数 |
| Task CQ full | 保留唯一终态，设置 backpressure，稍后发布 |
| Task ring/slot 水位错乱 | sticky `RESYNC_REQUIRED`，只从私有权威状态恢复 |
| cancel 与 completion 竞争 | effect fence/terminal winner 只允许一个 CQE；too-late 不覆盖原执行 |
| Task `RESOURCE_IMPORT` | 当前 bridge 固定 `DENIED`，不创建 handle 或 payload/result backend 状态 |
| hard deadline 到期 | timer 只标记；首个 schedulable safe point 终止，无 wall-clock 上界 |

## 10. 验证入口

```bash
python -B scripts/test-workflow-credit-domain.py
python -B scripts/test-agent-evidence-ring.py
python -B scripts/test-agent-live-query-fs.py
python -B scripts/test-workflow-fence.py
python -B scripts/test-workflow-syscall-cut.py
python -B scripts/test-agent-execution-contract.py
python -B scripts/test-agent-task-channel.py
python -B host_tools/test_workflow_scheduler_model.py
python -B host_tools/test_agent_task_transport.py
python -B host_tools/test_mcp_a2a_gateway.py
make agent-module-check TOOLPREFIX=riscv-none-elf-
make kernel-budget-check TOOLPREFIX=riscv-none-elf-
```

静态/模型检查验证安全合同形状。QEMU 行为和正式性能结论仍需独立 Guest/Host 证据。

## 11. 来源与 clean-room 边界

Linux CPU accounting/percpu/rstat、Linux BPF ring buffer、Haiku BFS live query 分别提供批量计数、有序事件环和属性实时查询的概念参考。新增合同主线还参考 [Linux EEVDF](https://docs.kernel.org/scheduler/sched-eevdf.html)、[io_uring SQ/CQ](https://kernel.dk/io_uring.pdf)、[WIT/WASI 0.3](https://component-model.bytecodealliance.org/design/wit.html)、[AgentCgroup](https://arxiv.org/abs/2602.09345)、[Murakkab](https://arxiv.org/abs/2508.18298)、[CaMeL](https://arxiv.org/abs/2503.18813)、[IPIGuard](https://arxiv.org/abs/2508.15310) 和 MCP/A2A 官方协议。项目没有复制或 vendoring 这些上游/原型/SDK 的源码、数据、二进制、测试或磁盘格式；没有把 io_uring/Wasm 或远程协议栈整体搬入内核。完整链接与披露见 [task6-execution-contract.md](task6-execution-contract.md) 与 [../../NOTICE](../../NOTICE)。
