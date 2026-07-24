# 接口与 ABI：Agent-OS

本文档描述用户态程序与 Agent-OS 内核扩展之间的稳定接口约定。Agent 结构体和常量定义以内核态 `os/agent.h` 和用户态 `user/include/agent.h` 为准；块 I/O 策略 ABI 由根目录 `io_policy.h` 与 `user/include/io_policy.h` 同步定义。

## 系统调用

### 系统调用：Agent-OS

Agent-OS 在 uCore syscall 编号空间中使用 500 至 542 及 545；通用内核工作与块 I/O 观测接口使用 543、544：

| syscall | 编号 | 用户态原型 | 说明 |
| --- | ---: | --- | --- |
| `agent_create` | 500 | `int agent_create(void)` | 创建 Agent 子进程 |
| `agent_info` | 501 | `int agent_info(struct agent_info *)` | 查询当前进程 Agent 元信息 |
| `agent_run` | 502 | `int agent_run(struct agent_op *, struct agent_result *, int, uint64)` | 高性能批量工具调用入口 |
| `agent_call` | 503 | `int agent_call(struct agent_request *, struct agent_response *)` | 正式名称协议入口，使用工具名称和参数键值列表 |
| `agent_tool_list` | 504 | `int agent_tool_list(struct agent_tool_desc *, int)` | 查询工具列表 |
| `context_push` | 505 | `int context_push(struct agent_context_record *)` | 手动追加 Context Path 节点 |
| `context_query` | 506 | `int context_query(uint64, struct agent_context_record *, int)` | 按 sequence 查询可见 Context Path |
| `context_snapshot` | 507 | `int context_snapshot(struct agent_context_header *, struct agent_context_record *, int)` | 批量返回 header 和可见 Context Path |
| `context_rollback` | 508 | `int context_rollback(uint64)` | 回滚到仍可见的历史节点 |
| `context_clear` | 509 | `int context_clear(void)` | 清空当前 Agent Context Path |
| `agent_watch` | 510 | `int agent_watch(int, const char *)` | 注册 Agent Loop 事件类型和短文本过滤器 |
| `agent_wait` | 511 | `int agent_wait(struct agent_event *, int)` | 等待事件或 timeout，成功消费事件后写入 Context Path |
| `agent_heartbeat` | 512 | `int agent_heartbeat(int)` | 设置心跳间隔并更新最后心跳 tick |
| `agent_wake` | 513 | `int agent_wake(int, struct agent_event *)` | 向目标 Agent 投递 `AGENT_EVENT_MESSAGE` 消息事件 |
| `agent_file_meta_init` | 514 | `int agent_file_meta_init(void)` | 重新加载文件对象元数据、重建索引并启用扫描 |
| `agent_file_meta_set` | 515 | `int agent_file_meta_set(struct agent_file_meta *)` | 插入或合并更新文件元数据，状态变化可触发事件 |
| `agent_file_query` | 516 | `int agent_file_query(struct agent_file_query *, struct agent_file_query_result *)` | Agent 文件属性查询，成功后写入 Context Path |
| `agent_create_role` | 517 | `int agent_create_role(int role)` | 按真实内核角色创建 Agent 子进程 |
| `agent_unwatch` | 518 | `int agent_unwatch(int, const char *)` | 删除匹配 watch；`AGENT_EVENT_NONE` 加空 filter 表示清空全部 watch |
| `context_detail` | 519 | `int context_detail(uint64, struct agent_context_detail *)` | 按 sequence 查询完整工具调用详情 |
| `agent_wait_cancel` | 520 | `int agent_wait_cancel(int pid, const char *reason)` | 给目标 Agent 设置一次性等待取消令牌，并唤醒目标 |
| `agent_sched_snapshot` | 521 | `int agent_sched_snapshot(struct agent_sched_record *, int)` | 查询当前 Agent 最近 16 次调度原因记录 |
| `agent_trace_snapshot` | 522 | `int agent_trace_snapshot(struct agent_trace_record *, int)` | 合并返回当前 Agent 的 Context 摘要和调度原因短记录 |
| `agent_audit_snapshot` | 523 | `int agent_audit_snapshot(struct agent_audit_record *, int)` | orchestrator 查询当前 workflow scope 的审计短记录 |
| `agent_audit_query` | 524 | `int agent_audit_query(struct agent_audit_filter *, struct agent_audit_record *, int)` | 在当前 scope 可见审计窗口内过滤查询 |
| `agent_sched_config` | 525 | `int agent_sched_config(struct agent_sched_config *)` | orchestrator 配置目标 Agent 的调度 policy、weight、priority 和 budget |
| `agent_file_prefetch_snapshot` | 526 | `int agent_file_prefetch_snapshot(struct agent_file_prefetch_hint *, int)` | 查询当前 Agent 的文件 metadata 预取提示 |
| `agent_file_prefetch_span_snapshot` | 527 | `int agent_file_prefetch_span_snapshot(struct agent_file_prefetch_hint *, int)` | 查询当前 scope/private-owner span 的文件 metadata 预取提示 |
| `agent_span_trace_snapshot` | 528 | `int agent_span_trace_snapshot(struct agent_audit_record *, int)` | 当前 Agent 查询当前 span 的系统级短记录 |
| `agent_timeline_snapshot` | 529 | `int agent_timeline_snapshot(struct agent_timeline_record *, int)` | 统一导出当前 Agent 可见的 Context、调度、审计和预取提示短记录 |
| `agent_timeline_query` | 530 | `int agent_timeline_query(struct agent_timeline_filter *, struct agent_timeline_record *, int)` | 在统一 timeline 上执行内核侧过滤查询 |
| `agent_provenance_snapshot` | 531 | `int agent_provenance_snapshot(struct agent_provenance_edge *, int)` | 导出当前 Agent 可见的 Context、审计和预取因果边 |
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
| `kernel_work_last_preemptions` | 543 | `long kernel_work_last_preemptions(void)` | 读取当前线程上一 syscall 的内核工作重调度次数 |
| `io_policy_info` | 544 | `int io_policy_info(struct io_policy_info *)` | 读取当前持久 owner 和前台 I/O class 的预算、等待、物理传输及 cache 观测，不修改策略状态 |
| `agent_workflow_close` | 545 | `int agent_workflow_close(uint64 scope_id)` | 由绑定根 controller 或可信 bootstrap factory 使 workflow 进入 CLOSING，并协作终止其全部成员 |

`agent_run` 和 `context_snapshot` 是性能主路径。`agent_file_prefetch_snapshot` 用于读取当前 Agent 自己可见的 metadata 预取提示，`agent_file_prefetch_span_snapshot` 用于读取同一可信 scope 和 span 下跨 Agent 汇总的 metadata 预取提示。`agent_trace_snapshot` 是单个 Agent 的运行查看和排查主路径，用于把工具调用历史与调度原因放进同一组短记录中。`agent_span_trace_snapshot` 读取当前 Agent 所在可信 span 的系统级短记录，使参与协作的 Agent 能解释本轮协作中的 Context、事件和预取交接来源。`agent_timeline_snapshot` 是统一导出入口，把当前 Agent 可见的 Context、调度、审计和预取提示转换成同一种 record，便于科研平台页面直接读取。`agent_timeline_query` 在同一组可见记录上执行 source、tick、span、pid、kind、tool、event、status、flags 和 after-cursor 过滤，减少页面重复拉取和用户态筛选，也支持页面拿上一条记录作为游标继续读取后续记录。`agent_timeline_wait` 复用同一 filter，在没有匹配记录时让 Agent 睡眠；新记录写入时内核把新记录规范化为 `agent_timeline_record`，并直接用等待者保存的完整 filter 判断是否唤醒。`agent_timeline_read` 在同一套规则上把等待和复制合并为一次 syscall，减少页面或 Agent worker 的 wait 后再 query 成本。`agent_file_edit_begin`、`agent_file_edit_commit`、`agent_file_edit_abort` 和 `agent_file_edit_state` 是真实文件编辑冲突控制接口；内核用真实 `dev + inum + incarnation` 识别文件，并在 `write`、`O_TRUNC`、`unlink` 路径上检查租约持有者和精确 scope。`agent_worker_create` 不创建 Agent 身份或 Agent Context，而是让 orchestrator 在自己的 scope 内显式建立一个最小权限 workflow worker；子进程随后必须执行创建时绑定的 immutable、domain-safe worker 映像才能取得受限文件系统能力。`agent_workflow_create` 是唯一创建新 workflow security boundary 的用户 ABI，角色委派接口本身不能铸造新 scope；`agent_workflow_close` 是对应的可信终止 ABI，关闭权由生命周期账本中的唯一根 control id 或可信 factory 身份决定。`agent_scope_delegate_fd` 只让调用线程的下一次 workflow、Agent、worker 或降权普通子主体显式携带选中的 pipe 端点。`agent_provenance_snapshot` 导出同一可见范围内的因果边，用于页面绘制“哪个 Context、事件或预取提示触发了后续动作”。`agent_audit_snapshot` 和 `agent_audit_query` 是 orchestrator 的 scope 内系统级观测入口；底层物理表共 512 槽，但调用者最多看到自己的 128 槽配额窗口。`agent_ledger_snapshot` 在同一 scope 的逻辑账本上返回可见范围、总量、已淘汰数、分类计数和账本 hash。`agent_call` 是赛题“工具名称 + 参数键值列表”结构化协议的正式入口，也兼容已有示例程序。

### 基础兼容系统调用：uCore

| syscall | 编号 | 用户态原型 | 说明 |
| --- | ---: | --- | --- |
| `mailread` | 401 | `int mailread(void *buf, int len)` | 非阻塞读取当前进程普通 mail 队列；无消息返回 0 |
| `mailwrite` | 402 | `int mailwrite(int pid, void *buf, int len)` | 向目标普通进程 mail 队列写入最多 256 字节 |
| `trace` | 410 | `int trace(enum trace_request req, unsigned long id, uint8 data)` | 支持 `TRACE_READ`、`TRACE_WRITE` 和 syscall 计数查询 |

这些接口用于保留代表性基础 uCore 用户测试能力。Agent-OS 的最终验收主路径仍是 `CHAPTER=agent` 下的专项程序。

`mailread` / `mailwrite` 使用每进程 16 槽普通消息队列，每条最多 256 字节。`mailread` 无消息时返回 0，成功时返回读取字节数；`mailwrite` 成功时返回写入字节数。目标不存在、长度非法、队列满或用户指针错误返回 `-1`。

`trace` 的 `TRACE_READ` / `TRACE_WRITE` 只做 1 字节用户地址读写检查。`TRACE_SYSCALL` 返回对应 syscall ID 的累计进入次数，查询 `SYS_trace` 时本次 `trace` 调用也计入。AgentOS-uCore 的镜像构建器从配套 ELF 提取只读可执行段与可写段的页对齐分界点，loader 校验该布局后把代码页映射为 RX，把数据、bss、用户栈和 Agent Context 映射为 RW+NX；用户页不会同时拥有写和执行权限。

### 块 I/O 策略观测

```c
int io_policy_info(struct io_policy_info *info);
```

用户态 wrapper 自动把 `sizeof(*info)` 作为 syscall 544 的隐式第二参数传给内核；底层内核 ABI 是 `(addr, user_size)`。内核要求 `user_size >= 8`，先生成当前完整结构，再只复制 `min(user_size, sizeof(struct io_policy_info))` 字节。前两个 32 位字段固定为 `version` 和 `struct_size`，以后只能在结构尾部追加字段。因此旧用户库仍按它编译时的较小 `sizeof` 读取稳定前缀，并可用 `struct_size` 判断当前内核完整结构的大小，不需要因尾部扩展破坏已有读取程序。

该接口是只读观测面。普通进程返回安装级 PUBLIC owner，workflow 进程返回带 `IO_POLICY_OWNER_SCOPE_FLAG` 的稳定 scope owner；Orchestrator/Recovery 的前台请求使用 `CONTROL` class，其他 workflow 与 PUBLIC 前台请求使用 `NORMAL`。内核扫描、metadata checkpoint 和 scope reclaim 不伪装成调用 scheduler 的进程，而是显式使用 SYSTEM 或触发 workflow 的 `BACKGROUND` class。owner 在 syscall 或后台 job 开始时捕获，PID 退出、重新 fork 或调度切换不会重置其账本。

预算信用代表一次已经完成的 1 KiB 块设备传输，refill 以 I/O policy tick 为单位：

| owner / class | burst | 每 tick refill |
| --- | ---: | ---: |
| PUBLIC / NORMAL | 32 | 16 |
| 每个 active workflow / NORMAL | 24 | 12 |
| 每个 active workflow / CONTROL | 48 | 24 |
| 每个 active workflow / BACKGROUND | 8 | 4 |
| 每个 retiring workflow / BACKGROUND | 8 | 4 |
| SYSTEM / SYSTEM | 96 | 48 |
| SYSTEM / BACKGROUND | 16 | 8 |
| 前台共享 slice | 32 | 16 |
| 设备根 bucket | 560 | 280 |

每个可能触盘的 syscall 先取得 owner 或 shared lease，并尝试取得设备根 lease；首个 VirtIO 完成事件提交已有 lease，后续完成事件继续消费本 class 与设备根 token，超额分别形成 owner debt 和 device debt。没有发生物理传输的请求会退款。相同线程中的嵌套文件操作沿用外层归因；退出撤销会清理未提交 lease。owner/class admission 使用各自的 FIFO 等待队列；存在 admission 排队者时，shared grant 按有资格的前台 owner/class cursor 轮转，没有排队者的 fast path 可直接借用 shared slice。`BACKGROUND` 不能借 shared slice。

设备根不是对所有 class 一刀切的硬总上限。PUBLIC、workflow `NORMAL` 和非保护后台流量必须取得根信用，并在 device debt 清零前等待；SYSTEM owner、`CONTROL` 和 `SYSTEM` class 在根信用耗尽时仍可凭自己的 owner/class 保留预算前进，但物理完成仍记入 device debt，后续 refill 先偿债。这样普通流量受 560/280 根速率约束，关键恢复/控制路径又不会因 PUBLIC 已耗尽根信用而停顿。编译期断言只验证已配置的 PUBLIC、4 个 active workflow、最多 8 个 retiring workflow `BACKGROUND`、SYSTEM 与 shared burst/refill 落在 560/280 静态 envelope 内；它不把保护流量的运行时带债前进描述成硬聚合限额。生命周期 admission 仍要求计入 admission 的 ACTIVE/CLOSING 与 RETIRING 身份合计不超过 8。

scheduler 每轮先把 `current_thread` 指向 idle context，安装 kernel trap 向量，短暂开启中断后再执行后台维护和选择下一线程。这个固定交付窗口不是按进程或 syscall 加白名单；它保证唯一 runnable 线程反复在内核态 pipe 条件路径 `yield()`、长期不返回用户态时，pending timer/device 中断仍能推进 I/O debt、token refill 和设备完成。中断窗口结束后 scheduler 再关中断进入两级选择：先严格轮转 active `resource_domain_id`，再从选中域的线程队列选择一个候选。

线程资源策略是内核 admission 契约，不新增用户态 syscall。进程 admission 原子预扣 t0 主线程；额外线程把不可变 `resource_domain_id`、ordinary/reserved admission 类别和 charged 状态保存在 `struct thread` 中。`THREAD_RESOURCE_POOL_SIZE` 约束物理总量，`THREAD_RESOURCE_ORDINARY_LIMIT` / `THREAD_RESOURCE_RESERVED_LIMIT` 划分普通水位与系统保留，两个 `THREAD_RESOURCE_DOMAIN_*_LIMIT` 再限制单域；策略头以静态断言保证 ordinary/reserved 单域上限分别严格小于对应全局水位，使两层 admission 始终是可独立验证的机制。创建失败、线程退出和 exec 清理 sibling 都经过统一退款；域只有在进程、线程和文件对象计数都归零后才可复用。

由主线程触发的正常退出、用户 fault 或非法指令通过 `exit()` 进入进程级 terminal teardown；非主 sibling 无论正常退出还是 fault 都走 `thread_exit_current()`，不释放整个进程。进程级退出等待 sibling 内核栈静止后，主线程调用 `kernel_work_begin_cleanup()` 与 `bio_request_begin_current_cleanup()` 建立不可中断的清理上下文；`fileclose()`、未链接 inode 回收和其他资源释放继续使用该线程原有稳定 owner/class。`bio_request_end_current_cleanup()` 提交剩余 lease 并结算 owner/class debt；PUBLIC/NORMAL 还等待 device debt，SYSTEM/CONTROL 的受保护 device debt 留在全局设备根账本中由 refill 偿还。随后才结束 kernel-work cleanup、`freethread()` 和 `vfs_proc_reset()`，因此主线程异常退出不能借线程账本先消失而制造未归因传输或遗留线程私有账目。

`struct io_policy_info` 的字段语义如下：

| 字段 | 说明 |
| --- | --- |
| `version` | 当前为 `IO_POLICY_VERSION=3` |
| `struct_size` | 当前内核完整 `struct io_policy_info` 的字节数；调用者收到的前缀长度仍由它传入的 `user_size` 决定 |
| `owner` / `io_class` | 调用者的稳定 owner 与前台 class |
| `tokens` / `leased` / `debt` | 当前 class 可用信用、尚未由完成事件提交的 lease 和待偿还超额传输 |
| `class_burst` / `class_refill` | 当前 class 配置 |
| `shared_tokens` / `shared_leased` | 前台共享 slice 的可用与已租信用 |
| `device_burst` / `device_refill` | 普通流量设备根 bucket 的 560/280 配置；保护流量可带债前进 |
| `device_tokens` / `device_leased` / `device_debt` | 设备根可用信用、未提交 lease 和累计待偿还传输；其中 debt 也包含保护流量在根信用耗尽后的实际完成 |
| `waiters` | admission 与 debt waiter 总数 |
| `admission_waiters` / `debt_waiters` / `admission_granted` | 两阶段等待队列及当前 FIFO baton 状态 |
| `admissions` / `throttles` / `waits` / `refills` | owner 聚合的接纳、限流、睡眠和补充计数 |
| `reserved_grants` / `shared_grants` | owner 保留信用和共享信用的累计授予数 |
| `physical_reads` / `physical_writes` | 在 VirtIO 完成路径按稳定 owner 累计的真实块传输 |
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
| 大小 | `AGENT_CONTEXT_SIZE = 6 * 4096` |
| 当前大小 | 24576 字节 |
| 记录容量 | `AGENT_CONTEXT_MAX_RECORDS = 128` |
| Context 版本 | `AGENT_CONTEXT_VERSION = 6` |
| 权限 | Agent Context 用户镜像页可读写、不可执行；内核 shadow 副本不可被用户态访问 |

说明：用户程序仍以 flat binary 内容装入，但镜像中的 `exec_layout_version` 和 `exec_rw_offset` 来自配套 ELF 并由 loader 重新校验。分界点之前的程序页为 RX，之后的数据和 bss 页为 RW+NX；Agent Context、用户栈同样为 RW+NX。布局缺失、超出有效范围或要求 W+X 的映像不会被装载。

布局：

| 偏移 | 内容 |
| --- | --- |
| `0` | `struct agent_context_header` |
| `sizeof(struct agent_context_header)` | `struct agent_result` |
| `AGENT_CONTEXT_RECORDS_OFFSET = 4096` | `struct agent_context_record[128]` |
| `header.user_cache_offset` | 用户自管 cache 起点，当前测试输出为 21504 |
| `header.user_cache_size` | 用户自管 cache 大小，当前测试输出为 3072 |

内核在 `struct proc` 中同时保存 6 个用户镜像页和 6 个内核私有 shadow 页。header、latest result 和 record 的权威数据先写入 shadow 页，再同步到用户镜像页。用户态直接写镜像页不会改变 `context_query()` 或 `context_snapshot()` 返回的权威历史。

用户自管 cache 区位于 Context 尾部，不进入 shadow 权威历史，也不会被 `context_snapshot()` 刷新覆盖。它只用于 Agent 自己保存策略缓存或短期状态，不能作为内核可信历史。若需要可信历史，应使用 `context_snapshot()` 刷新并读取 shadow 权威数据。若需要完整请求和完整响应，应使用 `context_detail(sequence, out)`，不要把 16 字节短摘要 record 当作完整日志。

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
| `agent_call_count` | 工具调用总数 |
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
| `observe_epoch` | 当前内核观测 epoch；Context、审计、调度和预取提示写入时递增 |
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

业务 capability 只回答“允许做哪类操作”，scope/owner 继续回答“允许操作哪个对象”。有效授权必须同时满足：调用进程属于仍 active 的内核签发 scope、capability 包含所需位、对象 scope 与主体 scope 精确相等；显式标为只读共享的 SYSTEM 对象只在对应读取路径上作为例外。Agent metadata、dependency、action history、编辑租约、版本状态、audit、span prefetch、IPC route 和 wait cancel 均使用该组合，不再把同一角色或同一 capability 解释为跨 workflow 权限。

### 创建和继承

```c
int agent_workflow_create(int role);
int agent_workflow_close(uint64 scope_id);
int agent_scope_delegate_fd(int fd);
```

`agent_workflow_create(role)` 仅允许“非 Agent、具有内核 resource-domain admin 状态、当前执行可信 bootstrap 映像”的 factory 调用。它先执行正常 role grant 检查，再原子申请新的进程资源域和动态 workflow scope，并创建该 scope 的根 Agent；该 scope 同时是 workflow 的存储计费主体，但与进程资源域槽独立。已在某个 scope 内的 orchestrator 即使具备 `ORCHESTRATE`，也只能用 `agent_create_role()` 在本 scope 内创建角色，不能用 role grant 铸造新安全域。系统最多同时接纳 4 个 active workflow；`VFS_SCOPE_LIFECYCLE_CAP=8` 限制计入 admission 的 ACTIVE/CLOSING 与 RETIRING 身份。没有可接纳槽时创建失败且不留下半初始化 scope。

`agent_workflow_close(scope_id)` 使用 syscall 545 发起可信终止。调用者必须是该 scope 创建时、在进程发布前由内核绑定的唯一根 controller，且其当前 `agent_control_id` 必须与生命周期账本精确相等；另一名 Orchestrator、低权限 Agent、PID/父子关系和单独的 `ORCHESTRATE` capability 都不产生关闭权。可信 bootstrap factory 还可以按稳定 scope id 执行恢复性关闭。`scope_id` 按完整 64 位值校验，非动态范围或高位别名返回 `AGENT_STATUS_BAD_PARAM`；无权调用返回 `AGENT_STATUS_DENIED`，factory 查询不到 active/closing 目标返回 `AGENT_STATUS_NOT_FOUND`。成功时返回 `AGENT_STATUS_OK`，但根 controller 关闭自身 scope 时通常会在 syscall 返回边界响应协作退出，以 `AGENT_STATUS_CANCELLED` 终止而不再执行下一条用户指令。

显式关闭与根 controller 的正常退出、异常退出或凭据清除汇合到同一个幂等生命周期入口。scope 原子从 ACTIVE 进入 CLOSING 后，现有 Agent/VFS capability 立即失效，新成员、pending exec 发布和新存储分配均被拒绝；内核向 active/pending 成员提交进程级协作退出请求，只唤醒可中断等待，不替其他线程直接关闭 FD 或释放内存。CLOSING 在成员完成自身 teardown 前仍保留完整 I/O/cache 归因和 admission，最后成员释放引用后才进入 RETIRING，由既有有界 reaper 回收 metadata、文件和 owner 状态。

同 scope 的 `agent_create_role()` 和 `agent_worker_create()` 继承该 scope，但会建立新的安全主体。pipe 是持有型 capability，不会因 scope 相同而自动进入新主体；inode 文件在 scope 不变时继续在每次操作中按新主体凭据重新鉴权，跨 scope 时连描述符也不继承。workflow 或可信 bootstrap 的动态 scope 中，普通 `fork()` 会丢弃 Agent/VFS 凭据，同样属于安全主体边界；普通 PUBLIC init 的 resource-domain admin 位只控制记账域 admission，不参与 Agent/VFS/IPC 授权，父子仍是同一安全主体并保留 POSIX pipe 继承。

`agent_scope_delegate_fd(fd)` 只接受当前打开的 pipe fd。调用者必须是可信 bootstrap factory 或具备 `ORCHESTRATE` 的 Agent。成功票据绑定调用线程，而不是整个进程：该线程下一次创建 workflow、Agent、worker 或发生凭据降级的普通子主体时，内核在不可让出的临界区固定精确 file 对象并消费该线程的全部票据。其他线程只能消费自己的票据；关闭、替换 fd 槽或 `exec` 会撤销相应票据，不能把旧票据转移给新对象。被标记端点才进入该子主体，子进程不继承票据；继续传递必须再次显式授权。创建 syscall 的参数、权限、映像或资源检查失败也会清除调用线程的票据。

### 生命周期和配额

scope 的稳定状态序列是 ACTIVE -> CLOSING -> RETIRING。自然耗尽时可以由最后成员从 ACTIVE 直接进入 RETIRING；强制关闭先进入 CLOSING，停止授权、新成员和新对象分配，但在成员完成 terminal cleanup 前保留完整 I/O/cache 份额。成员数降为 0 后进入 RETIRING，撤销 active 份额；内核随后按 scope 清理 metadata、dependency、action history、edit lease/version、digest/query cache、audit、span prefetch、IPC route 等全局表状态，清理完成后才释放 admission 槽。VFS 生命周期身份账本有 `NPROC` 条记录，active/closing 与 retiring 独立计数，只有 `used == 0` 的记录才允许分配给新 scope；`VFS_SCOPE_LIFECYCLE_CAP=8` 限制计入 admission 的 ACTIVE/CLOSING 与 RETIRING 合计，并最多提供 8 个 FS reclaim cursor。reaper 在 `NPROC` 身份账本范围内轮转选择 retiring scope，避免固定顺序饥饿，也不会用新身份覆盖尚未完成的文件回收或 I/O owner。普通动态 workflow 的文件随 scope 回收。由 boot scope 产生并声明持久化的文件保留在磁盘，其 scope 作为 inactive storage owner 保留，但不计入上述 admission 上限，避免下一次分配把旧文件解释为新 workflow 的对象。

当前固定资源边界：ACTIVE+CLOSING admission 最多 4 个，计入 admission 的 ACTIVE/CLOSING 与 RETIRING 身份合计硬上限 8；进程池 128 槽中普通 admission 96 槽、受控保留 32 槽，每个 admitted scope 的保留份额为 8；Agent metadata 每 scope 112 条、dependency 16 条、action history 8 条、edit lease 8 条、span prefetch 8 条、audit 128 条。共享系统 metadata 另保留 64 条。表满返回可恢复错误，不允许一个 scope 占用另一个 scope 的保证份额。

文件系统使用 `NINODE=2048`。workflow 的配置目标仍为总 inode 的三分之二和 `FSSIZE/2` 个 block，但真正的每 scope 保证由 mkfs 与内核共享的容量策略根据“完成镜像后”的空闲量计算，并把最多四个 workflow 的总目标限制在扣除 SYSTEM 后空闲量的四分之三。每个 admitted/future scope 的硬下限为 320 个 inode 和 512 个 block，SYSTEM 的硬下限为 8 个 inode 和 512 个 block；当前 `platform_agentos` 镜像实际得到每 scope 342 个 inode、1195 个 block，以及 SYSTEM 64 个 inode、512 个 block。mkfs 无法同时兑现硬下限时直接拒绝生成镜像，并把 policy version、scope 数、PUBLIC principal、实际保证、系统保留量和 checksum 作为容量契约写入 superblock。挂载从 qmap 和 dinode 的持久 owner 重建 PUBLIC block/inode 用量，再按契约回收旧 boot lease、验证四份固定保证并重建 SYSTEM 剩余信用；workflow admission 还会原子检查当时的实际余量。PUBLIC 分配必须留下所有尚未消费的 workflow 保证和 SYSTEM 剩余量；SYSTEM 维护分配可以消耗自己的信用，但不能侵占 workflow 的最低保证。缺少稳定 PUBLIC principal 的旧格式镜像会被版本检查明确拒绝，不会用空账本继续运行。

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
| `agent_file_prefetch_snapshot` | 返回 `-1` | `META_READ` |
| `agent_file_prefetch_span_snapshot` | 返回 `-1` | `META_READ` |
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

消息投递先完成路由授权，再匹配目标 watch 并占用队列资源。未授权返回 `AGENT_STATUS_DENIED`；目标不存在、正在退出或 watch/filter 不匹配返回 `AGENT_STATUS_NOT_FOUND`；路由表或队列配额耗尽返回 `AGENT_STATUS_NO_SPACE`。兼容 mailbox 镜像在授权事件成功入队后于同一临界区更新。metadata 预取交接随后使用内核捕获的 `slot + pid + control_id + scope` 稳定端点句柄；长阶段不保存目标 PCB 指针，发布前完整重校验身份，退出或槽复用只会丢弃派生提示。拒绝路径不会留下旁路副作用。

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

## 名称协议请求结构

`struct agent_request` / `struct agent_response` 是赛题“工具名称 + 参数键值列表”的正式结构化协议入口；性能主路径仍使用更紧凑的 `agent_op` / `agent_result`。`struct agent_request` 的关键字段：

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

当 `tool_id` 和 `tool_name` 同时提供时，内核先用 ID 定位工具，再校验名称是否匹配。不匹配时 `agent_call()` 返回 `AGENT_STATUS_BAD_REQUEST`，结果文本为 `tool_mismatch`，不会执行工具。只提供 `tool_name` 时，内核按工具表名称解析工具，并继续校验参数键和类型。

## 错误码

| 错误码 | 值 | 说明 |
| --- | ---: | --- |
| `AGENT_STATUS_OK` | 0 | 成功 |
| `AGENT_STATUS_BAD_REQUEST` | -1 | 版本错误或请求结构不一致 |
| `AGENT_STATUS_UNKNOWN_TOOL` | -2 | 工具不存在 |
| `AGENT_STATUS_NOT_AGENT` | -3 | 普通进程调用 Agent-only 接口 |
| `AGENT_STATUS_BAD_PARAM` | -4 | 参数键、类型或必要参数错误 |
| `AGENT_STATUS_NOT_FOUND` | -5 | 文件、Agent 或历史节点不存在；直接事件投递的目标 watch 不匹配也使用该状态 |
| `AGENT_STATUS_NO_SPACE` | -6 | Context、IPC route 表、事件总量/source/class/external 配额或布局空间不可用 |
| `AGENT_STATUS_TIMEOUT` | -7 | `agent_wait()` 等待超时 |
| `AGENT_STATUS_DENIED` | -8 | capability 或角色权限拒绝 |
| `AGENT_STATUS_DUPLICATE` | -9 | 重复幂等动作被识别 |
| `AGENT_STATUS_CANCELLED` | -10 | `agent_wait()` 被受权 Agent 取消 |
| `AGENT_STATUS_CONFLICT` | -11 | 文件编辑租约已被其他 Agent 持有 |
| `AGENT_STATUS_STALE` | -12 | 提交时给出的期望版本已经不是当前租约基准版本 |

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
| 13 | `capability_check` | `legacy_role:uint64,action:string` | 按当前进程真实 capability 检查动作；返回真实 role 和 capability mask |
| 14 | `rerun_stage` | `legacy_role:uint64,stage:string` | 旧示例兼容；内部调用通用 `action_commit` 状态更新路径 |
| 15 | `write_report` | `legacy_role:uint64,payload:string` | 旧示例兼容；内部调用通用 `artifact_update` 状态更新路径 |
| 16 | `agent_watch` | `event_type:uint64,filter:string` | 注册 Agent Loop watch |
| 17 | `agent_wait` | `timeout:uint64` | syscall-only 可发现项；`agent_run()` 调用返回 `AGENT_STATUS_BAD_PARAM` |
| 18 | `agent_heartbeat` | `interval:uint64` | 设置心跳间隔；`interval=0` 停止心跳 |
| 19 | `context_push` | `record` | 手动 Context 节点使用的内部工具 ID |
| 20 | `read_file_digest` | `selector:string` | 读取真实文件的短预览、参与计算字节数和 FNV-1a 内容指纹 |
| 21 | `action_commit` | `selector:string` | 按通用对象 selector 幂等提交 Agent 动作，可根据依赖标签刷新后续对象 |
| 22 | `artifact_update` | `selector:string` | 按通用对象 selector 更新工件、报告、记忆或结果对象状态 |
| 23 | `llm_request` | `target_pid:uint64,prompt_summary:string` | 记录 LLM 请求摘要；target 非零时沿 `MESSAGE` 路由投递，target 为零时只记录 |
| 24 | `llm_response` | `target_pid:uint64,response_summary:string` | 由具备 `LLM_RELAY` 的 Agent 沿 `LLM_DONE` 路由投递结果，唤醒请求方 |
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

文件元数据授权键先包含当前 kernel-issued workflow scope，再使用真实文件的 `dev + inum + incarnation` 标识该 scope 内的对象生命期。`incarnation` 在 inode 槽重新分配时递增，使删除后复用同一 inode 号的新文件不会继承旧 metadata、租约或摘要缓存；另一 scope 即使创建相同 `physical_name`、`fid`、namespace、run 或 label，也只会命中自己的记录。`physical_name` 必须能解析为 uCore 根目录中当前 scope 的真实短文件名，复杂逻辑路径保存在 `logical_path` 等 Agent 属性字段中。根目录私有文件 `.agentmeta` 和 `.agentmeta1` 组成双 bank 版本化快照：目标 bank 先写入无效 header，再按 1 KiB segment 与已验证的内存 shadow 比较，只写入并逐段回读变化的 `PERSIST` payload；未变化 segment 沿用该 shadow。整体 payload 摘要一致后才发布包含 count、generation 和 payload hash 的有效 header，header 回读一致后才切换 active generation。只有新的 primary 已完整验证并成为 active bank，状态机才用同一不可变快照更新旧 bank 作为 mirror，因此旧的已验证代不会在新主副本可恢复前被覆盖。bank 不再为缩短快照同步 truncate，而是复用已有高水位块；loader 只按 header 声明的精确逻辑长度校验 payload，旧尾部不属于已提交快照。在新 primary 的 header 回读验证完成前，失败保留旧 active bank；切换 active 后 mirror 更新失败时，已验证的新 primary 仍可恢复。校验不匹配会使失败目标 bank 的内存 shadow 失效并强制下次完整重写。显式 metadata set/delete 在同步提交失败时同时回滚内存表和 inode sidecar。`agent_file_meta_init()` 会强制读取两个 bank 并选择 generation 最高的完整快照，但只替换调用者 scope 的已提交记录，其他 scope 的内存记录保持不变；已有 active bank 时重载失败会保留当前状态并返回错误，尚无 active bank 时则把当前可持久记录提交为第一代，避免一次只读查询抢先初始化后阻断 Orchestrator。两个 bank 都标记为 `KERNEL_PRIVATE`，普通文件 syscall 不能直接读取、创建、修改或删除，Agent 子系统内部 helper 使用内核凭据负责持久化和重新加载。

可信 bank 还是用户进程发布的启动依赖。`main()` 在 `fsinit()` 和 `timer_init()` 之后调用 `agent_storage_init()`，在启动继续前完成可信加载判定：成功时选择、校验、绑定并按需恢复 bank，失败时设置 fail-closed；随后才执行 `bio_policy_start()` 和 `load_init_app()`。单个 bank 损坏时选择另一份可验证副本并标记恢复；不存在可验证有效 bank 或选择失败时设置 `agent_meta_store_failed_closed`，系统继续启动，但后续 metadata load/persist/init API 返回失败，不能用空表冒充可信状态。这个 fail-closed 状态不阻断 scope 的 VFS 生命周期回收：`agent_scope_reclaim()` 仍清理 scope 的依赖、动作、缓存、审计、租约和真实 VFS-labelled 文件，并在成功后退休 scope 身份；它不会声称已恢复损坏 bank 中不可读的 metadata。

内存 metadata、索引、依赖表、inode sidecar 与双 bank 提交属于同一个可睡眠、可重入的内核事务域。进程 syscall 在竞争时原子领取单调 ticket，并在对象私有等待队列中不可中断地等待 serving ticket；最外层 owner 清空后只唤醒队首。退出请求不能遗弃 ticket：线程先取得并传递事务门，再由 syscall 边界退出。真实 VFS callback、scope retirement 等进程态外部路径只使用不插队的 try-lock。scheduler 可在事务门恰好空闲时取得一个硬有界维护轮次；若 serving waiter 已由前任唤醒，该保留轮次解锁时抑制第二次 wake-one，保持 ticket 与睡眠队列一一对应。进程态可扩展扫描每 128 条记录计入 kernel-work 预算；scheduler 只执行固定 16 目录项扫描或单个持久化步骤。依赖表只存放每 scope 最多 16 条显式用户边；兼容 `dependency_mask` 始终留在文件记录中，由依赖查询、action 和预取在固定表的线性遍历中按需解释，不再建立全局派生依赖图。

所有可能替换物理 COW job 的同步 set/delete/init/reload 还必须进入单独的 FIFO submit lane。调用者先领取单调 ticket，只有 serving ticket、persist idle、sync owner 和 reload owner 条件同时满足时才能进入；若条件不满足，内核从失败检查开始一直保持中断关闭，依次释放 metadata 事务门、把当前线程插入 submit condition queue，再恢复中断。完成检查到入队之间不存在可丢失 wake 的窗口。ticket 不允许在线程退出时放弃，否则会卡住全部后继；退出请求在该有界 COW lane 完成后由 syscall 边界处理。持久化跨预算等待时 immutable job 保持同一 `job_id`，同步调用者临时释放全局事务门，维护线程只能推进该 job，不能让后来提交者替换它。

普通 workflow 文件的 create/write/truncate/delete callback 不再同步重写全局 metadata bank。write/truncate 先按 inode incarnation 把已提交的 size、更新时间和文件代数发布到 sidecar，create/delete 直接完成对应内存记录变化；只有带 `PERSIST` 的记录才在所属 workflow scope 的 `dirty_generation` 上登记写回，volatile 记录只更新内存和 sidecar。重复持久变化进入固定一秒、不会因新请求延长的合并窗口。scheduler 只在窗口到期且事务门空闲时推进一个后台 checkpoint step；诱发写回的 dirty scope 按轮转选择稳定 owner，整个 job 使用该 owner 的硬 `BACKGROUND` I/O 预算。每个维护轮次至多推进一个 invalidate/write/publish/verify/commit 状态机步骤，I/O debt 通过预算安全点延后续步。checkpoint 尝试成功或失败后的 not-before deadline 都是一个固定合并窗口，不再按 checkpoint 执行耗时放大；其实际块设备占用由 `BACKGROUND` token budget 限制。扫描请求不能饿死已经到期的 checkpoint。一次 checkpoint 会合并所有当时已捕获的 scope，但只把写入期间没有继续变化的 scope 推进到 `durable_generation`，失败则保留脏代数等待固定窗口后的预算接纳。callback 竞争失败时，已有 sidecar 发布的普通写入无需扩大成全目录扫描；确需恢复绑定时才登记协调扫描。显式 `PERSIST` set/delete、空 bank 安装、reload 和 scope retirement 同步进入 FIFO submit lane 并建立不可替换的持久化任务；这保证有序接纳，不保证调用返回时 primary 已完成回读验证。`agent_file_meta_init()` 只在调用者自己的 scope 有未提交变化时建立相同任务。按真实路径设置 metadata 时只读探测单个 inode 是否应继承自动持久属性，不以全局扫描作为正常提交前置条件，也不会在请求校验失败前修改 metadata。

查询缓存代数同样按 workflow scope 维护；某域的普通变化只使该域缓存失效，SYSTEM 对象变化才使所有已存在 scope 的缓存代数失效。这样低权限 Agent 的微小写入既不能逐次触发全 bank I/O，也不能借另一 workflow 的脏状态迫使其同步提交；查询仍通过 sidecar 立即看到本域已经提交到文件系统的数据。

字符串 selector 支持两组字段名：兼容字段 `project/run_id/stage/kind/status`，以及通用字段 `namespace/object_id/label/type/state`。内核按这些字段执行同一套查询、状态更新、依赖查询和预取提示生成。科研平台中的设定的模拟流程数据由用户态 orchestrator 写入；内核不会预置项目名、run id 或固定阶段顺序。该流程的用户态环节包括数据准备、比对处理、结果分析、报告生成和归档交付。

对象依赖关系不再由内核固定解释某几个阶段名称。用户态可以通过 `dependency_update` 显式注册 `source/target/namespace/run_id/relation/summary` 形式的通用依赖记录，也可以继续通过 `agent_file_meta_set()` 写入对象 label 和 `dependency_mask` 作为紧凑兼容输入。每条显式记录包含源对象 label、目标对象 label、关系、namespace、run_id 和摘要；`dependency_query` 可用 `label=...;namespace=...;run_id=...` 缩小查询范围，文件查询后的预取提示和 provenance 也优先读取这些通用记录。旧的 `dependency_mask` 不复制进全局依赖表；没有匹配显式边时，消费者直接在同 scope、namespace、workflow 和 run 的文件记录上解释位图。这既保留兼容 ABI，也避免文件拓扑变化触发全局派生重建。

`update_mask` 用于精确更新字段，也允许清空字段。例如只清空 status 时传入 `AGENT_FILE_META_UPDATE_STATUS` 并让 `status` 为空字符串。

`flags` 支持：

| flag | 含义 |
| --- | --- |
| `AGENT_FILE_META_F_DELETE` | 删除所有非空 selector 共同指向的元数据；fid、physical/logical path 和完整 inode identity 必须收敛到同一记录 |
| `AGENT_FILE_META_F_PERSIST` | 记录纳入持久快照；显式 set/delete 同步提交，普通 VFS 自动变化按 scope 合并后台写回 |
| `AGENT_FILE_META_F_AUTOSCAN` | 由根目录自动扫描维护的元数据 |

`dev + inum + incarnation` 是不可变身份 guard，不是可写更新值。多个 selector 指向不同记录时返回 `AGENT_STATUS_CONFLICT`；没有 selector 命中时返回 `AGENT_STATUS_NOT_FOUND`，失败请求不会先改写或删除其他对象。

根目录自动扫描由 Agent 文件元数据服务启用。timer tick 安排周期扫描；文件系统 hook 能局部更新时直接发布内存记录或 inode sidecar 并登记分域写回，只有绑定缺失等无法局部协调的状态才追加扫描请求。扫描请求采用非滑动合并：首次启用可立即执行，之后不能提前已有 cooldown；扫描中到达的任意数量请求最多排队一轮。调度器空隙调用 `agent_background_maintain()`，先给到期写回一次独立机会，再按每 tick 最多 16 个目录项推进扫描。完整扫描成功或失败后都至少休息 20 tick，并按本轮耗时的四倍延长休息期；metadata 满表后的未绑定文件微写因此不能让扫描完成即重启。扫描发现的新真实文件会生成 `AUTOSCAN | PERSIST` 元数据，文件删除后对应自动元数据会被清理。当前扫描范围是 uCore 根目录短文件名，不承诺多级目录递归或全文内容索引。

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
| `scanned_records` | 本次检查了多少条候选记录 |
| `plan` | 查询计划，0 为扫描，1/2/3 分别为 status/stage/kind 索引 |
| `index_bucket` | 命中的索引桶；扫描路径为 -1 |
| `candidate_records` | 本次候选记录数量，和 `scanned_records` 一起用于解释索引收益 |
| `query_ticks` | 查询内部 tick 差值 |
| `plan_reason` | 查询计划原因 flags，例如强制扫描、status 索引、stage 索引、kind 索引、查询缓存命中或没有可用索引键 |
| `fs_generation` | 查询时当前 workflow scope（包含其可见 SYSTEM 对象）的缓存代数 |

每条 hit 还返回 `dev`、`inum`、`incarnation`、`size` 和 `fs_generation`，用于说明查询结果来自同一代真实文件绑定和当前元数据版本。

查询计划常量：

| 常量 | 含义 |
| --- | --- |
| `AGENT_FILE_QUERY_PLAN_SCAN` | 扫描全部可用元数据槽 |
| `AGENT_FILE_QUERY_PLAN_STATUS_INDEX` | 使用 status 索引链 |
| `AGENT_FILE_QUERY_PLAN_STAGE_INDEX` | 使用 stage 索引链 |
| `AGENT_FILE_QUERY_PLAN_KIND_INDEX` | 使用 kind 索引链 |

`plan_reason` 使用位标记说明为什么选择该计划：`FORCED_SCAN` 表示调用者强制扫描；`INDEX_OFF` 表示未请求索引；`STATUS_INDEX`、`STAGE_INDEX`、`KIND_INDEX` 表示对应索引参与计划；`NO_INDEX_KEY` 表示请求了索引但查询条件没有 status、stage 或 kind；`CACHE_HIT` 表示本次非强制扫描命中查询直接复用了同一 scope cache generation 下的结果缓存。自动扫描可以和其他 scope 的缓存命中并行；扫描真正改变调用 scope 或 SYSTEM 记录时，相应 cache generation 会使旧结果失效。显式 `AGENT_FILE_QUERY_SCAN` 不进入缓存。

### 文件编辑租约 ABI

文件编辑租约用于处理同一 workflow scope 内两个 Agent 同时希望修改同一真实文件的情况。内核用 `scope_id + dev + inum + incarnation` 识别文件，不依赖用户态传入的逻辑路径或全局裸租约号。普通进程不能申请租约；Agent 需要具备内容读取、工件写入、元数据写入或编排能力之一，同时普通 VFS 路径仍要求当前 workflow 凭据具备相应文件能力。租约存在时，真实 `write`、`O_TRUNC` 和 `unlink` 路径会先按当前凭据重新鉴权，再检查当前进程是否是本 scope 的租约持有者；另一个 scope 不能查询、提交、终止或复用该租约。

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

### 文件预取提示 ABI

文件查询成功写入 Context 后，内核会根据命中的 source 文件、同一 namespace/workflow/run 的对象标签依赖和当前索引信息，生成 metadata 预取提示。查询结果缓存连同精确 hit slot 一起保存；预取先验证 slot 身份，再在当前 scope 的依赖配额内收集选择器。label hash 只作预筛，label、namespace、run 和 workflow 仍作精确比较；目标通过一次文件表扫描和槽位位图全局去重，单次查询最多发布 `AGENT_FILE_PREFETCH_MAX_HINTS = 8` 个唯一目标。相关依赖、文件表、提示总线和 handoff 遍历统一计入 metadata 工作预算。提示首先保存在当前 Agent 的 PCB 中，容量为 8。同时，带有可信 span 的提示会写入物理容量为 `AGENT_FILE_PREFETCH_SPAN_MAX = 32` 的 scope 分区提示表；最多 4 个 workflow 各保留 8 条，查询还必须同时匹配调用者 scope 和内核私有 span owner。它只提示“后续可能需要哪些文件 metadata”，预取提示本身不读取文件内容，也不替代 `agent_file_query()`；需要内容级证据时使用 `read_file_digest` 工具读取短预览和内容指纹。

当 Agent 通过 message 事件唤醒另一个 Agent 时，内核会把发送者当前可见的 metadata 预取提示复制到接收者的预取提示 ring，并给复制后的提示增加 `AGENT_FILE_PREFETCH_REASON_HANDOFF`。这样接收者可以直接调用 `agent_file_prefetch_snapshot()` 得到上游 Agent 的下一步候选，不需要从消息文本中解析策略字段。复制后的提示也会写入 span 预取提示总线，保留 source pid、target pid 和 span id，便于同一因果链上的 Agent 用统一接口查询本轮协作中的候选工件。依赖/FID 查找、总线选择和审计预留先纳入 metadata 工作预算；最终只在重新解析稳定接收端点成功后，以固定上界、不可调度的短提交同时发布本地 hint、span bus 和观测记录。

`agent_file_prefetch_snapshot(hints, max)` 返回当前可见提示数量，普通进程调用返回 `-1`，无 `META_READ` 能力的 Agent 返回 `AGENT_STATUS_DENIED`。`max=0` 时只返回数量，不复制记录；`max>0` 时按产生顺序复制最多 `max` 条。

`agent_file_prefetch_span_snapshot(hints, max)` 返回当前 Agent 的 `current_span_id` 对应的 scope 内提示数量或记录。它只返回 `scope_id + public span_id + private span_owner` 全部匹配的提示；仅猜中公开 span 数字不能读取其他 workflow 或另一条已回收链。当前 Agent 尚未进入任何可信 span 时返回 0。普通进程调用返回 `-1`，无 `META_READ` 能力的 Agent 返回 `AGENT_STATUS_DENIED`。`max=0` 时只返回匹配数量；`max>0` 时按 sequence 复制最多 `max` 条。

`struct agent_file_prefetch_hint` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `sequence` | 预取提示自身的递增序号 |
| `source_sequence` | 触发本提示的 Context sequence |
| `span_id` | 触发本提示的因果链 span |
| `reason` | 生成原因 flags |
| `score` | 内核给出的简单排序分数 |
| `tick` | 生成提示时的 tick |
| `fs_generation` | 生成提示时的文件元数据代数 |
| `fid` | 建议后续关注的目标元数据 ID |
| `source_fid` | 触发提示的源元数据 ID |
| `source_pid` | 触发提示的 Agent pid |
| `target_pid` | 当前应该消费提示的 Agent pid |
| `plan` | 建议使用的查询计划，当前为 stage 索引 |
| `candidate_records` | 目标 stage 在同一 run 下的候选记录数 |
| `total_hits` | 当前可用于提示的目标记录数量 |
| `hit` | 目标文件的 `agent_file_hit` 快照 |

`reason` 使用以下 flags：

| flag | 含义 |
| --- | --- |
| `AGENT_FILE_PREFETCH_REASON_DEPENDENCY` | 由对象标签依赖关系产生 |
| `AGENT_FILE_PREFETCH_REASON_SAME_RUN` | source 和 target 属于同一 project/workflow/run |
| `AGENT_FILE_PREFETCH_REASON_PENDING` | target 当前状态为 pending |
| `AGENT_FILE_PREFETCH_REASON_STAGE_INDEX` | target 可通过 stage 索引定位 |
| `AGENT_FILE_PREFETCH_REASON_HANDOFF` | 由另一个 Agent 的 message 事件交接而来 |
| `AGENT_FILE_PREFETCH_REASON_SPAN_BUS` | 已写入同 scope/private-owner span 的分区提示表 |

## 任务五 Agent Loop ABI

Agent Loop 使用每 Agent 16 槽 FIFO 事件队列。可归因外部事件合计上限为 12；directed IPC 与 attributed system notification 各自上限为 8；同一 stable source 跨两类合计上限为 4。directed 投递达到任一 admission 边界时返回 `AGENT_STATUS_NO_SPACE`，不会覆盖旧事件。attributed 广播遇到某个目标超配额或总队列已满时只对该目标记 drop 并继续扫描，不把 `NO_SPACE` 透传给已经提交状态的源操作。只有显式 `KERNEL` origin（当前包括 heartbeat TIMER 等内核直接产生的事件）可以越过 external=12 的 admission 边界，继续使用总容量中保留的至少 4 个名额；它仍受 16 槽总容量约束。每个 Agent 最多注册 8 条 watch。相同 `event_type + filter` 会替换原 watch，`agent_unwatch()` 可删除匹配 watch 或清空全部 watch。有限 timeout 的 `agent_wait()` 会进入睡眠，由事件入队、heartbeat 到期、deadline 到期或 wait cancel 令牌唤醒；`agent_info.wait_loop_count` 用于观察该路径没有反复轮询。

所有 runnable 线程先进入所属资源域的私有队列；外层 active-domain FIFO 中每个非空域至多有一个节点。每次调度只从队首域取一个线程，域仍非空时把它放回外层队尾，因此线程数量不能换取更多跨域 dispatch。纯普通域在本域按 FIFO 选择；本域存在 Agent 时才读取 Agent 状态并允许 orchestrator 配置 weight、priority 和 budget。角色权重、配置优先级、事件队列、等待状态、timeout deadline、heartbeat 到期、等待时长、虚拟运行量和预算使用量只影响该域内分数。

每个域独立维护不可配置的 Agent/score burst。当选中域内同时有 Agent 和普通线程时，连续调度 Agent 达到 `AGENT_SCHED_MAX_AGENT_BURST` 次后必须选择本域普通 FIFO 候选；连续按分值绕过队首达到同一上限后必须选择本域 FIFO 队首。域内任一线程运行后，外层仍轮转到下一个 active 域。`agentsched_ucore` 验证既有角色权重、受权配置、事件优先、原因记录和域内普通进展；`threadresource_ucore` 验证多线程 PUBLIC 域不能阻断另一资源域完成 512 次让出。

`agent_sched_snapshot(records, max)` 返回当前 Agent 最近最多 16 次被调度时的原因记录。`max=0` 时不复制记录，只返回当前可见记录数。普通进程调用返回 `-1`。

`agent_trace_snapshot(records, max)` 返回当前 Agent 的运行轨迹短记录。它会把 Context Path 中最近最多 128 条摘要记录和调度器中最近最多 16 条调度原因记录按 tick 合并，最多返回 `AGENT_TRACE_MAX_RECORDS = 144` 条。`max=0` 时不复制记录，只返回当前可见记录数。普通进程调用返回 `-1`。该接口只整理当前 Agent 自己已经拥有的 Context 和调度数据，不创建新的全局日志，也不改变事件队列。

`agent_span_trace_snapshot(records, max)` 返回当前 Agent 所在可信 span 的系统级短记录。它读取共享物理表，但只返回调用者 scope、`current_span_id` 和内核私有 `current_span_owner` 全部匹配的记录；当前 Agent 尚未进入 span 时返回 0。普通进程调用返回 `-1`；缺少 `AUDIT_WRITE` 能力的 Agent 返回 `AGENT_STATUS_DENIED`；`max=0` 返回匹配数量；`max>0` 时按 sequence 顺序复制最多 `max` 条。该接口不接受调用者传入 span id，公开 span 字段也不构成跨 scope 或跨 owner 的查询票据。

该接口按调用者 scope 的 sequence 索引单遍检查，`max=0` 的计数模式不会绕过工作计费。内核在扫描前按每 16 条候选记录换算预算，按不超过一个 `kernel_work` 量子的批次分段预付，并在让出后重计和补足增长差额。该机制不增加公共 ABI，也不公开内部扫描量。

`agent_audit_snapshot(records, max)` 返回调用者 workflow scope 的系统级 Agent 审计短记录。内核物理表固定为 `AGENT_AUDIT_MAX_RECORDS = 512`，按最多 4 个 admitted workflow 划分为每 scope 128 条保证；每个 scope 再独立划分 general/low 64 条和 protected/high 64 条。low 对每个 stable principal 最多保留 16 条；high 按每 scope 8 个保留进程份额均分为每 active principal 8 条。Context、事件入队/消费、调度、预取和用户手动记录等遥测始终进入 low；只有工具或 syscall 成功后由内核确认的特权状态效果才进入 high，用户 flags、公开 span 或委派来的 cause 不能把遥测提升为 protected evidence。high 满时只滚动当前 principal 自己的 8 条，或回收已经退出/inactive principal 的最旧记录，绝不淘汰另一 active principal 的 protected evidence；inactive 历史仍是由 `dropped_records` 明示的有界窗口。低权限主体持续制造遥测也不能占用其他 workflow 的 128 条份额。

每个 scope 维护自己的逻辑 hash 链。物理 `sequence` 仍为系统单调序号，所以同一 scope 可见 sequence 可以因其他 scope 写入而跳号；low/high 与 per-principal 独立滚动还会使当前 128 条窗口缺少早期前驱。每条新记录的 `prev_hash` 指向本 scope 上一条逻辑记录的 hash，即使该前驱已被淘汰。因而只在两条相邻可见记录的 sequence/prev-hash 连续时逐条核验；`dropped_records = total_records - visible_records` 说明窗口外记录数量，sequence/hash 间隙不等同于链损坏。`ledger_hash` 始终是本 scope 最新逻辑记录的 hash。

每个 scope 维护 sequence 与 `(tick, sequence)` 两个 128 槽有序索引。记录覆盖统一先从两份索引 unlink 旧槽，再 publish 新槽；因此 ledger 的 `visible_records`、`oldest_sequence` 和 `latest_sequence` 都可在 O(1) 时间得到，不需要扫描物理 512 槽。需要复制或过滤的审计查询沿 sequence 索引单遍推进。

普通进程调用返回 `-1`；不具备 `ORCHESTRATE` 的 Agent 返回 `AGENT_STATUS_DENIED`；`max=0` 返回本 scope 当前可见记录数；`max>0` 时按 sequence 复制记录。该接口是内存态观测能力，不写磁盘，也不替代完整 `context_detail()`。

`agent_audit_query(filter, records, max)` 在调用者 scope 的同一组可见短记录上执行过滤查询。`filter=NULL` 表示不过滤；`filter->flags` 决定哪些字段参与匹配，可按 `start_sequence`、`span_id`、`kind`、`pid`、`source_pid`、`target_pid`、`role`、`tool_id`、`event_type` 和 `status` 过滤。filter 只能缩小 scope/owner 裁剪后的集合，不能用公开 PID 或 span 扩大可见范围。`max=0` 返回匹配数量，不复制记录；`max>0` 时复制最多 `max` 条匹配记录。权限和错误语义与 `agent_audit_snapshot()` 相同。

`agent_ledger_snapshot(summary)` 返回当前 workflow scope 的运行账本摘要。它不复制审计明细，只返回 `oldest_sequence`、`latest_sequence`、`visible_records`、`total_records`、`dropped_records`、`ledger_hash`、Context/event/sched/prefetch 分类计数、`timeline_total` 和 `observe_epoch`。其中 `ledger_hash` 等于本 scope 最新逻辑审计记录的 `record_hash`，而不是物理 512 槽中其他 scope 的最后记录。普通进程调用返回 `-1`，非 orchestrator Agent 返回 `AGENT_STATUS_DENIED`，空指针返回 `AGENT_STATUS_BAD_PARAM`。该接口适合状态页面或示例脚本确认当前 scope 的稀疏可见窗口仍锚定在同一条内核维护的运行事实链。

`agent_timeline_snapshot(records, max)` 返回统一 timeline 记录。该接口把当前 Agent 可见的四类短记录规范化为 `struct agent_timeline_record`：

- 当前 Agent 的 Context Path 摘要；
- 当前 Agent 最近 16 次调度原因；
- 当前 Agent 可见的审计记录：orchestrator 可见本 workflow scope 的审计，其他具备 `AUDIT_WRITE` 的 Agent 只可见同 scope 的当前可信 span；
- 当前 Agent 自己的 metadata 预取提示。

普通进程调用返回 `-1`。`max=0` 返回当前可见记录总数，不复制记录；`max>0` 时按 tick 输出最多 `max` 条。共享物理审计表中的记录按系统 sequence 排序，导出前先按 scope/owner 裁剪，再按 tick 选择，避免多 Agent 并发记录导致页面时间线乱序。该接口不替代 `context_detail()`，也不保存完整 raw 请求/响应；它面向结果页面和研究平台运行详情。

`agent_timeline_query(filter, records, max)` 在同一组当前可见记录上执行内核侧过滤。`filter=NULL` 表示不过滤，语义等同于 `agent_timeline_snapshot()`。`max=0` 返回匹配数量，不复制记录；`max>0` 时按 tick 复制最多 `max` 条匹配记录。普通进程调用返回 `-1`。过滤只能缩小当前 Agent 已经有权看到的记录集合：普通 Agent 不能通过 filter 读取其他 scope/span 的审计记录，非 orchestrator Agent 也不能把 source mask 设为 audit 后获得整个 scope 的审计。

timeline 将 Context、Sched、Audit 和 Prefetch 四个有序来源按 `(tick, source, sequence)` 四路归并；Audit 来源使用当前 scope 的 `(tick, sequence)` 索引。每个来源记录至多被检查一次，需要过滤扫描的计数查询与复制查询采用相同预算规则。预算让出在扫描开始前完成，`agent_timeline_wait()` 不会因本机制在“确认当前无匹配记录”和“登记等待者”之间新增调度窗口。

`AGENT_TIMELINE_FILTER_AFTER_CURSOR` 用于增量读取。调用者把已经处理过的最后一条 record 的 `tick`、`source` 和 `sequence` 填入 `after_tick`、`after_source` 和 `after_sequence`，内核只返回严格晚于该游标的记录。比较顺序与 timeline 导出顺序一致：先比较 `tick`，同一 tick 下按 `source` 顺序比较，再比较来源内部 `sequence`。它比只使用 `start_tick` 更适合页面刷新，因为同一个 tick 中可能存在多条不同来源记录。

`agent_timeline_wait(filter, timeout_ticks)` 使用同一套 timeline filter，但只返回匹配记录数，不复制记录。若当前已经有匹配记录，接口立即返回数量；若没有匹配记录，当前 Agent 保存 filter 并进入睡眠。Context、调度、审计或预取提示写入时，内核把新事实转换成 `agent_timeline_record`，并在内核里用等待者保存的完整 filter 判断是否匹配；例如只等待 Context 的 Agent 不会因为 Audit 记录写入而增加 timeline wait 唤醒计数，只等待 TIMER 事件的 Agent 也不会被 MESSAGE 审计记录唤醒。`timeout_ticks >= 0` 表示有限等待，超时返回 `AGENT_STATUS_TIMEOUT`；`timeout_ticks == -1` 表示无限等待。返回正数后，调用者用同一个 filter 调用 `agent_timeline_query()` 读取记录。普通进程调用返回 `-1`，非法 filter flags 返回 `AGENT_STATUS_BAD_PARAM`。该接口用于状态页面或 Agent worker 的事件驱动刷新，避免用户态循环调用 `agent_timeline_query()`。

`agent_timeline_read(filter, records, max, timeout_ticks)` 使用与 `agent_timeline_wait()` 相同的等待和唤醒规则，但在匹配记录出现后立即把最多 `max` 条记录复制到 `records`。如果当前已经有匹配记录，它不睡眠并直接复制；如果当前没有匹配记录，它先睡眠等待，醒来后在同一次 syscall 中复制记录。`max=0` 时只返回匹配数量，不复制记录；`max>AGENT_TIMELINE_MAX_RECORDS` 返回 `AGENT_STATUS_BAD_PARAM`；坏输出指针在睡眠前返回 `-1`。这个接口用于最终 Web UI 或 Agent worker 的热路径，避免 `agent_timeline_wait()` 返回后再调用 `agent_timeline_query()` 的第二次 syscall 和中间状态变化。

`struct agent_timeline_record` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `source` | 规范化来源：`CONTEXT`、`SCHED`、`AUDIT`、`PREFETCH` |
| `kind` | 来源内部类型；Context/Sched 使用 trace kind，Audit 使用 audit kind，Prefetch 使用 query plan |
| `tick` | 产生该条记录时的 tick 计数值 |
| `sequence` | 来源内部序号：Context sequence、dispatch count、audit sequence 或 prefetch sequence |
| `cause_sequence` / `span_id` | 因果字段 |
| `pid` / `tid` | 产生记录的进程和线程；无线程信息时为 0 |
| `source_pid` / `target_pid` | 事件或提示的来源与目标；单 Agent 记录中通常等于 `pid` |
| `role` / `loop_state` | 记录产生时的 Agent 角色和 Loop 状态 |
| `tool_id` / `event_type` / `status` | 工具、事件类型和状态码 |
| `value0` / `value1` / `value2` | 来源相关的数值槽，例如调度分数、事件 ID、source fid、target fid、候选记录数 |
| `flags` | Context flags、调度 reason flags、审计 flags 或预取 reason flags |
| `text` | 32 字节短摘要 |

`struct agent_timeline_filter` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `flags` | 使用 `AGENT_TIMELINE_FILTER_*` 位选择参与匹配的字段 |
| `source_mask` | 配合 `AGENT_TIMELINE_FILTER_SOURCE_MASK`，可选择 Context、Sched、Audit、Prefetch 来源 |
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

`agent_provenance_snapshot(edges, max)` 返回当前 Agent 可见的因果边。`max=0` 返回匹配到的边数量，不复制记录；`max>0` 时复制最多 `max` 条。普通进程调用返回 `-1`。该接口不扩大权限：每个 Agent 都能看到自己的 Context 因果边和本地预取提示边；审计边遵循与 timeline 相同的可见规则，orchestrator 可见本 scope 审计边，其他具备审计能力的 Agent 只可见当前可信 span 内的审计边。跨 Agent cause 的 source pid/control 来自内核 sidecar，不把 source 本地 sequence 误解释为 target 本地 Context。

provenance 对 Context、scope audit 和本地 prefetch 各执行一次有界扫描；被过滤掉的候选仍计入 kernel-work 预算，`max=0` 不形成免计费旁路。

`struct agent_provenance_edge` 字段如下：

| 字段 | 含义 |
| --- | --- |
| `kind` | 边来源：`CONTEXT`、`AUDIT` 或 `PREFETCH` |
| `source_type` / `target_type` | 源节点和目标节点类型：Context、Audit 或 Prefetch |
| `source_sequence` / `target_sequence` | 源节点和目标节点在对应来源中的 sequence |
| `span_id` | 该因果边所属 span |
| `tick` | 目标节点产生时的 tick |
| `source_pid` / `target_pid` | 源 Agent 和目标 Agent；单 Agent Context 边通常相同 |
| `role` | 目标记录产生时的 Agent 角色 |
| `tool_id` / `event_type` / `status` | 关联工具、事件类型和状态码 |
| `value0` / `value1` / `value2` | 来源相关数值，例如文件 fid、候选记录数或线程 id |
| `flags` | Context flags、audit flags 或 prefetch reason flags |
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
| `kind` | 记录来源：Context 追加、事件入队、事件消费、调度 dispatch 或预取提示交接 |
| `tick` | 记录产生时的 tick |
| `prev_hash` | 追加本条审计记录前的同 scope 逻辑账本链尾 hash |
| `record_hash` | 由 prev_hash、sequence、tick、cause/span、角色、来源、状态、数值槽和短文本计算得到的本条记录 hash |
| `pid` / `source_pid` / `target_pid` | 产生记录的 Agent、事件来源和事件目标 |
| `agent_id` / `role` / `loop_state` | 记录产生时的 Agent 身份、角色和 Loop 状态 |
| `tool_id` / `event_type` / `status` | 工具 ID、事件类型和状态码 |
| `cause_sequence` / `span_id` | 对应 Context 或事件的公开因果字段；可信来源还由内核私有 source control/span owner sidecar 约束 |
| `value0` / `value1` / `value2` | 按来源解释的数值槽；Context 记录保留工具结果数值槽，事件记录保留事件 ID/corr ID/目标 pid，调度记录保留分数和队列信息，预取交接记录中分别是 source sequence、source fid、target fid |
| `flags` | Context record flags、调度 reason flags 或预取 reason flags |
| `text` | 32 字节短摘要，例如工具结果、事件 payload、`sched` 或预取目标 stage |

当前 `kind` 取值如下：

| 取值 | 含义 |
| --- | --- |
| `AGENT_AUDIT_KIND_CONTEXT` | Context 追加 |
| `AGENT_AUDIT_KIND_EVENT_ENQUEUE` | 事件入队 |
| `AGENT_AUDIT_KIND_EVENT_CONSUME` | 事件消费 |
| `AGENT_AUDIT_KIND_SCHED` | Agent 调度 dispatch |
| `AGENT_AUDIT_KIND_PREFETCH` | message 事件触发的预取提示交接 |

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
| `version` | 当前为 `AGENT_LEDGER_VERSION = 1` |
| `oldest_sequence` / `latest_sequence` | 当前 scope 可见审计窗口的最早和最新系统 sequence；中间允许因跨 scope 写入和分区滚动而稀疏 |
| `visible_records` | 当前 scope 可复制的审计记录数，最多 128 |
| `total_records` | 当前 scope 建立后累计写入的审计记录数 |
| `dropped_records` | 当前 scope 逻辑记录中已不在可见窗口的数量，即 `total_records - visible_records` |
| `ledger_hash` | 当前 scope 逻辑审计链尾 hash，等于该 scope 最新记录的 `record_hash` |
| `context_records` / `event_records` / `sched_records` / `prefetch_records` | 按来源累计的记录数 |
| `timeline_total` | 可作为当前 scope timeline 候选来源的记录总量 |
| `observe_epoch` | 观测 epoch，Context、审计、调度或预取提示写入时递增 |

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

`agent_heartbeat_stop()` 是用户态便利 wrapper，内部调用 `agent_heartbeat(0)`。heartbeat 到期产生 `AGENT_EVENT_TIMER` 时仍需匹配 TIMER watch；删除 TIMER watch 后，heartbeat 不会投递可消费 TIMER 事件。

`agent_wait_cancel(pid, reason)` 是 Agent-only 控制接口。消息数据面与等待控制面彼此独立：调用者必须具备 `AGENT_CAP_WAIT_CANCEL`，目标必须由调用者直接创建，并且双方必须仍属于同一 active workflow scope。创建时内核为每个 Agent 签发不向用户态暴露的 64 位 control id，并把创建者的 control id 绑定为目标 controller；授权不读取 role、PID、父指针或可复用 PCB 地址，controller 退出后旧 id 也不会转授给复用该槽的新进程。内核给合法目标写入一次性取消令牌并唤醒目标；如果目标已经在 `agent_wait()` 中睡眠，会立即返回 `AGENT_STATUS_CANCELLED`；如果取消令牌先到达，目标下一次 `agent_wait()` 会立即返回。返回事件的 payload 保存短 reason，cause/span 及其私有来源身份由内核继承。普通进程调用返回 `-1`，能力不足、scope 不同或目标不受调用者控制返回 `AGENT_STATUS_DENIED`，目标不存在或正在退出返回 `AGENT_STATUS_NOT_FOUND`，目标已有未消费取消令牌时返回 `AGENT_STATUS_DUPLICATE`。

## 上下文路径接口：Context Path

| 接口 | 行为 |
| --- | --- |
| `context_push(record)` | 追加手动节点，内核分配新的 sequence |
| `context_query(start_sequence, out, max)` | 从 `start_sequence` 起按时间顺序复制仍可见记录；`start_sequence=0` 表示从最早可见记录开始 |
| `context_snapshot(header, records, max)` | 一次返回 header 和按时间顺序排列的可见 records |
| `context_detail(sequence, out)` | 返回最近 128 条完整详情中指定 sequence 对应的 `agent_op`、`agent_result` 和 flags |
| `context_rollback(sequence)` | 回滚到仍可见 sequence；不存在时返回 `AGENT_STATUS_NOT_FOUND` |
| `context_clear()` | 清空记录、计数和 latest response |

`struct agent_context_record` 保存工具 ID、状态码、sequence、request_id、cause_sequence、span_id、数值槽、tick、flags、`prev_hash`、`record_hash`，以及 16 字节 payload/result 短文本摘要；工具名称可通过 `agent_tool_list()` 按 `tool_id` 解释。它不是完整 raw 请求/响应日志，不保存全部参数键名、参数类型或完整长文本。最近 128 条完整详情保存在内核 PCB 的 detail ring 中，通过 `context_detail()` 查询，不放在用户 Context 页内。超过 128 条记录时，Context Path 按 FIFO 覆盖旧记录，并更新 `oldest_sequence`、`latest_sequence` 和 `dropped_records`。

Context v6 增加轻量因果链字段和完整性链字段：

| 字段 | 含义 |
| --- | --- |
| `cause_sequence` | 当前记录由哪条前序 Context record 触发；0 表示本链路根节点 |
| `span_id` | 当前链路 ID，用于把工具调用、事件投递和事件消费串起来 |
| `prev_hash` | 追加本条记录前的 Context 链尾 hash；第一条记录为 0 |
| `record_hash` | 由 prev_hash、sequence、cause/span、工具 ID、状态、数值槽和短文本摘要计算得到的记录 hash |

`struct agent_context_header.latest_record_hash` 暴露当前 Context 链尾 hash。`context_rollback(sequence)` 会把链尾 hash 回滚到目标记录的 `record_hash`，`context_clear()` 会把链尾 hash 清零。这个字段用于表达当前可见路径的顺序关系由内核维护，不依赖用户态日志拼接。

内核写入自动工具记录时，会使用当前 Agent 的 `current_cause_sequence` 和 `current_span_id`，并在 PCB 私有 sidecar 中同时保存真实 source pid/control id 与 span owner；写入完成后，当前 cause 更新为新记录的 sequence。`context_push(record)` 只能提交本地手动内容，调用者必须把 `cause_sequence` 和 `span_id` 都设为 0，否则返回 `AGENT_STATUS_BAD_PARAM`；内核再把该记录接到当前可信链，防止用户伪造跨 Agent cause 或把公开 span 当作授权票据。跨 Agent 消息或文件事件只有在同 scope 路由/对象检查通过后才携带由内核认证的 cause/span，目标 Agent 在 `agent_wait()` 消费事件后继承其公开 span 和私有 owner/source，后续工具调用继续写入同一可信链。该机制用于内核内可信审计和示例追踪，不等同于持久化密码学保证。

Context record flags：

| flag | 含义 |
| --- | --- |
| `AGENT_CONTEXT_RECORD_F_SYSTEM` | 内核自动工具或系统事件记录 |
| `AGENT_CONTEXT_RECORD_F_MANUAL` | `context_push()` 手动记录 |
| `AGENT_CONTEXT_RECORD_F_TRUNCATED` | payload 或 result 短摘要发生截断 |
