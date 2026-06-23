<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

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
| G-1 | 在教学操作系统内核中实现 Agent-OS 功能模块 | 已验证 | `kernel/agent.c`、`kernel/agent.h`、`kernel/proc.c` | `agentfinal`、`agentbench` |
| G-2 | 系统可在 QEMU 上运行 | 已验证 | `Makefile`、`fs.img` | `make qemu` 后运行用户态测试 |
| G-3 | 提供内核代码 | 已验证 | `kernel/` | Git 仓库源码 |
| G-4 | 提供用户态测试程序 | 已验证 | `user/labdemo.c`、`user/labbench.c`、`user/agentfinal.c`、`user/agentbench.c`、`user/agentcall.c`、`user/contexttest.c`、`user/agentstress.c`、`user/agentexec.c` | [verification.md](verification.md) |
| G-5 | 提供综合演示场景 | 已验证 | `user/labdemo.c` | `labdemo: passed` |
| G-6 | 提供设计文档和运行说明 | 已验证 | [../README.md](../README.md)、[design.md](design.md)、[demo-script.md](demo-script.md)、[task4-file-query.md](task4-file-query.md)、[task5-agent-loop.md](task5-agent-loop.md) | 本文档、[verification.md](verification.md) |

## 任务一：Agent 进程创建与地址空间设计

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T1-1 | Agent 进程能成功创建 | 已验证 | `agent_create()`、`agent_fork()`、`agent_make()` | `agentfinal` |
| T1-2 | PCB 扩展字段正确初始化 | 已验证 | `struct proc` Agent 字段、`agent_clear_metadata()`、`agent_make()` | `agent_info()`、`agentfinal` |
| T1-3 | Agent Context 区在用户地址空间中正确分配 | 已验证 | `agent_map_context()`、`AGENT_CONTEXT_BASE` | `agentfinal: context size=16384 capacity=128` |
| T1-4 | Agent 进程可直接读写 Context | 已验证 | Agent Context 用户镜像页和内核 shadow 权威页；直接读是高速镜像，可信历史以 snapshot 为准 | `agentfinal: direct_context_match=1`、`agentfinal: direct_dirty_before_snapshot=1`、`agentfinal: tamper_protected=1` |
| T1-5 | 普通进程和 Agent 进程可共存，互不影响 | 已验证 | 普通父进程创建并等待 Agent 子进程；普通进程不安装 Agent metadata/context；父进程堆越过 Agent Context 起点时拒绝创建 Agent | `agentfinal`、`agentstress: parent_over_context_rejected=1` |
| T1-6 | `exec`/`exit` 后生命周期稳定 | 扩展增强 | `exec.c` 延迟安装 Context 指针，`proc.c` 释放路径 | `agentexec`、`agentstress: exec_failure_preserved=1` |

## 任务二：Agent 与内核结构化交互

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T2-1 | 用户态 Agent 测试程序能成功调用至少 3 个内核工具 | 已验证 | `agent_tools[]` 18 个工具、`agent_run()` | `agentfinal` 批量调用 4 类工具；`labdemo` 调用 query_file、summary、dependency、capability、rerun、write_report、send_message |
| T2-2 | 每个工具请求和响应均为结构化格式 | 已验证 | `struct agent_op`、`struct agent_result`、`struct agent_tool_desc` | `agentfinal`、`agentbench` |
| T2-3 | 提供工具列表及参数说明 | 已验证 | `tool_list()`、`agent_tool_list()` | `tool_list: total=18` |
| T2-4 | 工具调用结果可写入 Agent Context | 已验证 | `agent_append_context()` 写 shadow 权威页并同步用户镜像 | `agentfinal` 读取 latest 和 snapshot |
| T2-5 | 错误路径有明确返回 | 扩展增强 | `AGENT_STATUS_*`、legacy 键名/类型校验、输出指针 writable-prefault 预检、Context 空间错误返回 `NO_SPACE` | `agentcall` 验证未知工具、坏版本、坏参数、普通进程调用、坏输出指针无副作用、lazy 输出页可用 |
| T2-6 | 工具解析性能优化 | 扩展增强 | `agent_run()` 批量执行、ID O(1) 查找、legacy name 兼容查找 | `agentbench` |

## 任务三：上下文路径管理

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T3-1 | Agent 测试程序执行 5 轮以上连续工具调用 | 已验证 | `agentfinal` 批量调用 | 连续 192 个 op |
| T3-2 | 系统正确维护 128 条短文本摘要路径 | 已验证 | shadow 权威 `agent_context_record[128]` 环形记录，record 含 16 字节 payload/result 摘要 | `agentfinal: short_text_history=1`、`contexttest: short_text_history=1` |
| T3-3 | Agent 可直接从 Context 区高速读取路径数据 | 已验证 | Agent Context 用户镜像可读；`context_snapshot()` 可刷新并返回可信 shadow 历史 | `agentbench: direct_context`、`agentfinal: direct_context_match=1` |
| T3-4 | 路径超长时自动淘汰，不导致内核 OOM | 已验证 | 固定容量 FIFO 环形覆盖 | `agentfinal: fifo oldest=65 latest=192 dropped=64` |
| T3-5 | 支持批量上下文快照和可区分 rollback 错误 | 扩展增强 | `context_snapshot`、`context_query`、`context_rollback`；rollback 只裁剪可见历史，不复用旧 sequence | `agentfinal`、`agentbench`、`contexttest: rollback_not_found=-5`、`branch_latest=131` |

## 任务四：Agent 子系统内存元数据表版本的文件查询扩展

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T4-1 | Agent 可按文件属性查询实验工件 | 已验证 | `struct agent_file_meta`、`agent_file_query()`、`AGENT_TOOL_QUERY_FILE` 属性 payload | `labdemo: query_file ... status=failed hits=1` |
| T4-2 | 支持 fid、项目、工作流、运行、阶段、类型、状态、摘要、逻辑路径字段 | 已验证 | `kernel/agent.h` 中 `agent_file_meta` / `agent_file_query` | [task4-file-query.md](task4-file-query.md)、`labdemo`、`labbench: file_semantics ... fid=1` |
| T4-3 | 有扫描路径和索引路径 | 已验证 | `agent_file_query_locked()`、status/run_id/stage/kind 索引桶和最短候选桶选择 | `labbench: file_scan_query`、`labbench: file_index_query`、`file_semantics ... report_scanned=9` |
| T4-4 | 查询结果包含命中、截断、扫描数、是否使用索引和 tick | 已验证 | `struct agent_file_query_result` | `labbench` 性能表 |
| T4-5 | 支持依赖关系查询，服务最小恢复 | 已验证 | `AGENT_TOOL_DEPENDENCY_QUERY`、metadata `dependency_mask` 优先，`rerun_stage` 按 mask 更新 selector 指定运行内的阶段 | `labdemo: affected stages=align+analyze+report+archive`、`labbench: metadata_dependency=1 scoped_rerun=1 scoped_report=1 history_preserved=1 single_report=1 mask_text=1 dep_clear=1 insert=1 delete=1` |
| T4-6 | 查询写入 Context Path，可用于报告回放 | 已验证 | `agent_file_query()` 调用 `agent_append_system_context()`，工具路径自动记录 | `labdemo`、`context_snapshot` |
| T4-7 | 支持按 fid 回查和删除元数据 | 已验证 | `agent_file_query(fid=...)`、`AGENT_FILE_META_DELETE` | `labbench: file_status_partial_payload fid=4 ... full_lookup=1`、`delete=1` |

## 任务五：Agent Loop 内核运行机制

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T5-1 | Agent 可注册和删除 watch | 已验证 | `agent_watch()`、`agent_unwatch()`、`AGENT_TOOL_AGENT_WATCH` | `labdemo: sentinel state=WAITING` 前完成 watch；`labbench: unwatch=1` |
| T5-2 | Agent 可等待事件并 timeout | 已验证 | `agent_wait()`、`AGENT_STATUS_TIMEOUT` | `labbench` wait/wake 压测；timeout 语义见 [api.md](api.md) |
| T5-3 | 文件状态变化能唤醒目标 Agent | 已验证 | `agent_file_meta_set()` 基于合并后元数据投递 `AGENT_EVENT_FILE_STATUS`，payload 只承诺短摘要 | `labdemo: sentinel event type=FILE_STATUS`、`labbench: file_status_partial_payload fid=4 stage=align run_id=RUN-042 full_lookup=1`、`labbench: file_status_overflow queued=8 dropped=1 no_space=1` |
| T5-4 | mailbox 消息能触发事件 | 已验证 | `send_message` 工具投递 `AGENT_EVENT_MESSAGE` | `labdemo` sentinel->investigator、investigator->recovery；`labbench: send_message_overflow queued=8 dropped=1 rollback=1` |
| T5-5 | 心跳字段可设置和停止，TIMER watch 匹配时可入队 timer 事件 | 已验证 | `agent_heartbeat()`、`agent_heartbeat_stop()`、`agent_info.last_heartbeat_tick`、`agent_tick()` | `labbench: loop_timeout=1 heartbeat_timer=1 heartbeat_stop=1` |
| T5-6 | event wait/wake 路径可稳定处理事件 | 已验证 | `labbench` | `labbench: event_wait_wake ops=512` |
| T5-7 | 事件队列有明确溢出语义 | 已验证 | 8 槽 FIFO，满队列返回 `AGENT_STATUS_NO_SPACE` 并增加 dropped | `labbench: event_fifo queued=8 dropped=1 ordered=1` |
| T5-8 | 事件处理写入 Context Path | 已验证 | `agent_wait()` 成功消费事件后追加 Context | `labdemo` 和 [task5-agent-loop.md](task5-agent-loop.md) |
| T5-9 | 高权限事件和元数据入口有角色/capability 权限限制 | 已验证 | `agent_create_role()` 限制普通进程只能创建 Sentinel 或引导 Orchestrator；工作 Agent 由 Orchestrator 创建，`agent_set_role()` 禁止自升权，PCB `agent_capability_mask` 执行检查 | `labbench: permission_denied self_escalation=1 wake=1 meta=1 rerun=1 report=1` |

## 任务六：综合演示与创新

| ID | 赛题方向 | 当前状态 | 说明 |
| --- | --- | --- | --- |
| T6-1 | 综合演示程序 | 已验证 | `labdemo` 串联任务一至五，输出 `agentos:event`；宿主机 replay/live 均可生成 `LLM_ANALYSIS` 并显示在 Vite 大屏 |
| T6-2 | 性能演示程序 | 已验证 | `labbench` 输出文件查询、事件等待、批量工具、Context、权限、幂等性能 |
| T6-3 | 云端 LLM Gateway | 已验证 | 宿主机 `host/gateway/llm.mjs` 支持 OpenAI-compatible Chat Completions、`.env` 配置和离线 fallback；mock cloud、坏 JSON、网络失败、无 key 和本地 DeepSeek 兼容调用已验证 |
| T6-4 | 可视化大屏 | 已验证 | 当前已实现 Node + Vite replay/live 大屏、Gateway `/api/replay` 和 `/events` SSE；live QEMU 数据源已接入 |

## 追踪结论

任务一至五已有实现和测试证据。需要注意，任务三当前保存的是固定容量的短文本摘要路径，不承诺完整 raw 请求/响应日志；任务四当前使用 Agent 子系统内核元数据表，不直接修改 xv6 inode 主结构；任务五当前事件队列是 8 槽 FIFO，仍不是最终平台级优先级队列。任务六已完成宿主机事件解析、LLM Gateway/fallback、replay 大屏和 live QEMU 串接，当前主要短板在演示视频和答辩材料。
