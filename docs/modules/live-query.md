# Agent Live-Query FS

Agent workflow 会持续产生中间文件、检查点和结果文件。AgentOS 为显式登记的文件保存结构化 metadata，并在 uCore 内核中维护选择性索引、typed watch 和编辑租约，使文件状态可以直接进入 Agent Loop。

## 文件对象模型

Agent 通过 `agent_file_meta_set()` 登记需要管理的文件。每条记录同时保存业务语义与 VFS 物理身份：

```text
project / workflow / run_id
stage / kind / status / summary
dependency_mask
dev / inum / incarnation / size
fs_generation
```

`dev + inum + incarnation` 组成文件身份。inode 回收并复用后，incarnation 随之变化，原 metadata、content receipt、edit lease 和 pending unlink 不会命中新文件。Catalog 容量为 512，未登记文件沿用 uCore 原有 VFS 行为。

登记操作受 workflow scope 和 `AGENT_CAP_META_WRITE` 控制。查询、修改和回收路径都会重新检查 lifecycle generation。

## 查询计划

Catalog 为 `status`、`stage` 和 `kind` 维护等值索引。`agent_file_query()` 接收 path、project、workflow、run、三类索引字段和有界 `summary_contains`，planner 可以选择 traversal 或 index。

索引用于缩小候选集，返回前继续复核完整谓词、lifecycle、scope 和 catalog generation。inode incarnation 由 VFS 增量路径维护，并作为文件身份的一部分随结果返回。两条路径使用相同的结果结构，一次调用最多复制 8 条记录，并返回：

- 命中数与实际复制数；
- 扫描量与候选量；
- plan、reason 与索引 bucket；
- truncation、query ticks 与 catalog generation。

```c
int agent_file_meta_init(void);
int agent_file_meta_set(struct agent_file_meta *meta);
int agent_file_query(struct agent_file_query *query,
                     struct agent_file_query_result *result);
```

`AGENT_FILE_QUERY_USE_INDEX` 允许 planner 使用索引，`AGENT_FILE_QUERY_SCAN` 强制 traversal。调用方可以利用返回的 plan 和 generation 判断本次视图及查询工作量。

## Typed watch

`agent_live_watch()` 保存完整的 typed query。已登记对象发生 metadata、write、truncate 或 unlink 变化时，内核比较变更前后的查询集合：

| 变更前 | 变更后 | 事件 |
| --- | --- | --- |
| 未命中 | 命中 | `ENTER` |
| 命中 | 仍命中且记录改变 | `UPDATE` |
| 命中 | 未命中或删除 | `LEAVE` |

事件携带 target control id、scope 和 lifecycle generation，经 Agent event queue 进入 Context。watch token 同时绑定 target、control id、scope 和 lifecycle generation。

```c
int agent_live_watch(struct agent_file_live_watch *watch);
int agent_live_unwatch(struct agent_file_live_watch *watch);
```

## Generation resync

队列压力、事件缺口或 catalog generation 变化时，内核推进 `resync_generation` 并发送 `RESYNC_REQUIRED`。Agent 重新执行 query 或 snapshot，再用同一 generation 提交 `ACK_RESYNC` 建立新基线。晚到的旧 ACK 无法覆盖新的缺口。

workflow 收口前会排空当前 scope 的 metadata 与 pending VFS 更新。resync 尚未完成时返回 `RETRY`，调用方等待状态推进后按对应接口规则重试。

## VFS 增量维护

write 与 truncate 更新 size 和 generation，unlink 与 deferred reclaim 进入有界 pending 队列。内容摘要用于固定文件字节，metadata query 返回结构化业务状态。索引项、对象 identity 和查询结果在同一次更新中提交。

文件编辑使用带版本的租约：

```c
int agent_file_edit_begin(const char *path, uint64 flags, int ttl_ticks,
                          struct agent_file_edit_state *state);
int agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
                           struct agent_file_edit_state *state);
int agent_file_edit_abort(uint64 lease_id);
```

lease id、owner、scope、expected version 和 TTL 共同约束并发写入。版本变化时 commit 返回冲突状态。

## 实现位置

| 模块 | 源码 |
| --- | --- |
| Catalog 与 metadata | [`os/agent_metadata_catalog.c`](../../os/agent_metadata_catalog.c)、[`os/agent_metadata.c`](../../os/agent_metadata.c) |
| Query planner 与谓词 | [`os/agent_metadata_query.c`](../../os/agent_metadata_query.c) |
| 摘要、lease 与 incarnation | [`os/agent_metadata_objects.c`](../../os/agent_metadata_objects.c)、[`os/agent_file_state.c`](../../os/agent_file_state.c) |
| Typed watch 与 resync | [`os/agent_live_query_events.c`](../../os/agent_live_query_events.c) |
| VFS 增量更新 | [`os/file.c`](../../os/file.c)、[`os/agent_metadata_actions.c`](../../os/agent_metadata_actions.c) |
| 用户态接口 | [`user/include/agent.h`](../../user/include/agent.h) |

## 查询性能

同一 96-record corpus 的 16 组 traversal/indexed workflow core 配对中，indexed 全部取得更短延迟，中位配对加速比为 `3.118x`。参数化测试进一步覆盖 catalog size `24/64/96` 与 hit count `1/2/4/8`，12 个参数格的中位加速比为 `1.164x-2.808x`。查询区间、端到端区间和逐样本分布见[性能结果](../performance.md)。

## 测试入口

`agentfs_ucore` 覆盖登记、删除、incarnation、scan/index 等价性、typed transition、scope 和 resync；`agentbench_ucore` 输出扫描量、索引候选量、query ticks 和 workload fingerprint。

```bash
python3 -B scripts/check-agent-live-query-fs.py
python3 -B scripts/test-agent-live-query-fs.py
AGENT_TEST_CASE=agentfs_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentbench_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

查询结构和错误状态见 [API](../api.md)，事件等待与 resync 流程见 [Workflow 运行时](workflow-runtime.md)。
