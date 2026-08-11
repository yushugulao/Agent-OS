# Agent Live-Query FS

一个 workflow 会持续创建检索结果、检查点和报告。文件名只能回答“文件在哪里”，无法表达阶段、状态、依赖关系，也无法处理 inode 删除后复用。AgentOS 在 uCore 内核中为显式登记的文件维护结构化 catalog，将查询计划、对象代际、typed watch 和编辑租约组合成可进入 Agent Loop 的文件状态服务。

## 文档索引

- [显式文件目录](#显式文件目录)
- [从登记到索引](#从登记到索引)
- [查询规划器](#查询规划器)
- [Typed watch](#typed-watch)
- [Generation resync](#generation-resync)
- [VFS 增量与 inode incarnation](#vfs-增量与-inode-incarnation)
- [编辑租约](#编辑租约)
- [性能结果](#性能结果)
- [测试与实现索引](#测试与实现索引)

## 显式文件目录

`agent_file_meta_set()` 将业务语义绑定到一个真实 inode。Catalog 每条记录同时保存两组字段：

| 业务语义 | 物理身份与版本 |
| --- | --- |
| `project`、`workflow`、`run_id` | `dev`、`inum`、`incarnation` |
| `stage`、`kind`、`status`、`summary` | `size`、`fs_generation`、`updated_tick` |
| `logical_path`、`dependency_mask` | workflow lifecycle 与 scope |

Catalog 容量为 512，单次 query 最多返回 8 个 hit。普通 VFS 文件不会自动进入目录；只有 flags 为零的显式 metadata update 会发布 live-query membership。ABI 中保留的 `PERSIST` 与 `AUTOSCAN` 数值不会进入当前 catalog，owner 在登记时拒绝这两个 flags。未登记文件继续使用 uCore 原有 open/read/write/unlink 路径。

`update_mask` 允许只修改部分语义字段，`AGENT_FILE_META_F_DELETE` 删除登记项。登记和更新需要当前 Agent 持有 `AGENT_CAP_META_WRITE`，查询需要 `AGENT_CAP_META_READ`，两者都绑定 workflow scope 与 lifecycle generation。

## 从登记到索引

一次 metadata 登记从 [`user/lib/syscall.c`](../../user/lib/syscall.c) 的 `agent_file_meta_set()` 进入 `SYS_agent_file_meta_set`，随后由 [`os/agent_metadata_objects.c`](../../os/agent_metadata_objects.c) 完成授权和私有 copyin。Catalog owner 在一个 mutation transaction 中解析 selector、绑定 inode、发布主记录与派生索引，再计算集合变化。

```text
agent_file_meta_set()
    -> sys_agent_file_meta_set()
    -> agent_file_meta_set_execute()
    -> agent_metadata_catalog_bind_volatile()
    -> dev + inum + incarnation 绑定
    -> status / stage / kind bitmap 更新
    -> fs_generation 推进
    -> typed watch transition 发布
```

实际 Guest 用法可在 [`user/src/agentscan_ucore.c`](../../user/src/agentscan_ucore.c) 中定位：

```c
struct agent_file_meta meta = {0};

strcpy(meta.physical_name, "liveqobj");
strcpy(meta.logical_path, "/live-query/explicit");
strcpy(meta.project, "live-query");
strcpy(meta.run_id, "RUN-LIVE");
strcpy(meta.stage, "observe");
strcpy(meta.kind, "artifact");
strcpy(meta.status, "ready");
meta.update_mask = 0;          /* 首次登记填写完整记录。 */
agent_file_meta_set(&meta);
```

目录 mutation 与 query snapshot 使用同一 metadata transaction。查询开始时记录 lifecycle 和 `fs_generation`，复制命中项后再次核对；期间若目录或可见文件代际发生变化，owner 将本轮 snapshot 判为 stale 并在内核中重试，重试耗尽后向调用方返回 `RETRY`，避免拼接两个时刻的视图。

## 查询规划器

`agent_file_query` 支持物理名、逻辑路径、project、workflow、run、status、stage、kind 的等值谓词，以及有界 `summary_contains`。Planner 在允许索引时按 `status -> stage -> kind` 的优先级选择第一个可用键；`AGENT_FILE_QUERY_SCAN` 强制 traversal。

```c
struct agent_file_query query = {0};
struct agent_file_query_result result = {0};

query.flags = AGENT_FILE_QUERY_USE_INDEX;
query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
strcpy(query.project, "live-query");
strcpy(query.run_id, "RUN-LIVE");
strcpy(query.status, "ready");

int returned = agent_file_query(&query, &result);
```

索引只缩小候选集。每个候选仍由 `agent_metadata_query_matches()` 复核完整谓词、scope 和 lifecycle，因此 traversal 与 indexed 返回相同的 `agent_file_hit` 语义。

| Result 字段 | 含义 |
| --- | --- |
| `total_hits / returned / truncated` | 完整命中数、已复制数以及是否超过 `max_hits` |
| `plan / used_index / index_bucket` | 实际计划、是否使用索引、命中的 bitmap bucket |
| `scanned_records / candidate_records` | 访问槽数与 scope 可见候选数 |
| `plan_reason` | forced scan、index off、status/stage/kind index 或 no index key |
| `query_ticks / fs_generation` | 内核查询区间和本次一致视图代际 |
| `hits[].dev/inum/incarnation` | 命中文件的完整物理身份 |

单次 query 最多复制 8 个 hit，`total_hits` 仍报告全部命中数；当前 ABI 没有分页 cursor。Planner 和谓词实现位于 [`os/agent_metadata_query.c`](../../os/agent_metadata_query.c)。其扫描循环每处理 128 个槽执行一次 kernel work checkpoint，使大 catalog 查询不会长期占用内核执行权。

## Typed watch

轮询只能看到两次查询之间的最终状态。`agent_live_watch()` 将完整 typed query 编译到内核 predicate arena，并把订阅绑定到 target proc、control id、workflow lifecycle 和 scope。watch 安装返回 `watch_id`、`initial_generation` 与 `catalog_generation`，Agent 可以从这一 generation 开始接收增量。

```c
struct agent_file_live_watch watch = {0};

watch.version = AGENT_FILE_LIVE_WATCH_VERSION;
watch.query.flags = AGENT_FILE_QUERY_USE_INDEX;
watch.query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
strcpy(watch.query.project, "live-query");
strcpy(watch.query.status, "failed");
agent_live_watch(&watch);
```

每次 metadata、write、truncate 或 unlink 更新都会比较变更前后的集合：

| 变更前 | 变更后 | `AGENT_EVENT_FILE_QUERY` payload |
| --- | --- | --- |
| 未命中 | 命中 | `change=ENTER;...` |
| 命中 | 仍命中且记录发生变化 | `change=UPDATE;...` |
| 命中 | 未命中或对象删除 | `change=LEAVE;...` |

事件的 `corr_id` 是文件 fid，`cause_sequence` 携带可见 generation，payload 以紧凑十六进制字段给出 workflow lifecycle key。typed delivery 使用明确的 FILE_QUERY 模式，不经过 legacy substring filter。Agent 通过 `agent_wait()` 消费事件，结束时以原 `watch_id` 调用 `agent_live_unwatch()`。

## Generation resync

事件队列满、增量发布失败或 domain generation 出现缺口时，内核记录 sticky `resync_generation` 并投递 `RESYNC_REQUIRED`。正常连续 generation 变化仍发布 `ENTER/UPDATE/LEAVE`。恢复时旧 watch 保持活动，替代 watch 覆盖 snapshot 前后的变化：

1. 保存收到的 `resync_generation`；
2. 使用原 typed query 安装 replacement watch，不携带 ACK；
3. 执行 `agent_file_query()` 取得一致 snapshot；只有 `truncated == 0` 时该结果才是完整基线；
4. 在旧 watch 上设置 `ACK_RESYNC` 与保存的 generation，再调用 `agent_live_unwatch()`；旧 watch 的移除与 ACK 位于同一内核临界区，replacement 已经活动；
5. 合并 replacement watch 在 snapshot 之后到达的事件。若出现更大的 gap generation，则重复该过程。

```c
struct agent_file_live_watch replacement = {0};
replacement.version = AGENT_FILE_LIVE_WATCH_VERSION;
replacement.query = watch.query;
if (agent_live_watch(&replacement) == AGENT_STATUS_OK &&
    agent_file_query(&replacement.query, &snapshot) >= 0 &&
    !snapshot.truncated) {
    watch.flags |= AGENT_FILE_LIVE_WATCH_F_ACK_RESYNC;
    watch.resync_generation = gap_generation;
    agent_live_unwatch(&watch);
} else if (replacement.watch_id != 0) {
    replacement.flags &= ~AGENT_FILE_LIVE_WATCH_F_ACK_RESYNC;
    agent_live_unwatch(&replacement);
}
```

结果被截断时，调用方移除 replacement 而不 ACK，继续保持旧 watch 与 sticky generation，并收紧 query；分页基线需要后续 ABI 扩展。ACK 只清除不晚于所携 generation 的缺口，新的更大 generation 仍保持 pending。workflow fence 会排空 tombstone、content delta 并尝试投递 resync marker；domain sticky generation 尚未 ACK 或 marker 仍待投递时返回 `AGENT_STATUS_RETRY`。close 成功后 lifecycle 进入 closing，最终回收阶段清理剩余 pending 状态。

## VFS 增量与 inode incarnation

已绑定 inode 的 write 与 truncate 将最新 size 和 content generation 投影回 catalog；unlink 先保存 `{lifecycle, scope, dev, inum, incarnation}` tombstone，再执行 exact remove。metadata transaction 忙时，tombstone 进入有界队列，后续 drain 仍以完整对象身份删除。

`incarnation` 解决 inode number 复用问题。uCore 的 `ialloc()` 每次复用空闲 inode 时递增 `vfs_incarnation`；达到 `UINT_MAX` 的 inode 不再回收使用。Catalog selector、content receipt、digest cache、edit lease 和 pending unlink 都包含该字段，因此旧对象状态不会命中新文件。

```text
旧文件 A = {dev=1, inum=42, incarnation=7}
unlink(A)
新文件 B = {dev=1, inum=42, incarnation=8}

A 的 metadata / lease / tombstone  !=  B 的对象身份
```

核心实现可见 [`os/fs.c`](../../os/fs.c) 的 `ialloc()` 和 [`os/agent_metadata_catalog.c`](../../os/agent_metadata_catalog.c) 的 identity match。

## 编辑租约

文件编辑通过 lease id、owner、scope、inode identity、base version 和 TTL 建立排他状态：

```c
int agent_file_edit_begin(const char *path, uint64 flags, int ttl_ticks,
                          struct agent_file_edit_state *state);
int agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
                           struct agent_file_edit_state *state);
int agent_file_edit_abort(uint64 lease_id);
```

`begin` 返回当前 `base_version`；`commit` 和 `abort` 允许当前 owner，或同 scope/lifecycle 中具备 `AGENT_CAP_ORCHESTRATE` 的 Agent 操作 lease。过期租约会先被清理，后续 commit/abort 返回 `NOT_FOUND`；incarnation 或 expected version 不匹配返回 `STALE`。`ORCHESTRATOR_BREAK` 允许具备该能力的 Agent 强制释放冲突 lease；`BREAK_EXPIRED` 与未知 flag 在当前实现中不改变处理结果。

## 性能结果

同一 96-record corpus 的 16 组 traversal/indexed workflow-core 配对中，traversal 每次检查 97 条记录，indexed 检查 2 条。indexed 在 16 组中全部更快，core latency 中位数由 `34,712.5 us` 降至 `13,293.5 us`，配对加速比中位数为 `3.118x`。

参数化负载覆盖 catalog size `24/64/96` 与 hit count `1/2/4/8`。12 个参数格的中位加速比为 `1.164x-2.808x`；ready index 的每查询中位时延在三个 catalog size 下约为 `98.3/100.5/108.3 us`。配对哑铃图、雨云图、ECDF、热力图和扫描状态分组结果见[性能测试](../performance.md#3-live-query-核心路径)。

## 测试与实现索引

| 验证入口 | 覆盖内容 |
| --- | --- |
| [`scripts/check-agent-live-query-fs.py`](../../scripts/check-agent-live-query-fs.py) | 显式 membership、snapshot lifecycle、完整 predicate、增量与 resync wiring |
| [`scripts/test-agent-live-query-fs.py`](../../scripts/test-agent-live-query-fs.py) | source contract 的负向变体与回归检查 |
| [`user/src/agentfs_ucore.c`](../../user/src/agentfs_ucore.c) | partial update、stale identity、重建同名文件、scan/index 等价性、truncate 与 delete |
| [`user/src/agentscan_ucore.c`](../../user/src/agentscan_ucore.c) | 普通文件不入 catalog、typed `ENTER/UPDATE/LEAVE`、indexed result identity |
| [`user/src/agentbench_ucore.c`](../../user/src/agentbench_ucore.c) | traversal/indexed 工作量、query ticks 与参数化 corpus |

```bash
python3 -B scripts/check-agent-live-query-fs.py
python3 -B scripts/test-agent-live-query-fs.py
AGENT_TEST_CASE=agentfs_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentscan_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentbench_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

| 模块 | 源码 |
| --- | --- |
| Catalog、bitmap 与 identity | [`os/agent_metadata_catalog.c`](../../os/agent_metadata_catalog.c)、[`os/agent_metadata_catalog.h`](../../os/agent_metadata_catalog.h) |
| Planner 与 predicate | [`os/agent_metadata_query.c`](../../os/agent_metadata_query.c) |
| Syscall owner 与对象操作 | [`os/agent_metadata_objects.c`](../../os/agent_metadata_objects.c)、[`os/agent_metadata.c`](../../os/agent_metadata.c) |
| Typed watch 与 resync | [`os/agent_live_query_events.c`](../../os/agent_live_query_events.c)、[`os/agent_ipc.c`](../../os/agent_ipc.c) |
| Size、digest、lease 与 generation | [`os/agent_file_state.c`](../../os/agent_file_state.c) |
| VFS hook 与 inode allocation | [`os/file.c`](../../os/file.c)、[`os/fs.c`](../../os/fs.c) |

公开查询字段和返回状态见 [API](../api.md)，事件等待与 workflow 收口见 [Workflow 运行时](workflow-runtime.md)。
