# AgentOS 安全机制

AgentOS 把 Agent workflow 的安全状态绑定到 lifecycle。副作用检查覆盖身份、generation、capability、scope、schema、execution contract、来源和资源额度。

## 受保护对象

| 对象 | 内核标识 | 检查内容 |
| --- | --- | --- |
| Agent | Agent id、controller id、role | 受控创建、可信映像、exec policy |
| Workflow | id、generation、scope | 生命周期、关闭与回收 |
| 工具请求 | tool、schema、request id | typed copyin、目录匹配、effect gate |
| 合同节点 | contract generation、node、attempt | 前驱、输入摘要、deadline、重试策略 |
| 文件对象 | dev、inode、incarnation、fs generation | scope、版本、索引与 watch resync |
| Task Channel | channel、ring、slot generation | single issuer、CQ 只读、terminal CQE |
| 资源账户 | account、class、credit epoch | 预留、计费与析构结算 |
| Workflow receipt | challenge、request id、fence sequence | metadata、资源与执行记录的一致状态 |

## 请求检查链

`os/syscall.c` 与各 AgentOS owner 按以下顺序处理请求：

1. copyin 完整 request，验证 version、size、flags 和保留字段；
2. 读取当前进程身份与 `{workflow id, generation}`；
3. 解析工具并对参数执行 schema、类型与长度检查；
4. 进入 lifecycle operation gate，执行 contract admission 与 required capability 匹配；
5. 完成来源授权并预留本次操作需要的 credit；
6. 通过 effect gate，由工具 owner 继续执行 scope 与对象检查；
7. 发布 Context、terminal 状态和资源结算结果。

检查完成后，内核只使用 request 私有副本。用户态修改原缓冲区不会改变本次决策。

## 身份与 Lifecycle

Agent 身份由 `agent_create_role()`、`agent_workflow_create()` 和 `agent_worker_create()` 等受控入口建立。worker 创建路径核对可信映像、controller、role、capability 和 scope。

所有 workflow 对象保存 lifecycle key。generation 变化后，旧 metadata watch、合同、Task Channel、resource handle、correlation 与 fence request 根据对象状态返回 `STALE`、`CONFLICT` 或 `NOT_FOUND`。这一规则同时处理内核表槽、进程槽、inode 与循环队列槽的复用。

`agent_scope_delegate_fd()` 对单个文件描述符执行委派检查。VFS 在 open、read、write、metadata 和编辑租约路径继续核对 scope，使 Agent 权限与 uCore 文件访问使用同一条检查链。

## Capability 与 Schema

role 描述 workflow 职责，capability 控制可执行动作。当前 capability 覆盖 metadata/content/process read、message send、watch、action/artifact/audit write 和 metadata write 等类别。

V2 工具目录为每个参数声明 key、type、最大长度和必要性。内核拒绝以下输入：

- 未知或重复的 key；
- type 与 schema 不一致；
- 字符串缺少终止符或超出长度；
- `tool_id` 与 `tool_name` 指向不同目录项；
- 未定义 flags 或非零保留字段。

直接文件写入、route 修改和调度配置也进入副作用分类检查，使工具与直接 syscall 使用一致的授权规则。

## Execution Contract

ENFORCE 合同冻结最多 24 个拓扑有序节点。每个节点记录 tool、schema digest、predecessor mask、artifact 类型、deadline、cancel/retry policy 和资源包络。

V3 调用继续匹配以下运行字段：

1. contract key 与当前 lifecycle；
2. node id 与合法 attempt；
3. predecessor terminal 状态；
4. canonical input fingerprint；
5. source Context sequence 与 producer；
6. deadline 与 phase credit。

effect gate 前的取消可以把节点转入 `CANCELLED`。副作用开始后的取消返回对应 too-late reason，节点按实际执行结果结算。completion cache 保存已完成节点的终态，合法重试获得同一结果。

## 来源传播

文件读取、工具结果与 IPC 将来源位写入后续 Context 和工具输入。标签采用 OR 传播，策略要求的 accepted labels 在 effect gate 前检查。SHA-256 固定 payload 字节和 input fingerprint，授权继续由身份、scope、capability 和来源共同决定。

主要标签包括 `KERNEL_FACT`、`TRUSTED_USER_CONTROL`、`AGENT_DERIVED`、`UNTRUSTED_FILE_DATA`、`UNTRUSTED_TOOL_OUTPUT` 和 `CROSS_AGENT_DATA`。

## 资源准入

Workflow Credit Domain 维护 `held = F + P + U`。F 表示账户 free credit，P 表示 reservation，U 表示 charged live object。account、resource class 和 global limit 同时约束 held。

```text
reserve:  F -> P
publish:  P -> U
failure:  P -> F
destroy:  U -> F
```

Tool Phase Credit Lease 从 U 中锁定执行阶段额度。每个 claim 使用 nonce，publish、failure 和 destroy 进入唯一结算路径。资源不足返回 `NO_SPACE`，已经发布的对象保持原状态。

workflow 关闭时，内核核对成员、active operation、resource account、Task Channel 和后台工作。所有引用归零后回收 lifecycle。

## Context 与 Workflow receipt

Context 的 6 个内核页对用户保持只读，cache 使用独立页面。奇偶 publication sequence 让读取方识别并发写入。rollback 调整 branch head，历史 record 的 sequence 和 hash 保持原值。

有界执行记录使用 ordinary 与 critical 槽保存成功、拒绝、失败、fence 和 fallback 事件。workflow fence 取得 metadata generation、精确 credit cut 并封存当前有序记录，成功后发布 320 字节 receipt；处理中状态返回 `RETRY`。receipt 绑定 32 字节 challenge、request id、previous root 和本次 root，并通过 flags 标记 partial coverage 与 volatile metadata。

## 文件状态

Live Query catalog 接收 `agent_file_meta_set()` 显式登记的 metadata。记录绑定 `dev + inode + incarnation`，文件删除或 inode 复用产生新的对象身份。

typed watch 根据查询集合变化发布 `ENTER`、`UPDATE` 或 `LEAVE`。事件队列压力、generation 变化与增量缺口设置 `RESYNC_REQUIRED`；全量 query 后使用 `ACK_RESYNC` 更新基线。

编辑租约保存 owner、scope、lease id、version 和 TTL。commit 要求 lease 与 `expected_version` 同时匹配，并发更新返回冲突状态。

## 事件与 IPC

事件队列容量为 16，其中 4 个槽保留给内核事件，来源类别另有保留额度。`agent_wake()` 允许注入的事件集合固定，file-query、policy-denied 等内核来源由对应模块发布。

跨 Agent `MESSAGE` 与 `LLM_DONE` 需要显式 route。route 检查 source、target、event mask、active workflow 和双方身份。wait、cancel 与 heartbeat 绑定当前 Agent incarnation，timer IRQ 标记到期并唤醒，回收与记录提交在可调度检查点完成。

## Task Channel

Task Channel 使用 single issuer 与内核权威水位。SQ 对 issuer 可写，CQ 对用户只读；内核消费前复制完整 128 字节 SQE。

request id、channel generation、ring generation 和 slot generation 共同处理 ABA。每个 accepted target 发布一个 terminal CQE。cancel 使用独立 request id 引用目标，CQ full 触发 backpressure，水位异常进入 sticky resync。

typed resource handle 包含 slot、type、flags 和 generation。resource control 对 release 与 query 检查 lifecycle 和 handle generation，析构路径回收表内资源。

## 失败状态

| 条件 | 状态 |
| --- | --- |
| lifecycle 或对象 generation 过期 | `STALE` / `CONFLICT` / `NOT_FOUND` |
| capability、scope、来源或合同不匹配 | `DENIED` |
| 版本、尺寸、flags 或参数错误 | `BAD_REQUEST` / `BAD_*` |
| credit、队列或对象表容量耗尽 | `NO_SPACE` / backpressure |
| watch 增量出现缺口 | `RESYNC_REQUIRED` |
| fence 仍在排空或结算 | `RETRY` |
| deadline 到期 | `TIMEOUT` 或带 deadline flag 的 terminal CQE |
| effect gate 后收到取消 | too-late reason |

## 验证入口

| 检查内容 | Guest 用例 |
| --- | --- |
| 身份、映像与 scope | `agenttrust_ucore`、`agentscope_ucore` |
| schema 与工具权限 | `agenttoolabi_ucore`、`agentsecurity_ucore` |
| 合同与 effect gate | `agentcontract_ucore` |
| credit 与 teardown | `agentfinal_ucore` |
| Task Channel | `agenttask_ucore` |

```bash
make agent-uapi-check
make agent-module-check
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

系统调用字段见 [API](api.md)，模块关系见[产品架构](architecture.md)。
