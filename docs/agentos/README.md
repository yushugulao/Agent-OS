# AgentOS 文档索引

本目录记录当前代码，而不是历史方案。若文档与实现发生冲突，以公开 UAPI 头文件、生产构建的源码清单和版本化 checker 为准。

## 推荐顺序

| 顺序 | 文档 | 内容 |
| ---: | --- | --- |
| 1 | [../../README.md](../../README.md) | 项目定位、快速构建和关键边界 |
| 2 | [design.md](design.md) | Credit Domain、Evidence Ring、Live Query 与 lifecycle 总设计 |
| 3 | [api.md](api.md) | 当前 ABI、workflow fence、typed watch 和兼容项 |
| 4 | [security-hardening.md](security-hardening.md) | 威胁模型、fail-closed cut 与资源安全 |
| 5 | [requirements-traceability.md](requirements-traceability.md) | 赛题任务到源码、静态检查和 Guest 验证的映射 |
| 6 | [verification.md](verification.md) | 开发验证顺序和证据边界 |

## 任务附录

| 文档 | 定位 |
| --- | --- |
| [task1-agent-process.md](task1-agent-process.md) | Agent 身份、地址空间、workflow lifecycle 与资源域 |
| [task2-agent-call.md](task2-agent-call.md) | 结构化工具协议 |
| [task3-context-path.md](task3-context-path.md) | Context Path 与因果字段 |
| [task4-file-query.md](task4-file-query.md) | 显式 volatile metadata、选择性索引和 typed live query |
| [task5-agent-loop.md](task5-agent-loop.md) | 事件循环、IPC、Evidence Ring 和兼容观测视图 |

## 当前架构速记

1. **资源**：U/P/F credit 以 `U+P+F` 参加硬准入；热路径在本账户内移动 credit，fence/context switch/压力路径才 trim。
2. **证据**：普通成功 Context 只进入一次 canonical Evidence Ring；关键拒绝和授权效果另有兼容 ledger 投影；workflow fence 生成 challenge-bound SHA-256 根。
3. **文件查询**：只有显式 `agent_file_meta_set()` 进入内存 catalog/index；typed watch 产生 `ENTER/UPDATE/LEAVE`，丢失增量时要求 generation resync。
4. **生命周期**：`member_refcount + closing` 是核心状态；operation、departure、fence 三类 gate 保证 cut；最后成员离开后回收。
5. **不提供**：普通目录 autoscan、metadata crash catalog、逐操作磁盘证据、observe recovery catalog、多阶段 workflow retirement。

## 兼容性提示

- audit/timeline/provenance/ledger API 保留，并从 Context、Evidence Ring、调度记录及少量兼容 ledger 记录构造读取视图。
- `AGENT_FILE_META_F_PERSIST`、`AGENT_FILE_META_F_AUTOSCAN` 名称仍在头文件中，但当前 metadata set 拒绝它们。
- observe recovery syscall 编号和请求结构仍保留，调用固定返回 `AGENT_STATUS_BAD_PARAM`。
- 源码中可能留有未进入生产对象清单的历史实现文件。文档只描述 Makefile 实际构建的路径。

## 来源说明

Credit Domain、Evidence Ring 和 Live Query 分别受到 Linux CPU accounting/percpu/rstat、Linux BPF ring buffer 和 Haiku BFS 属性/live query 的概念启发。实现为本项目 clean-room 代码，没有复制或 vendoring 上游源码、数据、二进制或磁盘格式，详见 [../../NOTICE](../../NOTICE)。

## 维护规则

- 发布数值只从 [正式证据索引](../../evidence/releases/INDEX.md) 指向的冻结 bundle 读取。
- UAPI 布局以 `ci/agent-uapi-layout.json` 和 `scripts/check-agent-uapi-layout.py` 为准。
- 模块、体积和测试预算以 `ci/kernel-budgets.json` 及 checker 为准。
- 修改 fence、credit、ring 或 live query 时，必须同步更新设计、API、追踪表与验证说明。
