# 验证与性能评估

本文档给出 uCore 分支最终成品的评审可复现验证入口。逐项测试说明见 [testing-details.md](testing-details.md)，测试输出摘要保存在 [test-record.md](test-record.md)。

## 验证环境

| 项目 | 内容 |
| --- | --- |
| 分支 | `uCore` |
| 开发环境 | WSL2 Ubuntu 26.04 |
| 通用要求 | Linux、RISC-V GCC/binutils、QEMU riscv64、make、git |
| 已验证工具链 | `riscv64-linux-gnu-` |
| 构建命令 | `make clean`、`make user nfs/fs.img`、`make build` |
| 运行命令 | `make run` 或 `scripts/run-agent-tests.sh` |

## 构建命令

```bash
make -C user clean
make clean
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

1. `make -C user clean`
2. `make clean`
3. `make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=agent`
4. `make build TOOLPREFIX=riscv64-linux-gnu- LOG=warn INIT_PROC=agentfinal_ucore`
5. `make run ... INIT_PROC=agentfinal_ucore`
6. `make run ... INIT_PROC=agentfs_ucore`
7. `make run ... INIT_PROC=agentloop_ucore`
8. `make run ... INIT_PROC=agentbench_ucore`
9. `make run ... INIT_PROC=labbench_ucore`
10. `make run ... INIT_PROC=labdemo_ucore`
11. `make run ... INIT_PROC=agentsecurity_ucore`

也可以手动分别运行：

```bash
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfinal_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentfs_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentloop_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=agentbench_ucore CHAPTER=agent
make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=labbench_ucore CHAPTER=agent
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
| 任务四文件系统能力 | `agentfs_ucore: parent passed` |
| 任务五 Agent Loop | `agentloop_ucore: parent passed` |
| 任务一至五性能 | `agentbench_ucore: parent passed` |
| 演示规划性能入口 | `labbench_ucore: parent passed` |
| 综合场景 | `labdemo_ucore: parent passed` |
| 权限限制 | `agentsecurity_ucore: parent passed` |

## 测试覆盖表

| 测试程序 | 覆盖范围 | 关键通过输出 |
| --- | --- | --- |
| `agentfinal_ucore` | Agent 创建、5 页 Context、批量工具调用、短文本历史、`context_detail()`、用户自管 cache、名称协议、直接读 Context 镜像、Context Snapshot、FIFO 淘汰、篡改保护、文件索引、自唤醒事件 | `agentfinal_ucore: passed` |
| `agentfs_ucore` | 真实文件 inode 绑定、字段清空、文件删除清理、`.agentmeta` 重新加载、接近 128 条文件元数据下的 scan/index 差异和一致性、结果截断标志、不存在 selector 返回 NOT_FOUND | `agentfs_ucore: parent passed` |
| `agentloop_ucore` | FIFO 事件顺序、队列满丢弃、多 watch、unwatch、有限 timeout 睡眠、TIMER unwatch、heartbeat wake/stop | `agentloop_ucore: parent passed` |
| `agentbench_ucore` | scalar vs batch、direct Context、context_query vs context_snapshot、文件扫描 vs 索引候选记录数、timeout/heartbeat 断言、busy polling 和 event wait/wake 计时观测 | `agentbench_ucore: parent passed` |
| `labbench_ucore` | 初步演示规划中的性能入口，包装运行 `agentbench_ucore`，后续可升级为 `labbench --full` | `labbench_ucore: parent passed` |
| `labdemo_ucore` | orchestrator 控制的多 Agent 综合场景、文件属性查询、依赖查询、事件等待、消息唤醒、权限拒绝、幂等恢复、报告查询、结构化 `agentos:event` | `labdemo_ucore: parent passed` |
| `agentsecurity_ucore` | 初始化前索引查询、legacy tool mismatch、普通进程敏感调用拒绝、`.agentmeta` 普通访问保护、pid 1 直接子进程启动 orchestrator、sentinel 伪造 recovery 拒绝、多 run 定向恢复、重复 corr_id 拒绝、role/capability mask 检查 | `agentsecurity_ucore: parent passed` |

综合演示主入口是 `labdemo_ucore`。`agentfinal_ucore` 和 `agentbench_ucore` 是任务一至三高性能底座和任务四/五演示能力的持续验证入口。

## 功能输出样例

```text
agentfinal_ucore: Agent-OS on uCore final verification
agentfinal_ucore: context size=20480 capacity=128
agentfinal_ucore: batch first_seq=1 last_seq=64
agentfinal_ucore: short_text_history=1 payload=ucore-final result=ucore-final
agentfinal_ucore: tamper_protected=1
agentfinal_ucore: context_detail=1 sequence=8
agentfinal_ucore: user_cache_preserved=1 offset=17408 size=3072
agentfinal_ucore: record_flags system=1 manual=1 truncated=0
agentfinal_ucore: legacy_name_protocol=1
agentfinal_ucore: fifo oldest=66 latest=193 dropped=65
agentfinal_ucore: file_query hits=2 scanned=2 used_index=1
agentfinal_ucore: event_wait=1 payload=self wake
agentfinal_ucore: passed
agentfinal_ucore: parent passed
```

文件系统测试输出摘要：

```text
agentfs_ucore: default_inode dev=1 inum=11 scanned=2
agentfs_ucore: custom_inode dev=1 inum=17 size=7
agentfs_ucore: bulk_index scan=108 index=6 hits=1
agentfs_ucore: scan_index_consistent=1
agentfs_ucore: truncated_query total=100 returned=3 truncated=1
agentfs_ucore: .agentmeta_reload=1
agentfs_ucore: clear_status=1
agentfs_ucore: delete_clears_metadata=1
agentfs_ucore: missing_selector_not_found=1
agentfs_ucore: passed
agentfs_ucore: parent passed
```

Agent Loop 测试输出摘要：

```text
agentloop_ucore: fifo=1
agentloop_ucore: overflow_dropped=1
agentloop_ucore: unwatch=1
agentloop_ucore: timeout_sleep_no_poll=1
agentloop_ucore: timer_unwatch=1
agentloop_ucore: heartbeat_wake_stop=1
agentloop_ucore: passed
agentloop_ucore: parent passed
```

综合演示输出摘要：

```text
labdemo_ucore: Agent-OS laboratory recovery demo
agentos:event type=AGENT_CREATED tick=... role=orchestrator
agentos:event type=RUN_OBJECT tick=... project=lab-gene-x workflow=nightly-regression run_id=RUN-042 desired_state=RECOVERED
agentos:event type=WATCH_REGISTERED tick=... role=sentinel event=FILE_STATUS filter=status=failed
agentos:event type=INCIDENT_CREATED tick=... id=INC-RUN-042-ALIGN-OOM project=lab-gene-x run_id=RUN-042 stage=align reason=memory_limit
labdemo_ucore: sentinel event payload=status=failed;stage=align;run_id=RUN-042;project=lab-gene-x
agentos:event type=TOOL_CALL tick=... role=sentinel tool=query_file project=lab-gene-x run_id=RUN-042 status=failed hits=1 used_index=1
agentos:event type=LLM_CALL tick=... mode=template task=explain_root_cause llm_request_id=LLM-RUN-042-RCA-1 status=OK
agentos:event type=PLAN_CREATED tick=... role=investigator plan=PLAN-RUN-042-RECOVER-1 actions=align,report skip=prepare
agentos:event type=AUDIT tick=... role=sentinel action=rerun_stage result=DENIED corr_id=RUN-042-align-rerun-1
agentos:event type=ACTION tick=... role=recovery stage=align status=OK corr_id=RUN-042-align-rerun-1
agentos:event type=REPORT tick=... role=recovery project=lab-gene-x run_id=RUN-042 file=RUN-042-recovery.md status=OK corr_id=RUN-042-report-write-1 plan=PLAN-RUN-042-RECOVER-1 seq=... llm_enhanced=0
agentos:event type=FINAL tick=... status=RECOVERED plan=PLAN-RUN-042-RECOVER-1
labdemo_ucore: passed
labdemo_ucore: parent passed
```

权限限制输出摘要：

```text
agentsecurity_ucore: Agent permission test
agentsecurity_ucore: mail_basic=1
agentsecurity_ucore: plain_process_denied=1
agentsecurity_ucore: .agentmeta_protected=1
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

## 性能数据样例

```text
agentbench_ucore: timeout_heartbeat=1
agentbench_ucore: repeated_ticks scalar_min=5 scalar_avg=5 scalar_max=6 batch_min=3 batch_avg=3 batch_max=4
agentbench_ucore: file_query_records scan_records=107 index_records=6
agentbench_ucore: case ops ticks ops_per_tick speedup_x100
agentbench_ucore: scalar_agent_run ops=256 ticks=5 ops_per_tick=51 speedup_x100=100
agentbench_ucore: batch_agent_run ops=256 ticks=3 ops_per_tick=85 speedup_x100=166
agentbench_ucore: direct_context ops=5000 ticks=1 ops_per_tick=5000 speedup_x100=9765
agentbench_ucore: context_query ops=16 ticks=1 ops_per_tick=16 speedup_x100=100
agentbench_ucore: context_snapshot ops=2048 ticks=3 ops_per_tick=682 speedup_x100=4266
agentbench_ucore: file_scan_query ops=64 ticks=6 ops_per_tick=10 speedup_x100=100
agentbench_ucore: file_index_query ops=64 ticks=2 ops_per_tick=32 speedup_x100=300
agentbench_ucore: busy_poll_query ops=128 ticks=5 ops_per_tick=25 speedup_x100=100
agentbench_ucore: event_wait_wake ops=8 ticks=3 ops_per_tick=2 speedup_x100=100
agentbench_ucore: busy_poll_vs_wait busy_ops=128 busy_ticks=5 wait_ops=8 wait_ticks=3
agentbench_ucore: passed
agentbench_ucore: parent passed
labbench_ucore: parent passed
```

说明：

- 上述性能数字是一次样例输出，QEMU tick、宿主机负载和日志等级都会影响绝对数值。
- `batch_agent_run` 与 `scalar_agent_run` 执行同样数量的 echo 工具操作，前者将 64 个 op 合并为一次 syscall。
- `direct_context` 直接读取用户态 Context 镜像，不进入内核。
- `context_snapshot` 一次返回最多 128 条可见记录，按返回记录数计算吞吐。
- 文件索引查询的提升幅度与数据分布有关；当前测试保证能观察到 scan/index 两条路径，并输出候选记录数差异。
- `busy_poll_query` 和 `event_wait_wake` 同时输出，用于展示轮询路径与事件路径都可观测；不设置固定 tick 阈值。

## 基础兼容抽测

Agent-OS 最终验收主路径是 `CHAPTER=agent`。同时，当前实现补充了代表性的 uCore 基础 syscall：`trace`、`mailread`、`mailwrite`。基础兼容抽测使用 `CHAPTER=3` 中的 `ch3_trace`：

```bash
make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX=riscv64-linux-gnu- CHAPTER=3
timeout 60s make run TOOLPREFIX=riscv64-linux-gnu- LOG=error INIT_PROC=ch3_trace CHAPTER=3
```

期望输出：

```text
string from task trace test
Test trace OK!
```

`agentsecurity_ucore` 中还验证了普通进程 mail 的最小路径：

```text
agentsecurity_ucore: mail_basic=1
```

## 覆盖到的赛题验收项

| 赛题验收项 | 证据 |
| --- | --- |
| Agent 进程能成功创建，PCB 扩展字段正确初始化 | `agentfinal_ucore` |
| Agent Context 区正确分配，Agent 可直接读取 | `agentfinal_ucore` 直接读取 header/latest/record |
| 用户态篡改 Context 镜像不影响内核权威历史 | `agentfinal_ucore: tamper_protected=1` |
| 用户自管 Context cache 不被 snapshot 覆盖 | `agentfinal_ucore: user_cache_preserved=1` |
| 普通进程和 Agent 进程共存 | `agent_create_role` 由 pid 1 普通 init 或 pid 1 直接普通子进程创建 orchestrator；普通进程不能直接执行敏感 Agent syscall |
| 用户态 Agent 能调用至少 3 个内核工具 | `agentfinal_ucore`、`labdemo_ucore` |
| 请求和响应为结构化格式 | `agent_op`、`agent_result`、`agent_request`、`agent_response` |
| 工具名称 + 参数键值列表协议可用 | `agentfinal_ucore: legacy_name_protocol=1` |
| 5 轮以上连续工具调用并维护路径 | `agentfinal_ucore` 连续 192 次 op |
| Context Path 保存 128 条短文本摘要路径 | `agentfinal_ucore: short_text_history=1` |
| Agent 直接从 Context 高速读取路径数据 | `agentbench_ucore: direct_context` |
| 路径超长自动淘汰且不 OOM | `agentfinal_ucore` 验证 `oldest=66 latest=193 dropped=65` |
| 完整工具调用详情可查询 | `agentfinal_ucore: context_detail=1` |
| 文件属性查询、真实 inode 关联、私有 `.agentmeta` 和索引路径 | `agentfinal_ucore`、`agentfs_ucore: .agentmeta_reload=1`、`agentbench_ucore`、`labdemo_ucore` |
| 初始化前索引查询不会卡死 | `agentsecurity_ucore: preinit_index_query=1` |
| legacy `tool_id` / `tool_name` 不匹配会失败 | `agentsecurity_ucore: legacy_tool_mismatch=1` |
| 文件依赖查询和最小恢复 | `labdemo_ucore: affected stages=align+analyze+report+archive` |
| Agent watch/wait 和文件状态唤醒 | `agentloop_ucore`、`labdemo_ucore` |
| Agent wait timeout、heartbeat 字段更新和 heartbeat 事件停止 | `agentbench_ucore: timeout_heartbeat=1`、`agentloop_ucore: timeout_sleep_no_poll=1`、`agentloop_ucore: timer_unwatch=1`、`agentloop_ucore: heartbeat_wake_stop=1` |
| 消息触发 Agent 事件 | `labdemo_ucore` 中 sentinel->investigator、investigator->recovery 消息 |
| 权限拒绝和幂等恢复 | `labdemo_ucore` 中 denied 和 duplicate 输出 |
| 权限不能由用户态 role 参数伪造 | `agentsecurity_ucore` 中 sentinel 伪造 recovery 仍返回 `AGENT_STATUS_DENIED` |
| 多 run 恢复和报告写入只修改目标 run | `agentsecurity_ucore: scoped_rerun=1`、`agentsecurity_ucore: scoped_report=1` |
| 代表性 uCore 基础 syscall 可用 | `ch3_trace` 输出 `Test trace OK!`；`agentsecurity_ucore: mail_basic=1` |

## 仍需补充的验证

| 方向 | 当前缺口 |
| --- | --- |
| 后台目录扫描 | 当前是显式元数据更新、真实 inode 绑定和私有 `.agentmeta` 元数据文件，不含后台线程持续扫描 |
| 长期 Agent 调度 | 当前验证 watch/unwatch、FIFO 事件队列、wait/wake/timeout/heartbeat，不含优先级和取消机制 |
| 最终成品 LLM Gateway | 当前只预留结构化事件和工具输出，未接真实云端 LLM |
| 最终成品可视化大屏 | 当前输出 `agentos:event`，但宿主机大屏尚未实现 |
| 性能可信度 | tick 粒度较粗，后续可补更细粒度计数 |
