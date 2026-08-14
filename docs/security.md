# AgentOS 安全机制

Agent（智能体）工作流会同时持有身份、文件、工具结果和异步任务。Agent 进程创建与地址空间设计先确定“谁在运行、哪些页由谁写”，工具调用协议再确定“请求如何解析、什么操作可以生效”。进程槽、inode、Task Channel 环槽和执行节点都可能被复用，用户缓冲区也可能在系统调用尚未结束时发生变化。AgentOS 为这些对象附上工作流生命周期与 generation；请求进入内核后，先核对 Agent identity 与生命周期，再解析工具和 schema，随后检查能力位、文件访问范围、Execution Contract、provenance（来源追溯）和资源额度，全部通过后才允许工具产生副作用。

## 文档索引

- [受保护的对象](#受保护的对象)
- [请求检查顺序](#请求检查顺序)
- [身份、生命周期与文件访问范围](#身份生命周期与文件访问范围)
- [schema 与能力](#schema-与能力)
- [Execution Contract 与副作用检查](#execution-contract-与副作用检查)
- [Provenance label](#provenance-传播)
- [资源准入与 terminal commit](#资源准入与-terminal-commit)
- [文件对象与 Live Query](#文件对象与-live-query)
- [Task Channel 协议](#task-channel-协议)
- [失败状态与测试](#失败状态与测试)

<a id="受保护对象"></a>
## 受保护的对象

| 对象 | 内核用来识别对象的信息 | 主要检查 |
| --- | --- | --- |
| Agent | Agent 编号、控制编号、角色、程序映像身份 | 受控创建、可信可执行文件、能力集合 |
| 工作流 | 生命周期 `{id, generation}`、文件访问范围编号 | 是否仍在运行、操作引用和回收状态 |
| 工具请求 | 工具编号和名称、schema digest、请求号 | 私有复制、带类型信息的参数、Tool Registry 清单 |
| 执行节点 | Execution Contract generation、节点号、尝试次数 | 前驱、输入指纹、截止时间、重试和取消规则 |
| 文件对象 | `dev`、`inum`、`incarnation`、文件更新序号 | 文件访问范围、元数据快照、编辑版本 |
| Typed Watch | 目标控制编号、生命周期、文件访问范围、订阅编号 | 查询条件、目录 generation 和重新同步状态 |
| Task Channel | 所有者、通道 generation、`slot_generation` | 唯一提交者、只读 CQ、内核水位和完成结果 |
| 类型化句柄 | 槽位、类型、所有权、generation | 所属生命周期、引用计数和析构 |
| Workflow Credit Domain | 账户、计费类型、计费轮次 | reserve、阶段占用、commit 和退还 |
| Workflow Fence | `challenge`、请求号、屏障序号 | 元数据、资源和工作流记录是否来自同一阶段 |

这些标识都含有 generation。槽位编号相同而 generation 不同，内核就会把它们当成两个对象，旧请求不能借此操作新对象。

<a id="请求检查链"></a>
## 请求检查顺序

工具调用的主要检查位于 [`os/agent_core.c`](../os/agent_core.c) 的 `agent_execute_one()`。系统先取得 Context commit 权，再检查 Execution Contract 和 provenance，随后预留 Phase Lease。只有副作用检查允许执行时，具体工具才会修改进程、文件或元数据。

```text
复制请求或描述符
    -> 检查版本、大小、标志、保留字段和字符串结尾
    -> 核对 Agent 身份与工作流生命周期
    -> 解析工具编号、名称和带类型信息的参数
    -> 检查生命周期是否允许新操作
    -> 核对 Execution Contract 节点、前驱、尝试次数、截止时间
    -> 核对 provenance 和所需能力
    -> 预留 Phase Lease
    -> 检查取消状态和副作用阶段
    -> 执行工具，再次核对文件访问范围和对象身份
    -> 写入输出 provenance 并结算资源
    -> commit Context 和 terminal record
```

源码中的先后关系与上面一致：

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

V2 参数数组、V3 绑定信息和 SQE 都会先复制到内核私有内存。Task Channel 每次复制完整的 128 字节 SQE，检查和提交阶段只读取这份副本。

<a id="身份lifecycle-与-scope"></a>
## 身份、生命周期与文件访问范围

`agent_workflow_create()` 创建管理 Agent 和生命周期根。工作流内拥有 `AGENT_CAP_ORCHESTRATE` 的 Agent 可以调用 `agent_worker_create()`。申请的能力集合必须非零，只能取 `AGENT_CAP_CONTENT_READ` 和 `AGENT_CAP_ARTIFACT_WRITE` 的子集，同时不能超过调用方已有能力。内核还会核对工作流的文件访问范围和可执行文件身份。Agent 的进程控制块保存控制编号和完整生命周期键，进程通信、Typed Watch、Context 与资源账户都从这份身份取得归属信息。

关闭工作流时，生命周期按以下步骤处理：

1. 运行中的生命周期接受新操作，并为已接受的操作增加引用；
2. `agent_workflow_close()` 将其改为关闭中，普通新操作从此不再进入；
3. 已经接受的任务、元数据增量、成员退出和资源析构继续处理；
4. 所有引用清零后，内核清理文件访问范围、Execution Contract、Typed Watch、Task Channel 和资源账户，再增加槽位的 generation。

不同接口对旧对象的称呼略有差别。旧的 Execution Contract 键通常返回 `STALE`；已经删除的查询目标可能返回 `NOT_FOUND`；编辑租约过期并被清理后返回 `NOT_FOUND`，而 inode 的 `incarnation` 或 `expected_version` 不匹配时返回 `STALE`。

`agent_scope_delegate_fd()` 只委派一个文件描述符。文件打开、读写、元数据修改、删除和编辑租约仍会在 VFS 路径中检查文件访问范围，因此上层工具检查通过后也不能绕过文件权限。

带有 `preserve_on_retire` 的 artifact 是一项例外。生命周期槽位可以先回收，登记项、文件和存储计费继续保留；存储账户即使已经进入 `DRAINING` 或 `FREE`，这些数据也可继续存在，等后续处理完成后再释放。

<a id="schema-与-capability"></a>
## schema 与能力

角色说明 Agent 在工作流中的分工，能力位决定它能做什么。现有能力包括读取元数据和文件内容、读取进程信息、发送消息、建立订阅、写入动作和 artifact、写入审计与元数据、管理工作流、转发模型调用、取消等待以及管理消息路由。部分兼容名称会映射到同一能力，例如更新依赖关系使用 `META_WRITE`。

Tool Registry 把 schema 和权限要求写在同一项中：

| 目录字段 | 含义 |
| --- | --- |
| `required_capabilities` | 调用 Agent 必须具备的能力集合 |
| `accepted_input_labels` | 允许进入本工具的 provenance label |
| `output_add_labels` | 工具结果增加的 provenance label |
| `side_effect_mask` | 文件、元数据、进程通信、进程、权限、artifact 或订阅副作用 |

[`os/agent_tool_protocol.c`](../os/agent_tool_protocol.c) 逐项检查 V2 参数：

- 工具编号与名称是否指向同一目录项；
- 键名是否出现在该工具的 schema 中；
- 是否有重复键，必填参数是否齐全；
- 类型和值的长度是否相符；
- 字符串是否在声明范围内准确地以 NUL 结束；
- 请求标志和保留字段是否有效。

系统启动时还会检查工具名称是否重复、编号是否连续、参数写入位置是否重叠。`schema_digest` 把工具名称、参数、能力、provenance 和副作用一起写入摘要。工具含义发生变化时，即便编号未变，摘要也会随之改变。

## Execution Contract 与副作用检查

设置 `AGENT_EXECUTION_CONTRACT_F_ENFORCE` 后，一份 Execution Contract 最多保存 24 个按依赖顺序排列的节点。每个节点注明工具和 schema、前驱、artifact 类型、允许的 provenance、副作用、截止时间、尝试次数、重试与取消规则，以及资源上限。

V3 请求按顺序检查以下内容：

1. Execution Contract 键是否属于当前生命周期；
2. 节点和尝试次数是否存在，是否与已有记录冲突；
3. 前驱是否已经得到 Execution Contract 允许的执行结果；
4. schema digest 和规范化输入指纹是否一致；
5. 来源 Context 序号、来源节点和生产者身份是否对应；
6. 能力、provenance、截止时间和 Phase Lease 是否符合节点声明。

`agent_execution_contract_effect_begin()` 划分了“还能取消”和“已经开始产生副作用”两个阶段。取消请求若在这一步之前到达，节点可记录为 `CANCELLED`；工具已经开始修改系统状态时，取消会被标记为来得太晚，内核仍按实际结果结算。已经接受的尝试会保存执行结果，合法 Replay 直接取用相同的执行序号和结果，并设置缓存标志。

启用 `ENFORCE` 后，受约束的直接系统调用也会经过 `agent_execution_contract_gate_direct_syscall()`。缺少 Execution Contract 的副作用调用会先写下一条结构化拒绝记录。记录成功时返回 `DENIED`，terminal record 槽位不足时返回 `NO_SPACE`，暂时无法写入时返回 `RETRY`。

<a id="provenance-传播"></a>
## Provenance label

Provenance label 定义在 [`include/agent_provenance_abi.h`](../include/agent_provenance_abi.h)：

| 标记 | 常见来源 |
| --- | --- |
| `KERNEL_FACT` | 内核产生的进程、调度和对象信息 |
| `TRUSTED_USER_CONTROL` | 用户程序明确给出的控制输入 |
| `AGENT_DERIVED` | AgentOS 内核模块计算出的结果 |
| `UNTRUSTED_FILE_DATA` | 文件内容和元数据查询结果 |
| `UNTRUSTED_TOOL_OUTPUT` | 外部工具或模型返回值 |
| `CROSS_AGENT_DATA` | 其他 Agent 的消息和前驱结果 |

这些标记按位加入后续 Context。`agent_provenance_authorize_tool()` 比较当前输入与 Tool Registry 允许的 provenance，同时核对 Execution Contract 是否漏写了 Registry 中登记的副作用。内容指纹用于固定输入字节和规范化输入；身份、文件访问范围和能力仍要单独检查。

拒绝原因会写入当前 Context 的 provenance 标志，并使用固定的原因码，例如生命周期过期、缺少 Execution Contract、前驱不合法、能力不足、provenance 不被接受或副作用不符。V3 响应和任务 CQE 会带回相应的执行决定原因。

<a id="资源准入与提交"></a>
## 资源准入与 terminal commit

Workflow Credit Domain 记录 `held = free + pending + used`。其中，`free` 是账户中的可用额度，`pending` 是已经 reserve 的额度，`used` 是正在计费的存活对象。账户上限、资源类别上限和系统总上限会同时检查这一数值。

```text
reserve：free -> pending
commit：pending -> used
失败：pending -> free
销毁：used -> free
```

Execution Contract 中的执行额度和存储额度，会在工具运行前转成一份 Phase Lease。它以工作流、节点、请求号和 generation 标识，并按开始、启用、停用、结算的顺序变化。额度不足返回 `NO_SPACE`。失败时预留额度退回；已经发布的对象则在销毁时退回额度。

Context commit 顺序固定为：取得工具结果、结算 Phase Lease、写入输出 provenance、写入 Context 与工作流记录票号，最后更新 Execution Contract 节点。受约束调用会在执行前 reserve terminal record，避免工具已经修改系统状态却无处记录。`agent_workflow_close()` 只负责把生命周期改为关闭中并要求成员退出；成员、已有操作、任务回调、资源对象和 Context commit 全部处理完后，后台清理程序才回收工作流。

Nexus artifact 通过 `agent_file_publish()` 发布。内核先把 header 与 payload 复制到私有快照并写入未命名 inode，完整数据 checkpoint 结束后才接入正式文件名。正式路径不会出现半份内容；同名竞争至多一个调用成功，后来的调用返回 `DUPLICATE`，原文件保持不变。若目录接入结果为 `INDETERMINATE`，Guest 只在正式路径逐字节等于本次请求且紧接 EOF 时接受已有文件。Context、metadata 和 Workflow Fence 仍按 artifact handle、lifecycle generation 与 terminal record 关联，各自沿原有提交路径推进。

## 文件对象与 Live Query

Metadata Catalog 只接收 `agent_file_meta_set()` 显式登记、仅在运行期保存的元数据。记录绑定工作流生命周期、文件访问范围和 `{dev, inum, incarnation}`。uCore 每次重新分配 inode 都会增加 `incarnation`；达到最大值后，该 inode 不再复用。元数据选择器、摘要缓存、编辑租约和待处理删除记录都要核对这三部分身份。

查询开始和结束时会分别检查生命周期与 `fs_generation`。即使用索引找到候选项，内核仍会重新核对完整查询条件和文件访问范围。Typed Watch 绑定目标控制编号、生命周期、文件访问范围和订阅号，集合变化时发布 `ENTER`、`UPDATE` 或 `LEAVE`。

事件队列已满或增量事件出现 generation 缺口时，内核记录持续待确认的 `resync_generation`。恢复时，旧订阅必须继续工作。Agent 先用同一查询条件建立替代订阅，且不带确认标志；接着执行一次查询，只有 `truncated == 0` 才得到完整基线；随后在旧订阅上确认保存的 generation 缺口，并在同一内核临界区移除旧订阅。替代订阅在整个交接期间一直有效，之后由它继续接收事件，并按顺序补入基线。若又发现更大的 generation 缺口，就重复这组操作。

单次查询最多返回 8 项，当前 ABI 没有分页游标。结果被截断时，Agent 应移除替代订阅且不确认缺口，保留旧订阅和待确认状态，再缩小查询范围。普通、连续的 generation 变化不会触发重新同步。只要工作流中仍有未确认的缺口，或重新同步提示尚未投递，Workflow Fence 就返回 `RETRY`。

文件提交还要同时核对租约号、inode 的 `incarnation` 和 `expected_version`。

## Task Channel 协议

Task Channel 只有一个提交者，队列水位以内核记录为准。SQ 由提交者写入，CQ 在用户页表中只读。每个已经接受的目标任务只产生一条 CQE；取消命令使用独立的请求号，通过 `link_request_id` 指向目标。

过期请求和通道协议失步分别处理：

| 状态 | 检查结果 | 后续处理 |
| --- | --- | --- |
| `AGENT_TASK_CHANNEL_STALE` | `enter.generation` 不是当前通道 generation | 不设置 `RING_F_RESYNC`；返回当前 generation 和水位，调用方重新构造控制请求 |
| `AGENT_TASK_CHANNEL_STALE` | 提交者身份、所有者或工作流生命周期已经失效 | 不设置 `RING_F_RESYNC`；除 ABI 版本、大小和状态外，其余字段为零，调用方重建 Task Channel，并使用有效的提交者和工作流 |
| `AGENT_TASK_CHANNEL_STALE` | 取消目标已经被 CQ 确认读取，或目标不存在 | 不设置 `RING_F_RESYNC`；取消请求号已经用掉，并返回当前 generation 和水位 |
| `AGENT_TASK_CHANNEL_RESYNC_REQUIRED` | 请求号倒退、队列水位非法、SQE 通道 generation 或 `slot_generation` 错误、请求槽重复、关联关系非法 | `protocol_faults` 增加，通道 generation 前进，SQ 清零，并保持持续重新同步标记，等待调用方明确确认 |

恢复时，提交者丢弃尚未被接受的 SQE，采用 `enter` 结果中的新通道 generation，再提交 `ENTER_F_RESYNC` 和 `sq_tail=0`。普通 `STALE` 不增加 `protocol_faults` 或 `resync_count`。

取消策略若当场拒绝取消命令，取消请求号仍会被记为已使用。此时 `enter` 返回 `DENIED`，不产生取消 CQE，原目标继续运行。CQ 已满时，内核保留尚未写出的结果；用户读取 CQ 后，再继续发布。

类型化资源句柄包含槽位、类型、所有权标志和 generation。`IMPORT` 只接受当前 Agent 可读的普通文件、准确的 1–63 字节长度和 OWNED UTF-8，并要求当前 Agent 已经有一条经过校验的最新 Context；导入只绑定这条记录，不另建 Context。内核从 offset 0 取得内容，不改动共享文件 offset；EOF、NUL 和 UTF-8 检查通过后，只把不可变快照、内容指纹与内核生成的 producer/Context/provenance 元数据放入资源表，不保留 `struct file *`。最终 copyout 失败会回滚尚未暴露的资源，因此调用方不会得到“接口失败但槽位已占用”的半成功状态。

SQE 可以用 BORROWED 别名引用同一 `{slot, type, generation}`。BORROWED 任务结束后引用归还，资源保持 `LIVE`；OWNED 任务结束后资源自动消费。`RELEASE` 以及槽位再次使用都会使旧 generation 返回 `STALE`。当前 ECHO Task Bridge 从内核快照复制 payload，不再读取导入时的 fd，文件后来被改写也不会改变已经接受的任务输入。

<a id="失败状态与验证"></a>
## 失败状态与测试

| 条件 | 状态 |
| --- | --- |
| 生命周期、Execution Contract、Task Channel 或对象 generation 过期 | `STALE`、`CONFLICT` 或 `NOT_FOUND` |
| 能力、文件访问范围、provenance 或 Execution Contract 不符 | `DENIED` |
| 版本、大小、标志、保留字段或参数错误 | `BAD_VERSION`、`BAD_SIZE` 或 `BAD_PARAM` |
| 额度、对象表或队列容量不足 | `NO_SPACE`，或等待完成队列腾出位置 |
| Live Query 增量出现缺口 | `RESYNC_REQUIRED` 事件 |
| Task Channel 协议失步 | `AGENT_TASK_CHANNEL_RESYNC_REQUIRED` |
| Workflow Fence、元数据或 terminal record 尚未写完 | `RETRY` |
| 超过截止时间 | `TIMEOUT`，或带截止时间标志的 CQE |

| 测试 | 检查内容 |
| --- | --- |
| [`agenttrust_ucore.c`](../user/src/agenttrust_ucore.c) | 可执行文件身份、受控创建和启动状态 |
| [`agentscope_ucore.c`](../user/src/agentscope_ucore.c) | 文件访问范围和文件描述符委派 |
| [`agenttoolabi_ucore.c`](../user/src/agenttoolabi_ucore.c) | Tool Registry 和带类型信息参数的错误输入 |
| [`agentsecurity_ucore.c`](../user/src/agentsecurity_ucore.c) | 角色、能力、进程通信和副作用授权 |
| [`agentcontract_ucore.c`](../user/src/agentcontract_ucore.c) | Execution Contract、provenance、截止时间、Replay 和 Phase Lease |
| [`agentscan_ucore.c`](../user/src/agentscan_ucore.c) | 文件身份和 Live Query 集合变化 |
| [`agentfinal_ucore.c`](../user/src/agentfinal_ucore.c) | Context commit 顺序、资源结算和工作流回收 |
| [`agenttask_ucore.c`](../user/src/agenttask_ucore.c) | SQ/CQ 所有权、重新同步、取消、截止时间和 CQE |

```bash
make agent-uapi-check
make agent-module-check
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

系统调用字段见 [API](api.md)，模块关系见[产品架构](architecture.md)。
