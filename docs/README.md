# AgentOS-uCore 文档导航

这套文档按评委、使用者和开发者三条路径组织。每条路径从可运行入口开始，再进入设计与证据。

## 评委：先看产品和结果

1. [根 README](../README.md)：一分钟了解定位、成果、赛题完成度与复现命令。
2. [竞赛评审入口](contest/README.md)：按六项任务查看实现、Guest 程序和演示顺序。
3. [决赛产品文档 PDF](final-report/AgentOS-uCore-final-report.pdf)：完整架构、功能设计、实现与实验报告。
4. [实测性能结果](contest/performance-results.md)：30 次 QEMU boot 的方法、统计、原始数据和关键结论。
5. [现场演示脚本](agentos/scenario-script.md)：15 分钟主演示与观察点。
6. [要求追踪表](agentos/requirements-traceability.md)：题面要求到源码、命令和通过条件的映射。

原创增量、外部来源与开发工具披露见[第三方及原创增量说明](contest/third-party-and-originality.md)和 [AI 工具使用披露](contest/ai-usage-disclosure.md)。

## 使用者：把系统运行起来

1. [Windows 快速开始](windows-quickstart.md)：安装 WSL、QEMU 与 RISC-V 工具链。
2. [验证说明](verification.md)：构建内核、运行专项 Guest 和综合测试。
3. [交互控制台](agentos/interactive-console.md)：启动长驻 Guest session 和 observer。
4. [AgentOS Nexus](agentos/nexus.md)：运行 Coordinator、System、Research、Analyst 四业务 Agent。
5. [双目标说明](dual-targets.md)：比较 plain uCore 与 AgentOS-uCore。

最短路径为：

```bash
make doctor
make build
make contest-demo
```

## 开发者：理解并修改系统

1. [系统设计](agentos/design.md)：可信边界、核心数据结构与跨模块不变量。
2. [ABI 参考](agentos/api.md)：系统调用、结构体、错误码与兼容规则。
3. [执行合同](agentos/task6-execution-contract.md)：V2 探索式调用、ENFORCE V3 和 Task Channel。
4. [安全加固](agentos/security-hardening.md)：generation、scope、resync 与 fail-closed 路径。
5. [验证矩阵](agentos/verification.md)：Guest/Host 测试与代表 marker。
6. [仓库验证入口](verification.md)：构建、静态合同、QEMU 和性能回归命令。

开发时先确认设计边界，再修改 ABI 或实现，最后运行对应 Host checker 与真实 Guest 测试。

## 数据与图表

- [高级性能图表说明](agentos/advanced-performance-figures.md)
- [one-shot campaign 说明](../one_shot_metrics/README.md)
- [campaign manifest](../one_shot_metrics/data/20260811/manifest.json)
- [数据就绪验证](../one_shot_metrics/data/20260811/validation.json)
- [LaTeX 产品文档入口](final-report/main.tex)

数据表保留逐样本来源、来源 SHA-256 和实验标识。图表同时提供 PNG 与 PDF，正文引用 PDF，Markdown 使用 PNG。
