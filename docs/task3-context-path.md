# 任务三：上下文路径管理

本文是 [design.md](design.md) 的任务三细节附录，重点说明 Context Path 的布局、写入、查询、回滚、淘汰和可信性设计。

## 目标

任务三要求系统维护 Agent 多轮工具调用上下文，使 Agent 能看到自己历史调用路径，并能在路径过长时自动淘汰旧记录。

uCore 分支实现的是 128 条固定容量短文本摘要路径。它不保存完整 raw 请求/响应日志，而是保存评审可见、内核可快速维护的摘要记录。

## Context 布局

Agent Context 共 4 页：

| 区域 | 偏移 | 内容 |
| --- | ---: | --- |
| header | 0 | `struct agent_context_header` |
| latest | `sizeof(struct agent_context_header)` | `struct agent_result` |
| records | 4096 | `struct agent_context_record[128]` |

`struct agent_context_header` 关键字段：

| 字段 | 说明 |
| --- | --- |
| `magic` | Context magic |
| `version` | Context layout 版本 |
| `capacity` | 最大记录数，当前为 128 |
| `count` | 当前有效记录数 |
| `head` | 下一次写入槽位 |
| `total_calls` | 总工具调用数 |
| `oldest_sequence` | 当前最早可见 sequence |
| `latest_sequence` | 当前最新 sequence |
| `dropped_records` | 因 FIFO 淘汰的记录数 |
| `rollback_count` | 成功 rollback 次数 |
| `latest_response_offset` | latest result 偏移 |
| `records_offset` | record 区偏移 |

`struct agent_context_record` 关键字段：

| 字段 | 说明 |
| --- | --- |
| `sequence` | 内核分配的递增序号 |
| `request_id` | 用户请求 ID |
| `arg0` | 第一个数值参数摘要 |
| `value0/value1/value2` | 工具结果数值槽 |
| `tick` | 写入时 tick |
| `tool_id` | 工具 ID |
| `status` | 工具结果状态 |
| `payload` | 16 字节 payload 摘要 |
| `result` | 16 字节 result 摘要 |

## shadow 权威历史

Context 使用双份数据：

| 副本 | 用途 |
| --- | --- |
| kernel shadow | 内核权威历史，用户态无法直接修改 |
| user mirror | 用户态高速读取镜像 |

所有写入先进入 kernel shadow，再同步到 user mirror。`context_query()` 和 `context_snapshot()` 都读取 shadow。用户态即使直接写坏镜像，也不能伪造内核返回的历史。

`agentfinal_ucore` 的篡改测试：

1. 用户态把 user mirror 中第一条记录 sequence 改成 9999。
2. 调用 `context_snapshot()`。
3. snapshot 返回原始 sequence。
4. snapshot 同步 user mirror，使直接读也恢复为原始内容。

对应输出：

```text
agentfinal_ucore: tamper_protected=1
```

## 写入路径

每次工具调用完成后，内核执行：

1. 给本次调用分配 sequence。
2. 把 `struct agent_result` 写入 latest 区。
3. 构造 `struct agent_context_record`。
4. 写入当前 head 指向的 record 槽。
5. 更新 count、head、oldest、latest、dropped。
6. 同步用户镜像。

手动 `context_push()` 使用同一个 sequence 流，保证手动记录和工具调用记录在同一时间线中。

## 查询接口

| 接口 | 说明 |
| --- | --- |
| `context_query(start_sequence, out, max)` | 从指定 sequence 开始返回可见记录；`start_sequence=0` 表示从最早可见记录开始 |
| `context_snapshot(header, records, max)` | 一次返回 header 和有序 records，是推荐读取方式 |
| `context_rollback(sequence)` | 回滚到仍可见 sequence |
| `context_clear()` | 清空记录和元信息 |

`context_snapshot()` 是最终测试和演示的主路径，因为它一次 syscall 就能拿到 header 和多条记录。

## FIFO 淘汰

当前容量固定为 128 条。超过容量时，系统覆盖最旧记录，并增加 `dropped_records`。

`agentfinal_ucore` 写入 192 条记录后检查：

| 字段 | 期望 |
| --- | ---: |
| `count` | 128 |
| `oldest_sequence` | 65 |
| `latest_sequence` | 192 |
| `dropped_records` | 64 |

对应输出：

```text
agentfinal_ucore: fifo oldest=65 latest=192 dropped=64
```

## 性能路径

Context Path 有三种读取方式：

| 方式 | 适用场景 |
| --- | --- |
| 直接读 user mirror | 高频读取 latest 状态或可信 Agent 自身调试 |
| `context_query()` | 查询少量历史记录 |
| `context_snapshot()` | 批量读取当前可见历史，推荐评审和演示使用 |

`agentbench_ucore` 对比了 direct、query 和 snapshot：

```text
agentbench_ucore: direct_context ops=50000 ticks=1 ops_per_tick=50000 speedup_x100=76904
agentbench_ucore: context_query ops=256 ticks=2 ops_per_tick=128 speedup_x100=100
agentbench_ucore: context_snapshot ops=32768 ticks=23 ops_per_tick=1424 speedup_x100=1113
```

## 当前边界

| 边界 | 说明 |
| --- | --- |
| 历史容量 | 固定 128 条 |
| 文本长度 | payload/result 各保存 16 字节摘要 |
| 完整日志 | 不保存完整 raw 请求、完整参数键名或长文本 |
| 持久化 | Context Path 当前随进程生命周期存在，不持久化到磁盘 |

## 验证证据

```text
agentfinal_ucore: short_text_history=1 payload=ucore-final result=ucore-final
agentfinal_ucore: tamper_protected=1
agentfinal_ucore: fifo oldest=65 latest=192 dropped=64
agentfinal_ucore: passed
```
