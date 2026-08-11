# AI 工具使用披露

我们在开发过程中使用 OpenAI Codex 辅助阅读仓库、讨论方案、编辑代码与文档、运行测试和复核变更。参赛队负责架构选择、代码取舍、实验设计、许可证核验和最终陈述。

## 开发阶段如何使用

| 工作 | Codex 的作用 | 进入仓库前的检查 |
| --- | --- | --- |
| 代码分析与修改 | 定位调用链、提出实现方案、编辑源码和测试 | 查看 diff，编译目标，运行对应 Host checker 与 QEMU Guest |
| 测试与数据管线 | 参数化负载、保存逐样本数据、提取和验证 CSV | 核对原始串口日志、来源 SHA-256、配对关系和统计口径 |
| 文档与 LaTeX | 重组章节、统一术语、生成表格与排版代码 | 对照生产源码、赛题要求和实测结果逐项审阅 |
| 图表与架构图 | 编写绘图程序、组织 draw.io 图和导出文件 | 数据图只读取 campaign CSV；架构图逐节点核对实现边界 |

AI 生成的文本或代码不会自动成为项目结论。变更只有进入 Git diff 并通过与人工编写内容相同的工程检查后，才纳入项目。

## 性能数据如何产生

性能样本来自真实 QEMU Guest。one-shot campaign 共运行 30 次 fresh boot，保存原始串口日志、命令、环境、Guest 镜像哈希和逐样本表格。

Codex 协助实现采集、提取、验证和绘图脚本，没有生成或补写测量样本。图表不以插值填补缺失单元；完整证据见 [manifest](../../one_shot_metrics/data/20260811/manifest.json)、[validation](../../one_shot_metrics/data/20260811/validation.json)和[性能结果](performance-results.md)。

## 产品运行时边界

默认竞赛演示 `make contest-demo` 使用 Guest 内的 deterministic policy workflow，可在无网络和无 API key 的环境中运行。它验证 AgentOS 内核机制和完整业务路径。

可选 `agentlive` 模式允许模型参与 tool loop。prompt、history、tool catalog、工具选择、参数校验、执行和结果回灌由 Guest 用户态管理；Host relay 负责串口 frame、TLS 和供应商 JSON 转换。内核不保存云端密钥，也不调用模型 HTTP API。

offline replay 经过相同的 Guest/Host wire 和状态机，用于稳定回归。只有真实 provider 命令成功完成时，我们才把结果称为云模型实测。

## 数据与密钥

- API key、访问令牌、会话文本和本机私有配置不进入仓库。
- 模型回复不作为代码正确性、赛题完成度或性能提升的证据。
- 正确性证据来自编译、Host checker、真实 QEMU Guest 行为和拒绝路径。
- 性能结论来自可追溯的 raw log、逐样本 CSV、验证报告和统计程序。
- 新增 AI 生成的代码、文字或视觉材料时，我们继续执行来源、许可和内容审查。
