# 赛题要求追踪表

本文按 ISO/IEC/IEEE 29148 的需求可追踪思想裁剪编写。每条需求都给出来源、当前状态、实现位置、验证证据和剩余缺口。

## 状态定义

| 状态 | 含义 |
| --- | --- |
| 已验证 | 已有实现和用户态测试输出支持 |
| 已验证，保留测试缺口 | 核心机制已有运行证据，但表中明确列出的极端组合仍缺独立断言 |
| 已实现，待本次回归 | 机制和对应回归断言已进入源码，但本文不在新一轮 QEMU 输出产生前宣称通过 |
| 部分实现 | 有基础能力，但未覆盖赛题完整语义 |

## 总体交付要求

| ID | 赛题要求 | 状态 | 实现/材料 | 验证证据 |
| --- | --- | --- | --- | --- |
| G-1 | 在教学操作系统内核中实现 Agent-OS 功能模块 | 已验证 | `os/agent.c`、`os/agent.h`、`os/proc.c` | `agentfinal_ucore`、`agentbench_ucore`、`labdemo_ucore`、`agentsecurity_ucore` |
| G-2 | 系统可在 QEMU 上运行 | 已验证 | `Makefile`、`nfs/fs.img` | `scripts/run-agent-tests.sh`、`scripts/run-fs-enospc-tests.sh`、`scripts/run-proc-reap-tests.sh`、`scripts/run-syscall-fairness-tests.sh`、`scripts/run-file-resource-tests.sh`、`scripts/run-thread-resource-tests.sh`；本次线程专项和三组相关回归均通过 |
| G-3 | 提供内核代码 | 已验证 | `os/` | Git 仓库源码 |
| G-4 | 提供用户态测试程序 | 已验证 | `agentfinal_ucore`、`agentfs_ucore`、`agentscan_ucore`、`agentloop_ucore`、`agentsched_ucore`、`agentconflict_ucore`、`agentllm_ucore`、`agentbench_ucore`、`labbench_ucore`、`labdemo_ucore`、`agentsecurity_ucore`、`agenttrust_ucore`、`agentvfs_ucore`、`agentscope_ucore`、`iobudget_ucore`、`usersafety_ucore`、`fsenospc_ucore`、`fsquota_ucore`、`fspquota_ucore`、`procreap_ucore`、`procreap_agent_ucore`、`syscallfair_ucore`、`fileresource_ucore`、`threadresource_ucore` | 当前 pipe 安全主体委派改动后完整 16 项 Agent 脚本通过，墙钟约 359.4s；此前线程资源、单独 `agentsched_ucore` 和双目标进程回收/syscall 公平性/filepool 脚本的通过结果继续按历史轮保留，证据边界见 [verification.md](verification.md) 和 [test-record.md](test-record.md) |
| G-5 | 提供综合示例场景 | 已验证 | `user/src/labdemo_ucore.c` | `labdemo_ucore: passed` |
| G-6 | 提供设计文档和运行说明 | 已验证 | [../../README.md](../../README.md)、[design.md](design.md)、[scenario-script.md](scenario-script.md) | 本文档、[verification.md](verification.md) |
| G-7 | 保留代表性的 uCore 基础 syscall 兼容性 | 已验证 | `SYS_trace`、`SYS_mailread`、`SYS_mailwrite` | `ch3_trace` 输出 `Test trace OK!`；`agentsecurity_ucore: mail_basic=1` |

## 内核安全与稳定性机制

这些条目不是对个别测试程序的特判，而是普通进程和 Agent 共用的内核机制。普通 uCore 对照目标同步保留通用的输入检查、等待队列、文件系统失败处理、内核工作预算、内核栈防护和进程生命周期机制；AgentOS 目标在此基础上增加 Agent 授权、可信映像、文件安全域、线程资源域配额和两级调度公平边界。

| ID | 安全或稳定性要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| S-1 | 所有 syscall 用户指针、字符串、长度、权限和描述符方向在解引用前统一校验，失败路径不留下半完成资源 | 已验证 | `user_range_check()`、`copyin()`、`copyout()`、`copyinstr()`、`os/syscall.c`、`os/loader.c`、`os/file.c`、`os/pipe.c` | `usersafety_ucore` 覆盖 pointer/string/exec argv/thread 范围、pipe buffer、wait/time copyout、fd 方向、文件分配回滚、信号量输入和 exec 事务，并输出 `usersafety_ucore: parent passed` |
| S-2 | 睡眠线程只挂入所属对象的定向等待队列，唤醒、取消和退出中断不会扫描或破坏其他同步队列 | 已验证 | `struct wait_queue`、`wait_queue_sleep/wake_one/wake_all/interrupt/cancel()`；mutex、semaphore、condvar、child、Agent event、timeline 和 thread-exit 各自持有队列 | `usersafety_ucore: live after directed wakeup`；`procreap_ucore: wait-queue cancellation passed`；`agentfinal_ucore` 验证 timeline filter 不匹配不唤醒 |
| S-3 | inode、inode cache 和数据块耗尽返回可恢复错误；未提交的创建/扩容状态回滚，短写准确报告已经提交的前缀 | 已验证 | `ialloc()`、`balloc()`、`bmap()`、`writei()`、`create()` 的错误返回、回滚和短写路径 | `fsenospc_ucore: inode exhaustion survived`、`inode cache exhaustion survived`、`block exhaustion survived`、`parent passed` |
| S-4 | 每线程内核栈有独立映射、不可映射 guard page 和 canary，构建期限制大栈帧、动态栈分配和无界递归 | 已验证 | `proc_mapstacks()`、`kernel_stack_reset_slot()`、`kernel_stack_check()`、`KSTACK_SIZE`、`KSTACK_GUARD_SIZE`、`scripts/check-kernel-stack-usage.py`、Makefile stack-usage 策略 | 所有内核构建自动执行静态栈检查；`scripts/run-agent-tests.sh` 和进程压力测试在 guard/canary 检查启用时通过 |
| S-5 | Agent 软调度分值不能越过资源域公平边界，也不能让同域普通任务永久饥饿 | 已验证 | `scheduler_active_domains` 外层严格轮转；`scheduler_domain_tasks[]` 域内队列；每域 `scheduler_agent_burst` / `scheduler_score_burst` 与 `AGENT_SCHED_MAX_AGENT_BURST=8`；`fetch_task()` 两级选择 | 本次改动后单独 `agentsched_ucore` 与含 `procreap_agent_ucore: adversarial-agent=1` 的双目标进程回收脚本均通过 |
| S-6 | 用户态事件注入只允许消息类型，系统事件只能由对应内核或受权专用工具产生 | 已验证 | `agent_wake()` 仅接受 `AGENT_EVENT_MESSAGE`；FILE_STATUS、TIMER、LLM_DONE 等走专用投递路径 | `agentsecurity_ucore: wake_event_authorization=1`；普通进程调用拒绝、保留系统类型返回 `AGENT_STATUS_DENIED`、非法类型返回 `AGENT_STATUS_BAD_PARAM` |
| S-7 | Agent 创建授权、workflow factory 权与业务 capability 分离；只有可信 bootstrap factory 能创建新 scope，scope 内 orchestrator 只能委派本域角色 | 已验证 | `agent_role_grant_mask`、`agent_authority_bootstrap/on_exec/check()`、`agent_create_role_proc()`、`agent_workflow_create_proc()`、动态 scope admission | `agentscope_ucore: cross_scope_isolation=1 same_scope_collaboration=1 scope_capacity_reservation=1` |
| S-8 | Agent 角色与可信、不可变且角色许可的可执行映像绑定，复制或篡改程序文件不能继承 Agent 权限 | 已验证 | `user/include/exec_policy_manifest.h`、`os/exec_policy.c`、loader 映像身份、不可变 inode 和 role mask 校验 | `agenttrust_ucore: wx_image=1`、`immutable_image=1`、`bootstrap_role_boundary=1`、`trusted_agent_exec=1`、`role_image_binding=1`、`parent passed` |
| S-9 | 普通 open/read/write/truncate/unlink 服从 capability 与精确 workflow scope；执行委派绑定 `scope + dev + inum + incarnation`，跨 scope inode fd 撤销，同 scope inode fd 每次重新授权 | 已验证 | `os/vfs_security.c`、inode VFS scope/label/checksum/incarnation、`vfs_inode_authorize()`、`vfs_proc_delegate_exec/install_image()`、scope-aware lookup、真实文件 syscall 路径 | `agentvfs_ucore` 负向检查通过；`agentscope_ucore` 的同名文件跨 scope 隔离、租约隔离和一次性 pipe fd 委派断言通过 |
| S-10 | 父进程先退出时，活子进程转为内核持有；无父退出进程由内核立即回收，不遗留孤儿僵尸 | 已验证 | `proc_orphan_children()`、`proc_child_publish_exit()`、`proc_recycle()` | `procreap_ucore: child-first=...`、`parent-first=...`、`orphan-resource=...`、`parent passed` |
| S-11 | 多线程进程退出采用协作撤销：阻塞 sibling 先从 syscall 和等待队列展开，所有内核栈静止后才释放共享文件和内存 | 已验证 | `exit_requested`、`proc_thread_exit_requested()`、`wait_queue_interrupt()`、`thread_exit_waiters`、`proc_release_resources()` | `procreap_ucore: blocked-syscall=...`、`wait-queue cancellation passed`、资源复用探针通过 |
| S-12 | wait 凭据与可执行进程槽解耦；子进程退出码发布到父进程 `child_record` 后即可回收进程槽，拒绝 wait 的父进程不能占住全局进程表 | 已验证 | `struct child_record`、`proc_child_bind/publish_exit/wait_result/unbind()`、每父进程完成表容量检查 | `procreap_ucore: detached-wait=...`、`unreaped-parent-isolated=1`；延迟 wait 仍按退出顺序取得 pid 和状态 |
| S-13 | 活进程按不可伪造资源域计费；128 槽中普通 admission 96 槽、受控保留 32 槽，最多 4 个 workflow 各保证 8 个保留槽 | 已验证 | `proc_resource_reserve/release()`、`PROC_RESOURCE_DOMAIN_LIMIT=64`、`PROC_ORDINARY_SLOTS=96`、`PROC_RESERVED_SLOTS=32`、per-scope reserved admission | fork bomb/配额归还专项通过；`agentscope_ucore: scope_capacity_reservation=1` 覆盖 4 个 scope 的独立 admission 与回收再利用 |
| S-14 | capability 必须与 active kernel-issued workflow scope 和精确对象 owner 共同命中；同能力、同名称、同 PID 或公开 span 都不能跨 scope 访问对象 | 已验证 | `VFS_SCOPE_NONE=0`、`VFS_SCOPE_SYSTEM=1`、动态 scope `>=3`，2 保留为 PUBLIC 存储 principal；metadata/dependency/action/edit/audit/prefetch/IPC 的 scope 与 stable owner 字段；metadata force reload 只替换调用者 scope | `agentscope_ucore: scope_reload_isolation=1 action_scope_isolation=1 audit_event_scope_isolation=1 lease_scope_isolation=1 ipc_scope_isolation=1` |
| S-15 | 物理 512 槽审计表按 4 个 workflow 各保留 128 条；scope 内 low/high 各 64，low principal 上限16、high active principal 上限8，其他主体不能淘汰其 protected evidence | 已验证 | scope ledger state、stable audit principal、private span owner、authority-effect 分类；high 满时仅自滚或回收 inactive principal，稀疏窗口由 `dropped_records` 说明 | `agentsecurity_ucore: trusted_span_authority=1 trusted_cause_attribution=1 audit_authority_partition=1` |
| S-16 | syscall 不能越过调度公平边界；时间片由 dispatch 建立且不能由重复 syscall 刷新，长循环按已提交工作量在安全点延迟抢占 | 已验证 | `os/kernel_work.c`、每线程 dispatch-cycle deadline/work units/pending/resumed/redispatch 状态、syscall 统一 begin/end、timer 延迟请求；console/pipe/exec/fork 分页、Agent batch 和 FD_INODE 块安全点；fork VM snapshot 调度屏障；inode 调度后短返回；truncate detach/reclaim 与 cleanup checkpoint；文件槽快照释放和 FD reservation；baseline 保持通用路径同语义 | 本次线程改动后双目标 `run-syscall-fairness-tests.sh` 已通过控制台、inode write、`O_TRUNC` last-syscall 重调度、短写/EOF observer 和 worker 退出完整性契约 |
| S-17 | 单安装、单租户的 PUBLIC 持久存储配额绑定稳定主体，不得因短命进程资源域退出、重新创建或系统重启而清零 | 已验证 | `storage_principal_id` 与 `resource_domain_id`/workflow scope 分离；当前无 uid/tenant ABI，普通进程统一绑定安装级 principal 2；superblock 记录 principal，挂载从 qmap/dinode 重建 PUBLIC block/inode 用量并回收无目录引用的孤儿；可变 SYSTEM 赞助文件在首次修改前整体转移 owner；策略/owner 格式升级并拒绝旧镜像。按认证用户/租户拆分多个稳定 principal 属后续 ABI 扩展 | `fspquota_ucore` 已在 AgentOS 与 baseline 的同一镜像各完成 crash/seed/verify 三次启动，覆盖打开后 unlink 强制断电、赞助文件接管、完整域退出、`reboot_charge_persisted`、`deletion_reuse`、`relaunch_charge_persisted` 和 `cleanup_reuse` |
| S-18 | 全局文件对象表按不可伪造资源域隔离并为内核受控工作保留容量；阻塞 syscall 的临时引用不能借关闭和复用 FD 逃离配额 | 已验证 | `file_resource_policy.h`、`proc_file_slot_reserve/release()`、`struct file.resource_domain_id/resource_reserved`、原子 `filealloc/filedup/fileclose`、`fdget/fdclose`、pipe 双 FD reservation；AgentOS 与 baseline 共用同一策略 | `make file-resource-test` 已在双目标输出 `blocking_pin_bounded`、`pipe_rollback`、`domain_limit`、`ordinary_waterline`、`reserved_progress`、`exit_reuse` 和 `parent passed`，最终输出 `[file-resource] both targets passed` |
| S-19 | 块设备 I/O 与 buffer cache 按稳定 PUBLIC/workflow/SYSTEM owner 隔离；普通压力不得消耗控制或系统保留，owner 退出与 scope retirement 不得遗留 lease/cache 状态，内核态 yield loop 不得阻断 refill/完成中断，主线程触发的进程级 fault teardown 不得绕过归因与 debt 结算 | 已验证，保留测试缺口 | `io_policy.h` ABI v3、`os/bio.c`、`os/virtio_disk.c`、`os/trap.c`、`os/proc.c`、`os/kernel_work.c`；syscall 入口 owner/class 捕获、完成事件对 owner 与设备根账本双重计费、lease/token/debt、每 bucket FIFO admission/debt queue、排队 shared grant 的 owner/class cursor 轮转；普通流量受 560/280 根 bucket 限速，SYSTEM/CONTROL 可带 device debt 前进；scheduler 每轮 idle kerneltrap 中断窗口；进程级 terminal cleanup I/O/kernel-work 上下文在 freethread/quiesce 前结算剩余 lease 与 owner/class debt，PUBLIC/NORMAL 另等 device debt，受保护 device debt 留在设备根账本由 refill 偿还；`NBUF=256` sponsor floor/cap、exclusive holder、FS atomic/quiescent checkpoint；`VFS_SCOPE_LIFECYCLE_CAP=8` 和轮转 reclaim；syscall 544 sized-prefix copyout | 最终修复后的独立 `iobudget_ucore` 新增 `scheduler_interrupt_progress=1` 和 `fault_exit_cleanup=1`，与线程退出、PUBLIC budget/attribution、cache isolation、bounded progress、CONTROL reserve 共八项具名机制标记和 `parent passed`，`elapsed=2.4s`；未动态断言 shared 排队 grant 轮转，也未覆盖 Recovery、多 workflow、SYSTEM/workflow BACKGROUND、retiring 3/8、跨 owner LRU/transient、主动 device-debt 注入或设备故障 |
| S-20 | 线程槽必须按不可伪造资源域和 ordinary/reserved 类别计费，thread bomb 不能耗尽系统保留或按线程数放大跨域 CPU 份额 | 已验证 | `thread_resource_policy.h`；进程 admission 预扣 t0；`proc_thread_resource_charge_locked()` / `proc_thread_resource_release()`；线程不可变 `resource_domain_id/resource_slot_reserved/resource_slot_charged`；普通/保留全局水位、域上限和物理池总量；编译期要求每类域上限严格小于对应全局水位；active-domain FIFO + 域内线程队列两级调度 | 本次改动后 `make thread-resource-test` 以 19/12/6/6/4 tiny policy 验证普通/保留域上限与复用、容量拒绝计数稳定、普通/保留全局水位与复用、退出退款、系统保留进展及 `domain_fairness ... victim=512 bound=576`，并输出 `parent passed` |

S-16 验证的是已识别的可扩展长路径和相应生命周期协议，不表示已经穷尽任意 syscall 实现。metadata raw I/O 与 scope reclaim 已纳入分步 I/O debt checkpoint，但这不能替代 CPU 工作量 checkpoint；目录 scanner 仍保留固定每轮条目上限。S-18 把全局 filepool 的唯一对象槽纳入资源域和系统保留机制；运行证据必须以独立双目标 `file-resource-test` 为准，不能用既有的每进程 fd 槽回滚用例替代。S-19 的 560/280 静态 envelope 约束配置总和，而非 SYSTEM/CONTROL 带债前进时的运行时硬总上限。S-20 的 `capacity_reject_stable` 只证明容量拒绝不污染线程计数，不声称动态注入了用户栈或 trapframe 映射故障；本次线程改动后完整 Agent 16/16 和相关专项均通过，但 `full-verify` 尚未运行。I/O 动态证据仍只覆盖一个 PUBLIC 和一个 workflow CONTROL owner；shared 排队 grant 轮转、多 workflow 并发、SYSTEM/workflow BACKGROUND、retiring cache、跨 owner LRU/transient、debt/退出注入及 VirtIO 错误/短 I/O/掉电仍需专门压力轮。

## 任务一：Agent 进程创建与地址空间设计

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T1-1 | Agent 进程能成功创建 | 已验证 | `agent_create()`、`agent_create_role()`、`agent_create_role_proc()`、`agent_make_role()`；创建前校验 role grant 和可信映像 role mask | `agentfinal_ucore` 创建 orchestrator Agent 子进程；`labdemo_ucore` 创建三类角色 Agent；`agenttrust_ucore` 验证可信映像成功、复制映像拒绝 |
| T1-2 | PCB 扩展字段正确初始化 | 已验证 | `struct proc` Agent 字段、`agent_role`、`agent_capability_mask`、`agent_role_grant_mask`、cause/span 当前状态、`agent_clear_metadata()`、`agent_make_role()` | `agent_info()` 验证公开身份和能力；`agentsecurity_ucore` 通过 bootstrap 创建成功、低权限委派拒绝和 Agent 槽复用清理验证内核私有 grant |
| T1-3 | Agent Context 区在用户地址空间中正确分配 | 已验证 | `agent_map_context()`、`AGENT_CONTEXT_BASE` | `agentfinal_ucore: context size=24576 capacity=128` |
| T1-4 | Agent 进程可直接读取 Context 镜像 | 已验证 | Agent Context 用户镜像页和内核 shadow 权威页 | `agentfinal_ucore` 读取 header/latest |
| T1-5 | 普通进程和 Agent 进程可共存，互不影响 | 已验证 | loader 从可信映像建立启动 grant；普通 fork/exec 不继承；普通进程不安装 Agent metadata/context，且不能创建 Agent 或调用敏感 Agent syscall；orchestrator 显式委派角色；普通与 workflow VFS 域彼此隔离 | `agentfinal_ucore`、`labdemo_ucore`、`agentsecurity_ucore: plain_child_role_creation_denied=1`、`agentvfs_ucore: protected_paths=1` |
| T1-6 | Agent 退出后资源能释放 | 已验证 | `agent_free_proc_context()`、协作退出、`proc_release_resources()`、`proc_recycle()` | Agent 专项测试均正常退出；`procreap_agent_ucore: adversarial-agent=1 parent passed` |
| T1-7 | 新 workflow 必须通过可信 factory 建立独立 scope；同 scope 子 Agent/worker 协作，普通 fork 不继承安全域 | 已验证 | syscall 541 `agent_workflow_create()`、动态 scope ref/admission、spawn scope mode | `agentscope_ucore: cross_scope_isolation=1 same_scope_collaboration=1` |
| T1-8 | pipe 端点不得随 scope、角色或并发线程成为环境权限；每个新 Agent/worker/workflow/降权普通主体仅接收创建线程经 syscall 542 显式一次性委派的精确对象 | 已验证 | `agent_scope_delegate_fd()`、线程私有 `fd_delegate_ticket[]`、默认拒绝的 file 继承类别、原子 FD 对象快照与票据消费 | `agentscope_ucore: transactional_fd_delegation=1 pipe_redelegation_isolation=1`、`agentvfs_ucore: worker_pipe_delegation=1` |

## 任务二：Agent 与内核结构化交互

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T2-1 | 用户态 Agent 测试程序能成功调用至少 3 个内核工具 | 已验证 | `agent_tools[]` 25 个工具、`agent_run()` | `agentfinal_ucore` 批量调用 echo，并验证 `action_commit`、`artifact_update`、`llm_request`、`llm_response`；`agentllm_ucore` 验证请求 Agent 和 Relay Agent 之间的 LLM 事件流；`labdemo_ucore` 调用 query_file、read_file_summary、read_file_digest、dependency、capability、action、artifact、send_message；`agentfs_ucore` 调用 read_file_digest 和 dependency_update |
| T2-2 | 每个工具请求和响应均为结构化格式 | 已验证 | `struct agent_op`、`struct agent_result`、`struct agent_tool_desc` | `agentfinal_ucore`、`agentbench_ucore` |
| T2-3 | 提供工具列表及参数说明 | 已验证 | `agent_tool_list()`、`agent_tools[]`、tool flags | [api.md](api.md) 工具表 |
| T2-4 | 工具调用结果可写入 Agent Context | 已验证 | `agent_append_context()` 写 shadow 权威页并同步用户镜像 | `agentfinal_ucore` 读取 latest 和 snapshot |
| T2-5 | 错误路径有明确返回 | 已验证 | `AGENT_STATUS_*`、工具执行状态码、真实 role/capability 授权、legacy 工具 ID/名称一致性检查 | `labdemo_ucore` 验证 denied 和 duplicate；`agentsecurity_ucore` 验证伪造 role 被拒绝和 `legacy_tool_mismatch=1` |
| T2-6 | 工具解析性能优化 | 扩展增强 | `agent_run()` 批量执行、ID 分发 | `agentbench_ucore` |
| T2-7 | 敏感工具授权不信任用户态自报 role | 扩展增强 | `capability_check`、`action_commit`、`artifact_update`、`dependency_update`、`llm_response` 均读取当前 PCB capability；`rerun_stage`、`write_report` 作为兼容别名也走同一授权、事件记录和重复请求判断路径 | `agentsecurity_ucore: sentinel spoof_denied=1` |
| T2-8 | legacy `tool_id` 和 `tool_name` 不一致时拒绝执行 | 扩展增强 | `sys_agent_call()` 先校验 ID 对应工具名，错误时返回 `AGENT_STATUS_BAD_REQUEST` 和 `tool_mismatch` | `agentsecurity_ucore: legacy_tool_mismatch=1` |
| T2-9 | legacy 参数键/类型错误被拒绝，syscall-only 工具不能走 batch | 扩展增强 | legacy 参数校验、`AGENT_TOOL_F_SYSCALL_ONLY` | `agentsecurity_ucore: legacy_param_validation=1 syscall_only=1` |
| T2-10 | 工具名称 + 参数键值列表协议可作为正式入口 | 已验证 | `agent_call()` 支持 name-only 请求和 key/type 校验，覆盖基础工具、文件摘要工具和依赖注册/查询工具 | `agentfinal_ucore: legacy_name_protocol=1` |
| T2-11 | LLM 请求和响应能作为结构化工具调用进入内核记录 | 已验证 | `AGENT_TOOL_LLM_REQUEST`、`AGENT_TOOL_LLM_RESPONSE`、`AGENT_EVENT_LLM_DONE`、`AGENT_CAP_LLM_RELAY` | `agentllm_ucore: template_relay=1`、`agentfinal_ucore: llm_template_relay=1` |

## 任务三：上下文路径管理

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T3-1 | Agent 测试程序执行 5 轮以上连续工具调用 | 已验证 | `agentfinal_ucore` 批量调用 | 连续 192 个 op |
| T3-2 | 系统正确维护多轮上下文路径 | 已验证 | shadow 权威 `agent_context_record[128]` 环形记录，record 含 16 字节 payload/result 摘要、cause/span 因果字段和 prev/record hash | `agentfinal_ucore: short_text_history=1`、`agentfinal_ucore: causal_context=1`、`agentfinal_ucore: context_integrity=1` |
| T3-3 | Agent 可直接从 Context 区高速读取路径数据 | 已验证 | Agent Context 用户镜像可读；`context_snapshot()` 可刷新并返回可信 shadow 历史 | `agentbench_ucore: direct_context`、`agentfinal_ucore` |
| T3-4 | 路径超长时自动淘汰，不导致内核 OOM | 已验证 | 固定容量 FIFO 环形覆盖 | `agentfinal_ucore: fifo oldest=66 latest=193 dropped=65` |
| T3-5 | 支持批量上下文快照和 rollback | 已验证 | `context_snapshot`、`context_query`、`context_rollback`、`context_clear` | `agentfinal_ucore`、`agentbench_ucore` |
| T3-6 | 用户态篡改 Context 镜像不影响内核权威历史 | 扩展增强 | kernel shadow + user mirror | `agentfinal_ucore: tamper_protected=1` |
| T3-7 | 可区分系统自动记录和手动记录，并能查询完整工具详情 | 扩展增强 | record flags、detail ring、`context_detail()` | `agentfinal_ucore: record_flags system=1 manual=1 truncated=0`、`context_detail=1` |
| T3-8 | Agent 有可自管的 Context cache，且不影响内核可信历史 | 扩展增强 | `agent_context_header.user_cache_offset/user_cache_size`，snapshot 只刷新内核管理区 | `agentfinal_ucore: user_cache_preserved=1` |
| T3-9 | 工具调用、手动记录和事件消费可形成轻量因果链，跨 Agent cause/span 由私有 source control/span owner 认证 | 已验证 | `agent_context_record.cause_sequence/span_id`、私有 cause sidecar、事件消费继承可信 owner；`context_push` 拒绝用户非零 cause/span | `agentsecurity_ucore: trusted_span_authority=1 trusted_cause_attribution=1` |
| T3-10 | Context 摘要和调度原因可合并为运行轨迹 | 扩展增强 | `agent_trace_snapshot()`、`struct agent_trace_record` | `agentfinal_ucore: runtime_trace=1 records=... context=1 sched=1 wait=1` |
| T3-11 | 当前 span 的系统级短记录可被参与 Agent 查询 | 扩展增强 | `agent_span_trace_snapshot()`、`struct agent_audit_record`、当前 `span_id` 过滤 | `agentfinal_ucore: span_trace=1 records=... context=1 event=1` |
| T3-12 | Context、调度、审计和预取提示可统一导出 | 扩展增强 | `agent_timeline_snapshot()`、`struct agent_timeline_record`、来源字段 `source` | `agentfinal_ucore: unified_timeline=1 records=... context=1 sched=1 audit=1 prefetch=1` |
| T3-13 | 统一 timeline 可由内核按条件过滤和按游标增量读取 | 扩展增强 | `agent_timeline_query()`、`struct agent_timeline_filter`、source mask、start tick、span/pid/kind/status/flags 过滤、`after_tick/source/sequence` 游标过滤 | `agentfinal_ucore: timeline_query=1 audit=213 recent=281 cursor=177` |
| T3-14 | Context Path 可验证相邻记录顺序 | 扩展增强 | `agent_context_record.prev_hash`、`agent_context_record.record_hash`、`agent_context_header.latest_record_hash`、rollback/clear 同步链尾 hash | `agentfinal_ucore: context_integrity=1` |
| T3-15 | 每 workflow scope 的稀疏审计窗口可用摘要和逻辑 hash 链校验 | 已验证 | scope-local `prev_hash/record_hash/ledger_hash`、全局单调 sequence、`dropped_records=total-visible`；相邻可见记录只在无 gap 时直连 | `agentfinal_ucore` 用 dropped 解释可见链缺口的回归已通过 |

## 任务四：面向 Agent 查询优化的文件系统扩展

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T4-1 | Agent 可按文件属性查询实验工件 | 已验证 | `struct agent_file_meta`、`agent_file_query()`、`AGENT_TOOL_QUERY_FILE` 属性 payload | `labdemo_ucore: tool=query_file hits=1 used_index=1` |
| T4-2 | 支持项目、工作流、运行、阶段、类型、状态、摘要、逻辑路径字段 | 已验证 | `os/agent.h` 中 `agent_file_meta` / `agent_file_query` | [task4-file-query.md](task4-file-query.md)、`labdemo_ucore` |
| T4-3 | 有扫描路径和索引路径 | 已验证 | `agent_file_query()`、status/stage/kind 索引桶 | `agentfs_ucore: bulk_index scan=118 index=6 hits=1`、`agentfs_ucore: scan_index_consistent=1`、`agentbench_ucore: file_scan_query/file_index_query` |
| T4-4 | 查询结果包含命中、截断、扫描数、是否使用索引、查询计划、候选数和 tick | 已验证 | `struct agent_file_query_result` | `agentfs_ucore: query_plan ...`、`agentfs_ucore: truncated_query total=100 returned=3 truncated=1`、`agentbench_ucore: file_query_plan ...` |
| T4-5 | 查询计划能解释索引选择原因 | 扩展增强 | `plan`、`plan_reason`、`index_bucket`、`candidate_records` | `agentfs_ucore: query_plan scan_plan=0 index_plan=1 reason=4 bucket=15 candidates=6` |
| T4-6 | 支持对象依赖关系注册和查询，服务预取提示和用户态恢复策略 | 已验证 | `AGENT_TOOL_DEPENDENCY_UPDATE`、`AGENT_TOOL_DEPENDENCY_QUERY`、内部依赖记录、依赖位图兼容输入、`label/namespace/run_id` 选择条件 | `agentfs_ucore: dependency_update=1`、`agentfs_ucore: scoped_dependency=1`、`labdemo_ucore: affected labels=align+analyze+report+archive` |
| T4-7 | 查询写入 Context Path，可用于报告回放 | 已验证 | 文件查询和工具调用均追加 Context | `labdemo_ucore`、`context_snapshot` |
| T4-8 | 文件元数据写入只能由具备权限的 Agent 执行 | 扩展增强 | `agent_file_meta_init()`、`agent_file_meta_set()` 要求 Agent 且具备 `AGENT_CAP_META_WRITE` | `agentsecurity_ucore: plain_process_denied=1`、`sentinel meta write denied` |
| T4-9 | 索引初始化前查询安全 | 扩展增强 | `agentinit()` 初始化 status/stage/kind 索引桶为 `-1` | `agentsecurity_ucore: preinit_index_query=1` |
| T4-10 | 多 run 动作提交和工件更新只修改目标 run | 扩展增强 | `action_commit` 和 `artifact_update` 支持 `label=...;run_id=...;namespace=...` selector；旧 `rerun_stage` 和 `write_report` 为兼容别名 | `agentsecurity_ucore: scoped_action=1`、`agentsecurity_ucore: scoped_artifact=1` |
| T4-11 | 文件元数据绑定真实 uCore 根目录 inode 的当前生命期 | 已验证 | `agent_fs_note_create/write/truncate/delete()`、`dev + inum + incarnation` 主键；inode 重用会生成新 incarnation | `agentfs_ucore: demo_inode`、`agentfs_ucore: custom_inode`；`agentvfs_ucore` 验证执行委派和删除均校验 incarnation |
| T4-12 | 元数据可事务写入并强制重新加载 | 已验证 | 私有 `.agentmeta` / `.agentmeta1` 双 bank 紧凑快照，以 generation、精确长度和 payload hash 校验后切换 | `agentfs_ucore: .agentmeta_reload=1` |
| T4-13 | 普通文件 syscall 不能直接访问 Agent 元数据后端 | 已验证 | `fileopen()` / `fileunlink()` 对两个 metadata bank 均返回 `-1` | `agentsecurity_ucore: .agentmeta_protected=1` |
| T4-14 | 对真实磁盘目录做自动扫描并维护索引 | 部分实现 | `agent_background_maintain()`、`agent_file_request_scan()`、调度器空隙分批扫描 uCore 根目录 | `agentscan_ucore: background_scan usershell=1`、`agentscan_ucore: auto_file_create=1`、`agentscan_ucore: auto_file_delete=1`；当前不做多级目录递归 |
| T4-15 | 基于历史查询和对象标签依赖生成 scope-local 预取提示 | 已验证 | 查询/缓存保存精确 hit slot；依赖选择器受每 scope 16 条配额约束，hash 预筛后精确比较 scope/label/namespace/run/workflow；一次文件表扫描、目标位图去重、单次最多发布 8 条；物理 32 条 span 表按 4 scope 各保留 8 条；message 交接以 `slot + pid + control_id + scope` 重校验接收端点后有界原子发布 | `agentfs_ucore: prefetch_hints=1 bounded=1 count=2 preemptions=8`、`handoff_target_exit=1 endpoint_reuse=1 preemptions=6 ... clean=1`；`agentscope_ucore` 另验证跨 scope 审计/事件隔离 |
| T4-16 | 预取提示交接可由 orchestrator 审计和过滤 | 扩展增强 | `AGENT_AUDIT_KIND_PREFETCH`、`agent_audit_snapshot()`、`agent_audit_query()` | `labdemo_ucore: global_audit=1 ... prefetch=1`、`labdemo_ucore: audit_query=1 ... prefetch=...` |
| T4-17 | 同一 scope、同一 kernel-owned span 的预取提示可跨 Agent 查询 | 已验证 | scope-partitioned span prefetch、公开 span id + private span owner、source/target stable attribution | 同 workflow 场景与 `agentscope_ucore` 跨 scope 负向回归均通过 |
| T4-18 | Agent 可读取真实文件的轻量内容证据 | 扩展增强 | `AGENT_TOOL_READ_FILE_DIGEST`、`read_file_digest` 工具、真实 inode `readi()`、FNV-1a 内容指纹、短预览、`CONTENT_READ` capability 授权 | `agentfs_ucore: content_digest=1 size=7 bytes=7 ...`、`agentbench_ucore: file_digest ...`、`agentsecurity_ucore` sentinel 拒绝 |
| T4-19 | 重复文件属性查询可复用同一元数据代数下的结果 | 扩展增强 | 8 槽 generation-aware 文件查询结果缓存、`AGENT_FILE_QUERY_REASON_CACHE_HIT`、`fs_generation` 失效判断 | `agentfs_ucore: query_cache=1 ...`、`agentfs_ucore: clear_status=1 cache_invalidated=1`、`agentbench_ucore: file_query_cache hit=1 ...` |
| T4-20 | 重复读取同一真实文件内容证据可复用缓存并在文件变化或 inode 重用后失效 | 扩展增强 | 8 槽 digest cache、`dev/inum/incarnation/size/content_generation` key、`agent_info.file_digest_cache_hits/misses` | `agentfs_ucore: digest_cache=1 ...`、`agentfs_ucore: digest_cache_invalidated=1 ...`、`agentbench_ucore: file_digest_cache hits=63 misses=1` |
| T4-21 | 文件内容证据可进入统一观测流 | 扩展增强 | `read_file_digest` 工具调用自动追加 Context，`agent_timeline_query()` 按 `tool_id=AGENT_TOOL_READ_FILE_DIGEST` 过滤，timeline value/text 保留 size、bytes、hash 和 preview | `agentfs_ucore: digest_timeline=1 tool=20 preview=agentfs2`、`labdemo_ucore: timeline_query prefetch=3 cursor=... digest=1` |
| T4-22 | 两个 Agent 同时编辑同一真实文件时，内核能阻止无序覆盖 | 扩展增强 | `agent_file_edit_begin/commit/abort/state` 以 `dev + inum + incarnation` 标识当前文件生命期；真实 `write/O_TRUNC/unlink` 路径调用租约检查和版本提交检查 | `agentconflict_ucore: conflict_denied=1 direct_write_denied=1`、`agentconflict_ucore: stale_commit=1 versioned_commit=1` |
| T4-23 | 普通文件路径不能绕过 Agent 内容读取和工件写入能力 | 扩展增强 | 持久化 VFS label、安全域 credential、`vfs_inode_authorize()` 覆盖 lookup/open/read/write/truncate/unlink；跨 scope inode fd 撤销，同 scope inode fd 按 `dev + inum + incarnation` 重新校验 | `agentvfs_ucore: protected_paths=1 cross_scope_fd_revoked=1 parent passed` |
| T4-24 | 普通域短命文件不能耗尽 Agent 文件版本状态 | 扩展增强 | 编辑/内容版本使用按 `inum` 直接索引的统一 sidecar；`iput()` 最终回收同一 incarnation 的版本、租约和 digest cache；sidecar 容量由稳定存储 principal 的 inode 配额与分级保留量共同约束 | `fsquota_ucore: public_version_churn=1 cycles=640`、`workflow_version_reserve=1`、`content_version_reserve=1` |
| T4-25 | 文件系统为 4 个 admitted workflow 各保留独立 inode/block 保证，单个 scope 不能消耗其他 scope 或 SYSTEM 的余量 | 已验证 | mkfs/内核共享容量算法并将 version/slots/PUBLIC principal/G/S/checksum 持久化；每 scope 下限 320 inode/512 block、SYSTEM 下限 8 inode/512 block；当前平台实际值 342/1195 与 64/512；重启固定 G 并由 `free-4G` 恢复 S，admission 原子复核 | `test_fs_storage_policy`、mkfs 负向容量契约、ENOSPC 配额保留专项和 `agentscope_ucore: scope_storage_quota=1` 均通过 |
| T4-26 | metadata 内存表、索引、inode sidecar、依赖和双 bank 提交形成统一并发事务，查询不得观察半提交状态 | 已验证 | 进程事务使用单调 ticket FIFO 和 wake-one，退出请求先传递 ticket；scheduler 在门空闲时可取得硬有界保留轮次且不重复唤醒 serving waiter；进程态扩展扫描每 128 records 计入 kernel-work 预算；显式依赖固定表与文件内兼容位图分离，消费者按需线性解析，不在事务门内重建派生图；同步 mutation/reload 另用 FIFO submit lane，immutable COW job 跨预算等待保持 `job_id` | `agentfs_ucore` 输出 `metadata_action_bounded=1 field_driven=1 batched=1 preemptions=5`、依赖查询兼容结果、预取预算和端点复用负向标记；完整轮另由 `agentscope_ucore` 验证事务竞争、跨 scope 进展和最终一致性 |
| T4-27 | PUBLIC 持久存储计费必须跨完整进程域退出和磁盘重挂载保持，删除后才能归还并复用容量 | 已验证，保留掉电注入缺口 | 安装级 PUBLIC principal 2、VFS 独立计费凭据、qmap/dinode 持久 owner、挂载孤儿清扫与 PUBLIC 用量重建；SYSTEM 赞助对象先在固定工作区收集/排序全部块，按 qmap block 分组预检，一次预留后由 claim gate 保持 qmap-first、inode-last 前向提交，挂载继续部分 claim | 当前 `make fs-enospc-test` 以 75.1s 通过 quota/domain/persistent principal/orphan/reboot 全流程；未在 grouped claim 中点注入掉电 |
| T4-28 | 低权限 workflow 的高频微小文件变化不得同步放大全局 metadata I/O，也不得阻断另一 workflow 的查询服务；可信 metadata 必须在用户进程发布前加载，无可验证有效 bank 时拒绝以空状态继续授权 | 已验证，保留故障注入缺口 | `timer_init()` 后、`bio_policy_start()`/`load_init_app()` 前调用 `agent_storage_init()`；单个 bank 损坏时选择另一有效副本并标记恢复，无有效 bank 或选择失败时设置 `agent_meta_store_failed_closed`，metadata API 拒绝；scope VFS-labelled 清理仍可退休；运行期使用 inode sidecar、PERSIST scope-local 固定窗口写回、primary-then-mirror COW、触发 scope `BACKGROUND` budget 和 scope-local query cache generation | 当前完整轮中 `agentscope_ucore` 通过微写合并、跨 scope 查询、volatile 分流、scan-pressure、最终 reload 一致性、生命周期等标记并输出 `parent passed`，`elapsed=139.9s`；未动态注入启动 bank 损坏、VirtIO 错误、短写或掉电 |
| T4-29 | PUBLIC 冷工作集和持续微写不能挤掉另一 workflow 的 cache 保留或块设备服务；Orchestrator/Recovery 控制 I/O 与后台维护有独立保证；内核态 yield loop 与主线程进程级 fault teardown 不得屏蔽 refill/完成或绕过归因 | 已验证，保留测试缺口 | 每个稳定 owner 的 NORMAL/CONTROL/BACKGROUND bucket、每 bucket FIFO admission、排队 shared grant 的 owner/class cursor 轮转及无排队 fast path、普通流量设备根限速与保护流量带债前进；scheduler 每轮 idle kerneltrap 中断窗口；进程级 terminal cleanup I/O/kernel-work 上下文；buffer sponsor floor/cap、exclusive holder、cross-owner LRU 防刷新、transient buffer、FS atomic/quiescent checkpoint、scope quiesce/retire 回收；`io_policy_info()` ABI v3 sized-prefix | 最终修复后的独立 `iobudget_ucore` 输出八项具名机制标记和 `parent passed`，`elapsed=2.4s`；未动态断言 shared 排队 grant 轮转，也未覆盖 Recovery、多 workflow、SYSTEM/workflow BACKGROUND、retiring 3/8、跨 owner LRU/transient、主动 device-debt 注入或设备故障 |

## 任务五：Agent Loop 内核运行机制

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T5-1 | Agent 可注册和删除 watch | 已验证 | `agent_watch()`、`agent_unwatch()`、`AGENT_WATCH_MAX=8` | `labdemo_ucore: WATCH_REGISTERED`、`agentloop_ucore: unwatch=1` |
| T5-2 | Agent 可等待事件并 timeout | 已验证 | `agent_wait()`、`AGENT_STATUS_TIMEOUT`、`timeout_count`、有限 timeout 睡眠等待路径 | `agentbench_ucore: timeout_heartbeat=1`、`agentloop_ucore: timeout_sleep_no_poll=1` |
| T5-3 | 文件状态变化能唤醒目标 Agent | 已验证 | `agent_file_meta_set()` 投递包含 status、stage、run_id、project 的 `AGENT_EVENT_FILE_STATUS` | `labdemo_ucore` sentinel 收到 failed 事件 |
| T5-4 | 消息能触发 Agent 事件，用户 `agent_wake()` 不能伪造系统事件 | 已验证 | `send_message` 工具；`agent_wake()` 唯一接受 `AGENT_EVENT_MESSAGE`；跨 Agent 消息还需 stable control id 定向路由，系统事件走内核或受权专用工具路径 | `labdemo_ucore: parent passed`；`agentsecurity_ucore: wake_event_authorization=1 route_source_enforced=1 route_target_isolated=1 ipc_route_authorization=1` |
| T5-5 | 心跳字段可设置、可按 TIMER watch 投递事件、可停止 | 已验证 | `agent_heartbeat()`、`agent_heartbeat_stop()`、TIMER watch/unwatch | `agentbench_ucore: timeout_heartbeat=1`、`agentloop_ucore: timer_unwatch=1`、`agentloop_ucore: heartbeat_wake_stop=1` |
| T5-6 | busy polling 和 event wait/wake 可计时观测并稳定完成 | 已验证 | `agentbench_ucore` | `agentbench_ucore: busy_poll_query`、`agentbench_ucore: event_wait_wake`、`agentbench_ucore: busy_poll_vs_wait`；不设置固定 tick 阈值 |
| T5-7 | 事件处理写入 Context Path | 已验证 | `agent_wait()` 成功消费事件后追加 Context，并继承事件 cause/span | `agentloop_ucore: event_causality=1`、`labdemo_ucore` 和 [task5-agent-loop.md](task5-agent-loop.md) |
| T5-8 | 普通进程或低权限 Agent 不能伪造系统事件或越权取消等待 | 扩展增强 | `agent_wake()` 使用 `MESSAGE_SEND` 且只接受 `AGENT_EVENT_MESSAGE`；`agent_wait_cancel()` 使用独立 `WAIT_CANCEL` capability，并校验内核签发的直接 controller 关系 | `agentsecurity_ucore` 已验证普通进程返回 `-1`、所有低角色取消父 orchestrator 均被拒绝，以及 controller A 退出后 controller B 的新 control id 不继承旧取消权；control id 不复用机制保证 PID/PCB 槽复用不会扩权；消息通信改由 T5-21 的显式路由验证 |
| T5-9 | 事件队列满时拒绝新事件且不覆盖旧事件，可归因外部压力不能占用内核 origin 保留量 | 已验证，保留测试缺口 | `AGENT_EVENT_QUEUE_CAP=16`、`AGENT_EVENT_EXTERNAL_LIMIT=12`、`AGENT_EVENT_IPC_LIMIT=8`、`AGENT_EVENT_ATTRIBUTED_LIMIT=8`、`AGENT_EVENT_SOURCE_LIMIT=4`；每槽 accounting/stable source 记账、FIFO queue | `message_source_limit=4`、`ipc_class_limit=8`、`external_limit=12`、`system_event_reserved=4`、`external_reject_reclaim=1`：第 13 条 external 不入队，4 条 KERNEL TIMER 将队列填至 16，消费后 directed 和 attributed 均可重新接纳；尚未单测 attributed=8、同一来源混合跨类，或在总容量 16 上再投第 17 条并逐条核对旧内容 |
| T5-10 | 调度器感知 Agent 角色、事件状态和受权配置，但软分值不能越过资源域外层轮转或域内强制公平上限 | 扩展增强 | `fetch_task()` 先轮转 `scheduler_active_domains`，再在 `scheduler_domain_tasks[]` 中调用域内评分；role weight、priority、budget、event/deadline/heartbeat/vruntime scoring 与每域 `AGENT_SCHED_MAX_AGENT_BURST=8` FIFO escape | 本次改动后单独 `agentsched_ucore` 已通过；其角色、配置、事件和普通进展契约保持有效 |
| T5-11 | 受权 Agent 可取消其受控 Agent 的等待 | 扩展增强 | 每个 Agent 使用内核私有且不复用的 64 位 control id；创建时绑定直接 controller，合法 `agent_wait_cancel()` 写一次性令牌，目标返回 `AGENT_STATUS_CANCELLED` 并追加 Context | `agentloop_ucore: wait_cancel=1`、`agentsecurity_ucore: wait_cancel_scope=1 wait_cancel_controller_lifecycle=1` |
| T5-12 | Agent 最近调度原因可查询 | 扩展增强 | `agent_sched_snapshot()`、`struct agent_sched_record`、reason flags | `agentsched_ucore: reason_trace=1` |
| T5-13 | 调度原因能和 Context 历史一起供 Agent 查询 | 扩展增强 | `agent_trace_snapshot()` 按 tick 合并 Context 与调度记录 | `agentfinal_ucore: runtime_trace=1 ... sched=1` |
| T5-14 | 同一 workflow 多 Agent 的 Context、事件、调度和预取交接摘要可由本 scope orchestrator 查询 | 已验证 | 物理 512 条审计表、每 scope 128 条、scope-local ledger；同 scope span 提示可由参与 Agent 查询 | `labdemo_ucore` 同 workflow 场景和 `agentscope_ucore` 跨 scope 隔离断言均通过 |
| T5-15 | 多 Agent scope-local 短记录可按条件过滤且 filter 不扩大对象范围 | 已验证 | `agent_audit_query()` 先按 scope/private owner 裁剪，再应用 filter flags | 既有过滤回归与 `agentscope_ucore: audit_event_scope_isolation=1` 均通过 |
| T5-16 | 非 orchestrator 参与者可读取当前可信 span 的协作短记录 | 已验证 | `agent_span_trace_snapshot()` 匹配 scope + current span id + private span owner，普通进程返回 `-1` | 同 workflow span trace 与 `agentsecurity_ucore` foreign/forged span 负向断言均通过 |
| T5-17 | 状态页面可读取、过滤并增量刷新统一运行时间线 | 扩展增强 | `agent_timeline_snapshot()` 把 Context、调度、可见审计和预取提示规范化为同一结构，`agent_timeline_query()` 在可见集合上过滤，并支持 `tick/source/sequence` 游标；Context 审计记录保留工具结果数值槽，可承载内容摘要证据 | `labdemo_ucore: unified_timeline records=... context=1 event=1 sched=1 prefetch=1 digest=1`、`labdemo_ucore: timeline_query prefetch=3 cursor=... digest=1`、`agentsecurity_ucore` 普通进程拒绝 |
| T5-18 | 状态页面可读取当前可见因果关系 | 扩展增强 | `agent_provenance_snapshot()` 把 Context、审计和预取提示转换成 source/target 因果边，并沿用当前 Agent 可见范围；内容摘要工具调用也能作为可见因果边导出 | `agentfinal_ucore: provenance_graph=1 edges=126 context=1 audit=1`、`labdemo_ucore: provenance_graph edges=... message=1 prefetch=1 digest=1`、`agentsecurity_ucore` 普通进程拒绝 |
| T5-19 | 状态页面或 Agent worker 可等待新 timeline 记录 | 扩展增强 | `agent_timeline_wait()` 复用 timeline filter，在无匹配记录时睡眠，由 observe epoch、timeout 或新记录唤醒；等待 filter 保存在 PCB 中，写入新记录时按完整 filter 判断 source、event、status、tool、span、pid 和 flags 是否匹配；`agent_timeline_read()` 可在同一次 syscall 中等待并复制记录 | `agentfinal_ucore: timeline_wait=1 timeout=-7 source_gate=1 event_gate=1 wake=1 query=1 read=1 sleeps=1 wakeups=1`、`agentbench_ucore: timeline_wait_ready ...`、`agentsecurity_ucore` 普通进程拒绝 |
| T5-20 | 普通资源域在其他域持续运行任意数量、高软分值 Agent 线程时仍能在有限域轮次内运行 | 已验证 | active-domain FIFO 中每域至多一个节点并严格轮转；域内 `scheduler_agent_burst == 8` 强制选普通线程，`scheduler_score_burst == 8` 强制选 FIFO 队首；这些计数按域保存，weight、priority、budget、事件积压、角色和线程数均不能修改外层轮转 | `threadresource_ucore: domain_fairness ... victim=512 bound=576`；本次改动后单独 `agentsched_ucore` 和完整 Agent 脚本通过 |
| T5-21 | 低权限 Agent 不能把 `MESSAGE_SEND` 扩大成任意 PID 或跨 workflow 通道 | 已验证 | stable control id route、same-active-scope 强制检查、target consent 同样受 scope 限制；所有直接投递入口统一鉴权 | route grant/revoke/生命周期标记和 `agentscope_ucore: ipc_scope_isolation=1` 跨 scope consent/投递拒绝均通过 |
| T5-22 | 单个来源、单一外部事件类别或慢 watcher 不能耗尽关键事件资源或阻断其他订阅者 | 已验证，保留测试缺口 | 同一 stable source 跨类 4 条、directed/attributed 各 8 条、external 合计 12 条，并为内核 origin 保留至少 4 个容量名额；广播逐 watcher 继续投递，权威 metadata 提交不接收单一通知队列背压 | `message_source_limit=4`、`ipc_class_limit=8`、`external_limit=12`、`system_event_reserved=4`、`external_reject_reclaim=1`、`broadcast_slow_watcher_isolated=1`；尚未专项覆盖 attributed=8、同一来源混合跨类、满队列与 wait cancel 组合、LLM_DONE 配额压力和 metadata 提交返回值 |

## 任务六：综合示例与创新

| ID | 赛题方向 | 当前状态 | 说明 |
| --- | --- | --- | --- |
| T6-1 | 综合示例程序 | 已验证 | `labdemo_ucore` 以科研 Agent 平台为示例负载，串联任务一至五，输出 `agentos:event`，读取真实 align 日志内容摘要，并查询、过滤本 workflow 的审计短记录和统一 timeline |
| T6-2 | 性能和计时示例程序 | 已验证 | `agentbench_ucore` 输出批量工具、Context、文件查询性能，以及轮询/事件等待计时观测；`labbench_ucore` 作为示例规划入口包装运行 |
| T6-3 | 权限与稳定性负向示例程序 | 已验证 | `agentsecurity_ucore` 验证角色、工具和系统事件授权；`agenttrust_ucore` 验证可信映像绑定；`agentvfs_ucore` 验证普通路径不能绕过文件 capability；`usersafety_ucore`、`fsenospc_ucore` 和 `procreap*_ucore` 验证恶意输入、资源耗尽、阻塞退出、拒绝 wait 和 fork pressure 不会停止内核；`fileresource_ucore` 已验证唯一文件对象槽的域配额、水位、保留和最终引用退款 |
| T6-4 | LLM-friendly template relay | 已验证 | 内核提供 `llm_request` / `llm_response` 工具、`AGENT_EVENT_LLM_DONE`、`LLM_RELAY` capability、Context/timeline/audit 记录；真实云端调用放在用户态或宿主机 relay | `agentllm_ucore: template_relay=1`、`agentfinal_ucore: llm_template_relay=1` |
| T6-5 | 页面查看与图表查看 | 已验证 | `make reader` 启动本地页面服务；`results/latest/monitor.html`、`reader-guide.html`、`index.html`、`charts/*.svg` 呈现双目标运行、测试入口、实验图表和 AgentOS 证据 | `host_tools/plain_ucore_reader.py`、`host_tools/summarize_dual_platform_results.py`、`host_tools/test_plain_ucore_reader.py`、`host_tools/test_summarize_dual_platform_results.py` |
| T6-6 | 查询历史驱动的预测性预取 | 部分实现 | 当前实现文件 metadata 预取提示，覆盖同一 scope/run 的对象标签依赖；综合示例中 message 入队时内核把 sentinel 的提示交接给同 scope investigator，并写入该 scope 的可信 span 提示分区；尚未做文件内容预加载或通用预测器 | `agentfs_ucore: prefetch_hints=1`、`agentbench_ucore: file_prefetch_snapshot ...`、`agentfinal_ucore: span_prefetch=1`、`labdemo_ucore: sentinel prefetch_hint ...`、`labdemo_ucore: investigator handoff_prefetch ...`、`labdemo_ucore: investigator span_prefetch ...`、`agentos:event type=PREFETCH_USED ...` |

## 追踪结论

任务一至三已有增强实现和既有测试证据；capability、VFS 凭据、Context cause/span、audit 和对象表均绑定到 kernel-issued workflow scope。任务四的真实对象身份是 scope 与 `dev + inum + incarnation` 的组合，metadata、依赖、动作、租约、版本、查询缓存和预取按 scope 分区；可信 bank 在用户进程发布前加载，单副本损坏可由另一有效 bank 恢复，无可验证有效 bank 时 metadata API fail closed，而 VFS-labelled scope cleanup 仍可退休。块设备速率与 buffer cache sponsor 绑定到稳定 PUBLIC/workflow/SYSTEM owner；线程槽和调度外层则绑定不可变进程资源域。当前 pipe 安全主体委派改动后已通过默认构建和完整 Agent 16/16，完整轮墙钟约 `359.4s`，AgentOS 栈预算为 `13856 < 16384`；此前线程资源、双目标进程回收、syscall 公平性和 filepool 脚本的通过结果继续作为历史证据。聚合 `full-verify` 尚未运行，当前交付范围不包含多级目录递归扫描、复杂策略语言和内核直连云端 LLM Gateway。
