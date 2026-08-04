# AgentOS-uCore 竞赛评审入口

AgentOS-uCore 面向长时间、多角色的 AI Agent 工作流，在 RISC-V 64 uCore 中提供结构化工具调用、可信 Context、语义文件查询、事件驱动 Agent Loop、workflow 生命周期和统一资源控制。

根目录是 AgentOS-uCore；`baseline_ucore/` 是不含 AgentOS 服务的共享安全基底对照。两侧运行同一科研 Agent 工作流，用于区分用户态实现与内核原生机制的行为和成本。

## 核心能力

| 赛题任务 | 系统能力 | 演示观察点 |
| --- | --- | --- |
| 任务一 | Agent 身份、角色、Context 映射和生命周期 | 普通进程与 Agent 共存，Context 可直接读取 |
| 任务二 | 名称协议、typed KV、工具目录和批处理 | 结构化请求、响应、错误和扩展字段 |
| 任务三 | Context Path、快照、FIFO 淘汰和 rollback | 连续工具调用、分支回溯和可信历史 |
| 任务四 | inode 绑定 metadata、摘要、索引和租约 | 路径遍历与语义索引实测对照 |
| 任务五 | watch/wait、heartbeat、IPC 和公平调度 | 无事件休眠、事件唤醒和多 Agent 协作 |
| 任务六 | 科研 Agent 检索、分析、恢复、写作和审计流程 | 双目标完整场景、结果一致性和阶段耗时 |

[赛题要求追踪表](../agentos/requirements-traceability.md)列出每项要求对应的实现、ABI 和动态测试入口。

## 实测数据

[正式证据索引](../../evidence/releases/INDEX.md)是评审数据入口。索引存在 release 记录时，请打开最新 bundle 内的 `dashboard/index.html`；索引为空表示正式数据尚未发布，不应引用开发日志或历史结果。正式 Dashboard 展示实际样本，而不是测试状态汇总：

- 路径遍历与 metadata 索引的各负载耗时、工作量和样本数；
- metadata 全表扫描消融、工具批处理和 Context 映射读取；
- Task 6 双目标 p50/p95、逐阶段耗时、结果一致性和样本数；
- 两个内核的 ELF、text/data/BSS，以及 `struct proc` 和栈预算。

每个数值均可从 Dashboard 回到 bundle 中登记的 Guest/Host 原始材料、源码提交和执行环境。统计方法与比较边界见[评价方法](../evaluation.md)。

## 三分钟演示

在已安装 Bash、Python 3.10+、RISC-V GCC/binutils 和 `qemu-system-riscv64` 的 POSIX 环境中：

```bash
make doctor
make contest-demo TOOLPREFIX=riscv64-linux-gnu-
make contest-demo-check
```

演示包含任务一至五的结构化功能路径和短版多 Agent 科研恢复场景，不连接云 API。完整科研工作流的讲解顺序见[演示脚本](../agentos/scenario-script.md)。

## 完整复现

```bash
make target-readiness
make ci-check
AGENT_TEST_DURATION_PROFILE=none make full-verify TOOLPREFIX=riscv64-linux-gnu-
```

正式评价的采集、复验、成本测量、Dashboard 和打包入口见[评价方法](../evaluation.md)；Windows、WSL 和 MSYS2 配置见[Windows 快速开始](../windows-quickstart.md)。同一工作树内不要并发运行多个 QEMU 测试。

## 建议审阅顺序

| 时间 | 内容 | 入口 |
| --- | --- | --- |
| 3 分钟 | 项目定位与任务一至六 | [根 README](../../README.md)、[要求追踪表](../agentos/requirements-traceability.md) |
| 8 分钟 | 架构、内核边界与机制/策略分离 | [系统设计](../agentos/design.md)、[系统调用与 ABI](../agentos/api.md) |
| 5 分钟 | 科研 Agent 综合场景 | [演示脚本](../agentos/scenario-script.md) |
| 8 分钟 | 实验设计、原始材料和统计口径 | [正式证据索引](../../evidence/releases/INDEX.md)、[评价方法](../evaluation.md) |

## 项目材料

- [视频和幻灯片](../../项目介绍视频和ppt网盘链接.txt)
- [第三方来源与原创增量](third-party-and-originality.md)
- [AI 工具使用披露](ai-usage-disclosure.md)
- [源码许可证](../../LICENSE)、[文档许可证](../../DOCUMENTATION_LICENSE.md)、[第三方通知](../../NOTICE)
