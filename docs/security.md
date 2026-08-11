# AgentOS 安全机制

Agent workflow 会长期持有身份、文件对象、工具结果和异步请求。进程槽、inode、ring slot 和合同节点都会复用，用户缓冲区还可能在 syscall 期间变化。AgentOS 将安全状态绑定到 workflow lifecycle，并让 schema、capability、scope、Execution Contract、来源标签与资源额度在副作用发生前共同完成准入。

## 文档索引

- [受保护对象](#受保护对象)
- [请求检查链](#请求检查链)
- [身份、Lifecycle 与 Scope](#身份lifecycle-与-scope)
- [Schema 与 Capability](#schema-与-capability)
- [Execution Contract 与 Effect Gate](#execution-contract-与-effect-gate)
- [Provenance 传播](#provenance-传播)
- [资源准入与提交](#资源准入与提交)
- [文件对象与 Live Query](#文件对象与-live-query)
- [Task Channel 协议](#task-channel-协议)
- [失败状态与验证](#失败状态与验证)

## 受保护对象

| 对象 | 内核身份 | 核心检查 |
| --- | --- | --- |
| Agent | Agent id、control id、role、image identity | 受控创建、可信 executable、capability |
| Workflow | lifecycle `{id, generation}`、scope id | active/closing 状态、operation 引用与回收 |
| 工具请求 | tool id/name、schema digest、request id | 私有 copyin、typed schema、目录 manifest |
| 合同节点 | contract generation、node、attempt | 前驱、输入 fingerprint、deadline、retry/cancel policy |
| 文件对象 | `dev + inum + incarnation`、fs generation | VFS scope、metadata snapshot、edit version |
| Live watch | target/control id、lifecycle、scope、watch id | typed predicate、增量 generation 与 resync |
| Task Channel | owner、channel/ring/slot generation | single issuer、CQ 只读、权威水位与 terminal CQE |
| Typed handle | slot、type、ownership、generation | owner lifecycle、引用和析构 |
| 资源账户 | account、charge class、credit epoch | reservation、phase lease、publish/refund |
| Workflow fence | challenge、request id、fence sequence | metadata、资源和执行记录的一致 cut |

这些 key 都包含代际信息。数值槽位相同但 generation 不同的对象不会互相授权。

## 请求检查链

工具调用的关键顺序位于 [`os/agent_core.c`](../os/agent_core.c) 的 `agent_execute_one()`。系统先取得 Context commit lane，再执行合同和来源准入，随后锁定 phase credit；只有 effect gate 返回 execute，owner 才能产生副作用。

```text
copyin request / descriptor
    -> version、size、flags、reserved 与字符串终止位置
    -> Agent identity + workflow lifecycle
    -> tool id/name + typed schema
    -> lifecycle operation gate
    -> contract node / predecessor / attempt / deadline
    -> provenance manifest + required capability
    -> Tool Phase Credit Lease
    -> effect gate
    -> tool owner + VFS scope/object recheck
    -> provenance output + resource settle
    -> Context / terminal state commit
```

实际代码保持同样的先后关系：

```c
admission = agent_execution_contract_admit(...);
status = agent_provenance_authorize_tool(...);
resource_phase_lease_begin(...);
effect_admission = agent_execution_contract_effect_begin(&claim);

agent_execute_op(p, op, res);
resource_phase_lease_settle(&phase_lease, phase_generation);
agent_provenance_commit_tool_output(...);
agent_execution_append_terminal(...);
```

V2 参数数组、V3 binding 和 Task SQE 都在检查前复制到内核私有结构。Task Channel 每次复制完整 128 字节 SQE，并在 validation/submit 阶段只读取该副本。

## 身份、Lifecycle 与 Scope

`agent_workflow_create()` 建立 controller 和 lifecycle root；同一 workflow 中具备 `AGENT_CAP_ORCHESTRATE` 的 Agent 可调用 `agent_worker_create()`，内核核对 capability mask、workflow scope 与 executable identity。Agent PCB 同时保存 control id 和 lifecycle key，IPC、watch、Context 与资源账户都从这份身份派生。

Lifecycle operation gate 处理关闭与并发调用：

1. active lifecycle 接受新的 operation 并增加引用；
2. close 将 lifecycle 转入 closing，停止普通新操作；
3. 已接受 Task、metadata delta、成员退出和资源析构继续完成；
4. 引用归零后清理 scope、合同、watch、Task Channel 与资源账户，再推进 slot generation。

旧对象在不同 owner 路径中返回 `STALE` 或 `NOT_FOUND`。返回值取决于调用语义，例如旧合同 key 为 stale，已经删除的 query target 可以是 not found；过期 edit lease 清理后返回 not found，incarnation 或 expected version 不匹配返回 stale。

`agent_scope_delegate_fd()` 对单个文件描述符执行委派。VFS 在 open/read/write、metadata 变更、unlink 和编辑租约路径继续检查动态 scope，使工具调用无法用已通过的上层检查绕开文件 owner。

## Schema 与 Capability

Role 表达 workflow 职责，capability 控制具体动作。当前位包括 metadata/content/process read、message send、watch、action/artifact/audit write、metadata write、orchestrate、LLM relay、wait cancel 和 route manage。部分兼容名称映射到相同 capability，例如 dependency update 使用 `META_WRITE`。

工具目录的 manifest 将 schema 与安全属性放在同一注册表中：

| Manifest 字段 | 用途 |
| --- | --- |
| `required_capabilities` | 当前 Agent 必须具备的能力集合 |
| `accepted_input_labels` | 本工具可接受的来源标签 |
| `output_add_labels` | 工具结果增加的标签 |
| `side_effect_mask` | file、metadata、IPC、process、permission、artifact 或 watch 副作用 |

[`os/agent_tool_protocol.c`](../os/agent_tool_protocol.c) 对 V2 参数执行以下检查：

- tool id 与 name 是否解析到同一目录项；
- key 是否在该工具的稀疏规则行中；
- key 是否重复、必要参数是否缺失；
- type 与 value size 是否匹配；
- 字符串是否在声明长度内精确 NUL 终止；
- request flags 与保留字段是否合法。

目录启动自检还验证 name 唯一、tool id 连续、参数 target 不重叠。Schema digest 将工具名称、参数、capability、来源策略和 side-effect mask 一起哈希，合同无法用相同 id 替换成不同语义。

## Execution Contract 与 Effect Gate

`AGENT_EXECUTION_CONTRACT_F_ENFORCE` 冻结最多 24 个拓扑有序节点。每个节点声明 tool/schema、predecessor mask、artifact 类型、accepted labels、side effects、deadline、attempt/retry/cancel policy 和资源包络。

V3 准入逐项检查：

1. contract key 是否属于当前 lifecycle；
2. node 与 attempt 是否存在且未冲突；
3. predecessor 是否已经取得允许的 terminal 状态；
4. schema digest 与 canonical input fingerprint 是否匹配；
5. source Context sequence、source node 和 producer identity 是否对应；
6. capability、provenance、deadline 和 phase credit 是否满足节点声明。

`agent_execution_contract_effect_begin()` 是取消与副作用的分界点。effect 开始前收到合法取消，节点提交 `CANCELLED`；effect 已经开始时，取消按 too-late reason 处理，owner 结果继续进入资源和 Context 结算。完成缓存保存 accepted attempt 的 terminal result，合法 replay 复用相同 sequence/result identity，并设置 cached flag。

ENFORCE lifecycle 中的受约束直接 syscall 通过 [`agent_execution_contract_gate_direct_syscall()`](../os/agent_core.c) 进入相同控制面。缺少合同的副作用调用先追加结构化 denial：记录成功时返回 `DENIED`，安全记录槽位不足时返回 `NO_SPACE`，其它暂时无法追加记录的情况返回 `RETRY`。

## Provenance 传播

来源词汇定义在 [`include/agent_provenance_abi.h`](../include/agent_provenance_abi.h)：

| 标签 | 典型来源 |
| --- | --- |
| `KERNEL_FACT` | 内核生成的进程、调度和对象事实 |
| `TRUSTED_USER_CONTROL` | Guest 明确提供的控制输入 |
| `AGENT_DERIVED` | AgentOS owner 派生的结果 |
| `UNTRUSTED_FILE_DATA` | 文件内容与 metadata query |
| `UNTRUSTED_TOOL_OUTPUT` | 外部工具或模型返回 |
| `CROSS_AGENT_DATA` | 其他 Agent 的消息和前驱结果 |

标签按 OR 传播到后续 Context。`agent_provenance_authorize_tool()` 将当前输入标签与 manifest 的 accepted set 比较，并核对合同声明没有隐藏目录中的真实 side effect。SHA-256 固定 payload 和 canonical input fingerprint；身份、scope 与 capability 继续独立参与授权。

安全拒绝写入当前 Context 的 provenance flags，并带有稳定 reason，例如 stale lifecycle、missing contract、illegal predecessor、capability missing、provenance not accepted 或 effect mismatch。V3 response 和 Task CQE 返回对应 execution decision reason。

## 资源准入与提交

Workflow Credit Domain 维护 `held = F + P + U`：F 是账户 free credit，P 是 reservation，U 是已经计费的 live object。Account limit、resource class limit 和 global limit 同时约束 held。

```text
reserve:  F -> P
publish:  P -> U
failure:  P -> F
destroy:  U -> F
```

Execution Contract 的 exec/storage envelope 在 owner 执行前转为 Tool Phase Credit Lease。Lease 使用 workflow、node、request 和 generation 标识，begin/activate/deactivate/settle 形成唯一状态机。额度不足返回 `NO_SPACE`；失败路径归还 reservation，已发布对象由析构路径归还 U。

Context commit lane 将结果发布顺序固定为：owner 结果、phase settle、provenance output、Context/Evidence terminal、合同节点完成。受约束调用在执行前预留 terminal record，避免副作用完成后无法记录终态。Workflow close 入口标记 lifecycle 为 closing、请求成员退出并返回；closing/finalizer 阶段在成员、active operation、Task callback、资源对象和 Context 发布结算后完成回收。

Nexus artifact 文件位于用户态发布路径，不与内核 Context terminal 组成原子事务。[`agent_nexus.c`](../user/lib/agent_nexus.c) 以 workflow edit lease 串行化发布，先持久化 zero-magic header 与 payload，再写最终 header；[`agent_nexus.h`](../user/include/agent_nexus.h) 明确将 `AGENT_NEXUS_ARTIFACT_PUBLISH_IS_ATOMIC` 设为 `0`。读取方通过 magic、manifest/payload digest 和 lifecycle 复核，只接受完整终态。

## 文件对象与 Live Query

Live Query catalog 接收 `agent_file_meta_set()` 显式登记的易失 metadata。记录绑定 workflow lifecycle、scope 和 `{dev, inum, incarnation}`。uCore 在 inode 复用时递增 incarnation；达到最大值的 inode 不再复用。Metadata selector、digest cache、edit lease 与 deferred unlink 都检查完整身份。

Query snapshot 在开始和结束时核对 lifecycle 与 `fs_generation`，索引候选仍要重新执行完整 predicate 和 scope 检查。Typed watch 绑定 target control id、lifecycle、scope 和 watch id，集合变化发布 `ENTER/UPDATE/LEAVE`。

事件队列压力或增量缺口推进 sticky `resync_generation`。Agent 在旧 watch 仍活动时安装 replacement，取得未截断 snapshot 后再 ACK 指定 generation 并移除旧 watch；晚于 ACK 的新缺口继续保持 pending。单次 query 超过 8 个 hit 时保持 resync，直到 query 被收紧或 ABI 提供分页基线。编辑 commit 还要求 lease id、inode incarnation 和 `expected_version` 一起匹配。

## Task Channel 协议

Task Channel 使用 single issuer 和内核权威水位。SQ 对 issuer 可写，CQ 在用户页表中只读。每条 accepted target 只发布一个 terminal CQE，cancel command 以独立 request id 引用目标。

通道 generation 过期与协议 sticky resync 分开处理：

| 状态 | 检查 | 后续状态 |
| --- | --- | --- |
| `AGENT_TASK_CHANNEL_STALE` | `enter.generation` 不是当前 generation | 不设置 `RING_F_RESYNC`；返回当前 generation 与水位，调用方重建控制请求 |
| `AGENT_TASK_CHANNEL_STALE` | issuer identity、owner 或 lifecycle 已失效 | 不设置 `RING_F_RESYNC`；结果除 ABI version/size/status 外为零，调用方重新建立有效 owner/lifecycle |
| `AGENT_TASK_CHANNEL_STALE` | cancel 目标已被 CQ ack 或不存在 | 不设置 `RING_F_RESYNC`；cancel id 已消费，并返回当前 channel generation 与水位 |
| `AGENT_TASK_CHANNEL_RESYNC_REQUIRED` | request id 倒退、非法 SQ/CQ 水位、SQE ring/slot generation 错误、重复槽或非法 link | `protocol_faults` 增加，generation 前进，SQ 清零，sticky resync 等待显式确认 |

恢复 sticky resync 时，issuer 丢弃尚未 accepted 的 SQE，采用 enter result 中的新 generation，并提交 `ENTER_F_RESYNC`、`sq_tail=0`。普通 stale target 不增加 `protocol_faults` 或 `resync_count`。

Typed resource handle 包含 slot、type、ownership flags 与 generation。Core resource table 实现 import/release/query、引用和析构；当前同步 Task bridge 只接受 null input/output，非空 import 返回 `AGENT_TASK_CHANNEL_DENIED`。CQ full 保留 terminal pending 并施加 backpressure，消费 CQ 后继续发布。

## 失败状态与验证

| 条件 | 状态 |
| --- | --- |
| lifecycle、contract、channel 或对象 generation 过期 | `STALE` / `CONFLICT` / `NOT_FOUND` |
| capability、scope、来源或合同不匹配 | `DENIED` |
| version、size、flags、reserved 或参数错误 | `BAD_VERSION` / `BAD_SIZE` / `BAD_PARAM` |
| credit、对象表或队列容量耗尽 | `NO_SPACE` / backpressure |
| Live Query 增量出现缺口 | `RESYNC_REQUIRED` event |
| Task SQ/CQ 协议故障 | `AGENT_TASK_CHANNEL_RESYNC_REQUIRED` |
| fence、metadata 或 terminal publisher 尚未推进 | `RETRY` |
| deadline 到期 | `TIMEOUT` 或带 deadline flag 的 terminal CQE |

| 测试 | 覆盖内容 |
| --- | --- |
| [`agenttrust_ucore.c`](../user/src/agenttrust_ucore.c) | executable identity、受控创建与 launch 状态 |
| [`agentscope_ucore.c`](../user/src/agentscope_ucore.c) | VFS scope 与文件描述符委派 |
| [`agenttoolabi_ucore.c`](../user/src/agenttoolabi_ucore.c) | schema 目录与 typed 负向矩阵 |
| [`agentsecurity_ucore.c`](../user/src/agentsecurity_ucore.c) | role、capability、IPC 与副作用授权 |
| [`agentcontract_ucore.c`](../user/src/agentcontract_ucore.c) | 合同、来源、deadline、retry 与 phase credit |
| [`agentscan_ucore.c`](../user/src/agentscan_ucore.c) | 文件 identity 与 typed transition |
| [`agentfinal_ucore.c`](../user/src/agentfinal_ucore.c) | Context commit lane、资源结算与 teardown |
| [`agenttask_ucore.c`](../user/src/agenttask_ucore.c) | SQ/CQ 所有权、resync、cancel、deadline 与 terminal CQE |

```bash
make agent-uapi-check
make agent-module-check
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

系统调用字段见 [API](api.md)，模块间关系见[产品架构](architecture.md)。
