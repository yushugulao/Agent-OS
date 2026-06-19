# 赛题要求追踪表

本文按 ISO/IEC/IEEE 29148 的需求可追踪思想裁剪编写。每条需求都给出来源、当前状态、实现位置、验证证据和剩余缺口。

## 状态定义

| 状态 | 含义 |
| --- | --- |
| 已验证 | 已有实现和用户态测试输出证明 |
| 部分实现 | 有基础能力，但未覆盖赛题完整语义 |
| 未实现 | 尚无可验收实现 |
| 文档待补 | 功能存在，但评审材料仍需补强 |

## 总体交付要求

| ID | 赛题要求 | 状态 | 实现/材料 | 验证证据 |
| --- | --- | --- | --- | --- |
| G-1 | 在教学操作系统内核中实现 Agent-OS 功能模块 | 已验证 | `os/agent.c`、`os/agent.h`、`os/proc.c` | `agentfinal_ucore`、`agentbench_ucore`、`labdemo_ucore`、`agentsecurity_ucore` |
| G-2 | 系统可在 QEMU 上运行 | 已验证 | `Makefile`、`nfs/fs.img` | `scripts/run-agent-tests.sh` |
| G-3 | 提供内核代码 | 已验证 | `os/` | Git 仓库源码 |
| G-4 | 提供用户态测试程序 | 已验证 | `agentfinal_ucore`、`agentfs_ucore`、`agentloop_ucore`、`agentbench_ucore`、`labbench_ucore`、`labdemo_ucore`、`agentsecurity_ucore` | [verification.md](verification.md) |
| G-5 | 提供综合演示场景 | 已验证 | `user/src/labdemo_ucore.c` | `labdemo_ucore: passed` |
| G-6 | 提供设计文档和运行说明 | 已验证 | [../README.md](../README.md)、[design.md](design.md)、[demo-script.md](demo-script.md) | 本文档、[verification.md](verification.md) |
| G-7 | 保留代表性的 uCore 基础 syscall 兼容性 | 已验证 | `SYS_trace`、`SYS_mailread`、`SYS_mailwrite` | `ch3_trace` 输出 `Test trace OK!`；`agentsecurity_ucore: mail_basic=1` |

## 任务一：Agent 进程创建与地址空间设计

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T1-1 | Agent 进程能成功创建 | 已验证 | `agent_create()`、`agent_create_role()`、`agent_create_role_proc()`、`agent_make_role()` | `agentfinal_ucore` 创建 orchestrator Agent 子进程；`labdemo_ucore` 创建三类角色 Agent |
| T1-2 | PCB 扩展字段正确初始化 | 已验证 | `struct proc` Agent 字段、`agent_role`、`agent_capability_mask`、`agent_clear_metadata()`、`agent_make_role()` | `agent_info()`、`agentfinal_ucore`、`agentsecurity_ucore` |
| T1-3 | Agent Context 区在用户地址空间中正确分配 | 已验证 | `agent_map_context()`、`AGENT_CONTEXT_BASE` | `agentfinal_ucore: context size=20480 capacity=128` |
| T1-4 | Agent 进程可直接读取 Context 镜像 | 已验证 | Agent Context 用户镜像页和内核 shadow 权威页 | `agentfinal_ucore` 读取 header/latest |
| T1-5 | 普通进程和 Agent 进程可共存，互不影响 | 已验证 | 普通父进程创建并等待 Agent 子进程；普通进程不安装 Agent metadata/context，且不能直接调用敏感 Agent syscall；pid 1 的普通直接子进程可创建 orchestrator，支持 usershell 手动测试路径 | `agentfinal_ucore`、`labdemo_ucore`、`agentsecurity_ucore: plain_child_orchestrator=1` |
| T1-6 | Agent 退出后资源能释放 | 已验证 | `agent_free_proc_context()`、`freeproc()` | 三个最终测试均正常退出 |

## 任务二：Agent 与内核结构化交互

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T2-1 | 用户态 Agent 测试程序能成功调用至少 3 个内核工具 | 已验证 | `agent_tools[]` 19 个工具、`agent_run()` | `agentfinal_ucore` 批量调用 echo；`labdemo_ucore` 调用 query_file、summary、dependency、capability、rerun、write_report、send_message |
| T2-2 | 每个工具请求和响应均为结构化格式 | 已验证 | `struct agent_op`、`struct agent_result`、`struct agent_tool_desc` | `agentfinal_ucore`、`agentbench_ucore` |
| T2-3 | 提供工具列表及参数说明 | 已验证 | `agent_tool_list()`、`agent_tools[]`、tool flags | [api.md](api.md) 工具表 |
| T2-4 | 工具调用结果可写入 Agent Context | 已验证 | `agent_append_context()` 写 shadow 权威页并同步用户镜像 | `agentfinal_ucore` 读取 latest 和 snapshot |
| T2-5 | 错误路径有明确返回 | 已验证 | `AGENT_STATUS_*`、工具执行状态码、真实 role/capability 授权、legacy 工具 ID/名称一致性检查 | `labdemo_ucore` 验证 denied 和 duplicate；`agentsecurity_ucore` 验证伪造 role 被拒绝和 `legacy_tool_mismatch=1` |
| T2-6 | 工具解析性能优化 | 扩展增强 | `agent_run()` 批量执行、ID 分发 | `agentbench_ucore` |
| T2-7 | 敏感工具授权不信任用户态自报 role | 扩展增强 | `capability_check`、`rerun_stage`、`write_report` 均读取当前 PCB capability | `agentsecurity_ucore: sentinel spoof_denied=1` |
| T2-8 | legacy `tool_id` 和 `tool_name` 不一致时拒绝执行 | 扩展增强 | `sys_agent_call()` 先校验 ID 对应工具名，错误时返回 `AGENT_STATUS_BAD_REQUEST` 和 `tool_mismatch` | `agentsecurity_ucore: legacy_tool_mismatch=1` |
| T2-9 | legacy 参数键/类型错误被拒绝，syscall-only 工具不能走 batch | 扩展增强 | legacy 参数校验、`AGENT_TOOL_F_SYSCALL_ONLY` | `agentsecurity_ucore: legacy_param_validation=1 syscall_only=1` |
| T2-10 | 工具名称 + 参数键值列表协议可作为正式入口 | 已验证 | `agent_call()` 支持 name-only 请求和 key/type 校验 | `agentfinal_ucore: legacy_name_protocol=1` |

## 任务三：上下文路径管理

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T3-1 | Agent 测试程序执行 5 轮以上连续工具调用 | 已验证 | `agentfinal_ucore` 批量调用 | 连续 192 个 op |
| T3-2 | 系统正确维护多轮上下文路径 | 已验证 | shadow 权威 `agent_context_record[128]` 环形记录，record 含 16 字节 payload/result 摘要 | `agentfinal_ucore: short_text_history=1` |
| T3-3 | Agent 可直接从 Context 区高速读取路径数据 | 已验证 | Agent Context 用户镜像可读；`context_snapshot()` 可刷新并返回可信 shadow 历史 | `agentbench_ucore: direct_context`、`agentfinal_ucore` |
| T3-4 | 路径超长时自动淘汰，不导致内核 OOM | 已验证 | 固定容量 FIFO 环形覆盖 | `agentfinal_ucore: fifo oldest=66 latest=193 dropped=65` |
| T3-5 | 支持批量上下文快照和 rollback | 已验证 | `context_snapshot`、`context_query`、`context_rollback`、`context_clear` | `agentfinal_ucore`、`agentbench_ucore` |
| T3-6 | 用户态篡改 Context 镜像不影响内核权威历史 | 扩展增强 | kernel shadow + user mirror | `agentfinal_ucore: tamper_protected=1` |
| T3-7 | 可区分系统自动记录和手动记录，并能查询完整工具详情 | 扩展增强 | record flags、detail ring、`context_detail()` | `agentfinal_ucore: record_flags system=1 manual=1 truncated=0`、`context_detail=1` |
| T3-8 | Agent 有可自管的 Context cache，且不影响内核可信历史 | 扩展增强 | `agent_context_header.user_cache_offset/user_cache_size`，snapshot 只刷新内核管理区 | `agentfinal_ucore: user_cache_preserved=1` |

## 任务四：面向 Agent 查询优化的文件系统扩展

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T4-1 | Agent 可按文件属性查询实验工件 | 已验证 | `struct agent_file_meta`、`agent_file_query()`、`AGENT_TOOL_QUERY_FILE` 属性 payload | `labdemo_ucore: tool=query_file hits=1 used_index=1` |
| T4-2 | 支持项目、工作流、运行、阶段、类型、状态、摘要、逻辑路径字段 | 已验证 | `os/agent.h` 中 `agent_file_meta` / `agent_file_query` | [task4-file-query.md](task4-file-query.md)、`labdemo_ucore` |
| T4-3 | 有扫描路径和索引路径 | 已验证 | `agent_file_query()`、status/stage/kind 索引桶 | `agentfs_ucore: bulk_index scan=108 index=6 hits=1`、`agentfs_ucore: scan_index_consistent=1`、`agentbench_ucore: file_scan_query/file_index_query` |
| T4-4 | 查询结果包含命中、截断、扫描数、是否使用索引和 tick | 已验证 | `struct agent_file_query_result` | `agentfs_ucore: truncated_query total=100 returned=3 truncated=1`、`agentbench_ucore` 性能表 |
| T4-5 | 支持依赖关系查询，服务最小恢复 | 已验证 | `AGENT_TOOL_DEPENDENCY_QUERY`、dependency mask | `labdemo_ucore: affected stages=align+analyze+report+archive` |
| T4-6 | 查询写入 Context Path，可用于报告回放 | 已验证 | 文件查询和工具调用均追加 Context | `labdemo_ucore`、`context_snapshot` |
| T4-7 | 文件元数据写入只能由具备权限的 Agent 执行 | 扩展增强 | `agent_file_meta_init()`、`agent_file_meta_set()` 要求 Agent 且具备 `AGENT_CAP_META_WRITE` | `agentsecurity_ucore: plain_process_denied=1`、`sentinel meta write denied` |
| T4-8 | 索引初始化前查询安全 | 扩展增强 | `agentinit()` 初始化 status/stage/kind 索引桶为 `-1` | `agentsecurity_ucore: preinit_index_query=1` |
| T4-9 | 多 run 恢复和报告写入只修改目标 run | 扩展增强 | `rerun_stage` 和 `write_report` 支持 `stage=...;run_id=...;project=...` selector | `agentsecurity_ucore: scoped_rerun=1`、`agentsecurity_ucore: scoped_report=1` |
| T4-10 | 文件元数据绑定真实 uCore 根目录 inode | 已验证 | `agent_fs_note_create/write/truncate/delete()`、`dev + inum` 主键 | `agentfs_ucore: default_inode`、`agentfs_ucore: custom_inode` |
| T4-11 | 元数据可写入并重新加载 | 已验证 | 私有 `.agentmeta` 固定格式元数据文件 | `agentfs_ucore: .agentmeta_reload=1` |
| T4-12 | 普通文件 syscall 不能直接访问 Agent 元数据后端 | 已验证 | `fileopen()` / `fileunlink()` 对 `.agentmeta` 返回 `-1` | `agentsecurity_ucore: .agentmeta_protected=1` |
| T4-13 | 对真实磁盘目录做后台持续扫描 | 未实现 | 无 | 后续增强方向 |

## 任务五：Agent Loop 内核运行机制

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T5-1 | Agent 可注册和删除 watch | 已验证 | `agent_watch()`、`agent_unwatch()`、`AGENT_WATCH_MAX=8` | `labdemo_ucore: WATCH_REGISTERED`、`agentloop_ucore: unwatch=1` |
| T5-2 | Agent 可等待事件并 timeout | 已验证 | `agent_wait()`、`AGENT_STATUS_TIMEOUT`、`timeout_count`、有限 timeout 睡眠等待路径 | `agentbench_ucore: timeout_heartbeat=1`、`agentloop_ucore: timeout_sleep_no_poll=1` |
| T5-3 | 文件状态变化能唤醒目标 Agent | 已验证 | `agent_file_meta_set()` 投递包含 status、stage、run_id、project 的 `AGENT_EVENT_FILE_STATUS` | `labdemo_ucore` sentinel 收到 failed 事件 |
| T5-4 | 消息能触发 Agent 事件 | 已验证 | `send_message` 工具、`agent_wake()` | `labdemo_ucore` sentinel->investigator、investigator->recovery |
| T5-5 | 心跳字段可设置、可按 TIMER watch 投递事件、可停止 | 已验证 | `agent_heartbeat()`、`agent_heartbeat_stop()`、TIMER watch/unwatch | `agentbench_ucore: timeout_heartbeat=1`、`agentloop_ucore: timer_unwatch=1`、`agentloop_ucore: heartbeat_wake_stop=1` |
| T5-6 | busy polling 和 event wait/wake 可计时观测并稳定完成 | 已验证 | `agentbench_ucore` | `agentbench_ucore: busy_poll_query`、`agentbench_ucore: event_wait_wake`、`agentbench_ucore: busy_poll_vs_wait`；不设置固定 tick 阈值 |
| T5-7 | 事件处理写入 Context Path | 已验证 | `agent_wait()` 成功消费事件后追加 Context | `labdemo_ucore` 和 [task5-agent-loop.md](task5-agent-loop.md) |
| T5-8 | 普通进程不能直接伪造事件 | 扩展增强 | `agent_wake()` 要求 Agent 且具备 `MESSAGE_SEND` 或 `ORCHESTRATE` | `agentsecurity_ucore` 普通进程调用 `agent_wake()` 返回 `-1` |
| T5-9 | 事件队列满时拒绝新事件且不覆盖旧事件 | 扩展增强 | `AGENT_EVENT_QUEUE_CAP=16`、FIFO queue | `agentloop_ucore: overflow_dropped=1` |

## 任务六：综合演示与创新

| ID | 赛题方向 | 当前状态 | 说明 |
| --- | --- | --- | --- |
| T6-1 | 综合演示程序 | 已验证 | `labdemo_ucore` 串联任务一至五，输出 `agentos:event` |
| T6-2 | 性能和计时演示程序 | 已验证 | `agentbench_ucore` 输出批量工具、Context、文件查询性能，以及轮询/事件等待计时观测；`labbench_ucore` 作为演示规划入口包装运行 |
| T6-3 | 权限限制演示程序 | 已验证 | `agentsecurity_ucore` 输出普通进程拒绝、usershell 等价启动路径、初始化前索引查询、legacy mismatch、sentinel 伪造拒绝、recovery 幂等恢复和定向恢复 |
| T6-4 | 云端 LLM Gateway | 未实现 | 当前保留结构化事件、模板 `LLM_CALL` / `LLM_RESULT`、referenced sequence、plan 和 corr_id 字段，尚未接真实云端 LLM |
| T6-5 | 可视化大屏 | 未实现 | 当前已输出 `agentos:event`，大屏解析器尚未实现 |

## 追踪结论

任务一至三已有增强实现和测试证据，并且在 Context 容量、批量工具调用、Context shadow 可信历史、用户自管 cache、detail 查询、snapshot 查询和性能测试方面高于最小要求。任务四已经实现真实 inode 关联、私有 `.agentmeta` 元数据文件、属性查询和索引查询；任务五已经实现 16 槽 FIFO 事件队列、watch/unwatch、有限 timeout 睡眠等待和 heartbeat wake/stop。当前主要短板在后台目录扫描、事件优先级和取消等待、云端 LLM Gateway、可视化大屏、演示视频和答辩材料。
