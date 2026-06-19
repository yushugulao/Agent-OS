# 文档标准采用说明

本项目不是机械套用某一个模板，而是按操作系统内核项目和软件架构文档的共性要求进行裁剪。目标是让评委能够快速完成四件事：编译运行、理解架构、核对赛题要求、复现实验证据。

## 采用的标准和惯例

| 来源 | 采用点 | 本项目落点 |
| --- | --- | --- |
| 赛题说明 | 交付物包含内核代码、用户态测试程序、综合演示场景、设计文档与运行说明；设计文档要求 Markdown、架构说明、关键设计决策、测试程序和演示脚本 | [../README.md](../README.md)、[design.md](design.md)、[verification.md](verification.md)、[demo-script.md](demo-script.md) |
| ISO/IEC/IEEE 29148 Requirements Engineering | 用可追踪的需求条目描述“必须做什么”，并把需求关联到验证证据 | [requirements-traceability.md](requirements-traceability.md) |
| ISO/IEC/IEEE 42010 Architecture Description | 用利益相关方、关注点、视图、架构决策和理由描述系统架构 | [design.md](design.md) |
| IEEE 1016 Software Design Description | 设计文档应能记录并沟通设计信息，覆盖高层设计和详细设计 | [design.md](design.md)、[api.md](api.md) |
| Linux kernel documentation practice | 区分用户视角文档、构建运行、用户态 API、内部 API、子系统说明；函数和数据结构说明尽量靠近代码 | [api.md](api.md)、[task1-agent-process.md](task1-agent-process.md)、[task2-agent-call.md](task2-agent-call.md)、[task3-context-path.md](task3-context-path.md) |
| arc42 architecture template | 用 Introduction, Constraints, Context, Solution Strategy, Building Block, Runtime, Deployment, Crosscutting Concepts, Decisions, Quality, Risks, Glossary 组织架构说明 | [design.md](design.md) |

## 裁剪原则

| 原则 | 说明 |
| --- | --- |
| 评审优先 | README 必须能让评委快速构建、运行和定位材料 |
| 需求可追踪 | 每条关键赛题要求都要能找到实现位置和测试证据 |
| 架构多视图 | 不只给一个模块表，还要给运行流程、部署环境、接口和质量要求 |
| 内核职责划分清晰 | 文档必须说明用户态 API、系统调用层、Agent 子系统、进程/页表/文件元数据服务之间的职责划分 |
| 证据和结论分离 | `test-record.md` 存放输出记录，`verification.md` 给出验证结论和覆盖范围 |
| 详细度分层 | 主设计文档解释总体方案，任务文档作为细节附录，避免 README 过长 |

## uCore 分支文档口径

uCore 分支文档按当前代码事实编写：

- 源码主目录是 `os/`，不是旧版教学内核目录。
- 用户态 ABI 以 `user/include/agent.h` 为准。
- 内核 ABI 以 `os/agent.h` 为准。
- 最终测试入口是 `agentfinal_ucore`、`agentfs_ucore`、`agentloop_ucore`、`agentbench_ucore`、`labbench_ucore`、`labdemo_ucore` 和 `agentsecurity_ucore`。
- Agent 交付以 `CHAPTER=agent` 为验收主路径；`ch3_trace` 作为代表性 uCore 基础 syscall 抽测材料。
- 任务四当前是绑定真实 inode 的内核文件元数据表、私有 `.agentmeta` 元数据文件、重新加载和索引查询服务；不表述为后台线程持续扫描整棵目录。
- 任务五当前是可验证的 watch/unwatch、FIFO 事件队列、wait/wake、有限 timeout 睡眠、heartbeat event delivery；不表述为完整平台级长期调度器。
- 性能数据是样例输出，复跑时 tick 数值会波动；文件查询性能重点看候选记录数差异和多轮 tick 观测。
- event wait/wake 输出是计时观测，不表述为调度器性能结论。

## 外部参考

- IEEE 29148 Requirements Engineering: <https://standards.ieee.org/ieee/29148/6937/>
- ISO/IEC/IEEE 42010 Architecture Description overview: <https://www.iso-architecture.org/42010/>
- IEEE 1016 Software Design Description: <https://standards.ieee.org/ieee/1016/4502/>
- Linux kernel documentation guide: <https://docs.kernel.org/doc-guide/kernel-doc.html>
- Linux kernel documentation index: <https://github.com/torvalds/linux/blob/master/Documentation/index.rst>
- arc42 template overview: <https://arc42.org/overview>
