# 文档索引

本目录是项目文档阅读入口。README 负责基本信息、项目简介、完成情况和快速运行；设计文档负责架构图、模块职责和关键决策；API/ABI 文档负责说明用户态与内核的接口分工；验证文档负责用测试场景、输出标记和性能观测说明项目是否可复现。

如果是第一次阅读，建议先从仓库根目录 README 理解科研 Agent 平台、普通 uCore 对照目标和 AgentOS-uCore 增强目标之间的关系，再进入本目录查看内核机制细节。

## 推荐阅读顺序

| 顺序 | 文档 | 用途 |
| ---: | --- | --- |
| 1 | [../../README.md](../../README.md) | 项目概览、构建运行、当前完成状态 |
| 2 | [design.md](design.md) | 主设计文档：架构、模块、运行视图、关键决策、当前范围和取舍 |
| 3 | [requirements-traceability.md](requirements-traceability.md) | 赛题要求到实现位置、测试证据和文档材料的对应表 |
| 4 | [api.md](api.md) | 系统调用、Agent ABI、工具协议和错误语义 |
| 5 | [security-hardening.md](security-hardening.md) | 安全威胁、可信执行、文件安全域、生命周期和资源韧性设计 |
| 6 | [verification.md](verification.md) | 验证计划、测试场景、测试覆盖表、性能数据摘要 |
| 7 | [testing-details.md](testing-details.md) | Agent 功能、可信映像、VFS、安全约束、资源耗尽和进程生命周期测试的逐项说明 |
| 8 | [scenario-script.md](scenario-script.md) | 综合场景运行脚本 |

## 详细附录

| 文档 | 定位 |
| --- | --- |
| [security-hardening.md](security-hardening.md) | 全部安全修复的威胁模型、机制总表、双目标分工、可信程序注册和安全专项测试入口 |
| [task1-agent-process.md](task1-agent-process.md) | 任务一 Agent 进程与地址空间设计细节 |
| [task2-agent-call.md](task2-agent-call.md) | 任务二结构化工具调用设计细节 |
| [task3-context-path.md](task3-context-path.md) | 任务三 Context Path、运行轨迹、cause/span 因果字段、用户自管 cache、统一 timeline 导出、timeline 过滤查询、timeline 等待、wait-and-read、游标增量读取和 provenance edge 设计细节 |
| [task4-file-query.md](task4-file-query.md) | 任务四文件属性查询、真实 inode 关联、私有 `.agentmeta` 元数据文件、根目录自动扫描、索引、查询计划、内容摘要、依赖查询、本地预取提示和 span 预取提示设计细节 |
| [task5-agent-loop.md](task5-agent-loop.md) | 任务五 watch/unwatch、FIFO 事件队列、wait/timeout 睡眠、事件因果继承、heartbeat、Agent 感知调度、受权调度配置、调度原因记录、运行轨迹、当前 span 短记录、全局审计短记录、过滤查询、统一 timeline、timeline 过滤查询、timeline 等待、wait-and-read、游标增量读取和 provenance edge 设计细节 |
| [test-record.md](test-record.md) | 测试记录和关键输出 |
| [assets/agentos_arch.svg](assets/agentos_arch.svg) | 用户态/内核态总架构图 |
| [assets/agentos_telemetry_pipeline.svg](assets/agentos_telemetry_pipeline.svg) | 内核记录到平台页面的数据路径图 |
| [assets/agentos_test_evidence.svg](assets/agentos_test_evidence.svg) | 测试证据组织图 |
| [../../LICENSE](../../LICENSE) | 源代码 GPL-3.0 |
| [../../DOCUMENTATION_LICENSE.md](../../DOCUMENTATION_LICENSE.md) | 文档与结果材料 CC BY-SA 4.0 |
| [../../NOTICE](../../NOTICE) | 第三方来源和许可声明 |

## 文档维护约定

- 主设计事实以 [design.md](design.md) 为准，分任务文档只展开实现细节。
- 用户态/内核态接口分工和结构体布局以 [api.md](api.md) 为准。
- 通用安全修复与 AgentOS 专属安全机制的分工以 [security-hardening.md](security-hardening.md) 为准。
- 赛题完成度判断以 [requirements-traceability.md](requirements-traceability.md) 和 [verification.md](verification.md) 共同为准。
- 新增功能需要同步更新对应设计说明、API/ABI、验证记录和示例脚本。
- `test-record.md` 保留测试输出摘要，不替代验证结论。
