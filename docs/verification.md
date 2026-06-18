# 验证与性能评估

本文档给出 uCore 分支最终成品的评审可复现验证入口。逐项测试说明见 [testing-details.md](testing-details.md)，当前输出摘要保存在 [test-record.md](test-record.md)。

## 验证环境

| 项目 | 内容 |
| --- | --- |
| 分支 | `uCore` |
| 开发环境 | WSL2 Ubuntu 26.04 |
| 通用要求 | Linux、RISC-V GCC/binutils、QEMU riscv64、make、git |
| 已验证工具链 | `riscv64-linux-gnu-` |
| 构建命令 | `make user nfs/fs.img`、`make build` |
| 运行命令 | `make run` 或 `scripts/run-agent-tests.sh` |

## 构建命令

```bash
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent
make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore
```

其中：

- `CHAPTER=agent` 只构建本项目最终验证所需用户程序；
- `INIT_PROC=agentfinal_ucore` 用于把指定测试程序编译为内核启动后的第一个用户程序；
- `LOG=error` 可减少 QEMU 输出噪声；
- `SBI` 默认使用 OpenSBI，避免部分环境下 bootloader 输出不稳定。

## 最终验收命令

推荐直接运行：

```bash
bash scripts/run-agent-tests.sh
```

脚本会顺序执行：

1. `make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent`
2. `make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore`
3. `make run ... INIT_PROC=agentfinal_ucore`
4. `make run ... INIT_PROC=agentbench_ucore`
5. `make run ... INIT_PROC=labdemo_ucore`
6. `make run ... INIT_PROC=agentsecurity_ucore`

也可以手动分别运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentsecurity_ucore CHAPTER=agent
```

通过标准：

| 标准 | 期望 |
| --- | --- |
| 构建 | `make user nfs/fs.img` 和 `make build` 成功 |
| 运行 | QEMU 正常启动并执行指定 init 程序 |
| 稳定性 | 无 kernel panic |
| 任务一至三功能 | `agentfinal_ucore: passed` |
| 任务一至五性能 | `agentbench_ucore: passed` |
| 综合场景 | `labdemo_ucore: passed` |
| 权限边界 | `agentsecurity_ucore: passed` |

## 测试覆盖表

| 测试程序 | 覆盖范围 | 关键通过输出 |
| --- | --- | --- |
| `agentfinal_ucore` | Agent 创建、4 页 Context、批量工具调用、短文本历史、直接读 Context 镜像、Context Snapshot、FIFO 淘汰、篡改边界、文件索引、自唤醒事件 | `agentfinal_ucore: passed` |
| `agentbench_ucore` | scalar vs batch、direct Context、context_query vs context_snapshot、文件扫描 vs 索引、event wait/wake | `agentbench_ucore: passed` |
| `labdemo_ucore` | orchestrator 控制的多 Agent 综合场景、文件属性查询、依赖查询、事件等待、消息唤醒、权限拒绝、幂等恢复、报告查询、结构化 `agentos:event` | `labdemo_ucore: passed` |
| `agentsecurity_ucore` | 普通进程敏感调用拒绝、sentinel 伪造 recovery 拒绝、recovery 真实权限恢复、重复 corr_id 拒绝、role/capability mask 检查 | `agentsecurity_ucore: passed` |

综合演示主入口是 `labdemo_ucore`。`agentfinal_ucore` 和 `agentbench_ucore` 是任务一至三高性能底座和任务四/五演示能力的回归入口。

## 最新功能输出

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

综合演示输出摘要：

```text
labdemo_ucore: Agent-OS laboratory recovery demo
agentos:event type=AGENT_CREATED role=orchestrator
agentos:event type=WATCH_REGISTERED role=sentinel filter=status=failed
agentos:event type=INCIDENT_CREATED id=INC-RUN-042-ALIGN-OOM stage=align
agentos:event type=TOOL_CALL role=sentinel tool=query_file hits=1 used_index=1
agentos:event type=AUDIT role=sentinel action=rerun_stage result=DENIED
agentos:event type=MESSAGE from=sentinel to=investigator status=OK
agentos:event type=CONTEXT_SNAPSHOT role=investigator records=4
agentos:event type=ACTION role=recovery stage=align status=OK
agentos:event type=AUDIT role=recovery action=rerun_align result=DUPLICATE
agentos:event type=FINAL status=RECOVERED
labdemo_ucore: passed
```

权限边界输出摘要：

```text
agentsecurity_ucore: Agent permission boundary test
agentsecurity_ucore: plain_process_denied=1
agentsecurity_ucore: role=orchestrator capability_checked=1
agentsecurity_ucore: role=sentinel capability_checked=1
agentsecurity_ucore: sentinel spoof_denied=1
agentsecurity_ucore: role=recovery capability_checked=1
agentsecurity_ucore: recovery rerun_ok=1 duplicate=1
agentsecurity_ucore: passed
```

## 最新性能数据

```text
agentbench_ucore: case ops ticks ops_per_tick speedup_x100
agentbench_ucore: scalar_agent_run ops=8192 ticks=118 ops_per_tick=69 speedup_x100=100
agentbench_ucore: batch_agent_run ops=8192 ticks=70 ops_per_tick=117 speedup_x100=168
agentbench_ucore: direct_context ops=50000 ticks=1 ops_per_tick=50000 speedup_x100=72021
agentbench_ucore: context_query ops=256 ticks=3 ops_per_tick=85 speedup_x100=100
agentbench_ucore: context_snapshot ops=32768 ticks=23 ops_per_tick=1424 speedup_x100=1669
agentbench_ucore: file_scan_query ops=1024 ticks=26 ops_per_tick=39 speedup_x100=100
agentbench_ucore: file_index_query ops=1024 ticks=23 ops_per_tick=44 speedup_x100=113
agentbench_ucore: event_wait_wake ops=32 ticks=5 ops_per_tick=6 speedup_x100=100
agentbench_ucore: passed
```

说明：

- 上述性能数字是一次样例输出，QEMU tick、宿主机负载和日志等级都会影响绝对数值。
- `batch_agent_run` 与 `scalar_agent_run` 执行同样数量的 echo 工具操作，前者将 64 个 op 合并为一次 syscall。
- `direct_context` 直接读取用户态 Context 镜像，不进入内核。
- `context_snapshot` 一次返回最多 128 条可见记录，按返回记录数计算吞吐。
- 文件索引查询的提升幅度与数据分布有关；当前测试保证能观察到 scan/index 两条路径。

## 覆盖到的赛题验收项

| 赛题验收项 | 证据 |
| --- | --- |
| Agent 进程能成功创建，PCB 扩展字段正确初始化 | `agentfinal_ucore` |
| Agent Context 区正确分配，Agent 可直接读取 | `agentfinal_ucore` 直接读取 header/latest/record |
| 用户态篡改 Context 镜像不影响内核权威历史 | `agentfinal_ucore: tamper_protected=1` |
| 普通进程和 Agent 进程共存 | `agent_create_role` 由 pid 1 普通 init 创建 orchestrator；普通进程不能直接执行敏感 Agent syscall |
| 用户态 Agent 能调用至少 3 个内核工具 | `agentfinal_ucore`、`labdemo_ucore` |
| 请求和响应为结构化格式 | `agent_op`、`agent_result` |
| 5 轮以上连续工具调用并维护路径 | `agentfinal_ucore` 连续 192 次 op |
| Context Path 保存 128 条短文本摘要路径 | `agentfinal_ucore: short_text_history=1` |
| Agent 直接从 Context 高速读取路径数据 | `agentbench_ucore: direct_context` |
| 路径超长自动淘汰且不 OOM | `agentfinal_ucore` 验证 `oldest=65 latest=192 dropped=64` |
| 文件属性查询和索引路径 | `agentfinal_ucore`、`agentbench_ucore`、`labdemo_ucore` |
| 文件依赖查询和最小恢复 | `labdemo_ucore: affected stages=align+analyze+report+archive` |
| Agent watch/wait 和文件状态唤醒 | `labdemo_ucore` |
| 消息触发 Agent 事件 | `labdemo_ucore` 中 sentinel->investigator、investigator->recovery 消息 |
| 权限拒绝和幂等恢复 | `labdemo_ucore` 中 denied 和 duplicate 输出 |
| 权限不能由用户态 role 参数伪造 | `agentsecurity_ucore` 中 sentinel 伪造 recovery 仍返回 `AGENT_STATUS_DENIED` |

## 仍需补充的验证

| 方向 | 当前缺口 |
| --- | --- |
| 真实目录后台索引 | 当前是 Agent 子系统文件元数据表和演示数据 |
| 长期 Agent 调度 | 当前验证 watch/wait/wake/heartbeat，不含优先级和取消机制 |
| 最终成品 LLM Gateway | 当前只预留结构化事件和工具输出，未接真实云端 LLM |
| 最终成品可视化大屏 | 当前输出 `agentos:event`，但宿主机大屏尚未实现 |
| 性能可信度 | tick 粒度较粗，后续可补更细粒度计数 |
