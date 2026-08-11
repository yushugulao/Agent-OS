<p align="center">
  <img src="docs/agentos/assets/agentos_logo.png" alt="AgentOS-uCore" width="680">
</p>

# AgentOS-uCore

AgentOS-uCore 是面向 AI Agent workflow 的 RISC-V uCore 内核扩展。我们把可信身份、Context Path、结构化工具调用、实时文件查询、workflow 资源调度和可验证执行证据放入内核，并在 Guest 用户态运行实际 Agent workflow。

## 为什么需要 AgentOS

传统进程接口能够启动程序，却无法直接表达 Agent 的身份、上下文因果、工具权限和跨轮资源预算。多个 Agent 并发工作时，文件状态变化、工具副作用和调度服务量也缺少统一的内核边界。

我们围绕这组问题设计 AgentOS。内核负责可信状态、权限、资源和证据；Guest 用户态负责策略、业务与模型循环；可选 Host relay 只处理串口、TLS 和模型供应商协议。

<p align="center">
  <img src="docs/figures/architecture/agentos_overview.png" alt="AgentOS-uCore 总体架构" width="960">
</p>

## 我们交付了什么

| 问题 | 核心实现 | 可见结果 |
| --- | --- | --- |
| Agent 身份和上下文如何可信传递 | 可信 Agent 映像、7 页 Context 地址区、workflow `id + generation`、六类 provenance 标签 | 普通进程与 Agent 共存；Context 可查询、分支、回滚并保留因果关系 |
| 工具调用如何约束副作用 | 25 项工具目录、V2 typed RPC、ENFORCE V3 冻结执行合同、Tool Phase Credit Lease | schema、capability、前驱、来源和资源包络在副作用前统一检查 |
| 多轮 workflow 如何受控运行 | Workflow Credit Domain、workflow EEVDF、事件等待、heartbeat、可信 IPC | 资源按 workflow 记账；并发服务量和唤醒延迟可逐样本测量 |
| 文件状态如何实时进入 Agent Loop | 显式 metadata catalog、`status/stage/kind` 索引、typed watch、generation resync | 查询返回结构化结果；谓词变化产生 `ENTER/UPDATE/LEAVE` |
| 执行结果如何复核 | Fence-Sealed Evidence Ring、challenge-bound workflow fence、320 字节 receipt | 事件范围、gap、credit、metadata generation 和根链在同一 cut 中密封 |
| 产品如何形成完整 workflow | `labdemo_ucore` 综合场景、交互控制台、四业务 Agent Nexus、Guest-owned model loop | QEMU 中完成文件查询、工具调用、授权拒绝、委派、工件流转与结果汇总 |

## 目标完成情况

| 赛题任务 | 完成状态 | 代表实现与验证入口 |
| --- | :---: | --- |
| 任务一：Agent 创建、Context 区与进程共存 | 完成 | `agentfinal_ucore`、`agenttrust_ucore` |
| 任务二：不少于 3 个结构化工具及错误处理 | 完成 | `agenttoolabi_ucore`、`agentcontract_ucore` |
| 任务三：不少于 5 轮连续调用与 Context 管理 | 完成 | `agentfinal_ucore` |
| 任务四：文件扩展属性、结构化查询与性能比较 | 完成 | `agentfs_ucore`、`agentbench_ucore`、one-shot campaign |
| 任务五：Agent Loop、事件等待与多 Agent 调度 | 完成 | `agentloop_ucore`、`agent_eevdf_ucore` |
| 任务六：QEMU 综合应用与性能对比 | 完成 | `labdemo_ucore`、`make contest-demo` |

逐项源码、命令和判定标准见[要求追踪表](docs/agentos/requirements-traceability.md)。

## 实测结果

我们在源码提交 `2b14fb1f74b9bd093e6de939a16554620835699e` 上完成了 30 次 fresh QEMU boot，保留 33 个原始文件、19 张数据表和 7,498 行逐样本记录。

- 16/16 个 traversal/indexed 配对中，indexed workflow core interval 更短；该窗口包含 query、recovery write、`fsync` 和 verify，配对加速比中位数为 **3.118x**。相同配对的端到端中位差为 indexed **+13.452 ms**。
- `catalog size × hit count` 的 12 个实测参数格均包含 15 个 AB/BA 配对，单格中位加速比为 **1.164x–2.808x**。
- 16-op sequence 中位延迟为 batch **561 us**、scalar V3 **2051 us**、SQ/CQ **1620.5 us**。
- 504 条 EEVDF exact wake probe 全部落在 **0–1 tick**；1–4 workflow 的 Jain fairness 中位数均不低于 **0.99998**。

完整方法、统计口径、图表和原始数据入口见[实测性能结果](docs/contest/performance-results.md)。

## 三步复现

在 Linux、WSL 或已配置 RISC-V 工具链的环境中运行：

```bash
make doctor
make build
make contest-demo
```

`contest-demo` 启动真实 QEMU Guest，按 AB/BA 顺序运行 traversal/indexed 综合负载，并把当前机器的串口日志、CSV、JSON 和报告写入 `results/contest-demo/`。Windows 依赖与工具链配置见[快速开始](docs/windows-quickstart.md)。

## 文档入口

| 读者 | 建议入口 |
| --- | --- |
| 决赛完整产品文档 | [AgentOS-uCore 决赛文档 PDF](docs/final-report/AgentOS-uCore-final-report.pdf) |
| 评委 | [竞赛评审入口](docs/contest/README.md) → [系统设计](docs/agentos/design.md) → [现场演示](docs/agentos/scenario-script.md) |
| 使用者 | [Windows 快速开始](docs/windows-quickstart.md) → [交互控制台](docs/agentos/interactive-console.md) → [AgentOS Nexus](docs/agentos/nexus.md) |
| 开发者 | [文档导航](docs/README.md) → [ABI 参考](docs/agentos/api.md) → [验证说明](docs/verification.md) |
| 数据复核 | [性能结果](docs/contest/performance-results.md) → [高级图表](docs/agentos/advanced-performance-figures.md) → [one-shot 数据说明](one_shot_metrics/README.md) |

## 来源与许可

项目基于 LearningOS/uCore 教学内核开发。源码采用 [GPL-3.0](LICENSE)，文档采用 [CC BY-SA 4.0](DOCUMENTATION_LICENSE.md)。第三方来源、clean-room 增量和 AI 工具使用分别见[第三方及原创增量说明](docs/contest/third-party-and-originality.md)与 [AI 工具使用披露](docs/contest/ai-usage-disclosure.md)。
