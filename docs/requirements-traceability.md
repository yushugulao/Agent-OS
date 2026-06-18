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
| G-1 | 在教学操作系统内核中实现 Agent-OS 功能模块 | 已验证 | `os/agent.c`、`os/agent.h`、`os/proc.c` | `agentfinal_ucore`、`agentbench_ucore`、`labdemo_ucore` |
| G-2 | 系统可在 QEMU 上运行 | 已验证 | `Makefile`、`nfs/fs.img` | `scripts/run-agent-tests.sh` |
| G-3 | 提供内核代码 | 已验证 | `os/` | Git 仓库源码 |
| G-4 | 提供用户态测试程序 | 已验证 | `user/src/agentfinal_ucore.c`、`user/src/agentbench_ucore.c`、`user/src/labdemo_ucore.c` | [verification.md](verification.md) |
| G-5 | 提供综合演示场景 | 已验证 | `user/src/labdemo_ucore.c` | `labdemo_ucore: passed` |
| G-6 | 提供设计文档和运行说明 | 已验证 | [../README.md](../README.md)、[design.md](design.md)、[demo-script.md](demo-script.md) | 本文档、[verification.md](verification.md) |

## 任务一：Agent 进程创建与地址空间设计

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T1-1 | Agent 进程能成功创建 | 已验证 | `agent_create()`、`agent_create_proc()`、`agent_make()` | `agentfinal_ucore` 创建 Agent 子进程 |
| T1-2 | PCB 扩展字段正确初始化 | 已验证 | `struct proc` Agent 字段、`agent_clear_metadata()`、`agent_make()` | `agent_info()`、`agentfinal_ucore` |
| T1-3 | Agent Context 区在用户地址空间中正确分配 | 已验证 | `agent_map_context()`、`AGENT_CONTEXT_BASE` | `agentfinal_ucore: context size=16384 capacity=128` |
| T1-4 | Agent 进程可直接读取 Context 镜像 | 已验证 | Agent Context 用户镜像页和内核 shadow 权威页 | `agentfinal_ucore` 读取 header/latest |
| T1-5 | 普通进程和 Agent 进程可共存，互不影响 | 已验证 | 普通父进程创建并等待 Agent 子进程；普通进程不安装 Agent metadata/context | `agentfinal_ucore`、`labdemo_ucore` |
| T1-6 | Agent 退出后资源能释放 | 已验证 | `agent_free_proc_context()`、`freeproc()` | 三个最终测试均正常退出 |

## 任务二：Agent 与内核结构化交互

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T2-1 | 用户态 Agent 测试程序能成功调用至少 3 个内核工具 | 已验证 | `agent_tools[]` 18 个工具、`agent_run()` | `agentfinal_ucore` 批量调用 echo；`labdemo_ucore` 调用 query_file、summary、dependency、capability、rerun、write_report、send_message |
| T2-2 | 每个工具请求和响应均为结构化格式 | 已验证 | `struct agent_op`、`struct agent_result`、`struct agent_tool_desc` | `agentfinal_ucore`、`agentbench_ucore` |
| T2-3 | 提供工具列表及参数说明 | 已验证 | `agent_tool_list()`、`agent_tools[]` | [api.md](api.md) 工具表 |
| T2-4 | 工具调用结果可写入 Agent Context | 已验证 | `agent_append_context()` 写 shadow 权威页并同步用户镜像 | `agentfinal_ucore` 读取 latest 和 snapshot |
| T2-5 | 错误路径有明确返回 | 已验证 | `AGENT_STATUS_*`、工具执行状态码 | `labdemo_ucore` 验证 denied 和 duplicate |
| T2-6 | 工具解析性能优化 | 扩展增强 | `agent_run()` 批量执行、ID 分发 | `agentbench_ucore` |

## 任务三：上下文路径管理

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T3-1 | Agent 测试程序执行 5 轮以上连续工具调用 | 已验证 | `agentfinal_ucore` 批量调用 | 连续 192 个 op |
| T3-2 | 系统正确维护多轮上下文路径 | 已验证 | shadow 权威 `agent_context_record[128]` 环形记录，record 含 16 字节 payload/result 摘要 | `agentfinal_ucore: short_text_history=1` |
| T3-3 | Agent 可直接从 Context 区高速读取路径数据 | 已验证 | Agent Context 用户镜像可读；`context_snapshot()` 可刷新并返回可信 shadow 历史 | `agentbench_ucore: direct_context`、`agentfinal_ucore` |
| T3-4 | 路径超长时自动淘汰，不导致内核 OOM | 已验证 | 固定容量 FIFO 环形覆盖 | `agentfinal_ucore: fifo oldest=65 latest=192 dropped=64` |
| T3-5 | 支持批量上下文快照和 rollback | 已验证 | `context_snapshot`、`context_query`、`context_rollback`、`context_clear` | `agentfinal_ucore`、`agentbench_ucore` |
| T3-6 | 用户态篡改 Context 镜像不影响内核权威历史 | 扩展增强 | kernel shadow + user mirror | `agentfinal_ucore: tamper_protected=1` |

## 任务四：面向 Agent 查询优化的文件系统扩展

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T4-1 | Agent 可按文件属性查询实验工件 | 已验证 | `struct agent_file_meta`、`agent_file_query()`、`AGENT_TOOL_QUERY_FILE` 属性 payload | `labdemo_ucore: tool=query_file hits=1 used_index=1` |
| T4-2 | 支持项目、工作流、运行、阶段、类型、状态、摘要、逻辑路径字段 | 已验证 | `os/agent.h` 中 `agent_file_meta` / `agent_file_query` | [task4-file-query.md](task4-file-query.md)、`labdemo_ucore` |
| T4-3 | 有扫描路径和索引路径 | 已验证 | `agent_file_query()`、status/stage/kind 索引桶 | `agentbench_ucore: file_scan_query`、`agentbench_ucore: file_index_query` |
| T4-4 | 查询结果包含命中、截断、扫描数、是否使用索引和 tick | 已验证 | `struct agent_file_query_result` | `agentbench_ucore` 性能表 |
| T4-5 | 支持依赖关系查询，服务最小恢复 | 已验证 | `AGENT_TOOL_DEPENDENCY_QUERY`、dependency mask | `labdemo_ucore: affected stages=align+analyze+report+archive` |
| T4-6 | 查询写入 Context Path，可用于报告回放 | 已验证 | 文件查询和工具调用均追加 Context | `labdemo_ucore`、`context_snapshot` |
| T4-7 | 对真实磁盘目录做持续后台扫描 | 未实现 | 无 | 后续增强方向 |

## 任务五：Agent Loop 内核运行机制

| ID | 赛题要求 | 状态 | 实现位置 | 验证证据 |
| --- | --- | --- | --- | --- |
| T5-1 | Agent 可注册 watch | 已验证 | `agent_watch()`、`AGENT_TOOL_AGENT_WATCH` | `labdemo_ucore: WATCH_REGISTERED` |
| T5-2 | Agent 可等待事件并 timeout | 已验证 | `agent_wait()`、`AGENT_STATUS_TIMEOUT` | `agentbench_ucore` wait/wake 压测 |
| T5-3 | 文件状态变化能唤醒目标 Agent | 已验证 | `agent_file_meta_set()` 投递 `AGENT_EVENT_FILE_STATUS` | `labdemo_ucore` sentinel 收到 failed 事件 |
| T5-4 | 消息能触发 Agent 事件 | 已验证 | `send_message` 工具、`agent_wake()` | `labdemo_ucore` sentinel->investigator、investigator->recovery |
| T5-5 | 心跳字段可设置并通过 Agent 信息观察 | 已验证 | `agent_heartbeat()`、`agent_info.last_heartbeat_tick` | `labdemo_ucore` Sentinel 调用 heartbeat |
| T5-6 | event wait/wake 有性能对比 | 已验证 | `agentbench_ucore` | `agentbench_ucore: event_wait_wake` |
| T5-7 | 事件处理写入 Context Path | 已验证 | `agent_wait()` 成功消费事件后追加 Context | `labdemo_ucore` 和 [task5-agent-loop.md](task5-agent-loop.md) |

## 任务六：综合演示与创新

| ID | 赛题方向 | 当前状态 | 说明 |
| --- | --- | --- | --- |
| T6-1 | 综合演示程序 | 已验证 | `labdemo_ucore` 串联任务一至五，输出 `agentos:event` |
| T6-2 | 性能演示程序 | 已验证 | `agentbench_ucore` 输出批量工具、Context、文件查询和事件等待性能 |
| T6-3 | 云端 LLM Gateway | 未实现 | 当前只保留结构化事件和工具结果，尚未接真实云端 LLM |
| T6-4 | 可视化大屏 | 未实现 | 当前已输出 `agentos:event`，大屏解析器尚未实现 |

## 追踪结论

任务一至三已有增强实现和测试证据，并且在 Context 容量、批量工具调用、Context shadow 可信历史、snapshot 查询和性能测试方面高于最小要求。任务四和任务五已有可运行的演示级实现，能够支撑综合场景和性能对比。当前主要短板在真实文件系统后台索引、长期 Agent 调度策略、云端 LLM Gateway、可视化大屏、演示视频和答辩材料。
