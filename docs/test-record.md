# 测试记录

本文只记录当前可复现的测试输出。评审结论、测试覆盖情况和性能数据归纳见 [verification.md](verification.md)。

## 2026-06-14：当前任务一至三验证记录

| 项目 | 内容 |
| --- | --- |
| 分支 | `Wang` |
| 环境 | WSL2 Ubuntu 26.04 |
| 构建命令 | `make fs.img`、`make kernel/kernel`、`make user/_agentcall user/_contexttest user/_agentstress user/_agentexec user/_agentfinal user/_agentbench` |
| 运行命令 | `make qemu` 后依次执行 `agentfinal`、`agentcall`、`contexttest`、`agentstress`、`agentbench`、`agentexec` |
| 结果 | 通过 |

关键输出：

```text
$ agentfinal
agentfinal: context size=16384 capacity=128
agentfinal: batch first_seq=1 last_seq=64
agentfinal: short_text_history=1 payload=final result=final
agentfinal: snapshot count=64 latest=64
agentfinal: direct_dirty_before_snapshot=1
agentfinal: tamper_protected=1
agentfinal: fifo oldest=65 latest=192 dropped=64
agentfinal: direct_context_match=1
agentfinal: passed
$ agentcall
tool read_context: status=0 seq=8 count=8 head=8 calls=8
agent bad_output: no_side_effect calls=15 context=15
agent lazy_output: legacy=1 batch=1
history: count=128 head=12 total=140 capacity=128
agentcall: strict validation passed
$ contexttest
contexttest: fifo oldest=3 latest=130 dropped=2
contexttest: short_text_history=1 payload=manual-in result=manual-out
contexttest: rollback_not_found=-5
contexttest: rollback latest=10 branch_latest=11
contexttest: passed
$ agentstress
agentstress: exec_failure_preserved=1
agentstress: create_exit=12
agentstress: sbrk_boundary_steps=255
agentstress: normal_context_fault=status -1
agentstress: parent_over_context_rejected=1
agentstress: passed
$ agentbench
agentbench: scalar_run ops=65536 ticks=16 ops_per_tick=4096 speedup_x100=100
agentbench: batch_run ops=65536 ticks=2 ops_per_tick=32768 speedup_x100=800
agentbench: direct_context ops=1000000 ticks=0 ops_per_tick=1000000 speedup_x100=24414
agentbench: context_query ops=2048 ticks=0 ops_per_tick=2048 speedup_x100=50
agentbench: context_snapshot ops=262144 ticks=3 ops_per_tick=87381 speedup_x100=4266
agentbench: passed
$ agentexec
agentexec: wrapper status=0
```

验证点：

| 验证项 | 结果 |
| --- | --- |
| Agent Context 当前布局 | `agentfinal: context size=16384 capacity=128` |
| 批量工具调用 sequence 连续 | `agentfinal: batch first_seq=1 last_seq=64` |
| Context Path 保存短文本 payload/result 摘要 | `agentfinal: short_text_history=1`、`contexttest: short_text_history=1` |
| 直接 Context 读是镜像，snapshot 刷新可信历史 | `agentfinal: direct_dirty_before_snapshot=1`、`agentfinal: tamper_protected=1` |
| FIFO 淘汰元信息正确 | `agentfinal: fifo oldest=65 latest=192 dropped=64` |
| `read_context` 返回本次追加后的 post-state | `count=8 head=8 calls=8` 与 `seq=8` 对齐 |
| 合法 lazy 输出页可通过 writable-prefault 使用 | `agent lazy_output: legacy=1 batch=1` |
| 父进程堆越过 Agent Context 后创建 Agent 被拒绝 | `agentstress: parent_over_context_rejected=1` |
| 性能测试输出吞吐对比 | `agentbench: passed` |
| 任务一至三回归仍通过 | `agentfinal`、`agentcall`、`contexttest`、`agentstress`、`agentbench`、`agentexec` 均通过 |
