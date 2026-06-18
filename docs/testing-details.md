# 测试内容详细说明

本文档解释当前 uCore 分支最终测试程序的内部步骤、覆盖范围和预期输出。测试入口和运行命令见 [verification.md](verification.md)。

## 1. `agentfinal_ucore`

`agentfinal_ucore` 是最终正确性测试，重点覆盖任务一、任务二、任务三，同时检查任务四文件索引和任务五事件自唤醒是否可用。

### 1.1 测试流程

1. 父进程打印 `Agent-OS on uCore final verification`。
2. 父进程调用 `agent_create()` 创建 Agent 子进程。
3. 子进程调用 `agent_info()`。
4. 子进程检查：
   - `info.is_agent == 1`；
   - `info.context_base == AGENT_CONTEXT_BASE`；
   - `info.context_size == AGENT_CONTEXT_SIZE`。
5. 子进程把 `info.context_base` 转成 `struct agent_context_header *`，直接读取 Context header。
6. 子进程检查 header magic，并输出 Context 大小和容量。
7. 子进程调用 `context_clear()`，保证后续 sequence 从干净状态开始。
8. 子进程构造 64 个 echo 操作，使用一次 `agent_run()` 批量执行。
9. 子进程检查：
   - batch 返回值等于 64；
   - 第一条 result 的 sequence 为 1；
   - 最后一条 result 的 sequence 为 64；
   - 直接读取 latest result 的 sequence 为 64。
10. 子进程调用 `context_snapshot()`，检查返回 64 条有序记录。
11. 子进程检查第 8 条记录的 payload/result 短文本为 `ucore-final`。
12. 子进程手动篡改用户态 Context 镜像中的第一条记录 sequence。
13. 子进程再次调用 `context_snapshot()`。
14. 子进程检查 snapshot 返回的第一条记录仍为原始 sequence，并检查用户镜像被刷新。
15. 子进程继续批量写入 128 条记录，使总调用达到 192 条。
16. 子进程再次 snapshot，检查 FIFO 淘汰：
   - count 为 128；
   - oldest 为 65；
   - latest 为 192；
   - dropped 为 64。
17. 子进程调用 `agent_file_meta_init()` 初始化文件元数据。
18. 子进程按 `project=lab-gene-x`、`run_id=RUN-042`、`stage=align` 查询文件。
19. 子进程检查查询命中，且 `used_index == 1`。
20. 子进程注册 message watch。
21. 子进程用 `agent_wake()` 向自己投递事件。
22. 子进程调用 `agent_wait()`，检查成功收到 `self wake`。
23. 子进程输出 `agentfinal_ucore: passed` 并退出。
24. 父进程等待子进程退出，检查退出状态为 0，输出 `agentfinal_ucore: parent passed`。

### 1.2 关键输出

```text
agentfinal_ucore: context size=16384 capacity=128
agentfinal_ucore: batch first_seq=1 last_seq=64
agentfinal_ucore: short_text_history=1 payload=ucore-final result=ucore-final
agentfinal_ucore: tamper_protected=1
agentfinal_ucore: fifo oldest=65 latest=192 dropped=64
agentfinal_ucore: file_query hits=2 scanned=2 used_index=1
agentfinal_ucore: event_wait=1 payload=self wake
agentfinal_ucore: passed
agentfinal_ucore: parent passed
```

### 1.3 覆盖结论

| 覆盖点 | 证明方式 |
| --- | --- |
| Agent 创建 | 父进程创建 Agent 子进程 |
| Agent PCB 字段 | `agent_info()` 返回 Agent 状态和 Context 信息 |
| Agent Context 映射 | 直接读取 header 和 latest result |
| 批量工具调用 | 一次 `agent_run()` 执行 64 个 echo op |
| Context Path 写入 | snapshot 返回 64 条记录 |
| 短文本历史 | payload/result 短摘要可查询 |
| shadow 防篡改 | 用户镜像被写坏后，snapshot 仍返回权威历史 |
| FIFO 淘汰 | 192 条调用后只保留 128 条，oldest/latest/dropped 正确 |
| 文件索引 | `agent_file_query()` 使用索引路径 |
| Agent 事件 | watch/wake/wait 自唤醒成功 |

## 2. `agentbench_ucore`

`agentbench_ucore` 是性能和吞吐测试。它不使用固定耗时阈值，而是输出可对比的 tick 统计。

### 2.1 测试项目

| 项目 | 操作数 | 测试内容 |
| --- | ---: | --- |
| `scalar_agent_run` | 8192 | 每次 syscall 执行 1 个 echo op |
| `batch_agent_run` | 8192 | 每次 syscall 执行 64 个 echo op |
| `direct_context` | 50000 | 用户态直接读取 Context header 的 latest sequence |
| `context_query` | 256 | 每次 syscall 查询 1 条 Context record |
| `context_snapshot` | 32768 | 多次 snapshot，每次最多返回 128 条记录 |
| `file_scan_query` | 1024 | 强制扫描文件元数据表 |
| `file_index_query` | 1024 | 使用文件元数据索引路径 |
| `event_wait_wake` | 32 | 父进程多次唤醒等待中的 Agent 子进程 |

### 2.2 输出字段

| 字段 | 含义 |
| --- | --- |
| `ops` | 执行的逻辑操作数量 |
| `ticks` | 消耗的内核 tick 数；最小按 1 处理，避免除 0 |
| `ops_per_tick` | 每 tick 完成的操作数 |
| `speedup_x100` | 相对基线放大 100 倍后的速度比 |

### 2.3 当前样例输出

```text
agentbench_ucore: case ops ticks ops_per_tick speedup_x100
agentbench_ucore: scalar_agent_run ops=8192 ticks=126 ops_per_tick=65 speedup_x100=100
agentbench_ucore: batch_agent_run ops=8192 ticks=67 ops_per_tick=122 speedup_x100=188
agentbench_ucore: direct_context ops=50000 ticks=1 ops_per_tick=50000 speedup_x100=76904
agentbench_ucore: context_query ops=256 ticks=2 ops_per_tick=128 speedup_x100=100
agentbench_ucore: context_snapshot ops=32768 ticks=23 ops_per_tick=1424 speedup_x100=1113
agentbench_ucore: file_scan_query ops=1024 ticks=27 ops_per_tick=37 speedup_x100=100
agentbench_ucore: file_index_query ops=1024 ticks=22 ops_per_tick=46 speedup_x100=122
agentbench_ucore: event_wait_wake ops=32 ticks=4 ops_per_tick=8 speedup_x100=100
agentbench_ucore: passed
agentbench_ucore: parent passed
```

### 2.4 性能解释

| 对比 | 设计含义 |
| --- | --- |
| `batch_agent_run` vs `scalar_agent_run` | 批量 syscall 减少陷入内核次数 |
| `direct_context` vs syscall 查询 | 用户态镜像适合高频读最新状态 |
| `context_snapshot` vs `context_query` | 批量历史查询减少多次 syscall 和多次遍历 |
| `file_index_query` vs `file_scan_query` | 文件元数据索引减少候选记录检查 |
| `event_wait_wake` | Agent Loop 不只是功能演示，也能被计时验证 |

tick 数值随环境波动，评审时应结合设计解释看相对趋势。

## 3. `labdemo_ucore`

`labdemo_ucore` 是面向答辩的最终场景测试。它把底层能力串成一个可解释的多 Agent 工作流。

### 3.1 场景设定

实验流水线包含多个阶段：

- prepare
- align
- analyze
- report
- archive

系统中创建三个 Agent：

| 角色 | 职责 |
| --- | --- |
| sentinel | 监听失败事件，发现异常 |
| investigator | 查询失败原因和影响范围 |
| recovery | 执行受控恢复动作并验证结果 |

### 3.2 流程

1. 父进程调用 `agent_file_meta_init()` 安装演示文件元数据。
2. 父进程创建 recovery、investigator、sentinel 三个 Agent。
3. 三个 Agent 分别输出自己的 role、pid 和 Context 地址。
4. sentinel 注册 `status=failed` 的文件状态监听。
5. investigator 注册 `investigate` 消息监听。
6. recovery 注册 `recover` 消息监听。
7. 父进程更新 align 阶段文件元数据，把状态改为 failed。
8. 内核投递 `AGENT_EVENT_FILE_STATUS`。
9. sentinel 调用 `agent_wait()` 收到失败事件。
10. sentinel 通过 `query_file` 找到失败工件。
11. sentinel 尝试执行恢复动作，`capability_check` 返回 denied。
12. sentinel 通过消息唤醒 investigator。
13. investigator 查询 align 文件摘要，得到故障原因。
14. investigator 查询 dependency，得到影响阶段。
15. investigator 调用 `context_snapshot()` 展示自身审计历史。
16. investigator 通过消息唤醒 recovery。
17. recovery 通过 capability 检查。
18. recovery 执行 `rerun_stage align`。
19. recovery 再次执行同一动作，内核返回 duplicate。
20. recovery 写报告并查询 report 文件。
21. 父进程等待三个 Agent 退出，输出 `labdemo_ucore: passed`。

### 3.3 关键输出

```text
agentos:event type=WATCH_REGISTERED role=sentinel filter=status=failed
agentos:event type=INCIDENT_CREATED id=INC-RUN-042-ALIGN-OOM stage=align
agentos:event type=TOOL_CALL role=sentinel tool=query_file hits=1 used_index=1
agentos:event type=AUDIT role=sentinel action=rerun_stage result=DENIED
agentos:event type=MESSAGE from=sentinel to=investigator status=OK
labdemo_ucore: investigator reason=align output is ready before injected failure
labdemo_ucore: affected stages=align+analyze+report+archive
agentos:event type=CONTEXT_SNAPSHOT role=investigator records=4
agentos:event type=ACTION role=recovery stage=align status=OK
agentos:event type=AUDIT role=recovery action=rerun_align result=DUPLICATE
agentos:event type=FINAL status=RECOVERED
labdemo_ucore: passed
```

### 3.4 覆盖结论

| 覆盖点 | 证明方式 |
| --- | --- |
| 多 Agent 并存 | 同时创建 sentinel、investigator、recovery |
| 文件状态事件 | 父进程注入 failed 状态后唤醒 sentinel |
| 文件属性查询 | sentinel 查询失败工件 |
| 依赖查询 | investigator 查询 align 的影响范围 |
| 权限控制 | sentinel 恢复动作被拒绝 |
| Agent 间通信 | sentinel 唤醒 investigator，investigator 唤醒 recovery |
| Context 审计 | investigator 输出 snapshot |
| 幂等恢复 | recovery 第二次 rerun 返回 duplicate |
| 最终状态 | 输出 `FINAL status=RECOVERED` |

## 4. 运行方式和复现建议

推荐用脚本运行完整测试：

```bash
bash scripts/run-agent-tests.sh
```

如果需要单独复现某一项：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
```

不要在同一工作树中并行启动多个 QEMU 测试，因为 `nfs/fs-copy.img` 会被多个进程同时访问，可能造成镜像锁冲突。
