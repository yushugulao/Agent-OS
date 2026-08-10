<p align="center">
  <img src="docs/agentos/assets/agentos_logo.png" alt="AgentOS logo" width="680">
</p>

# AgentOS-uCore

AgentOS-uCore 是面向 AI Agent workflow 的 RISC-V uCore 内核扩展，也是计算机操作系统能力竞赛系统功能实现赛道作品。项目把 Agent 身份、Context Path、结构化工具 RPC、声明式执行合同、workflow 资源/调度、实时文件查询和可验证 fence 放入内核。模型主导的 tool loop 由 Guest 用户态拥有；可选 Host relay 只处理串口与 HTTPS/provider 协议，MCP/A2A 则是另一条独立的标准对象映射 prototype。内核不运行 LLM，也不把这三层混成一个远程 Agent server。

## 评审入口

- [竞赛评审入口](docs/contest/README.md)：赛题映射、演示顺序和材料边界。
- [系统设计](docs/agentos/design.md)：当前内核架构与关键不变量。
- [合同与 Agent Loop 边界](docs/agentos/task6-execution-contract.md)：ENFORCE V3、Guest model loop、Task Channel core 与 MCP/A2A object prototype。
- [ABI 参考](docs/agentos/api.md)：用户接口、兼容项和错误语义。
- [要求追踪](docs/agentos/requirements-traceability.md)：任务到源码与验证入口的映射。
- [验证说明](docs/verification.md)：构建、功能、安全与性能测试入口。
- [Windows 快速开始](docs/windows-quickstart.md)：依赖、WSL/MSYS2 与 QEMU 运行方法。
- [现场演示脚本](docs/agentos/scenario-script.md)：主演示、专项程序和观察点。
- [实测性能结果](docs/contest/performance-results.md)：真实 QEMU 的 scan/index 配对数据与复测命令。
- [双目标说明](docs/dual-targets.md)：plain uCore 与 AgentOS-uCore 的边界和比较方法。
- [AI 工具使用披露](docs/contest/ai-usage-disclosure.md)：开发辅助工具与运行时边界。

项目以实际构建、QEMU Guest 行为和可重复的性能负载判断产品状态。测试输出用于定位问题和复核结果，不以材料封装替代产品行为。

## 当前核心设计

| 机制 | 当前实现 |
| --- | --- |
| Agent Context 映射 | 固定 7 页：前 6 页由内核发布身份、Context 记录与可信 mirror，用户态只读；第 7 页是用户态可直接读写的 cache。题面要求的高频缓存走第 7 页，但该页内容不参与 capability、scope、因果或授权判断。 |
| Agent Workflow Credit Domain | 每个资源账户维护 `used/pending/free`（U/P/F）credit。补充额度时批量预充，普通 commit/cancel/release 只在本地三态间移动；额度不足、资源压力、context switch、账户推进或 workflow fence 才 trim/汇总。全局硬容量和账户硬限额始终按 `U+P+F` 验证。 |
| Fence-Sealed Evidence Ring | 每个 workflow 按需计费 4 页，普通区 48 槽、关键区 16 槽。普通成功 Context 只写一次 canonical ring 记录；拒绝或有授权效果的关键记录同时保留兼容 ledger 投影。fence 把有序事件、gap、challenge、metadata generation 和 credit digest 密封为 SHA-256 根。 |
| Agent Live-Query FS | 只有显式 `agent_file_meta_set()` 的记录进入当前启动周期的内存 catalog 和 `status/stage/kind` 选择性索引。typed watch 根据谓词变化产生 `ENTER/UPDATE/LEAVE`，队列不足或增量丢失时产生带 generation 的 `RESYNC_REQUIRED`。普通目录不会自动进入 catalog。 |
| 收敛 lifecycle | workflow 以不可变 `id + generation` 标识，核心状态是 `member_refcount + closing`。operation、departure 和 fence gate 阻止新旧操作穿越 cut；最后成员离开后才允许回收资源域。不存在对外宣称的多阶段 retirement workflow。 |
| Declarative Execution Contract | 每个 lifecycle generation 最多冻结一份 24-node DAG。只有启用 `AGENT_EXECUTION_CONTRACT_F_ENFORCE` 的 V3 调用获得 node/schema/predecessor/capability/provenance/effect、deadline 和 resource envelope 的高保证包络；V1/V2 legacy/exploratory RPC 不获得同等声明。 |
| Tool Phase Credit Lease | 工具开始前从 workflow 已计入 U 的 exec/storage credit 原子锁定 envelope；分配 claim 带 nonce，失败 refund，未用量在完成/失败/取消/超时结算为 F。lease 不增加 `U+P+F` 硬额度。 |
| Workflow EEVDF | 以 workflow resource domain 而不是线程作为公平实体；lag 决定 eligibility，latency/event deadline 形成 virtual deadline，按实际 service cycles 记账，异常时回退旧 scheduler。当前同时跟踪的总上限为 4，由 1 个 `BOOT_SEALED` bootstrap participant 和最多 3 个 fresh workflow 组成。 |
| Context Provenance Security | Context、文件/工具输出和 IPC 使用六个固定来源标签。ENFORCE V3 的外部副作用同时检查完整 lifecycle、冻结 contract edge、schema、capability、manifest provenance/effect；非法调用在副作用前 `DENIED` 并进入 critical Evidence。 |
| Kernel LLM RPC | `LLM_REQUEST/LLM_RESPONSE` 绑定完整 requester/relay 身份；correlation 对同 requester 严格递增，成功投递才推进。pending 有 120 秒 tick TTL；容量为 `NPROC` 的有界终态 history 在记录仍保留时区分已消费 `STALE` 与过期 `TIMEOUT`，覆盖后旧响应按 unmatched 返回 `DENIED`。它不是模型 API、HTTPS 或 MCP。 |
| Guest-owned model loop | `agentlive` 在 Guest 保存 prompt/history/tool catalog/round，校验模型建议，以 typed V2 调用内核工具，并把实际 Context/tool result 回灌下一轮。`labdemo_ucore` 仍是 deterministic policy workflow，不声称由 LLM 决策。 |
| Host HTTPS relay | Host 独占 TLS/API key/provider JSON 与 QEMU 串口；不读取 Guest 业务文件、不选择或执行工具、不伪造 tool result。live API 可选，offline replay 仍走相同有界 wire。 |
| Typed Agent Task Channel | 按需计费 4 页：16 槽 SQ、16 槽 CQ 和两个私有页。single issuer、copy-before-validate、sticky resync 与 one-terminal CQE core 已实现；当前仅同步 null/NONE provider，`RESOURCE_IMPORT` fail closed，无业务 payload backend，也不承载模型 wire。 |
| MCP/A2A object prototype | 用户态参考 [MCP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) 与 A2A v1 的对象形状，使用 deterministic in-memory transport；无 HTTP server、streaming、跨实现互操作验证或内核 SQ/CQ adapter。 |

## 语义边界

- Workflow fence 返回 320 字节 receipt，绑定 request id、challenge、fence sequence、精确 credit 使用量、metadata generation、事件范围、gap 数和前后根。`PARTIAL_COVERAGE` 是显式标志：当前根覆盖 Evidence Ring 的规范事件，不声称覆盖整个文件系统或所有调度事实。
- `CREDIT_EXACT` 表示 fence gate 内已 trim exec/storage 账户，`pending == 0`，receipt 的 `resource_used[]` 是该 cut 的精确 U 值；它只描述该次 cut。
- `EVIDENCE_SEALED` 表示 challenge-bound 的当前启动周期内存证据根已经发布；它不是外部签名或全系统证明。
- `METADATA_VOLATILE` 明确表示 metadata generation 属于当前启动周期的内存 catalog。
- audit、timeline、provenance 和 ledger API 仍作为兼容读取视图存在。普通成功 Context 的 canonical 存储来自 Evidence Ring；关键记录或 ring 写入失败时才走受保护的兼容 ledger 路径。因此不能把兼容视图描述为完全删除。
- scalar V2 是逐轮探索式 typed RPC；`agent_run()` 是最多 64 项的 compact 数组，在一次 syscall 内顺序同步执行，不是 typed-KV/non-blocking batch。V3 保留 V2 ABI 前缀，并在 ENFORCE 下追加 immutable contract binding。
- provenance 标签只表达固定来源并保守传播，不表示内核理解文本或判断 prompt injection。计划外副作用不能越过 frozen contract/dataflow gate 的主张限定于 ENFORCE V3；V1/V2 仍受 capability/scope/schema 约束，但没有同等 frozen-DAG 保证。
- Task Channel 的 exactly-once 只表示每个 accepted target 有一个 terminal CQE，不表示任意远程工具具备分布式 exactly-once；timer 只标记/唤醒，hard deadline 到第一个可调度 safe point 才结算，不提供 wall-clock completion bound。
- EEVDF 评价中的 1-way 只验证单实体 fast path；4-way 满容量 cohort 是同一个 `BOOT_SEALED` bootstrap participant 加 3 个 fresh workflow，不是 4 个 fresh lifecycle。线程放大场景比较 1 个 fresh 4-thread workflow 与 2 个 fresh single-thread workflow，并保留 bootstrap peer。
- 16 是逻辑样本数：四波都复用同一个 bootstrap participant 一次，并各创建 3 个 fresh workflow，因此合计为 4 次 bootstrap 观测和 12 个 fresh lifecycle 样本；它既不是 16-way 并发，也不是每波 4 个 fresh workflow，更不表示 16 个独立 lifecycle。唤醒直方图及其 p50/p99 聚合只覆盖 fresh-agent 样本。
- typed handle 参考 WIT owned/borrowed 词汇，但当前没有 payload import/result backend，也不嵌入完整 Wasm/WASI runtime。MCP/A2A 代码只是标准对象/in-memory 状态映射 prototype，不是 server、streaming transport、已验证互操作或内核 adapter。
- live model 回复只允许一个 `tool_use` 或 `final`；工具选择与执行留在 Guest。Host replay 使用与 live 相同的串口 frame/session/sequence/hash 边界，但 replay 不是云模型实测，也不提供远程 exactly-once 或任意长上下文。

## 赛题任务映射

| 任务 | 主要实现 |
| --- | --- |
| Agent 进程与地址空间 | 可信映像、角色/capability、Context 映射、不可变 workflow generation、closing/member/gate 生命周期。 |
| 结构化工具调用 | name/id 目录、V2 exploratory typed RPC、ENFORCE V3 immutable contract、顺序 compact batch、Phase Lease 与 Task SQ/CQ core。 |
| Context Path | 内核可信记录、只读 mirror、查询/快照/分支/因果字段、六标签 provenance 与 critical denial evidence。 |
| 文件属性查询 | 显式 volatile metadata、选择性内存索引、inode incarnation、内容摘要、typed live query。 |
| Agent Loop | 为用户态 policy/model loop 提供有界事件队列、watch/wait、heartbeat、可信 IPC/LLM correlation 和 workflow EEVDF substrate。 |
| 综合应用 | `labdemo_ucore` deterministic 综合场景；另有 Guest-owned live/replay model loop。MCP/A2A object prototype 独立存在，不作为综合应用的网络互操作证明。 |

`baseline_ucore/` 是共享通用修复但不包含 AgentOS 子系统的对照目标，不是未经修改的上游镜像。双目标总耗时用于端到端比较；单项内核机制归因需要同内核消融或专项计数。

题面硬门槛和代表 Guest 如下：任务一验证 Agent/普通进程共存与 Context 映射；任务二验证至少 3 个结构化工具；任务三验证至少 5 轮连续调用及有界淘汰；任务四覆盖至少 2 类文件查询扩展并做 scan/index 对比；任务五覆盖 heartbeat 与事件驱动等待；任务六在 QEMU 综合场景中整合至少 3 个已实现模块。逐项源码、命令和判定边界见[要求追踪](docs/agentos/requirements-traceability.md)。

## 快速构建

Windows 首次检查：

```powershell
.\scripts\check-windows-prereqs.ps1
```

在配置好的 Linux、WSL 或项目工具链环境中，先让 Makefile 自动选择交叉编译器：

```bash
make doctor
make build
make agent-module-check
make kernel-stack-check
```

自动选择会优先使用 `riscv64-unknown-elf-`，否则使用 Ubuntu/WSL 软件包提供的 `riscv64-linux-gnu-`。Windows xPack 等裸机工具链通常使用 `riscv-none-elf-`；若它不在自动选择范围内，请给上述命令追加 `TOOLPREFIX=riscv-none-elf-`。安装方式和 Windows 路径示例见 [Windows 快速开始](docs/windows-quickstart.md)。

核心机制的 Host 模型/变异测试：

```bash
python -B scripts/test-workflow-credit-domain.py
python -B scripts/test-workflow-fence.py
python -B scripts/test-workflow-syscall-cut.py
python -B scripts/test-agent-evidence-ring.py
python -B scripts/test-agent-live-query-fs.py
python -B scripts/test-agent-execution-contract.py
python -B scripts/test-agent-task-channel.py
python -B host_tools/test_workflow_scheduler_model.py
python -B host_tools/test_agent_task_transport.py
python -B host_tools/test_mcp_a2a_gateway.py
python -B host_tools/test_guest_llm_relay.py
```

需要 Guest 行为时再运行：

```bash
make agentos-test
make dual-platform-run
```

现场综合演示：

```bash
make contest-demo
```

该命令默认运行 4 个等量 AB/BA QEMU 样本并写入 `results/contest-demo/`；主要结果为 `report.md`、`summary.json`、`measurements.csv` 和逐样本串口日志。这些文件只描述本次运行。`labdemo_ucore` 使用确定性用户态 policy，便于离线重复和性能比较，不把主演示冒充真实 LLM 决策。当前源码的一次真实测量摘要见[实测性能结果](docs/contest/performance-results.md)，演示内容与专项程序见[现场演示脚本](docs/agentos/scenario-script.md)。

与主演示分开的 Guest model loop 有独立 QEMU 入口：

```bash
make agent-live-demo
make agent-live-demo-check
```

`agent-live-demo` 默认显式选择 Host `replay` provider，读取 `ci/agent-live-replay.jsonl`，让 6 轮响应经过与 live 相同的 QEMU 串口 wire，不需要网络或 API key。成功日志包含 `agentlive_ucore: discovery=1 rich_overlay=3`、工具/拒绝/Context roundtrip 计数、`transcript_turns=5 retained=5 dropped=0`、`agentlive_ucore: passed` 和顶层 `agentlive_ucore: parent passed`；`agent-live-demo-check` 只运行 Guest/Host 静态集成与协议单测，不能替代 QEMU 闭环。

真实 provider 必须显式选择。当前工作区可直接运行：

```bash
AGENT_LIVE_PROVIDER=deepseek make agent-live-demo
```

DeepSeek 入口默认使用官方 OpenAI-compatible Chat Completions endpoint、`deepseek-v4-flash` 和 non-thinking tool mode；旧的 `deepseek-chat` 已由官方退役。默认目标要求模型先查询 Guest 进程创建的 `agentlive.note`，再把真实返回的文件大小与 inode 编成下一轮 `echo` 参数，最后才生成自然语言回答；入口会精确验收两个工具、零拒绝和两轮完整 transcript，而不是只看进程退出码。命令行覆盖 `AGENT_LIVE_GOAL` 时会关闭这组默认场景专属 marker，但仍保留通用 Guest 完成门。

Make 会优先读取仓库外的 `../计算机操作系统能力竞赛/deepseek_api.txt`，只把内容交给 Host relay，不写入 Guest、命令行或日志；文件不存在时回退 Host 的 `DEEPSEEK_API_KEY`。可用 `AGENT_LIVE_API_KEY_FILE=/path/to/key.txt` 指定其他单行 key 文件，或清空该变量后通过 `AGENT_LIVE_API_KEY_ENV=ENV_NAME` 选择环境变量，两者同时设置会 fail closed。即便是演示专用 key 也保持仓库外；[DeepSeek Open Platform Terms](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html) 明确要求不得共享或公开披露 API key。模型与 endpoint 可用 `AGENT_LIVE_MODEL`、`AGENT_LIVE_ENDPOINT` 覆写。DeepSeek 采用 non-thinking 模式，是因为当前有界 Guest transcript 只保存可复验的 `tool_use`/`tool_result`，不传递供应商私有的 `reasoning_content`。

OpenAI 与 Anthropic 的默认 key 环境变量仍分别是 `OPENAI_API_KEY` 与 `ANTHROPIC_API_KEY`。需用户批准的工具由 `AGENT_LIVE_APPROVED_TOOLS` 生成可重复的 `--approve-tool` 参数；当前 `query_file`、`echo` 始终在 catalog 中，只有 `send_message` 受这一批准 gate。底层参数合同可通过 `python -B host_tools/guest_llm_relay.py --help` 查看，其中 `--provider openai|anthropic|deepseek|replay`、`--goal`/`--goal-file`、`--api-key-file` 和可重复的 `--approve-tool NAME` 都是 Host 控制面。Linux/WSL 默认让 Make 自动探测工具链；Windows xPack 环境可给 `agent-live-demo` 追加 `TOOLPREFIX=riscv-none-elf-`。没有运行真实 provider 时，不把 replay 输出写成云模型结果。竞赛主演示不调用这个入口。DeepSeek 的 endpoint、现行模型与工具调用语义以[官方 API 文档](https://api-docs.deepseek.com/quick_start/pricing)和[工具调用说明](https://api-docs.deepseek.com/guides/tool_calls)为准。

也可以直接运行最相关的产品场景，缩短修改后的反馈时间：

```bash
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test
AGENT_TEST_CASE=agenttask_ucore make agentos-test
AGENT_TEST_CASE=agent_eevdf_ucore make agentos-test
```

这里的 `Evidence Ring` 是当前启动周期内的内核安全审计能力。详细测试选择和结果解释见 [验证说明](docs/verification.md)。

## 仓库结构

```text
os/                AgentOS-uCore 内核
user/              用户库、专项程序和科研平台负载
baseline_ucore/    不含 AgentOS 服务的共享安全基底对照
scripts/           构建、静态合同、QEMU 与回归工具
host_tools/        HTTPS/model relay、协议对象 prototype、双目标比较与结果解析
ci/                UAPI、安全边界与测试配置
docs/              设计、ABI、验证和竞赛材料
```

## 来源与许可

项目基于 LearningOS/uCore 教学内核扩展。源码采用 [GPL-3.0](LICENSE)，文档采用 [CC-BY-SA-4.0](DOCUMENTATION_LICENSE.md)。Linux CPU accounting/percpu/rstat、BPF ring buffer、Haiku BFS、Linux EEVDF、io_uring、WIT/WASI 0.3、AgentCgroup、Murakkab、CaMeL、IPIGuard、[Anthropic tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)、[Claude Code agentic loop](https://code.claude.com/docs/en/how-claude-code-works)、[MCP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) 与 [A2A v1](https://a2a-protocol.org/latest/specification/) 仅作为公开设计/协议思想来源；本仓库采用 clean-room 的项目特定实现，没有 vendoring 它们的源码、SDK、测试数据、二进制或磁盘格式。完整披露见 [NOTICE](NOTICE)、[合同架构附录](docs/agentos/task6-execution-contract.md) 与[第三方及原创增量说明](docs/contest/third-party-and-originality.md)。
