<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# project61-agentOS-happylegend

## 项目信息

| 项目 | 内容 |
| --- | --- |
| 比赛 | 2026 年全国大学生计算机系统能力大赛-操作系统设计赛（全国）-OS 功能挑战赛道 |
| 选题编号 | project61 |
| 赛题名称 | 面向 AI 智能体的操作系统内核（Agent-OS） |
| 队伍名称 | happy-legend |
| 平台 Project ID | 39809 |
| GitLab 仓库 | https://gitlab.eduxiji.net/T2026106149911107/project3136859-388870 |

## 项目简介

本项目围绕 Agent-OS 赛题，探索让操作系统内核感知、管理和支持 AI Agent 的机制。项目目标是在教学操作系统内核中实现 Agent 进程、结构化内核交互、上下文路径管理等能力，并形成可在 QEMU 中运行和演示的系统。

文档体系参考操作系统内核项目和软件架构文档惯例重构：README 负责快速运行，主设计文档解释架构和关键决策，API/ABI 文档说明用户态与内核接口，验证文档给出可复现证据。

## 基底来源

本项目当前以 MIT PDOS 的 [xv6-riscv](https://github.com/mit-pdos/xv6-riscv) 作为教学操作系统基底。仓库中的 `kernel/`、`user/`、`mkfs/`、`Makefile` 等基础代码来自 xv6-riscv，后续将在此基础上实现 Agent-OS 相关能力。

原始 xv6-riscv 说明和许可文件已保留：

- [THIRD_PARTY/xv6-riscv-README](THIRD_PARTY/xv6-riscv-README)
- [THIRD_PARTY/xv6-riscv-LICENSE](THIRD_PARTY/xv6-riscv-LICENSE)

## 赛题对应关系

| 赛题任务 | 项目目标 | 当前状态 |
| --- | --- | --- |
| 任务一：Agent 进程创建与地址空间设计 | 支持 Agent 进程概念和上下文空间 | 已完成增强实现 |
| 任务二：Agent 与内核结构化交互 | 支持结构化工具调用和结果返回 | 已完成增强实现 |
| 任务三：上下文路径管理 | 记录并查询 128 条短文本摘要历史 | 已完成增强实现 |
| 任务四：Agent 子系统内核元数据表版本的文件查询扩展 | 支持按 fid、项目、运行、阶段、状态、类型、摘要查询文件元数据，支持插入、删除和扫描/索引对比 | 原型能力已完成；不声明为真实 xv6 inode 扩展 |
| 任务五：Agent Loop 内核运行机制 | 支持 watch/unwatch、wait、heartbeat/heartbeat_stop、event delivery/timeout，文件状态和 mailbox 可唤醒 Agent | 原型能力已完成 |
| 任务六：综合演示与创新 | 用夜间实验批量复测故障诊断与受控恢复场景串联任务一至五，并提供宿主机 LLM Gateway、replay 大屏和 live QEMU 串接 | 已完成可验证版本；演示视频/幻灯片待补 |

## 构建与运行

已验证开发环境：WSL2 Ubuntu 26.04。

通用运行要求：Linux 环境，安装 RISC-V GCC/binutils、QEMU riscv64、make、git。使用 `make qemu` 构建和运行。

```bash
cd project61-agentOS-happylegend
make qemu
```

进入 xv6 shell 后运行最终功能验收程序：

```sh
labdemo
labbench
agentfinal
```

`labdemo` 是当前任务四、任务五和综合场景主入口。普通父进程只引导创建 Orchestrator，Recovery、Investigator、Sentinel 由 Orchestrator 创建。Orchestrator 初始化元数据并注入 `RUN-042` 的 `align` 阶段失败，展示文件属性查询、事件唤醒、Agent 间消息、权限拒绝、幂等恢复和单个恢复报告工件元数据更新。同一 QEMU 会话中连续运行两次 `labdemo` 均应输出 `labdemo: passed`。

`labbench` 是任务四、任务五性能和可靠性入口。它输出文件扫描查询 vs 属性索引查询、`fid` 查询、元数据插入/删除、selector 限定报告更新、轮询查询基线、event wait/wake 路径、scalar tool call vs batch `agent_run`、`context_query` vs `context_snapshot`、capability check、duplicate reject，以及角色自升权拒绝、`heartbeat_stop`、`unwatch`、依赖掩码驱动恢复、短事件 payload 回查、事件队列满、mailbox 满队列、文件状态满队列的测试结果。

`agentfinal` 继续作为任务一至三高性能底座复测。预期输出包括 4 页 Agent Context、64 路批量工具调用、Context Snapshot、短文本历史记录、用户镜像篡改防护范围、128 条容量 FIFO 淘汰和直接 Context 一致性检查：

```text
agentfinal: context size=16384 capacity=128
agentfinal: batch first_seq=1 last_seq=64
agentfinal: short_text_history=1 payload=final result=final
agentfinal: snapshot count=64 latest=64
agentfinal: direct_dirty_before_snapshot=1
agentfinal: tamper_protected=1
agentfinal: fifo oldest=65 latest=192 dropped=64
agentfinal: direct_context_match=1
agentfinal: passed
```

进入 xv6 shell 后运行最终性能验收程序：

```sh
agentbench
```

输出包括 scalar run、batch run、direct Context、context query 和 context snapshot 的吞吐对比。下列数值是一次样例输出，`ticks` 会随宿主机和 QEMU 状态波动：

```text
agentbench: case ops ticks ops_per_tick speedup_x100
agentbench: scalar_run ops=65536 ticks=20 ops_per_tick=3276 speedup_x100=100
agentbench: batch_run ops=65536 ticks=3 ops_per_tick=21845 speedup_x100=666
agentbench: direct_context ops=1000000 ticks=0 ops_per_tick=1000000 speedup_x100=30517
agentbench: context_query ops=2048 ticks=0 ops_per_tick=2048 speedup_x100=62
agentbench: context_snapshot ops=262144 ticks=3 ops_per_tick=87381 speedup_x100=4266
agentbench: latest_sequence=131072 dropped=130944 capacity=128
agentbench: passed
```

完整复测还可以运行以下辅助测试。`agentexec` 可直接从 shell 运行，也可作为 Agent `exec("agentexec")` 的目标程序：

```sh
agentexec
agentcall
contexttest
agentstress
```

进入 xv6 shell 后会看到 `$` 提示符。退出 QEMU：

```text
Ctrl-a x
```

如需清理构建产物：

```bash
make clean
```

宿主机 LLM Gateway 和 replay 大屏使用 Node + Vite。首次运行先安装依赖：

```bash
npm install
```

无 QEMU 回放验证：

```bash
npm run host:test
npm run host:replay
npm run host:dashboard:build
```

启动宿主机 replay 大屏：

```bash
npm run host:dev
```

默认页面地址为 `http://127.0.0.1:5173`，Gateway 地址为 `http://127.0.0.1:8787`。页面会读取 fixture replay，展示 4 个 Agent、事件时间线、`LLM_ANALYSIS`、恢复报告和 `BENCH` 指标。真实云端 LLM 可复制 `.env.example` 为 `.env` 后配置 OpenAI-compatible API；没有 key 或网络失败时自动使用 fallback。

启动 live QEMU 大屏：

```bash
npm run host:live
```

Windows PowerShell 下该命令默认通过 WSL 执行 `make qemu`，自动在 xv6 shell 中运行 `labdemo` 和 `labbench`，实时解析串口中的 `agentos:event` 并推送到同一个大屏。若只想快速验证综合恢复主线，可运行：

```powershell
$env:HOST_LIVE_RUN_BENCH='0'
npm run host:live
```

关键通过输出为：

```text
host:live done status=done events=... final=RECOVERED llm=fallback
```

当前交付材料包括：

- 内核代码；
- 用户态测试程序；
- QEMU 运行和演示说明；
- 测试记录与结果分析；
- 任务一至五演示材料；
- `labdemo` 综合演示入口；
- `labbench` 性能演示入口；
- 宿主机事件解析器；
- OpenAI-compatible LLM Gateway 和 fallback；
- Node + Vite replay/live 可视化大屏；
- live QEMU 串口事件接入。

仍需补充：

- 进展汇报幻灯片；
- 作品演示视频。

## 文档与演示

| 材料 | 位置 |
| --- | --- |
| 文档索引 | [docs/README.md](docs/README.md) |
| 文档标准采用说明 | [docs/documentation-standard.md](docs/documentation-standard.md) |
| 主设计文档 | [docs/design.md](docs/design.md) |
| 赛题要求追踪表 | [docs/requirements-traceability.md](docs/requirements-traceability.md) |
| API 与 ABI | [docs/api.md](docs/api.md) |
| 验证与性能评估 | [docs/verification.md](docs/verification.md) |
| 测试内容详细说明 | [docs/testing-details.md](docs/testing-details.md) |
| 演示脚本 | [docs/demo-script.md](docs/demo-script.md) |
| xv6 文件系统内说明 | [README](README) |
| xv6-riscv 原始说明 | [THIRD_PARTY/xv6-riscv-README](THIRD_PARTY/xv6-riscv-README) |
| 任务一细节附录 | [docs/task1-agent-process.md](docs/task1-agent-process.md) |
| 任务二细节附录 | [docs/task2-agent-call.md](docs/task2-agent-call.md) |
| 任务三细节附录 | [docs/task3-context-path.md](docs/task3-context-path.md) |
| 任务四细节附录 | [docs/task4-file-query.md](docs/task4-file-query.md) |
| 任务五细节附录 | [docs/task5-agent-loop.md](docs/task5-agent-loop.md) |
| 任务六细节附录 | [docs/task6-llm-dashboard.md](docs/task6-llm-dashboard.md) |
| 当前测试记录 | [docs/test-record.md](docs/test-record.md) |
| 源代码许可 | [LICENSE](LICENSE) |
| 文档与答辩材料许可 | [DOCUMENTATION_LICENSE.md](DOCUMENTATION_LICENSE.md) |
| 第三方声明 | [NOTICE](NOTICE) |
| 进展汇报幻灯片 | 待补充 |
| 演示视频 | 待补充 |

## 许可声明

本仓库作为参赛作品提交的源代码采用 [Apache License 2.0](LICENSE) 许可，满足赛事对源代码开源协议的要求。

xv6-riscv 基底代码和原始说明保留其上游版权与许可声明，详见 [THIRD_PARTY/xv6-riscv-LICENSE](THIRD_PARTY/xv6-riscv-LICENSE) 和 [THIRD_PARTY/xv6-riscv-README](THIRD_PARTY/xv6-riscv-README)。这些第三方声明是上游代码合规要求的一部分，不应删除。

本队伍原创的技术文档、答辩材料、汇报幻灯片和演示视频采用 [Creative Commons Attribution-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-sa/4.0/) 许可，详见 [DOCUMENTATION_LICENSE.md](DOCUMENTATION_LICENSE.md)。

如后续引用、复制或改编非本队伍来源的代码、文档或公开项目内容，将在对应源码位置、文档位置、设计文档和答辩材料中明确标注来源、用途、授权信息及本项目的增量贡献。对 xv6-riscv 的改动也会在设计文档和答辩材料中说明。
