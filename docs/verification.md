<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# 验证与性能评估

本文档给出最终成品的评审可复现验证入口。逐项测试说明见 [testing-details.md](testing-details.md)，输出摘要保存在 [test-record.md](test-record.md)。

## 验证环境

| 项目 | 内容 |
| --- | --- |
| 分支 | `Wang` |
| 开发环境 | WSL2 Ubuntu 26.04 |
| 通用要求 | Linux、RISC-V GCC/binutils、QEMU riscv64、make、git |
| 构建命令 | `make fs.img`、`make kernel/kernel` |
| 运行命令 | `make qemu` |

## 最终验收命令

进入 xv6 shell 后依次运行最终验收程序：

```sh
labdemo
labdemo
labbench
agentfinal
agentbench
```

完整复测建议继续运行：

```sh
agentexec
agentcall
contexttest
agentstress
```

通过标准：

| 标准 | 期望 |
| --- | --- |
| 构建 | `make fs.img` 和 `make kernel/kernel` 成功 |
| 运行 | QEMU 正常启动并进入 xv6 shell |
| 稳定性 | 无 kernel panic |
| 综合场景 | `labdemo: passed` |
| 任务四/五性能 | `labbench: passed`，输出文件查询和事件等待性能表 |
| 任务一至三复测 | `agentfinal: passed`、`agentbench: passed` |

## 测试覆盖表

| 测试程序 | 覆盖范围 | 关键通过输出 |
| --- | --- | --- |
| `labdemo` | 四 Agent 综合场景、文件属性查询、依赖查询、事件等待、mailbox 唤醒、权限拒绝、幂等恢复、报告元数据更新、结构化 `agentos:event` | `labdemo: passed` |
| `labbench` | 文件扫描 vs 索引、`fid` 查询、元数据插入/删除、轮询查询基线、wait/wake 路径、scalar vs batch、context_query vs snapshot、capability、duplicate reject、事件队列溢出、短事件 payload 回查、角色创建权限、`unwatch` 和 `heartbeat_stop` | `labbench: passed` |
| `agentfinal` | Agent 创建、4 页 Context、批量工具调用、短文本历史、直接读 Context 镜像、Context Snapshot、FIFO 淘汰、篡改防护范围 | `agentfinal: passed` |
| `agentbench` | scalar run、batch run、direct Context、context_query、context_snapshot 吞吐 | `agentbench: passed` |
| `agentexec` | shell 直跑 wrapper 和 Agent exec 成功路径 | `agentexec: wrapper status=0` |
| `agentcall` | legacy 工具调用、参数键/类型错误、长 payload、坏输出指针无副作用、lazy 输出缓冲、多 Agent mailbox、历史 wrap | `agentcall: strict validation passed` |
| `contexttest` | 手动 push/query/rollback/clear、短文本历史、rollback 不存在返回码和 128 条 FIFO | `contexttest: passed` |
| `agentstress` | exec 成功/失败、连续创建退出、sbrk 增长上限、普通进程隔离、父进程堆越过 Context 后拒绝创建 Agent | `agentstress: passed` |

综合演示主入口是 `labdemo`，任务四/五性能主入口是 `labbench`。`labdemo` 需要能在同一 QEMU 会话连续运行两次并都输出 `labdemo: passed`。`agentfinal` 和 `agentbench` 是任务一至三高性能底座复测。

## 详细测试内容索引

| 测试程序 | 详细说明 |
| --- | --- |
| `labdemo` | [testing-details.md](testing-details.md) 中的夜间实验批量复测故障诊断与受控恢复场景 |
| `labbench` | [testing-details.md](testing-details.md) 中的文件查询、事件等待、批量工具和 Context 性能对比 |
| `agentfinal` | [testing-details.md](testing-details.md) 中的 Agent 创建、Context 映射、64 路批量工具调用、Context Snapshot、FIFO 淘汰和 direct/snapshot 一致性检查 |
| `agentbench` | [testing-details.md](testing-details.md) 中的 scalar run、batch run、direct context read、context_query、context_snapshot 和性能表字段解释 |

## 功能输出样例

```text
labdemo: Agent-OS lab recovery demo
labdemo: sentinel state=WAITING
labdemo: sentinel event type=FILE_STATUS payload=fid=4;status=failed;stage=align;run_id=RUN-042;truncated=0
labdemo: query_file project=lab-gene-x run=RUN-042 status=failed hits=1 scanned=1 used_index=1 first=lab_RUN042_align_err
labdemo: affected stages=align+analyze+report+archive
labdemo: unauthorized rerun by sentinel status=DENIED
labdemo: rerun stage=align status=OK corr_id=RUN-042-align-rerun-1
labdemo: duplicate corr_id=RUN-042-align-rerun-1 status=DUPLICATE
labdemo: report metadata updated artifact=lab_RUN042_recovery_report
labdemo: final report_query hits=1 used_index=1 scanned=9
labdemo: final status=RECOVERED
labdemo: passed
```

任务一至三底座复测输出：

```text
agentfinal: context size=16384 capacity=128
agentfinal: batch first_seq=1 last_seq=64
agentfinal: short_text_history=1 payload=final result=final
agentfinal: snapshot count=64 latest=64
agentfinal: direct_dirty_before_snapshot=1
agentfinal: tamper_protected=1
agentfinal: fifo oldest=65 latest=192 dropped=64
agentfinal: direct_context_match=1
agentfinal: passed
```

## 性能数据样例

```text
labbench: file_semantics scan_hits=2 index_hits=2 scan_scanned=112 index_scanned=2 report_scanned=9 empty=0 truncated=1 fid=1
labbench: metadata_dependency=1 scoped_rerun=1 scoped_report=1 history_preserved=1 single_report=1 mask_text=1 dep_clear=1 insert=1 delete=1
labbench: loop_timeout=1 heartbeat_timer=1 heartbeat_stop=1 unwatch=1 heartbeat_interval=0 last_heartbeat=42
labbench: permission_denied self_escalation=1 wake=1 meta=1 rerun=1 report=1
labbench: case ops ticks ops_per_tick speedup_x100
labbench: file_scan_query ops=32768 ticks=36 ops_per_tick=910 speedup_x100=100
labbench: file_index_query ops=32768 ticks=22 ops_per_tick=1489 speedup_x100=163
labbench: busy_poll_query ops=512 ticks=0 ops_per_tick=512 speedup_x100=100
labbench: scalar_tool_call ops=8192 ticks=3 ops_per_tick=2730 speedup_x100=100
labbench: batch_agent_run ops=8192 ticks=0 ops_per_tick=8192 speedup_x100=300
labbench: context_query ops=512 ticks=0 ops_per_tick=512 speedup_x100=100
labbench: context_snapshot ops=65536 ticks=1 ops_per_tick=65536 speedup_x100=12800
labbench: capability_check ops=32768 ticks=9 ops_per_tick=3640 speedup_x100=100
labbench: event_context_records=128 latest=513
labbench: non_target_timeout=1
labbench: event_wait_wake ops=512 ticks=2 ops_per_tick=256 speedup_x100=100
labbench: event_fifo queued=8 dropped=1 ordered=1
labbench: send_message_overflow queued=8 dropped=1 rollback=1
labbench: file_status_partial_payload fid=4 stage=align run_id=RUN-042 full_lookup=1
labbench: file_status_overflow queued=8 dropped=1 no_space=1
labbench: passed
```

任务一至三底座性能样例：

```text
agentbench: case ops ticks ops_per_tick speedup_x100
agentbench: scalar_run ops=65536 ticks=18 ops_per_tick=3640 speedup_x100=100
agentbench: batch_run ops=65536 ticks=2 ops_per_tick=32768 speedup_x100=900
agentbench: direct_context ops=1000000 ticks=0 ops_per_tick=1000000 speedup_x100=27465
agentbench: context_query ops=2048 ticks=1 ops_per_tick=2048 speedup_x100=56
agentbench: context_snapshot ops=262144 ticks=3 ops_per_tick=87381 speedup_x100=4266
agentbench: latest_sequence=131072 dropped=130944 capacity=128
agentbench: passed
```

说明：

- 上述性能数字是一次样例输出，xv6 tick 粒度较粗，复跑时具体 tick 和 speedup 会波动。
- `batch_run` 与 `scalar_run` 执行同样数量的 echo 工具操作，前者将 64 个 op 合并为一次 syscall。
- `direct_context` 的 tick 为 0，表示该测试中 1000000 次直接读低于一个 xv6 tick；`speedup_x100` 使用 1 tick 作为保守下界计算。
- `context_snapshot` 一次返回最多 128 条可见记录，按返回记录数计算吞吐。

## 覆盖到的赛题验收项

| 赛题验收项 | 证据 |
| --- | --- |
| Agent 进程能成功创建，PCB 扩展字段正确初始化 | `agentfinal` |
| Agent Context 区正确分配，Agent 可直接读写 | `agentfinal` 直接读取 header/latest/record |
| 用户态篡改 Context 镜像不影响内核权威历史 | `agentfinal: direct_dirty_before_snapshot=1` 说明 direct read 可见脏镜像，`agentfinal: tamper_protected=1` 说明 snapshot 刷新后恢复权威历史 |
| 父进程堆越过 Agent Context 起点时拒绝创建 Agent | `agentstress: parent_over_context_rejected=1` |
| 普通进程和 Agent 进程共存 | `agent_create` 由普通父进程创建 Agent 子进程 |
| 用户态 Agent 能调用至少 3 个内核工具 | `agentfinal` 批量调用 echo、pid_info、ctx_stat、read_context |
| 请求和响应为结构化格式 | `agent_op`、`agent_result` |
| legacy 参数键名和类型错误返回明确状态 | `agentcall` 输出 `bad_payload_key`、`bad_payload_type`、`unexpected_param` |
| 坏输出指针失败不产生工具副作用 | `agentcall` 输出 `agent bad_output: no_side_effect`，receiver 仍读到 `hello-agent` |
| 合法 lazy sbrk 输出页可作为 Agent 输出缓冲 | `agentcall: agent lazy_output: legacy=1 batch=1` |
| 5 轮以上连续工具调用并维护路径 | `agentfinal` 连续 192 次 op |
| Context Path 保存 128 条短文本摘要路径 | `agentfinal: short_text_history=1`、`contexttest: short_text_history=1` |
| Agent 直接从 Context 高速读取路径数据 | `agentfinal` 和 `agentbench` |
| 路径超长自动淘汰且不 OOM | `agentfinal` 验证 `oldest=65 latest=192 dropped=64` |
| 历史节点不存在返回可区分状态码 | `contexttest: rollback_not_found=-5` |
| rollback 后 sequence 不复用旧编号 | `contexttest: rollback latest=10 branch_latest=131` |
| Agent exec 失败后 Context 指针仍有效 | `agentstress: exec_failure_preserved=1` |
| 文件属性查询和索引路径 | `labdemo`、`labbench: file_scan_query` / `file_index_query`、`fid=1` |
| 元数据插入和删除 | `labbench: metadata_dependency=... insert=1 delete=1` |
| 文件依赖查询和最小恢复 | `labdemo: affected stages=align+analyze+report+archive`、`skip stage=prepare`、`labbench: scoped_rerun=1 scoped_report=1 mask_text=1 dep_clear=1` |
| Agent watch/wait 和文件状态唤醒 | `labdemo: sentinel state=WAITING`、`sentinel event type=FILE_STATUS`、`labbench: heartbeat_stop=1 unwatch=1` |
| mailbox 触发 Agent 事件 | `labdemo` 中 sentinel->investigator、investigator->recovery 消息 |
| 权限拒绝和幂等恢复 | `labdemo: unauthorized rerun by sentinel status=DENIED`、`duplicate ... status=DUPLICATE`、`labbench: permission_denied self_escalation=1` |

## 仍需补充的验证

| 方向 | 当前缺口 |
| --- | --- |
| 最终成品 LLM Gateway | 当前只预留 `llm_status=template` 和引用字段，未接真实云端 LLM |
| 最终成品可视化大屏 | 当前输出 `agentos:event`，但宿主机大屏尚未实现 |
| 性能可信度 | xv6 tick 粒度粗，后续可补更细粒度计数 |
