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
- 把兼容字段 `DURABLE/PERSIST/AUTOSCAN/recovery` 误解释为当前能力；
- 将用户态/Host 自报成功冒充内核或正式证据。

不在当前证明范围：恶意内核、敌对 hypervisor/Host、物理内存篡改、供应链攻击、密码学密钥保护、物理断电后的 metadata/evidence 恢复、完整磁盘机密性。

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
| evidence | workflow key + ticket/fence sequence |

旧 generation 无法通过数值复用获得新 generation 的权限。

## 3. lifecycle cut

workflow 只有 `members + closing` 核心生命周期，加三类 gate：

- operation gate：普通操作进入前计数；closing/fence 后拒绝新进入；
- departure gate：exit/teardown 与 fence 互斥；
- fence gate：只由当前 controller 关闭，在已有 operation/departure 为零时取得 cut。

fence begin 不能在持 gate 时睡眠；发现非 quiescent 立即撤销并返回 `RETRY`。reclaim 要求 closing、members/operations/departures 为零且 fence gate 关闭。这样不需要多个 retirement phase，也不会在不同子系统各自猜测 workflow 是否结束。

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

## 10. 验证入口

```bash
python -B scripts/test-workflow-credit-domain.py
python -B scripts/test-agent-evidence-ring.py
python -B scripts/test-agent-live-query-fs.py
python -B scripts/test-workflow-fence.py
python -B scripts/test-workflow-syscall-cut.py
make agent-module-check TOOLPREFIX=riscv-none-elf-
make kernel-budget-check TOOLPREFIX=riscv-none-elf-
```

静态/模型检查验证安全合同形状。QEMU 行为和正式性能结论仍需独立 Guest/Host 证据。

## 11. 来源与 clean-room 边界

Linux CPU accounting/percpu/rstat、Linux BPF ring buffer、Haiku BFS live query 分别提供批量计数、有序事件环和属性实时查询的概念参考。项目没有复制或 vendoring 这些上游的源码、数据、二进制、测试或磁盘格式。完整链接与披露见 [../../NOTICE](../../NOTICE)。
