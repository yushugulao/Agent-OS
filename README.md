# AgentOS-uCore

AgentOS-uCore 是面向 AI Agent 工作流的 RISC-V uCore 内核扩展，也是计算机操作系统能力竞赛系统功能实现赛道作品。项目把身份授权、上下文、结构化工具调用、可信 IPC、工作流生命周期、资源控制和观测能力放入内核；科研 Agent 平台保留在用户态，作为综合负载和现场演示。

## 评审入口

- [竞赛评审入口](docs/contest/README.md)：赛题映射、演示顺序与提交材料。
- [要求追踪表](docs/agentos/requirements-traceability.md)：任务要求、实现位置和动态验收入口。
- [正式证据索引](evidence/releases/INDEX.md)：绑定提交、环境、原始日志和 Dashboard 的发布结果。
- [评价方法](docs/evaluation.md)：实验设计、统计口径和证据边界。
- [系统设计](docs/agentos/design.md) 与 [ABI 参考](docs/agentos/api.md)：内核机制和用户接口。

仓库中的 `results/` 是本机生成物，不作为正式数据源。展示数值必须来自正式证据包，Dashboard 只呈现实测时延、吞吐、公平性、隔离性、体积和资源预算，不使用“通过数量”代替性能。

## 系统概览

根目录是 AgentOS-uCore；`baseline_ucore/` 是共享通用安全修复、但不包含 AgentOS 子系统的对照目标。两者运行同一科研工作流合同，用于观察完整系统路径。单个内核机制的性能结论由同内核消融实验给出，不从双目标总耗时直接归因。

| 内核机制 | 作用 |
| --- | --- |
| 身份与授权 | 可信 Agent 角色、capability、workflow scope 和可执行映像绑定。 |
| Context Path | 内核可信历史、用户只读 mirror、快照、查询与 rollback。 |
| 结构化工具调用 | 名称和编号协议、类型校验、批处理与稳定错误码。 |
| 生命周期与 IPC | 不可变 lifecycle id/generation、端点授权、撤销和统一 teardown。 |
| 通用资源控制 | 进程、线程、文件、存储、物理内存和 I/O 的账户、配额及系统保留。 |
| 持久化与观测 | 文件 metadata、audit、timeline、provenance、恢复和有界查询。 |

内核按职责拆为身份授权、Context、IPC、metadata、观测、生命周期、资源控制和块 I/O 模块。普通文件路径和传统 uCore syscall 保留兼容；可信文件引用热路径使用缓存授权和批量结算，避免每次小操作重复支付完整安全检查成本。

## 赛题任务

| 任务 | 主要实现 | 动态验收 |
| --- | --- | --- |
| Agent 进程与地址空间 | 可信身份、角色、能力、Context 映射和 fork/exec/exit 生命周期 | `agentfinal_ucore`、`agentsecurity_ucore`、`agentscope_ucore` |
| 结构化工具调用 | name/id 协议、批量运行、参数类型和权限校验 | `agentfinal_ucore`、`agentbench_ucore` |
| Context Path | push/query/snapshot/rollback、shadow 与 mirror | `agentfinal_ucore`、`agentscan_ucore` |
| 文件属性与摘要查询 | inode 绑定 metadata、索引、摘要、查询计划和编辑租约 | `agentfs_ucore`、`agentscan_ucore`、`agentconflict_ucore` |
| Agent Loop | 事件队列、watch/wait、heartbeat、可信消息路由和公平调度 | `agentloop_ucore`、`agentsched_ucore`、`threadresource_ucore` |
| 综合应用 | 检索、分析、复核、恢复、写作和审计组成的科研 Agent 工作流 | `make dual-platform-run`、正式评价套件 |

专项程序和顺序以 [`ci/kernel-budgets.json`](ci/kernel-budgets.json) 为准；实验负载、样本和统计方法以 [`ci/evaluation-suite.json`](ci/evaluation-suite.json) 为准。README 不复制容易漂移的清单。

## 快速运行

Windows 首次使用：

```powershell
.\scripts\check-windows-prereqs.ps1
```

进入 Linux、WSL 或项目配置的 MSYS2 工具链环境后：

```bash
make doctor
make contest-demo TOOLPREFIX=riscv64-linux-gnu-
make contest-demo-check
```

现场演示从干净提交构建真实 RISC-V 镜像，在 QEMU 中交替运行兼容路径与 AgentOS 原生路径，再生成包含原始计数、时延和结果摘要的离线页面。

常用开发入口：

```bash
# AgentOS 专项 Guest 测试
AGENT_TEST_DURATION_PROFILE=none \
  make agentos-test TOOLPREFIX=riscv64-linux-gnu-

# 对照与 AgentOS 综合科研工作流
make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-

# Host 合同、内核预算和全部本地验收
make local-check
AGENT_TEST_DURATION_PROFILE=none \
  make full-verify TOOLPREFIX=riscv64-linux-gnu-
```

正式评价把采集、验证和展示分开，避免页面或旧结果反向成为证据：

```bash
make evaluation-doctor
make evaluation-smoke
make evaluation-run TOOLPREFIX=riscv64-linux-gnu-
make evaluation-verify
make evaluation-kernel-cost TOOLPREFIX=riscv64-linux-gnu-
make evaluation-dashboard
make evaluation-package
```

`evaluation-run` 需要干净提交。开发环境通常使用 `AGENT_TEST_DURATION_PROFILE=none`；只有与版本化校准记录完全匹配的机器才能使用本地时长门。具体参数见 [评价方法](docs/evaluation.md)。

## 并行策略

构建、Host 测试和互相隔离的 QEMU case 默认通过 `scripts/resource-jobs.py` 按 CPU affinity、cgroup 限额和可用内存选择并行度。可显式覆盖：

```bash
export AGENTOS_BUILD_JOBS=12
export AGENTOS_TEST_JOBS=8
export AGENTOS_QEMU_JOBS=4
```

每个 QEMU lane 使用独立工作树、构建目录、文件系统镜像和日志。当前 uCore 内核是单 Hart，因此 Guest 保持 `-smp 1`；多核用于并行运行独立虚拟机，而不是伪装成尚未实现的 SMP 内核。

正式证据 campaign 为保证提交、计划和原始材料的原子绑定而串行记录；这条一致性边界不与普通回归测试的多核执行混用。

## 结果阅读

评审时优先查看正式证据包中的：

| 文件 | 内容 |
| --- | --- |
| `dashboard/index.html` | 实测时延、吞吐、并发扩展、公平性和隔离性图表 |
| `dashboard/metrics.csv` | 可复算的派生指标 |
| `dashboard/evaluation-summary.json` | 机器可读汇总 |
| `manifest.json`、`checksums.sha256` | 提交、环境和原始材料绑定 |
| manifest 登记的日志与 evidence | Guest/Host 原始记录 |

缺失的数据保持 unavailable，不使用公式生成样本、硬编码成功证据或旧运行结果填充页面。

## 仓库结构

```text
os/                AgentOS-uCore 内核
user/              AgentOS 用户库、专项程序和科研平台
baseline_ucore/    不含 AgentOS 子系统的共享安全基底对照
host_tools/        证据校验、统计和 Dashboard 生成
scripts/           构建、QEMU、回归与环境工具
ci/                版本化预算、实验和测试清单
docs/              设计、ABI、评价和竞赛材料
evidence/releases/ 已发布的可复验证据索引
results/           本机生成结果，不提交
```

长期调试后可先预览再清理 Git 忽略的构建产物：

```bash
make clean-workspace-dry-run
make clean-workspace
```

## 文档

| 内容 | 文档 |
| --- | --- |
| 竞赛说明与演示顺序 | [docs/contest/README.md](docs/contest/README.md) |
| 双目标职责 | [docs/dual-targets.md](docs/dual-targets.md) |
| 架构与机制 | [docs/agentos/design.md](docs/agentos/design.md) |
| 系统调用和 ABI | [docs/agentos/api.md](docs/agentos/api.md) |
| 安全与资源控制 | [docs/agentos/security-hardening.md](docs/agentos/security-hardening.md) |
| 要求追踪 | [docs/agentos/requirements-traceability.md](docs/agentos/requirements-traceability.md) |
| 评价方法 | [docs/evaluation.md](docs/evaluation.md) |
| 验证与证据边界 | [docs/verification.md](docs/verification.md) |
| Windows 环境 | [docs/windows-quickstart.md](docs/windows-quickstart.md) |

## 许可与交付

项目基于 uCore 扩展。源代码遵循 [GPL-3.0](LICENSE)，文档遵循 [CC-BY-SA-4.0](DOCUMENTATION_LICENSE.md)，第三方来源见 [NOTICE](NOTICE)。AIOS Evaluation 和 Linux 仅用于公开论文、文档和机制层面的干净室参考，未复制许可不明确的测试代码或数据。

远程 GitLab 只托管源码和已提交证据，不依赖 Runner。所有验收入口均可在本地复现，阶段提交同时推送到 `contest/final-2026` 与 `main`。
