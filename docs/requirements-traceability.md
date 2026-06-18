# 赛题要求对应说明

本文档说明当前 uCore 分支与赛题功能要求之间的对应关系。表述重点放在已经实现并可运行验证的内容。

## 任务一：Agent 进程创建与地址空间设计

已实现内容：

- `agent_create()` 创建 Agent 子进程。
- `struct proc` 中保存 Agent 类型、Context 页面、调用计数、事件状态和心跳状态。
- 每个 Agent 映射 4 页 Agent Context。
- 普通进程调用 Agent-only 接口会被拒绝。
- `agent_info()` 可查询 Agent 身份、Context 地址、Context 大小和运行统计。

验证来源：

- `agentfinal_ucore` 检查 `agent_info()`、Context base、Context size、Context magic 和 Context capacity。
- `labdemo_ucore` 同时创建三个 Agent，验证多 Agent 并存。

高于基础要求的部分：

- Context 使用 kernel shadow 加 user mirror，防止用户态伪造历史。
- Context 大小扩大到 4 页。
- Agent 事件状态和心跳状态进入进程元数据。

## 任务二：Agent 工具调用机制

已实现内容：

- `agent_run()` 支持一次 syscall 执行最多 64 个工具操作。
- 工具表支持 18 个工具。
- 工具调用返回结构化 result。
- 工具调用自动写入 Context Path。
- `agent_tool_list()` 可查询工具描述。
- `agent_call()` 保留为兼容接口。

验证来源：

- `agentfinal_ucore` 验证 64 个工具调用的 batch sequence。
- `agentbench_ucore` 对比 scalar 和 batch 的吞吐。
- `labdemo_ucore` 使用 query_file、capability_check、send_message、read_file_summary、dependency_query、rerun_stage 等工具完成场景。

高于基础要求的部分：

- 工具 ID 快速分发。
- 批量 syscall。
- 结构化错误码。
- 权限检查和重复动作检测。

## 任务三：Context Path

已实现内容：

- 工具调用自动追加 Context record。
- `context_push()` 支持手动追加记录。
- `context_query()` 支持按 sequence 查询。
- `context_snapshot()` 一次返回 header 和有序 records。
- `context_rollback()` 支持回滚到可见 sequence。
- `context_clear()` 支持清空 Context。
- 超过 128 条后 FIFO 淘汰，并维护 oldest/latest/dropped。

验证来源：

- `agentfinal_ucore` 验证短文本历史、snapshot、篡改保护和 FIFO 淘汰。
- `agentbench_ucore` 对比 query 和 snapshot。
- `labdemo_ucore` 通过 investigator 的 Context snapshot 展示审计能力。

高于基础要求的部分：

- kernel shadow 权威历史。
- 4 页共享镜像。
- 128 条短文本摘要路径。
- snapshot fast path。

## 任务四：文件系统相关 Agent 能力

当前实现范围：

- 实现了内核级文件元数据表。
- 支持 status、stage、kind 索引。
- 支持文件摘要、依赖关系和状态查询。
- 文件状态变化可以触发 Agent 事件。

边界说明：

- 当前没有实现对真实磁盘目录的全量后台索引。
- 当前文件元数据由内核演示数据和 `agent_file_meta_set()` 提供。

验证来源：

- `agentfinal_ucore` 验证 indexed file query。
- `agentbench_ucore` 输出 scan query 和 index query。
- `labdemo_ucore` 使用文件状态变更驱动故障恢复场景。

## 任务五：Agent Loop

当前实现范围：

- `agent_watch()` 注册监听条件。
- `agent_wait()` 阻塞等待事件。
- `agent_wake()` 投递事件。
- `agent_heartbeat()` 设置心跳。
- 文件状态变化可以唤醒匹配 Agent。

验证来源：

- `agentfinal_ucore` 验证自唤醒。
- `agentbench_ucore` 验证多次 wait/wake。
- `labdemo_ucore` 使用 sentinel、investigator、recovery 三个 Agent 串联事件。

## 任务六：综合演示

当前实现范围：

- `labdemo_ucore` 提供完整多 Agent 场景。
- 场景覆盖故障注入、事件监听、索引查询、权限拒绝、跨 Agent 通信、依赖分析、恢复执行、重复动作检测和最终报告查询。

验证来源：

- `labdemo_ucore: passed`

## 尚未覆盖的高级方向

以下内容可以作为后续增强，但不影响当前 uCore 分支的已实现功能判断：

- 对真实文件系统目录进行持续后台扫描。
- 更复杂的权限模型，例如按 Agent role 绑定能力集合。
- 更完整的 Agent 调度策略，例如优先级、长期队列和取消机制。
- 将 Context Path 扩展为可持久化日志。
