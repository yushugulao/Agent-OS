# 竞赛评审入口

AgentOS-uCore 面向 AI Agent workflow 提供内核身份、Context、结构化工具、实时文件属性事件、可信 IPC、批量资源域和可验证 fence。科研业务与模型语义都在 Guest 用户态；plain/AgentOS 两目标运行同一 deterministic 工作流合同。可选 Host 只转发串口与 HTTPS/provider 协议，MCP/A2A 另有独立对象映射 prototype。

## 赛题映射与硬门槛

| 任务 | 题面门槛 | 当前机制 | 代表 Guest / 入口 |
| --- | --- | --- | --- |
| 任务一 | Agent 创建、Context 区、普通/Agent 进程共存 | 可信 role/capability、6 页内核发布只读区 + 1 页用户 cache 读写区、immutable lifecycle id+generation、members/closing/gates | `agentfinal_ucore`、`agenttrust_ucore`、`agentscope_ucore` |
| 任务二 | 至少 3 个结构化工具，含错误处理 | 25 项 name/id 目录；V2 exploratory typed RPC；ENFORCE V3 immutable contract；最多 64 项的顺序 compact batch；稳定错误码 | `agenttoolabi_ucore`、`agentfinal_ucore`、`agentcontract_ucore` |
| 任务三 | 至少 5 轮连续调用、直接读取、超长淘汰 | 内核可信记录、只读 mirror、cause/span/branch、查询/快照/rollback、有界 FIFO 窗口 | `agentfinal_ucore` |
| 任务四 | 至少 2 类扩展，结构化返回并提供查询对比 | explicit boot-scoped metadata、`status/stage/kind` 索引、summary/digest、typed `ENTER/UPDATE/LEAVE` 与 resync | `agentfs_ucore`、`agentbench_ucore` |
| 任务五 | 至少 2 类机制；heartbeat、事件等待、休眠与多 Agent 稳定性 | 用户态 Agent Loop substrate：event/watch/wait/heartbeat、可信 route/LLM correlation、workflow EEVDF、Evidence Ring 与 workflow fence | `agentloop_ucore`、`agentsched_ucore`、`agent_eevdf_ucore` |
| 任务六 | 整合至少 3 个模块、QEMU 综合程序、至少 1 组性能对比 | deterministic `labdemo_ucore` 串联身份 + Context + metadata/index + event/IPC + 授权拒绝；同内核 Compat/Native 与 plain/AgentOS 两类比较 | `labdemo_ucore`、`make contest-demo`、`make dual-platform-run` |

完整映射见[要求追踪表](../agentos/requirements-traceability.md)。

## 三项重点增量

1. **Workflow Credit Domain**：借鉴 Linux CPU accounting/percpu/rstat 的批量思想，以 U/P/F credit 保持硬额度，并在 context switch、压力和 fence 精确 trim。
2. **Fence-Sealed Evidence Ring**：借鉴 Linux BPF ring buffer 的有序 reserve/commit/discard，普通成功只写一次 canonical event，critical 独立分区，fence 绑定 challenge/credit/metadata/gap 根。
3. **Agent Live-Query FS**：借鉴 Haiku BFS 显式属性、索引和 live query，只索引显式 volatile metadata，把 typed transition 直接投递到 Agent Context，并用 generation resync 处理有界丢失。

三项都是 clean-room、项目特定实现，没有复制/vendoring 上游源码、测试、数据、二进制或磁盘格式。详见 [NOTICE](../../NOTICE) 与[第三方及原创增量说明](third-party-and-originality.md)。

## 必须主动说明的限制

- metadata 只覆盖用户态显式登记的对象，catalog/index/watch 都属于当前启动周期；
- Evidence Ring 和 audit receipt 是 fence-sealed memory evidence；
- audit/timeline/provenance/ledger 兼容视图仍保留，不能称为完全删除；
- lifecycle 使用 members/closing/gates，不宣称多阶段 retirement；
- fence receipt 明确 partial coverage 与 volatile metadata；
- V1/V2 与 compact batch 不获得 ENFORCE V3 的 frozen-DAG/provenance 包络；
- Task Channel 当前只有同步 null/NONE provider，没有业务 payload backend，也不承载模型 wire；
- `labdemo_ucore` 是 deterministic policy workflow，不是 LLM 决策演示；
- live model 的 history、tool catalog、选择/执行和 result 回灌属于 Guest；Host 只拥有串口、TLS/API key 与 provider JSON；
- MCP/A2A 代码只是 deterministic in-memory 对象映射 prototype，不是 server、streaming、互操作或内核 adapter；
- 当前 Guest 是单 Hart，Host 多 lane 不等于 SMP。

## 评审材料顺序

1. [根 README](../../README.md)：定位和快速入口。
2. [系统设计](../agentos/design.md)：三项机制与 workflow fence。
3. [ABI](../agentos/api.md)：320 字节 receipt、typed watch、结构化工具与错误语义。
4. [安全加固](../agentos/security-hardening.md)：hard admission、generation、resync、fail closed。
5. [验证说明](../verification.md)：构建、QEMU、功能、安全与性能测试。
6. [现场演示脚本](../agentos/scenario-script.md)：主演示命令、输出与专项讲解。
7. [实测性能结果](performance-results.md)：真实 QEMU 的 scan/index 配对数据与复测命令。
8. [Windows 快速开始](../windows-quickstart.md)：新机器依赖和工具链前缀。
9. [双目标说明](../dual-targets.md)：plain/AgentOS 比较边界。
10. [AI 工具使用披露](ai-usage-disclosure.md)：开发辅助与运行时边界。
评审应优先查看实际 QEMU 行为、拒绝路径、工作量计数和双目标比较。静态 checker 用于快速发现合同错误，不应代替 Guest 运行或真实性能测量。内核 `Evidence Ring` 是产品安全功能，不是参赛材料打包机制。

## 一条命令启动主演示

```bash
make contest-demo
```

默认运行 4 个等量 AB/BA QEMU 样本，并在 `results/contest-demo/` 生成 `report.md`、`summary.json`、`measurements.csv` 和逐样本串口日志。Makefile 在常见 Linux/WSL 环境中自动选择工具链；Windows xPack 环境可显式追加 `TOOLPREFIX=riscv-none-elf-`。结果只代表本次机器、工具链和负载；当前源码的一次实测摘要见[实测性能结果](performance-results.md)。可通过 `CONTEST_DEMO_SAMPLES` 选择 4 到 16 之间的偶数样本数。主演示始终运行 deterministic Guest policy，不需要 API key，也不把结果包装成模型决策。单独复核 `labdemo_ucore`、专项 Guest 或双目标的方法见[现场演示脚本](../agentos/scenario-script.md)、[验证说明](../verification.md)和 [Windows 快速开始](../windows-quickstart.md)。

## 可选模型循环

`agentlive` 是与主演示分开的 Guest-owned tool loop，沿用 [Anthropic tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works) 和 [Claude Code agentic loop](https://code.claude.com/docs/en/how-claude-code-works) 的公开交互模式：模型输出结构化 `tool_use`，Guest 校验并执行工具，再把真实结果回灌下一轮。Host relay 只做有界串口 frame 与 OpenAI-compatible/Anthropic HTTPS 翻译；API key/TLS 不进入 Guest，Host 不读业务文件、不选或执行工具。

live API 可选；offline replay 仍通过相同 QEMU 串口、session/sequence/hash/round 边界，因此可复核 wire 与 Guest 状态机，但 replay 不是云模型实测。adaptive loop 使用 V2 exploratory typed RPC；ENFORCE V3 只适用于预先冻结的固定 node/tool/edge 高保证路径。MCP/A2A prototype 参考 [MCP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) 与 [A2A v1](https://a2a-protocol.org/latest/specification/) 对象规范，但不参与这条 live relay。

Host relay 的稳定 CLI 要求显式 `--provider openai|anthropic|replay`、`--goal` 或 `--goal-file`，并以可重复的 `--approve-tool NAME` 给出用户批准的 Guest 工具；`replay` 另需 `--replay-file`。精确选项以 `python -B host_tools/guest_llm_relay.py --help` 为准。真实 provider 及 API key 不属于默认竞赛演示。

```bash
make agent-live-demo-check
make agent-live-demo
```

第二条默认读取 `ci/agent-live-replay.jsonl`，在 QEMU 中走 6 轮同串口 replay；Make 要求 discovery、`passed`、唯一顶层 `parent passed` 以及 replay 的精确工具/拒绝/relay markers，Guest 另输出 Context roundtrip。完整 marker 值见[验证说明](../agentos/verification.md)。第一条只做静态/Host 检查。Windows xPack 环境可追加 `TOOLPREFIX=riscv-none-elf-`。live 模式把 `AGENT_LIVE_PROVIDER` 设为 `openai` 或 `anthropic`，并把 `AGENT_LIVE_MODEL=MODEL_ID` 替换成真实 model id；key 只从 Host 环境读取，未实际运行时不展示或声称云模型结果。

## 建议演示顺序

| 时间 | 内容 | 关键证据 |
| --- | --- | --- |
| 0-3 分钟 | 可信 Agent、Context、tool batch | 身份/Context/tool Guest 输出 |
| 3-6 分钟 | Live Query 显式登记与 typed transition | query plan、ENTER/UPDATE/LEAVE、resync |
| 6-9 分钟 | Credit U/P/F 与 hard quota | resource snapshot、Host U/P/F model 与 Guest quota 拒绝 |
| 9-12 分钟 | Evidence Ring 与 workflow fence | challenge receipt、exact U、gap/root、retry |
| 12-15 分钟 | plain/AgentOS 同工作流对比 | 两侧原始日志、状态 compare、paired summary |

答辩时每项机制按“赛题问题、参考思想、AgentOS 特定实现、实际测试、限制”陈述，避免用功能通过数替代性能，也避免把端到端差异直接归因到单一模块。
