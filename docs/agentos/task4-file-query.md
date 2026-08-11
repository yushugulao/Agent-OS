# 任务四：Agent Live-Query FS

## 场景与约束

科研工作流经常按 project、stage、kind 或 status 查找文件。重复遍历目录会把查询成本放大，inode 复用又可能让旧属性命中新文件。普通文件也不应自动变成带 Agent 语义的对象。

任务四采用显式登记、选择性索引和事件驱动更新。用户决定哪些文件进入 catalog，内核负责物理身份、可见性和增量一致性。

## 方案

### 显式对象

具备 `AGENT_CAP_META_WRITE` 的当前 workflow Agent 调用 `agent_file_meta_set()`，把高层属性绑定到真实文件身份：

```text
physical/logical path
project + workflow + run_id
stage + kind + status + summary
dependency_mask
dev + inum + incarnation + size
fs_generation
```

`dev + inum + incarnation` 共同标识文件。inode 复用后，旧 metadata、content receipt、edit lease 和 pending unlink 都无法命中新对象。普通 create/rename 不会自动登记。

### Catalog 与查询

内存 catalog 为 `status`、`stage` 和 `kind` 建立选择性等值索引。`agent_file_query()` 同时支持路径、project/workflow/run、三类索引字段和有界 `summary_contains`。索引只缩小候选集，返回前仍对每条候选执行完整谓词和 scope 检查。

结果携带 hits、实际复制数、扫描/候选工作量、plan/reason、索引 bucket、截断状态、tick 和当前 generation。一次最多复制 8 条结果。没有合适索引时，planner 选择 scan。

### Live watch 与恢复

`agent_live_watch()` 保存完整 typed query。已绑定文件发生 metadata 或 content 变化时，内核比较 before/after 并产生：

| before | after | 事件 |
| --- | --- | --- |
| 不匹配 | 匹配 | `ENTER` |
| 匹配 | 仍匹配且记录变化 | `UPDATE` |
| 匹配 | 不匹配或删除 | `LEAVE` |

事件绑定 target control id、scope 和 lifecycle generation，并进入 Agent Queue 与 Context。队列无法保存完整增量时，内核提高 `resync_generation` 并投递 `RESYNC_REQUIRED`。用户重新执行 query/snapshot 后，以同一 generation `ACK_RESYNC`；旧 ACK 不会清除更晚的缺口。

VFS 只维护已登记对象的实时性。write/truncate 更新 size 和 generation，unlink/deferred reclaim 进入有界 pending 队列。workflow fence 排空本 scope pending；缺口未恢复时返回 `RETRY`。

## 关键实现

| 职责 | 源码 |
| --- | --- |
| Catalog 与显式 metadata | [os/agent_metadata_catalog.c](../../os/agent_metadata_catalog.c)、[os/agent_metadata.c](../../os/agent_metadata.c) |
| Query planner 与谓词 | [os/agent_metadata_query.c](../../os/agent_metadata_query.c) |
| 内容摘要、edit lease 与 incarnation | [os/agent_metadata_objects.c](../../os/agent_metadata_objects.c)、[os/agent_file_state.c](../../os/agent_file_state.c) |
| Typed watch、transition 与 resync | [os/agent_live_query_events.c](../../os/agent_live_query_events.c) |
| VFS 增量更新 | [os/file.c](../../os/file.c)、[os/agent_metadata_actions.c](../../os/agent_metadata_actions.c) |
| 公开接口 | [user/include/agent.h](../../user/include/agent.h) |

## 验证与量化

```bash
python -B scripts/check-agent-live-query-fs.py
python -B scripts/test-agent-live-query-fs.py
AGENT_TEST_CASE=agentfs_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
AGENT_TEST_CASE=agentbench_ucore make agentos-test TOOLPREFIX=riscv-none-elf-
```

`agentfs_ucore` 覆盖 set/delete、incarnation、scan/index、typed transition、scope 和 resync。`agentbench_ucore` 在同一数据集报告扫描记录、索引候选、query tick 和 fingerprint。

2026-08-11 一次性活动测量了 catalog size `24/64/96` 与 hit count `1/2/4/8` 的完整 `3 x 4` 网格，每格 15 个 AB/BA 配对且没有插值。各单元 median speedup 为 `1.164x` 到 `2.808x`。分组数据保留 first retained scan 12 条、repeat scan 168 条和 ready index 180 条；first 组位于显式 warmup 后。原始配对和图表见 [高级性能图](advanced-performance-figures.md)。

## 当前边界

- Catalog、索引和 watch 属于当前启动周期，Guest 每次启动重新登记。
- 目录遍历不会自动发现或登记普通文件。
- 索引只覆盖 `status/stage/kind` 等值字段，其他谓词执行 scan 或候选后过滤。
- `summary_contains` 是有界 substring；content digest 提供指纹，不提供全文检索。
- live event 表示集合变化，用户仍通过 query 获取当前完整对象。
- fence receipt 标记 `METADATA_VOLATILE`，不承诺重启恢复。
