# 最终加固与证据状态矩阵

本文追踪 2026-07-27 终审提出的 17 项问题。它是当前工作树的验收清单，
不是最终通过报告，也不把旧提交的 QEMU 日志外推到当前 HEAD。实现位置说明机制已经进入
源码；只有与同一提交绑定的动态日志才能证明 Guest 行为，只有最终证据包才能证明发布状态。

## 证据等级

| 等级 | 含义 |
| --- | --- |
| E0 | 设计和实现位置可定位；未说明测试结果。 |
| E1 | 当前源码的 Host、静态合同、mutation 或编译检查通过；不能替代 QEMU。 |
| E2 | 同一已提交代码在 QEMU 中通过具名 marker、失败模式和退出状态验证。 |
| E3 | `evidence/releases/INDEX.md` 的有效记录指向一个满足 C→E 直接子提交合同的可校验发布包，且包内 manifest 将干净代码提交 C、成功的 `make full-verify`、原始日志、环境和指标绑定为同一来源。E3 是本地交付等级，不依赖远程 Runner。 |
| E4 | 在 E3 之上，同一 C 的 1 个 Host-class 与 8 个 QEMU-class 必选远端 job 均取得可离线复验的执行 attestation，并绑定到组合证据包。 |

本表随代码提交 C 冻结验收语义；C 本身不得预置声称由 C 生成的发布包。最终交付状态必须
从 append-only `evidence/releases/INDEX.md` 和包内 manifest 读取，不能仅凭本文的阶段性状态
文字判断。仓库历史中的专项 QEMU 结果和 `75d0dfd` clean checkpoint 只作回归线索，不能外推
到新的代码提交。没有可用 Runner 时，包内 `remote_ci.status` 必须为 `not-attached`；这只阻塞
E4，不影响满足上述本地交付合同的 E3，也不能写成远程 CI 通过。

metadata 的动态覆盖同样是 bundle-bound：只有 `INDEX.md` 选中的发布包同时通过 recovery step
语义复验、完整日志验证和恢复前 raw-bank 校验，才可据此授予 E2/E3。计划用例数、源码中的
runner 接线、本表文字或工作区外的临时日志都不能替代该发布证据。

证据包内具名 `*.guest.log` 是 UTF-8/LF canonical transcript，exact-line marker、SHA256 和派生
数据都绑定它。终端 stdout/raw console 只是可能含 CR 或 ANSI 控制序列的传输诊断流，不能
替代 canonical Guest 日志；`logs/raw/` 的 `raw` 也不表示逐字节串口捕获。

双目标 complete-state ZIP 是纯 Guest 归档：每侧只含 `extract-summary.json` 和其中精确列出的
普通非链接 `rp_*` 文件，禁止 `rp_host_run_result`。Plain/AgentOS Host run receipt 是两份独立
raw sidecar；离线验证必须安全解包、显式传入 receipt、seeded summary 与 Guest 日志，以
`min_common_files=240` 重放 `compare_state()`，并核对 Mainflow、program ledger 与 backend 原件。
普通 `dual-platform-run` 不生成这些 ZIP，只有最终采集启用的 evidence mode 才打包发布。

表内日志片段中的字面 `...` 只表示省略字段的格式示例，不是可被 validator 接受的实际输出；
发布证据中的 marker 必须保存并逐字匹配完整原行。

## 逐项矩阵

| ID | 级别与问题 | 机制性处理 | 验收入口与判据 | 当前状态与限制 |
| --- | --- | --- | --- | --- |
| H-01 | P0：六组“原始实验数据”由公式生成 | 删除旧公式数据及其 `experiment-*.svg`；只承认 `agentbench_ucore` 的文件查询实测。Guest 以 `sys_get_time` 记录原始微秒差，允许真实 0；遍历、含重建的冷索引、已就绪热索引都执行真实查询。提取器把日志、完整 marker、通过行、commit、run id、runner 自有命令、行号和 SHA256 绑定到每行。汇总器原地重跑前清除旧生成面并复制 manifest/Guest log；Reader 服务再次验签并执行 raw/chart 白名单。 | `host_tools/test_measured_experiments.py`、`host_tools/benchmark_source_contract.py` 的 control-flow/def-use 与 mutation 合同；`test_result_bundle_contract.py` 拒绝旧名、改名、路径逃逸、symlink、hash/逐行 CSV 篡改；最终必须出现 schema 2 的 `file_query_benchmark ... status=measured` 和 `agentbench_ucore: parent passed`。 | 机制/Host 合同为 E1，冻结提交 `31d4ddf53695` 的三轮 `agentbench_ucore` 提供 Guest E2；provenance-bound 发布 CSV 与 E3 仍待 release bundle。Context、事件、并发写入、LLM Relay、恢复等五类只保留功能测试，不再称“原始实验数据”或性能曲线。 |
| H-02 | P0：部分综合验收程序硬编码“通过证据” | Plain 参考产品只允许 target-specific `REFERENCE_SOURCES` 中登记的唯一 source owner；源码按 token 剥除注释后再检查真实调用，文件必须形成完整 `demo_reference/demo_expected/reference_ready` envelope，记录以 `(destination,anchor)` 唯一，缺失、未知、重复、跨 owner 预发布或冒名一律 fail closed。seeded program observation 还绑定 seeded profile、QEMU 日志以及 `rp_orch_timing` 中 orchestrator/launcher/program 的顺序、数量、字节数、哈希和名称摘要。AgentOS 删除 Guest Mainflow 自签 receipt 和可绕过的静态 producer 证明；`rp_agentos_mainflow` 只发布 11 个有序未验证 telemetry stage，Host 从安全状态清单独立复验 11 个规范来源的唯一 claim、成功状态、阶段字段和完整 bytes/hash，任何 Guest runtime 验证回执都 fail closed。Comparator 只接受与单层非链接状态目录精确相等的 summary inventory；Host run receipt 独立于 Guest 状态，并与 seeded summary 和 Guest action marker 交叉绑定。当前没有可信 runner tick producer，因此删除不可达 measured ABI 与两张推导图，只保留 `unavailable/plain_runtime_cases_zero`。Reader runner 明确区分 clean/build/guest。 | source/reference/mainflow mutation tests 拒绝注释伪调用、失败源提升、目录穿越、symlink、残缺 envelope、Guest 自签回执、Host receipt 混入 Guest 清单、缺失/重复/乱序阶段、单行拼接事实、绕过读取、顺序/哈希换绑和外部日志复用；函数指针与字符数组 producer 不能取得验证身份。`test-rp-evidence-file-field.py` 覆盖长字段、长 key、CR/NUL、重复与伪匹配；Reader runner 测试覆盖“构建路径含 `panic` 不失败”和“真实 Guest panic 必须失败”。result bundle 合同安全解包完整 Guest 状态、显式传入独立 Host receipt 重放比较，并拒绝旧 measured 字段、11 列 sweep 和旧 runner 图。 | 当前候选的双目标、Reader、预算和聚合门结果只在 [test-record.md](test-record.md) 集中记录；提交前工作树结果不等于 E3。`814021ab9dac` 的三轮 18-case 只属于其源码，具体 E3 仍须由 clean C→E bundle 授予。 |
| H-03 | P1：必做 `context_rollback` 没有动态验收 | rollback 不截断或覆写旧记录，而是分配新 branch generation、移动 visible head，并让后续记录以新单调 sequence 指向旧 hash。Context v8 以独立 `path_parent_sequence` 绑定本地分支拓扑，避免跨 Agent provenance cause 混入；query/snapshot 返回可信 active path，detail/provenance 保留 archive 历史。append/rollback/clear 统一先验证全部用户镜像范围，再执行 header-last commit。 | `agentfinal_ucore` 必须逐字输出 `context_rollback_branch=1 sequence_reuse=0 provenance_bound=1`、`context_active_path=1 archive_retained=1 direct_query=1 fifo_suffix=1`、不存在/已淘汰负例，以及测试 profile 的失败原子性 marker，最后自然输出 `parent passed`。 | 冻结提交 `31d4ddf53695` 的三轮 18-case 与独立 prelude 均通过严格 runner，达到本项 E2；最终 E3 仍待 clean release bundle。物理 archive 固定 128 条，FIFO 淘汰后 active view 明确收敛到 retained suffix。 |
| H-04 | P1：工具协议扩展性与错误处理不严格 | `agent_tool_protocol` 用单一 typed rule table 生成 V1/V2 描述、参数数量和 decoder；共享键注册表同时供规则表、编译期容量断言和 Host checker 使用，16 字节字段只允许最多 15 个可见 ASCII 字符。V2 请求保留题面要求的工具名称字符串与键值参数，并带 version/size 前缀。初始化时验证工具 id/name、key、类型、required、终止符和容量；未知工具、重复/未知参数、错误类型、缺失必需参数、未终止字符串和尺寸错误 fail closed。 | `agenttoolabi_ucore` 的 `tool_list_contract=1`、`schema_generated=1 validated=25`、`key_capacity=1 llm_response_v1_v2=1 buffer_sentinel=1`、`v1_compatible=1`、`v2_typed_reordered=1`、`strict_negative_matrix=1` 与 `parent passed`；UAPI layout checker 的正负例还证明 15 字符接受、16 字符在 Host 阶段拒绝且编译期断言不可旁路。 | 静态合同与冻结提交 `31d4ddf53695` 的三轮 `agenttoolabi_ucore` 达到 E2；最终 E3 待 release bundle。工具表目前仍是内核编译期注册，不宣称已经实现题面“创新方向”中的用户态动态注册。 |
| H-05 | P1：查询性能对照混入缓存效果 | benchmark 明确拆分强制全表遍历、一次含索引重建的冷查询和 64 次索引已就绪查询。热路径仍遍历真实候选链，要求内核 query result cache 未命中；用户态 Context cache 另行测试，不混入内核查询计时。 | schema 2 marker 必须包含 `traversal_ops=64`、`cold_index_ops=1`、`cold_rebuild_included=1`、`warm_index_ops=64` 和三组原始微秒值；Guest 同时断言冷/热 `CACHE_HIT=0`，source contract 拒绝短循环、常量时钟、floor、字段换绑和提前返回。 | 方法合同为 E1，冻结提交 `31d4ddf53695` 的三轮 `agentbench_ucore` 达到 Guest E2；provenance-bound CSV 与 E3 仍待 release bundle。该实验比较三条查询执行路径，不外推端到端业务吞吐。 |
| H-06 | P1：最终证据没有随仓库交付 | 采用“代码提交 C + 证据提交 E”模型：在 clean detached C 中执行唯一 `make full-verify`，以 manifest、原始日志、环境、指标和逐文件校验和绑定 C；E 只能新增一个 release 目录并精确追加 INDEX。 | 正式 Python 工具只能经 `python3 -I -S scripts/trusted-python-entry.py <allowlisted-tool> ...` 启动，隔离工作目录、`PYTHONPATH`、用户与全局 site startup。以此执行 `scripts/capture-final-evidence.py collect/verify` 与 `host_tools/evidence_delivery_contract.py verify-committed`；mutation 合同拒绝来源换绑、伪 raw、错误 Git 父子关系、额外文件及非普通文件。 | Host/mutation 合同达到 E1；E3/E4 只由所选 bundle 的 manifest 和离线复验结果决定，九个远端执行证明全部有效时才达到 E4。 |
| H-07 | P2：按题面逐字验收的三处兼容风险 | 一，科研平台的 Agent 角色由共享 manifest 驱动，实际调用 `agent_create_role`，普通 worker 用 `agent_worker_create`，exec 后子进程回传 `agent_info` 身份证明；二，工具 V2 保留“工具名称字符串 + 键值参数列表 + 状态/结构化结果”，同时兼容 V1；三，必做 push/query/rollback/clear 均有用户态调用和动态 marker，rollback 还覆盖失败原子性。 | 双目标对照器核对 launcher、post-exec `is_agent/role/domain/caps`；`agenttoolabi_ucore` 验证 name/KV/schema/error；挑战绑定的 `agenteval_ucore` receipt 验证四个 Context syscall、六轮路径、直接镜像读取、FIFO 和 rollback。 | 源码/Host 合同及冻结提交 `31d4ddf53695` 的 `agenttoolabi`/`agentfinal` 三轮只构成历史部分 E2；当前 `agenteval_ucore` 与双目标角色对照仍须由同一冻结提交的 full-verify/bundle 授予动态等级。ABI 的额外安全字段是扩展，不替代题面字段；`baseline_ucore/` 只是对照目标，不冒充 AgentOS。 |
| H-08 | P1：旧 `mailwrite`/`mailread` 绕过 Agent IPC 安全模型 | legacy mailbox 归 `agent_ipc` owner 管理并按需分配两页；PUBLIC 通信要求 generation-safe 的同一 EXEC account。处于 workflow 时还要求相同 active lifecycle key、scope 和非零 OPEN controller lineage；Agent、跨账户、跨 workflow、过期 PID/endpoint 一律拒绝。读取采用 begin/reserve、copyout、commit/abort，失败 copyout 不弹队列；exec 轮换 endpoint，teardown 退款。 | `agentsecurity_ucore` 的 lazy/queue-full/read-atomic/endpoint-reuse/exec-rotate/same-account/active-workflow/same-lineage/cross-scope/missing-controller marker；`physicalresource_ucore` 要求首次分配 `+2` 页且退出归零。 | 冻结提交 `31d4ddf53695` 的三轮 `agentsecurity_ucore` 使 IPC 合同达到 E2；独立 `physicalresource_ucore` 的页退款与最终 E3 仍待 full-verify/bundle。保留同一普通账户的 legacy 兼容正例，不把旧 syscall 改成 Agent 专用通道。 |
| H-09 | P1：崩溃恢复可能让全局 metadata fail-closed | mkfs 通过共享纯磁盘 ABI 预装两个字节一致、完整预分配且属于 `KERNEL_PRIVATE/SYSTEM` 的 v7 generation-1 空 bank；运行时不再从无 authority 状态安装空表。metadata bank 读取保留 `ABSENT/UNCOMMITTED/CORRUPT/INTERRUPTED/BUSY/IO/PROGRESS`，任一有效 v5/v7 bank 仍可迁移并修复另一副本，稳定状态没有有效 bank 则 fail closed。后台 cursor 绑定 authority、bank、inode identity/size 和 header，缓存 terminal 分类并最终重读确认所选 bank；大 bank 在 SYSTEM 预算下续跑，真实失败才增加退避。原始候选只读，catalog 在非 active shadow 生成绑定 epoch/generation/hash 的纯内存 plan；full boot 只接受 SYSTEM 64、ordinary 448、每 scope 112 等固定边界，live scoped reload 绑定目标 scope 的 `(lifecycle_id,generation)`，并在 prepare/apply 重验同一身份。ACTIVE/CLOSING/RETIRING 共用 4 个准入槽，RETIRING 在目录回收完成前保持原分区；不建立全局 union/max、metadata envelope 或第二套资源账本。plan 完成并取得 mutation fence 后才执行不再分配的 apply 和 authority publication，foreign fence 在改写前返回 `INTERRUPTED`。零身份普通记录 quarantine，待扫描记录 pending，加载门内不做目录乘积查找。前台 reload 则在单次 syscall 内等待 I/O debt 并完成有界 scope plan，不保留跨 syscall 候选。确认 identity/header/generation/hash 不一致仍永久关闭。 | `make metadata-recovery-test` 在每个 Guest 前验证 mkfs raw genesis，并对全部 bank 和较新 bank 分别注入三轮 `BUSY/EIO/INTERRUPTED`，严格验证 fail-closed、动态失败计数、递增有界 deadline、恢复后创建/查询和 authority 不回滚。Host mutation 明确拒绝双 `ABSENT`、双 `UNCOMMITTED`、双损坏和缺失/未提交混合状态，并覆盖标签校验和、尾部、映射、bitmap/qmap owner；另用超过 16 块 background burst 的 32-record bank 动态验证前台 reload 单次完成，以及单 peer `ABSENT/UNCOMMITTED/CORRUPT` 下的副本修复。保留 header flush EIO、COW phase 矩阵和 durable-dirty-retry probe。production 对象不得依赖 profile owner。 | Host mutation、生产/profile 交叉编译和接线为 E1；动态等级只能由所选 release bundle 的 recovery step、完整日志和恢复前 raw-bank 复验授予。genesis 的信任根是受控 mkfs 构建和普通进程不可达的 raw `KERNEL_PRIVATE` 路径；checksum/hash 只检测完整性与绑定身份，不是 MAC、数字签名或供应链认证。确认双 bank 同时损坏或永久介质故障时仍故意 fail closed，不宣称在线可用或物理掉电原子性。 |
| H-10 | P1：持久化协议缺少真正的掉电一致性 | metadata 使用双 bank COW：先使目标 header 无效，分块写入并回读 payload，最后发布并回读 header；新 primary 完整验证后才切 active generation，再更新 mirror。故障测试绑定明确事务和 COW phase，不按全局提交次数猜测目标；恢复前先解析原始 bank，结果只能是完整旧代或完整新代。 | `make metadata-recovery-test` 对 primary/mirror 各 8 个 phase 执行 powercut/recovery 配对，并验证 generation、checksum、旧值/新值和副本修复；日志验证器要求 arm/bind/fire 身份唯一且有序。文件系统 block/inode 分配与释放事务另由 `make fs-allocator-fault-test` 的 busy、EIO、crash/reboot 矩阵验收，不能拿 metadata bank 测试代替。 | 实现与 Host 合同为 E1；powercut 动态等级只读取所选 release bundle 的 recovery step、完整日志和恢复前 raw-bank 复验结果。这里的 powercut 是受认证 supervisor 用 `SIGKILL` 突然终止 QEMU；它不清空宿主页缓存，不等同整机物理断电，因此文档不得使用“证明真实物理掉电安全”。 |
| H-11 | P1：rollback 能改写 provenance 指向的历史 | Context record 的 sequence、branch generation、record hash 和旧内容保持不可变；rollback 只建立新 branch 与 visible head。新记录使用新 sequence，并保存 source/target branch generation、source record hash、cause sequence 和 `prev_hash`，provenance edge 因此指向稳定历史身份。 | `agentfinal_ucore` 在 rollback 前后比较完整历史，要求 latest sequence 不倒退、不复用，并查到从旧 branch 记录到新 branch 记录的精确 provenance edge；负例拒绝不存在和已淘汰 sequence。 | 冻结提交 `31d4ddf53695` 的三轮 `agentfinal_ucore` 与 prelude 达到 E2；最终 E3 待 release bundle。有限 FIFO 淘汰会让旧 payload 不再可查询；持久审计保留窗口和擦除语义不能被描述为无限历史存档。 |
| H-12 | P1：VirtIO I/O 可冻结单核系统 | completion 按 used index 与 outstanding 双预算有界扫描；非法/重复 used entry 触发 generation reset，已完成请求发布结果，只有真实可用 descriptor 才唤醒等待者。timeout、丢中断、延迟完成和设备错误走统一 reset/retry/error 边界；scheduler idle 路径提供 timer/device 中断交付窗口。 | `make virtio-disk-test` 的 full-ring read/read/flush、forged index、duplicate used、lost interrupt、delayed completion、descriptor pressure、status error、flush disabled、timeout/stuck reset；runner 要求具名 marker、无 panic 且自然退出。 | 静态合同/构建为 E1，当前 HEAD QEMU 故障矩阵待 E2。测试是单核 QEMU 设备模型，不能外推真实硬件、SMP 或任意 VirtIO 实现。 |
| H-13 | P1：mutex 没有 owner | mutex owner 绑定 thread slot + generation；递归锁和非 owner unlock 被拒绝；FIFO baton handoff 避免抢锁，owner 退出释放其全部锁并向合法 waiter 交接。exec 原子清理该进程的 mutex/semaphore/cond namespace；cond/semaphore 的 predicate-to-enqueue 在关中断窗口内完成。 | `blocking_semantics_ucore` 的 owner/nonowner/recursive、slot reuse、owner exit、多锁、baton revoke、FIFO 64 waiter、exec reset、cond/semaphore cancellation refund 和 512 次原子发布 marker；静态 wait-queue 合同检查所有生产 predicate wait。 | 静态合同与冻结提交 `31d4ddf53695` 的三轮 `blocking_semantics_ucore` 达到 E2；最终 E3 待 release bundle。实现面向当前单核、进程内同步 ABI，不声称 priority inheritance 或 SMP memory-model 证明。 |
| H-14 | P1：观测证据不持久、身份不稳定，且 `replicated(scope,target)` 可能被误当成某条记录已持久 | metadata 双 bank durable section 保存五组 allocator、lifecycle lease 和 checkpoint v7。每 scope 固定保留 latest tail 4 + causal diversity anchor 4；entry 显式保存 `identity_class`/`link_flags`/`principal`/`span_owner`/`receipt_id` sidecar，`admission_drops` 把取号前拒绝与成功入链后的 retention omission 分开。low principal 保证 8 条、空闲时可突发到 16 条，满载时只回收已离开主体或高于 8 条的借用溢出；causal victim scratch 覆盖完整 burst 16，不遗漏第 9 到 16 条的冗余。加载先全量验证 v7 image，再在关中断窗口预检、原子发布并在失败时回滚，不能覆盖已有 live 证据。`DURABLE` receipt 仍须精确重读 `(lifecycle,sequence,record_hash,receipt_id)` 并确认 active generation 未滚动；durable store 另以 replication fence 证明 active generation 已完成双 bank 复制，覆写/repair/fail-closed 时撤销，boot 仅在双 bank 一致时恢复，mirror `COMMIT` 后发布。`target == 0` 的淘汰 receipt 在精确扫描前后都检查该 fence，primary-only 不得误报。REAP 控制写通过通用 durable `URGENT`/`expedite` 提前，普通 receipt 保持 flags=0 的 serial fence 与合并策略。 | disk-layout probe/JSON、Host parser 与 mutation 合同验证 v7 固定布局、reserved 零值、tail/anchor 选择、显式 gap/link、sidecar 身份组合、drop-only scope、全局 sequence/receipt 唯一性、lease 高水位、恢复失败原子性、low 保证/突发/完整 scratch、active generation replication fence 及 durable expedite；既有状态机 probe 继续覆盖四槽与 Recovery 保留类、tokenless intent、重发和一次消费。`make observe-recovery-test` 的动态合同覆盖五类稳定 ID、lifecycle generation、exact receipt、权限/teardown、checkpoint eviction、sealed read、双副本擦除和 timeline wait。 | 当前 v7 的 layout、Host 与 mutation 结果只授予 E1；冻结前 v51 三启动本地结果仅是旧格式动态回归线索，不能外推为 v7 E2。`814021ab9dac` 与 `04c1e6652324` 的校准均只属于各自历史源码；当前候选保持 provisional，v7 多启动 QEMU、clean evidence bundle、完整 `full-verify` 与远端 Runner 仍须重新验证。receipt/checkpoint 是可授权清除的有界证据，不承诺永久保留或外部不可抵赖。 |
| H-15 | P2：生命周期控制仍有断点 | workflow 使用不可变 `(lifecycle_id,generation)` 和 ACTIVE/CLOSING/RETIRING 状态。降权、fork、exec 保留终止谱系；根离开或可信 factory close 会撤销授权、拒绝新成员并统一进入 teardown。Agent wait、timeline wait 和最后 sibling teardown 的“重检 + 发布等待”在同一关中断窗口，关闭丢失唤醒窗口；不可逆销毁前分别复核 live、VFS 和 sync exec guard。 | `workflow_teardown_race_ucore` 连续三轮组合 factory/natural close、降权后代、阻塞 syscall、Context/metadata gate、临时 file 引用、I/O debt/cache、资源退款和 generation 重用；`agentfinal_ucore` 测 event/timeline/sibling 原子发布；teardown mutation checker 删除或移动任一 guard 都应失败。 | 静态合同、冻结提交 `31d4ddf53695` 的三轮 wait prelude 与 `agentscope`/`agentfinal` 达到部分 E2；独立 `workflow_teardown_race_ucore` 仍待 full-verify 动态复验。单核关中断证明不等于 SMP 形式化验证；未来新增阻塞点必须接入同一 wait/teardown 合同。 |
| H-16 | P2：资源控制器未覆盖通用物理内存 | `RESOURCE_PHYSICAL_PAGE` 成为 pool-affine 资源种类；普通/保留页来自分离池并由 EXEC/STORAGE account admission、commit、cancel、refund。用户页表、brk/fork/exec、Agent Context/state、pipe、legacy mail、线程栈和文件系统临时页使用 account-aware allocator；混合 bundle 原子拒绝，物理页禁止 count-only import/transfer/reconcile。 | `make physical-resource-test` 验证 brk 增减、fork/exec、失败 rollback、普通域上限、SYSTEM/保留进展、pipe/mail 临时页、promise lifecycle 和退出归零；`test-resource-kind-policy.py` 与 `test-physical-brk-wiring.py` 拒绝绕过。 | 已知生产路径和静态合同为 E1，当前 HEAD Guest 待 E2。覆盖范围是通过内核页分配器管理的 RAM 页，不包括 MMIO、设备固有内存或宿主 QEMU 内存；未来新增分配路径必须登记 owner。 |
| H-17 | P2：系统偏重，模块拆分只完成一半 | `agent.c` 收敛为无可写状态 facade；身份、Context、IPC、生命周期、tool protocol、观测和 metadata 分 owner。版本化 registry 精确登记 owner、依赖、SCC、逐模块 LOC/BSS，并额外限制全内核源码、ELF/raw image、runtime text/data/BSS、`struct proc`、线程/boot 栈和 metadata control-plane 聚合 source/text/BSS，防止靠搬文件或提高单项上限隐藏增长。 | `make ci-check` 同时执行 budget selftest、模块边界、生产对象构建和栈调用图检查。历史与当前 source、image、runtime、PCB、栈及 metadata 聚合测量统一记录在 [test-record.md](test-record.md)，避免矩阵复制易漂移数值。 | `814021ab9dac` 曾完成其 Host `ci-check` 和三轮 18-case 校准；新候选不能复用其时长策略。最终源码、体积、PCB、栈和 metadata 指标只能从同一 C 的 canonical budget log 与所选 C→E bundle 读取。 |

## 补充残余项

并行审计在原 17 项终审之外发现一个 P1：Agent metadata catalog 原先既有固定数组上限，又被错误用作 workflow inode 的 backing lease，完整科研工作集会在第 113 个 scope inode 处提前失败。2026-07-29 的机制修复保留 512 槽、SYSTEM 64、4 个 workflow 各 112 的 catalog 隔离，但将每 scope AUTOSCAN 物化上限设为 96，并为显式 metadata 保留 16；workflow inode 账户改回独立 STORAGE policy domain limit，每 scope 硬下限 320、当前镜像约 342。所有 `slot/flags/version` 变化统一经 `agent_file_state_set_index()` 校验、`iupdate()` 并在失败时恢复旧值；write/sync/truncate/delete 统一经 `agent_fs_apply_inode_event()` 发布，create 则只在 VFS 创建成功后进入目录协调，metadata 容量不足不会回滚成功的 VFS create。catalog 满时普通文件保持 scope 标签和逐操作鉴权，inode 持久记录 capacity-deferred sidecar，后续写入不反复触发全目录扫描；可信快照发布后无论 catalog 是否为空都安排一次可合并的有界 reconcile，slot 实际释放时再触发 scoped urgent full restart。固定 catalog 仍不跨 scope 借用，也不增加全局 union/max、catalog resource kind、backing lease 或 metadata envelope 账本。当前候选的 AgentScope 与双目标动态结果见 [test-record.md](test-record.md)；clean `full-verify` 和 release bundle 仍按 E3 合同独立判定。

另一个 E1 残余是 Agent wait 的交付原子性。`sys_agent_wait` 已使用精确 event/cancel cookie 执行 reserve、用户 copyout/Context attribution、commit；失败会 abort reservation 并保持事件与配额可见，单槽只允许一个消费者。静态 wiring 和 wait-queue 合同已覆盖该机制，但最终动态套件尚未组合注入 reserve 后用户页失效、sibling waiter、cancel 与 teardown，因此不能把“无丢唤醒”外推成该组合已经取得 E2。

本轮独立审查还保留三个 P2，不以新增状态机在冻结前掩盖：active scan 接收 catalog full reset 时不会立即清除本轮 saturated-scope token，可能到下一次 full restart 才重新尝试 deferred inode；显式 metadata delete、rollback 或 reload 释放 slot 时尚未全部升级为 scoped urgent 通知，最坏多等待一个普通 rest window；可逆持久化失败恢复原本的 deferred sidecar 时，`-1` 可能暂时回到 `0`，后续协调扫描会自愈。它们不会导致永久资源泄漏或全局 fail-closed，但需要在减法阶段先补组合测试，再决定合并通知机制或传播 indeterminate 状态。

时长门的源码绑定缺口已经收敛：calibrated 配置必须保存
`source_fingerprint_sha256`，checker 用带长度分帧的路径与文件字节绑定 expected cases、固定
toolchain、runner profile/tag、构建规则、内核、用户程序、NFS 镜像输入和 runner 直接依赖；
相关字节改变会在 QEMU 前 fail closed。provisional 不得夹带旧 fingerprint、基线、上限或样本。
普通 Linux、WSL 和普通 Runner 必须显式使用 `AGENT_TEST_DURATION_PROFILE=none`：完整 18-case、
语义、Guest 日志与 timing 行清单仍需通过，但本地 duration baseline/limit/ratio 记为不适用。
`local-e3` 只属于与记录身份完全一致的受信原生 MSYS2 E3。
冻结提交 `04c1e6652324` 的三轮完整样本和 71 文件包仍只作为该历史源码的离线证据。当前
门禁已由 `a9e7c67feda5` 的新三轮完整样本恢复为 `calibrated_full_suite`，且绑定自身源码指纹；
当前策略与实际门禁结果见 `ci/kernel-budgets.json` 和
[test-record.md](test-record.md)。该包只构成未签名本地 E3 校准证据，仍不能宣称 release E3、远端
attestation 或 E4；发布等级由最终 C→E evidence registry 决定。

## 冻结后的减法审计

候选 C0 提交后冻结新内核功能和 ABI；只允许修复验收阻塞、写入校准数据以及形成最终 C→E
证据。随后按“保留、合并、删除”逐项审计，并把现有能力收敛为身份授权、生命周期与统一
teardown、通用资源控制、可信 IPC、持久化与有界 I/O、观测审计等 6 至 8 个核心机制。

- 保留：已经拥有唯一 owner、明确不变量、动态用例和资源预算的核心机制。
- 合并：重复的生命周期状态、资源 principal、Guest 语义和证据注册表；校准证据必须复用现有
  evidence registry，而不是建立第二套 delivery 协议。
- 删除：重复测试 ABI、只证明字符串存在的测试、过期 reference/公式图和无消费者的 sidecar；
  首批候选是多份 `fs_allocator_test_abi.h` 与双目标重复的 `rp_program_manifest.h`。
- 每个减法提交都必须行为保持，并同时检查 source、ELF/raw、text/BSS、`struct proc`、线程/boot
  栈和测试耗时不增长；巨型 metadata/observation 模块继续按 owner 边界小步拆分，不再横跨多个
  子系统重写。

## 与赛题逐字对齐

赛题允许并要求在 uCore 等教学内核中以“内核子系统或模块”形式扩展，而不是禁止修改
uCore。根目录是 AgentOS-uCore 交付目标；`baseline_ucore/` 只是运行同一用户态负载的对照组。
安全、资源、同步、文件系统和 VirtIO 修复只有在确属共享基础机制时才同步到对照目标，不能把
对照目标的用户态模拟写成 AgentOS 内核实现。

| 赛题验收文字 | 本轮对应关系 | 发布前仍需证明 |
| --- | --- | --- |
| 任务一：`sys_agent_create`/`sys_agent_info`、PCB 字段、Agent Context、普通/Agent 共存 | syscall 入口实际存在；角色程序由内核 Agent 创建接口启动，exec 后回传身份；Context 是独立用户映射，可信数据由内核 shadow 管理。 | 当前提交的 `agentfinal`、角色启动 ledger、普通/Agent 共存 QEMU 日志。 |
| 任务二：工具名称字符串、键值参数、结构化响应、可扩展、明确错误、至少 3 个工具 | V2 保留 name/KV 并由 25 项 typed schema 生成描述和 decoder；V1 兼容；负向矩阵覆盖错误语义。 | `agenttoolabi` 与实际 3 个以上工具成功调用的完整 Guest 日志。 |
| 任务三：push/query/rollback/clear、5 轮以上、Context 直接读、超配额淘汰 | 四个 syscall、branch rollback、128 条 FIFO、用户直接 mirror 读取和失败原子性均有动态程序。 | `agentfinal` 的全部逐字 marker 和自然退出。 |
| 任务四：属性/内容方向至少 2 项、结构化结果、查询快于遍历并给出数据 | inode metadata、属性索引、digest、结构化 query plan 已实现；唯一可宣称的性能数据是强制遍历/冷索引/热索引 Guest 实测。 | provenance-bound CSV 与原始日志进入 E3；不得用缓存、公式或示意图替代。 |
| 任务五：心跳、事件休眠、多 Agent 稳定运行（方向至少 2 项） | heartbeat set/stop、watch/wait/unwatch、消息/文件事件、域级公平和 wait 原子发布均有程序与 runner。 | 当前提交的 `agentloop`、`agentsched`、`blocking_semantics` 及相关安全 marker。 |
| 任务六：整合至少 3 个模块、QEMU 演示、至少 1 组性能对比 | `labdemo_ucore` 串联任务一至五；文件查询是唯一性能对比。 | clean `full-verify`、综合场景原始日志和文件查询实测随仓库交付。 |

额外安全机制不能替代基础任务的字面接口，也不能用“更安全”解释缺少必做动态验收。
反过来，赛题没有要求为了兼容而保留可绕过 Agent 安全域的 legacy 行为；旧 syscall 可以保留，
但必须纳入同一身份、资源和 teardown 机制。

## 最终证据两提交模型

1. 冻结代码和验收语义，清理工作区，提交代码 C。C 必须包含 runner、测试、预算和文档，
   但不包含声称来自 C 的尚未生成发布包。
2. 从干净 C、且已经由正式 platform dispatcher 建立的受信原生 MSYS2 E3 child domain 运行：
   ```bash
   python3 -I -S scripts/trusted-python-entry.py scripts/capture-final-evidence.py collect \
     --agent-test-duration-profile local-e3 \
     --output "evidence/releases/$(date -u +%Y%m%d)-$(git rev-parse --short=12 HEAD)"
   ```
   常规入口优先使用 `AGENT_TEST_DURATION_PROFILE=local-e3 make evaluation-full-verify` 建立并
   复核该执行域。若校准状态仍为 `provisional_requires_full_suite`，两条入口都必须在 QEMU 前
   fail closed；采集器在 detached C 中执行 `make full-verify`，失败时不得留下 ready release。
3. 用 `python3 -I -S scripts/trusted-python-entry.py scripts/capture-final-evidence.py verify evidence/releases/<bundle> --contract-root <clean-checkout-of-C>` 校验引用、SHA256 和证据 commit C 对应的干净可信源码根。人工核对所有 E2 marker、失败
   模式、环境版本、预算实际值及唯一文件查询原始数据。
4. 提交证据 E。E 必须以 C 为唯一父提交；`git diff C..E` 只允许新增单一
   `evidence/releases/<bundle>/` 的普通文件，并精确追加 `evidence/releases/INDEX.md` 一行。
   E 中 manifest 的 `source_commit` 仍为 C。其他文档、内核、用户程序或 runner 的任何改动
   都必须形成新的 C 并重新采集。
5. 推送 C/E。远端无 Runner 时保留 `not-attached`；以后只能从 GitLab API 抓取同一 C 的
   1 个 Host-class 与 8 个 QEMU-class job 的 project/pipeline/job 身份、trace 和 artifact，
   经 per-job attestation、安全 ZIP 与离线语义复验后写入新的组合包，不能回填本地包。

## 发布判定清单

- C 必须冻结机制、验收语义、runner、预算和文档；任何后续代码改动都会产生新的 C，并使旧包
  不能证明新提交。
- 18-case 时长预算必须由固定 runner 上至少三轮同版本完整 suite 校准；旧 case 集合的样本不能沿用。
- Reader、Agent suite、资源、metadata/observation 多启动、VirtIO、workflow teardown、ENOSPC
  和双目标路径必须由同一 C 的 clean `full-verify` 执行，不能拼接历史运行。
- E3 只由 `evidence/releases/INDEX.md` 中有效记录、对应 manifest 和完整离线复验结果确定；代码
  文档不预先声明某个提交已有或没有 E3。
- E4 还必须绑定同一 C 的 1 个 Host-class 与 8 个 QEMU-class 远端 attestation。没有可用 Runner
  时保持 `remote_ci.status=not-attached`，只表示 E4 未满足，不会降级已经独立成立的 E3。

## 历史文档审计

以下是 2026-07-27 文档审计的历史记录，只支持当时 H-01、H-02、H-05、H-06 和 H-07 的
Host/E1 判断，不代表 `INDEX.md` 所选发布包的当前等级，也不提升任何项目到 E2：

```text
python host_tools/test_measured_experiments.py
python host_tools/test_backend_evidence_contract.py
python host_tools/test_plain_ucore_action_runner.py
python host_tools/test_compare_dual_platform_state.py
python host_tools/test_check_host_platform_alignment.py
make evidence-capture-selftest
python host_tools/test_remote_ci_evidence.py
```

该次文档检查确认矩阵恰有 17 行、所有入口文档能定位本页且 `git diff --check` 成功；该次
审计没有运行 QEMU、`make full-verify`、证据采集或远程 CI。最终状态仍只能读取发布包。
