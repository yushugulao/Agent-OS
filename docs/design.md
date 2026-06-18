# Agent-OS on uCore 设计说明

## 目标

uCore 分支的目标是把 Agent-OS 做成一个完整的内核功能扩展，而不是只保留演示级接口。当前版本重点覆盖六类能力：

1. Agent 进程创建和生命周期管理。
2. 高吞吐结构化工具调用。
3. 可查询、可回滚、可抗用户态篡改的 Context Path。
4. 文件元数据索引和按条件查询。
5. Agent 事件监听、等待、唤醒和心跳。
6. 多 Agent 协作恢复场景。

## 总体架构

Agent-OS 在 uCore 内核中新增 `os/agent.c` 和 `os/agent.h`。内核进程结构 `struct proc` 增加 Agent 元数据，包括 Agent 类型、调用计数、Context 页面、事件状态、心跳状态和监听过滤条件。

用户态通过 `user/include/agent.h` 共享 ABI 定义，通过 `user/lib/syscall.c` 中的包装函数调用内核接口。最终验证程序位于 `user/src/agentfinal_ucore.c`、`user/src/agentbench_ucore.c` 和 `user/src/labdemo_ucore.c`。

## Agent 进程

普通进程调用 `agent_create()` 后，内核走专门的 `agent_create_proc()` 路径创建子进程。子进程继承基本执行状态，但会被标记为 Agent，并额外分配 Agent Context。

Agent Context 固定映射在用户地址空间 trapframe 下方的一段区域。当前大小为 4 页，共 16 KiB。普通进程没有 Agent 元数据，也不会通过 Agent syscall 访问该区域。

## Agent Context

Agent Context 分为两份：

- kernel shadow：内核私有权威副本。
- user mirror：用户态高速读取镜像。

所有工具调用、手动 Context 写入、回滚和清空操作都先修改 kernel shadow，再同步到 user mirror。这样可以同时满足两个目标：

1. 用户态可以直接读取最近结果和历史摘要，减少 syscall。
2. 用户态写坏镜像后，`context_snapshot()` 仍以 kernel shadow 为准，并会刷新用户镜像。

Context 第 0 页保存 header 和 latest result；第 1 至第 3 页保存 128 条固定容量记录。每条记录包含 sequence、tool_id、状态、数值槽、tick、短 payload 和短 result。

## 批量工具调用

性能主路径是 `agent_run()`。它一次接收最多 64 个 `struct agent_op`，并返回同等数量的 `struct agent_result`。每个操作包含工具 ID、两个整数参数和 64 字节 payload。

内核执行流程：

1. 预先检查用户输入和输出缓冲区。
2. 按 tool_id 直接定位工具执行函数。
3. 将每条结果写入 result 数组。
4. 将结果追加到 Context Path。
5. 更新 latest result 和 header。

这种设计减少了频繁 syscall 的开销，也减少了重复解析工具名称的成本。

## 工具系统

当前工具表包含 18 个工具：

- `echo`
- `pid_info`
- `ctx_stat`
- `query_process`
- `get_system_status`
- `read_context`
- `query_file`
- `send_message`
- `read_message`
- `file_meta_init`
- `read_file_summary`
- `dependency_query`
- `capability_check`
- `rerun_stage`
- `write_report`
- `agent_watch`
- `agent_wait`
- `agent_heartbeat`

其中 `agent_wait` 作为工具表项保留，用于统一工具发现；实际等待路径走独立 syscall `agent_wait()`，避免长时间阻塞批量工具调用。

## 文件元数据索引

Agent-OS 内核维护一个最多 128 条的文件元数据表。每条记录包含物理路径、逻辑路径、项目、工作流、阶段、类型、状态、版本、摘要和依赖关系。

为避免每次查询都完整扫描，内核维护三组索引链：

- status index
- stage index
- kind index

查询时如果条件命中索引字段，就先从对应索引链遍历，再做剩余条件过滤。`agentbench_ucore` 会对扫描查询和索引查询分别计时。

## Agent Loop 和事件

Agent 可以通过 `agent_watch(event_type, filter)` 注册监听条件，通过 `agent_wait(event, timeout_ticks)` 等待事件。其他 Agent 或内核工具通过 `agent_wake(pid, event)` 或文件状态变化投递事件。

事件结构包含 event_id、type、source_pid、correlation id、tick 和 payload。等待成功、超时、心跳等行为都会写入 Context Path，便于演示和审计。

## 演示场景

`labdemo_ucore` 构造三个 Agent：

- sentinel：监控失败事件。
- investigator：分析失败原因和影响范围。
- recovery：执行恢复动作并生成结果。

演示流程中会注入一个 `align` 阶段失败，sentinel 收到事件后查询文件索引，再把调查任务交给 investigator。investigator 查询文件摘要和依赖关系，recovery 经过能力检查后执行 rerun，最后验证重复操作被拒绝并查询报告状态。

## 性能设计取舍

当前版本优先保证可解释、可验证和可扩展：

- 热路径使用批量 syscall 和工具 ID 分发。
- Context 直接读用于高频观察。
- `context_snapshot()` 用一次 syscall 返回 header 和有序 records。
- 文件查询有索引路径，但保留扫描路径作为对比和兜底。
- 事件等待保持简单阻塞模型，便于在 uCore 当前调度框架中稳定验证。
