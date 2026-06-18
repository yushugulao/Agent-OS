# 测试内容详细说明

## agentfinal_ucore

`agentfinal_ucore` 是最终正确性测试，覆盖 Agent-OS 的主要功能面。

测试步骤：

1. 父进程调用 `agent_create()` 创建 Agent 子进程。
2. 子进程调用 `agent_info()`，检查自己是 Agent，并检查 Context 基址和大小。
3. 直接读取 Agent Context header，检查 magic、version 和容量。
4. 调用 `context_clear()`，保证后续 sequence 从干净状态开始。
5. 构造 64 个 echo 操作，使用一次 `agent_run()` 批量执行。
6. 检查第一条和最后一条 result 的 sequence，确认 batch 内 sequence 连续递增。
7. 直接读取 latest result，确认用户镜像已同步。
8. 调用 `context_snapshot()`，检查返回 64 条有序记录。
9. 检查短 payload/result 是否进入 Context record。
10. 手动篡改用户态 Context 镜像中的第一条 record。
11. 再次调用 `context_snapshot()`，确认返回内容仍来自内核 shadow。
12. 检查 snapshot 后用户镜像被刷新。
13. 继续批量写入直到超过 128 条容量，检查 FIFO 淘汰结果。
14. 初始化文件元数据，按 stage 查询，检查命中数量和 `used_index`。
15. 注册 message watch，向自己投递事件，再用 `agent_wait()` 收到事件。
16. 子进程输出 `agentfinal_ucore: passed`，父进程等待子进程并输出 `agentfinal_ucore: parent passed`。

该测试证明：

- Agent 创建可用。
- Agent Context 映射可用。
- 批量工具调用可用。
- Context Path 可保存有序历史。
- kernel shadow 能防止用户态伪造历史。
- 128 条容量和 FIFO 元信息正确。
- 文件元数据索引可用。
- Agent wait/wake 可用。

## agentbench_ucore

`agentbench_ucore` 是性能和吞吐测试。它不使用固定耗时阈值，而是输出可对比的 tick 统计。

测试项目：

1. `scalar_agent_run`：8192 次单操作 `agent_run()`。
2. `batch_agent_run`：8192 次操作按 64 条一组批量提交。
3. `direct_context`：直接读取用户态 Context 镜像中的 latest sequence。
4. `context_query`：多次用 syscall 查询单条 Context record。
5. `context_snapshot`：多次用 syscall 批量获取 Context records。
6. `file_scan_query`：构造不走索引的文件查询。
7. `file_index_query`：构造可走索引的文件查询。
8. `event_wait_wake`：父进程多次唤醒等待中的 Agent 子进程。

输出字段：

- `ops`：执行的逻辑操作数量。
- `ticks`：消耗的内核 tick 数。
- `ops_per_tick`：每 tick 完成的操作数。
- `speedup_x100`：相对基线放大 100 倍后的速度比。

该测试证明：

- 批量接口能减少 syscall 开销。
- 直接读 Context 适合高频读取最新状态。
- snapshot 比逐条 query 更适合批量历史查询。
- 文件索引查询路径可观测。
- 事件等待和唤醒可以稳定运行。

## labdemo_ucore

`labdemo_ucore` 是面向答辩的最终场景测试。它把底层能力串成一个可解释的多 Agent 工作流。

角色：

- sentinel：监听失败事件。
- investigator：查询失败原因和影响范围。
- recovery：执行恢复动作。

流程：

1. 父进程初始化文件元数据。
2. 父进程创建 recovery、investigator、sentinel 三个 Agent。
3. sentinel 注册 `status=failed` 的文件状态监听。
4. investigator 注册 message 监听。
5. recovery 注册 recovery message 监听。
6. 父进程把 align 阶段的文件状态改成 failed，制造故障。
7. sentinel 收到失败事件，通过 `query_file` 找到失败文件。
8. sentinel 尝试执行恢复动作，能力检查返回 denied。
9. sentinel 通过消息唤醒 investigator。
10. investigator 查询文件摘要和依赖关系，并快照自己的 Context。
11. investigator 唤醒 recovery。
12. recovery 通过能力检查，执行 `rerun_stage`。
13. recovery 再次执行同一动作，内核返回 duplicate。
14. 父进程等待三个 Agent 结束，输出最终 recovered。

该测试证明：

- 多 Agent 可以并发存在。
- 文件状态变化可以触发事件。
- Agent 之间可以用内核事件通信。
- 工具调用结果进入 Context，便于审计。
- 简单权限控制和重复操作检测可用。
- 最终场景不是孤立 API 展示，而是组合后的系统行为。
