# 最终演示讲解稿

本文档用于评审现场讲解或录制演示视频。推荐演示顺序是：先讲系统目标，再跑正确性测试，再跑性能测试，最后跑多 Agent 综合场景。

## 1. 开场说明

本项目在 uCore 内核上实现 Agent-OS。它不是普通用户态脚本，而是内核级 Agent 进程、结构化工具调用、上下文历史、文件元数据索引和 Agent 事件运行机制。

当前演示分为六部分：

```bash
agentfinal_ucore
agentfs_ucore
agentloop_ucore
agentbench_ucore
labdemo_ucore
agentsecurity_ucore
```

三者分工：

| 程序 | 作用 |
| --- | --- |
| `agentfinal_ucore` | 证明任务一至三核心功能正确，同时检查文件索引和事件自唤醒 |
| `agentfs_ucore` | 证明任务四已经绑定真实 inode、支持 `.agentmeta` 和索引查询 |
| `agentloop_ucore` | 证明任务五的 FIFO 事件队列、unwatch、timeout 和 heartbeat stop |
| `agentbench_ucore` | 给出批量调用、Context 直接读、snapshot、文件索引的性能证据，并验证 timeout/heartbeat 与 wait/wake 计时 |
| `labdemo_ucore` | 展示一个由 orchestrator 控制的多 Agent 实验恢复场景 |
| `agentsecurity_ucore` | 展示普通进程和低权限 Agent 无法越权，并验证普通 mail 与多 run 精确恢复 |

## 2. 环境和运行方式

演示前说明当前环境：

- WSL2 Ubuntu 26.04；
- QEMU riscv64；
- `riscv64-linux-gnu-` 工具链；
- 当前 Git 分支：`uCore`。

推荐运行：

```bash
bash scripts/run-agent-tests.sh
```

如果需要分步演示，可以分别运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfs_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentloop_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsecurity_ucore CHAPTER=agent
```

## 3. 正确性演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
```

重点解释输出：

```text
agentfinal_ucore: context size=20480 capacity=128
```

说明每个 Agent 有 5 页 Context，最多保留 128 条可见历史，并为完整 detail ring 预留空间。

```text
agentfinal_ucore: batch first_seq=1 last_seq=64
```

说明一次 syscall 执行 64 个工具调用，并保证 sequence 连续。

```text
agentfinal_ucore: short_text_history=1 payload=ucore-final result=ucore-final
```

说明 Context Path 保存短 payload/result 摘要，评审可以看到多轮调用的内容，不只是数字计数。

```text
agentfinal_ucore: context_detail=1 sequence=8
agentfinal_ucore: record_flags system=1 manual=1 truncated=0
```

说明短摘要之外还可以按 sequence 查询完整请求/响应；系统自动记录和手动 push 记录能被区分。

```text
agentfinal_ucore: tamper_protected=1
```

说明用户态直接修改 Context 镜像不能伪造内核权威历史。当前设计同时兼顾直接读性能和可信 snapshot。

```text
agentfinal_ucore: fifo oldest=66 latest=193 dropped=65
```

说明超过容量后按 FIFO 淘汰旧记录，并维护 oldest/latest/dropped 元信息。

```text
agentfinal_ucore: file_query hits=2 scanned=2 used_index=1
```

说明文件元数据查询已经走索引路径。

```text
agentfinal_ucore: event_wait=1 payload=self wake
```

说明 Agent watch/wake/wait 机制可用。

最后看到：

```text
agentfinal_ucore: passed
agentfinal_ucore: parent passed
```

## 4. 文件系统能力演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfs_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `default_inode` | 默认演示元数据已经绑定真实根目录文件的 `dev/inum` |
| `custom_inode` | 用户态创建的新文件也能绑定 Agent 元数据 |
| `bulk_index` | 接近 128 条记录时，索引路径检查的候选记录少于扫描路径 |
| `clear_status` | 属性清空能够生效 |
| `delete_clears_metadata` | 删除真实文件会同步清理 Agent 元数据 |
| `missing_selector_not_found` | 恢复/报告 selector 没有命中时返回明确失败 |

最后看到：

```text
agentfs_ucore: passed
agentfs_ucore: parent passed
```

## 5. Agent Loop 演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentloop_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `fifo=1` | 事件按照投递顺序被消费 |
| `overflow_dropped=1` | 16 槽队列满时拒绝新事件，不覆盖旧事件 |
| `unwatch=1` | watch 可删除 |
| `timeout_sleep=1` | 有限 timeout 等待进入睡眠，不通过循环消耗 CPU |
| `heartbeat_wake_stop=1` | heartbeat 能唤醒 Agent，停止后不再投递 |

最后看到：

```text
agentloop_ucore: passed
agentloop_ucore: parent passed
```

## 6. 性能演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
```

重点解释：

| 输出项 | 讲解重点 |
| --- | --- |
| `scalar_agent_run` | 单个工具调用基线 |
| `batch_agent_run` | 64 个 op 合并到一次 syscall，减少内核入口次数 |
| `direct_context` | 用户态直接读 Context 镜像，不需要 syscall |
| `context_query` | 逐条查询历史 |
| `context_snapshot` | 一次返回多条历史，适合批量读取 |
| `file_scan_query` | 文件元数据扫描路径 |
| `file_index_query` | 文件元数据索引路径 |
| `timeout_heartbeat` | 无事件等待会 timeout，心跳字段可通过 `agent_info()` 观察 |
| `event_wait_wake` | Agent Loop 等待和唤醒计时观测 |

说明性能数字会随 QEMU 和宿主机负载波动。答辩时应强调相对趋势和设计原因：减少 syscall 次数、减少重复查询、减少线性扫描。

最后看到：

```text
agentbench_ucore: passed
agentbench_ucore: parent passed
```

## 7. 场景演示

运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
```

### 7.1 场景设定

一个实验流水线有多个阶段：

- prepare；
- align；
- analyze；
- report；
- archive。

系统中有一个 orchestrator 控制 Agent，以及三个业务 Agent：

| 角色 | 职责 |
| --- | --- |
| orchestrator | 初始化文件元数据、创建业务 Agent、注入失败事件 |
| sentinel | 发现失败事件 |
| investigator | 分析失败原因和影响范围 |
| recovery | 执行恢复动作 |

### 7.2 讲解流程

1. 普通 init 只创建 orchestrator。
2. orchestrator 初始化文件元数据并创建三个业务 Agent。
3. sentinel 监听 `status=failed`。
4. orchestrator 注入 align 阶段失败。
5. sentinel 收到事件并查询文件索引。
6. sentinel 尝试恢复但权限不足，被内核拒绝。
7. sentinel 唤醒 investigator。
8. investigator 查询摘要和依赖，确认影响范围。
9. investigator 输出 Context Snapshot，证明决策过程可审计。
10. investigator 唤醒 recovery。
11. recovery 通过权限检查并执行恢复。
12. recovery 重复执行同一恢复动作，内核识别为 duplicate。
13. 最终查询报告文件，系统输出 recovered。

### 7.3 关键输出

```text
agentos:event type=WATCH_REGISTERED role=sentinel filter=status=failed
agentos:event type=INCIDENT_CREATED id=INC-RUN-042-ALIGN-OOM stage=align
labdemo_ucore: sentinel event payload=status=failed;stage=align;run_id=RUN-042;project=lab-gene-x
agentos:event type=TOOL_CALL role=sentinel tool=query_file hits=1 used_index=1
agentos:event type=AUDIT role=sentinel action=rerun_stage result=DENIED
agentos:event type=MESSAGE from=sentinel to=investigator status=OK
agentos:event type=CONTEXT_SNAPSHOT role=investigator records=4
agentos:event type=ACTION role=recovery stage=align status=OK
agentos:event type=AUDIT role=recovery action=rerun_align result=DUPLICATE
agentos:event type=FINAL status=RECOVERED
labdemo_ucore: passed
labdemo_ucore: parent passed
```

### 7.4 讲解重点

| 输出 | 说明 |
| --- | --- |
| `WATCH_REGISTERED` | Agent Loop 注册成功 |
| `INCIDENT_CREATED` | 文件状态变化触发事件 |
| `TOOL_CALL ... query_file` | Agent 使用文件元数据索引查询失败工件 |
| `DENIED` | 内核权限检查生效，sentinel 不能直接恢复 |
| `MESSAGE` | Agent 间通过内核事件通信 |
| `CONTEXT_SNAPSHOT` | investigator 的判断过程进入 Context Path |
| `ACTION ... status=OK` | recovery 执行恢复动作 |
| `DUPLICATE` | 幂等表拒绝重复恢复 |
| `FINAL status=RECOVERED` | 场景完成 |

## 8. 结尾总结

本项目在 uCore 上实现了 Agent 进程、工具调用、Context Path、文件元数据索引和 Agent Loop。最终场景证明这些功能可以组合成一个完整的内核级 Agent 协作系统，而不是只停留在分散 syscall 的层面。

补充安全验证可运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsecurity_ucore CHAPTER=agent
```

该程序证明普通进程 mail 最小路径可用；普通进程不能直接投递事件或修改 Agent 文件元数据；usershell 等价路径可以创建 orchestrator；初始化前索引查询不会卡住；legacy 工具 ID/名称不一致会失败；sentinel 也不能通过伪造 `AGENT_ROLE_RECOVERY` 获得恢复权限；recovery 只会恢复和写入 selector 指定的 run。

当前版本已经具备任务一至三的增强实现，完成任务四的真实 inode 关联文件元数据服务，完成任务五的有界事件队列和等待/唤醒机制，并提供任务六综合演示。后续可以继续增强后台目录扫描、Agent 事件优先级、云端 LLM Gateway 和可视化大屏。
