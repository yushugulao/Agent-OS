# AgentOS 文档索引

本目录记录当前代码，而不是历史方案。若文档与实现发生冲突，以公开 UAPI 头文件、生产构建的源码清单和版本化 checker 为准。

![AgentOS-uCore 总体架构](assets/agentos_arch.svg)

## 推荐顺序

| 顺序 | 文档 | 内容 |
| ---: | --- | --- |
| 1 | [../../README.md](../../README.md) | 项目定位、快速构建和关键边界 |
| 2 | [design.md](design.md) | 执行合同、Credit Domain、Provenance、Task Channel 与 lifecycle 总设计 |
| 3 | [api.md](api.md) | 当前 ABI、contract-bound call、Task Channel、workflow fence 和兼容项 |
| 4 | [security-hardening.md](security-hardening.md) | 威胁模型、fail-closed cut 与资源安全 |
| 5 | [requirements-traceability.md](requirements-traceability.md) | 赛题任务到源码、静态检查和 Guest 验证的映射 |
| 6 | [interactive-console.md](interactive-console.md) | 长驻 Guest Agent Loop、双窗口交互、动态审批和 observer 边界 |
| 7 | [verification.md](verification.md) | 开发验证顺序和结果边界 |
| 8 | [scenario-script.md](scenario-script.md) | 现场主演示、输出和专项观察点 |
| 9 | [../verification.md](../verification.md) | 从环境检查到 QEMU、双目标和结果解释的运行说明 |
| 10 | [../windows-quickstart.md](../windows-quickstart.md) | Windows/WSL 环境、工具链与 QEMU 快速开始 |

## 任务附录

| 文档 | 定位 |
| --- | --- |
| [task1-agent-process.md](task1-agent-process.md) | Agent 身份、地址空间、workflow lifecycle 与资源域 |
| [task2-agent-call.md](task2-agent-call.md) | V2 exploratory typed RPC、ENFORCE V3 合同调用与顺序 compact batch |
| [task3-context-path.md](task3-context-path.md) | Context Path 与因果字段 |
| [task4-file-query.md](task4-file-query.md) | 显式 volatile metadata、选择性索引和 typed live query |
| [task5-agent-loop.md](task5-agent-loop.md) | 用户态 policy/model loop 的事件、IPC、调度与证据 substrate |
| [task6-execution-contract.md](task6-execution-contract.md) | 执行合同、Guest-owned model loop、Task Channel 与协议对象 prototype 的边界 |

## 当前架构速记

Agent Context 固定映射 7 页：前 6 页由内核发布且对 Guest 只读，承载可信身份/Context mirror；第 7 页是 Guest 可直接读写的 user cache，但不参与 capability、scope、因果或授权判断。这样同时满足题面的直接读写需求与可信记录隔离。

1. **资源**：U/P/F credit 以 `U+P+F` 参加硬准入；热路径在本账户内移动 credit，fence/context switch/压力路径才 trim。
2. **证据**：普通成功 Context 只进入一次 canonical Evidence Ring；关键拒绝和授权效果另有兼容 ledger 投影；workflow fence 生成 challenge-bound SHA-256 根。
3. **文件查询**：只有显式 `agent_file_meta_set()` 进入内存 catalog/index；typed watch 产生 `ENTER/UPDATE/LEAVE`，丢失增量时要求 generation resync。
4. **生命周期**：`member_refcount + closing` 是核心状态；operation、departure、fence 三类 gate 保证 cut；最后成员离开后回收。
5. **执行合同**：每个 lifecycle generation 最多冻结一份 24 节点 DAG；启用 `AGENT_EXECUTION_CONTRACT_F_ENFORCE` 的 V3 调用绑定节点、schema、predecessor Context、artifact、deadline 和 retry/cancel/resource envelope。V1/V2 不获得这一冻结包络。
6. **资源阶段与调度**：Tool Phase Lease 从既有 U 中锁定短生命周期 envelope，未用量结算为 F；workflow EEVDF 按 lag/virtual deadline 选择总计最多 4 个公平实体，并在异常时回退旧调度器。固定拓扑是 1 个 `BOOT_SEALED` bootstrap participant 加最多 3 个 fresh workflow；4-way 使用 bootstrap+3 fresh。16 个逻辑样本分四波复用同一 bootstrap，并累计 12 个 fresh lifecycle 样本，不是 16-way 并发或每波 4 个 fresh；唤醒直方图只聚合 fresh-agent 样本。
7. **数据流安全**：Context、文件/工具输出和 IPC 传播六个固定 provenance 标签；ENFORCE V3 在副作用前同时检查 frozen edge、capability、完整 generation、manifest 和数据流规则，非法调用进入 critical Evidence Ring。
8. **Task Channel core**：single-issuer SQ/CQ 使用 16 槽队列和 2 个私有页，共 4 页；描述符先完整复制再验证，目标 request 只发布一个 terminal CQE。当前 provider 同步、只接受 null input/output `NONE`，没有业务 payload backend，也不承载模型 wire。
9. **模型循环**：`LLM_REQUEST/LLM_RESPONSE` 是绑定完整 requester/relay 身份的一次响应内核 RPC；correlation 对同 requester 严格递增，成功投递才推进，pending 有 120 秒 tick TTL。Guest `agentlive` 拥有 prompt/history/tool catalog、选择校验、V2 执行和 result 回灌；Host relay 只拥有 QEMU 串口、TLS/API key 和 provider JSON，不读 Guest 业务文件，不选择或执行工具。
10. **协议 prototype**：MCP/A2A 模块只做标准对象到 deterministic in-memory Task 状态机的用户态映射；它不是 HTTP server、streaming transport、已验证互操作或内核 Task adapter，并且不是 live model relay。
11. **明确边界**：内核不做自然语言规划/prompt-injection 分类，不包含完整 Wasm runtime 或 JSON/HTTP/OAuth 栈；`labdemo_ucore` 是 deterministic policy workflow，不是 LLM 演示；EEVDF 活跃实体总上限为 4，metadata 只覆盖当前启动周期内显式登记的对象。

## 兼容性提示

- audit/timeline/provenance/ledger API 保留，并从 Context、Evidence Ring、调度记录及少量兼容 ledger 记录构造读取视图。
- scalar V2 是 `uint64`/string typed-KV exploratory RPC，最终压平到共享执行器的 `arg0/arg1/64-byte payload`；`agent_run()` 接受最多 64 个 compact `agent_op`，一次 syscall 内顺序同步执行，不是 typed-KV、parallel 或 non-blocking batch。V3 保留 V2 前缀，并仅在 ENFORCE 下追加冻结合同保证。
- Task Channel 是按需接口；hard deadline 到第一个可调度 safe point 才结算，不保证 wall-clock 终止。MCP `2026-07-28` 与 A2A v1 当前只有用户态对象形状/in-memory 状态映射，尚无 server、streaming、跨实现互操作验证或内核 SQ/CQ adapter。
- live API 是可选 Host 能力；offline replay 仍通过相同 QEMU 串口 frame/session/sequence/hash/round 边界，不能被称为真实云模型结果或直接 Guest 状态注入。

## 来源说明

Credit Domain、Evidence Ring 和 Live Query 分别受到 Linux CPU accounting/percpu/rstat、Linux BPF ring buffer 和 Haiku BFS 属性/live query 的概念启发。新增主线还参考 Linux EEVDF、io_uring SQ/CQ、WIT/WASI 0.3、AgentCgroup、Murakkab、CaMeL、IPIGuard、[Anthropic tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)、[Claude Code agentic loop](https://code.claude.com/docs/en/how-claude-code-works)、[MCP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) 与 [A2A v1](https://a2a-protocol.org/latest/specification/) 的公开思想或对象规范。实现为本项目 clean-room 代码，没有复制或 vendoring 上游源码、数据、二进制或磁盘格式，详见 [task6-execution-contract.md](task6-execution-contract.md) 与 [../../NOTICE](../../NOTICE)。

## 可选模型循环入口

`make agent-live-demo` 构建 `user/src/agentlive_ucore.c` 并默认以 `ci/agent-live-replay.jsonl` 运行 6 轮同串口 replay；成功以顶层 `agentlive_ucore: parent passed` 收口。`make agent-live-demo-check` 只运行 `scripts/test-agent-live-loop.py` 与 Host relay 单测，不等于 QEMU 或 live provider 已运行。Linux/WSL 默认自动探测工具链，Windows xPack 可追加 `TOOLPREFIX=riscv-none-elf-`。真实 provider 的 Host 变量与边界见[根 README](../../README.md)和[验证说明](verification.md)。

面向现场自由输入的产品入口是默认使用 DeepSeek 的 `make agentos-console`；它在获得 final 后保留 Guest/QEMU session，并可由第二窗口用 `make agentos-observe` 查看 high-signal live snapshots。`make agentos-console-deepseek` 是等价的清晰别名，`make agentos-console-replay` 则是一次 boot、并发 observer 的多回合固定脚本验收。三者的会话、25 秒动态审批、本地 socket、observer 扰动和结果解释见[交互控制台](interactive-console.md)。

## 维护规则

- 功能与性能数值只引用实际 QEMU 或 Host 测量，并同时说明负载、样本和单位。
- `Evidence Ring` 是当前启动周期内的内核安全能力，结果解释必须遵守 receipt 的覆盖范围。
- UAPI 布局以 `ci/agent-uapi-layout.json` 和 `scripts/check-agent-uapi-layout.py` 为准。
- 模块边界、栈安全与 Guest 行为分别由对应 checker、构建和 QEMU 测试验证。
- 修改 contract、phase credit、scheduler、provenance、Task Channel、fence、ring 或 live query 时，必须同步更新设计、API、追踪表与验证说明。
