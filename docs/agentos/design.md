# AgentOS-uCore 系统设计

AgentOS-uCore 在 uCore 中增加一组面向 Agent workflow 的可信原语。我们把身份、权限、资源、Context、文件状态与执行证据放在内核边界内，把策略、业务流程和可选模型循环留在 Guest 用户态。这样既保留普通进程语义，也让多轮 Agent 工作流可以被约束、观察和复核。

![AgentOS-uCore 总体架构](../figures/architecture/agentos_overview.png)

[DrawIO 源文件](../figures/architecture/agentos_overview.drawio)

## 设计问题

传统进程接口能够运行程序，却不能直接回答以下问题：

| 问题 | AgentOS 的回答 |
| --- | --- |
| 谁在执行 | 可信 Agent 映像、role、capability、scope 和 lifecycle generation |
| 为什么执行 | Context cause/span/branch 与结构化请求 |
| 能否产生副作用 | schema、capability、scope、provenance 和资源包络联合检查 |
| 文件状态何时变化 | 显式 metadata catalog、索引查询与 typed watch |
| 并发 workflow 如何共享 CPU | workflow EEVDF、预算与可测量的服务量 |
| 结果如何复核 | canonical Context evidence、Evidence Ring 与 challenge-bound fence receipt |

## 分层结构

| 层次 | 主要职责 | 代表实现 |
| --- | --- | --- |
| Host | 启动 QEMU、采集串口、保存结果；可选地处理 TLS、API key 和 provider JSON | `host_tools/`、`scripts/` |
| Guest 产品 | 组织确定性 workflow、交互循环和多 Agent 协作 | `labdemo_ucore`、`agentlive_ucore`、`agentnexus_ucore` |
| 用户态 ABI | 提供 V1/V2/V3、Context、文件查询、事件和 Task Channel 包装 | `user/include/agent.h`、`user/lib/` |
| AgentOS 内核 | 验证身份、权限、生命周期、资源、来源与执行合同 | `os/agent_core.c`、`os/workflow_lifecycle.c`、`os/syscall.c` |
| uCore 基础层 | 进程、调度、VFS、IPC、VirtIO 和页表 | uCore 原有子系统及项目内适配 |

Host 不读取 Guest 业务文件，也不替 Guest 选择或执行工具。内核不运行模型，不解析 provider 协议，也不理解具体科研业务。

## 可信生命周期

我们用 `workflow id + generation` 作为跨模块主键。generation 阻止 PID、slot 或 inode 复用造成的旧状态重放。

一次 workflow 的主路径如下：

1. controller 通过可信映像创建 Agent，并建立 workflow lifecycle；
2. 内核为成员绑定 role、capability、scope 和资源账户；
3. Guest 发布 Context、文件 metadata、watch 或执行合同；
4. 工具调用在副作用前完成身份、schema、权限、来源和额度检查；
5. Context 与关键状态进入 canonical evidence；
6. controller 请求 workflow fence，内核排空 metadata/live-query 工作，精确结算资源并封存证据；
7. controller 取得 receipt 后关闭成员和 workflow；
8. lifecycle 在成员和活动操作归零后回收。

旧 generation 的 watch、合同、Task Channel 和 fence 请求都返回结构化错误，不会自动绑定到新 workflow。

## 身份与 Context

Agent 由受控入口创建，普通 `fork` 不能自行获得 Agent 身份或 capability。身份随进程继承规则、exec policy 和 workflow scope 一起检查。

每个 Agent 映射 7 页 Context 区：6 页由内核发布并保持用户只读，第 7 页作为用户 cache。Context 记录使用单调 sequence，并携带 cause、span、branch、tool、status 和 provenance。用户态可以查询、取得 detail、建立分支或回滚到仍在窗口内的 sequence；FIFO 淘汰和 dropped 计数明确暴露。

Context Path 解决进程内因果历史，Evidence Ring 解决 workflow 级封存。两者共享事实来源，但用途不同。

## 工具调用与执行合同

公开目录包含 25 项工具。三条调用路径服务不同负载：

| 路径 | 使用场景 | 约束 |
| --- | --- | --- |
| V1 | 固定旧 ABI 兼容 | 定长参数，保留用于已有 Guest |
| V2 | 动态 Agent Loop | typed 参数数组，逐次检查 schema、capability 和 scope |
| V3 | 预先冻结的高保证 DAG | 在 V2 前缀上增加合同、节点、attempt、deadline、来源和输入 fingerprint |
| compact batch | 同步、短小、已知顺序的批量操作 | 最多 64 项，减少 syscall 次数 |

ENFORCE V3 合同每个 lifecycle 最多 24 个拓扑有序节点。节点冻结工具、schema digest、前驱、来源、artifact 类型、deadline、重试策略和资源包络。内核只在前驱成立、输入匹配且 effect gate 通过后开始副作用；合法重试可以读取已完成终态。

## 资源与调度

Workflow Credit Domain 用三个状态表示硬额度：

```text
F: free credit
P: reserved but not published
U: charged to a live object
held = U + P + F <= account, class and global limits
```

F 是该账户已经持有但尚未使用的 free credit，因此 held 可以低于配置上限。创建对象先执行 `F -> P`，发布后执行 `P -> U`，失败路径退回 `F`，析构执行 `U -> F`。Tool Phase Credit Lease 从已计入的 U 中锁定短峰值，不扩大 workflow 的硬上限。

workflow EEVDF 以 workflow 为公平实体，结合 weight、priority、budget、deadline、ready age 与 vruntime 选择运行对象。当前内核为单 Hart，活跃实体上限为 4；这些边界由实现常量和测试固定。

## 文件状态与实时查询

Agent Live-Query FS 只索引通过 API 显式登记的 volatile metadata。catalog 绑定 `dev + inode + incarnation`，支持 `status`、`stage` 和 `kind` 索引，也保留等语义 traversal 路径用于对照。

typed watch 保存完整结构化谓词。metadata 更新后，内核比较 before/after membership，并产生 `ENTER`、`UPDATE` 或 `LEAVE`。队列溢出或 generation 变化时，watch 标记 `RESYNC_REQUIRED`；用户态重新查询完整集合并显式确认 resync。

索引不扫描任意磁盘内容，不提供全文检索，也不跨重启持久化。

## 证据与 fence

普通成功 Context 只写一次 canonical Evidence Ring 事件。关键拒绝、失败和 fence 记录使用独立容量，避免普通成功流量挤掉安全证据。ring 记录 sequence、actor、tool、status、provenance、payload 摘要和前向 hash。

workflow fence 依次固定 metadata generation、资源账户和 evidence 范围，返回 320 字节 receipt。receipt 绑定调用者 challenge、lifecycle key、事件范围、dropped 计数、资源摘要、previous root 与 evidence root。任何尚未排空的 live-query、credit 或 evidence 状态都会返回 `RETRY`，不会发布一个看似完整的 cut。

## Task Channel

Task Channel 是按需建立的 single-issuer SQ/CQ core。SQ 与 CQ 各 16 个 128 字节槽；SQ 对用户可写，CQ 只读，另有两个内核私有页保存权威状态。request id、ring generation 和 slot generation 共同防止 ABA 与重放。

内核在消费前复制完整 SQE。每个已接受目标 request 只发布一个 terminal CQE；cancel、deadline、CQ backpressure 和 sticky resync 都有明确状态。typed resource handle 为 16 字节，私有表容量为 8。

当前 bridge 只有同步 provider，接受 null input 并返回 artifact `NONE`。`RESOURCE_IMPORT` fail closed，尚未开放业务 payload backend 或动态 provider registration。Task Channel 也不承载模型 prompt/response。

## 产品路径

| 产品入口 | 解决的问题 | 控制方式 |
| --- | --- | --- |
| `labdemo_ucore` | 六项赛题能力的确定性综合验收 | Guest 固定 policy |
| `agentlive_ucore` | 多轮工具结果驱动的交互循环 | Guest 保存历史并执行 V2；Host 只转发模型协议 |
| `agentnexus_ucore` | 四业务 Agent 的委派、工件和汇总 | Guest `N1` TASK envelope over kernel `MESSAGE` |

offline replay 和 live provider 经过相同串口 frame 与 Guest 状态机。仓库保存的验收证据来自 replay；只有实际完成外部 provider 往返的单次运行才可称为 live 结果。

Nexus `N1` TASK、内核 `MESSAGE`、Task Channel SQ/CQ、V3 执行合同和 MCP/A2A prototype 是不同协议层。MCP/A2A 代码目前只做用户态 in-memory 对象映射，不包含 HTTP server、streaming、OAuth/JWS 或跨实现互操作。

## 实现与验证

| 模块 | 主要源码 | 主要验证 |
| --- | --- | --- |
| 身份、lifecycle、资源 | `os/agent_identity.c`、`os/agent_lifecycle.c`、`os/workflow_credit_domain.c`、`os/resource_controller.c` | `agenttrust_ucore`、`agentscope_ucore` |
| Context、provenance、evidence | `os/agent_context_path.c`、`os/agent_provenance.c`、`os/agent_evidence_ring.c` | `agentfinal_ucore`、`scripts/test-agent-evidence-ring.py` |
| 工具与合同 | `os/agent_core.c`、`os/agent_execution_contract.c` | `agenttoolabi_ucore`、`agentcontract_ucore` |
| Live Query | `os/agent_metadata.c`、`os/agent_metadata_query.c`、`os/agent_live_query_events.c` | `agentfs_ucore`、`agentbench_ucore`、`scripts/check-agent-live-query-fs.py` |
| 调度与 Task Channel | `os/workflow_scheduler.c`、`os/agent_task_channel.c` | `agent_eevdf_ucore`、`agenttask_ucore` |

```bash
make agent-module-check
make agentos-test TOOLPREFIX=riscv64-linux-gnu-
make contest-demo TOOLPREFIX=riscv64-linux-gnu-
```

2026-08-11 的一次性活动保存了 30 次 fresh QEMU boot 和 7,498 行逐样本数据。它支持 indexed workflow core interval、Task 延迟和 workflow EEVDF 的量化结论；完整口径见[实测性能结果](../contest/performance-results.md)。该 core interval 包含 query、recovery write、`fsync` 和 verify。

## 当前边界

- Guest 为 RISC-V64 单 Hart；SMP 和裸机性能未纳入本轮数据。
- Context、metadata、Evidence Ring、合同和 Nexus artifact 均属于当前启动周期的有界状态。
- indexed 结论限定在 workflow core path；同一活动的端到端结果没有显示整体加速。
- Task Channel 尚无业务 payload backend。
- 默认可复验产品证据来自 replay，不代表外部模型质量。
- observer 会产生读取和串口扰动，不用于性能测量。

字段和结构体以 [API 参考](api.md)及公开 ABI 头文件为准；安全检查见[安全加固](security-hardening.md)，复现顺序见[验证说明](verification.md)。
