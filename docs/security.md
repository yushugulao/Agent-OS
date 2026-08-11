# AgentOS 安全机制

智能体工作流会同时持有身份、文件、工具结果和异步任务。进程槽、索引节点（inode）、任务环槽和执行节点都可能被重复使用，用户缓冲区也可能在系统调用尚未结束时发生变化。AgentOS 为这些对象附上工作流生命周期和代次号；工具产生副作用之前，还要依次检查参数格式、能力、文件访问范围、执行约定、数据来源和资源额度。

## 文档索引

- [受保护的对象](#受保护的对象)
- [请求检查顺序](#请求检查顺序)
- [身份、生命周期和文件访问范围](#身份生命周期和文件访问范围)
- [参数格式与能力](#参数格式与能力)
- [执行约定与副作用检查](#执行约定与副作用检查)
- [数据来源标记](#数据来源标记)
- [资源预留与结果写入](#资源预留与结果写入)
- [文件对象与实时查询](#文件对象与实时查询)
- [任务通道协议](#任务通道协议)
- [失败状态与测试](#失败状态与测试)

<a id="受保护对象"></a>
## 受保护的对象

| 对象 | 内核用来识别对象的信息 | 主要检查 |
| --- | --- | --- |
| 智能体 | 智能体编号、控制编号、角色、程序映像身份 | 受控创建、可信可执行文件、能力集合 |
| 工作流 | 生命周期 `{id, generation}`、文件访问范围编号 | 是否仍在运行、操作引用和回收状态 |
| 工具请求 | 工具编号和名称、参数格式摘要、请求号 | 私有复制、带类型信息的参数、工具目录清单 |
| 执行节点 | 约定代次号、节点号、尝试次数 | 前驱、输入指纹、截止时间、重试和取消规则 |
| 文件对象 | `dev`、`inum`、`incarnation`、文件更新序号 | 文件访问范围、元数据快照、编辑版本 |
| 实时订阅 | 目标控制编号、生命周期、文件访问范围、订阅编号 | 查询条件、增量序号和重新同步状态 |
| 任务通道 | 所有者、通道版本、环槽代次号 | 唯一提交者、只读完成队列、内核水位和完成结果 |
| 带类型句柄 | 槽位、类型、所有权、代次号 | 所属生命周期、引用计数和析构 |
| 资源账户 | 账户、计费类型、计费轮次 | 预留、阶段占用、发布和退还 |
| 工作流阶段快照 | `challenge`、请求号、快照序号 | 元数据、资源和执行记录是否来自同一阶段 |

这些标识都含有代次信息。槽位编号相同而代次号不同，内核就会把它们当成两个对象，旧请求不能借此操作新对象。

<a id="请求检查链"></a>
## 请求检查顺序

工具调用的主要检查位于 [`os/agent_core.c`](../os/agent_core.c) 的 `agent_execute_one()`。系统先取得上下文写入权，再检查执行约定和数据来源，随后预留阶段额度。只有副作用检查允许执行时，具体工具才会修改进程、文件或元数据。

```text
复制请求或描述符
    -> 检查版本、大小、标志、保留字段和字符串结尾
    -> 核对智能体身份与工作流生命周期
    -> 解析工具编号、名称和带类型信息的参数
    -> 检查生命周期是否允许新操作
    -> 核对约定节点、前驱、尝试次数、截止时间
    -> 核对数据来源和所需能力
    -> 预留工具阶段额度
    -> 检查取消状态和副作用
    -> 执行工具，再次核对文件访问范围和对象身份
    -> 写入输出来源并结算资源
    -> 写入上下文和执行结果
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

V2 参数数组、V3 绑定信息和任务 SQE 都会先复制到内核私有内存。任务通道每次复制完整的 128 字节 SQE，检查和提交阶段只读取这份副本。

<a id="身份lifecycle-与-scope"></a>
## 身份、生命周期和文件访问范围

`agent_workflow_create()` 创建管理智能体和生命周期根。工作流内拥有 `AGENT_CAP_ORCHESTRATE` 的智能体可以调用 `agent_worker_create()`。申请的能力集合必须非零，只能取 `AGENT_CAP_CONTENT_READ` 和 `AGENT_CAP_ARTIFACT_WRITE` 的子集，同时不能超过调用方已有能力。内核还会核对工作流的文件访问范围和可执行文件身份。智能体的进程控制块保存控制编号和完整生命周期键，进程通信、实时订阅、上下文与资源账户都从这份身份取得归属信息。

关闭工作流时，生命周期按以下步骤处理：

1. 运行中的生命周期接受新操作，并为已接受的操作增加引用；
2. `agent_workflow_close()` 将其改为关闭中，普通新操作从此不再进入；
3. 已经接受的任务、元数据增量、成员退出和资源析构继续处理；
4. 所有引用清零后，内核清理文件访问范围、执行约定、实时订阅、任务通道和资源账户，再增加槽位的代次号。

不同接口对旧对象的称呼略有差别。旧的约定键通常返回 `STALE`；已经删除的查询目标可能返回 `NOT_FOUND`；编辑租约过期并被清理后返回 `NOT_FOUND`，而 inode 的 `incarnation` 或 `expected_version` 不匹配时返回 `STALE`。

`agent_scope_delegate_fd()` 只委派一个文件描述符。文件打开、读写、元数据修改、删除和编辑租约仍会在 VFS 路径中检查文件访问范围，因此上层工具检查通过后也不能绕过文件权限。

带有 `preserve_on_retire` 的持久输出是一项例外。生命周期槽位可以先回收，登记项、文件和存储计费继续保留；存储账户即使已经进入 `DRAINING` 或 `FREE`，这些数据也可继续存在，等后续处理完成后再释放。

<a id="schema-与-capability"></a>
## 参数格式与能力

角色说明智能体在工作流中的分工，能力位则决定它能做什么。现有能力包括读取元数据和文件内容、读取进程信息、发送消息、建立订阅、写入动作和结果文件、写入审计与元数据、管理工作流、转发模型调用、取消等待以及管理消息路由。部分兼容名称会映射到同一能力，例如更新依赖关系使用 `META_WRITE`。

工具目录把参数格式和权限要求写在同一项中：

| 目录字段 | 含义 |
| --- | --- |
| `required_capabilities` | 调用智能体必须具备的能力集合 |
| `accepted_input_labels` | 允许进入本工具的数据来源 |
| `output_add_labels` | 工具结果增加的来源标记 |
| `side_effect_mask` | 文件、元数据、进程通信、进程、权限、结果文件或订阅副作用 |

[`os/agent_tool_protocol.c`](../os/agent_tool_protocol.c) 逐项检查 V2 参数：

- 工具编号与名称是否指向同一目录项；
- 键名是否出现在该工具的参数规则中；
- 是否有重复键，必填参数是否齐全；
- 类型和值的长度是否相符；
- 字符串是否在声明范围内准确地以 NUL 结束；
- 请求标志和保留字段是否有效。

系统启动时还会检查工具名称是否重复、编号是否连续、参数写入位置是否重叠。`schema_digest` 把工具名称、参数、能力、数据来源和副作用一起写入摘要。工具含义发生变化时，即便编号未变，摘要也会随之改变。

<a id="execution-contract-与-effect-gate"></a>
## 执行约定与副作用检查

设置 `AGENT_EXECUTION_CONTRACT_F_ENFORCE` 后，一份执行约定最多保存 24 个按依赖顺序排列的节点。每个节点注明工具和参数格式、前驱、结果类型、允许的数据来源、副作用、截止时间、尝试次数、重试与取消规则，以及资源上限。

V3 请求按顺序检查以下内容：

1. 约定键是否属于当前生命周期；
2. 节点和尝试次数是否存在，是否与已有记录冲突；
3. 前驱是否已经得到约定允许的执行结果；
4. 参数格式摘要和规范化输入指纹是否一致；
5. 来源上下文序号、来源节点和生产者身份是否对应；
6. 能力、数据来源、截止时间和阶段额度是否符合节点声明。

`agent_execution_contract_effect_begin()` 划分了“还能取消”和“已经开始产生副作用”两个阶段。取消请求若在这一步之前到达，节点可记录为 `CANCELLED`；工具已经开始修改系统状态时，取消会被标记为来得太晚，内核仍按实际结果结算。已经接受的尝试会保存执行结果，合法重放直接取用相同的执行序号和结果，并设置缓存标志。

启用 `ENFORCE` 后，受约束的直接系统调用也会经过 `agent_execution_contract_gate_direct_syscall()`。缺少执行约定的副作用调用会先写下一条结构化拒绝记录。记录成功时返回 `DENIED`，安全记录槽位不足时返回 `NO_SPACE`，暂时无法写入时返回 `RETRY`。

<a id="provenance-传播"></a>
## 数据来源标记

来源标记定义在 [`include/agent_provenance_abi.h`](../include/agent_provenance_abi.h)：

| 标记 | 常见来源 |
| --- | --- |
| `KERNEL_FACT` | 内核产生的进程、调度和对象信息 |
| `TRUSTED_USER_CONTROL` | 用户程序明确给出的控制输入 |
| `AGENT_DERIVED` | AgentOS 内核模块计算出的结果 |
| `UNTRUSTED_FILE_DATA` | 文件内容和元数据查询结果 |
| `UNTRUSTED_TOOL_OUTPUT` | 外部工具或模型返回值 |
| `CROSS_AGENT_DATA` | 其他智能体的消息和前驱结果 |

这些标记按位加入后续上下文。`agent_provenance_authorize_tool()` 比较当前输入与工具目录允许的来源，同时核对执行约定是否漏写了目录中登记的副作用。SHA-256 用于固定输入字节和规范化输入指纹；身份、文件访问范围和能力仍要单独检查。

拒绝原因会写入当前上下文的来源标志，并使用固定的原因码，例如生命周期过期、缺少执行约定、前驱不合法、能力不足、来源不被接受或副作用不符。V3 响应和任务 CQE 会带回相应的执行决定原因。

<a id="资源准入与提交"></a>
## 资源预留与结果写入

工作流资源账户记录 `held = F + P + U`。其中，F 是账户中的可用额度，P 是已经预留的额度，U 是正在计费的存活对象。账户上限、资源类别上限和系统总上限会同时检查这一数值。

```text
预留：F -> P
发布：P -> U
失败：P -> F
销毁：U -> F
```

执行约定中的执行额度和存储额度，会在工具运行前转成一份阶段额度。它以工作流、节点、请求号和代次号标识，并按开始、启用、停用、结算的顺序变化。额度不足返回 `NO_SPACE`。失败时预留额度退回；已经发布的对象则在销毁时退回额度。

上下文写入顺序固定为：取得工具结果、结算阶段额度、写入输出来源、写入上下文与执行记录票号，最后更新约定节点。受约束调用会在执行前预留结果记录，避免工具已经修改系统状态却无处记录。`agent_workflow_close()` 只负责把生命周期改为关闭中并要求成员退出；成员、已有操作、任务回调、资源对象和上下文写入全部处理完后，后台清理程序才回收工作流。

Nexus 结果文件由用户态另行写入，不与内核上下文记录构成同一个原子事务。[`agent_nexus.c`](../user/lib/agent_nexus.c) 使用工作流编辑租约串行写入，先持久化魔数为零的文件头和数据，再写正式文件头。[`agent_nexus.h`](../user/include/agent_nexus.h) 将 `AGENT_NEXUS_ARTIFACT_PUBLISH_IS_ATOMIC` 明确定义为 `0`。读取方重新核对魔数、清单摘要、数据摘要和生命周期，只接收完整文件。

<a id="文件对象与-live-query"></a>
## 文件对象与实时查询

实时查询目录只接收 `agent_file_meta_set()` 显式登记、仅在运行期保存的元数据。记录绑定工作流生命周期、文件访问范围和 `{dev, inum, incarnation}`。uCore 每次重新分配 inode 都会增加 `incarnation`；达到最大值后，该 inode 不再复用。元数据选择器、摘要缓存、编辑租约和待处理删除记录都要核对这三部分身份。

查询开始和结束时会分别检查生命周期与 `fs_generation`。即使用索引找到候选项，内核仍会重新核对完整查询条件和文件访问范围。实时订阅绑定目标控制编号、生命周期、文件访问范围和订阅号，集合变化时发布 `ENTER`、`UPDATE` 或 `LEAVE`。

事件队列已满或增量事件出现缺口时，内核记录持续待确认的 `resync_generation`。恢复时，旧订阅必须继续工作。智能体先用同一查询条件建立替代订阅，且不带确认标志；接着执行一次查询，只有 `truncated == 0` 才得到完整基线；随后在旧订阅上确认保存的缺口序号，并在同一内核临界区移除旧订阅。替代订阅在整个交接期间一直有效，之后由它继续接收事件，并按顺序补入基线。若又发现更大的缺口序号，就重复这组操作。

单次查询最多返回 8 项，当前 ABI 没有分页游标。结果被截断时，智能体应移除替代订阅且不确认缺口，保留旧订阅和待确认状态，再缩小查询范围。普通、连续的更新序号变化不会触发重新同步。只要工作流中仍有未确认的缺口，或重新同步提示尚未投递，工作流阶段快照就返回 `RETRY`。

文件提交还要同时核对租约号、inode 的 `incarnation` 和 `expected_version`。

<a id="task-channel-协议"></a>
## 任务通道协议

任务通道只有一个提交者，队列水位以内核记录为准。提交队列由提交者写入，完成队列在用户页表中只读。每个已经接受的目标任务只产生一条 CQE；取消命令使用独立的请求号，通过 `link_request_id` 指向目标。

过期请求和通道协议失步分别处理：

| 状态 | 检查结果 | 后续处理 |
| --- | --- | --- |
| `AGENT_TASK_CHANNEL_STALE` | `enter.generation` 不是当前通道版本 | 不设置 `RING_F_RESYNC`；返回当前通道版本和水位，调用方重新构造控制请求 |
| `AGENT_TASK_CHANNEL_STALE` | 提交者身份、所有者或工作流生命周期已经失效 | 不设置 `RING_F_RESYNC`；除 ABI 版本、大小和状态外，其余字段为零，调用方重新建立任务通道，并使用有效的提交者和工作流 |
| `AGENT_TASK_CHANNEL_STALE` | 取消目标已经被 CQ 确认读取，或目标不存在 | 不设置 `RING_F_RESYNC`；取消请求号已经用掉，并返回当前通道版本和水位 |
| `AGENT_TASK_CHANNEL_RESYNC_REQUIRED` | 请求号倒退、队列水位非法、SQE 通道版本或环槽代次号错误、请求槽重复、关联关系非法 | `protocol_faults` 增加，通道版本前进，SQ 清零，并保持重新同步标记，等待调用方明确确认 |

恢复时，提交者丢弃尚未被接受的 SQE，采用 `enter` 结果中的新通道版本，再提交 `ENTER_F_RESYNC` 和 `sq_tail=0`。普通 `STALE` 不增加 `protocol_faults` 或 `resync_count`。

取消策略若当场拒绝取消命令，取消请求号仍会被记为已使用。此时 `enter` 返回 `DENIED`，不产生取消 CQE，原目标继续运行。完成队列已满时，内核保留尚未写出的结果；用户读取 CQ 后，再继续发布。

带类型资源句柄包含槽位、类型、所有权标志和代次号。资源表实现导入、释放、查询、引用和析构。当前同步任务转换层只接受空输入和空输出；导入非空资源时返回 `AGENT_TASK_CHANNEL_DENIED`。

<a id="失败状态与验证"></a>
## 失败状态与测试

| 条件 | 状态 |
| --- | --- |
| 生命周期、执行约定、任务通道或对象代次号过期 | `STALE`、`CONFLICT` 或 `NOT_FOUND` |
| 能力、文件访问范围、数据来源或约定不符 | `DENIED` |
| 版本、大小、标志、保留字段或参数错误 | `BAD_VERSION`、`BAD_SIZE` 或 `BAD_PARAM` |
| 额度、对象表或队列容量不足 | `NO_SPACE`，或等待完成队列腾出位置 |
| 实时查询增量出现缺口 | `RESYNC_REQUIRED` 事件 |
| 任务通道协议失步 | `AGENT_TASK_CHANNEL_RESYNC_REQUIRED` |
| 阶段快照、元数据或结果记录尚未写完 | `RETRY` |
| 超过截止时间 | `TIMEOUT`，或带截止时间标志的 CQE |

| 测试 | 检查内容 |
| --- | --- |
| [`agenttrust_ucore.c`](../user/src/agenttrust_ucore.c) | 可执行文件身份、受控创建和启动状态 |
| [`agentscope_ucore.c`](../user/src/agentscope_ucore.c) | 文件访问范围和文件描述符委派 |
| [`agenttoolabi_ucore.c`](../user/src/agenttoolabi_ucore.c) | 工具目录和带类型信息参数的错误输入 |
| [`agentsecurity_ucore.c`](../user/src/agentsecurity_ucore.c) | 角色、能力、进程通信和副作用授权 |
| [`agentcontract_ucore.c`](../user/src/agentcontract_ucore.c) | 执行约定、数据来源、截止时间、重试和阶段额度 |
| [`agentscan_ucore.c`](../user/src/agentscan_ucore.c) | 文件身份和实时查询集合变化 |
| [`agentfinal_ucore.c`](../user/src/agentfinal_ucore.c) | 上下文写入顺序、资源结算和工作流回收 |
| [`agenttask_ucore.c`](../user/src/agenttask_ucore.c) | 提交队列与完成队列的所有权、重新同步、取消、截止时间和 CQE |

```bash
make agent-uapi-check
make agent-module-check
AGENT_TEST_CASE=agentsecurity_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentcontract_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agenttask_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

系统调用字段见 [API](api.md)，模块关系见[产品架构](architecture.md)。
