# 双目标 uCore 科研 Agent 平台验证说明

本文档说明如何构建、运行和检查项目的两个目标。正文使用中文；命令、程序名、状态字段和运行输出保持原文。发布状态不由可变工作树或本文中的样例输出决定，而由 `evidence/releases/INDEX.md` 指向的 release bundle 和 `manifest.json` 决定。代码提交 C 先冻结，采集器在干净 C 上完成唯一 `make full-verify`，证据提交 E 再作为 C 的直接子提交只加入 bundle 与索引行。可重验的本地 C→E 交付是唯一正式交付链。GitLab 只托管源码与证据，不配置 Runner；`remote_ci.status` 固定为 `not-attached`。

本文日志或“关键输出”片段中出现的字面 `...` 只表示省略字段的格式示例，不是实际 marker；
validator 要求保存并匹配完整原行，不能把含省略号的示例复制成验收日志。

## 构建命令

换机器或刚 clone 仓库后，先检查依赖：

```bash
make doctor
```

Windows 上可以先在 PowerShell 运行：

```powershell
.\scripts\check-windows-prereqs.ps1
```

如果 Ubuntu/WSL 缺少工具链，可以运行：

```bash
bash scripts/install-ubuntu-deps.sh
```

plain target：

```bash
make -C baseline_ucore/user clean
make -C baseline_ucore clean
make -C baseline_ucore user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_plain
make -C baseline_ucore build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_plain LOG=warn INIT_PROC=rp_plain
make plain-platform-build TOOLPREFIX=riscv64-linux-gnu-
```

seeded plain target：

```bash
make -C baseline_ucore user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_seeded
make -C baseline_ucore build TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_seeded LOG=warn INIT_PROC=rp_seed_orch
```

AgentOS 专项构建和测试命令见 [agentos/verification.md](agentos/verification.md)。双目标平台构建由 `make dual-platform-run` 和 `make full-verify` 自动调用。

不启动 QEMU 的 AgentOS 内核增长与模块边界检查：

```bash
make local-check
```

它使用 `ci/kernel-budgets.json` 的固定 profile 检查源码、镜像、运行段、`struct proc`、Context 状态容量、线程栈和 boot stack。模块边界与 Agent case 清单均从版本化配置读取，不在文档复制易漂移的数量。当前配置为 `provisional_requires_full_suite`，在最终提交完成三轮重校准前不启用本地时长门。

双目标运行：

```bash
bash scripts/verify-dual-target-structure.sh
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

`make dual-platform-run` 会在启动 QEMU 前再次执行同一结构检查，避免直接运行双目标时跳过目录职责、平台程序覆盖、源码同步和 backend 证据覆盖检查。脚本随后运行一批代表性 seeded 请求：同一批请求会分别进入 plain uCore 和 AgentOS-uCore，两个平台目标在生成文件系统镜像前会清理用户态编译产物，确保镜像来自当前源码。脚本会复用这次 seeded 双目标运行提取出的 `rp_*` 状态文件执行状态文件对照，不再额外重复跑一轮普通平台 QEMU。

`ci/research-state-manifest.json` 是状态文件跨层契约：它限定两个目标的源码根、能声明状态文件名的调用、宿主状态和可选报告状态。`host_tools/research_state_manifest.py` 从该契约派生双目标 inventory 和状态 API allowlist；镜像提取器只从状态文件调用的字符串操作数恢复 14 字节目录短名，不再把函数名或文档中的任意 `rp_*` 单词当候选。契约同时核对 API 引用与 fixture，拒绝缺失、未知、重复 manifest 项和冲突短名前缀。因此 `rp_evidence_packet` 等长名必须由同一机制到达提取目录与 `/api/state/`，不能靠单文件特判恢复。

完整验证入口：

```bash
make full-verify TOOLPREFIX=riscv64-linux-gnu-
```

这条命令会按顺序执行：

- 先检查 `ci/kernel-budgets.json` 登记的 Agent case 集合已经过有效校准；provisional 会在 profile/QEMU 前失败；
- 双目标结构检查；
- 内核增长、PCB、栈容量和 Agent 模块边界门；
- 宿主机科研 Agent 平台能力对齐检查；
- 宿主机科研 Agent 平台测试主题对齐检查；
- 宿主机 Web/API/action 规模检查；
- 先运行与普通套件分离的 Context-sync/WAIT_ATOMIC `agentfinal_ucore` profile，再运行版本化 AgentOS 内核专项；prelude 使用独立 timing file，不计入 Agent suite 校准，并保存完整 canonical LF Guest 日志供后续实测提取；
- 共享基础安全加固、不含 AgentOS 扩展的 uCore 对照平台和 AgentOS-uCore 平台的 QEMU 运行；
- 主目标、Agent 对抗场景和 baseline 的进程生命周期复测；
- 双目标 syscall 公平性和全局文件对象表资源配额复测；
- AgentOS 线程资源账户、系统保留和跨域调度公平复测；
- 物理页全局/域级配额、系统保留及 teardown 退款复测；
- metadata primary/mirror 各八个 COW phase 的突然 VM 终止、raw-bank 恢复、单副本降级修复和单次暂态 EIO 修复复测；
- audit/timeline/provenance 三次同盘启动的持久身份、回收与擦除复测；
- VirtIO 丢中断、延迟完成、描述符压力、设备状态错误、flush 禁用和超时 reset 复测；
- workflow teardown 组合竞态三轮复测；
- 双目标 ENOSPC、持久 PUBLIC principal 与 AgentOS 存储保留复测；
- 文件系统块/inode 分配与释放事务的 busy、EIO 和突然终止一致性复测。

`make full-verify` 的 profile v6 严格约束上述步骤顺序和逐项原始日志。QEMU runner 控制台可转发原始字节，但落盘 `.guest.log` 将 CRLF 和孤立 CR 统一为 LF；exact-line marker、SHA256、CSV 行号和 manifest 一律绑定这份 canonical transcript。各项仍可用
`make physical-resource-test`、`make metadata-recovery-test`、`make observe-recovery-test`、
`make virtio-disk-test`、`make fs-enospc-test`、`make fs-allocator-fault-test` 等入口单独复现。多启动 runner 会把 runner stdout
和每次 Guest 启动日志合并保存；checkpoint mode 使用单次 `SIGTERM` 建立受控边界，metadata
powercut mode 通过认证 supervisor 向 QEMU leader 发送 `SIGKILL`，并在恢复前检查原始 bank。
两者都不等同于整机物理断电。聚合是否通过只以本次完整命令日志为准。

powercut runner 的 host 威胁边界是“受信 QEMU、非受信 Guest”：成功必须同时取得认证
`DONE`、Guest leader 的 `-SIGKILL`、自然退出为 0 的 supervisor、空进程树及控制端点恢复证明；
超时、控制通道异常、supervisor 被停止/终止或端点恢复失败一律 fail-closed。临时
`CLOEXEC`/procfs 防护只防止 QEMU 意外继承或重开 runner 管道，不承诺隔离任意同 UID
恶意 host workload；后者必须由独立 UID、PID namespace 和 cgroup（含 `cgroup.kill`）提供
外部 containment，不能把本 runner 当作 host 安全边界。

期望最后看到：

```text
[full-verify] all checks passed
```

如果需要指定工具，可以设置 `PYTHON_BIN=...`、`QEMU=...`、`CASE_TIMEOUT=...`。本机 WSL 环境下可直接使用默认 `python3`、`qemu-system-riscv64` 和 `240s` 单项超时时间。

正式评价还必须对同一 run 显式执行 `make evaluation-full-verify`。该入口不会只记录一个通过布尔值，也不会修改 `evidence/releases/INDEX.md`；它在源码提交 C 的 clean detached worktree 中运行上述真实命令，并把原始 `full-verify.log`、严格 16 步 summary、全部 raw artifact、工具版本和双层校验和封存在 run 的 `full-verification/`。采集器从 C 的 Git blob 提取 child dispatcher 到私有 runtime，并把 `PATH` 中的 `python`/`python3`、`sys.executable` 和 `sys._base_executable` 统一绑定到 shim；backing CPython 固定使用 `-I -S -B -u`，嵌套 dispatch 恢复精确标准库路径后只开放当前 detached worktree 与受控临时目录。环境记录交叉绑定 backing Python、Bash、dispatcher、shim、精确环境、PATH 解析及执行前后 hash。它防止 Host 环境注入和普通递归 Python 调用漂移，但不是隔离提交 C 内恶意源码、显式绕过 launcher 的命令或执行期敌对 Host 的安全沙箱。formal package 创建和可搬运验证先检查包内 measurement-source 快照，再由快照内版本化 verifier 重放双目标测量、全 raw semantic registry 与 allocator archive，同时检查命令退出状态、唯一完成 marker、step/raw 一一对应和逐文件内容。无快照、回退审计机 live checkout、自洽重签伪 raw、失败、删除或篡改 raw 均 fail closed。可搬运验证只证明包内快照、receipt 和 raw evidence 的内部完整性及语义可重放性，不认证声明的提交 C；只有 `verify --require-committed --repo-root <仓库>` 将快照核对到 C 的 Git blob 并验证 C→E 历史后，才达到本地 E3。development package 固定将该证明标记为 `unavailable`，不能携带 formal payload。

快速目标检查：

```bash
make target-readiness
```

这条命令会执行双目标结构检查和关键 Host 工具单测，不启动 QEMU。它适合在修改文档、脚本、状态渲染逻辑或对照逻辑后快速确认目标关系没有被破坏。内核、用户程序、文件系统镜像或启动流程发生变化时，仍应运行 `make dual-platform-run` 和 AgentOS 专项测试。

## 普通目标验证：plain target

运行目录程序：

```bash
timeout 45s make run TOOLPREFIX=riscv64-linux-gnu- CHAPTER=platform_plain LOG=warn INIT_PROC=rp_plain
```

`make run` 表示用当前构建产物重新安装全新可写镜像；需要重启并保留 `nfs/fs-copy.img` 中的现有数据时使用 `make run-persist`。后者不会因用户程序或磁盘格式变化自动覆盖持久盘，不兼容格式由内核挂载校验明确拒绝。baseline 目录中的同名目标语义一致。

关键输出应包含：

```text
rp_plain summary
catalog_ok=1 checks_ok=1 mature_ok=1 run_ok=1 search_ok=1
rp_plain: passed
```

运行多进程科研平台：

```bash
timeout 45s make plain-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

关键输出应包含：

```text
rp_orch: start programs=70
plain backend reference: expected_cases=7 runtime_cases=0
rp_compare_plain: demo_reference=plain_kernel ...
rp_orch: passed
```

说明：

- `rp_orch` 不是单进程静态表，而是通过普通 `fork/exec/waitpid` 串联多个用户程序。
- `rp_backend`、`rp_backend_exec`、`rp_study`、`rp_agentcmp` 记录 plain target 的用户态演示目录和 AgentOS 替代目标。`demo_reference` 只说明示例引用关系，不能当作内核已经通过某项动态检查的证据。
- `rp_realtask`、`rp_artifact_manifest`、`rp_workbench`、`rp_package` 对应真实输入、artifact、workbench、证据包和交付记录。

## 增强目标验证：AgentOS target

运行 AgentOS 科研平台：

```bash
make agentos-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

关键输出应包含：

```text
rp_agentos_orch: passed
rp_backend: cases=8 executable=8 agentos=mainflow_bound exports=1 status=ready
```

AgentOS 运行必须生成或验证以下状态文件：

```text
rp_agentos_kernel
rp_agentos_mainflow
rp_agentos_query
rp_agentos_recovery
rp_agentos_timeline
rp_agentos_collab_ack
rp_agentos_audit
rp_agentos_workbench
rp_agentos_package
rp_agentos_real_task
rp_agentos_conflict
```

其中 `rp_agentos_mainflow` 是增强目标的原始 telemetry 文件，不是 Guest 可自行签发的通过证据。它必须按固定顺序保存 11 个唯一、完整的未验证阶段并覆盖可信 Context、metadata 查询、事件通知、失败恢复、权限拒绝、timeline、ledger/provenance、文件编辑租约、workbench 文件校验、证据包 provenance 和真实任务 Context 等 12 类事实；任何 Guest `runtime_verified` 或 `generation=runtime;status=verified` 记录都 fail closed。Host 只从与提取清单一致的单层非链接目录读取该文件和 11 个规范来源，逐项复验唯一 claim、预期成功状态与阶段 telemetry，并独立计算完整 byte count/FNV-1a hash。

## 双目标验证

先做快速结构检查：

```bash
bash scripts/verify-dual-target-structure.sh
```

结构检查的数量随注册表变化，不在文档中硬编码。`baseline AgentOS surface: absent` 只表示检查器没有在 baseline 中发现 Agent syscall、Agent Context、Agent 文件 metadata 或 Agent 事件服务，不表示 `baseline_ucore/` 与上游 uCore 源码逐字相同。baseline 与主目标共享 syscall、同步、文件系统和进程生命周期等通用安全加固。

运行：

```bash
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-
```

语义成功标记的形状如下；发布计数只从本次日志和[正式证据索引](../evidence/releases/INDEX.md)指向的 bundle 读取：

```text
[dual-platform] checking target structure
[dual-platform] running seeded dual-target research platform
seeded_action_state: plain done passed=1 ... status=ready
seeded_action_state: agentos done passed=1 ... status=ready
rp_orch: passed
rp_agentos_orch: passed
rp_agentos_orch: kernel_agent=1 workflow=rp_orch status=ready
host_platform_alignment: ... status=ready
dual_platform_state_compare: ... run_result_match=1 ... status=ready
[dual-platform] plain and AgentOS platforms both passed
```

这条命令的意义是：用同一批 seeded 请求分别运行共享安全基底的 uCore 对照目标和 AgentOS-uCore 目标，并检查两个目标是否实际跑完同一批科研平台程序、围绕同一设定的模拟流程输出可比较结果。该流程包含数据准备、比对处理、结果分析、报告生成和归档交付；脚本会从两个文件系统镜像中提取 `rp_*` 状态文件，并执行状态文件对照：plain target 产出的状态文件必须全部能在 AgentOS target 中找到；只有普通的非证据状态兼容记录才要求 AgentOS 保留同一标识和状态。提取 summary 不是路径权威，必须与目录内单层、非链接、名字匹配 `rp_[a-z0-9_]+` 的普通文件精确相等，目录穿越、链接、重复或计数不符都失败。`demo_reference`/`demo_expected` 目录与 `runtime_verified` 记录从兼容性计数中排除。参考目录只能包含 target-specific registry 登记的文件和 `(destination, anchor)` 记录，并在去注释的源码中精确绑定唯一 owner；缺失、未知、重复、跨 owner 预发布和 reference/runtime 身份混用都会失败。Plain seeded 程序清单还要同时绑定 seeded profile、QEMU 日志和 `rp_orch_timing` 的 orchestrator/launcher、程序顺序、字节数、hash 与名称摘要。AgentOS target 可以额外增加内核证据文件和内核观测字段；`rp_agentos_mainflow` 只提供 11 个唯一、完整、有序的未验证 telemetry 阶段并覆盖 12 类内核事实，Host 再从安全状态清单独立复验每个来源的唯一 claim、成功状态、阶段字段、byte count 和 hash。任何 Guest runtime 验证回执都被拒绝。双目标比较器最后核对完整状态清单与关键原件，确认 AgentOS 状态产物不少于 plain target。两侧共有的通用安全加固不是本组对照的 AgentOS 增量。

`rp_ack` 与 `rp_tool` 是多个科研平台程序共享的追加日志。两个目标的 planner 都只通过
`rp_append_file()` 写入后缀，不再在启动时截断共享文件；追加 helper 以 `O_RDWR | O_CREATE`
打开并在写入前复核既有前缀，因此程序调度顺序不会丢弃先行 writer 的记录，也不会把一次
planner 启动放大成全文件 truncate/reclaim。`scripts/test-rp-state-append.py` 同时锁定两侧源码和
ASan/UBSan 下的追加行为。

双目标状态与 Host 执行回执不混用。每侧 complete-state ZIP 只含 `extract-summary.json` 和其中精确列出的纯 Guest `rp_*` 普通文件，禁止 `rp_host_run_result`；Plain/AgentOS Host run receipt 分别作为 `dual-plain-host-run-result.state` 和 `dual-agentos-host-run-result.state` 独立 raw artifact。receipt 使用 `sha256-inventory-v1` 规范绑定排序文件名、文件数、长度与全部字节，归档验证器和比较器均重算校验。离线验证安全解包，以 `min_common_files=240`、两份 receipt、seeded summary 和两份 Guest 日志显式重放 `compare_state()`，要求结果等于 `dual-state-compare.json`，并逐字核对 Mainflow、program ledger 与 backend 原件。普通 `make dual-platform-run` 只保留状态目录和 Host sidecar，只有最终采集启用的 `full-verify` evidence mode 才生成并发布 complete-state ZIP。

## 结果产物和实测边界

`make dual-platform-run` 会把原始日志、纯 Guest 提取状态和独立 Host run receipt 保存在 `/tmp/agentos-dual-platform/`，并把状态对照和诊断汇总写入 `results/latest/`。普通运行不生成最终 complete-state ZIP。这些文件证明两个目标执行了哪些功能路径，但状态计数、模板记录和页面汇总不能自动视为原始性能实验。

当前仓库只承认一组 provenance-bound Guest 实测：`agentbench_ucore` 的文件查询 benchmark。它在真实 Guest 中输出强制遍历、包含索引重建的冷索引和索引已就绪后的热索引测量。热索引每次都实际遍历候选链，不使用内核查询结果缓存。提取器同时要求后续存在完整的 `agentbench_ucore: parent passed` 行，任何字段、顺序或来源检查失败都会拒绝整组数据。

可信测量产物如下：

```text
results/latest/experiments/status.json
results/latest/experiments/measured-experiments.json
results/latest/experiments/dual-targeted-agentbench-guest.log
results/latest/experiments/raw/file-query-benchmark.csv
results/latest/experiments/experiment-stats.csv
results/latest/experiments/mechanism-notes.csv
results/latest/charts/experiment-file-query-bar.svg

evidence/releases/<bundle>/metrics/file-query-benchmark.csv
evidence/releases/<bundle>/metrics/file-query-benchmark.json
evidence/releases/<bundle>/logs/raw/agent-suite-guest.log
evidence/releases/<bundle>/logs/raw/dual-plain-complete-state.zip
evidence/releases/<bundle>/logs/raw/dual-agentos-complete-state.zip
evidence/releases/<bundle>/logs/raw/dual-plain-host-run-result.state
evidence/releases/<bundle>/logs/raw/dual-agentos-host-run-result.state
```

其中 `results/latest/` 只是可覆盖的本地预览。`measured-experiments.json` 不存在时，`experiments/status.json` 必须标记 `unavailable`，原始 CSV、统计和图表不得由公式、固定常量或功能状态推导。正式发布只引用 clean、已提交 HEAD 对应的 `evidence/releases/<bundle>/`；CSV 每行都保存来源日志 SHA256、marker SHA256、行号、命令、commit 和 run id，JSON manifest 再绑定完整来源文件。

Context/timeline、事件等待、并发写入、LLM Relay 和恢复流程仍由专项 Guest 测试验证功能和安全语义。它们在补充同等级的真实 Guest marker、来源哈希和重复测量前，不再宣称拥有独立 raw CSV、性能曲线或“六组原始实验数据”。仓库也不再提交由旧公式数据绘制的示例 `experiment-*.svg`。

已移除硬编码布局的运行时示例，避免将演示图误读为性能证据。运行时性能只接受由可信动态测量生成、并与原始日志及 commit/run 身份绑定的数据。

文件查询图只在 provenance-bound 测量可用时生成。阅读时必须同时打开 `file-query-benchmark.csv` 和 JSON manifest，核对三条路径的 `operations`、`primary_value`、`duration_unit=us`、`duration_value`、`rebuild_records` 以及来源绑定。时间来自 Guest `gettimeofday` 的原始微秒差值，允许真实的零差值，禁止 floor 或公式补值。图表只是同一 CSV 的可视化，不是额外证据，也不能用来外推 Context、事件、并发写入、LLM Relay 或恢复路径的性能。

报告生成由 `host_tools/summarize_dual_platform_results.py` 完成。该脚本只读取已有运行产物，不重新启动 QEMU，因此可以单独重跑：

```bash
python3 host_tools/summarize_dual_platform_results.py \
  --work-dir /tmp/agentos-dual-platform \
  --out-dir results/latest
```

汇总器每次都会先清除旧的 `experiments/` 和 `experiment-*.svg` 生成面，防止无测量重跑继续暴露旧公式文件。它会把已经验签的 manifest 和 Guest 源日志复制进结果目录；缺少 manifest 时只生成 `unavailable` 状态。

现场说明使用 `make contest-demo` 生成静态实测 Dashboard；正式评审使用 `make evaluation-dashboard` 生成 release bundle 内的 `dashboard/index.html`。两者都直接读取已校验数据，不启动独立页面服务。

快速结构检查不替代 QEMU 运行。它会检查 `baseline_ucore/` 不包含 AgentOS syscall、Agent Context、内核文件 metadata、Agent 事件队列等增强符号，同时确认根目录 AgentOS 内核、科研平台入口、同名科研平台程序覆盖关系、源码同步关系、backend 成本项保留关系和测试脚本仍然存在。它还会检查 AgentOS 内核源码中没有设定的模拟流程编号、示例项目名、固定阶段 selector、固定失败原因等科研示例常量，保证科研平台仍是用户态负载，不是内核默认业务；旧兼容工具 id 只允许出现在兼容性和权限测试里，平台主流程必须使用 `action_commit`、`artifact_update` 等通用工具。它还会检查 Makefile 和脚本入口关系：`make full-verify` 必须调用完整验证脚本并串联结构检查、`local-check`、seeded 双目标 QEMU、AgentOS 内核专项及资源和恢复回归；`make dual-platform-run` 必须调用双平台脚本；plain target 必须以 `rp_orch` 启动，AgentOS target 必须以 `rp_agentos_orch` 启动。完整功能仍以 `make dual-platform-run` 和 AgentOS 专项测试为准。

宿主机科研 Agent 平台能力对齐检查由 `host_tools/check_host_platform_alignment.py` 完成。它默认读取同级目录 `research-agent-platform-userland`，把其中的工作流、项目工作台、artifact、数据与实验室对象、多 Agent 协作、provenance、治理、运行控制、复核发布、页面/API 和 AgentOS 对照等核心模块，映射到 `baseline_ucore/` 普通目标与根目录 AgentOS-uCore 的 `rp_*` 程序和状态输出入口。下列行只说明输出形态，发布数值以 bundle 中本次日志为准：

```text
host_platform_alignment: ... runtime_state_checked=1 ... untracked_host_modules=0 status=ready
```

如果公开环境没有仓库外的宿主机平台目录，该检查会输出 `status=skipped`，不影响仓库内双目标验证；在本机开发时应以 `status=ready` 作为宿主机平台和 uCore 迁移层仍保持主要能力对齐的证据。双目标脚本会在提取两个文件系统镜像后再次运行该检查，并传入 `plain-state` 与 `agentos-state` 目录；此时 `runtime_state_checked=1` 表示清单中的能力族都已经在两个目标里产出真实状态文件。模块总数和 `untracked_host_modules` 必须从本次日志读取，不把历史数量外推；宿主机平台新增模块时，检查器要求先归入能力族，再判断是否需要增加 uCore 侧程序、状态文件或页面入口。

宿主机科研 Agent 平台测试主题对齐检查由 `host_tools/check_host_test_alignment.py` 完成。它默认读取同级目录 `research-agent-platform-userland/tests/test_platform.py`，把宿主机平台的测试方法归入状态配置、工作流运行、科研工作台、数据与实验室、Agent/LLM/对照、页面/API/交付、provenance/复核/治理等主题，并检查 plain target 与 AgentOS target 的 `rp_test_suite.c` 是否保留对应证据项。下列行只说明输出形态，发布数值以 bundle 中本次日志为准：

```text
host_test_alignment: ... unclassified_tests=0 runtime_state_checked=1 status=ready
```

`runtime_state_checked=1` 表示检查器已经读取 plain target 与 AgentOS target 在 QEMU 运行后抽取出的 `rp_tests` 状态文件，并确认七类测试主题都由运行时状态给出证据。

如果宿主机平台新增测试方法，而测试名称无法归入现有主题，本机验证会显示 `unclassified_tests` 大于 0。此时应先判断新增测试代表的新能力是否已经迁移到两个 uCore 目标；如果没有，需要补充对应 `rp_*` 程序、状态文件、状态查看入口或 AgentOS 内核使用路径。

宿主机 action kind 对齐检查由 `host_tools/check_host_action_kind_alignment.py` 完成。它读取宿主机 `api_server.py` 里的 `/actions/...` 路由，用 `plain_ucore_action_runner.py` 的映射函数转换成 seed kind，再检查 plain target 与 AgentOS target 的用户态源码中是否都有对应 `kind=...` 处理。检查器还会排除 `rp_compare_plain.c`、`rp_test_suite.c` 这类只负责验证的文件，要求每个 kind 至少出现在一个真实运行程序里。下列行只说明输出形态，发布数值以 bundle 中本次日志为准：

```text
host_action_kind_alignment: ... plain_missing=0 agentos_missing=0 plain_handler_missing=0 agentos_handler_missing=0 status=ready
```

这项检查用于发现“路由数量已经跟上，但 uCore seed 路径没有真正处理某个 action”的问题。例如宿主机提供 `/actions/research/rerun` 时，两个 uCore 目标都应当能接收 `kind=research_rerun`，并在 `rp_input`、`rp_runner`、`rp_report_text` 或相关状态文件中留下可读结果。

预置 action 状态检查由 `host_tools/check_seeded_action_state.py` 完成。它构造 44 个代表性宿主机请求，以 `/actions/research/rerun` 为主，同时覆盖研究输入、证据处理、artifact 输入与派生、workflow 主流程和阶段动作、Guest 模板 relay 请求与返回、workbench 文件校验、数据集操作、项目生命周期、研究协议、项目复核、workflow 可移植性和 AgentCompare；随后分别运行 plain uCore 与 AgentOS-uCore 的预置入口，并从两个文件系统镜像中检查 `rp_input`、`rp_runner`、`rp_report_text`、`rp_artifact_manifest`、`rp_stage_dag`、`rp_llm_packets`、`rp_wfio`、`rp_usableproj`、`rp_studyproto` 等状态文件是否都写入同一组关键状态。该脚本还会读取宿主机 action 路由，报告 `host_routes`、`seeded_routes` 和 `seeded_kinds`：前者表示宿主机 action 路由总数，后两者表示 44 个实跑请求中能与当前宿主机 API 路由逐字对应的路由和 kind 数量；其余实跑请求是 uCore 迁移层保留的代表性样本，会作为 `seeded_extra_routes` 写入渲染摘要。`make dual-platform-run` 直接复用这次检查得到的镜像提取目录作为状态对照和渲染输入，因此它也是双目标主运行路径。下列行是契约输出形态，不是预填的发布结果：

```text
seeded_action_state: action=/actions/research/rerun action_count=44 ... plain=ready agentos=ready status=ready
```

这项检查补充了 action kind 检查：action kind 检查回答“源码是否有对应处理”，预置 action 状态检查回答“代表性宿主机请求进入 QEMU 后是否真的产生可读结果”。当前批次以 rerun action 作为主线，因为它会同时影响输入、运行器、报告文本和 artifact manifest；其余请求用于覆盖数据、证据、artifact、workflow、LLM、workbench、项目生命周期、研究协议、项目复核和可移植性状态，避免只验证单一路径。未进入 QEMU 实跑的宿主机 action 仍由 action kind 检查约束源码处理路径；如果某个新路由需要成为示例主证据，应加入预置请求，并补充对应状态文件断言。

宿主机 Web/API/action 规模检查由 `host_tools/check_host_surface_alignment.py` 完成。它直接读取仓库外宿主机平台的 `agent_platform/api_server.py`，统计显式 API 路由、action 路由和下载引用数量，再检查 `baseline_ucore/` 普通目标与根目录 AgentOS-uCore 的 `rp_web_export.c` 和双目标运行状态文件是否保留对应规模。下列行只说明输出形态，发布数值以 bundle 中本次日志为准：

```text
host_surface_alignment: ... runtime_state_checked=1 status=ready
```

如果宿主机平台新增 API 或 action，本机验证会要求更新两个 uCore 目标的状态输出和状态查看入口。该检查关注平台能力是否在迁移层保留，不要求把宿主机 Python Web 服务复制进 uCore 镜像。

## AgentOS 专项验证

增强目标的内核机制还需要单独运行专项脚本。若 case 集合或固定环境变化而使时长策略重新进入
provisional，普通全套会在 QEMU 前按设计失败；开发阶段应运行定向 case。固定 runner 校准不能
直接手工调用 `run-agent-tests.sh` 或独立填写 timing，而必须由统一 harness 在冻结提交上执行：

当前容量合同把持久存储与 metadata catalog 分开验收：每 workflow 的 inode STORAGE policy 硬下限为 320，当前镜像约为 342；catalog 仍为每 scope 112 条，其中 live AUTOSCAN 新增长度最多 96 条并为显式 metadata 保留 16 条。旧候选把 workflow inode 同样钳制为 112 的做法只保留为历史失败基线。定向 AgentScope 必须证明第 97 个普通文件及 catalog 满后的额外文件仍能创建且保持 scope 隔离，同时第 17 个显式 metadata 请求稳定返回 `NO_SPACE`。所有 sidecar bind/clear/deferred 更新统一通过 `agent_file_state_set_index()` 校验、持久化并在失败时恢复旧值；write/sync/truncate/delete 统一通过 `agent_fs_apply_inode_event()`，create 只在 VFS 成功发布后进入目录协调。Host 合同还检查持久 deferred 状态、饱和时重复扫描抑制、释放后重建和强制 reload 后的持久结果。v7 快照加载只按表示、SYSTEM 64、ordinary 448、每 scope 112、lifecycle 与唯一键等稳定磁盘合同判定；同版本旧快照中 97 至 112 条 AUTOSCAN 会完整装载而不静默删除，第 113 条仍判损坏。加载后的超额 scope 只允许 AUTOSCAN 数量不变或减少；新增及显式记录转 AUTOSCAN 均被拒绝，降至 95 条后才可再次增长。精确 receipt 回滚只复核硬边界、唯一键与 post-state，不受后来收紧的软准入策略阻断。

```bash
# 定向开发回归，不宣称完整套件或时长门通过
AGENT_TEST_CASE=agentfinal_ucore TOOLPREFIX=riscv64-linux-gnu- \
  QEMU=qemu-system-riscv64 CASE_TIMEOUT=240s bash scripts/run-agent-tests.sh

# 只在版本化 local E3 profile 和 clean detached HEAD 执行；output 必须在仓库外
QEMU=/opt/qemu/qemu-system-riscv64.exe \
TOOLPREFIX=/opt/xpack-riscv/bin/riscv-none-elf- \
  python3 -I -S -B scripts/agent_test_calibration.py collect \
  --root . --source-commit "$(git rev-parse HEAD)" \
  --output /var/tmp/agentos-calibration-"$(git rev-parse --short=12 HEAD)" \
  --case-timeout 240s
```

该 harness 预声明并串行执行严格三轮版本化 Agent case 集合，为 campaign、round、session 和 execution 生成互不
重复的 256-bit nonce。每个 QEMU 执行由 runner 独占创建 attestation，绑定源码 commit/tree、
runner 与 QEMU/编译器/Python 路径和哈希、内核及文件系统镜像、Guest 日志、真实 monotonic
区间和退出结果；timing inventory 只能由与 `agent_test_suite.expected_cases` 完整一致的 attestation 集合重建。输出明确标记为未签名的本地 E3
复现证据，不是第三方执行或签名证明，也不能证明控制本机和工具的操作者没有主动伪造。
采集器逐字节核对 tracked tree，并把版本化 profile 中的 GCC/ld/objcopy/objdump/as、Host CC、
QEMU、Python、Bash、Make 与 Git 身份回灌给子进程；production 路径没有公式或 fixture 输入。单元测试中的合成
fixture 只验证拒绝逻辑，不能成为校准事实。未校准的本地环境仍验完整
Agent suite 语义、日志和 timing inventory，但以 `duration-profile profile=none` 回执明确跳过
墙钟阈值。旧 schema-2 timing/log 包只能作为历史记录。

校准子进程不继承任意开发环境：只保留最小 OS/runtime 变量，并固定 locale、临时目录、
`LOG=error`、`CHAPTER=agent` 和 local-E3 duration profile；`BASH_ENV`、`MAKEFLAGS`、`MAKEFILES`、
`PYTHONPATH`、GCC 搜索路径及所有测试 profile 注入均被移除。采集开始时拒绝全部 ignored/untracked
条目；每轮 clean 后重验零额外条目，结束后只允许版本化清单中的 build、用户目标、镜像和
`initproc.S` 输出。大小写等价路径、空目录、symlink 和 NTFS junction 同样 fail closed。

各 case 的完整 marker 与负向条件由 runner validator 维护，本文不复制容易与实现分叉的 marker 清单。套件覆盖 Agent Context、结构化工具调用、metadata/观测、可信 workflow 关闭和资源回收；只有 `ci/kernel-budgets.json` 登记的全部 case 通过严格退出契约并进入同一 release bundle，才能形成当前发布结论。

## 静态结果验证

现场演示和正式评价分别生成静态 Dashboard，不启动本地页面服务：

```bash
make contest-demo-check
make evaluation-verify
make evaluation-dashboard
```

Dashboard 只读取已经校验的 Guest/Host 原始材料；缺失字段保持 `unavailable`，不能把页面状态替代内核专项测试或性能测量。

## 安全与资源专项复测

安全修复按机制约束分别验证，不能只用结构扫描或科研平台页面代替：

```bash
# Agent 权限、可信映像、VFS 域、调度公平和 syscall 用户输入防护
# calibrated 后可运行 make agentos-test；provisional 阶段逐项替换定向 case
AGENT_TEST_CASE=agentsecurity_ucore bash scripts/run-agent-tests.sh

# 主目标、Agent 对抗场景和 baseline 的退出、等待、回收与进程域配额
make proc-reap-test TOOLPREFIX=riscv64-linux-gnu-

# 双目标全局 filepool 的资源域上限、普通水位、系统保留和最终引用退款
make file-resource-test TOOLPREFIX=riscv64-linux-gnu-

# 双目标真实 ENOSPC、持久 PUBLIC principal，以及 AgentOS 分级保留量
make fs-enospc-test TOOLPREFIX=riscv64-linux-gnu-

# 物理页配额/保留、metadata COW 重启、观测持久身份和 VirtIO 故障矩阵
make physical-resource-test TOOLPREFIX=riscv64-linux-gnu-
make metadata-recovery-test TOOLPREFIX=riscv64-linux-gnu-
make observe-recovery-test TOOLPREFIX=riscv64-linux-gnu-
make virtio-disk-test TOOLPREFIX=riscv64-linux-gnu-

# 16 KiB 内核栈、4 KiB guard 和构建期调用图预算
make kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-
make -C baseline_ucore kernel-stack-check TOOLPREFIX=riscv64-linux-gnu-

# canonical profile 下的增长、PCB、栈容量和 Agent 模块边界
make local-check
```

Agent suite 的时长数据必须与 `ci/kernel-budgets.json` 中的源码 fingerprint、profile 和 case 清单完全匹配。独立 Context-sync/WAIT_ATOMIC prelude 不计入 suite 时长。`make full-verify` 会串联 physical、metadata recovery、observation recovery、VirtIO fault 和 filesystem allocator fault runner，并保存原始 runner/Guest 日志。发布状态由 `INDEX.md` 和 bundle manifest 判定；完整边界见 [agentos/security-hardening.md](agentos/security-hardening.md) 与 [agentos/verification.md](agentos/verification.md)。

## 内核机制说明

功能呈现应和内核实现对应起来。以下七项按赛题要求整理，便于检查设计是否落在真实内核路径上。

1. 启动、trap、中断、syscall、上下文切换。

   代码位置：`baseline_ucore/os/entry.S`、`baseline_ucore/os/proc.c`、`baseline_ucore/os/trap.c`、`baseline_ucore/os/syscall.c`、`baseline_ucore/os/timer.c`，以及根目录 `os/` 下对应增强实现。

   相关处理：QEMU 进入 RISC-V 内核后，`INIT_PROC` 成为用户态入口。用户态通过 `a7` 和 `a0..a5` 发起 syscall，trapframe 保存用户寄存器，`scheduler()`、`sched()`、`yield()` 完成上下文切换。`rp_plain`、`rp_orch`、`rp_seed_orch`、`rp_agentos_orch` 都通过这一路径运行。

2. 进程、线程、调度、`fork`、`exec`、`wait`。

   代码位置：`os/proc.c`、`os/loader.c`、`os/syscall.c`。

   相关处理：plain target 中 `rp_orch` 启动并等待多个角色程序，覆盖 `fork()`、`exec()`、`wait()`、`exit()`、trapframe 复制、用户栈参数布置和文件描述符继承。AgentOS target 在同一进程模型上增加 Agent role、capability、Context 和调度记录。

3. 虚拟内存、地址空间、地址翻译、页表、缺页处理、权限检查。

   代码位置：`baseline_ucore/os/vm.c`、`baseline_ucore/os/proc.c`、`baseline_ucore/os/loader.c`、`baseline_ucore/os/trap.c`；增强目标的 `os/vm.c`、`os/proc.c`、`os/agent_context.c`、`os/workflow_lifecycle.c`。

   相关处理：syscall 访问用户地址时使用 `copyin()`、`copyout()`、`copyinstr()`；`uvmcopy()` 服务 `fork()`；exec 用 prepare/commit/abort 把身份与地址空间原子发布。Agent Context 的 9 页 detail sidecar、6 页用户 mirror 和 6 页可信 shadow 由 Context owner 管理，并作为 21 页整体通过 `RESOURCE_AGENT_STATE_PAGE` 原子计费；4.5 MiB 是 sidecar-only 的独立细节预算，完整状态全局预算为 10.5 MiB。每线程物理内核栈按 live admission 映射，32 MiB 是虚拟容量，8 MiB 是受信/保留物理池；启动/调度另使用 64 KiB boot stack。

4. 文件系统、目录、文件描述符、pipe、设备文件和文件抽象。

   代码位置：`os/fs.c`、`os/file.c`、`os/pipe.c`、`os/console.c`、`os/virtio_disk.c`。

   相关处理：plain target 的科研状态都通过普通文件保存，覆盖 inode、目录项、file table、fd、read/write/close、pipe、console、virtio block 和文件系统镜像。增强目标把 metadata 绑定到真实 `dev/inum/incarnation`，并通过 `.agentmeta`、digest cache、VFS 安全域和 edit lease 连接 Agent 记录与真实文件活动。

5. Linux/RISC-V syscall ABI、参数、返回值、错误处理。

   代码位置：`baseline_ucore/os/syscall_ids.h`、`baseline_ucore/os/syscall.c`、根目录 `os/syscall_ids.h`、`os/syscall.c` 和用户态 syscall wrapper。

   相关处理：syscall id、参数寄存器、返回寄存器、用户指针复制、错误返回共同决定 syscall ABI。增强目标的工具调用在产生副作用前校验 tool id/name、参数类型、payload、输出缓冲区和权限，错误请求不能污染 Context、metadata、mailbox 或 ledger。

6. 并发同步、资源管理、死锁处理、竞态处理、用户态/内核态隔离。

   代码位置：`baseline_ucore/os/sync.c`、`baseline_ucore/os/proc.c`、`baseline_ucore/os/file.c`、`baseline_ucore/os/fs.c`；增强目标的 `os/resource_controller.c`、`os/workflow_lifecycle.c`、`os/proc.c`、`os/agent_core.c`、`os/agent_context.c`、`os/agent_identity.c`、`os/agent_ipc.c`、`os/agent_lifecycle.c`、`os/agent_metadata*.c`、`os/agent_observe.c`。

   相关处理：AgentOS 用 generation-safe EXEC/STORAGE account 统一核算进程、线程、file object、block/inode、cache、I/O 和 Agent state page；`resource_domain_id` 仅用于 CPU 调度分区。workflow 以 `(id,generation)` 维持撤销谱系；正常退出、fault、revoke 和构造回滚共用单一 teardown。`os/agent.c` 只保留 facade，可写状态归各 owner 模块。

7. QEMU、RISC-V、设备适配和行为一致性。

   代码位置：`os/riscv.h`、`os/sbi.c`、`os/sbi.h`、`os/timer.c`、`os/virtio.h`、`os/virtio_disk.c`、`os/bio.c`。

   相关处理：两个目标使用同一 RISC-V QEMU、SBI、timer、trap、virtio block 和文件系统镜像形态，因此比较重点是内核 Agent 支持差异，而不是平台差异。如果迁移到真实硬件，应优先检查 SBI、timer、中断、内存布局和块设备驱动。

## 最终检查重点

最终材料至少应呈现：

- `baseline_ucore/` 保持共享通用安全加固、不含 AgentOS 专属服务的对照目标职责。
- 根目录 `os/`、`user/` 和 `scripts/` 提供 AgentOS-uCore 增强目标。
- plain target 能运行完整科研 Agent 平台并输出可读状态文件。
- AgentOS target 能运行等价科研流程，并让关键阶段依赖内核 Agent 服务。
- 状态查看入口能呈现两个目标的差异。
- 文档能把功能呈现对应到内核机制，而不是只描述用户态应用。

## 暂停验证说明

如果当前有其他编辑者正在修改根目录 AgentOS 源码，可以先暂停构建和 QEMU 运行，只做静态文档和状态渲染对齐。提交前仍需要重新运行双目标构建、QEMU 路径和状态渲染检查。
