<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 任务四：Agent 子系统内存元数据表版本的文件查询扩展

本文说明当前任务四实现。实现目标是让 Agent 不再靠遍历文件名或手工解析日志定位对象，而是通过内核维护的文件元数据表，按科研平台对象字段快速查询实验工件。

## 设计目标

当前阶段围绕“夜间实验批量复测故障诊断与受控恢复”场景实现：

- 支持 `project`、`workflow`、`run_id`、`stage`、`kind`、`status`、`summary`、`logical_path` 字段。
- 支持 `fid` 精确查询，用于从短事件 payload 回查完整元数据。
- 支持扫描路径和索引路径两种查询方式。
- 查询结果返回命中数、返回数、是否使用索引、是否截断、扫描记录数和查询 tick。
- 支持依赖关系查询，用于判断失败阶段影响范围。
- 查询动作写入 Context Path，供报告和审计回放。
- 不直接修改 xv6 inode 主结构，避免破坏教学文件系统稳定性；当前先在 Agent 子系统中维护内核元数据表。

## 核心数据结构

公开 ABI 位于 `kernel/agent.h`：

```c
struct agent_file_meta {
  int used;
  int fid;
  char physical_name[32];
  char logical_path[80];
  char project[16];
  char workflow[24];
  char run_id[16];
  char stage[16];
  char kind[16];
  char status[16];
  char summary[96];
  uint64 dependency_mask;
  uint64 updated_tick;
  uint64 update_mask;
};
```

查询请求：

```c
struct agent_file_query {
  uint64 flags;
  int fid;
  int max_hits;
  char physical_name[32];
  char logical_path[80];
  char project[16];
  char workflow[24];
  char run_id[16];
  char stage[16];
  char kind[16];
  char status[16];
  char summary_contains[16];
};
```

查询结果最多直接返回 8 条 hit。超过容量时设置 `truncated=1`，总命中数仍保存在 `total_hits`。

## 查询引擎

实现位置：[kernel/agent.c](../kernel/agent.c)。

当前元数据表容量为 `AGENT_FILE_META_MAX = 128`。初始化时写入 `RUN-042` 的核心工件，并补充历史工件到 112 条默认记录，保留 16 个空槽用于测试和演示新 artifact 插入。这样扫描路径和索引路径仍有可测差异，同时 `agent_file_meta_set()` 的插入语义可复现。

索引当前覆盖：

- `status`
- `run_id`
- `stage`
- `kind`

查询逻辑：

1. 如果请求设置 `AGENT_FILE_QUERY_SCAN`，遍历全部已用记录。
2. 如果请求设置 `AGENT_FILE_QUERY_USE_INDEX` 且包含 `status/run_id/stage/kind` 之一，内核先估算这些字段对应索引桶的候选数量，选择候选最少的索引桶。
3. 对候选记录继续检查所有非空字段，确保扫描路径和索引路径语义一致。

如果查询包含 `fid > 0`，内核会按工件 ID 精确匹配；如果 payload 形式使用 `fid=4`，也会转换成同一查询条件。空查询、未知 key、空 value 或坏格式片段统一返回 `AGENT_STATUS_BAD_PARAM`，避免用户以为查询成功但实际条件被静默忽略。

## 工具和系统调用

任务四新增 syscall：

| syscall | 说明 |
| --- | --- |
| `agent_file_meta_init()` | Orchestrator Agent 初始化演示元数据表 |
| `agent_file_meta_set(meta)` | 具备元数据写权限的 Agent 插入或合并更新一条文件元数据；状态变化可触发事件 |
| `agent_file_query(query, result)` | Agent-only 属性查询接口，成功后追加 Context Path |

任务四相关工具：

| 工具 | 说明 |
| --- | --- |
| `file_meta_init` | 初始化内核元数据表 |
| `query_file` | 兼容原路径查询；payload 为 `key=value` 时走属性查询 |
| `read_file_summary` | 按物理名、逻辑路径或 stage 返回摘要 |
| `dependency_query` | 查询某个 stage 的影响范围 |

`query_file` 的结构化 payload 示例：

```text
project=lab-gene-x;run_id=RUN-042;status=failed
fid=4
```

`agent_file_meta_set()` 还支持 `AGENT_FILE_META_DELETE`。调用方设置该 flag，并提供 `fid`、`physical_name` 或 `logical_path` 中至少一个定位条件后，内核删除匹配元数据并重建索引。删除只影响 Agent 子系统内存表，不删除 xv6 文件。

## 验证证据

`labdemo` 关键输出：

```text
labdemo: query_file project=lab-gene-x run=RUN-042 status=failed hits=1 scanned=1 used_index=1 first=lab_RUN042_align_err
labdemo: affected stages=align+analyze+report+archive
labdemo: final report_query hits=1 used_index=1 scanned=9
```

`labbench` 关键输出：

```text
labbench: file_semantics scan_hits=2 index_hits=2 scan_scanned=112 index_scanned=2 report_scanned=9 empty=0 truncated=1
labbench: metadata_dependency=1 scoped_rerun=1 scoped_report=1 history_preserved=1 single_report=1 mask_text=1 dep_clear=1 insert=1 delete=1
labbench: file_scan_query ops=32768 ticks=36 ops_per_tick=910 speedup_x100=100
labbench: file_index_query ops=32768 ticks=22 ops_per_tick=1489 speedup_x100=163
labbench: file_status_partial_payload fid=4 stage=align run_id=RUN-042 full_lookup=1
```

解释：

- `status=failed` 查询走 status 索引，只扫描失败桶，能快速定位 `lab_RUN042_align_err`。
- `stage=align` 在性能测试中同时走扫描和索引路径；样例输出中索引查询只扫描 2 条候选，约为扫描查询的 2 倍吞吐。
- `run_id=RUN-042;kind=report` 查询会选择更小的 `run_id` 索引桶，最终报告查询从全表或宽桶扫描降到 9 条候选。
- `fid=4` 查询可从文件状态事件短 payload 回查完整 `lab_RUN042_align_err` 记录。
- `dependency_query align` 优先读取当前运行中同 stage 最近更新的 `dependency_mask`，默认演示输出为 `align+analyze+report+archive`，支持“只重跑受影响阶段”的恢复策略。`dependency_query`、`rerun_stage` 和 `write_report` 也支持 `run_id=...;stage=...` 形式的 selector，便于限定目标运行。
- `labbench` 显式验证自定义依赖掩码会驱动 `rerun_stage` 的实际更新范围，任意合法 bitmask 会按 bit 输出文本，`dependency_mask=0` 可通过 `update_mask` 清除，新工件可插入预留槽，也可通过 `AGENT_FILE_META_DELETE` 删除。
- `write_report` 只更新 selector 指定运行内的 `lab_RUN042_recovery_report`，最终查询限定该物理工件后命中数为 1；`labbench` 还插入 `RUN-999` 的报告记录，确认不会被 `RUN-042` 的写报告动作误更新。

## 当前实现限制

- 当前实现是 Agent 子系统内核元数据表，不是 inode 结构永久扩展。
- 摘要查询是短文本字段匹配，不是全文检索或语义向量检索。
- 索引覆盖 `status/run_id/stage/kind`，后续最终成品可继续加入 summary index、复合索引和持久化索引文件。
