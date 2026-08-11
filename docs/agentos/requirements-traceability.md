# 赛题要求追踪

本文把六项赛题目标映射到当前实现、源码和运行证据。`完成` 表示已有生产路径和对应 Guest 验证；静态 checker 只证明结构约束，不单独提升任务状态。

## 目标到证据矩阵

| 任务与目标 | 状态 | 当前实现 | 主要源码 | 验证证据 | 量化结果 | 当前边界 |
| --- | --- | --- | --- | --- | --- | --- |
| 1. 创建 Agent；区分普通进程；提供 Context 区和资源隔离 | 完成 | 受控映像身份、role/capability/scope、workflow `id+generation`、7 页 Context、exec/storage U/P/F 账户、fork/exec/exit gate | [agent_identity.c](../../os/agent_identity.c)、[workflow_lifecycle.c](../../os/workflow_lifecycle.c)、[workflow_credit_domain.c](../../os/workflow_credit_domain.c)、[proc.c](../../os/proc.c) | `agenttrust_ucore`、`agentfinal_ucore`、`agentscope_ucore`；`test-workflow-credit-domain.py`、`test-workflow-syscall-cut.py` | Context 为 6 页可信只读区 + 1 页用户 cache；最多 4 个 active workflow | 单 Hart；身份、receipt 和 generation 属于当前启动周期 |
| 2. 提供至少 3 个结构化工具、发现接口和错误处理 | 完成 | 25 项 name/id 工具目录；V1 兼容调用、V2 typed-KV、ENFORCE V3、64 项 compact batch、16 槽 Task SQ/CQ | [agent_tool_protocol.c](../../os/agent_tool_protocol.c)、[agent_core.c](../../os/agent_core.c)、[agent_execution_contract.c](../../os/agent_execution_contract.c)、[agent_task_channel.c](../../os/agent_task_channel.c) | `agenttoolabi_ucore`、`agentsecurity_ucore`、`agentcontract_ucore`、`agenttask_ucore` | 一次性活动每条路径保留 32 个 16-op sequence；batch/scalar V3/SQ-CQ 中位耗时为 `561/2051/1620.5 us` | batch 顺序同步；V1/V2 无冻结 DAG；Task provider 只处理 null/NONE |
| 3. 保存至少 5 轮调用历史，支持直接读取和超长淘汰 | 完成 | 内核 Context archive、只读 mirror、user cache、cause/span/branch、hash 链、rollback 和有界 FIFO | [agent_context.c](../../os/agent_context.c)、[agent_context_path.c](../../os/agent_context_path.c)、[agent_provenance.c](../../os/agent_provenance.c) | `agentfinal_ucore`；`test-context-evidence-atomicity.py`、`test-context-snapshot-reader-atomicity.py` | `agentfinal_ucore` 一次提交 64 个顺序调用，再验证 snapshot、rollback 和淘汰 | rollback 只移动 active path；user cache 不参与授权；历史窗口有界 |
| 4. 扩展文件属性/摘要/查询能力，并比较索引与遍历 | 完成 | 显式 metadata、`dev+inum+incarnation`、status/stage/kind 索引、scan/index planner、typed live watch、generation resync | [agent_metadata_catalog.c](../../os/agent_metadata_catalog.c)、[agent_metadata_query.c](../../os/agent_metadata_query.c)、[agent_metadata_objects.c](../../os/agent_metadata_objects.c)、[agent_live_query_events.c](../../os/agent_live_query_events.c) | `agentfs_ucore`、`agentbench_ucore`；`test-agent-live-query-fs.py` | `3 x 4` catalog/hit 网格每格 15 对；median speedup `1.164x-2.808x`；780 个 AgentEval pair fingerprint 等价 | 只查询显式登记对象；catalog boot-scoped；一次最多返回 8 hits |
| 5. 提供至少两类 Agent Loop 机制，支持休眠、唤醒和多 Agent 稳定运行 | 完成 | 16 槽 event queue、watch/wait、heartbeat、可信 IPC、LLM correlation、workflow EEVDF、Evidence Ring 与 fence | [agent_ipc.c](../../os/agent_ipc.c)、[workflow_scheduler.c](../../os/workflow_scheduler.c)、[agent_evidence_ring.c](../../os/agent_evidence_ring.c)、[agent_workflow_fence.c](../../os/agent_workflow_fence.c) | `agentloop_ucore`、`agentsched_ucore`、`agent_eevdf_ucore`；`test-agent-evidence-ring.py`、`test-workflow-fence.py` | 504 条 exact wake probe：425 条 0 tick、79 条 1 tick；并发 1-4 的 Jain 中位数均高于 `0.99998` | 内核不做模型推理；EEVDF 总 cap 为 4；evidence 为当前启动周期的有界窗口 |
| 6. 整合至少 3 个模块，提供 QEMU 综合程序和性能对比 | 完成 | `labdemo_ucore` 串联身份、Context、metadata/index、event/wait、IPC、capability denial 和 audit；另有 plain/AgentOS 对照、V3 合同和可选 model loop | [labdemo_ucore.c](../../user/src/labdemo_ucore.c)、[contest_demo.py](../../host_tools/contest_demo.py)、[rp_agentos_orch.c](../../user/src/rp_agentos_orch.c)、[agentlive_ucore.c](../../user/src/agentlive_ucore.c) | `make contest-demo`、`make dual-platform-run`、`make agent-live-demo` | 规范活动完成 30 个 fresh boot、33 个 raw 文件、7,498 行和 10 组图；16/16 core 配对 indexed 更快，median speedup `3.118x` | 主演示使用确定性 policy；core 改善不外推到端到端；replay 不计作 live provider 实测 |

## 共同工程合同

| 合同 | 当前实现 | 代表检查 |
| --- | --- | --- |
| UAPI 稳定 | kernel/user 共享 ABI，布局快照受版本控制 | `make agent-uapi-check` |
| 模块边界 | identity、Context、metadata、Evidence、fence 与 Task owner 分离 | `make agent-module-check` |
| Scope 与 generation | 敏感对象和 IPC 重验 lifecycle、control id、scope 与 incarnation | `agentsecurity_ucore`、`agentscope_ucore` |
| 副作用前授权 | ENFORCE V3 检查 frozen edge、schema、capability、provenance 与 effect mask | `agentcontract_ucore` |
| 可核验证据 | Context canonical event 进入 Evidence Ring，fence 绑定 challenge、credit 和 metadata cut | `test-agent-evidence-ring.py`、`test-workflow-fence.py` |
| 可复核运行 | Host runner 保存退出状态、panic/timeout 判定、逐样本原始输出和结构化表 | `contest-demo`、[one_shot_metrics](../../one_shot_metrics/README.md) |

## 代表命令

```bash
make agent-module-check TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentfinal_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentfs_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentloop_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
make contest-demo TOOLPREFIX=riscv-none-elf-
make dual-platform-run TOOLPREFIX=riscv-none-elf-
```

完整运行顺序见 [verification.md](verification.md)。2026-08-11 数据活动的 [manifest](../../one_shot_metrics/data/20260811/manifest.json) 记录命令、环境和 raw SHA-256；[validation](../../one_shot_metrics/data/20260811/validation.json) 为 `valid=true`、`ready=true`，包含 0 个 error 和 1 个已披露 serial-fragment warning。

## 结果解释

- checker、编译、Guest 和 paired measurement 属于不同证据层级；表中功能状态至少有 Guest 路径支撑。
- paired speedup 只解释相同 workload/result fingerprint 和相同计时窗口。
- traversal/indexed 的 core median speedup 为 `3.118x`；端到端中位差值为 indexed 慢 `13.452 ms`。
- Task 三条路径执行等价空 ECHO，但 wire、描述符和复制范围不同。
- 16 档 EEVDF 数据由四波组成，每波为 bootstrap 加 3 个 fresh workflow。
- Task 的一个 terminal CQE 只约束内核终态发布；hard deadline 在 schedulable safe point 结算。
- MCP/A2A prototype、offline model replay 和 live provider 运行分别记录，互不代替。
