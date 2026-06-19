# 文档索引

本目录是评审阅读入口。文档结构参考操作系统和软件架构领域常见做法：README 负责快速运行，设计文档负责解释架构和关键决策，API/ABI 文档负责说明用户态与内核的接口分工，验证文档负责给出可复现证据。

## 推荐阅读顺序

| 顺序 | 文档 | 用途 |
| ---: | --- | --- |
| 1 | [../README.md](../README.md) | 项目概览、构建运行、当前完成状态 |
| 2 | [documentation-standard.md](documentation-standard.md) | 本项目采用的文档标准和重构原则 |
| 3 | [design.md](design.md) | 主设计文档：架构、模块、运行视图、决策、风险 |
| 4 | [requirements-traceability.md](requirements-traceability.md) | 赛题要求到实现位置、测试证据和文档材料的对应表 |
| 5 | [api.md](api.md) | 系统调用、Agent ABI、工具协议和错误语义 |
| 6 | [verification.md](verification.md) | 验证计划、测试覆盖表、性能数据摘要 |
| 7 | [testing-details.md](testing-details.md) | `agentfinal_ucore`、`agentfs_ucore`、`agentloop_ucore`、`agentbench_ucore`、`labbench_ucore`、`labdemo_ucore` 和 `agentsecurity_ucore` 的逐项测试说明 |
| 8 | [demo-script.md](demo-script.md) | 评审现场或视频演示脚本 |

## 详细附录

| 文档 | 定位 |
| --- | --- |
| [task1-agent-process.md](task1-agent-process.md) | 任务一 Agent 进程与地址空间设计细节 |
| [task2-agent-call.md](task2-agent-call.md) | 任务二结构化工具调用设计细节 |
| [task3-context-path.md](task3-context-path.md) | 任务三 Context Path 设计细节 |
| [task4-file-query.md](task4-file-query.md) | 任务四文件属性查询、真实 inode 关联、私有 `.agentmeta` 元数据文件、索引和依赖查询设计细节 |
| [task5-agent-loop.md](task5-agent-loop.md) | 任务五 watch/unwatch、FIFO 事件队列、wait/timeout 睡眠、heartbeat 设计细节 |
| [test-record.md](test-record.md) | 测试记录和关键输出 |
| [../LICENSE](../LICENSE) | 源代码 GPL-3.0 |
| [../DOCUMENTATION_LICENSE.md](../DOCUMENTATION_LICENSE.md) | 文档与答辩材料 CC BY-SA 4.0 |
| [../NOTICE](../NOTICE) | 第三方来源和许可声明 |

## 文档维护约定

- 主设计事实以 [design.md](design.md) 为准，分任务文档只展开实现细节。
- 用户态/内核态接口分工和结构体布局以 [api.md](api.md) 为准。
- 赛题完成度判断以 [requirements-traceability.md](requirements-traceability.md) 和 [verification.md](verification.md) 共同为准。
- 新增功能必须同步更新对应设计说明、API/ABI、验证记录和演示脚本。
- `test-record.md` 保留测试输出摘要，不替代验证结论。
