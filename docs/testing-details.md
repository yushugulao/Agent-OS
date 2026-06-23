<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 测试内容详细说明

本文补充说明最终验收程序的测试意图、测试步骤、关键断言和证明范围。快速运行命令和验证输出见 [verification.md](verification.md)，输出摘要见 [test-record.md](test-record.md)。

## 总体测试目标

当前最终验收覆盖任务一至五，并保留任务一至三高性能底座复测：

| 任务 | 测试目标 |
| --- | --- |
| 任务一 | 验证 Agent 进程能创建，PCB 字段和 Agent Context 地址空间正确初始化，普通父进程和 Agent 子进程能共存 |
| 任务二 | 验证用户态 Agent 能通过结构化 ABI 批量调用内核工具，工具结果按 sequence 写回 Context |
| 任务三 | 验证 Context Path 能维护多轮短文本摘要历史，支持 snapshot 查询，超出容量后按 FIFO 淘汰，并且用户态直接读取镜像和 syscall snapshot 结果一致 |
| 任务四 | 验证 Agent 可按科研平台字段查询文件元数据，扫描路径和索引路径语义一致，并输出命中统计和性能对比 |
| 任务五 | 验证 Agent 可 watch、wait、heartbeat，被文件状态和 mailbox 事件唤醒，并以轮询查询作为基线 |

最终入口是四个用户态程序：

| 程序 | 定位 |
| --- | --- |
| `labdemo` | 综合场景验收，覆盖任务四、任务五和任务六初步演示主线 |
| `labbench` | 任务四、任务五性能趋势验收 |
| `agentfinal` | 功能正确性验收，失败时打印 `check failed` 并退出非 0 |
| `agentbench` | 性能趋势验收，输出 scalar、batch、direct context、query、snapshot 的吞吐对比 |

## `labdemo` 详细测试内容

源码位置：[../user/labdemo.c](../user/labdemo.c)。

`labdemo` 执行“夜间实验批量复测故障诊断与受控恢复”场景。

测试步骤：

1. 普通父进程通过 `agent_create_role(AGENT_ROLE_ORCHESTRATOR)` 创建 Orchestrator Agent；子进程调用 `agent_set_role(ORCHESTRATOR)` 只是确认当前角色，然后调用 `agent_file_meta_init()` 初始化 `lab-gene-x / RUN-042` 文件元数据。
2. Orchestrator 创建 Recovery、Investigator、Sentinel 三个工作 Agent，普通父进程只负责同步和等待。
3. 每个 Agent 调用 `agent_watch()` 注册自己的关注事件。
4. 父进程等待三个业务 Agent 都完成 watch 注册后，只发送同步信号；Orchestrator 调用 `agent_file_meta_set()` 把 `align` 阶段状态改成 `failed`。
5. 内核向 Sentinel 投递 `AGENT_EVENT_FILE_STATUS`。
6. Sentinel 从 `agent_wait()` 返回，调用 `query_file` 属性查询失败工件。
7. Sentinel 调用 `capability_check` 尝试 `rerun_stage`，预期被拒绝。
8. Sentinel 通过 `send_message` 唤醒 Investigator。
9. Investigator 查询 `read_file_summary` 和 `dependency_query`，得到失败原因摘要和影响范围。
10. Investigator 通过 `send_message` 唤醒 Recovery。
11. Recovery 通过 `capability_check` 后调用 `rerun_stage align`、重复调用同一 `corr_id`、再调用 `rerun_stage report`。
12. 重复 corr_id 返回 `AGENT_STATUS_DUPLICATE`。
13. Recovery 调用 `write_report`，只把 `lab_RUN042_recovery_report` 的内存元数据状态更新为完成，最终限定该物理工件查询并输出 `RECOVERED`。

关键断言：

| 断言 | 目的 |
| --- | --- |
| 三个 Agent 均创建成功 | 证明综合场景不是单进程脚本 |
| Sentinel 输出 `state=WAITING` 后才注入失败 | 证明 watch/wait 路径被真实使用 |
| `agent_wait()` 返回 `AGENT_EVENT_FILE_STATUS` | 证明文件状态变化能唤醒目标 Agent |
| `query_file ... status=failed hits=1` | 证明任务四属性查询能定位失败工件 |
| `used_index=1 scanned=1` | 证明本次失败查询使用索引路径 |
| `affected stages=align+analyze+report+archive` | 证明依赖查询支持最小恢复 |
| `unauthorized rerun by sentinel status=DENIED` | 证明恢复动作有权限限制 |
| `duplicate ... status=DUPLICATE` | 证明幂等拒绝生效 |
| `final status=RECOVERED` | 证明场景完成恢复 |
| `final report_query hits=1` | 证明最终报告查询只命中恢复报告工件 |

## `labbench` 详细测试内容

源码位置：[../user/labbench.c](../user/labbench.c)。

`labbench` 关注相对趋势和可复现输出，不设置固定 tick 阈值。

测试项：

| case | 测试方法 | 证明点 |
| --- | --- | --- |
| `file_scan_query` | 强制扫描 112 条默认元数据记录，查询 `RUN-042 stage=align` | 扫描路径正确，且默认表保留空槽支持插入新工件 |
| `file_index_query` | 使用 stage 索引查询同一条件 | 索引路径语义一致且扫描候选更少 |
| `file_semantics` | 对比扫描/索引命中数，验证空结果、截断、`run_id+kind` 选择性查询 | 索引语义完整，宽桶查询可被更小索引桶优化 |
| `fid_query` | 使用 `fid=4` 查询失败工件 | 短事件 payload 可回查完整元数据 |
| `busy_poll_query` | 反复查询文件状态作为轮询基线 | 展示无事件机制时的查询式等待 |
| `event_wait_wake` | 一个 Agent 等待 512 次消息事件，Orchestrator Agent 逐次 wake 并等待 ack | Agent Loop wait/wake 稳定 |
| `event_fifo` | Orchestrator Agent 在目标 Agent 未消费前连续投递 8 个事件，再投递第 9 个 | FIFO 顺序正确，满队列返回 `NO_SPACE`，dropped 计数可观察 |
| `send_message_overflow` | `send_message` 连续投递到满队列 | 第 9 条返回 `NO_SPACE`，mailbox 回滚保留上一条成功消息 |
| `file_status_partial_payload` | 只传 `fid/status` 更新已有文件元数据 | 事件 payload 输出短摘要，按 `stage=align` watcher 可命中，并可通过 `fid=4` 回查完整记录 |
| `file_status_overflow` | 文件状态变化连续投递到满队列 | 第 9 条返回 `NO_SPACE`，目标 dropped 计数可观察 |
| `permission_denied` | 普通进程尝试直接创建高权限工作 Agent；默认 Sentinel 尝试自升为 Orchestrator，再调用 wake、meta_set、rerun、write_report | 高权限创建和自升权被拒绝，capability 权限限制有效 |
| `heartbeat_stop` / `unwatch` | 注册 TIMER watch 后停止 heartbeat；注册后删除 watch 再投递事件 | 停止 heartbeat 后不再投递 timer，删除 watch 后不再接收匹配事件 |
| `agent_run_bad_flags` | 调用 `agent_run(..., flags=1)` | 非 0 flags 返回 `AGENT_STATUS_BAD_PARAM`，不执行工具 |
| `scalar_tool_call` | 8192 次单 op `agent_run` | 批量工具调用对照组 |
| `batch_agent_run` | 8192 个 op 按 64 个一批执行 | syscall 合并带来吞吐优势 |
| `context_query` | 512 次逐条查询 | Context 查询对照组 |
| `context_snapshot` | 512 次批量 snapshot | 批量导出更适合报告和大屏 |
| `capability_check` | 32768 次恢复权限检查 | 权限检查成本可量化 |
| `duplicate_reject` | 同一 stage/action/request_id 执行两次恢复动作 | 幂等表拒绝重复副作用；不同 stage 可复用同一 request_id |

元数据语义额外断言：

| 断言 | 证明点 |
| --- | --- |
| `metadata_dependency=1` | `dependency_query` 优先读取 selector 指定运行中的 `dependency_mask` |
| `scoped_rerun=1` | `rerun_stage` 按 dependency mask 更新 selector 指定运行内的对应阶段 |
| `scoped_report=1` | `write_report` 只更新 selector 指定运行内的恢复报告，不影响其他 run |
| `single_report=1` | `write_report` 只更新 `lab_RUN042_recovery_report` |
| `mask_text=1` | 任意合法 dependency bitmask 按 bit 拼接文本，例如 `align+report` |
| `dep_clear=1` | `update_mask=AGENT_FILE_META_UPDATE_DEPS` 可把 `dependency_mask` 显式清零 |
| `insert=1` | 默认表预留空槽后，新 artifact 可插入并查询到 |
| `delete=1` | `AGENT_FILE_META_DELETE` 可删除指定元数据并重建索引 |

## `agentfinal` 详细测试内容

源码位置：[../user/agentfinal.c](../user/agentfinal.c)。

### 1. Agent 创建和父子进程关系

测试步骤：

1. 普通进程调用 `agent_create()`。
2. 父进程等待子进程退出。
3. 子进程作为 Agent 执行后续检查。
4. 父进程检查子进程退出状态为 0。

关键断言：

| 断言 | 目的 |
| --- | --- |
| `agent_create()` 返回值大于等于 0 | 证明 Agent 创建系统调用成功 |
| 父进程 `wait()` 得到状态 0 | 证明 Agent 子进程完整跑完测试且未被异常杀死 |

对应赛题要求：Agent 进程能够成功创建，普通进程和 Agent 进程可以共存。

### 2. Agent 身份和 Context 映射

测试步骤：

1. Agent 子进程调用 `agent_info()`。
2. 检查 `is_agent`、`context_size` 等元信息。
3. 直接把 `info.context_base` 转换为 `struct agent_context_header *`。
4. 从用户态直接读取 Context header 和 latest result 区。

关键断言：

| 断言 | 目的 |
| --- | --- |
| `agent_info(&info) == 0` | Agent 信息接口可用 |
| `info.is_agent == 1` | 当前子进程已经被标记为 Agent |
| `info.context_size == AGENT_CONTEXT_SIZE` | Context 大小与内核 ABI 一致，当前为 16384 字节 |
| `direct_header->magic == AGENT_CONTEXT_MAGIC` | Context header 已正确初始化 |
| `direct_header->capacity == AGENT_CONTEXT_MAX_RECORDS` | Context Path 容量正确，当前为 128 条 |

对应赛题要求：PCB 扩展字段正确初始化，Agent Context 区在用户地址空间中正确分配，Agent 可直接访问 Context。

### 3. 第一批 64 个工具调用

测试步骤：

1. 构造 64 个 `struct agent_op`。
2. 工具按 `i % 4` 分布为 `echo`、`pid_info`、`ctx_stat`、`read_context`。
3. 调用一次 `agent_run(ops, results, 64, 0)`。
4. 检查每条 result 的状态和 sequence。
5. 直接读取 Context latest result，确认最近结果序号为 64。

关键断言：

| 断言 | 目的 |
| --- | --- |
| `agent_run(...) == AGENT_BATCH_MAX` | 批量 syscall 成功执行 64 个 op |
| `results[i].status == AGENT_STATUS_OK` | 每个工具调用都成功 |
| `results[i].sequence == first_sequence + i` | 批量调用内部 sequence 连续递增 |
| `latest->sequence == AGENT_BATCH_MAX` | 最新结果已写入 Agent Context |

对应赛题要求：用户态 Agent 能调用至少 3 个内核工具，请求和响应为结构化格式，工具调用结果能写入 Agent Context。当前一次批量调用覆盖 4 类工具。

### 4. 第一次 Context Snapshot

测试步骤：

1. 调用 `context_snapshot(&header, snapshot_records, 128)`。
2. 检查返回记录数为 64。
3. 检查 header 中最新 sequence 为 64。
4. 检查 snapshot 第一条和最后一条记录分别为 1 和 64。
5. 检查第一条 echo 记录中的 16 字节 payload/result 摘要。
6. 用 `check_snapshot_matches_direct()` 将 snapshot records 与用户态直接读取的 Context 镜像 records 对比。

关键断言：

| 断言 | 目的 |
| --- | --- |
| `n == AGENT_BATCH_MAX` | snapshot 返回当前可见的 64 条历史 |
| `header.latest_sequence == AGENT_BATCH_MAX` | header 元信息与执行次数一致 |
| `snapshot_records[0].sequence == 1` | 最早记录正确 |
| `snapshot_records[n - 1].sequence == 64` | 最新记录正确 |
| `snapshot_records[0].payload/result == "final"` | 证明 Context Path 保存短文本历史摘要 |
| direct record 的 `sequence/tool_id/status/payload/result` 与 snapshot 匹配 | 证明首次同步后 syscall snapshot 和用户镜像视图一致 |

对应赛题要求：系统能维护工具调用路径，Agent 可从 Context 区读取路径数据。

### 5. 用户镜像篡改防护

测试步骤：

1. 用户态直接修改 Context 镜像中第一条 record 的 `sequence/tool_id/status/payload/result`。
2. 在调用 snapshot 前直接读取镜像，确认脏数据确实对 direct read 可见。
3. 再次调用 `context_snapshot()`。
4. 检查 snapshot 返回的 record 仍是内核 shadow 中的原始值。
5. 检查 direct Context 镜像已被刷新回原始值。

关键断言：

| 断言 | 目的 |
| --- | --- |
| `snapshot_records[0].sequence == sequence` | 用户态篡改镜像不影响内核权威 sequence |
| `snapshot_records[0].tool_id == tool_id` | 用户态篡改镜像不影响内核权威工具 ID |
| `snapshot_records[0].status == status` | 用户态篡改镜像不影响内核权威状态 |
| snapshot 前 direct record 可见 `dirty` 文本 | 说明 direct Context 是高速镜像，不是可信数据来源 |
| snapshot 后 payload/result 恢复为 `final` | 说明 snapshot 会刷新镜像并返回 shadow 权威短文本摘要 |
| direct record 被恢复 | `context_snapshot()` 会将 shadow 权威历史重新同步到用户镜像 |

验证目的：证明当前 Context Path 的可信状态保存在内核 shadow 中，用户态 Context 只是可直接读取的镜像。

### 6. FIFO 淘汰和 128 条容量验证

测试步骤：

1. 再执行两批 `agent_run()`，每批 64 个 op。
2. 总工具调用数达到 192。
3. 调用 `context_snapshot()` 查询当前可见记录。
4. 检查 Context Path 容量为 128 条。
5. 检查最早可见 sequence 为 65，最新 sequence 为 192，淘汰条数为 64。
6. 再次对比 snapshot records 与用户态直接读取的 Context records。

关键断言：

| 断言 | 目的 |
| --- | --- |
| `n == AGENT_CONTEXT_MAX_RECORDS` | 可见记录达到容量上限 128 |
| `header.oldest_sequence == 65` | 前 64 条已被 FIFO 淘汰 |
| `header.latest_sequence == 192` | 最新记录序号正确 |
| `header.dropped_records == 64` | 淘汰计数正确 |
| `snapshot_records[0].sequence == 65` | 当前最早可见记录正确 |
| `snapshot_records[n - 1].sequence == 192` | 当前最新可见记录正确 |
| snapshot 和 direct context 匹配 | 证明淘汰后内核 shadow 和用户镜像视图仍一致 |

对应赛题要求：连续 5 轮以上调用能维护路径，路径超长时不会导致内核 OOM，而是按固定容量淘汰旧记录。当前验证规模为 192 次调用，远超过 5 轮要求。

## `agentbench` 详细测试内容

源码位置：[../user/agentbench.c](../user/agentbench.c)。

`agentbench` 的目标不是证明绝对时间，而是证明优化方向有效：减少 syscall 次数、减少 Context 复制、用 snapshot 批量读取历史。

### 1. Scalar Run 基线

测试步骤：

1. 构造 1 个 `echo` op。
2. 循环 65536 次调用 `agent_run(&op, &res, 1, 0)`。
3. 每次检查返回值和 result 状态。
4. 用 `uptime()` 统计 xv6 tick。

意义：这是批量调用的对照组。虽然仍使用新 ABI，但每次 syscall 只执行 1 个 op，因此可以反映频繁 syscall 的成本。

### 2. Batch Run

测试步骤：

1. 每轮构造 64 个 `echo` op。
2. 循环 1024 轮，总 op 数仍为 65536。
3. 每轮调用一次 `agent_run(batch_ops, batch_results, 64, 0)`。
4. 检查每批第一条和最后一条 sequence 连续。

意义：与 scalar run 执行相同数量的工具操作，但 syscall 次数从 65536 次减少到 1024 次。一次样例记录中 batch run 为 2 ticks，scalar run 为 16 ticks；具体 tick 会波动，但批量 ABI 对端到端吞吐仍明显有效。

### 3. Direct Context Read

测试步骤：

1. 调用 `agent_info()` 获取 `context_base`。
2. 用户态直接读取 Context header 中的 `total_calls`、`latest_sequence`、`dropped_records`。
3. 循环 1000000 次。
4. 用 `uptime()` 统计 tick。

意义：验证 Context 用户镜像读路径不需要 syscall，也不需要内核复制。样例记录中 1000000 次直接读低于 1 tick，说明该路径开销很低。由于 xv6 tick 粒度粗，文档只把它作为低开销证据，不作为零成本证明。

### 4. Context Query

测试步骤：

1. 循环 2048 次调用 `context_query(0, &record, 1)`。
2. 每次只查询 1 条可见记录。
3. 检查每次返回 1。

意义：这是逐条查询 Context Path 的对照组，用于和 snapshot 批量查询比较。

### 5. Context Snapshot

测试步骤：

1. 循环 2048 次调用 `context_snapshot(&header, snapshot_records, 128)`。
2. 每次返回完整 128 条可见记录。
3. 检查返回记录数为 128。
4. 统计总返回记录数 262144。

意义：与 `context_query` 相比，snapshot 每次 syscall 返回一批 records。引入内核 shadow 后 snapshot 会先刷新用户镜像，具体 tick 会波动；验证重点是它一次 syscall 批量返回 128 条记录并保持 `agentbench: passed`。

### 6. 性能表字段解释

| 字段 | 含义 |
| --- | --- |
| `case` | 测试场景名称 |
| `ops` | 当前场景统计的操作数或返回记录数 |
| `ticks` | xv6 `uptime()` 差值 |
| `ops_per_tick` | 每 tick 完成的操作数；若 ticks 为 0，按 1 tick 保守估算 |
| `speedup_x100` | 相对对照组的放大 100 倍速度比，例如 1600 表示约 16.00 倍；内部用 64 位计算，避免高吞吐场景溢出 |

性能结果受 QEMU、宿主机负载和 xv6 tick 粒度影响。评审时应关注相对趋势：batch 优于 scalar，direct context 低于 syscall 查询，snapshot 优于逐条 query。

## 辅助复测内容

除最终入口外，当前仍保留 `agentcall`、`contexttest`、`agentstress` 和 `agentexec` 作为复测程序。

| 程序 | 当前重点断言 |
| --- | --- |
| `agentcall` | legacy `tool_call()` 的工具名/ID 解析、参数键名错误、参数类型错误、64 字节 payload 不截断、Agent 坏输出指针无副作用、合法 lazy 输出缓冲 prefault、多 Agent mailbox 和历史 wrap |
| `contexttest` | `context_push/query/rollback/clear`、128 条容量 FIFO、短文本 payload/result 历史、rollback 空历史/已淘汰/未来 sequence 返回 `AGENT_STATUS_NOT_FOUND` |
| `agentstress` | Agent 多次 exec、exec 失败后 Context 指针保持有效、连续创建退出、`sbrk` 不越过 Agent Context、普通进程未映射 Agent Context 特殊页、父进程堆越过 Context 后拒绝创建 Agent |
| `agentexec` | 既可作为 Agent exec 目标，也可从 shell 直跑 wrapper 路径 |

其中 `agentcall` 的坏输出指针测试会先发送一条正常 mailbox 消息，再用坏响应指针尝试发送覆盖消息。通过条件是发送者的 `agent_call_count` 和 `context_path_count` 不变，接收者最终仍读到正常消息 `hello-agent`，从而证明预检失败时没有执行工具副作用。

`agentcall` 还会用 `sbrklazy()` 分配但不预先触碰 response/result 输出页，分别验证 legacy `tool_call()` 和批量 `agent_run()` 能通过 writable-prefault 接受合法 lazy 输出缓冲。对应输出为 `agent lazy_output: legacy=1 batch=1`。

`agentstress` 的父进程越界测试会让普通进程先把堆扩展到 `AGENT_CONTEXT_BASE` 以上，再分别在未触碰页和已触碰页两种情况下调用 `agent_create()`。通过条件是两次创建均失败且系统无 panic，对应输出为 `agentstress: parent_over_context_rejected=1`。

## 与赛题要求的对应关系

| 测试内容 | 覆盖的赛题点 |
| --- | --- |
| `agent_create()`、`agent_info()`、Context header 检查 | 任务一 Agent 进程创建、PCB 字段、地址空间设计 |
| 64 个 `agent_op` 批量执行 | 任务二结构化工具调用、至少 3 个工具、响应写回 |
| sequence 连续性检查 | 任务三多轮调用路径维护 |
| direct context 与 snapshot 对比 | 任务三 Agent 从 Context 镜像读取路径数据 |
| short_text_history 检查 | 证明 Context Path 保存 128 条短文本摘要路径，而不是只保存 latest |
| tamper 防护测试 | 证明 Context Path 权威历史保存在内核 shadow 中，并说明 direct Context 只是高速镜像 |
| 192 次 op 和 FIFO 元信息检查 | 任务三路径超长自动淘汰、不 OOM |
| legacy 参数和坏指针复测 | 证明兼容 API 的错误语义和无副作用要求 |
| rollback not found 复测 | 证明历史节点不存在可通过状态码区分 |
| rollback 后继续 push | 证明裁剪历史不会复用旧 sequence，`branch_latest=131` |
| lazy 输出缓冲复测 | 证明合法 lazy `sbrk` 输出页不会被预检误杀，非法坏指针仍无副作用 |
| 父进程越界创建复测 | 证明普通父进程堆越过 Agent Context 后不能直接创建重叠 Agent |
| scalar/batch/query/snapshot 性能表 | 创新增强：高性能批量执行和批量 Context 查询 |

## 当前测试覆盖范围和限制

| 测试限制 | 说明 |
| --- | --- |
| 任务六仍是初步综合演示 | `labdemo` 已串联任务一至五，但最终 LLM Gateway 和可视化大屏尚未接入 |
| tick 粒度较粗 | 当前性能数据适合证明趋势，不适合精确微基准结论 |
| 事件队列容量有限 | 当前为每 Agent 8 槽 FIFO，测试覆盖投递、等待、唤醒、FIFO 顺序、满队列拒绝和 dropped 统计；最终可扩展容量和优先级 |
| 文件查询是内核元数据表 | 当前不改 xv6 inode 主路径；最终可迁移为 inode 扩展或持久化索引 |
| 不设固定性能阈值 | 避免 QEMU 波动导致误判，以输出吞吐表和相对差异作为评审证据 |
