# 任务六：综合工作流与执行合同

## 场景与约束

任务六需要把身份、工具、Context、文件查询和事件循环组合成可运行程序，并提供可复核的对照结果。项目还需要区分三种执行方式：确定性主演示、预声明高保证合同和可选模型主导循环。三者共用内核工具与证据能力，但采用不同的控制入口。

## 综合场景

`labdemo_ucore` 运行确定性科研恢复流程：

1. 受控创建 Agent 并取得 workflow 身份；
2. 记录 Context cause/span/branch；
3. 登记科研文件 metadata，执行 traversal 或 indexed query；
4. 通过 event wait 与 IPC 协作；
5. 执行恢复写入和复核；
6. 验证 capability 拒绝与审计视图；
7. 输出同一 workload fingerprint 下的可配对测量。

`make contest-demo` 自动构建 QEMU Guest 并保存逐样本结果。`make dual-platform-run` 让 plain uCore 与 AgentOS-uCore 执行同一科研合同，Host 只负责启动、采集和对齐结果。

## ENFORCE V3 执行合同

固定工作流可以在 lifecycle generation 内冻结一份合同。合同最多 24 个拓扑有序节点，所有 predecessor 只能引用编号更小的节点。每个节点声明：

| 合同维度 | 冻结内容 |
| --- | --- |
| 工具 | tool id、32 字节 schema digest、required capability 和 side-effect mask |
| 依赖 | predecessor mask、source node 和 source Context sequence |
| 数据 | input/output artifact type、accepted/output provenance 标签、32 字节 input fingerprint |
| 资源 | exec/storage envelope 与 charge class |
| 控制 | deadline、最大 attempt、retry mask 和 cancel policy |

启用 `AGENT_EXECUTION_CONTRACT_F_ENFORCE` 的 V3 调用依次验证 lifecycle、合同/节点、依赖、schema、capability、provenance、资源和 deadline。内核在副作用前预留 Evidence ticket 并建立 Tool Phase Credit Lease，执行完成后结算资源、提交 Context/Evidence，再发布节点终态。合法重试命中完成缓存并返回原终态。

Phase Lease 从 workflow 已计入 U 的 credit 锁定资源，不增加硬额度。claim 在对象发布前取得 nonce；失败路径 refund，已发布对象在析构时执行 `U -> F`，未使用锁定量在 settle 时归还 F。

## Task Channel

每个 Agent 按需建立 single-issuer SQ/CQ，计费 4 页：

| 页 | 用户权限 | 内容 |
| --- | --- | --- |
| SQ | read/write | 16 个 128 字节 SQE 与 ring header |
| CQ | read-only | 16 个 128 字节 CQE 与 ring header |
| request private | 不映射 | 权威水位、request 状态、issuer 与 deadline |
| resource private | 不映射 | handle generation、owner、digest 与 provenance |

内核消费前复制完整 SQE，此后只验证私有副本。request id 严格递增；共享水位或 generation 不一致时进入 sticky resync，issuer 通过显式 `RESYNC` 重建可见 header。

每个已接受目标 request 只发布一个 terminal CQE。cancel 引用目标 request id：effect fence 前可以成为目标终态，effect 开始后返回 too-late/denied。timer 只标记 deadline，到进程下一个 schedulable safe point 完成结算。typed resource handle 固定为 16 字节，私有表为 8 槽。

## 可选模型循环

`agentlive_ucore` 在 Guest 保存 prompt、whole-turn history、tool catalog、round 和 correlation。Guest relay 校验模型返回的单个 `tool_use` 或 `final`，main Agent 执行 V2 typed 工具，再把真实 Context 和 result 回到下一轮。

Host relay 只处理 QEMU 串口、TLS、API key 和 provider JSON。它不读取 Guest 业务文件，也不选择或执行工具。串口 frame 绑定 session、双向 sequence、kind、length 和 SHA-256；offline replay 经过同一 wire。默认 replay 为 6 轮，live provider 需要显式选择。

`host_tools/mcp_a2a_gateway.py` 另行映射 MCP tool/task 与 A2A Task/Context/Message/Artifact 对象，并使用 deterministic in-memory transport 验证状态机。该 prototype 不参与主演示或内核 SQ/CQ。

## 关键实现

| 职责 | 源码 |
| --- | --- |
| 主演示 Guest | [user/src/labdemo_ucore.c](../../user/src/labdemo_ucore.c)、[host_tools/contest_demo.py](../../host_tools/contest_demo.py) |
| plain/AgentOS 同合同 | [baseline_ucore](../../baseline_ucore)、[user/src/rp_agentos_orch.c](../../user/src/rp_agentos_orch.c) |
| 执行合同与 Phase Lease | [agent_execution_contract_abi.h](../../agent_execution_contract_abi.h)、[os/agent_execution_contract.c](../../os/agent_execution_contract.c) |
| Provenance effect gate | [agent_provenance_abi.h](../../agent_provenance_abi.h)、[os/agent_provenance.c](../../os/agent_provenance.c) |
| Task SQ/CQ | [agent_task_channel_abi.h](../../agent_task_channel_abi.h)、[os/agent_task_channel.c](../../os/agent_task_channel.c)、[os/agent_task_bridge.c](../../os/agent_task_bridge.c) |
| Guest model loop与 Host relay | [user/src/agentlive_ucore.c](../../user/src/agentlive_ucore.c)、[host_tools/guest_llm_relay.py](../../host_tools/guest_llm_relay.py) |
| MCP/A2A prototype | [host_tools/mcp_a2a_gateway.py](../../host_tools/mcp_a2a_gateway.py)、[host_tools/agent_task_transport.py](../../host_tools/agent_task_transport.py) |

## 验证与量化

```bash
make contest-demo TOOLPREFIX=riscv-none-elf-
make dual-platform-run TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
make agent-live-demo-check
make agent-live-demo
```

2026-08-11 规范活动完成 30 个 fresh QEMU boot，保留 33 个 raw 文件、19 张长表和 7,498 行数据。16 个 traversal/indexed 配对中，indexed core interval 全部更短，配对 speedup 中位数为 `3.118x`；端到端中位差值为 indexed 慢 `13.452 ms`，因此性能结论限定在 core path。完整口径见 [高级性能图](advanced-performance-figures.md)、[manifest.json](../../one_shot_metrics/data/20260811/manifest.json) 和 [validation.json](../../one_shot_metrics/data/20260811/validation.json)。

## 当前边界

| 能力 | 当前范围 |
| --- | --- |
| 主演示 | 使用确定性 Guest policy，可离线复验，不依赖模型 API |
| ENFORCE V3 | 每 lifecycle 一份合同，最多 24 节点和 48 个 accepted attempt 终态槽 |
| V1/V2 | 保留 schema、capability 和 scope 检查，不携带冻结 DAG/provenance envelope |
| Deadline/cancel | 在 effect fence 和 schedulable safe point 结算，不提供 wall-clock completion bound |
| Task provider | 同步、null input、artifact `NONE`；无动态 provider registration 或业务 payload backend |
| Exactly-one CQE | 约束内核终态发布，不覆盖远程服务的分布式 exactly-once |
| Model replay | 验证同串口协议与 Guest loop，live 云模型结果需单独运行 |
| MCP/A2A | 用户态 in-memory 对象映射；无 HTTP server、streaming、OAuth/JWS、外部互操作或内核 adapter |

公开思想与 clean-room 边界记录在 [../../NOTICE](../../NOTICE)。协议与完整字段定义见 [api.md](api.md)。
