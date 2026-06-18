# 测试记录

记录时间：2026-06-19

测试分支：`uCore`

测试环境：

- WSL2 Ubuntu
- QEMU riscv64
- `riscv64-linux-gnu-` 工具链

## 构建

执行：

```bash
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent
make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore
```

结果：通过。

## agentfinal_ucore 样例输出

```text
agentfinal_ucore: Agent-OS on uCore final verification
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

结论：Agent 创建、Context 映射、batch 调用、短文本历史、篡改保护、FIFO 淘汰、文件索引和事件等待均通过。

## agentbench_ucore 样例输出

```text
agentbench_ucore: Agent-OS on uCore benchmark
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

结论：batch、direct context 和 snapshot 的性能趋势符合设计预期。tick 数值会随运行环境波动。

## labdemo_ucore 样例输出

```text
labdemo_ucore: Agent-OS laboratory recovery demo
agentos:event type=AGENT_CREATED role=recovery ...
agentos:event type=AGENT_CREATED role=investigator ...
agentos:event type=AGENT_CREATED role=sentinel ...
agentos:event type=WATCH_REGISTERED role=sentinel filter=status=failed
agentos:event type=INCIDENT_CREATED id=INC-RUN-042-ALIGN-OOM stage=align
agentos:event type=TOOL_CALL role=sentinel tool=query_file hits=1 used_index=1 ...
agentos:event type=AUDIT role=sentinel action=rerun_stage result=DENIED ...
agentos:event type=MESSAGE from=sentinel to=investigator status=OK ...
agentos:event type=CONTEXT_SNAPSHOT role=investigator records=4 ...
agentos:event type=ACTION role=recovery stage=align status=OK ...
agentos:event type=AUDIT role=recovery action=rerun_align result=DUPLICATE ...
agentos:event type=FINAL status=RECOVERED
labdemo_ucore: passed
```

结论：多 Agent 场景通过，能够展示监控、诊断、恢复和审计过程。
