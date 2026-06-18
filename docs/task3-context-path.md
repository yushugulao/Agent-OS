# 任务三：上下文路径管理

本文是 [design.md](design.md) 的任务三细节附录，重点展开 Context Path 布局、自动记录、手动操作和验证。需求完成度和验证结论见 [requirements-traceability.md](requirements-traceability.md) 与 [verification.md](verification.md)。

任务三要求为每个 Agent 进程维护查询路径，记录 Agent Loop 中每一轮请求和结果。当前实现完成增强版上下文路径功能：内核维护 Context Path 元信息和 shadow 权威历史，用户态 Agent Context 保存固定容量的历史镜像，用户态演示程序可直接读取、查询、回滚和清空路径。

## Context 布局

Agent Context 当前大小为 16384 字节，布局如下：

| 区域 | 说明 |
| --- | --- |
| `struct agent_context_header` | Context 魔数、版本、容量、有效记录数、head、总调用次数、最早序号、最新序号、淘汰数、回滚数、最近响应偏移、记录区偏移 |
| `struct agent_result` | 最近一次工具调用结果 |
| `struct agent_context_record[]` | Context Path 环形历史记录，当前容量 128 条 |

## 历史记录

每次 `agent_call()` / `tool_call()` 完成后，内核追加一条历史记录。记录内容包括：

- 调用序号 `sequence`；
- 请求 ID `request_id`；
- 工具 ID，工具名可通过工具表按 ID 解释；
- 状态码；
- 参数和值摘要；
- 工具返回数值摘要；
- 16 字节 payload 短文本摘要；
- 16 字节 result 短文本摘要；
- tick 时间戳。

历史 record 不保存工具名字符串、完整参数键名、参数类型、`arg1` 或完整 raw 请求/响应文本；工具名通过工具表按 `tool_id` 解释，最近一次完整 64 字节结果文本保存在 latest `struct agent_result` 中。当前取舍是“128 条短文本摘要路径”，不是无限或完整日志。`struct agent_context_record` 当前为 96 字节，3 页记录区正好容纳 128 条；内核 record 读写 helper 支持跨物理页边界，因此不要求 record 大小整除 `PGSIZE`。

当记录数超过容量后，新的记录会覆盖最旧槽位。当前 `resource_quota` 与 `context_path_capacity` 均为 128。

## Context Path 接口

| 接口 | 说明 |
| --- | --- |
| `context_push(struct agent_context_record *)` | 手动追加一个上下文节点，使用同一 sequence 流 |
| `context_query(uint64 start_sequence, struct agent_context_record *, int max)` | 按时间顺序查询当前仍可见的路径记录 |
| `context_snapshot(struct agent_context_header *, struct agent_context_record *, int max)` | 一次返回 header 和按时间顺序排列的可见路径记录 |
| `context_rollback(uint64 sequence)` | 回滚到仍在 Context 中的历史节点；不存在时返回 `AGENT_STATUS_NOT_FOUND` |
| `context_clear()` | 清空当前 Agent 的 Context Path |

工具调用仍会自动追加路径记录；手动 push 与自动记录共用 `agent_call_count`，保证 sequence 单调推进。发生 FIFO 覆盖时，header 中的 `dropped_records` 会递增，用户态可判断历史是否被淘汰。`context_query()` 和 `context_snapshot()` 都从内核 shadow 页读取权威记录。直接读取 Agent Context 是高速镜像路径，适合可信 Agent 自读；`context_snapshot()` 返回前会刷新用户镜像，避免用户态篡改镜像污染内核权威查询结果。

## 验证

最终功能验收程序 `agentfinal` 会连续触发 192 次批量工具 op，包括超过 128 条历史容量后的覆盖写入，然后直接从 Agent Context 和 `context_snapshot()` 双路径验证：

```text
agentfinal: snapshot count=64 latest=64
agentfinal: short_text_history=1 payload=final result=final
agentfinal: direct_dirty_before_snapshot=1
agentfinal: tamper_protected=1
agentfinal: fifo oldest=65 latest=192 dropped=64
agentfinal: direct_context_match=1
agentfinal: passed
```

该验证证明当前实现已经不是只保存最近一次响应，而是能保存多轮工具调用路径，并且在记录数超过容量后按环形队列覆盖旧记录。后续仍可继续扩展为按条件查询历史节点、跨 Agent 共享上下文或持久化路径。
