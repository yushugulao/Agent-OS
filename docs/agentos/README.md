# AgentOS 文档索引

本目录记录当前代码，而不是历史方案。若文档与实现发生冲突，以公开 UAPI 头文件、生产构建的源码清单和版本化 checker 为准。

## 推荐顺序

| 顺序 | 文档 | 内容 |
| ---: | --- | --- |
| 1 | [../../README.md](../../README.md) | 项目定位、快速构建和关键边界 |
| 2 | [design.md](design.md) | 执行合同、Credit Domain、Provenance、Task Channel 与 lifecycle 总设计 |
| 3 | [api.md](api.md) | 当前 ABI、contract-bound call、Task Channel、workflow fence 和兼容项 |
| 4 | [security-hardening.md](security-hardening.md) | 威胁模型、fail-closed cut 与资源安全 |
| 5 | [requirements-traceability.md](requirements-traceability.md) | 赛题任务到源码、静态检查和 Guest 验证的映射 |
| 6 | [verification.md](verification.md) | 开发验证顺序和结果边界 |

## 任务附录

| 文档 | 定位 |
| --- | --- |
| [task1-agent-process.md](task1-agent-process.md) | Agent 身份、地址空间、workflow lifecycle 与资源域 |
| [task2-agent-call.md](task2-agent-call.md) | 结构化工具协议 |
| [task3-context-path.md](task3-context-path.md) | Context Path 与因果字段 |
| [task4-file-query.md](task4-file-query.md) | 显式 volatile metadata、选择性索引和 typed live query |
| [task5-agent-loop.md](task5-agent-loop.md) | 事件循环、IPC、Evidence Ring 和兼容观测视图 |
| [task6-execution-contract.md](task6-execution-contract.md) | 声明式执行合同、Phase Credit、workflow EEVDF、Provenance 与 MCP/A2A Task Channel |

## 当前架构速记

1. **资源**：U/P/F credit 以 `U+P+F` 参加硬准入；热路径在本账户内移动 credit，fence/context switch/压力路径才 trim。
2. **证据**：普通成功 Context 只进入一次 canonical Evidence Ring；关键拒绝和授权效果另有兼容 ledger 投影；workflow fence 生成 challenge-bound SHA-256 根。
3. **文件查询**：只有显式 `agent_file_meta_set()` 进入内存 catalog/index；typed watch 产生 `ENTER/UPDATE/LEAVE`，丢失增量时要求 generation resync。
4. **生命周期**：`member_refcount + closing` 是核心状态；operation、departure、fence 三类 gate 保证 cut；最后成员离开后回收。
5. **执行合同**：每个 lifecycle generation 最多冻结一份 24 节点 DAG；V3 工具调用必须绑定节点、schema、predecessor Context、artifact、deadline 和 retry/cancel/resource envelope。
6. **资源阶段与调度**：Tool Phase Lease 从既有 U 中锁定短生命周期 envelope，未用量结算为 F；workflow EEVDF 按 lag/virtual deadline 选择总计最多 4 个公平实体，并在异常时回退旧调度器。固定拓扑是 1 个 `BOOT_SEALED` bootstrap participant 加最多 3 个 fresh workflow；4-way 使用 bootstrap+3 fresh。16 个逻辑样本分四波复用同一 bootstrap，并累计 12 个 fresh lifecycle 样本，不是 16-way 并发或每波 4 个 fresh；唤醒直方图只聚合 fresh-agent 样本。
7. **数据流安全**：Context、文件/工具输出和 IPC 传播六个固定 provenance 标签；副作用前同时检查 contract edge、capability、完整 generation、manifest 和数据流规则，非法调用进入 critical Evidence Ring。
8. **异步通道**：single-issuer Task Channel 使用 16 槽 SQ/CQ 和 2 个私有页，共 4 页；描述符先完整复制再验证，目标 request 只发布一个 terminal CQE。typed handle ABI/8-slot 私有表已实现，但当前仅 null payload，资源导入 fail closed。
9. **不提供**：内核自然语言规划/prompt-injection 分类、完整 Wasm runtime、内核 JSON/HTTP/OAuth、16 个并发 EEVDF workflow、普通目录 autoscan、metadata/evidence crash recovery。

## 兼容性提示

- audit/timeline/provenance/ledger API 保留，并从 Context、Evidence Ring、调度记录及少量兼容 ledger 记录构造读取视图。
- scalar V2 tool call 与 `agent_run()` batch 保留；V3 只追加冻结合同绑定。lifecycle info V3 保留 64 字节 V2 前缀。
- Task Channel 是按需接口；hard deadline 到第一个可调度 safe point 才结算，不保证 wall-clock 终止。MCP `2026-07-28` 与 A2A v1 当前只有用户态协议形状/in-memory transport 映射，尚无到内核 SQ/CQ 的 binary adapter。
- `AGENT_FILE_META_F_PERSIST`、`AGENT_FILE_META_F_AUTOSCAN` 名称仍在头文件中，但当前 metadata set 拒绝它们。
- observe recovery syscall 编号和请求结构仍保留，调用固定返回 `AGENT_STATUS_BAD_PARAM`。
- 源码中可能留有未进入生产对象清单的历史实现文件。文档只描述 Makefile 实际构建的路径。

## 来源说明

Credit Domain、Evidence Ring 和 Live Query 分别受到 Linux CPU accounting/percpu/rstat、Linux BPF ring buffer 和 Haiku BFS 属性/live query 的概念启发。新增主线还参考 Linux EEVDF、io_uring SQ/CQ、WIT/WASI 0.3、AgentCgroup、Murakkab、CaMeL、IPIGuard 以及 MCP/A2A 官方协议。实现为本项目 clean-room 代码，没有复制或 vendoring 上游源码、数据、二进制或磁盘格式，详见 [task6-execution-contract.md](task6-execution-contract.md) 与 [../../NOTICE](../../NOTICE)。

## 维护规则

- 功能与性能数值只引用实际 QEMU 或 Host 测量，并同时说明负载、样本和单位。
- `Evidence Ring` 是内核运行期安全能力，不是 Host 发布证据包。
- UAPI 布局以 `ci/agent-uapi-layout.json` 和 `scripts/check-agent-uapi-layout.py` 为准。
- 模块边界、栈安全与 Guest 行为分别由对应 checker、构建和 QEMU 测试验证。
- 修改 contract、phase credit、scheduler、provenance、Task Channel、fence、ring 或 live query 时，必须同步更新设计、API、追踪表与验证说明。
