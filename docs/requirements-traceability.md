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
| G-4 | 提供用户态测试程序 | 已验证 | `user/agentfinal.c`、`user/agentbench.c`、`user/agentcall.c`、`user/contexttest.c`、`user/agentstress.c`、`user/agentexec.c` | [verification.md](verification.md) |
| G-5 | 提供综合演示场景 | 未实现 | 待设计 | 当前只有基础演示程序 |
| G-6 | 提供设计文档和运行说明 | 文档待补 | [../README.md](../README.md)、[design.md](design.md)、[demo-script.md](demo-script.md) | 本次文档重构补齐框架 |

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
| T2-1 | 用户态 Agent 测试程序能成功调用至少 3 个内核工具 | 已验证 | `agent_tools[]` 9 个工具、`agent_run()` | `agentfinal` 批量调用 4 类工具 |
| T2-2 | 每个工具请求和响应均为结构化格式 | 已验证 | `struct agent_op`、`struct agent_result`、`struct agent_tool_desc` | `agentfinal`、`agentbench` |
| T2-3 | 提供工具列表及参数说明 | 已验证 | `tool_list()`、`agent_tool_list()` | `tool_list: total=9` |
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
| T3-5 | 支持批量上下文快照和可区分 rollback 错误 | 扩展增强 | `context_snapshot`、`context_query`、`context_rollback` | `agentfinal`、`agentbench`、`contexttest: rollback_not_found=-5` |

## 任务四至六当前缺口

| ID | 赛题方向 | 当前状态 | 说明 |
| --- | --- | --- | --- |
| T4 | 面向 Agent 查询优化的文件系统扩展 | 部分实现 | 只有 `query_file` 元数据查询，未实现索引、属性过滤、语义查询或批量查询 |
| T5 | Agent Loop 内核运行机制 | 部分实现 | 只有 `loop_state` 和心跳字段预留，未实现心跳触发、等待队列、唤醒 |
| T6 | 综合演示与创新 | 未实现 | 需要构建完整场景、演示脚本、视频和创新点叙述 |

## 追踪结论

任务一至三的基础验收项已有实现和测试证据。需要注意，任务三当前保存的是固定容量的短文本摘要路径，不承诺完整 raw 请求/响应日志。当前主要短板不在基础功能，而在任务四至六、综合演示、性能测量精度和最终答辩材料。
