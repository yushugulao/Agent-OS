# 竞赛评审入口

AgentOS-uCore 为 Agent workflow 提供内核级身份、Context、工具合同、实时文件状态、资源调度和执行证据。我们用真实 QEMU Guest 展示六项赛题任务，并保留逐样本性能数据供复核。

## 先看四项证据

| 内容 | 入口 | 评审重点 |
| --- | --- | --- |
| 完整产品文档 | [决赛文档 PDF](../final-report/AgentOS-uCore-final-report.pdf) | 架构、实现、实验和工程完整性 |
| 综合运行 | `make contest-demo` | 每个隔离 Guest workflow 中的身份、Context、文件查询、事件、工具和拒绝路径 |
| 实测数据 | [性能结果](performance-results.md) | 30 次 QEMU boot、7,498 行逐样本数据、统计口径和原始来源 |
| 要求追踪 | [要求追踪表](../agentos/requirements-traceability.md) | 每项题面要求对应的源码、命令和通过条件 |

## 六项任务如何落地

| 任务 | 赛题问题 | 我们的实现 | 代表证据 |
| --- | --- | --- | --- |
| 一 | Agent 如何拥有可信身份和独立 Context | role/capability、7 页 Context 地址区、workflow `id + generation`、进程生命周期集成 | `agentfinal_ucore`、`agenttrust_ucore`、`agentscope_ucore` |
| 二 | 工具如何结构化调用并在失败时收口 | 25 项工具目录、typed V2、ENFORCE V3、顺序 compact batch、稳定错误码 | `agenttoolabi_ucore`、`agentcontract_ucore` |
| 三 | 多轮调用如何保留因果并控制窗口 | cause/span/branch、查询、快照、rollback、只读 mirror、有界 FIFO | `agentfinal_ucore` |
| 四 | 文件属性如何查询并随状态变化更新 | 显式 metadata catalog、`status/stage/kind` 索引、typed watch、generation resync | `agentfs_ucore`、`agentbench_ucore` |
| 五 | Agent Loop 如何等待、通信和公平获得服务 | event/watch/wait、heartbeat、可信 IPC/LLM correlation、workflow EEVDF | `agentloop_ucore`、`agentsched_ucore`、`agent_eevdf_ucore` |
| 六 | 多项机制如何组成可运行产品 | `labdemo_ucore` 综合场景、交互控制台、Nexus 四业务 Agent、workflow fence | `make contest-demo`、`make dual-platform-run`、`make agentos-console-replay`、`make agentos-nexus-replay` |

## 三个核心问题

### 1. 内核如何认识 Agent

我们把 Agent 身份、角色、能力和 workflow generation 纳入进程生命周期。内核发布的 Context 页由用户态只读映射，用户 cache 使用独立页面，授权判断只依赖可信区域。

Context 记录携带 cause、span、branch 和 provenance。工具结果、文件事件与 IPC 因此能够进入同一条可追踪链路。

### 2. 工具副作用和资源如何受控

ENFORCE V3 在 workflow generation 内冻结 DAG。节点、schema、前驱、capability、provenance、effect、deadline 和 resource envelope 在副作用前完成检查。

Workflow Credit Domain 以 `used/pending/free` 管理 exec/storage credit。Evidence Ring 记录有序事件，workflow fence 将 challenge、事件范围、gap、credit digest 和 metadata generation 密封到 receipt。

### 3. Agent 如何看到变化并获得公平服务

Agent Live-Query FS 只接收显式登记的 metadata，并为常用 Agent 字段建立选择性索引。谓词变化直接产生 `ENTER/UPDATE/LEAVE`；增量丢失时使用 generation resync 恢复一致视图。

workflow EEVDF 以资源域作为公平实体，按实际 service cycles 记账。事件等待与 heartbeat 让 Agent 在无任务时休眠，在状态变化后及时恢复。

## 15 分钟演示顺序

| 时间 | 演示内容 | 观察点 |
| --- | --- | --- |
| 0–3 分钟 | 阅读 README 与总体架构图 | 内核、Guest 与 Host 的职责边界 |
| 3–8 分钟 | 运行 `make contest-demo` | 可信身份、Context、Live Query、工具、拒绝路径与 evidence |
| 8–12 分钟 | 展示配对图、Task 分布和 EEVDF 图 | 逐样本、配对、参数网格和统计单位 |
| 12–15 分钟 | 展示 capability 拒绝和 core/end-to-end 对照 | 副作用前拒绝、结果等价与计时窗口边界 |

完整命令、预期 marker 和讲解顺序见[现场演示脚本](../agentos/scenario-script.md)。

## 性能证据链

one-shot campaign 已完成并锁定，不进入默认测试。证据按以下顺序组织：

1. [manifest](../../one_shot_metrics/data/20260811/manifest.json)：源码提交、环境、命令、文件哈希和 30 次 boot 组成。
2. [raw](../../one_shot_metrics/data/20260811/raw/)：串口日志与 contest 原始测量。
3. [tables](../../one_shot_metrics/data/20260811/tables/)：19 张逐样本和派生数据表。
4. [validation](../../one_shot_metrics/data/20260811/validation.json)：schema、配对、参数网格和图表就绪检查。
5. [高级图表](../agentos/advanced-performance-figures.md)：10 组 PNG/PDF 图及读图说明。

## 继续阅读

- [系统设计](../agentos/design.md)
- [ABI 参考](../agentos/api.md)
- [验证说明](../verification.md)
- [Windows 快速开始](../windows-quickstart.md)
- [第三方及原创增量说明](third-party-and-originality.md)
- [AI 工具使用披露](ai-usage-disclosure.md)
