# 任务四：面向 Agent 查询优化的文件系统扩展

本文是 [design.md](design.md) 的任务四细节附录，重点说明 uCore 分支当前实现的文件元数据表、真实 inode 关联、私有 `.agentmeta` 元数据文件、属性查询、索引路径、内容摘要工具、通用依赖注册/查询和查询历史驱动的预取提示。

## 目标

任务四希望操作系统为 Agent 提供更适合智能体使用的文件查询能力，使 Agent 不只按路径打开文件，还能按 namespace、object_id、label、state、type、summary 等属性查询文件对象。科研工件是当前演示负载使用的一类文件对象，不是内核唯一支持的对象模型。

当前 uCore 分支实现的是内核级文件元数据服务：

- 支持最多 128 条文件元数据；
- 以真实文件的 `dev + inum` 作为主要身份；
- 要求 `physical_name` 能解析到 uCore 根目录中的真实文件名；
- 使用根目录私有 `.agentmeta` 元数据文件保存固定格式元数据表；
- 支持扫描查询；
- 支持 state/label/type 索引查询，ABI 字段兼容保留为 status/stage/kind；
- 支持同一文件元数据代数下的查询结果缓存；
- 支持摘要查询；
- 支持受权 Agent 读取真实文件短预览和 FNV-1a 内容指纹；
- 支持同一文件元数据代数下的真实文件内容摘要缓存；
- 支持依赖关系注册和查询；
- 支持根据历史查询和对象标签依赖生成 metadata 预取提示；
- 支持按当前 span 查询跨 Agent 汇总的 metadata 预取提示；
- 文件状态变化可以触发 Agent 事件；
- 调度器空隙会分批扫描 uCore 根目录，自动维护真实文件的元数据和索引；
- 查询结果会写入 Context Path。

当前实现聚焦 uCore 根目录短文件名，不把范围扩大成多级目录递归扫描或通用全文索引。

## 数据结构

`struct agent_file_meta` 表示一条文件元数据：

| 字段 | 说明 |
| --- | --- |
| `used` | 该槽位是否有效 |
| `fid` | 文件元数据 ID |
| `physical_name` | 物理文件名或内核演示名 |
| `logical_path` | Agent 可理解的逻辑路径 |
| `project` | namespace；科研 demo 中用作项目名 |
| `workflow` | 工作流名 |
| `run_id` | 实验运行 ID |
| `stage` | 对象 label；科研 demo 中用作阶段名 |
| `kind` | 对象 type；科研 demo 中用作工件类型 |
| `status` | 对象 state，如 ok、failed |
| `summary` | 文件摘要 |
| `dependency_mask` | 对象标签依赖位图 |
| `updated_tick` | 最近更新时间 |
| `flags` | 删除、持久化等元数据操作标志 |
| `dev` | 真实文件设备号 |
| `inum` | 真实文件 inode 号 |
| `size` | 真实文件大小 |
| `fs_generation` | 文件系统侧更新代数 |
| `update_mask` | 本次更新哪些字段 |

查询结构 `struct agent_file_query` 以空字符串表示“不限制该字段”。结果结构 `struct agent_file_query_result` 返回命中数、返回数、扫描数、是否使用索引、查询计划、计划原因、候选记录数、索引桶、是否截断、tick 和最多 8 条命中。

`struct agent_file_prefetch_hint` 表示一条预取提示。它保存触发提示的 Context sequence、span id、source pid、target pid、source fid、target fid、原因 flags、排序分数、文件元数据代数、候选记录数量和一份目标文件 hit 快照。每个 Agent 本地提示容量固定为 8 条，属于当前 Agent 的内核 PCB 状态；全局 span 预取提示总线容量固定为 32 条，用于同一因果链上的跨 Agent 查询。

内核启动时 `agentinit()` 会把 status、stage、kind 三类索引桶初始化为 `-1`。因此即使测试程序在调用 `agent_file_meta_init()` 前先执行带索引查询，也会返回 0 条命中，而不会沿着未初始化链表扫描。

## 文件节点关联和私有元数据文件

任务四不是只保存一张脱离文件系统的演示表。当前实现会把 Agent 元数据绑定到真实 uCore 根目录文件：

1. `agent_file_meta_init()` 会先强制重新加载 `.agentmeta` 私有元数据文件。
2. 如果 `.agentmeta` 不存在、格式错误或没有有效记录，内核安装空元数据表，由用户态 demo 再写入需要的对象记录。
3. 每条持久化元数据保存 `physical_name`、`dev`、`inum`、`size` 和 `fs_generation`。
4. `fileopen(O_CREATE)`、写入、截断、删除会通知 Agent 子系统刷新或删除关联元数据。
5. `agent_file_meta_set()` 支持 `AGENT_FILE_META_F_DELETE` 删除属性，支持 `AGENT_FILE_META_F_PERSIST` 写入 `.agentmeta`。

`.agentmeta` 是 Agent 子系统内部后端文件。普通文件 syscall 直接 `open(".agentmeta")`、`open(O_CREATE)` 或 `unlink(".agentmeta")` 会返回 `-1`；Agent 子系统内部 helper 仍可读写它，用于持久化和重新加载元数据。

这套实现让查询结果中的文件身份可以追溯到真实 inode，同时保留 Agent 需要的 namespace、workflow、run、label、type、state 等高层属性。为了兼容已有用户态测试，结构体字段名仍使用 project/stage/kind/status；字符串 selector 同时接受 `namespace/object_id/label/type/state` 等通用字段名。

## 根目录自动扫描

任务四的自动维护路径由 Agent 子系统和调度器配合完成：

1. `agent_file_meta_init()` 或 `file_meta_init` 工具启用扫描能力。
2. timer tick 和文件创建、写入、截断、删除 hook 只标记 `file_scan_pending`，不在中断上下文做文件系统遍历。
3. 调度器空隙调用 `agent_background_maintain()`，每次最多处理 16 个根目录项，避免一次扫描长时间占用内核。
4. 扫描会跳过 `.agentmeta`，只为真实根目录文件建立 `AGENT_FILE_META_F_AUTOSCAN | AGENT_FILE_META_F_PERSIST` 元数据。
5. 自动元数据使用真实 `dev + inum + size` 作为身份，并填入 `project=root`、`workflow=background-scan`、`run_id=ROOT`、`stage=scan` 等默认属性。
6. 完整扫描结束后，已经不存在的自动元数据会被清理；手动写入的实验元数据不会被扫描流程误删。
7. 元数据变化后重建 status、stage、kind 索引，并按需写回 `.agentmeta`。

`agentscan_ucore` 专门验证这条路径：系统先发现镜像中已有的 `usershell`，再通过普通文件 syscall 创建 `autoscan_ok`，确认无需显式调用 `agent_file_meta_set()` 也能查询到该文件；删除该文件后，下一轮扫描会清理对应元数据。

## 用户态初始化数据

`agent_file_meta_init()` 只负责重新加载 `.agentmeta`、重建索引和启用扫描。如果 `.agentmeta` 不存在、格式错误或没有有效记录，内核安装空元数据表。科研 demo 需要的 RUN-042、lab-gene-x 和对象依赖由用户态 orchestrator 调用 `agent_file_meta_set()` 写入。

`labdemo_ucore` 中由 orchestrator Agent 写入科研平台演示数据，再把 `RUN-042` 的 align 对象状态改为 failed，从而触发 sentinel Agent。普通进程不能直接初始化或修改这张全局元数据表。

`agentsecurity_ucore` 还会在初始化前先执行一次 indexed query，确认未初始化索引不会卡住；随后同时构造 `RUN-042` 和 `RUN-999` 两个 failed run，用于验证通用 action/artifact 更新只修改 selector 指定的目标 run。

`agentfs_ucore` 会创建额外真实文件，绑定自定义元数据，并验证重新调用 `agent_file_meta_init()` 时自定义数据来自 `.agentmeta` 重新加载，而不是被空表覆盖。它还会验证字段清空、文件删除清理、selector 未命中、scan/index 返回语义一致、结果超过 `max_hits` 时设置 `truncated`，并生成接近 128 条真实文件元数据，让扫描路径和索引路径的 `scanned_records` 差异明显。重复执行同一个非强制扫描查询时，它会验证 `CACHE_HIT` 原因位；随后更新文件状态，确认旧代数缓存不会继续返回过期结果。

## 查询路径

当前支持两条查询路径：

| 路径 | 使用方式 | 说明 |
| --- | --- | --- |
| 扫描路径 | `AGENT_FILE_QUERY_SCAN` | 遍历全部 128 条元数据槽 |
| 索引路径 | `AGENT_FILE_QUERY_USE_INDEX` | 根据 state/label/type 索引链减少候选记录；ABI 字段名兼容 status/stage/kind |

查询结果会解释本次选择：

| 字段 | 含义 |
| --- | --- |
| `plan` | 0 表示扫描，1/2/3 分别表示 state/label/type 索引 |
| `plan_reason` | 位标记，说明是强制扫描、未请求索引、使用了某类索引、命中查询缓存或没有可用索引键 |
| `index_bucket` | 命中的索引桶；扫描路径为 -1 |
| `candidate_records` | 本次候选记录数量，用于和全量扫描规模对比 |
| `fs_generation` | 查询时文件元数据服务的更新代数 |

这使 Agent 可以直接知道“为什么这次按 status 索引查，只检查了 6 条候选记录”，而不是只能看到最终命中结果。

## 查询结果缓存

任务四在索引路径之上加入了一个 8 槽内核查询结果缓存。缓存 key 包含查询 flags、归一化后的 `max_hits` 以及 physical、logical、project、workflow、run、stage、kind、status、summary_contains 等查询字段。缓存 value 保存完整 `agent_file_query_result`。

缓存只服务扫描未进行时的非 `AGENT_FILE_QUERY_SCAN` 命中查询。强制扫描查询总是重新遍历元数据，用于测试、诊断和对照索引路径；空结果不进入缓存，避免把“文件暂时不存在”当成稳定事实。自动扫描进行中也不读写缓存，因为这时元数据 generation 可能已经变化而索引链尚未完成重建。缓存条目记录生成时的 `fs_generation`；每次文件元数据新增、修改、删除或自动扫描造成 generation 增加后，旧缓存条目会因为 generation 不一致而被跳过，不需要额外遍历清空缓存表。

命中缓存时，结果仍保留原查询计划、候选记录数、命中信息和 `fs_generation`，并额外设置 `AGENT_FILE_QUERY_REASON_CACHE_HIT`。因此 `plan_reason=68` 表示同一次查询既来自 status 索引计划，又命中了查询结果缓存。缓存命中不会追加额外的扫描成本，`query_ticks` 置为 0。

这个设计面向 Agent 的常见访问模式：同一个页面、同一个 Agent worker 或同一轮多 Agent 协作，经常会重复读取“某 run 的 failed artifact”“某 label 的输出”“恢复后的 report”。内核用 generation-aware cache 避免重复走索引链，同时保留强制扫描路径用于检查结果一致性。

索引路径适合 Agent 常见查询，例如：

- 查询某个 run 的 failed 文件；
- 查询某个 label 的输出；
- 查询某类 report 文件；
- 查询恢复后状态为 ok 的报告。

## 文件内容摘要

metadata 查询说明“哪个文件符合条件”，内容摘要工具说明“这个真实文件的开头内容和内容指纹是什么”。`AGENT_TOOL_READ_FILE_DIGEST` 使用 `read_file_digest` 工具名，输入为 `selector:string`。selector 可以是物理文件名、逻辑路径、label，也可以是 `namespace=...;run_id=...;label=...;state=...` 或兼容的 `project=...;run_id=...;stage=...;status=...` 属性过滤串。属性过滤命中多条时读取第一条命中文件。

该工具要求调用者具备 `AGENT_CAP_CONTENT_READ`。sentinel 这类只具备 metadata 读权限的 Agent 会收到 `AGENT_STATUS_DENIED`；`.agentmeta` 私有后端文件不会通过该工具暴露。工具返回：

| 字段 | 含义 |
| --- | --- |
| `value0` | 真实文件大小 |
| `value1` | 参与 FNV-1a 指纹计算的字节数，最多 4096 |
| `value2` | FNV-1a 64 位内容指纹 |
| `result` | 文件开头短预览 |

这不是全文搜索和内容倒排索引。它提供的是轻量内容证据：Agent 在得到 metadata 命中后，可以进一步确认文件确实存在、文件内容和预期一致，并把该工具调用写入 Context Path、timeline 和性能输出。统一 timeline 中可以按 `tool_id=AGENT_TOOL_READ_FILE_DIGEST` 查询到该记录，`value0/value1/value2/text` 分别保留文件大小、参与计算字节数、内容指纹和短预览。

### 内容摘要缓存

`read_file_digest` 还维护 8 槽内核 digest cache。缓存 key 是真实 inode 身份和文件内容版本：

- `dev`
- `inum`
- `size`
- `content_generation`

缓存 value 保存文件大小、参与计算字节数、FNV-1a 指纹和短预览。重复读取同一个绑定了 Agent metadata 的真实文件时，第二次可以直接复用缓存结果。文件创建、写入、截断或删除会更新内容版本，旧缓存条目会被跳过；单纯 metadata 更新不会让同一文件内容摘要缓存失效。

未绑定 Agent metadata 的普通文件不会进入 digest cache。这样做是为了避免普通文件同尺寸改写时，Agent 子系统缺少可靠内容版本信号而返回过期内容证据。缓存计数通过 `agent_info.file_digest_cache_hits` 和 `agent_info.file_digest_cache_misses` 暴露，供测试和性能材料直接引用。

## 查询历史驱动的预取提示

文件查询命中后，内核会把本次查询视为 Agent 当前探索路径的一部分，并根据源文件的对象标签依赖推导后续可能需要关注的文件 metadata。例如科研 demo 查询 `RUN-042` 的 `align` label 后，如果用户态注册的依赖关系显示 align 会影响 analyze、report 和 archive，内核会在同一 namespace/workflow/run 中查找这些后续对象的元数据，并生成预取提示。

预取提示的生成条件：

1. 当前进程必须是 Agent；
2. 文件查询必须至少命中一条记录；
3. 命中的 source 文件有 `dependency_mask`；
4. target 文件与 source 位于同一 namespace/workflow/run；
5. target label 位于 source 的依赖集合中；
6. target label 可以通过 label 索引解释候选记录数量。

每个 Agent 最多保留 8 条本地预取提示。相同 target fid 的提示会被更新；容量满后按 FIFO 方式替换较早提示。带有 span 的提示还会写入全局 span 预取提示总线，最多保留 32 条，并记录 source pid 和 target pid。提示使用 `AGENT_FILE_PREFETCH_REASON_DEPENDENCY`、`AGENT_FILE_PREFETCH_REASON_SAME_RUN`、`AGENT_FILE_PREFETCH_REASON_PENDING`、`AGENT_FILE_PREFETCH_REASON_STAGE_INDEX`、`AGENT_FILE_PREFETCH_REASON_HANDOFF` 和 `AGENT_FILE_PREFETCH_REASON_SPAN_BUS` 说明生成原因。其中 `HANDOFF` 表示提示来自另一个 Agent 的 message 事件交接，`SPAN_BUS` 表示该提示已进入同一 span 的全局提示总线。

读取接口：

```c
int agent_file_prefetch_snapshot(struct agent_file_prefetch_hint *hints, int max);
int agent_file_prefetch_span_snapshot(struct agent_file_prefetch_hint *hints, int max);
```

`agent_file_prefetch_snapshot()` 查询当前 Agent 自己可见的提示。`agent_file_prefetch_span_snapshot()` 查询当前 Agent 的 `current_span_id` 对应的全局提示，只返回同一 span 下的记录；当前 Agent 尚未进入 span 时返回 0。两者在 `max=0` 时只返回当前提示数量；`max>0` 会按产生顺序复制提示。普通进程调用返回 `-1`；没有 `META_READ` 能力的 Agent 返回 `AGENT_STATUS_DENIED`。

这项能力不是完整文件内容预加载。它的作用是把“Agent 查到一个阶段后，后续大概率会继续查哪些相关工件”提前交给内核表达，减少下一轮 Agent 继续做宽泛扫描或重新拼接依赖关系的成本。Agent 之间通过 message 事件协作时，内核可以把发送者当前可见的提示复制给接收者，让接收者直接从自己的 snapshot 中读取上游提示，而不需要从消息文本中解析策略字段。
同一 span 的提示总线进一步减少了跨 Agent 协作时的状态拼接成本：接收者不仅能读取自己 PCB 中被交接来的提示，还能用 span 查询看到“这条因果链中是谁产生了提示、提示交给了谁、目标工件是什么”。这为后续宿主机科研 Agent 对比演示提供了更直观的内核级协作证据。

## 工具接口

任务四能力既可以通过 syscall 直接调用，也可以通过工具调用进入：

| 接口 | 说明 |
| --- | --- |
| `agent_file_meta_init()` | 初始化文件元数据表 |
| `agent_file_meta_set(meta)` | 插入或更新一条元数据 |
| `agent_file_query(query, result)` | 结构化查询 |
| `agent_file_prefetch_snapshot(hints, max)` | 读取当前 Agent 的 metadata 预取提示 |
| `agent_file_prefetch_span_snapshot(hints, max)` | 读取当前 span 的全局 metadata 预取提示 |
| `AGENT_TOOL_QUERY_FILE` | 工具方式查询文件 |
| `AGENT_TOOL_READ_FILE_SUMMARY` | 按 selector 读取摘要 |
| `AGENT_TOOL_READ_FILE_DIGEST` | 按 selector 读取真实文件短预览和内容指纹 |
| `AGENT_TOOL_DEPENDENCY_QUERY` | 查询某个对象 label 的影响范围 |
| `AGENT_TOOL_ACTION_COMMIT` | 按 selector 提交通用 Agent 动作 |
| `AGENT_TOOL_ARTIFACT_UPDATE` | 按 selector 更新通用工件或结果对象状态 |

`AGENT_TOOL_QUERY_FILE` 支持属性条件串，例如：

```text
project=lab-gene-x;run_id=RUN-042;status=failed
```

通用动作工具 `AGENT_TOOL_ACTION_COMMIT` 支持同样的 selector 风格，例如：

```text
label=align;run_id=RUN-999;namespace=lab-gene-x
```

通用工件更新工具 `AGENT_TOOL_ARTIFACT_UPDATE` 也支持 selector 风格，例如：

```text
label=report;run_id=RUN-999;namespace=lab-gene-x
```

内核会同时匹配 label、run_id 和 namespace。这样动作提交和工件更新不会因为同一个 label 上存在多个 run 而误修改其他文件元数据。`AGENT_TOOL_RERUN_STAGE` 和 `AGENT_TOOL_WRITE_REPORT` 仍保留为旧 demo 兼容工具，但它们内部调用同一套通用状态更新路径，并把事件 action 与重复请求判断归入 `action_commit` 或 `artifact_update`。

`query_file` 对空查询、未知 key 和坏格式片段返回 `AGENT_STATUS_BAD_PARAM`，不会静默忽略错误条件。

## 与 Context Path 的关系

文件查询成功后会追加 Context record。这样 Agent 的“看到什么文件、做出什么判断”可以在 Context Path 中回放。Context record 同时保存 cause/span：如果这次文件查询来自前一个事件或工具调用，它会指向对应的前序 sequence，并延续当前 span。

文件元数据写接口要求调用者是 Agent 且具备 `AGENT_CAP_META_WRITE`。当前只有 orchestrator 拥有该能力；sentinel、investigator 和 recovery 可按各自能力读取元数据或内容，但不能直接改写全局文件状态。

在 `labdemo_ucore` 中：

1. sentinel 查询失败文件；
2. sentinel 读取本次查询产生的 metadata 预取提示；
3. sentinel 发送普通 investigate 消息，内核在 message 入队时交接预取提示，并写入同一 span 的全局提示总线；
4. investigator 查询 align 摘要；
5. investigator 查询依赖；
6. investigator 从自己的 snapshot 读取带 `HANDOFF` 原因位的 analyze 提示，也从 span snapshot 验证 source/target pid，并据此读取 analyze 摘要；
7. recovery 查询报告；
8. 这些工具调用都会进入各自 Agent 的 Context Path。

## 与 Agent Loop 的关系

`agent_file_meta_set()` 更新文件状态时，会检查是否有 Agent 注册了匹配 watch。如果某个 Agent 监听 `status=failed` 或兼容的 state 条件，更新为 failed 的文件会产生 `AGENT_EVENT_FILE_STATUS`，并唤醒等待中的 Agent。事件会携带触发本次状态变化的 cause/span，目标 Agent 消费事件后可以把后续查询和动作提交接到同一因果链。

这正是 `labdemo_ucore` 的启动条件：

1. sentinel 注册 failed 状态监听；
2. orchestrator 把 align 阶段文件更新为 failed；
3. sentinel 从 `agent_wait()` 返回；
4. sentinel 查询失败文件并启动后续分析。

## 依赖关系查询

`dependency_update` 是通用依赖注册工具，payload 使用 `source/target/namespace/run_id/relation/summary` 这组字段。内核只保存对象之间的标签关系，不解释这些标签的业务含义。`dependency_mask` 仍作为紧凑兼容 ABI，表示某个对象 label 会影响哪些后续对象 label；每个 label 通过稳定 hash 映射到一个 bit。用户态写入元数据或调用 `dependency_update` 后，内核会按同一 namespace 和 run_id 维护依赖记录：

| 字段 | 说明 |
| --- | --- |
| `namespace` | 对象所属命名空间，兼容字段为 `project` |
| `run_id` | 本次运行或任务实例 |
| `source` | 产生影响的对象 label |
| `target` | 被影响的对象 label |
| `relation` | 当前为通用 `depends_on` |
| `summary` | 目标对象摘要 |

`dependency_query("label=align;namespace=lab-gene-x;run_id=RUN-042")` 返回：

```text
align+analyze+report+archive
```

查询可以带 namespace 和 run_id。同一个 namespace 下如果存在多个 run，内核只返回所选 run 的依赖影响范围；不带这些字段时则返回该 label 当前可见的合并结果。这个例子来自科研平台用户态初始化数据，不是内核固定规则。换成代码 Agent、运维 Agent 或写作 Agent 时，用户态可以用 `dependency_update` 写入 `parse -> compile`、`alert -> diagnose`、`outline -> draft` 之类的 label 关系，内核按同一套依赖记录查询和生成预取提示。

## 性能验证

`agentbench_ucore` 对比扫描路径和索引路径。tick 数值只作为观测，重点看 `scan_records` 与 `index_records` 的候选记录数差异：

```text
agentbench_ucore: file_query_records scan_records=118 index_records=6
agentbench_ucore: file_query_plan scan_plan=0 index_plan=1 index_reason=68 index_candidates=6
agentbench_ucore: file_query_cache hit=1 reason=68
agentbench_ucore: file_digest bytes=37888 ticks=10 preview=agentbench-digest-content-block-0001 agentbench-digest-content-
agentbench_ucore: file_digest_cache hits=63 misses=1
agentbench_ucore: prefetch_records total=192 first_stage=analyze
agentbench_ucore: file_scan_query ops=64 ticks=13 ops_per_tick=4 speedup_x100=100
agentbench_ucore: file_index_query ops=64 ticks=7 ops_per_tick=9 speedup_x100=185
agentbench_ucore: file_digest_read ops=37888 ticks=10 ops_per_tick=3788 speedup_x100=100
agentbench_ucore: file_prefetch_snapshot ops=192 ticks=1 ops_per_tick=192 speedup_x100=300
```

这表示当前系统同时具备：

- 可观测扫描路径；
- 可观测索引路径；
- 可输出 `used_index`、`scanned_records`、`plan`、`plan_reason`、`candidate_records` 和缓存命中原因；
- 可用受权工具读取真实文件短预览和内容指纹；
- 可用 `agent_info` 观察内容摘要缓存命中和未命中；
- 可输出由历史查询和对象标签依赖生成的 metadata 预取提示；
- 能用候选记录数和多轮 tick 观测解释索引价值。

## 综合场景中的证据

`labdemo_ucore` 中的文件查询证据：

```text
agentos:event type=TOOL_CALL role=sentinel tool=query_file hits=1 used_index=1 seq=4
labdemo_ucore: sentinel prefetch_hint stage=analyze source_seq=4 plan=2 candidates=1
agentos:event type=PREFETCH_USED tick=... role=investigator stage=analyze summary=analysis waits for align seq=...
labdemo_ucore: investigator reason=align output is ready before injected failure
labdemo_ucore: investigator digest bytes=27 preview=align memory_limit evidence seq=4
labdemo_ucore: affected labels=align+analyze+report+archive
labdemo_ucore: final report_query hits=2 used_index=1 scanned=7
```

这些输出说明：

- sentinel 能按属性查询失败文件；
- sentinel 能读取查询历史驱动的预取提示；
- investigator 能通过内核交接的预取提示读取摘要、真实日志 digest、依赖和后续 label 摘要；
- recovery 后能查询报告文件；
- 查询路径使用索引。

`agentsecurity_ucore` 中的文件查询和恢复范围证据：

```text
agentsecurity_ucore: preinit_index_query=1
agentsecurity_ucore: scoped_action=1
agentsecurity_ucore: scoped_artifact=1
```

这些输出说明索引初始化前查询安全，且 recovery 只更新 selector 指定的 run。

`agentfs_ucore` 中的真实文件关联证据：

```text
agentfs_ucore: demo_inode dev=1 inum=14 scanned=2
agentfs_ucore: prefetch_hints=1 count=3 first_stage=analyze source_seq=1
agentfs_ucore: custom_inode dev=1 inum=20 size=7
agentfs_ucore: content_digest=1 size=7 bytes=7 hash=... preview=agentfs
agentfs_ucore: digest_cache=1 hits=1 misses=1
agentfs_ucore: digest_cache_invalidated=1 misses=1
agentfs_ucore: digest_timeline=1 tool=20 preview=agentfs2
agentfs_ucore: .agentmeta_reload=1
agentfs_ucore: query_cache=1 reason=68
agentfs_ucore: bulk_index scan=118 index=6 hits=1
agentfs_ucore: scan_index_consistent=1
agentfs_ucore: truncated_query total=100 returned=3 truncated=1
agentfs_ucore: clear_status=1 cache_invalidated=1
agentfs_ucore: delete_clears_metadata=1
agentfs_ucore: missing_selector_not_found=1
agentfs_ucore: passed
```

`agentscan_ucore` 中的自动扫描证据：

```text
agentscan_ucore: background_scan usershell=1 runs=1 entries=64 added=10
agentscan_ucore: auto_file_create=1 size=14 generation=19
agentscan_ucore: auto_file_delete=1
agentscan_ucore: passed
```

## 当前限制

| 限制项 | 说明 |
| --- | --- |
| 元数据来源 | 当前来自私有 `.agentmeta`、用户态 `agent_file_meta_set()` 和根目录自动扫描；后端为空时内核只安装空表 |
| 文件系统扫描范围 | 当前自动扫描 uCore 根目录短文件名，不做多级目录递归 |
| 持久化索引 | 元数据表可写入并重新加载 `.agentmeta`；内存索引启动后根据元数据重建 |
| 查询语法 | 当前支持结构体字段查询和简单 `key=value` 字符串 |
| 查询规模 | 当前最多 128 条元数据，最多返回 8 条 hit |
| 内容摘要 | 当前读取最多 4096 字节计算指纹，返回短预览，不做全文索引 |
| 预取提示 | 当前只生成 metadata 提示，提示本身不预读文件内容，不保存到磁盘 |

## 后续增强

后续可以把任务四推进为完整文件系统能力：

- 多级目录递归扫描；
- 更丰富的文件分类规则；
- 把更多 Agent 元数据直接纳入 inode 或目录项；
- 支持索引增量持久化；
- 支持更复杂查询条件；
- 支持按时间、大小、版本、生产者 Agent 查询；
- 将预取提示扩展为可配置策略，并与宿主机科研 Agent 平台的运行计划对齐；
- 将文件查询结果直接喂给 LLM Gateway。
