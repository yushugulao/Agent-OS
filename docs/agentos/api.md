# 接口与 ABI：Agent-OS

本文档描述用户态程序与 Agent-OS 内核扩展之间的稳定接口约定。Agent 结构体和常量定义以内核态 `os/agent.h` 和用户态 `user/include/agent.h` 为准；版本化工具协议由内核和用户态共同包含的 `agent_tool_abi.h` 定义；workflow lifecycle 只读 ABI 由根目录共享头 `agent_lifecycle_abi.h` 定义；持久观测恢复 ABI 由共享头 `agent_observe_abi.h` 定义；资源快照与性能快照 sized ABI 分别由共享头 `agent_resource_abi.h` 和 `agent_performance_abi.h` 定义；块 I/O 策略 ABI 由根目录 `io_policy.h` 与 `user/include/io_policy.h` 同步定义。

## 系统调用

### 系统调用：Agent-OS

Agent-OS 在 uCore syscall 编号空间中使用 500 至 560；其中 543、544、558 至 560 是受限观测接口，547、548 是可扩展工具协议的 V2 入口，549、551、554 至 556 仅供对应测试 profile 使用：

| syscall | 编号 | 用户态原型 | 说明 |
| --- | ---: | --- | --- |
| `agent_create` | 500 | `int agent_create(void)` | 创建 Agent 子进程 |
| `agent_info` | 501 | `int agent_info(struct agent_info *)` | 查询当前进程 Agent 元信息 |
| `agent_run` | 502 | `int agent_run(struct agent_op *, struct agent_result *, int, uint64)` | 高性能批量工具调用入口 |
| `agent_call` | 503 | `int agent_call(struct agent_request *, struct agent_response *)` | V1 兼容名称协议入口，使用工具名称和参数键值列表 |
| `agent_tool_list` | 504 | `int agent_tool_list(struct agent_tool_desc *, int)` | 查询工具列表 |
| `context_push` | 505 | `int context_push(struct agent_context_record *)` | 手动追加 Context Path 节点 |
| `context_query` | 506 | `int context_query(uint64, struct agent_context_record *, int)` | 按 sequence 查询当前 active Context Path |
| `context_snapshot` | 507 | `int context_snapshot(struct agent_context_header *, struct agent_context_record *, int)` | 批量返回 archive header 和 active Context Path |
| `context_rollback` | 508 | `int context_rollback(uint64)` | 以仍在 FIFO 窗口内的历史节点为因果锚点创建新分支；不截断旧记录，不复用 sequence |
| `context_clear` | 509 | `int context_clear(void)` | 清空当前 Agent Context Path |
| `agent_watch` | 510 | `int agent_watch(int, const char *)` | 注册 Agent Loop 事件类型和短文本过滤器 |
| `agent_wait` | 511 | `int agent_wait(struct agent_event *, int)` | 等待事件或 timeout，成功消费事件后写入 Context Path |
| `agent_heartbeat` | 512 | `int agent_heartbeat(int)` | 旧版兼容入口；设置间隔，传 0 停止，仍使用统一边界校验 |
| `agent_wake` | 513 | `int agent_wake(int, struct agent_event *)` | 向目标 Agent 投递 `AGENT_EVENT_MESSAGE` 消息事件 |
| `agent_file_meta_init` | 514 | `int agent_file_meta_init(void)` | 重新加载文件对象元数据、重建索引并启用扫描 |
| `agent_file_meta_set` | 515 | `int agent_file_meta_set(struct agent_file_meta *)` | 插入或合并更新文件元数据，状态变化可触发事件 |
| `agent_file_query` | 516 | `int agent_file_query(struct agent_file_query *, struct agent_file_query_result *)` | Agent 文件属性查询，成功后写入 Context Path |
| `agent_create_role` | 517 | `int agent_create_role(int role)` | 按真实内核角色创建 Agent 子进程 |
| `agent_unwatch` | 518 | `int agent_unwatch(int, const char *)` | 删除匹配 watch；`AGENT_EVENT_NONE` 加空 filter 表示清空全部 watch |
| `context_detail` | 519 | `int context_detail(uint64, struct agent_context_detail *)` | 按 sequence 查询完整工具调用详情 |
| `agent_wait_cancel` | 520 | `int agent_wait_cancel(int pid, const char *reason)` | 给目标 Agent 设置一次性等待取消令牌，并唤醒目标 |
| `agent_sched_snapshot` | 521 | `int agent_sched_snapshot(struct agent_sched_record *, int)` | 查询当前 Agent 最近 8 次调度原因记录 |
| `agent_trace_snapshot` | 522 | `int agent_trace_snapshot(struct agent_trace_record *, int)` | 合并返回当前 Agent 的 Context 摘要和调度原因短记录 |
| `agent_audit_snapshot` | 523 | `int agent_audit_snapshot(struct agent_audit_record *, int)` | orchestrator 查询当前 workflow scope 的审计短记录 |
| `agent_audit_query` | 524 | `int agent_audit_query(struct agent_audit_filter *, struct agent_audit_record *, int)` | 在当前 scope 可见审计窗口内过滤查询 |
| `agent_sched_config` | 525 | `int agent_sched_config(struct agent_sched_config *)` | orchestrator 配置目标 Agent 的调度 policy、weight、priority 和 budget |
| `agent_span_trace_snapshot` | 528 | `int agent_span_trace_snapshot(struct agent_audit_record *, int)` | 当前 Agent 查询当前 span 的系统级短记录 |
| `agent_timeline_snapshot` | 529 | `int agent_timeline_snapshot(struct agent_timeline_record *, int)` | 统一导出当前 Agent 可见的 Context、调度和审计短记录 |
| `agent_timeline_query` | 530 | `int agent_timeline_query(struct agent_timeline_filter *, struct agent_timeline_record *, int)` | 在统一 timeline 上执行内核侧过滤查询 |
| `agent_provenance_snapshot` | 531 | `int agent_provenance_snapshot(struct agent_provenance_edge *, int)` | 导出当前 Agent 可见的 Context 和审计因果边 |
| `agent_timeline_wait` | 532 | `int agent_timeline_wait(struct agent_timeline_filter *, int)` | 等待当前可见 timeline 出现匹配记录或 timeout |
| `agent_timeline_read` | 533 | `int agent_timeline_read(struct agent_timeline_filter *, struct agent_timeline_record *, int, int)` | 等待匹配 timeline 记录并在同一次 syscall 中复制记录 |
| `agent_ledger_snapshot` | 534 | `int agent_ledger_snapshot(struct agent_ledger_summary *)` | orchestrator 读取当前 workflow scope 的运行账本摘要和链尾 hash |
| `agent_file_edit_begin` | 535 | `int agent_file_edit_begin(const char *, uint64, int, struct agent_file_edit_state *)` | 为真实文件申请独占编辑租约 |
| `agent_file_edit_commit` | 536 | `int agent_file_edit_commit(uint64, uint64, struct agent_file_edit_state *)` | 按租约和期望版本提交编辑 |
| `agent_file_edit_abort` | 537 | `int agent_file_edit_abort(uint64)` | 放弃当前进程持有的编辑租约 |
| `agent_file_edit_state` | 538 | `int agent_file_edit_state(const char *, struct agent_file_edit_state *)` | 查询真实文件当前编辑租约和版本状态 |
| `agent_worker_create` | 539 | `int agent_worker_create(const char *, uint64)` | 创建等待执行指定 immutable、domain-safe worker 映像的非 Agent workflow worker，并按映像安全配置衰减文件系统能力 |
| `agent_route_config` | 540 | `int agent_route_config(int, int, uint64, int)` | 为指定 source/target Agent 授予或撤销 `MESSAGE` / `LLM_DONE` 定向 IPC 路由 |
| `agent_workflow_create` | 541 | `int agent_workflow_create(int role)` | 由可信 bootstrap factory 创建新的、内核签发的 workflow scope，并在其中创建根 Agent |
| `agent_scope_delegate_fd` | 542 | `int agent_scope_delegate_fd(int fd)` | 为调用线程的下一次安全主体创建授予一个一次性 pipe fd 继承票据 |
| `kernel_work_last_preemptions` | 543 | `long kernel_work_last_preemptions(void)` | 读取当前线程上一 syscall 的内核工作重调度次数；短调用稳定返回 0 |
| `io_policy_info` | 544 | `int io_policy_info(struct io_policy_info *)` | 读取当前持久 owner 和前台 I/O class 的预算、等待、物理传输及 cache 观测，不修改策略状态 |
| `agent_workflow_close` | 545 | `int agent_workflow_close(uint64 scope_id)` | 由绑定根 controller 或可信 bootstrap factory 使 workflow 进入 CLOSING，并协作终止其全部成员 |
| `agent_workflow_lifecycle_info` | 546 | `int agent_workflow_lifecycle_info(struct agent_workflow_lifecycle_info *, const struct agent_workflow_lifecycle_key *)` | 读取调用进程自身的 lifecycle/runtime 快照，并可精确比较 expected key；不查询其他进程，也不授予关闭权 |
| `sys_tool_call` / `tool_call` | 547 | `int sys_tool_call(struct agent_request_v2 *, struct agent_response_v2 *)` | V2 sized typed KV 工具调用；顺序无关，并严格拒绝未知、重复、错类型、错 size 和错 version 参数 |
| `sys_tool_list` / `tool_list` | 548 | `int sys_tool_list(struct agent_tool_desc_v2 *, int)` | 查询 V2 sized 工具描述；wrapper 固定传入 descriptor size 和 V2 version |
| `agent_observe_recovery` | 550 | `int agent_observe_recovery(struct agent_observe_recovery_request *, void *)` | 仅供 bootstrap 绑定的 Recovery 读取、列举和擦除已封存 workflow 的持久观测证据 |
| `sys_agent_heartbeat_set` / `agent_heartbeat_set` | 552 | `int sys_agent_heartbeat_set(uint64)` | 独立心跳设置入口；从调用时 tick 重新计算周期 |
| `sys_agent_heartbeat_stop` / `agent_heartbeat_stop` | 553 | `int sys_agent_heartbeat_stop(void)` | 独立且幂等的心跳停止入口 |
| `agent_audit_receipt` | 557 | `int agent_audit_receipt(struct agent_audit_receipt_request *)` | orchestrator 为当前 lifecycle 中的精确审计记录取得或等待持久性回执；结果区分 `PENDING`、`DURABLE`、`FAILED`，并拒绝陈旧 lifecycle 或伪造 receipt id |
| `agent_resource_snapshot` | 559 | `int agent_resource_snapshot(struct agent_resource_snapshot *)` | 仅允许 bootstrap 绑定的非 Agent 资源域管理员读取版本化全局资源计数和空闲页快照；普通 Agent 与普通进程均被拒绝 |
| `agent_performance_snapshot` | 560 | `int agent_performance_snapshot(struct agent_performance_snapshot *)` | 仅允许由签名 bootstrap 策略建立的资源域观测 authority 读取版本化的全局累计性能计数；scope-local Agent capability 不能授予该全局权限 |

`agent_run` 和 `context_snapshot` 是性能主路径。syscall 503/504 保持 V1 名称协议二进制兼容；新程序应优先使用 syscall 547/548 的 V2 sized typed KV 协议。`agent_trace_snapshot` 是单个 Agent 的运行查看和排查主路径，用于把工具调用历史与调度原因放进同一组短记录中。`agent_span_trace_snapshot` 读取当前 Agent 所在可信 span 的系统级短记录，使参与协作的 Agent 能解释本轮协作中的 Context 和事件来源。`agent_timeline_snapshot` 是统一导出入口，把当前 Agent 可见的 Context、调度和审计转换成同一种 record，便于科研平台页面直接读取。`agent_timeline_query` 在同一组可见记录上执行 source、tick、span、pid、kind、tool、event、status、flags 和 after-cursor 过滤，减少页面重复拉取和用户态筛选，也支持页面拿上一条记录作为游标继续读取后续记录。`agent_timeline_wait` 复用同一 filter，在没有匹配记录时让 Agent 睡眠；新记录写入时内核把新记录规范化为 `agent_timeline_record`，并直接用等待者保存的完整 filter 判断是否唤醒。`agent_timeline_read` 在同一套规则上把等待和复制合并为一次 syscall，减少页面或 Agent worker 的 wait 后再 query 成本。`agent_file_edit_begin`、`agent_file_edit_commit`、`agent_file_edit_abort` 和 `agent_file_edit_state` 是真实文件编辑冲突控制接口；内核用真实 `dev + inum + incarnation` 识别文件，并在 `write`、`O_TRUNC`、`unlink` 路径上检查租约持有者和精确 scope。`agent_worker_create` 不创建 Agent 身份或 Agent Context，而是让 orchestrator 在自己的 scope 内显式建立一个最小权限 workflow worker；子进程随后必须执行创建时绑定的 immutable、domain-safe worker 映像才能取得受限文件系统能力。`agent_workflow_create` 是唯一创建新 workflow security boundary 的用户 ABI，角色委派接口本身不能铸造新 scope；`agent_workflow_close` 是对应的可信终止 ABI，关闭权由生命周期账本中的唯一根 control id 或可信 factory 身份决定。`agent_workflow_lifecycle_info` 只是 self-only 观测/比较接口，其返回 key 不是可转移权限。`agent_scope_delegate_fd` 只让调用线程的下一次 workflow、Agent、worker 或降权普通子主体显式携带选中的 pipe 端点。`agent_provenance_snapshot` 导出同一可见范围内的 Context 和审计因果边，用于页面绘制可信动作之间的来源关系。`agent_audit_snapshot` 和 `agent_audit_query` 是 orchestrator 的 scope 内系统级观测入口；底层物理表共 512 槽，但调用者最多看到自己的 128 槽配额窗口。`agent_ledger_snapshot` 在同一 scope 的逻辑账本上返回可见范围、总量、已淘汰数、分类计数和账本 hash。

`agent_resource_snapshot()` 使用 sized-prefix 输出：前两个字段始终是 `version` 与完整
`struct_size`，较旧调用者可以传入较小但不低于 8 字节的缓冲区。`measured_mask` 明确
区分策略已配置、具有全局计数器的资源种类与零值占位；每一类同时返回 capacity、used、
pending 以及 ordinary/reserved 分类。`measured_mask` 不代表逐 resource account 的用量
覆盖，也不覆盖 BIO 专属速率 bucket/debt，更不是全局无泄漏声明。入口只接受内核 bootstrap 策略
绑定、持有资源域管理权且尚未成为 Agent 的进程，用于 Task 6 验收和可信诊断，不是面向
workflow Agent 的全局信息查询能力。

`agent_performance_snapshot()` 同样使用 sized-prefix 输出：调用者至少提供前 8 字节，
内核填写 `version`、完整 `struct_size`，并只复制调用者容量与当前结构大小中的较小值。
入口只接受由签名 bootstrap 策略建立且持有资源域管理标记的观测 authority；该 authority
之后即使进入 Agent 状态也保留观测资格。普通进程以及只具备 scope-local capability 的
Orchestrator、Recovery 或 worker 均返回 `AGENT_STATUS_DENIED`，不能借
`AGENT_CAP_ORCHESTRATE` 读取其他 workflow 的活动侧信道。

`counter_scope` 当前固定为 `AGENT_PERFORMANCE_COUNTER_SCOPE_GLOBAL`，表示快照的默认
计数口径。`observer_workload_syscalls` 是明确的例外：它只累计当前观察进程在注册工作负载
syscall 集合中的调用，报告口径为 `observer_process`。除这个例外以及 `version`、
`struct_size`、`counter_scope`、`sample_tick`、`observer_lifecycle_id` 和
`observer_lifecycle_generation` 外，其余字段都是本次启动以来的内核全局单调累计计数：文件系统 epoch 成功提交、进入 epoch 的
buffer 和去重 stage，真实块写与 durable FLUSH，COW 共享映射、故障复制与原地提升，以及
只读执行映像 cache 的 hit、miss、共享页与 eviction。两个 `observer_lifecycle_*` 字段只
记录调用者当时的 workflow lifecycle 标签；它们不是 VFS scope id，也不能把这些全局计数
解释成该 workflow 的独占用量。

块写与 FLUSH 字段只累计设备成功完成的传输；失败请求不冒充 durable 完成。内核使用普通
`copyout` 返回快照，因而不会从全局 COW 计数中隐藏真实页复制。测量器必须在 before 之前
预触碰并私有化 before/after 输出缓冲，避免结果投递本身落入被测区间。

区间测量必须在同一次启动、相同 ABI version 和相同默认 counter scope 下取得 before/after，
要求 `after.sample_tick >= before.sample_tick` 且每个累计字段均不小于对应 before，再执行
无符号 `after - before`。任一字段倒退、重启、版本变化或计数回绕都使该样本对无效，不能
把差值截成零。当前 ABI 不携带 boot UUID，因此测量器必须在同一个 Guest 会话中配对样本，
不能仅根据计数值猜测是否重启。不同子系统分别在自己的同步边界生成快照，因此差值表示
采样窗口内的全局增量，不是跨字段的瞬时原子事务；窗口内存在其他工作时也会被计入。竞赛
测量应串行化目标负载或明确报告并发背景，不能仅凭 observer lifecycle 标签将差值归因给
单个 workflow。

当前认证目标是单核 RISC-V。内核用一个外层 `intr_save` 临界区包住所有 policy counter
和三类空闲页的读取，使一次 `agent_resource_snapshot()` 表示禁止本地中断下的一致单核
切片；该约束没有声称在未来多核实现中天然提供跨 CPU 原子性。Task 6 会序列化
ordinary/reserved 的原始 used/pending 分类，Guest 与 Host 分别重算总数，并用第一个
workflow 的 before 和最后一个 terminal after 执行一次全序列增长上限及平台期/回收
检查。平台期只检查 load workflow 之间的不增长；若所有 load 都增长，terminal after 必须
严格低于最后一个 load after，不能用无负载 terminal 的相等值冒充平台期。因此逐 workflow
的局部上限不能掩盖每轮少量、最终线性累积的泄漏。

`agent_observe_recovery()` 使用严格的 version/size/flags 请求。`LIST` 只列出 lifecycle 已失效或不再绑定原 scope 的封存证据；`READ` 必须携带完整、不可复用的 lifecycle key，并按 audit sequence 游标读取；`REAP` 返回内核保存的 opaque completion token；`STATUS` 只接受同一 lifecycle 的原 token。token 只在内核取得非零 durable-section serial 和持久提交目标后签发，并同时绑定 lifecycle generation、发起时的 bank generation、serial 和目标，不能用旧 workflow、旧 bank 或另一条擦除事务的完成值确认新擦除；重启恢复出的无 token intent 会在 Recovery 首次显式请求时锁定当次 active bank generation，后续重试不改写。若初次 `REAP` 的最终 request copyout 失败，内核按唯一 lifecycle 在磁盘 scope 查找前重发内存态 AUTHORIZE/ERASE/DONE 的同一 token，不启动第二条擦除。`STATUS` 先只读确认 DONE，生成绑定 slot、scope、lifecycle、token、source generation 和 active bank generation 的内核 cookie；仍持有 metadata gate 时，最终 response copyout 成功后才执行无 I/O 的精确消费。copyout 或 cookie 校验失败都保留 token，成功消费后旧 token 立即失效；期间新 admission 不能清除或复用该槽。内核 teardown 发起的无 token 擦除则在双副本完成后自动释放槽。若持久 sink 暂时返回 0，`REAP` 返回 `RETRY` 且内核保留可重试 intent，不签发零目标 token；后台接纳并完成双副本后，遗弃的 intent 也可安全回收。durable-section serial 到达 `UINT64_MAX` 后停止分配，不回绕也不覆盖已有 pending intent；新请求 fail closed，旧请求仍按原 serial 完成。证据擦除不擦除 lifecycle 槽 generation 及 audit/span/event/control/agent allocator 的持久高水位；event 编号空间耗尽后 IPC 返回 `NO_SPACE`，不会发布零身份。只有由可信 bootstrap factory 创建并在创建时绑定的 Recovery control identity 可以使用该接口，普通 Agent、Orchestrator 和后来复用 PID/PCB 的进程均被拒绝。

五组 allocator 和每个 lifecycle 物理槽都使用 exclusive-end durable lease。内核先把下一段 lease 写入并确认 durable-section 目标已复制，随后才允许从该段发布身份；启动恢复把 volatile next 直接提高到已持久 lease end，因此未使用的尾段也会被牺牲，在当前设备 flush/durable-barrier 契约与认证 QEMU `SIGKILL` 后重启的故障模型中不会重放。首次 lease 尚未复制、运行期续租只能返回 pending、持久层终止失败或编号耗尽时，Agent/lifecycle admission 保持关闭或对应分配 fail closed。这里的 durable 语义不覆盖物理控制器易失缓存、整机供电中断或永久介质故障；它保证的是上述模型内的“不复用”，不是连续编号。

`agent_audit_receipt()` 使用 `agent_observe_abi.h` 中固定 72 字节、version/size/flags 严格校验的请求。`STATUS` 以零 `receipt_id` 查询当前 scope 的精确 `(lifecycle, sequence, record_hash)`，返回绑定该 ledger 槽的 opaque id；`WAIT` 必须回送该 id，且只执行有界的持久推进尝试。只有当前 lifecycle 中具备 `AGENT_CAP_ORCHESTRATE` 的 Agent 可以调用，普通进程、Recovery、陈旧 lifecycle 和伪造 id 分别得到明确拒绝。`PENDING` 只表示该记录有一个尚未证明的写回目标，不能作为持久证据；即使目标代数已经复制，内核仍会重新读取经过验证的 active durable section，并仅在其中仍存在完全匹配的 lifecycle、sequence、record hash 和 receipt id 时返回 `DURABLE`。记录在 checkpoint 前被有界窗口淘汰、目标已越过但 active image 中缺失，或 receipt 所属 ledger 槽被复用时，结果为 `FAILED` 或 `STALE`，不会仅凭 `replicated(scope,target)>0` 升级。receipt sidecar 与 512 个 ledger 槽同寿命，淘汰时一同清除，不使用可被独立耗尽的全局 token 表。

### 基础兼容系统调用：uCore

| syscall | 编号 | 用户态原型 | 说明 |
| --- | ---: | --- | --- |
| `mailread` | 401 | `int mailread(void *buf, int len)` | 非阻塞读取当前 PUBLIC 进程的 legacy mail 队列；无消息返回 0 |
| `mailwrite` | 402 | `int mailwrite(int pid, void *buf, int len)` | 向同一授权域内的 PUBLIC 目标写入最多 256 字节 |
| `trace` | 410 | `int trace(enum trace_request req, unsigned long id, uint8 data)` | 支持 `TRACE_READ`、`TRACE_WRITE` 和 syscall 计数查询 |

这些接口用于保留代表性基础 uCore 用户测试能力。Agent-OS 的最终验收主路径仍是 `CHAPTER=agent` 下的专项程序。

`mailread` / `mailwrite` 保留 16 槽、每条最多 256 字节的 legacy PUBLIC 接口。普通 PUBLIC 仅能与同一 ACTIVE、generation-safe EXEC account 内的 PUBLIC 互通；workflow 内 PUBLIC 还必须命中相同 ACTIVE lifecycle key、动态 scope 和非零 OPEN controller lineage。每次进程发布和 PUBLIC exec 都获得新的 endpoint generation，PID 或 PCB 槽不能把旧队列授权带给新端点。Agent 端点、不同账户、缺失或失效 controller、跨 lifecycle/scope/lineage 均返回 `-1`。队列首次合法写入时按目标 EXEC account 分配两页 sidecar，空读不分配，排空后保留至 teardown 并在账户释放前退款。读取先在内核中保留队首，只有用户态 `copyout` 成功后才提交出队；失败会撤销读取保留而不丢消息。`mailread` 无消息时返回 0，成功时返回读取字节数；目标不存在、长度非法、队列满或用户指针错误也返回 `-1`。

`trace` 的 `TRACE_READ` / `TRACE_WRITE` 只做 1 字节用户地址读写检查。`TRACE_SYSCALL` 返回已登记 syscall ID 的累计进入次数，未登记编号返回 0，超出 ABI 范围返回 -1；查询 `SYS_trace` 时本次 `trace` 调用也计入。计数使用紧凑内部槽并在 `INT_MAX` 饱和，不会按稀疏 ABI 编号放大每个进程的常驻空间。AgentOS-uCore 的镜像构建器从配套 ELF 提取只读可执行段与可写段的页对齐分界点，loader 校验该布局后把代码页映射为 RX，把数据、bss、用户栈和 Agent Context 映射为 RW+NX；用户页不会同时拥有写和执行权限。

### 块 I/O 策略观测

```c
int io_policy_info(struct io_policy_info *info);
```

用户态 wrapper 自动把 `sizeof(*info)` 作为 syscall 544 的隐式第二参数传给内核；底层内核 ABI 是 `(addr, user_size)`。内核要求 `user_size >= 8`，先生成当前完整结构，再只复制 `min(user_size, sizeof(struct io_policy_info))` 字节。前两个 32 位字段固定为 `version` 和 `struct_size`，以后只能在结构尾部追加字段。因此旧用户库仍按它编译时的较小 `sizeof` 读取稳定前缀，并可用 `struct_size` 判断当前内核完整结构的大小，不需要因尾部扩展破坏已有读取程序。

该接口是只读观测面。普通进程返回安装级 PUBLIC owner，workflow 进程返回带 `IO_POLICY_OWNER_SCOPE_FLAG` 的稳定 scope owner；Orchestrator/Recovery 的前台请求使用 `CONTROL` class，其他 workflow 与 PUBLIC 前台请求使用 `NORMAL`。内核扫描、metadata checkpoint 和 scope reclaim 不伪装成调用 scheduler 的进程，而是显式使用 SYSTEM 或触发 workflow 的 `BACKGROUND` class。owner 在 syscall 或后台 job 开始时捕获，PID 退出、重新 fork 或调度切换不会重置其账本。

预算信用代表一次已提交到设备的 1 KiB 块传输或一次 FLUSH；提交后的失败仍计费，未提交的 capability/range 拒绝不计费。refill 以 I/O policy tick 为单位：

| owner / class | burst | 每 tick refill |
| --- | ---: | ---: |
| PUBLIC / NORMAL | 32 | 16 |
| 每个 active workflow / NORMAL | 24 | 12 |
| 每个 active workflow / CONTROL | 48 | 24 |
| 每个 active workflow / BACKGROUND | 8 | 4 |
| 每个 retiring workflow / BACKGROUND | 8 | 4 |
| SYSTEM / SYSTEM | 96 | 48 |
| SYSTEM / BACKGROUND | 16 | 8 |
| 前台机会流量门 | 560 | 280 |
| 设备根 bucket | 560 | 280 |

syscall 入口只计算一次策略位图，分别表示是否可能触盘、是否需要文件系统 epoch。普通短 syscall 的策略为零；`read`/`write` 只固定一次文件对象并按 inode 类型补位，`openat` 只在 create/truncate 时补充 epoch。BIO 准入、失败回滚和结算从同一事务上下文读取该策略，未知 syscall 保守进入慢路径。这样传统接口不再重复执行两套 syscall switch 和 FD 分类。

每个可能触盘的 syscall 再在 BIO 内取得 owner/shared 与 device 两级 reservation。每次真实 `disk_submit` 是唯一物理计费边界：两级 credit 在同一关中断窗口预留，提交或取消恰好一次。无竞争时，前台 owner 在自身 reserved bucket 后可借 shared；出现异域活动、排队、retire/quiesce 或既有 debt 后停止直接借用，`BACKGROUND` 不能借 shared。shared 永不带债；已接纳的有界原子请求可以在请求上界内形成 owner/device debt，由 quiescent checkpoint 或 teardown settlement 清偿。SYSTEM/CONTROL 保留进展仍受 protected aggregate envelope 约束。bucket 按 `last_refill_tick` 惰性补充，不再由周期 tick 扫描所有账户；没有物理提交的 reservation 退款，volatile overlay 命中不计费，写回块和最终 FLUSH 在统一提交边界分别计费。

设备根定义物理机会容量，owner/class 的保留总和和 shared gate 分别受编译期 envelope 约束。两级 reservation 只在 BIO 内保存紧凑来源/device receipt；shared 不带债，已接纳的有界原子请求可以形成有上界的 owner/device debt，由 checkpoint、teardown 和后续 refill 清偿。具体 burst/refill、active/retiring owner 上界与 cache floor/cap 以 `io_policy.h` 为准。

scheduler 每轮先把 `current_thread` 指向 idle context，安装 kernel trap 向量，短暂开启中断后再执行后台维护和选择下一线程。这个固定交付窗口不是按进程或 syscall 加白名单；它保证唯一 runnable 线程反复在内核态 pipe 条件路径 `yield()`、长期不返回用户态时，pending timer/device 中断仍能推进 I/O debt、token refill 和设备完成。中断窗口结束后 scheduler 再关中断进入两级选择：先严格轮转 active `resource_domain_id`，再从选中域的线程队列选择一个候选。这里的 `resource_domain_id` 仅是 CPU 调度分区索引，不是配额账户。

资源控制器是内核 admission 契约，不新增用户态 syscall。进程和线程保存 generation-safe EXEC `resource_account`；文件系统根据稳定 `storage_principal_id`/workflow owner 取得 STORAGE account。PROCESS、THREAD、FILE_OBJECT、FS_BLOCK、FS_INODE、BUFFER_CACHE 和 AGENT_STATE_PAGE 统一使用 ordinary/reserved 计费。进程 + t0 与 pipe 两端通过一个向量 reservation 原子预留；失败取消，成功提交，账户进入 CLOSING/DRAINING 后要等成员、usage 和 pending reservation 全清才可复用。BIO 速率、债务和在途请求由 I/O owner 专属 bucket 结算，不再嵌入每个通用账户。线程仍保存 `resource_domain_id` 供 scheduler 分区，但该字段不参与配额退款。

由主线程触发的正常退出、用户 fault/非法指令、workflow revoke 和未发布构造失败进入同一状态机：`LIVE -> REQUESTED -> QUIESCING -> DETACHED -> RECLAIMING -> SETTLING -> HANDOFF -> PUBLISHED -> RECYCLED`。进程生命周期只调用 phase-aware、幂等的 `agent_proc_teardown()` 处理 Agent 私有状态：QUIESCING 撤销控制权，RECLAIMING 释放 Context 状态并清除身份，SETTLING 验证 Agent 私有状态与 Agent state page 计费为空；通用 process/thread/file/I/O 账目由外层 teardown 继续结算，原始 Context 清理函数不再是进程析构 API。唯一 `teardown_owner_tid` 推进状态，第一次退出码生效；REQUESTED 后不得发布新进程所有对象。它依次展开 sibling、分离 child/FD、释放文件/Context 状态/VM、结算 cleanup I/O 与 resource account、清除 terminal 凭据并释放 lifecycle。scheduler 已切至 idle stack 后才释放最后物理栈页、发布退出并复用槽。非主 sibling 的正常退出或 fault 仍只走 `thread_exit_current()`。

内核栈 ABI 不对用户态暴露。每个线程仍有 16 KiB 虚拟栈和 4 KiB guard/canary，但物理页只在 admission 时映射并在 scheduler handoff 后释放。32 MiB 是全部虚拟槽容量；8 MiB 是 reserved thread 物理池，不是每次启动都为全部线程常驻 32 MiB。

`struct io_policy_info` 的字段语义如下：

| 字段 | 说明 |
| --- | --- |
| `version` | 当前为 `IO_POLICY_VERSION=5` |
| `struct_size` | 当前内核完整 `struct io_policy_info` 的字节数；调用者收到的前缀长度仍由它传入的 `user_size` 决定 |
| `owner` / `io_class` | 调用者的稳定 owner 与前台 class |
| `tokens` / `leased` / `debt` | 当前 class 可用信用、尚未在真实 `disk_submit` 边界结算的 lease 和待偿还超额传输 |
| `class_burst` / `class_refill` | 当前 class 配置 |
| `shared_tokens` / `shared_leased` | 前台机会流量门的可用与已租信用；它与设备根同时扣减，不是额外物理容量 |
| `device_burst` / `device_refill` | 设备根 bucket 配置；已接纳的有界原子请求可形成受请求上界约束、由 checkpoint/teardown/refill 清偿的 owner/device debt |
| `device_tokens` / `device_leased` / `device_debt` | 设备根可用信用、未提交 lease 和累计待偿还传输；每次真实 `disk_submit` 是唯一计费边界，shared 与设备根同步扣减且不叠加容量 |
| `waiters` | admission 与 debt waiter 总数 |
| `admission_waiters` / `debt_waiters` / `admission_granted` | 两阶段等待队列及当前 FIFO baton 状态 |
| `admissions` / `throttles` / `waits` / `refills` | owner 聚合的接纳、限流、睡眠和补充计数 |
| `reserved_grants` / `shared_grants` | owner 保留信用和共享信用的累计授予数 |
| `physical_reads` / `physical_writes` | 在真实 `disk_submit` 边界按稳定 owner 累计的块传输；完成失败另记 `failed_transfers`，不撤销已经发生的物理提交 |
| `physical_flushes` / `failed_transfers` | 已提交的真实 FLUSH 数，以及提交后失败的 read/write/FLUSH 数；capability/range 拒绝和 volatile overlay 命中不计入 |
| `unreserved_transfers` | 未处于 syscall/background reservation 的防御性计数；正常用户 I/O 应保持不增长 |
| `cache_resident` / `cache_floor` / `cache_cap` | 当前 owner 的 buffer cache 驻留数、受保护下限和稳态上限；必要的 transient 引用可暂时越过 cap，归零即失效 |
| `cache_hits` / `cache_misses` / `cache_evictions` | owner 聚合 cache 观测 |
| `completion_sequence` | 该 owner 最近一次物理完成对应的全局单调序号 |

buffer cache 固定为 256 个 1 KiB buffer。SYSTEM 的 floor/cap 为 40/96，PUBLIC 为 24/48，每个 active workflow 为 36/64。新分配的数据块通过 `bclaim()` 绑定当前 sponsor；共享文件系统 metadata 不会因一次跨域读取被改绑。跨 sponsor 命中可以复用同一块，但不会刷新原 sponsor 的 LRU。victim owner 只有高于自己的 floor 时才能捐出空闲块；调用者达到 cap 后只能轮换自身驻留，必要的超 cap transient buffer 在最后释放时立即失效。scope 进入 retirement 时撤销 36/64 active 分区，仅在该 scope 的轮转清理 job 实际运行期间提供 3/8 的临时 floor/cap。后台 job 的 cache sponsor 始终是其 SYSTEM 或 workflow owner，不存在所有 workflow 共用的全局后台 cache 身份。

每个 `struct buf` 还保存 exclusive holder、递归 `hold_depth` 和私有 holder wait queue。命中同一 `dev + blockno` 的其他进程必须等待当前 holder 完整 `brelse()`，不能只靠引用计数并发修改同一块。线程和 background job 分别累计自己持有的 buffer 数；持有任一 buffer 时预算 checkpoint 只能返回 deferred，不能睡眠。`readi()` / `writei()` 用 `bio_fs_atomic_enter/leave()` 包住复合文件系统原语，普通 checkpoint 在原子段内也只能延后；只有调用者已经释放全部 buffer、并已提交对外可见的 inode/目录状态时，才可调用 quiescent checkpoint 睡眠偿债。进入 qmap-first、truncate 或退役清理等不可回滚阶段后使用 cleanup 变体，即使线程收到退出请求也要完成有界的前向提交。

因此文件读写可以在已提交块边界返回合法短 I/O。loader 和 metadata bank 的 exact-read helper 会在文件系统原子段外执行预算 checkpoint，并从正数短读的已完成 offset 继续；临时 interruption 与持久 bank 损坏使用不同状态，不会把公平限流误判成镜像损坏。

## 上下文 ABI：Agent Context

| 项目 | 值 |
| --- | --- |
| 起始地址 | `AGENT_CONTEXT_BASE` |
| 用户态计算 | `AGENT_TRAPFRAME - (16 + AGENT_CONTEXT_PAGES) * AGENT_PAGE_SIZE` |
| 大小 | `AGENT_CONTEXT_SIZE = 7 * 4096` |
| 当前大小 | 28672 字节 |
| 记录容量 | `AGENT_CONTEXT_MAX_RECORDS = 128` |
| Context 版本 | `AGENT_CONTEXT_VERSION = 9` |
| 权限 | 前 6 页用户只读、内核可写；末页用户可读写；全部不可执行 |

说明：用户程序仍以 flat binary 内容装入，但镜像中的 `exec_layout_version` 和 `exec_rw_offset` 来自配套 ELF 并由 loader 重新校验。分界点之前的程序页为 RX，之后的数据、bss 和用户栈为 RW+NX。Agent Context 前 6 页为 R+NX，末页 cache 为 RW+NX。布局缺失、超出有效范围或要求 W+X 的映像不会被装载。

布局：

| 偏移 | 内容 |
| --- | --- |
| `0` | `struct agent_context_header` |
| `sizeof(struct agent_context_header)` | `struct agent_result` |
| `4088` | 只读 publication sequence，偶数表示稳定，奇数表示正在发布 |
| `AGENT_CONTEXT_RECORDS_OFFSET = 4096` | `struct agent_context_record[128]` |
| `header.user_cache_offset` | 用户自管 cache 起点，Context v9 为 24576 |
| `header.user_cache_size` | 用户自管 cache 大小，Context v9 为 4096 |

Context ABI v9 在 header 中公开不可变 workflow lifecycle key、当前 `branch_generation`、`visible_head_sequence`、`active_path_count`、`active_path_oldest_sequence` 和 `eviction_policy`。每条 record 保存自己的 `branch_generation` 以及独立的 `path_parent_sequence`；后者描述本地分支父子关系，不复用可能来自跨 Agent IPC 的 provenance `cause_sequence`。初始化和 `context_clear()` 都从 lifecycle ledger 取得新的 branch generation；`context_rollback()` 同样创建新 branch，而不是重写旧 record 或复用旧 sequence。

内核和用户态共享同一组 Context 物理页。header、latest result 和 128 条 record 位于前 6 页，用户页表不授予写权限；内核直接更新这些页，不再维护 shadow、镜像副本或同步复制。第 7 页完整留给用户 cache，不参与可信历史。完整 detail 及 source/span attribution 位于 9 页 Context sidecar；IPC、事件来源归因、调度轨迹和观测状态共用一个 4 KiB 冷页。PCB 只保存页指针，使 `struct proc` 从 `23464` B 降为 `10024` B。

运行时把 9 Context sidecar + 1 cold sidecar + 7 mapped pages 作为一次 17 页 `RESOURCE_AGENT_STATE_PAGE` 请求计入 EXEC account，共 `69632` B（68 KiB）。原子预留后才逐页分配，任一失败都统一释放和退款，普通进程不承担这些页。当前每进程/全局池/ordinary 池/reserved 池/ordinary 域/reserved 域基线为 `69632/8912896/6684672/2228224/4456448/557056` B，上限为 `73114/9358541/7018906/2339636/4679271/584909` B；实际门禁以 `ci/kernel-budgets.json` 为准。

同一进程的 sequence 接纳、工具执行、Context header/record、`sys_context_*`、IPC 记录、文件查询和 wait 归因通过可睡眠、FIFO、可重入的 Context commit lane 串行提交；同时需要 metadata 时固定按 `lane -> metadata` 加锁。append 按 record、latest、header 顺序直接写入只读视图的物理页，并以 4088 偏移处的奇偶序列包围整次发布；clear 跳过该序列且只清内核管理区，不覆盖用户 cache。`agent_call_count`/header `total_calls` 表示已经接纳并保留的调用序号，可在慢调用执行期间暂时领先；`latest_sequence` 是 result、record、hash 与 header 全部发布后的已提交水位。

这里的“事务提交”只指 Context 自身的可信页、sidecar 和 PCB 字段。事件投递、watch 配置、文件/metadata 更新等外部效果不是与 Context 记录组成的跨子系统事务；这些接口的返回语义以其主操作为准，Context system record 是同一 lane 内的运行归因。固定映射在提交前验证，若映射不变量在 commit 中破坏则内核 fail-stop，而不是在外部效果已发生后伪造回滚。

用户自管 cache 独占末页，不进入权威历史，也不会被 `context_snapshot()` 覆盖。它只用于 Agent 保存策略缓存、文件查询结构化结果或短期状态，不能作为内核可信历史。发布顺序采用 Linux perf ring 的“先写数据、后 release 发布 head”原则，并增加奇偶序列处理 clear、rollback 和 FIFO 重用造成的 ABA。`context_direct_header_snapshot()` 和 `context_direct_active_query()` 以 acquire 读取 publication sequence，复制后再次核对同一偶数；发布竞争时最多重试 8 次，随后返回错误供调用者退回 syscall。active query 以 16 字节 slot 位图完成一次反向链校验和一次正向窗口扫描，复杂度从逐索引反向重走的 O(n²) 收敛为 O(n)。需要完整请求和完整响应时使用 `context_detail(sequence, out)`，不要把 16 字节短摘要 record 当作完整日志。

固定容量达到 128 条后，内核按 `AGENT_CONTEXT_EVICT_FIFO` 淘汰最早物理记录，并通过 `oldest_sequence`、`dropped_records` 和 `eviction_policy` 明示 archive 窗口边界。FIFO 淘汰与 rollback 是两种不同机制：rollback 只移动当前分支的可见 head 和因果锚点，旧分支记录仍保持原 hash、sequence 与 branch identity，直到日后按统一 FIFO 容量规则自然离开窗口。`context_query()`/`context_snapshot()` 从 visible head 沿 path parent 返回 active path；若祖先已被 FIFO 淘汰，则 header 的 active count/oldest 收敛到仍保留的 suffix。`context_detail(sequence)` 继续提供 retained archive 的精确历史访问。

## 信息结构：Agent

`struct agent_info` 用于 `agent_info()`，关键字段如下：

| 字段 | 说明 |
| --- | --- |
| `is_agent` | 当前进程是否为 Agent |
| `agent_id` | 当前 Agent ID |
| `agent_role` | 当前 Agent 的真实内核角色；普通进程为 0 |
| `context_base` / `context_size` | Agent Context 用户虚拟地址和大小 |
| `agent_type` | Agent 类型，当前支持普通进程和 Agent 进程 |
| `heartbeat_interval` | 心跳间隔 |
| `resource_quota` | 当前 Context Path 记录配额 |
| `loop_state` | Agent Loop 状态 |
| `agent_call_count` | 已接纳并保留 sequence 的工具调用数；可能在在途调用期间暂时领先 `context_path_latest` |
| `metadata_txn_wait_count` | 当前进程实际等待 metadata 事务门的次数；仅用于争用 telemetry，不是并发提交正确性的时序门槛 |
| `metadata_writeback_dirty` / `metadata_writeback_durable` | 当前 workflow scope 已发布和已持久化的 metadata 写回代数；两者相等表示该域没有未提交变化 |
| `metadata_writeback_requests` / `metadata_writeback_coalesced` | 当前 scope 进入写回队列的变化数，以及在已有脏代数上被合并的变化数 |
| `metadata_writeback_commits` / `metadata_writeback_pending` | 当前 scope 完成的批量 checkpoint 数，以及是否仍有等待后台写回的变化 |
| `context_path_count` | 当前可见历史记录数 |
| `context_path_capacity` | 历史记录容量 |
| `context_path_head` | 下一次写入槽位 |
| `context_path_oldest` | 最早可见 sequence |
| `context_path_latest` | 最新 sequence |
| `context_path_dropped` | 被 FIFO 淘汰的记录数 |
| `context_path_rollback_count` | 成功回滚次数 |
| `current_span_id` | 当前 Agent 因果链 span；同一链路中的记录和事件共享该值 |
| `current_cause_sequence` | 当前记录或事件默认指向的前序 Context sequence |
| `provenance_edges` | 已写入的非 root 因果关系数量 |
| `observe_epoch` | 当前内核观测 epoch；Context、审计和调度写入时递增 |
| `latest_response_offset` | latest result 在 Agent Context 中的偏移 |
| `records_offset` | record 区在 Agent Context 中的偏移 |
| `event_queue_count` / `event_count` / `event_dropped` | 当前队列长度、累计事件数和丢弃计数 |
| `watch_count` | 当前有效 watch 条件数 |
| `wait_count` / `wait_sleep_count` / `wait_wakeup_count` / `wait_cancel_count` / `timeout_count` | 等待、进入等待路径、被唤醒、等待取消和超时统计 |
| `wait_loop_count` | `agent_wait()` 的检查循环次数，用于检查有限 timeout 是否避免反复轮询 |
| `timeline_wait_count` / `timeline_wait_sleep_count` / `timeline_wait_wakeup_count` / `timeline_wait_timeout_count` | timeline 等待、睡眠、被观测事件唤醒和等待超时统计 |
| `last_heartbeat_tick` | 最近心跳 tick |
| `current_tick` | `agent_info()` 返回时的内核 Agent tick，供 timeline 等待建立未来记录过滤条件 |
| `capability_mask` | 当前 Agent 能力位 |
| `filesystem_domain` | 当前进程的可信 VFS scope ID：public 为 0、system 为 1、动态 workflow 为不小于 3 的内核签发值；2 保留为 PUBLIC 存储 principal，不是 VFS scope |
| `filesystem_capability_mask` | 当前映像实际生效的文件能力，只包含经父进程授权且不超过映像安全配置上限的 `CONTENT_READ` / `ARTIFACT_WRITE` |
| `file_scan_runs` / `file_scan_entries` | 根目录自动扫描轮数和检查过的目录项数量 |
| `file_scan_added` / `file_scan_updated` / `file_scan_removed` | 自动扫描新增、更新和清理的元数据计数 |
| `file_scan_generation` / `file_scan_pending` | 文件元数据代数和是否存在待处理扫描请求 |
| `file_digest_cache_hits` / `file_digest_cache_misses` | 真实文件内容摘要缓存命中和未命中计数 |
| `sched_policy` / `sched_weight` / `sched_priority` / `sched_budget` | 当前 Agent 调度策略、角色权重、调度优先级和预算总额 |
| `sched_dispatch_count` / `sched_event_dispatch_count` / `sched_deadline_dispatch_count` | 被调度次数、因事件获得调度的次数和 deadline 相关调度次数 |
| `sched_vruntime` / `sched_ready_tick` / `sched_last_dispatch_tick` | 虚拟运行量、最近进入可运行队列 tick 和最近被调度 tick |
| `sched_preemptions` / `sched_budget_used` | 让出处理器次数和当前预算使用量 |
| `sched_last_score` / `sched_last_reason` / `sched_trace_count` | 最近一次调度分数、原因 flags 和累计原因记录数 |
| `current_span_id` / `current_cause_sequence` / `provenance_edges` | 当前 Agent 的因果链观测字段 |

事件队列容量为 `AGENT_EVENT_QUEUE_CAP = 16`。所有带 Agent 来源、可归因到外部主体的事件合计最多占 `AGENT_EVENT_EXTERNAL_LIMIT = 12` 槽，因此 admission accounting 始终为显式 `KERNEL` origin 保留至少 4 个容量名额；其中定向 IPC（`MESSAGE` / `LLM_DONE`）和带来源的系统通知（如 `FILE_STATUS` / `JOB_DONE` / `POLICY_DENIED`）各自最多占 8 槽，为另一类外部事件各保留至少 4 个 external 名额。同一个 stable source control id 跨这两类合计最多保留 4 条未消费事件。watch 数量上限为 `AGENT_WATCH_MAX = 8`，每个目标最多保存 `AGENT_IPC_ROUTE_MAX = 16` 条 IPC 来源路由。

## 可信 workflow scope

`filesystem_domain` 不再是一个所有 Agent 共享的布尔“workflow 域”。它直接暴露当前可信 scope ID，固定语义如下：

| scope | 含义 |
| ---: | --- |
| 0 (`VFS_SCOPE_NONE`) | PUBLIC；没有 workflow 对象权限 |
| 1 (`VFS_SCOPE_SYSTEM`) | SYSTEM；构建镜像中的可信、只读共享对象，不能由普通 Agent 创建或改写 |
| >= 3 (`VFS_SCOPE_FIRST_DYNAMIC`) | 内核为一次独立 workflow admission 签发的动态 scope；当前最多同时接纳 4 个 |

数值 2 不属于 `filesystem_domain` 的可用 scope。它是磁盘容量契约中的安装级匿名 PUBLIC principal：当前系统没有 uid/tenant ABI，所以所有普通进程都用该稳定主体累计持久块和 inode，不随进程资源域退出或系统重启变化。对象授权仍由上表 scope 和 capability 决定，存储计费使用 VFS 凭据中独立的 `storage_principal_id`，不能相互替代。

业务 capability 只回答“允许做哪类操作”，scope/owner 继续回答“允许操作哪个对象”。有效授权必须同时满足：调用进程属于仍 active 的内核签发 scope、capability 包含所需位、对象 scope 与主体 scope 精确相等；显式标为只读共享的 SYSTEM 对象只在对应读取路径上作为例外。Agent metadata、dependency、action history、编辑租约、版本状态、audit、IPC route 和 wait cancel 均使用该组合，不再把同一角色或同一 capability 解释为跨 workflow 权限。

### 创建和继承

```c
int agent_workflow_create(int role);
int agent_workflow_close(uint64 scope_id);
int agent_workflow_lifecycle_info(
    struct agent_workflow_lifecycle_info *info,
    const struct agent_workflow_lifecycle_key *expected);
int agent_scope_delegate_fd(int fd);
```

`agent_workflow_create(role)` 仅允许“非 Agent、具有内核 factory/admin 状态、当前执行可信 bootstrap 映像”的 factory 调用。它申请新的动态 VFS scope、generation-safe workflow lifecycle key、EXEC/STORAGE resource account，并用一个原子 admission 建立根进程与 t0；任一步失败都走构造 teardown，不留下半初始化 scope。已在某个 scope 内的 orchestrator 即使具备 `ORCHESTRATE`，也只能用 `agent_create_role()` 在本 scope 内创建角色，不能铸造新安全域。权威 lifecycle ledger 有 8 槽，ACTIVE+CLOSING+RETIRING 合计最多 4 个；RETIRING 完成目录回收前不能让新 workflow 越过该边界。

`agent_workflow_close(scope_id)` 使用 syscall 545 发起可信终止。调用者必须是创建时绑定的唯一根 controller，且 `agent_control_id` 与当前 `(workflow_lifecycle_id,generation)` 都匹配；另一名 Orchestrator、低权限 Agent、PID/父子关系和单独的 `ORCHESTRATE` capability 都不产生关闭权。`agent_control_id` 不复用；lifecycle slot 可在彻底退休后复用，但 generation 必须增长。可信 bootstrap factory 还可以按 scope id 执行恢复性关闭。参数按完整 64 位值校验，非动态范围或高位别名返回 `AGENT_STATUS_BAD_PARAM`。

显式关闭与根 controller 的正常退出、异常退出或 terminal credential clear 汇合到同一个幂等生命周期入口。scope 原子从 ACTIVE 进入 CLOSING 后，现有 Agent/VFS capability 立即失效，新成员、pending exec commit 和新存储分配均被拒绝。内核按不可变 lifecycle key 向成员提交进程级 teardown request；即使 fork 子孙已经降权为 `is_agent=0`、`filesystem_domain=0`、capability=0，也仍属于该谱系。CLOSING 在成员完成自身 teardown 前保留完整 I/O/cache 归因，最后成员释放 lifecycle 引用后才进入 RETIRING。

同 scope 的 `agent_create_role()` 和 `agent_worker_create()` 继承该 scope，但会建立新的安全主体。pipe 是持有型 capability，不会因 scope 相同而自动进入新主体；inode 文件在 scope 不变时继续逐操作鉴权。workflow 中的普通 `fork()` 可以丢弃 Agent/VFS 凭据，却必须继承 lifecycle key；这既阻止它继续访问 workflow 对象，也阻止它靠降权逃离强制撤销。只有原本不在 workflow lifecycle 中的 PUBLIC 父子保留普通 POSIX pipe 继承。

`agent_scope_delegate_fd(fd)` 只接受当前打开的 pipe fd。调用者必须是可信 bootstrap factory 或具备 `ORCHESTRATE` 的 Agent。成功票据绑定调用线程，而不是整个进程：该线程下一次创建 workflow、Agent、worker 或发生凭据降级的普通子主体时，内核在不可让出的临界区固定精确 file 对象并消费该线程的全部票据。其他线程只能消费自己的票据；关闭、替换 fd 槽或 `exec` 会撤销相应票据，不能把旧票据转移给新对象。被标记端点才进入该子主体，子进程不继承票据；继续传递必须再次显式授权。创建 syscall 的参数、权限、映像或资源检查失败也会清除调用线程的票据。

### 只读生命周期观测

syscall 546 的共享 ABI 定义在 `agent_lifecycle_abi.h`。当前版本为 `AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION=2`，完整 `struct agent_workflow_lifecycle_info` 为 64 字节：

| 字段 | 语义 |
| --- | --- |
| `version` / `struct_size` | 内核当前 ABI 版本和完整结构大小 |
| `charged` | 当前进程是否仍持有一个有效 workflow lifecycle 计费引用 |
| `key.id` / `key.generation` | 当前进程不可变的 lifecycle 身份；`key.reserved` 固定为 0 |
| `context_lane_depth` / `context_lane_waiters` | 当前进程 Context commit lane 的重入深度和睡眠等待者数量 |
| `metadata_txn_owned` / `metadata_txn_waiters` | 当前进程是否持有 metadata transaction gate，以及等待该 gate 的线程数量 |
| `resource_account_valid` | 当前进程是否绑定有效、generation-safe 的 EXEC resource account |
| `resource_account_slot` / `resource_account_generation` | 当前进程 resource-account handle 的只读身份；slot 只有与 generation 组合才可比较 |

用户 wrapper 在 `expected == NULL` 时读取当前快照；传入非空 expected 时设置 `AGENT_WORKFLOW_LIFECYCLE_INFO_F_MATCH_CURRENT` 并精确比较完整 `(id,generation)`。匹配返回 `AGENT_STATUS_OK`，合法但不匹配返回 `AGENT_STATUS_STALE`，调用者没有有效 lifecycle 时返回 `AGENT_STATUS_NOT_FOUND`。该接口始终以 `curr_proc()` 为对象，没有 PID 或 scope 查询参数；lifecycle key 和 resource-account handle 仅供 self identity 确认与竞态测试比较，不能作为关闭、委派、对象访问或账户查询凭据。

raw syscall 使用 sized-prefix：`user_size` 至少为 8 字节，内核只复制 `min(user_size, 64)`。V2 仅在原 48 字节 V1 前缀之后追加 resource-account 字段，因此旧调用者传入 48 字节时仍得到布局不变的 V1 前缀，新调用者以返回的 `version`/`struct_size` 判断尾部是否存在。短于公共头、坏输出地址、未知 flags 或非法 expected key 在任何 copyout 前失败，输出保持不变；用户 wrapper 还在陷入前拒绝 expected 的非零 `reserved`。`STALE` 和 `NOT_FOUND` 是完成 self snapshot 后的语义结果；在输出区有效时仍可能复制当前可用前缀，调用者不能把这两种返回值理解为“输出未写”。

### 生命周期和配额

scope 状态序列是 ACTIVE -> CLOSING -> RETIRING -> FREE。权威 `workflow_lifecycle` ledger 固定 8 槽，key 为 `(id,generation)`；自然耗尽可以从 ACTIVE 进入 RETIRING，强制关闭先进入 CLOSING。成员数降为 0 后，reaper 清理 metadata、dependency、action history、edit lease/version、digest cache、audit、IPC route 和普通文件。文件查询没有内核结果 cache；用户 Context cache 随所属 Agent 地址空间 teardown。只有 lifecycle 成员、退休工作、STORAGE usage 与 owner-scoped I/O request/lease/lane debt 全部归零，槽才回到 FREE，并在下一次使用时递增 generation；global device debt 不属于该槽，允许跨 owner lifecycle 并由设备根 refill 偿还；generation 耗尽时拒绝复用。`vfs_scope_refs[NPROC]` 仅用于 VFS 引用/清理，不是身份 ledger。

当前固定 workflow 边界为 ACTIVE+CLOSING+RETIRING 最多 4 个、lifecycle ledger 8 槽。RETIRING 在 catalog 回收完成前继续占用这 4 个准入槽之一，不能由新 generation 提前复用。进程、线程、file object、block/inode、buffer cache 与 Agent state page 的容量由 generation-safe EXEC/STORAGE account 和 ordinary/reserved policy 控制。metadata catalog 总计 512 条：SYSTEM 固定 64 条，4 个 workflow 分区各固定 112 条；每个 workflow 的自动扫描物化视图最多占 96 条，余下 16 条保留给显式 metadata。catalog 是有界索引而不是文件系统 inode 的 backing lease，workflow inode 账户使用独立的 STORAGE policy 上限。该设计不允许 catalog 跨 scope 借用，不计算全局 union/max，也不增加 catalog resource kind 或 metadata envelope 账本。dependency 仍为每 scope 16 条、action history 8 条、edit lease 8 条、audit 128 条；action status 一次原子批次独立限制为 112 条，超出时在修改任何记录前返回可恢复的 `NO_SPACE`。lifecycle 改变、键冲突与不可确定提交分别保留可重试、`CONFLICT` 与 `INDETERMINATE` 状态。

文件系统使用 `NINODE=2048`。workflow 的持久存储保证由 mkfs 与内核共享的容量策略根据“完成镜像后”的空闲量计算，并把最多四个 workflow 的总目标限制在扣除 SYSTEM 后空闲量的四分之三。每个 admitted/future scope 的硬下限为 320 个 inode 和 512 个 block，SYSTEM 的硬下限为 8 个 inode 和 512 个 block；当前 `platform_agentos` 镜像写入 superblock 的每 scope 保证为 342 个 inode、1195 个 block，SYSTEM 保留为 64 个 inode、512 个 block。workflow inode 账户直接采用该 STORAGE policy 的 domain limit，不再被 112 条 metadata catalog 容量钳制；112 只描述索引记录容量。mkfs 无法同时兑现硬下限时直接拒绝生成镜像，并把 policy version、scope 数、PUBLIC principal、实际保证、系统保留量和 checksum 作为容量契约写入 superblock。挂载从 qmap 和 dinode 的持久 owner 重建 PUBLIC block/inode 用量，再按契约回收旧 boot lease、验证四份固定保证并重建 SYSTEM 剩余信用；workflow admission 还会原子检查当时的实际余量。PUBLIC 分配必须留下所有尚未消费的 workflow 存储保证和 SYSTEM 剩余量；SYSTEM 维护分配可以消耗自己的信用，但不能侵占 workflow 的最低保证。缺少稳定 PUBLIC principal 的旧格式镜像会被版本检查明确拒绝，不会用空账本继续运行。

PUBLIC 第一次修改 SYSTEM 赞助的可变文件时不会逐块反复申请配额。内核在固定 `MAXFILE + 1` 工作区收集直接块、间接索引块和间接数据块，排序后按 `QBLOCK` 分组，每个 qmap block 在一轮中只读写一次。可睡眠 claim gate 串行化同类接管；预检可中断且只在释放 buffer 后进入 quiescent checkpoint，随后一次性预留全部 PUBLIC 配额并开始 qmap-first 前向提交。此后 cleanup checkpoint 可以等待但不能因线程退出回滚；所有 qmap owner 转换完成后才发布 inode owner。挂载发现“SYSTEM inode + 部分 PUBLIC qmap”时沿同一顺序继续完成，而不是猜测回滚。

## 角色与 capability

`agent_create()` 是创建 `AGENT_ROLE_SENTINEL` 的兼容入口，`agent_create_role(role)` 用于创建指定角色 Agent。两者进入同一个内核授权检查，只有内核私有的 `role_grant_mask` 包含目标角色时才会分配进程和 Context。创建授权不通过 `agent_info()` 暴露，避免把安全凭证并入未版本化的查询 ABI：

| 调用者 | 允许行为 |
| --- | --- |
| 内核装载且清单标记为 bootstrap 的可信 init | 按清单允许的角色获得 bootstrap role grant，可建立根 orchestrator |
| orchestrator Agent | 按角色策略获得全部 role grant，可显式委派合法角色 |
| 普通 `fork` 子进程及其后续 `exec` 映像 | 不继承 bootstrap 或 Agent 的 role grant；普通 `exec` 即使执行清单中的 bootstrap 映像也不会重新获得启动授权 |
| sentinel、investigator、recovery、artifact | role grant 为空，不能继续创建 Agent |
| 未获目标 role grant 的调用者 | 返回 `AGENT_STATUS_DENIED`，且不分配进程、内存或文件资源 |

当前角色能力如下：

| 角色 | capability |
| --- | --- |
| `AGENT_ROLE_SENTINEL` | `META_READ`、`PROCESS_READ`、`MESSAGE_SEND`、`WATCH`、`AUDIT_WRITE` |
| `AGENT_ROLE_INVESTIGATOR` | `META_READ`、`CONTENT_READ`、`MESSAGE_SEND`、`WATCH`、`AUDIT_WRITE` |
| `AGENT_ROLE_RECOVERY` | `META_READ`、`CONTENT_READ`、`MESSAGE_SEND`、`WATCH`、`ACTION_WRITE`、`ARTIFACT_WRITE`、`AUDIT_WRITE` |
| `AGENT_ROLE_ARTIFACT` | `META_READ`、`CONTENT_READ`、`MESSAGE_SEND`、`WATCH`、`ARTIFACT_WRITE`、`AUDIT_WRITE` |
| `AGENT_ROLE_ORCHESTRATOR` | 全部能力，包括 `META_WRITE`、`ORCHESTRATE`、`LLM_RELAY`、`WAIT_CANCEL` 和 `ROUTE_MANAGE` |

Agent 创建授权与业务操作授权彼此独立。前者只使用内核 `struct proc.agent_role_grant_mask`；后者使用 `agent_role`、`agent_capability_mask` 和当前可信 workflow scope/对象 owner 的交集。构建期可信清单集中定义源程序、安装映像名、不可变/启动标志、允许角色和文件系统安全配置；mkfs 把策略写入 inode，loader 按 inode 策略和映像布局建立进程凭据。bootstrap grant 只在内核初次装载带 `BOOTSTRAP` 标志的可信映像时产生，不根据 PID、父 PID 或用户态字符串推断；普通 fork 不复制 grant，普通进程成功 exec 会清空 grant，也不会因为执行同名或带 bootstrap 标志的映像而恢复。Agent 执行新映像时还必须满足该可信 inode 的角色掩码。`agent_op.arg0` 中传入的 role 只保留为旧兼容参数，不参与 `capability_check`、`action_commit`、`artifact_update`、`llm_response` 等敏感工具授权。`rerun_stage` 和 `write_report` 仍可调用，但它们只是面向旧示例的兼容别名；运行记录、事件 action 和重复请求判断都归入本 scope 的 `action_commit` 或 `artifact_update`。

### 可信 Agent 映像、workflow worker 与文件系统凭据

`user/include/exec_policy_manifest.h` 中的 `EXEC_POLICY_ENTRIES` 是 Agent 可执行信任策略的集中注册表。镜像构建器和用户态 launcher 使用同一份条目，避免安装名、允许角色和安全配置漂移。要新增可保留 Agent 角色的映像，需要在清单中声明安装映像、可信/不可变/域安全标志、允许角色掩码和 VFS profile；仅复制相同字节到普通文件不会复制 inode 信任属性。可信 Agent 映像拒绝普通 `write`、`O_TRUNC`、覆盖创建和 `unlink`。

非 Agent worker 使用另一条受控链路：mkfs 为具有有效 W^X 布局的程序生成确定性的短别名，设置 `IMMUTABLE | DOMAIN_SAFE` 和 workflow VFS profile，但不要求 `TRUSTED` 或 Agent role mask。`agent_worker_create()` 的精确 inode 委派才是 worker 获得文件能力的权限来源。

Agent 业务 capability 与 VFS effective capability 是两套独立凭据，但两者都绑定同一个 kernel-issued scope。前者控制 Agent syscall 和工具，后者控制普通文件数据操作；任一 capability 都不能脱离 scope 单独授权。VFS 当前使用以下策略：

| inode 策略 | 访问规则 |
| --- | --- |
| `PUBLIC` | 数据操作由 public 域普通进程访问；workflow 域不会借 Agent 身份绕回 public 文件 |
| `WORKFLOW` | 数据操作只允许与 inode `scope_id` 精确相等的 workflow scope；读取要求 `CONTENT_READ`，创建、写入、截断和删除要求 `ARTIFACT_WRITE` |
| `KERNEL_PRIVATE` | 只允许内核凭据访问，例如 metadata 双 bank 后端 |
| `ROOT` | 两个域都可查找、读取和执行目录入口；修改要求 public 域或 workflow 域的 `ARTIFACT_WRITE` |

普通 `fork()` 不继承 workflow effective capability，也不保留动态 scope，因此跨 scope 的 inode 描述符直接撤销。同 scope 继承的 inode 描述符也不是授权票据：每次 `read`、`write` 等操作都会用当前进程的 capability 和 scope 重新检查 inode。每次新 Agent/worker/workflow 主体，以及从 workflow 或可信 bootstrap 动态 scope 降权的普通子主体，都默认不继承 pipe；只有调用线程通过 `agent_scope_delegate_fd()` 明确标记的一次性端点例外。同一 PUBLIC 安全主体内的普通 `fork()` 仍保留 POSIX pipe 继承，单纯更换资源记账域不改变这一语义。

`exec` 是命名空间隔离中的显式例外。内核可在调用者所在域未命中后查找另一域的布局有效映像，但仅执行该映像不会安装 workflow 凭据：普通进程仍保持 public/无能力状态。只有 `agent_worker_create()` 预先绑定的精确委派可以为非 Agent worker 安装能力；已有 Agent 则还要通过可信 role-image 校验才能保留身份和能力。

统一映像安装入口要求调用方显式选择 `PROC_IMAGE_INSTALL_BOOTSTRAP` 或 `PROC_IMAGE_INSTALL_LIVE_EXEC`，不会再从 PID、线程字段或调用位置猜测模式。不可逆凭据发布前，内核在关中断临界区调用 `proc_image_install_state_valid_locked()`：bootstrap 目标的主线程必须尚未发布，所有线程槽均为 `T_UNUSED`、`tid=-1`、generation 0 且不在运行队列；live exec 的主线程必须就是当前 `RUNNING` 的 t0、generation 非零且不在运行队列，其他槽只能是 `T_UNUSED`/`EXITED` 且均不在队列。两种模式都逐槽核对 `thread.process` 所属进程。状态不符时凭据和 VM 所有权都不移动；提交凭据后，进程级 IPC/wait 临时状态会在同步对象 reset 和 VM 交换之前统一清理。

`agent_worker_create(image, requested_caps)` 只允许具备 `ORCHESTRATE` 且处于 active workflow scope 的 Agent 调用。`requested_caps` 必须非零，只能包含 `CONTENT_READ` / `ARTIFACT_WRITE`，同时必须是调用者业务能力、调用者 VFS 能力和目标映像 profile 上限的子集。成功时返回与 `fork()` 相同的父子返回值，但子进程仍是非 Agent、没有 Agent Context；授权先以 pending 状态绑定到目标 inode 的 `dev + inum + incarnation` 和父 scope，子进程只有随后成功 `exec()` 完全相同的 immutable、domain-safe SYSTEM worker 映像才取得父 workflow 的凭据。执行其他映像会清除 pending 授权。

`RECOVER_STAGE` 和 `REPORT_WRITE` 在头文件中保留为旧程序兼容别名，分别等价于 `ACTION_WRITE` 和 `ARTIFACT_WRITE`。新代码和文档应优先使用通用能力名。

Agent-only 直接 syscall 的权限要求：

| syscall | 普通进程 | Agent capability 要求 |
| --- | --- | --- |
| `agent_wake` | 返回 `-1` | `MESSAGE_SEND` 或 `ORCHESTRATE` |
| `agent_route_config` | 返回 `-1` | 接收方可用 `WATCH` 接受来源；非接收方调用需要 `ROUTE_MANAGE`，source/target 均为调用者自身或直接受控 Agent，并且三者属于同一 active workflow scope |
| `agent_wait_cancel` | 返回 `-1` | `WAIT_CANCEL`，且目标由调用者直接创建并受其控制 |
| `agent_file_meta_init` | 返回 `-1` | `META_WRITE` |
| `agent_file_meta_set` | 返回 `-1` | `META_WRITE` |
| `agent_file_query` | 返回 `-1` | `META_READ` |
| `agent_file_edit_begin` | 返回 `-1` | `CONTENT_READ`、`ARTIFACT_WRITE`、`META_WRITE` 或 `ORCHESTRATE` 之一 |
| `agent_file_edit_commit` | 返回 `-1` | 租约持有者；orchestrator 可释放卡住的租约 |
| `agent_file_edit_abort` | 返回 `-1` | 租约持有者；orchestrator 可释放卡住的租约 |
| `agent_file_edit_state` | 返回 `-1` | Agent 身份 |
| `agent_span_trace_snapshot` | 返回 `-1` | `AUDIT_WRITE` |
| `agent_timeline_snapshot` | 返回 `-1` | Agent 身份；审计记录按 role/capability 自动裁剪 |
| `agent_timeline_query` | 返回 `-1` | Agent 身份；只在当前 Agent 已可见的 timeline 记录上过滤 |
| `agent_timeline_wait` | 返回 `-1` | Agent 身份；只等待当前 Agent 已可见的 timeline 记录 |
| `agent_timeline_read` | 返回 `-1` | Agent 身份；只等待并复制当前 Agent 已可见的 timeline 记录 |
| `agent_provenance_snapshot` | 返回 `-1` | Agent 身份；审计边按 role/capability 自动裁剪 |
| `agent_audit_snapshot` | 返回 `-1` | `ORCHESTRATE` |
| `agent_audit_query` | 返回 `-1` | `ORCHESTRATE` |
| `agent_ledger_snapshot` | 返回 `-1` | `ORCHESTRATE` |
| `agent_sched_config` | 返回 `-1` | `ORCHESTRATE` |
| `agent_worker_create` | 无 `ORCHESTRATE` 返回 `AGENT_STATUS_DENIED`；非法能力返回 `AGENT_STATUS_BAD_PARAM`；用户地址、映像查找、VFS 鉴权或进程分配失败返回 `-1` | `ORCHESTRATE`；并受请求能力、父凭据和目标映像 profile 的共同上限约束 |
| `agent_workflow_create` | 未获 bootstrap factory 权限返回 `AGENT_STATUS_DENIED`，admission/资源不足返回失败 | 仅可信 bootstrap、非 Agent resource-domain factory；创建全新 scope，不接受普通 Agent capability 替代 |
| `agent_scope_delegate_fd` | 非法/非 pipe fd 返回 `AGENT_STATUS_BAD_PARAM` | 可信 bootstrap factory 或 `ORCHESTRATE`；仅签发调用线程下一次安全主体创建的一次性票据 |

`agent_wake()` 是消息投递接口，只接受 `AGENT_EVENT_MESSAGE`。`AGENT_EVENT_NONE` 或超出 `AGENT_EVENT_MAX` 的类型返回 `AGENT_STATUS_BAD_PARAM`；`FILE_STATUS`、`TIMER`、`POLICY_DENIED`、`LLM_DONE` 等由内核或专用工具产生的事件返回 `AGENT_STATUS_DENIED`。例如 `LLM_DONE` 只能由具备 `LLM_RELAY` capability 的 `llm_response` 工具路径投递，不能通过 `agent_wake()` 伪造。`MESSAGE_SEND` 只表示调用者能够发起消息，不再等价于向任意 PID 投递；跨 Agent 的 `agent_wake`、`send_message`、非零 target 的 `llm_request` 和 `llm_response` 还必须命中相应事件类型的定向 IPC 路由，并且 source/target 必须属于同一 active workflow scope。`llm_request(target_pid=0, ...)` 是只记录摘要、不执行投递的模式，不需要 route。

### 定向 Agent IPC 路由

接口：

```c
int agent_route_config(int source_pid, int target_pid, uint64 event_mask,
                       int operation);
```

`event_mask` 只接受 `AGENT_IPC_EVENT_MESSAGE`、`AGENT_IPC_EVENT_LLM_DONE` 或两者的组合；`operation` 为 `AGENT_IPC_ROUTE_GRANT` 或 `AGENT_IPC_ROUTE_REVOKE`。跨 Agent 投递默认拒绝，自投递隐式允许且不占路由槽。接收方可用自己的 `WATCH` 能力显式接受某个仍存活的 source；具备 `ROUTE_MANAGE` 的控制者也可为自己或直接创建的 Agent 配置路由，但调用者、source 和 target 必须处于同一 active workflow scope，source 和 target 还必须处于该控制边界内。因此 target 自主同意也不能建立跨 scope 路由，知道 PID、共享角色或持有相同 capability 均不足以通信。

内核把 PID 解析、存活检查、控制关系检查和路由更新放在同一临界区中。路由表保存 source 的内核私有 64 位 `agent_control_id`，不把 PID、PCB 槽、父指针、角色或资源域当作授权身份。source 退出时，其所有入站授权会从各目标回收；target 退出时清空自己的路由表，所以 PID 或 PCB 槽复用不会继承旧通道。重复 grant/revoke 是幂等操作；revoke 或 source 退出只阻止后续入队，之前已经成功入队的事件仍按 FIFO 消费。

消息投递先完成路由授权，再匹配目标 watch 并占用队列资源。未授权返回 `AGENT_STATUS_DENIED`；目标不存在、正在退出或 watch/filter 不匹配返回 `AGENT_STATUS_NOT_FOUND`；路由表或队列配额耗尽返回 `AGENT_STATUS_NO_SPACE`。兼容 mailbox 镜像在授权事件成功入队后于同一临界区更新，拒绝路径不会留下旁路副作用。

## 高性能请求结构

`struct agent_op` 是最终热路径 ABI：

| 字段 | 说明 |
| --- | --- |
| `version` | 必须为 `AGENT_CALL_VERSION` |
| `tool_id` | 工具 ID，固定走 ID 分发 |
| `request_id` | 用户态请求 ID |
| `arg0` / `arg1` | 两个数值参数槽 |
| `flags` | 预留标志位 |
| `payload` | 64 字节短文本参数 |

`struct agent_result` 是对应结果：

| 字段 | 说明 |
| --- | --- |
| `version` / `status` / `tool_id` | 版本、状态和工具 ID |
| `request_id` / `sequence` | 请求 ID 和内核分配的 Agent 调用序号 |
| `value0` / `value1` / `value2` | 工具返回的三个数值槽 |
| `result` | 64 字节短文本结果 |

`agent_run()` 一次最多执行 `AGENT_BATCH_MAX = 64` 个 op。单个 op 的工具错误写入对应 result；用户指针错误、count 非法或非 Agent 调用返回 `-1`。

## 版本化名称协议请求结构

V1 `struct agent_request` / `struct agent_response` 是原始“工具名称 + 固定参数槽”入口，syscall 503 保持其 192/184 字节布局不变。性能主路径仍使用更紧凑的 `agent_op` / `agent_result`。V1 请求的关键字段：

| 字段 | 说明 |
| --- | --- |
| `version` | 必须为 `AGENT_CALL_VERSION` |
| `tool_id` | 工具 ID |
| `tool_name` | 工具名称 |
| `request_id` | 用户态请求 ID，内核原样带回 |
| `arg0_key` / `arg1_key` | 参数键名 |
| `arg0_type` / `arg1_type` | 参数类型 |
| `arg0` / `arg1` | 数值参数 |
| `payload_key` | 字符串参数键名 |
| `payload_type` | 字符串参数类型 |
| `payload` | 短字符串 payload |

当 `tool_id` 和 `tool_name` 同时提供时，内核先用 ID 定位工具，再校验名称是否匹配。不匹配时 `agent_call()` 的传输返回值为 0，`response.status` 为 `AGENT_STATUS_BAD_REQUEST`，结果文本为 `tool_mismatch`，不会执行工具。只提供 `tool_name` 时，内核按工具表名称解析工具，并继续校验参数键和类型。V1 保留三个固定参数槽，只用于兼容已有程序。

V2 使用 syscall 547 `sys_tool_call()`。`struct agent_request_v2` 的前两个字段固定为 `version` 和 `size`，随后是 `tool_id`、`param_count`、`request_id`、`flags`、用户态参数数组地址 `params` 和 `tool_name`。当前要求 `version=AGENT_CALL_VERSION_V2`、`size=sizeof(struct agent_request_v2)`、`flags=0`，参数数目不超过 `AGENT_TOOL_PARAM_MAX=8`；`param_count=0` 当且仅当 `params=0`。`struct agent_response_v2` 同样以 `version + size` 开头，并返回 status、解析后的 tool id/name、request id、sequence、三个数值槽和结果文本。请求与响应地址有效时，协议或工具语义错误通过 `response.status` 返回，syscall 传输返回值仍为 0；只有用户地址检查或复制失败直接返回 -1。协议校验失败保留 validator 诊断，工具执行失败保留工具返回的稳定错误文本；调用者仍应以 `response.status` 判断错误类别。

参数数组由 `struct agent_param_v2` 组成。每项独立携带 `version`、`size`、`type`、`value_size`、`key` 和 tagged value；当前类型是 `AGENT_PARAM_UINT64` 或 `AGENT_PARAM_STRING`。参数按 key 匹配，与数组顺序无关。内核拒绝未知 key、同一 key 重复、类型不匹配、缺失必选参数、非终止字符串、错误 value size、错误结构 size 和错误 version，不会静默忽略或截断为另一种含义。

syscall 548 `sys_tool_list()` 返回 `struct agent_tool_desc_v2`。用户态 wrapper 隐式传入当前 descriptor size 和 V2 version；内核要求精确匹配并返回工具总数。每个 descriptor 同样带 `version + size` 和 `param_count`。将来扩展协议时应定义新 version 或兼容的 sized-prefix 规则，不能复用旧 version 改变既有字段含义。V1 syscall 503/504 和 V2 syscall 547/548 的 decoder、param count 和可见 schema 都由同一 CSR 参数规则派生：每个工具以 offset 指向紧凑规则流，键名通过注册表偏移引用，type/target/required 压入一个字节。没有参数的工具不再占固定空槽，初始化仍验证 offset 单调、键和目标唯一、类型匹配及容量边界。

所有参数键由共享 `agent_tool_abi.h` 的 `AGENT_PARAM_KEY_REGISTRY` 集中登记。`AGENT_PARAM_KEY_SIZE=16` 包含结尾 NUL，所以最多容纳 15 个可见 ASCII 字符；每个注册字面量都有 `_Static_assert`，typed rule 只能引用登记符号。Host UAPI checker 还独立拒绝空键、非 ASCII、重复、超长、未登记引用和未使用旧键，强制编译期容量断言继续接入注册表，并复核工具 ID/name、rule target/type、参数数量与生成 schema 容量。`agenttoolabi_ucore: key_capacity=1 llm_response_v1_v2=1 buffer_sentinel=1` 动态覆盖 15 字符容量边界、16 字节无 NUL 的 V1/V2 拒绝、`reply_summary` 在两版 `llm_response` 中的一致解码，以及内核 copyout 不越过用户响应缓冲区。

## 错误码

| 错误码 | 值 | 说明 |
| --- | ---: | --- |
| `AGENT_STATUS_OK` | 0 | 成功 |
| `AGENT_STATUS_BAD_REQUEST` | -1 | 版本错误或请求结构不一致 |
| `AGENT_STATUS_UNKNOWN_TOOL` | -2 | 工具不存在 |
| `AGENT_STATUS_NOT_AGENT` | -3 | 普通进程调用 Agent-only 接口 |
| `AGENT_STATUS_BAD_PARAM` | -4 | 参数键、类型或必要参数错误 |
| `AGENT_STATUS_NOT_FOUND` | -5 | 文件、Agent 或历史节点不存在；直接事件投递的目标 watch 不匹配，或 lifecycle compare 时调用者没有有效 lifecycle，也使用该状态 |
| `AGENT_STATUS_NO_SPACE` | -6 | Context、IPC route 表、事件总量/source/class/external 配额或布局空间不可用 |
| `AGENT_STATUS_TIMEOUT` | -7 | `agent_wait()` 等待超时 |
| `AGENT_STATUS_DENIED` | -8 | capability 或角色权限拒绝 |
| `AGENT_STATUS_DUPLICATE` | -9 | 重复幂等动作被识别 |
| `AGENT_STATUS_CANCELLED` | -10 | `agent_wait()` 被受权 Agent 取消 |
| `AGENT_STATUS_CONFLICT` | -11 | 文件编辑租约已被其他 Agent 持有 |
| `AGENT_STATUS_STALE` | -12 | 提交时给出的期望版本已经不是当前租约基准版本，或 syscall 546 的 expected lifecycle key 与调用者当前 key 不匹配 |
| `AGENT_STATUS_BAD_VERSION` | -13 | V2 request、parameter 或 descriptor version 不受支持 |
| `AGENT_STATUS_BAD_SIZE` | -14 | sized 结构、value 长度或字符串终止边界不合法 |
| `AGENT_STATUS_BAD_TYPE` | -15 | V2 typed KV 的参数类型与工具规则不一致 |
| `AGENT_STATUS_UNKNOWN_PARAM` | -16 | V2 参数 key 不属于该工具 |
| `AGENT_STATUS_RETRY` | -17 | 操作未提交，调用者可在满足重试条件后再次请求 |
| `AGENT_STATUS_IO_ERROR` | -18 | 底层 I/O 明确失败且未形成成功提交 |
| `AGENT_STATUS_DURABILITY` | -19 | 操作不能满足所请求的持久性保证 |
| `AGENT_STATUS_INDETERMINATE` | -20 | 故障发生在不可撤销发布边界后，调用者必须先查询状态再决定是否重试 |

## 内核工具表

| ID | 名称 | 参数 | 返回 |
| ---: | --- | --- | --- |
| 1 | `echo` | `payload:string,arg0:uint64,arg1:uint64` | payload 长度、两个数值参数、payload 文本 |
| 2 | `pid_info` | `none` | 当前 pid、Agent ID、Agent 身份 |
| 3 | `ctx_stat` | `none` | Agent Context 起始地址、大小和当前调用次数 |
| 4 | `query_process` | `type:uint64` | 进程数量、Agent 数量和可运行进程数量 |
| 5 | `get_system_status` | `none` | 进程数量、Agent 数量和系统 tick |
| 6 | `read_context` | `none` | 本次调用追加后的 Context Path 记录数、head 和总调用次数 |
| 7 | `query_file` | `path:string` 或 `key=value` 属性过滤串 | 兼容路径查询；属性查询返回 hits、scanned、used_index、query plan、truncated 和首个命中文件 |
| 8 | `send_message` | `target_pid:uint64,message:string` | 沿已授权 `MESSAGE` 路由向目标 Agent 发送短消息 |
| 9 | `read_message` | `none` | 读取当前 Agent 消息 |
| 10 | `file_meta_init` | `none` | 只重载调用者 workflow scope 在 metadata 双 bank 中的已提交记录，保留其他 scope 的内存状态；重建索引并启用根目录扫描 |
| 11 | `read_file_summary` | `selector:string` | 按物理名、逻辑路径或对象 label 返回摘要 |
| 12 | `dependency_query` | `label:string` 或 `label/namespace/run_id` selector | 返回用户态注册的对象依赖影响范围，结果中的 `value2` 是依赖记录代数 |
| 13 | `capability_check` | `role:uint64,action:string` | `role` 是兼容参数，不参与授权；按当前进程真实 capability 检查动作并返回真实 role 和 capability mask |
| 14 | `rerun_stage` | `role?:uint64,stage:string` | `role` 是可选兼容参数，不参与授权；内部调用通用 `action_commit` 状态更新路径 |
| 15 | `write_report` | `role?:uint64,payload:string` | `role` 是可选兼容参数，不参与授权；内部调用通用 `artifact_update` 状态更新路径 |
| 16 | `agent_watch` | `event_type:uint64,filter:string` | 注册 Agent Loop watch |
| 17 | `agent_wait` | `timeout:uint64` | syscall-only 可发现项；`agent_run()` 调用返回 `AGENT_STATUS_BAD_PARAM` |
| 18 | `agent_heartbeat` | `interval:uint64` | 与 syscall 共用校验器设置心跳；范围为 0 至 `AGENT_HEARTBEAT_MAX_TICKS`，0 表示停止 |
| 19 | `context_push` | `record` | 手动 Context 节点使用的内部工具 ID |
| 20 | `read_file_digest` | `selector:string` | 读取真实文件的短预览、参与计算字节数和 FNV-1a 内容指纹 |
| 21 | `action_commit` | `role?:uint64,selector:string` | `role` 是可选兼容参数，不参与授权；按通用对象 selector 幂等提交 Agent 动作，可根据依赖标签刷新后续对象 |
| 22 | `artifact_update` | `role?:uint64,selector:string` | `role` 是可选兼容参数，不参与授权；按通用对象 selector 更新工件、报告、记忆或结果对象状态 |
| 23 | `llm_request` | `target_pid:uint64,prompt_summary:string` | 记录 LLM 请求摘要；target 非零时沿 `MESSAGE` 路由投递，target 为零时只记录 |
| 24 | `llm_response` | `target_pid:uint64,reply_summary:string` | 由具备 `LLM_RELAY` 的 Agent 沿 `LLM_DONE` 路由投递结果，唤醒请求方 |
| 25 | `dependency_update` | `selector:string` | 由具备元数据写权限的 Agent 注册或更新通用对象依赖 |

工具描述中 `flags` 表示调用方式：

| flag | 含义 |
| --- | --- |
| `AGENT_TOOL_F_CALLABLE` | 可通过 `agent_run()` 执行 |
| `AGENT_TOOL_F_SYSCALL_ONLY` | 只作为工具表可发现项，必须通过对应 syscall 执行 |

## 任务四文件查询 ABI

`struct agent_file_meta` 表示一条 Agent 文件对象元数据。字段名保留早期科研示例兼容形式，但语义按通用对象模型使用：`project` 可作为 namespace，`logical_path` 可作为 object_id，`stage` 可作为 label，`kind` 可作为 type，`status` 可作为 state。字段包括：

- `physical_name`
- `logical_path`
- `project`
- `workflow`
- `run_id`
- `stage`
- `kind`
- `status`
- `summary`
- `dependency_mask`
- `updated_tick`
- `flags`
- `dev`
- `inum`
- `incarnation`
- `size`
- `fs_generation`
- `update_mask`

文件元数据授权键先包含当前 kernel-issued workflow scope，再使用真实文件的 `dev + inum + incarnation` 标识该 scope 内的对象生命期。`incarnation` 在 inode 槽重新分配时递增，使删除后复用同一 inode 号的新文件不会继承旧 metadata、租约或摘要缓存；另一 scope 即使创建相同 `physical_name`、`fid`、namespace、run 或 label，也只会命中自己的记录。`physical_name` 必须是 uCore 根目录实际的 `DIRSIZ = 14` 字节 canonical dirent key，复杂逻辑路径保存在 `logical_path` 等 Agent 属性字段中。文件 syscall 的长名与同前 14 字节名称为同一 legacy alias；内核在任何查找、发布、删除或回滚前统一 canonicalize，但授权始终依据命中 inode 的 label/scope，不依据 raw alias。根目录私有文件 `.agentmeta` 和 `.agentmeta1` 组成双 bank 版本化快照：mkfs 先按共享纯磁盘 ABI 安装两份 canonical v7 generation-1 空 bank，完整预分配并标为 `KERNEL_PRIVATE/SYSTEM`；运行时从不把双缺失或双未提交解释为初始 authority。提交时目标 bank 先写入无效 header，再按 1 KiB segment 与已验证的内存 shadow 比较，只写入并逐段回读变化的 `PERSIST` payload；整体摘要一致后才发布有效 header，header 回读一致后才切换 active generation。新的 primary 完整验证后，状态机才用同一不可变快照更新旧 bank 作为 mirror。bank 不为缩短快照同步 truncate，loader 只按 header 声明的精确逻辑长度校验 payload。不可逆 COW 边界前的失败可用 catalog undo token 恢复内存记录和 inode sidecar；边界后的失败返回 `AGENT_STATUS_INDETERMINATE`，若 exact post-state、容量或唯一键复核失败则运行时 fail closed，不能报告为已回滚。`agent_file_meta_init()` 强制读取两个 bank 并选择 generation 最高的完整快照，但只替换调用者 scope 的已提交记录；其他 scope 保持不变。任一有效 v5/v7 bank 可加载并修复 peer，稳定状态无有效 bank 则 fail closed。普通文件 syscall 不能直接读取、创建、修改或删除 bank，Agent 子系统内部 helper 使用内核凭据负责持久化和重新加载。

按 `physical_name` 设置 metadata 时，缺失的 workflow 文件可以由受权调用者自动创建，但创建结果是三态而不是布尔值：`existing=0` 表示复用已存在 inode，`created=1` 表示本次已经发布新目录项，`FS_CREATE_INDETERMINATE` 表示目录或 inode 发布可能已经发生、不能猜测为“不存在”。对 `created`，catalog undo 保存精确创建回执 `(path, scope, dev, inum, incarnation)`；后续仍处于可逆边界内的绑定或事务失败，只允许 `fs_rollback_created_workflow()` 删除仍与该回执完全相同、仍属对应 workflow 且尚未被 metadata/编辑状态接管的 inode。existing 路径绝不由这条回滚删除；名称已指向替换 inode、身份不一致、清理写回失败或 create 本身不确定时保留现场，向上返回 `AGENT_STATUS_INDETERMINATE` 并把 metadata 运行时置为 fail closed，不能伪造成功回滚。

可信 bank 还是用户进程发布的启动依赖。`main()` 在 `fsinit()` 和 `timer_init()` 之后调用 `agent_storage_init()`，在启动继续前完成可信加载判定：成功时选择、校验、绑定并按需恢复 bank，失败时设置 fail-closed；随后才执行 `bio_policy_start()` 和 `load_init_app()`。单个 bank 损坏时选择另一份可验证副本并标记恢复；不存在可验证有效 bank 或选择失败时设置 `agent_meta_store_failed_closed`，系统继续启动，但后续 metadata load/persist/init API 返回失败，不能用空表冒充可信状态。这个 fail-closed 状态不阻断 scope 的 VFS 生命周期回收：`agent_scope_reclaim()` 仍清理 scope 的依赖、动作、缓存、审计、租约和真实 VFS-labelled 文件，并在成功后退休 scope 身份；它不会声称已恢复损坏 bank 中不可读的 metadata。

内存 metadata、索引、依赖表、inode sidecar 与双 bank 提交属于同一个可睡眠、可重入的内核事务域。进程 syscall 在竞争时原子领取单调 ticket，并在对象私有等待队列中不可中断地等待 serving ticket；最外层 owner 清空后只唤醒队首。退出请求不能遗弃 ticket：线程先取得并传递事务门，再由 syscall 边界退出。真实 VFS callback、scope retirement 等进程态外部路径只使用不插队的 try-lock。scheduler 可在事务门恰好空闲时取得一个硬有界维护轮次；若 serving waiter 已由前任唤醒，该保留轮次解锁时抑制第二次 wake-one，保持 ticket 与睡眠队列一一对应。进程态可扩展扫描每 128 条记录计入 kernel-work 预算；scheduler 只执行固定 16 目录项扫描或单个持久化步骤。依赖表只存放每 scope 最多 16 条显式用户边；兼容 `dependency_mask` 始终留在文件记录中，由依赖查询和 action 在固定表的线性遍历中按需解释，不再建立全局派生依赖图。

所有可能替换物理 COW job 的同步 set/delete/init/reload 还必须进入单独的 FIFO submit lane。调用者先领取单调 ticket，只有 serving ticket、persist idle、sync owner 和 reload owner 条件同时满足时才能进入；若条件不满足，内核从失败检查开始一直保持中断关闭，依次释放 metadata 事务门、把当前线程插入 submit condition queue，再恢复中断。完成检查到入队之间不存在可丢失 wake 的窗口。ticket 不允许在线程退出时放弃，否则会卡住全部后继；退出请求在该有界 COW lane 完成后由 syscall 边界处理。持久化跨预算等待时 immutable job 保持同一 `job_id`，同步调用者临时释放全局事务门，维护线程只能推进该 job，不能让后来提交者替换它。

修改 catalog 的同步操作还持有 owner-token mutation fence。fence 跨持久化 checkpoint 保持，foreign writer 在改写前得到冲突/重试；undo token 绑定 fence、slot、generation 和完整 post-record，恢复前再次执行 exact binding、容量及唯一键检查。读取不会被 fence 阻塞，因此调用者不得把它解释为全局 opacity；只有 commit 完成后才可把返回值解释为确定持久结果。

catalog 的磁盘与 live 准入边界分开。full boot 先按不可变 lifecycle key 淘汰已失效的动态 scope，再按 v7 表示、SYSTEM 64、ordinary 448、每 scope 112、lifecycle 和唯一键校验；每 scope 自动扫描最多 96 是新增或 explicit-to-AUTOSCAN 的 live 软边界，不参与同版本快照损坏判定。因此 97 至 112 条旧 AUTOSCAN 会完整装载，第 113 条仍 fail closed；不会静默删表或建立迁移状态。处于 RETIRING 的合法持久快照仍在原分区装载和回收；运行期 scoped reload 把目标 scope 的不可变 `(lifecycle_id,generation)` 绑定到 prepare plan，并在 prepare/apply 边界重新验证同一身份。超额 scope 的 AUTOSCAN-to-AUTOSCAN edit 与 AUTOSCAN-to-explicit 降额有效，只有降到 95 条后才可重新增长。不改变类别的 edit 仍重验固定分区和唯一键；由精确 receipt 约束的 rollback 只重验硬边界、唯一键与 exact post-state，不会被新软策略阻断。身份改变返回可重试结果，容量、键冲突和不可逆持久化边界分别保持 `NO_SPACE`、`CONFLICT` 和 `INDETERMINATE`。

catalog 或 96 条 AUTOSCAN 物化上限耗尽时，只要独立 STORAGE inode/block 配额仍允许，VFS 文件仍会创建并保持原 workflow 标签与逐操作 scope 隔离；统一 setter 将 inode sidecar 持久记录为 `agent_meta_slot=-1`、flags 0 和当前 version，后续 write/sync/truncate/delete 不会为同一容量状态反复请求全目录扫描。扫描已标记该 scope 饱和且 slot 实际清除时，内核安排 scoped urgent full restart 并从目录 offset 0 重新物化；未释放容量的 metadata-gate busy delete 和生命周期等其他变化只登记遵守 cooldown 的普通协调扫描。

普通 workflow 文件的 create/write/truncate/delete callback 不再同步重写全局 metadata bank。所有 `agent_meta_slot/flags/version` 更新统一经 `agent_file_state_set_index()` 校验、`iupdate()` 并在失败时恢复旧值；write/sync/truncate/delete 统一经 `agent_fs_apply_inode_event()`，create 只在 VFS 创建成功后进入目录协调，metadata 容量不足不会回滚已经发布的目录项。write/truncate 先按 inode incarnation 把已提交的 size、更新时间和文件代数发布到 sidecar，create/delete 直接完成对应内存记录变化；只有带 `PERSIST` 的记录才在所属 workflow scope 的 `dirty_generation` 上登记写回，volatile 记录只更新内存和 sidecar。重复持久变化进入固定一秒、不会因新请求延长的合并窗口。scheduler 只在窗口到期且事务门空闲时推进一个后台 checkpoint step；诱发写回的 dirty scope 按轮转选择稳定 owner，整个 job 使用该 owner 的硬 `BACKGROUND` I/O 预算。每个维护轮次至多推进一个 invalidate/write/publish/verify/commit 状态机步骤，I/O debt 通过预算安全点延后续步。checkpoint 尝试成功或失败后的 not-before deadline 都是一个固定合并窗口，不再按 checkpoint 执行耗时放大；其实际块设备占用由 `BACKGROUND` token budget 限制。扫描请求不能饿死已经到期的 checkpoint。一次 checkpoint 会合并所有当时已捕获的 scope，但只把写入期间没有继续变化的 scope 推进到 `durable_generation`，失败则保留脏代数等待固定窗口后的预算接纳。callback 竞争失败时，已有 sidecar 发布的普通写入无需扩大成全目录扫描；确需恢复绑定时才登记协调扫描。显式 `PERSIST` set/delete、reload 和 scope retirement 同步进入 FIFO submit lane 并建立不可替换的持久化任务；这保证有序接纳，不保证调用返回时 primary 已完成回读验证。`agent_file_meta_init()` 只在调用者自己的 scope 有未提交变化时建立相同任务。按真实路径设置 metadata 时只读探测单个 inode 是否应继承自动持久属性，不以全局扫描作为正常提交前置条件，也不会在请求校验失败前修改 metadata。

metadata 可见代数按 workflow scope 维护；某域的普通变化只推进该域代数，SYSTEM 对象变化才影响所有已存在 scope。它用于标识一次查询所观察的对象版本，不代表内核保存查询结果。这样低权限 Agent 的微小写入既不能逐次触发全 bank I/O，也不能借另一 workflow 的脏状态迫使其同步提交；每次查询仍通过 sidecar 和当前 catalog/index 实际读取本域已经提交到文件系统的数据。

字符串 selector 支持两组字段名：兼容字段 `project/run_id/stage/kind/status`，以及通用字段 `namespace/object_id/label/type/state`。内核按这些字段执行同一套查询、状态更新和依赖查询。科研平台中的设定的模拟流程数据由用户态 orchestrator 写入；内核不会预置项目名、run id 或固定阶段顺序。该流程的用户态环节包括数据准备、比对处理、结果分析、报告生成和归档交付。

对象依赖关系不再由内核固定解释某几个阶段名称。用户态可以通过 `dependency_update` 显式注册 `source/target/namespace/run_id/relation/summary` 形式的通用依赖记录，也可以继续通过 `agent_file_meta_set()` 写入对象 label 和 `dependency_mask` 作为紧凑兼容输入。每条显式记录包含源对象 label、目标对象 label、关系、namespace、run_id 和摘要；`dependency_query` 可用 `label=...;namespace=...;run_id=...` 缩小查询范围。旧的 `dependency_mask` 不复制进全局依赖表；没有匹配显式边时，依赖查询和 action 直接在同 scope、namespace、workflow 和 run 的文件记录上解释位图。这既保留兼容 ABI，也避免文件拓扑变化触发全局派生重建。

`update_mask` 用于精确更新字段，也允许清空字段。例如只清空 status 时传入 `AGENT_FILE_META_UPDATE_STATUS` 并让 `status` 为空字符串。

`flags` 支持：

| flag | 含义 |
| --- | --- |
| `AGENT_FILE_META_F_DELETE` | 删除所有非空 selector 共同指向的元数据；fid、physical/logical path 和完整 inode identity 必须收敛到同一记录 |
| `AGENT_FILE_META_F_PERSIST` | 记录纳入持久快照；显式 set/delete 同步提交，普通 VFS 自动变化按 scope 合并后台写回 |
| `AGENT_FILE_META_F_AUTOSCAN` | 由根目录自动扫描维护的元数据 |

`dev + inum + incarnation` 是不可变身份 guard，不是可写更新值，三项必须同时为零或同时非零。catalog 的单一 resolver 在当前 scope 一次遍历中同时聚合 fid、`physical_name`、`logical_path` 和完整 inode identity 的命中位；多个非空 selector 指向不同记录时返回 `AGENT_STATUS_CONFLICT`，隐藏的 PENDING 记录使普通写入口返回 `AGENT_STATUS_RETRY`，没有 selector 命中时返回 `AGENT_STATUS_NOT_FOUND`。失败请求不会先改写或删除其他对象。

根目录自动扫描由 Agent 文件元数据服务启用。timer tick 安排周期扫描；文件系统 hook 能局部更新时直接发布内存记录或 inode sidecar 并登记分域写回，只有绑定缺失等无法局部协调的状态才追加扫描请求。扫描器优先核验 inode sidecar；需要路径回退时，用同一个有界目录项名称填写 physical/logical path，加上完整 `dev + inum + incarnation` 调用上述 resolver，不再执行独立的第二遍 catalog name scan。若路径和 identity 分裂命中，或只由 identity 命中另一条路径，本轮返回 retry 且不选择任意一方；若原路径记录的 incarnation 与当前 inode 不同，则先撤销旧记录，再为同名新对象分配新 FID。目录项先复制完整 `DIRSIZ` 字节再显式补 NUL，因此恰好 14 字节的名称可被完整解析；create hook 同样调用 `fs_dirent_canonicalize()` 后才建立自动 physical/logical 索引，raw 长名不会进入 catalog。只有长度大于 `DIRSIZ` 或命中内部 bank 保留名的显式 metadata 记录才会按 catalog 规则规范化物理键。扫描请求采用非滑动合并：首次启用可立即执行，之后不能提前已有 cooldown；扫描中到达的任意数量请求最多排队一轮。调度器空隙调用 `agent_background_maintain()`，先给到期写回一次独立机会，再按每 tick 最多 16 个目录项推进扫描。完整扫描成功或失败后都至少休息 20 tick，并按本轮耗时的四倍延长休息期；metadata 满表后的未绑定文件微写因此不能让扫描完成即重启。扫描发现的新真实文件会生成 `AUTOSCAN | PERSIST` 元数据，文件删除后对应自动元数据会被清理。当前扫描范围是 uCore 根目录短文件名，不承诺多级目录递归或全文内容索引。

`struct agent_file_query` 以空字符串表示“不限制该字段”。`flags` 支持：

| flag | 含义 |
| --- | --- |
| `AGENT_FILE_QUERY_USE_INDEX` | 在可用索引中选择候选路径 |
| `AGENT_FILE_QUERY_SCAN` | 强制扫描全部元数据记录 |

`struct agent_file_query_result` 返回：

| 字段 | 含义 |
| --- | --- |
| `total_hits` | 总命中数 |
| `returned` | 实际复制到 `hits[]` 的条数 |
| `used_index` | 本次是否使用索引路径 |
| `truncated` | 命中数超过 `hits[]` 容量时为 1 |
| `scanned_records` | 本次查询循环实际计费的 metadata 槽或索引链节点数；强制扫描会包含空槽和不可见槽 |
| `plan` | 查询计划，0 为扫描，1/2/3 分别为 status/stage/kind 索引 |
| `index_bucket` | 命中的索引桶；扫描路径为 -1 |
| `candidate_records` | 成功借用、对当前 scope 可见并实际进入谓词匹配的活记录数，恒不大于 `scanned_records` |
| `index_rebuild_records` | 索引失效时，本次冷查询为实际重建索引访问的 metadata 槽数；热索引和扫描路径为 0 |
| `reserved` | 保留为 0，供后续 ABI 扩展 |
| `query_ticks` | 查询内部 tick 差值 |
| `plan_reason` | 查询计划原因 flags，例如强制扫描、status 索引、stage 索引、kind 索引或没有可用索引键 |
| `fs_generation` | 查询时当前 workflow scope（包含其可见 SYSTEM 对象）的 metadata 可见代数 |

每条 hit 还返回 `dev`、`inum`、`incarnation`、`size` 和 `fs_generation`，用于说明查询结果来自同一代真实文件绑定和当前元数据版本。

查询计划常量：

| 常量 | 含义 |
| --- | --- |
| `AGENT_FILE_QUERY_PLAN_SCAN` | 扫描全部可用元数据槽 |
| `AGENT_FILE_QUERY_PLAN_STATUS_INDEX` | 使用 status 索引链 |
| `AGENT_FILE_QUERY_PLAN_STAGE_INDEX` | 使用 stage 索引链 |
| `AGENT_FILE_QUERY_PLAN_KIND_INDEX` | 使用 kind 索引链 |

`plan_reason` 使用位标记说明为什么选择该计划：`FORCED_SCAN` 表示调用者强制扫描；`INDEX_OFF` 表示未请求索引；`STATUS_INDEX`、`STAGE_INDEX`、`KIND_INDEX` 表示对应索引参与计划；`NO_INDEX_KEY` 表示请求了索引但查询条件没有 status、stage 或 kind。每次查询都实际执行全表扫描或索引候选遍历，不存在全局内核查询结果缓存。`AGENT_FILE_QUERY_REASON_CACHE_HIT` 的数值只为旧用户 ABI/日志解析兼容而保留，当前内核不会设置该位。需要复用查询结果的 Agent 可以把完整结构化结果和 `fs_generation` 保存到自己的 Context user cache，并在 metadata 代数变化后自行失效；该 cache 不具备内核可信性。

### 文件编辑租约 ABI

编辑版本、内容版本和已发布大小保存在 512 槽稀疏 sidecar 中。哈希键为 `dev + inum + incarnation + scope + lifecycle generation`；开放寻址探测使用空槽和 tombstone 回收槽位，inode 最终回收或 scope retirement 时清除对应项。版本表不再按磁盘 `NINODE` 常驻展开，旧 generation 也不能命中新 workflow 的同号 inode。

文件编辑租约用于处理同一 workflow scope 内两个 Agent 同时希望修改同一真实文件的情况。内核用 `scope_id + lifecycle generation + dev + inum + incarnation` 识别文件，不依赖用户态传入的逻辑路径或全局裸租约号。普通进程不能申请租约；Agent 需要具备内容读取、工件写入、元数据写入或编排能力之一，同时普通 VFS 路径仍要求当前 workflow 凭据具备相应文件能力。租约存在时，真实 `write`、`O_TRUNC` 和 `unlink` 路径会先按当前凭据重新鉴权，再检查当前进程是否是本 scope 的租约持有者；另一个 scope 不能查询、提交、终止或复用该租约。

调用方式如下：

| 接口 | 语义 |
| --- | --- |
| `agent_file_edit_begin(path, flags, ttl_ticks, state)` | 为根目录真实文件申请独占编辑租约；成功返回 0，文件不存在返回 `AGENT_STATUS_NOT_FOUND`，已有持有者返回 `AGENT_STATUS_CONFLICT` |
| `agent_file_edit_commit(lease_id, expected_version, state)` | 提交租约；`expected_version` 必须等于 begin 返回的 `base_version`，否则返回 `AGENT_STATUS_STALE` |
| `agent_file_edit_abort(lease_id)` | 释放当前进程持有的租约；如果已经写入过文件，内核仍会推进文件版本 |
| `agent_file_edit_state(path, state)` | 查询某个真实文件当前是否有租约和当前版本 |

`flags` 当前支持：

| flag | 含义 |
| --- | --- |
| `AGENT_FILE_EDIT_F_BREAK_EXPIRED` | 保留标志；过期租约当前会在新操作到来时自动释放 |
| `AGENT_FILE_EDIT_F_ORCHESTRATOR_BREAK` | orchestrator 可主动释放已有租约并重新申请，用于示例控制面处理卡住的编辑者 |

`struct agent_file_edit_state` 返回：

| 字段 | 含义 |
| --- | --- |
| `active` | 是否存在有效租约 |
| `owner_pid` / `owner_agent_id` / `owner_role` | 当前持有者信息 |
| `dirty` | 持有者是否已经写入、截断或删除文件 |
| `lease_id` | 提交或放弃时使用的租约编号 |
| `dev` / `inum` / `incarnation` | 真实文件身份；incarnation 防止 inode 号复用后把旧租约关联到新文件 |
| `base_version` | begin 时看到的版本 |
| `current_version` | 当前版本；提交成功后若发生写入会加 1 |
| `deadline_tick` | 租约自动释放 tick |
| `conflict_count` | 本租约被其他进程拒绝的次数 |
| `path` | begin/state 调用时使用的短文件名 |

该机制采用“立即拒绝 + 有限租约”的方式处理资源访问冲突：没有等待队列，因此不会形成循环等待；持有者异常退出或长时间不提交时，后续操作会释放过期租约。它不是文件内容合并器，不会自动把两个 Agent 的修改合成一份新内容；它负责阻止无序覆盖，并用版本检查告诉调用者必须重新读取、重新生成或走恢复流程。

### 文件内容摘要工具

`read_file_digest(selector:string)` 是任务四的内容级工具。它要求调用者具备 `AGENT_CAP_CONTENT_READ`，普通 metadata 查询能力不足以读取文件内容。`selector` 可以是物理文件名、逻辑路径、stage，也可以是 `project=...;run_id=...;stage=...;status=...` 这类属性过滤串；属性过滤命中多条时读取第一条命中文件。两个 metadata bank 都不会通过该工具暴露。

绑定 Agent metadata 的真实文件会进入 8 槽 digest cache。缓存 key 是真实文件 `dev + inum + incarnation + size + content_generation`，缓存 value 是文件大小、参与计算字节数、FNV-1a 指纹和短预览。文件创建、写入、截断或删除后，incarnation 或内容版本变化，旧 digest cache 条目自然失效；单纯 metadata 更新不会让同一文件内容摘要缓存失效。未绑定 Agent metadata 的普通文件不缓存，避免内核无法感知同尺寸改写时返回过期摘要。缓存命中和未命中计数通过 `agent_info.file_digest_cache_hits`、`agent_info.file_digest_cache_misses` 暴露。

返回值使用 `struct agent_result`：

| 字段 | 含义 |
| --- | --- |
| `value0` | 真实文件大小 |
| `value1` | 参与本次指纹计算的字节数，最多 `AGENT_FILE_DIGEST_MAX_BYTES = 4096` |
| `value2` | FNV-1a 64 位内容指纹 |
| `result` | 文件开头的短预览，非可打印字符会被替换为 `.` |

该工具不是全文搜索接口，也不建立内容倒排索引。它的用途是让 Agent 在拿到 metadata 命中后，能用受权工具取得轻量内容证据，例如报告页面呈现 preview、复核脚本校验 artifact 是否真的存在、或者对照不含 AgentOS 专属服务的 plain uCore 目标中用户态读取文件的成本。

### 文件查询、索引与显式依赖

`agent_file_query()` 每次都对当前 scope 的 metadata 快照执行真实扫描或 status/stage/kind 索引候选遍历，并把 `plan`、`plan_reason`、`candidate_records`、`scanned_records`、`index_bucket` 和 `fs_generation` 随结果返回。成功查询会追加一条 `AGENT_TOOL_QUERY_FILE` Context 记录；内核不保存跨调用查询结果，也不会从查询历史隐式生成另一类内核对象。

对象关系由 `dependency_update` 显式登记，并由 `dependency_query` 在调用者 scope 内按 label、project 和 run 查询。每个 scope 最多保留 16 条显式依赖；兼容 `dependency_mask` 也只在 dependency/action 路径中按需解释。跨 Agent 协作通过经过 route 授权的事件与其 Context/audit 归因完成，不把文件查询副作用暗中附加到 message 投递。需要内容级证据时，调用者在 metadata 命中后显式使用 `read_file_digest`。

## 任务五 Agent Loop ABI

Agent Loop 使用每 Agent 16 槽 FIFO 事件队列。可归因外部事件合计上限为 12；directed IPC 与 attributed system notification 各自上限为 8；同一 stable source 跨两类合计上限为 4。directed 投递达到任一 admission 边界时返回 `AGENT_STATUS_NO_SPACE`，不会覆盖旧事件。attributed 广播遇到某个目标超配额或总队列已满时只对该目标记 drop 并继续扫描，不把 `NO_SPACE` 透传给已经提交状态的源操作。只有显式 `KERNEL` origin 可以越过 external=12 的 admission 边界，继续使用总容量中保留的至少 4 个名额；它仍受 16 槽总容量约束。heartbeat 使用明确的 intrinsic delivery policy 产生 SYSTEM `AGENT_EVENT_TIMER`，不经过 watch 过滤，并按事件类别最多保留一条未消费记录。每个 Agent 最多注册 8 条 watch；相同 `event_type + filter` 会替换原 watch，`agent_unwatch()` 可删除匹配 watch 或清空全部 watch，但不会关闭或抑制 heartbeat。有限 timeout 的 `agent_wait()` 会进入睡眠，由事件入队、heartbeat 到期、deadline 到期或 wait cancel 令牌唤醒；`agent_info.wait_loop_count` 用于观察该路径没有反复轮询。

`agent_wait()` 选择事件不等于立即出队。内核在关中断窗口内为精确 FIFO 队首或 cancel token 建立单消费者 reservation，以 event id 作为 cookie；随后进入 Context commit lane，完成用户 copyout、可信 source/span 归因和 Context/audit 记录。finish 阶段重新核对 slot/head/cookie：只有全部成功才 commit 消费、退还事件类别配额并唤醒下一 waiter；lane 或 copyout 失败会 abort reservation、保留事件或 cancel，并定向唤醒等待者。静态、mutation 和 `WAIT_ATOMIC_TEST_PROFILE` 共同约束 reserve/cookie/commit/abort、逐线程 deadline/generation、谓词重检与 teardown 展开。

所有 runnable 线程先进入所属资源域的私有队列；外层 active-domain FIFO 中每个非空域至多有一个节点。每次调度只从队首域取一个线程，域仍非空时把它放回外层队尾，因此线程数量不能换取更多跨域 dispatch。纯普通域在本域按 FIFO 选择；本域存在 Agent 时才读取 Agent 状态并允许 orchestrator 配置 weight、priority 和 budget。角色权重、配置优先级、事件队列、等待状态、timeout deadline、heartbeat 到期、等待时长、虚拟运行量和预算使用量只影响该域内分数。

每个域独立维护不可配置的 Agent/score burst。当选中域内同时有 Agent 和普通线程时，连续调度 Agent 达到 `AGENT_SCHED_MAX_AGENT_BURST` 次后必须选择本域普通 FIFO 候选；连续按分值绕过队首达到同一上限后必须选择本域 FIFO 队首。域内任一线程运行后，外层仍轮转到下一个 active 域。`agentsched_ucore` 验证既有角色权重、受权配置、事件优先、原因记录和域内普通进展；`threadresource_ucore` 验证多线程 PUBLIC 域不能阻断另一资源域完成 512 次让出。

`agent_sched_snapshot(records, max)` 返回当前 Agent 最近最多 8 次被调度时的原因记录。`max=0` 时不复制记录，只返回当前可见记录数。普通进程调用返回 `-1`。

`agent_trace_snapshot(records, max)` 返回当前 Agent 的运行轨迹短记录。它会把 Context Path 中最近最多 128 条摘要记录和调度器中最近最多 8 条调度原因记录按 tick 合并，最多返回 `AGENT_TRACE_MAX_RECORDS = 136` 条。`max=0` 时不复制记录，只返回当前可见记录数。普通进程调用返回 `-1`。该接口只整理当前 Agent 自己已经拥有的 Context 和调度数据，不创建新的全局日志，也不改变事件队列。

`agent_span_trace_snapshot(records, max)` 返回当前 Agent 所在可信 span 的系统级短记录。它读取共享物理表，但只返回调用者 scope、`current_span_id` 和内核私有 `current_span_owner` 全部匹配的记录；当前 Agent 尚未进入 span 时返回 0。普通进程调用返回 `-1`；缺少 `AUDIT_WRITE` 能力的 Agent 返回 `AGENT_STATUS_DENIED`；`max=0` 返回匹配数量；`max>0` 时按 sequence 顺序复制最多 `max` 条。该接口不接受调用者传入 span id，公开 span 字段也不构成跨 scope 或跨 owner 的查询票据。

该接口按调用者 scope 的 sequence 索引单遍检查，`max=0` 的计数模式不会绕过工作计费。内核在扫描前按每 16 条候选记录换算预算，按不超过一个 `kernel_work` 量子的批次分段预付，并在让出后重计和补足增长差额。该机制不增加公共 ABI，也不公开内部扫描量。

`agent_audit_snapshot(records, max)` 返回调用者 workflow scope 的系统级 Agent 审计短记录。内核物理表固定为 `AGENT_AUDIT_MAX_RECORDS = 512`，按最多 4 个 admitted workflow 划分为每 scope 128 条保证；每个 scope 再独立划分 general/low 64 条和 protected/high 64 条。low 对每个 stable principal 最多保留 16 条；high 按每 scope 8 个保留进程份额均分为每 active principal 8 条。Context、事件入队/消费、调度和用户手动记录等遥测始终进入 low；只有工具或 syscall 成功后由内核确认的特权状态效果才进入 high，用户 flags、公开 span 或委派来的 cause 不能把遥测提升为 protected evidence。high 满时只滚动当前 principal 自己的 8 条，或回收已经退出/inactive principal 的最旧记录，绝不淘汰另一 active principal 的 protected evidence；inactive 历史仍是由 `dropped_records` 明示的有界窗口。低权限主体持续制造遥测也不能占用其他 workflow 的 128 条份额。

每个 scope 维护自己的逻辑 hash 链。物理 `sequence` 仍为系统单调序号，所以同一 scope 可见 sequence 可以因其他 scope 写入而跳号；low/high 与 per-principal 独立滚动还会使当前 128 条窗口缺少早期前驱。每条新记录的 `prev_hash` 指向本 scope 上一条逻辑记录的 hash，即使该前驱已被淘汰。因而只在两条相邻可见记录的 sequence/prev-hash 连续时逐条核验；`dropped_records = total_records - visible_records` 说明窗口外记录数量，sequence/hash 间隙不等同于链损坏。`ledger_hash` 始终是本 scope 最新逻辑记录的 hash。

每个 scope 维护 sequence 与 `(tick, sequence)` 两个 128 槽有序索引。记录覆盖统一先从两份索引 unlink 旧槽，再 publish 新槽；因此 ledger 的 `visible_records`、`oldest_sequence` 和 `latest_sequence` 都可在 O(1) 时间得到，不需要扫描物理 512 槽。需要复制或过滤的审计查询沿 sequence 索引单遍推进。

普通进程调用返回 `-1`；不具备 `ORCHESTRATE` 的 Agent 返回 `AGENT_STATUS_DENIED`；`max=0` 返回本 scope 当前可见记录数；`max>0` 时按 sequence 复制记录。该接口是内存态观测能力，不写磁盘，也不替代完整 `context_detail()`。

`agent_audit_query(filter, records, max)` 在调用者 scope 的同一组可见短记录上执行过滤查询。`filter=NULL` 表示不过滤；`filter->flags` 决定哪些字段参与匹配，可按 `start_sequence`、`span_id`、`kind`、`pid`、`source_pid`、`target_pid`、`role`、`tool_id`、`event_type` 和 `status` 过滤。filter 只能缩小 scope/owner 裁剪后的集合，不能用公开 PID 或 span 扩大可见范围。`max=0` 返回匹配数量，不复制记录；`max>0` 时复制最多 `max` 条匹配记录。权限和错误语义与 `agent_audit_snapshot()` 相同。

`agent_ledger_snapshot(summary)` 返回当前 workflow scope 的运行账本摘要。它不复制审计明细，只返回 `oldest_sequence`、`latest_sequence`、`visible_records`、`total_records`、`dropped_records`、`ledger_hash`、Context/event/sched 分类计数、保留计数槽、`timeline_total` 和 `observe_epoch`。其中 `ledger_hash` 等于本 scope 最新逻辑审计记录的 `record_hash`，而不是物理 512 槽中其他 scope 的最后记录。普通进程调用返回 `-1`，非 orchestrator Agent 返回 `AGENT_STATUS_DENIED`，空指针返回 `AGENT_STATUS_BAD_PARAM`。该接口适合状态页面或示例脚本确认当前 scope 的稀疏可见窗口仍锚定在同一条内核维护的运行事实链。

`agent_timeline_snapshot(records, max)` 返回统一 timeline 记录。该接口把当前 Agent 可见的三类短记录规范化为 `struct agent_timeline_record`：

- 当前 Agent 的 Context Path 摘要；
- 当前 Agent 最近 8 次调度原因；
- 当前 Agent 可见的审计记录：orchestrator 可见本 workflow scope 的审计，其他具备 `AUDIT_WRITE` 的 Agent 只可见同 scope 的当前可信 span。

普通进程调用返回 `-1`。`max=0` 返回当前可见记录总数，不复制记录；`max>0` 时按 tick 输出最多 `max` 条。共享物理审计表中的记录按系统 sequence 排序，导出前先按 scope/owner 裁剪，再按 tick 选择，避免多 Agent 并发记录导致页面时间线乱序。该接口不替代 `context_detail()`，也不保存完整 raw 请求/响应；它面向结果页面和研究平台运行详情。

`agent_timeline_query(filter, records, max)` 在同一组当前可见记录上执行内核侧过滤。`filter=NULL` 表示不过滤，语义等同于 `agent_timeline_snapshot()`。`max=0` 返回匹配数量，不复制记录；`max>0` 时按 tick 复制最多 `max` 条匹配记录。普通进程调用返回 `-1`。过滤只能缩小当前 Agent 已经有权看到的记录集合：普通 Agent 不能通过 filter 读取其他 scope/span 的审计记录，非 orchestrator Agent 也不能把 source mask 设为 audit 后获得整个 scope 的审计。

timeline 将 Context、Sched 和 Audit 三个有序来源按 `(tick, source, sequence)` 三路归并；Audit 来源使用当前 scope 的 `(tick, sequence)` 索引。每个来源记录至多被检查一次，需要过滤扫描的计数查询与复制查询采用相同预算规则。等待路径的匹配查询先按来源上界预付预算；若预付产生 yield，则重新读取来源上界并重新采样 epoch，只有最后一次可能的预算 yield 已结束且预留足够时才保存 `scan_epoch`。导出本身保持开中断；未命中后在关中断窗口最终比较当前 epoch 与该 `scan_epoch`，再原子发布 filter、waiting 状态和等待队列节点，因此预算安全点和 scan-to-publish 窗口都不会造成丢失唤醒。

SCHED ring 由 timeline owner 持有。调度 core 只生成采样，观测 facade 先检查当前线程的 suppression；未被抑制时，发布顺序固定为提交 SCHED ring、规范化同一记录并推进 epoch/定向唤醒、最后写入 scope audit。被抑制的采样不会只留下 ring 或 audit 的一半状态。

`AGENT_TIMELINE_FILTER_AFTER_CURSOR` 用于增量读取。调用者把已经处理过的最后一条 record 的 `tick`、`source` 和 `sequence` 填入 `after_tick`、`after_source` 和 `after_sequence`，内核只返回严格晚于该游标的记录。比较顺序与 timeline 导出顺序一致：先比较 `tick`，同一 tick 下按 `source` 顺序比较，再比较来源内部 `sequence`。它比只使用 `start_tick` 更适合页面刷新，因为同一个 tick 中可能存在多条不同来源记录。

`agent_timeline_wait(filter, timeout_ticks)` 使用同一套 timeline filter，但只返回匹配记录数，不复制记录。若当前已经有匹配记录，接口立即返回数量；否则调用线程以不可复用的 thread generation 为 key 进入进程所属的 timeline 队列。filter、deadline、scope 和 epoch 都属于本次线程调用，多个 sibling 的并发等待互不覆盖；发布路径只定向唤醒通过可见性和完整 filter 检查的 waiter，返回路径也只注销调用线程。`timeout_ticks >= 0` 表示独立的有限等待，超时返回 `AGENT_STATUS_TIMEOUT`；到期边界若 epoch 已变化会最后重扫一次，但同 scope 的持续不匹配 churn 不会继续延长等待。`timeout_ticks == -1` 表示无限等待。exit、exec 和线程槽复用会在释放内核栈前撤销仍发布的 waiter，旧 generation 不能接收新线程的事件。返回正数后，调用者用同一个 filter 调用 `agent_timeline_query()` 读取记录。普通进程调用返回 `-1`，非法 filter flags 返回 `AGENT_STATUS_BAD_PARAM`。

`agent_timeline_read(filter, records, max, timeout_ticks)` 使用与 `agent_timeline_wait()` 相同的等待和唤醒规则，但在匹配记录出现后立即把最多 `max` 条记录复制到 `records`。如果当前已经有匹配记录，它不睡眠并直接复制；如果当前没有匹配记录，它先睡眠等待，醒来后在同一次 syscall 中复制记录。`max=0` 时只返回匹配数量，不复制记录；`max>AGENT_TIMELINE_MAX_RECORDS` 返回 `AGENT_STATUS_BAD_PARAM`；坏输出指针在睡眠前返回 `-1`。这个接口用于最终 Web UI 或 Agent worker 的热路径，避免 `agent_timeline_wait()` 返回后再调用 `agent_timeline_query()` 的第二次 syscall 和中间状态变化。

`struct agent_timeline_record` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `source` | 规范化来源：`CONTEXT`、`SCHED`、`AUDIT` |
| `kind` | 来源内部类型；Context/Sched 使用 trace kind，Audit 使用 audit kind |
| `tick` | 产生该条记录时的 tick 计数值 |
| `sequence` | 来源内部序号：Context sequence、dispatch count 或 audit sequence |
| `cause_sequence` / `span_id` | 因果字段 |
| `pid` / `tid` | 产生记录时的原始进程/线程数值；`tid=0` 也是合法主线程，不能据此判断“无线程信息” |
| `source_pid` / `target_pid` | 事件或提示的来源与目标；单 Agent 记录中通常等于 `pid` |
| `role` / `loop_state` | 记录产生时的 Agent 角色和 Loop 状态 |
| `tool_id` / `event_type` / `status` | 工具、事件类型和状态码 |
| `value0` / `value1` / `value2` | 来源相关的数值槽，例如调度分数、事件 ID、source fid、target fid、候选记录数 |
| `flags` | Context flags、调度 reason flags或审计 flags |
| `text` | 32 字节短摘要 |

timeline、trace 和调度历史中的 `tid` 当前没有同时保存 `thread.identity_generation`。因此 `tid=0` 既可能表示主线程，也可能出现在没有独立线程归因的规范化来源中；任意 raw tid 都不是 incarnation-safe 历史身份，线程槽回收后不能用它连接新旧记录。wait queue、mutex owner 和 timeline waiter 在内核内部使用 generation-safe key，Agent 授权则使用 lifecycle/control identity；公开 pid/tid 只用于显示与过滤，不能作为授权或跨槽归因依据。

`struct agent_timeline_filter` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `flags` | 使用 `AGENT_TIMELINE_FILTER_*` 位选择参与匹配的字段 |
| `source_mask` | 配合 `AGENT_TIMELINE_FILTER_SOURCE_MASK`，可选择 Context、Sched、Audit 来源 |
| `start_tick` | 配合 `AGENT_TIMELINE_FILTER_START_TICK`，只返回 `tick >= start_tick` 的记录 |
| `span_id` | 配合 `AGENT_TIMELINE_FILTER_SPAN_ID`，只返回指定 span 的记录 |
| `require_flags` | 配合 `AGENT_TIMELINE_FILTER_FLAGS_ALL`，要求记录 flags 至少包含这些位 |
| `after_tick` / `after_source` / `after_sequence` | 配合 `AGENT_TIMELINE_FILTER_AFTER_CURSOR`，只返回严格晚于该三元游标的记录 |
| `kind` | 配合 `AGENT_TIMELINE_FILTER_KIND`，匹配来源内部类型 |
| `pid` / `source_pid` / `target_pid` | 匹配产生记录的 Agent、事件来源 Agent 或事件目标 Agent |
| `role` | 匹配 Agent 角色 |
| `tool_id` | 匹配工具 ID |
| `event_type` | 匹配事件类型 |
| `status` | 匹配状态码 |

`agent_provenance_snapshot(edges, max)` 返回当前 Agent 可见的因果边。`max=0` 返回匹配到的边数量，不复制记录；`max>0` 时复制最多 `max` 条。普通进程调用返回 `-1`。该接口不扩大权限：每个 Agent 都能看到自己的 Context 因果边；审计边遵循与 timeline 相同的可见规则，orchestrator 可见本 scope 审计边，其他具备审计能力的 Agent 只可见当前可信 span 内的审计边。跨 Agent cause 的 source pid/control 来自内核 sidecar，不把 source 本地 sequence 误解释为 target 本地 Context。

provenance 对 Context 和 scope audit 各执行一次有界扫描；被过滤掉的候选仍计入 kernel-work 预算，`max=0` 不形成免计费旁路。

`struct agent_provenance_edge` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `kind` | 边来源：`CONTEXT` 或 `AUDIT` |
| `source_type` / `target_type` | 源节点和目标节点类型：Context 或 Audit |
| `source_sequence` / `target_sequence` | 源节点和目标节点在对应来源中的 sequence |
| `span_id` | 该因果边所属 span |
| `tick` | 目标节点产生时的 tick |
| `source_pid` / `target_pid` | 源 Agent 和目标 Agent；单 Agent Context 边通常相同 |
| `role` | 目标记录产生时的 Agent 角色 |
| `tool_id` / `event_type` / `status` | 关联工具、事件类型和状态码 |
| `value0` / `value1` / `value2` | 来源相关数值，例如文件 fid、候选记录数或线程 id |
| `flags` | Context flags 或 audit flags |
| `text` | 32 字节短摘要 |

`struct agent_sched_record` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `tick` | 记录产生时的 tick |
| `dispatch_count` | 本 Agent 第几次被调度 |
| `score` | 本次调度分值 |
| `reason_flags` | 参与本次调度分值计算的原因 flags |
| `event_queue_count` | 被调度前待消费事件数量 |
| `ready_age` | 进入可运行状态后的等待 tick |
| `deadline_delta` | timeout deadline 距当前 tick 的差值；无 deadline 时为 0 |
| `heartbeat_due` | heartbeat 是否到期 |
| `vruntime` | 调度前虚拟运行量 |
| `budget_used` | 调度前预算使用量 |
| `pid` / `tid` | 被调度的进程和线程 |
| `role` / `loop_state` / `weight` / `priority` | 被调度时的 Agent 角色、Loop 状态、角色权重和调度优先级 |

`agent_sched_config(config)` 由 orchestrator 调用，用于配置目标 Agent 的调度参数。普通进程调用返回 `-1`，非 orchestrator Agent 返回 `AGENT_STATUS_DENIED`。结构体字段：

| 字段 | 含义 |
| --- | --- |
| `update_mask` | 使用 `AGENT_SCHED_CONFIG_*` 位选择要更新的字段 |
| `target_pid` | 目标 Agent pid |
| `policy` | 当前支持 `AGENT_SCHED_POLICY_ADAPTIVE` |
| `weight` | 权重，合法范围 `AGENT_SCHED_WEIGHT_MIN..AGENT_SCHED_WEIGHT_MAX` |
| `priority` | 额外优先级，合法范围 `AGENT_SCHED_PRIORITY_MIN..AGENT_SCHED_PRIORITY_MAX` |
| `budget` | 调度预算，合法范围 `AGENT_SCHED_BUDGET_MIN..AGENT_SCHED_BUDGET_MAX` |

配置接口只更新 `update_mask` 指定的字段。参数越界返回 `AGENT_STATUS_BAD_PARAM`，目标不存在或目标不是 Agent 返回 `AGENT_STATUS_NOT_FOUND`。`agentsched_ucore` 会把一个 sentinel Agent 配置为 `weight=150 priority=20 budget=3`，并检查后续调度记录中出现 `AGENT_SCHED_REASON_PRIORITY`。

调度原因 flags：

| flag | 含义 |
| --- | --- |
| `AGENT_SCHED_REASON_ROLE_WEIGHT` | 角色权重参与基础分 |
| `AGENT_SCHED_REASON_EVENT_QUEUE` | 事件队列中有待消费事件 |
| `AGENT_SCHED_REASON_WAITING` | Agent 处于等待状态 |
| `AGENT_SCHED_REASON_DEADLINE_NEAR` | timeout deadline 接近 |
| `AGENT_SCHED_REASON_DEADLINE_NOW` | timeout deadline 已到或马上到 |
| `AGENT_SCHED_REASON_HEARTBEAT_DUE` | heartbeat 到期 |
| `AGENT_SCHED_REASON_BUDGET_USED` | 当前调度预算已用满并产生扣分 |
| `AGENT_SCHED_REASON_VRUNTIME` | 虚拟运行量产生扣分 |
| `AGENT_SCHED_REASON_READY_AGE` | 进入可运行队列后的等待时间参与调度分值计算 |
| `AGENT_SCHED_REASON_PRIORITY` | orchestrator 配置的 priority 参与调度分值计算 |

`struct agent_trace_record` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `kind` | 记录来源：`AGENT_TRACE_KIND_CONTEXT` 或 `AGENT_TRACE_KIND_SCHED` |
| `tick` | 记录产生时的 tick |
| `sequence` | Context 记录的 sequence，或调度记录的 dispatch count |
| `cause_sequence` / `span_id` | Context 因果字段；调度记录中为 0 |
| `value0` / `value1` / `value2` | Context 记录的数值槽；调度记录中分别为 score、event queue count、vruntime |
| `flags` | Context record flags，或调度 reason flags |
| `tool_id` / `status` | Context 工具 ID 和结果状态；调度记录中为 0 |
| `role` / `loop_state` | 记录产生时的 Agent 角色和 Loop 状态 |
| `pid` / `tid` | 对应进程和线程 |
| `text` | Context 短结果或短 payload；调度记录中为 `sched` |

`struct agent_audit_record` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `sequence` | 系统审计序号，单调递增；某个 scope 的可见序列允许跳号 |
| `kind` | 记录来源：Context 追加、事件入队、事件消费或调度 dispatch |
| `tick` | 记录产生时的 tick |
| `prev_hash` | 追加本条审计记录前的同 scope 逻辑账本链尾 hash |
| `record_hash` | 由 prev_hash、sequence、tick、cause/span、角色、来源、状态、数值槽和短文本计算得到的本条记录 hash |
| `pid` / `source_pid` / `target_pid` | 产生记录的 Agent、事件来源和事件目标 |
| `agent_id` / `role` / `loop_state` | 记录产生时的 Agent 身份、角色和 Loop 状态 |
| `tool_id` / `event_type` / `status` | 工具 ID、事件类型和状态码 |
| `cause_sequence` / `span_id` | 对应 Context 或事件的公开因果字段；可信来源还由内核私有 source control/span owner sidecar 约束 |
| `value0` / `value1` / `value2` | 按来源解释的数值槽；Context 记录保留工具结果数值槽，事件记录保留事件 ID/corr ID/目标 pid，调度记录保留分数和队列信息 |
| `flags` | Context record flags 或调度 reason flags |
| `text` | 32 字节短摘要，例如工具结果、事件 payload 或 `sched` |

当前 `kind` 取值如下：

| 取值 | 含义 |
| --- | --- |
| `AGENT_AUDIT_KIND_CONTEXT` | Context 追加 |
| `AGENT_AUDIT_KIND_EVENT_ENQUEUE` | 事件入队 |
| `AGENT_AUDIT_KIND_EVENT_CONSUME` | 事件消费 |
| `AGENT_AUDIT_KIND_SCHED` | Agent 调度 dispatch |

`struct agent_audit_filter` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `flags` | 过滤开关，使用 `AGENT_AUDIT_FILTER_*` 位 |
| `start_sequence` | 设置 `START_SEQUENCE` 后，只返回 sequence 不小于该值的记录 |
| `span_id` | 设置 `SPAN_ID` 后，只返回同一 span 的记录 |
| `kind` | 设置 `KIND` 后，只返回指定来源类型 |
| `pid` / `source_pid` / `target_pid` | 设置对应 flag 后按产生记录的 Agent、事件来源或事件目标过滤 |
| `role` | 设置 `ROLE` 后按 Agent 角色过滤 |
| `tool_id` / `event_type` / `status` | 设置对应 flag 后按工具、事件类型或状态码过滤 |

`struct agent_ledger_summary` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `version` | 当前为 `AGENT_LEDGER_VERSION = 3`；v3 将同偏移字段定义为未分类记录数 |
| `oldest_sequence` / `latest_sequence` | 当前 scope 可见审计窗口的最早和最新系统 sequence；中间允许因跨 scope 写入和分区滚动而稀疏 |
| `visible_records` | 当前 scope 可复制的审计记录数，最多 128 |
| `total_records` | 当前 scope 建立后累计写入的审计记录数 |
| `dropped_records` | 当前 scope 逻辑记录中已不在可见窗口的数量，即 `total_records - visible_records` |
| `ledger_hash` | 当前 scope 逻辑审计链尾 hash，等于该 scope 最新记录的 `record_hash` |
| `context_records` / `event_records` / `sched_records` | 按来源累计的记录数 |
| `other_records` | 准入拒绝数与从 v8 检查点恢复的停产 prefetch 记录数之和；新内核不再生成 prefetch |
| `timeline_total` | 可作为当前 scope timeline 候选来源的记录总量 |
| `observe_epoch` | 观测 epoch，Context、审计或调度写入时递增 |

`struct agent_event` 是事件等待和唤醒结构：

| 字段 | 含义 |
| --- | --- |
| `type` | 事件类型，如 `AGENT_EVENT_FILE_STATUS` 或 `AGENT_EVENT_MESSAGE` |
| `source_pid` / `target_pid` | 事件来源和目标 |
| `status` | 等待结果状态 |
| `event_id` | 内核分配的事件 ID |
| `tick` | 投递 tick |
| `corr_id` | 可选相关 ID，用于动作、消息、LLM 请求或测试 |
| `cause_sequence` | 触发该事件的来源 Context sequence；跨 Agent 时由内核私有 source pid/control sidecar 确定该 sequence 属于谁 |
| `span_id` | 公开因果链 ID；授权和继承还要求同一 scope 及匹配的内核私有 span owner |
| `payload` | 64 字节短文本事件摘要 |

当前事件类型包括：

| 类型 | 含义 |
| --- | --- |
| `AGENT_EVENT_FILE_STATUS` | 文件状态变化 |
| `AGENT_EVENT_MESSAGE` | Agent 消息 |
| `AGENT_EVENT_TIMER` | 定时/心跳事件 |
| `AGENT_EVENT_JOB_DONE` | 作业完成 |
| `AGENT_EVENT_POLICY_DENIED` | 策略拒绝 |
| `AGENT_EVENT_CONTEXT_LIMIT` | Context 限制事件 |
| `AGENT_EVENT_LLM_DONE` | 用户态 LLM Relay 返回解释或摘要；内核只负责事件、Context、timeline 和审计记录 |
| `AGENT_EVENT_DASHBOARD_EXPORT` | 可视化导出完成；当前作为页面工具预留事件 |
| `AGENT_EVENT_CANCELLED` | `agent_wait_cancel()` 产生的等待取消事件 |

`sys_agent_heartbeat_set()` 和 `sys_agent_heartbeat_stop()` 分别使用 syscall 552、553；`agent_heartbeat_set()` / `agent_heartbeat_stop()` 是对应便利 wrapper，512 号 `agent_heartbeat(int)` 保留为旧 ABI。所有入口和工具调用共用 `0 <= interval <= AGENT_HEARTBEAT_MAX_TICKS`（`0x7fffffffULL`）校验，越界或负数的 64 位表示返回 `AGENT_STATUS_BAD_PARAM` 且不改变原状态。重设周期从当前 tick 重新计时；stop 幂等，只阻止后续生成，不删除已经入队的 heartbeat。

heartbeat 到期时内核投递 payload 为 `timer=heartbeat` 的 SYSTEM `AGENT_EVENT_TIMER` 并唤醒睡眠 Agent。该内生触发不要求 TIMER watch，watch/unwatch 也不能抑制它；同一 Agent 同时最多有一条未消费 heartbeat，周期继续到期时执行 coalesce，避免慢消费者造成无界积压。

`agent_wait_cancel(pid, reason)` 是 Agent-only 控制接口。消息数据面与等待控制面彼此独立：调用者必须具备 `AGENT_CAP_WAIT_CANCEL`，目标必须由调用者直接创建，并且双方必须仍属于同一 active workflow scope。创建时内核为每个 Agent 签发不向用户态暴露的 64 位 control id，并把创建者的 control id 绑定为目标 controller；授权不读取 role、PID、父指针或可复用 PCB 地址，controller 退出后旧 id 也不会转授给复用该槽的新进程。内核给合法目标写入一次性取消令牌并唤醒目标；如果目标已经在 `agent_wait()` 中睡眠，会立即返回 `AGENT_STATUS_CANCELLED`；如果取消令牌先到达，目标下一次 `agent_wait()` 会立即返回。返回事件的 payload 保存短 reason，cause/span 及其私有来源身份由内核继承。普通进程调用返回 `-1`，能力不足、scope 不同或目标不受调用者控制返回 `AGENT_STATUS_DENIED`，目标不存在或正在退出返回 `AGENT_STATUS_NOT_FOUND`，目标已有未消费取消令牌时返回 `AGENT_STATUS_DUPLICATE`。

## 上下文路径接口：Context Path

| 接口 | 行为 |
| --- | --- |
| `context_push(record)` | 追加手动节点，内核分配新的 sequence |
| `context_query(start_sequence, out, max)` | 从 `start_sequence` 起按时间顺序复制仍可见记录；`start_sequence=0` 表示从最早可见记录开始 |
| `context_snapshot(header, records, max)` | 一次返回 archive header 和按路径顺序排列的 active records |
| `context_detail(sequence, out)` | 返回最近 128 条完整详情中指定 sequence 对应的 `agent_op`、`agent_result` 和 flags |
| `context_rollback(sequence)` | 以仍在 FIFO 窗口内的 sequence 为因果锚点创建新 branch；保留旧历史且不复用 sequence，不存在时返回 `AGENT_STATUS_NOT_FOUND` |
| `context_clear()` | 清空当前可见记录并创建新的 branch identity；旧 branch identity 不复用 |

`struct agent_context_record` 保存工具 ID、状态码、sequence、request_id、cause_sequence、span_id、`branch_generation`、数值槽、tick、flags、`prev_hash`、`record_hash`，以及 16 字节 payload/result 短文本摘要；工具名称可通过 `agent_tool_list()` 或 `sys_tool_list()` 按 `tool_id` 解释。它不是完整 raw 请求/响应日志。最近 128 条完整详情保存在按活跃 Agent 分配的内核私有 Context sidecar 中，通过 `context_detail()` 查询，不放在用户 Context 页或固定 PCB 大数组内。超过 128 条记录时，Context Path 按 header 明示的 `AGENT_CONTEXT_EVICT_FIFO` 覆盖旧记录。

Context v9 保留轻量因果链与完整性链，并把 branch/lifecycle identity 及本地 active-path parent 纳入可信绑定：

| 字段 | 含义 |
| --- | --- |
| `cause_sequence` | 当前记录由哪条前序 Context record 触发；0 表示本链路根节点 |
| `span_id` | 当前链路 ID，用于把工具调用、事件投递和事件消费串起来 |
| `prev_hash` | 追加本条记录前的 Context 链尾 hash；第一条记录为 0 |
| `record_hash` | 由 prev_hash、sequence、cause/span、工具 ID、状态、数值槽和短文本摘要计算得到的记录 hash |
| `branch_generation` | 由当前 workflow lifecycle ledger 分配的不可复用 Context 分支身份 |

`struct agent_context_header.latest_record_hash` 暴露当前分支的链尾 hash，`visible_head_sequence` 暴露 rollback 选择的历史锚点，`active_path_count/active_path_oldest_sequence` 则界定当前仍在 FIFO archive 内的活跃 suffix。`context_rollback(sequence)` 要求目标仍在物理 FIFO 窗口内，并为后续提交分配新的 `branch_generation`；它把当前因果锚点设为目标 record，但不删除、覆盖或重编号旧分支记录。后续记录取得新的全局 sequence，以 `path_parent_sequence` 指向 rollback 锚点，并在 record/provenance 中同时绑定旧 source branch 与新 target branch。`context_clear()` 同样开始新分支，而不是使旧 branch identity 可复用。该语义保证 provenance 指向的历史不因 rollback 被改写。

内核写入自动工具记录时，会使用当前 Agent 的 `current_cause_sequence` 和 `current_span_id`，并在 Context owner 管理的私有 sidecar 中保存真实 source pid/control id 与 span owner；写入完成后，当前 cause 更新为新记录的 sequence。9 页 Context sidecar、1 页 IPC/观测冷 sidecar 与 7 页单拷贝 Context 一起按 17 页 Agent 状态通过 EXEC resource account 精确预留、提交和退款。`context_push(record)` 只能提交本地手动内容，调用者必须把 `cause_sequence` 和 `span_id` 都设为 0；内核再把记录接到当前可信链。该机制用于内核内可信审计和示例追踪，不等同于持久化密码学保证。

Context record flags：

| flag | 含义 |
| --- | --- |
| `AGENT_CONTEXT_RECORD_F_SYSTEM` | 内核自动工具或系统事件记录 |
| `AGENT_CONTEXT_RECORD_F_MANUAL` | `context_push()` 手动记录 |
| `AGENT_CONTEXT_RECORD_F_TRUNCATED` | payload 或 result 短摘要发生截断 |
