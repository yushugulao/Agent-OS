# 测试记录

记录时间：2026-06-19

测试分支：`uCore`

测试环境：

- WSL2 Ubuntu；
- QEMU riscv64；
- `riscv64-linux-gnu-` 工具链。

## 构建

执行：

```bash
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent
make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore
```

结果：通过。

## 完整脚本

执行：

```bash
bash scripts/run-agent-tests.sh
```

结果：通过。脚本依次运行 `agentfinal_ucore`、`agentbench_ucore`、`labdemo_ucore`、`agentsecurity_ucore`，均找到对应 `parent passed` 标记，且日志中没有 `check failed`、`panic` 或 `unknown syscall`。

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
agentbench_ucore: scalar_agent_run ops=8192 ticks=119 ops_per_tick=68 speedup_x100=100
agentbench_ucore: batch_agent_run ops=8192 ticks=72 ops_per_tick=113 speedup_x100=165
agentbench_ucore: direct_context ops=50000 ticks=1 ops_per_tick=50000 speedup_x100=72631
agentbench_ucore: context_query ops=256 ticks=3 ops_per_tick=85 speedup_x100=100
agentbench_ucore: context_snapshot ops=32768 ticks=22 ops_per_tick=1489 speedup_x100=1745
agentbench_ucore: file_scan_query ops=1024 ticks=28 ops_per_tick=36 speedup_x100=100
agentbench_ucore: file_index_query ops=1024 ticks=23 ops_per_tick=44 speedup_x100=121
agentbench_ucore: event_wait_wake ops=32 ticks=3 ops_per_tick=10 speedup_x100=100
agentbench_ucore: passed
agentbench_ucore: parent passed
```

结论：batch、direct context 和 snapshot 的性能趋势符合设计预期。tick 数值会随运行环境波动。

## labdemo_ucore 样例输出

```text
labdemo_ucore: Agent-OS laboratory recovery demo
labdemo_ucore: created role=orchestrator pid=2 context=0x0000003ffffea000
agentos:event type=AGENT_CREATED role=orchestrator pid=2 context=0x0000003ffffea000
labdemo_ucore: created role=investigator pid=4 context=0x0000003ffffea000
agentos:event type=AGENT_CREATED role=investigator pid=4 context=0x0000003ffffea000
labdemo_ucore: created role=sentinel pid=5 context=0x0000003ffffea000
agentos:event type=AGENT_CREATED role=sentinel pid=5 context=0x0000003ffffea000
agentos:event type=WATCH_REGISTERED role=sentinel filter=status=failed
labdemo_ucore: created role=recovery pid=3 context=0x0000003ffffea000
agentos:event type=AGENT_CREATED role=recovery pid=3 context=0x0000003ffffea000
agentos:event type=INCIDENT_CREATED id=INC-RUN-042-ALIGN-OOM stage=align
labdemo_ucore: sentinel event payload=status=failed;stage=align;run_id=RUN-042;project=lab-gene-x
agentos:event type=TOOL_CALL role=sentinel tool=query_file hits=1 used_index=1 seq=4
agentos:event type=AUDIT role=sentinel action=rerun_stage result=DENIED seq=5
agentos:event type=MESSAGE from=sentinel to=investigator status=OK seq=6
labdemo_ucore: investigator reason=align output is ready before injected failure
labdemo_ucore: affected stages=align+analyze+report+archive
agentos:event type=CONTEXT_SNAPSHOT role=investigator records=4 latest=4
agentos:event type=ACTION role=recovery stage=align status=OK seq=4
agentos:event type=AUDIT role=recovery action=rerun_align result=DUPLICATE seq=5
labdemo_ucore: final report_query hits=2 used_index=1 scanned=7
agentos:event type=FINAL status=RECOVERED
labdemo_ucore: passed
labdemo_ucore: parent passed
```

结论：多 Agent 场景通过，能够展示监控、诊断、恢复和审计过程。

## agentsecurity_ucore 样例输出

```text
agentsecurity_ucore: Agent permission boundary test
agentsecurity_ucore: plain_process_denied=1
agentsecurity_ucore: role=orchestrator_child capability_checked=1
agentsecurity_ucore: plain_child_orchestrator=1
agentsecurity_ucore: role=orchestrator capability_checked=1
agentsecurity_ucore: preinit_index_query=1
agentsecurity_ucore: legacy_tool_mismatch=1
agentsecurity_ucore: role=sentinel capability_checked=1
agentsecurity_ucore: sentinel spoof_denied=1
agentsecurity_ucore: role=recovery capability_checked=1
agentsecurity_ucore: recovery rerun_ok=1 duplicate=1
agentsecurity_ucore: scoped_rerun=1
agentsecurity_ucore: passed
agentsecurity_ucore: parent passed
```

结论：普通进程不能直接投递事件或修改 Agent 文件元数据；pid 1 的普通直接子进程可创建 orchestrator，保证 usershell 手动测试路径可用；初始化前索引查询不会阻塞；legacy `tool_id` 和 `tool_name` 不一致会失败；sentinel 不能通过用户态传入 recovery role 伪造恢复权限；recovery 的恢复能力来自内核真实 role/capability，重复 corr_id 被识别为 duplicate，且定向恢复不会误修改其他 run。

## 提交前检查记录

已检查：

- `git diff --check` 通过；
- 仓库内容未包含敏感 token 字符串；
- 当前 `HEAD` 中没有旧版内核关键字；
- 当前 `HEAD` 中没有旧 `kernel/`、旧 `mkfs/`、旧测试入口残留；
- 本地 `uCore` 分支已推送到 `origin/uCore`。
