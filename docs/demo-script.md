# 最终演示讲解稿

## 1. 开场说明

本项目把 Agent-OS 推进到 uCore 内核上。我们实现的不是普通用户态脚本，而是内核级 Agent 进程、结构化工具调用、上下文历史、文件元数据索引和 Agent 事件循环。

演示会运行三个程序：

```bash
agentfinal_ucore
agentbench_ucore
labdemo_ucore
```

其中 `agentfinal_ucore` 证明功能正确，`agentbench_ucore` 给出性能证据，`labdemo_ucore` 展示完整场景。

## 2. 正确性演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
```

重点解释输出：

- `context size=16384 capacity=128`：每个 Agent 有 4 页 Context，最多保留 128 条可见历史。
- `batch first_seq=1 last_seq=64`：一次 syscall 执行 64 个工具调用，并保证 sequence 连续。
- `short_text_history=1`：Context Path 不只保存数字，也保存短 payload/result 摘要。
- `tamper_protected=1`：用户态直接修改 Context 镜像不能伪造内核权威历史。
- `fifo oldest=65 latest=192 dropped=64`：超过容量后按 FIFO 淘汰，元信息正确。
- `file_query ... used_index=1`：文件元数据查询走索引路径。
- `event_wait=1`：Agent 事件等待和唤醒可用。

最后出现：

```text
agentfinal_ucore: passed
agentfinal_ucore: parent passed
```

## 3. 性能演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
```

重点解释：

- `scalar_agent_run` 是单个工具调用的基线。
- `batch_agent_run` 展示批量 syscall 的吞吐提升。
- `direct_context` 展示用户态直接读 Context 镜像的低成本。
- `context_query` 和 `context_snapshot` 对比逐条查询和批量快照。
- `file_scan_query` 和 `file_index_query` 对比扫描路径和索引路径。
- `event_wait_wake` 证明事件机制不仅能跑通，也被纳入性能验证。

性能数字会随 QEMU 和宿主机负载波动。答辩时应强调相对趋势和设计原因：减少 syscall 次数、减少重复拷贝、减少线性扫描。

最后出现：

```text
agentbench_ucore: passed
agentbench_ucore: parent passed
```

## 4. 场景演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
```

场景设定：

一个实验流水线有多个阶段，包括 align、analyze、report、archive。系统中有多个 Agent：

- sentinel 负责发现失败。
- investigator 负责分析原因。
- recovery 负责恢复。

演示流程：

1. 父进程初始化文件元数据。
2. 三个 Agent 被创建。
3. sentinel 监听 `status=failed`。
4. 父进程注入 align 阶段失败。
5. sentinel 收到事件并查询文件索引。
6. sentinel 尝试恢复但权限不足，被内核拒绝。
7. sentinel 唤醒 investigator。
8. investigator 查询摘要和依赖，确认影响范围。
9. investigator 唤醒 recovery。
10. recovery 通过权限检查并执行恢复。
11. recovery 重复执行同一恢复动作，内核识别为 duplicate。
12. 最终查询报告文件，系统输出 recovered。

关键输出：

```text
agentos:event type=WATCH_REGISTERED role=sentinel filter=status=failed
agentos:event type=INCIDENT_CREATED id=INC-RUN-042-ALIGN-OOM stage=align
agentos:event type=TOOL_CALL role=sentinel tool=query_file ...
agentos:event type=AUDIT role=sentinel action=rerun_stage result=DENIED
agentos:event type=CONTEXT_SNAPSHOT role=investigator ...
agentos:event type=ACTION role=recovery stage=align status=OK
agentos:event type=AUDIT role=recovery action=rerun_align result=DUPLICATE
agentos:event type=FINAL status=RECOVERED
labdemo_ucore: passed
```

## 5. 结尾总结

本项目在 uCore 上实现了 Agent 进程、工具调用、Context Path、文件元数据索引和 Agent Loop。最终场景证明这些功能可以组合成一个完整的内核级 Agent 协作系统，而不是只停留在分散 syscall 的层面。
