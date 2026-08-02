# 验证与性能评估

本文档说明 AgentOS-uCore 专项测试如何运行、每个测试覆盖哪些能力、性能数据如何解读。逐项流程见 [testing-details.md](testing-details.md)，原始输出样例见 [test-record.md](test-record.md)。

发布状态不绑定可变工作树或本文的“最近一次”文字，而绑定 `evidence/releases/INDEX.md` 与对应 release bundle 的 `manifest.json`。代码提交 C 必须先冻结；采集器在干净 C 上执行唯一一次 `make full-verify`，随后证据提交 E 作为 C 的直接子提交，只新增该 bundle 并精确追加索引。包内 `source_commit` 仍指向 C，承载提交由 `SELF` 解析为 E。C 的本地完整验收和可校验 bundle 可形成 E3，不依赖远端 Runner；没有可用 Runner 时 `remote_ci.status` 必须保持 `not-attached`，只阻止同一 C 的远端 attestation 等级 E4。下文带日期、提交号或旧数值的段落都是历史记录，不能外推到未被 bundle 绑定的代码。

## 验证组织方式

AgentOS-uCore 的验证分五层：

| 层次 | 入口 | 作用 |
| --- | --- | --- |
| 构建检查 | `make agentos-user`、`make agentos-build`、`make kernel-stack-check` | 确认内核、用户态 ABI 和文件系统镜像能从当前源码构建。 |
| 增长与边界门 | `make ci-check` | 固定 profile 下检查源码、镜像、运行段、PCB、栈深/容量、完整 Agent 状态、版本化 owner/bridge 集合及 metadata 聚合 source/text/BSS；不启动 QEMU。 |
| AgentOS 专项测试 | `make agentos-test` 或 `bash scripts/run-agent-tests.sh` | 在 QEMU 中逐项运行 Agent 功能、权限和用户输入检查。 |
| 资源与持久化复测 | `make fs-enospc-test`、`make fs-allocator-fault-test`、`make proc-reap-test`、`make thread-resource-test`、`make file-resource-test`、`make physical-resource-test`、`make metadata-recovery-test`、`make observe-recovery-test`、`make virtio-disk-test`、`make syscall-fairness-test`、`make workflow-teardown-race-test` | 动态验证存储/物理页资源边界、统一 teardown、metadata/观测重启恢复、VirtIO 故障矩阵、文件系统分配事务一致性和跨资源竞态。 |
| 双目标与聚合验证 | `make dual-platform-run`、`make full-verify` | 运行双目标负载；profile v5 按固定顺序串联 Host/Reader、18-case Agent、双目标及十一类机制专项。 |

`agentos-test` 和 `thread-resource-test` 只关注根目录 AgentOS-uCore 目标；`fs-enospc-test`、`proc-reap-test`、`file-resource-test` 和 `syscall-fairness-test` 同时覆盖根目录增强目标与 `baseline_ucore/` 普通目标。双目标验证详情见 [../verification.md](../verification.md)。

双目标的 AgentOS launcher 由共享 `RP_AGENTOS_ROLE_PROGRAMS` 清单区分真实 `agent_create_role()` 角色进程和 `agent_worker_create()` 非 Agent worker，不再从父进程的预期分支反推子进程身份。每个子进程在 `exec` 后由通用启动钩调用 `agent_info()`，通过显式委派的 pipe 回传 `is_agent`、role、filesystem domain 和 capability mask；Guest ledger 与 Host 检查器都会拒绝 launcher/回传身份不一致，Host mutation test 另会改写两类字段确认 fail closed。发布 bundle 必须保存本次双目标 Guest 记录，并动态产生 `identity_source=child_after_exec`、`agentos_agent_launches` 和 `agentos_worker_launches`；静态编译、Host 自测或旧轮次不能代替这些记录。

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

`make ci-check` 使用 `ci/kernel-budgets.json` 的 canonical toolchain/profile。静态指标包括内核 LOC、stripped ELF/raw、text/data/BSS/total、`struct proc`、Context detail sidecar 与完整 21 页 Agent 状态的单实例/全局/ordinary/reserved/account 容量、线程调用图栈深、64 KiB boot stack 的链接跨度与启动调用图、32 MiB 虚拟栈容量和 8 MiB 受信/保留物理栈池。owner 模块、integration bridge、允许依赖和 SCC 边界都由版本化注册集合给出，不在文档复制固定数量。metadata transaction/file-state/catalog/query/scan/directory/objects/actions/prefetch/store（含 format/I/O）、IPC 及 contract headers 还共同受 `metadata_control_plane` 聚合 source/text/BSS 预算；source 只保留固定接口开销，loaded text 与 BSS 维持 no-growth，防止跨文件迁移。受控 integration graph 不包含只通过普通 uCore 符号形成的依赖，不能解释为完整 uCore 调用图。

同一 Host 门还执行 metadata canonical genesis raw-image 合同、catalog capacity、catalog rollback fence 模型/变异、metadata boot reprobe 和 Agent wait 原子交付接线检查。catalog capacity 模型要求 512 总量、SYSTEM 64、ordinary 448、最多 4 个 ACTIVE/CLOSING/RETIRING workflow 各固定 112，其中 live AUTOSCAN 新增长度最多 96 条并保留 16 条显式 metadata；RETIRING 的目录未回收前继续占准入槽。workflow inode 账户改用独立 STORAGE policy domain limit，每 scope 硬下限 320、当前镜像约 342，不再以 catalog 112 为上限。所有 `agent_meta_slot/flags/version` 更新统一经 `agent_file_state_set_index()` 校验、`iupdate()` 并在失败时恢复旧值；write/sync/truncate/delete 统一经 `agent_fs_apply_inode_event()`，create 只在 VFS 发布成功后进入目录协调。checker 还验证 catalog 满后 VFS create 与 scope 隔离不降级、持久 deferred sidecar 抑制重复扫描，以及释放槽位后的重建、写回和强制 reload。v7 load 以表示、总表、SYSTEM/ordinary、每 scope 112、lifecycle 和唯一键为稳定合同，完整接受同版本旧快照中的 97 至 112 条 AUTOSCAN；113 条仍是损坏。加载后的超额 scope 只能保持或减少 AUTOSCAN，新增或 explicit-to-AUTOSCAN 在 96 条及以上均拒绝，降到 95 后才重新准入；失败事务的 receipt restore 只受硬边界、唯一键和 exact post-state 约束。合同拒绝重新引入跨 scope 借用、全局 union/max、快照软策略迁移状态、catalog resource kind 或 metadata envelope 账本。scoped snapshot 的 `(lifecycle_id,generation)` 绑定以及 `NO_SPACE`、重试、`CONFLICT`、`INDETERMINATE` 的错误传播保持不变；具体 mutation 数由当前候选的 checker 输出记录。genesis 合同从真实 mkfs 镜像独立解析两份完整预分配 bank，复核 canonical hash、VFS label checksum、bitmap/qmap、SYSTEM owner、零尾部和跨 bank 无 alias，并拒绝双 `ABSENT`、双 `UNCOMMITTED`、双损坏及混合状态。rollback fence checker 删除 owner guard、post-state binding、容量/唯一键复核或清理路径时必须失败；wait checker 删除 reserve/cookie/abort/commit 顺序时必须失败。这些均为 E1，不能替代 reserve 后 copyout 失效或持久化 checkpoint 并发的 Guest 故障注入。`WAIT_ATOMIC_TEST_PROFILE` 的动态用例与 Context 同步故障 profile 在单独构建中运行，不属于普通 18-case 套件，也不计入其 timing file。

Agent suite duration 只累计各 QEMU case 的 monotonic 运行时间，不包含编译。固定 runner 上 `bounded-runner-final-01/02/03` 的历史 16/16、`31d4ddf53695` 和 `814021ab9dac` 的三轮 18/18 都只证明各自源码。当前受管源码指纹已在冻结提交 `04c1e6652324` 的 clean detached worktree 上完成三轮：`287.9945528s`、`283.0201263s`、`280.9651484s`，中位基线 `283.0201263s`、上限 `297.172s`，材料位于 `evidence/calibrations/04c1e6652324/`。配置为 `calibrated_full_suite`；任何受管输入变化都必须丢弃不匹配的 baseline、limit、samples 和 fingerprint 并重新校准。该包仅为未签名本地 E3 校准证据，不是 release bundle、GitLab CI 或 E4 attestation。

通用 QEMU runner 以字节流读取并在进程退出前后全量 drain，不依赖文本行边界；包括 panic 在内的预定义 failure 模式按大小写不敏感方式检查，marker 后的剩余输出也不会跳过。控制台仍可转发原始字节，落盘的 `.guest.log` 则把 CRLF、孤立 CR 统一成 LF；exact-line marker、SHA256、CSV 行号和 release manifest 一律绑定这份 canonical LF transcript。监控循环每轮最多读取一个 64 KiB 块，随后重新检查 case timeout 和 marker grace，持续 stdout 洪泛不能饿死 deadline。每 case 最多接受 16 MiB 总输出，未终止记录最多保留 64 KiB，诊断行最多保留 4 KiB；总量/记录越界 fail closed，诊断副本截断。case deadline 优先于完成判断，并在 scanner feed 和 runner notice 后重新核对，迟到 marker 不能通过。普通 case 必须自然 `rc=0`；stdout/stderr pipe 先到 EOF 时仍在原 deadline 内等待真实退出状态。checkpoint profile 只接受完整 marker 后 runner 发出的单次 `SIGTERM`。powercut profile 另由专用 supervisor 隔离被测树，以随机 nonce 和稳定 PID/starttime 认证控制请求；它只在向 QEMU leader 发送 `SIGKILL`、回收跨 `setsid()` 的全部后代并取得一致镜像退出码后提交完成证明。supervisor/leader 被 workload 提前杀死、控制通道 EOF、残留后代、超时、非零退出或后置 panic 均失败。该 profile 建模突然 VM 终止，`SIGKILL` 不清空宿主页缓存，不能等同于整机物理断电。预算 checker、通用 runner 和生产 profile validator 的 fail-closed 自测集合以当前源码为准，不固化易过时数量。

Reader seeded-action runner 不复用“对所有阶段扫描 panic 子串”的旧逻辑。clean/build/guest 各有独立 phase 和 timeout，前两阶段只依据进程退出码；QEMU guest 启动后才对去除 ANSI 的完整日志行匹配 Guest panic、trap、`check failed` 或 orchestrator failure，并在 summary 中记录 `failure_phase`。单测明确要求构建输出 `build/riscv64/ch6b_panic` 成功，也要求规范 Guest `[PANIC ...]` 行失败。科研平台 `exact-field-v1` receipt 以 128 B 分块流式读取完整文件，只接受唯一完整的目标 `key=value`；跨块长 key/目标和长无关字段合法，空 key/value、CR、NUL、重复目标及前后缀伪匹配 fail closed。完整 bytes/hash/line count 与字段断言一并进入 receipt，ASan/UBSan Host probe 接入 `ci-check`。历史与当前候选的 Reader 执行结果统一见 [test-record.md](test-record.md)。

Plain reference registry 按目标登记唯一 source owner，先剥除注释再严格校验真实调用及完整文件/记录 envelope；missing、unknown、duplicate、cross-owner prepublication 和 impersonation 都 fail closed。seeded program observation 还绑定 seeded profile、QEMU 日志和 `rp_orch_timing` 中 orchestrator/launcher/program 的顺序、数量、字节、哈希与名称摘要。AgentOS `rp_agentos_mainflow` 只给出 11 个唯一、完整、有序的未验证 telemetry stage；任何 Guest `runtime_verified` 记录都 fail closed。Host 从与提取清单一致的单层非链接目录读取 telemetry 和 11 个规范来源，逐 source 复验唯一 claim、预期成功状态、阶段字段及完整 bytes/hash。`host-platform-alignment.json` 保存逐来源明细，最终 bundle 保存对应原始文件并在离线验签时重算。

每侧 complete-state ZIP 只含 `extract-summary.json` 和其中精确列出的纯 Guest `rp_*` 普通文件，并显式拒绝 `rp_host_run_result`。Plain/AgentOS Host run receipt 分别作为 `dual-plain-host-run-result.state` 和 `dual-agentos-host-run-result.state` 独立保存，并以 `sha256-inventory-v1` 绑定 Guest 清单和内容；Reader 与比较器都重算该摘要，同数量内容替换不能继续使用旧 receipt。Host LLM relay 只发布独立差异 overlay，不改写已签收 Guest generation。离线验证安全解包，以 `min_common_files=240`、两份 receipt、seeded summary 与两份 Guest 日志重放 `compare_state()`，要求摘要逐字段一致，并核对 Mainflow、program ledger 和 backend 原始字节。普通 `dual-platform-run` 不打包 complete-state ZIP；只有最终采集启用的 `full-verify` evidence mode 才发布它们。当前候选的实际执行和发布边界只在 [test-record.md](test-record.md) 记录。

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

下表记录当前 18-case Agent 回归契约。脚本在进入普通套件前，先用同时启用 `AGENT_CONTEXT_SYNC_TEST_PROFILE` 与 `WAIT_ATOMIC_TEST_PROFILE` 的独立镜像运行一次 `agentfinal_ucore`；该 prelude 有独立 timing 临时文件，既不是 18 个 case 之一，也不得进入 18-case 校准总时长。它必须额外输出 `agentfinal_ucore: thread_wait_deadlines finite_infinite=1 distinct_deadlines=1 keyed_timer=1 loop_aggregate=1 slot_reuse=1`、`agentfinal_ucore: wait_publication_atomic=1 event_wake_none=1 event_no_sleep=1 sibling_wake_none=1 teardown_completed=1` 和 Context 同步失败原子性 marker。2026-07-25 的 generation-safe lifecycle、统一 resource controller/teardown、lazy physical stack 和 Context sidecar checkpoint 曾在当时的 16-case 固定 runner 连续完成三轮；后续增加了工具 ABI 和 mutex 阻塞语义专项。历史结果不能外推到 release bundle 所绑定的 C。裸 marker 仍只表示对应脚本断言，不能从程序已编译或标记字符串存在推断通过。

| 测试程序 | 覆盖重点 | 通过标记 |
| --- | --- | --- |
| `agentfinal_ucore` | Agent 创建、21 页状态原子计费、批量工具调用、Context commit lane、snapshot、v8 不可变 archive 与 active-path rollback、FIFO、用户态结构化查询 cache、timeline、provenance、Run Ledger；独立 profile 覆盖 Context 发布预检失败和 wait/teardown 发布原子性。 | 生产套件：`context_commit_lane=1 sequence=1..3 hash=1`、`context_rollback_branch=1 sequence_reuse=0 provenance_bound=1`、`context_active_path=1 archive_retained=1 direct_query=1 fifo_suffix=1`、`context_rollback_negative nonexistent=1 evicted=1`、`agentfinal_ucore: parent passed`；独立 profile：`context_sync_atomic=1 append=1 rollback=1 clear=1 recovery=1`、`thread_wait_deadlines finite_infinite=1 distinct_deadlines=1 keyed_timer=1 loop_aggregate=1 slot_reuse=1`、`wait_publication_atomic=1 event_wake_none=1 event_no_sleep=1 sibling_wake_none=1 teardown_completed=1` |
| `agentfs_ucore` | 真实 inode 绑定、metadata 双 bank、属性查询、每次真实执行的扫描/索引查询、内容摘要、有界去重预取、文件删除清理、字段驱动批量 action 状态维护、依赖 generation 稳定性、metadata 工作预算和交接端点生命周期。 | 重复查询不得出现内核 `CACHE_HIT`；`metadata_action_bounded=1 field_driven=1 batched=1 preemptions=5`、`prefetch_hints=1 bounded=1 count=2 preemptions=8`、`handoff_target_exit=1 endpoint_reuse=1 preemptions=6 ... clean=1`、`agentfs_ucore: parent passed` |
| `agentscan_ucore` | 根目录自动扫描、自动 metadata 写入、文件创建和删除后的 metadata 更新。 | `agentscan_ucore: parent passed` |
| `agentloop_ucore` | FIFO、stable source=4、directed=8、external=12、KERNEL origin 预留容量、heartbeat 单条 coalesce、消费后配额归还、external 饱和 watcher 广播隔离、watch/unwatch、timeout、动态 heartbeat set/stop/边界/旧 ABI、wait cancel、事件因果。 | `message_source_limit=4`、`ipc_class_limit=8`、`external_limit=12`、`system_event_reserved=4`、`heartbeat_reserve_coalesced=1`、`external_reject_reclaim=1`、`broadcast_slow_watcher_isolated=1`、`heartbeat_intrinsic=1 dynamic=1 coalesced=1 stop=1 bounds=1 legacy=1`、`parent passed` |
| `agentsched_ucore` | 角色权重、受权调度配置、事件优先、调度原因记录和资源域内 Agent/FIFO 公平性观测。 | 2026-07-25 历史 16-case 三轮均输出 `agentsched_ucore: parent passed`；本次发布结果只从 C 对应 bundle 的 canonical Guest 日志读取 |
| `agentconflict_ucore` | 文件编辑租约、非持有者写入拒绝、版本提交检查、普通进程拒绝。 | `agentconflict_ucore: parent passed` |
| `agentllm_ucore` | 显式 `MESSAGE` / `LLM_DONE` route 下的结构化请求、Relay 模板响应、LLM capability、完成事件、Context/timeline。 | `agentllm_ucore: parent passed` |
| `agentbench_ucore` | 批量工具、Context、provenance-bound 文件查询强制遍历/冷索引/热索引、预取、timeout/heartbeat，以及显式 route 下的 wait/wake 计时。 | `file_query_benchmark ... status=measured`、`agentbench_ucore: parent passed` |
| `labbench_ucore` | 综合场景中的性能入口，包装运行 `agentbench_ucore`。 | `labbench_ucore: parent passed` |
| `labdemo_ucore` | orchestrator 建立 sentinel -> investigator -> recovery 路由后的恢复场景、文件查询、预取交接、消息、权限、audit/timeline/provenance。 | `labdemo_ucore: parent passed` |
| `agentsecurity_ucore` | 既有权限/route/controller 负向检查；新增用户非零 cause/span 拒绝、可信跨 Agent source attribution、low/high audit authority 隔离。 | 2026-07-25 历史 16-case 三轮均输出 `trusted_span_authority=1`、`trusted_cause_attribution=1`、`audit_authority_partition=1` 和 `parent passed`；本次发布结果只从 C 对应 bundle 的 canonical Guest 日志读取 |
| `agenttoolabi_ucore` | syscall 547/548、V1 兼容、V2 sized typed KV、25 项 generated schema 全表、15 字符键容量边界、两版 LLM response、用户缓冲哨兵、可选参数与 heartbeat zero-stop 描述、参数重排，以及未知/重复/类型/size/version 错误拒绝。 | `schema_generated=1 validated=25`、`key_capacity=1 llm_response_v1_v2=1 buffer_sentinel=1`、`optional_schema=1 heartbeat_zero_stop=1`、`strict_negative_matrix=1`、`agenttoolabi_ucore: parent passed` |
| `agenttrust_ucore` | 可执行映像 W^X、密封映像不可变、bootstrap 授权范围、Agent 角色与可信映像绑定。 | `agenttrust_ucore: parent passed` |
| `agentvfs_ucore` | 工作流文件能力、公共/工作流命名空间隔离、跨 scope inode 描述符撤销、worker pipe 单跳委派和失败事务原子性。 | `cross_scope_fd_revoked=1`、`worker_pipe_delegation=1`、`parent passed` |
| `agentscope_ucore` | syscall 541/542/545、generation-safe lifecycle、跨域对象隔离、PUBLIC 降权后代谱系撤销、metadata/观测预算和 retirement。 | 历史专项约 `93.7s`，曾输出 `scope_close_authority=1`、`scope_controller_exit_revoke=1 public_lineage=1`、`scope_forced_cleanup=1`、`scope_replacement_admitted=1` 和 `parent passed`；本次发布结果只从 C 对应 bundle 读取 |
| `iobudget_ucore` | syscall 544 ABI v3 sized-copy、稳定 PUBLIC/workflow owner、NORMAL/CONTROL class、owner/shared/device lease 上界、线程退出 lease 回收、scheduler 内核态中断交付、fault teardown 清理归因/debt 结算、完成归因、PUBLIC cache/速率压力、workflow cache floor 与压力下写入进展。 | 2026-07-25 历史 16-case 三轮均通过八项具名机制 marker 与 `parent passed`；ABI sized-copy 是无单独 marker 的第九类断言；本次发布结果只从 C 对应 bundle 读取 |
| `usersafety_ucore` | syscall 指针、字符串、`exec` 参数、线程入口、等待队列、管道、文件和信号量输入范围。 | `usersafety_ucore: parent passed` |
| `blocking_semantics_ucore` | mutex owner、递归锁拒绝、非 owner 解锁拒绝、owner 退出交接和 FIFO waiter。 | `mutex_fifo_waiters=... dispatch_stable=1`、`blocking_semantics_ucore: parent passed` |

原始输出不在本文档重复展开，统一保存在 [test-record.md](test-record.md)。每个测试的流程和断言解释见 [testing-details.md](testing-details.md)。

`workflow_teardown_race_ucore` 不在上述 18-case Agent 套件中。独立入口 `make workflow-teardown-race-test` 默认连续运行三轮，并按顺序核对 syscall 546 ABI/self-only stale、factory close、根自然退出、PUBLIC lineage、Context/metadata waiter、阻塞 `fdget` 容量跨越、I/O debt/cache、inode/file/account 回收、同 id 更高 generation 重用和 `parent passed`。checkpoint `75d0dfd` 的 clean `full-verify` 已执行这三轮；目录拆分提交 `14a9450` 后又完成三轮定向复测。

scope 回归核对：PUBLIC=0、SYSTEM=1、动态 workflow>=3，数值 2 是安装级 PUBLIC 存储 principal。权威 lifecycle ledger 固定 8 槽，key 为 `(id,generation)`；冻结期 ACTIVE+CLOSING+RETIRING 合计最多 4 个，目录彻底退休后才释放准入槽，身份槽随后才能以更高 generation 复用。`vfs_scope_refs[NPROC]` 只是 VFS 引用/清理记录。进程、线程、file object、block/inode、cache 和 Agent 状态页统一映射到 generation-safe EXEC/STORAGE account；每个 Agent 的 9 sidecar + 6 mirror + 6 shadow 以一次 21 页 `RESOURCE_AGENT_STATE_PAGE` 请求原子计费。`resource_domain_id` 只做 CPU 调度分区。其余 metadata/audit 与存储容量契约仍按对应 policy 文件核对。

Workflow 根把不复用的 `agent_control_id` 绑定到当次 `(lifecycle id,generation)`。根离开或 factory 关闭时先进入 CLOSING，再按完整 key 撤销；Agent/VFS 凭据已清零的 PUBLIC child/grandchild 仍必须终止。exec prepare/commit/abort 在同一发布边界复核 lifecycle。成员进入统一 teardown，自行清理 FD、inode、sidecar、VM 和 resource/I/O 账目；最后成员释放引用后才进入 RETIRING。历史专项曾实际取得 `public_lineage=1` 和 `parent passed`；发布结论仍以 C 对应 bundle 的复跑记录为准。

块 I/O policy ABI v4 的设备根 burst/refill 为 560/280，PUBLIC NORMAL 为 32/16；每个 active workflow 的 NORMAL/CONTROL/BACKGROUND 为 24/12、48/24、8/4，每个 retiring workflow 只保留 BACKGROUND 8/4；SYSTEM SYSTEM/BACKGROUND 为 96/48、16/8，共享前台 slice 为 32/16。普通流量必须取得设备根信用并等待 device debt；SYSTEM owner、CONTROL 和 SYSTEM class 可在根信用耗尽时带 debt 前进，因此静态 560/280 envelope 是配置约束，不是保护流量的运行时硬总上限。shared fast path 在没有 admission waiter 时可直接借信用，排队 grant 才按 owner/class cursor 轮转。cache 的 SYSTEM/PUBLIC/active workflow floor/cap 为 40/96、24/48、36/64，`NBUF=256`；当前轮转退役清理 job 临时使用 3/8，cap 是稳态驻留边界而非瞬时硬上限。

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

物理页、metadata、观测证据与 VirtIO 的故障专项均由真实 Guest marker 判定，不以源码字符串扫描代替：

```bash
make physical-resource-test TOOLPREFIX=riscv64-linux-gnu-
make metadata-recovery-test TOOLPREFIX=riscv64-linux-gnu-
make observe-recovery-test TOOLPREFIX=riscv64-linux-gnu-
make virtio-disk-test TOOLPREFIX=riscv64-linux-gnu-
make fs-allocator-fault-test TOOLPREFIX=riscv64-linux-gnu-
```

`physical-resource` 在 tiny policy 下动态覆盖普通域隔离、系统保留、reserved promise 生命周期/公平、teardown 退款；仅 policy 非法组合与生产对象不得包含 test hook 两项使用编译期负向门。`metadata-recovery` 的每个新镜像先由 mkfs 安装并由 Host parser 验证 canonical 双 bank genesis，再对 primary/mirror 各八个 COW phase 动态确认 baseline、显式 arm 下一代事务并受控中止。powercut runner 只在完整 phase marker 后强制中止；case 随后还必须由 host 日志验证器确认 quiet baseline、armed/bound/fire/phase 的唯一有序链，以及一致的 scope、generation、token、job、bank 与 phase。normal kernel 启动前，host raw parser 要求至少一份完整有效 bank、无同代异 hash，并按 payload/header 发布边界只接受完整 baseline 或 updated。恢复后两 bank 必须相同且旧 scope 已清理。启动重探分别对全部 bank 和较新 bank 注入三轮 `BUSY/EIO/INTERRUPTED`，要求 fail-closed、动态 `retries=N`、连续 deferred attempt 与同启动恢复；超过 background burst 的 32-record bank 还对 `ABSENT/UNCOMMITTED/CORRUPT` peer 分别复测可恢复 cursor、terminal cache、selected confirm、catalog plan 和最终副本一致性，并在 seed Guest 内要求前台 live reload 单次完成。单次暂态 header-flush EIO 继续要求显式不确定结果和副本修复。`observe-recovery` 先用 host probe 直接编译生产 durable owner，动态覆盖 SYSTEM sink=0、commit 后重新通知失败再成功、lifecycle 槽之外的系统保留 dirty 位，以及 section serial 耗尽时保留最后 pending 状态并拒绝新分配；容量状态机 probe 直接包含生产 `agent_observe_capacity.c`，动态覆盖三个普通槽和一个 Recovery 保留槽、sticky 槽类别、同 workflow admission/abort、跨 scope 隔离、serial/target/source-generation token 绑定、DONE admission race、REAP response 丢失后的同 token 重发、STATUS delivery failure 保留、scope/generation cookie 与单次消费、授权 reload 推进，以及同身份恢复幂等与冲突身份 fail closed。对应 production validator mutation 必须能拒绝弱 token、恢复 token 缺 generation、类别升级、admission 清除 DONE、reload 停滞、冲突恢复、scope/generation cookie 绕过、copyout 前消费和已擦除 scope lookup 先于 token 重发。第一启动在 audit/span/event/control/agent 和一个空闲 lifecycle 槽完成分配后的首个 kernel marker 立即 `SIGKILL`，且不允许先发生后续 audit/checkpoint。下一启动从同一镜像分配 successor，Host 必须逐字段证明五组 ID 严格增大、同一 lifecycle 槽 generation 严格增大；之后再完成 checkpoint、reap 双副本确认和最终擦除三阶段。receipt 回归要求初始 `PENDING` 明确不是证据，伪 id 为 `STALE`，当前 lifecycle 的 exact entry 经 active durable section 重读后才为 `DURABLE`，在持久前挤出 checkpoint 窗口的 entry 即使 scope target 已复制也必须为 `FAILED`；普通进程、Recovery 与 teardown 后旧 lifecycle 都不能查询成功。最后一阶段使用只存在于测试 profile 的注入点耗尽 event ID，动态断言 IPC 返回 `NO_SPACE`、队列长度不变且生产对象不含该 hook；timeline profile 通过真实 publish owner 连续推进两次 epoch，独立 exact marker 必须证明第一次触发 final retry、第二次在到期边界被有界 timeout 截止；并发 profile 还建立不同 filter/deadline 的两个 waiter，要求一次 Context 记录只定向唤醒匹配线程、另一线程独立超时且退出后 sidecar 全清。`virtio-disk` 动态核对 lost IRQ、delayed progress、descriptor pressure、设备 status error、flush-disabled、timeout reset 和 stuck reset 的有序请求 identity/tick/result。

checkpoint runner 的单次 `SIGTERM` 只发生在完整 marker 后，用来建立受控重启边界；metadata 与 observation powercut runner 则使用同一个认证 supervisor 首次发送 `SIGKILL`。metadata case 在恢复前保存 raw-bank 解析结果；observation case 在 boot2 启动前先由独立 Host verifier 解析原始 uCore 镜像中的双 bank、durable arena 和 observation section，复算所有格式与哈希层，并把 scope、lifecycle generation、agent 和 receipt 身份精确绑定到 boot1 marker，恢复后再比较 cut/successor 身份 marker。它们证明进程级突然中止边界，不声称复现整机物理断电。profile v5 与 GitLab 分别保存每项 runner stdout 和逐次 canonical LF Guest 日志的合并产物，缺 Guest 日志即失败；allocator fault 还必须保存通过同一 verifier 复验的 `fs-allocator-evidence.tar`。

根目录 AgentOS 仍使用 16 KiB stack + 4 KiB guard/canary，但只预建虚拟槽，物理页在线程 admission 时映射，在 scheduler handoff 后释放。32 MiB 是全部虚拟槽容量，8 MiB 是受信/保留线程物理池。`baseline_ucore/` 当前仍是固定物理栈实现，二者共享栈深/guard 行为目标而非 lazy mapping 实现。构建期调用图检查可单独执行：

```bash
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
make -C baseline_ucore kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
```

`make full-verify` 当前先执行 `agent-test-policy`；provisional 在任何 profile/QEMU 步骤前 fail closed。校准有效后，profile v5 依次串联结构检查、`make ci-check`、Host/Reader、独立 Context-sync/WAIT_ATOMIC prelude、18-case Agent、双目标、proc、syscall、file、thread、physical、metadata recovery、observation recovery、VirtIO、workflow teardown race、ENOSPC 和 filesystem allocator fault。GitLab 保留原机制 job，并把 physical、metadata recovery、observation recovery、VirtIO、filesystem allocator fault 拆成五个独立、同一 `resource_group` 串行的 QEMU job；metadata 的 45 次启动使用独立 `60m` job，allocator fault 使用独立 `45m` job 并交付 canonical archive。是否通过必须以 C 对应 release bundle 的完整日志为准。

## 覆盖关系

| 赛题任务 | 对应测试 |
| --- | --- |
| 任务一：Agent 进程与地址空间 | `agentfinal_ucore`、`agentscope_ucore`、`agentsecurity_ucore`、`agenttrust_ucore`、`usersafety_ucore` |
| 任务二：结构化工具调用 | `agentfinal_ucore`、`agentbench_ucore`、`agentsecurity_ucore` |
| 任务三：Context Path | `agentfinal_ucore`、`agentscope_ucore`、`agentsecurity_ucore`、`agentscan_ucore`、`labdemo_ucore` |
| 任务四：文件属性查询 | `agentfs_ucore`、`agentscope_ucore`、`agentscan_ucore`、`agentbench_ucore`、`agentconflict_ucore`、`agentvfs_ucore` |
| 任务五：Agent Loop | `agentloop_ucore`、`agentscope_ucore`、`agentsched_ucore`、`threadresource_ucore`、`agentbench_ucore`、`labdemo_ucore` |
| 任务六：综合场景 | `labdemo_ucore`、`labbench_ucore`、`make dual-platform-run` |
| 安全与稳健性复测 | `agentscope_ucore`、`agentsecurity_ucore`、`agenttrust_ucore`、`agentvfs_ucore`、`usersafety_ucore`、`blocking_semantics_ucore`、`make physical-resource-test`、`make metadata-recovery-test`、`make observe-recovery-test`、`make virtio-disk-test`、其余资源专项与 `make kernel-stack-check` |

## 性能数据说明

功能 marker、运行诊断和性能原始数据必须分开解释。当前唯一进入 provenance-bound 原始实验链路的是 `agentbench_ucore` 的文件查询 benchmark：

| 指标 | 含义 |
| --- | --- |
| `traversal` | 强制遍历 metadata catalog 的实际 operations、触达记录和未经补值的 Guest 微秒差值。 |
| `cold_index` | 包含索引重建成本的一次冷索引查询及重建记录数。 |
| `warm_index` | 索引已就绪后的多次查询；每次仍真实遍历候选链，不使用内核查询结果缓存。 |
| provenance 字段 | 来源 Guest 日志 SHA256、marker SHA256、行号、命令、commit 和 run id。 |

`agentbench_ucore` 还会输出 scalar/batch、digest cache、prefetch 和 event wait/wake 等诊断 telemetry。这些值用于本轮调试和功能回归，但在获得同等级的来源绑定与重复测量前，不列为独立原始实验。

双目标运行后的本地预览包含：

| 文件 | 内容 |
| --- | --- |
| `summary.csv` | 双目标总体状态、状态文件数量和关键对照项。 |
| `runner-sweep.csv` | Runner tick 可用性记录。当前没有可信的独立 runtime producer，只允许 `unavailable/plain_runtime_cases_zero` 和零数据行；旧 measured collector 与两张推导图已删除。恢复测量必须发布新协议并绑定非 reference 源、逐字段 receipt、日志和 commit/run。 |
| `experiments/status.json` | 当前实测是 `measured` 还是 `unavailable`；缺少可信 manifest 时必须不可用。 |
| `experiments/raw/file-query-benchmark.csv` | 当前唯一的 provenance-bound Guest 原始实验数据。 |
| `experiments/experiment-stats.csv` | 只从上述实测行聚合 min、avg、max、P50、P95。 |
| `charts/experiment-file-query-bar.svg` | 只从上述 CSV 生成的可视化，不是独立证据。 |

QEMU tick 会受到宿主机调度、终端输出和文件系统缓存影响，因此文件查询报告同时保留 operations 和实际触达记录数，并明确区分冷索引重建与热索引查询。Context/timeline、事件等待、并发写入、LLM Relay 和恢复流程目前只有动态功能证据，不宣称拥有独立 raw CSV 或性能曲线。

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

`/tmp/agentos-dual-platform/` 保存 QEMU 日志、纯 Guest 状态目录、独立 Host run receipt 和页面渲染结果；普通运行不生成 complete-state ZIP。`results/latest/` 保存本地汇总、Markdown 摘要和 HTML 导览页，默认不提交，也不能替代最终证据。正式发布使用与 clean、已提交 HEAD 绑定的 `evidence/releases/<bundle>/`；其中 `logs/raw/dual-{plain,agentos}-complete-state.zip` 保存完整 Guest 状态，`logs/raw/dual-{plain,agentos}-host-run-result.state` 保存独立 Host receipt，`metrics/file-query-benchmark.{csv,json}` 必须能回溯到 `logs/raw/agent-suite-guest.log`。

## 失败定位

| 现象 | 优先查看 |
| --- | --- |
| QEMU 长时间无输出 | `/tmp/agentos-dual-platform/seeded-action-state/*/ucore-run.log` |
| 构建失败 | `make agentos-user` 或 `make agentos-build` 的编译输出 |
| 某个专项测试失败 | [testing-details.md](testing-details.md) 中对应测试流程 |
| 双目标状态不一致 | [../verification.md](../verification.md) 的双目标验证章节 |
| 页面或图表缺失 | `host_tools/test_*.py` 和 `results/latest/` |

## 发布判定与历史状态

发布判定必须区分冻结 checkpoint、代码提交 C、证据提交 E 和远端 attestation：

- `13824/16384`、`371.5s`、`126.1s`、曾经的 `sizeof(struct proc)=25640/25936` B 及相邻 H-17 模块体积都是历史快照，不是待发布 C 的最终指标；最终源码、镜像、运行段、`struct proc`、栈和 metadata 聚合数值由 C 上的 `make ci-check` 原始日志、包内 `metrics/measurements.csv` 与版本化 JSON 共同绑定。schema v6 固定收集 `metadata_control_plane` 的 source lines/source bytes/loaded text/BSS，并在离线验证时从严格日志块和配置重算；
- 完整 Agent 状态的机制口径仍是每 Agent 21 页/`86016` B 原子计费，legacy mail 两页 sidecar 按需另计；这类固定布局事实不能替代发布时的全局容量实测；
- `31d4ddf53695`、`814021ab9dac` 的三轮 18-case duration 和更早 16-case 结果都只作各自源码的历史记录；任何新候选都不得复用不匹配的 fingerprint、baseline、limit 或 samples，当前门禁结果见 [test-record.md](test-record.md)；
- 2026-07-26 的 `75d0dfde716453af90d7310c6a1521968fcf7167` 曾在 clean 环境完成一次旧 profile `make full-verify`，墙钟 `19:45.97`；`14a9450` 后也有具名定向复测。这些都是明确提交上的 checkpoint，不证明其他 HEAD；
- profile v5 已把 physical、metadata recovery、observation recovery、VirtIO 和 filesystem allocator fault runner 纳入本地聚合。是否实际通过由 [test-record.md](test-record.md) 的候选记录和最终 `INDEX.md` 指向的 C/E bundle 分级判定；
- 干净 C 的完整本地 bundle 经提交 E 绑定后可达到 E3。远程必选集合仍是同一 C 的 1 个 Host-class 和 8 个 QEMU-class job；没有可用 Runner 时 `remote_ci.status=not-attached`，仅 E4 不可用，不否定已校验的本地 E3。

详细命令、关键输出和覆盖边界见 [test-record.md](test-record.md)。

## 当前范围说明

| 方向 | 当前范围 |
| --- | --- |
| 文件扫描深度 | 自动扫描 uCore 根目录短文件名，文件对象 metadata 支持用户态显式写入和根目录自动发现。 |
| syscall 与 I/O 公平性覆盖 | 历史 CPU 终审轮曾动态覆盖控制台、inode 写和截断；`iobudget_ucore` 契约覆盖 scheduler 中断交付、fault teardown 归因和一个 PUBLIC/workflow CONTROL owner。独立 VirtIO fault matrix 契约覆盖丢中断、延迟完成、描述符压力、status error、flush-disabled 与 timeout/stuck reset；metadata recovery 契约覆盖 primary/mirror 各八个 COW phase 的 power-cut model 与 raw-bank 校验，并以 45 次 Guest 启动复测双目标 `BUSY/EIO/INTERRUPTED` 的 fail-closed/退避/同启动重探、单次暂态 header-flush EIO，以及超过 background burst 的 32-record bank 在 `ABSENT/UNCOMMITTED/CORRUPT` peer 下的 cursor、terminal cache、selected confirm 和 catalog plan。Host mutation、生产 hook 隔离和交叉编译属于 E1；动态是否完成只从发布 bundle 读取。仍未覆盖永久设备故障、Recovery、SYSTEM/workflow BACKGROUND、多 workflow 同压、retiring 3/8、跨 owner LRU/transient、主动 device-debt、确认双 bank 同时损坏后的在线修复或 grouped qmap claim 中点故障。突然 VM 终止模型不等同于整机物理断电。 |
| Agent 调度 | 验证 active resource domain 外层轮转、域内角色权重、受权调度配置、事件优先、deadline、heartbeat、wait cancel、虚拟运行量和 thread bomb 下的 victim 进展。 |
| Workflow 撤销 | `agentscope_ucore` 验证根自关、factory 关闭、阻塞低权限成员和 9 轮回收；独立 teardown race 再组合 factory/自然退出、PUBLIC lineage、Context/metadata waiter、阻塞 fdget、主动 I/O debt/cache、inode/file/account 和 generation 重用。仍未精确注入 close 与 spawn/pending exec 的发布瞬间或多线程 controller。 |
| LLM Gateway | 内核提供结构化请求、响应事件、Context 和审计记录；云端访问由用户态或宿主机 Relay 完成。 |
| 页面和图表 | 内核输出 `agentos:event`、timeline、audit 和 provenance，宿主机工具负责转成页面和图表。 |
| 性能数据 | 发布性能结论只接受 provenance-bound 的强制遍历/冷索引/热索引文件查询 benchmark；普通 tick、扫描数、候选数、轮询数、拒绝数和重建步骤只作当次诊断 telemetry。 |
