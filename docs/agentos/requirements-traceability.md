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
| G-1 | 在教学操作系统内核中实现 Agent-OS 功能模块 | 已验证，保留测试缺口 | `os/agent.c` facade；core/context/identity/IPC/lifecycle/observe owner；metadata transaction、`agent_file_state`、catalog/query/scan/directory/objects/store；`resource_controller`、`workflow_lifecycle` 与 `os/proc.c` | `75d0dfd` checkpoint 的 clean `full-verify` 通过；后续 query/scan/directory 行为保持拆分已通过分阶段边界/定向检查，但最终 HEAD 仍待新的 clean 聚合验收 |
| G-2 | 系统可在 QEMU 上运行 | 已验证 | `Makefile`、`nfs/fs.img` | `scripts/run-agent-tests.sh`、各资源专项和 `scripts/run-workflow-teardown-race-tests.sh`；checkpoint 的 Reader E2E、双目标和聚合 QEMU 路径通过，边界见 [verification.md](verification.md) |
| G-3 | 提供内核代码 | 已验证 | `os/` | Git 仓库源码 |
| G-4 | 提供用户态测试程序 | 已验证 | `agentfinal_ucore`、`agentfs_ucore`、`agentscan_ucore`、`agentloop_ucore`、`agentsched_ucore`、`agentconflict_ucore`、`agentllm_ucore`、`agentbench_ucore`、`labbench_ucore`、`labdemo_ucore`、`agentsecurity_ucore`、`agenttrust_ucore`、`agentvfs_ucore`、`agentscope_ucore`、`iobudget_ucore`、`usersafety_ucore`、`workflow_teardown_race_ucore` 及资源专项程序 | 16-case Agent 套件保持独立；checkpoint 的 `workflow_teardown_race_ucore` 另行连续三轮通过，边界见 [verification.md](verification.md) 和 [test-record.md](test-record.md) |
| G-5 | 提供综合示例场景 | 已验证 | `user/src/labdemo_ucore.c` | `labdemo_ucore: passed` |
| G-6 | 提供设计文档和运行说明 | 已验证 | [../../README.md](../../README.md)、[design.md](design.md)、[scenario-script.md](scenario-script.md) | 本文档、[verification.md](verification.md) |
| G-7 | 保留代表性的 uCore 基础 syscall 兼容性 | 已验证 | `SYS_trace`、`SYS_mailread`、`SYS_mailwrite` | `ch3_trace` 输出 `Test trace OK!`；`agentsecurity_ucore: mail_basic=1` |
| G-8 | 内核增长、模块边界和关键运行预算必须可审查并在 CI 中拒绝回退 | 已验证，保留测试缺口 | `.gitlab-ci.yml`、`ci/kernel-budgets.json`、`scripts/check-kernel-budgets.py`、`scripts/check-agent-module-boundaries.sh`、`make ci-check` | owner/bridge/dependency/SCC 采用版本化注册集合；metadata control plane 聚合限制 source、loaded text 与 BSS，防止跨文件迁移；fail-closed 自测集合由源码自举；checkpoint 本地门通过，但最终 HEAD 与远程普通/QEMU Runner 仍待确认 |

## 内核安全与稳定性机制

这些条目不是对个别测试程序的特判，而是普通进程和 Agent 共用的内核机制。两个目标共享输入检查、等待队列、文件系统失败处理等行为目标，但当前 generation-safe resource controller、workflow lifecycle、统一 teardown 和 lazy physical stack 只在根目录 AgentOS 目标实现；`baseline_ucore/` 仍保留旧计数与固定栈路径。追踪表只把实际共享代码写成共享实现。

| ID | 安全或稳定性要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| S-1 | 所有 syscall 用户指针、字符串、长度、权限和描述符方向在解引用前统一校验，失败路径不留下半完成资源 | 已验证 | `user_range_check()`、`copyin()`、`copyout()`、`copyinstr()`、`os/syscall.c`、`os/loader.c`、`os/file.c`、`os/pipe.c` | `usersafety_ucore` 覆盖 pointer/string/exec argv/thread 范围、pipe buffer、wait/time copyout、fd 方向、文件分配回滚、信号量输入和 exec 事务，并输出 `usersafety_ucore: parent passed` |
| S-2 | 睡眠线程只挂入所属对象的定向等待队列，唤醒、取消和退出中断不会扫描或破坏其他同步队列 | 已验证 | `struct wait_queue`、`wait_queue_sleep/wake_one/wake_all/interrupt/cancel()`；mutex、semaphore、condvar、child、Agent event、timeline 和 thread-exit 各自持有队列 | `usersafety_ucore: live after directed wakeup`；`procreap_ucore: wait-queue cancellation passed`；`agentfinal_ucore` 验证 timeline filter 不匹配不唤醒 |
| S-3 | inode、inode cache 和数据块耗尽返回可恢复错误；未提交的创建/扩容状态回滚，短写准确报告已经提交的前缀 | 已验证 | `ialloc()`、`balloc()`、`bmap()`、`writei()`、`create()` 的错误返回、回滚和短写路径 | `fsenospc_ucore: inode exhaustion survived`、`inode cache exhaustion survived`、`block exhaustion survived`、`parent passed` |
| S-4 | 每线程保留独立虚拟栈、guard 和 canary，物理页按 live thread 分配并在 scheduler handoff 后释放；构建与 CI 限制线程栈、boot stack 和总容量 | 已验证 | `kernel_stack_acquire()`、`kernel_stack_release_inactive()`、`KSTACK_NONE/LIVE/REAP`、`boot_stack` 链接符号、`scripts/check-kernel-stack-usage.py`、budget probe | 当前 `make ci-check` 测得线程栈 `14592/16384`、boot path `10144/65536`；thread/Agent/proc 专项通过 |
| S-5 | Agent 软调度分值不能越过资源域公平边界，也不能让同域普通任务永久饥饿 | 已验证 | `scheduler_active_domains` 外层严格轮转；`scheduler_domain_tasks[]` 域内队列；每域 `scheduler_agent_burst` / `scheduler_score_burst` 与 `AGENT_SCHED_MAX_AGENT_BURST=8`；`fetch_task()` 两级选择 | 当前三轮 Agent 套件、proc-reap 和 thread-resource 均通过，thread-resource 另完成同镜像 50/50 压力轮 |
| S-6 | 用户态事件注入只允许消息类型，系统事件只能由对应内核或受权专用工具产生 | 已验证 | `agent_wake()` 仅接受 `AGENT_EVENT_MESSAGE`；FILE_STATUS、TIMER、LLM_DONE 等走专用投递路径 | `agentsecurity_ucore: wake_event_authorization=1`；普通进程调用拒绝、保留系统类型返回 `AGENT_STATUS_DENIED`、非法类型返回 `AGENT_STATUS_BAD_PARAM` |
| S-7 | Agent 创建授权、workflow factory 权与业务 capability 分离；只有可信 bootstrap factory 能创建新 scope，scope 内 orchestrator 只能委派本域角色 | 已验证 | `agent_role_grant_mask`、`agent_authority_bootstrap/on_exec/check()`、`agent_create_role_proc()`、`agent_workflow_create_proc()`、动态 scope admission | `agentscope_ucore: cross_scope_isolation=1 same_scope_collaboration=1 scope_capacity_reservation=1` |
| S-8 | Agent 角色与可信、不可变且角色许可的可执行映像绑定，复制或篡改程序文件不能继承 Agent 权限 | 已验证 | `user/include/exec_policy_manifest.h`、`os/exec_policy.c`、loader 映像身份、不可变 inode 和 role mask 校验 | `agenttrust_ucore: wx_image=1`、`immutable_image=1`、`bootstrap_role_boundary=1`、`trusted_agent_exec=1`、`role_image_binding=1`、`parent passed` |
| S-9 | 普通 open/read/write/truncate/unlink 服从 capability 与精确 workflow scope；执行委派绑定 `scope + dev + inum + incarnation`，跨 scope inode fd 撤销，同 scope inode fd 每次重新授权 | 已验证 | `os/vfs_security.c`、inode VFS scope/label/checksum/incarnation、`vfs_inode_authorize()`、`vfs_proc_delegate_exec/install_image()`、scope-aware lookup、真实文件 syscall 路径 | `agentvfs_ucore` 负向检查通过；`agentscope_ucore` 的同名文件跨 scope 隔离、租约隔离和一次性 pipe fd 委派断言通过 |
| S-10 | 父进程先退出时，活子进程转为内核持有；无父退出进程由内核立即回收，不遗留孤儿僵尸 | 已验证 | `proc_orphan_children()`、`proc_child_publish_exit()`、`proc_recycle()` | `procreap_ucore: child-first=...`、`parent-first=...`、`orphan-resource=...`、`parent passed` |
| S-11 | 所有进程级退出原因采用单一正向 teardown；阻塞 sibling 展开后依次 detach、reclaim、settle、handoff，scheduler 最后发布并回收栈/槽 | 已验证 | `enum proc_teardown_state`、`teardown_owner_tid`、`proc_teardown_run()`、terminal clear/lifecycle release、scheduler publish/recycle | checkpoint 的 teardown race 连续三轮组合覆盖 factory close/自然退出、Context/metadata waiter、阻塞 file 引用、I/O debt/cache、inode/account 和 lifecycle 结算；既有 Agent/proc/resource 专项继续覆盖单路径 |
| S-12 | wait 凭据与可执行进程槽解耦；子进程退出码发布到父进程 `child_record` 后即可回收进程槽，拒绝 wait 的父进程不能占住全局进程表 | 已验证 | `struct child_record`、`proc_child_bind/publish_exit/wait_result/unbind()`、每父进程完成表容量检查 | `procreap_ucore: detached-wait=...`、`unreaped-parent-isolated=1`；延迟 wait 仍按退出顺序取得 pid 和状态 |
| S-13 | 进程与主线程必须由 generation-safe EXEC account 原子接纳；普通/保留上限及系统保留不能被 fork bomb 或账户槽复用绕过 | 已验证 | `resource_controller.[ch]`、PROCESS+THREAD vector reservation、EXEC account ACTIVE/CLOSING/DRAINING、generation handle | 当前 proc-reap、thread-resource 与三轮 Agent 套件通过 |
| S-14 | capability 必须与 active kernel-issued workflow scope 和精确对象 owner 共同命中；同能力、同名称、同 PID 或公开 span 都不能跨 scope 访问对象 | 已验证 | `VFS_SCOPE_NONE=0`、`VFS_SCOPE_SYSTEM=1`、动态 scope `>=3`，2 保留为 PUBLIC 存储 principal；metadata/dependency/action/edit/audit/prefetch/IPC 的 scope 与 stable owner 字段；metadata force reload 只替换调用者 scope | `agentscope_ucore: scope_reload_isolation=1 action_scope_isolation=1 audit_event_scope_isolation=1 lease_scope_isolation=1 ipc_scope_isolation=1` |
| S-15 | 物理 512 槽审计表按 4 个 workflow 各保留 128 条；scope 内 low/high 各 64，low principal 上限16、high active principal 上限8，其他主体不能淘汰其 protected evidence | 已验证 | scope ledger state、stable audit principal、private span owner、authority-effect 分类；high 满时仅自滚或回收 inactive principal，稀疏窗口由 `dropped_records` 说明 | `agentsecurity_ucore: trusted_span_authority=1 trusted_cause_attribution=1 audit_authority_partition=1` |
| S-16 | syscall 不能越过调度公平边界；时间片由 dispatch 建立且不能由重复 syscall 刷新，长循环按已提交工作量在安全点延迟抢占 | 已验证 | `os/kernel_work.c`、每线程 dispatch-cycle deadline/work units/pending/resumed/redispatch 状态、syscall 统一 begin/end、timer 延迟请求；console/pipe/exec/fork 分页、Agent batch 和 FD_INODE 块安全点；fork VM snapshot 调度屏障；inode 调度后短返回；truncate detach/reclaim 与 cleanup checkpoint；文件槽快照释放和 FD reservation；baseline 保持通用路径同语义 | 当前双目标 syscall-fairness 专项通过，覆盖 console、inode write、`O_TRUNC`、observer 和 worker 退出完整性 |
| S-17 | 单安装、单租户的 PUBLIC 持久存储配额绑定稳定主体，不得因短命进程资源域退出、重新创建或系统重启而清零 | 已验证 | `storage_principal_id` 与 `resource_domain_id`/workflow scope 分离；当前无 uid/tenant ABI，普通进程统一绑定安装级 principal 2；superblock 记录 principal，挂载从 qmap/dinode 重建 PUBLIC block/inode 用量并回收无目录引用的孤儿；可变 SYSTEM 赞助文件在首次修改前整体转移 owner；策略/owner 格式升级并拒绝旧镜像。按认证用户/租户拆分多个稳定 principal 属后续 ABI 扩展 | `fspquota_ucore` 已在 AgentOS 与 baseline 的同一镜像各完成 crash/seed/verify 三次启动，覆盖打开后 unlink 的显式 SIGTERM checkpoint、赞助文件接管、完整域退出、`reboot_charge_persisted`、`deletion_reuse`、`relaunch_charge_persisted` 和 `cleanup_reuse`；不宣称硬掉电注入 |
| S-18 | 全局 file object 按 EXEC account 与 ordinary/reserved class 计费；阻塞引用不能借关闭 FD 逃账，pipe 两端必须原子 admission | 已验证 | `struct file.resource_account/resource_reserved`、FILE_OBJECT vector reservation、generation-safe final refund；baseline 保留旧实现 | 当前双目标 file-resource 专项通过 |
| S-19 | 块设备 I/O 与 buffer cache 按稳定 STORAGE account 隔离；普通压力不得消耗控制/系统保留，退出与 retirement 不得遗留 lease/cache/debt | 已验证，保留测试缺口 | `resource_controller` rate lane/global pool/bundle lease；`os/bio.c`、`os/virtio_disk.c`、`os/proc.c`；stable owner/class、设备根、cache sponsor、统一 teardown settlement | 当前三轮 Agent 套件均包含 `iobudget_ucore` 并通过；多 workflow、设备故障与主动 debt 注入仍缺独立用例 |
| S-20 | THREAD usage 必须计入 EXEC account，thread bomb 不能耗尽保留容量；CPU 份额只按独立 `resource_domain_id` 调度分区轮转 | 已验证 | `resource_controller` THREAD kind、thread `resource_account`、lazy stack admission/rollback、active-domain FIFO | 当前 thread-resource 专项通过，并在同一镜像额外完成 50/50 轮压力复测 |
| S-21 | 观测计数和过滤查询必须服从内核工作预算，不能通过不复制结果或重复选择最小记录绕过域级调度公平 | 已验证 | 每 scope 的 sequence 与 `(tick, sequence)` 双有序索引；audit/span/provenance 单遍读取；timeline 对四个有序来源做四路归并；每 16 个候选预付 kernel-work，预算让出后重新读取边界并补足增长差额 | `agentscope_ucore: observe_query_bounded=1 context=128 loops=12 preemptions=64`、`observe_index_ordered=1`、`observe_cross_scope_progress=1 queries=32 latency_ms=3` |
| S-22 | workflow 必须以不可变 `(id,generation)` 标识终止谱系；Agent/VFS 降权、fork 和 exec 不能逃离撤销，槽回收不能让旧 key 指向新 workflow | 已验证 | `workflow_lifecycle.[ch]` 8 槽 ledger、proc lifecycle fields、DROP join、exec prepare/commit/abort、按 key 的 `proc_request_workflow_exit()`、terminal release | `agentscope_ucore` 的 PUBLIC lineage 通过；teardown race 进一步验证旧 key 拒绝、同 id 更高 generation 重用、factory/natural 两类退休与 fresh account |
| S-23 | lifecycle 诊断必须 self-only、版本化且不成为 bearer credential；竞态测试不得依赖裸 PCB 或任意 PID 查询 | 已验证 | syscall 546、`agent_lifecycle_abi.h` v1、sized-prefix copyout、expected-key compare、Context/metadata runtime snapshot | teardown race 三轮输出 `lifecycle_abi_prefix=1 bad_param_no_write=1 factory_charged=1 self_only_stale=1`，并核对 stale key 与 generation advancement |

S-16 验证的是已识别的可扩展长路径，不表示穷尽任意 syscall。S-13、S-18、S-19、S-20 的当前 AgentOS 实现都汇入 `resource_controller`：EXEC account 承载 process/thread/file/Agent page，STORAGE account 承载 block/inode/cache 和 BIO rate lane；`resource_domain_id` 只用于 CPU 分区。提交 `75d0dfd` 的 clean `full-verify` 已通过，包含 16-case Agent 套件和独立三轮 teardown race。S-19 的 560/280 静态 envelope 不是保护流量的运行时硬总上限，S-21 的证据也不是任意规模或 SMP 下的形式化复杂度证明；后续 metadata 拆分后的最终 HEAD 尚未完成新的 clean 聚合验收和远程 Runner 验证。

## 任务一：Agent 进程创建与地址空间设计

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T1-1 | Agent 进程能成功创建 | 已验证 | `agent_create()`、`agent_create_role()`、`agent_create_role_proc()`、`agent_make_role()`；创建前校验 role grant 和可信映像 role mask | `agentfinal_ucore` 创建 orchestrator Agent 子进程；`labdemo_ucore` 创建三类角色 Agent；`agenttrust_ucore` 验证可信映像成功、复制映像拒绝 |
| T1-2 | PCB 热字段、不可变 lifecycle key 和按需 Agent 状态正确初始化并回滚 | 已验证 | `struct proc.workflow_lifecycle_*`、`resource_account`、`agent_state_account`、`agent_context.c` reserve/allocate/revalidate/commit | 当前三轮 Agent 套件通过；`sizeof(struct proc)=28808`；9 sidecar + 6 mirror + 6 shadow 作为 21 页/`86016` B 原子计费，sidecar-only 为 `36864` B/Agent |
| T1-3 | Agent Context 区在用户地址空间中正确分配 | 已验证 | `agent_map_context()`、`AGENT_CONTEXT_BASE` | `agentfinal_ucore: context size=24576 capacity=128` |
| T1-4 | Agent 进程可直接读取 Context 镜像 | 已验证 | Agent Context 用户镜像页和内核 shadow 权威页 | `agentfinal_ucore` 读取 header/latest |
| T1-5 | 普通进程和 Agent 进程可共存，互不影响 | 已验证 | loader 从可信映像建立启动 grant；普通 fork/exec 不继承；普通进程不安装 Agent metadata/context，且不能创建 Agent 或调用敏感 Agent syscall；orchestrator 显式委派角色；普通与 workflow VFS 域彼此隔离 | `agentfinal_ucore`、`labdemo_ucore`、`agentsecurity_ucore: plain_child_role_creation_denied=1`、`agentvfs_ucore: protected_paths=1` |
| T1-6 | Agent 退出后 Context 状态、文件、VM、resource account、lifecycle 与内核栈按统一 teardown 顺序释放 | 已验证 | `proc_teardown_run()` 对 Agent 私有清理只调用 phase-aware、幂等的 `agent_proc_teardown()`；其 QUIESCING 撤权、RECLAIMING 释放/清身份、SETTLING 只验 Agent 状态/页账为空，通用 process/thread/file/I/O 账目仍由外层状态机结算；scheduler stack publish/recycle | Agent/proc/resource 专项通过；teardown race 连续三轮补充 Context/metadata waiter、阻塞 fdget、I/O debt/cache、inode/file/account 的组合释放证据 |
| T1-7 | 新 workflow 必须通过可信 factory 建立独立 scope；同 scope 子 Agent/worker 协作，普通 fork 不继承安全域 | 已验证 | syscall 541 `agent_workflow_create()`、动态 scope ref/admission、spawn scope mode | `agentscope_ucore: cross_scope_isolation=1 same_scope_collaboration=1` |
| T1-8 | pipe 端点不得随 scope、角色或并发线程成为环境权限；每个新 Agent/worker/workflow/降权普通主体仅接收创建线程经 syscall 542 显式一次性委派的精确对象 | 已验证 | `agent_scope_delegate_fd()`、线程私有 `fd_delegate_ticket[]`、默认拒绝的 file 继承类别、原子 FD 对象快照与票据消费 | `agentscope_ucore: transactional_fd_delegation=1 pipe_redelegation_isolation=1`、`agentvfs_ucore: worker_pipe_delegation=1` |
| T1-9 | workflow 根退出或可信控制面终止时，整个 lifecycle 谱系立即失权并最终回收；降权 PUBLIC 后代不能逃逸 | 已验证 | controller control id、generation-safe lifecycle key、ACTIVE/CLOSING/RETIRING、按 key 撤销、exec 原子发布和统一 teardown | `agentscope_ucore` 输出 `scope_controller_exit_revoke=1 public_lineage=1`；teardown race 同时覆盖 factory close、根自然退出、PUBLIC 谱系、退休完成与同 id 更高 generation replacement |
| T1-10 | 用户态可用版本化 self-only ABI 读取/比较本进程 lifecycle key 与 teardown runtime 状态，返回值不授予权限 | 已验证 | syscall 546、共享 `agent_lifecycle_abi.h`、`agent_workflow_lifecycle_info()` wrapper、sized-prefix 和 bad-param-before-copyout | teardown race 连续三轮验证 ABI 前缀、坏参数不写、factory charged、self-only stale、旧 key 拒绝和 generation 增长 |

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
| T3-7 | 可区分系统自动记录和手动记录，并能查询完整工具详情 | 已验证 | record flags、按活跃 Agent 分配且由 EXEC account 计费的 Context sidecar、`context_detail()` | 当前三轮 `agentfinal_ucore` 均通过 sidecar 上的 `context_detail=1` 与 record flags 断言 |
| T3-8 | Agent 有可自管的 Context cache，且不影响内核可信历史 | 扩展增强 | `agent_context_header.user_cache_offset/user_cache_size`，snapshot 只刷新内核管理区 | `agentfinal_ucore: user_cache_preserved=1` |
| T3-9 | 工具调用、手动记录和事件消费可形成轻量因果链，跨 Agent cause/span 由私有 source control/span owner 认证 | 已验证 | `agent_context_record.cause_sequence/span_id`、私有 cause sidecar、事件消费继承可信 owner；`context_push` 拒绝用户非零 cause/span | `agentsecurity_ucore: trusted_span_authority=1 trusted_cause_attribution=1` |
| T3-10 | Context 摘要和调度原因可合并为运行轨迹 | 扩展增强 | `agent_trace_snapshot()`、`struct agent_trace_record` | `agentfinal_ucore: runtime_trace=1 records=... context=1 sched=1 wait=1` |
| T3-11 | 当前 span 的系统级短记录可被参与 Agent 查询 | 扩展增强 | `agent_span_trace_snapshot()`、`struct agent_audit_record`、当前 `span_id` 过滤 | `agentfinal_ucore: span_trace=1 records=... context=1 event=1` |
| T3-12 | Context、调度、审计和预取提示可统一导出 | 扩展增强 | `agent_timeline_snapshot()`、`struct agent_timeline_record`、来源字段 `source` | `agentfinal_ucore: unified_timeline=1 records=... context=1 sched=1 audit=1 prefetch=1` |
| T3-13 | 统一 timeline 可由内核按条件过滤和按游标增量读取 | 扩展增强 | `agent_timeline_query()`、`struct agent_timeline_filter`、source mask、start tick、span/pid/kind/status/flags 过滤、`after_tick/source/sequence` 游标过滤 | `agentfinal_ucore: timeline_query=1 audit=213 recent=281 cursor=177` |
| T3-14 | Context Path 可验证相邻记录顺序 | 扩展增强 | `agent_context_record.prev_hash`、`agent_context_record.record_hash`、`agent_context_header.latest_record_hash`、rollback/clear 同步链尾 hash | `agentfinal_ucore: context_integrity=1` |
| T3-15 | 每 workflow scope 的稀疏审计窗口可用摘要和逻辑 hash 链校验 | 已验证 | scope-local `prev_hash/record_hash/ledger_hash`、全局单调 sequence、`dropped_records=total-visible`；相邻可见记录只在无 gap 时直连 | `agentfinal_ucore` 用 dropped 解释可见链缺口的回归已通过 |
| T3-16 | 同一进程的并发工具、Context、IPC、文件和等待归因按单一提交顺序发布 | 已验证 | 可睡眠、FIFO、可重入 Context commit lane；锁序 `lane -> metadata`；`agent_call_count` 为已接纳/保留序号，`latest_sequence` 为已提交水位 | `agentfinal_ucore: context_commit_lane=1 sequence=1..3 hash=1` |

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
| T4-26 | metadata 内存表、索引、inode sidecar、依赖和双 bank 提交形成统一并发事务，查询不得观察半提交状态 | 已验证，保留测试缺口 | `agent_metadata.c` 持 FIFO transaction/projection gate；`agent_file_state.c` 持 incarnation sidecar；catalog/query/scan/directory/objects/store 各持 live catalog、查询、根扫描、VFS 协调、对象动作/依赖和 COW 持久状态；进程态扩展扫描每 128 records 计入 kernel-work，submit lane 保持 immutable COW job | `75d0dfd` checkpoint 验证 file-state/catalog 和总体事务；最终拆分提交 `14a9450` 已完成 owner/零 BSS/依赖门及 `agentfs`、`agentscan`、`agentvfs`、teardown race 三轮定向 QEMU；仍待 clean `full-verify` |
| T4-27 | PUBLIC 持久存储计费必须跨完整进程域退出和磁盘重挂载保持，删除后才能归还并复用容量 | 已验证，保留测试缺口 | 安装级 PUBLIC principal 2、VFS 独立计费凭据、qmap/dinode 持久 owner、挂载孤儿清扫与 PUBLIC 用量重建；SYSTEM 赞助对象先在固定工作区收集/排序全部块，按 qmap block 分组预检，一次预留后由 claim gate 保持 qmap-first、inode-last 前向提交，挂载继续部分 claim | 当前 fs-enospc 双目标 generic/domain/reserve/persistent/orphan/reclaim/verify 全部通过；grouped claim 中点掉电仍缺故障注入 |
| T4-28 | 低权限 workflow 的高频微小文件变化不得同步放大全局 metadata I/O，也不得阻断另一 workflow 的查询服务；可信 metadata 必须在用户进程发布前加载，无可验证有效 bank 时拒绝以空状态继续授权 | 已验证，保留故障注入缺口 | `timer_init()` 后、`bio_policy_start()`/`load_init_app()` 前调用 `agent_storage_init()`；单个 bank 损坏时选择另一有效副本并标记恢复，无有效 bank 或选择失败时设置 `agent_meta_store_failed_closed`，metadata API 拒绝；scope VFS-labelled 清理仍可退休；运行期使用 inode sidecar、PERSIST scope-local 固定窗口写回、primary-then-mirror COW、触发 scope `BACKGROUND` budget 和 scope-local query cache generation | 历史 metadata/I/O 修复完整轮中 `agentscope_ucore` 通过微写合并、跨 scope 查询、volatile 分流、scan-pressure、最终 reload 一致性、生命周期等标记并输出 `parent passed`，`elapsed=139.9s`；未动态注入启动 bank 损坏、VirtIO 错误、短写或掉电 |
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
| T5-10 | 调度器感知 Agent 角色、事件状态和受权配置，但软分值不能越过资源域外层轮转或域内强制公平上限 | 已验证 | `fetch_task()` 先轮转 `scheduler_active_domains`，再在 `scheduler_domain_tasks[]` 中调用域内评分；role weight、priority、budget、event/deadline/heartbeat/vruntime scoring 与每域 `AGENT_SCHED_MAX_AGENT_BURST=8` FIFO escape | 当前三轮 `agentsched_ucore` 与 thread-resource/proc-reap 通过 |
| T5-11 | 受权 Agent 可取消其受控 Agent 的等待 | 扩展增强 | 每个 Agent 使用内核私有且不复用的 64 位 control id；创建时绑定直接 controller，合法 `agent_wait_cancel()` 写一次性令牌，目标返回 `AGENT_STATUS_CANCELLED` 并追加 Context | `agentloop_ucore: wait_cancel=1`、`agentsecurity_ucore: wait_cancel_scope=1 wait_cancel_controller_lifecycle=1` |
| T5-12 | Agent 最近调度原因可查询 | 扩展增强 | `agent_sched_snapshot()`、`struct agent_sched_record`、reason flags | `agentsched_ucore: reason_trace=1` |
| T5-13 | 调度原因能和 Context 历史一起供 Agent 查询 | 扩展增强 | `agent_trace_snapshot()` 按 tick 合并 Context 与调度记录 | `agentfinal_ucore: runtime_trace=1 ... sched=1` |
| T5-14 | 同一 workflow 多 Agent 的 Context、事件、调度和预取交接摘要可由本 scope orchestrator 查询 | 已验证 | 物理 512 条审计表、每 scope 128 条、scope-local ledger；同 scope span 提示可由参与 Agent 查询 | `labdemo_ucore` 同 workflow 场景和 `agentscope_ucore` 跨 scope 隔离断言均通过 |
| T5-15 | 多 Agent scope-local 短记录可按条件过滤且 filter 不扩大对象范围 | 已验证 | `agent_audit_query()` 先按 scope/private owner 裁剪，再应用 filter flags | 既有过滤回归与 `agentscope_ucore: audit_event_scope_isolation=1` 均通过 |
| T5-16 | 非 orchestrator 参与者可读取当前可信 span 的协作短记录 | 已验证 | `agent_span_trace_snapshot()` 匹配 scope + current span id + private span owner，普通进程返回 `-1` | 同 workflow span trace 与 `agentsecurity_ucore` foreign/forged span 负向断言均通过 |
| T5-17 | 状态页面可读取、过滤并增量刷新统一运行时间线 | 扩展增强 | `agent_timeline_snapshot()` 把 Context、调度、可见审计和预取提示规范化为同一结构，`agent_timeline_query()` 在可见集合上过滤，并支持 `tick/source/sequence` 游标；Context 审计记录保留工具结果数值槽，可承载内容摘要证据 | `labdemo_ucore: unified_timeline records=... context=1 event=1 sched=1 prefetch=1 digest=1`、`labdemo_ucore: timeline_query prefetch=3 cursor=... digest=1`、`agentsecurity_ucore` 普通进程拒绝 |
| T5-18 | 状态页面可读取当前可见因果关系 | 扩展增强 | `agent_provenance_snapshot()` 把 Context、审计和预取提示转换成 source/target 因果边，并沿用当前 Agent 可见范围；内容摘要工具调用也能作为可见因果边导出 | `agentfinal_ucore: provenance_graph=1 edges=126 context=1 audit=1`、`labdemo_ucore: provenance_graph edges=... message=1 prefetch=1 digest=1`、`agentsecurity_ucore` 普通进程拒绝 |
| T5-19 | 状态页面或 Agent worker 可等待新 timeline 记录 | 扩展增强 | `agent_timeline_wait()` 复用 timeline filter，在无匹配记录时睡眠，由 observe epoch、timeout 或新记录唤醒；等待 filter 保存在 PCB 中，写入新记录时按完整 filter 判断 source、event、status、tool、span、pid 和 flags 是否匹配；`agent_timeline_read()` 可在同一次 syscall 中等待并复制记录 | `agentfinal_ucore: timeline_wait=1 timeout=-7 source_gate=1 event_gate=1 wake=1 query=1 read=1 sleeps=1 wakeups=1`、`agentbench_ucore: timeline_wait_ready ...`、`agentsecurity_ucore` 普通进程拒绝 |
| T5-20 | 普通资源域在其他域持续运行任意数量、高软分值 Agent 线程时仍能在有限域轮次内运行 | 已验证 | active-domain FIFO 中每域至多一个节点并严格轮转；域内 `scheduler_agent_burst == 8` 强制选普通线程，`scheduler_score_burst == 8` 强制选 FIFO 队首；这些计数按域保存，weight、priority、budget、事件积压、角色和线程数均不能修改外层轮转 | 当前 `threadresource_ucore` 通过 `domain_fairness ... victim=512 bound=576`，并完成同镜像 50/50 轮压力复测 |
| T5-21 | 低权限 Agent 不能把 `MESSAGE_SEND` 扩大成任意 PID 或跨 workflow 通道 | 已验证 | stable control id route、same-active-scope 强制检查、target consent 同样受 scope 限制；所有直接投递入口统一鉴权 | route grant/revoke/生命周期标记和 `agentscope_ucore: ipc_scope_isolation=1` 跨 scope consent/投递拒绝均通过 |
| T5-22 | 单个来源、单一外部事件类别或慢 watcher 不能耗尽关键事件资源或阻断其他订阅者 | 已验证，保留测试缺口 | 同一 stable source 跨类 4 条、directed/attributed 各 8 条、external 合计 12 条，并为内核 origin 保留至少 4 个容量名额；广播逐 watcher 继续投递，权威 metadata 提交不接收单一通知队列背压 | `message_source_limit=4`、`ipc_class_limit=8`、`external_limit=12`、`system_event_reserved=4`、`external_reject_reclaim=1`、`broadcast_slow_watcher_isolated=1`；尚未专项覆盖 attributed=8、同一来源混合跨类、满队列与 wait cancel 组合、LLM_DONE 配额压力和 metadata 提交返回值 |
| T5-23 | audit/span/timeline/provenance 的计数、过滤和增量读取不能形成无预算全表重扫 | 已验证 | scope-local 双有序索引、单遍 scan、timeline 四路归并和候选预付预算；不增加公共 telemetry ABI，也不暴露其他 scope 的查询负载 | `observe_query_bounded=1`、`observe_index_ordered=1`、`observe_cross_scope_progress=1` |

## 任务六：综合示例与创新

| ID | 赛题方向 | 当前状态 | 说明 |
| --- | --- | --- | --- |
| T6-1 | 综合示例程序 | 已验证 | `labdemo_ucore` 以科研 Agent 平台为示例负载，串联任务一至五，输出 `agentos:event`，读取真实 align 日志内容摘要，并查询、过滤本 workflow 的审计短记录和统一 timeline |
| T6-2 | 性能和计时示例程序 | 已验证 | `agentbench_ucore` 输出批量工具、Context、文件查询性能，以及轮询/事件等待计时观测；`labbench_ucore` 作为示例规划入口包装运行 |
| T6-3 | 权限与稳定性负向示例程序 | 已验证 | `agentsecurity_ucore` 验证角色、工具和系统事件授权；`agenttrust_ucore` 验证可信映像绑定；`agentvfs_ucore` 验证普通路径不能绕过文件 capability；`usersafety_ucore`、`fsenospc_ucore` 和 `procreap*_ucore` 验证恶意输入与资源耗尽；`workflow_teardown_race_ucore` 独立验证跨资源 teardown/lifecycle 组合竞态 |
| T6-4 | LLM-friendly template relay | 已验证 | 内核提供 `llm_request` / `llm_response` 工具、`AGENT_EVENT_LLM_DONE`、`LLM_RELAY` capability、Context/timeline/audit 记录；真实云端调用放在用户态或宿主机 relay | `agentllm_ucore: template_relay=1`、`agentfinal_ucore: llm_template_relay=1` |
| T6-5 | 页面查看与图表查看 | 已验证 | `make reader` 启动本地页面服务；`results/latest/monitor.html`、`reader-guide.html`、`index.html`、`charts/*.svg` 呈现双目标运行、测试入口、实验图表和 AgentOS 证据 | Reader E2E 在 `75d0dfd` checkpoint 的 clean `full-verify` 通过；action runner 以 clean/build/guest 阶段化执行，构建路径含 `panic` 不误报、真实 Guest panic 必须失败 |
| T6-6 | 查询历史驱动的预测性预取 | 部分实现 | 当前实现文件 metadata 预取提示，覆盖同一 scope/run 的对象标签依赖；综合示例中 message 入队时内核把 sentinel 的提示交接给同 scope investigator，并写入该 scope 的可信 span 提示分区；尚未做文件内容预加载或通用预测器 | `agentfs_ucore: prefetch_hints=1`、`agentbench_ucore: file_prefetch_snapshot ...`、`agentfinal_ucore: span_prefetch=1`、`labdemo_ucore: sentinel prefetch_hint ...`、`labdemo_ucore: investigator handoff_prefetch ...`、`labdemo_ucore: investigator span_prefetch ...`、`agentos:event type=PREFETCH_USED ...` |

## 追踪结论

任务一至三已有增强实现；授权凭据与不可变 lifecycle key 分离，PUBLIC 降权、fork 和 exec 不再改变终止谱系。syscall 546 只提供 self-only 身份/运行状态比较，key 不是 bearer credential。进程、线程、文件、存储、缓存、I/O 与 Agent 私有页统一映射到 generation-safe EXEC/STORAGE account，CPU 公平仍由独立调度 domain 实现。`os/agent.c` 已收敛为 facade，metadata 又分为 transaction、file state、catalog、query、scan、directory、objects 与 store。CI 的 owner/bridge/dependency 集合和 fail-closed 自测均以版本化配置/源码为准，metadata control plane 另受 source/text/BSS 聚合预算，防止横向迁移绕过增长门禁。

2026-07-26 的 `75d0dfd` checkpoint 已完成一次 clean `full-verify`，包含 Reader E2E、16-case Agent 套件和独立三轮 workflow teardown race；2026-07-25 的三轮 16/16 时间仍保留为历史校准。checkpoint 后的 query/scan/directory 拆分尚未在最终 HEAD 重新完成 clean `full-verify`，远程普通/QEMU Runner 也没有成功证据，因此当前状态不是最终发布验收全绿。
