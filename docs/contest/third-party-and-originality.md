# 第三方来源与原创增量说明

本文用于帮助评委区分上游教学内核、外部运行工具和本项目新增内容。它是工程披露，不替代各
许可证正文，也不构成法律意见。最终范围应以冻结提交的 Git tree 和历史为准。

## 已知第三方基础

| 项目 | 在本项目中的作用 | 获取/分发方式 | 许可与依据 |
| --- | --- | --- | --- |
| LearningOS/uCore-Tutorial-Code-2025S | 教学内核和基础用户态代码 | 本仓库衍生修改 | GPL-3.0；见根目录 `NOTICE`、`LICENSE` |
| LearningOS/uCore-Tutorial-Test-2025S | 教学测试基础 | 本仓库衍生修改 | GPL-3.0；见根目录 `NOTICE` |
| QEMU、RISC-V GCC/binutils、GNU Make、Bash、Python | 构建和运行环境 | 不随仓库捆绑，由复现环境安装 | 各自上游许可；其绝对路径、版本和哈希由正式证据清单记录 |

根目录源码许可证为 GPL-3.0，技术文档和展示材料采用 CC BY-SA 4.0。当前 `NOTICE` 给出了已知
上游链接。仓库的 `.clang-format`、`user/.clang-format` 和
`baseline_ucore/user/.clang-format` 来自 Linux 内核格式配置并保留
`SPDX-License-Identifier: GPL-2.0` 标记；它们作为独立格式数据分发，不改写为 GPL-3.0。

## 相对上游的主要项目增量

以下内容描述本仓库围绕赛题实现的主要增量，不主张其中每个通用算法都由本队首次发明：

1. Agent 身份、角色、capability、Context 地址区以及与 exec/fork/exit 集成的内核生命周期。
2. 名称协议和版本化 typed KV 协议、内核工具目录、统一参数规则与批量工具调用运行时。
3. shadow/mirror/cache Context Path、分支 rollback、可信因果、timeline、audit 和 provenance。
4. 与 inode incarnation 绑定的 Agent 文件 metadata、属性索引、内容摘要、租约和查询计划。
5. watch/wait/heartbeat、可信 Agent IPC、事件配额和资源域两级公平调度。
6. 不可变 workflow lifecycle id/generation、统一 teardown 状态机和通用资源控制器。
7. metadata 双 bank、持久观测、VirtIO flush/fault、分域 I/O 和 buffer cache 控制。
8. 同负载双目标科研平台、QEMU 动态测试、预算门、来源绑定证据和离线评价 Dashboard。

完整实现位置、测试状态和已知限制见[要求追踪表](../agentos/requirements-traceability.md)和
[最终加固矩阵](../agentos/final-hardening-matrix.md)。`baseline_ucore/` 同样包含一部分通用安全
加固，因此只能作为“不包含 AgentOS 服务的共享基底对照”，不能称作原封不动的上游基线。

## 原创性核验方式

- 用 Git 历史列出从选定上游基线到最终提交的文件和提交增量，不以代码行数替代实质贡献说明。
- 答辩时按“问题、内核抽象、关键实现、动态证据、边界”说明每项增量。
- 对参考论文、博客、代码片段、图片、数据集和生成式 AI 输出逐项记录来源与许可。
- 对无法确认来源的材料先移出提交包或标记待核验，不能自行推断为公有领域。
- 最终 `NOTICE`、本页、AI 披露、材料清单与实际 Git tree 必须一致。

## 提交负责人确认

| 项目 | 记录 |
| --- | --- |
| 对比的上游仓库与 commit | `待填写` |
| 本项目最终 commit/tag | `待填写` |
| 额外第三方代码或素材 | `待核验并填写` |
| Git 增量复核人 | `待填写` |
| 许可与署名复核人 | `待填写` |
| 确认日期 | `待填写` |
