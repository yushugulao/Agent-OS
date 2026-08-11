# AgentOS 文档索引

AgentOS-uCore 在 uCore 上实现可信 Agent 身份、结构化工具调用、Context Path、文件属性查询、事件循环和可验证工作流。本文只做导航；接口字段以 [api.md](api.md) 和公开 ABI 为准，运行结论以 QEMU 日志与保存的原始数据为准。

![AgentOS-uCore 总体架构](../figures/architecture/agentos_overview.png)

## 评委阅读路径

| 顺序 | 文档 | 解决的问题 |
| ---: | --- | --- |
| 1 | [../../README.md](../../README.md) | 项目价值、完成情况与最短运行入口 |
| 2 | [requirements-traceability.md](requirements-traceability.md) | 六项赛题目标如何落到源码、验证和量化结果 |
| 3 | [design.md](design.md) | 身份、资源、Context、证据和生命周期的总体设计 |
| 4 | [api.md](api.md) | 当前 UAPI、状态码、调用顺序和 ABI 边界 |
| 5 | [security-hardening.md](security-hardening.md) | 威胁模型、授权检查和 fail-closed 路径 |
| 6 | [verification.md](verification.md) | 静态检查、交叉构建、QEMU Guest 与结果解释 |
| 7 | [scenario-script.md](scenario-script.md) | 决赛现场的主演示流程 |
| 8 | [advanced-performance-figures.md](advanced-performance-figures.md) | 一次性逐样本数据、统计口径和十组性能图 |

## 六项任务

| 任务 | 工程主线 | 专题文档 |
| --- | --- | --- |
| 1 | 受控创建可信 Agent，并把身份、地址空间、workflow 与资源账户绑定 | [Agent 进程与 workflow 域](task1-agent-process.md) |
| 2 | 用版本化 schema 接收工具调用，在授权后执行并返回结构化状态 | [结构化工具调用](task2-agent-call.md) |
| 3 | 保存多轮因果历史，支持一致读取、分支和有界回滚 | [Context Path](task3-context-path.md) |
| 4 | 为显式登记文件提供属性索引、结构化查询和实时集合变化 | [Agent Live-Query FS](task4-file-query.md) |
| 5 | 通过 watch/wait、IPC、heartbeat、workflow 调度和证据封存驱动长驻循环 | [Agent Loop](task5-agent-loop.md) |
| 6 | 以确定性科研工作流完成综合验收，并提供执行合同、Task Channel 和可选模型循环 | [综合工作流与执行合同](task6-execution-contract.md) |

## 系统主线

1. 内核从可信映像和父身份建立 Agent，把 role、capability、scope 与完整 lifecycle generation 写入进程身份。
2. Guest 通过 V1/V2/V3 或 compact batch 提交结构化工具调用；内核检查 schema、权限、scope、资源和可选执行合同。
3. 工具结果进入 Context Path，内核维护 sequence、cause、span、branch 与来源标签。
4. 显式文件 metadata 进入当前启动周期的 catalog；typed watch 把 `ENTER/UPDATE/LEAVE` 送入事件队列。
5. 用户态 policy 或 model loop 在无事件时休眠，收到事件后选择下一次调用。内核负责等待、路由、调度和边界验证。
6. Context 事件进入 Evidence Ring；controller 在 workflow fence 处取得 challenge-bound receipt、精确资源摘要和 metadata generation。

## 产品与运行入口

| 入口 | 用途 |
| --- | --- |
| `make contest-demo` | 构建并运行确定性主演示，生成配对测量 |
| `make dual-platform-run` | 对照 plain uCore 与 AgentOS-uCore 的同一科研合同 |
| `make agentos-console` | 启动可自由输入的长驻 Guest model loop |
| `make agentos-nexus-replay` | 运行四业务 Agent 的固定 replay 验收 |
| `make agent-module-check` | 检查 UAPI、模块边界、live query 与 workflow fence |

交互控制面见 [interactive-console.md](interactive-console.md)，四 Agent 产品场景见 [nexus.md](nexus.md)。Windows/WSL 环境和完整验证顺序分别见 [Windows 快速开始](../windows-quickstart.md) 与 [顶层验证说明](../verification.md)。

## 当前边界

- Guest 内核为单 Hart；Host 同时运行多台 QEMU 只提高测试吞吐。
- Context 固定映射 7 页，其中 6 页由内核发布且用户只读，第 7 页是用户 cache。
- 文件 metadata、Context evidence 和合同状态属于当前启动周期的有界内存状态。
- workflow EEVDF 最多跟踪 4 个活跃实体；测量 Guest 中 bootstrap 已占 1 个实体。
- Task Channel 当前 provider 同步处理 null input，并返回 artifact `NONE`；typed payload backend 尚未开放。
- Host relay 只处理串口、TLS、API key 与 provider JSON。默认 replay 验证协议和循环，live provider 需要显式选择。
- MCP/A2A 代码只覆盖用户态对象与状态机映射，不包含远程 server、streaming 或内核 SQ/CQ adapter。

## 维护规则

- 修改 ABI、模块边界或运行结论时，同步更新本索引、六项任务文档、追踪矩阵和验证说明。
- 性能数字必须链接到明确负载、样本数、单位和原始数据。公开思想与 clean-room 边界见 [../../NOTICE](../../NOTICE)。
