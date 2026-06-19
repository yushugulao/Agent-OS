# 测试记录

测试分支：`uCore`

测试环境：

- WSL2 Ubuntu；
- QEMU riscv64；
- `riscv64-linux-gnu-` 工具链。

## 构建

执行：

```bash
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent
make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore
```

结果：通过。

## 完整脚本

执行：

```bash
bash scripts/run-agent-tests.sh
```

结果：通过。脚本依次运行 `agentfinal_ucore`、`agentfs_ucore`、`agentloop_ucore`、`agentbench_ucore`、`labdemo_ucore`、`agentsecurity_ucore`，均找到对应 `parent passed` 标记，且日志中没有 `check failed`、`panic` 或 `unknown syscall`。

## agentfinal_ucore 样例输出

```text
agentfinal_ucore: Agent-OS on uCore final verification
agentfinal_ucore: context size=20480 capacity=128
agentfinal_ucore: batch first_seq=1 last_seq=64
agentfinal_ucore: short_text_history=1 payload=ucore-final result=ucore-final
agentfinal_ucore: context_detail=1 sequence=8
agentfinal_ucore: tamper_protected=1
agentfinal_ucore: record_flags system=1 manual=1 truncated=0
agentfinal_ucore: fifo oldest=66 latest=193 dropped=65
agentfinal_ucore: file_query hits=2 scanned=2 used_index=1
agentfinal_ucore: event_wait=1 payload=self wake
agentfinal_ucore: passed
agentfinal_ucore: parent passed
```

结论：Agent 创建、Context 映射、batch 调用、短文本历史、完整 detail 查询、篡改保护、手动/系统记录区分、FIFO 淘汰、文件索引和事件等待均通过。

## agentfs_ucore 样例输出

```text
agentfs_ucore: Agent FS metadata test
agentfs_ucore: default_inode dev=1 inum=11 scanned=2
agentfs_ucore: custom_inode dev=1 inum=17 size=7
agentfs_ucore: bulk_index scan=108 index=6 hits=1
agentfs_ucore: clear_status=1
agentfs_ucore: delete_clears_metadata=1
agentfs_ucore: missing_selector_not_found=1
agentfs_ucore: passed
agentfs_ucore: parent passed
```

结论：文件元数据可绑定真实根目录文件 inode，查询结果包含 `dev`、`inum`、`size`；接近 128 条记录时 scan/index 的 `scanned_records` 差异可见；属性清空、文件删除同步和不存在 selector 返回 `NOT_FOUND` 均通过。

## agentloop_ucore 样例输出

```text
agentloop_ucore: Agent event queue test
agentloop_ucore: fifo=1
agentloop_ucore: overflow_dropped=1
agentloop_ucore: unwatch=1
agentloop_ucore: timeout_sleep=1
agentloop_ucore: heartbeat_wake_stop=1
agentloop_ucore: passed
agentloop_ucore: parent passed
```

结论：16 槽 FIFO 事件队列顺序正确，队列满时拒绝新事件且不覆盖旧事件；`agent_unwatch()`、timeout、heartbeat 唤醒和停止均通过。

## agentbench_ucore 样例输出

```text
agentbench_ucore: Agent-OS on uCore benchmark
agentbench_ucore: timeout_heartbeat=1
agentbench_ucore: case ops ticks ops_per_tick speedup_x100
agentbench_ucore: scalar_agent_run ops=256 ticks=5 ops_per_tick=51 speedup_x100=100
agentbench_ucore: batch_agent_run ops=256 ticks=2 ops_per_tick=128 speedup_x100=250
agentbench_ucore: direct_context ops=5000 ticks=1 ops_per_tick=5000 speedup_x100=9765
agentbench_ucore: context_query ops=16 ticks=1 ops_per_tick=16 speedup_x100=100
agentbench_ucore: context_snapshot ops=2048 ticks=2 ops_per_tick=1024 speedup_x100=6400
agentbench_ucore: file_scan_query ops=64 ticks=5 ops_per_tick=12 speedup_x100=100
agentbench_ucore: file_index_query ops=64 ticks=2 ops_per_tick=32 speedup_x100=250
agentbench_ucore: event_wait_wake ops=8 ticks=2 ops_per_tick=4 speedup_x100=100
agentbench_ucore: passed
agentbench_ucore: parent passed
```

结论：batch、direct context 和 snapshot 的性能趋势符合设计预期。tick 数值会随运行环境波动。

## labdemo_ucore 样例输出

```text
labdemo_ucore: Agent-OS laboratory recovery demo
labdemo_ucore: created role=orchestrator pid=2 context=0x0000003ffffe9000
agentos:event type=AGENT_CREATED role=orchestrator pid=2 context=0x0000003ffffe9000
labdemo_ucore: created role=investigator pid=4 context=0x0000003ffffe9000
agentos:event type=AGENT_CREATED role=investigator pid=4 context=0x0000003ffffe9000
labdemo_ucore: created role=sentinel pid=5 context=0x0000003ffffe9000
agentos:event type=AGENT_CREATED role=sentinel pid=5 context=0x0000003ffffe9000
agentos:event type=WATCH_REGISTERED role=sentinel filter=status=failed
labdemo_ucore: created role=recovery pid=3 context=0x0000003ffffe9000
agentos:event type=AGENT_CREATED role=recovery pid=3 context=0x0000003ffffe9000
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
agentsecurity_ucore: Agent permission test
agentsecurity_ucore: mail_basic=1
agentsecurity_ucore: plain_process_denied=1
agentsecurity_ucore: role=orchestrator_child capability_checked=1
agentsecurity_ucore: plain_child_orchestrator=1
agentsecurity_ucore: role=orchestrator capability_checked=1
agentsecurity_ucore: preinit_index_query=1
agentsecurity_ucore: legacy_tool_mismatch=1
agentsecurity_ucore: legacy_param_validation=1 syscall_only=1
agentsecurity_ucore: role=sentinel capability_checked=1
agentsecurity_ucore: sentinel spoof_denied=1
agentsecurity_ucore: role=recovery capability_checked=1
agentsecurity_ucore: recovery rerun_ok=1 duplicate=1
agentsecurity_ucore: scoped_rerun=1
agentsecurity_ucore: scoped_report=1
agentsecurity_ucore: passed
agentsecurity_ucore: parent passed
```

结论：普通进程 mail 最小路径可用；普通进程不能直接投递事件或修改 Agent 文件元数据；pid 1 的普通直接子进程可创建 orchestrator，保证 usershell 手动测试路径可用；初始化前索引查询不会阻塞；legacy `tool_id` 和 `tool_name` 不一致会失败；legacy 参数 key/type 错误会返回 `BAD_PARAM`；syscall-only 工具不能通过 batch 执行；sentinel 不能通过用户态传入 recovery role 伪造恢复权限；recovery 的恢复能力来自内核真实 role/capability，重复 corr_id 被识别为 duplicate，且定向恢复和报告写入不会误修改其他 run。

## ch3_trace 基础兼容抽测

执行：

```bash
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=3
timeout 60s make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=ch3_trace CHAPTER=3
```

样例输出：

```text
string from task trace test
Test trace OK!
```

结论：`SYS_trace=410` 已接入 syscall 分发表，`TRACE_READ`、`TRACE_WRITE` 和 `TRACE_SYSCALL` 可被基础用户程序验证。

## 代码与材料检查

已检查：

- `git diff --check` 通过；
- 仓库内容未包含敏感 token 字符串；
- 仓库内容没有旧版内核关键字；
- 仓库内容没有旧版目录或旧测试入口残留。
