# 任务四：面向 Agent 查询优化的文件系统扩展

本文是 [design.md](design.md) 的任务四细节附录，重点说明 uCore 分支当前实现的文件元数据表、真实 inode 关联、`.agentmeta` 隐藏元数据文件、属性查询、索引路径和依赖查询。

## 目标

任务四希望操作系统为 Agent 提供更适合智能体使用的文件查询能力，使 Agent 不只按路径打开文件，还能按项目、运行、阶段、状态、类型、摘要等属性查询实验工件。

当前 uCore 分支实现的是内核级文件元数据服务：

- 支持最多 128 条文件元数据；
- 以真实文件的 `dev + inum` 作为主要身份；
- 要求 `physical_name` 能解析到 uCore 根目录中的真实文件名；
- 使用根目录 `.agentmeta` 隐藏元数据文件保存固定格式元数据表；
- 支持扫描查询；
- 支持 status、stage、kind 索引查询；
- 支持摘要查询；
- 支持依赖关系查询；
- 文件状态变化可以触发 Agent 事件；
- 查询结果会写入 Context Path。

当前没有实现后台线程持续扫描整棵目录。这是后续增强方向。

## 数据结构

`struct agent_file_meta` 表示一条文件元数据：

| 字段 | 说明 |
| --- | --- |
| `used` | 该槽位是否有效 |
| `fid` | 文件元数据 ID |
| `physical_name` | 物理文件名或内核演示名 |
| `logical_path` | Agent 可理解的逻辑路径 |
| `project` | 项目名 |
| `workflow` | 工作流名 |
| `run_id` | 实验运行 ID |
| `stage` | 实验阶段，如 prepare、align、analyze、report |
| `kind` | 工件类型，如 input、log、report |
| `status` | 状态，如 ok、failed |
| `summary` | 文件摘要 |
| `dependency_mask` | 依赖阶段位图 |
| `updated_tick` | 最近更新时间 |
| `flags` | 删除、持久化等元数据操作标志 |
| `dev` | 真实文件设备号 |
| `inum` | 真实文件 inode 号 |
| `size` | 真实文件大小 |
| `fs_generation` | 文件系统侧更新代数 |
| `update_mask` | 本次更新哪些字段 |

查询结构 `struct agent_file_query` 以空字符串表示“不限制该字段”。结果结构 `struct agent_file_query_result` 返回命中数、返回数、扫描数、是否使用索引、是否截断、tick 和最多 8 条命中。

内核启动时 `agentinit()` 会把 status、stage、kind 三类索引桶初始化为 `-1`。因此即使测试程序在调用 `agent_file_meta_init()` 前先执行带索引查询，也会返回 0 条命中，而不会沿着未初始化链表扫描。

## inode 关联和隐藏元数据文件

任务四不是只保存一张脱离文件系统的演示表。当前实现会把 Agent 元数据绑定到真实 uCore 根目录文件：

1. `agent_file_meta_init()` 优先读取 `.agentmeta` 隐藏元数据文件。
2. 如果 `.agentmeta` 不存在，内核创建默认演示文件，例如 `r42align`、`r42report`。
3. 每条持久化元数据保存 `physical_name`、`dev`、`inum`、`size` 和 `fs_generation`。
4. `fileopen(O_CREATE)`、写入、截断、删除会通知 Agent 子系统刷新或删除关联元数据。
5. `agent_file_meta_set()` 支持 `AGENT_FILE_META_F_DELETE` 删除属性，支持 `AGENT_FILE_META_F_PERSIST` 写入 `.agentmeta`。

这套实现让查询结果中的文件身份可以追溯到真实 inode，同时保留 Agent 需要的 project、workflow、run、stage、kind、status 等高层属性。

## 初始化数据

`agent_file_meta_init()` 安装或加载演示数据，用于 `agentfinal_ucore`、`agentfs_ucore`、`agentbench_ucore` 和 `labdemo_ucore`。演示数据模拟一个实验流水线：

| 阶段 | 含义 |
| --- | --- |
| prepare | 准备输入数据 |
| align | 对齐或预处理 |
| analyze | 分析阶段 |
| report | 报告生成 |
| archive | 归档 |

`labdemo_ucore` 中由 orchestrator Agent 调用 `agent_file_meta_set()`，把 `RUN-042` 的 align 阶段状态改为 failed，从而触发 sentinel Agent。普通进程不能直接初始化或修改这张全局元数据表。

`agentsecurity_ucore` 还会在初始化前先执行一次 indexed query，确认未初始化索引不会卡住；随后同时构造 `RUN-042` 和 `RUN-999` 两个 failed run，用于验证恢复动作只修改 selector 指定的目标 run。

`agentfs_ucore` 会创建额外真实文件，绑定自定义元数据，并验证字段清空、文件删除清理和 selector 未命中。它还会生成接近 128 条真实文件元数据，让扫描路径和索引路径的 `scanned_records` 差异明显。

## 查询路径

当前支持两条查询路径：

| 路径 | 使用方式 | 说明 |
| --- | --- | --- |
| 扫描路径 | `AGENT_FILE_QUERY_SCAN` | 遍历全部 128 条元数据槽 |
| 索引路径 | `AGENT_FILE_QUERY_USE_INDEX` | 根据 status、stage、kind 的索引链减少候选记录 |

索引路径适合 Agent 常见查询，例如：

- 查询某个 run 的 failed 文件；
- 查询某个 stage 的输出；
- 查询某类 report 文件；
- 查询恢复后状态为 ok 的报告。

## 工具接口

任务四能力既可以通过 syscall 直接调用，也可以通过工具调用进入：

| 接口 | 说明 |
| --- | --- |
| `agent_file_meta_init()` | 初始化文件元数据表 |
| `agent_file_meta_set(meta)` | 插入或更新一条元数据 |
| `agent_file_query(query, result)` | 结构化查询 |
| `AGENT_TOOL_QUERY_FILE` | 工具方式查询文件 |
| `AGENT_TOOL_READ_FILE_SUMMARY` | 按 selector 读取摘要 |
| `AGENT_TOOL_DEPENDENCY_QUERY` | 查询某阶段影响范围 |
| `AGENT_TOOL_WRITE_REPORT` | 写入恢复报告状态 |

`AGENT_TOOL_QUERY_FILE` 支持属性条件串，例如：

```text
project=lab-gene-x;run_id=RUN-042;status=failed
```

恢复工具 `AGENT_TOOL_RERUN_STAGE` 支持同样的 selector 风格，例如：

```text
stage=align;run_id=RUN-999;project=lab-gene-x
```

报告写入工具 `AGENT_TOOL_WRITE_REPORT` 也支持 selector 风格，例如：

```text
stage=report;run_id=RUN-999;project=lab-gene-x
```

内核会同时匹配 stage、run_id 和 project。这样恢复动作和报告写入不会因为同一个 stage 上存在多个 run 而误修改其他文件元数据。

`query_file` 对空查询、未知 key 和坏格式片段返回 `AGENT_STATUS_BAD_PARAM`，不会静默忽略错误条件。

## 与 Context Path 的关系

文件查询成功后会追加 Context record。这样 Agent 的“看到什么文件、做出什么判断”可以在 Context Path 中回放。

文件元数据写接口要求调用者是 Agent 且具备 `AGENT_CAP_META_WRITE`。当前只有 orchestrator 拥有该能力；sentinel、investigator 和 recovery 可按各自能力读取元数据或内容，但不能直接改写全局文件状态。

在 `labdemo_ucore` 中：

1. sentinel 查询失败文件；
2. investigator 查询摘要；
3. investigator 查询依赖；
4. recovery 查询报告；
5. 这些工具调用都会进入各自 Agent 的 Context Path。

## 与 Agent Loop 的关系

`agent_file_meta_set()` 更新文件状态时，会检查是否有 Agent 注册了匹配 watch。如果某个 Agent 监听 `status=failed`，更新为 failed 的文件会产生 `AGENT_EVENT_FILE_STATUS`，并唤醒等待中的 Agent。

这正是 `labdemo_ucore` 的启动条件：

1. sentinel 注册 failed 状态监听；
2. orchestrator 把 align 阶段文件更新为 failed；
3. sentinel 从 `agent_wait()` 返回；
4. sentinel 查询失败文件并启动后续分析。

## 依赖关系查询

`dependency_mask` 用位图表示某阶段影响哪些阶段：

| 位 | 阶段 |
| ---: | --- |
| 0 | prepare |
| 1 | align |
| 2 | analyze |
| 3 | report |
| 4 | archive |

`dependency_query("align")` 返回：

```text
align+analyze+report+archive
```

说明 align 阶段失败会影响自身和后续阶段。

## 性能验证

`agentbench_ucore` 对比扫描路径和索引路径：

```text
agentbench_ucore: file_scan_query ops=64 ticks=5 ops_per_tick=12 speedup_x100=100
agentbench_ucore: file_index_query ops=64 ticks=2 ops_per_tick=32 speedup_x100=250
```

这证明当前系统同时具备：

- 可观测扫描路径；
- 可观测索引路径；
- 可输出 `used_index` 和 `scanned_records`；
- 能用性能表解释索引价值。

## 综合场景中的证据

`labdemo_ucore` 中的文件查询证据：

```text
agentos:event type=TOOL_CALL role=sentinel tool=query_file hits=1 used_index=1 seq=4
labdemo_ucore: investigator reason=align output is ready before injected failure
labdemo_ucore: affected stages=align+analyze+report+archive
labdemo_ucore: final report_query hits=2 used_index=1 scanned=7
```

这些输出说明：

- sentinel 能按属性查询失败文件；
- investigator 能读取摘要和依赖；
- recovery 后能查询报告文件；
- 查询路径使用索引。

`agentsecurity_ucore` 中的文件查询和恢复范围证据：

```text
agentsecurity_ucore: preinit_index_query=1
agentsecurity_ucore: scoped_rerun=1
agentsecurity_ucore: scoped_report=1
```

这些输出说明索引初始化前查询安全，且 recovery 只恢复和写入 selector 指定的 run。

`agentfs_ucore` 中的真实文件关联证据：

```text
agentfs_ucore: default_inode dev=1 inum=11 scanned=2
agentfs_ucore: custom_inode dev=1 inum=17 size=7
agentfs_ucore: bulk_index scan=108 index=6 hits=1
agentfs_ucore: clear_status=1
agentfs_ucore: delete_clears_metadata=1
agentfs_ucore: missing_selector_not_found=1
agentfs_ucore: passed
```

## 当前限制

| 限制项 | 说明 |
| --- | --- |
| 元数据来源 | 当前来自 `.agentmeta`、默认真实文件和 `agent_file_meta_set()` |
| 真实文件系统扫描 | 尚未实现后台线程持续扫描整棵目录 |
| 持久化索引 | 元数据表可写入 `.agentmeta`；内存索引启动后根据元数据重建 |
| 查询语法 | 当前支持结构体字段查询和简单 `key=value` 字符串 |
| 查询规模 | 当前最多 128 条元数据，最多返回 8 条 hit |

## 后续增强

后续可以把任务四推进为完整文件系统能力：

- 后台扫描真实目录；
- 把更多 Agent 元数据直接纳入 inode 或目录项；
- 支持索引增量持久化；
- 支持更复杂查询条件；
- 支持按时间、大小、版本、生产者 Agent 查询；
- 将文件查询结果直接喂给 LLM Gateway。
