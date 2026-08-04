# 任务二：Agent 与内核结构化交互

本文是 [design.md](design.md) 的任务二细节附录，重点展开结构化工具调用、工具表、错误语义和 Agent Context 写入路径。系统总体接口分工和 ABI 汇总见 [api.md](api.md)。

任务二的目标是在 Agent 进程机制基础上，提供 Agent 进程与内核之间的结构化工具调用接口。AgentOS-uCore 当前热路径使用 `agent_op` / `agent_result` 和 `agent_run()` 批量 ABI，一次 syscall 最多执行 64 个工具 op。syscall 503/504 保留原始 V1 名称协议布局；syscall 547/548 提供可扩展的 V2 sized typed KV 正式入口。两版共享同一工具表和参数规则。

## 接口

| 接口 | 说明 |
| --- | --- |
| `agent_run(struct agent_op *, struct agent_result *, int, uint64)` | 最终高性能批量工具调用入口 |
| `agent_call(struct agent_request *, struct agent_response *)` | syscall 503，V1 固定三参数槽兼容入口 |
| `agent_tool_list(struct agent_tool_desc *, int)` | syscall 504，V1 工具列表兼容入口 |
| `sys_tool_call(struct agent_request_v2 *, struct agent_response_v2 *)` / `tool_call()` | syscall 547，V2 sized typed KV 正式入口 |
| `sys_tool_list(struct agent_tool_desc_v2 *, int)` / `tool_list()` | syscall 548，V2 sized 工具描述入口 |

系统调用层只负责分发，`os/agent.c` 只保留历史入口的薄 facade。可写状态和实现按所有权拆分，避免工具调用、IPC、metadata 和观测继续堆积在同一编译单元：

| owner 模块 | 职责 |
| --- | --- |
| `agent_core.c` | 工具运行时与跨 owner 流程编排 |
| `agent_context.c` | Context shadow/mirror、按需私有 sidecar 与 `sys_context_*` |
| `agent_identity.c` | role、capability 和对象授权 |
| `agent_ipc.c` | route、event、watch、wait/cancel、heartbeat |
| `agent_lifecycle.c` | control id 与 controller departure |
| `agent_metadata.c` | FIFO transaction/projection gate、工作预算和 runtime snapshot |
| `agent_file_state.c` | incarnation-bound 文件版本、租约、digest cache 和 size/generation sidecar |
| `agent_metadata_catalog.c` / `agent_metadata_query.c` | live catalog、scope/index、bounded snapshot exchange、查询 filter/plan/execute；不保存跨调用结果 |
| `agent_metadata_scan.c` / `agent_metadata_directory.c` | 有界根扫描状态，以及 VFS create/write/truncate/delete 的无状态目录协调 |
| `agent_metadata_objects.c` / `agent_metadata_store.c` | dependency/action/prefetch 对象语义和 COW 双 bank 持久化 |
| `agent_observe.c` | audit、span、timeline、ledger 与 provenance |

模块间只通过 `agent_internal.h` 和 metadata 私有接口传递操作，不导出可由其他模块直接修改的全局数据。`make ci-check` 对 `ci/kernel-budgets.json` 中版本化登记的 owner、bridge、符号所有权和依赖方向执行静态门禁。metadata 拆分单元、IPC 及 contract headers 还共同进入 `metadata_control_plane` 聚合 source/text/BSS 预算；source 仅允许固定接口开销，loaded text 与 BSS 保持 no-growth，避免通过新建文件迁移旧单体实现。

## 协议

版本化工具 ABI 集中定义在内核和用户态共同包含的 `agent_tool_abi.h`：

| 结构 | 关键字段 |
| --- | --- |
| `struct agent_op` | `version`、`tool_id`、`request_id`、`arg0`、`arg1`、`flags`、`payload` |
| `struct agent_result` | `version`、`status`、`tool_id`、`request_id`、`sequence`、`value0`、`value1`、`value2`、`result` |

V1 名称协议请求和响应结构保持 byte-for-byte 兼容：

| 结构 | 关键字段 |
| --- | --- |
| `struct agent_request` | `version`、`tool_id`、`tool_name`、`request_id`、`arg0_key`、`arg0_type`、`arg0`、`arg1_key`、`arg1_type`、`arg1`、`payload_key`、`payload_type`、`payload` |
| `struct agent_response` | `version`、`status`、`tool_id`、`tool_name`、`request_id`、`sequence`、`value0`、`value1`、`value2`、`result` |
| `struct agent_tool_desc` | `tool_id`、`flags`、`name`、`params`、`description` |

V2 结构如下：

| 结构 | 关键字段 |
| --- | --- |
| `struct agent_request_v2` | `version`、`size`、`tool_id`、`param_count`、`request_id`、`flags`、参数数组地址 `params`、`tool_name` |
| `struct agent_param_v2` | 独立的 `version`、`size`、`type`、`value_size`、`key` 和 tagged value |
| `struct agent_response_v2` | `version`、`size`、`status`、解析后的 tool identity、request/sequence、数值槽和结果文本 |
| `struct agent_tool_desc_v2` | `version`、`size`、`tool_id`、`param_count`、`flags`、name/params/description |

`agent_run()` 只走 `tool_id`，避免热路径字符串扫描。V1 `agent_call()` 和 V2 `sys_tool_call()` 都可以通过 ID、名称或二者共同选择工具；同时提供时必须精确匹配。V2 最多接受 8 个参数，按 key 匹配而不是按数组位置匹配，因此合法重排不改变语义。参数 key、type、target、required 的 typed rule table 是唯一参数语义源：V1/V2 decoder、V2 `param_count` 和两版 tool-list 的 `params` 字符串都从该表读取或生成，不再维护手写 schema/count 副本。内核启动时验证 25 个 tool id 连续、名称唯一、rule key/target 唯一、type 与 target 相容及生成 schema 不越界。请求的 `flags` 当前必须为 0，`param_count=0` 当且仅当 `params=0`。每项都必须使用当前 parameter version 和精确结构 size；uint64 的 `value_size` 必须为 8，string 必须在声明边界内以 NUL 终止且 `value_size` 包含终止符。请求/响应地址有效时，协议和工具语义状态读取 `response.status`，syscall 返回 0；只有用户地址或复制失败直接返回 -1。

参数键还必须来自共享头中的 `AGENT_PARAM_KEY_REGISTRY`。`AGENT_PARAM_KEY_SIZE=16` 包含结尾 NUL，因此线上最多编码 15 个可见 ASCII 字符；注册表中的每个字面量都受 `_Static_assert` 约束，typed rule 只引用注册表符号，不能另写未登记的字符串。Host `check-agent-uapi-layout.py` 独立检查键名非空、ASCII、唯一和容量，检查每条 rule 都引用已登记键、没有陈旧未使用键，并同时检查工具 ID 顺序、名称唯一性、参数上限和生成 schema 的容量。新增参数必须同时经过共享注册表、typed rule、编译期断言和 Host 校验，错误不会拖到内核启动后才暴露。

协议扩展不能在同一 version 下重解释既有字段。当前内核对 request、parameter 和 descriptor 使用严格 version/size 检查；以后若增加后缀，应发布新 version 或明确兼容 sized-prefix 规则。V1 固定布局继续由 503/504 服务，不因 V2 演进而改变。

## 批量执行

`agent_run()` 的执行流程：

1. 检查当前进程是否为 Agent。
2. 检查 `count` 是否在 `1..AGENT_BATCH_MAX`。
3. 从用户态复制 `agent_op` 数组。
4. 逐条检查 version 和 tool_id。
5. 执行工具。
6. 为每条工具调用分配 sequence。
7. 写入对应 `agent_result`。
8. 将结果追加到 Context Path。
9. 同步 shadow Context 到用户镜像。

完整 `agent_op/result` 详情与可信 attribution 不再嵌入每个 PCB，而是写入 Context owner 为活跃 Agent 按需分配的 9 页 sidecar。运行时把它与 6 页用户 mirror、6 页可信 shadow 合为一次 21 页 `RESOURCE_AGENT_STATE_PAGE` 请求，由 EXEC resource account 原子预留、提交和退款；sidecar-only 的 9 页预算仍由 CI 独立观察。

同一进程的 sequence 接纳、工具执行、result/header/Context record、Context syscall、IPC 状态、文件查询和 wait 归因进入可睡眠、FIFO、可重入的 Context commit lane；需要 metadata 时锁序固定为 `lane -> metadata`。`agent_call_count` 是已接纳并保留的调用序号，可在在途慢调用期间暂时领先；`latest_sequence` 是完整记录已经提交的水位。并发回归要求 `context_commit_lane=1 sequence=1..3 hash=1`。

批量执行的性能收益来自：

- 减少 syscall 次数；
- 减少重复检查；
- 用 tool_id 直接定位工具；
- 批量写出结果。

## 内核工具

当前实现 25 个工具，任务二基础工具和任务四、五扩展工具共用同一套工具表：

| 工具 | `tool_id` | 输入 | 输出 |
| --- | ---: | --- | --- |
| `echo` | 1 | `payload`、`arg0`、`arg1` | 返回 payload 长度、两个数值参数和 payload 文本 |
| `pid_info` | 2 | 无 | 返回当前 pid、Agent ID 和 Agent 身份 |
| `ctx_stat` | 3 | 无 | 返回 Agent Context 起始地址、大小和当前调用次数 |
| `query_process` | 4 | 可选类型 | 返回进程数量、Agent 数量和可运行进程数量 |
| `get_system_status` | 5 | 无 | 返回进程数量、Agent 数量和系统 tick |
| `read_context` | 6 | 无 | 返回本次调用追加后的 Context Path 记录数、head 和总调用次数 |
| `query_file` | 7 | 路径或属性条件串 | 返回文件查询结果 |
| `send_message` | 8 | `target_pid`、message | 沿显式 `MESSAGE` route 向目标 Agent 发送短消息 |
| `read_message` | 9 | 无 | 读取当前 Agent 消息 |
| `file_meta_init` | 10 | 无 | 重新加载任务四文件对象元数据表 |
| `read_file_summary` | 11 | selector | 返回文件摘要 |
| `dependency_query` | 12 | label | 返回对象标签影响范围 |
| `capability_check` | 13 | `role`、`action` | `role` 是兼容参数，不参与授权；按当前进程真实 capability 检查动作，并返回真实 role/capability |
| `rerun_stage` | 14 | 可选 `role`、`stage` | `role` 不参与授权；旧示例兼容，内部调用通用动作提交路径，记录和重复请求判断归入 `action_commit` |
| `write_report` | 15 | 可选 `role`、`payload` | `role` 不参与授权；旧示例兼容，内部调用通用工件更新路径，记录和重复请求判断归入 `artifact_update` |
| `agent_watch` | 16 | event_type、filter | 注册事件 watch |
| `agent_wait` | 17 | timeout | syscall-only 工具表可发现项；`agent_run()` 调用返回 `AGENT_STATUS_BAD_PARAM` |
| `agent_heartbeat` | 18 | interval | 与 set/stop syscall 共用 `0..AGENT_HEARTBEAT_MAX_TICKS` 校验；0 兼容停止语义 |
| `context_push` | 19 | record | 手动 Context 节点使用的内部工具 ID |
| `read_file_digest` | 20 | selector | 读取真实文件短预览和 FNV-1a 内容指纹；绑定 metadata 的真实文件可复用 digest cache |
| `action_commit` | 21 | 可选 `role`、`selector` | `role` 不参与授权；按通用对象 selector 幂等提交 Agent 动作 |
| `artifact_update` | 22 | 可选 `role`、`selector` | `role` 不参与授权；按通用对象 selector 更新工件、报告、记忆或结果对象状态 |
| `llm_request` | 23 | target_pid、prompt_summary | 记录请求摘要；target 非零时沿 `MESSAGE` route 投递，target 为零时只记录 |
| `llm_response` | 24 | target_pid、reply_summary | 由具备 `LLM_RELAY` 的 Agent 沿显式 `LLM_DONE` route 投递结果事件 |
| `dependency_update` | 25 | selector | 注册或更新通用对象依赖关系 |

`MESSAGE_SEND` 和 `LLM_RELAY` 只决定调用者能否发起对应操作，不授予任意目标范围。跨 Agent 的 `send_message`、非零 target `llm_request` 和 `llm_response` 必须先解析 source/target 的不可复用 stable control id，确认两端属于同一 active workflow scope，再分别命中 target 入站表中的 `MESSAGE` 或 `LLM_DONE` route；target consent 也不能越过 scope。自投递隐式允许。`llm_request(target_pid=0, ...)` 只记录摘要，不执行投递。`agentsecurity_ucore` 已覆盖 `send_message` / 非零 target `llm_request` 的未授权拒绝、`MESSAGE` grant/revoke、target 自主接受 `LLM_DONE`，并验证 LLM-only route 拒绝 `MESSAGE`；`agentllm_ucore` 提供 `LLM_DONE` route 的端到端正向回归。尚未由具备 `LLM_RELAY` 的 source 专项验证无 `LLM_DONE` 位时的响应拒绝。

## 错误处理

当前覆盖的错误状态：

| 状态 | 说明 |
| --- | --- |
| `AGENT_STATUS_NOT_AGENT` | 普通进程调用 Agent 工具入口 |
| `AGENT_STATUS_BAD_REQUEST` | 请求版本错误，或请求结构不一致 |
| `AGENT_STATUS_UNKNOWN_TOOL` | 工具不存在 |
| `AGENT_STATUS_BAD_PARAM` | 参数或必要字段不符合工具要求 |
| `AGENT_STATUS_NOT_FOUND` | 查询文件或目标 Agent 不存在 |
| `AGENT_STATUS_NO_SPACE` | Agent Context、IPC route 表、事件 source/class/external/总量配额或同步路径不可用 |
| `AGENT_STATUS_DENIED` | 权限检查拒绝 |
| `AGENT_STATUS_DUPLICATE` | 重复幂等动作被识别 |
| `AGENT_STATUS_CANCELLED` | Agent 等待被受权 Agent 取消 |
| `AGENT_STATUS_BAD_VERSION` | V2 request、parameter 或 descriptor version 不受支持 |
| `AGENT_STATUS_BAD_SIZE` | sized 结构、value 长度或字符串边界不合法 |
| `AGENT_STATUS_BAD_TYPE` | typed KV 类型与该工具参数规则不一致 |
| `AGENT_STATUS_UNKNOWN_PARAM` | 参数 key 不属于该工具 |
| `AGENT_STATUS_RETRY` / `IO_ERROR` / `DURABILITY` / `INDETERMINATE` | 可重试、明确 I/O 失败、持久性不足和发布边界后结果待查询四类持久化错误 |

最终功能验收程序 `agentfinal_ucore` 会覆盖批量工具调用、sequence 连续性、Context 写入、Context Snapshot、通用 `action_commit/artifact_update` 和基础 template LLM 调用，并用 name-only V1 `agent_call()` 验证 `echo`、`query_file`、`pid_info`、`read_file_digest`、`dependency_update` 和 `dependency_query`。`agenttoolabi_ucore` 专门逐项核对 25 个工具的 id、name、flags、param_count 和生成 schema 在 V1/V2 列表中完全一致，并输出 `schema_generated=1 validated=25`；它还用 `key_capacity=1 llm_response_v1_v2=1 buffer_sentinel=1` 验证 15 字符 key 可完整编码、16 字节无 NUL 的 V1/V2 key 均被拒绝、`reply_summary` 在 V1/V2 `llm_response` 中一致可用，以及两版响应 copyout 均不越过用户缓冲区。其余覆盖包括可选 `role` 与 heartbeat zero-stop 描述、V1 兼容、V2 参数重排，以及 unknown/duplicate/type/size/version/missing 的严格负向矩阵。`agentllm_ucore` 覆盖请求 Agent 与 Relay Agent 之间的 LLM 请求、模板响应、事件唤醒、Context 和 timeline 记录。`labdemo_ucore` 覆盖 denied 和 duplicate 业务错误；`agentsecurity_ucore` 覆盖用户态伪造 role 和 V1 ID/name mismatch。

## 上下文写入：Agent Context

每次 Agent 调用结束后，内核会把最新 `struct agent_result` 写入 Agent Context，同时追加一条 `struct agent_context_record` 到 Context Path 环形记录区。legacy `struct agent_response` 只作为 `agent_call()` 的返回结构，不直接写入 Context latest 区。

写入路径：

1. 工具执行得到 `agent_result`。
2. 内核分配新的 sequence。
3. record 写入当前 cause/span；首条记录 cause 为 0，后续记录默认指向上一条 sequence。
4. latest result 写入 shadow。
5. record 写入 shadow record 区。
6. header 元信息更新，包括当前 cause/span 和 provenance edge 计数。
7. 用户镜像同步。

工具触发的事件也会携带 cause/span。目标 Agent 消费事件后继承 span，后续工具调用继续同一链路。这让结构化工具调用不仅能返回结果，还能为多 Agent 协作提供前后关系。

## 与任务四、五的关系

任务四文件查询和任务五 Agent Loop 都复用任务二工具调用机制：

- 文件属性查询可以作为 `AGENT_TOOL_QUERY_FILE` 执行；
- 文件摘要和对象标签依赖查询作为工具执行；
- 权限检查、通用动作提交、通用工件更新和 LLM 请求/响应作为工具执行；
- watch、heartbeat 和 `context_push` 可以通过工具表发现；
- `agent_wait` 只允许通过 `agent_wait()` syscall 执行，避免在批量热路径中阻塞整个 batch；
- wait/wake 使用独立 syscall，因为 wait 可能阻塞，不适合作为 batch 热路径。

## 验证证据

原始串口输出统一保存在 [正式证据索引](../../evidence/releases/INDEX.md)，逐项测试步骤见 [验证说明](verification.md)。本任务文档只保留任务二相关检查点：

| 程序 | 检查点 |
| --- | --- |
| `agentfinal_ucore` | 批量工具调用 sequence 连续；短 payload/result 写入 Context；通用 action ABI、LLM 模板 relay 和按名称调用协议均可用。 |
| `agentllm_ucore` | requester/relay/response 路径可跑通，LLM 请求以结构化工具调用进入 Context、timeline 和 audit。 |
| `agentbench_ucore` | scalar 与 batch 两条路径均输出多轮 tick 统计，batch 路径体现减少 syscall 次数后的吞吐优势。 |
| `labdemo_ucore` | 通用 action、事件和 audit 能在科研示例负载中组合使用，权限拒绝和重复请求都有结构化记录。 |
| `agentsecurity_ucore` | 用户态伪造 role 不生效；旧工具名/ID 不一致会失败；错误参数键或类型按结构化错误返回。 |
| `agenttoolabi_ucore` | syscall 547/548 实际执行；V1/V2 列表一致，可选 `role` 和 heartbeat zero-stop 描述可发现，V1 byte-compatible，V2 typed KV 可重排，未知/重复/错类型/错 size/错 version/缺失参数全部拒绝；`key_capacity=1 llm_response_v1_v2=1 buffer_sentinel=1` 绑定键容量边界、两版 LLM response 与用户响应缓冲边界。 |
