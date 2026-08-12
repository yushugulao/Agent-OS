# 面向 Agent 查询优化的文件系统扩展

一个工作流会不断产生检索结果、检查点和报告。只看文件名，无法得知文件处于哪个阶段、是否可用、依赖哪些数据，也无法分辨 inode 删除后又被新文件复用的情况。面向 Agent 查询优化的文件系统扩展因此在 uCore 内核中维护 Metadata Catalog（元数据目录），让 Agent 按属性与内容摘要描述目标文件；索引查询、结构化结果缓存、文件身份、Typed Watch（类型化订阅）和编辑租约放在一起管理。

## 文档索引

- [Metadata Catalog](#metadata-catalog)
- [登记与索引更新](#登记与索引更新)
- [查询方法](#查询方法)
- [Typed Watch](#typed-watch)
- [generation 缺口与重新同步](#generation-缺口与重新同步)
- [VFS 更新与 inode incarnation](#vfs-更新与-inode-incarnation)
- [编辑租约](#编辑租约)
- [性能结果](#性能结果)
- [测试与实现索引](#测试与实现索引)

<a id="显式文件目录"></a>
## Metadata Catalog

`agent_file_meta_set()` 把业务信息登记到真实 inode 上。每条记录同时保存业务字段和文件身份：

| 业务字段 | 文件身份与版本 |
| --- | --- |
| `project`、`workflow`、`run_id` | `dev`、`inum`、`incarnation` |
| `stage`、`kind`、`status`、`summary` | `size`、`fs_generation`、`updated_tick` |
| `logical_path`、`dependency_mask` | 工作流生命周期和文件访问范围 |

Metadata Catalog 最多保存 512 条记录，一次查询最多返回 8 项。普通文件不会自动进入目录，只有 `flags` 为零的显式元数据登记才会引起 Live Query 结果变化。ABI 虽然保留了 `PERSIST` 和 `AUTOSCAN` 的数值，当前实现会拒绝这两个标志。未登记文件仍按 uCore 原有的 `open`、`read`、`write` 和 `unlink` 路径处理。

`update_mask` 可以只修改部分业务字段，`AGENT_FILE_META_F_DELETE` 用于删除登记项。登记和更新要求当前 Agent（智能体）具有 `AGENT_CAP_META_WRITE`，查询要求 `AGENT_CAP_META_READ`；两类操作还要核对工作流生命周期与文件访问范围。

<a id="从登记到索引"></a>
## 登记与索引更新

用户态的 `agent_file_meta_set()` 通过 [`user/lib/syscall.c`](../../user/lib/syscall.c) 进入 `SYS_agent_file_meta_set`。随后，[`os/agent_metadata_objects.c`](../../os/agent_metadata_objects.c) 检查权限并复制用户参数。Metadata Catalog 在同一次元数据事务中解析选择条件、绑定 inode、写入主记录和索引，最后比较 Typed Watch 的结果集是否发生变化。

```text
agent_file_meta_set()
    -> sys_agent_file_meta_set()
    -> agent_file_meta_set_execute()
    -> agent_metadata_catalog_bind_volatile()
    -> 绑定 dev、inum 和 incarnation
    -> 更新 status、stage 和 kind 索引
    -> 增加 fs_generation
    -> 发布 Typed Watch 的集合变化事件
```

[`user/src/agentscan_ucore.c`](../../user/src/agentscan_ucore.c) 中的实际用法如下：

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

目录修改和查询快照共用同一份元数据事务。查询开始时，内核记下生命周期和 `fs_generation`，复制命中项后再检查一次。若这段时间内目录或可见文件的 generation 发生变化，本轮结果作废并在内核中重试。重试次数用完后返回 `RETRY`，避免把两个时刻的数据拼成一份结果。

<a id="查询规划器"></a>
## 查询方法

`agent_file_query` 可以按物理名称、逻辑路径、项目、工作流、运行编号、状态、阶段和类型进行等值查询，也支持长度受限的 `summary_contains`。允许使用索引时，内核依次尝试状态、阶段和类型索引，选取第一个可用项。设置 `AGENT_FILE_QUERY_SCAN` 则强制逐条扫描。

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

索引只负责缩小候选范围。每个候选项仍要经过 `agent_metadata_query_matches()`，重新核对全部查询条件、工作流生命周期和文件访问范围。因此，扫描与索引返回的 `agent_file_hit` 含义相同。

| 返回字段 | 含义 |
| --- | --- |
| `total_hits`、`returned`、`truncated` | 完整命中数、实际复制数、结果是否被截断 |
| `plan`、`used_index`、`index_bucket` | 实际查询方式、是否使用索引、采用的位图桶 |
| `scanned_records`、`candidate_records` | 检查的槽位数、当前文件访问范围内的候选数 |
| `plan_reason` | 强制扫描、禁用索引、使用状态索引、使用阶段索引、使用类型索引，或没有可用索引键 |
| `query_ticks`、`fs_generation` | 查询耗时和本次一致视图的更新序号 |
| `hits[].dev`、`hits[].inum`、`hits[].incarnation` | 命中文件的完整物理身份 |

一次查询最多复制 8 项，但 `total_hits` 仍报告完整命中数。当前 ABI 没有分页游标。查询实现位于 [`os/agent_metadata_query.c`](../../os/agent_metadata_query.c)。逐条扫描时，每处理 128 个槽位，内核都会检查是否需要让出执行权，避免大目录查询长时间占用内核。

## Typed Watch

定时查询只能看到前后两个时刻的文件状态。`agent_live_watch()` 把完整查询条件转换后存入内核的条件区，并将 Typed Watch 绑定到目标进程、控制编号、工作流生命周期和文件访问范围。安装成功后返回 `watch_id`、`initial_generation` 和 `catalog_generation`，Agent 从这个 generation 开始接收增量事件。

```c
struct agent_file_live_watch watch = {0};

watch.version = AGENT_FILE_LIVE_WATCH_VERSION;
watch.query.flags = AGENT_FILE_QUERY_USE_INDEX;
watch.query.max_hits = AGENT_FILE_QUERY_MAX_HITS;
strcpy(watch.query.project, "live-query");
strcpy(watch.query.status, "failed");
agent_live_watch(&watch);
```

元数据修改、文件写入、截断或删除发生后，内核比较文件在修改前后是否属于查询结果：

| 修改前 | 修改后 | `AGENT_EVENT_FILE_QUERY` 中的 `change` |
| --- | --- | --- |
| 不属于结果集 | 属于结果集 | `ENTER` |
| 属于结果集 | 仍在结果集中，且记录发生变化 | `UPDATE` |
| 属于结果集 | 不再属于结果集，或文件已经删除 | `LEAVE` |

事件的 `corr_id` 是文件 `fid`，`cause_sequence` 记录可见的更新序号，事件数据中还以紧凑的十六进制字段给出生命周期键。文件查询事件使用专门的 `FILE_QUERY` 类型，不再按旧接口的字符串包含关系过滤。Agent 通过 `agent_wait()` 读取事件，结束订阅时把原 `watch_id` 交给 `agent_live_unwatch()`。

<a id="generation-resync"></a>
## generation 缺口与重新同步

事件队列已满、增量事件发布失败，或 generation 出现缺口时，内核记录持续待确认的 `resync_generation`，并投递 `RESYNC_REQUIRED`。普通、连续的 generation 变化仍按 `ENTER`、`UPDATE` 或 `LEAVE` 发布，不会触发重新同步。

重新同步时，旧订阅不能提前移除。正确的交接顺序如下：

1. 保存事件中的 `resync_generation`；
2. 用原查询条件建立替代订阅，此时不要设置确认标志；
3. 调用 `agent_file_query()` 取得一致快照，只有 `truncated == 0` 时，这份结果才是完整基线；
4. 在旧订阅上设置 `ACK_RESYNC` 和保存的缺口序号，再调用 `agent_live_unwatch()`；确认缺口与移除旧订阅位于同一内核临界区，而替代订阅已经开始接收事件；
5. 由替代订阅继续接收快照之后的事件，并按顺序补入基线。若出现更大的 generation 缺口，重新执行上述步骤。

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

结果被截断时，应移除替代订阅，但不能确认缺口。旧订阅和待确认序号继续保留，调用方需要缩小查询范围。一次查询只有 8 个返回位置，当前又没有分页游标，因此被截断的结果不能作为完整基线。

一次确认只会清除不晚于所带 generation 的缺口，后来出现的更大缺口仍然等待处理。Workflow Fence（工作流屏障）会先处理删除占位记录和文件内容增量，并尝试投递重新同步提示。只要该工作流仍有未确认的缺口，或者提示尚未成功投递，Workflow Fence 就返回 `AGENT_STATUS_RETRY`。工作流关闭后，生命周期进入关闭中，后台清理程序随后处理剩余状态。

<a id="vfs-增量与-inode-incarnation"></a>
<a id="vfs-更新与-inode-代次号"></a>
## VFS 更新与 inode incarnation

已登记 inode 被写入或截断后，最新文件大小和内容更新序号会同步到目录。删除文件时，内核先保存生命周期键、文件访问范围、`dev`、`inum` 和 `incarnation`，再精确移除对应登记项。元数据事务忙碌时，这条删除记录会进入有界队列；稍后处理时仍按完整文件身份查找。

`incarnation` 用来区分 inode 编号的重复使用。uCore 的 `ialloc()` 每次重新分配空闲 inode 都会增加 `vfs_incarnation`；达到 `UINT_MAX` 后，该 inode 不再回收。目录选择器、内容回执、摘要缓存、编辑租约和待处理删除记录都保存这个字段，因此旧文件的状态不会落到新文件上。

```text
旧文件 A = {dev=1, inum=42, incarnation=7}
unlink(A)
新文件 B = {dev=1, inum=42, incarnation=8}

A 的元数据、租约和删除记录与 B 的文件身份不同
```

实现可见 [`os/fs.c`](../../os/fs.c) 中的 `ialloc()`，以及 [`os/agent_metadata_catalog.c`](../../os/agent_metadata_catalog.c) 中的文件身份比较。

## 编辑租约

编辑租约记录租约号、所有者、文件访问范围、inode 身份、基础版本和有效期，用来防止多个 Agent 同时提交同一文件：

```c
int agent_file_edit_begin(const char *path, uint64 flags, int ttl_ticks,
                          struct agent_file_edit_state *state);
int agent_file_edit_commit(uint64 lease_id, uint64 expected_version,
                           struct agent_file_edit_state *state);
int agent_file_edit_abort(uint64 lease_id);
```

`begin` 返回当前的 `base_version`。`commit` 和 `abort` 可以由租约所有者调用，也可以由同一工作流、同一文件访问范围内拥有 `AGENT_CAP_ORCHESTRATE` 的 Agent 调用。租约过期后会先被清理，再次提交或放弃时返回 `NOT_FOUND`；`incarnation` 或 `expected_version` 不符时返回 `STALE`。具有相应能力的 Agent 可以用 `ORCHESTRATOR_BREAK` 强制释放冲突租约。当前实现中，`BREAK_EXPIRED` 和未知标志不会改变处理结果。

## 性能结果

在同一份包含 96 条记录的数据上，我们进行了 16 组扫描查询与索引查询配对测试。扫描查询每次检查 97 条记录，索引查询只检查 2 条。16 组中索引查询的核心阶段全部更快，中位耗时由 34,712.5 微秒降至 13,293.5 微秒，中位加速比为 3.118 倍。候选对象从 97 条降到 2 条，说明 `status/stage/kind` 位图索引确实缩短了内核查询的主要路径。

参数测试覆盖 24、64、96 三种目录规模，以及 1、2、4、8 四种命中数。12 组参数下，中位加速比为 1.164 至 2.808 倍；三个目录规模下，状态索引的单次查询中位耗时约为 98.3、100.5 和 108.3 微秒。完整流程中，索引路径却只有 3/16 个批次更快，中位差值为 `+13.452 毫秒`。这说明索引设计已经改善核心查询，但核心窗口之外的聚合路径抵消了这部分收益；现有计时尚不能指出差值具体来自哪个环节，端到端工作流也不能直接沿用 3.118 倍这一核心阶段数字。详细图表见[性能测试](../performance.md#3-实时查询的核心阶段)。

## 测试与实现索引

| 验证入口 | 检查内容 |
| --- | --- |
| [`scripts/check-agent-live-query-fs.py`](../../scripts/check-agent-live-query-fs.py) | 显式登记、快照生命周期、完整查询条件、增量事件和重新同步调用 |
| [`scripts/test-agent-live-query-fs.py`](../../scripts/test-agent-live-query-fs.py) | 针对源码约束构造的错误输入与回归检查 |
| [`user/src/agentfs_ucore.c`](../../user/src/agentfs_ucore.c) | 部分更新、过期文件身份、同名文件重建、扫描与索引等价、截断和删除 |
| [`user/src/agentscan_ucore.c`](../../user/src/agentscan_ucore.c) | 普通文件不进入目录、`ENTER`、`UPDATE`、`LEAVE`、索引结果中的文件身份 |
| [`user/src/agentbench_ucore.c`](../../user/src/agentbench_ucore.c) | 扫描与索引工作量、查询耗时和不同规模数据 |

```bash
python3 -B scripts/check-agent-live-query-fs.py
python3 -B scripts/test-agent-live-query-fs.py
AGENT_TEST_CASE=agentfs_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentscan_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
AGENT_TEST_CASE=agentbench_ucore make agentos-test TOOLPREFIX=riscv64-linux-gnu-
```

| 模块 | 源码 |
| --- | --- |
| Metadata Catalog、位图和文件身份 | [`os/agent_metadata_catalog.c`](../../os/agent_metadata_catalog.c)、[`os/agent_metadata_catalog.h`](../../os/agent_metadata_catalog.h) |
| 查询与条件匹配 | [`os/agent_metadata_query.c`](../../os/agent_metadata_query.c) |
| 系统调用和对象操作 | [`os/agent_metadata_objects.c`](../../os/agent_metadata_objects.c)、[`os/agent_metadata.c`](../../os/agent_metadata.c) |
| Typed Watch 与重新同步 | [`os/agent_live_query_events.c`](../../os/agent_live_query_events.c)、[`os/agent_ipc.c`](../../os/agent_ipc.c) |
| 文件大小、摘要、租约和更新序号 | [`os/agent_file_state.c`](../../os/agent_file_state.c) |
| VFS 调用点与 inode 分配 | [`os/file.c`](../../os/file.c)、[`os/fs.c`](../../os/fs.c) |

公开查询字段和返回状态见 [API](../api.md)，事件等待与工作流关闭过程见[工作流运行时](workflow-runtime.md)。
