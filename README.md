<p align="center">
  <img src="docs/agentos/assets/agentos_logo.png" alt="AgentOS logo" width="680">
</p>

# AgentOS-uCore

AgentOS-uCore 是面向 AI Agent workflow 的 RISC-V uCore 内核扩展，也是计算机操作系统能力竞赛系统功能实现赛道作品。项目把 Agent 身份、Context Path、结构化工具、可信 IPC、资源域、实时文件查询和可验证 workflow fence 放入内核；科研 Agent 平台仍在用户态，作为综合负载而不是内核内置业务。

## 评审入口

- [竞赛评审入口](docs/contest/README.md)：赛题映射、演示顺序和材料边界。
- [系统设计](docs/agentos/design.md)：当前内核架构与关键不变量。
- [ABI 参考](docs/agentos/api.md)：用户接口、兼容项和错误语义。
- [要求追踪](docs/agentos/requirements-traceability.md)：任务到源码与验证入口的映射。
- [验证说明](docs/verification.md)：构建、静态检查、QEMU 与正式证据边界。
- [正式证据索引](evidence/releases/INDEX.md)：只从已冻结 bundle 读取发布结果。

`results/` 和普通开发日志不是发布证据。缺少实测数据时保持 unavailable，不以公式、固定常量或历史样本填充。

## 当前核心设计

| 机制 | 当前实现 |
| --- | --- |
| Agent Workflow Credit Domain | 每个资源账户维护 `used/pending/free`（U/P/F）credit。补充额度时批量预充，普通 commit/cancel/release 只在本地三态间移动；额度不足、资源压力、context switch、账户推进或 workflow fence 才 trim/汇总。全局硬容量和账户硬限额始终按 `U+P+F` 验证。 |
| Fence-Sealed Evidence Ring | 每个 workflow 按需计费 4 页，普通区 48 槽、关键区 16 槽。普通成功 Context 只写一次 canonical ring 记录；拒绝或有授权效果的关键记录同时保留兼容 ledger 投影。fence 把有序事件、gap、challenge、metadata generation 和 credit digest 密封为 SHA-256 根。 |
| Agent Live-Query FS | 只有显式 `agent_file_meta_set()` 的记录进入内存 catalog 和 `status/stage/kind` 选择性索引。typed watch 根据谓词变化产生 `ENTER/UPDATE/LEAVE`，队列不足或增量丢失时产生带 generation 的 `RESYNC_REQUIRED`。不扫描普通目录，不写 metadata catalog 磁盘快照，也不提供 crash catalog recovery。 |
| 收敛 lifecycle | workflow 以不可变 `id + generation` 标识，核心状态是 `member_refcount + closing`。operation、departure 和 fence gate 阻止新旧操作穿越 cut；最后成员离开后才允许回收资源域。不存在对外宣称的多阶段 retirement workflow。 |

## 语义边界

- Workflow fence 返回 320 字节 receipt，绑定 request id、challenge、fence sequence、精确 credit 使用量、metadata generation、事件范围、gap 数和前后根。`PARTIAL_COVERAGE` 是显式标志：当前根覆盖 Evidence Ring 的规范事件，不声称覆盖整个文件系统或所有调度事实。
- `CREDIT_EXACT` 表示 fence gate 内已 trim exec/storage 账户，`pending == 0`，receipt 的 `resource_used[]` 是该 cut 的精确 U 值。它不表示资源数据本身持久化。
- `EVIDENCE_SEALED` 表示 challenge-bound 内存证据根已经发布；不是磁盘 durable receipt，也不证明掉电恢复。
- `METADATA_VOLATILE` 明确表示 metadata generation 属于当前启动周期的内存 catalog。
- audit、timeline、provenance 和 ledger API 仍作为兼容读取视图存在。普通成功 Context 的 canonical 存储来自 Evidence Ring；关键记录或 ring 写入失败时才走受保护的兼容 ledger 路径。因此不能把兼容视图描述为完全删除。
- `AGENT_FILE_META_F_PERSIST` 与 `AGENT_FILE_META_F_AUTOSCAN` 只为源码/ABI 兼容保留，当前显式 metadata 写入若携带这些标志会返回 `AGENT_STATUS_BAD_PARAM`。
- observe recovery syscall 号仍被保留以避免编号复用，但内核固定返回 `AGENT_STATUS_BAD_PARAM`；不存在可枚举、读取或 reap 的 observation crash-recovery catalog。

## 赛题任务映射

| 任务 | 主要实现 |
| --- | --- |
| Agent 进程与地址空间 | 可信映像、角色/capability、Context 映射、不可变 workflow generation、closing/member/gate 生命周期。 |
| 结构化工具调用 | name/id 目录、typed KV、参数验证、能力校验和 `agent_run()` 批处理。 |
| Context Path | 内核可信记录、只读 mirror、查询、快照、分支与因果字段。 |
| 文件属性查询 | 显式 volatile metadata、选择性内存索引、inode incarnation、内容摘要、typed live query。 |
| Agent Loop | 有界事件队列、watch/wait、heartbeat、可信 IPC 路由和 Agent 感知调度。 |
| 综合应用 | plain uCore 与 AgentOS-uCore 运行同一科研工作流合同；完整结果只由正式双目标测量发布。 |

`baseline_ucore/` 是共享通用修复但不包含 AgentOS 子系统的对照目标，不是未经修改的上游镜像。双目标总耗时用于端到端比较；单项内核机制归因需要同内核消融或专项计数。

## 快速构建

Windows 首次检查：

```powershell
.\scripts\check-windows-prereqs.ps1
```

在配置好的 Linux、WSL 或项目工具链环境中：

```bash
make doctor
make build TOOLPREFIX=riscv-none-elf-
make agent-module-check TOOLPREFIX=riscv-none-elf-
make kernel-budget-check TOOLPREFIX=riscv-none-elf-
```

核心机制的 Host 模型/变异测试：

```bash
python -B scripts/test-workflow-credit-domain.py
python -B scripts/test-workflow-fence.py
python -B scripts/test-workflow-syscall-cut.py
python -B scripts/test-agent-evidence-ring.py
python -B scripts/test-agent-live-query-fs.py
```

需要 Guest 行为时再运行：

```bash
make agentos-test TOOLPREFIX=riscv-none-elf-
make dual-platform-run TOOLPREFIX=riscv-none-elf-
```

正式采集必须遵守 [验证说明](docs/verification.md) 的干净提交、命令计数和证据绑定规则，不能用额外 QEMU 试跑替换或污染指定配对测量。

## 仓库结构

```text
os/                AgentOS-uCore 内核
user/              用户库、专项程序和科研平台负载
baseline_ucore/    不含 AgentOS 服务的共享安全基底对照
scripts/           构建、静态合同、QEMU 与回归工具
host_tools/        双目标测量、证据校验和 Dashboard 生成
ci/                UAPI、预算、测试与评价清单
docs/              设计、ABI、验证和竞赛材料
evidence/releases/ 已发布的冻结证据索引
```

## 来源与许可

项目基于 LearningOS/uCore 教学内核扩展。源码采用 [GPL-3.0](LICENSE)，文档采用 [CC-BY-SA-4.0](DOCUMENTATION_LICENSE.md)。Linux CPU accounting/percpu/rstat、Linux BPF ring buffer 和 Haiku BFS live query 仅作为公开设计思想来源；本仓库采用 clean-room 的项目特定实现，没有 vendoring 它们的源码、测试数据、二进制或磁盘格式。完整披露见 [NOTICE](NOTICE) 与[第三方及原创增量说明](docs/contest/third-party-and-originality.md)。
